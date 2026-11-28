import logging
import re
from datetime import date
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message, ReactionTypeEmoji
from aiogram.utils.keyboard import InlineKeyboardBuilder
from anthropic import AuthenticationError

from expense_bot.dates import today
from expense_bot.llm import ClaudeError
from expense_bot.reports import money, previous_month
from expense_bot.service import ExpenseService, ImportReport, LineResult
from expense_bot.statement import UnsupportedFile

MAX_FILE_BYTES = 20 * 1024 * 1024  # больше бот скачать не может
THUMBS_UP = [ReactionTypeEmoji(emoji="👍")]
KEYWORD = re.compile(r"«([^»]+)»")

HELP = """\
Пиши траты наличными, по одной в строке:
450 кофе
1 200 продукты
20 евро такси
12,5 € сувениры

Категорию бот определит сам. Если ошибся, нажми нужную кнопку под его ответом. \
Опечатку в сумме исправь прямо в сообщении — строка в таблице поправится.

Карточные траты в чат не пиши: в конце месяца пришли сюда выписку из банка \
файлом (PDF, Excel или CSV).

/month — отчёт за текущий месяц
/month 10 или /month 2026-10 — за другой месяц
/month прошлый — за прошлый месяц"""

NO_AMOUNT = "Не понял сумму. Пример: 450 кофе"

log = logging.getLogger(__name__)


def author_name(message: Message) -> str:
    user = message.from_user
    return user.first_name if user and user.first_name else "?"


def parse_month(arg: str | None, now: date) -> tuple[int, int] | None:
    """«10» → (год, 10), «2026-10» → (2026, 10), «прошлый» → прошлый месяц, пусто → текущий."""
    arg = (arg or "").strip().lower()
    if not arg:
        return now.year, now.month
    if arg in {"прошлый", "prev", "-1"}:
        return previous_month(now.year, now.month)
    if match := re.fullmatch(r"(\d{4})-(\d{1,2})", arg):
        year, month = int(match[1]), int(match[2])
    elif arg.isdigit():
        month = int(arg)
        year = now.year if month <= now.month else now.year - 1
    else:
        return None
    return (year, month) if 1 <= month <= 12 else None


def category_keyboard(message_id: int, line: int | None, names: list[str]):
    builder = InlineKeyboardBuilder()
    for index, name in enumerate(names):
        line_part = "" if line is None else str(line)
        builder.button(text=name, callback_data=f"c:{message_id}:{line_part}:{index}")
    builder.adjust(2)
    return builder.as_markup()


def category_prompt(result: LineResult) -> str:
    category = result.category
    word = category.keyword or result.expense.comment or "без описания"
    text = f"«{word}» → {category.category}"
    if result.line is not None:
        text += f" ({result.text})"
    if category.source == "claude":
        return text + ". Неверно? Выбери категорию:"
    return text + ". Выбери категорию:"


def plural(number: int, one: str, few: str, many: str) -> str:
    """plural(2, "трату", "траты", "трат") → «траты»."""
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def import_summary(report: ImportReport) -> str:
    if report.found == 0:
        return "В этом файле не нашёл операций по счёту. Пришли выписку из банка."
    expenses = plural(report.added, "трату", "траты", "трат")
    lines = [f"✅ Выписка: добавил {report.added} {expenses} на {money(report.total)} RSD."]
    if report.refunds:
        lines.append(f"Возвраты: {report.refunds} на {money(report.refunds_total)} RSD.")
    lines.append(
        f"Пропустил: снятие наличных — {report.atm}, переводы и поступления — "
        f"{report.transfers}, уже были в таблице — {report.duplicates}."
    )
    if report.total_mismatch:
        stated, recognized = report.total_mismatch
        lines.append(
            f"⚠️ В выписке списаний на {money(stated)}, а я распознал {money(recognized)}: "
            "сверь выписку с таблицей."
        )
    if report.without_rate:
        lines.append(f"⚠️ Без курса: {report.without_rate}. Впиши «Сумма RSD» в таблице.")
    if report.suspicious:
        lines.append("⚠️ Похоже на дубли с чатом:")
        for cash, card in report.suspicious[:10]:
            lines.append(
                f"• {cash.date:%d.%m}, {money(card.amount_rsd)} RSD: «{escape(cash.comment)}» "
                f"({escape(cash.author)}) и {escape(card.comment)}"
            )
        lines.append("Если это одна покупка, удали из таблицы строку с наличными.")
    return "\n".join(lines)


async def answer_results(message: Message, results: list[LineResult], service: ExpenseService) -> None:
    """Ответ на сообщение с тратами: 👍, кнопки для новых слов, подсказки об ошибках."""
    if any(r.status in {"added", "duplicate"} for r in results):
        await message.react(THUMBS_UP)
    names = service.category_names()
    for result in results:
        if result.status != "added":
            continue
        if result.category.source != "dictionary":
            await message.reply(
                category_prompt(result),
                reply_markup=category_keyboard(message.message_id, result.line, names),
            )
        if result.expense.amount_rsd is None:
            await message.reply(
                f"Курс {result.expense.currency} не нашёл: трата не попадёт в отчёт, "
                "пока не впишешь «Сумма RSD» в таблице."
            )
    failed = [r for r in results if r.status == "no_amount"]
    if failed and len(results) == 1:
        await message.reply(NO_AMOUNT)
    elif failed:
        listed = ", ".join(f"«{r.text}»" for r in failed)
        await message.reply(f"Не понял сумму в строках: {listed}. Пример: 450 кофе")


