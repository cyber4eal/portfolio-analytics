from __future__ import annotations

import pytest

from fundengine import advice, pension


# ---------------- pension ----------------

def test_holdings_and_contributions_round_trip(tmp_path):
    store = tmp_path / "pension.json"
    pension.set_holdings([
        {"name": "Irish Life Indexed World Equity", "value_eur": 12_500, "units": 1840.2},
        {"name": "Self-directed all-world", "ticker": "vwce.de", "value_eur": 4_200},
    ], store)
    pension.add_contribution({"date": "2026-07-31", "amount_eur": 450}, store)
    pension.add_contribution({"date": "2026-07-31", "amount_eur": 300,
                              "source": "employer"}, store)
    summary = pension.summary(path=store)

    assert summary["total"] == pytest.approx(16_700)
    assert summary["paidIn"] == pytest.approx(750)
    assert summary["bySource"] == {"employee": 450.0, "employer": 300.0}
    assert summary["pricedCount"] == 1 and summary["unpricedCount"] == 1


def test_a_line_without_a_ticker_is_carried_but_not_priced(tmp_path):
    """Most provider funds have no market listing. They must count towards
    the pot and stay out of anything that needs a return series."""
    store = tmp_path / "pension.json"
    pension.set_holdings([
        {"name": "Zurich Prisma 4", "value_eur": 9_000},
        {"name": "All-world", "ticker": "VWCE.DE", "value_eur": 1_000},
    ], store)
    rows = pension.as_holdings(path=store)

    assert [r.tradable for r in rows] == [False, True]
    assert sum(r.value_eur for r in rows) == pytest.approx(10_000)
    assert all(r.portfolio == pension.BOOK for r in rows)


def test_setting_holdings_replaces_rather_than_appends(tmp_path):
    """A statement is a whole picture, not a delta."""
    store = tmp_path / "pension.json"
    pension.set_holdings([{"name": "A", "value_eur": 100}], store)
    pension.set_holdings([{"name": "B", "value_eur": 200}], store)
    summary = pension.summary(path=store)

    assert len(summary["holdings"]) == 1
    assert summary["total"] == pytest.approx(200)


def test_contributions_survive_a_holdings_update(tmp_path):
    store = tmp_path / "pension.json"
    pension.add_contribution({"date": "2026-01-31", "amount_eur": 100}, store)
    pension.set_holdings([{"name": "A", "value_eur": 500}], store)
    assert pension.summary(path=store)["paidIn"] == pytest.approx(100)


@pytest.mark.parametrize("row,message", [
    ({"date": "2026-01-01", "amount_eur": 0}, "positive"),
    ({"date": "2026-01-01", "amount_eur": -5}, "positive"),
    ({"date": "01-01-2026", "amount_eur": 10}, "YYYY-MM-DD"),
    ({"date": "2026-01-01", "amount_eur": 10, "source": "bonus"}, "source"),
])
def test_bad_contributions_are_refused(tmp_path, row, message):
    with pytest.raises(ValueError, match=message):
        pension.add_contribution(row, tmp_path / "p.json")


def test_a_holding_needs_a_name_and_a_sane_value(tmp_path):
    with pytest.raises(ValueError, match="name"):
        pension.set_holdings([{"value_eur": 100}], tmp_path / "p.json")
    with pytest.raises(ValueError, match="negative"):
        pension.set_holdings([{"name": "A", "value_eur": -1}], tmp_path / "p.json")


# ---------------- sizing ----------------

def test_conviction_scales_the_buy():
    low = advice.size_position("buy", 3, price=100, current_weight=0.0)
    high = advice.size_position("buy", 9, price=100, current_weight=0.0)
    assert high["euros"] > low["euros"]


def test_headroom_damps_a_buy_into_a_crowded_name():
    """Conviction must not talk you into pouring more into something already
    at the single-name cap."""
    room = advice.size_position("buy", 8, price=100, current_weight=0.02)
    crowded = advice.size_position("buy", 8, price=100, current_weight=0.14)

    assert crowded["euros"] < room["euros"]
    assert "flag" in crowded
    assert advice.size_position("buy", 10, price=100,
                                current_weight=advice.MAX_NAME_WEIGHT)["euros"] == 0


def test_speculative_is_sized_at_half():
    normal = advice.size_position("buy", 8, price=100, current_weight=0.0)
    spec = advice.size_position("buy", 8, price=100, current_weight=0.0, speculative=True)
    assert spec["euros"] == pytest.approx(normal["euros"] / 2, abs=25)


def test_a_sell_is_bounded_by_the_position_not_by_cash():
    out = advice.size_position("sell", 7, price=1.95, position_value=118.91,
                               position_shares=71)
    assert out["euros"] == pytest.approx(118.91 * 0.7, abs=0.01)
    assert out["shares"] == 49
    assert out["euros"] <= 118.91


def test_full_conviction_exits_the_whole_position():
    out = advice.size_position("sell", 10, price=10, position_value=500, position_shares=50)
    assert out["trimFraction"] == 1.0
    assert out["euros"] == pytest.approx(500)


# ---------------- candidates ----------------

