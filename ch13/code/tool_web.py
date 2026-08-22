"""第 13 章教学重构：tool-web —— 把 web_search / web_fetch 暴露给模型。

源码对应（packages/web/tool-web/src/search.ts / fetch.ts）：
- applyWebSearchTool     ↔ search.ts:210-274（systemPrompt.section order:110 + defineTool web_search）
- applyWebFetchTool      ↔ fetch.ts:429-495（section order:111 + defineTool web_fetch）
- execute                ↔ search.ts:259-264 / fetch.ts:479-484（只调 ctx.web.search/fetch）
- formatSearchOutput     ↔ search.ts:54-75
- WEB_SEARCH_MAX_RESULTS ↔ search.ts:20（=8）

[教学决策] 真实版 inject=['tools','web','systemPrompt'] 并写 prompt section；
教学版不引入 systemPrompt（同第 12 章 tool-skill 的处理），只把工具注册进
ctx.tools。真实版用 turndown 把 HTML 转 markdown；教学版用朴素去标签。
"""

import re
import sys
from pathlib import Path

# 复用第 4 章：ToolDefinition（tools.py:79-91）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ch04" / "code"))

from tools import ToolDefinition  # noqa: E402
from web_runtime import WebError  # noqa: E402

WEB_SEARCH_MAX_RESULTS = 8  # search.ts:20


class WebSearchTool:
    """web_search 工具：execute 只调 ctx.web.search（search.ts:259-264）。"""

    inject = ["tools", "web"]

    def __init__(self, ctx):
        self.ctx = ctx

    def apply(self):
        self.ctx.tools.register(ToolDefinition(
            name="web_search",
            description="搜索网络，返回来源列表",
            parameters={"query": "搜索词",
                        "maxResults": "结果上限（可选，默认 8）"},
            execute=self._execute,
        ))

    def _execute(self, args):
        query = args.get("query")
        if not query:
            return "web_search failed: query is required"
        max_results = args.get("maxResults") or WEB_SEARCH_MAX_RESULTS
        max_results = min(int(max_results), WEB_SEARCH_MAX_RESULTS)
        try:
            result = self.ctx.web.search({"query": query,
                                          "maxResults": max_results})
        except WebError as err:
            return f"web_search failed: {err.code}: {err}"
        return format_search_output(result)


class WebFetchTool:
    """web_fetch 工具：execute 只调 ctx.web.fetch（fetch.ts:479-484）。"""

    inject = ["tools", "web"]

    def __init__(self, ctx):
        self.ctx = ctx

    def apply(self):
        self.ctx.tools.register(ToolDefinition(
            name="web_fetch",
            description="抓取 URL，返回页面正文",
            parameters={"url": "要抓取的 URL"},
            execute=self._execute,
        ))

    def _execute(self, args):
        url = args.get("url")
        if not url:
            return "web_fetch failed: url is required"
        try:
            result = self.ctx.web.fetch({"url": url})
        except WebError as err:
            return f"web_fetch failed: {err.code}: {err}"
        return f"[{result['status']}] {result['url']}\n{render_body(result['body'])}"


def format_search_output(result):
    """把搜索结果渲染成给模型的纯文本（search.ts:54-75）。"""
    lines = [f"search: {result['query']}"]
    for i, source in enumerate(result["sources"], 1):
        lines.append(f"{i}. {source['title']}")
        lines.append(f"   {source['url']}")
        if source.get("snippet"):
            lines.append(f"   {source['snippet']}")
    if result.get("truncated"):
        lines.append("（结果已按 maxResults 截断）")
    return "\n".join(lines)


def render_body(body):
    """body 是 html|text 并集（types.ts:93）。

    真实版用 turndown 把 HTML 转 markdown（fetch.ts renderBody）；
    [教学简化] 用朴素去标签。
    """
    if body["kind"] == "text":
        return body["text"]
    html = re.sub(r"</(h1|h2|p|li)>", "\n", body["html"])  # 块级标签换行
    return re.sub(r"<[^>]+>", "", html).strip()
