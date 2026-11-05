"""第 14 章教学重构：plan-mode（协同规划闸）。

仅标准库，Python 3.10+。运行：python3 -m main

源码对应（HEAD 8bd073392，packages/plan/plan-mode/src/）：
- planProjectionDefinition ↔ index.ts:135（key='plan'，stateVersion 4：active/pending/wanted/entered）
- set()                    ↔ index.ts:431（已在该态 → noop；有打开回合 → queued；否则立即 append plan/mode）
- exit_plan_mode 工具      ↔ index.ts:280（defineTool；执行体 :301-362）
- 复核问题                 ↔ index.ts:318-330（REVIEW_ID='plan-review'，Approve / Keep planning 两个选项，带 plan-review intent）
- 复核结果处理             ↔ index.ts:350-361（选 Approve 才退出；否则抛错把反馈带回模型）
- 先计划后执行             ↔ plan:policy 提示段（index.ts:198-203）只在 plan mode 激活时出现

[教学简化] 真实版的状态由 session 日志折叠（plan/mode 事件），并在下一回合被接受时
才提交选择；教学版用一个 active 标志。真实版的「复核」走 user-questions 瀑布；
教学版直接问 UserQuestionService。
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

from questions import INTENT_PLAN_REVIEW  # noqa: E402

APPROVE_LABEL = "Approve"        # index.ts:76
KEEP_PLANNING_LABEL = "Keep planning"  # index.ts:79


class PlanMode(Service):
    """ctx.planMode 服务：先计划、经人复核、再执行。"""

    def __init__(self, ctx):
        super().__init__(ctx, "planMode")
        self.active = False

    def set(self, active):
        """set（index.ts:431）：已在该态 → noop；否则提交 plan/mode。"""
        if self.active == active:
            return "noop"
        self.active = active  # [教学简化] 真实版在有打开回合时排队（queued），下回合再提交
        self.ctx.emit("plan/mode", {"active": active})  # 真实事件名（plan-mode/index.ts:447）
        return "committed"

    def exit_via_review(self, plan_text):
        """exit_plan_mode（index.ts:280，执行体 :301-362）：经 userQuestions 请人复核计划。"""
        if not plan_text.lstrip().startswith("#"):
            # 真实版要求计划是一份完整 markdown，且以 # 标题开头（index.ts:308）
            raise ValueError("exit_plan_mode 需要以 # 标题开头的完整 markdown 计划")
        answer = self.ctx.userQuestions.ask(plan_text, intent=INTENT_PLAN_REVIEW)
        approved = str(answer).strip() == APPROVE_LABEL  # 只认一个精确标签（index.ts:352）
        if approved:
            self.set(False)  # 退出照样走 set，拿 noop/queued/committed 的一致语义
        else:
            # 真实版抛错，把用户的反馈带回模型继续改计划（index.ts:354-357）
            self.ctx.emit("plan-mode/keep-planning", {"feedback": str(answer)})  # [教学决策] 事件名
        return approved
