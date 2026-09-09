# Upstream Diff-Map Notes — 宏观扫描（macro-scan）

> 目标仓：`/home/chenyu/deepseek-harness`
> 基线：`47f943859b`（教程写作时 checkout，17 章大纲基准）
> 最新：`0a53fb55be`（已 fast-forward 到 `origin/master`，Merge PR #3334 "release/dsh-0.1.2-alpha.2"）
> 扫描方式：仅 `git ls-tree` / `git diff --stat` / `--dirstat` / `README.md` 首行，未逐文件读源码（宏观层）。
> 产出日期：2026-08-30

---

## 1. Scale（规模）

- Commit 数：`git rev-list --count 47f943859b..0a53fb55be` = **2167**
- 文件变更：`git diff --shortstat 47f943859b 0a53fb55be` =
  **7673 files changed, 420613 insertions(+), 141776 deletions(-)**
  （git 已提示 rename detection 因文件过多被跳过，建议 `diff.renameLimit >= 2530`；实际增删包含大量改名/重打包，非纯新增。）

> 注：42 万行 ≠ 全在为教学新增；其中 `.agents/notes/**` 约占 dirstat 前段 18%（Agent Note 归档/实现记录），`docs/subsystems/` 21%、`docs/` 7.6%、`apps/web/tests/` 14% 为测试快照与文档生成物。宏观扫描按「除测试/快照/AgentNote/文档生成物外的产品源码」来判新机制。

---

## 2. NEW top-level packages（`git ls-tree --name-only` 对比）

对比 `packages/`（base vs HEAD）：

- **`packages/experimental/`**（整组新增，base 无此组）——标记为「私下原型、不算进官方发布」（见组 README `kind: package-group`）。子包：
  - `agent-team` — "Run a small team of named agents in one session: durable messages between members and a shared task board"（单会话内多命名 agent 编队 + 成员间持久消息 + 共享任务板）。
  - `agent-team-profile` — "Private Agent Teams profile layer over dsh-base"（Agent Teams 的 profile 组合层）。
  - `agent-team-web-profile` — "Add the experimental Agent Teams panel to a source-checkout Web profile after the Host Team layer"（Web 侧 Team 面板 profile）。
  - `agent-team-web-profile` / `client-ui-agent-team` / `tool-agent-team` — Agent Team 的 Web UI / 模型工具组件。
  - `inspector` — "Experimental Chrome DevTools inspection for Host and browser Client Cordis runtimes"（CDP 调试器：Console/Sources/Network/Elements）。
  - `webworker-runtime` — "Browser-worker harness hosting"（浏览器 worker 运行时）。
  - `webworker-packer` — "Browser-worker VFS image packaging"（worker VFS 打包）。

- **`packages/webhook/`**（整组新增）：
  - `webhook` — "Webhook rule runtime for maintainers registering trusted external-event policies that create Workspace Sessions"（webhook 规则运行时：外部受信事件 → 新建 Workspace Session）。
  - `webhook-github` — "Signed GitHub webhook adapter"（GitHub 签名 webhook 适配器）。

其余 base↔HEAD 同名组内的**新增子包**（同样值得列，因为也是新机制锚点）：

- `packages/llm/deepseek-llm-api-extensions`（新增）— "Official DeepSeek request-extension registry for provider plugins contributing lifecycle-owned top-level API fields"（DeepSeek 请求扩展注册表）。
- `packages/llm/plugin-package-inventory-deepseek`（新增）— plugin 包清单上报（DeepSeek 侧）。
- `packages/client/store`（新增）— Web Client 状态 store（新基础层）。
- `packages/client/ui-approval` / `ui-chat` / `ui-brand-official` / `ui-reference` / `ui-renderer` / `ui-schedule` / `ui-session`（新增，一组 Web UI 主题/会话/审批/引用/渲染组件）。注意 base 有 `ui-slots` 但 HEAD 新增 `ui-renderer`（Slots 系统拆分出 renderer）。
- `packages/client` 去掉 `web-react`（base 有，HEAD 无，重命名/合并进 `web` 或 `ui-renderer`）。

`packages/api` head 含 `session-controller`（见 dirstat 高频）——属 client/web 架构重组的产物。

---

## 3. NEW docs/subsystems entries（未纳入 17 章对应模块的）

base `docs/subsystems/` vs HEAD 新增（只列 `.md`，忽略 `.i18n.yaml`/`.zh.md`；对比后新增的纯文件名）：

| 新增子系统 | 一句话 | 是否已入大纲 |
|---|---|---|
| `agent-team.md` | 实验性 Team 域的持久类型（TeamId/TeamTaskId/TeamMessageId、mailbox、task board） | **未纳入**（无对应章） |
| `conversation.md` | Conversation 装配层：Client 事件窗 → 浏览器视图的 target-neutral 装配 | **未纳入**（Web 前端层，超 ch13/17 范围） |
| `slots.md` | Web Client 的 typed React 组合系统（`ctx.slots.register()`、SlotMap、ui-renderer） | **未纳入**（首次成为正式子系统文档） |
| `todo.md` | durable todo 词汇表（`@deepseek-ai/dsh-tool-todo` 替换整会话列表） | **未纳入**（todo 未独立成章，仅在 ch14 附近提及） |
| `user-questions.md` | 已出现在 base？→ base 无此文件，HEAD 有（用户提问子系统） | **未纳入** |
| `web-client.md` | Web Client 整体架构（4 基础：Client Modules / API Gateway / Slots / Conversation） | **未纳入** |
| `web-server.md` | 已在 base? → base 无，HEAD 有（Web 服务端子系统） | **未纳入** |
| `webhook.md` | Webhook 运行时（受信外部投递 → 普通 root Session；WebhookRuleId 等） | **未纳入** |

