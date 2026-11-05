"""第 3 章教学重构：session 事件流、handle 持久化与格式迁移。

仅标准库，Python 3.10+。在第 1/2 章基础上增量构建：
- Context/Service 来自第 1 章 cordis.py
- Session/SessionEvent/Sessions/AgentLoop/LlmStub/SystemPromptService 来自第 2 章 agent_loop.py（只增不改）

本章新增机制与源码对应：
- SessionPersistence seam        ↔ packages/session/session-persistence/src/index.ts:144
- SessionHandle 契约             ↔ packages/session/session-persistence/src/handle.ts:59
- 所有权与只读错误                ↔ packages/session/session-persistence/src/errors.ts:13
- 活跃 writer 表 + live 路由      ↔ packages/session/session-persistence-jsonl/src/storage.ts:391（install :535）
- handle 的 live 缓冲            ↔ packages/session/session-persistence-jsonl/src/storage.ts:274
- 耐久屏障 flush                 ↔ packages/session/session-persistence-jsonl/src/storage.ts:203
- 撕裂尾截断                     ↔ packages/session/session-persistence-jsonl/src/storage.ts:319
- 中断 turn 修复（读侧）          ↔ packages/core/session/src/repair.ts:209
- agent-loop 恢复（open+read+closers）↔ packages/core/agent-loop/src/index.ts:788
- 相邻迁移边                     ↔ packages/session/session-format/src/chain.ts:24
- 静态 catalog                   ↔ packages/session/session-format-catalog/src/generated.ts:16
- 投影注册表                     ↔ packages/session/session-projection/src/index.ts:282
- 投影缓存                       ↔ packages/session/session-projection-cache/src/index.ts:105
- 标题投影                       ↔ packages/session/session-title/src/index.ts:269/:422
- 遥测捕获                       ↔ packages/session/session-telemetry/src/coordinator.ts:75/:180
- 遥测授权与 sourceEvent 校验     ↔ packages/session/session-telemetry-otel/src/index.ts:43/:224
"""
import copy
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

from cordis import Service
from agent_loop import SessionEvent

CURRENT_FORMAT_VERSION = 4


# ---------------------------------------------------------------------------
# 一、错误类：调用方靠名字区分失败，而不是猜 message
# ---------------------------------------------------------------------------

class SessionPersistenceError(Exception):
    """持久化失败的公共基类（真实包不设基类，这里只为教学归类）。"""


class SessionPersistenceNotFoundError(SessionPersistenceError):
    """open/stat 的会话在本后端不可见。"""

    def __init__(self, session_id):
        super().__init__(f'session "{session_id}" not found')
        self.session_id = session_id


class SessionAlreadyExistsError(SessionPersistenceError):
    """create 的目标 id 已被占用。"""

    def __init__(self, session_id):
        super().__init__(f'session "{session_id}" already exists')
        self.session_id = session_id


class SessionAlreadyOwnedError(SessionPersistenceError):
    """open(write) 撞上了一个仍然活着的写句柄。"""

    def __init__(self, session_id):
        super().__init__(f'session "{session_id}" is already owned by an active write handle')
        self.session_id = session_id


class SessionReadOnlyError(SessionPersistenceError):
    """在只读句柄上调用 append/flush。"""

    def __init__(self, session_id, operation):
        super().__init__(f'session "{session_id}": {operation} is not available on a read handle')
        self.session_id = session_id
        self.operation = operation


class SessionOwnershipLostError(SessionPersistenceError):
    """写所有权被永久夺走：lease 过期或所有权记录不再指向本句柄。

    [教学简化] 真实包已声明该类但进程内后端目前不会抛；跨进程 lease 层才会。
    """

    def __init__(self, session_id):
        super().__init__(f'session "{session_id}": write ownership was lost; close this handle and reopen')
        self.session_id = session_id


