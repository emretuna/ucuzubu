"""Offline tests for the Hepsiburada adapter against captured fixtures."""

from __future__ import annotations

import pathlib

import pytest

import ucuzu.core.fetch as fetch_mod
from ucuzu.core.models import FetchResult, Offer
from ucuzu.sites.base import ParseError
from ucuzu.sites.hepsiburada import HepsiburadaAdapter

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
PRODUCT_URL = (
    "https://www.hepsiburada.com/"
    "shimano-power-aero-14000-xsc-pg-3-5-devir-84cm-sarim-surf-makine-p-HBCV00008XOKU2"
)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def adapter() -> HepsiburadaAdapter:
    return HepsiburadaAdapter()


def _patch_fetch(monkeypatch, text: str, *, expected_mobile: bool | None = None) -> list:
    calls: list = []

    async def fake_fetch(url: str, *, mobile: bool = False, **kwargs):
        calls.append({"url": url, "mobile": mobile, **kwargs})
        return FetchResult(status=200, url=url, text=text)

    monkeypatch.setattr(fetch_mod, "fetch", fake_fetch)
    return calls


async def test_fetch_product_parses_redux_store(adapter, monkeypatch):
    calls = _patch_fetch(monkeypatch, _fixture("hb_product.html"), expected_mobile=True)
    ref = adapter.parse_product_url(PRODUCT_URL)
    assert ref is not None

    offer = await adapter.fetch_product(ref)

    assert calls[0]["url"] == PRODUCT_URL
    assert calls[0]["mobile"] is True
    assert "shimano" in offer.title.lower() and "14000" in offer.title
    assert offer.title.startswith("SHIMANO")
    assert offer.site == "hepsiburada"
    assert offer.price == 37685.5
    assert offer.barcode == "0PA14000PGXSC"
    assert offer.in_stock is True
    assert offer.product_id == "HBCV00008XOKU2"
    assert offer.merchant == "trançabalıkçılık"
    assert offer.brand == "SHIMANO"
    assert offer.variant_id is None
    assert offer.list_price is None
    assert offer.coupon_price is False
    assert offer.url == ref.url


def test_parse_product_url_canonicalizes(adapter):
    messy = (
        PRODUCT_URL
        + "?merchantId=abc&boutiqueId=61&adjust_t=xyz&utm_source=share#yorumlar"
    )
    ref = adapter.parse_product_url(messy)

    assert ref is not None
    assert ref.site == "hepsiburada"
    assert ref.product_id == "HBCV00008XOKU2"
    assert ref.url == PRODUCT_URL
    assert "?" not in ref.url and "#" not in ref.url


def test_parse_product_url_pm_form(adapter):
    ref = adapter.parse_product_url(
        "https://www.hepsiburada.com/shimano-power-aero-14000-xsc-pg-surf-olta-makina"
        "-pm-HBC00008XOKU1?magaza=foo"
    )

    assert ref is not None
    assert ref.product_id == "HBC00008XOKU1"
    assert ref.url == (
        "https://www.hepsiburada.com/shimano-power-aero-14000-xsc-pg-surf-olta-makina"
        "-pm-HBC00008XOKU1"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.trendyol.com/brand/slug-p-123456",
        "https://www.hepsiburada.com/ara?q=shimano",
        "https://www.hepsiburada.com/",
        "",
    ],
)
def test_parse_product_url_rejects(adapter, url):
    assert adapter.parse_product_url(url) is None


async def test_search_parses_product_list(adapter, monkeypatch):
    calls = _patch_fetch(monkeypatch, _fixture("hb_search.html"))
    offers = await adapter.search("shimano power aero 14000")

    assert calls[0]["url"] == (
        "https://www.hepsiburada.com/ara?q=shimano+power+aero+14000"
    )
    assert calls[0]["mobile"] is False
    assert len(offers) == 20
    assert all(isinstance(offer, Offer) for offer in offers)
    assert all(offer.site == "hepsiburada" for offer in offers)

    match = [o for o in offers if o.product_id == "HBCV00008XOKU2"]
    assert len(match) == 1
    offer = match[0]
    assert offer.price == 37685.5
    assert offer.title == (
        "Shimano Power Aero 14000 Xsc Pg 3.5 Devir 84CM Sarım Surf Makine"
    )
    assert offer.url == (
        "https://www.hepsiburada.com/shimano-power-aero-14000-xsc-pg-3-5-devir-84cm-"
        "sarim-surf-makine-pm-HBC00008XOKU1"
    )
    assert offer.merchant == "trançabalıkçılık"
    assert offer.in_stock is True
    assert offer.variant_id == "HBCV00008XOKU2"
    assert offer.brand == "SHIMANO"
    assert offer.barcode is None
    assert offer.list_price is None
    assert offer.coupon_price is False


async def test_search_without_productlist_raises(adapter, monkeypatch):
    _patch_fetch(monkeypatch, _fixture("hb_product.html"))
    with pytest.raises(ParseError):
        await adapter.search("shimano")


async def test_fetch_product_without_anchor_raises(adapter, monkeypatch):
    _patch_fetch(monkeypatch, "<html><body>blocked</body></html>")
    ref = adapter.parse_product_url(PRODUCT_URL)
    assert ref is not None
    with pytest.raises(ParseError):
        await adapter.fetch_product(ref)


def test_affiliate_url_delegates(adapter, monkeypatch):
    seen = {}

    def fake_affiliate(site: str, url: str) -> str:
        seen["args"] = (site, url)
        return "https://track.example/x"

    monkeypatch.setattr(
        "ucuzu.sites.hepsiburada._affiliate_url", fake_affiliate
    )
    offer = Offer(
        site="hepsiburada",
        title="t",
        price=1.0,
        list_price=None,
        url=PRODUCT_URL,
        product_id="HBCV00008XOKU2",
        variant_id=None,
        merchant=None,
        in_stock=True,
        coupon_price=False,
        barcode=None,
        brand=None,
    )
    assert adapter.affiliate_url(offer) == "https://track.example/x"
    assert seen["args"] == ("hepsiburada", PRODUCT_URL)