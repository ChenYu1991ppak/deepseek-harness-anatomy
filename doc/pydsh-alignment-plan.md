# 教学代码优化计划

> 核心原则：所有代码、解释都是为**章节的核心内容**服务的。不必完全照搬完整实现，没有用到的变量和代码不应出现。

---

## 已完成：全项目 `label` 参数清理

`effect()` 的 `label` 参数在教学中没有任何用途——它只在真实 cordis 的调试追踪中有意义。全部清除：

| 文件 | 改动 |
|------|------|
| `ch01/code/cordis.py` | `effect(execute, label="anonymous")` → `effect(execute)` |
| `ch02/code/agent_loop.py` | `ctx.effect(setup, label=...)` → `ctx.effect(setup)` |
| `ch04/code/tools.py` | `ctx.effect(lambda: dispose, label=...)` → `ctx.effect(lambda: dispose)` |
| `ch06/code/subprocess_service.py` | `ctx.effect(..., label=...)` → `ctx.effect(...)` |
| `ch08/code/system_prompt.py` | 两处 `ctx.effect(setup, label=...)` → `ctx.effect(setup)` |
| `ch09/code/scope.py` | `ctx.effect(setup, label=...)` → `ctx.effect(setup)` |
| `ch11/code/subagent_runtime.py` | `ctx.effect(lambda: unregister, label=...)` → `ctx.effect(lambda: unregister)` |
| `ch13/code/web_runtime.py` | `ctx.effect(setup, label=...)` → `ctx.effect(setup)` |
| `ch13/code/lsp_runtime.py` | `ctx.effect(setup, label=...)` → `ctx.effect(setup)` |
| `ch16/code/typert.py` | `ctx.effect(commit, label=...)` → `ctx.effect(commit)` |

## 已完成：ch01/code/cordis.py 精简

去掉了以下**没有被任何后续章节使用**的代码：

### 移除内容

| 移除项 | 原因 |
|--------|------|
| `Plugin` dataclass + `normalize_plugin` 四形态 | 无任何章节调用；仅 ch01 自身在 `ctx.plugin()` 内使用，内联 `_normalize` 即可 |
| `types` 模块导入 | 只被 `normalize_plugin` 使用 |
| `Context(parent)` 子容器 | 无任何章节调用 `extend()`；ch09 scope 用自有 ScopedLayers |
| `_lookup()` / `probe()` / `has()` / `inject()` | 无任何章节调用 |
| Fiber 变化即重载（UNLOADING / `_service_subs` / `_subscribe_service_events`） | 无任何章节依赖此行为 |
| `__all__` 中多余的导出名 | 只保留实际被 import 的符号 |
| 文档中 `pydsh` 参考引用 | 教学代码不应对读者暴露 pydsh 路径 |
| Symbols 中未使用的常量（invoke/fiber/intercept 等） | 只保留 services/events/dispose 三个 |

### 保留架构不变的核心

| 机制 | 状态 | 说明 |
|------|------|------|
| `Context` 服务表 + 监听器表 | ✅ | 核心 |
| `Service` 构造即注册 | ✅ | 核心 |
| `Fiber` PENDING/ACTIVE/DISPOSED 三态 | ✅ | 核心 |
| `plugin` + `inject` 满足才加载 | ✅ | 核心 |
| `effect` 可逆注册 | ✅ | 核心，**去掉 label** |
| `on/emit/serial/waterfall` 事件派发 | ✅ | 核心 |
| 插件三形态归一（函数/类/apply） | ✅ | 保留，`__all__` 不暴露 |

## 已完成：ch02 代码清理

| 文件 | 改动 |
|------|------|
| `ch02/code/agent_loop.py` | `LlmStub.stream()` 移除未使用的 `system_prompt=None` 参数（保留 `**options` 以兼容 ch08 调用） |
| `ch02/code/agent_loop.py` | `__all__` 移除 `Inbox`、`PromptAssembly`、`PromptSection`（从未被外部 import） |
| `ch02/code/agent_loop.py` | `ReactLoopAgent.options` 保留 — ch11 依赖此属性读取 `subagent_depth` |

## 已完成：ch04-ch07 代码清理

