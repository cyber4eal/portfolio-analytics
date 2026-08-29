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


# ---------------- executable sizes ----------------

POSITIONS = {"IEMG": {"shares": 38, "value_eur": 2_684.0},
             "TSLA": {"shares": 5, "value_eur": 1_505.0}}


def whole(**kwargs):
    return actions.build(PLAN, PRICES, VOLS, WEIGHTS, growth_gap_pp=4.8,
                         today=TODAY, positions=POSITIONS, whole_shares=True,
                         **kwargs)


def test_a_sell_is_the_whole_position():
    """Fractions cannot be traded, so a partial trim is not an order anyone
    can place."""
    sell = next(o for o in whole() if o["ticker"] == "IEMG")
    assert sell["shares"] == 38
    assert sell["euros"] == pytest.approx(2_684.0)


def test_a_buy_is_a_whole_number_of_shares():
    buy = next(o for o in whole() if o["side"] == "buy")
    assert buy["shares"] == int(buy["shares"])
    assert buy["euros"] == pytest.approx(buy["shares"] * PRICES["CSPX.AS"], abs=0.01)
    assert buy["euros"] <= buy["wantedEuros"] + 0.01      # never overspends


def test_the_leftover_cash_is_stated():
    buy = next(o for o in whole() if o["side"] == "buy")
    assert buy["rounding"] and "cash" in buy["rounding"]


def test_a_buy_too_small_for_one_share_is_skipped_not_faked():
    plan = {"thisMonth": {"sells": [], "buys": [
        {"ticker": "CSPX.AS", "euros": 300.0, "currentPct": 0, "targetPct": 5}]}}
    out = actions.build(plan, PRICES, VOLS, {}, 4.8, today=TODAY, whole_shares=True)
    assert out[0]["skipped"] is True
    assert out[0]["euros"] == 0
    assert "does not buy one share" in out[0]["rounding"]


def test_a_big_overshoot_is_flagged_rather_than_slipped_through():
    """Selling the lot when the plan wanted a third of it is a decision, not
    a rounding."""
    plan = {"thisMonth": {"sells": [
        {"ticker": "IEMG", "euros": 800.0, "currentPct": 18.4, "targetPct": 12}], "buys": []}}
    out = actions.build(plan, PRICES, VOLS, WEIGHTS, 4.8, today=TODAY,
                        positions=POSITIONS, whole_shares=True)
    assert "overshoots" in out[0]["rounding"]
    assert out[0]["euros"] == pytest.approx(2_684.0)


def test_skipped_orders_sort_to_the_bottom():
    plan = {"thisMonth": {"sells": [
        {"ticker": "IEMG", "euros": 2_684.0, "currentPct": 18.4, "targetPct": 0}],
        "buys": [{"ticker": "CSPX.AS", "euros": 300.0, "currentPct": 0, "targetPct": 5}]}}
    out = actions.build(plan, PRICES, VOLS, WEIGHTS, 4.8, today=TODAY,
                        positions=POSITIONS, whole_shares=True)
    assert out[-1]["skipped"] is True


# ---------------- broker currency ----------------

def test_a_us_line_carries_the_price_its_broker_quotes():
    """Showing EUR 71.80 for a ticket that reads USD 83.16 is a 16% error on
    the one number that has to be exact."""
    out = actions.build(PLAN, PRICES, VOLS, WEIGHTS, 4.8, today=TODAY,
                        currencies={"IEMG": "USD", "CSPX.AS": "EUR"}, fx=1.1587)
    by_ticker = {o["ticker"]: o for o in out}

    assert by_ticker["IEMG"]["currency"] == "USD"
    assert by_ticker["IEMG"]["nativeLimit"] == pytest.approx(
        by_ticker["IEMG"]["limit"] * 1.1587, abs=0.01)
    assert by_ticker["IEMG"]["nativeLimit"] > by_ticker["IEMG"]["limit"]


def test_a_euro_line_is_left_alone():
    out = actions.build(PLAN, PRICES, VOLS, WEIGHTS, 4.8, today=TODAY,
                        currencies={"CSPX.AS": "EUR"}, fx=1.1587)
    buy = next(o for o in out if o["ticker"] == "CSPX.AS")
    assert buy["nativeLimit"] == pytest.approx(buy["limit"])
    assert buy["fx"] is None


