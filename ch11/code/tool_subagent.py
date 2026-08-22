"""第 11 章教学重构：subagent 委派工具（消费层）。

源码对应：
- tool-subagent 包 ↔ packages/subagent/tool-subagent/src/index.ts
  - apply 把 subagent 工具注册进 ctx.tools ↔ index.ts:267
  - execute 调用 ctx.subagents.start ↔ index.ts:369
- tool-subagent-control 包（send_message / interrupt_agent）
  ↔ packages/subagent/tool-subagent-control/src/index.ts:27/80

[Educational simplification] 真实版工具参数经 JSON schema 校验；教学版参数是普通 dict。
[Educational simplification] 真实版的 parent 不来自模型参数，而来自执行上下文
exec.agent（execute，index.ts:369-370）——谁发起工具调用，谁就是 parent；
ch04 的 ToolExecution 不带调用者，教学版由 args["parent"] 显式传入。
"""
from __future__ import annotations

import os
import sys

# 复用第 4 章：ToolDefinition 是工具契约（tools.py:80）。
_CH04 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch04", "code"))
if _CH04 not in sys.path:
    sys.path.insert(0, _CH04)
from tools import ToolDefinition  # noqa: E402


class SubagentTool:
    """委派工具（tool-subagent 的教学版）：把「委派」暴露成模型可调用的工具。

    这是 seam 三角色的消费层：工具只面向 ctx.subagents.start 一个入口，
    不关心子 agent 由哪个传输、以什么方式启动。
    """

    name = "subagent"

    def __init__(self, ctx):
        self.ctx = ctx

    def apply(self):
        """把工具定义注册进 ctx.tools（apply，index.ts:267），返回注销 disposer。"""
        definition = ToolDefinition(
            name=self.name,
            description="把一个任务委派给子 agent 独立执行，返回它的结果",
            parameters={"provider": "传输名（spawn/fork）", "prompt": "任务描述"},
            execute=self.execute,
        )
        return self.ctx.tools.register(definition)

    def execute(self, args):
        """工具体：把工具参数翻译成 start 请求（execute，index.ts:369）。

        返回子 agent 的结算文本——它作为工具结果回到模型上下文，
        主 agent 由此「只拿到结果，不沾过程」。
        """
        provider_name = args.get("provider", "spawn")
        request = {"parent": args["parent"], "prompt": args["prompt"]}
        run = self.ctx.subagents.start(provider_name, request)
        return f"子 agent（{run.result.stop_reason}）：{run.result.text}"


class SubagentControlTool:
    """可续子 agent 的控制工具（tool-subagent-control 的教学版）。

    send_message ↔ followup（index.ts:27）；interrupt_agent ↔ interrupt（index.ts:80）。
    只有可续委派（start_continuable）产生的存活子 agent 才用得上它们。
    """

    def __init__(self, ctx):
        self.ctx = ctx

    def apply(self):
        """注册两个控制工具，返回合并的注销 disposer。"""
        dispose_send = self.ctx.tools.register(ToolDefinition(
            name="send_message",
            description="向存活的子 agent 追加一条消息并取回它的最新回复",
            parameters={"run_id": "运行 id", "message": "消息内容"},
            execute=self._send_message,
        ))
        dispose_interrupt = self.ctx.tools.register(ToolDefinition(
            name="interrupt_agent",
            description="中断并结算一个存活的子 agent",
            parameters={"run_id": "运行 id"},
            execute=self._interrupt_agent,
        ))
        return lambda: (dispose_send(), dispose_interrupt())

    def _send_message(self, args):
        result = self.ctx.subagents.followup(args["run_id"], args["message"])
        return f"子 agent 回复：{result.text}"

    def _interrupt_agent(self, args):
        self.ctx.subagents.kill(args["run_id"])
        return f"已中断 {args['run_id']}"
