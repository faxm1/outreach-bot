# handlers.py
import logging
import re
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

import db
import job_parser
from config import TELEGRAM_ALLOWED_CHAT_ID as ALLOWED_CHAT_ID, CV_PDF_PATH, SENDER_NAME, DB_PATH
from ollama_client import generate_email, generate_email_from_posting
from scheduler import compute_next_send_time
from notifier import (send_preview, notify_suppressed,
                      notify_duplicate, notify_rate_limited)

logger = logging.getLogger(__name__)


def esc(text: str) -> str:
    """Escape any string for safe use in a MarkdownV2 message."""
    return escape_markdown(str(text), version=2)


# ─── Email validation ─────────────────────────────────────────────────────────

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

def is_valid_email(s: str) -> bool:
    return bool(EMAIL_RE.match(s.strip()))


# ─── CV attachment filename ───────────────────────────────────────────────────

def get_cv_filename() -> str:
    parts = SENDER_NAME.split()
    year  = datetime.now().year
    first = parts[0]  if parts else "Applicant"
    last  = parts[-1] if len(parts) > 1 else ""
    return f"{first}_{last}_CV_{year}.pdf".replace("__", "_")


# ─── Main text message handler ────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id   = update.effective_chat.id
    update_id = update.update_id

    if chat_id != ALLOWED_CHAT_ID:
        return

    text = (update.message.text or "").strip()

    # 1. Job posting state check — must come before YES/NO so any input is
    #    treated as a posting response while we're waiting for one.
    state = await db.get_chat_state(chat_id)
    if state:
        if update_id == state["update_id"]:
            # Re-delivery of the original email message — re-prompt instead of processing
            await update.message.reply_text(
                "📎 Still waiting for your job posting\\.\n"
                "Send a URL, paste the text, or reply *SKIP*\\.",
                parse_mode="MarkdownV2"
            )
            return
        await handle_job_posting_input(update, context, state)
        return

    # 2. YES / NO confirmations
    if text.upper() in ("YES", "Y", "CONFIRM", "SEND", "✅"):
        await handle_yes(update, context)
        return
    if text.upper() in ("NO", "N", "CANCEL", "❌"):
        await handle_no(update, context)
        return

    # 3. Treat as new recipient email
    email = text.lower().strip()

    if not is_valid_email(email):
        await update.message.reply_text(
            "❓ Send me a recipient email address\\.\nExample: `hr@acme\\.com`",
            parse_mode="MarkdownV2"
        )
        return

    # Idempotency: ignore duplicate Telegram update
    if await db.is_telegram_update_seen(update_id):
        logger.info(f"Duplicate Telegram update ignored: {update_id}")
        return

    # Suppression check
    suppressed = await db.is_suppressed(email)
    if suppressed:
        await notify_suppressed(update, email, suppressed)
        return

    # Dedup check
    duplicate = await db.is_duplicate(email)
    if duplicate:
        await notify_duplicate(update, email, duplicate)
        return

    # Rate limit check
    rate = await db.check_rate_limits()
    if not rate["allowed"]:
        await notify_rate_limited(update, rate)
        return

    # CV sanity check
    cv_path = Path(CV_PDF_PATH)
    if not cv_path.exists():
        await update.message.reply_text(
            f"⚠️ CV not found at configured path: `{esc(CV_PDF_PATH)}`\n"
            "Check CV\\_PDF\\_PATH in your \\.env",
            parse_mode="MarkdownV2"
        )
        return

    # All checks passed — store state and ask for job posting
    await db.set_chat_state(chat_id, "awaiting_job_posting", email, update_id)
    await update.message.reply_text(
        f"Got it: `{esc(email)}`\n\n"
        "📎 Do you have a job posting?\n\n"
        "• Send the *URL* and I'll fetch it\n"
        "• Paste the *job description* text directly\n"
        "• Reply *SKIP* for a generic SOC email",
        parse_mode="MarkdownV2"
    )


# ─── Job posting input handler ────────────────────────────────────────────────