# ---------------- broker routing ----------------

LEDGER = [
    {"ticker": "IEMG", "action": "buy", "shares": 38, "portfolio": "Catalin",
     "note": "Trading 212 buy - market"},
    {"ticker": "AMZN", "action": "buy", "shares": 7, "portfolio": "Catalin",
     "note": "Trading 212 buy - market"},
    {"ticker": "AMZN", "action": "buy", "shares": 1, "portfolio": "Catalin",
     "note": "Davy 25G25767"},
    {"ticker": "TSLA", "action": "buy", "shares": 5, "portfolio": "Catalin",
     "note": "Davy 25H41501"},
]


def test_shares_are_located_from_the_imported_statements():
    from fundengine import brokers
    where = brokers.locate(LEDGER, "Catalin")
    assert where["IEMG"] == {"Trading 212": 38}
    assert where["AMZN"] == {"Trading 212": 7, "Davy": 1}


def test_a_split_position_is_two_orders():
    from fundengine import brokers
    routed = brokers.route_sell("AMZN", 1_840, brokers.locate(LEDGER, "Catalin"))
    assert len(routed["split"]) == 2
    # Counted rather than spelled, because a position can sit across three.
    assert "2 orders" in routed["why"]


def test_a_holding_with_no_history_says_so_rather_than_guessing():
    from fundengine import brokers
    routed = brokers.route_sell("RIVN", 450, brokers.locate(LEDGER, "Catalin"))
    assert routed["broker"] is None
    assert "not" in routed["why"] and "known" in routed["why"]


def test_a_position_from_the_unparseable_statement_is_still_located():
    """The Trading 212 PDF's numbers cannot be extracted, but it does name
    its open positions, so those are recorded rather than called unknown."""
    from fundengine import brokers
    routed = brokers.route_sell("AEM", 350, brokers.locate(LEDGER, "Catalin"))
    assert routed["broker"] == brokers.TRADING212
    assert "cannot be extracted" in routed["why"]


def test_a_buy_goes_to_the_cheaper_venue():
    from fundengine import brokers
    routed = brokers.route_buy(342, "EUR")
    assert routed["broker"] == brokers.TRADING212
    assert routed["cost"] == 0
    assert routed["saving"] == pytest.approx(14.99)


def test_the_minimum_commission_is_what_makes_small_trades_expensive():
    from fundengine import brokers
    small = brokers.route_buy(342, "EUR")["saving"]
    large = brokers.route_buy(10_000, "EUR")["saving"]
    assert large > small                       # 0.5% of 10k beats the floor
    assert "4.4%" in brokers.route_buy(342, "EUR")["why"]


def test_conversion_is_charged_only_on_a_non_euro_line():
    from fundengine import brokers
    assert brokers.cost_at(brokers.TRADING212, 1_000, "EUR") == 0
    assert brokers.cost_at(brokers.TRADING212, 1_000, "USD") == pytest.approx(1.5)


def test_a_stated_constraint_beats_an_inferred_capability():
    """Trading 212 fills fractions in the history, but Catalin has said these
    accounts cannot, so whole_shares wins."""
    out = actions.build(PLAN, PRICES, VOLS, WEIGHTS, 4.8, today=TODAY,
                        positions=POSITIONS, whole_shares=True,
                        location={"IEMG": {"Trading 212": 38}})
    buy = next(o for o in out if o["side"] == "buy")
    assert buy["shares"] == int(buy["shares"])
    assert buy["fractionalOk"] is False


def test_a_note_that_merely_mentions_another_broker_is_not_filed_under_it():
    """The opening balance for the Trading 212 half of APLD explained itself
    by naming Revolut, and a substring scan filed all twenty shares there."""
    from fundengine import brokers

    trades = [{
        "ticker": "APLD", "action": "buy", "shares": 20.0, "portfolio": "Catalin",
        "note": "Trading 212 opening balance - 26 are at Revolut, these are not",
    }]
    assert brokers.locate(trades) == {"APLD": {brokers.TRADING212: 20.0}}


