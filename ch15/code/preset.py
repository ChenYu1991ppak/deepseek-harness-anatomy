"""第 15 章：preset——会话级「面向模型的插件组合」，题眼是 revision 保留。

源码对应（完整路径，均相对目标仓库根 /Users/chenyu/PycharmProjects/deepseek-harness）：
- 声明行：packages/preset/agent-preset/src/index.ts:12 `class AgentPreset`，
  :13 `static inject = ['agentPresets']`，:27-29 `[Service.init]` 把 config 交给
  `ctx.agentPresets.register(...)`；
- 注册表：packages/preset/agent-preset-registry/src/index.ts:54
  `class AgentPresetRegistry extends TypertRemoteService`，:33 `interface Generation`，
  :248 `retain(id)`，:297 `mount(ctx, id)`，:313 `composeFrom(ctx, parent)`；
- 挂载 + 守卫：packages/preset/agent-preset-registry/src/mount.ts:204
  `mountPreset(ctx, id, plugins)`（:205 要求 `scopeOf(ctx) !== undefined`），
  :69 `leakedServices`，:134 `auditRows`；
- 组合内的人格行：packages/preset/persona/src/index.ts:4-8（为什么只能挂在
  scope 里）与 :62（apply 注册 prefix/suffix 段）。

[教学简化] 真实实现里：
1. 一个 preset 定义在 `register` 时就被 `activate` 挂载成一代 `Generation`
   （index.ts:119），之后内容变化才换代；教学版把「挂载」推迟到第一次 `retain`，
   这样能直观看清「每一代都有自己的服务实例」。
2. 真实用 `bindScopeParent` 把 agent 的 scope key 的父指针指向该代的 scope key
   （index.ts:282），服务可见性由 Cordis 的 scope 链决定；教学版用一个显式的
   `Generation.bindings` 列表表达「这个 agent 认这一代」。
3. 真实守卫是 `auditRows`（failed/pending 两类诊断）+ `leakedServices`，
   `pending` 的行不判失败（等 Host 服务到位后自愈）；教学版只用
   「激活失败」一条判定（报成 auditRows.failed），pending 不单独建模。
"""
from __future__ import annotations

import os
import sys

# 复用第 1 章：Service 构造即注册（cordis.py:233 起）。
_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)
from cordis import Service  # noqa: E402


class PresetMountError(Exception):
    """一代组合没通过挂载守卫时抛出（对应 mount.ts:212-213 的两条 throw）。"""


class MountedService:
    """组合里的一个服务实例。realm 标记它注册在哪一层：
    "isolate" = 该代自己的隔离层（正确）；"ROOT" = 进程级（泄漏）。
    activated = 是否被激活（对应 auditRows 的 failed 行）。
    """

    def __init__(self, service_id, activated=True, realm="isolate"):
        self.id = service_id
        self.activated = activated
        self.realm = realm


class Generation:
    """一代组合（对应 index.ts:33 `interface Generation`）。

    真实字段是 { scope, key, mount, users, retired }：scope 是这一代自己的子容器，
    users 是还引用它的 Agent / 子代理 / 历史读的计数，retired 表示定义已换代。
    教学版再加两个观察字段：services（这一代挂载出的服务实例）与 bindings
    （认这一代的 agent scope key 列表）。
    """

    def __init__(self, preset_id, revision, services):
        self.preset_id = preset_id
        self.revision = revision
        self.services = services      # 这一代自己的服务实例，代与代之间不共享
        self.users = 0                # 还引用这一代的 Agent 数（对应 users 计数）
        self.retired = False          # 定义换代后置真（对应 retired）
        self.bindings = []            # 认这一代的 agent scope key（教学观察用）


