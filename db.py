# db.py
import aiosqlite
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import (
    DB_PATH,
    DEDUP_WINDOW_DAYS          as DEDUP_DAYS,
    MAX_EMAILS_PER_HOUR        as MAX_PER_HOUR,
    MAX_EMAILS_PER_DAY         as MAX_PER_DAY,
    CONFIRMATION_TIMEOUT_HOURS as CONFIRM_TIMEOUT_H,
)

# ─── Schema ───────────────────────────────────────────────────────────────────

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS requests (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id           TEXT    NOT NULL UNIQUE,
    telegram_update_id   INTEGER UNIQUE,
    telegram_chat_id     INTEGER,
    recipient_email      TEXT    NOT NULL,
    generated_subject    TEXT,
    generated_body       TEXT,
    cv_filename          TEXT,
    status               TEXT    NOT NULL DEFAULT 'pending_confirmation',
    scheduled_send_time  TEXT,
    confirmation_deadline TEXT,
    confirmed_at         TEXT,
    created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_req_status    ON requests (status);
CREATE INDEX IF NOT EXISTS idx_req_scheduled ON requests (scheduled_send_time);
CREATE INDEX IF NOT EXISTS idx_req_recipient ON requests (recipient_email);
CREATE INDEX IF NOT EXISTS idx_req_update_id ON requests (telegram_update_id);

CREATE TABLE IF NOT EXISTS send_attempts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id       TEXT    NOT NULL REFERENCES requests(request_id),
    attempt_number   INTEGER NOT NULL DEFAULT 1,
    status           TEXT    NOT NULL,
    failure_category TEXT,
    smtp_response    TEXT,
    error_detail     TEXT,
    attempted_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_att_request ON send_attempts (request_id);

CREATE TABLE IF NOT EXISTS sent_dedup (
    recipient_email TEXT PRIMARY KEY,
    request_id      TEXT NOT NULL,
    sent_at         TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_windows (
    window_key TEXT PRIMARY KEY,
    count      INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS opt_outs (
    recipient_email TEXT PRIMARY KEY,
    reason          TEXT NOT NULL DEFAULT 'manual',
    added_at        TEXT NOT NULL DEFAULT (datetime('now')),
    added_by        TEXT NOT NULL DEFAULT 'user'
);

CREATE TABLE IF NOT EXISTS chat_states (
    chat_id       INTEGER PRIMARY KEY,
    state         TEXT    NOT NULL,
    pending_email TEXT    NOT NULL,
    update_id     INTEGER NOT NULL,
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

async def _ensure_schema_migrations(db_conn: aiosqlite.Connection):
    async with db_conn.execute("PRAGMA table_info(requests)") as cur:
        cols = await cur.fetchall()
    existing_cols = {row[1] for row in cols}
    if "telegram_chat_id" not in existing_cols:
        await db_conn.execute("ALTER TABLE requests ADD COLUMN telegram_chat_id INTEGER")
    if "job_posting_text" not in existing_cols:
        await db_conn.execute("ALTER TABLE requests ADD COLUMN job_posting_text TEXT")
    await db_conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_req_chat_id ON requests (telegram_chat_id)"
    )


def _normalize_db_time(value):
    if isinstance(value, datetime):
        value = value.isoformat()
    if isinstance(value, str):
        return value.replace("T", " ")
    return value


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await _ensure_schema_migrations(db)
        await db.commit()

# ─── Checks ──────────────────────────────────────────────────────────────────

async def is_suppressed(email: str) -> Optional[dict]:
    """Returns suppression row dict or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT reason, added_at FROM opt_outs WHERE recipient_email = ?",
            (email.lower(),)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def is_duplicate(email: str) -> Optional[dict]:
    """Returns dedup row dict if within window, else None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT sent_at, expires_at FROM sent_dedup
               WHERE recipient_email = ? AND expires_at > datetime('now')""",
            (email.lower(),)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def check_rate_limits() -> dict:
    """Returns {"allowed": bool, "hour": int, "day": int}"""
    now = datetime.utcnow()
    hour_key = f"hour:{now.strftime('%Y%m%d%H')}"
    day_key  = f"day:{now.strftime('%Y%m%d')}"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async def get_count(key):
            async with db.execute(
                "SELECT count FROM rate_windows WHERE window_key = ? AND expires_at > datetime('now')",
                (key,)
            ) as cur:
                row = await cur.fetchone()
                return row["count"] if row else 0
        hour_count = await get_count(hour_key)
        day_count  = await get_count(day_key)
    return {
        "allowed": hour_count < MAX_PER_HOUR and day_count < MAX_PER_DAY,
        "hour": hour_count,
        "day":  day_count,
    }

async def is_telegram_update_seen(update_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM requests WHERE telegram_update_id = ?", (update_id,)
        ) as cur:
            return await cur.fetchone() is not None

# ─── Write operations ─────────────────────────────────────────────────────────

async def insert_request(
    telegram_update_id: int,
    telegram_chat_id: int,
    recipient_email: str,
    subject: str,
    body: str,
    cv_filename: str,
    job_posting_text: str = None,
    status: str = "pending_confirmation"
) -> str:
    request_id = f"req_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    deadline = (datetime.utcnow() + timedelta(hours=CONFIRM_TIMEOUT_H)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO requests
               (request_id, telegram_update_id, telegram_chat_id, recipient_email,
                generated_subject, generated_body, cv_filename, job_posting_text,
                status, confirmation_deadline)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (request_id, telegram_update_id, telegram_chat_id, recipient_email.lower(),
             subject, body, cv_filename, job_posting_text, status, deadline)
        )
        await db.commit()
    return request_id

async def confirm_request(request_id: str, scheduled_send_time: str):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE requests
               SET status='confirmed', scheduled_send_time=?, confirmed_at=?, updated_at=?
               WHERE request_id=? AND status='pending_confirmation'""",
            (_normalize_db_time(scheduled_send_time), now, now, request_id)
        )
        await db.commit()

async def cancel_request(request_id: str):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE requests SET status='cancelled', updated_at=? WHERE request_id=?",
            (now, request_id)
        )
        await db.commit()

async def set_request_status(request_id: str, status: str, **extra_fields):
    now = datetime.utcnow().isoformat()
    fields = {"status": status, "updated_at": now, **extra_fields}
    if "scheduled_send_time" in fields:
        fields["scheduled_send_time"] = _normalize_db_time(fields["scheduled_send_time"])
    set_clause = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [request_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE requests SET {set_clause} WHERE request_id=?", values
        )
        await db.commit()

async def get_pending_sends(limit: int = 5) -> list:
    """Returns confirmed requests whose scheduled_send_time has arrived."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM requests
               WHERE status = 'confirmed'
                 AND scheduled_send_time IS NOT NULL
                 AND datetime(replace(scheduled_send_time, 'T', ' ')) <= datetime('now')
               ORDER BY scheduled_send_time ASC
               LIMIT ?""",
            (limit,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

async def expire_old_confirmations() -> int:
    """Expires timed-out pending confirmations. Returns count of rows expired."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """UPDATE requests SET status='expired', updated_at=?
               WHERE status='pending_confirmation' AND confirmation_deadline < ?""",
            (now, now)
        )
        await db.commit()
        return cursor.rowcount

async def count_attempts(request_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM send_attempts WHERE request_id=?", (request_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def record_attempt(request_id: str, status: str, category: str = None,
                         smtp_response: str = None, error_detail: str = None):
    attempt_num = await count_attempts(request_id) + 1
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO send_attempts
               (request_id, attempt_number, status, failure_category, smtp_response, error_detail)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (request_id, attempt_num, status, category, smtp_response, error_detail)
        )
        await db.commit()

async def record_successful_send(request_id: str, recipient_email: str):
    now = datetime.utcnow()
    expires = (now + timedelta(days=DEDUP_DAYS)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO sent_dedup (recipient_email, request_id, sent_at, expires_at)
               VALUES (?, ?, ?, ?)""",
            (recipient_email.lower(), request_id, now.isoformat(), expires)
        )
        hour_key = f"hour:{now.strftime('%Y%m%d%H')}"
        day_key  = f"day:{now.strftime('%Y%m%d')}"
        for key, ttl_secs in [(hour_key, 7200), (day_key, 172800)]:
            await db.execute(
                """INSERT INTO rate_windows (window_key, count, expires_at)
                   VALUES (?, 1, datetime('now', '+' || ? || ' seconds'))
                   ON CONFLICT(window_key) DO UPDATE SET count = count + 1""",
                (key, ttl_secs)
            )
        await db.execute(
            "UPDATE requests SET status='sent', updated_at=? WHERE request_id=?",
            (now.isoformat(), request_id)
        )
        await db.commit()

async def add_to_opt_outs(email: str, reason: str = "manual", added_by: str = "user"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO opt_outs (recipient_email, reason, added_by)
               VALUES (?, ?, ?)""",
            (email.lower(), reason, added_by)
        )
        await db.commit()

async def get_last_request_for_chat(chat_id: int) -> Optional[dict]:
    """Find the most recent pending_confirmation request for a chat."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM requests
               WHERE status = 'pending_confirmation'
                 AND telegram_chat_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

# ─── Chat state (job posting flow) ───────────────────────────────────────────

async def get_chat_state(chat_id: int) -> Optional[dict]:
    """Returns the current intermediate state for a chat, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT state, pending_email, update_id FROM chat_states WHERE chat_id = ?",
            (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

async def set_chat_state(chat_id: int, state: str, pending_email: str, update_id: int):
    """Upserts the intermediate state for a chat."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO chat_states (chat_id, state, pending_email, update_id, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   state=excluded.state,
                   pending_email=excluded.pending_email,
                   update_id=excluded.update_id,
                   updated_at=excluded.updated_at""",
            (chat_id, state, pending_email.lower(), update_id, now)
        )
        await db.commit()

async def clear_chat_state(chat_id: int):
    """Removes any intermediate state for a chat."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM chat_states WHERE chat_id = ?", (chat_id,))
        await db.commit()
