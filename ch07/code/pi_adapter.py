"""第 7 章教学重构：Pi.AI 适配器（教学桩：SDK 事件来自内置样例）。

仅标准库，Python 3.10+。

源码对应（HEAD 8bd0733）：
- PiAiAdapter     ↔ packages/llm/llm-pi-ai/src/adapter.ts:273
                     （resolveModel :356 / stream :394 / streamWithSnapshot :398 /
                      streamSimple :460）
- toStreamChunks  ↔ packages/llm/llm-pi-ai/src/stream.ts:144
- mapStopReason   ↔ packages/llm/llm-pi-ai/src/stream.ts:80
"""
from __future__ import annotations

from llm_runtime import LlmAdapter

__all__ = [
    "PiAiStubAdapter",
]

# [教学桩] 模拟 Pi.AI SDK streamSimple 输出的事件序列。
FAKE_SDK_EVENTS = (
    {"kind": "text", "text": "你好，"},
    {"kind": "text", "text": "我是 Pi。"},
    {"kind": "usage", "input": 9, "output": 6},
    {"kind": "stop", "reason": "end_turn"},
)


def map_stop_reason(raw):
    """SDK 停止原因 → 统一 FinishReason（stream.ts:80）。"""
    return {"end_turn": "stop", "max_tokens": "max-tokens", "tool_use": "tool-calls"}.get(raw, "error")


def to_stream_chunks(events):
    """SDK 事件流 → 统一 StreamChunk 序列（stream.ts:144）。

    与 DeepSeek 的 translate 对照：SSE 字节流要自己维护块状态，SDK 事件本身
    已带块边界；两者出口都是同一套七变体协议。
    """
    for event in events:
        kind = event["kind"]
        if kind == "text":
            yield {"type": "text-delta", "index": 0, "text": event["text"]}
        elif kind == "usage":
            yield {
                "type": "usage",
                "usage": {
                    "input_tokens": event["input"],
                    "output_tokens": event["output"],
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "reasoning_tokens": 0,
                },
            }
        elif kind == "stop":
            yield {"type": "finish", "reason": map_stop_reason(event["reason"])}


class PiAiStubAdapter(LlmAdapter):
    """Pi.AI 适配器（教学桩）：真实版经 SDK `Models.streamSimple()`（adapter.ts:460）。

    与 DeepSeek 适配器对照：上游形态不同（SDK 事件 vs SSE 字节流），
    但出口都是统一 StreamChunk —— 差异被适配器吸收。
    """

    def __init__(self, fail=False):
        self._fail = fail  # 演示异常归一化：stream 时抛错

    def provider_info(self, provider):
        return {"id": provider, "name": "Pi.AI"}

    def resolve_model(self, provider, model, signal=None):
        """解析模型别名 → 真实模型名（adapter.ts:356）。"""
        aliases = {"fast": "pi-fast-v1", "smart": "pi-smart-v2"}
        resolved = aliases.get(model, model)
        return {"provider": provider, "id": resolved, "name": resolved}

    def stream(self, options):
        """真实版 stream（adapter.ts:394）先取一份配置快照，再交给 streamWithSnapshot。

        教学版忽略 options：样例事件是固定的，模型别名解析在 resolve_model 里演示。
        """
        if self._fail:
            raise RuntimeError("upstream unavailable（演示异常归一化）")
        events = FAKE_SDK_EVENTS  # [教学桩] 内置样例顶替 SDK streamSimple
        text = ""
        finish_chunk = None
        yield {"type": "block-start", "index": 0, "block_type": "text"}
        for chunk in to_stream_chunks(events):
            if chunk["type"] == "text-delta":
                text += chunk["text"]
            elif chunk["type"] == "finish":
                finish_chunk = chunk  # finish 必须最后发（block-end/usage 先于 finish）
                continue
            yield chunk
        yield {"type": "block-end", "index": 0, "block": {"type": "text", "text": text}}
        if finish_chunk is not None:
            yield finish_chunk
