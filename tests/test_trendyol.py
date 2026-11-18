"""Offline parser tests for the Trendyol adapter (fixtures only, no network)."""

from __future__ import annotations

import pytest

from ucuzu.core.config import settings
from ucuzu.core.models import FetchResult, Offer, ProductRef
from ucuzu.sites import trendyol
from ucuzu.sites.base import ParseError

FIXTURES = "tests/fixtures"


def _fixture(name: str) -> str:
    with open(f"{FIXTURES}/{name}", encoding="utf-8") as fh:
        return fh.read()


def _patch_fetch(monkeypatch, html: str, *, status: int = 200, calls: list | None = None):
    async def fake_fetch(url, *, mobile=False, timeout=20.0, follow_redirects=True):
        if calls is not None:
            calls.append({"url": url, "mobile": mobile})
        return FetchResult(status=status, url=url, text=html, history=[], headers={})

    monkeypatch.setattr(trendyol.fetch, "fetch", fake_fetch)


@pytest.fixture
def adapter() -> trendyol.TrendyolAdapter:
    return trendyol.TrendyolAdapter()


# -- parse_product_url -----------------------------------------------------
def test_parse_product_url_keeps_brand_and_strips_query(adapter):
    ref = adapter.parse_product_url(
        "https://www.trendyol.com/shimano/power-aero-14000-xsc-olta-makinasi-p-753753295"
        "?boutiqueId=61&merchantId=270749"
    )
    assert ref is not None
    assert ref.site == "trendyol"
    assert ref.product_id == "753753295"
    assert ref.url == (
        "https://www.trendyol.com/shimano/power-aero-14000-xsc-olta-makinasi-p-753753295"
    )
    assert "?" not in ref.url and "merchantId" not in ref.url


def test_parse_product_url_accepts_bare_host_and_trailing_slash(adapter):
    ref = adapter.parse_product_url(
        "https://trendyol.com/shimano/power-aero-14000-xsc-surf-olta-makinesi-p-1233054890/"
    )
    assert ref is not None
    assert ref.product_id == "1233054890"
    assert ref.url == (
        "https://www.trendyol.com/shimano/power-aero-14000-xsc-surf-olta-makinesi-p-1233054890"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.trendyol.com/sr?q=shimano",
        "https://www.trendyol.com/shimano/power-aero-14000-xsc",  # no -p-<id>
        "https://www.trendyol.com/",  # single segment only
        "https://www.hepsiburada.com/foo-p-123",
        "https://www.trendyol.com.evil.test/shimano/x-p-123",  # suffix spoof
    ],
)
def test_parse_product_url_rejects(adapter, url):
    assert adapter.parse_product_url(url) is None


# -- fetch_product ---------------------------------------------------------
async def test_fetch_product_prices_and_identity(adapter, monkeypatch):
    calls: list[dict] = []
    _patch_fetch(monkeypatch, _fixture("ty_product.html"), calls=calls)
    ref = ProductRef(
        site="trendyol",
        product_id="753753295",
        url="https://www.trendyol.com/shimano/power-aero-14000-xsc-olta-makinasi-p-753753295",
    )

    offer = await adapter.fetch_product(ref)

    # requested with the mobile UA
    assert calls == [{"url": ref.url, "mobile": True}]

    assert offer.site == "trendyol"
    assert offer.price == pytest.approx(33916.95)
    assert offer.list_price == pytest.approx(37685.5)
    assert offer.coupon_price is True
    # membership-only tyPlusCouponApplicablePrice (32221.1) must never surface
    assert offer.price != pytest.approx(32221.1)
    assert offer.in_stock is True
    assert offer.barcode == "PA14000XSC"
    assert offer.brand == "Shimano"
    assert "Shimano" in offer.title and "14000" in offer.title
    assert offer.title.startswith("Shimano Power Aero")
    assert offer.product_id in {"753753295", "1233054890"}
    assert offer.variant_id is None
    assert offer.merchant == "Trançabalıkçılık"
    assert offer.url == ref.url


async def test_fetch_product_raises_without_anchor(adapter, monkeypatch):
    _patch_fetch(monkeypatch, "<html><body>no state here</body></html>")
    ref = ProductRef(site="trendyol", product_id="1", url="https://www.trendyol.com/a/b-p-1")
    with pytest.raises(ParseError):
        await adapter.fetch_product(ref)


async def test_fetch_product_raises_on_http_error(adapter, monkeypatch):
    _patch_fetch(monkeypatch, "not found", status=404)
    ref = ProductRef(site="trendyol", product_id="1", url="https://www.trendyol.com/a/b-p-1")
    with pytest.raises(ParseError):
        await adapter.fetch_product(ref)


# -- search ----------------------------------------------------------------
async def test_search_parses_products(adapter, monkeypatch):
    calls: list[dict] = []
    _patch_fetch(monkeypatch, _fixture("ty_search.html"), calls=calls)

    offers = await adapter.search("Shimano Power Aero 14000 XSC")

    assert calls[0]["mobile"] is False
    assert calls[0]["url"] == (
        "https://www.trendyol.com/sr?q=Shimano+Power+Aero+14000+XSC"
    )
    assert offers, "search must return offers"
    assert {o.product_id for o in offers} & {"753753295", "1233054890"}

    for offer in offers:
        assert offer.site == "trendyol"
        assert offer.url.startswith("https://www.trendyol.com/")
        assert "?" not in offer.url and "#" not in offer.url
        assert offer.product_id.isdigit()
        assert offer.variant_id is None and offer.barcode is None
        assert offer.merchant is None
        assert offer.in_stock is True
        assert offer.brand == "Shimano"
        assert offer.title.startswith("Shimano Power Aero")

    first = next(o for o in offers if o.product_id == "753753295")
    assert first.price == pytest.approx(33916.95)
    assert first.list_price == pytest.approx(37685.5)
    assert first.coupon_price is True
    assert first.url == (
        "https://www.trendyol.com/shimano/power-aero-14000-xsc-olta-makinasi-p-753753295"
    )

    second = next(o for o in offers if o.product_id == "1233054890")
    assert second.price == pytest.approx(39374.0)
    assert second.list_price is None
    assert second.coupon_price is False


async def test_search_raises_without_anchor(adapter, monkeypatch):
    _patch_fetch(monkeypatch, "<html><body>blocked-ish page</body></html>")
    with pytest.raises(ParseError):
        await adapter.search("shimano")


# -- affiliate -------------------------------------------------------------
def test_affiliate_url_empty_config_returns_canonical(adapter, monkeypatch):
    monkeypatch.setattr(settings, "affiliate_trendyol", "")
    offer = _offer()
    assert adapter.affiliate_url(offer) == offer.url


def test_affiliate_url_applies_template(adapter, monkeypatch):
    monkeypatch.setattr(
        settings, "affiliate_trendyol", "https://go.example/ty?u={url}"
    )
    offer = _offer()
    out = adapter.affiliate_url(offer)
    assert out.startswith("https://go.example/ty?u=https%3A%2F%2Fwww.trendyol.com%2F")
    assert "p-753753295" in out


def _offer() -> Offer:
    return Offer(
        site="trendyol",
        title="Shimano Power Aero 14000 XSC",
        price=33916.95,
        list_price=37685.5,
        url="https://www.trendyol.com/shimano/power-aero-14000-xsc-p-753753295",
        product_id="753753295",
        variant_id=None,
        merchant=None,
        in_stock=True,
        coupon_price=True,
        barcode=None,
        brand="Shimano",
    )