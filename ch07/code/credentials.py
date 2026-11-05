"""第 7 章教学重构：凭据 seam 与设置 seam。

仅标准库，Python 3.10+。

源码对应（HEAD 8bd0733）：
- CredentialProvider       ↔ packages/credentials/credentials/src/index.ts:171
                              （resolve :184 / describe :192 / set :201 / unset :209 /
                               notifyUpdated :266；记录半区 readRecord :217 起）
- CredentialInfo           ↔ packages/credentials/credentials/src/types.ts:67
- LocalCredentialProvider  ↔ packages/credentials/credentials-local/src/index.ts:517
                              （resolve :624 / describe :634 / set :645 / unset :652）
- SettingsForms            ↔ packages/settings/settings/src/index.ts:223
                              （configure :266 / describe :302 / update :348 /
                               replace :358 / mutate :368）
- SettingsConflictError    ↔ packages/settings/settings/src/index.ts:45

[教学简化] 真实版 resolve/describe/set/unset 均为 async；教学版用同步方法，机制语义不变。
[教学简化] 真实版设置值来自 profile 里的插件 Config（各插件直接读自己的 Config 引用），
SettingsForms 只提供编辑面；教学版用 declare() 直接放入一份已解析值。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

__all__ = [
    "CredentialProvider",
    "InMemoryCredentialProvider",
    "SettingsConflictError",
    "InMemorySettingsForms",
]


# ---------- 凭据 seam ----------


class CredentialProvider(ABC):
    """凭据抽象：secret 读写的唯一通道。

    resolve() 读 secret（附带来源标记）；describe() 只回元数据、不回 secret；
    set()/unset() 写入/删除；notify_updated() 在写入提交后广播变更。
    """

    @abstractmethod
    def resolve(self, ref):
        """解析凭据，返回 {'value': ..., 'source': ...}；未配置返回 None。

        真实版为 async（packages/credentials/credentials/src/index.ts:184）。
        """

    @abstractmethod
    def describe(self, ref):
        """返回 {'configured', 'source', 'writable'}，不含 secret（index.ts:192）。"""

    @abstractmethod
    def set(self, ref, value):
        """写入凭据；空值被拒绝，删除请用 unset（index.ts:201）。"""

    @abstractmethod
    def unset(self, ref):
        """删除凭据；删除不存在的引用是 no-op（index.ts:209）。"""

    def notify_updated(self, ref):
        """写入提交后广播凭据变更（index.ts:266 notifyUpdated）；子类覆写。"""


class InMemoryCredentialProvider(CredentialProvider):
    """内存凭据实现（教学桩：真实版由 credentials-local 落盘）。"""

    def __init__(self):
        self._store = {}       # ref -> {"value": ..., "source": ...}
        self._listeners = []   # 变更监听器

    def on_updated(self, listener):
        self._listeners.append(listener)

    def set(self, ref, value, source="manual"):
        if value == "":
            raise ValueError("empty credential values are rejected; use unset")
        self._store[ref] = {"value": value, "source": source}
        self.notify_updated(ref)

    def unset(self, ref):
        self._store.pop(ref, None)
        self.notify_updated(ref)

    def resolve(self, ref):
        entry = self._store.get(ref)
        return None if entry is None else dict(entry)

    def describe(self, ref):
        entry = self._store.get(ref)
        if entry is None:
            return {"configured": False, "writable": True}
        return {"configured": True, "source": entry["source"], "writable": True}

    def notify_updated(self, ref):
        for listener in self._listeners:
            listener(ref)


# ---------- 设置 seam ----------


class SettingsConflictError(RuntimeError):
    """写入携带的 expected_revision 与当前值不符：拒绝写入而不是静默覆盖。

    对应 packages/settings/settings/src/index.ts:45。
    """


class InMemorySettingsForms:
    """设置编辑面（教学桩）：describe 给快照，update/replace/mutate 走 revision CAS。

    对应 SettingsForms（packages/settings/settings/src/index.ts:223）。
    真实版没有 register/get：设置项由插件的 Config schema 声明，业务代码直接读
    自己的 Config 引用；这个服务只负责把值投影成表单并接收编辑。
    """

    def __init__(self):
        self._docs = {}      # ns -> {"value": ..., "revision": ...}

    def declare(self, ns, value):
        """[教学简化] 真实版的值来自 profile 里的插件 Config；教学版直接放入。"""
        self._docs[ns] = {"value": dict(value), "revision": 0}

    def describe(self, ns):
        """读一份带 revision 的快照（index.ts:302）。"""
        doc = self._docs[ns]
        return {"ns": ns, "value": dict(doc["value"]), "revision": doc["revision"]}

    def _write(self, ns, change, expected_revision):
        doc = self._docs[ns]
        if expected_revision is not None and expected_revision != doc["revision"]:
            raise SettingsConflictError(f"{ns}: stale revision {expected_revision}")
        self._docs[ns] = {"value": change(dict(doc["value"])), "revision": doc["revision"] + 1}

    def update(self, ns, patch, expected_revision=None):
        """把 patch 合并进当前值（index.ts:348）。"""
        self._write(ns, lambda value: {**value, **patch}, expected_revision)

    def replace(self, ns, section, expected_revision=None):
        """整段替换（index.ts:358）。"""
        self._write(ns, lambda _value: dict(section), expected_revision)

    def mutate(self, ns, ops, expected_revision=None):
        """按路径编辑；op 形如 {'op': 'set'|'unset', 'path': [...]}（index.ts:368）。"""

        def apply(value):
            for op in ops:
                node = value
                for key in op["path"][:-1]:
                    node = node.setdefault(key, {})
                last = op["path"][-1]
                if op["op"] == "unset":
                    node.pop(last, None)
                else:
                    node[last] = op["value"]
            return value

        self._write(ns, apply, expected_revision)
