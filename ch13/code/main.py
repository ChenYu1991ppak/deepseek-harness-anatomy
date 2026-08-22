#!/usr/bin/env python3
"""第 13 章：web / lsp 能力 —— 提供者注册表式 seam + 归一化查询。

运行：python3 ch13/code/main.py

装配：
  ctx.tools（复用第 4 章 ToolRuntime）
  ctx.web  （本章 WebRuntime：两张注册表 + search/fetch 方法面）
  ctx.lsp  （本章 Lsp：扩展名路由 + 原子注册）
  tool-web / tool-lsp 把工具注册进 ctx.tools

[教学决策] 提供者全部是确定性 mock 实现（无网络、无语言服务器进程），
输出可复现；真实提供者见正文 §7 源码对应。
"""

import sys
from pathlib import Path

# 复用第 1 章 cordis 与第 4 章 tools（同第 12 章的约定）
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ch01" / "code"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ch04" / "code"))

from cordis import Context  # noqa: E402
from tools import ToolExecution, ToolRuntime  # noqa: E402
from web_runtime import WebError, WebRuntime  # noqa: E402
from web_providers import (EchoSearchProvider, MockFetchProvider,  # noqa: E402
                           MockSearchProvider, PremiumSearchProvider)
from lsp_runtime import Lsp, LspError  # noqa: E402
from lsp_providers import MockLspProvider  # noqa: E402
from tool_web import WebFetchTool, WebSearchTool  # noqa: E402
from tool_lsp import LspTool  # noqa: E402