class SessionHandleClosedError(SessionPersistenceError):
    """close() 之后又对句柄做了操作。"""

    def __init__(self, session_id, operation):
        super().__init__(f'session "{session_id}": {operation} on a closed handle')
        self.session_id = session_id
        self.operation = operation


class SessionPersistenceCorruptionError(SessionPersistenceError):
    """后端成功读出字节，但已提交区的内容不合法。"""


class SessionFormatUnsupportedError(SessionPersistenceError):
    """日志完好，但本构建读不懂它：版本更新（请升级 harness）或链条有缺口。"""

    def __init__(self, message, location=None):
        super().__init__(message)
        self.location = location


# ---------------------------------------------------------------------------
# 二、格式链：相邻迁移边 + 启动时缺口校验 + 单遍消费物理行
# ---------------------------------------------------------------------------

class SessionFormatMigration:
    """一条相邻迁移边：只允许 vN → v(N+1)（defineSessionFormatMigration，packages/session/session-format/src/chain.ts:24）。"""

    def __init__(self, name, from_version, to_version, migrate_header=None, stage=None):
        if to_version != from_version + 1:
            raise ValueError(f"{name} must declare adjacent v{from_version}->v{from_version + 1}")
        self.name = name
        self.from_version = from_version
        self.to_version = to_version
        self._migrate_header = migrate_header or (lambda header: dict(header))
        self._stage = stage or (lambda row: [row])

    def migrate_header(self, header):
        out = dict(self._migrate_header(header))
        out["version"] = self.to_version
        return out

    def stage(self, row):
        """一行源记录 → 零到多行目标记录（真实版是 transformEvent/emitEvent）。"""
        return self._stage(row)


def _lift_tool_results(row):
    """v3→v4 的一条真实变换：把消息里内嵌的 tool_results 提升为独立 tool/result 事件。

    对应 session-format-v3-to-v4 的 liftToolResult（tool-role.ts）。
    [教学简化] 真实边还做 producer source、parent catalog、引用重映射。
    """
    if row["type"] != "assistant/message":
        return [row]
    results = row["payload"].get("tool_results") or []
    if not results:
        return [row]
    message = {k: v for k, v in row["payload"].items() if k != "tool_results"}
    lifted = [{**row, "payload": message}]
    for result in results:
        lifted.append({
            "session_id": row["session_id"],
            "seq": -1,  # 由链统一重编号
            "type": "tool/result",
            "payload": {"tool_call_id": result["call_id"], "content": result["content"]},
        })
    return lifted


def _rename_ptc_preset(header):
    """v2→v3 的头变换：preset 名从 code 改成 ptc（session-format-v2-to-v3/src/migration.ts:18）。"""
    if header.get("agentPreset") == "code":
        return {**header, "agentPreset": "ptc"}
    return header


# [教学简化] 真实版四条边各自是冻结的解码器 + 完整迁移；教学版只有 v3→v4 做了真实变换，
# 其余三条是为演示「相邻链 + 缺口校验」而留的占位边。
SESSION_FORMAT_MIGRATIONS = [
    SessionFormatMigration("v0-to-v1", 0, 1),
    SessionFormatMigration("v1-to-v2", 1, 2),
    SessionFormatMigration("v2-to-v3", 2, 3, migrate_header=_rename_ptc_preset),
    SessionFormatMigration("v3-to-v4", 3, 4, stage=_lift_tool_results),
]


