"""Integration tests: full runtime loop driven by a scripted fake LLM."""
import json

from mini_agent import config
from mini_agent.tests.conftest import FakeLLM


def _run(rt, user, sess, text):
    return rt.run(user_id=user, session_id=sess, user_input=text)


def _new_session(rt, user="za", title="t"):
    return rt.sessions.create_session(user, title)


def test_direct_reply(make_runtime):
    llm = FakeLLM([FakeLLM.final("你好，我可以帮你。")])
    rt = make_runtime(llm)
    sid = _new_session(rt)
    res = _run(rt, "za", sid, "hi")
    assert res.status == "finished"
    assert res.final_answer == "你好，我可以帮你。"
    assert len(llm.calls) == 1


def test_calculator_flow(make_runtime):
    llm = FakeLLM([
        FakeLLM.tool([("c1", "calculator", {"expression": "12*8"})], reasoning="cot-1"),
        FakeLLM.final("12*8 = 96"),
    ])
    rt = make_runtime(llm)
    sid = _new_session(rt)
    res = _run(rt, "za", sid, "12*8 等于多少")
    assert res.status == "finished"
    assert "96" in res.final_answer
    # tool result was persisted
    events = rt.sessions.get_events(sid)
    tool_events = [e for e in events if e["role"] == "tool"]
    assert tool_events
    payload = json.loads(tool_events[0]["message"]["content"])
    assert payload["ok"] and "96" in payload["summary"]


def test_search_flow(make_runtime, monkeypatch):
    from mini_agent.core.tools import search as search_mod

    class R:
        status_code = 200
        text = "{}"
        def json(self):
            return {"results": [{"title": "Tavily", "url": "u", "content": "search api", "score": 1}]}
    monkeypatch.setattr(search_mod.httpx, "post", lambda *a, **k: R())

    llm = FakeLLM([
        FakeLLM.tool([("c1", "search", {"query": "tavily"})]),
        FakeLLM.final("Tavily 是一个搜索 API。"),
    ])
    rt = make_runtime(llm, env={"TAVILY_API_KEY": "k"})
    sid = _new_session(rt)
    res = _run(rt, "za", sid, "tavily 是什么")
    assert res.status == "finished" and "Tavily" in res.final_answer


def test_weather_flow(make_runtime, monkeypatch):
    from mini_agent.core.tools import weather as weather_mod

    class R:
        status_code = 200
        text = "{}"
        def json(self):
            return {"status": "1", "lives": [{"city": "武汉市", "weather": "多云",
                    "temperature": "30", "winddirection": "东", "windpower": "3",
                    "humidity": "50", "reporttime": "now"}]}
    monkeypatch.setattr(weather_mod.httpx, "get", lambda *a, **k: R())

    llm = FakeLLM([
        FakeLLM.tool([("c1", "weather", {"location": "420100"})]),
        FakeLLM.final("武汉今天多云，30°C。"),
    ])
    rt = make_runtime(llm, env={"AMAP_API_KEY": "k"})
    sid = _new_session(rt)
    res = _run(rt, "za", sid, "武汉天气")
    assert res.status == "finished" and "武汉" in res.final_answer


def test_read_docs_flow(make_runtime, tmp_path):
    doc = tmp_path / "note.md"
    doc.write_text("# 标题\n这是内容。", encoding="utf-8")
    llm = FakeLLM([
        FakeLLM.tool([("c1", "read_docs", {"path": str(doc)})]),
        FakeLLM.final("文档讲了一个标题和内容。"),
    ])
    rt = make_runtime(llm)
    sid = _new_session(rt)
    res = _run(rt, "za", sid, f"读取 {doc}")
    assert res.status == "finished"


def test_max_loop_stops(make_runtime):
    # LLM always asks for a tool call
    always_tool = FakeLLM.tool([("c1", "calculator", {"expression": "1+1"})])
    llm = FakeLLM([always_tool])  # single script repeats
    rt = make_runtime(llm)
    sid = _new_session(rt)
    res = _run(rt, "za", sid, "loop forever")
    assert res.status == "partial"
    assert res.steps == config.MAX_AGENT_STEPS
    assert llm.calls and len(llm.calls) == config.MAX_AGENT_STEPS
    assert config.MAX_LOOP_MESSAGE in (res.final_answer or "")
    # on the last (8th) LLM call we must NOT have executed the tool -> count tool events
    events = rt.sessions.get_events(sid)
    tool_events = [e for e in events if e["role"] == "tool"]
    # 7 tool executions (steps 1..7), step 8 returns tool_calls but is not executed
    assert len(tool_events) == config.MAX_AGENT_STEPS - 1


def test_reasoning_content_replayed(make_runtime):
    llm = FakeLLM([
        FakeLLM.tool([("c1", "calculator", {"expression": "2+2"})], reasoning="secret-cot"),
        FakeLLM.final("2+2=4"),
    ])
    rt = make_runtime(llm)
    sid = _new_session(rt)
    _run(rt, "za", sid, "2+2")
    # the SECOND llm call's messages must contain the assistant with reasoning_content
    second_call_messages = llm.calls[1]["messages"]
    assistant_msgs = [m for m in second_call_messages
                      if m.get("role") == "assistant" and m.get("tool_calls")]
    assert assistant_msgs
    assert assistant_msgs[0]["reasoning_content"] == "secret-cot"
    # and the tool message follows with matching id
    tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
    assert tool_msgs[0]["tool_call_id"] == "c1"
