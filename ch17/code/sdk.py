"""ch17 教学代码：Python 内嵌面（对应 dsh-sdk / python/sdk）。

真实源码（HEAD 8bd0733）：
- `DeepSeekHarness`：`python/sdk/src/deepseek_harness/api.py:102`。构造既可以传
  `DeepSeekHarnessConfig`（`:14`），也可以直接传关键字参数（`:110-119`
  「二选一，不能同时给」）。`start()`（`:157`）懒启动子进程并 `initialize`。
- `start_session()`（`:175`）/ `resume_session()`（`:179`）/ `run()`（`:188`）
  是三个入口；`Session.run()` 在 `api.py:214`。
- `Session.run` 的实现不是「发一次 prompt 等一次回复」，而是：
  订阅通知（`api.py:236`）→ `client._query()`（`api.py:237`）拿回「回执」——
  要么是 prompt 的 messageId，要么是原生命令的 `CommandExecution`；然后逐条读
  通知，直到看到 `session.status == 'idle'`（`:265-270`）才收口成 `RunResult`。
- `RunResult`（`api.py:45`）：`session_id / final_response / finish_reason /
  events / notifications`；`final_response()`（`:298`）与 `finish_reason()`
  （`:318`）从事件流反读。
- `HarnessClient`：`python/sdk/src/deepseek_harness/client.py:42`，
  `subprocess.Popen`（`:89`）起 Node runtime，stdio 上跑换行分隔的 JSON-RPC。
  `_query` 发的是 `session/query`（`:233`），`initialize` 发 `initialize`（`:181`）。
  启动参数默认 `--profile sdk`（`_default_launch_args`，`client.py:588`）。
- 对端处理器：`packages/sdk/server/src/server.ts:505` 的 `case 'session/query'`，
  最终落到 `ctx.agents.create`（`:592`）与 `agent.followup`（`:279`）。

[教学简化] 真实链路是 Python → stdio JSON-RPC → Node runtime → agent-loop；
这里把 runtime 换成纯 Python 的迷你实现（第 2 章机制的 20 行版），模型换成
FakeModel，不经过进程间通信。但协议形状保留：`subscribe` → `query` 拿回执 →
逐条读通知直到 idle → 从事件流反读最终文本与 finish_reason。
"""

__all__ = ["DeepSeekHarness", "HarnessConfig", "FakeModel", "RunResult", "final_response", "finish_reason"]


class HarnessConfig:
    """对应 `client.py:26` 的 HarnessConfig：启动参数。"""

    def __init__(self, profile="sdk", dsh_home="/tmp/dsh-home", cwd=None):
        self.profile = profile
        self.dsh_home = dsh_home
        self.cwd = cwd


class RunResult:
    """对应 `api.py:45` 的 RunResult（dataclass 五个字段）。"""

    def __init__(self, session_id, final_response, finish_reason, events, notifications):
        self.session_id = session_id
        self.final_response = final_response
        self.finish_reason = finish_reason
        self.events = events
        self.notifications = notifications


