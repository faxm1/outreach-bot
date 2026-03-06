# notifier.py
import logging
import pytz
from datetime import datetime

from telegram.helpers import escape_markdown

from config import TELEGRAM_ALLOWED_CHAT_ID, MAX_EMAILS_PER_HOUR, MAX_EMAILS_PER_DAY, SEND_TIMEZONE

logger = logging.getLogger(__name__)
RIYADH_TZ = pytz.timezone(SEND_TIMEZONE)


def _riyadh_time(iso_utc: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_utc).replace(tzinfo=pytz.utc)
        return dt.astimezone(RIYADH_TZ).strftime("%d %b %Y %H:%M")
    except Exception:
        return iso_utc


async def send_preview(update, email, subject, body, cv_filename, request_id):
    safe_email       = escape_markdown(email,        version=1)
    safe_subject     = escape_markdown(subject,      version=1)
    safe_body        = escape_markdown(body,         version=1)
    safe_filename    = escape_markdown(cv_filename,  version=1)
    safe_request_id  = escape_markdown(request_id,   version=1)

    msg = (
        f"📧 *DRAFT PREVIEW*\n\n"
        f"*To:* `{safe_email}`\n"
        f"*Subject:* {safe_subject}\n"
        f"*Attachment:* 📎 {safe_filename}\n\n"
        f"─────────────────────\n"
        f"{safe_body}\n"
        f"─────────────────────\n\n"
        f"Reply *YES* to send · *NO* to cancel\n"
        f"_(Expires in 12 hours)_\n"
        f"ID: `{safe_request_id}`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def notify_sent(bot, row: dict):
    try:
        safe_email      = escape_markdown(row["recipient_email"],    version=1)
        safe_subject    = escape_markdown(row["generated_subject"],  version=1)
        safe_request_id = escape_markdown(row["request_id"],         version=1)
        safe_time       = escape_markdown(_riyadh_time(row.get("updated_at", "")), version=1)

        msg = (
            f"✅ *EMAIL SENT*\n\n"
            f"*To:* `{safe_email}`\n"
            f"*Subject:* {safe_subject}\n"
            f"*Sent:* {safe_time} (Riyadh)\n"
            f"*ID:* `{safe_request_id}`"
        )
        await bot.send_message(
            chat_id=TELEGRAM_ALLOWED_CHAT_ID,
            text=msg,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to send success notification: {e}")


async def notify_failure(bot, row: dict, error_info: dict,
                         final: bool = True, retry_in_minutes: int = None):
    try:
        safe_email      = escape_markdown(row["recipient_email"], version=1)
        safe_category   = escape_markdown(error_info.get("category", "unknown"), version=1)
        safe_detail     = escape_markdown(str(error_info.get("detail", "See logs")), version=1)
        safe_request_id = escape_markdown(row["request_id"], version=1)

        emoji = "💀" if final else "⚠️"
        heading = "EMAIL FAILED" if final else "TRANSIENT FAILURE"

        if not final and retry_in_minutes:
            retry_line = f"\n*Next retry in:* {retry_in_minutes} minutes"
        else:
            retry_line = "\n_No further retries._"

        suppress_line = "\n_Address added to suppression list._" if error_info.get("suppress") else ""

        msg = (
            f"{emoji} *{heading}*\n\n"
            f"*To:* `{safe_email}`\n"
            f"*Category:* `{safe_category}`\n"
            f"*Error:* {safe_detail}\n"
            f"*ID:* `{safe_request_id}`"
            f"{retry_line}{suppress_line}"
        )
        await bot.send_message(
            chat_id=TELEGRAM_ALLOWED_CHAT_ID,
            text=msg,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to send failure notification: {e}")


async def notify_suppressed(update, email: str, row: dict):
    safe_email  = escape_markdown(email,            version=1)
    safe_reason = escape_markdown(row["reason"],    version=1)
    safe_date   = escape_markdown(row["added_at"][:10], version=1)

    await update.message.reply_text(
        f"🚫 *SUPPRESSED*\n\n"
        f"`{safe_email}` is on the suppression list.\n"
        f"*Reason:* {safe_reason}\n"
        f"*Added:* {safe_date}\n\n"
        f"Not sent.",
        parse_mode="Markdown"
    )


async def notify_duplicate(update, email: str, row: dict):
    safe_email   = escape_markdown(email,                          version=1)
    safe_sent    = escape_markdown(_riyadh_time(row["sent_at"]),   version=1)
    safe_expires = escape_markdown(row["expires_at"][:10],         version=1)

    await update.message.reply_text(
        f"🔁 *DUPLICATE*\n\n"
        f"`{safe_email}` was already contacted.\n"
        f"*Last sent:* {safe_sent} (Riyadh)\n"
        f"*Dedup window expires:* {safe_expires}\n\n"
        f"Not sent.",
        parse_mode="Markdown"
    )


async def notify_rate_limited(update, rate: dict):
    max_hour = str(MAX_EMAILS_PER_HOUR)
    max_day  = str(MAX_EMAILS_PER_DAY)

    await update.message.reply_text(
        f"⏸ *RATE LIMITED*\n\n"
        f"*Sent this hour:* {rate['hour']} / {max_hour}\n"
        f"*Sent today:* {rate['day']} / {max_day}\n\n"
        f"Try again after the current window resets.",
        parse_mode="Markdown"
    )


async def notify_expired_cleanup(bot, expired_count: int):
    if expired_count == 0:
        return
    try:
        msg = (
            f"⏰ *{expired_count} request{'s' if expired_count > 1 else ''} expired*\n\n"
            f"No confirmation was received within the timeout window.\n"
            f"Use /status to review."
        )
        await bot.send_message(
            chat_id=TELEGRAM_ALLOWED_CHAT_ID,
            text=msg,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to send expiry notification: {e}")