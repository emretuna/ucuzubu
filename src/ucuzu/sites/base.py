"""Adapter protocol, registry, shared errors."""

from __future__ import annotations

import re
from typing import Protocol

from ucuzu.core.models import Offer, ProductRef


class ParseError(Exception):
    """Page markup did not contain the expected anchors."""


class SiteAdapter(Protocol):
    site: str

    def parse_product_url(self, url: str) -> ProductRef | None: ...

    async def fetch_product(self, ref: ProductRef) -> Offer: ...

    async def search(self, query: str) -> list[Offer]: ...

    def affiliate_url(self, offer: Offer) -> str: ...


ADAPTERS: dict[str, SiteAdapter] = {}

HOST_SITE: dict[str, str] = {
    "hepsiburada.com": "hepsiburada",
    "trendyol.com": "trendyol",
    "n11.com": "n11",
    "amazon.com.tr": "amazon",
}


def register(adapter: SiteAdapter) -> None:
    ADAPTERS[adapter.site] = adapter


def site_for_host(host: str) -> str | None:
    host = host.lower().split(":")[0]
    for domain, site in HOST_SITE.items():
        if host == domain or host.endswith("." + domain):
            return site
    return None


_PRICE_CLEAN_RE = re.compile(r"[\s\xa0₺]|TL", re.IGNORECASE)


def parse_price(value: str | float | int | None) -> float:
    """Parse a price to float. Handles tr-TR ('34.605,12 TL') and plain ('33163.24')."""
    if isinstance(value, (int, float)):
        return float(value)
    s = _PRICE_CLEAN_RE.sub("", value)
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)