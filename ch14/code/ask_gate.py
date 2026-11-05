"""第 14 章教学重构：serviceAsk——ch04 ask 分支的消费点。

仅标准库，Python 3.10+。运行：python3 -m main

源码对应（HEAD 8bd073392）：
- serviceAsk ↔ packages/core/tools/src/pipeline.ts:432
  （调用点在 pipeline.ts:203-205 的 prepare()：pre-execute 瀑布返回 ask 才进来）
- ask 分支机会消费 ctx.get('approval')（pipeline.ts:436）；无实现时降级 deny（:437-442）
- 无 agent 的执行同样降级 deny（:443-448）：没有 agent 就没有会话可审计
- 四种 outcome 一对一映射（:457-471）：allowed-once → allow；其余 → deny 且各带不同 reason

[教学决策] 真实源码里 ask 决策由 tools/pre-execute 监听器产出（如 auto-review 的
askUser、hooks-claude-code 的 PreToolUse ask）；guards 结构上产生不了 ask
（ToolGuard 只能返回拒绝理由或 undefined）。第 4 章教学版没有 ask 产生方，
故 AskGate 用白名单模拟「ask 决策点」，再交给 service_ask 消费。
[教学简化] 这份白名单在 main.py 的 assemble() 里写死，不随 /permission 切换变化；
permission-presets 只管 sandbox 半与 approval 策略半，不决定哪些调用会 ask。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH04 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch04", "code"))
for _p in (_HERE, _CH04):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import PreToolDecision  # noqa: E402

from approval import ALLOWED_ONCE, CANCELLED, ApprovalRequest  # noqa: E402


def service_ask(ctx, exec):
    """serviceAsk（pipeline.ts:432）：机会消费 approval 接缝。"""
    approval = getattr(ctx, "approval", None)  # 机会消费（pipeline.ts:436）：可能不存在
    if approval is None:
        return PreToolDecision.deny("approval 服务未实现，降级为 deny")
    outcome = approval.request(
        ApprovalRequest(tool_name=exec.name, arguments=dict(exec.arguments))
    )
    if outcome == ALLOWED_ONCE:
        return PreToolDecision.allow()
    if outcome == CANCELLED:
        return PreToolDecision.deny("approval for tool was cancelled")
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
