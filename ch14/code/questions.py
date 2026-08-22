"""第 14 章教学重构：userQuestions 接缝（「请回答/复核」通道）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（packages/interaction/user-questions/src/，行号见 notes §2.4）：
- UserQuestionService ↔ 注册为 ctx.userQuestions
- ask()               ↔ index.ts:92（活根检查 L100-113，intent 校验 L121-135，委托唯一 UI provider）
- intent plan-review  ↔ AskUserQuestionItem（types.ts）（plan-mode 复核计划复用本通道）

与 approval 通道的分工（notes §6 第 2 条）：approval 问「能否做」（允许/拒绝），
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

INTENT_PLAN_REVIEW = "plan-review"  # AskUserQuestionItem.intent（types.ts）：计划复核意图


@dataclass(frozen=True)
class UserQuestion:
    """一次提问：编号 + 内容 + 意图。"""

    id: int
    content: str
    intent: str


class UserQuestionError(RuntimeError):
    """provider 缺失等 userQuestions 通道错误。

    [教学决策] notes 未给出 provider 缺失时的真实行为；教学版抛异常显式暴露装配遗漏。
    """


class UserQuestionService(Service):
    """ctx.userQuestions 服务。

    provider 是唯一接缝：ask() 委托单一 UI provider 渲染问题 → 收集并返回回答。
    真实项目同一时刻只有一个 provider；教学版 set_provider 整体替换。
    """

    def __init__(self, ctx):
        super().__init__(ctx, "userQuestions")
        self._provider = None
        self._seq = 0

    def set_provider(self, provider):
        """替换 UI provider（唯一接缝），签名 provider(question) -> 回答。"""
        self._provider = provider

    def ask(self, content, intent="answer"):
        """发起提问（index.ts:92）。"""
        self._seq += 1
        question = UserQuestion(id=self._seq, content=content, intent=intent)
        self.ctx.emit("userQuestions/asked", question)  # [教学决策] 事件名
        if self._provider is None:
            raise UserQuestionError("未注册 UI provider")  # [教学决策]
        return self._provider(question)  # 委托唯一 UI provider：渲染→收集回答
