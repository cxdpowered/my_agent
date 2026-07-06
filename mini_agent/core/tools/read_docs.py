"""read_docs: read a local file the user points at.

This is the ONLY unrestricted tool (by explicit user decision): it can read any
file the process has permission to access. See README security section.
"""
from __future__ import annotations

from pathlib import Path

from .base import ToolContext, ToolResult, ToolSpec

_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "要读取的本地文件路径，可为绝对或相对路径",
        },
        "start_line": {
            "type": "integer",
            "description": "从第几行开始读取，默认 1",
            "default": 1,
        },
        "max_chars": {
            "type": "integer",
            "description": "最多返回字符数，默认 20000",
            "default": 20000,
        },
    },
    "required": ["path"],
}


def _decode(raw: bytes) -> tuple[str | None, str | None]:
    """Return (text, encoding) or (None, None) if it looks binary."""
    if b"\x00" in raw:
        return None, None
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    try:  # optional chardet fallback
        import chardet

        guess = chardet.detect(raw)
        enc = guess.get("encoding")
        if enc:
            return raw.decode(enc, errors="replace"), enc
    except Exception:
        pass
    return None, None


def _run(arguments: dict, ctx: ToolContext) -> ToolResult:
    path = Path(str(arguments["path"]))
    start_line = int(arguments.get("start_line", 1) or 1)
    max_chars = int(arguments.get("max_chars", 20000) or 20000)
    if start_line < 1:
        start_line = 1

    if not path.exists():
        return ToolResult(ok=False, content="", error=f"文件不存在: {path}")
    if path.is_dir():
        return ToolResult(ok=False, content="", error=f"路径是目录，不是文件: {path}")

    try:
        raw = path.read_bytes()
    except OSError as e:
        return ToolResult(ok=False, content="", error=f"读取失败: {e}")

    text, enc = _decode(raw)
    if text is None:
        return ToolResult(ok=False, content="", error="疑似二进制文件或无法解码，已拒绝读取")

    all_lines = text.splitlines(keepends=True)
    total_lines = len(all_lines)
    selected = all_lines[start_line - 1:]
    body = "".join(selected)

    truncated = False
    next_start = None
    if len(body) > max_chars:
        # figure out how many lines fit within max_chars
        acc = 0
        consumed_lines = 0
        for ln in selected:
            if acc + len(ln) > max_chars:
                break
            acc += len(ln)
            consumed_lines += 1
        if consumed_lines == 0:  # a single huge line: hard cut
            body = body[:max_chars]
            consumed_lines = 1
        else:
            body = "".join(selected[:consumed_lines])
        truncated = True
        next_start = start_line + consumed_lines

    summary = (
        f"读取 {path.name}（编码 {enc}，第 {start_line} 行起，"
        f"共 {total_lines} 行，返回 {len(body)} 字符"
        f"{'，已截断' if truncated else ''}）:\n\n{body}"
    )
    data = {
        "path": str(path),
        "encoding": enc,
        "start_line": start_line,
        "total_lines": total_lines,
        "chars_returned": len(body),
        "truncated": truncated,
        "next_start_line": next_start,
    }
    return ToolResult(ok=True, content=summary, data=data)


def read_docs_tool() -> ToolSpec:
    return ToolSpec(
        name="read_docs",
        description="读取本地文件内容（不限制路径），支持 start_line 与 max_chars 分块。",
        parameters_schema=_SCHEMA,
        handler=_run,
    )
