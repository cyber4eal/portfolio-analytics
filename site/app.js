/* Bond Portfolio Centre — all arithmetic runs here.

   The build ships a return matrix rather than finished numbers, because a
   finished number goes stale the moment a holding is edited. Everything on
   screen is derived from weights at render time. */

const TRADING_DAYS = 252, LAMBDA = 0.94, Z95 = 1.6448536269514722;
/* Read from the stylesheet rather than hardcoded, so one theme switch
   repaints the charts too instead of leaving grey axes on a dark page. */
const cssVar = name =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const PALETTE = () => Array.from({ length: 10 }, (_, i) => cssVar(`--c${i + 1}`));
const INK = () => cssVar("--ink");
const SLATE = () => cssVar("--slate");
const LINE = () => cssVar("--line");
const THEME_KEY = "bond.theme";

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
  Chart.defaults.color = SLATE();
  Chart.defaults.font.size = 11.5;
  charts[id] = new Chart(el_, config);
}

const gridScale = (extra = {}) => Object.assign({
  grid: { color: LINE(), drawTicks: false }, border: { display: false },
}, extra);

/* ---------------- freshness ---------------- */

/* Prices, the theories, the rebalance plan and the trend signals are all
   frozen into the payload at build time. Only the holdings editor and the
   simulations recompute in the page. So the age of the build is the age of
   the advice, and saying so plainly is the difference between a tool and a
   trap - a plan computed against week-old prices looks exactly like one
   computed against this morning's. */

function ageInDays(iso) {
  if (!iso) return null;
  return (Date.now() - new Date(iso).getTime()) / 86400000;
}

function renderFreshness() {
  const f = DATA.freshness || {};
  const built = f.builtAt || DATA.generated;
  const age = ageInDays(built);
  const stale = age !== null && age > 1.5;
  const label = age === null ? ""
    : age < 1 / 24 ? "just now"
    : age < 1 ? `${Math.round(age * 24)}h ago`
    : `${Math.round(age)} days ago`;

  document.getElementById("asof").innerHTML =
    `Prices as at <strong>${DATA.asOf}</strong><br>` +
    `built <span class="${stale ? "stale" : "fresh"}">${label}</span>` +
    (stale ? " — advice may be out of date" : "");
}

/* ---------------- theme ---------------- */

function currentTheme() {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved) return saved;
  } catch (e) { /* storage unavailable */ }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const button = document.getElementById("themeToggle");
  if (button) {
    button.textContent = theme === "dark" ? "☀" : "☾";
    button.title = theme === "dark" ? "Switch to light" : "Switch to dark";
  }
  try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
}

