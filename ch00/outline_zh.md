# 《DeepSeek Harness（dsh）》源码学习大纲

> 递进式章节大纲：每章在前一章基础上新增一个机制，第 17 章把全部机制整合为端到端流程，第 18 章从横切面收束。
> 术语在章节内首次出现处定义，全文末尾附「术语命名表」供跨章一致性参考。
> 正文简体中文，代码/符号英文，配图 Mermaid。
> 教学代码：`chNN/code/` 按章增量累积（每章一个自包含目录）。
> 版本锚：基于上游 HEAD `8bd0733`（dsh-v0.2.2-alpha.3，2026-11-03）定稿。

## 0. 总览

- **世界观**：一切皆插件；agent = model + harness；注册即效应；seam 三角色。
- **章节数**：18 章（ch01–ch18）。ch01–ch16 逐层加厚，ch17 主线整合，ch18 横切面收束。
- **阅读路径**：先读 vendor/Cordis 原语 → 最小 agent-loop 闭环 → 逐机制加厚 → 第 17 章一条启动路径跑起来 → 第 18 章观测这套跑起来的系统（事件如何出站）。
- **章节定位**：第 17 章仍是主线整合章（一条启动路径 + 一个执行循环 + 一个代码执行宿主）；第 18 章不是第二个整合章，它是横切面收束——只回答"系统跑起来之后，事件各自怎么离开进程"。
- **读者起点**：无需先读完整仓，跟着大纲逐章建立心智模型。

## 1. Cordis 容器内核（一切皆插件）

- **主题**：插件容器如何装配（上下文/服务/事件的最小核心）。
- **新增机制**：`Context` 服务容器 + `Service` + plugin 注册 + `inject` 依赖检查 + `Fiber`/`effect` 可逆注册 + `on`/`emit`/`serial`/`waterfall` 事件派发。
- **对应源码模块**：`vendor/cordis`（vendored 自 Cordis 内核，本仓包名 `@deepseek-ai/cordis`；文件 context/service/registry/events/fiber/reflect/utils）。
- **本章回答的问题**：什么是上下文、服务、可逆注册、事件派发；它们如何被一个 `Context` 统一起来。
- **本章新增教学代码**（`ch01/code/`，自包含、仅用标准库，`python ch01/code/hello.py` 可运行）：
  - `cordis.py`：教学版 Cordis 内核。
  - `hello.py`：§2 最小可运行例。
  - `bad_example.py`：§1 反面示例。

## 2. 最小 agent-loop 闭环（一条消息进，一条回复出）

- **主题**：一条用户消息如何变成一条回复——最小可运行闭环。
- **新增机制**：`Session`（内存 append-only）+ `SystemPromptService`（+`render_prompt`）+ `AgentLoop`（kick/turn/step）。
- **对应源码模块**：`packages/core/agent-loop`（`agent.ts` / `inbox.ts`）+ `packages/core/session`（已拆成 surface/repair/fork/seq-ranges/preparation 等 10 个文件）+ `packages/core/system-prompt` + llm stub。
- **本章回答的问题**：对话状态放在哪里、system prompt 从哪来、谁驱动循环；一个 agent 如何被容器拼装出来。
- **本章新增教学代码**（`ch02/code/`，增量复用 ch01 的 `cordis.py`，`python ch02/code/main.py` 可运行）：
  - `agent_loop.py`：教学版 agent-loop。
  - `main.py`：可运行装配入口。

## 3. session 事件流、handle 持久化与格式迁移（写时无声，冷读有迹）

- **主题**：会话如何被记录、落盘、重放、跨版本读取。
- **新增机制**：`SessionEvent` 追加日志 + `SessionPersistence`/`SessionHandle` 所有权契约（七类错误）+ 后端拥有的 live write path + 格式版本链 v0→v4（相邻迁移边）+ 冷读投影与缓存 + 捕获侧遥测（授权与出向见第 18 章）。
- **对应源码模块**：
  - `packages/session/session-persistence`（`index.ts` 服务契约、`handle.ts`、`errors.ts`）、`session-persistence-jsonl`（唯一 session 后端：单写者路由、批量窗、耐久屏障、撕裂尾恢复）。
  - 格式链六包：`packages/session/session-format`、`session-format-catalog`、`session-format-v0-to-v1`、`session-format-v1-to-v2`、`session-format-v2-to-v3`、`session-format-v3-to-v4`。
  - `packages/session/session-projection`、`session-projection-cache`、`session-title`、`session-telemetry`（捕获与脱敏）。
  - `packages/storage/{storage,storage-domain,storage-json,storage-sqlite}`：只管非 session 应用数据（workspace 记录、session sidecar），与持久化 seam 划清边界。
  - `packages/core/session`（`repair.ts`、`surface.ts`）：中断 turn 修复与冷读折叠。
