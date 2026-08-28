/* Bond Portfolio Centre — all arithmetic runs here.

   The build ships a return matrix rather than finished numbers, because a
   finished number goes stale the moment a holding is edited. Everything on
   screen is derived from weights at render time. */

const TRADING_DAYS = 252, LAMBDA = 0.94, Z95 = 1.6448536269514722;
const PALETTE = ["#14527A","#4A7FA5","#0F7E82","#2A9D9F","#7FB8B6",
                 "#0B1030","#3A3F66","#7C7FA0","#00B2A9","#6FD5CE"];
const INK = "#1C1C1E", SLATE = "#5B5B62", LINE = "#E6E3DE";

let DATA = null, holdings = [], charts = {}, worldGeo = null;
let BOOK = null;              // which book is on screen
let API = false;              // does the transaction API answer

/* Every per-book number lives under bookViews. Reading DATA directly would
   quietly show Catalin's exposure next to Stefani's holdings, which is the
   exact failure the sheet's Portfolio column exists to prevent. */
const view = () => DATA.bookViews[BOOK];
const STORE_KEY_FOR = book => `bond.holdings.${book}.v1`;

/* ---------------- matrix + statistics ---------------- */

const columnFor = t => DATA.returns.series[t] || null;

/* Rows where every requested ticker printed, optionally narrowed to a set of
   dates. Aligning on dates rather than array position is the whole game: two
   series of equal length from different exchanges are not the same days. */
function alignedRows(tickers, restrict) {
  const cols = tickers.map(columnFor);
  if (cols.some(c => !c)) return { rows: [], index: [] };
  const rows = [], index = [], dates = DATA.returns.dates;
  for (let i = 0; i < dates.length; i++) {
    if (restrict && !restrict.has(dates[i])) continue;
    let ok = true; const row = new Array(cols.length);
    for (let j = 0; j < cols.length; j++) {
      const v = cols[j][i];
      if (v === null || v === undefined || !isFinite(v)) { ok = false; break; }
      row[j] = v;
    }
    if (ok) { rows.push(row); index.push(dates[i]); }
  }
  return { rows, index };
}

function ewmaVol(series) {
  const n = series.length; if (n < 30) return NaN;
  const mean = series.reduce((a, b) => a + b, 0) / n;
  let weight = 1, total = 0, acc = 0;
  for (let i = n - 1; i >= 0; i--) {
    const d = series[i] - mean; acc += weight * d * d; total += weight; weight *= LAMBDA;
  }
  return Math.sqrt((acc / total) * TRADING_DAYS);
}

function correlation(a, b) {
  const n = Math.min(a.length, b.length); if (n < 30) return NaN;
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
  const n = Math.min(series.length, bench.length); if (n < 30) return NaN;
  let ms = 0, mb = 0;
  for (let i = 0; i < n; i++) { ms += series[i]; mb += bench[i]; }
  ms /= n; mb /= n;
  let cov = 0, varb = 0;
  for (let i = 0; i < n; i++) {
    const db = bench[i] - mb;
    cov += (series[i] - ms) * db; varb += db * db;
  }
  return varb ? cov / varb : NaN;
}

function drawdownCurve(series) {
  let level = 1, peak = 1; const out = [];
  for (const r of series) {
    level *= 1 + r; if (level > peak) peak = level;
    out.push((level / peak - 1) * 100);
  }
  return out;
}

function levelCurve(series, base = 100) {
  let level = base; const out = [];
  for (const r of series) { level *= 1 + r; out.push(level); }
  return out;
}

function statsFor(tickers, weights, value, restrict) {
  const { rows, index } = alignedRows([...tickers, DATA.benchmarkTicker], restrict);
  if (!rows.length) return null;
  const w = weights.concat([0]);
  const series = rows.map(row => row.reduce((a, v, j) => a + v * w[j], 0));
  const bench = rows.map(r => r[r.length - 1]);
  const vol = ewmaVol(series);
  const years = rows.length / TRADING_DAYS;
  const total = series.reduce((a, r) => a * (1 + r), 1) - 1;
  const cagr = years >= 1 ? Math.pow(1 + total, 1 / years) - 1 : total;
  const sorted = [...series].sort((a, b) => a - b);
  const tailIdx = Math.max(0, Math.floor(sorted.length * 0.05) - 1);
  return {
    vol: vol * 100, beta: beta(series, bench),
    maxDrawdown: Math.min(...drawdownCurve(series)),
    sharpe: vol ? (cagr - 0.02) / vol : NaN,
    var95: value * Z95 * vol / Math.sqrt(TRADING_DAYS),
    varHist: -sorted[tailIdx] * value,
    cagr: cagr * 100, total: total * 100,
    series, bench, index, days: rows.length,
    from: index[0], to: index[index.length - 1],
  };
}

/* ---------------- holdings state ---------------- */

const sheetHoldings = () => view().holdings
  .filter(h => h.tradable && columnFor(h.ticker))
  .map(h => ({ ticker: h.ticker, name: h.name, value: h.value_eur }));

function loadHoldings() {
  try { const s = localStorage.getItem(STORE_KEY_FOR(BOOK)); if (s) return JSON.parse(s); }
  catch (e) { /* private window or storage off */ }
  return null;
}
function saveHoldings() {
  try { localStorage.setItem(STORE_KEY_FOR(BOOK), JSON.stringify(holdings)); } catch (e) {}
}
function resetHoldings() {
  holdings = sheetHoldings();
  try { localStorage.removeItem(STORE_KEY_FOR(BOOK)); } catch (e) {}
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
  return { tickers: live.map(h => h.ticker), weights: live.map(h => h.value / total), value: total };
}

/* ---------------- formatting + DOM ---------------- */

const ok = v => v !== null && v !== undefined && isFinite(v);
const fmtPct = v => ok(v) ? `${v.toFixed(2)}%` : "–";
const fmtPct1 = v => ok(v) ? `${v.toFixed(1)}%` : "–";
const fmtNum = v => ok(v) ? v.toFixed(2) : "–";
const fmtEur = v => ok(v) ? "€" + Math.round(v).toLocaleString("en-IE") : "–";
const signed = v => (v >= 0 ? "+" : "") + v.toFixed(2);

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

function table(headers, rows, opts = {}) {
  const t = el("table", { class: "grid" });
  t.append(el("thead", {}, el("tr", {}, headers.map((h, i) =>
    el("th", { class: opts.numFrom !== undefined && i >= opts.numFrom ? "num" : "" }, h)))));
  const body = el("tbody");
  for (const row of rows) {
    const tr = el("tr", { class: row.cls || "" });
    row.cells.forEach((c, i) => tr.append(el("td", {
      class: (opts.numFrom !== undefined && i >= opts.numFrom ? "num " : "") + (c.cls || ""),
    }, c.node !== undefined ? c.node : c.text !== undefined ? c.text : c)));
    body.append(tr);
  }
  t.append(body);
  return t;
}

function chart(id, config) {
  if (charts[id]) charts[id].destroy();
  const el_ = document.getElementById(id);
  if (!el_) return;
  Chart.defaults.font.family = '"Segoe UI",-apple-system,Helvetica,Arial,sans-serif';
  Chart.defaults.color = SLATE;
  Chart.defaults.font.size = 11.5;
  charts[id] = new Chart(el_, config);
}

const gridScale = (extra = {}) => Object.assign({
  grid: { color: LINE, drawTicks: false }, border: { display: false },
}, extra);

/* ---------------- overview ---------------- */

/* Inverse Herfindahl. Twenty lines where one is 40% behaves like far fewer
   than twenty, and the count is the honest headline for that. */
function effectiveHoldings() {
  const { weights } = currentWeights();
  const hhi = weights.reduce((a, w) => a + w * w, 0);
  return hhi ? 1 / hhi : 0;
}

