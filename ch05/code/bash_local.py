"""第 5 章教学重构：capability seam 三角色 —— Service Provider（bash-local）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（路径从目标仓库根起）：
- packages/shell/bash-local/src/index.ts:27-32    ENV_OVERRIDES（NO_COLOR / TERM=dumb / PAGER / GIT_PAGER）
- packages/shell/bash-local/src/index.ts:79-88    assertServiceableBashConfig
- packages/shell/bash-local/src/index.ts:97       LocalBashExecutor 类
- packages/shell/bash-local/src/index.ts:98       static inject = ['subprocess']
- packages/shell/bash-local/src/index.ts:100-107  Config 默认值（120s / 600s / 64k / 64MB / grace 3s）
- packages/shell/bash-local/src/index.ts:120-147  resolve：填 workdir、timeout 封顶、onExpiry 默认、透传其余
- packages/shell/bash-local/src/index.ts:151-174  spawnSpec：argv / cwd / stdio 预算 / graceMs / env
- packages/shell/bash-local/src/index.ts:172      env 合并顺序 ENV_OVERRIDES → spec.env → dshEnv
- packages/shell/bash-local/src/index.ts:187-189  execute → executeArgv(['bash','-c',command])
- packages/shell/bash-local/src/index.ts:213-222  onExpiry='kill'：单一 deadline 融合超时与取消
- packages/shell/bash-local/src/index.ts:218-221  首因分类：timedOut 与 aborted 互斥
- packages/shell/bash-local/src/index.ts:224-227  onExpiry='none'：不装 deadline
- packages/shell/bash-local/src/index.ts:302-386  句柄：status / done / readOutput / kill / result

[教学决策] bash-local 依赖 ctx.subprocess（第 6 章 seam）。本章用 SubprocessStub 教学桩
占位，只模拟 spawn / done / read_from / terminate 的机制面，真正进程树管理留到第 6 章展开。
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
    ShellExecution,
    ShellExecutor,
    ShellProcessRead,
    ShellProcessStatus,
    ShellRunResult,
)

__all__ = [
    "LocalBashExecutor",
]

# 模型友好环境覆盖：抑制颜色/分页/交互（index.ts:27）。
ENV_OVERRIDES = {
    "NO_COLOR": "1",
    "TERM": "dumb",
    "PAGER": "cat",
    "GIT_PAGER": "cat",
}

MAX_TIMER_DELAY_MS = 2_147_483_647  # Node 定时器上限；真实版来自 dsh-timeout


@dataclass(frozen=True)
class Config:
    """bash-local 配置，默认值对齐 index.ts:100-107。"""

    timeout_ms: int = 120_000
    max_timeout_ms: int = 600_000
    max_output_bytes: int = 64 * 1024
    max_spill_bytes: int = 64 * 1024 * 1024
    grace_ms: int = 3_000
    cwd: str | None = None


def _assert_positive_finite(name, value):
    """正且有限（index.ts:66-70 的 assertPositiveFinite）。"""
    if value is None or value <= 0:
        raise ValueError(f"bash-local: {name} must be a positive finite number")


def assert_serviceable_bash_config(config: Config):
    """构造前校验 schema 表达不了的约束（index.ts:79-88）。

    [教学简化] 只校验教学版暴露的字段；真实版逐个检查五个预算。
    """
    _assert_positive_finite("timeout_ms", config.timeout_ms)
    _assert_positive_finite("max_timeout_ms", config.max_timeout_ms)
    _assert_positive_finite("max_output_bytes", config.max_output_bytes)
    if config.grace_ms < 0 or config.grace_ms > MAX_TIMER_DELAY_MS:
        raise ValueError("bash-local: grace_ms must fit a timer")


def _clamp_timeout(request_ms, config):
    """timeout 封顶：未给用默认，超上限封顶（dsh-timeout 的 clampTimeout，index.ts:122-127）。"""
    if request_ms is None:
        return config.timeout_ms
    return min(max(request_ms, 1), config.max_timeout_ms)


class Deadline:
    """单一 deadline：融合「执行器超时」与「调用方取消」（index.ts:213-222）。

    [教学简化] 真实版是 dsh-timeout 的 AbortSignal（超时用 reason='BASH_TIMEOUT' 触发）；
    教学版同步执行，只记录首因：timeout / abort / None。
    """

    def __init__(self, signal, timeout_ms):
        self._external = signal
        self.timeout_ms = timeout_ms
        self.reason = None  # 'timeout' | 'abort' | None

    @property
    def aborted(self):
        """已经停了吗：外部信号已 aborted，或本 deadline 已经判过首因。"""
        return self.reason is not None or bool(getattr(self._external, "aborted", False))

    def classify(self):
        """首因分类（index.ts:218-221）：timedOut 与 aborted 互斥。"""
        if self.reason is None and getattr(self._external, "aborted", False):
            self.reason = "abort"
        return {"timed_out": self.reason == "timeout", "aborted": self.reason == "abort"}


# ---------- SubprocessStub：第 6 章 subprocess seam 的教学桩 ----------


class _Handle:
    """spawn 返回的句柄：done 阻塞等待，read_from 按 offset 增量读，terminate 终止。

    [教学简化] 真实版是进程树管理 + 异步 handle + spill 文件；本章同步桩只保留机制面。
    """

    def __init__(self, spec):
        self._argv = spec["argv"]
        self._cwd = spec["cwd"]
        self._env = spec["env"]
        self._deadline = spec["signal"]  # 教学版没装计时器时是 None
        self._outcome = None
        self._read = False
        self._killed = False

    @property
    def done(self):
        if self._outcome is None:
            self._outcome = self._run()
        return self._outcome

    def _run(self):
        import subprocess

        if self._killed:
            return {"exit_code": None, "signal": "SIGKILL", "stdout": "", "stderr": ""}
        timeout_s = None
        if self._deadline is not None and self._deadline.timeout_ms:
            timeout_s = self._deadline.timeout_ms / 1000
        try:
            p = subprocess.run(
                self._argv,
                cwd=self._cwd or None,
                env=self._env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            # 超时由 deadline 判首因；进程这边只报「死于 SIGKILL」
            self._deadline.reason = "timeout"
            return {"exit_code": None, "signal": "SIGKILL", "stdout": "", "stderr": ""}
        signal = f"SIG{-p.returncode}" if p.returncode < 0 else None
        return {
            "exit_code": None if signal else p.returncode,
            "signal": signal,
            "stdout": p.stdout or "",
            "stderr": p.stderr or "",
        }

    def read_from(self, offset):
        """offset 读：返回 (text, next_offset, lossy)，连续读不重复交付。"""
        text = self.done["stdout"]
        if self._read:
            return "", len(text), False
        self._read = True
        return text, len(text), False

    def terminate(self):
        self._killed = True


class SubprocessStub(Service):
    """ctx.subprocess 教学桩（第 6 章展开真实进程树 seam）。"""

    def __init__(self, ctx, config=None):
        super().__init__(ctx, "subprocess")

    def spawn(self, spec):
        """spec 是一个 dict，对应 SubprocessSpawnSpec（argv/cwd/stdio/graceMs/signal/env）。"""
        return _Handle(spec)


# ---------- LocalBashExecutor：本地 bash 实现 ----------


class LocalBashExecutor(ShellExecutor):
    """本地 bash 实现（index.ts:97）。

    inject=['subprocess'] 在 TS 由 cordis 注入；教学版改为构造函数显式注入（notes §5 命名总则）。
    """

    inject = ["subprocess"]

    def __init__(self, ctx, subprocess, config=None):
        super().__init__(ctx)  # 挂到 ctx.shell
        self._subprocess = subprocess
        self.config = config or Config()
        assert_serviceable_bash_config(self.config)

    # -- resolve：把请求填默认值/封顶成 spec（index.ts:120-147） --

    def resolve(self, request: ShellExecRequest) -> ShellExecSpec:
        assert_serviceable_bash_config(self.config)
        timeout_ms = _clamp_timeout(request.timeout_ms, self.config)
        stdout_max_bytes = (
            request.stdout_max_bytes
            if request.stdout_max_bytes is not None
            else self.config.max_output_bytes
        )
        _assert_positive_finite("request.stdout_max_bytes", stdout_max_bytes)
        return ShellExecSpec(
            command=request.command,
            # index.ts:132：request.workdir ?? config.cwd ?? process.cwd()
            workdir=request.workdir or self.config.cwd or os.getcwd(),
            timeout_ms=timeout_ms,
            # 真实版在这里填默认 'kill'（index.ts:134）；教学版的 'kill' 默认落在 ShellExecRequest 字段上
            on_expiry=request.on_expiry,
            stdout_max_bytes=stdout_max_bytes,
            # stdin/env/dshEnv/sandboxPolicy 原样透传，不给默认（index.ts:139-145）
            signal=request.signal,
            stdin=request.stdin,
            env=request.env,
            dsh_env=request.dsh_env,
            sandbox_policy=request.sandbox_policy,
        )

    # -- execute：前台/后台不是两个方法，是调用方等不等 result()（index.ts:187-189） --

    def execute(self, spec: ShellExecSpec) -> ShellExecution:
        return self.execute_argv(spec, ["bash", "-c", spec.command])

    def execute_argv(self, spec: ShellExecSpec, argv) -> ShellExecution:
        # 1. deadline 接线（index.ts:213-227）
        if spec.on_expiry == "kill":
            deadline = Deadline(spec.signal, spec.timeout_ms)

            def classify():
                return deadline.classify()

            spawn_signal = deadline
        else:
            # onExpiry='none'：不装 deadline，只能靠 kill() 或 spec.signal 停
            def classify():
                return {"timed_out": False, "aborted": bool(getattr(spec.signal, "aborted", False))}

            spawn_signal = spec.signal

        # 2. spawn（index.ts:251-263）：同步 spawn 抛错被收进一个已结算的 killed 句柄，
        #    错误本身由 result() 抛出（index.ts:370-374），done() 不 reject
        handle = None
        spawn_error = None
        try:
            handle = self._subprocess.spawn(self.spawn_spec(spec, argv, spawn_signal))
        except Exception as error:  # noqa: BLE001
            spawn_error = error

        # 3. 句柄（index.ts:302-386）：status 只结算一次，result() 按需 memoize
        proc = ShellExecution(status=ShellProcessStatus.running)
        stdout_offset = 0
        result_box = {}

        def done():
            """等待终态；永不 reject（packages/shell/shell/src/index.ts:51-52）。"""
            if handle is None:
                # spawn 没产出进程：句柄照样结算成 killed，错误留给 result()
                proc.status = ShellProcessStatus.killed
                return proc
            outcome = handle.done
            if proc.status == ShellProcessStatus.running:
                # 任何信号终止都算 killed，包括命令给自己发信号（index.ts:310）
                killed = (deadline.aborted if spec.on_expiry == "kill" else False) or outcome.get("signal") is not None
                proc.status = ShellProcessStatus.killed if killed else ShellProcessStatus.completed
            proc.exit_code = outcome.get("exit_code")
            proc.signal = outcome.get("signal")
            return proc

        def read_output():
            nonlocal stdout_offset
            if handle is None:
                return ShellProcessRead(delta="")
            delta, stdout_offset, lossy = handle.read_from(stdout_offset)
            return ShellProcessRead(delta=delta, lossy=lossy)

        def kill():
            if proc.status != ShellProcessStatus.running:
                return False
            proc.status = ShellProcessStatus.killed
            if handle is not None:
                handle.terminate()
            return True

        def result():
            """前台投影：只在基础设施失败时 reject（index.ts:370-374）。"""
            if "value" in result_box:
                return result_box["value"]
            if handle is None:
                # 与真实版一致：spawn 没产出进程，投影抛原始错误
                raise spawn_error
            outcome = handle.done
            result_box["value"] = ShellRunResult(
                exit_code=outcome.get("exit_code"),
                stdout=(outcome.get("stdout") or "")[: spec.stdout_max_bytes],
                stderr=(outcome.get("stderr") or "")[: self.config.max_output_bytes],
                signal=outcome.get("signal"),
                timeout_ms=spec.timeout_ms,
                **classify(),
            )
            return result_box["value"]

        # [教学简化] Python 允许用实例属性覆盖类方法，这里把 provider 内部的闭包
        # 绑成句柄方法；生产代码应定义真实的 ShellExecution 子类。
        proc.done = done
        proc.read_output = read_output
        proc.kill = kill
        proc.result = result
        return proc

    # -- spawnSpec：组装 argv 与 env 合并顺序（index.ts:151-174） --

    def spawn_spec(self, spec: ShellExecSpec, argv, signal=None):
        """spawn 规格：argv + cwd + stdio 预算 + graceMs + env（index.ts:151-174）。"""
        return {
            "argv": argv,
            "cwd": spec.workdir,
            "stdio": {
                "stdin": spec.stdin,
                "stdout": {"maxBytes": spec.stdout_max_bytes},
                # stderr 用自己的输出上限，不跟随 stdout_max_bytes（index.ts:165）
                "stderr": {"maxBytes": self.config.max_output_bytes},
            },
            "graceMs": self.config.grace_ms,
            # deadline 走 signal 进 spawn，spawnSpec 本身不带 timeout 字段
            "signal": signal,
            # 一个显式 env：受信 dshEnv 压过调用方 env，也压过终端覆盖（index.ts:172）
            "env": {**ENV_OVERRIDES, **(spec.env or {}), **(spec.dsh_env or {})},
        }
