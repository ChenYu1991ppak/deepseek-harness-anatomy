"""第 5 章反面示例：没有 capability seam 时的耦合写法。

仅标准库，Python 3.10+。运行：python3 bad_example.py

[教学定位] 用于 §1 的问题场景：如果消费者直接 import subprocess 跑 bash，
「在哪台机器跑」「怎么渲染退出码」全焊死在消费者体内，换 provider 必须改消费者。
对比正解：消费者只面向 ctx.shell 三个方法，换 provider 一行不改（见 main.py 第 4 段）。
"""
import subprocess


def run_bash_naive(command: str) -> str:
    """把「执行 bash」的实现细节直接写死在消费者里。

    问题：
    1. 换 provider（本地 → 沙箱/远程）必须改这一行，且所有调用点都要跟着改；
    2. 退出码/超时/取消的语义散落在调用方，没有统一的 marker 契约；
    3. 无法在组合期二选一 provider，也无法 mock 做测试。
    """
    p = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
    return (p.stdout or "") + (p.stderr or "") + f"\n[exit code: {p.returncode}]"


if __name__ == "__main__":
    print(run_bash_naive("echo naive-no-seam"))
