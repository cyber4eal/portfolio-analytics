/* Fund engine — all arithmetic runs here so edited holdings recompute live.
   The build ships a return matrix, not finished numbers; anything derived
   from weights has to be recomputed the moment a weight changes. */

const TRADING_DAYS = 252;
const LAMBDA = 0.94;          // RiskMetrics decay, ~11 day half-life
const Z95 = 1.6448536269514722;
const STORE_KEY = "fundengine.holdings.v1";

let DATA = null;
let COL = {};                 // ticker -> column index in the return matrix
let holdings = [];            // live, editable

/* ---------- matrix helpers ---------- */

function columnFor(ticker) {
  return DATA.returns.series[ticker] || null;
}

/* Rows where every requested ticker printed. Aligning on dates rather than
   position is the whole game: a Xetra name and a US name of equal length are
   not the same dates, and one missing print NaNs the covariance.

   `restrict` narrows further to an explicit set of dates. Every row of the
   comparison table is measured over the book's own window, because a fund
   scored across five years that include 2022 against a book scored across
   sixteen bullish months is not a comparison, it is two different questions
   printed in the same table. */
function alignedRows(tickers, restrict) {
  const cols = tickers.map(columnFor);
  if (cols.some(c => !c)) return { rows: [], index: [] };
  const rows = [], index = [];
  for (let i = 0; i < DATA.returns.dates.length; i++) {
    if (restrict && !restrict.has(DATA.returns.dates[i])) continue;
    let ok = true;
    const row = new Array(cols.length);
    for (let j = 0; j < cols.length; j++) {
      const v = cols[j][i];
      if (v === null || v === undefined || !isFinite(v)) { ok = false; break; }
      row[j] = v;
    }
    if (ok) { rows.push(row); index.push(DATA.returns.dates[i]); }
  }
  return { rows, index };
}

function weightedSeries(rows, weights) {
  return rows.map(row => row.reduce((a, v, j) => a + v * weights[j], 0));
}

/* ---------- statistics ---------- */

function ewmaVol(series) {
  const n = series.length;
  if (n < 30) return NaN;
  const mean = series.reduce((a, b) => a + b, 0) / n;
  let weight = 1, total = 0, acc = 0;
  for (let i = n - 1; i >= 0; i--) {
    const d = series[i] - mean;
    acc += weight * d * d;
    total += weight;
    weight *= LAMBDA;
  }
  return Math.sqrt((acc / total) * TRADING_DAYS);
}

function correlation(a, b) {
  const n = Math.min(a.length, b.length);
  if (n < 30) return NaN;
  let ma = 0, mb = 0;
  for (let i = 0; i < n; i++) { ma += a[i]; mb += b[i]; }
  ma /= n; mb /= n;
  let sab = 0, saa = 0, sbb = 0;
  for (let i = 0; i < n; i++) {
    const da = a[i] - ma, db = b[i] - mb;
    sab += da * db; saa += da * da; sbb += db * db;
  }
  return sab / Math.sqrt(saa * sbb);
}

function beta(series, bench) {
  const n = Math.min(series.length, bench.length);
  if (n < 30) return NaN;
  let ms = 0, mb = 0;
  for (let i = 0; i < n; i++) { ms += series[i]; mb += bench[i]; }
  ms /= n; mb /= n;
  let cov = 0, varb = 0;
  for (let i = 0; i < n; i++) {
    const db = bench[i] - mb;
    cov += (series[i] - ms) * db;
    varb += db * db;
  }
  return varb ? cov / varb : NaN;
}

function maxDrawdown(series) {
  let level = 1, peak = 1, worst = 0;
  for (const r of series) {
    level *= 1 + r;
    if (level > peak) peak = level;
    const dd = level / peak - 1;
    if (dd < worst) worst = dd;
  }
  return worst;
}

function totalReturn(series) {
  return series.reduce((a, r) => a * (1 + r), 1) - 1;
}

/* Historical VaR beats the parametric one here: this book is concentrated
   and its tail is not normal, so the 5th percentile of what actually
   happened is the more honest number. Both are shown. */
function historicalVar(series, value) {
  const sorted = [...series].sort((a, b) => a - b);
  const idx = Math.max(0, Math.floor(sorted.length * 0.05) - 1);
  return -sorted[idx] * value;
}

