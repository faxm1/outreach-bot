<div align="center">

# 📧 OutreachBot

**A self-hosted Telegram bot that drafts and sends professional internship and job application emails using a local AI model. No cloud AI APIs. No subscriptions. Draft generation happens locally, while delivery uses your configured SMTP provider.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)](https://python.org)
[![Ollama](https://img.shields.io/badge/AI-Ollama%20Local-black?style=flat-square)](https://ollama.ai)
[![Telegram](https://img.shields.io/badge/Interface-Telegram%20Bot-2CA5E0?style=flat-square&logo=telegram)](https://core.telegram.org/bots)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

</div>

---

## What Is This?

OutreachBot turns the chaotic process of sending internship and job applications into a clean, controlled workflow.

You send an email address, and optionally a job posting URL or pasted text, to your Telegram bot. The bot generates a professional draft using a local Ollama model, shows you the preview, waits for your approval, and only then sends the email during your configured send window with your CV attached.

Everything is logged. Nothing gets sent without your approval.

---

## How It Works

```text
You → Bot:  hr@company.com

Bot → You:  📎 Do you have a job posting? Send URL, paste text, or reply SKIP.

You → Bot:  [paste job requirements]

Bot → You:  📧 DRAFT PREVIEW
                 To: hr@company.com
                 Subject: Internship / Job Application – CV Attached
                 ─────────────────────
                 [tailored email draft]
                 ─────────────────────
                 Reply YES to send · NO to cancel

You → Bot:  YES

Bot → You:  ✅ Sent at 09:14 (Riyadh) · hr@company.com
```

---

## Features

### Core
- **Local AI generation** — uses Ollama on your machine to generate email drafts
- **Job posting input** — accepts a URL or pasted text and tailors the draft when posting text is available
- **Mandatory approval gate** — every email requires explicit confirmation before sending
- **Send window enforcement** — sends only during your configured hours; confirmed drafts outside the window are queued automatically
- **CV attachment** — attaches your existing PDF without modifying it

### Safety & Control
- **Deduplication** — avoids re-sending to the same address within a configurable window
- **Rate limiting** — configurable hourly and daily send caps
- **Suppression list** — permanently blocks addresses via `/suppress`
- **Smart retry** — transient SMTP failures retry automatically with backoff
- **Permanent failure handling** — hard bounces and spam-type rejections are not retried
- **Idempotency** — duplicate Telegram updates do not trigger duplicate sends

### Developer-Friendly
- **Single process** — no Redis, no Celery, no Docker required
- **Environment-driven config** — behavior controlled from `.env`
- **SQLite audit trail** — requests, attempts, and outcomes are logged locally
- **Basic lock file** — prevents accidental double-start in normal usage

---

## Quick Start

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | 3.13 recommended |
| Ollama | Latest | [ollama.ai](https://ollama.ai) |
| RAM | Depends on model | Larger models need more memory |
| Telegram account | — | To create a bot |
| SMTP account | — | Gmail or another SMTP provider |

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

```bash
ollama pull huihui_ai/deepseek-r1-abliterated:14b
```

Verify it is available:

```bash
ollama list
```

### 4. Create your Telegram bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the bot token
4. Get your personal chat ID from **@userinfobot**

### 5. Configure `.env`

```bash
cp .env.example .env
```

Required fields:

```env
TELEGRAM_TOKEN=your_bot_token_here
TELEGRAM_ALLOWED_CHAT_ID=your_numeric_chat_id
SENDER_NAME=Your Full Name
SENDER_EMAIL=you@example.com
CV_PDF_PATH=C:/Users/YourName/Documents/YourCV.pdf
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@example.com
SMTP_PASS=your_app_password
```

### 6. Run the bot

```bash
python bot.py
```

---

## Commands

| Command | What it does |
|---|---|
| Send any email address | Start a new application |
| `YES` | Confirm and schedule the draft for sending |
| `NO` | Cancel the draft |
| `SKIP` | Skip job posting input and generate a generic email |
| `/status` | Show recent requests and outcomes |
| `/suppress email@domain.com` | Permanently block an address |

---

## Configuration Reference

Key variables in `.env`:

| Variable | Default | Description |
|---|---|---|
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP server port |
| `SMTP_USE_TLS` | `true` | Enable STARTTLS |
| `SMTP_USE_AUTH` | `true` | Enable SMTP login |
| `OLLAMA_MODEL` | `huihui_ai/deepseek-r1-abliterated:14b` | Default local model |
| `SEND_TIMEZONE` | `Asia/Riyadh` | Timezone for send window |
| `SEND_WINDOW_START` | `8` | Send window start hour |
| `SEND_WINDOW_END` | `18` | Send window end hour |
| `MAX_EMAILS_PER_HOUR` | `10` | Hourly send cap |
| `MAX_EMAILS_PER_DAY` | `50` | Daily send cap |
| `DEDUP_WINDOW_DAYS` | `30` | Dedup retention in days |
| `CONFIRMATION_TIMEOUT_HOURS` | `12` | Confirmation expiry |
| `MAX_RETRIES` | `3` | Maximum transient retries |
| `RETRY_BACKOFF_MINUTES` | `5,30,120` | Retry delays in minutes |
| `MAX_CV_SIZE_MB` | `5` | Maximum allowed CV file size |

---

## Notes

- Draft generation is local when using Ollama.
- Email delivery still goes through your configured SMTP provider.
- If you supply a public job posting URL, the bot fetches that page to extract text.
- Store secrets in `.env`, and never commit your real `.env` file.
