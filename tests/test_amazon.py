"""Offline tests for the Amazon.com.tr adapter against captured fixtures."""

from __future__ import annotations

import pathlib

import pytest

import ucuzu.core.fetch as fetch_mod
from ucuzu.core.fetch import BlockedError
from ucuzu.core.models import FetchResult, Offer, ProductRef
from ucuzu.sites.amazon import AmazonAdapter
from ucuzu.sites.base import ParseError

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
ASIN = "B0FL7ST13J"
PRODUCT_URL = f"https://www.amazon.com.tr/dp/{ASIN}"
SEARCH_QUERY = "shimano power aero 14000 xsc pg"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def adapter() -> AmazonAdapter:
    return AmazonAdapter()


def _patch_fetch(monkeypatch, text: str) -> list:
    calls: list = []

    async def fake_fetch(url: str, *, mobile: bool = False, **kwargs):
        calls.append({"url": url, "mobile": mobile, **kwargs})
        return FetchResult(status=200, url=url, text=text)

    monkeypatch.setattr(fetch_mod, "fetch", fake_fetch)
    return calls


async def test_fetch_product_parses_mobile_layout(adapter, monkeypatch):
    calls = _patch_fetch(monkeypatch, _fixture("amz_product.html"))
    ref = adapter.parse_product_url(PRODUCT_URL)
    assert ref is not None

    offer = await adapter.fetch_product(ref)

    assert calls[0]["url"] == PRODUCT_URL
    assert calls[0]["mobile"] is True
    assert offer.site == "amazon"
    assert "shimano" in offer.title.lower()
    assert "14000" in offer.title
    assert offer.price == 37141.0
    assert offer.list_price is None
    assert offer.in_stock is True
    assert offer.coupon_price is False
    assert offer.barcode is None
    assert offer.brand is None
    assert offer.merchant is None
    assert offer.variant_id is None
    assert offer.product_id == ASIN
    assert offer.url == PRODUCT_URL


async def test_fetch_product_reads_desktop_price_region(adapter, monkeypatch):
    html = (
        '<html><body><span id="productTitle"> Shimano Test 14000 </span>'
        '<div id="corePrice_desktop"><div class="a-section">'
        '<span class="a-price a-text-price"><span class="a-offscreen">'
        "120,00 TL</span></span>"
        '<span class="a-price"><span class="a-offscreen">99,90 TL</span></span>'
        "</div></div>"
        '<div id="availability">Stokta var</div></body></html>'
    )
    _patch_fetch(monkeypatch, html)
    ref = adapter.parse_product_url(PRODUCT_URL)
    assert ref is not None

    offer = await adapter.fetch_product(ref)

    assert offer.title == "Shimano Test 14000"
    assert offer.price == 99.9
    assert offer.list_price == 120.0
    assert offer.in_stock is True


async def test_fetch_product_out_of_stock(adapter, monkeypatch):
    html = (
        '<html><body><span id="productTitle">Shimano Test 14000</span>'
        '<div id="corePrice_desktop"><span class="a-offscreen">10,00 TL</span></div>'
        '<div id="availability">Stokta yok</div></body></html>'
    )
    _patch_fetch(monkeypatch, html)
    ref = adapter.parse_product_url(PRODUCT_URL)
    assert ref is not None

    offer = await adapter.fetch_product(ref)

    assert offer.in_stock is False


async def test_fetch_product_without_title_raises(adapter, monkeypatch):
    _patch_fetch(monkeypatch, "<html><body>blocked</body></html>")
    ref = adapter.parse_product_url(PRODUCT_URL)
    assert ref is not None

    with pytest.raises(ParseError):
        await adapter.fetch_product(ref)


async def test_fetch_product_without_price_raises(adapter, monkeypatch):
    _patch_fetch(
        monkeypatch,
        '<html><body><span id="productTitle">Shimano Test</span></body></html>',
    )
    ref = adapter.parse_product_url(PRODUCT_URL)
    assert ref is not None

    with pytest.raises(ParseError):
        await adapter.fetch_product(ref)


def test_parse_product_url_dp_form_strips_tracking(adapter):
    messy = (
        PRODUCT_URL
        + "?th=1&psc=1&_encoding=UTF8&pd_rd_i=B0FL7ST13J&ref_=share#reviews"
    )
    ref = adapter.parse_product_url(messy)

    assert ref is not None
    assert ref.site == "amazon"
    assert ref.product_id == ASIN
    assert ref.url == PRODUCT_URL
    assert ref.variant_id is None
    assert "?" not in ref.url and "#" not in ref.url


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.amazon.com.tr/gp/product/{ASIN}/ref=abc?th=1",
        f"https://www.amazon.com.tr/Shimano-Power-14000-Devir-Makine/dp/{ASIN}/ref=sr_1_1?dib=x",
        f"https://m.amazon.com.tr/dp/{ASIN}",
        f"https://amazon.com.tr/dp/{ASIN}",
    ],
)
def test_parse_product_url_alternate_forms(adapter, url):
    ref = adapter.parse_product_url(url)

    assert ref is not None
    assert ref.product_id == ASIN
    assert ref.url == PRODUCT_URL


