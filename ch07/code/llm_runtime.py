"""第 7 章教学重构：LLM 运行时内核 —— 适配器 seam + 统一流式入口 + 块累积器。

仅标准库，Python 3.10+。

源码对应（HEAD 8bd0733）：
- LlmAdapter      ↔ packages/llm/llm/src/index.ts:208（providerInfo :214 /
                     providerRetryPolicy :223 / listModels :260 / resolveModel :273 /
                     prepareCall :290 / stream :302）
- LlmRuntime      ↔ packages/llm/llm/src/index.ts:354（registerAdapter :401 /
                     prepareRoutes :437 / listProviders :483 / registration :1020 /
                     resolveCallFor :902 / adapterStream :1068 / stream :1170 /
                     streamWithRegistration :1174 / adapterFailureChunk :1188）
- StreamChunk     ↔ packages/llm/llm/src/types.ts:496（7 变体）
- BlockAssembler  ↔ packages/llm/llm/src/assembler.ts:38（blocks :158 / usage :182 /
                     finish :187 / replayState :196 / message :205）
"""
from __future__ import annotations

from abc import ABC, abstractmethod

__all__ = [
    "LlmAdapter",
    "LlmRuntime",
    "BlockAssembler",
]

# StreamChunk 的 7 个变体（packages/llm/llm/src/types.ts:496）；教学版用 dict 表达，靠 "type" 判别。
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

    对应 LlmAdapter（packages/llm/llm/src/index.ts:208）：其余方法均有默认实现，子类按需覆写。
    [教学决策] 真实版 stream 是 async 生成器；教学版用同步生成器，机制语义不变。
    """

    def provider_info(self, provider):
        """provider 元数据：路由键 id 必须等于注册时的 provider（index.ts:214）。"""
        return {"id": provider, "name": provider}

    def provider_retry_policy(self, provider):
        """重试策略：adapter 元数据，默认 None 表示用默认策略（index.ts:223）。

        注册时被 runtime 读走并冻结进注册表（index.ts:450），之后由 llm-retry
        插件在 agent/request-error 点上执行 —— 不是 runtime 里的重试循环。
        """
        return None

    def list_models(self, provider):
        """模型列表：默认空列表，子类覆写（index.ts:260）。"""
        return []

    def resolve_model(self, provider, model, signal=None):
        """把模型解析为 {provider, id, name}（index.ts:273）。

        signal 用于取消这次模型元数据查询；教学桩不发起 I/O，只保留签名。
        """
        return {"provider": provider, "id": model, "name": model}

    def prepare_call(self, provider, model):
        """绑定一次请求的模型元数据与派发入口（index.ts:290）。

        真实版返回 {model, stream}，让 header 记录与流式派发共享同一代 adapter。
        """
        return {"model": self.resolve_model(provider, model), "stream": self.stream}

    @abstractmethod
    def stream(self, options):
        """抽象方法：接收 GenerateOptions，逐条 yield StreamChunk（index.ts:302）。"""


class LlmRuntime:
    """运行时内核：适配器注册表 + 统一流式入口。

    对应 LlmRuntime（packages/llm/llm/src/index.ts:354）。调用方只见 stream()；
    provider 路由、异常归一化都发生在 runtime 内部。
    """

    def __init__(self):
        self._adapters = {}   # provider id -> {"adapter": ..., "retry_policy": ...}
        self._default = None  # 默认 provider（首个注册者）

    def register_adapter(self, providers, adapter):
        """为一个或多个 provider 路由注册 adapter，返回带 dispose/replace 的句柄（index.ts:401）。

        先整批校验再写入（prepareRoutes :437）：路由名不能重复，adapter 的
        provider_info(provider)["id"] 必须原样回显路由名；任一不通过就整体失败。
        [教学简化] 真实版的重复路由检查在 ctx.effect 回调内（index.ts:91-99），
        且句柄本身就是 disposer 函数（index.ts:309-327）；教学版用 dict 暴露 dispose 键。
        """
        if not providers:
            raise ValueError("an adapter must register at least one provider")
        seen = set()
        for provider in providers:
            if provider in seen:
                raise ValueError(f"duplicate provider {provider!r} in one registration")
            seen.add(provider)
            if provider in self._adapters:
                raise ValueError(f"adapter for provider {provider!r} is already registered")
            if adapter.provider_info(provider)["id"] != provider:
                raise ValueError(f"adapter metadata for {provider!r} must preserve its id")
        previous = {provider: self._adapters.get(provider) for provider in providers}
        # 整批校验通过后才写入；重试策略在注册瞬间读走并冻结（index.ts:450）
        for provider in providers:
            self._adapters[provider] = {
                "adapter": adapter,
                "retry_policy": adapter.provider_retry_policy(provider),
            }
            if self._default is None:
                self._default = provider

        def dispose():
            for provider in providers:
                if self._adapters.get(provider, {}).get("adapter") is adapter:
                    del self._adapters[provider]

        def replace(new_adapter):
            for provider in providers:
                self._adapters[provider] = {
                    "adapter": new_adapter,
                    "retry_policy": new_adapter.provider_retry_policy(provider),
                }

        return {"dispose": dispose, "replace": replace, "previous": previous}

    def list_providers(self):
        """列出全部已注册 provider 的元数据（index.ts:483）。"""
        return [entry["adapter"].provider_info(provider) for provider, entry in self._adapters.items()]

    def provider_retry_policy(self, provider):
        """读回注册时冻结的重试策略（index.ts:668）。"""
        return self._adapters[provider]["retry_policy"]

    def resolve_call_for(self, provider):
        """按 provider 定位注册项，缺失即抛错（index.ts:1020 的 registration()）。

        真实版 resolveCallFor（index.ts:902）接收的是已解析好的注册项，做模型信息
        解析与控制项校验；「查表 + NO_ADAPTER」发生在 registration()。
        """
        entry = self._adapters.get(provider)
        if entry is None:
            raise KeyError(f"no adapter registered for provider {provider!r}")
        return entry["adapter"]

    def stream(self, messages, system_prompt=None, provider=None, model=None, **options):
        """对外统一入口（index.ts:1170 → streamWithRegistration :1174）。

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
        """最终 adapter 边界（index.ts:1068）：try/except 包住查表、prepare_call 与 stream。

        adapter 边界内的异常都归一化为终止 finish chunk；真实的中间件与下游消费者
        异常仍然照常抛出（index.ts:1063-1066 的注释就是这个范围）。
        [教学简化] 真实版还有 waterfall('llm/stream') 事件包装、prepareCall 代际绑定
        与 file/image/tool-update 投影；教学版直接 route → prepare_call → stream。
        """
        # 真实的查表（registration :1020）在 try 内；教学版把它挪到外侧，查不到
        # 直接抛 KeyError，演示「路由错误在入口暴露」这一点。
        adapter = self.resolve_call_for(options["provider"])
        try:
            call = adapter.prepare_call(options["provider"], options["model"])
            yield from call["stream"](options)
        except Exception as exc:  # 归一化：异常 → 终止 chunk
            yield self._failure_chunk(exc)

    @staticmethod
    def _failure_chunk(exc):
        """把异常构造为终止 chunk（index.ts:1188 adapterFailureChunk）。"""
        return {"type": "finish", "reason": "error", "error": str(exc)}


class BlockAssembler:
    """流式块累积器：把 *-delta 增量累积成完整内容块。

    对应 BlockAssembler（packages/llm/llm/src/assembler.ts:38）：push() 逐条消费 chunk，
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
        """已完成内容块列表（assembler.ts:158）。"""
        return list(self._blocks)

    @property
    def usage(self):
        """最近一次 usage 快照（assembler.ts:182）。"""
        return self._usage

    @property
    def finish(self):
        """终止原因（assembler.ts:187）。"""
        return self._finish

    @property
    def replay_state(self):
        """adapter 私有重放状态（assembler.ts:196）。"""
        return self._replay_state

    def message(self):
        """把累积结果转一条 assistant 消息（assembler.ts:205）。

        真实版还要一个 source 参数，记录这条消息由哪个插件/模型产生。
        """
        return {"role": "assistant", "content": [dict(block) for block in self._blocks]}
