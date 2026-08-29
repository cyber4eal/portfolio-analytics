"""Which account to place each order in, and what that costs.

An order without a venue is still not an instruction. These holdings sit
across two accounts with different rules, and the difference is not
cosmetic:

  Trading 212  No commission. 0.15% on any currency conversion, so a US
               line costs something and a EUR-listed ETF costs nothing.
               Trades fractional shares.

  Davy         Commission with a floor, which is what hurts on small
               trades - a EUR 350 buy paying a EUR 14.99 minimum is 4.3%
               gone before the position has done anything. Whole shares
               only.

Two consequences worth stating rather than leaving implicit. A sell can
only happen where the shares are, and several positions are split across
both accounts - so "sell all of AMZN" is two orders, not one. And a buy
should go wherever it is cheapest, which for a EUR-denominated ETF is
almost always the commission-free account.

Broker costs are assumptions, not measurements: the Davy contract notes do
not separate commission from FX spread, and the two are the same money
either way. They are stated on screen so a wrong one is visible.
"""

from __future__ import annotations

from dataclasses import dataclass

TRADING212, DAVY, REVOLUT = "Trading 212", "Davy", "Revolut"


@dataclass(frozen=True)
class Broker:
    name: str
    fractional: bool
    commission_pct: float
    commission_min: float
    fx_pct: float
    note: str


BROKERS = {
    REVOLUT: Broker(REVOLUT, True, 0.0, 0.0, 0.005,
                    "Free trades within the monthly plan allowance, then a small "
                    "per-trade fee; conversion is the real cost. Fractions allowed."),
    TRADING212: Broker(TRADING212, True, 0.0, 0.0, 0.0015,
                       "No commission; 0.15% on currency conversion. Fractions allowed."),
    DAVY: Broker(DAVY, False, 0.005, 14.99, 0.0,
                 "Commission with a floor, so small trades are expensive. Whole shares only."),
}

#: Holdings whose account is known but has no importable statement. The
#: Trading 212 PDF lists these as open positions and cannot be parsed, so
#: the location is recorded here rather than left unknown.
KNOWN_LOCATION = {
    "AEM": TRADING212,
    "SPCX": TRADING212,
}


#: How a note names its account. Matched against the START of the note, not
#: anywhere in it: an opening balance whose explanation mentions a second
#: broker was being filed under whichever name appeared first in this list,
#: which put twenty Trading 212 shares in the Revolut column.
NOTE_PREFIXES = ((DAVY, ("davy",)),
                 (REVOLUT, ("revolut",)),
                 (TRADING212, ("trading 212", "t212")))


def broker_from_note(note: str | None) -> str | None:
    text = (note or "").strip().lower()
    if not text:
        return None
    for broker, prefixes in NOTE_PREFIXES:
        if any(text.startswith(prefix) for prefix in prefixes):
            return broker
    # Fall back to a scan, for a hand-written note that leads with something
    # else. Ambiguous notes - two broker names, neither at the front - are
    # left unplaced rather than guessed at.
    hits = [b for b, prefixes in NOTE_PREFIXES if any(p in text for p in prefixes)]
    return hits[0] if len(hits) == 1 else None


def locate(trades: list[dict], portfolio: str | None = None) -> dict:
    """Replay the ledger per broker to see where the shares actually are.

    Derived from the imported statements rather than declared, so it stays
    right as trades are added - and it is only as complete as the imports.
    A position with no ledger history has no known location, which is said
    rather than guessed.
    """
    held: dict = {}
    for trade in trades:
        if portfolio and trade.get("portfolio") != portfolio:
            continue
        broker = broker_from_note(trade.get("note"))
        if not broker:
            continue
        row = held.setdefault(trade["ticker"], {})
        shares = float(trade["shares"]) * (1 if trade["action"] == "buy" else -1)
        row[broker] = row.get(broker, 0.0) + shares

    return {ticker: {b: round(s, 6) for b, s in brokers.items() if s > 1e-6}
            for ticker, brokers in held.items()}


def cost_at(broker: str, euros: float, currency: str) -> float:
    spec = BROKERS[broker]
    commission = max(euros * spec.commission_pct, spec.commission_min) \
        if spec.commission_pct or spec.commission_min else 0.0
    fx = euros * spec.fx_pct if currency != "EUR" else 0.0
    return round(commission + fx, 2)


#: Which venues plausibly list what. European UCITS ETFs on Xetra and
#: Euronext are a Trading 212 and Davy thing; Revolut's range is mostly
#: US-listed. This decides ties and raises a flag, it does not claim to be
#: a product list - availability changes and is worth confirming in the app
#: before placing anything.
EUROPEAN_LINES = (".DE", ".AS", ".PA", ".MI", ".L", ".SW")
UNLIKELY_AT = {REVOLUT: EUROPEAN_LINES}


