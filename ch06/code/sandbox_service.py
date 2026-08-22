"""第 6 章教学重构：sandbox seam —— 隔离能力。

仅标准库，Python 3.10+。运行：python3 main.py

进程与文件都管好了，但 agent 能跑任意命令、碰任意文件——还缺一道围栏。
sandbox seam 的职责只有一件事：把 argv 包成「runner + profile + -- + argv」，
真正的隔离由平台 runner（bwrap/Landlock/Seatbelt）在内核层施加。

源码对应（packages/sandbox/）：
- SandboxProvider            ↔ abstract SandboxProvider（sandbox/src/index.ts:158）
- confine(argv, policy)      ↔ confine（index.ts:158/:175）
- SandboxMode 三模式          ↔ read-only/workspace-write/danger-full-access（index.ts:29）
- SandboxPolicy 按次携带      ↔ SandboxPolicy（index.ts:69）
- SandboxUnavailableError    ↔ SANDBOX_UNAVAILABLE fail-closed（index.ts:124/:131）
- approve_escalation 严格加宽 ↔ approveEscalation + WIDER_MODES（escalation.ts:157/:28）

[教学简化] 真实版有平台 runner 链（bwrap→landlock probe 仲裁 / seatbelt / windows-acl）、
denialSignatures 识别、escalation 审批流；教学版用 mock_runner.py 充当 runner，
只演示「包裹结构 + fail-closed + 按次携带 policy」主干。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from enum import Enum

_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from cordis import Service  # noqa: E402

__all__ = [
    "ConfinedArgv",
    "SandboxMode",
    "SandboxPolicy",
    "SandboxProvider",
    "SandboxUnavailableError",
]


class SandboxMode(Enum):
    """三模式（index.ts:29）：只读 / 工作区写 / 全放行。"""

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


# 严格加宽序（escalation.ts:28 WIDER_MODES）：只能往更宽走。
_WIDER_MODES = {
    SandboxMode.READ_ONLY: {SandboxMode.WORKSPACE_WRITE, SandboxMode.DANGER_FULL_ACCESS},
    SandboxMode.WORKSPACE_WRITE: {SandboxMode.DANGER_FULL_ACCESS},
    SandboxMode.DANGER_FULL_ACCESS: set(),
}


@dataclass
class SandboxPolicy:
    """按次携带的策略（index.ts:69）：provider 视为完全指定，不与默认合并。"""

    mode: SandboxMode
    workspace_root: str
    allowed_paths: list = field(default_factory=list)


@dataclass
class ConfinedArgv:
    """confine 的产物：包裹后的 argv + 本次 policy。"""

    argv: list
    policy: SandboxPolicy


class SandboxUnavailableError(Exception):
    """fail-closed（index.ts:124/:131）：沙箱不可用就拒绝执行，绝不裸奔。"""


class SandboxProvider(Service):
    """ctx.sandbox seam：confine(argv, policy) 包住 runner。"""

    inject = []

    def __init__(self, ctx, runner_argv=None):
        super().__init__(ctx, "sandbox")  # 构造即注册到 ctx.sandbox
        # 平台 runner：真实版是 bwrap/landlock/seatbelt 可执行文件；
        # 教学版用 mock_runner.py（原样 exec '--' 之后的 argv）。
        self._runner_argv = runner_argv
        self._approved = {}  # 升级审批记录：key → 已批准的 mode

    @property
    def available(self):
        return self._runner_argv is not None

    def confine(self, argv, policy):
        """把 argv 包成「runner + profile + -- + argv」（index.ts:175）。

        fail-closed：没有可用 runner 直接抛 SandboxUnavailableError，
        而不是退化成裸跑。
        """
        if not self.available:
            raise SandboxUnavailableError(
                "SANDBOX_UNAVAILABLE: 无可用 runner，拒绝执行（fail-closed）"
            )
        profile = self._render_profile(policy)
        return ConfinedArgv([*self._runner_argv, *profile, "--", *argv], policy)

    @staticmethod
    def _render_profile(policy):
        """把 policy 渲染成 runner 的命令行 profile 参数。"""
        args = ["--mode", policy.mode.value, "--workspace", policy.workspace_root]
        for path in policy.allowed_paths:
            args += ["--allow", path]
        return args

    def approve_escalation(self, key, current, requested):
        """升级严格加宽（escalation.ts:157）：只批准往更宽模式的请求。"""
        if requested not in _WIDER_MODES[current]:
            raise ValueError(
                f"escalation 拒绝：{current.value} → {requested.value} 不是严格加宽"
            )
        self._approved[key] = requested
        return requested
