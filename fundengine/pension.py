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

#: Published standard annual management charges for the scheme's funds.
#:
#: ILIM's own factsheet for the Indexed North American Equity Fund states:
#: "Fund returns are quoted before taxes and after a standard annual
#: management charge of 1.50%. The fund management charge and product
#: charges will vary depending on the terms and conditions of your
#: contract." An occupational scheme almost always negotiates below the
#: retail rate, so this is an upper bound and not a measurement - the real
#: figure is in the scheme booklet.
#:
#: It is modelled at all because leaving it out is the worse error. Over
#: thirty-five years a charge of this size is the difference between one
#: retirement and a materially poorer one, and a projection that silently
#: assumes zero is not conservative, it is wrong.
STANDARD_CHARGES = {
    "ILIM INDEXED NORTH AMERICAN EQUITY": 0.0150,
    "ILIM PASSIVE GLOBAL EQUITY": 0.0150,
    "DAVY GLOBAL EQUITIES FOUNDATION": 0.0150,
    "DAVY GLOBAL FUNDAMENTALS EQUITY": 0.0150,
    "DAVY LONG TERM GROWTH": 0.0150,
    "DAVY MODERATE GROWTH": 0.0150,
}
DEFAULT_CHARGE = 0.0150


def charge_for(name: str) -> float:
    key = (name or "").upper()
    for fragment, rate in STANDARD_CHARGES.items():
        if fragment in key:
            return rate
    return DEFAULT_CHARGE


def blended_charge(holdings: list) -> float:
    """Value-weighted charge across the pot."""
    total = sum(float(h.get("value_eur") or 0) for h in holdings) or 1.0
    return sum(float(h.get("value_eur") or 0) * charge_for(h.get("name", ""))
               for h in holdings) / total


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
    contribution_override: float | None = None
    charge_override: float | None = None

    def as_dict(self) -> dict:
        return {"holdings": self.holdings, "contributions": self.contributions,
                "updated": self.updated,
                "contribution_override": self.contribution_override,
                "charge_override": self.charge_override}


#: The scheme covers both of them, so a pot has an owner. Files written
#: before that was true are migrated on read into the first owner's slot.
DEFAULT_OWNER = "Catalin"


def _read_blob(path: Path) -> dict:
    if not Path(path).exists():
        return {"pensions": {}}
    try:
        blob = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"pensions": {}}
    if "pensions" in blob:
        return blob
    # Old flat shape: one unnamed pot.
    if blob.get("holdings") or blob.get("contributions"):
        return {"pensions": {DEFAULT_OWNER: blob}}
    return {"pensions": {}}


def owners(path: Path = STORE) -> list:
    return sorted(_read_blob(path).get("pensions", {}))


def load(owner: str | None = None, path: Path = STORE) -> Pension:
    blob = _read_blob(path).get("pensions", {})
    owner = owner or (sorted(blob)[0] if blob else DEFAULT_OWNER)
    one = blob.get(owner, {})
    return Pension(holdings=one.get("holdings", []),
                   contributions=one.get("contributions", []),
                   updated=one.get("updated", ""),
                   contribution_override=one.get("contribution_override"),
                   charge_override=one.get("charge_override"))


def save(pension: Pension, owner: str | None = None, path: Path = STORE) -> Pension:
    pension.updated = datetime.now().isoformat(timespec="seconds")
    blob = _read_blob(path)
    owner = owner or (sorted(blob["pensions"])[0] if blob["pensions"] else DEFAULT_OWNER)
    blob["pensions"][owner] = pension.as_dict()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(blob, indent=1), encoding="utf-8")
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


def set_holdings(rows: list[dict], path: Path = STORE,
                 owner: str | None = None) -> Pension:
    """Replace the holdings wholesale.

    A pension statement arrives as a whole picture rather than a stream of
    trades, so this overwrites rather than appending. Contributions are the
    append-only half.
    """
    pension = load(owner, path)
    pension.holdings = [_validate_holding(r) for r in rows]
    return save(pension, owner, path)


