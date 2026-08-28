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
import os
from pathlib import Path

import pandas as pd

from . import advice, combo, ledger, pension, portfolio as book, prices, profile, scenarios, universe

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
        all_holdings = read_snapshot(Path(from_csv))
    else:
        print("Reading the live book...")
        all_holdings = book.read_holdings(agent_dir)

    # The pension is its own book: never merged into Combined, because the
    # money is not accessible and folding it into tradable weights would
    # inflate every budget and cap that depends on them.
    pension_rows = pension.as_holdings()
    if pension_rows:
        all_holdings = all_holdings + pension_rows

    names = [n for n in book.books(all_holdings) if n != pension.BOOK]
    views = names + ([book.COMBINED] if len(names) > 1 else [])
    if pension_rows:
        views.append(pension.BOOK)
    print(f"  {len(all_holdings)} rows across {len(names)} book(s): {', '.join(names)}")

    # The default view is whichever book the agent is scoped to.
    primary = os.environ.get("PORTFOLIO", names[0] if names else book.COMBINED)
    if primary not in views:
        primary = views[0]

    holdings = book.for_book(all_holdings, primary)
    parked = book.parked_value(holdings)
    weights = book.weights(holdings)
    tradable_value = sum(h.value_eur for h in holdings if h.tradable)

    print("Fetching prices...")
    # Every book's tickers, not just the active one: the per-book views and
    # the pension all need price history, and a ticker missing from the
    # matrix drops that whole view rather than just that row.
    holding_tickers = sorted({h.ticker for h in all_holdings if h.tradable})
    fund_tickers = universe.tickers()
    closes = prices.download_closes(
        sorted(set(holding_tickers + fund_tickers + [universe.BENCHMARK_TICKER]))
    )
    closes = book.to_eur(closes, holdings)

    available_holdings = [t for t in holding_tickers if t in closes.columns]
    holding_returns = prices.to_returns(closes[available_holdings])
    fund_returns = prices.to_returns(closes[[t for t in fund_tickers if t in closes.columns]])
    benchmark = prices.to_returns(closes[[universe.BENCHMARK_TICKER]]).iloc[:, 0]

    weights = weights.reindex([t for t in weights.index if t in available_holdings]).dropna()
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
    book_beta = next((l.beta for l in lines if l.id == combo.PORTFOLIO_ID), 1.0)
    forecast = combo.projection(portfolio_series, tradable_value, beta=book_beta)

    print("Profiling holdings and exploding funds...")
    profiles = profile.fetch(sorted(set(available_holdings + fund_tickers)))
    holding_dicts = [h.as_dict() for h in holdings]
    exposure = profile.exposures(holding_dicts, profiles)
    income = profile.income(holding_dicts, profiles)

    print("Projecting the pension...")
    all_trades = ledger.read_all()
    pension_payload = pension.accrue(pension.summary())
    monthly_contribution = pension.recent_monthly(pension_payload)
    if pension_payload["total"] > 0:
        # Several scheme funds share one proxy - two of these three are
        # global equity trackers - so values are summed per ticker before
        # weighting. Keeping one column per holding put a duplicate column
        # in the price matrix and broke the weighting outright.
        pension_values: dict = {}
        for h in pension_payload["holdings"]:
            ticker = h.get("ticker")
            value = float(h.get("value_eur") or 0)
            if not ticker or ticker not in closes.columns or value <= 0:
                continue
            pension_values[ticker] = pension_values.get(ticker, 0.0) + value
        pension_tickers = sorted(pension_values)
        if pension_tickers:
            pw = pd.Series({t: pension_values[t] for t in pension_tickers}, dtype=float)
            pw = pw / pw.sum()
            pension_series = combo.portfolio_returns(
                prices.to_returns(closes[pension_tickers]), pw)
            pension_beta = float(
                pd.concat([pension_series, benchmark], axis=1, join="inner")
                .dropna().cov().iloc[0, 1] / benchmark.var())
            pension_payload["projection"] = scenarios.monte_carlo_with_contributions(
                pension_series, pension_payload.get("estimatedTotal")
                or pension_payload["total"],
                monthly_contribution=monthly_contribution,
                years=35, target_return=combo.expected_return(pension_beta))
            pension_payload["proxyNote"] = (
                "Scheme funds are unlisted, so returns are proxied by listed "
                "trackers with the same mandate: "
                + ", ".join(sorted(set(pension_tickers)))
                + ". The pot value is the statement's; only the shape of the "
                "simulation comes from the proxy.")
            pension_payload["beta"] = round(pension_beta, 2)
    pension_payload["monthlyContribution"] = monthly_contribution

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
        portfolio_series, tradable_value, monthly_contribution=500.0,
        target_return=combo.expected_return(book_beta))

    matrix = pd.concat(
        [holding_returns, fund_returns, benchmark.rename("__benchmark__")],
        axis=1, join="outer",
    ).dropna(how="all")
    # A ticker can be both a holding and a comparison fund - the pension holds
    # VWCE.DE and the universe compares against it. Two columns of the same
    # name make matrix[name] a DataFrame instead of a Series, and the payload
    # writer then serialises the column label where a number should be.
    matrix = matrix.loc[:, ~matrix.columns.duplicated()]

    print("Building each book's view...")
    book_views = {}
    for name in views:
        rows = book.for_book(all_holdings, name)
        priced = [h for h in rows if h.tradable and h.ticker in closes.columns]
        if not priced:
            continue
        view_value = sum(h.value_eur for h in priced)
        # Two holdings can share a ticker - the pension's scheme funds are
        # proxied by the same tracker, and a symbol can appear in both books.
        # Summing per ticker keeps the weight vector the same length as the
        # column list; building a dict from a list with repeats silently
        # dropped one and then failed the matrix multiply.
        by_ticker: dict = {}
        for h in priced:
            by_ticker[h.ticker] = by_ticker.get(h.ticker, 0.0) + h.value_eur
        view_tickers = sorted(by_ticker)
        view_weights = pd.Series({t: by_ticker[t] for t in view_tickers}, dtype=float)
        view_weights = view_weights / view_weights.sum()
        series = combo.portfolio_returns(
            holding_returns.reindex(columns=view_tickers), view_weights)
        book_views[name] = {
            "name": name,
            "holdings": [h.as_dict() for h in rows],
            "priced": round(view_value, 2),
            "parked": round(book.parked_value(rows), 2),
            "riskContributions": scenarios.risk_contributions(
                holding_returns.reindex(columns=view_tickers), view_weights),
            "income": profile.income([h.as_dict() for h in rows], profiles),
            "exposure": profile.exposures([h.as_dict() for h in rows], profiles),
            "stress": {
                "drawdown": scenarios.drawdown_series(series),
                "worstWindows": scenarios.worst_windows(series),
                "episodes": scenarios.episodes(series),
            },
        }
        view_correlations = scenarios.correlation_matrix(
            holding_returns.reindex(columns=view_tickers), view_weights)
        view_rows = [h.as_dict() for h in rows]
        hhi = float((view_weights ** 2).sum())
        book_views[name]["correlations"] = view_correlations
        book_views[name]["reconciliation"] = ledger.reconcile(
            all_trades, view_rows, None if name in (book.COMBINED, pension.BOOK) else name)
        book_views[name]["advice"] = {
            "sell": advice.sell_candidates(
                book_views[name]["riskContributions"], view_correlations,
                view_rows, view_value),
            "notes": advice.concentration_notes(
                book_views[name]["exposure"], 1 / hhi if hhi else 0, len(priced)),
            "budgets": {
                "monthlyBuyCashEur": advice.MONTHLY_BUY_CASH_EUR,
                "monthlyFreeSells": advice.MONTHLY_FREE_SELLS,
                "maxNameWeightPct": advice.MAX_NAME_WEIGHT * 100,
            },
        }
    print(f"  {len(book_views)} view(s): {', '.join(book_views)}")

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
        "books": list(book_views),
        "defaultBook": primary,
        "bookViews": book_views,
        "holdings": [h.as_dict() for h in holdings],
        "funds": [f.as_dict() for f in universe.UNIVERSE],
        "lines": [line.as_dict() for line in lines],
        "additions": {"allocation": allocation, "ranked": additions},
        "buyCandidates": advice.buy_candidates(
            additions, [f.as_dict() for f in universe.UNIVERSE], exposure, allocation),
        "ledger": ledger.summary(ledger.read_all()),
        "pension": pension_payload,
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
                        "currency", "tradable", "portfolio"],
        )
        writer.writeheader()
        for view in payload["bookViews"].values():
            if view["name"] == "Combined":
                continue          # a view, not a book - it would double-count
            for holding in view["holdings"]:
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
                portfolio=row.get("portfolio", ""),
            )
            for row in csv.DictReader(handle)
        ]
