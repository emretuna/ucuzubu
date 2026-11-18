"""Live end-to-end smoke test without Telegram — the acceptance gate.

Usage:
    uv run python scripts/smoke_test.py [--no-cache] [--url URL]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ucuzu.sites  # noqa: E402,F401  (registers adapters)
from ucuzu.core import fetch, pipeline  # noqa: E402
from ucuzu.core.cache import Cache  # noqa: E402
from ucuzu.core.models import Offer  # noqa: E402

TEST_URL = (
    "https://www.hepsiburada.com/shimano-power-aero-14000-xsc-pg-3-5-devir-84cm-"
    "sarim-surf-makine-p-HBCV00008XOKU2"
)

SITE_NAMES = {
    "hepsiburada": "Hepsiburada",
    "trendyol": "Trendyol",
    "n11": "n11",
    "amazon": "Amazon.tr",
}


def fmt(p: float) -> str:
    return f"{p:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " ₺"


def row(o: Offer) -> str:
    extra = " (kuponlu)" if o.coupon_price else ""
    stock = "" if o.in_stock else " [stok yok]"
    return f"  {SITE_NAMES[o.site]:<12} {fmt(o.price):>14}{extra}{stock}  {o.title[:60]}"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--url", default=TEST_URL)
    args = ap.parse_args()

    cache = None
    if not args.no_cache:
        cache = Cache("ucuzu.db")
        await cache.init()

    t0 = time.monotonic()
    try:
        outcome = await pipeline.compare(args.url, cache)
    finally:
        elapsed = time.monotonic() - t0
    result = outcome.result

    print(f"\nOrigin: {result.origin.title}")
    print(f"  {SITE_NAMES[result.origin.site]} {fmt(result.origin.price)}  ({result.origin.url})")
    print(f"\nSite status: {result.site_status}")
    print("\nExact matches (price asc):")
    for o in result.exact:
        print(row(o))
    if result.similar:
        print("\nSimilar (benzer ürün):")
        for o in result.similar:
            print(row(o))
    if result.cheapest:
        print(f"\n🏆 Cheapest: {SITE_NAMES[result.cheapest.site]} {fmt(result.cheapest.price)}")
        print(f"   {result.cheapest.url}")
        print(f"   savings vs origin: {fmt(result.savings)}")
    else:
        print("\nNo in-stock exact match found.")

    hit = outcome.cache_hit
    print(f"\n[{'CACHE HIT' if hit else 'live'}] elapsed {elapsed:.2f}s")

    ok = True
    if args.url == TEST_URL and not args.no_cache:
        if not hit:
            print("WARN: expected comparison-cache hit on second run")
        if elapsed >= 1.0:
            print("FAIL: cached run took >= 1s")
            ok = False
    if args.url == TEST_URL and args.no_cache:
        # Identity expectations (prices drift; identities must hold).
        ids = {(o.site, o.product_id) for o in result.exact}
        checks = [
            ("n11 exact 83521276", ("n11", "83521276") in ids),
            ("amazon exact B0FL7ST13J", ("amazon", "B0FL7ST13J") in ids),
            (
                "trendyol exact 753753295/1233054890",
                ("trendyol", "753753295") in ids or ("trendyol", "1233054890") in ids,
            ),
            ("XTC 44264045 NOT exact", ("trendyol", "44264045") not in ids),
            ("cheapest is n11", result.cheapest is not None and result.cheapest.site == "n11"),
            ("all 4 sites searched", len(result.site_status) == 4),
        ]
        print("\nIdentity checks:")
        for name, passed in checks:
            print(f"  {'PASS' if passed else 'FAIL'}  {name}")
            ok = ok and passed

        # n11-origin scenario: origin has no barcode; TY identity must come
        # from cross-site corroboration, never self-confirmation.
        n11_url = next(
            (o.url for o in result.exact if o.site == "n11" and o.product_id == "83521276"),
            None,
        )
        if n11_url:
            n11_outcome = await pipeline.compare(n11_url, cache)
            n11_ids = {(o.site, o.product_id) for o in n11_outcome.result.exact}
            n11_checks = [
                ("n11-origin: HB exact", ("hepsiburada", "HBCV00008XOKU2") in n11_ids),
                (
                    "n11-origin: TY exact 753753295/1233054890",
                    ("trendyol", "753753295") in n11_ids or ("trendyol", "1233054890") in n11_ids,
                ),
                ("n11-origin: XTC NOT exact", ("trendyol", "44264045") not in n11_ids),
            ]
            print("\nn11-origin checks:")
            for name, passed in n11_checks:
                print(f"  {'PASS' if passed else 'FAIL'}  {name}")
                ok = ok and passed

    if cache is not None:
        await cache.close()
    await fetch.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))