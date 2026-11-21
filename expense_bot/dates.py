from datetime import date, datetime
from zoneinfo import ZoneInfo

# Нови-Сад живёт по белградскому времени: UTC+1 зимой, UTC+2 летом.
TZ = ZoneInfo("Europe/Belgrade")


def local_date(dt: datetime) -> date:
    """Дата отправки сообщения по местному времени. Telegram присылает время в UTC."""
    return dt.astimezone(TZ).date()


def today() -> date:
    return datetime.now(TZ).date()
