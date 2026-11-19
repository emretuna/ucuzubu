"""Pipeline regression tests: fan-out, matching, TY barcode probes, cache flow.

All network replaced by fake adapters; exercises the orchestration logic that
the live smoke test proved end-to-end (TY abbreviated titles + barcode identity).
"""

from __future__ import annotations

import pytest

from ucuzu.core import pipeline
from ucuzu.core.cache import Cache
from ucuzu.core.exa import ExaCandidate
from ucuzu.core.models import Offer, ProductRef
from ucuzu.sites.base import ParseError

HB_URL = "https://www.hepsiburada.com/shimano-power-aero-14000-xsc-pg-p-HBCV00008XOKU2"
N11_URL = "https://www.n11.com/urun/shimano-power-aero-14000-83521276"
HB_TITLE = "SHIMANO Power Aero 14000 Xsc Pg 3.5 Devir 84CM Sarım Surf Makine"


def offer(site, title, price, pid, *, barcode=None, coupon=False, stock=True, url=None):
    if url is None:
        url = (
            f"https://www.hepsiburada.test/slug-p-{pid}"
            if site == "hepsiburada"
            else f"https://www.{site}.test/{pid}"
        )
    return Offer(
        site=site,
        title=title,
        price=price,
        list_price=None,
        url=url,
        product_id=pid,
        variant_id=None,
        merchant=None,
        in_stock=stock,
        coupon_price=coupon,
        barcode=barcode,
        brand=None,
    )


ORIGIN = offer("hepsiburada", HB_TITLE, 37685.5, "HBCV00008XOKU2", barcode="0PA14000PGXSC", url=HB_URL)


class FakeAdapter:
    def __init__(self, site, product=None, search_offers=None, fail_search=False, products=None):
        self.site = site
        self.product = product
        self.products = products or {}
        self.search_offers = search_offers or []
        self.fail_search = fail_search
        self.search_calls: list[str] = []
        self.product_calls: list[str] = []

    def parse_product_url(self, url):
        if self.site == "hepsiburada" and (
            url.startswith("https://www.hepsiburada.com/") or url.startswith("https://www.hepsiburada.test/")
        ) and "-p-" in url:
            pid = url.rsplit("-p-", 1)[1]
            return ProductRef(site=self.site, product_id=pid, url=url)
        if self.site == "n11" and url.startswith("https://www.n11.com/urun/"):
            pid = url.rsplit("-", 1)[1]
            return ProductRef(site=self.site, product_id=pid, url=url)
        if self.site == "trendyol" and url.startswith("https://www.trendyol.test/"):
            pid = url.rsplit("/", 1)[1]
            return ProductRef(site=self.site, product_id=pid, url=url)
        return None

    async def fetch_product(self, ref):
        self.product_calls.append(ref.product_id)
        product = self.products.get(ref.product_id, self.product)
        if product is None:
            raise ParseError(f"{self.site}: no product")
        return product

    async def search(self, query):
        self.search_calls.append(query)
        if self.fail_search:
            raise ParseError(f"{self.site}: search down")
        return list(self.search_offers)

    async def probe_barcodes(self, offers, limit=4):
        out = []
        for o in offers[:limit]:
            ref = self.parse_product_url(o.url)
            p = self.products.get(o.product_id, self.product)
            if ref is not None and p is not None and p.product_id == o.product_id:
                o = Offer(
                    site=o.site, title=o.title, price=o.price, list_price=o.list_price,
                    url=o.url, product_id=o.product_id, variant_id=o.variant_id,
                    merchant=o.merchant, in_stock=o.in_stock, coupon_price=o.coupon_price,
                    barcode=p.barcode, brand=o.brand,
                )
            out.append(o)
        return out + offers[limit:]


@pytest.fixture
def adapters(monkeypatch):
    """Install fake adapters; return them keyed by site."""
    fake = {
        "hepsiburada": FakeAdapter("hepsiburada", product=ORIGIN, search_offers=[ORIGIN]),
        "trendyol": FakeAdapter("trendyol"),
        "n11": FakeAdapter("n11"),
        "amazon": FakeAdapter("amazon"),
    }
    monkeypatch.setattr(pipeline, "ADAPTERS", fake)
    return fake


