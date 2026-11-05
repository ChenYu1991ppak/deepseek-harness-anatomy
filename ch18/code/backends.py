"""第 18 章教学代码：两个消费者适配器（授权、同一性、关停 deadline 都在这里）。

仅标准库，Python 3.10+。

源码对应：
- SessionLogBackend / isFeedback ↔ packages/session/session-telemetry-otel/src/index.ts:43
- 默认 FEEDBACK_ONLY            ↔ packages/session/session-telemetry-otel/src/index.ts:31-37
- inject=['sessions','otel']    ↔ packages/session/session-telemetry-otel/src/index.ts:145
- createSessionLogReporter      ↔ packages/session/session-telemetry-otel/src/index.ts:190
- sourceEvent 缺失即拒          ↔ packages/session/session-telemetry-otel/src/index.ts:204-207
- eventAt(seq) 同一性           ↔ packages/session/session-telemetry-otel/src/index.ts:227
- shutdown(signal) + deadline   ↔ packages/session/session-telemetry-otel/src/index.ts:259-275
- ProductAnalyticsBackend       ↔ packages/host/product-telemetry-otel/src/index.ts:61-110

[教学简化] 真实版用 `Session.append` 校验过的 canonical 事件对象与 `SessionEvent`
类型；教学版用 dict 复刻事件信封（session_id/seq/type/payload），不 import 第 3 章
代码，避免把持久化依赖带进这一章。
[教学简化] 真实版的 `ctx.effect(() => async () => ...)` 支持异步清理；教学版的
cordis.py 只收集同步清理函数，因此关停用一个后台线程 + `join(timeout)` 模拟
「Promise.race(providerShutdown, deadline)」。
"""
from __future__ import annotations

import json
import os
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
for _p in (_HERE, _CH01):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from channels import measure_record  # noqa: E402

__all__ = [
    "SessionLogBackend",
    "ProductAnalyticsBackend",
    "is_feedback",
    "event_at",
    "FEEDBACK_ONLY",
    "DISABLED",
    "envelope_of",
    "make_record",
]

FEEDBACK_ONLY = "FEEDBACK_ONLY"
DISABLED = "DISABLED"
DEFAULT_TELEMETRY_MODE = FEEDBACK_ONLY

DEFAULT_SHUTDOWN_TIMEOUT_MILLIS = 3_000


# ---------------------------------------------------------------- 事件模型


def envelope_of(event: dict) -> dict:
    """去掉 payload 的事件信封：{session_id, seq, type}。"""
    return {"session_id": event["session_id"], "seq": event["seq"], "type": event["type"]}


def make_record(session_id: str, event: dict, *, body=None, source=True) -> dict:
    """构造一条交给后端 sink 的记录。

    `source=False` 模拟脱敏规则把 `sourceEvent` 删掉（第 3 章 §7.2 的 fail-closed 路径）。
    真实版字段名是 `sourceEvent`；教学版用 `source_event`。
    """
    record = {
        "source_event": (
            {"session_id": session_id, "envelope": envelope_of(event)} if source else None
        ),
        "channel": "ledger",
        "time": event.get("time", 0),
        "severity": "info",
        "attributes": {"session.id": session_id, "event.seq": event["seq"], "event.type": event["type"]},
        "body": json.loads(json.dumps(event["payload"] if body is None else body)),
    }
    return record


def is_feedback(session, event: dict) -> bool:
    """只有本会话新鲜提交的 feedback 事件授权重放；fork 继承来的前缀一律否决。

    对应 packages/session/session-telemetry-otel/src/index.ts:43-51。
    """
    if event["seq"] < session.inherited_event_count:
        return False
    if event["type"] == "feedback/record":
        return True
    if event["type"] in ("feedback/message-put", "feedback/message-delete"):
        return getattr(session, "id", None) == event["session_id"]
    return False