function toggleTheme() {
  const next = document.documentElement.getAttribute("data-theme") === "dark"
    ? "light" : "dark";
  applyTheme(next);
  // Charts bake their colours in at construction, so they have to be rebuilt.
  if (DATA) {
    renderDerived();
    if (document.getElementById("view-map").classList.contains("on")) renderMap();
    if (document.getElementById("view-cube").classList.contains("on")) renderCube();
  }
}

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
    label: "My book", data: levelCurve(mine.series), borderColor: INK(),
    borderWidth: 2.4, pointRadius: 0, tension: .18, order: 0,
  }];
  // The benchmark first, always: it is the line the book is measured
  // against everywhere else, and leaving it off the one chart people
  // actually look at made the comparison invisible.
  const benchStats = statsFor([DATA.benchmarkTicker], [1], 1, window_);
  if (benchStats) {
    datasets.push({
      label: `${DATA.benchmarkTicker} (benchmark)`, data: levelCurve(benchStats.series),
      borderColor: "#B9002F", borderWidth: 1.8, borderDash: [6, 3],
      pointRadius: 0, tension: .18, order: 1,
    });
  }
  const picks = ["iwda", "cspx", "aggh", "sgln"];
  picks.forEach((id, i) => {
    const fund = DATA.funds.find(f => f.id === id);
    if (!fund || fund.ticker === DATA.benchmarkTicker) return;
    const s = statsFor([fund.ticker], [1], 1, window_);
    if (!s) return;
    datasets.push({
      label: fund.name.replace(/ UCITS ETF.*| ETC.*/, ""), data: levelCurve(s.series),
      borderColor: PALETTE()[i * 2], borderWidth: 1.5, pointRadius: 0, tension: .18,
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
    `€100 invested on ${mine.from}, rebased. All lines share the book's window ` +
    `(${(mine.days / TRADING_DAYS).toFixed(1)} years) — the shortest holding decides how far back the ` +
    `comparison can honestly go. Every fund line is already net of its own ongoing charge: the fee comes ` +
    `out of NAV daily, so these are what a holder actually received, not what the fund earned before paying itself.`;
}

function donut(id, entries, note) {
  const labels = entries.map(e => e[0]), values = entries.map(e => e[1]);
  chart(id, {
    type: "doughnut",
    data: { labels, datasets: [{ data: values, backgroundColor: PALETTE(),
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
        datalabels: { anchor: "end", align: "end", color: SLATE(), font: { size: 10 },
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
          backgroundColor: INK(), pointRadius: 9, pointStyle: "rectRot" },
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
        x: gridScale({ title: { display: true, text: "Volatility (annualised)", color: SLATE() },
                       ticks: { callback: v => v + "%" } }),
        y: gridScale({ title: { display: true, text: "Return p.a.", color: SLATE() },
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
        x: gridScale({ title: { display: true, text: "Volatility", color: SLATE() },
                       ticks: { callback: v => v + "%" } }),
        y: gridScale({ title: { display: true, text: "Return p.a.", color: SLATE() },
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

/* The forward assumption. Cash plus beta times an equity risk premium -
   the return you can defend asking for, given the risk taken. Projecting
   what the sample happened to deliver is what makes these charts lie: this
   book's own sixteen months annualise near 50%, and compounding that for a
   decade produces a number no asset class has sustained. */
const RISK_FREE = 0.025, EQUITY_RISK_PREMIUM = 0.045;
const capmReturn = beta => RISK_FREE + Math.max(0, beta) * EQUITY_RISK_PREMIUM;

function runSimulation(mine, value) {
  const years = parseInt(document.getElementById("mcYears").value, 10);
  const monthly = parseFloat(document.getElementById("mcMonthly").value) || 0;
  const startInput = parseFloat(document.getElementById("mcStart").value);
  const start = isFinite(startInput) && startInput > 0 ? startInput : value;
  const basis = document.getElementById("mcBasis").value;
  const months = years * 12, paths = 6000;

  const daily = mine.series;
  const sampleDrift = daily.reduce((a, b) => a + b, 0) / daily.length * TRADING_DAYS;
  const capm = capmReturn(mine.beta);

  let target = capm;
  if (basis === "sample") target = sampleDrift;
  if (basis === "custom") {
    const typed = parseFloat(document.getElementById("mcDrift").value);
    target = isFinite(typed) ? typed / 100 : capm;
  }
  const track = bootstrapPaths(daily, start, months, monthly, paths, target - sampleDrift);

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
    const paidIn = start + monthly * (m + 1);
    let losses = 0;
    for (let p = 0; p < paths; p++) if (col[p] < paidIn) losses++;
    boxes.push({ label: `${y} yr`, p05: percentile(col, .05), p25: percentile(col, .25),
                 median: percentile(col, .5), p75: percentile(col, .75),
                 p95: percentile(col, .95), paidIn, probLoss: losses / paths });
  }

  renderFan(fan, start, monthly, months);
  renderBoxes(boxes);
  renderMcSummary(boxes, start, monthly, months, target, sampleDrift, capm,
                  basis, mine, daily.length);
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
        { label: "Median", data: fan.map(f => f.median), borderColor: INK(),
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
          backgroundColor: INK(), barPercentage: .78, order: 1 },
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

function renderMcSummary(boxes, start, monthly, months, target, sampleDrift,
                        capm, basis, mine, sampleDays) {
  const last = boxes[boxes.length - 1];
  const box = document.getElementById("mcSummary");
  box.innerHTML = "";
  if (!last) return;

  // Compounded, not arithmetic: the median grows at mu - sigma^2/2, and on a
  // book this volatile that drag is most of the expected return.
  const vol = mine.vol / 100;
  const drag = vol * vol / 2;
  box.append(table(["At " + last.label, ""], [
    { cells: ["Paid in", fmtEur(last.paidIn)] },
    { cells: ["Median outcome", fmtEur(last.median)] },
    { cells: ["Bad case (5th pct)", fmtEur(last.p05)] },
    { cells: ["Good case (95th pct)", fmtEur(last.p95)] },
    { cells: ["Chance of ending below what you paid in", fmtPct(last.probLoss * 100)] },
    { cells: ["Assumed return p.a.", fmtPct(target * 100)] },
    { cells: ["Less volatility drag", "−" + fmtPct(drag * 100)] },
    { cells: ["Median compounds at", fmtPct((target - drag) * 100)] },
  ], { numFrom: 1 }));

  const note = document.getElementById("mcBasisNote");
  if (note) {
    note.textContent = basis === "capm"
      ? `${(RISK_FREE * 100).toFixed(1)}% cash + beta ${fmtNum(mine.beta)} × ${(EQUITY_RISK_PREMIUM * 100).toFixed(1)}% = ${(capm * 100).toFixed(1)}%`
      : basis === "sample"
        ? `the sample's ${(sampleDrift * 100).toFixed(0)}% — an extrapolation, not a forecast`
        : "";
  }

  const lines = [];
  lines.push(basis === "sample"
    ? `<strong>You are projecting this book's own ${(sampleDrift * 100).toFixed(0)}% a year forward.</strong> Nothing about ${(sampleDays / TRADING_DAYS).toFixed(1)} bullish years entitles the next ${months / 12} to repeat them; no asset class has sustained that over a decade.`
    : `Drift is an assumption of ${(target * 100).toFixed(1)}% a year, not a measurement. The book's own ${(sampleDays / TRADING_DAYS).toFixed(1)} years annualise to ${(sampleDrift * 100).toFixed(0)}%, which is a bull market being mistaken for an expected return.`);
  lines.push(`At ${fmtPct(mine.vol)} volatility the drag is ${fmtPct(drag * 100)} a year, so the median compounds at ${fmtPct((target - drag) * 100)} rather than ${fmtPct(target * 100)}. On a book this volatile that gap is most of the return.`);
  lines.push(`The spread is resampled from this book's own returns, and those ${(sampleDays / TRADING_DAYS).toFixed(1)} years contain no 2008 and no 2020 — so the bad case shown is optimistic. There is no true crash in the sample to resample from.`);
  document.getElementById("mcWarning").innerHTML = lines.join("<br><br>");
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
  ["simulate", "Simulate"], ["stress", "Stress"], ["cube", "Risk surfaces"],
  ["explore", "Explore"], ["goal", "Mortgage"], ["pension", "Pension"],
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
  if (id === "goal") renderGoal();
  if (id === "cube") renderCube();
  if (id === "explore") renderExplore();
  if (id === "simulate") {
    const { tickers, weights, value } = currentWeights();
    const start = document.getElementById("mcStart");
    // Default the starting amount to this book, so opening the tab simulates
    // the portfolio you are looking at rather than a round number.
    if (!start.value) start.value = Math.round(value);
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
  renderOrders();
  renderDeadlines();
  renderLevers();
  renderMilestones();
  renderTheories();
  renderPlan();
  renderHedges();
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
  const start = document.getElementById("mcStart");
  if (start) start.value = Math.round(currentWeights().value);
  renderAll();
  // Every view that depends on which book you are looking at has to be told.
  // The map and transactions were wired and the goal and pension were not,
  // so those two silently kept showing the previous book's numbers.
  const on = id => document.getElementById("view-" + id).classList.contains("on");
  if (on("map")) renderMap();
  if (on("transactions")) renderTransactions();
  if (on("goal")) renderGoal();
  if (on("pension")) renderPension();
  if (on("cube")) renderCube();
}

function renderCaveats() {
  const list = el("ul");
  for (const c of DATA.caveats) list.append(el("li", {}, c));
  document.getElementById("caveats").replaceChildren(list);
}

async function boot() {
  DATA = await (await fetch("data.json")).json();
  renderFreshness();
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
  document.getElementById("mcStart").addEventListener("change", rerun);
  document.getElementById("mcStartReset").addEventListener("click", () => {
    document.getElementById("mcStart").value = Math.round(currentWeights().value);
    rerun();
  });
  document.getElementById("mcBasis").addEventListener("change", e => {
    document.getElementById("mcDrift").style.display =
      e.target.value === "custom" ? "" : "none";
    if (e.target.value === "custom" && !document.getElementById("mcDrift").value) {
      const { tickers, weights, value } = currentWeights();
      const mine = statsFor(tickers, weights, value);
      document.getElementById("mcDrift").value = (capmReturn(mine.beta) * 100).toFixed(1);
    }
    rerun();
  });
  document.getElementById("mcDrift").style.display = "none";

  applyTheme(currentTheme());
  document.getElementById("themeToggle").addEventListener("click", toggleTheme);
  // A rebuild is what makes the theories, the plan and the trend signals
  // move; the button only appears when there is an API able to run one.
  const refresh = document.getElementById("refreshNow");
  checkApi().then(() => {
    if (!API) return;
    refresh.style.display = "";
    refresh.addEventListener("click", async () => {
      refresh.disabled = true;
      refresh.textContent = "rebuilding…";
      try {
        await fetch("/api/rebuild", { method: "POST",
          headers: { "Content-Type": "application/json" }, body: "{}" });
        for (let i = 0; i < 40; i++) {
          await new Promise(r => setTimeout(r, 3000));
          const state = await apiGet("/api/rebuild");
          if (!state.running) {
            if (state.status && state.status.startsWith("failed")) {
              refresh.textContent = "rebuild failed";
              return;
            }
            location.reload();
            return;
          }
        }
        refresh.textContent = "still going…";
      } catch (e) {
        refresh.textContent = "rebuild failed";
      } finally {
        refresh.disabled = false;
      }
    });
  });

  document.getElementById("exploreGo").addEventListener("click", () => renderExplore());
  document.getElementById("exploreTicker").addEventListener("keydown", e => {
    if (e.key === "Enter") renderExplore();
  });
  document.getElementById("exploreWeight").addEventListener("input", e => {
    document.getElementById("exploreWeightLabel").textContent = e.target.value + "%";
  });
  document.getElementById("exploreWeight").addEventListener("change", () => {
    if (document.getElementById("exploreTicker").value.trim()) renderExplore();
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

  const pots = DATA.pension || {};
  const people = Object.keys(pots);
  // One switcher, at the top. A second one here asked the reader to hold two
  // ideas of "whose" at once, and they could disagree.
  PENSION_OWNER = pots[BOOK] ? BOOK : (BOOK === "Combined" ? "Combined" : people[0]);

  const picker = document.getElementById("pensionOwner");
  picker.innerHTML = "";
  if (BOOK === "Combined" && people.length > 1) {
    picker.append(el("span", { class: "muted" },
      `Both pots together — ${people.join(" and ")}. Switch book at the top to see one.`));
  } else if (!pots[BOOK]) {
    picker.append(el("span", { class: "muted" },
      `No pension statement imported for ${BOOK}; showing ${PENSION_OWNER}.`));
  }

  let data = BOOK === "Combined" && people.length > 1
    ? combinePots(pots) : (pots[PENSION_OWNER] || {});
  if (API) {
    if (BOOK !== "Combined") {
      try { data = await apiGet(`/api/pension?owner=${encodeURIComponent(PENSION_OWNER)}`); }
      catch (e) { /* keep the built copy */ }
    }
  } else {
    box.append(el("div", { class: "warnbox" },
      "The API is not answering, so this is the pot as it stood when the site was last built and nothing can be edited from here."));
  }

  if (data.accrualNote) box.append(el("div", { class: "warnbox" }, data.accrualNote));
  // A WTW statement exports one fund at a time, so the log can explain
  // almost none of the pot - and the contribution rate is read off that log.
  if (data.contributionCoverage !== undefined && data.contributionCoverage < 50
      && data.total > 0) {
    const box_ = el("div", { class: "warnbox" });
    box_.innerHTML =
      `<strong>The contribution history here is partial.</strong> ${fmtEur(data.paidIn)} is logged against ` +
      `a ${fmtEur(data.total)} pot — ${data.contributionCoverage}% — because a WTW statement exports one ` +
      `fund at a time and only ${data.monthsObserved} month${data.monthsObserved === 1 ? " has" : "s have"} ` +
      `been imported. The projection assumes ${fmtEur(data.monthlyRate)} goes in every month, which is what ` +
      `the log shows and is very likely too low. Export the other fund views, or just type the real monthly ` +
      `figure in the box below — that single number moves the thirty-five year outcome more than anything ` +
      `else on this page.`;
    box.append(box_);
  }
  const cards = el("div", { class: "kpis" });
  for (const [k, v, s] of [
    ["Pot value", fmtEur(data.estimatedTotal ?? data.total),
     data.accruedMonths ? `${fmtEur(data.total)} confirmed + ${fmtEur(data.accrued)} accrued`
                        : (data.updated ? `as at ${data.updated.slice(0, 10)}` : "not set yet")],
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
  const byMonth = contributionsByMonth(data.contributions || []);
  if (byMonth.length) {
    contribBody.append(table(
      ["Month", "Total in", "Split", "You", "Employer"],
      byMonth.map(m => ({
        cells: [
          m.month,
          { node: el("strong", {}, fmtEur(m.total)) },
          { node: splitPie(m.employee, m.employer) },
          { text: m.employee ? fmtEur(m.employee) : "–",
            cls: m.employee ? "" : "muted" },
          { text: m.employer ? fmtEur(m.employer) : "–",
            cls: m.employer ? "" : "muted" },
        ],
      })), { numFrom: 1 }));

    const totals = byMonth.reduce((a, m) => ({
      employee: a.employee + m.employee, employer: a.employer + m.employer,
    }), { employee: 0, employer: 0 });
    const grand = totals.employee + totals.employer;
    contribBody.append(el("p", { class: "note muted", style: "padding-top:12px" },
      `${byMonth.length} month${byMonth.length === 1 ? "" : "s"} on record, ${fmtEur(grand)} in total — ` +
      `${fmtEur(totals.employer)} of it your employer's, which is ` +
      `${grand ? (100 * totals.employer / grand).toFixed(0) : 0}% of everything going in. ` +
      `That share is the part of the pension worth protecting: it is pay you only receive by contributing.`));
    const legend = el("div", { class: "pielegend" },
      el("span", {}, el("i", { style: `background:${CONTRIB_COLOURS.employee}` }), "you"),
      el("span", {}, el("i", { style: `background:${CONTRIB_COLOURS.employer}` }), "employer"));
    contribBody.append(legend);
  } else {
    contribBody.append(el("p", { class: "muted", style: "margin-top:12px" },
      "Nothing logged. Until the history is complete, treat the growth figure above as missing data rather than performance."));
  }
  contribPanel.append(contribBody);
  box.append(contribPanel);

  const monthly = document.getElementById("penMonthly");
  const years = document.getElementById("penYears");
  if (!monthly.value) monthly.value = Math.round(data.monthlyRate || 0);
  if (!monthly.dataset.wired) {
    monthly.dataset.wired = "1";
    const redraw = () => renderPensionCharts(data);
    monthly.addEventListener("change", redraw);
    years.addEventListener("input", () => {
      document.getElementById("penYearsLabel").textContent = years.value;
    });
    years.addEventListener("change", redraw);
    document.getElementById("penRateSave").addEventListener("click", async () => {
      await fetch("/api/pension/rate", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ monthly: parseFloat(monthly.value) || 0 }) });
      renderPension();
    });
    document.getElementById("penRateAuto").addEventListener("click", async () => {
      await fetch("/api/pension/rate", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ monthly: "auto" }) });
      monthly.value = "";
      renderPension();
    });
  }
  const charge = document.getElementById("penCharge");
  if (!charge.dataset.wired) {
    charge.dataset.wired = "1";
    document.getElementById("penChargeSave").addEventListener("click", async () => {
      await fetch("/api/pension/charge", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ charge: parseFloat(charge.value), owner: PENSION_OWNER }) });
      renderPension();
    });
  }
  charge.value = ((data.charge ?? 0.015) * 100).toFixed(2);

  document.getElementById("penYearsLabel").textContent = years.value;
  renderPensionCharts(data);
}

/* ---------------- risk surfaces ---------------- */

/* Two surfaces, both answering something a flat chart cannot.

   The blend surface sweeps every mix of two funds and plots what it does to
   the book, so the shape of the trade-off is visible rather than inferred
   from a table of eighteen rows.

   The correlation surface is the one worth the rotation: rolling correlation
   of each holding to the book, through time. Diversification is not a number,
   it is a number that moves - and it moves most at exactly the moment you
   were relying on it. A ridge running across every holding at once is the
   month your diversification stopped working. */

let PENSION_OWNER = null;
let cubeScene = null;
let cubeMode = "blend";

function normalise(values) {
  const finite = values.filter(v => isFinite(v));
  const lo = Math.min(...finite), hi = Math.max(...finite);
  const span = hi - lo || 1;
  return { lo, hi, at: v => isFinite(v) ? (v - lo) / span : 0.5 };
}

const SURFACE_METRICS = {
  vol: { label: "Volatility", get: s => s.vol, unit: "%", lowerIsBetter: true },
  sharpe: { label: "Sharpe", get: s => s.sharpe, unit: "", lowerIsBetter: false },
  cagr: { label: "Return p.a.", get: s => s.cagr, unit: "%", lowerIsBetter: false },
  drawdown: { label: "Worst drawdown", get: s => s.maxDrawdown, unit: "%", lowerIsBetter: false },
};

function cubeControls() {
  const box = document.getElementById("cubeControls");
  box.innerHTML = "";

  const modeSelect = el("select", {},
    el("option", { value: "blend" }, "Blend surface — what two funds would do"),
    el("option", { value: "correlation" }, "Correlation through time — when diversification failed"));
  modeSelect.value = cubeMode;
  modeSelect.addEventListener("change", () => { cubeMode = modeSelect.value; renderCube(); });
  box.append(el("label", {}, "Show"), modeSelect);

  if (cubeMode === "blend") {
    const options = DATA.funds.filter(f => columnFor(f.ticker));
    const one = el("select", { id: "cubeFundA" }, options.map(f => el("option", { value: f.ticker }, f.name)));
    const two = el("select", { id: "cubeFundB" }, options.map(f => el("option", { value: f.ticker }, f.name)));
    one.value = options.find(f => f.id === "aggh")?.ticker || options[0].ticker;
    two.value = options.find(f => f.id === "sgln")?.ticker || options[1].ticker;
    const metric = el("select", { id: "cubeSurfaceMetric" },
      Object.entries(SURFACE_METRICS).map(([k, m]) => el("option", { value: k }, m.label)));
    [one, two, metric].forEach(c => c.addEventListener("change", drawBlendSurface));
    box.append(el("label", {}, "Fund A"), one, el("label", {}, "Fund B"), two,
               el("label", {}, "Height"), metric);
  } else {
    const window_ = el("select", { id: "corrWindow" },
      el("option", { value: "42" }, "2 months"),
      el("option", { value: "63" }, "3 months"),
      el("option", { value: "126" }, "6 months"));
    window_.value = "63";
    window_.addEventListener("change", drawCorrelationSurface);
    box.append(el("label", {}, "Rolling window"), window_,
               el("span", { class: "muted" }, "height and colour are both correlation to the rest of the book"));
  }
}

function ensureScene() {
  if (!cubeScene) {
    cubeScene = new CUBE.Scene(document.getElementById("cubeCanvas"));
    const tip = document.getElementById("cubeTip");
    cubeScene.onHover = (point, projected) => {
      if (!point) { tip.style.opacity = 0; return; }
      tip.innerHTML = point.tip;
      const box = cubeScene.canvas.getBoundingClientRect();
      const dpr = cubeScene.canvas.width / box.width;
      tip.style.left = (projected.sx / dpr + 14) + "px";
      tip.style.top = (projected.sy / dpr + 10) + "px";
      tip.style.opacity = 1;
    };
    window.addEventListener("resize", () => cubeScene && cubeScene.resize());
  }
  return cubeScene;
}

function drawBlendSurface() {
  const { tickers, weights, value } = currentWeights();
  const mine = statsFor(tickers, weights, value);
  if (!mine) return;

  const tickerA = document.getElementById("cubeFundA").value;
  const tickerB = document.getElementById("cubeFundB").value;
  const key = document.getElementById("cubeSurfaceMetric").value;
  const metric = SURFACE_METRICS[key];
  const steps = 11;
  const window_ = new Set(mine.index);

  const cells = [];
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < steps; i++) {
    const row = [];
    for (let j = 0; j < steps; j++) {
      const a = i / (steps - 1) * 0.5;
      const b = j / (steps - 1) * 0.5;
      const rest = 1 - a - b;
      const mix = statsFor([...tickers, tickerA, tickerB],
        [...weights.map(w => w * rest), a, b], value, window_);
      const height = mix ? metric.get(mix) : NaN;
      if (isFinite(height)) { lo = Math.min(lo, height); hi = Math.max(hi, height); }
      row.push({ a, b, height });
    }
    cells.push(row);
  }

  const span = hi - lo || 1;
  const grid = cells.map((row, i) => row.map((cell, j) => ({
    x: i / (steps - 1),
    y: isFinite(cell.height) ? (cell.height - lo) / span : 0,
    z: j / (steps - 1),
  })));

  const scene = ensureScene();
  scene.points = [];
  // Red must mean "worse". On volatility that is the high end; on Sharpe
  // and return it is the low end, so the ramp follows the metric rather
  // than the raw height.
  scene.surface = { grid, rows: steps, cols: steps, invert: metric.lowerIsBetter };
  scene.axes = { x: tickerA + " %", y: metric.label, z: tickerB + " %" };
  scene.ranges = { x: [0, 50], y: [lo, hi], z: [0, 50] };
  scene.tickLabels = null;      // the other surface sets date ticks; clear them

  let best = null;
  for (const row of cells) for (const cell of row) {
    if (!isFinite(cell.height)) continue;
    const better = metric.lowerIsBetter ? (!best || cell.height < best.height)
                                        : (!best || cell.height > best.height);
    if (better) best = cell;
  }
  const place = (cell, colour, label, emphasis) => ({
    x: cell.a / 0.5, y: (cell.height - lo) / span, z: cell.b / 0.5,
    r: 0.55, colour, emphasis, label,
    tip: `<strong>${label}</strong><br>${tickerA} ${(cell.a * 100).toFixed(0)}% · ${tickerB} ${(cell.b * 100).toFixed(0)}%` +
         `<br>${metric.label} ${fmtNum(cell.height)}${metric.unit}`,
  });
  const today = cells[0][0];
  scene.points.push(place(today, "#000", "As it stands", true));
  if (best && (best.a || best.b)) scene.points.push(place(best, "#B9002F", "Best mix", true));
  scene.resize();

  cubeLegend(metric.label, metric.lowerIsBetter ? hi : lo, metric.lowerIsBetter ? lo : hi);
  document.getElementById("cubeTitle").textContent =
    `Blending your book with ${tickerA} and ${tickerB}`;

  const delta = best ? best.height - today.height : 0;
  document.getElementById("cubeNote").innerHTML =
    `Every cell is a real mix — your book funded down pro-rata to make room, up to 50% in each fund. ` +
    `Black dot is where you are now; red is the best cell on this metric. ` +
    (best && Math.abs(delta) > 0.01
      ? `<strong>${tickerA} ${(best.a * 100).toFixed(0)}% / ${tickerB} ${(best.b * 100).toFixed(0)}%</strong> takes ${metric.label.toLowerCase()} from ` +
        `${fmtNum(today.height)}${metric.unit} to ${fmtNum(best.height)}${metric.unit}. `
      : "") +
    `If the surface is a broad flat basin rather than a sharp point, the decision is not delicate — anywhere in the basin does nearly the same job, so pick on cost and tax instead.`;
}

/* Rolling correlation of each holding to the rest of the book. The "rest"
   matters: correlating a holding against a book it is 40% of mostly measures
   it against itself. */
function drawCorrelationSurface() {
  const { tickers, weights, value } = currentWeights();
  const span = parseInt(document.getElementById("corrWindow").value, 10);

  const ordered = tickers
    .map((t, i) => ({ ticker: t, weight: weights[i] }))
    .sort((a, b) => b.weight - a.weight)
    .slice(0, 14);
  const names = ordered.map(o => o.ticker);

  const { rows, index } = alignedRows(names);
  if (rows.length < span + 12) {
    document.getElementById("cubeNote").textContent =
      "Not enough overlapping history for a rolling window this long.";
    return;
  }

  const steps = Math.min(46, Math.floor((rows.length - span) / 5));
  const stride = Math.floor((rows.length - span) / steps);
  const grid = [], labels = [], cells = [];

  for (let k = 0; k <= steps; k++) {
    const start = k * stride, end = start + span;
    const slice = rows.slice(start, end);
    labels.push(index[end - 1]);
    const gridRow = [], cellRow = [];
    for (let n = 0; n < names.length; n++) {
      const own = slice.map(r => r[n]);
      // Rest of the book: every other holding, reweighted to sum to one.
      const rest = ordered.filter((_, i) => i !== n);
      const restTotal = rest.reduce((a, o) => a + o.weight, 0) || 1;
      const restSeries = slice.map(r =>
        rest.reduce((a, o, i) => {
          const column = names.indexOf(o.ticker);
          return a + r[column] * (o.weight / restTotal);
        }, 0));
      const c = correlation(own, restSeries);
      cellRow.push(c);
      gridRow.push({ x: k / steps, y: isFinite(c) ? (c + 1) / 2 : 0.5,
                     z: names.length > 1 ? n / (names.length - 1) : 0 });
    }
    grid.push(gridRow);
    cells.push(cellRow);
  }

  const scene = ensureScene();
  scene.points = [];
  scene.surface = { grid, rows: grid.length, cols: names.length, invert: true };
  scene.axes = { x: "time", y: "correlation", z: "holding" };
  scene.ranges = { x: [0, 1], y: [-1, 1], z: [0, 1] };
  scene.tickLabels = { x: [labels[0].slice(0, 7), labels[labels.length - 1].slice(0, 7)] };

  // Mark the month where the average correlation peaked - the moment the
  // book behaved most like a single position.
  let worst = 0, worstAvg = -2;
  cells.forEach((row, k) => {
    const finite = row.filter(v => isFinite(v));
    const avg = finite.reduce((a, b) => a + b, 0) / (finite.length || 1);
    if (avg > worstAvg) { worstAvg = avg; worst = k; }
  });
  scene.points.push({
    x: worst / steps, y: (worstAvg + 1) / 2, z: 0.5, r: 0.6,
    colour: "#B9002F", emphasis: true, label: labels[worst].slice(0, 7),
    tip: `<strong>${labels[worst]}</strong><br>average correlation across the book ${worstAvg.toFixed(2)}`,
  });
  scene.resize();

  cubeLegend("correlation", -1, 1);
  document.getElementById("cubeTitle").textContent =
    `Rolling ${span}-day correlation of each holding to the rest of the book`;

  const first = cells[0].filter(isFinite);
  const last = cells[cells.length - 1].filter(isFinite);
  const firstAvg = first.reduce((a, b) => a + b, 0) / (first.length || 1);
  const lastAvg = last.reduce((a, b) => a + b, 0) / (last.length || 1);
  document.getElementById("cubeNote").innerHTML =
    `Each ridge running across the whole width is one holding; each slice across the depth is one date. ` +
    `A ridge that rises everywhere at once is the book converging — the month your ${names.length} positions ` +
    `started behaving like one. Highest average was <strong>${worstAvg.toFixed(2)} around ${labels[worst].slice(0, 7)}</strong>, ` +
    `against ${firstAvg.toFixed(2)} at the start of the window and ${lastAvg.toFixed(2)} now. ` +
    `Correlations measured in calm markets understate what happens in a crash, so read the peaks as the honest number.`;
}

function cubeLegend(label, lo, hi) {
  const box = document.getElementById("cubeLegend");
  box.innerHTML = "";
  box.append(el("span", { class: "muted" }, label + ":"));
  box.append(el("span", {}, fmtNum(lo)));
  for (const colour of CUBE.RAMP) box.append(el("i", { style: `background:${colour}` }));
  box.append(el("span", {}, fmtNum(hi)));
  box.append(el("span", { class: "muted", style: "margin-left:14px" }, "drag to rotate · scroll to zoom"));
}

function renderCube() {
  cubeControls();
  if (cubeMode === "blend") drawBlendSurface(); else drawCorrelationSurface();
}

/* Both pots as one. The projection is rerun in the page rather than summed
   from the two, because adding two medians is not the median of the sum -
   the pots do not have their bad years at the same time. */
function combinePots(pots) {
  const people = Object.keys(pots);
  const total = people.reduce((a, p) => a + (pots[p].total || 0), 0);
  const holdings = [];
  const contributions = [];
  for (const person of people) {
    for (const h of pots[person].holdings || []) {
      holdings.push({ ...h, name: `${h.name} (${person})` });
    }
    for (const c of pots[person].contributions || []) {
      contributions.push({ ...c, note: `${person}: ${c.note || ""}` });
    }
  }
  contributions.sort((a, b) => a.date.localeCompare(b.date));
  const weighted = total
    ? people.reduce((a, p) => a + (pots[p].charge || 0) * (pots[p].total || 0), 0) / total
    : 0.015;
  return {
    owner: "Combined",
    total,
    estimatedTotal: people.reduce((a, p) => a + (pots[p].estimatedTotal ?? pots[p].total ?? 0), 0),
    paidIn: people.reduce((a, p) => a + (pots[p].paidIn || 0), 0),
    monthlyRate: people.reduce((a, p) => a + (pots[p].monthlyRate || 0), 0),
    monthlyContribution: people.reduce((a, p) => a + (pots[p].monthlyContribution || 0), 0),
    charge: weighted,
    beta: total ? people.reduce((a, p) => a + (pots[p].beta || 1) * (pots[p].total || 0), 0) / total : 1,
    holdings, contributions,
    bySource: people.reduce((acc, p) => {
      for (const [k, v] of Object.entries(pots[p].bySource || {})) acc[k] = (acc[k] || 0) + v;
      return acc;
    }, {}),
    pricedCount: people.reduce((a, p) => a + (pots[p].pricedCount || 0), 0),
    unpricedCount: people.reduce((a, p) => a + (pots[p].unpricedCount || 0), 0),
    accruedMonths: 0, accrued: 0,
    combinedFrom: people,
  };
}

/* ---------------- pension charts ---------------- */

/* The pot runs on different rules from the tradable books: money goes in
   every month, nothing comes out for decades, and the funds are unlisted so
   their behaviour is proxied. All three change what the chart means, so all
   three are stated on it rather than buried. */

function pensionProjection(data) {
  const years = parseInt(document.getElementById("penYears").value, 10);
  const monthly = parseFloat(document.getElementById("penMonthly").value) || 0;
  const start = data.estimatedTotal ?? data.total;
  const beta = data.beta ?? 1.0;
  // Net of the scheme's annual charge. Over decades this dominates.
  const target = capmReturn(beta) - (data.charge ?? 0.015);

  // Without a proxy series there is nothing to resample, so fall back to a
  // smooth lognormal rather than showing nothing.
  const proxy = (data.holdings || []).map(h => h.ticker).filter(t => t && columnFor(t));
  let daily = null;
  if (proxy.length) {
    const { rows } = alignedRows(proxy);
    if (rows.length > 250) {
      const w = new Array(proxy.length).fill(1 / proxy.length);
      daily = rows.map(r => r.reduce((a, v, i) => a + v * w[i], 0));
    }
  }
  const months = years * 12, paths = 4000;
  if (!daily) {
    const sigma = 0.16, mu = target;
    daily = Array.from({ length: 800 }, () => {
      let u = 0, v = 0;
      while (!u) u = Math.random();
      while (!v) v = Math.random();
      const z = Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
      return mu / TRADING_DAYS + z * sigma / Math.sqrt(TRADING_DAYS);
    });
  }
  const sample = daily.reduce((a, b) => a + b, 0) / daily.length * TRADING_DAYS;
  const track = bootstrapPaths(daily, start, months, monthly, paths, target - sample);

  const fan = [];
  const stride = Math.max(1, Math.round(months / 24));
  for (let m = stride - 1; m < months; m += stride) {
    const col = new Float64Array(paths);
    for (let p = 0; p < paths; p++) col[p] = track[p * months + m];
    col.sort();
    fan.push({ month: m + 1, p05: percentile(col, .05), p25: percentile(col, .25),
               median: percentile(col, .5), p75: percentile(col, .75), p95: percentile(col, .95) });
  }
  const final = new Float64Array(paths);
  for (let p = 0; p < paths; p++) final[p] = track[p * months + months - 1];
  final.sort();

  return { fan, years, monthly, start, target, beta,
           paidIn: start + monthly * months,
           median: percentile(final, .5), p05: percentile(final, .05),
           p95: percentile(final, .95) };
}

function renderPensionCharts(data) {
  const result = pensionProjection(data);
  const charge = data.charge ?? 0.015;
  const labels = result.fan.map(f => (f.month / 12).toFixed(0) + "y");
  const band = (to, colour) => ({
    label: "", data: result.fan.map(f => f[to]), fill: { target: "-1" },
    backgroundColor: colour, borderWidth: 0, pointRadius: 0, tension: .2, order: 3,
  });
  chart("chartPension", {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "5th percentile", data: result.fan.map(f => f.p05), borderColor: "#9BC7C8",
          borderWidth: 1, pointRadius: 0, tension: .2, fill: false },
        band("p25", "rgba(155,199,200,.32)"),
        band("median", "rgba(74,158,161,.32)"),
        band("p75", "rgba(74,158,161,.32)"),
        band("p95", "rgba(155,199,200,.32)"),
        { label: "Median", data: result.fan.map(f => f.median), borderColor: INK(),
          borderWidth: 2.4, pointRadius: 0, tension: .2, order: 0, fill: false },
        { label: "Paid in", data: result.fan.map(f => result.start + result.monthly * f.month),
          borderColor: "#B07C1F", borderWidth: 1.5, borderDash: [5, 4],
          pointRadius: 0, tension: 0, order: 1, fill: false },
      ],
    },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10,
          filter: i => i.text && i.text !== "" } },
        datalabels: { display: false },
        tooltip: { filter: i => i.dataset.label !== "",
                   callbacks: { label: c => `${c.dataset.label}: ${fmtEur(c.parsed.y)}` } },
      },
      scales: { x: gridScale({ grid: { display: false } }),
                y: gridScale({ ticks: { callback: v => "€" + (v / 1000).toFixed(0) + "k" } }) },
    },
  });

  document.getElementById("penWarning").innerHTML =
    `At ${fmtEur(result.monthly)} a month for ${result.years} years you would pay in ` +
    `<strong>${fmtEur(result.paidIn)}</strong> and the median outcome is <strong>${fmtEur(result.median)}</strong> ` +
    `(bad case ${fmtEur(result.p05)}, good case ${fmtEur(result.p95)}).<br><br>` +
    `Drift is an assumption — ${(RISK_FREE * 100).toFixed(1)}% cash plus beta ${fmtNum(result.beta)} × ${(EQUITY_RISK_PREMIUM * 100).toFixed(1)}% ` +
    `less the ${fmtPct(charge * 100)} annual charge, so ${fmtPct(result.target * 100)} a year net. ` +
    (data.chargeCost ? `<strong>That charge is the single biggest number on this page: over ` +
      `${data.chargeCost.over} years it costs about ${fmtEur(data.chargeCost.median)}, or ` +
      `${data.chargeCost.pct}% of what the pot would otherwise be.</strong> ` +
      `${fmtPct(charge * 100)} is ILIM's published <em>standard</em> rate — an occupational scheme almost ` +
      `always negotiates below retail, so treat it as an upper bound and put your real figure in the box ` +
      `above once you have it from the scheme booklet. ` : "") +
    `Contributions are assumed flat in cash terms, so anything tied to a rising salary is understated ` +
    `while the effect of inflation on the end figure is not shown at all: ${fmtEur(result.median)} in ` +
    `${result.years} years is worth far less than ${fmtEur(result.median)} today.`;

  // Contributions in, by month and source.
  const byMonth = {};
  for (const c of data.contributions || []) {
    const key = c.date.slice(0, 7);
    byMonth[key] = byMonth[key] || { employee: 0, employer: 0, other: 0 };
    const bucket = c.source === "employee" ? "employee"
                 : c.source === "employer" ? "employer" : "other";
    byMonth[key][bucket] += c.amount_eur;
  }
  const months = Object.keys(byMonth).sort();
  chart("chartPenContrib", {
    type: "bar",
    data: {
      labels: months,
      datasets: [
        { label: "Employer", data: months.map(m => byMonth[m].employer), backgroundColor: "#14527A" },
        { label: "You", data: months.map(m => byMonth[m].employee), backgroundColor: "#2A9D9F" },
      ],
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10 } },
        datalabels: { display: false },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${fmtEur(c.parsed.y)}` } } },
      scales: { x: gridScale({ stacked: true, grid: { display: false } }),
                y: gridScale({ stacked: true, ticks: { callback: v => "€" + v } }) },
    },
  });

  const funded = (data.holdings || []).filter(h => h.value_eur > 0);
  donut("chartPenSplit", funded.map(h => [h.name.replace(/ Fund.*| D Accumulating.*/, ""), h.value_eur]));
  document.getElementById("penProxyNote").textContent = data.proxyNote ||
    "Scheme funds are unlisted, so the simulation's shape is proxied by listed trackers with the same mandate. The pot value is the statement's.";
}

/* ---------------- explore ---------------- */

async function renderExplore(ticker) {
  const box = document.getElementById("exploreBox");
  const input = document.getElementById("exploreTicker");
  const symbol = (ticker || input.value || "").trim().toUpperCase();
  if (!symbol) {
    box.innerHTML = "";
    box.append(el("p", { class: "muted" },
      "Type a ticker. Anything in the return matrix is instant; anything else is fetched live through the API."));
    return;
  }

  box.innerHTML = "";
  const { tickers, weights, value } = currentWeights();
  const mine = statsFor(tickers, weights, value);
  const allocation = parseFloat(document.getElementById("exploreWeight").value) / 100;

  let local = columnFor(symbol);
  let profile = null;
  if (!local || true) {
    try {
      const response = await fetch(`/api/quote?ticker=${encodeURIComponent(symbol)}`);
      if (response.ok) profile = await response.json();
    } catch (e) { /* offline: fall back to what the build shipped */ }
  }
  if (!local && !(profile && profile.returns)) {
    box.append(el("div", { class: "warnbox" },
      `No price history for ${symbol}. Either the ticker is wrong, or it is not on Yahoo under that symbol — cross-listed ETFs are the usual culprit (EIMI.AS returns nothing while EIMI.L works).`));
    return;
  }

  // Splice a fetched series into the matrix so every existing calculation
  // works on it unchanged.
  if (!local && profile && profile.returns) {
    const map = new Map(profile.returns.dates.map((d, i) => [d, profile.returns.values[i]]));
    DATA.returns.series[symbol] = DATA.returns.dates.map(d => map.has(d) ? map.get(d) : null);
    local = DATA.returns.series[symbol];
  }

  const window_ = new Set(mine.index);
  const solo = statsFor([symbol], [1], value, window_);
  const held = holdings.find(h => h.ticker === symbol);

  const cards = el("div", { class: "kpis" });
  const facts = [
    ["Name", (profile && profile.name) || symbol, (profile && profile.sector) || ""],
    ["Price", profile && profile.price ? `${profile.currency || ""} ${fmtNum(profile.price)}` : "–",
     profile && profile.country ? profile.country : ""],
    ["Volatility", solo ? fmtPct(solo.vol) : "–", "over your book's window"],
    ["Return p.a.", solo ? fmtPct(solo.cagr) : "–", "same window"],
    ["Correlation to your book", solo ? fmtNum(correlation(solo.series, mine.series)) : "–",
     "1.00 means no diversification"],
    ["You hold", held ? fmtEur(held.value) : "nothing", held ? fmtPct(100 * held.value / value) : "not in this book"],
  ];
  for (const [k, v, s] of facts) {
    cards.append(el("div", { class: "kpi" }, el("div", { class: "k" }, k),
      el("div", { class: "v", style: String(v).length > 16 ? "font-size:15px" : "" }, v),
      el("div", { class: "s" }, s)));
  }
  box.append(cards);

  if (profile && (profile.pe || profile.marketCap || profile.dividendYield)) {
    const rows = [
      ["Market cap", profile.marketCap ? fmtEur(profile.marketCap) : "–"],
      ["P/E", profile.pe ? fmtNum(profile.pe) : "–"],
      ["Dividend yield", profile.dividendYield ? fmtPct(profile.dividendYield * 100) : "–"],
      ["52-week range", profile.low52 && profile.high52
        ? `${fmtNum(profile.low52)} – ${fmtNum(profile.high52)}` : "–"],
    ];
    box.append(el("div", { class: "panel wide", style: "margin-top:20px" },
      el("h3", {}, "Fundamentals"),
      el("div", { class: "body" }, table(["", ""], rows.map(r => ({ cells: r })), { numFrom: 1 }))));
  }

  // What buying it would do, and what the sizer suggests.
  const after = statsFor([...tickers, symbol],
    [...weights.map(w => w * (1 - allocation)), allocation], value, window_);
  const impact = el("div", { class: "panel wide", style: "margin-top:20px" },
    el("h3", {}, `Buying it at ${(allocation * 100).toFixed(0)}%`));
  const impactBody = el("div", { class: "body" });
  if (after) {
    impactBody.append(table(["", "Now", `With ${symbol}`, "Change"], [
      { cells: ["Volatility", fmtPct(mine.vol), fmtPct(after.vol),
        { text: signed(after.vol - mine.vol) + "pp", cls: after.vol < mine.vol ? "pos" : "neg" }] },
      { cells: ["Beta", fmtNum(mine.beta), fmtNum(after.beta), signed(after.beta - mine.beta)] },
      { cells: ["Worst drawdown", fmtPct(mine.maxDrawdown), fmtPct(after.maxDrawdown),
        signed(after.maxDrawdown - mine.maxDrawdown) + "pp"] },
      { cells: ["Sharpe", fmtNum(mine.sharpe), fmtNum(after.sharpe), signed(after.sharpe - mine.sharpe)] },
    ], { numFrom: 1 }));
  }
  const budgets = view().advice.budgets;
  const suggestion = sizeLocally({
    side: "buy", conviction: 6, price: (profile && profile.price) || 0,
    currentWeight: held ? held.value / value : 0, speculative: false,
  }, budgets);
  impactBody.append(el("p", { class: "note muted", style: "padding-top:12px" },
    `At conviction 6 the sizer suggests ${fmtEur(suggestion.euros)}` +
    (suggestion.shares ? ` — about ${suggestion.shares} shares` : "") +
    `, from a ${fmtEur(budgets.monthlyBuyCashEur)} monthly budget against a ${budgets.maxNameWeightPct}% single-name cap. ` +
    (suggestion.flag || "") + " Set conviction yourself on the Advice tab."));
  impact.append(impactBody);
  box.append(impact);
}

/* ---------------- portfolio theory ---------------- */

const THEORY_LABELS = {
  current: ["As it stands", "what you hold today"],
  growth: ["Growth-optimal", "maximises return − vol²∕2, the rate wealth compounds at"],
  sharpe: ["Best risk-adjusted", "maximises return per unit of risk"],
  minvar: ["Least risk", "ignores return entirely"],
  parity: ["Risk parity", "every holding contributes the same risk"],
};

function renderTheories() {
  const opt = view().optimisation;
  const box = document.getElementById("theoryTable");
  const caveat = document.getElementById("theoryCaveat");
  box.innerHTML = "";
  if (!opt) {
    caveat.textContent = "Not enough overlapping history in this book to optimise.";
    return;
  }

  const order = ["current", "growth", "sharpe", "minvar", "parity"];
  const rows = order.filter(k => opt.theories[k]).map(key => {
    const t = opt.theories[key];
    const entries = Object.entries(t.weights).sort((a, b) => b[1] - a[1]);
    const shown = entries.slice(0, 3)
      .map(([ticker, pct]) => `${ticker} ${pct.toFixed(0)}%`).join(", ");
    const rest = entries.length - 3;
    const top = shown + (rest > 0 ? ` +${rest} more` : "");
    return {
      cls: key === "current" ? "me" : "",
      cells: [
        { node: el("span", {}, el("strong", {}, THEORY_LABELS[key][0]),
            el("div", { class: "muted", style: "font-size:12px" }, THEORY_LABELS[key][1])) },
        fmtPct(t.expectedReturn),
        { text: t.fee ? t.fee.toFixed(3) + "%" : "0%", cls: "muted" },
        fmtPct(t.vol),
        { text: "−" + fmtPct(t.drag), cls: "muted" },
        { text: fmtPct(t.growth), cls: key === "current" ? "" : (t.growthGain > 0 ? "pos" : "neg") },
        { text: key === "current" ? "—" : signed(t.growthGain) + "pp",
          cls: t.growthGain > 0 ? "pos" : "neg" },
        { text: top, cls: "muted" },
      ],
    };
  });
  box.append(table(["Theory", "Expected return", "Ongoing charge", "Volatility", "Drag",
                    "Compounds at", "vs today", "Mostly"], rows, { numFrom: 1 }));

  // What the difference is actually worth over time.
  const current = opt.theories.current, best = opt.theories.growth;
  const years = 10;
  const growthOf = t => Math.pow(1 + t.growth / 100, years);
  chart("chartTheories", {
    type: "bar",
    data: {
      labels: order.filter(k => opt.theories[k]).map(k => THEORY_LABELS[k][0]),
      datasets: [
        { label: "Expected return", data: order.filter(k => opt.theories[k])
            .map(k => opt.theories[k].expectedReturn), backgroundColor: "#B8C4CC" },
        { label: "Compounds at", data: order.filter(k => opt.theories[k])
            .map(k => opt.theories[k].growth), backgroundColor: "#0F7E82" },
      ],
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10 } },
        datalabels: { display: false },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(2)}%` } } },
      scales: { x: gridScale({ grid: { display: false } }),
                y: gridScale({ ticks: { callback: v => v + "%" } }) },
    },
  });

  const multipleNow = growthOf(current), multipleBest = growthOf(best);
  caveat.innerHTML =
    `<strong>Your book is taking ${fmtPct(current.vol)} volatility to earn an expected ${fmtPct(current.expectedReturn)}, ` +
    `so it compounds at ${fmtPct(current.growth)}.</strong> The growth-optimal mix earns a higher expected ` +
    `${fmtPct(best.expectedReturn)} at ${fmtPct(best.vol)} — more return for half the risk — and compounds at ` +
    `${fmtPct(best.growth)}. Over ${years} years that is ${multipleNow.toFixed(2)}× against ${multipleBest.toFixed(2)}×. ` +
    `The uncomfortable part is that the way to more money here is <em>less</em> risk, not more.<br><br>` +
    `That conclusion rests on one assumption worth naming: expected returns are CAPM — each asset earns what its ` +
    `beta entitles it to and nothing more. The optimiser therefore cannot see any edge you think you have in ` +
    `PLTR or NVDA, and prices concentrated single-stock risk as unpaid. If you genuinely expect those to beat ` +
    `their beta, the honest reading is that you are being paid for a view the model does not hold, not that the ` +
    `model has found free money. Feeding it historical returns instead would be worse: it would put the entire ` +
    `book into whatever ran hottest. Weights are capped at ${(opt.cap * 100).toFixed(0)}% per line.`;
}