async def test_compare_ranks_and_marks_errors(adapters):
    adapters["n11"].search_offers = [
        offer("n11", HB_TITLE, 33163.24, "83521276", coupon=True)
    ]
    adapters["amazon"].search_offers = [offer("amazon", HB_TITLE, 37141.0, "B0FL7ST13J")]
    adapters["trendyol"].fail_search = True

    outcome = await pipeline.compare(HB_URL)
    r = outcome.result
    assert outcome.cache_hit is False
    assert r.site_status == {"hepsiburada": "ok", "trendyol": "error", "n11": "ok", "amazon": "ok"}
    assert r.cheapest.site == "n11"
    assert r.savings == pytest.approx(4522.26)
    assert [o.price for o in r.exact] == sorted(o.price for o in r.exact)


async def test_ty_barcode_probe_recovers_abbreviated_listing(adapters):
    # TY search title is abbreviated (low sim); PDP barcode identifies it.
    ty_short = offer(
        "trendyol",
        "Shimano Power Aero 14000 XSC Olta Makinası",
        33916.95,
        "753753295",
        coupon=True,
        url="https://www.trendyol.test/753753295",
    )
    adapters["trendyol"].search_offers = [ty_short]
    adapters["trendyol"].product = offer(
        "trendyol",
        "Shimano Power Aero 14000 XSC Olta Makinası",
        33916.95,
        "753753295",
        barcode="PA14000XSC",
    )
    adapters["n11"].search_offers = [offer("n11", HB_TITLE, 33163.24, "83521276")]

    outcome = await pipeline.compare(HB_URL)
    r = outcome.result
    ty_exact = [o for o in r.exact if o.site == "trendyol"]
    assert [o.product_id for o in ty_exact] == ["753753295"]
    assert r.cheapest.site == "n11"  # TY coupon price still above n11


async def test_origin_without_barcode_adopts_ty_anchor(adapters):
    # n11-origin flow: origin barcode unknown → adopted from plausible TY PDP.
    n11_origin = offer("n11", HB_TITLE, 33163.24, "83521276", url=N11_URL)
    adapters["n11"].product = n11_origin
    adapters["n11"].search_offers = [n11_origin]
    ty_short = offer(
        "trendyol",
        "Shimano Power Aero 14000 XSC Olta Makinası",
        33916.95,
        "753753295",
        url="https://www.trendyol.test/753753295",
    )
    adapters["trendyol"].search_offers = [ty_short]
    adapters["trendyol"].product = offer(
        "trendyol", ty_short.title, 33916.95, "753753295", barcode="PA14000XSC"
    )
    adapters["hepsiburada"].search_offers = [
        offer("hepsiburada", HB_TITLE, 37685.5, "HBCV00008XOKU2", barcode="0PA14000PGXSC")
    ]

    outcome = await pipeline.compare(N11_URL)
    r = outcome.result
    assert r.origin.barcode == "PA14000XSC"
    # HB candidate matches via barcode subsequence identity despite n11 origin.
    assert any(o.site == "hepsiburada" for o in r.exact)
    assert any(o.site == "trendyol" for o in r.exact)


async def test_uncorroborated_ty_barcode_not_adopted(adapters):
    # n11 origin without barcode; the only TY candidate's barcode matches no
    # other site's offer → must NOT be adopted (circular self-confirmation).
    n11_origin = offer("n11", HB_TITLE, 33163.24, "83521276", url=N11_URL)
    adapters["n11"].product = n11_origin
    adapters["n11"].search_offers = [n11_origin]
    ty_wrong = offer(
        "trendyol",
        "Shimano Power Aero 14000 XSC Surf Olta Makinesi",
        39374.0,
        "1233054890",
        url="https://www.trendyol.test/1233054890",
    )
    adapters["trendyol"].search_offers = [ty_wrong]
    adapters["trendyol"].product = offer(
        "trendyol", ty_wrong.title, 39374.0, "1233054890", barcode="W4969363090645"
    )
    adapters["hepsiburada"].search_offers = [
        offer("hepsiburada", HB_TITLE, 37685.5, "HBCV00008XOKU2", barcode="0PA14000PGXSC")
    ]

    outcome = await pipeline.compare(N11_URL)
    r = outcome.result
    assert r.origin.barcode is None
    # The wrong TY listing must not become exact via its own donated barcode.
    assert not any(o.site == "trendyol" for o in r.exact)
    # HB still matches the origin by title.
    assert any(o.site == "hepsiburada" for o in r.exact)

