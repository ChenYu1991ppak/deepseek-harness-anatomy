"""第 1 章反面示例：没有容器时会发生什么。

两个 agent 共享一个全局消息列表、硬编码的"模型调用"，
彼此的对话历史互相污染。运行：python3 bad_example.py
"""
messages = []  # 全局消息列表：所有 agent 共享


def ask(agent, question):
    messages.append({"role": "user", "content": question})
    reply = f"{agent} 收到: " + "".join(m["content"] for m in messages)
    messages.append({"role": "assistant", "content": reply})
    print(f"[{agent}] {reply}")


ask("客服 agent", "怎么改签车票？")
ask("导购 agent", "推荐一款机械键盘")