async def handle_job_posting_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: dict,
):
    chat_id   = update.effective_chat.id
    update_id = update.update_id
    text      = (update.message.text or "").strip()
    email     = state["pending_email"]

    # Clear state immediately — any failure below still leaves the user unblocked
    await db.clear_chat_state(chat_id)

    job_text = None

    if text.upper() in ("SKIP", "S"):
        pass  # job_text stays None → generic email

    elif job_parser.is_url(text):
        await update.message.reply_text("🔗 Fetching job posting…", parse_mode="MarkdownV2")
        job_text = await job_parser.fetch_url(text)
        if not job_text:
            await update.message.reply_text(
                "⚠️ Could not fetch that URL\\. Generating generic email instead\\.",
                parse_mode="MarkdownV2"
            )

    else:
        job_text = text  # pasted text — cap at 3000 chars
        if len(job_text) > 3000:
            job_text = job_text[:3000]

    await update.message.reply_text("⏳ Generating draft…", parse_mode="MarkdownV2")

    if job_text:
        draft = await generate_email_from_posting(email, job_text)
    else:
        draft = await generate_email(email)

    cv_filename = get_cv_filename()

    request_id = await db.insert_request(
        telegram_update_id=update_id,
        telegram_chat_id=chat_id,
        recipient_email=email,
        subject=draft["subject"],
        body=draft["body"],
        cv_filename=cv_filename,
        job_posting_text=job_text,
    )

    await send_preview(update, email, draft["subject"], draft["body"], cv_filename, request_id)


# ─── YES handler ──────────────────────────────────────────────────────────────

async def handle_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = await db.get_last_request_for_chat(update.effective_chat.id)
    if not row:
        await update.message.reply_text("❓ No pending request found to confirm\\.", parse_mode="MarkdownV2")
        return

    import pytz
    from scheduler import is_in_window, RIYADH_TZ

    now_utc   = datetime.utcnow()
    scheduled = compute_next_send_time(now_utc)
    await db.confirm_request(row["request_id"], scheduled.isoformat())

    sched_riyadh = scheduled.replace(tzinfo=pytz.utc).astimezone(RIYADH_TZ)

    if is_in_window(now_utc.replace(tzinfo=pytz.utc)):
        msg = f"✅ Confirmed\\. Sending shortly to `{esc(row['recipient_email'])}`\\."
    else:
        day = esc(sched_riyadh.strftime('%A %d %b %Y at %H:%M'))
        msg = (
            f"✅ Confirmed\\. Scheduled for *{day}* \\(Riyadh\\)\n"
            f"To: `{esc(row['recipient_email'])}`"
        )
    await update.message.reply_text(msg, parse_mode="MarkdownV2")


# ─── NO handler ───────────────────────────────────────────────────────────────

async def handle_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = await db.get_last_request_for_chat(update.effective_chat.id)
    if not row:
        await update.message.reply_text("❓ No pending request found to cancel\\.", parse_mode="MarkdownV2")
        return
    await db.cancel_request(row["request_id"])
    await update.message.reply_text(
        f"❌ Cancelled\\. Email to `{esc(row['recipient_email'])}` will not be sent\\.",
        parse_mode="MarkdownV2"
    )


# ─── /status command ──────────────────────────────────────────────────────────

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db_conn:
        db_conn.row_factory = aiosqlite.Row
        async with db_conn.execute(
            """SELECT recipient_email, status, created_at
               FROM requests ORDER BY created_at DESC LIMIT 10"""
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await update.message.reply_text("📊 No records yet\\.", parse_mode="MarkdownV2")
        return

    STATUS_EMOJI = {
        "sent":                 "✅",
        "cancelled":            "❌",
        "failed_permanent":     "💀",
        "expired":              "⏰",
        "confirmed":            "⏳",
        "pending_confirmation": "👀",
        "suppressed":           "🚫",
        "duplicate":            "🔁",
        "failed_transient":     "⚠️",
        "sending":              "📤",
    }

    lines = ["📊 *Recent Requests \\(last 10\\)*\n"]
    for r in rows:
        emoji  = STATUS_EMOJI.get(r["status"], "❓")
        email  = esc(r["recipient_email"])
        status = esc(r["status"])
        date   = esc(r["created_at"][:16])
        lines.append(f"{emoji} `{email}`\n   _{status}_ · {date}\n")

    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def handle_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for CommandHandler('/status')."""
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    await handle_status(update, context)


# ─── /suppress command ────────────────────────────────────────────────────────

async def handle_suppress_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for CommandHandler('/suppress')."""
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    if not context.args or not is_valid_email(context.args[0]):
        await update.message.reply_text(
            "Usage: `/suppress email@domain\\.com`",
            parse_mode="MarkdownV2"
        )
        return
    email = context.args[0].lower()
    await db.add_to_opt_outs(email, reason="manual", added_by="user")
    await update.message.reply_text(
        f"⛔ Suppressed: `{esc(email)}`",
        parse_mode="MarkdownV2"
    )
