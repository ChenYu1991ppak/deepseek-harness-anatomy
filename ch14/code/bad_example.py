"""第 14 章反面示例：没有介入/授权/协同规划机制。

运行：python3 bad_example.py

场景一：没有审批接缝——危险命令直接执行，人没有机会拦。
场景二：没有 goal 状态——多轮对话失去方向，目标只存在于聊天记录里。
"""


def scene_no_approval():
    print("场景一：没有审批接缝")
    tool_calls = [
        {"name": "shell", "args": {"cmd": "make build"}},
        {"name": "shell", "args": {"cmd": "rm -rf /"}},  # 危险调用
    ]
    for call in tool_calls:
        # 朴素分派：拿到调用直接执行，没有「问人」这一步
        print(f"  执行 {call['name']}: {call['args']['cmd']} → 完成")
    print("  → 危险命令直接执行了，人没有机会拦")


def scene_no_goal():
    print("场景二：没有 goal 状态")
    print("  用户：完成 utils 模块重构")
    print("  agent：好的，开始……")
    print("  用户：（打断）先帮我看个 bug")
    print("  agent：bug 修好了。")
    print("  用户：继续刚才的任务")
    print("  agent：刚才的任务是什么来着？（目标只存在于聊天记录，压缩后就丢了）")


def main():
    scene_no_approval()
    print()
    scene_no_goal()


if __name__ == "__main__":
    main()
