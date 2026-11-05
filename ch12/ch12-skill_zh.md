# 第 12 章 skill 加载：能力是注册进来的，按需加载的

> 技能挂在墙上，不焊进身体；用时取下，用完归位。

## 本章回答的问题

- 第 2 章的 `SystemPromptService` 组装出发给模型的系统提示词，但技能正文为什么不能直接焊进系统提示词？
- 第 5 章的 shell seam 在组合期二选一，但 skill 的多个来源要同时在场，seam 怎么容纳「多实现同时在场」？
- 第 4 章的 tools 管线让模型按名调用工具，但模型怎么才能按需加载一个技能的正文？

第 5 章讲了 seam 的三角色骨架，并给出第一种 seam 形态：shell 那种「方法调用式 / 组合期二选一」——`createShell` 在组合期从 `node` 与 `local` 里选定唯一实现，之后消费方只调方法。本章看第二种形态：`packages/skill` 的 skill 加载。技能来源不是一个而是多个（磁盘目录、随包 Office 工作流、实验性的 badge provider、第三方包……），它们**同时注册**进一张分层注册表，读取时按 scope 分层、同层按 rank 合并去重。这是「provider 注册表」型 seam：实现可并存、可叠加、可遮蔽。

本章教学代码在 `ch12/code/`，运行方式 `python3 ch12/code/main.py`（依赖 ch01/ch02/ch04/ch09 教学代码，无第三方库）。

第 5 章确立的 seam 三角色（Service Definition / Service Provider / Consumer）本章全部复用，但 seam 的**形态**换了：

| | 第 5 章 shell seam | 本章 skill seam |
|---|---|---|
| 实现数量 | 组合期二选一（node / local） | 多个 provider 并存注册 |
| 消费方式 | 直接方法调用（`shell.exec`） | 经注册表读取（`ctx.skills.list/get`） |
| 冲突问题 | 不存在（只有一个实现） | 同名谁赢：同层 rank、跨层遮蔽 |
| 生命周期 | 随 Context 装配一次 | 注册是 effect，返回 disposer 可撤销 |

复用清单（不重复实现，直接 import 或同构借用）：

- 第 1 章 `Service`（`ch01/code/cordis.py`）：构造即注册，`SkillRegistry(ctx)` 自动绑定 `ctx.skills`；
- 第 2 章 `Session`（`ch02/code/agent_loop.py`）：技能目录以一条 append-only 事件注入会话；
- 第 4 章 `ToolRuntime` / `ToolDefinition`（`ch04/code/tools.py`）：skill 工具是 tools 管线里的一个普通工具；
- 第 9 章 `ScopedLayers`（`ch09/code/scope.py`）：global 层 + 每作用域叠加层 + 父链 lineage 的分层容器，本章直接 import 复用。

新增代码全部在 `ch12/code/`：`skill_registry.py`（定义层）、`skill_providers.py`（实现层）、`tool_skill.py`（消费层）、`bad_example.py`（问题演示）、`main.py`（分段演示）。

## 1. 把技能焊进系统提示词

先看反面教材 `ch12/code/bad_example.py`：把三个技能的完整正文直接拼进系统提示词。

```python
# 三个「技能」的正文：真实项目里这类专门步骤会越积越多。
SKILLS = {
    "commit-message": (
        "步骤：1. 读 git diff 归纳主题；2. 选 type：feat/fix/docs/refactor；"
        "3. 输出 type(scope): 描述，祈使句、不超过 72 字符。"
    ),
    "code-review": (
        "评审要点：1. 错误处理是否完整；2. 边界条件是否覆盖；"
        "3. 命名是否表达意图；4. 是否有重复代码可抽取。"
    ),
    "release-notes": (
        "步骤：1. 汇总上次 tag 以来的 PR；2. 按 feat/fix/breaking 分类；"
        "3. 渲染成面向用户的 release notes。"
    ),
}


def build_system_prompt():
    """把全部技能正文焊进系统提示词：不管本次任务用不用得上，全量常驻。"""
    parts = ["你是一个编码助手。以下是你必须随时记住的全部专业技能："]
    for name, body in SKILLS.items():
        parts.append(f"[技能 {name}] {body}")
    return "\n".join(parts)


def main():
    system_prompt = build_system_prompt()
    task = "帮我把这次改动写一条提交信息。"

    print("=== 反例：技能全量焊进系统提示词 ===")
    print(f"系统提示词长度：{len(system_prompt)} 字符（含全部 {len(SKILLS)} 个技能正文）")
    print(f"本次任务：{task}")
    print("→ 任务只需要 commit-message，但 code-review / release-notes 的正文也全程常驻。")
    print()
    print("--- 实际发给模型的系统提示词 ---")
    print(system_prompt)
    print()
    print("坑：")
    print("1. 全量常驻：用不上的技能正文也占上下文，技能越多、会话越长，滚得越大。")
    print("2. 不可发现：模型不知道有哪些能力、何时该用，只能靠提示词硬灌。")
    print("3. 不可插拔：加 / 删一个技能要改系统提示词（甚至改代码）重新装配。")
```

运行 `python3 ch12/code/bad_example.py`：

```text
=== 反例：技能全量焊进系统提示词 ===
系统提示词长度：306 字符（含全部 3 个技能正文）
本次任务：帮我把这次改动写一条提交信息。
→ 任务只需要 commit-message，但 code-review / release-notes 的正文也全程常驻。

--- 实际发给模型的系统提示词 ---
你是一个编码助手。以下是你必须随时记住的全部专业技能：
[技能 commit-message] 步骤：1. 读 git diff 归纳主题；2. 选 type：feat/fix/docs/refactor；3. 输出 type(scope): 描述，祈使句、不超过 72 字符。
[技能 code-review] 评审要点：1. 错误处理是否完整；2. 边界条件是否覆盖；3. 命名是否表达意图；4. 是否有重复代码可抽取。
[技能 release-notes] 步骤：1. 汇总上次 tag 以来的 PR；2. 按 feat/fix/breaking 分类；3. 渲染成面向用户的 release notes。

坑：
1. 全量常驻：用不上的技能正文也占上下文，技能越多、会话越长，滚得越大。
2. 不可发现：模型不知道有哪些能力、何时该用，只能靠提示词硬灌。
3. 不可插拔：加 / 删一个技能要改系统提示词（甚至改代码）重新装配。
```

三个技能就占了 306 字符，而且每一轮对话都要重复付费。归纳出三个坑：

1. **全量常驻**：所有技能正文随系统提示词常驻上下文。技能越多、正文越长，token 开销越大，而单次任务往往只用其中一个；
2. **不可发现**：系统提示词里虽然硬编码了三个技能名，但那是**写死的**——没有任何机制能在运行时列出「现在有哪些技能」，模型也无权按名索取正文。这里的「不可发现」指的是发现能力，不是字面上看不见名字：新增、删除技能都要改这段拼装代码；
3. **不可插拔**：技能来源被焊死——想加一个「从磁盘目录读技能」的来源，或让某个子代理带一套自己的技能，都无处下手。

三个坑指向同一组缺失的机制：来源要能**注册**（治坑 3）、目录要能**发现**（治坑 2）、正文要能**按需加载**（治坑 1）。这正是 skill seam 的三件事。

