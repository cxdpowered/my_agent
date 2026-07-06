"""Tool package: base types + built-in tools + default registry builder."""
from __future__ import annotations

from .base import ToolContext, ToolRegistry, ToolResult, ToolSpec
from .calculator import calculator_tool
from .search import search_tool
from .weather import weather_tool
from .read_docs import read_docs_tool
from .memory_tool import memory_tool


def build_default_registry() -> ToolRegistry:
    """Register the built-in tools in a stable order (cache-friendly)."""
    reg = ToolRegistry()
    reg.register(calculator_tool())
    reg.register(search_tool())
    reg.register(weather_tool())
    reg.register(read_docs_tool())
    reg.register(memory_tool())
    return reg


__all__ = [
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "build_default_registry",
]
