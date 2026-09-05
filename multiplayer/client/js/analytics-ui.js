/* Currents and Critters: Developer Analytics dashboard (admin only).
 *
 * A full-screen overlay opened from the Player Home, closed with the ✕ in its
 * header (or Escape). Every number comes from the server-authoritative
 * /api/analytics/* API through the window.__ccAnalytics bridge; this file owns
 * no maths beyond formatting, and no player data ever reaches it un-aggregated.
 *
 * THE DESIGN CONTRACT, the thing to defend when adding to this file
 * The dashboard is useful within five seconds, and it stays that way only if
 * new information is added BEHIND something rather than beside it:
 *   • Overview shows ten summary cards and exactly four blocks. Nothing else.
 *   • A page never draws more than four charts. Related measures share ONE
 *     chart with a switcher (see Player Growth) instead of sitting three-abreast.
 *   • Every chart answers one question, printed under its title.
 *   • Detail lives in the drawer (openDrawer), advanced options live in the
 *     collapsed tray, definitions live in tooltips and the Help drawer.
 *   • Only the selected section is fetched and rendered.
 * If something new has nowhere to go, that means it belongs in a section, or
 * behind a "View details", which is nearly always the right answer.
 *
 * COLOUR
 * Identity colours are --a-series-1/2/3 in that fixed order (see analytics.css
 * for why those three), and most charts use only the first. Green/amber/red are
 * reserved for state and always carry a word, so nothing is said in colour alone.
 */
