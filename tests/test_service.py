import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from expense_bot.llm import ClaudeError
from expense_bot.service import ExpenseService, message_ref
from expense_bot.sheets import Expense
from tests.fakes import FakeClaude, FakeRates, FakeSheets

SENT = datetime(2026, 11, 21, 19, 55, tzinfo=timezone.utc)
CHAT = -100


def make_service(sheets=None, claude=None, rates=None) -> ExpenseService:
    return ExpenseService(sheets or FakeSheets(), claude, rates or FakeRates())


def add(service, text, message_id=1, sent=SENT):
    return asyncio.run(service.add_from_message(text, "Sergei", CHAT, message_id, sent))


def test_known_word_goes_from_dictionary_without_claude():
    sheets, claude = FakeSheets(), FakeClaude()
    [result] = add(make_service(sheets, claude), "450 кофе")
    assert result.status == "added"
    assert result.category.source == "dictionary"
    assert claude.classify_calls == []
    assert sheets.rows == [Expense(
        date=date(2026, 11, 21), amount=Decimal("450"), currency="RSD", category="Кафе",
        comment="кофе", author="Sergei", ref="tg:-100:1", amount_rsd=Decimal("450.00"),
    )]


def test_unknown_word_goes_to_claude_and_is_remembered():
    sheets = FakeSheets()
    claude = FakeClaude({"шаурма у метро": ("Кафе", "шаурма")})
    service = make_service(sheets, claude)
    [first] = add(service, "350 шаурма у метро", message_id=1)
    assert (first.category.category, first.category.source) == ("Кафе", "claude")
    assert sheets.saved_aliases == [("шаурма", "Кафе")]
    # второй раз слово уже в словаре, Claude не нужен
    [second] = add(service, "300 шаурма", message_id=2)
    assert second.category.source == "dictionary"
    assert len(claude.classify_calls) == 1


def test_claude_fallback_category_is_not_remembered():
    sheets = FakeSheets()
    claude = FakeClaude({"налог": ("Прочие", "налог")})
    [result] = add(make_service(sheets, claude), "900 налог")
    assert result.category.category == "Прочие"
    assert sheets.saved_aliases == []


def test_without_claude_unknown_word_goes_to_fallback():
    sheets = FakeSheets()
    [result] = add(make_service(sheets, claude=None), "350 шаурма")
    assert (result.category.category, result.category.source) == ("Прочие", "fallback")
    assert result.category.keyword == "шаурма"
    assert len(sheets.rows) == 1


def test_claude_failure_does_not_lose_expense():
    sheets, claude = FakeSheets(), FakeClaude()
    claude.fail = True
    [result] = add(make_service(sheets, claude), "350 шаурма")
    assert result.status == "added"
    assert result.category.source == "fallback"
    assert len(sheets.rows) == 1


@pytest.mark.parametrize("text", ["кафе", "0 кофе", "2 кофе 450"])
def test_message_without_amount_is_not_written(text):
    sheets = FakeSheets()
    [result] = add(make_service(sheets), text)
    assert result.status == "no_amount"
    assert sheets.rows == []


def test_same_message_twice_is_written_once():
    sheets = FakeSheets()
    service = make_service(sheets)
    add(service, "450 кофе")
    [again] = add(service, "450 кофе")
    assert again.status == "duplicate"
    assert len(sheets.rows) == 1


def test_multiline_message_gives_one_row_per_line():
    sheets = FakeSheets()
    results = add(make_service(sheets), "450 кофе\n\nтакси\n1 200 сад")
    assert [r.status for r in results] == ["added", "no_amount", "added"]
    assert [e.ref for e in sheets.rows] == ["tg:-100:1:0", "tg:-100:1:2"]
    assert sheets.append_calls == 1


def test_date_is_local_not_utc():
    sheets = FakeSheets()
    add(make_service(sheets), "450 кофе", sent=datetime(2026, 11, 30, 23, 30, tzinfo=timezone.utc))
    assert sheets.rows[0].date == date(2026, 12, 1)


def test_foreign_currency_is_converted():
    sheets = FakeSheets()
    rates = FakeRates({"EUR": Decimal("117.2")})
    add(make_service(sheets, rates=rates), "20 евро такси")
    assert sheets.rows[0].amount_rsd == Decimal("2344.00")


def test_missing_rate_keeps_row_without_rsd():
    sheets = FakeSheets()
    [result] = add(make_service(sheets), "15 фунтов музей")
    assert result.status == "added"
    assert sheets.rows[0].amount_rsd is None


def test_edit_updates_amount_and_keeps_manual_category():
    sheets = FakeSheets()
    service = make_service(sheets)
    add(service, "450 кофе")
    sheets.update_category(2, "Развлечения")  # поправили категорию вручную
    status = asyncio.run(service.edit_from_message("540 кофе", CHAT, 1, SENT))
    assert status == "updated"
    assert sheets.rows[0].amount == Decimal("540")
    assert sheets.rows[0].category == "Развлечения"


def test_edit_with_new_comment_recategorizes():
    sheets = FakeSheets()
    service = make_service(sheets)
    add(service, "450 кофе")
    asyncio.run(service.edit_from_message("450 такси", CHAT, 1, SENT))
    assert sheets.rows[0].category == "Транспорт"


