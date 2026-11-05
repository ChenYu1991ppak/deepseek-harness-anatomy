"""第 17 章：Ralph 循环——已降级的可选 bundle（opt-in 实验包）。

真实源码（HEAD 8bd0733）：
- 包位置：`packages/experimental/tool-ralph/src/index.ts`，插件名 `tool-ralph`
  （`:17`），inject `['tools', 'workflowEngine', 'subagents', 'systemPrompt']`（`:18`）。
  它已从 `packages/workflow/` 挪进 experimental，**默认组合不启用**：
  `packages/workflow/workflow-ptc/README.md:28` 写着 "Ralph remains disabled in
  shipped defaults"；`packages/bundle/base/cordis.patch.yml` 里没有 ralph 行。
- 分发形态：`packages/experimental/ralph-bundle/cordis.patch.yml:1-14` 插入一个
  group `optional-ralph`，`isolate: { workflowEngine: true }`（`:5`）自带一个
  workflow 引擎，再在组内挂 `@deepseek-ai/dsh-workflow-ptc`（`:9`）与
  `@deepseek-ai/dsh-tool-ralph`（`:13`）。
- `RALPH_META`：`tool-ralph/src/index.ts:78`，name = 'ralph-loop'，带
  `phases: [{ title: 'Fresh-agent rounds', ... }]`。
- `RALPH_SCRIPT`：`tool-ralph/src/index.ts:88` 起的 String.raw 模板，
  `for (let round = 1; ...)` 在 `:151`，每轮 `await agent(prompt, { schema })`
  在 `:161`，`round-failed` 在 `:166`，`budget-limited` 在 `:174`。
- `validateReport`（`:110`）在脚本内校验；`readReport`（`:245`）与
  `readRunResult`（`:281`）在宿主侧再解码一次，四态：
  complete | blocked | budget-limited | round-failed。
- `requireFreshProvider`（`:218`）：provider 必须支持结构化输出且
  `inheritsParentContext === false`。
- 工具注册 `apply(ctx, config)` 在 `:403`，`ctx.tools.register` 在 `:410`，
  `execute` 里 `ctx.workflowEngine.start(...)` 在 `:445`，`await run.result` 在
  `:459`，`await run.dispose()` 在 `:471`。

[教学简化] 真实 Ralph 经 subagentProvider 启动全新子 agent 进程/会话；
教学版用一个工厂函数，每次调用返回全新执行逻辑，previous 报告同样只经 prompt 传递。
[教学决策] 保留「固定脚本 + 每轮全新 + previous 唯一交接 + report 双检 +
四种终值」这条主干；本轮新增的是「它是 opt-in」这件事本身。
"""

__all__ = ["run_ralph", "RALPH_META", "RALPH_SCRIPT_OBJECTIVE_KEY"]

REPORT_STATUSES = ("continue", "complete", "blocked")

# 对应 RALPH_META（packages/experimental/tool-ralph/src/index.ts:78）
RALPH_META = {
    "name": "ralph-loop",
    "description": "Iterate toward one objective with a fresh child and bounded "
                   "structured handoff per round.",
    "phases": [{"title": "Fresh-agent rounds",
                "detail": "One clean child context per Ralph round."}],
}

RALPH_SCRIPT_OBJECTIVE_KEY = "objective"


def ralph_script(agent, args, phase, log):
    """RALPH_SCRIPT 固定脚本骨架，对应 tool-ralph/src/index.ts:88。

    模型只提供数据（objective / maxRounds），脚本本身写死在部署侧：
    模型不能改循环、不能改 provider 路由、不能改 schema。
    """
    objective = args["objective"]
    max_rounds = args["maxRounds"]
    phase("Fresh-agent rounds")           # 脚本自己的 phase() 调用（:150）
    previous = None
    rounds_started = 0
    for round_no in range(1, max_rounds + 1):
        prior = "(none — this is the first round)" if previous is None else repr(previous)
        prompt = (f"Immutable objective:\n{objective}\n\n"
                  f"Ralph round: {round_no} of {max_rounds}.\n\n"
                  f"Previous structured handoff:\n{prior}")
        log(f"round {round_no} starts a fresh child")
        raw = agent(prompt, {"label": f"Ralph round {round_no}",
                             "phase": "Fresh-agent rounds",
                             "schema": _REPORT_SCHEMA})
        rounds_started = round_no
        if raw is None:
            # 子 agent 失败：脚本把它收口成可解码的终值（:166）
            return {"status": "round-failed", "roundsStarted": round_no,
                    "lastReport": previous}
        report = _validate_report(raw, args["maxHandoffChars"])
        if report["status"] == "complete":
            return {"status": "complete", "roundsStarted": round_no, "report": report}
        if report["status"] == "blocked":
            return {"status": "blocked", "roundsStarted": round_no, "report": report}
        previous = report
    # 轮数耗尽：直接落成终值，不再是「抛异常」（:174）
    return {"status": "budget-limited", "roundsStarted": rounds_started, "report": previous}


