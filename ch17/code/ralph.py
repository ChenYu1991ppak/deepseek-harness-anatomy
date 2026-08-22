"""第 17 章：Ralph 循环——固定前台工作流，把不可变 objective 依次交给全新子 agent。

真实源码（17a 笔记）：
- ralph 是面向模型的固定工具：`packages/workflow/tool-ralph/src/index.ts:405`
  注册，`execute`（`:437`）里调
  `ctx.workflowEngine.start({ script: RALPH_SCRIPT, subagentProvider, maxTotalAgents: maxRounds, ... })`。
- `RALPH_SCRIPT` 固定脚本：`index.ts:90` —— `for round in 1..maxRounds`，
  每轮 `agent(prompt, { schema: reportSchema })` 启动**全新**子 agent，
  `previous` 报告是唯一跨轮交接；工作区是唯一跨轮长期记忆。
- `RalphRoundReport`：`{ status: continue|complete|blocked, summary, evidence[],
  nextSteps[], blocker }`；脚本内 validateReport 与消费方 readReport（`:247`）
  双重校验；终值经 readRunResult（`:283`）解码为
  complete | blocked | budget-limited | round-failed。
- `requireFreshProvider`（`index.ts:220`）要求子 agent 提供方
  `inheritsParentContext === false`：每轮必须全新，不继承父上下文。
- Ralph 不向 agent-loop 加模式，与第 14 章 goal 领域独立（17a 笔记 §5）。

[教学简化] 真实 Ralph 经 subagentProvider 启动全新子 agent 进程/会话；
教学版用一个工厂函数，每次调用返回全新执行逻辑，previous 报告同样只经 prompt 传递。
[教学决策] 保留「固定脚本 + 每轮全新 + previous 唯一交接 + report 双重校验 +
四种终值」这条主干，与 RALPH_SCRIPT 骨架（17a 笔记 §4.6）逐段对应。
"""

__all__ = ["RALPH_META", "ralph_script", "run_ralph", "REPORT_STATUSES"]

REPORT_STATUSES = ("continue", "complete", "blocked")

# 对应 RALPH_META（tool-ralph/src/index.ts:80）
RALPH_META = {"name": "ralph",
              "description": "固定前台工作流：把不可变 objective 逐轮交给全新子 agent"}


def ralph_script(agent, args):
    """RALPH_SCRIPT 固定脚本骨架，对应 tool-ralph/src/index.ts:90。

    agent(prompt) 每轮启动一个全新子 agent（fresh provider，
    inheritsParentContext === false）；上一轮 report 作为 previous 写进 prompt，
    是唯一的跨轮交接。
    """
    objective = args["objective"]
    report = None
    for round_no in range(1, args["maxRounds"] + 1):
        previous = report
        prompt = f"objective: {objective}\n"
        if previous is None:
            prompt += "首轮，没有上一轮报告。"
        else:
            prompt += f"上一轮报告: {previous['summary']}；待办: {previous.get('nextSteps', [])}"
        report = agent(prompt)
        _validate_report(report)  # 脚本内校验（对应 validateReport，17a 笔记 §3）
        if report["status"] == "complete":
            return {"outcome": "complete", "rounds": round_no, "report": report}
        if report["status"] == "blocked":
            return {"outcome": "blocked", "rounds": round_no, "report": report}
    # 轮数耗尽：对应 readRunResult 的 budget-limited 终值（index.ts:283）
    return {"outcome": "budget-limited", "rounds": args["maxRounds"], "report": report}


def _validate_report(report):
    """RalphRoundReport 校验：status 必须是封闭集合（17a 笔记 §1）。"""
    if not isinstance(report, dict) or report.get("status") not in REPORT_STATUSES:
        raise ValueError(f"非法 RalphRoundReport: {report!r}")


def run_ralph(engine, objective, max_rounds):
    """ralph 工具的 execute 主干（tool-ralph/src/index.ts:437）：
    start({script: RALPH_SCRIPT, args, ...}) → await run.result。

    [教学简化] 省略 requireFreshProvider 校验（:445）与 maxTotalAgents 映射，
    「每轮全新」由 engine 的子 agent 工厂天然保证。
    """
    run = engine.start({
        "script": ralph_script,
        "meta": RALPH_META,
        "args": {"objective": objective, "maxRounds": max_rounds},
    })
    return run.result
