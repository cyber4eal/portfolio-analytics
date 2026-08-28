"""Price history for funds and holdings, cached on disk.

Yahoo is rate-limited and occasionally hands back an empty frame for a
ticker that worked ten minutes ago, so every download is cached as a
parquet-free CSV under `.cache/`. A rebuild of the site should not need the
network at all if the cache is warm and `max_age_hours` has not elapsed.

Everything returned is a *date-indexed* frame. Alignment happens later in
`portfolio_analytics.align_returns`, which is the only place allowed to
decide what a common date is.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
DEFAULT_PERIOD = "5y"
MAX_AGE_HOURS = 12


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.csv"


def _fresh(path: Path, max_age_hours: float) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < max_age_hours * 3600


def download_closes(
    tickers: list[str],
    period: str = DEFAULT_PERIOD,
    max_age_hours: float = MAX_AGE_HOURS,
    cache_key: str = "closes",
) -> pd.DataFrame:
    """Adjusted closes for `tickers`, one column each, missing lines dropped.

    A ticker Yahoo has no data for is dropped with a warning rather than
    left as an all-NaN column: an empty column would survive into the
    covariance and take the whole report down with it.
    """
    CACHE_DIR.mkdir(exist_ok=True)
    path = _cache_path(cache_key)

    if _fresh(path, max_age_hours):
        frame = pd.read_csv(path, index_col=0, parse_dates=True)
        missing = [t for t in tickers if t not in frame.columns]
        if not missing:
            return frame[tickers]

    import yfinance as yf

    raw = yf.download(
        tickers, period=period, interval="1d",
        auto_adjust=True, progress=False, threads=True,
    )
    closes = raw["Close"] if "Close" in raw else raw
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(tickers[0])

    usable, dropped = [], []
    for ticker in tickers:
        series = closes[ticker].dropna() if ticker in closes else pd.Series(dtype=float)
        (usable if len(series) >= 200 else dropped).append(ticker)
    if dropped:
        print(f"  no usable price history, dropped: {', '.join(dropped)}")

    frame = closes[usable].copy()
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    frame.to_csv(path)
    return frame


def to_returns(closes: pd.DataFrame) -> pd.DataFrame:
    """Daily simple returns. The first row is dropped, not zero-filled -
    a zero would be read as a real flat day by the volatility estimate."""
    return closes.sort_index().pct_change(fill_method=None).iloc[1:]


def trailing(closes: pd.Series, months: int) -> float | None:
    """Total return over the last `months`, as a fraction, or None if the
    series does not reach back that far.

    Returning None rather than a partial-period number matters: a fund that
    listed 8 months ago reporting a "1 year" figure computed from 8 months
    is the single most common way a comparison table lies.
    """
    series = closes.dropna()
    if series.empty:
        return None
    cutoff = series.index[-1] - pd.DateOffset(months=months)
    if series.index[0] > cutoff:
        return None
    start = series.asof(cutoff)
    if pd.isna(start) or start == 0:
        return None
    return float(series.iloc[-1] / start - 1)


def annualised(closes: pd.Series, months: int) -> float | None:
    total = trailing(closes, months)
    if total is None:
        return None
    years = months / 12
    return float((1 + total) ** (1 / years) - 1) if years >= 1 else total


def calendar_years(closes: pd.Series) -> list[tuple[str, float]]:
    """Discrete calendar-year returns, plus the current year to date.

    Partial first years are skipped. A fund that listed in October cannot
    honestly report a full-year 2021 number.
    """
    series = closes.dropna()
    if series.empty:
        return []
    out: list[tuple[str, float]] = []
    for year, group in series.groupby(series.index.year):
        first, last = group.index[0], group.index[-1]
        is_current = year == series.index[-1].year
        if not is_current and first.month > 1:
            continue
        prior = series[series.index < pd.Timestamp(year=year, month=1, day=1)]
        base = prior.iloc[-1] if len(prior) else group.iloc[0]
        if base == 0:
            continue
        label = f"{year} YTD" if is_current else str(year)
        out.append((label, float(group.iloc[-1] / base - 1)))
    return out
