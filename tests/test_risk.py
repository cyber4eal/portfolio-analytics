from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from portfolio_analytics import risk

VALUE = 15_400.0


def test_component_var_sums_to_total_var(returns, weights):
    """The property that makes component VaR actionable: the parts add up,
    so 'this position is 55% of my risk' is a real statement."""
    report = risk.compute_risk(returns, weights, VALUE)

    assert sum(p.component_var for p in report.positions) == pytest.approx(
        report.var_95_1d
    )
    assert sum(p.pct_of_var for p in report.positions) == pytest.approx(1.0)


def test_positions_are_ranked_by_risk_contribution(returns, weights):
    report = risk.compute_risk(returns, weights, VALUE)
    contributions = [p.component_var for p in report.positions]
    assert contributions == sorted(contributions, reverse=True)


def test_var_scales_with_portfolio_value(returns, weights):
    small = risk.compute_risk(returns, weights, 1_000.0)
    big = risk.compute_risk(returns, weights, 10_000.0)

    assert big.var_95_1d == pytest.approx(small.var_95_1d * 10)
    assert big.vol_annual == pytest.approx(small.vol_annual)


def test_monthly_var_is_the_daily_number_scaled_by_root_time(returns, weights):
    report = risk.compute_risk(returns, weights, VALUE)
    assert report.var_95_1m == pytest.approx(report.var_95_1d * math.sqrt(21))


def test_cvar_is_worse_than_var(returns, weights):
    """CVaR averages the tail beyond VaR, so it cannot be the smaller number."""
    report = risk.compute_risk(returns, weights, VALUE)
    assert report.cvar_95_1d > report.var_95_1d_historical


def test_effective_holdings_penalises_concentration():
    equal = np.array([0.25, 0.25, 0.25, 0.25])
    lopsided = np.array([0.85, 0.05, 0.05, 0.05])

    assert risk.effective_holdings(equal) == pytest.approx(4.0)
    assert risk.effective_holdings(lopsided) < 1.5


def test_ewma_weights_recent_observations_more_heavily(dates, rng):
    """A late volatility spike must move EWMA vol more than equal weighting."""
    quiet_then_loud = np.concatenate(
        [rng.normal(0, 0.004, 400), rng.normal(0, 0.030, 100)]
    )
    frame = pd.DataFrame({"X": quiet_then_loud}, index=dates)

    ewma_vol = math.sqrt(risk.ewma_cov(frame, lam=0.94).iloc[0, 0])
    equal_vol = float(frame["X"].std(ddof=1) * math.sqrt(risk.TRADING_DAYS))

    assert ewma_vol > equal_vol


def test_beta_against_a_scaled_copy_of_the_benchmark(dates, benchmark):
    """A portfolio that is exactly 1.5x the benchmark has beta 1.5."""
    single = pd.DataFrame({"LEV": benchmark * 1.5}, index=dates)
    report = risk.compute_risk(
        single, pd.Series([1.0], index=["LEV"]), VALUE, benchmark=benchmark
    )
    assert report.beta == pytest.approx(1.5, rel=1e-6)


def test_beta_is_zero_without_a_benchmark(returns, weights):
    assert risk.compute_risk(returns, weights, VALUE).beta == 0.0


def test_a_gap_in_one_series_does_not_poison_every_number(gappy_returns, weights):
    """Regression: a Xetra holding with no print on US holidays used to make
    vol, VaR, sharpe and every position nan."""
    report = risk.compute_risk(gappy_returns, weights, VALUE)
    numbers = [v for v in report.as_dict().values() if isinstance(v, float)]

    assert not any(math.isnan(v) for v in numbers)
    assert report.vol_annual > 0


def test_alignment_keeps_only_dates_where_every_series_printed(gappy_returns):
    aligned = risk.align_returns(gappy_returns)

    assert not aligned.isna().any().any()
    assert len(aligned) < len(gappy_returns)
    assert aligned.index.isin(gappy_returns.index).all()


def test_alignment_refuses_a_sample_too_thin_to_estimate(returns):
    with pytest.raises(ValueError, match="need at least"):
        risk.align_returns(returns.head(30))


def test_weights_are_normalised_not_taken_literally(returns):
    """Weights may arrive as euro amounts rather than fractions."""
    fractions = pd.Series([0.4, 0.3, 0.2, 0.1], index=returns.columns)
    euros = fractions * 15_400

    assert risk.compute_risk(returns, euros, VALUE).vol_annual == pytest.approx(
        risk.compute_risk(returns, fractions, VALUE).vol_annual
    )


def test_empty_weights_are_refused(returns):
    zeros = pd.Series(0.0, index=returns.columns)
    with pytest.raises(ValueError, match="weights sum to zero"):
        risk.compute_risk(returns, zeros, VALUE)


def test_report_is_strict_json(returns, weights, benchmark):
    report = risk.compute_risk(returns, weights, VALUE, benchmark=benchmark)
    decoded = json.loads(json.dumps(report.as_dict(), allow_nan=False))
    assert len(decoded["positions"]) == len(returns.columns)
