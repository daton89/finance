from datetime import date, timedelta, datetime
import pytz

NYSE_HOLIDAYS: set[date] = {
    date(2024, 1, 1),
    date(2024, 1, 15),
    date(2024, 2, 19),
    date(2024, 3, 29),
    date(2024, 5, 27),
    date(2024, 6, 19),
    date(2024, 7, 4),
    date(2024, 9, 2),
    date(2024, 11, 28),
    date(2024, 12, 25),
    date(2025, 1, 1),
    date(2025, 1, 20),
    date(2025, 2, 17),
    date(2025, 4, 18),
    date(2025, 5, 26),
    date(2025, 6, 19),
    date(2025, 7, 4),
    date(2025, 9, 1),
    date(2025, 11, 27),
    date(2025, 12, 25),
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25),
}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS


def last_trading_day(reference: date | None = None) -> date:
    d = reference or date.today()
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def trading_days_between(start: date, end: date) -> int:
    count = 0
    current = start
    while current <= end:
        if is_trading_day(current):
            count += 1
        current += timedelta(days=1)
    return count


_ET = pytz.timezone("US/Eastern")
_MARKET_OPEN_TIME  = (9, 30)
_MARKET_CLOSE_TIME = (16, 0)


def is_market_open(now: datetime | None = None) -> bool:
    if now is None:
        now = datetime.now(_ET)
    elif now.tzinfo is None:
        now = _ET.localize(now)
    else:
        now = now.astimezone(_ET)
    if now.weekday() >= 5:
        return False
    if not is_trading_day(now.date()):
        return False
    t = (now.hour, now.minute)
    return _MARKET_OPEN_TIME <= t < _MARKET_CLOSE_TIME


def next_market_event(now: datetime | None = None) -> dict:
    if now is None:
        now = datetime.now(_ET)
    elif now.tzinfo is None:
        now = _ET.localize(now)
    else:
        now = now.astimezone(_ET)
    open_flag = is_market_open(now)
    d = now.date()
    if not open_flag:
        if now.weekday() < 5 and is_trading_day(d) and (now.hour, now.minute) >= _MARKET_CLOSE_TIME:
            d += timedelta(days=1)
        while not is_trading_day(d):
            d += timedelta(days=1)
    session_open  = _ET.localize(datetime(d.year, d.month, d.day, *_MARKET_OPEN_TIME))
    session_close = _ET.localize(datetime(d.year, d.month, d.day, *_MARKET_CLOSE_TIME))
    return {
        "open": open_flag,
        "session_open_et":  session_open.isoformat(),
        "session_close_et": session_close.isoformat(),
    }
