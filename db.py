# db.py
import uuid
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite

from config import (
    CONFIRMATION_TIMEOUT_HOURS as CONFIRM_TIMEOUT_H,
    DB_PATH,
    DEDUP_WINDOW_DAYS as DEDUP_DAYS,
    MAX_EMAILS_PER_DAY as MAX_PER_DAY,
    MAX_EMAILS_PER_HOUR as MAX_PER_HOUR,
)

STATUS_PENDING = 'pending_confirmation'
STATUS_CONFIRMED = 'confirmed'
STATUS_SENDING = 'sending'
STATUS_SENT = 'sent'
STATUS_CANCELLED = 'cancelled'
STATUS_FAILED_PERMANENT = 'failed_permanent'
STATUS_EXPIRED = 'expired'
STATUS_SUPPRESSED = 'suppressed'
STATUS_DUPLICATE = 'duplicate'

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS requests (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id            TEXT    NOT NULL UNIQUE,
    telegram_update_id    INTEGER UNIQUE,
    telegram_chat_id      INTEGER,
    recipient_email       TEXT    NOT NULL,
    generated_subject     TEXT,
    generated_body        TEXT,
    cv_filename           TEXT,
    job_posting_text      TEXT,
    company_name          TEXT,
    status                TEXT    NOT NULL DEFAULT 'pending_confirmation',
    scheduled_send_time   TEXT,
    confirmation_deadline TEXT,
    confirmed_at          TEXT,
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_req_status ON requests (status);
CREATE INDEX IF NOT EXISTS idx_req_scheduled ON requests (scheduled_send_time);
CREATE INDEX IF NOT EXISTS idx_req_recipient ON requests (recipient_email);
CREATE INDEX IF NOT EXISTS idx_req_update_id ON requests (telegram_update_id);
CREATE INDEX IF NOT EXISTS idx_req_chat_id ON requests (telegram_chat_id);

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
    company_name  TEXT,
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def _normalize_db_time(value):
    if isinstance(value, datetime):
        value = value.isoformat()
    if isinstance(value, str):
        return value.replace('T', ' ')
    return value


async def _ensure_schema_migrations(db_conn: aiosqlite.Connection):
    async with db_conn.execute('PRAGMA table_info(requests)') as cur:
        cols = await cur.fetchall()
    existing_cols = {row[1] for row in cols}
    if 'telegram_chat_id' not in existing_cols:
        await db_conn.execute('ALTER TABLE requests ADD COLUMN telegram_chat_id INTEGER')
    if 'job_posting_text' not in existing_cols:
        await db_conn.execute('ALTER TABLE requests ADD COLUMN job_posting_text TEXT')
    if 'company_name' not in existing_cols:
        await db_conn.execute('ALTER TABLE requests ADD COLUMN company_name TEXT')
    await db_conn.execute('CREATE INDEX IF NOT EXISTS idx_req_chat_id ON requests (telegram_chat_id)')

    async with db_conn.execute('PRAGMA table_info(chat_states)') as cur:
        chat_cols = {row[1] for row in await cur.fetchall()}
    if 'company_name' not in chat_cols:
        await db_conn.execute('ALTER TABLE chat_states ADD COLUMN company_name TEXT')


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.executescript(SCHEMA)
        await _ensure_schema_migrations(db_conn)
        await db_conn.commit()


async def is_suppressed(email: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db_conn:
        db_conn.row_factory = aiosqlite.Row
        async with db_conn.execute(
            'SELECT reason, added_at FROM opt_outs WHERE recipient_email = ?',
            (email.lower(),),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def is_duplicate(email: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db_conn:
        db_conn.row_factory = aiosqlite.Row
        async with db_conn.execute(
            """SELECT sent_at, expires_at FROM sent_dedup
               WHERE recipient_email = ? AND expires_at > datetime('now')""",
            (email.lower(),),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def check_rate_limits() -> dict:
    now = datetime.utcnow()
    hour_key = f"hour:{now.strftime('%Y%m%d%H')}"
    day_key = f"day:{now.strftime('%Y%m%d')}"

    async with aiosqlite.connect(DB_PATH) as db_conn:
        db_conn.row_factory = aiosqlite.Row

        async def get_count(key: str) -> int:
            async with db_conn.execute(
                "SELECT count FROM rate_windows WHERE window_key = ? AND expires_at > datetime('now')",
                (key,),
            ) as cur:
                row = await cur.fetchone()
                return row['count'] if row else 0

        hour_count = await get_count(hour_key)
        day_count = await get_count(day_key)

    return {
        'allowed': hour_count < MAX_PER_HOUR and day_count < MAX_PER_DAY,
        'hour': hour_count,
        'day': day_count,
    }


async def is_telegram_update_seen(update_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db_conn:
        async with db_conn.execute('SELECT 1 FROM requests WHERE telegram_update_id = ?', (update_id,)) as cur:
            return await cur.fetchone() is not None


async def insert_request(
    telegram_update_id: int,
    telegram_chat_id: int,
    recipient_email: str,
    subject: str,
    body: str,
    cv_filename: str,
    job_posting_text: str = None,
    company_name: str = None,
    status: str = STATUS_PENDING,
) -> str:
    request_id = f"req_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    deadline = (datetime.utcnow() + timedelta(hours=CONFIRM_TIMEOUT_H)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.execute(
            """INSERT INTO requests
               (request_id, telegram_update_id, telegram_chat_id, recipient_email,
                generated_subject, generated_body, cv_filename, job_posting_text,
                company_name, status, confirmation_deadline)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request_id,
                telegram_update_id,
                telegram_chat_id,
                recipient_email.lower(),
                subject,
                body,
                cv_filename,
                job_posting_text,
                company_name,
                status,
                deadline,
            ),
        )
        await db_conn.commit()
    return request_id


async def confirm_request(request_id: str, scheduled_send_time: str):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.execute(
            """UPDATE requests
               SET status=?, scheduled_send_time=?, confirmed_at=?, updated_at=?
               WHERE request_id=? AND status=?""",
            (STATUS_CONFIRMED, _normalize_db_time(scheduled_send_time), now, now, request_id, STATUS_PENDING),
        )
        await db_conn.commit()


async def cancel_request(request_id: str):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.execute(
            'UPDATE requests SET status=?, updated_at=? WHERE request_id=?',
            (STATUS_CANCELLED, now, request_id),
        )
        await db_conn.commit()


async def set_request_status(request_id: str, status: str, **extra_fields):
    now = datetime.utcnow().isoformat()
    fields = {'status': status, 'updated_at': now, **extra_fields}
    if 'scheduled_send_time' in fields:
        fields['scheduled_send_time'] = _normalize_db_time(fields['scheduled_send_time'])
    set_clause = ', '.join(f"{key}=?" for key in fields)
    values = list(fields.values()) + [request_id]
    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.execute(f'UPDATE requests SET {set_clause} WHERE request_id=?', values)
        await db_conn.commit()


async def get_pending_sends(limit: int = 5) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db_conn:
        db_conn.row_factory = aiosqlite.Row
        async with db_conn.execute(
            """SELECT * FROM requests
               WHERE status = ?
                 AND scheduled_send_time IS NOT NULL
                 AND datetime(replace(scheduled_send_time, 'T', ' ')) <= datetime('now')
               ORDER BY scheduled_send_time ASC
               LIMIT ?""",
            (STATUS_CONFIRMED, limit),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def expire_old_confirmations() -> int:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db_conn:
        cursor = await db_conn.execute(
            """UPDATE requests SET status=?, updated_at=?
               WHERE status=? AND confirmation_deadline < ?""",
            (STATUS_EXPIRED, now, STATUS_PENDING, now),
        )
        await db_conn.commit()
        return cursor.rowcount


async def count_attempts(request_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db_conn:
        async with db_conn.execute('SELECT COUNT(*) FROM send_attempts WHERE request_id=?', (request_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def record_attempt(request_id: str, status: str, category: str = None, smtp_response: str = None, error_detail: str = None):
    attempt_num = await count_attempts(request_id) + 1
    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.execute(
            """INSERT INTO send_attempts
               (request_id, attempt_number, status, failure_category, smtp_response, error_detail)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (request_id, attempt_num, status, category, smtp_response, error_detail),
        )
        await db_conn.commit()


async def record_successful_send(request_id: str, recipient_email: str):
    now = datetime.utcnow()
    expires = (now + timedelta(days=DEDUP_DAYS)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.execute(
            """INSERT OR REPLACE INTO sent_dedup (recipient_email, request_id, sent_at, expires_at)
               VALUES (?, ?, ?, ?)""",
            (recipient_email.lower(), request_id, now.isoformat(), expires),
        )

        hour_key = f"hour:{now.strftime('%Y%m%d%H')}"
        day_key = f"day:{now.strftime('%Y%m%d')}"
        for key, ttl_secs in [(hour_key, 7200), (day_key, 172800)]:
            await db_conn.execute(
                """INSERT INTO rate_windows (window_key, count, expires_at)
                   VALUES (?, 1, datetime('now', '+' || ? || ' seconds'))
                   ON CONFLICT(window_key) DO UPDATE SET count = count + 1""",
                (key, ttl_secs),
            )

        await db_conn.execute(
            'UPDATE requests SET status=?, updated_at=? WHERE request_id=?',
            (STATUS_SENT, now.isoformat(), request_id),
        )
        await db_conn.commit()


async def add_to_opt_outs(email: str, reason: str = 'manual', added_by: str = 'user'):
    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.execute(
            """INSERT OR IGNORE INTO opt_outs (recipient_email, reason, added_by)
               VALUES (?, ?, ?)""",
            (email.lower(), reason, added_by),
        )
        await db_conn.commit()


async def get_last_request_for_chat(chat_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db_conn:
        db_conn.row_factory = aiosqlite.Row
        async with db_conn.execute(
            """SELECT * FROM requests
               WHERE status = ? AND telegram_chat_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (STATUS_PENDING, chat_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_recent_requests(limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db_conn:
        db_conn.row_factory = aiosqlite.Row
        async with db_conn.execute(
            "SELECT recipient_email, status, created_at FROM requests ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_chat_state(chat_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db_conn:
        db_conn.row_factory = aiosqlite.Row
        async with db_conn.execute(
            'SELECT state, pending_email, update_id, company_name FROM chat_states WHERE chat_id = ?',
            (chat_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def set_chat_state(chat_id: int, state: str, pending_email: str, update_id: int, company_name: str = None):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.execute(
            """INSERT INTO chat_states (chat_id, state, pending_email, update_id, company_name, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   state=excluded.state,
                   pending_email=excluded.pending_email,
                   update_id=excluded.update_id,
                   company_name=excluded.company_name,
                   updated_at=excluded.updated_at""",
            (chat_id, state, pending_email.lower(), update_id, company_name, now),
        )
        await db_conn.commit()


async def clear_chat_state(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.execute('DELETE FROM chat_states WHERE chat_id = ?', (chat_id,))
        await db_conn.commit()
