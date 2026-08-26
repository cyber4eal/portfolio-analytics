from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from portfolio_analytics import suggest
from portfolio_analytics.risk import ewma_cov


def test_near_clones_are_flagged_as_redundant(returns, weights):
    """MSFT is built as 0.95 * AAPL in the fixture, so the pair is one
    position wearing two names."""
    pairs = suggest.redundant_pairs(returns, weights, threshold=0.80)
    flagged = {tuple(sorted((p.a, p.b))) for p in pairs}

    assert ("AAPL", "MSFT") in flagged
    assert all(p.correlation >= 0.80 for p in pairs)


def test_redundant_pairs_are_ranked_by_money_at_stake(returns, weights):
    pairs = suggest.redundant_pairs(returns, weights, threshold=0.0)
    combined = [p.combined_weight for p in pairs]
    assert combined == sorted(combined, reverse=True)


def test_a_high_threshold_flags_nothing(returns, weights):
    assert suggest.redundant_pairs(returns, weights, threshold=0.999) == []


def test_redundancy_ignores_columns_that_are_not_held(returns, weights, benchmark):
    """A benchmark column riding along in the returns frame is not a holding
    and must not turn up as a redundant pair."""
    with_bench = returns.assign(SPY=benchmark)
    pairs = suggest.redundant_pairs(with_bench, weights, threshold=0.0)

    assert not any("SPY" in (p.a, p.b) for p in pairs)


def test_trim_ranking_is_risk_per_unit_of_weight_not_size(returns, weights):
    candidates = suggest.trim_candidates(returns, weights)
    ratios = [c.vol_per_weight for c in candidates]

    assert ratios == sorted(ratios, reverse=True)
    assert {c.ticker for c in candidates} == set(returns.columns)
    assert float(np.median(ratios)) == pytest.approx(1.0, abs=0.02)


def test_a_diversifying_holding_is_called_risk_efficient(dates, rng):
    """Three correlated names plus one independent one: the loner should be
    the risk-efficient position even though it is not the smallest."""
    core = rng.normal(0.0005, 0.020, len(dates))
    frame = pd.DataFrame(
        {
            "A": core + rng.normal(0, 0.002, len(dates)),
            "B": core + rng.normal(0, 0.002, len(dates)),
            "C": core + rng.normal(0, 0.002, len(dates)),
            "HEDGE": rng.normal(0.0002, 0.006, len(dates)),
        },
        index=dates,
    )
    weights = pd.Series([0.3, 0.3, 0.2, 0.2], index=frame.columns)

    by_ticker = {c.ticker: c for c in suggest.trim_candidates(frame, weights)}
    assert by_ticker["HEDGE"].vol_per_weight < by_ticker["A"].vol_per_weight
    assert "risk-efficient" in by_ticker["HEDGE"].verdict


def test_an_uncorrelated_candidate_lowers_portfolio_volatility(
    returns, weights, benchmark, dates, rng
):
    additions = pd.DataFrame(
        {
            "TLT": rng.normal(0.0001, 0.005, len(dates)),
            "LEVERED": returns["AAPL"] * 2.0,
        },
        index=dates,
    )
    results = {c.ticker: c for c in suggest.evaluate_additions(returns, weights, additions, benchmark)}

    assert results["TLT"].vol_change < 0
    assert results["LEVERED"].vol_change > results["TLT"].vol_change
    assert abs(results["LEVERED"].correlation_to_portfolio) > abs(
        results["TLT"].correlation_to_portfolio
    )


def test_additions_are_ranked_by_volatility_impact(returns, weights, benchmark, dates, rng):
    additions = pd.DataFrame(
        {
            "CALM": rng.normal(0.0, 0.004, len(dates)),
            "WILD": rng.normal(0.0, 0.045, len(dates)),
        },
        index=dates,
    )
    changes = [c.vol_change for c in suggest.evaluate_additions(returns, weights, additions, benchmark)]
    assert changes == sorted(changes)


def test_baseline_is_measured_on_the_candidate_window(returns, weights, benchmark, dates, rng):
    """Regression: the baseline used to be computed over the full history
    while the new portfolio was computed over the candidate's shorter one,
    so a young listing was charged for a regime change it was not present
    for. Both halves of the comparison must come from the same dates.

    Asserted directly: the implied baseline (new vol minus the reported
    change) has to equal the portfolio's volatility over exactly the dates
    the candidate traded, not over the full history.
    """
    violent = returns.copy()
    violent.iloc[: len(dates) // 2] *= 6.0

    late = pd.Series(np.nan, index=dates, name="NEWLISTING")
    tail = dates[len(dates) // 2 :]
    late.loc[tail] = rng.normal(0.0, 0.012, len(tail))

    result = suggest.evaluate_additions(
        violent, weights, late.to_frame(), benchmark, allocation=0.05
    )[0]
    implied_baseline = result.new_portfolio_vol - result.vol_change

    w = (weights / weights.sum()).to_numpy()
    joint_window = violent.loc[tail]
    same_window = math.sqrt(w @ ewma_cov(joint_window).to_numpy() @ w)
    full_history = math.sqrt(w @ ewma_cov(violent).to_numpy() @ w)

    assert implied_baseline == pytest.approx(same_window)
    assert implied_baseline != pytest.approx(full_history, rel=1e-9)


def test_a_candidate_with_too_little_overlap_is_skipped(returns, weights, benchmark, dates, rng):
    stub = pd.Series(np.nan, index=dates, name="IPO")
    stub.iloc[-20:] = rng.normal(0.0, 0.01, 20)

    assert suggest.evaluate_additions(returns, weights, stub.to_frame(), benchmark) == []


def test_allocation_size_scales_the_effect(returns, weights, benchmark, dates, rng):
    hedge = pd.DataFrame({"TLT": rng.normal(0.0, 0.004, len(dates))}, index=dates)

    small = suggest.evaluate_additions(returns, weights, hedge, benchmark, allocation=0.02)[0]
    large = suggest.evaluate_additions(returns, weights, hedge, benchmark, allocation=0.20)[0]

    assert large.vol_change < small.vol_change


def test_empty_weights_are_refused(returns, benchmark, dates, rng):
    zeros = pd.Series(0.0, index=returns.columns)
    hedge = pd.DataFrame({"TLT": rng.normal(0.0, 0.004, len(dates))}, index=dates)

    with pytest.raises(ValueError, match="weights sum to zero"):
        suggest.trim_candidates(returns, zeros)
    with pytest.raises(ValueError, match="weights sum to zero"):
        suggest.evaluate_additions(returns, zeros, hedge, benchmark)


def test_output_is_strict_json(returns, weights, benchmark, dates, rng):
    hedge = pd.DataFrame({"TLT": rng.normal(0.0, 0.004, len(dates))}, index=dates)
    payload = {
        "redundant": [p.as_dict() for p in suggest.redundant_pairs(returns, weights)],
        "trim": [c.as_dict() for c in suggest.trim_candidates(returns, weights)],
        "add": [
            a.as_dict() for a in suggest.evaluate_additions(returns, weights, hedge, benchmark)
        ],
    }
    decoded = json.loads(json.dumps(payload, allow_nan=False))
    assert decoded["trim"]
