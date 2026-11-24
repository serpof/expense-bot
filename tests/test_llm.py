import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from expense_bot.llm import MODEL, Claude, ClaudeError, StatementFile, parse_json_response

CATEGORIES = [("Продукты", "супермаркеты"), ("Кафе", ""), ("Прочие", "остальное")]


def response(data=None, stop_reason="end_turn", text=None):
    """Ответ API: сначала блок thinking, потом текст — как у моделей с адаптивным мышлением."""
    body = text if text is not None else json.dumps(data, ensure_ascii=False)
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=body)],
    )


def client_returning(message):
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=message)
    stream = MagicMock()
    stream.__aenter__ = AsyncMock(return_value=SimpleNamespace(get_final_message=AsyncMock(return_value=message)))
    stream.__aexit__ = AsyncMock(return_value=False)
    client.messages.stream = MagicMock(return_value=stream)
    return client


def test_classify_returns_results_in_input_order():
    client = client_returning(response({"results": [
        {"index": 1, "category": "Кафе", "keyword": "шаурма"},
        {"index": 0, "category": "Продукты", "keyword": "maxi"},
    ]}))
    result = asyncio.run(Claude(client).classify(["MAXI 0123", "шаурма"], CATEGORIES))
    assert [(r.category, r.keyword) for r in result] == [("Продукты", "maxi"), ("Кафе", "шаурма")]


def test_classify_request_uses_haiku_low_effort_and_enum_of_categories():
    client = client_returning(response({"results": []}))
    asyncio.run(Claude(client).classify(["шаурма"], CATEGORIES))
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == MODEL == "claude-haiku-5-5"
    assert kwargs["output_config"]["effort"] == "low"
    schema = kwargs["output_config"]["format"]["schema"]
    item = schema["properties"]["results"]["items"]
    assert item["properties"]["category"]["enum"] == ["Продукты", "Кафе", "Прочие"]
    assert "- Продукты: супермаркеты" in kwargs["system"]
    assert "0. шаурма" in kwargs["messages"][0]["content"]
    # параметры, которые Claude Haiku 5.5 отклоняет
    assert not {"temperature", "top_p", "top_k"} & kwargs.keys()


def test_classify_fixes_enum_case_and_missing_items():
    client = client_returning(response({"results": [{"index": 0, "category": "кафе", "keyword": ""}]}))
    result = asyncio.run(Claude(client).classify(["шаурма", "что-то"], CATEGORIES))
    assert [r.category for r in result] == ["Кафе", "Прочие"]


def test_classify_splits_large_batches():
    client = client_returning(response({"results": []}))
    asyncio.run(Claude(client).classify([f"трата {i}" for i in range(120)], CATEGORIES))
    assert client.messages.create.await_count == 3


@pytest.mark.parametrize("message", [
    response(stop_reason="refusal", text="нет"),
    response(stop_reason="max_tokens", text='{"results": ['),
    response(text="не json"),
    SimpleNamespace(stop_reason="end_turn", content=[]),
])
def test_bad_responses_raise_claude_error(message):
    with pytest.raises(ClaudeError):
        parse_json_response(message)


def test_extract_statement_sends_pdf_as_document():
    data = {"account_currency": "RSD", "total_debit": None, "transactions": []}
    client = client_returning(response(data))
    result = asyncio.run(Claude(client).extract_statement(StatementFile(pdf=b"%PDF-1.7")))
    assert result == data
    kwargs = client.messages.stream.call_args.kwargs
    document = kwargs["messages"][0]["content"][0]
    assert document["type"] == "document"
    assert document["source"]["media_type"] == "application/pdf"
    assert base64.b64decode(document["source"]["data"]) == b"%PDF-1.7"
    assert kwargs["output_config"]["format"]["type"] == "json_schema"


def test_extract_statement_sends_table_as_text():
    client = client_returning(response({"account_currency": "RSD", "total_debit": None, "transactions": []}))
    asyncio.run(Claude(client).extract_statement(StatementFile(text="Datum;Iznos")))
    first_block = client.messages.stream.call_args.kwargs["messages"][0]["content"][0]
    assert first_block == {"type": "text", "text": "<statement>\nDatum;Iznos\n</statement>"}
