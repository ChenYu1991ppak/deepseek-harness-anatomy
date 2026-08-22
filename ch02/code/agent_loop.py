"""第 2 章教学重构：最小 agent-loop 闭环（session / llm stub / system-prompt / agent-loop）。

仅标准库，Python 3.10+。

源码对应（行号为 notes §13 r7 裁定）：
- Session/Sessions          ↔ packages/core/session/src/index.ts:425（Session；append :604-653，seq getter :565-567，
                              deepFreeze :627）/ :792（SessionStore，即 ctx.sessions）（r7 裁定 notes §13 D2）
- LlmStub                   ↔ packages/llm/llm/src/index.ts:913（LlmRuntime.stream → streamWithRegistration :917）
                              + types.ts:291（StreamChunk）；LlmStub 是纯教学桩，源码无此类（r7 裁定 notes §13 D4）
- SystemPromptService/render_prompt ↔ packages/core/system-prompt/src/index.ts:338（section :381、assemble :467、renderPrompt :212-217）
- AgentLoop                 ↔ packages/core/agent-loop/src/index.ts:296（static inject :296-297、create :589、publish :556-570）
- ReactLoopAgent/Inbox      ↔ packages/core/agent-loop/src/agent.ts:64 / inbox.ts:25
- create_assistant_message  ↔ packages/llm/llm/src/message.ts:206-217
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import os
import sys

_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from cordis import Service  # noqa: E402

__all__ = [
    "AgentLoop",
    "Inbox",
    "LlmStub",
    "PromptAssembly",
    "PromptSection",
    "ReactLoopAgent",
    "Session",
    "SessionEvent",
    "Sessions",
    "SystemPromptService",
    "create_assistant_message",
    "render_prompt",
]


# ---------- session：append-only 事件日志 ----------


@dataclass(frozen=True)
class SessionEvent:
    """append-only 事件条目，对应 SessionEvent（packages/core/session/src/types.ts:404）。

    [教学简化] 真实事件还带 timestamp 与 surface 元数据；frozen 对应 deepFreeze。
    """

    session_id: str
    seq: int
    type: str
    payload: dict


class Session:
    """append-only 事件日志，对应 Session（packages/core/session/src/index.ts:425）。"""

    def __init__(self, ctx, session_id):
        self.ctx = ctx
        self.id = session_id
        self.log: list[SessionEvent] = []

    def append(self, type, payload):
        """追加一条事件：seq == len(log)（index.ts:604-653），事件不可变（deepFreeze，index.ts:627）。

        [教学简化] 省略 JSON 校验（append 内）与 surface/surfaceOp（第 10 章机制，G9）。
        """
        event = SessionEvent(self.id, len(self.log), type, payload)
        self.log.append(event)
        self.ctx.emit("session/event", event)
        return event


class Sessions(Service):
    """会话注册表服务 ctx.sessions，对应 SessionStore（packages/core/session/src/index.ts:792）。

    [教学简化] 真实版本有 prepare/commitPrepared 两段式创建与 enter/announce 登记；此处直接创建。
    """

    def __init__(self, ctx, config=None):
        super().__init__(ctx, "sessions")
        self._sessions = {}
        self._next_id = 0

    def create(self):
        self._next_id += 1
        session = Session(self.ctx, f"session-{self._next_id:04d}")
        self._sessions[session.id] = session
        return session

    def get(self, session_id):
        return self._sessions.get(session_id)


# ---------- llm stub：无真实模型的流式服务 ----------


class LlmStub:
    """[教学简化] ctx.llm 服务的桩：无需真实模型。

    stream() 是生成器，yield 3 个写死的文本 chunk + 1 个 finish，
    对应 LlmRuntime.stream（packages/llm/llm/src/index.ts:913 → streamWithRegistration :917）与 StreamChunk 协议（types.ts:291）。
    [教学决策 G7] 真实版是 async 流；教学版用同步生成器，机制语义不变。
    """

    def stream(self, messages, system_prompt=None, **options):
        user_text = messages[-1]["content"] if messages else ""
        for piece in (
            f"收到问题「{user_text}」。",
            "这是一个最小闭环：",
            "chunk 逐条到达，拼成完整回复。",
        ):
            yield {"type": "text-delta", "text": piece}
        yield {"type": "finish", "stop_reason": "end-turn"}


# ---------- system-prompt：sections 组装 ----------


@dataclass
class PromptSection:
    """提示词片段，对应 PromptSection（packages/core/system-prompt/src/index.ts:53）。"""

    name: str
    order: int
    text: str


@dataclass
class PromptAssembly:
    """组装快照，对应 PromptAssembly（packages/core/system-prompt/src/index.ts:115）。

    [教学简化] 真实快照还含 variables/tools/contexts。
    """

    sections: list[PromptSection] = field(default_factory=list)


class SystemPromptService(Service):
    """ctx.systemPrompt 最小版，对应真实项目 SystemPromptService（packages/core/system-prompt/src/index.ts:338）。

    [教学简化] 真实版有 section/variable/tool/context 四种注册；此处只保留 section。
    """

    def __init__(self, ctx, config=None):
        super().__init__(ctx, "systemPrompt")
        self._sections: list[PromptSection] = []

    def section(self, name, text, order=0):
        """注册一个片段（index.ts:381）。注册即效应：卸载时自动移除。"""
        entry = PromptSection(name, order, text)

        def setup():
            self._sections.append(entry)
            return lambda: self._sections.remove(entry)

        return self.ctx.effect(setup, label=f"section:{name}")

    def assemble(self):
        """按 order 升序组装，对应 assemble（index.ts:467）。

        [教学简化] 真实版还会派发 waterfall('system-prompt/assemble')（:467）并合并 variables/tools。
        """
        assembly = PromptAssembly()
        assembly.sections = sorted(self._sections, key=lambda s: s.order)
        return assembly


def render_prompt(assembly):
    """sections → 过滤空 → \\n\\n 拼接，对应 renderPrompt（index.ts:212-217）。

    [教学简化] 省略 {{variable}} 插值。
    """
    return "\n\n".join(section.text for section in assembly.sections if section.text)


# ---------- assistant message：chunk 拼装终点 ----------


def create_assistant_message(text, source_event_seqs):
    """流式文本 → 不可变 assistant 消息，对应 createAssistantMessage（packages/llm/llm/src/message.ts:206-217）。

    [教学简化] 真实版接收 ContentBlock[] 与 source；此处接收已拼好的文本。
    [教学决策 G10] 省略 BlockAssembler 块归一化（assembler.ts:134-139），直接拼接文本。
    """
    return {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "source": {"kind": "model"},
        "source_event_seqs": tuple(source_event_seqs),
    }


# ---------- inbox：待处理队列 ----------


class Inbox:
    """待处理队列，对应 Inbox（packages/core/agent-loop/src/inbox.ts:25）。

    [教学简化] 只实现 next_turn；next_step（steer/inject，agent.ts:126/:130）本章不展开。
    """

    def __init__(self):
        self.next_turn: deque = deque()

    def enqueue(self, message):
        self.next_turn.append(message)

    def claim(self):
        """在 step 边界一次性取走全部待处理消息（inbox.ts claim）。"""
        claimed = list(self.next_turn)
        self.next_turn.clear()
        return claimed

    @property
    def has_pending(self):
        return bool(self.next_turn)


# ---------- ReactLoopAgent：反应式循环驱动器 ----------


class ReactLoopAgent:
    """反应式循环驱动器，对应 ReactLoopAgent（packages/core/agent-loop/src/agent.ts:64）。

    [教学简化] 真实代码的 phase 是判别联合（idle/running/...，agent.ts:217-221）；
    教学版用一个字符串字段表达 idle/running 两相。
    """

    def __init__(self, ctx, session, options=None):
        self.ctx = ctx
        self.session = session
        self.options = options or {}
        self.inbox = Inbox()
        self.phase = "idle"
        self._wake_requested = False

    # -- 入队口（agent.ts:113-132；steer/inject 本章省略） --

    def send(self, message, wakeup=True):
        """入队 + 唤醒（agent.ts:113-118）。

        [教学简化] 省略 wakingAfterAbort 分支（agent.ts:114-117）。
        """
        if isinstance(message, str):
            message = {"role": "user", "content": message}
        self.inbox.enqueue(message)
        if wakeup:
            self.wake_driver()

    def followup(self, message):
        """用户追问：入 next_turn 并立即唤醒（agent.ts:121）。"""
        self.send(message, wakeup=True)

    # -- 驱动循环 --

    def wake_driver(self):
        """进入 running 相位的唯一入口（agent.ts:172）。"""
        if self.phase == "running":
            self._wake_requested = True
            return
        self.phase = "running"
        try:
            self.kick()
        finally:
            self.phase = "idle"
            # 真实代码 kick finally：wakeRequested 且 inbox.hasPending → 再 wakeDriver（agent.ts:215-222）
            if self._wake_requested and self.inbox.has_pending:
                self._wake_requested = False
                self.wake_driver()

    def kick(self):
        """kick() → while (await turn()) {}（agent.ts:210-223）。

        [教学简化] 真实代码 catch 在驱动边界吞掉已上报错误（agent.ts:212-213）。
        """
        while self.turn():
            pass

    def turn(self):
        """一个 turn：turn/start → pre_step → step/start → user/message → step → step/end
        → turn-stopping → turn/end（turn() agent.ts:246-330）。

        返回是否续跑。[教学决策 G5] 判定取「inbox 是否仍有 pending」。
        """
        if not self.inbox.has_pending:
            return False
        self.session.append("turn/start", {})
        snapshot = self.pre_step()
        # 真实源码顺序：step/start（:279）→ 逐条 user/message（:283）→ step() 调用（:287）（agent.ts:246-330 内）
        self.session.append("step/start", {})
        for message in snapshot["claimed"]:
            self.session.append("user/message", {"message": message})
        self.step(snapshot)
        self.session.append("step/end", {})
        # turn-stopping：真实代码在无 next-step 待处理时 serial('agent/turn-stopping') 后 break（agent.ts:296）
        stop = self.ctx.serial("agent/turn-stopping", self)
        self.session.append("turn/end", {"reason": stop or "completed"})
        return self.inbox.has_pending

    def pre_step(self):
        """step 前奏：取消息 → 组装 prompt → waterfall('agent/pre-step') 放行拦截（agent.ts:225-243）。"""
        assembly = self.ctx.systemPrompt.assemble()
        snapshot = {
            "claimed": self.inbox.claim(),
            "assembly": assembly,
            "additional_contexts": [],
        }
        return self.ctx.waterfall("agent/pre-step", snapshot)

    def step(self, snapshot):
        """一次模型调用（agent.ts:332-401）。

        [教学简化] 真实代码一个 turn 内可循环多个 step（tool-call 驱动）；本章无工具，一 turn 一 step。
        """
        claimed = snapshot["claimed"]
        assembly = snapshot["assembly"]
        system_text = render_prompt(assembly)
        # [教学简化] 真实代码经 deriveMessages 派生模型消息（session/src/index.ts:726-747）
        messages = [{"role": "user", "content": text} for text in snapshot["additional_contexts"]]
        messages += [{"role": "user", "content": m["content"]} for m in claimed]

        text_parts, chunk_seqs = [], []
        for chunk in self.ctx.llm.stream(messages, system_prompt=system_text):
            if chunk["type"] == "text-delta":
                event = self.session.append("assistant/chunk", {"chunk": chunk})
                chunk_seqs.append(event.seq)
                text_parts.append(chunk["text"])
        message = create_assistant_message("".join(text_parts), chunk_seqs)
        self.session.append("assistant/message", {"message": message})
        return "completed"  # 无 tool-call → 本轮结束（agent.ts:332-401）


# ---------- AgentLoop：被容器装配出来的服务 ----------


class AgentLoop(Service):
    """agent-loop 服务，对应 AgentLoop（packages/core/agent-loop/src/index.ts:296）。

    inject 声明对应 static inject（index.ts:296-297）：sessions/llm 都提供后才加载。
    [教学简化] 真实版 create() 与 publish() 两段（create :589；publish :556-570：
    sessions.enter → agents.enter → announce → 广播）；教学版合并为一个 create()。
    """

    inject = ["sessions", "llm"]

    def __init__(self, ctx, config=None):
        super().__init__(ctx, "agent_loop")  # [教学决策 G2] 教学服务名 agent_loop（素材未定，见 notes §11）
        self.agents = {}

    def create(self, **options):
        """create + publish（[教学简化] 两段合一）：建 Session → 建 ReactLoopAgent → 登记 → 广播 agent/session-start。"""
        session = self.ctx.sessions.create()
        agent = ReactLoopAgent(self.ctx, session, options)
        self.agents[session.id] = agent
        # 真实 publish() 在 sessions.enter/agents.enter/announce 后广播 agent/session-start（index.ts:556-570）
        self.ctx.emit("agent/session-start", {"session_id": session.id, "agent": agent})
        return agent