def add_contribution(row: dict, path: Path = STORE,
                     owner: str | None = None) -> Pension:
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

    pension = load(owner, path)
    pension.contributions.append(
        Contribution(date=when, amount_eur=round(amount, 2), source=source,
                     note=str(row.get("note", "")).strip()).as_dict())
    pension.contributions.sort(key=lambda c: c["date"])
    return save(pension, owner, path)


def summary(pension: Pension | None = None, path: Path = STORE,
            owner: str | None = None) -> dict:
    pension = pension or load(owner, path)
    total = sum(float(h.get("value_eur") or 0) for h in pension.holdings)
    paid_in = sum(float(c.get("amount_eur") or 0) for c in pension.contributions)
    by_source: dict[str, float] = {}
    for c in pension.contributions:
        by_source[c["source"]] = by_source.get(c["source"], 0) + float(c["amount_eur"] or 0)
    priced = [h for h in pension.holdings if h.get("ticker")]
    out = {
        "total": round(total, 2),
        "paidIn": round(paid_in, 2),
        # Only meaningful once the contribution history is complete; a pot
        # with two contributions logged against ten years of real ones will
        # show a growth figure that is mostly missing data.
        "growth": round(total - paid_in, 2) if paid_in else None,
        "bySource": {k: round(v, 2) for k, v in by_source.items()},
        "holdings": pension.holdings,
        "contributions": pension.contributions,
        "contributionOverride": pension.contribution_override,
        "chargeOverride": pension.charge_override,
        # How much of the pot the logged contributions actually explain. A
        # WTW statement is exported one fund at a time, so a pot can be five
        # times the contributions on record - and a projection that treats
        # the logged amount as the whole story understates what goes in.
        "contributionCoverage": (
            round(100 * paid_in / total, 1) if total and paid_in else 0.0),
        "monthsObserved": len({c["date"][:7] for c in pension.contributions}),
        "impliedMonthly": 0.0,   # filled below once coverage is known
        "charge": (pension.charge_override
                   if pension.charge_override is not None
                   else blended_charge(pension.holdings)),
        "owner": owner or "",
        "pricedCount": len(priced),
        "unpricedCount": len(pension.holdings) - len(priced),
        "updated": pension.updated,
    }
    out["impliedMonthly"] = implied_monthly(out)
    out["monthlyRate"] = recent_monthly(out)
    return out


def as_holdings(pension: Pension | None = None, path: Path = STORE,
                owner: str | None = None) -> list:
    """Pension lines shaped like book holdings, so the rest of the engine
    can treat the pot as one more book without special cases."""
    from .portfolio import Holding, currency_for

    pension = pension or load(owner, path)
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


def set_contribution_override(amount: float | None, path: Path = STORE,
                              owner: str | None = None) -> Pension:
    """Pin the monthly contribution rate, or clear it to go back to the average.

    Contributions track salary, so a raise or a change in employer rate makes
    the trailing average wrong for everything ahead of it. The override says
    what to project with from here; the history keeps what actually happened.
    """
    pension = load(owner, path)
    if amount is None:
        pension.contribution_override = None
    else:
        amount = float(amount)
        if amount < 0:
            raise ValueError("a contribution rate cannot be negative")
        pension.contribution_override = round(amount, 2)
    return save(pension, owner, path)


#: Below this, the logged contributions do not explain the pot and the
#: observed rate is a fragment of the real one.
COVERAGE_FLOOR = 50.0

#: And below this many distinct months there is no series to infer from.
MIN_MONTHS_TO_INFER = 3


def implied_monthly(summary_: dict) -> float:
    """What the pot implies per month, IF the log covers its whole life.

    Reported for comparison and deliberately not used to set the rate. The
    temptation is obvious - a WTW statement exports one fund at a time, so
    the log can explain a fraction of the pot, and dividing the pot by the
    months on record looks like a fix. It is not.

    Low coverage almost never means "the log is missing contributions from
    this period". It usually means the pot has history predating the log,
    which is the normal state of any pension. Inferring from it turns a
    mature EUR 200k pot with EUR 400 a month on record into a projected
    EUR 66,000 a month. The observed rate can be too low; the inferred one
    can be absurd, and absurd is worse in a projection nobody re-checks.

    So the rate stays what was actually observed, and the page says plainly
    when the log is too thin to trust it.
    """
    contributions = summary_.get("contributions") or []
    total = float(summary_.get("total") or 0)
    observed = len({c["date"][:7] for c in contributions})
    # Fewer than three months is not a series, it is a point. Dividing the
    # pot by one month produced a projected contribution equal to the entire
    # pot every month - an estimate worse than the gap it was patching.
    if not contributions or total <= 0 or observed < MIN_MONTHS_TO_INFER:
        return 0.0
    start = date.fromisoformat(min(c["date"] for c in contributions))
    end = date.fromisoformat(max(c["date"] for c in contributions))
    months = max(1, (end.year - start.year) * 12 + end.month - start.month + 1)
    return round(total / months, 2)


