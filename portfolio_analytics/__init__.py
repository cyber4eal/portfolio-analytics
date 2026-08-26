"""Portfolio analytics: risk, Monte Carlo, and diversification analysis.

Three modules, all of which take a returns matrix (dates x tickers) and a
weight vector and return plain dataclasses with an `as_dict()` for the API
layer:

  risk        - volatility, beta, VaR/CVaR, component VaR per position
  montecarlo  - fan chart bands and horizon box plots
  suggest     - redundant pairs, trim efficiency, candidate additions

Nothing here fetches prices or knows about a broker. Feed it returns.
"""

from __future__ import annotations

from .montecarlo import (
    BoxStat,
    Engine,
    FanBand,
    SimulationResult,
    simulate,
)
from .risk import (
    MIN_OBSERVATIONS,
    PositionRisk,
    RiskReport,
    TRADING_DAYS,
    align_returns,
    compute_risk,
    effective_holdings,
    ewma_cov,
)
from .suggest import (
    AddCandidate,
    RedundantPair,
    TrimCandidate,
    evaluate_additions,
    redundant_pairs,
    trim_candidates,
)

__version__ = "0.1.0"

__all__ = [
    "AddCandidate",
    "BoxStat",
    "Engine",
    "FanBand",
    "MIN_OBSERVATIONS",
    "PositionRisk",
    "RedundantPair",
    "RiskReport",
    "SimulationResult",
    "TRADING_DAYS",
    "TrimCandidate",
    "align_returns",
    "compute_risk",
    "effective_holdings",
    "evaluate_additions",
    "ewma_cov",
    "redundant_pairs",
    "simulate",
    "trim_candidates",
    "__version__",
]
