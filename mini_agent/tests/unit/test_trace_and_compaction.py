import json

from mini_agent.core.trace import TraceWriter, _whitelist
from mini_agent.core.compaction import _extract_json


def test_whitelist_drops_credentials():
    payload = {"messages": [{"role": "user", "content": "hi"}],
               "authorization": "Bearer secret", "api_key": "sk-xxx",
               "headers": {"Authorization": "secret"}}
    out = _whitelist("llm_request", payload)
    assert "messages" in out
    assert "authorization" not in out
    assert "api_key" not in out
    assert "headers" not in out


def test_trace_writer_jsonl(store, tmp_path):
    path = tmp_path / "trace.jsonl"
    tw = TraceWriter(store=store, jsonl_path=path, run_id="run1")
    tw.write("run_started", {"user_input": "hello", "api_key": "leak"})
    tw.write("run_finished", {"final_answer": "done", "steps": 2})
    records = tw.read_run("run1")
    assert len(records) == 2
    assert records[0]["payload"]["user_input"] == "hello"
    assert "api_key" not in records[0]["payload"]
    # no credentials anywhere in the file
    text = path.read_text(encoding="utf-8")
    assert "leak" not in text


def test_compaction_json_extract():
    good = '{"用户目标":"查天气","关键事实":["武汉"]}'
    assert _extract_json(good)["用户目标"] == "查天气"
    fenced = "```json\n{\"a\":1}\n```"
    assert _extract_json(fenced) == {"a": 1}
    embedded = "这是摘要 {\"a\":2} 结束"
    assert _extract_json(embedded) == {"a": 2}
    assert _extract_json("完全不是 json") is None