function renderHedges() {
  const rows = view().hedges || [];
  const box = document.getElementById("hedgeTable");
  box.innerHTML = "";
  if (!rows.length) {
    document.getElementById("hedgeNote").textContent = "Not enough history to test hedges in this book.";
    return;
  }
  const named = t => (DATA.funds.find(f => f.ticker === t) || {}).name || t;
  box.append(table(
    ["Fund", "Best size", "Growth gain", "Δ vol", "Δ return", "Corr.", "In your worst 10%", "Change"],
    rows.slice(0, 12).map(r => ({
      cells: [
        named(r.ticker),
        fmtPct1(r.optimalWeightPct),
        { text: signed(r.growthGain) + "pp", cls: r.growthGain > 0 ? "pos" : "neg" },
        { text: signed(r.volChange) + "pp", cls: r.volChange < 0 ? "pos" : "neg" },
        signed(r.returnChange) + "pp",
        fmtNum(r.correlation),
        fmtNum(r.stressCorrelation),
        { text: r.deterioration === undefined ? "–" : signed(r.deterioration),
          cls: r.deterioration > 0.05 ? "neg" : r.deterioration < -0.05 ? "pos" : "muted" },
      ],
    })), { numFrom: 1 }));

  const worst = [...rows].filter(r => r.deterioration !== undefined)
    .sort((a, b) => b.deterioration - a.deterioration)[0];
  const best = [...rows].filter(r => r.stressCorrelation !== undefined)
    .sort((a, b) => a.stressCorrelation - b.stressCorrelation)[0];
  const parts = [];
  if (best) {
    parts.push(`<strong>${named(best.ticker)}</strong> holds up best when your book is falling — ` +
      `correlation ${fmtNum(best.stressCorrelation)} on your worst days, and it averages ` +
      `${best.meanOnBadDays >= 0 ? "+" : ""}${fmtNum(best.meanOnBadDays)}% on them.`);
  }
  if (worst && worst.deterioration > 0.05) {
    parts.push(`<strong>${named(worst.ticker)}</strong> is the opposite trap: ${fmtNum(worst.correlation)} correlated ` +
      `on ordinary days but ${fmtNum(worst.stressCorrelation)} on your worst ones. Diversification that ` +
      `disappears in a selloff is diversification you were not actually holding.`);
  }
  parts.push("A size at the top of the range means the optimiser wants as much as it is allowed, " +
    "not that the number is precise. Correlations are measured over the book's own window, which " +
    "contains one real drawdown and no crisis — so read every stress figure here as a floor.");
  document.getElementById("hedgeNote").innerHTML = parts.join("<br><br>");
}

