"""After-tax compounding under Irish rules.

Every other number in this project is pre-tax, and for an Irish investor
that is the single largest thing left out. The rules are unusually
asymmetric here - they do not merely reduce returns, they change which
asset is better - so a pre-tax optimiser can confidently recommend the
worse holding.

Three wrappers, three different regimes:

  Shares      33% CGT, paid only when you actually sell. Losses offset
              other gains, and the first EUR 1,270 of gains each year is
              exempt. Deferral is the quiet advantage: an unrealised gain
              keeps compounding on money the state has not taken yet.

  UCITS ETFs  38% exit tax from January 2026, down from 41%. Crucially a
              disposal is *deemed* to happen every eight years whether or
              not you sell, so the tax is taken out of the compounding
              base three times over a twenty-four year horizon. No loss
              relief, and no annual exemption.

  Pension     Contributions get relief at your marginal income tax rate,
              growth is untaxed, and only drawdown is taxed - with 25%
              available as a tax-free lump sum. At a 40% marginal rate a
              euro in the pension costs 60 cent, which is a 67% return
              before the fund has done anything at all.

The consequence runs against the usual advice: a diversified ETF can be
the better pre-tax holding and the worse after-tax one, purely because of
deemed disposal. That is worth stating explicitly rather than leaving in a
spreadsheet.

Rates current as at August 2026. Tax rules change; this is arithmetic, not
tax advice, and a Revenue-registered adviser is the right check before
acting on any of it.
"""

from __future__ import annotations

from dataclasses import dataclass

CGT_RATE = 0.33
CGT_EXEMPTION = 1_270.0
EXIT_TAX_RATE = 0.38          # 41% before January 2026
DEEMED_DISPOSAL_YEARS = 8
PENSION_LUMP_SUM_FREE = 0.25

SHARE, FUND, PENSION = "share", "fund", "pension"


@dataclass(frozen=True)
class Wrapper:
    kind: str
    rate: float
    label: str
    note: str


WRAPPERS = {
    SHARE: Wrapper(SHARE, CGT_RATE, "Shares (CGT)",
                   "33%, only when you sell, losses relievable"),
    FUND: Wrapper(FUND, EXIT_TAX_RATE, "ETFs (exit tax)",
                  "38%, forced every 8 years, no loss relief"),
    PENSION: Wrapper(PENSION, 0.0, "Pension",
                     "relief going in, untaxed growth, taxed on drawdown"),
}


def after_tax_share(gross: float, years: float, rate: float = CGT_RATE) -> float:
    """Annualised return on a share position held throughout and sold once.

    The whole gain is taxed at the end, so the tax never touches the
    compounding base until the day you sell. That deferral is worth more
    the longer the horizon.
    """
    if years <= 0:
        return 0.0
    terminal = (1 + gross) ** years
    net = 1 + (terminal - 1) * (1 - rate)
    return net ** (1 / years) - 1


def after_tax_fund(gross: float, years: float, rate: float = EXIT_TAX_RATE,
                   cycle: int = DEEMED_DISPOSAL_YEARS) -> float:
    """Annualised return on an ETF, with deemed disposal every `cycle` years.

    Each cycle the accumulated gain is taxed and only the remainder carries
    on compounding. Tax paid in year eight is money that never earns
    anything in years nine to thirty-five, which is why this bites far
    harder than the five point rate difference suggests.
    """
    if years <= 0:
        return 0.0
    value, basis, elapsed = 1.0, 1.0, 0.0
    while elapsed < years:
        step = min(cycle, years - elapsed)
        value *= (1 + gross) ** step
        elapsed += step
        if step == cycle and elapsed < years:
            gain = max(0.0, value - basis)
            value -= gain * rate           # tax comes out of the pot
            basis = value                  # and resets the base
    gain = max(0.0, value - basis)
    value -= gain * rate                   # final disposal
    return value ** (1 / years) - 1


def after_tax(kind: str, gross: float, years: float) -> float:
    if kind == FUND:
        return after_tax_fund(gross, years)
    if kind == PENSION:
        return gross                       # untaxed until drawdown
    return after_tax_share(gross, years)


def drag(kind: str, gross: float, years: float) -> float:
    """Annual percentage points lost to tax in this wrapper."""
    return gross - after_tax(kind, gross, years)


def classify(ticker: str, quote_type: str | None = None) -> str:
    """Which regime a holding falls under.

    Errs toward FUND when unsure, because the fund regime is the harsher
    one and understating tax is the more expensive mistake.
    """
    if quote_type and quote_type.upper() in ("ETF", "MUTUALFUND"):
        return FUND
    if quote_type and quote_type.upper() == "EQUITY":
        return SHARE
    return SHARE


def compare(gross: float, years: float, amount: float = 10_000.0) -> list[dict]:
    """The same money, the same gross return, in each wrapper."""
    out = []
    for kind in (SHARE, FUND, PENSION):
        net = after_tax(kind, gross, years)
        out.append({
            "wrapper": WRAPPERS[kind].label,
            "kind": kind,
            "note": WRAPPERS[kind].note,
            "netReturn": round(net * 100, 2),
            "drag": round((gross - net) * 100, 2),
            "terminal": round(amount * (1 + net) ** years, 2),
        })
    return out


#: Age-banded limits on relievable pension contributions, as a share of
#: earnings, on earnings capped at EUR 115,000.
AGE_LIMITS = ((30, 0.15), (40, 0.20), (50, 0.25), (55, 0.30),
              (60, 0.35), (200, 0.40))
EARNINGS_CAP = 115_000.0


def pension_relief_limit(age: int, earnings: float) -> float:
    """The most you can put in this year and still get relief on it."""
    share = next(limit for threshold, limit in AGE_LIMITS if age < threshold)
    return round(min(earnings, EARNINGS_CAP) * share, 2)


def pension_vs_brokerage(amount: float, years: float, gross: float,
                         marginal_rate: float = 0.40) -> dict:
    """The same take-home euro, into a pension or into a brokerage.

    The comparison people get wrong is the starting amount. A euro of
    take-home pay is a euro in the brokerage, but in a pension it is a euro
    plus the relief - at a 40% marginal rate, EUR 600 of take-home becomes
    EUR 1,000 invested before anything has been earned.
    """
    gross_equivalent = amount / (1 - marginal_rate)

    pension_end = gross_equivalent * (1 + gross) ** years
    lump = pension_end * PENSION_LUMP_SUM_FREE
    taxed = (pension_end - lump) * (1 - marginal_rate)
    pension_net = lump + taxed

    share_net = amount * (1 + after_tax_share(gross, years)) ** years
    fund_net = amount * (1 + after_tax_fund(gross, years)) ** years

    return {
        "takeHome": round(amount, 2),
        "grossEquivalent": round(gross_equivalent, 2),
        "marginalRate": marginal_rate,
        "years": years,
        "pension": round(pension_net, 2),
        "shares": round(share_net, 2),
        "etf": round(fund_net, 2),
        "pensionAdvantageVsShares": round(pension_net - share_net, 2),
        "multiple": round(pension_net / share_net, 2) if share_net else 0.0,
    }
