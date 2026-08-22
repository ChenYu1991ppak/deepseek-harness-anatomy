"""第 14 章教学重构：serviceAsk——ch04 ask 分支的消费点。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（行号见 notes §5 第 1 条）：
- serviceAsk ↔ core/tools/index.ts:1689
  ask 分支经 serviceAsk 机会消费 ctx.get('approval')——本章的 ApprovalService
  正是该接缝的实现方；无实现时降级为 deny。

[教学决策] 真实源码中 ask 决策由流水线内的 guards/mode 产生；第 4 章教学版没有
ask 产生方（tools.py _prepare 注释：ask 降级为 deny），故 AskGate 用白名单模拟
「ask 决策点」，再交给 service_ask 消费——完整演示 ask 分支的路径。
白名单来自 permission-presets 捆绑的 ask_tools（presets.py），切换 preset 时一并更新。
"""
from __future__ import annotations

import os
import sys

_CH04 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch04", "code"))
if _CH04 not in sys.path:
    sys.path.insert(0, _CH04)

from tools import PreToolDecision  # noqa: E402

from approval import ALLOWED_ONCE, ApprovalRequest  # noqa: E402


def service_ask(ctx, exec):
    """serviceAsk（core/tools/index.ts:1689）：机会消费 approval 接缝。"""
    approval = getattr(ctx, "approval", None)  # 机会消费：可能不存在
    if approval is None:
        return PreToolDecision.deny("approval 服务未实现，降级为 deny")
    outcome = approval.request(
        ApprovalRequest(tool_name=exec.name, arguments=dict(exec.arguments))
    )
    if outcome == ALLOWED_ONCE:
        return PreToolDecision.allow()
    return PreToolDecision.deny(f"approval 结果：{outcome}")


class AskGate:
    """[教学决策] ask 决策点：工具在白名单内 → 产生 ask，交给 service_ask 消费。"""

    def __init__(self, ctx, ask_tools=()):
        self._ctx = ctx
        self.ask_tools = set(ask_tools)

    def __call__(self, exec, next):
        if exec.name in self.ask_tools:
            return service_ask(self._ctx, exec)
        return next()
