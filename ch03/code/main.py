"""第 3 章演示：session 事件流、handle 持久化与格式迁移。

仅标准库，Python 3.10+。运行：python3 main.py

演示段落：
1. 装配：seam + JSONL 后端 + live 路由 + 投影/缓存/标题/遥测
2. 创建会话：create 返回写句柄（所有权）
3. 跑一轮对话：事件由后端按 id 路由，session/flush 是耐久屏障
4. 所有权与只读：第二次写打开被拒，只读句柄不能 append
5. 冷读：新进程 open(read) + handle.read + 投影还原
6. 中断 turn 修复：读侧合成 turn/end(interrupted)，同一句柄作为普通批追加
7. 撕裂尾：半行不进已提交区；首次新写入前被截断
8. 格式迁移：v3 日志经 v3→v4 边升级，读出口永远当前格式
9. 拒绝：启动期链条缺口、更新版本
10. 遥测：FEEDBACK_ONLY 授权 + 脱敏 + sourceEvent 同一性
"""
import json
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
from agent_loop import (  # noqa: E402
    AgentLoop,
    LlmStub,
    SessionEvent,
    Sessions,
    SystemPromptService,
)
from session_persistence import (  # noqa: E402
    CURRENT_FORMAT_VERSION,
    JsonlSessionPersistence,
    SESSION_FORMAT_MIGRATIONS,
    SessionAlreadyOwnedError,
    SessionFormatChain,
    SessionFormatUnsupportedError,
    SessionProjectionCache,
    SessionProjectionRegistry,
    SessionReadOnlyError,
    SessionTelemetryCoordinator,
    SessionTelemetrySink,
    SessionTitleService,
    interrupted_turn_closers,
)


def build_ctx(root):
    """装配一套最小容器：后端（含 live 路由）+ 投影 + 缓存 + 标题 + 遥测。"""
    ctx = Context()
    backend = JsonlSessionPersistence(ctx, root)
    registry = SessionProjectionRegistry(ctx)
    SessionTitleService(ctx, registry)
    cache = SessionProjectionCache(ctx, registry)
    sink = SessionTelemetrySink()
    telemetry = SessionTelemetryCoordinator(ctx, sink)
    return ctx, backend, registry, cache, telemetry, sink


