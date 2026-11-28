import json
import os
from dataclasses import dataclass
from pathlib import Path

# Секреты лежат в папке secrets/ (она в .gitignore). На сервере Docker подключает
# её в контейнер. Любой секрет можно переопределить переменной окружения.
SECRETS_DIR = Path(__file__).resolve().parent.parent / "secrets"


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    google_credentials: dict
    spreadsheet_name: str
    anthropic_api_key: str | None
    allowed_chat_ids: frozenset[int]


def read_secret(env_name: str, file_name: str, secrets_dir: Path = SECRETS_DIR) -> str | None:
    """Сначала переменная окружения, потом файл в secrets/."""
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    path = secrets_dir / file_name
    if path.exists():
        return path.read_text(encoding="utf-8").strip() or None
    return None


def parse_chat_ids(raw: str | None) -> frozenset[int]:
    """«123, -100456» → {123, -100456}. Разделители — запятые или переносы строк."""
    if not raw:
        return frozenset()
    parts = raw.replace("\n", ",").split(",")
    return frozenset(int(part) for part in parts if part.strip())


def load_settings(secrets_dir: Path = SECRETS_DIR) -> Settings:
    token = read_secret("TELEGRAM_TOKEN", "bot_token.txt", secrets_dir)
    google = read_secret("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json", secrets_dir)
    if not token:
        raise SystemExit("Нет токена бота: положи его в secrets/bot_token.txt или в TELEGRAM_TOKEN")
    if not google:
        raise SystemExit(
            "Нет ключа Google: положи его в secrets/service_account.json "
            "или в GOOGLE_SERVICE_ACCOUNT_JSON"
        )
    return Settings(
        telegram_token=token,
        google_credentials=json.loads(google),
        spreadsheet_name=os.environ.get("SPREADSHEET_NAME", "").strip() or "Расходы",
        anthropic_api_key=read_secret("ANTHROPIC_API_KEY", "anthropic_key.txt", secrets_dir),
        allowed_chat_ids=parse_chat_ids(
            read_secret("ALLOWED_CHAT_IDS", "allowed_chats.txt", secrets_dir)
        ),
    )
