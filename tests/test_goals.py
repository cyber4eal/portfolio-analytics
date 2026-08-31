from __future__ import annotations

from datetime import date

import pytest

from fundengine import goals, pension
from fundengine.portfolio import Holding


def deposit(book, name, value):
    return Holding(symbol=name, ticker=name, name=name, shares=0,
                   value_eur=value, currency="EUR", tradable=False, portfolio=book)


BASE = {"target": 15_000.0, "targetDate": "2027-02-01",
        "monthlyContribution": 1_000.0, "sheetHeld": 2_787.82,
        "includeMoneyMarket": True}


def test_the_goal_counts_deposit_money_in_every_book():
    """The sheet's own tracker looks at one book. If the other is holding
    deposit money for the same house, the goal is much further along."""
    tracked = goals.track(BASE, [
        deposit("Catalin", "Mortgage Deposit", 2_000),
        deposit("Catalin", "Money Market Fund", 787.82),
        deposit("Stefani", "Mortgage Deposit", 7_000),
    ], as_of=date(2026, 8, 28))

    assert tracked["held"] == pytest.approx(9_787.82)
    assert tracked["byBook"] == {"Catalin": 2787.82, "Stefani": 7000.0}
    assert tracked["countedElsewhere"] == pytest.approx(7_000)
    assert tracked["pct"] == pytest.approx(65.3, abs=0.1)


def test_investments_are_not_deposit_money():
    tracked = goals.track(BASE, [
        deposit("Catalin", "Mortgage Deposit", 2_000),
        Holding("NVDA", "NVDA", "Nvidia", 9, 1_700, "USD", True, "Catalin"),
    ], as_of=date(2026, 8, 28))
    assert tracked["held"] == pytest.approx(2_000)


def test_the_money_market_toggle_is_respected():
    holdings = [deposit("Catalin", "Mortgage Deposit", 2_000),
                deposit("Catalin", "Money Market Fund", 787.82)]
    excluded = goals.track({**BASE, "includeMoneyMarket": False}, holdings,
                           as_of=date(2026, 8, 28))
    assert excluded["held"] == pytest.approx(2_000)


def test_required_monthly_and_whether_that_is_enough():
    tracked = goals.track(BASE, [deposit("Catalin", "Mortgage Deposit", 9_787.82)],
                          as_of=date(2026, 8, 28))
    # 5,212 to find over 6 months.
    assert tracked["monthsRemaining"] == 6
    assert tracked["requiredMonthly"] == pytest.approx(868.70, abs=0.5)
    assert tracked["onTrack"] is True
    assert tracked["shortfallMonthly"] == 0


def test_a_shortfall_is_reported_as_one():
    tracked = goals.track(BASE, [deposit("Catalin", "Mortgage Deposit", 2_000)],
                          as_of=date(2026, 8, 28))
    assert tracked["onTrack"] is False
    assert tracked["shortfallMonthly"] > 0


def test_a_met_goal_does_not_report_a_negative_gap():
    tracked = goals.track(BASE, [deposit("Catalin", "Mortgage Deposit", 20_000)],
                          as_of=date(2026, 8, 28))
    assert tracked["gap"] == 0
    assert tracked["onTrack"] is True


# ---------------- pension charges and owners ----------------

def test_the_published_charge_is_applied_per_fund():
    assert pension.charge_for("ILIM Indexed North American Equity Fund NE5") == 0.015
    assert pension.charge_for("Something Unlisted") == pension.DEFAULT_CHARGE


def test_the_blended_charge_is_value_weighted():
    blended = pension.blended_charge([
        {"name": "ILIM Indexed North American Equity", "value_eur": 9_000},
        {"name": "Davy Moderate Growth", "value_eur": 1_000},
    ])
    assert blended == pytest.approx(0.015)


