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
  * The sheet lists two books, so the same ticker appears twice. They are
    summed into one line - the question being asked here is what the whole
    book looks like against a fund.
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


@dataclass(frozen=True)
class Holding:
    symbol: str          # as written in the sheet
    ticker: str          # Yahoo line
    name: str
    shares: float
    value_eur: float
    currency: str
    tradable: bool

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


def read_holdings(agent_dir: str | os.PathLike, tab: str = "Holdings") -> list[Holding]:
    """Pull the live sheet through the portfolio-agent service account.

    `agent_dir` is the portfolio-agent checkout - its .env carries the sheet
    id and its secrets/ carries the service account key. Nothing is copied
    into this repo, so there is one place where those credentials live.
    """
    agent_path = Path(agent_dir)
    _agent_env(agent_path)

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    key_file = Path(os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"])
    if not key_file.is_absolute():
        key_file = agent_path / key_file
    creds = service_account.Credentials.from_service_account_file(
        str(key_file), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    rows = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=os.environ["HOLDINGS_SHEET_ID"], range=f"'{tab}'!A2:F200")
        .execute()
        .get("values", [])
    )

    merged: dict[str, Holding] = {}
    for row in rows:
        row = row + [""] * (6 - len(row))
        symbol, name, shares, _price, _price_eur, value = (c.strip() for c in row[:6])
        if not symbol or symbol in NON_TRADABLE or symbol.lower().startswith("total"):
            continue

        ticker = TICKER_OVERRIDES.get(symbol, symbol)
        tradable = symbol not in UNPRICEABLE
        holding = Holding(
            symbol=symbol,
            ticker=ticker,
            name=name or symbol,
            shares=_number(shares),
            value_eur=_number(value),
            currency=currency_for(ticker),
            tradable=tradable,
        )
        if symbol in merged:
            previous = merged[symbol]
            holding = Holding(
                symbol, ticker, previous.name,
                previous.shares + holding.shares,
                previous.value_eur + holding.value_eur,
                holding.currency, tradable,
            )
        merged[symbol] = holding

    return [h for h in merged.values() if h.value_eur > 0]


def read_untradable_value(agent_dir: str | os.PathLike, tab: str = "Holdings") -> float:
    """Money market plus deposit balances - real money, no price series."""
    agent_path = Path(agent_dir)
    _agent_env(agent_path)

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    key_file = Path(os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"])
    if not key_file.is_absolute():
        key_file = agent_path / key_file
    creds = service_account.Credentials.from_service_account_file(
        str(key_file), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    rows = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=os.environ["HOLDINGS_SHEET_ID"], range=f"'{tab}'!A2:F200")
        .execute()
        .get("values", [])
    )
    total = 0.0
    for row in rows:
        row = row + [""] * (6 - len(row))
        symbol, value = row[0].strip(), row[5]
        if symbol.lower().startswith("total"):
            continue
        # Parked cash and an unlisted holding are both real money with no
        # price series. Leaving the unlisted one out of both buckets made the
        # headline total quietly disagree with the sheet.
        if symbol in NON_TRADABLE or symbol in UNPRICEABLE:
            total += _number(value)
    return total


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