/* ---------------- the plan ---------------- */

function trendTag(row) {
  if (row.vsAverage200 === null || row.vsAverage200 === undefined) return "";
  const above = row.vsAverage200 >= 0;
  return `${above ? "+" : ""}${row.vsAverage200.toFixed(1)}% vs 200d`;
}

function renderPlan() {
  const plan = view().plan;
  const box = document.getElementById("planBox");
  const taxBox = document.getElementById("planTax");
  box.innerHTML = "";
  if (!plan || (!plan.sells.length && !plan.buys.length)) {
    box.append(el("p", { class: "muted" },
      "Your book is already close enough to the target that no trade clears the minimum size."));
    taxBox.textContent = "";
    return;
  }

  const month = plan.thisMonth;
  box.append(el("p", { style: "margin-bottom:14px" },
    el("strong", {}, "This month: "),
    `sell ${month.sells.length} position${month.sells.length === 1 ? "" : "s"} raising ${fmtEur(month.raised)}, `,
    `add ${fmtEur(month.newCash)} of new cash, and put ${fmtEur(month.spent)} to work. `,
    `That uses ${month.freeSellsUsed} of your ${month.freeSells} free sells.`));

  const rows = [
    ...month.sells.map(t => ({ ...t, kind: "Sell" })),
    ...month.buys.map(t => ({ ...t, kind: "Buy" })),
  ];
  const named = t => {
    const fund = DATA.funds.find(f => f.ticker === t);
    if (fund) return fund.name;
    const held = view().holdings.find(h => h.ticker === t);
    return held ? held.name : t;
  };
  box.append(table(["", "Holding", "Amount", "Shares", "Weight now", "Target", "Trend"],
    rows.map(t => ({
      cls: t.kind === "Sell" ? "" : "me",
      cells: [
        { text: t.kind + (t.partial ? " (part)" : ""), cls: t.kind === "Sell" ? "neg" : "pos" },
        named(t.ticker),
        fmtEur(t.euros),
        t.shares ? fmtNum(t.shares) : "–",
        fmtPct1(t.currentPct),
        fmtPct1(t.targetPct),
        { text: trendTag(t), cls: "muted" },
      ],
    })), { numFrom: 2 }));

  box.append(el("p", { class: "note muted", style: "padding-top:12px" },
    `The full move is ${fmtEur(plan.turnover)} of trading — ${plan.turnoverPct}% of the book — which at this ` +
    `cash rate takes roughly ${plan.months} months. Nothing forces you to do it all: the first month captures ` +
    `most of the concentration reduction, since the largest single-name positions go first.`));

  // Tax is usually the biggest number nobody models.
  const tax = plan.tax || {};
  const cost = plan.tradingCost || {};
  const growthGain = view().optimisation.theories.growth.growthGain;
  const annual = view().priced * growthGain / 100;
  const parts = [];

  if (cost.total !== undefined) {
    const worst = cost.worst;
    parts.push(`<strong>Dealing costs.</strong> This month's ${cost.trades.length} trades cost about ` +
      `${fmtEur(cost.total)} in commission and currency conversion` +
      (worst ? `, and the burden is uneven — ${worst.ticker} at ${fmtEur(worst.euros)} pays ` +
        `${fmtNum(worst.costPct)}% of the trade just to be executed. Small trades are where a minimum ` +
        `commission does the damage; batching them into fewer, larger ones is worth more than getting ` +
        `the allocation exactly right.` : ".") +
      ` That assumes ${(cost.assumptions.commission_pct * 100).toFixed(2)}% commission with a ` +
      `${fmtEur(cost.assumptions.commission_min)} minimum and ${(cost.assumptions.fx_pct * 100).toFixed(2)}% ` +
      `on currency — adjust if your rate differs.`);
  }
  if (tax.tax > 0) {
    const payback = tax.tax / Math.max(annual, 1);
    parts.push(`<strong>Tax first.</strong> These sells realise ${fmtEur(tax.gain)} of gains. After the ` +
      `${fmtEur(tax.exemption)} annual exemption that is ${fmtEur(tax.taxable)} taxable, ` +
      `<strong>${fmtEur(tax.tax)} of CGT at ${(tax.rate * 100).toFixed(0)}%</strong> — payable now, against ` +
      `roughly ${fmtEur(annual)} a year of extra compounding. It pays for itself in about ${payback.toFixed(1)} years, ` +
      `so this is only worth doing if you intend to hold the new allocation for longer than that.`);
  } else {
    parts.push(`<strong>Tax first, and here it is the good news.</strong> These sells realise ${fmtEur(tax.gain)} of ` +
      `net gains — losses on some positions offset the winners — which is inside the ${fmtEur(tax.exemption)} annual ` +
      `exemption, so the CGT bill is <strong>nil</strong>. That will not be true next year if these positions keep ` +
      `rising, which is an argument for doing it now rather than later.`);
  }
  if (tax.unknownBasis && tax.unknownBasis.length) {
    parts.push(`No cost basis imported for ${tax.unknownBasis.map(r => r.ticker).join(", ")}, so their gains are ` +
      `not in that figure. Import the statement covering them before relying on the tax number.`);
  }
  const switchCost = (cost.total || 0) + (tax.tax || 0);
  if (annual > 0) {
    parts.push(`<strong>All in: ${fmtEur(switchCost)} to switch</strong>, against roughly ${fmtEur(annual)} a year ` +
      `of extra compounding — about ${(switchCost / annual).toFixed(2)} years to pay back. ` +
      `That is the number to judge this on, not the headline improvement.`);
  }
  parts.push(`Tax is estimated on average cost. Irish CGT is FIFO with a four-week rule on losses, so the ` +
    `real figure will differ — treat it as the order of magnitude, not the return.`);
  taxBox.innerHTML = parts.join("<br><br>");
}

