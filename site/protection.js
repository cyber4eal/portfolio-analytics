/* Bond — protection.

   The rest of this site works on EUR 14k of shares. At 23 that is the
   smaller half of the balance sheet by a factor of about a hundred: the
   larger half is forty years of earnings that have not happened yet, worth
   over a million in present value, attached to one body, uninsured.

   That asymmetry decides every answer here, and it inverts the usual order
   these products are sold in. Life cover is cheap at 23 and insures a loss
   nobody currently suffers. Income protection is the one that matches the
   exposure and the only one with tax relief. Health cover in Ireland is not
   a decision at all, it is a dated deadline.

   Computed in the browser rather than shipped as an answer, because the two
   inputs it turns on — salary and age — are the two this project has never
   been told directly, and a correction should move every number on the page
   at the moment it is typed. */

const PROT = { people: {} };

function protectionConstants() { return (DATA.protection || {}).constants || {}; }

/* Present value of everything still to be earned, in today's money so it
   sits on the same page as a portfolio value without an inflation argument. */
function humanCapital(age, salary) {
  const c = protectionConstants();
  const years = Math.max(0, c.retirementAge - age);
  const growth = c.realEarningsGrowthPct / 100, discount = c.realDiscountPct / 100;
  let total = 0, pay = salary;
  for (let y = 0; y < years; y++) {
    total += pay / Math.pow(1 + discount, y + 1);
    pay *= 1 + growth;
  }
  return { years, presentValue: total, finalSalary: pay };
}

function protectionRisks(age, sex) {
  const c = protectionConstants();
  const death = (c.mortality || {})[sex] ?? 0.00045;
  const disability = death * (c.disabilityMultiple || 6);
  const to65 = Math.max(1, c.retirementAge - age);
  const over = (rate, years) => 1 - Math.pow(1 - rate, years);
  return {
    death, disability,
    deathBy10: over(death, 10), disabilityBy10: over(disability, 10),
    deathTo65: over(death, to65), disabilityTo65: over(disability, to65),
    multiple: c.disabilityMultiple || 6, to65,
  };
}

/* The Lifetime Community Rating clock. Unusual enough to be worth stating
   plainly: the loading depends on the age at which inpatient cover is FIRST
   held, not on health, claims or insurer. It is the one deadline in Irish
   personal finance that is both hard and perfectly predictable. */
function lcrSchedule(age, hasCover, premium) {
  const c = protectionConstants();
  if (hasCover) return { covered: true, yearsLeft: null, rows: [] };
  const rows = [];
  for (let start = age; start <= age + 20; start++) {
    const over = Math.max(0, start - (c.lcrFreeUntilAge - 1));
    const loading = Math.min(c.lcrMaxLoadingPct / 100, over * c.lcrLoadingPerYearPct / 100);
    rows.push({
      age: start, inYears: start - age, loading,
      extraPerYear: premium * loading,
      extraOverTen: premium * loading * c.lcrYearsApplied,
    });
  }
  return { covered: false, yearsLeft: c.lcrFreeUntilAge - age, rows };
}

const agedPremium = (base23, age) =>
  base23 * Math.pow(1 + protectionConstants().indicative.age_loading_per_year,
                    Math.max(0, age - 23));

