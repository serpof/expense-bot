from datetime import date
from decimal import Decimal

from expense_bot.reports import money, month_report
from expense_bot.sheets import Expense


def expense(day, amount, category, source="наличные", currency="RSD", amount_rsd="same"):
    rsd = Decimal(amount) if amount_rsd == "same" else amount_rsd
    return Expense(day, Decimal(amount), currency, category, "", "Sergei", "ref", rsd, source)


NOVEMBER = [
    expense(date(2026, 11, 1), "48000", "Дети", source="карта"),
    expense(date(2026, 11, 3), "32000", "Продукты", source="карта"),
    expense(date(2026, 11, 5), "-2000", "Продукты", source="карта"),  # возврат
    expense(date(2026, 11, 9), "9450", "Кафе"),
    expense(date(2026, 11, 12), "15", "Развлечения", currency="GBP", amount_rsd=None),
]
OCTOBER = [expense(date(2026, 10, 10), "78000", "Продукты")]
DECEMBER = [expense(date(2026, 12, 1), "1000", "Кафе")]


def test_month_report():
    report = month_report(OCTOBER + NOVEMBER + DECEMBER, 2026, 11)
    assert report.splitlines() == [
        "<b>Ноябрь 2026: 87 450 RSD</b>",
        "+12% к октябрю (78 000 RSD)",
        "<pre>Дети         48 000   55%",
        "Продукты     30 000   34%",
        "Кафе          9 450   11%</pre>",
        "Наличные: 9 450 · карта: 78 000",
        "⚠️ Без курса и не в итоге: 1. Впиши «Сумма RSD» в таблице.",
    ]


def test_first_month_has_no_comparison():
    report = month_report(NOVEMBER, 2026, 11)
    assert "к октябрю" not in report


def test_january_compares_with_december():
    january = [expense(date(2027, 1, 5), "500", "Кафе")]
    assert "к декабрю" in month_report(DECEMBER + january, 2027, 1)


def test_empty_month():
    assert month_report(NOVEMBER, 2026, 9) == "За сентябрь 2026 трат нет."


def test_category_names_are_escaped():
    report = month_report([expense(date(2026, 11, 1), "100", "Дом & <сад>")], 2026, 11)
    assert "Дом &amp; &lt;сад&gt;" in report


def test_money():
    assert money(Decimal("87450.49")) == "87 450"
    assert money(Decimal("-2000")) == "-2 000"
