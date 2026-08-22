"""第 2 章演示：最小 agent-loop 闭环（一条消息进，一条回复出）。

仅标准库，Python 3.10+。运行：python3 main.py

演示环节（聚焦 agent-loop 装配闭环，不复演第 1 章机制教学段）：
1. 建容器
2. provide sessions + llm
3. 注册 identity 提示词片段（section 注册即效应）
4. plugin 加载 AgentLoop（inject 满足才加载）
5. 注册监听器：session/event 打印 + session-start 打印 + pre-step 注入
6. followup 跑一轮闭环：事件流 + 拼出 assistant 消息
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
for _p in (_HERE, _CH01):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Context  # noqa: E402
from agent_loop import AgentLoop, LlmStub, Sessions, SystemPromptService, render_prompt  # noqa: E402


def main():
    # -- 1. 建容器 --
    print("== 1. 建容器 ==")
    ctx = Context()

    # -- 2. provide sessions + llm --
    print("\n== 2. provide sessions + llm ==")
    Sessions(ctx)  # Service 子类：构造即注册为 ctx.sessions
    ctx.provide("llm", LlmStub())  # 普通对象：显式 provide
    print(f"  ctx.sessions -> {type(ctx.sessions).__name__}")
    print(f"  ctx.llm      -> {type(ctx.llm).__name__}")

    # -- 3. 注册 identity 提示词片段 --
    print("\n== 3. 注册 identity 提示词片段 ==")
    SystemPromptService(ctx)  # 构造即注册为 ctx.systemPrompt
    ctx.systemPrompt.section("identity", "你是一个最小教学 agent，只用标准库，没有工具。", order=0)
    print(f"  组装结果: {render_prompt(ctx.systemPrompt.assemble())}")

    # -- 4. plugin 加载 AgentLoop（inject 满足才加载） --
    print("\n== 4. plugin 加载 AgentLoop（inject 满足才加载） ==")
    fiber = ctx.plugin(AgentLoop)  # inject = ["sessions", "llm"] 已备齐 → 立即加载
    print(f"  AgentLoop inject={AgentLoop.inject} -> state={fiber.state}")
    print(f"  ctx.agent_loop -> {type(ctx.agent_loop).__name__}")

    # -- 5. 注册监听器 --
    print("\n== 5. 注册监听器 ==")
    ctx.on("session/event", lambda ev: print(f"  [event] seq={ev.seq} {ev.type}"))
    ctx.on("agent/session-start", lambda info: print(f"  [agent/session-start] {info['session_id']}"))

    def time_context(snapshot):
        # waterfall('agent/pre-step') 监听器：每次 step 前注入时间上下文（每次现算）
        print("  [pre-step] 注入时间上下文")
        snapshot["additional_contexts"].append("当前时间：2026-08-18")
        return snapshot

    ctx.on("agent/pre-step", time_context)

    # -- 6. followup 跑一轮闭环 --
    print("\n== 6. followup 跑一轮闭环 ==")
    agent = ctx.agent_loop.create()
    print(f"  session={agent.session.id}")
    agent.followup("什么是 Cordis？")

    print("\n  -- session 日志（append-only，seq == len(log)） --")
    for ev in agent.session.log:
        print(f"  [seq={ev.seq}] {ev.type}: {ev.payload}")
    assistant = next(
        ev.payload["message"]["content"][0]["text"]
        for ev in agent.session.log
        if ev.type == "assistant/message"
    )
    print(f"\n  拼出的 assistant 消息: {assistant}")


if __name__ == "__main__":
    main()
