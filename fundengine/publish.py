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

from . import combo, portfolio as book, prices, profile, scenarios, universe

SITE_DIR = Path(__file__).resolve().parent.parent / "site"


def _series_payload(returns: pd.DataFrame) -> dict:
    """Column-major, with one shared date axis - the same dates repeated per
    ticker would be most of the file."""
    return {
        "dates": [d.date().isoformat() for d in returns.index],
        "series": {c: [None if pd.isna(v) else round(float(v), 6)
                       for v in returns[c]] for c in returns.columns},
    }


def build(agent_dir: str, allocation: float = 0.10,
          from_csv: str | None = None) -> dict:
    if from_csv:
        print(f"Reading the book from {from_csv}...")
        holdings = read_snapshot(Path(from_csv))
        parked = sum(h.value_eur for h in holdings if not h.tradable)
    else:
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

    print("Profiling holdings and exploding funds...")
    profiles = profile.fetch(sorted(set(available_holdings + fund_tickers)))
    holding_dicts = [h.as_dict() for h in holdings]
    exposure = profile.exposures(holding_dicts, profiles)
    income = profile.income(holding_dicts, profiles)

    print("Stressing, decomposing and projecting...")
    stress = {
        "drawdown": scenarios.drawdown_series(portfolio_series),
        "worstWindows": scenarios.worst_windows(portfolio_series),
        "episodes": scenarios.episodes(portfolio_series),
    }
    contributions = scenarios.risk_contributions(holding_returns, weights)
    correlations = scenarios.correlation_matrix(holding_returns, weights)
    frontiers = {}
    for fund in universe.UNIVERSE:
        if fund.ticker not in fund_returns.columns:
            continue
        curve = scenarios.frontier(portfolio_series, fund_returns[fund.ticker], benchmark)
        if curve:
            frontiers[fund.id] = curve
    contributing = scenarios.monte_carlo_with_contributions(
        portfolio_series, tradable_value, monthly_contribution=500.0)

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
        "exposure": exposure,
        "income": income,
        "stress": stress,
        "riskContributions": contributions,
        "correlations": correlations,
        "frontiers": frontiers,
        "projection": forecast,
        "projectionWithContributions": contributing,
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
            "Country and sector weights explode each ETF through its index's "
            "published breakdown. Those weights are hand-entered, approximate and "
            "not refetched, so treat the map as the shape of the exposure rather "
            "than a precise measurement of it.",
            "Stress episodes are only shown where the book's own history reaches "
            "them. Nothing is spliced in from an index to fill a gap.",
        ],
    }


def write(payload: dict, out_dir: Path = SITE_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "data.json"
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    size = path.stat().st_size / 1024
    print(f"  wrote {path} ({size:,.0f} KB)")
    return path


def write_snapshot(payload: dict, out_dir: Path | None = None) -> Path:
    """Write the book to CSV alongside the JSON.

    Two reasons. It is a plain-text record of what the sheet said on a given
    day, which the sheet itself does not keep; and it lets the build run
    with `--from-csv` on a machine that has no Google credentials, which is
    the difference between this being reproducible and being a thing that
    only works on one laptop.
    """
    import csv

    out_dir = out_dir or (SITE_DIR.parent / "data")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "holdings.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["symbol", "ticker", "name", "shares", "value_eur",
                        "currency", "tradable"],
        )
        writer.writeheader()
        for holding in payload["holdings"]:
            writer.writerow(holding)
    print(f"  wrote {path}")
    return path


def read_snapshot(path: Path) -> list:
    """Rebuild the holdings list from the CSV snapshot."""
    import csv

    from .portfolio import Holding

    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [
            Holding(
                symbol=row["symbol"], ticker=row["ticker"], name=row["name"],
                shares=float(row["shares"] or 0), value_eur=float(row["value_eur"]),
                currency=row["currency"], tradable=row["tradable"] in ("True", "true", "1"),
            )
            for row in csv.DictReader(handle)
        ]
