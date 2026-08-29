"""Options and CFDs, scanned and ranked into one concrete recommendation.

The rest of this project decides how much of a thing to own. This module
answers a different question that gets asked anyway: if you want convexity,
which exact contract, at which broker, at what size, and how sure is it.

Three things make this honest rather than a screen full of Greeks.

**The premium here is modelled, not quoted.** There is no options feed in
this project, so every price is Black-Scholes at the underlying's own
realised volatility plus a volatility risk premium, because implied trades
persistently above realised and that gap is precisely the option seller's
income. Pricing at realised alone would invent an edge that does not exist.
Even with the haircut, the number to type into a ticket is the offer on the
day, and it will differ.

**The ranking is by growth, not by expected return.** A far out-of-the-money
call always has the highest expected return on the screen and always
deserves the smallest position, because the mean lives in a tail you get one
draw from. Sizing each candidate at its own Kelly fraction and ranking on
what that contributes to compound growth is the only ordering that does not
reward lottery tickets for being lottery tickets.

**Access is a hard constraint, not a footnote.** Neither Revolut nor
Trading 212 nor Davy sells listed options to an Irish retail client. A
recommendation you cannot place is not a recommendation, so the broker you
would actually need is named and the score is reduced for it.
"""

from __future__ import annotations

import math

RISK_FREE = 0.025

#: Implied volatility trades above realised almost all of the time - that
#: spread is the volatility risk premium and it is what option sellers are
#: paid. Three points is a conservative single-name figure; pricing at
#: realised would show an edge that belongs to the other side of the trade.
VOL_RISK_PREMIUM = 0.03

#: US listed options come in lots of 100 shares.
CONTRACT_SIZE = 100

#: Underlyings with an options chain deep enough for a retail order to fill
#: near the mid. Asserted from listing status, not measured - this project
#: has no options feed - and everything outside it is excluded by name with
#: the reason, which is more useful than quietly dropping it.
OPTIONABLE = {
    "AMZN", "NVDA", "PLTR", "TSLA", "AMD", "GOOGL", "AVGO", "ANET",
    "MSTR", "RIVN", "AGNC", "ARR", "AEM",
}
NOT_OPTIONABLE_WHY = {
    "SPCX": "a private-market vehicle, so there is no listed chain at all",
    "RR": "a microcap; any chain on it is too thin to fill near the mid",
    "NNE": "small cap, chain too thin to trade at a sensible spread",
    "EADSY": "an ADR - the liquid chain is on the Paris line, not this one",
    "BYDDY": "an ADR - the liquid chain is in Hong Kong, not this one",
    "IEMA.AS": "a European UCITS ETF; retail options on these effectively do not exist",
    "SPYL.DE": "a European UCITS ETF; retail options on these effectively do not exist",
    "VUAA.DE": "a European UCITS ETF; retail options on these effectively do not exist",
    "RHM.DE": "the liquid chain is on Eurex, which is a different account again",
}

EXPIRIES_MONTHS = (3, 6, 12, 18, 24)
STRIKE_MULTIPLES = (0.90, 1.00, 1.10, 1.20, 1.30, 1.50)

#: Where these can actually be bought, checked 2026-08-29.
VENUES = {
    "options": {
        "recommended": "Interactive Brokers Ireland",
        "why": ("The only broker an Irish retail client can realistically use for a "
                "full US options chain. Roughly USD 0.65 a contract, real Greeks, "
                "and the account has to be opened before any of this is placeable."),
        "alternative": ("Saxo carries a curated US and European menu at USD 1.25-3.00 "
                        "a contract, which on a single contract is the whole edge."),
        "not_available": {
            "Revolut": "US stocks and ETFs only - no options product",
            "Trading 212": "no options; the leveraged product here is a CFD, which is different",
            "Davy": "full-service Irish broker; listed options are not a retail offering",
        },
    },
    "cfd": {
        "recommended": "Trading 212 CFD account",
        "why": ("You already hold an account there and the CFD side is separate from "
                "Invest. It is the only CFD venue among the three."),
        "warning": ("Trading 212's own published figure is that 76-77% of retail CFD "
                    "accounts lose money, recalculated quarterly under the ESMA rule. "
                    "ESMA's own range across the EU is 74-89%. That is not a warning "
                    "label, it is the base rate for the product."),
    },
}

