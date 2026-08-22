"""第 4 章演示：tools 注册与执行管线。

仅标准库，Python 3.10+。运行：python3 main.py

演示段落：
1. 装配容器 + 注册工具（触发 tools/change 事件）
2. schema 注入：wireSchemas 白名单投影 + 三种展示模式（presentAs）
3. 守卫管线：pre-execute 拒绝 / 单调守卫 / execute 超时 / post-execute 拦截
4. 反注册：注销工具定义
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
_CH02 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch02", "code"))
for _p in (_HERE, _CH02, _CH01):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Context  # noqa: E402
from agent_loop import AgentLoop, LlmStub, Sessions, SystemPromptService  # noqa: E402
from tools import (  # noqa: E402
    PostToolDecision,
    PreToolDecision,
    ToolDefinition,
    ToolExecution,
    ToolResult,
    ToolRuntime,
)


def main():
    # -- 1. 装配容器 + 注册工具 --
    print("== 1. 装配容器 + 注册工具 ==")
    ctx = Context()
    Sessions(ctx)
    ctx.provide("llm", LlmStub())
    SystemPromptService(ctx)
    ToolRuntime(ctx)  # 注册为 ctx.tools（Service 构造即注册）

    # 观察者：监听 tools/change 通知（复用 ch01 的 on/emit，只读事件流）
    ctx.on("tools/change", lambda change: print(f"  [tools/change] {change['name']} {change['op']}"))
    ctx.on("tools/result", lambda exec_, result: print(f"  [tools/result] {exec_.name} is_error={result.is_error}"))

    def read_file(args):
        with open(args["path"], "r", encoding="utf-8") as f:
            return f.read()

    def run_shell(args):
        import subprocess
        return subprocess.run(args["cmd"], shell=True, capture_output=True, text=True).stdout

    def slow_echo(args):
        time.sleep(0.05)  # 50ms，制造超时
        return args["text"]

    def echo(args):
        return args["text"]

    ctx.tools.register(ToolDefinition("read_file", "读文件", {"path": "string"}, read_file))
    ctx.tools.register(ToolDefinition("shell", "执行 shell 命令", {"cmd": "string"}, run_shell))
    ctx.tools.register(ToolDefinition("slow_echo", "慢速回声（演示超时）", {"text": "string"}, slow_echo, timeout_ms=20))
    ctx.tools.register(ToolDefinition("echo", "回声", {"text": "string"}, echo))

    # -- 2. schema 注入 --
    print("\n== 2. schema 注入：wireSchemas 白名单投影 ==")
    print("  wireSchemas 原始结果：")
    print(f"    {ctx.tools.wire_schemas()}")
    print("  presentAs('shell', 'code') 后，shell 从展示面隐藏：")
    ctx.tools.present_as("shell", "code")
    print(f"    knownNames = {ctx.tools.wire_schemas()['knownNames']}")
    ctx.systemPrompt.section("tools", ctx.tools.render_schemas(), order=10)
    print("  注入 system-prompt 的 section 文本：")
    print("    " + ctx.tools.render_schemas().replace("\n", "\n    "))

    # -- 3. 守卫管线 --
    print("\n== 3. 守卫管线：pre-execute / execute / post-execute ==")

    # pre-execute：拒绝敏感路径（index.ts:1475 兜底 allow）
    def sensitive_path_guard(exec_, next):
        if exec_.name == "read_file" and exec_.arguments.get("path", "").startswith("/etc"):
            return PreToolDecision.deny("敏感路径 /etc 被拒绝")
        return next()

    ctx.tools.on_pre_execute(sensitive_path_guard)

    # 单调守卫：只能拒绝（index.ts:1110 guard, 1119 guardReason 定义）
    ctx.tools.guard(lambda exec_: "禁止读取 /root/secret.txt" if exec_.arguments.get("path") == "/root/secret.txt" else None)

    # execute：超时环绕（packages/guard/timeout-policy/src/index.ts:56）
    def timeout_wrapper(exec_, next):
        tool = ctx.tools.get(exec_.name)
        budget = tool.timeout_ms if tool and tool.timeout_ms else None
        if budget is None:
            return next()
        start = time.monotonic()
        result = next()
        if (time.monotonic() - start) * 1000 > budget:
            return ToolResult(content=f"timeout: 超过 {budget}ms", is_error=True)
        return result

    ctx.tools.on_execute(timeout_wrapper)

    # post-execute：拦截含 secret 的结果（packages/guard/repeat-tool-reminder 同源扩展点）
    def secret_blocker(exec_, result, next):
        if "secret" in result.content:
            return PostToolDecision.block("结果包含 secret，已拦截")
        return next()

    ctx.tools.on_post_execute(secret_blocker)

    def run(name, args):
        exec_ = ToolExecution(call_id=f"call-{name}", name=name, arguments=args)
        result = ctx.tools.execute(exec_)
        print(f"  {name}{args} -> is_error={result.is_error} content={result.content!r}")
        return result

    import tempfile

    tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt")
    tmp.write("hello from a safe file\n")
    tmp.close()

    run("read_file", {"path": "/etc/hostname"})       # pre-execute 拒绝（敏感路径）
    run("read_file", {"path": "/root/secret.txt"})    # 单调守卫拒绝
    run("shell", {"cmd": "ls"})                        # code 态折叠拒绝
    run("slow_echo", {"text": "hi"})                   # execute 超时
    run("echo", {"text": "my secret key"})             # post-execute 拦截
    run("read_file", {"path": tmp.name})               # 正常通过
    run("echo", {"text": "all good"})                  # 正常通过

    os.remove(tmp.name)

    # -- 4. 反注册 --
    print("\n== 4. 反注册：注销工具定义 ==")
    dispose = ctx.tools.register(ToolDefinition("temp_tool", "临时工具", {}, lambda args: "temp"))
    print(f"  temp_tool 已注册，knownNames={ctx.tools.wire_schemas()['knownNames']}")
    dispose()
    print(f"  注销后 knownNames={ctx.tools.wire_schemas()['knownNames']}")


if __name__ == "__main__":
    main()
