"""第 12 章教学重构：skill 加载 —— Service Provider（实现层）。

仅标准库，Python 3.10+。运行：python3 main.py

两个 provider 范本，对应真实两个实现包：
- BadgeSkillProvider      ↔ packages/skill/skill-badge/src/index.ts（内置，静态 list + 读资源 get）
- FilesystemSkillProvider ↔ packages/skill/skill-filesystem/src/index.ts（目录扫描 + parse SKILL.md）

源码对应：
- badge：PROVIDER_NAME:17、CANDIDATE:25（source='bundled'、rank=BUNDLED_SKILL_RANK）、
  provider:36（list→[CANDIDATE]、get→readFile）、apply:58（一行 registerProvider）
- filesystem：name:45、FileSystemSkillProvider:146（list:182、get:206、roots:241）、
  parseSkillFile:793、parseFrontmatter:909、discoverRoot:719

[教学决策 2] 两个 provider 同现，展示「provider 注册表」型 seam 的多实现并存：
badge 是「最简内置 provider」（静态候选 + 内置正文），filesystem 是「目录扫描 provider」。
[教学决策 3] badge 内置两个技能（skill-creator + code-review 的 bundled 版），
让 bundled 版 code-review 与 filesystem 的项目版 code-review 同名，
以便在同一个 global 层内演示「同层 rank 决胜（小者胜）」。
[教学简化 5] filesystem 真实实现扫 project/user/custom 多层目录 + Chokidar watch
（SkillWatchManager:284、ctx.on('fs/observed'):139）；教学版只扫单一 root、不 watch，
靠注册表手动 invalidate 代替精确失效。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 复用本层契约与词汇类型（SkillProvider / SkillCandidate / SkillDefinition / rank 常量）。
_CH12 = os.path.abspath(os.path.dirname(__file__))
if _CH12 not in sys.path:
    sys.path.insert(0, _CH12)
from skill_registry import (  # noqa: E402
    BUNDLED_SKILL_RANK,
    PROJECT_SKILL_RANK,
    SkillCandidate,
    SkillDefinition,
    SkillProvider,
)


# ---------- 最简内置 provider：badge ----------


class BadgeSkillProvider(SkillProvider):
    """内置最简 provider（skill-badge/src/index.ts:36）：静态候选 + 内置正文。

    真实 badge 只内置一个技能 dsh-badge（CANDIDATE:25，source='bundled'，
    rank=BUNDLED_SKILL_RANK:27），list() 静态返回 [CANDIDATE]，get() 读资源文件。
    [教学决策 3] 教学版内置两个技能，其中 code-review 的 bundled 版与 filesystem 的
    项目版同名，用于演示同层 rank 决胜。
    """

    name = "dsh-badge"  # PROVIDER_NAME（skill-badge/src/index.ts:17）

    def __init__(self):
        # 内置正文直接写在代码里；真实 badge 从资源 URL 读（SKILL_BODY_URL:18）。
        self._bodies = {
            "skill-creator": (
                "创建新技能的步骤：\n"
                "1. 新建目录并写 SKILL.md（frontmatter 声明 name/description）。\n"
                "2. 正文写清触发条件与操作步骤。\n"
                "3. 放进技能目录，由 filesystem provider 扫描加载。"
            ),
            "code-review": (
                "通用代码评审（内置兜底版）：\n"
                "1. 检查语法与明显错误。"
            ),
        }

    def list(self):
        """静态返回内置候选（对应 list:()=>[CANDIDATE]，skill-badge/src/index.ts:38）。"""
        return [
            SkillCandidate(
                name="skill-creator",
                description="指导如何创建一个新的技能包",
                provider=self.name,
                source="bundled",
                rank=BUNDLED_SKILL_RANK,
            ),
            SkillCandidate(
                name="code-review",
                description="通用代码评审清单（内置兜底）",
                provider=self.name,
                source="bundled",
                rank=BUNDLED_SKILL_RANK,
            ),
        ]

    def get(self, candidate):
        """按候选读内置正文（对应 get:readFile，skill-badge/src/index.ts:39-49）。"""
        content = self._bodies.get(candidate.name)
        if content is None:
            return None
        return SkillDefinition(
            name=candidate.name,
            provider=self.name,
            content=content,
            source="bundled",
            description=candidate.description,
        )


# ---------- 目录扫描 provider：filesystem ----------


def parse_skill_file(path: Path):
    """parse 一个 SKILL.md：frontmatter（name/description）+ 正文。

    对应 parseSkillFile（skill-filesystem/src/index.ts:793）与
    parseFrontmatter（:909）。返回 (name, description, content)；
    无 frontmatter 或缺 name 则返回 None。
    [教学简化 6] 真实 parseFrontmatter 支持任意字段 + invocation policy 解析
    （parseInvocationPolicy:992）；教学版只取 name/description 两个字段。
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    # 从首个 --- 之后找闭合分隔线，切出 frontmatter 与正文两段。
    end = text.find("\n---", 3)
    if end == -1:
        return None
    frontmatter = text[3:end].strip()
    content = text[end + 4:].strip()
    # 逐行按冒号切键值，得到 frontmatter 字段表。
    fields = {}
    for line in frontmatter.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    name = fields.get("name")
    if not name:
        return None
    return name, fields.get("description", ""), content


class FilesystemSkillProvider(SkillProvider):
    """目录扫描 provider（skill-filesystem/src/index.ts:146）：扫 root 下 *.md 并 parse。

    真实 filesystem 经 discoverRoot:719 发现 project/user/custom 多层目录，
    list:182 汇总候选、get:206 按 locator 读文件 parse 出正文。
    [教学简化 5] 教学版只扫单一 root、不 watch；正文定位用「name.md」约定代替 locator。
    """

    name = "filesystem"  # Config.providerName 默认 'filesystem'（skill-filesystem/src/index.ts:76）

    def __init__(self, root):
        self.root = Path(root)
        # 教学用计数器：统计 list() 真实扫盘次数，用于演示注册表缓存命中。
        self.scan_count = 0

    def list(self):
        """扫描 root 下全部 *.md，parse 出候选（对应 list:182 + roots:241）。"""
        self.scan_count += 1  # 每次被调用都真实扫盘；缓存命中时注册表不会调到这里
        candidates = []
        for path in sorted(self.root.glob("*.md")):
            parsed = parse_skill_file(path)
            if parsed is None:
                continue
            name, description, _content = parsed
            candidates.append(
                SkillCandidate(
                    name=name,
                    description=description,
                    provider=self.name,
                    source="project",
                    rank=PROJECT_SKILL_RANK,
                )
            )
        return candidates

    def get(self, candidate):
        """按「name.md」约定读文件并 parse 出正文（对应 get:206 + parseSkillFile:793）。"""
        path = self.root / f"{candidate.name}.md"
        if not path.exists():
            return None
        parsed = parse_skill_file(path)
        if parsed is None:
            return None
        name, description, content = parsed
        return SkillDefinition(
            name=name,
            provider=self.name,
            content=content,
            source="project",
            description=description,
        )
