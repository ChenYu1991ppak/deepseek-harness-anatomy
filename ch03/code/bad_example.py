"""反面示例：第 2 章的 Session 只活在内存——进程退出即丢，冷读无门。

仅标准库，Python 3.10+。运行：python3 bad_example.py
用两个函数模拟两次进程生命周期：
进程 1 跑一轮对话（事件只进内存日志）；进程 2 用全新容器冷读同一个 session。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
_CH02 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch02", "code"))
for _p in (_CH01, _CH02):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Context  # noqa: E402
from agent_loop import AgentLoop, LlmStub, Sessions, SystemPromptService  # noqa: E402


def run_first_process():
    """进程 1：装配容器、跑一轮对话，事件全部留在内存日志。"""
    ctx = Context()
    Sessions(ctx)
    ctx.provide("llm", LlmStub())
    SystemPromptService(ctx)
    ctx.plugin(AgentLoop)
    agent = ctx.agent_loop.create()
    agent.followup("什么是 Cordis？")
    print(f"进程 1：session={agent.session.id}，内存日志 {len(agent.session.log)} 条事件")
    print("进程 1：进程退出——容器销毁，日志随之消失")
    return agent.session.id


def run_second_process(session_id):
    """进程 2：全新容器，尝试冷读上一个进程留下的会话。"""
    ctx = Context()
    Sessions(ctx)
    session = ctx.sessions.get(session_id)
    print(f"进程 2：冷读 {session_id} -> {session}")
    print("进程 2：注册表空空如也——那场对话「从未发生过」")


if __name__ == "__main__":
    run_second_process(run_first_process())
