"""Tool base types: ToolSpec / ToolRegistry / ToolContext / ToolResult.

Tools are plain callables described by a JSON Schema. The registry emits a
stable-ordered OpenAI `tools` array and validates arguments locally (no
provider strict mode).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

try:
    from jsonschema import Draft7Validator
except Exception:  # pragma: no cover - jsonschema is a declared dependency
    Draft7Validator = None  # type: ignore


@dataclass
class ToolResult:
    ok: bool
    content: str                       # compact summary returned to the LLM
    data: Any = None                   # structured payload -> trace
    error: str | None = None

    def to_tool_payload(self, tool_name: str) -> dict[str, Any]:
        """Compact JSON string body handed back to the LLM as the tool message."""
        if self.ok:
            return {"ok": True, "tool": tool_name, "summary": self.content, "data": self.data}
        return {"ok": False, "tool": tool_name, "error": self.error or self.content}


@dataclass
class ToolContext:
    user_id: str
    session_id: str
    run_id: str
    store: Any                         # StateStore
    memory: Any                        # MemoryStore
    trace: Any                         # TraceWriter
    env: Mapping[str, str]             # read-only env view (for API keys)


ToolHandler = Callable[[dict, ToolContext], ToolResult]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters_schema: dict
    handler: ToolHandler

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._order: list[str] = []

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool name: {spec.name}")
        self._tools[spec.name] = spec
        self._order.append(spec.name)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._order)

    def to_openai_tools(self) -> list[dict]:
        """Stable-ordered tools array (registration order)."""
        return [self._tools[name].to_openai_tool() for name in self._order]

    # --- local JSON Schema validation --------------------------------------
    def validate_arguments(self, name: str, arguments: dict) -> str | None:
        """Return None if valid, else a human-readable error string."""
        spec = self._tools.get(name)
        if spec is None:
            return f"unknown tool: {name}"
        if Draft7Validator is None:  # pragma: no cover
            return None
        validator = Draft7Validator(spec.parameters_schema)
        errors = sorted(validator.iter_errors(arguments), key=lambda e: list(e.path))
        if not errors:
            return None
        parts = []
        for e in errors:
            loc = "/".join(str(p) for p in e.path) or "(root)"
            parts.append(f"{loc}: {e.message}")
        return "参数校验失败: " + "; ".join(parts)

    def apply_defaults(self, name: str, arguments: dict) -> dict:
        """Fill in top-level `default` values declared in the schema."""
        spec = self._tools.get(name)
        if spec is None:
            return arguments
        props = spec.parameters_schema.get("properties", {})
        out = dict(arguments)
        for key, meta in props.items():
            if key not in out and isinstance(meta, dict) and "default" in meta:
                out[key] = meta["default"]
        return out
