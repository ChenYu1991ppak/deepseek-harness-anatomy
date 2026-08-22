"""第 13 章反例：把外部能力硬连线进调用方。

运行：python3 ch13/code/bad_example.py

三个坑：
1. 实现写死：搜索函数直接调具体引擎；换引擎 = 改函数体。
2. 选择分散：「用哪个引擎」由每个调用方各写各的分支，没有统一规则；
   多个引擎同时可用时，不同调用方可能写出不同选择。
3. 结果不归一：每个引擎返回自己的结构，给模型的格式化在每个调用点重复。
"""


def engine_brave_search(query):
    """引擎 A（模拟真实搜索 API）：返回自己的私有格式。"""
    return {"links": [{"t": f"brave:{query}", "u": f"https://brave.example/{query}"}]}


def engine_mock_search(query):
    """引擎 B（模拟测试桩）：返回另一种私有格式。"""
    return {"sources": [{"title": f"mock:{query}", "url": f"https://mock.example/{query}"}]}


def web_search(query, engine="brave"):
    # 坑 1：实现写死 —— 引擎以分支形式出现在函数体里
    # 坑 2：选择分散 —— 用哪个引擎是调用方传参决定，没有统一规则
    if engine == "brave":
        return engine_brave_search(query)
    return engine_mock_search(query)


def render_for_model(raw):
    # 坑 3：结果不归一 —— 调用方必须认识每种引擎的格式
    if "links" in raw:
        items = [(x["t"], x["u"]) for x in raw["links"]]
    else:
        items = [(x["title"], x["url"]) for x in raw["sources"]]
    return "\n".join(f"- {title} {url}" for title, url in items)


if __name__ == "__main__":
    print("调用方 1（写死 brave）：")
    print(render_for_model(web_search("seam")))
    print()
    print("调用方 2（想换 mock，得改调用参数，并确认格式化分支认得 mock 的格式）：")
    print(render_for_model(web_search("seam", engine="mock")))
    print()
    print("想加第三个引擎？要改 web_search 的分支、render_for_model 的格式识别，")
    print("以及每一个调用点 —— 而它们本来只想要「搜一下」这件事。")
