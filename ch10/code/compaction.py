"""第 10 章教学重构：compaction 引擎 —— token 压力下的上下文压缩。

仅标准库，Python 3.10+。

源码对应（行号取自 notes §2）：
- CompactionEngine             ↔ packages/core/compaction/src/index.ts:19（服务定义，三动词
                                  compactIfNeeded :21 / compactNow :24 / compactRegion :27）
- BasicCompactionEngine        ↔ packages/core/compaction-basic/src/index.ts:20（服务提供者）
- measure_session              ↔ tokenMeter.measure(session)（packages/llm/token-meter/src/index.ts:116）
- resolve_compact_spec         ↔ resolveCompactSpec（compaction-basic/index.ts:110）
- select_compactable_range     ↔ selectCompactableRange（compaction-basic/index.ts:122）
- tool_pairing_balanced_before ↔ toolPairingBalancedBefore（session/surface.ts:114）
- compact_region               ↔ compactSurfaceRegion（compaction-basic/index.ts:144）
- _summarize                   ↔ summarizeWithLlm（compaction-basic/index.ts:230）
- compact_checkpoint_source    ↔ compactCheckpointSource（compaction-basic/index.ts:106）

[教学简化] 真实版三动词接收 Agent（引擎经 agent 触达 session 与 runtime）；教学版直接传
session，机制语义不变。真实版摘要前会重放 system/tools 前缀（KV-cache 复用），教学版只发
压缩区间消息 + 压缩指令（见 _summarize 注释）。
"""
import os
import sys
from abc import ABC, abstractmethod

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH07 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch07", "code"))
if _CH07 not in sys.path:
    sys.path.insert(0, _CH07)

from token_meter import estimate_message  # noqa: E402  (ch07)
from llm_runtime import BlockAssembler  # noqa: E402  (ch07)

# 压缩指令：作为最后一条 user 消息追加，指示模型把对话压缩成摘要。
# 真实源：COMPACTION_INSTRUCTION（compaction-basic/index.ts:16）。
COMPACTION_INSTRUCTION = (
    "请把上面的对话压缩成一份简明摘要：保留关键决策、结论、工具执行结果与未完成任务，"
    "使对话可以基于这份摘要继续。"
)


def measure_session(session):
    """测量会话 surface 的 token 压力 → TokenMeasurement。

    扩展第 7 章计量缝：真实源 tokenMeter.measure(session)
    （token-meter/src/index.ts:116）返回 totalTokens + 逐节点计价 nodes；
    教学版复用第 7 章 estimate_message，对每个 surface 节点逐一计价。
    返回 {'total_tokens': int, 'nodes': [{'seq': int, 'tokens': int}]}.
    """
    nodes = []
    total = 0
    for seq in session.surface:
        event = session.log[seq]
        message = event.payload.get("message", {})
        tokens = estimate_message(message)
        nodes.append({"seq": seq, "tokens": tokens})
        total += tokens
    return {"total_tokens": total, "nodes": nodes}


def resolve_compact_spec(context_window, threshold_ratio=0.8, retain_ratio=0.16):
    """解析压缩参数（resolveCompactSpec，compaction-basic/index.ts:110）。

    - threshold：触发压缩的压力阈值 = context_window × threshold_ratio（默认 0.8）；
    - retain_tokens：压缩时尾部保留的 token 数 = context_window × retain_ratio（默认 0.16）。
    """
    return {
        "threshold": int(context_window * threshold_ratio),
        "retain_tokens": int(context_window * retain_ratio),
    }


def count_tool_calls(message):
    """统计一条 assistant 消息里 tool-call 内容块的个数。"""
    content = message.get("content")
    if not isinstance(content, list):
        return 0
    return sum(1 for block in content
               if isinstance(block, dict) and block.get("type") == "tool-call")


