"""第 9 章演示：scope 作用域注册——同一进程里多个 agent 如何隔离各自能力。

演示六件事：
1. 注册全局工具（scope=None）；
2. per-agent 作用域注册：writer 铸造自己的作用域，注册同名 echo shadow 全局；
3. shadowing 生效：同一名字，不同作用域执行到不同实现，defined_in 给出来源；
4. lineage 作用域链：writer-helper 绑为 writer 的子作用域，继承 writer + 全局的注册；
5. restriction：给 writer 单调禁用 read_file——writer 看不见，子作用域仍可见（不继承）；
6. 注册即效应：register 返回 disposer，调用即注销；ctx.dispose() 全部回收。

运行：python3 main.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
_CH04 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch04", "code"))
for _p in (_HERE, _CH04, _CH01):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Context  # noqa: E402
from scope import ScopedToolRuntime  # noqa: E402
from tools import ToolDefinition, ToolExecution  # noqa: E402

_TEXT_PARAM = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def main():
    ctx = Context()
    runtime = ScopedToolRuntime(ctx)

    # 订阅 tools/change，把注册 / 注销 / 限制记到日志
    changes = []
    ctx.on("tools/change", lambda payload: changes.append(payload))

    # ── 1. 注册全局工具（scope=None）──
    runtime.register(ToolDefinition(
        name="echo",
        description="原样回显输入",
        parameters=_TEXT_PARAM,
        execute=lambda args: f"[global] {args['text']}",
    ))
    runtime.register(ToolDefinition(
        name="read_file",
        description="读取文件内容",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"]},
        execute=lambda args: f"读取 {args['path']}",
    ))
    print("1. 全局 view:", [d.name for d in runtime.view()])

    # ── 2. per-agent 作用域注册 + shadow ──
    # writer 铸造自己的作用域：注册一个带写作口吻的 echo（shadow 全局 echo）
    runtime.register(ToolDefinition(
        name="echo",
        description="写作口吻回显",
        parameters=_TEXT_PARAM,
        execute=lambda args: f"[writer] {args['text']}",
    ), scope="writer")
    # writer 专属工具：全局层没有
    runtime.register(ToolDefinition(
        name="draft_outline",
        description="为主题起草大纲",
        parameters={"type": "object",
                    "properties": {"topic": {"type": "string"}},
                    "required": ["topic"]},
        execute=lambda args: f"为「{args['topic']}」起草大纲",
    ), scope="writer")
    print("2. 全局 view:", [d.name for d in runtime.view()])
    print("   writer view:", [d.name for d in runtime.view("writer")])

    # ── 3. shadowing 生效 ──
    print("3. 全局执行 echo:",
          runtime.execute(ToolExecution(call_id="e1", name="echo",
                                        arguments={"text": "hi"})).content)
    print("   writer 执行 echo:",
          runtime.execute(ToolExecution(call_id="e2", name="echo",
                                        arguments={"text": "hi"}),
                          scope="writer").content)
    print("   writer 的 echo 定义在:", runtime.defined_in("echo", scope="writer"))
    print("   writer 的 read_file 定义在:", runtime.defined_in("read_file", scope="writer"))

    # ── 4. lineage 作用域链：writer-helper 是 writer 的子作用域 ──
    runtime.bind_scope_parent("writer-helper", "writer")
    runtime.register(ToolDefinition(
        name="summarize",
        description="汇总要点",
        parameters=_TEXT_PARAM,
        execute=lambda args: f"汇总：{args['text']}",
    ), scope="writer-helper")
    print("4. writer-helper 谱系链:", runtime.scope_chain_of("writer-helper"))
    print("   writer-helper view（继承 writer + 全局）:",
          [d.name for d in runtime.view("writer-helper")])

    # ── 5. restriction：exact-scope 单调黑名单，不继承 ──
    runtime.restrict(["read_file"], scope="writer")
    print("5. restrict 后 writer view:", [d.name for d in runtime.view("writer")])
    print("   restrict 后 writer-helper view（不受父级限制影响）:",
          [d.name for d in runtime.view("writer-helper")])
    print("   restrict 后全局 view:", [d.name for d in runtime.view()])

    # ── 6. 注册即效应：disposer 注销 ──
    dispose_temp = runtime.register(ToolDefinition(
        name="temp_tool",
        description="临时工具",
        parameters=_TEXT_PARAM,
        execute=lambda args: "temp",
    ), scope="writer")
    print("6. 注册 temp_tool 后 writer view:", [d.name for d in runtime.view("writer")])
    dispose_temp()
    print("   调用 disposer 后 writer view:", [d.name for d in runtime.view("writer")])

    # ── 收尾：事件日志 + 容器销毁 ──
    print("tools/change 事件数:", len(changes))
    ctx.dispose()
    print("dispose 后全局 view:", [d.name for d in runtime.view()])


if __name__ == "__main__":
    main()
