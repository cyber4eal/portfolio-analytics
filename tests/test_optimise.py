from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fundengine import optimise


@pytest.fixture
def market(rng, dates):
    """Three assets on one factor plus a hedge that leans against it.

    The book assets are deliberately volatile: hedging only pays when the
    variance it removes is worth more than the expected return it gives up,
    and on a placid book the honest answer is not to hedge at all.
    """
    factor = rng.normal(0.0004, 0.014, len(dates))
    frame = pd.DataFrame({
        "BETA_HIGH": factor * 1.6 + rng.normal(0, 0.022, len(dates)),
        "BETA_MID": factor * 1.0 + rng.normal(0, 0.012, len(dates)),
        "WILD": factor * 0.9 + rng.normal(0, 0.045, len(dates)),   # much idiosyncratic risk
        "HEDGE": factor * -0.3 + rng.normal(0, 0.006, len(dates)),
    }, index=dates)
    return frame, pd.Series(factor, index=dates, name="BENCH")


def test_projection_respects_the_constraints():
    raw = np.array([0.9, -0.4, 0.7, 0.2])
    w = optimise._project_to_simplex(raw)
    assert w.min() >= 0
    assert w.sum() == pytest.approx(1.0)


def test_projection_respects_a_cap():
    """Regression: the excess from a capped weight was only handed to slots
    that already held something. When the projection put everything on one
    name the others were exactly zero, so there was nothing to receive it and
    the result summed to the cap - an accidental 70% cash position."""
    w = optimise._project_to_simplex(np.array([5.0, 0.1, 0.1, 0.1]), cap=0.3)
    assert w.max() <= 0.3 + 1e-9
    assert w.sum() == pytest.approx(1.0, abs=1e-6)

    concentrated = optimise._project_to_simplex(np.array([1.0, 0.0, 0.0, 0.0]), cap=0.25)
    assert concentrated.sum() == pytest.approx(1.0, abs=1e-6)
    assert concentrated.max() <= 0.25 + 1e-9


def test_an_impossible_cap_falls_back_to_equal_weight():
    """Four names cannot be held at 20% each and still sum to one."""
    w = optimise._project_to_simplex(np.array([1.0, 0.0, 0.0]), cap=0.2)
    assert w.sum() == pytest.approx(1.0)
    assert w == pytest.approx(np.full(3, 1 / 3))


def test_shrinkage_pulls_correlations_toward_their_average():
    sample = np.array([[0.04, 0.038, 0.000],
                       [0.038, 0.04, 0.000],
                       [0.000, 0.000, 0.04]])
    shrunk = optimise.shrink_covariance(sample, intensity=0.5)

    # Variances survive; it is the correlations that are noisy.
    assert np.allclose(np.diag(shrunk), np.diag(sample))
    assert shrunk[0, 1] < sample[0, 1]      # the extreme pair is pulled in
    assert shrunk[0, 2] > sample[0, 2]      # the zero pair is pulled up
    assert np.all(np.linalg.eigvalsh(shrunk) > 0)


def test_capm_pays_more_for_more_beta(market):
    frame, benchmark = market
    mu = optimise.capm_returns(frame, benchmark)
    assert mu["BETA_HIGH"] > mu["BETA_MID"] > mu["HEDGE"]
    assert mu["BETA_MID"] == pytest.approx(
        optimise.RISK_FREE + optimise.EQUITY_RISK_PREMIUM, abs=0.01)


def test_growth_optimal_beats_the_starting_book_on_growth(market):
    frame, benchmark = market
    weights = pd.Series([0.4, 0.1, 0.4, 0.1], index=frame.columns)
    result = optimise.build(frame, weights, benchmark, cap=0.5)

    assert result["theories"]["growth"]["growth"] >= result["theories"]["current"]["growth"]
    assert result["theories"]["growth"]["growthGain"] >= 0


def test_each_theory_wins_on_its_own_measure(market):
    frame, benchmark = market
    weights = pd.Series(0.25, index=frame.columns)
    theories = optimise.build(frame, weights, benchmark, cap=0.6)["theories"]

    assert theories["minvar"]["vol"] <= min(t["vol"] for t in theories.values()) + 0.15
    assert theories["sharpe"]["sharpe"] >= max(
        t["sharpe"] for k, t in theories.items() if k != "sharpe") - 0.05
    assert theories["growth"]["growth"] >= max(
        t["growth"] for k, t in theories.items() if k != "growth") - 0.05


