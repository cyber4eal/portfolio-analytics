/* Bond — escape velocity.

   Every other tab answers "how should this money be arranged". This one
   answers the question that decides whether any of that matters: what
   would actually have to happen to reach the number, and has anyone ever
   done it.

   It is deliberately the least flattering page here. A target is the one
   input nobody sanity-checks, and a goal that is merely ambitious looks
   identical to one that is arithmetically impossible until the required
   rate is written next to the best records that exist.

   All of it runs in the browser off the same numbers as the rest of the
   site, so every control is live. */

/* ---------------- compounding ---------------- */

function fvMonthly(start, monthly, annualRate, years) {
  const n = Math.round(years * 12);
  const i = Math.pow(1 + annualRate, 1 / 12) - 1;
  if (Math.abs(i) < 1e-12) return start + monthly * n;
  const g = Math.pow(1 + i, n);
  return start * g + monthly * (g - 1) / i;
}

/* The rate that lands exactly on the target. Bisection rather than algebra
   because there is no closed form once a monthly contribution is in it. */
function requiredRate(start, monthly, target, years) {
  let low = -0.99, high = 50;
  if (fvMonthly(start, monthly, high, years) < target) return Infinity;
  if (fvMonthly(start, monthly, low, years) > target) return -Infinity;
  for (let k = 0; k < 300; k++) {
    const mid = (low + high) / 2;
    if (fvMonthly(start, monthly, mid, years) < target) low = mid; else high = mid;
  }
  return (low + high) / 2;
}

/* The contribution that lands on the target at a given rate. This one does
   have a closed form, and it is the more useful direction: a rate is a
   wish, a monthly figure is a decision. */
function requiredMonthly(start, rate, target, years) {
  const n = Math.round(years * 12);
  const i = Math.pow(1 + rate, 1 / 12) - 1;
  if (Math.abs(i) < 1e-12) return (target - start) / n;
  const g = Math.pow(1 + i, n);
  return (target - start * g) * i / (g - 1);
}

/* ---------------- what history actually contains ---------------- */

/* Net of fees, over the stated stretch, because a gross number and a net
   number are not the same claim and the difference is the whole industry. */
const RECORDS = [
  { name: "Cash", rate: 2.5, note: "deposit rate" },
  { name: "Global equities, long run", rate: 8, note: "the honest default" },
  { name: "Citadel Wellington, since 2000", rate: 21, note: "closed to new money" },
  { name: "Berkshire, 1965–2024", rate: 19.5, note: "Buffett, at scale" },
  { name: "Buffett Partnership, 1957–69", rate: 29.5, note: "small, concentrated" },
  { name: "Quantum, 1969–89", rate: 36, note: "Soros, leveraged macro" },
  { name: "Medallion, net, 1988–2018", rate: 39.1, note: "the best record that exists" },
];
const CEILING = 39.1;

/* Correlation Ventures, 21,000 investments 2004–2013 and 27,000 2009–2018,
   as reported by AngelList and Hustle Fund. Roughly a third are total
   losses, more than half return less than the money in, about 7% return
   more than 10x and carry three quarters of all the gains, and the top 1%
   return 50x or better. The shape matters more than the decimals: the mean
   is fine and the median is a loss. */
const VENTURE = [
  { p: 0.35, mult: 0.0, label: "total loss" },
  { p: 0.30, mult: 0.6, label: "returns less than the money in" },
  { p: 0.19, mult: 2.0, label: "2×" },
  { p: 0.09, mult: 5.0, label: "5×" },
  { p: 0.06, mult: 15.0, label: "15×" },
  { p: 0.01, mult: 50.0, label: "50× or better" },
];

/* Founding, not backing. Ownership is the whole difference: an angel owns
   a fraction of a percent of a company, a founder owns tens of percent of
   one, and that ratio is why the billionaire list is founders. Tiers are
   conditional on getting a company funded at all, which is itself the
   filter most attempts do not pass. */