| 文件 | 改动 |
|------|------|
| `ch04/code/main.py` | 移除未使用的 `AgentLoop` import |
| `ch05/code/shell.py` | 移除未使用的 `field` import |
| `ch05/code/tool_bash.py` | `__all__` 移除 `render_result`（仅内部使用） |
| `ch05/code/bash_local.py` | `__all__` 移除 `ENV_OVERRIDES`、`Config`、`assert_serviceable_bash_config` |
| `ch06/code/subprocess_service.py` | `__all__` 移除 `DSH_ENV_PREFIX`、`OutputCollector`、`SubprocessHandle`、`scrub_env` |
| `ch06/code/sandbox_service.py` | `__all__` 移除 `ConfinedArgv` |
| `ch07/code/deepseek_adapter.py` | `__all__` 移除 `serialize_request`、`parse_sse`、`translate`、`map_usage`、`map_finish_reason` |
| `ch07/code/pi_adapter.py` | `__all__` 移除 `to_stream_chunks`、`map_stop_reason` |
| `ch07/code/token_meter.py` | `__all__` 移除 `ROLE_OVERHEAD`、`estimate_content` |
| `ch07/code/llm_runtime.py` | `__all__` 移除 `STREAM_CHUNK_TYPES` |
| `ch07/code/credentials.py` | `__all__` 移除 `CredentialProvider`、`SettingsProvider`（ABC，外部只用具体实现） |

## 已完成：ch08-ch17 代码扫描与清理

### 未使用 import 清理

| 文件 | 改动 |
|------|------|
| `ch08/code/system_prompt.py` | 移除未使用的 `Context` import（只保留 `Service`） |
| `ch09/code/scope.py` | 移除未使用的 `from cordis import Service`；`tools` import 精简为 `ToolLayer, ToolResult, ToolRuntime`（`ToolDefinition`/`ToolExecution` 未使用） |
| `ch11/code/providers.py` | 移除过时的 `# noqa: E402` 注释 |

### `__all__` 清理（仅内部使用的名字移出导出表，定义保留）

| 文件 | 移除的导出 |
|------|-----------|
| `ch16/code/gateway.py` | `LookupStore` |
| `ch16/code/sdk.py` | `HarnessClient`, `HarnessSession`, `JsonRpcLineTransport`, `SERVER_NOTIFICATIONS`, `SERVER_REQUESTS` |
| `ch16/code/typert.py` | `CODEC_STRICT`, `TypeGraph`, `TypeNode`, `TypertContribution`, `typert_key` |
| `ch17/code/workflow.py` | `STOP_COMPLETED`, `STOP_CANCELLED`, `STOP_ERROR` |
| `ch17/code/ralph.py` | `RALPH_META`, `ralph_script`, `REPORT_STATUSES` |

### 死代码移除

| 文件 | 改动 |
|------|------|
| `ch17/code/workflow.py` | 删除 `STOP_CANCELLED` 常量——`cancel()` 是教学 no-op，从不产生 cancelled 状态；`cancel()` docstring 补充说明真实实现会把 stopReason 置为 "cancelled" |

### 保留项（扫描中确认不可删）

- `ch10` `prune_tool_result`/`PRUNE_MARKER`、`ch08` `suppress_runtime_context`、`ch12` `_revision`/`user_invocable`：在章节正文中有讲解、对应真实源码 API，属于教学叙事的一部分
- ABC 抽象方法参数、Service 子类 `config=None`：接口契约，合法
- 所有 `import os`/`import sys`：跨章 `sys.path.insert` 模式，合法

### 验证

- 全项目 AST 扫描：**0 个未使用 import**（ch01-ch17 所有 .py 文件）
- 全 17 章回归测试：**全部 PASS**

---

## 已完成：全项目 markdown 文档同步

### 第一批：清除过时的 `label=` 参数引用

| 文件 | 改动 |
|------|------|
| `ch01/ch01-cordis-kernel_en.md` | `ctx.effect(lambda: dispose, label=...)` → `ctx.effect(lambda: dispose)` |
| `ch02/ch02-agent-loop_{zh,en}.md` | `ctx.effect(setup, label=...)` → `ctx.effect(setup)` |
| `ch04/ch04-tools-pipeline_{zh,en}.md` | `ctx.effect(lambda: dispose, label=...)` → `ctx.effect(lambda: dispose)` |
| `ch08/ch08-system-prompt-context_{zh,en}.md` | `ctx.effect(setup, label=...)` → `ctx.effect(setup)` |
| `ch09/ch09-scope_{zh,en}.md` | `ctx.effect(setup, label=...)` → `ctx.effect(setup)` |
| `ch11/ch11-subagent_{zh,en}.md` | `ctx.effect(lambda: unregister, label=...)` → `ctx.effect(lambda: unregister)` |
| `ch13/ch13-web-lsp_{zh,en}.md` | 两处 `ctx.effect(setup, label=...)` → `ctx.effect(setup)` |

同时更新 ch01 中英文档的代码段、源码对照表、小结表、符号映射表，使其与精简后的 `cordis.py` 一致。

### 第二批：文档摘录与实际代码的签名一致性检查（全 34 份 md）

用脚本提取所有 markdown python 代码块中的 `def`/`class` 签名，与各章实际代码逐一比对。发现并修复：

