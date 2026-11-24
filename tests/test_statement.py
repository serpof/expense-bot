import io
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import Workbook

from expense_bot.sheets import Expense
from expense_bot.statement import (
    Transaction,
    UnsupportedFile,
    decode_text,
    parse_transactions,
    plan_import,
    possible_duplicates,
    prepare_file,
    transaction_refs,
)


def tx(kind="purchase", direction="debit", amount="450", merchant="CAFE", day=3, reference=""):
    return Transaction(date(2026, 11, day), Decimal(amount), direction, kind, merchant, reference)


@pytest.mark.parametrize("transaction, field", [
    (tx(kind="atm"), "atm"),
    (tx(kind="own_transfer"), "transfers"),
    (tx(kind="income", direction="credit"), "transfers"),
    (tx(kind="other", direction="credit"), "transfers"),
])
def test_plan_skips_non_expenses(transaction, field):
    plan = plan_import([transaction], set())
    assert plan.to_add == []
    assert getattr(plan, field) == 1


@pytest.mark.parametrize("kind", ["purchase", "payment", "fee", "other"])
def test_plan_adds_debits(kind):
    plan = plan_import([tx(kind=kind)], set())
    assert [amount for _, _, amount in plan.to_add] == [Decimal("450")]


def test_refund_is_negative():
    plan = plan_import([tx(kind="refund", direction="credit")], set())
    assert [amount for _, _, amount in plan.to_add] == [Decimal("-450")]
    assert plan.refunds == 1


def test_known_transaction_is_duplicate():
    transaction = tx()
    [ref] = transaction_refs([transaction])
    plan = plan_import([transaction], {ref})
    assert (plan.to_add, plan.duplicates) == ([], 1)


def test_refs_are_stable_and_twins_differ():
    first = transaction_refs([tx(), tx()])
    second = transaction_refs([tx(), tx()])
    assert first == second
    assert first[0] != first[1]  # два кофе по 450 в один день — две разные траты
    assert all(ref.startswith("card:") for ref in first)


def test_refs_ignore_merchant_case():
    assert transaction_refs([tx(merchant="Maxi")]) == transaction_refs([tx(merchant="MAXI")])


def test_parse_transactions_normalizes_and_skips_broken():
    data = {"transactions": [
        {"date": "2026-11-03", "amount": -450.5, "direction": "DEBIT", "kind": "Purchase",
         "merchant": "  MAXI   0123 ", "reference": " 77 "},
        {"date": "3.11.2026", "amount": 100, "direction": "debit", "kind": "purchase",
         "merchant": "X", "reference": ""},
        {"date": "2026-11-04", "amount": 0, "direction": "debit", "kind": "fee",
         "merchant": "BANKA", "reference": ""},
    ]}
    assert parse_transactions(data) == [
        Transaction(date(2026, 11, 3), Decimal("450.50"), "debit", "purchase", "MAXI 0123", "77")
    ]


def test_prepare_pdf():
    assert prepare_file(b"%PDF-1.7 data", "izvod.pdf").pdf == b"%PDF-1.7 data"
    assert prepare_file(b"%PDF-1.7 data", "file").pdf is not None  # PDF без расширения


def test_prepare_xlsx_becomes_text():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Izvod"
    sheet.append(["Datum", "Opis", "Iznos"])
    sheet.append([date(2026, 11, 3), "MAXI", 4200])
    sheet.append([None, None, None])
    buffer = io.BytesIO()
    workbook.save(buffer)
    text = prepare_file(buffer.getvalue(), "izvod.xlsx").text
    assert text.splitlines() == ["# Лист: Izvod", "Datum;Opis;Iznos", "2026-11-03;MAXI;4200"]


def test_prepare_csv_in_windows_1250():
    data = "Datum;Opis\n03.11.2026;Plaćanje računa".encode("cp1250")
    assert "Plaćanje računa" in prepare_file(data, "izvod.csv").text


def test_decode_utf8_with_bom():
    assert decode_text("﻿Datum".encode("utf-8")) == "Datum"


@pytest.mark.parametrize("name", ["izvod.xls", "photo.jpg", "izvod.docx"])
def test_unsupported_formats(name):
    with pytest.raises(UnsupportedFile):
        prepare_file(b"data", name)


def expense(source, amount_rsd, day=6, comment="кофе", currency="RSD"):
    return Expense(date(2026, 11, day), Decimal(amount_rsd or "1"), currency, "Кафе", comment,
                   "Sergei", f"ref-{comment}", Decimal(amount_rsd) if amount_rsd else None, source)


def test_possible_duplicates_match_date_and_amount():
    cash = expense("наличные", "450")
    card_same = expense("карта", "450.00", comment="CAFE")
    card_other_day = expense("карта", "450", day=7, comment="CAFE 2")
    card_other_sum = expense("карта", "500", comment="CAFE 3")
    found = possible_duplicates([card_same, card_other_day, card_other_sum], [cash])
    assert found == [(cash, card_same)]


def test_possible_duplicates_ignore_card_rows_and_missing_rates():
    card_existing = expense("карта", "450", comment="CAFE")
    cash_no_rate = expense("наличные", None, comment="сувенир", currency="GBP")
    new = [expense("карта", "450", comment="CAFE NEW")]
    assert possible_duplicates(new, [card_existing, cash_no_rate]) == []
