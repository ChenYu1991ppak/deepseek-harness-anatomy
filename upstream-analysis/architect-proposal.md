# Architect 提案：上游升级（47f943859b → 0a53fb55be）章节决策文档

> 产出角色：Architect（Source Decomposition Expert Team）
> 输入：`doc/upstream-analysis/notes/diff-map-notes.md`（宏观扫描）+ `notes/candidates-notes.md`（深潜 3 候选）+ `ch00/outline_zh.md`（既有 17 章大纲，权威）
> 产出：新章候选提案 + 既有章修订清单 + 风险边界（**不写任何章正文**）
> 产出日期：2026-08-30
> 硬约束：零源码阅读；材料缺失处标「待 Explorer 核实」，不猜测。

---

## 0. 两份 Explorer 素材的裁定前提（先对齐口径）

宏观扫描给了 **5 个候选**；深潜只覆盖其中 **3 个（A/B/C）**，多出的 2 个（Web Client 架构、cookbook/session-controller 备选）**深潜未勘探、无符号级证据**。因此本提案的裁定分三档：

- **有符号级证据、可直接裁**：A=Agent Teams、B=Webhook、C=DeepSeek wire extensions。
- **有宏观证据、无符号级证据、需条件裁**：Web Client 架构（宏观列为优先级第 1）。
- **宏观列为备选、建议不成章或并入**：cookbook 扩展点总集 / session-controller BFF。

### 裁定汇总表（两份意见对账）

| 宏观 5 候选 | 深潜 3 候选 | 二者关系 | 本提案裁定 |
|---|---|---|---|
| 1. Web Client 架构（Slots+Conversation） | 未覆盖 | 宏观列为 #1，深潜未勘探 | **条件成章**（降级为「待核实的第 3 新章」） |
| 2. Agent Teams | A = NEW | 一致（NEW） | **成章**（带实验性保留） |
| 3. Webhook | B = NEW | 一致（NEW） | **成章**（优先级最前） |
| 4. DeepSeek LLM wire extensions | C = merge into ch7 | 宏观列候选、深潜裁并列 ch7；二者不冲突（宏观本列为最弱 #4） | **并入 ch7**（采纳深潜裁定） |
| 5. cookbook / session-controller（备选） | 未覆盖 | 宏观备选 | 均**不成章**（见下） |

**一致点**：Agent Teams、Webhook 双方都判「机制新颖、成独立新章」。**分歧点**：(a) 优先级——宏观 #1=Web Client，本提案改为 Webhook > Agent Teams > Web Client（理由见 §1.4）；(b) DeepSeek wire extensions 成章与否——宏观仅列为最弱候选，深潜明确「体量不足（核心注册表 ~130 行）、应并 ch7」，本提案采深潜。

---

## 1. 新章候选（写出具体 outline 条目）

### 1.1 新章候选 ①：Webhook（外部事件 → Session 的 fire-and-forget 规则运行时）

- **主题**：外部 HTTP 事件如何变成一次 agent 会话（外→内触发 ingress 面）。
- **新增机制**：`ctx.webhookRuntime` 规则运行时 + `VerifiedWebhookDelivery`（提供方中立、`deepFreeze` 快照）+ `WebhookEventMap` 声明合并扩展点 + `dispatch` fire-and-forget（规则按 `kind` 匹配后独立并发、错误按规则隔离、不冒泡整批）+ `createWebhookSession`（preset→workspace→agent→followup 带 `source.kind:'webhook'` provenance，失败分层回滚）+ 提供方适配器（`webhook-github`：验签 / `readBoundedUtf8Body` 有界读 / `202` 立即返回 / `503` runtime 不可用）。
- **对应源码模块**：`packages/webhook/webhook`、`packages/webhook/webhook-github`；`docs/subsystems/webhook.md`；决策 `.agents/notes/implemented/feature/2026-08-22-fire-and-forget-webhook-sessions.md`。
- **延伸基础章**：ch5（capability seam 三角色：runtime=定义方 / webhook-github=提供方实现 / rule=消费方）；复用 ch15（`agentPreset`/`permissionPreset`）、ch6（`workspaceRegistry`/cwd）、ch7（创建后交回普通 Session 生命周期）。
- **本章回答的问题**：① 一次外部 HTTP 事件如何被鉴权、规范化、投递给规则并最终创建 agent 会话；② 三角色（鉴权适配器 / 信任规则 / runtime）如何分工；③ fire-and-forget 分发为何放弃投递/完成状态、如何处置重复交付（不去重、可能重复 Session）。
- **新增教学代码建议**（增量复用 ch5 的 seam 三角色 + ch15/ch6 的 preset/workspace）：
  - `webhook_runtime.py`：`register`/`disposeRegistration`/`dispatch`（快照 + fork 调度）。
  - `delivery.py`：`Delivery`（frozen dataclass）+ `SessionRequest`（绝对路径 / preset / model 快照）。
  - `webhook_github.py`：验签 handler + 有界读 body + `202`/`503` 语义。
  - `create_session_from_delivery.py`：preset→workspace→agent→followup 链 + 回滚。
