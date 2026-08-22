"""第 12 章教学重构：skill 加载 —— Consumer（消费层）。

仅标准库，Python 3.10+。运行：python3 main.py

把 ctx.skills 暴露成模型可见的 skill 工具（走第 4 章 tools 管线），并把技能目录注入会话。

源码对应（packages/skill/tool-skill/src/index.ts）：
- SkillTool.apply（注册 skill 工具 + 会话注入）    ↔ index.ts:77
- skill 工具定义 defineTool({name:'skill'})       ↔ index.ts:81
- execute（按名加载 + 模型可调用校验）              ↔ index.ts:127
- ctx.tools.register(skillTool)                  ↔ index.ts:161
- pre-step #2 会话目录发布                        ↔ index.ts:213
- render_catalog_message（目录渲染）              ↔ index.ts:254
- render_skill_content（<skill_content> 规范块）  ↔ skill/src/index.ts:171

[教学决策 4] tool-skill 是 tools 管线里的一个工具（第 4 章 ToolDefinition/ToolRuntime），
与第 5 章 tool-bash 同型：工具 = seam 的模型侧消费面。skill 工具内部回读
ctx.skills.list/get，构成「工具 → 注册表」的消费闭环。
[教学简化 7] 真实 tool-skill 还有 pre-step #1 的 /name 手势显式调用（index.ts:177）与
目录增量更新 renderCatalogUpdate（:279）；教学版只做「目录一次性注入 + skill 工具加载」。
"""
from __future__ import annotations

import os
import sys

# 复用第 4 章：ToolDefinition 工具定义（tools.py:74）。
_CH04 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch04", "code"))
if _CH04 not in sys.path:
    sys.path.insert(0, _CH04)
from tools import ToolDefinition  # noqa: E402


def render_skill_content(definition):
    """把技能正文渲染成 <skill_content> 规范块（skill/src/index.ts:171 renderSkillContent）。

    模型读到这个块即「技能已加载」，正文里的步骤成为它接下来的操作指引。
    """
    return (
        f'<skill_content name="{definition.name}" provider="{definition.provider}">\n'
        f"{definition.content}\n"
        f"</skill_content>"
    )


def render_catalog_message(summaries):
    """把技能目录渲染成注入会话的文本（tool-skill/src/index.ts:254 renderCatalogMessage）。

    目录只含 name + description（元数据），不含正文；正文留到模型用 skill 工具按需加载。
    """
    if not summaries:
        return ""
    lines = ["可用技能（用 skill 工具按名加载正文）："]
    for summary in summaries:
        lines.append(f"- {summary.name}: {summary.description}")
    return "\n".join(lines)


class SkillTool:
    """skill 工具的装配与执行（tool-skill/src/index.ts:77 apply）。

    消费 ctx.skills：把注册表里的技能变成模型可调用的 skill 工具。
    """

    def __init__(self, ctx):
        self.ctx = ctx

    def apply(self):
        """注册 skill 工具进 ctx.tools（index.ts:81 defineTool + :161 register）。"""
        definition = ToolDefinition(
            name="skill",
            description="按名加载一个技能的完整指引正文",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要加载的技能名"},
                },
                "required": ["name"],
            },
            execute=self._execute,
        )
        return self.ctx.tools.register(definition)

    def _execute(self, arguments):
        """按名加载技能（index.ts:127 execute）：先 list 校验存在，再 get 加载正文。

        出错分支（未知技能 / 正文加载失败 / 不可模型调用）返回错误文本：
        真实 tool-skill 在这些分支抛错、由执行管线渲染成错误结果；第 4 章
        教学管线不捕获工具异常（tools.py:254-259 直接 str(execute(...))），
        教学版改为返回错误文本，模型看到的形态一致（[教学简化 8]）。
        """
        name = arguments.get("name")
        # 在目录里找该技能，对应 ctx.skills.list(...).find(name)（index.ts:131）。
        summary = next((s for s in self.ctx.skills.list() if s.name == name), None)
        if summary is None:
            return f'error: skill "{name}" is unknown or no longer available'
        # 加载完整定义，对应 ctx.skills.get(name)（index.ts:136）。
        definition = self.ctx.skills.get(name)
        if definition is None:
            return f'error: skill "{name}" is unknown or no longer available'
        # 校验模型可调用，对应 isModelInvocable（index.ts:138）。
        if not definition.model_invocable:
            return f'error: skill "{name}" is not available for model invocation'
        # 渲染成 <skill_content> 规范块返回给模型。
        return render_skill_content(definition)


def inject_catalog(session, summaries):
    """把技能目录作为 user 消息注入会话（pre-step #2 目录发布，index.ts:213）。

    复用第 2 章 Session 的 append-only 事件日志（agent_loop.py:69 append）：
    目录以一条 user/message 事件进会话，消息源标记为 'skill-catalog'
    （SkillCatalogSource:34），与用户真实输入区分开。
    返回注入的消息 dict；目录为空则不注入并返回 None。
    """
    text = render_catalog_message(summaries)
    if not text:
        return None
    message = {"role": "user", "content": text, "source": "skill-catalog"}
    session.append("user/message", {"message": message})
    return message
