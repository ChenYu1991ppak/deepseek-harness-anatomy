"""第 8 章：循环侧的粘合——运行时上下文投影 + 完整版 pre_step。

对应 packages/core/agent-loop/src/agent.ts 的 preStep（:225-243）：
assemble → 渲染上下文快照 → runtimeContext.project 去重 → pre-step waterfall。

本模块继承 ch02/agent_loop.py 的 ReactLoopAgent，只替换 pre_step/step 两处：
- ch02 桩的 assemble() 返回段对象列表，render_prompt 直接 join；
- 本章完整版 assemble() 返回 PromptAssembly，渲染走 system_prompt.render_prompt，
  且工具 schema 以并列字段传给模型调用方。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 跨章复用第 2 章的循环骨架（ReactLoopAgent / create_assistant_message）
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ch02" / "code"))

from agent_loop import ReactLoopAgent, create_assistant_message  # noqa: E402

# 本章的渲染函数（机制 A 的出口）
from system_prompt import render_context_snapshot, render_prompt  # noqa: E402


class RuntimeContext:
    """运行时上下文快照去重器，对应 agent.ts:233 runtimeContext.project。

    快照内容变化时才产生新的注入文本；内容不变返回 None，
    避免同一份上下文在每步重复注入（真实版按投影身份比较，教学版按文本比较）。
    """

    def __init__(self):
        self._last_snapshot = None

    def project(self, snapshot_text: str):
        """投影一份快照：变化返回文本，未变化返回 None。"""
        if not snapshot_text or snapshot_text == self._last_snapshot:
            return None
        self._last_snapshot = snapshot_text
        return snapshot_text


class PromptAwareAgent(ReactLoopAgent):
    """接上完整版 SystemPrompt 的循环代理。

    pre_step 对应 agent.ts:225-243 preStep：
    1. assemble() 得到完整 PromptAssembly（含 sections/contexts/tools）；
    2. 机制 A：渲染上下文快照，经 RuntimeContext.project 去重后注入；
    3. pre-step waterfall：机制 B 插件在此追加各自的注入文本；
    4. step 把 system 文本与工具 schema（并列字段）一起交给模型。
    """

    def __init__(self, ctx, session, runtime_context, options=None):
        super().__init__(ctx, session, options)
        self.runtime_context = runtime_context
        # ch02 的 turn/step 是方法不是计数器，这里自行记录循环位置
        self._turn_count = 0
        self._step_count = 0

    def send(self, message, wakeup=True):
        """入队并可选唤醒（行为：唤醒即开启新 turn，计数加一）。"""
        if wakeup:
            self._turn_count += 1
        super().send(message, wakeup)

    def pre_step(self):
        """组装本步决策快照（行为：替换 ch02 桩版 pre_step）。"""
        self._step_count += 1
        # 组装完整 PromptAssembly（行为），替换 ch02 桩的 assemble（概念：完整版产物）
        assembly = self.ctx.systemPrompt.assemble()
        snapshot = {
            # 教学版单次唤醒只跑一个 turn；真实版 turn/step 来自循环位置
            "turn": max(self._turn_count, 1),
            "step": self._step_count,
            "claimed": self.inbox.claim(),
            "assembly": assembly,
            "additional_contexts": [],
        }
        # 机制 A：上下文快照只在内容变化时注入（对应 agent.ts:232-238）
        context_text = self.runtime_context.project(render_context_snapshot(assembly))
        if context_text is not None:
            snapshot["additional_contexts"].append(context_text)
        # pre-step waterfall：机制 B 插件在此追加注入文本（对应 agent.ts:241）
        return self.ctx.waterfall("agent/pre-step", snapshot)

    def step(self, snapshot):
        """执行一步（行为：替换 ch02 桩版 step，透传工具 schema 并列字段）。"""
        assembly = snapshot["assembly"]
        # system 文本只含 sections 渲染结果，不含工具 schema（概念：两分组成）
        system_text = render_prompt(assembly)
        # 注入文本以 user 消息排在 claimed 消息之前（ch02 step 的既有约定）
        messages = [{"role": "user", "content": text}
                    for text in snapshot["additional_contexts"]]
        messages += [{"role": "user", "content": m["content"]}
                     for m in snapshot["claimed"]]
        text_parts = []
        # 工具 schema 作为并列字段随调用传出（对应 wireSchemas 的真实落点）
        for chunk in self.ctx.llm.stream(
            messages, system_prompt=system_text, tools=assembly.tools
        ):
            if chunk["type"] == "text-delta":
                self.session.append("assistant/chunk", {"chunk": chunk})
                text_parts.append(chunk["text"])
        message = create_assistant_message("".join(text_parts), [])
        self.session.append("assistant/message", {"message": message})
        return "completed"
