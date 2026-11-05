"""第 6 章教学重构：fs seam —— 文件系统能力。

仅标准库，Python 3.10+。运行：python3 main.py

进程只是执行世界的一半，另一半是文件系统。直接调 os/open 会丢三样东西：
统一错误格式、路径安全、版本守卫。本文件把文件系统做成 ctx.fs seam。

源码对应（packages/fs/）：
- FileSystem                 ↔ abstract FileSystem（packages/fs/fs/src/index.ts:103）
- write/edit/delete          ↔ writeText/editText/deleteFile（packages/fs/fs/src/index.ts:282/:303/:344；
                                 LocalFileSystem 在 packages/fs/fs-local/src/index.ts:74，writeText :216 / editText :250）
- FsError（统一错误码）       ↔ FsErrorCode（packages/fs/fs/src/types.ts:209）
- 版本守卫                    ↔ FS_STALE_VERSION/FS_NOT_OBSERVED（packages/fs/fs-local/src/index.ts:354-363）
- _resolve（realpath 身份）   ↔ resolveLocalTarget（packages/fs/fs-local/src/fsio.ts:179）
                                 + isPathUnder（packages/fs/fs-sandbox/src/containment.ts:58）
- 原子写 staging+rename       ↔ writeFileAtomic（packages/fs/fs-local/src/fsio.ts:618）
- 版本 token                  ↔ versionOf（packages/fs/fs-local/src/fsio.ts:76）+ FsVersion（types.ts:35）
- 意图瀑布                    ↔ 'fs/write-intent'/'fs/edit-intent'/'fs/delete-intent'
                                 （packages/fs/fs/src/index.ts:66/:74/:82；真实触发点在
                                 tool-fs 工具层，如 packages/fs/tool-fs/src/write.ts:121）

[教学简化] 真实版还有逐 key FIFO 锁 withLock（packages/fs/fs-local/src/index.ts:124）、
glob/grep、SandboxedFileSystem 的 canonicalize-then-contain；教学版只保留
read/write/edit/delete/list、版本守卫与意图瀑布主干。
"""
from __future__ import annotations

import hashlib
import os
import sys

_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from cordis import Service, Symbols  # noqa: E402

__all__ = ["FileSystem", "FsError"]


def _run_intent(ctx, event, target, actor, default=None):
    """意图瀑布调度器：复刻 cordis waterfall 的「最内层 next」语义。

    监听器按注册顺序串成一层层包裹，签名 (target, actor, next)：
    调 next() 表示「交给下一层」，不调就占住决策位（第一层不调 next，后面全不跑）。
    真实版返回 Promise（监听器可 async）；教学版同步执行。
    """
    listeners = list(getattr(ctx, Symbols.events).get(event, []))
    cursor = 0

    def dispatch():
        nonlocal cursor
        if cursor >= len(listeners):
            return default  # 最内层：没有任何监听器接管
        listener = listeners[cursor]
        cursor += 1
        called = False

        def next_():
            nonlocal called
            if called:
                raise RuntimeError("next() 被调用了两次")
            called = True
            return dispatch()

        return listener(target, actor, next_)

    return dispatch()


class FsError(Exception):
    """统一错误格式：一个错误码 + 路径 + 人话消息（packages/fs/fs/src/types.ts:209 FsErrorCode）。

    消费者只需 catch FsError 读 .code，不必分辨 FileNotFoundError /
    PermissionError / IsADirectoryError 等一堆标准库异常。
    """

    def __init__(self, code, path, message):
        super().__init__(f"[{code}] {path}: {message}")
        self.code = code
        self.path = path


