"""第 9 章：scope 作用域注册——ScopedLayers 与 ScopedToolRuntime。

同一进程里多个 agent 如何隔离各自能力：每个 agent 一个作用域，注册挂到作用域
名下；读取时沿作用域链合并（同名 shadow）；作用域可单调限制自己可见的工具；
作用域之间还有父子谱系（lineage），子作用域继承祖先的注册。

对应 packages/core/scope（@deepseek-ai/dsh-scope）：
- ScopedLayers ↔ src/store.ts:159（global :161；scoped :163；chainLayers :192；merge :208；effect :226）
- lineage     ↔ src/index.ts:39 scopeParents / :72 bindScopeParent / :98 scopeChainOf
- restriction ↔ exact-scope overlay，不继承（notes §1）

第 4 章的 ToolLayer 正是 ScopeLayer 的一个实例（notes §5）；本章把通用作用域
机制落地到工具注册表，得到 ScopedToolRuntime。仅标准库，Python 3.10+。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
_CH04 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch04", "code"))
for _p in (_HERE, _CH04, _CH01):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Service  # noqa: E402
from tools import ToolDefinition, ToolExecution, ToolLayer, ToolResult, ToolRuntime  # noqa: E402


class ScopedLayers:
    """作用域分层：全局层 + 每个 scope 的 exact-scope overlay + scope 父链（lineage）。

    对应 src/store.ts:159 ScopedLayers。
    - global：急构造的全局层（store.ts:161）；
    - scoped：scope -> exact-scope overlay（store.ts:163），刻意 chain-blind（不继承祖先）；
    - shadowing：merge 时 global 打底、沿父链覆盖，最近 scope 同名胜出（store.ts:208）；
    - lineage：scope_parents 记录父子关系（index.ts:39），chain_layers 沿链取层。

    [教学简化] 真实 scope key 是不透明对象（通常就是 Agent 本身，index.ts:15），
    按身份（===）比较；教学版用字符串作 scope key，便于打印观察。
    """

    def __init__(self, create_layer):
        self.create_layer = create_layer      # 层工厂（tools 传 ToolLayer）
        self.global_layer = create_layer()     # 全局层（store.ts:161）
        self.scoped = {}                       # scope -> exact-scope overlay（store.ts:163）
        self.scope_parents = {}                # scope -> parent scope（lineage，index.ts:39）
        self.restrictions = {}                 # scope -> set(名字)；exact-scope，不继承

    # ---------- lineage（scope 父链）----------

    def bind_scope_parent(self, key, parent):
        """一次性绑定 key 的父 scope，带环检测。对应 index.ts:72 bindScopeParent。"""
        if key in self.scope_parents:
            raise ValueError(f"scope {key!r} 已绑定父级，重链需走 rebind 句柄")
        cursor = parent
        while cursor is not None:  # 沿 parent 向上走，遇到 key 即成环（index.ts:54）
            if cursor == key:
                raise ValueError("scope 父链将成环")
            cursor = self.scope_parents.get(cursor)
        self.scope_parents[key] = parent

    def scope_chain_of(self, key):
        """从 key 到根的链：[key, parent, grandparent, ...]。对应 index.ts:98 scopeChainOf。"""
        chain = []
        cursor = key
        while cursor is not None:
            chain.append(cursor)
            cursor = self.scope_parents.get(cursor)
        return chain

    # ---------- 层访问 ----------

    def for_scope(self, scope):
        """取 exact-scope overlay：None 返回全局层，否则返回（必要时创建）该 scope 的层。

        对应 store.ts:180 peek（刻意 chain-blind，不继承祖先）+ 首次创建。
        """
        if scope is None:
            return self.global_layer
        if scope not in self.scoped:
            self.scoped[scope] = self.create_layer()
        return self.scoped[scope]

    def chain_layers(self, scope):
        """沿父链取已存在的 overlay：最远祖先在前、exact scope 最后。

        对应 store.ts:192 chainLayers = scopeChainOf(scope).reverse() 过滤存在的 overlay。
        """
        layers = []
        for key in reversed(self.scope_chain_of(scope)):  # 祖先 -> 最近
            layer = self.scoped.get(key)
            if layer is not None:
                layers.append(layer)
        return layers

    # ---------- shadowing（合并）----------

    def effective_tools(self, scope):
        """有效工具集：global 打底 + 沿父链覆盖，最近 scope 同名胜出（shadowing）。

        对应 store.ts:208 merge。
        """
        merged = dict(self.global_layer.tools)
        if scope is not None:
            for layer in self.chain_layers(scope):
                merged.update(layer.tools)  # 同名覆盖：最近 scope 胜出
        return merged

    # ---------- restriction（exact-scope，单调）----------

    def is_restricted(self, scope, name):
        """工具在 exact scope 是否被限制（不继承祖先限制）。"""
        return name in self.restrictions.get(scope, set())


class ScopedToolRuntime(ToolRuntime):
    """作用域感知的工具运行时：把 ch04 的单一全局层升级为 ScopedLayers。

    第 4 章的 ToolLayer 是 ScopeLayer 的实例（notes §5）；这里用 ScopedLayers
    组织「全局层 + 每 agent 一层」，注册 / 视图 / 取定义 / 执行全部作用域感知，
    并新增 restrict 与 lineage。
    """

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        # 用 ScopedLayers 取代「单一全局层」的视角；全局层直接复用父类的 _layer，
        # 使「不传 scope」的注册 / 执行与 ch04 行为完全一致。
        self._layers = ScopedLayers(create_layer=ToolLayer)
        self._layers.global_layer = self._layer

    # ---------- lineage（委托给 ScopedLayers）----------

    def bind_scope_parent(self, key, parent):
        """建立 scope 父子谱系（子作用域继承祖先注册）。对应 index.ts:72。"""
        self._layers.bind_scope_parent(key, parent)

    def scope_chain_of(self, key):
        """读 scope 谱系链 [key, parent, ...]。对应 index.ts:98。"""
        return self._layers.scope_chain_of(key)

    # ---------- 注册（写路径）----------

    def register(self, definition, scope=None):
        """注册工具到指定作用域，返回 disposer（注册即可逆，第 1 章 ctx.effect）。

        对应 store.ts:226 effect：scope=None 写全局层，否则写该 scope 的 exact overlay。
        注册即效应：写入 tools 表 + 发 tools/change 事件。
        """
        layer = self._layers.for_scope(scope)

        def setup():
            layer.tools[definition.name] = definition
            self.ctx.emit("tools/change",
                          {"name": definition.name, "scope": scope, "op": "add"})

            def teardown():
                layer.tools.pop(definition.name, None)
                self.ctx.emit("tools/change",
                              {"name": definition.name, "scope": scope, "op": "remove"})

            return teardown

        return self.ctx.effect(setup, label=f"tool:{scope or 'global'}:{definition.name}")

    # ---------- restriction（写路径）----------

    def restrict(self, names, scope):
        """把若干工具加入某作用域的单调黑名单（只增不减）。

        [教学还原] 真实 restrict 强制要求 scope 存在，否则 throw
        「context-global restriction would mask every agent」（见 §8 源码对照）。
        被限制的工具只从该 exact scope 的 view 里消失，不继承、不影响祖先与其它 scope。
        """
        if scope is None:
            raise ValueError("context-global restriction 会遮蔽所有 agent，必须指定 scope")
        self._layers.restrictions.setdefault(scope, set()).update(names)
        for name in names:
            self.ctx.emit("tools/change", {"name": name, "scope": scope, "op": "restrict"})

    # ---------- 视图与取定义（读路径）----------

    def view(self, scope=None):
        """某作用域可见的工具定义：有效层（global+父链 shadow）→ 剔除 exact-scope restrictions。

        [教学简化] 真实 view 末尾还按 mode 过滤（presentAs，第 4 章）；本章聚焦作用域。
        """
        merged = self._layers.effective_tools(scope)
        return [definition for name, definition in merged.items()
                if not self._layers.is_restricted(scope, name)]

    def get(self, name, scope=None):
        """按作用域取工具定义：exact scope -> 祖先 -> 全局（shadow 的查找侧）。"""
        if scope is not None:
            for key in self._layers.scope_chain_of(scope):  # exact -> 祖先
                layer = self._layers.scoped.get(key)
                if layer is not None and name in layer.tools:
                    return layer.tools[name]
        return self._layer.tools.get(name)

    def defined_in(self, name, scope=None):
        """有效定义所在的层（shadow 解析的证据）：exact scope -> 祖先 -> global。"""
        if scope is not None:
            for key in self._layers.scope_chain_of(scope):
                layer = self._layers.scoped.get(key)
                if layer is not None and name in layer.tools:
                    return f"scope:{key}"
        if name in self._layer.tools:
            return "global"
        return None

    # ---------- 作用域感知执行 ----------

    def execute(self, exec, scope=None):
        """作用域感知执行：按 scope 沿链解析有效工具后走 ch04 守卫管线。

        scope=None 时与 ch04 行为一致（全局层）。
        [教学简化] 用实例属性把 scope 透传给 _dispatch_body；真实代码把 scope
        放进执行上下文（AsyncLocalStorage）。
        """
        self._exec_scope = scope
        try:
            return super().execute(exec)
        finally:
            self._exec_scope = None

    def _dispatch_body(self, exec):
        """覆盖 ch04：从有效层（作用域链 shadow）解析工具，而非单一全局层。"""
        scope = getattr(self, "_exec_scope", None)
        tool = self.get(exec.name, scope)
        if tool is None:
            return ToolResult(content=f"工具 {exec.name} 未注册", is_error=True)
        try:
            content = tool.execute(exec.arguments)
            return ToolResult(content=str(content))
        except Exception as e:  # noqa: BLE001  教学版统一兜底
            return ToolResult(content=f"工具执行失败：{e}", is_error=True)
