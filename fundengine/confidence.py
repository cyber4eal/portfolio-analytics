"""How much weight a recommendation can actually carry.

Every other module on this project produces a number. This one produces a
reason to distrust it, which is the part that is usually left off.

The motivation is specific. The optimiser will happily emit "sell 46 APLD"
whether that instruction rests on ten years of data and four models agreeing
or on eighteen months and one model with a shrug. Those are not the same
recommendation, and presenting them identically is how a tool trains you to
either follow everything or ignore everything.

So each order carries a score out of 100 built from five things that can be
checked, never from how strong the conclusion feels:

  agreement   do the four independent optimisations want the same direction
  edge        is the expected gain bigger than what the trade costs to place
  data        is the share count reconciled and the cost basis real
  history     how much overlapping return history the estimate rests on
  trend       does price momentum point the same way

Two of them can also veto. A position whose share count does not reconcile
is capped low no matter how good the arithmetic looks, because an order
sized off an unknown count is the one error here that costs real money. A
book with under two years of history can never reach "high", because the
covariance matrix behind the advice has not seen a full cycle.
"""

from __future__ import annotations

BANDS = ((75, "high"), (55, "moderate"), (35, "low"), (0, "speculative"))

#: The four optimisations in publish's `theories`, excluding "current".
THEORIES = ("growth", "sharpe", "minvar", "parity")


def _band(score: float) -> str:
    for floor, name in BANDS:
        if score >= floor:
            return name
    return "speculative"


def theory_agreement(ticker: str, side: str, theories: dict,
                     current_pct: float) -> tuple[float, str]:
    """How many independent optimisations want this direction.

    Each theory is a different objective - maximise growth, maximise return
    per unit of risk, minimise risk, equalise risk - so agreement between
    them is not four confirmations of one idea. It is the closest thing this
    project has to an out-of-sample check.
    """
    votes, total = 0, 0
    for name in THEORIES:
        weights = (theories.get(name) or {}).get("weights")
        if weights is None:
            continue
        total += 1
        target = float(weights.get(ticker, 0.0))
        wants_less = target < current_pct - 0.5
        wants_more = target > current_pct + 0.5
        if (side == "sell" and wants_less) or (side == "buy" and wants_more):
            votes += 1
    if not total:
        return 0.0, "no optimisation converged, so nothing corroborates this"
    share = votes / total
    points = {1.0: 30.0, 0.75: 20.0}.get(round(share, 2), 8.0 if share >= 0.5 else 0.0)
    return points, (f"{votes} of {total} optimisations want the same direction"
                    if votes else
                    f"none of the {total} optimisations want this direction")


def edge_vs_cost(annual_gain: float, trade_cost: float) -> tuple[float, str]:
    """A gain smaller than the cost of capturing it is not a gain."""
    if trade_cost <= 0:
        return 20.0, "no dealing cost at this venue, so the whole edge is kept"
    ratio = annual_gain / trade_cost
    if ratio >= 5:
        return 20.0, f"the expected gain is {ratio:.0f}x what the trade costs to place"
    if ratio >= 2:
        return 12.0, f"the expected gain is {ratio:.1f}x the dealing cost"
    if ratio >= 1:
        return 5.0, f"the expected gain only just covers the {ratio:.1f}x dealing cost"
    return -10.0, (f"the dealing cost is {1 / ratio:.1f}x the expected annual gain, "
                   f"so placing this loses money on arithmetic alone")


def data_quality(reconciled: bool, basis_known: bool,
                 price_age_hours: float | None) -> tuple[float, str, bool]:
    """Returns points, a note, and whether to veto a high score."""
    points, notes = 0.0, []
    if reconciled:
        points += 10
        notes.append("share count reconciles with the statements")
    else:
        notes.append("SHARE COUNT DOES NOT RECONCILE - the size of this order is a guess")
    if basis_known:
        points += 5
        notes.append("cost basis is real, not an opening estimate")
    else:
        notes.append("cost basis is an opening estimate")
    if price_age_hours is not None and price_age_hours <= 36:
        points += 5
    elif price_age_hours is not None:
        notes.append(f"prices are {price_age_hours / 24:.0f} days old")
    return points, "; ".join(notes), not reconciled


def history_depth(years: float) -> tuple[float, str, bool]:
    if years >= 10:
        return 15.0, f"{years:.0f} years of overlapping history", False
    if years >= 5:
        return 11.0, f"{years:.1f} years of overlapping history", False
    if years >= 3:
        return 7.0, f"{years:.1f} years of history - one cycle at best", False
    if years >= 1.5:
        return 3.0, (f"only {years:.1f} years of overlapping history, all of it "
                     f"a rising market"), True
    return 0.0, f"under {max(years, 0):.1f} years of history - the estimate is barely informed", True


def trend_agreement(side: str, vs_200d: float | None,
                    momentum: float | None) -> tuple[float, str]:
    """Momentum is not why the trade exists - the target weight is - but a
    trade placed against the trend tends to be placed again next month."""
    signals = []
    if vs_200d is not None:
        signals.append(vs_200d)
    if momentum is not None:
        signals.append(momentum)
    if not signals:
        return 7.0, "no trend reading available"
    average = sum(signals) / len(signals)
    with_trade = (average < 0) if side == "sell" else (average > 0)
    if abs(average) < 2:
        return 7.0, "price is flat against its trend, so timing adds nothing"
    if with_trade:
        return 15.0, f"price momentum ({average:+.0f}%) points the same way"
    return 0.0, (f"price momentum ({average:+.0f}%) points the other way - "
                 f"you would be {'selling weakness' if side == 'sell' else 'buying strength'}")


def score(ticker: str, side: str, *, theories: dict, current_pct: float,
          annual_gain: float, trade_cost: float, reconciled: bool,
          basis_known: bool, history_years: float,
          price_age_hours: float | None = None,
          vs_200d: float | None = None,
          momentum: float | None = None) -> dict:
    parts = []

    points, why = theory_agreement(ticker, side, theories, current_pct)
    parts.append(("Agreement", points, 30.0, why))

    points, why = edge_vs_cost(annual_gain, trade_cost)
    parts.append(("Edge over cost", points, 20.0, why))

    points, why, veto_data = data_quality(reconciled, basis_known, price_age_hours)
    parts.append(("Data", points, 20.0, why))

    points, why, veto_history = history_depth(history_years)
    parts.append(("History", points, 15.0, why))

    points, why = trend_agreement(side, vs_200d, momentum)
    parts.append(("Trend", points, 15.0, why))

    total = max(0.0, min(100.0, sum(p for _, p, _, _ in parts)))
    caps = []
    if veto_data:
        total = min(total, 34.0)
        caps.append("capped: the share count does not reconcile, so the size is unverified")
    if veto_history:
        total = min(total, 70.0)
        caps.append("capped below high: under three years of overlapping history")

    return {
        "score": round(total),
        "band": _band(total),
        "parts": [{"name": n, "points": round(p), "outOf": o, "why": w}
                  for n, p, o, w in parts],
        "caps": caps,
        "ceiling": ("CAPM sets the expected returns, so none of this can see a "
                    "stock-picking edge. It measures how well-supported the "
                    "allocation is, not whether the company is good."),
    }