- **本章回答的问题**：事件流如何在不打扰运行循环的前提下落盘？一条日志为什么只有一个写者、靠什么强制？冷读如何恢复中断 turn 与撕裂尾？旧版本日志怎么被读懂、读不懂时为什么必须拒绝？
- **本章新增教学代码**（`ch03/code/`，增量复用 ch01 的 `cordis.py` 与 ch02 的 `agent_loop.py`，`python ch03/code/main.py` 可运行）：
  - `session_persistence.py`：handle 版（`SessionHandle` 契约 + 单一 JSONL 后端 + live write path + 一段 v3→v4 迁移演示 + 捕获侧遥测）。
  - `main.py`：可运行装配入口（8 段闭环，含格式迁移段）。
  - `bad_example.py`：§1 反面示例。

## 4. tools 注册表与执行器管线（先定义，再展示，最后把守）

- **主题**：模型如何获得工具：先注册定义，再决定展示，最后把守每一次调用。
- **新增机制**：`ctx.tools` 作用域注册 + restriction（工具掩码）+ 工具视图（ToolView）与 schema 投影 + pre-execute/execute/post-execute 三段瀑布 + 与注册表并列的执行器 seam（原生执行器 / PTC 执行器）。
- **对应源码模块**：
  - `packages/core/tools`（`index.ts` 只剩注册与层；`contracts.ts`、`presentation.ts`、`pipeline.ts`、`batch.ts`、`executor.ts`、`schema.ts`）。
  - `packages/core/tool-executor-native`（原生执行器）、`packages/core/tool-executor-ptc`（PTC 执行器）。
  - `packages/core/agent-tool-presentation`（Deprecated：旧的选择器行，已迁移为执行器行）。
  - `packages/guard/*`、`packages/deliverables/tool-present`（执行器内的 `present` 实例）。
- **本章回答的问题**：工具如何注册、隔离、隐藏、覆盖？模型从哪里得知有哪些工具、schema 由谁决定？一次调用经过哪些关卡？PTC 与 native 为什么能共享同一张注册表、换执行器为什么不用动注册表？
- **本章新增教学代码**（`ch04/code/`，增量复用 ch01/ch02）：
  - `tools.py`：注册表 + 工具视图 + 三段瀑布 + native / PTC 两个执行器桩（含 `run_code`）。
  - `main.py`：换执行器、注册表不动的一段演示。
  - `bad_example.py`：§1 反面示例。

## 5. capability seam 三角色

- **主题**：一个能力如何被「定义、实现、消费」三分解耦。
- **新增机制**：Service Definition / Service Provider / Consumer 三角色 + 完整 seam 契约（以 shell 为例，`resolve`/`execute` 抽象方法）+ 三种变体对照（空定义式、描述字段、整包替换 Provider）。
- **对应源码模块**：`packages/shell/shell`、`packages/shell/bash-local`、`packages/shell/tool-bash`；`docs/capability-seams.md`。
- **本章回答的问题**：一个能力如何拆成三个互不耦合的角色？方法调用式 seam 与事件瀑布式管线有何本质区别？为什么换掉实现，定义与消费者可以一行不改？
- **本章新增教学代码**（`ch05/code/`）：`shell.py`、`bash_local.py`、`tool_bash.py`、`sandbox_executor.py`、`main.py`、`bad_example.py`。

## 6. subprocess / shell / fs 执行世界

- **主题**：让 agent 真正能在机器上做事的执行面。
- **新增机制**：`ctx.subprocess` 进程树 + `ctx.fs`（版本守卫、原子写、意图瀑布）+ `ctx.sandbox` 隔离 + `workspace/changes` 双快照 + SSH provider family（整包替换到远端）。
- **对应源码模块**：
  - `packages/subprocess/{subprocess,subprocess-local}`、`packages/shell/{shell,bash-sandbox}`、`packages/fs/{fs,fs-local,fs-sandbox,fs-patch}`、`packages/sandbox/{sandbox,sandbox-local}`。
  - `native/system`（`landlock-run` 为其子路径）。
  - `packages/ssh/{ssh,ssh-helper-runtime,subprocess-ssh,fs-ssh,sandbox-ssh}`（五包）。
  - `packages/deliverables/workspace-changes`（意图瀑布的消费者、双快照与 `workspace/changes` 事件）。