/* ---------------- mortgage deposit ---------------- */

function renderGoal() {
  const goal = DATA.goal || {};
  const box = document.getElementById("goalBox");
  box.innerHTML = "";
  if (!goal.target) {
    box.append(el("p", { class: "muted" },
      "No Goal_Mortgage tab found in the analytics sheet."));
    return;
  }

  // The target is a household one - a house is bought once - but the money
  // sits in two books, so the switcher decides whose share is shown while
  // the goal itself stays the household's.
  const showing = BOOK === "Combined" ? null : BOOK;
  const lines = showing ? goal.lines.filter(l => l.book === showing) : goal.lines;
  const thisBook = lines.reduce((a, l) => a + l.value, 0);

  const cards = el("div", { class: "kpis" });
  for (const [k, v, s] of [
    showing
      ? [`${showing} holds`, fmtEur(thisBook),
         `of ${fmtEur(goal.held)} saved between you`]
      : ["Saved", fmtEur(goal.held), `${goal.pct}% of ${fmtEur(goal.target)}`],
    ["Still to find", fmtEur(goal.gap),
     goal.monthsRemaining !== null ? `over ${goal.monthsRemaining} months` : ""],
    ["Needed each month", goal.requiredMonthly === null ? "–" : fmtEur(goal.requiredMonthly),
     `you plan ${fmtEur(goal.monthlyContribution)}`],
    ["Target date", (goal.targetDate || "").slice(0, 10),
     goal.onTrack ? "on track" : "behind"],
  ]) {
    cards.append(el("div", { class: "kpi" }, el("div", { class: "k" }, k),
      el("div", { class: "v" }, v), el("div", { class: "s" }, s)));
  }
  box.append(cards);

  // Progress bar - the one chart this deserves.
  const pct = Math.min(100, goal.pct);
  const bar = el("div", { style: "margin:20px 0 6px;height:26px;background:var(--mist);border:1px solid var(--line);position:relative" },
    el("div", { style: `height:100%;width:${pct}%;background:${goal.onTrack ? "var(--accent)" : "#B07C1F"}` }),
    el("div", { style: "position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:12.5px;font-weight:600" },
      `${fmtEur(goal.held)} of ${fmtEur(goal.target)}`));
  box.append(bar);

  const panel = el("div", { class: "panel wide", style: "margin-top:20px" },
    el("h3", {}, "Where the deposit is"));
  const body = el("div", { class: "body" });
  body.append(table(["Book", "Account", "Amount"],
    goal.lines.map(l => ({
      cls: showing && l.book === showing ? "me" : "",
      cells: [l.book, l.name, fmtEur(l.value)],
    })), { numFrom: 2 }));
  panel.append(body);
  box.append(panel);

  const notes = [];
  if (Math.abs(goal.countedElsewhere) > 1) {
    notes.push(`<strong>The sheet's own tracker counts ${fmtEur(goal.sheetHeld)}, not ${fmtEur(goal.held)}.</strong> ` +
      `It looks at one book; ${fmtEur(goal.countedElsewhere)} of deposit money sits in the other. That is the ` +
      `difference between ${goal.pct}% of the target and ${(100 * goal.sheetHeld / goal.target).toFixed(1)}%, ` +
      `and between being on track and a reported shortfall. Worth reconciling — if that money is earmarked ` +
      `for something else, this page is the one that is wrong.`);
  }
  notes.push(goal.onTrack
    ? `At ${fmtEur(goal.monthlyContribution)} a month you reach ${fmtEur(goal.target)} with room to spare — ` +
      `${fmtEur(goal.requiredMonthly)} a month is what the remaining gap actually requires.`
    : `${fmtEur(goal.requiredMonthly)} a month is required and ${fmtEur(goal.monthlyContribution)} is planned, ` +
      `a shortfall of ${fmtEur(goal.shortfallMonthly)} a month. Either the date moves, the target moves, or ` +
      `the monthly amount does.`);
  if (showing) {
    notes.push(`You are looking at ${showing}'s book, so the highlighted row is ${showing}'s ` +
      `${fmtEur(thisBook)} of it. The target is a household one — a house is bought once — so the ` +
      `progress bar and the monthly figure stay whole rather than being split in two.`);
  }
  notes.push(`This money is deliberately excluded from every portfolio weight, risk figure and rebalance ` +
    `suggestion on the rest of the site. A deposit needed in ${goal.monthsRemaining} months has no business ` +
    `in equities: the horizon is far too short for volatility to average out, and the cost of being down ` +
    `20% on the day you need it is not a paper loss, it is not buying the house.`);
  box.append(el("div", { class: "warnbox", style: "margin-top:18px" },
    (() => { const d = el("div"); d.innerHTML = notes.join("<br><br>"); return d; })()));
}

