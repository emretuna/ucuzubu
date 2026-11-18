"""URL extraction, shortener resolution, canonicalization."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from ucuzu.core import fetch

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_TRAILING_PUNCT = ".,;:!?)]}'\"»"

SHORTENER_HOSTS = {"app.hb.biz", "ty.gl", "amzn.to", "amzn.eu", "a.co"}
MAX_HOPS = 8


class UnsupportedLinkError(Exception):
    """No usable marketplace URL found in the message."""


def extract_url(text: str) -> str:
    m = _URL_RE.search(text)
    if not m:
        raise UnsupportedLinkError("no URL in text")
    return m.group(0).rstrip(_TRAILING_PUNCT)


def canonicalize(url: str) -> str:
    """Strip ALL query params and fragments (tracking, merchantId, boutiqueId…)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def is_known(url: str) -> bool:
    # Lazy import: ucuzu.sites' package init imports adapters that import this
    # module; a top-level import here would cycle when links is imported first.
    from ucuzu.sites.base import site_for_host

    return site_for_host(urlsplit(url).netloc) is not None


async def resolve(url: str) -> str:
    """Resolve a shared link to a canonical marketplace product URL.

    Known hosts pass through without fetching; shorteners and unknown hosts
    are followed hop-by-hop (mobile UA) up to MAX_HOPS.
    """
    for _hop in range(MAX_HOPS):
        if is_known(url):
            return canonicalize(url)
        result = await fetch.fetch(url, mobile=True, follow_redirects=False)
        location = None
        if 300 <= result.status < 400:
            location = result.headers.get("location") or result.headers.get("Location")
        if not location:
            raise UnsupportedLinkError(f"unsupported host: {urlsplit(url).netloc}")
        url = urljoin(url, location)
    raise UnsupportedLinkError(f"too many redirects: {url}")