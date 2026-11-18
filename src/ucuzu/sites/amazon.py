"""Amazon.com.tr adapter: product page (mobile layout) + search results page.

Anchors:
- PDP title: ``<span id="productTitle">`` (desktop layout) or ``<span id="title">``
  (mobile layout, which is what ``mobile=True`` returns).
- PDP price: first price inside ``#corePrice_desktop``, ``#apex_desktop`` or the
  mobile ``#corePriceDisplay_mobile_feature_div``. Amazon sometimes ships the
  accessibility ``a-offscreen`` price empty, so the visible ``a-price-whole`` /
  ``a-price-fraction`` pair is used as a fallback.
- Search: cards delimited by ``data-asin="``; title from the ``<h2 aria-label>``
  (fallback: first ``<span>`` inside the h2), price from the first
  ``class="a-offscreen"`` in the card. Cards without title or price (sponsored
  placeholders, add-to-cart fragments) are skipped.
"""

from __future__ import annotations

import html as html_lib
import re
from urllib.parse import quote_plus, urlsplit

from ucuzu.core import fetch as fetch_mod
from ucuzu.core.affiliate import affiliate_url as _affiliate_url
from ucuzu.core.models import Offer, ProductRef
from ucuzu.sites.base import ParseError, parse_price

BASE_URL = "https://www.amazon.com.tr"
SEARCH_URL = BASE_URL + "/s?k="

_ASIN_RE = re.compile(r"[A-Z0-9]{10}")
_DP_PATH_RE = re.compile(r"/dp/([A-Za-z0-9]{10})(?=[/?#]|$)")
_GP_PATH_RE = re.compile(r"/gp/product/([A-Za-z0-9]{10})(?=[/?#]|$)")
_CARD_ASIN_RE = re.compile(r'([A-Z0-9]{10})"')

_DIV_TAG_RE = re.compile(r"<(/?)div\b[^>]*>", re.IGNORECASE)
_OFFSCREEN_RE = re.compile(r'class="a-offscreen"[^>]*>([^<]*)<')
_WHOLE_RE = re.compile(
    r'class="a-price-whole">([^<]*)'
    r'(?:<span class="a-price-decimal">[^<]*</span>)?'
    r"</span>\s*"
    r'<span class="a-price-fraction">([^<]*)<',
    re.DOTALL,
)
_STRIKE_RE = re.compile(r'class="[^"]*\ba-text-price\b')

_PRODUCT_TITLE_RE = re.compile(
    r'<span[^>]*\bid="productTitle"[^>]*>(.*?)</span>', re.DOTALL
)
_MOBILE_TITLE_RE = re.compile(r'<span[^>]*\bid="title"[^>]*>(.*?)</span>', re.DOTALL)
_H2_ARIA_RE = re.compile(r'<h2\b[^>]*\baria-label="([^"]*)"', re.DOTALL)
_H2_BLOCK_RE = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.DOTALL)
_FIRST_SPAN_RE = re.compile(r"<span\b[^>]*>(.*?)</span>", re.DOTALL)
_TAG_TEXT_RE = re.compile(r"<[^>]+>")

# Price containers, in priority order. Desktop pages use corePrice_desktop;
# mobile pages (mobile=True) use corePriceDisplay_mobile_feature_div.
_PRICE_ANCHORS = (
    "corePrice_desktop",
    "corePriceDisplay_mobile_feature_div",
    "apex_desktop",
    "corePrice_feature_div",
)


def _clean_text(raw: str) -> str:
    """Strip tags/entities and collapse whitespace."""
    text = html_lib.unescape(_TAG_TEXT_RE.sub(" ", raw))
    return " ".join(text.split())


def _element_region(html: str, anchor: str) -> str | None:
    """Return the markup of the <div id="anchor"> element (balanced tags)."""
    match = re.search(
        r'<div\b[^>]*\bid="%s"' % re.escape(anchor), html, re.IGNORECASE
    )
    if match is None:
        return None
    depth = 0
    for tag in _DIV_TAG_RE.finditer(html, match.start()):
        if tag.group(1):
            depth -= 1
            if depth == 0:
                return html[match.start() : tag.start()]
        else:
            depth += 1
    return html[match.start() :]


def _is_strike_price(region: str, index: int) -> bool:
    """True when the offscreen span at ``index`` belongs to a strike-through price."""
    marker = region.rfind('class="a-price', 0, index)
    if marker < 0:
        return False
    return "a-text-price" in region[marker:index]


