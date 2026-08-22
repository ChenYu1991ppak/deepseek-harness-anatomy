"""ch17 教学代码：Python 内嵌面（对应 dsh-sdk / python/sdk）。

真实源码（17b 笔记）：
- `DeepSeekHarness` 类定义在 `python/sdk/src/deepseek_harness/api.py:48`，
  是 Python 侧的总入口；`Session.run()` 在 `api.py:132`，
  通过 stdio JSON-RPC 与 Node runtime（单文件 Node 可执行）通信。
- `HarnessClient` 在 `python/sdk/src/deepseek_harness/client.py:37`，
  负责连接管理。
- Python 面与 CLI/Web 面的收敛点：最终都走 `agents.create` + `agent.followup`
  （17b 笔记「收敛点」节）。

[教学简化] 真实链路是 Python → stdio JSON-RPC → Node runtime → agent-loop；
这里把 runtime 换成一个纯 Python 的迷你 agent-loop（第 2 章机制的 20 行版），
模型换成 FakeModel，不经过网络/进程间通信，但保留「模型 → 工具 → 模型」
的两段式往返结构。
[教学决策] 保留 `create_agent` / `run` 的命名，与 SDK 的
`DeepSeekHarness` / `Session.run()` 对应，方便读者建立映射。
"""


class FakeModel:
    """[教学简化] 确定性假模型：按脚本逐轮返回，用于演示 agent-loop 往返。

    第 1 轮返回工具调用（模拟模型决定调工具）；
    第 2 轮返回最终文本（模拟模型看到工具结果后收尾）。
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def generate(self, messages):
        self.calls += 1
        return self.script.pop(0)


def create_agent(model, tools):
    """创建一个迷你 agent（对应 agents.create 收敛点）。"""
    return {"model": model, "tools": tools, "messages": []}


def run(agent, user_input):
    """迷你版 Session.run()：跑完「模型 → 工具 → 模型」循环直到出最终文本。

    对应第 2 章 agent-loop 的骨架：
    while True: response = model.generate(); 有 tool_call 就执行并回填，
    否则返回最终文本。[教学简化] 省略 stop_reason 解析与错误重试。
    """
    agent["messages"].append({"role": "user", "content": user_input})
    while True:
        response = agent["model"].generate(agent["messages"])
        if response.get("tool_call"):
            name = response["tool_call"]["name"]
            args = response["tool_call"].get("args", {})
            tool = agent["tools"][name]
            result = tool(**args)
            agent["messages"].append({"role": "tool", "name": name, "content": result})
            continue
        agent["messages"].append({"role": "assistant", "content": response["text"]})
        return response["text"]
