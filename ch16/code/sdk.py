"""第 16 章教学重构：JSON-RPC SDK（packages/sdk）。

源码对应：
- JsonRpcLineTransport        ↔ JsonRpcLineTransport（sdk/protocol/src/transport.ts:62）
- request / -32601 / -32603   ↔ request（transport.ts:121）
- SERVER_REQUESTS             ↔ HarnessSdkRequestMap（sdk/protocol/src/types.ts:101）
- SERVER_NOTIFICATIONS        ↔ HarnessSdkNotificationMap（sdk/protocol/src/types.ts:93，共 4 个）
- HarnessSdkJsonRpcServer     ↔ HarnessSdkJsonRpcServer（sdk/server/src/server.ts:53）
- HarnessClient               ↔ HarnessClient（sdk/client/src/client.ts:184）
- DeepSeekHarness / HarnessSession ↔ DeepSeekHarness / HarnessSession（sdk/client/src/api.ts:22/:132）

[教学简化] 真实传输是 stdio 上的换行分隔 JSON-RPC 2.0，客户端 spawn 子进程；
教学版用两个进程内对象模拟双向管道，保留协议三要素：「一行一个 JSON 对象」
帧、按 id 匹配响应、稳定错误码。
"""
from __future__ import annotations

import itertools
import json

__all__ = [
    "DeepSeekHarness", "HarnessClient", "HarnessSdkJsonRpcServer",
    "HarnessSession", "JsonRpcLineTransport", "SERVER_NOTIFICATIONS",
    "SERVER_REQUESTS",
]

# JSON-RPC 2.0 稳定错误码（transport.ts:121：无 handler 返回 -32601，失败返回 -32603）
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603

# 服务端三个请求 ↔ HarnessSdkRequestMap（types.ts:101）
SERVER_REQUESTS = ("initialize", "session.prompt", "shutdown")
# 真实有 4 个通知 ↔ HarnessSdkNotificationMap（types.ts:93）。
# [教学决策] 笔记未枚举通知名，下面四个名字是教学示意名，非源码原名。
SERVER_NOTIFICATIONS = ("session.started", "session.chunk", "session.finished", "session.error")


class JsonRpcLineTransport:
    """换行分隔的 JSON-RPC 2.0 ↔ JsonRpcLineTransport（transport.ts:62）。

    一行 = 一个 JSON 对象：带 method + id 是请求，只有 method 是通知，
    带 id 无 method 是响应；响应沿同一条线回来，按 id 匹配。
    [教学简化] 真实是 stdio 异步 I/O，request 支持 AbortSignal（transport.ts:121）；
    教学版是进程内同步调用，没有超时与中止。
    """

    def __init__(self, role):
        self.role = role                  # 'client' / 'server'，仅用于观察输出
        self._peer = None
        self._handlers = {}               # method -> 请求 handler
        self._notification_handlers = {}  # method -> 通知 handler
        self._pending = {}                # id -> 响应（教学版同步送达）
        self._seq = itertools.count(1)

    def connect(self, peer):
        """把管道两端接成一对（教学版的 stdio 对）。"""
        self._peer = peer
        peer._peer = self

    def on_request(self, method, handler):
        """登记请求 handler：收到带 id 的该 method 请求时调用。"""
        self._handlers[method] = handler

    def on_notification(self, method, handler):
        """登记通知 handler：收到无 id 的该 method 通知时调用。"""
        self._notification_handlers[method] = handler

    # ---------- 发送侧 ----------

    def request(self, method, params=None):
        """发请求并等待按 id 匹配的响应 ↔ request（transport.ts:121）。"""
        req_id = f"req_{next(self._seq)}"
        line = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method,
                           "params": params or {}}, ensure_ascii=False)
        self._peer.send_line(line)        # 对端进程内同步处理，并把响应写回
        response = self._pending.pop(req_id)
        if "error" in response:
            error = response["error"]
            raise RuntimeError(f"JSON-RPC 错误 {error['code']}: {error['message']}")
        return response["result"]

    def notify(self, method, params=None):
        """发通知：没有 id，不期待响应。"""
        line = json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}},
                          ensure_ascii=False)
        self._peer.send_line(line)

    # ---------- 接收侧 ----------

    def send_line(self, line):
        """收到对端一行 JSON，按形状分发。

        无 method → 响应（按 id 放进 _pending）；
        有 method 有 id → 请求；有 method 无 id → 通知。
        """
        message = json.loads(line)
        if "method" not in message:
            self._pending[message["id"]] = message
        elif "id" in message:
            self._handle_request(message)
        else:
            handler = self._notification_handlers.get(message["method"])
            if handler is not None:
                handler(message.get("params", {}))

    def _handle_request(self, message):
        handler = self._handlers.get(message["method"])
        if handler is None:
            # 无 handler 返回 -32601（transport.ts:121）
            self._reply(message["id"], error={"code": METHOD_NOT_FOUND,
                                              "message": f"method not found: {message['method']}"})
            return
        try:
            result = handler(message.get("params", {}))
        except Exception as exc:  # handler 失败返回 -32603
            self._reply(message["id"], error={"code": INTERNAL_ERROR, "message": str(exc)})
            return
        self._reply(message["id"], result=result)

    def _reply(self, req_id, result=None, error=None):
        """把响应写回对端：error 与 result 二选一。"""
        payload = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result
        self._peer.send_line(json.dumps(payload, ensure_ascii=False))


