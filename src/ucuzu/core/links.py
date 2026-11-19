"""URL extraction, shortener resolution, canonicalization."""

from __future__ import annotations

import html as html_lib
import re
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from ucuzu.core import fetch

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_TRAILING_PUNCT = ".,;:!?)]}'\"»"

MAX_HOPS = 8


# JS redirect interstitials (amzlinks.in behind link.amazon, Adjust fallback pages).
_JS_REDIRECT_RE = re.compile(
    r"""(?:window\.)?location(?:\.href)?\s*(?:=\s*|\.replace\(\s*|\.assign\(\s*)["']([^"']+)["']""",
    re.IGNORECASE,
)
_META_REFRESH_RE = re.compile(
    r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]+url=([^"'>\s]+)""",
    re.IGNORECASE,
)


def _interstitial_target(url: str, body: str) -> str | None:
    """Extract a redirect target from a non-3xx interstitial page, or None."""
    if urlsplit(url).netloc.lower().endswith(".adj.st"):
        params = parse_qs(urlsplit(url).query)
        fallback = (params.get("adj_fallback") or [None])[0]
        if fallback and fallback.startswith(("http://", "https://")):
            return fallback
    match = _JS_REDIRECT_RE.search(body) or _META_REFRESH_RE.search(body)
    if match:
        return html_lib.unescape(match.group(1))
    return None


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
    path = parts.path
    host = parts.netloc.lower()
    if host == "amazon.com.tr" or host.endswith(".amazon.com.tr"):
        # Amazon app-share links append a /ref=<tracking> path segment.
        path = re.sub(r"/ref=[^/]+/?$", "", path)
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


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
        # Adjust embeds the real target in the hop URL itself (adj_fallback);
        # extract it without fetching the tracker.
        location = _interstitial_target(url, "")
        if location is None:
            result = await fetch.fetch(url, mobile=True, follow_redirects=False)
            if 300 <= result.status < 400:
                location = result.headers.get("location") or result.headers.get("Location")
            if location is None and urlsplit(url).netloc.lower().endswith(".adj.st"):
                # Adjust serves mobile UAs a 200 app-store page; desktop gets the 302.
                retry = await fetch.fetch(url, mobile=False, follow_redirects=False)
                if 300 <= retry.status < 400:
                    location = retry.headers.get("location") or retry.headers.get("Location")
                result = retry
            if location is None:
                location = _interstitial_target(url, result.text)
        if not location:
            raise UnsupportedLinkError(f"unsupported host: {urlsplit(url).netloc}")
        url = urljoin(url, location)
    raise UnsupportedLinkError(f"too many redirects: {url}")