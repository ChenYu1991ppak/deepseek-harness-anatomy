"""第 8 章问题演示：系统提示词是硬编码大字符串。

运行：python3 ch08/code/bad_example.py

各功能模块把自己的文本直接拼进全局大字符串：
- 顺序由「谁后来」决定，无法按重要性排布；
- 字符串一旦拼上就取不下来，模块卸载也带着它的文本；
- 工具 schema 混进文本，模型读到的是一串 JSON 字符串而非结构化字段；
- 重复注册直接造成内容重复。

正文 §1 用它引出问题；正文后续机制节逐一解决。
"""

SYSTEM_PROMPT = "You are an AI assistant."  # 唯一的初始文本


def register_safety(prompt):
    """安全模块：直接拼到末尾，位置由注册先后决定。"""
    return prompt + "\n\n[安全策略] 不要泄露敏感信息。"


def register_persona(prompt):
    """人格设定：本应在最前，但只能拼到末尾。"""
    return prompt + "\n\n[人格] 你是严谨的代码评审员。"


def register_tools(prompt):
    """工具 schema：也混进文本，模型读到的是一串 JSON 字符串。"""
    schema = {"name": "read_file", "parameters": {"path": "string"}}
    return prompt + f"\n\n[工具] {schema}"


def main():
    prompt = SYSTEM_PROMPT
    prompt = register_safety(prompt)
    prompt = register_persona(prompt)
    prompt = register_tools(prompt)

    print("最终提示词（注意：人格设定本应在最前，实际排在最后）：")
    print(prompt)
    print()

    # 问题 2：模块卸载无效——文本已经焊死在字符串里
    print("假设安全模块现在卸载……")
    print("大字符串里仍含安全策略:", "[安全策略]" in prompt)
    print()

    # 问题 2：重复注册造成内容重复，且无从检测
    prompt2 = register_safety(prompt)
    print("安全模块重复注册后，安全策略出现次数:",
          prompt2.count("[安全策略]"))


if __name__ == "__main__":
    main()