function renderKpis(stats, value) {
  const box = document.getElementById("kpis");
  box.innerHTML = "";
  // The headline is everything you have, not the tradable slice. Leading
  // with the priced half made a EUR 29k book read as EUR 19k, because cash,
  // the deposit and the unlisted line sat in a subtitle.
  const parked = view().parked;
  const cards = [
    ["Total", fmtEur(value + parked),
     `${fmtEur(value)} priced + ${fmtEur(parked)} cash & deposits`],
    ["Return p.a.", fmtPct(stats.cagr), `over ${(stats.days / TRADING_DAYS).toFixed(1)} years`],
    ["Volatility", fmtPct(stats.vol), "annualised, EWMA"],
    ["Beta", fmtNum(stats.beta), `vs ${DATA.benchmarkTicker}`],
    ["Worst drawdown", fmtPct(stats.maxDrawdown), "peak to trough, in sample"],
    ["1-day VaR 95%", fmtEur(stats.var95), `historical ${fmtEur(stats.varHist)}`],
    ["Income", fmtEur(view().income.annual_eur), `${view().income.portfolio_yield}% yield`],
    ["Diversification", effectiveHoldings().toFixed(1), `lines behaving like this many equal ones`],
  ];
  for (const [k, v, s] of cards) {
    box.append(el("div", { class: "kpi" },
      el("div", { class: "k" }, k), el("div", { class: "v" }, v), el("div", { class: "s" }, s)));
  }
}

function renderGrowth(mine) {
  const window_ = new Set(mine.index);
  const datasets = [{
    label: "My book", data: levelCurve(mine.series), borderColor: "#000",
    borderWidth: 2.4, pointRadius: 0, tension: .18, order: 0,
  }];
  const picks = ["iwda", "cspx", "aggh", "sgln"];
  picks.forEach((id, i) => {
    const fund = DATA.funds.find(f => f.id === id);
    if (!fund) return;
    const s = statsFor([fund.ticker], [1], 1, window_);
    if (!s) return;
    datasets.push({
      label: fund.name.replace(/ UCITS ETF.*| ETC.*/, ""), data: levelCurve(s.series),
      borderColor: PALETTE[i * 2], borderWidth: 1.5, pointRadius: 0, tension: .18,
    });
  });
  chart("chartGrowth", {
    type: "line",
    data: { labels: mine.index, datasets },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10, padding: 14 } },
        datalabels: { display: false },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: €${c.parsed.y.toFixed(1)}` } },
      },
      scales: {
        x: gridScale({ type: "time" in Chart.registry.scales ? "category" : "category",
                       ticks: { maxTicksLimit: 8, autoSkip: true }, grid: { display: false } }),
        y: gridScale({ ticks: { callback: v => "€" + v } }),
      },
    },
  });
  document.getElementById("growthNote").textContent =
    `€100 invested on ${mine.from}, rebased. All lines share the book's window (${(mine.days / TRADING_DAYS).toFixed(1)} years) — the shortest holding decides how far back the comparison can honestly go.`;
}

function donut(id, entries, note) {
  const labels = entries.map(e => e[0]), values = entries.map(e => e[1]);
  chart(id, {
    type: "doughnut",
    data: { labels, datasets: [{ data: values, backgroundColor: PALETTE,
            borderColor: "#fff", borderWidth: 2 }] },
    options: {
      maintainAspectRatio: false, cutout: "56%",
      plugins: {
        legend: { position: "right", labels: { boxWidth: 10, boxHeight: 10, padding: 9,
                  font: { size: 11 } } },
        datalabels: {
          color: "#fff", font: { size: 10, weight: 600 },
          formatter: (v, c) => v >= 7 ? v.toFixed(0) + "%" : "",
        },
        tooltip: { callbacks: { label: c => `${c.label}: ${c.parsed.toFixed(1)}%` } },
      },
    },
    plugins: [ChartDataLabels],
  });
}

function topN(obj, n) {
  const entries = Object.entries(obj);
  const head = entries.slice(0, n);
  const rest = entries.slice(n).reduce((a, e) => a + e[1], 0);
  if (rest > 0.05) head.push(["Other", rest]);
  return head;
}

function renderRiskBars() {
  const rows = view().riskContributions.slice(0, 10);
  chart("chartRisk", {
    type: "bar",
    data: {
      labels: rows.map(r => r.ticker),
      datasets: [
        { label: "Weight", data: rows.map(r => r.weight), backgroundColor: "#B8C4CC" },
        { label: "Risk share", data: rows.map(r => r.riskShare), backgroundColor: "#0F7E82" },
      ],
    },
    options: {
      maintainAspectRatio: false, indexAxis: "y",
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10 } },
                 datalabels: { display: false },
                 tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.parsed.x.toFixed(1)}%` } } },
      scales: { x: gridScale({ ticks: { callback: v => v + "%" } }),
                y: gridScale({ grid: { display: false } }) },
    },
  });
}

function renderYears(mine) {
  const line = DATA.lines.find(l => l.id === "me");
  const rows = line ? line.performance.discrete : [];
  chart("chartYears", {
    type: "bar",
    data: {
      labels: rows.map(r => r.year),
      datasets: [{ label: "My book", data: rows.map(r => r.value),
        backgroundColor: rows.map(r => r.value >= 0 ? "#1E7A46" : "#B9002F") }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { display: false },
        datalabels: { anchor: "end", align: "end", color: SLATE, font: { size: 10 },
                      formatter: v => v.toFixed(1) + "%" },
        tooltip: { callbacks: { label: c => c.parsed.y.toFixed(2) + "%" } } },
      scales: { x: gridScale({ grid: { display: false } }),
                y: gridScale({ ticks: { callback: v => v + "%" } }) },
    },
    plugins: [ChartDataLabels],
  });
}

/* ---------------- holdings ---------------- */

function renderHoldings() {
  const box = document.getElementById("holdings");
  box.innerHTML = "";
  const { value } = currentWeights();

  const rows = holdings.map((h, i) => ({
    cells: [
      h.name || h.ticker,
      { node: el("span", { class: "mono" }, h.ticker) },
      { node: el("input", { type: "number", value: h.value.toFixed(2), step: "10", min: "0",
          class: "val", oninput: e => {
            holdings[i].value = parseFloat(e.target.value) || 0;
            saveHoldings(); renderDerived(); refreshWeights();
          } }) },
      { text: value ? fmtPct((h.value / value) * 100) : "–", cls: "weight" },
      { node: el("button", { class: "link", onclick: () => {
          holdings.splice(i, 1); saveHoldings(); renderAll(); } }, "remove") },
    ],
  }));
  box.append(table(["Holding", "Ticker", "Value (€)", "Weight", ""], rows, { numFrom: 2 }));

  const options = Object.keys(DATA.returns.series)
    .filter(t => t !== "__benchmark__" && !holdings.some(h => h.ticker === t));
  const select = el("select", {}, el("option", { value: "" }, "add a line…"),
    options.map(t => {
      const fund = DATA.funds.find(f => f.ticker === t);
      return el("option", { value: t }, fund ? `${t} — ${fund.name}` : t);
    }));
  const amount = el("input", { type: "number", value: "1000", step: "100", class: "val" });
  box.append(el("div", { class: "controls", style: "margin-top:14px" }, select, amount,
    el("button", { class: "btn", onclick: () => {
      if (!select.value) return;
      const fund = DATA.funds.find(f => f.ticker === select.value);
      holdings.push({ ticker: select.value, name: fund ? fund.name : select.value,
                      value: parseFloat(amount.value) || 0 });
      saveHoldings(); renderAll();
    } }, "add"),
    el("button", { class: "btn ghost", onclick: resetHoldings }, "reset to sheet")));
}

function refreshWeights() {
  const { value } = currentWeights();
  document.querySelectorAll("#holdings tbody tr").forEach((row, i) => {
    const cell = row.querySelector(".weight");
    if (cell && holdings[i]) cell.textContent = value ? fmtPct((holdings[i].value / value) * 100) : "–";
  });
}

function renderConcentration(value) {
  const sorted = [...holdings].filter(h => h.value > 0).sort((a, b) => b.value - a.value).slice(0, 12);
  chart("chartWeights", {
    type: "bar",
    data: { labels: sorted.map(h => h.ticker),
      datasets: [{ data: sorted.map(h => 100 * h.value / value), backgroundColor: "#14527A" }] },
    options: {
      maintainAspectRatio: false, indexAxis: "y",
      plugins: { legend: { display: false }, datalabels: { display: false },
                 tooltip: { callbacks: { label: c => c.parsed.x.toFixed(1) + "%" } } },
      scales: { x: gridScale({ ticks: { callback: v => v + "%" } }),
                y: gridScale({ grid: { display: false } }) },
    },
  });
  const weights = holdings.filter(h => h.value > 0).map(h => h.value / value);
  const hhi = weights.reduce((a, w) => a + w * w, 0);
  document.getElementById("concNote").textContent =
    `Inverse Herfindahl: this book of ${weights.length} lines behaves like ${(1 / hhi).toFixed(1)} equally-sized positions.`;
}

function renderHeatmap() {
  const box = document.getElementById("heatmapBox");
  box.innerHTML = "";
  const { tickers } = view().correlations;
  const present = tickers.filter(t => holdings.some(h => h.ticker === t && h.value > 0));
  const use = present.length >= 3 ? present : tickers;
  const window_ = new Set(alignedRows(use).index);
  const series = {};
  for (const t of use) {
    const s = statsFor([t], [1], 1, window_);
    if (s) series[t] = s.series;
  }
  const live = use.filter(t => series[t]);

  const t = el("table", { id: "heatmap" });
  t.append(el("thead", {}, el("tr", {}, el("th", {}, ""), live.map(x => el("th", {}, x)))));
  const body = el("tbody");
  for (const a of live) {
    const tr = el("tr", {}, el("th", {}, a));
    for (const b of live) {
      const c = a === b ? 1 : correlation(series[a], series[b]);
      const shade = Math.max(0, Math.min(1, (c + 0.2) / 1.2));
      tr.append(el("td", {
        class: "cell",
        style: `background:rgba(15,126,130,${(0.08 + shade * 0.9).toFixed(2)})`,
        title: `${a} / ${b}: ${c.toFixed(2)}`,
      }, c.toFixed(1)));
    }
    body.append(tr);
  }
  t.append(body);
  box.append(t);
}

function renderIncome() {
  const box = document.getElementById("incomeBox");
  box.innerHTML = "";
  const rows = view().income.top.map(r => ({
    cells: [r.name || r.ticker, { node: el("span", { class: "mono" }, r.ticker) },
            fmtPct(r.yield), fmtEur(r.annual_eur)],
  }));
  box.append(el("p", { class: "muted", style: "margin-bottom:12px" },
    `${fmtEur(view().income.annual_eur)} a year at today's yields — a ${view().income.portfolio_yield}% book yield. That is ${(view().income.annual_eur / (20000 * 12) * 100).toFixed(2)}% of a €20k-a-month retirement income.`));
  box.append(table(["Payer", "Ticker", "Yield", "Annual €"], rows, { numFrom: 2 }));
}

