"""Per-site egress diagnostic — run inside the deployed machine (railway ssh).

Prints the RAW outcome per site (status, final URL, anchor presence, exception
type) so a failure can be classified: BlockedError → IP reputation (proxy),
ParseError → page variant served to this IP, FetchError → network/timeout.

Usage: uv run --no-sync python scripts/diagnose_sites.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ucuzu.sites  # noqa: E402,F401
from ucuzu.core import fetch  # noqa: E402
from ucuzu.core.config import settings  # noqa: E402
from ucuzu.sites.base import ADAPTERS  # noqa: E402

QUERY = "Shimano Power Aero 14000 XSC PG"

SEARCH_URLS = {
    "hepsiburada": "https://www.hepsiburada.com/ara?q=Shimano+Power+Aero+14000+XSC+PG",
    "trendyol": "https://www.trendyol.com/sr?q=Shimano+Power+Aero+14000+XSC+PG",
    "n11": "https://www.n11.com/arama?q=Shimano+Power+Aero+14000+XSC+PG",
    "amazon": "https://www.amazon.com.tr/s?k=Shimano+Power+Aero+14000+XSC+PG",
}

ANCHORS = {
    "hepsiburada": "reduxStore",
    "trendyol": "__single-search-result__PROPS",
    "n11": "application/ld+json",
    "amazon": "data-asin",
}


async def probe_raw(site: str) -> None:
    url = SEARCH_URLS[site]
    try:
        r = await fetch.fetch(url)
        anchor = ANCHORS[site] in r.text
        print(f"[{site}] raw: HTTP {r.status} len={len(r.text)} anchor={anchor} final={r.url[:90]}")
        if r.status != 200 or not anchor:
            print(f"[{site}] body head: {r.text[:200]!r}")
    except Exception as exc:
        print(f"[{site}] raw fetch raised {type(exc).__name__}: {exc}")


async def probe_adapter(site: str) -> None:
    try:
        offers = await ADAPTERS[site].search(QUERY)
        print(f"[{site}] adapter.search: OK, {len(offers)} offers")
    except Exception as exc:
        print(f"[{site}] adapter.search raised {type(exc).__name__}: {exc}")


async def main() -> None:
    print(f"proxy configured: {bool(settings.fetch_proxy_url.strip())}")
    for site in SEARCH_URLS:
        await probe_raw(site)
        await probe_adapter(site)
    await fetch.close()


if __name__ == "__main__":
    asyncio.run(main())