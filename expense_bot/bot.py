"""Бот работает постоянно: удобно на Mac для проверки. Запуск: python -m expense_bot.bot"""
import asyncio
import logging

from expense_bot.app import ALLOWED_UPDATES, build
from expense_bot.config import load_settings


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot, dispatcher = build(load_settings())
    await dispatcher.start_polling(bot, allowed_updates=ALLOWED_UPDATES)


if __name__ == "__main__":
    asyncio.run(main())