async def test_comparison_cache_hit_skips_network(adapters, tmp_path):
    cache = Cache(str(tmp_path / "t.db"))
    await cache.init()
    try:
        first = await pipeline.compare(HB_URL, cache)
        assert first.cache_hit is False
        calls_after_first = {s: len(a.search_calls) for s, a in adapters.items()}

        second = await pipeline.compare(HB_URL, cache)
        assert second.cache_hit is True
        assert second.created_at is not None
        assert second.result.cheapest == first.result.cheapest
        # No additional searches on the cached path.
        assert {s: len(a.search_calls) for s, a in adapters.items()} == calls_after_first
    finally:
        await cache.close()


async def test_out_of_stock_exact_not_cheapest(adapters):
    cheap_oos = offer("n11", HB_TITLE, 100.0, "83521276", stock=False)
    adapters["n11"].search_offers = [cheap_oos, offer("n11", HB_TITLE, 33163.24, "83521277")]
    outcome = await pipeline.compare(HB_URL)
    r = outcome.result
    assert r.cheapest.product_id == "83521277"
    assert cheap_oos in r.exact  # kept for display


def test_build_model_query_drops_spec_noise():
    assert pipeline.build_model_query(HB_TITLE) == "shimano power aero 14000 xsc pg"
    assert pipeline.build_model_query("Shimano Nexave 4000 FE Olta Makinesi") == "shimano nexave 4000 fe"


async def test_n11_origin_adopt_via_hb_pdp_enrichment(adapters):
    # Real-world shape: n11 origin without barcode, HB search offers without
    # barcode; HB PDP enrichment supplies the corroborating barcode so the
    # correct TY listing (not the higher-sim wrong one) becomes exact.
    n11_origin = offer("n11", HB_TITLE, 33163.24, "83521276", url=N11_URL)
    adapters["n11"].product = n11_origin
    adapters["n11"].search_offers = [n11_origin]

    hb_search = offer("hepsiburada", HB_TITLE, 37685.5, "HBCV00008XOKU2")  # no barcode
    adapters["hepsiburada"].search_offers = [hb_search]
    adapters["hepsiburada"].products = {
        "HBCV00008XOKU2": offer(
            "hepsiburada", HB_TITLE, 37685.5, "HBCV00008XOKU2", barcode="0PA14000PGXSC"
        )
    }

    ty_wrong = offer(
        "trendyol", "Shimano Power Aero 14000 XSC Surf Olta Makinesi", 39374.0, "1233054890",
        url="https://www.trendyol.test/1233054890",
    )
    ty_right = offer(
        "trendyol", "Shimano Power Aero 14000 XSC Olta Makinası", 33916.95, "753753295",
        url="https://www.trendyol.test/753753295",
    )
    adapters["trendyol"].search_offers = [ty_wrong, ty_right]
    adapters["trendyol"].products = {
        "1233054890": offer("trendyol", ty_wrong.title, 39374.0, "1233054890", barcode="W4969363090645"),
        "753753295": offer("trendyol", ty_right.title, 33916.95, "753753295", barcode="PA14000XSC"),
    }

    outcome = await pipeline.compare(N11_URL)
    r = outcome.result
    assert r.origin.barcode == "PA14000XSC"
    ty_ids = {o.product_id for o in r.exact if o.site == "trendyol"}
    assert ty_ids == {"753753295"}  # wrong higher-sim listing excluded


