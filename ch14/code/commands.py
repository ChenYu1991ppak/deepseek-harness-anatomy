"""第 14 章教学重构：命令平面（人类介入的第三种形态）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（packages/interaction/commands/src/，行号见 notes §2.3）：
- parseCommand   ↔ index.ts:102（以 / 开头的输入解析为命令）
- CommandRuntime ↔ 注册为 ctx.commands
- register       ↔ index.ts:245
- execute        ↔ index.ts:296

[教学简化] 真实版还有幂等 id（mintCommandId index.ts:341）与一次性命令；
教学版只保留 register + parse → dispatch 两步。
"""
from __future__ import annotations

import os
import sys

_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from cordis import Service  # noqa: E402


def parse_command(text):
    """parseCommand（index.ts:102）：以 / 开头的输入 → (命令名, 参数串)；否则 None。"""
    if not isinstance(text, str) or not text.startswith("/"):
        return None
    parts = text[1:].split(None, 1)
    if not parts or not parts[0]:
        return None
    return parts[0], (parts[1] if len(parts) > 1 else "")


class CommandRuntime(Service):
    """ctx.commands 服务：命令注册表 + 分发。"""

    def __init__(self, ctx):
        super().__init__(ctx, "commands")
        self._handlers = {}

    def register(self, name, handler):
        """注册命令（index.ts:245），handler 签名 handler(参数串) -> 文本结果。"""
        self._handlers[name] = handler

    def execute(self, text):
        """执行命令（index.ts:296）：parse → dispatch；非命令/未知命令返回可读消息。"""
        parsed = parse_command(text)
        if parsed is None:
            return f"不是命令：{text!r}"
        name, args = parsed
        handler = self._handlers.get(name)
        if handler is None:
            return f"未知命令：/{name}"
        return handler(args)
