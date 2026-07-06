"""Agent runtime: the self-implemented loop (LLM -> parse -> tool -> loop -> final).

See design §6.1 for the state machine. This module owns no I/O rendering; it
emits structured turn events via an optional callback so the CLI can render
incrementally while the loop stays surface-agnostic.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .. import config
from . import compaction as compaction_mod
from .context import ContextBuilder, estimate_tokens
from .memory import MemoryStore
from .parser import AgentAction, ToolCall, parse_message
from .sessions import SessionManager
from .tools.base import ToolContext, ToolRegistry, ToolResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TurnEvent:
    kind: str                 # thought / tool_call / tool_result / final / notice / error
    payload: dict = field(default_factory=dict)


@dataclass
class TurnResult:
    run_id: str
    status: str               # finished / partial / failed
    final_answer: str | None
    steps: int
    events: list[TurnEvent] = field(default_factory=list)
    error: str | None = None


EventCallback = Callable[[TurnEvent], None]


class Runtime:
    def __init__(self, *, store, llm, registry: ToolRegistry, trace, env: dict,
                 memories_root=None):
        self.store = store
        self.llm = llm
        self.registry = registry
        self.trace = trace
        self.env = env
        self.memories_root = memories_root
        self.sessions = SessionManager(store)
        self.context = ContextBuilder(self.sessions, store)
        self._memory_cache: dict[str, MemoryStore] = {}

    def _memory(self, user_id: str) -> MemoryStore:
        if user_id not in self._memory_cache:
            self._memory_cache[user_id] = MemoryStore(user_id, self.store, self.memories_root)
        return self._memory_cache[user_id]

    def _latest_summary_text(self, session_id: str) -> str:
        row = self.store.query_one(
            "SELECT summary FROM compactions WHERE session_id=?"
            " ORDER BY covered_until_event_id DESC, compaction_id DESC LIMIT 1",
            (session_id,),
        )
        return (row["summary"] if row else "") or ""

    # -- main entry ---------------------------------------------------------
    def run(self, *, user_id: str, session_id: str, user_input: str,
            on_event: EventCallback | None = None) -> TurnResult:
        events: list[TurnEvent] = []

        def emit(ev: TurnEvent) -> None:
            events.append(ev)
            if on_event:
                on_event(ev)

        run_id = self.sessions.start_run(
            user_id=user_id, session_id=session_id, user_input=user_input
        )
        self.trace.bind(user_id=user_id, session_id=session_id, run_id=run_id)
        self.trace.write("run_started", {"user_input": user_input})

        # persist user message
        self.sessions.append_event(
            session_id=session_id, run_id=run_id, role="user",
            event_type="user_message", message={"role": "user", "content": user_input},
        )

        # auto memory recall
        memory = self._memory(user_id)
        recall_query = user_input + "\n" + self._latest_summary_text(session_id)
        memory_block, hits = memory.recall_block(recall_query)
        self.trace.write("memory_recalled", {
            "query": user_input,
            "hits": [{"path": h["path"], "score": h["score"]} for h in hits],
        })
        if hits:
            emit(TurnEvent("notice", {"text": f"召回 {len(hits)} 条长期记忆"}))

        step = 0
        invalid_retried = False
        try:
            while True:
                # maybe compact BEFORE the LLM call
                self._maybe_compact(session_id, memory_block)

                rendered = self.context.build(session_id=session_id, memory_block=memory_block)
                self.trace.write("context_built", {
                    "message_count": len(rendered.messages),
                    "estimated_tokens": rendered.estimated_tokens,
                    "compacted": rendered.used_summary,
                })

                tools = self.registry.to_openai_tools()
                self.trace.write("llm_request", {
                    "messages": rendered.messages,
                    "tools": tools,
                    "model": getattr(self.llm, "model", None),
                })

                step += 1
                resp = self.llm.complete(messages=rendered.messages, tools=tools)
                self.trace.write("llm_response", {
                    "message": resp.message,
                    "usage": resp.usage,
                    "cache": resp.cache,
                    "finish_reason": resp.finish_reason,
                })
                # online calibration of token estimator
                prompt_tokens = resp.usage.get("prompt_tokens") if resp.usage else None
                if prompt_tokens:
                    self.context.update_calibration(rendered.estimated_tokens, prompt_tokens)

                action = parse_message(resp.message, resp.raw)
                self.trace.write("action_parsed", {
                    "kind": action.kind,
                    "thought_summary": action.thought_summary,
                    "tool_names": [tc.name for tc in action.tool_calls],
                })

                # persist assistant message (complete OpenAI object)
                self._persist_assistant(session_id, run_id, resp.message, action)

                if action.thought_summary:
                    emit(TurnEvent("thought", {"text": action.thought_summary}))

                if action.kind == "final_answer":
                    emit(TurnEvent("final", {"text": action.content}))
                    self.trace.write("run_finished", {"final_answer": action.content, "steps": step})
                    self.sessions.finish_run(run_id, "finished")
                    return TurnResult(run_id, "finished", action.content, step, events)

                if action.kind == "tool_calls":
                    if step >= config.MAX_AGENT_STEPS:
                        # do NOT execute tools; wrap up.
                        msg = config.MAX_LOOP_MESSAGE
                        emit(TurnEvent("notice", {"text": msg}))
                        emit(TurnEvent("final", {"text": msg}))
                        self.trace.write("run_finished", {"final_answer": msg, "steps": step})
                        self.sessions.finish_run(run_id, "partial")
                        return TurnResult(run_id, "partial", msg, step, events)
                    self._execute_tools(user_id, session_id, run_id, action.tool_calls, emit)
                    continue

                # invalid
                if not invalid_retried:
                    invalid_retried = True
                    correction = {
                        "role": "system",
                        "content": "上一条回复无法解析为最终答案或合法工具调用，"
                                   "请直接给出最终答案，或按工具 schema 正确调用工具。",
                    }
                    self.sessions.append_event(
                        session_id=session_id, run_id=run_id, role="system",
                        event_type="system_note", message=correction,
                    )
                    emit(TurnEvent("notice", {"text": "解析失败，尝试纠错重试"}))
                    continue

                # give up
                final = action.content or "抱歉，我暂时无法给出有效回答。"
                emit(TurnEvent("final", {"text": final}))
                self.trace.write("run_finished", {"final_answer": final, "steps": step})
                self.sessions.finish_run(run_id, "partial")
                return TurnResult(run_id, "partial", final, step, events)

        except Exception as e:  # noqa: BLE001
            self.trace.write("run_failed", {"error": str(e), "steps": step})
            self.sessions.finish_run(run_id, "failed", error=str(e))
            emit(TurnEvent("error", {"text": str(e)}))
            return TurnResult(run_id, "failed", None, step, events, error=str(e))

    # -- helpers ------------------------------------------------------------
    def _persist_assistant(self, session_id, run_id, message: dict, action: AgentAction) -> None:
        stored = {"role": "assistant", "content": message.get("content") or ""}
        if action.reasoning_content:
            stored["reasoning_content"] = action.reasoning_content
        if message.get("tool_calls"):
            stored["tool_calls"] = message["tool_calls"]
        self.sessions.append_event(
            session_id=session_id, run_id=run_id, role="assistant",
            event_type="assistant_message", message=stored,
        )

    def _maybe_compact(self, session_id: str, memory_block: str) -> None:
        rendered = self.context.build(session_id=session_id, memory_block=memory_block)
        if rendered.estimated_tokens < config.CONTEXT_COMPACT_THRESHOLD_TOKENS:
            return
        candidates, _ = self.context.compaction_candidates(session_id)
        if not candidates:
            return
        compaction_mod.run_compaction(
            llm=self.llm, store=self.store, sessions=self.sessions,
            candidates=candidates, session_id=session_id, trace=self.trace,
        )

    def _execute_tools(self, user_id, session_id, run_id, tool_calls: list[ToolCall], emit) -> None:
        ctx = ToolContext(
            user_id=user_id, session_id=session_id, run_id=run_id,
            store=self.store, memory=self._memory(user_id), trace=self.trace, env=self.env,
        )
        for tc in tool_calls:
            emit(TurnEvent("tool_call", {"name": tc.name, "arguments": tc.arguments}))
            self.trace.write("tool_started", {"tool_name": tc.name, "arguments": tc.arguments})
            started = _now()

            result = self._run_single_tool(tc, ctx)

            ended = _now()
            payload = result.to_tool_payload(tc.name)
            tool_message = {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(payload, ensure_ascii=False),
            }
            self.sessions.append_event(
                session_id=session_id, run_id=run_id, role="tool",
                event_type="tool_result", message=tool_message,
            )
            self.trace.record_tool_trace(
                tool_name=tc.name, arguments=tc.arguments, result=payload,
                ok=result.ok, started_at=started, ended_at=ended, error=result.error,
            )
            if result.ok:
                self.trace.write("tool_finished", {
                    "tool_name": tc.name, "ok": True,
                    "content": result.content, "data": result.data,
                })
            else:
                self.trace.write("tool_failed", {
                    "tool_name": tc.name, "error": result.error, "arguments": tc.arguments,
                })
            emit(TurnEvent("tool_result", {
                "name": tc.name, "ok": result.ok,
                "content": result.content if result.ok else (result.error or ""),
            }))

    def _run_single_tool(self, tc: ToolCall, ctx: ToolContext) -> ToolResult:
        if tc.parse_error:
            return ToolResult(ok=False, content="", error=tc.parse_error)
        spec = self.registry.get(tc.name)
        if spec is None:
            return ToolResult(ok=False, content="", error=f"工具不存在: {tc.name}")
        arguments = self.registry.apply_defaults(tc.name, tc.arguments)
        err = self.registry.validate_arguments(tc.name, arguments)
        if err:
            return ToolResult(ok=False, content="", error=err)
        try:
            return spec.handler(arguments, ctx)
        except Exception as e:  # noqa: BLE001 - a tool crash must not kill the run
            return ToolResult(ok=False, content="", error=f"工具执行异常: {e}")
