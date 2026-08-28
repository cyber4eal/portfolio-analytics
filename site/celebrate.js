/* Bond — milestone celebrations.

   Two things that have to stay apart. A *ledger* of what has already been
   banked, which is durable and lives in this browser, and a *renderer*,
   which is pure decoration. Without the ledger every page load would set
   off fireworks for a number you passed months ago, and a celebration that
   fires every time is indistinguishable from a background image. */

const Celebrate = (() => {
  const KEY = "bond.milestones.v1";
  const SEEDED = "_seeded";

  const reduced = () => !!(window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  /* ---------------- the ledger ---------------- */

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; }
  }
  function store(ledger) {
    try { localStorage.setItem(KEY, JSON.stringify(ledger)); } catch (e) {}
  }

  /* `items` is everything currently true, not everything new. Returns only
     what had not been banked before, so the caller can celebrate it.

     The first call ever banks silently. A browser opening this page for the
     first time has not just earned eleven milestones at once - it has just
     been told about eleven that were already true - and firing for all of
     them would teach you to ignore the next one, which is the only one that
     will mean anything. */
  function bank(items) {
    const ledger = load();
    const seeded = !!ledger[SEEDED];
    const fresh = [];
    for (const it of items) {
      if (ledger[it.id]) continue;
      ledger[it.id] = { at: new Date().toISOString(), label: it.label, detail: it.detail };
      if (seeded) fresh.push(it);
    }
    if (!seeded) ledger[SEEDED] = { at: new Date().toISOString() };
    store(ledger);
    return fresh;
  }

  /* Banked milestones, newest first. A milestone is never withdrawn: a book
     that dips back under EUR 30,000 still crossed EUR 30,000, and a counter
     that goes backwards is a counter nobody trusts. */
  function achieved() {
    const ledger = load();
    return Object.entries(ledger)
      .filter(([id]) => id !== SEEDED)
      .map(([id, v]) => ({ id, ...v }))
      .sort((a, b) => (a.at < b.at ? 1 : -1));
  }

  function seededAt() { return (load()[SEEDED] || {}).at || null; }

  function forget() { try { localStorage.removeItem(KEY); } catch (e) {} }

  /* ---------------- the renderer ---------------- */

  const SHELL_GRAVITY = 0.00030;   // px per ms squared
  const SPARK_GRAVITY = 0.00024;
  const DRAG = 0.972;              // per 16ms, applied as a power of dt

  let canvas = null, ctx = null, raf = 0, last = 0;
  let shells = [], sparks = [];

  /* Colours come from the stylesheet, so the burst repaints with the theme
     instead of firing navy sparks onto a dark page. */
  function palette() {
    const root = getComputedStyle(document.documentElement);
    const picked = ["--c9", "--c4", "--c1", "--c10", "--c3", "--accent"]
      .map(n => root.getPropertyValue(n).trim())
      .filter(c => /^#[0-9a-f]{3,6}$/i.test(c));
    return picked.length ? picked : ["#00B2A9", "#14527A", "#6FD5CE"];
  }

  function rgb(hex) {
    const h = hex.replace("#", "");
    const full = h.length === 3 ? h.split("").map(c => c + c).join("") : h;
    const n = parseInt(full, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function size() {
    if (!canvas) return;
    // Capped at 2: a 3x phone screen triples the fill cost for a difference
    // nobody can see on a spark two pixels wide.
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(window.innerWidth * dpr);
    canvas.height = Math.round(window.innerHeight * dpr);
    canvas.style.width = window.innerWidth + "px";
    canvas.style.height = window.innerHeight + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function teardown() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    window.removeEventListener("resize", size);
    if (canvas) canvas.remove();
    canvas = null; ctx = null; shells = []; sparks = [];
  }

  function burst(shell) {
    const [r, g, b] = rgb(shell.colour);
    const count = 44 + Math.floor(Math.random() * 34);
    const base = 0.11 + Math.random() * 0.09;
    // A ring plus a slower inner core reads as a firework; one uniform ring
    // reads as a clock face.
    for (let i = 0; i < count; i++) {
      const angle = (i / count) * Math.PI * 2 + Math.random() * 0.14;
      const speed = base * (i % 5 === 0 ? 0.35 + Math.random() * 0.3
                                        : 0.62 + Math.random() * 0.6);
      sparks.push({
        x: shell.x, y: shell.y, px: shell.x, py: shell.y,
        vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
        life: 0, span: 850 + Math.random() * 1000,
        r, g, b, twinkle: Math.random() < 0.28,
      });
    }
  }

  function frame(now) {
    const dt = Math.min(now - last, 48);
    last = now;
    ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    ctx.globalCompositeOperation = "lighter";
    ctx.lineCap = "round";

    for (const s of shells) {
      if (s.delay > 0) { s.delay -= dt; continue; }
      s.px = s.x; s.py = s.y;
      s.x += s.vx * dt;
      s.y += s.vy * dt;
      s.vy += SHELL_GRAVITY * dt;
      const [r, g, b] = rgb(s.colour);
      ctx.strokeStyle = `rgba(${r},${g},${b},0.85)`;
      ctx.lineWidth = 2.4;
      ctx.beginPath(); ctx.moveTo(s.px, s.py); ctx.lineTo(s.x, s.y); ctx.stroke();
      if (s.y <= s.burstY || s.vy >= 0) { burst(s); s.spent = true; }
    }
    shells = shells.filter(s => !s.spent);

    for (const p of sparks) {
      p.life += dt;
      const t = p.life / p.span;
      if (t >= 1) { p.spent = true; continue; }
      const decay = Math.pow(DRAG, dt / 16);
      p.vx *= decay; p.vy *= decay;
      p.vy += SPARK_GRAVITY * dt;
      p.px = p.x; p.py = p.y;
      p.x += p.vx * dt; p.y += p.vy * dt;
      let alpha = 1 - t * t;
      if (p.twinkle) alpha *= 0.5 + 0.5 * Math.sin(p.life / 28);
      ctx.strokeStyle = `rgba(${p.r},${p.g},${p.b},${alpha.toFixed(3)})`;
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(p.px, p.py); ctx.lineTo(p.x, p.y); ctx.stroke();
    }
    sparks = sparks.filter(p => !p.spent);

    if (shells.length || sparks.length) raf = requestAnimationFrame(frame);
    else teardown();
  }

  /* Fires over whatever is already in the air, so two milestones banked in
     the same render make one bigger display rather than two queued ones. */
  function fire({ count = 8 } = {}) {
    if (reduced()) return false;
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.className = "fireworks";
      canvas.setAttribute("aria-hidden", "true");
      document.body.append(canvas);
      ctx = canvas.getContext("2d");
      size();
      window.addEventListener("resize", size);
    }
    const colours = palette();
    const w = window.innerWidth, h = window.innerHeight;
    for (let i = 0; i < count; i++) {
      shells.push({
        delay: i * (150 + Math.random() * 260),
        x: w * (0.15 + Math.random() * 0.7),
        y: h + 12,
        vx: (Math.random() - 0.5) * 0.05,
        vy: -(h * 0.00085 + Math.random() * h * 0.00035),
        burstY: h * (0.10 + Math.random() * 0.32),
        colour: colours[i % colours.length],
      });
    }
    if (!raf) { last = performance.now(); raf = requestAnimationFrame(frame); }
    return true;
  }

  /* ---------------- the announcement ---------------- */

  function toastHost() {
    let host = document.getElementById("celebrateToasts");
    if (!host) {
      host = document.createElement("div");
      host.id = "celebrateToasts";
      host.className = "toasts";
      // polite, not assertive: a firework is not an alert, and a screen
      // reader should finish the sentence it is on before mentioning it.
      host.setAttribute("role", "status");
      host.setAttribute("aria-live", "polite");
      document.body.append(host);
    }
    return host;
  }

  function announce(items) {
    if (!items.length) return;
    const host = toastHost();
    for (const it of items) {
      const card = document.createElement("div");
      card.className = "toast" + (it.grand ? " grand" : "");
      card.innerHTML =
        '<div class="mark">✦</div>' +
        '<div class="body"><div class="kicker">Milestone reached</div>' +
        '<div class="headline"></div><div class="detail"></div></div>' +
        '<button class="close" aria-label="Dismiss">×</button>';
      card.querySelector(".headline").textContent = it.label;
      card.querySelector(".detail").textContent = it.detail || "";
      const shut = () => { card.classList.add("out"); setTimeout(() => card.remove(), 320); };
      card.querySelector(".close").addEventListener("click", shut);
      host.append(card);
      setTimeout(shut, 11000);
    }
  }

  return { bank, achieved, seededAt, forget, fire, announce, reduced };
})();
