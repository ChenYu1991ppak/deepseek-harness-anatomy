"""第 6 章演示：subprocess / shell / fs 执行世界。

仅标准库，Python 3.10+。运行：python3 main.py

演示段落：
1. ctx.subprocess：前台执行（经 bash 工具，复用第 5 章消费者，替换 SubprocessStub）
2. ctx.subprocess：进程树终止（SIGTERM → grace → SIGKILL，杀整棵树）
3. ctx.subprocess：dispose 兜底回收（context 卸载时 terminate 受管进程）
4. ctx.subprocess：scrub_env 剔除凭据形变量
5. ctx.fs：写 + 读 + 版本守卫（拒绝盲覆盖与过期版本）
6. ctx.sandbox：confine 把 argv 包成「runner + profile + -- + argv」+ fail-closed
7. bash-sandbox：整 provider 迁移（换执行器不动工具层）
"""
import os
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
_CH02 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch02", "code"))
_CH04 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch04", "code"))
_CH05 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch05", "code"))
for _p in (_HERE, _CH05, _CH04, _CH02, _CH01):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Context  # noqa: E402
from agent_loop import SystemPromptService  # noqa: E402
from tools import ToolExecution, ToolRuntime  # noqa: E402
from bash_local import LocalBashExecutor  # noqa: E402
import tool_bash  # noqa: E402

from subprocess_service import SubprocessService  # noqa: E402
from fs_service import FileSystem, FsError  # noqa: E402
from sandbox_service import (  # noqa: E402
    SandboxMode,
    SandboxPolicy,
    SandboxProvider,
    SandboxUnavailableError,
)
from bash_sandbox import SandboxBashExecutor  # noqa: E402

_MOCK_RUNNER = os.path.join(_HERE, "mock_runner.py")


def _assemble(provider_factory, fs_root, grace_ms=200):
    """装配一个 context：三个执行世界 seam 就位 + shell provider + tool-bash 消费者。

    [教学简化] 真实组合期由 cordis 按 inject 声明自动装配；这里手写装配顺序。
    """
    ctx = Context()
    SystemPromptService(ctx)  # ctx.systemPrompt（第 2 章）
    ToolRuntime(ctx)          # ctx.tools（第 4 章）
    SubprocessService(ctx, config={"grace_ms": grace_ms})  # ctx.subprocess（真实 seam）
    FileSystem(ctx, root=fs_root)                          # ctx.fs
    SandboxProvider(ctx, runner_argv=[sys.executable, _MOCK_RUNNER])  # ctx.sandbox
    provider_factory(ctx)     # ctx.shell ← provider
    tool_bash.apply(ctx)      # Consumer：只面向 ctx.shell 抽象
    return ctx


def run_bash(ctx, command, **kw):
    """通过第 4 章 tools 管线触发 bash 工具体（execute 即 tool body）。"""
    exec_ = ToolExecution(call_id=f"call-{command[:8]}", name="bash", arguments={"command": command, **kw})
    return ctx.tools.execute(exec_).content


