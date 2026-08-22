"""第 8 章：system-prompt 服务完整版。

对应 packages/core/system-prompt/src/index.ts（546 行）。

本章新模块，替换 ch02/agent_loop.py:145 的 SystemPromptService 最小桩：
桩只有 section() + assemble() 两个方法，assemble() 返回按 order 排序的段列表；
本模块补齐 context()（运行时上下文段）、tools()（工具 schema 提供者）、
variable()（提示词变量）与 render_prompt / render_context_snapshot 两个渲染函数。

[教学简化] 真实 assemble() 末尾还有 ctx.waterfall('system-prompt/assemble', assembly)
钩子（index.ts:532），允许插件在组装完成后改写产物；教学版省略该钩子。
scope 只实现「作用域层 shadow 全局层」的最小形态，完整作用域链第 9 章展开。
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Union

# 跨章复用第 1 章的 cordis 内核（Context / Service），不复制不修改前章文件
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ch01" / "code"))

from cordis import Context, Service  # noqa: E402,F401

# 人格段的固定段名（行为：常量定义），对应 index.ts:128 PERSONA_SECTION
PERSONA_SECTION = "deployment:persona"
# 人格段的固定 order（行为：常量定义），对应 index.ts:131 PERSONA_ORDER
PERSONA_ORDER = 0

# 动态文本提供者：assemble 时以 {"scope": ...} 为上下文求值（对应 TextProvider）
TextProvider = Callable[[dict], str]


@dataclass
class PromptSection:
    """一条 system-prompt 段贡献（注册输入），对应 index.ts:53 PromptSection。"""

    name: str                        # 段名，同层内唯一；同名重复注册抛错
    order: int                       # 段按 order 升序拼接（稳定排序）
    text: Union[str, TextProvider]   # 静态文本或动态提供者
    scope: Optional[str] = None      # 所属作用域层；None 表示全局层（第 9 章展开）


@dataclass
class PromptContext:
    """一条运行时上下文贡献，对应 index.ts:78 PromptContext。"""

    name: str
    order: int
    text: Union[str, TextProvider]
    scope: Optional[str] = None


@dataclass
class PromptAssembly:
    """一次 assemble 的产物，对应 index.ts:115 PromptAssembly。

    sections 与 contexts 是排好序的 (name, text) 列表；
    tools 与 variables 是两个独立字段——工具 schema 不进 render_prompt 文本，
    而是与 sections 并列传给模型调用方。
    """

    sections: list                   # [(name, text), ...] 按 order 升序
    contexts: list                   # [(name, text), ...] 按 order 升序
    tools: Optional[list] = None     # ToolSchema 列表（对应 index.ts:118 tools 字段）
    variables: dict = field(default_factory=dict)


def render_prompt(assembly: PromptAssembly) -> str:
    """把组装产物渲染成 system-prompt 文本，对应 index.ts:212 renderPrompt。

    步骤：逐段插值 {{variable}} → 丢弃渲染后为空的段 → 以空行连接。
    """
    parts = []
    for _name, text in assembly.sections:
        rendered = _interpolate(text, assembly.variables)
        if rendered.strip():
            parts.append(rendered)
    return "\n\n".join(parts)


def render_context_snapshot(assembly: PromptAssembly) -> str:
    """把运行时上下文渲染成一份快照文本，对应 index.ts:224 renderContextSnapshot。"""
    sections = []
    for _name, text in assembly.contexts:
        rendered = _interpolate(text, assembly.variables)
        if rendered.strip():
            sections.append(rendered)
    return join_context_sections(sections)


def join_context_sections(sections: list) -> str:
    """拼接上下文段并加统一前缀，对应 index.ts:236 joinContextSections。"""
    body = "\n\n".join(sections)
    if not body:
        return ""
    # 前缀显式声明「本快照取代更早的运行时上下文」（对应 index.ts:239 常量文本）
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
    """system-prompt 服务完整版，对应 index.ts:338 SystemPrompt 类。

    构造时自动注册两个默认段（对应 index.ts:358-369）：
    - harness:identity：harness 身份段，order 固定 -100，永远在最前；
    - deployment:persona：部署人格段，order 固定 0，由部署方传入。
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
            # 身份段 order -100（行为：以最小 order 注册），保证它排在所有段之前
            self.section(
                "harness:identity",
                "You are an AI agent powered by DeepSeek Harness.",
                order=-100,
            )
        if persona:
            # 人格段用固定段名与 order（行为：按常量注册），部署方可被插件识别替换
            self.section(PERSONA_SECTION, persona, order=PERSONA_ORDER)

    # ---------- 注册 API ----------

    def section(self, name, text, order: int = 0, scope=None):
        """注册一条 system-prompt 段，对应 index.ts:381 section()。

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

        return self.ctx.effect(setup, label=f"section:{name}")

    def context(self, name, text, order: int = 0, scope=None):
        """注册一条运行时上下文段，对应 index.ts:398 context()。

        与 section() 的区别：context 段不进 system 文本，而是经
        render_context_snapshot 渲染成快照、以 user 消息注入（机制 A）。
        """
        if self._context_suppressed:
            raise RuntimeError("运行时上下文已被 suppress_runtime_context 抑制")
        layer = self._layer(scope)
        if name in layer["contexts"]:
            raise ValueError(f'context "{name}" 已注册（同层同名重复）')
        entry = PromptContext(name, order, text, scope)

        def setup():
            layer["contexts"][name] = entry
            self.ctx.emit("system-prompt/change", {"name": name, "op": "add"})

            def teardown():
                layer["contexts"].pop(name, None)
                self.ctx.emit("system-prompt/change", {"name": name, "op": "remove"})

            return teardown

        return self.ctx.effect(setup, label=f"context:{name}")

    def suppress_runtime_context(self):
        """抑制运行时上下文注册，对应 index.ts:415 suppressRuntimeContext。

        用于压缩等场景：期间任何 context() 注册都会抛错。
        """
        self._context_suppressed = True

        def dispose():
            self._context_suppressed = False

        return dispose

    def tools(self, provider):
        """注册工具 schema 提供者，对应 index.ts:430 tools()。

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
        """注册提示词变量提供者，对应 index.ts:446 variable()。

        段文本中的 {{name}} 在 render_prompt 时被替换为 provider 的求值结果。
        """
        self._variable_providers[name] = provider

        def dispose():
            self._variable_providers.pop(name, None)

        return dispose

    # ---------- 组装 ----------

    def assemble(self, scope=None) -> PromptAssembly:
        """组装一次 PromptAssembly，对应 index.ts:467 assemble()。

        步骤：合并全局层与作用域层（同名 shadow）→ 按 order 稳定排序
        → 求值动态文本 → 求值工具提供者 → 解析变量。
        """
        assemble_context = {"scope": scope}

        sections = self._merge("sections", scope)
        contexts = self._merge("contexts", scope)
        # 按 order 升序稳定排序（行为：sort 不改变同 order 段的注册次序）
        sections.sort(key=lambda item: item.order)
        contexts.sort(key=lambda item: item.order)

        resolved_sections = [
            (s.name, self._resolve(s.text, assemble_context)) for s in sections
        ]
        resolved_contexts = [
            (c.name, self._resolve(c.text, assemble_context)) for c in contexts
        ]

        # 工具 schema：逐个求值提供者并累积合并（对应 index.ts:491-503 collected）；
        # 真实版还有 orderTools 排序配置（index.ts:164/529），教学版省略
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
        """取（或建）某作用域层；None 是全局层。"""
        if scope not in self._layers:
            self._layers[scope] = {"sections": {}, "contexts": {}}
        return self._layers[scope]

    def _merge(self, kind: str, scope):
        """合并全局层与作用域层，对应 index.ts:484 merge 语义。

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
