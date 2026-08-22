"""第 15 章：preset——会话级「面向模型的插件组合」，题眼是 standing mount。

真实结构（notes §3）：
- AgentPresets 服务（packages/preset/agent-presets/src/index.ts:82，extends Service）；
- standing Map（index.ts:252）按 preset id 缓存「常驻挂载」的 Promise——单飞：
  同一 preset 只真正挂载一次，后续命中的是缓存（ensureStanding，index.ts:491）；
- 三个 join 入口 mount(275)/composeFrom(316)/recompose(458) 都先 ensureStanding 再绑 scope；
- 实际挂载 mountPreset（mount.ts:332）挂完子树后跑「双守卫」，任一不过即抛
  PresetMountError 并 dispose 子树。

[教学简化] 真实 standing 缓存的是 Promise（异步单飞），教学版缓存同步结果；
真实 scope 绑定走 bindScopeParent（把 agent 的 scope key 挂到 resident key 下），
教学版用「把 agent 的 scope key 记进 mount.joined」表达「join 同一组合实例」。
"""
from __future__ import annotations

import os
import sys

# 复用第 1 章：Service 构造即注册（cordis.py:271–284）。
_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)
from cordis import Service  # noqa: E402


class PresetMountError(Exception):
    """挂载未通过双守卫时抛出（对应 mount.ts 的 PresetMountError，preset.ts:83）。"""


class MountedService:
    """组合里的一个服务实例。realm 标记它注册在哪一层：
    "isolate" = 会话隔离层（正确）；"ROOT" = 进程级（泄漏）。activated = 是否被激活。
    """

    def __init__(self, service_id, activated=True, realm="isolate"):
        self.id = service_id
        self.activated = activated
        self.realm = realm


class StandingMount:
    """一个 preset 的「常驻挂载」实例：插件实例唯一，多个 agent join 同一份。"""

    def __init__(self, preset_id, services):
        self.preset_id = preset_id
        self.services = services    # 该组合挂载出的全部服务实例
        self.joined = []            # 已 join 的 agent scope key 列表


class PresetService(Service):
    """会话级 preset 服务：常驻挂载 + 多 agent join + 双守卫。

    [教学决策] 真实 AgentPresets 还负责发现/读写/重组合（list/read/copy/recompose 等），
    教学版只保留与本章题眼直接相关的 mount / ensure_standing / 双守卫。
    """

    def __init__(self, ctx, presets, name="agentPresets"):
        super().__init__(ctx, name)      # 构造即注册：ctx.agentPresets 指向本实例
        self._presets = presets          # preset_id -> 插件规格列表（每个规格产出一个服务）
        self._standing = {}              # preset_id -> StandingMount（常驻挂载缓存，单飞）
        self.mount_times = {}            # preset_id -> 真正挂载次数（教学观察用）

    def ensure_standing(self, preset_id):
        """常驻挂载：只挂一次，命中缓存直接复用（对应 ensureStanding，index.ts:491）。

        返回同一个 StandingMount 实例，是「一个 preset 只挂一次、多 agent join」的根。
        """
        cached = self._standing.get(preset_id)
        if cached is not None:
            return cached                       # 命中缓存：不再挂载，直接复用
        specs = self._presets[preset_id]        # 未命中：取出该 preset 的插件规格
        services = [self._mount_one(spec) for spec in specs]
        mount = StandingMount(preset_id, services)
        self._guard(mount)                      # 双守卫：任一不过即抛错，且不进缓存
        self._standing[preset_id] = mount       # 通过守卫才写入常驻缓存
        self.mount_times[preset_id] = self.mount_times.get(preset_id, 0) + 1
        return mount

    def mount(self, agent_scope_key, preset_id):
        """agent 挂载入口：ensureStanding + join（对应 mount，index.ts:275）。

        多个 agent 用各自 scope key 调它，拿到的是同一个 StandingMount——
        这就是「standing mount：一个 preset 只挂一次、多 agent join」。
        """
        mount = self.ensure_standing(preset_id)
        mount.joined.append(agent_scope_key)    # 该 agent join 到同一组合实例
        return mount

    def _mount_one(self, spec):
        """按规格挂出一个服务实例（教学版直接按 spec 字段构造）。"""
        return MountedService(
            spec["id"],
            activated=spec.get("activated", True),
            realm=spec.get("realm", "isolate"),
        )

    def _guard(self, mount):
        """双守卫（对应 mountPreset 挂载后的两道检查，mount.ts:332–381）。

        守卫一 inactiveRows（mount.ts:283）：有声明却未被激活的条目 → 组合不完整。
        守卫二 leakedServices（mount.ts:189）：有服务注册到进程级 ROOT realm
        而非会话隔离层 → 会话间会互相看见，必须拦下。
        任一不过即抛 PresetMountError；教学版不缓存失败挂载，故每次都会重挂并重检。
        """
        inactive = [s.id for s in mount.services if not s.activated]
        if inactive:
            raise PresetMountError(f"inactiveRows: {inactive} 声明了但未被激活")
        leaked = [s.id for s in mount.services if s.realm == "ROOT"]
        if leaked:
            raise PresetMountError(f"leakedServices: {leaked} 注册到了进程级 ROOT realm")
