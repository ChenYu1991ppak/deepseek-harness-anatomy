"""第 11 章教学重构：Team 共享任务板（整块快照 + revision CAS + 环检测 + 投影）。

仅标准库，Python 3.10+。自测：python3 task_board.py

源码对应（以下路径均相对上游仓库根 /Users/chenyu/PycharmProjects/deepseek-harness）：
- TeamTaskBoard.create / update ↔ packages/team/agent-team/src/task-board.ts:55 / :117
- revision CAS（expectedRevision 不匹配即拒）↔ packages/team/agent-team/src/task-board.ts:128-133
- blockedBy 无环校验 ↔ packages/team/agent-team/src/task-graph.ts:26-69
- writeScopes 只告警、不封锁 ↔ packages/team/agent-team/src/task-view.ts:35-55
- TeamJournal.transact（每个 Lead 一条串行队列）↔ packages/team/agent-team/src/journal.ts:44-54
- TeamJournal.appendAndFlush（根会话 append('team/task') + flush）↔ packages/team/agent-team/src/journal.ts:62-74
- team/task 与 team/mode 的增量重放 ↔ packages/team/agent-team/src/projection.ts:205（teamProjectionDefinition）
- 成员权威：rootOf / listDescendants ↔ packages/team/agent-team/src/index.ts:95、task-board.ts:229-234
- 任务视图派生（ready / writeScopeWarnings）↔ packages/team/agent-team/src/task-view.ts:22 / :35
- 四个 team_task_* 工具 ↔ packages/team/tool-agent-team/src/index.ts:113 / :138 / :170 / :184

[教学简化] 真实版的任务状态住在 Session 投影里（sessionProjections 注册、可跨进程冷读），
教学版直接从根会话的事件列表即时重放——「日志即真相、视图是派生」的语义相同，少了缓存与失败降级。
[教学简化] 真实版的 transact 是异步 Promise 队列；教学版同步执行，串行语义靠调用顺序体现。
[教学简化] 真实版 TeamService.setEnabled 写在 Service 上（packages/team/agent-team/src/index.ts:111）；教学版并入任务板。
"""
from __future__ import annotations

MAX_TASKS = 256


