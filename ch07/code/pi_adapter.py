"""第 7 章教学重构：Pi.AI 适配器（教学桩：SDK 事件来自内置样例）。

仅标准库，Python 3.10+。

源码对应（行号取自 notes §2.3）：
- PiAiAdapter ↔ packages/llm/llm-pi-ai/src/adapter.ts:186（stream :276 / resolveModel :251）
- to_stream_chunks ↔ stream.ts:124；map_stop_reason ↔ stream.ts:73
"""
from __future__ import annotations

from llm_runtime import LlmAdapter

__all__ = ["PiAiStubAdapter", "to_stream_chunks", "map_stop_reason"]

# [教学桩] 模拟 Pi.AI SDK streamSimple 输出的事件序列。
FAKE_SDK_EVENTS = (
    {"kind": "text", "text": "你好，"},
    {"kind": "text", "text": "我是 Pi。"},
    {"kind": "usage", "input": 9, "output": 6},
    {"kind": "stop", "reason": "end_turn"},
)


def map_stop_reason(raw):
    """SDK 停止原因 → 统一 FinishReason（stream.ts:73）。"""
    return {"end_turn": "stop", "max_tokens": "max-tokens", "tool_use": "tool-calls"}.get(raw, "error")


def to_stream_chunks(events):
    """SDK 事件流 → 统一 StreamChunk 序列（stream.ts:124）。"""
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
    """Pi.AI 适配器（教学桩）：真实版经 SDK streamSimple（adapter.ts:276）。

    与 DeepSeek 适配器对照：上游形态不同（SDK 事件 vs SSE 字节流），
    但出口都是统一 StreamChunk —— 差异被适配器吸收。
    """

    def __init__(self, provider_id="pi-ai", fail=False):
        self._provider_id = provider_id
        self._fail = fail  # 演示异常归一化：stream 时抛错

    def provider_info(self):
        return {"id": self._provider_id, "name": "Pi.AI"}

    def resolve_model(self, model):
        """解析模型别名 → 真实模型名（adapter.ts:251）。"""
        aliases = {"fast": "pi-fast-v1", "smart": "pi-smart-v2"}
        resolved = aliases.get(model, model)
        return {"provider": self._provider_id, "id": resolved, "name": resolved}

    def stream(self, options):
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
                finish_chunk = chunk  # finish 必须最后发（block-end 先于 finish）
                continue
            yield chunk
        yield {"type": "block-end", "index": 0, "block": {"type": "text", "text": text}}
        if finish_chunk is not None:
            yield finish_chunk
