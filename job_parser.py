# job_parser.py
import re
import logging

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r'^https?://', re.IGNORECASE)


def is_url(text: str) -> bool:
    """Returns True if text looks like an http/https URL."""
    return bool(_URL_RE.match(text.strip()))


async def fetch_url(url: str) -> str | None:
    """
    Fetches a job posting URL and returns clean plain text, or None on failure.
    Caps output at 3000 characters after cleaning.
    """
    try:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; OutreachBot/1.0)"}
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return clean_text(resp.text)
    except Exception as e:
        logger.warning(f"fetch_url failed for {url}: {e}")
        return None


def clean_text(html: str) -> str:
    """
    Strips HTML noise and returns plain text capped at 3000 characters.
    Removes script, style, nav, footer, header, aside, noscript tags before extraction.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:3000]
