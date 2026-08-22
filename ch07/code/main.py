"""第 7 章演示：LLM 适配与流式。

仅标准库，Python 3.10+。运行：python3 main.py

演示段落：
1. 建容器 + 凭据/设置 seam（API key 进 credentials，流式设置进 settings）
2. 注册两个适配器（DeepSeek 桩 / Pi.AI 桩）→ list_providers
3. 统一流式入口：同一调用点，两个 provider（调用方一行不改）
4. BlockAssembler：delta 累积成完整块 → message()
5. 异常归一化：adapter 抛错 → finish(reason=error) 终止 chunk
6. 兑现第 2 章承诺：runtime 顶替 LlmStub 当 ctx.llm，第 2 章 AgentLoop 原样跑
7. TokenMeter：把 chunk 流折算成 usage
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
_CH02 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch02", "code"))
for _p in (_HERE, _CH02, _CH01):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Context  # noqa: E402
from agent_loop import AgentLoop, Sessions, SystemPromptService  # noqa: E402

from credentials import InMemoryCredentialProvider, InMemorySettingsProvider  # noqa: E402
from llm_runtime import BlockAssembler, LlmRuntime  # noqa: E402
from deepseek_adapter import DeepSeekStubAdapter  # noqa: E402
from pi_adapter import PiAiStubAdapter  # noqa: E402
from token_meter import TokenMeter, estimate_message  # noqa: E402


def main():
    # -- 1. 建容器 + 凭据/设置 seam --
    print("== 1. 凭据 / 设置 seam ==")
    ctx = Context()
    credentials = InMemoryCredentialProvider()
    settings = InMemorySettingsProvider()
    ctx.provide("credentials", credentials)
    ctx.provide("settings", settings)
    credentials.set("deepseek/api-key", "sk-demo-001", source="env")
    settings.register("llm/stream", {"idle_timeout_ms": 30000}, "流式设置")
    print(f"  describe(deepseek/api-key) -> {credentials.describe('deepseek/api-key')}")
    print(f"  settings.get(llm/stream)   -> {settings.get('llm/stream')}")

    # -- 2. 注册适配器 --
    print("\n== 2. 注册适配器：LlmRuntime 注册表 ==")
    runtime = LlmRuntime()
    deepseek_handle = runtime.register_adapter(DeepSeekStubAdapter(credentials))
    runtime.register_adapter(PiAiStubAdapter())
    runtime.register_adapter(PiAiStubAdapter(provider_id="pi-fail", fail=True))
    print(f"  list_providers() -> {runtime.list_providers()}")
    print(f"  register 返回句柄键 -> {sorted(deepseek_handle.keys())}")

    # -- 3. 统一流式入口：同一调用点，两个 provider --
    print("\n== 3. 统一流式入口：调用方一行不改，只换 provider ==")
    messages = [{"role": "user", "content": "你是谁？"}]
    for provider in ("deepseek", "pi-ai"):
        chunks = list(runtime.stream(messages, system_prompt="你是教学助手。", provider=provider))
        types = [chunk["type"] for chunk in chunks]
        print(f"  provider={provider}: {len(chunks)} chunks")
        print(f"    types={types}")

    # -- 4. BlockAssembler：delta → 完整块 --
    print("\n== 4. BlockAssembler：delta 累积成完整块 ==")
    assembler = BlockAssembler()
    for chunk in runtime.stream(messages, system_prompt="你是教学助手。", provider="deepseek"):
        assembler.push(chunk)
    print(f"  blocks -> {assembler.blocks}")
    print(f"  usage  -> {assembler.usage}")
    print(f"  finish -> {assembler.finish}")
    print(f"  message() -> {assembler.message()}")

    # -- 5. 异常归一化 --
    print("\n== 5. 异常归一化：异常 → finish(reason=error) ==")
    for chunk in runtime.stream(messages, provider="pi-fail"):
        print(f"  chunk -> {chunk}")

    # -- 6. 兑现第 2 章承诺：runtime 当 ctx.llm，第 2 章 AgentLoop 原样跑 --
    print("\n== 6. 兑现第 2 章承诺：runtime 顶替 LlmStub ==")
    Sessions(ctx)
    ctx.provide("llm", runtime)  # 第 2 章此处是 LlmStub；现在换成 runtime
    SystemPromptService(ctx)
    ctx.systemPrompt.section("identity", "你是一个最小教学 agent。", order=0)
    ctx.plugin(AgentLoop)  # inject = ["sessions", "llm"] 满足 → 加载
    agent = ctx.agent_loop.create()
    agent.followup("介绍一下 DeepSeek。")
    assistant = next(
        ev.payload["message"]["content"][0]["text"]
        for ev in agent.session.log
        if ev.type == "assistant/message"
    )
    chunk_count = sum(1 for ev in agent.session.log if ev.type == "assistant/chunk")
    print(f"  assistant/chunk 事件 {chunk_count} 条；assistant 消息：{assistant}")

    # -- 7. TokenMeter --
    print("\n== 7. TokenMeter：chunk 流折算 usage ==")
    meter = TokenMeter()
    usage = meter.measure(runtime.stream(messages, provider="deepseek"))
    print(f"  measure(stream) -> {usage}")
    sample = {"role": "user", "content": "你是谁？"}
    print(f"  estimate_message(user) -> {estimate_message(sample)} tokens")


if __name__ == "__main__":
    main()
