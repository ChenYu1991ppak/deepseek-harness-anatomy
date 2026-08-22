"""第 14 章教学重构：goal 域（事件溯源）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（packages/goal/goal/src/，行号见 notes §2.6）：
- GoalService 操作   ↔ index.ts:251-376（create/edit/pause/resume/complete/block/clear）
- commit()           ↔ index.ts:542（追加事件 → 重折 → 发 goal/changed）
- goal/changed       ↔ domain.ts:104-114
- foldGoal()         ↔ fold.ts:339（从事件流折出整值）
- applyGoalEvent()   ↔ fold.ts:313（单步折叠）
- GoalRef {id, revision} ↔ types.ts:19（CAS 乐观锁把手）
- GoalPhase          ↔ types.ts:44（active/paused/blocked/complete）

事件是整值快照（携带完整 phase/text/revision），不是 delta：foldGoal 无需累积
增量，每个事件自足。这也是「activation 不持久化」（notes §6 第 3 条）的前提：
冷启动只需从历史重折。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from cordis import Service  # noqa: E402

# GoalPhase 四态（types.ts:44）
ACTIVE = "active"
PAUSED = "paused"
BLOCKED = "blocked"
COMPLETE = "complete"


@dataclass(frozen=True)
class GoalRef:
    """goal 坐标（types.ts:19）：{id, revision}，CAS 乐观锁把手。"""

    id: str
    revision: int


@dataclass(frozen=True)
class GoalState:
    """折叠后的整值：phase + text + revision。"""

    phase: str
    text: str
    revision: int


class GoalConflictError(RuntimeError):
    """CAS 冲突：调用方持有的 ref.revision 已落后于当前 revision。"""

    def __init__(self, ref, current):
        super().__init__(f"goal 冲突：ref revision {ref.revision} 已过期，当前 {current}")
        self.ref = ref
        self.current = current


def fold_goal(events):
    """foldGoal（fold.ts:339）：从事件流重折整值。"""
    state = None
    for event in events:
        if event["kind"] == "cleared":
            state = None
        else:  # 整值快照：直接覆盖整值
            state = GoalState(phase=event["phase"], text=event["text"], revision=event["revision"])
    return state


class GoalService(Service):
    """ctx.goal 服务（index.ts:251-376）：每个操作 → 整值事件 → commit → 重折。"""

    GOAL_ID = "goal-1"  # [教学简化] 真实源码支持多 goal；教学版单 goal

    def __init__(self, ctx):
        super().__init__(ctx, "goal")
        self._history = []  # 事件历史：唯一持久状态

    # ---------- 读面 ----------

    def state(self):
        """当前整值（每次从历史折叠，不缓存——fold 廉价且保证一致）。"""
        return fold_goal(self._history)

    def ref(self):
        """当前 GoalRef（供 CAS 操作）；无 goal 时 None。"""
        state = self.state()
        return GoalRef(id=self.GOAL_ID, revision=state.revision) if state else None

    def render_activation(self):
        """渲染 goal activation 文本（注入 systemPrompt）。"""
        state = self.state()
        if state is None or state.phase != ACTIVE:
            return ""
        return f"[当前目标] {state.text}"

    # ---------- 写面：操作（index.ts:251-376） ----------

    def create(self, text):
        """create（index.ts:251-376 操作之一）。"""
        return self._commit("created", text, ACTIVE)

    def edit(self, ref, text):
        """edit：CAS 校验后写入。"""
        self._check_cas(ref)
        return self._commit("edited", text, ACTIVE)

    def pause(self, ref):
        """pause：phase → paused，文本不变。"""
        self._check_cas(ref)
        return self._commit("paused", self.state().text, PAUSED)

    def resume(self, ref):
        """resume：phase → active。"""
        self._check_cas(ref)
        return self._commit("resumed", self.state().text, ACTIVE)

    def complete(self, ref):
        """complete：phase → complete。"""
        self._check_cas(ref)
        return self._commit("completed", self.state().text, COMPLETE)

    def block(self, ref):
        """block：phase → blocked。"""
        self._check_cas(ref)
        return self._commit("blocked", self.state().text, BLOCKED)

    def clear(self):
        """clear（index.ts:376）：追加 cleared 事件，折叠结果置空。"""
        state = self.state()
        revision = (state.revision + 1) if state else 1
        self._history.append({"kind": "cleared", "revision": revision})
        self.ctx.emit("goal/changed", None)

    # ---------- 内部 ----------

    def _check_cas(self, ref):
        """CAS 校验：ref.revision 必须等于当前 revision（types.ts:19）。"""
        state = self.state()
        current = state.revision if state else 0
        if ref.id != self.GOAL_ID or ref.revision != current:
            raise GoalConflictError(ref, current)

    def _commit(self, kind, text, phase):
        """commit（index.ts:542）：追加整值事件 → foldGoal 重折 → 发 goal/changed。"""
        state = self.state()
        revision = (state.revision + 1) if state else 1
        event = {"kind": kind, "text": text, "phase": phase, "revision": revision}
        self._history.append(event)
        new_state = fold_goal(self._history)  # 从头重折，不缓存
        self.ctx.emit("goal/changed", GoalRef(id=self.GOAL_ID, revision=revision))
        return new_state