def test_two_pots_are_kept_apart(tmp_path):
    """The scheme covers both of them; importing one over the other would be
    silent and expensive."""
    store = tmp_path / "pension.json"
    pension.set_holdings([{"name": "A", "value_eur": 5_827}], store, owner="Catalin")
    pension.set_holdings([{"name": "B", "value_eur": 1_133}], store, owner="Stefani")

    assert pension.owners(store) == ["Catalin", "Stefani"]
    assert pension.summary(path=store, owner="Catalin")["total"] == pytest.approx(5_827)
    assert pension.summary(path=store, owner="Stefani")["total"] == pytest.approx(1_133)


def test_an_old_single_pot_file_still_loads(tmp_path):
    import json
    store = tmp_path / "pension.json"
    store.write_text(json.dumps({"holdings": [{"name": "A", "value_eur": 100}],
                                 "contributions": []}))
    assert pension.summary(path=store)["total"] == pytest.approx(100)


def test_a_negotiated_charge_replaces_the_published_one(tmp_path):
    store = tmp_path / "pension.json"
    pension.set_holdings([{"name": "ILIM Indexed North American Equity",
                           "value_eur": 5_000}], store, owner="Catalin")
    assert pension.summary(path=store, owner="Catalin")["charge"] == pytest.approx(0.015)

    pension.set_charge_override(0.0075, store, owner="Catalin")
    assert pension.summary(path=store, owner="Catalin")["charge"] == pytest.approx(0.0075)

    pension.set_charge_override(None, store, owner="Catalin")
    assert pension.summary(path=store, owner="Catalin")["charge"] == pytest.approx(0.015)


def test_a_charge_given_as_a_percent_is_refused(tmp_path):
    """1.5 would be 150% a year - the field wants a fraction."""
    with pytest.raises(ValueError, match="fraction"):
        pension.set_charge_override(1.5, tmp_path / "p.json", owner="Catalin")


def test_one_month_is_not_a_series_to_infer_from(tmp_path):
    """Regression: dividing the pot by a single logged month projected a
    monthly contribution equal to the whole pot - an estimate far worse
    than the gap it was patching."""
    store = tmp_path / "pension.json"
    pension.set_holdings([{"name": "Fund", "value_eur": 1_133}], store, owner="Stefani")
    pension.add_contribution({"date": "2026-05-11", "amount_eur": 84,
                              "source": "employer"}, store, owner="Stefani")
    summary = pension.summary(path=store, owner="Stefani")

    assert summary["monthsObserved"] == 1
    assert summary["impliedMonthly"] == 0          # refuses to guess from a point
    assert summary["monthlyRate"] == pytest.approx(84)


def test_a_thin_log_is_flagged_but_never_inferred_from(tmp_path):
    """Low coverage usually means the pot predates the log, not that
    contributions are missing from this period. Inferring from it turns a
    mature pot into an absurd monthly figure, so the observed rate stands
    and the page warns instead."""
    store = tmp_path / "pension.json"
    pension.set_holdings([{"name": "Fund", "value_eur": 200_000}], store, owner="X")
    for month in (5, 6, 7):
        pension.add_contribution({"date": f"2026-0{month}-11", "amount_eur": 400},
                                 store, owner="X")
    summary = pension.summary(path=store, owner="X")

    assert summary["contributionCoverage"] < 5        # the log explains almost none
    assert summary["monthlyRate"] == pytest.approx(400)   # and is still what is used
    assert summary["impliedMonthly"] > 400                # reported, not applied


def test_a_complete_log_is_trusted_over_the_pot(tmp_path):
    store = tmp_path / "pension.json"
    pension.set_holdings([{"name": "Fund", "value_eur": 1_200}], store, owner="Y")
    for month in (5, 6, 7):
        pension.add_contribution({"date": f"2026-0{month}-11", "amount_eur": 400},
                                 store, owner="Y")
    summary = pension.summary(path=store, owner="Y")

    assert summary["contributionCoverage"] == pytest.approx(100)
    assert summary["monthlyRate"] == pytest.approx(400)


