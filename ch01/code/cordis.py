"""第 1 章教学重构：Cordis 插件内核最小集。

仅标准库，Python 3.10+。

源码对应（vendor/cordis/src/，行号为 notes §13 r7 裁定）：
- Context                    ↔ context.ts:42（容器；接口 :16；Proxy get 陷阱路由 → reflect.ts:133）
- Service                    ↔ service.ts:11（构造器 :42-59，注册语句 ctx.reflect.provide :57；r7 裁定 notes §13 D3）
- Fiber                      ↔ fiber.ts:184（插件的一次激活；effect/逆序回收 :415/:418/:431）
- Context.plugin             ↔ RegistryService（registry.ts:195）plugin（:316），三形态归一在 plugin 内
- on/emit/serial/waterfall   ↔ events.ts:288（on）/:194（emit）/:204（serial）/:234（waterfall）
                              （r7 裁定 notes §13 D1；:183 是 parallel，不是 emit）
"""
from __future__ import annotations

__all__ = ["Context", "Fiber", "Service", "ServiceNotFoundError", "Symbols"]


class Symbols:
    """对应 vendor/cordis/src/utils.ts:50-73（源码无独立 symbols.ts；r7 裁定）。

    [教学决策] Symbol.for('cordis:...') → 双下划线字符串常量（notes §6 符号映射）。
    教学版实际使用 services/events/dispose 三个，其余列出仅供映射参考。
    """

    invoke = "__cordis_invoke__"
    dispose = "__cordis_dispose__"
    events = "__cordis_events__"
    fiber = "__cordis_fiber__"
    inject = "__cordis_inject__"
    intercept = "__cordis_intercept__"
    parent = "__cordis_parent__"
    provider = "__cordis_provider__"
    root = "__cordis_root__"
    services = "__cordis_services__"
    setup = "__cordis_setup__"


class ServiceNotFoundError(AttributeError):
    """读取未提供服务时抛出。

    [教学决策 G1] 素材未给定错误类型；此处定义为继承 AttributeError，
    使 getattr(ctx, name, default) 的缺省值写法仍然可用。
    """

    def __init__(self, name: str):
        super().__init__(f"service '{name}' is not provided")
        self.service_name = name


# [教学简化] 真实 cordis 用 AsyncLocalStorage 找到宿主 fiber；
# 教学版同步单线程，用模块级变量标记「当前正在激活的 fiber」即可。
_current_fiber: "Fiber | None" = None


