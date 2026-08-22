"""第 16 章可运行入口：typert 类型图 → 注册表 → RPC 网关 → JSON-RPC SDK。

运行：python3 ch16/code/main.py
复用第 1 章 cordis.py 的 Context / Service（经 sys.path，见各模块文件头）。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                  # 本章模块：typert / gateway / sdk
sys.path.insert(0, str(HERE.parents[1] / "ch01" / "code"))  # 第 1 章 cordis.py

from cordis import Context, Service  # noqa: E402
from gateway import (  # noqa: E402
    GATEWAY_ERROR_CODES, GatewayError, RpcConnection, TypertGatewayService, remote,
)
from sdk import DeepSeekHarness, HarnessSdkJsonRpcServer  # noqa: E402
from typert import TypertError, TypertRegistry, generate_contribution, typert_endpoint  # noqa: E402


def banner(title):
    print(f"\n== {title} ==")


# ---------- 生产端：两个待导出的服务（呼应第 5 章 capability seam 的词汇） ----------

class ShellExecutorStub(Service):
    """第 5 章 capability seam 里 shellExecutor 的教学 stub。

    [教学决策] 不 import 第 5 章代码；shell seam 的词汇类型
    （ShellExecRequest / ShellRunResult）用普通 dict 表示，保持本章自包含。
    """

    service_key = "shellExecutor"
    typert_exports = ("run",)  # ./typert 导出声明：哪些方法要跨进程

    def __init__(self, ctx):
        super().__init__(ctx, self.service_key)

    def run(self, request: dict) -> dict:
        return {"exitCode": 0, "stdout": f"[stub] 执行: {request['command']}"}


class AgentInfo:
    """宿主对象：只存在于宿主进程，wire 上只传它的身份字段。"""

    def __init__(self, identity, name):
        self.identity = identity
        self.name = name


class SessionService(Service):
    """带 lookup 参数的服务：summarize 的 agent 参数是宿主对象。"""

    service_key = "sessionService"
    typert_exports = ("summarize",)
    typert_lookups = {"summarize": {"agent": "agent"}}  # 方法 -> 参数 -> lookup 种类

    def __init__(self, ctx):
        super().__init__(ctx, self.service_key)

    def summarize(self, agent, note: str) -> dict:
        # 走到这里时，agent 已被网关从身份字段换回宿主对象
        return {"agent": agent.name, "summary": f"{agent.name}: {note}"}


class DiagnosticsBridge:
    """SRC 侧对象：不进注册表，方法用 @remote 标记。"""

    @remote
    def ping(self, tag: str) -> dict:
        return {"pong": tag}

    def internal(self):  # 未标记 @remote：不可导出
        return "secret"


def main():
    # 装配：容器 + 连接 + 注册表 + 网关 + 两个服务 + 一个宿主对象 + 一个 SRC 绑定
    ctx = Context()
    connection = RpcConnection()
    TypertRegistry(ctx)                                  # ctx.typert
    gateway = TypertGatewayService(ctx, connection)      # ctx.typertGateway，inject=['typert']
    ShellExecutorStub(ctx)                               # ctx.shellExecutor
    SessionService(ctx)                                  # ctx.sessionService
    agent = AgentInfo("agent-1", "writer")
    gateway.lookups.configure("agent", agent.identity, agent)
    gateway.bind_src("diagnostics", DiagnosticsBridge())

    banner("段 1：生成器 —— 把服务契约编译成类型图")
    contrib_shell, graph = generate_contribution(ShellExecutorStub, package="demo-shell")
    descriptor = contrib_shell.invocations[0]
    print(f"类型图节点数: {len(graph.nodes)}，命名声明数: {len(graph.declarations)}")
    print(f"调用描述符: id={descriptor.id}")
    print(f"  service={descriptor.service} "
          f"endpoint={typert_endpoint(descriptor.namespace, descriptor.method)}")
    print(f"  参数={[(p.name, p.schema) for p in descriptor.parameters]} 结果 codec={descriptor.result}")

    banner("段 2：注册表 —— 原子注册与回滚")
    dispose_shell = ctx.typert.register(contrib_shell)
    contrib_session, _ = generate_contribution(SessionService, package="demo-session")
    ctx.typert.register(contrib_session)
    print(f"在册 endpoint: {ctx.typert.endpoints()}")
    try:
        ctx.typert.register(contrib_shell)  # 同一个包重复注册
    except TypertError as exc:
        print(f"重复注册被拒: TypertError: {exc}")
    dispose_shell()  # 调 disposer 撤销 shell 这条 contribution
    print(f"dispose 后 shellExecutor/run -> {ctx.typert.get('shellExecutor/run')}")
    print(f"hasSeen 仍记得: {ctx.typert.has_seen('shellExecutor/run')}")
    ctx.typert.register(contrib_shell)  # 重新注册（相当于 fiber 重载路径）
    print(f"重新注册后: {ctx.typert.endpoints()}")

    banner("段 3：网关 strict 路径 —— 一次 /api 调用")
    result = connection.call("/api/shellExecutor/run", {"request": {"command": "ls -l"}})
    print(f"调用 /api/shellExecutor/run -> {result}")
    for label, args in (
        ("拼错参数名", {"reqeust": {"command": "ls"}}),
        ("多传参数", {"request": {"command": "ls"}, "verbose": True}),
    ):
        try:
            connection.call("/api/shellExecutor/run", args)
        except GatewayError as exc:
            print(f"{label}被边界拦下 -> {exc.code}: {exc}")
    print(f"网关错误码表共 {len(GATEWAY_ERROR_CODES)} 个（见 GATEWAY_ERROR_CODES）")

    banner("段 4：lookup provider —— 身份字段换回宿主对象")
    wire_args = {"agent": "agent-1", "note": "写完第 16 章初稿"}
    print(f"wire 上只有身份: args = {wire_args}")
    result = connection.call("/api/sessionService/summarize", wire_args)
    print(f"网关换回宿主对象后调用 -> {result}")
    try:
        connection.call("/api/sessionService/summarize", {"agent": "agent-404", "note": "x"})
    except GatewayError as exc:
        print(f"未知身份 -> {exc.code}: {exc}")

    banner("段 5：SRC 弱解析 —— 无注册表，现场反射出描述符")
    result = connection.call("/api/diagnostics/ping", {"tag": "hello"})
    print(f"调用 /api/diagnostics/ping -> {result}")
    try:
        connection.call("/api/diagnostics/internal", {})
    except GatewayError as exc:
        print(f"未标记方法被拒 -> {exc.code}: {exc}")

    banner("段 6：JSON-RPC SDK —— 行帧 + 三请求 + 通知")

    def echo_model(text):
        yield f"echo: {text}"
        yield "（完成）"

    def make_server(transport):
        return HarnessSdkJsonRpcServer(transport, echo_model)

    harness = DeepSeekHarness(make_server).start()  # start 内部自动 initialize
    outcome = harness.session().prompt("跨进程调用")
    print(f"session.prompt 收到的 chunk: {outcome['chunks']}")
    print(f"finalResponse: {outcome['finalResponse']}")
    harness.shutdown()
    print(f"销毁阶梯: {harness.destroy_trace}（优雅退出，未升级）")


if __name__ == "__main__":
    main()
