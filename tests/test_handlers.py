"""Handler-flow tests without Telegram: fake Message objects exercise the real
on_text closure — ack → compare → edit, cooldown, error mapping, cache-hit age.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from ucuzu.bot import messages
from ucuzu.bot.handlers import create_router
from ucuzu.core import links, pipeline
from ucuzu.core.cache import Cache
from ucuzu.core.fetch import BlockedError
from ucuzu.core.models import ComparisonOutcome, ComparisonResult, Offer

HB_URL = "https://www.hepsiburada.com/x-p-HBCV00008XOKU2"


def offer(price=33163.24):
    return Offer(
        site="n11", title="Shimano Power Aero 14000 XSC PG", price=price,
        list_price=None, url="https://www.n11.com/urun/x-83521276",
        product_id="83521276", variant_id=None, merchant=None, in_stock=True,
        coupon_price=True, barcode=None, brand="Shimano",
    )


def result():
    o = offer()
    origin = Offer(
        site="hepsiburada", title="Shimano Power Aero 14000 XSC PG", price=37685.5,
        list_price=None, url=HB_URL, product_id="HBCV00008XOKU2", variant_id=None,
        merchant=None, in_stock=True, coupon_price=False, barcode=None, brand="Shimano",
    )
    return ComparisonResult(
        origin=origin, offers=[o], exact=[o], similar=[],
        site_status={"hepsiburada": "ok", "trendyol": "ok", "n11": "ok", "amazon": "ok"},
        cheapest=o,
    )


class FakeMessage:
    def __init__(self, text, user_id=42):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[dict] = []
        self.edits: list[dict] = []

    async def answer(self, text, **kw):
        self.answers.append({"text": text, **kw})
        return FakeMessage(text, self.from_user.id)  # ack message

    async def edit_text(self, text, **kw):
        self.edits.append({"text": text, **kw})


class TrackedMessage(FakeMessage):
    """FakeMessage whose ack records edits into self.edits."""

    async def answer(self, text, **kw):
        ack = await super().answer(text, **kw)
        parent = self

        async def edit(text_, **kw_):
            parent.edits.append({"text": text_, **kw_})

        ack.edit_text = edit
        return ack


def text_handler(router):
    # second registered message handler is the F.text one (first: /start,/help)
    return router.message.handlers[1].callback


@pytest.fixture
def router(tmp_path):
    return create_router(Cache(str(tmp_path / "h.db")))


async def test_link_flow_ack_edits_into_comparison(router, monkeypatch):
    async def fake_compare(url, cache=None):
        assert url == HB_URL
        return ComparisonOutcome(result=result())

    monkeypatch.setattr(pipeline, "compare", fake_compare)
    msg = TrackedMessage(f"şuna bak {HB_URL}")
    await text_handler(router)(msg)

    assert [a["text"] for a in msg.answers] == [messages.ACK_TEXT]
    assert len(msg.edits) == 1
    final = msg.edits[0]
    assert "33.163,24 ₺" in final["text"]
    assert "🏆" in final["text"]
    assert final["reply_markup"].inline_keyboard[0][0].url == "https://www.n11.com/urun/x-83521276"


async def test_cache_hit_adds_age_footer(router, monkeypatch):
    created = time.time() - 12 * 60

    async def fake_compare(url, cache=None):
        return ComparisonOutcome(result=result(), cache_hit=True, created_at=created)

    monkeypatch.setattr(pipeline, "compare", fake_compare)
    msg = TrackedMessage(HB_URL)
    await text_handler(router)(msg)
    assert "fiyatlar 12 dk önce güncellendi" in msg.edits[0]["text"]


async def test_cooldown_blocks_second_compare(router, monkeypatch):
    calls = []

    async def fake_compare(url, cache=None):
        calls.append(url)
        return ComparisonOutcome(result=result())

    monkeypatch.setattr(pipeline, "compare", fake_compare)
    handler = text_handler(router)
    await handler(TrackedMessage(HB_URL))
    msg2 = FakeMessage(HB_URL)
    await handler(msg2)
    assert len(calls) == 1
    assert msg2.answers[0]["text"] == messages.RATE_LIMIT_MSG


async def test_unsupported_link_edits_error(router, monkeypatch):
    async def fake_compare(url, cache=None):
        raise links.UnsupportedLinkError("no adapter")

    monkeypatch.setattr(pipeline, "compare", fake_compare)
    msg = TrackedMessage("https://example.com/x")
    await text_handler(router)(msg)
    assert [e["text"] for e in msg.edits] == [messages.ERR_UNSUPPORTED_LINK]


async def test_blocked_site_edits_fetch_error(router, monkeypatch):
    async def fake_compare(url, cache=None):
        raise BlockedError("403")

    monkeypatch.setattr(pipeline, "compare", fake_compare)
    msg = TrackedMessage(HB_URL)
    await text_handler(router)(msg)
    assert [e["text"] for e in msg.edits] == [messages.ERR_PRODUCT_FETCH]


async def test_non_url_text_gets_hint(router):
    msg = FakeMessage("merhaba nasilsin")
    await text_handler(router)(msg)
    assert msg.answers[0]["text"] == messages.NO_URL_HINT