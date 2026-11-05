"""第 14 章教学重构：permission-presets（授权捆绑）。

仅标准库，Python 3.10+。运行：python3 -m main

源码对应（HEAD 8bd073392，packages/interaction/permission-presets/src/）：
- Config.presets / defaultPreset  ↔ index.ts:153（默认表两行：workspace-write=ask、danger-full-access=never，:177-189）
- PresetSpec                      ↔ index.ts:58（sandbox + approval，外加两个展示字段）
- derive()                        ↔ index.ts:348（折叠态 → 名字；对不上任何表项返回 'custom'）
- resolve()                       ↔ index.ts:372（名字 → 捆绑；未知名字抛错）
- apply()                         ↔ index.ts:403（先写 permission/preset，再逐项写 sandbox / approval）
- set()                           ↔ index.ts:399（会话侧入口：apply + setApprovalPolicy）
- pinInitialPermission()          ↔ index.ts:429（agent 创建时补齐缺失的权限事实）
- /permission 命令                ↔ index.ts:254-276（无参列当前值，带参切换）

两个与旧叙事不同的点（按 HEAD 修正）：
1. danger-full-access 的 approval 是 **never**，不是 ask——它在审批层就短路，不靠白名单。
2. derive 是「由当前旋钮值反推名字」，对不上就返回 'custom'；名字 → 捆绑是 resolve。

permission-presets 是 ch15 bundle 组合思想的先导：一个 preset 名同时决定 sandbox 半与 approval 半。
它不决定哪些调用会产生 ask（那是 tools/pre-execute 监听器的事），只决定这些 ask
到了裁决入口之后是被交给人、还是被 never 直接拒。
danger-full-access 的 never 不是「放行」，而是「永不询问」——任何 ask 都被裁决层直接判成 rejected
（user-approval/index.ts:64、:301）。它之所以少问，靠的是 sandbox 半已经给了全盘访问。
"""
from __future__ import annotations

CUSTOM_PRESET = "custom"  # 对不上任何表项的派生态，不是可切换目标（index.ts:73）

# 与 HEAD 的默认表逐项一致；教学版无客户端，省略 name/description 两个展示字段
PRESETS = {
    "workspace-write": {"sandbox": "workspace-write", "approval": "ask"},
    "danger-full-access": {"sandbox": "danger-full-access", "approval": "never"},
}


def resolve(preset):
    """resolve（index.ts:372）：名字 → 捆绑；未知名字抛 ValueError。"""
    if preset not in PRESETS:
        raise ValueError(f"未知 preset：{preset}（可用：{', '.join(PRESETS)}）")
    return dict(PRESETS[preset])


def derive(ctx):
    """derive（index.ts:348）：由当前旋钮值反推 preset 名；对不上返回 'custom'。"""
    sandbox = getattr(ctx, "sandbox_mode", "workspace-write")
    approval = ctx.approval.effective_policy()
    for name, spec in PRESETS.items():
        if spec["sandbox"] == sandbox and spec["approval"] == approval:
            return name
    return CUSTOM_PRESET


def apply(ctx, preset):
    """apply（index.ts:403）：先记 preset 身份，再逐项写 sandbox / approval 旋钮。"""
    spec = resolve(preset)
    if getattr(ctx, "current_preset", None) != preset:
        ctx.emit("permission/preset", preset)  # index.ts:416
    ctx.current_preset = preset
    ctx.sandbox_mode = spec["sandbox"]  # 真实版写 sandbox/mode 事件，并在派生时校验
    ctx.approval.set_policy(spec["approval"])
    return spec


def pin_initial_permission(ctx, preset):
    """pinInitialPermission（index.ts:429）：agent 创建时补齐缺失的权限事实。

    真实版只补「缺失」的旋钮（新建会话用默认值，已有会话保留原值）。
    """
    if getattr(ctx, "_permission_pinned", False):
        return None
    ctx._permission_pinned = True
    return apply(ctx, preset)


def register_permission_command(ctx):
    """/permission 命令（index.ts:254-276）：无参显示当前 preset，带参切换。"""

    def handler(args_text):
        args_text = args_text.strip()
        if not args_text:
            return f"current preset {derive(ctx)} (available: {', '.join(PRESETS)})"
        apply(ctx, args_text)
        return f"preset {args_text}"

    ctx.commands.register("permission", handler)
