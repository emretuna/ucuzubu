"""aiogram handlers: /start, /help and link comparison flow."""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, Message

from ucuzu.bot import messages
from ucuzu.core import links, pipeline
from ucuzu.core.cache import Cache
from ucuzu.core.fetch import BlockedError, FetchError
from ucuzu.sites.base import ParseError

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 10.0


async def _edit(message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """Edit the ack message; fall back to a fresh message if editing is impossible."""
    try:
        await message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest:
        await message.answer(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )


def create_router(cache: Cache) -> Router:
    router = Router(name="ucuzu")
    locks: dict[int, asyncio.Lock] = {}
    last_compare: dict[int, float] = {}

    @router.message(Command("start", "help"))
    async def on_help(message: Message) -> None:
        await message.answer(messages.HELP_TEXT, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    @router.message(F.text)
    async def on_text(message: Message) -> None:
        text = message.text or ""
        try:
            url = links.extract_url(text)
        except links.UnsupportedLinkError:
            await message.answer(messages.NO_URL_HINT, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            return

        user_id = message.from_user.id if message.from_user else 0
        lock = locks.setdefault(user_id, asyncio.Lock())
        if lock.locked():
            await message.answer(messages.RATE_LIMIT_MSG)
            return
        now = time.monotonic()
        previous = last_compare.get(user_id)
        if previous is not None and now - previous < COOLDOWN_SECONDS:
            await message.answer(messages.RATE_LIMIT_MSG)
            return
        last_compare[user_id] = now

        async with lock:
            ack = await message.answer(messages.ACK_TEXT, disable_web_page_preview=True)

            async def notify_fallback() -> None:
                await _edit(ack, messages.FALLBACK_WAIT_TEXT)

            try:
                outcome = await pipeline.compare(url, cache, on_fallback=notify_fallback)
            except links.UnsupportedLinkError:
                await _edit(ack, messages.ERR_UNSUPPORTED_LINK)
                return
            except (BlockedError, FetchError, ParseError):
                logger.warning("product fetch failed for %s", url, exc_info=True)
                await _edit(ack, messages.ERR_PRODUCT_FETCH)
                return
            except Exception:
                logger.exception("comparison failed for %s", url)
                await _edit(ack, messages.ERR_GENERIC)
                return

            age = None
            if outcome.cache_hit and outcome.created_at is not None:
                age = max(0.0, (time.time() - outcome.created_at) / 60.0)
            await _edit(
                ack,
                messages.format_comparison(outcome.result, age_minutes=age),
                reply_markup=messages.cheapest_button(outcome.result),
            )

    return router