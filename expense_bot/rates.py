import logging
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from decimal import Decimal

import aiohttp

# Средний курс Народного банка Сербии через бесплатный API без ключа.
RATE_URL = "https://kurs.resenje.org/api/v1/currencies/{code}/rates/{day}"
# Сколько дней назад искать курс, если на нужную дату его нет:
# выходные, праздники или утро, когда НБС ещё не опубликовал курс.
DAYS_BACK = 5

log = logging.getLogger(__name__)

FetchJson = Callable[[str], Awaitable[dict]]


async def fetch_json(url: str) -> dict:
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json(content_type=None)


class Rates:
    def __init__(self, fetch: FetchJson = fetch_json):
        self._fetch = fetch
        self._cache: dict[tuple[str, date], Decimal] = {}

    async def rate(self, currency: str, day: date) -> Decimal | None:
        """Сколько динаров стоит 1 единица валюты на дату. None — курс не нашёлся."""
        currency = currency.upper()
        if currency == "RSD":
            return Decimal(1)
        if (currency, day) in self._cache:
            return self._cache[(currency, day)]
        for back in range(DAYS_BACK):
            url = RATE_URL.format(code=currency.lower(), day=(day - timedelta(days=back)).isoformat())
            try:
                data = await self._fetch(url)
                middle = Decimal(str(data["exchange_middle"]))
                parity = Decimal(str(data.get("parity") or 1))
            except Exception as error:  # сеть, 404, неожиданный ответ — пробуем день раньше
                log.warning("Курс %s не получен: %s", currency, type(error).__name__)
                continue
            rate = middle / parity
            self._cache[(currency, day)] = rate
            return rate
        return None

    async def to_rsd(self, amount: Decimal, currency: str, day: date) -> Decimal | None:
        rate = await self.rate(currency, day)
        if rate is None:
            return None
        return (amount * rate).quantize(Decimal("0.01"))
