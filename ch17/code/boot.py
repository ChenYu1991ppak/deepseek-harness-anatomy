"""ch17 教学代码：三条启动面共享的 boot（对应 packages/boot/app-boot）。

真实源码（17b 笔记）：
- `boot()` 定义在 `packages/boot/app-boot/src/index.ts`，职责是
  "加载 runtime 配置 → 解析 profile → 按顺序实例化插件 → 触发 ready"。
- 三条表面共享同一 boot 库：
  - Python 内嵌：`dsh-sdk` 直接调 `boot()`（17b 笔记「Python 内嵌面」节）；
  - CLI：`apps/cli/src/index.ts` 的 `main()` 调 `composeProfile()`（profile-boot.ts:142）
    再走 boot；
  - Web：`apps/web` 入口 + `packages/host` 网关同样走 boot，
    并把结果挂到 `window.__DSH_BOOT__`。
- 默认插件清单来自 `runtime/cordis.yml`，实际装载 8 个 id（17b 笔记 §2.6）：
  sdk-jsonrpc-server / agent-core / llm-deepseek / sessions /
  session-checkpoints / subprocess / bash / fs-local。

[教学简化] 这里把「profile 决定插件清单 → 逐个实例化」压成一个字典 + 循环，
不实现 cordis.yml 的 YAML 解析与插件工厂。
[教学决策] 三个 profile 的「公共基座」刻意列出前几章的服务名
（session / agent-loop / system-prompt / tools），让读者直观看到：
表面不同，内核是同一棵树——这正是 17b 笔记的结论
「三条启动面共享同一 cordis 插件树」。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))

from cordis import Context, Service, Symbols  # noqa: E402

# [教学决策] 基座用前几章的服务名示意；真实 runtime/cordis.yml 的
# 8 个 id 见模块 docstring（sdk-jsonrpc-server / agent-core / ...）
BASE_SERVICES = ["session", "agent-loop", "system-prompt", "tools"]

# 每条表面在基座之上追加的差异化服务
PROFILES = {
    "python-sdk": BASE_SERVICES + ["sdk-runtime"],          # dsh-sdk 内嵌面
    "cli": BASE_SERVICES + ["terminal-ui"],                 # apps/cli 面
    "web": BASE_SERVICES + ["server", "api-proxy"],         # apps/web + packages/host 面
}


class StubService(Service):
    """[教学简化] 占位服务：只记录名字，不实现真实逻辑。

    真实插件（如 session、agent-loop）在第 3、2 章已拆解；
    这里只关心「谁被注册进了容器」。
    """

    def __init__(self, ctx: Context, name: str):
        super().__init__(ctx, name)


def boot(profile: str) -> Context:
    """按 profile 实例化插件清单，返回装配好的 Context。

    对应真实 `boot()` 的骨架：解析配置 → 按序实例化 → ready。
    [教学简化] 省略 ready 事件与 dispose 顺序管理。
    """
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile}")
    ctx = Context()
    for name in PROFILES[profile]:
        StubService(ctx, name)  # Service 构造即注册到 ctx.<name>
    return ctx


def booted_services(ctx: Context) -> list:
    """枚举容器里已注册的服务名（按注册序）。

    利用 ch01 的服务表（Symbols.services，即 `__cordis_services__`）枚举。
    """
    return list(getattr(ctx, Symbols.services).keys())
