"""Проверяем бота целиком: обновление Telegram → диспетчер aiogram → сервис → запросы к Bot API.

Вместо сети — сессия, которая записывает запросы бота и отвечает заглушками.
"""
import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetFile, SendMessage, SetMessageReaction
from aiogram.types import Chat, File, Message, Update

from expense_bot.app import on_error
from expense_bot.handlers import build_router, category_prompt, import_summary, parse_month, plural
from expense_bot.service import ExpenseService, ImportReport, LineResult
from expense_bot.categories import CategoryResult
from expense_bot.sheets import Expense
from tests.fakes import FakeClaude, FakeRates, FakeSheets

CHAT = -100
STRANGER = 777
SENT = int(datetime(2026, 11, 21, 19, 0, tzinfo=timezone.utc).timestamp())


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.file_content = b""

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(
                message_id=1000 + len(self.requests),
                date=datetime.now(timezone.utc),
                chat=Chat(id=method.chat_id or CHAT, type="group"),
                text=method.text,
            ).as_(bot)
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u1", file_path="documents/file")
        return True

    async def close(self):
        pass

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield self.file_content

    def of(self, kind):
        return [request for request in self.requests if isinstance(request, kind)]


class Harness:
    def __init__(self, claude=None):
        self.session = RecordingSession()
        self.bot = Bot("123456:TEST-token", session=self.session)
        self.sheets = FakeSheets()
        self.service = ExpenseService(self.sheets, claude, FakeRates())
        self.dispatcher = Dispatcher()
        self.dispatcher["service"] = self.service
        self.dispatcher.include_router(build_router(frozenset({CHAT})))
        self.dispatcher.errors.register(on_error)
        self.update_id = 0

    def feed(self, **update):
        self.update_id += 1
        data = Update.model_validate({"update_id": self.update_id, **update}, context={"bot": self.bot})
        asyncio.run(self.dispatcher.feed_update(self.bot, data))

    def send(self, text, chat_id=CHAT, message_id=10, chat_type="group"):
        self.feed(message=message(text, chat_id, message_id, chat_type))


def message(text, chat_id=CHAT, message_id=10, chat_type="group"):
    chat = {"id": chat_id, "type": chat_type}
    if chat_type == "group":
        chat["title"] = "Семья"
    return {
        "message_id": message_id, "date": SENT, "chat": chat, "text": text,
        "from": {"id": 5, "is_bot": False, "first_name": "Sergei"},
    }


def test_known_expense_gets_thumbs_up_only():
    harness = Harness()
    harness.send("450 кофе")
    [reaction] = harness.session.of(SetMessageReaction)
    assert reaction.reaction[0].emoji == "👍"
    assert harness.session.of(SendMessage) == []
    assert harness.sheets.rows[0].category == "Кафе"


def test_new_word_gets_category_buttons():
    harness = Harness(FakeClaude({"шаурма": ("Кафе", "шаурма")}))
    harness.send("350 шаурма")
    [reply] = harness.session.of(SendMessage)
    assert reply.text == "«шаурма» → Кафе. Неверно? Выбери категорию:"
    buttons = [b for row in reply.reply_markup.inline_keyboard for b in row]
    assert [b.text for b in buttons] == harness.service.category_names()
    assert buttons[0].callback_data == "c:10::0"


def test_button_changes_category_and_teaches_dictionary():
    harness = Harness(FakeClaude({"шаурма": ("Кафе", "шаурма")}))
    harness.send("350 шаурма")
    index = harness.service.category_names().index("Продукты")
    prompt = message("«шаурма» → Кафе. Неверно? Выбери категорию:", message_id=11)
    prompt["from"] = {"id": 1, "is_bot": True, "first_name": "Бот"}
    harness.feed(callback_query={
        "id": "q1", "chat_instance": "ci", "data": f"c:10::{index}", "message": prompt,
        "from": {"id": 5, "is_bot": False, "first_name": "Sergei"},
    })
    [edit] = harness.session.of(EditMessageText)
    assert edit.text == "✅ «шаурма» → Продукты"
    assert harness.session.of(AnswerCallbackQuery)
    assert harness.sheets.rows[0].category == "Продукты"
    assert harness.sheets.alias_map["шаурма"] == "Продукты"


def test_message_without_amount_gets_hint():
    harness = Harness()
    harness.send("кафе")
    assert harness.session.of(SetMessageReaction) == []
    assert [m.text for m in harness.session.of(SendMessage)] == ["Не понял сумму. Пример: 450 кофе"]


def test_edit_fixes_amount():
    harness = Harness()
    harness.send("450 кофе")
    harness.feed(edited_message={**message("540 кофе"), "edit_date": SENT + 60})
    assert harness.sheets.rows[0].amount == Decimal("540")
    assert len(harness.sheets.rows) == 1


def test_stranger_gets_chat_id_and_nothing_is_written():
    harness = Harness()
    harness.send("450 кофе", chat_id=STRANGER, chat_type="private")
    [reply] = harness.session.of(SendMessage)
    assert str(STRANGER) in reply.text
    assert harness.sheets.rows == []