def _first_price(region: str) -> float | None:
    for match in _OFFSCREEN_RE.finditer(region):
        if _is_strike_price(region, match.start()):
            continue
        text = _clean_text(match.group(1))
        if not text:
            continue
        try:
            value = parse_price(text)
        except ValueError:
            continue
        if value > 0:
            return value
    whole = _WHOLE_RE.search(region)
    if whole is not None:
        whole_text = _clean_text(whole.group(1)).rstrip(",.")
        fraction = _clean_text(whole.group(2))
        if whole_text and fraction.isdigit():
            try:
                value = parse_price(f"{whole_text},{fraction}")
            except ValueError:
                return None
            if value > 0:
                return value
    return None


def _strike_price(region: str) -> float | None:
    for match in _STRIKE_RE.finditer(region):
        window = region[match.start() : match.start() + 500]
        for offscreen in _OFFSCREEN_RE.finditer(window):
            text = _clean_text(offscreen.group(1))
            if not text:
                continue
            try:
                value = parse_price(text)
            except ValueError:
                continue
            if value > 0:
                return value
    return None


def _extract_title(html: str) -> str | None:
    for pattern in (_PRODUCT_TITLE_RE, _MOBILE_TITLE_RE):
        match = pattern.search(html)
        if match is not None:
            title = _clean_text(match.group(1))
            if title:
                return title
    return None


def _extract_prices(html: str) -> tuple[float | None, float | None]:
    for anchor in _PRICE_ANCHORS:
        region = _element_region(html, anchor)
        if region is None:
            continue
        price = _first_price(region)
        if price is None:
            continue
        struck = _strike_price(region)
        list_price = struck if struck is not None and struck > price else None
        return price, list_price
    return None, None


def _extract_stock(html: str) -> bool:
    region = _element_region(html, "availability")
    if region is None:
        return True
    text = _clean_text(region).lower()
    if not text:
        return True
    return "stokta" in text and "stokta yok" not in text


def _card_title(segment: str) -> str | None:
    match = _H2_ARIA_RE.search(segment)
    if match is not None:
        title = " ".join(html_lib.unescape(match.group(1)).split())
        if title:
            return title
    block = _H2_BLOCK_RE.search(segment)
    if block is not None:
        span = _FIRST_SPAN_RE.search(block.group(1))
        if span is not None:
            title = _clean_text(span.group(1))
            if title:
                return title
    return None


def _card_price(segment: str) -> float | None:
    for match in _OFFSCREEN_RE.finditer(segment):
        text = _clean_text(match.group(1))
        if not text:
            continue
        try:
            value = parse_price(text)
        except ValueError:
            continue
        if value > 0:
            return value
    return None


class AmazonAdapter:
    site = "amazon"

    def parse_product_url(self, url: str) -> ProductRef | None:
        parts = urlsplit(url)
        host = parts.netloc.lower().split("@")[-1].split(":")[0]
        if host != "amazon.com.tr" and not host.endswith(".amazon.com.tr"):
            return None
        path = parts.path
        match = _DP_PATH_RE.search(path) or _GP_PATH_RE.search(path)
        if match is None:
            return None
        asin = match.group(1).upper()
        if _ASIN_RE.fullmatch(asin) is None:
            return None
        return ProductRef(
            site=self.site,
            product_id=asin,
            url=f"{BASE_URL}/dp/{asin}",
        )

    async def fetch_product(self, ref: ProductRef) -> Offer:
        result = await fetch_mod.fetch(ref.url, mobile=True)
        html = result.text
        title = _extract_title(html)
        if not title:
            raise ParseError("amazon: product title anchor not found")
        price, list_price = _extract_prices(html)
        if price is None:
            raise ParseError("amazon: product price anchor not found")
        return Offer(
            site=self.site,
            title=title,
            price=price,
            list_price=list_price,
            url=ref.url,
            product_id=ref.product_id,
            variant_id=None,
            merchant=None,
            in_stock=_extract_stock(html),
            coupon_price=False,
            barcode=None,
            brand=None,
        )

    async def search(self, query: str) -> list[Offer]:
        url = SEARCH_URL + quote_plus(query)
        result = await fetch_mod.fetch(url)
        offers: list[Offer] = []
        seen: set[str] = set()
        for segment in result.text.split('data-asin="')[1:]:
            match = _CARD_ASIN_RE.match(segment)
            if match is None:
                continue
            asin = match.group(1)
            if asin in seen:
                continue
            title = _card_title(segment)
            price = _card_price(segment)
            if not title or price is None:
                continue
            seen.add(asin)
            offers.append(
                Offer(
                    site=self.site,
                    title=title,
                    price=price,
                    list_price=None,
                    url=f"{BASE_URL}/dp/{asin}",
                    product_id=asin,
                    variant_id=None,
                    merchant=None,
                    in_stock=True,
                    coupon_price=False,
                    barcode=None,
                    brand=None,
                )
            )
        if not offers:
            raise ParseError("amazon: no search result cards parsed")
        return offers

    def affiliate_url(self, offer: Offer) -> str:
        return _affiliate_url(self.site, offer.url)