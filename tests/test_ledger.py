from __future__ import annotations

import pytest

from fundengine import ledger


def trade(**kwargs):
    base = dict(ticker="NVDA", action="buy", shares=1, price=100,
                date="2026-01-05", portfolio="Catalin")
    base.update(kwargs)
    return ledger.Trade(**base)


def test_average_cost_and_realised_pnl():
    """Two buys at different prices then a partial sale: the basis is the
    blended cost, and the gain is measured against that, not against the
    most recent purchase."""
    book = ledger.positions([
        trade(shares=5, price=100).as_dict(),
        trade(shares=5, price=200, date="2026-02-05").as_dict(),
        trade(shares=4, price=300, action="sell", date="2026-03-05").as_dict(),
    ])
    nvda = book["NVDA"]

    assert nvda["shares"] == 6
    assert nvda["avg_cost"] == pytest.approx(150)
    assert nvda["realised"] == pytest.approx(4 * (300 - 150))
    assert nvda["cost"] == pytest.approx(6 * 150)


def test_selling_everything_leaves_no_basis():
    book = ledger.positions([
        trade(shares=10, price=50).as_dict(),
        trade(shares=10, price=75, action="sell", date="2026-02-01").as_dict(),
    ])
    assert book["NVDA"]["shares"] == 0
    assert book["NVDA"]["avg_cost"] is None
    assert book["NVDA"]["realised"] == pytest.approx(250)


def test_a_sale_with_no_recorded_purchase_books_proceeds_not_profit():
    """The ledger starts the day you begin using it, so the first sale of a
    long-held position has no basis behind it. Proceeds are recorded; a
    fabricated cost of zero would report the whole sale as gain."""
    book = ledger.positions([trade(shares=3, price=90, action="sell").as_dict()])
    assert book["NVDA"]["shares"] == -3
    assert book["NVDA"]["realised"] == pytest.approx(270)


def test_books_are_kept_apart():
    trades = [trade(shares=5, price=100).as_dict(),
              trade(shares=2, price=100, portfolio="Stefani").as_dict()]
    assert ledger.positions(trades, "Catalin")["NVDA"]["shares"] == 5
    assert ledger.positions(trades, "Stefani")["NVDA"]["shares"] == 2
    assert ledger.positions(trades)["NVDA"]["shares"] == 7


@pytest.mark.parametrize("kwargs,message", [
    ({"shares": -1}, "positive"),
    ({"shares": 0}, "positive"),
    ({"action": "hold"}, "action"),
    ({"portfolio": "Combined"}, "Combined"),
    ({"portfolio": ""}, "Combined"),
    ({"date": "05/01/2026"}, "YYYY-MM-DD"),
    ({"ticker": "  "}, "ticker"),
    ({"price": -5}, "negative"),
])
def test_bad_trades_are_refused(kwargs, message):
    with pytest.raises(ValueError, match=message):
        ledger._validate(trade(**kwargs))


def test_round_trip_through_the_file(tmp_path):
    path = tmp_path / "transactions.jsonl"
    ledger.append(trade(shares=2, price=10), path)
    ledger.append(trade(shares=3, price=20, date="2026-02-01"), path)
    assert len(ledger.read_all(path)) == 2


def test_a_truncated_final_line_costs_only_that_line(tmp_path):
    """JSON Lines is chosen precisely so a half-written record cannot take
    the history with it."""
    path = tmp_path / "transactions.jsonl"
    ledger.append(trade(shares=2, price=10), path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"ticker":"AMZN","action":"bu')

    assert len(ledger.read_all(path)) == 1


def test_summary_totals(tmp_path):
    path = tmp_path / "t.jsonl"
    ledger.append(trade(shares=10, price=10, fee=2), path)
    ledger.append(trade(shares=5, price=20, action="sell", date="2026-06-01", fee=1), path)
    summary = ledger.summary(ledger.read_all(path))

    assert summary["count"] == 2
    assert summary["fees"] == pytest.approx(3)
    assert summary["realised"] == pytest.approx(5 * (20 - 10))
    assert summary["first"] == "2026-01-05" and summary["last"] == "2026-06-01"
