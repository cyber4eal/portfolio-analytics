"""Portfolio construction under several theories, and honest hedging.

"Make the most money possible" has a precise answer, and it is not "hold the
highest-returning thing". Wealth after many periods compounds, so what gets
maximised is expected *log* return, not expected return:

    growth  ~  mu - sigma^2 / 2

That second term is why this matters. On a book at 31% volatility the drag is
4.9 points a year. A mix with a lower expected return but materially lower
variance can end up with more money, and past a point taking more risk
reduces terminal wealth outright. That is the growth-optimal (Kelly) result,
and it is the one lens here that answers the question as asked.

The other lenses disagree on purpose, because they optimise different things:
max-Sharpe buys the best risk-adjusted trade-off, minimum-variance ignores
return entirely, and risk parity ignores both and equalises risk instead.
Showing them together is the point - where they agree you can be confident,
and where they diverge is where the assumptions are doing the work.

Two deliberate choices about inputs, because mean-variance optimisers are
notorious for turning small input errors into absurd allocations:

  * Expected returns are CAPM, not historical means. Feeding in what each
    asset happened to return puts the whole portfolio into whatever ran
    hottest - here that would be almost all APLD and PLTR. CAPM says what
    return the risk taken entitles you to, which is stable and defensible.
  * Covariance is shrunk toward a constant-correlation target. A sample
    covariance over twenty-odd assets and a few hundred days is mostly
    noise in its extreme eigenvalues, and those extremes are exactly what
    an optimiser leans on.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from portfolio_analytics import align_returns, ewma_cov

TRADING_DAYS = 252
RISK_FREE = 0.025
EQUITY_RISK_PREMIUM = 0.045


def shrink_covariance(sample: np.ndarray, intensity: float = 0.3) -> np.ndarray:
    """Pull the sample covariance toward a constant-correlation matrix.

    Keeps each asset's own variance (which is estimated reasonably well) and
    shrinks only the correlations (which are not) toward their average. The
    result stays positive-definite and stops the optimiser from betting the
    portfolio on a correlation that happens to be -0.4 in this sample.
    """
    deviation = np.sqrt(np.diag(sample))
    outer = np.outer(deviation, deviation)
    with np.errstate(divide="ignore", invalid="ignore"):
        correlation = np.where(outer > 0, sample / outer, 0.0)
    n = correlation.shape[0]
    if n < 2:
        return sample
    off_diagonal = correlation[~np.eye(n, dtype=bool)]
    average = float(np.mean(off_diagonal))
    target_correlation = np.full((n, n), average)
    np.fill_diagonal(target_correlation, 1.0)
    target = target_correlation * outer
    return (1 - intensity) * sample + intensity * target


def capm_returns(returns: pd.DataFrame, benchmark: pd.Series,
                 costs: dict[str, float] | None = None) -> pd.Series:
    """Expected return per asset from its beta to the benchmark, net of fees.

    The netting matters and is easy to get wrong in both directions.

    A fund's *historical* line is already net: the ongoing charge comes out
    of NAV daily, so every past number on this site is what a holder
    actually received, and subtracting the fee again would double-count it.

    A *forward* CAPM estimate is gross. It knows the asset's beta and
    nothing else, so left alone it credits a 0.65% thematic fund with the
    same net return as a 0.07% index fund at the same beta. That is not a
    rounding error in an optimiser deciding what to hold for a decade: it
    systematically favours funds over directly-held shares, which carry no
    ongoing charge at all. So the charge is subtracted here, once.
    """
    aligned = pd.concat([returns, benchmark.rename("__b__")], axis=1,
                        join="inner").dropna()
    market = aligned["__b__"].to_numpy()
    variance = market.var(ddof=1)
    out = {}
    for column in returns.columns:
        series = aligned[column].to_numpy()
        beta = float(np.cov(series, market, ddof=1)[0, 1] / variance) if variance else 1.0
        gross = RISK_FREE + max(-0.5, beta) * EQUITY_RISK_PREMIUM
        out[column] = gross - (costs or {}).get(column, 0.0)
    return pd.Series(out)


def _project_to_simplex(v: np.ndarray, cap: float | None = None) -> np.ndarray:
    """Nearest point with non-negative weights summing to one.

    Long-only because the book is long-only - a short leg suggested here
    would be advice you cannot act on in the accounts you actually have.
    """
    n = v.size
    u = np.sort(v)[::-1]
    cumulative = np.cumsum(u)
    rho = np.nonzero(u * np.arange(1, n + 1) > (cumulative - 1))[0][-1]
    theta = (cumulative[rho] - 1) / (rho + 1.0)
    w = np.maximum(v - theta, 0.0)
    if cap is not None:
        if cap * n < 1 - 1e-12:
            # The cap cannot be met at all - spread evenly and say nothing
            # further, rather than silently returning an under-invested book.
            return np.full(n, 1.0 / n)
        # Repeatedly clip and redistribute the excess. Redistribution goes to
        # every uncapped slot, not only those already carrying weight: when
        # the projection puts everything on one name, the rest are exactly
        # zero, and handing the excess only to non-zero slots left nothing to
        # receive it. That returned a portfolio summing to the cap - a book
        # 70% in cash that no theory had asked for.
        for _ in range(200):
            over = w > cap + 1e-12
            if not over.any():
                break
            excess = float((w[over] - cap).sum())
            w[over] = cap
            room = ~over
            if not room.any():
                break
            share = w[room]
            if share.sum() > 1e-12:
                w[room] = share + excess * share / share.sum()
            else:
                w[room] = excess / room.sum()
        total = w.sum()
        if total > 0:
            w /= total
        # One last clip: normalising can nudge a capped weight back over.
        if (w > cap + 1e-9).any():
            w = np.minimum(w, cap)
            w /= w.sum()
    return w


def _ascend(gradient, n: int, cap: float | None, steps: int = 4000,
            rate: float = 0.05, seed: int = 3) -> np.ndarray:
    """Projected gradient ascent from several starts.

    Several starts because the Sharpe objective is not concave, so one run
    can settle in a local optimum that happens to look plausible.
    """
    rng = np.random.default_rng(seed)
    best, best_value = None, -np.inf
    starts = [np.full(n, 1.0 / n)] + [rng.random(n) for _ in range(4)]
    for start in starts:
        w = _project_to_simplex(start / start.sum(), cap)
        step = rate
        for i in range(steps):
            g = gradient(w)
            w_new = _project_to_simplex(w + step * g, cap)
            if np.abs(w_new - w).max() < 1e-9:
                w = w_new
                break
            w = w_new
            if i % 500 == 499:
                step *= 0.7
        value = gradient(w, value_only=True)
        if value > best_value:
            best, best_value = w.copy(), value
    return best


def growth_optimal(mu: np.ndarray, sigma: np.ndarray, cap: float | None = None) -> np.ndarray:
    """Maximise mu'w - (w'Sigma w)/2 - expected log growth.

    This is the one that answers "the most money possible" as literally as
    the maths allows: it maximises the rate wealth compounds at, which is
    what you actually end up with.
    """
    def gradient(w, value_only=False):
        if value_only:
            return float(mu @ w - 0.5 * w @ sigma @ w)
        return mu - sigma @ w
    return _ascend(gradient, mu.size, cap)


def max_sharpe(mu: np.ndarray, sigma: np.ndarray, cap: float | None = None) -> np.ndarray:
    """Maximise (mu'w - rf) / sqrt(w'Sigma w)."""
    def gradient(w, value_only=False):
        excess = float(mu @ w - RISK_FREE)
        variance = float(w @ sigma @ w)
        vol = np.sqrt(variance) if variance > 0 else 1e-9
        if value_only:
            return excess / vol
        return (mu * vol - excess * (sigma @ w) / vol) / (vol ** 2)
    return _ascend(gradient, mu.size, cap)


def min_variance(sigma: np.ndarray, cap: float | None = None) -> np.ndarray:
    def gradient(w, value_only=False):
        if value_only:
            return -float(w @ sigma @ w)
        return -2 * (sigma @ w)
    return _ascend(gradient, sigma.shape[0], cap)


def risk_parity(sigma: np.ndarray, cap: float | None = None,
                iterations: int = 3000) -> np.ndarray:
    """Equalise each holding's contribution to portfolio risk.

    Ignores expected return entirely, which is either its weakness or the
    reason to trust it, depending on how much you believe the return
    estimates.

    The cap applies here as it does everywhere else. Left uncapped this
    routine put 63% into the single lowest-volatility line, under a caveat
    on screen promising nothing exceeded 25% - the table would have been
    contradicting its own footnote.
    """
    n = sigma.shape[0]
    w = np.full(n, 1.0 / n)
    for _ in range(iterations):
        marginal = sigma @ w
        contribution = w * marginal
        target = contribution.mean()
        w = np.maximum(w * (target / np.maximum(contribution, 1e-12)) ** 0.1, 1e-9)
        w /= w.sum()
        if cap is not None and (w > cap).any():
            w = _project_to_simplex(w, cap)
    return _project_to_simplex(w, cap) if cap is not None else w


def describe(w: np.ndarray, mu: np.ndarray, sigma: np.ndarray) -> dict:
    """The three numbers that let the theories be compared on one row."""
    expected = float(mu @ w)
    variance = float(w @ sigma @ w)
    vol = float(np.sqrt(variance))
    return {
        "expectedReturn": round(expected * 100, 2),
        "vol": round(vol * 100, 2),
        # mu - sigma^2/2: what the money actually compounds at.
        "growth": round((expected - variance / 2) * 100, 2),
        "drag": round((variance / 2) * 100, 2),
        "sharpe": round((expected - RISK_FREE) / vol, 2) if vol else 0.0,
    }


def build(returns: pd.DataFrame, weights: pd.Series, benchmark: pd.Series,
          candidates: pd.DataFrame | None = None, cap: float = 0.25,
          costs: dict[str, float] | None = None) -> dict:
    """Run every theory over the holdings, optionally plus candidate funds.

    `costs` is the annual ongoing charge per ticker, subtracted from each
    forward expected return so the comparison is on what reaches your pocket
    rather than on what the fund earns before paying itself.
    """
    columns = [c for c in returns.columns if c in weights.index]
    frame = returns[columns]
    if candidates is not None and not candidates.empty:
        extra = [c for c in candidates.columns if c not in columns]
        frame = pd.concat([frame, candidates[extra]], axis=1)
    aligned = align_returns(frame.dropna(axis=1, how="all"))

    names = list(aligned.columns)
    sigma = shrink_covariance(ewma_cov(aligned).to_numpy())
    mu = capm_returns(aligned, benchmark, costs).reindex(names).to_numpy()

    current = np.array([weights.get(name, 0.0) for name in names], dtype=float)
    if current.sum() <= 0:
        raise ValueError("no current weights overlap the return matrix")
    current = current / current.sum()

    theories = {
        "current": current,
        "growth": growth_optimal(mu, sigma, cap),
        "sharpe": max_sharpe(mu, sigma, cap),
        "minvar": min_variance(sigma, cap),
        "parity": risk_parity(sigma, cap),
    }

    out = {"tickers": names, "cap": cap, "costs": costs or {}, "theories": {}}
    for key, w in theories.items():
        stats = describe(w, mu, sigma)
        weights_out = {names[i]: round(float(w[i]) * 100, 2)
                       for i in range(len(names)) if w[i] > 0.005}
        # The blended ongoing charge of the mix, so "what it costs to hold"
        # sits beside "what it is expected to earn".
        fee = sum(float(w[i]) * (costs or {}).get(names[i], 0.0)
                  for i in range(len(names)))
        out["theories"][key] = {
            **stats,
            "fee": round(fee * 100, 3),
            "weights": weights_out,
        }
    baseline = out["theories"]["current"]["growth"]
    for key, block in out["theories"].items():
        block["growthGain"] = round(block["growth"] - baseline, 2)
    return out


def stress_correlation(returns: pd.DataFrame, portfolio: pd.Series,
                       quantile: float = 0.1) -> list[dict]:
    """Correlation to the book overall, versus on the book's worst days.

    This is the number that decides whether a hedge is real. Correlations
    measured across calm and crisis together understate what happens when it
    matters: assets that look diversifying on average routinely converge in
    a selloff, which is precisely when the diversification was the reason
    for owning them. An asset whose stress correlation is far above its
    average one is not a hedge, whatever the headline figure says.
    """
    aligned = pd.concat([returns, portfolio.rename("__p__")], axis=1,
                        join="inner").dropna()
    if len(aligned) < 120:
        return []
    book = aligned["__p__"]
    threshold = book.quantile(quantile)
    stressed = aligned[book <= threshold]

    out = []
    for column in returns.columns:
        if column not in aligned:
            continue
        overall = float(aligned[column].corr(book))
        crisis = float(stressed[column].corr(stressed["__p__"]))
        if not np.isfinite(overall) or not np.isfinite(crisis):
            continue
        out.append({
            "ticker": column,
            "correlation": round(overall, 3),
            "stressCorrelation": round(crisis, 3),
            "deterioration": round(crisis - overall, 3),
            "meanOnBadDays": round(float(stressed[column].mean()) * 100, 3),
        })
    out.sort(key=lambda r: r["stressCorrelation"])
    return out


def hedges(returns: pd.DataFrame, weights: pd.Series, candidates: pd.DataFrame,
           benchmark: pd.Series, portfolio: pd.Series,
           costs: dict[str, float] | None = None) -> list[dict]:
    """Rank candidates by what they do to compounding, not just to variance.

    A hedge that removes volatility but costs more return than the variance
    it saves makes you poorer, slowly. The ranking is therefore on the change
    in mu - sigma^2/2, with the optimal hedge size solved for rather than
    assumed, and with the stress correlation attached so a diversifier that
    only works in calm markets is visible as such.
    """
    columns = [c for c in returns.columns if c in weights.index]
    aligned = align_returns(returns[columns])
    w = weights.reindex(columns).fillna(0.0).to_numpy(dtype=float)
    w = w / w.sum()

    stress = {row["ticker"]: row
              for row in stress_correlation(candidates, portfolio)}

    out = []
    for ticker in candidates.columns:
        joint = pd.concat([aligned, candidates[[ticker]]], axis=1).dropna()
        if len(joint) < 120:
            continue
        sigma = shrink_covariance(ewma_cov(joint).to_numpy())
        mu = capm_returns(joint, benchmark, costs).reindex(joint.columns).to_numpy()

        best = None
        for step in range(0, 41):
            share = step / 100.0                       # 0% to 40%
            mixed = np.append(w * (1 - share), share)
            expected = float(mu @ mixed)
            variance = float(mixed @ sigma @ mixed)
            growth = expected - variance / 2
            if best is None or growth > best["growth"]:
                best = {"share": share, "growth": growth,
                        "expected": expected, "vol": float(np.sqrt(variance))}

        base_mixed = np.append(w, 0.0)
        base_expected = float(mu @ base_mixed)
        base_variance = float(base_mixed @ sigma @ base_mixed)
        base_growth = base_expected - base_variance / 2

        row = {
            "ticker": ticker,
            "optimalWeightPct": round(best["share"] * 100, 1),
            "growthGain": round((best["growth"] - base_growth) * 100, 3),
            "volChange": round((best["vol"] - np.sqrt(base_variance)) * 100, 2),
            "returnChange": round((best["expected"] - base_expected) * 100, 2),
        }
        if ticker in stress:
            row.update({
                "correlation": stress[ticker]["correlation"],
                "stressCorrelation": stress[ticker]["stressCorrelation"],
                "deterioration": stress[ticker]["deterioration"],
                "meanOnBadDays": stress[ticker]["meanOnBadDays"],
            })
        out.append(row)

    out.sort(key=lambda r: -r["growthGain"])
    return out


def trend_signals(closes: pd.DataFrame) -> dict[str, dict]:
    """Momentum and trend, per ticker.

    Three measures, all standard and all noisy:

      * 12-1 momentum - the last twelve months excluding the most recent one.
        The skip matters: the final month tends to reverse, and including it
        turns a momentum signal into a short-term-reversal one pointing the
        wrong way.
      * Price against its 200-day average, the crudest trend filter there is.
      * How far below the 52-week high, which says whether a name is already
        extended or is being bought on the way down.

    None of this changes where the portfolio should end up. Momentum is a
    real and well documented effect, but it is weak, it reverses without
    warning, and sizing a book on it is a different strategy from the one
    the optimiser is solving. It is used here only to order and time trades
    that are being made anyway - sell what is already stretched, buy what is
    not - because the destination is set by the optimiser and the route
    there is free.
    """
    out: dict[str, dict] = {}
    for ticker in closes.columns:
        series = closes[ticker].dropna()
        if len(series) < 260:
            continue
        last = float(series.iloc[-1])
        month_ago = float(series.iloc[-22])
        year_ago = float(series.iloc[-252])
        average200 = float(series.iloc[-200:].mean())
        high52 = float(series.iloc[-252:].max())

        momentum = (month_ago / year_ago - 1) if year_ago else 0.0
        recent = (last / month_ago - 1) if month_ago else 0.0
        out[ticker] = {
            "momentum12_1": round(momentum * 100, 2),
            "lastMonth": round(recent * 100, 2),
            "vsAverage200": round((last / average200 - 1) * 100, 2) if average200 else 0.0,
            "belowHigh52": round((last / high52 - 1) * 100, 2) if high52 else 0.0,
            "aboveTrend": bool(average200 and last > average200),
        }
    return out


#: Trades smaller than this are not worth a commission or a free-sell slot.
MIN_TRADE_EUR = 50.0


def rebalance_plan(current: dict[str, float], target: dict[str, float],
                   total_value: float, trends: dict[str, dict],
                   prices: dict[str, float] | None = None,
                   monthly_cash: float = 400.0,
                   free_sells: int = 5) -> dict:
    """The trades that move today's book toward a target allocation.

    Ordered by how much of the gap each trade closes, then broken into what
    can actually be done this month. Two constraints make the difference
    between a plan and a wish: buys are limited by new cash unless something
    is sold first, and sells are limited by the free-sell allowance.

    Trend is attached to each trade rather than driving it. Where two trades
    close a similar amount of the gap, doing the stretched sell and the
    un-stretched buy first is free; letting the signal change the target
    would be a different strategy altogether.
    """
    tickers = sorted(set(current) | set(target))
    trades = []
    for ticker in tickers:
        now = current.get(ticker, 0.0)
        want = target.get(ticker, 0.0)
        gap = want - now
        euros = gap / 100.0 * total_value
        if abs(euros) < MIN_TRADE_EUR:
            continue
        signal = trends.get(ticker, {})
        price = (prices or {}).get(ticker)
        trades.append({
            "ticker": ticker,
            "action": "buy" if euros > 0 else "sell",
            "currentPct": round(now, 2),
            "targetPct": round(want, 2),
            "euros": round(abs(euros), 2),
            "shares": round(abs(euros) / price, 2) if price else None,
            "momentum12_1": signal.get("momentum12_1"),
            "vsAverage200": signal.get("vsAverage200"),
            "belowHigh52": signal.get("belowHigh52"),
            "aboveTrend": signal.get("aboveTrend"),
        })

    sells = sorted([t for t in trades if t["action"] == "sell"],
                   key=lambda t: -t["euros"])
    buys = sorted([t for t in trades if t["action"] == "buy"],
                  key=lambda t: -t["euros"])

    # Among comparable trades, sell what is most extended and buy what is
    # least. Trades within 25% of each other in size count as comparable.
    def nudge(rows, prefer_extended: bool):
        if not rows:
            return rows
        biggest = rows[0]["euros"]
        band = [r for r in rows if r["euros"] >= biggest * 0.75]
        rest = [r for r in rows if r["euros"] < biggest * 0.75]
        band.sort(key=lambda r: -(r["vsAverage200"] or 0) if prefer_extended
                  else (r["vsAverage200"] or 0))
        return band + rest

    sells = nudge(sells, prefer_extended=True)
    buys = nudge(buys, prefer_extended=False)

    this_month_sells = sells[:free_sells]
    raised = sum(t["euros"] for t in this_month_sells)
    budget = monthly_cash + raised

    this_month_buys, spent = [], 0.0
    for trade in buys:
        if spent + trade["euros"] <= budget:
            this_month_buys.append(trade)
            spent += trade["euros"]
        elif budget - spent >= MIN_TRADE_EUR:
            partial = dict(trade)
            partial["euros"] = round(budget - spent, 2)
            partial["partial"] = True
            if partial.get("shares") and trade["euros"]:
                partial["shares"] = round(
                    trade["shares"] * partial["euros"] / trade["euros"], 2)
            this_month_buys.append(partial)
            spent = budget
            break

    turnover = sum(t["euros"] for t in trades)
    return {
        "sells": sells,
        "buys": buys,
        "thisMonth": {
            "sells": this_month_sells,
            "buys": this_month_buys,
            "raised": round(raised, 2),
            "newCash": monthly_cash,
            "spent": round(spent, 2),
            "freeSellsUsed": len(this_month_sells),
            "freeSells": free_sells,
        },
        "turnover": round(turnover, 2),
        "turnoverPct": round(100 * turnover / total_value, 1) if total_value else 0,
        "months": max(1, int(np.ceil(turnover / max(monthly_cash * 2, 1)))),
    }


#: Irish CGT: 33%, with the first EUR 1,270 of gains each year exempt.
CGT_RATE = 0.33
CGT_ANNUAL_EXEMPTION = 1270.0


def tax_on_plan(sells: list[dict], holdings: list[dict],
                cost_basis: dict[str, dict]) -> dict:
    """What the sells would cost in capital gains tax.

    The optimiser cannot see this and it is frequently the largest number in
    the whole exercise. A book sitting on large unrealised gains can be
    genuinely better off keeping a suboptimal allocation, because the tax is
    certain and immediate while the improvement in compounding is neither.

    Positions with no imported cost basis are reported separately rather than
    assumed to have none: treating an unknown basis as zero would price the
    entire proceeds as gain and overstate the bill.
    """
    by_ticker = {h["ticker"]: h for h in holdings if h.get("tradable")}
    priced, unpriced, gain = [], [], 0.0

    for trade in sells:
        ticker = trade["ticker"]
        held = by_ticker.get(ticker)
        basis = cost_basis.get(ticker) or {}
        unit_cost = basis.get("avg_cost")
        shares_held = float(held.get("shares") or 0) if held else 0.0
        value_held = float(held.get("value_eur") or 0) if held else 0.0

        if not unit_cost or not shares_held or not value_held:
            unpriced.append({"ticker": ticker, "euros": trade["euros"]})
            continue

        current_unit = value_held / shares_held
        fraction = min(1.0, trade["euros"] / value_held) if value_held else 0.0
        shares_sold = shares_held * fraction
        realised = shares_sold * (current_unit - unit_cost)
        gain += realised
        priced.append({
            "ticker": ticker,
            "euros": trade["euros"],
            "gain": round(realised, 2),
            "avgCost": unit_cost,
            "currentPrice": round(current_unit, 4),
        })

    taxable = max(0.0, gain - CGT_ANNUAL_EXEMPTION)
    return {
        "known": sorted(priced, key=lambda r: -r["gain"]),
        "unknownBasis": unpriced,
        "gain": round(gain, 2),
        "exemption": CGT_ANNUAL_EXEMPTION,
        "taxable": round(taxable, 2),
        "tax": round(taxable * CGT_RATE, 2),
        "rate": CGT_RATE,
        "coverage": round(100 * len(priced) / max(1, len(sells)), 0),
    }


#: What it costs to trade, by venue. Assumptions, not measurements - Davy's
#: contract notes do not break out commission from FX spread, and the two
#: are the same money to you either way. Adjust if your rate differs.
TRADING_COSTS = {
    "commission_pct": 0.005,     # Davy-style percentage commission
    "commission_min": 14.99,     # and its floor per contract note
    "fx_pct": 0.0015,            # currency conversion on a non-EUR trade
}


def trading_cost(trades: list[dict], holdings: list[dict],
                 assumptions: dict | None = None) -> dict:
    """What executing the plan costs in commission and currency conversion.

    Small trades are where this bites. A EUR 356 buy carrying a EUR 14.99
    minimum commission is paying 4.2% to be executed, which no allocation
    improvement recovers - so the per-trade cost as a percentage is reported
    alongside the total, because the total looks tolerable and the small
    trades inside it do not.
    """
    assumptions = {**TRADING_COSTS, **(assumptions or {})}
    currency = {h["ticker"]: h.get("currency", "EUR") for h in holdings}

    rows, total = [], 0.0
    for trade in trades:
        euros = float(trade["euros"])
        commission = max(euros * assumptions["commission_pct"],
                         assumptions["commission_min"])
        fx = euros * assumptions["fx_pct"] if currency.get(trade["ticker"], "EUR") != "EUR" else 0.0
        cost = commission + fx
        total += cost
        rows.append({
            "ticker": trade["ticker"],
            "action": trade["action"],
            "euros": round(euros, 2),
            "cost": round(cost, 2),
            "costPct": round(100 * cost / euros, 2) if euros else 0.0,
        })

    rows.sort(key=lambda r: -r["costPct"])
    return {
        "total": round(total, 2),
        "trades": rows,
        "worst": rows[0] if rows else None,
        "assumptions": assumptions,
    }
