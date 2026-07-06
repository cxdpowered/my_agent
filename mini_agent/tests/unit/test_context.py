from mini_agent.core.context import (ContextBuilder, estimate_tokens,
                                      render_message_for_api)
from mini_agent.core.sessions import SessionManager
from mini_agent import config


def test_estimate_tokens_nonzero():
    msgs = [{"role": "user", "content": "你好世界 hello world"}]
    assert estimate_tokens(msgs) > 0


def test_render_strips_reasoning_from_final():
    final = {"role": "assistant", "content": "answer", "reasoning_content": "cot"}
    out = render_message_for_api(final)
    assert "reasoning_content" not in out
    assert out["content"] == "answer"


def test_render_keeps_reasoning_for_tool_calls():
    msg = {"role": "assistant", "content": "", "reasoning_content": "cot",
           "tool_calls": [{"id": "c1", "type": "function",
                           "function": {"name": "x", "arguments": "{}"}}]}
    out = render_message_for_api(msg)
    assert out["reasoning_content"] == "cot"
    assert out["tool_calls"]


def test_recent_window_and_tool_pairing(store):
    mgr = SessionManager(store)
    sid = mgr.create_session("za")
    run = mgr.start_run(user_id="za", session_id=sid, user_input="x")
    # create many turns so the recent-window trims
    for i in range(config.RECENT_TURNS_TO_KEEP + 5):
        mgr.append_event(session_id=sid, run_id=run, role="user",
                         event_type="user_message", message={"role": "user", "content": f"q{i}"})
        mgr.append_event(session_id=sid, run_id=run, role="assistant",
                         event_type="assistant_message",
                         message={"role": "assistant", "content": f"a{i}"})
    cb = ContextBuilder(mgr, store)
    rc = cb.build(session_id=sid)
    # system prompt + at most RECENT_TURNS_TO_KEEP*2 events
    convo = [m for m in rc.messages if m["role"] in ("user", "assistant")]
    assert len(convo) <= config.RECENT_TURNS_TO_KEEP * 2
    # last message should be the most recent assistant answer
    assert rc.messages[-1]["content"].startswith("a")


def test_calibration_updates(store):
    mgr = SessionManager(store)
    cb = ContextBuilder(mgr, store)
    before = cb.calibration
    cb.update_calibration(estimated=100, actual_prompt_tokens=200)
    assert cb.calibration != before