/* ---------------- contribution helpers ---------------- */

/* Employer and employee land as separate rows on the same day, so a raw
   list reads as twice as many events as actually happened and the number
   people want - what went in this month - is never shown. Grouped, the
   answer is one row per month. */

const CONTRIB_COLOURS = { employee: "#14527A", employer: "#B9002F" };

function contributionsByMonth(contributions) {
  const months = {};
  for (const row of contributions) {
    const key = (row.date || "").slice(0, 7);
    if (!key) continue;
    const month = months[key] || (months[key] = {
      month: key, total: 0, employee: 0, employer: 0, other: 0,
    });
    const amount = Number(row.amount_eur) || 0;
    month.total += amount;
    if (row.source === "employee") month.employee += amount;
    else if (row.source === "employer") month.employer += amount;
    else month.other += amount;
  }
  return Object.values(months).sort((a, b) => b.month.localeCompare(a.month));
}

/* A two-slice pie, drawn as SVG rather than a chart instance: one Chart.js
   object per table row would be dozens of canvases and animation loops for
   a shape that never changes. */
function splitPie(employee, employer, size = 26) {
  const total = employee + employer;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("viewBox", "0 0 32 32");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label",
    total ? `${Math.round(100 * employee / total)}% you, ${Math.round(100 * employer / total)}% employer`
          : "no contributions");
  svg.style.verticalAlign = "middle";

  // A filled pie, not a ring: the stroke is drawn on a circle of radius 8
  // and is 16 wide, so it reaches from the centre out to r=16 exactly.
  // Drawing it on r=15.9 - the usual trick for a percentage dash array -
  // pushed the stroke half outside the viewBox and rendered as a square.
  const RADIUS = 8, CIRCUMFERENCE = 2 * Math.PI * RADIUS;
  const slice = (colour, share) => {
    const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    c.setAttribute("cx", 16); c.setAttribute("cy", 16); c.setAttribute("r", RADIUS);
    c.setAttribute("fill", "none");
    c.setAttribute("stroke", colour);
    c.setAttribute("stroke-width", RADIUS * 2);
    if (share !== undefined) {
      const length = share * CIRCUMFERENCE;
      c.setAttribute("stroke-dasharray", `${length} ${CIRCUMFERENCE - length}`);
    }
    c.setAttribute("transform", "rotate(-90 16 16)");
    return c;
  };

  if (!total) {
    svg.append(slice(cssVar("--line") || "#E6E3DE"));
    return svg;
  }
  svg.append(slice(CONTRIB_COLOURS.employer));
  const employeeShare = employee / total;
  if (employeeShare > 0) svg.append(slice(CONTRIB_COLOURS.employee, employeeShare));
  svg.setAttribute("title",
    `You ${employee.toFixed(0)}, employer ${employer.toFixed(0)}`);
  return svg;
}

