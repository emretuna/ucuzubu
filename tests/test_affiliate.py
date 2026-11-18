"""Offline tests for config-driven affiliate link wrapping."""

from __future__ import annotations

from urllib.parse import quote

import pytest

from ucuzu.core import affiliate
from ucuzu.core.affiliate import affiliate_url

HB_URL = (
    "https://www.hepsiburada.com/shimano-power-aero-14000-xsc-pg-3-5-devir-84cm-"
    "sarim-surf-makine-p-HBCV00008XOKU2"
)
AMZ_URL = "https://www.amazon.com.tr/dp/B0FL7ST13J"

TEMPLATE_ATTRS = (
    "affiliate_hepsiburada",
    "affiliate_trendyol",
    "affiliate_n11",
    "affiliate_amazon_tag",
)


@pytest.fixture
def settings(monkeypatch):
    """Settings instance with every affiliate field cleared before each test."""
    instance = affiliate.settings
    for attr in TEMPLATE_ATTRS:
        monkeypatch.setattr(instance, attr, "")
    return instance


def test_amazon_tag_appended(settings, monkeypatch):
    monkeypatch.setattr(settings, "affiliate_amazon_tag", "mytag-21")
    assert affiliate_url("amazon", AMZ_URL) == AMZ_URL + "?tag=mytag-21"


def test_amazon_tag_empty_leaves_url_unchanged(settings):
    assert affiliate_url("amazon", AMZ_URL) == AMZ_URL


def test_amazon_tag_whitespace_only_leaves_url_unchanged(settings, monkeypatch):
    monkeypatch.setattr(settings, "affiliate_amazon_tag", "   ")
    assert affiliate_url("amazon", AMZ_URL) == AMZ_URL


@pytest.mark.parametrize(
    ("site", "attr"),
    [
        ("hepsiburada", "affiliate_hepsiburada"),
        ("trendyol", "affiliate_trendyol"),
        ("n11", "affiliate_n11"),
    ],
)
def test_template_substitutes_quoted_url(site, attr, settings, monkeypatch):
    monkeypatch.setattr(settings, attr, "https://track.example/go?u={url}")
    assert affiliate_url(site, HB_URL) == "https://track.example/go?u=" + quote(HB_URL, safe="")


def test_hepsiburada_template_with_query_placeholder(settings, monkeypatch):
    monkeypatch.setattr(settings, "affiliate_hepsiburada", "https://app.hb.biz/x?redirect={url}")
    expected = "https://app.hb.biz/x?redirect=" + quote(HB_URL, safe="")
    assert affiliate_url("hepsiburada", HB_URL) == expected


def test_template_without_placeholder_leaves_url_unchanged(settings, monkeypatch):
    monkeypatch.setattr(settings, "affiliate_trendyol", "https://ty.gl/influencer/no-placeholder")
    assert affiliate_url("trendyol", HB_URL) == HB_URL


@pytest.mark.parametrize(
    "site", ["hepsiburada", "trendyol", "n11", "amazon"]
)
def test_empty_templates_leave_url_unchanged(site, settings):
    assert affiliate_url(site, HB_URL) == HB_URL