def test_growth_is_return_minus_half_variance(market):
    """The identity the whole tab rests on."""
    frame, benchmark = market
    weights = pd.Series(0.25, index=frame.columns)
    for block in optimise.build(frame, weights, benchmark)["theories"].values():
        implied = block["expectedReturn"] - (block["vol"] / 100) ** 2 / 2 * 100
        assert implied == pytest.approx(block["growth"], abs=0.02)


def test_a_volatile_asset_with_no_extra_expected_return_is_avoided(market):
    """WILD carries the same beta as BETA_MID and far more of its own noise,
    so under CAPM it earns nothing for that risk and should be dropped."""
    frame, benchmark = market
    weights = pd.Series(0.25, index=frame.columns)
    growth = optimise.build(frame, weights, benchmark, cap=0.6)["theories"]["growth"]
    assert growth["weights"].get("WILD", 0) < growth["weights"].get("BETA_MID", 0)


def test_every_theory_is_long_only_and_fully_invested(market):
    frame, benchmark = market
    weights = pd.Series(0.25, index=frame.columns)
    for block in optimise.build(frame, weights, benchmark, cap=0.4)["theories"].values():
        assert all(w >= 0 for w in block["weights"].values())
        assert sum(block["weights"].values()) == pytest.approx(100, abs=1.5)
        assert max(block["weights"].values()) <= 40.5


def test_risk_parity_equalises_risk_not_weight():
    sigma = np.diag([0.04, 0.01, 0.0025])       # very different volatilities
    w = optimise.risk_parity(sigma)
    contributions = w * (sigma @ w)
    assert contributions.max() / contributions.min() < 1.15
    assert w[0] < w[2]                          # least of the riskiest


def test_stress_correlation_exposes_a_hedge_that_fails_in_a_selloff(rng, dates):
    """The point of the column: an asset can look diversifying on average and
    still converge exactly when the book is falling."""
    book = pd.Series(rng.normal(0.0005, 0.014, len(dates)), index=dates)
    bad = book < book.quantile(0.1)

    # Uncorrelated day to day, but dumps whenever the book does.
    fake = pd.Series(rng.normal(0.0, 0.010, len(dates)), index=dates)
    fake[bad] = book[bad] * 1.3
    # Genuinely independent throughout.
    real = pd.Series(rng.normal(0.0, 0.010, len(dates)), index=dates)

    rows = {r["ticker"]: r for r in optimise.stress_correlation(
        pd.DataFrame({"FAKE": fake, "REAL": real}), book)}

    assert rows["FAKE"]["stressCorrelation"] > 0.7
    assert rows["FAKE"]["deterioration"] > 0.3
    assert abs(rows["REAL"]["stressCorrelation"]) < 0.4


def test_hedges_are_ranked_by_compounding_not_by_variance_removed(market):
    frame, benchmark = market
    holdings = frame[["BETA_HIGH", "BETA_MID", "WILD"]]
    weights = pd.Series([0.4, 0.3, 0.3], index=holdings.columns)
    portfolio = pd.Series(holdings.to_numpy() @ weights.to_numpy(), index=frame.index)

    rows = optimise.hedges(holdings, weights, frame[["HEDGE"]], benchmark, portfolio)
    assert rows and rows[0]["ticker"] == "HEDGE"
    # On a volatile book the variance saved outweighs the return given up.
    assert rows[0]["growthGain"] > 0
    assert rows[0]["volChange"] < 0
    assert 0 < rows[0]["optimalWeightPct"] <= 40
    # It costs expected return - a hedge that did not would be a free lunch.
    assert rows[0]["returnChange"] < 0


# ---------------- trend, plan and tax ----------------

def _trending(dates, drift):
    import numpy as np
    return pd.Series(100 * np.exp(np.linspace(0, drift, len(dates))), index=dates)