def test_month_command_sends_html_report():
    harness = Harness()
    harness.sheets.rows.append(Expense(date(2026, 11, 3), Decimal("450"), "RSD", "Кафе", "кофе",
                                       "Sergei", "tg:-100:1", Decimal("450")))
    harness.send("/month 2026-11")
    [reply] = harness.session.of(SendMessage)
    assert reply.parse_mode == "HTML"
    assert reply.text.startswith("<b>Ноябрь 2026: 450 RSD</b>")


def test_error_is_reported_to_chat():
    harness = Harness()
    harness.service.add_from_message = AsyncMock(side_effect=RuntimeError("Google недоступен"))
    harness.send("450 кофе")
    assert [m.text for m in harness.session.of(SendMessage)] == [
        "⚠️ Не получилось записать, попробуй ещё раз позже."
    ]


@pytest.mark.parametrize("arg, expected", [
    (None, (2026, 11)),
    ("10", (2026, 10)),
    ("12", (2025, 12)),  # декабрь ещё не наступил — значит, прошлогодний
    ("2026-03", (2026, 3)),
    ("прошлый", (2026, 10)),
    ("13", None),
    ("ноябрь", None),
])
def test_parse_month(arg, expected):
    assert parse_month(arg, date(2026, 11, 21)) == expected


def test_category_prompt_for_multiline_fallback():
    expense = Expense(date(2026, 11, 21), Decimal("900"), "RSD", "Прочие", "ужин в ресторане",
                      "Sergei", "tg:-100:1:1", Decimal("900"))
    result = LineResult(1, "900 ужин в ресторане", "added", expense,
                        CategoryResult("Прочие", "ужин", "fallback"))
    assert category_prompt(result) == "«ужин» → Прочие (900 ужин в ресторане). Выбери категорию:"


def test_import_summary_mentions_warnings():
    cash = Expense(date(2026, 11, 6), Decimal("450"), "RSD", "Кафе", "кофе", "Sergei", "tg", Decimal("450"))
    card = Expense(date(2026, 11, 6), Decimal("450"), "RSD", "Кафе", "CAFE <1>", "Sergei", "card",
                   Decimal("450"), "карта")
    report = ImportReport(found=8, added=4, total=Decimal("5950"), atm=1, transfers=2, refunds=1,
                          refunds_total=Decimal("-1200"),
                          total_mismatch=(Decimal("20000"), Decimal("15950")), suspicious=[(cash, card)])
    lines = import_summary(report).splitlines()
    assert lines[0] == "✅ Выписка: добавил 4 траты на 5 950 RSD."
    assert "Возвраты: 1 на -1 200 RSD." in lines
    assert "• 06.11, 450 RSD: «кофе» (Sergei) и CAFE &lt;1&gt;" in lines


def send_document(harness, name="izvod.pdf", content=b"%PDF-1.7 ..."):
    harness.session.file_content = content
    data = message("", message_id=20)
    del data["text"]
    data["document"] = {"file_id": "f1", "file_unique_id": "u1", "file_name": name, "file_size": len(content)}
    harness.feed(message=data)


def test_statement_file_is_imported():
    statement = {"account_currency": "RSD", "total_debit": None, "transactions": [
        {"date": "2026-11-03", "amount": 4200, "direction": "debit", "kind": "purchase",
         "merchant": "MAXI 0123", "reference": ""},
        {"date": "2026-11-05", "amount": 10000, "direction": "debit", "kind": "atm",
         "merchant": "ATM", "reference": ""},
    ]}
    harness = Harness(FakeClaude({"MAXI 0123": ("Продукты", "maxi")}, statement=statement))
    send_document(harness)
    [progress] = harness.session.of(SendMessage)
    assert progress.text.startswith("Разбираю выписку")
    [summary] = harness.session.of(EditMessageText)
    assert summary.text.startswith("✅ Выписка: добавил 1 трату на 4 200 RSD.")
    assert "снятие наличных — 1" in summary.text
    assert [(e.comment, e.source) for e in harness.sheets.rows] == [("MAXI 0123", "карта")]


def test_unsupported_statement_format():
    harness = Harness(FakeClaude(statement={}))
    send_document(harness, name="izvod.xls", content=b"\xd0\xcf\x11\xe0")
    [summary] = harness.session.of(EditMessageText)
    assert summary.text == "Не знаю такой формат. Пришли выписку в PDF, XLSX или CSV."


def test_statement_without_claude_key():
    harness = Harness(claude=None)
    send_document(harness)
    assert [m.text for m in harness.session.of(SendMessage)] == [
        "Чтобы разбирать выписки, нужен ключ Claude API: см. README."
    ]


@pytest.mark.parametrize("number, expected", [
    (1, "трату"), (2, "траты"), (4, "траты"), (5, "трат"), (11, "трат"),
    (12, "трат"), (21, "трату"), (22, "траты"), (111, "трат"), (0, "трат"),
])
def test_plural(number, expected):
    assert plural(number, "трату", "траты", "трат") == expected


def test_file_without_transactions():
    harness = Harness(FakeClaude(statement={"account_currency": "RSD", "total_debit": None, "transactions": []}))
    send_document(harness, name="menu.pdf")
    [summary] = harness.session.of(EditMessageText)
    assert summary.text == "В этом файле не нашёл операций по счёту. Пришли выписку из банка."
    assert harness.sheets.rows == []
