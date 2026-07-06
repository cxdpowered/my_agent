from mini_agent.core.parser import parse_message


def test_parse_final_answer():
    msg = {"role": "assistant", "content": "你好", "reasoning_content": "secret cot"}
    action = parse_message(msg)
    assert action.kind == "final_answer"
    assert action.content == "你好"
    assert action.reasoning_content == "secret cot"
    assert action.thought_summary is None  # never shown for final answers


def test_parse_tool_calls_keeps_reasoning():
    msg = {
        "role": "assistant",
        "content": "",
        "reasoning_content": "thinking...",
        "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "weather", "arguments": '{"location":"武汉"}'}}
        ],
    }
    action = parse_message(msg)
    assert action.kind == "tool_calls"
    assert action.reasoning_content == "thinking..."
    assert action.tool_calls[0].name == "weather"
    assert action.tool_calls[0].arguments == {"location": "武汉"}
    # thought summary is generated from the template, not the CoT
    assert "weather" in action.thought_summary
    assert "thinking" not in action.thought_summary


def test_parse_multi_tool_summary():
    msg = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "search", "arguments": "{}"}},
        {"id": "c2", "type": "function", "function": {"name": "weather", "arguments": "{}"}},
    ]}
    action = parse_message(msg)
    assert "search" in action.thought_summary and "weather" in action.thought_summary


def test_parse_bad_tool_arguments():
    msg = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{not json"}},
    ]}
    action = parse_message(msg)
    assert action.kind == "tool_calls"
    assert action.tool_calls[0].parse_error is not None


def test_parse_invalid():
    action = parse_message({"role": "assistant", "content": ""})
    assert action.kind == "invalid"
