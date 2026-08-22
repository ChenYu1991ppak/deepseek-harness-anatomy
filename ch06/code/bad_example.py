"""第 6 章反面示例：没有执行世界三件套，agent 直接上手标准库。

仅标准库，Python 3.10+。运行：python3 bad_example.py

三个坑对应本章三件套：
① 进程管不好 → ctx.subprocess；② 文件管不好 → ctx.fs；③ 没有隔离 → ctx.sandbox。
"""
import subprocess

# 坑 ① 进程管不好：失败即异常，退出码没被当成数据
try:
    subprocess.run(["bash", "-c", "exit 3"], check=True, capture_output=True)
except subprocess.CalledProcessError as e:
    print(f"坑①: 失败抛 {type(e).__name__}，模型拿不到干净的退出码")
# 而且：make 派生 gcc/cc1 时，kill 直接子进程杀不掉整棵树；后台任务无登记，卸载即泄漏

# 坑 ② 文件管不好：错误格式不统一，各是各的异常
try:
    open("no_such_dir/config.json").read()
except OSError as e:
    print(f"坑②: {type(e).__name__}，而不是统一的 [FS_NOT_FOUND] 错误码")

# 坑 ③ 没有隔离：危险命令无人设防
print("坑③: 没有 sandbox，rm -rf / 这类命令没有围栏")
