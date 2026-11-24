"""Подделки внешних систем для тестов сервиса: таблица в памяти, Claude и курсы без сети."""
from datetime import date
from decimal import Decimal

from expense_bot.categories import DEFAULT_ALIASES, DEFAULT_CATEGORIES
from expense_bot.llm import Classification
from expense_bot.sheets import Expense


class FakeSheets:
    def __init__(self, expenses: list[Expense] | None = None):
        self.rows: list[Expense] = list(expenses or [])
        self.categories_list = list(DEFAULT_CATEGORIES)
        self.alias_map = dict(DEFAULT_ALIASES)
        self.saved_aliases: list[tuple[str, str]] = []
        self.append_calls = 0

    def categories(self):
        return list(self.categories_list)

    def aliases(self):
        return dict(self.alias_map)

    def save_alias(self, word, category):
        self.alias_map[word] = category
        self.saved_aliases.append((word, category))

    def append(self, expenses):
        self.append_calls += 1
        self.rows.extend(expenses)

    def read_all(self):
        return list(self.rows)

    def refs(self):
        return {e.ref for e in self.rows}

    def find_row(self, ref):
        for index, expense in enumerate(self.rows):
            if expense.ref == ref:
                return index + 2  # строка 1 — заголовки
        return None

    def read_row(self, row):
        return self.rows[row - 2]

    def update_row(self, row, expense):
        self.rows[row - 2] = expense

    def update_category(self, row, category):
        self.rows[row - 2] = self.rows[row - 2].with_category(category)


class FakeClaude:
    """Отвечает по заранее заданной таблице «текст → (категория, слово)»."""

    def __init__(self, answers: dict[str, tuple[str, str]] | None = None, statement: dict | None = None):
        self.answers = answers or {}
        self.statement = statement
        self.classify_calls: list[list[str]] = []
        self.fail = False

    async def classify(self, texts, categories):
        self.classify_calls.append(list(texts))
        if self.fail:
            raise RuntimeError("API недоступен")
        return [Classification(*self.answers.get(text, ("Прочие", ""))) for text in texts]

    async def extract_statement(self, statement_file):
        return self.statement


class FakeRates:
    def __init__(self, rates: dict[str, Decimal] | None = None):
        self.rates = {"RSD": Decimal(1), **(rates or {})}

    async def to_rsd(self, amount: Decimal, currency: str, day: date) -> Decimal | None:
        rate = self.rates.get(currency)
        return None if rate is None else (amount * rate).quantize(Decimal("0.01"))
