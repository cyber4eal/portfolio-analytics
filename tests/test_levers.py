from __future__ import annotations

import pytest

from fundengine import irish_tax, levers


def test_terminal_value_matches_the_closed_form():
    """A pot plus a monthly contribution, compounded monthly."""
    value = levers.terminal(1_000, 100, 0.12, 1)
    monthly = 1.12 ** (1 / 12) - 1
    expected = 1_000 * 1.12 + 100 * sum((1 + monthly) ** i for i in range(12))
    assert value == pytest.approx(expected, rel=1e-9)


def test_contributions_dominate_a_small_pot():
    """The point of the whole module: at this size the money you add is
    worth more than any plausible edge on the money you have."""
    ranked = levers.rank(19_255, 400, 0.08, 10, extra_monthly=200)
    by_name = {l["name"]: l["gain"] for l in ranked["levers"]}

    assert by_name["Save more"] > by_name["Earn more on it"]
    assert by_name["Earn more on it"] > by_name["Pay less"]


def test_returns_eventually_overtake_contributions():
    ranked = levers.rank(19_255, 400, 0.08, 10)
    assert ranked["crossoverYears"] is not None
    # A year of saving is 4,800; 8% of the pot passes that around 60k.
    assert ranked["crossoverValue"] == pytest.approx(60_000, rel=0.15)


def test_a_large_pot_flips_the_ranking():
    """Allocation is not always the smallest term - it becomes the biggest
    one once the pot is large enough."""
    ranked = levers.rank(2_000_000, 400, 0.08, 10, extra_monthly=200)
    by_name = {l["name"]: l["gain"] for l in ranked["levers"]}
    assert by_name["Earn more on it"] > by_name["Save more"]


def test_a_year_in_cash_is_shown_as_a_cost():
    ranked = levers.rank(19_255, 400, 0.08, 10)
    cash = next(l for l in ranked["levers"] if "cash" in l["name"].lower())
    assert cash["gain"] < 0


def test_the_employer_match_is_not_offered_as_a_lever():
    """Both of them already contribute at the level that maxes the match, so
    there is nothing left to take. A list with one impossible item at the
    top devalues the real ones underneath it."""
    names = [l["name"] for l in levers.rank(19_255, 400, 0.08, 10)["levers"]]
    assert not any("match" in name.lower() for name in names)


# ---------------- feasibility ----------------

def test_a_reachable_target_needs_a_sane_return():
    result = levers.feasibility(19_255, 400, 100_000, 10)
    assert 0 < result["requiredPct"] < 15
    assert result["exceedsAll"] is False
    assert "within long-run" in result["verdict"]


def test_an_impossible_target_is_named_as_one():
    """EUR 1bn from EUR 19k in ten years needs roughly tripling every year."""
    result = levers.feasibility(19_255, 400, 1_000_000_000, 10)
    assert result["requiredPct"] > 150
    assert result["exceedsAll"] is True
    assert "beyond any sustained record" in result["verdict"]


def test_the_required_return_actually_reaches_the_target():
    for target in (50_000, 250_000, 5_000_000):
        result = levers.feasibility(19_255, 400, target, 10)
        reached = levers.terminal(19_255, 400, result["requiredReturn"], 10)
        assert reached == pytest.approx(target, rel=1e-3)


# ---------------- Irish wrappers ----------------

def test_deferral_makes_shares_beat_an_etf_over_a_long_hold():
    """The rate gap is five points; the gap that matters is deemed disposal
    taking tax out of the compounding base every eight years."""
    shares = irish_tax.after_tax_share(0.07, 35)
    fund = irish_tax.after_tax_fund(0.07, 35)
    assert shares > fund
    assert (shares - fund) * 100 > 1.0          # over a point a year


def test_the_share_advantage_grows_with_the_horizon():
    short = irish_tax.after_tax_share(0.07, 5) - irish_tax.after_tax_fund(0.07, 5)
    long = irish_tax.after_tax_share(0.07, 35) - irish_tax.after_tax_fund(0.07, 35)
    assert long > short


def test_a_pension_euro_starts_larger_and_ends_larger():
    result = irish_tax.pension_vs_brokerage(600, 20, 0.07, marginal_rate=0.40)
    assert result["grossEquivalent"] == pytest.approx(1_000)
    assert result["pension"] > result["shares"] > result["etf"]


def test_relief_limits_follow_age():
    assert irish_tax.pension_relief_limit(28, 60_000) == pytest.approx(9_000)
    assert irish_tax.pension_relief_limit(35, 60_000) == pytest.approx(12_000)
    # Earnings above the cap do not increase the allowance.
    assert irish_tax.pension_relief_limit(35, 500_000) == pytest.approx(23_000)


def test_tax_free_is_the_default_and_changes_nothing():
    """Tax modelling is opt-in; with no wrapper given the return is untouched."""
    assert irish_tax.after_tax(irish_tax.PENSION, 0.07, 10) == pytest.approx(0.07)
