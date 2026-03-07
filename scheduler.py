# scheduler.py
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytz

import db
from config import (
    CV_PDF_PATH as CV_PATH,
    MAX_RETRIES,
    RATE_LIMIT_REQUEUE_MINUTES,
    RETRY_BACKOFF_MINUTES,
    SEND_TIMEZONE,
    SEND_WINDOW_END,
    SEND_WINDOW_START,
    SENDER_EMAIL,
    SENDER_NAME,
)
from mailer import classify_smtp_error, send_email
from notifier import notify_expired_cleanup, notify_failure, notify_sent

logger = logging.getLogger(__name__)

RIYADH_TZ = pytz.timezone(SEND_TIMEZONE)
WINDOW_START = SEND_WINDOW_START
WINDOW_END = SEND_WINDOW_END
TICK_SECONDS = 30
RETRY_BACKOFF = RETRY_BACKOFF_MINUTES


def is_in_window(dt_utc: datetime) -> bool:
    dt_riyadh = dt_utc.astimezone(RIYADH_TZ)
    return WINDOW_START <= dt_riyadh.hour < WINDOW_END


def compute_next_send_time(from_dt_utc: datetime) -> datetime:
    dt_riyadh = from_dt_utc.astimezone(RIYADH_TZ)
    hour = dt_riyadh.hour

    if WINDOW_START <= hour < WINDOW_END:
        return from_dt_utc

    if hour < WINDOW_START:
        next_open = dt_riyadh.replace(hour=WINDOW_START, minute=0, second=0, microsecond=0)
    else:
        tomorrow = dt_riyadh.date() + timedelta(days=1)
        next_open = RIYADH_TZ.localize(datetime(tomorrow.year, tomorrow.month, tomorrow.day, WINDOW_START, 0, 0))

    return next_open.astimezone(timezone.utc).replace(tzinfo=None)


async def process_due_sends(app):
    rows = await db.get_pending_sends(limit=5)
    for row in rows:
        request_id = row['request_id']
        email = row['recipient_email']

        await db.set_request_status(request_id, db.STATUS_SENDING)

        suppressed = await db.is_suppressed(email)
        if suppressed:
            await db.set_request_status(request_id, db.STATUS_SUPPRESSED)
            logger.info('Suppressed at send time: %s', email)
            continue

        duplicate = await db.is_duplicate(email)
        if duplicate:
            await db.set_request_status(request_id, db.STATUS_DUPLICATE)
            logger.info('Duplicate at send time: %s -> %s', request_id, email)
            continue

        rate = await db.check_rate_limits()
        if not rate['allowed']:
            new_time = (datetime.utcnow() + timedelta(minutes=RATE_LIMIT_REQUEUE_MINUTES)).isoformat()
            await db.set_request_status(request_id, db.STATUS_CONFIRMED, scheduled_send_time=new_time)
            logger.info('Rate limit hit; rescheduled %s to %s', request_id, new_time)
            continue

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                functools.partial(
                    send_email,
                    recipient=email,
                    subject=row['generated_subject'],
                    body=row['generated_body'],
                    cv_path=CV_PATH,
                    cv_filename=row['cv_filename'],
                    sender_name=SENDER_NAME,
                    sender_email=SENDER_EMAIL,
                )
            )
            await db.record_attempt(request_id, 'success')
            await db.record_successful_send(request_id, email)
            await notify_sent(app.bot, row)
            logger.info('Sent successfully: %s -> %s', request_id, email)

        except Exception as exc:
            error_info = classify_smtp_error(exc)
            await db.record_attempt(
                request_id,
                status='failed',
                category=error_info['category'],
                error_detail=str(exc),
            )

            if error_info.get('suppress'):
                await db.add_to_opt_outs(email, reason=error_info['category'], added_by='system')

            if not error_info['retry']:
                await db.set_request_status(request_id, db.STATUS_FAILED_PERMANENT)
                await notify_failure(app.bot, row, error_info, final=True)
                logger.error('Permanent failure %s: %s', request_id, error_info['category'])
                continue

            attempts = await db.count_attempts(request_id)
            if attempts >= MAX_RETRIES:
                await db.set_request_status(request_id, db.STATUS_FAILED_PERMANENT)
                await notify_failure(app.bot, row, error_info, final=True)
                logger.error('Max retries reached for %s', request_id)
                continue

            backoff_m = RETRY_BACKOFF[min(attempts - 1, len(RETRY_BACKOFF) - 1)]
            reschedule = (datetime.utcnow() + timedelta(minutes=backoff_m)).isoformat()
            await db.set_request_status(request_id, db.STATUS_CONFIRMED, scheduled_send_time=reschedule)
            await notify_failure(app.bot, row, error_info, final=False, retry_in_minutes=backoff_m)
            logger.warning('Transient failure %s, retry in %sm (%s)', request_id, backoff_m, error_info['category'])


async def scheduler_loop(app):
    logger.info('Scheduler loop started')
    while True:
        try:
            expired_count = await db.expire_old_confirmations()
            await notify_expired_cleanup(app.bot, expired_count)
            await process_due_sends(app)
        except Exception as exc:
            logger.error('Scheduler tick error: %s', exc, exc_info=True)
        await asyncio.sleep(TICK_SECONDS)
