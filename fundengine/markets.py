"""When you can actually place the order.

Every deadline on this site was counted in calendar days, which is wrong in
a way that matters on exactly the days people look at it. "Sell by the 31st,
2 days" read on a Saturday sounds like there is room. There is not: Saturday
and Sunday are not sessions, so the whole of that deadline is one Monday.
A countdown that includes days the exchange is shut is not a countdown, it
is a comfort.

So this module holds the only calendar facts the rest of the project needs:
which days are sessions, when a session opens, and how many sessions there
actually are between now and a date. Holidays are computed rather than
listed wherever they can be - the moveable ones follow rules, and a rule
does not go stale in January the way a hardcoded list does.

Session times are stored in UTC and shipped to the browser, because whether
the market is open is a question about right now and the answer must not be
as old as the last build.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

US, EU = "US", "EU"

#: Regular sessions, in UTC. The US moves an hour twice a year against
#: Europe on a different schedule, which is why these are stored as local
#: exchange time plus an offset rule rather than a fixed UTC pair.
SESSIONS = {
    US: {"name": "New York (NYSE and Nasdaq)", "openLocal": time(9, 30),
         "closeLocal": time(16, 0), "tz": "America/New_York",
         "utcOffsetSummer": -4, "utcOffsetWinter": -5},
    EU: {"name": "Xetra and Euronext", "openLocal": time(9, 0),
         "closeLocal": time(17, 30), "tz": "Europe/Amsterdam",
         "utcOffsetSummer": 2, "utcOffsetWinter": 1},
}

#: Suffixes that say which calendar a line trades on. Everything without a
#: suffix on this site is a US line, which is true of this book and stated
#: rather than assumed silently.
EUROPEAN_SUFFIXES = (".DE", ".AS", ".PA", ".MI", ".SW", ".L", ".IR")


def market_for(ticker: str, currency: str = "") -> str:
    if ticker.upper().endswith(EUROPEAN_SUFFIXES) or ticker.upper().startswith(
            ("AMS:", "ETR:", "EPA:", "BIT:", "LON:")):
        return EU
    if currency and currency.upper() != "USD":
        return EU
    return US


def easter(year: int) -> date:
    """Anonymous Gregorian algorithm. Good Friday and Easter Monday are the
    only moveable market holidays that matter here, and both hang off it."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth given weekday of a month; n = -1 for the last one."""
    if n > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (n - 1))
    last_day = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def _observed(day: date) -> date:
    """US markets move a Saturday holiday to the Friday before and a Sunday
    holiday to the Monday after."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def holidays(year: int, market: str) -> set[date]:
    good_friday = easter(year) - timedelta(days=2)
    if market == US:
        return {
            _observed(date(year, 1, 1)),
            _nth_weekday(year, 1, 0, 3),          # Martin Luther King Jr Day
            _nth_weekday(year, 2, 0, 3),          # Washington's Birthday
            good_friday,
            _nth_weekday(year, 5, 0, -1),         # Memorial Day
            _observed(date(year, 6, 19)),         # Juneteenth
            _observed(date(year, 7, 4)),
            _nth_weekday(year, 9, 0, 1),          # Labor Day
            _nth_weekday(year, 11, 3, 4),         # Thanksgiving
            _observed(date(year, 12, 25)),
        }
    # Xetra and Euronext keep a shorter list and do not observe a weekend
    # holiday on an adjacent weekday - it simply does not happen that year.
    return {
        date(year, 1, 1),
        good_friday,
        easter(year) + timedelta(days=1),         # Easter Monday
        date(year, 5, 1),
        date(year, 12, 25),
        date(year, 12, 26),
        date(year, 12, 31),                       # Xetra closes; Euronext half day
    }


def is_session(day: date, market: str = US) -> bool:
    return day.weekday() < 5 and day not in holidays(day.year, market)


def next_session(day: date, market: str = US) -> date:
    """The next day the exchange is open, today included if it is one."""
    probe = day
    for _ in range(30):
        if is_session(probe, market):
            return probe
        probe += timedelta(days=1)
    return probe


def previous_session(day: date, market: str = US) -> date:
    probe = day
    for _ in range(30):
        if is_session(probe, market):
            return probe
        probe -= timedelta(days=1)
    return probe


def sessions_between(start: date, end: date, market: str = US) -> int:
    """Sessions strictly after `start` up to and including `end`.

    Zero means the deadline is today or already gone, which is a different
    statement from "it is two days away" and is the one people need.
    """
    if end <= start:
        return 0
    count, probe = 0, start + timedelta(days=1)
    while probe <= end:
        if is_session(probe, market):
            count += 1
        probe += timedelta(days=1)
    return count


def add_sessions(start: date, count: int, market: str = US) -> date:
    day, added = start, 0
    while added < count:
        day += timedelta(days=1)
        if is_session(day, market):
            added += 1
    return day


def _utc_offset(day: date, market: str) -> int:
    """Northern-hemisphere DST, which both calendars follow on different
    dates: the EU switches the last Sunday of March and October, the US the
    second Sunday of March and first Sunday of November."""
    spec = SESSIONS[market]
    if market == US:
        start = _nth_weekday(day.year, 3, 6, 2)
        end = _nth_weekday(day.year, 11, 6, 1)
    else:
        start = _nth_weekday(day.year, 3, 6, -1)
        end = _nth_weekday(day.year, 10, 6, -1)
    summer = start <= day < end
    return spec["utcOffsetSummer"] if summer else spec["utcOffsetWinter"]


def session_window(day: date, market: str = US) -> tuple[datetime, datetime]:
    """Open and close for a given session day, in UTC."""
    spec = SESSIONS[market]
    offset = _utc_offset(day, market)
    open_utc = datetime.combine(day, spec["openLocal"]) - timedelta(hours=offset)
    close_utc = datetime.combine(day, spec["closeLocal"]) - timedelta(hours=offset)
    return open_utc.replace(tzinfo=timezone.utc), close_utc.replace(tzinfo=timezone.utc)


def is_open(when: datetime, market: str = US) -> bool:
    when = when.astimezone(timezone.utc)
    if not is_session(when.date(), market):
        return False
    start, end = session_window(when.date(), market)
    return start <= when < end


def next_open(when: datetime, market: str = US) -> datetime:
    """The next moment an order can actually execute."""
    when = when.astimezone(timezone.utc)
    day = when.date()
    for _ in range(30):
        if is_session(day, market):
            start, end = session_window(day, market)
            if when < start:
                return start
            if when < end:
                return when          # already open
        day += timedelta(days=1)
    return when


def why_closed(when: datetime, market: str = US) -> str:
    when = when.astimezone(timezone.utc)
    day = when.date()
    if day.weekday() >= 5:
        return "the weekend"
    if day in holidays(day.year, market):
        return "a market holiday"
    start, end = session_window(day, market)
    return "before the open" if when < start else "after the close"


def publishable(today: date | None = None) -> dict:
    """Everything the browser needs to answer "is it open right now" without
    asking this machine, which may have built the page days ago."""
    today = today or date.today()
    out = {}
    for market, spec in SESSIONS.items():
        holiday_days = sorted(
            d.isoformat()
            for year in (today.year, today.year + 1)
            for d in holidays(year, market))
        out[market] = {
            "name": spec["name"],
            "openLocal": spec["openLocal"].strftime("%H:%M"),
            "closeLocal": spec["closeLocal"].strftime("%H:%M"),
            "tz": spec["tz"],
            "utcOffsetSummer": spec["utcOffsetSummer"],
            "utcOffsetWinter": spec["utcOffsetWinter"],
            "holidays": holiday_days,
        }
    return out