(function () {
  "use strict";

  function bridge() { return window.__ccAnalytics; }

  // ── Helpers ───────────────────────────────────────────────────────────────
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  // Big numbers use the font's proportional figures and compact above 10k:
  // "12.4K" reads at a glance where "12,438" has to be counted.
  function fmt(n) {
    if (n == null || n === "") return "-";
    if (typeof n === "string") return n;
    const v = Number(n);
    if (!isFinite(v)) return "-";
    if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (Math.abs(v) >= 1e4) return (v / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
    return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
  }
  function fmtAgo(sec) {
    const s = Math.max(0, Math.floor(Number(sec) || 0));
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }
  function fmtWhen(unix) {
    if (!unix) return "-";
    try {
      return new Date(unix * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    } catch (_) { return "-"; }
  }
  function dayLabel(iso) {
    const p = String(iso || "").split("-");
    if (p.length !== 3) return iso || "";
    const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return MON[Number(p[1]) - 1] + " " + Number(p[2]);
  }
  const sum = (a) => (a || []).reduce((x, y) => x + (Number(y) || 0), 0);

  // ── State ─────────────────────────────────────────────────────────────────
  const S = {
    open: false,
    section: "overview",
    data: {},              // section → last payload
    loading: {},           // section → true while in flight
    error: {},             // section → message
    seq: 0,                // bumped on every navigation; late fetches check it
    liveTimer: null,
    filters: {
      days: 30,
      compare: false,
      include_bots: false,
      include_test: false,
      include_guests: false,
      mode: "all",
      player_count: 0,
      min_sample: 20,
    },
    growthMeasure: "new",
    advOpen: false,
    tablePage: {},
    extraCols: {},
    searchQuery: "",
  };

  const SECTIONS = [
    { id: "overview",    name: "Overview",         ico: "◈" },
    { id: "players",     name: "Players",          ico: "◉" },
    { id: "gameplay",    name: "Gameplay",         ico: "▦" },
    { id: "cards",       name: "Cards",            ico: "▤" },
    { id: "competitive", name: "Competitive",      ico: "✦" },
    { id: "clans",       name: "Clans",            ico: "⬡" },
    { id: "economy",     name: "Economy",          ico: "◍" },
    { id: "events",      name: "Events",           ico: "◆" },
    { id: "technical",   name: "Technical Health", ico: "▲" },
    { id: "search",      name: "Player Search",    ico: "⌕" },
  ];

  const RANGES = [
    { days: 7,  label: "Last 7 days" },
    { days: 14, label: "Last 14 days" },
    { days: 30, label: "Last 30 days" },
    { days: 90, label: "Last 90 days" },
  ];

  // Plain-language definitions. They live HERE and in the Help drawer, never
  // as a paragraph on the dashboard itself.
  const DEFS = {
    "New players": "Accounts created during the selected date range.",
    "Active players": "Accounts that opened the game at least once in the range.",
    "Players who returned": "Players active in this range who joined before it started.",
    "Came back after 7 days": "Of players who joined at least a week ago, the share still playing seven days later.",
    "Games completed": "Games that reached their real ending, with a winner.",
    "Games finished": "The share of started games that were played to the end.",
    "Players online now": "Signed in and seen within the last few minutes.",
    "Games being played": "Rooms with a game running right now.",
    "Average game length": "Mean time from the first turn to the final score.",
    "Server": "Green when every health check passes. Details live in Technical Health.",
    "Games players left early": "Games that ended without a final score because people left.",
    "Ranked matches": "Competitive matches recorded in the range, forfeits included.",
    "Worth a balance look": "Animals whose win rate sits far from typical, on a big enough sample to trust.",
    "Typical win rate": "The average win rate across animals that met the sample size.",
  };

  // ══════════════════════════════════════════════════════════════════════════
  //  API
  // ══════════════════════════════════════════════════════════════════════════
  // The host bridge's post() resolves to an ENVELOPE: { ok, status, data },
  // where `ok` is only the HTTP status. Unwrapping it here, once, is what stops
  // a 200 with an error body from looking like success (the exact bug that once
  // blanked the Clans tab). A bare payload passes straight through, so test
  // harnesses that already unwrap keep working.
  function unwrap(res) {
    if (res && typeof res === "object" && "data" in res && "status" in res) {
      return res.data || { ok: false, error: "server_error" };
    }
    return res;
  }

  async function post(action, extra) {
    const b = bridge();
    if (!b) return { ok: false, error: "unavailable" };
    const body = Object.assign({}, S.filters, extra || {});
    try { body.idToken = await b.idToken(); } catch (_) { body.idToken = ""; }
    if (!body.idToken) return { ok: false, error: "unauthorized" };
    try {
      return unwrap(await b.post("/api/analytics/" + action, body));
    } catch (_) {
      // The request never landed: retryable, and NOT the same thing as the
      // server refusing it.
      return null;
    }
  }

  const ERRORS = {
    unavailable: "The dashboard didn't finish loading: please refresh the page.",
    unauthorized: "This dashboard is for the developer account only.",
    section_failed: "That section couldn't be built from the current data.",
    unknown_section: "That section doesn't exist.",
  };
  const errMsg = (e) => ERRORS[e] || "Couldn't load this section: try Refresh.";

  // ══════════════════════════════════════════════════════════════════════════
  //  CHART PRIMITIVES
  //  Hand-drawn SVG on purpose: no chart library is loaded anywhere in this
  //  app, and these five shapes are all the dashboard needs.
  // ══════════════════════════════════════════════════════════════════════════
  const SERIES = ["var(--a-series-1)", "var(--a-series-2)", "var(--a-series-3)"];

  function niceTicks(max) {
    if (max <= 0) return [0, 1];
    const raw = max / 3;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || mag * 10;
    const out = [];
    for (let v = 0; v <= max + step * 0.001; v += step) out.push(Math.round(v * 100) / 100);
    if (out.length < 2) out.push(step);
    return out;
  }

  /* One time-series chart. `series` is [{ label, values }] in fixed slot order;
     a single series gets an area wash and no legend (the title names it). */
  function lineChart(host, opt) {
    const days = opt.days || [];
    const series = (opt.series || []).filter(s => s && s.values && s.values.length);
    if (!days.length || !series.length || !series.some(s => sum(s.values) > 0)) {
      host.innerHTML = emptyHtml(opt.empty || "Not enough data yet.");
      return;
    }
    // The viewBox is sized to the host's ACTUAL pixel width, so the SVG renders
    // 1:1 and its 11px axis text really is 11px. A fixed 720-wide viewBox
    // stretched to a 1130px panel scaled every label up by 1.6×, which is what
    // made the charts shout over the numbers they were supporting.
    const W = Math.max(320, Math.round(host.clientWidth || 720));
    const H = opt.height || 230, PL = 44, PR = 54, PT = 14, PB = 26;
    const iw = W - PL - PR, ih = H - PT - PB;
    let max = 0;
    series.forEach(s => s.values.forEach(v => { if (Number(v) > max) max = Number(v); }));
    if (opt.compare) opt.compare.forEach(v => { if (Number(v) > max) max = Number(v); });
    const ticks = niceTicks(max);
    const top = ticks[ticks.length - 1] || 1;
    const x = (i) => PL + (days.length === 1 ? iw / 2 : (i / (days.length - 1)) * iw);
    const y = (v) => PT + ih - (Number(v) || 0) / top * ih;

    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opt.title || "chart")}" preserveAspectRatio="none">`;
    // Recessive gridlines + axis ticks carry every value not directly labelled.
    svg += `<g class="ccA-grid">` + ticks.map(t =>
      `<line x1="${PL}" y1="${y(t).toFixed(1)}" x2="${PL + iw}" y2="${y(t).toFixed(1)}"/>`).join("") + `</g>`;
    svg += `<g class="ccA-axis">` + ticks.map(t =>
      `<text x="${PL - 9}" y="${(y(t) + 4).toFixed(1)}" text-anchor="end">${fmt(t)}</text>`).join("") + `</g>`;

    // Date labels: one every `stride` days, plus the final day, but only when
    // there is room for it. Without the gap test the last two labels overlap
    // into an unreadable smudge whenever the range doesn't divide evenly.
    const MIN_LABEL_GAP = 58;
    const stride = Math.max(1, Math.ceil(days.length / 5));
    const shown = [];
    for (let i = 0; i < days.length; i += stride) shown.push(i);
    const last = days.length - 1;
    if (shown[shown.length - 1] !== last) {
      if (x(last) - x(shown[shown.length - 1]) < MIN_LABEL_GAP) shown.pop();
      shown.push(last);
    }
    svg += `<g class="ccA-axis">` + shown.map((i, n) => {
      const anchor = n === 0 ? "start" : (i === last ? "end" : "middle");
      return `<text x="${x(i).toFixed(1)}" y="${H - 6}" text-anchor="${anchor}">${esc(dayLabel(days[i]))}</text>`;
    }).join("") + `</g>`;
    svg += `<line class="ccA-baseline" x1="${PL}" y1="${PT + ih}" x2="${PL + iw}" y2="${PT + ih}"/>`;

    // The previous period rides as a neutral dashed ghost, it is a reference,
    // not a second identity, so it never takes a series colour.
    if (opt.compare && opt.compare.length) {
      const c = opt.compare;
      const pts = c.map((v, i) => `${x(Math.min(i, days.length - 1)).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
      svg += `<polyline class="ccA-ghost" points="${pts}"/>`;
    }

    series.forEach((s, si) => {
      const color = SERIES[si % SERIES.length];
      const pts = s.values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
      if (series.length === 1) {
        svg += `<polygon class="ccA-area" fill="${color}" points="${PL},${PT + ih} ${pts} ${PL + iw},${PT + ih}"/>`;
      }
      svg += `<polyline class="ccA-line" stroke="${color}" points="${pts}"/>`;
      const last = s.values.length - 1;
      svg += `<circle class="ccA-dot" cx="${x(last).toFixed(1)}" cy="${y(s.values[last]).toFixed(1)}" r="4.5" fill="${color}"/>`;
      // Label the endpoint only, a number on every point goes unread.
      svg += `<text class="ccA-endlabel" x="${(x(last) + 9).toFixed(1)}" y="${(y(s.values[last]) + 4).toFixed(1)}">${fmt(s.values[last])}</text>`;
    });

    svg += `<line class="ccA-cross" x1="0" y1="${PT}" x2="0" y2="${PT + ih}" style="display:none"/>`;
    svg += `<rect x="${PL}" y="${PT}" width="${iw}" height="${ih}" fill="transparent" data-hit="1"/>`;
    svg += `</svg><div class="ccA-tip"></div>`;
    host.innerHTML = svg;

    if (series.length > 1) {
      host.insertAdjacentHTML("beforeend", `<div class="ccA-legend">` + series.map((s, i) =>
        `<span class="ccA-legend-item"><span class="ccA-legend-key" style="background:${SERIES[i % SERIES.length]}"></span>${esc(s.label || "")}</span>`
      ).join("") + (opt.compare && opt.compare.length
        ? `<span class="ccA-legend-item"><span class="ccA-legend-key" style="background:#b9cbd9"></span>Previous period</span>` : "")
      + `</div>`);
    } else if (opt.compare && opt.compare.length) {
      host.insertAdjacentHTML("beforeend",
        `<div class="ccA-legend"><span class="ccA-legend-item"><span class="ccA-legend-key" style="background:${SERIES[0]}"></span>${esc(series[0].label || "This period")}</span>`
        + `<span class="ccA-legend-item"><span class="ccA-legend-key" style="background:#b9cbd9"></span>Previous period</span></div>`);
    }

    // Crosshair + tooltip. An SVG chart in a browser IS interactive; the hover
    // layer is what carries every value the endpoint label doesn't.
    const svgEl = host.querySelector("svg");
    const tip = host.querySelector(".ccA-tip");
    const cross = host.querySelector(".ccA-cross");
    const hit = host.querySelector("[data-hit]");
    function move(ev) {
      const box = svgEl.getBoundingClientRect();
      const px = ((ev.touches ? ev.touches[0].clientX : ev.clientX) - box.left) / box.width * W;
      let i = Math.round((px - PL) / (iw || 1) * (days.length - 1));
      i = Math.max(0, Math.min(days.length - 1, i));
      cross.style.display = "";
      cross.setAttribute("x1", x(i).toFixed(1));
      cross.setAttribute("x2", x(i).toFixed(1));
      tip.className = "ccA-tip on";
      tip.innerHTML = `<div class="ccA-tip-k">${esc(dayLabel(days[i]))}</div>`
        + series.map(s => `${esc(s.label || "")}: <b>${fmt(s.values[i])}</b>`).join("<br>");
      const left = Math.max(4, Math.min(host.clientWidth - tip.offsetWidth - 4,
        x(i) / W * host.clientWidth - tip.offsetWidth / 2));
      tip.style.left = left + "px";
      tip.style.top = "2px";
    }
    hit.addEventListener("mousemove", move);
    hit.addEventListener("touchmove", move, { passive: true });
    hit.addEventListener("mouseleave", () => { tip.className = "ccA-tip"; cross.style.display = "none"; });
  }

  /* Vertical columns for a small set of named buckets. Caps thickness at 24px
     so a 3-bucket chart never draws three slabs. */
  function columnChart(host, opt) {
    const items = (opt.items || []).filter(i => i && i.label != null);
    if (!items.length || !items.some(i => Number(i.value) > 0)) {
      host.innerHTML = emptyHtml(opt.empty || "Not enough data yet.");
      return;
    }
    const W = 720, H = opt.height || 210, PL = 44, PR = 12, PT = 14, PB = 34;
    const iw = W - PL - PR, ih = H - PT - PB;
    const max = Math.max(...items.map(i => Number(i.value) || 0));
    const ticks = niceTicks(max);
    const top = ticks[ticks.length - 1] || 1;
    const band = iw / items.length;
    const bw = Math.min(24, band * 0.55);
    const maxIdx = items.reduce((b, it, i) => (Number(it.value) > Number(items[b].value) ? i : b), 0);

    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opt.title || "chart")}" preserveAspectRatio="none">`;
    svg += `<g class="ccA-grid">` + ticks.map(t =>
      `<line x1="${PL}" y1="${(PT + ih - t / top * ih).toFixed(1)}" x2="${PL + iw}" y2="${(PT + ih - t / top * ih).toFixed(1)}"/>`).join("") + `</g>`;
    svg += `<g class="ccA-axis">` + ticks.map(t =>
      `<text x="${PL - 9}" y="${(PT + ih - t / top * ih + 4).toFixed(1)}" text-anchor="end">${fmt(t)}</text>`).join("") + `</g>`;

    items.forEach((it, i) => {
      const v = Number(it.value) || 0;
      const h = Math.max(v > 0 ? 2 : 0, v / top * ih);
      const cx = PL + band * i + band / 2;
      svg += `<rect class="ccA-bar" x="${(cx - bw / 2).toFixed(1)}" y="${(PT + ih - h).toFixed(1)}" `
           + `width="${bw.toFixed(1)}" height="${h.toFixed(1)}" fill="${SERIES[0]}"><title>${esc(it.label)}: ${fmt(v)}</title></rect>`;
      // Only the tallest column is labelled directly; the axis carries the rest.
      if (i === maxIdx && v > 0) {
        svg += `<text class="ccA-endlabel" x="${cx.toFixed(1)}" y="${(PT + ih - h - 6).toFixed(1)}" text-anchor="middle">${fmt(v)}</text>`;
      }
      svg += `<text class="ccA-axis-x" x="${cx.toFixed(1)}" y="${H - 12}" text-anchor="middle" `
           + `style="font-size:11px;fill:var(--a-muted);font-weight:600">${esc(shorten(it.label, 12))}</text>`;
    });
    svg += `<line class="ccA-baseline" x1="${PL}" y1="${PT + ih}" x2="${PL + iw}" y2="${PT + ih}"/></svg>`;
    host.innerHTML = svg;
  }

  /* A ranked bar list. Deliberately used everywhere a pie chart would be: a
     reader can compare lengths but not angles. */
  function rankList(host, items, opt) {
    opt = opt || {};
    const rows = (items || []).filter(i => i && i.label != null);
    if (!rows.length) { host.innerHTML = emptyHtml(opt.empty || "Not enough data yet."); return; }
    const max = Math.max(1, ...rows.map(r => Number(r.value) || 0));
    host.innerHTML = `<div class="ccA-rank">` + rows.map(r => {
      const v = Number(r.value) || 0;
      const color = r.tone === "warn" ? "var(--a-warn)" : r.tone === "bad" ? "var(--a-bad)"
                  : r.tone === "good" ? "var(--a-good)" : SERIES[0];
      return `<div class="ccA-rank-row">
        <div class="ccA-rank-lbl" title="${esc(r.label)}">${esc(r.label)}</div>
        <div class="ccA-rank-track"><div class="ccA-rank-fill" style="width:${(v / max * 100).toFixed(1)}%;background:${color}"></div></div>
        <div class="ccA-rank-val">${opt.suffix ? fmt(v) + opt.suffix : fmt(v)}</div>
      </div>`;
    }).join("") + `</div>`;
  }

  /* 12-point sparkline for a summary card. No axes, no labels, it shows shape
     only; the card's own number carries the value. */
  function sparkline(values, color) {
    const v = (values || []).slice(-12).map(n => Number(n) || 0);
    if (v.length < 2 || !v.some(n => n > 0)) return "";
    const W = 72, H = 24, max = Math.max(...v), min = Math.min(...v);
    const span = (max - min) || 1;
    const pts = v.map((n, i) => `${(i / (v.length - 1) * W).toFixed(1)},${(H - 2 - (n - min) / span * (H - 4)).toFixed(1)}`).join(" ");
    return `<svg class="ccA-card-spark" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">`
      + `<polyline fill="none" stroke="${color || SERIES[0]}" stroke-width="2" stroke-linecap="round" `
      + `stroke-linejoin="round" opacity=".55" points="${pts}"/></svg>`;
  }

  const shorten = (s, n) => { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
  const emptyHtml = (msg, ico) =>
    `<div class="ccA-empty"><span class="ccA-empty-ico">${ico || "〜"}</span>${esc(msg)}</div>`;

  // ══════════════════════════════════════════════════════════════════════════
  //  BUILDING BLOCKS
  // ══════════════════════════════════════════════════════════════════════════
  function cardHtml(c) {
    let tip = c.hint || DEFS[c.label] || "";
    const hasVal = c.value !== null && c.value !== undefined && c.value !== "";
    const delta = c.delta;
    let deltaHtml = "";
    if (delta !== null && delta !== undefined && isFinite(delta)) {
      const dir = delta > 0.05 ? "up" : delta < -0.05 ? "down" : "flat";
      const arrow = dir === "up" ? "▲" : dir === "down" ? "▼" : "•";
      // The arrow is never the only channel, the signed number says it too.
      // What the change is measured against goes in the tooltip rather than as
      // a second line of text wrapping inside a 212px card.
      deltaHtml = `<span class="ccA-delta ${dir}">${arrow} ${Math.abs(delta).toFixed(1)}%</span>`;
      tip = (tip ? tip + " " : "")
        + `Compared with the previous ${S.filters.days} days.`;
    }
    const toneChip = c.tone && c.tone !== "neutral"
      ? `<span class="ccA-chip ${esc(c.tone === "bad" ? "bad" : c.tone)}">${
          c.tone === "good" ? "Healthy" : c.tone === "warn" ? "Watch" : "Problem"}</span>`
      : "";
    return `<div class="ccA-card">
      <div class="ccA-card-lbl">${esc(c.label)}${tip ? `<span class="ccA-info" tabindex="0" data-tip="${esc(tip)}">i</span>` : ""}</div>
      <div class="ccA-card-val${hasVal ? "" : " ccA-empty"}">${
        hasVal ? esc(fmt(c.value)) + (c.unit ? `<span class="ccA-unit">${esc(c.unit)}</span>` : "")
               : "No data yet"}</div>
      <div class="ccA-card-foot">${deltaHtml || toneChip}${sparkline(c.spark)}</div>
    </div>`;
  }

  const cardsHtml = (cards) => `<div class="ccA-cards">${(cards || []).map(cardHtml).join("")}</div>`;

  function panelHtml(id, title, question, opts) {
    opts = opts || {};
    return `<div class="ccA-panel">
      <div class="ccA-panel-head">
        <div>
          <div class="ccA-panel-title">${esc(title)}${
            opts.tip ? `<span class="ccA-info" tabindex="0" data-tip="${esc(opts.tip)}">i</span>` : ""}</div>
          ${question ? `<div class="ccA-panel-q">${esc(question)}</div>` : ""}
        </div>
        <div class="ccA-spacer"></div>
        ${opts.controls || ""}
        ${opts.more ? `<button class="ccA-btn" data-more="${esc(opts.more)}">View details</button>` : ""}
      </div>
      <div class="ccA-chart" id="${esc(id)}"></div>
    </div>`;
  }

  function blockHtml(title, inner, opts) {
    opts = opts || {};
    return `<div class="ccA-block">
      <div class="ccA-block-head"><div class="ccA-h2">${esc(title)}</div><div class="ccA-spacer"></div>${opts.right || ""}</div>
      ${inner}
    </div>`;
  }

  /* A paginated table with a column menu: the useful columns are on by default,
     the rest are opt-in per section so no table ever opens 10 columns wide. */
  function tableHtml(sectionId, spec, opts) {
    opts = opts || {};
    const cols = (spec.columns || []).filter(c => c.always || (S.extraCols[sectionId] || {})[c.key]);
    const rows = spec.rows || [];
    if (!rows.length) return emptyHtml(opts.empty || "No rows for this date range.");
    const per = opts.perPage || 15;
    const page = Math.min(S.tablePage[sectionId] || 0, Math.max(0, Math.ceil(rows.length / per) - 1));
    const slice = rows.slice(page * per, page * per + per);
    const cell = (row, c) => {
      const v = row[c.key];
      if (c.key === "last_seen" || c.key === "joined" || c.key === "created" || c.key === "when") {
        return v ? esc(fmtWhen(v)) : "-";
      }
      if (c.key === "win_rate") return v == null ? "-" : esc(v + "%");
      if (typeof v === "number") return esc(fmt(v));
      return esc(v == null || v === "" ? "-" : v);
    };
    const isNum = (c) => !["name", "label", "mode"].includes(c.key);
    const optional = (spec.columns || []).filter(c => !c.always);
    return `<div class="ccA-table-wrap"><table class="ccA-table">
      <thead><tr>${cols.map(c => `<th class="${isNum(c) ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr></thead>
      <tbody>${slice.map(r => `<tr>${cols.map(c =>
        `<td class="${isNum(c) ? "num" : ""}">${cell(r, c)}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div>
    <div class="ccA-pager">
      <span>${rows.length.toLocaleString()} rows${rows.length > per ? ` · page ${page + 1} of ${Math.ceil(rows.length / per)}` : ""}</span>
      <span class="ccA-spacer"></span>
      ${optional.length ? `<button class="ccA-btn" data-cols="${esc(sectionId)}">Columns</button>` : ""}
      ${rows.length > per ? `<button class="ccA-btn" data-page="${esc(sectionId)}:${page - 1}" ${page === 0 ? "disabled" : ""}>Back</button>
      <button class="ccA-btn" data-page="${esc(sectionId)}:${page + 1}" ${(page + 1) * per >= rows.length ? "disabled" : ""}>Next</button>` : ""}
    </div>`;
  }

  const stepsHtml = (rows) => rows.length ? `<div class="ccA-steps">` + rows.map(r => `
    <div class="ccA-step">
      <div class="ccA-step-lbl">${esc(r.label)}</div>
      <div class="ccA-step-val">${esc(r.value)}</div>
      <div class="ccA-step-note">${esc(r.note || "")}</div>
      ${r.pct != null ? `<div class="ccA-step-bar"><span style="width:${Math.max(0, Math.min(100, r.pct)).toFixed(1)}%"></span></div>` : ""}
    </div>`).join("") + `</div>` : emptyHtml("Not enough data yet.");

  // ══════════════════════════════════════════════════════════════════════════
  //  SECTION RENDERERS
  // ══════════════════════════════════════════════════════════════════════════
  const RENDER = {};

  RENDER.overview = function (d, root) {
    root.innerHTML =
      headHtml("Overview", "How the game is doing right now.")
      + cardsHtml(d.cards)
      // The block names the topic; the panel names the MEASURE currently shown,
      // so the switcher visibly changes something and no title is said twice.
      + blockHtml("Player growth",
          panelHtml("ccA-c-growth", MEASURE[S.growthMeasure], "How quickly is the player base growing?", {
            controls: growthSwitchHtml(), more: "players",
          }))
      + blockHtml("Games played",
          panelHtml("ccA-c-games", "Games completed each day",
                    "How many games are being completed?", { more: "gameplay" }))
      + blockHtml("Retention", `<div class="ccA-panel">
          <div class="ccA-panel-head"><div>
            <div class="ccA-panel-title">Players who came back<span class="ccA-info" tabindex="0" data-tip="Only players who have had the chance to come back are counted, someone who joined yesterday can't have a 7-day return yet.">i</span></div>
            <div class="ccA-panel-q">Are players returning after joining?</div>
          </div><div class="ccA-spacer"></div>
          <button class="ccA-btn" data-more="players">View details</button></div>
          ${stepsHtml((d.retention || []).map(r => ({
            label: "After " + r.day + (r.day === 1 ? " day" : " days"),
            value: r.rate == null ? "-" : r.rate + "%",
            note: r.returned + " of " + r.cohort + " players",
            pct: r.rate,
          })))}
        </div>`)
      + blockHtml("Live activity and alerts", `<div class="ccA-grid-2">
          ${liveHtml(d.live || {})}
          ${alertsHtml(d.alerts || [], true)}
        </div>`);

    drawGrowth(d);
    lineChart($("#ccA-c-games", root), {
      days: (d.games || {}).days,
      title: "Games completed per day",
      series: [{ label: "Completed", values: (d.games || {}).completed || [] }],
      empty: "No games were played during this period.",
    });
  };

  // One measure per chart; the switcher swaps between them in place rather than
  // three near-identical charts sitting side by side.
  const MEASURE = {
    new: "New players each day",
    returning: "Returning players each day",
    cumulative: "Players in total",
  };
  const growthSwitchHtml = () => `<div class="ccA-switch" data-switch="growth">`
    + ["new", "returning", "cumulative"].map(m =>
        `<button data-m="${m}" class="${S.growthMeasure === m ? "on" : ""}">${
          { new: "New", returning: "Returning", cumulative: "Total" }[m]}</button>`).join("")
    + `</div>`;

  function drawGrowth(d, hostSel) {
    const host = $(hostSel || "#ccA-c-growth");
    if (!host) return;
    lineChart(host, growthOpts(d));
    const title = host.closest(".ccA-panel").querySelector(".ccA-panel-title");
    if (title) title.firstChild.nodeValue = MEASURE[S.growthMeasure];
  }

  function liveHtml(live) {
    const cell = (v, l) => `<div class="ccA-live-cell"><div class="ccA-live-val">${fmt(v)}</div><div class="ccA-live-lbl">${esc(l)}</div></div>`;
    const signups = (live.recent_signups || []).slice(0, 5);
    // The id is how refreshLive swaps ONLY this panel: replacing it by
    // position would silently start replacing whatever else the Overview grid
    // happens to hold first.
    return `<div class="ccA-panel" id="ccA-live-panel">
      <div class="ccA-panel-head"><div>
        <div class="ccA-panel-title"><span class="ccA-pulse" style="background:${live.server_ok ? "var(--a-good)" : "var(--a-bad)"}"></span>Right now</div>
        <div class="ccA-panel-q">${esc(live.server_ok ? "Server healthy" : live.server_note || "Server needs attention")}</div>
      </div></div>
      <div class="ccA-live">
        ${cell(live.online_players, "Players online")}
        ${cell(live.active_games, "Games running")}
        ${cell(live.matchmaking, "Matchmaking")}
        ${cell(live.open_lobbies, "Open lobbies")}
      </div>
      <div class="ccA-signups">
        ${signups.length ? signups.map(s =>
          `<div class="ccA-signup"><b>${esc(s.name)}</b><span>${esc(fmtAgo(s.ago))}</span></div>`).join("")
          : `<div class="ccA-signup"><span>No new players yet.</span></div>`}
      </div>
    </div>`;
  }

  function alertsHtml(alerts, capped) {
    const list = capped ? (alerts || []).slice(0, 3) : (alerts || []);
    return `<div class="ccA-panel">
      <div class="ccA-panel-head"><div>
        <div class="ccA-panel-title">Needs attention</div>
        <div class="ccA-panel-q">${capped ? "The three most important right now." : "Everything currently flagged."}</div>
      </div><div class="ccA-spacer"></div>
      ${capped ? `<button class="ccA-btn" data-more="technical">View all</button>` : ""}</div>
      ${list.length ? `<div class="ccA-alerts">` + list.map(a => `
        <div class="ccA-alert ${esc(a.level === "bad" ? "bad" : "warn")}">
          <span class="ccA-alert-ico">${a.level === "bad" ? "⛔" : "⚠️"}</span>
          <div><div class="ccA-alert-title">${esc(a.title)}</div>
          <div class="ccA-alert-detail">${esc(a.detail)}</div></div>
          ${a.section ? `<button class="ccA-btn ccA-alert-go" data-more="${esc(a.section)}">Open</button>` : ""}
        </div>`).join("") + `</div>`
        : emptyHtml("No problems were detected.", "✓")}
    </div>`;
  }

  RENDER.players = function (d, root) {
    root.innerHTML =
      headHtml("Players", "Who is joining, and who is staying.")
      + cardsHtml(d.cards)
      + blockHtml("Growth",
          panelHtml("ccA-p-growth", MEASURE[S.growthMeasure],
                    "How quickly is the player base growing?", { controls: growthSwitchHtml() }))
      + blockHtml("Coming back", `<div class="ccA-grid-2">
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">Players who came back</div>
              <div class="ccA-panel-q">Are players returning after joining?</div>
            </div></div>
            ${stepsHtml((d.retention || []).map(r => ({
              label: "After " + r.day + (r.day === 1 ? " day" : " days"),
              value: r.rate == null ? "-" : r.rate + "%",
              note: r.returned + " of " + r.cohort,
              pct: r.rate,
            })))}
          </div>
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">How far players get</div>
              <div class="ccA-panel-q">Where do new players stop?</div>
            </div></div>
            <div class="ccA-chart" id="ccA-p-funnel"></div>
          </div>
        </div>`)
      + blockHtml("Levels reached", `<div class="ccA-panel">
          <div class="ccA-panel-head"><div>
            <div class="ccA-panel-title">Levels reached</div>
            <div class="ccA-panel-q">How far along is the player base?</div>
          </div></div>
          <div class="ccA-chart" id="ccA-p-levels"></div>
        </div>`)
      + blockHtml("Most active players", `<div class="ccA-panel">${tableHtml("players", d.table || {})}</div>`);

    drawGrowth(d, "#ccA-p-growth");
    rankList($("#ccA-p-funnel", root), d.funnel || []);
    columnChart($("#ccA-p-levels", root), { items: d.levels || [], title: "Levels reached" });
  };

  function growthOpts(d) {
    const growth = d.growth || {};
    const gs = growth.series || {};
    const label = MEASURE[S.growthMeasure];
    return {
      days: growth.days,
      title: label,
      // The previous period is only comparable for a per-day count, a running
      // total against a running total says nothing.
      series: [{ label: label, values: gs[S.growthMeasure] || [] }],
      compare: (S.filters.compare && S.growthMeasure !== "cumulative") ? growth.compare : null,
    };
  }

  RENDER.gameplay = function (d, root) {
    root.innerHTML =
      headHtml("Gameplay", "What happens inside the games.")
      + cardsHtml(d.cards)
      + blockHtml("Games over time",
          panelHtml("ccA-g-vol", "Games played", "How many games are being completed?", {
            controls: `<div class="ccA-switch" data-switch="games">
              <button data-m="completed" class="on">Completed</button>
              <button data-m="left_early">Left early</button>
            </div>`,
          }))
      + blockHtml("How games are played", `<div class="ccA-grid-2">
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">Players per game</div>
              <div class="ccA-panel-q">What size do people actually play?</div>
            </div></div>
            <div class="ccA-chart" id="ccA-g-sizes"></div>
          </div>
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">Strategies played</div>
              <div class="ccA-panel-q">Which board is everyone building?</div>
            </div><div class="ccA-spacer"></div>
            <button class="ccA-btn" data-drawer="strategies">View details</button></div>
            <div class="ccA-chart" id="ccA-g-strats"></div>
          </div>
        </div>`)
      + blockHtml("Scores and length", `<div class="ccA-grid-2">
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">Final scores</div>
              <div class="ccA-panel-q">What does a normal score look like?</div>
            </div></div>
            <div class="ccA-chart" id="ccA-g-scores"></div>
          </div>
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">Game length</div>
              <div class="ccA-panel-q">How long does a game take?</div>
            </div></div>
            <div class="ccA-chart" id="ccA-g-len"></div>
          </div>
        </div>`);

    drawGameVolume(d, "completed");
    columnChart($("#ccA-g-sizes", root), { items: d.sizes || [], title: "Players per game" });
    rankList($("#ccA-g-strats", root), (d.strategies || []).slice(0, 6));
    columnChart($("#ccA-g-scores", root), { items: d.scores || [], title: "Final scores" });
    columnChart($("#ccA-g-len", root), { items: d.lengths || [], title: "Game length",
      empty: "No games have recorded a length yet." });
  };

  function drawGameVolume(d, measure) {
    const host = $("#ccA-g-vol");
    if (!host) return;
    const vol = d.volume || {};
    const label = measure === "left_early" ? "Games players left early" : "Games completed";
    lineChart(host, {
      days: vol.days,
      title: label,
      series: [{ label: label, values: vol[measure] || [] }],
      empty: "No games were played during this period.",
    });
  }

  RENDER.cards = function (d, root) {
    const review = d.review || [];
    root.innerHTML =
      headHtml("Cards", "Which animals get played, and which look out of balance.")
      + cardsHtml(d.cards)
      + blockHtml("Balance review", `<div class="ccA-panel">
          <div class="ccA-panel-head"><div>
            <div class="ccA-panel-title">Animals worth a look<span class="ccA-info" tabindex="0" data-tip="Win rate more than 12 points away from typical, counted only on animals that appeared on at least ${esc(d.min_sample || 20)} boards.">i</span></div>
            <div class="ccA-panel-q">Which cards may need a balance review?</div>
          </div><div class="ccA-spacer"></div>
          <button class="ccA-btn" data-drawer="sample">Sample size</button></div>
          ${review.length ? `<div class="ccA-rank">` + review.slice(0, 8).map(r => {
            // The direction is spelled out, not left to the bar's colour, a
            // reader who can't tell amber from teal still gets the answer.
            const strong = r.direction === "strong";
            return `<div class="ccA-rank-row">
              <div class="ccA-rank-lbl" title="${esc(r.name)}">${esc(r.name)}
                <span class="ccA-chip ${strong ? "warn" : "neutral"}">${strong ? "Too strong" : "Too weak"}</span>
              </div>
              <div class="ccA-rank-track"><div class="ccA-rank-fill" style="width:${Math.min(100, r.win_rate).toFixed(1)}%;background:${strong ? "var(--a-warn)" : "var(--a-series-3)"}"></div></div>
              <div class="ccA-rank-val">${esc(r.win_rate)}%</div>
            </div>`;
          }).join("") + `</div>
            <div class="ccA-panel-q" style="margin-top:12px">${review.length} ${review.length === 1 ? "animal sits" : "animals sit"} more than 12 points from the typical win rate of ${esc((d.cards.find(c => c.label === "Typical win rate") || {}).value ?? "-")}%.</div>`
            : emptyHtml("No animals look out of balance right now.", "✓")}
        </div>`)
      + blockHtml("What gets played", `<div class="ccA-grid-2">
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">Most played animals</div>
              <div class="ccA-panel-q">What ends up on boards most often?</div>
            </div></div>
            <div class="ccA-chart" id="ccA-cd-played"></div>
          </div>
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">Families played</div>
              <div class="ccA-panel-q">Which families dominate?</div>
            </div></div>
            <div class="ccA-chart" id="ccA-cd-species"></div>
          </div>
        </div>`)
      + blockHtml("Every animal", `<div class="ccA-panel">${tableHtml("cards", d.table || {}, { perPage: 20 })}</div>`);

    rankList($("#ccA-cd-played", root), (d.most_played || []).slice(0, 8));
    rankList($("#ccA-cd-species", root), (d.species || []).slice(0, 8));
  };

  RENDER.competitive = function (d, root) {
    root.innerHTML =
      headHtml("Competitive", "Ranked matches and how they end.")
      + cardsHtml(d.cards)
      + blockHtml("Matches over time",
          panelHtml("ccA-comp-vol", "Ranked matches", "How much competitive play is happening?"))
      + blockHtml("How matches end", `<div class="ccA-grid-2">
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">Match endings</div>
              <div class="ccA-panel-q">Do matches get played to the end?</div>
            </div></div>
            <div class="ccA-chart" id="ccA-comp-out"></div>
          </div>
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">Most active competitors</div>
              <div class="ccA-panel-q">Who is playing ranked?</div>
            </div></div>
            <div class="ccA-chart" id="ccA-comp-top"></div>
          </div>
        </div>`)
      + blockHtml("Competitive players", `<div class="ccA-panel">${tableHtml("competitive", d.table || {})}</div>`);

    const vol = d.volume || {};
    lineChart($("#ccA-comp-vol", root), {
      days: vol.days,
      title: "Ranked matches",
      series: [{ label: "Matches", values: vol.matches || [] },
               { label: "Given up", values: vol.forfeits || [] }],
      empty: "No ranked matches during this period.",
    });
    rankList($("#ccA-comp-out", root), d.outcomes || []);
    rankList($("#ccA-comp-top", root),
      (d.table && d.table.rows || []).slice(0, 8).map(r => ({ label: r.name, value: r.matches })));
  };

  RENDER.clans = function (d, root) {
    root.innerHTML =
      headHtml("Clans", "How the clan season is going.")
      + cardsHtml(d.cards)
      + blockHtml("This season", `<div class="ccA-grid-2">
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">Top clans</div>
              <div class="ccA-panel-q">Who is winning the season?</div>
            </div></div>
            <div class="ccA-chart" id="ccA-cl-top"></div>
          </div>
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">Clan sizes</div>
              <div class="ccA-panel-q">Are clans filling up?</div>
            </div></div>
            <div class="ccA-chart" id="ccA-cl-sizes"></div>
          </div>
        </div>`)
      + blockHtml("Every clan", `<div class="ccA-panel">${tableHtml("clans", d.table || {})}</div>`);

    rankList($("#ccA-cl-top", root), d.top || [], { empty: "No clans have scored yet." });
    columnChart($("#ccA-cl-sizes", root), { items: d.sizes || [], title: "Clan sizes" });
  };

  RENDER.economy = function (d, root) {
    const rev = d.revenue || {};
    root.innerHTML =
      headHtml("Economy", "Critter Coins and support.")
      + cardsHtml(d.cards)
      + blockHtml("Purchases",
          panelHtml("ccA-ec-rev", "Purchases", "How often does anyone buy something?", {
            tip: "Completed Stripe payments recorded in this date range.",
          }))
      + blockHtml("Where the coins are", `<div class="ccA-grid-2">
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">Coin balances</div>
              <div class="ccA-panel-q">How much is everyone holding?</div>
            </div></div>
            <div class="ccA-chart" id="ccA-ec-bal"></div>
          </div>
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">Biggest balances</div>
              <div class="ccA-panel-q">Who has the most coins?</div>
            </div></div>
            <div class="ccA-chart" id="ccA-ec-top"></div>
          </div>
        </div>`);

    lineChart($("#ccA-ec-rev", root), {
      days: rev.days,
      title: "Purchases",
      series: [{ label: "Purchases", values: rev.series || [] }],
      empty: "No purchases during this period.",
    });
    columnChart($("#ccA-ec-bal", root), { items: d.balances || [], title: "Coin balances" });
    rankList($("#ccA-ec-top", root), d.top_holders || []);
  };

  RENDER.events = function (d, root) {
    const tr = d.trades || {};
    root.innerHTML =
      headHtml("Events", "Trading, team games and unlocks.")
      + cardsHtml(d.cards)
      + blockHtml("Trading",
          panelHtml("ccA-ev-trades", "Trades completed", "Are players trading with each other?"))
      + blockHtml("Team games", `<div class="ccA-panel">
          <div class="ccA-panel-head"><div>
            <div class="ccA-panel-title">Team sizes played</div>
            <div class="ccA-panel-q">How are people setting up team games?</div>
          </div></div>
          <div class="ccA-chart" id="ccA-ev-teams"></div>
        </div>`);

    lineChart($("#ccA-ev-trades", root), {
      days: tr.days,
      title: "Trades completed",
      series: [{ label: "Trades", values: tr.series || [] }],
      empty: "No trades during this period.",
    });
    columnChart($("#ccA-ev-teams", root), { items: d.team_sizes || [], title: "Team sizes",
      empty: "No team games during this period." });
  };

  RENDER.technical = function (d, root) {
    const load = d.load || {};
    root.innerHTML =
      headHtml("Technical Health", "Whether the game is working properly.")
      + cardsHtml(d.cards)
      + blockHtml("Checks", `<div class="ccA-panel">
          <div class="ccA-panel-head"><div>
            <div class="ccA-panel-title">System checks</div>
            <div class="ccA-panel-q">Is anything failing right now?</div>
          </div></div>
          <div class="ccA-alerts">${(d.checks || []).map(c => `
            <div class="ccA-alert ${c.ok ? "" : "warn"}">
              <span class="ccA-alert-ico">${c.ok ? "✓" : "⚠️"}</span>
              <div><div class="ccA-alert-title">${esc(c.label)}</div>
              <div class="ccA-alert-detail">${esc(c.detail)}</div></div>
              <span class="ccA-chip ${c.ok ? "good" : "warn"} ccA-alert-go">${c.ok ? "OK" : "Watch"}</span>
            </div>`).join("")}</div>
        </div>`)
      + blockHtml("Games that ended badly",
          panelHtml("ccA-t-trunc", "Games that ended badly", "Are games failing to finish?", {
            tip: "Games saved without a final score, everyone left, or the room errored.",
          }))
      + blockHtml("All alerts", alertsHtml(d.alerts || [], false))
      + blockHtml("Server load", `<div class="ccA-panel">
          <div class="ccA-facts">
            <div class="ccA-fact"><div class="ccA-fact-lbl">Rooms in memory</div><div class="ccA-fact-val">${fmt(load.rooms)}</div></div>
            <div class="ccA-fact"><div class="ccA-fact-lbl">Threads</div><div class="ccA-fact-val">${fmt(load.threads)}</div></div>
            <div class="ccA-fact"><div class="ccA-fact-lbl">Bot planning slots</div><div class="ccA-fact-val">${fmt(load.deep_plan_slots)}</div></div>
            <div class="ccA-fact"><div class="ccA-fact-lbl">Deep plans granted</div><div class="ccA-fact-val">${fmt(load.deep_plan_granted)}</div></div>
            <div class="ccA-fact"><div class="ccA-fact-lbl">Deep plans skipped</div><div class="ccA-fact-val">${fmt(load.deep_plan_skipped)}</div></div>
          </div>
        </div>`);

    const t = d.truncated || {};
    lineChart($("#ccA-t-trunc", root), {
      days: t.days,
      title: "Games that ended badly",
      series: [{ label: "Ended badly", values: t.series || [] }],
      empty: "No technical problems were detected.",
    });
  };

  RENDER.search = function (d, root) {
    const p = d.player;
    root.innerHTML =
      headHtml("Player Search", "Look up one player by name or friend code.")
      + `<div class="ccA-search-row">
          <input class="ccA-input" id="ccA-q" placeholder="Player name or friend code…" value="${esc(S.searchQuery)}" autocomplete="off">
          <button class="ccA-btn primary" id="ccA-go">Search</button>
        </div>`
      + (!d.query ? emptyHtml("Type a name or friend code to begin.", "⌕")
        : !(d.matches || []).length ? emptyHtml("No player matched that search.")
        : `${(d.matches || []).length > 1 ? blockHtml("Matches", `<div class="ccA-panel">${tableHtml("search", {
              columns: [
                { key: "name", label: "Player", always: true },
                { key: "friend_code", label: "Code", always: true },
                { key: "games", label: "Games", always: true },
                { key: "level", label: "Level", always: true },
                { key: "last_seen", label: "Last seen", always: true },
              ], rows: d.matches,
            })}</div>`) : ""}
          ${p ? blockHtml(p.name, `<div class="ccA-panel">
            <div class="ccA-facts">
              <div class="ccA-fact"><div class="ccA-fact-lbl">Friend code</div><div class="ccA-fact-val">${esc(p.friend_code || "-")}</div></div>
              <div class="ccA-fact"><div class="ccA-fact-lbl">Joined</div><div class="ccA-fact-val">${esc(fmtWhen(p.joined))}</div></div>
              <div class="ccA-fact"><div class="ccA-fact-lbl">Last seen</div><div class="ccA-fact-val">${esc(p.online ? "Online now" : fmtAgo(Math.floor(Date.now() / 1000) - p.last_seen))}</div></div>
              <div class="ccA-fact"><div class="ccA-fact-lbl">Games</div><div class="ccA-fact-val">${fmt(p.games)}</div></div>
              <div class="ccA-fact"><div class="ccA-fact-lbl">Wins</div><div class="ccA-fact-val">${fmt(p.wins)}</div></div>
              <div class="ccA-fact"><div class="ccA-fact-lbl">Level</div><div class="ccA-fact-val">${fmt(p.level)}</div></div>
              <div class="ccA-fact"><div class="ccA-fact-lbl">Best score</div><div class="ccA-fact-val">${fmt(p.highest_score)}</div></div>
              <div class="ccA-fact"><div class="ccA-fact-lbl">Coins</div><div class="ccA-fact-val">${fmt(p.coins)}</div></div>
              <div class="ccA-fact"><div class="ccA-fact-lbl">Critters</div><div class="ccA-fact-val">${fmt(p.icons)}</div></div>
              <div class="ccA-fact"><div class="ccA-fact-lbl">Prestige</div><div class="ccA-fact-val">${fmt(p.prestige)}</div></div>
            </div>
          </div>`) : ""}
          ${p && (p.recent || []).length ? blockHtml("Recent games", `<div class="ccA-panel">${tableHtml("recent", {
            columns: [
              { key: "when", label: "When", always: true },
              { key: "mode", label: "Mode", always: true },
              { key: "players", label: "Players", always: true },
              { key: "score", label: "Score", always: true },
              { key: "result", label: "Result", always: true },
            ],
            rows: (p.recent || []).map(g => ({
              when: g.when, mode: g.mode, players: g.players, score: g.score,
              result: !g.finished ? "Left early" : g.won ? "Won" : "Lost",
            })),
          })}</div>`) : ""}`);

    const go = () => {
      S.searchQuery = ($("#ccA-q") || {}).value || "";
      load("search", true);
    };
    const input = $("#ccA-q", root);
    if (input) {
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      if (d.query) input.focus();
    }
    const btn = $("#ccA-go", root);
    if (btn) btn.addEventListener("click", go);
  };

  const headHtml = (title, sub) =>
    `<div class="ccA-section-head"><div class="ccA-h1">${esc(title)}</div><div class="ccA-h1-sub">${esc(sub)}</div></div>`;

  // ══════════════════════════════════════════════════════════════════════════
  //  SHELL
  // ══════════════════════════════════════════════════════════════════════════
  let overlay = null;

  function build() {
    if (overlay) return overlay;
    overlay = document.createElement("div");
    overlay.id = "ccA-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "Developer Analytics");
    overlay.innerHTML = `
      <div class="ccA-head">
        <div class="ccA-brand">
          <span class="ccA-title">≈ Analytics ≈</span>
          <span class="ccA-sub" id="ccA-updated"></span>
        </div>
        <div class="ccA-head-spacer"></div>
        <div class="ccA-filters">
          <select class="ccA-ctl" id="ccA-range" aria-label="Date range">
            ${RANGES.map(r => `<option value="${r.days}">${r.label}</option>`).join("")}
          </select>
          <button class="ccA-ctl" id="ccA-compare" aria-pressed="false">Compare</button>
          <button class="ccA-ctl" id="ccA-refresh" title="Reload this section">↻ Refresh</button>
          <button class="ccA-ctl" id="ccA-export" title="Download everything as JSON">Export</button>
          <button class="ccA-ctl ccA-adv-toggle" id="ccA-adv-btn">Filters <span class="ccA-caret">▾</span></button>
          <button class="ccA-ctl" id="ccA-help">?</button>
        </div>
        <button id="ccA-close" title="Close (Esc)" aria-label="Close analytics">✕</button>
      </div>
      <div class="ccA-adv" id="ccA-adv">
        <label><input type="checkbox" data-f="include_test"> Include test accounts</label>
        <label><input type="checkbox" data-f="include_guests"> Include guest players</label>
        <label><input type="checkbox" data-f="include_bots"> Include games against bots</label>
        <label>Game mode
          <select class="ccA-ctl" data-f="mode" style="height:30px">
            <option value="all">All</option><option value="casual">Casual</option>
            <option value="competitive">Competitive</option><option value="team">Team</option>
          </select>
        </label>
        <label>Players
          <select class="ccA-ctl" data-f="player_count" style="height:30px">
            <option value="0">Any</option><option value="2">2</option><option value="3">3</option>
            <option value="4">4</option><option value="5">5</option><option value="6">6</option>
            <option value="7">7</option><option value="8">8</option>
          </select>
        </label>
        <div class="ccA-adv-note">Filters apply to every section. Turning on test accounts includes the developer account in the player numbers.</div>
      </div>
      <div class="ccA-nav-mobile">
        <select class="ccA-ctl" id="ccA-nav-sel" aria-label="Section">
          ${SECTIONS.map(s => `<option value="${s.id}">${s.name}</option>`).join("")}
        </select>
      </div>
      <div class="ccA-main">
        <nav class="ccA-nav" id="ccA-nav">
          ${SECTIONS.map((s, i) => (i === 9 ? `<div class="ccA-nav-sep"></div>` : "")
            + `<button class="ccA-nav-btn${s.id === "overview" ? " active" : ""}" data-sec="${s.id}">
                 <span class="ccA-nav-ico" aria-hidden="true">${s.ico}</span>${s.name}</button>`).join("")}
        </nav>
        <div class="ccA-body"><div class="ccA-inner" id="ccA-inner"></div></div>
      </div>`;
    document.body.appendChild(overlay);
    wire();
    return overlay;
  }

  function wire() {
    $("#ccA-close", overlay).addEventListener("click", close);
    $("#ccA-range", overlay).addEventListener("change", (e) => {
      S.filters.days = Number(e.target.value) || 30;
      invalidate();
    });
    $("#ccA-compare", overlay).addEventListener("click", (e) => {
      S.filters.compare = !S.filters.compare;
      e.currentTarget.classList.toggle("on", S.filters.compare);
      e.currentTarget.setAttribute("aria-pressed", String(S.filters.compare));
      invalidate();
    });
    $("#ccA-refresh", overlay).addEventListener("click", () => load(S.section, true));
    $("#ccA-export", overlay).addEventListener("click", exportAll);
    $("#ccA-help", overlay).addEventListener("click", openHelp);
    $("#ccA-adv-btn", overlay).addEventListener("click", (e) => {
      S.advOpen = !S.advOpen;
      $("#ccA-adv", overlay).classList.toggle("open", S.advOpen);
      e.currentTarget.classList.toggle("open", S.advOpen);
    });
    $$("[data-f]", $("#ccA-adv", overlay)).forEach(el => {
      el.addEventListener("change", () => {
        const key = el.getAttribute("data-f");
        S.filters[key] = el.type === "checkbox" ? el.checked : (
          key === "player_count" ? Number(el.value) || 0 : el.value);
        invalidate();
      });
    });
    $$(".ccA-nav-btn", overlay).forEach(b =>
      b.addEventListener("click", () => go(b.getAttribute("data-sec"))));
    $("#ccA-nav-sel", overlay).addEventListener("change", (e) => go(e.target.value));

    // One delegated listener for everything the sections render.
    $("#ccA-inner", overlay).addEventListener("click", (e) => {
      const more = e.target.closest("[data-more]");
      if (more) { go(more.getAttribute("data-more")); return; }
      const sw = e.target.closest("[data-switch] button");
      if (sw) { onSwitch(sw); return; }
      const page = e.target.closest("[data-page]");
      if (page) {
        const [sec, n] = page.getAttribute("data-page").split(":");
        S.tablePage[sec] = Math.max(0, Number(n) || 0);
        render();
        return;
      }
      const cols = e.target.closest("[data-cols]");
      if (cols) { openColumns(cols.getAttribute("data-cols")); return; }
      const dr = e.target.closest("[data-drawer]");
      if (dr) { openInfoDrawer(dr.getAttribute("data-drawer")); return; }
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && S.open) {
        if ($(".ccA-drawer-back")) { closeDrawer(); return; }
        close();
      }
    });
  }

  function onSwitch(btn) {
    const group = btn.closest("[data-switch]").getAttribute("data-switch");
    btn.parentNode.querySelectorAll("button").forEach(b => b.classList.remove("on"));
    btn.classList.add("on");
    const m = btn.getAttribute("data-m");
    if (group === "growth") {
      S.growthMeasure = m;
      const d = S.data[S.section];
      if (!d) return;
      // Redraw the ONE chart rather than the page, the switcher exists so the
      // dashboard doesn't need three near-identical charts side by side.
      drawGrowth(d, S.section === "overview" ? "#ccA-c-growth" : "#ccA-p-growth");
    } else if (group === "games") {
      drawGameVolume(S.data[S.section] || {}, m);
    }
  }

  function go(section) {
    if (!SECTIONS.some(s => s.id === section)) return;
    S.section = section;
    S.seq++;
    $$(".ccA-nav-btn", overlay).forEach(b =>
      b.classList.toggle("active", b.getAttribute("data-sec") === section));
    const sel = $("#ccA-nav-sel", overlay);
    if (sel) sel.value = section;
    $(".ccA-body", overlay).scrollTop = 0;
    load(section);
  }

  /* Drop every cached section, the filters changed, so every number is stale.
     Only the visible one is re-fetched; the rest reload when opened. */
  function invalidate() {
    S.data = {};
    S.tablePage = {};
    load(S.section, true);
  }

  // ══════════════════════════════════════════════════════════════════════════
  //  LOAD + RENDER
  // ══════════════════════════════════════════════════════════════════════════
  async function load(section, force) {
    const seq = ++S.seq;
    if (!force && S.data[section]) { render(); return; }
    S.loading[section] = true;
    S.error[section] = "";
    render();
    const extra = section === "search" ? { query: S.searchQuery } : null;
    const res = await post(section, extra);
    if (seq !== S.seq) return;                 // navigated away mid-flight
    S.loading[section] = false;
    if (res === null) {
      S.error[section] = "Couldn't reach the server. Check your connection and hit Refresh.";
    } else if (!res || !res.ok) {
      S.error[section] = errMsg(res && res.error);
    } else {
      S.data[section] = res;
      const el = $("#ccA-updated", overlay);
      if (el) el.textContent = "Updated " + new Date().toLocaleTimeString(undefined,
        { hour: "numeric", minute: "2-digit" });
    }
    render();
  }

  function render() {
    const root = $("#ccA-inner", overlay);
    if (!root) return;
    const sec = S.section;
    if (S.loading[sec] && !S.data[sec]) { root.innerHTML = skeleton(sec); return; }
    if (S.error[sec] && !S.data[sec]) {
      root.innerHTML = headHtml(nameOf(sec), "") + `<div class="ccA-panel">${emptyHtml(S.error[sec], "⚠️")}</div>`;
      return;
    }
    const data = S.data[sec];
    if (!data) { root.innerHTML = skeleton(sec); return; }
    // A section that throws must not blank the dashboard, that failure mode is
    // silent (no console error reaches the developer looking at an empty page).
    try {
      (RENDER[sec] || RENDER.overview)(data, root);
    } catch (err) {
      root.innerHTML = headHtml(nameOf(sec), "")
        + `<div class="ccA-panel">${emptyHtml("This section hit an error: " + (err && err.message || err), "⚠️")}</div>`;
      try { console.error("[analytics] render " + sec + " failed:", err); } catch (_) {}
    }
  }

  const nameOf = (id) => (SECTIONS.find(s => s.id === id) || {}).name || "Analytics";

  /* Flat placeholders in the shapes that are coming, no spinners anywhere. */
  function skeleton(sec) {
    const cards = sec === "search" ? 0 : 4;
    return headHtml(nameOf(sec), "")
      + (cards ? `<div class="ccA-cards">${Array(cards).fill(`<div class="ccA-skel ccA-skel-card"></div>`).join("")}</div>` : "")
      + `<div class="ccA-block"><div class="ccA-skel ccA-skel-panel"></div></div>
         <div class="ccA-block"><div class="ccA-skel ccA-skel-panel" style="height:200px"></div></div>`;
  }

  // ══════════════════════════════════════════════════════════════════════════
  //  DRAWER (where every "View details" goes)
  // ══════════════════════════════════════════════════════════════════════════
  function openDrawer(title, html) {
    closeDrawer();
    const back = document.createElement("div");
    back.className = "ccA-drawer-back";
    back.innerHTML = `<div class="ccA-drawer">
      <div class="ccA-drawer-head">
        <div class="ccA-drawer-title">${esc(title)}</div>
        <button class="ccA-btn" data-close-drawer>Close</button>
      </div>
      <div class="ccA-drawer-body">${html}</div>
    </div>`;
    (overlay || document.body).appendChild(back);
    requestAnimationFrame(() => back.classList.add("on"));
    back.addEventListener("click", (e) => {
      if (e.target === back || e.target.closest("[data-close-drawer]")) closeDrawer();
    });
  }
  function closeDrawer() {
    const back = $(".ccA-drawer-back", overlay || document);
    if (back) back.remove();
  }

  function openHelp() {
    const rows = Object.keys(DEFS).map(k =>
      `<div class="ccA-alert"><div><div class="ccA-alert-title">${esc(k)}</div>
       <div class="ccA-alert-detail">${esc(DEFS[k])}</div></div></div>`).join("");
    openDrawer("What the numbers mean", `
      <div class="ccA-panel">
        <div class="ccA-panel-title">Reading this dashboard</div>
        <div class="ccA-panel-q" style="margin-top:6px">Everything is measured over the date range in the header. A dash means there
        isn't enough data yet, never zero. Rates are left blank rather than shown as 0% when nothing has happened to divide by.</div>
      </div>
      <div class="ccA-block"><div class="ccA-h2">Definitions</div>
        <div class="ccA-alerts">${rows}</div>
      </div>`);
  }

  function openInfoDrawer(kind) {
    if (kind === "sample") {
      const d = S.data.cards || {};
      openDrawer("Sample size", `
        <div class="ccA-panel">
          <div class="ccA-panel-title">Why some animals are left out</div>
          <div class="ccA-panel-q" style="margin-top:6px">A win rate needs enough boards behind it to mean anything. Animals that
          appeared on fewer than ${esc(d.min_sample || 20)} boards in this range are still listed in the table, but they are never
          flagged for balance review, with a handful of games, one lucky win swings the rate by tens of points.</div>
        </div>
        <div class="ccA-block"><div class="ccA-h2">Flagged animals</div>
          ${(d.review || []).length ? `<div class="ccA-panel">${tableHtml("review", {
            columns: [
              { key: "name", label: "Animal", always: true },
              { key: "boards", label: "Boards", always: true },
              { key: "win_rate", label: "Win rate", always: true },
              { key: "gap", label: "From typical", always: true },
            ], rows: d.review,
          }, { perPage: 20 })}</div>` : emptyHtml("No animals look out of balance right now.", "✓")}
        </div>`);
      return;
    }
    if (kind === "strategies") {
      const d = S.data.gameplay || {};
      openDrawer("Strategies played", `<div class="ccA-panel">${tableHtml("strats", {
        columns: [{ key: "label", label: "Strategy", always: true },
                  { key: "value", label: "Boards", always: true }],
        rows: d.strategies || [],
      }, { perPage: 20 })}</div>`);
    }
  }

  function openColumns(sectionId) {
    const spec = (S.data[S.section] || {}).table || {};
    const optional = (spec.columns || []).filter(c => !c.always);
    openDrawer("Columns", `<div class="ccA-panel">
      <div class="ccA-panel-q" style="margin-bottom:10px">The most useful columns are always on. Add the rest here.</div>
      ${optional.map(c => `<label style="display:flex;gap:9px;align-items:center;padding:7px 0;font-size:.87rem">
        <input type="checkbox" data-col="${esc(c.key)}" ${(S.extraCols[sectionId] || {})[c.key] ? "checked" : ""}
          style="width:16px;height:16px;accent-color:var(--a-series-1)"> ${esc(c.label)}</label>`).join("")}
    </div>`);
    $$("[data-col]", $(".ccA-drawer-back", overlay)).forEach(el => {
      el.addEventListener("change", () => {
        S.extraCols[sectionId] = S.extraCols[sectionId] || {};
        S.extraCols[sectionId][el.getAttribute("data-col")] = el.checked;
        render();
      });
    });
  }

  async function exportAll() {
    const btn = $("#ccA-export", overlay);
    const old = btn.textContent;
    btn.textContent = "Exporting…";
    btn.disabled = true;
    const res = await post("export");
    btn.textContent = old;
    btn.disabled = false;
    if (!res || !res.ok) {
      openDrawer("Export", `<div class="ccA-panel">${emptyHtml(errMsg(res && res.error), "⚠️")}</div>`);
      return;
    }
    try {
      const blob = new Blob([JSON.stringify(res, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "critters-analytics-" + new Date().toISOString().slice(0, 10) + ".json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    } catch (_) {
      openDrawer("Export", `<div class="ccA-panel">${emptyHtml("This browser blocked the download.", "⚠️")}</div>`);
    }
  }

  // ══════════════════════════════════════════════════════════════════════════
  //  OPEN / CLOSE
  // ══════════════════════════════════════════════════════════════════════════
  function isAdmin() {
    try {
      const b = bridge();
      return !!(b && b.isAdmin && b.isAdmin());
    } catch (_) { return false; }
  }

  function open(section) {
    if (!isAdmin()) return;
    build();
    S.open = true;
    overlay.classList.add("open");
    document.body.style.overflow = "hidden";
    $("#ccA-range", overlay).value = String(S.filters.days);
    go(section && SECTIONS.some(s => s.id === section) ? section : S.section);
    // The live panel refreshes on its own clock, in place. It re-renders only
    // the four numbers and the sign-up list, so nothing under the reader's
    // cursor moves and the page never jumps.
    clearInterval(S.liveTimer);
    S.liveTimer = setInterval(refreshLive, 20000);
  }

  async function refreshLive() {
    if (!S.open || S.section !== "overview") return;
    // The overview costs a full scan of the users collection on the server
    // (analytics_server._load_users, behind a 120s cache), so a dashboard left
    // open in a background tab quietly rescans every account all day for
    // nobody. The panel is re-rendered on the next tick after it is looked at
    // again, which is the only time its numbers are read. See the free-tier
    // read allowance this helped exhaust on 2026-09-04.
    if (document.hidden) return;
    const res = await post("overview");
    if (!res || !res.ok || S.section !== "overview") return;
    S.data.overview = res;
    const host = $("#ccA-live-panel", overlay);
    if (!host) return;
    const wrap = document.createElement("div");
    wrap.innerHTML = liveHtml(res.live || {});
    const fresh = wrap.firstElementChild;
    if (fresh && host.parentNode) host.parentNode.replaceChild(fresh, host);
  }

  function close() {
    if (!overlay) return;
    closeDrawer();
    S.open = false;
    overlay.classList.remove("open");
    document.body.style.overflow = "";
    clearInterval(S.liveTimer);
    S.liveTimer = null;
  }

  // ══════════════════════════════════════════════════════════════════════════
  //  ENTRY POINTS
  // ══════════════════════════════════════════════════════════════════════════
  window.__ccAnalyticsOpen = open;
  window.__ccAnalyticsClose = close;
  window.__ccAnalyticsIsOpen = () => S.open;
  // Exposed for the tests, which drive the real renderers against real payloads.
  window.__ccAnalyticsInternals = { S, RENDER, lineChart, columnChart, rankList, cardHtml, tableHtml, fmt, SECTIONS };

  // A signed-out (or non-admin) session must never keep the panel on screen.
  setInterval(() => { if (S.open && !isAdmin()) close(); }, 2000);

  // Charts are drawn at the panel's real pixel width, so a resized window has
  // to redraw them or the axis text scales with the stretch. Debounced, and
  // straight from the cached payload, no request, no skeleton flash.
  let _resizeTimer = null;
  window.addEventListener("resize", () => {
    if (!S.open) return;
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(() => { if (S.open && S.data[S.section]) render(); }, 180);
  });
})();
