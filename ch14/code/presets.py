"""第 14 章教学重构：permission-presets（授权捆绑）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（packages/interaction/permission-presets/src/，行号见 notes §2.2）：
- derive               ↔ index.ts:309（preset 名 → sandbox+approval 策略捆绑）
- apply                ↔ index.ts:380（应用捆绑）
- pinInitialPermission ↔ index.ts:400（钉住初始权限）
- Config.presets       ↔ index.ts:167（配置入口）
- /permission 命令     ↔ index.ts:257-277（列出或切换 preset）
- preset 名            ↔ PresetSpec/PermissionSelect（types.ts）（workspace-write / danger-full-access）

permission-presets 是 ch15 bundle 组合思想的先导（notes §5 第 2 条）：
一个 preset 名同时决定 sandbox 半与 approval 半。
"""
from __future__ import annotations

PRESETS = {
    # [教学决策] notes 未逐项给出各 preset 的 derive 输出；教学版给出自洽的捆绑形态：
    # sandbox 半为模式串（[教学简化] 不接第 6 章 SandboxProvider），approval 半为 ask/never 策略。
    "workspace-write": {
        "sandbox": "workspace-write",
        "approval": "ask",
        "ask_tools": ("shell", "fs_write"),
    },
    "danger-full-access": {
        "sandbox": "off",
        "approval": "ask",
        "ask_tools": (),  # 全放行：没有工具产生 ask
    },
}


def derive(preset):
    """derive（index.ts:309）：由 preset 名推导 sandbox+approval 捆绑；未知 preset 抛 ValueError。"""
    if preset not in PRESETS:
        raise ValueError(f"未知 preset：{preset}（可用：{', '.join(PRESETS)}）")
    return dict(PRESETS[preset])


def apply(ctx, preset):
    """apply（index.ts:380）：把 approval 策略写入 ctx.approval，返回完整捆绑。"""
    bundle = derive(preset)  # derive（index.ts:309）：名字 → 捆绑，未知抛 ValueError
    ctx.approval.set_policy(bundle["approval"])
    return bundle


def pin_initial_permission(ctx, preset):
    """pinInitialPermission（index.ts:400）：钉住初始权限——启动时应用并记录钉住值。

    [教学决策] 真实 pin 语义 notes 未展开；教学版以容器属性记录被钉住的 preset 名，
    供 /permission 命令展示。
    """
    bundle = apply(ctx, preset)
    ctx.pinned_preset = preset
    return bundle


def register_permission_command(ctx, gate):
    """/permission 命令（index.ts:257-277）：无参列出 presets，带参切换 preset。

    切换时同步更新 ask 闸白名单——preset 是捆绑，授权范围随捆绑整体切换。
    """

    def handler(args_text):
        args_text = args_text.strip()
        if not args_text:
            return "可用 presets: " + ", ".join(PRESETS)
        bundle = apply(ctx, args_text)
        gate.ask_tools = set(bundle["ask_tools"])
        return f"已切换 {args_text}: sandbox={bundle['sandbox']}, approval={bundle['approval']}"

    ctx.commands.register("permission", handler)
