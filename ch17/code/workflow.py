"""第 17 章：workflow 引擎——四段骨架：校验 → 提交 → 事件流 → 结果。

真实源码（HEAD 8bd0733）：
- `WorkflowEngine` 是抽象 Service seam：`packages/workflow/workflow/src/index.ts:157`，
  唯一抽象方法 `start(request): WorkflowRun`（`index.ts:168`），每个 context 只挂
  一个实现（`ctx.workflowEngine`）。
- `WorkflowStartRequest`：`packages/workflow/workflow/src/runtime-types.ts:19`。
- `WorkflowRun` 契约：`runtime-types.ts:40` —— `result` 永不 reject，
  `cancel()`/`dispose()` 幂等。
- `WorkflowResult`：`packages/workflow/workflow/src/types.ts:72`
  `{ value, stopReason, error?, agentsStarted }`；stopReason 是封闭 union
  completed | cancelled | error（`types.ts:63`）。
- 六类 workflow/* 事件：`packages/workflow/workflow/src/index.ts:94-100`，
  `workflow/end` 的负载刻意不含 result value（`types.ts:124`）。
- 实装 `PtcWorkflowEngine`：`packages/workflow/workflow-ptc/src/index.ts:103`。
  `start()`（`:134`）先同步校验：`validateMeta`（`workflow-ptc/src/meta.ts:76`）
  与 `assertBodyParses`（`workflow-ptc/src/index.ts:55`，只编译不执行）。
- 脚本宿主是共享的 PTC Node 进程：`PtcWorkflowRun.drive()` 调
  `ctx.ptcRuntime.run(...)`（`workflow-ptc/src/host.ts:288`），并带上
  `sandboxPolicy`（`host.ts:296`）——脚本在调用方 Session 的文件策略下跑。
- 进程内的 `WorkflowExecution`（`workflow-ptc/src/runtime.ts:56`）用
  `vm.Context` + `vm.Script` 编译脚本（`runtime.ts:62-63`），`drive()`（`:105`）
  把一切失败折叠成 stopReason=error；终值经 `materializeFromRealm`
  （`workflow-ptc/src/realm.ts:62`）实体化为纯 JSON。

[教学简化] 真实实现把脚本放进 PTC 管理的 Node 进程，在 vm 里执行；教学版在
同进程同步执行，脚本就是一个 Python 函数，`agent` / `phase` / `log` 钩子以参数
形式显式传入。`start()` 提交后立刻同步跑完，而不是返回 pending 的 Promise。
[教学决策] 保留「同步校验 → 提交 → 事件流 → 结果」四段骨架，这是本章的主干。
"""
import json

__all__ = ["WorkflowEngine", "WorkflowFailure"]

STOP_COMPLETED = "completed"
STOP_CANCELLED = "cancelled"
STOP_ERROR = "error"


class WorkflowFailure(Exception):
    """对应 WorkflowError（`workflow/src/index.ts:130`）：带机器可路由的 code。

    真实实现里所有 code 都标记为 fatal：钩子用错、上限触发、provider 故障
    都会终止整段脚本，而不是把某一项降级成 null。
    """

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def validate_meta(meta):
    """对应 validateMeta（`workflow-ptc/src/meta.ts:76`）：逐条报违规字段名。"""
    if not isinstance(meta, dict):
        raise WorkflowFailure("invalid meta: meta must be an object", "META_INVALID")
    violations = []
    known = {"name", "description", "whenToUse", "phases"}
    for key in meta:
        if key not in known:
            violations.append(f"meta.{key} is not a recognized field")
    if not isinstance(meta.get("name"), str) or not meta["name"]:
        violations.append("meta.name must be a non-empty string")
    if not isinstance(meta.get("description"), str) or not meta["description"]:
        violations.append("meta.description must be a non-empty string")
    if violations:
        raise WorkflowFailure("invalid meta: " + "; ".join(violations), "META_INVALID")
    return {k: meta[k] for k in ("name", "description", "whenToUse", "phases") if k in meta}


