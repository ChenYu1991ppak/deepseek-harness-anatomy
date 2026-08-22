"""第 12 章教学重构：skill 加载 —— 分段闭环演示。

仅标准库，Python 3.10+。运行：python3 ch12/code/main.py

主线：provider 注册（register_provider）→ 分层收集（_collect）→ rank+shadowing 合并
→ 缓存（revision）→ 按需加载（provider.get）→ 模型消费（tool-skill 的 skill 工具 + 会话目录注入）。

依赖前章（均经 sys.path 复用，不修改）：
- ch01/cordis：Context / Service（构造即注册）
- ch02/agent_loop：Session（会话目录注入的 append-only 事件日志）
- ch04/tools：ToolRuntime / ToolExecution（skill 工具走 tools 管线）
- ch09/scope：ScopedLayers（分层 shadowing 的教学底座）
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

_CH12 = Path(__file__).resolve().parent
_CODE_ROOT = _CH12.parents[1]

for _chapter in ("ch01", "ch02", "ch04", "ch09"):
    _entry = _CODE_ROOT / _chapter / "code"
    _entry_str = str(_entry)
    if _entry_str not in sys.path:
        sys.path.insert(0, _entry_str)
_CH12_STR = str(_CH12)
if _CH12_STR not in sys.path:
    sys.path.insert(0, _CH12_STR)

from cordis import Context  # noqa: E402
from agent_loop import Session  # noqa: E402
from tools import ToolRuntime, ToolExecution  # noqa: E402
from skill_registry import (  # noqa: E402
    PROJECT_SKILL_RANK,
    SkillCandidate,
    SkillDefinition,
    SkillProvider,
    SkillRegistry,
)
from skill_providers import BadgeSkillProvider, FilesystemSkillProvider  # noqa: E402
from tool_skill import SkillTool, inject_catalog  # noqa: E402


# ---------- 技能正文（写进临时目录，供 filesystem provider 扫描） ----------

COMMIT_BODY = """# commit-message

1. 读 git diff，归纳本次变更的主题。
2. 选择 type：feat / fix / docs / refactor / test / chore。
3. 输出 `type(scope): 描述`，描述用祈使句、不超过 72 字符。"""

REVIEW_BODY = """# code-review

1. 错误处理是否完整，异常路径是否被覆盖。
2. 边界条件（空输入、越界、并发）是否考虑到。
3. 命名是否表达意图，是否有重复代码可抽取。"""

RELEASE_BODY = """# release-notes

