"""Append-only transaction ledger.

The sheet stores a share count and nothing else: what you hold now, with no
record of what you paid or when. That makes several ordinary questions
unanswerable - what is my cost basis, what have I actually realised, is this
position up because it went up or because I bought more of it.

So trades are recorded here as well as being applied to the sheet, and the
file is append-only. A correction is a new compensating entry, never an edit
to an old one: a ledger you can rewrite is not a record of anything.

Format is JSON Lines so a partly-written final line can be dropped without
taking the whole history with it, which is the failure mode a single JSON
array has.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, date
from pathlib import Path

LEDGER = Path(__file__).resolve().parent.parent / "data" / "transactions.jsonl"
BUY, SELL = "buy", "sell"


@dataclass
class Trade:
    ticker: str
    action: str                  # buy | sell
    shares: float
    price: float                 # per share, in `currency`
    date: str                    # trade date, YYYY-MM-DD
    portfolio: str
    currency: str = "EUR"
    fee: float = 0.0
    note: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    recorded: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    applied_to_sheet: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _validate(trade: Trade) -> None:
    if trade.action not in (BUY, SELL):
        raise ValueError(f"action must be '{BUY}' or '{SELL}', got {trade.action!r}")
    if trade.shares <= 0:
        raise ValueError("shares must be positive - a sale is action='sell', not negative shares")
    if trade.price < 0:
        raise ValueError("price cannot be negative")
    if not trade.ticker.strip():
        raise ValueError("ticker is required")
    if not trade.portfolio.strip() or trade.portfolio.strip().lower() == "combined":
        # Same rule the sheet writer enforces: Combined is a reporting view,
        # and a trade booked against it could land on either book's row.
        raise ValueError("a trade needs a real book ('Catalin' or 'Stefani'), not 'Combined'")
    try:
        date.fromisoformat(trade.date)
    except ValueError as exc:
        raise ValueError(f"date must be YYYY-MM-DD: {exc}") from exc


def append(trade: Trade, path: Path = LEDGER) -> Trade:
    _validate(trade)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(trade.as_dict(), separators=(",", ":")) + "\n")
    return trade


def read_all(path: Path = LEDGER) -> list[dict]:
    """Every trade, oldest first. A truncated last line is skipped rather
    than raising - a half-written record should cost you that record, not
    the whole history."""
    if not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.sort(key=lambda t: (t.get("date", ""), t.get("recorded", "")))
    return out


def positions(trades: list[dict], portfolio: str | None = None) -> dict[str, dict]:
    """Replay the ledger into share counts, cost basis and realised P&L.

    Average cost, not FIFO. Irish CGT actually requires FIFO with a four-week
    rule, so these figures are for seeing what a position cost you, not for a
    tax return - the note says so on screen too.
    """
    book: dict[str, dict] = {}
    for trade in trades:
        if portfolio and trade.get("portfolio") != portfolio:
            continue
        ticker = trade["ticker"]
        row = book.setdefault(ticker, {
            "ticker": ticker, "shares": 0.0, "cost": 0.0,
            "realised": 0.0, "fees": 0.0, "trades": 0,
        })
        shares, price = float(trade["shares"]), float(trade["price"])
        row["fees"] += float(trade.get("fee") or 0)
        row["trades"] += 1

        if trade["action"] == BUY:
            row["shares"] += shares
            row["cost"] += shares * price
        else:
            if row["shares"] <= 0:
                # Selling something the ledger never saw bought: record the
                # proceeds, but there is no basis to net them against.
                row["realised"] += shares * price
                row["shares"] -= shares
                continue
            sold = min(shares, row["shares"])
            unit_cost = row["cost"] / row["shares"]
            row["realised"] += sold * (price - unit_cost)
            row["cost"] -= sold * unit_cost
            row["shares"] -= sold

    for row in book.values():
        row["avg_cost"] = round(row["cost"] / row["shares"], 4) if row["shares"] > 0 else None
        row["shares"] = round(row["shares"], 6)
        row["cost"] = round(row["cost"], 2)
        row["realised"] = round(row["realised"], 2)
    return book


def summary(trades: list[dict], portfolio: str | None = None) -> dict:
    book = positions(trades, portfolio)
    rows = [t for t in trades if not portfolio or t.get("portfolio") == portfolio]
    return {
        "count": len(rows),
        "realised": round(sum(r["realised"] for r in book.values()), 2),
        "fees": round(sum(r["fees"] for r in book.values()), 2),
        "invested": round(sum(r["cost"] for r in book.values()), 2),
        "first": rows[0]["date"] if rows else None,
        "last": rows[-1]["date"] if rows else None,
        "positions": book,
    }