- **预计篇幅**：~500–600 行（3 个机制簇 × 100 行 + 章首尾 ~80 行）。
- **结论**：**值得成章** —— 全新 ingress 面，seam 三角色完整、体量适配独立成章、正常发布（无 experimental 负担）、后端域映射 Python 顺滑。**优先级最前**。

### 1.2 新章候选 ②：Agent Teams（单会话内多命名 agent 的持久协作域）

- **主题**：一个会话里多个命名 agent 如何共享身份、消息与任务并持久协作。
- **新增机制**：隐式根团队（`TeamId` = root `SessionId` 品牌 id，无需显式建队）+ `TeamRoster`（lead/teammate 名册，`provisioning→active|failed` 一次性生命周期）+ `TeamMailbox` 持久邮箱（`team/message/queued` → `delivered` 双回执、`quiet`/`wakeup` 投递）+ `TeamTaskBoard` 共享任务 DAG（`revision` CAS 乐观锁 + `blockedBy` 无环校验 + `writeScopes` 提示性重叠告警）+ `teamProjectionDefinition` 投影折叠（Lead 日志即团队状态，普通 fork 继承事件绝不进新 root）+ team-scoped 模型工具（`spawn_teammate`/`send_message`/`followup_task`/`team_task_*`，装在 exact agent scope）+ `agent-team-profile` patch 叠层（`one-shot` vs `continuable` 工具行替换）。
- **对应源码模块**：`packages/experimental/agent-team`、`experimental/agent-team-profile`、`experimental/tool-agent-team`；`docs/subsystems/agent-team.md`。
- **延伸基础章**：ch11（subagent transport：`ctx.subagents.startContinuable`/`followup` 是 Team 的传输底座）+ ch3（`sessionProjections` 投影，折叠 `TeamState`）+ ch9（per-agent scope 注册，Team 工具装在 exact scope）+ ch15（`agent-team-profile` 用 `cordis.patch.yml` 叠层）。
- **本章回答的问题**：① 多个 agent 如何在单会话内获得持久身份、消息与任务并协作；② 为何「Lead 日志 → 投影折叠」能避免独立团队存储（事件流即状态）；③ CAS 任务图如何保证无环与并发一致（`revision` 校验、`blockedBy`、tombstone）。
- **新增教学代码建议**（增量复用 ch11 的 `subagents`、ch3 的投影、ch9 的 scope）：
  - `team_service.py`：门面（`inject = ['agents','sessions','sessionPersistence','sessionProjections','subagents']`）。
  - `roster.py`：`tryMembership`（lead/teammate/非成员核心分支）、`spawn`/`spawnAdmitted`（provisioning 冲突 `TEAM_PROVISIONING_CONFLICT`）、`interrupt`、`stopTeammates`。
  - `mailbox.py`：`send`/`observeSessionEvent`/`dispatchOnce`（`queued`/`delivered` 两张表、`quiet` vs `wakeup`）、`markDelivered`。
  - `task_board.py`：`update`（switch 覆盖 8 种 CAS action）、`taskView`、`assertTaskGraphCandidate`（缺依赖/自环/成环）。
  - `team_projection.py`：`teamProjectionDefinition`（`key:'agentTeam'`, `stateVersion:2`, `apply` 逐事件校验）。
  - `team_tools.py`：scoped 注册 + `POLICY`。
- **预计篇幅**：~600 行（机制密度高：投影 + roster/mailbox/task 三原语 + 工具 + patch；可能触及上界，写作前按「投影 vs 三原语」可二分原则评估是否拆两章）。
- **结论**：**值得成章（有保留）** —— 机制自洽、多条 Python 重构支点、与 ch11 一次性委派不同质；但 **experimental + private（发布产物排除）**，是否成章取决于教程是否纳入「实验性扩展」卷，成章须在开头标注实验性状态（呼应上游 `SAFETY.md`）。

### 1.3 新章候选 ③：Web Client 架构（Slots 类型化组合 + Conversation 装配，条件成章）

