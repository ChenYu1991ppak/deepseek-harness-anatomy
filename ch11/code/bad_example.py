"""第 11 章反例：子任务内联在主会话里执行。

仅标准库，Python 3.10+。运行：python3 bad_example.py

[教学定位] 用于 §1 的问题场景展示，三个坑：
① 子任务的探索过程全部涌进主会话，主对话上下文被淹没；
② 子任务的执行方式焊死在调用点——想换「带历史 fork」「独立进程」必须改调用代码；
③ 子任务还能再嵌套子任务，递归没有任何深度约束。
正确对照：子任务委派给子 agent，子 agent 用自己的会话独立执行，只有结果回传（见 main.py）。
"""

MAIN_SESSION = []  # 主会话：全局唯一，主对话与所有子任务共用


def run_subtask_inline(task, depth=0):
    """内联执行子任务：过程事件直接追加进主会话（坑①），调用方式焊死（坑②）。"""
    pad = "  " * depth
    MAIN_SESSION.append(f"{pad}[子任务] 开始 {task}")
    MAIN_SESSION.append(f"{pad}[子任务] 探索步骤 1：读日志……")
    MAIN_SESSION.append(f"{pad}[子任务] 探索步骤 2：复现失败……")
    if depth == 0:
        # 子任务再嵌套子任务：递归没有任何深度约束（坑③）
        run_subtask_inline(f"{task} 的子模块排查", depth + 1)
    result = f"{task} 的结论"
    MAIN_SESSION.append(f"{pad}[子任务] 结束 → {result}")
    return result


def main():
    MAIN_SESSION.append("[主对话] user: 总结一下项目进展")
    answer = run_subtask_inline("排查模块 A")
    MAIN_SESSION.append(f"[主对话] assistant: 项目进展正常（{answer}）")

    main_turns = sum(1 for line in MAIN_SESSION if line.startswith("[主对话]"))
    print(f"主会话共 {len(MAIN_SESSION)} 条事件，属于主对话的只有 {main_turns} 条：")
    for line in MAIN_SESSION:
        print("  " + line)
    print("想把「排查」换成独立进程执行？只能改 run_subtask_inline 的每一个调用点。")


if __name__ == "__main__":
    main()
