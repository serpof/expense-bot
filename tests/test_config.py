import json

import pytest

from expense_bot.config import load_settings, parse_chat_ids

ENV = ["TELEGRAM_TOKEN", "GOOGLE_SERVICE_ACCOUNT_JSON", "ANTHROPIC_API_KEY", "ALLOWED_CHAT_IDS", "SPREADSHEET_NAME"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_reads_files_from_secrets_dir(tmp_path):
    (tmp_path / "bot_token.txt").write_text("123:abc\n")
    (tmp_path / "service_account.json").write_text(json.dumps({"type": "service_account"}))
    (tmp_path / "allowed_chats.txt").write_text("-100123\n456\n")
    settings = load_settings(tmp_path)
    assert settings.telegram_token == "123:abc"
    assert settings.google_credentials == {"type": "service_account"}
    assert settings.anthropic_api_key is None
    assert settings.allowed_chat_ids == {-100123, 456}
    assert settings.spreadsheet_name == "Расходы"


def test_environment_wins_over_files(tmp_path, monkeypatch):
    (tmp_path / "bot_token.txt").write_text("from-file")
    monkeypatch.setenv("TELEGRAM_TOKEN", "from-env")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert load_settings(tmp_path).telegram_token == "from-env"
    assert load_settings(tmp_path).anthropic_api_key == "sk-test"


def test_empty_github_secret_is_treated_as_missing(tmp_path, monkeypatch):
    # незаданный секрет в Actions приходит пустой строкой
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert load_settings(tmp_path).anthropic_api_key is None


def test_missing_token_stops_with_message(tmp_path):
    with pytest.raises(SystemExit, match="токена"):
        load_settings(tmp_path)


@pytest.mark.parametrize("raw, expected", [
    (None, set()),
    ("", set()),
    ("-100123", {-100123}),
    (" -100123 , 456 ", {-100123, 456}),
])
def test_parse_chat_ids(raw, expected):
    assert parse_chat_ids(raw) == expected
