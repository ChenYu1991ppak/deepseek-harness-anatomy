"""第 2 章教学重构：最小 agent-loop 闭环（session / llm stub / system-prompt / agent-loop）。

仅标准库，Python 3.10+。

源码对应（上游仓库根 /Users/chenyu/PycharmProjects/deepseek-harness，HEAD 8bd0733）：
- Session/Sessions          ↔ packages/core/session/src/index.ts:525（Session；append :806-837、deepFreeze :827、
                              commit 广播 :892-912）/ packages/core/session/src/index.ts:1064（SessionStore，即
                              ctx.sessions；create :1126、prepare :1158、enter :1231、announce :1287）
- LlmStub                   ↔ packages/llm/llm/src/index.ts:1170（LlmRuntime.stream）→
                              packages/llm/llm/src/index.ts:1174（streamWithRegistration，派发 llm/stream waterfall）
                              + packages/llm/llm/src/types.ts:496-508（StreamChunk）；LlmStub 是纯教学桩，源码无此类
- SystemPromptService/render_prompt ↔ packages/core/system-prompt/src/index.ts:435（section :484、assemble :620、
                              renderPrompt :296-301、system-prompt/assemble waterfall :683）
- AgentLoop                 ↔ packages/core/agent-loop/src/index.ts:290（static inject :292、create :667、
                              initializeAgent :464、发布顺序 :626-637）
- ReactLoopAgent/Inbox      ↔ packages/core/agent-loop/src/agent.ts:112 / packages/core/agent-loop/src/inbox.ts:73
- create_assistant_message  ↔ packages/llm/llm/src/message.ts:260-271
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
    "LlmStub",
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
    """append-only 事件条目，对应 SessionEvent（packages/core/session/src/types.ts:501-524）。

    [教学简化] 真实事件字段是 type/seq/time/data，无 session_id，且可带 surface 元数据；
    frozen 对应 deepFreeze（packages/util/values/src/index.ts:195）。
    """

    session_id: str
    seq: int
    type: str
    payload: dict


class Session:
    """append-only 事件日志，对应 Session（packages/core/session/src/index.ts:525）。"""

    def __init__(self, ctx, session_id):
        self.ctx = ctx
        self.id = session_id
        self.log: list[SessionEvent] = []

    def append(self, type, payload):
        """追加一条事件：seq == len(log)（packages/core/session/src/index.ts:806-837），
        事件不可变（deepFreeze，packages/core/session/src/index.ts:827）。

        [教学简化] 省略 JSON 校验（append 内）与 surface/surfaceOp（第 10 章机制，G9）。
        """
        event = SessionEvent(self.id, len(self.log), type, payload)
        self.log.append(event)
        self.ctx.emit("session/event", event)
        return event


class Sessions(Service):
    """会话注册表服务 ctx.sessions，对应 SessionStore（packages/core/session/src/index.ts:1064）。

    [教学简化] 真实 create() 走 prepare（:1158）→ enter（:1231）→ announce（:1287）三步；此处直接创建。
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
    对应 LlmRuntime.stream（packages/llm/llm/src/index.ts:1170 → streamWithRegistration
    packages/llm/llm/src/index.ts:1174）与 StreamChunk 协议（packages/llm/llm/src/types.ts:496-508）。
    [教学简化] 真实 finish chunk 的形状是 {type: 'finish', reason}；教学版用 stop_reason 简写。
    [教学决策 G7] 真实版是 async 流；教学版用同步生成器，机制语义不变。
    """

    def stream(self, messages, **options):
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
    """组装快照，对应 PromptAssembly（packages/core/system-prompt/src/index.ts:132）。

    [教学简化] 真实快照还含 variables/tools/contexts。
    """

    sections: list[PromptSection] = field(default_factory=list)


class SystemPromptService(Service):
    """ctx.systemPrompt 最小版，对应真实项目 SystemPrompt（packages/core/system-prompt/src/index.ts:435）。

    [教学简化] 真实版有 section/context/tools/variable 四种注册（:484/:519/:551/:567）；此处只保留 section。
    """

    def __init__(self, ctx, config=None):
        super().__init__(ctx, "systemPrompt")
        self._sections: list[PromptSection] = []

    def section(self, name, text, order=0):
        """注册一个片段（packages/core/system-prompt/src/index.ts:484）。注册即效应：卸载时自动移除。"""
        entry = PromptSection(name, order, text)

        def setup():
            self._sections.append(entry)
            return lambda: self._sections.remove(entry)

        return self.ctx.effect(setup)

    def assemble(self):
        """按 order 升序组装，对应 assemble（packages/core/system-prompt/src/index.ts:620）。

        [教学简化] 真实版还会派发 waterfall('system-prompt/assemble')
        （packages/core/system-prompt/src/index.ts:683）并合并 variables/tools。
        """
        assembly = PromptAssembly()
        assembly.sections = sorted(self._sections, key=lambda s: s.order)
        return assembly


def render_prompt(assembly):
    """sections → 过滤空 → \\n\\n 拼接，对应 renderPrompt（packages/core/system-prompt/src/index.ts:296-301）。

    [教学简化] 省略 {{variable}} 插值。
    """
    return "\n\n".join(section.text for section in assembly.sections if section.text)


# ---------- assistant message：chunk 拼装终点 ----------


def create_assistant_message(text, source_event_seqs):
    """流式文本 → 不可变 assistant 消息，对应 createAssistantMessage（packages/llm/llm/src/message.ts:260-271）。

    [教学简化] 真实版接收 ContentBlock[] 与 source；此处接收已拼好的文本。
    [教学决策 G10] 省略 BlockAssembler 块归一化（assembler.ts:158-163），直接拼接文本。
    """
    return {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "source": {"kind": "model"},
        "source_event_seqs": tuple(source_event_seqs),
    }


# ---------- inbox：待处理队列 ----------


class Inbox:
    """待处理队列，对应 ReactLoopInbox（packages/core/agent-loop/src/inbox.ts:73）。

    [教学简化] 只实现 next-turn 一条队列；next-step（steer/inject，
    packages/core/agent-loop/src/agent.ts:183/:187）本章不展开。
    """

    def __init__(self):
        self.next_turn: deque = deque()

    def enqueue(self, message):
        self.next_turn.append(message)

    def claim(self):
        """在 step 边界一次性取走整批待处理消息（packages/core/agent-loop/src/inbox.ts:108）。"""
        claimed = list(self.next_turn)
        self.next_turn.clear()
        return claimed

    @property
    def has_pending(self):
        return bool(self.next_turn)


# ---------- ReactLoopAgent：反应式循环驱动器 ----------


class ReactLoopAgent:
    """反应式循环驱动器，对应 ReactLoopAgent（packages/core/agent-loop/src/agent.ts:112）。

    [教学简化] 真实代码的 phase 是判别联合 idle/maintenance/running
    （packages/core/agent-loop/src/agent.ts:53）；
    教学版用一个字符串字段表达 idle/running 两相。
    """

    def __init__(self, ctx, session, options=None):
        self.ctx = ctx
        self.session = session
        self.options = options or {}
        self.inbox = Inbox()
        self.phase = "idle"
        self._wake_requested = False

    # -- 入队口（packages/core/agent-loop/src/agent.ts:170-189；steer/inject 本章省略） --

    def send(self, message, wakeup=True):
        """入队 + 唤醒（packages/core/agent-loop/src/agent.ts:170-177）。

        [教学简化] 省略 wakingAfterAbort 分支（packages/core/agent-loop/src/agent.ts:171-174）：
        真实 send() 还带 target 参数，入队走 inbox.splice。
        """
        if isinstance(message, str):
            message = {"role": "user", "content": message}
        self.inbox.enqueue(message)
        if wakeup:
            self.wake_driver()

    def followup(self, message):
        """用户追问：入 next-turn 并立即唤醒（packages/core/agent-loop/src/agent.ts:179-181）。"""
        self.send(message, wakeup=True)

    # -- 驱动循环 --

    def wake_driver(self):
        """进入 running 相位的唯一入口（packages/core/agent-loop/src/agent.ts:230）。"""
        if self.phase == "running":
            self._wake_requested = True
            return
        self.phase = "running"
        try:
            self.kick()
        finally:
            self.phase = "idle"
            # 真实代码 kick finally：wakeRequested 且 inbox.hasPending → 再 wakeDriver
            # （packages/core/agent-loop/src/agent.ts:275-279）
            if self._wake_requested and self.inbox.has_pending:
                self._wake_requested = False
                self.wake_driver()

    def kick(self):
        """kick() → while (await turn()) {}（packages/core/agent-loop/src/agent.ts:268-281）。

        [教学简化] 真实代码 catch 在驱动边界吞掉错误（packages/core/agent-loop/src/agent.ts:271-272），
        finally 里把 phase 归零并在需要时再唤醒。
        """
        while self.turn():
            pass

    def turn(self):
        """一个 turn：turn/start → pre_step → step/start → user/message → step → step/end
        → turn-stopping → turn/end（turn() packages/core/agent-loop/src/agent.ts:327-433）。

        返回是否续跑。[教学决策 G5] 判定取「inbox 是否仍有 pending」。
        """
        if not self.inbox.has_pending:
            return False
        self.session.append("turn/start", {})
        snapshot = self.pre_step()
        # 真实源码顺序：step/start（:361）→ 逐条 user/message（:467-477）→ step() 调用（:370）（agent.ts:344-401 内）
        self.session.append("step/start", {})
        for message in snapshot["claimed"]:
            self.session.append("user/message", {"message": message})
        self.step(snapshot)
        self.session.append("step/end", {})
        # turn-stopping：真实代码在无 next-step 待处理时 serial('agent/turn-stopping') 后 break
        # （packages/core/agent-loop/src/agent.ts:395-399）
        stop = self.ctx.serial("agent/turn-stopping", self)
        self.session.append("turn/end", {"reason": stop or "completed"})
        return self.inbox.has_pending

    def pre_step(self):
        """step 前奏：取消息 → 组装 prompt → waterfall('agent/pre-step') 放行拦截
        （packages/core/agent-loop/src/agent.ts:283-317）。"""
        assembly = self.ctx.systemPrompt.assemble()
        snapshot = {
            "claimed": self.inbox.claim(),
            "assembly": assembly,
            "additional_contexts": [],
        }
        return self.ctx.waterfall("agent/pre-step", snapshot)

    def step(self, snapshot):
        """一次模型调用（packages/core/agent-loop/src/agent.ts:435-610）。

        [教学简化] 真实代码一个 turn 内可循环多个 step（tool-call 驱动）；本章无工具，一 turn 一 step。
        """
        claimed = snapshot["claimed"]
        assembly = snapshot["assembly"]
        system_text = render_prompt(assembly)
        # [教学简化] 真实代码经 deriveMessages 派生模型消息（packages/core/session/src/index.ts:999-1020）
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
        return "completed"  # 无 tool-call → 本轮结束（packages/core/agent-loop/src/agent.ts:435-610）


# ---------- AgentLoop：被容器装配出来的服务 ----------


class AgentLoop(Service):
    """agent-loop 服务，对应 AgentLoop（packages/core/agent-loop/src/index.ts:290）。

    inject 声明对应 static inject（packages/core/agent-loop/src/index.ts:292）：真实版要求
    agents/sessions/llm/tools/systemPrompt/sessionProjections 六个依赖；教学版只取两个。
    [教学简化] 真实版 create()（:667）→ initializeAgent()（:464）两段，发布时
    sessions.enter → sessions.announce → agents.enter → agents.announce；教学版合并为一个 create()。
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
        # 真实 initializeAgent 在 sessions.enter/announce 与 agents.enter/announce 之后才返回 agent
        # （packages/core/agent-loop/src/index.ts:626-637）
        self.ctx.emit("agent/session-start", {"session_id": session.id, "agent": agent})
        return agent