def event_at(session, seq: int):
    """取规范日志里 seq 位置上的那个对象；教学版直接按列表下标。"""
    if 0 <= seq < len(session.log):
        return session.log[seq]
    return None


# ---------------------------------------------------------------- session 后端


class SessionLogBackend:
    """session 日志消费者：授权、同一性、脱敏后拒发、带 deadline 的关停。

    真实类名 `OpenTelemetrySessionBackend`，`static inject = ['sessions', 'otel']`。
    """

    inject = ["sessions", "otel"]

    def __init__(self, ctx, config=None):
        config = config or {}
        self.ctx = ctx
        self.mode = config.get("mode", DEFAULT_TELEMETRY_MODE)
        self.shutdown_timeout_millis = config.get(
            "shutdown_timeout_millis", DEFAULT_SHUTDOWN_TIMEOUT_MILLIS
        )
        self.skipped_unauthorized = 0
        self.refused_no_source = 0
        self.non_canonical = 0
        self.enqueued = 0
        self.reporter = None
        self.shutdown_status = "not-started"
        self.shutdown_dropped = 0
        self.shutdown_seconds = 0.0

        if self.mode == DISABLED:
            # DISABLED 不建 transport，只在本地 warn（index.ts:156-163）
            ctx.on("session/event", self._on_event_disabled)
            return

        self.reporter = ctx.otel.create_session_log_reporter({
            "scope": {"name": "@demo/session-telemetry-otel", "version": "0.18.0"},
            "exporter": config["exporter"],
            "resource_attributes": {
                "service.name": config.get("service_name", "demo"),
                "service.version": config.get("service_version", "0.18.0"),
                "user.id": config.get("user_id", "anon-0001"),
            },
            "processor": config.get("processor", {}),
            "max_request_bytes": config.get("max_request_bytes", 4_000_000),
            "on_failure": self._warn,
        })
        ctx.on("session/event", self._on_event)
        # 真实版在消费者自己的 fiber disposal 里关通道；教学版同样挂在 effect 上
        ctx.effect(self._disposal)

    # ---- 授权侧 ----

    def _warn(self, message, error=None):
        print(f"    [warn] {message}" + (f": {error}" if error else ""))

    def _on_event(self, session, event):
        if not is_feedback(session, event):
            self.skipped_unauthorized += 1
            return
        # 只有规范日志上的那一条能授权这一前缀；重放的副本不授权
        if event_at(session, event["seq"]) is not event:
            self.non_canonical += 1
            self._warn("session telemetry ignored an event absent from the canonical session log")
            return
        self.capture_session(session, event["seq"])

    def _on_event_disabled(self, session, event):
        if is_feedback(session, event):
            self._warn("OpenTelemetry session upload is DISABLED; this feedback is not uploaded")

    def capture_session(self, session, through_seq=None):
        """把 handoff cursor 之后的规范事件交给 sink（coordinator.captureSession 的教学版）。"""
        cursor = getattr(self, "_cursor", -1)
        for event in session.log[cursor + 1:]:
            if through_seq is not None and event["seq"] > through_seq:
                break
            self.deliver(make_record(session.id, event))
            self._cursor = event["seq"]

    def deliver(self, record):
        """sink：sourceEvent 缺失的记录直接拒发（index.ts:204-207）。"""
        if record.get("source_event") is None:
            self.refused_no_source += 1
            self._warn("Session log record withheld: redaction removed sourceEvent")
            return False
        source = record["source_event"]
        self.reporter.report_session_log({
            "session_id": source["session_id"],
            "event": {**source["envelope"], "data": record["body"]},
            "severity": record["severity"],
            "attributes": record["attributes"],
        })
        self.enqueued += 1
        return True

    # ---- 关停所有权 ----

    def shutdown(self):
        """带外层 deadline 的关停；超时 stopPending() 丢弃剩余队列。

        对应 index.ts:259-275 的 Promise.race(providerShutdown, deadline)。
        教学版把「排空」放后台线程，主线程只等 deadline——这正是
        Promise.race 的语义；超时后队列丢弃，在途请求仍可能自然结束。
        """
        if self.reporter is None:
            self.shutdown_status = "disabled"
            return
        deadline_seconds = self.shutdown_timeout_millis / 1000

        def run():
            self.reporter.shutdown()
            self._flush_finished = True

        self._flush_finished = False
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(deadline_seconds)
        if not self._flush_finished:
            self.shutdown_dropped = self.reporter.stop_pending()
            self.shutdown_status = "deadline-exceeded"
        else:
            self.shutdown_status = "flushed"

    def _disposal(self):
        """effect 清理：卸载时由容器调用。"""
        self.shutdown()
        return None


