from __future__ import annotations

import pytest

from fundengine import importers, ledger


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


T212 = """Date,Ticker,Type,Quantity,Price per share,Total Amount,Currency,FX Rate
2024-06-27T14:25:33.911Z,TSLA,BUY - MARKET,0.21762818,USD 196.39,USD 42.74,USD,1.0735
2024-06-27T14:24:36.985589Z,,CASH WITHDRAWAL,,,EUR -1,EUR,1.0000
2024-12-16T10:00:00.000Z,TSLA,SELL - MARKET,0.1,USD 441.08,USD 44.11,USD,1.05
2025-01-02T10:00:00.000Z,WTF,BUY - LIMIT,1,USD 10.00,USD 10,USD,1.03
2025-01-03T10:00:00.000Z,NVDA,DIVIDEND,,USD 0.01,USD 0.05,USD,1.03
"""


def test_trading212_reads_trades_and_ignores_cash(tmp_path):
    result = importers.trading212_csv(write(tmp_path, "t.csv", T212), "Catalin")

    assert len(result.trades) == 2
    assert {t.action for t in result.trades} == {ledger.BUY, ledger.SELL}
    assert result.trades[0].ticker == "TSLA"
    assert result.trades[0].price == pytest.approx(196.39)
    assert result.trades[0].currency == "USD"
    assert result.trades[0].date == "2024-06-27"


def test_an_unmapped_symbol_is_reported_not_guessed(tmp_path):
    """Booking a trade against a guessed ticker is worse than not booking it."""
    result = importers.trading212_csv(write(tmp_path, "t.csv", T212), "Catalin")
    assert "WTF" in result.unmapped
    assert all(t.ticker != "WTF" for t in result.trades)


def test_dividends_are_not_trades(tmp_path):
    result = importers.trading212_csv(write(tmp_path, "t.csv", T212), "Catalin")
    assert all("DIVIDEND" not in t.note.upper() for t in result.trades)


def test_broker_symbols_map_to_the_tickers_this_project_uses():
    # T212 lists Airbus on a European line; the sheet holds the ADR.
    assert importers._map_symbol("AIR1") == "EADSY"
    assert importers._map_symbol("EUNM") == "IEMG"
    # Davy contract notes describe the instrument rather than ticker it.
    assert importers._map_symbol("PALANTIR TECH INC COM USD0.001 CLASS A") == "PLTR"
    assert importers._map_symbol("BYD COMPANY LTD 'H'CNY1") == "BYDDY"
    assert importers._map_symbol("SOMETHING UNKNOWN PLC") is None


def test_pdf_statements_are_refused_with_the_reason(tmp_path):
    """The broker PDF embeds a font with no ToUnicode map for digits, so
    every number extracts as a null byte. Failing loudly beats importing
    zeros."""
    path = tmp_path / "statement.pdf"
    path.write_bytes(b"%PDF-1.4")
    with pytest.raises(ValueError, match="not importable"):
        importers.detect(path, "Catalin")


def test_imported_trades_replay_into_a_cost_basis(tmp_path):
    result = importers.trading212_csv(write(tmp_path, "t.csv", T212), "Catalin")
    book = ledger.positions([t.as_dict() for t in result.trades])
    tesla = book["TSLA"]

    # Share counts are stored to 6 decimals; Trading 212 reports 8. A
    # millionth of a share is worth a fraction of a cent, and the rounding
    # keeps the ledger readable.
    assert tesla["shares"] == pytest.approx(0.21762818 - 0.1, abs=1e-6)
    assert tesla["avg_cost"] == pytest.approx(196.39)
    # Realised P&L is stored to the cent, which is the unit it is paid in.
    assert tesla["realised"] == pytest.approx(0.1 * (441.08 - 196.39), abs=0.01)