#: What a retail CFD costs to hold, annualised on the full notional. Charged
#: daily on the borrowed part whether the position is right or not.
CFD_FINANCING = 0.05


def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def black_scholes_call(spot: float, strike: float, years: float,
                       vol: float) -> float:
    if not (spot > 0 and strike > 0 and years > 0 and vol > 0):
        return float("nan")
    sq = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (RISK_FREE + vol * vol / 2) * years) / sq
    d2 = d1 - sq
    return spot * _norm_cdf(d1) - strike * math.exp(-RISK_FREE * years) * _norm_cdf(d2)


def _lognormal_nodes(spot: float, years: float, vol: float, drift: float,
                     count: int = 801):
    """Terminal prices and their probabilities, on a grid wide enough that
    the tail the whole trade depends on is actually inside it."""
    sigma = vol * math.sqrt(years)
    mu = math.log(spot) + (drift - vol * vol / 2) * years
    lo, hi = -7.0, 7.0
    step = (hi - lo) / (count - 1)
    zs = [lo + i * step for i in range(count)]
    density = [math.exp(-z * z / 2) / math.sqrt(2 * math.pi) for z in zs]
    total = sum(density) * step
    return ([math.exp(mu + sigma * z) for z in zs],
            [d * step / total for d in density])


def _kelly(returns: list[float], probabilities: list[float]) -> float:
    """The fraction of the book that maximises expected log wealth.

    Golden section rather than a solver, because there is no scipy on this
    machine and the function is unimodal on (0, 1). Bounded strictly below 1
    since a total loss is a real outcome here and log(0) is not a number a
    position size can be derived from.
    """
    def growth(f: float) -> float:
        total = 0.0
        for r, p in zip(returns, probabilities):
            wealth = 1 + f * r
            if wealth <= 1e-12:
                return -1e9
            total += p * math.log(wealth)
        return total

    lo, hi = 0.0, 0.98
    phi = (math.sqrt(5) - 1) / 2
    a, b = hi - phi * (hi - lo), lo + phi * (hi - lo)
    fa, fb = growth(a), growth(b)
    for _ in range(80):
        if fa < fb:
            lo, a, fa = a, b, fb
            b = lo + phi * (hi - lo)
            fb = growth(b)
        else:
            hi, b, fb = b, a, fa
            a = hi - phi * (hi - lo)
            fa = growth(a)
    best = (a + b) / 2
    return best if growth(best) > 0 else 0.0


def price_call(spot: float, strike: float, years: float, vol: float,
               drift: float) -> dict:
    """One contract, priced to pay and evaluated to hold."""
    premium = black_scholes_call(spot, strike, years, vol + VOL_RISK_PREMIUM)
    if not (premium > 0):
        return {}

    prices, probabilities = _lognormal_nodes(spot, years, vol, drift)
    payoffs = [max(0.0, s - strike) for s in prices]
    returns = [p / premium - 1 for p in payoffs]

    expected = sum(p * q for p, q in zip(payoffs, probabilities))
    p_itm = sum(q for s, q in zip(prices, probabilities) if s > strike)
    breakeven = strike + premium
    p_profit = sum(q for s, q in zip(prices, probabilities) if s > breakeven)

    fraction = _kelly(returns, probabilities)
    growth = 0.0
    if fraction > 0:
        growth = sum(q * math.log(1 + fraction * r)
                     for r, q in zip(returns, probabilities)) / years

    return {
        "premium": round(premium, 2),
        "premiumPctOfSpot": round(premium / spot * 100, 2),
        "breakeven": round(breakeven, 2),
        "moveNeededPct": round((breakeven / spot - 1) * 100, 1),
        "pInTheMoney": round(p_itm * 100, 1),
        "pProfit": round(p_profit * 100, 1),
        "pWorthless": round((1 - p_itm) * 100, 1),
        "expectedPayoff": round(expected, 2),
        "expectedReturnPct": round((expected / premium - 1) * 100, 1),
        "kellyPct": round(fraction * 100, 2),
        # A quarter of Kelly is the usual practice on a fat-tailed payoff,
        # and full Kelly on something that goes to zero most of the time is
        # a position size nobody survives the variance of.
        "quarterKellyPct": round(fraction * 100 / 4, 2),
        "growthAtKellyPct": round(growth * 100, 2),
        "leverage": round(spot / premium, 1),
    }


