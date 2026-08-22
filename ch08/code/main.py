"""第 8 章演示入口：system-prompt 组装与 context。

运行：python3 ch08/code/main.py

经 sys.path 复用（不复制不修改前章文件）：
- ch01/cordis.py      Context / Service
- ch02/agent_loop.py  Sessions / ReactLoopAgent / create_assistant_message
- ch04/tools.py       ToolRuntime / ToolDefinition

本章新增：system_prompt.py（system-prompt 服务完整版）、
context_plugins.py（机制 B 三插件）、prompt_agent.py（循环侧粘合）。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# 经 sys.path 复用前章模块（与 ch04/main.py 同款做法）
CHAPTER_ROOT = Path(__file__).resolve().parent
for _chapter in ("ch01", "ch02", "ch04"):
    _path = str(CHAPTER_ROOT.parents[1] / _chapter / "code")
    if _path not in sys.path:
        sys.path.insert(0, _path)
# 本章目录入 sys.path，prompt_agent.py 才能 import 本章模块
if str(CHAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(CHAPTER_ROOT))

from cordis import Context  # noqa: E402
from agent_loop import Sessions  # noqa: E402
from tools import ToolDefinition, ToolRuntime  # noqa: E402

from system_prompt import (  # noqa: E402
    SystemPrompt,
    render_context_snapshot,
    render_prompt,
)
from context_plugins import (  # noqa: E402
    AgentInstructionsPlugin,
    SessionReferenceResolver,
    TimeContextPlugin,
)
from prompt_agent import PromptAwareAgent, RuntimeContext  # noqa: E402


def demo_default_sections(ctx):
    print("== 段落 1：容器与 systemPrompt 服务——两个默认段 ==")
    # 组装一次 PromptAssembly（行为），观察构造器自动注册的两个默认段
    assembly = ctx.systemPrompt.assemble()
    print("构造器注册的段（assemble 顺序）：")
    for name, text in assembly.sections:
        print(f"  - {name}: {text}")
    print()
    print("render_prompt 渲染结果：")
    print(render_prompt(assembly))
    print()


def demo_ordered_sections(ctx):
    print("== 段落 2：插件注册有序段——assemble 合并排序 ==")
    # 模拟插件注册四个段：注册次序任意，order 决定最终位置
    ctx.systemPrompt.section(
        "tools:guidance", "调用工具前先确认路径存在。", order=100)
    ctx.systemPrompt.section(
        "workspace:conventions", "项目代码在 chNN/code/ 下，注释用中文。", order=20)
    dispose_safety = ctx.systemPrompt.section(
        "safety:policy", "不要输出任何密钥或凭据信息。", order=50)
    # 提示词变量：段文本里的 {{review_focus}} 在渲染时插值
    ctx.systemPrompt.variable(
        "review_focus", lambda c: "错误处理与边界条件")
    ctx.systemPrompt.section(
        "review:focus", "本次评审重点：{{review_focus}}。", order=30)

    print("注册顺序：tools:guidance(order=100) → workspace:conventions(order=20)")
    print("          → safety:policy(order=50) → review:focus(order=30)")
    print("render_prompt 渲染结果（段按 order 升序排列）：")
    print(render_prompt(ctx.systemPrompt.assemble()))
    print()
    # 注册可逆：调用 disposer 注销段（可逆性来自第 1 章的 ctx.effect）
    dispose_safety()
    print("注销 safety:policy 后再渲染（该段消失）：")
    print(render_prompt(ctx.systemPrompt.assemble()))
    print()

    # 再次注册同层同名段触发 ValueError（行为：try 捕获并打印），
    # 对照反例问题「重复注册无从检测」——这里重复可检测
    try:
        ctx.systemPrompt.section(
            "tools:guidance", "这条注册会被拒绝。", order=99)
    except ValueError as error:
        print(f"重复注册抛错：{error}")
    print()


def demo_tools_field(ctx):
    print("== 段落 3：工具 schema 是并列独立字段 ==")
    # 复用第 4 章的 ToolRuntime：注册一个工具
    ToolRuntime(ctx)
    ctx.tools.register(ToolDefinition(
        name="read_file",
        description="读取文件内容",
        parameters={"path": "文件路径"},
        execute=lambda args: f"[{args['path']} 的内容]",
    ))
    # 把 wireSchemas 接到 systemPrompt.tools()——第 4 章 wireSchemas 的真实落点
    ctx.systemPrompt.tools(lambda c: ctx.tools.wire_schemas())

    assembly = ctx.systemPrompt.assemble()
    print("assembly.tools（并列字段，独立传给模型调用方）：")
    print(f"  {assembly.tools}")
    system_text = render_prompt(assembly)
    print()
    print("render_prompt 文本不含工具 schema：")
    print(f"  '读取文件内容' 在 system 文本中? {'读取文件内容' in system_text}")
    print(f"  'parameters' 在 system 文本中? {'parameters' in system_text}")
    print()


def demo_context_snapshot(ctx):
    print("== 段落 4：机制 A——context() → 运行时上下文快照 ==")
    # 注册两条运行时上下文段：文本也可以是动态提供者
    ctx.systemPrompt.context(
        "workspace:state",
        lambda c: "工作目录: ch08/code\nGit 状态: working tree clean",
        order=10)
    ctx.systemPrompt.context(
        "active:plan", "当前计划执行到第 3 步：撰写 system-prompt 章节。", order=20)

    assembly = ctx.systemPrompt.assemble()
    print("render_context_snapshot 渲染结果（带固定前缀）：")
    print(render_context_snapshot(assembly))
    print()
    # RuntimeContext.project 去重：快照内容变化才产生注入文本
    runtime_context = RuntimeContext()
    first = runtime_context.project(
        render_context_snapshot(ctx.systemPrompt.assemble()))
    print(f"第一次投影：{'产生注入文本' if first is not None else 'None'}")
    second = runtime_context.project(
        render_context_snapshot(ctx.systemPrompt.assemble()))
    print(f"第二次投影（内容未变）：{second}")
    print()


class RecordingLlm:
    """记录入参的 LLM 桩：用于验证模型最终收到了什么。"""

    def __init__(self):
        self.received = None

    def stream(self, messages, system_prompt=None, **options):
        # 记录本次调用的全部入参（行为），再返回固定回复流
        self.received = {
            "system_prompt": system_prompt,
            "tools": options.get("tools"),
            "messages": messages,
        }
        yield {"type": "text-delta",
               "text": "收到：先查看 sample.txt，再继续 s-001 的未完成工作。"}
        yield {"type": "finish", "stop_reason": "end-turn"}


def demo_full_loop():
    print("== 段落 5：机制 B——三个插件在完整循环里注入 user 消息 ==")
    # 全新容器：完整装配（循环 + 完整版 SystemPrompt + 三个 context 插件）
    ctx = Context()
    Sessions(ctx)
    llm = RecordingLlm()
    ctx.provide("llm", llm)

    SystemPrompt(ctx, persona="你是 harness 演示助手。")
    ToolRuntime(ctx)
    ctx.tools.register(ToolDefinition(
        name="read_file",
        description="读取文件内容",
        parameters={"path": "文件路径"},
        execute=lambda args: f"[{args['path']} 的内容]",
    ))
    ctx.systemPrompt.tools(lambda c: ctx.tools.wire_schemas())
    # 机制 A：注册一条运行时上下文段
    ctx.systemPrompt.context(
        "workspace:state", "工作目录: ch08/code", order=10)

    # 机制 B：两个 pre-step 插件 + 一个入队期解析器
    AgentInstructionsPlugin(
        ctx,
        baseline_instructions={
            "identity": "你是编码助手，用中文回复。",
            "formatting": "代码标识符保持英文。",
        },
        # 项目指令 shadow 基线同名块：formatting 被项目级覆盖
        project_instructions={
            "formatting": "代码标识符保持英文，注释用中文。",
        },
    )
    # 固定时钟：保证每次运行输出可复现（真实版用 Date.now 采样）
    TimeContextPlugin(ctx, clock=lambda: datetime(2026, 8, 19, 10, 30, 0))
    SessionReferenceResolver(ctx, session_store={
        "s-001": "上一次会话：初始化项目，创建了 ch08/code/main.py",
    })

    runtime_context = RuntimeContext()
    session = ctx.sessions.create()
    agent = PromptAwareAgent(ctx, session, runtime_context)

    # 宿主在 enqueue 前解析跨会话引用（session-reference 不是 pre-step 插件）
    user_text = "继续上次的工作 session:s-001，并查看 sample.txt 的内容"
    content, reference_context = ctx.sessionReference.prepare(user_text)
    if reference_context is not None:
        agent.send(reference_context, wakeup=False)  # 只入队，不唤醒
    agent.send(content)  # 入队并唤醒，一个 turn 一并处理两条消息

    print("模型收到的内容：")
    print("  [system 提示词]（renderPrompt 渲染，不含工具 schema）：")
    for line in llm.received["system_prompt"].splitlines():
        print(f"    {line}")
    print("  [工具 schema]（并列字段）：")
    print(f"    {llm.received['tools']}")
    print("  [消息历史]（注入的 user 消息 + 原始 user 消息）：")
    for message in llm.received["messages"]:
        # 每条消息只打印首行预览，避免快照 JSON 刷屏
        preview = message["content"].splitlines()[0]
        if len(preview) > 60:
            preview = preview[:60] + "..."
        print(f"    [{message['role']}] {preview}")
    # 助手回复记录在 session 事件日志里（行为：从日志取 assistant/message 事件）
    reply = next(e for e in session.log if e.type == "assistant/message")
    print("  [助手回复]（session 事件 assistant/message）：")
    print(f"    {reply.payload['message']['content']}")
    print()


def main():
    # 段落 1–4 共用一个容器，演示增量装配；段落 5 全新容器跑完整循环
    ctx = Context()
    SystemPrompt(ctx, persona="你是严谨的代码评审助手，用中文回答。")

    demo_default_sections(ctx)
    demo_ordered_sections(ctx)
    demo_tools_field(ctx)
    demo_context_snapshot(ctx)
    demo_full_loop()


if __name__ == "__main__":
    main()
