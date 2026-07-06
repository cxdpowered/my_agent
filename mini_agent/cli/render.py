"""Terminal rendering. Uses rich when available, degrades to plain print."""
from __future__ import annotations

import json
from typing import Any

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.table import Table

    _console = Console()
    _HAS_RICH = True
except Exception:  # pragma: no cover
    _console = None
    _HAS_RICH = False


def _p(text: str = "") -> None:
    if _HAS_RICH:
        _console.print(text)
    else:
        print(text)


def info(text: str) -> None:
    if _HAS_RICH:
        _console.print(f"[cyan]{text}[/cyan]")
    else:
        print(text)


def warn(text: str) -> None:
    if _HAS_RICH:
        _console.print(f"[yellow]{text}[/yellow]")
    else:
        print(text)


def error(text: str) -> None:
    if _HAS_RICH:
        _console.print(f"[red]{text}[/red]")
    else:
        print("ERROR:", text)


def thought(text: str) -> None:
    if _HAS_RICH:
        _console.print(f"[dim]💭 {text}[/dim]")
    else:
        print(f"[thinking] {text}")


def tool_call(name: str, arguments: dict) -> None:
    args = json.dumps(arguments, ensure_ascii=False)
    if len(args) > 120:
        args = args[:120] + "…"
    if _HAS_RICH:
        _console.print(f"[magenta]🔧 {name}[/magenta] [dim]{args}[/dim]")
    else:
        print(f"[tool] {name} {args}")


def tool_result(name: str, ok: bool, content: str) -> None:
    head = content.splitlines()[0] if content else ""
    if len(head) > 200:
        head = head[:200] + "…"
    mark = "✅" if ok else "❌"
    if _HAS_RICH:
        color = "green" if ok else "red"
        _console.print(f"[{color}]{mark} {name}[/{color}] [dim]{head}[/dim]")
    else:
        print(f"[result] {mark} {name}: {head}")


def final_answer(text: str) -> None:
    text = text or ""
    if _HAS_RICH:
        # Render the model's markdown (bold, headings, lists, code) as real
        # terminal formatting instead of showing raw ** / # characters.
        _console.print(Panel(Markdown(text), title="Agent", border_style="green"))
    else:
        print("\nAgent:", strip_markdown(text), "\n")


def strip_markdown(text: str) -> str:
    """Fallback for plain terminals: reduce common markdown to readable text."""
    import re

    out = text
    out = re.sub(r"```[a-zA-Z0-9]*\n?", "", out)   # code fences
    out = re.sub(r"`([^`]*)`", r"\1", out)          # inline code
    out = re.sub(r"\*\*([^*]+)\*\*", r"\1", out)    # bold
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", out)  # italic
    out = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", out)  # headings
    out = re.sub(r"(?m)^\s*[-*+]\s+", "• ", out)     # bullet markers
    return out


def banner(user_id: str, session_id: str, title: str) -> None:
    line = f"user={user_id}  session={session_id}  ({title})"
    if _HAS_RICH:
        _console.print(Panel(line, border_style="blue"))
    else:
        print("=" * 60)
        print(line)
        print("=" * 60)


def session_table(rows: list[Any]) -> None:
    if _HAS_RICH:
        table = Table(title="Sessions")
        table.add_column("session_id")
        table.add_column("title")
        table.add_column("archived")
        table.add_column("updated_at")
        for r in rows:
            table.add_row(r["session_id"], r["title"] or "",
                          "yes" if r["archived"] else "no", r["updated_at"] or "")
        _console.print(table)
    else:
        for r in rows:
            print(f"{r['session_id']}  {r['title']}  archived={r['archived']}  {r['updated_at']}")


def trace_dump(records: list[dict], as_json: bool = False) -> None:
    if as_json:
        _p(json.dumps(records, ensure_ascii=False, indent=2))
        return
    for rec in records:
        event = rec.get("event")
        payload = rec.get("payload", {})
        ts = rec.get("timestamp", "")
        if _HAS_RICH:
            _console.print(f"[dim]{ts}[/dim] [bold]{event}[/bold]")
        else:
            print(f"{ts} {event}")
        summary = _summarize_payload(event, payload)
        if summary:
            _p("    " + summary)


def _summarize_payload(event: str, payload: dict) -> str:
    if event == "run_started":
        return f"input: {payload.get('user_input','')[:200]}"
    if event == "llm_response":
        usage = payload.get("usage", {})
        msg = payload.get("message", {})
        tc = msg.get("tool_calls")
        piece = f"tool_calls={[t.get('function',{}).get('name') for t in tc]}" if tc else \
                f"content={(msg.get('content') or '')[:120]}"
        return f"{piece}  usage={usage}"
    if event in ("tool_started", "tool_finished", "tool_failed"):
        return json.dumps(payload, ensure_ascii=False)[:400]
    if event == "run_finished":
        return f"final: {(payload.get('final_answer') or '')[:200]}  steps={payload.get('steps')}"
    if event == "run_failed":
        return f"error: {payload.get('error')}"
    if event == "memory_recalled":
        return f"hits: {payload.get('hits')}"
    if event == "context_built":
        return json.dumps(payload, ensure_ascii=False)
    return ""
