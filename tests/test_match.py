"""Unit tests for ucuzu.core.match — normalization, Jaccard similarity, classification.

Regression spec = the dry-run table for the test product
"Shimano Power Aero 14000 XSC PG 3.5 Devir 84cm Sarım Surf Makinesi".
Thresholds (0.85 exact / 0.50 similar) are fixed by contract and are NOT tuned here;
places where a legitimate wording variant lands below 0.85 are asserted as-is and
flagged in comments.
"""

from __future__ import annotations

import pytest

from ucuzu.core.match import classify, model_tokens, normalize, similarity
from ucuzu.core.models import Offer

ORIGIN_TITLE = "Shimano Power Aero 14000 XSC PG 3.5 Devir 84cm Sarım Surf Makinesi"


def offer(
    *,
    site: str = "hepsiburada",
    title: str = ORIGIN_TITLE,
    price: float = 100.0,
    product_id: str = "HBCV00008XOKU2",
    variant_id: str | None = None,
    barcode: str | None = None,
    url: str = "https://www.hepsiburada.com/x-p-HBCV00008XOKU2",
    merchant: str | None = "Hepsiburada",
    in_stock: bool = True,
    coupon_price: bool = False,
    brand: str | None = "Shimano",
) -> Offer:
    return Offer(
        site=site,
        title=title,
        price=price,
        list_price=None,
        url=url,
        product_id=product_id,
        variant_id=variant_id,
        merchant=merchant,
        in_stock=in_stock,
        coupon_price=coupon_price,
        barcode=barcode,
        brand=brand,
    )


# --------------------------------------------------------------------------- normalize


def test_normalize_folds_turkish_characters():
    assert normalize("ŞİMŞEK ĞÜÖÇ IĞDIR İĞNE ÇİÇEK") == "simsek guoc igdir igne cicek"


def test_normalize_lowercase_and_collapse_whitespace():
    assert normalize("  Çok   İYİ -- 3.5  ") == "cok iyi 3.5"


def test_normalize_keeps_only_alnum_dot_tokens():
    assert normalize("XSC-PG (84cm) [güç] %50!") == "xsc pg 84 cm guc 50"
    assert normalize("!!! ---") == ""


def test_model_tokens_are_digit_carrying_tokens():
    assert model_tokens(ORIGIN_TITLE) == {"14000", "84", "3.5"}
    assert model_tokens("Shimano Power Aero XSC PG Sarım Surf Makinesi") == set()


# ------------------------------------------------------------------------ similarity


def test_similarity_identical_titles_is_one():
    assert similarity(ORIGIN_TITLE, ORIGIN_TITLE) == 1.0


def test_similarity_empty_union_is_zero():
    assert similarity("", "") == 0.0
    assert similarity("!!!", "---") == 0.0


def test_similarity_exact_wording_variant_with_extra_category_word():
    cand = "Shimano Power Aero 14000 Xsc Pg 3.5 Devir 84cm Sarım Surf Olta Makinesi"
    assert similarity(ORIGIN_TITLE, cand) == pytest.approx(13 / 14)
    assert similarity(ORIGIN_TITLE, cand) >= 0.85
    assert classify(offer(), offer(site="trendyol", title=cand)) == "exact"


def test_typographic_wording_variant_is_exact():
    """'84 Cm' (split) + 'XSC-PG' (hyphen) tokenize identically after glued-size
    splitting and hyphen tokenization → sim 1.0, exact."""
    cand = "Shimano Power Aero 14000 XSC-PG 3.5 Devir 84 Cm Sarım Surf Makinesi"
    assert similarity(ORIGIN_TITLE, cand) == 1.0
    assert classify(offer(), offer(site="trendyol", title=cand)) == "exact"


def test_model_token_gate_kills_disjoint_models():
    # Identical wording, different model number: gate fires before Jaccard.
    assert similarity("Shimano 14000 XSC", "Shimano 4000 XSC") == 0.0
    assert (
        similarity(
            "Shimano Power Aero 14000 XSC PG Devir Sarım Surf Makinesi",
            "Shimano Power Aero 4000 XSC PG Devir Sarım Surf Makinesi",
        )
        == 0.0
    )


def test_model_number_swap_vetoed_even_on_long_titles():
    """Swapping 14000→4000 keeps shared size tokens, so Jaccard alone could cross
    0.85 on long titles. The model-token conflict veto in classify caps any
    differing digit-token set at 'similar'."""
    swapped = "Shimano Power Aero 4000 XSC PG 3.5 Devir 84cm Sarım Surf Makinesi"
    assert similarity(ORIGIN_TITLE, swapped) == pytest.approx(12 / 14)
    assert classify(offer(), offer(site="trendyol", title=swapped, price=9000.0)) == "similar"
    long_origin = ORIGIN_TITLE + " Extra Words Here Many More Tokens Added For Length"
    long_swapped = swapped + " Extra Words Here Many More Tokens Added For Length"
    assert similarity(long_origin, long_swapped) >= 0.85
    assert (
        classify(offer(title=long_origin), offer(site="trendyol", title=long_swapped))
        == "similar"
    )


def test_model_gate_does_not_fire_when_one_side_has_no_model_tokens():
    # Only one side carries digits → gate stays open, Jaccard decides.
    assert similarity(ORIGIN_TITLE, "Shimano Power Aero XSC PG Sarım Surf Makinesi") > 0.0


# ------------------------------------------------------------------------ classify


