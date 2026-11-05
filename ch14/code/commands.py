"""第 14 章教学重构：commands 平面（人类介入的第三种形态）。

仅标准库，Python 3.10+。运行：python3 -m main

源码对应（HEAD 8bd073392，packages/interaction/commands/src/）：
- parseCommand   ↔ index.ts:125（只认「/ + 小写名 + 空白或行尾」；尾部输入原样保留）
- CommandRuntime ↔ 注册为 ctx.commands
- register       ↔ index.ts:285（返回 disposer，注册是 effect）
- execute        ↔ index.ts:361（分发并配对 command/run / command/done）
- mintCommandId  ↔ index.ts:446（每次执行分配一个命令 id，供完成事件配对）

[教学简化] 真实版还有幂等 id 与一次性命令、文件回执解析等；
教学版只保留 register + parse → dispatch 两步。
"""
from __future__ import annotations

import os
import re
import sys

_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from cordis import Service  # noqa: E402

# 与 parseCommand 同形的正则：命令名后必须紧跟空白或行尾（index.ts:126）
_COMMAND_RE = re.compile(r"^/([a-z][a-z0-9_-]*)(?=$|[\t\n\r ])")


def parse_command(line):
    """parseCommand（index.ts:125）：以 / 开头的输入 → (命令名, 原始参数串)；否则 None。"""
    if not isinstance(line, str):
        return None
    match = _COMMAND_RE.match(line)
    if match is None:
        return None
    return match.group(1), line[match.end():]


class CommandRuntime(Service):
    """ctx.commands 服务：命令注册表 + 分发。"""

    def __init__(self, ctx):
        super().__init__(ctx, "commands")
        self._handlers = {}

    def register(self, name, handler):
        """注册命令（index.ts:285），handler 签名 handler(参数串) -> 文本结果。"""
        self._handlers[name] = handler
        return lambda: self._handlers.pop(name, None)  # 真实版返回 disposer

    def execute(self, text):
        """执行命令（index.ts:361）：parse → dispatch；非命令/未知命令返回可读消息。"""
        parsed = parse_command(text)
        if parsed is None:
            return f"不是命令：{text!r}"
        name, args = parsed
        handler = self._handlers.get(name)
        if handler is None:
            return f"未知命令：/{name}"
        return handler(args)  # 真实版在这里 mintCommandId 并落 command/run
