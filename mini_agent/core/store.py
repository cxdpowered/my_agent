"""SQLite state store (WAL mode) + schema initialisation.

The store owns the single authoritative state DB (`data/agent.sqlite`). It is
deliberately thin: connection management, schema, and a couple of low-level
helpers. Higher-level persistence lives in `sessions.py` / `memory.py`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .. import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  user_id text primary key,
  display_name text,
  created_at text
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id text primary key,
  user_id text not null,
  title text,
  archived integer default 0,
  created_at text,
  updated_at text,
  last_summary_event_id integer
);

CREATE TABLE IF NOT EXISTS session_events (
  event_id integer primary key autoincrement,
  session_id text not null,
  run_id text,
  role text,
  event_type text,
  content text,
  raw_json text,
  created_at text
);

CREATE TABLE IF NOT EXISTS runs (
  run_id text primary key,
  user_id text not null,
  session_id text not null,
  user_input text,
  status text,
  started_at text,
  ended_at text,
  error text
);

CREATE TABLE IF NOT EXISTS tool_traces (
  trace_id integer primary key autoincrement,
  run_id text not null,
  session_id text not null,
  tool_name text,
  arguments_json text,
  result_json text,
  ok integer,
  started_at text,
  ended_at text,
  error text
);

CREATE TABLE IF NOT EXISTS compactions (
  compaction_id integer primary key autoincrement,
  session_id text not null,
  covered_until_event_id integer,
  summary text,
  created_at text
);

CREATE TABLE IF NOT EXISTS memory_index (
  id integer primary key autoincrement,
  user_id text not null,
  path text not null,
  chunk text,
  updated_at text
);

CREATE INDEX IF NOT EXISTS idx_events_session ON session_events(session_id, event_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_memidx_user ON memory_index(user_id);
"""


class StateStore:
    """Thin wrapper around a single SQLite connection in WAL mode."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else config.DB_PATH
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        self._init_pragmas()
        self.init_schema()

    def _init_pragmas(self) -> None:
        cur = self.conn.cursor()
        # WAL only works on file-backed DBs; skip gracefully for :memory:.
        if str(self.db_path) != ":memory:":
            cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA busy_timeout=5000;")
        cur.execute("PRAGMA foreign_keys=ON;")
        self.conn.commit()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- low level helpers -------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, tuple(params))
        self.conn.commit()
        return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(params)).fetchone()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # pragma: no cover
            pass
