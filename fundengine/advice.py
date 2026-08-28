"""Deterministic buy and sell candidates, and position sizing.

This is the portfolio-agent's judgement made explicit. The agent asks a
model for conviction and then runs arithmetic on it; the arithmetic is the
part worth keeping in a page, because it is reproducible and free. So
conviction is an input here - a slider you set - and everything downstream
of it is a formula you can check.

Nothing here is a recommendation to trade. Every candidate is a mechanical
consequence of the numbers: this position contributes more risk than its
weight, that pair is 0.9 correlated, this fund would cut volatility. Why
that matters, and whether to act, stays with you.

The sizing formula is ported from the agent's size_position.py so the site
and the Telegram bot cannot quietly disagree about the same question.
"""

from __future__ import annotations

import os

MAX_NAME_WEIGHT = float(os.environ.get("MAX_NAME_WEIGHT", 0.15))
MONTHLY_BUY_CASH_EUR = float(os.environ.get("MONTHLY_BUY_CASH_EUR", 400))
MONTHLY_FREE_SELLS = int(os.environ.get("MONTHLY_FREE_SELLS", 5))

#: A position whose share of portfolio risk exceeds its weight by this
#: multiple is doing more damage per euro than its size suggests.
RISK_WEIGHT_RATIO = 1.5
#: Above this, two holdings are close to one position with two rows.
REDUNDANT_CORRELATION = 0.85


def size_position(side: str, conviction: float, price: float,
                  current_weight: float = 0.0, position_value: float = 0.0,
                  position_shares: float | None = None,
                  speculative: bool = False,
                  cash_budget: float = MONTHLY_BUY_CASH_EUR) -> dict:
    """Conviction -> a concrete euro and share suggestion.

    BUY is cash-limited and damped by how close the name already is to the
    single-name cap, so conviction cannot talk you into pouring more into
    something already at 15%. SELL is a fraction of what you hold, because
    a sell is bounded by the position rather than by cash.
    """
    conviction_fraction = max(0.0, min(1.0, conviction / 10.0))
    out = {"side": side, "conviction": conviction, "price": price,
           "speculative": speculative}

    if side == "buy":
        headroom = max(0.0, min(1.0, (MAX_NAME_WEIGHT - current_weight) / MAX_NAME_WEIGHT))
        euros = cash_budget * conviction_fraction * headroom
        if speculative:
            euros *= 0.5
        euros = round(euros / 25.0) * 25.0        # retail-sized, nearest EUR25
        out.update({
            "currentWeightPct": round(current_weight * 100, 2),
            "maxNameWeightPct": round(MAX_NAME_WEIGHT * 100, 1),
            "headroomFactor": round(headroom, 2),
            "cashBudget": cash_budget,
            "euros": euros,
            "shares": round(euros / price, 2) if price else None,
            "note": ("Sized at half for a speculative name. " if speculative else "") +
                    ("New position." if current_weight == 0 else "Adds to an existing position."),
        })
        if current_weight and headroom < 0.15:
            out["flag"] = (f"Already {current_weight * 100:.1f}% of the book against a "
                           f"{MAX_NAME_WEIGHT * 100:.0f}% cap — almost no room to add.")
    elif side == "sell":
        fraction = 1.0 if conviction >= 10 else conviction_fraction
        euros = round(position_value * fraction, 2)
        out.update({
            "positionValue": round(position_value, 2),
            "positionShares": position_shares,
            "trimFraction": round(fraction, 2),
            "euros": euros,
            "shares": int(position_shares * fraction) if position_shares else None,
            "freeSellsPerMonth": MONTHLY_FREE_SELLS,
            "note": f"Trims about {fraction * 100:.0f}% of the position. Uses one of "
                    f"the {MONTHLY_FREE_SELLS} free sells a month; past that they cost.",
        })
    else:
        out["error"] = f"unknown side {side!r}"
    return out