## 2. Service Definition：技能来源是注册进来的

### 2.1 三角色与契约

先看全景（对应 `ch12/code/` 的 `skill_registry.py` / `skill_providers.py` / `tool_skill.py` 三个文件）：

```mermaid
classDiagram
    direction LR
    class SkillProvider {
        <<契约>>
        +name
        +list() 候选
        +get(candidate) 完整定义
    }
    class BadgeSkillProvider {
        +name = "dsh-badge"
        静态内置候选
    }
    class FilesystemSkillProvider {
        +name = "filesystem"
        扫盘出候选
    }
    class SkillRegistry {
        <<Service Definition>>
        +layers ScopedLayers
        +register_provider()
        +list() 目录
        +get() 正文
        +invalidate()
    }
    class SkillTool {
        <<Consumer>>
        +apply() 注册 skill 工具
    }
    SkillProvider <|.. BadgeSkillProvider
    SkillProvider <|.. FilesystemSkillProvider
    SkillRegistry --> SkillProvider : register_provider
    SkillTool ..> SkillRegistry : list / get
```

- **Service Definition**：`SkillRegistry`，绑定 `ctx.skills`。它不生产技能，只负责收注册、合并目录、按需取正文；
- **Service Provider**：实现 `SkillProvider` 契约的来源。教学版给两个：`BadgeSkillProvider`（静态内置，对应实验包 `packages/experimental/skill-badge`）与 `FilesystemSkillProvider`（磁盘扫描，对应正式包族 `packages/skill/skill-filesystem`）；
- **Consumer**：`SkillTool`——把 `ctx.skills` 变成模型可调用的 `skill` 工具（走第 4 章 tools 管线），外加把目录注入会话的 `inject_catalog`。

契约本身是一个 `name` 属性加两个方法 `list()` / `get()`（`ch12/code/skill_registry.py`，对应 `packages/skill/skill/src/index.ts:247`）：

```python
class SkillProvider(ABC):
    """skill provider 契约（index.ts:247）：list() 出候选、get() 按候选加载全文。

    这是「provider 注册表」型 seam 的实现侧接口：多个实现并存注册，注册表读取时
    合并去重；而不是第 5 章 shell 那种「组合期选定唯一实现」的方法调用式 seam。
    [教学简化 1] 真实契约没有 watch 方法：注册表在 registerProvider 时把
    control.invalidate / control.signal 交给 provider（index.ts:270-276），
    provider 自行择机触发失效；教学版省略 control，靠手动 invalidate。
    """

    name: str = ""

    @abstractmethod
    def list(self) -> list[SkillCandidate]:
        """列出本 provider 的候选（元数据，无正文）（index.ts:251）。"""
        raise NotImplementedError

    @abstractmethod
    def get(self, candidate: SkillCandidate) -> SkillDefinition | None:
        """按候选加载完整定义（含正文）（index.ts:257）。"""
        raise NotImplementedError
```

契约本身没有第三样东西，但注册时会多给 provider 一个 `control`（`SkillProviderControl`：`signal` + `invalidate`，见 §2.2 开头）——那是注册表交来的工具，不属于契约成员。

注意两个方法的返回类型不同：`list()` 只出**元数据**，`get()` 才出**正文**。这不是随意的——它把「目录便宜、正文昂贵」写进了契约。围绕这条分界线，定义层给了三级词汇类型（`packages/skill/skill/src/index.ts:57` / `:77` / `:87`）：

```python
@dataclass(frozen=True)
class SkillSummary:
    """目录条目：渲染用元数据，无正文（index.ts:57）。list() 返回它。"""

    name: str
    description: str
    provider: str
    source: str


@dataclass(frozen=True)
class SkillCandidate(SkillSummary):
    """候选：Summary + 同层优先级 rank（index.ts:77）。provider.list() 返回它。

    [教学简化 1] 真实候选还携带不透明 locator（index.ts:77-82，供 get 定位正文）；
    教学版由 provider 自持定位。
    """

    rank: int = BUNDLED_SKILL_RANK


@dataclass(frozen=True)
class SkillDefinition:
    """完整定义：带正文，按需加载（index.ts:87）。provider.get() 返回它。"""

    name: str
    provider: str
    content: str
    source: str = "bundled"
    description: str = ""
    model_invocable: bool = True  # [教学简化 2] 真实为 invocation.modelInvocable（index.ts:49-54）
    user_invocable: bool = True   # [教学简化 2] 真实为 invocation.userInvocable（index.ts:49-54）
```

三级词汇各管一段：`SkillSummary` 是目录条目（给模型看的菜单），`SkillCandidate` 是候选（Summary 加同层优先级 rank，provider 交给注册表的内部形态），`SkillDefinition` 是完整定义（正文在场，只在 `get` 时出现）。词汇类型放在定义层文件里，让 provider 与消费者共享同一套词表——这与第 4 章 `ToolDefinition`/`ToolResult` 放在 `tools.py` 同出一辙。

### 2.2 register_provider：注册是 effect

注册表怎么收 provider？（`packages/skill/skill/src/index.ts:390`）。真实 `registerProvider` 接收的是**工厂函数**而不是现成的 provider 对象：注册表生成一个 `control`（`SkillProviderControl`——交给该次注册的两件工具：`signal` 在注册失败或撤销时 abort，`invalidate` 让 provider 主动宣告「我的目录变了」），再把 `control` 传给工厂、拿回 provider（`:394-404`）。教学版为了少一层，直接收 provider 对象、把这两件工具省略（见下方 `[教学简化 1]`）。

```python
# ch12/code/skill_registry.py — SkillRegistry.register_provider（节选）
    def register_provider(self, provider: SkillProvider, scope=None):
        """把 provider 注册进 scope 对应层（index.ts:390）。注册是 effect：返回 disposer。

        scope 为 None 落 global 层（宿主级）；否则落该作用域叠加层。
        [教学简化] 真实 registerProvider 收工厂 (control) => provider，只拒绝保留名
        runtime（index.ts:406-408）；同层重名由 layer.providers.insert 抛出
        （index.ts:414 → packages/core/scope/src/store.ts:45）。
        """
        layer = self.layers.for_scope(scope)
        if provider.name in layer.providers:
            raise ValueError(f"skill provider already registered: {provider.name}")
        # 写入 provider 并立即失效缓存：注册改变了后续读取的结果。
        layer.providers[provider.name] = provider
        self.invalidate()

        def dispose():
            # 撤销注册并再次失效，对应真实 disposer（undo insert + invalidateCache）。
            layer.providers.pop(provider.name, None)
            self.invalidate()

        return dispose
```

四个细节，每个都对着一个真实问题：

