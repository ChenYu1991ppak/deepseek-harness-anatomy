"""第 13 章教学重构：tool-lsp —— 一个工具承载四种操作。

源码对应（packages/lsp/tool-lsp/src/index.ts + render.ts）：
- apply                       ↔ index.ts:98-229（systemPrompt.section order:112 + defineTool lsp）
- execute                     ↔ index.ts:180-226（无工作区 → LSP_WORKSPACE_REQUIRED :184；ctx.lsp.query :186-191）
- parseLspArgs                ↔ render.ts:46-59（模型侧 1 基 → 协议 0 基，换算在 :51-52）
- LSP_OPERATIONS              ↔ render.ts:15
- formatLocations/formatHover ↔ render.ts:85-111 / 117-121
- renderUri                   ↔ render.ts:138-165

[教学决策] 真实版 inject=['tools','lsp','systemPrompt']；教学版不引入
systemPrompt。模型侧参数用 1 基行列（对模型友好），parse_lsp_args 换算成
协议的 0 基再进 seam —— 该换算保留。
"""

import sys
from pathlib import Path

# 复用第 4 章：ToolDefinition（tools.py:79-91）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ch04" / "code"))

from tools import ToolDefinition  # noqa: E402
from lsp_runtime import LspError  # noqa: E402

LSP_OPERATIONS = ("goToDefinition", "findReferences",
                  "goToImplementation", "hover")  # render.ts:15


class LspTool:
    """lsp 工具：一个工具承载四种操作（index.ts:106-228）。"""

    inject = ["tools", "lsp"]

    def __init__(self, ctx):
        self.ctx = ctx

    def apply(self):
        self.ctx.tools.register(ToolDefinition(
            name="lsp",
            description="代码语义查询：" + " / ".join(LSP_OPERATIONS),
            parameters={
                "operation": "四选一：" + " / ".join(LSP_OPERATIONS),
                "filePath": "目标文件路径",
                "line": "行号（1 基）",
                "character": "列号（1 基）",
                "workspaceUri": "工作区 URI",
            },
            execute=self._execute,
        ))

    def _execute(self, args):
        try:
            request = parse_lsp_args(args)  # render.ts:46-59
        except LspError as err:
            return f"lsp failed: {err.code}: {err}"
        if not request["workspaceUri"]:
            # 真实 execute：无工作区 → LSP_WORKSPACE_REQUIRED（index.ts:184）
            return "lsp failed: LSP_WORKSPACE_REQUIRED: workspaceUri is required"
        try:
            result = self.ctx.lsp.query(request)  # index.ts:186-191
        except LspError as err:
            return f"lsp failed: {err.code}: {err}"
        if result["kind"] == "hover":
            return format_hover(result["hover"])
        return format_locations(result["locations"])


def parse_lsp_args(args):
    """模型侧 1 基行列 → 协议 0 基（render.ts:46-59，换算在 :51-52）。"""
    operation = args.get("operation")
    if operation not in LSP_OPERATIONS:
        raise LspError("LSP_UNSUPPORTED_OPERATION",
                       f"unknown operation: {operation}")
    file_path = args.get("filePath")
    if not file_path:
        raise LspError("LSP_UNAVAILABLE", "filePath is required")
    line = args.get("line")
    character = args.get("character")
    if (not isinstance(line, int) or not isinstance(character, int)
            or line < 1 or character < 1):
        raise LspError("LSP_UNAVAILABLE",
                       "line/character must be 1-based positive integers")
    return {
        "operation": operation,
        "filePath": file_path,
        # 1 基 → 0 基换算（render.ts:51-52）
        "position": {"line": line - 1, "character": character - 1},
        "workspaceUri": args.get("workspaceUri"),
    }


def format_locations(locations):
    """渲染 locations：展示时换回 1 基行:列（render.ts:85-111）。"""
    if not locations:
        return "no locations found"
    lines = []
    for loc in locations:
        start = loc["range"]["start"]
        lines.append(f"{render_uri(loc['uri'])}:{start['line'] + 1}:{start['character'] + 1}")
    return "\n".join(lines)


def format_hover(hover):
    """渲染 hover（render.ts:117-121）。"""
    if not hover or hover.get("contents") is None:
        return "no hover info"
    return hover["contents"]


def render_uri(uri):
    """渲染 uri 供展示（render.ts:138-165）；[教学简化] 原样返回。"""
    return uri
