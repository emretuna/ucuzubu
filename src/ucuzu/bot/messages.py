"""User-facing message rendering (Turkish UI, Telegram HTML parse mode)."""

from __future__ import annotations

from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ucuzu.core.affiliate import affiliate_url
from ucuzu.core.models import ComparisonResult, Offer

SITE_NAMES: dict[str, str] = {
    "hepsiburada": "Hepsiburada",
    "trendyol": "Trendyol",
    "n11": "n11",
    "amazon": "Amazon",
}

TABLE_ORDER = ("hepsiburada", "trendyol", "n11", "amazon")
_LABEL_WIDTH = max(len(name) for name in SITE_NAMES.values())
_SIMILAR_TITLE_LIMIT = 60

ERR_UNSUPPORTED_LINK = (
    "Link çözümlenemedi… Hepsiburada, Trendyol, n11 veya Amazon.com.tr ürün linki paylaş."
)
ERR_PRODUCT_FETCH = (
    "Ürün bilgisi alınamadı… site şu an engelliyor olabilir, biraz sonra tekrar dene."
)
ERR_GENERIC = "Bir şeyler ters gitti… biraz sonra tekrar dene."
RATE_LIMIT_MSG = "Biraz yavaş 🙂 tekrar dene"
ACK_TEXT = "🔍 Ürün aranıyor… (4 site)"
FALLBACK_WAIT_TEXT = (
    "⏳ Standart aramada sonuç çıkmadı. Alternatifleri daha geniş bir web taramasıyla "
    "arıyorum — bu işlem normalden biraz uzun sürebilir…"
)
FALLBACK_HEADER = "🌐 Web taramasıyla bulunan alternatifler (birebir eşleşme doğrulanmadı):"
NO_URL_HINT = (
    "🔗 Bir ürün linki bulamadım 🙂\n\n"
    "📲 Ürün sayfasında <b>Paylaş ➜ Telegram</b> ➜ bu sohbeti seç,\n"
    "ya da ürün linkini buraya yapıştır.\n\n"
    "🛒 Desteklenen siteler: <b>Hepsiburada, Trendyol, n11, Amazon.com.tr</b>"
)
HELP_TEXT = (
    "👋 <b>UcuzuBu'ya hoş geldin!</b>\n"
    "<i>Aynı ürün, dört site, tek en ucuz fiyat.</i> 🏷️\n\n"
    "🛒 <b>Nasıl çalışır?</b>\n"
    "Bir ürün linki paylaş → Hepsiburada, Trendyol, n11 ve Amazon.com.tr'de "
    "aynı ürünü ararım → fiyatları karşılaştırır, 🏆 en ucuzunu linkiyle getiririm.\n\n"
    "📲 <b>Nasıl kullanılır?</b>\n"
    "1️⃣ Ürün sayfasında <b>Paylaş ➜ Telegram</b> ➜ bu sohbeti seç\n"
    "2️⃣ Ya da ürün linkini buraya yapıştır\n\n"
    "🔎 <b>Sonuçta ne var?</b>\n"
    "• Dört sitenin fiyat tablosu\n"
    "• 🏆 En ucuz teklif + tek tıkla git butonu\n"
    "• 💸 Paylaştığın fiyata göre ne kadar tasarruf ettiğin\n"
    "• 🎟️ Kuponlu fiyatlar dahil (üyelik indirimleri hariç)\n\n"
    "⚡ İlk arama ~1 dakika sürebilir; tekrar paylaşımlar anında gelir.\n"
    "💡 İpucu: uygulama paylaşım linkleri (app.hb.biz, ty.gl…) da çalışır.\n\n"
    "🚀 Hadi, bir ürün linki at — en ucuzunu bulalım!"
)


def format_price(p: float) -> str:
    """37685.5 → '37.685,50 ₺' (tr-TR)."""
    text = f"{p:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} ₺"


def _site_name(site: str) -> str:
    return SITE_NAMES.get(site, site)


def _truncate_title(title: str, limit: int = _SIMILAR_TITLE_LIMIT) -> str:
    title = " ".join(title.split())
    if len(title) <= limit:
        return title
    return title[: limit - 1].rstrip() + "…"