- **本章回答的问题**：为什么不能直接拿标准库 `subprocess` 给 agent 用？进程树指什么、为什么 terminate 要杀整棵树？文件系统为什么要做成 seam？sandbox 如何包住 runner 却不碰工具层？一轮改了哪些文件、为什么事件里只有 turn 号？把执行世界搬到远端，消费者要改几行？
- **本章新增教学代码**（`ch06/code/`）：`subprocess_service.py`、`fs_service.py`、`sandbox_service.py`、`bash_sandbox.py`、`workspace_changes.py`、`remote_world.py`、`mock_runner.py`、`main.py`、`bad_example.py`。

## 7. LLM 适配与流式

- **主题**：模型层如何被替换与如何流式输出。
- **新增机制**：`ctx.llm` 适配器 seam + 统一 chunk 流式协议 + credentials seam + SettingsForms 编辑面 + 请求扩展注册表（`register→prepare→accept` 三段事务）。
- **对应源码模块**：`packages/llm/{llm,llm-deepseek,llm-pi-ai,llm-retry,token-meter,deepseek-llm-api-extensions,plugin-package-inventory-deepseek}`；`packages/credentials/credentials`、`packages/settings/settings`（`SettingsForms`，不是 key-value 仓库）。
- **本章回答的问题**：桩位如何换成真实模型而调用方一行不改？各家流式协议如何被一套 chunk 屏蔽？secret 放在哪里？插件想往请求里加字段，怎么加才不用改适配器？
- **本章新增教学代码**（`ch07/code/`）：`llm_runtime.py`、`deepseek_adapter.py`、`pi_adapter.py`、`credentials.py`、`token_meter.py`、`main.py`、`bad_example.py`。

## 8. system-prompt 组装与 context

- **主题**：发给模型的那段「提示词」从哪来。
- **新增机制**：prompt section 注册/合并 + workspace 指令 + 时间上下文 + `session-reference` + `deliverable-file-references`（固定 order 的纯 prompt 段）。
- **对应源码模块**：`packages/core/system-prompt`、`packages/context/{agent-instructions,time-context,session-reference}`、`packages/workspace/workspace`、`packages/deliverables/deliverable-file-references`、`packages/preset/persona`（每会话人格段）。
- **本章回答的问题**：每一段提示词从哪来？多个插件如何写同一份提示词而不互相踩踏？工具 schema 为什么不在提示词文本里？`context()` 与 context 插件是同一种机制吗？
- **本章新增教学代码**（`ch08/code/`）：`system_prompt.py`、`prompt_agent.py`、`context_plugins.py`、`main.py`、`bad_example.py`。

## 9. scope 作用域注册

- **主题**：同一进程里不同 agent 如何隔离各自能力。
- **新增机制**：`ScopedLayers`（global 层 + 每 agent 叠加层 + lineage 链）+ shadowing + restriction 链上交集 + per-Agent scope（按 Agent 铸造的作用域，作用域即卸载单位）。
- **对应源码模块**：`packages/core/scope`（`index.ts`、`store.ts`）；消费者 `packages/core/tools`；同构应用 `packages/core/system-prompt`；per-Agent scope 实例 `packages/experimental/browser-use-runtime`（详见第 13 章）。
- **本章回答的问题**：多个 agent 同进程共存时，工具注册如何不互相污染？同名工具谁胜出？子代理为什么能看到父代理的注册？如何只对某个 agent 收起某工具？
- **本章新增教学代码**（`ch09/code/`）：`scope.py`、`main.py`、`bad_example.py`。

## 10. compaction 与 token 压力

- **主题**：上下文太长怎么办。
- **新增机制**：`compaction` seam（checkpoint / region / tool-pairing）+ tool-result-pruner + image-offload + token-meter 触发；surface 折叠与增量快照。
- **对应源码模块**：`packages/compaction/{compaction,compaction-basic,compaction-tool-result-pruner,compaction-image-offload}`、`packages/core/session/src/surface.ts`、`packages/llm/token-meter`。
- **本章回答的问题**：累计 token 撞上窗口上限时会发生什么？日志要求完整、模型只看窗口，compaction 如何让两件事同时成立？为什么不能从任意位置开始压？
- **本章新增教学代码**（`ch10/code/`）：`compaction.py`、`surface.py`、`main.py`、`bad_example.py`。

