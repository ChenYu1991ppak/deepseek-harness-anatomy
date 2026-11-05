"""第 14 章教学重构：goal-round-driver（目标自动续轮）。

仅标准库，Python 3.10+。运行：python3 -m main

源码对应（HEAD 8bd073392，packages/goal/goal-round-driver/src/）：
- drive()               ↔ index.ts:146（readyToDrive → 检查点 → 预算 → 起一轮）
- drivable()            ↔ index.ts:106（phase=active 且 activation=armed）
- 轮次预算                ↔ index.ts:182（roundsStarted 到顶就 block，code='round-limit'）
- reservation 自身       ↔ index.ts:200-206（一轮一个 RoundAttempt：goalId/revision/round/messageId）
- 入队                    ↔ index.ts:208（agent.followup(message)）
- requestDrive()        ↔ index.ts:224（把触发合并到一个串行驱动器上）
- validReservation      ↔ index.ts:372（提交前的围栏，在 agent/pre-step 里用）
- 事件接线                ↔ index.ts:302（ctx.on('goal/changed')）
- renderGoalRoundPrompt ↔ prompt.ts:12（<goal_round> 续轮提示）

事件触发，不是轮询：driver 不周期检查，而是被 goal/changed 等事件唤醒。
goal-round-driver 是 ch17 Ralph 循环的局部形态。

[教学简化] 真实版还有一个完整状态机（queued → claimed → admitted / stale / cancelled），
并在 agent/pre-step 里拿 validReservation 把过期轮次拒掉。教学版保留「reservation 是
一轮的身份证、旧凭证失效」这一核心，省略状态机与 pre-step 拒绝。
[教学决策] goal/round-start 为教学版事件名，真实版没有单独的轮次启动事件。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from cordis import Service  # noqa: E402


@dataclass(frozen=True)
class RoundAttempt:
    """一轮的预约（index.ts:200 的 RoundAttempt 简化版）：绑死 goal 的某个 revision 与轮号。"""

    goal_id: str
    revision: int
    round: int


class GoalRoundDriver(Service):
    """ctx.goalRoundDriver 服务：被 goal/changed 唤醒，goal 可驱动时自动起下一轮。"""

    def __init__(self, ctx):
        super().__init__(ctx, "goalRoundDriver")
        self._attempt = None    # 当前预约；新预约覆盖旧的
        self._round = 0         # 最近起的轮号（prompt 里用）
        ctx.on("goal/changed", self._on_goal_changed)  # index.ts:302

    def _on_goal_changed(self, payload=None):
        self.request_drive()

    def reserve(self):
        """建立新一轮预约（index.ts:200）：绑死当前 goal 的 revision。"""
        ref = self.ctx.goal.ref()
        if ref is None:  # goal 已被 clear：没有可预约的对象
            return None
        attempt = RoundAttempt(ref.id, ref.revision, self.ctx.goal.rounds_started() + 1)
        self._attempt = attempt
        return attempt

    def valid_reservation(self, attempt):
        """validReservation（index.ts:372）：预约必须仍对应当前 active revision。"""
        state = self.ctx.goal.state()
        return (
            attempt is not None
            and self._attempt is attempt
            and state is not None
            and state.phase == "active"
            and self.ctx.goal.activation() == "armed"
            and attempt.revision == state.revision
            and attempt.round == self.ctx.goal.rounds_started() + 1
        )

    def drive(self, attempt):
        """drive（index.ts:146）：预约有效 + 仍有轮次预算 → 起新轮。"""
        if not self.valid_reservation(attempt):
            return False  # 过期预约：goal 已变（edit/pause/complete）或被新预约顶掉
        round_no = self.ctx.goal.start_round()
        if round_no is None:
            return False  # 预算到顶：start_round 已把 goal block 掉（index.ts:182）
        self._round = round_no
        self.ctx.emit("goal/round-start", {"round": round_no})  # [教学决策] 事件名
        return True

    def request_drive(self):
        """requestDrive（index.ts:224）：触发合流入口——每次触发都申请新预约再驱动。"""
        attempt = self.reserve()
        if attempt is None:
            return False
        return self.drive(attempt)

    def render_round_prompt(self):
        """renderGoalRoundPrompt（prompt.ts:12）：<goal_round> 提示，带 Objective 与轮次。"""
        state = self.ctx.goal.state()
        if state is None or state.phase != "active":
            return None
        return (
            f"<goal_round>\nObjective: {state.text!r}\n"
            f"Round: {self._round}/{self.ctx.goal.MAX_ROUNDS}\n"
            "Continue working toward the objective in this same session.\n</goal_round>"
        )
