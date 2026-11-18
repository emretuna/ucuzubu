"""Shared scraping layer: curl_cffi Chrome impersonation, rate limits.

Plain TLS gets 403/503 on all four marketplaces; impersonate="chrome" is
load-bearing. One AsyncSession so cookies persist across requests.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import quote, urlsplit

from curl_cffi import requests as curl_requests

from ucuzu.core.config import settings

from ucuzu.core.models import FetchResult


class BlockedError(Exception):
    """Site returned 403/429/503 — anti-bot block, do not retry."""


class FetchError(Exception):
    """Network error / timeout after retry."""


DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)

_IMPERSONATES = ("chrome", "chrome124", "safari")
_sessions: dict[str, curl_requests.AsyncSession] = {}
_semaphore = asyncio.Semaphore(4)
_last_request: dict[str, float] = {}
_MIN_INTERVAL = 1.0


def _proxies() -> dict[str, str] | None:
    """Residential/datacenter proxy for anti-bot IP reputation (plan contingency).

    Accepted FETCH_PROXY_URL formats:
      http://user:pass@host:port   (canonical URL)
      user:pass@host:port          (scheme-less URL)
      user:pass:host:port          (provider panel format)
    Turkish residential exit strongly preferred; datacenter exits stay blocked."""
    raw = settings.fetch_proxy_url.strip()
    if not raw:
        return None
    if "://" in raw:
        url = raw
    elif "@" in raw:
        url = "http://" + raw
    else:
        parts = raw.split(":")
        if len(parts) != 4 or not all(parts):
            raise ValueError(
                "FETCH_PROXY_URL must be user:pass:host:port or [http://]user:pass@host:port"
            )
        user, password, host, port = parts
        url = f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
    return {"http": url, "https": url}


def _get_session(impersonate: str) -> curl_requests.AsyncSession:
    session = _sessions.get(impersonate)
    if session is None:
        session = curl_requests.AsyncSession(impersonate=impersonate, proxies=_proxies())
        _sessions[impersonate] = session
    return session


async def close() -> None:
    for session in _sessions.values():
        await session.close()
    _sessions.clear()


def _site_key(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    # group www/m subdomains per marketplace
    for known in ("hepsiburada.com", "trendyol.com", "n11.com", "amazon.com.tr"):
        if host == known or host.endswith("." + known):
            return known
    return host


async def _throttle(url: str) -> None:
    key = _site_key(url)
    while True:
        last = _last_request.get(key, 0.0)
        wait = _MIN_INTERVAL - (time.monotonic() - last)
        if wait <= 0:
            _last_request[key] = time.monotonic()
            return
        await asyncio.sleep(wait)


async def fetch(
    url: str,
    *,
    mobile: bool = False,
    timeout: float = 20.0,
    follow_redirects: bool = True,
) -> FetchResult:
    """Fetch a page. One retry on network error/timeout. On 403/429/503 rotate
    impersonate profile (fresh session/cookies); BlockedError when all fail."""
    headers = {"User-Agent": MOBILE_UA if mobile else DESKTOP_UA}
    async with _semaphore:
        blocked_status: int | None = None
        for impersonate in _IMPERSONATES:
            session = _get_session(impersonate)
            last_exc: Exception | None = None
            for _attempt in range(2):
                await _throttle(url)
                try:
                    resp = await session.get(
                        url,
                        headers=headers,
                        timeout=timeout,
                        allow_redirects=follow_redirects,
                    )
                except Exception as exc:  # curl_cffi errors, timeouts
                    last_exc = exc
                    continue
                if resp.status_code in (403, 429, 503):
                    blocked_status = resp.status_code
                    break  # rotate impersonate profile
                history = [str(r.url) for r in (resp.history or [])]
                return FetchResult(
                    status=resp.status_code,
                    url=str(resp.url),
                    text=resp.text,
                    history=history,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                )
            else:
                raise FetchError(f"network error for {url}: {last_exc}")
        raise BlockedError(f"{blocked_status} for {url} (all impersonates)")