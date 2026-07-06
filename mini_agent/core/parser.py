"""Parse an OpenAI assistant message into an internal AgentAction.

thought_summary is generated *deterministically from the tool_calls template*
(never derived from reasoning_content) so the full chain-of-thought is never
exposed to the user.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    raw_arguments: str = ""
    parse_error: str | None = None


@dataclass
class AgentAction:
    kind: Literal["final_answer", "tool_calls", "invalid"]
    content: str | None = None
    reasoning_content: str | None = None
    thought_summary: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_response: dict = field(default_factory=dict)
    error: str | None = None


def _arg_summary(name: str, args: dict) -> str:
    """One short human-readable phrase describing a single call's key arg."""
    if not isinstance(args, dict):
        return ""
    for key in ("query", "location", "expression", "path"):
        if key in args and args[key]:
            val = str(args[key])
            if len(val) > 40:
                val = val[:40] + "…"
            return f"（{key}={val}）"
    if "operation" in args:
        return f"（{args['operation']}）"
    return ""


def _make_thought_summary(tool_calls: list[ToolCall]) -> str | None:
    if not tool_calls:
        return None
    if len(tool_calls) == 1:
        tc = tool_calls[0]
        return f"正在调用 {tc.name} …{_arg_summary(tc.name, tc.arguments)}"
    names = "、".join(tc.name for tc in tool_calls)
    return f"正在调用 {names} …"


def parse_message(message: dict, raw_response: dict | None = None) -> AgentAction:
    raw_response = raw_response or {}
    reasoning = message.get("reasoning_content")
    content = message.get("content")
    raw_tool_calls = message.get("tool_calls") or []

    if raw_tool_calls:
        parsed: list[ToolCall] = []
        for tc in raw_tool_calls:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            raw_args = fn.get("arguments", "") or ""
            parse_error = None
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
                if not isinstance(args, dict):
                    args = {}
                    parse_error = "arguments 不是 JSON 对象"
            except json.JSONDecodeError as e:
                args = {}
                parse_error = f"arguments JSON 解析失败: {e}"
            parsed.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=fn.get("name", ""),
                    arguments=args,
                    raw_arguments=raw_args,
                    parse_error=parse_error,
                )
            )
        return AgentAction(
            kind="tool_calls",
            content=content,
            reasoning_content=reasoning,
            thought_summary=_make_thought_summary(parsed),
            tool_calls=parsed,
            raw_response=raw_response,
        )

    if isinstance(content, str) and content.strip():
        return AgentAction(
            kind="final_answer",
            content=content,
            reasoning_content=reasoning,
            thought_summary=None,
            raw_response=raw_response,
        )

    return AgentAction(
        kind="invalid",
        content=content,
        reasoning_content=reasoning,
        raw_response=raw_response,
        error="assistant 消息既无 tool_calls 也无有效 content",
    )