| 文件 | 问题 | 修复 |
|------|------|------|
| `ch02/ch02-agent-loop_{zh,en}.md` | `LlmStub.stream` 摘录仍是旧签名 `system_prompt=None` | 更新为 `**options`，正文说明改为"桩只消费 messages，其余进 **options 忽略" |
| `ch09/ch09-scope_{zh,en}.md` | 6 处骨架摘录 `class ScopedToolRuntime:` 漏掉基类 | 补全为 `class ScopedToolRuntime(ToolRuntime):`（继承是本章叙事关键） |
| `ch05/ch05-..._en.md` | `apply`/`bash_tool`/`render_result`/`SandboxExecutor` 摘录与代码严重漂移（`exec` 参数、`order=10`、`background`、`workdir="/sandbox"`、`start` 抛 NotImplementedError 等均与代码不符） | 按实际代码（=中文版）重写全部四个摘录块 |
| `ch16/ch16-..._en.md` | **§6/§7/§8段6/附录整体描述不存在的代码**：`SdkServer`/`SdkRuntime`/`SdkHostBridge` 三层骨架、`SDK_METHODS` 10 方法、`session.send` 虚构运行输出——实际代码是 `JsonRpcLineTransport`/`HarnessSdkJsonRpcServer`/`HarnessClient`/`DeepSeekHarness`/`HarnessSession`；且缺失整节 §9 源码对照 | 以中文版为基准重写 §6、§7；§8 段 6 换成真实输出译文；补译 §9 源码对照表；小结/附录重编号为 §10/§11 并按中文版校正（删除 "SDK 10 methods"、`SdkServer` 映射等虚构条目） |

**保留项（比对中确认为合法教学素材，非漂移）**：ch02 `chat`/`build_prompt`/`append`/`section`/`assemble`、ch03 `MemoryPersistence`/`FilePersistence`、ch07 `EchoAdapter` ——均为「概念引入：最小示例」栏目的自包含示意代码，不是真实文件摘录。

**结论**：zh 文档签名比对 0 漂移；en 文档修复后 0 漂移（概念素材除外）。

### 第三批：全项目"运行输出真实性"验证

用脚本实跑每章 `main.py`，与文档「完整运行输出」节逐行比对：

| 检查项 | 结果 |
|--------|------|
| zh 文档运行输出 vs 实际输出（ch02-ch17） | **全部一致**。ch10/ch14 的"此处省略"为合法编辑标记；ch04 的 `/tmp/tmpXXXXXX.txt` 为已声明的占位符 |
| ch06 动态值（pgid/耗时/解释器路径） | 已有标注覆盖 pgid/耗时/临时路径；**补充**"解释器路径随机器而异，实录来自作者机器"（zh+en 两版） |
| en/zh 章节结构比对（全 17 章） | **全部一致**（ch16 en 修复后恢复 12 节对齐，含补译的 §9 源码对照） |
| en 文档运行输出 token 抽查 | 被标记项均为合法译文（项目惯例：en 文档翻译输出字符串内容），无结构性虚构 |

**修复的唯一实质问题**：ch16 en §8 段 6 的虚构输出已在上批替换为真实输出译文。

---

## 后续执行原则

### 原则 1：只添加服务于"教学故事线"的代码

每一章有一个核心机制。加代码之前先回答：**这段代码回答了本章的哪个问题？**

- ch01 的故事线：能力、依赖、生命周期、协作四个问题
- ch02 的故事线：agent-loop 闭环怎么拼装出来
- ch03 的故事线：会话事件流怎么落盘、怎么冷读
- 以此类推

### 原则 2：未被使用的导出和参数不出现

- `__all__` 只列真正被其他模块 import 的名字
- 函数参数表中不出现只为了"对齐真实 API"而存在的未使用参数
- Symbols 只列实际访问的键

### 原则 3：不要为了"将来可能需要"而添加代码

- 不提前添加"为后续章节预留"的 API（如 probe/extend/inject）
- 后续章节需要时再在 ch01 添加，同步更新所有章节
- 这保证每段代码都有**当下的**读者能理解的用途

### 原则 4：`[教学简化]` 标注只保留关键差异

- 只标注对理解当前行为有影响的简化
- 去掉"真实 cordis 有几态"这类与当前教学无关的源码细节
- 保持标注简练，一两句话说明白

---

## 后续任务

### 待排查：其他章节中可能存在的未使用代码

- `Symbols` 导入：ch14, ch17 仍有 `from cordis import Symbols`，检查是否确实用到
- 各章 `inject = [...]` 声明：是否真的被 ch01 的 Fiber 消费（是的，`getattr(callback, "inject", None)`）
- 各章 `__all__` 导出：无用的导出清理

### 待做：逐章自检

每改一章，验证：
1. 本章 `main.py` 能正常运行
2. ch02-ch17 全部能正常运行（批量测试脚本）
3. docstring 中不出现 pydsh 内部路径
4. 没有悬浮的未使用 import

---

*版本：v2.0 · 2026-09-14*