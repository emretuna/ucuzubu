"""Fetch-layer proxy wiring (plan contingency for datacenter-IP blocks)."""

from __future__ import annotations

import pytest

from ucuzu.core import fetch


@pytest.fixture(autouse=True)
def reset_sessions():
    fetch._sessions.clear()
    yield
    fetch._sessions.clear()


def test_no_proxy_when_unset(monkeypatch):
    monkeypatch.setattr(fetch.settings, "fetch_proxy_url", "")
    assert fetch._proxies() is None


def test_proxy_url_applied_to_both_schemes(monkeypatch):
    monkeypatch.setattr(fetch.settings, "fetch_proxy_url", "http://u:p@tr.proxy.example:8080")
    assert fetch._proxies() == {
        "http": "http://u:p@tr.proxy.example:8080",
        "https": "http://u:p@tr.proxy.example:8080",
    }


def test_schemeless_proxy_gets_http_prefix(monkeypatch):
    monkeypatch.setattr(fetch.settings, "fetch_proxy_url", "u:p@tr.proxy.example:8080")
    assert fetch._proxies() == {
        "http": "http://u:p@tr.proxy.example:8080",
        "https": "http://u:p@tr.proxy.example:8080",
    }

def test_whitespace_proxy_is_unset(monkeypatch):
    monkeypatch.setattr(fetch.settings, "fetch_proxy_url", "   ")
    assert fetch._proxies() is None


def test_session_created_with_proxies(monkeypatch):
    monkeypatch.setattr(fetch.settings, "fetch_proxy_url", "http://tr.proxy.example:8080")
    captured = {}

    class FakeSession:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(fetch.curl_requests, "AsyncSession", FakeSession)
    fetch._get_session("chrome")
    assert captured["impersonate"] == "chrome"
    assert captured["proxies"] == {
        "http": "http://tr.proxy.example:8080",
        "https": "http://tr.proxy.example:8080",
    }


def test_session_created_without_proxies(monkeypatch):
    monkeypatch.setattr(fetch.settings, "fetch_proxy_url", "")
    captured = {}

    class FakeSession:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(fetch.curl_requests, "AsyncSession", FakeSession)
    fetch._get_session("chrome")
    assert captured["proxies"] is None