import asyncio
from datetime import date
from decimal import Decimal

from expense_bot.rates import Rates

DAY = date(2026, 11, 21)


class FakeApi:
    """API курсов: отвечает только на даты из словаря, остальные — ошибка."""

    def __init__(self, answers: dict[str, dict]):
        self.answers = answers
        self.urls: list[str] = []

    async def __call__(self, url: str) -> dict:
        self.urls.append(url)
        for key, data in self.answers.items():
            if url.endswith(key):
                return data
        raise RuntimeError("404")


def test_rsd_needs_no_request():
    api = FakeApi({})
    assert asyncio.run(Rates(api).to_rsd(Decimal("450"), "RSD", DAY)) == Decimal("450.00")
    assert api.urls == []


def test_converts_by_middle_rate():
    api = FakeApi({"eur/rates/2026-11-21": {"exchange_middle": 117.1717, "parity": 1}})
    assert asyncio.run(Rates(api).to_rsd(Decimal("20"), "EUR", DAY)) == Decimal("2343.43")
    assert api.urls == ["https://kurs.resenje.org/api/v1/currencies/eur/rates/2026-11-21"]


def test_parity_is_respected():
    # курс иены у НБС — за 100 единиц
    api = FakeApi({"jpy/rates/2026-11-21": {"exchange_middle": 75.5, "parity": 100}})
    assert asyncio.run(Rates(api).rate("JPY", DAY)) == Decimal("0.755")


def test_falls_back_to_previous_days():
    api = FakeApi({"gbp/rates/2026-11-19": {"exchange_middle": 135, "parity": 1}})
    assert asyncio.run(Rates(api).rate("GBP", DAY)) == Decimal("135")
    assert len(api.urls) == 3


def test_returns_none_when_rate_is_missing():
    api = FakeApi({})
    assert asyncio.run(Rates(api).to_rsd(Decimal("15"), "GBP", DAY)) is None
    assert len(api.urls) == 5


def test_rate_is_cached():
    api = FakeApi({"eur/rates/2026-11-21": {"exchange_middle": 117, "parity": 1}})
    rates = Rates(api)
    asyncio.run(rates.rate("EUR", DAY))
    asyncio.run(rates.rate("eur", DAY))
    assert len(api.urls) == 1