def test_momentum_skips_the_most_recent_month(dates):
    """The final month tends to reverse, so including it turns a momentum
    signal into a short-term-reversal one pointing the wrong way."""
    rising = _trending(dates, 0.4)
    rising.iloc[-15:] *= 0.75                      # sharp recent drop
    signals = optimise.trend_signals(rising.to_frame("X"))["X"]

    assert signals["momentum12_1"] > 0             # the year was still good
    assert signals["lastMonth"] < 0                # the month was not
    assert signals["belowHigh52"] < 0


def test_trend_flags_a_name_under_its_average(dates):
    falling = _trending(dates, -0.3)
    assert optimise.trend_signals(falling.to_frame("X"))["X"]["aboveTrend"] is False
    rising = _trending(dates, 0.3)
    assert optimise.trend_signals(rising.to_frame("X"))["X"]["aboveTrend"] is True


def test_a_short_history_is_skipped_rather_than_guessed(dates):
    short = _trending(dates[:100], 0.2)
    assert optimise.trend_signals(short.to_frame("X")) == {}


def test_the_plan_closes_the_gap_in_the_right_direction():
    plan = optimise.rebalance_plan(
        current={"OLD": 60.0, "KEEP": 40.0},
        target={"KEEP": 40.0, "NEW": 60.0},
        total_value=10_000,
        trends={"OLD": {"vsAverage200": 12.0}, "NEW": {"vsAverage200": -3.0}},
    )
    sells = {t["ticker"]: t for t in plan["sells"]}
    buys = {t["ticker"]: t for t in plan["buys"]}

    assert sells["OLD"]["euros"] == pytest.approx(6_000)
    assert buys["NEW"]["euros"] == pytest.approx(6_000)
    assert "KEEP" not in sells and "KEEP" not in buys      # already on target


def test_trades_below_the_minimum_are_not_worth_making():
    plan = optimise.rebalance_plan(
        current={"A": 50.0, "B": 50.0}, target={"A": 50.2, "B": 49.8},
        total_value=10_000, trends={})
    assert plan["sells"] == [] and plan["buys"] == []


def test_the_month_respects_cash_and_the_free_sell_allowance():
    current = {f"S{i}": 10.0 for i in range(8)}
    current["KEEP"] = 20.0
    target = {"NEW": 80.0, "KEEP": 20.0}
    plan = optimise.rebalance_plan(current, target, total_value=20_000, trends={},
                                   monthly_cash=400, free_sells=3)
    month = plan["thisMonth"]

    assert month["freeSellsUsed"] == 3
    assert len(month["sells"]) == 3
    # Spending cannot exceed what was raised plus the new cash.
    assert month["spent"] <= month["raised"] + month["newCash"] + 0.01


def test_trend_orders_comparable_trades_without_changing_them():
    """Among similar-sized trades, sell what is stretched and buy what is
    not. The set of trades must be identical either way."""
    current = {"HOT": 30.0, "COLD": 30.0, "KEEP": 40.0}
    target = {"KEEP": 100.0}
    plan = optimise.rebalance_plan(
        current, target, total_value=10_000,
        trends={"HOT": {"vsAverage200": 25.0}, "COLD": {"vsAverage200": -8.0}},
        free_sells=5)

    assert [t["ticker"] for t in plan["sells"]][:2] == ["HOT", "COLD"]
    assert sum(t["euros"] for t in plan["sells"]) == pytest.approx(6_000)


def test_tax_uses_the_imported_basis_and_the_exemption():
    sells = [{"ticker": "NVDA", "euros": 1000.0}]
    holdings = [{"ticker": "NVDA", "tradable": True, "shares": 10, "value_eur": 2000.0}]
    basis = {"NVDA": {"avg_cost": 100.0}}

    tax = optimise.tax_on_plan(sells, holdings, basis)
    # Selling half the position: 5 shares, cost 100, now 200 -> 500 of gain.
    assert tax["gain"] == pytest.approx(500)
    assert tax["taxable"] == 0                     # inside the exemption
    assert tax["tax"] == 0


def test_a_large_gain_is_taxed_above_the_exemption():
    sells = [{"ticker": "NVDA", "euros": 10_000.0}]
    holdings = [{"ticker": "NVDA", "tradable": True, "shares": 100, "value_eur": 10_000.0}]
    tax = optimise.tax_on_plan(sells, holdings, {"NVDA": {"avg_cost": 20.0}})

    assert tax["gain"] == pytest.approx(8_000)
    assert tax["taxable"] == pytest.approx(8_000 - optimise.CGT_ANNUAL_EXEMPTION)
    assert tax["tax"] == pytest.approx(tax["taxable"] * 0.33)


