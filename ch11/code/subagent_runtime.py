"""第 11 章教学重构：subagent 委派 seam（定义层）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应：
- SubagentRuntime（seam 类）      ↔ packages/subagent/subagent/src/index.ts:171
- registerProvider ↔ index.ts:369；getProvider ↔ :392；expectProvider ↔ :449；list ↔ :400
- start（一次性入口）↔ index.ts:414；startContinuable ↔ :212；followup ↔ :231；interrupt ↔ :255
- assertCapabilities ↔ index.ts:481；assertSubagentMaxDepth ↔ depth.ts:42
- emitLifecycle（subagent/start、subagent/end）↔ packages/subagent/subagent/src/lifecycle.ts:100
- readResult（finalAssistantOutput + toStopReason）↔ subagent-in-process-driver/src/index.ts:208
- completedTurnPrefix ↔ subagent-fork-in-process/src/index.ts:48
- SubagentRun / SubagentRunInfo ↔ types.ts:249/36；SubagentProvider 契约 ↔ types.ts:285
- delegationDepthOf ↔ depth.ts:28；resolveChildDepth ↔ child-agent.ts:48；
  childSessionMeta ↔ child-agent.ts:102

[Educational simplification] 真实版是异步管线：start 返回带 Promise 的 run 句柄，
observeRun 订阅结算（lifecycle.ts:133）；教学版同步——start() 把子 agent 驱动到完成才返回。
[Educational simplification] 真实版把子会话 lineage meta 写进会话持久 meta（session.header）；
ch02 的 Session 没有 header 字段，教学版改存 SubagentManager.session_meta 表（以会话 id 为键）。
[Educational decision G1] 真实版 provider 契约是单个 start（返回 run 句柄）；教学版同步化后
拆成 create（建子 agent）+ drive（驱动到完成），seam 在两步之间广播 start/end，
事件次序与真实版 observeRun「立即 start → 等结算 → end」一致。
"""
from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass

# 复用第 1 章：Service 构造即注册（cordis.py:271-284），ctx.effect 表达可逆效应。
_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)
from cordis import Service  # noqa: E402

# 能力白名单：in-process 传输全部支持；out-of-process 传输全部为 false
# （subagent-acp/src/index.ts:146-149）。
ALL_CAPABILITIES = ("output_schema", "depth_limit", "tool_filter", "persona")

# 请求字段 -> 所需能力：请求用到某字段，provider 必须声明对应能力（assertCapabilities 逐项检查）。
REQUEST_CAPABILITY = {
    "output_schema": "output_schema",
    "max_depth": "depth_limit",
    "tool_filter": "tool_filter",
    "persona": "persona",
}

# 请求未显式给 max_depth 时的默认委派深度上限（教学版取 2：最多委派到第二层）。
DEFAULT_MAX_DEPTH = 2