def main():
    root = tempfile.mkdtemp(prefix="ch03-persist-")

    # -- 1. 装配 --
    print("== 1. 装配：seam + JSONL 后端 + live 路由 ==")
    ctx, backend, registry, cache, telemetry, sink = build_ctx(root)
    Sessions(ctx)
    ctx.provide("llm", LlmStub())
    SystemPromptService(ctx)
    ctx.systemPrompt.section("identity", "你是最小教学代理，仅标准库、无工具。", order=0)
    ctx.plugin(AgentLoop)
    print("  ctx.sessionPersistence -> JsonlSessionPersistence")
    print("  live 路由：session/event -> 活跃写句柄批量窗；session/flush -> drain+flush")

    # -- 2. 创建会话：create 返回写句柄 --
    print("\n== 2. 创建会话：create 返回写句柄 ==")
    agent = ctx.agent_loop.create()
    session = agent.session
    session_id = session.id
    header = {"id": session_id, "version": CURRENT_FORMAT_VERSION, "cwd": "/tmp/demo"}
    handle = backend.create(header)
    print(f"  create({session_id}) -> access={handle.access}，写所有权已认领")
    print(f"  stat：{backend.stat(session_id)}")

    # -- 3. 跑一轮对话 --
    print("\n== 3. 跑一轮对话：live 事件按 id 路由进写句柄 ==")
    agent.followup("什么是 Cordis？")
    title = ctx.session_title.generate(session)
    handle.flush()  # 耐久屏障
    print(f"  session={session_id}：内存 {len(session.log)} 条事件（seq 0-{len(session.log) - 1}）")
    print(f"  生成标题：{title!r}（session/title 也是一条事件）")
    print(f"  活快照：{registry.snapshot(session_id)}")
    lines = backend.log_path(session_id).read_text(encoding="utf-8").splitlines()
    print(f"  flush 后磁盘 {len(lines)} 行（1 行头 + {len(lines) - 1} 行事件）")
    print(f"  首行：{lines[0]}")
    print(f"  末行：{lines[-1]}")

    # -- 4. 所有权与只读 --
    print("\n== 4. 所有权：单写者、只读句柄只能读 ==")
    try:
        backend.open(session_id, "write")
    except SessionAlreadyOwnedError as error:
        print(f"  第二次 open(write)：{type(error).__name__}")
    reader = backend.open(session_id, "read")
    print(f"  open(read) -> access={reader.access}，读回 {len(reader.read())} 条")
    try:
        reader.append([])
    except SessionReadOnlyError as error:
        print(f"  只读句柄 append：{type(error).__name__}")
    reader.close()
    handle.close()

    # -- 5. 冷读：新进程 --
    print("\n== 5. 冷读：新进程 open(read) + handle.read ==")
    ctx2 = Context()
    backend2 = JsonlSessionPersistence(ctx2, root)     # 同一数据目录
    registry2 = SessionProjectionRegistry(ctx2)
    SessionTitleService(ctx2, registry2)
    cache2 = SessionProjectionCache(ctx2, registry2)
    cold = backend2.open(session_id, "read")
    events = cold.read()
    print(f"  open(read) + read()：{len(events)} 条事件、header.version={cold.header['version']}")
    print(f"  缓存查找：{cache2.cached_snapshot(session_id)}")
    snap = cache2.cold_snapshot(session_id, events)
    print(f"  cold_snapshot 构建：{snap}")
    print(f"  再次缓存查找：命中={cache2.cached_snapshot(session_id) is snap}")
    cold.close()

    # -- 6. 中断 turn 修复：读侧，写到同一句柄 --
    print("\n== 6. 中断 turn 修复：断电留下未闭合 turn ==")
    path = backend.log_path(session_id)
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps({"session_id": session_id, "seq": len(session.log),
                               "type": "turn/start", "payload": {}}, ensure_ascii=False) + "\n")
    writer = backend2.open(session_id, "write")
    persisted = writer.read()
    closers = interrupted_turn_closers(persisted)
    base = len(persisted)
    writer.append([SessionEvent(session_id, base + i, c.type, c.payload)
                   for i, c in enumerate(closers)])
    writer.flush()
    print(f"  open(write) 读回 {len(persisted)} 条，合成 {len(closers)} 条收尾")
    print(f"  合成收尾：{closers[-1].type} {closers[-1].payload}")
    print(f"  append 后磁盘 {len(path.read_text(encoding='utf-8').splitlines())} 行")
    writer.close()

    # -- 7. 撕裂尾 --
    print("\n== 7. 撕裂尾：半行不进已提交区 ==")
    with open(path, "a", encoding="utf-8") as file:
        file.write('{"session_id": "%s", "seq": 99, "type": "tur' % session_id)  # 半行
    writer2 = backend2.open(session_id, "write")
    readable = writer2.read()
    print(f"  读到 {len(readable)} 条（半行被挡在已提交区之外）")
    writer2.append([SessionEvent(session_id, len(readable), "session/title",
                                 {"title": "截断后写入"})])
    writer2.flush()
    print(f"  首次新写入后磁盘 {len(path.read_text(encoding='utf-8').splitlines())} 行（半行已截掉）")
    writer2.close()

    # -- 8. 格式迁移 v3→v4 --
    print("\n== 8. 格式迁移：v3 日志经 v3→v4 边升级 ==")
    legacy_id = "legacy-v3"
    _write_v3_log(backend.log_path(legacy_id), legacy_id)
    legacy = backend2.open(legacy_id, "read")
    print(f"  磁盘头：version=3；open(read) 后 header.version={legacy.header['version']}")
    lifted = legacy.read()
    print(f"  读出口 {len(lifted)} 条事件，类型：{[e.type for e in lifted]}")
    legacy.close()
    print(f"  stat：header.version={backend2.stat(legacy_id)['header']['version']}（对调用方永远是当前格式）")

    # -- 9. 拒绝：链条缺口 / 更新版本 --
    print("\n== 9. 拒绝：链条缺口与更新版本 ==")
    try:
        SessionFormatChain(4, SESSION_FORMAT_MIGRATIONS[:2])   # 缺 v2→v3、v3→v4
    except SessionFormatUnsupportedError as error:
        print(f"  启动期缺边：{error}")
    newer_id = "from-newer-build"
    _write_newer_log(backend.log_path(newer_id), newer_id)
    try:
        backend2.open(newer_id, "read")
    except SessionFormatUnsupportedError as error:
        print(f"  读到 v5：{error}")

    # -- 10. 遥测 --
    print("\n== 10. 遥测：FEEDBACK_ONLY 授权 + 脱敏 + 同一性 ==")
    print(f"  普通事件后：records={len(sink.records)}（未授权，一条都没上传）")
    session.append("feedback/record", {"rating": "up", "note": "ok"})
    print(f"  一条 feedback 后：records={len(sink.records)}，"
          f"末条 seq={sink.records[-1]['sourceEvent']['event']['seq']}")
    # 脱敏 waterfall：删掉 sourceEvent 即拒发（fail-closed）
    ctx.on("session-telemetry/record", lambda record: {k: v for k, v in record.items()
                                                       if k != "sourceEvent"})
    session.append("feedback/record", {"rating": "down", "note": "bad"})
    print(f"  脱敏删掉 sourceEvent 后：records={len(sink.records)}，withheld={sink.withheld}")

    shutil.rmtree(root, ignore_errors=True)


def _write_v3_log(path, session_id):
    """手工造一份 v3 日志：assistant 消息里内嵌 tool_results（v4 之前的老形状）。"""
    header = {"id": session_id, "version": 3, "cwd": "/tmp/legacy"}
    rows = [
        {"session_id": session_id, "seq": 0, "type": "turn/start", "payload": {}},
        {"session_id": session_id, "seq": 1, "type": "assistant/message", "payload": {
            "message": {"role": "assistant", "content": "查一下"},
            "tool_results": [{"call_id": "call-1", "content": "42"}],
        }},
        {"session_id": session_id, "seq": 2, "type": "turn/end", "payload": {"reason": "completed"}},
    ]
    lines = [json.dumps(header, ensure_ascii=False)]
    lines += [json.dumps(row, ensure_ascii=False) for row in rows]
    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")


def _write_newer_log(path, session_id):
    header = {"id": session_id, "version": 5, "cwd": "/tmp/future"}
    with open(path, "w", encoding="utf-8") as file:
        file.write(json.dumps(header, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
