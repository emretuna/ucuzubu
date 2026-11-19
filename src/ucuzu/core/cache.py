"""SQLite cache (aiosqlite) for link resolution, products, searches, comparisons.

One shared connection per process, WAL mode, no background jobs. Expired rows
are treated as misses and lazily deleted on read.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

import aiosqlite

from ucuzu.core.models import ComparisonResult, Offer

LINK_TTL = 30 * 24 * 3600  # 30 days
PRODUCT_TTL = 6 * 3600  # 6 hours
SEARCH_TTL = 1 * 3600  # 1 hour
COMPARISON_TTL = 30 * 60  # 30 minutes

_SCHEMA = """
CREATE TABLE IF NOT EXISTS link_cache (
    url TEXT PRIMARY KEY,
    canonical_url TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS product_cache (
    site TEXT NOT NULL,
    product_id TEXT NOT NULL,
    offer_json TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (site, product_id)
);
CREATE TABLE IF NOT EXISTS search_cache (
    site TEXT NOT NULL,
    query TEXT NOT NULL,
    offers_json TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (site, query)
);
CREATE TABLE IF NOT EXISTS comparison_cache (
    source_canonical_url TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def offer_to_dict(offer: Offer) -> dict[str, Any]:
    return asdict(offer)


def offer_from_dict(data: dict[str, Any]) -> Offer:
    return Offer(
        site=data["site"],
        title=data["title"],
        price=data["price"],
        list_price=data.get("list_price"),
        url=data["url"],
        product_id=data["product_id"],
        variant_id=data.get("variant_id"),
        merchant=data.get("merchant"),
        in_stock=data["in_stock"],
        coupon_price=data["coupon_price"],
        barcode=data.get("barcode"),
        brand=data.get("brand"),
    )


def result_to_dict(result: ComparisonResult) -> dict[str, Any]:
    """Serialize a ComparisonResult (nested Offers become dicts via asdict)."""
    return asdict(result)


def result_from_dict(data: dict[str, Any]) -> ComparisonResult:
    cheapest = data.get("cheapest")
    return ComparisonResult(
        origin=offer_from_dict(data["origin"]),
        offers=[offer_from_dict(o) for o in data["offers"]],
        exact=[offer_from_dict(o) for o in data["exact"]],
        similar=[offer_from_dict(o) for o in data["similar"]],
        site_status=dict(data["site_status"]),
        cheapest=offer_from_dict(cheapest) if cheapest else None,
        fallback=[offer_from_dict(o) for o in data.get("fallback", [])],
    )


def normalize_query(query: str) -> str:
    """Lowercase and collapse whitespace so search keys are stable."""
    return " ".join(query.lower().split())


class Cache:
    def __init__(self, path: str = "ucuzu.db", clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._clock = clock
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        if self._conn is not None:
            return
        conn = await aiosqlite.connect(self.path)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.executescript(_SCHEMA)
        await conn.commit()
        self._conn = conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Cache.init() must be awaited before use")
        return self._conn

    def _expired(self, created_at: float, ttl: float) -> bool:
        return self._clock() - float(created_at) > ttl

    async def _write(self, sql: str, params: tuple[Any, ...]) -> None:
        await self.conn.execute(sql, params)
        await self.conn.commit()

    # -- links ---------------------------------------------------------

    async def get_link(self, url: str) -> str | None:
        async with self.conn.execute(
            "SELECT canonical_url, created_at FROM link_cache WHERE url = ?", (url,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        canonical_url, created_at = row
        if self._expired(created_at, LINK_TTL):
            await self._write("DELETE FROM link_cache WHERE url = ?", (url,))
            return None
        return str(canonical_url)

    async def set_link(self, url: str, canonical: str) -> None:
        await self._write(
            "INSERT OR REPLACE INTO link_cache (url, canonical_url, created_at) VALUES (?, ?, ?)",
            (url, canonical, self._clock()),
        )

    # -- products ------------------------------------------------------

    async def get_product(self, site: str, product_id: str) -> Offer | None:
        async with self.conn.execute(
            "SELECT offer_json, fetched_at FROM product_cache WHERE site = ? AND product_id = ?",
            (site, product_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        offer_json, fetched_at = row
        if self._expired(fetched_at, PRODUCT_TTL):
            await self._write(
                "DELETE FROM product_cache WHERE site = ? AND product_id = ?", (site, product_id)
            )
            return None
        return offer_from_dict(json.loads(offer_json))

    async def set_product(self, offer: Offer, product_id: str | None = None) -> None:
        # product_id overrides the storage key: HB -pm- refs are productId-keyed
        # while the parsed offer is sku-keyed; reads use the ref id, so writes
        # must use it too or every re-verify misses.
        await self._write(
            "INSERT OR REPLACE INTO product_cache (site, product_id, offer_json, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (
                offer.site,
                product_id if product_id is not None else offer.product_id,
                json.dumps(offer_to_dict(offer), ensure_ascii=False),
                self._clock(),
            ),
        )

    # -- searches ------------------------------------------------------

    async def get_search(self, site: str, query: str) -> list[Offer] | None:
        key = normalize_query(query)
        async with self.conn.execute(
            "SELECT offers_json, fetched_at FROM search_cache WHERE site = ? AND query = ?",
            (site, key),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        offers_json, fetched_at = row
        if self._expired(fetched_at, SEARCH_TTL):
            await self._write(
                "DELETE FROM search_cache WHERE site = ? AND query = ?", (site, key)
            )
            return None
        return [offer_from_dict(d) for d in json.loads(offers_json)]

    async def set_search(self, site: str, query: str, offers: list[Offer]) -> None:
        await self._write(
            "INSERT OR REPLACE INTO search_cache (site, query, offers_json, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (
                site,
                normalize_query(query),
                json.dumps([offer_to_dict(o) for o in offers], ensure_ascii=False),
                self._clock(),
            ),
        )

    # -- comparisons ---------------------------------------------------

    async def get_comparison(self, url: str) -> tuple[ComparisonResult, float] | None:
        async with self.conn.execute(
            "SELECT result_json, created_at FROM comparison_cache WHERE source_canonical_url = ?",
            (url,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        result_json, created_at = row
        if self._expired(created_at, COMPARISON_TTL):
            await self._write(
                "DELETE FROM comparison_cache WHERE source_canonical_url = ?", (url,)
            )
            return None
        return result_from_dict(json.loads(result_json)), float(created_at)

    async def set_comparison(self, url: str, result: ComparisonResult) -> None:
        await self._write(
            "INSERT OR REPLACE INTO comparison_cache (source_canonical_url, result_json, created_at) "
            "VALUES (?, ?, ?)",
            (
                url,
                json.dumps(result_to_dict(result), ensure_ascii=False),
                self._clock(),
            ),
        )