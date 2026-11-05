"""第 15 章演示：preset / bundle / profile 组合——把散装零件装配成可复用的产品形态。

仅标准库，Python 3.10+。运行：python3 main.py

演示段落（与正文小节一一对应）：
1. preset revision 保留：每个 Agent 绑定创建时那一代，新 revision 不打扰旧 Agent（§2 题眼）
2. 挂载守卫：auditRows / leakedServices 拦下没起来的行与泄漏到进程级的服务（§3）
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

WRITER_V1 = [
    {"id": "persona"},
    {"id": "tool-bash"},
    {"id": "system-prompt-section"},
]
WRITER_V2 = [
    {"id": "persona"},
    {"id": "tool-bash"},
    {"id": "system-prompt-section"},
    {"id": "tool-web"},             # 新 revision 多了一个工具
]


def demo_revision_binding():
    """段 1：Agent 绑定创建时的 revision——新 revision 生效，旧 Agent 仍用旧组合。"""
    print("== 段 1：preset revision 保留 —— 新 revision 生效，旧 agent 仍用旧组合 ==")
    ctx = Context()
    svc = PresetService(ctx)                 # 构造即注册：ctx.agentPresets 指向 svc
    svc.register("writer", WRITER_V1)

    # agent-a 在 revision 1 创建，绑定这一代
    gen_a = svc.mount("agent-a", "writer")
    print(f"agent-a 创建时绑定的 revision: {gen_a.revision}")

    # 声明被改：revision 进位到 2（旧代退役，但不回收）
    svc.update("writer", WRITER_V2)

    # agent-b 在 revision 2 创建，拿到的是新的一代
    gen_b = svc.mount("agent-b", "writer")
    print(f"agent-b 创建时绑定的 revision: {gen_b.revision}")
    print(f"两代是同一个组合实例吗: {gen_a is gen_b}")
    print(f"agent-a 现在仍绑定的 revision: {svc.current_of('agent-a').revision}")
    print(f"agent-a 的组合里有没有 tool-web: {any(s.id == 'tool-web' for s in gen_a.services)}")
    print(f"agent-b 的组合里有没有 tool-web: {any(s.id == 'tool-web' for s in gen_b.services)}")

    # 同一 revision 内再建一个 agent：它和 agent-b 复用同一代
    gen_c = svc.mount("agent-c", "writer")
    print(f"同一 revision 内 agent-b 与 agent-c 复用同一代: {gen_b is gen_c}")

    # 子代理加入父代理的同一代，不按 id 去取当前 revision
    gen_child = svc.compose_from("agent-a-child", gen_a)
    print(f"agent-a 的子代理与父代理同一代: {gen_child is gen_a}")
    print(f"writer 累计真正挂载次数: {svc.mount_times['writer']}")
    print()


def demo_guards():
    """段 2：挂载守卫——「行没起来」与「泄漏到进程级」的挂载都会被拦下。"""
    print("== 段 2：挂载守卫 —— 为什么挂载要守卫 ==")
    ctx = Context()
    svc = PresetService(ctx)
    svc.register("broken-inactive", [
        {"id": "persona"},
        {"id": "flaky-tool", "activated": False},   # 声明了，但行没起来
    ])
    svc.register("broken-leak", [
        {"id": "persona"},
        {"id": "global-cache", "realm": "ROOT"},    # 服务发到了进程级 ROOT realm
    ])

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
    # [教学简化] apply_entry_patches 的 override 按 key 浅合并，所以 lang 原样保留；
    # 真实 patch 是整段替换被定向 entry 的 config，真实写法要把 lang 也写上
    print(f"override 只改 theme，lang 保留: settings.config = {settings['config']}")
    # disable 只翻禁用位，条目本身还在列表里（可追溯「它被谁关了」）
    print(f"disable 只翻禁用位: headless-startup.disabled = {by_id['headless-startup']['disabled']}")
    # insert 追加新条目
    print(f"insert 追加 server 后: {[e['id'] for e in result]}")
    print()


def demo_profile_boot():
    """段 4：profile 把 bundle 串成栈，boot 拍平成 entry 列表并激活。"""
    print("== 段 4：profile 栈 + boot 拍平 ==")
    # 真实 web 模板的 bundle 栈是 base → web-app（见
    # packages/util/official-bundles/src/bundles.ts:14-16）。教学版多留一层
    # headless，是为了让「web-app 禁用前一层插入的 headless-startup」有一个
    # 看得见的靶子——headless 层自己插的正是这一行（headless/cordis.patch.yml:15）。
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
    demo_revision_binding()
    demo_guards()
    demo_patch_not_merge()
    demo_profile_boot()


if __name__ == "__main__":
    main()