def test_holding_something_at_a_venue_beats_the_range_heuristic():
    """Revolut is 'unlikely' to list an Amsterdam line and nonetheless holds
    the EM tracker in this book. Evidence outranks the rule of thumb."""
    from fundengine import brokers

    assert not brokers.lists_it(brokers.REVOLUT, "IEMA.AS")
    assert brokers.lists_it(brokers.REVOLUT, "IEMA.AS", {brokers.REVOLUT: 38.0})


def test_a_split_names_the_venue_that_actually_allows_fractions():
    from fundengine import brokers

    why = brokers.route_sell("AMZN", 1800.0,
                             {"AMZN": {brokers.REVOLUT: 7.0, brokers.DAVY: 1.0}},
                             234.0)["why"]
    assert "Revolut leg can be a fraction" in why
    assert "Davy trades whole shares" in why
    assert "Trading 212" not in why


def test_confidence_is_capped_when_the_share_count_does_not_reconcile():
    """An order sized off an unverified count is the one error here that
    costs real money, so no amount of good arithmetic may outrank it."""
    from fundengine import confidence

    kwargs = dict(theories={t: {"weights": {"X": 0.0}} for t in confidence.THEORIES},
                  current_pct=10.0, annual_gain=500.0, trade_cost=1.0,
                  basis_known=True, history_years=12.0, price_age_hours=1.0,
                  vs_200d=-10.0, momentum=-10.0)
    clean = confidence.score("X", "sell", reconciled=True, **kwargs)
    dirty = confidence.score("X", "sell", reconciled=False, **kwargs)

    assert clean["band"] == "high" and clean["score"] == 100
    assert dirty["score"] <= 34 and dirty["band"] == "speculative"
    assert any("does not reconcile" in c for c in dirty["caps"])


def test_a_trade_that_costs_more_than_it_gains_loses_points():
    from fundengine import confidence

    points, why = confidence.edge_vs_cost(annual_gain=10.0, trade_cost=15.0)
    assert points < 0 and "loses money on arithmetic alone" in why


def test_a_deadline_is_counted_in_sessions_not_calendar_days():
    """Saturday 29 August 2026 to Monday the 31st is two calendar days and
    one session. The calendar-day version read as if there were room."""
    from datetime import date
    from fundengine import markets

    saturday, monday = date(2026, 8, 29), date(2026, 8, 31)
    assert (monday - saturday).days == 2
    assert markets.sessions_between(saturday, monday) == 1


def test_the_weekend_and_holidays_are_not_sessions():
    from datetime import date
    from fundengine import markets

    assert not markets.is_session(date(2026, 8, 29))          # Saturday
    assert not markets.is_session(date(2026, 8, 30))          # Sunday
    assert markets.is_session(date(2026, 8, 31))              # Monday
    assert not markets.is_session(date(2026, 11, 26))         # Thanksgiving
    assert not markets.is_session(date(2026, 4, 3))           # Good Friday
    # Easter Monday is a session in New York and not in Frankfurt.
    assert markets.is_session(date(2026, 4, 6), markets.US)
    assert not markets.is_session(date(2026, 4, 6), markets.EU)


def test_the_next_open_is_a_real_moment_not_a_date():
    from datetime import datetime, timezone
    from fundengine import markets

    saturday = datetime(2026, 8, 29, 9, 51, tzinfo=timezone.utc)
    assert not markets.is_open(saturday)
    assert markets.why_closed(saturday) == "the weekend"
    # 09:30 New York on the Monday, which is 13:30 UTC in summer.
    assert markets.next_open(saturday).isoformat() == "2026-08-31T13:30:00+00:00"
    assert markets.next_open(saturday, markets.EU).isoformat() == "2026-08-31T07:00:00+00:00"


def test_a_month_ending_at_the_weekend_moves_the_free_sell_cutoff():
    """31 January 2027 is a Sunday, so the last chance to use a free sell is
    the Friday. The date on the deadline did not used to move."""
    from datetime import date
    from fundengine import markets

    assert date(2027, 1, 31).weekday() == 6
    assert markets.previous_session(date(2027, 1, 31)) == date(2027, 1, 29)