1. 汇总上一个 tag 以来的所有 PR。
2. 按 feat / fix / breaking change 分类。
3. 渲染成面向用户的 release notes。"""


def write_skill(root, name, description, body):
    """在 root 下写一个 SKILL.md：frontmatter（name/description）+ 正文。"""
    text = f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    (root / f"{name}.md").write_text(text, encoding="utf-8")


def banner(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


class ReviewBotSkillProvider(SkillProvider):
    """review-bot 作用域专属 provider：在 review-bot 层覆盖 global 的 code-review。

    用于演示「近层遮蔽远层」：同一个 code-review，review-bot 作用域读到的是本层
    版本，global 读到的仍是项目版本。
    """

    name = "review-bot"

    def list(self):
        return [
            SkillCandidate(
                name="code-review",
                description="review-bot 专用评审流程",
                provider=self.name,
                source="agent",
                rank=PROJECT_SKILL_RANK,
            )
        ]

    def get(self, candidate):
        return SkillDefinition(
            name=candidate.name,
            provider=self.name,
            content="review-bot 评审：先跑测试，再逐块看 diff，最后给出结论。",
            source="agent",
            description=candidate.description,
        )


def main():
    ctx = Context()
    # 统计 skills/change 事件：注册 / 失效都会广播变更（index.ts:649）。
    change_events = []
    ctx.on("skills/change", lambda payload: change_events.append(payload))
    root = Path(tempfile.mkdtemp(prefix="ch12-skills-"))
    try:
        _run(ctx, root, change_events)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        ctx.dispose()


def _run(ctx, root, change_events):
    # 段 1 —— 装配：provider 注册表
    banner("段 1 —— 装配：ctx.skills provider 注册表")
    SkillRegistry(ctx)  # Service，构造即注册 ctx.skills（对应 declare('skills')）
    print(f"ctx.skills 类型：{type(ctx.skills).__name__}（Service，构造即注册）")
    # 写两个项目技能文件，供 filesystem provider 扫描。
    write_skill(root, "commit-message", "按 Conventional Commits 生成提交信息", COMMIT_BODY)
    write_skill(root, "code-review", "项目代码评审清单", REVIEW_BODY)
    badge = BadgeSkillProvider()
    filesystem = FilesystemSkillProvider(root)
    ctx.skills.register_provider(badge)
    ctx.skills.register_provider(filesystem)
    print(f"已注册 provider：{ctx.skills.snapshot()['providers']}")
    print(f"注册触发的 skills/change 事件数：{len(change_events)}")

    # 段 2 —— 目录合并 + 同层 rank 决胜
    banner("段 2 —— 目录合并 + 同层 rank 决胜（小者胜）")
    catalog = ctx.skills.list()
    for summary in catalog:
        print(f"- {summary.name:<15} provider={summary.provider:<10} source={summary.source}")
    review = next(s for s in catalog if s.name == "code-review")
    print(f"code-review 取胜者：{review.provider}（项目 rank=100 < 内置 rank=600，小者胜）")

    # 段 3 —— 分层 shadowing（ScopedLayers 复用，近层遮蔽远层）
    banner("段 3 —— 分层 shadowing：review-bot 作用域遮蔽 global")
    ctx.skills.register_provider(ReviewBotSkillProvider(), scope="review-bot")
    global_review = next(s for s in ctx.skills.list() if s.name == "code-review")
    bot_review = next(s for s in ctx.skills.list(scope="review-bot") if s.name == "code-review")
    print(f"global 读 code-review：{global_review.provider}")
    print(f"review-bot 读 code-review：{bot_review.provider}（本层更近，整体遮蔽 global）")
    print(f"review-bot 目录技能数：{len(ctx.skills.list(scope='review-bot'))}（继承 global + 本层覆盖）")

    # 段 4 —— 按需加载全文（get）
    banner("段 4 —— 按需加载全文：get('commit-message')")
    definition = ctx.skills.get("commit-message")
    print(f"provider={definition.provider} source={definition.source}")
    print(definition.content)

    # 段 5 —— tool-skill 消费：skill 工具
    banner("段 5 —— tool-skill 消费：skill 工具（走第 4 章 tools 管线）")
    ToolRuntime(ctx)  # Service，构造即注册 ctx.tools
    SkillTool(ctx).apply()
    print(f"ctx.tools 可见工具：{[d.name for d in ctx.tools.view()]}")
    result = ctx.tools.execute(ToolExecution("call-1", "skill", {"name": "commit-message"}))
    print(f"skill(commit-message) is_error={result.is_error}：")
    print(result.content)
    missing = ctx.tools.execute(ToolExecution("call-2", "skill", {"name": "no-such-skill"}))
    print(f"skill(no-such-skill) → 工具返回错误文本：{missing.content}")

    # 段 6 —— 会话目录注入
    banner("段 6 —— 会话目录注入：skill-catalog 消息进会话")
    session = Session(ctx, "session-0001")
    message = inject_catalog(session, ctx.skills.list())
    print(f"注入消息 source={message['source']}，会话事件数={len(session.log)}")
    print(message["content"])

    # 段 7 —— 缓存与失效
    banner("段 7 —— 缓存与失效")
    ctx.skills.invalidate()
    base = filesystem.scan_count  # 以当前扫盘次数为基准，后面用相对增量说明缓存
    first = ctx.skills.list()
    print(f"第一次 list()：{len(first)} 个技能，扫盘 +{filesystem.scan_count - base}（真实扫盘）")
    second = ctx.skills.list()
    print(f"第二次 list()：{len(second)} 个技能，扫盘 +{filesystem.scan_count - base}（缓存命中，未新增）")
    write_skill(root, "release-notes", "生成面向用户的 release notes", RELEASE_BODY)
    stale = ctx.skills.list()
    print(f"新增 release-notes.md 后直接 list()：{[s.name for s in stale]}（仍命中旧缓存）")
    ctx.skills.invalidate()
    fresh = ctx.skills.list()
    print(f"invalidate() 后 list()：{[s.name for s in fresh]}（重新扫盘）")
    print(f"全程 skills/change 事件数：{len(change_events)}")


if __name__ == "__main__":
    main()
