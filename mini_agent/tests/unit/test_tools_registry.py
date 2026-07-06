import pytest

from mini_agent.core.tools.base import ToolRegistry, ToolResult, ToolSpec


def _spec(name):
    return ToolSpec(
        name=name,
        description="d",
        parameters_schema={"type": "object", "properties": {"x": {"type": "integer"}},
                           "required": ["x"]},
        handler=lambda a, c: ToolResult(ok=True, content="ok"),
    )


def test_register_and_get():
    reg = ToolRegistry()
    reg.register(_spec("a"))
    assert "a" in reg
    assert reg.get("a").name == "a"
    assert reg.get("missing") is None


def test_duplicate_name_raises():
    reg = ToolRegistry()
    reg.register(_spec("a"))
    with pytest.raises(ValueError):
        reg.register(_spec("a"))


def test_stable_order():
    reg = ToolRegistry()
    for n in ["c", "a", "b"]:
        reg.register(_spec(n))
    assert reg.names() == ["c", "a", "b"]
    tools = reg.to_openai_tools()
    assert [t["function"]["name"] for t in tools] == ["c", "a", "b"]


def test_validation():
    reg = ToolRegistry()
    reg.register(_spec("a"))
    assert reg.validate_arguments("a", {"x": 1}) is None
    assert reg.validate_arguments("a", {}) is not None          # missing required
    assert reg.validate_arguments("a", {"x": "no"}) is not None  # wrong type


def test_apply_defaults():
    reg = ToolRegistry()
    reg.register(ToolSpec(
        name="d", description="", handler=lambda a, c: ToolResult(True, "ok"),
        parameters_schema={"type": "object", "properties": {"n": {"type": "integer", "default": 5}}},
    ))
    assert reg.apply_defaults("d", {})["n"] == 5
    assert reg.apply_defaults("d", {"n": 9})["n"] == 9
