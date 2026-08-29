/* Bond — the shortcuts, priced.

   Options, shorting and leverage are the three answers people reach for
   when the honest arithmetic says a target is out of reach. All three are
   real instruments with real uses, and all three are routinely sold on a
   story rather than a number. This file puts the number next to the story.

   The one structural point worth keeping in mind while reading it: a long
   option is the only listed instrument that produces a convex payoff -
   bounded loss, unbounded gain - and convexity is exactly what a power-law
   route needs. That is a genuine argument for owning them. The price of
   that convexity is a near-certain slow bleed, and this page measures the
   bleed rather than waving at it. */

/* RISK_FREE, EQUITY_RISK_PREMIUM and capmReturn come from app.js - one
   definition of the discount rate, or two panels quietly disagree. */

/* Abramowitz & Stegun 26.2.17 - about seven decimal places, which is six
   more than anything downstream of it deserves. */
function normCdf(x) {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989422804014327 * Math.exp(-x * x / 2);
  const p = d * t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 +
            t * (-1.821255978 + t * 1.330274429))));
  return x > 0 ? 1 - p : p;
}

function blackScholes(spot, strike, years, vol, call = true) {
  if (!(spot > 0 && strike > 0 && years > 0 && vol > 0)) return NaN;
  const sq = vol * Math.sqrt(years);
  const d1 = (Math.log(spot / strike) + (RISK_FREE + vol * vol / 2) * years) / sq;
  const d2 = d1 - sq;
  const disc = Math.exp(-RISK_FREE * years);
  return call
    ? spot * normCdf(d1) - strike * disc * normCdf(d2)
    : strike * disc * normCdf(-d2) - spot * normCdf(-d1);
}

/* The bit the pricing model deliberately does not tell you. Black-Scholes
   prices under the risk-neutral measure, where every asset drifts at the
   risk-free rate; what actually happens to the position depends on the real
   drift. Both are computed here, and the gap between them is the entire
   argument for or against owning the thing. */
function realWorldCall(spot, strike, years, vol, drift) {
  const sq = vol * Math.sqrt(years);
  const d2 = (Math.log(spot / strike) + (drift - vol * vol / 2) * years) / sq;
  const d1 = d2 + sq;
  const pITM = normCdf(d2);
  const expiryValue = spot * Math.exp(drift * years) * normCdf(d1) - strike * pITM;
  return { pITM, expiryValue };
}

/* Per-ticker inputs, computed here rather than shipped, so editing holdings
   moves them. Beta against the same benchmark the rest of the site uses. */
function tickerInputs(ticker) {
  const { rows } = alignedRows([ticker, DATA.benchmarkTicker]);
  if (rows.length < 60) return null;
  const own = rows.map(r => r[0]);
  const bench = rows.map(r => r[1]);
  const vol = ewmaVol(own);
  const b = beta(own, bench);
  const held = view().holdings.find(h => h.ticker === ticker);
  const spot = held && held.shares ? held.value_eur / held.shares : null;
  return {
    ticker, vol, beta: b, spot,
    drift: capmReturn(b),
    name: held ? held.name : ticker,
    currency: held ? held.currency : "EUR",
  };
}

/* ---------------- options ---------------- */

