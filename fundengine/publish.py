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

from . import actions, advice, brokers, combo, goals, irish_tax, ledger, levers, optimise, pension, portfolio as book, prices, profile, scenarios, universe

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

    # The pension is deliberately NOT a book in the switcher. It has its own
    # tab with its own projection, and listing it beside the tradable books
    # invited exactly the confusion it caused: the switcher's other entries
    # are books you can trade, and this is money you cannot touch for
    # decades. Its price history is still needed, so its proxy tickers are
    # collected for the download.
    pension_pots = pension.all_owners()
    pension_tickers_needed = sorted({
        h.get("ticker") for pot in pension_pots.values()
        for h in pot.get("holdings", []) if h.get("ticker")})

    names = book.books(all_holdings)
    views = names + ([book.COMBINED] if len(names) > 1 else [])
    print(f"  {len(all_holdings)} rows across {len(names)} book(s): {', '.join(names)}")

    # The default view is whichever book the agent is scoped to.
    primary = os.environ.get("PORTFOLIO", names[0] if names else book.COMBINED)
    if primary not in views:
        primary = views[0]

    holdings = book.for_book(all_holdings, primary)
    parked = book.parked_value(holdings)
    weights = book.weights(holdings)
    tradable_value = sum(h.value_eur for h in holdings if h.tradable)

    all_trades = ledger.read_all()

    print("Fetching prices...")
    # Every book's tickers, not just the active one: the per-book views and
    # the pension all need price history, and a ticker missing from the
    # matrix drops that whole view rather than just that row.
    holding_tickers = sorted({h.ticker for h in all_holdings if h.tradable}
                             | set(pension_tickers_needed))
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

    print("Projecting the pensions...")

    for owner, pot in pension_pots.items():
        _project_pension(pot, closes, benchmark)
    print(f"  {len(pension_pots)} pot(s): " + ", ".join(
        f"{o} EUR {p['total']:,.0f}" for o, p in pension_pots.items()))

    print("Tracking the mortgage goal...")
    try:
        goal = goals.track(goals.read_goal(agent_dir), all_holdings)
        if goal:
            print(f"  EUR {goal['held']:,.0f} of EUR {goal['target']:,.0f} "
                  f"({goal['pct']}%), {goal['monthsRemaining']} months left")
    except Exception as exc:                                  # noqa: BLE001
        print(f"  goal unavailable ({type(exc).__name__})")
        goal = {}

    pension_payload = pension.accrue(pension.summary())
    monthly_contribution = pension.recent_monthly(pension_payload)
    if False:
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

    # Directly-held shares carry no ongoing charge; funds do, and a forward
    # estimate that ignores it favours funds for no reason.
    fund_costs = universe.costs_by_ticker(
        [h.ticker for h in all_holdings if h.tradable])

    # Which tax regime each holding falls under. Everything in the fund
    # universe is a UCITS ETF and so carries deemed disposal; a directly
    # held share does not.
    wrappers = {f.ticker: irish_tax.FUND for f in universe.UNIVERSE}
    wrappers.update(universe.HELD_FUND_TERS.keys().__iter__().__class__ and
                    {t: irish_tax.FUND for t in universe.HELD_FUND_TERS})
    for holding in all_holdings:
        if holding.tradable and holding.ticker not in wrappers:
            kind = profiles.get(holding.ticker, {}).get("kind") if "profiles" in dir() else None
            wrappers[holding.ticker] = irish_tax.classify(holding.ticker, kind)
    # Tax modelling is off by default, at Catalin's instruction: returns are
    # treated as tax-free. Kept as a switch rather than deleted, because on a
    # long horizon the difference is not cosmetic - the same 7% gross over 35
    # years is EUR 74,833 in shares and EUR 49,720 in an ETF once deemed
    # disposal is applied. Set TAX_MODE="irish" to see that view.
    TAX_MODE = os.environ.get("TAX_MODE", "none")
    TAX_HORIZON = 10.0
    tax_wrappers = wrappers if TAX_MODE == "irish" else None

    # The rate used to show a US line at the price its broker quotes.
    try:
        import yfinance as _yf
        _fx = _yf.download("EURUSD=X", period="5d", auto_adjust=True,
                           progress=False)["Close"].dropna()
        eurusd = float(_fx.iloc[-1].item() if hasattr(_fx.iloc[-1], "item")
                       else _fx.iloc[-1])
    except Exception:                                          # noqa: BLE001
        eurusd = None

    # The statements are what the broker actually did; the sheet is typed.
    # Where they disagree, the statements win.
    ledger_positions = {}
    for owner in {h.portfolio for h in all_holdings}:
        ledger_positions.update({
            (owner, ticker): row
            for ticker, row in ledger.positions(all_trades, owner).items()})
    corrections = []
    corrected = []
    for holding in all_holdings:
        row = ledger_positions.get((holding.portfolio, holding.ticker))
        one, fixes = book.apply_ledger([holding], {holding.ticker: row} if row else {},
                                       closes)
        corrected.extend(one)
        corrections.extend(fixes)
    all_holdings = corrected
    if corrections:
        # Share counts are never touched - only the price is refreshed - so
        # the euro move here is the market's, not a reconciliation.
        print(f"  {len(corrections)} holding(s) the statements do not agree with "
              f"(share counts left as the sheet has them, price refreshed):")
        for fix in corrections:
            print(f"    {fix['ticker']:9} sheet {fix['sheetShares']:>9,.4f} vs "
                  f"statements {fix['ledgerShares']:>9,.4f}  "
                  f"EUR {fix['sheetValue']:>9,.2f} -> {fix['newValue']:>9,.2f}  {fix['reason']}")

    print("Reading trend...")
    trends = optimise.trend_signals(closes)

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
        # Every theory, and hedges ranked by what they do to compounding.
        try:
            view_series = series
            book_views[name]["optimisation"] = optimise.build(
                holding_returns.reindex(columns=view_tickers), view_weights,
                benchmark, candidates=fund_returns, cap=0.25, costs=fund_costs,
                wrappers=tax_wrappers, horizon=TAX_HORIZON)
            book_views[name]["hedges"] = optimise.hedges(
                holding_returns.reindex(columns=view_tickers), view_weights,
                fund_returns, benchmark, view_series, costs=fund_costs)
            book_views[name]["stressCorrelation"] = optimise.stress_correlation(
                pd.concat([holding_returns.reindex(columns=view_tickers),
                           fund_returns], axis=1), view_series)
            # The trades that get from here to the growth-optimal mix,
            # sequenced by trend and cut to what a month can actually do.
            latest = {t: float(closes[t].dropna().iloc[-1])
                      for t in closes.columns if closes[t].notna().any()}
            budgets = book_views[name]["advice"]["budgets"] if "advice" in book_views[name] else {}
            book_views[name]["plan"] = optimise.rebalance_plan(
                current={k: v for k, v in
                         book_views[name]["optimisation"]["theories"]["current"]["weights"].items()},
                target=book_views[name]["optimisation"]["theories"]["growth"]["weights"],
                total_value=view_value,
                trends=trends,
                prices=latest,
                monthly_cash=advice.MONTHLY_BUY_CASH_EUR,
                free_sells=advice.MONTHLY_FREE_SELLS,
            )
            basis = ledger.positions(
                all_trades, None if name in (book.COMBINED, pension.BOOK) else name)
            book_views[name]["plan"]["tax"] = optimise.tax_on_plan(
                book_views[name]["plan"]["sells"], view_rows, basis)
            month = book_views[name]["plan"]["thisMonth"]
            book_views[name]["plan"]["tradingCost"] = optimise.trading_cost(
                month["sells"] + month["buys"], view_rows)

            # Dated, priced orders rather than a target allocation.
            daily_vols = {
                t: float(holding_returns[t].dropna().tail(120).std())
                for t in holding_returns.columns
                if holding_returns[t].notna().sum() > 30}
            daily_vols.update({
                t: float(fund_returns[t].dropna().tail(120).std())
                for t in fund_returns.columns
                if fund_returns[t].notna().sum() > 30})
            book_views[name]["orders"] = actions.build(
                plan=book_views[name]["plan"],
                prices=latest,
                vols=daily_vols,
                weights=book_views[name]["optimisation"]["theories"]["current"]["weights"],
                growth_gap_pp=book_views[name]["optimisation"]["theories"]["growth"]["growthGain"],
                max_name_weight=advice.MAX_NAME_WEIGHT,
                free_sells=advice.MONTHLY_FREE_SELLS,
                goal_date=(goal or {}).get("targetDate"),
                currencies={h.ticker: h.currency for h in all_holdings}
                           | {f.ticker: f.currency for f in universe.UNIVERSE},
                fx=eurusd,
                positions={h.ticker: {"shares": h.shares, "value_eur": h.value_eur}
                           for h in rows if h.tradable},
                whole_shares=True,
                location=brokers.locate(
                    all_trades,
                    None if name in (book.COMBINED,) else name),
            )
        except ValueError as exc:
            print(f"  {name}: optimisation skipped ({exc})")
            book_views[name]["optimisation"] = None
            book_views[name]["hedges"] = []
            book_views[name]["stressCorrelation"] = []

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

    # How old is the oldest thing feeding this build. The page uses it to
    # say plainly whether the numbers are today's or last week's, because a
    # rebalance plan computed against stale prices looks exactly like one
    # computed against live ones.
    freshness = {
        "builtAt": dt.datetime.now().isoformat(timespec="seconds"),
        "pricesAsOf": matrix.index[-1].date().isoformat(),
        "sheetReadAt": dt.datetime.now().isoformat(timespec="seconds"),
        "ledgerTrades": len(all_trades),
        "lastTrade": max((t["date"] for t in all_trades), default=None),
        "pensionStatement": next(
            (p.get("lastStatement") for p in pension_pots.values()
             if p.get("lastStatement")), None),
    }

    return {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "freshness": freshness,
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
        "trends": trends,
        "fundCosts": fund_costs,
        "levers": levers.rank(
            tradable_value,
            advice.MONTHLY_BUY_CASH_EUR,
            0.08, 10.0,
            fee_saving=0.005,
            extra_monthly=200.0,
        ),
        "feasibility": {
            horizon: levers.feasibility(tradable_value,
                                        advice.MONTHLY_BUY_CASH_EUR,
                                        target, horizon)
            for horizon, target in ((10.0, 1_000_000_000.0),)
        },
        "deadlines": actions.calendar_deadlines(
            goal_date=(goal or {}).get("targetDate")),
        "milestones": [
            levers.feasibility(tradable_value, advice.MONTHLY_BUY_CASH_EUR,
                               target, 10.0)
            for target in (100_000.0, 250_000.0, 1_000_000.0)
        ],
        "wrappers": wrappers,
        "corrections": corrections,
        "brokers": {n: {"fractional": b.fractional, "note": b.note}
                    for n, b in brokers.BROKERS.items()},
        "taxMode": TAX_MODE,
        "taxHorizon": TAX_HORIZON,
        "taxComparison": irish_tax.compare(0.07, TAX_HORIZON, 10_000),
        "taxComparisonLong": irish_tax.compare(0.07, 35, 10_000),
        "pensionVsBrokerage": irish_tax.pension_vs_brokerage(600, 20, 0.07),
        "buyCandidates": advice.buy_candidates(
            additions, [f.as_dict() for f in universe.UNIVERSE], exposure, allocation),
        "ledger": ledger.summary(ledger.read_all()),
        "pension": pension_pots,
        "goal": goal,
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


def _project_pension(pot: dict, closes, benchmark) -> None:
    """Attach a projection to one pot, in place.

    Scheme funds are unlisted, so several map to the same listed proxy and
    values have to be summed per ticker before weighting - one column per
    holding put a duplicate in the price matrix and broke the weighting.
    """
    if pot.get("total", 0) <= 0:
        return
    values: dict = {}
    for holding in pot.get("holdings", []):
        ticker = holding.get("ticker")
        amount = float(holding.get("value_eur") or 0)
        if not ticker or ticker not in closes.columns or amount <= 0:
            continue
        values[ticker] = values.get(ticker, 0.0) + amount
    if not values:
        return

    tickers = sorted(values)
    weights = pd.Series({t: values[t] for t in tickers}, dtype=float)
    weights = weights / weights.sum()
    series = combo.portfolio_returns(prices.to_returns(closes[tickers]), weights)

    joined = pd.concat([series, benchmark], axis=1, join="inner").dropna()
    beta = float(joined.cov().iloc[0, 1] / benchmark.var()) if len(joined) > 20 else 1.0

    monthly = pension.recent_monthly(pot)
    charge = pot.get("charge", pension.DEFAULT_CHARGE)
    start = pot.get("estimatedTotal") or pot["total"]

    pot["beta"] = round(beta, 2)
    pot["monthlyContribution"] = monthly
    # Net of the scheme charge. Over thirty-five years this is not a detail:
    # a projection that assumes zero is not conservative, it is wrong.
    pot["projection"] = scenarios.monte_carlo_with_contributions(
        series, start, monthly_contribution=monthly, years=35,
        target_return=combo.expected_return(beta) - charge)
    # And what the charge itself costs, by running the same paths without it.
    gross = scenarios.monte_carlo_with_contributions(
        series, start, monthly_contribution=monthly, years=35,
        target_return=combo.expected_return(beta))
    pot["projectionGross"] = gross
    if gross and pot["projection"]:
        cost = gross["final"]["median"] - pot["projection"]["final"]["median"]
        pot["chargeCost"] = {
            "over": 35,
            "rate": round(charge, 4),
            "median": round(cost, 2),
            "pct": round(100 * cost / gross["final"]["median"], 1)
            if gross["final"]["median"] else 0.0,
        }
    pot["proxyNote"] = (
        "Scheme funds are unlisted, so returns are proxied by listed trackers "
        "with the same mandate: " + ", ".join(tickers) + ". The pot value is "
        "the statement's; only the shape of the simulation comes from the proxy.")
