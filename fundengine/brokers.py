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

TRADING212, DAVY = "Trading 212", "Davy"


@dataclass(frozen=True)
class Broker:
    name: str
    fractional: bool
    commission_pct: float
    commission_min: float
    fx_pct: float
    note: str


BROKERS = {
    TRADING212: Broker(TRADING212, True, 0.0, 0.0, 0.0015,
                       "No commission; 0.15% on currency conversion. Fractions allowed."),
    DAVY: Broker(DAVY, False, 0.005, 14.99, 0.0,
                 "Commission with a floor, so small trades are expensive. Whole shares only."),
}


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
        note = (trade.get("note") or "").lower()
        broker = (DAVY if "davy" in note
                  else TRADING212 if "trading 212" in note or "t212" in note
                  else None)
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


def route_buy(euros: float, currency: str) -> dict:
    """Cheapest venue for a purchase, with both costs shown.

    Almost always the commission-free account, and by a wide margin on the
    sizes traded here - a floor of EUR 14.99 is simply larger than 0.15% of
    anything under EUR 10,000.
    """
    options = [{"broker": name, "cost": cost_at(name, euros, currency),
                "note": BROKERS[name].note}
               for name in BROKERS]
    options.sort(key=lambda o: o["cost"])
    best, worst = options[0], options[-1]
    return {
        "broker": best["broker"],
        "cost": best["cost"],
        "options": options,
        "saving": round(worst["cost"] - best["cost"], 2),
        "why": (
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
        return {
            "broker": None,
            "split": [],
            "why": (f"No imported history for {ticker}, so where it is held is not "
                    f"known. Import the statement covering it, or check both "
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

    return {
        "broker": split[0]["broker"],
        "split": split,
        "why": ("Held across both accounts — "
                + ", ".join(f"{s['shares']:,.4g} at {s['broker']}" for s in split)
                + ". Selling the position is two orders, not one, and only the "
                  "Trading 212 leg can be a fraction."),
    }