1. **按 scope 落层**：`scope=None` 落 global 层（宿主级，所有会话可见），否则落该作用域的叠加层。分层容器 `self.layers` 直接复用第 9 章 `ScopedLayers`（`ch09/code/scope.py`）——global 层 + 每作用域一层 + 父链 lineage，即真实类文档所言「the host+per-scope shape the tools registry established」（`packages/skill/skill/src/index.ts:344-355`）。这段文档还补了后半句：宿主行与仓库插件落 global 层，由 agent preset 的 standing composition 挂载的插件落该 preset 自己的层——「按会话装一整套能力」在第 15 章展开，本章只需记住注册落哪一层由**调用方 context 的 scope** 决定；
2. **同层重名处理**：真实源码分三种情况。第一，保留名 `runtime`（`RUNTIME_PROVIDER`，`:24`）直接 throw（`:406-408`）。第二，其余 provider 同名由 provider 表自己的 insert 抛出（`:414` → `packages/core/scope/src/store.ts:45`），错误文案来自 `SkillLayer` 构造时给的 duplicate Error（`packages/skill/skill/src/index.ts:334-336`）。第三，走 `register`（`:439`）注册的 runtime 技能撞名时反而不抛——**warn+noop**，告警后返回一个空 disposer（`:443-446`）。[教学简化] 教学版 `register_provider` 把三合一简化成同层同名即抛错，把「避免一个来源悄悄顶掉另一个」这条规则做显式；
3. **注册即失效缓存**：写入后立即 `invalidate()`，因为注册改变了后续所有读取的结果——不失效，新 provider 就会藏在旧缓存里读不到。真实源码里，`ScopedLayers.effect` 在注册完成后回调一次变更通知（`packages/core/scope/src/store.ts:262`），`SkillRegistry` 把它接到 `invalidateCache`（`packages/skill/skill/src/index.ts:362-365`）；另外 provider 自己也能经 `control.invalidate` 主动触发（`:396-401`）。[教学简化] 教学版没有这层间接，把这些入口合并成一个显式调用；
4. **返回 disposer**：注册是一个 effect（第 1 章术语）——调用返回撤销函数，`dispose()` 移除 provider 并再次失效缓存。第三方包带来的技能随装配生效、随拆卸消失，这就是坑 3「不可插拔」的解药。

`SkillRegistry` 本身是第 1 章 `Service` 的子类：`super().__init__(ctx, "skills")` 构造即注册到 `ctx.skills`（`ch01/code/cordis.py:248-252`），对应真实源码的 `declare('skills')` 上下文扩展（`packages/skill/skill/src/index.ts:283-286`）。与第 4 章 `ToolRuntime(ctx)`、第 9 章 `ScopedToolRuntime(ctx)` 同一路数。

### 2.3 两个 provider 示例

实现层 `ch12/code/skill_providers.py` 给出两个形态截然不同的 provider。最简单的是 badge——静态候选表 + 内置正文。它住在 `packages/experimental/skill-badge`：这是一个**实验 provider**，默认组合不声明它，要用得显式装包并插一行插件配置（对应 `packages/experimental/skill-badge/src/index.ts:36`，全文件仅 60 行；实验性也意味着它可能随版本移动或移除，本章只把它当「最简 provider」的样板，不当稳定 API）：

```python
class BadgeSkillProvider(SkillProvider):
    """内置最简 provider（packages/experimental/skill-badge/src/index.ts:36）：
    静态候选 + 内置正文。

    真实 badge 只内置一个技能 dsh-badge（CANDIDATE:25，source='bundled'，
    rank=BUNDLED_SKILL_RANK:32），list() 静态返回 Promise.resolve([CANDIDATE])，
    get() 读资源文件。它是实验 provider：默认组合不声明，部署方要显式插行。
    [教学决策 3] 教学版内置两个技能，其中 code-review 的 bundled 版与 filesystem 的
    项目版同名，用于演示同层 rank 决胜。
    """

    name = "dsh-badge"  # PROVIDER_NAME（packages/experimental/skill-badge/src/index.ts:17）

    def __init__(self):
        # 内置正文直接写在代码里（skill-creator / code-review 两段）；全文见代码文件
        self._bodies = {...}

    def list(self):
        """静态返回内置候选（对应 list:()=>[CANDIDATE]，packages/experimental/skill-badge/src/index.ts:38）。"""
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
        """按候选读内置正文（对应 get:readFile，packages/experimental/skill-badge/src/index.ts:39-49）。"""
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
```

badge 住在 `packages/experimental/`，filesystem 住在 `packages/skill/`——位置说明了两者的定位。实验包的 README 把 badge 的可观察行为写成「提供方不可变，发现始终成功且恰好返回一个 skill，绝不会报告部分结果」：它不扫盘、不监听、不会只成功一半，所以天然适合当「最简样板」。代价是它也在实验区——升级可能挪位置或整体移除，依赖它的部署要显式插行（见上文）。正式包族的 filesystem 才承担多根发现、增量监听与失败容忍。

磁盘 provider 多一步解析：技能文件是「frontmatter 元数据 + Markdown 正文」的 `.md` 文件（对应 `packages/skill/skill-filesystem/src/index.ts:811` 的 `parseSkillFile`）：

```python
def parse_skill_file(path: Path):
    """parse 一个 SKILL.md：frontmatter（name/description）+ 正文。

    对应 parseSkillFile（packages/skill/skill-filesystem/src/index.ts:811）与
    parseFrontmatter（:931）。返回 (name, description, content)；
    无 frontmatter 或缺 name 则返回 None。
    [教学简化 6] 真实 parseFrontmatter 支持任意字段 + invocation policy 解析
    （parseInvocationPolicy:1014）；教学版只取 name/description 两个字段。
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
```

`FilesystemSkillProvider` 把「扫盘」与「读正文」拆进契约的两个方法（对应 `packages/skill/skill-filesystem/src/index.ts:149`）：

```python
class FilesystemSkillProvider(SkillProvider):
    """目录扫描 provider（packages/skill/skill-filesystem/src/index.ts:149）：扫 root 下
    *.md 并 parse。

    真实 filesystem 经 roots:251 枚举六类根（project-dsh / project-agents / custom /
    user-dsh / user-agents / bundled）、discoverRoot:737 发现，list:185 汇总候选、
    get:216 用候选携带的 locator 读文件 parse 出正文。
    [教学简化 5] 教学版只扫单一 root、不 watch；正文定位用「name.md」约定代替 locator。
    """

    name = "filesystem"  # Config.providerName 默认 'filesystem'（packages/skill/skill-filesystem/src/index.ts:76）

    def __init__(self, root):
        self.root = Path(root)
        # 教学用计数器：统计 list() 真实扫盘次数，用于演示注册表缓存命中。
        self.scan_count = 0

    def list(self):
        """扫描 root 下全部 *.md，parse 出候选（对应 list:185 + roots:251）。"""
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
        """按「name.md」约定读文件并 parse 出正文（对应 get:216 + parseSkillFile:811）。"""
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
```

注意 `list()` 只解析 frontmatter 出候选，正文要等 `get()` 被调用才整体读入——契约里「目录便宜、正文昂贵」的分界线，在实现里就是两次粒度不同的文件读取。

**回溯**：§1 的坑 3「不可插拔」到此解决——技能来源不再是焊死的字典，而是实现 `SkillProvider` 契约、经 `register_provider` 注册进 `ctx.skills` 的可插拔组件，注册是 effect、可撤销。但注册表一旦向多个来源敞开，新问题立刻出现：badge 有一个 `code-review`，filesystem 也有一个 `code-review`，目录里该出现哪一个？

## 3. 目录合并：同层 rank 决胜，近层遮蔽

### 3.1 合并主流程

