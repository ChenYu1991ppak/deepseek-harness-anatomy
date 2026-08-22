"""第 15 章反例：用「整段 merge」而非「增量 patch」组合配置。

问题场景：手头有一份跑得好好的 base 配置，想派生一个 Web 变体——
把主题改成 dark、加一个 server、关掉 headless 启动。

merge 的做法：overlay 出现的 key，整段替换 base 里的值。结果三个痛：
1. 只想改 settings 的 theme，却必须整段重写 settings——漏写 lang，它就丢了；
2. merge 没有「禁用」语义：base 里的 headless-startup 关不掉，只能干看着；
3. settings 被整段替换，事后看不出「只是改了 theme」还是「换掉了整个配置」。

正确对照：patch 用 id 定向 override / disable / insert，只写增量、可追溯
（见 main.py 段 3）。
"""


def merge_configs(base, overlay):
    """整段 merge：overlay 出现的每个 key，整段覆盖 base 里的值。"""
    merged = dict(base)
    for key, value in overlay.items():
        merged[key] = value      # 整段替换：不是增量，也不保留 base 的其余字段
    return merged


def main():
    # base：一份跑得好好的配置（每个 key 是一个服务的 config）
    base = {
        "settings": {"theme": "light", "lang": "zh"},
        "headless-startup": {"provider": "headless"},
    }

    # 目标：theme 改 dark、加 server、关掉 headless-startup。
    overlay = {
        "settings": {"theme": "dark"},     # 只写了 theme，没带 lang
        "server": {"port": 8080},
        # merge 没有办法表达「关掉 headless-startup」
    }

    merged = merge_configs(base, overlay)
    print("== 反例：整段 merge 的三个痛 ==")
    print(f"痛 1：只想改 theme，却得整段重写 settings；漏写 lang，它丢了 -> {merged['settings']}")
    print(f"痛 2：merge 没有『禁用』，headless-startup 关不掉，仍在 -> {'headless-startup' in merged}")
    print("痛 3：settings 被整段替换，看不出『只是改了 theme』还是『换掉了整个配置』")


if __name__ == "__main__":
    main()
