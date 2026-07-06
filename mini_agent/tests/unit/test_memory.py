import pytest

from mini_agent.core.memory import MemoryPathError, MemoryStore


def test_write_read_search(store, tmp_path):
    mem = MemoryStore("za", store, memories_root=tmp_path / "memories")
    mem.write("facts.md", "## 常用城市\n常用城市：武汉\n")
    assert "武汉" in mem.read("facts.md")
    hits = mem.search("武汉")
    assert hits and hits[0]["path"] == "facts.md"


def test_append_rebuilds_index(store, tmp_path):
    mem = MemoryStore("za", store, memories_root=tmp_path / "memories")
    mem.write("preferences.md", "## 周报\n写周报喜欢先结论后细节\n")
    mem.append("preferences.md", "## 语言\n喜欢中文回答\n")
    rows = store.query("SELECT chunk FROM memory_index WHERE user_id='za' AND path='preferences.md'")
    chunks = "\n".join(r["chunk"] for r in rows)
    assert "结论" in chunks and "中文" in chunks
    assert mem.search("中文")


def test_path_traversal_rejected(store, tmp_path):
    mem = MemoryStore("za", store, memories_root=tmp_path / "memories")
    with pytest.raises(MemoryPathError):
        mem.write("../evil.md", "x")
    with pytest.raises(MemoryPathError):
        mem.write("/etc/passwd", "x")


def test_user_isolation(store, tmp_path):
    root = tmp_path / "memories"
    mem_a = MemoryStore("za", store, memories_root=root)
    mem_b = MemoryStore("bob", store, memories_root=root)
    mem_a.write("facts.md", "常用城市：武汉")
    assert mem_b.search("武汉") == []


def test_recall_block(store, tmp_path):
    mem = MemoryStore("za", store, memories_root=tmp_path / "memories")
    mem.write("facts.md", "## 城市\n常用城市：武汉")
    block, hits = mem.recall_block("武汉 天气")
    assert "【长期记忆】" in block
    assert "facts.md" in block
    assert hits
