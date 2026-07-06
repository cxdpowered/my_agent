"""User-scoped memory: file storage + keyword index + recall.

Memory is user-scoped and stored under `data/memories/{user_id}/`. All paths are
resolved relative to that root and validated with realpath prefix checking, so
absolute paths and `..` traversal are rejected. (read_docs is unrestricted;
memory is restricted — see README.)
"""
from __future__ import annotations

import os
import re
import string
from datetime import datetime, timezone
from pathlib import Path

from .. import config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryPathError(Exception):
    pass


class MemoryStore:
    def __init__(self, user_id: str, store, memories_root: str | Path | None = None):
        self.user_id = user_id
        self.store = store
        root = Path(memories_root) if memories_root else config.MEMORIES_DIR
        self.user_root = (root / user_id).resolve()
        self.user_root.mkdir(parents=True, exist_ok=True)

    # --- path safety -------------------------------------------------------
    def _resolve(self, rel_path: str) -> Path:
        if not rel_path or not rel_path.strip():
            raise MemoryPathError("path 不能为空")
        p = Path(rel_path)
        if p.is_absolute():
            raise MemoryPathError("memory 路径必须是相对路径")
        candidate = (self.user_root / p).resolve()
        # realpath prefix check
        try:
            candidate.relative_to(self.user_root)
        except ValueError:
            raise MemoryPathError("memory 路径越界，拒绝访问")
        return candidate

    # --- file operations ---------------------------------------------------
    def list_files(self) -> list[str]:
        out = []
        for p in sorted(self.user_root.rglob("*")):
            if p.is_file():
                out.append(str(p.relative_to(self.user_root)).replace(os.sep, "/"))
        return out

    def read(self, rel_path: str) -> str:
        target = self._resolve(rel_path)
        if not target.exists():
            raise FileNotFoundError(rel_path)
        return target.read_text(encoding="utf-8")

    def write(self, rel_path: str, content: str) -> None:
        target = self._resolve(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._reindex_file(rel_path, content)

    def append(self, rel_path: str, content: str) -> None:
        target = self._resolve(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        new_content = existing + sep + content
        target.write_text(new_content, encoding="utf-8")
        self._reindex_file(rel_path, new_content)

    # --- index -------------------------------------------------------------
    @staticmethod
    def _chunk(content: str) -> list[str]:
        """Split by markdown '##' headings; else by blank-line paragraphs."""
        text = content.strip()
        if not text:
            return []
        if re.search(r"(?m)^##\s", text):
            parts = re.split(r"(?m)(?=^##\s)", text)
        else:
            parts = re.split(r"\n\s*\n", text)
        return [p.strip() for p in parts if p.strip()]

    def _reindex_file(self, rel_path: str, content: str) -> None:
        rel = rel_path.replace(os.sep, "/")
        self.store.execute(
            "DELETE FROM memory_index WHERE user_id=? AND path=?",
            (self.user_id, rel),
        )
        now = _now()
        for chunk in self._chunk(content):
            self.store.execute(
                "INSERT INTO memory_index(user_id, path, chunk, updated_at) VALUES(?,?,?,?)",
                (self.user_id, rel, chunk, now),
            )

    # --- recall ------------------------------------------------------------
    @staticmethod
    def _keywords(text: str) -> list[str]:
        lowered = text.lower()
        # strip ascii punctuation
        table = str.maketrans({c: " " for c in string.punctuation})
        lowered = lowered.translate(table)
        # ascii word tokens
        ascii_tokens = [t for t in re.findall(r"[a-z0-9]+", lowered) if len(t) >= 2]
        # CJK characters as individual substring tokens
        cjk_tokens = re.findall(r"[一-鿿]", text)
        return ascii_tokens + cjk_tokens

    def search(self, query: str, top_k: int = None) -> list[dict]:
        top_k = top_k or config.MEMORY_TOP_K
        rows = self.store.query(
            "SELECT path, chunk, updated_at FROM memory_index WHERE user_id=?",
            (self.user_id,),
        )
        keywords = set(self._keywords(query))
        if not keywords:
            return []
        scored = []
        for r in rows:
            chunk = r["chunk"] or ""
            chunk_lc = chunk.lower()
            score = 0
            for kw in keywords:
                if kw in chunk_lc:
                    score += 1
            if score > 0:
                scored.append((score, r["updated_at"] or "", {
                    "path": r["path"],
                    "chunk": chunk,
                    "score": score,
                    "updated_at": r["updated_at"],
                }))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [item[2] for item in scored[:top_k]]

    def recall_block(self, query: str, top_k: int = None) -> tuple[str, list[dict]]:
        """Build the fixed 【长期记忆】 injection block. Returns (block, hits)."""
        hits = self.search(query, top_k=top_k)
        if not hits:
            return "", []
        lines = ["【长期记忆】"]
        for h in hits:
            first_line = (h["chunk"].splitlines() or [""])[0].strip().lstrip("#").strip()
            lines.append(f"- ({h['path']}) {first_line}")
        return "\n".join(lines), hits
