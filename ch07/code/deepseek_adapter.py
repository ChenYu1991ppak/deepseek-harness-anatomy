"""第 7 章教学重构：DeepSeek 适配器（教学桩：无网络，SSE 来自内置样例）。

仅标准库，Python 3.10+。

源码对应（行号取自 notes §2.2）：
- DeepSeekAdapter ↔ packages/llm/llm-deepseek/src/adapter.ts:158（stream :214 / request :271）
- serialize_request ↔ serialize.ts:151；parse_sse ↔ sse.ts:28；translate ↔ translate.ts:86
  （map_finish_reason ↔ translate.ts:31；map_usage ↔ translate.ts:53）
"""
from __future__ import annotations

import json

from llm_runtime import LlmAdapter

__all__ = [
    "DeepSeekStubAdapter",
    "serialize_request",
    "parse_sse",
    "translate",
    "map_usage",
    "map_finish_reason",
]

# [教学桩] 模拟 DeepSeek chat/completions 流式响应的 SSE 文本（不发起网络请求）。
FAKE_SSE_RESPONSE = """data: {"choices":[{"delta":{"reasoning_content":"先拆解问题，"}}]}

data: {"choices":[{"delta":{"reasoning_content":"再给结论。"}}]}

data: {"choices":[{"delta":{"content":"DeepSeek 是"}}]}

data: {"choices":[{"delta":{"content":"一家模型公司。"}}],"usage":{"prompt_tokens":12,"completion_tokens":8,"cached_tokens":4}}

data: {"choices":[{"finish_reason":"stop","delta":{}}]}

data: [DONE]
"""


def serialize_request(options):
    """把 GenerateOptions 映射为 DeepSeek 请求体（serialize.ts:151）。

    system 占独立槽位，不混入 messages；stream 恒为 True（本章只讲流式）。
    """
    body = {
        "model": options.get("model"),
        "messages": list(options.get("messages", [])),
        "stream": True,
    }
    if options.get("system"):
        body["system"] = options["system"]
    if options.get("temperature") is not None:
        body["temperature"] = options["temperature"]
    if options.get("max_tokens") is not None:
        body["max_tokens"] = options["max_tokens"]
    return body


def parse_sse(text):
    """把 SSE 文本解析为事件 dict 序列（sse.ts:28）；[DONE] 帧终止。"""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        yield json.loads(payload)


def map_finish_reason(raw):
    """DeepSeek finish_reason → 统一 FinishReason（translate.ts:31）。"""
    return {"stop": "stop", "tool_calls": "tool-calls", "length": "max-tokens"}.get(raw, "error")


def map_usage(usage):
    """字段级映射：DeepSeek usage → TokenUsage（translate.ts:53）。

    input_tokens 只计缓存未命中部分；cached_tokens 归入 cache_read_tokens。
    """
    cached = usage.get("cached_tokens", 0)
    details = usage.get("completion_tokens_details") or {}
    return {
        "input_tokens": usage.get("prompt_tokens", 0) - cached,
        "output_tokens": usage.get("completion_tokens", 0),
        "cache_read_tokens": cached,
        "cache_write_tokens": 0,  # DeepSeek 无 cache-write 概念
        "reasoning_tokens": details.get("reasoning_tokens", 0),
    }


def translate(events):
    """把 DeepSeek 事件流翻译为统一 StreamChunk（translate.ts:86）。

    StreamChunk 协议要求 block-end 携带完整块（types.ts:291），
    因此 translate 内部累积各块文本，在块切换/终止点整块吐出。
    """
    open_block = None  # 正在累积的块：{"index", "block_type", "text"}
    for event in events:
        choice = (event.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        for field, block_type, chunk_type in (
            ("reasoning_content", "reasoning", "reasoning-delta"),
            ("content", "text", "text-delta"),
        ):
            if field not in delta:
                continue
            if open_block is None or open_block["block_type"] != block_type:
                if open_block is not None:
                    yield _close_block(open_block)
                open_block = {"index": 0, "block_type": block_type, "text": ""}
                yield {"type": "block-start", "index": 0, "block_type": block_type}
            open_block["text"] += delta[field]
            yield {"type": chunk_type, "index": 0, "text": delta[field]}
        if "usage" in event:
            yield {"type": "usage", "usage": map_usage(event["usage"])}
        if choice.get("finish_reason"):
            if open_block is not None:
                yield _close_block(open_block)
                open_block = None
            yield {"type": "finish", "reason": map_finish_reason(choice["finish_reason"])}


def _close_block(block):
    """构造 block-end chunk：携带完整块。"""
    return {
        "type": "block-end",
        "index": block["index"],
        "block": {"type": block["block_type"], "text": block["text"]},
    }


class DeepSeekStubAdapter(LlmAdapter):
    """DeepSeek 适配器（教学桩）。

    真实版 stream（adapter.ts:214）：resolve key → 构造请求 → fetch → SSE 解析 → translate；
    教学版用内置 FAKE_SSE_RESPONSE 顶替 fetch 响应，其余流水线与真实版一致。
    """

    def __init__(self, credentials, api_key_ref="deepseek/api-key"):
        self._credentials = credentials
        self._api_key_ref = api_key_ref

    def provider_info(self):
        return {"id": "deepseek", "name": "DeepSeek"}

    def list_models(self, provider):
        return [{"id": "deepseek-chat", "name": "DeepSeek Chat"}]

    def resolve_api_key(self):
        """经凭据 seam 解析 API key（adapter.ts:221 → credentials resolve :73）。"""
        resolved = self._credentials.resolve(self._api_key_ref)
        return resolved["secret"]

    def stream(self, options):
        api_key = self.resolve_api_key()               # 1) 凭据 seam：key 不进业务代码
        body = serialize_request(options)              # 2) 序列化：GenerateOptions → 请求体
        request = self.request(body, api_key)          # 3) 构造请求（教学版不发出）
        # 4) SSE 解析 + 5) 翻译：translate 接收整条事件流（块累积需要跨事件状态）
        yield from translate(parse_sse(request["upstream"]))

    def request(self, body, api_key):
        """构造请求（adapter.ts:271）：真实版是 fetch + abort + idle watchdog。

        [教学桩] 不发起网络请求；返回构造好的 headers/body 与内置样例响应。
        """
        return {
            "url": "https://api.deepseek.com/chat/completions",
            "headers": {"authorization": f"Bearer {api_key}"},
            "body": body,
            "upstream": FAKE_SSE_RESPONSE,
        }
