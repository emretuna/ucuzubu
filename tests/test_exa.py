"""Exa fallback search parsing: domain filter, title/price extraction, failure."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ucuzu.core import exa

HB_URL = "https://www.hepsiburada.com/shimano-power-aero-14000-xsc-pg-p-HBCV00008XOKU2"


def fake_exa(monkeypatch, results=None, raises=None):
    """Install a fake exa_py.Exa; return its construction/call record."""
    state = {"constructed": 0, "calls": [], "init_kwargs": None}

    class FakeExa:
        def __init__(self, **kwargs):
            state["constructed"] += 1
            state["init_kwargs"] = kwargs

        def search(self, *args, **kwargs):
            state["calls"].append((args, kwargs))
            if raises is not None:
                raise raises
            return SimpleNamespace(results=list(results or []))

    monkeypatch.setattr("exa_py.Exa", FakeExa)
    return state


@pytest.fixture(autouse=True)
def exa_key(monkeypatch):
    monkeypatch.setattr(exa.settings, "exa_api_key", "test-key")


async def test_empty_key_returns_empty_without_client(monkeypatch):
    monkeypatch.setattr(exa.settings, "exa_api_key", "  ")
    state = fake_exa(monkeypatch, results=[SimpleNamespace(url=HB_URL, title="x")])

    assert await exa.search_alternatives("shimano power aero") == []
    assert state["constructed"] == 0


async def test_price_from_highlights():
    assert exa._price_from_highlights(["# Shimano\n2.959,95 TL"]) == pytest.approx(2959.95)
    # dot-thousands without decimals are TR thousands separators, not decimals
    assert exa._price_from_highlights(["20.857 TL"]) == pytest.approx(20857.0)
    assert exa._price_from_highlights(["120.000 TL"]) == pytest.approx(120000.0)
    # installment noise is skipped by its multiplier prefix, not by magnitude
    assert exa._price_from_highlights(["9 x 1.474 TL"]) is None
    assert exa._price_from_highlights(["Stokta yok"]) is None


async def test_empty_title_uses_highlight_heading(monkeypatch):
    state = fake_exa(
        monkeypatch,
        results=[
            SimpleNamespace(
                url=HB_URL,
                title="",
                highlights=["# shimano Speedmaster 14000 Xsc Olta Makinası\n2.959,95 TL"],
            )
        ],
    )

    [cand] = await exa.search_alternatives("shimano speedmaster")

    assert cand.title == "shimano Speedmaster 14000 Xsc Olta Makinası"
    assert cand.price == pytest.approx(2959.95)
    assert cand.url == HB_URL
    args, kwargs = state["calls"][0]
    assert args == ("shimano speedmaster",)
    assert kwargs["include_domains"] == list(exa.ALLOWED_DOMAINS)
    assert kwargs["num_results"] == 20
    assert kwargs["type"] == "auto"
    assert kwargs["contents"] == {"highlights": True}
    assert "shimano speedmaster" in kwargs["objective"]


async def test_domain_filter_and_dedupe(monkeypatch):
    fake_exa(
        monkeypatch,
        results=[
            SimpleNamespace(url="https://www.amazon.com/dp/B076T8LYMX", title="US listing"),
            SimpleNamespace(
                url=HB_URL + "?merchantId=379801",
                title="Shimano Power Aero 14000 XSC PG",
            ),
            SimpleNamespace(url=HB_URL, title="Shimano Power Aero 14000 XSC PG"),
            SimpleNamespace(
                url="https://www.amazon.com.tr/dp/B076T8LYMX",
                title="Shimano Power Aero 14000 XSC PG",
            ),
        ],
    )

    cands = await exa.search_alternatives("shimano power aero")

    assert [c.url for c in cands] == [HB_URL, "https://www.amazon.com.tr/dp/B076T8LYMX"]


async def test_caps_candidates(monkeypatch):
    fake_exa(
        monkeypatch,
        results=[
            SimpleNamespace(url=f"https://www.n11.com/urun/test-{i}", title=f"t{i}")
            for i in range(10)
        ],
    )

    cands = await exa.search_alternatives("test")

    assert len(cands) == exa.MAX_CANDIDATES


async def test_search_failure_returns_empty(monkeypatch):
    fake_exa(monkeypatch, raises=RuntimeError("boom"))

    assert await exa.search_alternatives("shimano power aero") == []