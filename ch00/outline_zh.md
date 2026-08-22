# 《DeepSeek Harness（dsh）》源码学习大纲

> 递进式章节大纲：每章在前一章基础上新增一个机制，末章整合所有机制为端到端流程。
> 术语在章节内首次出现处定义，全文末尾附「术语命名表」供跨章一致性参考。
> 正文简体中文，代码/符号英文，配图 Mermaid。
> 教学代码：`chNN/code/` 按章增量累积（每章一个自包含目录）。

## 0. 总览

- **世界观**：一切皆插件；agent = model + harness；注册即效应；seam 三角色。
- **阅读路径**：先读 vendor/Cordis 原语 → 最小 agent-loop 闭环 → 逐机制加厚。
- **读者起点**：无需先读完整仓，跟着大纲逐章建立心智模型。

## 1. Cordis 容器内核（一切皆插件）

- **主题**：插件容器如何装配（上下文/服务/事件的最小核心）。
- **新增机制**：`Context` 服务容器 + `Service` + plugin 注册 + `inject` 依赖检查 + `Fiber`/`effect` 可逆注册 + `on`/`emit`/`serial`/`waterfall` 事件派发。
- **对应源码模块**：`@cordisjs/core`（context/service/registry/events/fiber/reflect）。
- **本章回答的问题**：什么是上下文、服务、可逆注册、事件派发。
- **本章新增教学代码**（`ch01/code/`，自包含、仅用标准库，`python ch01/code/hello.py` 可运行）：
  - `cordis.py`：教学版 Cordis 内核。
  - `hello.py`：§2 最小可运行例。
  - `bad_example.py`：§1 反面示例。

## 2. 最小 agent-loop 闭环（一条消息进，一条回复出）

- **主题**：一条用户消息如何变成一条回复——最小可运行闭环。
- **新增机制**：`Session`（内存 append-only）+ `SystemPromptService`（+`render_prompt`）+ `AgentLoop`（kick/turn/step）。
- **对应源码模块**：`@cordisjs/agent-loop` + `@cordisjs/core` Session + `@cordisjs/system-prompt` + llm stub。
- **本章回答的问题**：一个 agent 如何被「拼装」出来。
- **本章新增教学代码**（`ch02/code/`，增量复用 ch01 的 `cordis.py`，`python ch02/code/main.py` 可运行）：
  - `agent_loop.py`：教学版 agent-loop。
  - `main.py`：可运行装配入口。

## 3. session 事件流与持久化投影

- **主题**：会话如何被记录、落盘、重放。
- **新增机制**：`SessionEvent` 追加日志 + `sessionPersistence` seam（JSONL/SQLite）+ `sessionProjection` 冷读与缓存。
- **对应源码模块**：`packages/session/session-persistence`、`session-persistence-jsonl`、`session-persistence-sqlite`、`session-projection`、`session-projection-cache`、`session-title`、`session-telemetry`。
- **本章新增教学代码**（`ch03/code/`，增量复用 ch01 的 `cordis.py` 与 ch02 的 `agent_loop.py`，`python ch03/code/main.py` 可运行）：
  - `session_persistence.py`：教学版 session 持久化与投影（seam 双契约 / write-behind / 协调器 / JSONL 与 SQLite 双后端 / 投影注册表与缓存 / 标题与遥测）。
  - `main.py`：可运行装配入口（8 段闭环）。
  - `bad_example.py`：§1 反面示例。

## 4. tools 注册与执行管线

- **主题**：模型如何获得并调用工具。
- **新增机制**：`ctx.tools` 作用域注册 + 工具 schema 注入 system-prompt + 守卫执行管线（pre-execute/execute/post-execute）。
- **对应源码模块**：`packages/core/tools`、`packages/guard`。

## 5. capability seam 三角色

- **主题**：一个能力如何被「定义、实现、消费」三分解耦。
- **新增机制**：Service Definition / Service Provider / Consumer 三角色 + 完整 seam 契约（以 shell 为例）。
- **对应源码模块**：`packages/shell/shell`、`shell/bash-local`、`shell/tool-bash`；`docs/capability-seams.md`。

