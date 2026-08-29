"""Protection: insuring the asset that actually dominates the balance sheet.

Every other module here works on the financial capital - EUR 14k of shares
and a EUR 5.8k pension. At 23 that is the smaller half of the balance sheet
by an order of magnitude. The larger half is human capital: forty-odd years
of earnings that have not happened yet, worth over a million euro in present
value, uninsured, and attached to one body.

That asymmetry is what decides every answer in this module, and it flips the
usual ordering of these products:

  * Life cover pays out when you die. With no dependants and no mortgage,
    nobody suffers a financial loss when you die, so the economic need today
    is close to zero - however cheap the premium is. Cheap cover for a risk
    you do not carry is not a bargain.

  * Income protection pays out when you cannot work. At these ages a long
    disability is several times more likely than death, the loss is the
    entire human capital rather than a lump sum, and it is the only one of
    these that gets income tax relief at the marginal rate.

  * Health insurance in Ireland is not priced on your health, it is priced
    on when you first bought it. Lifetime Community Rating adds 2% for every
    year you are over 34 when you first take inpatient cover, to a maximum
    of 70%, and it sticks for ten years. That makes it a deadline, not a
    decision, and the deadline is computable.

  * Mortgage protection is not optional. Section 126 of the Consumer Credit
    Act 1995 requires the lender to see a policy in place before drawdown,
    with four exemptions - buy-to-let, uninsurable risk, borrower over 50,
    and existing cover that already meets the standard. A 2027 purchase
    means it is already on the calendar.

Premiums here are indicative market rates, not quotes, and they are the
weakest input in the file - flagged as such everywhere they are used. The
structure of the answer does not depend on them: it depends on which risks
are actually carried, and that is arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Lifetime Community Rating, in force since 1 May 2015. First inpatient
#: policy taken at 35 or over carries 2% for each year of age above 34,
#: capped at 70%, applied for ten years. Previous cover is credited.
LCR_FREE_UNTIL_AGE = 35
LCR_LOADING_PER_YEAR = 0.02
LCR_MAX_LOADING = 0.70
LCR_YEARS_APPLIED = 10

#: Income protection premiums qualify for relief at the marginal rate on
#: premiums up to 10% of total income. Life cover and specified illness
#: cover get nothing, which is a bigger difference than most comparisons
#: of the two headline premiums show.
MARGINAL_RATE = 0.40
IP_RELIEF_INCOME_CAP = 0.10

RETIREMENT_AGE = 65
#: Real terms throughout: earnings growth and the discount rate are both
#: net of inflation, so the human capital figure is in today's money.
REAL_EARNINGS_GROWTH = 0.02
REAL_DISCOUNT_RATE = 0.03

#: Approximate Irish annual mortality at these ages, from CSO vital
#: statistics. Deliberately shown on screen rather than buried: it is the
#: input the life-cover answer turns on, and it is small enough that people
#: assume it is larger.
MORTALITY = {"male": 0.00045, "female": 0.00022}
#: And the reason the ordering flips. Industry incidence tables put the
#: chance of a disability spell long enough to trigger a 13-week deferred
#: claim at several times the chance of death at these ages. Six is a
#: conservative multiple; some tables are higher.
DISABILITY_MULTIPLE = 6.0

#: Indicative Irish market rates, monthly, non-smoker, in good health.
#: These are the weakest numbers in this module. Replace them with real
#: quotes before acting - the point of showing them is the ranking, not
#: the euro.
INDICATIVE = {
    "income_protection_pct_of_salary": 0.008,   # a year, gross of relief
    "life_per_100k_per_year_at_23": 78.0,       # level term, 25 years
    "mortgage_protection_per_100k_per_year_at_23": 60.0,   # decreasing term
    "specified_illness_per_50k_per_year_at_23": 300.0,
    "health_annual": 1400.0,
    #: Premiums rise roughly 8-9% for each year of age at these ages on
    #: level term. Compounding, which is why "buy it young" is usually
    #: right for cover you will definitely need.
    "age_loading_per_year": 0.085,
}


@dataclass
class Person:
    name: str
    age: int
    sex: str
    salary: float
    has_health_cover: bool
    has_life_cover: bool
    dependants: int = 0
    pension_monthly: float = 0.0
    smoker: bool = False


def human_capital(person: Person) -> dict:
    """Present value of what this person will earn between now and 65.

    Real terms, so it is comparable with a portfolio value on the same page
    without an inflation argument getting in the way.
    """
    years = max(0, RETIREMENT_AGE - person.age)
    total, salary = 0.0, person.salary
    for year in range(years):
        total += salary / ((1 + REAL_DISCOUNT_RATE) ** (year + 1))
        salary *= (1 + REAL_EARNINGS_GROWTH)
    return {
        "years": years,
        "presentValue": round(total, 2),
        "finalSalary": round(salary, 2),
        "assumptions": {
            "realGrowthPct": REAL_EARNINGS_GROWTH * 100,
            "realDiscountPct": REAL_DISCOUNT_RATE * 100,
            "retirementAge": RETIREMENT_AGE,
        },
    }


def lcr(person: Person, annual_premium: float | None = None) -> dict:
    """When health cover stops being free to defer, and what waiting costs.

    The rule is unusual and worth stating plainly: the loading depends on
    the age at which you FIRST hold inpatient cover, not on your health, not
    on your claims, and not on the insurer. It is the one deadline in Irish
    personal finance that is both hard and completely predictable.
    """
    premium = annual_premium or INDICATIVE["health_annual"]
    if person.has_health_cover:
        return {
            "covered": True,
            "note": ("Already holds inpatient cover, so the clock is stopped and "
                     "no loading can ever apply as long as it is not allowed to "
                     "lapse for long."),
            "yearsLeft": None, "schedule": [],
        }

    years_left = LCR_FREE_UNTIL_AGE - person.age
    schedule = []
    for start_age in range(person.age, person.age + 21):
        over = max(0, start_age - (LCR_FREE_UNTIL_AGE - 1))
        loading = min(LCR_MAX_LOADING, over * LCR_LOADING_PER_YEAR)
        schedule.append({
            "age": start_age,
            "inYears": start_age - person.age,
            "loadingPct": round(loading * 100, 1),
            "extraPerYear": round(premium * loading, 2),
            "extraOverTenYears": round(premium * loading * LCR_YEARS_APPLIED, 2),
        })

    worst = schedule[-1]
    return {
        "covered": False,
        "yearsLeft": years_left,
        "freeUntilAge": LCR_FREE_UNTIL_AGE,
        "premiumAssumed": premium,
        "schedule": schedule,
        "costOfWaitingToCap": round(premium * LCR_MAX_LOADING * LCR_YEARS_APPLIED, 2),
        "worst": worst,
        "note": (f"{years_left} years before the loading starts. Buying at "
                 f"{LCR_FREE_UNTIL_AGE - 1} costs exactly the same as buying today "
                 f"in loading terms - which means the honest answer is that there "
                 f"is no LCR reason to buy now, only a health-cover reason."),
    }


def risks(person: Person) -> dict:
    """The two events these products exist for, at this person's age."""
    annual_death = MORTALITY.get(person.sex, MORTALITY["male"])
    annual_disability = annual_death * DISABILITY_MULTIPLE
    horizon = 10

    def over(rate: float, years: int) -> float:
        return 1 - (1 - rate) ** years

    return {
        "annualDeathPct": round(annual_death * 100, 4),
        "annualDisabilityPct": round(annual_disability * 100, 4),
        "deathBy10yPct": round(over(annual_death, horizon) * 100, 2),
        "disabilityBy10yPct": round(over(annual_disability, horizon) * 100, 2),
        "deathTo65Pct": round(over(annual_death, RETIREMENT_AGE - person.age) * 100, 1),
        "disabilityTo65Pct": round(over(annual_disability, RETIREMENT_AGE - person.age) * 100, 1),
        "multiple": DISABILITY_MULTIPLE,
        "note": ("Mortality is CSO vital statistics, rounded. The disability "
                 "figure is a conservative multiple of it rather than an Irish "
                 "incidence table, which is not published in a usable form - so "
                 "treat the ratio as the finding and the level as indicative."),
    }