class SessionFormatChain:
    """把静态 catalog 编译成「唯一且无缺口」的相邻迁移链（packages/session/session-format/src/chain.ts:41）。

    构造期就校验：每版本恰好一条边、0..current-1 无缺口、没有越过 current 的边。
    读历史会话时才按 from_version 取后缀，逐条穿过。
    """

    def __init__(self, current_version, migrations):
        self.current_version = current_version
        by_from = {}
        for migration in migrations:
            if migration.to_version != migration.from_version + 1:
                raise ValueError(f"{migration.name} must declare adjacent versions")
            if migration.from_version in by_from:
                raise ValueError(f"migration from v{migration.from_version} is duplicated")
            by_from[migration.from_version] = migration
        ordered = []
        for version in range(current_version):
            migration = by_from.get(version)
            if migration is None:
                raise SessionFormatUnsupportedError(
                    f"Session migration v{version}->v{version + 1} is missing"
                )
            ordered.append(migration)
        if len(by_from) != len(ordered):
            raise SessionFormatUnsupportedError("a migration does not lead to the current version")
        self.migrations = ordered

    def plan(self, from_version):
        if from_version > self.current_version:
            raise SessionFormatUnsupportedError(
                f"stored Session uses newer format v{from_version}; "
                f"this build writes v{self.current_version}"
            )
        return self.migrations[from_version:]

    def migrate(self, header, rows):
        """单遍迁移：每条物理行只解码一次，穿过计划里的全部 stage。

        真实版把 stage 串成链（packages/session/session-format/src/chain.ts:89 createStream），事件经 emitEvent 逐个向后传；
        教学版用嵌套循环表达同一件事，并按目标顺序重编号 seq（[教学简化] 真实版由各 stage 算目标 seq）。
        """
        plan = self.plan(header["version"])
        migrated_header = dict(header)
        for migration in plan:
            migrated_header = migration.migrate_header(migrated_header)
        events = []
        for row in rows:
            produced = [row]
            for migration in plan:
                next_rows = []
                for item in produced:
                    next_rows.extend(migration.stage(item))
                produced = next_rows
            for item in produced:
                events.append({**item, "seq": len(events)})
        return migrated_header, events


# ---------------------------------------------------------------------------
# 三、SessionHandle：每会话一个读写句柄，日志读写的唯一通道
# ---------------------------------------------------------------------------

