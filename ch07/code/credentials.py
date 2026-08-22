"""第 7 章教学重构：凭据 seam 与设置 seam。

仅标准库，Python 3.10+。

源码对应（行号取自 notes §2.4/§2.5）：
- CredentialProvider ↔ packages/credentials/src/index.ts:60
  （resolve :73 / describe :81 / set :91 / unset :99 / notifyUpdated :115）
- SettingsProvider   ↔ packages/settings/src/index.ts:350
  （register :435 / describe :479 / get :519 / update :534 / replace :548）

[教学简化] 真实版 resolve 为 async 接口；教学版用同步方法，机制语义不变。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

__all__ = [
    "CredentialProvider",
    "InMemoryCredentialProvider",
    "SettingsProvider",
    "InMemorySettingsProvider",
]


# ---------- 凭据 seam ----------


class CredentialProvider(ABC):
    """凭据抽象：secret 读写的唯一通道。

    resolve() 读 secret（带 source 标记）；describe() 只回元数据不含 secret；
    set()/unset() 写入/删除；notify_updated() 广播变更事件。
    """

    @abstractmethod
    def resolve(self, ref):
        """解析凭据，返回 {'secret': ..., 'source': ...}（真实版为 async，index.ts:73）。"""

    @abstractmethod
    def describe(self, ref):
        """返回凭据元数据（不含 secret，index.ts:81）。"""

    @abstractmethod
    def set(self, ref, secret, source="manual"):
        """写入凭据（index.ts:91）。"""

    @abstractmethod
    def unset(self, ref):
        """删除凭据（index.ts:99）。"""

    def notify_updated(self, ref):
        """广播凭据变更事件（index.ts:115 notifyUpdated）；默认空实现，子类覆写。"""


class InMemoryCredentialProvider(CredentialProvider):
    """内存凭据实现（教学桩：真实版可接 keychain / 环境变量等后端）。"""

    def __init__(self):
        self._store = {}       # ref -> {"secret": ..., "source": ...}
        self._listeners = []   # 变更监听器

    def on_updated(self, listener):
        self._listeners.append(listener)

    def set(self, ref, secret, source="manual"):
        self._store[ref] = {"secret": secret, "source": source}
        self.notify_updated(ref)

    def unset(self, ref):
        self._store.pop(ref, None)
        self.notify_updated(ref)

    def resolve(self, ref):
        entry = self._store.get(ref)
        if entry is None:
            raise KeyError(f"credential not found: {ref}")
        return {"secret": entry["secret"], "source": entry["source"]}

    def describe(self, ref):
        entry = self._store.get(ref)
        if entry is None:
            return None
        return {"ref": ref, "source": entry["source"]}  # 元数据不含 secret

    def notify_updated(self, ref):
        for listener in self._listeners:
            listener(ref)


# ---------- 设置 seam ----------


class SettingsProvider(ABC):
    """设置抽象：register/describe 声明设置项；get/update/replace 读写。"""

    @abstractmethod
    def register(self, path, default, description=""):
        """注册设置项（index.ts:435）。"""

    @abstractmethod
    def describe(self, path):
        """返回设置项元数据（index.ts:479）。"""

    @abstractmethod
    def get(self, path):
        """读取设置值（index.ts:519）。"""

    @abstractmethod
    def update(self, path, patch):
        """dict 型设置值的部分更新（index.ts:534）。"""

    @abstractmethod
    def replace(self, path, value):
        """整体替换设置值（index.ts:548）。"""


class InMemorySettingsProvider(SettingsProvider):
    """内存设置实现（教学桩）。"""

    def __init__(self):
        self._items = {}  # path -> {"value": ..., "default": ..., "description": ...}

    def register(self, path, default, description=""):
        self._items[path] = {"value": default, "default": default, "description": description}

    def describe(self, path):
        item = self._items.get(path)
        if item is None:
            return None
        return {"path": path, "default": item["default"], "description": item["description"]}

    def get(self, path):
        item = self._items.get(path)
        return None if item is None else item["value"]

    def update(self, path, patch):
        item = self._items[path]
        if not isinstance(item["value"], dict) or not isinstance(patch, dict):
            raise TypeError("update 仅用于 dict 型设置值的部分更新")
        item["value"].update(patch)

    def replace(self, path, value):
        self._items[path]["value"] = value
