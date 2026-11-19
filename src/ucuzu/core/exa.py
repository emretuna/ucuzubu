"""Exa web-search fallback: find product pages the site searches missed.

Query is one pipeline step, so the Exa call always carries an ``objective``.
Results are best-effort candidates only: the pipeline re-verifies them through
the site adapters and the bot labels the survivors as unverified alternatives.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from ucuzu.core import links
from ucuzu.core.config import settings
from ucuzu.sites.base import parse_price, site_for_host

logger = logging.getLogger(__name__)

ALLOWED_DOMAINS = ("hepsiburada.com", "trendyol.com", "n11.com", "amazon.com.tr")
MAX_CANDIDATES = 6

# TR prices in highlights: '2.959,95 TL', '20.857 TL'. A dot-thousands token
# without decimals must be normalized before parse_price, which only treats
# dots as separators when a comma is present ('20.857' would parse as 20.857).
_PRICE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?\s*(?:TL|₺)", re.IGNORECASE)
_THOUSANDS_INT_RE = re.compile(r"\d{1,3}(?:\.\d{3})+")
# Installment noise ('9 x 1.474 TL', '12 × 2.959,95 TL') is recognized by its
# multiplier prefix, not by magnitude.
_INSTALLMENT_PREFIX_RE = re.compile(r"\d+\s*[x×]\s*$", re.IGNORECASE)

_OBJECTIVE = (
    "Find product detail pages selling this exact product on Turkish marketplaces: {query}. "
    "Rank pages for the exact brand and model first; similar models may follow. Exclude "
    "category, search, brand-list, blog and bestseller pages. Extract the listed selling "
    "price in TRY when the page shows one."
)


@dataclass(frozen=True)
class ExaCandidate:
    url: str  # links.canonicalize applied (strips merchantId etc.)
    title: str  # result title, else first highlight heading, else ""
    price: float | None  # best-effort from highlights


def _heading(highlights: list[str]) -> str:
    """First markdown heading line across highlights ('# <title>')."""
    for highlight in highlights:
        for line in highlight.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("# "):
                return line[2:].strip()
            break
    return ""


def _price_from_highlights(highlights: list[str]) -> float | None:
    for highlight in highlights:
        for m in _PRICE_RE.finditer(highlight):
            if _INSTALLMENT_PREFIX_RE.search(highlight[: m.start()]):
                continue
            matched = m.group(0)
            token = m.group(1)
            raw = matched
            if "," not in matched and _THOUSANDS_INT_RE.fullmatch(token):
                raw = token.replace(".", "")
            try:
                value = parse_price(raw)
            except ValueError:
                continue
            if value > 0:
                return value
    return None


def _candidate(item) -> ExaCandidate | None:
    url = getattr(item, "url", None)
    if not url:
        return None
    canonical = links.canonicalize(url)
    if site_for_host(urlsplit(canonical).netloc) is None:
        return None  # amazon.com (global) and off-list hosts land here

    highlights = [h for h in (getattr(item, "highlights", None) or []) if isinstance(h, str)]
    title = (getattr(item, "title", None) or "").strip() or _heading(highlights)
    return ExaCandidate(url=canonical, title=title, price=_price_from_highlights(highlights))


async def search_alternatives(query: str) -> list[ExaCandidate]:
    """Web-search the four TR marketplaces; never raises, [] on any failure."""
    api_key = settings.exa_api_key.strip()
    if not api_key:
        return []
    try:
        from exa_py import Exa

        client = Exa(api_key=api_key)
        result = await asyncio.wait_for(
            asyncio.to_thread(
                client.search,
                query,
                type="auto",
                num_results=20,
                include_domains=list(ALLOWED_DOMAINS),
                contents={"highlights": True},
                objective=_OBJECTIVE.format(query=query),
            ),
            timeout=45,
        )
        candidates: dict[str, ExaCandidate] = {}
        for item in result.results:
            cand = _candidate(item)
            if cand is not None and cand.url not in candidates:
                candidates[cand.url] = cand
            if len(candidates) >= MAX_CANDIDATES:
                break
        return list(candidates.values())
    except Exception as exc:  # timeout, HTTP, SDK, malformed payload
        logger.warning("exa fallback search failed: %s", exc)
        return []