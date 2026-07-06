"""Shared pytest fixtures + a scriptable fake LLM."""
from __future__ import annotations

import json

import pytest

from mini_agent.core.llm import LLMResponse
from mini_agent.core.runtime import Runtime
from mini_agent.core.store import StateStore
from mini_agent.core.tools import build_default_registry
from mini_agent.core.trace import TraceWriter


class FakeLLM:
    """A fake LLM with a scripted list of responses.

    Each script entry is a fully-formed assistant `message` dict. Optionally a
    `usage` dict. Responses are consumed in order; the final one repeats if the
    runtime asks for more (useful for the max-loop test).
    """

    model = "fake-model"

    def __init__(self, scripts: list[dict]):
        self.scripts = list(scripts)
        self.calls: list[dict] = []
        self._i = 0

    @staticmethod
    def final(content: str, reasoning: str | None = None, usage: dict | None = None) -> dict:
        msg = {"role": "assistant", "content": content}
        if reasoning is not None:
            msg["reasoning_content"] = reasoning
        return {"message": msg, "usage": usage or {}}

    @staticmethod
    def tool(calls: list[tuple[str, str, dict]], reasoning: str | None = None,
             content: str = "", usage: dict | None = None) -> dict:
        tool_calls = []
        for cid, name, args in calls:
            tool_calls.append({
                "id": cid,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            })
        msg = {"role": "assistant", "content": content, "tool_calls": tool_calls}
        if reasoning is not None:
            msg["reasoning_content"] = reasoning
        return {"message": msg, "usage": usage or {}}

    def complete(self, *, messages, tools=None) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools})
        if self._i < len(self.scripts):
            script = self.scripts[self._i]
            self._i += 1
        else:
            script = self.scripts[-1]
        return LLMResponse(
            message=script["message"],
            usage=script.get("usage", {}),
            finish_reason="stop",
            raw={"choices": [{"message": script["message"]}], "usage": script.get("usage", {})},
        )


@pytest.fixture
def store():
    s = StateStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def registry():
    return build_default_registry()


@pytest.fixture
def trace_writer(store, tmp_path):
    return TraceWriter(store=store, jsonl_path=tmp_path / "trace.jsonl")


@pytest.fixture
def make_runtime(store, registry, tmp_path):
    def _make(llm, env=None):
        trace = TraceWriter(store=store, jsonl_path=tmp_path / "trace.jsonl")
        return Runtime(
            store=store, llm=llm, registry=registry, trace=trace,
            env=env or {}, memories_root=tmp_path / "memories",
        )
    return _make
