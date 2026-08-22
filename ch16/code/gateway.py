"""第 16 章教学重构：RPC 网关（packages/api/gateway）。

源码对应：
- RpcConnection            ↔ connection.rpc.intercept('/api', …)（api/gateway/src/index.ts:104-111）
- TypertGatewayService     ↔ TypertGatewayService（api/gateway/src/index.ts:90）
- LookupStore              ↔ LookupStore（typert/registry/src/service.ts:216）
- GATEWAY_ERROR_CODES      ↔ TypertGatewayErrorCode（api/gateway/src/types.ts:19-36）

[教学简化] 真实网关拦截的是 HTTP/JSON-RPC 请求（authority 'trusted-host'）；
教学版用进程内函数调用模拟同一条「拦截 → 认领 → 派发」链路。
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ch01" / "code"))
from cordis import Service, ServiceNotFoundError  # noqa: E402

# 复用本章 typert.py 的描述符与 codec 常量
from typert import (  # noqa: E402
    CODEC_SRC_JSON, InvocationDescriptor, InvocationParameterDescriptor, typert_endpoint,
)

__all__ = [
    "GATEWAY_ERROR_CODES", "GatewayError", "LookupStore", "RpcConnection",
    "TypertGatewayService", "remote",
]

# 17 个稳定错误码 ↔ TypertGatewayErrorCode（types.ts:19-36），逐一对应
GATEWAY_ERROR_CODES = (
    "ambiguous-endpoint", "arguments-invalid", "binding-invalid",
    "context-failed", "context-not-found", "context-unavailable",
    "definition-unavailable", "input-invalid", "invocation-unavailable",
    "lookup-failed", "lookup-not-found", "lookup-unavailable",
    "method-unavailable", "provider-mismatch", "result-invalid",
    "service-unavailable", "signature-invalid",
)


class GatewayError(Exception):
    """网关失败：code 必出自 GATEWAY_ERROR_CODES，消费端按稳定码分支。"""

    def __init__(self, code, message):
        if code not in GATEWAY_ERROR_CODES:
            raise ValueError(f"未知错误码: {code}")
        super().__init__(message)
        self.code = code


def remote(fn):
    """@remote 标记 ↔ @Remote 标记：声明该方法可被跨进程导出。

    SRC 弱解析路径靠它识别导出方法（见 TypertGatewayService._resolve_descriptor）。
    给函数对象挂属性是合法的 Python 用法（functools 等标准库同样这么做）。
    """
    setattr(fn, "__typert_remote__", True)
    return fn


class RpcConnection:
    """[教学简化] 模拟 connection.rpc：intercept(prefix, claim, dispatch)。

    真实是 HTTP/JSON-RPC 连接，网关以 authority 'trusted-host' 拦截全部
    /api/* 端点（index.ts:104-111）；教学版用进程内调用，保留「先认领、
    后派发」两步——没有人认领的端点直接拒绝。
    """

    def __init__(self):
        self._routes = []  # (prefix, claim, dispatch) 三元组

    def intercept(self, prefix, claim, dispatch):
        """登记一个拦截器：prefix 匹配且 claim(endpoint) 为真时交给 dispatch。"""
        self._routes.append((prefix, claim, dispatch))

    def call(self, endpoint, payload):
        """外部调用入口：endpoint 形如 '/api/shellExecutor/run'，payload 是 JSON 安全 dict。

        剥掉前缀后把 'shellExecutor/run' 交给认领它的拦截器；无人认领即失败。
        """
        for prefix, claim, dispatch in self._routes:
            base = f"/{prefix.strip('/')}/"
            if endpoint.startswith(base):
                rest = endpoint[len(base):]          # 'shellExecutor/run'
                if claim(rest):
                    return dispatch(rest, payload)
        raise GatewayError("invocation-unavailable", f"端点无人认领: {endpoint}")


class LookupStore:
    """lookup provider ↔ LookupStore（service.ts:216）：宿主对象按身份登记。

    宿主对象（如一个 agent 实例）无法跨进程；wire 上只传身份字段（字符串），
    网关在调用前把身份换回宿主对象（见 TypertGatewayService._resolve_parameter）。
    """

    def __init__(self):
        self._objects = {}  # (kind, identity) -> 宿主对象

    def configure(self, kind, identity, obj):
        """登记一个宿主对象的身份 ↔ typert.lookups.configure（组合可覆写解析策略）。"""
        self._objects[(kind, identity)] = obj

    def resolve(self, kind, identity):
        """把身份换回宿主对象；查不到即 lookup-not-found。"""
        try:
            return self._objects[(kind, identity)]
        except KeyError:
            raise GatewayError("lookup-not-found", f"身份 {kind}:{identity} 查不到宿主对象")


class TypertGatewayService(Service):
    """RPC 网关 ↔ TypertGatewayService（index.ts:90），@typert service typertGateway。

    inject = ['typert']：只依赖注册表契约（ctx.typert），不依赖 typert 包的实现。
    """

    inject = ["typert"]

    def __init__(self, ctx, connection, name="typertGateway"):
        super().__init__(ctx, name)
        self.connection = connection
        self.lookups = LookupStore()
        self._src = {}  # namespace -> 对象（typertRemote 绑定，SRC 弱解析路径用）
        # 拦截全部 /api 端点（index.ts:104-111；真实 authority 为 'trusted-host'）
        connection.intercept("/api", self._claims_endpoint, self._dispatch)

    # ---------- 认领与路由 ----------

    def _claims_endpoint(self, endpoint):
        """是否认领该端点 ↔ claimsEndpoint（index.ts:99-120 摘录）。

        端点必须恰为两段（namespace/method）；注册表 local 查得到、或曾经
        注册过（hasSeen）、或 SRC 绑定存在，才认领。
        """
        segments = endpoint.split("/")
        if len(segments) != 2 or segments[0] == "" or segments[1] == "":
            return False
        if self.ctx.typert.get(endpoint) is not None or self.ctx.typert.has_seen(endpoint):
            return True
        return segments[0] in self._src

    def bind_src(self, namespace, obj):
        """绑定 SRC 侧对象（教学版的 typertRemote 绑定）。

        这类对象不进注册表；调用时由网关现场反射出描述符（见 _resolve_descriptor）。
        """
        self._src[namespace] = obj

    def _dispatch(self, endpoint, payload):
        """把 'namespace/method' + payload 组装成 InvokeRemoteRequest 再走 invoke。

        ↔ InvokeRemoteRequest（types.ts:7）：namespace / method / args（教学版省去 signal）。
        """
        namespace, _, method = endpoint.partition("/")
        return self.invoke({"namespace": namespace, "method": method, "args": payload})

    # ---------- 一次 invoke ----------

    def invoke(self, request):
        """↔ invoke（index.ts:145）：解析描述符 → 校验参数 → 取接收者 → 执行 → 校验结果。

        五步全部通过才返回业务结果；任一步失败都抛带稳定错误码的 GatewayError。
        """
        endpoint = typert_endpoint(request["namespace"], request["method"])
        descriptor = self._resolve_descriptor(endpoint)
        if descriptor is None:
            raise GatewayError("definition-unavailable", f"解析不到 {endpoint} 的描述符")
        self._assert_exact_arguments(request.get("args", {}), descriptor)
        receiver = self._resolve_receiver(descriptor)
        kwargs = {}
        for param in descriptor.parameters:
            kwargs[param.name] = self._resolve_parameter(param, request["args"])
        result = getattr(receiver, descriptor.method)(**kwargs)
        # 结果 codec 校验：教学版 strict / src-json 统一为 JSON 安全检查
        self._assert_json_value(result, "结果", code="result-invalid")
        return result

    def _resolve_descriptor(self, endpoint):
        """两条描述符解析路径：

        - strict：查注册表 local ↔ resolveDescriptor（index.ts:224）；
        - SRC 弱解析：typertRemote 绑定 + @remote 标记 + 签名反射 ↔
          resolveSrcDescriptor（:237）+ srcDescriptor（:265）。真实代码用
          Function.prototype.toString 反射参数名，教学版用 inspect.signature。
        """
        descriptor = self.ctx.typert.get(endpoint)
        if descriptor is not None:
            return descriptor
        namespace, _, method = endpoint.partition("/")
        obj = self._src.get(namespace)
        if obj is None:
            return None
        fn = getattr(obj, method, None)
        if fn is None or not getattr(fn, "__typert_remote__", False):
            raise GatewayError("method-unavailable", f"{endpoint} 未标记 @remote")
        names = [n for n in inspect.signature(fn).parameters if n != "self"]
        params = tuple(InvocationParameterDescriptor(n, schema="src-json") for n in names)
        return InvocationDescriptor(id=f"src:{endpoint}", service="", namespace=namespace,
                                    method=method, parameters=params, result=CODEC_SRC_JSON)

    def _resolve_receiver(self, descriptor):
        """取接收者 ↔ resolveReceiverContext（index.ts:359）+ ctx.get(descriptor.service)。"""
        if descriptor.service == "":
            return self._src[descriptor.namespace]  # SRC 路径：接收者就是绑定对象本身
        try:
            return getattr(self.ctx, descriptor.service)
        except ServiceNotFoundError:
            raise GatewayError("service-unavailable", f"服务未提供: {descriptor.service}")

    def _assert_exact_arguments(self, args, descriptor):
        """↔ assertExactArguments（index.ts:586）：参数名必须与描述符完全一致 + JSON 安全边界。

        拼错、多传、漏传都在边界被拦下，不会被拖进业务代码深处才炸。
        """
        expected = {p.name for p in descriptor.parameters}
        actual = set(args)
        if actual != expected:
            raise GatewayError("arguments-invalid",
                               f"期望参数 {sorted(expected)}，实际 {sorted(actual)}")
        for name, value in args.items():
            self._assert_json_value(value, name)

    def _assert_json_value(self, value, label, code="arguments-invalid"):
        """JSON 安全边界 ↔ assertJsonValue（index.ts:640）：只有可 JSON 序列化的值能上 wire。"""
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            raise GatewayError(code, f"{label} 不是 JSON 安全值")

    def _resolve_parameter(self, param, args):
        """↔ resolveParameter（index.ts:407）：普通参数直传；lookup 参数把身份换回宿主对象。"""
        value = args[param.name]
        if param.lookup is not None:
            return self.lookups.resolve(param.lookup, value)
        return value
