"""第 7 章教学重构：LLM 运行时内核 —— 适配器 seam + 统一流式入口 + 块累积器。

仅标准库，Python 3.10+。

源码对应（行号取自 notes §2.1）：
- LlmAdapter      ↔ packages/llm/llm/src/index.ts:180（providerInfo :186 / providerRetryPolicy :195 /
                     listModels :206 / resolveModel :219 / stream :232）
- LlmRuntime      ↔ packages/llm/llm/src/index.ts:284（registerAdapter :338 / listProviders :419 /
                     resolveCallFor :734 / prepareCall :779 / adapterStream :843 / stream :913 /
                     streamWithRegistration :917 / adapterFailureChunk :931）
- StreamChunk     ↔ packages/llm/llm/src/types.ts:291（7 变体）
- BlockAssembler  ↔ packages/llm/llm/src/assembler.ts:36（blocks :134 / usage :142 / finish :147 /
                     replayState :152 / message :161）
"""
from __future__ import annotations

from abc import ABC, abstractmethod

__all__ = ["LlmAdapter", "LlmRuntime", "BlockAssembler", "STREAM_CHUNK_TYPES"]

# StreamChunk 的 7 个变体（types.ts:291）；教学版用 dict 表达，靠 "type" 字段判别。
STREAM_CHUNK_TYPES = (
    "block-start",      # 块开始：携带 index 与 block_type
    "text-delta",       # 文本增量
    "reasoning-delta",  # 推理增量
    "tool-call-delta",  # 工具调用增量（arguments 逐段追加）
    "block-end",        # 块结束：携带完整 block
    "usage",            # token 计量快照
    "finish",           # 调用结束：携带 reason 与可选 replay_state
)


class LlmAdapter(ABC):
    """适配器抽象基类：每家 provider 实现一个，唯一抽象方法是 stream。

    对应 LlmAdapter（index.ts:180）：其余方法均有默认实现，子类按需覆写。
    [教学决策 G7] 真实版 stream 是 async 生成器；教学版用同步生成器，机制语义不变。
    """

    def provider_info(self):
        """provider 元数据：路由键 id + 展示名 name（index.ts:186）。"""
        return {"id": "unknown", "name": "unknown"}

    def provider_retry_policy(self, provider):
        """重试策略：默认返回 None 表示不重试（index.ts:195）。"""
        return None

    def list_models(self, provider):
        """模型列表：默认返回空列表，子类覆写（index.ts:206）。"""
        return []

    def resolve_model(self, model):
        """把模型解析为 {provider, id, name}（index.ts:219）。"""
        return {"provider": "unknown", "id": model, "name": model}

    @abstractmethod
    def stream(self, options):
        """抽象方法：接收 GenerateOptions，逐条 yield StreamChunk（index.ts:232）。"""