def scan(candidates: dict, book_value: float, fx: float = 1.0) -> dict:
    """Every optionable holding across the expiry and strike grid.

    `candidates` maps ticker to {spot, vol, beta, drift, name, currency}.
    Spot is in the underlying's own currency, because that is the number on
    the ticket; the euro figures are conversions of it.
    """
    rows, excluded = [], []
    for ticker, info in sorted(candidates.items()):
        if ticker not in OPTIONABLE:
            excluded.append({"ticker": ticker,
                             "why": NOT_OPTIONABLE_WHY.get(
                                 ticker, "no liquid listed chain on this line")})
            continue
        spot, vol, drift = info["spot"], info["vol"], info["drift"]
        if not (spot > 0 and vol > 0):
            continue
        for months in EXPIRIES_MONTHS:
            for multiple in STRIKE_MULTIPLES:
                priced = price_call(spot, spot * multiple, months / 12, vol, drift)
                if not priced or priced["kellyPct"] <= 0:
                    continue
                size = _size(priced, book_value, info, fx)
                rows.append({
                    "ticker": ticker, "name": info.get("name", ticker),
                    "currency": info.get("currency", "USD"),
                    "months": months, "strikeMultiple": round(multiple * 100),
                    "spot": round(spot, 2), "strike": round(spot * multiple, 2),
                    "vol": round(vol * 100, 1), "beta": round(info.get("beta", 0), 2),
                    "drift": round(drift * 100, 1),
                    **priced, **size,
                })

    # Growth at its own best size is the ranking that does not reward a
    # lottery ticket for having the biggest expected return on the page.
    rows.sort(key=lambda r: (-r["growthAtKellyPct"], -r["pProfit"]))

    # The distinction that decides whether any of this is usable. A contract
    # is 100 shares and cannot be split, so on a small book the best trade on
    # the screen is routinely twenty times the size the arithmetic allows.
    # Reporting only the winner would be recommending something unplaceable.
    placeable = [r for r in rows if r["affordable"]]
    best = rows[0] if rows else None
    needed = None
    if best and not best["affordable"] and best["quarterKellyPct"] > 0:
        needed = round(best["perContractEur"] / (best["quarterKellyPct"] / 100), 2)

    # And the smallest bet that can be made at all, which on a book this
    # size is the number that actually decides the answer.
    cheapest = min(rows, key=lambda r: r["perContractEur"]) if rows else None
    if cheapest and book_value:
        cheapest = dict(cheapest,
                        oneContractPctOfBook=round(
                            cheapest["perContractEur"] / book_value * 100, 1))

    return {"best": best,
            "cheapest": cheapest,
            "bestPlaceable": placeable[0] if placeable else None,
            "bookValueNeededForBest": needed,
            "placeableCount": len(placeable),
            "shortlist": rows[:8],
            "shortlistPlaceable": placeable[:6],
            "excluded": excluded,
            "assumptions": {
                "volRiskPremiumPoints": round(VOL_RISK_PREMIUM * 100, 1),
                "riskFreePct": round(RISK_FREE * 100, 1),
                "contractSize": CONTRACT_SIZE,
                "scanned": len(rows),
            }}


