"""第 7 章反面示例：没有适配器 seam —— 模型调用焊死，各家流式协议手工对接。

仅标准库，Python 3.10+。运行：python3 bad_example.py
"""


def provider_a_complete(prompt):
    """Provider A 的私有接口：同步、整段返回。"""
    return f"[A] 对「{prompt}」的回答"


def provider_b_stream(prompt):
    """Provider B 的私有接口：流式、字段名完全不同（piece 而非 text）。"""
    for piece in (f"[B] 对「{prompt}」的回答", "（流式到达）"):
        yield {"piece": piece}


def answer_via_a(question):
    # 调用点焊死在 Provider A：同步、整段文本
    return provider_a_complete(question)


def answer_via_b(question):
    # 换到 Provider B：调用点必须重写，字段名、拼装方式全不同
    parts = []
    for event in provider_b_stream(question):
        parts.append(event["piece"])  # 字段名是 piece，不是 text
    return "".join(parts)


if __name__ == "__main__":
    print("Provider A:", answer_via_a("什么是 agent？"))
    print("Provider B:", answer_via_b("什么是 agent？"))
    print("问题：每接一家 provider 就写一套调用代码，换 provider = 重写调用点")
