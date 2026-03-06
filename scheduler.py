# scheduler.py
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import pytz

import db
from config import (
    SEND_TIMEZONE, SEND_WINDOW_START, SEND_WINDOW_END,
    MAX_RETRIES, CV_PDF_PATH as CV_PATH,
    SENDER_NAME, SENDER_EMAIL,
)
from mailer import send_email, classify_smtp_error
from notifier import (notify_sent, notify_failure, notify_expired_cleanup)

logger = logging.getLogger(__name__)

RIYADH_TZ    = pytz.timezone(SEND_TIMEZONE)
WINDOW_START  = SEND_WINDOW_START
WINDOW_END    = SEND_WINDOW_END
TICK_SECONDS  = 30
RETRY_BACKOFF = [5, 30, 120]   # Minutes after each failure

def is_in_window(dt_utc: datetime) -> bool:
    dt_riyadh = dt_utc.astimezone(RIYADH_TZ)
    return WINDOW_START <= dt_riyadh.hour < WINDOW_END

def compute_next_send_time(from_dt_utc: datetime) -> datetime:
    """Returns the next valid send time in UTC."""
    dt_riyadh = from_dt_utc.astimezone(RIYADH_TZ)
    h = dt_riyadh.hour

    if WINDOW_START <= h < WINDOW_END:
        # Inside window → send now
        return from_dt_utc

    # Before window today
    if h < WINDOW_START:
        next_open = dt_riyadh.replace(hour=WINDOW_START, minute=0, second=0, microsecond=0)
    else:
        # After window → next day
        tomorrow = dt_riyadh.date() + timedelta(days=1)
        next_open = RIYADH_TZ.localize(
            datetime(tomorrow.year, tomorrow.month, tomorrow.day, WINDOW_START, 0, 0)
        )
    return next_open.astimezone(timezone.utc).replace(tzinfo=None)


async def process_due_sends(app):
    """Process confirmed requests that are due to send."""
    rows = await db.get_pending_sends(limit=5)
    for row in rows:
        request_id = row["request_id"]
        email      = row["recipient_email"]

        # Lock the row: prevent concurrent double-send
        await db.set_request_status(request_id, "sending")

        # Re-check opt-outs (may have been added after confirmation)
        suppressed = await db.is_suppressed(email)
        if suppressed:
            await db.set_request_status(request_id, "suppressed")
            logger.info(f"Suppressed at send time: {email}")
            continue

        # Re-check dedup right before send to avoid queued duplicates.
        duplicate = await db.is_duplicate(email)
        if duplicate:
            await db.set_request_status(request_id, "duplicate")
            logger.info(f"Duplicate at send time: {request_id} -> {email}")
            continue

        # Re-check rate limits
        rate = await db.check_rate_limits()
        if not rate["allowed"]:
            # Push back by 10 minutes
            new_time = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
            await db.set_request_status(request_id, "confirmed",
                                        scheduled_send_time=new_time)
            logger.info(f"Rate limit hit; rescheduled {request_id} to {new_time}")
            continue

        # Attempt send
        try:
            send_email(
                recipient=email,
                subject=row["generated_subject"],
                body=row["generated_body"],
                cv_path=CV_PATH,
                cv_filename=row["cv_filename"],
                sender_name=SENDER_NAME,
                sender_email=SENDER_EMAIL
            )
            # Success
            await db.record_attempt(request_id, "success")
            await db.record_successful_send(request_id, email)
            await notify_sent(app.bot, row)
            logger.info(f"Sent successfully: {request_id} → {email}")

        except Exception as exc:
            error_info = classify_smtp_error(exc)
            await db.record_attempt(
                request_id,
                status="failed",
                category=error_info["category"],
                error_detail=str(exc)
            )

            if error_info.get("suppress"):
                await db.add_to_opt_outs(email, reason=error_info["category"], added_by="system")

            if not error_info["retry"]:
                await db.set_request_status(request_id, "failed_permanent")
                await notify_failure(app.bot, row, error_info, final=True)
                logger.error(f"Permanent failure {request_id}: {error_info['category']}")
            else:
                attempts = await db.count_attempts(request_id)
                if attempts >= MAX_RETRIES:
                    await db.set_request_status(request_id, "failed_permanent")
                    await notify_failure(app.bot, row, error_info, final=True)
                else:
                    backoff_m = RETRY_BACKOFF[min(attempts - 1, len(RETRY_BACKOFF) - 1)]
                    reschedule = (datetime.utcnow() + timedelta(minutes=backoff_m)).isoformat()
                    await db.set_request_status(request_id, "confirmed",
                                                scheduled_send_time=reschedule)
                    await notify_failure(app.bot, row, error_info,
                                        final=False, retry_in_minutes=backoff_m)
                    logger.warning(f"Transient failure {request_id}, retry in {backoff_m}m")


async def scheduler_loop(app):
    """Main scheduler: runs every TICK_SECONDS forever."""
    logger.info("Scheduler loop started")
    while True:
        try:
            expired_count = await db.expire_old_confirmations()
            await notify_expired_cleanup(app.bot, expired_count)
            await process_due_sends(app)
        except Exception as e:
            logger.error(f"Scheduler tick error: {e}", exc_info=True)
        await asyncio.sleep(TICK_SECONDS)