function statsFor(tickers, weights, value, restrict) {
  const { rows, index } = alignedRows([...tickers, DATA.benchmarkTicker], restrict);
  if (!rows.length) return null;
  const w = weights.concat([0]);
  const series = weightedSeries(rows, w);
  const bench = rows.map(r => r[r.length - 1]);
  const vol = ewmaVol(series);
  const years = rows.length / TRADING_DAYS;
  const total = totalReturn(series);
  const cagr = years >= 1 ? Math.pow(1 + total, 1 / years) - 1 : total;
  return {
    vol: vol * 100,
    beta: beta(series, bench),
    maxDrawdown: maxDrawdown(series) * 100,
    sharpe: vol ? (cagr - 0.02) / vol : NaN,
    var95: (value * Z95 * vol) / Math.sqrt(TRADING_DAYS),
    varHist: historicalVar(series, value),
    cagr: cagr * 100,
    total: total * 100,
    correlationToBenchmark: correlation(series, bench),
    from: index[0], to: index[index.length - 1],
    days: rows.length,
    series, bench, index,
  };
}

/* ---------- holdings state ---------- */

function loadHoldings() {
  try {
    const saved = localStorage.getItem(STORE_KEY);
    if (saved) return JSON.parse(saved);
  } catch (e) { /* private window, or storage disabled */ }
  return null;
}

function saveHoldings() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(holdings)); }
  catch (e) { /* nothing persists this session; the page still works */ }
}

function sheetHoldings() {
  return DATA.holdings
    .filter(h => h.tradable && columnFor(h.ticker))
    .map(h => ({ ticker: h.ticker, name: h.name, value: h.value_eur }));
}

function resetHoldings() {
  holdings = sheetHoldings();
  try { localStorage.removeItem(STORE_KEY); } catch (e) {}
  renderAll();
}

function isEdited() {
  const base = sheetHoldings();
  if (base.length !== holdings.length) return true;
  const byTicker = Object.fromEntries(base.map(h => [h.ticker, h.value]));
  return holdings.some(h => Math.abs((byTicker[h.ticker] ?? -1) - h.value) > 0.005);
}

function currentWeights() {
  const live = holdings.filter(h => h.value > 0 && columnFor(h.ticker));
  const total = live.reduce((a, h) => a + h.value, 0);
  return {
    tickers: live.map(h => h.ticker),
    weights: live.map(h => h.value / total),
    value: total,
  };
}

/* ---------- rendering ---------- */

const fmtPct = v => (v === null || v === undefined || !isFinite(v)) ? "–" : `${v.toFixed(2)}%`;
const fmtNum = v => (v === null || v === undefined || !isFinite(v)) ? "–" : v.toFixed(2);
const fmtEur = v => (v === null || !isFinite(v)) ? "–" :
  "€" + Math.round(v).toLocaleString("en-IE");

function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

function renderHoldings() {
  const box = document.getElementById("holdings");
  box.innerHTML = "";
  const { value } = currentWeights();

  const table = el("table", { class: "grid" });
  table.append(el("thead", {}, el("tr", {},
    el("th", {}, "Holding"), el("th", {}, "Ticker"),
    el("th", { class: "num" }, "Value (€)"),
    el("th", { class: "num" }, "Weight"), el("th", {}, ""))));

  const body = el("tbody");
  holdings.forEach((h, i) => {
    const input = el("input", {
      type: "number", value: h.value.toFixed(2), step: "10", min: "0",
      class: "val", oninput: e => {
        holdings[i].value = parseFloat(e.target.value) || 0;
        saveHoldings();
        renderDerived();
        renderWeightsOnly();
      },
    });
    body.append(el("tr", {},
      el("td", {}, h.name || h.ticker),
      el("td", { class: "mono" }, h.ticker),
      el("td", { class: "num" }, input),
      el("td", { class: "num weight" }, value ? fmtPct((h.value / value) * 100) : "–"),
      el("td", {}, el("button", {
        class: "link", onclick: () => { holdings.splice(i, 1); saveHoldings(); renderAll(); },
      }, "remove"))));
  });
  table.append(body);
  box.append(table);

  const options = Object.keys(DATA.returns.series)
    .filter(t => t !== "__benchmark__" && !holdings.some(h => h.ticker === t));
  const select = el("select", { id: "addTicker" },
    el("option", { value: "" }, "add a line…"),
    options.map(t => {
      const fund = DATA.funds.find(f => f.ticker === t);
      return el("option", { value: t }, fund ? `${t} — ${fund.name}` : t);
    }));
  const amount = el("input", { type: "number", id: "addValue", value: "1000", step: "100", class: "val" });
  box.append(el("div", { class: "addrow" }, select, amount,
    el("button", {
      class: "btn", onclick: () => {
        const ticker = select.value;
        if (!ticker) return;
        const fund = DATA.funds.find(f => f.ticker === ticker);
        holdings.push({
          ticker, name: fund ? fund.name : ticker,
          value: parseFloat(amount.value) || 0,
        });
        saveHoldings(); renderAll();
      },
    }, "add"),
    el("button", { class: "btn ghost", onclick: resetHoldings }, "reset to sheet")));
}

