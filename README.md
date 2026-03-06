<div align="center">

# 📧 OutreachBot

**A self-hosted Telegram bot that writes and sends professional cybersecurity internship/co-op application emails — powered by local AI. No cloud. No subscriptions. Your data never leaves your machine.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)](https://python.org)
[![Ollama](https://img.shields.io/badge/AI-Ollama%20Local-black?style=flat-square)](https://ollama.ai)
[![Telegram](https://img.shields.io/badge/Interface-Telegram%20Bot-2CA5E0?style=flat-square&logo=telegram)](https://core.telegram.org/bots)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

</div>

---

## What Is This?

OutreachBot turns the chaotic process of sending internship applications into a clean, controlled workflow.

You send an email address (and optionally a job posting) to your Telegram bot. The bot generates a professional, tailored application email using a local AI model — no OpenAI, no cloud APIs. You review the draft. You confirm. The bot sends it during business hours with your CV attached.

Everything is logged. Nothing gets sent without your approval. Ever.

---

## How It Works

```
You → Bot:  hr@company.com

Bot → You:  📋 Do you have a job posting? Send URL, paste text, or reply SKIP.

You → Bot:  [paste job requirements]

Bot → You:  📧 DRAFT PREVIEW
                 To: hr@company.com
                 Subject: SOC Co-op Application – Cybersecurity Student, CV Attached
                 ─────────────────────
                 [tailored 3-paragraph email based on the job posting]
                 ─────────────────────
                 Reply YES to send · NO to cancel

You → Bot:  YES

Bot → You:  ✅ Sent at 09:14 (Riyadh) · hr@company.com
```

That's the entire user experience.

---

## Features

### Core
- **Local AI generation** — uses Ollama (qwen2.5:7b) to write professional emails on your machine
- **Job posting input** — paste a URL or raw text; the bot generates a tailored email matching the actual requirements
- **Mandatory approval gate** — every email requires your explicit YES before it sends
- **Send window enforcement** — only sends between 08:00–18:00 in your timezone; confirmed emails outside the window queue automatically
- **CV attachment** — your existing PDF attached to every email, never modified

### Safety & Control
- **Deduplication** — won't send to the same address twice within a configurable window (default: 30 days)
- **Rate limiting** — configurable hourly and daily send caps to protect your sender reputation
- **Suppression list** — permanent blocklist; `/suppress email@domain.com` to add any address
- **Smart retry** — transient SMTP failures (timeouts, busy servers) are retried automatically with backoff
- **Permanent failure handling** — hard bounces and spam rejections auto-suppress the address and never retry
- **Idempotency** — duplicate Telegram updates never cause duplicate sends

### Developer-Friendly
- **Single process** — no Redis, no Celery, no Docker required; just `python bot.py`
- **One config file** — everything controlled from `.env`; fully annotated `.env.example` included
- **Full audit trail** — every request, attempt, and outcome logged to SQLite
- **Lock file** — prevents accidentally running two instances at once

---

## Quick Start

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | 3.13 recommended |
| Ollama | Latest | [ollama.ai](https://ollama.ai) |
| RAM | 6 GB free | For qwen2.5:7b model |
| Telegram account | — | To create a bot |
| Gmail account | — | For SMTP sending |

### 1. Clone the repository

```bash
git clone https://github.com/faxm1/outreach-bot.git
cd outreach-bot
```

### 2. Install dependencies

```bash
python -m venv venv

# Windows:
venv\Scripts\activate

# macOS / Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Install Ollama and pull the model

Download Ollama from [ollama.ai](https://ollama.ai), then:

```bash
ollama pull qwen2.5:7b
```

Verify it's ready:
```bash
ollama list
# Should show: qwen2.5:7b
```

### 4. Create your Telegram bot

1. Open Telegram → search **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the token (format: `123456789:ABCdef...`)
4. Get your chat ID: search **@userinfobot** → send `/start`

### 5. Get a Gmail App Password

1. Go to [myaccount.google.com](https://myaccount.google.com)
2. Security → 2-Step Verification → turn **ON**
3. Security → App Passwords → Generate
4. Copy the 16-character password

### 6. Configure `.env`

```bash
cp .env.example .env
```

Open `.env` and fill in your values. The mandatory fields:

```env
TELEGRAM_TOKEN=your_bot_token_here
TELEGRAM_ALLOWED_CHAT_ID=your_numeric_chat_id

SENDER_NAME=Your Full Name
SENDER_EMAIL=you@gmail.com

CV_PDF_PATH=C:/Users/YourName/Documents/YourCV.pdf

SMTP_USER=you@gmail.com
SMTP_PASS=xxxx xxxx xxxx xxxx
```

Every setting is explained in `.env.example`.

### 7. Run the bot

```bash
python bot.py
```

On first run the bot validates your config and tells you exactly what's wrong if anything is missing. Fix it and run again.

---

## Commands

| Command | What it does |
|---|---|
| Send any email address | Start a new application |
| `YES` | Confirm and schedule the draft for sending |
| `NO` | Cancel the draft |
| `SKIP` | Skip job posting input, generate a generic email |
| `/status` | Show last 10 requests and their outcomes |
| `/suppress email@domain.com` | Permanently block an address |

---

## Configuration Reference

All settings live in `.env`. Full documentation in `.env.example`.

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | — | Bot token from @BotFather |
| `TELEGRAM_ALLOWED_CHAT_ID` | — | Your Telegram user ID |
| `SENDER_NAME` | — | Your full name |
| `SENDER_EMAIL` | — | Your sending email address |
| `CV_PDF_PATH` | — | Absolute path to your CV PDF |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | — | SMTP username |
| `SMTP_PASS` | — | Gmail App Password |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama API URL |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Model to use |
| `DB_PATH` | `outreach.db` | SQLite database path |
| `SEND_TIMEZONE` | `Asia/Riyadh` | Your timezone |
| `SEND_WINDOW_START` | `8` | Send window start hour |
| `SEND_WINDOW_END` | `18` | Send window end hour |
| `MAX_EMAILS_PER_HOUR` | `10` | Hourly send cap |
| `MAX_EMAILS_PER_DAY` | `50` | Daily send cap |
| `DEDUP_WINDOW_DAYS` | `30` | Days before re-sending to same address |
| `CONFIRMATION_TIMEOUT_HOURS` | `12` | Hours before unconfirmed draft expires |
| `MAX_RETRIES` | `3` | Max retry attempts on transient failure |

---

## Project Structure

```
outreach-bot/
├── bot.py              # Entry point
├── config.py           # Central config loader
├── handlers.py         # Telegram message and command handlers
├── scheduler.py        # Send window + retry loop
├── db.py               # SQLite database layer
├── mailer.py           # SMTP sender
├── notifier.py         # Telegram notifications
├── ollama_client.py    # AI email generation
├── job_parser.py       # Job posting URL fetcher + text cleaner
├── requirements.txt
├── .env.example        # Fully annotated config reference
├── .gitignore
└── force_send_now.py   # Dev utility: bypass send window for testing
```

---

## Architecture

```
Telegram Poller ──────────────────────────────────────────────┐
  │                                                            │
  │  email received                                           │
  ▼                                                            │
validate → check suppression → check dedup → check rate limit  │
  │                                                            │
  ▼                                                            │
ask for job posting (URL / text / SKIP)                        │
  │                                                            │
  ▼                                                            │
Ollama generates tailored subject + body                       │
  │                                                            │
  ▼                                                            │
Telegram preview → wait for YES/NO                            │
  │                                                            │
  ▼                                                            │
compute_next_send_time() → store in SQLite                    │
  │                                                            │
Scheduler Loop (every 30s) ◄──────────────────────────────────┘
  │
  ▼
SMTP send + CV attachment
  │
  ├─ success → update DB → notify Telegram
  └─ failure → classify → retry / suppress / notify
```

Single Python process. No external queue. No separate workers.

---

## Security

- All credentials stored in `.env` only — never hardcoded
- `.env` is in `.gitignore` — can never be accidentally committed
- CV files are read locally and attached directly — never uploaded anywhere
- All AI inference runs locally via Ollama — no data leaves your machine
- Emails sent from your own SMTP account — bot never accesses your inbox
- SMTP passwords stored in environment only, never logged
- One allowed Telegram chat ID — only you can control the bot

---

## Troubleshooting

**Bot doesn't start**
```bash
# Check your .env has all required fields
python -c "import config"
```

**Ollama errors**
```bash
# Is Ollama running?
ollama list

# Is the model downloaded?
ollama pull qwen2.5:7b

# Test it directly
ollama run qwen2.5:7b "Reply with only: {\"test\": \"ok\"}"
```

**Gmail SMTP authentication fails**
- Make sure you're using an App Password, not your real Gmail password
- 2-Step Verification must be enabled before App Passwords are available

**Bot stops responding / duplicate instance error**
```powershell
# Windows: kill all Python processes and restart
Get-Process python | Stop-Process -Force
python bot.py
```

**Email stuck in confirmed state, not sending**
```bash
# Force send immediately (bypass send window, for testing)
python force_send_now.py
```

---

## Requirements File

```
python-telegram-bot==21.0.1
aiosqlite==0.20.0
pytz==2024.1
python-dotenv==1.0.1
httpx==0.27.0
beautifulsoup4==4.12.3
cryptography==42.0.5
PyPDF2==3.0.1
```

---

## Contributing

This project is designed to be self-hosted by individuals. If you find a bug or have a suggestion:

1. Open an issue with a clear description
2. Include your Python version, OS, and the full error traceback
3. Pull requests welcome — please test before submitting

---

## License

MIT License — use it, modify it, share it freely.

---

<div align="center">

Built by [Faisal Alhamad](https://github.com/faxm1) · Cybersecurity Student · Majmaah University

*If this helped your job search, give it a ⭐*

</div>