async def test_hb_enrichment_skips_model_mismatch(adapters):
    # A high-sim HB listing with a DIFFERENT model number must not donate its
    # barcode (would mint false exacts at other sites).
    n11_origin = offer("n11", HB_TITLE, 33163.24, "83521276", url=N11_URL)
    adapters["n11"].product = n11_origin
    adapters["n11"].search_offers = [n11_origin]

    wrong_model_title = HB_TITLE.replace("14000", "14001")
    hb_wrong = offer("hepsiburada", wrong_model_title, 30000.0, "HBCVWRONG0001")
    adapters["hepsiburada"].search_offers = [hb_wrong]
    adapters["hepsiburada"].products = {
        "HBCVWRONG0001": offer(
            "hepsiburada", wrong_model_title, 30000.0, "HBCVWRONG0001", barcode="0PA14001PGXSC"
        )
    }
    ty = offer(
        "trendyol", "Shimano Power Aero 14000 XSC Olta Makinası", 33916.95, "753753295",
        url="https://www.trendyol.test/753753295",
    )
    adapters["trendyol"].search_offers = [ty]
    adapters["trendyol"].products = {
        "753753295": offer("trendyol", ty.title, 33916.95, "753753295", barcode="PA14000XSC")
    }

    outcome = await pipeline.compare(N11_URL)
    r = outcome.result
    assert "HBCVWRONG0001" not in adapters["hepsiburada"].product_calls
    assert r.origin.barcode is None  # no corroborating anchor → no adoption
    assert not any(o.site == "trendyol" for o in r.exact)


async def test_hb_enrichment_reaches_genuine_listing_behind_merchant_copies(adapters):
    # Live data drift: HB search pads the top of the title-exact list with 4
    # merchant copies carrying junk barcodes; the genuine listing (whose
    # barcode corroborates TY) ranks 5th. Enrichment must reach it.
    n11_origin = offer("n11", HB_TITLE, 33163.24, "83521276", url=N11_URL)
    adapters["n11"].product = n11_origin
    adapters["n11"].search_offers = [n11_origin]

    junk_pids = ["HBJUNK0000001", "HBJUNK0000002", "HBJUNK0000003", "HBJUNK0000004"]
    junk_barcodes = [
        "FRN-TM936640724357234775",
        "ZURAX-TM936640724357234775",
        "AYSMZRTM936640724357234775",
        "ZURZURA-TM936640724357234775",
    ]
    genuine = offer("hepsiburada", HB_TITLE, 37685.5, "HBCV00008XOKU2")  # no barcode
    adapters["hepsiburada"].search_offers = [
        *(offer("hepsiburada", HB_TITLE, 37685.5, pid) for pid in junk_pids),
        genuine,
    ]
    adapters["hepsiburada"].products = {
        pid: offer("hepsiburada", HB_TITLE, 37685.5, pid, barcode=barcode)
        for pid, barcode in zip(junk_pids, junk_barcodes)
    }
    adapters["hepsiburada"].products["HBCV00008XOKU2"] = offer(
        "hepsiburada", HB_TITLE, 37685.5, "HBCV00008XOKU2", barcode="0PA14000PGXSC"
    )

    ty_wrong = offer(
        "trendyol", "Shimano Power Aero 14000 XSC Surf Olta Makinesi", 39374.0, "1233054890",
        url="https://www.trendyol.test/1233054890",
    )
    ty_right = offer(
        "trendyol", "Shimano Power Aero 14000 XSC Olta Makinası", 33916.95, "753753295",
        url="https://www.trendyol.test/753753295",
    )
    adapters["trendyol"].search_offers = [ty_wrong, ty_right]
    adapters["trendyol"].products = {
        "1233054890": offer("trendyol", ty_wrong.title, 39374.0, "1233054890", barcode="W4969363090645"),
        "753753295": offer("trendyol", ty_right.title, 33916.95, "753753295", barcode="PA14000XSC"),
    }

    outcome = await pipeline.compare(N11_URL)
    r = outcome.result
    # Rank 5 is only reached if the enrichment cap exceeds the 4 copies.
    assert "HBCV00008XOKU2" in adapters["hepsiburada"].product_calls
    assert r.origin.barcode == "PA14000XSC"
    ty_ids = {o.product_id for o in r.exact if o.site == "trendyol"}
    assert ty_ids == {"753753295"}