`list()` 是注册表的读取主入口（`packages/skill/skill/src/index.ts:470`）：真实实现里它只是 `snapshot()`（`:481`）的一层薄壳，取胜者在快照里被投影成 `SkillSummary`、按名排序、结果进缓存；缓存键把 `cwd`、scope 链和 revision 一起编进去（`:519-549`）。[教学简化] 教学版的依赖方向反过来——`snapshot()` 调 `list()`，`list()` 是唯一实现（见 `ch12/code/skill_registry.py` 的 `snapshot`），并省略了 `cwd` 与 revision 两个缓存键维度。真正的合并逻辑在三层内部方法里——跨层、层内、provider 汇齐（`:551-565` / `:567-582` / `:584`）：

```python
# ch12/code/skill_registry.py — SkillRegistry 的分层合并内部方法（节选）
    def _layer_chain(self, scope):
        """生效层链：global 打底 + scope 父链（祖先在前、最近层在后）（index.ts:556）。"""
        layers = [self.layers.global_layer]
        if scope is not None:
            layers.extend(self.layers.chain_layers(scope))
        return layers

    def _collect(self, scope) -> dict:
        """跨层合并：层链由远及近遍历，近层同名覆盖远层（shadowing）（index.ts:551-565）。"""
        merged: dict = {}
        for layer in self._layer_chain(scope):
            for name, entry in self._collect_layer(layer).items():
                merged[name] = entry  # 后写的近层覆盖先写的远层
        return merged

    def _collect_layer(self, layer: SkillLayer) -> dict:
        """单层：汇齐各 provider 候选，按 rank 排序（小者胜），同名去重（index.ts:567-582）。"""
        indexed = []
        for provider in layer.providers.values():
            for candidate in provider.list():
                indexed.append((candidate, provider))
        # 按 rank 升序稳定排序：rank 小者排前，同 rank 保留注册序（真实还比 providerOrder / localOrder）。
        indexed.sort(key=lambda pair: pair[0].rank)
        winning: dict = {}
        for candidate, provider in indexed:
            if candidate.name not in winning:  # 同层同名：首个（rank 最小）胜，其余丢弃
                winning[candidate.name] = {"candidate": candidate, "provider": provider}
        return winning
```

两条规则各治一种冲突，方向正好相反：

- **同层同名 → rank 决胜（小者胜）**：`_collect_layer` 把一层里所有 provider 的候选汇齐，按 rank 升序稳定排序，首个取胜、其余丢弃。rank 阶梯取自真实源码（[教学简化 4] 只取两端）：`PROJECT_SKILL_RANK = 100`（项目技能，高优先）、`BUNDLED_SKILL_RANK = 600`（内置技能，低优先）。**数值越小越优先**；真实阶梯按优先级从高到低共 7 级：

  | 来源 | rank | 由谁给 |
  |---|---|---|
  | project-dsh | 100 | `packages/skill/skill-filesystem/src/index.ts:35` |
  | project-agents | 200 | 同上 `:36` |
  | runtime | 250 | `packages/skill/skill/src/index.ts:25` |
  | custom | 300 | `packages/skill/skill-filesystem/src/index.ts:37` |
  | user-dsh | 400 | 同上 `:38` |
  | user-agents | 500 | 同上 `:39` |
  | bundled | 600 | `packages/skill/skill/src/index.ts:28` |

  rank 是**每个候选自带的数**，不是注册表按 provider 名硬排序——provider 自己决定给候选排哪一级，注册表只负责比大小。被判负的重复项不会静默消失：真实 `collectLayer` 对每一项被丢弃的同名候选打一条 warn（`packages/skill/skill/src/index.ts:575`）；[教学简化] 教学版只保留「rank 小者胜」这一条，丢弃时不打日志；
- **跨层同名 → 近层整体遮蔽远层**：`_collect` 沿层链由远及近遍历（global 打底、scope 叠加层在后），近层写同名 key 直接覆盖远层——不是字段合并，是整个取胜者替换。这与第 9 章 tools 的 scope 遮蔽同规则，因为分层容器就是同一个 `ScopedLayers`。

### 3.2 两种冲突的现场

`main.py` 段 2 与段 3 各演示一种。段 2：badge 与 filesystem 都注册在 global 层，两者的 `code-review` 在**同层**相遇——filesystem 的 rank=100 胜过 badge 的 rank=600，目录里 `code-review` 的 provider 是 filesystem。段 3：再注册一个 `ReviewBotSkillProvider` 到 `scope="review-bot"`，它也提供 `code-review`——这次是**跨层**相遇，review-bot 层的候选整体遮蔽 global 层的取胜者，但从 review-bot 读目录仍能看到 global 层其余技能（`skill-creator`、`commit-message`）的继承。

**回溯**：「多来源并存谁赢」有了确定答案——同层比 rank（小者胜），跨层比远近（近层遮蔽）。但注意 `list()` 返回的始终只是 `SkillSummary`：名字、描述、来源，没有正文。正文在哪里？

## 4. 按需加载：正文只在需要时读

### 4.1 get：委托取胜者的 provider 加载正文

`get(name)` 是正文的唯一入口（`packages/skill/skill/src/index.ts:500`）：先按 scope 合并出取胜候选，再**委托该候选自己的 provider** 加载正文，并把候选携带的 locator 原样交回去（`:506-509`）：

```python
# ch12/code/skill_registry.py — SkillRegistry.get(name, scope)（节选）
    def get(self, name: str, scope=None) -> SkillDefinition | None:
        """按名加载取胜者的完整定义（含正文）（index.ts:500），带缓存。"""
        key = (scope, name)
        if key in self._get_cache:
            return self._get_cache[key]
        entry = self._collect(scope).get(name)
        # 命中取胜候选后，委托它的 provider 加载正文；未命中则为 None。
        # [教学简化 1] 真实调用是 provider.get(candidate, options)：候选自带 locator，
        # 注册表原样交回（index.ts:506-509）；教学版候选无 locator，由 provider 自持定位。
        definition = entry["provider"].get(entry["candidate"]) if entry else None
        self._get_cache[key] = definition
        return definition
```

注册表自己从不读正文：badge 的取胜者回内置表取，filesystem 的取胜者回磁盘读文件。正文「从哪个来源来」由取胜候选的 provider 字段决定——合并规则（§3）与加载动作（§4）由此解耦。真实实现还把候选携带的 **locator** 原样交回 provider（`packages/skill/skill/src/index.ts:506-509` 的 `match.provider.get(match.candidate, options)`）：locator 是 provider 自己的不透明句柄（filesystem 里是 `{ path, directory }`，见 `packages/skill/skill-filesystem/src/index.ts:118-121`），注册表碰都不碰。教学版候选没有 locator 字段，provider 用「name.md」约定自己拼路径。

真实实现还在加载后校验定义（`validateDefinition`，`:511`）：名字改了或不再合法，就失效对应条目并当作加载失败（`:512-515`），避免一个过期候选把错位的正文塞给模型；[教学简化] 教学版没有这一步。

### 4.2 缓存与失效

目录与正文都走缓存（`_list_cache` / `_get_cache` / `_snapshot_cache`），失效集中在一个方法（`packages/skill/skill/src/index.ts:621` 清缓存 + `:648` 广播）：

