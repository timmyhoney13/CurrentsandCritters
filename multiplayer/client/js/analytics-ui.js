/* Currents and Critters: Developer Analytics dashboard (admin only).
 *
 * A full-screen overlay opened from the Player Home, closed with the ✕ in its
 * header (or Escape). Every number comes from the server-authoritative
 * /api/analytics/* API through the window.__ccAnalytics bridge; this file owns
 * no maths beyond formatting, and no player data ever reaches it un-aggregated.
 *
 * WHERE THE NUMBERS COME FROM
 * The accounts: each one's lifetime counters, its last 50 finished games and
 * the dates it played (see analytics_server.py). A "game played" is one player
 * finishing one game, the unit the homepage counts. The range control switches
 * between the last 7, 30 or 90 days and Lifetime; Lifetime has no previous
 * period, so Compare switches itself off there.
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
  function fmtSpan(sec) {
    const s = Math.max(0, Math.floor(Number(sec) || 0));
    if (s < 60) return "a moment";
    if (s < 3600) return Math.floor(s / 60) + " min";
    if (s < 86400) return Math.floor(s / 3600) + " h";
    return Math.floor(s / 86400) + " days";
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
      const d = new Date(unix * 1000);
      const sameYear = d.getFullYear() === new Date().getFullYear();
      return d.toLocaleDateString(undefined, sameYear
        ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
    } catch (_) { return "-"; }
  }
  function fmtDateTime(unix) {
    if (!unix) return "-";
    try {
      return fmtWhen(unix) + ", " + new Date(unix * 1000).toLocaleTimeString(undefined,
        { hour: "numeric", minute: "2-digit" });
    } catch (_) { return "-"; }
  }
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MONTH = ["January", "February", "March", "April", "May", "June", "July", "August",
                 "September", "October", "November", "December"];
  // Chart keys are the date a bucket starts on. A month-wide bucket is named by
  // its month, a day or week by its date.
  function dayLabel(iso, gran) {
    const p = String(iso || "").split("-");
    if (p.length !== 3) return iso || "";
    if (gran === "month") return MON[Number(p[1]) - 1] + " " + p[0];
    return MON[Number(p[1]) - 1] + " " + Number(p[2]);
  }
  function tipLabel(iso, gran) {
    const p = String(iso || "").split("-");
    if (p.length !== 3) return iso || "";
    if (gran === "month") return MONTH[Number(p[1]) - 1] + " " + p[0];
    return (gran === "week" ? "Week of " : "") + dayLabel(iso, "day");
  }
  const PER = { day: "each day", week: "each week", month: "each month" };
  const per = (gran) => PER[gran] || PER.day;
  const sum = (a) => (a || []).reduce((x, y) => x + (Number(y) || 0), 0);

  // ── State ─────────────────────────────────────────────────────────────────
  const S = {
    open: false,
    section: "overview",
    data: {},              // section → last payload
    loading: {},           // section → true while in flight
    error: {},             // section → message
    reqs: {},              // section → id of its newest request
    epoch: 0,              // bumped when the filters change; older answers are stale
    liveTimer: null,
    filters: {
      days: 30,            // 0 = Lifetime
      compare: false,
      only_multiplayer: false,
      include_test: false,
      include_guests: false,
      mode: "all",
      player_count: 0,
      min_sample: 20,
    },
    growthMeasure: "played",
    gamesMeasure: "games",
    playerScope: "range",  // "range" = on the game in this range, "all" = everyone
    playerQuery: "",
    advOpen: false,
    tablePage: {},
    sort: {},
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
    { days: 7,  label: "7 days" },
    { days: 30, label: "30 days" },
    { days: 90, label: "90 days" },
    { days: 0,  label: "Lifetime" },
  ];
  const rangeLabel = (days) => Number(days) === 0 ? "Lifetime" : "Last " + days + " days";

  // Plain-language definitions. They live HERE and in the Help drawer, never
  // as a paragraph on the dashboard itself.
  const DEFS = {
    "Players on the game": "Accounts that opened the game during the date range, whether or not they finished a game.",
    "Played a game": "Accounts that finished at least one game during the date range.",
    "New players": "Accounts created during the selected date range.",
    "Games played": "Finished games, counted once per player at the table: three friends finishing one game is three games played, the way the homepage counts.",
    "Came back after 7 days": "Of players who joined at least a week ago, the share still playing seven days later.",
    "Most played strategy": "Lifetime: the strategy each player names at the end of a game. A date range: the strategy the game spotted on each finished board.",
    "Most played table": "The table size with the most games played.",
    "Players online now": "Signed in and seen within the last few minutes.",
    "Games being played": "Rooms with a game running right now.",
    "Server": "Green when every health check passes. Details live in Technical Health.",
    "Played with other people": "The share of games with at least two signed-in players at the table. Guests don't save games, so a game with a guest reads as solo.",
    "Lifetime": "Every game since the start. Totals come from each account's own counters. Charts of modes, animals and scores come from the last 50 games each account keeps in detail.",
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
    // Days on the charts are this browser's days, not UTC's.
    try { body.tz = new Date().getTimezoneOffset(); } catch (_) { body.tz = 0; }
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
  //  app, and these shapes are all the dashboard needs.
  // ══════════════════════════════════════════════════════════════════════════
  const SERIES = ["var(--a-series-1)", "var(--a-series-2)", "var(--a-series-3)"];

  /* Gridlines for 0..max. Counts get whole-number steps: a line at "2.5
     players" is a line at nobody. */
  function niceTicks(max, whole) {
    if (max <= 0) return [0, 1];
    const raw = max / 3;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const mults = whole && mag < 10 ? [1, 2, 5, 10] : [1, 2, 2.5, 5, 10];
    let step = mults.map(m => m * mag).find(s => s >= raw) || mag * 10;
    if (whole) step = Math.max(1, Math.round(step));
    const out = [];
    for (let v = 0; v <= max + step * 0.001; v += step) out.push(Math.round(v * 100) / 100);
    // The top gridline has to reach the biggest value. Stopping one step short
    // is what drew a peak straight up through the chart's own title and buttons.
    while (out[out.length - 1] < max) out.push(Math.round((out[out.length - 1] + step) * 100) / 100);
    if (out.length < 2) out.push(step);
    return out;
  }
  const allWhole = (vals) => vals.every(v => Number.isInteger(Number(v) || 0));

  /* One time-series chart. `series` is [{ label, values }] in fixed slot order;
     a single series gets an area wash and no legend (the title names it). */
  function lineChart(host, opt) {
    if (!host) return;
    const days = opt.days || [];
    const gran = opt.gran || "day";
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
    const ticks = niceTicks(max, series.every(s => allWhole(s.values)));
    const top = ticks[ticks.length - 1] || 1;
    const x = (i) => PL + (days.length === 1 ? iw / 2 : (i / (days.length - 1)) * iw);
    const y = (v) => PT + ih - (Number(v) || 0) / top * ih;
    // Every axis ends at now, so its last day (or week, or month) is still
    // filling up. Its segment is drawn dashed and its tooltip says "so far",
    // so an evening that has barely started never reads as a collapse.
    const partial = opt.partial !== false && days.length > 1;

    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opt.title || "chart")}" preserveAspectRatio="none">`;
    // Recessive gridlines + axis ticks carry every value not directly labelled.
    svg += `<g class="ccA-grid">` + ticks.map(t =>
      `<line x1="${PL}" y1="${y(t).toFixed(1)}" x2="${PL + iw}" y2="${y(t).toFixed(1)}"/>`).join("") + `</g>`;
    svg += `<g class="ccA-axis">` + ticks.map(t =>
      `<text x="${PL - 9}" y="${(y(t) + 4).toFixed(1)}" text-anchor="end">${fmt(t)}</text>`).join("") + `</g>`;

    // Date labels: one every `stride` buckets, plus the final one, but only when
    // there is room for it. Without the gap test the last two labels overlap
    // into an unreadable smudge whenever the range doesn't divide evenly.
    const MIN_LABEL_GAP = gran === "month" ? 70 : 58;
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
      return `<text x="${x(i).toFixed(1)}" y="${H - 6}" text-anchor="${anchor}">${esc(dayLabel(days[i], gran))}</text>`;
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
      const coords = s.values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
      const pts = coords.join(" ");
      if (series.length === 1) {
        svg += `<polygon class="ccA-area" fill="${color}" points="${PL},${PT + ih} ${pts} ${PL + iw},${PT + ih}"/>`;
      }
      if (partial && coords.length > 1) {
        svg += `<polyline class="ccA-line" stroke="${color}" points="${coords.slice(0, -1).join(" ")}"/>`;
        svg += `<polyline class="ccA-line ccA-line-partial" stroke="${color}" points="${coords.slice(-2).join(" ")}"/>`;
      } else {
        svg += `<polyline class="ccA-line" stroke="${color}" points="${pts}"/>`;
      }
      const end = s.values.length - 1;
      svg += `<circle class="ccA-dot" cx="${x(end).toFixed(1)}" cy="${y(s.values[end]).toFixed(1)}" r="4.5" fill="${color}"/>`;
      // Label the endpoint only, a number on every point goes unread.
      svg += `<text class="ccA-endlabel" x="${(x(end) + 9).toFixed(1)}" y="${(y(s.values[end]) + 4).toFixed(1)}">${fmt(s.values[end])}</text>`;
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
      tip.innerHTML = `<div class="ccA-tip-k">${esc(tipLabel(days[i], gran))}${
          partial && i === days.length - 1 ? " (so far)" : ""}</div>`
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
     so a 3-bucket chart never draws three slabs. Sized to its host for the same
     reason lineChart is: a stretched viewBox stretches its text with it. */
  function columnChart(host, opt) {
    if (!host) return;
    const items = (opt.items || []).filter(i => i && i.label != null);
    if (!items.length || !items.some(i => Number(i.value) > 0)) {
      host.innerHTML = emptyHtml(opt.empty || "Not enough data yet.");
      return;
    }
    const W = Math.max(300, Math.round(host.clientWidth || 720));
    const H = opt.height || 210, PL = 44, PR = 12, PT = 18, PB = 34;
    const iw = W - PL - PR, ih = H - PT - PB;
    const max = Math.max(...items.map(i => Number(i.value) || 0));
    const ticks = niceTicks(max, allWhole(items.map(i => i.value)));
    const top = ticks[ticks.length - 1] || 1;
    const band = iw / items.length;
    const bw = Math.min(24, band * 0.55);
    const maxIdx = items.reduce((b, it, i) => (Number(it.value) > Number(items[b].value) ? i : b), 0);
    // Room for a label under each column, in characters of 11px bold, leaving a
    // gap to the next one: at 6.4px a character, "60–81 pts" ran into its
    // neighbour and the whole axis read as one line.
    const chars = Math.max(3, Math.floor((band - 8) / 7.2));

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
           + `style="font-size:11px;fill:var(--a-muted);font-weight:600">${esc(shorten(it.label, chars))}</text>`;
    });
    svg += `<line class="ccA-baseline" x1="${PL}" y1="${PT + ih}" x2="${PL + iw}" y2="${PT + ih}"/></svg>`;
    host.innerHTML = svg;
  }

  /* A ranked bar list. Deliberately used everywhere a pie chart would be: a
     reader can compare lengths but not angles. */
  function rankList(host, items, opt) {
    if (!host) return;
    opt = opt || {};
    const rows = (items || []).filter(i => i && i.label != null);
    if (!rows.length) { host.innerHTML = emptyHtml(opt.empty || "Not enough data yet."); return; }
    const max = Math.max(1, ...rows.map(r => Number(r.value) || 0));
    host.innerHTML = `<div class="ccA-rank">` + rows.map(r => {
      const v = Number(r.value) || 0;
      const color = r.tone === "warn" ? "var(--a-warn)" : r.tone === "bad" ? "var(--a-bad)"
                  : r.tone === "good" ? "var(--a-good)" : SERIES[0];
      return `<div class="ccA-rank-row">
        <div class="ccA-rank-lbl" title="${esc(r.label)}"><span class="ccA-rank-name">${esc(r.label)}</span></div>
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
    // A card can name a thing ("King Salmon", "4 players") instead of counting
    // one. A name is set smaller and on one line, so it never makes its card
    // taller than the numbers beside it.
    const isText = hasVal && typeof c.value === "string" && !isFinite(Number(c.value));
    const delta = c.delta;
    let deltaHtml = "";
    if (delta !== null && delta !== undefined && isFinite(delta)) {
      const dir = delta > 0.05 ? "up" : delta < -0.05 ? "down" : "flat";
      const arrow = dir === "up" ? "▲" : dir === "down" ? "▼" : "•";
      // The arrow is never the only channel, the signed number says it too.
      // What the change is measured against goes in the tooltip rather than as
      // a second line of text wrapping inside a 212px card.
      deltaHtml = `<span class="ccA-delta ${dir}">${arrow} ${Math.abs(delta).toFixed(1)}%</span>`;
      tip = (tip ? tip + " " : "") + `Compared with the previous ${S.filters.days} days.`;
    }
    const toneChip = c.tone && c.tone !== "neutral"
      ? `<span class="ccA-chip ${esc(c.tone === "bad" ? "bad" : c.tone)}">${
          c.tone === "good" ? "Healthy" : c.tone === "warn" ? "Watch" : "Problem"}</span>`
      : "";
    return `<div class="ccA-card">
      <div class="ccA-card-lbl">${esc(c.label)}${tip ? `<span class="ccA-info" tabindex="0" data-tip="${esc(tip)}">i</span>` : ""}</div>
      <div class="ccA-card-val${hasVal ? "" : " ccA-empty"}${isText ? " ccA-card-val--text" : ""}"${isText ? ` title="${esc(c.value)}"` : ""}>${
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

  /* A panel holding one chart host, with no controls: most of the grid cells. */
  const cellHtml = (id, title, question, extra) => `<div class="ccA-panel">
      <div class="ccA-panel-head"><div>
        <div class="ccA-panel-title">${esc(title)}${
          extra && extra.tip ? `<span class="ccA-info" tabindex="0" data-tip="${esc(extra.tip)}">i</span>` : ""}</div>
        <div class="ccA-panel-q">${esc(question)}</div>
      </div>${extra && extra.right ? `<div class="ccA-spacer"></div>${extra.right}` : ""}</div>
      <div class="ccA-chart" id="${esc(id)}"></div>
    </div>`;

  function blockHtml(title, inner, opts) {
    opts = opts || {};
    return `<div class="ccA-block">
      <div class="ccA-block-head"><div class="ccA-h2">${esc(title)}</div><div class="ccA-spacer"></div>${opts.right || ""}</div>
      ${inner}
    </div>`;
  }

  function headHtml(title, sub, d) {
    const days = d && d.range_days != null ? d.range_days : S.filters.days;
    const chip = d === false ? "" : `<span class="ccA-range-chip">${esc(rangeLabel(days))}</span>`;
    return `<div class="ccA-section-head"><div class="ccA-h1">${esc(title)}${chip}</div><div class="ccA-h1-sub">${esc(sub)}</div></div>`;
  }

  /* Said loudly, above everything: the player database couldn't be read, so
     the numbers below are old (or missing), not a collapse in players. */
  function statusHtml(d) {
    const st = d && d.data_status;
    if (!st || st.ok) return "";
    const age = st.age_sec != null
      ? ` Showing the numbers read ${fmtSpan(st.age_sec)} ago.`
      : " Player numbers can't be shown until it can be read.";
    return `<div class="ccA-banner" role="status"><span aria-hidden="true">⚠️</span><div>`
      + `<b>Couldn't read the player database.</b> ${esc(st.error || "")}${esc(age)}</div></div>`;
  }

  const noteHtml = (cov) => cov && cov.note ? `<div class="ccA-note">${esc(cov.note)}</div>` : "";

  // ── Tables ────────────────────────────────────────────────────────────────
  /* A paginated, sortable table with a column menu: the useful columns are on
     by default, the rest are opt-in so no table ever opens 20 columns wide.
     Every table is remembered by id, so sorting or paging one redraws just that
     table, including one inside a drawer, and never the charts around it. */
  const TABLES = {};
  const TYPE_BY_KEY = { last_seen: "date", joined: "date", created: "date", when: "date", win_rate: "pct" };
  const typeOf = (c) => c.type || TYPE_BY_KEY[c.key] || "auto";

  function tableHtml(id, spec, opts) {
    TABLES[id] = { spec: spec || {}, opts: opts || {} };
    return `<div class="ccA-tbl" data-tbl="${esc(id)}">${tableInner(id)}</div>`;
  }

  function redrawTable(id) {
    const t = TABLES[id];
    const box = $$(".ccA-tbl", overlay || document).find(el => el.getAttribute("data-tbl") === id);
    if (t && box) box.innerHTML = tableInner(id);
  }

  const sortOf = (id) => S.sort[id] || (TABLES[id] && TABLES[id].spec.sort) || null;

  function sortedRows(id) {
    const spec = TABLES[id].spec;
    const rows = spec.rows || [];
    const s = sortOf(id);
    if (!s || !s.key) return rows;
    const col = (spec.columns || []).find(c => c.key === s.key) || {};
    const dir = s.dir === "asc" ? 1 : -1;
    // Blanks always sink, whichever way the column is sorted: an unknown join
    // date is not "the oldest account".
    const blank = (v) => v == null || v === "" || (typeOf(col) === "date" && !v);
    return rows.slice().sort((a, b) => {
      const x = a[s.key], y = b[s.key];
      if (blank(x) || blank(y)) return blank(x) === blank(y) ? 0 : blank(x) ? 1 : -1;
      if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
      return String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: "base" }) * dir;
    });
  }

  function cellText(row, c) {
    const v = row[c.key];
    const type = typeOf(c);
    if (type === "date") return v ? fmtWhen(v) : "-";
    if (type === "datetime") return v ? fmtDateTime(v) : "-";
    if (type === "pct") return v == null || v === "" ? "-" : v + "%";
    if (v == null || v === "") return "-";
    if (typeof v === "number") return fmt(v);
    return String(v);
  }

  function tableInner(id) {
    const { spec, opts } = TABLES[id];
    const all = spec.columns || [];
    const cols = all.filter(c => c.always || (S.extraCols[id] || {})[c.key]);
    const rows = sortedRows(id);
    if (!rows.length) return emptyHtml(opts.empty || "No rows for this date range.");
    const per = opts.perPage || 15;
    const pages = Math.max(1, Math.ceil(rows.length / per));
    const page = Math.min(S.tablePage[id] || 0, pages - 1);
    const slice = rows.slice(page * per, page * per + per);
    const s = sortOf(id) || {};
    // Figures line up on the right; names and dates read from the left.
    const isNum = (c) => ["num", "pct"].includes(typeOf(c))
      || (typeOf(c) === "auto" && typeof (rows[0] || {})[c.key] === "number");
    const optional = all.filter(c => !c.always);
    return `<div class="ccA-table-wrap"><table class="ccA-table">
      <thead><tr>${cols.map(c => {
        const on = s.key === c.key;
        const aria = on ? ` aria-sort="${s.dir === "asc" ? "ascending" : "descending"}"` : "";
        return `<th class="${isNum(c) ? "num" : ""}"${aria}><button type="button" class="ccA-th-btn${on ? " on" : ""}" `
          + `data-sort="${esc(id)}" data-key="${esc(c.key)}">${esc(c.label)}<span class="ccA-sort-ico" aria-hidden="true">${
            on ? (s.dir === "asc" ? "▲" : "▼") : "↕"}</span></button></th>`;
      }).join("")}</tr></thead>
      <tbody>${slice.map(r => `<tr>${cols.map(c =>
        `<td class="${isNum(c) ? "num" : ""}">${esc(cellText(r, c))}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div>
    <div class="ccA-pager">
      <span>${rows.length.toLocaleString()} ${rows.length === 1 ? "row" : "rows"}${pages > 1 ? ` · page ${page + 1} of ${pages}` : ""}</span>
      <span class="ccA-spacer"></span>
      ${optional.length ? `<button class="ccA-btn" data-cols="${esc(id)}">Columns</button>` : ""}
      ${pages > 1 ? `<button class="ccA-btn" data-page="${esc(id)}" data-to="${page - 1}" ${page === 0 ? "disabled" : ""}>Back</button>
      <button class="ccA-btn" data-page="${esc(id)}" data-to="${page + 1}" ${page + 1 >= pages ? "disabled" : ""}>Next</button>` : ""}
    </div>`;
  }

  function onSort(id, key) {
    if (!TABLES[id]) return;
    const cur = sortOf(id) || {};
    const col = (TABLES[id].spec.columns || []).find(c => c.key === key) || {};
    // A new column starts where it is most useful: names A→Z, numbers biggest first.
    const dir = cur.key === key ? (cur.dir === "asc" ? "desc" : "asc")
      : (typeOf(col) === "text" ? "asc" : "desc");
    S.sort[id] = { key, dir };
    S.tablePage[id] = 0;
    redrawTable(id);
  }

  const stepsHtml = (rows) => rows.length ? `<div class="ccA-steps">` + rows.map(r => `
    <div class="ccA-step">
      <div class="ccA-step-lbl">${esc(r.label)}</div>
      <div class="ccA-step-val">${esc(r.value)}</div>
      <div class="ccA-step-note">${esc(r.note || "")}</div>
      ${r.pct != null ? `<div class="ccA-step-bar"><span style="width:${Math.max(0, Math.min(100, r.pct)).toFixed(1)}%"></span></div>` : ""}
    </div>`).join("") + `</div>` : emptyHtml("Not enough data yet.");

  const retentionSteps = (d, long) => stepsHtml((d.retention || []).map(r => ({
    label: "After " + r.day + (r.day === 1 ? " day" : " days"),
    value: r.rate == null ? "-" : r.rate + "%",
    note: r.returned + " of " + r.cohort + (long ? " players" : ""),
    pct: r.rate,
  })));

  // ══════════════════════════════════════════════════════════════════════════
  //  SECTION RENDERERS
  // ══════════════════════════════════════════════════════════════════════════
  const RENDER = {};

  // One measure per chart; the switcher swaps between them in place rather than
  // three near-identical charts sitting side by side.
  function growthTitle(m, gran) {
    if (m === "cumulative") return "Players in total";
    return (m === "new" ? "New players " : "Players who played ") + per(gran);
  }
  const GROWTH_SHORT = { played: "Played", new: "Joined", cumulative: "Players" };
  const growthSwitchHtml = () => `<div class="ccA-switch" data-switch="growth">`
    + [["played", "Played"], ["new", "New"], ["cumulative", "Total"]].map(([m, l]) =>
        `<button data-m="${m}" class="${S.growthMeasure === m ? "on" : ""}">${l}</button>`).join("")
    + `</div>`;

  function drawGrowth(d, hostSel) {
    const host = $(hostSel, overlay || document);
    if (!host) return;
    const g = d.growth || {};
    const m = S.growthMeasure;
    const title = growthTitle(m, g.gran);
    lineChart(host, {
      days: g.days, gran: g.gran, title,
      series: [{ label: GROWTH_SHORT[m], values: (g.series || {})[m] || [] }],
      // The previous period is only comparable for a per-day count, a running
      // total against a running total says nothing.
      compare: (S.filters.compare && !d.lifetime && m !== "cumulative" && g.compare) ? g.compare[m] : null,
      empty: m === "new" ? "Nobody joined during this period." : "Nobody played during this period.",
    });
    const t = host.closest(".ccA-panel").querySelector(".ccA-panel-title");
    if (t && t.firstChild) t.firstChild.nodeValue = title;
  }

  RENDER.overview = function (d, root) {
    const g = d.games || {};
    root.innerHTML =
      headHtml("Overview", d.lifetime ? "Everything since the first game." : "How the game is doing right now.", d)
      + statusHtml(d)
      + cardsHtml(d.cards)
      + noteHtml(d.coverage)
      // The block names the topic; the panel names the MEASURE currently shown,
      // so the switcher visibly changes something and no title is said twice.
      + blockHtml("Player growth",
          panelHtml("ccA-c-growth", growthTitle(S.growthMeasure, (d.growth || {}).gran),
                    "How many people are playing?", { controls: growthSwitchHtml(), more: "players" }))
      + blockHtml("Games played",
          panelHtml("ccA-c-games", "Games played " + per(g.gran),
                    "How many games are being played?", { more: "gameplay" }))
      + blockHtml("Retention", `<div class="ccA-panel">
          <div class="ccA-panel-head"><div>
            <div class="ccA-panel-title">Players who came back<span class="ccA-info" tabindex="0" data-tip="Only players who have had the chance to come back are counted, someone who joined yesterday can't have a 7-day return yet.">i</span></div>
            <div class="ccA-panel-q">Are players returning after joining?</div>
          </div><div class="ccA-spacer"></div>
          <button class="ccA-btn" data-more="players">View details</button></div>
          ${retentionSteps(d, true)}
        </div>`)
      + blockHtml("Live activity and alerts", `<div class="ccA-grid-2">
          ${liveHtml(d.live || {})}
          ${alertsHtml(d.alerts || [], true)}
        </div>`);

    drawGrowth(d, "#ccA-c-growth");
    lineChart($("#ccA-c-games", root), {
      days: g.days, gran: g.gran, title: "Games played",
      series: [{ label: "Games", values: g.played || [] }],
      empty: d.lifetime ? "No games have been saved to any account yet."
                        : "No games were played during this period.",
    });
  };

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

  // ── Players ───────────────────────────────────────────────────────────────
  function playersTableHtml(d) {
    const t = d.table || {};
    const scope = d.lifetime ? "all" : S.playerScope;
    const q = S.playerQuery.trim().toLowerCase();
    let rows = t.rows || [];
    if (scope === "range") rows = rows.filter(r => r.in_range);
    if (q) rows = rows.filter(r => String(r.name || "").toLowerCase().indexOf(q) >= 0);
    return tableHtml("players", Object.assign({}, t, { rows }), {
      empty: q ? "No player matches that name." : "Nobody opened the game in this date range.",
    });
  }

  RENDER.players = function (d, root) {
    const t = d.table || {};
    const everyone = (t.rows || []).length;
    const onGame = t.in_range_count != null ? t.in_range_count : everyone;
    const scopeAll = d.lifetime || S.playerScope === "all";
    root.innerHTML =
      headHtml("Players", d.lifetime ? "Everyone who has made an account, and what they play."
                                     : "Who is on the game, and who is staying.", d)
      + statusHtml(d)
      + cardsHtml(d.cards)
      + blockHtml("Growth",
          panelHtml("ccA-p-growth", growthTitle(S.growthMeasure, (d.growth || {}).gran),
                    "How many people are playing?", { controls: growthSwitchHtml() }))
      + blockHtml("Coming back", `<div class="ccA-grid-2">
          <div class="ccA-panel">
            <div class="ccA-panel-head"><div>
              <div class="ccA-panel-title">Players who came back</div>
              <div class="ccA-panel-q">Are players returning after joining?</div>
            </div></div>
            ${retentionSteps(d, false)}
          </div>
          ${cellHtml("ccA-p-funnel", "How far players get", "Where do new players stop?")}
        </div>`)
      + blockHtml("Levels reached", cellHtml("ccA-p-levels", "Levels reached", "How far along is the player base?"))
      + blockHtml("Player list", `<div class="ccA-panel">
          <div class="ccA-panel-head"><div>
            <div class="ccA-panel-title" id="ccA-p-list-title">${scopeAll ? "Every player" : "Players on the game"}</div>
            <div class="ccA-panel-q">${d.lifetime ? "Who has played, and what do they play most?"
                                                  : "Who opened the game in this date range?"}</div>
          </div><div class="ccA-spacer"></div>
          ${d.lifetime ? "" : `<div class="ccA-switch" data-switch="scope">
            <button data-m="range" class="${S.playerScope === "range" ? "on" : ""}">On the game · ${fmt(onGame)}</button>
            <button data-m="all" class="${S.playerScope === "all" ? "on" : ""}">Everyone · ${fmt(everyone)}</button>
          </div>`}</div>
          <div class="ccA-table-tools">
            <input class="ccA-input ccA-input-sm" id="ccA-p-find" type="search" placeholder="Find a player…"
                   value="${esc(S.playerQuery)}" autocomplete="off" aria-label="Find a player">
          </div>
          <div id="ccA-p-table">${playersTableHtml(d)}</div>
        </div>`);

    drawGrowth(d, "#ccA-p-growth");
    rankList($("#ccA-p-funnel", root), d.funnel || []);
    columnChart($("#ccA-p-levels", root), { items: d.levels || [], title: "Levels reached" });
    const find = $("#ccA-p-find", root);
    if (find) {
      find.addEventListener("input", () => {
        S.playerQuery = find.value || "";
        S.tablePage.players = 0;
        const box = $("#ccA-p-table", root);
        if (box) box.innerHTML = playersTableHtml(d);
      });
    }
  };

  // ── Gameplay ──────────────────────────────────────────────────────────────
  const GAMES_MEASURES = [["games", "Games"], ["multiplayer", "With others"], ["players", "Players"]];
  function volTitle(m, gran) {
    if (m === "players") return "Players who played " + per(gran);
    if (m === "multiplayer") return "Games with other people " + per(gran);
    return "Games played " + per(gran);
  }

  RENDER.gameplay = function (d, root) {
    const v = d.volume || {};
    const stratTip = d.strategy_source === "confirmed"
      ? "From the strategy each player names at the end of a game."
      : "From the strategy the game spotted on each finished board.";
    root.innerHTML =
      headHtml("Gameplay", d.lifetime ? "Every game players have finished, and what they played most."
                                      : "What happens inside the games.", d)
      + statusHtml(d)
      + cardsHtml(d.cards)
      + noteHtml(d.coverage)
      + blockHtml("Games over time",
          panelHtml("ccA-g-vol", volTitle(S.gamesMeasure, v.gran), "How many games are being played?", {
            controls: `<div class="ccA-switch" data-switch="games">${GAMES_MEASURES.map(([m, l]) =>
              `<button data-m="${m}" class="${S.gamesMeasure === m ? "on" : ""}">${l}</button>`).join("")}</div>`,
          }))
      + blockHtml("How games are played", `<div class="ccA-grid-2">
          ${cellHtml("ccA-g-sizes", "Players per game", "What size table do people play most?")}
          ${cellHtml("ccA-g-modes", "Game modes", "Which modes get played?",
                     { tip: "From each player's saved games." })}
        </div>`)
      + blockHtml("What gets played", `<div class="ccA-grid-2">
          ${cellHtml("ccA-g-strats", "Strategies played", "Which strategy do players build most?", {
            tip: stratTip, right: `<button class="ccA-btn" data-drawer="strategies">View details</button>`,
          })}
          ${cellHtml("ccA-g-scores", "Final scores", "What does a normal score look like?")}
        </div>`);

    drawGameVolume(d);
    columnChart($("#ccA-g-sizes", root), { items: d.sizes || [], title: "Players per game" });
    rankList($("#ccA-g-modes", root), d.modes || []);
    rankList($("#ccA-g-strats", root), (d.strategies || []).slice(0, 6),
      { empty: "No strategies recorded yet." });
    columnChart($("#ccA-g-scores", root), { items: d.scores || [], title: "Final scores" });
  };

  function drawGameVolume(d) {
    const host = $("#ccA-g-vol", overlay || document);
    if (!host) return;
    const v = d.volume || {};
    const m = S.gamesMeasure;
    lineChart(host, {
      days: v.days, gran: v.gran, title: volTitle(m, v.gran),
      series: [{ label: (GAMES_MEASURES.find(x => x[0] === m) || [0, "Games"])[1], values: v[m] || [] }],
      empty: m === "multiplayer" ? "No games with other people during this period."
                                 : "No games were played during this period.",
    });
    const t = host.closest(".ccA-panel").querySelector(".ccA-panel-title");
    if (t && t.firstChild) t.firstChild.nodeValue = volTitle(m, v.gran);
  }

  // ── Cards ─────────────────────────────────────────────────────────────────
  RENDER.cards = function (d, root) {
    const review = d.review || [];
    const typical = ((d.cards || []).find(c => c.label === "Typical win rate") || {}).value;
    root.innerHTML =
      headHtml("Cards", "Which animals get played, and which look out of balance.", d)
      + statusHtml(d)
      + cardsHtml(d.cards)
      + noteHtml(d.coverage)
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
              <div class="ccA-rank-lbl" title="${esc(r.name)}"><span class="ccA-rank-name">${esc(r.name)}</span>
                <span class="ccA-chip ${strong ? "warn" : "neutral"}">${strong ? "Too strong" : "Too weak"}</span>
              </div>
              <div class="ccA-rank-track"><div class="ccA-rank-fill" style="width:${Math.min(100, r.win_rate).toFixed(1)}%;background:${strong ? "var(--a-warn)" : "var(--a-series-3)"}"></div></div>
              <div class="ccA-rank-val">${esc(r.win_rate)}%</div>
            </div>`;
          }).join("") + `</div>
            <div class="ccA-panel-q" style="margin-top:12px">${review.length} ${review.length === 1 ? "animal sits" : "animals sit"} more than 12 points from the typical win rate of ${esc(typical == null ? "-" : typical)}%.</div>`
            : emptyHtml("No animals look out of balance right now.", "✓")}
        </div>`)
      + blockHtml("What gets played", `<div class="ccA-grid-2">
          ${cellHtml("ccA-cd-played", "Most played animals", "What ends up on boards most often?")}
          ${cellHtml("ccA-cd-species", "Families played", "Which families dominate?")}
        </div>`)
      + blockHtml("Oceans", cellHtml("ccA-cd-oceans", "Oceans played", "Which oceans do players build on?"))
      + blockHtml("Every animal", `<div class="ccA-panel">${tableHtml("cards", d.table || {}, { perPage: 20 })}</div>`);

    rankList($("#ccA-cd-played", root), (d.most_played || []).slice(0, 8));
    rankList($("#ccA-cd-species", root), (d.species || []).slice(0, 8));
    rankList($("#ccA-cd-oceans", root), (d.oceans || []).slice(0, 8));
  };

  // ── Competitive ───────────────────────────────────────────────────────────
  RENDER.competitive = function (d, root) {
    const v = d.volume || {};
    root.innerHTML =
      headHtml("Competitive", "Both competitive tables: the paired game and the free-for-all.", d)
      + statusHtml(d)
      + cardsHtml(d.cards)
      + noteHtml(d.coverage)
      + blockHtml("Games over time",
          panelHtml("ccA-comp-vol", "Competitive games " + per(v.gran), "How much competitive play is happening?"))
      + blockHtml("How games end", `<div class="ccA-grid-2">
          ${cellHtml("ccA-comp-out", "Results", "How do competitive games end?",
                     { tip: d.lifetime ? "Every account's lifetime wins, losses and draws." : "From each player's saved games." })}
          ${cellHtml("ccA-comp-top", "Top Ocean Points", "Who leads this season?")}
        </div>`)
      + blockHtml("Competitive players", `<div class="ccA-panel">${tableHtml("competitive", d.table || {}, {
          empty: "Nobody played competitive in this date range." })}</div>`);

    lineChart($("#ccA-comp-vol", root), {
      days: v.days, gran: v.gran, title: "Competitive games",
      series: [{ label: "Pairs", values: v.paired || [] },
               { label: "Free-for-all", values: v.ffa || [] }],
      empty: "No competitive games during this period.",
    });
    rankList($("#ccA-comp-out", root), (d.outcomes || []).filter(o => Number(o.value) > 0),
      { empty: "No competitive results yet." });
    rankList($("#ccA-comp-top", root), d.top || [], { empty: "Nobody has Ocean Points this season." });
  };

  // ── Clans ─────────────────────────────────────────────────────────────────
  RENDER.clans = function (d, root) {
    root.innerHTML =
      headHtml("Clans", d.lifetime ? "Every clan, across every season." : "How the clan season is going.", d)
      + statusHtml(d)
      + cardsHtml(d.cards)
      + blockHtml(d.lifetime ? "Every season" : "This season", `<div class="ccA-grid-2">
          ${cellHtml("ccA-cl-top", "Top clans", d.lifetime ? "Who has scored the most, ever?" : "Who is winning the season?")}
          ${cellHtml("ccA-cl-sizes", "Clan sizes", "Are clans filling up?")}
        </div>`)
      + blockHtml("Every clan", `<div class="ccA-panel">${tableHtml("clans", d.table || {}, {
          empty: "No clans have been made yet." })}</div>`);

    rankList($("#ccA-cl-top", root), d.top || [], {
      empty: d.lifetime ? "No clan has scored yet." : "No clan has scored this season yet." });
    columnChart($("#ccA-cl-sizes", root), { items: d.sizes || [], title: "Clan sizes" });
  };

  // ── Economy ───────────────────────────────────────────────────────────────
  RENDER.economy = function (d, root) {
    const rev = d.revenue || {};
    root.innerHTML =
      headHtml("Economy", "Critter Coins and support.", d)
      + statusHtml(d)
      + cardsHtml(d.cards)
      + blockHtml("Purchases",
          panelHtml("ccA-ec-rev", "Purchases " + per(rev.gran), "How often does anyone buy something?", {
            tip: "Completed Stripe payments recorded in this date range.",
          }))
      + blockHtml("Where the coins are", `<div class="ccA-grid-2">
          ${cellHtml("ccA-ec-bal", "Coin balances", "How much is everyone holding?")}
          ${cellHtml("ccA-ec-top", "Biggest balances", "Who has the most coins?")}
        </div>`);

    lineChart($("#ccA-ec-rev", root), {
      days: rev.days, gran: rev.gran, title: "Purchases",
      series: [{ label: "Purchases", values: rev.series || [] }],
      empty: "No purchases during this period.",
    });
    columnChart($("#ccA-ec-bal", root), { items: d.balances || [], title: "Coin balances" });
    rankList($("#ccA-ec-top", root), d.top_holders || [], { empty: "Nobody is holding coins yet." });
  };

  // ── Events ────────────────────────────────────────────────────────────────
  RENDER.events = function (d, root) {
    const tr = d.trades || {};
    root.innerHTML =
      headHtml("Events", "Trading, team games and unlocks.", d)
      + statusHtml(d)
      + cardsHtml(d.cards)
      + noteHtml(d.coverage)
      + blockHtml("Trading",
          panelHtml("ccA-ev-trades", "Trades completed " + per(tr.gran), "Are players trading with each other?"))
      + blockHtml("Team games", cellHtml("ccA-ev-teams", "Team games by table size",
                                         "How are people setting up team games?"));

    lineChart($("#ccA-ev-trades", root), {
      days: tr.days, gran: tr.gran, title: "Trades completed",
      series: [{ label: "Trades", values: tr.series || [] }],
      empty: "No trades during this period.",
    });
    columnChart($("#ccA-ev-teams", root), { items: d.team_sizes || [], title: "Team games",
      empty: "No team games during this period." });
  };

  // ── Technical ─────────────────────────────────────────────────────────────
  RENDER.technical = function (d, root) {
    const load = d.load || {};
    const t = d.truncated || {};
    root.innerHTML =
      headHtml("Technical Health", "Whether the game is working properly.", d)
      + statusHtml(d)
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
            tip: "From the server's own records: games saved without a final score because everyone left or the room errored. Nobody's account keeps a game that never finished.",
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

    lineChart($("#ccA-t-trunc", root), {
      days: t.days, gran: t.gran, title: "Games that ended badly",
      series: [{ label: "Ended badly", values: t.series || [] }],
      empty: d.records_on_disk ? "No games ended badly during this period."
        : "The server has no game records on its disk, so games nobody finished can't be counted.",
    });
  };

  // ── Player Search ─────────────────────────────────────────────────────────
  RENDER.search = function (d, root) {
    const p = d.player;
    const fact = (label, value) =>
      `<div class="ccA-fact"><div class="ccA-fact-lbl">${esc(label)}</div><div class="ccA-fact-val">${esc(value)}</div></div>`;
    const rec = (p && p.comp_record) || {};
    root.innerHTML =
      headHtml("Player Search", "Look up one player by name or friend code.", false)
      + `<div class="ccA-search-row">
          <input class="ccA-input" id="ccA-q" placeholder="Player name or friend code…" value="${esc(S.searchQuery)}" autocomplete="off">
          <button class="ccA-btn primary" id="ccA-go">Search</button>
        </div>`
      + statusHtml(d)
      + (!d.query ? emptyHtml("Type a name or friend code to begin.", "⌕")
        : !(d.matches || []).length ? emptyHtml("No player matched that search.")
        : `${(d.matches || []).length > 1 ? blockHtml("Matches", `<div class="ccA-panel">${tableHtml("search", {
              columns: [
                { key: "name", label: "Player", always: true, type: "text" },
                { key: "friend_code", label: "Code", always: true, type: "text" },
                { key: "games", label: "Games", always: true, type: "num" },
                { key: "level", label: "Level", always: true, type: "num" },
                { key: "last_seen", label: "Last seen", always: true, type: "date" },
              ], rows: d.matches,
            })}</div>`) : ""}
          ${p ? blockHtml(p.name, `<div class="ccA-panel">
            <div class="ccA-facts">
              ${fact("Friend code", p.friend_code || "-")}
              ${fact("Joined", fmtWhen(p.joined))}
              ${fact("Last seen", p.online ? "Online now" : p.last_seen ? fmtAgo(Math.floor(Date.now() / 1000) - p.last_seen) : "-")}
              ${fact("Games", fmt(p.games))}
              ${fact("Wins", fmt(p.wins))}
              ${fact("Level", fmt(p.level))}
              ${fact("Hours played", fmt(p.hours))}
              ${fact("Best score", fmt(p.highest_score))}
              ${fact("Favourite strategy", p.favorite || "-")}
              ${fact("Competitive (W-L-D)", fmt(rec.wins || 0) + "-" + fmt(rec.losses || 0) + "-" + fmt(rec.draws || 0))}
              ${fact("Ocean Points (season)", fmt(p.comp_points))}
              ${fact("Coins", fmt(p.coins))}
              ${fact("Critters", fmt(p.icons))}
              ${fact("Prestige", fmt(p.prestige))}
            </div>
          </div>`) : ""}
          ${p ? blockHtml("What they play most", `<div class="ccA-grid-2">
            ${cellHtml("ccA-s-strats", "Strategies", "Which strategy do they build most?")}
            ${cellHtml("ccA-s-sizes", "Table sizes", "What size table do they play?")}
          </div>`) : ""}
          ${p && (p.recent || []).length ? blockHtml("Their games", `<div class="ccA-panel">${tableHtml("recent", {
            columns: [
              { key: "when", label: "When", always: true, type: "datetime" },
              { key: "mode", label: "Mode", always: true, type: "text" },
              { key: "players", label: "Players", always: true, type: "num" },
              { key: "score", label: "Score", always: true, type: "num" },
              { key: "result", label: "Result", always: true, type: "text" },
              { key: "strategy", label: "Strategy", type: "text" },
              { key: "opponents", label: "At the table", type: "text" },
            ],
            rows: p.recent,
            sort: { key: "when", dir: "desc" },
          }, { perPage: 10 })}</div>`) : ""}`);

    if (p) {
      rankList($("#ccA-s-strats", root), p.strategies || [], { empty: "No strategies recorded yet." });
      rankList($("#ccA-s-sizes", root), p.sizes || [], { empty: "No games recorded yet." });
    }
    const go = () => {
      S.searchQuery = ($("#ccA-q", root) || {}).value || "";
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
          <div class="ccA-ctl ccA-seg" id="ccA-range" role="radiogroup" aria-label="Date range">
            ${RANGES.map(r => `<button type="button" role="radio" data-days="${r.days}">${r.label}</button>`).join("")}
          </div>
          <button class="ccA-ctl" id="ccA-compare" aria-pressed="false">Compare</button>
          <button class="ccA-ctl" id="ccA-refresh" title="Read the latest numbers">↻ Refresh</button>
          <button class="ccA-ctl" id="ccA-export" title="Download everything as JSON">Export</button>
          <button class="ccA-ctl ccA-adv-toggle" id="ccA-adv-btn">Filters <span class="ccA-caret">▾</span></button>
          <button class="ccA-ctl" id="ccA-help" aria-label="What the numbers mean">?</button>
        </div>
        <button id="ccA-close" title="Close (Esc)" aria-label="Close analytics">✕</button>
      </div>
      <div class="ccA-adv" id="ccA-adv">
        <label><input type="checkbox" data-f="include_test"> Include test accounts</label>
        <label><input type="checkbox" data-f="include_guests"> Include guest players</label>
        <label><input type="checkbox" data-f="only_multiplayer"> Only games with other people</label>
        <label>Game mode
          <select class="ccA-ctl" data-f="mode" style="height:30px">
            <option value="all">All</option><option value="casual">Casual</option>
            <option value="competitive">Competitive</option><option value="team">Team</option>
            <option value="physical">Table game (Snap Score)</option>
          </select>
        </label>
        <label>Players
          <select class="ccA-ctl" data-f="player_count" style="height:30px">
            <option value="0">Any</option><option value="2">2</option><option value="3">3</option>
            <option value="4">4</option><option value="5">5</option><option value="6">6</option>
            <option value="7">7</option><option value="8">8</option>
          </select>
        </label>
        <div class="ccA-adv-note">Filters apply to every section. Turning on test accounts includes the developer account. A game mode, table size or "only games with other people" filter counts from each player's last 50 saved games, so lifetime totals then leave out older games.</div>
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

  /* The range buttons and Compare always say what the filters say. Lifetime
     has no earlier period, so Compare is switched off and greyed out there. */
  function syncRange() {
    if (!overlay) return;
    $$("#ccA-range [data-days]", overlay).forEach(b => {
      const on = Number(b.getAttribute("data-days")) === Number(S.filters.days);
      b.classList.toggle("on", on);
      b.setAttribute("aria-checked", String(on));
    });
    const cmp = $("#ccA-compare", overlay);
    const lifetime = Number(S.filters.days) === 0;
    if (lifetime) S.filters.compare = false;
    cmp.disabled = lifetime;
    cmp.title = lifetime ? "Lifetime has no earlier period to compare with" : "Compare with the period before";
    cmp.classList.toggle("on", S.filters.compare);
    cmp.setAttribute("aria-pressed", String(S.filters.compare));
  }

  function wire() {
    $("#ccA-close", overlay).addEventListener("click", close);
    $("#ccA-range", overlay).addEventListener("click", (e) => {
      const b = e.target.closest("[data-days]");
      if (!b) return;
      const days = Number(b.getAttribute("data-days"));
      if (!isFinite(days) || days === S.filters.days) return;
      S.filters.days = days;
      syncRange();
      invalidate();
    });
    $("#ccA-compare", overlay).addEventListener("click", () => {
      if (Number(S.filters.days) === 0) return;
      S.filters.compare = !S.filters.compare;
      syncRange();
      invalidate();
    });
    $("#ccA-refresh", overlay).addEventListener("click", () => load(S.section, true, true));
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

    // A definition opens toward whichever side has room. Centred on its icon,
    // the rightmost card's ran off the edge of the screen.
    const placeTip = (e) => {
      const info = e.target && e.target.closest ? e.target.closest(".ccA-info") : null;
      if (!info) return;
      const box = (info.closest(".ccA-body, .ccA-drawer-body") || overlay).getBoundingClientRect();
      const r = info.getBoundingClientRect();
      const mid = r.left + r.width / 2;
      const half = Math.min(240, box.width - 32) / 2;
      info.classList.toggle("ccA-tip-start", mid - half < box.left + 8);
      info.classList.toggle("ccA-tip-end", mid + half > box.right - 8);
    };
    overlay.addEventListener("mouseover", placeTip);
    overlay.addEventListener("focusin", placeTip);

    // Tables live on the page AND in drawers, so their controls are handled
    // once, for the whole overlay.
    overlay.addEventListener("click", (e) => {
      const sort = e.target.closest("[data-sort]");
      if (sort) { onSort(sort.getAttribute("data-sort"), sort.getAttribute("data-key")); return; }
      const page = e.target.closest("[data-page]");
      if (page && !page.disabled) {
        const id = page.getAttribute("data-page");
        S.tablePage[id] = Math.max(0, Number(page.getAttribute("data-to")) || 0);
        redrawTable(id);
        return;
      }
      const cols = e.target.closest("[data-cols]");
      if (cols) openColumns(cols.getAttribute("data-cols"));
    });

    // One delegated listener for everything else the sections render.
    $("#ccA-inner", overlay).addEventListener("click", (e) => {
      const more = e.target.closest("[data-more]");
      if (more) { go(more.getAttribute("data-more")); return; }
      const sw = e.target.closest("[data-switch] button");
      if (sw) { onSwitch(sw); return; }
      const dr = e.target.closest("[data-drawer]");
      if (dr) openInfoDrawer(dr.getAttribute("data-drawer"));
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
    const d = S.data[S.section];
    if (!d) return;
    // Redraw the ONE chart (or table) rather than the page, the switcher exists
    // so the dashboard doesn't need near-identical charts side by side.
    if (group === "growth") {
      S.growthMeasure = m;
      drawGrowth(d, S.section === "overview" ? "#ccA-c-growth" : "#ccA-p-growth");
    } else if (group === "games") {
      S.gamesMeasure = m;
      drawGameVolume(d);
    } else if (group === "scope") {
      S.playerScope = m;
      S.tablePage.players = 0;
      const title = $("#ccA-p-list-title", overlay);
      if (title) title.textContent = m === "all" ? "Every player" : "Players on the game";
      const box = $("#ccA-p-table", overlay);
      if (box) box.innerHTML = playersTableHtml(d);
    }
  }

  function go(section) {
    if (!SECTIONS.some(s => s.id === section)) return;
    S.section = section;
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
    S.epoch++;
    S.data = {};
    S.loading = {};
    S.error = {};
    S.tablePage = {};
    load(S.section, true);
  }

  // ══════════════════════════════════════════════════════════════════════════
  //  LOAD + RENDER
  // ══════════════════════════════════════════════════════════════════════════
  async function load(section, force, refresh) {
    if (!force && S.data[section]) { render(); return; }
    // Already on its way: going back to a tab that is still loading must not
    // send it twice.
    if (!force && S.loading[section]) { render(); return; }
    const id = (S.reqs[section] = (S.reqs[section] || 0) + 1);
    const epoch = S.epoch;
    S.loading[section] = true;
    S.error[section] = "";
    const stamp = $("#ccA-updated", overlay);
    if (stamp && refresh) stamp.textContent = "Refreshing…";
    render();
    const extra = Object.assign(section === "search" ? { query: S.searchQuery } : {},
                                refresh ? { refresh: true } : {});
    const res = await post(section, extra);
    // A newer request for this section, or a change of filters, made this
    // answer stale. An answer for a tab the reader has since left is still
    // kept: it is exactly what they will see when they come back.
    if (id !== S.reqs[section] || epoch !== S.epoch) return;
    S.loading[section] = false;
    const el = $("#ccA-updated", overlay);
    if (res === null) {
      S.error[section] = "Couldn't reach the server. Check your connection and hit Refresh.";
      if (el && S.data[section]) el.textContent = "Couldn't refresh";
    } else if (!res || !res.ok) {
      S.error[section] = errMsg(res && res.error);
      if (el && S.data[section]) el.textContent = "Couldn't refresh";
    } else {
      S.data[section] = res;
      if (el) el.textContent = "Updated " + new Date().toLocaleTimeString(undefined,
        { hour: "numeric", minute: "2-digit" });
    }
    if (S.section === section) render();
  }

  function render() {
    const root = $("#ccA-inner", overlay);
    if (!root) return;
    const sec = S.section;
    const data = S.data[sec];
    if (!data) {
      root.innerHTML = S.error[sec]
        ? headHtml(nameOf(sec), "") + `<div class="ccA-panel">${emptyHtml(S.error[sec], "⚠️")}</div>`
        : skeleton(sec);
      return;
    }
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
    return headHtml(nameOf(sec), "", sec === "search" ? false : undefined)
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
        <div class="ccA-panel-q" style="margin-top:6px">Everything is measured over the range in the header: the last 7, 30 or 90 days, or Lifetime.
        Every number comes from the players' own accounts, the same place the homepage and each Player Home read.
        A dash means there isn't enough data yet, never zero, and a rate with nothing to divide by is left blank rather than shown as 0%.</div>
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
              { key: "name", label: "Animal", always: true, type: "text" },
              { key: "boards", label: "Boards", always: true, type: "num" },
              { key: "win_rate", label: "Win rate", always: true, type: "pct" },
              { key: "gap", label: "From typical", always: true, type: "num" },
            ], rows: d.review,
          }, { perPage: 20 })}</div>` : emptyHtml("No animals look out of balance right now.", "✓")}
        </div>`);
      return;
    }
    if (kind === "strategies") {
      const d = S.data.gameplay || {};
      openDrawer("Strategies played", `<div class="ccA-panel">
        <div class="ccA-panel-q" style="margin-bottom:4px">${esc(d.strategy_source === "confirmed"
          ? "How many times players named each strategy at the end of a game."
          : "How many finished boards the game recognised as each strategy.")}</div>
        ${tableHtml("strats", {
          columns: [{ key: "label", label: "Strategy", always: true, type: "text" },
                    { key: "value", label: "Games", always: true, type: "num" }],
          rows: d.strategies || [],
          sort: { key: "value", dir: "desc" },
        }, { perPage: 20, empty: "No strategies recorded yet." })}</div>`);
    }
  }

  function openColumns(tableId) {
    const spec = (TABLES[tableId] || {}).spec || {};
    const optional = (spec.columns || []).filter(c => !c.always);
    openDrawer("Columns", `<div class="ccA-panel">
      <div class="ccA-panel-q" style="margin-bottom:10px">The most useful columns are always on. Add the rest here.</div>
      ${optional.map(c => `<label style="display:flex;gap:9px;align-items:center;padding:7px 0;font-size:.87rem">
        <input type="checkbox" data-col="${esc(c.key)}" ${(S.extraCols[tableId] || {})[c.key] ? "checked" : ""}
          style="width:16px;height:16px;accent-color:var(--a-series-1)"> ${esc(c.label)}</label>`).join("")}
    </div>`);
    $$("[data-col]", $(".ccA-drawer-back", overlay)).forEach(el => {
      el.addEventListener("change", () => {
        S.extraCols[tableId] = S.extraCols[tableId] || {};
        S.extraCols[tableId][el.getAttribute("data-col")] = el.checked;
        redrawTable(tableId);
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
      a.download = "critters-analytics-" + (Number(S.filters.days) === 0 ? "lifetime" : S.filters.days + "d")
        + "-" + new Date().toISOString().slice(0, 10) + ".json";
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
    syncRange();
    go(section && SECTIONS.some(s => s.id === section) ? section : S.section);
    // The live panel refreshes on its own clock, in place. It re-renders only
    // the four numbers and the sign-up list, so nothing under the reader's
    // cursor moves and the page never jumps.
    clearInterval(S.liveTimer);
    S.liveTimer = setInterval(refreshLive, 20000);
  }

  async function refreshLive() {
    if (!S.open || S.section !== "overview") return;
    // Nobody is looking at a background tab, so it asks for nothing. The tick
    // calls the "live" action, which reuses the cached account scan and never
    // starts one: the Overview used to be re-fetched whole here, a full scan
    // of every account behind a 2-minute cache, all day, for a tab nobody
    // had open. See the free-tier read allowance that ran out on 2026-09-04.
    if (document.hidden) return;
    const res = await post("live");
    if (!res || !res.ok || !res.live || !S.open || S.section !== "overview") return;
    if (S.data.overview) S.data.overview.live = res.live;
    const host = $("#ccA-live-panel", overlay);
    if (!host) return;
    const wrap = document.createElement("div");
    wrap.innerHTML = liveHtml(res.live);
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
  window.__ccAnalyticsInternals = { S, RENDER, lineChart, columnChart, rankList, cardHtml, tableHtml,
                                    fmt, dayLabel, niceTicks, SECTIONS, RANGES, refreshLive };

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
