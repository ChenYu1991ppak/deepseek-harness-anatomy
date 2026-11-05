"""第 6 章教学重构：远端执行世界 —— SSH provider family 的进程内版。

仅标准库，Python 3.10+。运行：python3 main.py

本地三个 provider 之外，还可以把整个执行世界搬到远端：fs、subprocess、
sandbox 各换一个远程实现，消费者一行不改。真实版（packages/ssh/）用一条
OpenSSH 连接 + 远端 helper 进程；教学版把「远端」做成进程内的一个 helper
对象，只保留协议边界（每个动词都是一次 RPC），不模拟 SSH 协议本身。

源码对应（packages/ssh/）：
- SshConnection（一条 -T -M master + 管理 RPC）  ↔ packages/ssh/ssh/src/index.ts:59 / :136
- 程序流独立 channel（-O forward -L）             ↔ packages/ssh/ssh/src/index.ts:152 / :171
- helperHash / launch.kind 校验                   ↔ packages/ssh/ssh/src/index.ts:316/:317
- heartbeat 租约                                  ↔ packages/ssh/ssh/src/index.ts:321
- ptcLaunch                                       ↔ packages/ssh/ssh/src/index.ts:114
- SshFileSystem extends FileSystem                ↔ packages/ssh/fs-ssh/src/index.ts:16
- SshSubprocessRuntime extends SubprocessRuntime  ↔ packages/ssh/subprocess-ssh/src/index.ts:229
- SshSandboxProvider extends SandboxProvider      ↔ packages/ssh/sandbox-ssh/src/index.ts:10
- helper 端装的是本地 provider                     ↔ packages/ssh/ssh/src/helper.ts:28

[教学简化] 真实版的管理通道是 jsonl 帧、程序流走 TLS-PSK；教学版用普通
方法调用 + 一个「已开启」位代替通道，用哈希比较代替 helper 二进制校验。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CH01 = os.path.abspath(os.path.join(_HERE, "..", "..", "ch01", "code"))
for _p in (_HERE, _CH01):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cordis import Service  # noqa: E402
from fs_service import FileSystem  # noqa: E402
from sandbox_service import (  # noqa: E402
    ConfinedArgv,
    SandboxProvider,
    SandboxUnavailableError,
)
from subprocess_service import SubprocessService  # noqa: E402

__all__ = [
    "RemoteHelper",
    "RemoteSubprocess",
    "RemoteFs",
    "RemoteSandbox",
]


class RemoteHelper:
    """远端 helper 的进程内替身：内部装一份本地 provider（packages/ssh/ssh/src/helper.ts:28 的 services()）。

    client 侧只认三个动词 + 一次握手：hello（校验 digest）、request（管理 RPC）、
    stream（每条程序流一个独立通道）。
    """

    def __init__(self, workspace: str, artifact_digest: str):
        # 远端自己的一套 Context 与本地 provider——执行发生在「那边」。
        self.root = os.path.realpath(workspace)
        self._artifact_digest = artifact_digest
        self._fs = _LocalFsView(self.root)
        self._sandbox = _LocalSandboxView()
        self._alive = True
        self.streams = 0  # 统计开了几条数据通道，供演示打印

    def hello(self, protocol: int) -> dict:
        """握手：版本 + helper 自身摘要；client 拿它跟配置的 helperHash 比。"""
        return {"protocol": protocol, "hash": self._artifact_digest, "kind": "executable"}

    def request(self, method: str, params: dict):
        """管理 RPC：一次请求一条应答，失败不重放（真实版 SshRpcPeer 语义）。"""
        if not self._alive:
            raise ConnectionError("SSH helper disconnected; remote outcomes are unknown")
        if method == "fs.write":
            return self._fs.write(params["path"], params["content"])
        if method == "fs.read":
            return self._fs.read(params["path"])
        if method == "exec":
            return self._exec(params["argv"], params.get("cwd"))
        if method == "sandbox":
            return self._sandbox.confine(params["argv"], params["policy"])
        if method == "heartbeat":
            return None
        raise ValueError(f"unknown remote method: {method}")

    def stream(self):
        """每条程序流另开一个独立通道（真实版 -O forward -L 一条 Unix socket）。"""
        self.streams += 1
        return self  # 教学版流与请求共用同一个 helper；真实版是隔离的 channel

    def close(self):
        self._alive = False

    def _exec(self, argv, cwd):
        import subprocess

        proc = subprocess.run(
            argv, cwd=cwd or self.root, capture_output=True, text=True,
        )
        return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


class _LocalFsView:
    """helper 端的文件系统：远端路径，绝不把本地路径当远端路径用。"""

    def __init__(self, root):
        self._root = root

    def _absolute(self, path):
        return os.path.join(self._root, path)

    def write(self, path, content):
        absolute = self._absolute(path)
        parent = os.path.dirname(absolute)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(absolute, "w", encoding="utf-8") as f:
            f.write(content)
        return {"path": path, "bytes": len(content.encode())}

    def read(self, path):
        with open(self._absolute(path), "r", encoding="utf-8") as f:
            return {"content": f.read()}


class _LocalSandboxView:
    """helper 端的 sandbox：在自己的机器上选 runner 并包 argv。"""

    def confine(self, argv, policy):
        # 远端用 platform runner；教学版用 echo 前缀模拟「远端包了一层」。
        return {
            "argv": ["remote-runner", "--mode", policy.mode.value, "--", *argv],
            "enforcement": "full",
        }


class SshConnection(Service):
    """ctx.ssh：连接所有者。握手校验放在构造时，失败即不可用。

    真实版还有 heartbeat 定时器与 leaseMs；教学版只在握手时校验一次，
    并把「连接是否还活着」暴露成 alive，供演示断开后不重放。
    """

    inject = []

    def __init__(self, ctx, helper: RemoteHelper, helper_hash: str, protocol: int = 3):
        super().__init__(ctx, "ssh")
        hello = helper.hello(protocol)
        if hello["hash"] != helper_hash:
            raise RuntimeError("SSH helper digest differs from the configured artifact")
        if hello["kind"] != "executable":
            raise RuntimeError("SSH helper runtime differs from the configured launch kind")
        self._helper = helper
        self._hello = hello

    @property
    def ptcLaunch(self):
        """把远端可执行文件交给 PTC 运行时（第 17 章）：配置是启动时冻结的。"""
        return {"kind": "embedded", "executable": "/remote/bin/dsh-helper"}

    def request(self, method, params):
        return self._helper.request(method, params)

    def stream(self):
        return self._helper.stream()


class RemoteFs(FileSystem):
    """SshFileSystem 的教学版：保留同一组动词与返回形状，版本守卫等细节省略。

    [教学简化] 真实 SshFileSystem 的每个动词都按本地 FileSystem 契约实现（含
    FS_NOT_OBSERVED/FS_STALE_VERSION 等统一错误码，远端 helper 里跑的是
    SandboxedFileSystem）；教学版只演示 read/write/edit 的转发，不实现版本守卫，
    delete/list 直接留空。
    """

    def __init__(self, ctx, ssh):
        # 不调用 super().__init__：FileSystem 会把自己注册成 ctx.fs 的本地版。
        Service.__init__(self, ctx, "fs")
        self._ssh = ssh

    def read(self, path):
        content = self._ssh.request("fs.read", {"path": path})["content"]
        return {"content": content, "version": hashlib.sha256(content.encode()).hexdigest()[:16]}

    def write(self, path, content, base_version=None, actor=None):
        # 远端 provider 同样先过意图瀑布，观察者看的是远端路径。
        self._intent("fs/write-intent", os.path.join(self._ssh._helper.root, path), actor)
        self._ssh.request("fs.write", {"path": path, "content": content})
        return {"version": hashlib.sha256(content.encode()).hexdigest()[:16]}

    def edit(self, path, old, new, base_version=None, actor=None):
        observed = self.read(path)
        if old not in observed["content"]:
            raise ValueError("FS_EDIT_NO_MATCH")
        return self.write(path, observed["content"].replace(old, new, 1), base_version, actor)

    def delete(self, path, base_version=None, actor=None):
        raise NotImplementedError("教学版省略删除")

    def list(self, path="."):
        raise NotImplementedError("教学版省略列目录")


class _RemoteHandle:
    """远端进程句柄：done 来自一次 exec RPC。"""

    def __init__(self, helper, argv, cwd):
        self._helper = helper
        self._argv = argv
        self._cwd = cwd
        self._result = None

    @property
    def done(self):
        if self._result is None:
            self._result = self._helper.request("exec", {"argv": self._argv, "cwd": self._cwd})
            self._result.update({"timed_out": False, "killed": False})
        return self._result

    def read_output(self):
        return self.done["stdout"]

    def kill(self):
        self._result = {"exit_code": -1, "stdout": "", "stderr": "", "timed_out": False, "killed": True}

    def terminate(self):
        self.kill()


class RemoteSubprocess(SubprocessService):
    """SshSubprocessRuntime 的教学版：spawn 变成一次远端请求。"""

    def __init__(self, ctx, ssh):
        Service.__init__(self, ctx, "subprocess")
        self._ssh = ssh

    def spawn(self, argv, workdir=None, env=None, timeout_ms=None):
        return _RemoteHandle(self._ssh._helper, argv, workdir)

    def _dispose_managed(self):
        return lambda: None


class RemoteSandbox(SandboxProvider):
    """SshSandboxProvider 的教学版：confine 的决策在远端。"""

    def __init__(self, ctx, ssh):
        Service.__init__(self, ctx, "sandbox")
        self._ssh = ssh
        self._runner_argv = ["remote-runner"]
        self._approved = {}

    def confine(self, argv, policy):
        try:
            facts = self._ssh.request("sandbox", {"argv": list(argv), "policy": policy})
        except ConnectionError as error:
            raise SandboxUnavailableError(f"remote sandbox unavailable: {error}") from error
        return ConfinedArgv(facts["argv"], policy)
