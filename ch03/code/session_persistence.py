"""第 3 章教学重构：session 事件流与持久化投影。

仅标准库，Python 3.10+。在第 1/2 章基础上增量构建：
- Context/Service 来自第 1 章 cordis.py
- Session/SessionEvent/Sessions/AgentLoop/LlmStub/SystemPromptService 来自第 2 章 agent_loop.py（只增不改）

本章新增机制与源码对应：
- SessionPersistence seam        ↔ packages/session/session-persistence/src/index.ts:84（Service Definition）
- PersistenceBackend 适配器契约   ↔ packages/session/session-persistence/src/coordinator.ts:127
- PersistenceCoordinator         ↔ coordinator.ts:588（installWritePath :1086、flush :1325、appendCore :682、prepareCore :892）
- WriteBehind 延迟写缓冲          ↔ session-persistence/src/write-behind.ts:22
- JsonlSessionPersistence        ↔ session-persistence-jsonl/src/index.ts:121（materialize :514、appendLines :651、loadStored :209、commitRepair :436）
- SqliteSessionPersistence       ↔ session-persistence-sqlite/src/index.ts:99（appendBatch :284、loadStored :207、commitRepair :309）
- SessionProjectionRegistry      ↔ session-projection/src/index.ts:171（ProjectionDefinition :42、订阅 :181-183、snapshot :248、restore :355、drive :405）
- SessionProjectionCache         ↔ session-projection-cache/src/index.ts:71（cachedSnapshot :119、coldSnapshot :166、installWritePath :201）
- 标题投影单元                    ↔ session-title/src/index.ts:261（foldSessionTitle :191、注册 :308-317、生成器追加 :374）
- TelemetryRecorder              ↔ session-telemetry/src/coordinator.ts:60（captureEvent :180）
"""
import json
import os
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path

from cordis import Service
from agent_loop import Session, SessionEvent


# ---------------------------------------------------------------------------
# 一、seam 与适配器契约
# ---------------------------------------------------------------------------

class SessionPersistence(ABC):
    """sessionPersistence seam：持久化的 Service Definition（session-persistence/src/index.ts:84）。

    真实契约有 create/append/load/inspect/readFrom/list/locate 等 11 个方法（:96-:240）；
    [教学简化] 教学版保留三个：
    - append：追加一批 seq 连续的事件，首条 seq 必须等于存储的 next-seq（:143）
    - load：冷读——从存储把会话读回来（:183）
    - list：枚举已持久化的会话（:228）
    """

    @abstractmethod
    def append(self, session_id, events):
        """追加一批事件到存储。"""

    @abstractmethod
    def load(self, session_id):
        """冷读会话；不存在返回 None。"""

    @abstractmethod
    def list(self):
        """枚举已持久化的 session_id 列表。"""


class PersistenceBackend(ABC):
    """存储适配器契约（PersistenceBackend，coordinator.ts:127）。

    真实接口还有 locate/close；[教学简化] 教学版保留四个：
    append_batch / load_stored / commit_repair / list（:184/:144/:193/:199）。
    """

    @abstractmethod
    def append_batch(self, session_id, events):
        """把一批事件写入存储。"""

    @abstractmethod
    def load_stored(self, session_id):
        """从存储读出事件列表；不存在返回 None。"""

    @abstractmethod
    def commit_repair(self, session_id, events):
        """把修复后的事件序列落盘（截断 + 补写）。"""

    @abstractmethod
    def list(self):
        """枚举已持久化的 session_id 列表。"""


def event_to_dict(event):
    """把 SessionEvent 展开为可 JSON 序列化的 dict（写盘前的序列化）。"""
    return {
        "session_id": event.session_id,
        "seq": event.seq,
        "type": event.type,
        "payload": event.payload,
    }


def dict_to_event(data):
    """把 dict 还原为 SessionEvent（冷读时的反序列化）。"""
    return SessionEvent(data["session_id"], data["seq"], data["type"], data["payload"])


# ---------------------------------------------------------------------------
# 二、写路径：协调器 + 延迟写缓冲
# ---------------------------------------------------------------------------