def _aged_premium(base_at_23: float, age: int) -> float:
    return base_at_23 * ((1 + INDICATIVE["age_loading_per_year"]) ** max(0, age - 23))


def instruments(person: Person, mortgage_share: float,
                mortgage_year: int | None, emergency_months_held: float,
                death_in_service_multiple: float | None = None) -> list[dict]:
    """Each protection product, with what it covers, what it costs, and
    whether this person actually carries the risk it insures.

    Ranked by exposure covered rather than by premium, because the cheapest
    product is always the one insuring the risk you do not have.
    """
    capital = human_capital(person)["presentValue"]
    risk = risks(person)
    out = []

    # 1. The emergency fund. Not an insurance product, which is exactly why
    #    it gets left off these lists, and it is the one that pays out for
    #    the events that actually happen.
    out.append({
        "name": "Emergency fund, 6 months of spending",
        "covers": "job loss, a car, a boiler, an excess - the claims that actually occur",
        "need": "essential",
        "exposure": round(person.salary / 2, 2),
        "annualCost": 0.0,
        "taxRelief": None,
        "verdict": "hold it before buying any policy" if emergency_months_held < 6
                   else "in place",
        "why": ("Every policy below has a waiting period, a deferred period or an "
                "excess. Cash is what covers the gap, and it is also what stops a "
                "bad month turning into a sold position. Deposit money earmarked "
                "for a house is not an emergency fund - it has a job."),
        "confidence": 95,
    })

    # 2. Income protection. The one that matches the exposure.
    ip_gross = person.salary * INDICATIVE["income_protection_pct_of_salary"]
    relievable = min(ip_gross, person.salary * IP_RELIEF_INCOME_CAP)
    ip_net = ip_gross - relievable * MARGINAL_RATE
    out.append({
        "name": "Income protection to 65",
        "covers": "the whole of your future earnings, paid monthly while you cannot work",
        "need": "high",
        "exposure": round(capital, 2),
        "annualCost": round(ip_gross, 2),
        "annualCostAfterRelief": round(ip_net, 2),
        "taxRelief": f"relief at {MARGINAL_RATE * 100:.0f}% on premiums up to "
                     f"{IP_RELIEF_INCOME_CAP * 100:.0f}% of income",
        "verdict": "buy",
        "why": (f"A disability spell is roughly {risk['multiple']:.0f}x more likely than "
                f"death at {person.age}, and the loss is not a lump sum - it is every "
                f"euro you would have earned, {_money(capital)} in today's money. It is "
                f"also the only policy here with tax relief, so the real cost is "
                f"{_money(ip_net)} a year, not {_money(ip_gross)}. Check the employer "
                f"scheme first: many Irish employers already provide it, and paying "
                f"twice for the same benefit is the common mistake."),
        "confidence": 82,
    })

    # 3. Life cover. Sized to the loss someone else suffers, which is the
    #    only thing it can sensibly be sized to.
    # The mortgage is already covered by the decreasing-term policy the
    # lender requires, so counting it here too would sell the same debt
    # twice - which is exactly what happens when both are bought off a
    # salary multiple instead of off an exposure.
    debt_uncovered = 0.0 if mortgage_year else mortgage_share
    if person.dependants or debt_uncovered > 0:
        need = debt_uncovered + person.dependants * capital * 0.5
        cost = _aged_premium(INDICATIVE["life_per_100k_per_year_at_23"], person.age) * need / 100_000
        verdict = "buy, sized to the debt"
        why = (f"There is a real loss to cover: {_money(debt_uncovered)} of uncovered debt"
               + (f" and {person.dependants} dependant(s)" if person.dependants else "")
               + ". Size the cover to that, not to a multiple of salary.")
    else:
        need, cost = 0.0, 0.0
        verdict = "not yet"
        why = ("Nobody loses money if you die today. No dependants, and the mortgage "
               "when it arrives is covered by the policy the lender already requires "
               "- so the economic need here is zero, however cheap the "
               "premium looks. The argument for buying young is that the rate is "
               "locked while you are healthy, and that argument is real, but it is "
               "an argument for buying it when the need appears and not before, "
               "unless a health condition is likely to develop first.")
    out.append({
        "name": "Life cover (level term)",
        "covers": "a lump sum to whoever depends on your income",
        "need": "high" if need else "none today",
        "exposure": round(need, 2),
        "annualCost": round(cost, 2),
        "taxRelief": None,
        "verdict": verdict,
        "why": why,
        "confidence": 88,
    })

    # 4. Mortgage protection. Not a recommendation - a legal precondition.
    if mortgage_year:
        cost = (_aged_premium(INDICATIVE["mortgage_protection_per_100k_per_year_at_23"],
                              person.age) * max(mortgage_share, 1) / 100_000)
        out.append({
            "name": "Mortgage protection (decreasing term)",
            "covers": "the outstanding mortgage, falling as the balance falls",
            "need": "mandatory",
            "exposure": round(mortgage_share, 2),
            "annualCost": round(cost, 2),
            "taxRelief": None,
            "verdict": f"required at drawdown, planned {mortgage_year}",
            "why": ("Section 126 of the Consumer Credit Act 1995 makes the lender "
                    "check a policy is in place before releasing the money. The four "
                    "exemptions are buy-to-let, uninsurable risk, borrower over 50, "
                    "and existing cover that already does the job - none of which "
                    "apply here. You are not obliged to buy it from the lender, and "
                    "the lender's own product is usually not the cheapest. Rate it "
                    "while young and healthy: at these ages the premium rises about "
                    f"{INDICATIVE['age_loading_per_year'] * 100:.0f}% a year of age."),
            "confidence": 93,
        })

    # 5. Specified illness. The one that is bought most and earns its place
    #    least at this age.
    si_cost = _aged_premium(INDICATIVE["specified_illness_per_50k_per_year_at_23"], person.age)
    out.append({
        "name": "Specified illness cover",
        "covers": "a lump sum on diagnosis of a listed condition",
        "need": "low",
        "exposure": 50_000.0,
        "annualCost": round(si_cost, 2),
        "taxRelief": None,
        "verdict": "skip while income protection is unbought",
        "why": ("Pays only for conditions on a list, only if the definition is met, "
                "and only once. Income protection pays for anything that stops you "
                "working, for as long as it does, and gets tax relief. Buying this "
                "first is the most common ordering mistake in Irish protection, and "
                f"at {_money(si_cost)} a year for {_money(50000)} of cover it is not "
                "cheap enough to be a rounding error."),
        "confidence": 74,
    })

    # 6. Health cover, which is a deadline rather than a product decision.
    clock = lcr(person)
    out.append({
        "name": "Private health insurance (inpatient)",
        "covers": "hospital costs, and the Lifetime Community Rating clock",
        "need": "deadline" if not clock["covered"] else "held",
        "exposure": 0.0,
        "annualCost": 0.0 if clock["covered"] else INDICATIVE["health_annual"],
        "taxRelief": "tax relief at 20% is given at source on the premium",
        "verdict": "held" if clock["covered"]
                   else f"no rush for {clock['yearsLeft']} years, then it is a deadline",
        "why": clock["note"],
        "confidence": 96,
    })

    order = {"essential": 0, "mandatory": 1, "high": 2, "deadline": 3, "low": 4,
             "none today": 5, "held": 6}
    out.sort(key=lambda r: order.get(r["need"], 9))
    return out


def _money(value: float) -> str:
    return f"EUR {value:,.0f}"


def assess(people: list[Person], mortgage_amount: float,
           mortgage_year: int | None, emergency_months: float) -> dict:
    """One household view. The mortgage is split between the borrowers,
    because each policy covers one life and the debt is joint."""
    share = mortgage_amount / max(len(people), 1)
    out = {}
    for person in people:
        out[person.name] = {
            "age": person.age,
            "salary": person.salary,
            "humanCapital": human_capital(person),
            "risks": risks(person),
            "lcr": lcr(person),
            "instruments": instruments(person, share, mortgage_year,
                                       emergency_months),
        }
    return {
        "people": out,
        "mortgage": {"amount": mortgage_amount, "year": mortgage_year,
                     "perBorrower": round(share, 2)},
        "emergencyMonthsHeld": emergency_months,
        "assumptions": INDICATIVE,
        "marginalRatePct": MARGINAL_RATE * 100,
    }
