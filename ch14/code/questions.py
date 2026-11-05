"""第 14 章教学重构：userQuestions 接缝（「请回答/复核」通道）。

仅标准库，Python 3.10+。运行：python3 -m main

源码对应（HEAD 8bd073392，packages/interaction/user-questions/src/）：
- UserQuestionService ↔ 注册为 ctx.userQuestions
- ask()               ↔ index.ts:291（ask 入口：信号检查 → 空问题检查 → 活根检查 → intent 校验 → 瀑布）
- 活根检查             ↔ index.ts:137（assertLiveRoot：agent 不是注册表里的同一个活实例就拒；subagent 出身也拒）
- intent 校验          ↔ index.ts:300-337（每个问题的 intent 必须在 options 里找到对应标签）
- 唯一应答者接缝       ↔ index.ts:343-351（ctx.waterfall('user-questions/request', ...)，无人接就 NO_PROVIDER）

与 approval 通道的分工：approval 问「能否做」（允许/拒绝），
userQuestions 问「请回答/复核」（任意回答）。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from cordis import Service  # noqa: E402

INTENT_PLAN_REVIEW = "plan-review"  # plan-mode 复核问题的 intent.kind（plan-mode/index.ts:329）


@dataclass(frozen=True)
class UserQuestion:
    """一次提问：编号 + 内容 + 意图。"""

    id: int
    content: str
    intent: str


class UserQuestionError(RuntimeError):
    """userQuestions 通道错误（真实版带 code：NO_PROVIDER / BAD_INTENT / ASK_ABORTED …）。"""


class UserQuestionService(Service):
    """ctx.userQuestions 服务。

    真实版把请求交给 user-questions/request 瀑布，链上可以挂多个应答者，
    一个监听器可以用 next() 交给下游；教学版简化为单一 provider，
    set_provider 是整体替换而非逐层委托。
    """

    def __init__(self, ctx):
        super().__init__(ctx, "userQuestions")
        self._provider = None
        self._seq = 0

    def set_provider(self, provider):
        """替换 UI provider（唯一接缝），签名 provider(question) -> 回答。"""
        self._provider = provider

    def ask(self, content, intent="answer"):
        """发起提问（index.ts:291）。"""
        if not content.strip():
            raise UserQuestionError("ask 至少需要一个非空问题")  # 空问题检查（index.ts:295）
        self._seq += 1
        question = UserQuestion(id=self._seq, content=content, intent=intent)
        self.ctx.emit("userQuestions/asked", question)  # [教学决策] 事件名
        if self._provider is None:
            # 无人接受请求：真实版抛 code=NO_PROVIDER（index.ts:343）
            raise UserQuestionError("no user-questions answerer accepted the request")
        return self._provider(question)  # 交给应答者：渲染→收集回答（教学版只有一个）
