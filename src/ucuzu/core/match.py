"""Pure title matching: Turkish-aware normalization, Jaccard similarity, classification.

Thresholds are fixed by the project contract: exact at 0.85, similar at 0.50.
"""

from __future__ import annotations

import re

from ucuzu.core.models import Offer

# Turkish-specific case folding, applied before str.lower() so that "İ" does not
# degrade into "i" + combining dot (U+0307) and "I" does not become dotted "i" by
# ASCII rules alone.
_FOLD = {
    ord("İ"): "i",
    ord("I"): "i",
    ord("Ş"): "s",
    ord("Ğ"): "g",
    ord("Ü"): "u",
    ord("Ö"): "o",
    ord("Ç"): "c",
    ord("ı"): "i",
    ord("ş"): "s",
    ord("ğ"): "g",
    ord("ü"): "u",
    ord("ö"): "o",
    ord("ç"): "c",
}

_TOKEN_RE = re.compile(r"[a-z0-9.]+")
_GLUED_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)(cm|mm|kg|gr|ml|lt|inc)$")
_THOUSANDS_RE = re.compile(r"^\d{1,3}(?:\.\d{3})+$")

EXACT_THRESHOLD = 0.85
SIMILAR_THRESHOLD = 0.5


def normalize(s: str) -> str:
    """Lowercase, fold Turkish chars, keep [a-z0-9.] tokens; split glued sizes
    ('84cm' → '84 cm') and drop thousand-separator dots ('33.163' → '33163')
    so typographic variants of the same product tokenize identically.
    Decimal ratios ('3.5') keep their dot — they are model identity."""
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(s.translate(_FOLD).lower()):
        if _THOUSANDS_RE.match(raw):
            raw = raw.replace(".", "")
        m = _GLUED_SIZE_RE.match(raw)
        if m:
            tokens.extend(m.groups())
        elif raw:
            tokens.append(raw)
    return " ".join(tokens)


def _tokens(title: str) -> set[str]:
    return set(normalize(title).split())


def _has_digit(token: str) -> bool:
    return any(ch.isdigit() for ch in token)


def model_tokens(title: str) -> set[str]:
    """Normalized tokens carrying a digit (model numbers, sizes, ratios)."""
    return {token for token in _tokens(title) if _has_digit(token)}


def similarity(origin_title: str, cand_title: str) -> float:
    """Jaccard similarity over normalized token sets, with a model-token gate."""
    origin_tokens = _tokens(origin_title)
    cand_tokens = _tokens(cand_title)
    union = origin_tokens | cand_tokens
    if not union:
        return 0.0
    origin_models = {t for t in origin_tokens if _has_digit(t)}
    cand_models = {t for t in cand_tokens if _has_digit(t)}
    if origin_models and cand_models and not (origin_models & cand_models):
        return 0.0
    return len(origin_tokens & cand_tokens) / len(union)


def _barcode_key(barcode: str | None) -> str | None:
    """Barcode identity key: case-insensitive, leading zeros stripped."""
    if not barcode:
        return None
    key = barcode.strip().lstrip("0").lower()
    return key or None


def _leading_digits(s: str) -> str:
    m = re.search(r"\d+", s)
    return m.group(0) if m else ""


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


def _barcode_match(a: str, b: str) -> bool:
    """Identity via barcode. Marketplace barcodes embed the manufacturer model
    code with different affixes/infixes (HB '0PA14000PGXSC' vs TY 'PA14000XSC':
    shared core PA14000…XSC, 'PG' inserted). Rules: equal after leading-zero
    strip; or the shorter (≥6 chars, letter-prefixed) is a subsequence of the
    longer AND both share the same leading digit run (the model number)."""
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < 6 or not short[0].isalpha():
        return False
    da, db = _leading_digits(a), _leading_digits(b)
    if not da or da != db:
        return False
    return _is_subsequence(short, long)


def _code_tokens(title: str) -> set[str]:
    """Short all-letter uppercase tokens in the raw title — model/series codes
    like XSC, XTC, PG, FE. Distinguishes letter-swap variants that digit-token
    comparison cannot see."""
    return {
        t
        for t in re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]+", title)
        if 2 <= len(t) <= 5 and t.isupper()
    }


def classify(origin: Offer, cand: Offer) -> str:
    """Return "exact" | "similar" | "discard" for a candidate offer."""
    if origin.site == cand.site:
        if origin.product_id and cand.product_id and origin.product_id == cand.product_id:
            return "exact"
        if origin.variant_id and cand.variant_id and origin.variant_id == cand.variant_id:
            return "exact"

    origin_barcode = _barcode_key(origin.barcode)
    cand_barcode = _barcode_key(cand.barcode)
    if origin_barcode and cand_barcode and _barcode_match(origin_barcode, cand_barcode):
        return "exact"

    score = similarity(origin.title, cand.title)
    if score >= EXACT_THRESHOLD:
        # Conflict vetoes: differing digit tokens (14000→4000) or differing
        # uppercase model codes (XSC→XTC) mean a different variant, even when
        # Jaccard crosses the exact threshold on long titles.
        origin_models = model_tokens(origin.title)
        cand_models = model_tokens(cand.title)
        digit_conflict = bool(origin_models and cand_models and origin_models != cand_models)
        origin_codes = _code_tokens(origin.title)
        cand_codes = _code_tokens(cand.title)
        code_conflict = bool(origin_codes and cand_codes and origin_codes != cand_codes)
        if digit_conflict or code_conflict:
            return "similar" if score >= SIMILAR_THRESHOLD else "discard"
        return "exact"
    if score >= SIMILAR_THRESHOLD:
        return "similar"
    return "discard"