const FOUNDER = [
  { p: 0.70, exit: 0, label: "fails or never exits" },
  { p: 0.22, exit: 5e6, label: "small trade sale, €5m" },
  { p: 0.06, exit: 50e6, label: "€50m exit" },
  { p: 0.017, exit: 300e6, label: "€300m exit" },
  { p: 0.0025, exit: 1e9, label: "unicorn, €1bn" },
  { p: 0.0005, exit: 10e9, label: "€10bn, roughly one a year in Europe" },
];

/* A fixed seed so the same inputs give the same answer twice. A figure that
   changes when you look away is one nobody can act on. */
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function draw(rand, buckets, key) {
  let u = rand(), acc = 0;
  for (const b of buckets) { acc += b.p; if (u <= acc) return b[key]; }
  return buckets[buckets.length - 1][key];
}

/* ---------------- state ---------------- */

const ESCAPE = { target: 1e9, years: 10, monthly: 400, leverage: 1 };
const TARGET_CHOICES = [
  [1e5, "€100k"], [2.5e5, "€250k"], [1e6, "€1m"],
  [1e7, "€10m"], [1e8, "€100m"], [1e9, "€1bn"],
];

const fmtBig = v =>
  v >= 1e9 ? "€" + (v / 1e9).toFixed(v % 1e9 ? 1 : 0) + "bn"
  : v >= 1e6 ? "€" + (v / 1e6).toFixed(v % 1e6 ? 1 : 0) + "m"
  : v >= 1e3 ? "€" + Math.round(v / 1e3) + "k"
  : "€" + Math.round(v);

/* ---------------- the arithmetic ---------------- */

