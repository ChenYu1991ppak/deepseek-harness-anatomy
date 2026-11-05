"""第 5 章教学重构：capability seam 三角色 —— Service Definition（shell）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（路径从目标仓库根起）：
- packages/shell/shell/src/index.ts:64        ShellExecutor 抽象类
- packages/shell/shell/src/index.ts:65-67    构造函数 super(ctx, 'shell')：挂到 ctx.shell
- packages/shell/shell/src/index.ts:74       get sandboxMode()，默认 undefined
- packages/shell/shell/src/index.ts:84       abstract resolve(request)
- packages/shell/shell/src/index.ts:93       abstract execute(spec)
- packages/shell/shell/src/types.ts:61       ShellExecRequest（仅 command 必填）
- packages/shell/shell/src/types.ts:111      ShellExecSpec（resolve 产物，核心字段必填）
- packages/shell/shell/src/types.ts:140      ShellRunResult
- packages/shell/shell/src/types.ts:168      ShellProcessStatus
- packages/shell/shell/src/types.ts:171      ShellProcessRead
- packages/shell/shell/src/types.ts:188      ShellProcess（后台句柄）
- packages/shell/shell/src/types.ts:226      ShellExecution（句柄 + result() 前台投影）
- packages/shell/shell/src/render.ts:13      ParsedExitStatus
- packages/shell/shell/src/render.ts:37-43   parseExitStatus

[教学决策] shell seam 是「方法调用式」seam：resolve/execute 两个抽象方法，
消费者直接方法调用，而非第 4 章 tools 的「事件瀑布式」管线。
「前台还是后台」不是两个方法，而是调用方等不等 result()——见 index.ts:42 的类注释。
"""
from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

# 复用第 1 章：Service 构造即注册（ch01/code/cordis.py:241）。
_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)
from cordis import Service  # noqa: E402


# ---------- 词汇类型：定义者与实现者/消费者共享的「词表」 ----------


@dataclass(frozen=True)
class ShellExecRequest:
    """调用方请求：仅 command 必填，其余可选（types.ts:61）。"""

    command: str
    workdir: str | None = None
    timeout_ms: int | None = None
    on_expiry: str = "kill"  # 'kill' 到点杀；'none' 不装 deadline（types.ts:68）
    stdout_max_bytes: int | None = None
    signal: object | None = None
    stdin: str | None = None
    env: dict | None = None
    dsh_env: dict | None = None
    sandbox_policy: object | None = None


@dataclass(frozen=True)
class ShellExecSpec:
    """resolve 后的完整 spec：核心字段必填，signal/stdin/env/dsh_env 可缺省（types.ts:111）。"""

    command: str
    workdir: str
    timeout_ms: int
    on_expiry: str
    stdout_max_bytes: int
    signal: object | None
    stdin: str | None
    env: dict | None
    dsh_env: dict | None
    sandbox_policy: object | None


@dataclass(frozen=True)
class ShellRunResult:
    """前台结果：非零退出/超时/中止都 resolve 成它，永不 reject（types.ts:140）。

    exit_code 为 None 表示进程死于信号（或准备阶段就超时），
    真实终止原因由 signal 字段承载。
    """

    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    signal: str | None = None
    timed_out: bool = False
    aborted: bool = False
    timeout_ms: int | None = None


class ShellProcessStatus(str, Enum):
    """后台句柄状态（types.ts:168）。"""

    running = "running"
    completed = "completed"
    killed = "killed"


@dataclass(frozen=True)
class ShellProcessRead:
    """后台增量读（types.ts:171）：delta + 是否溢出 + 溢出落盘路径。"""

    delta: str
    lossy: bool = False
    stdout_spill_path: str | None = None
    stderr_spill_path: str | None = None


class ShellProcess:
    """后台句柄（types.ts:188）：立即返回，状态走 running → completed | killed。"""

    def __init__(self, status: ShellProcessStatus):
        self.status = status
        self.exit_code: int | None = None
        self.signal: str | None = None

    def done(self):
        """等待终态并回读结果；永不 reject（types.ts:199）。"""
        raise NotImplementedError  # 由 provider 的具体句柄实现

    def read_output(self) -> ShellProcessRead:
        """增量读：连续两次读不会重复交付同一段输出（types.ts:207）。"""
        raise NotImplementedError  # 由 provider 的具体句柄实现

    def kill(self) -> bool:
        """终止进程；已经结束时返回 False（幂等，types.ts:219）。"""
        raise NotImplementedError  # 由 provider 的具体句柄实现


class ShellExecution(ShellProcess):
    """execute() 返回的那一个句柄：活的 ShellProcess + 一个前台投影 result()（types.ts:226）。

    消费者等 result() 就是「前台」：settle 时拿到切分好的 stdout/stderr 与首因分类；
    不调它、只留着句柄就是「后台」：随时 read_output/kill。
    """

    def result(self) -> ShellRunResult:
        """前台投影：进程关闭时 settle（types.ts:237）。"""
        raise NotImplementedError  # 由 provider 的具体句柄实现


@dataclass(frozen=True)
class ParsedExitStatus:
    """parse_exit_status 产物（render.ts:13）：body 是剥掉 marker 的输出。"""

    body: str
    exit_code: int = 0
    signal: str | None = None


def parse_exit_status(text: str) -> ParsedExitStatus:
    """共享 marker 契约：剥离尾部 [killed by signal: X] / [exit code: N]（render.ts:37-43）。

    tool-pwsh 与 tool-bash 复用同一份，保证展示层能回读退出状态。
    没有 marker 就是干净退出 0——所以渲染端对成功不写 [exit code: 0]。
    超时、沙箱拒绝这类 marker 留在 body 里：它们承载的事实没有对应的展示位置。
    """
    import re

    m = re.search(r"\n\[killed by signal: ([^\]\n]+)\]$", text)
    if m is not None:
        return ParsedExitStatus(body=text[: m.start()], signal=m.group(1))
    m = re.search(r"\n\[exit code: (\d+)\]$", text)
    if m is not None:
        return ParsedExitStatus(body=text[: m.start()], exit_code=int(m.group(1)))
    return ParsedExitStatus(body=text, exit_code=0)


# ---------- Service Definition：抽象接口 + ctx.shell 唯一绑定 ----------


class ShellExecutor(Service, ABC):
    """shell seam 接口（index.ts:64）：每 context 唯一，挂到 ctx.shell。

    两个抽象方法即「方法调用式」seam 的契约面：
    - resolve(request) -> spec   填默认值/封顶成全量 spec
    - execute(spec)   -> 句柄    准备并启动命令，返回 live 句柄
    换 provider 就是换一个实现了这两个方法的类，消费者与定义一行不改。
    """

    def __init__(self, ctx):
        super().__init__(ctx, "shell")  # 挂到 ctx.shell（index.ts:65-67）

    @property
    def sandbox_mode(self):
        """可选能力：默认无沙箱返回 None（index.ts:74）。"""
        return None

    @abstractmethod
    def resolve(self, request: ShellExecRequest) -> ShellExecSpec:
        """把「请求」填默认值/封顶成「spec」（index.ts:84）。"""
        raise NotImplementedError

    @abstractmethod
    def execute(self, spec: ShellExecSpec) -> ShellExecution:
        """准备并启动命令，返回活句柄（index.ts:93）；准备失败才抛。"""
        raise NotImplementedError
