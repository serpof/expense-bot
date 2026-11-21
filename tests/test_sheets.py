from datetime import date
from decimal import Decimal
from unittest.mock import Mock

import pytest
from gspread.exceptions import WorksheetNotFound

from expense_bot.sheets import HEADERS, Expense, Sheets, from_row, rsd_amount, to_row


def make_expense(**changes) -> Expense:
    values = dict(
        date=date(2026, 10, 21), amount=Decimal("450"), currency="RSD", category="Кафе",
        comment="кофе", author="Sergei", ref="tg:1:101", amount_rsd=Decimal("450"),
    )
    values.update(changes)
    return Expense(**values)


@pytest.mark.parametrize("expense, expected_row", [
    (make_expense(),
     ["2026-10-21", 450.0, "RSD", "Кафе", "кофе", "Sergei", "tg:1:101", 450.0, "наличные"]),
    (make_expense(comment="", category=""),
     ["2026-10-21", 450.0, "RSD", "", "", "Sergei", "tg:1:101", 450.0, "наличные"]),
    # Decimal в JSON не превращается — в таблицу уходит float
    (make_expense(amount=Decimal("156.56"), amount_rsd=Decimal("156.56")),
     ["2026-10-21", 156.56, "RSD", "Кафе", "кофе", "Sergei", "tg:1:101", 156.56, "наличные"]),
    # курс не нашёлся — пустая ячейка, а не 0
    (make_expense(amount=Decimal("20"), currency="EUR", amount_rsd=None, source="карта"),
     ["2026-10-21", 20.0, "EUR", "Кафе", "кофе", "Sergei", "tg:1:101", "", "карта"]),
])
def test_to_row(expense, expected_row):
    assert to_row(expense) == expected_row


@pytest.mark.parametrize("values, expected", [
    # без форматирования Google отдаёт дату числом дней с 30.12.1899
    ([46316, 450, "RSD", "Кафе", "кофе", "Sergei", "tg:1:101", 450, "наличные"],
     make_expense(amount_rsd=Decimal("450"))),
    # старая строка из 7 колонок: нет «Сумма RSD» и «Источник»
    (["2026-10-21", "157,56", "RSD", "", "булка", "Sergei", 1],
     make_expense(amount=Decimal("157.56"), category="", comment="булка", ref="1", amount_rsd=None)),
])
def test_from_row(values, expected):
    assert from_row(values) == expected


@pytest.mark.parametrize("values", [
    ["итого", 450],
    ["2026-10-21", "много"],
    [],
])
def test_from_row_skips_broken_rows(values):
    assert from_row(values) is None


@pytest.mark.parametrize("expense, expected", [
    (make_expense(amount_rsd=Decimal("450")), Decimal("450")),
    (make_expense(amount_rsd=None), Decimal("450")),  # старая строка в динарах
    (make_expense(currency="EUR", amount_rsd=None), None),
])
def test_rsd_amount(expense, expected):
    assert rsd_amount(expense) == expected


def make_book(header: list[str], tabs_exist: bool = True):
    book = Mock()
    book.sheet1.row_values.return_value = header
    if not tabs_exist:
        book.worksheet.side_effect = WorksheetNotFound("нет вкладки")
    return book


def test_ensure_layout_adds_missing_headers_to_old_table():
    book = make_book(HEADERS[:7])
    Sheets(book).ensure_layout()
    book.sheet1.update.assert_called_once_with([["Сумма RSD", "Источник"]], "H1")


def test_ensure_layout_fills_empty_table():
    book = make_book([])
    Sheets(book).ensure_layout()
    book.sheet1.update.assert_called_once_with([HEADERS], "A1")


def test_ensure_layout_keeps_unknown_headers():
    book = make_book(["Date", "Amount"])
    Sheets(book).ensure_layout()
    book.sheet1.update.assert_not_called()


def test_ensure_layout_creates_tabs_with_defaults():
    book = make_book(HEADERS, tabs_exist=False)
    Sheets(book).ensure_layout()
    titles = [call.kwargs["title"] for call in book.add_worksheet.call_args_list]
    assert titles == ["Категории", "Словарь"]
    rows = book.add_worksheet.return_value.update.call_args_list[0].args[0]
    assert rows[0] == ["Категория", "Описание"]
    assert ["Прочие", "всё, что не подходит к остальным"] in rows


def test_append_writes_all_rows_in_one_call():
    book = make_book(HEADERS)
    sheets = Sheets(book)
    sheets.append([make_expense(), make_expense(ref="tg:1:102")])
    book.sheet1.append_rows.assert_called_once()
    rows = book.sheet1.append_rows.call_args.args[0]
    assert [row[6] for row in rows] == ["tg:1:101", "tg:1:102"]
    assert book.sheet1.append_rows.call_args.kwargs == {"value_input_option": "USER_ENTERED"}


def test_append_nothing_makes_no_request():
    book = make_book(HEADERS)
    Sheets(book).append([])
    book.sheet1.append_rows.assert_not_called()


def test_aliases_are_normalized():
    book = make_book(HEADERS)
    sheets = Sheets(book)
    sheets.ensure_layout()
    book.worksheet.return_value.get_all_values.return_value = [
        ["Слово", "Категория"], ["Булка", "Продукты"], ["ёлка", "Прочие"], ["", "Кафе"],
    ]
    assert sheets.aliases() == {"булка": "Продукты", "елка": "Прочие"}


def test_categories_skip_duplicates_and_empty_rows():
    book = make_book(HEADERS)
    sheets = Sheets(book)
    sheets.ensure_layout()
    book.worksheet.return_value.get_all_values.return_value = [
        ["Категория", "Описание"], ["Кафе", "рестораны"], ["кафе", "дубль"], [""], ["Дети"],
    ]
    assert sheets.categories() == [("Кафе", "рестораны"), ("Дети", "")]
