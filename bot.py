# bot.py
import asyncio
import sys
import logging
import os
import atexit

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("outreach.log")
    ]
)
logging.getLogger("aiosqlite").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters

import db
from config import TELEGRAM_TOKEN
from handlers import (handle_message, handle_status_command,
                      handle_suppress_command)
from scheduler import scheduler_loop

# Module-level reference — prevents garbage collection of the scheduler task
_scheduler_task = None

# ─── Lock file: prevent two instances running at once ─────────────────────────
_LOCK_FILE = "bot.lock"
if os.path.exists(_LOCK_FILE):
    print("ERROR: bot.py is already running. Kill the existing process first.")
    print("Run:  Get-Process python | Stop-Process -Force")
    sys.exit(1)
open(_LOCK_FILE, "w").write(str(os.getpid()))
atexit.register(lambda: os.path.exists(_LOCK_FILE) and os.remove(_LOCK_FILE))


async def post_init(app):
    global _scheduler_task
    await db.init_db()
    logging.info("Database initialized")
    _scheduler_task = asyncio.ensure_future(scheduler_loop(app))
    logging.info("Scheduler loop started")


async def post_shutdown(app):
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    logging.info("Scheduler loop stopped")


def main():
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Plain text: email addresses, YES, NO
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Slash commands
    app.add_handler(CommandHandler("status",   handle_status_command))
    app.add_handler(CommandHandler("suppress", handle_suppress_command))

    logging.info("Bot started, polling Telegram...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()