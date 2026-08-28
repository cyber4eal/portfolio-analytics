"""Build the site payload: one JSON the page can recompute everything from.

The page has to stay useful when the holdings are edited in the browser,
which rules out shipping pre-computed answers. What ships instead is the
raw material - a daily return matrix for every holding and every fund, plus
metadata - and the page redoes the arithmetic itself whenever a weight
changes. That is why the numbers here are returns rather than the finished
risk blocks: a finished block would go stale the moment a weight moved.

Returns are rounded to six places. At daily frequency that is well inside
the noise of the underlying prices and it roughly halves the payload.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from . import combo, portfolio as book, prices, universe

SITE_DIR = Path(__file__).resolve().parent.parent / "site"


def _series_payload(returns: pd.DataFrame) -> dict:
    """Column-major, with one shared date axis - the same dates repeated per
    ticker would be most of the file."""
    return {
        "dates": [d.date().isoformat() for d in returns.index],
        "series": {c: [None if pd.isna(v) else round(float(v), 6)
                       for v in returns[c]] for c in returns.columns},
    }


def build(agent_dir: str, allocation: float = 0.10) -> dict:
    print("Reading the live book...")
    holdings = book.read_holdings(agent_dir)
    parked = book.read_untradable_value(agent_dir)
    weights = book.weights(holdings)
    tradable_value = sum(h.value_eur for h in holdings if h.tradable)
    print(f"  {len(holdings)} lines, EUR {tradable_value:,.0f} priced, "
          f"EUR {parked:,.0f} parked")

    print("Fetching prices...")
    holding_tickers = [h.ticker for h in holdings if h.tradable]
    fund_tickers = universe.tickers()
    closes = prices.download_closes(
        sorted(set(holding_tickers + fund_tickers + [universe.BENCHMARK_TICKER]))
    )
    closes = book.to_eur(closes, holdings)

    available_holdings = [t for t in holding_tickers if t in closes.columns]
    holding_returns = prices.to_returns(closes[available_holdings])
    fund_returns = prices.to_returns(closes[[t for t in fund_tickers if t in closes.columns]])
    benchmark = prices.to_returns(closes[[universe.BENCHMARK_TICKER]]).iloc[:, 0]

    weights = weights.reindex(available_holdings).dropna()
    weights = weights / weights.sum()

    print("Building the book's own series...")
    portfolio_series = combo.portfolio_returns(holding_returns, weights)

    lines = [
        combo.build_line(
            combo.PORTFOLIO_ID, "My portfolio", "portfolio",
            portfolio_series, benchmark, tradable_value,
            asset="Multi-Asset", benchmark="MSCI World",
        )
    ]
    for fund in universe.UNIVERSE:
        if fund.ticker not in fund_returns.columns:
            continue
        lines.append(
            combo.build_line(
                fund.id, fund.name, "fund",
                fund_returns[fund.ticker], benchmark, tradable_value,
                isin=fund.isin, issuer=fund.issuer, asset=fund.asset,
                currency=fund.currency, benchmark=fund.benchmark,
            )
        )
    print(f"  {len(lines)} lines ({len(lines) - 1} funds)")

    print("Ranking additions and projecting...")
    additions = combo.rank_additions(holding_returns, weights, fund_returns,
                                     benchmark, allocation=allocation)
    forecast = combo.projection(portfolio_series, tradable_value)

    matrix = pd.concat(
        [holding_returns, fund_returns, benchmark.rename("__benchmark__")],
        axis=1, join="outer",
    ).dropna(how="all")

    return {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "asOf": matrix.index[-1].date().isoformat(),
        "currency": "EUR",
        "benchmarkTicker": universe.BENCHMARK_TICKER,
        "totals": {
            "priced": round(tradable_value, 2),
            "parked": round(parked, 2),
            "book": round(tradable_value + parked, 2),
        },
        "holdings": [h.as_dict() for h in holdings],
        "funds": [f.as_dict() for f in universe.UNIVERSE],
        "lines": [line.as_dict() for line in lines],
        "additions": {"allocation": allocation, "ranked": additions},
        "projection": forecast,
        "returns": _series_payload(matrix),
        "caveats": [
            "The book's history applies today's weights to past returns. It is "
            "what this portfolio would have done, not what it did - positions "
            "since sold never drag on it, so its past flatters itself.",
            "Non-EUR holdings are converted to EUR before anything is measured, "
            "so the roughly two thirds unhedged USD shows up as portfolio risk "
            "rather than hiding inside it.",
            "Fund costs and SRI ratings are absent until factsheets or KIDs are "
            "parsed - performance shown is NAV total return, before the platform "
            "fees you actually pay.",
        ],
    }


def write(payload: dict, out_dir: Path = SITE_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "data.json"
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    size = path.stat().st_size / 1024
    print(f"  wrote {path} ({size:,.0f} KB)")
    return path
