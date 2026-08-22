"""第 14 章教学重构：goal-round-driver（目标自动续轮）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（packages/goal/goal-round-driver/src/，行号见 notes §2.7）：
- drive()               ↔ index.ts:138（校验 goal 状态 + validReservation 竞态围栏 → 起新轮）
- validReservation      ↔ index.ts:334（只有最新 reservation 有效）
- requestDrive()        ↔ index.ts:208（drive 入口）
- 事件接线              ↔ index.ts:245-331（监听 goal 事件触发驱动）
- renderGoalRoundPrompt ↔ prompt.ts:12（续轮提示注入）

事件触发，不是轮询：driver 不周期检查 goal 状态，而是被 goal/changed 唤醒。
goal-round-driver 是 ch17 Ralph 循环的局部形态（notes §5 第 3 条）。

[教学简化] 真实版事件接线监听多个 goal 事件（index.ts:245-331），在「一轮完成/被
block」时才请求续跑；教学版只订阅 goal/changed，goal 一变就请求续跑。
[教学决策] goal/round-start 为教学版事件名，notes 未给出真实轮次启动事件名。
"""
from __future__ import annotations

import os
import sys

_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from cordis import Service  # noqa: E402

from goal import ACTIVE


class GoalRoundDriver(Service):
    """ctx.goalRoundDriver 服务：被 goal/changed 唤醒，goal 仍 active 时自动起下一轮。"""

    def __init__(self, ctx):
        super().__init__(ctx, "goalRoundDriver")
        self._reservation = 0
        self._round = 0
        # 事件触发，不是轮询：订阅 goal/changed（真实版事件接线 index.ts:245-331）
        ctx.on("goal/changed", self._on_goal_changed)

    def _on_goal_changed(self, payload=None):
        self.request_drive()

    def reserve(self):
        """建立新 reservation 并返回 token；更新的 reservation 覆盖旧的。"""
        self._reservation += 1
        return self._reservation

    def valid_reservation(self, token):
        """validReservation（index.ts:334）：只有最新 reservation 有效（竞态围栏）。"""
        return token == self._reservation

    def drive(self, token):
        """drive（index.ts:138）：校验 goal 状态 + reservation 有效 → 起新轮。"""
        if not self.valid_reservation(token):
            return False
        state = self.ctx.goal.state()
        if state is None or state.phase != ACTIVE:
            return False
        self._round += 1
        self.ctx.emit("goal/round-start", {"round": self._round})  # [教学决策] 事件名
        return True

    def request_drive(self):
        """requestDrive（index.ts:208）：drive 入口。"""
        return self.drive(self.reserve())

    def render_round_prompt(self):
        """renderGoalRoundPrompt（prompt.ts:12）：续轮提示注入。"""
        state = self.ctx.goal.state()
        if state is None or state.phase != ACTIVE:
            return None
        return f"继续推进当前目标（第 {self._round} 轮）：{state.text}"
