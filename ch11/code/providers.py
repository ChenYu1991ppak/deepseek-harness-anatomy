"""第 11 章教学重构：subagent 传输 provider（实现层）。

源码对应（以下路径均相对上游仓库根 /Users/chenyu/PycharmProjects/deepseek-harness）：
- subagent-spawn-in-process ↔ packages/subagent/subagent-spawn-in-process/src/index.ts:38
- subagent-fork-in-process  ↔ packages/subagent/subagent-fork-in-process/src/index.ts:60
- completedTurnPrefix       ↔ packages/subagent/subagent-fork-in-process/src/index.ts:46
- out-of-process 四兄弟（acp/codex/claude-code/dsh-sdk）
  ↔ packages/subagent/subagent-acp/src/index.ts:80-83（capabilities 全 false）
- startLocal（本地激活的建子管线）↔ packages/subagent/subagent/src/manager.ts:294
- captureLocalResult（结算读取）  ↔ packages/subagent/subagent/src/activation.ts:147
- resolveChildAgentOptions        ↔ packages/subagent/subagent/src/child-agent.ts:98
- 挂载                            ↔ packages/bundle/base/cordis.patch.yml:374-381

spawn 与 fork 的差异只有一处：fork 先贡献父会话的「已完成回合前缀」作为 seed
（inherits_parent_context=True），spawn 的子会话从零开始。真实版里这个差异表现为
prepareContinuable 返回 {} 还是一个 { seed }，建子与会话创建都由 startLocal 完成。
"""
from __future__ import annotations

from subagent_runtime import (
    ALL_CAPABILITIES,
    SubagentProvider,
    completed_turn_prefix,
    read_result,
)


def start_in_process_run(resolved, manager, provider_name):
    """建 in-process 运行（startLocal 的教学版，packages/subagent/subagent/src/manager.ts:294）。

    复用第 2 章 AgentLoop.create：sessions.create + ReactLoopAgent + 注册 + session-start 事件，
    子 agent 因此是一个「完整的 agent-loop」，而不是一段裸函数。
    """
    parent = resolved["parent"]
    # 子的 options 带上深度：子再委派时 delegation_depth_of 读得到（resolveChildAgentOptions，child-agent.ts:98）
    child_options = dict(resolved.get("child_options", {}))
    child_options["subagent_depth"] = resolved["child_depth"]
    child = parent.ctx.agent_loop.create(**child_options)
    return manager.make_run(provider_name, parent, child, resolved["child_depth"])


def seed_prefix(parent_session, child_session):
    """把父会话的已完成回合前缀播种进子会话（fork 的 seed）。

    [Educational simplification] 真实版在 sessions.create({seed}) 时携带前缀；
    ch02 的 Sessions.create 不接受参数，教学版在创建后、首轮驱动前用 append 逐条补记。
    payload 原样复制（其中 session/agent id 仍指向父，不重写）。
    """
    for ev in completed_turn_prefix(parent_session):
        child_session.append(ev.type, ev.payload)


class InProcessProvider(SubagentProvider):
    """in-process 传输公共基类：drive 统一为「发任务 → 同步到 idle → 读结算」。

    对应 startLocal 建子之后的投递与 captureLocalResult 结算（packages/subagent/subagent/src/activation.ts:147）；spawn/fork 的差异只在 create。
    """

    capabilities = frozenset(ALL_CAPABILITIES)  # in-process 传输支持全部能力

    def drive(self, run, resolved):
        # followup = send + wake_driver（ch02）：把任务作为 user 消息发给子 agent，同步驱动到 idle
        run.child_agent.followup(resolved["prompt"])
        return read_result(run.child_agent.session)


class SpawnProvider(InProcessProvider):
    """spawn 传输（subagent-spawn-in-process）：子会话从零开始，不继承父上下文。"""

    name = "spawn"
    inherits_parent_context = False

    def create(self, resolved, manager):
        return start_in_process_run(resolved, manager, self.name)


class ForkProvider(InProcessProvider):
    """fork 传输（subagent-fork-in-process）：子会话继承父会话的已完成回合前缀。"""

    name = "fork"
    inherits_parent_context = True

    def create(self, resolved, manager):
        run = start_in_process_run(resolved, manager, self.name)
        seed_prefix(resolved["parent"].session, run.child_agent.session)
        return run


class AcpStubProvider(SubagentProvider):
    """out-of-process 传输的教学替身（acp/codex/claude-code/dsh-sdk 四兄弟的代表）。

    真实版经 ctx.subprocess.spawn 起独立 OS 进程（第 6 章）；正因为不在本进程，
    进程内细粒度控制全都做不到，capabilities 全为 false
    （packages/subagent/subagent-acp/src/index.ts:80-83）——带任何特性的请求会在
    start 之前被 seam 的能力检查拒绝。
    """

    name = "acp-stub"
    capabilities = frozenset()
    inherits_parent_context = False

    def create(self, resolved, manager):
        raise NotImplementedError("真实版经 ctx.subprocess.spawn 起独立 OS 进程（见第 6 章）")

    def drive(self, run, resolved):
        raise NotImplementedError("真实版经进程间通道与子进程通信")
