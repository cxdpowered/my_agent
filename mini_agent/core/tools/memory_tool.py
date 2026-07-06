"""memory: LLM-facing tool over the user-scoped MemoryStore.

Operations: search / list / read / write / append. Writes update the keyword
index. Paths are confined to the user's memory directory by MemoryStore.
"""
from __future__ import annotations

from ..memory import MemoryPathError
from .base import ToolContext, ToolResult, ToolSpec

_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["search", "list", "read", "write", "append"],
            "description": "memory 操作",
        },
        "query": {"type": "string", "description": "search 时的查询"},
        "path": {"type": "string", "description": "memory 文件相对路径，例如 preferences.md"},
        "content": {"type": "string", "description": "write/append 写入的内容"},
    },
    "required": ["operation"],
}


def _run(arguments: dict, ctx: ToolContext) -> ToolResult:
    mem = ctx.memory
    operation = arguments["operation"]

    try:
        if operation == "list":
            files = mem.list_files()
            return ToolResult(ok=True, content="memory 文件: " + (", ".join(files) or "(空)"),
                              data={"files": files})

        if operation == "search":
            query = arguments.get("query")
            if not query:
                return ToolResult(ok=False, content="", error="search 需要 query 参数")
            hits = mem.search(query)
            if not hits:
                return ToolResult(ok=True, content=f"没有找到与“{query}”相关的记忆。", data={"hits": []})
            lines = [f"与“{query}”相关的记忆:"]
            for h in hits:
                lines.append(f"- ({h['path']}) {h['chunk'][:200]}")
            return ToolResult(ok=True, content="\n".join(lines), data={"hits": hits})

        if operation == "read":
            path = arguments.get("path")
            if not path:
                return ToolResult(ok=False, content="", error="read 需要 path 参数")
            content = mem.read(path)
            return ToolResult(ok=True, content=f"({path})\n{content}", data={"path": path, "content": content})

        if operation == "write":
            path = arguments.get("path")
            content = arguments.get("content")
            if not path or content is None:
                return ToolResult(ok=False, content="", error="write 需要 path 与 content 参数")
            mem.write(path, content)
            return ToolResult(ok=True, content=f"已写入记忆 {path}。", data={"path": path})

        if operation == "append":
            path = arguments.get("path")
            content = arguments.get("content")
            if not path or content is None:
                return ToolResult(ok=False, content="", error="append 需要 path 与 content 参数")
            mem.append(path, content)
            return ToolResult(ok=True, content=f"已追加到记忆 {path}。", data={"path": path})

        return ToolResult(ok=False, content="", error=f"未知 operation: {operation}")

    except MemoryPathError as e:
        return ToolResult(ok=False, content="", error=str(e))
    except FileNotFoundError as e:
        return ToolResult(ok=False, content="", error=f"memory 文件不存在: {e}")
    except OSError as e:
        return ToolResult(ok=False, content="", error=f"memory 读写失败: {e}")


def memory_tool() -> ToolSpec:
    return ToolSpec(
        name="memory",
        description=(
            "跨 session 的长期记忆工具（user-scoped）。可 search/list/read/write/append。"
            "写入长期偏好、身份/项目背景、常用城市等；不要写入临时闲聊或密钥等敏感信息。"
        ),
        parameters_schema=_SCHEMA,
        handler=_run,
    )