class LlmRuntime:
    """运行时内核：适配器注册表 + 统一流式入口。

    对应 LlmRuntime（index.ts:284）。调用方只见 stream()；
    provider 路由、异常归一化都发生在 runtime 内部。
    """

    def __init__(self):
        self._adapters = {}   # provider id -> adapter 实例
        self._default = None  # 默认 provider（首个注册者）

    def register_adapter(self, adapter):
        """注册 adapter 实例，返回带 dispose/replace 的句柄（index.ts:338）。"""
        info = adapter.provider_info()
        provider_id = info["id"]
        previous = self._adapters.get(provider_id)
        self._adapters[provider_id] = adapter
        if self._default is None:
            self._default = provider_id

        def dispose():
            if self._adapters.get(provider_id) is adapter:
                del self._adapters[provider_id]

        def replace(new_adapter):
            self._adapters[provider_id] = new_adapter

        return {"dispose": dispose, "replace": replace, "previous": previous}

    def list_providers(self):
        """列出全部已注册 provider 的元数据（index.ts:419）。"""
        return [adapter.provider_info() for adapter in self._adapters.values()]

    def resolve_call_for(self, provider):
        """按 provider 定位 adapter 实例（index.ts:734）。"""
        adapter = self._adapters.get(provider)
        if adapter is None:
            raise KeyError(f"provider not registered: {provider}")
        return adapter

    def stream(self, messages, system_prompt=None, provider=None, model=None, **options):
        """对外统一入口（index.ts:913）：同步返回可迭代对象。

        签名与第 2 章 LlmStub.stream（messages, system_prompt=...）兼容，
        因此 runtime 可直接顶替 stub 成为 ctx.llm。
        """
        call_options = {
            "provider": provider or self._default,
            "model": model or "default",
            "messages": messages,
            "system": system_prompt,
        }
        call_options.update(options)
        return self._adapter_stream(call_options)

    def _adapter_stream(self, options):
        """统一流式入口内核（index.ts:843）：try/except 包住 adapter.stream。

        任何异常都被归一化为终止 finish chunk，消费者永远看不到裸异常。
        [教学简化] 真实版还有 waterfall('llm/stream') 事件包装（index.ts:925）
        与 prepareCall 配置冻结（index.ts:779）；教学版直接路由。
        """
        adapter = self.resolve_call_for(options["provider"])
        try:
            yield from adapter.stream(options)
        except Exception as exc:  # 归一化：异常 → 终止 chunk
            yield self._failure_chunk(exc)

    @staticmethod
    def _failure_chunk(exc):
        """把异常构造为终止 chunk（index.ts:931 adapterFailureChunk）。"""
        return {"type": "finish", "reason": "error", "error": str(exc)}


class BlockAssembler:
    """流式块累积器：把 *-delta 增量累积成完整内容块。

    对应 BlockAssembler（assembler.ts:36）：push() 逐条消费 chunk，
    blocks/usage/finish/replay_state 是只读观察点，message() 转一条 assistant 消息。
    """

    def __init__(self):
        self._open = {}            # index -> 正在累积的块
        self._blocks = []          # 已完成的内容块
        self._usage = None         # 最近一次 usage 快照
        self._finish = None        # 终止原因
        self._replay_state = None  # adapter 私有重放状态

    def push(self, chunk):
        """消费一条 chunk，按类型更新内部状态。"""
        chunk_type = chunk["type"]
        if chunk_type == "block-start":
            block = {"type": chunk["block_type"]}
            if chunk["block_type"] in ("text", "reasoning"):
                block["text"] = ""
            elif chunk["block_type"] == "tool-call":
                block.update({"id": None, "name": "", "arguments": ""})
            self._open[chunk["index"]] = block
        elif chunk_type in ("text-delta", "reasoning-delta"):
            block = self._open.setdefault(
                chunk["index"], {"type": chunk_type.split("-")[0], "text": ""}
            )
            block["text"] += chunk["text"]
        elif chunk_type == "tool-call-delta":
            block = self._open.setdefault(
                chunk["index"], {"type": "tool-call", "id": None, "name": "", "arguments": ""}
            )
            if chunk.get("id") is not None:
                block["id"] = chunk["id"]
            if chunk.get("name"):
                block["name"] += chunk["name"]
            block["arguments"] += chunk["arguments_delta"]
        elif chunk_type == "block-end":
            self._open.pop(chunk["index"], None)
            self._blocks.append(chunk["block"])
        elif chunk_type == "usage":
            self._usage = chunk["usage"]
        elif chunk_type == "finish":
            self._finish = chunk["reason"]
            self._replay_state = chunk.get("replay_state")

    @property
    def blocks(self):
        """已完成内容块列表（assembler.ts:134）。"""
        return list(self._blocks)

    @property
    def usage(self):
        """最近一次 usage 快照（assembler.ts:142）。"""
        return self._usage

    @property
    def finish(self):
        """终止原因（assembler.ts:147）。"""
        return self._finish

    @property
    def replay_state(self):
        """adapter 私有重放状态（assembler.ts:152）。"""
        return self._replay_state

    def message(self):
        """把累积结果转一条 assistant 消息（assembler.ts:161）。"""
        return {"role": "assistant", "content": [dict(block) for block in self._blocks]}