## 6. subprocess / shell / fs 执行世界

- **主题**：让 agent 真正能在机器上做事的执行面。
- **新增机制**：`ctx.subprocess` 进程树 + `ctx.shell` + `ctx.fs` + `ctx.sandbox` 隔离，provider 整体迁移。
- **对应源码模块**：`packages/subprocess`、`packages/shell`、`packages/fs`、`packages/sandbox`、`native/landlock-run`。

## 7. LLM 适配与流式

- **主题**：模型层如何被替换与如何流式输出。
- **新增机制**：`ctx.llm` 适配器 seam + `assistant/chunk` 流式 + `credentials`/`settings` seam。
- **对应源码模块**：`packages/llm/llm`、`llm-deepseek`、`llm-pi-ai`、`token-meter`；`packages/credentials`、`packages/settings`。

## 8. system-prompt 组装与 context

- **主题**：发给模型的那段「提示词」从哪来。
- **新增机制**：prompt section 注册/合并 + workspace 指令 + 时间上下文 + `session-reference`。
- **对应源码模块**：`packages/core/system-prompt`、`packages/context`、`packages/workspace`。

## 9. scope 作用域注册

- **主题**：同一进程里不同 agent 如何隔离各自能力。
- **新增机制**：per-agent scoped registration + shadowing + restriction + lineage。
- **对应源码模块**：`packages/core/scope`。

## 10. compaction 与 token 压力

- **主题**：上下文太长怎么办。
- **新增机制**：`compaction` seam + tool-result-pruner + token-meter 触发。
- **对应源码模块**：`packages/compaction`。

## 11. subagent 委派

- **主题**：把任务委派给子代理并行/隔离执行。
- **新增机制**：`ctx.subagents` seam + 多 transport provider（spawn/fork/acp/codex/claude-code/dsh-sdk）+ `tool-subagent`/`tool-subagent-control`。
- **对应源码模块**：`packages/subagent`。

## 12. skill 加载

- **主题**：可插拔的专业能力包。
- **新增机制**：`ctx.skills` provider 注册表 + skill-badge/filesystem + `tool-skill` 目录与加载。
- **对应源码模块**：`packages/skill`。

## 13. web / lsp 能力

- **主题**：检索与代码语义两大外部能力。
- **新增机制**：`ctx.web`（search/fetch）+ `ctx.lsp`（normalized 四操作查询）。
- **对应源码模块**：`packages/web`、`packages/lsp`。

## 14. interaction / permission / goal / plan

- **主题**：人类如何介入、授权与协同规划。
- **新增机制**：approval seam + permission-presets + commands + goal domain + plan-mode。
- **对应源码模块**：`packages/interaction`、`packages/goal`、`packages/plan`。

## 15. preset / bundle / profile 组合

- **主题**：把散件组装成可复用的产品形态。
- **新增机制**：agent-presets 每会话组合 + bundle patch 层 + profile 栈。
- **对应源码模块**：`packages/preset`、`packages/bundle`、`packages/boot`。

## 16. typert / api / sdk

- **主题**：跨进程的类型化 RPC 运行时。
- **新增机制**：typert 类型图 + RPC gateway + JSON-RPC sdk + ACP。
- **对应源码模块**：`packages/typert`、`packages/api`、`packages/sdk`、`packages/acp`。

## 17. workflow / Ralph / python + apps-cli-web 端到端

- **主题**：全部机制整合为完整启动链路与端到端流程。
- **新增机制**：workflow 引擎 + Ralph 循环 + Python SDK/runtime + apps 入口（host/client）整合。
- **对应源码模块**：`packages/workflow`、`python/sdk`、`python/sdk-runtime`、`apps/cli`、`apps/web`、`packages/host`、`packages/client`。

---

## 递进性自检清单

- [x] 第 1 章建立容器内核（一切皆插件，hello.py 闭环）。
- [x] 第 2 章建立最小可运行核心（agent-loop 闭环，无工具无持久化）。
- [x] 第 3 章起每章只新增一个机制簇。
- [x] 末章（17）整合所有机制为端到端启动链路。
- [x] 每章写明：章号、主题、新增机制、源码模块。
