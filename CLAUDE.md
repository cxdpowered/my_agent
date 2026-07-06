# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A from-scratch minimal Agent Runtime (no LangGraph/LangChain/OpenHands). Core runtime + interactive CLI. Real DeepSeek thinking-mode LLM, real Tavily/AMap tools. The authoritative spec is `minimal-agent-requirements-design-final.md` (Chinese) — decisions there override intuition; §0 is a decision cheat-sheet.

## Commands

```bash
pip install -r requirements.txt          # includes socksio (needed if ALL_PROXY=socks5://...)

pytest                                    # mock suite (default; excludes live via pytest.ini addopts)
pytest -m live                            # real DeepSeek/Tavily/AMap smoke tests (needs keys)
pytest mini_agent/tests/unit/test_calculator.py::test_basic   # single test

python -m mini_agent --user za            # run CLI (default = new session)
python -m mini_agent --user za -c         # continue latest session; -r [id] resume; --session <id>
```

Keys load from `.env` (git-ignored, already populated). `pytest.ini` sets `addopts = -m "not live"`, so live tests never run unless you pass `-m live` explicitly.

## Architecture — the parts that span files

**Layering:** `core/` never imports `cli/`. The CLI drives the runtime via `Runtime.run(...)` and receives structured `TurnEvent`s through an `on_event` callback — rendering is fully decoupled from loop logic. Tests drive `Runtime` directly with a scripted `FakeLLM` (see `tests/conftest.py`), no CLI involved.

**The loop** (`core/runtime.py`): `MAX_AGENT_STEPS=8` counts *LLM calls*, not tool executions. On the 8th call, if the model still returns tool_calls, tools are **not** executed — it wraps up with `config.MAX_LOOP_MESSAGE`. This is why `test_max_loop_stops` asserts exactly 7 tool executions.

**Message persistence & replay is the spine** (`core/sessions.py`): `session_events.raw_json` stores the *complete* OpenAI message object. Replaying events by `event_id ASC` yields a valid `messages` array with no pairing logic — a tool_calls assistant is naturally followed by its matching tool messages. The `content` column is a redundant display copy, **not** used for replay.

**reasoning_content is the sharpest constraint** (DeepSeek returns 400 otherwise): an assistant message carrying `tool_calls` MUST retain its `reasoning_content` on the next request. `context.py::render_message_for_api` keeps `reasoning_content` only on tool_calls assistants and strips it from completed final answers. It is **never** shown to users — the CLI only shows `thought_summary`, generated deterministically from the tool_calls template in `parser.py` (never derived from the CoT). Compaction must never split an unclosed assistant(tool_calls)/tool pair.

**Context building** (`core/context.py`): stable render order (system → 【长期记忆】 → 会话摘要 → recent turns → current run) for cache friendliness. Token estimate = char heuristic calibrated online by the ratio of previous `usage.prompt_tokens` to the local estimate. Compaction is checked *before every LLM call*; when over `CONTEXT_COMPACT_THRESHOLD_TOKENS`, it summarizes only events outside the `RECENT_TURNS_TO_KEEP` window that don't break tool pairs, writes a `compactions` row, and mirrors `covered_until_event_id` into `sessions.last_summary_event_id`. Original events are never deleted.

**Memory vs read_docs — deliberate asymmetry** (`core/memory.py`, `tools/read_docs.py`): memory is user-scoped and path-confined to `data/memories/{user_id}/` via realpath prefix check (rejects absolute paths and `..`). read_docs is the ONE unrestricted tool by explicit design decision. Don't "harden" read_docs or loosen memory. Memory recall runs at run start (`query = user_input + latest summary`), keyword-scored, injected as a fixed system block.

**Trace security** (`core/trace.py`): whitelist serialization — `_EVENT_FIELDS` enumerates allowed payload keys per event type; anything else is dropped at write time. Credentials are structurally never passed into these fields, so they cannot leak (not post-hoc scrubbing). Adding a trace event type requires adding its allowed fields to `_EVENT_FIELDS`.

**Tools** (`core/tools/`): `ToolRegistry` enforces unique names and emits `to_openai_tools()` in stable registration order. Args are validated locally against JSON Schema (`jsonschema`, not provider strict mode); validation failure returns `ok=false` and is fed back to the LLM as a tool result to self-correct. Handlers return `ToolResult(ok, content, data, error)` — `content` is the compact summary to the LLM, `data` is the full payload for trace.

**Model note:** design doc says `deepseek-v4-pro`; the publicly available thinking model is `deepseek-reasoner` (set in `.env`, overridable via `DEEPSEEK_MODEL`). Code default follows the doc.

## Testing conventions

`FakeLLM` (in `conftest.py`) scripts assistant messages: `FakeLLM.final(content, reasoning=...)` and `FakeLLM.tool([(id, name, args)], reasoning=...)`. The last script repeats if the loop asks for more (used by the max-loop test). Fixtures use in-memory SQLite (`StateStore(":memory:")`) and `tmp_path` for memory/trace, so tests are isolated.
