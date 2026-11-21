import base64
import json
import logging
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from expense_bot.categories import fallback_category, match_category

MODEL = "claude-haiku-5-5"
CLASSIFY_BATCH = 50

log = logging.getLogger(__name__)


class ClaudeError(Exception):
    """Claude не вернул пригодный ответ: отказ, обрыв по лимиту или пустой ответ."""


@dataclass(frozen=True)
class Classification:
    category: str
    keyword: str


@dataclass(frozen=True)
class StatementFile:
    """Выписка для Claude: PDF как есть или текст (CSV, Excel, переведённый в текст)."""
    pdf: bytes | None = None
    text: str | None = None


CLASSIFY_SYSTEM = """\
Ты раскладываешь семейные траты по категориям. Семья живёт в Сербии, траты пишут \
по-русски, а в банковских выписках встречаются сербские названия магазинов и компаний.

Категории:
{categories}

Для каждой траты выбери ровно одну категорию из списка. В поле keyword верни одно слово \
из текста траты, по которому видна категория, в нижнем регистре и так, как оно написано \
в тексте: например «шаурма» или «maxi». Если такого слова нет, верни пустую строку."""

EXTRACT_SYSTEM = """\
Ты переносишь операции из банковской выписки в таблицу учёта семейных расходов. \
Выписка из сербского банка, на сербском или английском."""

EXTRACT_INSTRUCTIONS = """\
Выпиши все операции из выписки, каждую ровно один раз. Начальный и конечный остаток, \
итоговые строки и заголовки — не операции.

Поля операции:
- date: дата операции в формате ГГГГ-ММ-ДД; если есть и дата операции, и дата \
проведения, бери дату операции.
- amount: сумма в валюте счёта, всегда положительное число.
- direction: debit — деньги ушли со счёта, credit — пришли на счёт.
- kind: purchase — оплата картой в магазине или онлайн; payment — оплата счёта или \
перевод другому человеку или компании; fee — комиссия или плата банку; atm — снятие \
наличных; own_transfer — перевод между своими счетами и картами, сбережения, обмен \
валюты; income — зарплата и другие поступления; refund — возврат или отмена покупки; \
other — всё остальное.
- merchant: название магазина или получателя так, как оно написано в выписке, без \
номеров карт.
- reference: номер или идентификатор операции, если он есть, иначе пустая строка.

Подсказки по сербским словам: isplata gotovine, bankomat, ATM — atm; uplata, priliv, \
zarada — income; provizija, naknada, članarina — fee; storno, povraćaj — refund; \
prenos sredstava между своими счетами — own_transfer.

account_currency — валюта счёта кодом ISO, например RSD. total_debit — итоговая сумма \
списаний, если выписка её указывает, иначе null."""

STATEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "account_currency": {"type": "string"},
        "total_debit": {"type": ["number", "null"]},
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "format": "date"},
                    "amount": {"type": "number"},
                    "direction": {"type": "string", "enum": ["debit", "credit"]},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "purchase", "payment", "fee", "atm",
                            "own_transfer", "income", "refund", "other",
                        ],
                    },
                    "merchant": {"type": "string"},
                    "reference": {"type": "string"},
                },
                "required": ["date", "amount", "direction", "kind", "merchant", "reference"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["account_currency", "total_debit", "transactions"],
    "additionalProperties": False,
}


def _classify_schema(names: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "category": {"type": "string", "enum": names},
                        "keyword": {"type": "string"},
                    },
                    "required": ["index", "category", "keyword"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    }


def parse_json_response(message) -> dict:
    """JSON из текстового блока ответа. Блоки ищем по типу: перед текстом бывает thinking."""
    if message.stop_reason == "refusal":
        raise ClaudeError("Claude отказался обрабатывать запрос")
    if message.stop_reason == "max_tokens":
        raise ClaudeError("ответ Claude не поместился в лимит токенов")
    text = next((block.text for block in message.content if block.type == "text"), None)
    if not text:
        raise ClaudeError("пустой ответ Claude")
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ClaudeError("Claude вернул невалидный JSON") from error


class Claude:
    def __init__(self, client: AsyncAnthropic):
        self._client = client

    @classmethod
    def from_api_key(cls, api_key: str) -> "Claude":
        return cls(AsyncAnthropic(api_key=api_key))

    async def classify(
        self, texts: list[str], categories: list[tuple[str, str]]
    ) -> list[Classification]:
        """Категория и ключевое слово для каждого текста, в том же порядке."""
        results: list[Classification] = []
        for start in range(0, len(texts), CLASSIFY_BATCH):
            results += await self._classify_batch(texts[start:start + CLASSIFY_BATCH], categories)
        return results

    async def _classify_batch(
        self, texts: list[str], categories: list[tuple[str, str]]
    ) -> list[Classification]:
        names = [name for name, _ in categories]
        listing = "\n".join(f"- {name}: {hint}" if hint else f"- {name}" for name, hint in categories)
        items = "\n".join(f"{i}. {text}" for i, text in enumerate(texts))
        message = await self._client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=CLASSIFY_SYSTEM.format(categories=listing),
            messages=[{"role": "user", "content": f"<expenses>\n{items}\n</expenses>"}],
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": _classify_schema(names)},
            },
        )
        by_index = {item["index"]: item for item in parse_json_response(message)["results"]}
        fallback = fallback_category(names)
        results = []
        for i in range(len(texts)):
            item = by_index.get(i, {})
            category = match_category(item.get("category", ""), names) or fallback
            results.append(Classification(category, item.get("keyword", "")))
        return results

    async def extract_statement(self, statement: StatementFile) -> dict:
        """Операции из выписки в виде словаря по схеме STATEMENT_SCHEMA."""
        if statement.pdf is not None:
            data = base64.standard_b64encode(statement.pdf).decode("ascii")
            document = {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": data},
            }
        else:
            document = {"type": "text", "text": f"<statement>\n{statement.text}\n</statement>"}
        # Длинный ответ: стриминг, чтобы не упираться в таймаут обычного запроса.
        async with self._client.messages.stream(
            model=MODEL,
            max_tokens=32000,
            system=EXTRACT_SYSTEM,
            messages=[{
                "role": "user",
                "content": [document, {"type": "text", "text": EXTRACT_INSTRUCTIONS}],
            }],
            output_config={"format": {"type": "json_schema", "schema": STATEMENT_SCHEMA}},
        ) as stream:
            message = await stream.get_final_message()
        return parse_json_response(message)