def _size(priced: dict, book_value: float, info: dict, fx: float) -> dict:
    """Whole contracts at a quarter of Kelly, and what they cost.

    Rounded down, because a contract is indivisible and rounding up turns a
    disciplined size into a bigger bet than the arithmetic asked for.
    """
    budget_eur = book_value * priced["quarterKellyPct"] / 100
    per_contract_native = priced["premium"] * CONTRACT_SIZE
    per_contract_eur = per_contract_native / fx if fx else per_contract_native
    contracts = int(budget_eur // per_contract_eur) if per_contract_eur > 0 else 0
    return {
        "budgetEur": round(budget_eur, 2),
        "perContractNative": round(per_contract_native, 2),
        "perContractEur": round(per_contract_eur, 2),
        "contracts": contracts,
        "costEur": round(contracts * per_contract_eur, 2),
        "controlsEur": round(contracts * CONTRACT_SIZE * priced["premium"]
                             * priced["leverage"] / (fx or 1), 2),
        "affordable": contracts >= 1,
    }


def cfd(mu: float, sigma: float, book_value: float) -> dict:
    """What a CFD does to compound growth, at each leverage a retail account
    is allowed. ESMA caps retail equity CFDs at 5:1, which is already well
    past the point where this book stops compounding."""
    rows = []
    for leverage in (2, 3, 5):
        gross = leverage * mu
        drag = (leverage * sigma) ** 2 / 2
        financing = (leverage - 1) * CFD_FINANCING
        growth = gross - drag - financing
        # Margin is wiped by a move of 1/leverage against you, and the
        # probability of touching that at some point is what closes accounts.
        wipeout_move = 1 / leverage
        rows.append({
            "leverage": leverage,
            "grossPct": round(gross * 100, 1),
            "dragPct": round(drag * 100, 1),
            "financingPct": round(financing * 100, 1),
            "growthPct": round(growth * 100, 1),
            "wipeoutMovePct": round(wipeout_move * 100, 1),
            "unlevered": round((mu - sigma ** 2 / 2) * 100, 1),
        })
    return {"rows": rows, "financingPct": round(CFD_FINANCING * 100, 1),
            "venue": VENUES["cfd"],
            "bookVol": round(sigma * 100, 1), "bookDrift": round(mu * 100, 1)}


def confidence(best: dict | None) -> dict:
    """Deliberately capped. Every premium here is modelled rather than
    quoted, so no options recommendation on this page may present itself as
    well-supported, however good the arithmetic downstream of it looks."""
    parts = [
        {"name": "Pricing", "points": 8, "outOf": 25,
         "why": ("premium is Black-Scholes at realised volatility plus "
                 f"{VOL_RISK_PREMIUM * 100:.0f} points, not a quote - the offer on the "
                 "day is the number that counts")},
        {"name": "Access", "points": 5, "outOf": 20,
         "why": ("none of your three brokers sells listed options, so this needs an "
                 "account you do not have yet")},
        {"name": "Drift", "points": 10, "outOf": 25,
         "why": ("expected return rests on a CAPM drift, which cannot see any edge and "
                 "is the single assumption the whole answer turns on")},
        {"name": "Sizing", "points": 20, "outOf": 20,
         "why": "quarter-Kelly on the modelled distribution, rounded down to whole contracts"},
        {"name": "Structure", "points": 10, "outOf": 10,
         "why": "loss is capped at the premium, which is the one part of this that is certain"},
    ]
    score = sum(p["points"] for p in parts)
    return {
        "score": score,
        "band": "moderate" if score >= 55 else "low" if score >= 35 else "speculative",
        "parts": parts,
        "caps": ["capped: no options price here is a live quote, so this can never "
                 "read as high confidence"],
        "ceiling": ("This ranks contracts against each other under one set of "
                    "assumptions. It is not a claim that buying one is better than "
                    "buying none - the panel above this says the opposite for the "
                    "target you set."),
    }