```python
# ch12/code/skill_registry.py — SkillRegistry.invalidate（节选）
    def invalidate(self):
        """清空全部读缓存并广播 skills/change（index.ts:621 + :648）。"""
        self._revision += 1
        self._list_cache.clear()
        self._get_cache.clear()
        self._snapshot_cache.clear()
        self.ctx.emit("skills/change", {})
```

三个调用点会触发它：`register_provider` 注册时、disposer 撤销时、外部手动调用时。失效做两件事：清空全部读缓存（下次 `list`/`get` 重新收集），并广播 `skills/change` 事件——真实源码里该事件由 `notifyChange` 派发（`packages/skill/skill/src/index.ts:648`，`dispatch('emit', ['skills/change'])`），且逐个包住监听器异常，一次监听器失败不会否决注册本身（`:649-660`），供关心目录变化的组件自行订阅消费。`_revision` 是失效计数器，对应真实的 revision 机制：真实缓存键里带 revision，`collect` 发现读取期间 revision 变过就重试一次，仍不稳定则返回未缓存结果（`:519-549`）；教学版简化成「清空整表」。

真实世界里的失效大多是 provider 自己触发的：filesystem provider 在 `apply` 里把「怎么失效」交给注册表（`packages/skill/skill-filesystem/src/index.ts:135-138`，注册表给的 `control.invalidate` 被 `SkillWatchManager` 接管，`:169`），Chokidar 观察到目录变化或 `ctx.on('fs/observed')`（`:142`）看到一次工具写盘，就触发它；[教学简化 1] 教学版没有这条回路，靠手动 `invalidate()` 代替。

`main.py` 段 7 用 filesystem 的 `scan_count` 把缓存行为变成可见数字：第一次 `list()` 真实扫盘（+1），第二次 `list()` 命中缓存（扫盘数不变）；新增一个技能文件后直接 `list()` 仍命中旧缓存——直到 `invalidate()` 后才重新扫盘、新技能出现。

**回溯**：§1 的坑 1「全量常驻」到此解决——正文只在 `get` 被调用时由取胜者的 provider 加载，`list` 永远只出元数据；缓存让重复读取不再扫盘，失效让注册变化即时可见。但到目前为止，所有调用都是我们这段演示代码发出的——模型怎么知道有哪些技能可用？

## 5. 模型消费：skill 工具 + 目录注入

消费层 `ch12/code/tool_skill.py` 做两件事，对应真实 tool-skill 的两条消费路径：把技能目录注入会话（模型「知道有什么」），把 `skill` 工具注册进第 4 章 tools 管线（模型「按需取正文」）。

### 5.1 两个渲染函数

先准备两种输出形态（对应 `packages/skill/skill/src/index.ts:170` 的 `renderSkillContent` 与 `packages/skill/tool-skill/src/index.ts:258` 的 `renderCatalogMessage`）：

```python
def render_skill_content(definition):
    """把技能正文渲染成 <skill_content> 规范块（packages/skill/skill/src/index.ts:170
    renderSkillContent）。

    模型读到这个块即「技能已加载」，正文里的步骤成为它接下来的操作指引。
    [教学简化] 真实块的结构是 <skill_content name="..."> 包 <skill_resources>（相对资源
    基址提示）再加 <skill_instructions> 正文（index.ts:172-182）；开标签上没有 provider
    属性，provider 只出现在 skill_resources 的兜底提示文字里（index.ts:188-190）。
    教学版在 <skill_content> 上多渲染了 provider="..."，只为让输出直接看出取胜者；
    正文也没有 <skill_instructions> 外壳。
    """
    return (
        f'<skill_content name="{definition.name}" provider="{definition.provider}">\n'
        f"{definition.content}\n"
        f"</skill_content>"
    )


def render_catalog_message(summaries):
    """把技能目录渲染成注入会话的文本（packages/skill/tool-skill/src/index.ts:258
    renderCatalogMessage）。

    目录只含 name + description（元数据），不含正文；正文留到模型用 skill 工具按需加载。
    """
    if not summaries:
        return ""
    lines = ["可用技能（用 skill 工具按名加载正文）："]
    for summary in summaries:
        lines.append(f"- {summary.name}: {summary.description}")
    return "\n".join(lines)
```

### 5.2 SkillTool：skill 工具走 tools 管线

`SkillTool.apply` 把一个名为 `skill` 的工具注册进 `ctx.tools`（[教学决策 4] 与第 5 章 tool-bash 同型：工具 = seam 的模型侧消费面），执行体内部回读 `ctx.skills`（对应 `packages/skill/tool-skill/src/index.ts:80` 的 `apply` / `:84` 的 `defineTool` / `:130` 的 `execute` / `:165` 的 `ctx.tools.register`）：

```python
class SkillTool:
    """skill 工具的装配与执行（packages/skill/tool-skill/src/index.ts:80 apply）。

    消费 ctx.skills：把注册表里的技能变成模型可调用的 skill 工具。
    """

    def __init__(self, ctx):
        self.ctx = ctx

    def apply(self):
        """注册 skill 工具进 ctx.tools（index.ts:84 defineTool + :165 register）。"""
        definition = ToolDefinition(
            name="skill",
            description="按名加载一个技能的完整指引正文",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要加载的技能名"},
                },
                "required": ["name"],
            },
            execute=self._execute,
        )
        return self.ctx.tools.register(definition)

    def _execute(self, arguments):
        """按名加载技能（index.ts:130 execute）：先 list 校验存在，再 get 加载正文。

        出错分支（未知技能 / 正文加载失败 / 不可模型调用）返回错误文本：
        真实 tool-skill 在这些分支直接 throw；第 4 章教学管线的 _dispatch_body
        也有 try/except 兜底，会把任何异常渲染成 "tool failed: ..."（is_error=True，
        ch04/code/tools.py:423-430）。教学版选择自己返回 "error: ..." 文案，
        让模型看到 skill 自己的错误措辞（[教学简化 8]）。
        """
        name = arguments.get("name")
        # 在目录里找该技能，对应 ctx.skills.list(lookup).find(...)（index.ts:138）。
        summary = next((s for s in self.ctx.skills.list() if s.name == name), None)
        if summary is None:
            return f'error: skill "{name}" is unknown or no longer available'
        # 加载完整定义，对应 ctx.skills.get(name, lookup)（index.ts:145）。
        definition = self.ctx.skills.get(name)
        if definition is None:
            return f'error: skill "{name}" is unknown or no longer available'
        # 校验模型可调用，对应 isModelInvocable(summary)（index.ts:142）。
        if not definition.model_invocable:
            return f'error: skill "{name}" is not available for model invocation'
        # 渲染成 <skill_content> 规范块返回给模型。
        return render_skill_content(definition)
```

执行体走的是真实 execute 的主路径（list → get → 可调用校验 → 渲染）：先 `isSkillName` 校验名字（`:131`），再 `list` 校验技能存在并查 summary（`:138`），然后 `get` 加载正文（`:145`），两次都做模型可调用校验（`isModelInvocable(summary)` 在 `:142`、`isModelInvocable(skill)` 在 `:149`），最后渲染成 `<skill_content>` 规范块。教学版省了入口校验、只查一次。模型发起一次工具调用，换回一份完整指引——正文从「常驻提示词」变成「调用时才进上下文」。

