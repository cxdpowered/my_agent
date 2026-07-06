"""ContextBuilder: render the request `messages`, estimate tokens, decide compaction.

Rendering order (stable, cache-friendly, design §12.1):
  1. system prompt
  2. 【长期记忆】 memory block
  3. session summary (latest compaction, if any)
  4. recent conversation turns (after covered_until, capped by RECENT_TURNS_TO_KEEP)
  5. current run's messages (user + assistant(tool_calls)+tool_result ...)

Unclosed assistant(tool_calls)/tool_result pairs and their reasoning_content are
never split (avoids DeepSeek 400).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .. import config
from .prompts import SYSTEM_PROMPT


@dataclass
class RenderedContext:
    messages: list[dict]
    estimated_tokens: int
    covered_until_event_id: int
    used_summary: bool


def estimate_tokens(messages: list[dict], calibration: float = 1.0) -> int:
    """Char heuristic: CJK ~1.6 tok/char, ASCII ~0.25 tok/char. Calibrated online."""
    total = 0.0
    for m in messages:
        text = _message_text(m)
        cjk = len(re.findall(r"[一-鿿]", text))
        ascii_like = len(text) - cjk
        total += cjk * 1.6 + max(ascii_like, 0) * 0.25
        total += 4  # per-message overhead
    return int(total * calibration)


def _message_text(m: dict) -> str:
    parts = []
    content = m.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif content is not None:
        parts.append(json.dumps(content, ensure_ascii=False))
    if m.get("reasoning_content"):
        parts.append(str(m["reasoning_content"]))
    for tc in m.get("tool_calls") or []:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        parts.append(str(fn.get("name", "")))
        parts.append(str(fn.get("arguments", "")))
    return "\n".join(parts)


def render_message_for_api(msg: dict) -> dict:
    """Normalise a stored message for the API.

    Keep reasoning_content ONLY on assistant messages that carry tool_calls
    (required by DeepSeek for the tool round). Strip it from completed final
    answers so historical turns stay clean.
    """
    role = msg.get("role")
    if role == "assistant":
        out: dict = {"role": "assistant"}
        if msg.get("tool_calls"):
            out["content"] = msg.get("content") or ""
            if msg.get("reasoning_content"):
                out["reasoning_content"] = msg["reasoning_content"]
            out["tool_calls"] = msg["tool_calls"]
        else:
            out["content"] = msg.get("content") or ""
        return out
    if role == "tool":
        return {
            "role": "tool",
            "tool_call_id": msg.get("tool_call_id", ""),
            "content": msg.get("content", ""),
        }
    # user / system
    return {"role": role, "content": msg.get("content", "")}


class ContextBuilder:
    def __init__(self, session_mgr, store):
        self.sessions = session_mgr
        self.store = store
        self._calibration = 1.0  # online-corrected via usage.prompt_tokens

    def update_calibration(self, estimated: int, actual_prompt_tokens: int) -> None:
        if estimated > 0 and actual_prompt_tokens > 0:
            ratio = actual_prompt_tokens / estimated
            # smooth to avoid wild swings
            self._calibration = 0.5 * self._calibration + 0.5 * ratio

    @property
    def calibration(self) -> float:
        return self._calibration

    def _latest_compaction(self, session_id: str):
        return self.store.query_one(
            "SELECT covered_until_event_id, summary FROM compactions WHERE session_id=?"
            " ORDER BY covered_until_event_id DESC, compaction_id DESC LIMIT 1",
            (session_id,),
        )

    def build(self, *, session_id: str, memory_block: str = "") -> RenderedContext:
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

        if memory_block:
            messages.append({"role": "system", "content": memory_block})

        comp = self._latest_compaction(session_id)
        covered_until = 0
        used_summary = False
        if comp is not None and comp["covered_until_event_id"]:
            covered_until = comp["covered_until_event_id"]
            used_summary = True
            messages.append({
                "role": "system",
                "content": "【会话摘要】\n" + (comp["summary"] or ""),
            })

        events = self.sessions.get_events(session_id, after_event_id=covered_until)
        tail = self._select_recent(events)
        for ev in tail:
            messages.append(render_message_for_api(ev["message"]))

        est = estimate_tokens(messages, self._calibration)
        return RenderedContext(
            messages=messages,
            estimated_tokens=est,
            covered_until_event_id=covered_until,
            used_summary=used_summary,
        )

    def _select_recent(self, events: list[dict]) -> list[dict]:
        """Keep the last RECENT_TURNS_TO_KEEP user-turns worth of events without
        splitting any assistant(tool_calls)/tool pairing."""
        keep = config.RECENT_TURNS_TO_KEEP
        # count user messages from the end; find cutoff index
        user_positions = [i for i, ev in enumerate(events) if ev["role"] == "user"]
        if len(user_positions) <= keep:
            start = 0
        else:
            start = user_positions[-keep]
        # never start on an orphan tool message: back up to its assistant
        while start > 0 and events[start]["role"] == "tool":
            start -= 1
        return events[start:]

    def compaction_candidates(self, session_id: str) -> tuple[list[dict], int]:
        """Events eligible to be summarised: before the recent window and not
        breaking any unclosed tool pairing. Returns (events, covered_until)."""
        comp = self._latest_compaction(session_id)
        covered_until = comp["covered_until_event_id"] if comp and comp["covered_until_event_id"] else 0
        events = self.sessions.get_events(session_id, after_event_id=covered_until)
        recent = self._select_recent(events)
        recent_ids = {ev["event_id"] for ev in recent}
        candidates = [ev for ev in events if ev["event_id"] not in recent_ids]
        # ensure we don't end candidates mid tool-pair: if last candidate is an
        # assistant with tool_calls, keep dropping until closed.
        while candidates and candidates[-1]["role"] == "assistant" and \
                candidates[-1]["message"].get("tool_calls"):
            candidates.pop()
        return candidates, covered_until
