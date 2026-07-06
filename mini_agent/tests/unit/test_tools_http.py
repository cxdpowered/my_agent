import json

import pytest

from mini_agent.core.tools.base import ToolContext
from mini_agent.core.tools import search as search_mod
from mini_agent.core.tools import weather as weather_mod
from mini_agent.core.tools.read_docs import read_docs_tool


class FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _ctx(env):
    return ToolContext(user_id="za", session_id="s", run_id="r", store=None,
                       memory=None, trace=None, env=env)


# --- search ----------------------------------------------------------------
def test_search_success(monkeypatch):
    payload = {"results": [{"title": "T", "url": "http://x", "content": "snippet", "score": 0.9}],
               "answer": "a"}
    monkeypatch.setattr(search_mod.httpx, "post", lambda *a, **k: FakeResp(200, payload))
    r = search_mod.search_tool().handler({"query": "tavily"}, _ctx({"TAVILY_API_KEY": "k"}))
    assert r.ok
    assert r.data["results"][0]["url"] == "http://x"


def test_search_401(monkeypatch):
    monkeypatch.setattr(search_mod.httpx, "post", lambda *a, **k: FakeResp(401, text="unauth"))
    r = search_mod.search_tool().handler({"query": "x"}, _ctx({"TAVILY_API_KEY": "k"}))
    assert not r.ok and "401" in r.error


def test_search_429(monkeypatch):
    monkeypatch.setattr(search_mod.httpx, "post", lambda *a, **k: FakeResp(429))
    r = search_mod.search_tool().handler({"query": "x"}, _ctx({"TAVILY_API_KEY": "k"}))
    assert not r.ok and "429" in r.error


def test_search_missing_key():
    r = search_mod.search_tool().handler({"query": "x"}, _ctx({}))
    assert not r.ok


# --- weather ---------------------------------------------------------------
def test_weather_adcode_direct(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        assert "weatherInfo" in url
        assert params["city"] == "420100"
        return FakeResp(200, {"status": "1", "lives": [
            {"city": "武汉市", "weather": "多云", "temperature": "30",
             "winddirection": "东", "windpower": "3", "humidity": "50", "reporttime": "now"}]})
    monkeypatch.setattr(weather_mod.httpx, "get", fake_get)
    r = weather_mod.weather_tool().handler({"location": "420100"}, _ctx({"AMAP_API_KEY": "k"}))
    assert r.ok and "多云" in r.content


def test_weather_geocode_then_weather(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        if "geocode" in url:
            calls["n"] += 1
            return FakeResp(200, {"status": "1", "geocodes": [
                {"adcode": "420100", "formatted_address": "湖北省武汉市"}]})
        return FakeResp(200, {"status": "1", "lives": [
            {"city": "武汉市", "weather": "晴", "temperature": "28",
             "winddirection": "南", "windpower": "2", "humidity": "40", "reporttime": "t"}]})
    monkeypatch.setattr(weather_mod.httpx, "get", fake_get)
    r = weather_mod.weather_tool().handler({"location": "武汉"}, _ctx({"AMAP_API_KEY": "k"}))
    assert r.ok and calls["n"] == 1 and "晴" in r.content


def test_weather_geocode_fail(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return FakeResp(200, {"status": "1", "geocodes": []})
    monkeypatch.setattr(weather_mod.httpx, "get", fake_get)
    r = weather_mod.weather_tool().handler({"location": "不存在的地方"}, _ctx({"AMAP_API_KEY": "k"}))
    assert not r.ok and "无法解析" in r.error


# --- read_docs -------------------------------------------------------------
def test_read_docs_text(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    r = read_docs_tool().handler({"path": str(f)}, _ctx({}))
    assert r.ok and "line1" in r.content and r.data["total_lines"] == 3


def test_read_docs_chunking(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
    r = read_docs_tool().handler({"path": str(f), "max_chars": 30}, _ctx({}))
    assert r.ok and r.data["truncated"] and r.data["next_start_line"] is not None


def test_read_docs_missing(tmp_path):
    r = read_docs_tool().handler({"path": str(tmp_path / "nope.txt")}, _ctx({}))
    assert not r.ok


def test_read_docs_binary(tmp_path):
    f = tmp_path / "bin"
    f.write_bytes(b"\x00\x01\x02binary")
    r = read_docs_tool().handler({"path": str(f)}, _ctx({}))
    assert not r.ok
