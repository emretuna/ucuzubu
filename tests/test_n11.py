"""Offline tests for the n11 adapter against captured fixtures."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ucuzu.core.models import FetchResult, Offer, ProductRef
from ucuzu.sites import n11
from ucuzu.sites.base import ParseError
from ucuzu.sites.n11 import N11Adapter

FIXTURES = Path(__file__).parent / "fixtures"

PRODUCT_URL = (
    "https://www.n11.com/urun/"
    "shimano-power-aero-14000-xsc-pg-35-devir-84cm-sarim-surf-makine-83521276"
)
SEARCH_ITEM_ID = "83521276"


@pytest.fixture()
def adapter() -> N11Adapter:
    return N11Adapter()


@pytest.fixture()
def product_html() -> str:
    return (FIXTURES / "n11_product.html").read_text(encoding="utf-8")


@pytest.fixture()
def search_html() -> str:
    return (FIXTURES / "n11_search.html").read_text(encoding="utf-8")


def _patch_fetch(monkeypatch: pytest.MonkeyPatch, html: str, calls: list[dict]) -> None:
    async def fake_fetch(url: str, **kwargs) -> FetchResult:
        calls.append({"url": url, **kwargs})
        return FetchResult(status=200, url=url, text=html, history=[], headers={})

    monkeypatch.setattr(n11, "fetch", fake_fetch)


def _dummy_offer() -> Offer:
    return Offer(
        site="n11",
        title="x",
        price=1.0,
        list_price=None,
        url=PRODUCT_URL,
        product_id=SEARCH_ITEM_ID,
        variant_id=None,
        merchant=None,
        in_stock=True,
        coupon_price=False,
        barcode=None,
        brand=None,
    )


class TestParseProductUrl:
    def test_extracts_trailing_id_and_strips_query(self, adapter: N11Adapter) -> None:
        ref = adapter.parse_product_url(PRODUCT_URL + "?magaza=RASTGELE&utm_source=x#yorumlar")
        assert ref == ProductRef(
            site="n11",
            product_id="83521276",
            url=PRODUCT_URL,
        )

    def test_rejects_foreign_host(self, adapter: N11Adapter) -> None:
        assert adapter.parse_product_url(
            "https://www.hepsiburada.com/urun/shimano-14000-83521276"
        ) is None

    def test_rejects_search_and_slugless_paths(self, adapter: N11Adapter) -> None:
        assert adapter.parse_product_url("https://www.n11.com/arama?q=shimano") is None
        assert adapter.parse_product_url("https://www.n11.com/urun/shimano-olta") is None


class TestFetchProduct:
    async def test_parses_pdp_fields(
        self, adapter: N11Adapter, product_html: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict] = []
        _patch_fetch(monkeypatch, product_html, calls)

        ref = ProductRef(site="n11", product_id=SEARCH_ITEM_ID, url=PRODUCT_URL)
        offer = await adapter.fetch_product(ref)

        assert calls == [{"url": PRODUCT_URL, "mobile": True}]
        assert offer.site == "n11"
        assert offer.price == pytest.approx(34605.12)
        assert offer.list_price == pytest.approx(39324.0)
        assert offer.coupon_price is True
        assert offer.in_stock is True
        assert offer.product_id == SEARCH_ITEM_ID
        assert offer.variant_id is None
        assert offer.merchant == "178358"
        assert offer.barcode is None  # defaultGtin and skus[].gtin are all null here
        assert offer.brand == "Shimano"
        assert offer.url == PRODUCT_URL
        assert "Shimano Power Aero 14000" in offer.title

    async def test_missing_anchor_raises_parse_error(
        self, adapter: N11Adapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict] = []
        _patch_fetch(monkeypatch, "<html><body>blocked</body></html>", calls)

        ref = ProductRef(site="n11", product_id=SEARCH_ITEM_ID, url=PRODUCT_URL)
        with pytest.raises(ParseError):
            await adapter.fetch_product(ref)


class TestSearch:
    async def test_parses_item_list(
        self, adapter: N11Adapter, search_html: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict] = []
        _patch_fetch(monkeypatch, search_html, calls)

        offers = await adapter.search("Shimano Power Aero 14000 Xsc Pg")

        assert calls == [
            {"url": "https://www.n11.com/arama?q=Shimano+Power+Aero+14000+Xsc+Pg"}
        ]
        assert len(offers) == 9

        exact = [o for o in offers if o.product_id == SEARCH_ITEM_ID]
        assert len(exact) == 1
        best = exact[0]
        assert best.price == pytest.approx(33163.24)
        assert best.list_price == pytest.approx(37685.50)
        assert best.coupon_price is True
        assert best.url == PRODUCT_URL
        assert best.in_stock is True
        assert best.brand == "Shimano"
        assert best.variant_id is None
        assert best.merchant is None
        assert best.barcode is None
        assert best.title == "Shimano Power Aero 14000 Xsc Pg 3.5 Devir 84cm Sarım Surf Makine"

    async def test_only_item_list_blocks_are_used(
        self, adapter: N11Adapter, search_html: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict] = []
        _patch_fetch(monkeypatch, search_html, calls)

        offers = await adapter.search("shimano")

        # Organization + WebPage JSON-LD blocks must not leak into results.
        assert all(o.site == "n11" and o.price > 0 for o in offers)
        assert {o.product_id for o in offers} == {
            "58981303",
            "42421392",
            "90461076",
            "44264045",
            "152656903",
            "118071558",
            "83521276",
            "47165475",
            "139918336",
        }

    async def test_no_item_list_raises_parse_error(
        self, adapter: N11Adapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict] = []
        _patch_fetch(monkeypatch, "<html><script>var x=1;</script></html>", calls)

        with pytest.raises(ParseError):
            await adapter.search("shimano")


class TestAffiliateUrl:
    def test_template_substitutes_encoded_url(
        self, adapter: N11Adapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_settings = SimpleNamespace(
            affiliate_n11="https://go.n11.test/link?target={url}",
            affiliate_hepsiburada="",
            affiliate_trendyol="",
            affiliate_amazon_tag="",
        )
        monkeypatch.setattr("ucuzu.core.affiliate.settings", fake_settings)

        assert adapter.affiliate_url(_dummy_offer()) == (
            "https://go.n11.test/link?target=https%3A%2F%2Fwww.n11.com%2Furun%2F"
            "shimano-power-aero-14000-xsc-pg-35-devir-84cm-sarim-surf-makine-83521276"
        )

    def test_no_template_returns_canonical_url(
        self, adapter: N11Adapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "ucuzu.core.affiliate.settings",
            SimpleNamespace(
                affiliate_n11="",
                affiliate_hepsiburada="",
                affiliate_trendyol="",
                affiliate_amazon_tag="",
            ),
        )

        assert adapter.affiliate_url(_dummy_offer()) == PRODUCT_URL