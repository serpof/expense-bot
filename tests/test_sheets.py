from decimal import Decimal
import pytest
import expense_bot.sheets as sheets
from unittest.mock import Mock

@pytest.mark.parametrize("expense, expected_row", [
    ({"amount": Decimal("450"), "currency": "RSD", "comment": "кофе"},
     ["2026-10-21", 450.0, "RSD", "", "кофе", "Sergei", 101]),
    ({"amount": Decimal("450"), "currency": "RSD", "comment": None},
     ["2026-10-21", 450.0, "RSD", "", "", "Sergei", 101]),
    ({"amount": Decimal("156.56"), "currency": "RSD", "comment": None},
     ["2026-10-21", 156.56, "RSD", "", "", "Sergei", 101]),
])
def test_append_expense(expense, expected_row):
    ws = Mock()
    sheets.append_expense(ws, expense, author="Sergei", message_id=101, date="2026-10-21")
    ws.append_row.assert_called_once_with(expected_row, value_input_option="USER_ENTERED")