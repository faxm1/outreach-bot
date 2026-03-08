# mailer.py
import logging
import smtplib
import socket
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from config import MAX_CV_SIZE_MB, SMTP_HOST, SMTP_PASS, SMTP_PORT, SMTP_USE_AUTH, SMTP_USE_TLS, SMTP_USER

logger = logging.getLogger(__name__)


def validate_cv(cv_path: str) -> Path:
    path = Path(cv_path)
    if not path.exists():
        raise FileNotFoundError(f'CV not found: {cv_path}')
    if path.suffix.lower() != '.pdf':
        raise ValueError(f'CV must be a PDF, got: {path.suffix}')
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_CV_SIZE_MB:
        raise ValueError(f'CV is {size_mb:.1f} MB, must be ≤ {MAX_CV_SIZE_MB} MB')
    return path


def send_email(
    recipient: str,
    subject: str,
    body: str,
    cv_path: str,
    cv_filename: str,
    sender_name: str,
    sender_email: str,
) -> bool:
    cv = validate_cv(cv_path)

    msg = MIMEMultipart()
    msg['From'] = f'{sender_name} <{sender_email}>'
    msg['To'] = recipient
    msg['Subject'] = subject
    msg['X-Mailer'] = 'OutreachBot/1.0'
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    with open(cv, 'rb') as fh:
        part = MIMEApplication(fh.read(), Name=cv_filename)
        part['Content-Disposition'] = f'attachment; filename="{cv_filename}"'
        msg.attach(part)

    logger.info('Sending email to %s via %s:%s', recipient, SMTP_HOST, SMTP_PORT)

    if SMTP_USE_AUTH and not SMTP_USE_TLS:
        logger.warning('SMTP_USE_AUTH is True but SMTP_USE_TLS is False — credentials will be sent in plaintext')

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.ehlo()
        if SMTP_USE_TLS:
            server.starttls()
            server.ehlo()
        if SMTP_USE_AUTH:
            server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(sender_email, recipient, msg.as_string())

    logger.info('Email delivered to %s', recipient)
    return True


def classify_smtp_error(exception: Exception) -> dict:
    msg = str(exception).lower()
    code = None
    if hasattr(exception, 'smtp_code'):
        code = exception.smtp_code
    elif hasattr(exception, 'args') and exception.args:
        try:
            code = int(str(exception.args[0])[:3])
        except (ValueError, TypeError):
            pass

    if code in (550, 551, 553, 554) or 'no such user' in msg or 'user unknown' in msg:
        return {'category': 'hard_bounce', 'retry': False, 'suppress': True}
    if code == 552 or 'mailbox full' in msg:
        return {'category': 'mailbox_full', 'retry': True, 'suppress': False}
    if 'spam' in msg or 'policy violation' in msg or 'blocked' in msg:
        return {'category': 'spam_rejection', 'retry': False, 'suppress': True}
    if code == 535 or 'authentication' in msg or 'credentials' in msg:
        return {'category': 'auth_failure', 'retry': False, 'suppress': False}
    if 'blacklist' in msg or 'dnsbl' in msg or 'rbl' in msg:
        return {'category': 'sender_blacklisted', 'retry': False, 'suppress': False}
    if code in (421, 450, 451) or 'try again' in msg or 'temporarily' in msg:
        return {'category': 'server_busy', 'retry': True, 'suppress': False}
    if 'timeout' in msg or 'timed out' in msg:
        return {'category': 'connection_timeout', 'retry': True, 'suppress': False}
    if isinstance(exception, ssl.SSLError) or 'ssl' in msg or 'tls' in msg:
        return {'category': 'tls_error', 'retry': True, 'suppress': False}
    if isinstance(exception, socket.gaierror) or 'name or service not known' in msg or 'getaddrinfo' in msg:
        return {'category': 'dns_error', 'retry': True, 'suppress': False}
    if 'network is unreachable' in msg:
        return {'category': 'network_unreachable', 'retry': True, 'suppress': False}
    if 'connection refused' in msg or 'refused' in msg:
        return {'category': 'connection_refused', 'retry': True, 'suppress': False}
    return {'category': 'unknown_error', 'retry': True, 'suppress': False}
