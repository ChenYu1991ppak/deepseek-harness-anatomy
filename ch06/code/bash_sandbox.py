"""第 6 章教学重构：bash-sandbox provider —— sandbox seam 的消费方。

仅标准库，Python 3.10+。运行：python3 main.py

shell 在本章点到即止：bash-sandbox 不是新机制，而是 ctx.sandbox 的一个消费方。
它继承 LocalBashExecutor，只在 spawn 前把 argv 经 ctx.sandbox.confine() 包一层，
其余机制（resolve / env 合并 / 前后台 / 超时）全部复用父类。

「整 provider 迁移」：换执行器（LocalBashExecutor → SandboxBashExecutor）时，
工具层 tool_bash.py 与词汇层 shell.py 一行不改——消费者只认 ctx.shell 抽象。

源码对应（packages/shell/bash-sandbox/）：
- SandboxBashExecutor extends LocalBashExecutor ↔ shell/bash-sandbox/src/index.ts:44
- static inject=['subprocess','sandbox','sandboxPolicy'] ↔ index.ts:45
- run() / confine()                              ↔ index.ts:88 / :177
- 无 ctx.sandbox 时 fail-closed 抛 SandboxUnavailableError ↔ sandbox/src/index.ts:124/:131
"""
from __future__ import annotations

import os
import sys

# 复用第 5 章 bash_local.py 的 LocalBashExecutor 与 shell.py 词汇类型。
_CH05 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch05", "code"))
if _CH05 not in sys.path:
    sys.path.insert(0, _CH05)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from bash_local import LocalBashExecutor  # noqa: E402
from sandbox_service import (  # noqa: E402
    SandboxMode,
    SandboxPolicy,
    SandboxUnavailableError,
)

__all__ = ["SandboxBashExecutor"]


class SandboxBashExecutor(LocalBashExecutor):
    """bash-sandbox 实现（index.ts:44）：sandbox seam 的消费方。

    inject=['subprocess','sandbox'] 在 TS 由 cordis 注入；教学版构造函数显式注入。
    [教学简化] 真实版还注入 sandboxPolicy（按次策略）；教学版用固定 workspace-write 策略。
    """

    inject = ["subprocess", "sandbox"]

    def __init__(self, ctx, subprocess, sandbox, config=None):
        super().__init__(ctx, subprocess, config)  # 复用父类全部机制
        self._sandbox = sandbox
        self._policy = SandboxPolicy(
            mode=SandboxMode.WORKSPACE_WRITE,
            workspace_root=os.getcwd(),
        )

    def _confine(self, argv):
        """把 argv 经 ctx.sandbox.confine 包成「runner + profile + -- + argv」。

        fail-closed：没有 ctx.sandbox 直接抛 SandboxUnavailableError（sandbox/src/index.ts:124/:131），
        绝不退化成裸跑。
        """
        if self._sandbox is None:
            raise SandboxUnavailableError("bash-sandbox 需要 ctx.sandbox（fail-closed）")
        return self._sandbox.confine(argv, self._policy).argv

    # -- 只覆写 spawn 前的 argv 组装，其余机制全部复用父类 --

    def run_argv(self, spec, argv):
        return super().run_argv(spec, self._confine(argv))

    def start_argv(self, spec, argv):
        return super().start_argv(spec, self._confine(argv))
