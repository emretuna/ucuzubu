"""Hepsiburada adapter: product page + search page via embedded reduxStore JSON.

Anchors:
  product page: <script type="mime/invalid" id="reduxStore"> → productState.product
  search page:  same script → voltranState.fragmentsMap[*].html containing
                window.MORIA.PRODUCTLIST → 'STATE': {…} → data.products[]
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator
from urllib.parse import quote_plus, urlsplit

from ucuzu.core import fetch as fetch_mod
from ucuzu.core.affiliate import affiliate_url as _affiliate_url
from ucuzu.core.models import Offer, ProductRef
from ucuzu.sites.base import ParseError, parse_price, site_for_host

BASE_URL = "https://www.hepsiburada.com"
SEARCH_URL = BASE_URL + "/ara?q="

_REDUX_RE = re.compile(
    r'<script[^>]*\bid=["\']reduxStore["\'][^>]*>(.*?)</script>', re.DOTALL
)
_PRODUCTLIST_MARKER = "window.MORIA.PRODUCTLIST"
_STATE_KEY_RE = re.compile(r"'STATE':\s*")

_P_SKU_RE = re.compile(r"-p-(HBC[A-Z0-9]{9,12})/?$")
_PM_ID_RE = re.compile(r"-pm-([A-Za-z0-9]+)/?$")


def _load_redux(text: str) -> dict[str, Any]:
    match = _REDUX_RE.search(text)
    if not match:
        raise ParseError("hepsiburada: reduxStore script not found")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ParseError(f"hepsiburada: reduxStore JSON invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise ParseError("hepsiburada: reduxStore is not an object")
    return data


def _walk_fragments(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_fragments(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_fragments(value)


def _find_product_list_html(data: dict[str, Any]) -> str:
    fragments = (data.get("voltranState") or {}).get("fragmentsMap") or {}
    for node in _walk_fragments(fragments):
        html = node.get("html")
        if isinstance(html, str) and _PRODUCTLIST_MARKER in html:
            return html
    raise ParseError("hepsiburada: PRODUCTLIST fragment not found")


def _search_state(html: str) -> dict[str, Any]:
    marker = _STATE_KEY_RE.search(html)
    if marker is None:
        raise ParseError("hepsiburada: PRODUCTLIST STATE not found")
    try:
        state, _ = json.JSONDecoder().raw_decode(html, marker.end())
    except json.JSONDecodeError as exc:
        raise ParseError(f"hepsiburada: PRODUCTLIST STATE invalid: {exc}") from exc
    if not isinstance(state, dict):
        raise ParseError("hepsiburada: PRODUCTLIST STATE is not an object")
    return state


class HepsiburadaAdapter:
    site = "hepsiburada"

    def parse_product_url(self, url: str) -> ProductRef | None:
        if not url:
            return None
        parts = urlsplit(url.strip())
        if site_for_host(parts.netloc) != "hepsiburada":
            return None
        path = parts.path
        if not path.startswith("/"):
            return None

        match = _P_SKU_RE.search(path)
        if match:
            product_id = match.group(1)
        else:
            match = _PM_ID_RE.search(path)
            if not match:
                return None
            product_id = match.group(1)

        return ProductRef(
            site=self.site,
            product_id=product_id,
            url=BASE_URL + path,
        )

    async def fetch_product(self, ref: ProductRef) -> Offer:
        page = await fetch_mod.fetch(ref.url, mobile=True)
        data = _load_redux(page.text)
        product = (data.get("productState") or {}).get("product")
        if not isinstance(product, dict):
            raise ParseError("hepsiburada: productState.product not found")

        name = product.get("name")
        prices = product.get("prices")
        if not isinstance(name, str) or not name.strip():
            raise ParseError("hepsiburada: product name missing")
        if not isinstance(prices, list) or not prices:
            raise ParseError("hepsiburada: product prices missing")
        first_price = prices[0]
        if not isinstance(first_price, dict) or first_price.get("value") is None:
            raise ParseError("hepsiburada: product price missing")

        brand = product.get("brand")
        brand = brand.strip() if isinstance(brand, str) and brand.strip() else None
        title = f"{brand} {name.strip()}" if brand else name.strip()
        product_id = product.get("sku") or product.get("productId")
        if not product_id:
            raise ParseError("hepsiburada: product sku/productId missing")
        merchant = product.get("merchant")
        merchant_name = merchant.get("name") if isinstance(merchant, dict) else None

        barcode = product.get("barcode")
        barcode = barcode.strip() if isinstance(barcode, str) and barcode.strip() else None

        return Offer(
            site=self.site,
            title=title,
            price=parse_price(first_price["value"]),
            list_price=None,
            url=ref.url,
            product_id=str(product_id),
            variant_id=None,
            merchant=merchant_name or None,
            in_stock=bool(product.get("isInStock")),
            coupon_price=False,
            barcode=barcode,
            brand=brand,
        )

    async def search(self, query: str) -> list[Offer]:
        page = await fetch_mod.fetch(SEARCH_URL + quote_plus(query))
        data = _load_redux(page.text)
        state = _search_state(_find_product_list_html(data))

        products = (state.get("data") or {}).get("products")
        if not isinstance(products, list):
            raise ParseError("hepsiburada: search products missing")

        offers: list[Offer] = []
        for product in products:
            if not isinstance(product, dict):
                continue
            brand = product.get("brand")
            brand = brand.strip() if isinstance(brand, str) and brand.strip() else None
            for variant in product.get("variantList") or []:
                if not isinstance(variant, dict):
                    continue
                listing = variant.get("listing")
                if not isinstance(listing, dict):
                    continue
                price_info = listing.get("priceInfo")
                if not isinstance(price_info, dict) or price_info.get("price") is None:
                    continue
                sku = variant.get("sku")
                variant_url = variant.get("url")
                variant_name = variant.get("name")
                if not sku or not variant_url or not variant_name:
                    continue
                offers.append(
                    Offer(
                        site=self.site,
                        title=variant_name.strip(),
                        price=parse_price(price_info["price"]),
                        list_price=None,
                        url=BASE_URL + urlsplit(variant_url).path,
                        product_id=str(sku),
                        variant_id=str(sku),
                        merchant=listing.get("merchantName") or None,
                        in_stock=bool(variant.get("procurable", True)),
                        coupon_price=False,
                        barcode=None,
                        brand=brand,
                    )
                )
        return offers

    def affiliate_url(self, offer: Offer) -> str:
        return _affiliate_url(self.site, offer.url)