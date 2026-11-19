"""Unit tests for ucuzu.core.links — extraction, canonicalization, redirect resolution.

All network traffic is faked: ucuzu.core.links.fetch.fetch is monkeypatched.
"""

from __future__ import annotations

import pytest

from ucuzu.core import links
from ucuzu.core.links import (
    MAX_HOPS,
    UnsupportedLinkError,
    canonicalize,
    extract_url,
    is_known,
    resolve,
)
from ucuzu.core.models import FetchResult


def install_fetch(monkeypatch, handler):
    """Replace ucuzu.core.links.fetch.fetch; `handler(url)` -> (status, headers[, text])."""
    calls: list[dict] = []

    async def fake_fetch(url, *, mobile=False, timeout=20.0, follow_redirects=True):
        calls.append({"url": url, "mobile": mobile, "follow_redirects": follow_redirects})
        entry = handler(url)
        status, headers = entry[0], entry[1]
        text = entry[2] if len(entry) > 2 else ""
        return FetchResult(status=status, url=url, text=text, history=[], headers=headers)

    monkeypatch.setattr(links.fetch, "fetch", fake_fetch)
    return calls


# --------------------------------------------------------------------- extract_url


def test_extract_url_from_share_text_strips_sentence_punctuation():
    assert (
        extract_url("Bak buna https://app.hb.biz/abc123. çok iyi!")
        == "https://app.hb.biz/abc123"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('link: "https://www.trendyol.com/brand/slug-p-123"', "https://www.trendyol.com/brand/slug-p-123"),
        ("(https://www.n11.com/urun/foo-1)", "https://www.n11.com/urun/foo-1"),
        ("https://www.amazon.com.tr/dp/B0FL7ST13J, bak", "https://www.amazon.com.tr/dp/B0FL7ST13J"),
        ("https://ty.gl/xyz?!", "https://ty.gl/xyz"),
        ("şuna bak: https://hepsiburada.com/a-p-HBCV00008XOKU2;", "https://hepsiburada.com/a-p-HBCV00008XOKU2"),
    ],
)
def test_extract_url_trailing_punctuation_variants(text, expected):
    assert extract_url(text) == expected


def test_extract_url_keeps_query_string_intact():
    url = "https://www.trendyol.com/brand/slug-p-123?boutiqueId=61"
    assert extract_url(f"<{url}>") == url


def test_extract_url_without_url_raises():
    with pytest.raises(UnsupportedLinkError):
        extract_url("merhaba, bu ürünü bulabilir misin?")


def test_extract_url_without_scheme_raises():
    with pytest.raises(UnsupportedLinkError):
        extract_url("www.hepsiburada.com/x-p-HBCV00008XOKU2")


# --------------------------------------------------------------------- canonicalize


def test_canonicalize_strips_query_and_fragment():
    assert (
        canonicalize(
            "https://www.hepsiburada.com/shimano-p-HBCV00008XOKU2"
            "?merchantId=123&boutiqueId=61&adjust_t=xyz#yorumlar"
        )
        == "https://www.hepsiburada.com/shimano-p-HBCV00008XOKU2"
    )


def test_canonicalize_keeps_path_trailing_slash_and_netloc():
    assert (
        canonicalize("https://www.amazon.com.tr/dp/B0FL7ST13J/?tag=x&ref_=y")
        == "https://www.amazon.com.tr/dp/B0FL7ST13J/"
    )


def test_canonicalize_is_idempotent():
    url = "https://www.n11.com/urun/foo-83521276?magaza=x#yorumlar"
    once = canonicalize(url)
    assert canonicalize(once) == once


# ------------------------------------------------------------------------- is_known


@pytest.mark.parametrize(
    "url",
    [
        "https://www.hepsiburada.com/x-p-HBCV00008XOKU2",
        "https://hepsiburada.com/x",
        "https://www.trendyol.com/brand/slug-p-123",
        "https://www.n11.com/urun/foo-83521276",
        "https://www.amazon.com.tr/dp/B0FL7ST13J",
    ],
)
def test_is_known_true_for_marketplace_hosts(url):
    assert is_known(url) is True


@pytest.mark.parametrize(
    "url",
    ["https://app.hb.biz/abc123", "https://ty.gl/abc", "https://amzn.to/abc", "https://example.com/x"],
)
def test_is_known_false_for_shorteners_and_strangers(url):
    assert is_known(url) is False


# --------------------------------------------------------------------------- resolve


async def test_resolve_known_host_does_not_fetch(monkeypatch):
    calls = install_fetch(monkeypatch, lambda url: pytest.fail(f"unexpected fetch: {url}"))

    resolved = await resolve(
        "https://www.hepsiburada.com/shimano-p-HBCV00008XOKU2?merchantId=1&adjust_t=z#yorumlar"
    )

    assert resolved == "https://www.hepsiburada.com/shimano-p-HBCV00008XOKU2"
    assert calls == []


async def test_resolve_shortener_follows_to_marketplace(monkeypatch):
    calls = install_fetch(
        monkeypatch,
        lambda url: (
            302,
            {"location": "https://www.hepsiburada.com/shimano-p-HBCV00008XOKU2?adjust_campaign=share"},
        ),
    )

    resolved = await resolve("https://app.hb.biz/abc123")

    assert resolved == "https://www.hepsiburada.com/shimano-p-HBCV00008XOKU2"
    assert [c["url"] for c in calls] == ["https://app.hb.biz/abc123"]
    assert calls[0]["mobile"] is True
    assert calls[0]["follow_redirects"] is False


