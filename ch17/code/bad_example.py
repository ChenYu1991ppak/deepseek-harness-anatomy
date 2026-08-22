"""第 17 章反例：没有 workflow 引擎和共享 boot，你得手写多少胶水。

两个问题：
  问题 1 —— 手工编排多 agent：顺序写死在代码里，异常直接炸给调用方，
            没有统一的 result/stopReason/dispose 契约，也没有事件可观察。
  问题 2 —— 三条入口各写一份启动清单：改一处忘另一处，清单漂移。

直接运行：python3 ch17/code/bad_example.py
"""


# ---------- 问题 1：手工编排多 agent ----------

def orchestrate_manual(objective, workers):
    """没有 workflow 引擎时的「编排」：手写调用顺序 + 手写错误处理。

    对比 main.py 段 1：真实 workflow 引擎把失败折叠进
    result.stopReason=error，调用方只查结果对象；这里异常直接外抛，
    每个调用点都得自己包 try/except，也没有 cancel/dispose/事件可查。
    """
    plan = workers["planner"](f"拆解目标: {objective}")
    code = workers["coder"](f"按 [{plan}] 实现: {objective}")
    # 没有 materialize 边界：worker 返回什么脏东西都能流到下游
    return {"plan": plan, "code": code}


def demo_pain1():
    print("=" * 60)
    print("问题 1  手工编排多 agent")
    print("=" * 60)

    def flaky_worker(prompt):
        raise RuntimeError("子任务失败（模拟）")

    workers = {"planner": lambda p: f"计划({p[:6]}...)", "coder": flaky_worker}
    try:
        orchestrate_manual("实现天气查询", workers)
    except RuntimeError as e:
        print(f"  异常直接炸给调用方: {e}")
    print("  -> 没有 result 对象收口，没有 stopReason，没有事件轨迹，")
    print("     每个调用点都得自己包 try/except——这就是缺 workflow seam 的代价。")


# ---------- 问题 2：三条入口各维护一份启动清单 ----------

PYTHON_SERVICES = ["session", "agent-loop", "system-prompt", "tools", "sdk-runtime"]
CLI_SERVICES = ["session", "agent-loop", "system-prompt", "tools", "terminal-ui"]
# 某次迭代，有人在 web 清单里漏写了 "system-prompt" —— 漂移发生
WEB_SERVICES = ["session", "agent-loop", "tools", "server", "api-proxy"]

EXPECTED_BASE = ["session", "agent-loop", "system-prompt", "tools"]


def demo_pain2():
    print()
    print("=" * 60)
    print("问题 2  三条入口各写一份启动清单")
    print("=" * 60)
    for name, services in (("python-sdk", PYTHON_SERVICES),
                           ("cli", CLI_SERVICES),
                           ("web", WEB_SERVICES)):
        missing = [s for s in EXPECTED_BASE if s not in services]
        status = f"缺失 {missing}" if missing else "OK"
        print(f"  {name:>10}: {status}")
    print("  -> web 清单漏了 system-prompt：三份清单各自维护，必然漂移")
    print("  -> 正解：一份 boot + profile（见 main.py 段 3）")


if __name__ == "__main__":
    demo_pain1()
    demo_pain2()
