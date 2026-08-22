"""第 6 章教学重构：fs seam —— 文件系统能力。

仅标准库，Python 3.10+。运行：python3 main.py

进程只是执行世界的一半，另一半是文件系统。直接调 os/open 会丢三样东西：
统一错误格式、路径安全、版本守卫。本文件把文件系统做成 ctx.fs seam。

源码对应（packages/fs/）：
- FileSystem                 ↔ abstract FileSystem（fs/src/index.ts:86）
- write/edit                 ↔ writeText/editText（index.ts:222/:243；LocalFileSystem :166/:221）
- FsError（统一错误码）       ↔ FsErrorCode（types.ts:175）；守卫 fs-local/src/index.ts:178-187
- _resolve（realpath 身份）   ↔ resolveLocalTarget（fs-local/src/fsio.ts:146）+ isPathUnder（containment.ts:58）
- 原子写 staging+rename       ↔ writeFileAtomic（fs-local/src/fsio.ts:533）
- 版本守卫                    ↔ versionOf（fsio.ts:74）+ FsVersion（types.ts:35）+ FS_STALE_VERSION

[教学简化] 真实版还有逐 key FIFO 锁 withLock、glob/grep、SandboxedFileSystem
的 canonicalize-then-contain；教学版只保留 read/write/edit/list 与版本守卫主干。
"""
from __future__ import annotations

import hashlib
import os
import sys

_CH01 = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ch01", "code"))
if _CH01 not in sys.path:
    sys.path.insert(0, _CH01)

from cordis import Service  # noqa: E402

__all__ = ["FileSystem", "FsError"]


class FsError(Exception):
    """统一错误格式：一个错误码 + 路径 + 人话消息（types.ts:175 FsErrorCode）。

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

    # -- 路径安全：realpath 身份 + workspace 包含（fsio.ts:146 resolveLocalTarget + containment.ts:58 isPathUnder） --

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

    def write(self, path, content, base_version=None):
        target = self._resolve(path)
        if os.path.isdir(target):
            raise FsError("FS_IS_DIR", path, "目标是目录")
        if os.path.exists(target):
            # 已存在的文件必须先 read 观察再写，杜绝盲覆盖。
            if base_version is None:
                raise FsError("FS_NOT_OBSERVED", path, "写入前必须先 read 观察")
            if self._version_of(target) != base_version:
                raise FsError("FS_STALE_VERSION", path, "版本已变化，写入被拒")
        # 原子写：先写临时 staging，再 rename 落位，避免半截文件。
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

    def edit(self, path, old, new, base_version=None):
        observed = self.read(path)
        if base_version is not None and observed["version"] != base_version:
            raise FsError("FS_STALE_VERSION", path, "版本已变化，编辑被拒")
        if old not in observed["content"]:
            raise FsError("FS_EDIT_NO_MATCH", path, "old 片段未找到")
        new_content = observed["content"].replace(old, new, 1)
        return self.write(path, new_content, base_version=observed["version"])

    # -- 列目录 --

    def list(self, path="."):
        target = self._resolve(path)
        if not os.path.isdir(target):
            raise FsError("FS_NOT_DIR", path, "不是目录")
        return sorted(os.listdir(target))
