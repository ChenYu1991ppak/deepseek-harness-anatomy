# 2026-11 更新报告 — 按上游 v0.2.2-alpha.3 全面更新 18 章

> 生成：2026-11-04 · 工具链：source-decomposition-expert-team（source-tour 插件）改编流水线
> 上游锚点：`8bd0733`（dsh-v0.2.2-alpha.3，2026-11-03）· 文档旧基线：`47f9438`（2026-08-13，来自旧 `upstream-analysis/` 记录，该目录已删除、可从 git 历史找回）
> 跨度：上游约 12,000+ commits、包结构 +8/−6、被引 73 文件几乎全部变更
> 工件说明：报告引用的 `.update/`（审计清单、36 份把关报告、勘探简报、大纲裁定、工具脚本）与上游 checkout 为本机工作文件，不入库；关键数据已汇总于本文。

## 总览

- **18/18 章 verified（100%）**，0 失败章、0 跳过章；新增 1 章（ch18 遥测通道），4 章改 H1 标题（ch03/ch04/ch13/ch17），章号零重排。
- 工作树改动：**115 文件 +15,238/−8,766**，另 4 个新增文件（ch18 全套、ch06×2、ch11×1 教学代码）。
- 流水线：机械审计 → surveyor 大纲裁定（用户确认）→ 6 批「author 修订 → 机械门禁 → reviewer+fact-checker 并行 → author 修订轮 → verified → en 镜像 → 全量终验」。

## 机械层（零 token 脚本）

| 项 | 数值 |
|---|---|
| 引注审计位点（zh+en+py） | 2,408 |
| 自动行号重映射（--apply，内容锚定唯一匹配） | 407 条建议 / 1,092 处替换（62%→68% 机械可处置率） |
| 终验引注总量（18 章 zh+en+code 全量） | **4,966 条，0 FAIL** |
| 全仓 demo 回归（main/hello/bad_example/task_board） | **36/36 rc=0** |
| 工具 | .update/{citation_lib,audit-citations,remap-citations,verify-citations}.py（改编自插件 verify-chapter.py：放宽反引号限制、短路径上下文解析、行号合理性消歧、绝对路径排除） |

## 把关层（模型判断）

| 章 | 处置级别 | review P0/P1 | fact 已核/FAIL | 终验引注 |
|---|---|---|---|---|
| ch01 | 重锚+存量错注结案 | 4/3 | 52/0 | 120 |
| ch02 | 重锚+拆分适配 | 3/2 | 47/2 | 245 |
| ch03 | **重写**（handle 契约+格式链） | 12/4 | 97/2 | 399 |
| ch04 | **重写**（执行器 seam） | 11/5 | 41/3 | 231 |
| ch05 | 契约收敛重写（resolve/execute） | 5/4 | 43/3 | 225 |
| ch06 | 加节×2（SSH+改动观测） | 4/6 | 56/0 | 371 |
| ch07 | **协议换代**（Messages+SettingsForms） | 5/5 | 35/5 | 293 |
| ch08 | 重锚+4 处语义纠错 | 4/2 | 30/2 | 244 |
| ch09 | 重锚+restriction 语义纠错 | 2/2 | 55/1 | 165 |
| ch10 | 重锚+8 处断言修正 | 4/3 | 38/1 | 170 |
| ch11 | 加节（Team 任务板） | 5/2 | 46/0 | 284 |
| ch12 | 重锚+7 处实质纠错 | 1/3 | 48/1 | 295 |
| ch13 | 加节+改标题（单槽排他） | 4/2 | 56/2 | 242 |
| ch14 | 重锚+3 处契约纠正 | 4/4 | 54/3 | 350 |
| ch15 | **题眼重写**（revision 绑定） | 5/3 | 60/0 | 358 |
| ch16 | 重锚+加节（officeToPdf） | 7/3 | 57/2 | 317 |
| ch17 | **重写**（PTC 运行时） | 10/4 | 104/2 | 456 |
| ch18 | **新章从零写**（遥测通道） | 10/6 | 54/2 | 201 |
| **合计** | | **P0 100 / P1 63** | **973 核 / 31 FAIL** | **4,966** |

P0 类型分布（约）：章内自洽/口径矛盾 ~45%、图文不一致 ~20%、先后颠倒/先用后定义 ~15%、承接断裂 ~10%、摘录与代码不符 ~10%。全部修复并有回报记录。

## 重大上游事实修正（本轮沉淀进正文的知识）

1. DeepSeek 适配器从 chat/completions 换代为 **Messages 协议**（ch07）
2. `SettingsProvider` key-value seam 已不存在 → **SettingsForms** 编辑面（ch07）
3. session 持久化换 **handle 所有权契约** + **格式链 v0→v4**；SQLite 后端退役（ch03）
4. tools 拆出**执行器 seam**（native/PTC），presentAs/modeFor 删除（ch04/ch09）
5. preset 题眼从「常驻挂载」换为 **revision 绑定 Generation**；PresetMountError 已不存在（ch15）
6. workflow 引擎 worker-thread → **workflow-ptc**；ralph 降级 experimental（ch17）
7. shell 契约收敛为 resolve+execute；「前台/后台」= 等不等 result()（ch05）
8. restriction 语义为**链上交集+自己层豁免**（原稿「不继承」是错的）（ch09）
9. 压缩锁跨进程边界：session/end-seed 使旧 orphan start 失效（ch10）
10. ask 由 pre-execute 监听器产生，guards 结构上产生不了 ask（ch14）
11. SkillProvider 契约无 watch；invocationPolicy 是两个布尔（ch12）
12. `context.ts:231-234` 存量错注结案：真身 `fiber.ts:424-448`（ch01）

## 新机制覆盖裁定（outline-v2，用户已批准）

- 融入：computer-use/browser-use→ch13 §5；team→ch11 §6；ssh→ch06 §6；deliverables→ch04 §4.4+ch06 §3.5+ch08 一行；document→ch16 §7.1；PTC→ch04 §5+ch17 §2
- 新章：**ch18 遥测通道**（telemetry/otel + product-telemetry-otel），配套 5 个教学代码文件
- 不覆盖（大纲点名）：mcp/jobs/schedule/spill/terminal/attachment/feedback/session-query（下一轮候选：mcp）

## 遗留与后续

1. **assets/anatomy-chapters_{zh,en}.png 待用户用 archify 按新 JSON spec 重制**（doc/anatomy-chapters.*.json 已更新为 18 章）
2. `.update/` 保留作审计痕迹（gitignored）：drift 工作清单、_review 36 份把关报告、_briefs 4 份勘探简报、_map/outline-v2.md、工具脚本
3. 外部 checkout 保留：/Users/chenyu/PycharmProjects/deepseek-harness（HEAD）、dsh-baseline（worktree @47f9438）
4. 上游 developer preview、迭代极快——下次更新可复用 .update/ 工具链，以 8bd0733 为新基线做增量
5. ch01/ch11/ch13 的 demo 依赖脚本目录自动入 sys.path（文档口径 `python3 chNN/code/main.py` 正常；`-I` 隔离模式需 cd 进 code/）

## 建议抽样章节（人工复核用）

随机 5 章：**ch05**（契约收敛+reject 三层口径）、**ch07**（协议换代最大改动面）、**ch10**（8 处断言修正+输出重拍）、**ch15**（题眼重写）、**ch18**（新章质量基线）。无 failed 章。
