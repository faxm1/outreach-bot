# bot.py
import asyncio
import atexit
import logging
import os
import sys

import psutil

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('outreach.log'),
    ],
)
logging.getLogger('aiosqlite').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

import db
import ollama_client
from config import TELEGRAM_TOKEN
from handlers import handle_message, handle_status_command, handle_suppress_command
from ollama_client import load_cv_text
from scheduler import scheduler_loop

_scheduler_task = None
_LOCK_FILE = 'bot.lock'


def _pid_is_running(pid: int) -> bool:
    return psutil.pid_exists(pid)


def _acquire_lock() -> None:
    if os.path.exists(_LOCK_FILE):
        try:
            with open(_LOCK_FILE, 'r', encoding='utf-8') as f:
                existing_pid = int(f.read().strip())
        except Exception:
            existing_pid = None

        if existing_pid and _pid_is_running(existing_pid):
            print('ERROR: bot.py is already running. Kill the existing process first.')
            sys.exit(1)

        try:
            os.remove(_LOCK_FILE)
        except OSError:
            print('ERROR: stale bot.lock exists and could not be removed.')
            sys.exit(1)

    with open(_LOCK_FILE, 'w', encoding='utf-8') as fh:
        fh.write(str(os.getpid()))


def _cleanup_lock() -> None:
    if os.path.exists(_LOCK_FILE):
        try:
            os.remove(_LOCK_FILE)
        except OSError:
            pass


async def post_init(app):
    global _scheduler_task
    await db.init_db()
    logging.info('Database initialized')

    ollama_client.CV_TEXT = load_cv_text()
    if ollama_client.CV_TEXT:
        logging.info('CV loaded and ready')
    else:
        logging.warning('CV could not be loaded — job-posting personalization may fall back')

    _scheduler_task = asyncio.create_task(scheduler_loop(app))
    logging.info('Scheduler loop started')


async def post_shutdown(app):
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    logging.info('Scheduler loop stopped')


def main():
    _acquire_lock()
    atexit.register(_cleanup_lock)

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CommandHandler('status', handle_status_command))
    app.add_handler(CommandHandler('suppress', handle_suppress_command))

    logging.info('Bot started, polling Telegram...')
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