def sell_candidates(risk_contributions: list[dict], correlations: dict,
                    holdings: list[dict], total: float) -> list[dict]:
    """Positions the numbers argue with, most arguable first.

    Three mechanical reasons, and a position can carry more than one:
    it costs more risk than its weight, it duplicates something else you
    hold, or it is past the single-name cap.
    """
    by_ticker = {h["ticker"]: h for h in holdings if h.get("tradable")}
    flagged: dict[str, dict] = {}

    def flag(ticker: str, reason: str, detail: str, score: float) -> None:
        row = flagged.setdefault(ticker, {
            "ticker": ticker,
            "name": by_ticker.get(ticker, {}).get("name", ticker),
            "value": round(by_ticker.get(ticker, {}).get("value_eur", 0), 2),
            "reasons": [], "score": 0.0,
        })
        row["reasons"].append({"reason": reason, "detail": detail})
        row["score"] += score

    for row in risk_contributions:
        if row["weight"] <= 0:
            continue
        ratio = row["riskShare"] / row["weight"]
        if ratio >= RISK_WEIGHT_RATIO:
            flag(row["ticker"], "Costs more risk than it is worth",
                 f"{row['riskShare']:.1f}% of portfolio risk on a {row['weight']:.1f}% "
                 f"weight — {ratio:.1f}x. Its own volatility is {row['vol']:.0f}%.",
                 ratio)
        if row["weight"] > MAX_NAME_WEIGHT * 100:
            flag(row["ticker"], "Past the single-name cap",
                 f"{row['weight']:.1f}% against a {MAX_NAME_WEIGHT * 100:.0f}% soft cap.",
                 2.0)

    tickers = correlations.get("tickers", [])
    matrix = correlations.get("matrix", [])
    for i, a in enumerate(tickers):
        for j in range(i + 1, len(tickers)):
            value = matrix[i][j]
            if value < REDUNDANT_CORRELATION:
                continue
            b = tickers[j]
            weight_a = by_ticker.get(a, {}).get("value_eur", 0) / (total or 1) * 100
            weight_b = by_ticker.get(b, {}).get("value_eur", 0) / (total or 1) * 100
            smaller = a if weight_a < weight_b else b
            other = b if smaller == a else a
            flag(smaller, "Duplicates another holding",
                 f"{value:.2f} correlated with {other}. Together they are "
                 f"{weight_a + weight_b:.1f}% of the book behaving like one position.",
                 1.5)

    out = sorted(flagged.values(), key=lambda r: -r["score"])
    for row in out:
        row["score"] = round(row["score"], 2)
    return out


def buy_candidates(additions: list[dict], funds: list[dict],
                   exposure: dict, allocation: float) -> list[dict]:
    """Funds ranked by what they would do to the book, with the reason stated.

    `additions` is already sorted by volatility impact, so this mostly adds
    the why: a fund that cuts risk because it is uncorrelated is a different
    proposition from one that cuts risk by being tame.
    """
    by_ticker = {f["ticker"]: f for f in funds}
    out = []
    for row in additions[:8]:
        fund = by_ticker.get(row["ticker"], {})
        reasons = []
        if row["vol_change"] < -0.5:
            reasons.append(f"cuts book volatility by {abs(row['vol_change']):.1f} points")
        if abs(row["correlation"]) < 0.3:
            reasons.append(f"barely correlated with what you hold ({row['correlation']:.2f})")
        if row["beta_change"] < -0.05:
            reasons.append(f"pulls beta down {abs(row['beta_change']):.2f}")
        asset = fund.get("asset", "")
        if asset and asset not in exposure.get("sectors", {}):
            reasons.append(f"adds {asset.lower()} exposure the book has none of")
        if not reasons:
            continue
        out.append({
            "ticker": row["ticker"],
            "name": fund.get("name", row["ticker"]),
            "asset": asset,
            "allocationPct": round(allocation * 100, 1),
            "volChange": row["vol_change"],
            "betaChange": row["beta_change"],
            "correlation": row["correlation"],
            "reasons": reasons,
        })
    return out


def concentration_notes(exposure: dict, effective_holdings: float,
                        n_lines: int) -> list[str]:
    """The structural observations that are true regardless of any one name."""
    notes = []
    countries = exposure.get("countries", {})
    top_country, top_weight = next(iter(countries.items()), ("", 0))
    if top_weight > 60:
        notes.append(
            f"{top_weight:.0f}% of the book sits in {top_country}. That is a "
            "single-country bet whether or not it was chosen as one.")

    currencies = exposure.get("currencies", {})
    non_eur = 100 - currencies.get("EUR", 0)
    if non_eur > 50:
        notes.append(
            f"{non_eur:.0f}% is unhedged non-EUR. You are paid in euro and the "
            "mortgage is in euro, so that currency exposure is a live risk, not "
            "a rounding error.")

    sectors = exposure.get("sectors", {})
    top_sector, sector_weight = next(iter(sectors.items()), ("", 0))
    if sector_weight > 35:
        notes.append(
            f"{sector_weight:.0f}% is in {top_sector.lower()}. Sector concentration "
            "is the kind that looks like skill in a bull market.")

    if effective_holdings < n_lines * 0.5:
        notes.append(
            f"{n_lines} lines that behave like {effective_holdings:.1f} equally-sized "
            "ones — the diversification is thinner than the line count suggests.")
    return notes
