from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

try:
    import trafilatura  # type: ignore
except Exception:
    trafilatura = None

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:
    BeautifulSoup = None


class FetchedPage(BaseModel):
    url: str
    content_type: str = ""
    title: str = ""
    text: str = ""
    truncated: bool = False


_PRIVATE_HOST_PATTERNS = (
    "localhost",
    "127.",
    "10.",
    "192.168.",
    "169.254.",
)


def _is_probably_safe_url(url: str) -> bool:
    """
    Basic SSRF guardrails. Not perfect, but helps.
    - allow http(s)
    - block localhost/private IP-ish hostnames
    """
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https"):
            return False
        host = (u.hostname or "").lower()
        if not host:
            return False
        if host == "localhost" or host.endswith(".local"):
            return False
        if any(host.startswith(p) for p in _PRIVATE_HOST_PATTERNS):
            return False
        # block obvious credentials in URL
        if u.username or u.password:
            return False
        return True
    except Exception:
        return False


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    if not m:
        return ""
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    return title[:200]


def _extract_text_fallback(html: str) -> str:
    if BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = " ".join(t.get_text(" ", strip=True) for t in soup.find_all(["p", "li", "h1", "h2", "h3"]))
        text = re.sub(r"\s+", " ", text).strip()
        return text
    # ultra-basic fallback
    html = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"\s+", " ", html).strip()
    return html


async def fetch_page_text(url: str) -> FetchedPage:
    if not _is_probably_safe_url(url):
        return FetchedPage(url=url, content_type="", title="", text="")

    timeout = float(os.getenv("LOOKUP_TIMEOUT_SECONDS", "12"))
    max_chars = int(os.getenv("LOOKUP_MAX_PAGE_CHARS", "12000"))
    user_agent = os.getenv(
        "LOOKUP_USER_AGENT",
        "consensus-mvp/1.0 (+https://example.invalid; legal-research-assistant)",
    )

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"})
        r.raise_for_status()

        ct = (r.headers.get("content-type") or "").lower()
        # Don’t try to parse PDFs here (you can add pdf parsing later if needed)
        if "application/pdf" in ct:
            return FetchedPage(url=str(r.url), content_type=ct, title="", text="")

        html = r.text
        title = _extract_title(html)

        text = ""
        if trafilatura:
            downloaded = trafilatura.extract(html, include_comments=False, include_tables=False)
            text = (downloaded or "").strip()

        if not text:
            text = _extract_text_fallback(html)

        truncated = False
        if len(text) > max_chars:
            text = text[:max_chars]
            truncated = True

        return FetchedPage(url=str(r.url), content_type=ct, title=title, text=text, truncated=truncated)