/* ---------------- compare ---------------- */

function fundRows(mine, value) {
  const window_ = new Set(mine.index);
  const rows = [{ id: "me", name: "My book", asset: "Your holdings", kind: "portfolio",
                  ...mine, corr: 1 }];
  for (const fund of DATA.funds) {
    const s = statsFor([fund.ticker], [1], value, window_);
    if (!s || s.days < 60) continue;
    rows.push({ id: fund.id, name: fund.name, asset: fund.asset, kind: "fund",
                ...s, corr: correlation(s.series, mine.series) });
  }
  return rows;
}

function renderScatter(rows) {
  const funds = rows.filter(r => r.kind === "fund");
  const me = rows.find(r => r.kind === "portfolio");
  chart("chartScatter", {
    type: "scatter",
    data: {
      datasets: [
        { label: "Funds", data: funds.map(r => ({ x: r.vol, y: r.cagr, n: r.name })),
          backgroundColor: "#4A7FA5", pointRadius: 6, pointHoverRadius: 8 },
        { label: "My book", data: [{ x: me.vol, y: me.cagr, n: "My book" }],
          backgroundColor: "#000", pointRadius: 9, pointStyle: "rectRot" },
      ],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10 } },
        datalabels: { display: false },
        tooltip: { callbacks: { label: c =>
          `${c.raw.n}: ${c.parsed.y.toFixed(1)}% return at ${c.parsed.x.toFixed(1)}% vol` } },
      },
      scales: {
        x: gridScale({ title: { display: true, text: "Volatility (annualised)", color: SLATE },
                       ticks: { callback: v => v + "%" } }),
        y: gridScale({ title: { display: true, text: "Return p.a.", color: SLATE },
                       ticks: { callback: v => v + "%" } }),
      },
    },
  });
}

function renderComparison(rows) {
  const box = document.getElementById("comparison");
  box.innerHTML = "";
  const sorted = [...rows].sort((a, b) =>
    a.kind === "portfolio" ? -1 : b.kind === "portfolio" ? 1 : b.sharpe - a.sharpe);
  box.append(table(
    ["Line", "Asset class", "Return p.a.", "Vol", "Sharpe", "Beta", "Max DD", "Corr. to book"],
    sorted.map(r => ({
      cls: r.kind === "portfolio" ? "me" : "",
      cells: [r.name, { text: r.asset, cls: "muted" }, fmtPct(r.cagr), fmtPct(r.vol),
              fmtNum(r.sharpe), fmtNum(r.beta),
              { text: fmtPct(r.maxDrawdown), cls: "neg" }, fmtNum(r.corr)],
    })), { numFrom: 2 }));
}

function renderAdditions(tickers, weights, value, mine) {
  const allocation = parseFloat(document.getElementById("alloc").value) / 100;
  const box = document.getElementById("additions");
  box.innerHTML = "";
  const window_ = new Set(mine.index);
  const out = [];
  for (const fund of DATA.funds) {
    if (tickers.includes(fund.ticker)) continue;
    const after = statsFor([...tickers, fund.ticker],
      [...weights.map(w => w * (1 - allocation)), allocation], value, window_);
    if (!after || after.days < 60) continue;
    // Baseline on the mixture's own dates, so a fund with a shorter history is
    // not charged for a stretch the book was measured over and it was not.
    const shared = new Set(after.index);
    const base = statsFor(tickers, weights, value, shared);
    const solo = statsFor([fund.ticker], [1], value, shared);
    if (!base || !solo) continue;
    out.push({ fund, newVol: after.vol, volChange: after.vol - base.vol,
               newBeta: after.beta, betaChange: after.beta - base.beta,
               retChange: after.cagr - base.cagr,
               corr: correlation(solo.series, base.series) });
  }
  out.sort((a, b) => a.volChange - b.volChange);
  box.append(table(
    [`Add at ${(allocation * 100).toFixed(0)}%`, "Corr.", "New vol", "Δ vol", "Δ return", "Δ beta"],
    out.map(r => ({
      cells: [r.fund.name, fmtNum(r.corr), fmtPct(r.newVol),
        { text: signed(r.volChange) + "pp", cls: r.volChange < 0 ? "pos" : "neg" },
        { text: signed(r.retChange) + "pp", cls: r.retChange >= 0 ? "pos" : "neg" },
        signed(r.betaChange)],
    })), { numFrom: 1 }));
}

function renderFrontier() {
  const select = document.getElementById("frontierFund");
  const curve = DATA.frontiers[select.value];
  if (!curve) return;
  chart("chartFrontier", {
    type: "scatter",
    data: {
      datasets: [{
        label: "Mix", data: curve.map(p => ({ x: p.vol, y: p.return, w: p.fundWeight })),
        showLine: true, borderColor: "#0F7E82", borderWidth: 2,
        backgroundColor: curve.map(p => p.fundWeight === 0 ? "#000" : "#0F7E82"),
        pointRadius: curve.map(p => p.fundWeight === 0 ? 7 : 3),
      }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { label: c =>
          `${c.raw.w}% fund: ${c.parsed.y.toFixed(1)}% return at ${c.parsed.x.toFixed(1)}% vol` } },
      },
      scales: {
        x: gridScale({ title: { display: true, text: "Volatility", color: SLATE },
                       ticks: { callback: v => v + "%" } }),
        y: gridScale({ title: { display: true, text: "Return p.a.", color: SLATE },
                       ticks: { callback: v => v + "%" } }),
      },
    },
  });
}

