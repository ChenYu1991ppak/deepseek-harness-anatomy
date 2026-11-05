"""第 4 章教学重构：tools 注册表与执行器管线（定义面 + 展示面 + 执行面 + 执行器 seam）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（HEAD 8bd073392，packages/core/tools/src/ 已拆成五个文件）：
- ToolDefinition / ToolRestriction / Pre|PostToolDecision ↔ contracts.ts:26 / 376 / 357 / 367
- ToolsService（ctx.tools）  ↔ index.ts:99；register ↔ :128；restrict ↔ :162；view ↔ :199
- ToolLayer                  ↔ index.ts:63（只剩 tools + restrictions，mode 字段已删除）
- checkGuards                ↔ index.ts:393；guard ↔ :384
- setExecutor / selectExecutor ↔ index.ts:325 / :357（最近作用域选执行器）
- ToolExecutor 契约          ↔ executor.ts:64（describe + execute + sections）
- 三段管线                   ↔ pipeline.ts:62（ToolExecutionPipeline：prepare :190 / dispatch :295 / complete :336）
- 批量调度                   ↔ batch.ts:50（executeToolBatch：exclusive barrier / parallel pool）
- 原生执行器                 ↔ tool-executor-native/src/index.ts:8
- PTC 执行器                 ↔ tool-executor-ptc/src/index.ts:20（只暴露 run_code + 生成 SDK）

[教学简化] 真实代码把管线拆成独立的 ToolExecutionPipeline 类，注册表通过 tools.setExecutor() 选执行器；
教学版把两者合并进 ToolRuntime，并默认挂一个原生执行器（真实代码需要显式 mount 一个执行器行）。

[教学决策 G4-升级] 第 1 章的 ctx.waterfall 是「值传递」简化版（cordis.py:153–163）。
本章执行管线需要「中间件式」瀑布——监听器包裹 next 续体、最外层优先、兜底在最内层
（vendor/cordis/src/events.ts:246）。故新增 waterfall_wrap 补全这一真实语义，
三段管线统一复用它，仅参数与兜底不同。
"""
from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass, field

# 复用第 1 章：Service 构造即注册（cordis.py:271–284），ctx.effect 表达可逆效应。
_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)
from cordis import Service  # noqa: E402


def waterfall_wrap(listeners, args, fallback):
    """中间件式瀑布：监听器从外到内包裹，fallback 最内层兜底。

    监听器签名 listener(*args, next)；调用 next() 才把控制权交给内层，不调则短路。
    补全第 1 章简化掉的真实中间件语义（events.ts:246 ↔ cordis.py:153–163）。
    """

    def invoke(index):
        if index >= len(listeners):
            return fallback()
        return listeners[index](*args, lambda: invoke(index + 1))

    return invoke(0)


@dataclass(frozen=True)
class PreToolDecision:
    """调用前决策：allow / deny / cancel / ask（contracts.ts:357–365）。"""

    kind: str
    reason: str | None = None

    @staticmethod
    def allow():
        return PreToolDecision("allow")

    @staticmethod
    def deny(reason):
        return PreToolDecision("deny", reason)


@dataclass(frozen=True)
class PostToolDecision:
    """调用后决策：accept / block（contracts.ts:367–374）。"""

    kind: str
    feedback: str | None = None

    @staticmethod
    def accept():
        return PostToolDecision("accept")

    @staticmethod
    def block(feedback):
        return PostToolDecision("block", feedback)


@dataclass(frozen=True)
class ToolDefinition:
    """一条工具契约（contracts.ts:26）：名字 + 描述 + 参数 schema + 执行体。

    [教学简化] 真实版另有必填的 output 契约 {schema, render}，execute 签名 (args, exec)；
    教学版删掉了 output，结果不再快照/校验/渲染，execute 默认只收 args。
    wants_exec=True 时按真实签名把 exec 也传进去（present 工具用它暂存待落事件）。
    """

    name: str
    description: str
    parameters: dict
    execute: "callable"  # dict -> str（wants_exec 时为 dict, exec -> str）
    timeout_ms: int | None = None
    wants_exec: bool = False


