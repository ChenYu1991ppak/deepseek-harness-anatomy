"""第 5 章教学重构：capability seam 三角色 —— Service Provider（bash-local）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（packages/shell/bash-local/src/index.ts）：
- LocalBashExecutor        ↔ index.ts:102（static inject=['subprocess']）
- Config schema            ↔ index.ts:105–112（默认 timeout_ms=120s / max=600s / 64k / 64MB / grace 3s）
- ENV_OVERRIDES            ↔ index.ts:27（NO_COLOR / TERM=dumb / PAGER / GIT_PAGER）
- assertServiceableBashConfig ↔ index.ts:83
- resolve(request)         ↔ index.ts:146（填 workdir/timeout 封顶，透传其余）
- run / runArgv            ↔ index.ts:211 / :223（单一 deadline 融合 timeout+取消）
- start / startArgv        ↔ index.ts:242 / :255（后台立即返回句柄）
- spawnSpec                ↔ index.ts:175（env 合并顺序 ENV_OVERRIDES → spec.env → dsh_env）

[教学决策] bash-local 依赖 ctx.subprocess（第 6 章 seam）。本章用 SubprocessStub 教学桩
占位，只模拟 spawn/done/read_output/kill 的机制面，真正进程树管理留到第 6 章展开。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# 复用第 1 章 Service；复用本章 shell.py 的词汇类型与抽象接口。
_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from cordis import Service  # noqa: E402
from shell import (  # noqa: E402
    ShellExecRequest,
    ShellExecSpec,
    ShellExecutor,
    ShellProcess,
    ShellProcessRead,
    ShellProcessStatus,
    ShellRunResult,
)

__all__ = [
    "ENV_OVERRIDES",
    "Config",
    "LocalBashExecutor",
    "assert_serviceable_bash_config",
]

# 模型友好环境覆盖：抑制颜色/分页/交互（index.ts:27）。
ENV_OVERRIDES = {
    "NO_COLOR": "1",
    "TERM": "dumb",
    "PAGER": "cat",
    "GIT_PAGER": "cat",
}


@dataclass(frozen=True)
class Config:
    """bash-local 配置，默认值对齐 index.ts:105–112。"""

    timeout_ms: int = 120_000
    max_timeout_ms: int = 600_000
    max_output_bytes: int = 64 * 1024
    max_spill_bytes: int = 64 * 1024 * 1024
    grace_ms: int = 3_000
    cwd: str | None = None


def assert_serviceable_bash_config(config: Config):
    """构造前校验 schema 表达不了的约束（index.ts:83）。

    [教学简化] 只校验「正且有限」与 grace 上限，其余默认值约束省略。
    """
    if config.timeout_ms <= 0 or config.max_timeout_ms <= 0:
        raise ValueError("timeout_ms / max_timeout_ms 必须为正")
    if config.grace_ms < 0 or config.grace_ms > config.timeout_ms:
        raise ValueError("grace_ms 必须在 [0, timeout_ms] 内")


def _clamp_timeout(request_ms, config):
    """timeout 封顶：未给用默认，超上限封顶（index.ts:201）。"""
    if request_ms is None:
        return config.timeout_ms
    return min(max(request_ms, 0), config.max_timeout_ms)


# ---------- SubprocessStub：第 6 章 subprocess seam 的教学桩 ----------


class _Handle:
    """spawn 返回的句柄：done 同步阻塞等待，read_output 增量读，kill 终止。

    [教学简化] 真实版是进程树管理 + 异步 handle；本章同步桩只保留机制面。
    """

    def __init__(self, argv, workdir, env, timeout_ms):
        self._argv = argv
        self._workdir = workdir
        self._env = env
        self._timeout_ms = timeout_ms
        self._result = None
        self._spill = ""

    @property
    def done(self):
        """阻塞等待进程结束，返回 {exit_code, stdout, stderr, timed_out}。"""
        if self._result is None:
            self._result = self._run()
        return self._result

    def _run(self):
        import subprocess

        try:
            p = subprocess.run(
                self._argv,
                cwd=self._workdir or None,
                env=self._env,
                capture_output=True,
                text=True,
                timeout=self._timeout_ms / 1000 if self._timeout_ms else None,
            )
            return {
                "exit_code": p.returncode,
                "stdout": p.stdout or "",
                "stderr": p.stderr or "",
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or b""
            if isinstance(out, bytes):
                out = out.decode(errors="replace")
            return {"exit_code": None, "stdout": out, "stderr": "", "timed_out": True}

    def read_output(self):
        """[教学简化] 增量读：桩里一次性返回全部剩余输出。"""
        return self._spill

    def kill(self):
        """[教学简化] 终止：桩里把结果标记为 killed。"""
        self._result = {"exit_code": None, "stdout": "", "stderr": "", "timed_out": False, "killed": True}


class SubprocessStub(Service):
    """ctx.subprocess 教学桩（第 6 章展开真实进程树 seam）。"""

    def __init__(self, ctx, config=None):
        super().__init__(ctx, "subprocess")
        self._spawns = []

    def spawn(self, argv, workdir=None, env=None, timeout_ms=None):
        handle = _Handle(argv, workdir, env, timeout_ms)
        self._spawns.append(handle)
        return handle


# ---------- LocalBashExecutor：本地 bash 实现 ----------


class LocalBashExecutor(ShellExecutor):
    """本地 bash 实现（index.ts:102）。

    inject=['subprocess'] 在 TS 由 cordis 注入；教学版改为构造函数显式注入（notes §5 命名总则）。
    """

    inject = ["subprocess"]

    def __init__(self, ctx, subprocess, config=None):
        super().__init__(ctx)  # 挂到 ctx.shell
        self._subprocess = subprocess
        self.config = config or Config()
        assert_serviceable_bash_config(self.config)

    # -- resolve：把请求填默认值/封顶成 spec（index.ts:146） --

    def resolve(self, request: ShellExecRequest) -> ShellExecSpec:
        timeout_ms = _clamp_timeout(request.timeout_ms, self.config)
        stdout_max_bytes = request.stdout_max_bytes if request.stdout_max_bytes is not None else self.config.max_output_bytes
        if stdout_max_bytes <= 0:
            raise ValueError("stdout_max_bytes 必须为正且有限")  # assertPositiveFinite（:154）
        return ShellExecSpec(
            command=request.command,
            workdir=request.workdir or self.config.cwd or os.getcwd(),
            timeout_ms=timeout_ms,
            stdout_max_bytes=stdout_max_bytes,
            signal=request.signal,
            stdin=request.stdin,
            env=request.env,
            dsh_env=request.dsh_env,
            sandbox_policy=request.sandbox_policy,
        )

    # -- run：前台执行（index.ts:211 → runArgv :223） --

    def run(self, spec: ShellExecSpec) -> ShellRunResult:
        return self.run_argv(spec, ["bash", "-c", spec.command])

    def run_argv(self, spec: ShellExecSpec, argv) -> ShellRunResult:
        handle = self._subprocess.spawn(
            argv,
            workdir=spec.workdir,
            env=self._merged_env(spec),
            timeout_ms=spec.timeout_ms,
        )
        outcome = handle.done
        if outcome.get("killed"):
            # [教学简化] killed 时 exit_code 记 0：被杀进程没有正常退出码，
            # 真实终止原因由 signal 字段承载，展示层靠 [killed by signal: X] marker 区分。
            return ShellRunResult(exit_code=0, signal="SIGKILL", aborted=True, timeout_ms=spec.timeout_ms)
        timed_out = outcome["timed_out"]
        # aborted：spec.signal.aborted 且非超时（index.ts:224）
        aborted = bool(spec.signal is not None and getattr(spec.signal, "aborted", False)) and not timed_out
        return ShellRunResult(
            exit_code=outcome["exit_code"] if outcome["exit_code"] is not None else 0,
            stdout=outcome["stdout"][: spec.stdout_max_bytes],
            stderr=outcome["stderr"][: spec.stdout_max_bytes],
            timed_out=timed_out,
            aborted=aborted,
            timeout_ms=spec.timeout_ms,
        )

    # -- start：后台执行（index.ts:242 → startArgv :255） --

    def start(self, spec: ShellExecSpec) -> ShellProcess:
        return self.start_argv(spec, ["bash", "-c", spec.command])

    def start_argv(self, spec: ShellExecSpec, argv) -> ShellProcess:
        handle = self._subprocess.spawn(
            argv,
            workdir=spec.workdir,
            env=self._merged_env(spec),
            timeout_ms=None,  # 后台忽略 timeout，只靠 kill（notes §5.2 ③）
        )

        proc = ShellProcess(status=ShellProcessStatus.running)
        proc._outcome = handle

        def read_output():
            delta = handle.read_output()
            return ShellProcessRead(delta=delta)

        def kill():
            handle.kill()
            proc.status = ShellProcessStatus.killed

        def done():
            outcome = handle.done
            proc.exit_code = outcome["exit_code"]
            if outcome.get("killed"):
                proc.status = ShellProcessStatus.killed
                proc.signal = "SIGKILL"
            else:
                proc.status = ShellProcessStatus.completed
            return proc

        proc.read_output = read_output
        proc.kill = kill
        proc.done = done
        return proc

    # -- spawnSpec：组装 argv 与 env 合并顺序（index.ts:175 / :193–196） --

    def _merged_env(self, spec: ShellExecSpec):
        """env = {**ENV_OVERRIDES, **spec.env, **spec.dsh_env}（index.ts:196）。"""
        merged = dict(ENV_OVERRIDES)
        if spec.env:
            merged.update(spec.env)
        if spec.dsh_env:
            merged.update(spec.dsh_env)
        return merged or None
