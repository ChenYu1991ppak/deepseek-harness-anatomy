"""第 14 章教学重构：goal 域（事件溯源）。

仅标准库，Python 3.10+。运行：python3 -m main

源码对应（HEAD 8bd073392，packages/goal/goal/src/）：
- GoalService 写面操作  ↔ index.ts:305-449（create:305 · edit:331 · pause:354 · resume:366 · complete:393 · block:411 · clear:435）
- CAS 校验              ↔ index.ts:458（expectCurrent：id/revision 不符抛 GOAL_STALE_REVISION）
- commit()              ↔ index.ts:608（追加整值事件 → 发 goal/changed）
- foldGoal()            ↔ fold.ts:339（从事件流折出整值）
- applyGoalEvent()      ↔ fold.ts:313（单步折叠）
- GoalRef {id, revision} ↔ types.ts:20（CAS 乐观锁把手）
- GoalPhase             ↔ types.ts:45（active/paused/blocked/complete）
- GoalActivation        ↔ types.ts:72（armed/disarmed，**进程本地**、不持久化）
- activation-changed    ↔ index.ts:497（setActivation 发 goal/activation-changed）

事件是整值快照（携带完整 objective/phase/revision），不是 delta：foldGoal 无需累积
增量，每个事件自足。foldGoal 的返回值里故意没有 activation——activation 只活在
进程内，冷启动重折只能得到持久态。这也是「activation 不持久化」的代码依据。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from cordis import Service  # noqa: E402

# GoalPhase 四态（types.ts:45）
ACTIVE = "active"
PAUSED = "paused"
BLOCKED = "blocked"
COMPLETE = "complete"


@dataclass(frozen=True)
class GoalRef:
    """goal 坐标（types.ts:20）：{id, revision}，CAS 乐观锁把手。"""

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
    """foldGoal（fold.ts:339）：从事件流重折整值；activation 不在返回值里。"""
    state = None
    for event in events:
        if event["kind"] == "cleared":
            state = None
        else:  # 整值快照：直接覆盖整值
            state = GoalState(phase=event["phase"], text=event["text"], revision=event["revision"])
    return state


class GoalService(Service):
    """ctx.goal 服务：每个操作 → 整值事件 → commit → 重折。"""

    MAX_ROUNDS = 3  # [教学简化] 真实版 maxGoalRounds 随 create 入参（默认 256，index.ts:240）

    def __init__(self, ctx):
        super().__init__(ctx, "goal")
        self._history = []   # 事件历史：唯一持久状态
        self._activation = "disarmed"  # 进程本地激活态（types.ts:72），不进历史
        self._rounds = 0     # 已开始的轮数（真实版由折叠 goal 轮 user/message 推进）
        self._next_id = 1    # 每次 create 生成新 id（index.ts:314 的 goal-<uuid>）
        self._goal_id = None

    # ---------- 读面 ----------

    def state(self):
        """当前整值（每次从历史折叠，不缓存——fold 廉价且保证一致）。"""
        return fold_goal(self._history)

    def ref(self):
        """当前 GoalRef（供 CAS 操作）；无 goal 时 None。"""
        state = self.state()
        return GoalRef(id=self._goal_id, revision=state.revision) if state else None

    def activation(self):
        """进程本地激活态（index.ts:484）：armed 才能自动续轮，冷启动一律 disarmed。"""
        return self._activation

    def rounds_started(self):
        """已起的轮数（fold 视角的 roundsStarted，types.ts:89）。"""
        return self._rounds

    def start_round(self):
        """起一轮：预算内返回轮号并推进 roundsStarted；到顶则 block 并返回 None。"""
        state = self.state()
        if state is None or state.phase != ACTIVE or self._activation != "armed":
            return None
        if self._rounds >= self.MAX_ROUNDS:
            self.block(self.ref())  # index.ts:182：round-limit
            return None
        self._rounds += 1
        return self._rounds

    def render_activation(self):
        """渲染 goal 的模型可见文本（[教学简化]，真实形式见 goal-round-driver/prompt.ts:12）。

        真实源码没有单独的「goal activation 段」；模型看到的目标文本来自续轮 prompt。
        教学版保留一个最小渲染，用来说明它是从历史派生、而非另存的状态。
        """
        state = self.state()
        if state is None or state.phase != ACTIVE:
            return ""
        return f"[当前目标] {state.text}"

    # ---------- 写面：操作（index.ts:305-449） ----------

    def create(self, text):
        """create（index.ts:305）：新 goal 从 revision 1、phase=active、activation=armed 起。

        [教学简化] 真实 id 是 goal-<uuid>；教学版用自增序号。
        """
        state = self.state()
        if state is not None and state.phase != COMPLETE:
            raise NotImplementedError("已有未完成 goal：真实版抛 GOAL_ALREADY_EXISTS（index.ts:309）")
        self._goal_id = f"goal-{self._next_id}"
        self._next_id += 1
        self._rounds = 0  # 新 goal 的轮次从头计
        return self._commit("created", text, ACTIVE, armed=True, fresh=True)

    def edit(self, ref, text):
        """edit（index.ts:331）：CAS 校验后写入；phase 与 activation 都不变。

        真实版 edit 调 commitCurrent(..., runtime.activation)，把当前激活态原样
        带进这次提交（index.ts:344）——所以这里 armed=None 表示「保持原状」。
        """
        self._check_cas(ref)
        return self._commit("edited", text, ACTIVE, armed=None)

    def pause(self, ref):
        """pause（index.ts:354）：active → paused，并 disarm 自动续轮。"""
        self._check_cas(ref)
        return self._commit("paused", self.state().text, PAUSED, armed=False)

    def resume(self, ref):
        """resume（index.ts:366）：paused/blocked → active，重新 armed。"""
        self._check_cas(ref)
        return self._commit("resumed", self.state().text, ACTIVE, armed=True)

    def complete(self, ref):
        """complete（index.ts:393）：→ complete，并 disarm。"""
        self._check_cas(ref)
        return self._commit("completed", self.state().text, COMPLETE, armed=False)

    def block(self, ref):
        """block（index.ts:411）：→ blocked，并 disarm。"""
        self._check_cas(ref)
        return self._commit("blocked", self.state().text, BLOCKED, armed=False)

    def clear(self, ref):
        """clear（index.ts:435）：写一条 tombstone（revision+1），当前 goal 置空并 disarm。"""
        self._check_cas(ref)
        state = self.state()
        revision = state.revision + 1
        self._history.append({"kind": "cleared", "revision": revision})
        self._set_activation("disarmed")  # 真实版同样走 commit(..., 'disarmed')
        self.ctx.emit("goal/changed", None)

    # ---------- 内部 ----------

    def _check_cas(self, ref):
        """CAS 校验（index.ts:458）：ref.id/revision 必须与当前一致（types.ts:20）。"""
        state = self.state()
        current = state.revision if state else 0
        if ref.id != self._goal_id or ref.revision != current:
            raise GoalConflictError(ref, current)

    def _commit(self, kind, text, phase, armed, fresh=False):
        """commit（index.ts:608）：追加整值事件 → 重折 → 发 goal/changed。

        armed=None 表示保持当前激活态（edit 用）；fresh=True 表示新 goal 从
        revision 1 起（真实版 create 每次生成新 id，index.ts:313-320）。
        """
        state = self.state()
        revision = 1 if fresh else ((state.revision + 1) if state else 1)
        event = {"kind": kind, "text": text, "phase": phase, "revision": revision}
        self._history.append(event)
        if armed is not None:
            self._set_activation("armed" if armed else "disarmed")  # index.ts:497
        new_state = fold_goal(self._history)  # 从头重折，不缓存
        self.ctx.emit("goal/changed", GoalRef(id=self._goal_id, revision=revision))
        return new_state

    def _set_activation(self, value):
        """只改进程本地态，并在真正变化时发 goal/activation-changed（index.ts:497）。"""
        if self._activation == value:
            return
        self._activation = value
        self.ctx.emit("goal/activation-changed", {"activation": value})