def test_contributions_keep_accruing_between_statements(tmp_path):
    """The ask: assume they land every month, as they do in practice."""
    from datetime import date
    store = tmp_path / "pension.json"
    pension.set_holdings([{"name": "Fund", "value_eur": 1_133}], store, owner="Stefani")
    pension.add_contribution({"date": "2026-05-11", "amount_eur": 84}, store, owner="Stefani")

    accrued = pension.accrue(pension.summary(path=store, owner="Stefani"),
                             as_of=date(2026, 8, 28))
    assert accrued["accruedMonths"] == 3
    assert accrued["accrued"] == pytest.approx(252)
    assert accrued["estimatedTotal"] == pytest.approx(1_385)


# ---------------- reconciliation safety ----------------

def test_share_counts_are_never_overwritten_from_an_incomplete_ledger():
    """The ledger covers the brokers whose statements import, and one
    exports an unreadable PDF. It disagrees with the sheet in both
    directions, so overwriting either way corrupts the book: it would
    restore five RR shares that were sold and delete twenty APLD that are
    held."""
    from fundengine.portfolio import Holding, apply_ledger

    holdings = [
        Holding("RR", "RR", "Rolls-Royce", 71, 112.14, "USD", True, "Catalin"),
        Holding("APLD", "APLD", "Applied Digital", 46, 1006.06, "USD", True, "Catalin"),
    ]
    positions = {"RR": {"shares": 76.0}, "APLD": {"shares": 26.0}}
    out, fixes = apply_ledger(holdings, positions, None)

    assert [h.shares for h in out] == [71, 46]        # untouched
    assert {f["ticker"] for f in fixes} == {"RR", "APLD"}
    assert next(f for f in fixes if f["ticker"] == "RR")["gap"] == pytest.approx(5)
    assert next(f for f in fixes if f["ticker"] == "APLD")["gap"] == pytest.approx(-20)


def test_a_matching_position_raises_nothing():
    from fundengine.portfolio import Holding, apply_ledger
    holdings = [Holding("AMZN", "AMZN", "Amazon", 8, 1839.65, "USD", True, "Catalin")]
    out, fixes = apply_ledger(holdings, {"AMZN": {"shares": 8.0}}, None)
    assert fixes == []
    assert out[0].shares == 8


def test_a_holding_with_no_statement_is_left_entirely_alone():
    from fundengine.portfolio import Holding, apply_ledger
    holdings = [Holding("RIVN", "RIVN", "Rivian", 33, 457.71, "USD", True, "Catalin")]
    out, fixes = apply_ledger(holdings, {}, None)
    assert fixes == [] and out[0].shares == 33 and out[0].value_eur == 457.71


def test_the_payload_can_be_written_somewhere_other_than_the_checkout(tmp_path, monkeypatch):
    """The VPS serves /var/www/fundlab, which is not inside the checkout. A
    rebuild started from the site used to write into the checkout instead,
    report success, and leave the served page days old."""
    import importlib

    monkeypatch.setenv("BOND_SITE_DIR", str(tmp_path / "served"))
    from fundengine import publish
    reloaded = importlib.reload(publish)
    try:
        assert reloaded.SITE_DIR == tmp_path / "served"
        written = reloaded.write({"asOf": "2026-08-31"})
        assert written == tmp_path / "served" / "data.json"
        assert written.exists()
    finally:
        monkeypatch.delenv("BOND_SITE_DIR", raising=False)
        importlib.reload(publish)


def test_the_api_says_why_it_cannot_rebuild_instead_of_shelling_out():
    """The failure this replaces showed the tail of a Python traceback -
    "No module named 'numpy'" - to someone who pressed a refresh button."""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "server"))
    try:
        import api
    finally:
        sys.path.pop(0)

    capability = api.rebuild_capability()
    assert set(capability) >= {"canRebuild", "rebuildMode", "rebuildBlockers",
                               "sheetReadable", "csvFallback"}
    # Every blocker has to read as a sentence, not a stack frame.
    for blocker in capability["rebuildBlockers"]:
        assert blocker and "Traceback" not in blocker
    if not capability["canRebuild"]:
        assert capability["rebuildBlockers"], "a refusal has to say why"
