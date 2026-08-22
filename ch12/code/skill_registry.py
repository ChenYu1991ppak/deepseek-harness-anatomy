"""第 12 章教学重构：skill 加载 —— Service Definition（定义层）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（packages/skill/skill/src/index.ts）：
- SkillSummary / SkillCandidate / SkillDefinition 词汇类型 ↔ index.ts:56 / :74 / :86
- SkillProvider 契约 { name, list(), get() }              ↔ index.ts:248
- SkillLayer（一个作用域的 provider 注册表）                ↔ index.ts:328
- SkillRegistry extends Service（super(ctx,'skills')）      ↔ index.ts:357 / :374-375
- register_provider                                        ↔ index.ts:391
- list / snapshot / get                                    ↔ index.ts:471 / :482 / :501
- collect 分层合并（global + scope 链，近层遮蔽远层）         ↔ index.ts:552-566
- collect_layer 同层 rank 去重（小者胜）                    ↔ index.ts:568-586
- 缓存与失效（revision + invalidate + skills/change）        ↔ index.ts:622 / :649
- declare('skills')（cordis.Context.skills 扩展）           ↔ index.ts:284-287

[教学决策 1] skill seam 是「provider 注册表」型 seam：多个 provider 并存注册进分层注册表，
读取时按 scope 分层 + 同层 rank 合并去重；对照第 5 章 shell 的「方法调用式 / 组合期二选一」seam。
分层 shadowing 规则（近层同名覆盖远层、同层 rank 决胜）复用第 9 章 ScopedLayers，
即类文档所言「the host+per-scope shape the tools registry established」（index.ts:346-355）。
[教学简化 1] 真实 SkillProvider 还有 watch(callback) 变更订阅与不透明 locator（index.ts:264-267）；
教学版去掉 watch，用手动 invalidate 触发缓存刷新，正文定位退化为 provider 自持。
[教学简化 2] 真实 invocationPolicy 是 {model, user} 各 allow/deny/ask（index.ts:48）；
教学版用 model_invocable / user_invocable 两个布尔。
[教学简化 3] 真实 collectLayer 排序键为 rank→providerOrder→localOrder→name（compareIndexedCandidates）；
教学版同层只按 rank（小者胜），稳定排序使注册序成为同 rank 的隐式次级键。
"""
from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass

# 复用第 1 章：Service 构造即注册（cordis.py:271-284）。
_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)
# 复用第 9 章：ScopedLayers 分层容器（scope.py:104）。
_CH09 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch09", "code"))
if _CH09 not in sys.path:
    sys.path.insert(0, _CH09)
from cordis import Service  # noqa: E402
from scope import ScopedLayers  # noqa: E402


# ---------- rank 阶梯（同层内小者胜） ----------
# 真实阶梯跨三层常量（skill-filesystem/src/index.ts:36-40 + skill/src/index.ts:24,27）：
#   project-dsh(100) > project-agents(200) > runtime(250) > custom(300)
#     > user-dsh(400) > user-agents(500) > bundled(600)
# [教学简化 4] 教学版只取阶梯两端：项目技能 100（高优先）、内置技能 600（低优先）。
PROJECT_SKILL_RANK = 100
BUNDLED_SKILL_RANK = 600


# ---------- 词汇类型：定义者与实现者/消费者共享的「词表」 ----------


@dataclass(frozen=True)
class SkillSummary:
    """目录条目：渲染用元数据，无正文（index.ts:56）。list() 返回它。"""

    name: str
    description: str
    provider: str
    source: str


@dataclass(frozen=True)
class SkillCandidate(SkillSummary):
    """候选：Summary + 同层优先级 rank（index.ts:74）。provider.list() 返回它。

    [教学简化 1] 真实候选还有不透明 locator（供 get 定位正文）；教学版由 provider 自持定位。
    """

    rank: int = BUNDLED_SKILL_RANK


@dataclass(frozen=True)
class SkillDefinition:
    """完整定义：带正文，按需加载（index.ts:86）。provider.get() 返回它。"""

    name: str
    provider: str
    content: str
    source: str = "bundled"
    description: str = ""
    model_invocable: bool = True  # [教学简化 2] 真实为 invocation.model ∈ allow/deny/ask
    user_invocable: bool = True   # [教学简化 2] 真实为 invocation.user ∈ allow/deny/ask


class SkillProvider(ABC):
    """skill provider 契约（index.ts:248）：list() 出候选、get() 按候选加载全文。

    这是「provider 注册表」型 seam 的实现侧接口：多个实现并存注册，注册表读取时
    合并去重；而不是第 5 章 shell 那种「组合期选定唯一实现」的方法调用式 seam。
    [教学简化 1] 真实契约还有 watch(callback) 变更订阅；教学版省略，靠手动 invalidate。
    """

    name: str = ""

    @abstractmethod
    def list(self) -> list[SkillCandidate]:
        """列出本 provider 的候选（元数据，无正文）（index.ts:252）。"""
        raise NotImplementedError

    @abstractmethod
    def get(self, candidate: SkillCandidate) -> SkillDefinition | None:
        """按候选加载完整定义（含正文）（index.ts:258）。"""
        raise NotImplementedError


# ---------- 分层容器：一个作用域 = 一层 provider 注册表 ----------


class SkillLayer:
    """一个作用域的 skill 贡献：一张有序 provider 表（index.ts:328）。

    与第 9 章 ToolLayer 同形——都是 scope 名下的容器；
    不同在于这里装的是 provider（而非单个工具）。
    """

    def __init__(self):
        # 以 dict 保序存放 provider：插入序 = 注册序，对应真实 NamedEntries 的 providerOrder。
        self.providers: dict[str, SkillProvider] = {}


