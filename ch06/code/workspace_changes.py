"""第 6 章教学重构：workspace/changes —— 改动意图与双快照。

仅标准库，Python 3.10+。运行：python3 main.py

fs 的第三个机制面：除了「版本守卫」（防盲写）和「原子写」（防半截），
文件 provider 之外还有一层可插拔的**改动意图**（intent）瀑布。工具层在
真正改动之前广播 fs/write-intent / edit-intent / delete-intent，观察者
趁旧内容还在时整份拷贝；轮次结束时再拍第二份快照，两次对比得到这一轮
改了哪些文件。事件只带 turn 号，摘要留在 Host 内存——重启即消失是决定行为。

源码对应（packages/deliverables/workspace-changes/）：
- apply / provide('workspaceChanges')   ↔ packages/deliverables/workspace-changes/src/index.ts:95 / :120
- session/event：turn/start             ↔ packages/deliverables/workspace-changes/src/index.ts:145
- agent/turn-stopping                   ↔ packages/deliverables/workspace-changes/src/index.ts:155
- fs/write-intent|edit-intent|delete-intent（prepend）
                                        ↔ packages/deliverables/workspace-changes/src/index.ts:164/169/174
- tools/pre-execute（改动前等快照就绪）  ↔ packages/deliverables/workspace-changes/src/index.ts:179
- TurnRecorder.start/capture/stopping   ↔ packages/deliverables/workspace-changes/src/recorder.ts:131/155/181
- workspace/changes 事件只带 {turn}      ↔ packages/deliverables/workspace-changes/src/types.ts:106
- summary(sessionId, seq)               ↔ packages/deliverables/workspace-changes/src/types.ts:86

[教学简化] 真实版用 git 临时索引/对象目录拍基线树，再对快照覆盖不到的
路径用整份拷贝补集；教学版不引入 git，基线快照与二次快照都用「文件拷贝 +
sha256」模拟，diff 用标准库 difflib。事件会话用内存列表代替 Session 日志。
"""
from __future__ import annotations

import difflib
import hashlib
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
for _p in (_HERE, _CH01):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Service  # noqa: E402

__all__ = ["WorkspaceChanges", "TurnRecorder"]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot(root: str) -> dict[str, str]:
    """把整棵工作树拍成 {相对路径: 内容哈希}。真实版是 git 树对象。"""
    tree = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            absolute = os.path.join(base, name)
            rel = os.path.relpath(absolute, root)
            # 快照产物（staging 临时文件）不进比较。
            if ".staging-" in rel:
                continue
            with open(absolute, "rb") as f:
                tree[rel] = _sha256(f.read())
    return tree


class TurnRecorder:
    """每会话一个记录器：一轮里「基线快照 → 改前捕获 → 收尾二次快照」。"""

    def __init__(self, root: str, scratch: str):
        self._root = os.path.realpath(root)
        self._scratch = scratch
        self._turn: int | None = None
        self._baseline: dict[str, str] = {}
        self._captures: dict[str, tuple[str, bytes]] = {}  # rel → (改前哈希, 改前内容)
        self._records: dict[int, dict] = {}

    # -- turn/start：拍基线树（recorder.ts:131） --

    def start(self, turn: int) -> None:
        self._turn = turn
        self._baseline = _snapshot(self._root)
        self._captures.clear()

    # -- fs/*-intent：改动之前整份拷贝，一轮同一路径只捕一次（recorder.ts:155） --

    def capture(self, absolute: str) -> None:
        if self._turn is None:
            return
        if not os.path.isfile(absolute):
            return
        rel = os.path.relpath(absolute, self._root)
        if rel in self._captures:
            return
        with open(absolute, "rb") as f:
            data = f.read()
        self._captures[rel] = (_sha256(data), data)

    # -- agent/turn-stopping：二次快照 → diff → 事件只报地址（recorder.ts:181） --

    def stopping(self, turn: int, session) -> int | None:
        if self._turn is None or turn != self._turn:
            return None
        after = _snapshot(self._root)
        changed = []
        for rel in sorted(set(self._baseline) | set(after) | set(self._captures)):
            before_hash = self._baseline.get(rel)
            after_hash = after.get(rel)
            if before_hash == after_hash and rel not in self._captures:
                continue
            before = self._read_before(rel, before_hash)
            after_bytes = self._read_after(rel)
            status = "deleted" if after_bytes is None else ("added" if before is None else "modified")
            changed.append({
                "path": rel,
                "status": status,
                "before": before,
                "after": after_bytes.decode("utf-8", "replace") if after_bytes is not None else None,
                "hunks": _line_counts(before, after_bytes),
            })
        summary = {"turn": turn, "files": changed}
        # 真实版把摘要存在 Host 内存、按 (sessionId, seq) 回取；事件本身只有一个 turn。
        seq = session.append("workspace/changes", {"turn": turn}, summary)
        self._records[seq] = summary
        self._turn = None
        self._baseline = {}
        self._captures.clear()
        return seq

    def summary(self, seq: int) -> dict | None:
        return self._records.get(seq)

    def _read_before(self, rel: str, baseline_hash: str | None):
        if rel in self._captures:
            return self._captures[rel][1].decode("utf-8", "replace")
        if baseline_hash is None:
            return None
        absolute = os.path.join(self._root, rel)
        if not os.path.exists(absolute):
            return None
        with open(absolute, "rb") as f:
            return f.read().decode("utf-8", "replace")

    def _read_after(self, rel: str):
        absolute = os.path.join(self._root, rel)
        if not os.path.exists(absolute):
            return None
        with open(absolute, "rb") as f:
            return f.read()


