"""The pension pot, kept outside the Google Sheet.

A pension is not a brokerage account and the sheet is not the right home for
it. The units sit with a provider, most of them are in funds with no Yahoo
line - "Irish Life Indexed World Equity", "Zurich Prisma 4" - and the money
is not accessible, so it should never be mixed into the tradable book's
weights, budgets or free-sell counts.

It still belongs on the page, because leaving it out understates what you
have by whatever the pot is worth. So it is its own book: added to the
switcher, priced where a ticker resolves, carried at stated value where it
does not, and never merged into Combined.

Stored as one JSON file, edited through the API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import date, datetime
from pathlib import Path

STORE = Path(__file__).resolve().parent.parent / "data" / "pension.json"
BOOK = "Pension"


@dataclass
class PensionHolding:
    name: str
    value_eur: float
    ticker: str = ""          # optional: a Yahoo line, if the fund has one
    units: float = 0.0
    provider: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Contribution:
    date: str
    amount_eur: float
    source: str = "employee"   # employee | employer | avc | transfer
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Pension:
    holdings: list = field(default_factory=list)
    contributions: list = field(default_factory=list)
    updated: str = ""

    def as_dict(self) -> dict:
        return {"holdings": self.holdings, "contributions": self.contributions,
                "updated": self.updated}


def load(path: Path = STORE) -> Pension:
    if not Path(path).exists():
        return Pension()
    try:
        blob = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return Pension()
    return Pension(holdings=blob.get("holdings", []),
                   contributions=blob.get("contributions", []),
                   updated=blob.get("updated", ""))


def save(pension: Pension, path: Path = STORE) -> Pension:
    pension.updated = datetime.now().isoformat(timespec="seconds")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(pension.as_dict(), indent=1), encoding="utf-8")
    return pension


def _validate_holding(row: dict) -> dict:
    name = str(row.get("name", "")).strip()
    if not name:
        raise ValueError("a pension holding needs a name")
    try:
        value = float(row.get("value_eur") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value must be a number: {exc}") from exc
    if value < 0:
        raise ValueError("value cannot be negative")
    return PensionHolding(
        name=name, value_eur=round(value, 2),
        ticker=str(row.get("ticker", "")).strip().upper(),
        units=float(row.get("units") or 0),
        provider=str(row.get("provider", "")).strip(),
        note=str(row.get("note", "")).strip(),
    ).as_dict()


def set_holdings(rows: list[dict], path: Path = STORE) -> Pension:
    """Replace the holdings wholesale.

    A pension statement arrives as a whole picture rather than a stream of
    trades, so this overwrites rather than appending. Contributions are the
    append-only half.
    """
    pension = load(path)
    pension.holdings = [_validate_holding(r) for r in rows]
    return save(pension, path)


def add_contribution(row: dict, path: Path = STORE) -> Pension:
    try:
        amount = float(row.get("amount_eur") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"amount must be a number: {exc}") from exc
    if amount <= 0:
        raise ValueError("a contribution must be positive")
    when = str(row.get("date", "")).strip()
    try:
        date.fromisoformat(when)
    except ValueError as exc:
        raise ValueError(f"date must be YYYY-MM-DD: {exc}") from exc
    source = str(row.get("source", "employee")).strip().lower()
    if source not in ("employee", "employer", "avc", "transfer"):
        raise ValueError("source must be employee, employer, avc or transfer")

    pension = load(path)
    pension.contributions.append(
        Contribution(date=when, amount_eur=round(amount, 2), source=source,
                     note=str(row.get("note", "")).strip()).as_dict())
    pension.contributions.sort(key=lambda c: c["date"])
    return save(pension, path)


def summary(pension: Pension | None = None, path: Path = STORE) -> dict:
    pension = pension or load(path)
    total = sum(float(h.get("value_eur") or 0) for h in pension.holdings)
    paid_in = sum(float(c.get("amount_eur") or 0) for c in pension.contributions)
    by_source: dict[str, float] = {}
    for c in pension.contributions:
        by_source[c["source"]] = by_source.get(c["source"], 0) + float(c["amount_eur"] or 0)
    priced = [h for h in pension.holdings if h.get("ticker")]
    return {
        "total": round(total, 2),
        "paidIn": round(paid_in, 2),
        # Only meaningful once the contribution history is complete; a pot
        # with two contributions logged against ten years of real ones will
        # show a growth figure that is mostly missing data.
        "growth": round(total - paid_in, 2) if paid_in else None,
        "bySource": {k: round(v, 2) for k, v in by_source.items()},
        "holdings": pension.holdings,
        "contributions": pension.contributions,
        "pricedCount": len(priced),
        "unpricedCount": len(pension.holdings) - len(priced),
        "updated": pension.updated,
    }


def as_holdings(pension: Pension | None = None, path: Path = STORE) -> list:
    """Pension lines shaped like book holdings, so the rest of the engine
    can treat the pot as one more book without special cases."""
    from .portfolio import Holding, currency_for

    pension = pension or load(path)
    out = []
    for row in pension.holdings:
        ticker = (row.get("ticker") or "").strip().upper()
        out.append(Holding(
            symbol=ticker or row["name"][:24],
            ticker=ticker or row["name"][:24],
            name=row["name"],
            shares=float(row.get("units") or 0),
            value_eur=float(row.get("value_eur") or 0),
            currency=currency_for(ticker) if ticker else "EUR",
            tradable=bool(ticker),
            portfolio=BOOK,
        ))
    return out