（另有 `user-questions`、`web-server`、`web-client`、`slots`、`conversation`、`todo`、`agent-team`、`webhook` 为本次新增；`feedback` 在 base 已存在故不列。)

> 精确判定用 `git ls-tree --name-only <ref>:docs/subsystems` 对比即可复现（本扫描 base 列表 62 项、HEAD 列表 70 项，净增 8 个主题：agent-team, conversation, slots, todo, user-questions, web-client, web-server, webhook）。

---

## 4. NEW root/new docs & 大型新目录

- **`docs/deepseek-llm-api-wire-extensions.md`**（+`.zh.md` + `.i18n.yaml`；base 无）——官方 DeepSeek LLM API 的 HTTP 头/JSON 扩展字段规范：`x-deepseek-harness-session-id` 等 kebab-case 头、`dsh_*` snake_case body 扩展（`dsh_plugin_packages`、`dsh_session_log`）、`dsh_` 命名空间与版本化、`DeepSeekLlmApiExtensionRegistry` 每扩展名保留一个 provider。对应包 `packages/llm/deepseek-llm-api-extensions` + `dsh-llm-deepseek` 注入。
- **`SAFETY.md` / `SAFETY.zh.md` / `SAFETY.i18n.yaml`**（base 无，root 新增）——实验性状态 + 安全声明。
- **`BRAND_GUIDELINES.md` / `.zh.md` / `.i18n.yaml`**（base 无，root 新增）——品牌资产使用规范（"DSH" 缩写命名等）。
- **`docs/user/`**（base 已存在 `develop/`、`guide/`、`index`）：HEAD 大幅扩充，dirstat 显示 `docs/user/guide/` 2.4%、`docs/user/develop/basic/` 1.4%、`develop/practice/` 1.2%、`develop/framework/` 1.0% —— 面向**最终用户**的分级教程（basic/practice/framework 三阶）。**对教学仓库是重要参照物**但不直接是「上游源码新机制」。
- **`docs/cookbook/`**（base 已存在，1 篇 `adding-a-conversation-node`；HEAD 扩展到约 12 篇）：`adding-a-package`、`adding-a-remote-api`、`adding-a-settings-card`、`adding-a-tool`、`adding-a-vendored-package`、`adding-an-llm-adapter`、`extension-cookbook` 等 —— 即「如何新增 X」的扩展点总集（chapter 化候选之一）。
- **`python/sdk` + `python/sdk-runtime`**：base 已有（非新增目录），但 dirstat `python/sdk-runtime/` 0.9%、`python/sdk/` 0.8% + `python/` 0.8%，说明**内容大改**（已在 ch17 覆盖 `python/sdk` + `python/sdk-runtime`，故不算新章，但需「ch17 是否过时」复核）。
- **`apps/cli` + `apps/web`**：base 已有目录，但 dirstat 高频 — `apps/web/tests/` 14%、`apps/cli/` 0.8%、`apps/web/src/` 0.5% —— 大改体现在测试/快照与 Web 架构重构（Slots/Conversation 新层），而非纯新增入口。ch17 已覆盖 apps 入口，需复核是否要吸收新的 Web 架构章节。

---

## 5. HIGH-CHURN existing areas → 回映 17 章

`git diff --dirstat=files,0 47f943859b..0a53fb55be`（仅 `packages/`，排序取 top，去测试/快照）：

| 变更区 | churn | 回映 17 章 |
|---|---|---|
| `packages/client/ui-*`（primitive/conversation/chat/tool/trajectory/settings…）| ~5% 合计 | **ch13（web）** 之外的**新前端层**：Slots、Conversation、ui-renderer、ui-chat —— 原大纲 ch13 只覆盖 `packages/web`（search/fetch），未覆盖 Web Client UI |
| `packages/api/session-controller/`（src+tests）| ~1.2% | 跨 ch16/ch17（api/sdk）但指向新的 **session-controller** BFF 层 |
| `packages/host/apiproxy/`（src+tests）| ~1.2% | ch16（api）——apiproxy 大改 |
| `packages/session/session-persistence-sqlite/`（sql 资源）| ~1.6% | **ch03**（session 持久化）——SQLite 后端 schema 演进（SCHEMA_VERSION 单调整） |
| `packages/core/agent-loop/tests + src`| ~0.4-0.5% | **ch02**（agent-loop）——核心闭环有实质改动 |
| `packages/core/tools/`| ~0.2% | **ch04**（tools 管线） |
| `packages/llm/llm*` + `token-meter`| ~1.6% | **ch07**（LLM 适配）——新增 `deepseek-llm-api-extensions`、`plugin-package-inventory-deepseek` |
| `packages/subagent/subagent/`| ~0.5% | **ch11**（subagent 委派） |
| `packages/preset/agent-presets/`| ~0.4% | **ch15**（preset/bundle/profile） |
| `packages/experimental/agent-team/src`| ~0.3% | 无对应章（新） |
| `packages/test-support/session-snapshot/`| ~0.4% | 支撑工具，非教学机制 |

