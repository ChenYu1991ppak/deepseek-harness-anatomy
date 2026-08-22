"""第 5 章教学重构：capability seam 三角色 —— Consumer（tool-bash）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（packages/tool/tool-bash/src/index.ts + render.ts）：
- name / inject           ↔ index.ts:30-31（inject=['tools','shell','systemPrompt','shellEnv']）
- apply(ctx, config)      ↔ index.ts:190（注入 guidance section + 注册 bash 工具）
- ctx.systemPrompt.section ↔ index.ts:236（注入跨调用 guidance「检查 [exit code: N] 标记」）
- ctx.tools.register      ↔ index.ts:242（注册第 4 章的 bash 工具）
- execute(args, exec)     ↔ index.ts:330（核心编排：先 resolve 后 run/start）
- 前台调用                 ↔ index.ts:380（ctx.shell.run(ctx.shell.resolve({...request, signal}))）
- renderResult            ↔ render.ts:28（尾部追加 [exit code: N] / [killed by signal: X] marker）

[教学决策] 第 4 章 ToolDefinition.execute 退化为 execute(args) -> str（tools.py:80-91）。
本章 tool-bash 作为 tools 管线里的一个 tool body，execute 沿用该退化签名：
面向 ctx.shell.resolve/run/start 三个方法调用，返回渲染后的文本。
（真实版 execute(args, exec) 里 exec 携带 signal，用于取消链；教学版省略。）
"""
from __future__ import annotations

import os
import sys

# 复用第 1 章 Context、第 4 章 ToolDefinition。
_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
_CH04 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch04", "code"))
for _p in (_CH01, _CH04):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Context  # noqa: E402
from tools import ToolDefinition  # noqa: E402
from shell import ShellExecRequest  # noqa: E402

__all__ = ["name", "inject", "apply", "render_result"]

# 三角色之「消费者」：只面向 ctx.shell 抽象，不 import 任何具体 provider。
name = "tool-bash"
inject = ["shell", "systemPrompt", "tools"]


def render_result(result) -> str:
    """渲染 ShellRunResult：尾部追加共享 marker（render.ts:58）。

    marker 即 shell seam 的「退出状态契约」：消费者负责写，parse_exit_status 负责读。
    [教学决策] 超时写成独立的 [timed out after Nms] marker，与 parse_exit_status 的超时分支对应。
    """
    out = result.stdout
    if result.stderr:
        out += ("\n" if out else "") + result.stderr
    if result.timed_out:
        # [教学决策] 超时渲染为独立 marker，保证 parse_exit_status 能回读（写读闭环）
        out += f"\n[timed out after {result.timeout_ms}ms]"
    elif result.signal:
        out += f"\n[killed by signal: {result.signal}]"
    elif result.aborted:
        out += "\n[aborted]"
    else:
        out += f"\n[exit code: {result.exit_code}]"
    return out


def _guidance_text() -> str:
    """告诉模型如何读懂退出状态标记（tool-bash/src/guidance.ts 的简写）。"""
    return (
        "bash 工具：执行 shell 命令。"
        "命令输出尾部会追加 [exit code: N] 标记，N=0 表示成功，非零表示失败；"
        "[killed by signal: X] 表示被信号终止。请据此判断命令是否成功。"
    )


def apply(ctx: Context, config=None):
    """tool-bash 插件体（index.ts:27）：注入 guidance + 注册 bash 工具。

    消费者职责只有两点：
    1. 往 system-prompt 注入一段「如何读退出标记」的指导；
    2. 把一个工具体注册进 ctx.tools（第 4 章管线），该工具体内部只调 ctx.shell 三个方法。
    """
    # 1. guidance：教育模型读 [exit code: N] 标记（index.ts:28-30）
    ctx.systemPrompt.section("tool:bash", _guidance_text(), order=20)

    # 2. 注册 bash 工具：execute 即第 4 章 tools 管线里的一个 tool body（index.ts:120）
    def bash_tool(args):
        # 面向抽象：resolve → run，消费者不认识 LocalBashExecutor（index.ts:120-135）
        request = ShellExecRequest(
            command=args["command"],
            workdir=args.get("workdir"),
            timeout_ms=args.get("timeout_ms"),
        )
        spec = ctx.shell.resolve(request)
        if args.get("run_in_background"):
            proc = ctx.shell.start(spec)
            return f"[started in background, status={proc.status.value}]"
        result = ctx.shell.run(spec)
        return render_result(result)

    ctx.tools.register(
        ToolDefinition(
            name="bash",
            description="执行 bash 命令（shell seam 消费者）",
            parameters={"command": "string", "workdir": "string?", "timeout_ms": "int?"},
            execute=bash_tool,
        )
    )