class WorkflowEngine:
    """workflow 引擎 seam，对应抽象 WorkflowEngine Service（`workflow/src/index.ts:157`）。"""

    def __init__(self, subagent_start, max_total_agents=1000):
        # subagent_start(prompt, opts) -> 子 agent 结果：对应
        # PtcWorkflowRun.startChild → subagents.startActivation（host.ts:203）
        self._subagent_start = subagent_start
        self._max_total_agents = max_total_agents
        self.events = []  # workflow/* 事件：只观察，监听器拿不到 live run

    # ---- 第一段：同步校验 ----
    def _validate(self, request):
        meta = validate_meta(request.get("meta"))
        script = request.get("script")
        # 真实实现用 `new vm.Script(...)` 只编译不执行（assertBodyParses，index.ts:55）；
        # 教学版用一个可调用对象代表「脚本已通过语法检查」。
        if not callable(script):
            raise WorkflowFailure("workflow script does not parse", "SCRIPT_PARSE")
        return meta, script

    # ---- 第二段：提交 ----
    def start(self, request):
        """对应 PtcWorkflowEngine.start()（`workflow-ptc/src/index.ts:134`）。

        违规在提交前同步 throw；一旦返回 run，后续失败全部走 run.result。
        """
        meta, script = self._validate(request)
        run = _WorkflowRun(self, script, meta, request.get("args") or {})
        # workflow/start 在 run 被返回前发出；workflow/end 由 run 结算时发出。
        self._emit("workflow/start", {"id": run.id, "meta": dict(meta)})
        # 真实实现是 `Promise.resolve().then(() => this.drive())`（host.ts:144），
        # 提交与执行之间隔一个微任务；教学版同步调用，保持先后顺序可观察。
        run._drive()
        return run

    # ---- 第三段：事件流 ----
    def _emit(self, name, payload):
        self.events.append((name, payload))

    def _run_agent(self, run, prompt, opts):
        """agent() 钩子的一次扇出，对应 WorkflowExecution.agent()（runtime.ts:163）。"""
        if run.agents_started >= self._max_total_agents:
            raise WorkflowFailure("this run reached its total agent cap", "AGENT_CAP")
        run.agents_started += 1
        info = {"seq": run.agents_started, "label": opts.get("label") or prompt[:12]}
        self._emit("workflow/agent-start", {"runId": run.id, **info})
        try:
            outcome = self._subagent_start(prompt, opts)
        except Exception as e:
            # 子 agent 基础设施故障是 fatal；普通子任务失败应 resolve 成 null。
            self._emit("workflow/agent-end", {"runId": run.id, "seq": info["seq"], "outcome": "failed"})
            raise WorkflowFailure(f"agent() could not start a child: {e}", "AGENT_START") from e
        self._emit("workflow/agent-end", {"runId": run.id, "seq": info["seq"], "outcome": "completed"})
        return outcome


class _WorkflowRun:
    """WorkflowRun 契约（`workflow/src/runtime-types.ts:40`）：result 永不 reject。"""

    def __init__(self, engine, script, meta, args):
        self._engine = engine
        self._script = script
        self._args = args
        self.id = f"run-{len(engine.events) + 1}"
        self.meta = meta
        self.agents_started = 0
        self.result = None
        self._cancel_reason = None

    def _drive(self):
        """对应 WorkflowExecution.drive()（`workflow-ptc/src/runtime.ts:105`）。"""
        agent = lambda prompt, opts=None: self._engine._run_agent(self, prompt, opts or {})  # noqa: E731
        phase = lambda title: self._engine._emit("workflow/phase", {"runId": self.id, "title": title})  # noqa: E731
        log = lambda message: self._engine._emit("workflow/log", {"runId": self.id, "message": message})  # noqa: E731
        try:
            if self._cancel_reason is not None:
                self.result = self._cancelled()
            else:
                value = self._script(agent=agent, args=self._args, phase=phase, log=log)
                value = _materialize_result(value)  # 对应 materializeResult（runtime.ts:128）
                self.result = {"value": value, "stopReason": STOP_COMPLETED,
                               "agentsStarted": self.agents_started}
        except Exception as e:  # 一切失败都收口为结果对象
            code = getattr(e, "code", "SCRIPT_ERROR")
            self.result = {"value": None, "stopReason": STOP_ERROR,
                           "error": f"{code}: {e}", "agentsStarted": self.agents_started}
        finally:
            # workflow/end 只带结果数据，刻意省略 value（types.ts:124）。
            self._engine._emit("workflow/end", {
                "id": self.id,
                "stopReason": self.result["stopReason"],
                "agentsStarted": self.result["agentsStarted"],
            })

    def _cancelled(self):
        return {"value": None, "stopReason": STOP_CANCELLED,
                "error": f"workflow run cancelled: {self._cancel_reason}",
                "agentsStarted": self.agents_started}

    def cancel(self, reason="workflow cancelled"):
        """对应 PtcWorkflowRun.cancel（`workflow-ptc/src/host.ts:151`）：首次请求生效。"""
        if self.result is not None or self._cancel_reason is not None:
            return
        self._cancel_reason = reason

    def dispose(self):
        """对应 PtcWorkflowRun.dispose（`workflow-ptc/src/host.ts:162`）：幂等。"""
        self.cancel("workflow disposed")


def _materialize_result(value):
    """实体化边界：终值必须是可无损序列化的纯 JSON（对应 realm.ts:62）。"""
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise WorkflowFailure(
            "the workflow's return value is not plain JSON data — "
            f"{e}. Return only JSON-serializable objects/arrays/scalars.",
            "RESULT_UNSERIALIZABLE",
        ) from e
    return value
