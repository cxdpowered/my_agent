"""Real external API smoke tests. Skipped by default (marked `live`).

Run explicitly with:  pytest -m live
Requires DEEPSEEK_API_KEY (and TAVILY_API_KEY / AMAP_API_KEY for those cases).
"""
import os

import pytest

from mini_agent.core.llm import LLMClient
from mini_agent.core.runtime import Runtime
from mini_agent.core.store import StateStore
from mini_agent.core.tools import build_default_registry
from mini_agent.core.trace import TraceWriter

pytestmark = pytest.mark.live


def _runtime(tmp_path):
    store = StateStore(str(tmp_path / "agent.sqlite"))
    llm = LLMClient()
    trace = TraceWriter(store=store, jsonl_path=tmp_path / "trace.jsonl")
    return Runtime(store=store, llm=llm, registry=build_default_registry(),
                   trace=trace, env=dict(os.environ), memories_root=tmp_path / "memories")


@pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="no DEEPSEEK_API_KEY")
def test_live_direct_reply(tmp_path):
    rt = _runtime(tmp_path)
    sid = rt.sessions.create_session("smoke", "direct")
    res = rt.run(user_id="smoke", session_id=sid, user_input="用一句话介绍你自己。")
    assert res.status == "finished" and res.final_answer


@pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="no DEEPSEEK_API_KEY")
def test_live_calculator(tmp_path):
    rt = _runtime(tmp_path)
    sid = rt.sessions.create_session("smoke", "calc")
    res = rt.run(user_id="smoke", session_id=sid, user_input="用计算器算 (123+877)*2 等于多少？")
    assert res.status == "finished"
    assert "2000" in (res.final_answer or "")


@pytest.mark.skipif(
    not (os.environ.get("DEEPSEEK_API_KEY") and os.environ.get("AMAP_API_KEY")),
    reason="need DEEPSEEK_API_KEY + AMAP_API_KEY",
)
def test_live_weather(tmp_path):
    rt = _runtime(tmp_path)
    sid = rt.sessions.create_session("smoke", "weather")
    res = rt.run(user_id="smoke", session_id=sid, user_input="武汉今天天气怎么样？")
    assert res.status == "finished" and res.final_answer


@pytest.mark.skipif(
    not (os.environ.get("DEEPSEEK_API_KEY") and os.environ.get("TAVILY_API_KEY")),
    reason="need DEEPSEEK_API_KEY + TAVILY_API_KEY",
)
def test_live_search(tmp_path):
    rt = _runtime(tmp_path)
    sid = rt.sessions.create_session("smoke", "search")
    res = rt.run(user_id="smoke", session_id=sid, user_input="搜索一下 Tavily 是什么，并总结一句。")
    assert res.status == "finished" and res.final_answer