class Context:
    """插件执行环境：服务解析、依赖注入、事件派发、生命周期注册四合一。

    对应 vendor/cordis/src/context.ts:42。属性读取对应 TS 的 Proxy get 陷阱：
    普通查找失败后路由到服务表（context.ts:16/42 → reflect.ts:133/136）。
    [教学决策 G6] TS Proxy 同时拦截 get/set；Python 的 __getattr__ 只拦截缺失属性读取，
    因此服务注册一律走显式 provide()，不用 __setattr__ 魔法，避免内部字段误伤。
    """

    def __init__(self):
        setattr(self, Symbols.services, {})    # 服务表：name → 实例
        setattr(self, Symbols.events, {})      # 监听器表：事件名 → [listener]
        setattr(self, Symbols.dispose, False)  # dispose 标记
        self._fibers = []      # 全部 fiber（按注册顺序）
        self._pending = []     # 依赖未满足、等待加载的 fiber
        self._disposers = []   # 根级 effect 的清理函数

    # ---------- 服务解析 ----------

    def __getattr__(self, name):
        """普通属性查找失败时才调用：一切非内部属性都按服务读取。

        对应 Proxy get 陷阱路由到 ReflectService.get（reflect.ts:133/136）。
        """
        if name.startswith("_"):
            raise AttributeError(name)
        services = getattr(self, Symbols.services)
        if name in services:
            return services[name]
        raise ServiceNotFoundError(name)

    def provide(self, name, value):
        """注册服务，返回「注销」disposer（reflect.ts:237-243）。

        注册后触发 service/provide，并结算等待依赖的 fiber。
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
        """新服务到达后逐个复查 PENDING fiber：满足才加载。

        fiber.ts:249-251 的反面：PENDING + _checkInject() 不满足 → 不加载而等待。
        """
        while True:
            ready = [f for f in self._pending if f.deps_satisfied()]
            if not ready:
                return
            for fiber in ready:
                self._pending.remove(fiber)
                fiber.activate()

    # ---------- 事件派发 ----------

    def on(self, event, listener):
        """注册监听器，返回 off disposer（events.ts:288-302；r7 裁定）。

        注册本身是 effect：宿主 fiber 卸载时自动注销（events.ts:254 register → fiber.effect）。
        """
        listeners = getattr(self, Symbols.events).setdefault(event, [])

        def setup():
            listeners.append(listener)

            def off():
                if listener in listeners:
                    listeners.remove(listener)

            return off

        return self.effect(setup, label=f"on:{event}")

    def emit(self, event, *args):
        """同步派发：按注册顺序调用，不等返回值（events.ts:194；r7 裁定，:183 是 parallel）。"""
        for listener in list(getattr(self, Symbols.events).get(event, [])):
            listener(*args)

    def serial(self, event, *args):
        """[教学简化] 简化 serial（约 5 行）：顺序调用，遇 bail 值立即返回。

        真实 serial 顺序 await（events.ts:204-209）；bail 判定：非 None 且非 False
        （isBailed，events.ts:13）。本章用于 turn-stopping agent/turn-stopping（agent.ts:296）。
        """
        for listener in list(getattr(self, Symbols.events).get(event, [])):
            result = listener(*args)
            if result is not None and result is not False:
                return result
        return None

    def waterfall(self, event, value):
        """[教学简化] 简化 waterfall：把返回值穿下去，返回 None 视为放行。

        真实 waterfall 是中间件式：监听器包裹 next 续体、最外层优先（events.ts:234）。
        [教学决策 G4] 无监听器时原样返回初始 value。
        """
        for listener in list(getattr(self, Symbols.events).get(event, [])):
            result = listener(value)
            if result is not None:
                value = result
        return value

    # ---------- 生命周期注册 ----------

    def effect(self, execute, label="anonymous"):
        """注册 effect：立即执行 execute，收集其返回的清理函数。

        清理函数在卸载时逆序执行（fiber.ts:415/418/431）——后注册的先清理。
        label 参数对齐真实 API，教学版不消费它。
        """
        disposer = execute()
        if disposer is None:
            disposer = lambda: None  # noqa: E731  无清理函数时用 no-op 占位
        if _current_fiber is not None:
            _current_fiber.disposers.append(disposer)
        else:
            self._disposers.append(disposer)
        return disposer

    def plugin(self, callback, config=None):
        """注册插件：函数 / 类 / apply 对象三形态在此归一化，返回本次激活的 Fiber。

        对应 RegistryService（registry.ts:195）plugin 方法（:316）。
        """
        return Fiber(self, callback, config)

    def dispose(self):
        """销毁容器：按注册逆序卸载全部 fiber，再逆序执行根级 effect。

        [教学简化] 真实代码 dispose 异步且带 settle 超时。
        """
        if getattr(self, Symbols.dispose):
            return
        setattr(self, Symbols.dispose, True)
        for fiber in reversed(self._fibers):
            fiber.dispose()
        for disposer in reversed(self._disposers):
            disposer()


class Fiber:
    """插件的一次激活（fiber.ts:184），跟踪生命周期与全部 effect 清理函数。

    [教学简化] 状态机简化为 PENDING/ACTIVE/DISPOSED 三态；真实代码为
    PENDING/LOADING/ACTIVE/FAILED/DISPOSED/UNLOADING 六态，依赖变化时先
    _unload（逆序执行 disposer）再 _reload（fiber.ts:588-609、646-696）。
    [教学决策 G8] 本章不实现「变化即重载」，只实现「满足才加载」。
    """

    PENDING = "pending"
    ACTIVE = "active"
    DISPOSED = "disposed"

    def __init__(self, ctx, callback, config=None):
        self.ctx = ctx
        self.config = config
        self.state = self.PENDING
        self.disposers = []
        self.inject = list(getattr(callback, "inject", None) or [])
        self._body = self._normalize(callback, config)
        ctx._fibers.append(self)
        if self.deps_satisfied():  # 满足才加载（fiber.ts:249-251）
            self.activate()
        else:
            ctx._pending.append(self)

    @staticmethod
    def _normalize(callback, config):
        """三形态归一：

        类 → 实例化 cls(ctx, config)；apply 对象 → 取其 apply(ctx)；函数 → 原样调用 fn(ctx)。
        """
        if isinstance(callback, type):
            return lambda ctx: callback(ctx, config)
        apply_ = getattr(callback, "apply", None)
        if callable(apply_):
            return lambda ctx: apply_(ctx)
        return callback

    def deps_satisfied(self):
        """inject「满足才加载」检查：声明的服务全部已提供才加载。

        events 是容器内置能力，不计入外部依赖。
        """
        services = getattr(self.ctx, Symbols.services)
        return all(name in services for name in self.inject if name != "events")

    def activate(self):
        """执行插件体；执行期间的宿主 fiber 指向 self（effect 的收集目标）。"""
        global _current_fiber
        self.state = self.ACTIVE
        parent = _current_fiber
        _current_fiber = self
        try:
            self._body(self.ctx)
        finally:
            _current_fiber = parent

    def dispose(self):
        """卸载：逆序执行清理函数（fiber.ts:431）。"""
        if self.state == self.DISPOSED:
            return
        for disposer in reversed(self.disposers):
            disposer()
        self.disposers.clear()
        self.state = self.DISPOSED


class Service:
    """服务基类：构造即注册。

    对应 vendor/cordis/src/service.ts:11：构造器 :42-59 调 ctx.reflect.provide（:57）把实例挂到 ctx.<key>
    （r7 裁定，notes §13 D3）。
    教学版用 ctx.effect 显式表达「卸载即移除」——注册本身就是可逆效应。
    """

    def __init__(self, ctx, name=None):
        self.ctx = ctx
        self.service_name = name
        if name is not None:
            dispose = ctx.provide(name, self)
            ctx.effect(lambda: dispose, label=f"service:{name}")
