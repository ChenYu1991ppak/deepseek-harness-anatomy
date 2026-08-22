"""反例：只有一张全局工具表——多个 agent 共享，互相污染。

真实项目里多个 agent（main / research / writer ...）同进程协作（第 11 章 subagent）；
若工具表只有全局一份、没有作用域：
- 任一 agent 注册的工具，所有 agent 都看得见；
- 两个 agent 想要同名工具的不同实现，只能互相覆盖；
- 想给某个 agent 临时收起某工具，只能全局删除。
运行：python3 bad_example.py
"""

GLOBAL_TOOLS = {}  # 唯一的全局工具表：没有作用域、没有 shadow、没有 restrict


def register(name, fn):
    """注册工具：一旦注册，所有 agent 可见。"""
    GLOBAL_TOOLS[name] = fn


def view():
    """任何 agent 看到的都是同一张表。"""
    return dict(GLOBAL_TOOLS)


def main():
    # main agent 注册一个通用 echo
    register("echo", lambda args: f"[global] {args['text']}")
    print("main 注册 echo 后，writer 也看见了:", sorted(view()))

    # writer 想要一个带写作口吻的 echo，但只能覆盖全局——main 的 echo 一起没了
    register("echo", lambda args: f"[writer] {args['text']}")
    print("writer 覆盖 echo 后，执行到的是:", view()["echo"]({"text": "hi"}))

    # research 想禁用 echo，只能全局删除——writer 的 echo 也跟着消失
    del GLOBAL_TOOLS["echo"]
    print("research 全局删除 echo 后:", sorted(view()))
    print("结论：一张全局表，没法让每个 agent「有自己的工具」")


if __name__ == "__main__":
    main()