function renderRequirement(start) {
  const { target, years, monthly } = ESCAPE;
  const need = requiredRate(start, monthly, target, years);
  const pct = need * 100;
  const beats = RECORDS.filter(r => pct > r.rate);
  const verdict = !isFinite(pct) ? "not reachable at any rate"
    : pct > CEILING ? "beyond every record in history"
    : pct > 19.5 ? "inside the record books, but only just"
    : pct > 8 ? "above the market, within what good managers have done"
    : "reachable at ordinary market returns";

  document.getElementById("escapeHead").innerHTML =
    `<div class="kpis">` +
    [[`${fmtBig(target)} in ${years} years`, isFinite(pct) ? pct.toFixed(1) + "% a year" : "—",
      `from ${fmtEur(start)} plus ${fmtEur(monthly)} a month`],
     ["Multiple of today", (target / Math.max(start, 1)).toLocaleString("en-IE",
        { maximumFractionDigits: 0 }) + "×", "before a euro of it is spent"],
     ["Records it must beat", `${beats.length} of ${RECORDS.length}`, verdict],
     ["At 8% you reach", fmtBig(fvMonthly(start, monthly, 0.08, years)),
      "the honest default, same horizon"],
    ].map(([k, v, s]) =>
      `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`
    ).join("") + `</div>`;

  /* The trade-off curve. Reading it left to right is the whole argument:
     the required return falls off a cliff with contributions at small
     targets and barely moves at large ones, because at EUR 1bn the
     contribution is a rounding error against the compounding. */
  const monthlies = [];
  for (let m = 0; m <= 5000; m += 100) monthlies.push(m);
  const curve = monthlies.map(m => {
    const r = requiredRate(start, m, target, years) * 100;
    return isFinite(r) ? Math.min(r, 400) : null;
  });

  const band = (from, to, colour) => ({
    type: "line", data: monthlies.map(() => to), fill: { target: { value: from } },
    backgroundColor: colour, borderWidth: 0, pointRadius: 0, order: 0,
  });

  chart("chartRequirement", {
    type: "line",
    data: {
      labels: monthlies.map(m => "€" + m),
      datasets: [
        { label: "Required return", data: curve, borderColor: cssVar("--accent"),
          borderWidth: 2.5, pointRadius: 0, tension: 0.2, order: 10 },
        { label: "Never sustained by anyone", ...band(CEILING, 400, "rgba(185,0,47,.10)") },
        { label: "Only the record books", ...band(8, CEILING, "rgba(15,126,130,.09)") },
        { label: "Ordinary market", ...band(1, 8, "rgba(30,122,70,.10)") },
      ],
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        datalabels: { display: false },
        legend: { labels: { boxWidth: 10, boxHeight: 10, font: { size: 11 } } },
        tooltip: { callbacks: {
          title: c => `Saving ${c[0].label} a month`,
          label: c => c.datasetIndex === 0
            ? `needs ${c.parsed.y.toFixed(1)}% a year` : null,
        } },
      },
      scales: {
        x: gridScale({ ticks: { maxTicksLimit: 11 } }),
        y: gridScale({ type: "logarithmic", min: 1, max: 400,
          ticks: { autoSkip: false,
            callback: v => [1, 2, 5, 10, 20, 40, 100, 200, 400].includes(v) ? v + "%" : "" } }),
      },
    },
  });

  document.getElementById("requirementNote").innerHTML =
    `The line is what the money must earn; the bands are what money has ever earned. ` +
    `Medallion's <strong>39.1% net</strong> over thirty years is the ceiling of the known world, and ` +
    `it has been closed to outside money since 1993 — so the red band is not "hard", it is ` +
    `<strong>unobserved</strong>.<br><br>` +
    (() => {
      const atMax = requiredRate(start, 5000, target, years) * 100;
      // The interesting question is not whether the target is hard at today's
      // contribution, but whether ANY contribution brings it inside the
      // record books. Those are different findings and deserve different text.
      if (!isFinite(pct) || atMax > CEILING) {
        return `At ${fmtBig(target)} in ${years} years the curve never leaves the red band. Saving ` +
          `€5,000 a month instead of ${fmtEur(monthly)} moves the requirement from ` +
          `${isFinite(pct) ? pct.toFixed(0) + "%" : "infinity"} to ${atMax.toFixed(0)}% — still past ` +
          `every record there is. <strong>That is the finding: at this target, contributions and ` +
          `allocation are both irrelevant, because neither can close a gap of that size.</strong> ` +
          `The rest of this page is about what can.`;
      }
      // Where does the curve cross out of the red band?
      let crossing = null;
      for (let m = 0; m <= 5000; m += 50) {
        if (requiredRate(start, m, target, years) * 100 <= CEILING) { crossing = m; break; }
      }
      if (pct > CEILING) {
        return `At ${fmtEur(monthly)} a month this needs ${pct.toFixed(1)}% a year, past every record ` +
          `that exists. <strong>The curve crosses out of the red band at about ` +
          `${fmtEur(crossing)} a month</strong>, and into ordinary market territory at ` +
          `${(() => { for (let m = 0; m <= 5000; m += 50) { if (requiredRate(start, m, target, years) * 100 <= 8) return fmtEur(m); } return "more than €5,000"; })()}. ` +
          `That is the whole trade this page exists to show: the gap is closed by the contribution, ` +
          `not by the allocation.`;
      }
      return `At ${fmtEur(monthly)} a month this needs ${pct.toFixed(1)}% a year, which is inside what ` +
        `has actually been achieved — ${pct <= 8 ? "and inside ordinary market returns, so this one is " +
        "a question of staying invested rather than of finding an edge" : "though it asks for a manager " +
        "better than the market, which is a different and much less reliable bet than saving more"}.`;
    })();
}

/* ---------------- what a plausible rate costs ---------------- */

