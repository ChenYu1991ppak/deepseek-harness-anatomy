"""第 18 章反例：一个全局 provider + 一条共享队列 + 没有 owner 的关停。

仅标准库，Python 3.10+。运行：python3 bad_example.py

四个坑：
1. 大事件挤掉产品分析事件：一条队列、一套按条攒批，session 日志把队列塞满。
2. 没人负责 drain：通道建在模块级单例里，进程退出时没有任何人持有它。
3. 关闭时丢数据：关停函数只是把队列丢掉，排队中的事件全部消失。
4. 没有授权闸：任何拿到共享 provider 的代码都能把整段会话历史（含敏感字段）
   直接发出去，捕获侧「该不该发」的判断在这里不存在。

[教学简化] 真实事故里这四个坑往往同时出现；教学版把它们拆成四段，各自
打印可观测的计数。前三个坑跑在同一个 `GlobalLoggerProvider` 上；第 4 个坑
新起一个同类 provider，免得前面队列已满的计数干扰。
"""
from __future__ import annotations

import queue


class GlobalLoggerProvider:
    """全局单例：一条队列、一个按条攒批的 exporter。

    这就是「把 OTel 直接塞进插件内部」的写法：端点、scope、队列都在单例里
    硬编码，任何两个消费者都只能排队共用。
    """

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.queue: queue.Queue = queue.Queue(maxsize=8)
        self.dropped = 0
        self.exported = 0
        self.discarded = 0
        self.owner = None

    def emit(self, kind: str, payload: dict) -> bool:
        try:
            self.queue.put_nowait({"kind": kind, "payload": payload})
            return True
        except queue.Full:
            # 坑 1：队列满时静默丢新记录，谁丢的不记名
            self.dropped += 1
            return False

    def drain(self, batch_size: int = 4) -> int:
        sent = 0
        while not self.queue.empty():
            batch = [self.queue.get_nowait() for _ in range(min(batch_size, self.queue.qsize()))]
            self.exported += len(batch)
            sent += len(batch)
        return sent

    def shutdown(self) -> None:
        # 坑 3：关停只把引用置空，排队中的记录被整批丢掉，且没有回执
        self.discarded = self.queue.qsize()
        while not self.queue.empty():
            self.queue.get_nowait()
        self.owner = None


PROVIDER = GlobalLoggerProvider("https://collector.example.com/v1/logs")


def session_capture(event):
    """session 日志消费者：一条完整事件一条，体量可能很大。"""
    PROVIDER.emit("session-log", event)


def upload_session_history(provider, session_events, credential):
    """坑 4：共享 provider 只要求「调用方想发」，不问「这条该不该发」。

    真实系统里这里缺少的正是 `isFeedback`、`same event`、`sourceEvent` 三道判据。
    """
    uploaded = 0
    sensitive = 0
    for event in session_events:
        if provider.emit("session-log", event):
            uploaded += 1
            if "secret" in event:
                sensitive += 1
    provider.drain()
    return uploaded, sensitive


def product_analytics(name):
    """产品分析消费者：小而频繁。"""
    return PROVIDER.emit("product-event", {"name": name})


def main():
    print(f"[坑 1] 一条共享队列（maxsize={PROVIDER.queue.maxsize}）：session 大事件挤掉产品分析事件")
    for i in range(6):
        session_capture({"seq": i, "bytes": 240_000})
    accepted = sum(1 for i in range(10) if product_analytics(f"app/turn-{i}"))
    print(f"    6 条 session-log 先占位；产品事件只挤进 {accepted}/10，其余 dropped={PROVIDER.dropped}")
    print("    两条通道共用一套按条攒批：谁先来谁占坑，体量差异在这里不起任何作用")

    print("[坑 2] 没人负责 drain：单例建在模块级，卸载时不归任何 fiber")
    print(f"    exported={PROVIDER.exported}，队列里压着 {PROVIDER.queue.qsize()} 条")
    print("    没有 owner 就没有 effect 清理：容器 dispose() 不会碰这个队列，")
    print("    也没有任何代码承诺「什么时候排空」")

    print("[坑 3] 关闭时丢数据：shutdown 只是把队列丢掉")
    pending = PROVIDER.queue.qsize()
    PROVIDER.shutdown()
    print(f"    shutdown 前排队 {pending} 条；shutdown 丢弃 {PROVIDER.discarded} 条，"
          f"exported 仍是 {PROVIDER.exported}")
    print(f"    合计丢失 dropped={PROVIDER.dropped} + discarded={PROVIDER.discarded}，"
          "调用方既拿不到回执，也不知道丢的是哪一条")

    print("[坑 4] 没有授权闸：共享 provider 把整段会话历史直接发走（含 secret）")
    second = GlobalLoggerProvider("https://collector.example.com/v1/logs")
    history = [
        {"seq": 0, "type": "user/message", "text": "帮我看看这个配置"},
        {"seq": 1, "type": "assistant/message", "text": "好的"},
        {"seq": 2, "type": "tool/result", "text": "token=...", "secret": "sk-live-..."},
        {"seq": 3, "type": "assistant/message", "text": "分析完成", "secret": "内部路径"},
    ]
    uploaded, sensitive = upload_session_history(second, history, "sk-live-...")
    print(f"    没有任何 feedback：uploaded={uploaded}，其中含 secret 字段 {sensitive} 条")
    print(f"    exported={second.exported}——「捕获侧不授权」被共享 provider 直接绕过")


if __name__ == "__main__":
    main()
