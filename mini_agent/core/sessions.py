"""User / Session / Event persistence and message reconstruction.

`session_events.raw_json` stores the *complete* OpenAI message object. Replaying
events by event_id ascending yields a valid `messages` array directly — a
tool_calls assistant is naturally followed by its matching tool messages, so no
extra pairing logic is needed (design §11.4).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .store import StateStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class SessionManager:
    def __init__(self, store: StateStore):
        self.store = store

    # --- users -------------------------------------------------------------
    def ensure_user(self, user_id: str, display_name: str | None = None) -> None:
        row = self.store.query_one("SELECT user_id FROM users WHERE user_id=?", (user_id,))
        if row is None:
            self.store.execute(
                "INSERT INTO users(user_id, display_name, created_at) VALUES(?,?,?)",
                (user_id, display_name or user_id, _now()),
            )

    # --- sessions ----------------------------------------------------------
    def create_session(self, user_id: str, title: str | None = None) -> str:
        self.ensure_user(user_id)
        session_id = new_id("sess")
        now = _now()
        self.store.execute(
            "INSERT INTO sessions(session_id, user_id, title, archived, created_at, updated_at,"
            " last_summary_event_id) VALUES(?,?,?,?,?,?,?)",
            (session_id, user_id, title or "untitled", 0, now, now, None),
        )
        return session_id

    def get_session(self, session_id: str):
        return self.store.query_one("SELECT * FROM sessions WHERE session_id=?", (session_id,))

    def list_sessions(self, user_id: str, include_archived: bool = False) -> list:
        if include_archived:
            return self.store.query(
                "SELECT * FROM sessions WHERE user_id=? ORDER BY updated_at DESC", (user_id,)
            )
        return self.store.query(
            "SELECT * FROM sessions WHERE user_id=? AND archived=0 ORDER BY updated_at DESC",
            (user_id,),
        )

    def latest_session(self, user_id: str):
        return self.store.query_one(
            "SELECT * FROM sessions WHERE user_id=? AND archived=0 ORDER BY updated_at DESC LIMIT 1",
            (user_id,),
        )

    def rename_session(self, session_id: str, title: str) -> None:
        self.store.execute(
            "UPDATE sessions SET title=?, updated_at=? WHERE session_id=?",
            (title, _now(), session_id),
        )

    def archive_session(self, session_id: str) -> None:
        self.store.execute(
            "UPDATE sessions SET archived=1, updated_at=? WHERE session_id=?",
            (_now(), session_id),
        )

    def touch_session(self, session_id: str) -> None:
        self.store.execute(
            "UPDATE sessions SET updated_at=? WHERE session_id=?", (_now(), session_id)
        )

    def set_last_summary_event(self, session_id: str, event_id: int) -> None:
        self.store.execute(
            "UPDATE sessions SET last_summary_event_id=?, updated_at=? WHERE session_id=?",
            (event_id, _now(), session_id),
        )

    # --- events ------------------------------------------------------------
    def append_event(self, *, session_id: str, run_id: str | None, role: str,
                     event_type: str, message: dict) -> int:
        """Persist one message. `message` is the complete OpenAI message object."""
        content = message.get("content")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False) if content is not None else ""
        cur = self.store.execute(
            "INSERT INTO session_events(session_id, run_id, role, event_type, content, raw_json,"
            " created_at) VALUES(?,?,?,?,?,?,?)",
            (
                session_id,
                run_id,
                role,
                event_type,
                content,
                json.dumps(message, ensure_ascii=False),
                _now(),
            ),
        )
        self.touch_session(session_id)
        return cur.lastrowid

    def get_events(self, session_id: str, after_event_id: int = 0) -> list[dict]:
        """Return events (with parsed raw_json message) ordered by event_id asc."""
        rows = self.store.query(
            "SELECT event_id, run_id, role, event_type, raw_json FROM session_events"
            " WHERE session_id=? AND event_id>? ORDER BY event_id ASC",
            (session_id, after_event_id),
        )
        out = []
        for r in rows:
            try:
                msg = json.loads(r["raw_json"])
            except (json.JSONDecodeError, TypeError):
                msg = {"role": r["role"], "content": ""}
            out.append({
                "event_id": r["event_id"],
                "run_id": r["run_id"],
                "role": r["role"],
                "event_type": r["event_type"],
                "message": msg,
            })
        return out

    # --- runs --------------------------------------------------------------
    def start_run(self, *, user_id: str, session_id: str, user_input: str) -> str:
        run_id = new_id("run")
        self.store.execute(
            "INSERT INTO runs(run_id, user_id, session_id, user_input, status, started_at)"
            " VALUES(?,?,?,?,?,?)",
            (run_id, user_id, session_id, user_input, "running", _now()),
        )
        return run_id

    def finish_run(self, run_id: str, status: str, error: str | None = None) -> None:
        self.store.execute(
            "UPDATE runs SET status=?, ended_at=?, error=? WHERE run_id=?",
            (status, _now(), error, run_id),
        )

    def latest_run(self, session_id: str):
        return self.store.query_one(
            "SELECT * FROM runs WHERE session_id=? ORDER BY started_at DESC LIMIT 1",
            (session_id,),
        )
