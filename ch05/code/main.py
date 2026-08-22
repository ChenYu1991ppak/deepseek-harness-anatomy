"""第 5 章演示：capability seam 三角色（以 shell 为例）。

仅标准库，Python 3.10+。运行：python3 main.py

演示段落：
1. 装配容器 + 三角色就位（Definition=shell.py / Provider=bash_local.py / Consumer=tool_bash.py）
2. 前台 run：resolve → run，输出尾部带 [exit code: N] marker
3. 后台 start：立即返回 running 句柄，再 done() 收敛
4. 换 provider：ctx.shell 换成 SandboxExecutor，消费者 tool-bash 与定义一行不改
5. 与第 4 章事件瀑布式管线的正交对照
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
_CH02 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch02", "code"))
_CH04 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch04", "code"))
for _p in (_HERE, _CH04, _CH02, _CH01):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Context  # noqa: E402
from agent_loop import SystemPromptService  # noqa: E402
from tools import ToolExecution, ToolRuntime  # noqa: E402
from bash_local import LocalBashExecutor, SubprocessStub  # noqa: E402
from sandbox_executor import SandboxExecutor  # noqa: E402
import tool_bash  # noqa: E402
from shell import parse_exit_status  # noqa: E402


def _assemble(provider_factory):
    """装配一个 context，三角色就位。provider_factory(ctx) -> ShellExecutor 实例。

    [教学简化] 真实组合期由 cordis 按 inject 声明自动装配；这里手写装配顺序，
    只求「消费者只 import shell 抽象、不认识具体 provider」这一论断成立。
    """
    ctx = Context()
    SystemPromptService(ctx)  # ctx.systemPrompt（第 2 章）
    ToolRuntime(ctx)          # ctx.tools（第 4 章）
    SubprocessStub(ctx)       # ctx.subprocess（第 6 章 seam 教学桩，构造即注册）
    provider_factory(ctx)     # ctx.shell ← 三角色之 Provider
    tool_bash.apply(ctx)      # 三角色之 Consumer：只面向 ctx.shell 抽象
    return ctx


def run_bash(ctx, command, **kw):
    """通过第 4 章 tools 管线触发 bash 工具体（execute 即 tool body）。"""
    exec_ = ToolExecution(call_id=f"call-{command[:8]}", name="bash", arguments={"command": command, **kw})
    return ctx.tools.execute(exec_).content


def main():
    # -- 1. 装配 + 三角色就位 --
    print("== 1. 装配容器 + 三角色就位 ==")
    ctx = _assemble(lambda c: LocalBashExecutor(c, c.subprocess))
    print("  ctx.shell =", type(ctx.shell).__name__)
    print("  ctx.shell.sandbox_mode =", ctx.shell.sandbox_mode)
    print("  tool-bash inject =", tool_bash.inject)
    print("  system-prompt 的 tool:bash section 已注入：")
    bash_section = next(s for s in ctx.systemPrompt.assemble().sections if s.name == "tool:bash")
    print("    " + bash_section.text.replace("\n", "\n    "))

    # -- 2. 前台 run --
    print("\n== 2. 前台 run：resolve → run ==")
    out = run_bash(ctx, "echo hello-from-shell-seam")
    print(out)
    parsed = parse_exit_status(out)
    print(f"  [解析 marker] body={parsed.body!r} exit_code={parsed.exit_code}")

    # -- 3. 后台 start --
    print("\n== 3. 后台 start：立即返回 running 句柄 ==")
    out2 = run_bash(ctx, "sleep 0.05 && echo background-done", run_in_background=True)
    print("  " + out2)

    # -- 4. 换 provider：消费者一行不改 --
    print("\n== 4. 换 provider：ctx.shell 换成 SandboxExecutor ==")
    ctx2 = _assemble(lambda c: SandboxExecutor(c))
    print("  ctx2.shell =", type(ctx2.shell).__name__)
    print("  ctx2.shell.sandbox_mode =", ctx2.shell.sandbox_mode)
    out3 = run_bash(ctx2, "rm -rf /tmp/dangerous")
    print(out3)
    print("  同一消费者 tool_bash 与定义 ShellExecutor 均未改一行。")

    # -- 5. 正交对照 --
    print("\n== 5. 方法调用式 seam vs 事件瀑布式管线 ==")
    print("  shell seam：三个抽象方法（resolve/run/start），消费者直接方法调用")
    print("  tools 管线：tools/pre-execute → tools/execute → tools/post-execute，事件瀑布")
    print("  二者正交：bash 工具体的 execute 正是 tools 管线里的一个 tool body")


if __name__ == "__main__":
    main()
