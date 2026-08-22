"""第 3 章演示：session 事件流与持久化投影。

仅标准库，Python 3.10+。运行：python3 main.py

演示段落：
1. 装配：seam + JSONL 后端 + 写路径（协调器订阅 session/event）
2. 跑一轮对话：事件入 write-behind，turn/end 批量落盘
3. 生成标题：session/title 进入事件流，session/flush 屏障立即落盘
4. 冷读：模拟进程重启，从磁盘重建会话 + 冷读投影快照
5. 中断 turn 修复：断电留下未闭合 turn，load 合成 turn/end 并落盘
6. 撕裂写：半行尾巴被丢弃，不影响冷读
7. 换用 SQLite：同一契约，不同存储
8. 遥测：又一个 session/event 消费者
"""
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
_CH02 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch02", "code"))
for _p in (_HERE, _CH02, _CH01):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Context  # noqa: E402
from agent_loop import AgentLoop, LlmStub, Sessions, SystemPromptService  # noqa: E402
from session_persistence import (  # noqa: E402
    JsonlSessionPersistence,
    PersistenceCoordinator,
    SessionProjectionCache,
    SessionProjectionRegistry,
    SessionTitleService,
    SqliteSessionPersistence,
    TelemetryRecorder,
)


def main():
    root = tempfile.mkdtemp(prefix="ch03-persist-")

    # -- 1. 装配：seam + JSONL 后端 + 写路径 --
    print("== 1. 装配：seam + JSONL 后端 + 写路径 ==")
    ctx = Context()
    backend = JsonlSessionPersistence(root)
    coordinator = PersistenceCoordinator(ctx, backend)  # 绑定为 ctx.sessionPersistence
    registry = SessionProjectionRegistry(ctx)
    SessionTitleService(ctx, registry)                  # 注册标题投影单元
    cache = SessionProjectionCache(ctx, registry)
    telemetry = TelemetryRecorder(ctx)
    Sessions(ctx)
    ctx.provide("llm", LlmStub())
    SystemPromptService(ctx)
    ctx.systemPrompt.section("identity", "你是最小教学代理，仅标准库、无工具。", order=0)
    ctx.plugin(AgentLoop)
    print("  ctx.sessionPersistence -> PersistenceCoordinator(backend=JsonlSessionPersistence)")
    print("  写路径：session/event -> WriteBehind 入队 -> turn/end 批量 flush")

    # -- 2. 跑一轮对话：事件流向磁盘 --
    print("\n== 2. 跑一轮对话：事件流向磁盘 ==")
    agent = ctx.agent_loop.create()
    agent.followup("什么是 Cordis？")
    session_id = agent.session.id
    print(f"  session={session_id}：内存 {len(agent.session.log)} 条事件（seq 0-{len(agent.session.log) - 1}）")
    print(f"  turn/end 触发批量 flush：cursor={coordinator.cursors[session_id]}")
    lines = backend.log_path(session_id).read_text(encoding="utf-8").splitlines()
    print(f"  磁盘 {len(lines)} 行（一行一个 JSON 事件）")
    print(f"  首行：{lines[0]}")
    print(f"  末行：{lines[-1]}")

    # -- 3. 生成标题：session/title 进入事件流 --
    print("\n== 3. 生成标题：session/title 进入事件流 ==")
    title = ctx.session_title.generate(agent.session)
    print(f"  生成标题：{title!r}（session/title seq={len(agent.session.log) - 1}，已入队未 flush）")
    ctx.emit("session/flush", session_id)  # 立即落盘屏障
    lines = backend.log_path(session_id).read_text(encoding="utf-8").splitlines()
    print(f"  session/flush 后磁盘 {len(lines)} 行")
    print(f"  活快照：{registry.snapshot(session_id)}")

    # -- 4. 冷读：模拟进程重启 --
    print("\n== 4. 冷读：模拟进程重启 ==")
    ctx2 = Context()
    backend2 = JsonlSessionPersistence(root)  # 同一数据目录
    coordinator2 = PersistenceCoordinator(ctx2, backend2)
    registry2 = SessionProjectionRegistry(ctx2)
    SessionTitleService(ctx2, registry2)
    cache2 = SessionProjectionCache(ctx2, registry2)
    session2 = coordinator2.load(session_id)
    print(f"  冷读：重建 session={session2.id}，{len(session2.log)} 条事件（replay 自磁盘）")
    print(f"  缓存查找：{cache2.cached_snapshot(session_id)}")
    snap = cache2.cold_snapshot(session_id, session2.log)
    print(f"  cold_snapshot 构建：{snap}")
    print(f"  再次缓存查找：命中={cache2.cached_snapshot(session_id) is snap}")

    # -- 5. 中断 turn 修复 --
    print("\n== 5. 中断 turn 修复：断电留下未闭合 turn ==")
    path = backend.log_path(session_id)
    with open(path, "a", encoding="utf-8") as f:
        # 模拟：turn/start 已落盘、进程在 turn/end 之前死亡
        f.write('{"session_id": "session-0001", "seq": 10, "type": "turn/start", "payload": {}}\n')
    session3 = coordinator2.load(session_id)
    lines = path.read_text(encoding="utf-8").splitlines()
    print(f"  手工追加 turn/start（seq=10），冷读修复为 {len(session3.log)} 条事件")
    print(f"  合成收尾：{session3.log[-1].type} {session3.log[-1].payload}")
    print(f"  commitRepair 落盘：磁盘 {len(lines)} 行")

    # -- 6. 撕裂写 --
    print("\n== 6. 撕裂写：半行尾巴被丢弃 ==")
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"session_id": "session-0001", "seq": 12, "type": "tur')  # 半行：模拟断电
    events = backend2.load_stored(session_id)
    print(f"  手工追加半行后 load_stored 读回 {len(events)} 条事件（半行被丢弃）")

    # -- 7. 换用 SQLite --
    print("\n== 7. 换用 SQLite：同一契约，不同存储 ==")
    sqlite_backend = SqliteSessionPersistence(os.path.join(root, "sessions.db"))
    sqlite_backend.append_batch(session_id, session3.log)  # 同一批事件，换存储
    events = sqlite_backend.load_stored(session_id)
    print(f"  append_batch 写入 sessions.db：{len(session3.log)} 条事件")
    print(f"  load_stored 读回 {len(events)} 条事件（ORDER BY seq）")
    print(f"  list()：{sqlite_backend.list()}")

    # -- 8. 遥测 --
    print("\n== 8. 遥测：又一个 session/event 消费者 ==")
    print(f"  共记录 {len(telemetry.records)} 条遥测")
    print(f"  末条：{telemetry.records[-1]}")

    shutil.rmtree(root, ignore_errors=True)  # 清理临时目录


if __name__ == "__main__":
    main()
