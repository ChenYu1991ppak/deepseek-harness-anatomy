"""第 4 章反面示例：写死的工具调用——没有注册、没有 schema 展示、没有守卫。

运行：python3 bad_example.py（读取同目录下的 sample.txt，内容固定，任何平台可复现）
"""
import os


def read_file(path: str) -> str:
    """读文件（真正的工具体）。"""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def run_shell(cmd: str) -> str:
    """执行 shell 命令（危险工具体，应当被守卫拦截）。"""
    import subprocess

    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


# 问题 1：工具散落在模块顶层，模型和容器都不知道它们存在。
# 问题 2：没有任何 schema 注入 prompt——模型无从得知有哪些工具、参数长什么样。
# 问题 3：调用点写死、无守卫——危险命令畅通无阻，也无法超时/提醒。


def naive_agent_dispatch(intent: str):
    """把模型的「意图」直接映射到 Python 函数：写死的分发，没有管线。"""
    if intent.startswith("read:"):
        return read_file(intent[len("read:"):])
    if intent.startswith("shell:"):
        return run_shell(intent[len("shell:"):])
    return "我不认识这个意图"


if __name__ == "__main__":
    # 读脚本同目录下的样例文件：内容固定为「hello from ch04」，输出在任何平台可复现。
    sample_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample.txt")
    # 模型说出「我要读 sample.txt」→ 直接调函数；模型说出「我要跑命令」→ 也直接调。
    print("[bad_example] read:", naive_agent_dispatch(f"read:{sample_path}").strip())
    print("[bad_example] shell:", naive_agent_dispatch("shell:echo hi").strip())