/* ---------------- map ---------------- */

/* Equirectangular, clipped to the latitudes anyone actually invests in.
   Antarctica is 8% of the land area and 0% of the book, so dropping it
   below 60°S buys real map for the rest. */
function project([lon, lat]) {
  return [(lon + 180) * (1000 / 360), (85 - lat) * (500 / 145)];
}

function pathFor(geometry) {
  const rings = geometry.type === "Polygon" ? [geometry.coordinates]
              : geometry.type === "MultiPolygon" ? geometry.coordinates : [];
  let d = "";
  for (const polygon of rings) {
    for (const ring of polygon) {
      let previousLon = null, open = false;
      for (const pt of ring) {
        // Alaska and Chukotka straddle the antimeridian, so consecutive
        // points jump from +179 to -179. Drawn straight through, that is a
        // line sweeping the full width of the map. Break the subpath there
        // instead: a hairline seam at the dateline beats a band across
        // everything.
        const jumped = previousLon !== null && Math.abs(pt[0] - previousLon) > 180;
        const [x, y] = project(pt);
        d += (open && !jumped ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
        open = true;
        previousLon = pt[0];
      }
      d += "Z";
    }
  }
  return d;
}

function mapColour(weight, max) {
  if (!weight) return "#E4E1DC";
  const t = Math.sqrt(weight / max);            // sqrt: one 78% country would
  const stops = ["#D6E5E5", "#9BC7C8", "#4A9EA1", "#0F7E82", "#0A5C5F"];
  return stops[Math.min(stops.length - 1, Math.floor(t * stops.length))];
}

async function renderMap() {
  const svg = document.getElementById("worldmap");
  if (!worldGeo) {
    const topo = await (await fetch("vendor/countries-110m.json")).json();
    worldGeo = topojson.feature(topo, topo.objects.countries);
  }
  const weights = view().exposure.countries;
  const max = Math.max(...Object.values(weights), 1);
  svg.innerHTML = "";
  const tip = document.getElementById("maptip");

  for (const feature of worldGeo.features) {
    const name = feature.properties.name;
    const weight = weights[name] || 0;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", pathFor(feature.geometry));
    path.setAttribute("fill", mapColour(weight, max));
    path.setAttribute("class", "hit");
    path.addEventListener("mousemove", ev => {
      tip.textContent = weight ? `${name} — ${weight.toFixed(1)}%` : `${name} — no exposure`;
      const box = svg.getBoundingClientRect();
      tip.style.left = (ev.clientX - box.left + 12) + "px";
      tip.style.top = (ev.clientY - box.top + 12) + "px";
      tip.style.opacity = 1;
    });
    path.addEventListener("mouseleave", () => { tip.style.opacity = 0; });
    svg.append(path);
  }

  const legend = document.getElementById("mapLegend");
  legend.innerHTML = "";
  const stops = ["#E4E1DC", "#D6E5E5", "#9BC7C8", "#4A9EA1", "#0F7E82", "#0A5C5F"];
  const labels = ["none", "small", "", "", "", `largest (${max.toFixed(0)}%)`];
  stops.forEach((c, i) => legend.append(el("span", {},
    el("i", { style: `background:${c}` }), labels[i] || "")));

  document.getElementById("mapNote").textContent =
    `${view().exposure.lookThroughWeight.toFixed(0)}% of the book sits in funds and has been exploded through its index's published country weights. Those are hand-entered and approximate — read the shape, not the decimals.`;

  const rows = Object.entries(weights).map(([k, v]) => ({ cells: [k, fmtPct1(v)] }));
  document.getElementById("countryTable").replaceChildren(
    table(["Country", "Weight"], rows, { numFrom: 1 }));
  const sectors = Object.entries(view().exposure.sectors).map(([k, v]) => ({ cells: [k, fmtPct1(v)] }));
  document.getElementById("sectorTable").replaceChildren(
    table(["Sector", "Weight"], sectors, { numFrom: 1 }));
}

/* ---------------- simulate ----------------
   Run in the browser rather than read from the payload, because the horizon,
   the monthly top-up and the drift are all controls. 6,000 paths keeps a
   slider drag under a frame budget; the percentiles are stable to well
   inside the width of the band by then. */

function bootstrapPaths(daily, startValue, months, monthly, paths, driftShift) {
  const step = Math.floor(TRADING_DAYS / 12), block = 21;
  const totalDays = months * step;
  const nBlocks = Math.ceil(totalDays / block);
  const maxStart = daily.length - block;
  const track = new Float64Array(paths * months);
  const dailyShift = driftShift / TRADING_DAYS;

  for (let p = 0; p < paths; p++) {
    let value = startValue, day = 0, month = 0, growth = 1;
    for (let b = 0; b < nBlocks && day < totalDays; b++) {
      const start = Math.floor(Math.random() * (maxStart + 1));
      for (let i = 0; i < block && day < totalDays; i++, day++) {
        growth *= 1 + daily[start + i] + dailyShift;
        if ((day + 1) % step === 0 && month < months) {
          value = value * growth + monthly;
          growth = 1;
          track[p * months + month] = value;
          month++;
        }
      }
    }
    for (; month < months; month++) track[p * months + month] = value;
  }
  return track;
}

function percentile(sorted, p) {
  const idx = (sorted.length - 1) * p;
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

function runSimulation(mine, value) {
  const years = parseInt(document.getElementById("mcYears").value, 10);
  const monthly = parseFloat(document.getElementById("mcMonthly").value) || 0;
  const driftInput = parseFloat(document.getElementById("mcDrift").value);
  const months = years * 12, paths = 6000;

  const daily = mine.series;
  const sampleDrift = daily.reduce((a, b) => a + b, 0) / daily.length * TRADING_DAYS;
  const target = isFinite(driftInput) ? driftInput / 100 : sampleDrift;
  const track = bootstrapPaths(daily, value, months, monthly, paths, target - sampleDrift);

  const fan = [];
  const stepMonths = Math.max(1, Math.round(months / 20));
  for (let m = stepMonths - 1; m < months; m += stepMonths) {
    const col = new Float64Array(paths);
    for (let p = 0; p < paths; p++) col[p] = track[p * months + m];
    col.sort();
    fan.push({ month: m + 1, p05: percentile(col, .05), p25: percentile(col, .25),
               median: percentile(col, .5), p75: percentile(col, .75), p95: percentile(col, .95) });
  }

  const boxes = [];
  for (const y of [1, 3, 5, 10, 20]) {
    if (y > years) continue;
    const m = y * 12 - 1;
    const col = new Float64Array(paths);
    for (let p = 0; p < paths; p++) col[p] = track[p * months + m];
    col.sort();
    const paidIn = value + monthly * (m + 1);
    let losses = 0;
    for (let p = 0; p < paths; p++) if (col[p] < paidIn) losses++;
    boxes.push({ label: `${y} yr`, p05: percentile(col, .05), p25: percentile(col, .25),
                 median: percentile(col, .5), p75: percentile(col, .75),
                 p95: percentile(col, .95), paidIn, probLoss: losses / paths });
  }

  renderFan(fan, value, monthly, months);
  renderBoxes(boxes);
  renderMcSummary(boxes, value, monthly, months, target, sampleDrift, daily.length);
}

function renderFan(fan, startValue, monthly, months) {
  const labels = fan.map(f => (f.month / 12).toFixed(1) + "y");
  const band = (from, to, colour) => ({
    label: "", data: fan.map(f => f[to]), fill: { target: "-1" },
    backgroundColor: colour, borderWidth: 0, pointRadius: 0, tension: .2, order: 3,
  });
  chart("chartFan", {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "5th percentile", data: fan.map(f => f.p05), borderColor: "#9BC7C8",
          borderWidth: 1, pointRadius: 0, tension: .2, fill: false },
        band("p05", "p25", "rgba(155,199,200,.35)"),
        band("p25", "median", "rgba(74,158,161,.35)"),
        band("median", "p75", "rgba(74,158,161,.35)"),
        band("p75", "p95", "rgba(155,199,200,.35)"),
        { label: "Median", data: fan.map(f => f.median), borderColor: "#000",
          borderWidth: 2.4, pointRadius: 0, tension: .2, order: 0, fill: false },
        { label: "Paid in", data: fan.map(f => startValue + monthly * f.month),
          borderColor: "#B07C1F", borderWidth: 1.5, borderDash: [5, 4],
          pointRadius: 0, tension: 0, order: 1, fill: false },
      ],
    },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10,
          filter: item => item.text && item.text !== "" } },
        datalabels: { display: false },
        tooltip: { filter: i => i.dataset.label !== "",
                   callbacks: { label: c => `${c.dataset.label}: ${fmtEur(c.parsed.y)}` } },
      },
      scales: { x: gridScale({ grid: { display: false } }),
                y: gridScale({ ticks: { callback: v => "€" + (v / 1000).toFixed(0) + "k" } }) },
    },
  });
}

