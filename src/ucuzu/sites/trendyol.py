"""Trendyol adapter: product page (PDP) and search results.

Anchors:
  PDP:    <script>window["__envoy__SHARED_PROPS"]={...}</script>
  Search: <script>window["__single-search-result__PROPS"]={...}</script>

PDP prices: winnerVariant.price.discountedPrice is the coupon-inclusive price
shown to everyone; sellingPrice is the list price. tyPlusCouponApplicablePrice
is membership-only and MUST NOT be used.
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import quote_plus, urlsplit

from ucuzu.core import fetch
from ucuzu.core.affiliate import affiliate_url
from ucuzu.core.models import Offer, ProductRef
from ucuzu.sites.base import ParseError

SITE = "trendyol"
BASE_URL = "https://www.trendyol.com"
SEARCH_URL = "https://www.trendyol.com/sr?q={query}"

# /<brand>/<slug>-p-<digits> — the brand segment is required (URL without it 404s).
_PRODUCT_PATH_RE = re.compile(r"^/(?P<brand>[^/]+)/(?P<slug>[^/]+?)-p-(?P<id>\d+)/?$")

_PDP_ANCHOR_RE = re.compile(r'<script[^>]*>\s*window\["__envoy__SHARED_PROPS"\]\s*=\s*')
_PDP_MOBILE_ANCHOR_RE = re.compile(
    r'<script[^>]*>\s*window\["__envoy-mobile__SHARED_PROPS"\]\s*=\s*'
)
_SEARCH_ANCHOR_RE = re.compile(
    r'<script[^>]*>\s*window\["__single-search-result__PROPS"\]\s*=\s*'
)


def _json_after(pattern: re.Pattern[str], html: str, what: str) -> dict:
    match = pattern.search(html)
    if match is None:
        raise ParseError(f"trendyol: {what} anchor not found")
    try:
        body, _end = json.JSONDecoder().raw_decode(html, match.end())
    except json.JSONDecodeError as exc:
        raise ParseError(f"trendyol: {what} JSON invalid: {exc}") from exc
    if not isinstance(body, dict):
        raise ParseError(f"trendyol: {what} payload is not an object")
    return body


def _dig(node: object, *keys: str) -> object:
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _value(node: object, what: str) -> float:
    """Extract a numeric price from {'value': x} or a bare number."""
    raw = node.get("value") if isinstance(node, dict) else node
    if isinstance(raw, (int, float)):
        return float(raw)
    raise ParseError(f"trendyol: missing price field {what}")


def _brand_prefix(title: str, brand: str | None) -> str:
    if not brand:
        return title
    if title.lower().startswith(brand.lower()):
        return title
    return f"{brand} {title}"


class TrendyolAdapter:
    site = SITE

    # -- URL parsing -------------------------------------------------------
    def parse_product_url(self, url: str) -> ProductRef | None:
        parts = urlsplit(url)
        host = parts.netloc.lower().split(":")[0]
        if not (host == "trendyol.com" or host.endswith(".trendyol.com")):
            return None
        match = _PRODUCT_PATH_RE.match(parts.path)
        if match is None:
            return None
        product_id = match.group("id")
        canonical = f"{BASE_URL}/{match.group('brand')}/{match.group('slug')}-p-{product_id}"
        return ProductRef(site=SITE, product_id=product_id, url=canonical)

    # -- Product page ------------------------------------------------------
    async def fetch_product(self, ref: ProductRef) -> Offer:
        result = await fetch.fetch(ref.url, mobile=True)
        if result.status != 200:
            raise ParseError(f"trendyol: product page HTTP {result.status}")
        try:
            body = _json_after(_PDP_ANCHOR_RE, result.text, "__envoy__SHARED_PROPS")
        except ParseError:
            # Mobile UA now serves the envoy-mobile layout; same product shape.
            body = _json_after(
                _PDP_MOBILE_ANCHOR_RE, result.text, "__envoy-mobile__SHARED_PROPS"
            )
        product = body.get("product")
        if not isinstance(product, dict):
            raise ParseError("trendyol: product missing from __envoy__SHARED_PROPS")

        listing = product.get("merchantListing")
        if not isinstance(listing, dict):
            raise ParseError("trendyol: merchantListing missing")
        price_node = _dig(listing, "winnerVariant", "price")
        if not isinstance(price_node, dict):
            raise ParseError("trendyol: winnerVariant.price missing")

        price = _value(price_node.get("discountedPrice"), "discountedPrice")
        list_price = _value(price_node.get("sellingPrice"), "sellingPrice")

        brand = _dig(product, "brand", "name")
        name = product.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ParseError("trendyol: product name missing")

        variants = product.get("variants")
        barcode = None
        if isinstance(variants, list) and variants:
            first = variants[0]
            if isinstance(first, dict):
                raw_barcode = first.get("barcode")
                barcode = str(raw_barcode) if raw_barcode else None

        product_id = product.get("id")
        if product_id is None:
            raise ParseError("trendyol: product id missing")

        merchant = _dig(listing, "merchant", "name")

        return Offer(
            site=SITE,
            title=_brand_prefix(" ".join(name.split()), brand if isinstance(brand, str) else None),
            price=price,
            list_price=list_price,
            url=ref.url,
            product_id=str(product_id),
            variant_id=None,
            merchant=str(merchant) if merchant else None,
            in_stock=bool(product.get("inStock", True)),
            coupon_price=price < list_price,
            barcode=barcode,
            brand=brand if isinstance(brand, str) and brand else None,
        )

    # -- Search ------------------------------------------------------------
    async def search(self, query: str) -> list[Offer]:
        url = SEARCH_URL.format(query=quote_plus(query))
        result = await fetch.fetch(url)
        if result.status != 200:
            raise ParseError(f"trendyol: search page HTTP {result.status}")
        body = _json_after(_SEARCH_ANCHOR_RE, result.text, "__single-search-result__PROPS")

        products = body.get("products")
        if products is None:
            data = body.get("data")
            products = data.get("products") if isinstance(data, dict) else None
        if not isinstance(products, list):
            raise ParseError("trendyol: search products list missing")

        offers: list[Offer] = []
        for item in products:
            offer = self._search_offer(item)
            if offer is not None:
                offers.append(offer)
        return offers

    async def probe_barcodes(self, offers: list[Offer], limit: int = 4) -> list[Offer]:
        """Fetch PDPs of top search candidates to recover barcodes.

        TY search titles are abbreviated ('Power Aero 14000 XSC Olta Makinası'),
        so title similarity alone cannot confirm identity; the PDP barcode can.
        Failures keep the original offer.
        """

        async def one(offer: Offer) -> Offer:
            ref = self.parse_product_url(offer.url)
            if ref is None:
                return offer
            try:
                pdp = await self.fetch_product(ref)
            except Exception:
                return offer
            if not pdp.barcode:
                return offer
            return Offer(
                site=offer.site,
                title=offer.title,
                price=offer.price,
                list_price=offer.list_price,
                url=offer.url,
                product_id=offer.product_id,
                variant_id=offer.variant_id,
                merchant=offer.merchant,
                in_stock=offer.in_stock,
                coupon_price=offer.coupon_price,
                barcode=pdp.barcode,
                brand=offer.brand,
            )

        head, tail = offers[:limit], offers[limit:]
        probed = await asyncio.gather(*(one(o) for o in head))
        return list(probed) + tail

    @staticmethod
    def _search_offer(item: object) -> Offer | None:
        if not isinstance(item, dict):
            return None
        product_id = item.get("id")
        name = item.get("name")
        path = item.get("url")
        price_node = item.get("price")
        if product_id is None or not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(path, str) or "-p-" not in path:
            return None
        if not isinstance(price_node, dict):
            return None

        current = price_node.get("current")
        if not isinstance(current, (int, float)):
            return None
        discounted = price_node.get("discountedPrice")
        if isinstance(discounted, (int, float)) and discounted < current:
            price, list_price, coupon = float(discounted), float(current), True
        else:
            price, list_price, coupon = float(current), None, False

        brand = item.get("brand")
        brand = brand if isinstance(brand, str) and brand else None
        clean_path = path.split("?", 1)[0].split("#", 1)[0]
        if not clean_path.startswith("/"):
            clean_path = "/" + clean_path

        return Offer(
            site=SITE,
            title=_brand_prefix(" ".join(name.split()), brand),
            price=price,
            list_price=list_price,
            url=f"{BASE_URL}{clean_path}",
            product_id=str(product_id),
            variant_id=None,
            merchant=None,
            in_stock=True,
            coupon_price=coupon,
            barcode=None,
            brand=brand,
        )

    # -- Affiliate ---------------------------------------------------------
    def affiliate_url(self, offer: Offer) -> str:
        return affiliate_url(self.site, offer.url)