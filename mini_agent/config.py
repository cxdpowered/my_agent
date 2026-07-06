"""Central configuration and tunable constants.

All secrets come from environment variables only (never hard-coded). `.env` is
loaded on import if python-dotenv is available.
"""
from __future__ import annotations

import os
from pathlib import Path

try:  # optional, but recommended
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


# --- Paths -----------------------------------------------------------------
# Project root = parent of the `mini_agent` package directory.
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent


def _data_dir() -> Path:
    return Path(os.environ.get("MINI_AGENT_DATA_DIR", PROJECT_ROOT / "data"))


def _logs_dir() -> Path:
    return Path(os.environ.get("MINI_AGENT_LOGS_DIR", PROJECT_ROOT / "logs"))


DATA_DIR = _data_dir()
LOGS_DIR = _logs_dir()
DB_PATH = DATA_DIR / "agent.sqlite"
MEMORIES_DIR = DATA_DIR / "memories"
TRACE_JSONL = LOGS_DIR / "trace.jsonl"


# --- Agent loop ------------------------------------------------------------
MAX_AGENT_STEPS = 8  # counted by number of LLM calls (see design §6.3)

# --- Context / compaction --------------------------------------------------
CONTEXT_COMPACT_THRESHOLD_TOKENS = 60_000
RECENT_TURNS_TO_KEEP = 8
MEMORY_TOP_K = 5

# --- LLM defaults (all overridable via env) --------------------------------
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEEPSEEK_REASONING_EFFORT = os.environ.get("DEEPSEEK_REASONING_EFFORT", "high")
LLM_MAX_TOKENS = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "4096"))
LLM_MAX_RETRIES = 2

MAX_LOOP_MESSAGE = (
    "当前任务达到最大工具调用轮次，已停止继续执行。"
    "下面是已经完成的部分和最后一次工具结果。"
)


def ensure_dirs() -> None:
    """Create the on-disk directories the runtime needs."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
