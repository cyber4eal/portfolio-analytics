from __future__ import annotations

import json

import numpy as np
import pytest

from portfolio_analytics import montecarlo


START = 15_400.0


def test_fan_starts_at_the_known_value_and_percentiles_are_ordered():
    result = montecarlo.simulate(START, 0.08, 0.22, seed=1, n_paths=4_000)

    first = result.fan[0]
    assert first.month == 0
    assert first.p05 == first.median == first.p95 == START

    for band in result.fan:
        assert band.p05 <= band.p25 <= band.median <= band.p75 <= band.p95


def test_fan_and_boxes_agree_at_a_shared_horizon():
    """The spreadsheet ran two simulations and could disagree with itself at
    the same horizon. One pass means the 12-month band is the 1-year box."""
    result = montecarlo.simulate(
        START, 0.08, 0.22, seed=7, n_paths=8_000, fan_months=60, fan_step=12
    )
    band = next(b for b in result.fan if b.month == 12)
    box = next(b for b in result.boxes if b.months == 12)

    assert band.median == pytest.approx(box.median)
    assert band.p05 == pytest.approx(box.p05)
    assert band.p95 == pytest.approx(box.p95)


def test_median_carries_the_variance_drag():
    """Median grows at mu - sigma^2/2, not mu, so the mean beats the median."""
    result = montecarlo.simulate(START, 0.08, 0.30, seed=3, n_paths=40_000)
    one_year = next(b for b in result.boxes if b.months == 12)

    assert one_year.median < one_year.mean
    expected = START * np.exp(0.08 - 0.5 * 0.30**2)
    assert one_year.median == pytest.approx(expected, rel=0.02)


def test_higher_volatility_raises_the_probability_of_loss():
    calm = montecarlo.simulate(START, 0.08, 0.10, seed=5, n_paths=20_000)
    wild = montecarlo.simulate(START, 0.08, 0.35, seed=5, n_paths=20_000)

    assert wild.boxes[0].prob_loss > calm.boxes[0].prob_loss


def test_bootstrap_engine_reproduces_the_supplied_return_distribution():
    rng = np.random.default_rng(11)
    daily = rng.normal(0.0006, 0.012, 1_500)
    result = montecarlo.simulate(
        START, 0.08, 0.22, engine="bootstrap", daily_returns=daily, seed=2, n_paths=8_000
    )

    assert result.engine == "bootstrap"
    realised_sigma = float(np.std(daily, ddof=1) * np.sqrt(montecarlo.TRADING_DAYS))
    one_year = next(b for b in result.boxes if b.months == 12)
    spread = np.log(one_year.p95 / one_year.p05) / (2 * 1.6448536269514722)
    assert spread == pytest.approx(realised_sigma, rel=0.15)


def test_bootstrap_without_returns_is_refused():
    with pytest.raises(ValueError, match="requires daily_returns"):
        montecarlo.simulate(START, 0.08, 0.22, engine="bootstrap")


def test_bootstrap_refuses_a_history_too_short_to_block_sample():
    with pytest.raises(ValueError, match="at least"):
        montecarlo.simulate(
            START, 0.08, 0.22, engine="bootstrap", daily_returns=[0.01] * 10
        )


@pytest.mark.parametrize(
    "kwargs", [{"start_value": 0.0}, {"sigma_annual": 0.0}, {"sigma_annual": -0.1}]
)
def test_degenerate_inputs_are_refused(kwargs):
    args = {"start_value": START, "mu_annual": 0.08, "sigma_annual": 0.22, **kwargs}
    with pytest.raises(ValueError):
        montecarlo.simulate(**args)


def test_seed_makes_the_result_reproducible():
    a = montecarlo.simulate(START, 0.08, 0.22, seed=42, n_paths=2_000)
    b = montecarlo.simulate(START, 0.08, 0.22, seed=42, n_paths=2_000)
    assert a.as_dict() == b.as_dict()


def test_as_dict_is_strict_json():
    """The frontend calls JSON.parse, which rejects a bare NaN or Infinity."""
    result = montecarlo.simulate(START, 0.08, 0.22, seed=1, n_paths=2_000)
    encoded = json.dumps(result.as_dict(), allow_nan=False)
    assert json.loads(encoded)["fan"][0]["median"] == START
