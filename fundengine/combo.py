"""The combo: the live book treated as a fund, then measured against real ones.

The whole point of modelling the portfolio as just another fund is that
every comparison becomes symmetric. Once the book has a return series, the
same volatility, beta, drawdown and VaR machinery in `portfolio_analytics`
applies to it and to a Vanguard ETF without special cases, and the blend of
the two is nothing more special than a third weight vector.

One honest caveat, stated here because it cannot be fixed with more code:
the book's history is reconstructed by applying *today's* weights to past
returns. It is what this portfolio would have done, not what it did - the
actual trade history is not in the sheet. Every fund it is compared against
is a real NAV series, so the book's past flatters itself by construction:
it never held the positions that were sold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field

import numpy as np
import pandas as pd

from portfolio_analytics import (
    align_returns,
    compute_risk,
    evaluate_additions,
    redundant_pairs,
    simulate,
)

PORTFOLIO_ID = "me"
TRAILING_PERIODS = (("1 month", 1), ("3 months", 3), ("YTD", None),
                    ("1 year", 12), ("3 years p.a.", 36), ("5 years p.a.", 60))


@dataclass
class Performance:
    cumulative: list[dict] = field(default_factory=list)
    discrete: list[dict] = field(default_factory=list)


@dataclass
class Line:
    """One row of the comparison table - a fund, or the book itself."""

    id: str
    name: str
    kind: str                     # "portfolio" | "fund"
    isin: str = ""
    issuer: str = ""
    asset: str = ""
    currency: str = "EUR"
    benchmark: str = ""
    ocf: float | None = None
    sri: int | None = None
    docs: list[dict] = field(default_factory=list)
    vol: float = 0.0
    beta: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    var_95_1d: float = 0.0
    tracking_error: float | None = None
    performance: Performance = field(default_factory=Performance)
    history: dict = field(default_factory=dict)
    series: list[list] = field(default_factory=list)

    def as_dict(self) -> dict:
        out = asdict(self)
        out["performance"] = {"cumulative": self.performance.cumulative,
                              "discrete": self.performance.discrete}
        return out


def portfolio_returns(returns: pd.DataFrame, weights: pd.Series) -> pd.Series:
    """The book's daily return series at current weights.

    Constant weights, which is a rebalanced-daily assumption. The
    alternative - letting weights drift from a start date - needs a
    starting basket this sheet does not record.
    """
    cols = [c for c in returns.columns if c in weights.index]
    aligned = align_returns(returns, cols)
    w = weights.reindex(cols).fillna(0.0).to_numpy(dtype=float)
    w = w / w.sum()
    return pd.Series(aligned.to_numpy() @ w, index=aligned.index, name=PORTFOLIO_ID)


def _index_from_returns(returns: pd.Series, base: float = 100.0) -> pd.Series:
    return base * (1 + returns).cumprod()


def _trailing_table(level: pd.Series, benchmark_level: pd.Series) -> list[dict]:
    from .prices import annualised, trailing

    rows = []
    for label, months in TRAILING_PERIODS:
        if months is None:
            start_of_year = pd.Timestamp(year=level.index[-1].year, month=1, day=1)
            prior = level[level.index < start_of_year]
            if prior.empty:
                continue
            fund = float(level.iloc[-1] / prior.iloc[-1] - 1)
            prior_b = benchmark_level[benchmark_level.index < start_of_year]
            bench = (float(benchmark_level.iloc[-1] / prior_b.iloc[-1] - 1)
                     if not prior_b.empty else None)
        else:
            fn = annualised if months >= 12 else trailing
            fund = fn(level, months)
            bench = fn(benchmark_level, months)
        if fund is None:
            continue
        if not math.isfinite(fund):
            continue
        row = {"period": label, "fund": round(fund * 100, 2)}
        if bench is not None and math.isfinite(bench):
            row["benchmark"] = round(bench * 100, 2)
        rows.append(row)
    return rows


def _risk_block(returns: pd.Series, benchmark: pd.Series, value: float) -> dict:
    """Reuse compute_risk on a single-column book so the fund rows and the
    portfolio row are produced by exactly the same estimator."""
    frame = returns.to_frame("self")
    report = compute_risk(frame, pd.Series([1.0], index=["self"]), value,
                          benchmark=benchmark)
    common = pd.concat([returns, benchmark], axis=1, join="inner").dropna()
    active = common.iloc[:, 0] - common.iloc[:, 1]
    tracking_error = float(active.std(ddof=1) * np.sqrt(252)) if len(active) > 20 else None
    return {
        "vol": round(report.vol_annual * 100, 2),
        "beta": round(report.beta, 2),
        "max_drawdown": round(report.max_drawdown * 100, 2),
        "sharpe": round(report.sharpe, 2),
        "var_95_1d": round(report.var_95_1d, 2),
        "tracking_error": round(tracking_error * 100, 2) if tracking_error else None,
    }


def build_line(
    line_id: str,
    name: str,
    kind: str,
    returns: pd.Series,
    benchmark_returns: pd.Series,
    value: float,
    **meta,
) -> Line:
    """`benchmark_returns` is the series; `benchmark` in **meta is the label
    printed on the row. Two different things, so two different names.

    Leading NaNs are dropped first. A fund that listed inside the window
    carries them at the front of the frame, and cumprod turns one NaN into
    an all-NaN index - which then reaches the page as a bare `NaN` literal.
    """
    returns = returns.dropna()
    level = _index_from_returns(returns)
    benchmark_level = _index_from_returns(benchmark_returns)

    from .prices import calendar_years

    line = Line(id=line_id, name=name, kind=kind, **meta)
    for key, val in _risk_block(returns, benchmark_returns, value).items():
        setattr(line, key, val)
    line.performance.cumulative = _trailing_table(level, benchmark_level)
    line.performance.discrete = [
        {"year": year, "value": round(value_ * 100, 2)}
        for year, value_ in calendar_years(level)
    ]
    line.history = {
        "from": returns.index[0].date().isoformat(),
        "to": returns.index[-1].date().isoformat(),
        "years": round((returns.index[-1] - returns.index[0]).days / 365.25, 2),
    }
    monthly = level.resample("ME").last()
    line.series = [[d.date().isoformat(), round(float(v), 4)] for d, v in monthly.items()]
    return line


def blend(
    portfolio: pd.Series,
    fund_returns: pd.DataFrame,
    fund_weights: dict[str, float],
    benchmark: pd.Series,
    value: float,
) -> dict:
    """Mix the book with one or more funds and report what changed.

    `fund_weights` are the fund shares of the combined pot; the book takes
    the remainder. Weights over 1.0 are rejected rather than normalised,
    because silently rescaling a typo turns "add 5%" into a different
    portfolio than the one asked about.
    """
    fund_total = sum(fund_weights.values())
    if fund_total > 1.0 + 1e-9:
        raise ValueError(f"fund weights sum to {fund_total:.2f}, leaving nothing for the book")

    columns = [PORTFOLIO_ID] + list(fund_weights)
    combined = pd.concat(
        [portfolio.rename(PORTFOLIO_ID)] + [fund_returns[t].rename(t) for t in fund_weights],
        axis=1, join="inner",
    ).dropna()
    weights = pd.Series(
        [1.0 - fund_total] + [fund_weights[t] for t in fund_weights], index=columns
    )

    before = _risk_block(combined[PORTFOLIO_ID], benchmark, value)
    mixed = pd.Series(combined.to_numpy() @ weights.to_numpy(), index=combined.index)
    after = _risk_block(mixed, benchmark, value)

    return {
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "before": before,
        "after": after,
        "delta": {k: round(after[k] - before[k], 2)
                  for k in ("vol", "beta", "max_drawdown", "sharpe")
                  if before[k] is not None and after[k] is not None},
        "overlap": [p.as_dict() for p in redundant_pairs(combined, weights, threshold=0.85)],
        "dates": [combined.index[0].date().isoformat(),
                  combined.index[-1].date().isoformat()],
    }


def rank_additions(
    holding_returns: pd.DataFrame,
    weights: pd.Series,
    fund_returns: pd.DataFrame,
    benchmark: pd.Series,
    allocation: float = 0.10,
) -> list[dict]:
    """Which fund, bought at `allocation`, does most for the book.

    This is the question the fund centre is actually for. It is answered
    against the holdings themselves rather than the book's blended series,
    so a fund that overlaps a specific position is penalised for that
    overlap rather than being netted against the whole.
    """
    cols = [c for c in holding_returns.columns if c in weights.index]
    aligned = align_returns(holding_returns, cols)
    candidates = fund_returns.reindex(aligned.index).dropna(axis=1, how="all")
    results = evaluate_additions(aligned, weights, candidates, benchmark,
                                 allocation=allocation)
    return [
        {
            "ticker": r.ticker,
            "correlation": round(r.correlation_to_portfolio, 3),
            "fund_vol": round(r.vol_annual * 100, 2),
            "new_vol": round(r.new_portfolio_vol * 100, 2),
            "vol_change": round(r.vol_change * 100, 2),
            "new_beta": round(r.new_beta, 2),
            "beta_change": round(r.beta_change, 2),
        }
        for r in results
    ]


#: A long-run forward assumption, used instead of whatever the sample did.
#: EUR cash rate and a standard equity risk premium; both are assumptions and
#: are labelled as such on screen rather than presented as measurements.
RISK_FREE = 0.025
EQUITY_RISK_PREMIUM = 0.045


def expected_return(beta: float) -> float:
    """CAPM: the return you can defend asking for, given the risk taken.

    The alternative - projecting whatever the sample happened to deliver -
    is what makes these charts lie. A book measured across sixteen bullish
    months annualises near 50%, and compounding 50% for a decade produces a
    number no asset class has ever sustained. Nothing about the past sixteen
    months entitles the next ten years to repeat them.
    """
    return RISK_FREE + max(0.0, beta) * EQUITY_RISK_PREMIUM


def projection(portfolio: pd.Series, value: float, beta: float = 1.0,
               seed: int = 7) -> dict:
    """Ten-year Monte Carlo on the book.

    Two halves, deliberately taken from different places. The *shape* -
    fat tails, volatility clustering - is bootstrapped from the book's own
    daily returns, because that is what the book actually does. The *drift*
    is a forward assumption, because the sample's drift is not a forecast.

    Resampled returns are shifted so they centre on the assumption rather
    than on the sample mean, which keeps the distribution's shape and moves
    only its centre.
    """
    daily = portfolio.dropna().to_numpy()
    sample_mu = float(daily.mean() * 252)
    sigma = float(daily.std(ddof=1) * np.sqrt(252))
    target = expected_return(beta)

    shifted = daily - (sample_mu - target) / 252
    result = simulate(value, target, sigma, engine="bootstrap",
                      daily_returns=shifted, seed=seed, n_paths=20_000)

    payload = result.as_dict()
    payload["sample_years"] = round(len(daily) / 252, 2)
    payload["sample_return"] = round(sample_mu, 4)
    payload["assumed_return"] = round(target, 4)
    payload["beta"] = round(beta, 2)
    payload["basis"] = (
        f"Drift is a forward assumption, not the sample: {RISK_FREE:.1%} cash "
        f"plus beta {beta:.2f} x {EQUITY_RISK_PREMIUM:.1%} equity risk premium "
        f"= {target:.1%} a year. The book's own {len(daily) / 252:.1f} years "
        f"annualise to {sample_mu:.0%}, which is a bull market being mistaken "
        f"for an expected return."
    )
    payload["warning"] = (
        f"The spread comes from resampling this book's own returns, and those "
        f"{len(daily) / 252:.1f} years contain no 2008 and no 2020. The worst "
        "case shown is therefore optimistic: the sample has no true crash in "
        "it to resample from."
    )
    return payload