class SubagentError(Exception):
    """subagent seam 的统一错误，带机器可读 code（真实版 SubagentError，error.ts:10）。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class SubagentDepthError(SubagentError):
    """委派深度超过 max_depth（resolveChildDepth 抛出，child-agent.ts:54）。"""

    def __init__(self, child_depth, max_depth):
        super().__init__(
            "MAX_DEPTH", f"委派深度 {child_depth} 超过上限 max_depth={max_depth}")
        self.child_depth = child_depth
        self.max_depth = max_depth


@dataclass(frozen=True)
class SubagentResult:
    """一次运行的结算结果（readResult，subagent-in-process-driver/src/index.ts:208）：子 agent 最终 assistant 输出 + 停止原因。"""

    text: str         # finalAssistantOutput：子会话里最后一条 assistant/message 的文本
    stop_reason: str  # toStopReason：end_turn / max_depth / killed ……


class SubagentRunContext:
    """一次运行的身份与结算状态（SubagentRun + SubagentRunInfo 的教学版，types.ts:249/36）。

    真实版 SubagentRun.result 是 Promise（异步结算）；教学版同步：
    start 返回时 status 已是 completed/killed，result 已就位。
    """

    def __init__(self, run_id, provider_name, parent_session, child_agent, depth):
        self.id = run_id
        self.provider = provider_name
        self.parent_session = parent_session
        self.child_agent = child_agent              # ↔ SubagentRun.localAgent（in-process 时存在）
        self.child_session = child_agent.session.id
        self.depth = depth
        self.status = "running"                     # running -> completed | killed
        self.result = None                          # 结算后填入 SubagentResult


class SubagentProvider(ABC):
    """传输 provider 契约（types.ts:285）：name + capabilities + inherits_parent_context + create/drive。

    inherits_parent_context：子 agent 是否继承父上下文（spawn=False，fork=True）。
    [Educational decision G1] 真实版是单个 start；教学版同步化后拆为 create + drive。
    """

    name = ""
    capabilities = frozenset()
    inherits_parent_context = False

    @abstractmethod
    def create(self, resolved, manager):
        """建子会话 + 子 agent，返回 SubagentRunContext（尚未驱动）。

        resolved 是 seam 补全过的请求（含 child_depth）；怎么创建子 agent 由传输自己决定：
        in-process 用父的 ctx 建本地 agent，out-of-process 真实版会 spawn 独立 OS 进程。
        """

    @abstractmethod
    def drive(self, run, resolved):
        """把子 agent 驱动到完成，返回 SubagentResult。"""


class SubagentRegistry:
    """provider 注册表（SubagentRuntime 的 providers Map，index.ts:172）。

    注册是可逆效应：register_provider 返回注销 disposer（第 1 章 ctx.effect），
    与第 5 章 capability seam 的注册面同一范式。
    """

    def __init__(self, ctx):
        self.ctx = ctx
        self._providers = {}

    def register_provider(self, provider):
        """按名注册 + 重名拒绝 + 效应回滚（registerProvider，index.ts:369）。"""
        name = provider.name
        if name in self._providers:
            raise SubagentError("DUPLICATE_PROVIDER", f"provider 重名: {name}")
        self._providers[name] = provider
        self.ctx.emit("subagent/provider-added", {"name": name})

        def unregister():
            self._providers.pop(name, None)

        # effect 立即执行 lambda 并收集其返回的清理函数（cordis.py:167）：卸载即注销
        return self.ctx.effect(lambda: unregister, label=f"subagent-provider:{name}")

    def expect_provider(self, name):
        """按名取 provider，缺失抛 UNKNOWN_PROVIDER（expectProvider，index.ts:449）。"""
        provider = self._providers.get(name)
        if provider is None:
            raise SubagentError("UNKNOWN_PROVIDER", f"未注册的 provider: {name}")
        return provider

    def list_providers(self):
        """按字典序列出已注册 provider 名（list，index.ts:400）。"""
        return sorted(self._providers)


def assert_capabilities(provider, request):
    """能力检查（assertCapabilities，index.ts:481）：请求用到的每个特性，provider 必须声明对应能力。

    缺能力抛 UNSUPPORTED_CAPABILITY——检查发生在 provider.start 之前，
    out-of-process provider（能力全 false）因此被挡在启动之外。
    """
    for field_name, capability in REQUEST_CAPABILITY.items():
        if field_name in request and capability not in provider.capabilities:
            raise SubagentError(
                "UNSUPPORTED_CAPABILITY",
                f"provider '{provider.name}' 不支持能力 '{capability}'（请求使用了 {field_name}）")


def assert_max_depth_value(max_depth):
    """max_depth 必须是正整数（assertSubagentMaxDepth，depth.ts:42）。"""
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1:
        raise SubagentError("INVALID_REQUEST", f"max_depth 必须是正整数，实际为 {max_depth!r}")


def delegation_depth_of(agent, session_meta):
    """agent 所处委派深度：会话 lineage meta 与 options 取大者（delegationDepthOf，depth.ts:28）。

    运行时只能加深、不能减小 agent 的委派深度（单调下界，depth.ts:20-23）。
    真实版读 session.header.delegationDepth；ch02 的 Session 没有 header 字段，
    教学版改读 manager 的 session_meta lineage 表（以会话 id 为键）。
    """
    meta = session_meta.get(agent.session.id, {})
    session_depth = meta.get("delegation_depth", 0)
    option_depth = agent.options.get("subagent_depth", 0)
    return max(session_depth, option_depth)


def resolve_child_depth(parent, max_depth, session_meta):
    """子深度 = 父深度 + 1，超过 max_depth 抛 SubagentDepthError（resolveChildDepth，child-agent.ts:48）。"""
    child_depth = delegation_depth_of(parent, session_meta) + 1
    if child_depth > max_depth:
        raise SubagentDepthError(child_depth, max_depth)
    return child_depth


def child_session_meta(parent, child_depth):
    """子会话 lineage meta（childSessionMeta，child-agent.ts:102）：父会话 / origin / 深度。"""
    return {
        "parent_session": parent.session.id,
        "origin": "subagent",
        "delegation_depth": child_depth,
    }


def completed_turn_prefix(session):
    """已完成回合前缀：截至（含）最后一个 turn/end 的全部事件（completedTurnPrefix，subagent-fork-in-process/src/index.ts:48）。

    fork 传输用它给子会话播种：子 agent 创建时就「记得」父会话已完成的历史。
    """
    last_end = -1
    for i, ev in enumerate(session.log):
        if ev.type == "turn/end":
            last_end = i
    if last_end < 0:
        return []
    return session.log[: last_end + 1]


def read_result(session):
    """从子会话读结算结果（readResult，subagent-in-process-driver/src/index.ts:208）：最后一条 assistant 输出 + 停止原因。

    assistant/message 的 payload 形如 {"message": {"content": [{"type": "text", "text": ...}]}}
    （ch02 的 create_assistant_message，agent_loop.py:186）；此处把所有 text 块拼回文本。
    """
    text, stop_reason = "", "max_depth"
    for ev in session.log:
        if ev.type == "assistant/message":
            blocks = ev.payload["message"]["content"]
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            stop_reason = "end_turn"
    return SubagentResult(text=text, stop_reason=stop_reason)


class SubagentManager(Service):
    """ctx.subagents seam：注册表 + 运行生命周期（SubagentRuntime 的教学版，index.ts:171）。

    [Educational simplification] 真实版异步（start 返回 run 句柄，observeRun 等结算）；
    教学版同步：start() 在子 agent 结算后才返回。
    """

    inject = ["agent_loop"]  # kill 时从 agent_loop.agents 注销子 agent（对应 dispose）

    def __init__(self, ctx, config=None):
        super().__init__(ctx, "subagents")
        self.registry = SubagentRegistry(ctx)
        self.runs = {}           # run_id -> SubagentRunContext
        self.session_meta = {}   # 子会话 id -> lineage meta（子会话持久 meta 的教学版）
        self._next_run_id = 0

    # ---------- 注册面 ----------

    def register_provider(self, provider):
        """委托给注册表（registerProvider，index.ts:369）。"""
        return self.registry.register_provider(provider)

    def list_providers(self):
        return self.registry.list_providers()

    # ---------- 一次性（one-shot） ----------

    def start(self, provider_name, request):
        """一次性委派（start，index.ts:414）：校验 → 建子 → 广播 start → 驱动到完成 → 广播 end。

        返回结算后的 SubagentRunContext（真实版返回带 Promise 的 run 句柄；教学版同步已结算）。
        """
        provider, resolved = self._prepare(provider_name, request)
        run = provider.create(resolved, self)
        self.runs[run.id] = run
        self.announce("subagent/start", self._start_info(run))  # observeRun：立即广播 start
        try:
            run.result = provider.drive(run, resolved)
            run.status = "completed"
        finally:
            self.announce("subagent/end", self._end_info(run))  # observeRun：结算后广播 end
        return run

    # ---------- 可续（continuable） ----------

    def start_continuable(self, provider_name, request):
        """可续委派（startContinuable，index.ts:212）：建子但不驱动，run 存活等待消息。

        [Educational simplification] 真实版由 ContinuationManager 管理存活子 agent；
        教学版直接把 run 留在 runs 表里。
        """
        provider, resolved = self._prepare(provider_name, request)
        run = provider.create(resolved, self)
        self.runs[run.id] = run
        self.announce("subagent/start", self._start_info(run))
        return run.id

    def followup(self, run_id, message):
        """向存活子 agent 发一条消息并驱动一轮（followup，index.ts:231 ↔ send_message 工具）。"""
        run = self._expect_run(run_id)
        run.child_agent.followup(message)          # ch02：send + wake_driver，同步驱动到 idle
        return read_result(run.child_agent.session)

    def kill(self, run_id):
        """中断并结算存活子 agent（interrupt，index.ts:255 ↔ interrupt_agent 工具）。

        [Educational simplification] 真实版 interrupt 走驱动器的 abort 路径；ch02 的
        ReactLoopAgent 没有 abort 态，教学版的 kill 即「从 agent 注册表移除 + 结算 run」，
        子 agent 对象随之不可达（对应 dispose）。
        """
        run = self._expect_run(run_id)
        self.ctx.agent_loop.agents.pop(run.child_session, None)   # 从 agent 注册表移除（对应 dispose）
        run.status = "killed"
        run.result = SubagentResult(text="", stop_reason="killed")
        self.announce("subagent/end", self._end_info(run))

    def wait_for_idle(self):
        """校验所有存活子 agent 均已 idle 并返回它们。

        [Educational simplification] 真实版异步等待子 agent 停稳；ch02 教学版同步——
        start/followup 返回即代表子 agent 已 idle，此方法总是立即返回。
        """
        active = [run for run in self.runs.values() if run.status == "running"]
        for run in active:
            if run.child_agent.phase != "idle":
                raise SubagentError("RUN_BUSY", f"子 agent 未 idle: {run.id}")
        return active

    # ---------- 生命周期与谱系 ----------

    def announce(self, event, payload):
        """生命周期广播（emitLifecycle，lifecycle.ts:100）：subagent/* 事件统一经 ctx.emit 发出。"""
        self.ctx.emit(event, payload)

    def list_children(self, session_id):
        """沿 lineage 找子会话（listChildren，index.ts:339）。

        [Educational simplification] 真实版在 scope 内沿 lineage 发现（第 9 章）；
        教学版直接扫 session_meta lineage 表。
        """
        return [sid for sid, meta in self.session_meta.items()
                if meta["parent_session"] == session_id]

    # ---------- 内部 ----------

    def _prepare(self, provider_name, request):
        """启动前校验（index.ts:415-417）：expectProvider → assertCapabilities → 深度校验与解析。"""
        provider = self.registry.expect_provider(provider_name)
        assert_capabilities(provider, request)
        max_depth = request.get("max_depth", DEFAULT_MAX_DEPTH)
        assert_max_depth_value(max_depth)
        resolved = dict(request)
        resolved["child_depth"] = resolve_child_depth(
            request["parent"], max_depth, self.session_meta)
        return provider, resolved

    def make_run(self, provider_name, parent, child_agent, child_depth):
        """开一个 run 上下文并写入子会话 lineage meta（provider.create 内调用）。"""
        self._next_run_id += 1
        run = SubagentRunContext(
            f"subagent-run-{self._next_run_id}", provider_name,
            parent.session.id, child_agent, child_depth)
        # 子会话持久 meta：父会话 / origin / 深度（真实版写 session.header，教学版存 lineage 表）
        self.session_meta[child_agent.session.id] = child_session_meta(parent, child_depth)
        return run

    def _expect_run(self, run_id):
        run = self.runs.get(run_id)
        if run is None or run.status != "running":
            raise SubagentError("UNKNOWN_RUN", f"run 不存在或已结算: {run_id}")
        return run

    def _start_info(self, run):
        """subagent/start 载荷（SubagentStartInfo，types.ts）。"""
        return {"id": run.id, "provider": run.provider,
                "parent_session": run.parent_session,
                "child_session": run.child_session, "depth": run.depth}

    def _end_info(self, run):
        """subagent/end 载荷（SubagentRunEndInfo：stopReason / lastAssistantMessage）。"""
        stop_reason = run.result.stop_reason if run.result else "killed"
        last_text = run.result.text if run.result else ""
        return {"id": run.id, "provider": run.provider,
                "child_session": run.child_session,
                "stop_reason": stop_reason, "last_assistant_message": last_text}
