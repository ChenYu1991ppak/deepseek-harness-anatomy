"""第 5 章教学桩：第二种 provider 形态 —— SandboxExecutor（模拟沙箱）。

仅标准库，Python 3.10+。运行：python3 main.py

[教学决策] 素材只给 LocalBashExecutor 一份实现。为演示「换 provider 消费者一行不改」，
本章新增一个纯教学桩 SandboxExecutor：同样实现 ShellExecutor 的 resolve/execute，
但 execute 不真正 spawn 进程，而是把命令记进沙箱日志并返回一个已结束的句柄。
真实 bash-sandbox 通过实现 sandboxMode + 覆盖 resolve 来标记沙箱策略，
留到第 6 章 subprocess / sandbox seam 展开。
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
    ShellExecution,
    ShellExecutor,
    ShellProcessStatus,
    ShellRunResult,
)

__all__ = ["SandboxExecutor"]


class _SandboxProcess(ShellExecution):
    """已结束的沙箱句柄：execute 返回时 status 已是 completed。"""

    def __init__(self, command, timeout_ms):
        super().__init__(status=ShellProcessStatus.completed)
        self.exit_code = 0
        self._command = command
        self._timeout_ms = timeout_ms

    def done(self):
        return self

    def read_output(self):
        from shell import ShellProcessRead

        return ShellProcessRead(delta="")

    def kill(self):
        return False

    def result(self):
        return ShellRunResult(
            exit_code=0,
            stdout=f"[sandbox] 已记录命令：{self._command}\n[sandbox] 未真正执行（教学桩）",
            timeout_ms=self._timeout_ms,
        )


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
        return "sandbox"  # 与 LocalBashExecutor 的 None 形成对照（shell/src/index.ts:74 的同一契约）

    def resolve(self, request: ShellExecRequest) -> ShellExecSpec:
        return ShellExecSpec(
            command=request.command,
            workdir=request.workdir or os.getcwd(),
            timeout_ms=request.timeout_ms or 120_000,
            on_expiry=request.on_expiry,
            stdout_max_bytes=request.stdout_max_bytes or 64 * 1024,
            signal=request.signal,
            stdin=request.stdin,
            env=request.env,
            dsh_env=request.dsh_env,
            sandbox_policy=request.sandbox_policy,
        )

    def execute(self, spec: ShellExecSpec) -> ShellExecution:
        self.log.append(spec.command)
        return _SandboxProcess(spec.command, spec.timeout_ms)
