"""第 12 章问题反例：专业能力直接焊进系统提示词——全量常驻、不可发现、不可插拔。

仅标准库，Python 3.10+。运行：python3 bad_example.py

没有 skill 机制时，想让 agent 会做「代码评审」「写提交信息」「生成 release notes」
这类需要专门步骤的事，只有两条路，都把「能力」和「装配」焊死：

路 1（本反例）：把所有专门知识塞进系统提示词。每次会话、不管用不用得上，全量常驻
      上下文，token 越滚越大，模型注意力被稀释。
路 2：把某个能力焊进单个工具实现。知识与工具耦合，模型无法发现、无法复用，
      加一个新能力就要改代码重新装配。

反例演示路 1：把三个技能的正文全部拼进系统提示词，然后跑一个只需要
「写提交信息」的简单任务——其余两个技能的知识白白常驻、白白花钱。
"""

# 三个「技能」的正文：真实项目里这类专门步骤会越积越多。
SKILLS = {
    "commit-message": (
        "步骤：1. 读 git diff 归纳主题；2. 选 type：feat/fix/docs/refactor；"
        "3. 输出 type(scope): 描述，祈使句、不超过 72 字符。"
    ),
    "code-review": (
        "评审要点：1. 错误处理是否完整；2. 边界条件是否覆盖；"
        "3. 命名是否表达意图；4. 是否有重复代码可抽取。"
    ),
    "release-notes": (
        "步骤：1. 汇总上次 tag 以来的 PR；2. 按 feat/fix/breaking 分类；"
        "3. 渲染成面向用户的 release notes。"
    ),
}


def build_system_prompt():
    """把全部技能正文焊进系统提示词：不管本次任务用不用得上，全量常驻。"""
    parts = ["你是一个编码助手。以下是你必须随时记住的全部专业技能："]
    for name, body in SKILLS.items():
        parts.append(f"[技能 {name}] {body}")
    return "\n".join(parts)


def main():
    system_prompt = build_system_prompt()
    task = "帮我把这次改动写一条提交信息。"

    print("=== 反例：技能全量焊进系统提示词 ===")
    print(f"系统提示词长度：{len(system_prompt)} 字符（含全部 {len(SKILLS)} 个技能正文）")
    print(f"本次任务：{task}")
    print("→ 任务只需要 commit-message，但 code-review / release-notes 的正文也全程常驻。")
    print()
    print("--- 实际发给模型的系统提示词 ---")
    print(system_prompt)
    print()
    print("坑：")
    print("1. 全量常驻：用不上的技能正文也占上下文，技能越多、会话越长，滚得越大。")
    print("2. 不可发现：模型不知道有哪些能力、何时该用，只能靠提示词硬灌。")
    print("3. 不可插拔：加 / 删一个技能要改系统提示词（甚至改代码）重新装配。")


if __name__ == "__main__":
    main()
