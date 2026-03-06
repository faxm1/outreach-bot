# mailer.py
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path

from config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS

logger = logging.getLogger(__name__)

def validate_cv(cv_path: str) -> Path:
    p = Path(cv_path)
    if not p.exists():
        raise FileNotFoundError(f"CV not found: {cv_path}")
    if p.suffix.lower() != ".pdf":
        raise ValueError(f"CV must be a PDF, got: {p.suffix}")
    size_mb = p.stat().st_size / (1024 * 1024)
    if size_mb > 5:
        raise ValueError(f"CV is {size_mb:.1f} MB, must be ≤ 5 MB")
    return p

def send_email(
    recipient: str,
    subject: str,
    body: str,
    cv_path: str,
    cv_filename: str,
    sender_name: str,
    sender_email: str
) -> bool:
    cv = validate_cv(cv_path)

    msg = MIMEMultipart()
    msg["From"]    = f"{sender_name} <{sender_email}>"
    msg["To"]      = recipient
    msg["Subject"] = subject
    msg["X-Mailer"] = "OutreachBot/1.0"

    msg.attach(MIMEText(body, "plain", "utf-8"))

    with open(cv, "rb") as f:
        part = MIMEApplication(f.read(), Name=cv_filename)
        part["Content-Disposition"] = f'attachment; filename="{cv_filename}"'
        msg.attach(part)

    # Log minimal info — no SMTP password, no body content
    logger.info(f"Sending email to {recipient} via {SMTP_HOST}:{SMTP_PORT}")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(sender_email, recipient, msg.as_string())

    logger.info(f"Email delivered to {recipient}")
    return True


def classify_smtp_error(exception: Exception) -> dict:
    msg = str(exception).lower()
    code = None
    if hasattr(exception, "smtp_code"):
        code = exception.smtp_code
    elif hasattr(exception, "args") and exception.args:
        try:
            code = int(str(exception.args[0])[:3])
        except (ValueError, TypeError):
            pass

    if code in (550, 551, 553, 554) or "no such user" in msg or "user unknown" in msg:
        return {"category": "hard_bounce",        "retry": False, "suppress": True}
    if code == 552 or "mailbox full" in msg:
        return {"category": "mailbox_full",       "retry": True,  "suppress": False}
    if "spam" in msg or "policy violation" in msg or "blocked" in msg:
        return {"category": "spam_rejection",     "retry": False, "suppress": True}
    if code == 535 or "authentication" in msg or "credentials" in msg:
        return {"category": "auth_failure",       "retry": False, "suppress": False}
    if "blacklist" in msg or "dnsbl" in msg or "rbl" in msg:
        return {"category": "sender_blacklisted", "retry": False, "suppress": False}
    if code in (421, 450, 451) or "try again" in msg or "temporarily" in msg:
        return {"category": "server_busy",        "retry": True,  "suppress": False}
    if "timeout" in msg or "timed out" in msg:
        return {"category": "connection_timeout", "retry": True,  "suppress": False}
    if "connection refused" in msg or "refused" in msg:
        return {"category": "connection_refused", "retry": True,  "suppress": False}
    return {"category": "unknown_error",          "retry": True,  "suppress": False}


# ─── SMTP Provider Notes ──────────────────────────────────────────────────────
# Gmail:
#   SMTP_HOST=smtp.gmail.com, SMTP_PORT=587
#   SMTP_PASS = 16-char App Password (not your account password)
#   Requires: Google Account → Security → 2FA on → App Passwords → generate one
#   Limit: ~500 emails/day
#
# Outlook / Hotmail:
#   SMTP_HOST=smtp-mail.outlook.com, SMTP_PORT=587
#   SMTP_PASS = your account password (if Basic Auth enabled) or App Password
#   Note: Microsoft increasingly requires OAuth2. Gmail is more reliable for SMTP.
#
# Custom SMTP / Postfix self-hosted:
#   SMTP_HOST=127.0.0.1, SMTP_PORT=25 (or 587)
#   No TLS needed for localhost; remove starttls() call