/* Chart.js has no box plot, so it is drawn as a floating bar for the
   interquartile range with the median and whiskers overlaid. The Sheets
   version faked this with a candlestick that could not draw a median at
   all — the median is the whole point, so here it is a real line. */
function renderBoxes(boxes) {
  chart("chartBox", {
    type: "bar",
    data: {
      labels: boxes.map(b => b.label),
      datasets: [
        { label: "5th–95th", data: boxes.map(b => [b.p05, b.p95]),
          backgroundColor: "rgba(155,199,200,.45)", barPercentage: .35, order: 3 },
        { label: "Middle half", data: boxes.map(b => [b.p25, b.p75]),
          backgroundColor: "#4A9EA1", barPercentage: .7, order: 2 },
        { label: "Median", data: boxes.map(b => [b.median * 0.998, b.median * 1.002]),
          backgroundColor: "#000", barPercentage: .78, order: 1 },
      ],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10 } },
        datalabels: { display: false },
        tooltip: { callbacks: { label: c => {
          const b = boxes[c.dataIndex];
          return c.datasetIndex === 2 ? `Median ${fmtEur(b.median)}`
               : c.datasetIndex === 1 ? `${fmtEur(b.p25)} to ${fmtEur(b.p75)}`
               : `${fmtEur(b.p05)} to ${fmtEur(b.p95)}`;
        } } },
      },
      scales: { x: gridScale({ stacked: false, grid: { display: false } }),
                y: gridScale({ ticks: { callback: v => "€" + (v / 1000).toFixed(0) + "k" } }) },
    },
  });
}

function renderMcSummary(boxes, value, monthly, months, target, sampleDrift, sampleDays) {
  const last = boxes[boxes.length - 1];
  const box = document.getElementById("mcSummary");
  box.innerHTML = "";
  if (!last) return;
  box.append(table(["At " + last.label, ""], [
    { cells: ["Paid in", fmtEur(last.paidIn)] },
    { cells: ["Median outcome", fmtEur(last.median)] },
    { cells: ["Bad case (5th pct)", fmtEur(last.p05)] },
    { cells: ["Good case (95th pct)", fmtEur(last.p95)] },
    { cells: ["Chance of ending below what you paid in", fmtPct(last.probLoss * 100)] },
  ], { numFrom: 1 }));

  document.getElementById("mcWarning").textContent =
    `Paths are resampled from ${(sampleDays / TRADING_DAYS).toFixed(1)} years of this book's own returns, which annualise to ${(sampleDrift * 100).toFixed(0)}%` +
    (Math.abs(target - sampleDrift) > 0.001
      ? `; you have overridden the drift to ${(target * 100).toFixed(1)}%.`
      : `. That is an extrapolation of a short and bullish sample, not a forecast — no equity book has sustained that over a decade. Read the width of the band, not the middle of it.`);
}

/* ---------------- stress ---------------- */

function renderStress(mine) {
  const curve = drawdownCurve(mine.series);
  chart("chartDrawdown", {
    type: "line",
    data: {
      labels: mine.index,
      datasets: [{ label: "Below previous peak", data: curve, borderColor: "#B9002F",
        backgroundColor: "rgba(185,0,47,.13)", fill: true, borderWidth: 1.6,
        pointRadius: 0, tension: .1 }],
    },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { label: c => c.parsed.y.toFixed(1) + "% below peak" } } },
      scales: { x: gridScale({ ticks: { maxTicksLimit: 8 }, grid: { display: false } }),
                y: gridScale({ ticks: { callback: v => v + "%" } }) },
    },
  });

  document.getElementById("windowsBox").replaceChildren(table(
    ["Rolling window", "Worst", "Best", "Worst ended"],
    view().stress.worstWindows.map(w => ({
      cells: [w.window, { text: fmtPct(w.return), cls: w.return < 0 ? "neg" : "" },
              { text: fmtPct(w.best), cls: "pos" }, w.ended],
    })), { numFrom: 1 }));

  const episodes = view().stress.episodes;
  const box = document.getElementById("episodeBox");
  box.innerHTML = "";
  if (!episodes.length) {
    box.append(el("p", { class: "muted" },
      "None of the named episodes fall inside this book's history."));
  } else {
    box.append(table(["Episode", "Window", "Book"], episodes.map(e => ({
      cells: [e.name + (e.partial ? " (partial)" : ""), `${e.from} → ${e.to}`,
              { text: fmtPct(e.return), cls: e.return < 0 ? "neg" : "pos" }],
    })), { numFrom: 2 }));
    box.append(el("p", { class: "note muted", style: "padding:12px 0 0" },
      "A book that only starts in 2025 cannot be shown a Covid number, and splicing index returns onto it to fill the gap would be a fabrication dressed as a stress test."));
  }
}

/* ---------------- wiring ---------------- */

const TABS = [
  ["overview", "Overview"], ["holdings", "Holdings"], ["transactions", "Transactions"],
  ["advice", "Advice"], ["compare", "Against the funds"], ["map", "Map & exposure"],
  ["simulate", "Simulate"], ["stress", "Stress"], ["pension", "Pension"],
];

function renderTabs() {
  const box = document.getElementById("tabs");
  box.innerHTML = "";
  TABS.forEach(([id, label], i) => {
    const button = el("button", {
      role: "tab", "aria-selected": i === 0 ? "true" : "false",
      onclick: () => selectTab(id),
    }, label);
    button.dataset.tab = id;
    box.append(button);
  });
}

function selectTab(id) {
  document.querySelectorAll(".view").forEach(v =>
    v.classList.toggle("on", v.id === "view-" + id));
  document.querySelectorAll("#tabs button").forEach(b =>
    b.setAttribute("aria-selected", b.dataset.tab === id ? "true" : "false"));
  location.hash = id;
  if (id === "map") renderMap();
  if (id === "transactions") renderTransactions();
  if (id === "pension") renderPension();
  if (id === "simulate") {
    const { tickers, weights, value } = currentWeights();
    runSimulation(statsFor(tickers, weights, value), value);
  }
}

function renderDerived() {
  const { tickers, weights, value } = currentWeights();
  const mine = statsFor(tickers, weights, value);
  document.getElementById("editBanner").style.display = isEdited() ? "" : "none";
  if (!mine) return;

  renderKpis(mine, value);
  renderGrowth(mine);
  donut("chartSector", topN(view().exposure.sectors, 9));
  donut("chartCurrency", topN(view().exposure.currencies, 6));
  renderRiskBars();
  renderYears(mine);
  renderConcentration(value);
  renderHeatmap();
  renderIncome();

  const rows = fundRows(mine, value);
  renderScatter(rows);
  renderComparison(rows);
  renderAdditions(tickers, weights, value, mine);
  renderFrontier();
  renderStress(mine);
  renderAdvice(mine, value);

  if (document.getElementById("view-simulate").classList.contains("on")) {
    runSimulation(mine, value);
  }
}

function renderAll() { renderHoldings(); renderDerived(); }

/* Switching book swaps the entire dataset, including which edits apply:
   holdings are stored per book, so an edit to Catalin's does not follow
   you into Stefani's. */