def _best_exact_for_site(result: ComparisonResult, site: str) -> Offer | None:
    offers = [o for o in result.exact if o.site == site]
    if not offers:
        return None
    return min(offers, key=lambda o: o.price)


def _price_cell(offer: Offer) -> str:
    text = format_price(offer.price)
    if offer.coupon_price:
        text += " (kuponlu)"
    return text


def _table(result: ComparisonResult) -> str:
    rows: list[str] = []
    for site in TABLE_ORDER:
        label = _site_name(site)
        is_origin = site == result.origin.site
        if result.site_status.get(site) == "error":
            note = "— (şu an ulaşılamıyor)"
        else:
            offer = result.origin if is_origin else _best_exact_for_site(result, site)
            note = _price_cell(offer) if offer is not None else "—"
        row = f"{label:<{_LABEL_WIDTH}}  {note}"
        if is_origin:
            row += "  ◀ paylaştığın"
        rows.append(escape(row))
    return "<pre>" + "\n".join(rows) + "</pre>"


def _is_only_origin(result: ComparisonResult) -> bool:
    if not result.exact:
        return True
    origin = result.origin
    return all(o.site == origin.site and o.product_id == origin.product_id for o in result.exact)


def _cheapest_line(result: ComparisonResult) -> str:
    cheapest = result.cheapest
    if cheapest is None:
        return ""
    parts = [f"🏆 En ucuz: {_site_name(cheapest.site)} — {format_price(cheapest.price)}"]
    if result.savings > 0:
        parts.append(f"{format_price(result.savings)} daha ucuz")
    line = escape(" · ".join(parts))
    if cheapest.site == result.origin.site and cheapest.product_id == result.origin.product_id:
        line += "\nZaten en iyi fiyatı buldun 👍"
    return line


def _similar_block(result: ComparisonResult) -> str:
    if not result.similar:
        return ""
    lines = ["🔎 Benzer ürün (farklı satıcı/model olabilir):"]
    for offer in result.similar[:3]:
        title = escape(_truncate_title(offer.title))
        url = affiliate_url(offer.site, offer.url)
        lines.append(
            f'• <a href="{escape(url, quote=True)}">{title}</a> — '
            f"{escape(format_price(offer.price))} ({escape(_site_name(offer.site))})"
        )
    return "\n".join(lines)


def _fallback_block(result: ComparisonResult) -> str:
    lines = [FALLBACK_HEADER]
    for offer in result.fallback[:3]:
        title = escape(_truncate_title(offer.title))
        price = format_price(offer.price) if offer.price > 0 else "fiyat sayfada"
        url = affiliate_url(offer.site, offer.url)
        lines.append(
            f'• <a href="{escape(url, quote=True)}">{title}</a> — {escape(price)} '
            f"({escape(_site_name(offer.site))})"
        )
    lines.append("Fiyatlar web taramasından alındı, güncel olmayabilir.")
    return "\n".join(lines)


def format_comparison(result: ComparisonResult, *, age_minutes: float | None = None) -> str:
    """Render a comparison as Telegram HTML (parse_mode=HTML)."""
    origin = result.origin
    blocks: list[str] = [
        f"<b>{escape(origin.title)}</b>\n{_site_name(origin.site)} · {format_price(origin.price)}",
        _table(result),
    ]

    if _is_only_origin(result):
        blocks.append(
            escape(
                f"Paylaştığın fiyat: {format_price(origin.price)}\n"
                "Diğer sitelerde birebir eşleşme bulunamadı."
            )
        )
    else:
        cheapest = _cheapest_line(result)
        if cheapest:
            blocks.append(cheapest)

    similar = _similar_block(result)
    if similar:
        blocks.append(similar)

    if result.fallback:
        blocks.append(_fallback_block(result))

    footer = "Fiyatlar anlık değişebilir."
    if age_minutes is not None:
        footer = f"fiyatlar {int(age_minutes)} dk önce güncellendi · " + footer
    blocks.append(footer)
    return "\n\n".join(blocks)


def cheapest_button(result: ComparisonResult) -> InlineKeyboardMarkup | None:
    cheapest = result.cheapest
    if cheapest is None:
        return None
    url = affiliate_url(cheapest.site, cheapest.url)
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="En ucuz linke git →", url=url)]]
    )