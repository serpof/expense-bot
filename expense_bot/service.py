import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from expense_bot.categories import (
    CategoryResult,
    fallback_category,
    find_in_dictionary,
    learnable_keyword,
    match_category,
    words,
)
from expense_bot.dates import local_date
from expense_bot.llm import Claude, ClaudeError
from expense_bot.parser import parse_expense
from expense_bot.rates import Rates
from expense_bot.reports import month_report
from expense_bot.sheets import CARD, CASH, Expense, Sheets
from expense_bot.statement import (
    debit_total,
    parse_transactions,
    plan_import,
    possible_duplicates,
    prepare_file,
)

CACHE_SECONDS = 300

log = logging.getLogger(__name__)


def message_ref(chat_id: int, message_id: int, line: int | None = None) -> str:
    """ID строки в таблице. Номер строки сообщения — только для многострочных сообщений."""
    ref = f"tg:{chat_id}:{message_id}"
    return ref if line is None else f"{ref}:{line}"


@dataclass
class LineResult:
    line: int | None  # номер строки в многострочном сообщении
    text: str
    status: str  # added | duplicate | no_amount
    expense: Expense | None = None
    category: CategoryResult | None = None


@dataclass
class ImportReport:
    found: int = 0  # сколько операций Claude нашёл в выписке
    added: int = 0
    total: Decimal = Decimal(0)
    atm: int = 0
    transfers: int = 0
    duplicates: int = 0
    refunds: int = 0
    refunds_total: Decimal = Decimal(0)
    without_rate: int = 0
    total_mismatch: tuple[Decimal, Decimal] | None = None  # (по выписке, распознано)
    suspicious: list[tuple[Expense, Expense]] = field(default_factory=list)


