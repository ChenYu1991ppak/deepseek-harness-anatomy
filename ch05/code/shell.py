"""第 5 章教学重构：capability seam 三角色 —— Service Definition（shell）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（packages/shell/shell/src/）：
- ShellExecutor          ↔ index.ts:65（抽象类；super(ctx,'shell') 挂到 ctx.shell）
- get sandboxMode()      ↔ index.ts:75（默认 None）
- abstract resolve/run/start ↔ index.ts:85 / :93 / :100
- ShellExecRequest       ↔ types.ts:38（仅 command 必填）
- ShellExecSpec          ↔ types.ts:86（resolve 产物，全必填）
- ShellRunResult         ↔ types.ts:113
- ShellProcess / ShellProcessRead / ShellProcessStatus ↔ types.ts:161 / :144 / :141
- parseExitStatus / ParsedExitStatus ↔ render.ts:36 / :12（共享 marker 契约）

[教学决策] shell seam 是「方法调用式」seam：resolve/run/start 三个抽象方法，
消费者直接方法调用，而非第 4 章 tools 的「事件瀑布式」管线。
"""
from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

# 复用第 1 章：Service 构造即注册（cordis.py:271–284）。
_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)
from cordis import Service  # noqa: E402


# ---------- 词汇类型：定义者与实现者/消费者共享的「词表」 ----------


@dataclass(frozen=True)
class ShellExecRequest:
    """调用方请求：仅 command 必填，其余可选（types.ts:38）。"""

    command: str
    workdir: str | None = None
    timeout_ms: int | None = None
    stdout_max_bytes: int | None = None
    signal: object | None = None
    stdin: str | None = None
    env: dict | None = None
    dsh_env: dict | None = None
    sandbox_policy: object | None = None


@dataclass(frozen=True)
class ShellExecSpec:
    """resolve 后的全量 spec：所有字段都有值（types.ts:86）。"""

    command: str
    workdir: str
    timeout_ms: int
    stdout_max_bytes: int
    signal: object | None
    stdin: str | None
    env: dict | None
    dsh_env: dict | None
    sandbox_policy: object | None


@dataclass(frozen=True)
class ShellRunResult:
    """前台结果：非零退出/超时/中止都 resolve 成它，永不 reject（types.ts:113）。"""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    signal: str | None = None
    timed_out: bool = False
    aborted: bool = False
    timeout_ms: int | None = None


class ShellProcessStatus(str, Enum):
    """后台句柄状态（types.ts:141）。"""

    running = "running"
    completed = "completed"
    killed = "killed"


@dataclass(frozen=True)
class ShellProcessRead:
    """后台增量读（types.ts:144）：delta + 是否溢出 + 溢出落盘路径。"""

    delta: str
    lossy: bool = False
    stdout_spill_path: str | None = None
    stderr_spill_path: str | None = None


@dataclass
class ShellProcess:
    """后台句柄（types.ts:161）：立即返回，状态走 running → completed | killed。"""

    status: ShellProcessStatus
    exit_code: int | None = None
    signal: str | None = None
    _outcome: object | None = None  # [教学简化] 承载 spawn 结果，供 done/read_output 读取

    def done(self):
        """后台终态：永不 reject，spawn 失败也 settle 成 killed（types.ts:161）。"""
        return self._outcome

    def read_output(self):
        """增量读：合并 stdout/stderr 的 delta + lossy + spill 路径（types.ts:144）。"""
        raise NotImplementedError  # [教学简化] 教学桩由 provider 提供具体读取

    def kill(self):
        """终止进程组（types.ts:161）。"""
        raise NotImplementedError  # [教学简化] 教学桩由 provider 提供具体 kill


@dataclass(frozen=True)
class ParsedExitStatus:
    """parse_exit_status 产物（render.ts:12）。"""

    body: str
    exit_code: int = 0
    signal: str | None = None
    timed_out: bool = False  # [教学决策] 超时回读分支，对应 render_result 的独立超时 marker


def parse_exit_status(text: str) -> ParsedExitStatus:
    """共享 marker 契约：剥离尾部 [killed by signal: X] / [timed out after Nms] / [exit code: N]（render.ts:36–42）。

    tool-pwsh 与 tool-bash 复用同一份，保证展示层能回读退出状态。
    [教学决策] 教学版在源码两个分支之外补 [timed out after Nms] 解析分支，
    与 render_result 的独立超时 marker 对应，使写读契约闭合。
    """
    import re

    m = re.search(r"\n\[killed by signal: ([^\]\n]+)\]$", text)
    if m is not None:
        return ParsedExitStatus(body=text[: m.start()], signal=m.group(1))
    m = re.search(r"\n\[timed out after \d+ms\]$", text)
    if m is not None:
        return ParsedExitStatus(body=text[: m.start()], timed_out=True)
    m = re.search(r"\n\[exit code: (\d+)\]$", text)
    if m is not None:
        return ParsedExitStatus(body=text[: m.start()], exit_code=int(m.group(1)))
    return ParsedExitStatus(body=text, exit_code=0)


# ---------- Service Definition：抽象接口 + ctx.shell 唯一绑定 ----------


class ShellExecutor(Service, ABC):
    """shell seam 接口（index.ts:65）：每 context 唯一，挂到 ctx.shell。

    三个抽象方法即「方法调用式」seam 的契约面：
    - resolve(request) -> spec  填默认值/封顶成全量 spec
    - run(spec)      -> 前台结果，永不 reject
    - start(spec)    -> 后台句柄，立即返回
    换 provider 就是换一个实现了这三个方法的类，消费者与定义一行不改。
    """

    def __init__(self, ctx):
        super().__init__(ctx, "shell")  # 挂到 ctx.shell（index.ts:66–68）

    @property
    def sandbox_mode(self):
        """可选能力：默认无沙箱返回 None（index.ts:75）。"""
        return None

    @abstractmethod
    def resolve(self, request: ShellExecRequest) -> ShellExecSpec:
        """把「请求」填默认值/封顶成「spec」（index.ts:85）。"""
        raise NotImplementedError

    @abstractmethod
    def run(self, spec: ShellExecSpec) -> ShellRunResult:
        """前台执行，resolve 后才跑；非零退出/超时/中止都 resolve 不 reject（index.ts:93）。"""
        raise NotImplementedError

    @abstractmethod
    def start(self, spec: ShellExecSpec) -> ShellProcess:
        """后台执行，立即返回 ShellProcess 句柄（index.ts:100）。"""
        raise NotImplementedError
