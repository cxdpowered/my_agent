"""Compaction: call the LLM to summarise old events, parse the JSON summary.

On any failure (LLM error or unparsable JSON) we fall back gracefully: the
runtime keeps using raw events. A parse failure that still returns text keeps
the raw text as the summary (design §12.3).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .context import render_message_for_api
from .prompts import COMPACTION_PROMPT


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _events_to_text(events: list[dict]) -> str:
    lines = []
    for ev in events:
        msg = render_message_for_api(ev["message"])
        role = msg.get("role")
        content = msg.get("content") or ""
        if msg.get("tool_calls"):
            names = ", ".join(
                tc.get("function", {}).get("name", "") for tc in msg["tool_calls"]
            )
            lines.append(f"[{role} -> tools: {names}] {content}")
        else:
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    # strip accidental code fences
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # try to locate the first {...} block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def summarize_events(llm, events: list[dict], trace=None) -> str:
    """Return a summary string (structured JSON text, or raw text fallback)."""
    history_text = _events_to_text(events)
    messages = [
        {"role": "system", "content": "你是对话压缩器，只输出压缩摘要。"},
        {"role": "user", "content": COMPACTION_PROMPT + history_text},
    ]
    resp = llm.complete(messages=messages, tools=None)
    raw_text = resp.message.get("content") or ""
    parsed = _extract_json(raw_text)
    if parsed is not None:
        return json.dumps(parsed, ensure_ascii=False)
    if trace is not None:
        trace.write("compaction_finished", {
            "covered_until_event_id": None,
            "summary": "(JSON 解析失败，回退为原文本)",
        })
    return raw_text.strip()


def run_compaction(*, llm, store, sessions, candidates: list[dict], session_id: str,
                   trace=None) -> int | None:
    """Summarise `candidates`, persist a compactions row, update session mirror.

    Returns covered_until_event_id, or None if nothing was compacted.
    """
    if not candidates:
        return None
    covered_until = candidates[-1]["event_id"]
    if trace is not None:
        trace.write("compaction_started", {"covered_until_event_id": covered_until})
    try:
        summary = summarize_events(llm, candidates, trace=trace)
    except Exception as e:  # noqa: BLE001 - compaction must never break a run
        if trace is not None:
            trace.write("compaction_finished", {
                "covered_until_event_id": None,
                "summary": f"(压缩失败，跳过: {e})",
            })
        return None

    store.execute(
        "INSERT INTO compactions(session_id, covered_until_event_id, summary, created_at)"
        " VALUES(?,?,?,?)",
        (session_id, covered_until, summary, _now()),
    )
    sessions.set_last_summary_event(session_id, covered_until)
    if trace is not None:
        trace.write("compaction_finished", {
            "covered_until_event_id": covered_until,
            "summary": summary[:500],
        })
    return covered_until
