"""第 7 章教学重构：DeepSeek 适配器（教学桩：无网络，SSE 来自内置样例）。

仅标准库，Python 3.10+。

HEAD（8bd0733）把 llm-deepseek 拆成了多文件，三段流水线各归其位：
- DeepSeekAdapter      ↔ packages/llm/llm-deepseek/src/adapter.ts:22
                          （stream :55 / generate :65 / request :91 / resolveAuth :98 /
                           prepareRequestExtensions :127 → fetch :142 → parseSse :169）
- serialize            ↔ packages/llm/llm-deepseek/src/serialize.ts:57
                          （system 槽位 :151 / 请求体组装 :152）
- parseSse             ↔ packages/llm/llm-deepseek/src/sse.ts:13
- translate            ↔ packages/llm/llm-deepseek/src/translate.ts:104
                          （startBlock :45 / deltaChunk :65 / stopReason :90 / updateUsage :34）
- 请求扩展事务         ↔ packages/llm/llm-deepseek/src/request-extensions.ts:21
- 端点与 beta 头       ↔ packages/llm/llm-deepseek/src/messages-api.ts:14
"""
from __future__ import annotations

import json

from llm_runtime import LlmAdapter

__all__ = [
    "DeepSeekStubAdapter",
    "RequestExtensionRegistry",
]

# [教学桩] 模拟 DeepSeek Messages 流式响应的 SSE 文本（不发起网络请求）。
FAKE_SSE_RESPONSE = """event: message_start
data: {"type":"message_start","message":{"usage":{"input_tokens":8,"cache_read_input_tokens":4}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"先拆解问题，"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"再给结论。"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"DeepSeek 是"}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"一家模型公司。"}}

event: content_block_stop
data: {"type":"content_block_stop","index":1}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":8}}

event: message_stop
data: {"type":"message_stop"}
"""