class SessionHandle:
    """一个打开中的会话通道（SessionHandle，handle.ts:59）。

    关键语义：
    - read 永远不返回撕裂尾；同一句柄上后一次 read 不会看到比先前更短的前缀
    - append 只接受与 next-seq 连续的批；解析即「已接受」，但不承诺崩溃后还在
    - flush 是耐久屏障：解析即承诺已 append 的事件落盘、会话对外物化
    - close 幂等、不可取消；写句柄在这里完成最后一次 drain 并交出所有权
    [教学简化] 真实版是 async 接口（含 AbortSignal、AsyncDisposable）；教学版同步。
    """

    def __init__(self, backend, header, access, cursor=0, materialized=False,
                 known_events=None, torn_truncate_to=None, migrated=False):
        self._backend = backend
        self.header = header
        self.id = header["id"]
        self.access = access
        self._cursor = cursor            # 本句柄已知的 next-seq（写句柄 = 已 append 的边界）
        self._materialized = materialized
        self._known_events = known_events or []
        self._torn_truncate_to = torn_truncate_to
        self._migrated = migrated
        self._live_buffer = []           # 后端按 id 路由来的 live 事件（持久化拥有的副本）
        self._observed = len(self._known_events)
        self._closing = False

    # -- 内部：关闭态与只读态检查 --

    def _assert_open(self, operation):
        if self._closing:
            raise SessionHandleClosedError(self.id, operation)

    def _require_write(self, operation):
        if self.access != "write":
            raise SessionReadOnlyError(self.id, operation)

    # -- 读 --

    def read(self, offset=0, length=None):
        """读合法前缀的一段（handle.ts:83）。

        [教学简化] 真实版返回 {eventState, events} 以区分事件值是否独占；
        教学版直接返回事件列表。
        """
        self._assert_open("read")
        if offset < 0 or (length is not None and length < 0):
            raise ValueError("read offset/length must be non-negative")
        events = self._load()
        if len(events) < self._observed:
            raise SessionPersistenceCorruptionError(
                f'session "{self.id}": stored log shrank below a previously observed prefix'
            )
        self._observed = len(events)
        end = None if length is None else offset + length
        return events[offset:end]

    def _load(self):
        path = self._backend.log_path(self.id)
        if not path.exists():
            if self.id in self._backend.pending:
                return list(self._known_events)   # 已 create 但未物化：本进程可见的空前缀
            raise SessionPersistenceNotFoundError(self.id)
        header, events, torn_at, migrated = self._backend.read_log(self.id)
        self.header = header
        self._known_events = events
        self._migrated = self._migrated or migrated
        if self._torn_truncate_to is None:
            self._torn_truncate_to = torn_at
        return events

    # -- 写 --

    def append(self, events):
        """追加一批 seq 连续的事件（handle.ts:97）。"""
        self._assert_open("append")
        self._require_write("append")
        batch = list(events)
        if not batch:
            return
        self._persist_contiguous(batch)

    def _persist_contiguous(self, batch):
        """真·写盘路径：连续性断言 → 撕裂尾截断 → 首次物化/后继发布 → 推进 cursor。

        对应 JsonlSessionHandle.persistContiguous（storage.ts:319）。
        """
        for index, event in enumerate(batch):
            if event.seq != self._cursor + index:
                raise ValueError(
                    f"append seq 断裂 {self.id!r}：期望 {self._cursor + index}，实际 {event.seq}"
                )
        path = self._backend.log_path(self.id)
        if self._torn_truncate_to is not None:
            # 撕裂尾属于一次从未解析的 append：第一次新写入前把它截掉
            with open(path, "r+b") as file:
                file.truncate(self._torn_truncate_to)
            self._torn_truncate_to = None
        if not self._materialized or self._migrated:
            # 首次物化，或历史格式读完后的「后继发布」：整份以当前格式写一份
            published = self._known_events + batch
            self._backend.publish(path, self.header, published)
            self._known_events = published
            self._materialized = True
            self._migrated = False
            self._backend.pending.pop(self.id, None)
        else:
            self._backend.append_lines(path, batch)
        self._cursor += len(batch)
        self._observed = max(self._observed, self._cursor)

    def flush(self):
        """耐久屏障：已 append 的事件落盘；空的新会话也在这里物化（handle.ts:109）。"""
        self._assert_open("flush")
        self._require_write("flush")
        self.drain_live()
        if not self._materialized:
            self._backend.publish(self._backend.log_path(self.id), self.header, self._known_events)
            self._materialized = True
            self._backend.pending.pop(self.id, None)

    # -- live 路由与关闭 --

    def enqueue_live(self, event):
        """后端路由调用的唯一入口：把已发布会话的 live 事件放进批量窗（storage.ts:274）。"""
        self._live_buffer.append(copy.deepcopy(event))

    def drain_live(self):
        """把批量窗排空成一次连续追加；失败时整批保留、下次重试（storage.ts:288）。"""
        if not self._live_buffer:
            return
        batch, self._live_buffer = self._live_buffer, []
        try:
            self._persist_contiguous(batch)
        except Exception:
            self._live_buffer = batch + self._live_buffer
            raise

    def close(self):
        """幂等关闭：写句柄先 drain 再交出所有权（storage.ts:223）。"""
        if self._closing:
            return
        self._closing = True
        try:
            self.drain_live()
        finally:
            self._backend.release(self, self._materialized)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


# ---------------------------------------------------------------------------
# 四、SessionPersistence seam：create / open / stat / list / flush
# ---------------------------------------------------------------------------

def event_to_dict(event):
    """把 SessionEvent 展开为可 JSON 序列化的 dict（storage-contract 的 materialize 语义）。"""
    return {
        "session_id": event.session_id,
        "seq": event.seq,
        "type": event.type,
        "payload": event.payload,
    }


def dict_to_event(data):
    """把一行物理记录还原为 SessionEvent。"""
    return SessionEvent(data["session_id"], data["seq"], data["type"], data["payload"])