class PresetService(Service):
    """会话级 preset 注册表：声明式定义 + revision 保留 + 挂载守卫。

    [教学决策] 真实 AgentPresetRegistry 还负责清单读取（list/read/
    compositionInventory）、Remote 导出与 Web 编辑器的落盘；教学版只保留与本章
    题眼直接相关的 register / retain / mount / 守卫。
    """

    def __init__(self, ctx, name="agentPresets"):
        super().__init__(ctx, name)   # 构造即注册：ctx.agentPresets 指向本实例
        self._defs = {}               # preset_id -> {"revision", "plugins", "match"}
        self._current = {}            # preset_id -> 当前的 Generation
        self._frozen = {}             # preset_id -> 已退役但仍有引用的旧代列表
        self.mount_times = {}         # preset_id -> 累计真正挂载次数（教学观察用）

    # ---------- 声明去 ----------

    def register(self, preset_id, plugins):
        """声明一套 preset（对应 AgentPreset 在 [Service.init] 里调 register，
        packages/preset/agent-preset/src/index.ts:27-29）。

        真实路径里「改定义」= 用 patch 覆盖声明行、重新走一次 register；
        教学版把它拆成 register（首发）与 update（改版）两个动作。
        """
        if preset_id in self._defs:
            raise ValueError(f"Duplicate agent preset: {preset_id}")   # 对应 index.ts:100
        self._defs[preset_id] = {
            "revision": 1,
            "plugins": list(plugins),   # 组合清单（对应 config.plugins）
        }

    def update(self, preset_id, plugins):
        """原地改一套 preset 的声明：revision 进位，旧代不回收。

        对应真实路径：注册表把旧的 `record.generation.retired = true`
        （index.ts:109-112），旧代继续为已绑定的 Agent 服务。
        """
        self._defs[preset_id]["revision"] += 1
        self._defs[preset_id]["plugins"] = list(plugins)
        old = self._current.get(preset_id)
        if old is not None:
            old.retired = True          # 旧代退役，但仍在为已绑定的 agent 服务
            self._frozen.setdefault(preset_id, []).append(old)

    # ---------- 保留与绑定 ----------

    def retain(self, preset_id):
        """取当前 revision 的那一代，引用计数 +1（对应 retain，index.ts:248）。

        [教学简化] 真实实现 register 时就 eager 挂载成一代（index.ts:114-115），
        retain 只拿 record.generation；教学版推迟到第一次取才物化。无论哪种，
        同一 revision 内反复取拿到的都是同一代——「复用」由此而来，
        但它不再是全局单例：换代之后就是另一代了。
        """
        self._check(preset_id)
        generation = self._current.get(preset_id)
        if generation is None or generation.revision != self._defs[preset_id]["revision"]:
            generation = self._materialize(preset_id)   # 未挂载 / 已换代：新挂一代
        generation.users += 1
        return generation

    def release(self, generation):
        """归还一次 retain 的引用（对应 index.ts:302-305 的 finally 里的 users--）。"""
        generation.users -= 1

    def mount(self, agent_scope_key, preset_id=None):
        """把一个 Agent 绑到「当前 revision」的那一代（对应 mount，index.ts:297）。

        返回该代；agent 之后一直用这一代，哪怕定义再改也不迁移——
        对应真实实现里的 `bindScopeParent(key, generation.key)`（index.ts:282）。
        """
        self._check(preset_id)
        generation = self.retain(preset_id)
        generation.bindings.append(agent_scope_key)
        self.release(generation)        # 绑定本身持有引用，retain 的临时引用还掉
        return generation

    def compose_from(self, child_scope_key, parent_generation):
        """子代理加入父代理的**同一代**（对应 composeFrom，index.ts:313）。

        关键：子代理不按 id 重新取当前 revision，而是认父代理手里那一代——
        否则父代理还在用旧组合，子代理却跑新组合，父子看到的工具集就会不一致。
        """
        parent_generation.bindings.append(child_scope_key)
        return parent_generation

    def current_of(self, agent_scope_key):
        """读这个 Agent 认的那一代（对应 generationFor / composedPreset）。"""
        for generation in [*self._current.values(), *self._all_frozen()]:
            if agent_scope_key in generation.bindings:
                return generation
        return None

    # ---------- 内部 ----------

    def _materialize(self, preset_id):
        """按当前定义真挂一代，并在写进 _current 之前跑守卫（对应 activate，index.ts:119）。"""
        definition = self._defs[preset_id]
        services = [self._mount_one(spec) for spec in definition["plugins"]]
        generation = Generation(preset_id, definition["revision"], services)
        self._guard(generation)
        self._current[preset_id] = generation
        self.mount_times[preset_id] = self.mount_times.get(preset_id, 0) + 1
        return generation

    def _mount_one(self, spec):
        """按规格挂出一个服务实例（教学版直接按 spec 字段构造）。"""
        service = MountedService(
            spec["id"],
            activated=spec.get("activated", True),
            realm=spec.get("realm", "isolate"),
        )
        return service

    def _guard(self, generation):
        """挂载守卫（对应 mountPreset 挂完后跑 auditRows + leakedServices，mount.ts:210-213）。

        守卫一 auditRows（mount.ts:134）：有行没起来（never started / 激活失败）→
        组合不完整。真实实现把「等 Host 服务」的 pending 行另算、不判失败。
        守卫二 leakedServices（mount.ts:69）：有服务把实现发到了 ROOT realm →
        会话之间会互相看见，必须拦下。
        任一不过即抛 PresetMountError；教学版不缓存失败的那一代。
        """
        failed = [s.id for s in generation.services if not s.activated]
        if failed:
            raise PresetMountError(f"auditRows.failed: {failed} 没有起来的行")
        leaked = [s.id for s in generation.services if s.realm == "ROOT"]
        if leaked:
            raise PresetMountError(f"leakedServices: {leaked} 注册到了进程级 ROOT realm")

    def _all_frozen(self):
        for generations in self._frozen.values():
            yield from generations

    def _check(self, preset_id):
        if preset_id not in self._defs:
            raise KeyError(f"Unknown agent preset: {preset_id}")
