"""第 16 章教学重构：typert —— 类型图 + 运行时注册表。

仅标准库，Python 3.10+。源码对应：
- TypeNode / TypeGraph           ↔ TypeNodeModel / TypeGraph（packages/typert/generator/src/model.ts:350/:430）
- InvocationDescriptor           ↔ InvocationDescriptor（packages/typert/protocol/src/types.ts:172-211）
- CODEC_STRICT / CODEC_SRC_JSON  ↔ TypertCodec（packages/typert/protocol/src/types.ts:139）
- typert_key / typert_endpoint   ↔ typertKey / typertEndpoint（packages/typert/registry/src/service.ts:48/:67）
- TypertRegistry                 ↔ TypertRegistry（packages/typert/registry/src/service.ts:446）
- generate_contribution          ↔ WorkspaceTypertGenerator.generate（packages/typert/generator/src/workspace.ts:46）

[教学简化] 真实 TypeNodeModel 有 18 个 variant；教学版 variant 只取 primitive / object，
再用 detail 细分 string/number/boolean/null。真实注册表有 local/remote 两套
DescriptorStore，另有 RemoteStore / LookupStore / ContextStore；教学版只保留 local，
lookup 一块移到 gateway.py 里讲。
"""
from __future__ import annotations

import inspect
import sys
import typing
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ch01" / "code"))
from cordis import Service  # noqa: E402  复用第 1 章 Service：构造即注册

__all__ = [
    "CODEC_SRC_JSON", "CODEC_STRICT", "InvocationDescriptor",
    "InvocationParameterDescriptor", "TypeGraph", "TypeNode",
    "TypertContribution", "TypertError", "TypertRegistry",
    "generate_contribution", "typert_endpoint", "typert_key",
]


class TypertError(Exception):
    """typert 教学版的统一错误：导出校验、注册校验失败时抛出。"""


# ---------- 类型图：与编译器解耦的载体无关中间模型 ----------

class TypeNode:
    """类型图节点 ↔ TypeNodeModel（model.ts:350）。

    [教学简化] 真实模型有 18 个 variant（primitive/object/union/literal 等）；
    教学版 variant 只取 primitive / object，detail 再细分 string/number 等。
    """

    __slots__ = ("id", "variant", "detail")

    def __init__(self, id, variant, detail=None):
        self.id = id            # 图内节点 id（真实代码靠它遍历子节点，model.ts:394）
        self.variant = variant  # 节点种类：primitive / object
        self.detail = detail    # 细分：string / number / boolean / null / lookup:<kind>

    def __repr__(self):
        return f"TypeNode({self.id}, {self.variant}, {self.detail!r})"


class TypeGraph:
    """类型图整体 ↔ TypeGraph（model.ts:430）：declarations + nodes。

    它是生成器的产出物：可序列化、可跨进程——类型契约从此不依赖任何一门源语言。
    """

    __slots__ = ("declarations", "nodes")

    def __init__(self):
        self.declarations = {}  # schema key -> TypeNode：命名类型（↔ TypeDeclarationModel）
        self.nodes = {}         # id -> TypeNode：全部节点


# ---------- 编解码策略与两种 key ----------

# 编解码策略 ↔ TypertCodec（types.ts:139）
CODEC_STRICT = "strict"        # 严格：按 schema 校验（真实用 Zod v4，loader/src/index.ts:264）
CODEC_SRC_JSON = "src-json"    # 弱解析：只做 JSON 安全校验，用于无生成器场景


def typert_key(package, name):
    """schema 标识 ↔ typertKey（service.ts:48）：<pkg>#<name>。"""
    return f"{package}#{name}"


def typert_endpoint(namespace, method):
    """可调用端点标识 ↔ typertEndpoint（service.ts:67）：<namespace>/<method>。"""
    return f"{namespace}/{method}"


# ---------- 调用描述符：一条导出方法的载体无关描述 ----------

@dataclass(frozen=True)
class InvocationParameterDescriptor:
    """导出方法的单个参数：名字 + 指向类型图的 schema + 是否为宿主对象。"""

    name: str
    schema: str                 # 指向类型图里的类型节点（<pkg>#<name> 形式）
    lookup: str | None = None   # None = 普通 JSON 参数；否则是 lookup 种类（如 "agent"）：
                                # wire 上只传身份字段，网关调用前换回宿主对象


@dataclass(frozen=True)
class InvocationDescriptor:
    """一条被导出的 RPC 方法的载体无关描述 ↔ InvocationDescriptor（types.ts:172-211）。

    [教学简化] 真实字段还有 implementation / invocation / scope / cancellation /
    sourceLocation；教学版保留 id / service / namespace / method / parameters / result。
    """

    id: str                    # 全局稳定生成标识
    service: str               # 拥有该方法的 Cordis service key（对应第 5 章 Service Definition 的 ctx.<key>）
    namespace: str             # wire 命名空间，默认等于 service key
    method: str                # 公共实例方法名
    parameters: tuple          # InvocationParameterDescriptor 序列
    result: str                # 结果 codec：CODEC_STRICT / CODEC_SRC_JSON


@dataclass
class TypertContribution:
    """一个包向注册表提交的全部内容：包记录 + schemas + 调用描述符。"""

    package: str
    schemas: dict              # schema key -> TypeNode
    invocations: list          # InvocationDescriptor 列表


# ---------- 生成器：从服务类到 contribution ----------

# 注解 → 类型 detail。生成器据此做「类型分析」：真实代码是编译器分析 TS 源码，
# 教学版用 Python 注解反射，二者产出同一种中间模型（类型图）。
_ANNOTATIONS = {str: "string", int: "number", bool: "boolean", dict: "object", list: "object"}


