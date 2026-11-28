import logging

import gspread
from aiogram import Bot, Dispatcher
from aiogram.types import ErrorEvent

from expense_bot.config import Settings
from expense_bot.handlers import build_router
from expense_bot.llm import Claude
from expense_bot.rates import Rates
from expense_bot.service import ExpenseService
from expense_bot.sheets import Sheets

ALLOWED_UPDATES = ["message", "edited_message", "callback_query"]

log = logging.getLogger(__name__)


async def on_error(event: ErrorEvent) -> bool:
    # В лог пишем только тип ошибки, без содержимого сообщений: траты в логах не нужны.
    log.error("Ошибка при обработке update %s: %s", event.update.update_id, type(event.exception).__name__)
    message = event.update.message or event.update.edited_message
    if message:
        try:
            await message.reply("⚠️ Не получилось записать, попробуй ещё раз позже.")
        except Exception:
            pass
    return True


def build(settings: Settings) -> tuple[Bot, Dispatcher]:
    client = gspread.service_account_from_dict(settings.google_credentials)
    sheets = Sheets(client.open(settings.spreadsheet_name))
    sheets.ensure_layout()
    claude = Claude.from_api_key(settings.anthropic_api_key) if settings.anthropic_api_key else None
    if claude is None:
        log.warning("Ключа Claude API нет: новые слова уйдут в «Прочие», выписки не разбираются")
    if not settings.allowed_chat_ids:
        log.warning("Список разрешённых чатов пуст: бот ответит только ID чата")

    dispatcher = Dispatcher()
    dispatcher["service"] = ExpenseService(sheets, claude, Rates())
    dispatcher.include_router(build_router(settings.allowed_chat_ids))
    dispatcher.errors.register(on_error)
    return Bot(settings.telegram_token), dispatcher
