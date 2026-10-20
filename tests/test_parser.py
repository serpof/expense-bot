from decimal import Decimal
import pytest
from expense_bot.parser import parse_expense

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
])
def test_parse_expense(text, expected):
    assert parse_expense(text) == expected