"""第 14 章教学重构：approval 接缝（「能否做」通道）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（packages/interaction/user-approval/src/）：
- ApprovalService.request() ↔ index.ts:257（审批入口；真实版 L259 有回合封装检查，教学版省略）
- ApprovalService.decide()  ↔ index.ts:304（私有裁决：L312 never 短路，L317-321 跑 approval/request 瀑布）
- effectivePolicy()         ↔ index.ts:285
- setPolicy()               ↔ index.ts:226（ask/never 两态，折入 approval/policy）
- ApprovalOutcome           ↔ types.ts:29（allowed-once | rejected | cancelled | unavailable）

每次裁决记录审计事件对 approval/asked + approval/decided（回合封装，notes §1.1）。
fail-closed：任何异常或非 allowed-once 的结果都折叠为 rejected。

[教学决策] 真实源码的 approval/request 瀑布经容器的 ctx.waterfall 承载（notes §4.2）；
教学版复用第 4 章 waterfall_wrap（中间件式）与内部监听器列表承载，语义不变。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
_CH04 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch04", "code"))
for _p in (_CH01, _CH04):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Service  # noqa: E402
from tools import waterfall_wrap  # noqa: E402

# 审批结果四值（types.ts:29）
ALLOWED_ONCE = "allowed-once"   # 放行这一次
REJECTED = "rejected"           # 拒绝
CANCELLED = "cancelled"         # 提问被取消（超时/打断）
UNAVAILABLE = "unavailable"     # 无人应答（fail-closed 兜底值）


@dataclass
class ApprovalRequest:
    """一次审批请求（ApprovalService.request 的入参，index.ts:257）。"""

    tool_name: str
    arguments: dict = field(default_factory=dict)
    request_id: int = 0  # 由 ApprovalService 裁决时填入序号


class ApprovalService(Service):
    """ctx.approval 服务：ch04 ask 分支接缝的实现方。

    真实形态（notes §3.1/§4.2）：request()（index.ts:257）是入口，私有 decide()
    （index.ts:304）承担裁决——never 短路、approval/request 瀑布、fail-closed 折叠。
    构造即注册（第 1 章约定）：ch04 的 serviceAsk 机会消费 ctx.approval，
    本服务就是那个「被机会消费」的实现（notes §5 第 1 条）。
    """

    def __init__(self, ctx):
        super().__init__(ctx, "approval")
        self._policy = "ask"
        self._request_listeners = []   # approval/request 瀑布监听器（应答者）
        self._seq = 0

    # ---------- 策略面 ----------

    def set_policy(self, policy):
        """设置 ask/never 两态策略（index.ts:226），折入 approval/policy。"""
        if policy not in ("ask", "never"):
            raise ValueError(f"policy 只能是 ask/never，收到：{policy}")
        self._policy = policy
        self.ctx.emit("approval/policy", policy)

    def effective_policy(self):
        """生效策略（index.ts:285）：从 approval/policy 折叠读出。"""
        return self._policy

    # ---------- 瀑布注册 ----------

    def on_request(self, listener):
        """注册 approval/request 瀑布监听器，签名 (req, next) -> outcome。

        监听器负责把问题渲染给人并收集回答；不调 next() 即短路。
        真实项目里终端 UI 注册在这里；教学版是脚本化的「人」（main.py）。
        """
        self._request_listeners.append(listener)

    # ---------- 请求/裁决 ----------

    def request(self, req):
        """审批请求（index.ts:257）：入口，委托私有 _decide 裁决。

        [教学简化] 真实版在 L259 做回合封装检查（一个回合只允许发一次请求）；
        教学版省略该检查，故可重复调用 request。
        """
        return self._decide(req)

    def _decide(self, req):
        """私有裁决（index.ts:304）：never 短路 → 审计 asked → 瀑布 → fail-closed 收敛 → 审计 decided。"""
        if self.effective_policy() == "never":
            return REJECTED  # never 策略短路（L312）：不问人，直接拒绝
        self._seq += 1
        req.request_id = self._seq
        self.ctx.emit("approval/asked", req)  # 审计事件（与 decided 成对，回合封装）
        try:
            outcome = waterfall_wrap(
                self._request_listeners, (req,), fallback=lambda: UNAVAILABLE
            )  # approval/request 环绕瀑布（L317-321）：裁决权交给下游应答者
        except Exception:
            outcome = UNAVAILABLE  # fail-closed：异常也不能产生批准
        # fail-closed 折叠：除 allowed-once 外一律 rejected
        result = outcome if outcome == ALLOWED_ONCE else REJECTED
        self.ctx.emit("approval/decided", {"request_id": req.request_id, "outcome": result})
        return result


def main():
    """最小自检：never 短路；ask 走瀑布；无人应答 fail-closed。"""
    from cordis import Context

    # never 短路：不进瀑布、不问人
    ctx1 = Context()
    a1 = ApprovalService(ctx1)
    a1.set_policy("never")
    assert a1.request(ApprovalRequest("shell")) == REJECTED

    # ask + 应答者批准：瀑布返回 allowed-once
    ctx2 = Context()
    a2 = ApprovalService(ctx2)
    a2.on_request(lambda req, next: ALLOWED_ONCE)
    assert a2.request(ApprovalRequest("shell")) == ALLOWED_ONCE

    # ask + 无人应答：fallback unavailable → 折叠为 rejected（fail-closed）
    ctx3 = Context()
    a3 = ApprovalService(ctx3)
    assert a3.request(ApprovalRequest("shell")) == REJECTED

    print("approval.py 自检通过：never 短路 / ask 走瀑布 / fail-closed")


if __name__ == "__main__":
    main()
