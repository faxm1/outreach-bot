# job_parser.py
import ipaddress
import logging
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r'^https?://', re.IGNORECASE)


def is_url(text: str) -> bool:
    return bool(_URL_RE.match(text.strip()))


def _is_safe_public_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {'http', 'https'}:
            return False
        host = (parsed.hostname or '').strip().lower()
        if not host or host in {'localhost', '127.0.0.1', '::1'}:
            return False
        try:
            ip = ipaddress.ip_address(host)
            # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:192.168.1.1)
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            pass
        return True
    except Exception:
        return False


async def fetch_url(url: str) -> str | None:
    if not _is_safe_public_url(url):
        logger.warning('Rejected non-public or unsafe URL: %s', url)
        return None

    try:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; OutreachBot/1.0)'},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get('content-type', '').lower()
            if 'text/html' not in content_type and 'application/xhtml+xml' not in content_type:
                logger.warning('Rejected non-HTML URL: %s (%s)', url, content_type)
                return None
            return clean_text(response.text)
    except Exception as exc:
        logger.warning('fetch_url failed for %s: %s', url, exc)
        return None


def clean_text(html: str) -> str | None:
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript', 'svg']):
        tag.decompose()
    text = soup.get_text(separator=' ', strip=True)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) < 120:
        return None
    return text[:3000]
