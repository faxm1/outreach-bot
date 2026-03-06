# config.py
# Single source of truth for all environment variables.
# All other modules MUST import from here — never call os.getenv() elsewhere.
import os
import sys


def _require(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        print(f"ERROR: Required env var '{name}' is not set. Check your .env file.")
        sys.exit(1)
    return val


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"ERROR: Env var '{name}' must be an integer, got: {raw!r}")
        sys.exit(1)


# ─── Telegram ─────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN          = _require("TELEGRAM_TOKEN")
TELEGRAM_ALLOWED_CHAT_ID = int(_require("TELEGRAM_ALLOWED_CHAT_ID"))

# ─── Sender identity ──────────────────────────────────────────────────────────

SENDER_NAME  = _require("SENDER_NAME")
SENDER_EMAIL = _require("SENDER_EMAIL")
CV_PDF_PATH  = _require("CV_PDF_PATH")

# ─── SMTP ─────────────────────────────────────────────────────────────────────

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_USER = _require("SMTP_USER")
SMTP_PASS = _require("SMTP_PASS")

# ─── Database ─────────────────────────────────────────────────────────────────

DB_PATH                    = os.getenv("DB_PATH", "outreach.db")
DEDUP_WINDOW_DAYS          = _int("DEDUP_WINDOW_DAYS",          30)
MAX_EMAILS_PER_HOUR        = _int("MAX_EMAILS_PER_HOUR",        10)
MAX_EMAILS_PER_DAY         = _int("MAX_EMAILS_PER_DAY",         50)
CONFIRMATION_TIMEOUT_HOURS = _int("CONFIRMATION_TIMEOUT_HOURS", 12)

# ─── Scheduler / send window ──────────────────────────────────────────────────

SEND_TIMEZONE    = os.getenv("SEND_TIMEZONE", "Asia/Riyadh")
SEND_WINDOW_START = _int("SEND_WINDOW_START", 8)
SEND_WINDOW_END   = _int("SEND_WINDOW_END",  18)
MAX_RETRIES       = _int("MAX_RETRIES", 3)

# ─── Ollama ───────────────────────────────────────────────────────────────────

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL",    "huihui_ai/deepseek-r1-abliterated:14b")