function renderOptions() {
  const pick = document.getElementById("optTicker");
  const inputs = tickerInputs(pick.value);
  const box = document.getElementById("optionBox");
  box.innerHTML = "";
  if (!inputs || !inputs.spot) {
    box.append(el("p", { class: "muted" }, "Not enough history on that line to price an option."));
    return;
  }

  const months = +document.getElementById("optMonths").value;
  const moneyness = +document.getElementById("optStrike").value / 100;
  const years = months / 12;
  const { spot, vol, drift } = inputs;
  const strike = spot * moneyness;

  const premium = blackScholes(spot, strike, years, vol, true);
  const { pITM, expiryValue } = realWorldCall(spot, strike, years, vol, drift);
  // Held to expiry, so the position pays only if the stock clears the strike
  // by more than the premium. That is the number people mean by "the odds".
  const breakeven = strike + premium;
  const pProfit = realWorldCall(spot, breakeven, years, vol, drift).pITM;
  const expectedReturn = premium > 0 ? expiryValue / premium - 1 : NaN;
  const leverageAtStart = spot / premium;

  document.getElementById("optLabels").textContent =
    `${months} months · strike ${(moneyness * 100).toFixed(0)}% of spot`;

  const cards = el("div", { class: "kpis" });
  for (const [k, v, s] of [
    ["Fair premium", fmtNum(premium, 2) + " per share",
     `${(premium / spot * 100).toFixed(1)}% of the ${fmtNum(spot, 2)} share price`],
    ["Chance it expires worthless", ((1 - pITM) * 100).toFixed(0) + "%",
     `needs ${(strike / spot * 100 - 100).toFixed(0)}% to be in the money at all`],
    ["Chance you make money", (pProfit * 100).toFixed(0) + "%",
     `breakeven ${fmtNum(breakeven, 2)}, ${((breakeven / spot - 1) * 100).toFixed(0)}% above spot`],
    ["Expected return", (expectedReturn * 100).toFixed(0) + "%",
     expectedReturn > 0 ? "positive, and almost never collected" : "negative before any spread"],
  ]) {
    cards.append(el("div", { class: "kpi" }, el("div", { class: "k" }, k),
      el("div", { class: "v" }, v), el("div", { class: "s" }, s)));
  }
  box.append(cards);

  /* Payoff at expiry against simply owning the shares for the same money.
     Drawn because the asymmetry is the whole product and a table hides it. */
  const points = [];
  for (let m = -60; m <= 100; m += 4) points.push(m);
  const shares = 1 / spot;                 // one euro of stock
  const contracts = 1 / premium;           // one euro of options
  chart("chartOption", {
    type: "line",
    data: {
      labels: points.map(m => m + "%"),
      datasets: [
        { label: "€1 of shares", borderColor: cssVar("--c1"), borderWidth: 2,
          pointRadius: 0, tension: 0.1,
          data: points.map(m => (spot * (1 + m / 100) * shares - 1) * 100) },
        { label: "€1 of these calls", borderColor: cssVar("--accent"), borderWidth: 2.5,
          pointRadius: 0, tension: 0.1,
          data: points.map(m => (Math.max(0, spot * (1 + m / 100) - strike) * contracts - 1) * 100) },
      ],
    },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { datalabels: { display: false },
        legend: { labels: { boxWidth: 10, boxHeight: 10, font: { size: 11 } } },
        tooltip: { callbacks: { title: c => `Share price ${c[0].label}`,
          label: c => `${c.dataset.label}: ${c.parsed.y >= 0 ? "+" : ""}${c.parsed.y.toFixed(0)}%` } } },
      scales: { x: gridScale({ ticks: { maxTicksLimit: 11 } }),
        y: gridScale({ ticks: { callback: v => v + "%" } }) },
    },
  });

  /* Kelly for a lottery-shaped payoff. Full Kelly on a bet that loses
     everything most of the time is already violent; the usual practice is a
     fraction of it, and a quarter is the common one. */
  const kelly = Math.max(0, (expectedReturn) / (leverageAtStart));
  const { value } = currentWeights();

  document.getElementById("optionNote").innerHTML =
    `<strong>What this actually is.</strong> ${inputs.name} at ${fmtNum(spot, 2)}, ` +
    `${(vol * 100).toFixed(0)}% volatility, beta ${inputs.beta.toFixed(2)}, so CAPM gives it a ` +
    `${(drift * 100).toFixed(1)}% expected return. A ${months}-month call struck ` +
    `${(moneyness * 100).toFixed(0)}% of spot costs ${(premium / spot * 100).toFixed(1)}% of the share ` +
    `price and controls ${leverageAtStart.toFixed(1)}× its own cost in stock.<br><br>` +
    `<strong>The trade in one line.</strong> You are paying ` +
    `${(premium / spot * 100).toFixed(1)}% for a ${(pProfit * 100).toFixed(0)}% chance of a payoff that ` +
    `is unlimited, against a ${((1 - pITM) * 100).toFixed(0)}% chance of losing every cent. That is not a ` +
    `bad deal or a good one on its own — it is a convex one, and convexity is the only thing on this site ` +
    `that can produce the tail a large target needs. It is also why the expected return is a number you ` +
    `will almost certainly never personally experience: the mean lives in the tail, and you get one draw.` +
    `<br><br>` +
    `<strong>Concrete, if you do it.</strong> Kelly on this payoff is about ` +
    `<strong>${(kelly * 100).toFixed(1)}% of the book</strong> — ${fmtEur(kelly * value)} — and a quarter ` +
    `of Kelly, which is what anyone sane uses on a fat-tailed bet, is ${fmtEur(kelly * value / 4)}. ` +
    `Buy time, not strikes: a twelve-month call loses time value roughly with the square root of time ` +
    `remaining, so the last sixty days cost you a quarter of the premium on their own, and weeklies are ` +
    `almost pure decay. Never roll a loser. Size it as money you have written off on the day you place it.` +
    `<br><br>` +
    `<strong>What is not in this number.</strong> The premium above is theoretical fair value at realised ` +
    `volatility. What you would actually pay is the offer, and implied volatility on single names trades ` +
    `persistently above realised — that gap is the option seller's edge and it is the reason writing calls ` +
    `is a business and buying them is not. Add the spread and this expected return goes negative. Neither ` +
    `Revolut nor Trading 212 sells you these, so it is a Davy or an overseas broker conversation, with ` +
    `their commission on top.`;
}

