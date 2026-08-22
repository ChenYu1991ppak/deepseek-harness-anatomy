"""第 17 章：workflow 引擎——让 agent 运行一段「编排脚本」，脚本经 agent() 钩子扇出 subagent。

真实源码（17a 笔记）：
- `WorkflowEngine` 是抽象 Service：`packages/workflow/workflow/src/index.ts:157`，
  唯一抽象方法 `start(request): WorkflowRun`（`index.ts:168`）。
- `WorkflowStartRequest`：`runtime-types.ts:19`
  `{ script, meta, args?, subagentProvider?, maxTotalAgents?, parent, signal? }`。
- `WorkflowRun` 契约：`runtime-types.ts:40` —— `result` 永不 reject，
  `cancel()`/`dispose()` 幂等。
- `WorkflowResult`：`types.ts:72` `{ value, stopReason, error?, agentsStarted }`；
  `stopReason` 是封闭 union：completed | cancelled | error。
- 6 个 workflow/* 事件只观察：`index.ts:94`；`workflow/end` 刻意省略 result value。
- 实装 `WorkerThreadWorkflowEngine`：`workflow-worker-thread/src/index.ts:112`，
  `start()`（`:143`）先同步校验（validateMeta `meta.ts:76` / assertBodyParses
  `index.ts:64`），再拉起 worker thread，脚本在 node:vm 内执行。
- 脚本终值出 realm 时经 `materializeFromRealm`（`realm.ts:66`）实体化为纯 JSON，
  非法值抛 RESULT_UNSERIALIZABLE。

[教学简化] 真实引擎把脚本放进 worker thread + node:vm 隔离执行（为了同步脚本
不阻塞宿主事件循环且可强制终止，注意它不是安全沙箱）；教学版在同进程同步执行，
脚本就是一个 Python 函数，agent() 钩子以参数形式显式传入。
[教学决策] 保留「start() 同步校验 → 返回 run 句柄 → result 永不抛异常 →
stopReason 封闭 union → 事件只观察」这套契约主干，这是 17a 笔记的核心。
"""
import json

__all__ = ["WorkflowEngine", "STOP_COMPLETED", "STOP_CANCELLED", "STOP_ERROR"]

STOP_COMPLETED = "completed"
STOP_CANCELLED = "cancelled"
STOP_ERROR = "error"


class WorkflowEngine:
    """workflow 引擎 seam，对应抽象 WorkflowEngine Service（index.ts:157）。

    每个 context 只允许一个引擎实现（17a 笔记「workflow seam」）。
    """

    def __init__(self, subagent_start):
        # subagent_start(prompt) -> 结果：对应 WorkerRun.startChild 经
        # this.subagents.start(...) 启动子 agent（host.ts:349，承接第 11 章）
        self._subagent_start = subagent_start
        self.events = []  # workflow/* 事件（只观察，监听器拿不到 live run）

    def start(self, request):
        """对应 WorkerThreadWorkflowEngine.start()（index.ts:143）：同步校验 → 执行。

        违规同步 throw；校验通过后返回 run 句柄，执行结果一律走 run.result。
        """
        # 同步校验 1：meta 必须带 name/description（对应 validateMeta，meta.ts:76）
        meta = request.get("meta") or {}
        if not meta.get("name") or not meta.get("description"):
            raise ValueError("META_INVALID: meta.name / meta.description 必填")
        # 同步校验 2：脚本必须可解析（对应 assertBodyParses，index.ts:64）
        script = request.get("script")
        if not callable(script):
            raise ValueError("SCRIPT_PARSE: script 必须可调用")

        run = _WorkflowRun(self, script, meta, request.get("args") or {})
        self._emit("workflow/start", {"id": run.id, "meta": dict(meta)})
        run._drive()
        return run

    def _emit(self, name, payload):
        self.events.append((name, payload))

    def _run_agent(self, run, prompt):
        """agent() 钩子的一次扇出，对应 WorkflowExecution.agent()（runtime.ts:250）。

        [教学简化] 真实实现还有 acquireSlot 并发槽（runtime.ts:227）与
        结构化输出 schema 校验；教学版直接串行调用。
        """
        run.agents_started += 1
        self._emit("workflow/agent-start", {"runId": run.id, "prompt": prompt})
        outcome = self._subagent_start(prompt)
        self._emit("workflow/agent-end", {"runId": run.id})
        return outcome


class _WorkflowRun:
    """WorkflowRun 契约（runtime-types.ts:40）：result 永不 reject，dispose 幂等。"""

    def __init__(self, engine, script, meta, args):
        self._engine = engine
        self._script = script
        self._args = args
        # [教学简化] 真实实现 mint UUID 作为 WorkflowRunId（types.ts:20）
        self.id = f"run-{len(engine.events) + 1}"
        self.meta = meta
        self.agents_started = 0
        self.result = None
        self._disposed = False

    def _drive(self):
        """对应 worker 侧 drive()（runtime.ts:162）：永不 reject。

        脚本抛异常或终值不可实体化，都折叠成 stopReason=error 的结果对象，
        而不是把异常抛给调用方——消费方只需 await result 再查 stopReason。
        """
        agent = lambda prompt: self._engine._run_agent(self, prompt)  # noqa: E731
        try:
            value = self._script(agent, self._args)
            value = _materialize(value)  # 对应 materializeFromRealm（realm.ts:66）
            self.result = {"value": value, "stopReason": STOP_COMPLETED,
                           "agentsStarted": self.agents_started}
        except Exception as e:  # 一切失败都收口为结果对象
            self.result = {"value": None, "stopReason": STOP_ERROR, "error": str(e),
                           "agentsStarted": self.agents_started}
        finally:
            # workflow/end 只带 id + stopReason，刻意省略 result value（17a 笔记 §1）
            self._engine._emit("workflow/end",
                               {"id": self.id, "stopReason": self.result["stopReason"]})

    def cancel(self):
        """[教学简化] 真实 cancel 可强停 worker thread（host.ts:180 幂等）；
        教学版同步执行，没有可中断点，仅保留接口与幂等语义。"""

    def dispose(self):
        """幂等清理，对应 host.ts:221（有界停稳子 agent）。"""
        self._disposed = True


def _materialize(value):
    """实体化边界：脚本终值必须是可序列化的纯 JSON（对应 realm.ts:66）。

    非法值（函数、集合、循环引用等）→ RESULT_UNSERIALIZABLE。
    """
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise ValueError(f"RESULT_UNSERIALIZABLE: {e}") from e
    return value
