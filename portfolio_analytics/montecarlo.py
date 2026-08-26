"""Monte Carlo simulation: fan chart bands and horizon box plots.

Ported from the Apps Script version, with the parts that were forced by
Google Sheets removed:

  * Sheets had no box-plot chart type, so the box plot was a candlestick
    hack that could not draw a median or print labels. Here the frontend
    gets raw percentiles and draws a real box plot.
  * The Sheets fan was a stack of *delta* bands, so every tooltip showed a
    band thickness rather than a portfolio value. Here we return absolute
    levels and let the chart stack them visually.
  * Sheets could only hold one portfolio's results at a time. Here every
    portfolio is simulated on demand.

Two engines are provided:

  gbm        - lognormal random walk from a single mu/sigma. Fast, smooth,
               and what the spreadsheet did. Understates tail risk because
               it assumes normal log-returns.
  bootstrap  - resamples actual historical daily returns in blocks, so fat
               tails and volatility clustering survive into the projection.
               Slower and needs price history, but far more honest at the
               5th/95th percentiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal, Sequence

import numpy as np

Engine = Literal["gbm", "bootstrap"]

# The percentiles every consumer of this module expects, in ascending order.
PERCENTILES: tuple[float, ...] = (5.0, 25.0, 50.0, 75.0, 95.0)

TRADING_DAYS = 252
MONTHS_PER_YEAR = 12


@dataclass(frozen=True)
class FanBand:
    """One horizon step of the fan: absolute portfolio values, not deltas."""

    month: int
    p05: float
    p25: float
    median: float
    p75: float
    p95: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BoxStat:
    """One box in the horizon box plot. Carries the median, unlike the
    candlestick it replaces."""

    label: str
    months: int
    p05: float
    p25: float
    median: float
    p75: float
    p95: float
    mean: float
    prob_loss: float  # P(ending value < starting value)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class SimulationResult:
    start_value: float
    engine: Engine
    n_paths: int
    mu_annual: float
    sigma_annual: float
    fan: list[FanBand] = field(default_factory=list)
    boxes: list[BoxStat] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "start_value": self.start_value,
            "engine": self.engine,
            "n_paths": self.n_paths,
            "mu_annual": self.mu_annual,
            "sigma_annual": self.sigma_annual,
            "fan": [b.as_dict() for b in self.fan],
            "boxes": [b.as_dict() for b in self.boxes],
        }


def _monthly_paths_gbm(
    start_value: float,
    mu_annual: float,
    sigma_annual: float,
    months: int,
    n_paths: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Return an (n_paths, months) array of month-end values.

    Monthly stepping is distributionally identical to daily stepping for
    GBM, because the increments are i.i.d. normal and sum over the step.
    120 steps instead of 2,520 for a 10-year horizon.

    Note the -sigma^2/2 term: the MEDIAN path grows at mu - sigma^2/2, not
    at mu. At 25% volatility that drag is over 3 percentage points a year,
    which is why a "flat looking" median line is usually correct rather
    than a bug.
    """
    dt = 1.0 / MONTHS_PER_YEAR
    drift = (mu_annual - 0.5 * sigma_annual**2) * dt
    vol_step = sigma_annual * np.sqrt(dt)

    shocks = rng.standard_normal((n_paths, months))
    log_paths = np.cumsum(drift + vol_step * shocks, axis=1)
    return start_value * np.exp(log_paths)


def _monthly_paths_bootstrap(
    start_value: float,
    daily_returns: Sequence[float],
    months: int,
    n_paths: int,
    rng: np.random.Generator,
    block_days: int = 21,
) -> np.ndarray:
    """Block bootstrap of real daily returns, aggregated to month ends.

    Sampling in blocks rather than single days preserves volatility
    clustering - calm periods and violent periods stay lumpy instead of
    being averaged into a smooth normal.
    """
    r = np.asarray(daily_returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < block_days * 2:
        raise ValueError(
            f"need at least {block_days * 2} daily returns to bootstrap, got {r.size}"
        )

    total_days = months * (TRADING_DAYS // MONTHS_PER_YEAR)
    n_blocks = int(np.ceil(total_days / block_days))
    max_start = r.size - block_days

    starts = rng.integers(0, max_start + 1, size=(n_paths, n_blocks))
    offsets = np.arange(block_days)
    idx = (starts[:, :, None] + offsets[None, None, :]).reshape(n_paths, -1)
    sampled = r[idx][:, :total_days]

    log_paths = np.cumsum(np.log1p(sampled), axis=1)
    values = start_value * np.exp(log_paths)

    step = TRADING_DAYS // MONTHS_PER_YEAR
    month_end_idx = np.arange(1, months + 1) * step - 1
    return values[:, month_end_idx]


def simulate(
    start_value: float,
    mu_annual: float,
    sigma_annual: float,
    *,
    engine: Engine = "gbm",
    daily_returns: Sequence[float] | None = None,
    fan_months: int = 60,
    fan_step: int = 3,
    box_horizons: Sequence[tuple[str, int]] = (
        ("1 year", 12),
        ("3 years", 36),
        ("5 years", 60),
        ("10 years", 120),
    ),
    n_paths: int = 20_000,
    seed: int | None = None,
) -> SimulationResult:
    """Simulate once, then read both the fan and the boxes off the same paths.

    Doing it in one pass matters: the spreadsheet ran the simulation twice
    and the box plot could disagree with the fan at the same horizon.
    """
    if start_value <= 0:
        raise ValueError(f"start_value must be > 0, got {start_value}")
    if sigma_annual <= 0:
        raise ValueError(f"sigma_annual must be > 0, got {sigma_annual}")

    horizon_months = max(fan_months, max(m for _, m in box_horizons))
    rng = np.random.default_rng(seed)

    if engine == "bootstrap":
        if daily_returns is None:
            raise ValueError("engine='bootstrap' requires daily_returns")
        paths = _monthly_paths_bootstrap(
            start_value, daily_returns, horizon_months, n_paths, rng
        )
    else:
        paths = _monthly_paths_gbm(
            start_value, mu_annual, sigma_annual, horizon_months, n_paths, rng
        )

    result = SimulationResult(
        start_value=start_value,
        engine=engine,
        n_paths=n_paths,
        mu_annual=mu_annual,
        sigma_annual=sigma_annual,
    )

    # Month 0 is the known starting point, not a simulated one.
    result.fan.append(
        FanBand(0, start_value, start_value, start_value, start_value, start_value)
    )
    for month in range(fan_step, fan_months + 1, fan_step):
        col = paths[:, month - 1]
        p05, p25, med, p75, p95 = np.percentile(col, PERCENTILES)
        result.fan.append(FanBand(month, p05, p25, med, p75, p95))

    for label, months in box_horizons:
        col = paths[:, months - 1]
        p05, p25, med, p75, p95 = np.percentile(col, PERCENTILES)
        result.boxes.append(
            BoxStat(
                label=label,
                months=months,
                p05=p05,
                p25=p25,
                median=med,
                p75=p75,
                p95=p95,
                mean=float(col.mean()),
                prob_loss=float((col < start_value).mean()),
            )
        )

    return result
