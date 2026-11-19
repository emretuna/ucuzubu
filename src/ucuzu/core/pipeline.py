"""Comparison pipeline: resolve → origin → fan-out search → match → re-verify → rank."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import replace
from urllib.parse import urlsplit

from ucuzu.core import exa, links, match
from ucuzu.core.cache import Cache
from ucuzu.core.config import settings
from ucuzu.core.fetch import BlockedError, FetchError
from ucuzu.core.models import ComparisonOutcome, ComparisonResult, Offer
from ucuzu.sites.base import ADAPTERS, ParseError, site_for_host

logger = logging.getLogger(__name__)


_SITE_ERRORS = (ParseError, BlockedError, FetchError, ValueError, KeyError, TypeError)
_WS_RE = re.compile(r"\s+")
_SPEC_NOISE = {"devir", "sarim", "surf", "olta", "makine", "makinesi", "makinasi", "cm"}


def build_query(title: str) -> str:
    q = _WS_RE.sub(" ", title).strip()
    if len(q) > 80:
        cut = q[:80]
        if " " in cut:
            cut = cut[: cut.rfind(" ")]
        q = cut
    return q


def build_model_query(title: str) -> str:
    """Brand + model identity tokens, no spec noise. Trendyol's search ranks the
    exact product much better with 'Shimano Power Aero 14000 XSC PG' than with
    the full spec-laden title."""
    kept = []
    for t in match.normalize(title).split():
        if t in _SPEC_NOISE:
            continue
        if t.replace(".", "").isdigit():
            # keep integer model numbers (14000), drop ratios/sizes (3.5, 84)
            if "." not in t and len(t) >= 4:
                kept.append(t)
            continue
        kept.append(t)
    return " ".join(kept[:8])


async def _cached_product(cache: Cache | None, adapter, ref) -> Offer:
    if cache is not None:
        hit = await cache.get_product(ref.site, ref.product_id)
        if hit is not None:
            return hit
    offer = await adapter.fetch_product(ref)
    if cache is not None:
        await cache.set_product(offer, product_id=ref.product_id)
    return offer


async def _cached_search(cache: Cache | None, adapter, query: str) -> list[Offer]:
    if cache is not None:
        hit = await cache.get_search(adapter.site, query)
        if hit is not None:
            return hit
    offers = await adapter.search(query)
    if cache is not None:
        await cache.set_search(adapter.site, query, offers)
    return offers


async def _reverify(cand: Offer, cache: Cache | None) -> Offer:
    if cand.site not in ("hepsiburada", "trendyol"):
        return cand
    ad = ADAPTERS[cand.site]
    ref = ad.parse_product_url(cand.url)
    if ref is None:
        return cand
    try:
        verified = await _cached_product(cache, ad, ref)
    except _SITE_ERRORS as exc:
        logger.warning("%s re-verify failed for %s: %s", cand.site, cand.product_id, exc)
        return cand  # keep search price
    # Preserve search identity when PDP id shape differs (sku vs productId).
    return Offer(
        site=cand.site,
        title=verified.title,
        price=verified.price,
        list_price=verified.list_price,
        url=cand.url,
        product_id=cand.product_id,
        variant_id=cand.variant_id,
        merchant=verified.merchant,
        in_stock=verified.in_stock,
        coupon_price=verified.coupon_price,
        barcode=verified.barcode or cand.barcode,
        brand=verified.brand or cand.brand,
    )


def _link_only(cand: exa.ExaCandidate, site: str, product_id: str) -> Offer:
    """Unverified alternative: the URL is real, the price is best-effort (0.0 = unknown)."""
    return Offer(
        site=site,
        title=cand.title or "Ürün sayfası",
        price=cand.price or 0.0,
        list_price=None,
        url=cand.url,
        product_id=product_id,
        variant_id=None,
        merchant=None,
        in_stock=True,
        coupon_price=False,
        barcode=None,
        brand=None,
    )


async def _exa_fallback(origin: Offer, cache: Cache | None) -> list[Offer]:
    """Web-search alternatives, verify each through its site adapter.

    Kept in Exa relevance order (not price-sorted): these are unverified
    alternatives, and a link-only hit has no comparable price.
    """
    cands = await exa.search_alternatives(build_query(origin.title))
    offers: list[Offer] = []
    for cand in cands:
        site = site_for_host(urlsplit(cand.url).netloc)
        if site is None:
            continue
        adapter = ADAPTERS.get(site)
        if adapter is None:
            continue
        ref = adapter.parse_product_url(cand.url)
        product_id = ref.product_id if ref is not None else cand.url
        if (site, product_id) == (origin.site, origin.product_id):
            continue
        if ref is None:
            offers.append(_link_only(cand, site, product_id))
            continue
        try:
            pdp = await _cached_product(cache, adapter, ref)
        except _SITE_ERRORS as exc:
            logger.warning("exa candidate %s fetch failed: %s", cand.url, exc)
            offers.append(_link_only(cand, site, product_id))
            continue
        verdict = match.classify(origin, pdp)
        if verdict == "discard":
            continue
        if verdict == "exact":
            try:
                pdp = await _reverify(pdp, cache)
            except _SITE_ERRORS as exc:
                logger.warning("exa candidate %s re-verify failed: %s", cand.url, exc)
        offers.append(pdp)

    dedup: dict[tuple[str, str], Offer] = {}
    for offer in offers:
        dedup.setdefault((offer.site, offer.product_id), offer)
    return list(dedup.values())[:3]


async def compare(
    source_url: str,
    cache: Cache | None = None,
    on_fallback: Callable[[], Awaitable[None]] | None = None,
) -> ComparisonOutcome:
    # 1. Resolve (link cache when available).
    canonical = None
    if cache is not None:
        canonical = await cache.get_link(source_url)
    if canonical is None:
        canonical = await links.resolve(source_url)
        if cache is not None:
            await cache.set_link(source_url, canonical)

    if cache is not None:
        hit = await cache.get_comparison(canonical)
        if hit is not None:
            return ComparisonOutcome(result=hit[0], cache_hit=True, created_at=hit[1])

    adapter = None
    for site, ad in ADAPTERS.items():
        ref = ad.parse_product_url(canonical)
        if ref is not None:
            adapter, origin_ref = ad, ref
            break
    if adapter is None:
        raise links.UnsupportedLinkError(f"no adapter for {canonical}")

    origin = await _cached_product(cache, adapter, origin_ref)

    # 2-3. Query + fan-out search across all four sites.
    query = build_query(origin.title)
    site_status: dict[str, str] = {}

    async def run_search(ad):
        try:
            offers = await _cached_search(cache, ad, query)
            site_status[ad.site] = "ok"
            return offers
        except _SITE_ERRORS as exc:
            logger.warning("%s search failed: %s", ad.site, exc, exc_info=True)
            site_status[ad.site] = "error"
            return []

    results = await asyncio.gather(*(run_search(ad) for ad in ADAPTERS.values()))

    # Trendyol abbreviates search titles ('Power Aero 14000 XSC Olta Makinası'),
    # so title similarity cannot confirm identity. Probe top TY candidate PDPs
    # for barcodes. When the origin lacks one (n11/Amazon origins), adopt a
    # candidate barcode ONLY if an independent non-TY offer carries a matching
    # barcode — never self-confirm a TY candidate against itself.
    ty_ad = ADAPTERS.get("trendyol")
    if ty_ad is not None and site_status.get("trendyol") == "ok":
        ty_idx = list(ADAPTERS).index("trendyol")
        ty_offers = results[ty_idx]
        # Full-title queries rank TY's exact listing poorly; a model-focused
        # query surfaces it. Merge both result sets (dedupe by product_id).
        model_query = build_model_query(origin.title)
        if model_query and model_query.lower() != query.lower():
            try:
                extra = await _cached_search(cache, ty_ad, model_query)
                have = {o.product_id for o in ty_offers}
                ty_offers = ty_offers + [o for o in extra if o.product_id not in have]
                results[ty_idx] = ty_offers
            except _SITE_ERRORS as exc:
                logger.warning("trendyol model-query search failed: %s", exc)
        probe = getattr(ty_ad, "probe_barcodes", None)
        plausible = sorted(
            (o for o in ty_offers if match.similarity(origin.title, o.title) >= 0.3),
            key=lambda o: match.similarity(origin.title, o.title),
            reverse=True,
        )
        if probe is not None and plausible:
            try:
                probed = {o.product_id: o for o in await probe(plausible)}
                ty_offers = [probed.get(o.product_id, o) for o in ty_offers]
                results[ty_idx] = ty_offers
            except _SITE_ERRORS as exc:
                logger.warning("trendyol barcode probe failed: %s", exc)
        # HB search offers carry no barcode; enrich top title-exact HB
        # candidates via PDP so they can corroborate TY barcode adoption.
        hb_ad = ADAPTERS.get("hepsiburada")
        if origin.barcode is None and hb_ad is not None and site_status.get("hepsiburada") == "ok":
            hb_idx = list(ADAPTERS).index("hepsiburada")
            hb_offers = results[hb_idx]
            origin_models = match.model_tokens(origin.title)
            hb_plausible = sorted(
                (
                    o
                    for o in hb_offers
                    if match.similarity(origin.title, o.title) >= 0.85
                    and (not origin_models or match.model_tokens(o.title) == origin_models)
                ),
                key=lambda o: match.similarity(origin.title, o.title),
                reverse=True,
            )[:8]

            async def hb_barcode(o: Offer) -> Offer:
                ref = hb_ad.parse_product_url(o.url)
                if ref is None:
                    return o
                try:
                    pdp = await _cached_product(cache, hb_ad, ref)
                except _SITE_ERRORS as exc:
                    logger.warning("hepsiburada PDP enrichment failed for %s: %s", o.product_id, exc)
                    return o
                return replace(o, barcode=pdp.barcode) if pdp.barcode else o

            if hb_plausible:
                enriched = {o.product_id: o for o in await asyncio.gather(*(hb_barcode(o) for o in hb_plausible))}
                results[hb_idx] = [enriched.get(o.product_id, o) for o in hb_offers]
        if origin.barcode is None:
            others = [
                o
                for i, offers in enumerate(results)
                if i != ty_idx
                for o in offers
                if o.barcode
            ]
            for cand in sorted(
                (o for o in ty_offers if o.barcode),
                key=lambda o: match.similarity(origin.title, o.title),
                reverse=True,
            ):
                if match.similarity(origin.title, cand.title) < 0.3:
                    continue
                cand_key = match._barcode_key(cand.barcode)
                if cand_key and any(
                    (key := match._barcode_key(o.barcode)) is not None
                    and match._barcode_match(cand_key, key)
                    for o in others
                ):
                    origin = replace(origin, barcode=cand.barcode)
                    break

    # 4. Matching.
    exact: list[Offer] = []
    similar: list[Offer] = []
    seen: set[tuple[str, str]] = set()
    for offers in results:
        for cand in offers:
            key = (cand.site, cand.product_id)
            if key in seen:
                continue
            seen.add(key)
            verdict = match.classify(origin, cand)
            if verdict == "exact":
                exact.append(cand)
            elif verdict == "similar":
                similar.append(cand)

    # 5. Re-verify HB/TY exact matches via PDP (authoritative buybox price).
    if exact:
        exact = list(await asyncio.gather(*(_reverify(c, cache) for c in exact)))

    # Dedupe again after re-verification (same product surfaced twice).
    dedup: dict[tuple[str, str], Offer] = {}
    for cand in exact:
        key = (cand.site, cand.product_id)
        if key not in dedup or cand.price < dedup[key].price:
            dedup[key] = cand
    exact = sorted(dedup.values(), key=lambda o: o.price)

    in_stock = [o for o in exact if o.in_stock]
    cheapest = min(in_stock, key=lambda o: o.price) if in_stock else None
    similar = sorted(similar, key=lambda o: o.price)[:3]

    fallback: list[Offer] = []
    if not in_stock and not similar and settings.exa_api_key.strip():
        if on_fallback is not None:
            await on_fallback()
        fallback = await _exa_fallback(origin, cache)

    result = ComparisonResult(
        origin=origin,
        offers=exact,
        exact=exact,
        similar=similar,
        site_status=site_status,
        cheapest=cheapest,
        fallback=fallback,
    )
    if cache is not None:
        await cache.set_comparison(canonical, result)
    return ComparisonOutcome(result=result)