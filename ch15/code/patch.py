"""第 15 章：patch 层——对已有 entry 列表做「增量修改」（patch 而非 merge）。

一个 entry 代表「进程要加载的一个服务/插件」，形如：
    {"id": "settings", "config": {...}, "disabled": False}

一条 patch 是对 entry 列表的一次增量操作，三种形态：
    {"op": "insert",   "entry": {...}}            # 插入一个新 entry
    {"op": "override", "id": ..., "config": {...}} # 按 id 定向覆盖某个 entry 的 config
    {"op": "disable",  "id": ...}                  # 按 id 禁用某个 entry

[教学简化] 真实的 cordis.patch.yml 是「顶层 YAML 数组，元素为 PatchOptions」
（packages/boot/app-boot/src/index.ts:320 parsePatchList 解析，允许 !!js 内联函数）；
教学版用 Python dict + op 字段表达同一语义，只保留 insert/override/disable 三种。

本章题眼：patch 是「对已有配置的增量修改」，不是另一份独立配置——
后一层能精确地插入 / 覆盖 / 禁用前面某一层留下的条目，而整段 merge 做不到。
"""


def make_entry(entry_id, config=None):
    """造一个 entry：id + config + 未禁用。config 缺省为空 dict。"""
    return {"id": entry_id, "config": dict(config or {}), "disabled": False}


def apply_entry_patches(entries, patches):
    """把一批 patch 依序作用到 entry 列表上，返回新列表（不改原列表）。

    对应真实代码：include 的 applyEntryPatches——profile 栈拍平成 entry 列表的最后一步
    （packages/boot/app-boot/src/profile.ts:413 composeEntries 调用它）。

    实现要点：
    - 先浅拷贝每个 entry，保证「patch 作用在副本上」，原列表不被污染；
    - 用 by_id 索引定位目标，所以 override/disable 都是「id 定向」的；
    - insert 追加到列表尾部，并同步进索引，供后续 patch 继续定向它。
    """
    entries = [dict(e) for e in entries]          # 副本：不动调用方传入的列表
    by_id = {e["id"]: e for e in entries}         # id -> entry 的索引，供定向查找
    for patch in patches:
        op = patch["op"]
        if op == "insert":
            entry = dict(patch["entry"])          # 新 entry 也拷一份，避免共享引用
            entry.setdefault("config", {})
            entry.setdefault("disabled", False)
            entries.append(entry)                 # 追加到尾部
            by_id[entry["id"]] = entry            # 进索引，后面的 patch 能定向它
        elif op == "override":
            target = by_id.get(patch["id"])
            if target is not None:                # 定向不到就跳过（幂等、不报错）
                # 浅合并 config：只覆盖写明的键，其余键保留——这正是「增量」而非「整段替换」
                target["config"] = {**target["config"], **patch["config"]}
        elif op == "disable":
            target = by_id.get(patch["id"])
            if target is not None:
                target["disabled"] = True         # 只翻禁用位，不删除条目本身
    return entries
