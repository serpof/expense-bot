from decimal import Decimal
import pytest
from expense_bot.parser import normalize_number, parse_expense

@pytest.mark.parametrize("text, expected", [
    ("48 000 сад", {"amount": Decimal("48000"), "currency": "RSD", "comment": "сад"}),
    ("1000", {"amount": Decimal("1000"), "currency": "RSD", "comment": None}),
    ("157,56 булка", {"amount": Decimal("157.56"), "currency": "RSD", "comment": "булка"}),
    ("1000 рублей", {"amount": Decimal("1000"), "currency": "RUB", "comment": None}),
    ("кафе", {"amount": None, "currency": "RSD", "comment": "кафе"}),
    ("500 динар сауна", {"amount": Decimal("500"), "currency": "RSD", "comment": "сауна"}),
    ("2 кофе 450", {"amount": None, "currency": "RSD", "comment": "кофе"}),
    ("450  кофе", {"amount": Decimal("450"), "currency": "RSD", "comment": "кофе"}),
    ("450 кофе латте", {"amount": Decimal("450"), "currency": "RSD", "comment": "кофе латте"}),
    ("48  000 сад", {"amount": Decimal("48000"), "currency": "RSD", "comment": "сад"}),
    # валюты и символы
    ("500 динаров сауна", {"amount": Decimal("500"), "currency": "RSD", "comment": "сауна"}),
    ("20 евро такси", {"amount": Decimal("20"), "currency": "EUR", "comment": "такси"}),
    ("450€ кофе", {"amount": Decimal("450"), "currency": "EUR", "comment": "кофе"}),
    ("€12 кофе", {"amount": Decimal("12"), "currency": "EUR", "comment": "кофе"}),
    ("12,5 € сувениры", {"amount": Decimal("12.5"), "currency": "EUR", "comment": "сувениры"}),
    ("15 фунтов музей", {"amount": Decimal("15"), "currency": "GBP", "comment": "музей"}),
    ("$30 подписка", {"amount": Decimal("30"), "currency": "USD", "comment": "подписка"}),
    # точка: разделитель тысяч в Сербии или дробная часть
    ("1.200 продукты", {"amount": Decimal("1200"), "currency": "RSD", "comment": "продукты"}),
    ("1.200,50 продукты", {"amount": Decimal("1200.50"), "currency": "RSD", "comment": "продукты"}),
    ("4.5 евро кофе", {"amount": Decimal("4.5"), "currency": "EUR", "comment": "кофе"}),
    ("1.2.3 кофе", {"amount": None, "currency": "RSD", "comment": "кофе"}),
    ("Кофе 450", {"amount": Decimal("450"), "currency": "RSD", "comment": "кофе"}),
])
def test_parse_expense(text, expected):
    assert parse_expense(text) == expected


@pytest.mark.parametrize("raw, expected", [
    ("48000", "48000"),
    ("157,56", "157.56"),
    ("1.200", "1200"),
    ("1.200.000", "1200000"),
    ("1.200,50", "1200.50"),
    ("4.5", "4.5"),
    ("12.50", "12.50"),
    ("1,2,3", None),
    ("1.2.3", None),
])
def test_normalize_number(raw, expected):
    assert normalize_number(raw) == expected