_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["continue", "complete", "blocked"]},
        "summary": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "nextSteps": {"type": "array", "items": {"type": "string"}},
        "blocker": {"type": "string"},
    },
    "required": ["status", "summary", "evidence", "nextSteps", "blocker"],
}


def _validate_report(report, max_handoff_chars):
    """脚本内校验，对应 validateReport（tool-ralph/src/index.ts:110）。

    三个状态各有配套约束：continue 需要 nextSteps 且 blocker 为空；
    complete 需要 evidence、无 nextSteps、blocker 为空；blocked 需要具体 blocker。
    """
    if not isinstance(report, dict) or report.get("status") not in REPORT_STATUSES:
        raise ValueError("Ralph round report status is invalid")
    if not isinstance(report.get("summary"), str) or not report["summary"]:
        raise ValueError("Ralph round report summary must be non-empty and normalized")
    evidence = report.get("evidence")
    next_steps = report.get("nextSteps")
    blocker = report.get("blocker")
    if not isinstance(evidence, list) or not isinstance(next_steps, list):
        raise ValueError("Ralph round report evidence and nextSteps must be arrays")
    if not isinstance(blocker, str):
        raise ValueError("Ralph round report blocker must be a normalized string")
    if report["status"] == "continue" and (not next_steps or blocker != ""):
        raise ValueError("a continuing Ralph report needs nextSteps and an empty blocker")
    if report["status"] == "complete" and (not evidence or next_steps or blocker != ""):
        raise ValueError("a complete Ralph report needs evidence, no nextSteps, and an empty blocker")
    if report["status"] == "blocked" and not blocker:
        raise ValueError("a blocked Ralph report needs a concrete blocker")
    if len(repr(report)) > max_handoff_chars:
        raise ValueError("Ralph round report exceeds maxHandoffChars")
    return report


def read_run_result(value, max_rounds):
    """宿主侧二次解码，对应 readRunResult（tool-ralph/src/index.ts:281）。

    这不是信任脚本的返回值，而是把「跨 provider 边界拿回来的 JSON」
    重新按封闭结构校验一遍：字段集合必须精确匹配。
    """
    if not isinstance(value, dict):
        raise ValueError("Ralph workflow returned a malformed terminal result")
    status = value.get("status")
    rounds_started = value.get("roundsStarted")
    if not isinstance(rounds_started, int) or not 1 <= rounds_started <= max_rounds:
        raise ValueError("Ralph workflow returned a malformed terminal result")
    if status == "round-failed":
        expected = {"status", "roundsStarted", "lastReport"}
        if set(value) != expected:
            raise ValueError("Ralph workflow returned a malformed terminal result")
        return {"status": status, "roundsStarted": rounds_started,
                "lastReport": value["lastReport"]}
    if status in ("complete", "blocked", "budget-limited"):
        if set(value) != {"status", "roundsStarted", "report"}:
            raise ValueError("Ralph workflow returned a malformed terminal result")
        if status == "budget-limited" and rounds_started != max_rounds:
            raise ValueError("Ralph workflow returned budget-limited before the round limit")
        return {"status": status, "roundsStarted": rounds_started, "report": value["report"]}
    raise ValueError("Ralph workflow returned an unknown terminal status")


def run_ralph(engine, objective, max_rounds):
    """ralph 工具的 execute 主干（tool-ralph/src/index.ts:410-470）。

    顺序与真实实现一致：先 start（同步校验 meta/script），再 await result，
    最后 dispose 收尾。[教学简化] 省略 requireFreshProvider 的注册表查询。
    """
    run = engine.start({
        "script": ralph_script,
        "meta": RALPH_META,
        "args": {"objective": objective, "maxRounds": max_rounds,
                 "maxHandoffChars": 16_384},
    })
    settled = run.result
    if settled["stopReason"] != "completed":
        raise RuntimeError(f"Ralph workflow failed: {settled.get('error')}")
    decoded = read_run_result(settled["value"], max_rounds)
    run.dispose()
    return decoded