def tool_pairing_balanced_before(session, index):
    """工具配对余额（toolPairingBalancedBefore，session/surface.ts:114）：
    surface[:index] 内 assistant 工具调用数 − tool/result 数。

    == 0：切点之前的工具调用都有成对结果，可在此安全切开；
    != 0：切点落在一对工具调用/结果中间，必须回退。
    """
    balance = 0
    for seq in session.surface[:index]:
        event = session.log[seq]
        if event.type == "assistant/message":
            balance += count_tool_calls(event.payload.get("message", {}))
        elif event.type == "tool/result":
            balance -= 1
    return balance


def select_compactable_range(session, measurement, retain_tokens):
    """选出可压缩区间（selectCompactableRange，compaction-basic/index.ts:122）。

    步骤：
    1. 保留尾部：从尾向前累计 retain_tokens 的节点作为「保留尾」；
    2. 把切点回退到工具配对平衡点（balance == 0），避免切断工具调用/结果对；
    3. 返回待压缩区间 {'start': 0, 'end': cut}；无可压缩返回 None。
    """
    nodes = measurement["nodes"]
    n = len(nodes)
    if n == 0:
        return None
    # 1. 保留尾部：从尾向前累计，再加一个就超 retain_tokens 时停下
    tail_tokens = 0
    cut = n
    for i in range(n - 1, -1, -1):
        if tail_tokens + nodes[i]["tokens"] > retain_tokens:
            break
        tail_tokens += nodes[i]["tokens"]
        cut = i
    # 2. 回退到工具配对平衡点：balance(cut) != 0 就继续回退
    while cut > 0 and tool_pairing_balanced_before(session, cut) != 0:
        cut -= 1
    if cut <= 0:
        return None  # 无可压缩
    return {"start": 0, "end": cut}


def compact_checkpoint_source():
    """检查点来源标记（compactCheckpointSource，compaction-basic/index.ts:106）。"""
    return {"kind": "plugin", "plugin": "compact"}


# ---- 无模型修剪：tool-result-pruner ----

# 修剪标记：插在保留的头尾之间，标明中段被剪掉。
# 真实源：PRUNE_MARKER（compaction-tool-result-pruner/src/index.ts）。
PRUNE_MARKER = "\n…[中段已修剪]…\n"


def prune_tool_result(text, keep_chars=20):
    """无模型修剪（tool-result-pruner，compaction-tool-result-pruner/src/index.ts）。

    是什么：不调用 LLM，直接把超大的 tool/result 文本「掐头去尾留中间标记」。
    解决什么：单个工具结果（如读一个大文件）就可能撑爆上下文，为它专门跑一次
    摘要 LLM 调用既贵又慢；无模型修剪零成本、即时生效。

    [教学简化] 真实版按 Unicode code point 计量、逐候选节点评估，并走
    「compaction/prune 影子价 + tool/result replace」协议落盘；教学版只对单个
    字符串演示掐头去尾，协议与主压缩一致（log-only 事件 + surface 替换）。
    """
    if len(text) <= keep_chars * 2:
        return text  # 不够大，不修剪
    return text[:keep_chars] + PRUNE_MARKER + text[-keep_chars:]


class CompactionEngine(ABC):
    """compaction 服务定义（CompactionEngine，compaction/index.ts:19）。

    三动词：compact_if_needed / compact_now / compact_region。
    抽象类自身不注册服务——ctx.compactionEngine 是否存在由提供者决定，
    消费者须先判存在（这正是 seam 三角色的「定义不保证实现」）。
    """

    @abstractmethod
    def compact_if_needed(self, session, trigger):
        """压力触发：测压力、达阈值才压（index.ts:21）。"""

    @abstractmethod
    def compact_now(self, session, trigger):
        """立即压缩：/compact 命令走这条（index.ts:24）。"""

    @abstractmethod
    def compact_region(self, session, region, trigger):
        """压缩指定区间（index.ts:27）。"""