这里还有一层作用域细节：真实 execute 的 `lookup` 带着 `scope: exec.agent`（`:135-137`），所以技能目录按**发起调用的那个 agent** 的层链读，而不是按进程全局读。[教学简化] 教学版去掉了这条，`list()` / `get()` 一律读 global 加显式传入的 scope。

### 5.3 inject_catalog：目录进会话

光有工具还不够——模型得先知道菜单上有什么。`inject_catalog` 把目录渲染成一条 user 消息追加进会话（对应 pre-step #2 的目录发布监听器，`packages/skill/tool-skill/src/index.ts:217`）。pre-step 指模型每次请求前、会话拼装阶段的一个钩子；#1 / #2 是同一插件挂的两个监听器，**#1** 处理用户手打的 `/技能名`，只认 `userInvocable` 的技能，**#2** 发布目录，只列 `modelInvocable` 的技能——两者正好对应 §2.1 的两个调用开关：

```python
# ch12/code/tool_skill.py — 模块级函数 inject_catalog
def inject_catalog(session, summaries):
    """把技能目录作为 user 消息注入会话（pre-step #2 目录发布，index.ts:217）。

    复用第 2 章 Session 的 append-only 事件日志（ch02/code/agent_loop.py:71 append）：
    目录以一条 user/message 事件进会话，消息源标记为 'skill-catalog'
    （SkillCatalogSource:35），与用户真实输入区分开。
    返回注入的消息 dict；目录为空则不注入并返回 None。
    """
    text = render_catalog_message(summaries)
    if not text:
        return None
    message = {"role": "user", "content": text, "source": "skill-catalog"}
    session.append("user/message", {"message": message})
    return message
```

复用第 2 章 `Session` 的 append-only 事件日志：目录以一条 `user/message` 事件进会话，`source` 标记为 `skill-catalog`，与用户真实输入区分开。模型在上下文里读到「可用技能（用 skill 工具按名加载正文）：……」，就同时拿到了菜单与点菜方式。

真实版本在这些演示之外还做了几件事，都会影响模型看到什么：

- **目录正文有固定框架**。`renderCatalogMessage`（`packages/skill/tool-skill/src/index.ts:258`）包一层 `<system-reminder>` + `<available_skills>`，每条渲染成 `` - `name`: description ``（`:339-341`），末尾再给一句「加载前不要照着摘要执行」的指令；目录为空或发现不完整时还追加不同措辞（`:263-283`）。
- **目录会替换，不会越积越多**。首次发布走 `renderCatalogMessage`，之后目录变了走 `renderCatalogUpdate`（`:289`），正文明确说「这份新目录替换本会话此前所有目录」，避免模型抱住过期清单；条目和摘要都没变时干脆不发消息（`:231-256`）。
- **目录可见性绑在工具上**。发布前先看这个 agent 能不能看见本插件注册的那个 `skill` 工具（`:225-227`），看不见就发布空目录——工具被作用域遮蔽掉，菜单也跟着消失，模型不会看到「有菜单但点不了」。

以上三条都是真实行为，教学版一条都没实现：它只渲染「可用技能」一行与条目列表，只做首次发布（[教学简化 7]），也没有「工具不可见就不发布」的联动。

**回溯**：§1 的坑 2「不可发现」到此解决——目录注入让模型看见有哪些技能，skill 工具让模型按名取正文。三个坑全部闭环：来源可注册（§2）、目录可发现（§5）、正文按需加载（§4），合并规则（§3）保证多来源并存时结果确定。

## 6. 完整运行输出

运行 `python3 ch12/code/main.py`（依赖 ch01/ch02/ch04/ch09 教学代码，无第三方库），输出分段编号与 `main.py` 的段一一对应：

```text
========================================================================
段 1 —— 装配：ctx.skills provider 注册表
========================================================================
ctx.skills 类型：SkillRegistry（Service，构造即注册）
已注册 provider：['dsh-badge', 'filesystem']
注册触发的 skills/change 事件数：2

========================================================================
段 2 —— 目录合并 + 同层 rank 决胜（小者胜）
========================================================================
- code-review     provider=filesystem source=project
- commit-message  provider=filesystem source=project
- skill-creator   provider=dsh-badge  source=bundled
code-review 取胜者：filesystem（项目 rank=100 < 内置 rank=600，小者胜）

========================================================================
段 3 —— 分层 shadowing：review-bot 作用域遮蔽 global
========================================================================
global 读 code-review：filesystem
review-bot 读 code-review：review-bot（本层更近，整体遮蔽 global）
review-bot 目录技能数：3（继承 global + 本层覆盖）

========================================================================
段 4 —— 按需加载全文：get('commit-message')
========================================================================
provider=filesystem source=project
# commit-message

1. 读 git diff，归纳本次变更的主题。
2. 选择 type：feat / fix / docs / refactor / test / chore。
3. 输出 `type(scope): 描述`，描述用祈使句、不超过 72 字符。

========================================================================
段 5 —— tool-skill 消费：skill 工具（走第 4 章 tools 管线）
========================================================================
ctx.tools 可见工具：['skill']
skill(commit-message) is_error=False：
<skill_content name="commit-message" provider="filesystem">
# commit-message

1. 读 git diff，归纳本次变更的主题。
2. 选择 type：feat / fix / docs / refactor / test / chore。
3. 输出 `type(scope): 描述`，描述用祈使句、不超过 72 字符。
</skill_content>
skill(no-such-skill) → 工具返回错误文本：error: skill "no-such-skill" is unknown or no longer available

========================================================================
段 6 —— 会话目录注入：skill-catalog 消息进会话
========================================================================
注入消息 source=skill-catalog，会话事件数=1
可用技能（用 skill 工具按名加载正文）：
- code-review: 项目代码评审清单
- commit-message: 按 Conventional Commits 生成提交信息
- skill-creator: 指导如何创建一个新的技能包

========================================================================
段 7 —— 缓存与失效
========================================================================
第一次 list()：3 个技能，扫盘 +1（真实扫盘）
第二次 list()：3 个技能，扫盘 +1（缓存命中，未新增）
新增 release-notes.md 后直接 list()：['code-review', 'commit-message', 'skill-creator']（仍命中旧缓存）
invalidate() 后 list()：['code-review', 'commit-message', 'release-notes', 'skill-creator']（重新扫盘）
全程 skills/change 事件数：5
```

### 6.1 逐段解读

