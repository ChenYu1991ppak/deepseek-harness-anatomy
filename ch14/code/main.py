"""第 14 章可运行入口：装配闭环。

运行：python3 main.py

段落：
1. 装配——介入/授权/协同规划服务进同一容器
2. approval 瀑布——策略两态 + fail-closed
3. ch04 回链——serviceAsk 消费 ask 分支（无实现降级 deny）
4. permission-presets——捆绑 derive/apply/pin + /permission 命令
5. goal 事件溯源——整值事件 + CAS + activation
6. goal-round-driver——事件触发续轮 + 竞态围栏
7. userQuestions 与 plan-review——「请回答/复核」通道
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (
    _HERE,
    os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code")),
    os.path.abspath(os.path.join(_HERE, "..", "..", "ch04", "code")),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Context, Symbols  # noqa: E402
from tools import ToolDefinition, ToolExecution, ToolRuntime  # noqa: E402

from approval import ALLOWED_ONCE, REJECTED, ApprovalRequest, ApprovalService  # noqa: E402
from ask_gate import AskGate  # noqa: E402
from commands import CommandRuntime  # noqa: E402
from goal import GoalConflictError, GoalService  # noqa: E402
from goal_round import GoalRoundDriver  # noqa: E402
from plan_mode import PlanMode  # noqa: E402
from presets import derive, pin_initial_permission, register_permission_command  # noqa: E402
from questions import UserQuestionService  # noqa: E402


def banner(title):
    print(f"\n── {title} " + "─" * max(0, 56 - len(title)))


class ScriptedHuman:
    """[教学决策] 脚本化的「人」：同时为两条问人通道提供回答。

    真实项目里两条通道都由终端 UI 应答；教学版用脚本列表模拟：
    - approval 通道：approval_answers（allow/deny）
    - userQuestions 通道：question_answers（任意字符串）
    """

    def __init__(self, approval_answers=(), question_answers=()):
        self._approval_answers = list(approval_answers)
        self._question_answers = list(question_answers)

    def as_approval_responder(self):
        """注册进 approval/request 瀑布：渲染问题 → 脚本回答 → 返回 outcome 即完成应答。"""

        def respond(req, next):
            if not self._approval_answers:
                return next()  # 无脚本答案：交给内层（fallback unavailable → rejected）
            answer = self._approval_answers.pop(0)
            print(f"  [人] 批准 `{req.tool_name}`？→ {answer}")
            return ALLOWED_ONCE if answer == "allow" else REJECTED

        return respond

    def as_question_provider(self, question):
        """注册为 userQuestions 的唯一 UI provider：渲染问题 → 脚本回答。"""
        if not self._question_answers:
            raise RuntimeError("脚本答案已用完")
        answer = self._question_answers.pop(0)
        print(f"  [人] 问题 #{question.id}（{question.intent}）→ {answer}")
        return answer


def assemble(approval_answers=(), question_answers=(), ask_tools=("shell", "fs_write")):
    """装配一个容器：介入/授权/协同规划服务 + ch04 工具流水线。"""
    ctx = Context()
    ApprovalService(ctx)
    UserQuestionService(ctx)
    CommandRuntime(ctx)
    GoalService(ctx)
    GoalRoundDriver(ctx)
    PlanMode(ctx)
    ToolRuntime(ctx)

    human = ScriptedHuman(approval_answers, question_answers)
    ctx.approval.on_request(human.as_approval_responder())
    ctx.userQuestions.set_provider(human.as_question_provider)

    ctx.tools.register(ToolDefinition(
        "shell", "执行 shell 命令", {"cmd": "str"}, lambda a: f"已执行: {a['cmd']}"))
    ctx.tools.register(ToolDefinition(
        "fs_write", "写文件", {"path": "str"}, lambda a: f"已写入: {a['path']}"))
    gate = AskGate(ctx, ask_tools)  # [教学决策] ask 决策点，见 ask_gate.py
    ctx.tools.on_pre_execute(gate)

    register_permission_command(ctx, gate)
    return ctx


def demo_approval_waterfall():
    banner("2. approval 瀑布：策略两态 + fail-closed")
    ctx = assemble(approval_answers=["allow", "deny"])
    approval = ctx.approval
    ctx.on("approval/asked", lambda req: print(f"  [审计] approval/asked #{req.request_id} {req.tool_name}"))
    ctx.on("approval/decided", lambda p: print(f"  [审计] approval/decided #{p['request_id']} → {p['outcome']}"))

    approval.set_policy("never")  # 只读姿态：never → 直接 rejected，不问人
    print("policy=never：短路拒绝，无审计事件")
    print("  结果 →", approval.request(ApprovalRequest("shell")))

    approval.set_policy("ask")
    print()
    print("policy=ask，人批准")
    print("  结果 →", approval.request(ApprovalRequest("shell")))
    print()
    print("policy=ask，人拒绝")
    print("  结果 →", approval.request(ApprovalRequest("shell")))
    print()
    print("policy=ask，无人应答")
    print("  结果 →", approval.request(ApprovalRequest("shell")), "（fail-closed）")


def demo_service_ask():
    banner("3. ch04 回链：serviceAsk 消费 ask 分支")

    # 场景一：无 approval 服务 → 降级为 deny
    print("无 approval 服务：降级为 deny")
    ctx0 = Context()
    ToolRuntime(ctx0)
    ctx0.tools.register(ToolDefinition(
        "shell", "执行 shell 命令", {"cmd": "str"}, lambda a: f"已执行: {a['cmd']}"))
    ctx0.tools.on_pre_execute(AskGate(ctx0, ("shell",)))
    r0 = ctx0.tools.execute(ToolExecution("c1", "shell", {"cmd": "rm -rf /tmp/build"}))
    print(f"  结果 → {r0.content} (is_error={r0.is_error})")

    # 场景二/三：有 approval 服务——人批准 → 执行；人拒绝 → 拒绝
    ctx = assemble(approval_answers=["allow", "deny"])
    print()
    print("有 approval 服务，人批准")
    r1 = ctx.tools.execute(ToolExecution("c2", "shell", {"cmd": "make clean"}))
    print(f"  结果 → {r1.content} (is_error={r1.is_error})")
    print()
    print("有 approval 服务，人拒绝")
    r2 = ctx.tools.execute(ToolExecution("c3", "shell", {"cmd": "rm -rf /tmp/cache"}))
    print(f"  结果 → {r2.content} (is_error={r2.is_error})")


def demo_presets():
    banner("4. permission-presets：捆绑 derive/apply/pin + /permission")
    print("derive(workspace-write)    →", derive("workspace-write"))
    print("derive(danger-full-access) →", derive("danger-full-access"))

    ctx = assemble()
    bundle = pin_initial_permission(ctx, "workspace-write")
    print(f"pinInitialPermission → pinned={ctx.pinned_preset}, approval={bundle['approval']}")
    print("/permission →", ctx.commands.execute("/permission"))
    print("/permission danger-full-access →", ctx.commands.execute("/permission danger-full-access"))

    # 切换后 shell 不在 ask 白名单：全放行，直接执行
    r = ctx.tools.execute(ToolExecution("c4", "shell", {"cmd": "echo full-access"}))
    print("切换后执行 shell →", r.content, f"(is_error={r.is_error})")


def demo_questions_and_plan_review():
    banner("7. userQuestions 与 plan-review：「请回答/复核」通道")
    ctx = assemble(question_answers=["utils.py", "reject", "approve"])
    ctx.on("userQuestions/asked", lambda q: print(f"  [事件] userQuestions/asked #{q.id} intent={q.intent}"))
    ctx.planMode.set(True)

    answer = ctx.userQuestions.ask("先重构哪个文件？")
    print(f"普通提问 → {answer}")

    plan = "计划：1) 梳理签名 2) 补测试 3) 更新文档"
    ok1 = ctx.planMode.exit_via_review(plan)
    print(f"第一次复核 → 批准={ok1}，仍在 plan mode={ctx.planMode.active}")
    ok2 = ctx.planMode.exit_via_review(plan)
    print(f"第二次复核 → 批准={ok2}，仍在 plan mode={ctx.planMode.active}")


def demo_goal_sourcing():
    banner("5. goal 事件溯源：整值事件 + CAS + activation")
    ctx = assemble()
    goal = ctx.goal
    ctx.on("goal/changed", lambda ref: print(f"  [事件] goal/changed → {ref}"))

    goal.create("完成 utils 模块重构")
    ref = goal.ref()
    goal.edit(ref, "完成 utils 模块重构（含测试）")  # CAS 通过
    try:
        goal.edit(ref, "用旧 ref 覆盖")  # ref 已过期 → 冲突
    except GoalConflictError as e:
        print(f"  [CAS] {e}")
    goal.pause(goal.ref())
    goal.resume(goal.ref())
    print("当前状态 →", goal.state())
    print("activation →", goal.render_activation())
    goal.complete(goal.ref())
    print("complete 后 activation →", repr(goal.render_activation()))
    goal.clear()
    print("clear 后状态 →", goal.state())


def demo_goal_round_driver():
    banner("6. goal-round-driver：事件触发续轮 + 竞态围栏")
    ctx = assemble()
    ctx.on("goal/round-start", lambda p: print(f"  [轮次] 自动起第 {p['round']} 轮"))

    ctx.goal.create("完成 utils 模块重构")  # goal/changed → 自动起第 1 轮
    ctx.goal.complete(ctx.goal.ref())       # goal/changed 仍会发，但非 active → 不续轮
    print("goal complete 后 → goal/changed 仍发，非 active 不续轮")
    ctx.goal.create("新目标")               # goal/changed → 自动起第 2 轮

    # 竞态围栏：旧 reservation 被新的顶掉
    driver = ctx.goalRoundDriver
    t1 = driver.reserve()
    t2 = driver.reserve()  # 更新的 reservation 覆盖
    print(f"旧 reservation drive → {driver.drive(t1)}")
    print(f"新 reservation drive → {driver.drive(t2)}")
    print("续轮 prompt →", driver.render_round_prompt())


def main():
    banner("1. 装配")
    ctx = assemble(approval_answers=["allow"], question_answers=["ok"])
    services = getattr(ctx, Symbols.services)
    print("已注册服务:", ", ".join(sorted(services)))
    print("两条问人通道：approval=「能否做」，userQuestions=「请回答/复核」")

    demo_approval_waterfall()
    demo_service_ask()
    demo_presets()
    demo_goal_sourcing()
    demo_goal_round_driver()
    demo_questions_and_plan_review()


if __name__ == "__main__":
    main()