@pytest.mark.parametrize("message_id, text, expected", [
    (1, "кофе", "no_amount"),  # из исправленного сообщения пропала сумма
    (99, "450 кофе", "not_found"),  # это сообщение ещё не записано
])
def test_edit_statuses(message_id, text, expected):
    sheets = FakeSheets()
    service = make_service(sheets)
    add(service, "450 кофе", message_id=1)
    assert asyncio.run(service.edit_from_message(text, CHAT, message_id, SENT)) == expected
    assert sheets.rows[0].amount == Decimal("450")


def test_change_category_updates_row_and_dictionary():
    sheets = FakeSheets()
    service = make_service(sheets)
    add(service, "350 шаурма")
    index = service.category_names().index("Кафе")
    assert service.change_category(CHAT, 1, None, index, "шаурма") == "Кафе"
    assert sheets.rows[0].category == "Кафе"
    assert ("шаурма", "Кафе") in sheets.saved_aliases


@pytest.mark.parametrize("message_id, index", [(99, 0), (1, 100)])
def test_change_category_for_unknown_row_or_index(message_id, index):
    service = make_service()
    add(service, "350 шаурма")
    assert service.change_category(CHAT, message_id, None, index, "шаурма") is None


def test_message_ref():
    assert message_ref(-100, 5) == "tg:-100:5"
    assert message_ref(-100, 5, 0) == "tg:-100:5:0"


STATEMENT = {
    "account_currency": "RSD",
    "total_debit": 15950,
    "transactions": [
        {"date": "2026-11-03", "amount": 4200, "direction": "debit", "kind": "purchase",
         "merchant": "MAXI 0123 NOVI SAD", "reference": ""},
        {"date": "2026-11-05", "amount": 10000, "direction": "debit", "kind": "atm",
         "merchant": "BANKOMAT", "reference": ""},
        {"date": "2026-11-06", "amount": 450, "direction": "debit", "kind": "purchase",
         "merchant": "CAFE CENTRAL", "reference": ""},
        {"date": "2026-11-07", "amount": 1300, "direction": "debit", "kind": "payment",
         "merchant": "INFOSTAN", "reference": "R-77"},
        {"date": "2026-11-08", "amount": 1200, "direction": "credit", "kind": "refund",
         "merchant": "MAXI 0123 NOVI SAD", "reference": ""},
        {"date": "2026-11-10", "amount": 90000, "direction": "credit", "kind": "income",
         "merchant": "ZARADA", "reference": ""},
    ],
}


def import_statement(service):
    return asyncio.run(service.import_statement(b"%PDF-1.7 ...", "izvod.pdf", "Sergei"))


def test_import_statement_adds_card_expenses():
    cash_coffee = Expense(date(2026, 11, 6), Decimal("450"), "RSD", "Кафе", "кофе", "Sergei",
                          "tg:-100:1", Decimal("450"))
    sheets = FakeSheets([cash_coffee])
    claude = FakeClaude({
        "MAXI 0123 NOVI SAD": ("Продукты", "maxi"),
        "CAFE CENTRAL": ("Кафе", "central"),
        "INFOSTAN": ("Жильё", "infostan"),
    }, statement=STATEMENT)
    report = import_statement(make_service(sheets, claude))

    card = [e for e in sheets.rows if e.source == "карта"]
    assert [(e.comment, e.amount, e.category) for e in card] == [
        ("MAXI 0123 NOVI SAD", Decimal("4200.00"), "Продукты"),
        ("CAFE CENTRAL", Decimal("450.00"), "Кафе"),
        ("INFOSTAN", Decimal("1300.00"), "Жильё"),
        ("MAXI 0123 NOVI SAD", Decimal("-1200.00"), "Продукты"),  # возврат — минусом
    ]
    assert (report.added, report.total, report.refunds_total) == (4, Decimal("5950.00"), Decimal("-1200.00"))
    assert (report.atm, report.transfers, report.duplicates, report.refunds) == (1, 1, 0, 1)
    assert report.total_mismatch is None
    # кофе за 450 в тот же день есть и в чате, и в выписке
    assert [(cash.comment, card.comment) for cash, card in report.suspicious] == [("кофе", "CAFE CENTRAL")]
    # одинаковые магазины Claude получил один раз
    assert claude.classify_calls == [["MAXI 0123 NOVI SAD", "CAFE CENTRAL", "INFOSTAN"]]


def test_import_same_statement_twice_adds_nothing():
    sheets = FakeSheets()
    service = make_service(sheets, FakeClaude(statement=STATEMENT))
    import_statement(service)
    rows_after_first = len(sheets.rows)
    report = import_statement(service)
    assert report.added == 0
    assert report.duplicates == 4
    assert len(sheets.rows) == rows_after_first


def test_import_reports_total_mismatch():
    statement = {**STATEMENT, "total_debit": 20000}
    report = import_statement(make_service(claude=FakeClaude(statement=statement)))
    assert report.total_mismatch == (Decimal("20000"), Decimal("15950.00"))


def test_import_without_claude_is_refused():
    with pytest.raises(ClaudeError):
        import_statement(make_service(claude=None))