/* ---------------- the parlay ---------------- */

function renderParlay(start) {
  const target = ESCAPE.target;
  const multiple = target / Math.max(start, 1);
  const rows = [];
  // Each row is a different bet shape: how much it pays when it wins, and
  // how often that happens. The point is what happens when you have to
  // repeat it.
  for (const [label, payoff, chance] of [
    ["A 10% move on a 2x-levered position", 1.2, 0.50],
    ["Doubling money on a stock", 2.0, 0.30],
    ["A 3x on an at-the-money call", 3.0, 0.22],
    ["A 10x on a far out-of-the-money call", 10.0, 0.05],
    ["A 50x on a lottery strike", 50.0, 0.01],
  ]) {
    const wins = Math.log(multiple) / Math.log(payoff);
    const probability = Math.pow(chance, wins);
    rows.push({
      cells: [
        label,
        payoff.toFixed(payoff < 2 ? 1 : 0) + "×",
        (chance * 100).toFixed(0) + "%",
        Math.ceil(wins) + " in a row",
        { text: probability < 1e-12 ? "less than 1 in a trillion"
              : "1 in " + Math.round(1 / probability).toLocaleString("en-IE"),
          cls: "neg" },
      ],
    });
  }

  const box = document.getElementById("parlayBox");
  box.innerHTML = "";
  box.append(table(["Bet you would have to win", "Pays", "Chance", "Times needed",
                    "Chance of the whole run"], rows, { numFrom: 1 }));

  document.getElementById("parlayNote").innerHTML =
    `Reaching ${fmtBig(target)} from ${fmtEur(start)} is a <strong>` +
    `${multiple.toLocaleString("en-IE", { maximumFractionDigits: 0 })}× </strong> move, and you cannot ` +
    `get there in one bet at any strike anyone will sell you. So it has to be a run — win, reinvest the ` +
    `whole thing, win again — and the probability of a run is the product, not the average.<br><br>` +
    `<strong>This is the specific reason leverage and options do not add up to the target.</strong> Each ` +
    `individual bet is plausible; every one of them is something people do every day and sometimes win. ` +
    `The run is not. And the run is what the target requires, because anything less than reinvesting the ` +
    `entire proceeds every time takes the multiple out of reach again.<br><br>` +
    `The asymmetry is worse than the table shows: one loss anywhere in the sequence ends it, and there is ` +
    `no reason a loss would be the last one rather than the first.`;
}

/* ---------------- shorting ---------------- */