function renderCost(start) {
  const { target, years } = ESCAPE;
  const box = document.getElementById("costTable");
  box.innerHTML = "";

  const rows = RECORDS.map(r => {
    const m = requiredMonthly(start, r.rate / 100, target, years);
    const yearsAt = (() => {
      // How long the target takes at this rate on the contribution you
      // actually make - the horizon is the one variable that is free.
      for (let y = 1; y <= 120; y++) {
        if (fvMonthly(start, ESCAPE.monthly, r.rate / 100, y) >= target) return y;
      }
      return null;
    })();
    return {
      cells: [
        { node: el("span", {}, el("strong", {}, r.name),
            el("div", { class: "muted", style: "font-size:12px" }, r.note)) },
        r.rate.toFixed(1) + "%",
        { text: m > 0 ? fmtEur(m) : "already there", cls: m > 20000 ? "neg" : "" },
        { text: yearsAt === null ? "never" : yearsAt + " years",
          cls: yearsAt === null ? "neg" : yearsAt <= years ? "pos" : "" },
        { text: fmtBig(fvMonthly(start, ESCAPE.monthly, r.rate / 100, years)), cls: "muted" },
      ],
    };
  });

  box.append(table(
    ["Earning at this rate", "Rate", `Needs each month for ${fmtBig(target)} in ${years}y`,
     `At ${fmtEur(ESCAPE.monthly)}/mo it takes`, `You end with in ${years}y`],
    rows, { numFrom: 1 }));

  const atMarket = requiredMonthly(start, 0.08, target, years);
  document.getElementById("costNote").innerHTML =
    `Read the third column as the price of refusing to change the return. To reach ${fmtBig(target)} ` +
    `in ${years} years at ordinary market returns you would have to save <strong>` +
    `${fmtEur(atMarket)} a month</strong>${atMarket > 1e6 ? " — which is itself the answer" : ""}. ` +
    `The fourth column is the same target with the horizon set free, and it is usually the only column ` +
    `with a number in it that a person can actually reach: <strong>time is the cheapest input on this ` +
    `page and the only one nobody is selling.</strong>`;
}

/* ---------------- where the money comes from ---------------- */

