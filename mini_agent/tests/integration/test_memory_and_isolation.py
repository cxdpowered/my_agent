"""Integration: cross-session memory recall, session isolation, compaction."""
from mini_agent import config
from mini_agent.tests.conftest import FakeLLM


def test_memory_cross_session(make_runtime):
    # session A: model writes a long-term preference via the memory tool
    llm = FakeLLM([
        FakeLLM.tool([("c1", "memory",
                       {"operation": "write", "path": "facts.md", "content": "常用城市：武汉"})]),
        FakeLLM.final("好的，我记住了你常用城市是武汉。"),
        # session B first (and only) call: should see recalled memory in context
        FakeLLM.final("你常用城市是武汉。"),
    ])
    rt = make_runtime(llm)
    sa = rt.sessions.create_session("za", "A")
    rt.run(user_id="za", session_id=sa, user_input="记住我常用城市是武汉")

    sb = rt.sessions.create_session("za", "B")
    rt.run(user_id="za", session_id=sb, user_input="我常用城市是哪里")

    # The third llm call (session B) must include the 【长期记忆】 block with 武汉
    b_call = llm.calls[2]
    joined = "\n".join(m.get("content", "") for m in b_call["messages"])
    assert "【长期记忆】" in joined
    assert "武汉" in joined


def test_session_isolation(make_runtime):
    llm = FakeLLM([
        FakeLLM.final("A 会话谈天气。"),
        FakeLLM.final("B 会话谈周报。"),
    ])
    rt = make_runtime(llm)
    sa = rt.sessions.create_session("za", "weather")
    sb = rt.sessions.create_session("za", "weekly")
    rt.run(user_id="za", session_id=sa, user_input="今天天气 secret-A-topic")
    rt.run(user_id="za", session_id=sb, user_input="帮我写周报")

    # session B's context must NOT contain session A's ordinary conversation
    b_call = llm.calls[1]
    joined = "\n".join(m.get("content", "") for m in b_call["messages"])
    assert "secret-A-topic" not in joined


def test_compaction_triggers(make_runtime, monkeypatch):
    # Force compaction to fire: tiny token threshold + tiny recent window so
    # older turns fall outside the protected window and become candidates.
    monkeypatch.setattr(config, "CONTEXT_COMPACT_THRESHOLD_TOKENS", 1)
    monkeypatch.setattr(config, "RECENT_TURNS_TO_KEEP", 2)

    # Enough scripted final replies to cover normal turns + summariser calls.
    llm = FakeLLM([FakeLLM.final(f"答复{i}") for i in range(1, 12)])
    rt = make_runtime(llm)
    sid = rt.sessions.create_session("za", "long")

    rt.run(user_id="za", session_id=sid, user_input="第一轮")
    rt.run(user_id="za", session_id=sid, user_input="第二轮")
    events_before = rt.sessions.get_events(sid)

    # by the third turn, turn-1's events are outside the 2-turn window -> compact
    rt.run(user_id="za", session_id=sid, user_input="第三轮")

    # a compaction row must exist
    comp = rt.store.query_one("SELECT * FROM compactions WHERE session_id=?", (sid,))
    assert comp is not None
    assert comp["covered_until_event_id"] is not None
    # session mirror updated
    sess = rt.sessions.get_session(sid)
    assert sess["last_summary_event_id"] == comp["covered_until_event_id"]
    # original events are NOT deleted (lossless log)
    events_after = rt.sessions.get_events(sid)
    assert len(events_after) >= len(events_before)
    # a later rendered context used the summary block
    summary_seen = any(
        any("【会话摘要】" in m.get("content", "") for m in call["messages"])
        for call in llm.calls
    )
    assert summary_seen