- **段 1**：`ctx.skills` 是 `SkillRegistry`（Service，构造即注册）。注册两个 provider 触发 2 次 `skills/change`——教学版每次 `register_provider` 都显式调 `invalidate()`；真实版由 `ScopedLayers.effect` 的变更通知与 `control.invalidate` 两处入口触发同样的广播（见 §2.2 第 3 点）；
- **段 2**：目录合并后共 3 个技能。`code-review` 同名冲突按 rank 决胜：filesystem（项目 rank=100）胜过 badge（内置 rank=600），`skill-creator` 只有 badge 提供、直接入选；
- **段 3**：`review-bot` scope 注册自己的 `code-review` 后，从该 scope 读取时近层整体遮蔽 global 的取胜者；目录仍是 3 个——`skill-creator` 与 `commit-message` 从 global 继承；
- **段 4**：`get('commit-message')` 委托取胜者 filesystem 读盘，返回带正文的 `SkillDefinition`；
- **段 5**：skill 工具经第 4 章 `ctx.tools.execute` 执行——成功调用返回 `<skill_content>` 规范块；未知技能由工具体自己返回 `error: ...` 文案（真实版直接抛错，由框架渲染成错误结果；第 4 章教学管线也会兜底成 `tool failed: ...`，见 [教学简化 8]）。输出里 `<skill_content ... provider="filesystem">` 的 provider 属性是教学版额外加的，真实开标签只有 `name`（见 §5.1 的 [教学简化]）；
- **段 6**：目录以 `source=skill-catalog` 的 user/message 事件进会话，正文只有 name + description；
- **段 7**：缓存命中时 `scan_count` 不增；磁盘新增文件不会自动可见，`invalidate()` 后重新扫盘才出现新技能。第二次 `list()` 输出的「扫盘 +1」是相对基线的累计增量（累计 +1），不是本次新扫盘——本次实际命中缓存、新增扫盘 0 次。全程 5 次 `skills/change`：2 次注册（段 1）+ 1 次 review-bot 注册（段 3）+ 2 次手动失效（段 7）。

## 7. 源码对照

教学代码与真实源码的符号对应：

| 教学符号（ch12/code/） | 真实符号 | 源码位置 |
|---|---|---|
| `skill_registry.py` `SkillSummary` / `SkillCandidate` / `SkillDefinition` | `SkillSummary` / `SkillCandidate` / `SkillDefinition` | `packages/skill/skill/src/index.ts:57` / `:77` / `:87` |
| `SkillProvider`（契约） | `SkillProvider` | `packages/skill/skill/src/index.ts:247` |
| `SkillLayer` | `SkillLayer` | `packages/skill/skill/src/index.ts:327` |
| `SkillRegistry`（`Service` 子类，绑定 `ctx.skills`） | `SkillRegistry extends Service` + `declare('skills')` | `packages/skill/skill/src/index.ts:356` / `:283-286` |
| `register_provider`（收 provider 对象，返回 disposer） | `registerProvider`（收工厂 `(control) => provider`） | `packages/skill/skill/src/index.ts:390` |
| `list` / `snapshot` / `get` | `list`（委托 `snapshot`）/ `snapshot` / `get`；**教学版方向相反**：`snapshot` 调 `list` | `packages/skill/skill/src/index.ts:470` / `:481` / `:500` |
| `_layer_chain` + `_collect` | `collectFresh`（跨层合并）+ `collect`（缓存与重试包装） | `packages/skill/skill/src/index.ts:551-565` / `:519-549` |
| `_collect_layer` | `collectLayer`（排序 + 去重）+ `listLayerCandidates`（汇齐） | `packages/skill/skill/src/index.ts:567-582` / `:584` |
| `invalidate`（revision + skills/change） | `invalidateCache` + `notifyChange` | `packages/skill/skill/src/index.ts:621` / `:648` |
| `PROJECT_SKILL_RANK` / `BUNDLED_SKILL_RANK` | rank 阶梯常量 | `packages/skill/skill-filesystem/src/index.ts:35-39` + `packages/skill/skill/src/index.ts:25` / `:28` |
| `skill_providers.py` `BadgeSkillProvider` | `provider`（**实验包**，默认组合不声明） | `packages/experimental/skill-badge/src/index.ts:36`（全文件 60 行） |
| `FilesystemSkillProvider.list` / `.get` | `FileSystemSkillProvider.list` / `.get` | `packages/skill/skill-filesystem/src/index.ts:185` / `:216` |
| `parse_skill_file` | `parseSkillFile` + `parseFrontmatter`（+ `parseInvocationPolicy`） | `packages/skill/skill-filesystem/src/index.ts:811` / `:931` / `:1014` |
| `tool_skill.py` `SkillTool.apply` | `apply` | `packages/skill/tool-skill/src/index.ts:80` |
| `skill` 工具定义 | `skillTool`（`defineTool({name:'skill'})`） | `packages/skill/tool-skill/src/index.ts:84` |
| `_execute` | `execute` | `packages/skill/tool-skill/src/index.ts:130` |
| `ctx.tools.register` | `ctx.tools.register(skillTool)` | `packages/skill/tool-skill/src/index.ts:165` |
| `inject_catalog` | pre-step #2 会话目录发布 | `packages/skill/tool-skill/src/index.ts:217` |
| `render_catalog_message` | `renderCatalogMessage`（+ 替换渲染 `renderCatalogUpdate`） | `packages/skill/tool-skill/src/index.ts:258` / `:289` |
| `render_skill_content` | `renderSkillContent` | `packages/skill/skill/src/index.ts:170` |

教学差异清单（编号与代码注释一一对应）：

**教学决策**（主动选择，不为难而难）：

1. **[教学决策 1]** skill seam 定位为「provider 注册表」型 seam：多 provider 并存注册、读取时分层 + rank 合并；对照第 5 章 shell 的「方法调用式 / 组合期二选一」。分层遮蔽复用第 9 章 `ScopedLayers`（skill_registry.py 模块 docstring）；
2. **[教学决策 2]** badge 与 filesystem 两个 provider 同现，展示多实现并存（skill_providers.py 模块 docstring）；
3. **[教学决策 3]** badge 内置两个技能（`skill-creator` + bundled 版 `code-review`），让 bundled 与项目版 `code-review` 同层同名，演示 rank 决胜（skill_providers.py `BadgeSkillProvider` docstring）；
4. **[教学决策 4]** tool-skill 实现为第 4 章 tools 管线里的一个工具，与第 5 章 tool-bash 同型（tool_skill.py 模块 docstring）。

**教学简化**（真实机制更复杂，教学版降级）：

