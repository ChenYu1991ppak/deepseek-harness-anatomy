"""第 13 章教学重构：web 能力 seam —— 提供者注册表 + 方法面。

源码对应（packages/web/web/src/index.ts）：
- WebRuntime                       ↔ index.ts:74（extends Service，注册为 ctx.web）
- searchProviders / fetchProviders ↔ index.ts:85-86（两张独立注册表）
- registerSearchProvider           ↔ index.ts:103-105
- registerFetchProvider            ↔ index.ts:114-116
- registerProvider                 ↔ index.ts:118-129（ctx.effect 在 :122-125）
- search / fetch                   ↔ index.ts:140-147 / 157-163
- resolveProvider                  ↔ index.ts:172-194（六条选择规则）
- capSources                       ↔ index.ts:197-200（seam 强制 maxResults）
- WebError                         ↔ types.ts:129

[教学决策] 真实提供者是 HttpFetchProvider（真实 HTTP）与 DeepSeekSearchProvider
（真实搜索 API）；教学版用确定性 mock 提供者，网络细节只出现在正文 §7 源码对应。
"""

import sys
from pathlib import Path

# 复用第 1 章：Service 构造即注册（cordis.py:271-284）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ch01" / "code"))

from cordis import Service  # noqa: E402


class WebError(Exception):
    """web seam 的统一错误，带机器可读 code（types.ts:129）。

    真实错误码含 WEB_DUPLICATE_PROVIDER / WEB_PROVIDER_* / WEB_ABORTED /
    WEB_FETCH_TIMEOUT / WEB_REDIRECT_BLOCKED / WEB_UNSUPPORTED_CONTENT_TYPE。
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class WebRuntime(Service):
    """ctx.web 服务：两张注册表 + search/fetch 方法面（index.ts:74）。

    seam 只管「谁在场 + 选谁」，从不碰网络：
    - register_*_provider：提供者运行时注册，重复 id 拒绝
    - search / fetch：执行时 resolve_provider 按规则选人，再委托提供者
    类 doc（index.ts:62-73）写明选择语义：执行时解析，绝不依赖注册顺序。
    """

    def __init__(self, ctx, config=None):
        super().__init__(ctx, "web")
        config = config or {}
        # 配置的提供者 id（index.ts:80-83）；真实版还可被环境变量覆盖（index.ts:92-93）
        self.configured_search = config.get("searchProvider")
        self.configured_fetch = config.get("fetchProvider")
        self.search_providers = {}  # search 注册表（index.ts:85）
        self.fetch_providers = {}   # fetch 注册表（index.ts:86）

    def register_search_provider(self, provider):
        """注册 search 提供者（index.ts:103-105）。"""
        return self._register_provider(self.search_providers, provider)

    def register_fetch_provider(self, provider):
        """注册 fetch 提供者（index.ts:114-116）。"""
        return self._register_provider(self.fetch_providers, provider)

    def _register_provider(self, registry, provider):
        """注册是 effect：注册写表、卸载还原（index.ts:118-129）。"""
        if provider.id in registry:
            raise WebError("WEB_DUPLICATE_PROVIDER",
                           f"duplicate web provider id: {provider.id}")

        def setup():
            registry[provider.id] = provider  # ctx.effect 体（index.ts:122-125）

            def dispose():
                if registry.get(provider.id) is provider:
                    del registry[provider.id]

            return dispose

        return self.ctx.effect(setup, label=f"web-provider:{provider.id}")

    def search(self, request):
        """search 方法面：按规则选人 → provider.search → cap_sources（index.ts:140-147）。"""
        provider = self._resolve_provider(self.search_providers,
                                          self.configured_search)
        result = provider.search(request)
        return cap_sources(result, request.get("maxResults"))

    def fetch(self, request):
        """fetch 方法面：按规则选人 → provider.fetch（index.ts:157-163）。"""
        provider = self._resolve_provider(self.fetch_providers,
                                          self.configured_fetch)
        return provider.fetch(request)

    def _resolve_provider(self, registry, configured_id):
        """执行时选人：六条规则，绝不依赖注册顺序（index.ts:172-194）。"""
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
    """seam 强制 maxResults：截断 sources 并置 truncated（index.ts:197-200）。

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
