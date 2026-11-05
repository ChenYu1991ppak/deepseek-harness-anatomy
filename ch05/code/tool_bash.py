"""第 5 章教学重构：capability seam 三角色 —— Consumer（tool-bash）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（路径从目标仓库根起）：
- packages/shell/tool-bash/src/index.ts:33       name = 'tool-bash'
- packages/shell/tool-bash/src/index.ts:34       inject = ['tools','shell','systemPrompt','shellEnv','workingDirectory']
- packages/shell/tool-bash/src/index.ts:202      apply(ctx, config)
- packages/shell/tool-bash/src/index.ts:251-255  ctx.systemPrompt.section（跨调用 guidance）
- packages/shell/tool-bash/src/index.ts:542-555  按 ctx.jobs 在场与否注册前台版 / 后台版
- packages/shell/tool-bash/src/index.ts:478-531  execute：校验 → 组装 request → resolve → execute
- packages/shell/tool-bash/src/index.ts:508      后台：resolve({...request, onExpiry:'none'})
- packages/shell/tool-bash/src/index.ts:526-530  前台：resolve({...request, signal}) → execute → result()
- packages/shell/tool-bash/src/render.ts:28-66   renderResult：输出体 + marker
- packages/shell/tool-bash/src/render.ts:62      干净退出不写 marker
- packages/shell/tool-bash/src/render.ts:126     重导出 shell 的 parseExitStatus

[教学决策] 第 4 章 ToolDefinition.execute 退化为 execute(args) -> str（ch04/code/tools.py:87-100）。
本章 tool-bash 作为 tools 管线里的一个 tool body，execute 沿用该退化签名：
面向 ctx.shell.resolve/execute 两个方法调用，返回渲染后的文本。
（真实版 execute(args, exec) 里 exec 携带 signal 与 agent，用于取消链与工作目录；教学版省略。）
"""
from __future__ import annotations

import dataclasses
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

__all__ = ["name", "inject", "apply"]

# 三角色之「消费者」：只面向 ctx.shell 抽象，不 import 任何具体 provider。
name = "tool-bash"
inject = ["shell", "systemPrompt", "tools"]


def render_result(result) -> str:
    """渲染 ShellRunResult：输出体 + marker（render.ts:28-66）。

    marker 即 shell seam 的「退出状态契约」：消费者负责写，parse_exit_status 负责读。
    成功（exit 0、无信号、未超时）不写 marker——parseExitStatus 的兜底就是 0（render.ts:42）。
    """
    out = result.stdout
    if result.stderr:
        out += ("\n" if out and not out.endswith("\n") else "") + "[stderr]\n" + result.stderr
    if not out:
        out = "(no output)"

    markers = []
    if result.timed_out:
        # 命令可能捕获 SIGTERM 后 exit 0，所以超时独立成 marker（render.ts:53）
        markers.append(f"[timed out after {result.timeout_ms}ms]")
    if result.signal is not None:
        markers.append(f"[killed by signal: {result.signal}]")
    elif result.exit_code is not None:
        # [教学简化] 真实版 exitCode 可为 null（准备阶段即超时）；这里与信号分支互斥，
        # 走到 None 说明没有退出码也没有信号，教学版不写伪 marker。
        if result.exit_code != 0:
            # 非零退出报给模型判断，不抛工具错误（render.ts:59-60）
            markers.append(f"[exit code: {result.exit_code}]")
    if not markers:
        return out
    return out + ("" if out.endswith("\n") else "\n") + "\n".join(markers)


def _guidance_text() -> str:
    """跨调用 guidance，进 system-prompt 而非单次 schema 描述（index.ts:254）。"""
    return "Check the [exit code: N] marker on every bash result; investigate failures before moving on."


def apply(ctx: Context, config=None):
    """tool-bash 插件体（index.ts:202）：注入 guidance + 注册 bash 工具。

    消费者职责只有两点：
    1. 往 system-prompt 注入一段「如何读退出标记」的指导；
    2. 把一个工具体注册进 ctx.tools（第 4 章管线），该工具体内部只调 ctx.shell 两个方法。
    """
    # 1. guidance：教育模型读 [exit code: N] 标记（index.ts:251-255）
    ctx.systemPrompt.section("tool:bash", _guidance_text(), order=20)

    # 2. 注册 bash 工具：execute 即第 4 章 tools 管线里的一个 tool body
    def bash_tool(args):
        # 面向抽象：resolve → execute；消费者不认识 LocalBashExecutor（index.ts:491-530）
        request = ShellExecRequest(
            command=args["command"],
            workdir=args.get("workdir"),
            timeout_ms=args.get("timeout_ms"),
        )
        if args.get("run_in_background"):
            # 后台：onExpiry='none' 不装 deadline（index.ts:508）。
            # 教学版打印 status 后即丢弃句柄；真实版把句柄注册进 ctx.jobs 长期持有。
            spec = ctx.shell.resolve(dataclasses.replace(request, on_expiry="none"))
            proc = ctx.shell.execute(spec)
            return f"[started in background, status={proc.status.value}]"
        # 前台：resolve 一次，execute 拿到句柄，再等 result()（index.ts:526-528）
        spec = ctx.shell.resolve(request)
        proc = ctx.shell.execute(spec)
        result = proc.result()
        return render_result(result)

    ctx.tools.register(
        ToolDefinition(
            name="bash",
            description="执行 bash 命令（shell seam 消费者）",
            parameters={"command": "string", "workdir": "string?", "timeout_ms": "int?"},
            execute=bash_tool,
        )
    )
