from datetime import date, datetime, timezone

import pytest

from expense_bot.dates import local_date


@pytest.mark.parametrize("utc, expected", [
    # зима, UTC+1: 23:30 по UTC — уже следующий день, и даже следующий месяц
    (datetime(2026, 10, 31, 23, 30, tzinfo=timezone.utc), date(2026, 11, 1)),
    (datetime(2026, 12, 31, 22, 59, tzinfo=timezone.utc), date(2026, 12, 31)),
    (datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc), date(2027, 1, 1)),
    # лето, UTC+2: граница сдвигается на час раньше
    (datetime(2026, 6, 30, 21, 59, tzinfo=timezone.utc), date(2026, 6, 30)),
    (datetime(2026, 6, 30, 22, 0, tzinfo=timezone.utc), date(2026, 7, 1)),
    # день перехода на зимнее время
    (datetime(2026, 10, 25, 22, 30, tzinfo=timezone.utc), date(2026, 10, 25)),
    (datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc), date(2026, 10, 26)),
])
def test_local_date(utc, expected):
    assert local_date(utc) == expected
