"""第 1 章教学重构：Cordis 插件内核最小集。

仅标准库，Python 3.10+。

源码对应（vendor/cordis/src/）：
- Context                    ↔ context.ts:42
- Service                    ↔ service.ts:11
- Fiber                      ↔ fiber.ts:184
- Context.plugin             ↔ registry.ts:195 plugin(:316)
- on/emit/serial/waterfall   ↔ events.ts:288/194/204/234
"""
from __future__ import annotations

from typing import Callable

__all__ = [
    "Context", "Fiber", "Service", "ServiceNotFoundError", "Symbols",
]


class Symbols:
    """对应 vendor/cordis/src/utils.ts:50-73 的 Symbol.for('cordis:*')。

    教学版实际使用 services / events / dispose 三个。
    """

    services = "__cordis_services__"
    events = "__cordis_events__"
    dispose = "__cordis_dispose__"


class ServiceNotFoundError(AttributeError):
    """读取未提供服务时抛出。

    [教学决策 G1] 继承 AttributeError，使 getattr(ctx, name, default) 的 fallback 仍可用。
    """

    def __init__(self, name: str):
        super().__init__(f"service '{name}' is not provided")
        self.service_name = name


_current_fiber: "Fiber | None" = None


class Context:
    """插件执行环境：服务解析、依赖注入、事件派发、生命周期注册四合一。"""

    def __init__(self):
        setattr(self, Symbols.services, {})    # 服务表：name → 实例
        setattr(self, Symbols.events, {})      # 监听器表：事件名 → [listener]
        setattr(self, Symbols.dispose, False)  # dispose 标记
        self._fibers: list[Fiber] = []
        self._pending: list[Fiber] = []
        self._disposers: list[Callable] = []

    # ---------- 服务解析 ----------

    def __getattr__(self, name):
        """普通属性查找失败时按服务名查服务表。"""
        if name.startswith("_"):
            raise AttributeError(name)
        services = getattr(self, Symbols.services)
        if name in services:
            return services[name]
        raise ServiceNotFoundError(name)

    def provide(self, name, value):
        """注册服务，返回注销 disposer。

        写服务表 → 派发 service/provide → 结算等待依赖的 fiber。
        """
        services = getattr(self, Symbols.services)
        services[name] = value
        self.emit("service/provide", {"name": name})
        self._settle_pending()

        def dispose():
            if services.get(name) is value:
                del services[name]
                self.emit("service/dispose", {"name": name})

        return dispose

    def _settle_pending(self):
        """新服务到达后逐个复查 PENDING fiber：依赖已满足才加载。"""
        while True:
            ready = [f for f in self._pending if f.deps_satisfied()]
            if not ready:
                return
            for fiber in ready:
                self._pending.remove(fiber)
                fiber.activate()

    # ---------- 事件派发 ----------

    def on(self, event, listener):
        """注册监听器，返回 off disposer。

        注册本身是 effect：宿主 fiber 卸载时自动注销。
        """
        listeners = getattr(self, Symbols.events).setdefault(event, [])

        def setup():
            listeners.append(listener)

            def off():
                if listener in listeners:
                    listeners.remove(listener)

            return off

        return self.effect(setup)

    def emit(self, event, *args):
        """同步派发：按注册顺序调用，不等返回值。"""
        for listener in list(getattr(self, Symbols.events).get(event, [])):
            listener(*args)

    def serial(self, event, *args):
        """顺序调用，遇 bail 值（非 None 且非 False）立即返回。"""
        for listener in list(getattr(self, Symbols.events).get(event, [])):
            result = listener(*args)
            if result is not None and result is not False:
                return result
        return None

    def waterfall(self, event, value):
        """把返回值逐个传递：返回 None 放行，非 None 替换当前值。"""
        for listener in list(getattr(self, Symbols.events).get(event, [])):
            result = listener(value)
            if result is not None:
                value = result
        return value

    # ---------- 生命周期注册 ----------

    def effect(self, execute):
        """注册 effect：立即执行 execute，收集其返回的清理函数。

        清理函数在卸载时逆序执行——后注册的先清理。
        """
        disposer = execute()
        if disposer is None:
            disposer = lambda: None
        if _current_fiber is not None:
            _current_fiber._disposers.append(disposer)
        else:
            self._disposers.append(disposer)
        return disposer

    def plugin(self, callback, config=None):
        """注册插件：函数 / 类 / apply 对象三形态归一，返回 Fiber。"""
        body = _normalize(callback, config)
        return Fiber(self, getattr(callback, "inject", None) or [], body)

    def dispose(self):
        """销毁容器：按注册逆序卸载 fiber，再逆序执行根级 effect。"""
        if getattr(self, Symbols.dispose):
            return
        setattr(self, Symbols.dispose, True)
        for fiber in reversed(self._fibers):
            fiber.dispose()
        for disposer in reversed(self._disposers):
            disposer()


# ---------- 插件三形态归一 ----------


def _normalize(callback, config):
    """三种插件形态归一化为「接收 ctx 的函数」。"""
    if isinstance(callback, type):
        if config is not None:
            return lambda ctx: callback(ctx, config)
        return callback
    apply_ = getattr(callback, "apply", None)
    if callable(apply_):
        return apply_
    return callback


class Fiber:
    """插件的一次激活。

    [教学简化] 态机简化为 PENDING / ACTIVE / DISPOSED 三态；
    真实 cordis 六态，且支持变化即重载（本章只实现「满足才加载」）。
    """

    PENDING = "pending"
    ACTIVE = "active"
    DISPOSED = "disposed"

    def __init__(self, ctx, inject, body):
        self.ctx = ctx
        self.name = getattr(body, "__name__", type(body).__name__)
        self.inject = inject
        self.state = self.PENDING
        self._disposers: list[Callable] = []
        self._body = body
        ctx._fibers.append(self)
        if self.deps_satisfied():
            self.activate()
        else:
            ctx._pending.append(self)

    def deps_satisfied(self):
        """inject 声明的服务全部已提供才加载。events 不计入。"""
        services = getattr(self.ctx, Symbols.services)
        return all(name in services for name in self.inject if name != "events")

    def activate(self):
        """执行插件体；期间 _current_fiber 指向 self（effect 收集目标）。"""
        global _current_fiber
        self.state = self.ACTIVE
        parent = _current_fiber
        _current_fiber = self
        try:
            self._body(self.ctx)
        finally:
            _current_fiber = parent

    def dispose(self):
        """卸载：逆序执行清理函数。"""
        if self.state == self.DISPOSED:
            return
        for disposer in reversed(self._disposers):
            disposer()
        self._disposers.clear()
        self.state = self.DISPOSED


class Service:
    """服务基类：构造即注册。

    对应 vendor/cordis/src/service.ts:11。
    注册本身是可逆效应——容器销毁时自动从服务表移除。
    """

    def __init__(self, ctx, name=None):
        self.ctx = ctx
        self.service_name = name
        if name is not None:
            dispose = ctx.provide(name, self)
            ctx.effect(lambda: dispose)