def banner(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def probe_search(config, providers, note):
    """在独立 ctx 上跑一次 search，观察执行时选择的结局。"""
    ctx = Context()
    web = WebRuntime(ctx, config)
    for provider in providers:
        web.register_search_provider(provider)
    try:
        result = web.search({"query": "seam"})
        outcome = f"正常返回，{len(result['sources'])} 条来源"
    except WebError as err:
        outcome = err.code
    ctx.dispose()
    print(f"  {note} -> {outcome}")


def try_lsp_register(lsp, provider, note):
    """尝试一次注册，打印结局（原子注册探针）。"""
    try:
        lsp.register_provider(provider)
        outcome = "注册成功"
    except LspError as err:
        outcome = f"{err.code}: {err}"
    print(f"  {note} -> {outcome}")


def main():
    banner("段 1 —— 装配：两条 seam + 三个工具")
    ctx = Context()
    ToolRuntime(ctx)       # Service，构造即注册 ctx.tools
    web = WebRuntime(ctx)  # 不配置提供者 -> 依赖选择规则 4
    lsp = Lsp(ctx)
    # 先注册不可用提供者、再注册可用的：选择与注册顺序无关
    web.register_search_provider(PremiumSearchProvider())  # 无 api_key，不可用
    web.register_search_provider(MockSearchProvider())
    web.register_fetch_provider(MockFetchProvider())
    lsp.register_provider(MockLspProvider())
    WebSearchTool(ctx).apply()
    WebFetchTool(ctx).apply()
    LspTool(ctx).apply()
    print(f"ctx.web 提供者：{web.list_providers()}")
    print(f"ctx.lsp 提供者：{lsp.list_providers()}")
    print(f"ctx.tools 可见工具：{[d.name for d in ctx.tools.view()]}")

    banner("段 2 —— web 执行时选择：六条规则")
    probe_search({"searchProvider": "mock-search"},
                 [MockSearchProvider(), PremiumSearchProvider()],
                 "规则 1：配置 id 已注册且可用")
    probe_search({"searchProvider": "no-such"},
                 [MockSearchProvider()],
                 "规则 2：配置 id 未注册")
    probe_search({"searchProvider": "premium-search"},
                 [PremiumSearchProvider()],
                 "规则 3：配置 id 已注册但不可用")
    probe_search({}, [PremiumSearchProvider(), MockSearchProvider()],
                 "规则 4：未配置，恰有一个可用（不可用者先注册也不选它）")
    probe_search({}, [MockSearchProvider(), EchoSearchProvider()],
                 "规则 5：未配置，多个可用")
    probe_search({}, [PremiumSearchProvider()],
                 "规则 6：未配置，无可用")

    banner("段 3 —— search 方法面：capSources 强制 maxResults")
    result = web.search({"query": "seam"})
    print(f"不带 maxResults：{len(result['sources'])} 条来源，"
          f"truncated={result.get('truncated', False)}")
    result = web.search({"query": "seam", "maxResults": 3})
    print(f"maxResults=3：{len(result['sources'])} 条来源，"
          f"truncated={result.get('truncated', False)}")
    print(f"第一条来源：{result['sources'][0]['title']}")

    banner("段 4 —— fetch 方法面：body 是 html|text 并集")
    for url in ("https://docs.example/seam", "https://docs.example/guide",
                "https://docs.example/missing", "https://docs.example/logo.png"):
        try:
            page = web.fetch({"url": url})
            print(f"  {url} -> [{page['status']}] body.kind={page['body']['kind']}")
        except WebError as err:
            print(f"  {url} -> {err.code}")

    banner("段 5 —— lsp 原子注册：先全量校验，再一次性写表")
    try_lsp_register(lsp, MockLspProvider(), "重复 id mock-python")
    try_lsp_register(lsp, MockLspProvider(provider_id="pyright", extensions=(".py",)),
                     "扩展名 .py 冲突（id 是新的）")
    try_lsp_register(lsp, MockLspProvider(provider_id="rust-py",
                                          extensions=(".rs", ".py"),
                                          language_id="rust"),
                     "多扩展名、.py 冲突（应整体拒绝）")
    print(f"  拒绝后 .rs 未注册：routes={sorted(lsp.routes)}")
    dispose_ts = lsp.register_provider(MockLspProvider(provider_id="mock-ts",
                                                       extensions=(".ts",),
                                                       language_id="typescript",
                                                       documents={}, symbols={}))
    print(f"  注册 mock-ts 后：providers={lsp.list_providers()}")
    dispose_ts()
    print(f"  disposer 回滚后：providers={lsp.list_providers()}")

    banner("段 6 —— lsp 查询：按扩展名路由 + 四种操作闭并集")
    position = {"line": 3, "character": 10}  # 零基：app.py 的 print(helper()) 中的 helper
    for operation in ("goToDefinition", "findReferences", "goToImplementation", "hover"):
        result = lsp.query({"operation": operation, "filePath": "/proj/app.py",
                            "position": position, "workspaceUri": "file:///proj"})
        if result["kind"] == "hover":
            print(f"  {operation:<18} -> hover: {result['hover']['contents']!r}")
        else:
            places = [f"{loc['uri']}:{loc['range']['start']['line']}:{loc['range']['start']['character']}"
                      for loc in result["locations"]]
            print(f"  {operation:<18} -> {places}")
    try:
        lsp.query({"operation": "hover", "filePath": "/proj/main.rs",
                   "position": {"line": 0, "character": 0}})
    except LspError as err:
        print(f"  .rs 无提供者 -> {err.code}")

    banner("段 7 —— 模型视角：三个工具走第 4 章 tools 管线")
    r = ctx.tools.execute(ToolExecution("call-1", "web_search",
                                        {"query": "seam", "maxResults": 2}))
    print(f"web_search ->\n{r.content}")
    print()
    r = ctx.tools.execute(ToolExecution("call-2", "web_fetch",
                                        {"url": "https://docs.example/guide"}))
    print(f"web_fetch ->\n{r.content}")
    print()
    r = ctx.tools.execute(ToolExecution(
        "call-3", "lsp",
        {"operation": "goToDefinition", "filePath": "/proj/app.py",
         "line": 4, "character": 11, "workspaceUri": "file:///proj"}))
    print(f"lsp（模型侧 1 基 line=4 character=11） ->\n{r.content}")

    ctx.dispose()


if __name__ == "__main__":
    main()
