# handlers.py
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import pytz
from telegram import Update
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

import db
import job_parser
from config import CV_PDF_PATH, SENDER_NAME, TELEGRAM_ALLOWED_CHAT_ID as ALLOWED_CHAT_ID
from notifier import notify_duplicate, notify_rate_limited, notify_suppressed, send_preview
from ollama_client import generate_email, generate_email_from_posting
from scheduler import RIYADH_TZ, compute_next_send_time, is_in_window

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')
STATUS_EMOJI = {
    'sent': '✅',
    'cancelled': '❌',
    'failed_permanent': '💀',
    'expired': '⏰',
    'confirmed': '⏳',
    'pending_confirmation': '👀',
    'suppressed': '🚫',
    'duplicate': '🔁',
    'failed_transient': '⚠️',
    'sending': '📤',
}


def esc(text: str) -> str:
    return escape_markdown(str(text), version=2)


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value.strip()))


def get_cv_filename() -> str:
    parts = SENDER_NAME.split()
    year = datetime.now().year
    first = parts[0] if parts else 'Applicant'
    last = parts[-1] if len(parts) > 1 else ''
    return f'{first}_{last}_CV_{year}.pdf'.replace('__', '_')


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    update_id = update.update_id

    if chat_id != ALLOWED_CHAT_ID:
        return

    text = (update.message.text or '').strip()

    state = await db.get_chat_state(chat_id)
    if state:
        if update_id == state['update_id']:
            if state['state'] == 'awaiting_company_name':
                await update.message.reply_text(
                    'Still waiting for the company name\\.\n'
                    'Send a name, or reply *SKIP* to continue without it\\.',
                    parse_mode='MarkdownV2',
                )
            else:
                await update.message.reply_text(
                    '📎 Still waiting for your job posting\\.\n'
                    'Send a URL, paste the text, or reply *SKIP*\\.',
                    parse_mode='MarkdownV2',
                )
            return
        if state['state'] == 'awaiting_company_name':
            await handle_company_name_input(update, context, state)
        else:
            await handle_job_posting_input(update, context, state)
        return

    if text.upper() in ('YES', 'Y', 'CONFIRM', 'SEND', '✅'):
        await handle_yes(update, context)
        return
    if text.upper() in ('NO', 'N', 'CANCEL', '❌'):
        await handle_no(update, context)
        return

    email = text.lower().strip()
    if not is_valid_email(email):
        await update.message.reply_text(
            '❓ Send me a recipient email address\\.\n'
            'Example: `hr@acme\\.com`',
            parse_mode='MarkdownV2',
        )
        return

    if await db.is_telegram_update_seen(update_id):
        logger.info('Duplicate Telegram update ignored: %s', update_id)
        return

    suppressed = await db.is_suppressed(email)
    if suppressed:
        await notify_suppressed(update, email, suppressed)
        return

    duplicate = await db.is_duplicate(email)
    if duplicate:
        await notify_duplicate(update, email, duplicate)
        return

    rate = await db.check_rate_limits()
    if not rate['allowed']:
        await notify_rate_limited(update, rate)
        return

    cv_path = Path(CV_PDF_PATH)
    if not cv_path.exists():
        await update.message.reply_text(
            f'⚠️ CV not found at configured path: `{esc(CV_PDF_PATH)}`\n'
            'Check CV\\_PDF\\_PATH in your \\.env',
            parse_mode='MarkdownV2',
        )
        return

    await db.set_chat_state(chat_id, 'awaiting_company_name', email, update_id)
    await update.message.reply_text(
        f'Got it: `{esc(email)}`\n\n'
        'Send the company or organization name, or reply *SKIP* to continue without it\\.',
        parse_mode='MarkdownV2',
    )


async def handle_company_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict):
    chat_id = update.effective_chat.id
    update_id = update.update_id
    text = (update.message.text or '').strip()
    email = state['pending_email']

    company_name = None if text.upper() in ('SKIP', 'S') else text

    await db.set_chat_state(chat_id, 'awaiting_job_posting', email, update_id, company_name=company_name)
    await update.message.reply_text(
        '📎 Do you have a job posting?\n\n'
        '• Send the *URL* and I\'ll fetch it\n'
        '• Paste the *job description* text directly\n'
        '• Reply *SKIP* for a generic email',
        parse_mode='MarkdownV2',
    )