class TeamError(Exception):
    """Team 的统一错误，带机器可读 code（真实版 TeamError，packages/team/agent-team/src/error.ts）。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def scopes_overlap(left, right):
    """两个写作用域前缀是否在路径分量上重叠（packages/team/agent-team/src/task-view.ts:12-14）。"""
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def task_ready(state, task):
    """任务的每个 blocker 都已完成（taskReady，packages/team/agent-team/src/task-view.ts:22）。"""
    return all(state["tasks"].get(b, {}).get("status") == "completed" for b in task["blockedBy"])


def project_task_view(state, task):
    """派生任务视图：ready + 与其他 in_progress 任务的写作用域重叠告警（projectTaskView，packages/team/agent-team/src/task-view.ts:35）。"""
    warnings = set()
    for other in state["tasks"].values():
        if other["id"] == task["id"] or other["status"] != "in_progress":
            continue
        if any(scopes_overlap(a, b) for a in task["writeScopes"] for b in other["writeScopes"]):
            warnings.add(f"write scopes overlap with {other['id']}")
    view = dict(task)
    view["ready"] = task["status"] == "pending" and task_ready(state, task)
    view["writeScopeWarnings"] = sorted(warnings)
    return view


class TeamJournal:
    """每个 Lead 一条事务队列；任务以 team/task 事件追加到根会话并 flush 才算确认（journal.ts）。"""

    def __init__(self):
        self.durable = True   # False 用于演示「append 成功但 flush 无人确认」

    def transact(self, root_id, operation):
        """把一次读-检查-追加操作串到该 Lead 的队列尾部（journal.ts:44-54）。"""
        return operation()

    def append_and_flush(self, root, task):
        """追加 team/task 并 flush；flush 失败抛 TEAM_TASK_PERSISTENCE_UNCONFIRMED（journal.ts:62-74）。"""
        root.session.append("team/task", {"version": 2, "task": task})
        if not self.durable:
            raise TeamError(
                "TEAM_TASK_PERSISTENCE_UNCONFIRMED",
                f"任务 \"{task['id']}\" revision {task['revision']} 已可见，但持久化未确认；"
                "重试前先读回该任务")

    def set_mode(self, root, enabled):
        """写 team/mode 并 flush（TeamService.setEnabled，packages/team/agent-team/src/index.ts:111-117）。"""
        root.session.append("team/mode", {"enabled": enabled})

    def state(self, root):
        """重放根会话日志里的 team/task、team/mode（teamProjectionDefinition，packages/team/agent-team/src/projection.ts:205）。"""
        tasks, enabled, next_number = {}, None, 1
        for ev in root.session.log:
            if ev.type == "team/mode":
                enabled = ev.payload["enabled"]
            elif ev.type == "team/task":
                task = ev.payload["task"]
                tasks[task["id"]] = task
                next_number = max(next_number, int(task["id"].split("-")[1]) + 1)
        return {"enabled": enabled, "tasks": tasks, "nextTaskNumber": next_number}


def list_descendants(subagents, root_session_id):
    """沿 lineage 收整棵子树的会话 id（listDescendants，packages/subagent/subagent/src/index.ts:380）。

    [教学简化] 真实版按投影目录逐层读取并区分 continuable / diagnostic；
    教学版复用第 11 章的 session_meta 谱系表，不区分 mode。
    """
    result, frontier = [], [root_session_id]
    while frontier:
        current = frontier.pop(0)
        for child in subagents.list_children(current):
            result.append(child)
            frontier.append(child)
    return result


class TeamTaskBoard:
    """共享任务板：不建 Agent、不调度，只维护一棵挂在根会话日志上的任务 DAG。"""

    def __init__(self, journal, subagents, max_tasks=MAX_TASKS):
        self.journal = journal
        self.subagents = subagents
        self.max_tasks = max_tasks

    # ---------- 读 ----------

    def get(self, root, task_id):
        state = self._state(root)
        task = state["tasks"].get(task_id)
        if task is None:
            raise TeamError("TEAM_TASK_NOT_FOUND", f"team task \"{task_id}\" not found")
        return project_task_view(state, task)

    def list_tasks(self, root):
        state = self._state(root)
        return [project_task_view(state, t) for t in state["tasks"].values() if t["status"] != "deleted"]

    def set_enabled(self, root, enabled):
        self.journal.set_mode(root, enabled)

    # ---------- 写（全部经 journal.transact 串行化 + CAS） ----------

    def create(self, caller, root, subject, blocked_by=(), write_scopes=()):
        def operation():
            state = self._state(root)
            active = sum(1 for t in state["tasks"].values() if t["status"] != "deleted")
            if active >= self.max_tasks:
                raise TeamError("TEAM_TASK_LIMIT", f"Team task limit {self.max_tasks} reached")
            task = {
                "id": f"task-{state['nextTaskNumber']}",
                "revision": 1,
                "subject": subject,
                "description": subject,
                "status": "pending",
                "blockedBy": self._dependencies(state, blocked_by),
                "writeScopes": sorted(set(write_scopes)),
            }
            self._assert_graph(state, task)
            self.journal.append_and_flush(root, task)
            return project_task_view(state, task)

        return self.journal.transact(root.session.id, operation)

    def update(self, caller, root, task_id, expected_revision, action, **fields):
        def operation():
            state = self._state(root)
            current = state["tasks"].get(task_id)
            if current is None:
                raise TeamError("TEAM_TASK_NOT_FOUND", f"team task \"{task_id}\" not found")
            # 整块快照 CAS：读到的 revision 与请求不一致，说明别人先改了（packages/team/agent-team/src/task-board.ts:128-133）
            if current["revision"] != expected_revision:
                raise TeamError(
                    "TEAM_TASK_STALE_REVISION",
                    f"stale team task \"{task_id}\" revision {expected_revision}; "
                    f"current revision is {current['revision']}")
            lead = caller is root
            owner = current.get("ownerId") == caller.session.id

            def authorize_owner():
                if not lead and not owner:
                    raise TeamError("TEAM_TASK_UNAUTHORIZED", "task mutation requires its owner or Team Lead")

            nxt = dict(current)
            if action == "claim":
                if current.get("ownerId") not in (None, caller.session.id):
                    raise TeamError("TEAM_TASK_ALREADY_CLAIMED",
                                    f"team task \"{task_id}\" is owned by another member")
                if current["status"] != "pending" or not task_ready(state, current):
                    raise TeamError("TEAM_TASK_BLOCKED", f"team task \"{task_id}\" is not ready to claim")
                nxt.update(status="in_progress", ownerId=caller.session.id)
            elif action == "complete":
                authorize_owner()
                if current["status"] != "in_progress":
                    raise TeamError("TEAM_TASK_INVALID_TRANSITION", "only an in-progress task can complete")
                nxt.update(status="completed")
            elif action == "set_dependencies":
                authorize_owner()
                nxt["blockedBy"] = self._dependencies(state, fields.get("blocked_by", []), task_id)
            elif action == "reassign":
                if not lead:
                    raise TeamError("TEAM_LEAD_REQUIRED", "only the Team Lead can reassign tasks")
                target = self._resolve_assignee(root, fields["owner"])
                nxt.update(status="in_progress", ownerId=target)
            else:
                raise TeamError("TEAM_INVALID_ARGUMENT", f"unsupported task action {action!r}")

            nxt["revision"] = current["revision"] + 1
            self._assert_graph(state, nxt)
            self.journal.append_and_flush(root, nxt)
            return project_task_view(state, nxt)

        return self.journal.transact(root.session.id, operation)

    # ---------- 内部 ----------

    def _state(self, root):
        state = self.journal.state(root)
        if state["enabled"] is not True:
            raise TeamError("TEAM_DISABLED", "enable Agent Teams for this Session before using the task board")
        return state

    def _resolve_assignee(self, root, target):
        """改派对象必须是根自己或某个 continuable 后代（resolveAssignee，packages/team/agent-team/src/task-board.ts:229-234）。"""
        if target == root.session.id:
            return target
        if target in list_descendants(self.subagents, root.session.id):
            return target
        raise TeamError("TEAM_MEMBER_NOT_FOUND",
                        f"agent \"{target}\" is not a continuable descendant of this task board's root")

    def _dependencies(self, state, values, self_id=None):
        result = []
        for blocker in values:
            if blocker == self_id:
                raise TeamError("TEAM_TASK_DEPENDENCY_CYCLE", "a team task cannot block itself")
            if blocker in result:
                raise TeamError("TEAM_INVALID_ARGUMENT", f"duplicate blocker \"{blocker}\"")
            task = state["tasks"].get(blocker)
            if task is None or task["status"] == "deleted":
                raise TeamError("TEAM_TASK_NOT_FOUND", f"blocker task \"{blocker}\" not found")
            result.append(blocker)
        return result

    def _assert_graph(self, state, candidate):
        """替换候选快照后整图校验：缺失、自环、重复、成环（task-graph.ts:26-69，packages/team/agent-team/src/）。"""
        tasks = dict(state["tasks"])
        tasks[candidate["id"]] = candidate
        for task in tasks.values():
            if task["status"] == "deleted":
                continue
            for blocker in task["blockedBy"]:
                if blocker == task["id"]:
                    raise TeamError("TEAM_TASK_DEPENDENCY_CYCLE", f"team task \"{task['id']}\" cannot block itself")
                other = tasks.get(blocker)
                if other is None or other["status"] == "deleted":
                    raise TeamError("TEAM_TASK_NOT_FOUND", f"blocker task \"{blocker}\" not found")
        visiting, visited = set(), set()

        def visit(task_id):
            if task_id in visiting:
                raise TeamError("TEAM_TASK_DEPENDENCY_CYCLE",
                                f"task dependency cycle includes \"{task_id}\"")
            if task_id in visited:
                return
            task = tasks.get(task_id)
            if task is None or task["status"] == "deleted":
                return
            visiting.add(task_id)
            for blocker in task["blockedBy"]:
                visit(blocker)
            visiting.discard(task_id)
            visited.add(task_id)

        for task_id in tasks:
            visit(task_id)


# ---------- 自测：不依赖第 11 章管理器，用最小假对象演示 CAS 与环检测 ----------

class _Session:
    def __init__(self, sid):
        self.id = sid
        self.log = []

    def append(self, event_type, payload):
        self.log.append(_Event(event_type, payload))


class _Event:
    def __init__(self, event_type, payload):
        self.type = event_type
        self.payload = payload


class _Agent:
    def __init__(self, sid):
        self.session = _Session(sid)


if __name__ == "__main__":
    root, alice = _Agent("session-root"), _Agent("session-alice")
    journal = TeamJournal()
    board = TeamTaskBoard(journal, subagents=None)
    board.set_enabled(root, True)

    first = board.create(root, root, "修构建", write_scopes=["pkg/a"])
    print(f"task: {first['id']} revision={first['revision']} ready={first['ready']}")
    claimed = board.update(alice, root, first["id"], 1, "claim")
    print(f"alice 认领: revision={claimed['revision']} status={claimed['status']} owner={claimed['ownerId']}")
    try:
        board.update(root, root, first["id"], 1, "claim")
    except TeamError as e:
        print(f"用旧 revision 再认领: 被拒 {e.code}")
    second = board.create(root, root, "补测试", blocked_by=[first["id"]])
    try:
        board.update(root, root, first["id"], 2, "set_dependencies", blocked_by=[second["id"]])
    except TeamError as e:
        print(f"反向依赖: 被拒 {e.code}: {e}")
    print(f"根会话事件 {len(root.session.log)} 条，投影出 {len(board.list_tasks(root))} 个任务")
