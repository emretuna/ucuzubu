"""Affiliate link wrapping. Config-driven templates; empty config → clean URL.

HB/TY/n11: template contains {url}, replaced with the fully URL-encoded
canonical offer URL. Amazon: Associates tag appended as ?tag=<tag>.
Applied only to outbound links — never to cache keys or matching.
"""

from __future__ import annotations

from urllib.parse import quote

from ucuzu.core.config import settings


def affiliate_url(site: str, url: str) -> str:
    if site == "amazon":
        tag = settings.affiliate_amazon_tag.strip()
        if not tag:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}tag={quote(tag, safe='')}"
    template = {
        "hepsiburada": settings.affiliate_hepsiburada,
        "trendyol": settings.affiliate_trendyol,
        "n11": settings.affiliate_n11,
    }.get(site, "")
    template = template.strip()
    if not template or "{url}" not in template:
        return url
    return template.replace("{url}", quote(url, safe=""))