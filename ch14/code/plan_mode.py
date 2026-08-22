"""第 14 章教学重构：plan-mode（协同规划闸）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（packages/plan/plan-mode/src/，行号见 notes §2.10）：
- foldPlanMode() ↔ index.ts:129（折叠 plan-mode 状态）
- set()          ↔ index.ts:425（设置 plan mode）
- exit_plan_mode ↔ index.ts:305-393（经 userQuestions 请人复核计划，plan-review 意图）

[教学简化] 真实版 foldPlanMode 从事件流折叠状态；教学版用单个 active 标志。
"""
from __future__ import annotations

import os
import sys

_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from cordis import Service  # noqa: E402

from questions import INTENT_PLAN_REVIEW  # noqa: E402


class PlanMode(Service):
    """ctx.planMode 服务：plan-mode 协作闸——先计划、经人复核、再执行。"""

    def __init__(self, ctx):
        super().__init__(ctx, "planMode")
        self.active = False

    def set(self, active):
        """set（index.ts:425）：设置 plan mode。"""
        self.active = active
        self.ctx.emit("plan-mode/changed", active)  # [教学决策] 事件名

    def exit_via_review(self, plan_text):
        """exit_plan_mode（index.ts:305-393）：经 userQuestions 请人复核计划。"""
        answer = self.ctx.userQuestions.ask(plan_text, intent=INTENT_PLAN_REVIEW)
        approved = str(answer).strip().lower() in ("approve", "approved", "yes")
        if approved:
            self.set(False)
        return approved
