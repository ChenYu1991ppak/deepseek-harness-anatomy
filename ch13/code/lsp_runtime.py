"""第 13 章教学重构：lsp 能力 seam —— 扩展名路由 + 原子注册。

源码对应（packages/lsp/lsp/src/index.ts）：
- Lsp                ↔ index.ts:82（extends Service，注册为 ctx.lsp）
- routes             ↔ index.ts:84（扩展名 → 路由表）
- finalExtension     ↔ index.ts:60-67
- registerProvider   ↔ index.ts:90-141（原子注册：先全量校验，再一次性写表）
- query              ↔ index.ts:143-149（按扩展名路由，透传 languageId）
- normalizeExtension ↔ index.ts:153-156
- LspError           ↔ index.ts:50

[教学决策] 真实后端是 lsp-stdio（stdio JSON-RPC 启动语言服务器进程）；教学版
用确定性 mock 提供者，进程/协议细节只出现在正文 §7 源码对应。
"""

import sys
from pathlib import Path

# 复用第 1 章：Service 构造即注册（cordis.py:271-284）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ch01" / "code"))

from cordis import Service  # noqa: E402


class LspError(Exception):
    """lsp seam 的统一错误，带机器可读 code（index.ts:50）。

    真实错误码含 LSP_INVALID_PROVIDER / LSP_CONFLICT / LSP_UNAVAILABLE /
    LSP_DISPOSED / LSP_UNSUPPORTED_OPERATION / LSP_MALFORMED_RESPONSE。
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Lsp(Service):
    """ctx.lsp 服务：扩展名 → 路由表 + 原子注册（index.ts:82）。

    seam 只管「哪个扩展名归哪个提供者」：
    - register_provider：先全量校验，再一次性写表，失败不留痕
    - query：按文件扩展名路由，透传 languageId 给提供者
    """

    def __init__(self, ctx):
        super().__init__(ctx, "lsp")
        self.routes = {}  # 扩展名 -> (provider, language_id)（index.ts:84）
        self.provider_ids = set()

    def register_provider(self, provider):
        """原子注册：先全量校验，再一次性写表（index.ts:90-141）。

        三项校验：空 id → LSP_INVALID_PROVIDER；重复 id 或扩展名冲突 →
        LSP_CONFLICT。全部通过后才用一个 ctx.effect 写入 id + 全部扩展名
        （index.ts:129-138），因此不存在「注册了一半」的中间态。
        """
        if not provider.id:
            raise LspError("LSP_INVALID_PROVIDER", "provider id must be non-empty")
        if provider.id in self.provider_ids:
            raise LspError("LSP_CONFLICT", f"duplicate provider id: {provider.id}")
        for ext in provider.extensions:
            ext = normalize_extension(ext)
            owner = self.routes.get(ext)
            if owner is not None:
                raise LspError("LSP_CONFLICT",
                               f"extension {ext} already owned by provider {owner[0].id}")

        def setup():
            self.provider_ids.add(provider.id)
            for ext in provider.extensions:
                self.routes[normalize_extension(ext)] = (provider, provider.language_id)

            def dispose():
                self.provider_ids.discard(provider.id)
                owned = [e for e, route in self.routes.items() if route[0] is provider]
                for ext in owned:
                    del self.routes[ext]

            return dispose

        return self.ctx.effect(setup, label=f"lsp-provider:{provider.id}")

    def query(self, request):
        """查询入口：按文件扩展名路由，未命中 → LSP_UNAVAILABLE（index.ts:143-149）。"""
        ext = final_extension(request["filePath"])
        route = self.routes.get(ext)
        if route is None:
            raise LspError("LSP_UNAVAILABLE",
                           f"no lsp provider for extension: {ext or '<none>'}")
        provider, language_id = route
        # seam 透传 languageId：提供者不必自己从扩展名猜语言
        return provider.query({**request, "languageId": language_id})

    def list_providers(self):
        """谁在场：返回已注册提供者 id 列表。"""
        return sorted(self.provider_ids)


def final_extension(file_path):
    """取最后一个路径分隔符之后的扩展名（index.ts:60-67）。

    'a/b.py' → '.py'；'a.d/b' → ''（点在分隔符前，不算）；
    '.hidden' → ''（点开头是隐藏文件，不是扩展名）。
    """
    name = file_path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    if dot <= 0:
        return ""
    return normalize_extension(name[dot:])


def normalize_extension(ext):
    """扩展名统一小写（index.ts:153-156）。"""
    return ext.lower()