class WriteBehind:
    """延迟写缓冲（SessionWriteBehind，session-persistence/src/write-behind.ts:22）。

    事件先入队，flush 时整批取走。
    [教学简化] 真实版按固定周期批量 flush（默认 200ms，coordinator.ts:30）；
    教学版单线程同步，用「turn/end 触发 + session/flush 屏障」代替定时器。
    """

    def __init__(self):
        self.queue = []  # 待落盘事件的 FIFO 队列

    def enqueue(self, event):
        """事件入队（write-behind.ts:22 的队列语义）。"""
        self.queue.append(event)

    def take(self):
        """整批取走队列，返回事件列表并清空（flush 的取批动作）。"""
        batch, self.queue = self.queue, []
        return batch


class PersistenceCoordinator(Service, SessionPersistence):
    """持久化协调器：事件流到磁盘的中枢（coordinator.ts:588），绑定为 ctx.sessionPersistence。

    写路径：installWritePath 订阅 session/event（:1086/:1123）→ 事件入 WriteBehind 队列
    → flush 取批（:1325）→ appendCore 校验 seq 连续（:682/:698-702）→ backend.append_batch。
    读路径：load 冷读（:756）→ backend.load_stored → 修复中断 turn（prepareCore :892）。
    """

    def __init__(self, ctx, backend):
        super().__init__(ctx, "sessionPersistence")  # 真实绑定名（session-persistence/src/index.ts:86）
        self.backend = backend
        self.cursors = {}  # session_id -> 已落盘的最高 seq 边界（cursor，appendCore :705-708）
        self.writes = {}   # session_id -> WriteBehind 队列
        self.install_write_path()

    def install_write_path(self):
        """订阅会话事件，把事件流引向磁盘（installWritePath，coordinator.ts:1086）。"""
        self.ctx.on("session/event", self._on_event)   # 每个事件入队（:1123-1126）
        self.ctx.on("session/flush", self.flush)       # session/flush = 立即落盘屏障（:1129）

    def _on_event(self, event):
        """收到 session/event：入队，并在 turn 结束时触发批量落盘。"""
        queue = self.writes.setdefault(event.session_id, WriteBehind())
        queue.enqueue(event)
        if event.type == "turn/end":
            # [教学决策] 用 turn/end 作为批量边界；真实版是 200ms 固定周期（coordinator.ts:30）
            self.flush(event.session_id)

    def flush(self, session_id):
        """flush：取走队列中全部事件、批量写盘（coordinator.ts:1325）。"""
        queue = self.writes.get(session_id)
        batch = queue.take() if queue else []
        if batch:
            self.append(session_id, batch)

    def append(self, session_id, events):
        """appendCore：seq 连续契约 + 事务性游标（coordinator.ts:682）。"""
        cursor = self.cursors.get(session_id, 0)
        for i, event in enumerate(events):
            # seq 连续断言：批内第 i 条必须恰好是 cursor+i（:698-702）
            if event.seq != cursor + i:
                raise ValueError(
                    f"append seq 断裂 {session_id!r}：期望 {cursor + i}，实际 {event.seq}"
                )
        self.backend.append_batch(session_id, events)      # 委托适配器写盘（:704）
        self.cursors[session_id] = cursor + len(events)    # 写盘成功后才推进 cursor（:705-708）

    def load(self, session_id):
        """冷读：load_stored → 修复中断 turn → replay 重建 Session（load :756、prepareCore :892）。"""
        events = self.backend.load_stored(session_id)
        if events is None:
            return None
        closers = interrupted_turn_closers(events)
        if closers:
            # 为未闭合的 turn 合成收尾事件，并连同修复一起落盘（commitRepair）
            next_seq = len(events)
            repaired = events + [
                SessionEvent(session_id, next_seq + i, c["type"], c["payload"])
                for i, c in enumerate(closers)
            ]
            self.backend.commit_repair(session_id, repaired)
            events = repaired
        session = Session(self.ctx, session_id)
        # [教学简化] replay 直接写 log，不走 append——避免再次广播 session/event 造成二次落盘
        session.log.extend(events)
        self.cursors[session_id] = len(events)  # adopt：游标与磁盘对齐（adopt :1036）
        return session

    def list(self):
        """枚举已持久化的会话（seam 契约 :228）。"""
        return self.backend.list()


def interrupted_turn_closers(events):
    """检测未闭合的 turn，返回需要合成的收尾事件（prepareCore，coordinator.ts:892）。

    [教学简化] 真实版会合成 turn/end、step/end 等多种 closer；教学版只补 turn/end。
    """
    open_turn = False
    for event in events:
        if event.type == "turn/start":
            open_turn = True
        elif event.type == "turn/end":
            open_turn = False
    if not open_turn:
        return []
    return [{"type": "turn/end", "payload": {"reason": "interrupted"}}]


