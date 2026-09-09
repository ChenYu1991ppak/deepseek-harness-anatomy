# 上游升级分析报告：deepseek-harness 新内容是否值得新写章节

> 生成方式：Source Decomposition Expert Team（Explorer 宏观扫描 + Explorer 深潜 3 候选 + Architect 综合提案）
> 日期：2026-08-30
> 目标仓：`/home/chenyu/deepseek-harness`（已 fast-forward 到 `origin/master`，HEAD=`0a53fb55be`）
> 基线：`47f943859b`（教程 17 章写作时 checkout；对应发布 `dsh-0.1.0-rc.5` 附近的 npm 公开合入）
> 现有教程大纲：`ch00/outline_zh.md`（17 章递进）

---

## 一句话结论

上游在基线后合入 **2167 个 commit、7673 文件、+42.1 万 / −14.2 万行**，其中真正值得**新写一个章节**的机制有 **3 个**（优先级：**Webhook > Agent Teams > Web Client 架构**），另有 1 个机制建议**并入现有第 7 章**（DeepSeek LLM API wire extensions），以及 **9 个既有章节需要修订或复核**。

---

## 一、升级规模（先校准「真新增」口径）

| 指标 | 值 |
|---|---|
| commit 数 | 2167 |
| 变更文件 | 7673（rename detection 被跳过，含大量改名/重打包） |
| 增删行 | +420,613 / −141,776 |

**关键警示**：42 万行 ≠ 净新增教学量。按 `--dirstat` 分布，`docs/subsystems/` 占 21%、`.agents/notes/`（Agent 归档）占 18%、`apps/web/tests/`（测试快照）占 14%、`docs/` 占 7.6% —— 这些是文档生成物与测试快照，不能折算成「新机制」。判定新章节时只认**产品源码 + subsystem 契约文档**。

---

## 二、新章候选结论

| 候选机制 | 基线后新增? | 判定 | 优先级 |
|---|---|---|---|
| **Webhook 运行时**（外部事件 → Session） | ✅ 全新（`packages/webhook` 基线无） | **✅ 新写一章** | 🥇 |
| **Agent Teams**（单会话多 agent 持久协作） | ✅ 全新（`packages/experimental/agent-team` 基线无） | **✅ 新写一章（带实验性保留）** | 🥈 |
| **Web Client 架构**（Slots + Conversation UI 组合） | ✅ 新建前端层（`ui-renderer`/`conversation` 等） | ⚠️ **条件成章 / 待符号级核实** | 🥉 |
| DeepSeek LLM API wire extensions | ✅ 全新（`llm/deepseek-llm-api-extensions`） | **并入第 7 章**（体量不足独立成章） | — |
| cookbook「添加一个 X」扩展点总集 | 扩容（1→12 篇） | ❌ 不单独成章（手册层，与 `docs/user/` 重叠） | — |
| session-controller BFF 同构层 | ✅ api 组重构新结构面 | ❌ 不单独成章（并入第 16 章修订） | — |

### 🥇 1. Webhook（最推荐，无实验性负担）

- **主题**：外部 HTTP 事件如何变成一次 agent 会话（外→内触发 ingress 面）。
- **新增机制**：`ctx.webhookRuntime` 规则运行时 + `VerifiedWebhookDelivery`（提供方中立、`deepFreeze` 快照）+ `WebhookEventMap` 声明合并扩展点 + `dispatch` fire-and-forget（错误按规则隔离）+ `createWebhookSession`（preset→workspace→agent→followup，失败分层回滚）+ `webhook-github` 适配器（验签 / 有界读 / `202`/`503`）。
- **延伸基础章**：ch5（seam 三角色）、ch15（preset）、ch6（workspace）。
- **为什么排第一**：全新 ingress 面、seam 三角色完整、体量适配（~840 行）、正常发布、后端域 Python 重建顺滑、与 17 章零重叠。

### 🥈 2. Agent Teams（机制最自洽，但 experimental + private）

