"""The protection module's job is to get the ORDER right. The premiums are
indicative and will be wrong; the ranking has to survive that."""

from fundengine.protection import Person, assess, lcr, human_capital


def _catalin(**kw):
    base = dict(name="Catalin", age=23, sex="male", salary=45_000,
                has_health_cover=True, has_life_cover=False)
    base.update(kw)
    return Person(**base)


def test_human_capital_dwarfs_the_portfolio_at_this_age():
    """The whole tab exists because of this ratio, so it is worth asserting."""
    assert human_capital(_catalin())["presentValue"] > 1_000_000


def test_life_cover_is_not_recommended_without_a_dependant_or_uncovered_debt():
    """Cheap cover for a loss nobody suffers is not a bargain, and the
    mortgage is already covered by the policy the lender requires."""
    rows = assess([_catalin()], 300_000, 2027, 0.0)["people"]["Catalin"]["instruments"]
    life = next(r for r in rows if r["name"].startswith("Life cover"))
    assert life["need"] == "none today"
    assert life["exposure"] == 0
    assert life["annualCost"] == 0


def test_life_cover_appears_when_the_debt_is_not_otherwise_covered():
    rows = assess([_catalin()], 300_000, None, 0.0)["people"]["Catalin"]["instruments"]
    life = next(r for r in rows if r["name"].startswith("Life cover"))
    assert life["need"] == "high" and life["exposure"] == 300_000


def test_income_protection_outranks_specified_illness():
    rows = assess([_catalin()], 300_000, 2027, 0.0)["people"]["Catalin"]["instruments"]
    names = [r["name"] for r in rows]
    assert names.index("Income protection to 65") < names.index("Specified illness cover")


def test_income_protection_is_the_only_one_with_marginal_relief():
    rows = assess([_catalin()], 300_000, 2027, 0.0)["people"]["Catalin"]["instruments"]
    ip = next(r for r in rows if r["name"].startswith("Income protection"))
    assert ip["annualCostAfterRelief"] < ip["annualCost"]
    life = next(r for r in rows if r["name"].startswith("Life cover"))
    assert life["taxRelief"] is None


def test_the_lcr_clock_only_runs_for_someone_without_cover():
    assert lcr(_catalin(has_health_cover=True))["covered"] is True
    stefani = lcr(Person("Stefani", 22, "female", 35_000, False, False))
    assert stefani["yearsLeft"] == 13
    # Deferring is genuinely free right up to the year before the threshold,
    # which is the finding: a reminder, not a policy.
    free = [r for r in stefani["schedule"] if r["loadingPct"] == 0]
    assert max(r["age"] for r in free) == 34
    assert stefani["schedule"][13]["loadingPct"] == 2.0


def test_the_lcr_loading_is_capped():
    rows = lcr(Person("Late", 40, "male", 50_000, False, False))["schedule"]
    assert max(r["loadingPct"] for r in rows) <= 70.0