function renderSources(start) {
  const { years, monthly } = ESCAPE;
  const rate = 0.08;
  const labels = [], contributed = [], grown = [];
  let cross = null;
  for (let y = 0; y <= Math.max(years, 30); y++) {
    const total = fvMonthly(start, monthly, rate, y);
    const paid = start + monthly * 12 * y;
    labels.push("Y" + y);
    contributed.push(Math.round(paid));
    grown.push(Math.round(total - paid));
    if (cross === null && total - paid > monthly * 12 && y > 0) cross = y;
  }

  chart("chartSources", {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Money you put in", data: contributed, fill: "origin",
          backgroundColor: "rgba(20,82,122,.55)", borderColor: cssVar("--c1"),
          borderWidth: 1.5, pointRadius: 0, tension: 0.15 },
        { label: "What it earned", data: grown, fill: "-1",
          backgroundColor: "rgba(15,126,130,.45)", borderColor: cssVar("--accent"),
          borderWidth: 1.5, pointRadius: 0, tension: 0.15 },
      ],
    },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { datalabels: { display: false },
        legend: { labels: { boxWidth: 10, boxHeight: 10, font: { size: 11 } } },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${fmtEur(c.parsed.y)}` } } },
      scales: { x: gridScale({ ticks: { maxTicksLimit: 12 } }),
        y: gridScale({ stacked: true, ticks: { callback: v => fmtBig(v) } }) },
    },
  });

  document.getElementById("sourcesNote").innerHTML =
    `Stacked, at 8%. The lower band is money you earned at work and did not spend; the upper band is ` +
    `everything the market added. <strong>A year of saving stays bigger than a year of returns until ` +
    `year ${cross === null ? "—" : cross}</strong>, at about ${fmtEur(fvMonthly(start, monthly, rate, cross || 0))}. ` +
    `Until that crossing, the entire allocation debate is an argument about the smaller of two numbers — ` +
    `which is exactly the period most people spend arguing about it.`;
}

/* ---------------- the concentrated route ---------------- */

function renderVenture(start) {
  const stake = Math.max(0, +document.getElementById("ventureStake").value || 0);
  const deals = Math.max(1, +document.getElementById("ventureDeals").value || 1);
  const { years, monthly, target } = ESCAPE;
  const rand = rng(20260829);
  const paths = 20000;

  // The stake comes out of the same monthly saving, not out of thin air, or
  // the comparison flatters whichever side you were rooting for. Clamped to
  // what is actually being saved.
  const staked = Math.min(stake, monthly * 12);
  const clamped = stake > staked;
  const kept = monthly - staked / 12;
  const baseline = fvMonthly(start, kept, 0.08, years);
  const noBet = fvMonthly(start, monthly, 0.08, years);
  const shots = Math.max(1, Math.round(deals * years));   // every year, for the horizon

  const ends = new Array(paths);
  for (let k = 0; k < paths; k++) {
    let venture = 0;
    // Venture money is illiquid and compounds only through its exit, so it
    // is not also earning 8% on the side. The multiple is applied whenever
    // the cheque was written, which slightly flatters the later ones.
    for (let d = 0; d < shots; d++) {
      venture += (staked * years / shots) * draw(rand, VENTURE, "mult");
    }
    ends[k] = baseline + venture;
  }
  ends.sort((a, b) => a - b);
  const q = p => ends[Math.min(ends.length - 1, Math.floor(p * ends.length))];
  const above = v => ends.filter(e => e >= v).length / ends.length;
  const mean = ends.reduce((a, b) => a + b, 0) / ends.length;

  const ev = VENTURE.reduce((a, v) => a + v.p * v.mult, 0);
  const ivRate = Math.pow(ev, 1 / 7) - 1;   // the same multiple read as a rate over a 7-year hold

  const box = document.getElementById("ventureBox");
  box.innerHTML = "";
  const cards = el("div", { class: "kpis" });
  for (const [k, v, s] of [
    ["Median outcome", fmtEur(q(0.5)),
     q(0.5) < noBet ? `worse than ${fmtEur(noBet)} without the bet` : "ahead of not betting"],
    ["Mean outcome", fmtEur(mean), "the number the pitch quotes"],
    ["Chance of beating not betting", (above(noBet) * 100).toFixed(1) + "%",
     `${fmtEur(noBet)} is the do-nothing result`],
    [`Chance of ${fmtBig(target)}`, (above(target) * 100).toFixed(3) + "%",
     above(target) === 0 ? "not once in 20,000 runs" : "in 20,000 runs"],
  ]) {
    cards.append(el("div", { class: "kpi" }, el("div", { class: "k" }, k),
      el("div", { class: "v" }, v), el("div", { class: "s" }, s)));
  }
  box.append(cards);

  const buckets = [0, 1e4, 5e4, 1e5, 2.5e5, 5e5, 1e6, 5e6, 1e7, 1e8, 1e9];
  const counts = buckets.map((b, i) =>
    ends.filter(e => e >= b && (i === buckets.length - 1 || e < buckets[i + 1])).length);
  chart("chartVenture", {
    type: "bar",
    data: {
      labels: buckets.map(fmtBig),
      datasets: [{ data: counts.map(c => (c / paths) * 100),
        backgroundColor: buckets.map(b => b >= target ? cssVar("--green") : cssVar("--c2")) }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { label: c => c.parsed.y.toFixed(2) + "% of runs land here" } } },
      scales: { x: gridScale({ grid: { display: false } }),
        y: gridScale({ ticks: { callback: v => v + "%" } }) },
    },
  });

  box.append(table(["Outcome", "Chance", "Returns"],
    VENTURE.slice().reverse().map(v => ({
      cls: v.mult >= 10 ? "me" : "",
      cells: [v.label, (v.p * 100).toFixed(0) + "%",
        { text: v.mult + "×", cls: v.mult >= 1 ? "pos" : "neg" }],
    })), { numFrom: 1 }));

  document.getElementById("ventureNote").innerHTML =
    `Outcome shape from Correlation Ventures — 21,000 investments 2004–2013 and 27,000 2009–2018 — ` +
    `where roughly a third are total losses, more than half return less than the money in, about 7% ` +
    `return over 10× and carry three quarters of all the gains.<br><br>` +
    `<strong>Read that source carefully, because it is the weakest assumption on this page.</strong> ` +
    `It describes deals professional funds got into. The expected multiple here is ` +
    `${ev.toFixed(2)}× — about ${(ivRate * 100).toFixed(0)}% a year on a seven-year hold, which is ` +
    `top-quartile venture, not average anything. An individual without a fund's deal flow sees the ` +
    `deals those funds passed on, and this model has no way to know which of the two you are. If the ` +
    `panel above says backing companies beats indexing, that conclusion is carried entirely by an ` +
    `access assumption you should not grant yourself for free.<br><br>` +
    `<strong>The median gets worse and the mean gets better.</strong> That is not a flaw in the model, ` +
    `it is what a power law is, and it is the reason funds hold fifty positions rather than five: the ` +
    `mean is only collectable if you own enough of the distribution to catch its tail. At ` +
    `${deals} deal${deals === 1 ? "" : "s"} a year over ${years} years you get <strong>${shots} ` +
    `draws</strong> — and with a 35% total-loss rate you need roughly twenty before the tail is ` +
    `likely to show up at all.<br><br>` +
    (clamped
      ? `<strong>Capped at ${fmtEur(staked)}</strong>: the stake is taken out of the ` +
        `${fmtEur(monthly)} a month you already save, so it cannot exceed it. Raise the monthly ` +
        `figure at the top to stake more.<br><br>`
      : "") +
    `And note what it still cannot do. Even at ${fmtEur(staked)} a year into venture, the chance of ` +
    `${fmtBig(target)} is ${(above(target) * 100).toFixed(3)}%. Backing companies does not get you ` +
    `there. <strong>Owning one does</strong> — which is the next panel, and the difference is not ` +
    `courage, it is the ownership percentage.`;
}