def test_a_position_costing_more_risk_than_its_weight_is_flagged():
    contributions = [
        {"ticker": "APLD", "weight": 7.2, "riskShare": 16.6, "vol": 92.3},
        {"ticker": "AMZN", "weight": 12.0, "riskShare": 9.0, "vol": 30.0},
    ]
    holdings = [{"ticker": "APLD", "name": "Applied Digital", "value_eur": 1000, "tradable": True},
                {"ticker": "AMZN", "name": "Amazon", "value_eur": 1700, "tradable": True}]
    flags = advice.sell_candidates(contributions, {"tickers": [], "matrix": []},
                                   holdings, 14_000)

    assert [f["ticker"] for f in flags] == ["APLD"]
    assert "risk" in flags[0]["reasons"][0]["reason"].lower()


def test_the_smaller_half_of_a_correlated_pair_is_the_one_flagged():
    correlations = {"tickers": ["AMZN", "NVDA"], "matrix": [[1.0, 0.93], [0.93, 1.0]]}
    holdings = [{"ticker": "AMZN", "name": "Amazon", "value_eur": 1700, "tradable": True},
                {"ticker": "NVDA", "name": "Nvidia", "value_eur": 400, "tradable": True}]
    flags = advice.sell_candidates([], correlations, holdings, 2100)

    assert [f["ticker"] for f in flags] == ["NVDA"]


def test_an_uncorrelated_pair_is_left_alone():
    correlations = {"tickers": ["A", "B"], "matrix": [[1.0, 0.2], [0.2, 1.0]]}
    holdings = [{"ticker": "A", "value_eur": 100, "tradable": True, "name": "A"},
                {"ticker": "B", "value_eur": 100, "tradable": True, "name": "B"}]
    assert advice.sell_candidates([], correlations, holdings, 200) == []


def test_concentration_notes_fire_on_the_real_thresholds():
    exposure = {"countries": {"United States of America": 78.0},
                "currencies": {"EUR": 6.0, "USD": 94.0},
                "sectors": {"Technology": 41.0}}
    notes = " ".join(advice.concentration_notes(exposure, 6.0, 20))

    assert "United States" in notes
    assert "unhedged" in notes
    assert "technology" in notes.lower()


def test_a_balanced_book_gets_no_notes():
    exposure = {"countries": {"United States of America": 40.0, "Japan": 30.0},
                "currencies": {"EUR": 60.0, "USD": 40.0},
                "sectors": {"Technology": 20.0}}
    assert advice.concentration_notes(exposure, 9.0, 10) == []


# ---------------- accrual and contribution rate ----------------

def test_the_pot_accrues_the_months_since_the_last_statement(tmp_path):
    """A statement is only fresh on the day it is downloaded. Between them
    the pot would otherwise drift below reality by a month's pay a month."""
    from datetime import date

    store = tmp_path / "pension.json"
    pension.set_holdings([{"name": "Fund", "value_eur": 5_000}], store)
    for month in (5, 6, 7):
        pension.add_contribution(
            {"date": f"2026-0{month}-11", "amount_eur": 400}, store)

    accrued = pension.accrue(pension.summary(path=store), as_of=date(2026, 10, 11))

    assert accrued["accruedMonths"] == 3
    assert accrued["accrued"] == pytest.approx(1_200)
    assert accrued["estimatedTotal"] == pytest.approx(6_200)
    assert "estimate" in accrued["accrualNote"]


def test_no_accrual_when_the_statement_is_current(tmp_path):
    from datetime import date

    store = tmp_path / "pension.json"
    pension.set_holdings([{"name": "Fund", "value_eur": 100}], store)
    pension.add_contribution({"date": "2026-08-11", "amount_eur": 400}, store)

    accrued = pension.accrue(pension.summary(path=store), as_of=date(2026, 8, 28))
    assert accrued["accruedMonths"] == 0
    assert accrued["estimatedTotal"] == pytest.approx(100)


def test_a_pinned_rate_beats_the_trailing_average(tmp_path):
    """After a pay rise the trailing average records the old salary."""
    store = tmp_path / "pension.json"
    for month in (5, 6, 7):
        pension.add_contribution(
            {"date": f"2026-0{month}-11", "amount_eur": 300}, store)
    assert pension.recent_monthly(pension.summary(path=store)) == pytest.approx(300)

    pension.set_contribution_override(500, store)
    assert pension.recent_monthly(pension.summary(path=store)) == pytest.approx(500)

    pension.set_contribution_override(None, store)
    assert pension.recent_monthly(pension.summary(path=store)) == pytest.approx(300)


def test_a_negative_contribution_rate_is_refused(tmp_path):
    with pytest.raises(ValueError, match="negative"):
        pension.set_contribution_override(-1, tmp_path / "p.json")


def test_both_halves_of_a_month_count_toward_the_rate(tmp_path):
    """Employer and employee land as separate rows on the same day; the rate
    is what goes in, not what either side puts in."""
    store = tmp_path / "pension.json"
    for month in (6, 7, 8):
        pension.add_contribution({"date": f"2026-0{month}-11", "amount_eur": 100,
                                  "source": "employee"}, store)
        pension.add_contribution({"date": f"2026-0{month}-11", "amount_eur": 300,
                                  "source": "employer"}, store)
    assert pension.recent_monthly(pension.summary(path=store)) == pytest.approx(400)
