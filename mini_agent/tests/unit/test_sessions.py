from mini_agent.core.sessions import SessionManager


def test_session_create_and_isolation(store):
    mgr = SessionManager(store)
    s1 = mgr.create_session("za", "one")
    s2 = mgr.create_session("za", "two")
    assert s1 != s2
    sessions = mgr.list_sessions("za")
    ids = {s["session_id"] for s in sessions}
    assert ids == {s1, s2}
    # other user sees nothing
    assert mgr.list_sessions("bob") == []


def test_message_rebuild_tool_pairing(store):
    mgr = SessionManager(store)
    sid = mgr.create_session("za")
    run = mgr.start_run(user_id="za", session_id=sid, user_input="hi")
    mgr.append_event(session_id=sid, run_id=run, role="user",
                     event_type="user_message", message={"role": "user", "content": "hi"})
    assistant = {"role": "assistant", "content": "", "reasoning_content": "cot",
                 "tool_calls": [{"id": "c1", "type": "function",
                                 "function": {"name": "calculator", "arguments": "{}"}}]}
    mgr.append_event(session_id=sid, run_id=run, role="assistant",
                     event_type="assistant_message", message=assistant)
    mgr.append_event(session_id=sid, run_id=run, role="tool",
                     event_type="tool_result",
                     message={"role": "tool", "tool_call_id": "c1", "content": "42"})

    events = mgr.get_events(sid)
    roles = [e["role"] for e in events]
    assert roles == ["user", "assistant", "tool"]
    # assistant with tool_calls immediately precedes the matching tool message
    assert events[1]["message"]["tool_calls"][0]["id"] == "c1"
    assert events[2]["message"]["tool_call_id"] == "c1"
    assert events[1]["message"]["reasoning_content"] == "cot"


def test_archive_and_latest(store):
    mgr = SessionManager(store)
    s1 = mgr.create_session("za", "one")
    s2 = mgr.create_session("za", "two")
    assert mgr.latest_session("za")["session_id"] == s2
    mgr.archive_session(s2)
    assert mgr.latest_session("za")["session_id"] == s1
