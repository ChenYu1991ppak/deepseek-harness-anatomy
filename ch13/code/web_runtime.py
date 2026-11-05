"""第 13 章教学重构：web 能力 seam —— 提供者注册表 + 方法面。

源码对应（上游仓库根起：packages/web/web/src/index.ts、packages/web/web/src/types.ts）：
- WebRuntime                       ↔ packages/web/web/src/index.ts:82（extends TypertRemoteService，注册为 ctx.web）
- searchProviders / fetchProviders ↔ packages/web/web/src/index.ts:93-94（两张独立注册表）
- registerSearchProvider           ↔ packages/web/web/src/index.ts:113-115
- registerFetchProvider            ↔ packages/web/web/src/index.ts:124-126
- registerProvider                 ↔ packages/web/web/src/index.ts:128-144（ctx.effect 在 :133-140）
- search / fetch                   ↔ packages/web/web/src/index.ts:185-188 / 219-221
- resolveProvider                  ↔ packages/web/web/src/index.ts:275-297（六条选择规则）
- capSources                       ↔ packages/web/web/src/index.ts:300-303（seam 强制 maxResults）
- WebError                         ↔ packages/web/web/src/types.ts:202

[教学决策] 真实提供者是 HttpFetchProvider（真实 HTTP）与 DeepSeekSearchProvider
（真实搜索 API）；教学版用确定性 mock 提供者，网络细节只出现在正文 §8 源码对照。
"""

import sys
from pathlib import Path

# 复用第 1 章：Service 构造即注册（cordis.py:271-284）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ch01" / "code"))

from cordis import Service  # noqa: E402


class WebError(Exception):
    """web seam 的统一错误，带机器可读 code（`packages/web/web/src/types.ts:202`）。

    真实错误码含 WEB_DUPLICATE_PROVIDER / WEB_PROVIDER_* / WEB_ABORTED /
    WEB_FETCH_TIMEOUT / WEB_REDIRECT_BLOCKED / WEB_UNSUPPORTED_CONTENT_TYPE。
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class WebRuntime(Service):
    """ctx.web 服务：两张注册表 + search/fetch 方法面（`packages/web/web/src/index.ts:82`）。

    seam 只管「谁在场 + 选谁」，从不碰网络：
    - register_*_provider：提供者运行时注册，重复 id 拒绝
    - search / fetch：执行时 resolve_provider 按规则选人，再委托提供者
    类 doc（`packages/web/web/src/index.ts:69-81`）写明选择语义：执行时解析，绝不依赖注册顺序。
    """

    def __init__(self, ctx, config=None):
        super().__init__(ctx, "web")
        config = config or {}
        # 配置的提供者 id（`packages/web/web/src/index.ts:88-91`）；真实版还可被环境变量覆盖（:100-101）
        self.configured_search = config.get("searchProvider")
        self.configured_fetch = config.get("fetchProvider")
        self.search_providers = {}  # search 注册表（`packages/web/web/src/index.ts:93`）
        self.fetch_providers = {}   # fetch 注册表（`packages/web/web/src/index.ts:94`）

    def register_search_provider(self, provider):
        """注册 search 提供者（`packages/web/web/src/index.ts:113-115`）。"""
        return self._register_provider(self.search_providers, provider)

    def register_fetch_provider(self, provider):
        """注册 fetch 提供者（`packages/web/web/src/index.ts:124-126`）。"""
        return self._register_provider(self.fetch_providers, provider)

    def _register_provider(self, registry, provider):
        """注册是 effect：注册写表、卸载还原（`packages/web/web/src/index.ts:128-144`）。"""
        if provider.id in registry:
            raise WebError("WEB_DUPLICATE_PROVIDER",
                           f"duplicate web provider id: {provider.id}")

        def setup():
            registry[provider.id] = provider  # ctx.effect 体（`packages/web/web/src/index.ts:133-140`）

            def dispose():
                if registry.get(provider.id) is provider:
                    del registry[provider.id]

            return dispose

        return self.ctx.effect(setup)

    def search(self, request):
        """search 方法面：按规则选人 → provider.search → cap_sources（`packages/web/web/src/index.ts:185-188`）。"""
        provider = self._resolve_provider(self.search_providers,
                                          self.configured_search)
        result = provider.search(request)
        return cap_sources(result, request.get("maxResults"))

    def fetch(self, request):
        """fetch 方法面：按规则选人 → provider.fetch（`packages/web/web/src/index.ts:219-221`）。"""
        provider = self._resolve_provider(self.fetch_providers,
                                          self.configured_fetch)
        return provider.fetch(request)

    def _resolve_provider(self, registry, configured_id):
        """执行时选人：六条规则，绝不依赖注册顺序（`packages/web/web/src/index.ts:275-297`）。"""
        if configured_id is not None:
            provider = registry.get(configured_id)
            if provider is None:
                # 规则 2：配置 id 未注册
                raise WebError("WEB_PROVIDER_CONFIGURED_MISSING",
                               f"configured provider not registered: {configured_id}")
            if not provider.available():
                # 规则 3：配置 id 已注册但不可用
                raise WebError("WEB_PROVIDER_CONFIGURED_UNAVAILABLE",
                               f"configured provider unavailable: {configured_id}")
            return provider  # 规则 1：配置 id 已注册且可用
        usable = [p for p in registry.values() if p.available()]
        if len(usable) == 1:
            return usable[0]  # 规则 4：未配置且恰有一个可用
        if len(usable) > 1:
            # 规则 5：多个可用 → 报错，绝不隐式选「先注册的那个」
            ids = ", ".join(sorted(p.id for p in usable))
            raise WebError("WEB_PROVIDER_AMBIGUOUS",
                           f"multiple providers available, configure one: {ids}")
        # 规则 6：无可用
        raise WebError("WEB_PROVIDER_UNAVAILABLE", "no web provider available")

    def list_providers(self):
        """谁在场：返回两张注册表的已注册 id 列表。"""
        return {
            "search": sorted(self.search_providers),
            "fetch": sorted(self.fetch_providers),
        }


def cap_sources(result, max_results):
    """seam 强制 maxResults：截断 sources 并置 truncated（`packages/web/web/src/index.ts:300-303`）。

    提供者不必知道上限 —— 上限写在 seam，所有提供者一律受益。
    """
    sources = result["sources"]
    if max_results is None or len(sources) <= max_results:
        return result
    return {
        "query": result["query"],
        "sources": sources[:max_results],
        "truncated": True,
    }