def interrupted_turn_closers(events):
    """读侧修复：为断电留下的未闭合 turn 合成收尾事件（repair.ts:209）。

    真实版 openTurnClosers 会补缺失的 tool/result 错误、step/end 与 turn/end；
    [教学简化] 教学版只补 turn/end。
    """
    open_turn = False
    for event in events:
        if event.type == "turn/start":
            open_turn = True
        elif event.type == "turn/end":
            open_turn = False
    if not open_turn:
        return []
    return [SessionEvent(events[0].session_id if events else "", 0, "turn/end",
                         {"reason": "interrupted"})]


class SessionPersistence(Service, ABC):
    """会话持久化 seam 的 Service Definition（index.ts:144），绑定为 ctx.sessionPersistence。

    真实契约五个方法：create(header)、open(id, access)、flush()、stat(id)、list()；
    日志的每一次读写都经返回的 SessionHandle，没有按 id 的 append/load。
    """

    def __init__(self, ctx):
        super().__init__(ctx, "sessionPersistence")

    @abstractmethod
    def create(self, header):
        """新建会话并取得写所有权；返回写句柄。"""

    @abstractmethod
    def open(self, session_id, access="read"):
        """打开已存在会话：read 只观察，write 原子地认领单写所有权。"""

    @abstractmethod
    def stat(self, session_id):
        """只看头与元数据，不读事件体、不取所有权。"""

    @abstractmethod
    def list(self):
        """枚举本进程可见的已存会话。"""

    @abstractmethod
    def flush(self):
        """服务级耐久屏障：把所有活跃写句柄的 live 缓冲排空并落盘。"""


# ---------------------------------------------------------------------------
# 五、JSONL 后端：后端拥有 live write path
# ---------------------------------------------------------------------------