function protectionInstruments(person, salary, mortgageShare, mortgageYear) {
  const c = protectionConstants(), ind = c.indicative;
  const capital = humanCapital(person.age, salary).presentValue;
  const risk = protectionRisks(person.age, person.sex);
  const clock = lcrSchedule(person.age, person.hasHealthCover, ind.health_annual);
  const out = [];

  out.push({
    need: "essential", rank: 0,
    name: "Emergency fund, 6 months of spending",
    covers: "job loss, a car, a boiler, an excess — the claims that actually happen",
    exposure: salary / 2,
    cost: 0, net: 0, relief: null,
    verdict: "hold this before buying any policy",
    why: "Every policy below has a deferred period, a waiting period or an excess. Cash covers the gap, " +
         "and it is also what stops a bad month becoming a sold position. Deposit money earmarked for a " +
         "house is not an emergency fund — it already has a job.",
    confidence: 95,
  });

  const ipGross = salary * ind.income_protection_pct_of_salary;
  const relievable = Math.min(ipGross, salary * c.ipReliefIncomeCapPct / 100);
  const ipNet = ipGross - relievable * c.marginalRatePct / 100;
  out.push({
    need: "high", rank: 2,
    name: "Income protection to 65",
    covers: "your whole future income, paid monthly for as long as you cannot work",
    exposure: capital,
    cost: ipGross, net: ipNet,
    relief: `relief at ${c.marginalRatePct}% on premiums up to ${c.ipReliefIncomeCapPct}% of income`,
    verdict: "buy — this is the one",
    why: `A disability spell is about ${risk.multiple}× more likely than death at ${person.age}, and the ` +
         `loss is not a lump sum. It is every euro you would have earned: ${fmtEur(capital)} in today's ` +
         `money. It is also the only policy here that gets income tax relief, so the real cost is ` +
         `${fmtEur(ipNet)} a year rather than ${fmtEur(ipGross)}. Check the employer scheme before you ` +
         `buy: many Irish employers already provide it, and paying twice for one benefit is the common ` +
         `mistake.`,
    confidence: 82,
  });

  const debtUncovered = mortgageYear ? 0 : mortgageShare;
  out.push({
    need: debtUncovered > 0 ? "high" : "none today", rank: debtUncovered > 0 ? 2 : 5,
    name: "Life cover (level term)",
    covers: "a lump sum for whoever depends on your income",
    exposure: debtUncovered,
    cost: debtUncovered > 0
      ? agedPremium(ind.life_per_100k_per_year_at_23, person.age) * debtUncovered / 100000 : 0,
    net: null, relief: null,
    verdict: debtUncovered > 0 ? "buy, sized to the debt" : "not yet — and this is the surprising one",
    why: debtUncovered > 0
      ? `There is a real loss to cover: ${fmtEur(debtUncovered)} of debt nobody else is insuring.`
      : "Nobody loses money if you die today. No dependants, no co-signed debt, and the mortgage when " +
        "it arrives is already covered by the policy the lender requires — so the economic need is zero, " +
        "however cheap the premium looks. The argument for buying young is that it locks the rate while " +
        "you are healthy, and that argument is real. It is an argument for buying when the need appears, " +
        "not years before it, unless you have reason to expect a health condition in between.",
    confidence: 88,
  });

  if (mortgageYear) {
    out.push({
      need: "mandatory", rank: 1,
      name: "Mortgage protection (decreasing term)",
      covers: "the outstanding mortgage, falling as the balance falls",
      exposure: mortgageShare,
      cost: agedPremium(ind.mortgage_protection_per_100k_per_year_at_23, person.age)
            * mortgageShare / 100000,
      net: null, relief: null,
      verdict: `required at drawdown — you plan ${mortgageYear}`,
      why: "Section 126 of the Consumer Credit Act 1995 makes the lender check a policy is in place " +
           "before releasing the money. The four exemptions are buy-to-let, uninsurable risk, borrower " +
           "over 50, and existing cover that already does the job — none apply. You are not obliged to " +
           "buy the lender's own product and it is usually not the cheapest. Rate it while young: at " +
           `these ages the premium rises about ${(ind.age_loading_per_year * 100).toFixed(0)}% for each ` +
           "year of age, so a two-year delay is roughly a fifth more, for life.",
      confidence: 93,
    });
  }

  out.push({
    need: "low", rank: 4,
    name: "Specified illness cover",
    covers: "a lump sum on diagnosis of a listed condition",
    exposure: 50000,
    cost: agedPremium(ind.specified_illness_per_50k_per_year_at_23, person.age),
    net: null, relief: null,
    verdict: "skip while income protection is unbought",
    why: "Pays only for conditions on a list, only if the definition is met, and only once. Income " +
         "protection pays for anything that stops you working, for as long as it does, and gets tax " +
         "relief. Buying this first is the most common ordering mistake in Irish protection.",
    confidence: 74,
  });

  out.push({
    need: clock.covered ? "held" : "deadline", rank: clock.covered ? 6 : 3,
    name: "Private health insurance (inpatient)",
    covers: "hospital costs — and the Lifetime Community Rating clock",
    exposure: 0,
    cost: clock.covered ? 0 : ind.health_annual,
    net: null, relief: "relief at 20% is already given at source on the premium",
    verdict: clock.covered
      ? "held — the clock is stopped"
      : `no LCR reason to rush for ${clock.yearsLeft} years, then it becomes a deadline`,
    why: clock.covered
      ? "Holding inpatient cover stops the Lifetime Community Rating clock permanently, as long as it is " +
        "not allowed to lapse for a long spell. Nothing to do here."
      : `The loading starts only if the first inpatient policy is taken at ${c.lcrFreeUntilAge} or over: ` +
        `${c.lcrLoadingPerYearPct}% for every year above ${c.lcrFreeUntilAge - 1}, capped at ` +
        `${c.lcrMaxLoadingPct}%, and it applies for ${c.lcrYearsApplied} years. Buying at ` +
        `${c.lcrFreeUntilAge - 1} costs exactly the same in loading terms as buying today — so the ` +
        `honest answer is that there is no LCR argument for buying now, only a health-cover one.`,
    confidence: 96,
  });

  out.sort((a, b) => a.rank - b.rank);
  return { rows: out, capital, risk, clock };
}