1. **[教学简化 1]** 真实 `SkillProvider` 没有 `watch` 方法：注册表在 `registerProvider` 时把 `control.invalidate` / `control.signal` 交给 provider（`packages/skill/skill/src/index.ts:270-276`），由 provider 自行择机失效；候选还携带不透明 locator 供 `get` 定位正文（`:77-82`）。教学版去掉 control 回路、用手动 `invalidate` 触发刷新，正文定位退化为 provider 自持（skill_registry.py 契约 / 候选注释）；
2. **[教学简化 2]** 真实 `SkillInvocationPolicy` 是 `{ modelInvocable, userInvocable }` 两个布尔（`packages/skill/skill/src/index.ts:49-54`），读取函数另有 `isModelInvocable` / `isUserInvocable`（`:126` / `:135`）；教学版把二者摊平成 `SkillDefinition` 上的两个字段、省略读取函数（skill_registry.py `SkillDefinition`）；
3. **[教学简化 3]** 真实 `collectLayer` 排序键为 rank→providerOrder→localOrder（`compareIndexedCandidates`，`packages/skill/skill/src/index.ts:569` / `:807-811`）；教学版只按 rank，稳定排序使注册序成为同 rank 的隐式次级键，且丢弃同名候选时不打日志（skill_registry.py 模块 docstring / `_collect_layer`）；
4. **[教学简化 4]** rank 阶梯只取两端 100/600，真实共 7 级（skill_registry.py 常量注释）；
5. **[教学简化 5]** filesystem 真实实现按 `roots:251` 枚举六类目录（project-dsh / project-agents / custom / user-dsh / user-agents / bundled）+ Chokidar watch（`SkillWatchManager`，`packages/skill/skill-filesystem/src/index.ts:294`）；教学版只扫单一 root、不 watch，正文定位用「name.md」约定（skill_providers.py 模块 docstring / `FilesystemSkillProvider` docstring）；
6. **[教学简化 6]** 真实 `parseFrontmatter`（`packages/skill/skill-filesystem/src/index.ts:931`）支持任意字段，并由 `parseInvocationPolicy`（`:1014`）解析调用策略；教学版只取 name/description（skill_providers.py `parse_skill_file`）；
7. **[教学简化 7]** 真实 tool-skill 还有 pre-step #1 的 `/name` 手势显式调用（packages/skill/tool-skill/src/index.ts:181，只扫 `source.kind === 'user'` 的消息；对 `modelInvocable: false`、即 `disable-model-invocation` 的技能，这是模型侧永远看不到它、用户却仍能手动加载的唯一入口）与目录替换渲染 `renderCatalogUpdate`（:289）；教学版只做「目录一次性注入 + skill 工具加载」（tool_skill.py 模块 docstring）；
8. **[教学简化 8]** 真实 tool-skill 在未知技能等分支直接 `throw`，由框架渲染成错误结果；第 4 章教学管线的 `_dispatch_body` 也有 `except Exception` 兜底，会把异常渲染成 `tool failed: ...`（`is_error=True`，`ch04/code/tools.py:423-430`）。教学版改为返回 `error: ...` 文本，差别只在错误措辞归谁：模型看到的是 skill 自己的文案，而不是管线的通用包装（tool_skill.py `_execute`）。

## 8. 小结与预告

本章把「技能」从焊死在提示词里的字符串，重构成一条三角色 seam：

| 机制 | 对应符号 | 治哪个坑 |
|---|---|---|
| provider 契约 + 注册是 effect | `SkillProvider` / `register_provider` | 坑 3 不可插拔 |
| 同层 rank 决胜 + 跨层近层遮蔽 | `_collect_layer` / `_collect` | 多来源并存的确定性 |
| 目录只出元数据、正文委托加载 | `list` / `get` | 坑 1 全量常驻 |
| 缓存 + invalidate + skills/change | `invalidate` | 注册变化即时可见 |
| skill 工具 + 目录注入会话 | `SkillTool` / `inject_catalog` | 坑 2 不可发现 |

来源的摆放位置也说明了分工：`packages/skill/` 是正式包族（`skill` 定义、`skill-filesystem` 发现、`skill-office` 随包 Office 工作流、`tool-skill` 消费、`tool-workspace-dependencies` 报告解释器路径），`packages/experimental/skill-badge` 是实验性的单技能 provider。同一份契约下，正式包承担发现、合并、监听与失败容忍，实验包只演示「最简实现」；增删一个来源不需要动注册表、消费者或其他来源。

与第 5 章对照，本章给出了 seam 的第二种形态：shell 是「组合期选定唯一实现、之后只调方法」，skill 是「多实现并存注册、读取时按规则合并」。后者把「谁能提供」与「谁取胜」拆开——提供是注册进来的事实，取胜是读取时算出的结果。

下一章看检索与代码语义两大外部能力：`ctx.web`（search/fetch）与 `ctx.lsp`（normalized 四操作查询），对应 `packages/web` 与 `packages/lsp`。同一章还会走到注册表一族的另一端：`ctx.computerUse` / browser-use 那种**单槽排他**的形态——组合期只容一个 provider，能力方法面为空，正好用来校验本章「provider 注册表」的边界。

**教学简化清单里新增的两条（不编号的局部差异）**：注册后委托 `provider.get()` 时不传 locator（§4.1）、加载后不做 `validateDefinition`（§4.1）；目录正文的固定框架、替换语义与「工具不可见则不发布」三条（§5.3）也一样只保留教学形态。

## 9. 附录：关键概念速查表

| 概念 | 一句话 | 教学代码 | 真实源码 |
|---|---|---|---|
| `ctx.skills` | 分层 skill provider 注册表（Service Definition） | `skill_registry.py` `SkillRegistry` | `packages/skill/skill/src/index.ts:356` |
| `SkillProvider` | 来源契约：`list()` 出候选、`get()` 出正文 | `skill_registry.py` | `packages/skill/skill/src/index.ts:247` |
| `SkillSummary` / `SkillCandidate` / `SkillDefinition` | 目录条目 / 候选（+rank+locator）/ 完整定义（+正文） | `skill_registry.py` | `packages/skill/skill/src/index.ts:57` / `:77` / `:87` |
| `register_provider` | 按 scope 落层注册，返回 disposer | `skill_registry.py` | `packages/skill/skill/src/index.ts:390` |
| rank 阶梯 | 同层内小者胜（教学版 100 / 600 两端） | `PROJECT_SKILL_RANK` 等 | `packages/skill/skill-filesystem/src/index.ts:35-39` |
| 跨层遮蔽 | 近层同名整体覆盖远层（复用 `ScopedLayers`） | `_collect` | `packages/skill/skill/src/index.ts:551-565` |
| `invalidate` | 清缓存 + revision + 广播 skills/change | `skill_registry.py` | `packages/skill/skill/src/index.ts:621` / `:648` |
| badge provider | **实验 provider**：静态内置候选 + 内置正文，默认组合不声明 | `skill_providers.py` `BadgeSkillProvider` | `packages/experimental/skill-badge/src/index.ts:36` |
| filesystem provider | 扫六类根出候选、按需读正文 | `skill_providers.py` `FilesystemSkillProvider` | `packages/skill/skill-filesystem/src/index.ts:149` |
| `parse_skill_file` | frontmatter 元数据 + Markdown 正文 | `skill_providers.py` | `packages/skill/skill-filesystem/src/index.ts:811` |
| skill 工具 | tools 管线里的工具，按名取正文渲染 `<skill_content>` | `tool_skill.py` `SkillTool` | `packages/skill/tool-skill/src/index.ts:84` / `:130` |
| 目录注入 | 目录以 source=skill-catalog 的 user 消息进会话 | `tool_skill.py` `inject_catalog` | `packages/skill/tool-skill/src/index.ts:217` |
| `render_skill_content` | 正文渲染成 `<skill_content>` 规范块 | `tool_skill.py` | `packages/skill/skill/src/index.ts:170` |
| office provider | 随包提供七个 Office 路由与创作/操作技能（office / office-docx / office-pptx / office-xlsx / office-render / office-recalculate / office-export-pdf，名单见 `:13`），目录列出摘要、正文按需加载；三个操作技能的正文末尾附加解释器与 CLI 的绝对路径（白名单与拼接见 `:90-91`） | —（本章不展开） | `packages/skill/skill-office/src/index.ts:13` / `:90-91` / `:94`（`apply` 注册 `dsh-office` provider） |
| workspace dependencies 工具 | 技能的同族工具：`load_workspace_dependencies` 报告随包 Python / Node.js / pnpm 的绝对路径与版本，不改 `PATH`；Office 技能按名引用它取默认解释器 | —（本章不展开） | `packages/skill/tool-workspace-dependencies/src/index.ts:294-295` |