class FakeModel:
    """[教学简化] 确定性假模型：按脚本逐轮返回，用于演示 agent-loop 往返。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def generate(self, messages):
        self.calls += 1
        return self.script.pop(0)


class DeepSeekHarness:
    """对应 api.py:102：可复用的同步入口，运行时就绪前懒启动。"""

    def __init__(self, runtime, config=None, **kwargs):
        # api.py:117-119 —— config 与关键字参数二选一
        if config is not None and kwargs:
            raise TypeError("pass either HarnessConfig or keyword options, not both")
        self._config = config or HarnessConfig(**kwargs)
        self._runtime = runtime        # [教学简化] 真实是 HarnessClient 起的子进程
        self._initialized = False

    @property
    def client(self):
        return self._runtime

    def start(self):
        if self._initialized:
            return
        self._runtime.initialize(profile=self._config.profile)
        self._initialized = True

    def close(self):
        self._runtime.close()
        self._initialized = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.close()

    def start_session(self, session_id=None):
        self.start()
        return Session(self, session_id or "session-1")

    def resume_session(self, session_id):
        """对应 api.py:179：不发输入，只恢复已持久化的历史（失败即抛）。"""
        self.start()
        self.client.resume_session(session_id)
        return Session(self, session_id)

    def run(self, user_input, *, session_id=None):
        """对应 api.py:188：一次输入跑完，返回 RunResult。"""
        return self.start_session(session_id).run(user_input)


class Session:
    def __init__(self, harness, session_id):
        self.harness = harness
        self.id = session_id

    def run(self, user_input):
        """对应 python/sdk/src/deepseek_harness/api.py:214。

        顺序与真实实现一致：订阅通知 → `_query` 拿回执 → 逐条读通知 →
        看到 `session.status == 'idle'` 才 break → 从事件流收口成 RunResult。
        """
        self.harness.start()
        events = []
        notifications = []
        with self.harness.client.subscribe_session_notifications(self.id) as subscription:
            self.harness.client._query(self.id, user_input)   # session/query 的回执
            while True:
                notification = subscription.next()
                notifications.append(notification)
                if notification["method"] == "session.event":
                    events.append(notification["payload"]["event"])
                if (notification["method"] == "session.status"
                        and notification["payload"].get("status") == "idle"):
                    break
        return RunResult(
            session_id=self.id,
            final_response=final_response(events),
            finish_reason=finish_reason(events),
            events=events,
            notifications=notifications,
        )


def final_response(events):
    """对应 api.py:298：从后往前找第一条 assistant/message，拼 text 块。"""
    for event in reversed(events):
        if event.get("type") != "assistant/message":
            continue
        content = event.get("data", {}).get("content", [])
        return "".join(block.get("text", "") for block in content if block.get("type") == "text")
    return ""


def finish_reason(events):
    """对应 api.py:318：从后往前找 turn/end，读 reason.kind。"""
    for event in reversed(events):
        if event.get("type") != "turn/end":
            continue
        return event.get("data", {}).get("reason", {}).get("kind")
    return None


# ---------- 以下是 [教学简化] 的 runtime 替身，真实实现见 client.py ----------

class FakeRuntime:
    """对应 HarnessClient：真实实现是 stdio JSON-RPC 子进程，这里同进程模拟。

    仍然按协议形状产出通知：`session.event` 承载会话事件，
    `session.status` 承载 idle 信号。
    """

    def __init__(self, model, tools):
        self.model = model
        self.tools = tools
        self.initialized = None
        self.closed = False

    def initialize(self, profile):
        self.initialized = profile

    def resume_session(self, session_id):
        pass

    def close(self):
        self.closed = True

    def subscribe_session_notifications(self, session_id):
        return _Subscription(self, session_id)

    def _query(self, session_id, user_input):
        self._pending = (session_id, user_input)
        return f"msg-{session_id}-1"      # session/query 的 prompt 回执

    def _produce(self, session_id):
        """[教学简化] Node 侧 agent-loop 的产物，按通知流一次性吐出。"""
        _, user_input = self._pending
        messages = [{"role": "user", "content": user_input}]
        notes = [{"method": "session.event", "payload": {
            "sessionId": session_id,
            "event": {"type": "agent/inbox/spliced",
                      "data": {"inserted": [{"id": f"msg-{session_id}-1"}]}},
        }}]
        while True:
            response = self.model.generate(messages)
            if response.get("tool_call"):
                name = response["tool_call"]["name"]
                args = response["tool_call"].get("args", {})
                result = self.tools[name](**args)
                messages.append({"role": "tool", "name": name, "content": result})
                notes.append({"method": "session.event", "payload": {
                    "sessionId": session_id,
                    "event": {"type": "tool/result", "data": {"name": name, "content": result}},
                }})
                continue
            messages.append({"role": "assistant", "content": response["text"]})
            notes.append({"method": "session.event", "payload": {
                "sessionId": session_id,
                "event": {"type": "assistant/message",
                          "data": {"content": [{"type": "text", "text": response["text"]}]}},
            }})
            break
        notes.append({"method": "session.event", "payload": {
            "sessionId": session_id,
            "event": {"type": "turn/end", "data": {"reason": {"kind": "completed"}}},
        }})
        notes.append({"method": "session.status", "payload": {"sessionId": session_id, "status": "idle"}})
        return notes


class _Subscription:
    """对应 client.py 的 NotificationSubscription：next() 阻塞到下一条通知。"""

    def __init__(self, runtime, session_id):
        self._runtime = runtime
        self._session_id = session_id
        self._queue = []

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        pass

    def next(self):
        if not self._queue:
            self._queue = self._runtime._produce(self._session_id)
        return self._queue.pop(0)