- **主题**：浏览器侧客户端 UI 如何成为插件化扩展点，而非定死的组件树。
- **新增机制**：`ctx.slots.register()` 类型化 React 组合系统 + `SlotMap` + `ui-renderer`（原 `ui-slots` 拆分出 renderer）+ `ConversationNodeAssembler`（Client 事件窗 → 浏览器视图的 target-neutral 装配）。
- **对应源码模块**：`packages/client/ui-slots`、`ui-renderer`、`ui-conversation`、`ui-chat`、`client/store`；`docs/subsystems/slots.md`、`conversation.md`、`web-client.md`。
- **延伸基础章**：ch13（web 能力，但 Web Client 是新前端层、超 ch13 范围）；可接 ch17（`apps/web`）。
- **本章回答的问题**：① 浏览器客户端 UI 如何成为插件扩展点（Slots 组合系统）；② 事件窗如何 target-neutral 地装配成视图（Conversation 装配层）。
- **新增教学代码建议**：**待 Explorer 核实** —— React 组合系统难以用纯 Python 等价重构；倾向以「注册表 + SlotMap 抽象」做 Python 教学近似（`slot_registry.py` + `conversation_assembler.py`），具体符号与可运行性待定点勘探。
- **预计篇幅**：**待 Explorer 核实**（slots / conversation / renderer 三簇，估 500+ 行）。
- **结论**：**条件成章 / 待核实** —— 架构意义高（成体系客户端扩展点、官方 subsystem 文档既成契约），但 (a) 前端 React 域与「Python 重构教学」模型错配；(b) 深潜未覆盖、缺符号级证据。建议 Explorer 补一轮定点勘探后再定；短期先降级为 **ch13 或 ch17 的修订项**。

### 1.4 优先级建议（含理由）

**推荐成章顺序：Webhook ＞ Agent Teams ＞ Web Client**（与宏观「Web Client #1」不同，理由如下）：

1. **Webhook**：正常发布、体量适配（~840 行）、seam 三角色完整、后端域 Python 重建顺滑、无实验性风险、与 17 章零重叠——「机制新颖 + 架构意义 + 教学可落地」三者得分最均衡，故列最前。
2. **Agent Teams**：机制自洽度与教学价值最高，但有 experimental/private 负担（发布产物排除、未来可能 churn），需前置「实验性扩展卷」定位决策，故列次席。
3. **Web Client**：架构意义高但前端 React 域与 Python 教学模型错配、且无符号级证据，需先核实再落地，故列末席（条件成章）。

> 章号与插入位置（建议接 ch15 之后、或作为 18/19/20 追加卷）不在此提案定死，交由大纲执笔时按「延伸基础章 < 本章号」递进约束统一排定。

### 1.5 明确不成章 / 并入的候选

- **DeepSeek LLM wire extensions** → **并入 ch7**（采纳深潜 C 裁定）：非新 adapter/新业务域，是 `llm-deepseek` adapter 体内「请求正文/头如何被插件贡献字段扩展」的一个 seam（`register→prepare→accept` 三段事务），核心注册表 ~130 行、无独立业务域，作为 ch7「DeepSeek 官方适配器的扩展点」小节最合适。
- **cookbook「添加一个 X」扩展点总集** → **不单独成章**：是「如何新增机制」的手册而非机制；且与 `docs/user/` 三阶教程、教学仓自身定位重叠（见 §3 风险）。
- **session-controller BFF 同构层** → **并入 ch16**（非新章）：是 `packages/api` 组重构的新结构面，符号级细节缺失，作为 ch16「apiproxy/session-controller」修订项纳入（见 §2）。

---

## 2. 需修订既有章清单（按章号）

> churn 来自宏观扫描 `--dirstat` top（去测试/快照/文档生成物）。**只列清单，不重写正文。**