class FileSystem(Service):
    """ctx.fs seam：read/write/edit/list + 路径安全 + 原子写 + 版本守卫。"""

    inject = []

    def __init__(self, ctx, root=None):
        super().__init__(ctx, "fs")  # 构造即注册到 ctx.fs
        self._root = os.path.realpath(root or os.getcwd())

    # -- 路径安全：realpath 身份 + workspace 包含
    #    （packages/fs/fs-local/src/fsio.ts:179 + packages/fs/fs-sandbox/src/containment.ts:58） --

    def _resolve(self, path):
        """把相对路径锚定到 workspace 根，并拒绝逃逸（canonicalize-then-contain）。"""
        resolved = os.path.realpath(os.path.join(self._root, path))
        if resolved != self._root and not resolved.startswith(self._root + os.sep):
            raise FsError("FS_PATH_ESCAPE", path, "路径逃逸 workspace 边界")
        return resolved

    # -- 版本守卫：不透明令牌（教学版用内容哈希充当 FsVersion） --

    @staticmethod
    def _version_of(target):
        with open(target, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]

    def _intent(self, event, target, actor):
        """执行一次意图瀑布，返回监听器给出的「计划」（None 表示走默认路径）。

        真实 cordis 的 waterfall 把最后一个参数当最内层 next，listener 签名是
        (target, actor, next)，第一个不调 next() 的监听器就占住了决策位。
        第 1 章 cordis.py 的 ctx.waterfall(value) 是简化的单参数串接；这里用一个
        显式的小调度器复刻真实形状，让监听器仍能收到 (target, actor, next) 三个参数。
        """
        return _run_intent(self.ctx, event, target, actor)

    # -- 读：返回内容 + 版本令牌（供后续写做守卫） --

    def read(self, path):
        target = self._resolve(path)
        if not os.path.exists(target):
            raise FsError("FS_NOT_FOUND", path, "文件不存在")
        if os.path.isdir(target):
            raise FsError("FS_IS_DIR", path, "目标是目录")
        with open(target, "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content, "version": self._version_of(target)}

    # -- 写：原子写（staging+rename）+ 版本守卫 --

    def write(self, path, content, base_version=None, actor=None):
        target = self._resolve(path)
        if os.path.isdir(target):
            raise FsError("FS_IS_DIR", path, "目标是目录")
        if os.path.exists(target):
            # 已存在的文件必须先 read 观察再写，杜绝盲覆盖。
            if base_version is None:
                raise FsError("FS_NOT_OBSERVED", path, "写入前必须先 read 观察")
            if self._version_of(target) != base_version:
                raise FsError("FS_STALE_VERSION", path, "版本已变化，写入被拒")
        # 意图瀑布：工具层在落盘之前广播，监听器（如 workspace-changes）趁旧内容还在时拷贝。
        # 真实版由 tool-fs 的 write 工具触发（packages/fs/tool-fs/src/write.ts:121），
        # 不在 LocalFileSystem 内部；教学版没有工具层，就在这里拦一道。
        self._intent("fs/write-intent", target, actor)
        return self._write_atomic(target, content)

    def _rewrite(self, target, content, observed_version):
        """读-改-写的守卫：落盘前把「刚读到的版本」再对一遍，不一致就拒写。"""
        if self._version_of(target) != observed_version:
            raise FsError("FS_STALE_VERSION", target, "版本已变化，写入被拒")
        return self._write_atomic(target, content)

    def _write_atomic(self, target, content):
        """原子写：先写临时 staging，再 rename 落位，避免半截文件（packages/fs/fs-local/src/fsio.ts:618）。"""
        staging = f"{target}.staging-{os.urandom(4).hex()}"
        try:
            with open(staging, "w", encoding="utf-8") as f:
                f.write(content)
            os.rename(staging, target)
        finally:
            if os.path.exists(staging):
                os.remove(staging)
        return {"version": self._version_of(target)}

    # -- 编辑：read→替换→带守卫写回 --

    def edit(self, path, old, new, base_version=None, actor=None):
        observed = self.read(path)
        if base_version is not None and observed["version"] != base_version:
            raise FsError("FS_STALE_VERSION", path, "版本已变化，编辑被拒")
        if old not in observed["content"]:
            raise FsError("FS_EDIT_NO_MATCH", path, "old 片段未找到")
        # 编辑只过一次 edit-intent；真实版 editText 不会再去碰 write-intent（tool-fs/src/edit.ts:133）。
        self._intent("fs/edit-intent", self._resolve(path), actor)
        new_content = observed["content"].replace(old, new, 1)
        # 写回时带上「刚读到的版本」：read 与 write 之间若有第三方改动，这里拒写。
        return self._rewrite(self._resolve(path), new_content, observed["version"])

    # -- 删除：同样先过意图瀑布 --

    def delete(self, path, base_version=None, actor=None):
        target = self._resolve(path)
        if not os.path.exists(target):
            raise FsError("FS_NOT_FOUND", path, "文件不存在")
        if base_version is not None and self._version_of(target) != base_version:
            raise FsError("FS_STALE_VERSION", path, "版本已变化，删除被拒")
        self._intent("fs/delete-intent", target, actor)
        os.remove(target)
        return {"deleted": path}

    # -- 列目录 --

    def list(self, path="."):
        target = self._resolve(path)
        if not os.path.isdir(target):
            raise FsError("FS_NOT_DIR", path, "不是目录")
        return sorted(os.listdir(target))