@dataclass(frozen=True)
class ToolResult:
    """工具最终结果（对应 ToolExecutionResult）。"""

    content: str
    is_error: bool = False


@dataclass
class ToolExecution:
    """一次工具调用对象，贯穿三段管线（对应 ToolExecution / ToolRunContext）。"""

    call_id: str
    name: str
    arguments: dict
    nested: bool = False  # True = 来自 run_code 程序内部（对应 ToolExecution.parent）


@dataclass
class ToolLayer:
    """作用域层（index.ts:63）：只剩工具与 restriction 两张表。

    [教学简化] 真实版是 ScopedLayers 的全局层 + agent 作用域层（作用域 shadow 全局）；
    教学版只维护单一全局层，restriction 也不分作用域。guards 在真实代码里是独立的
    GuardLayer（index.ts:93），教学版合并进来省一张表。
    """

    tools: dict = field(default_factory=dict)
    restrictions: list = field(default_factory=list)  # 编译后的 {allow?, deny?}
    guards: list = field(default_factory=list)

    def admits(self, name):
        """restriction 判定：allow 名单里没有或 deny 名单里点名，都算不可见（index.ts:79–85）。"""
        for f in self.restrictions:
            if "allow" in f and name not in f["allow"]:
                return False
            if "deny" in f and name in f["deny"]:
                return False
        return True

    def guard_reason(self, exec):
        """单调守卫：顺序跑 guards，返回第一个非 None 的拒绝理由（index.ts:393–401）。"""
        for guard in self.guards:
            reason = guard(exec)
            if reason is not None:
                return reason
        return None


class ToolExecutor:
    """执行器契约（executor.ts:64）：模型看见什么 + 一批调用怎样执行。

    两个成员各管一面：
    - describe(definitions)：返回 {schemas, knownNames}，决定模型的「可调用面」；
    - execute(batch)：接过整批调用（解析、调度、取消、落日志都归它）。
    sections 是它顺带注册的 prompt 片段（PTC 执行器用它在提示词里放 SDK）。
    """

    sections: list = ()

    def describe(self, definitions):
        raise NotImplementedError

    def execute(self, batch, runtime):
        raise NotImplementedError


class NativeToolExecutor(ToolExecutor):
    """原生执行器（tool-executor-native/src/index.ts:8–22）：每个可见工具都是一个函数定义。"""

    def describe(self, definitions):
        return {
            "schemas": [runtime_schema(d) for d in definitions],
            "knownNames": [d.name for d in definitions],
        }

    def execute(self, batch, runtime):
        return runtime.run_batch(batch)


class PtcExecutor(ToolExecutor):
    """PTC 执行器（tool-executor-ptc/src/index.ts:20–62）：只暴露 run_code，其余工具走生成的 SDK。

    [教学简化] 真实版按 runtime.language 生成 TypeScript/Python SDK 并接上 PTC 运行时；
    教学版只渲染一份 Python 风格 SDK 文本，run_code 用一个玩具解释器跑。
    """

    def __init__(self, runtime):
        self.runtime = runtime
        self.sections = [("tools:ptc-only", 5, self._only_instruction()),
                         ("tools:sdk", 6, self._sdk_text(runtime.definitions()))]

    def _only_instruction(self):
        return "`run_code` is the only tool you can call directly — a tool call naming any other tool fails."

    def _sdk_text(self, definitions):
        lines = ["可用的 SDK 函数（只能在 run_code 程序里调用）："]
        for d in definitions:
            args = ", ".join(f"{k}: {v}" for k, v in d.parameters.items())
            lines.append(f"  {d.name}({args}) -> {d.description}")
        return "\n".join(lines)

    def describe(self, _definitions):
        """PTC 下模型只看得见 run_code 一个 schema（index.ts:41–43）。"""
        return {
            "schemas": [runtime_schema(self.runtime.run_code_tool)],
            "knownNames": ["run_code"],
        }

    def execute(self, batch, runtime):
        return runtime.run_batch(batch)