def pgid_alive(pgid):
    """signal 0 探测进程组是否还有存活成员。"""
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def main():
    fs_root = tempfile.mkdtemp(prefix="ch06-fs-")

    # -- 1. 前台执行（经 bash 工具，真实 subprocess seam） --
    print("== 1. ctx.subprocess：前台执行（经 bash 工具） ==")
    ctx = _assemble(lambda c: LocalBashExecutor(c, c.subprocess), fs_root)
    print("  ctx.subprocess =", type(ctx.subprocess).__name__, "（替换第 5 章 SubprocessStub）")
    out = run_bash(ctx, "echo hello-from-real-subprocess")
    print("  " + out.replace("\n", "\n  "))

    # -- 2. 进程树终止：SIGTERM → grace → SIGKILL --
    print("\n== 2. ctx.subprocess：进程树终止（杀整棵树） ==")
    handle = ctx.subprocess.spawn(
        ["bash", "-c", "trap '' TERM; sleep 30 & while true; do sleep 1; done"]
    )
    time.sleep(0.3)  # 等子进程起来
    pgid = handle.pid
    print(f"  spawn 忽略 SIGTERM 的树根（带后台子进程），pgid={pgid}")
    print(f"  terminate 前进程组存活: {pgid_alive(pgid)}")
    t0 = time.monotonic()
    handle.terminate()
    result = handle.done  # 收尸
    elapsed_ms = (time.monotonic() - t0) * 1000
    time.sleep(0.1)
    print(f"  terminate 后进程组存活: {pgid_alive(pgid)}")
    print(f"  exit_code={result['exit_code']}，耗时 {elapsed_ms:.0f}ms ≈ grace 200ms（SIGTERM 被忽略 → SIGKILL 整组）")

    # -- 3. dispose 兜底回收 --
    print("\n== 3. ctx.subprocess：dispose 兜底回收 ==")
    ctx3 = Context()
    SubprocessService(ctx3, config={"grace_ms": 100})
    h3 = ctx3.subprocess.spawn(["bash", "-c", "sleep 30"])
    time.sleep(0.2)
    print(f"  spawn 一个睡眠进程（不等待 done），dispose 前进程组存活: {pgid_alive(h3.pid)}")
    ctx3.dispose()
    time.sleep(0.2)
    print(f"  dispose 后进程组存活: {pgid_alive(h3.pid)}（disposeManagedProcesses 兜底）")

    # -- 4. scrub_env 剔除凭据形变量 --
    print("\n== 4. ctx.subprocess：scrub_env 剔除凭据形变量 ==")
    os.environ["MY_API_TOKEN"] = "secret-123"
    out4 = run_bash(ctx, "echo token=${MY_API_TOKEN:-<scrubbed>}")
    print("  父环境 MY_API_TOKEN=secret-123")
    print("  " + out4.replace("\n", "\n  "))

    # -- 5. ctx.fs：写 + 读 + 版本守卫 --
    print("\n== 5. ctx.fs：写 + 读 + 版本守卫 ==")
    w = ctx.fs.write("hello.txt", "v1 content")
    print(f"  write hello.txt → version={w['version']}")
    r = ctx.fs.read("hello.txt")
    print(f"  read hello.txt → content={r['content']!r} version={r['version']}")
    try:
        ctx.fs.write("hello.txt", "blind overwrite")  # 无 base_version
    except FsError as e:
        print(f"  盲覆盖（未先 read）被拒: {e.code}")
    try:
        ctx.fs.write("hello.txt", "stale write", base_version="deadbeefdeadbeef")
    except FsError as e:
        print(f"  过期版本写入被拒: {e.code}")
    e2 = ctx.fs.edit("hello.txt", "v1", "v2", base_version=r["version"])
    print(f"  edit v1→v2（带正确版本）→ content={ctx.fs.read('hello.txt')['content']!r}")

    # -- 6. ctx.sandbox：confine 包裹 argv + fail-closed --
    print("\n== 6. ctx.sandbox：confine 包裹 argv ==")
    policy = SandboxPolicy(mode=SandboxMode.WORKSPACE_WRITE, workspace_root=fs_root)
    confined = ctx.sandbox.confine(["bash", "-c", "echo hi"], policy)
    print("  原 argv: ['bash', '-c', 'echo hi']")
    print("  confined.argv = runner + profile + '--' + argv:")
    print("    [" + ", ".join(repr(a) for a in confined.argv) + "]")
    no_runner = SandboxProvider(ctx, runner_argv=None)
    try:
        no_runner.confine(["echo"], policy)
    except SandboxUnavailableError:
        print("  无 runner 时 fail-closed: SandboxUnavailableError")

    # -- 7. bash-sandbox：整 provider 迁移 --
    print("\n== 7. bash-sandbox：整 provider 迁移（换执行器不动工具层） ==")
    ctx7 = _assemble(lambda c: SandboxBashExecutor(c, c.subprocess, c.sandbox), fs_root)
    print("  ctx7.shell =", type(ctx7.shell).__name__)
    out7 = run_bash(ctx7, "echo hello-from-sandbox")
    print("  " + out7.replace("\n", "\n  "))
    print("  同一消费者 tool_bash 与定义 ShellExecutor 均未改一行。")

    ctx.dispose()
    ctx7.dispose()


if __name__ == "__main__":
    main()