def test_a_position_with_no_basis_is_reported_not_assumed_free():
    """Treating an unknown basis as zero would price the whole proceeds as
    gain and overstate the bill."""
    sells = [{"ticker": "RIVN", "euros": 500.0}]
    holdings = [{"ticker": "RIVN", "tradable": True, "shares": 33, "value_eur": 500.0}]
    tax = optimise.tax_on_plan(sells, holdings, {})

    assert tax["gain"] == 0
    assert [r["ticker"] for r in tax["unknownBasis"]] == ["RIVN"]


# ---------------- fees ----------------

def test_charges_are_subtracted_from_the_forward_estimate(market):
    """A CAPM estimate knows beta and nothing else, so without this an
    expensive fund is credited with the same net return as a cheap one at
    the same beta - and a directly-held share with the same again."""
    frame, benchmark = market
    gross = optimise.capm_returns(frame, benchmark)
    net = optimise.capm_returns(frame, benchmark, costs={"BETA_MID": 0.0065})

    assert net["BETA_MID"] == pytest.approx(gross["BETA_MID"] - 0.0065)
    assert net["BETA_HIGH"] == pytest.approx(gross["BETA_HIGH"])


def test_the_cheaper_of_two_identical_funds_wins(rng, dates):
    """Same exposure, different charge: the optimiser must prefer the cheap
    one, which is the whole point of netting the fee."""
    factor = rng.normal(0.0004, 0.012, len(dates))
    frame = pd.DataFrame({
        "CHEAP": factor + rng.normal(0, 0.001, len(dates)),
        "PRICEY": factor + rng.normal(0, 0.001, len(dates)),
    }, index=dates)
    benchmark = pd.Series(factor, index=dates)
    weights = pd.Series([0.5, 0.5], index=frame.columns)

    result = optimise.build(frame, weights, benchmark, cap=1.0,
                            costs={"CHEAP": 0.0007, "PRICEY": 0.0065})
    growth = result["theories"]["growth"]["weights"]
    assert growth.get("CHEAP", 0) > growth.get("PRICEY", 0)


def test_the_blended_charge_is_reported(market):
    frame, benchmark = market
    weights = pd.Series(0.25, index=frame.columns)
    costs = {t: 0.002 for t in frame.columns}
    theories = optimise.build(frame, weights, benchmark, costs=costs)["theories"]
    for block in theories.values():
        assert block["fee"] == pytest.approx(0.2, abs=0.01)     # 0.2% of the mix


def test_a_minimum_commission_punishes_small_trades():
    """The total looks tolerable; the small trades inside it do not."""
    trades = [{"ticker": "BIG", "action": "buy", "euros": 5000.0},
              {"ticker": "SMALL", "action": "buy", "euros": 356.0}]
    holdings = [{"ticker": "BIG", "currency": "EUR"},
                {"ticker": "SMALL", "currency": "EUR"}]
    cost = optimise.trading_cost(trades, holdings)

    by_ticker = {r["ticker"]: r for r in cost["trades"]}
    assert by_ticker["SMALL"]["cost"] == pytest.approx(14.99)   # the floor bites
    assert by_ticker["SMALL"]["costPct"] > 4
    assert by_ticker["BIG"]["costPct"] < 1
    assert cost["worst"]["ticker"] == "SMALL"


def test_currency_conversion_is_only_charged_on_non_euro():
    trades = [{"ticker": "USD_LINE", "action": "sell", "euros": 10_000.0},
              {"ticker": "EUR_LINE", "action": "sell", "euros": 10_000.0}]
    holdings = [{"ticker": "USD_LINE", "currency": "USD"},
                {"ticker": "EUR_LINE", "currency": "EUR"}]
    by_ticker = {r["ticker"]: r for r in
                 optimise.trading_cost(trades, holdings)["trades"]}

    assert by_ticker["USD_LINE"]["cost"] > by_ticker["EUR_LINE"]["cost"]
    assert by_ticker["USD_LINE"]["cost"] - by_ticker["EUR_LINE"]["cost"] == pytest.approx(15.0)