## 11. subagent 委派

- **主题**：把任务委派给子代理并行/隔离执行。
- **新增机制**：`ctx.subagents` seam + 多 transport provider（in-process spawn / fork、acp、codex、claude-code、dsh-sdk）+ 委派深度与 lineage + `tool-subagent`/`tool-subagent-control` + Team 共享任务板（整块快照 + revision CAS + TeamJournal）。
- **对应源码模块**：`packages/subagent/{subagent,subagent-spawn-in-process,subagent-fork-in-process,tool-subagent,tool-subagent-control}`、`packages/team/{agent-team,tool-agent-team}`。
- **本章回答的问题**：子任务为什么要在自己的会话里跑？多种委派方式怎么共用一个入口？模型怎么自主触发委派并保持可续？Team 的共享任务板凭什么不建 Agent、不调度？
- **本章新增教学代码**（`ch11/code/`）：`subagent_runtime.py`、`providers.py`、`tool_subagent.py`、`task_board.py`（CAS + 环检测 + 投影）、`main.py`、`bad_example.py`。

## 12. skill 加载

- **主题**：可插拔的专业能力包。
- **新增机制**：`ctx.skills` provider 注册表（多源合并、rank 阶梯、近层遮蔽）+ 按需加载的目录消息（目录替换渲染）+ 实验 provider（`experimental/skill-badge`）。
- **对应源码模块**：`packages/skill/{skill,skill-filesystem,skill-office,tool-skill,tool-workspace-dependencies}`、`packages/experimental/skill-badge`。
- **本章回答的问题**：技能正文为什么不能直接焊进系统提示词？多个来源同时在场，seam 怎么容纳？模型怎么按需加载一个技能的正文？
- **本章新增教学代码**（`ch12/code/`）：`skill_registry.py`、`skill_providers.py`、`tool_skill.py`、`main.py`、`bad_example.py`。

## 13. web / lsp 与独占能力：选谁在执行时决定

- **主题**：外部能力以注册表进来，谁在什么时刻被选中。
- **新增机制**：`ctx.web`（执行时择优并归一化）+ `ctx.lsp`（按扩展名维度路由、原子注册、四操作闭并集）+ 单槽排他注册表（`ctx.computerUse`/`ctx.browserUse`：只做组合期准入）+ MCP 客户端挂载。
- **对应源码模块**：`packages/web/{web,web-search-deepseek,web-fetch-http,tool-web}`、`packages/lsp/{lsp,lsp-stdio,tool-lsp}`、`packages/computer-use/computer-use`、`packages/browser-use/browser-use`、`packages/experimental/{cua,computer-use-cua-driver-mcp,browser-use-runtime,browser-use-playwright,browser-use-chrome-devtools,browser-use-stagehand}`。
- **本章回答的问题**：搜索/抓取为什么不能把引擎调用写死？web 的执行时择优和 lsp 的维度路由差在哪？全局独占能力（一张桌面、一个浏览器）的注册表还剩哪些职责？谁在什么时刻被选中？
- **本章新增教学代码**（`ch13/code/`）：`web_runtime.py`、`web_providers.py`、`lsp_runtime.py`、`lsp_providers.py`、`tool_web.py`、`tool_lsp.py`、`main.py`、`bad_example.py`。
- **说明**：§5 单槽排他注册表仅 48 行，正文内联讲解，不新增教学文件。

## 14. interaction / permission / goal / plan

- **主题**：人类如何介入、授权与协同规划。
- **新增机制**：approval seam（request/decide/effectivePolicy/setPolicy）+ permission-presets + commands + user-questions + goal domain（drive/reservation/commit）+ plan-mode。
- **对应源码模块**：`packages/interaction/{user-approval,user-questions,permission-presets,commands}`、`packages/goal/{goal,goal-round-driver}`、`packages/plan/plan-mode`、`packages/core/tools/src/pipeline.ts`（ask 分支的落点）。
- **本章回答的问题**：工具管线的 ask 分支在 HEAD 接到了谁？哪些调用需要问人、由谁决定？多轮对话里如何让目标不被压缩弄丢？
- **本章新增教学代码**（`ch14/code/`）：`approval.py`、`ask_gate.py`、`questions.py`、`presets.py`、`commands.py`、`goal.py`、`goal_round.py`、`plan_mode.py`、`main.py`、`bad_example.py`。

## 15. preset / bundle / profile 组合

