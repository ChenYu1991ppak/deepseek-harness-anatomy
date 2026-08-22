"""第 6 章教学重构：subprocess seam —— 受管进程树。

仅标准库，Python 3.10+。运行：python3 main.py

第 5 章 bash_local.py 用 SubprocessStub 教学桩占位（其 docstring 写明
「真实进程树管理留到第 6 章」）；本文件把占位换成真实 seam，签名与
bash_local.py 消费的 spawn(argv, workdir, env, timeout_ms) → 句柄 完全一致，
因此 LocalBashExecutor 一行不改即可换用。

源码对应（packages/subprocess/）：
- SubprocessService          ↔ LocalSubprocessRuntime（subprocess-local/src/index.ts:37，disposeManagedProcesses :79）
- spawn / SubprocessHandle   ↔ spawnSubprocess / SubprocessHandle（spawn.ts:326 / types.ts:167）
- terminate（SIGTERM→SIGKILL）↔ spawn.ts:439；waitForExit ↔ spawn.ts:507
- OutputCollector（tail+spill）↔ spawn.ts:104
- scrub_env / _SENSITIVE_ENV_RE / DSH_ENV_PREFIX ↔ scrubbedParentEnv / SENSITIVE_ENV_PATTERN / DSH_ENV_PREFIX（index.ts:60/:44、types.ts:13）
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time

# 复用第 1 章 Service（构造即注册）。
_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from cordis import Service  # noqa: E402

__all__ = [
    "DSH_ENV_PREFIX",
    "OutputCollector",
    "SubprocessHandle",
    "SubprocessService",
    "scrub_env",
]

# 凭据形变量名（index.ts:44 SENSITIVE_ENV_PATTERN）。
_SENSITIVE_ENV_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH)", re.I)
# agent 自身前缀（types.ts:13）：与凭据形变量一并从父环境剔除，靠显式 env 重新注入。
DSH_ENV_PREFIX = "DSH_"


def scrub_env(parent_env):
    """剔除父环境里的凭据形变量与 DSH_* 前缀变量（index.ts:60 scrubbedParentEnv）。

    显式 env 在 scrub 之后合并——父环境只贡献 PATH 这类基础变量，
    敏感值一律不自动透传给子进程。
    """
    return {
        key: value
        for key, value in parent_env.items()
        if not _SENSITIVE_ENV_RE.search(key) and not key.startswith(DSH_ENV_PREFIX)
    }


class OutputCollector:
    """子进程输出收集器：tail + offset 非消耗读（spawn.ts:104）。

    [教学简化] 真实版超出 max_bytes 的旧输出 spill 到随机临时文件（0600，'wx' 独占创建）；
    教学版只在内存保留尾部 max_bytes，省略落盘。
    """

    def __init__(self, max_bytes=64 * 1024):
        self._chunks = []
        self._read_pos = 0  # read_delta 已消费到的字节下标
        self._max = max_bytes

    def push(self, chunk: bytes):
        self._chunks.append(chunk)

    def read_delta(self) -> str:
        """offset-based 增量读：返回自上次读以来的新增并推进读位置（非消耗整条流）。"""
        data = b"".join(self._chunks)
        delta = data[self._read_pos:]
        self._read_pos = len(data)
        return delta.decode(errors="replace")

    def tail_text(self) -> str:
        """最终结果只保留尾部 max_bytes（tail）。"""
        data = b"".join(self._chunks)
        return data[-self._max:].decode(errors="replace")


def _pump(stream, collector):
    """读线程：把管道字节持续推入收集器，直到 EOF。"""
    fd = stream.fileno()
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        collector.push(chunk)


class SubprocessHandle:
    """spawn 返回的句柄：done / read_output / kill + 唯一终止动词 terminate。

    对应 types.ts:167 SubprocessHandle（terminate :186 / waitForExit :193）。
    """

    def __init__(self, proc, stdout_collector, stderr_collector, grace_ms, timeout_ms):
        self._proc = proc
        self._stdout = stdout_collector
        self._stderr = stderr_collector
        self._grace_ms = grace_ms
        self._timeout_ms = timeout_ms
        self._result = None
        self._killed = False
        self._threads = []
        self.spawn_id = None

    @property
    def pid(self):
        """detached 树根 pid（== 进程组 pgid，供观察整树存活）。"""
        return self._proc.pid

    # -- done：阻塞等待整树收敛，返回统一结果（永不抛异常） --

    @property
    def done(self):
        if self._result is None:
            self._result = self._wait_for_exit()
        return self._result

    def _wait_for_exit(self):
        """waitForExit（spawn.ts:507）：观察进程退出；超时则 terminate 升级。"""
        timeout_s = self._timeout_ms / 1000 if self._timeout_ms else None
        timed_out = False
        try:
            self._proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            self.terminate()
            self._proc.wait()  # 升级后必然退出，收尸
        for thread in self._threads:  # 等读线程把管道残余读完
            thread.join(timeout=1.0)
        return {
            "exit_code": self._proc.returncode,
            "stdout": self._stdout.tail_text(),
            "stderr": self._stderr.tail_text(),
            "timed_out": timed_out,
            "killed": self._killed,
        }

    def read_output(self) -> str:
        """增量读新增输出（供后台进程轮询）。"""
        return self._stdout.read_delta()

    # -- 终止：kill 是入口，terminate 是唯一终止动词 --

    def kill(self):
        """显式终止（消费者语义）：标记 killed 后走 terminate 升级。"""
        self._killed = True
        self.terminate()

    def terminate(self):
        """唯一终止动词（spawn.ts:439）：SIGTERM → grace 窗口 → SIGKILL。

        作用于整个进程组（detached 树根），而非只杀直接子进程：
        领导者退出 ≠ 树退出，升级必须跨整树存活。
        """
        if self._proc.poll() is not None:
            return  # 已退出
        try:
            pgid = os.getpgid(self._proc.pid)
        except (ProcessLookupError, PermissionError):
            return
        try:
            os.killpg(pgid, signal.SIGTERM)  # 第一段：礼貌终止整组
        except ProcessLookupError:
            return
        deadline = time.monotonic() + self._grace_ms / 1000  # grace 窗口
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                return
            time.sleep(0.01)
        try:
            os.killpg(pgid, signal.SIGKILL)  # 第二段：强制终止整组
        except ProcessLookupError:
            pass


class SubprocessService(Service):
    """ctx.subprocess seam：受管进程树 + 后台登记 + dispose 兜底回收。

    对应 LocalSubprocessRuntime（subprocess-local/src/index.ts:37）。
    """

    inject = []

    def __init__(self, ctx, config=None):
        super().__init__(ctx, "subprocess")  # 构造即注册到 ctx.subprocess
        self._managed = {}  # 后台登记：spawn_id → 句柄
        self._next_id = 0
        self._grace_ms = (config or {}).get("grace_ms", 3000)
        # dispose 兜底回收（index.ts:79 disposeManagedProcesses）：
        # context 卸载时 terminate 所有仍存活的受管进程，杜绝进程泄漏。
        ctx.effect(self._dispose_managed, label="subprocess:dispose-managed")

    def spawn(self, argv, workdir=None, env=None, timeout_ms=None):
        """spawn 全显式、无默认（index.ts:130）：先 scrub 父环境再合并显式 env。"""
        full_env = scrub_env(dict(os.environ))
        if env:
            full_env.update(env)
        proc = subprocess.Popen(
            argv,
            cwd=workdir,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # detached 树根：自成进程组（spawn.ts:350-361）
        )
        stdout_c, stderr_c = OutputCollector(), OutputCollector()
        handle = SubprocessHandle(proc, stdout_c, stderr_c, self._grace_ms, timeout_ms)
        handle._threads = [
            threading.Thread(target=_pump, args=(proc.stdout, stdout_c), daemon=True),
            threading.Thread(target=_pump, args=(proc.stderr, stderr_c), daemon=True),
        ]
        for thread in handle._threads:
            thread.start()
        # 后台登记：每个受管进程都有唯一 spawn_id，dispose 时统一回收。
        handle.spawn_id = self._next_id
        self._managed[self._next_id] = handle
        self._next_id += 1
        return handle

    def _dispose_managed(self):
        def cleanup():
            for handle in list(self._managed.values()):
                handle.terminate()
            self._managed.clear()

        return cleanup
