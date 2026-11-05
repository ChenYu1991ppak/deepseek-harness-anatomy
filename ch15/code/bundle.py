"""第 15 章：bundle——「只有一个 payload 的 npm 包」，本质是一组命名的 patch。

源码对应（完整路径，均相对目标仓库根）：
- bundle 的载荷是 cordis.patch.yml，由 package.json 的 dsh.bundle.patch 点名
  （packages/bundle/base/package.json 的 "dsh.bundle" 字段）；
  packages/bundle/base/src/index.ts:1-7 只有一段说明注释与 export {}，
  这就是「入口只是占位」的出处；
- boot 侧按该字段取 patch 文件的函数是 packages/boot/app-boot/src/profile.ts:651
  resolveBundleDir；
- 三层 patch 各在 packages/bundle/{base,headless,web-app}/cordis.patch.yml。

教学版把「一个 bundle」建模为 Bundle(name, patches)：名字 + 一串 patch。
下面三个 bundle 对应真实三层：
- base     共享核心层：插入 harness 平面服务（settings/scope/session/agent/system-prompt）；
- headless 只插入 headlessStartup 提供者（packages/bundle/headless/cordis.patch.yml:15）；
- web-app  插入 Web 界面平面（server/api-proxy），并禁用 base 里逐会话的工具行
           （见 packages/bundle/web-app/cordis.patch.yml:518-601——「agent 平面
           移到 preset 身后」）。
"""
from patch import make_entry


class Bundle:
    """一个 bundle = 一个名字 + 一串 patch（它的「payload」）。"""

    def __init__(self, name, patches):
        self.name = name
        self.patches = patches    # list[dict]，每条都是一个 insert/override/disable

    def __repr__(self):
        return f"Bundle({self.name!r}, {len(self.patches)} patches)"


# --- 共享核心层 base：每个 profile 的第一层 patch，插入 harness 平面服务 ---
# 对应 packages/bundle/base/cordis.patch.yml（548 行，正文只摘代表性几行）
BASE = Bundle("base", [
    {"op": "insert", "entry": make_entry("settings")},
    {"op": "insert", "entry": make_entry("scope")},
    {"op": "insert", "entry": make_entry("session")},
    {"op": "insert", "entry": make_entry("agent")},
    {"op": "insert", "entry": make_entry("system-prompt")},
])

# --- headless 层：只插入 headlessStartup 提供者 ---
# 对应 packages/bundle/headless/cordis.patch.yml（46 行）
HEADLESS = Bundle("headless", [
    {"op": "insert", "entry": make_entry("headless-startup", {"provider": "headless"})},
])

# --- web-app 层：插入 Web 界面平面，并禁用 base 的逐会话工具行 ---
# 对应 packages/bundle/web-app/cordis.patch.yml（601 行）
WEB_APP = Bundle("web-app", [
    {"op": "insert", "entry": make_entry("server", {"port": 8080})},
    {"op": "insert", "entry": make_entry("api-proxy")},
    # 关键：后一层精确禁用前一层留下的条目——merge 做不到，patch 做得到
    {"op": "disable", "id": "headless-startup"},
    # 关键：只按 id 定向这一个 entry（教学版的 override 按键浅合并；真实 patch 会整段换 config）
    {"op": "override", "id": "settings", "config": {"theme": "dark"}},
])

# bundle 注册表：profile 按名字从这里取 bundle（类比 resolveBundleDir 的发现结果）
BUNDLE_REGISTRY = {b.name: b for b in (BASE, HEADLESS, WEB_APP)}