def serialize_request(options):
    """把 GenerateOptions 映射为 Messages 请求体（serialize.ts:57）。

    system 占独立顶层槽位，不混入 messages（serialize.ts:151）；stream 恒为 True
    （serialize.ts:152）。教学版只保留这三件事，消息投影与工具/图片处理见源码。
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
    """把 SSE 文本解析为事件 dict 序列（sse.ts:13）。

    真实版用 eventsource-parser 切帧，帧的 event 字段必须与 data 里的 type 一致
    （sse.ts:22）；教学版按空行切帧、校验同一件事。
    """
    data_lines = []
    name = None
    for line in text.splitlines() + [""]:
        if line == "":
            if data_lines:
                event = json.loads("\n".join(data_lines))
                if name is not None and name != event.get("type"):
                    raise ValueError(f"SSE event type mismatch: {name} != {event.get('type')}")
                yield event
            data_lines, name = [], None
            continue
        if line.startswith("event:"):
            name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())


def map_stop_reason(raw):
    """Messages stop_reason → 统一 FinishReason（translate.ts:90）。"""
    return {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "tool_use": "tool-calls",
        "max_tokens": "max-tokens",
    }.get(raw, "error")


def map_usage(usage):
    """字段级映射：Messages usage → TokenUsage（translate.ts:34 updateUsage）。

    计数字段不相交：input_tokens 只计缓存未命中部分，cache_read/creation 单列
    （packages/llm/llm/src/types.ts:182 的 TokenUsage 注释）。
    只映射上游真的给出的字段 —— updateUsage 逐键覆盖，缺的键保留旧值。
    """
    keys = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "cache_read_input_tokens": "cache_read_tokens",
        "cache_creation_input_tokens": "cache_write_tokens",
    }
    return {local: usage[wire] for wire, local in keys.items() if wire in usage}


def translate(events):
    """把 Messages 事件流翻译为统一 StreamChunk（translate.ts:104）。

    StreamChunk 协议要求 block-end 携带完整块（packages/llm/llm/src/types.ts:496），
    而上游只有增量 —— 所以 translate 是**有状态生成器**：按 wire index 累积各块，
    content_block_stop 时整块吐出；message_stop 时发 usage 与唯一一条 finish，
    finish 里带 replayState（translate.ts:161）。
    """
    blocks = {}   # wire index -> {"index", "block_type", "text", "signature"}
    usage = map_usage({})
    reason = None
    for event in events:
        event_type = event.get("type")
        if event_type == "message_start":
            usage = map_usage(event.get("message", {}).get("usage", {}))
        elif event_type == "content_block_start":
            native = event["content_block"]
            # wire 的 "thinking" 在协议里叫 "reasoning"（translate.ts:51-53 startBlock）
            block_type = "reasoning" if native["type"] == "thinking" else native["type"]
            block = {
                "index": len(blocks),
                "block_type": block_type,
                "text": native.get("thinking", native.get("text", "")),
                "signature": native.get("signature", ""),
            }
            blocks[event["index"]] = block
            yield {"type": "block-start", "index": block["index"], "block_type": block["block_type"]}
        elif event_type == "content_block_delta":
            block = blocks[event["index"]]
            delta = event["delta"]
            if delta["type"] in ("text_delta", "thinking_delta"):
                text = delta.get("text", delta.get("thinking", ""))
                block["text"] += text
                kind = "text" if delta["type"] == "text_delta" else "reasoning"
                yield {"type": f"{kind}-delta", "index": block["index"], "text": text}
            elif delta["type"] == "signature_delta":
                block["signature"] += delta["signature"]   # 不进 chunk，只进 replayState
        elif event_type == "content_block_stop":
            block = blocks[event["index"]]
            yield {
                "type": "block-end",
                "index": block["index"],
                "block": {"type": block["block_type"], "text": block["text"]},
            }
        elif event_type == "message_delta":
            if event["delta"].get("stop_reason") is not None:
                reason = map_stop_reason(event["delta"]["stop_reason"])
            if "usage" in event:
                usage.update(map_usage(event["usage"]))
        elif event_type == "message_stop":
            # 终局 usage 补上总量（translate.ts:159），再发唯一一条 finish（:161 带 replayState）
            total = usage["input_tokens"] + usage["output_tokens"] \
                + usage.get("cache_read_tokens", 0) + usage.get("cache_write_tokens", 0)
            yield {"type": "usage", "usage": {**usage, "total_tokens": total}}
            yield {
                "type": "finish",
                "reason": reason or "error",
                "replay_state": {"model": event.get("model"), "blocks": [b["signature"] for b in blocks.values()]},
            }


class RequestExtensionRegistry:
    """请求扩展注册表（教学版）：插件各自贡献一个顶层字段，adapter 做一次 prepare/accept 事务。

    对应 packages/llm/deepseek-llm-api-extensions/src/index.ts:66。
    字段名全局唯一（第二次注册直接抛错），prepare 的返回值被克隆冻结，
    失败只让该字段本次缺席 —— 不是让整个请求失败。
    """

    def __init__(self, warn=None):
        self._providers = {}
        self._warn = warn or (lambda message: None)

    def register(self, field, provider):
        """登记一个字段的贡献者，返回注销函数（deepseek-llm-api-extensions/src/index.ts:81）。"""
        if not field or field != field.strip():
            raise ValueError("field must be a non-blank trimmed string")
        if field in self._providers:
            raise ValueError(f"field {field!r} is already registered")
        self._providers[field] = provider

        def dispose():
            self._providers.pop(field, None)

        return dispose

    def prepare(self, request):
        """对当前全部贡献者跑一次 prepare，返回 {fields, accept}（index.ts:111）。"""
        fields, callbacks = {}, []
        for field, provider in self._providers.items():
            try:
                result = provider.prepare(request)
            except Exception as error:   # 单个贡献者失败：本次省略，不影响其他字段
                self._warn(f"omitting {field} from this request: {error}")
                continue
            if result is None:
                continue
            fields[field] = json.loads(json.dumps(result["value"]))   # 克隆：提供者拿不到请求别名
            if result.get("accept") is not None:
                callbacks.append(result["accept"])

        accepted = {"done": False}

        def accept():
            # 幂等：重复调用合并到同一次结算（index.ts:139）
            if not accepted["done"]:
                accepted["done"] = True
                for callback in callbacks:
                    callback()

        return {"fields": fields, "accept": accept}


class DeepSeekStubAdapter(LlmAdapter):
    """DeepSeek 适配器（教学桩）。

    真实版 request（adapter.ts:91）：resolveAuth 取凭据头 → 构造消息体 →
    prepareRequestExtensions 合并扩展字段 → fetch（adapter.ts:142）→ accept →
    parseSse → translate；stream/generate（adapter.ts:55/:65）在外面套 idle watchdog
    与 abort 生命周期。教学版用内置 FAKE_SSE_RESPONSE 顶替 fetch 响应。
    """

    def __init__(self, credentials, api_key_ref="deepseek/api-key", extensions=None):
        self._credentials = credentials
        self._api_key_ref = api_key_ref
        self._extensions = extensions or RequestExtensionRegistry()

    def provider_info(self, provider):
        return {"id": provider, "name": "DeepSeek"}

    def provider_retry_policy(self, provider):
        """重试策略是 adapter 元数据（adapter.ts:35 → LlmAdapter.providerRetryPolicy）。"""
        return {"mode": "normal", "max_retries": 2, "retryable_codes": ["RATE_LIMIT", "SERVER"]}

    def list_models(self, provider):
        return [{"id": "deepseek-chat", "name": "DeepSeek Chat"}]

    def resolve_auth(self, ref):
        """经凭据 seam 解析凭据头（adapter.ts:98 → credential.ts:15 resolveDeepSeekApiKey）。

        只在构造请求前的最后一步调用；未配置时返回 None，由上层报错。
        """
        hit = self._credentials.resolve(ref)
        if hit is None:
            return None
        return {"headers": {"x-api-key": hit["value"]}}

    def stream(self, options):
        """真实版 stream（adapter.ts:55）：admit → generate；generate 包 watchdog。"""
        return self.request(options)

    def request(self, options):
        """一次请求的完整三段流水线（adapter.ts:91）。"""
        auth = self.resolve_auth(self._api_key_ref)          # 1) 凭据 seam
        body = serialize_request(options)                    # 2) serialize（serialize.ts:57）
        extension = self._extensions.prepare({               # 3) 请求扩展 prepare
            "body": body,
            "sessionId": options.get("session_id"),
            "purpose": options.get("purpose"),
        })
        payload = {**body, **extension["fields"]}            # 4) 合并顶层字段
        response = self.send(payload, auth)                  # 5) fetch（教学桩）
        extension["accept"]()                                # 6) HTTP 2xx 后才 accept
        return translate(parse_sse(response))                # 7) SSE → StreamChunk

    def send(self, payload, auth):
        """构造并"发送"请求（adapter.ts:142）。

        [教学桩] 不发起网络请求；headers 与真实版同形，响应换成内置样例。
        """
        return FAKE_SSE_RESPONSE
