# config.py
# Single source of truth for environment variables.
import os
import sys


def _require(name: str) -> str:
    value = os.getenv(name, '').strip()
    if not value:
        print(f"ERROR: Required env var '{name}' is not set. Check your .env file.")
        sys.exit(1)
    return value


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, '').strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"ERROR: Env var '{name}' must be an integer, got: {raw!r}")
        sys.exit(1)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, '').strip().lower()
    if not raw:
        return default
    if raw in {'1', 'true', 'yes', 'on'}:
        return True
    if raw in {'0', 'false', 'no', 'off'}:
        return False
    print(f"ERROR: Env var '{name}' must be a boolean, got: {raw!r}")
    sys.exit(1)


def _csv_ints(name: str, default: list[int]) -> list[int]:
    raw = os.getenv(name, '').strip()
    if not raw:
        return default
    try:
        values = [int(part.strip()) for part in raw.split(',') if part.strip()]
    except ValueError:
        print(f"ERROR: Env var '{name}' must be a comma-separated list of integers, got: {raw!r}")
        sys.exit(1)
    if not values:
        print(f"ERROR: Env var '{name}' cannot be empty.")
        sys.exit(1)
    return values


# Telegram
TELEGRAM_TOKEN = _require('TELEGRAM_TOKEN')
TELEGRAM_ALLOWED_CHAT_ID = int(_require('TELEGRAM_ALLOWED_CHAT_ID'))

# Sender identity
SENDER_NAME = _require('SENDER_NAME')
SENDER_EMAIL = _require('SENDER_EMAIL')
CV_PDF_PATH = _require('CV_PDF_PATH')

# SMTP
SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com').strip()
SMTP_PORT = _int('SMTP_PORT', 587)
SMTP_USER = os.getenv('SMTP_USER', '').strip()
SMTP_PASS = os.getenv('SMTP_PASS', '').strip()
SMTP_USE_TLS = _bool('SMTP_USE_TLS', True)
SMTP_USE_AUTH = _bool('SMTP_USE_AUTH', True)

if SMTP_USE_AUTH:
    if not SMTP_USER:
        print("ERROR: SMTP_USER is required when SMTP_USE_AUTH=true")
        sys.exit(1)
    if not SMTP_PASS:
        print("ERROR: SMTP_PASS is required when SMTP_USE_AUTH=true")
        sys.exit(1)

# Database / safety
DB_PATH = os.getenv('DB_PATH', 'outreach.db').strip()
DEDUP_WINDOW_DAYS = _int('DEDUP_WINDOW_DAYS', 30)
MAX_EMAILS_PER_HOUR = _int('MAX_EMAILS_PER_HOUR', 10)
MAX_EMAILS_PER_DAY = _int('MAX_EMAILS_PER_DAY', 50)
CONFIRMATION_TIMEOUT_HOURS = _int('CONFIRMATION_TIMEOUT_HOURS', 12)
MAX_CV_SIZE_MB = _int('MAX_CV_SIZE_MB', 5)

# Scheduler / send window
SEND_TIMEZONE = os.getenv('SEND_TIMEZONE', 'Asia/Riyadh').strip()
SEND_WINDOW_START = _int('SEND_WINDOW_START', 8)
SEND_WINDOW_END = _int('SEND_WINDOW_END', 18)
MAX_RETRIES = _int('MAX_RETRIES', 3)
RETRY_BACKOFF_MINUTES = _csv_ints('RETRY_BACKOFF_MINUTES', [5, 30, 120])
RATE_LIMIT_REQUEUE_MINUTES = _int('RATE_LIMIT_REQUEUE_MINUTES', 10)

if not (0 <= SEND_WINDOW_START <= 23 and 1 <= SEND_WINDOW_END <= 24):
    print('ERROR: SEND_WINDOW_START/END must be valid hours.')
    sys.exit(1)
if SEND_WINDOW_START >= SEND_WINDOW_END:
    print('ERROR: SEND_WINDOW_START must be less than SEND_WINDOW_END.')
    sys.exit(1)

# Ollama
OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434').strip()
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'huihui_ai/deepseek-r1-abliterated:14b').strip()
