"""第 6 章教学桩：mock sandbox runner。

真实 runner（bwrap/Landlock/Seatbelt）会在 exec 前施加内核级隔离
（只读绑定挂载 / Landlock 规则集 / Seatbelt profile）。教学桩不施加隔离，
只解析并丢弃 '--' 之前的 profile 参数，然后原样 exec '--' 之后的 argv，
用来演示 confine 的包裹结构能被正常执行。

用法（由 ctx.sandbox.confine 组装，不直接调用）：
    python3 mock_runner.py --mode workspace-write --workspace /ws -- <真实 argv>
"""
from __future__ import annotations

import os
import sys


def main():
    argv = sys.argv[1:]
    if "--" not in argv:
        print("mock-runner: 缺少 '--' 分隔符", file=sys.stderr)
        return 125  # 与 landlock-run 一致：125 = 启动器自身失败
    sep = argv.index("--")
    _profile = argv[:sep]  # profile 参数：真实 runner 用它建隔离，教学桩忽略
    cmd = argv[sep + 1:]
    if not cmd:
        print("mock-runner: '--' 之后没有可执行命令", file=sys.stderr)
        return 125
    os.execvp(cmd[0], cmd)  # 替换自身为被隔离的命令
    return 125  # execvp 成功不会返回


if __name__ == "__main__":
    raise SystemExit(main())
