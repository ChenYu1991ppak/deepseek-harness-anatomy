"""第 5 章教学桩：第二种 provider 形态 —— SandboxExecutor（模拟沙箱）。

仅标准库，Python 3.10+。运行：python3 main.py

[教学决策] 素材 §2 提到 bash-sandbox / pwsh-local 是 bash-local 的兄弟 provider，
但 §5 符号映射总表只给 LocalBashExecutor 一行。为演示「换 provider 消费者一行不改」，
本章新增一个纯教学桩 SandboxExecutor：同样实现 ShellExecutor 的 resolve/run/start，
但 run 不真正 spawn 进程，而是把命令「放进沙箱」记录并返回固定退出码 0。
真实 bash-sandbox 的 sandbox_mode 机制留到第 6 章 subprocess seam 展开。
"""
from __future__ import annotations

import os
import sys

_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from shell import (  # noqa: E402
    ShellExecRequest,
    ShellExecSpec,
    ShellExecutor,
    ShellProcess,
    ShellProcessStatus,
    ShellRunResult,
)

__all__ = ["SandboxExecutor"]


class SandboxExecutor(ShellExecutor):
    """模拟沙箱实现：与 LocalBashExecutor 同接口、不同行为。

    换 provider 演示：消费者 tool-bash 与定义 ShellExecutor 均不改一行，
    只需在装配处把 ctx.shell 从 LocalBashExecutor 换成 SandboxExecutor。
    """

    def __init__(self, ctx, config=None):
        super().__init__(ctx)  # 挂到 ctx.shell
        self.log = []  # 记录「被放进沙箱的命令」

    @property
    def sandbox_mode(self):
        return "sandbox"  # 与 LocalBashExecutor 的 None 形成对照

    def resolve(self, request: ShellExecRequest) -> ShellExecSpec:
        return ShellExecSpec(
            command=request.command,
            workdir=request.workdir or os.getcwd(),
            timeout_ms=request.timeout_ms or 120_000,
            stdout_max_bytes=request.stdout_max_bytes or 64 * 1024,
            signal=request.signal,
            stdin=request.stdin,
            env=request.env,
            dsh_env=request.dsh_env,
            sandbox_policy=request.sandbox_policy,
        )

    def run(self, spec: ShellExecSpec) -> ShellRunResult:
        self.log.append(spec.command)
        return ShellRunResult(
            exit_code=0,
            stdout=f"[sandbox] 已记录命令：{spec.command}\n[sandbox] 未真正执行（教学桩）",
            timeout_ms=spec.timeout_ms,
        )

    def start(self, spec: ShellExecSpec) -> ShellProcess:
        self.log.append(spec.command)
        proc = ShellProcess(status=ShellProcessStatus.running)
        proc.status = ShellProcessStatus.completed
        proc.exit_code = 0

        def done():
            return proc

        proc.done = done
        return proc