def runtime_schema(definition):
    """把定义投影成给模型看的白名单：只留 {name, description, parameters}。

    真实版是 projectToolSchema（schema.ts:641）：丢 execute、output、presentCall，
    只留模型该看的四个字段（deferLoading 是可选的第五个）。
    """
    return {
        "name": definition.name,
        "description": definition.description,
        "parameters": definition.parameters,
    }


class ToolRuntime(Service):
    """ctx.tools 服务：注册表 + 执行器选择 + 三段管线（index.ts:99；管线本体见 pipeline.ts:62）。"""

    def __init__(self, ctx, config=None):
        super().__init__(ctx, "tools")
        self._layer = ToolLayer()
        self._pre_execute = []
        self._execute = []
        self._post_execute = []
        self._executor = None
        self._executor_unavailable = False
        self.run_code_tool = ToolDefinition(
            "run_code", "提交一段程序，由执行器代跑其中的工具调用",
            {"code": "string"}, self._run_code_body, wants_exec=True,
        )
        # [教学简化] 真实代码由组合里的执行器行调用 set_executor；教学版默认挂原生执行器。
        self.set_executor(NativeToolExecutor())

    # ---------- 定义面：注册与限制 ----------

    def register(self, definition):
        """把工具写入作用域层并触发 tools/change，返回注销 disposer（index.ts:128–153）。"""
        name = definition.name
        if name == "run_code":
            raise ValueError(f'tool name "run_code" is reserved for the PTC mode presentation transport')
        if name in self._layer.tools:
            raise ValueError(f'tool "{name}" is already registered in this scope')
        self._layer.tools[name] = definition
        self.ctx.emit("tools/change", {"name": name, "op": "add"})

        def dispose():
            self._layer.tools.pop(name, None)
            self.ctx.emit("tools/change", {"name": name, "op": "remove"})

        return self.ctx.effect(lambda: dispose)

    def restrict(self, allow=None, deny=None):
        """给继承来的全局工具加一层掩码，返回解除 disposer（index.ts:162–189）。

        restriction 只作用于「继承来的」工具，不遮自己作用域里注册的工具；
        真实版还会拒绝未知名字与 run_code，教学版从简。
        """
        if allow is None and deny is None:
            raise ValueError("tools.restrict() is a no-op: pass allow and/or deny")
        compiled = {}
        if allow is not None:
            compiled["allow"] = set(allow)
        if deny is not None:
            compiled["deny"] = set(deny)
        self._layer.restrictions.append(compiled)

        def dispose():
            self._layer.restrictions.remove(compiled)

        return self.ctx.effect(lambda: dispose)

    def guard(self, guard_fn):
        """注册单调守卫：只能拒绝、不能放行（index.ts:384–386）。"""

        def setup():
            self._layer.guards.append(guard_fn)
            return lambda: self._layer.guards.remove(guard_fn)

        return self.ctx.effect(setup)

    def get(self, name):
        """按名取可见工具定义（index.ts:239）。"""
        return self._layer.tools.get(name) if self._layer.admits(name) else None

    # ---------- 展示面：schema 与 prompt 片段 ----------

    def view(self):
        """可见工具集：注册表里通过 restriction 的那些（index.ts:199–227）。

        [教学简化] 真实 view(scope) 还要合并作用域链、应用继承 restriction（第 9 章）；
        教学版只有一张全局层。
        """
        return [d for name, d in self._layer.tools.items() if self._layer.admits(name)]

    def definitions(self):
        """交给执行器 describe() 的可见定义列表（index.ts:305–307）。"""
        return self.view()

    def schemas(self):
        """可见定义的 schema 投影（index.ts:316–318）。"""
        return [runtime_schema(d) for d in self.definitions()]

    def wire_schemas(self):
        """兼容名：describe 只给 {schemas}，knownNames 由这里兜底补齐。

        真实版由注册表在构造器里补齐（index.ts:115–116）；
        [教学简化] 教学版两个执行器的 describe 都直接返回 knownNames，省掉这一层兜底。
        """
        return self._executor.describe(self.definitions())

    def render_schemas(self):
        """把 describe 结果渲染成 prompt 文本，供 system-prompt section 注入。

        [教学简化] 真实代码由 ToolsService 构造时接到 ctx.systemPrompt.tools(provider)
        （index.ts:113–117 ↔ system-prompt/src/index.ts:551）；第 2 章教学版只有 section 缝合点。
        """
        description = self._executor.describe(self.definitions())
        lines = ["可用工具："]
        for s in description["schemas"]:
            lines.append(f"- {s['name']}: {s['description']}")
            lines.append(f"  参数 {s['parameters']}")
        return "\n".join(lines)

    # ---------- 执行器 seam ----------

    def set_executor(self, executor, replace=False):
        """在调用方作用域挂一个执行器；同一作用域重复挂即失败（index.ts:325–350）。

        真实版按 scope 分 root / scoped 两级存放，最近作用域胜出；教学版只有一个
        部署级槽位，用 replace=True 模拟「更近的作用域挂上新执行器」。
        """
        if self._executor is not None and not self._executor_unavailable and not replace:
            raise RuntimeError("Only one tool executor may be registered in each scope")
        self._executor = executor
        self._executor_unavailable = False

    def select_executor(self):
        """取当前生效的执行器；卸载后不会回落到继承的那个（index.ts:357–367）。"""
        if self._executor is None or self._executor_unavailable:
            raise RuntimeError("The selected tool executor is unavailable.")
        return self._executor

    def clear_executor(self):
        """模拟执行器被卸载：选择保留但不可用，调用直接失败，不回落（index.ts:345–348）。"""
        self._executor_unavailable = True

    # ---------- 执行面：守卫管线 ----------

    def on_pre_execute(self, listener):
        self._pre_execute.append(listener)

    def on_execute(self, wrapper):
        self._execute.append(wrapper)

    def on_post_execute(self, listener):
        self._post_execute.append(listener)

    def execute(self, exec):
        """单次调用：交给执行器，执行器内部走三段管线（index.ts:375–377）。

        [教学简化] 真实 execute 收的是整批 ToolBatchInput；教学版保留单次入口，
        让第 5/6/8/9/12 章的消费者一行不改。
        """
        return self.select_executor().execute([exec], self)[0]

    def run_batch(self, batch):
        """按顺序跑一批调用并返回结果（batch.ts:50 的串行简化版）。"""
        results = []
        for exec in batch:
            prepared = self._prepare(exec)
            if prepared["kind"] == "final-result":
                results.append(self._announce(prepared["exec"], prepared["result"]))  # 跳过 post-execute，但仍广播
                continue
            if prepared["kind"] == "post-result":
                results.append(self._complete(prepared["exec"], prepared["result"]))
                continue
            dispatched = self._dispatch(prepared["exec"])
            results.append(self._complete(prepared["exec"], dispatched))
        return results

    def _prepare(self, exec):
        """调用前：执行器拒绝 → pre-execute 瀑布 → 单调守卫（pipeline.ts:190–251）。

        返回 {"kind": "final-result"|"post-result"|"dispatch"}。
        [教学简化] 真实版的 ask 走 serviceAsk 审批 seam（pipeline.ts:432）；教学版没有 ask
        分支，监听器若返回 kind="ask" 会落进下面的 dispatch（按放行处理），不是 deny。
        """
        if self._executor_is_ptc() and not exec.nested and exec.name != "run_code":
            # PTC 执行器在策略之前就拒绝直接调用（tool-executor-ptc/src/index.ts:35–38）。
            return {"kind": "final-result", "exec": exec,
                    "result": ToolResult(f"unknown tool: {exec.name} (call it inside run_code)", True)}
        gate = waterfall_wrap(self._pre_execute, (exec,), fallback=PreToolDecision.allow)
        if gate.kind == "allow":
            reason = self._layer.guard_reason(exec)
            if reason is not None:
                return {"kind": "post-result", "exec": exec, "result": ToolResult(f"Error: {reason}", True)}
        elif gate.kind == "deny":
            return {"kind": "post-result", "exec": exec, "result": ToolResult(f"Error: {gate.reason}", True)}
        return {"kind": "dispatch", "exec": exec}

    def _dispatch(self, exec):
        """调用中：execute 环绕瀑布，兜底直接调函数体（pipeline.ts:295–327）。"""
        return waterfall_wrap(self._execute, (exec,), fallback=lambda: self._dispatch_body(exec))

    def _dispatch_body(self, exec):
        """真正调用用户工具体，包装成 ToolResult（pipeline.ts:258–292）。"""
        tool = self._lookup(exec)
        if tool is None:
            return ToolResult(content=f"unknown tool: {exec.name}", is_error=True)
        try:
            if tool.wants_exec:
                value = tool.execute(exec.arguments, exec)
            else:
                value = tool.execute(exec.arguments)
            return ToolResult(content=str(value))
        except Exception as e:  # noqa: BLE001  教学版统一兜底
            return ToolResult(content=f"tool failed: {e}", is_error=True)

    def _lookup(self, exec):
        """按名字解析可执行定义；run_code 由 PTC 执行器自带，不在注册表里。

        真实版由执行器给管线注入 get 覆盖（tool-executor-ptc/src/index.ts:32–34）。
        """
        if exec.name == "run_code" and self._executor_is_ptc():
            return self.run_code_tool
        return self._layer.tools.get(exec.name)

    def _complete(self, exec, result):
        """调用后：post-execute 瀑布，兜底 accept，然后广播结果（pipeline.ts:336–372）。"""
        decision = waterfall_wrap(self._post_execute, (exec, result), fallback=PostToolDecision.accept)
        if decision.kind == "block":
            result = ToolResult(content=decision.feedback or "blocked", is_error=True)
        return self._announce(exec, result)

    def _announce(self, exec, result):
        """广播冻结后的最终结果（pipeline.ts:374–423 finish → notifyResult）。

        被 pre-execute / 守卫拒绝的调用同样会广播；被执行器在策略前拒绝的也广播。
        真实版即使 complete 阶段出故障也仍会走到 notifyResult，只是结果换成错误。
        """
        self.ctx.emit("tools/result", exec, result)  # 只读通知（pipeline.ts:400–423）
        return result

    # ---------- PTC：run_code ----------

    def _executor_is_ptc(self):
        return isinstance(self._executor, PtcExecutor)

    def nested_execute(self, name, arguments, parent):
        """run_code 程序内部的一次调用：与顶层共用同一条管线，只是 nested=True。

        真实版同样复用 ToolExecutionPipeline（tool-executor-ptc/src/ptc.ts:587–601）。
        """
        child = ToolExecution(call_id=f"{parent.call_id}:ptc:1", name=name,
                              arguments=arguments, nested=True)
        prepared = self._prepare(child)
        if prepared["kind"] == "final-result":
            return self._announce(child, prepared["result"])
        if prepared["kind"] == "post-result":
            return self._complete(child, prepared["result"])
        return self._complete(child, self._dispatch(prepared["exec"]))

    def _run_code_body(self, args, exec):
        """玩具 run_code：逐行解析 `tool(args...)` 并走嵌套调用，返回拼接后的输出。

        真实 run_code 在 PTC 运行时里跑一段真程序（ptc.ts:273 createRunCodeTool）。
        """
        tree = ast.parse(args["code"])
        outputs = []
        for node in tree.body:
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                raise ValueError("run_code 只支持每行一个工具调用")
            call = node.value
            name = call.func.id
            arguments = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
            result = self.nested_execute(name, arguments, exec)
            outputs.append(result.content)
        return "\n".join(outputs)
