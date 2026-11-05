"""第 18 章教学代码：空的 OTel 服务 + 两个通道工厂。

仅标准库，Python 3.10+。复用 ch01/code/cordis.py 的 Service（构造即注册）。

源码对应：
- OTel                     ↔ packages/telemetry/otel/src/index.ts:13
- createEventReporter      ↔ packages/telemetry/otel/src/index.ts:22
- createSessionLogReporter ↔ packages/telemetry/otel/src/index.ts:30
- "Mounting creates no queue, identity, or network connection"
                           ↔ packages/telemetry/otel/src/index.ts:12

[教学简化] 真实版每个工厂方法返回一个持有 OTel SDK LoggerProvider 的通道对象；
教学版返回的通道只持有内存 Sink 与队列，不建连接、不装 SDK。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
for _p in (_HERE, _CH01):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Service  # noqa: E402
from channels import ByteGateProcessor, CountBatcher  # noqa: E402

__all__ = [
    "OTel",
    "Reporter",
    "EventLogReporter",
    "SessionLogReporter",
]


class Reporter:
    """通道的公共面：发送、排空、关停。

    真实版没有这个公共基类——两条通道返回的是不同类
    （`EventLogReporter` / `SessionLogReporter`），只有 `shutdown` 名字相同。
    教学版抽出来是为了让 main.py 用一个变量演示「通道归消费者所有」。
    """

    def shutdown(self, timeout: float | None = None):
        raise NotImplementedError

    def stop_pending(self) -> int:
        raise NotImplementedError


class EventLogReporter(Reporter):
    """普通产品事件通道：按条数攒批。

    对应 packages/telemetry/otel/src/event-log.ts:40 的 EventLogReporter。
    """

    def __init__(self, options):
        self.scope = options["scope"]
        self.resource_attributes = options["resource_attributes"]
        self.sink = options["exporter"]["sink"]
        processor = options.get("processor", {})
        self.batcher = CountBatcher(
            self.sink,
            max_export_batch_size=processor.get("max_export_batch_size", 512),
            max_queue_size=processor.get("max_queue_size", 2048),
        )
        self.on_failure = options.get("on_failure", lambda message, error=None: None)
        # 与 event-transport.ts:32 的 AbortSignal.any 对应：abort 同时掐断在途请求与重试等待
        self.cancellation = options["exporter"].get("cancellation")

    def emit(self, record: dict) -> None:
        """入队一条普通事件，不承诺送达（event-log.ts:73）。"""
        self.batcher.emit(record)

    @property
    def received(self) -> int:
        return self.sink.received

    @property
    def batches(self) -> list[int]:
        return self.batcher.batches

    def force_flush(self) -> None:
        self.batcher.flush()

    def shutdown(self, timeout: float | None = None):
        """排空队列；signal 已 abort 时先取消在途导出（event-log.ts:83-93）。"""
        if self.cancellation is not None and self.cancellation.is_set():
            self.on_failure("Product telemetry export cancelled by shutdown signal")
            return 0
        self.batcher.shutdown()
        return 0

    def stop_pending(self) -> int:
        return self.batcher.stop_pending()


class SessionLogReporter(Reporter):
    """session 日志通道：按字节攒批，一条完整事件一条 record。

    对应 packages/telemetry/otel/src/session-log.ts:184 的 SessionLogReporter；
    `reportSessionLog` 见 :210。
    """

    def __init__(self, options):
        self.scope = options["scope"]
        self.resource_attributes = options["resource_attributes"]
        self.sink = options["exporter"]["sink"]
        self.max_request_bytes = options.get("max_request_bytes", 4_000_000)
        processor = options.get("processor", {})
        self.processor = ByteGateProcessor(
            self.sink,
            limit=self.max_request_bytes,
            max_export_batch_size=processor.get("max_export_batch_size", 512),
            max_queue_size=processor.get("max_queue_size", 2048),
            on_failure=options.get("on_failure", lambda message, error=None: None),
        )

    def report_session_log(self, record: dict) -> None:
        """入队一条完整事件；content 是整条 canonical 事件的 JSON 串（session-log.ts:210-219）。"""
        self.processor.on_emit(record)

    @property
    def received(self) -> int:
        return self.sink.received

    @property
    def rejected(self) -> int:
        return self.processor.rejected

    @property
    def batches(self) -> list[int]:
        return self.processor.batches

    def force_flush(self) -> None:
        self.processor.force_flush()

    def shutdown(self, timeout: float | None = None):
        self.processor.shutdown()
        return 0

    def stop_pending(self) -> int:
        return self.processor.stop_pending()


class OTel(Service):
    """共享传输服务：mount 只注册服务名 `otel`，不建队列、身份或连接。"""

    def __init__(self, ctx):
        super().__init__(ctx, "otel")
        # 三个计数器是可观测证据：mount 之后全为 0，创建通道时才增长。
        # connections_created 计的是「通道拿到了一个出口」，不是真实网络连接
        # （真实版 mount 与建通道都不建连接，连接在 exporter 发请求时才建立）。
        self.queues_created = 0
        self.connections_created = 0
        self.channels_created = 0

    def create_event_reporter(self, options) -> EventLogReporter:
        """开一条独享队列的普通事件通道；端点/scope/队列参数全由调用方传入。"""
        self.channels_created += 1
        self.queues_created += 1
        if options.get("exporter", {}).get("sink") is not None:
            self.connections_created += 1  # 教学版把「拿到一个 Sink」视为一次出口登记
        return EventLogReporter(options)

    def create_session_log_reporter(self, options) -> SessionLogReporter:
        """开一条独享队列的 session 日志通道；授权在调用方。"""
        self.channels_created += 1
        self.queues_created += 1
        if options.get("exporter", {}).get("sink") is not None:
            self.connections_created += 1
        return SessionLogReporter(options)


# ---------------------------------------------------------------- 自测


def _selftest():
    """`python3 otel.py`：mount 不建任何东西，工厂才建通道。"""
    from cordis import Context
    from channels import Sink

    ctx = Context()
    service = OTel(ctx)
    assert (service.queues_created, service.connections_created, service.channels_created) == (0, 0, 0)
    assert ctx.otel is service
    print("  mount：ctx.otel 就绪，queues=0 connections=0 channels=0")

    events = service.create_event_reporter({
        "exporter": {"sink": Sink("events")},
        "processor": {"max_export_batch_size": 2},
        "scope": {"name": "test"},
        "resource_attributes": {},
    })
    logs = service.create_session_log_reporter({
        "exporter": {"sink": Sink("logs")},
        "scope": {"name": "test"},
        "resource_attributes": {},
    })
    assert events.batcher is not logs.processor
    assert events.sink is not logs.sink
    assert service.channels_created == 2 and service.queues_created == 2
    assert service.connections_created == 2
    print("  两个工厂：channels=2 queues=2 connections=2，两个通道的队列与 sink 互不共享")


if __name__ == "__main__":
    print("otel.py 自测：")
    _selftest()