# ---------- Service Definition：SkillRegistry 绑定 ctx.skills ----------


class SkillRegistry(Service):
    """ctx.skills：分层 skill provider 注册表（index.ts:357）。

    复用第 9 章 ScopedLayers：global 层 + 每作用域叠加层 + 父链 lineage，
    即「the host+per-scope shape the tools registry established」（index.ts:346-355）。
    读取合并 global + 观察 scope 链：近层同名整体遮蔽远层，同层内 rank 决胜（小者胜）。
    """

    def __init__(self, ctx, config=None):
        super().__init__(ctx, "skills")  # 构造即注册到 ctx.skills（cordis.py:271-284）
        self.layers = ScopedLayers(SkillLayer)  # 分层容器（index.ts:363）
        self._list_cache: dict = {}      # scope -> [SkillSummary]
        self._get_cache: dict = {}       # (scope, name) -> SkillDefinition | None
        self._snapshot_cache: dict = {}  # scope -> {providers, skills}
        self._revision = 0               # 失效计数，对应真实 revision（index.ts:368）

    # ---- 注册侧 ----

    def register_provider(self, provider: SkillProvider, scope=None):
        """把 provider 注册进 scope 对应层（index.ts:391）。注册是 effect：返回 disposer。

        scope 为 None 落 global 层（宿主级）；否则落该作用域叠加层。
        """
        layer = self.layers.for_scope(scope)
        if provider.name in layer.providers:
            raise ValueError(f"skill provider already registered: {provider.name}")
        # 写入 provider 并立即失效缓存：注册改变了后续读取的结果。
        layer.providers[provider.name] = provider
        self.invalidate()

        def dispose():
            # 撤销注册并再次失效，对应真实 disposer（undo insert + invalidateCache）。
            layer.providers.pop(provider.name, None)
            self.invalidate()

        return dispose

    # ---- 读取侧 ----

    def list(self, scope=None) -> list[SkillSummary]:
        """合并目录：取胜者 Summary，按名排序（index.ts:471），带缓存。"""
        if scope in self._list_cache:
            return self._list_cache[scope]
        merged = self._collect(scope)
        # 把取胜候选投影成 Summary，并按名排序，保证目录输出稳定。
        summaries = [
            SkillSummary(
                name=entry["candidate"].name,
                description=entry["candidate"].description,
                provider=entry["candidate"].provider,
                source=entry["candidate"].source,
            )
            for entry in sorted(merged.values(), key=lambda e: e["candidate"].name)
        ]
        self._list_cache[scope] = summaries
        return summaries

    def get(self, name: str, scope=None) -> SkillDefinition | None:
        """按名加载取胜者的完整定义（含正文）（index.ts:501），带缓存。"""
        key = (scope, name)
        if key in self._get_cache:
            return self._get_cache[key]
        entry = self._collect(scope).get(name)
        # 命中取胜候选后，委托它的 provider 加载正文；未命中则为 None。
        definition = entry["provider"].get(entry["candidate"]) if entry else None
        self._get_cache[key] = definition
        return definition

    def snapshot(self, scope=None) -> dict:
        """{providers, skills} 快照（index.ts:482）。"""
        if scope in self._snapshot_cache:
            return self._snapshot_cache[scope]
        snap = {
            "providers": self._provider_names(scope),
            "skills": self.list(scope),
        }
        self._snapshot_cache[scope] = snap
        return snap

    # ---- 失效 ----

    def invalidate(self):
        """清空全部读缓存并广播 skills/change（index.ts:622 + :649）。"""
        self._revision += 1
        self._list_cache.clear()
        self._get_cache.clear()
        self._snapshot_cache.clear()
        self.ctx.emit("skills/change", {})

    # ---- 分层合并（内部） ----

    def _layer_chain(self, scope):
        """生效层链：global 打底 + scope 父链（祖先在前、最近层在后）（index.ts:557）。"""
        layers = [self.layers.global_layer]
        if scope is not None:
            layers.extend(self.layers.chain_layers(scope))
        return layers

    def _collect(self, scope) -> dict:
        """跨层合并：层链由远及近遍历，近层同名覆盖远层（shadowing）（index.ts:552-566）。"""
        merged: dict = {}
        for layer in self._layer_chain(scope):
            for name, entry in self._collect_layer(layer).items():
                merged[name] = entry  # 后写的近层覆盖先写的远层
        return merged

    def _collect_layer(self, layer: SkillLayer) -> dict:
        """单层：汇齐各 provider 候选，按 rank 排序（小者胜），同名去重（index.ts:568-586）。"""
        indexed = []
        for provider in layer.providers.values():
            for candidate in provider.list():
                indexed.append((candidate, provider))
        # 按 rank 升序稳定排序：rank 小者排前，同 rank 保留注册序。
        indexed.sort(key=lambda pair: pair[0].rank)
        winning: dict = {}
        for candidate, provider in indexed:
            if candidate.name not in winning:  # 同层同名：首个（rank 最小）胜，其余丢弃
                winning[candidate.name] = {"candidate": candidate, "provider": provider}
        return winning

    def _provider_names(self, scope) -> list[str]:
        """scope 可见的 provider 名（global + 链，近层遮蔽同名）。"""
        merged: dict = {}
        for layer in self._layer_chain(scope):
            merged.update(dict.fromkeys(layer.providers))
        return sorted(merged)


# declare('skills')（index.ts:284-287）的教学对应：SkillRegistry(ctx) 构造时经
# Service.__init__ → ctx.provide("skills", self) 自动绑定 ctx.skills（cordis.py:279-284），
# 与第 4 章 ToolRuntime(ctx)、第 9 章 ScopedToolRuntime(ctx) 同一路数。