def recent_monthly(summary_: dict, months: int = 6) -> float:
    """Average monthly contribution over the last `months` of the history.

    An explicit override wins: after a pay rise the trailing average is a
    record of the old salary, not a rate to project forward.

    Averaged rather than taken from the newest entry, because employer and
    employee amounts land as separate rows on the same day and some months
    carry a catch-up. The early months of a scheme also include the opening
    transfer, which is not a monthly rate and would flatter a projection.
    """
    override = summary_.get("contributionOverride")
    if override:
        return float(override)
    contributions = summary_.get("contributions") or []
    if not contributions:
        return 0.0
    by_month: dict[str, float] = {}
    for row in contributions:
        key = row["date"][:7]
        by_month[key] = by_month.get(key, 0.0) + float(row.get("amount_eur") or 0)
    recent = [v for _, v in sorted(by_month.items())[-months:]]
    return round(sum(recent) / len(recent), 2) if recent else 0.0


def accrue(summary_: dict, as_of: date | None = None) -> dict:
    """Add the contributions expected since the last statement.

    Contributions land monthly, but a statement is only as fresh as the day
    it was downloaded. Between statements the pot shown would otherwise
    drift steadily below reality - by a full month's pay every month, which
    on this scheme is over four hundred euro.

    Accrued months are estimates and are labelled as such: they assume the
    contribution rate holds and they credit no investment growth, so the
    estimate is deliberately the conservative one. The next real statement
    replaces them, because `set_holdings` overwrites rather than appends.
    """
    contributions = summary_.get("contributions") or []
    monthly = recent_monthly(summary_)
    if not contributions or monthly <= 0:
        return {**summary_, "accruedMonths": 0, "accrued": 0.0,
                "estimatedTotal": summary_.get("total", 0.0)}

    as_of = as_of or date.today()
    last = date.fromisoformat(max(c["date"] for c in contributions))
    months = (as_of.year - last.year) * 12 + (as_of.month - last.month)
    # Only whole months that have actually come round since the last one.
    months = max(0, months)

    accrued = round(monthly * months, 2)
    return {
        **summary_,
        "lastStatement": last.isoformat(),
        "monthlyRate": monthly,
        "accruedMonths": months,
        "accrued": accrued,
        "estimatedTotal": round(summary_.get("total", 0.0) + accrued, 2),
        "accrualNote": (
            f"{months} month(s) since the {last.isoformat()} statement at "
            f"EUR {monthly:,.0f} a month, added as an estimate. No growth is "
            f"credited on it, so this understates rather than flatters. "
            f"Import the next WTW statement to replace the estimate."
        ) if months else "",
    }


def all_owners(path: Path = STORE) -> dict:
    """Every pot, keyed by owner, each with its accrual applied."""
    return {name: accrue(summary(path=path, owner=name), )
            for name in owners(path)}


def set_charge_override(rate: float | None, path: Path = STORE,
                        owner: str | None = None) -> Pension:
    """Pin the scheme's actual annual charge.

    The published rate is retail; a scheme negotiates its own. Once you have
    the real number from the scheme booklet, this replaces the assumption.
    """
    pension = load(owner, path)
    if rate is None:
        pension.charge_override = None
    else:
        rate = float(rate)
        if not 0 <= rate < 0.1:
            raise ValueError("a charge should be a fraction, e.g. 0.0075 for 0.75%")
        pension.charge_override = rate
    return save(pension, owner, path)