async def test_resolve_amazon_shortener_chain(monkeypatch):
    hops = {
        "https://amzn.to/abc": (301, {"location": "https://www.amazon.com.tr/dp/B0FL7ST13J?ref_=x"}),
    }
    calls = install_fetch(monkeypatch, lambda url: hops[url])

    assert await resolve("https://amzn.to/abc") == "https://www.amazon.com.tr/dp/B0FL7ST13J"
    assert len(calls) == 1


async def test_resolve_relative_location_uses_urljoin(monkeypatch):
    hops = {
        "https://example.com/s/abc": (302, {"location": "/track/xyz"}),
        "https://example.com/track/xyz": (302, {"location": "https://www.trendyol.com/brand/slug-p-123?x=1"}),
    }
    calls = install_fetch(monkeypatch, lambda url: hops[url])

    resolved = await resolve("https://example.com/s/abc")

    assert resolved == "https://www.trendyol.com/brand/slug-p-123"
    assert [c["url"] for c in calls] == [
        "https://example.com/s/abc",
        "https://example.com/track/xyz",
    ]


async def test_resolve_accepts_capitalized_location_header(monkeypatch):
    install_fetch(monkeypatch, lambda url: (302, {"Location": "https://www.n11.com/urun/foo-83521276"}))

    assert await resolve("https://ty.gl/abc") == "https://www.n11.com/urun/foo-83521276"


async def test_resolve_stops_at_hop_cap(monkeypatch):
    calls = install_fetch(
        monkeypatch,
        lambda url: (302, {"location": f"https://example.com/hop/{len(str(url)) + 1}"}),
    )

    with pytest.raises(UnsupportedLinkError):
        await resolve("https://example.com/hop/0")

    assert len(calls) == MAX_HOPS


async def test_resolve_unknown_host_without_redirect_raises(monkeypatch):
    install_fetch(monkeypatch, lambda url: (200, {}))

    with pytest.raises(UnsupportedLinkError):
        await resolve("https://example.com/s/abc")


async def test_resolve_unknown_host_without_location_header_raises(monkeypatch):
    install_fetch(monkeypatch, lambda url: (302, {}))

    with pytest.raises(UnsupportedLinkError):
        await resolve("https://example.com/s/abc")


async def test_resolve_unknown_host_landing_on_unknown_raises(monkeypatch):
    hops = {"https://app.hb.biz/abc123": (302, {"location": "https://example.com/moved"})}
    install_fetch(monkeypatch, lambda url: hops.get(url, (200, {})))

    with pytest.raises(UnsupportedLinkError):
        await resolve("https://app.hb.biz/abc123")


async def test_resolve_hb_deeplink_via_adjust_fallback(monkeypatch):
    adj = (
        "https://7t4g.adj.st/product?sku=HBCV00005JZLBB"
        "&adj_fallback=https%3A%2F%2Fwww.hepsiburada.com%2Fshimano-p-HBCV00005JZLBB%3Fmagaza%3DAvgross"
    )
    hops = {"https://app.hb.biz/kieA1aTweX3Z": (301, {"location": adj})}
    calls = install_fetch(monkeypatch, lambda url: hops[url])
    assert await resolve("https://app.hb.biz/kieA1aTweX3Z") == (
        "https://www.hepsiburada.com/shimano-p-HBCV00005JZLBB"
    )
    assert len(calls) == 1  # fallback extracted from the hop-1 URL; adj.st never fetched


async def test_resolve_adjust_desktop_retry(monkeypatch):
    adj = "https://7t4g.adj.st/product?sku=HBCV00005JZLBB"
    hb = "https://www.hepsiburada.com/shimano-p-HBCV00005JZLBB?magaza=Avgross"
    calls = []

    async def fake_fetch(url, *, mobile=False, timeout=20.0, follow_redirects=True):
        calls.append({"url": url, "mobile": mobile})
        if url == "https://app.hb.biz/abc":
            return FetchResult(status=301, url=url, text="", history=[], headers={"location": adj})
        if url == adj and not mobile:
            return FetchResult(status=302, url=url, text="", history=[], headers={"location": hb})
        return FetchResult(status=200, url=url, text="<html>app store</html>", history=[], headers={})

    monkeypatch.setattr(links.fetch, "fetch", fake_fetch)
    assert await resolve("https://app.hb.biz/abc") == "https://www.hepsiburada.com/shimano-p-HBCV00005JZLBB"
    assert [c["mobile"] for c in calls if c["url"] == adj] == [True, False]


async def test_resolve_link_amazon_js_interstitial(monkeypatch):
    target = (
        "https://www.amazon.com.tr/dp/B0FL7ST13J/ref=cm_sw_r_as_gl_api_gl_i_X"
        "?linkCode=ml1&tag=ucuzububot-21&linkId=abc"
    )
    body = (
        '<html><head><script type="text/javascript">window.setTimeout(() => { '
        f'window.location.replace("{target}")'
        " }, 200);</script></head>redirecting...</html>"
    )
    hops = {
        "https://link.amazon/B09HES2G4": (302, {"location": "https://amzlinks.in/B09HES2G4"}),
        "https://amzlinks.in/B09HES2G4": (200, {}, body),
    }
    calls = install_fetch(monkeypatch, lambda url: hops[url])
    assert await resolve("https://link.amazon/B09HES2G4") == "https://www.amazon.com.tr/dp/B0FL7ST13J"
    assert len(calls) == 2  # tag/linkCode query stripped by canonicalize


async def test_resolve_interstitial_without_target_raises(monkeypatch):
    install_fetch(monkeypatch, lambda url: (200, {}, "<html>nothing here</html>"))
    with pytest.raises(UnsupportedLinkError):
        await resolve("https://amzlinks.in/abc")