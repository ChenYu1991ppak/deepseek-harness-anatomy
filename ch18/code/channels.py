"""第 18 章教学代码：两种排队哲学 + 内存 Sink。

仅标准库，Python 3.10+。

源码对应：
- CountBatcher       ↔ packages/telemetry/otel/src/event-log.ts:52（SDK BatchLogRecordProcessor 按条攒批）
- ByteGateProcessor  ↔ packages/telemetry/otel/src/session-log.ts:72（自写字节/条数双闸）
- 单条超限直接拒     ↔ packages/telemetry/otel/src/session-log.ts:102-105
- 串行单飞 + 发完 forceFlush ↔ packages/telemetry/otel/src/session-log.ts:126-145
- stopPending 清队列 ↔ packages/telemetry/otel/src/session-log.ts:169-175

[教学简化] 真实版是事件循环里的异步排队：`onEmit` 只入队，由 `setTimeout`
（scheduledDelayMillis）或条数/字节阈值触发 `forceFlush()`，Promise 结束才释放
「单飞」槽位。教学版在 `on_emit` 里同步完成「阈值触发 → 排空」，用一把可重入
锁演示同一个不变量：一次只有一个请求在飞。
[教学简化] 真实版用 OTLP JSON 序列化器量整条请求（含 resource/scope 信封）的
UTF-8 字节；教学版用 `json.dumps` 的 UTF-8 长度代替，量级与语义一致。
"""
from __future__ import annotations

import json
import threading

__all__ = [
    "Sink",
    "SinkFailure",
    "measure_record",
    "CountBatcher",
    "ByteGateProcessor",
]


class SinkFailure(RuntimeError):
    """Sink 模拟一次导出失败（真实版是 HTTP 非 2xx 或网络错误）。"""


