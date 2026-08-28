"""What actually changes the number, ranked by how much.

The instinct with a portfolio is to optimise the holdings, because that is
the part that feels like investing. At a small pot size it is close to the
least important lever available, and the arithmetic says so plainly: a
EUR 19k book earning five points more is EUR 950 a year, while EUR 400 a
month of new money is EUR 4,800. Allocation only overtakes contributions
once the pot is large enough that a percentage of it exceeds a year of
saving - and knowing where that crossover sits is more useful than any
allocation advice given before it.

So this module ranks the levers in euro rather than in percent, over a
stated horizon, and reports them together. Nothing here is clever. It is
arithmetic that is usually left implicit, and leaving it implicit is how
people spend a decade optimising the smallest term.
"""

from __future__ import annotations

from dataclasses import dataclass


def terminal(start: float, monthly: float, annual_return: float,
             years: float) -> float:
    """Future value of a pot plus a monthly contribution."""
    months = int(round(years * 12))
    rate = (1 + annual_return) ** (1 / 12) - 1
    value = start
    for _ in range(months):
        value = value * (1 + rate) + monthly
    return value


@dataclass
class Lever:
    name: str
    change: str
    terminal: float
    gain: float
    note: str

    def as_dict(self) -> dict:
        return {"name": self.name, "change": self.change,
                "terminal": round(self.terminal, 2), "gain": round(self.gain, 2),
                "note": self.note}


def rank(start: float, monthly: float, annual_return: float, years: float,
         fee_saving: float = 0.005, extra_monthly: float = 200.0,
         employer_match: float = 0.0) -> dict:
    """Each lever pulled on its own, from the same starting point."""
    base = terminal(start, monthly, annual_return, years)
    levers = []

    levers.append(Lever(
        "Save more", f"+{extra_monthly:,.0f} a month",
        terminal(start, monthly + extra_monthly, annual_return, years),
        0, "The only lever you fully control, and the largest one until the "
           "pot outgrows a year of saving."))

    levers.append(Lever(
        "Earn more on it", "+2 points of return",
        terminal(start, monthly, annual_return + 0.02, years),
        0, "Two points is a large and uncertain edge. Compare what it buys "
           "against the certain levers before chasing it."))

    levers.append(Lever(
        "Pay less", f"-{fee_saving * 100:.2f}% in charges",
        terminal(start, monthly, annual_return + fee_saving, years),
        0, "Identical to earning more, except it is certain and needs no "
           "skill - the only free lunch on the list."))

    # Framed as the mistake rather than the virtue: it carries a negative
    # number, and "Stay invested: -13,021" reads as though staying invested
    # were the thing costing money.
    levers.append(Lever(
        "A year sat in cash", "one year out of the market",
        terminal(start, monthly, annual_return, years) -
        (terminal(start, monthly, annual_return, years) -
         terminal(start, monthly, annual_return, years - 1)),
        0, "A year out of the market near the start costs a whole year of "
           "compounding at the end, when the pot is largest."))

    if employer_match > 0:
        levers.append(Lever(
            "Take the match", f"+{employer_match:,.0f} a month from your employer",
            terminal(start, monthly + employer_match, annual_return, years),
            0, "Money you are offered and decline by not contributing. "
               "Nothing else on this list returns 100% on day one."))

    for lever in levers:
        lever.gain = lever.terminal - base
    levers.sort(key=lambda l: -l.gain)

    # Where a percentage of the pot starts to beat a year of contributions.
    crossover_years = None
    value = start
    rate = (1 + annual_return) ** (1 / 12) - 1
    for month in range(int(years * 12) + 600):
        growth = value * annual_return
        if growth > monthly * 12:
            crossover_years = round(month / 12, 1)
            break
        value = value * (1 + rate) + monthly

    return {
        "base": round(base, 2),
        "years": years,
        "assumedReturn": annual_return,
        "levers": [l.as_dict() for l in levers],
        "crossoverYears": crossover_years,
        "crossoverValue": round(value, 2) if crossover_years is not None else None,
        "contributionsShare": round(
            100 * (monthly * 12 * years) / base, 1) if base else 0.0,
    }


#: Long-run records, for judging whether a required return is plausible.
BENCHMARKS = (
    ("Global equities, long run", 0.08),
    ("S&P 500, best 10-year stretch", 0.20),
    ("Warren Buffett, 1965-2024", 0.195),
    ("Renaissance Medallion, net, the best record that exists", 0.39),
)


def feasibility(start: float, monthly: float, target: float,
                years: float) -> dict:
    """What annual return a target actually requires, and whether anyone
    has ever managed it.

    Included because a goal is the one input nobody sanity-checks. A number
    that sounds ambitious and a number that is arithmetically impossible
    look identical until the required rate is written down next to what the
    best investors alive have achieved.
    """
    low, high = -0.99, 5.0
    for _ in range(200):
        mid = (low + high) / 2
        if terminal(start, monthly, mid, years) < target:
            low = mid
        else:
            high = mid
    required = (low + high) / 2

    beaten = [name for name, rate in BENCHMARKS if required > rate]
    return {
        "target": target,
        "years": years,
        "start": round(start, 2),
        "monthly": round(monthly, 2),
        "requiredReturn": round(required, 4),
        "requiredPct": round(required * 100, 1),
        "multipleOfStart": round(target / start, 1) if start else None,
        "benchmarks": [{"name": n, "rate": round(r * 100, 1)} for n, r in BENCHMARKS],
        "exceedsAll": len(beaten) == len(BENCHMARKS),
        "verdict": (
            "beyond any sustained record in history" if len(beaten) == len(BENCHMARKS)
            else "beats the long-run market but is within what the best have done"
            if beaten else "within long-run market returns"),
        # What the same horizon reaches at a defensible rate.
        "atEightPercent": round(terminal(start, monthly, 0.08, years), 2),
        "monthlyNeededAtEight": round(_monthly_for(start, target, 0.08, years), 2),
    }


def _monthly_for(start: float, target: float, annual_return: float,
                 years: float) -> float:
    low, high = 0.0, max(target, 1.0)
    for _ in range(200):
        mid = (low + high) / 2
        if terminal(start, mid, annual_return, years) < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2
