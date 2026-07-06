"""Trace writer: whitelist serialisation + JSONL append + read-back.

Security-critical: payloads are built by *whitelist* serialisation. Only the
enumerated business fields are written. Request headers, Authorization, api
keys and `.env` contents are structurally never passed in, so they cannot
leak into trace. This is enforced here (see `_whitelist`), not by post-hoc
scrubbing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config

# Allowed top-level payload keys per event type. Anything not listed is dropped.
_EVENT_FIELDS: dict[str, set[str]] = {
    "run_started": {"user_input"},
    "memory_recalled": {"query", "hits"},
    "context_built": {"message_count", "estimated_tokens", "compacted"},
    "compaction_started": {"covered_until_event_id", "estimated_tokens"},
    "compaction_finished": {"covered_until_event_id", "summary"},
    "llm_request": {"messages", "tools", "model"},
    "llm_response": {"message", "usage", "cache", "finish_reason"},
    "action_parsed": {"kind", "thought_summary", "tool_names"},
    "tool_started": {"tool_name", "arguments"},
    "tool_finished": {"tool_name", "ok", "content", "data"},
    "tool_failed": {"tool_name", "error", "arguments"},
    "run_finished": {"final_answer", "steps"},
    "run_failed": {"error", "steps"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _whitelist(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    allowed = _EVENT_FIELDS.get(event)
    if allowed is None:
        # Unknown event: be conservative, drop everything but a note.
        return {"note": "unknown_event_type"}
    return {k: v for k, v in payload.items() if k in allowed}


class TraceWriter:
    """Appends whitelisted trace records to JSONL and mirrors tool traces to DB."""

    def __init__(self, store=None, jsonl_path: str | Path | None = None,
                 user_id: str = "", session_id: str = "", run_id: str = ""):
        self.store = store
        self.jsonl_path = Path(jsonl_path) if jsonl_path else config.TRACE_JSONL
        self.user_id = user_id
        self.session_id = session_id
        self.run_id = run_id

    def bind(self, *, user_id: str | None = None, session_id: str | None = None,
             run_id: str | None = None) -> "TraceWriter":
        if user_id is not None:
            self.user_id = user_id
        if session_id is not None:
            self.session_id = session_id
        if run_id is not None:
            self.run_id = run_id
        return self

    def write(self, event: str, payload: dict[str, Any] | None = None) -> None:
        record = {
            "timestamp": _now(),
            "run_id": self.run_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "event": event,
            "payload": _whitelist(event, payload or {}),
        }
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # --- convenience for tool traces mirrored into SQLite ------------------
    def record_tool_trace(self, *, tool_name: str, arguments: Any, result: Any,
                          ok: bool, started_at: str, ended_at: str,
                          error: str | None) -> None:
        if self.store is None:
            return
        self.store.execute(
            "INSERT INTO tool_traces(run_id, session_id, tool_name, arguments_json,"
            " result_json, ok, started_at, ended_at, error) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                self.run_id,
                self.session_id,
                tool_name,
                json.dumps(arguments, ensure_ascii=False),
                json.dumps(result, ensure_ascii=False),
                1 if ok else 0,
                started_at,
                ended_at,
                error,
            ),
        )

    # --- read-back for the /trace CLI command ------------------------------
    def read_run(self, run_id: str) -> list[dict[str, Any]]:
        if not self.jsonl_path.exists():
            return []
        out: list[dict[str, Any]] = []
        with open(self.jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("run_id") == run_id:
                    out.append(rec)
        return out