# ---------------------------------------------------------------- product 后端


class ProductAnalyticsBackend:
    """普通产品事件消费者：inject=['otel']，只做字段选择与 deadline。

    真实类名 `ProductTelemetry`，`static inject = ['otel']`（product-telemetry-otel/src/index.ts:62）。
    """

    inject = ["otel"]

    def __init__(self, ctx, config=None):
        config = config or {}
        self.ctx = ctx
        self.shutdown_timeout_millis = config.get("shutdown_timeout_millis", 21_000)
        self.reporter = ctx.otel.create_event_reporter({
            "exporter": config["exporter"],
            "resource_attributes": {
                "service.name": config.get("service_name", "demo"),
                "service.version": config.get("service_version", "0.18.0"),
            },
            "scope": {"name": "@demo/host-product-telemetry-otel"},
            "processor": config.get("processor", {}),
            "on_failure": lambda message, error=None: print(
                f"    [warn] {message}" + (f": {error}" if error else "")
            ),
        })
        ctx.effect(self._disposal)

    def emit(self, event_name: str, body: str, *, attributes=None):
        """调用方显式选择字段后提交一条事件；不读会话、账号或设备身份。"""
        self.reporter.emit({
            "event_name": event_name,
            "body": body,
            "timestamp": 0,
            "attributes": attributes or {},
        })

    def shutdown(self):
        return self.reporter.shutdown(timeout=self.shutdown_timeout_millis / 1000)

    def _disposal(self):
        self.shutdown()
        return None


# ---------------------------------------------------------------- 自测


def _selftest():
    """`python3 backends.py`：授权判据、同一性、缺 sourceEvent 拒发。"""
    from cordis import Context
    from channels import Sink
    from otel import OTel

    class _Session:
        def __init__(self, sid, inherited=0):
            self.id = sid
            self.inherited_event_count = inherited
            self.log = []

        def append(self, event_type):
            event = {"session_id": self.id, "seq": len(self.log), "type": event_type,
                     "payload": {}, "time": 0}
            self.log.append(event)
            return event

    ctx = Context()
    OTel(ctx)
    backend = SessionLogBackend(ctx, {"exporter": {"sink": Sink("selftest")}})
    session = _Session("s1")
    plain = session.append("assistant/message")
    feedback = session.append("feedback/record")
    assert not is_feedback(session, plain) and is_feedback(session, feedback)
    print("  isFeedback：普通事件 False，feedback/record True")

    forked = _Session("s2", inherited=2)
    inherited_feedback = forked.append("feedback/record")
    inherited_feedback["seq"] = 0
    assert not is_feedback(forked, inherited_feedback)
    print("  isFeedback：fork 继承前缀（seq < inheritedEventCount）一律否决")

    import copy
    clone = copy.deepcopy(feedback)
    assert event_at(session, feedback["seq"]) is feedback
    assert event_at(session, clone["seq"]) is not clone
    print("  eventAt(seq)：只有规范日志上的同一个对象通过同一性校验")

    assert backend.deliver(make_record("s1", feedback, source=False)) is False
    assert backend.refused_no_source == 1
    print("  deliver：sourceEvent 缺失 -> refused，不上传")


if __name__ == "__main__":
    print("backends.py 自测：")
    _selftest()
