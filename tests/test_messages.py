"""Rendering tests for Telegram messages (offline, fixture-free)."""

from __future__ import annotations

import dataclasses
import re
from urllib.parse import quote

import pytest

from ucuzu.bot import messages
from ucuzu.core.config import settings
from ucuzu.core.models import ComparisonResult, Offer

HB_URL = "https://www.hepsiburada.com/shimano-power-aero-14000-xsc-pg-p-HBCV00008XOKU2"
N11_URL = "https://www.n11.com/urun/shimano-power-aero-14000-xsc-pg-83521276"
TY_URL = "https://www.trendyol.com/shimano/power-aero-14000-xsc-pg-p-753753295"
AMZ_URL = "https://www.amazon.com.tr/dp/B0FL7ST13J"


def make_offer(
    site: str,
    title: str,
    price: float,
    *,
    coupon_price: bool = False,
    product_id: str | None = None,
    url: str | None = None,
    list_price: float | None = None,
    in_stock: bool = True,
) -> Offer:
    return Offer(
        site=site,
        title=title,
        price=price,
        list_price=list_price,
        url=url or f"https://example.test/{site}",
        product_id=product_id or f"{site}-1",
        variant_id=None,
        merchant=None,
        in_stock=in_stock,
        coupon_price=coupon_price,
        barcode=None,
        brand=None,
    )


ORIGIN = make_offer(
    "hepsiburada",
    "Shimano Power Aero 14000 XSC PG Surf Makine <&>",
    37685.5,
    product_id="HBCV00008XOKU2",
    url=HB_URL,
)
N11 = make_offer(
    "n11",
    "Shimano Power Aero 14000 XSC PG Surf Makine",
    33163.24,
    coupon_price=True,
    product_id="83521276",
    url=N11_URL,
    list_price=39324.0,
)
TY = make_offer(
    "trendyol",
    "Shimano Power Aero 14000 XSC PG Surf Makine",
    33916.95,
    coupon_price=True,
    product_id="753753295",
    url=TY_URL,
)
AMZ = make_offer(
    "amazon",
    "Shimano Power Aero 14000 XSC PG Surf Makine",
    37141.0,
    product_id="B0FL7ST13J",
    url=AMZ_URL,
)
SIMILAR = make_offer(
    "trendyol",
    "Shimano Power Aero 4000 XTC Surf Makine",
    21000.0,
    product_id="44264045",
)

ALL_OK = {"hepsiburada": "ok", "trendyol": "ok", "n11": "ok", "amazon": "ok"}


def make_result(
    *,
    exact: list[Offer] | None = None,
    similar: list[Offer] | None = None,
    site_status: dict[str, str] | None = None,
    cheapest: Offer | None = None,
) -> ComparisonResult:
    exact = [N11, TY, AMZ] if exact is None else exact
    if cheapest is None and exact:
        cheapest = min((o for o in exact if o.in_stock), key=lambda o: o.price)
    return ComparisonResult(
        origin=ORIGIN,
        offers=exact,
        exact=exact,
        similar=[SIMILAR] if similar is None else similar,
        site_status=dict(ALL_OK if site_status is None else site_status),
        cheapest=cheapest,
    )


def table_of(text: str) -> str:
    return text.split("<pre>", 1)[1].split("</pre>", 1)[0]


def test_format_price():
    assert messages.format_price(33163.24) == "33.163,24 ₺"
    assert messages.format_price(37685.5) == "37.685,50 ₺"
    assert messages.format_price(0) == "0,00 ₺"


def test_comparison_table_prices_and_cheapest():
    text = messages.format_comparison(make_result())
    assert "33.163,24 ₺" in text
    assert "33.916,95 ₺" in text
    assert "37.141,00 ₺" in text
    assert text.count("(kuponlu)") == 2
    assert "◀ paylaştığın" in text
    assert "🏆 En ucuz: n11 — 33.163,24 ₺" in text
    assert "4.522,26 ₺ daha ucuz" in text
    assert "ulaşılamıyor" not in table_of(text)


def test_table_site_order_is_fixed():
    table = table_of(messages.format_comparison(make_result()))
    positions = [table.index(name) for name in ("Hepsiburada", "Trendyol", "n11", "Amazon")]
    assert positions == sorted(positions)


def test_titles_are_html_escaped():
    text = messages.format_comparison(make_result())
    assert "&lt;&amp;&gt;" in text
    assert "<&>" not in text


def test_unreachable_site_note():
    text = messages.format_comparison(
        make_result(exact=[N11, TY], site_status={**ALL_OK, "amazon": "error"})
    )
    table = table_of(text)
    assert "— (şu an ulaşılamıyor)" in table
    assert "37.141,00 ₺" not in table


