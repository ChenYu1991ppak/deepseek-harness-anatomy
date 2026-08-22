"""第 17 章教学代码入口：workflow → Ralph → 三条启动面 → SDK 端到端。

直接运行：python3 ch17/code/main.py
四段演示：
  段 1  workflow 引擎：start() 返回 run 句柄，result 永不抛异常，事件只观察
  段 2  Ralph 循环：固定脚本，每轮全新子 agent，previous 报告是唯一交接
  段 3  三条启动面共享 boot：服务清单对比，公共基座是同一棵树
  段 4  Python 内嵌面端到端：FakeModel 两次 generate 模拟「模型→工具→模型」
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from workflow import WorkflowEngine  # noqa: E402
from ralph import run_ralph  # noqa: E402
from boot import boot, booted_services, BASE_SERVICES  # noqa: E402
from sdk import FakeModel, create_agent, run  # noqa: E402


# ---------- 段 1：workflow 引擎 ----------

def demo_script(agent, args):
    """一段编排脚本：先让子 agent 拆解目标，再让另一个子 agent 实现，汇总终值。

    对应真实场景里模型写出的 JS 编排脚本（在 node:vm 内执行）；
    教学版是普通 Python 函数，agent() 钩子以参数传入。
    """
    plan = agent(f"拆解目标: {args['objective']}")
    code = agent(f"按 [{plan}] 实现: {args['objective']}")
    return {"plan": plan, "code": code}


def demo1_workflow():
    print("=" * 60)
    print("段 1  workflow 引擎：脚本 + agent() 钩子扇出 subagent")
    print("=" * 60)

    def subagent_start(prompt):
        return f"完成({prompt[:10]}...)"

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

    def bad_script(agent, args):
        agent("随便跑一步")
        return {"bad": {1, 2, 3}}  # set 不是纯 JSON

    engine2 = WorkflowEngine(subagent_start)
    run2 = engine2.start({
        "script": bad_script,
        "meta": {"name": "bad", "description": "终值不可序列化演示"},
        "args": {},
    })
    print(f"result.stopReason = {run2.result['stopReason']}")
    print(f"result.error      = {run2.result['error'][:40]}...")
    print("调用方没有接到异常——失败被折叠进结果对象（result 永不 reject）。")


# ---------- 段 2：Ralph 循环 ----------

def make_fresh_subagent_factory(scripted_reports):
    """每轮返回全新子 agent 逻辑（对应 fresh provider：inheritsParentContext === false）。

    scripted_reports 按轮次给出每轮子 agent 的 RalphRoundReport。
    """
    calls = {"n": 0}

    def start_subagent(prompt):
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
    print("段 2  Ralph 循环：固定脚本，每轮全新子 agent")
    print("=" * 60)
    # 第 1 轮没做完（continue），第 2 轮做完（complete）
    factory = make_fresh_subagent_factory([
        {"status": "continue", "summary": "搭好骨架，测试未过",
         "nextSteps": ["补齐测试"]},
        {"status": "complete", "summary": "测试全过", "nextSteps": []},
    ])
    engine = WorkflowEngine(factory)
    result = run_ralph(engine, objective="让测试通过", max_rounds=3)
    print(f"终值: outcome={result['value']['outcome']}，"
          f"用了 {result['value']['rounds']} 轮，stopReason={result['stopReason']}")
    print("跨轮交接只有 previous 报告——工作区才是唯一长期记忆。")


# ---------- 段 3：三条启动面共享 boot ----------

def demo3_boot():
    print()
    print("=" * 60)
    print("段 3  三条启动面共享同一 boot")
    print("=" * 60)
    for profile in ("python-sdk", "cli", "web"):
        ctx = boot(profile)
        print(f"{profile:>10}: {booted_services(ctx)}")
    print(f"公共基座: {BASE_SERVICES}")
    print("三条表面的差异只在基座之后的追加服务——内核是同一棵 cordis 插件树。")


# ---------- 段 4：Python 内嵌面端到端 ----------

def demo4_sdk():
    print()
    print("=" * 60)
    print("段 4  Python 内嵌面端到端：模型 → 工具 → 模型")
    print("=" * 60)
    model = FakeModel(script=[
        {"tool_call": {"name": "get_weather", "args": {"city": "北京"}}},
        {"text": "北京今天晴，25°C。"},
    ])
    tools = {"get_weather": lambda city: f"{city}: 晴, 25°C"}
    agent = create_agent(model, tools)
    answer = run(agent, "北京天气怎么样？")
    print(f"最终回答: {answer}")
    print(f"模型调用次数: {model.calls}（第 1 次决定调工具，第 2 次收尾）")
    print(f"消息轨迹: {[m['role'] for m in agent['messages']]}")


if __name__ == "__main__":
    demo1_workflow()
    demo2_ralph()
    demo3_boot()
    demo4_sdk()