def exa_spy(monkeypatch, *, key="test-exa-key", cands=()):
    """Replace pipeline's Exa call; return recorded queries."""
    calls = []

    async def fake_search(query):
        calls.append(query)
        return list(cands)

    monkeypatch.setattr(pipeline.exa, "search_alternatives", fake_search)
    monkeypatch.setattr(pipeline.settings, "exa_api_key", key)
    return calls


async def test_exa_fallback_verifies_candidate_pdp(adapters, monkeypatch):
    adapters["hepsiburada"].search_offers = []
    n11_pdp = offer(
        "n11", HB_TITLE, 31000.0, "999", url="https://www.n11.com/urun/shimano-power-aero-14000-999"
    )
    adapters["n11"].products["999"] = n11_pdp
    calls = exa_spy(
        monkeypatch, cands=[ExaCandidate(url=n11_pdp.url, title=HB_TITLE, price=30500.0)]
    )
    notices = []

    async def on_fallback():
        notices.append(True)

    outcome = await pipeline.compare(HB_URL, on_fallback=on_fallback)
    r = outcome.result

    # PDP price wins over the highlight price; no cheapest on a fallback-only result.
    assert [(o.site, o.product_id, o.price) for o in r.fallback] == [("n11", "999", 31000.0)]
    assert r.exact == [] and r.similar == []
    assert r.cheapest is None
    assert notices == [True]
    assert calls == [pipeline.build_query(HB_TITLE)]


async def test_exa_fallback_skipped_when_exact_found(adapters, monkeypatch):
    calls = exa_spy(monkeypatch)

    outcome = await pipeline.compare(HB_URL)

    assert outcome.result.cheapest is not None
    assert outcome.result.fallback == []
    assert calls == []


async def test_exa_fallback_skipped_when_similar_found(adapters, monkeypatch):
    adapters["hepsiburada"].search_offers = [
        offer("hepsiburada", "SHIMANO Power Aero 14000 Xsc Pg Surf Makine", 35000.0, "HBCVSIMILAR")
    ]
    calls = exa_spy(monkeypatch)

    outcome = await pipeline.compare(HB_URL)

    r = outcome.result
    assert [o.product_id for o in r.similar] == ["HBCVSIMILAR"]
    assert r.fallback == []
    assert calls == []


async def test_exa_fallback_disabled_without_key(adapters, monkeypatch):
    adapters["hepsiburada"].search_offers = []
    calls = exa_spy(monkeypatch, key="")

    outcome = await pipeline.compare(HB_URL)

    assert outcome.result.fallback == []
    assert calls == []


async def test_exa_fallback_discards_different_model(adapters, monkeypatch):
    adapters["hepsiburada"].search_offers = []
    other = offer(
        "n11",
        "Daiwa Saltiga 20000 Olta Makine",
        9000.0,
        "777",
        url="https://www.n11.com/urun/daiwa-saltiga-20000-777",
    )
    adapters["n11"].products["777"] = other
    exa_spy(monkeypatch, cands=[ExaCandidate(url=other.url, title=other.title, price=9000.0)])

    outcome = await pipeline.compare(HB_URL)

    assert outcome.result.fallback == []


async def test_exa_fallback_keeps_link_only_for_unparsed_url(adapters, monkeypatch):
    adapters["hepsiburada"].search_offers = []
    exa_spy(
        monkeypatch,
        cands=[ExaCandidate(url="https://www.n11.com/arama", title="", price=1000.0)],
    )

    outcome = await pipeline.compare(HB_URL)

    [fb] = outcome.result.fallback
    assert fb.site == "n11"
    assert fb.title == "Ürün sayfası"
    assert fb.price == 1000.0
    assert fb.product_id == "https://www.n11.com/arama"
    assert fb.in_stock is True