def test_age_footer_only_when_given():
    fresh = messages.format_comparison(make_result())
    assert "güncellendi" not in fresh
    assert "Fiyatlar anlık değişebilir." in fresh

    cached = messages.format_comparison(make_result(), age_minutes=12.6)
    assert "fiyatlar 12 dk önce güncellendi" in cached
    assert "Fiyatlar anlık değişebilir." in cached


def test_similar_offers_are_labeled_and_truncated():
    long_title = "Shimano " + "cok uzun urun adi " * 8
    long_similar = dataclasses.replace(SIMILAR, title=long_title)
    text = messages.format_comparison(make_result(similar=[long_similar]))
    assert "Benzer ürün (farklı satıcı/model olabilir)" in text
    assert "• Benzer: Shimano cok uzun urun adi" in text
    assert "…" in text
    assert long_title not in text
    assert "21.000,00 ₺" in text


def test_similar_capped_at_three():
    extras = [dataclasses.replace(SIMILAR, title=f"Model {i}", price=1000.0 + i) for i in range(5)]
    text = messages.format_comparison(make_result(similar=extras))
    assert text.count("• Benzer:") == 3


def test_same_product_cheapest_message():
    result = make_result(exact=[ORIGIN, AMZ], cheapest=ORIGIN)
    text = messages.format_comparison(result)
    assert "Zaten en iyi fiyatı buldun 👍" in text
    assert "daha ucuz" not in text


def test_no_exact_matches_case():
    result = make_result(exact=[], similar=[], cheapest=None)
    text = messages.format_comparison(result)
    assert "Diğer sitelerde birebir eşleşme bulunamadı" in text
    assert "37.685,50 ₺" in text
    assert "🏆" not in text
    assert messages.cheapest_button(result) is None


def test_only_origin_exact_counts_as_no_match():
    result = make_result(exact=[ORIGIN], cheapest=ORIGIN)
    assert "Diğer sitelerde birebir eşleşme bulunamadı" in messages.format_comparison(result)


def test_cheapest_button_uses_affiliate_template(monkeypatch):
    monkeypatch.setattr(settings, "affiliate_n11", "https://aff.n11.example/go?u={url}")
    markup = messages.cheapest_button(make_result())
    assert markup is not None
    button = markup.inline_keyboard[0][0]
    assert button.text == "En ucuz linke git →"
    assert button.url == f"https://aff.n11.example/go?u={quote(N11_URL, safe='')}"


def test_cheapest_button_amazon_tag(monkeypatch):
    monkeypatch.setattr(settings, "affiliate_n11", "")
    monkeypatch.setattr(settings, "affiliate_amazon_tag", "ucuzu-21")
    markup = messages.cheapest_button(make_result(cheapest=AMZ))
    assert markup is not None
    assert markup.inline_keyboard[0][0].url == f"{AMZ_URL}?tag=ucuzu-21"


def test_error_constants_are_turkish():
    assert messages.ERR_UNSUPPORTED_LINK == (
        "Link çözümlenemedi… Hepsiburada, Trendyol, n11 veya Amazon.com.tr ürün linki paylaş."
    )
    assert messages.ERR_PRODUCT_FETCH == (
        "Ürün bilgisi alınamadı… site şu an engelliyor olabilir, biraz sonra tekrar dene."
    )
    assert messages.ERR_GENERIC == "Bir şeyler ters gitti… biraz sonra tekrar dene."
    assert messages.RATE_LIMIT_MSG == "Biraz yavaş 🙂 tekrar dene"
    assert messages.ACK_TEXT == "🔍 Ürün aranıyor… (4 site)"
    assert "Hepsiburada" in messages.HELP_TEXT
    assert "Hepsiburada" in messages.NO_URL_HINT


def test_help_text_is_fancy_html_guide():
    text = messages.HELP_TEXT
    assert "<b>UcuzuBu'ya hoş geldin!</b>" in text
    for marker in ("Nasıl çalışır?", "Nasıl kullanılır?", "Sonuçta ne var?", "Paylaş ➜ Telegram"):
        assert marker in text
    for site in ("Hepsiburada", "Trendyol", "n11", "Amazon.com.tr"):
        assert site in text
    assert "kupon" in text.lower() or "Kupon" in text
    # balanced, Telegram-allowed tags only
    stack = []
    for m in re.finditer(r"<(/?)([a-zA-Z0-9]+)[^>]*>", text):
        close, tag = m.groups()
        assert tag in {"b", "i", "u", "s", "pre", "code", "a"}
        if close:
            assert stack and stack[-1] == tag
            stack.pop()
        else:
            stack.append(tag)
    assert not stack
    assert len(text) <= 4096