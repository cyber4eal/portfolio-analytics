"""Concrete orders: what to trade, at what price, by when, and why now.

A target allocation is not an instruction. "Reduce IEMG to 0%" leaves every
practical question open - at what price, by when, and what it costs to
leave it another month. This module closes those, and each answer is
derived from something real rather than asserted:

  Limit price   Set a band around today's price using the holding's own
                daily volatility. This is not a forecast of direction. It
                is the observation that a stock moving 2% a day will, most
                weeks, touch a price 1% better than the current one, and
                that waiting for it is free if you attach an expiry. The
                expiry is what makes it honest: without one, "wait for a
                better price" becomes never selling a falling position.

  Deadline      Only from real constraints - the free-sell allowance that
                resets at month end, the deposit needed in February, a
                position drifting further past the single-name cap. An
                invented deadline is worse than none, because it spends
                the credibility of the real ones.

  Urgency       From the cost of waiting, in euro a month, computed from
                the growth gap the trade closes. A position costing EUR 40
                a month in foregone compounding is urgent in a way that a
                position costing EUR 2 is not, and saying which is which is
                more useful than a colour.

Nothing here is a prediction. Every number is a consequence of the current
book, its measured volatility, and a calendar.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

TRADING_DAYS = 252

#: How far out to place a limit, in daily standard deviations. Just under
#: one sigma fills within a week about two thirds of the time, which is
#: the point where waiting stops being free.
LIMIT_SIGMA = 0.8
#: And how long to leave it there before taking the market price.
LIMIT_DAYS = 5

URGENCY = ("now", "this week", "this month", "opportunistic")


def _month_end(today: date) -> date:
    return today.replace(day=calendar.monthrange(today.year, today.month)[1])


def _working_days(start: date, count: int) -> date:
    day, added = start, 0
    while added < count:
        day += timedelta(days=1)
        if day.weekday() < 5:
            added += 1
    return day


def limit_price(price: float, daily_vol: float, side: str) -> dict:
    """A limit that improves the fill without waiting forever.

    A sell asks slightly above today; a buy bids slightly below. The size
    of "slightly" is the holding's own daily volatility, so a placid ETF
    gets a tight band and a volatile single stock gets a wide one - the
    same percentage on both would be a rounding error on one and unfillable
    on the other.
    """
    if not price or not daily_vol:
        return {"limit": None, "band": None}
    move = price * daily_vol * LIMIT_SIGMA
    limit = price + move if side == "sell" else price - move
    return {
        "limit": round(limit, 2),
        "bandPct": round(100 * daily_vol * LIMIT_SIGMA, 2),
        "expiryDays": LIMIT_DAYS,
        "rationale": (
            f"{'Ask' if side == 'sell' else 'Bid'} {abs(limit - price) / price * 100:.1f}% "
            f"{'above' if side == 'sell' else 'below'} today, which is {LIMIT_SIGMA:g} "
            f"of this holding's {daily_vol * 100:.1f}% daily move. Most weeks it "
            f"trades through. If it has not filled in {LIMIT_DAYS} working days, "
            f"take the market price - the edge was never large enough to hold out for."),
    }


def cost_of_waiting(euros: float, growth_gap_pp: float) -> float:
    """Euro a month of foregone compounding from leaving a trade undone."""
    return euros * (growth_gap_pp / 100) / 12


def _urgency(monthly_cost: float, over_cap: bool,
             hard_deadline_days: int | None) -> str:
    """Urgency from what is actually forcing the issue.

    Only a real constraint counts. The first version treated a limit
    order's five-day expiry as a deadline, which made every order urgent -
    and a list where everything is urgent ranks nothing. The expiry is a
    parameter of the order, not a reason to place it today.
    """
    if over_cap:
        return "now"
    if hard_deadline_days is not None and hard_deadline_days <= 5:
        return "now"
    if monthly_cost >= 20 or (hard_deadline_days is not None and hard_deadline_days <= 14):
        return "this week"
    if monthly_cost >= 5:
        return "this month"
    return "opportunistic"


def build(plan: dict, prices: dict, vols: dict, weights: dict,
          growth_gap_pp: float, max_name_weight: float = 0.15,
          free_sells: int = 5, goal_date: str | None = None,
          today: date | None = None) -> list[dict]:
    """Turn the rebalance plan into dated, priced orders."""
    today = today or date.today()
    month_end = _month_end(today)
    orders = []

    month = plan.get("thisMonth", {})
    trades = [dict(t, side="sell") for t in month.get("sells", [])] + \
             [dict(t, side="buy") for t in month.get("buys", [])]
    if not trades:
        return orders

    # The gap is closed by the whole plan, so each trade earns the share of
    # it proportional to the money it moves.
    total = sum(t["euros"] for t in trades) or 1.0

    for trade in trades:
        ticker = trade["ticker"]
        price = prices.get(ticker)
        vol = vols.get(ticker, 0.0)
        share = trade["euros"] / total
        monthly_cost = cost_of_waiting(trade["euros"], growth_gap_pp * share * len(trades))

        weight = weights.get(ticker, 0.0)
        over_cap = trade["side"] == "sell" and weight > max_name_weight * 100

        # A hard deadline is something the calendar imposes. The limit's
        # expiry is tracked separately, because it constrains the order and
        # not the decision.
        expiry = _working_days(today, LIMIT_DAYS)
        deadline, reason = None, None
        if trade["side"] == "sell":
            # Free sells do not carry over, so an unused one is gone.
            deadline = month_end
            used = len(month.get("sells", []))
            reason = (f"Free sells reset on {month_end.isoformat()}. You get "
                      f"{free_sells} a month and this plan uses {used}; an unused "
                      f"one is not carried forward, so a sell deferred past month "
                      f"end costs a paid trade rather than a free one.")
        else:
            reason = ("No calendar forces this one. It is funded by the sells "
                      "above, so it happens when they do - there is nothing to "
                      "gain by buying before the cash exists.")

        if over_cap:
            reason = (f"{ticker} is {weight:.1f}% of the book against a "
                      f"{max_name_weight * 100:.0f}% cap. Every day it stays there is "
                      f"concentration you did not choose. ") + reason

        days_left = (deadline - today).days if deadline else None
        orders.append({
            "ticker": ticker,
            "side": trade["side"],
            "euros": trade["euros"],
            "shares": trade.get("shares"),
            "currentPct": trade.get("currentPct"),
            "targetPct": trade.get("targetPct"),
            "price": price,
            **limit_price(price, vol, trade["side"]),
            "deadline": deadline.isoformat() if deadline else None,
            "deadlineDays": days_left,
            "deadlineReason": reason,
            "limitExpires": expiry.isoformat(),
            "costPerMonth": round(monthly_cost, 2),
            "urgency": _urgency(monthly_cost, over_cap, days_left),
            "overCap": over_cap,
            "trend": trade.get("vsAverage200"),
        })

    order = {level: i for i, level in enumerate(URGENCY)}
    orders.sort(key=lambda o: (order[o["urgency"]], -o["costPerMonth"]))
    return orders


def calendar_deadlines(today: date | None = None,
                       goal_date: str | None = None,
                       pension_limit_left: float | None = None) -> list[dict]:
    """Dates that impose themselves, whether or not you act.

    Deliberately short. A list of invented milestones trains you to ignore
    the list, and then the two that matter get ignored with it.
    """
    today = today or date.today()
    out = [{
        "what": "Free sell allowance resets",
        "when": _month_end(today).isoformat(),
        "days": (_month_end(today) - today).days,
        "why": "Five free sells a month, and they do not carry over. If the plan "
               "calls for sells, unused ones are simply lost.",
    }, {
        "what": "Pension contribution year",
        "when": date(today.year, 12, 31).isoformat(),
        "days": (date(today.year, 12, 31) - today).days,
        "why": "Relief is claimed against a tax year. Contribution room not used "
               "by year end is gone - it is the one allowance that never carries "
               "forward and cannot be bought back later."
               + (f" About {pension_limit_left:,.0f} of room is left."
                  if pension_limit_left else ""),
    }]
    if goal_date:
        try:
            deadline = date.fromisoformat(goal_date[:10])
            out.append({
                "what": "Mortgage deposit needed",
                "when": deadline.isoformat(),
                "days": (deadline - today).days,
                "why": "Money needed on this date should already be out of "
                       "anything that can fall. There is no time left for a "
                       "drawdown to recover.",
            })
        except ValueError:
            pass
    return sorted(out, key=lambda r: r["days"])
