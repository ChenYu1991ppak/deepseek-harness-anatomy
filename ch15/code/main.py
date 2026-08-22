"""第 15 章演示：preset / bundle / profile 组合——把散装零件装配成可复用的产品形态。

仅标准库，Python 3.10+。运行：python3 main.py

演示段落（与正文小节一一对应）：
1. preset standing mount：一个 preset 只挂一次，多 agent join 同一组合实例（§2 题眼）
2. 双守卫：inactiveRows / leakedServices 拦下不完整或泄漏的挂载（§3）
3. bundle：patch 而非 merge——insert / override / disable 增量修改（§4 题眼）
4. profile 栈 + boot：把 bundle 串成栈、拍平成 entry 列表并激活（§5）
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
for _p in (_HERE, _CH01):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Context  # noqa: E402
from patch import apply_entry_patches, make_entry  # noqa: E402
from bundle import BUNDLE_REGISTRY  # noqa: E402
from profile import Profile, load_profile, compose_entries, boot  # noqa: E402
from preset import PresetService, PresetMountError  # noqa: E402


def demo_standing_mount():
    """段 1：standing mount——一个 preset 只挂一次，多 agent join 同一组合实例。"""
    print("== 段 1：preset standing mount —— 一个 preset 只挂一次，多 agent join ==")
    presets = {
        "writer-preset": [
            {"id": "persona"},
            {"id": "tool-bash"},
            {"id": "system-prompt-section"},
        ],
    }
    ctx = Context()
    svc = PresetService(ctx, presets)      # 构造即注册：ctx.agentPresets 指向 svc

    mount_a = svc.mount("agent-a", "writer-preset")
    mount_b = svc.mount("agent-b", "writer-preset")

    # mount_a is mount_b：两次 mount 拿到的是同一个 StandingMount 实例
    print(f"agent-a 与 agent-b 拿到同一个组合实例: {mount_a is mount_b}")
    # 真正挂载只发生一次，第二次命中的是常驻缓存
    print(f"writer-preset 真正挂载次数: {svc.mount_times['writer-preset']}")
    print(f"join 到该组合的 agent: {mount_a.joined}")
    print(f"组合内服务（唯一一份，会话间共享）: {[s.id for s in mount_a.services]}")
    print()


def demo_guards():
    """段 2：双守卫——「声明未激活」与「泄漏到进程级」的挂载都会被拦下。"""
    print("== 段 2：双守卫 —— 为什么挂载要守卫 ==")
    presets = {
        "broken-inactive": [
            {"id": "persona"},
            {"id": "flaky-tool", "activated": False},   # 声明了，但不会被激活
        ],
        "broken-leak": [
            {"id": "persona"},
            {"id": "global-cache", "realm": "ROOT"},    # 注册到进程级 ROOT realm
        ],
    }
    ctx = Context()
    svc = PresetService(ctx, presets)

    for preset_id in ("broken-inactive", "broken-leak"):
        try:
            svc.mount("agent-a", preset_id)
            print(f"{preset_id}: 挂载成功（不应出现）")
        except PresetMountError as exc:
            print(f"{preset_id}: 被守卫拦下 -> PresetMountError: {exc}")
    print()


def demo_patch_not_merge():
    """段 3：patch 而非 merge——对已有条目做 insert / override / disable 增量修改。"""
    print("== 段 3：bundle —— patch 而非 merge ==")
    seed = [
        make_entry("settings", {"theme": "light", "lang": "zh"}),
        make_entry("headless-startup", {"provider": "headless"}),
    ]
    patches = [
        {"op": "override", "id": "settings", "config": {"theme": "dark"}},
        {"op": "disable", "id": "headless-startup"},
        {"op": "insert", "entry": make_entry("server", {"port": 8080})},
    ]
    result = apply_entry_patches(seed, patches)
    by_id = {e["id"]: e for e in result}

    settings = by_id["settings"]
    # override 是增量合并：只改 theme，lang 原样保留（整段 merge 会把 lang 弄丢）
    print(f"override 只改 theme，lang 保留: settings.config = {settings['config']}")
    # disable 只翻禁用位，条目本身还在列表里（可追溯「它被谁关了」）
    print(f"disable 只翻禁用位: headless-startup.disabled = {by_id['headless-startup']['disabled']}")
    # insert 追加新条目
    print(f"insert 追加 server 后: {[e['id'] for e in result]}")
    print()


def demo_profile_boot():
    """段 4：profile 把 bundle 串成栈，boot 拍平成 entry 列表并激活。"""
    print("== 段 4：profile 栈 + boot 拍平 ==")
    # [教学决策] web 栈里保留 headless 层，是为了让「web-app 禁用前一层插入的
    # headless-startup」有一个看得见的靶子；真实 web profile 的 bundles 名单可能不同。
    web = Profile("web", ["base", "headless", "web-app"])
    stacked = load_profile(web, BUNDLE_REGISTRY)
    print(f"web profile 的 bundle 栈（栈底→栈顶）: {web.bundles}")
    print(f"叠好的 patch 总数: {len(stacked)}")

    entries = compose_entries([], stacked)      # boot 第一步：拍平成 entry 列表
    activated = boot(entries)                   # boot 第二步：激活未禁用条目
    print(f"compose 出的 entry: {[e['id'] for e in entries]}")
    print(f"boot 激活（跳过被禁用的 headless-startup）: {[e['id'] for e in activated]}")
    settings = next(e for e in activated if e["id"] == "settings")
    print(f"settings.config（theme 被 web-app 覆盖）: {settings['config']}")
    print()


def main():
    demo_standing_mount()
    demo_guards()
    demo_patch_not_merge()
    demo_profile_boot()


if __name__ == "__main__":
    main()