async def handle_job_posting_input(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict):
    chat_id = update.effective_chat.id
    update_id = update.update_id
    text = (update.message.text or '').strip()
    email = state['pending_email']
    company_name = state.get('company_name')

    await db.clear_chat_state(chat_id)
    job_text = None

    if text.upper() in ('SKIP', 'S'):
        pass
    elif job_parser.is_url(text):
        await update.message.reply_text('🔗 Fetching job posting…', parse_mode='MarkdownV2')
        job_text = await job_parser.fetch_url(text)
        if not job_text:
            await update.message.reply_text(
                '⚠️ Could not fetch that URL\\. Generating generic email instead\\.',
                parse_mode='MarkdownV2',
            )
    else:
        job_text = text[:3000] if len(text) > 3000 else text

    await update.message.reply_text('⏳ Generating draft…', parse_mode='MarkdownV2')

    if job_text:
        draft = await generate_email_from_posting(email, job_text, company_name=company_name)
    else:
        draft = await generate_email(email, company_name=company_name)
    cv_filename = get_cv_filename()

    request_id = await db.insert_request(
        telegram_update_id=update_id,
        telegram_chat_id=chat_id,
        recipient_email=email,
        subject=draft['subject'],
        body=draft['body'],
        cv_filename=cv_filename,
        job_posting_text=job_text,
        company_name=company_name,
    )

    await send_preview(update, email, draft['subject'], draft['body'], cv_filename, request_id)


async def handle_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = await db.get_last_request_for_chat(update.effective_chat.id)
    if not row:
        await update.message.reply_text('❓ No pending request found to confirm\\.', parse_mode='MarkdownV2')
        return

    now_utc = datetime.now(timezone.utc)
    scheduled = compute_next_send_time(now_utc)
    await db.confirm_request(row['request_id'], scheduled.isoformat())

    sched_riyadh = scheduled.replace(tzinfo=pytz.utc).astimezone(RIYADH_TZ)
    if is_in_window(now_utc):
        msg = f"✅ Confirmed\\. Sending shortly to `{esc(row['recipient_email'])}`\\."
    else:
        day = esc(sched_riyadh.strftime('%A %d %b %Y at %H:%M'))
        msg = (
            f"✅ Confirmed\\. Scheduled for *{day}* \\(Riyadh\\)\n"
            f"To: `{esc(row['recipient_email'])}`"
        )

    await update.message.reply_text(msg, parse_mode='MarkdownV2')


async def handle_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = await db.get_last_request_for_chat(update.effective_chat.id)
    if not row:
        await update.message.reply_text('❓ No pending request found to cancel\\.', parse_mode='MarkdownV2')
        return
    await db.cancel_request(row['request_id'])
    await update.message.reply_text(
        f"❌ Cancelled\\. Email to `{esc(row['recipient_email'])}` will not be sent\\.",
        parse_mode='MarkdownV2',
    )


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await db.get_recent_requests(limit=10)
    if not rows:
        await update.message.reply_text('📊 No records yet\\.', parse_mode='MarkdownV2')
        return

    lines = ['📊 *Recent Requests \\(last 10\\)*\n']
    for row in rows:
        emoji = STATUS_EMOJI.get(row['status'], '❓')
        email = esc(row['recipient_email'])
        status = esc(row['status'])
        date = esc(str(row['created_at'])[:16])
        lines.append(f'{emoji} `{email}`\n   _{status}_ · {date}\n')

    await update.message.reply_text('\n'.join(lines), parse_mode='MarkdownV2')


async def handle_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    await handle_status(update, context)


async def handle_suppress_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    if not context.args or not is_valid_email(context.args[0]):
        await update.message.reply_text('Usage: `/suppress email@domain\\.com`', parse_mode='MarkdownV2')
        return

    email = context.args[0].lower()
    await db.add_to_opt_outs(email, reason='manual', added_by='user')
    await update.message.reply_text(f'⛔ Suppressed: `{esc(email)}`', parse_mode='MarkdownV2')