- **主题**：单会话内多命名 agent 的持久协作域。
- **新增机制**：隐式根团队（`TeamId`=root `SessionId`）+ `TeamRoster`（lead/teammate 名册）+ `TeamMailbox` 持久邮箱（`queued`/`delivered` 双回执）+ `TeamTaskBoard` 任务 DAG（`revision` CAS 乐观锁 + 无环校验）+ `teamProjectionDefinition` 投影折叠（Lead 日志即团队状态）+ team-scoped 模型工具 + profile patch 叠层。
- **延伸基础章**：ch11（subagent transport）、ch3（投影）、ch9（scope）、ch15（patch 叠层）。
- **为什么排第二**：机制密度与教学价值最高（Lead-log 投影三原语 + CAS 任务图 + scoped 工具是全新「多主体持久协作」概念），但上游标 **experimental + private（发布产物排除）**，成章须在开头标注实验性状态并接受未来 churn 风险。

### 🥉 3. Web Client 架构（条件成章 / 待核实）

- **主题**：浏览器侧客户端 UI 如何成为插件化扩展点（`ctx.slots.register()` + `ConversationNodeAssembler`）。
- **存疑点**：① 前端 React 域与「Python 重构教学」模型错配；② 本轮深潜未覆盖、无符号级证据。
- **建议**：Explorer 补一轮定点勘探后再定；短期先降级为 ch13/ch17 修订项。

> 章号与插入位置（建议接 ch15 后作为追加卷 18/19/20）不在本报告定死，由大纲执笔时按「延伸基础章 < 本章号」递进约束排定。

---

## 三、需修订 / 复核的既有章

| 章 | 问题 | 建议 |
|---|---|---|
| **ch07**（LLM 适配） | 新增扩展注册表 + 插件包清单上报（churn ~1.6%） | **修订**：新增「请求扩展注册表」小节（register→prepare→accept + `dsh_*` 命名空间） |
| **ch16**（typert/api/sdk） | `session-controller`（~1.2%）+ `apiproxy`（~1.2%）大改，新增 BFF 同构层 | **修订/复核**：吸收 session-controller BFF（待 Explorer 核符号） |
| **ch02**（agent-loop） | 核心闭环 src+tests 实质改动 | 复核 |
| **ch03**（session 持久化） | SQLite 后端 `SCHEMA_VERSION` 演进（~1.6%） | 复核 |
| **ch04**（tools 管线） | 轻量 churn | 复核 |
| **ch11**（subagent 委派） | `subagent/` ~0.5%；被 Agent Teams 复用为底座 | 复核（若 Teams 成章，加衔接预告） |
| **ch13**（web/lsp） | 只覆盖 `packages/web`，未覆盖 Web Client UI | 复核（短期承接 Web Client 修订项） |
| **ch15**（preset/bundle/profile） | `agent-presets/` ~0.4% | 复核 |
| **ch17**（python/apps 端到端） | `python/sdk*` 大改（~1.7%）、apps/web 经 Slots/Conversation 重构 | 复核 |

---

## 四、风险与边界（写作前必须先解决）

1. **实验性包**：Agent Teams（+ 两个 profile）属 `experimental + private`，发布产物排除；其他实验组（inspector / webworker-runtime / webworker-packer）不纳入。
2. **定位重叠**：上游 `docs/user/` 新出 basic/practice/framework 三阶教程，与 learn-deepseek-harness「递进式源码学习」高度重合 —— 这是定位决策（复用 vs 差异化），需单独成本收益评估。
3. **硬缺口（已标「待 Explorer 核实」）**：
   - Web Client 架构缺符号级证据（Slots/Conversation/renderer 入口与 API）；
   - session-controller BFF 缺符号级描述；
   - ch02/ch03/ch07/ch16/ch17 正文是否仍与 HEAD 一致，未逐章核实；
   - Agent Teams 发布/接线状态需核实后再定「成章 vs 附录」；
   - 「净新增 vs 重打包」界线：`web-react` 被移除/合并、rename detection 跳过，判定机制边界须排除 `.agents/notes` / `docs/subsystems` / 测试快照。

---

## 五、本报告涉及的中间产物（团队各阶段落盘物）

| 文件 | 角色 | 内容 |
|---|---|---|
| `notes/diff-map-notes.md` | Explorer（宏观扫描） | 规模、新包/新子系统、churn 回映 17 章、候选清单 |
| `notes/candidates-notes.md` | Explorer（深潜） | 3 候选 A/B/C 符号级证据 + 逐项 verdict |
| `architect-proposal.md` | Architect（综合） | 新章 outline 条目 + 修订清单 + 风险边界 |
| `README.md`（本文件） | Orchestrator（组装） | 面向你的最终决策摘要 |