"""第 7 章教学重构：token 计量器。

仅标准库，Python 3.10+。

源码对应（行号取自 notes §2.6）：
- TokenMeter ↔ packages/llm/token-meter/src/index.ts:74（measure :116 / _foldEvent :188）
- ROLE_OVERHEAD ↔ estimate.ts:19；estimate_content ↔ estimate.ts:26；estimate_message ↔ estimate.ts:56
"""
from __future__ import annotations

__all__ = ["ROLE_OVERHEAD", "estimate_content", "estimate_message", "TokenMeter"]

ROLE_OVERHEAD = 4  # 每条消息的角色固定开销 token（estimate.ts:19）


def estimate_content(text):
    """按字符数估算内容 token（estimate.ts:26）：char/token 比 4:1，至少 1。"""
    return max(1, (len(text) + 3) // 4)


def estimate_message(message):
    """估算一条消息（estimate.ts:56）：消息头（角色开销）+ 内容。"""
    content = message.get("content", "")
    if isinstance(content, list):  # 内容块形态：只计 text 块
        content = "".join(
            block.get("text", "") for block in content if block.get("type") == "text"
        )
    return ROLE_OVERHEAD + estimate_content(content)


class TokenMeter:
    """token 计量器：把 chunk 事件流折叠成 TokenUsage（index.ts:74）。

    [教学简化] 真实版区分 provider 估算（_estimateProviderAssistant :277）；
    教学版统一用 char/token 比。
    """

    def __init__(self):
        self._usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
        }

    def measure(self, chunks):
        """消费 chunk 流，返回 usage 快照（index.ts:116）。"""
        for chunk in chunks:
            self._fold_event(chunk)
        return dict(self._usage)

    def _fold_event(self, chunk):
        """折叠单条 chunk 事件进内部累积（index.ts:188）。"""
        if chunk["type"] == "usage":
            # adapter 上报的精确计量优先：整体覆盖此前的估算（同步幂等）
            self._usage.update(chunk["usage"])
        elif chunk["type"] == "text-delta":
            # 尚无 usage 上报：按字符数估算输出增量
            self._usage["output_tokens"] += estimate_content(chunk["text"])