/* ---------------- levers and feasibility ---------------- */

function renderLevers() {
  const data = DATA.levers;
  const box = document.getElementById("leverTable");
  if (!data) return;
  box.innerHTML = "";

  box.append(table(["Lever", "Change", "Ends at", "Difference", "Why"],
    data.levers.map(l => ({
      cls: l.gain > 0 ? "" : "",
      cells: [
        { node: el("strong", {}, l.name) },
        l.change,
        fmtEur(l.terminal),
        { text: (l.gain >= 0 ? "+" : "−") + fmtEur(Math.abs(l.gain)).slice(1),
          cls: l.gain >= 0 ? "pos" : "neg" },
        { text: l.note, cls: "muted" },
      ],
    })), { numFrom: 2 }));

  chart("chartLevers", {
    type: "bar",
    data: {
      labels: data.levers.map(l => l.name),
      datasets: [{
        data: data.levers.map(l => l.gain),
        backgroundColor: data.levers.map(l => l.gain >= 0 ? "#0F7E82" : "#B9002F"),
      }],
    },
    options: {
      maintainAspectRatio: false, indexAxis: "y",
      plugins: { legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { label: c => fmtEur(c.parsed.x) } } },
      scales: { x: gridScale({ ticks: { callback: v => "€" + (v / 1000).toFixed(0) + "k" } }),
                y: gridScale({ grid: { display: false } }) },
    },
  });

  const top = data.levers[0];
  document.getElementById("leverNote").innerHTML =
    `<strong>Over ten years, ${top.name.toLowerCase()} is worth ${fmtEur(top.gain)} — more than every ` +
    `allocation decision on this page combined.</strong> Contributions are ${data.contributionsShare}% of ` +
    `the ${fmtEur(data.base)} base case, which is what a small pot looks like: the money you add dominates ` +
    `the money it earns.<br><br>` +
    `Returns only overtake a year of saving after <strong>${data.crossoverYears} years</strong>, at about ` +
    `${fmtEur(data.crossoverValue)}. Before that point, time spent optimising the portfolio is time spent on ` +
    `the smallest term in the equation — and after it, the habit you built is what got you there.<br><br>` +
    `Assumes 8% a year, which is roughly what global equities have returned over the long run. Nothing here ` +
    `is a forecast; it is arithmetic on an assumption, shown so the terms can be compared against each other.`;
}

