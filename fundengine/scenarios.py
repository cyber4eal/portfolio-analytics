"""Stress, drawdown, frontier and contribution analysis.

Everything a fan chart cannot tell you. A Monte Carlo says what the model
thinks could happen; these say what did happen, and what the book's own
structure implies. When the two disagree the history is usually the more
informative one, because it contains the correlations that only show up
when things break.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from portfolio_analytics import align_returns, ewma_cov

TRADING_DAYS = 252

#: Episodes worth replaying if the sample reaches them. Dates are the peak
#: and trough of the drawdown, not the headlines around it.
EPISODES = (
    ("Covid crash", "2020-02-19", "2020-03-23"),
    ("2022 rate shock", "2022-01-03", "2022-10-12"),
    ("Aug 2024 yen carry unwind", "2024-07-31", "2024-08-05"),
    ("Apr 2025 tariff selloff", "2025-02-19", "2025-04-08"),
)


def drawdown_series(returns: pd.Series) -> list[dict]:
    """Underwater curve: how far below the previous peak, every day.

    Monthly points, because a five-year daily underwater chart is 1,300
    values to draw a shape that reads identically at 60.
    """
    level = (1 + returns).cumprod()
    underwater = level / level.cummax() - 1
    monthly = underwater.resample("ME").min()
    return [{"date": d.date().isoformat(), "value": round(float(v) * 100, 2)}
            for d, v in monthly.items() if pd.notna(v)]


def worst_windows(returns: pd.Series) -> list[dict]:
    """The worst rolling 1, 3 and 12 month stretches actually lived through.

    Rolling windows rather than calendar ones: the worst year for this book
    did not politely start in January.
    """
    out = []
    for label, days in (("1 month", 21), ("3 months", 63), ("12 months", 252)):
        if len(returns) < days + 5:
            continue
        rolled = (1 + returns).rolling(days).apply(np.prod, raw=True) - 1
        rolled = rolled.dropna()
        if rolled.empty:
            continue
        worst_end = rolled.idxmin()
        out.append({
            "window": label,
            "return": round(float(rolled.min()) * 100, 2),
            "ended": worst_end.date().isoformat(),
            "best": round(float(rolled.max()) * 100, 2),
        })
    return out


def episodes(returns: pd.Series) -> list[dict]:
    """Replay named market episodes, but only those the sample covers.

    A book whose history starts in 2025 cannot be shown a Covid number, and
    inventing one by splicing index returns onto it would be a fabrication
    dressed as a stress test.
    """
    out = []
    for name, start, end in EPISODES:
        window = returns.loc[str(start):str(end)]
        if window.empty:
            continue
        covered = (returns.index[0] <= pd.Timestamp(start))
        out.append({
            "name": name,
            "from": start, "to": end,
            "return": round(float((1 + window).prod() - 1) * 100, 2),
            "days": int(len(window)),
            "partial": not covered,
        })
    return out


def risk_contributions(returns: pd.DataFrame, weights: pd.Series) -> list[dict]:
    """Component volatility per holding - the Euler decomposition.

    Weight answers "how much do I own". This answers "how much of the
    portfolio's movement is this position", and the two diverge sharply
    once anything is leveraged, concentrated or uncorrelated.
    """
    cols = [c for c in returns.columns if c in weights.index]
    aligned = align_returns(returns, cols)
    w = weights.reindex(cols).fillna(0.0).to_numpy(dtype=float)
    w = w / w.sum()

    sigma = ewma_cov(aligned).to_numpy()
    vol = float(np.sqrt(w @ sigma @ w))
    if not vol:
        return []
    marginal = (sigma @ w) / vol
    component = w * marginal

    rows = [
        {"ticker": t,
         "weight": round(100 * float(w[i]), 2),
         "riskShare": round(100 * float(component[i] / vol), 2),
         "vol": round(100 * float(np.sqrt(sigma[i, i])), 2)}
        for i, t in enumerate(cols)
    ]
    rows.sort(key=lambda r: -r["riskShare"])
    return rows


def correlation_matrix(returns: pd.DataFrame, weights: pd.Series, top: int = 14) -> dict:
    """Correlation heatmap for the largest positions.

    Capped at the top holdings by weight: a 25x25 grid of three-letter
    tickers is a texture, not a chart.
    """
    cols = [c for c in returns.columns if c in weights.index]
    biggest = weights.reindex(cols).sort_values(ascending=False).head(top).index.tolist()
    aligned = align_returns(returns, biggest)
    corr = aligned.corr()
    return {
        "tickers": biggest,
        "matrix": [[round(float(corr.loc[a, b]), 3) for b in biggest] for a in biggest],
    }


def frontier(portfolio: pd.Series, fund: pd.Series, benchmark: pd.Series,
             steps: int = 21) -> list[dict]:
    """Every mix of the book and one fund, from 0% to 100%.

    The curve bends because correlation is below one. Where it bends
    furthest left is the mix with the least volatility, which is almost
    never the mix anyone would actually choose - but it shows how much of
    the risk reduction is available and how little of it costs return.
    """
    joined = pd.concat([portfolio.rename("p"), fund.rename("f"),
                        benchmark.rename("b")], axis=1, join="inner").dropna()
    if len(joined) < 60:
        return []

    out = []
    for i in range(steps):
        share = i / (steps - 1)
        mixed = joined["p"] * (1 - share) + joined["f"] * share
        years = len(mixed) / TRADING_DAYS
        total = float((1 + mixed).prod() - 1)
        # EWMA, to match the volatility every other panel reports. An
        # equal-weighted sigma here read 30.5% against the summary card's
        # 24.8% for the same portfolio, which looks like a contradiction
        # rather than like two estimators.
        vol = float(np.sqrt(ewma_cov(mixed.to_frame("m")).iloc[0, 0]))
        out.append({
            "fundWeight": round(share * 100, 1),
            "vol": round(vol * 100, 2),
            "return": round(((1 + total) ** (1 / years) - 1) * 100, 2) if years >= 1
                      else round(total * 100, 2),
        })
    return out


def monte_carlo_with_contributions(
    returns: pd.Series,
    start_value: float,
    monthly_contribution: float = 0.0,
    years: int = 10,
    n_paths: int = 10_000,
    seed: int = 11,
) -> dict:
    """Bootstrap projection that also accepts money over time.

    Contributions change the shape, not just the level: paying in monthly
    buys more units when prices are low, so the spread of outcomes narrows
    relative to a lump sum left alone. A projection without them badly
    misrepresents anyone still saving.
    """
    daily = returns.dropna().to_numpy()
    if daily.size < 250:
        return {}

    months = years * 12
    step = TRADING_DAYS // 12
    rng = np.random.default_rng(seed)

    block = 21
    n_blocks = int(np.ceil(months * step / block))
    starts = rng.integers(0, daily.size - block, size=(n_paths, n_blocks))
    offsets = np.arange(block)
    idx = (starts[:, :, None] + offsets[None, None, :]).reshape(n_paths, -1)
    sampled = daily[idx][:, : months * step]

    monthly_growth = (1 + sampled.reshape(n_paths, months, step)).prod(axis=2)

    values = np.full(n_paths, float(start_value))
    track = np.empty((n_paths, months))
    for month in range(months):
        values = values * monthly_growth[:, month] + monthly_contribution
        track[:, month] = values

    percentiles = (5, 25, 50, 75, 95)
    fan = []
    for month in range(0, months, 3):
        column = track[:, month]
        p5, p25, p50, p75, p95 = np.percentile(column, percentiles)
        fan.append({"month": month + 1, "p05": round(float(p5)), "p25": round(float(p25)),
                    "median": round(float(p50)), "p75": round(float(p75)),
                    "p95": round(float(p95))})

    paid_in = start_value + monthly_contribution * months
    final = track[:, -1]
    drift = float(daily.mean() * TRADING_DAYS)
    return {
        "years": years,
        "warning": (
            f"Resampled from {daily.size / TRADING_DAYS:.1f} years of this book's "
            f"own returns, which annualise to {drift:.0%}. Over a decade that "
            "compounds into a median no equity book has sustained. The spread "
            "between the 5th and 95th percentile is the part worth reading."
        ),
        "monthlyContribution": monthly_contribution,
        "paidIn": round(paid_in, 2),
        "fan": fan,
        "final": {
            "p05": round(float(np.percentile(final, 5))),
            "median": round(float(np.percentile(final, 50))),
            "p95": round(float(np.percentile(final, 95))),
            "mean": round(float(final.mean())),
            "probLoss": round(float((final < paid_in).mean()), 4),
        },
    }