def build_router(allowed_chat_ids: frozenset[int]) -> Router:
    root = Router(name="root")
    main = Router(name="main")
    main.message.filter(F.chat.id.in_(allowed_chat_ids))
    main.edited_message.filter(F.chat.id.in_(allowed_chat_ids))
    main.callback_query.filter(F.message.chat.id.in_(allowed_chat_ids))

    @main.message(Command("start", "help"))
    async def on_help(message: Message) -> None:
        await message.reply(HELP)

    @main.message(Command("month"))
    async def on_month(message: Message, command: CommandObject, service: ExpenseService) -> None:
        period = parse_month(command.args, today())
        if period is None:
            await message.reply("Не понял месяц. Примеры: /month, /month 10, /month 2026-10")
            return
        await message.reply(service.month_report(*period), parse_mode="HTML")

    @main.message(F.document)
    async def on_document(message: Message, bot: Bot, service: ExpenseService) -> None:
        document = message.document
        if service.claude is None:
            await message.reply("Чтобы разбирать выписки, нужен ключ Claude API: см. README.")
            return
        if document.file_size and document.file_size > MAX_FILE_BYTES:
            await message.reply("Файл больше 20 МБ, такой бот скачать не может.")
            return
        progress = await message.reply("Разбираю выписку, это займёт пару минут…")
        downloaded = await bot.download(document)
        try:
            report = await service.import_statement(
                downloaded.read(), document.file_name or "", author_name(message)
            )
        except UnsupportedFile:
            await progress.edit_text("Не знаю такой формат. Пришли выписку в PDF, XLSX или CSV.")
            return
        except ClaudeError as error:
            await progress.edit_text(f"Не получилось разобрать выписку: {error}. Попробуй ещё раз.")
            return
        except AuthenticationError:
            await progress.edit_text("Ключ Claude API не подходит: проверь его по README.")
            return
        except Exception as error:  # сеть, лимиты API, Google Таблица
            log.error("Выписка не загружена: %s", type(error).__name__)
            await progress.edit_text("Не получилось разобрать выписку. Попробуй ещё раз позже.")
            return
        await progress.edit_text(import_summary(report), parse_mode="HTML")

    @main.message(F.text & ~F.text.startswith("/"))
    async def on_text(message: Message, service: ExpenseService) -> None:
        results = await service.add_from_message(
            message.text, author_name(message), message.chat.id, message.message_id, message.date
        )
        await answer_results(message, results, service)

    @main.edited_message(F.text & ~F.text.startswith("/"))
    async def on_edit(message: Message, service: ExpenseService) -> None:
        status = "not_found"
        if len([line for line in message.text.splitlines() if line.strip()]) == 1:
            status = await service.edit_from_message(
                message.text, message.chat.id, message.message_id, message.date
            )
        elif service.is_recorded(message.chat.id, message.message_id):
            await message.reply("Эту трату уже записал одной строкой: поправь её в таблице вручную.")
            return
        if status == "updated":
            await message.react(THUMBS_UP)
        elif status == "no_amount":
            await message.reply("В исправленном сообщении нет суммы, строку в таблице не менял.")
        else:  # трату ещё не записывали — например, в первый раз забыли сумму
            results = await service.add_from_message(
                message.text, author_name(message), message.chat.id, message.message_id, message.date
            )
            await answer_results(message, results, service)

    @main.callback_query(F.data.startswith("c:"))
    async def on_category(callback: CallbackQuery, service: ExpenseService) -> None:
        prompt = callback.message
        if not isinstance(prompt, Message) or not prompt.text:
            await _answer(callback, "Сообщение устарело, поправь категорию в таблице")
            return
        if prompt.text.startswith("✅"):  # кнопку нажали повторно, пока она не исчезла
            await _answer(callback)
            return
        _, message_id, line, index = callback.data.split(":")
        found = KEYWORD.search(prompt.text)
        keyword = found[1] if found else None
        category = service.change_category(
            prompt.chat.id, int(message_id), int(line) if line else None, int(index), keyword
        )
        if category is None:
            await _answer(callback, "Не нашёл эту трату в таблице")
            return
        await prompt.edit_text(f"✅ «{keyword or 'трата'}» → {category}")
        await _answer(callback)

    guest = Router(name="guest")
    guest.message.filter(~F.chat.id.in_(allowed_chat_ids))

    @guest.message(F.chat.type == "private")
    @guest.message(Command("start"))
    async def on_stranger(message: Message) -> None:
        await message.reply(
            "Это приватный бот для учёта расходов. "
            f"ID этого чата: <code>{message.chat.id}</code>. "
            "Чтобы бот здесь работал, добавь этот ID в настройки (см. README).",
            parse_mode="HTML",
        )

    root.include_routers(main, guest)
    return root


async def _answer(callback: CallbackQuery, text: str | None = None) -> None:
    # Если бот был выключен, кнопку могли нажать давно: Telegram уже не примет ответ.
    try:
        await callback.answer(text)
    except TelegramBadRequest:
        pass
