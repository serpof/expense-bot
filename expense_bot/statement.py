import hashlib
import io
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from expense_bot.llm import StatementFile
from expense_bot.sheets import CASH, Expense, rsd_amount

# Какие операции из выписки считаем тратами. Снятие наличных не трата:
# эти деньги посчитаются, когда наличные потратят и запишут в чат.
EXPENSE_KINDS = {"purchase", "payment", "fee", "other"}
SKIP_ATM = {"atm"}
SKIP_TRANSFERS = {"own_transfer", "income"}

MAX_TEXT_CHARS = 400_000


class UnsupportedFile(Exception):
    pass


@dataclass(frozen=True)
class Transaction:
    date: date
    amount: Decimal  # всегда положительная, в валюте счёта
    direction: str  # debit | credit
    kind: str
    merchant: str
    reference: str


@dataclass
class ImportPlan:
    to_add: list[tuple[Transaction, str, Decimal]] = field(default_factory=list)  # операция, id, сумма со знаком
    atm: int = 0
    transfers: int = 0
    duplicates: int = 0
    refunds: int = 0


def prepare_file(data: bytes, filename: str) -> StatementFile:
    """Файл выписки → то, что можно отправить Claude: PDF как есть, таблицы — текстом."""
    name = filename.lower()
    if name.endswith(".pdf") or data.startswith(b"%PDF"):
        return StatementFile(pdf=data)
    if name.endswith((".xlsx", ".xlsm")):
        text = xlsx_to_text(data)
    elif name.endswith((".csv", ".tsv", ".txt")):
        text = decode_text(data)
    else:
        raise UnsupportedFile(filename)
    if len(text) > MAX_TEXT_CHARS:
        raise UnsupportedFile("файл слишком большой")
    return StatementFile(text=text)


def decode_text(data: bytes) -> str:
    # Сербские банки иногда отдают CSV в Windows-1250.
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def xlsx_to_text(data: bytes) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines = []
    for sheet in workbook.worksheets:
        lines.append(f"# Лист: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = [_cell_text(value) for value in row]
            if any(cells):
                lines.append(";".join(cells))
    workbook.close()
    return "\n".join(lines)


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def parse_transactions(data: dict) -> list[Transaction]:
    """Ответ Claude → операции. Строки с битой датой или нулевой суммой пропускаем."""
    result = []
    for item in data.get("transactions", []):
        try:
            day = date.fromisoformat(item["date"])
            amount = abs(Decimal(str(item["amount"]))).quantize(Decimal("0.01"))
        except (KeyError, ValueError, InvalidOperation):
            continue
        if amount == 0:
            continue
        result.append(Transaction(
            date=day,
            amount=amount,
            direction=str(item.get("direction", "")).lower(),
            kind=str(item.get("kind", "")).lower(),
            merchant=" ".join(str(item.get("merchant", "")).split()),
            reference=str(item.get("reference", "")).strip(),
        ))
    return result


def transaction_refs(transactions: list[Transaction]) -> list[str]:
    """Стабильный id операции для защиты от повторной загрузки той же выписки.

    Две одинаковые операции за день (два кофе по 250) получают разные id по порядку.
    """
    seen: Counter[str] = Counter()
    refs = []
    for tx in transactions:
        base = f"{tx.date.isoformat()}|{tx.amount}|{tx.merchant.lower()}|{tx.reference}"
        seen[base] += 1
        digest = hashlib.sha1(f"{base}|{seen[base]}".encode()).hexdigest()[:12]
        refs.append(f"card:{digest}")
    return refs


def plan_import(transactions: list[Transaction], existing_refs: set[str]) -> ImportPlan:
    plan = ImportPlan()
    for tx, ref in zip(transactions, transaction_refs(transactions)):
        if ref in existing_refs:
            plan.duplicates += 1
        elif tx.kind == "refund":
            plan.refunds += 1
            plan.to_add.append((tx, ref, -tx.amount))
        elif tx.kind in SKIP_ATM:
            plan.atm += 1
        elif tx.kind in SKIP_TRANSFERS or tx.direction == "credit":
            plan.transfers += 1
        elif tx.kind in EXPENSE_KINDS:
            plan.to_add.append((tx, ref, tx.amount))
        else:
            plan.transfers += 1
    return plan


def debit_total(transactions: list[Transaction]) -> Decimal:
    return sum((tx.amount for tx in transactions if tx.direction == "debit"), Decimal(0))


def possible_duplicates(new: list[Expense], existing: list[Expense]) -> list[tuple[Expense, Expense]]:
    """Карточные траты, у которых в чате есть трата наличными с той же датой и суммой.

    Похоже, что одну и ту же покупку записали дважды: в чат и через выписку.
    """
    cash = {}
    for expense in existing:
        amount = rsd_amount(expense)
        if expense.source == CASH and amount is not None:
            cash.setdefault((expense.date, amount), expense)
    return [
        (cash[(card.date, rsd_amount(card))], card)
        for card in new
        if (card.date, rsd_amount(card)) in cash
    ]
