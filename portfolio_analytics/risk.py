"""Risk engine: volatility, beta, correlation, VaR/CVaR, component VaR.

Everything here works off a returns matrix (dates x tickers) and a weight
vector, so it is indifferent to where the prices came from.

Deliberate choices worth knowing about:

  * Covariance is EWMA-weighted by default. An equal-weighted 3-year window
    treats a print from 2023 as being as informative as yesterday, which is
    wrong when volatility clusters. lambda=0.94 is the RiskMetrics default.
  * Both parametric and historical VaR are returned. They disagree when the
    tails are fat, and that disagreement is the interesting number - if
    historical VaR is much worse than parametric, normality is lying to you.
  * Component VaR sums to total VaR, so it answers "which position is
    actually costing me risk" rather than just "which position is biggest".
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Sequence

import numpy as np
import pandas as pd

TRADING_DAYS = 252

# Below this many overlapping dates the covariance is noise, not an estimate.
MIN_OBSERVATIONS = 60

Z_95 = 1.6448536269514722
Z_99 = 2.3263478740408408


@dataclass
class PositionRisk:
    ticker: str
    weight: float
    vol_annual: float
    beta: float
    marginal_var: float
    component_var: float
    pct_of_var: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskReport:
    total_value: float
    vol_annual: float
    beta: float
    var_95_1d: float
    cvar_95_1d: float
    var_95_1d_historical: float
    var_95_1m: float
    max_drawdown: float
    sharpe: float
    effective_holdings: float
    avg_correlation: float
    positions: list[PositionRisk]

    def as_dict(self) -> dict:
        d = asdict(self)
        d["positions"] = [p.as_dict() for p in self.positions]
        return d


def align_returns(
    returns: pd.DataFrame,
    columns: Sequence[str] | None = None,
    min_rows: int = MIN_OBSERVATIONS,
) -> pd.DataFrame:
    """Restrict to `columns` and keep only dates where every series printed.

    Holdings sit on different exchanges and those exchanges close on
    different days, so the raw matrix is full of holes - a Xetra name has
    no print on July 4th, a US name has none on a German holiday. Left in
    place a single NaN propagates through the covariance and turns vol,
    beta, VaR and every position number into nan, which then serialises as
    a bare `NaN` that JSON.parse rejects.

    Dropping incomplete rows costs a few percent of the sample and keeps
    the covariance honest. Aligning on dates rather than array position is
    the whole point: same-length series from different calendars are not
    the same dates.
    """
    if columns is not None:
        returns = returns[list(columns)]
    aligned = returns.dropna(how="any")
    if len(aligned) < min_rows:
        raise ValueError(
            f"only {len(aligned)} dates where every series has a print, "
            f"need at least {min_rows} - check for a ticker with a short "
            f"or broken history"
        )
    return aligned


def ewma_cov(returns: pd.DataFrame, lam: float = 0.94) -> pd.DataFrame:
    """Exponentially weighted covariance, annualised.

    Recent observations dominate: with lam=0.94 the half-life is about 11
    trading days, so a volatility regime change shows up in weeks rather
    than being diluted across the whole lookback.
    """
    x = returns.to_numpy(dtype=float)
    x = x - x.mean(axis=0)
    n = x.shape[0]

    weights = lam ** np.arange(n - 1, -1, -1)
    weights /= weights.sum()

    cov = (x * weights[:, None]).T @ x
    return pd.DataFrame(
        cov * TRADING_DAYS, index=returns.columns, columns=returns.columns
    )


def effective_holdings(weights: np.ndarray) -> float:
    """Inverse Herfindahl: how many equally-sized positions this portfolio
    behaves like. Twenty holdings where one is 40% behaves like far fewer
    than twenty."""
    w = np.abs(weights)
    w = w / w.sum()
    return float(1.0 / np.sum(w**2))


def compute_risk(
    returns: pd.DataFrame,
    weights: pd.Series,
    total_value: float,
    benchmark: pd.Series | None = None,
    risk_free_annual: float = 0.02,
    lam: float = 0.94,
) -> RiskReport:
    cols = [c for c in returns.columns if c in weights.index]
    returns = align_returns(returns, cols)
    w = weights.reindex(cols).fillna(0.0).to_numpy(dtype=float)
    if w.sum() <= 0:
        raise ValueError("weights sum to zero - nothing to analyse")
    w = w / w.sum()

    cov = ewma_cov(returns, lam=lam)
    sigma = cov.to_numpy()

    var_p = float(w @ sigma @ w)
    vol_annual = float(np.sqrt(var_p))
    vol_daily = vol_annual / np.sqrt(TRADING_DAYS)

    port_returns = returns.to_numpy() @ w
    port_series = pd.Series(port_returns, index=returns.index)

    # Parametric vs historical: the gap is the fat-tail penalty.
    var_95_1d = float(total_value * Z_95 * vol_daily)
    var_95_1d_hist = float(-np.percentile(port_returns, 5) * total_value)
    tail = port_returns[port_returns <= np.percentile(port_returns, 5)]
    cvar_95_1d = float(-tail.mean() * total_value) if tail.size else var_95_1d

    curve = (1 + port_series).cumprod()
    max_dd = float((curve / curve.cummax() - 1).min())

    excess = port_returns.mean() * TRADING_DAYS - risk_free_annual
    sharpe = float(excess / vol_annual) if vol_annual else 0.0

    beta = 0.0
    if benchmark is not None:
        aligned = pd.concat([port_series, benchmark], axis=1, join="inner").dropna()
        if len(aligned) > 20:
            bench = aligned.iloc[:, 1].to_numpy()
            port = aligned.iloc[:, 0].to_numpy()
            bench_var = bench.var(ddof=1)
            beta = float(np.cov(port, bench, ddof=1)[0, 1] / bench_var) if bench_var else 0.0

    corr = returns.corr().to_numpy()
    off_diag = corr[~np.eye(len(cols), dtype=bool)]
    avg_corr = float(np.nanmean(off_diag)) if off_diag.size else 0.0

    # Marginal VaR is the derivative of portfolio VaR wrt each weight;
    # component VaR is that times the weight, and the components sum to
    # the total. That additivity is what makes it actionable.
    marginal = (sigma @ w) / vol_annual if vol_annual else np.zeros_like(w)
    component = w * marginal          # these sum to vol_annual, not to var_p
    share = component / vol_annual if vol_annual else np.zeros_like(w)
    comp_var_money = share * var_95_1d

    asset_vol = np.sqrt(np.diag(sigma))
    asset_beta = (sigma @ w) / var_p if var_p else np.zeros_like(w)

    positions = [
        PositionRisk(
            ticker=t,
            weight=float(w[i]),
            vol_annual=float(asset_vol[i]),
            beta=float(asset_beta[i]),
            marginal_var=float(marginal[i]),
            component_var=float(comp_var_money[i]),
            pct_of_var=float(share[i]),
        )
        for i, t in enumerate(cols)
    ]
    positions.sort(key=lambda p: p.component_var, reverse=True)

    return RiskReport(
        total_value=total_value,
        vol_annual=vol_annual,
        beta=beta,
        var_95_1d=var_95_1d,
        cvar_95_1d=cvar_95_1d,
        var_95_1d_historical=var_95_1d_hist,
        var_95_1m=float(var_95_1d * np.sqrt(21)),
        max_drawdown=max_dd,
        sharpe=sharpe,
        effective_holdings=effective_holdings(w),
        avg_correlation=avg_corr,
        positions=positions,
    )
