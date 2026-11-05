"""ch17 教学代码：三条启动面共享的 boot（对应 apps/cli → profile-boot → app-boot）。

真实源码（HEAD 8bd0733）：
- 唯一的 Node 启动器是 `dsh` 命令本身：`apps/cli/src/bin.ts:29` 解析 argv，
  `profile` 模式下 `runProfile({ profile, fromDefaultProfile, patchFiles, args })`
  （`apps/cli/src/bin.ts:35`）。SDK 与 ACP 都不是独立可执行文件，而是 profile。
- `runProfile`：`apps/cli/src/profile-boot.ts:271`。它先装代理环境
  （`:276`），再调 `composeProfile`（`:292`），最后 `boot(...)`（`:323`）。
- `composeProfile`：`apps/cli/src/profile-boot.ts:224` —— 只负责「装载 profile +
  计算运行时解析 + 收集 --patch 覆盖」，不负责实例化插件。
- `boot`：`packages/boot/app-boot/src/index.ts:1200` —— 把 patch 层叠成的树
  挂到空根配置上。
- 「三层 patch」的层序在 `apps/cli/README.md:39-44`：bundle 层（按
  `dsh.profile.bundles` 顺序）→ profile 的 `cordis.patch.yml` →
  `$DSH_HOME/cordis.patch.yml` → `--patch` 覆盖。
- 四个 shipped profile 的 bundle 列表：`packages/util/official-bundles/src/bundles.ts:10-26`
  —— sdk = base + sdk-app，web = base + web-app，headless = base + headless，
  acp = base + acp-app，sdk-minimal 自带整棵树。

[教学简化] 这里把「YAML patch 层叠」压成一个 bundle 名字列表 + 一个字典，
不实现 YAML 解析、包解析与插件工厂。
[教学决策] 用真实 profile 名与 bundle 名，让「三条表面 = 三套 bundle 列表、
同一个 base」在输出里直接可见；这正是 §4 要讲的装配事实。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))

from cordis import Context, Service, Symbols  # noqa: E402

# 对应 PROFILE_TEMPLATES（packages/util/official-bundles/src/bundles.ts:10）
PROFILE_BUNDLES = {
    "sdk": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-sdk-app"],
    "web": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"],
    "headless": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless"],
    "acp": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-acp-app"],
}

# [教学简化] 每个 bundle 展开成几个代表性服务名；真实 bundle 是几十行的
# cordis.patch.yml，见 packages/bundle/*/cordis.patch.yml。
BUNDLE_SERVICES = {
    "@deepseek-ai/dsh-base": [
        "session", "agent-loop", "system-prompt", "tools",
        "workflowEngine", "ptcRuntime",          # base 的 workflow-ptc 行（:408-409）
    ],
    "@deepseek-ai/dsh-sdk-app": ["sdk-jsonrpc-server"],
    "@deepseek-ai/dsh-web-app": ["webStartup", "connection"],
    "@deepseek-ai/dsh-headless": ["headless-runner"],
    "@deepseek-ai/dsh-acp-app": ["acp-bridge"],
}


class StubService(Service):
    """[教学简化] 占位服务：只记录名字，不实现真实逻辑。"""

    def __init__(self, ctx: Context, name: str):
        super().__init__(ctx, name)


def compose_profile(profile: str, patches=()):
    """对应 composeProfile（apps/cli/src/profile-boot.ts:224）：算出层叠后的配置。

    返回的是「要装什么」，不是「已经装好」——真实实现同样把装载与实例化分开。
    """
    if profile not in PROFILE_BUNDLES:
        raise ValueError(f"unknown profile: {profile}")
    entries = []
    for bundle in PROFILE_BUNDLES[profile]:
        entries.extend(BUNDLE_SERVICES[bundle])
    entries.extend(patches)          # --patch 覆盖层最后应用
    return entries


def boot(profile: str, patches=()):
    """对应 runProfile → boot（profile-boot.ts:271 / app-boot/src/index.ts:1200）。

    顺序与真实实现一致：compose（算清单）→ 挂到空根 → 逐个实例化。
    [教学简化] 省略 ready 事件、fiber 与 dispose 顺序管理。
    """
    ctx = Context()
    for name in compose_profile(profile, patches):
        StubService(ctx, name)       # Service 构造即注册到 ctx.<name>
    return ctx


def booted_services(ctx: Context) -> list:
    """枚举容器里已注册的服务名（按注册序），复用 ch01 的服务表。"""
    return list(getattr(ctx, Symbols.services).keys())