- **主题**：把散件组装成可复用的产品形态。
- **新增机制**：声明式 preset（一行插件行，不是目录扫描）+ 每个 Agent 绑定创建时的 revision（`retain` 返回一代 Generation，新 revision 不打扰旧 agent）+ bundle patch 层 + profile 栈 + opt-in 实验 bundle。
- **对应源码模块**：`packages/preset/{agent-preset,agent-preset-registry,persona}`、`packages/bundle/{base,headless,web-app}`、`packages/boot/app-boot`、`packages/util/official-bundles`、`packages/experimental/{cua-bundle,ralph-bundle,session-titles-bundle}`。
- **本章回答的问题**：同一套面向模型的插件组合，怎么让多个会话复用时又不被升级打断？启动清单怎么按产品形态增量定制？preset / bundle / profile 各管哪一层？为什么同名 preset 与第 14 章的 permission-presets 不是一回事？
- **本章新增教学代码**（`ch15/code/`）：`preset.py`（revision/Generation 版）、`bundle.py`、`patch.py`、`profile.py`、`main.py`、`bad_example.py`。

## 16. typert / api / sdk

- **主题**：跨进程的类型化 RPC 运行时。
- **新增机制**：typert 类型图与 InvocationDescriptor + RPC 网关两条校验路径（strict / src-json）+ JSON-RPC stdio SDK + `TypertRemoteService` 与 `@Remote` 导出名 + ACP。
- **对应源码模块**：`packages/typert/{protocol,registry,generator,loader}`、`packages/api/{gateway,remotes,workspace-files}`、`packages/sdk/{protocol,client,server}`、`packages/acp/acp`、`packages/document/office-to-pdf`（`@Remote render` + `@Remote('generation')`）。
- **本章回答的问题**：进程外的消费者怎么调用宿主进程里的能力？类型契约如何跨进程并在边界上校验？新增导出方法为什么不用手写路由与客户端桩？`typert`、网关、SDK、ACP 各管什么？
- **本章新增教学代码**（`ch16/code/`）：`typert.py`、`gateway.py`、`sdk.py`、`main.py`、`bad_example.py`。
- **边界**：`api/session-controller` 与 `host/apiproxy` 是 host 侧 BFF（把 host 语义按客户端重组），本教程不展开。

## 17. workflow / PTC 运行时 / python 端到端

- **主题**：全部机制整合为完整启动链路与端到端流程。
- **新增机制**：`ctx.workflowEngine` seam（校验 → 提交 → 事件流 → 结果四段骨架，脚本交给 PTC 运行时）+ Ralph（已降级为 opt-in 实验 bundle）+ 一个启动器与四个 shipped profile + Python SDK 端到端。
- **对应源码模块**：
  - `packages/workflow/{workflow,workflow-ptc}`（`WorkflowExecution` = `vm.Context` + `vm.Script`）。
  - `packages/ptc-runtime/{ptc-runtime,ptc-runtime-node}`（`ctx.ptcRuntime`，第 4 章执行器的运行宿主）。
  - `packages/experimental/{tool-ralph,ralph-bundle}`（默认组合不启用）。
  - `packages/boot/app-boot`、`apps/cli`（`bin.ts` / `profile-boot.ts`）、`packages/util/official-bundles`、`packages/bundle/base`。
  - `python/sdk`（`api.py` / `client.py`）、`packages/sdk/server`（同一条 JSON-RPC 协议的服务端）。
- **本章回答的问题**：让多个 agent 协作的 workflow 引擎为什么也只是「一个 seam」？Ralph 每轮都开一个全新子 agent 图什么？从 Python SDK 构造到模型吐出第一个字，请求穿过哪些服务？
- **本章新增教学代码**（`ch17/code/`）：`workflow.py`（四段式）、`ralph.py`（标注 opt-in）、`boot.py`、`sdk.py`、`main.py`。
- **边界**：`apps/web` 的浏览器前端与 `packages/host` / `packages/client` 的客户端装配不在本章展开；本章只走启动链路与执行宿主。

## 18. 遥测通道：一个空服务，两条队列，三份所有权