function selectBook(name) {
  BOOK = name;
  const picker = document.getElementById("bookSelect");
  if (picker && picker.value !== name) picker.value = name;   // keep them in step
  holdings = (loadHoldings() || sheetHoldings()).filter(h => columnFor(h.ticker));
  document.getElementById("strapline").textContent =
    BOOK === "Combined"
      ? "both books together — a reporting view, never a book you can trade against"
      : `${BOOK}'s book, measured against the funds you could buy instead`;
  renderAll();
  if (document.getElementById("view-map").classList.contains("on")) renderMap();
  if (document.getElementById("view-transactions").classList.contains("on")) renderTransactions();
}

function renderCaveats() {
  const list = el("ul");
  for (const c of DATA.caveats) list.append(el("li", {}, c));
  document.getElementById("caveats").replaceChildren(list);
}

async function boot() {
  DATA = await (await fetch("data.json")).json();
  document.getElementById("asof").innerHTML =
    `Prices as at <strong>${DATA.asOf}</strong><br>built ${DATA.generated.replace("T", " ")}`;
  const picker = document.getElementById("bookSelect");
  for (const name of DATA.books) picker.append(el("option", { value: name }, name));
  picker.value = DATA.defaultBook;
  picker.addEventListener("change", e => selectBook(e.target.value));
  BOOK = DATA.defaultBook;
  holdings = (loadHoldings() || sheetHoldings()).filter(h => columnFor(h.ticker));

  renderTabs();
  const select = document.getElementById("frontierFund");
  for (const fund of DATA.funds) {
    if (DATA.frontiers[fund.id]) select.append(el("option", { value: fund.id }, fund.name));
  }
  select.value = "aggh" in DATA.frontiers ? "aggh" : select.options[0]?.value;
  select.addEventListener("change", renderFrontier);

  document.getElementById("alloc").addEventListener("input", e => {
    document.getElementById("allocLabel").textContent = e.target.value + "%";
    const { tickers, weights, value } = currentWeights();
    renderAdditions(tickers, weights, value, statsFor(tickers, weights, value));
  });

  const rerun = () => {
    const { tickers, weights, value } = currentWeights();
    runSimulation(statsFor(tickers, weights, value), value);
  };
  document.getElementById("mcYears").addEventListener("change", e => {
    document.getElementById("mcYearsLabel").textContent = e.target.value + " yrs";
    rerun();
  });
  document.getElementById("mcYears").addEventListener("input", e => {
    document.getElementById("mcYearsLabel").textContent = e.target.value + " yrs";
  });
  document.getElementById("mcMonthly").addEventListener("change", rerun);
  document.getElementById("mcDrift").addEventListener("change", rerun);
  document.getElementById("mcReset").addEventListener("click", () => {
    document.getElementById("mcDrift").value = "";
    rerun();
  });

  renderCaveats();
  selectBook(DATA.defaultBook);
  const hash = location.hash.replace("#", "");
  if (TABS.some(t => t[0] === hash)) selectTab(hash);
}

boot();

/* ---------------- transactions ---------------- */

/* The ledger lives on the server, so the tab degrades honestly when the API
   is not there: you can still read what the build baked in, but the form
   says why it cannot record anything rather than pretending to. */

async function apiGet(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json();
}

async function checkApi() {
  try {
    const health = await apiGet("/api/health");
    API = true;
    return health;
  } catch (e) { API = false; return null; }
}

