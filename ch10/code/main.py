"""第 10 章教学入口：装配 + 完整运行输出。

装配关系（对应 notes §1 seam 三角色）：
- SurfaceSessions(ctx)           → ctx.sessions（会话服务，创建 SurfaceSession）
- LlmRuntime + FakeCompactionAdapter → ctx.llm（LLM 运行时，生成摘要）
- BasicCompactionEngine(ctx, …)  → ctx.compactionEngine（compaction 提供者）

运行：python3 ch10/code/main.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
_CH02 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch02", "code"))
_CH07 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch07", "code"))
for _p in (_CH01, _CH02, _CH07):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Context  # noqa: E402  (ch01)
from agent_loop import Sessions  # noqa: E402  (ch02)
from llm_runtime import LlmRuntime, LlmAdapter  # noqa: E402  (ch07)
from surface import SurfaceSession  # noqa: E402  (本章)
from compaction import (  # noqa: E402  (本章)
    BasicCompactionEngine,
    measure_session,
    resolve_compact_spec,
    select_compactable_range,
    tool_pairing_balanced_before,
)


class FakeCompactionAdapter(LlmAdapter):
    """假 adapter：压缩请求（purpose='compaction'）返回摘要，其余返回普通回复。"""

    def provider_info(self):
        return {"id": "fake", "name": "Fake"}

    def stream(self, options):
        messages = options.get("messages", [])
        if options.get("purpose") == "compaction":
            # 最后一条是 COMPACTION_INSTRUCTION，其余是压缩区间消息
            n = len(messages) - 1
            summary = (f"压缩了 {n} 条对话：讨论了项目方案，"
                       "确认了技术选型，决定继续推进任务。")
        else:
            summary = "这是一条普通回复。"
        yield {"type": "block-start", "index": 0, "block_type": "text"}
        yield {"type": "text-delta", "index": 0, "text": summary}
        yield {"type": "block-end", "index": 0,
               "block": {"type": "text", "text": summary}}
        yield {"type": "finish", "reason": "stop"}


class SurfaceSessions(Sessions):
    """创建 SurfaceSession 的会话服务（扩展 ch02 Sessions，只覆写 create）。"""

    def create(self):
        self._next_id += 1
        session = SurfaceSession(self.ctx, f"session-{self._next_id:04d}")
        self._sessions[session.id] = session
        return session


# ---- 追加 surface 事件的小工具 ----

def add_user(session, text):
    session.append("user/message", {"message": {"role": "user", "content": text}})


def add_assistant(session, text):
    session.append("assistant/message",
                   {"message": {"role": "assistant",
                                "content": [{"type": "text", "text": text}]}})


def add_tool_call(session, name):
    session.append("assistant/message",
                   {"message": {"role": "assistant",
                                "content": [{"type": "tool-call",
                                             "id": f"c{len(session.log)}",
                                             "name": name}]}})


def add_tool_result(session, text):
    session.append("tool/result", {"message": {"role": "tool", "content": text}})


def preview(message):
    """取一条消息的内容预览（文本块拼接，非文本块显示类型）。"""
    content = message.get("content")
    if isinstance(content, list):
        return " | ".join((b.get("text") or f"[{b.get('type')}]") for b in content)
    return str(content)


def print_surface(session, label):
    print(f"\n[{label}]")
    measurement = measure_session(session)
    print(f"  surface 节点数={len(session.surface)}  "
          f"token 压力={measurement['total_tokens']}  "
          f"replace_generation={session.replace_generation}")
    print("  模型将看到的消息：")
    for message in session.surface_messages():
        print(f"    - {message.get('role')}: {preview(message)[:42]}")


def main():
    print("=" * 62)
    print("第 10 章：compaction 与 token 压力")
    print("=" * 62)

    # ---- 装配 ----
    ctx = Context()
    SurfaceSessions(ctx)                          # ctx.sessions
    runtime = LlmRuntime()
    runtime.register_adapter(FakeCompactionAdapter())
    ctx.provide("llm", runtime)                   # ctx.llm
    context_window = 500                          # [教学简化] 缩小窗口便于演示
    engine = BasicCompactionEngine(ctx, context_window)  # ctx.compactionEngine
    spec = resolve_compact_spec(context_window)
    print(f"\n装配：context_window={context_window}  "
          f"threshold={spec['threshold']}  retain_tokens={spec['retain_tokens']}")

    # ---- 1. 会话增长，压力累积 ----
    session = ctx.sessions.create()
    for i in range(6):
        add_user(session, f"问题{i}：请讲解模块{i}的设计 " + "细节 " * 40)
        add_assistant(session, f"回答{i}：模块{i}的设计如下 " + "分析 " * 40)
    print_surface(session, "压缩前：会话已增长")

    # ---- 2. 压力触发 ----
    print("\n[压力触发] engine.compact_if_needed(session, trigger='pressure')")
    result = engine.compact_if_needed(session, trigger="pressure")
    if result is None:
        print("  压力未达阈值，不压缩")
    else:
        print(f"  压缩完成：区间 [{result['start']}, {result['end']}) 被替换为摘要节点")
        print(f"  摘要内容：{result['summary']}")
    print_surface(session, "压缩后：surface 被替换")

    # ---- 3. log 完整：compaction/* 事件 log-only ----
    print("\n[log 完整] log 中的事件（compaction/* 只进 log、不进 surface）：")
    surface_seqs = set(session.surface)
    for event in session.log:
        if event.seq in surface_seqs:
            where = "surface"
        elif event.type.startswith("compaction/"):
            # compaction/* 事件类型上从不进 surface（log-only）
            where = "log-only"
        else:
            # user/message、assistant/message 本是 surface 类型事件，
            # 只是被压缩替换移出了 surface——与「类型 log-only」不同
            where = "已移出 surface"
        print(f"  seq={event.seq:>2}  {event.type:<20} ({where})")

    # ---- 4. 工具配对余额：切点不得切断工具调用/结果对 ----
    print("\n[工具配对余额] 切点必须落在配对平衡点")
    demo_tool_pairing(ctx)

    print("\n" + "=" * 62)
    print("运行完成")


def demo_tool_pairing(ctx):
    """聚焦示例：tentative 切点落在工具对中间，算法回退到平衡点。"""
    session = ctx.sessions.create()
    add_user(session, "请读取文件")                          # surface[0]
    add_tool_call(session, "read_file")                      # surface[1]（1 个工具调用）
    add_tool_result(session, "文件内容 " + "数据 " * 6)      # surface[2]
    add_assistant(session, "已读取文件 " + "总结 " * 4)      # surface[3]

    measurement = measure_session(session)
    nodes = measurement["nodes"]
    # 让 retain_tokens = 尾部两节点之和 → tentative 切点落在 2（工具调用与结果之间）
    retain = nodes[2]["tokens"] + nodes[3]["tokens"]
    print(f"  surface 有 {len(nodes)} 个节点，各节点 tokens="
          f"{[nd['tokens'] for nd in nodes]}")
    print(f"  retain_tokens={retain} → tentative 切点=2（工具调用与它的结果之间）")
    print(f"  balance(2)={tool_pairing_balanced_before(session, 2)}（≠0，落在工具对中间）")
    region = select_compactable_range(session, measurement, retain)
    print(f"  回退后切点 end={region['end']}，"
          f"balance({region['end']})={tool_pairing_balanced_before(session, region['end'])}（安全）")
    print(f"  → 压缩 [0, {region['end']})，保留工具调用/结果对完整")


if __name__ == "__main__":
    main()