# ---------------------------------------------------------------------------
# 三、JSONL 后端
# ---------------------------------------------------------------------------

class JsonlSessionPersistence(PersistenceBackend):
    """JSONL 存储适配器（session-persistence-jsonl/src/index.ts:121）。

    物理形态：{root}/sessions/{session_id}.jsonl，一行一个 JSON 事件（eventLines，format.ts:221）。
    [教学简化] 真实版目录层级为 projectDir/sessionDir/logPath 三级 + encodeSegment 防路径穿越
    （format.ts:121-136/:176/:189/:201），教学版拍平为 sessions/{id}.jsonl；
    真实版用 zstd 压缩（头帧 + 事件帧拼接，jsonl/index.ts:619/:629），教学版写明文。
    """

    def __init__(self, root):
        self.root = Path(root) / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)

    def log_path(self, session_id):
        """会话日志文件路径（logPath，format.ts:201）。"""
        return self.root / f"{session_id}.jsonl"

    def append_batch(self, session_id, events):
        """批量追加事件行 + fsync（appendLines，jsonl/index.ts:651）。

        [教学简化] 真实版首次写入经 materialize：临时文件 + fsync + link()+unlink() 原子发布
        （:514/:529），教学版直接 open("a") 创建；
        追加失败时回滚文件大小，保证批内 seq 不重复（:662-678、rollbackAppend :681）。
        """
        path = self.log_path(session_id)
        size = path.stat().st_size if path.exists() else 0  # 记录写前大小，供失败回滚
        lines = "".join(json.dumps(event_to_dict(e), ensure_ascii=False) + "\n" for e in events)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(lines)
                f.flush()
                os.fsync(f.fileno())  # fsync 落盘：断电不丢已确认的批
        except OSError:
            with open(path, "a", encoding="utf-8") as f:
                f.truncate(size)  # 回滚语义：截回写前大小，整批可重试（:662-678）
            raise

    def load_stored(self, session_id):
        """逐行扫描 + 撕裂尾恢复（loadStored，jsonl/index.ts:209）。

        [教学简化] 真实版按 zstd 帧解码并用 SessionLogScanner 扫描，撕裂尾帧经 tornMarker
        恢复（:348/:407-410）；教学版明文逐行：最后一条解析失败的半行按撕裂尾丢弃。
        """
        path = self.log_path(session_id)
        if not path.exists():
            return None
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(dict_to_event(json.loads(line)))
            except json.JSONDecodeError:
                break  # 撕裂尾：到此为止（对应 tornMarker.truncateTo 语义）
        return events

    def commit_repair(self, session_id, events):
        """把修复后的事件序列落盘（commitRepair，jsonl/index.ts:436）。"""
        path = self.log_path(session_id)
        lines = "".join(json.dumps(event_to_dict(e), ensure_ascii=False) + "\n" for e in events)
        # [教学简化] 真实版是截断 + 补写；教学版整文件重写，语义相同
        with open(path, "w", encoding="utf-8") as f:
            f.write(lines)
            f.flush()
            os.fsync(f.fileno())

    def list(self):
        """枚举已持久化的会话（list，jsonl/index.ts:447）。"""
        return sorted(p.stem for p in self.root.glob("*.jsonl"))


# ---------------------------------------------------------------------------
# 四、SQLite 后端
# ---------------------------------------------------------------------------