function tradeForm(health) {
  const tickers = [...new Set([...holdings.map(h => h.ticker),
                               ...DATA.funds.map(f => f.ticker)])].sort();
  const ticker = el("input", { list: "tickerList", placeholder: "NVDA", style: "width:110px" });
  const datalist = el("datalist", { id: "tickerList" }, tickers.map(t => el("option", { value: t })));
  const action = el("select", {}, el("option", { value: "buy" }, "Bought"),
                                  el("option", { value: "sell" }, "Sold"));
  const shares = el("input", { type: "number", step: "any", min: "0", placeholder: "5", style: "width:90px" });
  const price = el("input", { type: "number", step: "any", min: "0", placeholder: "195.80", style: "width:110px" });
  const when = el("input", { type: "date", value: new Date().toISOString().slice(0, 10) });
  const fee = el("input", { type: "number", step: "any", min: "0", value: "0", style: "width:80px" });
  const note = el("input", { placeholder: "why", style: "width:150px" });
  const toSheet = el("input", { type: "checkbox" });
  toSheet.checked = true;
  const status = el("div", { class: "muted", style: "font-size:13px;margin-top:10px" });

  const submit = el("button", { class: "btn", onclick: async () => {
    if (BOOK === "Combined") {
      status.textContent = "Pick Catalin or Stefani first — Combined is a view, and a trade booked against it could land on either book's row.";
      return;
    }
    const payload = {
      ticker: ticker.value.trim().toUpperCase(), action: action.value,
      shares: parseFloat(shares.value), price: parseFloat(price.value),
      date: when.value, portfolio: BOOK, fee: parseFloat(fee.value) || 0,
      note: note.value.trim(), applyToSheet: toSheet.checked,
    };
    if (!payload.ticker || !(payload.shares > 0)) {
      status.textContent = "Ticker and a positive share count are required.";
      return;
    }
    const verb = payload.action === "buy" ? "Buy" : "Sell";
    const where = toSheet.checked ? " and update the Google Sheet" : " (ledger only)";
    if (!confirm(`${verb} ${payload.shares} ${payload.ticker} at ${payload.price} on ${payload.date} for ${BOOK}${where}?`)) return;

    status.textContent = "recording…";
    submit.disabled = true;
    try {
      const response = await fetch("/api/transactions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || response.status);
      status.innerHTML = result.sheet === "updated"
        ? `Recorded, and the sheet was updated. Rebuild to see it in the charts.`
        : result.sheet === "failed"
          ? `Recorded in the ledger, but the sheet write failed: ${result.sheetError}. Nothing was lost — replay it once the sheet is reachable.`
          : `Recorded in the ledger only.`;
      shares.value = ""; price.value = ""; note.value = "";
      renderTransactions();
    } catch (exc) {
      status.textContent = "Refused: " + exc.message;
    } finally { submit.disabled = false; }
  } }, "record");

  const row = el("div", { class: "controls" },
    action, ticker, datalist,
    el("span", { class: "muted" }, "shares"), shares,
    el("span", { class: "muted" }, "at"), price,
    el("span", { class: "muted" }, "on"), when,
    el("span", { class: "muted" }, "fee"), fee,
    note,
    el("label", { style: "display:flex;gap:6px;align-items:center;text-transform:none;letter-spacing:0" },
      toSheet, "also update the sheet"),
    submit);

  const box = el("div", {});
  if (!API) {
    box.append(el("div", { class: "warnbox" },
      "The transaction API is not answering, so nothing can be recorded from here. " +
      "It runs on the VPS alongside the site; locally, start it with " +
      "AGENT_DIR=… python3 server/api.py --port 8001."));
  } else if (!health.sheetWrites) {
    box.append(el("div", { class: "warnbox" },
      "The API is up but has no AGENT_DIR, so trades are recorded in the ledger " +
      "and not mirrored to the Google Sheet."));
  }
  box.append(row, status);
  return box;
}

async function renderTransactions() {
  const box = document.getElementById("txBox");
  box.innerHTML = "";
  const health = await checkApi();
  box.append(tradeForm(health));

  const listBox = document.getElementById("txList");
  listBox.innerHTML = "";
  if (!API) {
    listBox.append(el("p", { class: "muted" }, "No ledger available without the API."));
    return;
  }
  const data = await apiGet(`/api/transactions?portfolio=${encodeURIComponent(BOOK)}`);
  const { trades, summary } = data;

  const cards = el("div", { class: "kpis", style: "margin-bottom:18px" });
  for (const [k, v, s] of [
    ["Trades recorded", String(summary.count), summary.first ? `since ${summary.first}` : "none yet"],
    ["Cost of open positions", fmtEur(summary.invested), "average cost, per the ledger"],
    ["Realised", fmtEur(summary.realised), "closed out, before tax"],
    ["Fees paid", fmtEur(summary.fees), "as entered"],
  ]) {
    cards.append(el("div", { class: "kpi" }, el("div", { class: "k" }, k),
      el("div", { class: "v" }, v), el("div", { class: "s" }, s)));
  }
  listBox.append(cards);

  if (!trades.length) {
    listBox.append(el("p", { class: "muted" },
      "Nothing recorded yet. The sheet holds share counts but no history, so this ledger starts the day you log your first trade — earlier positions will show no cost basis until you backfill them."));
    return;
  }

  listBox.append(table(["Date", "Book", "Action", "Ticker", "Shares", "Price", "Value", "Sheet", "Note"],
    [...trades].reverse().map(t => ({
      cells: [t.date, t.portfolio,
        { text: t.action === "buy" ? "Bought" : "Sold", cls: t.action === "buy" ? "pos" : "neg" },
        { node: el("span", { class: "mono" }, t.ticker) },
        t.shares, fmtEur(t.price), fmtEur(t.shares * t.price),
        { text: t.applied_to_sheet ? "yes" : "ledger only", cls: "muted" },
        { text: t.note || "", cls: "muted" }],
    })), { numFrom: 4 }));

  const open = Object.values(summary.positions).filter(p => p.shares > 0);
  if (open.length) {
    listBox.append(el("h3", { style: "margin:22px 0 10px;font-size:14px" }, "Cost basis from the ledger"));
    listBox.append(table(["Ticker", "Shares", "Avg cost", "Book cost", "Realised"],
      open.map(p => ({
        cells: [{ node: el("span", { class: "mono" }, p.ticker) }, p.shares,
                fmtEur(p.avg_cost), fmtEur(p.cost),
                { text: fmtEur(p.realised), cls: p.realised >= 0 ? "pos" : "neg" }],
      })), { numFrom: 1 }));
    listBox.append(el("p", { class: "note muted", style: "padding-top:10px" },
      "Average cost, not FIFO. Irish CGT wants FIFO with a four-week rule, so read this as what a position cost you, not as a tax figure."));
  }
}

/* ---------------- advice ---------------- */

function renderAdvice(mine, value) {
  const box = document.getElementById("adviceBox");
  box.innerHTML = "";
  const bookAdvice = view().advice;

  if (bookAdvice.notes.length) {
    const notes = el("div", { class: "panel wide", style: "margin-bottom:20px" },
      el("h3", {}, "What stands out about the shape of this book"));
    const body = el("div", { class: "body" });
    const list = el("ul", { style: "margin-left:18px" });
    for (const note of bookAdvice.notes) list.append(el("li", { style: "margin-bottom:8px" }, note));
    body.append(list);
    notes.append(body);
    box.append(notes);
  }

  const sells = bookAdvice.sell;
  const sellPanel = el("div", { class: "panel wide", style: "margin-bottom:20px" },
    el("h3", {}, "Positions the numbers argue with"));
  const sellBody = el("div", { class: "body" });
  if (!sells.length) {
    sellBody.append(el("p", { class: "muted" },
      "Nothing flagged: no position costs materially more risk than its weight, nothing duplicates anything else above 0.85, and nothing is past the single-name cap."));
  } else {
    for (const row of sells) {
      const item = el("div", { style: "padding:12px 0;border-bottom:1px solid #EFEDE9" },
        el("div", {}, el("strong", {}, row.ticker),
          el("span", { class: "muted" }, ` · ${row.name} · ${fmtEur(row.value)}`)));
      const reasons = el("ul", { style: "margin:8px 0 0 18px;font-size:13.5px" });
      for (const r of row.reasons) {
        reasons.append(el("li", { style: "margin-bottom:4px" },
          el("strong", {}, r.reason + ". "), r.detail));
      }
      item.append(reasons);
      sellBody.append(item);
    }
    sellBody.append(el("p", { class: "note muted", style: "padding-top:12px" },
      `These are mechanical consequences of the numbers, not reasons to sell. A position can cost more risk than its weight because it is the one thing actually working. You get ${bookAdvice.budgets.monthlyFreeSells} free sells a month.`));
  }
  sellPanel.append(sellBody);
  box.append(sellPanel);

  const buys = DATA.buyCandidates;
  const buyPanel = el("div", { class: "panel wide", style: "margin-bottom:20px" },
    el("h3", {}, `What would change the shape, bought at ${DATA.additions.allocation * 100}%`));
  const buyBody = el("div", { class: "body" });
  if (!buys.length) {
    buyBody.append(el("p", { class: "muted" }, "No fund in the universe would materially change this book."));
  } else {
    buyBody.append(table(["Fund", "Asset class", "Δ vol", "Δ beta", "Corr.", "Why"],
      buys.map(b => ({
        cells: [b.name, { text: b.asset, cls: "muted" },
          { text: signed(b.volChange) + "pp", cls: b.volChange < 0 ? "pos" : "neg" },
          signed(b.betaChange), fmtNum(b.correlation),
          { text: b.reasons.join("; "), cls: "muted" }],
      })), { numFrom: 2 }));
    buyBody.append(el("p", { class: "note muted", style: "padding-top:12px" },
      `Cash budget is ${fmtEur(bookAdvice.budgets.monthlyBuyCashEur)} a month, and the single-name cap is ${bookAdvice.budgets.maxNameWeightPct}%.`));
  }
  buyPanel.append(buyBody);
  box.append(buyPanel);

  box.append(sizer(value));
}

/* The agent's size_position formula, run in the page. You supply the
   conviction — the part a model was doing — and this does the arithmetic. */
function sizer(value) {
  const panel = el("div", { class: "panel wide" }, el("h3", {}, "Size a position"));
  const body = el("div", { class: "body" });
  const budgets = view().advice.budgets;

  const side = el("select", {}, el("option", { value: "buy" }, "Buy"), el("option", { value: "sell" }, "Sell"));
  const tickers = [...new Set([...holdings.map(h => h.ticker), ...DATA.funds.map(f => f.ticker)])].sort();
  const ticker = el("select", {}, tickers.map(t => el("option", { value: t }, t)));
  const conviction = el("input", { type: "range", min: "1", max: "10", step: "1", value: "6" });
  const convictionLabel = el("strong", {}, "6");
  const speculative = el("input", { type: "checkbox" });
  const price = el("input", { type: "number", step: "any", placeholder: "price", style: "width:110px" });
  const out = el("div", { style: "margin-top:14px" });

  function recompute() {
    convictionLabel.textContent = conviction.value;
    const held = holdings.find(h => h.ticker === ticker.value);
    const weight = held ? held.value / value : 0;
    const px = parseFloat(price.value) || 0;
    const body_ = {
      side: side.value, conviction: parseFloat(conviction.value), price: px,
      currentWeight: weight, positionValue: held ? held.value : 0,
      positionShares: null, speculative: speculative.checked,
    };
    const r = sizeLocally(body_, budgets);
    out.innerHTML = "";
    const rows = side.value === "buy"
      ? [["Suggested", fmtEur(r.euros)], ["Shares at that price", r.shares ?? "–"],
         ["Current weight", fmtPct(r.currentWeightPct)],
         ["Headroom to the cap", fmtNum(r.headroomFactor)],
         ["Monthly cash budget", fmtEur(r.cashBudget)]]
      : [["Suggested", fmtEur(r.euros)], ["Fraction of the position", fmtPct(r.trimFraction * 100)],
         ["Position value", fmtEur(r.positionValue)],
         ["Free sells a month", String(r.freeSellsPerMonth)]];
    out.append(table(["", ""], rows.map(c => ({ cells: c })), { numFrom: 1 }));
    out.append(el("p", { class: "note muted", style: "padding-top:10px" },
      (r.flag ? r.flag + " " : "") + (r.note || "")));
  }

  [side, ticker, price, speculative].forEach(c => c.addEventListener("change", recompute));
  conviction.addEventListener("input", recompute);

  body.append(el("div", { class: "controls" },
    side, ticker,
    el("span", { class: "muted" }, "conviction"), conviction, convictionLabel,
    el("span", { class: "muted" }, "price"), price,
    el("label", { style: "display:flex;gap:6px;align-items:center;text-transform:none;letter-spacing:0" },
      speculative, "speculative")), out);
  panel.append(body);
  setTimeout(recompute, 0);
  return panel;
}

/* Same arithmetic as advice.size_position, kept in the page so the slider
   responds without a round trip. The server exposes /api/size with the
   identical formula for anything that needs it server-side. */
function sizeLocally(body, budgets) {
  const cap = budgets.maxNameWeightPct / 100;
  const fraction = Math.max(0, Math.min(1, body.conviction / 10));
  if (body.side === "buy") {
    const headroom = Math.max(0, Math.min(1, (cap - body.currentWeight) / cap));
    let euros = budgets.monthlyBuyCashEur * fraction * headroom;
    if (body.speculative) euros *= 0.5;
    euros = Math.round(euros / 25) * 25;
    return {
      euros, shares: body.price ? +(euros / body.price).toFixed(2) : null,
      currentWeightPct: body.currentWeight * 100, headroomFactor: headroom,
      cashBudget: budgets.monthlyBuyCashEur,
      note: (body.speculative ? "Sized at half for a speculative name. " : "") +
            (body.currentWeight ? "Adds to an existing position." : "New position."),
      flag: body.currentWeight && headroom < 0.15
        ? `Already ${(body.currentWeight * 100).toFixed(1)}% of the book against a ${budgets.maxNameWeightPct}% cap — almost no room to add.`
        : null,
    };
  }
  const trim = body.conviction >= 10 ? 1 : fraction;
  return {
    euros: body.positionValue * trim, trimFraction: trim,
    positionValue: body.positionValue, freeSellsPerMonth: budgets.monthlyFreeSells,
    note: `Trims about ${(trim * 100).toFixed(0)}% of the position.`,
  };
}

/* ---------------- pension ---------------- */

/* Edited as a whole rather than as a stream of trades, because a pension
   statement arrives as a whole picture: units and values as at a date, not
   a running record of what you bought. Contributions are the append-only
   half, since those genuinely are events. */

async function renderPension() {
  const box = document.getElementById("pensionBox");
  box.innerHTML = "";
  const health = await checkApi();

  let data = DATA.pension;
  if (API) {
    try { data = await apiGet("/api/pension"); } catch (e) { /* keep the built copy */ }
  } else {
    box.append(el("div", { class: "warnbox" },
      "The API is not answering, so this is the pot as it stood when the site was last built and nothing can be edited from here."));
  }

  const cards = el("div", { class: "kpis" });
  for (const [k, v, s] of [
    ["Pot value", fmtEur(data.total), data.updated ? `as entered ${data.updated.slice(0, 10)}` : "not set yet"],
    ["Contributions logged", fmtEur(data.paidIn),
     Object.entries(data.bySource || {}).map(([k2, v2]) => `${k2} ${fmtEur(v2)}`).join(" · ") || "none yet"],
    ["Growth on what is logged", data.growth === null ? "–" : fmtEur(data.growth),
     "only meaningful once the history is complete"],
    ["Lines", `${data.holdings.length}`,
     `${data.pricedCount} with a market ticker, ${data.unpricedCount} carried at stated value`],
  ]) {
    cards.append(el("div", { class: "kpi" }, el("div", { class: "k" }, k),
      el("div", { class: "v" }, v), el("div", { class: "s" }, s)));
  }
  box.append(cards);

  // --- holdings editor ---
  const rows = data.holdings.map(h => ({ ...h }));
  const panel = el("div", { class: "panel wide", style: "margin:20px 0" },
    el("h3", {}, "What the pot holds"));
  const body = el("div", { class: "body" });
  const tableBox = el("div", { class: "tablewrap" });

  function drawRows() {
    tableBox.innerHTML = "";
    const total = rows.reduce((a, r) => a + (parseFloat(r.value_eur) || 0), 0);
    tableBox.append(table(
      ["Fund", "Provider", "Ticker (optional)", "Units", "Value (€)", "Share", ""],
      rows.map((r, i) => ({
        cells: [
          { node: el("input", { value: r.name || "", style: "width:210px",
              oninput: e => { rows[i].name = e.target.value; } }) },
          { node: el("input", { value: r.provider || "", style: "width:120px",
              oninput: e => { rows[i].provider = e.target.value; } }) },
          { node: el("input", { value: r.ticker || "", placeholder: "VWCE.DE", style: "width:100px",
              oninput: e => { rows[i].ticker = e.target.value.toUpperCase(); } }) },
          { node: el("input", { type: "number", step: "any", value: r.units || 0, class: "val",
              style: "width:90px", oninput: e => { rows[i].units = parseFloat(e.target.value) || 0; } }) },
          { node: el("input", { type: "number", step: "any", value: r.value_eur || 0, class: "val",
              oninput: e => { rows[i].value_eur = parseFloat(e.target.value) || 0; drawRows(); } }) },
          { text: total ? fmtPct(100 * (parseFloat(r.value_eur) || 0) / total) : "–" },
          { node: el("button", { class: "link", onclick: () => { rows.splice(i, 1); drawRows(); } }, "remove") },
        ],
      })), { numFrom: 3 }));
  }
  drawRows();

  const status = el("div", { class: "muted", style: "font-size:13px;margin-top:10px" });
  const save = el("button", { class: "btn", onclick: async () => {
    save.disabled = true; status.textContent = "saving…";
    try {
      const response = await fetch("/api/pension/holdings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ holdings: rows }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || response.status);
      status.textContent = `Saved — pot now ${fmtEur(result.total)}. Rebuild to fold it into the charts.`;
      renderPension();
    } catch (exc) { status.textContent = "Refused: " + exc.message; }
    finally { save.disabled = false; }
  } }, "save holdings");

  body.append(tableBox, el("div", { class: "controls", style: "margin-top:14px" },
    el("button", { class: "btn ghost", onclick: () => {
      rows.push({ name: "", provider: "", ticker: "", units: 0, value_eur: 0 }); drawRows();
    } }, "add a line"),
    API ? save : el("span", { class: "muted" }, "read-only without the API")),
    status,
    el("p", { class: "note muted", style: "padding:10px 0 0" },
      "A ticker is optional. Give one and the line is priced and risk-analysed like any other holding; leave it blank and the line is carried at the value you type, counted in the total and excluded from volatility and beta."));
  panel.append(body);
  box.append(panel);

  // --- contributions ---
  const contribPanel = el("div", { class: "panel wide" }, el("h3", {}, "Contributions"));
  const contribBody = el("div", { class: "body" });
  const when = el("input", { type: "date", value: new Date().toISOString().slice(0, 10) });
  const amount = el("input", { type: "number", step: "any", min: "0", placeholder: "450", class: "val" });
  const source = el("select", {}, ["employee", "employer", "avc", "transfer"].map(
    s => el("option", { value: s }, s)));
  const cnote = el("input", { placeholder: "note", style: "width:150px" });
  const cstatus = el("div", { class: "muted", style: "font-size:13px;margin-top:10px" });

  const addContribution = el("button", { class: "btn", onclick: async () => {
    try {
      const response = await fetch("/api/pension/contribution", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date: when.value, amount_eur: parseFloat(amount.value),
                               source: source.value, note: cnote.value.trim() }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || response.status);
      amount.value = ""; cnote.value = "";
      renderPension();
    } catch (exc) { cstatus.textContent = "Refused: " + exc.message; }
  } }, "add");

  if (API) {
    contribBody.append(el("div", { class: "controls" },
      source, el("span", { class: "muted" }, "€"), amount,
      el("span", { class: "muted" }, "on"), when, cnote, addContribution), cstatus);
  }
  if ((data.contributions || []).length) {
    contribBody.append(table(["Date", "Source", "Amount", "Note"],
      [...data.contributions].reverse().map(c => ({
        cells: [c.date, c.source, fmtEur(c.amount_eur), { text: c.note || "", cls: "muted" }],
      })), { numFrom: 2 }));
  } else {
    contribBody.append(el("p", { class: "muted", style: "margin-top:12px" },
      "Nothing logged. Until the history is complete, treat the growth figure above as missing data rather than performance."));
  }
  contribPanel.append(contribBody);
  box.append(contribPanel);
}