function renderShort(mu, sigma) {
  const box = document.getElementById("shortBox");
  box.innerHTML = "";
  const borrow = 0.03;   // typical stock borrow plus financing on a retail short

  const cards = el("div", { class: "kpis" });
  for (const [k, v, s] of [
    ["Expected return, short the book", (-(mu) * 100 - borrow * 100).toFixed(1) + "%",
     `−${(mu * 100).toFixed(1)}% drift, −${(borrow * 100).toFixed(0)}% borrow and financing`],
    ["Best case", "+100%", "the position goes to zero, and not a cent more"],
    ["Worst case", "unbounded", "there is no ceiling on a share price"],
    ["Chance of a 50% loss in a year", (normCdf((Math.log(1.5) - (-mu - sigma * sigma / 2)) /
      sigma) * 100).toFixed(0) + "%", "on a 50% rise in what you are short"],
  ]) {
    cards.append(el("div", { class: "kpi" }, el("div", { class: "k" }, k),
      el("div", { class: "v" }, v), el("div", { class: "s" }, s)));
  }
  box.append(cards);

  document.getElementById("shortNote").innerHTML =
    `<strong>The recommendation here is concrete and it is no, with high confidence.</strong> Not because ` +
    `shorting is immoral or exotic, but because of three things that are all arithmetic.<br><br>` +
    `One: you are betting against a positive drift. Equities go up on average, so a permanent short has a ` +
    `negative expected return before any cost, and this book's own CAPM drift of ` +
    `${(mu * 100).toFixed(1)}% is the size of the headwind.<br><br>` +
    `Two: the payoff is backwards. Your gain is capped at 100% and your loss is not capped at all, which ` +
    `is the exact opposite of the convexity that makes a long option worth owning. A short that goes ` +
    `wrong also grows as a share of the book while it does, so position sizing works against you.<br><br>` +
    `Three: you pay to hold it. Borrow plus financing on a retail platform is a few percent a year, ` +
    `charged whether you are right or not, and being right eventually is not the same as being solvent ` +
    `throughout.<br><br>` +
    `<strong>If the actual goal is protection rather than profit</strong>, buy a put instead. Same ` +
    `direction, loss capped at the premium, no margin call, no borrow, and no possibility of owing more ` +
    `than you staked. It costs more up front, and that cost is what you are buying.`;
}

/* ---------------- leveraged products ---------------- */

function renderDecay(mu, sigma) {
  const levels = [1, 1.5, 2, 3, 5];
  const rows = levels.map(L => {
    const gross = L * mu;
    const drag = Math.pow(L * sigma, 2) / 2;
    const extraDrag = drag - sigma * sigma / 2;
    return {
      cls: L === 1 ? "me" : "",
      cells: [
        L + "×",
        fmtPct1(gross * 100),
        { text: "−" + fmtPct1(drag * 100), cls: "muted" },
        { text: fmtPct1((gross - drag) * 100), cls: gross - drag > mu - sigma * sigma / 2 ? "pos" : "neg" },
        { text: "−" + fmtPct1(extraDrag * 100), cls: "neg" },
      ],
    };
  });
  const box = document.getElementById("decayBox");
  box.innerHTML = "";
  box.append(table(["Daily-rebalanced leverage", "Gross expected", "Variance drag",
                    "Compound growth", "Lost to decay vs 1×"], rows, { numFrom: 1 }));

  const kelly = sigma > 0 ? mu / (sigma * sigma) : 0;
  document.getElementById("decayNote").innerHTML =
    `A 2× or 3× ETF resets its leverage daily, so what it delivers over a year is not twice or three ` +
    `times the index. It is <strong>L×μ − (L×σ)²∕2</strong>, and the second term grows with the square. ` +
    `At this book's ${(sigma * 100).toFixed(0)}% volatility, 2× gives up ` +
    `${((Math.pow(2 * sigma, 2) / 2 - sigma * sigma / 2) * 100).toFixed(1)} points a year to decay alone ` +
    `and 3× gives up ${((Math.pow(3 * sigma, 2) / 2 - sigma * sigma / 2) * 100).toFixed(1)}.<br><br> ` +
    `<strong>Concrete: the ceiling is ${kelly.toFixed(2)}×</strong>, and since that is below 1× the ` +
    `instruction is to hold ${((1 - kelly) * 100).toFixed(0)}% in cash rather than to borrow anything. ` +
    `That is the same answer the four theories give, arrived at from the leverage side. A 2× product on ` +
    `this book is not aggressive, it is arithmetically worse than the unlevered version.<br><br>` +
    `Decay is not a fee and cannot be shopped around — it is what daily rebalancing does to a volatile ` +
    `series. The only leveraged exposure that avoids it is one you rebalance rarely, which means margin, ` +
    `which means a margin call on the path.`;
}