/* ---------------- the founder route ---------------- */

function renderFounder() {
  const own = Math.max(0, Math.min(100, +document.getElementById("founderStake").value || 0)) / 100;
  const target = ESCAPE.target;
  const box = document.getElementById("founderBox");
  box.innerHTML = "";

  const rows = FOUNDER.slice().reverse().map(f => {
    const yours = f.exit * own;
    return {
      cls: yours >= target ? "me" : "",
      cells: [
        f.label,
        (f.p * 100).toFixed(f.p < 0.01 ? 2 : 0) + "%",
        fmtBig(f.exit),
        { node: el("strong", {}, fmtBig(yours)) },
        { text: yours >= target ? "reaches the target" : "does not", cls: yours >= target ? "pos" : "muted" },
      ],
    };
  });
  box.append(table(["Outcome", "Chance", "Company sells for", `Your ${(own * 100).toFixed(0)}%`, ""],
    rows, { numFrom: 1 }));

  const hit = FOUNDER.filter(f => f.exit * own >= target).reduce((a, f) => a + f.p, 0);
  const expected = FOUNDER.reduce((a, f) => a + f.p * f.exit * own, 0);

  const cards = el("div", { class: "kpis", style: "margin-top:16px" });
  for (const [k, v, s] of [
    [`Chance of ${fmtBig(target)}`, hit > 0 ? (hit * 100).toFixed(2) + "%" : "0%",
     hit > 0 ? `about 1 in ${Math.round(1 / hit).toLocaleString("en-IE")}` : "no tier reaches it"],
    ["Expected value of the attempt", fmtEur(expected), "before your time is priced in"],
    ["Chance of nothing at all", (FOUNDER[0].p * 100).toFixed(0) + "%", "the modal outcome"],
  ]) {
    cards.append(el("div", { class: "kpi" }, el("div", { class: "k" }, k),
      el("div", { class: "v" }, v), el("div", { class: "s" }, s)));
  }
  box.append(cards);

  document.getElementById("founderNote").innerHTML =
    `Business equity is <strong>66% of billionaire wealth</strong>; liquid assets are 31% and property ` +
    `is 3%. In surveys of the merely rich, only about 12% name stocks as important to how they got ` +
    `there. Those two facts are the entire answer to the question this page exists for, and neither of ` +
    `them is about asset allocation.<br><br>` +
    `The tiers above are conditional on getting a company funded at all, which is itself the filter most ` +
    `attempts never pass, and the ownership box is what you still hold after dilution — founders ` +
    `typically keep 10–20% by a late exit, not the 100% they started with.<br><br>` +
    `<strong>Read the top row honestly.</strong> ${hit > 0
      ? `A ${(hit * 100).toFixed(2)}% chance is real, and it is roughly one in ` +
        `${Math.round(1 / hit).toLocaleString("en-IE")}. That is not a plan, it is a lottery with ` +
        `positive expected value and a decade-long ticket price. It is also the only row on this ` +
        `entire site with a non-zero probability of ${fmtBig(target)}.`
      : `At this ownership no outcome tier reaches ${fmtBig(target)} — the exit would have to be larger ` +
        `than any in the table.`}`;
}

