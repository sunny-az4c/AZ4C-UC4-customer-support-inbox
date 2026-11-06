"""Settings, read from the environment once code/.env has been loaded by __main__."""
import os
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = CODE_DIR.parent

DATA_DIR = Path(os.environ.get("DATA_DIR") or REPO_DIR).resolve()
MOCK_DIR = Path(os.environ.get("MOCK_SYSTEM_DIR") or REPO_DIR / "mock_system").resolve()
RUNS_DIR = CODE_DIR / "runs"          # paused runs waiting for the support lead
TRACES_DIR = CODE_DIR / "traces"      # one JSONL trace per run
CACHE_DIR = CODE_DIR / ".cache" / "llm"

# Provider settings: only these four variables carry keys and addresses.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "").strip().rstrip("/")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_BASE_URL = os.environ.get("GEMINI_BASE_URL", "").strip().rstrip("/")

# Model choices (optional overrides, no secrets).
TRIAGE_MODEL = os.environ.get("TRIAGE_MODEL") or "openai/gpt-oss-120b"
KNOWLEDGE_MODEL = os.environ.get("KNOWLEDGE_MODEL") or "openai/gpt-oss-120b"
WRITER_MODEL = os.environ.get("WRITER_MODEL") or "openai/gpt-oss-20b"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"

LLM_CACHE = (os.environ.get("LLM_CACHE") or "1") != "0"      # reuse identical model calls (eval re-runs)
LLM_OFFLINE = (os.environ.get("LLM_OFFLINE") or "0") == "1"  # skip models, use the rule-based fallbacks
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT") or 60)
LLM_MAX_WAIT = float(os.environ.get("LLM_MAX_WAIT") or 60)  # longest single back-off on a rate limit
