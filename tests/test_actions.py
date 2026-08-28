from __future__ import annotations

from datetime import date

import pytest

from fundengine import actions

TODAY = date(2026, 8, 28)          # a Friday

PLAN = {"thisMonth": {
    "sells": [{"ticker": "IEMG", "euros": 2_683.0, "shares": 38, "currentPct": 18.4,
               "targetPct": 0.0, "vsAverage200": 10.2},
              {"ticker": "TSLA", "euros": 1_493.0, "shares": 5, "currentPct": 10.2,
               "targetPct": 0.0, "vsAverage200": -12.9}],
    "buys": [{"ticker": "CSPX.AS", "euros": 3_651.0, "shares": 5, "currentPct": 0.0,
              "targetPct": 25.0, "vsAverage200": 9.6}],
}}
PRICES = {"IEMG": 70.72, "TSLA": 300.20, "CSPX.AS": 721.18}
VOLS = {"IEMG": 0.011, "TSLA": 0.030, "CSPX.AS": 0.007}
WEIGHTS = {"IEMG": 18.4, "TSLA": 10.2}


def orders(**kwargs):
    return actions.build(PLAN, PRICES, VOLS, WEIGHTS, growth_gap_pp=4.8,
                         today=TODAY, **kwargs)


def test_a_sell_asks_above_and_a_buy_bids_below():
    by_ticker = {o["ticker"]: o for o in orders()}
    assert by_ticker["IEMG"]["limit"] > PRICES["IEMG"]
    assert by_ticker["CSPX.AS"]["limit"] < PRICES["CSPX.AS"]


def test_the_band_scales_with_the_holdings_own_volatility():
    """The same percentage on a placid ETF and a volatile stock would be a
    rounding error on one and unfillable on the other."""
    by_ticker = {o["ticker"]: o for o in orders()}
    assert by_ticker["TSLA"]["bandPct"] > by_ticker["CSPX.AS"]["bandPct"]
    assert by_ticker["TSLA"]["bandPct"] == pytest.approx(
        100 * VOLS["TSLA"] * actions.LIMIT_SIGMA, abs=0.01)


def test_a_position_past_the_cap_is_urgent_whatever_it_costs():
    by_ticker = {o["ticker"]: o for o in orders(max_name_weight=0.15)}
    assert by_ticker["IEMG"]["overCap"] is True
    assert by_ticker["IEMG"]["urgency"] == "now"
    assert "cap" in by_ticker["IEMG"]["deadlineReason"]


def test_a_limit_expiry_is_not_a_deadline():
    """Regression: treating the five-day limit expiry as a deadline made
    every order urgent, and a list where everything is urgent ranks
    nothing."""
    buy = next(o for o in orders() if o["side"] == "buy")
    assert buy["deadline"] is None          # nothing on the calendar forces it
    assert buy["limitExpires"]              # but the order still expires
    assert buy["urgency"] != "now"


def test_sells_are_dated_by_the_free_sell_reset():
    sell = next(o for o in orders() if o["side"] == "sell")
    assert sell["deadline"] == "2026-08-31"
    assert "carried forward" in sell["deadlineReason"]


def test_the_limit_expiry_skips_weekends():
    """Five working days from a Friday is the following Friday."""
    buy = next(o for o in orders() if o["side"] == "buy")
    assert buy["limitExpires"] == "2026-09-04"
    assert date.fromisoformat(buy["limitExpires"]).weekday() < 5


def test_waiting_costs_more_on_a_larger_trade():
    small = actions.cost_of_waiting(1_000, 4.8)
    large = actions.cost_of_waiting(10_000, 4.8)
    assert large == pytest.approx(small * 10)
    assert large == pytest.approx(10_000 * 0.048 / 12)


def test_orders_are_sorted_by_urgency_then_by_what_waiting_costs():
    levels = [actions.URGENCY.index(o["urgency"]) for o in orders()]
    assert levels == sorted(levels)


def test_an_empty_plan_produces_no_orders():
    assert actions.build({"thisMonth": {"sells": [], "buys": []}},
                         PRICES, VOLS, WEIGHTS, 4.8, today=TODAY) == []


def test_a_missing_price_degrades_to_a_market_order():
    out = actions.build(PLAN, {}, VOLS, WEIGHTS, 4.8, today=TODAY)
    assert all(o["limit"] is None for o in out)


# ---------------- the calendar ----------------

def test_only_real_dates_are_listed():
    rows = actions.calendar_deadlines(today=TODAY, goal_date="2027-02-01")
    what = [r["what"] for r in rows]

    assert "Free sell allowance resets" in what
    assert "Pension contribution year" in what
    assert "Mortgage deposit needed" in what
    assert len(rows) == 3          # short on purpose


def test_the_calendar_is_sorted_by_urgency():
    rows = actions.calendar_deadlines(today=TODAY, goal_date="2027-02-01")
    assert [r["days"] for r in rows] == sorted(r["days"] for r in rows)
    assert rows[0]["days"] == 3    # month end


def test_a_bad_goal_date_is_skipped_not_crashed_on():
    rows = actions.calendar_deadlines(today=TODAY, goal_date="not-a-date")
    assert all(r["what"] != "Mortgage deposit needed" for r in rows)