- **主题**：系统跑起来之后，事件如何各自出站。横切面收束：观测第 17 章跑起来的系统；主线整合已在第 17 章完成，本章不自称整合章。
- **新增机制**：空 mount 的 `ctx.otel`（不建连接/队列/身份，只给两个工厂）+ 两条通道两套排队哲学（普通事件按条攒批 / session 日志按字节闸攒批、单条超限直接拒、串行单飞）+ 授权与拒发在消费者侧（默认 `FEEDBACK_ONLY`、`sourceEvent` 缺失即拒、`eventAt(seq)` 同一性校验）+ 关停所有权（消费者自己的 disposal 里 `shutdown(signal)`，外层 deadline，超时 `stopPending()` 丢队列）。
- **对应源码模块**：
  - `packages/telemetry/otel`（`index.ts`、`event-log.ts`、`event-transport.ts`、`session-log.ts`）：服务定义与两条通道的实现。
  - `packages/host/product-telemetry-otel`：第二个宿主级消费者，是"全局面"的证据（`inject=['otel']`）。
  - `packages/session/session-telemetry-otel`（授权侧）+ `packages/session/session-telemetry`（捕获侧，第 3 章）。
- **本章回答的问题**：遥测服务挂载时到底创建了什么、为什么是"什么都没有"？普通事件与 session 日志为什么必须走两条通道、排队规则差在哪？谁决定一条 session 事件能不能出站？通道归消费者所有，关停时谁排空、谁在 deadline 之后丢弃？
- **本章新增教学代码**（`ch18/code/`，仅标准库，复用 `ch01/code/cordis.py` 的 `Context`/`Service`）：
  - `otel.py`：空 mount 的 `OTel(Service)` + `create_event_reporter` / `create_session_log_reporter` 两个工厂。
  - `channels.py`：`CountBatcher`（按条）+ `ByteGateProcessor`（按字节、单条拒、单飞）+ 可注入失败/超时的内存 `Sink`。
  - `backends.py`：两个消费者适配器（feedback 授权 + `sourceEvent` 同一性 + deadline 关停 / 普通事件）。
  - `main.py`：六段演示，逐段打印断言。
  - `bad_example.py`：单例 provider + 一条共享队列 + 无 owner 关停的三个坑。

---

## 递进性自检清单

- [x] 第 1 章建立容器内核（一切皆插件，hello.py 闭环）。
- [x] 第 2 章建立最小可运行核心（agent-loop 闭环，无工具无持久化）。
- [x] 第 3 章起每章只新增一个机制簇。
- [x] 第 17 章整合所有机制为端到端启动链路（主线整合章）。
- [x] 第 18 章以横切面收束：观测第 17 章跑起来的系统，不自称整合章。
- [x] 每章写明：章号、主题、新增机制、源码模块、本章回答的问题。
- [x] 全书 18 章；所有「对应源码模块」按上游 HEAD `8bd0733` 的包路径撰写。

---

## 术语命名表

> 命名纪律：一个概念只有一个中文名；「英文 / 源符号」列给出代码里能 grep 到的名字。只收本项目特有概念，通用编程概念（队列、TLS、PID 等）不收。首现章指正文首次给出定义的那一章。

