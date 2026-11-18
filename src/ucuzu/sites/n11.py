"""n11.com adapter.

Product identity is the numeric id at the end of a canonical product URL
(`/urun/<slug>-<digits>`). Note: the ItemList JSON-LD on search pages carries
`sku` (the SKU id, e.g. 127410465669) *and* a product URL whose trailing number
is the product-group id used by product pages (e.g. 83521276) — identity uses
the URL number so search hits match the origin product's `product_id`.

Data sources:
  * product page: ``window.model = {...};</script>`` → ``data.product``
  * search page:  ``<script type="application/ld+json">`` → ``@type == "ItemList"``
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote_plus, urlsplit

from ucuzu.core.affiliate import affiliate_url as wrap_affiliate_url
from ucuzu.core.fetch import fetch
from ucuzu.core.links import canonicalize
from ucuzu.core.models import Offer, ProductRef
from ucuzu.sites.base import ParseError, parse_price

SITE = "n11"
SEARCH_URL = "https://www.n11.com/arama?q={query}"

_MODEL_RE = re.compile(r"window\.model\s*=\s*(\{.*?\});?\s*</script>", re.DOTALL)
_PRODUCT_PATH_RE = re.compile(r"^/urun/[^/]+-(\d+)/?$")
_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)


def _item_id_from_url(url: str) -> str | None:
    m = _PRODUCT_PATH_RE.match(urlsplit(url).path)
    return m.group(1) if m else None


def _load_ld_blocks(html: str) -> list[object]:
    docs: list[object] = []
    for raw in _LD_RE.findall(html):
        raw = raw.strip()
        if not raw:
            continue
        try:
            docs.append(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            continue
    return docs


def _iter_item_lists(docs: list[object]):
    """Yield ItemList nodes, including ones nested in @graph containers."""
    stack = list(docs)
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("@type") == "ItemList":
                yield node
            for key in ("@graph", "itemListElement"):
                child = node.get(key)
                if isinstance(child, list):
                    stack.extend(child)
                elif isinstance(child, dict):
                    stack.append(child)
        elif isinstance(node, list):
            stack.extend(node)


def _barcode(product: dict) -> str | None:
    gtin = product.get("defaultGtin")
    if gtin:
        return str(gtin)
    for sku in product.get("skus") or []:
        if isinstance(sku, dict):
            gtin = sku.get("gtin")
            if gtin:
                return str(gtin)
    return None


def _optional_price(value: object) -> float | None:
    if isinstance(value, str) and not value.strip():
        return None
    if value is None:
        return None
    try:
        return parse_price(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class N11Adapter:
    site = SITE

    def parse_product_url(self, url: str) -> ProductRef | None:
        parts = urlsplit(url)
        host = parts.netloc.lower().split(":")[0]
        if host not in ("n11.com", "www.n11.com") and not host.endswith(".n11.com"):
            return None
        product_id = _item_id_from_url(url)
        if product_id is None:
            return None
        return ProductRef(
            site=SITE,
            product_id=product_id,
            url=canonicalize(url),
        )

    async def fetch_product(self, ref: ProductRef) -> Offer:
        result = await fetch(ref.url, mobile=True)
        match = _MODEL_RE.search(result.text)
        if not match:
            raise ParseError(f"n11: window.model not found in {ref.url}")
        try:
            model = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ParseError(f"n11: window.model is not valid JSON: {exc}") from exc

        product = (model.get("data") or {}).get("product")
        if not isinstance(product, dict):
            raise ParseError(f"n11: data.product missing in {ref.url}")

        title = product.get("title") or product.get("name")
        if not title:
            raise ParseError(f"n11: product title missing in {ref.url}")

        list_price = _optional_price(product.get("displayPriceFloat"))
        parsed_price = _optional_price(product.get("price"))
        price = parsed_price if parsed_price is not None else list_price
        if price is None:
            raise ParseError(f"n11: no price in {ref.url}")

        seller = product.get("seller")
        merchant = None
        if isinstance(seller, dict) and seller.get("id") is not None:
            merchant = str(seller["id"])

        return Offer(
            site=SITE,
            title=str(title).strip(),
            price=price,
            list_price=list_price,
            url=canonicalize(ref.url),
            product_id=ref.product_id,
            variant_id=None,
            merchant=merchant,
            in_stock=bool(product.get("availableToBuy")),
            coupon_price=(
                parsed_price is not None
                and list_price is not None
                and parsed_price < list_price
            ),
            barcode=_barcode(product),
            brand=product.get("productBrand") or None,
        )

    async def search(self, query: str) -> list[Offer]:
        url = SEARCH_URL.format(query=quote_plus(query))
        result = await fetch(url)
        item_lists = list(_iter_item_lists(_load_ld_blocks(result.text)))
        if not item_lists:
            raise ParseError(f"n11: no ItemList JSON-LD in search results for {query!r}")

        offers: list[Offer] = []
        for item_list in item_lists:
            for entry in item_list.get("itemListElement") or []:
                if not isinstance(entry, dict):
                    continue
                item = entry.get("item")
                if not isinstance(item, dict):
                    continue
                offer = self._offer_from_item(item)
                if offer is not None:
                    offers.append(offer)

        if not offers:
            raise ParseError(f"n11: no parsable items in search results for {query!r}")
        return offers

    def _offer_from_item(self, item: dict) -> Offer | None:
        raw_url = item.get("url")
        name = item.get("name")
        if not raw_url or not name:
            return None
        url = canonicalize(str(raw_url))
        product_id = _item_id_from_url(url) or (
            str(item["sku"]) if item.get("sku") else None
        )
        if product_id is None:
            return None

        offers = item.get("offers")
        if not isinstance(offers, dict):
            return None
        price = _optional_price(offers.get("price"))
        if price is None:
            return None

        spec = offers.get("priceSpecification")
        list_price = _optional_price(spec.get("price")) if isinstance(spec, dict) else None

        brand = item.get("brand")
        brand_name = brand.get("name") if isinstance(brand, dict) else brand

        return Offer(
            site=SITE,
            title=str(name).strip(),
            price=price,
            list_price=list_price,
            url=url,
            product_id=product_id,
            variant_id=None,
            merchant=None,
            in_stock=True,
            coupon_price=(
                list_price is not None and price < list_price
            ),
            barcode=None,
            brand=str(brand_name) if brand_name else None,
        )

    def affiliate_url(self, offer: Offer) -> str:
        return wrap_affiliate_url(SITE, offer.url)


adapter = N11Adapter()