| 章号 | 因上游变化而面临的问题 | 建议 |
|---|---|---|
| **ch02**（agent-loop 闭环） | 核心闭环 src+tests 有实质改动（~0.4–0.5% churn），README/装配细节可能与 HEAD 不一致 | **re-verify**（定点核实 agent-loop 装配链是否仍匹配） |
| **ch03**（session 持久化投影） | `session-persistence-sqlite` SQL 资源改动 ~1.6%，SQLite 后端 schema 演进（`SCHEMA_VERSION` 单调整） | **re-verify**（核对 schema 版本号与迁移语义） |
| **ch04**（tools 管线） | `packages/core/tools` ~0.2% churn | **re-verify**（轻量，确认守卫管线表述未过时） |
| **ch07**（LLM 适配与流式） | 新增 `deepseek-llm-api-extensions`、`plugin-package-inventory-deepseek`（llm* + token-meter 合计 ~1.6% churn） | **revise**：新增「请求扩展注册表」小节，吸收候选 C（register→prepare→accept + `dsh_*` 命名空间 + 2 贡献者） |
| **ch11**（subagent 委派） | `subagent/` ~0.5% churn；且被 Agent Teams 复用为 transport 底座 | **re-verify**；若 Agent Teams 成章，加一句「continuable/followup 是 Teams 底座」的衔接预告 |
| **ch13**（web/lsp 能力） | 原覆盖 `packages/web`（search/fetch），未覆盖 Web Client UI（Slots/Conversation/renderer） | **re-verify** 范围；短期承接 Web Client 修订项（若后者未成章） |
| **ch15**（preset/bundle/profile） | `agent-presets/` ~0.4% churn | **re-verify**（轻量） |
| **ch16**（typert/api/sdk） | `packages/api/session-controller/`（~1.2%）+ `packages/host/apiproxy/`（~1.2%）大改，新增 BFF 层 | **revise / re-verify**：吸收 session-controller BFF 同构层（待 Explorer 核符号级细节） |
| **ch17**（workflow/python/apps 端到端） | `python/sdk` + `python/sdk-runtime` 内容大改（dirstat 合计 ~1.7%）；`apps/web` 经 Slots/Conversation 架构重构 | **re-verify**：核对 python sdk 叙述是否仍与 HEAD 一致；Web 架构重构部分关联 ch13/Web Client 决策 |

---

## 3. 风险与边界

### 3.1 实验性 / 私有包

- `packages/experimental/agent-team`（+ 两个 profile）标 **experimental + private，正式发布产物排除**（组 README `kind: package-group` 即声明「私下原型、不算进官方发布」）。若成章：须开头标注实验性状态、上游引用 `SAFETY.md`，并接受「未来 churn / 可能被移除」的维护风险。
- `packages/experimental/inspector`、`webworker-runtime`、`webworker-packer` 同为实验组，本提案不纳入（无 subsystem 文档、且与教学主线弱相关）。

### 3.2 docs/user/ 三阶教程与教学仓定位重叠

- 上游 `docs/user/` 大幅扩充出 **basic / practice / framework 三阶**用户教程（dirstat 合计 ~4.6%），与 learn-deepseek-harness「递进式源码学习」定位高度重合——这是**定位决策**（复用 vs 差异化），非新机制，需 Orchestrator 单独做成本收益评估，本提案不将其列为新章。
- 同理 `docs/cookbook/`（1 篇 → ~12 篇「如何新增 X」）与三阶教程同属「用户手册层」，与「源码机制教学」定位区分，避免照搬。

### 3.3 必须标「待 Explorer 核实」的材料缺口（写作前需再勘探）

1. **Web Client 架构章**：深潜未覆盖，只有宏观子系统文档一句话，**无符号级证据**（Slots/Conversation/renderer 的入口与 API 待核）——未核实前不得成章落笔，短期降级为 ch13/ch17 修订项。
2. **session-controller BFF**：仅 dirstat 高频提示，无符号级描述——若作为 ch16 修订项，需 Explorer 补一轮「session-controller 结构与 host↔client 同构语义」定点勘探。
3. **各 churn 章的正文一致性**：宏观只给了 churn 量级，**未逐章核实正文是否仍与 HEAD 一致**（尤其 ch02/ch03/ch07/ch16/ch17）——修订前需 Explorer 定点核实。
4. **Agent Teams 发布/接线状态**：是否真被发布产物排除、运行入口如何接线，需核实后再定「成章 vs 附录」。
5. **新增包的「净新增 vs 重打包」界线**：宏观提示 7673 files / 42 万行含大量改名与重打包（`web-react` 被移除/合并、rename detection 被跳过），且 `.agents/notes` 18% / `docs/subsystems` 21% / `apps/web/tests` 14% 非教学源码——**不得以原始规模折算新机制量**，后续 Explorer 判定机制边界时须排除这些非产品源。

---

## 附：一句话总览（供回报）

- **推荐新章集合**：Webhook（优先最前）、Agent Teams（保留实验性）、Web Client（条件成章/待核实）共 3 章；DeepSeek wire extensions 并入 ch7。
- **优先级**：Webhook ＞ Agent Teams ＞ Web Client。
- **修订清单头**：ch07（并入扩展注册表小节）与 ch16（apiproxy/session-controller）为 **revise**；ch02/ch03/ch04/ch11/ch13/ch15/ch17 为 **re-verify**；其中 ch13 短期承接 Web Client 修订项。