class JsonlSessionPersistence(SessionPersistence):
    """JSONL 后端（session-persistence-jsonl/src/index.ts:291）。

    物理形态：{root}/sessions/{id}.jsonl，第一行是头（带 version），之后一行一个事件。
    [教学简化] 真实版每个会话一个目录、写不可变的代际文件名并用 zstd 压缩；
    教学版拍平成单文件、写明文。
    """

    def __init__(self, ctx, root):
        super().__init__(ctx)
        self.root = Path(root) / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        self.chain = SessionFormatChain(CURRENT_FORMAT_VERSION, SESSION_FORMAT_MIGRATIONS)
        self.writers = {}   # session_id -> SessionHandle | None（None = 认领中）
        self.pending = {}   # session_id -> header（已 create、未物化）
        self.handles = set()
        self._revision_counter = 0
        self.install_live_routing()

    # -- live 路由：后端按 id 把已发布会话的事件送进活跃写句柄 --

    def install_live_routing(self):
        """安装三条会话监听（storage.ts:535 install）。

        - session/event  → 复制进该会话写句柄的批量窗（写时无声）
        - session/flush  → 立即 drain + flush，是循环使用的错误观测点
        - session/disposed → 最后一次 drain 并关闭句柄
        """
        self.ctx.on("session/event", self._on_event)
        self.ctx.on("session/flush", self._on_flush)
        self.ctx.on("session/disposed", self._on_disposed)

    def _on_event(self, event):
        writer = self.writers.get(event.session_id)
        if isinstance(writer, SessionHandle):
            writer.enqueue_live(event)

    def _on_flush(self, session):
        writer = self.writers.get(session.id)
        if isinstance(writer, SessionHandle):
            writer.drain_live()
            writer.flush()

    def _on_disposed(self, session):
        writer = self.writers.get(session.id)
        if isinstance(writer, SessionHandle):
            writer.close()

    # -- 物理读写 --

    def log_path(self, session_id):
        return self.root / f"{session_id}.jsonl"

    def publish(self, path, header, events):
        """整份发布：头行 + 全部事件，fsync 后才返回（首次物化 / 迁移后的后继发布）。"""
        lines = [json.dumps(header, ensure_ascii=False)]
        lines += [json.dumps(event_to_dict(event), ensure_ascii=False) for event in events]
        with open(path, "w", encoding="utf-8") as file:
            file.write("\n".join(lines) + "\n")
            file.flush()
            os.fsync(file.fileno())

    def append_lines(self, path, events):
        """追加事件行 + fsync；失败即回滚到写前大小，整批可重试（storage.ts:338）。"""
        size = path.stat().st_size
        lines = "".join(json.dumps(event_to_dict(event), ensure_ascii=False) + "\n" for event in events)
        try:
            with open(path, "a", encoding="utf-8") as file:
                file.write(lines)
                file.flush()
                os.fsync(file.fileno())
        except OSError:
            with open(path, "a", encoding="utf-8") as file:
                file.truncate(size)
            raise

    def _scan(self, session_id):
        """一次读完全部字节：最后一条不完整记录即撕裂尾，不进入已提交区。"""
        path = self.log_path(session_id)
        if not path.exists():
            return None
        raw = path.read_bytes()
        if raw.endswith(b"\n"):
            committed, torn_at = raw, None
        else:
            cut = raw.rfind(b"\n") + 1
            committed, torn_at = raw[:cut], len(raw[:cut])
        if not committed:
            raise SessionPersistenceCorruptionError(f'session "{session_id}": 没有完整的头行')
        lines = committed.decode("utf-8").splitlines()
        try:
            header = json.loads(lines[0])
            rows = [json.loads(line) for line in lines[1:]]
        except json.JSONDecodeError as error:
            raise SessionPersistenceCorruptionError(
                f'session "{session_id}": committed prefix is not valid JSONL'
            ) from error
        return {"header": header, "rows": rows, "torn_at": torn_at}

    def read_log(self, session_id):
        """读回当前逻辑事件：历史版本先过格式链，出口永远是当前格式。"""
        scan = self._scan(session_id)
        if scan is None:
            raise SessionPersistenceNotFoundError(session_id)
        header, rows = scan["header"], scan["rows"]
        if header["version"] > CURRENT_FORMAT_VERSION:
            raise SessionFormatUnsupportedError(
                f'session "{session_id}" uses log format v{header["version"]}, '
                f"but this harness reads only v{CURRENT_FORMAT_VERSION}"
            )
        migrated = header["version"] < CURRENT_FORMAT_VERSION
        if migrated:
            header, rows = self.chain.migrate(header, rows)
        events = [dict_to_event({**row, "session_id": row.get("session_id", session_id)})
                  for row in rows]
        return header, events, scan["torn_at"], migrated

    # -- seam 方法 --

    def create(self, header):
        session_id = header["id"]
        if session_id in self.writers or session_id in self.pending or self.log_path(session_id).exists():
            raise SessionAlreadyExistsError(session_id)
        stored = dict(header)
        stored["version"] = CURRENT_FORMAT_VERSION
        self.pending[session_id] = stored
        self.writers[session_id] = None      # 先占位，句柄建好后再换成真句柄
        handle = SessionHandle(self, stored, "write")
        self.writers[session_id] = handle
        self.handles.add(handle)
        return handle

    def open(self, session_id, access="read"):
        if access not in ("read", "write"):
            raise ValueError(f"access must be 'read' or 'write', got {access!r}")
        if access == "read":
            if session_id in self.pending:
                header = self.pending[session_id]
                handle = SessionHandle(self, header, "read", materialized=False)
            else:
                header, events, torn_at, migrated = self.read_log(session_id)
                handle = SessionHandle(self, header, "read", cursor=len(events), materialized=True,
                                       known_events=events, torn_truncate_to=torn_at,
                                       migrated=migrated)
            self.handles.add(handle)
            return handle
        if session_id in self.writers:
            raise SessionAlreadyOwnedError(session_id)
        if session_id not in self.pending and not self.log_path(session_id).exists():
            raise SessionPersistenceNotFoundError(session_id)
        self.writers[session_id] = None
        try:
            if session_id in self.pending:
                header, events, torn_at, migrated = self.pending[session_id], [], None, False
                materialized = False
            else:
                header, events, torn_at, migrated = self.read_log(session_id)
                materialized = True
        except Exception:
            self.writers.pop(session_id, None)
            raise
        handle = SessionHandle(self, header, "write", cursor=len(events), materialized=materialized,
                               known_events=events, torn_truncate_to=torn_at, migrated=migrated)
        self.writers[session_id] = handle
        self.handles.add(handle)
        return handle

    def flush(self):
        for writer in list(self.writers.values()):
            if isinstance(writer, SessionHandle):
                writer.drain_live()
                writer.flush()

    def stat(self, session_id):
        if session_id in self.pending:
            header = self.pending[session_id]
            self._revision_counter += 1
            return {"header": header, "revision": f"memory:{self._revision_counter}", "eventCount": 0}
        scan = self._scan(session_id)
        if scan is None:
            return None
        path = self.log_path(session_id)
        header = scan["header"]
        if header["version"] < CURRENT_FORMAT_VERSION:
            for migration in self.chain.plan(header["version"]):
                header = migration.migrate_header(header)
        return {
            "header": header,
            "revision": f"file:{path.stat().st_size}",
            "eventCount": len(scan["rows"]),
            "sizeBytes": path.stat().st_size,
        }

    def list(self):
        ids = {path.stem for path in self.root.glob("*.jsonl")} | set(self.pending)
        return sorted(ids)

    def release(self, handle, materialized):
        """句柄关闭：丢掉所有权占位；从未物化的会话等于从未存在（storage.ts:495）。"""
        self.handles.discard(handle)
        if handle.access != "write":
            return
        if self.writers.get(handle.id) is handle:
            self.writers.pop(handle.id, None)
        if not materialized:
            self.pending.pop(handle.id, None)