**结论**：churn 最集中的「现有章源码」为 ch03（session sqlite）、ch07（llm 扩展注册表）、ch02（agent-loop）、ch16/ch17（api/apiproxy/session-controller、python sdk）。最需要**新增章**的区域是：Web Client 架构（Slots/Conversation/renderer）、Agent Teams、webhook、DeepSeek API wire extensions——这些在 17 章里**没有对应源码模块**。

---

## 6. Candidate chapter list（分析师视角）

审查标准：**(a) 基线后真正新增**，**(b) 架构上有意义（机制/扩展点，非 UI 微调）**，**(c) 不重复现有章**。

1. **Web Client 架构（Slots + Conversation 装配）**
   - 机制：`ctx.slots.register()` 类型化 React 组合系统 + `ConversationNodeAssembler`（事件窗 → 视图装配）。
   - 源码：`docs/subsystems/slots.md`、`docs/subsystems/conversation.md`、`docs/subsystems/web-client.md`；`packages/client/ui-slots`、`ui-renderer`、`ui-conversation`、`ui-chat`。
   - 理由：这是 base 后**成体系**的客户端插件化扩展点（现有 ch13 只覆盖 `packages/web` 检索，未覆盖浏览器侧 UI 组合），有完整 subsystem 文档既成契约。

2. **Agent Teams（实验性多 agent 编队）**
   - 机制：单会话内命名 agent 编队 + 成员间持久消息（mailbox）+ 共享 task board + TeamId/TeamTaskId/TeamMessageId 类型。
   - 源码：`docs/subsystems/agent-team.md`；`packages/experimental/agent-team/src/types.ts`（+ agent-team-profile / client-ui-agent-team / tool-agent-team）。
   - 理由：基线上无任何「多 agent 同一会话协调」机制，属新扩展点域；虽标 experimental，但类型/事件有官方 subsystem 文档。

3. **Webhook 运行时（外部事件 → Session）**
   - 机制：`ctx.webhookRuntime` + provider 适配器认证/JSON intake + 受信规则条件/外呼 + Workspace Session 创建（fire-and-forget，无投递/完成状态）。
   - 源码：`docs/subsystems/webhook.md`；`packages/webhook/webhook`、`webhook-github`；决策 `.agents/notes/implemented/feature/2026-08-22-fire-and-forget-webhook-sessions.md`。
   - 理由：全新 ingress 面（外→内触发 agent），`WebhookEventMap` 是 merge-extensible 扩展点，可与 ch13（web fetch 能力）形成对照。

4. **DeepSeek LLM API wire extensions（官方请求扩展注册表）**
   - 机制：`dsh_*` body 扩展字段 + `x-deepseek-harness-*` 头的带版本扩展协议 + `DeepSeekLlmApiExtensionRegistry`（每扩展名保留一个 provider，碰撞即 fail-loud）。
   - 源码：`docs/deepseek-llm-api-wire-extensions.md`；`packages/llm/deepseek-llm-api-extensions`；注入点 `packages/llm/llm-deepseek`。
   - 理由：ch07 只覆盖 llm adapter/stream，未覆盖「模型请求侧边带扩展」这一 provider 面向的 seam；有 registry 守卫与 fail-loud 语义，教学价值高。

5. **（备选）cookbook「添加一个 X」扩展点总集 / session-controller BFF 同构层**
   - 机制：`docs/cookbook/add-*`（add-a-package / add-a-tool / add-an-llm-adapter / extension-cookbook）构成一次完整的「扩展点耕读」；或 `packages/api/session-controller` 作为 host↔client 同构 session 控制器 BFF。
   - 源码：`docs/cookbook/`、`packages/api/session-controller`、`packages/host/apiproxy`。
   - 理由：cookbook 是「如何新增机制」的手册，若教学仓想加「实战/扩展章」可选；session-controller 是 api 组大改后的新结构面，ch16 可能需扩展。

> **优先级建议**（按「机制新颖 + 架构意义 + 不与 17 章重叠」三者加权）：1 > 2 > 4 > 3 > 5。

---

## 7. 附：复核提醒（不属本扫描，供 Orchestrator 派发下轮）

- ch02（agent-loop）、ch03（session SQLite schema）、ch07（llm 扩展）、ch16（apiproxy/session-controller）、ch17（python sdk）对应的源码有中等 churn，需后续 Explorer 定点核实「17 章正文是否仍与 HEAD 一致」。
- `docs/user/`（basic/practice/framework 三阶教程）是上游自行提供的「用户学习路径」，与 learn-deepseek-harness 定位高度重合，值得单独成本收益评估（是复用还是差异化）。