@pytest.mark.parametrize(
    "url",
    [
        "https://www.amazon.com.tr/s?k=shimano",
        "https://www.amazon.com.tr/gp/cart/view.html",
        f"https://www.amazon.de/dp/{ASIN}",
        "https://www.trendyol.com/shimano-power-aero-p-753753295",
        "https://www.amazon.com.tr/dp/B0FL7ST13",
        "https://www.amazon.com.tr/dp/NOT!ANASIN",
        "",
    ],
)
def test_parse_product_url_rejects(adapter, url):
    assert adapter.parse_product_url(url) is None


async def test_search_parses_cards(adapter, monkeypatch):
    calls = _patch_fetch(monkeypatch, _fixture("amz_search2.html"))

    offers = await adapter.search(SEARCH_QUERY)

    assert calls[0]["url"] == (
        "https://www.amazon.com.tr/s?k=shimano+power+aero+14000+xsc+pg"
    )
    assert calls[0]["mobile"] is False
    assert offers
    assert len({offer.product_id for offer in offers}) == len(offers)

    target = [offer for offer in offers if offer.product_id == ASIN]
    assert len(target) == 1
    card = target[0]
    assert card.site == "amazon"
    assert card.price == 37141.0
    assert "shimano" in card.title.lower()
    assert "14000" in card.title
    assert card.url == PRODUCT_URL
    assert card.in_stock is True
    assert card.list_price is None
    assert card.coupon_price is False
    assert card.barcode is None
    assert card.brand is None
    assert card.merchant is None
    assert card.variant_id is None
    assert all(offer.url == f"https://www.amazon.com.tr/dp/{offer.product_id}" for offer in offers)


async def test_search_skips_cards_without_title_or_price(adapter, monkeypatch):
    html = (
        '<div data-asin="B0FL7ST13J"><h2 aria-label="Shimano 14000"></h2></div>'
        '<div data-asin="B0ABCDEFGH"><h2 aria-label="Başka Ürün"></h2>'
        '<span class="a-offscreen">1.234,50 TL</span></div>'
        '<div data-asin=""><span class="a-offscreen">1,00 TL</span></div>'
    )
    _patch_fetch(monkeypatch, html)

    offers = await adapter.search("shimano")

    assert [offer.product_id for offer in offers] == ["B0ABCDEFGH"]
    assert offers[0].title == "Başka Ürün"
    assert offers[0].price == 1234.5


async def test_search_without_cards_raises(adapter, monkeypatch):
    _patch_fetch(monkeypatch, "<html><body>no results</body></html>")

    with pytest.raises(ParseError):
        await adapter.search("shimano")


_CHALLENGE_HTML = (
    '<html><head><meta http-equiv="refresh" content="5; URL=\'/s?k=x&bm-verify=AAQAAA\'">'
    "<script>var i = 1; var j = i + 2;</script></head><body></body></html>"
)


async def test_search_challenge_raises_blocked(adapter, monkeypatch):
    _patch_fetch(monkeypatch, _CHALLENGE_HTML)

    with pytest.raises(BlockedError, match="bm-verify"):
        await adapter.search("shimano")


async def test_fetch_product_challenge_raises_blocked(adapter, monkeypatch):
    _patch_fetch(monkeypatch, _CHALLENGE_HTML)

    with pytest.raises(BlockedError, match="bm-verify"):
        await adapter.fetch_product(ProductRef(site="amazon", product_id=ASIN, url=PRODUCT_URL))


async def test_search_no_results_page_returns_empty(adapter, monkeypatch):
    html = (
        '<html><body><div class="s-result-item" data-asin="" data-index="0"></div>'
        "<span>Arama sorgunuz için sonuç bulunamadı. Yazım hatalarını kontrol edin</span>"
        "</body></html>"
    )
    _patch_fetch(monkeypatch, html)

    assert await adapter.search("kudos sky ii pod") == []


def test_affiliate_url_delegates(adapter, monkeypatch):
    seen = {}

    def fake_affiliate(site: str, url: str) -> str:
        seen["args"] = (site, url)
        return "https://example.test/aff"

    monkeypatch.setattr("ucuzu.sites.amazon._affiliate_url", fake_affiliate)
    offer = Offer(
        site="amazon",
        title="x",
        price=1.0,
        list_price=None,
        url=PRODUCT_URL,
        product_id=ASIN,
        variant_id=None,
        merchant=None,
        in_stock=True,
        coupon_price=False,
        barcode=None,
        brand=None,
    )

    assert adapter.affiliate_url(offer) == "https://example.test/aff"
    assert seen["args"] == ("amazon", PRODUCT_URL)