const NEED_CLASS = { essential: "conf-high", mandatory: "conf-spec", high: "conf-mod",
                     deadline: "conf-low", low: "conf-low", "none today": "conf-low",
                     held: "conf-high" };

function renderProtection() {
  const box = document.getElementById("protectionBox");
  if (!box) return;
  const config = DATA.protection;
  if (!config) { box.innerHTML = "<p class='muted'>No protection inputs published.</p>"; return; }

  const wrap = document.getElementById("protectionControls");
  if (!wrap.dataset.wired) {
    for (const person of config.people) {
      PROT.people[person.name] = { ...person, salary: person.salaryEstimate };
      const group = el("div", { class: "grp" });
      group.append(el("label", {}, `${person.name}, age ${person.age} — salary`));
      const input = el("input", { class: "numin", type: "number",
                                  value: String(Math.round(person.salaryEstimate)),
                                  min: "0", step: "1000" });
      input.addEventListener("input", () => {
        PROT.people[person.name].salary = Math.max(0, +input.value || 0);
        renderProtection();
      });
      group.append(input);
      group.append(el("div", { class: "muted", style: "font-size:11.5px;max-width:260px" },
        person.salaryNote));
      wrap.append(group);
    }
    const mortgage = el("div", { class: "grp" });
    mortgage.append(el("label", {}, "Mortgage you expect to draw"));
    const mIn = el("input", { class: "numin", type: "number",
                              value: String(config.mortgage.amount), step: "10000" });
    PROT.mortgage = config.mortgage.amount;
    mIn.addEventListener("input", () => { PROT.mortgage = +mIn.value || 0; renderProtection(); });
    mortgage.append(mIn);
    mortgage.append(el("div", { class: "muted", style: "font-size:11.5px;max-width:260px" },
      `drawn ${config.mortgage.year}; split between two borrowers`));
    wrap.append(mortgage);
    wrap.dataset.wired = "1";
  }

  box.innerHTML = "";
  const share = (PROT.mortgage || 0) / Math.max(config.people.length, 1);
  const totals = [];

  for (const person of config.people) {
    const state = PROT.people[person.name];
    const { rows, capital, risk, clock } =
      protectionInstruments(person, state.salary, share, config.mortgage.year);
    totals.push({ name: person.name, capital,
                  cost: rows.reduce((a, r) => a + (r.net ?? r.cost), 0) });

    const panel = el("div", { class: "panel wide", style: "margin-bottom:20px" });
    panel.append(el("h3", {}, `${person.name} — ${person.age}`));
    const body = el("div", { class: "body" });

    const cards = el("div", { class: "kpis" });
    for (const [k, v, s] of [
      ["Human capital", fmtEur(capital),
       `${risk.to65} years of earnings, discounted, in today's money`],
      ["Financial capital", fmtEur(currentWeights().value),
       `${(currentWeights().value / capital * 100).toFixed(1)}% of the total, and all the attention`],
      ["Chance of dying before 65", (risk.deathTo65 * 100).toFixed(1) + "%",
       "CSO mortality, held flat — so an underestimate at older ages"],
      ["Chance of a disability spell", (risk.disabilityTo65 * 100).toFixed(1) + "%",
       `${risk.multiple}× the mortality figure, which is the whole argument`],
    ]) {
      cards.append(el("div", { class: "kpi" }, el("div", { class: "k" }, k),
        el("div", { class: "v" }, v), el("div", { class: "s" }, s)));
    }
    body.append(cards);

    body.append(el("div", { style: "margin-top:18px" }));
    body.append(table(["Priority", "Instrument", "What it covers", "Exposure", "Cost a year", "Verdict"],
      rows.map(r => ({
        cls: r.need === "none today" || r.need === "low" ? "muted" : "",
        cells: [
          { node: el("span", { class: "conf " + (NEED_CLASS[r.need] || "conf-low") }, r.need) },
          { node: el("strong", {}, r.name) },
          { text: r.covers, cls: "muted" },
          r.exposure ? fmtEur(r.exposure) : "–",
          { node: el("span", {},
              r.cost ? fmtEur(r.cost) : "–",
              r.net !== null && r.net !== undefined && r.net !== r.cost
                ? el("div", { class: "muted", style: "font-size:11.5px" },
                    `${fmtEur(r.net)} after relief`)
                : null) },
          { text: r.verdict, cls: r.verdict.startsWith("buy") || r.verdict.startsWith("hold")
              ? "pos" : r.need === "mandatory" ? "neg" : "muted" },
        ],
      })), { numFrom: 4 }));

    const detail = el("div", { style: "margin-top:16px" });
    for (const r of rows) {
      detail.append(el("div", { style: "padding:11px 0;border-bottom:1px solid var(--line-soft)" },
        el("div", {},
          el("span", { class: "conf " + (NEED_CLASS[r.need] || "conf-low") }, r.need),
          el("strong", { style: "margin-left:10px" }, r.name),
          el("span", { class: "muted", style: "font-size:12.5px;margin-left:8px" },
            `confidence ${r.confidence}/100`)),
        el("div", { class: "muted", style: "font-size:13px;margin-top:6px" }, r.why),
        r.relief ? el("div", { style: "font-size:13px;margin-top:5px;color:var(--green)" },
          "Tax: " + r.relief) : null));
    }
    body.append(detail);

    if (!clock.covered) {
      body.append(el("h4", { style: "margin:20px 0 8px;font-size:14px" },
        "What waiting costs — Lifetime Community Rating"));
      const shown = clock.rows.filter(r => r.inYears % 2 === 0 || r.loading > 0).slice(0, 14);
      body.append(table(["Buy first policy at age", "In", "Loading", "Extra a year",
                         `Extra over ${protectionConstants().lcrYearsApplied} years`],
        shown.map(r => ({
          cls: r.loading > 0 ? "" : "me",
          cells: [
            { node: el("strong", {}, String(r.age)) },
            r.inYears === 0 ? "now" : r.inYears + "y",
            { text: (r.loading * 100).toFixed(0) + "%", cls: r.loading > 0 ? "neg" : "pos" },
            r.extraPerYear ? fmtEur(r.extraPerYear) : "–",
            { text: r.extraOverTen ? fmtEur(r.extraOverTen) : "nothing",
              cls: r.extraOverTen ? "neg" : "pos" },
          ],
        })), { numFrom: 3 }));
      body.append(el("p", { class: "note muted" },
        `Green rows cost the same as buying today. That is the finding: there are ` +
        `${clock.yearsLeft} years in which deferring is genuinely free, and one year after that in ` +
        `which it stops being free forever. Set a reminder for age ` +
        `${protectionConstants().lcrFreeUntilAge - 1}, not a policy today — unless you want the cover ` +
        `itself, which is a different question with a different answer.`));
    }

    panel.append(body);
    box.append(panel);
  }

  const c = protectionConstants();
  document.getElementById("protectionNote").innerHTML =
    `<strong>The whole tab in one line: you are insuring the small half of the balance sheet and ` +
    `ignoring the large one.</strong> Between you there is about ` +
    `${fmtEur(totals.reduce((a, t) => a + t.capital, 0))} of human capital and ` +
    `${fmtEur(currentWeights().value)} of financial capital, and every other page here is about the ` +
    `second number.<br><br>` +
    `<strong>Order of operations, and it is not the order these are sold in.</strong> Emergency fund ` +
    `first, because it is the only one that pays out for things that actually happen. Then income ` +
    `protection, because it insures the big number and is the only one Revenue subsidises at ` +
    `${c.marginalRatePct}%. Then mortgage protection, because the lender will not complete without it. ` +
    `Life cover and specified illness cover come last and, today, not at all.<br><br>` +
    `<strong>On buying life cover young.</strong> The pitch is true — the rate is locked while you are ` +
    `healthy, and premiums rise about ${(c.indicative.age_loading_per_year * 100).toFixed(0)}% for every ` +
    `year of age. It is still an argument for buying when the need appears rather than a decade early, ` +
    `because for those years you are paying for a loss nobody suffers. The exception worth taking ` +
    `seriously: if a condition is likely to develop before the need arrives, cover bought now is cover ` +
    `you can still get.<br><br>` +
    `<strong>Two things to check before buying anything.</strong> Whether the Davy scheme includes death ` +
    `in service — Irish occupational schemes commonly pay a multiple of salary, and it would make a ` +
    `separate life policy redundant for years. And whether it includes income protection, for the same ` +
    `reason. Both are in the scheme booklet, and both are worth more than any decision on this page.` +
    `<br><br>` +
    `<strong>What is soft here.</strong> Premiums are indicative Irish market rates, not quotes, and they ` +
    `are the weakest input on the page. The ranking does not depend on them: it depends on which risks ` +
    `you actually carry, which is arithmetic. The salary boxes above change every figure as you type.`;
}
