"""第 18 章演示：一个空服务，两条队列，三份所有权。

仅标准库，Python 3.10+。运行：python3 main.py

八段演示：
1. mount：ctx.otel 就绪，队列=0 连接=0
2. 两个消费者各开一条通道：session-log(queue=1) / product-events(queue=1)
3. 普通事件按条攒批：20 条 → 批次 [8, 8, 4]
4. session 日志按字节攒批：单条超 4,000,000B → rejected（不截断）
5. session 日志一条约 12.4KB 的事件 → 1 条 record，content 是整条 canonical 事件
6. 授权在消费者侧：未授权 feedback=False → skipped；sourceEvent 缺失 → refused
7. 慢通道不拖快通道：session-log 卡住时 product-events 照常发出
8. 关停所有权：deadline 超时 stop_pending() 丢弃排队的 3 条
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
for _p in (_HERE, _CH01):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Context  # noqa: E402
from channels import Sink, measure_record  # noqa: E402
from otel import OTel  # noqa: E402
from backends import (  # noqa: E402
    ProductAnalyticsBackend,
    SessionLogBackend,
    make_record,
)

MAX_REQUEST_BYTES = 4_000_000
BIG_EVENT_BYTES = 5_242_880


class Session:
    """第 3 章 Session 的最小复刻：id、append-only 日志、fork 继承前缀长度。"""

    def __init__(self, session_id, *, inherited_event_count=0):
        self.id = session_id
        self.log = []
        self.inherited_event_count = inherited_event_count

    def append(self, event_type, payload, *, event_time=0):
        event = {
            "session_id": self.id,
            "seq": len(self.log),
            "type": event_type,
            "payload": payload,
            "time": event_time,
        }
        self.log.append(event)
        return event


def build_consumers(ctx, session_sink, product_sink, *,
                    session_processor=None, shutdown_timeout_millis=300):
    """装配两个消费者：各自 inject、各自建通道、各自关停。"""
    session_backend = SessionLogBackend(ctx, {
        "exporter": {"sink": session_sink},
        "processor": session_processor or {"max_export_batch_size": 512},
        "shutdown_timeout_millis": shutdown_timeout_millis,
    })
    product_backend = ProductAnalyticsBackend(ctx, {
        "exporter": {"sink": product_sink},
        "processor": {"max_export_batch_size": 8, "max_queue_size": 256},
        "shutdown_timeout_millis": 5_000,
    })
    return session_backend, product_backend


def feedback_session(session, *, message="你好", rating="up"):
    """一条普通 message + 一条 feedback/record；只有后者授权。"""
    session.append("assistant/message", {"text": message})
    return session.append("feedback/record", {"rating": rating})


def published_event(record):
    """后端交给 reporter 的那条 record 里，event 就是完整的 canonical 事件。"""
    return record["event"]


def main():
    print("[1] mount: ctx.otel 就绪，队列=0 连接=0")
    ctx = Context()
    otel = OTel(ctx)
    print(f"    ctx.otel = {type(otel).__name__}；queues={otel.queues_created} connections={otel.connections_created}")

    print("[2] 两条通道: session-log(queue=1) / product-events(queue=1)")
    session_sink = Sink("session-collector")
    product_sink = Sink("product-collector")
    session_backend, product_backend = build_consumers(ctx, session_sink, product_sink)
    print(f"    session-log(inject={session_backend.inject}) 自持队列 1 条；"
          f"product-events(inject={product_backend.inject}) 自持队列 1 条")
    print(f"    ctx.otel.channels_created={otel.channels_created} queues_created={otel.queues_created} "
          f"connections_created={otel.connections_created}（两条通道互不共享）")

    print("[3] product-events 20 条 → 批次 [8, 8, 4]")
    for i in range(20):
        product_backend.emit("app/turn-started", f"turn {i}", attributes={"index": i})
    product_backend.reporter.force_flush()
    print(f"    批次 {product_backend.reporter.batches}；collector 收到 {product_sink.received} 条")

    print("[4] session-log 一条 5,242,880B 事件 → rejected（> 4,000,000B，不截断）")
    big = Session("session-big")
    base = {"session_id": big.id, "seq": 0, "type": "tool/result", "data": {"blob": ""}}
    filler = "x" * (BIG_EVENT_BYTES - measure_record(base))
    big_event = big.append("tool/result", {"blob": filler})
    before_rejected = session_backend.reporter.rejected
    report = {
        "session_id": big.id,
        "event": {"session_id": big.id, "seq": big_event["seq"],
                  "type": big_event["type"], "data": {"blob": filler}},
    }
    size = measure_record(report)
    session_backend.reporter.report_session_log(report)
    session_backend.reporter.force_flush()
    print(f"    量得 {size:,}B -> accepted={session_backend.reporter.rejected == before_rejected}；"
          f"rejected={session_backend.reporter.rejected}；collector 收到 {session_sink.received} 条")

    print("[5] session-log 一条 12,400B 事件 → 1 条 record（content 为整条 canonical 事件的 JSON 串）")
    session = Session("session-0001")
    session.append("assistant/message", {"text": "y" * 12_306})
    feedback_session(session)
    for event in session.log:
        ctx.emit("session/event", session, event)
    session_backend.reporter.force_flush()
    published = published_event(session_sink.records[0])
    content = json.dumps(published, ensure_ascii=False)
    record_bytes = measure_record(session_sink.records[0])
    print(f"    enqueued={session_backend.enqueued}（feedback 授权后重放整段前缀，每条事件一条 record）")
    print(f"    第一条 record：eventName='session-log'；event.type={published['type']!r}；"
          f"整条请求 {record_bytes:,}B，content 是 {len(content.encode('utf-8')):,}B 的 canonical 事件 JSON")
    print(f"    末条 record 的 seq={published_event(session_sink.records[-1])['seq']}；"
          f"collector 收到 {len(session_sink.records)} 条")

    print("[6] 未授权 feedback=False → skipped；sourceEvent 缺失 → refused")
    before_skipped = session_backend.skipped_unauthorized
    plain = session.append("assistant/message", {"text": "没有 feedback，不授权"})
    ctx.emit("session/event", session, plain)
    print(f"    skipped_unauthorized +{session_backend.skipped_unauthorized - before_skipped}"
          f"（累计 {session_backend.skipped_unauthorized}）；collector 仍收到 {len(session_sink.records)} 条")
    naked = make_record(session.id, plain, source=False)
    print(f"    deliver(脱敏删掉 sourceEvent) -> {session_backend.deliver(naked)}；"
          f"refused_no_source={session_backend.refused_no_source}")

    print("[7] 慢通道不拖快通道：session-log 卡在请求里，product-events 照常发出")
    block_gate = threading.Event()
    slow_ctx = Context()
    OTel(slow_ctx)
    slow_sink = Sink("slow-session-collector", block=block_gate)
    slow_backend, _ = build_consumers(slow_ctx, slow_sink, Sink("unused"))
    slow_session = Session("session-slow")
    first = feedback_session(slow_session)   # 整个会话被一起重放，drain 卡在慢 sink 里
    slow_ctx.emit("session/event", slow_session, first)
    before = product_sink.received            # 第 3 段累计 20 条
    for i in range(5):
        product_backend.emit("app/ping", f"session-blocked-{i}")
    product_backend.reporter.force_flush()   # 慢 session 通道此刻正卡在请求里
    after = product_sink.received
    slow_backend.shutdown()                  # 同一时刻在慢通道上等 deadline
    block_gate.set()                         # 释放慢 collector；在途请求自然结束
    time.sleep(0.2)
    product_backend.shutdown()
    print(f"    慢通道卡在单次请求里：block_hits={slow_sink.block_hits}，"
          f"deadline {slow_backend.shutdown_timeout_millis}ms 内未排空")
    print(f"    阻塞期间产品通道 {before} -> {after}（+{after - before} 条），flush 正常")
    print(f"    session-log status={slow_backend.shutdown_status}；"
          f"释放后 collector 收到 {slow_sink.received} 条（队列里已经没有剩余）")

    print("[8] 关停 deadline：stop_pending() 丢弃排队的 3 条，在途请求仍可自然结束")
    drop_gate = threading.Event()
    drop_ctx = Context()
    OTel(drop_ctx)
    drop_sink = Sink("stuck-collector", block=drop_gate)
    drop_backend, _ = build_consumers(drop_ctx, drop_sink, Sink("unused"),
                                      shutdown_timeout_millis=120)
    drop_session = Session("session-drop")
    first = feedback_session(drop_session)
    drop_ctx.emit("session/event", drop_session, first)                  # 重放 2 条进队列
    # 真实版由 scheduledDelayMillis 定时器触发排空；教学版手动起一次，让请求卡在慢 sink 里
    threading.Thread(target=drop_backend.reporter.force_flush, daemon=True).start()
    time.sleep(0.05)                                                     # 等排空取走那 2 条
    for i in range(2):
        drop_session.append("assistant/message", {"text": f"queued {i}"})
    last = drop_session.append("feedback/record", {"rating": "up"})      # 再重放 3 条，全部排队
    drop_ctx.emit("session/event", drop_session, last)
    drop_backend.shutdown()                  # 排空被在途请求占着 → deadline 到期
    dropped = drop_backend.shutdown_dropped
    drop_gate.set()                          # 释放后，在途那 2 条自然送达
    time.sleep(0.2)
    print(f"    status={drop_backend.shutdown_status} dropped={dropped}；"
          f"在途的 2 条最终送达（collector 收到 {drop_sink.received} 条）")

if __name__ == "__main__":
    main()
