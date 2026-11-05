"""第 17 章教学代码入口：workflow → Ralph → 三条启动面 → SDK 端到端。

直接运行：python3 ch17/code/main.py
四段演示：
  段 1  workflow 引擎：校验 → 提交 → 事件流 → 结果（result 永不抛异常）
  段 2  Ralph：opt-in 实验 bundle，固定脚本 + 每轮全新子 agent
  段 3  三条启动面共享 boot：同一份 base + 各自 bundle
  段 4  Python 内嵌面端到端：回执 → 事件 → idle，收口成 RunResult
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from workflow import WorkflowEngine, WorkflowFailure  # noqa: E402
from ralph import run_ralph  # noqa: E402
from boot import boot, booted_services, PROFILE_BUNDLES  # noqa: E402
from sdk import DeepSeekHarness, FakeModel, FakeRuntime  # noqa: E402


# ---------- 段 1：workflow 引擎 ----------

def demo_script(agent, args, phase, log):
    """一段编排脚本：先让子 agent 拆解目标，再让另一个子 agent 实现，汇总终值。

    对应真实场景里模型写出的 JS 编排脚本（PTC 进程里的 vm.Script）；
    教学版是普通 Python 函数，钩子以关键字参数传入。
    """
    phase("plan")
    plan = agent(f"拆解目标: {args['objective']}")
    log("plan ready")
    phase("implement")
    code = agent(f"按 [{plan}] 实现: {args['objective']}")
    return {"plan": plan, "code": code}


def demo1_workflow():
    print("=" * 60)
    print("段 1  workflow 引擎：校验 → 提交 → 事件流 → 结果")
    print("=" * 60)

    def subagent_start(prompt, opts):
        return f"完成({prompt[:10]}...)"

    # 第一段：同步校验失败 → start() 直接 throw，run 根本没被提交
    engine0 = WorkflowEngine(subagent_start)
    try:
        engine0.start({"script": demo_script, "meta": {"name": "", "description": "x"}})
    except WorkflowFailure as e:
        print(f"校验失败（未提交）: {e.code} — {e}")

    # 第二段：提交；第三段：事件流；第四段：结果
    engine = WorkflowEngine(subagent_start)
    run_handle = engine.start({
        "script": demo_script,
        "meta": {"name": "demo", "description": "两步编排演示"},
        "args": {"objective": "实现天气查询"},
    })
    print(f"result: {run_handle.result}")
    print("事件轨迹（只观察）:")
    for name, payload in engine.events:
        print(f"  {name}: {payload}")

    # result 永不 reject：脚本终值不可序列化 → stopReason=error 的结果对象
    print("\n-- 脚本返回不可序列化的终值（set），看 result 如何收口 --")

    def bad_script(agent, args, phase, log):
        agent("随便跑一步")
        return {"bad": {1, 2, 3}}  # set 不是纯 JSON

    engine2 = WorkflowEngine(subagent_start)
    run2 = engine2.start({
        "script": bad_script,
        "meta": {"name": "bad", "description": "终值不可序列化演示"},
        "args": {},
    })
    print(f"result.stopReason = {run2.result['stopReason']}")
    print(f"result.error      = {run2.result['error'][:46]}...")
    print(f"失败 run 的事件: {[name for name, _ in engine2.events]}")
    print("调用方没有接到异常——失败被折叠进结果对象（result 永不 reject）。")


# ---------- 段 2：Ralph 循环（opt-in） ----------

def make_fresh_subagent_factory(scripted_reports):
    """每轮返回全新子 agent 逻辑（对应 fresh provider：inheritsParentContext === false）。"""
    calls = {"n": 0}

    def start_subagent(prompt, opts):
        n = calls["n"]
        calls["n"] += 1
        report = copy.deepcopy(scripted_reports[n])
        print(f"  [第 {n + 1} 轮] 全新子 agent 启动 -> status={report['status']}"
              f"（summary: {report['summary']}）")
        return report

    return start_subagent


def demo2_ralph():
    print()
    print("=" * 60)
    print("段 2  Ralph（opt-in 实验 bundle）：固定脚本，每轮全新子 agent")
    print("=" * 60)
    print("  包位置: packages/experimental/tool-ralph；默认组合不启用")
    print("  分发形态: packages/experimental/ralph-bundle 的 optional-ralph 组")
    # 第 1 轮没做完（continue），第 2 轮做完（complete）
    factory = make_fresh_subagent_factory([
        {"status": "continue", "summary": "搭好骨架，测试未过",
         "evidence": ["create_app.py 已在工作区"],
         "nextSteps": ["补齐测试"], "blocker": ""},
        {"status": "complete", "summary": "测试全过",
         "evidence": ["pytest: 12 passed"], "nextSteps": [], "blocker": ""},
    ])
    engine = WorkflowEngine(factory)
    result = run_ralph(engine, objective="让测试通过", max_rounds=3)
    print(f"终值: status={result['status']}，用了 {result['roundsStarted']} 轮")
    print("跨轮交接只有 previous 报告——工作区才是唯一长期记忆。")

    # 第二组：轮数耗尽 → budget-limited（四态里的第三种）
    factory2 = make_fresh_subagent_factory([
        {"status": "continue", "summary": f"第 {i} 轮仍在推进",
         "evidence": [f"step-{i}"], "nextSteps": ["继续"], "blocker": ""}
        for i in range(1, 3)
    ])
    result2 = run_ralph(WorkflowEngine(factory2), objective="跑到底", max_rounds=2)
    print(f"轮数耗尽: status={result2['status']}，roundsStarted={result2['roundsStarted']}")


# ---------- 段 3：三条启动面共享 boot ----------

def demo3_boot():
    print()
    print("=" * 60)
    print("段 3  三个使用场景共享同一 boot：四个 shipped profile")
    print("=" * 60)
    for profile in ("sdk", "web", "headless", "acp"):
        ctx = boot(profile)
        print(f"{profile:>10}: {booted_services(ctx)}")
    print(f"公共层: {PROFILE_BUNDLES['sdk'][0]}")
    print("四个 profile 的差异只在 base 之后追加的 bundle——内核是同一棵树。")


# ---------- 段 4：Python 内嵌面端到端 ----------

def demo4_sdk():
    print()
    print("=" * 60)
    print("段 4  Python 内嵌面端到端：回执 → 事件 → idle")
    print("=" * 60)
    runtime = FakeRuntime(
        model=FakeModel(script=[
            {"tool_call": {"name": "get_weather", "args": {"city": "北京"}}},
            {"text": "北京今天晴，25°C。"},
        ]),
        tools={"get_weather": lambda city: f"{city}: 晴, 25°C"},
    )
    with DeepSeekHarness(runtime, profile="sdk", dsh_home="/tmp/dsh-home") as harness:
        session = harness.start_session("session-demo")
        result = session.run("北京天气怎么样？")
    print(f"最终回答: {result.final_response}")
    print(f"finish_reason: {result.finish_reason}")
    print(f"模型调用次数: {runtime.model.calls}（第 1 次决定调工具，第 2 次收尾）")
    print(f"通知流: {[n['method'] for n in result.notifications]}")
    print(f"事件轨迹: {[e['type'] for e in result.events]}")


if __name__ == "__main__":
    demo1_workflow()
    demo2_ralph()
    demo3_boot()
    demo4_sdk()
