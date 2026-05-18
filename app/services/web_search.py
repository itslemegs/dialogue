import os
import random
from typing import List, Optional

import anyio
import httpx
from pydantic import BaseModel


class SearchResult(BaseModel):
    title: str = ""
    url: str
    snippet: str = ""
    provider: str = "searxng"
    rank: int = 0


class WebSearchClient:
    def __init__(self):
        self.base_url = os.getenv("SEARXNG_URL", "http://localhost:8080").rstrip("/")
        self.timeout = float(os.getenv("LOOKUP_TIMEOUT_SECONDS", "12"))
        self.user_agent = os.getenv("LOOKUP_USER_AGENT", "consensus-mvp/1.0")

    async def search(
        self,
        query: str,
        *,
        count: int = 5,
        pageno: int = 1,
        categories: str = "general",
        time_range: Optional[str] = None,   # day|month|year
        language: Optional[str] = None,
    ) -> List[SearchResult]:
        params = {
            "q": query,
            "format": "json",
            "categories": categories,
            "pageno": str(max(1, pageno)),
        }
        if time_range:
            params["time_range"] = time_range
        if language:
            params["language"] = language

        # Retries because SearxNG (or its engines) may close connections abruptly.
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                timeout = httpx.Timeout(self.timeout, read=max(self.timeout * 2, 20.0))
                headers = {
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                    # Common fix for intermittent protocol/read errors:
                    "Connection": "close",
                    # Avoid some odd gzip/chunk edge cases:
                    "Accept-Encoding": "identity",
                }

                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
                    r = await client.get(f"{self.base_url}/search", params=params)
                    r.raise_for_status()

                    # If JSON isn't enabled, this will throw -> we handle below.
                    data = r.json()

                items = data.get("results") or []
                out: List[SearchResult] = []
                for i, it in enumerate(items):
                    url = (it.get("url") or "").strip()
                    if not url:
                        continue
                    out.append(
                        SearchResult(
                            title=(it.get("title") or "").strip(),
                            url=url,
                            snippet=(it.get("content") or it.get("snippet") or "").strip(),
                            provider="searxng",
                            rank=i + 1,
                        )
                    )
                    if len(out) >= count:
                        break
                return out

            except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError, httpx.TimeoutException) as e:
                last_err = e
                # small exponential backoff
                await anyio.sleep(0.2 * (2**attempt) + random.random() * 0.1)
                continue
            except (httpx.HTTPStatusError, ValueError) as e:
                # HTTPStatusError: got 4xx/5xx
                # ValueError: JSON decode failed (JSON not enabled or broken response)
                last_err = e
                break

        # Don’t crash the whole endpoint; return empty results.
        # Optional: replace with logger.warning(...)
        print(f"[searxng] search failed: {type(last_err).__name__}: {last_err}")
        return []