class SqliteSessionPersistence(PersistenceBackend):
    """SQLite 存储适配器（session-persistence-sqlite/src/index.ts:99）。

    表结构（schema.ts）：sessions(:122) 行存在即 materialization 信号；events(:136) 一行一事件。
    [教学简化] 真实版有 persistence_state/sessions/events 三张表 + SCHEMA_VERSION=15（:20），
    configureDatabase 单事务执行 PRAGMA 集（:92）；教学版保留两张表、一条 WAL PRAGMA。
    """

    def __init__(self, db_path):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute("PRAGMA journal_mode = WAL")  # WAL：读写互不阻塞
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                revision   INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS events (
                session_id TEXT NOT NULL,
                seq        INTEGER NOT NULL,
                type       TEXT NOT NULL,
                payload    TEXT NOT NULL,
                PRIMARY KEY (session_id, seq)
            );
            """
        )
        self.conn.commit()

    def append_batch(self, session_id, events):
        """单事务批量写：BEGIN → INSERT 事件 → revision+1 → COMMIT（appendBatch，sqlite/index.ts:284）。"""
        with self.conn:  # sqlite3 的 with 块即单事务：成功提交、失败回滚
            self.conn.execute(
                "INSERT INTO sessions (session_id) VALUES (?) ON CONFLICT(session_id) DO NOTHING",
                (session_id,),
            )
            self.conn.executemany(
                "INSERT INTO events (session_id, seq, type, payload) VALUES (?, ?, ?, ?)",
                [
                    (e.session_id, e.seq, e.type, json.dumps(e.payload, ensure_ascii=False))
                    for e in events
                ],
            )
            self.conn.execute(
                "UPDATE sessions SET revision = revision + 1 WHERE session_id = ?",
                (session_id,),
            )

    def load_stored(self, session_id):
        """按 seq 升序读出事件（loadStored，sqlite/index.ts:207）。

        [教学简化] 真实版 scanRows(:232) 容忍最后一个 turn/end 之后的撕裂尾、
        拒绝已提交区损坏（lastTurnEnd :246-249、tornFrom :269）；
        教学版信任 SQLite 事务完整性，ORDER BY seq 后直接返回。
        """
        rows = self.conn.execute(
            "SELECT session_id, seq, type, payload FROM events WHERE session_id = ? ORDER BY seq ASC",
            (session_id,),
        ).fetchall()
        if not rows:
            return None
        return [
            SessionEvent(sid, seq, type_, json.loads(payload))
            for sid, seq, type_, payload in rows
        ]

    def commit_repair(self, session_id, events):
        """截断 + 补写（commitRepair，sqlite/index.ts:309）。"""
        with self.conn:
            self.conn.execute("DELETE FROM events WHERE session_id = ?", (session_id,))
            self.conn.executemany(
                "INSERT INTO events (session_id, seq, type, payload) VALUES (?, ?, ?, ?)",
                [
                    (e.session_id, e.seq, e.type, json.dumps(e.payload, ensure_ascii=False))
                    for e in events
                ],
            )

    def list(self):
        """枚举已持久化的会话（list，sqlite/index.ts:341）。"""
        rows = self.conn.execute("SELECT session_id FROM sessions ORDER BY session_id").fetchall()
        return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# 五、投影：注册表 + 缓存
# ---------------------------------------------------------------------------

class SessionProjectionRegistry:
    """投影注册表：每个投影单元把事件折叠成快照（session-projection/src/index.ts:171，真实版是 Service Definition（extends Service））。

    每个 ProjectionDefinition(:42) 真实成员为 key/schema/init/apply/view/stateVersion；
    [教学简化] 教学版保留 init/apply/view 三函数。
    增量路径：订阅 session/event、逐事件 drive（订阅 :181-183、私有 drive :405）；
    冷读路径：restore 从头折叠（:355）。
    """

    def __init__(self, ctx):
        self.ctx = ctx
        self._definitions = {}  # key -> {"init", "apply", "view"}
        self._states = {}       # session_id -> {key: 折叠状态}
        ctx.on("session/event", self._on_event)  # 增量驱动的订阅（:181-183）

    def register(self, key, init, apply, view):
        """注册一个投影单元（register，index.ts:194）。"""
        self._definitions[key] = {"init": init, "apply": apply, "view": view}

    def _on_event(self, event):
        """收到 session/event：驱动该会话的所有投影单元。"""
        self.drive(event.session_id, event)

    def drive(self, session_id, event):
        """把一个事件折叠进每个单元的状态（私有 drive，index.ts:405）。"""
        states = self._states.setdefault(session_id, {})
        for key, definition in self._definitions.items():
            state = states.get(key)
            if state is None:
                state = definition["init"]()  # 首次驱动前先 init
            states[key] = definition["apply"](state, event)

    def restore(self, session_id, events):
        """冷读：从事件序列从头折叠重建快照（restore，index.ts:355）。"""
        states = {}
        for key, definition in self._definitions.items():
            state = definition["init"]()
            for event in events:
                state = definition["apply"](state, event)
            states[key] = state
        self._states[session_id] = states
        return self.snapshot(session_id)

    def snapshot(self, session_id):
        """各单元 view 合成快照 map（snapshot，index.ts:248；SessionProjectionMap，types.ts:17）。"""
        states = self._states.get(session_id, {})
        return {
            key: definition["view"](states[key])
            for key, definition in self._definitions.items()
            if key in states
        }


class SessionProjectionCache:
    """投影缓存：冷读加速（session-projection-cache/src/index.ts:71，真实版 extends Service）。

    installWritePath 订阅 session/event（:201），turn/end 强制写缓存（:206-208）；
    冷读时先查缓存（cachedSnapshot :119），未命中再 coldSnapshot 一次性构建（:166）。
    [教学简化] 真实版把缓存持久化到磁盘（checkpoint ver/seq/val）并做计数/时间节流（:201-239）；
    教学版缓存留在内存，保留「turn/end 强制写 + 冷读查缓存」两个语义。
    """

    def __init__(self, ctx, registry):
        self.ctx = ctx
        self.registry = registry
        self._snapshots = {}  # session_id -> 快照 map
        ctx.on("session/event", self._on_event)

    def _on_event(self, event):
        """收到 session/event：turn 结束时强制写一次缓存（:206-208）。"""
        if event.type == "turn/end":
            self.write(event.session_id)

    def write(self, session_id):
        """把注册表当前快照写入缓存（write，:140）。"""
        self._snapshots[session_id] = self.registry.snapshot(session_id)

    def cached_snapshot(self, session_id):
        """查缓存；未命中返回 None（cachedSnapshot，:119）。"""
        return self._snapshots.get(session_id)

    def cold_snapshot(self, session_id, events):
        """冷读快照：从头折叠构建并写入缓存（coldSnapshot，:166）。"""
        snapshot = self.registry.restore(session_id, events)
        self._snapshots[session_id] = snapshot
        return snapshot


# ---------------------------------------------------------------------------
# 六、标题投影单元与遥测
# ---------------------------------------------------------------------------

def title_init():
    """标题单元的 init：初始状态为无标题（ProjectionDefinition.init，index.ts:42）。"""
    return None


def title_apply(state, event):
    """标题单元的 apply：只对 session/title 事件做 last-wins 折叠。

    对应 foldSessionTitle（session-title/src/index.ts:191）；
    apply 是纯同步函数（index.ts:42），其余事件原样返回状态。
    """
    if event.type == "session/title":
        return event.payload["title"]  # last-wins：后到的标题覆盖先前的
    return state


def title_view(state):
    """标题单元的 view：状态即对外快照值（ProjectionDefinition.view）。"""
    return state


class SessionTitleService(Service):
    """标题服务（SessionTitleService，session-title/src/index.ts:261）。

    注册标题投影单元（:308-317）；生成器产出标题后追加 session/title 日志事件（:374）——
    标题随其余事件一起入流、落盘，由投影 last-wins 折叠。
    [教学简化] 真实版 provider 可替换、可调用模型生成；教学版截取首条用户消息。
    """

    def __init__(self, ctx, registry):
        super().__init__(ctx, "session_title")
        registry.register("title", title_init, title_apply, title_view)  # 注册投影单元（:308-317）

    def generate(self, session):
        """生成标题并追加 session/title 事件（生成器追加事件，session-title/src/index.ts:374）。"""
        first_user = next((e for e in session.log if e.type == "user/message"), None)
        if first_user is None:
            return None
        text = first_user.payload["message"]["content"]
        title = text.rstrip("？?！!。，, ")[:20]  # [教学简化] 截取策略（真实版由 provider 生成）
        session.append("session/title", {"title": title})  # 标题作为事件进入事件流
        return title


class TelemetryRecorder:
    """遥测最小闭环：又一个 session/event 消费者。

    对应 SessionTelemetryCoordinator（session-telemetry/src/coordinator.ts:60）：
    captureEvent(:180) 对每个事件写一条 SessionTelemetryRecord(:64) 进 sink。
    [教学简化] 真实版还有 redaction waterfall、at-most-once 去重、固定 chunk 投影等；
    教学版只保留「每事件记一条」。
    """

    def __init__(self, ctx):
        self.records = []
        ctx.on("session/event", self._on_event)

    def _on_event(self, event):
        """收到 session/event：记录一条遥测（captureEvent，coordinator.ts:180）。"""
        self.records.append({"session_id": event.session_id, "seq": event.seq, "type": event.type})
