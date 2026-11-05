"""第 7 章教学重构：token 计量器。

仅标准库，Python 3.10+。

源码对应（HEAD 8bd0733）：
- TokenMeter      ↔ packages/llm/token-meter/src/index.ts:87
                     （measure :151 → _sync :231 回放 Session；_foldEvent :274）
- 精确/估算判定   ↔ packages/llm/token-meter/src/measurement-projection.ts:61-75
                     （assistant/message 带 usage 就用它，否则 estimateMessage）
- usageOf         ↔ packages/llm/token-meter/src/usage-projection.ts:82-85
- ROLE_OVERHEAD   ↔ packages/llm/token-meter/src/estimate.ts:19
- estimateContent ↔ packages/llm/token-meter/src/estimate.ts:42
- estimateMessage ↔ packages/llm/token-meter/src/estimate.ts:88
"""
from __future__ import annotations

__all__ = [
    "estimate_message",
    "TokenMeter",
]

ROLE_OVERHEAD = 4  # 每条消息的角色固定开销 token（estimate.ts:19）
BLOCK_OVERHEAD = 4  # 每个内容块的结构开销（estimate.ts:16）
CHARS_PER_TOKEN = 4  # 固定文本密度（estimate.ts:13）


def estimate_content(text):
    """按字符数估算内容 token（estimate.ts:42）：char/token 比 4:1，加块结构开销。"""
    return BLOCK_OVERHEAD + max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def estimate_message(message):
    """估算一条消息（estimate.ts:88）：内容块价格 + 角色开销。"""
    content = message.get("content", "")
    if isinstance(content, list):  # 内容块形态：逐块计价后求和
        blocks = sum(estimate_content(block.get("text", "")) for block in content if block.get("type") == "text")
    else:
        blocks = estimate_content(content)
    return blocks + ROLE_OVERHEAD


class TokenMeter:
    """token 计量器：把 chunk 事件流折叠成 TokenUsage（index.ts:87）。

    真实版 measure（index.ts:151）先经 _sync（:231）回放 Session 日志；每条
    assistant/message 的精确/估算判定在 measurement-projection.ts:61-75 ——
    带 usage 就记精确值，否则用 estimateMessage 估算；_foldEvent（:274）只维护
    surface 与锚点的价格缓存，不读 usage。教学版把「回放」简化成直接吃 chunk。
    [教学简化] 真实版区分 provider 估算与图片路由定价；教学版统一用 char/token 比。
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
        """消费 chunk 流，返回 usage 快照（index.ts:151）。"""
        for chunk in chunks:
            self._fold_event(chunk)
        return dict(self._usage)

    def _fold_event(self, chunk):
        """折叠单条 chunk 事件进内部累积（index.ts:274 的教学对应）。"""
        if chunk["type"] == "usage":
            # 以 adapter 上报的快照为准：逐键覆盖此前的估算，缺的键保留旧值
            # （与 packages/llm/llm-deepseek/src/translate.ts:34 updateUsage、
            #  packages/llm/llm/src/assembler.ts:182 的 usage 快照同一语义）
            self._usage.update(chunk["usage"])
        elif chunk["type"] == "text-delta":
            # 尚无 usage 上报：按字符数估算输出增量
            self._usage["output_tokens"] += estimate_content(chunk["text"])