class BasicCompactionEngine(CompactionEngine):
    """compaction 服务提供者（BasicCompactionEngine，compaction-basic/index.ts:20）。"""

    def __init__(self, ctx, context_window, threshold_ratio=0.8, retain_ratio=0.16):
        self.ctx = ctx
        self.context_window = context_window
        self.threshold_ratio = threshold_ratio
        self.retain_ratio = retain_ratio
        self._active = False  # 压缩锁（assertCompactionInactive，index.ts:149）
        ctx.provide("compactionEngine", self)

    def compact_if_needed(self, session, trigger="pressure"):
        """压力触发：测 token 压力，达阈值才压缩；未达返回 None。"""
        measurement = measure_session(session)
        spec = resolve_compact_spec(self.context_window,
                                    self.threshold_ratio, self.retain_ratio)
        if measurement["total_tokens"] < spec["threshold"]:
            return None  # 压力未达，不压
        return self._compact(session, measurement, spec, trigger)

    def compact_now(self, session, trigger="manual"):
        """立即压缩（/compact 命令走这条，command-compact/index.ts:13）。"""
        measurement = measure_session(session)
        spec = resolve_compact_spec(self.context_window,
                                    self.threshold_ratio, self.retain_ratio)
        return self._compact(session, measurement, spec, trigger)

    def _compact(self, session, measurement, spec, trigger):
        """选区间并执行压缩事务；无可压缩区间返回 None。"""
        region = select_compactable_range(session, measurement, spec["retain_tokens"])
        if region is None:
            return None
        return self.compact_region(session, region, trigger)

    def compact_region(self, session, region, trigger):
        """单次压缩事务（compactSurfaceRegion，compaction-basic/index.ts:144）。

        上锁 → compaction/start → 摘要 → 稳定性断言 → compaction/summary
        → 提交（user/message + surfaceOp replace）→ compaction/end → 解锁。

        关键协议：compaction/* 事件全部 log-only（只进 log、不进 surface），
        真正替换 surface 的是最后那条携带 surfaceOp 的 user/message。
        """
        if self._active:
            raise RuntimeError("compaction already in progress (assertCompactionInactive)")
        start, end = region["start"], region["end"]
        self._active = True
        session.append("compaction/start", {"trigger": trigger})
        try:
            gen_before = session.replace_generation
            summary, shadow_price = self._summarize(session, region)
            # 稳定性断言（assertStable，compaction/index.ts:154）：摘要期间
            # surface 不得发生 replace。教学版同步单线程恒成立，真实异步场景是防线。
            if session.replace_generation != gen_before:
                raise RuntimeError("surface changed during compaction (assertStable)")
            # compaction/summary：log-only，携带摘要文本与影子价
            session.append("compaction/summary",
                           {"summary": summary, "shadowPrice": shadow_price})
            # 提交：追加携带 surfaceOp replace 的 user/message，真正替换 surface
            checkpoint_message = {
                "role": "user",
                "content": f"[对话摘要] {summary}",
                "checkpointSource": compact_checkpoint_source(),
            }
            session.append("user/message", {"message": checkpoint_message},
                           surface_op={"op": "replace", "start": start, "end": end})
        finally:
            session.append("compaction/end", {})
            self._active = False
        return {"start": start, "end": end, "summary": summary}

    def _summarize(self, session, region):
        """生成摘要（summarizeWithLlm，compaction-basic/index.ts:230）。

        把压缩区间消息 + COMPACTION_INSTRUCTION 交给 LLM，purpose='compaction'。
        返回 (summary_text, shadow_price)。

        [教学简化] 真实源会先重放 system/tools/messages 前缀再追加指令（复用
        KV-cache，降低摘要调用成本）；教学版只发压缩区间消息 + 指令。
        影子价 = 压缩区间 token 数（摘要「对冲」掉的部分）。
        """
        start, end = region["start"], region["end"]
        region_messages = []
        for seq in session.surface[start:end]:
            event = session.log[seq]
            message = event.payload.get("message")
            if message is not None:
                region_messages.append(message)
        shadow_price = sum(estimate_message(m) for m in region_messages)
        request_messages = region_messages + [
            {"role": "user", "content": COMPACTION_INSTRUCTION}
        ]
        assembler = BlockAssembler()
        for chunk in self.ctx.llm.stream(request_messages, purpose="compaction"):
            assembler.push(chunk)
        summary = "".join(
            block.get("text", "") for block in assembler.blocks
            if block.get("type") == "text"
        )
        return summary, shadow_price