def test_wrong_model_cheap_variant_is_never_exact():
    """XTC is the cheaper wrong model: Jaccard crosses 0.85 (12/14), but the
    model-token conflict veto caps it at 'similar' — never exact/cheapest."""
    xtc = "Shimano Power Aero 14000 XTC PG 3.5 Devir 84cm Sarım Surf Makinesi"
    assert similarity(ORIGIN_TITLE, xtc) == pytest.approx(12 / 14)
    assert (
        classify(
            offer(price=37685.5),
            offer(site="trendyol", title=xtc, price=29000.0, product_id="44264045"),
        )
        == "similar"
    )


def test_wrong_model_with_different_size_is_discarded():
    # A cheaper different class of reel: model token 4000 is disjoint from
    # {14000, 35, 84} → gate forces 0.0 → discarded.
    xtc = "Shimano Nexave 4000 FE Olta Makinesi"
    assert similarity(ORIGIN_TITLE, xtc) == 0.0
    assert classify(offer(), offer(site="trendyol", title=xtc, price=29000.0)) == "discard"


def test_wrong_model_with_mid_overlap_is_similar_not_discard():
    """A wrong-model XTC variant with its own size tokens scores ~0.53,
    i.e. 'similar' — surfaced as a possibly-different product, never as exact."""
    xtc = "Shimano Power Aero 14000 XTC 4.0 Devir 100cm Sarım Surf Makinesi"
    assert similarity(ORIGIN_TITLE, xtc) == pytest.approx(9 / 16)
    assert classify(offer(), offer(site="trendyol", title=xtc, price=29000.0)) == "similar"


def test_unrelated_product_is_discarded():
    cand = "Bambu Olta Kamisi 3 Parca Seyahat Boy"
    assert similarity(ORIGIN_TITLE, cand) < 0.5
    assert classify(offer(), offer(site="n11", title=cand)) == "discard"


def test_same_site_product_id_equality_is_exact_regardless_of_title():
    assert (
        classify(
            offer(product_id="HBCV00008XOKU2"),
            offer(site="hepsiburada", title="Bambu Olta Kamisi", product_id="HBCV00008XOKU2"),
        )
        == "exact"
    )


def test_same_site_variant_id_equality_is_exact():
    assert (
        classify(
            offer(product_id="123", variant_id="PA14000XSC"),
            offer(
                site="hepsiburada",
                title="Bambu Olta Kamisi",
                product_id="999",
                variant_id="PA14000XSC",
            ),
        )
        == "exact"
    )


def test_product_id_equality_across_sites_is_not_exact():
    # Ids are only comparable within a site; cross-site id collision must not match.
    assert (
        classify(
            offer(site="hepsiburada", product_id="123"),
            offer(site="trendyol", title="Bambu Olta Kamisi", product_id="123"),
        )
        == "discard"
    )


def test_barcode_leading_zero_strip_matches():
    assert (
        classify(
            offer(barcode="0PA14000PGXSC"),
            offer(site="n11", title="Bambu Olta Kamisi", barcode="PA14000PGXSC"),
        )
        == "exact"
    )


def test_barcode_case_insensitive_match():
    assert (
        classify(
            offer(barcode="0pa14000pgxsc"),
            offer(site="n11", title="Bambu Olta Kamisi", barcode="PA14000PGXSC"),
        )
        == "exact"
    )


def test_barcode_containment_matches_manufacturer_code():
    # HB '0PA14000PGXSC' embeds TY merchant barcode 'PA14000XSC' — same product,
    # different marketplace barcode conventions → exact via containment.
    assert (
        classify(
            offer(barcode="0PA14000PGXSC"),
            offer(site="trendyol", title="Shimano Power Aero 14000 XSC Olta Makinası", barcode="PA14000XSC"),
        )
        == "exact"
    )


def test_barcode_unrelated_is_not_exact():
    assert (
        classify(
            offer(barcode="0PA14000PGXSC"),
            offer(site="trendyol", title="Bambu Olta Kamisi", barcode="W4969363090645"),
        )
        == "discard"
    )


def test_barcode_comparison_requires_both_sides_present():
    assert classify(offer(barcode=None), offer(site="trendyol", title="Bambu Olta Kamisi", barcode=None)) == "discard"
    assert classify(offer(barcode="0PA14000PGXSC"), offer(site="trendyol", title="Bambu Olta Kamisi")) == "discard"


def test_barcode_all_zero_is_treated_as_absent():
    assert (
        classify(
            offer(barcode="0000"),
            offer(site="trendyol", title="Bambu Olta Kamisi", barcode="000"),
        )
        == "discard"
    )


def test_classify_similar_window_bounds():
    # 0.50 ≤ sim < 0.85 → similar
    assert classify(offer(), offer(site="trendyol", title="Shimano Power Aero 14000 XSC PG Sarım Surf")) == "similar"


def test_model_conflict_veto_blocks_exact_above_threshold():
    # sim ≥ 0.85 but digit-token sets differ → capped at similar.
    origin = ORIGIN_TITLE + " Ekstra Ucuz Firsat Bugun Kampanya Indirim"
    cand = origin.replace("14000", "14001")
    assert similarity(origin, cand) >= 0.85
    assert classify(offer(title=origin), offer(site="trendyol", title=cand)) == "similar"


def test_equal_model_sets_pass_veto():
    cand = "Shimano Power Aero 14000 Xsc Pg 3.5 Devir 84cm Sarım Surf Olta Makinesi"
    assert classify(offer(), offer(site="n11", title=cand)) == "exact"