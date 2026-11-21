from collections import defaultdict
from decimal import Decimal
from html import escape

from expense_bot.sheets import CARD, CASH, Expense, rsd_amount

MONTHS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
# Для фразы «+12% к октябрю».
MONTHS_DATIVE = [
    "январю", "февралю", "марту", "апрелю", "маю", "июню",
    "июлю", "августу", "сентябрю", "октябрю", "ноябрю", "декабрю",
]


def money(value: Decimal) -> str:
    """87450.4 → «87 450». Копейки в отчёте не нужны."""
    return f"{round(value):,}".replace(",", " ")


def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _in_month(expenses: list[Expense], year: int, month: int) -> list[Expense]:
    return [e for e in expenses if e.date.year == year and e.date.month == month]


def month_total(expenses: list[Expense], year: int, month: int) -> Decimal:
    amounts = (rsd_amount(e) for e in _in_month(expenses, year, month))
    return sum((a for a in amounts if a is not None), Decimal(0))


def month_report(expenses: list[Expense], year: int, month: int) -> str:
    """Отчёт за месяц в HTML для Telegram: итог, сравнение с прошлым месяцем, категории."""
    title = f"{MONTHS[month - 1]} {year}"
    rows = _in_month(expenses, year, month)
    if not rows:
        return f"За {title.lower()} трат нет."

    by_category: dict[str, Decimal] = defaultdict(Decimal)
    by_source: dict[str, Decimal] = defaultdict(Decimal)
    without_rate = 0
    for expense in rows:
        amount = rsd_amount(expense)
        if amount is None:
            without_rate += 1
            continue
        by_category[expense.category or "Без категории"] += amount
        by_source[expense.source] += amount
    total = sum(by_category.values(), Decimal(0))

    lines = [f"<b>{escape(title)}: {money(total)} RSD</b>"]
    prev_year, prev_month = previous_month(year, month)
    prev_total = month_total(expenses, prev_year, prev_month)
    if prev_total > 0:
        change = round((total - prev_total) / prev_total * 100)
        lines.append(f"{change:+d}% к {MONTHS_DATIVE[prev_month - 1]} ({money(prev_total)} RSD)")

    ranked = sorted(by_category.items(), key=lambda item: item[1], reverse=True)
    width = max(len(name) for name, _ in ranked)
    table = []
    for name, amount in ranked:
        share = round(amount / total * 100) if total else 0
        table.append(f"{name:<{width}}  {money(amount):>9}  {share:>3}%")
    lines.append("<pre>" + escape("\n".join(table)) + "</pre>")

    lines.append(f"Наличные: {money(by_source[CASH])} · карта: {money(by_source[CARD])}")
    if without_rate:
        lines.append(f"⚠️ Без курса и не в итоге: {without_rate}. Впиши «Сумма RSD» в таблице.")
    return "\n".join(lines)
