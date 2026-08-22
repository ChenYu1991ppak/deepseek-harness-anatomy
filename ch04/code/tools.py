"""第 4 章教学重构：tools 注册与执行管线（定义面 + 展示面 + 执行面）。

仅标准库，Python 3.10+。运行：python3 main.py

源码对应（packages/core/tools/src/index.ts）：
- ToolRuntime   ↔ index.ts:787（类体 787–1863）
- ToolLayer     ↔ index.ts:714（tools/restrictions/guards/mode 四字段）
- ToolDefinition↔ index.ts:222；Pre/PostToolDecision ↔ index.ts:588–597
- register ↔ index.ts:1037；guard ↔ :1110；presentAs ↔ :946
- wireSchemas ↔ index.ts:980（返回 {schemas, knownNames}）
- 执行管线  ↔ execute :1342 → prepareExecution :1463 → dispatchScheduledExecution :1569
              → finalizeScheduledExecution :1609 → finishScheduledExecution :1631

[教学决策 G4-升级] 第 1 章的 ctx.waterfall 是「值传递」简化版（cordis.py:153–163）。
本章执行管线需要「中间件式」瀑布——监听器包裹 next 续体、最外层优先、兜底在最内层
（vendor/cordis/src/events.ts:234）。故新增 waterfall_wrap 补全这一真实语义，
三段管线统一复用它，仅参数与兜底不同。
"""
from __future__ import annotations

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
    补全第 1 章简化掉的真实中间件语义（events.ts:234 ↔ cordis.py:153–163）。
    """

    def invoke(index):
        if index >= len(listeners):
            return fallback()
        return listeners[index](*args, lambda: invoke(index + 1))

    return invoke(0)


@dataclass(frozen=True)
class PreToolDecision:
    """调用前决策：allow / deny / ask（index.ts:588–597）。"""

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
    """调用后决策：accept / block（index.ts:588–597）。"""

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
    """一条工具契约（index.ts:222）：名字 + 描述 + 参数 schema + 执行体。

    [教学简化] 真实版 execute 签名 (args, exec) 且返回任意值经 snapshot/validate/render；
    教学版退化为 execute(args) -> str，output 契约省略。
    """

    name: str
    description: str
    parameters: dict
    execute: "callable"  # dict -> str
    timeout_ms: int | None = None


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


@dataclass
class ToolLayer:
    """作用域层（index.ts:714）：工具 + 守卫 + 展示模式。

    [教学简化] 真实版是 ScopedLayers 的全局层 + agent 作用域层（作用域 shadow 全局）；
    教学版只维护单一全局层。
    """

    tools: dict = field(default_factory=dict)
    guards: list = field(default_factory=list)
    modes: dict = field(default_factory=dict)  # name -> "native"|"code"|"both"

    def guard_reason(self, exec):
        """单调守卫：顺序跑 guards，返回第一个非 None 的拒绝理由（index.ts:1119–1127）。"""
        for guard in self.guards:
            reason = guard(exec)
            if reason is not None:
                return reason
        return None


class ToolRuntime(Service):
    """ctx.tools 服务：注册面 + 展示面 + 执行面三合一（index.ts:787）。"""

    def __init__(self, ctx, config=None):
        super().__init__(ctx, "tools")
        self._layer = ToolLayer()
        self._pre_execute = []
        self._execute = []
        self._post_execute = []
        self._seq = 0

    # ---------- 定义面：注册 ----------

    def register(self, definition):
        """把工具写入作用域层并触发 tools/change，返回注销 disposer（index.ts:1037）。"""
        name = definition.name
        self._layer.tools[name] = definition
        self.ctx.emit("tools/change", {"name": name, "op": "add"})

        def dispose():
            self._layer.tools.pop(name, None)
            self.ctx.emit("tools/change", {"name": name, "op": "remove"})

        return self.ctx.effect(lambda: dispose, label=f"tool:{name}")

    def guard(self, guard_fn):
        """注册单调守卫：只能拒绝、不能放行（index.ts:1110）。"""
        self._layer.guards.append(guard_fn)

    def present_as(self, name, mode):
        """设置工具的展示/执行模式 native/code/both（index.ts:946）。"""
        self._layer.modes[name] = mode

    def mode_for(self, name):
        """读取工具模式，默认 native（对应 modeFor）。"""
        return self._layer.modes.get(name, "native")

    def get(self, name):
        """按名取工具定义（对应 ctx.tools.get）。"""
        return self._layer.tools.get(name)

    # ---------- 展示面：schema 注入 ----------

    def view(self):
        """可见工具集：模式非 code 的工具才向模型暴露 schema。

        [教学简化] 真实版 view(scope) 经 ScopedLayers 合并 + restrictions 过滤（index.ts:1152）；
        教学版只按模式过滤。
        """
        return [d for d in self._layer.tools.values() if self.mode_for(d.name) != "code"]

    def schema_of(self, definition):
        """把定义投影成给模型看的白名单：只留 {name, description, parameters}。"""
        return {
            "name": definition.name,
            "description": definition.description,
            "parameters": definition.parameters,
        }

    def wire_schemas(self):
        """汇总可见工具 schema 与已知名字（index.ts:980，返回 {schemas, knownNames}）。"""
        visible = self.view()
        return {
            "schemas": [self.schema_of(d) for d in visible],
            "knownNames": [d.name for d in visible],
        }

    def render_schemas(self):
        """把 wireSchemas 结果渲染成 prompt 文本，供 system-prompt section 注入。

        [教学简化] 真实代码走 ctx.systemPrompt.tools(provider)（index.ts:826/980）；
        第 2 章教学版 SystemPromptService 只有 section 缝合点，故以普通 section 承载。
        """
        lines = ["可用工具："]
        for s in self.wire_schemas()["schemas"]:
            lines.append(f"- {s['name']}: {s['description']}")
            lines.append(f"  参数 {s['parameters']}")
        return "\n".join(lines)

    # ---------- 执行面：守卫管线 ----------

    def on_pre_execute(self, listener):
        self._pre_execute.append(listener)

    def on_execute(self, wrapper):
        self._execute.append(wrapper)

    def on_post_execute(self, listener):
        self._post_execute.append(listener)

    def execute(self, exec):
        """跑完整守卫管线：prepare → dispatch → post → finish（index.ts:1342）。"""
        gate = self._prepare(exec)
        if gate.kind != "allow":
            return ToolResult(content=gate.reason or f"{gate.kind}", is_error=True)

        result = self._dispatch(exec)
        decision = self._post(exec, result)
        if decision.kind == "block":
            result = ToolResult(content=decision.feedback or "blocked", is_error=True)

        self.ctx.emit("tools/result", exec, result)  # 通知观察者（只读，index.ts:1657）
        return result

    def _prepare(self, exec):
        """调用前：pre-execute 瀑布 + 单调守卫，返回 PreToolDecision。

        [教学简化] ask 分支真实版走 serviceAsk 审批 seam（index.ts:1689），教学版降级为 deny。
        """
        if self.mode_for(exec.name) == "code":
            return PreToolDecision.deny("code 态工具只能经 run_code 传输调用")
        gate = waterfall_wrap(self._pre_execute, (exec,), fallback=PreToolDecision.allow)
        if gate.kind == "allow":
            reason = self._layer.guard_reason(exec)
            if reason is not None:
                return PreToolDecision.deny(reason)
        return gate

    def _dispatch(self, exec):
        """调用中：execute 环绕瀑布，兜底直接调函数体（index.ts:1569–1595）。"""
        return waterfall_wrap(self._execute, (exec,), fallback=lambda: self._dispatch_body(exec))

    def _dispatch_body(self, exec):
        """真正调用用户工具体，包装成 ToolResult（index.ts:1532–1560）。"""
        tool = self._layer.tools.get(exec.name)
        if tool is None:
            return ToolResult(content=f"unknown tool: {exec.name}", is_error=True)
        return ToolResult(content=str(tool.execute(exec.arguments)))

    def _post(self, exec, result):
        """调用后：post-execute 瀑布，兜底 accept（index.ts:1742–1781）。"""
        return waterfall_wrap(self._post_execute, (exec, result), fallback=PostToolDecision.accept)
