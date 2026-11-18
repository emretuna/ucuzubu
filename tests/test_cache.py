"""Offline tests for the SQLite cache (tmp db, injected clock)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from ucuzu.core.cache import (
    COMPARISON_TTL,
    LINK_TTL,
    PRODUCT_TTL,
    SEARCH_TTL,
    Cache,
    offer_from_dict,
    offer_to_dict,
    result_from_dict,
    result_to_dict,
)
from ucuzu.core.models import ComparisonResult, Offer


def make_offer(**overrides) -> Offer:
    data = {
        "site": "hepsiburada",
        "title": "Shimano Power Aero 14000 XSC PG 3.5 Devir 84cm Surf Makine",
        "price": 37685.5,
        "list_price": 39999.0,
        "url": "https://www.hepsiburada.com/x-p-HBCV00008XOKU2",
        "product_id": "HBCV00008XOKU2",
        "variant_id": None,
        "merchant": "Hepsiburada",
        "in_stock": True,
        "coupon_price": False,
        "barcode": "0PA14000PGXSC",
        "brand": "Shimano",
    }
    data.update(overrides)
    return Offer(**data)


def make_result(cheapest: Offer | None) -> ComparisonResult:
    origin = make_offer()
    ty = make_offer(
        site="trendyol",
        price=33916.95,
        url="https://www.trendyol.com/shimano/power-aero-14000-xsc-pg-p-753753295",
        product_id="753753295",
        variant_id="1233054890",
        merchant="Shimano Yetkili Satıcı",
        coupon_price=True,
        barcode="PA14000XSC",
    )
    return ComparisonResult(
        origin=origin,
        offers=[ty],
        exact=[ty],
        similar=[make_offer(site="n11", price=34605.12, coupon_price=True)],
        site_status={"hepsiburada": "ok", "trendyol": "ok", "n11": "ok", "amazon": "error"},
        cheapest=cheapest,
    )


def count_rows(path: str, table: str) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.fixture
async def cache(tmp_path):
    clock = [1_700_000_000.0]
    instance = Cache(path=str(tmp_path / "cache.db"), clock=lambda: clock[0])
    await instance.init()
    try:
        yield instance, clock
    finally:
        await instance.close()


async def test_product_roundtrip(cache):
    instance, _clock = cache
    offer = make_offer()
    await instance.set_product(offer)
    assert await instance.get_product("hepsiburada", "HBCV00008XOKU2") == offer
    # Different key → miss.
    assert await instance.get_product("hepsiburada", "HBCV0000000000") is None


async def test_product_ttl_expiry_deletes_row(cache):
    instance, clock = cache
    await instance.set_product(make_offer())
    clock[0] += PRODUCT_TTL - 1
    assert await instance.get_product("hepsiburada", "HBCV00008XOKU2") is not None

    clock[0] += 2  # now just past 6h
    assert await instance.get_product("hepsiburada", "HBCV00008XOKU2") is None
    # Lazy delete: the row is gone, not merely ignored.
    assert count_rows(instance.path, "product_cache") == 0


async def test_search_ttl_expiry_deletes_row(cache):
    instance, clock = cache
    offers = [make_offer(site="n11", price=33163.24)]
    await instance.set_search("n11", "shimano power aero", offers)
    assert await instance.get_search("n11", "shimano power aero") == offers

    clock[0] += SEARCH_TTL + 1
    assert await instance.get_search("n11", "shimano power aero") is None
    assert count_rows(instance.path, "search_cache") == 0


async def test_search_query_normalized_on_set_and_get(cache):
    instance, _clock = cache
    offers = [make_offer()]
    await instance.set_search("hepsiburada", "  Shimano  POWER  ", offers)

    assert await instance.get_search("hepsiburada", "shimano power") == offers
    assert await instance.get_search("hepsiburada", "SHIMANO   Power ") == offers

    # The stored key is normalized (single row, lowercase collapsed query).
    with sqlite3.connect(instance.path) as conn:
        keys = [row[0] for row in conn.execute("SELECT query FROM search_cache")]
    assert keys == ["shimano power"]


async def test_link_roundtrip_and_ttl(cache):
    instance, clock = cache
    short = "https://app.hb.biz/abc123"
    canonical = "https://www.hepsiburada.com/x-p-HBCV00008XOKU2"
    await instance.set_link(short, canonical)
    assert await instance.get_link(short) == canonical

    clock[0] += LINK_TTL - 1
    assert await instance.get_link(short) == canonical

    clock[0] += 2
    assert await instance.get_link(short) is None
    assert count_rows(instance.path, "link_cache") == 0


async def test_comparison_roundtrip_returns_created_at(cache):
    instance, clock = cache
    cheapest = make_offer(
        site="n11",
        price=33163.24,
        coupon_price=True,
        list_price=39324.0,
        merchant="n11 Satıcı",
    )
    result = make_result(cheapest)
    url = "https://www.hepsiburada.com/x-p-HBCV00008XOKU2"
    await instance.set_comparison(url, result)

    hit = await instance.get_comparison(url)
    assert hit is not None
    cached, created_at = hit
    assert created_at == clock[0]
    assert cached == result
    assert cached.cheapest == cheapest
    assert cached.savings == pytest.approx(37685.5 - 33163.24)


async def test_comparison_ttl_expiry_deletes_row(cache):
    instance, clock = cache
    url = "https://www.hepsiburada.com/x-p-HBCV00008XOKU2"
    await instance.set_comparison(url, make_result(None))

    clock[0] += COMPARISON_TTL - 1
    assert await instance.get_comparison(url) is not None

    clock[0] += 2
    assert await instance.get_comparison(url) is None
    assert count_rows(instance.path, "comparison_cache") == 0


async def test_result_roundtrip_without_cheapest(cache):
    instance, _clock = cache
    result = make_result(None)
    url = "https://www.hepsiburada.com/x-p-HBCV00008XOKU2"
    await instance.set_comparison(url, result)

    hit = await instance.get_comparison(url)
    assert hit is not None
    cached, _created_at = hit
    assert cached.cheapest is None
    assert cached.savings == 0.0
    assert cached.origin == result.origin
    assert cached.offers == result.offers
    assert cached.exact == result.exact
    assert cached.similar == result.similar
    assert cached.site_status == result.site_status


def test_offer_dict_helpers_roundtrip_none_fields():
    offer = make_offer(variant_id=None, merchant=None, barcode=None, brand=None, list_price=None)
    data = offer_to_dict(offer)
    assert data["variant_id"] is None
    assert offer_from_dict(data) == offer


def test_result_dict_helpers_are_json_serializable():
    result = make_result(None)
    payload = json.dumps(result_to_dict(result), ensure_ascii=False)
    assert result_from_dict(json.loads(payload)) == result


async def test_set_product_key_override(cache):
    # HB -pm- refs are productId-keyed while the parsed offer is sku-keyed;
    # the override keeps reads (ref id) and writes symmetric.
    instance, _clock = cache
    offer = make_offer()  # product_id HBCV00008XOKU2 (sku)
    await instance.set_product(offer, product_id="HBC0000PMREF1")
    assert await instance.get_product("hepsiburada", "HBC0000PMREF1") == offer
    assert await instance.get_product("hepsiburada", "HBCV00008XOKU2") is None


async def test_set_product_default_key_is_offer_id(cache):
    instance, _clock = cache
    offer = make_offer()
    await instance.set_product(offer)
    assert await instance.get_product("hepsiburada", offer.product_id) == offer