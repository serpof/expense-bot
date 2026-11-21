"""Один проход для запуска по расписанию: забрать накопившиеся сообщения, обработать и выйти.

Telegram хранит сообщения для бота до 24 часов, поэтому бот может быть выключен ночью.
Запуск: python -m expense_bot.run_once
"""
import asyncio
import logging

from expense_bot.app import ALLOWED_UPDATES, build
from expense_bot.config import load_settings

log = logging.getLogger("expense_bot.run_once")


async def main() -> None:
    # INFO не включаем: в публичных логах Actions не нужны подробности.
    logging.basicConfig(level=logging.WARNING)
    bot, dispatcher = build(load_settings())
    processed = 0
    try:
        while True:
            updates = await bot.get_updates(timeout=0, allowed_updates=ALLOWED_UPDATES)
            if not updates:
                break
            for update in updates:
                await dispatcher.feed_update(bot, update)
            # Подтверждаем обработанные обновления, чтобы Telegram их больше не присылал.
            await bot.get_updates(offset=updates[-1].update_id + 1, timeout=0)
            processed += len(updates)
    finally:
        await bot.session.close()
    log.warning("Обработано обновлений: %d", processed)


if __name__ == "__main__":
    asyncio.run(main())
