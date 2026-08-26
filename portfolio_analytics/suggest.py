"""Diversification analysis: where correlation and beta are costing you.

This does NOT tell you what to buy. It answers three mechanical questions
and leaves the judgement to you:

  1. Which pairs are so correlated they are effectively one position?
  2. If I trimmed position X by 1%, how much portfolio volatility goes away
     per unit of value sold? (risk efficiency of a trim)
  3. What would adding a candidate asset do to portfolio vol and beta?

Every number here is descriptive. None of it is advice, and correlations
measured in calm markets understate what happens in a crash - the pairs
flagged as diversifying are exactly the ones most likely to converge when
it matters.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .risk import MIN_OBSERVATIONS, TRADING_DAYS, align_returns, ewma_cov


@dataclass
class RedundantPair:
    a: str
    b: str
    correlation: float
    combined_weight: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrimCandidate:
    ticker: str
    weight: float
    marginal_var: float
    vol_per_weight: float
    verdict: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class AddCandidate:
    ticker: str
    correlation_to_portfolio: float
    vol_annual: float
    new_portfolio_vol: float
    vol_change: float
    new_beta: float
    beta_change: float

    def as_dict(self) -> dict:
        return asdict(self)


def redundant_pairs(
    returns: pd.DataFrame, weights: pd.Series, threshold: float = 0.80
) -> list[RedundantPair]:
    """Pairs correlated above the threshold, heaviest combined weight first.

    Two holdings at 0.9 correlation are close to one position with a
    two-line entry in your spreadsheet - the diversification is cosmetic.
    """
    cols = [c for c in returns.columns if c in weights.index]
    corr = align_returns(returns, cols).corr()
    out: list[RedundantPair] = []
    cols = list(corr.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1 :]:
            c = corr.loc[a, b]
            if pd.notna(c) and c >= threshold:
                out.append(
                    RedundantPair(
                        a=a,
                        b=b,
                        correlation=float(c),
                        combined_weight=float(
                            weights.get(a, 0.0) + weights.get(b, 0.0)
                        ),
                    )
                )
    out.sort(key=lambda p: p.combined_weight, reverse=True)
    return out


def trim_candidates(
    returns: pd.DataFrame, weights: pd.Series, lam: float = 0.94
) -> list[TrimCandidate]:
    """Rank positions by how much volatility each unit of weight buys.

    A position with high marginal VaR relative to its size is doing more
    damage per euro than its weight suggests. That is not the same as
    "biggest position" - the largest holding can be risk-efficient if it
    is uncorrelated with everything else.
    """
    cols = [c for c in returns.columns if c in weights.index]
    w = weights.reindex(cols).fillna(0.0).to_numpy(dtype=float)
    if w.sum() <= 0:
        raise ValueError("weights sum to zero - nothing to analyse")
    w = w / w.sum()
    sigma = ewma_cov(align_returns(returns, cols), lam=lam).to_numpy()

    vol = float(np.sqrt(w @ sigma @ w))
    marginal = (sigma @ w) / vol if vol else np.zeros_like(w)

    median_marginal = float(np.median(marginal))
    out: list[TrimCandidate] = []
    for i, t in enumerate(cols):
        ratio = marginal[i] / median_marginal if median_marginal else 1.0
        if ratio > 1.5:
            verdict = "carries well above median risk per unit of weight"
        elif ratio < 0.6:
            verdict = "risk-efficient - diversifying relative to the rest"
        else:
            verdict = "about average"
        out.append(
            TrimCandidate(
                ticker=t,
                weight=float(w[i]),
                marginal_var=float(marginal[i]),
                vol_per_weight=float(ratio),
                verdict=verdict,
            )
        )
    out.sort(key=lambda c: c.vol_per_weight, reverse=True)
    return out


def _beta(port: pd.Series, benchmark: pd.Series) -> float:
    """Beta of a portfolio return series against a benchmark, aligned on dates."""
    aligned = pd.concat([port, benchmark], axis=1, join="inner").dropna()
    if len(aligned) <= 20:
        return 0.0
    bench = aligned.iloc[:, 1].to_numpy()
    bench_var = bench.var(ddof=1)
    if not bench_var:
        return 0.0
    return float(
        np.cov(aligned.iloc[:, 0].to_numpy(), bench, ddof=1)[0, 1] / bench_var
    )


def evaluate_additions(
    returns: pd.DataFrame,
    weights: pd.Series,
    candidates: pd.DataFrame,
    benchmark: pd.Series,
    allocation: float = 0.05,
    lam: float = 0.94,
) -> list[AddCandidate]:
    """What each candidate would do to portfolio vol and beta at `allocation`.

    Funded pro-rata from existing holdings, which is the honest comparison -
    you cannot add without selling something.

    The before and after numbers are both measured on the dates the
    candidate actually traded. Measuring the baseline over the full history
    and the new portfolio over the candidate's shorter one compares two
    different market regimes and charges the difference to the candidate:
    a fund that only listed in 2023 would look like a volatility cure
    purely because 2022 is missing from its half of the comparison.

    Candidates with fewer than MIN_OBSERVATIONS overlapping dates are
    skipped rather than reported on thin evidence.
    """
    cols = [c for c in returns.columns if c in weights.index]
    w = weights.reindex(cols).fillna(0.0).to_numpy(dtype=float)
    if w.sum() <= 0:
        raise ValueError("weights sum to zero - nothing to analyse")
    w = w / w.sum()

    out: list[AddCandidate] = []
    for tic in candidates.columns:
        joint = pd.concat([returns[cols], candidates[[tic]]], axis=1).dropna(how="any")
        if len(joint) < MIN_OBSERVATIONS:
            continue

        held = joint[cols]
        base_vol = float(np.sqrt(w @ ewma_cov(held, lam=lam).to_numpy() @ w))
        base_beta = _beta(pd.Series(held.to_numpy() @ w, index=joint.index), benchmark)

        w_new = np.append(w * (1 - allocation), allocation)
        vol_new = float(np.sqrt(w_new @ ewma_cov(joint, lam=lam).to_numpy() @ w_new))
        beta_new = _beta(
            pd.Series(joint.to_numpy() @ w_new, index=joint.index), benchmark
        )

        cand_vol = float(joint[tic].std(ddof=1) * np.sqrt(TRADING_DAYS))
        corr_to_port = float(
            np.corrcoef(held.to_numpy() @ w, joint[tic].to_numpy())[0, 1]
        )

        out.append(
            AddCandidate(
                ticker=tic,
                correlation_to_portfolio=corr_to_port,
                vol_annual=cand_vol,
                new_portfolio_vol=vol_new,
                vol_change=vol_new - base_vol,
                new_beta=beta_new,
                beta_change=beta_new - base_beta,
            )
        )

    out.sort(key=lambda c: c.vol_change)
    return out
