"""第 13 章教学重构：lsp 提供者 —— 确定性 mock 语言服务。

源码对应（packages/lsp/lsp-stdio/src/index.ts）：
- LocalLspProvider ↔ index.ts:217（类定义；query :260-303：工作区归一 → 串行
  队列 enqueue :306-317 → 实例池）
- lsp-stdio apply  ↔ index.ts:126-186（按 server 经 ctx.effect 注册 LocalLspProvider）

[教学决策] 真实后端经 stdio JSON-RPC 启动语言服务器进程（initialize 握手、
Content-Length 分帧、$/cancelRequest 竞速）；教学版用固定符号表替代进程，
但保留提供者契约：id + extensions + language_id + query(request)，
request 含 seam 透传的 languageId。
"""

from lsp_runtime import LspError


def _range(start_line, start_char, end_line, end_char):
    """LspRange：零基 UTF-16 位置（types.ts:28-31）。"""
    return {"start": {"line": start_line, "character": start_char},
            "end": {"line": end_line, "character": end_char}}


# 小项目素材：app.py 调用 lib.py 里定义的 helper
DOCUMENTS = {
    "/proj/app.py": [
        "from lib import helper",
        "",
        "def main():",
        "    print(helper())",
    ],
    "/proj/lib.py": [
        "def helper():",
        "    return 'hello'",
    ],
}

# 符号表：词 → 四种操作的结果（位置全部零基）
SYMBOLS = {
    "helper": {
        "definition": {"uri": "/proj/lib.py", "range": _range(0, 4, 0, 10)},
        "references": [{"uri": "/proj/app.py", "range": _range(3, 10, 3, 16)}],
        "implementation": {"uri": "/proj/lib.py", "range": _range(0, 4, 0, 10)},
        "hover": "def helper() -> str\n返回问候语",
    },
    "main": {
        "definition": {"uri": "/proj/app.py", "range": _range(2, 4, 2, 8)},
        "references": [],
        "implementation": {"uri": "/proj/app.py", "range": _range(2, 4, 2, 8)},
        "hover": "def main() -> None\n程序入口",
    },
}


class MockLspProvider:
    """确定性 lsp 提供者：按位置取标识符，再查符号表。

    保留真实提供者契约（types.ts:95-107）：id、extensions、language_id、query。
    """

    def __init__(self, provider_id="mock-python", extensions=(".py",),
                 language_id="python", documents=None, symbols=None):
        self.id = provider_id
        self.extensions = tuple(extensions)
        self.language_id = language_id
        self.documents = DOCUMENTS if documents is None else documents
        self.symbols = SYMBOLS if symbols is None else symbols

    def query(self, request):
        """四种操作返回闭并集（types.ts:85-87）：locations 或 hover。"""
        word = self._word_at(request["filePath"], request["position"])
        info = self.symbols.get(word) if word else None
        if request["operation"] == "hover":
            hover = ({"contents": info["hover"], "range": None} if info
                     else {"contents": None, "range": None})
            return {"kind": "hover", "hover": hover}
        if info is None:
            locations = []
        elif request["operation"] == "goToDefinition":
            locations = [info["definition"]]
        elif request["operation"] == "findReferences":
            locations = list(info["references"])
        elif request["operation"] == "goToImplementation":
            locations = [info["implementation"]]
        else:
            raise LspError("LSP_UNSUPPORTED_OPERATION",
                           f"unsupported operation: {request['operation']}")
        return {"kind": "locations", "locations": locations,
                "resolvedWorkspaceUri": request.get("workspaceUri")}

    def _word_at(self, file_path, position):
        """按位置取标识符（教学版替代「语言服务器解析」）。"""
        lines = self.documents.get(file_path)
        if not lines or position["line"] >= len(lines):
            return None
        line = lines[position["line"]]
        char = position["character"]
        if char >= len(line) or not _is_ident(line[char]):
            return None
        start = char
        while start > 0 and _is_ident(line[start - 1]):
            start -= 1
        end = char
        while end < len(line) and _is_ident(line[end]):
            end += 1
        return line[start:end]


def _is_ident(ch):
    """标识符字符：字母数字或下划线。"""
    return ch.isalnum() or ch == "_"