function renderWeightsOnly() {
  const { value } = currentWeights();
  document.querySelectorAll("#holdings tbody tr").forEach((row, i) => {
    row.querySelector(".weight").textContent =
      value ? fmtPct((holdings[i].value / value) * 100) : "–";
  });
}

function renderDerived() {
  const { tickers, weights, value } = currentWeights();
  const stats = statsFor(tickers, weights, value);
  const banner = document.getElementById("editBanner");
  banner.style.display = isEdited() ? "" : "none";

  const box = document.getElementById("summary");
  box.innerHTML = "";
  if (!stats) {
    box.append(el("p", { class: "warn" }, "No overlapping price history for these lines."));
    return;
  }
  const parked = DATA.totals.parked;
  const cards = [
    ["Priced book", fmtEur(value), `plus ${fmtEur(parked)} parked`],
    ["Volatility", fmtPct(stats.vol), "annualised, EWMA"],
    ["Beta", fmtNum(stats.beta), `vs ${DATA.benchmarkTicker}`],
    ["Worst drawdown", fmtPct(stats.maxDrawdown), "peak to trough, in sample"],
    ["1-day VaR 95%", fmtEur(stats.var95), `historical ${fmtEur(stats.varHist)}`],
    ["Return p.a.", fmtPct(stats.cagr), `over ${(stats.days / TRADING_DAYS).toFixed(1)} years`],
  ];
  for (const [label, big, sub] of cards) {
    box.append(el("div", { class: "card" },
      el("div", { class: "clabel" }, label),
      el("div", { class: "cbig" }, big),
      el("div", { class: "csub" }, sub)));
  }
  document.getElementById("window").textContent =
    `Comparable history ${stats.from} to ${stats.to} — limited by the shortest series in the book.`;

  renderComparison(stats, value);
  renderAdditions(tickers, weights, value, stats);
}

function renderComparison(mine, value) {
  const box = document.getElementById("comparison");
  box.innerHTML = "";
  const window_ = new Set(mine.index);
  const rows = [];
  rows.push({
    id: "me", name: "My portfolio", kind: "portfolio",
    vol: mine.vol, beta: mine.beta, dd: mine.maxDrawdown,
    sharpe: mine.sharpe, cagr: mine.cagr, corr: 1,
  });
  for (const fund of DATA.funds) {
    const stats = statsFor([fund.ticker], [1], value, window_);
    if (!stats || stats.days < 60) continue;
    rows.push({
      id: fund.id, name: fund.name, kind: "fund", asset: fund.asset,
      vol: stats.vol, beta: stats.beta, dd: stats.maxDrawdown,
      sharpe: stats.sharpe, cagr: stats.cagr,
      corr: correlation(stats.series, mine.series),
    });
  }
  rows.sort((a, b) => (a.kind === "portfolio" ? -1 : b.kind === "portfolio" ? 1 : b.sharpe - a.sharpe));

  const table = el("table", { class: "grid" });
  table.append(el("thead", {}, el("tr", {},
    el("th", {}, "Line"), el("th", {}, "Asset class"),
    el("th", { class: "num" }, "Return p.a."), el("th", { class: "num" }, "Vol"),
    el("th", { class: "num" }, "Sharpe"), el("th", { class: "num" }, "Beta"),
    el("th", { class: "num" }, "Max DD"), el("th", { class: "num" }, "Corr. to book"))));
  const body = el("tbody");
  for (const r of rows) {
    body.append(el("tr", { class: r.kind === "portfolio" ? "me" : "" },
      el("td", {}, r.name),
      el("td", { class: "muted" }, r.asset || "Your holdings"),
      el("td", { class: "num" }, fmtPct(r.cagr)),
      el("td", { class: "num" }, fmtPct(r.vol)),
      el("td", { class: "num" }, fmtNum(r.sharpe)),
      el("td", { class: "num" }, fmtNum(r.beta)),
      el("td", { class: "num neg" }, fmtPct(r.dd)),
      el("td", { class: "num" }, fmtNum(r.corr))));
  }
  table.append(body);
  box.append(table);
}