# ---------------------------------------------------------------------------
# 六、投影：注册表 + 缓存 + 标题
# ---------------------------------------------------------------------------

class SessionProjectionRegistry:
    """投影注册表（session-projection/src/index.ts:282，真实版是 Service）。

    每个单元是 init/apply/view 三件套；活折叠 drive 逐事件推进，冷读 restore 从头重放。
    """

    def __init__(self, ctx):
        self.ctx = ctx
        self._definitions = {}
        self._states = {}
        ctx.on("session/event", self._on_event)

    def register(self, key, init, apply, view):
        self._definitions[key] = {"init": init, "apply": apply, "view": view}

    def _on_event(self, event):
        self.drive(event.session_id, event)

    def drive(self, session_id, event):
        states = self._states.setdefault(session_id, {})
        for key, definition in self._definitions.items():
            state = states.get(key)
            if state is None:
                state = definition["init"]()
            states[key] = definition["apply"](state, event)

    def restore(self, session_id, events):
        states = {}
        for key, definition in self._definitions.items():
            state = definition["init"]()
            for event in events:
                state = definition["apply"](state, event)
            states[key] = state
        self._states[session_id] = states
        return self.snapshot(session_id)

    def snapshot(self, session_id):
        states = self._states.get(session_id, {})
        return {key: definition["view"](states[key])
                for key, definition in self._definitions.items() if key in states}


class SessionProjectionCache:
    """投影缓存（session-projection-cache/src/index.ts:105）。

    [教学简化] 真实版把 checkpoint 持久化到 storage domain 并做节流；教学版只在内存里
    保留「turn/end 强制写 + 冷读先查缓存」两个语义。
    """

    def __init__(self, ctx, registry):
        self.ctx = ctx
        self.registry = registry
        self._snapshots = {}
        ctx.on("session/event", self._on_event)

    def _on_event(self, event):
        if event.type == "turn/end":
            self.write(event.session_id)

    def write(self, session_id):
        self._snapshots[session_id] = self.registry.snapshot(session_id)

    def cached_snapshot(self, session_id):
        return self._snapshots.get(session_id)

    def cold_snapshot(self, session_id, events):
        snapshot = self.registry.restore(session_id, events)
        self._snapshots[session_id] = snapshot
        return snapshot