/* ---------------- verdict with confidence ---------------- */

const SHORTCUT_VERDICTS = [
  ["Buy long-dated calls, tiny size", "worth doing", 72,
   "The only instrument here with a convex payoff, which is the shape a large target needs. Size at a " +
   "quarter of Kelly, twelve months or longer, and write it off on day one. Confidence is capped by the " +
   "gap between implied and realised volatility, which is the seller's edge and is not measured here."],
  ["Buy short-dated calls", "no", 88,
   "Same instrument, all the decay. Time value falls with the square root of time remaining, so the last " +
   "two months cost a quarter of the premium. This is where retail option money goes."],
  ["Short individual stocks", "no", 91,
   "Negative expected return against a positive drift, gain capped at 100%, loss uncapped, and a borrow " +
   "cost charged whether you are right or not. Buy a put if the goal is protection."],
  ["2× or 3× leveraged ETFs", "no", 89,
   "Daily rebalancing turns volatility into a permanent drag that grows with the square of leverage. On " +
   "a 28% book, 3× gives up nearly 32 points a year to decay before it is right about anything."],
  ["Margin to above 1.6×", "no", 94,
   "Compound growth is zero at twice Kelly and negative past it. This is not a bad-luck outcome, it is " +
   "what the arithmetic does."],
  ["Hold cash to about 20%", "worth doing", 68,
   "Kelly on this book is below 1×, so the growth-maximising position is partly in cash. Confidence is " +
   "capped because it rests on a CAPM expected return, and a different drift assumption moves the answer."],
  ["Parlay any of the above to a billion", "no", 99,
   "Requires a run of consecutive wins whose joint probability is smaller than every other number on " +
   "this site. One loss anywhere ends it."],
];

function renderShortcutVerdicts() {
  const box = document.getElementById("verdictTable");
  box.innerHTML = "";
  box.append(table(["Shortcut", "Verdict", "How sure", "Why"],
    SHORTCUT_VERDICTS.map(([what, verdict, score, why]) => ({
      cells: [
        { node: el("strong", {}, what) },
        { text: verdict, cls: verdict === "no" ? "neg" : "pos" },
        { node: el("span", { class: "conf " + (score >= 75 ? "conf-high" : score >= 55 ? "conf-mod" : "conf-low") },
            `${score} ${score >= 75 ? "high" : score >= 55 ? "moderate" : "low"}`) },
        { text: why, cls: "muted" },
      ],
    })), { numFrom: 1 }));

  document.getElementById("verdictTableNote").innerHTML =
    `<strong>Read the confidence column carefully, because the two recommendations that say "worth doing" ` +
    `are the two least confident rows.</strong> That is not an accident. Saying no to something with a ` +
    `mathematically guaranteed drag is easy and the arithmetic is closed; saying yes to a convex bet rests ` +
    `on an expected return nobody can verify in advance. A page that showed high confidence on its buy ` +
    `recommendations and low confidence on its refusals would have the epistemics exactly backwards.`;
}

/* ---------------- entry point ---------------- */

function renderShortcuts(mu, sigma, start) {
  const pick = document.getElementById("optTicker");
  if (!pick.dataset.filled) {
    const held = view().holdings
      .filter(h => h.tradable !== false && columnFor(h.ticker) && h.shares)
      .sort((a, b) => b.value_eur - a.value_eur);
    for (const h of held) pick.append(el("option", { value: h.ticker }, h.name));
    pick.dataset.filled = "1";
    for (const id of ["optTicker", "optMonths", "optStrike"]) {
      document.getElementById(id).addEventListener("input", renderOptions);
    }
  }
  renderOptions();
  renderParlay(start);
  renderShort(mu, sigma);
  renderDecay(mu, sigma);
  renderShortcutVerdicts();
}