class HarnessSdkJsonRpcServer:
    """服务端插件 ↔ HarnessSdkJsonRpcServer（server.ts:53）。

    三个请求：initialize（server.ts:111，挂载 fallback adapter）/
    session.prompt（:132）/ shutdown（:150，优雅退出）。
    """

    def __init__(self, transport, prompt_handler):
        self.transport = transport
        self._prompt_handler = prompt_handler  # text -> chunk 序列（教学版的模型流）
        self.initialized = False
        for method in SERVER_REQUESTS:
            handler = getattr(self, "_" + method.replace(".", "_"))
            transport.on_request(method, handler)

    def _initialize(self, params):
        self.initialized = True
        return {"serverInfo": {"name": "deepseek-harness（教学 stub）"}}

    def _session_prompt(self, params):
        if not self.initialized:
            raise RuntimeError("请先 initialize")  # 顺序契约：先 initialize 再 prompt
        self.transport.notify("session.started", {"sessionId": params.get("sessionId", "s-1")})
        chunks = list(self._prompt_handler(params["text"]))
        for chunk in chunks:
            self.transport.notify("session.chunk", {"text": chunk})
        self.transport.notify("session.finished", {"chunks": len(chunks)})
        # ↔ finalResponse（api.ts:236）：最终响应聚合
        return {"finalResponse": "".join(chunks)}

    def _shutdown(self, params):
        self.initialized = False
        return {"ok": True}


class HarnessClient:
    """子进程客户端 ↔ HarnessClient（client.ts:184）。

    [教学简化] 真实 start() spawn 子进程并走 stdio（client.ts:203）；教学版直接
    连进程内服务端，只保留「destroy() 按 EOF → SIGTERM → SIGKILL 阶梯走，
    对端已退出就不再升级」这一可观察行为。
    """

    def __init__(self, make_server):
        self._make_server = make_server
        self.transport = JsonRpcLineTransport("client")
        self.notifications = []    # 收集到的通知 (name, params)，供 finalResponse 聚合
        self.destroy_trace = []    # 销毁阶梯足迹（教学观察用）
        self._peer_closed = False

    def start(self):
        """↔ HarnessClient.start（client.ts:203）：建立传输，挂起服务端。"""
        server_transport = JsonRpcLineTransport("server")
        self._make_server(server_transport)
        self.transport.connect(server_transport)
        for name in SERVER_NOTIFICATIONS:
            self.transport.on_notification(
                name, lambda params, n=name: self.notifications.append((n, params)))
        return self

    def request(self, method, params=None):
        result = self.transport.request(method, params)
        if method == "shutdown":
            self._peer_closed = True  # 优雅 shutdown 后子进程已退出（教学版模拟）
        return result

    def destroy(self):
        """销毁阶梯：EOF → SIGTERM → SIGKILL（client.ts:184），对端已退出即停止升级。"""
        for step in ("EOF", "SIGTERM", "SIGKILL"):
            self.destroy_trace.append(step)
            if self._peer_closed:
                break                 # 上一级已让对端退出，不必升级
            if step == "EOF":
                self._peer_closed = True  # 教学版：EOF 即视为关闭管道成功
        return self.destroy_trace


class DeepSeekHarness:
    """高层 API ↔ DeepSeekHarness（api.ts:22）：懒启动，管整个生命周期。"""

    def __init__(self, make_server):
        self._client = HarnessClient(make_server)

    def start(self):
        self._client.start()
        self._client.request("initialize", {})  # 启动后第一个请求必须是 initialize
        return self

    def session(self):
        return HarnessSession(self._client)

    def shutdown(self):
        try:
            self._client.request("shutdown", {})
        finally:
            self._client.destroy()

    @property
    def destroy_trace(self):
        return self._client.destroy_trace


class HarnessSession:
    """会话 API ↔ HarnessSession（api.ts:132）。"""

    def __init__(self, client):
        self._client = client

    def prompt(self, text):
        """发一次 prompt：返回 finalResponse 与本次收到的 chunk 列表。"""
        before = len(self._client.notifications)
        result = self._client.request("session.prompt", {"text": text})
        chunks = [params["text"] for name, params in self._client.notifications[before:]
                  if name == "session.chunk"]
        return {"finalResponse": result["finalResponse"], "chunks": chunks}
