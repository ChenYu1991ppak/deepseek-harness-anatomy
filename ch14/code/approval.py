"""第 14 章教学重构：approval 接缝（「能否做」通道）。

仅标准库，Python 3.10+。运行：python3 -m main

源码对应（HEAD 8bd073392，packages/interaction/user-approval/src/）：
- ApprovalService.request()  ↔ index.ts:242（入口：L244 回合封装检查 → 审计 asked → decide → 审计 decided）
- ApprovalService.decide()   ↔ index.ts:293（私有裁决：L295 已取消短路 → L301 never 短路 → L306-310 approval/request 瀑布）
- effectivePolicy()          ↔ index.ts:270（session 覆盖 ?? 配置默认 ?? 'ask'）
- setPolicy()                ↔ index.ts:211（切换策略并向 agent 注入一条变更提示）
- ApprovalOutcome            ↔ types.ts:32（allowed-once | rejected | cancelled | unavailable）

契约要点（与旧版不同，按 HEAD 重写）：
- 审计对 approval/asked + approval/decided 在 request() 里成对落下，never 短路也照样落。
- decide() 不再把结果折叠成 rejected：非法返回值/异常归一为 unavailable，
  中止信号返回 cancelled，其余原样返回。
- 「非放行即拒绝」发生在工具层：pipeline.ts:432 的 serviceAsk 把四种 outcome
  一对一映射成 allow/deny，并给每种拒绝附不同 reason。

[教学决策] 真实源码的 approval/request 瀑布经容器的 ctx.waterfall 承载（index.ts:306-310）；
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

# 审批结果四值（types.ts:32）
ALLOWED_ONCE = "allowed-once"   # 放行这一次：唯一授权值
REJECTED = "rejected"           # 拒绝
CANCELLED = "cancelled"         # 提问被取消（信号中止）
UNAVAILABLE = "unavailable"     # 无人应答 / 应答者异常（fail-closed 兜底值）

OUTCOMES = frozenset({ALLOWED_ONCE, REJECTED, CANCELLED, UNAVAILABLE})


@dataclass
class ApprovalRequest:
    """一次审批请求（ApprovalRequestEvent，types.ts:63）。

    真实版还带 agent / callId / reason / displayReason / signal；教学版只留工具身份。
    """

    tool_name: str
    arguments: dict = field(default_factory=dict)
    request_id: int = 0  # 由 ApprovalService 发请求时填入序号


class ApprovalService(Service):
    """ctx.approval 服务：ch04 ask 分支接缝的实现方。

    真实形态：request()（index.ts:242）是入口，负责回合封装检查与审计对；
    私有 decide()（index.ts:293）承担裁决——取消短路、never 短路、
    approval/request 瀑布、非法答复归一。构造即注册（第 1 章约定）：
    ch04 的 serviceAsk 机会消费 ctx.approval，本服务就是被消费的实现。
    """

    def __init__(self, ctx):
        super().__init__(ctx, "approval")
        self._policy = "ask"
        self._request_listeners = []   # approval/request 瀑布监听器（应答者）
        self._seq = 0

    # ---------- 策略面 ----------

    def set_policy(self, policy):
        """切换 ask/never 两态策略（index.ts:211），折入 approval/policy。

        真实版还会给 agent 注入一条「策略已从 X 变为 Y」的用户消息（L215-221）。
        """
        if policy not in ("ask", "never"):
            raise ValueError(f"policy 只能是 ask/never，收到：{policy}")
        self._policy = policy
        self.ctx.emit("approval/policy", policy)

    def effective_policy(self):
        """生效策略（index.ts:270）：session 覆盖 ?? 配置默认 ?? 'ask'。"""
        return self._policy

    # ---------- 瀑布注册 ----------

    def on_request(self, listener):
        """注册 approval/request 瀑布监听器，签名 (req, next) -> outcome。

        监听器负责把问题渲染给人并收集回答；不调 next() 即认领本次请求。
        真实项目里终端 UI 注册在这里；教学版是脚本化的「人」（main.py）。
        """
        self._request_listeners.append(listener)

    # ---------- 请求/裁决 ----------

    def request(self, req):
        """审批请求（index.ts:242）：回合检查 → 落 asked → decide → 落 decided。

        [教学简化] 真实版 L244 检查当前有打开中的回合（批量事件必须落在
        回合的 commit/replay 边界内），不满足就抛错；教学版没有回合模型，省略。
        审计对在入口处成对落下，因此 never 短路同样留痕（与旧版叙事不同）。
        """
        self._seq += 1
        req.request_id = self._seq
        self.ctx.emit("approval/asked", req)  # 审计事件（L252）
        outcome = self._decide(req)
        self.ctx.emit("approval/decided", {"request_id": req.request_id, "outcome": outcome})  # L259
        return outcome

    def _decide(self, req):
        """私有裁决（index.ts:293）：取消短路 → never 短路 → 瀑布 → 归一。"""
        if getattr(req, "cancelled", False):
            return CANCELLED  # 已中止（L295）：不问人，直接取消
        if self.effective_policy() == "never":
            return REJECTED  # never 短路（L301）：任何监听器都不能把它翻成放行
        try:
            outcome = waterfall_wrap(
                self._request_listeners, (req,), fallback=lambda: UNAVAILABLE
            )  # approval/request 环绕瀑布（L306-310）：兜底 unavailable
        except Exception:
            outcome = UNAVAILABLE  # 应答者抛错：问题侧 fail-closed（L317）
        # 非法返回值归一为 unavailable（L314）；合法四值原样返回，不在这里折叠
        return outcome if outcome in OUTCOMES else UNAVAILABLE


def main():
    """最小自检：never 短路；ask 走瀑布；无人应答 fail-closed；取消值原样返回。"""
    from cordis import Context

    # never 短路：不进瀑布、不问人，但审计对照样落
    ctx1 = Context()
    a1 = ApprovalService(ctx1)
    a1.set_policy("never")
    assert a1.request(ApprovalRequest("shell")) == REJECTED

    # ask + 应答者批准：瀑布返回 allowed-once
    ctx2 = Context()
    a2 = ApprovalService(ctx2)
    a2.on_request(lambda req, next: ALLOWED_ONCE)
    assert a2.request(ApprovalRequest("shell")) == ALLOWED_ONCE

    # ask + 无人应答：fallback unavailable，原样返回（拒绝折叠在工具层）
    ctx3 = Context()
    a3 = ApprovalService(ctx3)
    assert a3.request(ApprovalRequest("shell")) == UNAVAILABLE

    # ask + 已取消：decide 短路返回 cancelled
    ctx4 = Context()
    a4 = ApprovalService(ctx4)
    req = ApprovalRequest("shell")
    req.cancelled = True
    assert a4.request(req) == CANCELLED

    print("approval.py 自检通过：never 短路 / ask 走瀑布 / 未应答 unavailable / 取消区分")


if __name__ == "__main__":
    main()
