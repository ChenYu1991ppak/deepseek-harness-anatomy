"""第 15 章：bundle——「只有一个 payload 的 npm 包」，本质是一组命名的 patch。

真实结构（notes §4）：
- bundle 的 TS 入口只是占位（packages/bundle/base/src/index.ts 仅 10 行注释），
  逻辑全在 cordis.patch.yml；
- package.json 用 "dsh": {"bundle": {"patch": "./cordis.patch.yml"}} 声明，
  dsh.bundle.patch 是 boot 侧发现 bundle 的唯一契约（profile.ts:344 resolveBundleDir）。

教学版把「一个 bundle」建模为 Bundle(name, patches)：名字 + 一串 patch。
下面三个 bundle 对应真实三层（notes §4.2）：
- base    共享核心层：插入 harness 平面服务（settings/scope/session/agent/system-prompt）；
- headless 只插入 headlessStartup 提供者；
- web-app 插入 Web 界面平面（server/api-proxy），并禁用 headless 的启动提供者。
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
# 对应 packages/bundle/base/cordis.patch.yml（452 行，正文只摘代表性几行）
BASE = Bundle("base", [
    {"op": "insert", "entry": make_entry("settings")},
    {"op": "insert", "entry": make_entry("scope")},
    {"op": "insert", "entry": make_entry("session")},
    {"op": "insert", "entry": make_entry("agent")},
    {"op": "insert", "entry": make_entry("system-prompt")},
])

# --- headless 层：只插入 headlessStartup 提供者 ---
# 对应 packages/bundle/headless/cordis.patch.yml（36 行）
HEADLESS = Bundle("headless", [
    {"op": "insert", "entry": make_entry("headless-startup", {"provider": "headless"})},
])

# --- web-app 层：插入 Web 界面平面，并禁用 headless 的启动提供者 ---
# 对应 packages/bundle/web-app/cordis.patch.yml（425 行）
WEB_APP = Bundle("web-app", [
    {"op": "insert", "entry": make_entry("server", {"port": 8080})},
    {"op": "insert", "entry": make_entry("api-proxy")},
    # 关键：后一层精确禁用前一层插入的条目——merge 做不到，patch 做得到
    {"op": "disable", "id": "headless-startup"},
    # 关键：id 定向覆盖 settings 的某个 config 键，其余键不动
    {"op": "override", "id": "settings", "config": {"theme": "dark"}},
])

# bundle 注册表：profile 按名字从这里取 bundle（类比 resolveBundleDir 的发现结果）
BUNDLE_REGISTRY = {b.name: b for b in (BASE, HEADLESS, WEB_APP)}
