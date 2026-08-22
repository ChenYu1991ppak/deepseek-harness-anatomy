"""第 11 章教学入口：装配 + 完整运行输出。

装配关系（对应 seam 三角色）：
- SubagentManager(ctx)                        → ctx.subagents（定义层：注册表 + 生命周期）
- SpawnProvider / ForkProvider / AcpStubProvider → 传输 provider（实现层）
- SubagentTool / SubagentControlTool          → 委派工具（消费层，注册进 ch04 的 ctx.tools）
- 复用 ch02：Sessions / LlmStub / SystemPromptService / AgentLoop（子 agent 也经它创建）

运行：python3 ch11/code/main.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
_CH02 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch02", "code"))
_CH04 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch04", "code"))
for _p in (_CH01, _CH02, _CH04):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Context  # noqa: E402  (ch01)
from agent_loop import (  # noqa: E402  (ch02)
    AgentLoop, LlmStub, Sessions, SystemPromptService)
from tools import ToolExecution, ToolRuntime  # noqa: E402  (ch04)
from subagent_runtime import (  # noqa: E402  (本章定义层)
    SubagentDepthError, SubagentError, SubagentManager)
from providers import AcpStubProvider, ForkProvider, SpawnProvider  # noqa: E402  (本章实现层)
from tool_subagent import SubagentControlTool, SubagentTool  # noqa: E402  (本章消费层)


def banner(title):
    print(f"\n=== {title} ===")


def main():
    # ---------- 段 1：装配 ----------
    banner("段 1：装配")
    ctx = Context()
    Sessions(ctx)                          # Service：构造即注册为 ctx.sessions
    ctx.provide("llm", LlmStub())          # 普通对象：显式 provide（ch02 同款装配）
    SystemPromptService(ctx)
    ctx.plugin(AgentLoop)                  # inject=[sessions, llm] 满足才加载；子 agent 也由它创建
    ToolRuntime(ctx)
    ctx.plugin(SubagentManager)            # inject=[agent_loop] 满足才加载 → ctx.subagents
    subagents = ctx.subagents
    subagents.register_provider(SpawnProvider())
    subagents.register_provider(ForkProvider())
    dispose_acp = subagents.register_provider(AcpStubProvider())
    SubagentTool(ctx).apply()
    SubagentControlTool(ctx).apply()
    print(f"已注册 provider: {subagents.list_providers()}")

    # 生命周期监听：subagent/start 与 subagent/end 由 seam 经 ctx.emit 广播（lifecycle.ts:100）
    ctx.on("subagent/start", lambda info: print(
        f"  [事件] subagent/start {info['id']} provider={info['provider']} "
        f"parent={info['parent_session']} child={info['child_session']} depth={info['depth']}"))
    ctx.on("subagent/end", lambda info: print(
        f"  [事件] subagent/end   {info['id']} stop_reason={info['stop_reason']}"))

    main_agent = ctx.agent_loop.create()   # 主 agent：深度 0
    print(f"主 agent 会话: {main_agent.session.id}")

    # ---------- 段 2：能力检查与注册回滚 ----------
    banner("段 2：能力检查与注册回滚")
    try:
        # acp-stub 是 out-of-process 传输：capabilities 全 false，带 persona 的请求在 start 前被拒
        subagents.start("acp-stub", {"parent": main_agent,
                                     "prompt": "审计这次变更", "persona": "安全审计员"})
    except SubagentError as e:
        print(f"被拒: {e.code}: {e}")
    try:
        subagents.register_provider(SpawnProvider())   # 重名注册
    except SubagentError as e:
        print(f"被拒: {e.code}: {e}")
    dispose_acp()                                      # 效应回滚：注销 acp-stub
    print(f"回滚后 provider 列表: {subagents.list_providers()}")

    # ---------- 段 3：一次性委派（spawn） ----------
    banner("段 3：一次性委派（spawn）")
    run = subagents.start("spawn", {"parent": main_agent,
                                    "prompt": "排查模块 A 的测试为什么失败"})
    print(f"结算: status={run.status} stop_reason={run.result.stop_reason}")
    print(f"子的回复: {run.result.text}")
    print(f"主会话事件数: {len(main_agent.session.log)}；"
          f"子会话 {run.child_session} 事件数: {len(run.child_agent.session.log)}")

    # ---------- 段 4：fork 继承历史 ----------
    banner("段 4：fork 继承历史")
    main_agent.followup("先同步一下：模块 A 的进度延期了")
    run_fork = subagents.start("fork", {"parent": main_agent,
                                        "prompt": "基于同步的上下文给出追赶计划"})
    types = [ev.type for ev in run_fork.child_agent.session.log]
    print(f"子会话事件序列（{len(types)} 条）: {types}")
    print(f"子的回复: {run_fork.result.text}")

    # ---------- 段 5：委派深度与谱系 ----------
    banner("段 5：委派深度与谱系")
    run2 = subagents.start("spawn", {"parent": run.child_agent, "max_depth": 2,
                                     "prompt": "排查子模块 A-1"})
    print(f"二级子运行: depth={run2.depth} child={run2.child_session}")
    try:
        subagents.start("spawn", {"parent": run2.child_agent, "max_depth": 2,
                                  "prompt": "排查 A-1 的依赖"})
    except SubagentDepthError as e:
        print(f"深度拦截: {e.code}: {e}")
    print(f"主会话的子会话: {subagents.list_children(main_agent.session.id)}")
    print(f"{run.child_session} 的子会话: {subagents.list_children(run.child_session)}")

    # ---------- 段 6：可续委派与控制 ----------
    banner("段 6：可续委派与控制")
    run_id = subagents.start_continuable("spawn", {"parent": main_agent})
    reply1 = subagents.followup(run_id, "先统计测试用例数量")
    print(f"第 1 轮回复: {reply1.text}")
    reply2 = subagents.followup(run_id, "再列出失败的用例")
    print(f"第 2 轮回复: {reply2.text}")
    idle = subagents.wait_for_idle()
    print(f"存活且 idle 的运行: {[r.id for r in idle]}")
    subagents.kill(run_id)
    killed = subagents.runs[run_id]
    print(f"kill 之后: status={killed.status} stop_reason={killed.result.stop_reason}")

    # ---------- 段 7：模型视角——经工具调用委派 ----------
    banner("段 7：模型视角——经工具调用委派")
    exec_ = ToolExecution(call_id="call-1", name="subagent",
                          arguments={"parent": main_agent, "provider": "spawn",
                                     "prompt": "验证部署脚本"})
    tool_result = ctx.tools.execute(exec_)
    print(f"工具结果: {tool_result.content}")
    print(f"主会话事件数仍为: {len(main_agent.session.log)}（委派不污染主会话）")


if __name__ == "__main__":
    main()
