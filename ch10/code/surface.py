"""第 10 章教学重构：surface —— 产生 LLM 消息的事件的有序视图。

[教学决策] 第 3 章只为 surface 预留了机制槽位（SurfaceOp 的 replace，
packages/core/types.ts:372-374），教学代码未实现；本章把这个槽位实现出来，
以支撑 compaction「压缩旧上下文、替换 surface 区间」的动作。

源码对应（packages/core/session/）：
- SurfaceSession.surface             ↔ Session 的有序 surface 节点列（产生 LLM 消息的事件）
- SurfaceSession.replace_generation  ↔ surface 的 replaceGeneration（压缩事务稳定性断言，
                                       compaction/index.ts:154 assertStable）
- surface_op {'op':'replace',...}    ↔ SurfaceOp 的 replace 形态（types.ts:372-374）

surface 事件类型只有三种（SurfaceEventType，types.ts:354-358）：
user/message、assistant/message、tool/result。compaction/* 事件是 log-only，
只进追加式 log、不进 surface。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
_CH02 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch02", "code"))
for _p in (_CH01, _CH02):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_loop import Session  # noqa: E402  (ch02)

# surface 事件类型（SurfaceEventType，types.ts:354-358）：
# 只有这三种事件会产生 LLM 消息、进入有序 surface。
SURFACE_EVENT_TYPES = ("user/message", "assistant/message", "tool/result")


class SurfaceSession(Session):
    """带 surface 的会话：在 ch02 Session 之上加一层有序 surface 视图。

    两层结构（这是理解 compaction 的关键）：
    - log（继承自 Session）：追加式全量历史，compaction/* 事件只进这里；
    - surface（本章新增）：产生 LLM 消息的事件的有序视图，是模型真正「看到」的内容。

    压缩时不动 log，只在 surface 上把一段区间替换成一个摘要节点——
    历史可回溯（log 完整），模型上下文变小（surface 变短）。
    """

    def __init__(self, ctx, session_id):
        super().__init__(ctx, session_id)
        self.surface = []            # 有序 surface 节点列，元素是事件 seq
        self.replace_generation = 0  # 每次 replace +1，压缩事务的稳定性断言用

    def append(self, type, payload=None, surface_op=None):
        """追加事件；若是 surface 事件，按 surface_op 更新 surface。

        surface_op 两种形态（SurfaceOp，types.ts:372-374）：
        - None（等价 'append'）：追加到 surface 尾部（默认）；
        - {'op': 'replace', 'start': i, 'end': j}：用本事件这一个节点
          替换 surface[i:j] 整段区间（压缩摘要落盘就走这条）。
        """
        event = super().append(type, payload)
        if type in SURFACE_EVENT_TYPES:
            if isinstance(surface_op, dict) and surface_op.get("op") == "replace":
                start, end = surface_op["start"], surface_op["end"]
                # 把 [start, end) 区间替换为这一个节点（压缩摘要节点）
                self.surface[start:end] = [event.seq]
                self.replace_generation += 1
            else:
                self.surface.append(event.seq)
        # 非 surface 事件（compaction/* 等）只进 log、不进 surface
        return event

    def surface_events(self):
        """按序返回 surface 当前暴露的事件列表。"""
        return [self.log[seq] for seq in self.surface]

    def surface_messages(self):
        """把 surface 事件转成 LLM 消息列表——模型下一轮真正收到的内容。"""
        messages = []
        for event in self.surface_events():
            message = event.payload.get("message")
            if message is not None:
                messages.append(message)
        return messages