/* ---------------- leverage and the boundary ---------------- */

function renderLeverage(mu, sigma) {
  const levels = [];
  for (let L = 0; L <= 5.01; L += 0.1) levels.push(Math.round(L * 10) / 10);

  // Log growth of a leveraged position: the drag term is quadratic in
  // leverage, so growth turns over and then goes negative. Kelly is where
  // it peaks; twice Kelly is where it returns to zero.
  const growth = levels.map(L => (L * mu - Math.pow(L * sigma, 2) / 2) * 100);
  const kelly = sigma > 0 ? mu / (sigma * sigma) : 0;
  const zero = kelly * 2;

  /* Probability of ever being down 50% along the way. For a drifting random
     walk the chance of ever touching a barrier below is exp(-2mb/s^2) when
     the drift is positive, and certainty when it is not. Path risk, not
     end-point risk: a margin call does not wait for your horizon. */
  const ruin = levels.map(L => {
    const s = L * sigma, m = L * mu - s * s / 2, b = Math.log(2);
    if (L === 0) return 0;
    if (m <= 0) return 100;
    return Math.min(100, Math.exp(-2 * m * b / (s * s)) * 100);
  });

  chart("chartLeverage", {
    type: "line",
    data: {
      labels: levels.map(L => L.toFixed(1) + "×"),
      datasets: [
        { label: "Compound growth", data: growth, borderColor: cssVar("--accent"),
          borderWidth: 2.5, pointRadius: 0, tension: 0.2, yAxisID: "y" },
        { label: "Chance of ever being down 50%", data: ruin, borderColor: cssVar("--loss"),
          borderWidth: 2, borderDash: [5, 4], pointRadius: 0, tension: 0.2, yAxisID: "y1" },
      ],
    },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { datalabels: { display: false },
        legend: { labels: { boxWidth: 10, boxHeight: 10, font: { size: 11 } } },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(1)}%` } } },
      scales: {
        x: gridScale({ ticks: { maxTicksLimit: 11 } }),
        y: gridScale({ ticks: { callback: v => v.toFixed(0) + "%" } }),
        y1: gridScale({ position: "right", min: 0, max: 100, grid: { display: false },
          ticks: { callback: v => v + "%" } }),
      },
    },
  });

  document.getElementById("leverageNote").innerHTML =
    `From this book's own numbers: expected ${(mu * 100).toFixed(1)}% at ` +
    `${(sigma * 100).toFixed(1)}% volatility.<br><br>` +
    `Growth peaks at <strong>${kelly.toFixed(2)}× leverage</strong> and is back to zero at ` +
    `<strong>${zero.toFixed(2)}×</strong>. ` +
    (kelly < 1
      ? `<strong>That peak is below 1×, which means this book is already past growth-optimal with no ` +
        `borrowing at all.</strong> The growth-maximising position is ${(kelly * 100).toFixed(0)}% ` +
        `invested and ${((1 - kelly) * 100).toFixed(0)}% in cash — the same conclusion the four ` +
        `theories reach from the other direction, arrived at here without any view on which holdings ` +
        `are good. `
      : "") +
    `Past the zero point more borrowing makes you poorer with ` +
    `certainty, not with bad luck — the variance drag grows with the square of leverage while the ` +
    `return grows only in proportion. There is no leverage at which this book compounds fast enough ` +
    `to matter for the target above; there is a great deal of leverage at which it goes to zero.<br><br>` +
    `The dashed line is path risk, not end risk. At ${zero > 1 ? "2×" : "any"} leverage the chance of ` +
    `being down 50% <em>at some point</em> is ${ruin[Math.min(levels.length - 1, 20)].toFixed(0)}% — ` +
    `and a broker liquidates on the path, not at the horizon. <strong>Leverage is the one lever on ` +
    `this page that can make the number zero.</strong>`;
}

/* ---------------- the verdict ---------------- */

function renderVerdict(start) {
  const { target, years, monthly } = ESCAPE;
  const need = requiredRate(start, monthly, target, years) * 100;
  const best = fvMonthly(start, monthly, 0.08, years);
  const reachable = (() => {
    for (const [v] of TARGET_CHOICES) {
      if (requiredRate(start, monthly, v, years) * 100 <= 8) continue;
      return v;
    }
    return null;
  })();

  document.getElementById("verdictBox").innerHTML =
    `<p><strong>The honest position.</strong> ${fmtBig(target)} in ${years} years from ` +
    `${fmtEur(start)} and ${fmtEur(monthly)} a month needs ${isFinite(need) ? need.toFixed(1) + "%" : "an infinite return"} ` +
    ` a year. The best net record in financial history is 39.1%, it ran for thirty years, and it has ` +
    `been closed to outside money since 1993. No arrangement of the holdings on this site closes that ` +
    `gap, and any page that implied otherwise would be lying to you.</p>` +
    `<p><strong>What the same effort does buy.</strong> At 8% the book reaches ` +
    `<strong>${fmtEur(best)}</strong> over the same ten years, and the largest single input to that ` +
    `figure is the ${fmtEur(monthly * 12 * years)} you put in, not the market. Doubling the ` +
    `contribution changes the answer by more than perfect allocation would.</p>` +
    `<p><strong>Where extreme wealth actually comes from.</strong> Two thirds of billionaire wealth is ` +
    `equity in a business the person built or controls. It is not a portfolio outcome, it is an ` +
    `ownership outcome, and it is reached by owning a large fraction of one thing rather than a small ` +
    `fraction of many. The founder panel above prices that route: a real probability, in the low ` +
    `basis points, over a decade, with a 70% chance of nothing.</p>` +
    `<p><strong>So the useful reframe is this.</strong> The portfolio's job is not to make you a ` +
    `billionaire — it cannot — it is to stay solvent, liquid and compounding while the thing that ` +
    `might is attempted somewhere else. That is a real job and this site does it well. Judge it on ` +
    `${reachable ? fmtBig(reachable) : "the number above"}, which is decided here, rather than on a ` +
    `number that is decided elsewhere.</p>`;
}

/* ---------------- entry point ---------------- */

function renderEscape() {
  if (!DATA) return;
  const { value } = currentWeights();
  const start = value;

  const opt = view().optimisation;
  const current = opt && opt.theories ? opt.theories.current : null;
  const mu = current ? current.expectedReturn / 100 : 0.06;
  const sigma = current ? current.vol / 100 : 0.20;

  renderRequirement(start);
  renderCost(start);
  renderSources(start);
  renderVenture(start);
  renderFounder();
  renderLeverage(mu, sigma);
  renderShortcuts(mu, sigma, start);
  renderVerdict(start);
}

function wireEscape() {
  const targets = document.getElementById("escapeTargets");
  if (!targets || targets.dataset.wired) return;
  targets.dataset.wired = "1";
  for (const [value, label] of TARGET_CHOICES) {
    const b = el("button", { class: "btn ghost" + (value === ESCAPE.target ? " on" : "") }, label);
    b.addEventListener("click", () => {
      ESCAPE.target = value;
      targets.querySelectorAll("button").forEach(x => x.classList.remove("on"));
      b.classList.add("on");
      renderEscape();
    });
    targets.append(b);
  }

  const bind = (id, labelId, fmt, key) => {
    const input = document.getElementById(id);
    const out = document.getElementById(labelId);
    const show = () => { out.textContent = fmt(+input.value); };
    show();
    input.addEventListener("input", () => {
      ESCAPE[key] = +input.value; show(); renderEscape();
    });
  };
  bind("escapeYears", "escapeYearsLabel", v => v + " years", "years");
  bind("escapeMonthly", "escapeMonthlyLabel", v => fmtEur(v) + " a month", "monthly");

  for (const id of ["ventureStake", "ventureDeals", "founderStake"]) {
    document.getElementById(id).addEventListener("input", renderEscape);
  }
}
