"""The live book, read from the Google Sheet the agent already maintains.

Read-only, and deliberately so - this engine reports, it never writes back.

Three traps this module exists to handle:

  * The sheet's first column is headed "Sedol No" but actually holds broker
    symbols, and a few of them are not Yahoo lines. SPYL, RHM and VUAA need
    an exchange suffix or Yahoo returns nothing; getting that wrong makes
    the largest positions silently invisible rather than loudly broken.
  * Some rows are not instruments. A money market balance, a mortgage
    deposit and an unlisted holding have a value but no price series, so
    they count towards the book's total while being excluded from anything
    that needs returns.
  * The sheet lists two books in one tab, tagged in the 'Portfolio' column,
    and they share tickers - AMZN, AMD, NVDA, PLTR and TSLA are in both.
    Reading it unfiltered prices one person's positions into the other's
    weights, risk and goals. Every holding therefore carries its book, and
    nothing merges across books except the explicit "Combined" view.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from pathlib import Path

import pandas as pd

#: Broker symbol -> Yahoo line, only where they differ.
TICKER_OVERRIDES = {
    "SPYL": "SPYL.DE",   # SPDR S&P 500 UCITS, Xetra line, already EUR
    "RHM": "RHM.DE",     # Rheinmetall, Xetra
    "VUAA": "VUAA.DE",   # Vanguard S&P 500 UCITS Acc, Xetra
}

#: Rows carrying value but no tradable price series.
NON_TRADABLE = {"Money Market Fund", "Mortgage Deposit", "Cash", "Total"}

#: Listed but not priceable - unlisted equity, no Yahoo line.
UNPRICEABLE = {"SPCX"}

_SUFFIX_CURRENCY = {".DE": "EUR", ".AS": "EUR", ".PA": "EUR", ".MI": "EUR",
                    ".L": "GBP", ".HK": "HKD", ".TO": "CAD", ".SW": "CHF"}


#: The books in the sheet. "Combined" is a view, never a label - a trade
#: written against it could land on either book's row.
COMBINED = "Combined"

#: Kept out of Combined - see for_book().
PENSION_BOOK = "Pension"


@dataclass(frozen=True)
class Holding:
    symbol: str          # as written in the sheet
    ticker: str          # Yahoo line
    name: str
    shares: float
    value_eur: float
    currency: str
    tradable: bool
    portfolio: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _number(text: str) -> float:
    """Sheet numbers arrive as '1,760.71' and share counts as '8.'."""
    cleaned = str(text).replace(",", "").replace("€", "").strip().rstrip(".")
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def currency_for(ticker: str) -> str:
    for suffix, ccy in _SUFFIX_CURRENCY.items():
        if ticker.endswith(suffix):
            return ccy
    return "USD"


def _agent_env(agent_dir: Path) -> None:
    from dotenv import load_dotenv

    load_dotenv(agent_dir / ".env")


def _fetch_rows(agent_dir: Path, tab: str) -> tuple[list[str], list[list[str]]]:
    """Header plus data rows, straight from the sheet."""
    _agent_env(agent_dir)

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    key_file = Path(os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"])
    if not key_file.is_absolute():
        key_file = agent_dir / key_file
    creds = service_account.Credentials.from_service_account_file(
        str(key_file), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    values = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=os.environ["HOLDINGS_SHEET_ID"], range=f"'{tab}'!A1:U300")
        .execute()
        .get("values", [])
    )
    if not values:
        return [], []
    return values[0], values[1:]


def read_holdings(agent_dir: str | os.PathLike, tab: str = "Holdings") -> list[Holding]:
    """Pull the live sheet through the portfolio-agent service account.

    `agent_dir` is the portfolio-agent checkout - its .env carries the sheet
    id and its secrets/ carries the service account key. Nothing is copied
    into this repo, so there is one place where those credentials live.
    """
    header, rows = _fetch_rows(Path(agent_dir), tab)
    index = {name: i for i, name in enumerate(header)}
    book_col = index.get("Portfolio")
    value_col = index.get("Market Value (EUR)", 5)

    out: list[Holding] = []
    for row in rows:
        row = row + [""] * (len(header) - len(row))
        symbol = row[0].strip()
        if not symbol or symbol.lower().startswith("total"):
            continue

        book = row[book_col].strip() if book_col is not None else ""
        ticker = TICKER_OVERRIDES.get(symbol, symbol)
        out.append(Holding(
            symbol=symbol,
            ticker=ticker,
            name=row[1].strip() or symbol,
            shares=_number(row[2]),
            value_eur=_number(row[value_col]),
            currency=currency_for(ticker),
            tradable=symbol not in NON_TRADABLE and symbol not in UNPRICEABLE,
            portfolio=book,
        ))

    return [h for h in out if h.value_eur > 0]


def books(holdings: list[Holding]) -> list[str]:
    """Book names in sheet order, so the switcher matches the spreadsheet."""
    seen: list[str] = []
    for holding in holdings:
        if holding.portfolio and holding.portfolio not in seen:
            seen.append(holding.portfolio)
    return seen


def for_book(holdings: list[Holding], book: str) -> list[Holding]:
    """One book's rows, or every tradable book's rows for Combined.

    Combined sums the two people's shared tickers, because AMZN held in both
    is one exposure to Amazon even though it is two rows in the sheet. It
    deliberately excludes the pension: that money is not accessible, and
    folding it in would inflate every weight, cap and budget computed off
    the tradable total.
    """
    if book != COMBINED:
        return [h for h in holdings if h.portfolio == book]

    merged: dict[str, Holding] = {}
    for holding in holdings:
        if holding.portfolio == PENSION_BOOK:
            continue
        existing = merged.get(holding.symbol)
        merged[holding.symbol] = Holding(
            holding.symbol, holding.ticker, holding.name,
            holding.shares + (existing.shares if existing else 0.0),
            holding.value_eur + (existing.value_eur if existing else 0.0),
            holding.currency, holding.tradable, COMBINED,
        )
    return list(merged.values())


def parked_value(holdings: list[Holding]) -> float:
    """Cash, deposits and unlisted lines - real money with no price series.

    Counted towards the headline total and excluded from everything that
    needs returns. Leaving the unlisted holding out of both buckets made the
    total quietly disagree with the sheet.
    """
    return sum(h.value_eur for h in holdings if not h.tradable)


def weights(holdings: list[Holding]) -> pd.Series:
    """Tradable weights, normalised. Untradable value is excluded here and
    added back only to the headline total, because a mortgage deposit has
    no volatility to contribute."""
    priced = [h for h in holdings if h.tradable]
    series = pd.Series({h.ticker: h.value_eur for h in priced}, dtype=float)
    return series / series.sum()


def to_eur(closes: pd.DataFrame, holdings: list[Holding]) -> pd.DataFrame:
    """Convert non-EUR price series into EUR before anything measures them.

    The comparison funds are EUR-denominated. Leaving a USD holding in USD
    measures the dollar's moves as if they were the stock's, and this book
    is roughly two thirds unhedged USD - that is not a rounding error, it
    is one of the larger risks in the portfolio and it belongs in the
    numbers rather than hidden by them.
    """
    currencies = {h.ticker: h.currency for h in holdings}
    needed = {c for t, c in currencies.items() if c != "EUR" and t in closes.columns}
    if not needed:
        return closes

    import yfinance as yf

    pairs = {c: f"{c}EUR=X" for c in needed}
    fx = yf.download(list(pairs.values()), period="5y", interval="1d",
                     auto_adjust=True, progress=False)["Close"]
    if isinstance(fx, pd.Series):
        fx = fx.to_frame(list(pairs.values())[0])
    fx.index = pd.to_datetime(fx.index).tz_localize(None)

    converted = closes.copy()
    for ticker, ccy in currencies.items():
        if ccy == "EUR" or ticker not in converted.columns:
            continue
        rate = fx[pairs[ccy]].reindex(converted.index).ffill()
        converted[ticker] = converted[ticker] * rate
    return converted