def measure_record(record) -> int:
    """量一条 record 序列化后的 UTF-8 字节数。

    真实版：`JsonLogsSerializer.serializeRequest([record])` 的长度，
    包含 resource/scope 信封（session-log.ts:95-97）。
    """
    return len(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class Sink:
    """内存收集器：替代 OTLP HTTP 端点，可注入失败与阻塞。

    `block` 是一个 `threading.Event`，置位前 `send` 一直等——用来模拟
    一条慢通道（远端 collector 不响应）。Sink 只负责「运输」，排队策略在
    两个 processor 里。`block_hits` 记录请求卡住过几次，供演示断言。
    """

    def __init__(self, name: str, *, fail_times: int = 0, block: threading.Event | None = None):
        self.name = name
        self.records: list[dict] = []
        self.batches: list[list[dict]] = []
        self.received = 0
        self.requests = 0
        self.force_flushes = 0
        self.block_hits = 0
        self._fail_remaining = fail_times
        self._block = block
        self._lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0

    def send(self, batch: list[dict]) -> None:
        """模拟一次 HTTP 请求；慢 sink 会在这里阻塞，直到外部释放。"""
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self._block is not None:
                with self._lock:
                    self.block_hits += 1
                self._block.wait()
            with self._lock:
                if self._fail_remaining > 0:
                    self._fail_remaining -= 1
                    raise SinkFailure(f"{self.name}: simulated HTTP 503")
                self.records.extend(batch)
                self.batches.append(list(batch))
                self.received += len(batch)
                self.requests += 1
        finally:
            with self._lock:
                self.in_flight -= 1

    def force_flush(self) -> None:
        """模拟 SDK 并发导出队列清空（session-log.ts:142 每个请求之后都等它）。"""
        with self._lock:
            self.force_flushes += 1


class CountBatcher:
    """按条数攒批：普通产品事件的排队哲学。

    对应 SDK `BatchLogRecordProcessor`。队列满时丢弃新记录；攒够
    `max_export_batch_size` 条立刻发一批，`flush` 发剩余。
    """

    def __init__(self, sink: Sink, *, max_export_batch_size: int, max_queue_size: int):
        self.sink = sink
        self.max_export_batch_size = max_export_batch_size
        self.max_queue_size = max_queue_size
        self.queue: list[dict] = []
        self.batches: list[int] = []
        self.dropped = 0
        self._lock = threading.Lock()

    def emit(self, record: dict) -> None:
        with self._lock:
            if len(self.queue) >= self.max_queue_size:
                self.dropped += 1
                return
            self.queue.append(record)
            should_send = len(self.queue) >= self.max_export_batch_size
        if should_send:
            self.flush()

    def flush(self) -> None:
        while True:
            with self._lock:
                if not self.queue:
                    return
                batch = self.queue[: self.max_export_batch_size]
                del self.queue[: len(batch)]
            self.batches.append(len(batch))
            try:
                self.sink.send(batch)
            except SinkFailure:
                # 真实 SDK 由重试策略接手；教学版只记录批大小，不回填重排
                pass

    def shutdown(self) -> None:
        self.flush()

    def stop_pending(self) -> int:
        with self._lock:
            dropped = len(self.queue)
            self.queue.clear()
            return dropped


class ByteGateProcessor:
    """按字节攒批 + 单条拒发 + 串行单飞：session 日志的排队哲学。

    对应 packages/telemetry/otel/src/session-log.ts:72 的 SessionLogProcessor。
    三个不变量：
    1. 一条 record 先序列化、再量字节；超过上限直接拒，绝不截断。
    2. 每次请求按「已量好的字节」装箱，装不下就留给下一请求。
    3. 同一时刻只有一个请求在飞；阈值到了只启动一个后台排空（对应
       真实版的 `void this.forceFlush()`），在途期间新到的记录只排队。
    """

    def __init__(
        self,
        sink: Sink,
        *,
        limit: int = 4_000_000,
        max_export_batch_size: int = 512,
        max_queue_size: int = 2048,
        on_failure=None,
    ):
        self.sink = sink
        self.limit = limit
        self.max_export_batch_size = max_export_batch_size
        self.max_queue_size = max_queue_size
        self.on_failure = on_failure or (lambda message, error=None: None)
        self.queue: list[tuple[dict, int]] = []
        self.bytes = 0
        self.rejected = 0
        self.batches: list[int] = []
        self.last_bytes: int | None = None
        self._cond = threading.Condition()
        self._active = False
        self._stopped = False

    # ---- 入队 ----

    def on_emit(self, record: dict) -> bool:
        """量字节、判上限、入队。返回是否被接受（真实版返回 void，拒发只 warn）。"""
        with self._cond:
            if self._stopped:
                # 关停后不再接受新记录（真实版 `if (this.stopped) return`）
                return False
            if len(self.queue) >= self.max_queue_size:
                self.on_failure("Session log queue is full; record rejected")
                self.rejected += 1
                return False
            try:
                size = measure_record(record)
            except (TypeError, ValueError) as error:
                self.on_failure("Session log serialization failed; record rejected", error)
                self.rejected += 1
                return False
            self.last_bytes = size
            if size > self.limit:
                # 一条完整事件不可拆：超限直接拒，绝不截断（session-log.ts:102-105）
                self.on_failure(
                    "Session log record rejected; content was not truncated",
                    ValueError(f"Session log record exceeds maxRequestBytes: {size} > {self.limit}"),
                )
                self.rejected += 1
                return False
            self.queue.append((record, size))
            self.bytes += size
            if self._active:
                # 已有请求在飞：新记录只排队（session-log.ts:108）
                return True
            if len(self.queue) >= self.max_export_batch_size or self.bytes >= self.limit:
                threading.Thread(target=self._drain, daemon=True).start()
            return True

    # ---- 排空 ----

    def force_flush(self) -> bool:
        """排空队列；已有排空在跑时等它结束（session-log.ts:117-124 的单飞语义）。"""
        with self._cond:
            if self._active:
                self._cond.wait_for(lambda: not self._active)
                return True
        self._drain()
        return True

    def _drain(self) -> None:
        with self._cond:
            if self._active or not self.queue:
                return
            self._active = True
        try:
            while True:
                with self._cond:
                    if not self.queue:
                        return
                    pack: list[dict] = []
                    packed_bytes = 0
                    for entry in self.queue:
                        if len(pack) == self.max_export_batch_size or packed_bytes + entry[1] > self.limit:
                            break
                        packed_bytes += entry[1]
                        pack.append(entry[0])
                    del self.queue[: len(pack)]
                    self.bytes = max(0, self.bytes - packed_bytes)
                    self.batches.append(len(pack))
                try:
                    self.sink.send(pack)
                except SinkFailure as error:
                    self.on_failure("Session log export failed", error)
                # 回调先于 SDK 并发队列清理；每请求之后都等一次 forceFlush
                self.sink.force_flush()
        finally:
            with self._cond:
                self._active = False
                self._cond.notify_all()

    def shutdown(self) -> None:
        self.force_flush()
        self.sink.force_flush()

    def stop_pending(self) -> int:
        """deadline 之后丢弃剩余队列，返回丢弃条数（session-log.ts:169-175）。

        真实版在单线程事件循环里执行，drain 的 `await` 一挂起，`stopPending()`
        就能插进来；它不拿锁。教学版照抄这一点：**不取 `_cond`**，否则会在
        drain 卡在慢 sink 时把自己饿死。在途请求不在队列里，仍可自然结束。
        """
        self._stopped = True
        dropped = len(self.queue)
        self.queue.clear()
        self.bytes = 0
        return dropped


# ---------------------------------------------------------------- 自测


def _selftest():
    """`python3 channels.py`：两个排队器的核心不变量。"""
    import threading
    import time

    gate = threading.Event()
    sink = Sink("selftest-instant")
    batcher = CountBatcher(sink, max_export_batch_size=3, max_queue_size=10)
    for i in range(7):
        batcher.emit({"i": i})
    batcher.flush()
    assert batcher.batches == [3, 3, 1], batcher.batches
    print(f"  CountBatcher：7 条 -> 批次 {batcher.batches}")

    slow = Sink("selftest-slow", block=gate)
    proc = ByteGateProcessor(slow, limit=4_000, max_export_batch_size=8)
    big = {"blob": "x" * 5_000}
    assert proc.on_emit(big) is False and proc.rejected == 1
    print(f"  ByteGateProcessor：5,000B 单条 -> rejected={proc.rejected}（不截断）")

    small = [{"n": i} for i in range(5)]
    for record in small:
        proc.on_emit(record)
    # 阈值没到（5 条 < 8，字节 << 4000），先手动起一次排空并把它卡住
    worker = threading.Thread(target=proc.force_flush)
    worker.start()
    time.sleep(0.05)
    assert len(proc.queue) == 0, "排空应已取走整批"
    gate.set()
    worker.join()
    assert slow.received == 5, slow.received
    print(f"  ByteGateProcessor：5 条小记录 -> 1 个请求，received={slow.received}；"
          f"单飞 max_in_flight={slow.max_in_flight}")
    assert slow.max_in_flight == 1

    sink2 = Sink("selftest-drop")
    proc2 = ByteGateProcessor(sink2)
    for i in range(3):
        proc2.on_emit({"n": i})
    dropped = proc2.stop_pending()
    assert dropped == 3 and not proc2.queue
    print(f"  stop_pending：dropped={dropped}，队列清空")


if __name__ == "__main__":
    print("channels.py 自测：")
    _selftest()
