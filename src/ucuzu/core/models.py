"""Core data models shared across fetch, adapters, pipeline and bot."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProductRef:
    site: str
    product_id: str
    url: str
    variant_id: str | None = None


@dataclass(frozen=True)
class Offer:
    site: str
    title: str
    price: float
    list_price: float | None
    url: str
    product_id: str
    variant_id: str | None
    merchant: str | None
    in_stock: bool
    coupon_price: bool
    barcode: str | None
    brand: str | None


@dataclass(frozen=True)
class ComparisonResult:
    origin: Offer
    offers: list[Offer]  # exact matches, price ascending
    exact: list[Offer]
    similar: list[Offer]  # capped at 3 cheapest
    site_status: dict[str, str]  # site -> "ok" | "error"
    cheapest: Offer | None = None

    @property
    def savings(self) -> float:
        if self.cheapest is None:
            return 0.0
        return self.origin.price - self.cheapest.price


@dataclass
class FetchResult:
    status: int
    url: str
    text: str
    history: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ComparisonOutcome:
    """Per-call compare() result: the cache-hit flag travels with the result,
    so concurrent comparisons never share mutable state."""

    result: ComparisonResult
    cache_hit: bool = False
    created_at: float | None = None  # comparison_cache timestamp on hits