/* The question the whole page exists for: what does buying this fund do to
   the book. Recomputed against the edited holdings, not the sheet's. */
function renderAdditions(tickers, weights, value, mine) {
  const allocation = parseFloat(document.getElementById("alloc").value) / 100;
  const box = document.getElementById("additions");
  box.innerHTML = "";
  const window_ = new Set(mine.index);
  const out = [];
  for (const fund of DATA.funds) {
    if (tickers.includes(fund.ticker)) continue;
    const mixTickers = [...tickers, fund.ticker];
    const mixWeights = [...weights.map(w => w * (1 - allocation)), allocation];
    const after = statsFor(mixTickers, mixWeights, value, window_);
    if (!after || after.days < 60) continue;
    // Baseline on the mixture's own dates, so a fund with a shorter history
    // is not charged for a period the book was measured over and it was not.
    const base = statsFor(tickers, weights, value, new Set(after.index));
    const solo = statsFor([fund.ticker], [1], value, new Set(after.index));
    out.push({
      fund, volChange: after.vol - base.vol, newVol: after.vol,
      betaChange: after.beta - base.beta, newBeta: after.beta,
      corr: solo ? correlation(solo.series, base.series) : NaN,
    });
  }
  out.sort((a, b) => a.volChange - b.volChange);

  const table = el("table", { class: "grid" });
  table.append(el("thead", {}, el("tr", {},
    el("th", {}, `Add at ${(allocation * 100).toFixed(0)}%`),
    el("th", { class: "num" }, "Corr. to book"),
    el("th", { class: "num" }, "New vol"), el("th", { class: "num" }, "Δ vol"),
    el("th", { class: "num" }, "New beta"), el("th", { class: "num" }, "Δ beta"))));
  const body = el("tbody");
  for (const r of out) {
    body.append(el("tr", {},
      el("td", {}, r.fund.name),
      el("td", { class: "num" }, fmtNum(r.corr)),
      el("td", { class: "num" }, fmtPct(r.newVol)),
      el("td", { class: "num " + (r.volChange < 0 ? "pos" : "neg") },
        (r.volChange >= 0 ? "+" : "") + r.volChange.toFixed(2) + "pp"),
      el("td", { class: "num" }, fmtNum(r.newBeta)),
      el("td", { class: "num" }, (r.betaChange >= 0 ? "+" : "") + r.betaChange.toFixed(2))));
  }
  table.append(body);
  box.append(table);
}

function renderCaveats() {
  const box = document.getElementById("caveats");
  box.innerHTML = "";
  const list = el("ul");
  for (const c of DATA.caveats) list.append(el("li", {}, c));
  if (DATA.projection && DATA.projection.warning) list.append(el("li", {}, DATA.projection.warning));
  box.append(list);
}

function renderAll() {
  renderHoldings();
  renderDerived();
}

async function boot() {
  const response = await fetch("data.json");
  DATA = await response.json();
  document.getElementById("asof").textContent =
    `Prices as at ${DATA.asOf} · built ${DATA.generated.replace("T", " ")}`;
  holdings = loadHoldings() || sheetHoldings();
  // A ticker saved before a rebuild may no longer be in the matrix.
  holdings = holdings.filter(h => columnFor(h.ticker));
  document.getElementById("alloc").addEventListener("input", e => {
    document.getElementById("allocLabel").textContent = e.target.value + "%";
    renderDerived();
  });
  renderCaveats();
  renderAll();
}

boot();