| 术语 | 英文 / 源符号 | 一句话定义 | 首现 |
|---|---|---|---|
| session 格式链 v0→v4 | `session-format*`（六包） | 每个版本一条相邻迁移边，catalog 启动时校验链条无缺口，物理行只解码一次。 | 03 |
| 相邻迁移边 | `defineSessionFormatMigration`（`session-format/src/chain.ts`） | 一条只允许 `vN → v(N+1)` 的迁移声明；跨版本必须拆成多条，声明期即锁死。 | 03 |
| live write path（实时写路径） | 后端（而非会话内缓冲）持有写通道 | 事件进 `Context` 总线后由后端按 session id 路由到活跃 handle 批量落盘；写者是 handle，且只有一个。 | 03 |
| SessionHandle | `session-persistence/src/handle.ts` | `create/open` 返回的每会话读写句柄（read/append/flush/close），日志读写的唯一通道。 | 03 |
| handle 所有权契约 | `session-persistence/src/errors.ts`（七类错误） | 同一会话同一时刻只允许一个 handle 写；重复打开报 AlreadyOwned，被抢走报 OwnershipLost。 | 03 |
| 耐久屏障（flush） | `SessionHandle.flush` | 把已 append 的事件落盘并返回确认；不返回就不算持久化。 | 03 |
| storage domain | `packages/storage/*` | 非 session 应用数据的键值域（workspace 记录、session sidecar）；session 日志不走这里。 | 03 |
| FEEDBACK_ONLY | `session-telemetry-otel` 枚举 | 遥测默认档：只有用户显式 feedback 的事件才授权回传（另一档是 `DISABLED`）。 | 03 |
| sourceEvent | `SessionTelemetryRecord` | 遥测记录里保留的源事件引用；被脱敏删掉源即拒发。 | 03 |
| 同一性校验 | `eventAt(seq)` 比较（`!==`） | 被授权的必须是规范日志同一位置上的同一个对象；重放副本不授权任何前缀。 | 03 |
| PTC（programmatic tool calls，程序化工具调用） | `packages/ptc-runtime/*`；旧名 Code Mode | 模型不直接发工具调用，而是写一段程序，经生成的 SDK 间接调用工具。 | 04 |
| run_code | `tool-executor-ptc` 暴露的唯一工具 | PTC 模式下模型能看见的唯一工具：提交一段程序，由执行器代跑。 | 04 |
| 工具执行器（tool executor） | `core/tools/src/executor.ts`、`tool-executor-{native,ptc}` | 与工具注册表并列的 seam，负责"工具怎样呈现给模型 + 一批调用怎样执行"；native 与 PTC 是两种实现。 | 04 |
| 原生执行器（native） | `packages/core/tool-executor-native` | 把每个可见工具投影成一条 schema，整批交给共享的 `executeToolBatch`。 | 04 |
| PTC 执行器 | `packages/core/tool-executor-ptc` | 只暴露 `run_code`，其余工具渲染成 SDK 文本；程序内的嵌套调用仍走同一条守卫管线。 | 04 |
| 最近作用域选执行器 | `ToolsService` + executor 注册 | 同一 scope 只能挂一个执行器，作用域链上最近的那个生效；同层重复即失败。 | 04 |
| agent-tool-presentation（deprecated） | `packages/core/agent-tool-presentation` | 旧的 native/code/both 选择器行，已废弃，迁移为执行器行并删除 `config.mode`。 | 04 |
| 工具视图（ToolView） | 作用域链合并 + restriction 过滤的结果 | 一个作用域"看得见"的全部工具；展示面只从视图取数，不从原始注册表取数。 | 04 |
| restriction（工具掩码） | `ToolRestriction`（`contracts.ts`） | 挂在作用域上的 allow/deny 名单，只筛继承来的全局工具，不影响本作用域自己注册的工具。 | 04 |
| present 的两段式 | `packages/deliverables/tool-present` | 执行期先把声明暂存，`tools/result` 成功后才 append `deliverables/presented`。 | 04 |
| deliverables/presented | `tool-present` | 模型声明"这一轮交付了哪些文件"的持久事件；被包住的程序后来失败也不撤销已完成声明。 | 04 |
| 整包替换 Provider | SSH provider family（第 6 章） | 不换单个实现，而是一族 provider 一起替换；定义与消费者零改动。 | 05 |
| 描述字段（descriptor） | `ctx.ptcRuntime` 的 `language`/`isolation` | 定义层在抽象方法之外公开的只读描述字段，只供诊断与选择用；不构成新的 seam 形态，仍归方法调用式。 | 05 |
| SSH helper | `packages/ssh/ssh/src/helper.ts` | 远端跑起来的一个真实 dsh 进程，在那边装本地 fs/subprocess/sandbox provider。 | 06 |
| 双通道（管理 / 数据） | `SshConnection` + `SshRpcPeer` | 一条 master 承载管理 RPC，每条程序流另开独立 channel，互不抢窗口。 | 06 |
| ptcLaunch | `packages/ssh/ssh/src/index.ts` | 把远端可执行文件交给 PTC runtime 的入口（第 17 章用）。 | 06 |
| 意图瀑布 | `fs/write-intent` / `edit-intent` / `delete-intent` | 文件被改之前的可插拔拦截点，用于整份捕获原内容。 | 06 |
| TurnRecorder | `workspace-changes/src/recorder.ts` | 每轮开始拍基线树、改前捕获、turn-stopping 出 diff 的记录器。 | 06 |
| workspace/changes | `workspace-changes/src/types.ts` | 只带 turn 号的地址事件；摘要与 diff 留在 Host，客户端按 (sessionId, seq) 回取。 | 06 |
| SettingsForms | `packages/settings/settings` | 把活跃插件的 Config 投影成可编辑表单并带 revision 冲突检查的编辑面；不是 key-value 仓库，也不负责读配置。 | 07 |
| per-Agent scope（按 Agent 铸造的作用域） | `createScope(ctx, agent)` | 作用域生命周期绑定在一个 Agent 实例上，资源关在里面，Agent 一关即卸载。 | 09 |
| 任务板（task board） | `TeamTaskBoard` | 挂在树根 Session 日志上的任务 DAG，整块快照 + revision CAS。 | 11 |
| claim / reassign / blockedBy | `team/agent-team/src/task-board.ts` | 任务三要素：认领、改派（只有 Lead 能改派）、依赖边（必须无环）。 | 11 |
| TeamJournal | `team/agent-team/src/journal.ts` | 每个 Lead 一条串行事务队列，`team/task` 事件 append 到根会话并 flush 才算确认。 | 11 |
| 单槽排他注册表 | `ComputerUseRegistry` / `BrowserUseRegistry` | 全局独占能力只允许一个 provider 占位；注册返回 disposer，第二次注册（含同名）抛错。 | 13 |
| ProviderName 品牌 | `brand.ts` | 纯类型层的名字标记，运行时不做校验，只为诊断信息带上来源。 | 13 |
| SessionResources | `experimental/browser-use-runtime` | 以活着的 Agent 实例为键的浏览器资源归属表，先 abort 在途操作再 await 释放。 | 13 |
| 空定义 seam | `ctx.computerUse` / `ctx.browserUse` / `ctx.otel` | 定义层不声明任何抽象成员、不带配置与默认值；可有具体工厂或注册方法，语义全在消费者侧。 | 13 |
| revision（preset 的修订） | `AgentPresetRegistry` | preset 定义每变一次 revision +1；Agent 绑定其创建时的 revision，不随新 revision 重载。 | 15 |
| Generation / retain | `agent-preset-registry/src/index.ts` | `retain(id)` 物化并返回一代组合；旧代继续服务已存在的 Agent。 | 15 |
| 声明式 preset | profile YAML / bundle patch + `AgentPreset` | preset 是声明行而非目录扫描产物；`$DSH_HOME/.agent-presets` 目录已不再被读取。 | 15 |
| opt-in 实验 bundle | `experimental/{ralph,cua,session-titles}-bundle` | 默认组合不启用、需显式挂载的实验打包形态。 | 15 |
| TypertRemoteService | `typert/protocol/src/index.ts` | 服务基类：构造时注册 Cordis 服务并挂 `typertRemote` 绑定，让 `@Remote` 方法可被网关发现。 | 16 |
| Remote 导出名 | `@Remote('generation')` | `@Remote` 的重载：不传参时方法名即端点名，传字符串则换一个对外导出名（`getGeneration` → `officeToPdf/generation`）。 | 16 |
| officeToPdf / generation | `packages/document/office-to-pdf` | Office→PDF 有界转换服务；配置换代即整代缓存作废，浏览器按 generation 判断缓存。 | 16 |
| 有界准入 | `ConversionQueue` | 前台/后台两级名额 + 读者上限 + 内容寻址缓存，字节预留先于读取。 | 16 |
| WorkflowExecution | `workflow-ptc/src/runtime.ts` | 在 PTC Node 运行时里编译执行的 workflow 脚本实体（`vm.Context` + `vm.Script`）。 | 17 |
| 回执（receipt） | `session/query` 的返回值（`api.py` / `client.py`） | Python SDK 一次输入得到的凭证：prompt 的 messageId 或命令的 `CommandExecution`；一次 run = 一个回执 + 一条通知流。 | 17 |
| OTel 通道（reporter） | `ctx.otel` + `create*Reporter` | 消费者用工厂拿到的一条独享 exporter/queue/scope 的事件出站通道。 | 18 |
| 按条攒批 | `CountBatcher` / SDK `BatchLogRecordProcessor` | 普通事件的排队策略：攒够条数发一批，队列满丢新记录。 | 18 |
| 字节闸 | `SessionLogProcessor` | session 日志按字节攒批，单条超上限直接拒、不截断，且一次只有一个请求在飞。 | 18 |
| 单飞 | 串行请求约束 | 任意时刻只有一个传输请求在飞；前一个请求及其 `forceFlush` 完成后才发下一个。 | 18 |
| 全局面 | `packages/host/product-telemetry-otel` | 两个互不认识的宿主级消费者共用同一个 `ctx.otel` 的事实。 | 18 |
| 通道所有权（shutdown signal） | `shutdown(signal)` + fiber disposal | 谁建通道谁负责关：在消费者自己的 disposal 里关，带 deadline，超时丢弃队列。 | 18 |
| stopPending | `SessionLogProcessor` | deadline 到期时清空排队记录、阻止新请求；在途请求仍可自然结束。 | 18 |
