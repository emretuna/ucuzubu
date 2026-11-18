"""Entry point: python -m ucuzu (Telegram long polling)."""

from __future__ import annotations

import asyncio
import logging
import sys

import ucuzu.sites  # noqa: F401  — registers site adapters on import
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from ucuzu.bot.handlers import create_router
from ucuzu.core import fetch
from ucuzu.core.cache import Cache
from ucuzu.core.config import settings

logger = logging.getLogger(__name__)


async def main() -> None:
    cache = Cache(settings.ucuzu_db_path)
    await cache.init()
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(cache))
    logger.info("ucuzu bot polling started")
    try:
        await dispatcher.start_polling(bot)
    finally:
        await cache.close()
        await fetch.close()
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN gerekli (.env dosyasına ekle)")
        sys.exit(1)
    asyncio.run(main())