class ExpenseService:
    def __init__(self, sheets: Sheets, claude: Claude | None, rates: Rates):
        self.sheets = sheets
        self.claude = claude
        self.rates = rates
        self._categories: list[tuple[str, str]] = []
        self._aliases: dict[str, str] = {}
        self._loaded_at = float("-inf")

    # --- категории и словарь -------------------------------------------------

    def _reload_if_stale(self) -> None:
        # Категории и словарь можно править руками в таблице: перечитываем раз в 5 минут.
        if time.monotonic() - self._loaded_at > CACHE_SECONDS:
            self._categories = self.sheets.categories()
            self._aliases = self.sheets.aliases()
            self._loaded_at = time.monotonic()

    def category_names(self) -> list[str]:
        self._reload_if_stale()
        return [name for name, _ in self._categories]

    def _remember(self, keyword: str, category: str) -> None:
        if self._aliases.get(keyword) != category:
            self.sheets.save_alias(keyword, category)
            self._aliases[keyword] = category

    async def categorize(self, texts: list[str | None]) -> list[CategoryResult]:
        """Категория для каждого текста: сначала словарь, потом Claude, иначе «Прочие»."""
        names = self.category_names()
        fallback = fallback_category(names)
        results: list[CategoryResult | None] = [None] * len(texts)
        unknown: list[int] = []
        for i, text in enumerate(texts):
            found = find_in_dictionary(text, self._aliases)
            category = match_category(found[0], names) if found else None
            if category:
                results[i] = CategoryResult(category, found[1], "dictionary")
            elif words(text):
                unknown.append(i)
            else:
                results[i] = CategoryResult(fallback, None, "fallback")

        if unknown and self.claude:
            unique = list(dict.fromkeys(texts[i] for i in unknown))
            try:
                answers = dict(zip(unique, await self.claude.classify(unique, self._categories)))
            except Exception as error:  # Claude недоступен — не теряем трату, пишем в «Прочие»
                log.warning("Claude не определил категории: %s", type(error).__name__)
                answers = {}
            for i in unknown:
                answer = answers.get(texts[i])
                if answer is None:
                    continue
                keyword = learnable_keyword(answer.keyword, texts[i])
                # В «Прочие» Claude кладёт то, в чём не уверен: такие слова не запоминаем.
                if keyword and answer.category != fallback:
                    self._remember(keyword, answer.category)
                results[i] = CategoryResult(answer.category, keyword, "claude")

        return [r or CategoryResult(fallback, (words(t) or [None])[0], "fallback")
                for r, t in zip(results, texts)]

    # --- траты из чата -------------------------------------------------------

    async def add_from_message(
        self, text: str, author: str, chat_id: int, message_id: int, sent_at: datetime
    ) -> list[LineResult]:
        """Записывает траты из сообщения. В одном сообщении можно писать по трате в строке."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        multiline = len(lines) > 1
        day = local_date(sent_at)
        existing = self.sheets.refs()

        results: list[LineResult] = []
        pending: list[tuple[LineResult, dict]] = []
        for i, line in enumerate(lines):
            parsed = parse_expense(line)
            result = LineResult(i if multiline else None, line, "no_amount")
            results.append(result)
            if parsed["amount"] is None or parsed["amount"] <= 0:
                continue
            if message_ref(chat_id, message_id, result.line) in existing:
                result.status = "duplicate"
                continue
            pending.append((result, parsed))

        categories = await self.categorize([parsed["comment"] for _, parsed in pending])
        new_expenses = []
        for (result, parsed), category in zip(pending, categories):
            expense = Expense(
                date=day,
                amount=parsed["amount"],
                currency=parsed["currency"],
                category=category.category,
                comment=parsed["comment"] or "",
                author=author,
                ref=message_ref(chat_id, message_id, result.line),
                amount_rsd=await self.rates.to_rsd(parsed["amount"], parsed["currency"], day),
                source=CASH,
            )
            result.status, result.expense, result.category = "added", expense, category
            new_expenses.append(expense)
        self.sheets.append(new_expenses)
        return results

    async def edit_from_message(
        self, text: str, chat_id: int, message_id: int, sent_at: datetime
    ) -> str:
        """Правка сообщения в чате правит строку в таблице. Статусы: updated, not_found, no_amount."""
        row = self.sheets.find_row(message_ref(chat_id, message_id))
        old = self.sheets.read_row(row) if row else None
        if old is None:
            return "not_found"
        parsed = parse_expense(text)
        if parsed["amount"] is None or parsed["amount"] <= 0:
            return "no_amount"
        comment = parsed["comment"] or ""
        category = old.category
        if comment != old.comment:
            category = (await self.categorize([parsed["comment"]]))[0].category
        updated = Expense(
            date=old.date,
            amount=parsed["amount"],
            currency=parsed["currency"],
            category=category,
            comment=comment,
            author=old.author,
            ref=old.ref,
            amount_rsd=await self.rates.to_rsd(parsed["amount"], parsed["currency"], old.date),
            source=old.source,
        )
        self.sheets.update_row(row, updated)
        return "updated"

    def is_recorded(self, chat_id: int, message_id: int) -> bool:
        """Записано ли сообщение одной тратой (без номера строки)."""
        return self.sheets.find_row(message_ref(chat_id, message_id)) is not None

    def change_category(
        self, chat_id: int, message_id: int, line: int | None, index: int, keyword: str | None
    ) -> str | None:
        """Кнопка выбора категории: правит строку и запоминает слово. None — строка не найдена."""
        names = self.category_names()
        if not 0 <= index < len(names):
            return None
        row = self.sheets.find_row(message_ref(chat_id, message_id, line))
        if row is None:
            return None
        category = names[index]
        self.sheets.update_category(row, category)
        learned = learnable_keyword(keyword, keyword)
        if learned:
            self._remember(learned, category)
        return category

    # --- выписка -------------------------------------------------------------

    async def import_statement(self, data: bytes, filename: str, author: str) -> ImportReport:
        if self.claude is None:
            raise ClaudeError("для выписки нужен ключ Claude API")
        extracted = await self.claude.extract_statement(prepare_file(data, filename))
        transactions = parse_transactions(extracted)
        currency = str(extracted.get("account_currency") or "RSD").strip().upper()
        currency = "RSD" if currency in {"DIN", "ДИН", ""} else currency

        report = ImportReport(found=len(transactions))
        stated = extracted.get("total_debit")
        if stated is not None:
            stated = Decimal(str(stated))
            recognized = debit_total(transactions)
            if abs(stated - recognized) > 1:
                report.total_mismatch = (stated, recognized)

        existing_expenses = self.sheets.read_all()
        plan = plan_import(transactions, {e.ref for e in existing_expenses})
        report.atm, report.transfers = plan.atm, plan.transfers
        report.duplicates, report.refunds = plan.duplicates, plan.refunds

        categories = await self.categorize([tx.merchant for tx, _, _ in plan.to_add])
        new_expenses = []
        for (tx, ref, amount), category in zip(plan.to_add, categories):
            amount_rsd = await self.rates.to_rsd(amount, currency, tx.date)
            new_expenses.append(Expense(
                date=tx.date,
                amount=amount,
                currency=currency,
                category=category.category,
                comment=tx.merchant,
                author=author,
                ref=ref,
                amount_rsd=amount_rsd,
                source=CARD,
            ))
            if amount_rsd is None:
                report.without_rate += 1
            elif amount < 0:
                report.refunds_total += amount_rsd
            else:
                report.total += amount_rsd
        self.sheets.append(new_expenses)
        report.added = len(new_expenses)
        report.suspicious = possible_duplicates(new_expenses, existing_expenses)
        return report

    # --- отчёты --------------------------------------------------------------

    def month_report(self, year: int, month: int) -> str:
        return month_report(self.sheets.read_all(), year, month)
