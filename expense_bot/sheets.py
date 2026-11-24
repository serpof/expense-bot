import logging
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from gspread.exceptions import WorksheetNotFound
from gspread.utils import DateTimeOption, ValueRenderOption, rowcol_to_a1

from expense_bot.categories import DEFAULT_ALIASES, DEFAULT_CATEGORIES, normalize_word

HEADERS = [
    "Дата", "Сумма", "Валюта", "Категория", "Комментарий",
    "Автор", "ID сообщения", "Сумма RSD", "Источник",
]
COL_CATEGORY = HEADERS.index("Категория") + 1
COL_REF = HEADERS.index("ID сообщения") + 1
CATEGORIES_TAB = "Категории"
ALIASES_TAB = "Словарь"
CASH = "наличные"
CARD = "карта"

# В Google Таблицах дата — число дней с 30.12.1899.
SHEETS_EPOCH = date(1899, 12, 30)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Expense:
    date: date
    amount: Decimal
    currency: str
    category: str
    comment: str
    author: str
    ref: str  # «tg:<чат>:<сообщение>» для трат из чата, «card:<хэш>» для выписки
    amount_rsd: Decimal | None
    source: str = CASH

    def with_category(self, category: str) -> "Expense":
        return replace(self, category=category)


def rsd_amount(expense: Expense) -> Decimal | None:
    """Сумма в динарах. У старых строк без «Сумма RSD» берём сумму, если она уже в RSD."""
    if expense.amount_rsd is not None:
        return expense.amount_rsd
    return expense.amount if expense.currency == "RSD" else None


def to_row(expense: Expense) -> list:
    """Строка для таблицы. Decimal переводим во float: gspread шлёт данные в JSON."""
    return [
        expense.date.isoformat(),
        float(expense.amount),
        expense.currency,
        expense.category,
        expense.comment,
        expense.author,
        expense.ref,
        float(expense.amount_rsd) if expense.amount_rsd is not None else "",
        expense.source,
    ]


def _to_date(value) -> date:
    if isinstance(value, (int, float)):
        return SHEETS_EPOCH + timedelta(days=int(value))
    return date.fromisoformat(str(value).strip())


def _to_decimal(value) -> Decimal:
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    return Decimal(str(value).strip().replace(" ", "").replace(" ", "").replace(",", "."))


def from_row(values: list) -> Expense | None:
    """Строка таблицы (без форматирования) → Expense. Битая строка → None."""
    cells = list(values) + [""] * (len(HEADERS) - len(values))
    try:
        day = _to_date(cells[0])
        amount = _to_decimal(cells[1])
    except (ValueError, InvalidOperation):
        return None
    try:
        amount_rsd = _to_decimal(cells[7]) if str(cells[7]).strip() else None
    except InvalidOperation:
        amount_rsd = None
    return Expense(
        date=day,
        amount=amount,
        currency=str(cells[2]).strip().upper() or "RSD",
        category=str(cells[3]).strip(),
        comment=str(cells[4]).strip(),
        author=str(cells[5]).strip(),
        ref=str(cells[6]).strip(),
        amount_rsd=amount_rsd,
        source=str(cells[8]).strip() or CASH,
    )


class Sheets:
    """Вся работа с Google Таблицей. Траты — на первом листе, плюс вкладки категорий и словаря."""

    def __init__(self, spreadsheet):
        self._book = spreadsheet
        self._expenses = spreadsheet.sheet1
        self._categories_ws = None
        self._aliases_ws = None

    def ensure_layout(self) -> None:
        """Дописывает недостающие заголовки и создаёт вкладки «Категории» и «Словарь»."""
        header = [cell.strip() for cell in self._expenses.row_values(1)]
        if not header:
            self._expenses.update([HEADERS], "A1")
        elif header == HEADERS[:len(header)] and len(header) < len(HEADERS):
            start = rowcol_to_a1(1, len(header) + 1)
            self._expenses.update([HEADERS[len(header):]], start)
        elif header[:len(HEADERS)] != HEADERS:
            log.warning("Заголовки на первом листе отличаются от ожидаемых, не трогаю их")
        self._categories_ws = self._tab(
            CATEGORIES_TAB, [["Категория", "Описание"], *map(list, DEFAULT_CATEGORIES)]
        )
        self._aliases_ws = self._tab(
            ALIASES_TAB, [["Слово", "Категория"], *map(list, DEFAULT_ALIASES.items())]
        )

    def _tab(self, title: str, initial_rows: list[list[str]]):
        try:
            return self._book.worksheet(title)
        except WorksheetNotFound:
            worksheet = self._book.add_worksheet(title=title, rows=200, cols=2)
            worksheet.update(initial_rows, "A1")
            return worksheet

    def categories(self) -> list[tuple[str, str]]:
        seen, result = set(), []
        for row in self._categories_ws.get_all_values()[1:]:
            name = row[0].strip() if row else ""
            if name and name.lower() not in seen:
                seen.add(name.lower())
                result.append((name, row[1].strip() if len(row) > 1 else ""))
        return result or DEFAULT_CATEGORIES

    def aliases(self) -> dict[str, str]:
        result = {}
        for row in self._aliases_ws.get_all_values()[1:]:
            if len(row) >= 2 and row[0].strip() and row[1].strip():
                result[normalize_word(row[0])] = row[1].strip()
        return result

    def save_alias(self, word: str, category: str) -> None:
        cell = self._aliases_ws.find(word, in_column=1)
        if cell:
            self._aliases_ws.update_cell(cell.row, 2, category)
        else:
            self._aliases_ws.append_row([word, category])

    def append(self, expenses: list[Expense]) -> None:
        if expenses:
            rows = [to_row(expense) for expense in expenses]
            self._expenses.append_rows(rows, value_input_option="USER_ENTERED")

    def read_all(self) -> list[Expense]:
        values = self._expenses.get_all_values(
            value_render_option=ValueRenderOption.unformatted,
            date_time_render_option=DateTimeOption.serial_number,
        )
        return [expense for expense in map(from_row, values[1:]) if expense is not None]

    def refs(self) -> set[str]:
        return {str(value).strip() for value in self._expenses.col_values(COL_REF)[1:] if str(value).strip()}

    def find_row(self, ref: str) -> int | None:
        cell = self._expenses.find(ref, in_column=COL_REF)
        return cell.row if cell else None

    def read_row(self, row: int) -> Expense | None:
        values = self._expenses.row_values(
            row,
            value_render_option=ValueRenderOption.unformatted,
            date_time_render_option=DateTimeOption.serial_number,
        )
        return from_row(values)

    def update_row(self, row: int, expense: Expense) -> None:
        last = rowcol_to_a1(row, len(HEADERS))
        self._expenses.update([to_row(expense)], f"A{row}:{last}", value_input_option="USER_ENTERED")

    def update_category(self, row: int, category: str) -> None:
        self._expenses.update_cell(row, COL_CATEGORY, category)