def _line_counts(before, after_bytes) -> tuple[int, int]:
    """±行数：真实版对捕集补集做行比较，教学版用 difflib。"""
    if after_bytes is None:
        after = ""
    else:
        after = after_bytes.decode("utf-8", "replace")
    before_lines = (before or "").splitlines()
    after_lines = after.splitlines()
    added = removed = 0
    for line in difflib.ndiff(before_lines, after_lines):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


class _InMemorySession:
    """教学版 Session 日志：append 返回递增 seq，事件与摘要分开存。"""

    def __init__(self):
        self.events = []

    def append(self, event_type: str, data: dict, summary: dict) -> int:
        seq = len(self.events) + 1
        self.events.append({"seq": seq, "type": event_type, "data": data})
        return seq


class WorkspaceChanges(Service):
    """ctx.workspaceChanges：挂意图瀑布，按轮出摘要（类比 workspace-changes 插件）。"""

    inject = []

    def __init__(self, ctx, fs, root):
        super().__init__(ctx, "workspaceChanges")
        self._root = os.path.realpath(root)
        # 暂存目录放在工作树之外，避免它自己被快照进去（真实版用 tmpdir）。
        scratch = os.environ.get("DSH_CHANGES_SCRATCH") or "/tmp"
        self._recorder = TurnRecorder(self._root, scratch)
        self._session = _InMemorySession()
        self._boot = object()  # 标识「本次 Host 进程」，重启后换新对象
        # prepend：先让后续监听器给出计划，再趁旧内容还在时整份拷贝（packages/deliverables/workspace-changes/src/index.ts:164-178）。
        for event in ("fs/write-intent", "fs/edit-intent", "fs/delete-intent"):
            ctx.on(event, self._capture_hook)
        # turn 生命周期与工具执行前的就绪等待（packages/deliverables/workspace-changes/src/index.ts:145/155/179）
        ctx.on("turn/start", self._on_turn_start)
        ctx.on("agent/turn-stopping", self._on_turn_stopping)
        ctx.on("tools/pre-execute", self._wait_settled)

    def _capture_hook(self, target, actor, next_):
        plan = next_()  # 先让后面监听器给出计划
        self._recorder.capture(target)
        return plan

    def _on_turn_start(self, turn):
        self._recorder.start(turn)

    def _on_turn_stopping(self, turn):
        return self._recorder.stopping(turn, self._session)

    def _wait_settled(self, *_args):
        return None

    def summary(self, session_id, seq):
        """按 (sessionId, seq) 回取摘要；Host 重启后自然取不到。"""
        return self._recorder.summary(seq)

    @property
    def events(self):
        return list(self._session.events)