def generate_contribution(cls, package, namespace=None):
    """教学版生成器：反射类的导出方法，产出类型图 + 调用描述符。

    ↔ WorkspaceTypertGenerator：discover（workspace.ts:33）→ generate（:46）→
    validateExport（:68，校验 ./typert / ./remote 导出）。
    [教学简化] 教学版用 inspect 反射 Python 签名，并把类属性 typert_exports
    当作「./typert 导出声明」：没声明就拒绝生成，对应真实 validateExport 的校验。
    返回 (contribution, graph)：前者提交给注册表，后者供观察类型图形状。
    """
    exports = getattr(cls, "typert_exports", None)
    if not exports:
        raise TypertError(f"{cls.__name__} 未声明 ./typert 导出（缺 typert_exports）")
    lookups = getattr(cls, "typert_lookups", {})
    service_key = getattr(cls, "service_key", None) or namespace or cls.__name__
    namespace = namespace or service_key
    graph = TypeGraph()
    invocations = []
    for method_name in exports:
        fn = getattr(cls, method_name, None)
        if fn is None or not callable(fn):
            raise TypertError(f"{cls.__name__}.{method_name} 不存在或不可调用")
        hints = typing.get_type_hints(fn)  # 解析注解（含字符串形式）为真实类型
        params = []
        for pname in list(inspect.signature(fn).parameters)[1:]:  # 跳过 self
            lookup_kind = lookups.get(method_name, {}).get(pname)
            if lookup_kind is not None:
                # lookup 参数：类型是宿主对象，wire 上只传身份字段（一个字符串）
                node = TypeNode(len(graph.nodes), "object", f"lookup:{lookup_kind}")
            else:
                detail = _ANNOTATIONS.get(hints.get(pname))
                if detail is None:
                    raise TypertError(f"{cls.__name__}.{method_name} 的参数 {pname} 缺少注解")
                variant = "object" if detail == "object" else "primitive"
                node = TypeNode(len(graph.nodes), variant, detail)
            schema_key = typert_key(package, f"{cls.__name__}.{method_name}.{pname}")
            graph.nodes[node.id] = node
            graph.declarations[schema_key] = node
            params.append(InvocationParameterDescriptor(pname, schema_key, lookup_kind))
        invocations.append(InvocationDescriptor(
            id=typert_key(package, f"{cls.__name__}.{method_name}"),
            service=service_key, namespace=namespace, method=method_name,
            parameters=tuple(params), result=CODEC_STRICT,
        ))
    return TypertContribution(package, dict(graph.declarations), invocations), graph


# ---------- 运行时注册表 ----------

class TypertRegistry(Service):
    """运行时注册表 ↔ TypertRegistry（service.ts:446），@typert service typert。

    持有三张表：packages（包记录）/ schemas（typertKey -> 类型节点）/
    local（endpoint -> 描述符，即 DescriptorStore 的 local 侧，service.ts:107）。
    以 name="typert" 注册为 Cordis 服务：构造后 ctx.typert 即指向本实例。
    """

    def __init__(self, ctx, name="typert"):
        super().__init__(ctx, name)   # 第 1 章 Service：构造即注册
        self.packages = {}            # package -> 包记录
        self.schemas = {}             # typertKey -> TypeNode
        self._local = {}              # endpoint -> InvocationDescriptor
        self._seen = set()            # 曾注册过的 endpoint（卸载后仍保留）

    # -- 消费端查询（供网关使用，对应依赖倒置契约的 local 段） --

    def get(self, endpoint):
        """按 endpoint 查描述符；未注册返回 None。"""
        return self._local.get(endpoint)

    def has_seen(self, endpoint):
        """该 endpoint 是否「曾经」注册过：dispose 后记录没了，见过的事实还在。

        网关的认领判断要用它（见 gateway.py 的 _claims_endpoint）。
        """
        return endpoint in self._seen

    def endpoints(self):
        """当前在册的全部 endpoint（教学观察用）。"""
        return sorted(self._local)

    # -- 生产端注册 --

    def register(self, contribution):
        """原子注册一个 generated contribution ↔ register（service.ts:499）。

        先做三道校验（对应 validatePackage / validateSchemas / localStore.validate），
        全部通过后才进 ctx.effect 提交：一次性写入 packages / schemas / local，
        并返回 disposer——调用它即撤销本 contribution 的全部记录
        （对应真实 effect 里 yield 的清理函数）。
        """
        if contribution.package in self.packages:
            raise TypertError(f"包已注册: {contribution.package}")
        for descriptor in contribution.invocations:
            endpoint = typert_endpoint(descriptor.namespace, descriptor.method)
            if endpoint in self._local:
                raise TypertError(f"endpoint 已被占用: {endpoint}")
            for param in descriptor.parameters:
                if param.schema not in contribution.schemas:
                    raise TypertError(f"缺少 schema: {param.schema}")

        def commit():
            # 走到这里说明校验全过：三张表一次性写入，不出现「写了一半」
            self.packages[contribution.package] = {
                "schemas": len(contribution.schemas),
                "invocations": len(contribution.invocations),
            }
            for key, node in contribution.schemas.items():
                self.schemas[key] = node
            for descriptor in contribution.invocations:
                endpoint = typert_endpoint(descriptor.namespace, descriptor.method)
                self._local[endpoint] = descriptor
                self._seen.add(endpoint)

            def dispose():
                # 撤销本 contribution 的全部记录：包、schemas、local 描述符
                for descriptor in contribution.invocations:
                    self._local.pop(typert_endpoint(descriptor.namespace, descriptor.method), None)
                for key in contribution.schemas:
                    self.schemas.pop(key, None)
                self.packages.pop(contribution.package, None)

            return dispose

        return self.ctx.effect(commit, label=f"typert:register:{contribution.package}")