def lists_it(broker: str, ticker: str, seen_at: dict | None = None) -> bool:
    # Having actually held it there beats any guess about a product range.
    # Revolut is "unlikely" for Amsterdam lines and nonetheless carries the
    # EM tracker in this book, which the ledger proves and the rule did not.
    if seen_at and seen_at.get(broker):
        return True
    suffixes = UNLIKELY_AT.get(broker)
    return not (suffixes and ticker.endswith(suffixes))


def route_buy(euros: float, currency: str, ticker: str = "",
              seen_at: dict | None = None) -> dict:
    """Cheapest venue that plausibly lists it, with the costs shown.

    Cost decides, but only among venues that carry the instrument. Routing
    a Xetra UCITS line to an account whose range is mostly US-listed is a
    cheaper trade that cannot be placed, which is not cheaper at all.

    Ties are broken on conversion cost, since a tie on this trade says
    nothing about the next one.
    """
    options = [{"broker": name, "cost": cost_at(name, euros, currency),
                "note": BROKERS[name].note,
                "lists": lists_it(name, ticker, seen_at) if ticker else True,
                "fxPct": BROKERS[name].fx_pct}
               for name in BROKERS]
    options.sort(key=lambda o: (not o["lists"], o["cost"], o["fxPct"]))
    best = options[0]
    worst = max(options, key=lambda o: o["cost"])
    excluded = [o["broker"] for o in options if not o["lists"]]
    return {
        "broker": best["broker"],
        "cost": best["cost"],
        "options": options,
        "excluded": excluded,
        "saving": round(worst["cost"] - best["cost"], 2),
        "why": (
            (f"{', '.join(excluded)} is unlikely to list a {ticker} line, so it is "
             f"out regardless of cost. " if excluded else "") +
            f"{best['broker']} costs {best['cost']:.2f} against {worst['cost']:.2f} at "
            f"{worst['broker']}"
            + (f", because a EUR {BROKERS[worst['broker']].commission_min:.2f} minimum "
               f"commission is {BROKERS[worst['broker']].commission_min / euros * 100:.1f}% "
               f"of a trade this size."
               if euros and BROKERS[worst['broker']].commission_min else ".")
            + (" This is a EUR-listed line, so no conversion is charged either."
               if currency == "EUR" else
               f" A {currency} line pays "
               f"{BROKERS[best['broker']].fx_pct * 100:.2f}% to convert.")),
    }


def route_sell(ticker: str, euros: float, location: dict,
               price: float | None = None) -> dict:
    """Where the shares are, and therefore where the sell has to happen."""
    where = location.get(ticker, {})
    if not where:
        # The Trading 212 statement cannot be parsed, but it does list its
        # open positions, so those are recorded rather than reported as
        # unknown. Everything else genuinely is unknown and says so.
        known = KNOWN_LOCATION.get(ticker)
        if known:
            return {
                "broker": known,
                "split": [{"broker": known, "shares": None, "share": 100.0,
                           "fractional": BROKERS[known].fractional}],
                "why": (f"Held at {known}. Its statement is a PDF whose numbers "
                        f"cannot be extracted, so the share count here comes from "
                        f"the sheet rather than from an imported trade - check it "
                        f"against the app before selling."),
            }
        return {
            "broker": None,
            "split": [],
            "why": (f"No imported history for {ticker}, so where it is held is not "
                    f"known. Import the statement covering it, or check all three "
                    f"accounts before placing anything."),
        }

    total = sum(where.values())
    split = [{"broker": b, "shares": round(s, 4),
              "share": round(100 * s / total, 1),
              "fractional": BROKERS[b].fractional}
             for b, s in sorted(where.items(), key=lambda kv: -kv[1])]

    if len(split) == 1:
        only = split[0]
        return {
            "broker": only["broker"],
            "split": split,
            "why": (f"All {total:,.4g} shares are at {only['broker']}"
                    + ("" if only["fractional"]
                       else ", which trades whole shares only.")),
        }

    fractional = [s["broker"] for s in split if s["fractional"]]
    whole_only = [s["broker"] for s in split if not s["fractional"]]
    if fractional and whole_only:
        constraint = (f" Only the {' and '.join(fractional)} "
                      f"{'legs' if len(fractional) > 1 else 'leg'} can be a fraction; "
                      f"{' and '.join(whole_only)} trades whole shares.")
    elif whole_only:
        constraint = " Every venue here trades whole shares only."
    else:
        constraint = " Both venues allow fractions."
    return {
        "broker": split[0]["broker"],
        "split": split,
        "why": (f"Held across {len(split)} accounts — "
                + ", ".join(f"{s['shares']:,.4g} at {s['broker']}" for s in split)
                + f". Selling the position is {len(split)} orders, not one."
                + constraint),
    }
