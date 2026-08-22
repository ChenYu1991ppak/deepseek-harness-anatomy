"""第 13 章教学重构：web 提供者 —— 确定性 mock 实现。

源码对应：
- HttpFetchProvider      ↔ web-fetch-http/src/provider.ts:36（id='http' :33，
                           available()=>true :42，followAndRead :56-101 只跟随同源重定向，
                           readCapped :155 限量读取）
- DeepSeekSearchProvider ↔ web-search-deepseek/src/provider.ts:177（id='deepseek-official' :27，
                           available :189 只查本地配置）

[教学决策] 真实提供者发真实 HTTP / 搜索 API 请求；教学版用确定性 mock
（固定 query→结果、URL→页面表），输出可复现、无网络。提供者契约保留：
id + available() + search()/fetch()，且 available() 只做本地检查、
绝不发网络调用（types.ts:101）。
"""

from web_runtime import WebError


class MockSearchProvider:
    """确定性 search 提供者：按 query 查固定表（对应 DeepSeekSearchProvider 的角色）。"""

    id = "mock-search"

    TABLE = {
        "seam": [
            {"title": "Capability seam", "url": "https://docs.example/seam",
             "snippet": "seam 定义契约，不定义实现"},
            {"title": "Provider registry", "url": "https://docs.example/registry",
             "snippet": "提供者运行时注册"},
            {"title": "Execution-time selection", "url": "https://docs.example/resolve",
             "snippet": "选谁由 seam 规则决定"},
            {"title": "Normalized result", "url": "https://docs.example/result",
             "snippet": "所有提供者返回同构结果"},
            {"title": "Tool layer", "url": "https://docs.example/tools",
             "snippet": "工具只格式化，不选提供者"},
        ],
        "provider": [
            {"title": "Provider contract", "url": "https://docs.example/provider",
             "snippet": "id + available() + search/fetch"},
            {"title": "available convention", "url": "https://docs.example/available",
             "snippet": "只做本地检查，不发网络调用"},
        ],
    }

    def available(self):
        """只做本地检查，不发网络调用（types.ts:101）。"""
        return True

    def search(self, request):
        """返回 WebSearchResult：query + sources（types.ts:34/:49）。"""
        query = request["query"]
        return {"query": query, "sources": list(self.TABLE.get(query, []))}


class EchoSearchProvider:
    """第二个可用 search 提供者：回显 query，用于演示规则 5（多可用 → AMBIGUOUS）。"""

    id = "echo-search"

    def available(self):
        return True

    def search(self, request):
        query = request["query"]
        return {"query": query, "sources": [
            {"title": f"echo: {query}", "url": f"https://echo.example/{query}",
             "snippet": "echo provider"},
        ]}


class PremiumSearchProvider:
    """模拟「API key 未配置」的提供者：已注册但 available() 返回 False。

    用于演示规则 3/6：seam 绝不把请求交给不可用提供者。
    """

    id = "premium-search"

    def __init__(self, api_key=None):
        self.api_key = api_key

    def available(self):
        """只查本地配置（对应 DeepSeekSearchProvider.available 的语义）。"""
        return self.api_key is not None

    def search(self, request):
        # 永不到达这里：available() 为 False 的提供者会被 seam 过滤
        raise WebError("WEB_PROVIDER_UNAVAILABLE", "premium-search has no api_key")


class MockFetchProvider:
    """确定性 fetch 提供者：固定 URL→页面表（对应 HttpFetchProvider 的角色）。"""

    id = "mock-fetch"

    PAGES = {
        "https://docs.example/seam": {
            "kind": "text",
            "text": "seam：定义契约，不定义实现。\n消费者只面对方法面。",
        },
        "https://docs.example/guide": {
            "kind": "html",
            "html": "<h1>guide</h1><p>先注册，再在执行时选择。</p>",
        },
        "https://docs.example/logo.png": {"kind": "binary"},
    }

    def available(self):
        return True

    def fetch(self, request):
        """返回 WebFetchResult：url + status + body（types.ts:73）；body 是 html|text 并集（types.ts:93）。"""
        url = request["url"]
        page = self.PAGES.get(url)
        if page is None:
            return {"url": url, "status": 404,
                    "body": {"kind": "text", "text": "not found"}}
        if page["kind"] == "binary":
            # 真实 HttpFetchProvider 在此拒绝不支持的内容类型
            raise WebError("WEB_UNSUPPORTED_CONTENT_TYPE",
                           f"unsupported content: {url}")
        return {"url": url, "status": 200, "body": page}
