"""CLIP MASTER settings. Everything is read from environment variables / a local `.env` file.

Copy `.env.example` to `.env` and fill what you need. No path or key is hard-coded.
"""
from __future__ import annotations

import os
from pathlib import Path

try:  # python-dotenv is a hard dependency, but keep imports of this module safe in minimal envs
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*_a, **_k):  # type: ignore[misc]
        return False

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)


def env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except ValueError:
        return default


def env_path(name: str, default: Path) -> Path:
    raw = env_str(name)
    return Path(raw).expanduser() if raw else default


# --- Server -----------------------------------------------------------------
HOST = env_str("CLIP_MASTER_HOST", "127.0.0.1")
PORT = env_int("CLIP_MASTER_PORT", 7866)

# --- Data folders (default: next to the code; all of them are git-ignored) ---
DATA_DIR = env_path("CLIP_MASTER_DATA_DIR", BASE_DIR)
PROJECTS_DIR = env_path("CLIP_MASTER_PROJECTS_DIR", DATA_DIR / "projects")
OUTPUTS_DIR = env_path("CLIP_MASTER_OUTPUTS_DIR", DATA_DIR / "outputs")
MODELS_DIR = env_path("CLIP_MASTER_MODELS_DIR", DATA_DIR / "models")
BIN_DIR = env_path("CLIP_MASTER_BIN_DIR", BASE_DIR / "bin")

# --- External binaries (optional: PATH is used when empty) -------------------
FFMPEG_PATH = env_str("FFMPEG_PATH")
TWITCHDOWNLOADER_CLI = env_str("TWITCHDOWNLOADER_CLI")

# --- LLM (AI crew + subtitle translation) ------------------------------------
# LLM_PROVIDER: auto (default) -> Groq if GROQ_API_KEY is set, otherwise local Ollama ("no-key mode")
#               groq | ollama | none (force a mode; "none" disables every LLM call)
LLM_PROVIDER = env_str("LLM_PROVIDER", "auto").lower()
GROQ_API_KEY = env_str("GROQ_API_KEY")
GROQ_URL = env_str("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions")
GROQ_MODEL_SMALL = env_str("GROQ_MODEL_SMALL", "qwen/qwen3.8-27b")
GROQ_MODEL_BIG = env_str("GROQ_MODEL_BIG", "openai/gpt-oss-120b")
# OLLAMA_URL may be the server root (http://localhost:11434) or the full /api/chat URL.
OLLAMA_URL = env_str("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = env_str("OLLAMA_MODEL") or env_str("MODEL_SMALL", "qwen2.5:7b")
LLM_TIMEOUT = env_int("LLM_TIMEOUT", 120)

# --- Limits ------------------------------------------------------------------
MAX_UPLOAD_MB = env_int("CLIP_MASTER_MAX_UPLOAD_MB", 2048)
DOWNLOAD_TIMEOUT = env_int("CLIP_MASTER_DOWNLOAD_TIMEOUT", 1800)  # seconds, yt-dlp / streamlink