function renderMilestones() {
  const rows = DATA.milestones || [];
  const box = document.getElementById("milestoneTable");
  if (!rows.length) return;
  box.innerHTML = "";

  box.append(table(["Target in 10 years", "Needs", "Multiple of today", "Verdict"],
    rows.map(m => ({
      cells: [
        { node: el("strong", {}, fmtEur(m.target)) },
        { text: fmtPct1(m.requiredPct),
          cls: m.requiredPct <= 10 ? "pos" : m.requiredPct <= 20 ? "" : "neg" },
        `${m.multipleOfStart.toLocaleString("en-IE")}×`,
        { text: m.verdict, cls: "muted" },
      ],
    })), { numFrom: 1 }));

  const reference = rows[0];
  const benchmarks = (reference.benchmarks || [])
    .map(b => `${b.name} ${b.rate}%`).join(" · ");
  document.getElementById("milestoneNote").innerHTML =
    `For scale: ${benchmarks}. A required return is the one input nobody sanity-checks, and a number that ` +
    `sounds ambitious looks identical to one that is arithmetically impossible until it is written down next ` +
    `to what the best investors alive have actually sustained. At a defensible 8% this book reaches ` +
    `<strong>${fmtEur(reference.atEightPercent)}</strong> in ten years — and the honest way to move that ` +
    `number is the table above, not a better fund.`;
}

/* ---------------- orders ---------------- */

const URGENCY_CLASS = {
  "now": "urg-now", "this week": "urg-week",
  "this month": "urg-month", "opportunistic": "urg-opp",
};

function renderOrders() {
  const orders = view().orders || [];
  const box = document.getElementById("orderBox");
  box.innerHTML = "";
  if (!orders.length) {
    box.append(el("p", { class: "muted" },
      "Nothing to place — the book is close enough to target that no trade clears the minimum size."));
    document.getElementById("orderNote").textContent = "";
    return;
  }

  const named = t => {
    const fund = DATA.funds.find(f => f.ticker === t);
    if (fund) return fund.name;
    const held = view().holdings.find(h => h.ticker === t);
    return held ? held.name : t;
  };

  const money = (value, currency) =>
    currency === "USD" ? "$" + value.toFixed(2) : fmtEur(value);

  box.append(table(
    ["When", "Order", "Holding", "Amount", "Limit price", "If unfilled", "Costs to wait"],
    orders.map(o => ({
      cls: o.skipped ? "muted" : "",
      cells: [
        { node: el("span", { class: "urg " + (o.skipped ? "urg-opp" : URGENCY_CLASS[o.urgency]) },
            o.skipped ? "skip" : o.urgency) },
        { node: el("strong", { class: o.side === "sell" ? "neg" : "pos" },
            o.skipped ? "—"
            : `${o.side === "sell" ? "SELL" : "BUY"} ${o.shares ? Math.round(o.shares) : ""}`) },
        named(o.ticker),
        o.euros ? fmtEur(o.euros) : "–",
        // The broker quotes a US line in dollars, so that is the number
        // typed into the ticket; the euro figure is for reconciling here.
        { node: o.limit
            ? el("span", {},
                el("strong", {}, money(o.nativeLimit ?? o.limit, o.currency)),
                el("div", { class: "muted", style: "font-size:11.5px" },
                  `now ${money(o.nativePrice ?? o.price, o.currency)}` +
                  (o.currency === "USD" ? ` · ${fmtEur(o.limit)}` : "") +
                  ` · ${o.bandPct}% band`))
            : el("span", { class: "muted" }, "market") },
        { text: o.deadline
            ? `${o.deadline} (${o.deadlineDays}d)`
            : `limit expires ${o.limitExpires}`,
          cls: o.deadlineDays !== null && o.deadlineDays <= 5 ? "neg" : "muted" },
        { text: o.costPerMonth >= 1 ? fmtEur(o.costPerMonth) + "/mo" : "–",
          cls: o.costPerMonth >= 20 ? "neg" : "muted" },
      ],
    })), { numFrom: 3 }));

  const detail = el("div", { style: "margin-top:18px" });
  for (const o of orders.slice(0, 4)) {
    detail.append(el("div", { style: "padding:12px 0;border-bottom:1px solid var(--line-soft)" },
      el("div", {},
        el("span", { class: "urg " + URGENCY_CLASS[o.urgency] }, o.urgency),
        el("strong", { style: "margin-left:10px" },
          `${o.side === "sell" ? "Sell" : "Buy"} ${named(o.ticker)}`),
        el("span", { class: "muted" },
          ` — ${fmtEur(o.euros)}${o.shares ? `, about ${fmtNum(o.shares)} shares` : ""}`)),
      el("div", { class: "muted", style: "font-size:13px;margin-top:6px" },
        o.limit ? `${o.side === "sell" ? "Limit sell" : "Limit buy"} at ` +
          `${money(o.nativeLimit ?? o.limit, o.currency)}` +
          (o.currency === "USD" ? ` (${fmtEur(o.limit)})` : "") + `. ${o.rationale}` : ""),
      o.rounding ? el("div", { style: "font-size:13px;margin-top:6px;color:#B07C1F" }, o.rounding) : null,
      el("div", { class: "muted", style: "font-size:13px;margin-top:6px" }, o.deadlineReason || "")));
  }
  box.append(detail);

  const urgent = orders.filter(o => o.urgency === "now");
  const totalCost = orders.reduce((a, o) => a + o.costPerMonth, 0);
  document.getElementById("orderNote").innerHTML =
    (urgent.length
      ? `<strong>${urgent.length} order${urgent.length === 1 ? "" : "s"} marked now.</strong> ` +
        (urgent.some(o => o.overCap)
          ? `${urgent.filter(o => o.overCap).map(o => o.ticker).join(", ")} sits past the ` +
            `${(view().advice.budgets.maxNameWeightPct)}% single-name cap, which is concentration you did ` +
            `not choose rather than a view you took. `
          : "") +
        `The rest are dated by the free-sell allowance, which resets at month end and does not carry over.<br><br>`
      : "") +
    `Leaving the whole plan undone costs about <strong>${fmtEur(totalCost)} a month</strong> in foregone ` +
    `compounding — that figure, not a colour, is what "urgent" means here.<br><br>` +
    `Sizes are whole shares and a sell is the whole position, because fractions cannot be traded in these ` +
    `accounts. That is not a rounding detail: an instruction to trim 62% of a holding cannot be placed, so ` +
    `the numbers here are the ones that can — with whatever they leave in cash stated on the order.<br><br>` +
    `Prices in bold are the currency the broker quotes, which for a US line is dollars. The euro figure ` +
    `beside it is for reconciling against the rest of this site; typing it into a dollar ticket would be a ` +
    `16% error on the one number that has to be exact.<br><br>` +
    `Two honest limits on all of this. The limit prices assume the recent daily volatility holds, which it ` +
    `does until it does not; if a holding gaps through your limit on news, you get filled at a price the ` +
    `band never contemplated. And the destination comes from CAPM, which cannot see any edge you believe ` +
    `you have — so these are the orders that follow from the model, not a claim that the model is right.`;
}

function renderDeadlines() {
  const rows = DATA.deadlines || [];
  const box = document.getElementById("deadlineTable");
  if (!rows.length) return;
  box.innerHTML = "";
  box.append(table(["Date", "In", "What", "Why it matters"],
    rows.map(r => ({
      cells: [
        { node: el("strong", {}, r.when) },
        { text: `${r.days} days`, cls: r.days <= 14 ? "neg" : "" },
        r.what,
        { text: r.why, cls: "muted" },
      ],
    })), { numFrom: 1 }));
}
