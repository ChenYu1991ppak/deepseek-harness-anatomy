"""第 8 章：system-prompt 服务完整版。

对应 packages/core/system-prompt/src/index.ts（HEAD 8bd073392，698 行）。
源码目录声明（本模块用到的符号都在这一个文件里）：
- PromptSection / PromptContext            ↔ index.ts:53 / :79
- PromptAssembly / PromptPresentation      ↔ index.ts:132 / :125
- SECTION_ORDERS / CONTEXT_ORDERS          ↔ index.ts:136 / :177
- PERSONA_PREFIX_SECTION / PERSONA_SUFFIX_SECTION ↔ index.ts:196 / :199
- renderPrompt / renderContextSnapshot     ↔ index.ts:296 / :308
- joinContextSections / renderContextSections ↔ index.ts:320 / :336
- SystemPrompt 类                          ↔ index.ts:435（构造器 :451）
- section / context / suppressRuntimeContext / tools / variable
                                           ↔ index.ts:484 / :519 / :536 / :551 / :567
- assemble                                 ↔ index.ts:620；orderTools ↔ :232

本章新模块，替换 ch02/agent_loop.py:150 的 SystemPromptService 最小桩：
桩只有 section() + assemble() 两个方法，assemble() 返回按 order 排序的段列表；
本模块补齐 context()（运行时上下文段）、tools()（工具 schema 提供者）、
variable()（提示词变量）与 render_prompt / render_context_snapshot 两个渲染函数。

[教学简化] 真实 assemble() 末尾还有 ctx.waterfall('system-prompt/assemble', assembly)
钩子（index.ts:683），允许插件在组装完成后改写产物；教学版省略该钩子，
也省略 waterfall 之后的 complete section 恢复（index.ts:687-693）。
required context 过滤保留（assemble 里按 required 过滤），只是没有 waterfall 改写这一步。
抑制标记在教学版是一个全局布尔 _context_suppressed；真实版按层存放（index.ts:404），
本章只用全局层，行为一致。
scope 只实现「作用域层 shadow 全局层」的最小形态，完整作用域链第 9 章展开。
真实 PromptSection 没有 scope 字段——段落在哪一层由注册时调用的 ctx 决定
（index.ts:488 layers.effect(this.ctx, ...)）；教学版把 scope 做成显式参数。
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Union

# 跨章复用第 1 章的 cordis 内核（Context / Service），不复制不修改前章文件
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ch01" / "code"))

from cordis import Service  # noqa: E402

# 人格前缀段的固定段名（行为：常量定义），对应 index.ts:196 PERSONA_PREFIX_SECTION
PERSONA_PREFIX_SECTION = "deployment:persona-prefix"
# 人格前缀段的固定 order（行为：常量定义），对应 SECTION_ORDERS.DEPLOYMENT_PERSONA_PREFIX（index.ts:138）
PERSONA_PREFIX_ORDER = 0

# 动态文本提供者：assemble 时以 {"scope": ...} 为上下文求值（对应 TextProvider）
TextProvider = Callable[[dict], str]


@dataclass
class PromptSection:
    """一条 system-prompt 段贡献（注册输入），对应 index.ts:53 PromptSection。

    教学版多了 scope 字段（真实版由注册时调用的 ctx 决定层，见模块 docstring）。
    """

    name: str                        # 段名，同层内唯一；同名重复注册抛错
    order: int                       # 段按 order 升序拼接；同 order 按段名比较（index.ts:254）
    text: Union[str, TextProvider]   # 静态文本或动态提供者
    scope: Optional[str] = None      # 所属作用域层；None 表示全局层（第 9 章展开）


@dataclass
class PromptContext:
    """一条运行时上下文贡献，对应 index.ts:79 PromptContext。"""

    name: str
    order: int
    text: Union[str, TextProvider]
    required: bool = False           # True 时即使运行时上下文被抑制也保留（index.ts:89）
    scope: Optional[str] = None


@dataclass
class PromptAssembly:
    """一次 assemble 的产物，对应 index.ts:132 PromptAssembly。

    真实版四个字段：sections / contexts / variables / tools（index.ts:125-134）。
    教学版把 sections 与 contexts 存成排好序的 (name, text) 列表；
    tools 与 variables 是两个独立字段——工具 schema 不进 render_prompt 文本，
    而是与 sections 并列传给模型调用方。
    """

    sections: list                   # [(name, text), ...] 按 order 升序
    contexts: list                   # [(name, text), ...] 按 order 升序
    tools: Optional[list] = None     # ToolSchema 列表（PromptAssembly.tools，index.ts:133）
    variables: dict = field(default_factory=dict)


def render_prompt(assembly: PromptAssembly) -> str:
    """把组装产物渲染成 system-prompt 文本，对应 index.ts:296 renderPrompt。

    步骤：逐段插值 {{variable}} → 丢弃渲染后为空的段 → 以空行连接。
    """
    parts = []
    for _name, text in assembly.sections:
        rendered = _interpolate(text, assembly.variables)
        if rendered.strip():
            parts.append(rendered)
    return "\n\n".join(parts)


def render_context_snapshot(assembly: PromptAssembly) -> str:
    """把运行时上下文渲染成一份快照文本，对应 index.ts:308 renderContextSnapshot。

    真实版把「逐段渲染」拆成 renderContextSections（index.ts:336）、
    「加前缀拼接」拆成 joinContextSections（index.ts:320）；教学版在同一个函数里完成。
    """
    sections = []
    for _name, text in assembly.contexts:
        rendered = _interpolate(text, assembly.variables)
        if rendered.strip():
            sections.append(rendered)
    return join_context_sections(sections)


def join_context_sections(sections: list) -> str:
    """拼接上下文段并加统一前缀，对应 index.ts:320 joinContextSections。"""
    body = "\n\n".join(sections)
    if not body:
        return ""
    # 前缀显式声明「本快照取代更早的运行时上下文」（对应 index.ts:323 常量文本）
    return (
        "Current runtime context. This snapshot supersedes earlier runtime-context snapshots."
        + "\n\n"
        + body
    )


def _interpolate(text: str, variables: dict) -> str:
    """把文本中的 {{name}} 占位符替换为变量值；未注册的变量名原样保留。"""

    def _sub(match):
        return str(variables.get(match.group(1), match.group(0)))

    return re.sub(r"\{\{(\w+)\}\}", _sub, text)


class SystemPrompt(Service):
    """system-prompt 服务完整版，对应 index.ts:435 SystemPrompt 类（构造器 :451）。

    构造时自动注册默认段（对应 index.ts:455-472）：
    - harness:identity：harness 身份段，order 固定 -1000（SECTION_ORDERS.HARNESS_IDENTITY），
      由 config.includeHarnessIdentity 控制是否注册（默认开）；
    - deployment:persona-prefix / deployment:persona-suffix：部署人格的前后缀两段，
      order 固定 0 与 10200，分别取 config.personaPrefix / config.personaSuffix。
    教学版只保留 persona 前缀段（后缀默认空串，输出上看不出差别）。
    """

    def __init__(self, ctx, persona: str = "", include_harness_identity: bool = True):
        super().__init__(ctx, "systemPrompt")
        # 按作用域分层存放段：None 是全局层，其余是作用域层（第 9 章展开）
        self._layers: dict = {}
        # [教学简化] 真实代码里 tools/variable 注册同样经 layers.effect 写进当前作用域层；教学版用扁平容器（本章只用全局层，行为一致）
        self._tool_providers: list = []
        self._variable_providers: dict = {}
        self._context_suppressed = False

        if include_harness_identity:
            # 身份段 order -1000（行为：以最小 order 注册），保证它排在所有段之前
            self.section(
                "harness:identity",
                "You are an AI agent powered by DeepSeek Harness.",
                order=-1000,
            )
        if persona:
            # 人格前缀段用固定段名与 order（行为：按常量注册），部署方可被插件识别替换
            self.section(PERSONA_PREFIX_SECTION, persona, order=PERSONA_PREFIX_ORDER)

    # ---------- 注册 API ----------

    def section(self, name, text, order: int = 0, scope=None):
        """注册一条 system-prompt 段，对应 index.ts:484 section()。

        同层同名抛错；作用域层的同名段在 assemble 时 shadow 全局层（第 9 章展开）。
        注册走 ctx.effect，返回的 disposer 调用即注销（可逆性来自第 1 章）。
        """
        layer = self._layer(scope)
        if name in layer["sections"]:
            raise ValueError(f'section "{name}" 已注册（同层同名重复）')
        entry = PromptSection(name, order, text, scope)

        def setup():
            layer["sections"][name] = entry
            self.ctx.emit("system-prompt/change", {"name": name, "op": "add"})

            def teardown():
                layer["sections"].pop(name, None)
                self.ctx.emit("system-prompt/change", {"name": name, "op": "remove"})

            return teardown

        return self.ctx.effect(setup)

    def context(self, name, text, order: int = 0, required: bool = False, scope=None):
        """注册一条运行时上下文段，对应 index.ts:519 context()。

        与 section() 的区别：context 段不进 system 文本，而是经
        render_context_snapshot 渲染成快照、以 user 消息注入（机制 A）。
        注册本身不受抑制影响；required=True 的段在组装时始终保留（index.ts:89）。
        """
        layer = self._layer(scope)
        if name in layer["contexts"]:
            raise ValueError(f'context "{name}" 已注册（同层同名重复）')
        entry = PromptContext(name, order, text, required, scope)

        def setup():
            layer["contexts"][name] = entry
            self.ctx.emit("system-prompt/change", {"name": name, "op": "add"})

            def teardown():
                layer["contexts"].pop(name, None)
                self.ctx.emit("system-prompt/change", {"name": name, "op": "remove"})

            return teardown

        return self.ctx.effect(setup)

    def suppress_runtime_context(self):
        """抑制可选的运行时上下文段，对应 index.ts:536 suppressRuntimeContext。

        用于压缩等场景：抑制期间 assemble 丢弃非 required 的 context 段
        （index.ts:677-679）；required=True 的段仍然保留。
        抑制不阻止 context() 注册——真实版的 suppress 只往层里追加一个标记，
        注册照常发生，只是组装时该段被过滤。
        """
        self._context_suppressed = True

        def dispose():
            self._context_suppressed = False

        return dispose

    def tools(self, provider):
        """注册工具 schema 提供者，对应 index.ts:551 tools()。

        provider 在每次 assemble 时求值，返回 {"schemas": [...], "knownNames": [...]}；
        assemble 提取各提供者的 schemas 合并进 PromptAssembly.tools。
        注意：schema 是 PromptAssembly 的并列独立字段，不是 section，
        不进 render_prompt 文本（第 4 章 wireSchemas 的真实落点就是这里）。
        """
        self._tool_providers.append(provider)

        def dispose():
            if provider in self._tool_providers:
                self._tool_providers.remove(provider)

        return dispose

    def variable(self, name, provider):
        """注册提示词变量提供者，对应 index.ts:567 variable()。

        段文本中的 {{name}} 在 render_prompt 时被替换为 provider 的求值结果。
        """
        self._variable_providers[name] = provider

        def dispose():
            self._variable_providers.pop(name, None)

        return dispose

    # ---------- 组装 ----------

    def assemble(self, scope=None) -> PromptAssembly:
        """组装一次 PromptAssembly，对应 index.ts:620 assemble()。

        真实版步骤（index.ts:620-695）：解析变量 → 合并分层 → 求值工具提供者
        → （order, 段名）排序 → orderTools（:674）→ 过滤被抑制的 context（:677-679）
        → 走 system-prompt/assemble waterfall（:683）。
        教学版：合并 → 排序 → 求值动态文本 → 求值工具提供者 → 解析变量（省略 orderTools 与 waterfall）。
        """
        assemble_context = {"scope": scope}

        sections = self._merge("sections", scope)
        contexts = self._merge("contexts", scope)
        # 按 order 升序排序；同 order 按段名比较（index.ts:254 comparePromptSections）
        sections.sort(key=lambda item: (item.order, item.name))
        # 被抑制时只保留 required 段（index.ts:677-679）
        if self._context_suppressed:
            contexts = [c for c in contexts if c.required]
        contexts.sort(key=lambda item: (item.order, item.name))

        resolved_sections = [
            (s.name, self._resolve(s.text, assemble_context)) for s in sections
        ]
        resolved_contexts = [
            (c.name, self._resolve(c.text, assemble_context)) for c in contexts
        ]

        # 工具 schema：逐个求值提供者并累积合并（对应 index.ts:644-656 collected）；
        # 真实版还有 orderTools 排序配置（index.ts:232，由 :674 调用），教学版省略
        collected = []
        for provider in self._tool_providers:
            provided = provider(assemble_context) or {}
            collected.extend(provided.get("schemas", []))
        tools = collected if collected else None

        variables = {
            name: provider(assemble_context)
            for name, provider in self._variable_providers.items()
        }
        return PromptAssembly(resolved_sections, resolved_contexts, tools, variables)

    # ---------- 内部工具 ----------

    def _layer(self, scope):
        """取（或建）某作用域层；None 是全局层。

        真实版的全局层另有 global 与作用域链两层结构（index.ts:622 chainLayers）；
        教学版只区分「全局 + 一个作用域层」，完整链第 9 章展开。
        """
        if scope not in self._layers:
            self._layers[scope] = {"sections": {}, "contexts": {}}
        return self._layers[scope]

    def _merge(self, kind: str, scope):
        """合并全局层与作用域层，对应 index.ts:637-638 layers.merge 语义。

        同名条目：作用域层 shadow 全局层（第 9 章展开完整作用域链）。
        """
        merged = dict(self._layer(None)[kind])
        if scope is not None:
            merged.update(self._layer(scope)[kind])
        return list(merged.values())

    @staticmethod
    def _resolve(text, assemble_context):
        """动态提供者在 assemble 时求值；静态文本原样返回。"""
        if callable(text):
            return text(assemble_context)
        return text