def _title_init():
    return None


def _title_apply(state, event):
    """标题单元：只对 session/title 做 last-wins（session-title/src/index.ts:274）。"""
    if event.type == "session/title":
        return event.payload["title"]
    return state


def _title_view(state):
    return state


class SessionTitleService(Service):
    """标题服务（session-title/src/index.ts:301）：生成后以 session/title 事件入流。"""

    def __init__(self, ctx, registry):
        super().__init__(ctx, "session_title")
        registry.register("title", _title_init, _title_apply, _title_view)

    def generate(self, session):
        first_user = next((event for event in session.log if event.type == "user/message"), None)
        if first_user is None:
            return None
        text = first_user.payload["message"]["content"]
        title = text.rstrip("？?！!。，, ")[:20]
        session.append("session/title", {"title": title})   # 标题也是事件（:422）
        return title


# ---------------------------------------------------------------------------
# 七、遥测捕获：授权、脱敏、sourceEvent 同一性（出向通道见第 18 章）
# ---------------------------------------------------------------------------

TELEMETRY_MODE_FEEDBACK_ONLY = "FEEDBACK_ONLY"
TELEMETRY_MODE_DISABLED = "DISABLED"


def is_feedback(event):
    """只有本会话显式的 feedback 事件授权重放（session-telemetry-otel/src/index.ts:43）。

    [教学简化] 真实版还区分 fork seed（seq < inheritedEventCount 一律否决）与
    feedback/message-* 的 sessionId 归属；教学版只看事件类型。
    """
    return event.type in ("feedback/record", "feedback/message-put", "feedback/message-delete")


def event_at(session, seq):
    """会话已提交日志里 seq 处的那条事件；越界返回 None。"""
    return session.log[seq] if 0 <= seq < len(session.log) else None


class SessionTelemetrySink:
    """后端侧：接收记录，拒收缺少 sourceEvent 的记录（packages/session/session-telemetry-otel/src/index.ts:204）。"""

    def __init__(self):
        self.records = []
        self.withheld = 0

    def emit(self, record):
        if record.get("sourceEvent") is None:
            self.withheld += 1
            return
        self.records.append(record)


class SessionTelemetryCoordinator:
    """捕获侧（session-telemetry/src/coordinator.ts:75）。

    抓取规则不是「每事件一条」：默认 FEEDBACK_ONLY 下，只有显式 feedback 事件
    授权把该会话到该 seq 为止的日志重放一遍；每条记录经
    `session-telemetry/record` waterfall 脱敏，再交给 sink。
    """

    def __init__(self, ctx, sink, mode=TELEMETRY_MODE_FEEDBACK_ONLY):
        self.ctx = ctx
        self.sink = sink
        self.mode = mode
        self.captured_upto = {}     # session_id -> 已交付的最高 seq（handoff cursor）
        self.non_canonical = 0
        ctx.on("session/event", self._on_event)

    def _on_event(self, event):
        if self.mode == TELEMETRY_MODE_DISABLED:
            return
        if not is_feedback(event):
            return
        session = self.ctx.sessions.get(event.session_id)
        if session is None:
            return
        if event_at(session, event.seq) is not event:
            # 事件不在规范日志上（重放来的副本）：不授权任何前缀
            self.non_canonical += 1
            return
        self.capture_session(session, event.seq)

    def capture_session(self, session, through_seq):
        start = self.captured_upto.get(session.id, -1) + 1
        for event in session.log[start:]:
            if event.seq > through_seq:
                break
            self._capture_event(session, event)

    def _capture_event(self, session, event):
        record = {
            "sourceEvent": {"sessionId": session.id, "event": event_to_dict(event)},
            "channel": "ledger",
            "attributes": {"event.type": event.type, "event.seq": event.seq},
            "body": copy.deepcopy(event.payload),
        }
        record = self.ctx.waterfall("session-telemetry/record", record)
        self.sink.emit(record)
        self.captured_upto[session.id] = event.seq
