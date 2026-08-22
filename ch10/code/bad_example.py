"""第 10 章反面示例：无限增长的会话，必然撞上上下文窗口的墙。

agent 处理长任务（读大代码库、多轮工具调用）时，对话事件不断累积；
每一轮模型调用都要把全部历史带上，token 压力单调上涨。一旦超过
上下文窗口，模型拒绝接收输入——之前的所有上下文都送不进去了。

本文件用计量数字展示这个过程（复用第 7 章的 estimate_message）。

运行：python3 ch10/code/bad_example.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH07 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch07", "code"))
if _CH07 not in sys.path:
    sys.path.insert(0, _CH07)

from token_meter import estimate_message  # noqa: E402  (ch07)


def main():
    print("=" * 62)
    print("反面示例：无限增长的会话，必然撞上上下文窗口的墙")
    print("=" * 62)

    context_window = 500  # 教学示例把窗口缩小，便于看清过程
    print(f"\n假设上下文窗口只有 {context_window} tokens。")
    print("对话一轮轮进行，每轮追加一问一答：\n")

    total = 0
    turn = 0
    while total < context_window:
        turn += 1
        user_msg = {"role": "user", "content": f"问题{turn}：" + "细节 " * 20}
        assistant_msg = {"role": "assistant", "content": f"回答{turn}：" + "分析 " * 20}
        total += estimate_message(user_msg) + estimate_message(assistant_msg)
        print(f"  第{turn:>2}轮：累计 tokens = {total:>4} / 窗口 = {context_window}")

    print(f"\n→ 第 {turn} 轮后，累计 tokens {total} 已超过窗口 {context_window}。")
    print("  此刻模型拒绝接收入输入：这一轮之后的对话再也进行不下去。")
    print("  而之前说过的所有内容，也因为装不进窗口而无法交给模型。")
    print("\n这就是 compaction 要解决的问题：")
    print("  上下文窗口是有限的，但对话必须继续。")


if __name__ == "__main__":
    main()
