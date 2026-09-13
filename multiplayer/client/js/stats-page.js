/* ================================================================
 * Currents and Critters: the Stats page.
 *
 * One page for every number an account keeps, drawn as charts rather than a
 * wall of figures. It replaced the Casual tab, and everything that tab showed
 * (the record at each table size from 2 to 8 players, and the games played
 * at that size) lives in the "Table sizes" card here.
 *
 * WHERE THE NUMBERS COME FROM. Nothing on this page reads Firestore or the
 * server. js/preview-app.js hands over a snapshot through
 * window.__ccStatsData(): the same cached stats map the Overview reads, plus
 * the achievement and critter counts, the Competitive rank and the bot
 * ladder, each worked out by the code that already owns it. The rules for
 * totals are the Overview's own (games = the larger of completed_games and
 * the per-size counts), and the per-size rules are the old Casual tab's, so
 * the same account reads the same everywhere.
 *
 * The charts are SVG drawn at the card's real pixel width, so their text is
 * the size it says it is on a phone and on a monitor. They redraw when the
 * card changes width. Every mark has a tooltip on hover, focus and tap, and
 * every value a tooltip shows is also printed on the page (or, for the score
 * line, in the table under it), so nothing is only reachable by hovering.
 *
 * The chart colours were checked for colour-blind separation against the
 * page's cream card: blue #2f86de, coral #f08a4b, violet #8b6be6 and teal
 * #1fa57f for identity; gold #d9920b for a win, wherever it appears.
 * ================================================================ */
(function () {
  "use strict";

  const ROOT_ID = "cc-stats-root";
  const PANEL_ID = "ph-panel-stats";

  const C = {
    blue:   "#2f86de",
    coral:  "#f08a4b",
    violet: "#8b6be6",
    teal:   "#1fa57f",
    gold:   "#d9920b",
    slate:  "#a9b4c4",
    band:   "rgba(84,112,224,.11)",
    grid:   "#eadccb",
    surface:"#fff7ec",
    ink:    "#103a74",
    ink2:   "#46638a",
    muted:  "#5f7089",
  };
  // Days played, light to dark. The empty day is its own quiet cell, not the
  // first step, so "no game" never reads as "a little".
  const HEAT = ["#6fb1eb", "#3a8fdf", "#2266b8", "#123f7c"];
  const HEAT_EMPTY = "#efe2cf";

  const MODE = {
    normal:      { label: "Casual",          color: C.blue },
    team:        { label: "Team",            color: C.teal },
    ranked:      { label: "Competitive",     color: C.violet },
    competitive: { label: "Competitive 1v1", color: C.coral },
  };
  const MODE_ORDER = ["normal", "team", "ranked", "competitive"];
  const SIZES = [2, 3, 4, 5, 6, 7, 8];
  const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  let _size = 0;          // the table size picked in "Table sizes" (0 = not yet)
  let _last = null;       // { m, d } of the last render, for redraws
  let _ro = null;
  let _roWidth = 0;

  // ── small helpers ───────────────────────────────────────────────────────
  function esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  const n0 = (v) => { const x = Number(v); return Number.isFinite(x) ? x : 0; };
  const fmt = (v) => Math.round(n0(v)).toLocaleString("en-US");
  const pct = (a, b) => (b > 0 ? Math.round((a / b) * 100) : 0);
  const sum = (obj) => Object.values(obj && typeof obj === "object" ? obj : {}).reduce((a, v) => a + n0(v), 0);
  const plural = (k, one, many) => `${fmt(k)} ${k === 1 ? one : (many || one + "s")}`;
  function fmtHours(h) {
    const x = n0(h);
    if (x <= 0) return "0 hrs";
    if (x < 1) return `${Math.max(1, Math.round(x * 60))} min`;
    const r = x < 10 ? Math.round(x * 10) / 10 : Math.round(x);
    return r === 1 ? "1 hr" : `${r.toLocaleString("en-US")} hrs`;
  }
  function ordinal(k) {
    const s = ["th", "st", "nd", "rd"], v = k % 100;
    return k + (s[(v - 20) % 10] || s[v] || s[0]);
  }
  function toMs(t) {
    if (typeof t === "number") return t;
    if (t && typeof t.seconds === "number") return t.seconds * 1000;
    if (t && typeof t.toMillis === "function") { try { return t.toMillis(); } catch (_) { return 0; } }
    const p = Date.parse(t);
    return Number.isFinite(p) ? p : 0;
  }
  const shortDate = (ms) => ms ? new Date(ms).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "";
  function dayKey(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  }
  function niceStep(range, count) {
    const raw = Math.max(range, 1) / Math.max(1, count);
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
  }
  function tipHtml(title, lines) {
    return `<b>${esc(title)}</b>` + (lines || []).filter(Boolean).map(l => `<span>${esc(l)}</span>`).join("");
  }
  const tipAttr = (title, lines) => `data-tip="${esc(tipHtml(title, lines))}"`;
  // A column with a 4px rounded data-end and a square foot on the baseline.
  function colPath(x, y, w, h, r) {
    if (h <= 0) return "";
    const rr = Math.min(r, h, w / 2);
    return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
  }

  // ── the numbers ─────────────────────────────────────────────────────────
  function model(d) {
    const s = (d && d.stats && typeof d.stats === "object") ? d.stats : {};
    const byNormal = sum(s.normal_games_by_size);
    const byComp = sum(s.comp_games_by_size);
    const totalGames = Math.max(n0(s.completed_games), byNormal + byComp);
    const normalWins = n0(s.normal_wins);
    const compW = n0(s.competitive_wins), compL = n0(s.competitive_losses), compD = n0(s.competitive_draws);
    const expTotal = Number(s.total_wins);
    const totalWins = Number.isFinite(expTotal) ? expTotal : normalWins + compW;
    // Both competitive modes count; only the 1v1 one writes comp_games_by_size.
    const compGames = Math.max(byComp, compW + compL + compD);
    const casualGames = byNormal || Math.max(0, totalGames - compGames);

    const recent = (Array.isArray(s.recent_games) ? s.recent_games : [])
      .filter(g => g && typeof g === "object")
      .map(g => {
        const all = Array.isArray(g.all) ? g.all.filter(p => p && typeof p === "object") : [];
        const score = n0(g.s);
        const mode = MODE[String(g.mode || "normal").toLowerCase()] ? String(g.mode || "normal").toLowerCase() : "normal";
        const pc = Math.round(n0(g.pc ?? g.player_count ?? g.total_players ?? (all.length || 0)));
        return {
          raw: g, t: toMs(g.t), score, win: Number(g.r) === 1, mode, pc,
          place: all.length ? all.filter(p => n0(p.s) > score).length + 1 : (Number(g.r) === 1 ? 1 : 0),
          seats: all.length || pc, strat: String(g.strat || "").trim(),
        };
      })
      .sort((a, b) => b.t - a.t);

    const scoreSum = n0(s.total_score);
    const avgScore = totalGames > 0 && scoreSum > 0 ? Math.round(scoreSum / Math.max(1, n0(s.completed_games) || totalGames)) : 0;
    const best = n0(s.highest_score);
    let bestSize = 0;
    SIZES.forEach(k => { if (best > 0 && n0(s[`highest_score_${k}p`]) === best && !bestSize) bestSize = k; });

    const sizes = SIZES.map(k => sizeRow(s, k, recent));

    // Strategies: the confirmed play counts, or failing that, what the last
    // games were detected as.
    let stratFrom = "confirmed";
    let strat = Object.entries(s.strategy_play_counts && typeof s.strategy_play_counts === "object" ? s.strategy_play_counts : {})
      .map(([label, c]) => ({ label: String(label || "").trim(), count: n0(c) }))
      .filter(e => e.label && e.count > 0);
    if (!strat.length) {
      stratFrom = "recent";
      const c = {};
      recent.forEach(g => { if (g.strat) c[g.strat] = (c[g.strat] || 0) + 1; });
      strat = Object.entries(c).map(([label, count]) => ({ label, count }));
    }
    strat.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));

    const modes = {};
    recent.forEach(g => { modes[g.mode] = (modes[g.mode] || 0) + 1; });

    const pt = s.playtime_by_mode && typeof s.playtime_by_mode === "object" ? s.playtime_by_mode : {};

    const streakDays = Array.isArray(s.streak_days) ? s.streak_days.map(String) : [];

    return {
      s, totalGames, totalWins, normalWins, casualGames, compGames, compW, compL, compD,
      winRate: pct(totalWins, totalGames), hours: n0(s.hours_played),
      matchHours: { normal: n0(pt.normal), competitive: n0(pt.competitive) },
      best, bestSize, avgScore, recent, sizes, strat, stratFrom, modes, streakDays,
      streak: n0(s.daily_streak), streakLongest: n0(s.streak_longest),
      hosted: n0(s.hosted_normal_games), tournaments: n0(s.tournament_wins),
    };
  }

  // One table size, by the old Casual tab's rules: the atomic per-size
  // counters first, the saved games at that size when a counter is missing.
  function sizeRow(s, size, recent) {
    const k = String(size);
    const list = recent.filter(g => g.pc === size && g.mode !== "competitive");
    const gAgg = n0((s.normal_games_by_size || {})[k]);
    const wRaw = (s.normal_wins_by_size || {})[k];
    const tsAgg = n0((s.total_score_by_size || {})[k]);
    const games = gAgg > 0 ? gAgg : list.length;
    const wins = (wRaw !== undefined && wRaw !== null) ? n0(wRaw) : list.filter(g => g.win).length;
    let avg = 0;
    if (games > 0) {
      if (tsAgg > 0) avg = Math.round(tsAgg / games);
      else if (list.length) avg = Math.round(list.reduce((a, g) => a + g.score, 0) / list.length);
    }
    const ptMap = s.normal_playtime_by_size && typeof s.normal_playtime_by_size === "object" ? s.normal_playtime_by_size : null;
    const hours = ptMap && Object.keys(ptMap).length ? n0(ptMap[k]) : null;
    const bySize = s.most_played_strategy_by_size && typeof s.most_played_strategy_by_size === "object" ? s.most_played_strategy_by_size : {};
    const strat = String(bySize[k] || s[`most_played_strategy_${size}p`] || "").trim();
    return { size, games, wins: Math.min(wins, games || wins), avg, hours, best: n0(s[`highest_score_${size}p`]), strat, list };
  }

  // ── strategy symbols: the family marks printed on the cards ───────────────
  const GUESS = [
    [/bird|puffin|pelican|gull/i, "bird"], [/crustacean|lobster|crab|shrimp/i, "crustacean"],
    [/cephalopod|octopus|squid|cuttle/i, "cephalopod"], [/mammal|dolphin|whale|narwhal/i, "mammal"],
    [/bait/i, "baitfish"], [/game fish|tuna|salmon|marlin/i, "game fish"], [/coral/i, "coral"],
    [/invertebrate|urchin|star|sponge|anemone/i, "invertebrate"], [/moon|cross/i, "crosscurrent"],
    [/ocean|blue|kelp|reef|pier|tide|mangrove|arctic/i, "ocean"],
  ];
  function symbolsHtml(label, d) {
    let syms = [];
    try {
      const list = Array.isArray(window.CC_BUILTIN_STRATEGIES) ? window.CC_BUILTIN_STRATEGIES : [];
      const i = list.findIndex(x => x && x.label === label);
      if (typeof window.CC_STRAT_SYMBOLS === "function") {
        if (i >= 0) syms = window.CC_STRAT_SYMBOLS(i) || [];
        else {
          const hit = GUESS.find(([re]) => re.test(label));
          syms = window.CC_STRAT_SYMBOLS(-1, { label, custom: true, cards: hit ? [{ species: hit[1] }] : [] }) || [];
        }
      }
    } catch (_) { syms = []; }
    if (!syms.length) return `<span class="cst-sym cst-sym-empty" aria-hidden="true"></span>`;
    const one = (e) => e.cardUid != null
      ? `<img class="cst-sym-card" src="${esc(d.oceanCardSrc || "")}" alt="${esc(e.label)}" loading="lazy" decoding="async">`
      : `<img src="/species/${esc(e.sym)}.png" alt="${esc(e.label)}" loading="lazy" decoding="async">`;
    return `<span class="cst-sym" data-n="${syms.length}" title="${esc(syms.map(e => e.label).join(" + "))}">`
      + syms.map(one).join(`<i aria-hidden="true">+</i>`) + `</span>`;
  }

  // ── page markup ─────────────────────────────────────────────────────────
  function card(cls, title, sub, body, extra) {
    return `<section class="cst-card ${cls || ""}">`
      + `<header class="cst-card-h"><div><h3 class="cst-h">${title}</h3>`
      + (sub ? `<p class="cst-hsub">${sub}</p>` : "") + `</div>${extra || ""}</header>`
      + body + `</section>`;
  }
  function legend(items) {
    return `<ul class="cst-legend">` + items.map(it =>
      `<li><i class="cst-key ${it.line ? "is-line" : ""}" style="--k:${it.color}"></i>${esc(it.label)}</li>`).join("") + `</ul>`;
  }

  function pageHtml(m, d) {
    const hasGames = m.totalGames > 0 || m.recent.length > 0;
    const last = m.recent[0];
    const head = `<header class="cst-head">`
      + `<div><h2 class="cst-title">Stats</h2>`
      + `<p class="cst-lede">Every game you play, charted. It all updates the moment a match ends.</p></div>`
      + `<div class="cst-head-meta">`
      + (last && last.t ? `<span class="cst-pill">Last game ${esc(shortDate(last.t))}</span>` : "")
      + `<span class="cst-pill">${hasGames ? plural(m.totalGames, "game") : "No games yet"}</span>`
      + `</div></header>`;

    const parts = [head, heroHtml(m, d)];
    if (hasGames) {
      parts.push(`<div class="cst-row">${tideCard(m)}${placesCard(m)}</div>`);
      parts.push(sizesCard(m, d));
      parts.push(`<div class="cst-row">${strategyCard(m, d)}${modesCard(m)}</div>`);
      parts.push(activityCard(m));
      parts.push(`<div class="cst-row">${compCard(m, d)}${ladderCard(d)}</div>`);
    } else {
      parts.push(`<section class="cst-card cst-empty">`
        + `<img src="/avatars/loggerhead-sea-turtle.png" alt="" loading="lazy" decoding="async">`
        + `<div><h3 class="cst-h">Your charts fill in as you play</h3>`
        + `<p>Finish a game and this page draws your score over time, where you finish, your record at every table size, the strategies you lean on and the days you play.</p>`
        + `<button type="button" class="cst-btn" data-act="play">Play Head to Head</button></div></section>`);
      parts.push(`<div class="cst-row">${compCard(m, d)}${ladderCard(d)}</div>`);
      parts.push(activityCard(m));
    }
    parts.push(collectionCard(m, d));
    parts.push(countersCard(m));
    return `<div class="cst">${parts.join("")}</div>`;
  }

  function heroHtml(m, d) {
    const lv = d.level || { level: 1, xpCurrent: 0, xpGoal: 1, totalXp: 0 };
    const frac = Math.max(0, Math.min(1, n0(lv.xpCurrent) / Math.max(1, n0(lv.xpGoal))));
    const R = 50, CIRC = 2 * Math.PI * R;
    const ring = `<svg class="cst-ring" viewBox="0 0 128 128" role="img" aria-label="Level ${esc(lv.level)}, ${fmt(lv.xpCurrent)} of ${fmt(lv.xpGoal)} XP">`
      + `<circle cx="64" cy="64" r="${R}" fill="none" stroke="#dbe8f8" stroke-width="11"/>`
      + (frac > 0 ? `<circle cx="64" cy="64" r="${R}" fill="none" stroke="${C.blue}" stroke-width="11" stroke-linecap="round"`
      + ` stroke-dasharray="${(CIRC * frac).toFixed(1)} ${CIRC.toFixed(1)}" transform="rotate(-90 64 64)"/>` : "")
      + `<text x="64" y="52" text-anchor="middle" class="cst-ring-k">LEVEL</text>`
      + `<text x="64" y="84" text-anchor="middle" class="cst-ring-v">${esc(lv.level)}</text></svg>`;
    const prestige = n0(d.prestige) > 0 ? `<span class="cst-pill is-gold">Prestige ${fmt(d.prestige)}</span>` : "";
    const levelBlock = `<div class="cst-level">${ring}<div class="cst-level-txt">`
      + `<div class="cst-level-title">${esc(d.levelTitle || "Ocean Explorer")}</div>`
      + `<div class="cst-level-xp">${lv.maxed ? "Top level reached" : `${fmt(lv.xpCurrent)} / ${fmt(lv.xpGoal)} XP to level ${fmt(n0(lv.level) + 1)}`}</div>`
      + `<div class="cst-level-total">${fmt(lv.totalXp)} total XP</div>${prestige}</div></div>`;

    const dash = "-";
    const has = m.totalGames > 0;
    const inMatch = m.matchHours.normal + m.matchHours.competitive;
    const wr = m.winRate;
    const WR = 19, WC = 2 * Math.PI * WR;
    const winRing = `<svg class="cst-mini-ring" viewBox="0 0 48 48" aria-hidden="true">`
      + `<circle cx="24" cy="24" r="${WR}" fill="none" stroke="#f5e3bb" stroke-width="6"/>`
      + (wr > 0 ? `<circle cx="24" cy="24" r="${WR}" fill="none" stroke="${C.gold}" stroke-width="6" stroke-linecap="round"`
      + ` stroke-dasharray="${(WC * wr / 100).toFixed(1)} ${WC.toFixed(1)}" transform="rotate(-90 24 24)"/>` : "") + `</svg>`;
    const spark = sparkline(m.recent.slice(0, 12).reverse().map(g => g.score));
    const tile = (label, value, sub, art, cls) =>
      `<div class="cst-kpi ${cls || ""}"><div class="cst-kpi-top"><span class="cst-kpi-l">${label}</span>${art || ""}</div>`
      + `<div class="cst-kpi-v">${value}</div><div class="cst-kpi-s">${sub || "&nbsp;"}</div></div>`;
    const kpis = [
      tile("Games played", has ? fmt(m.totalGames) : dash,
        has ? `${fmt(m.casualGames)} casual · ${fmt(m.compGames)} competitive` : "Play one to start"),
      tile("Wins", has ? fmt(m.totalWins) : dash,
        has ? `${fmt(m.normalWins)} casual · ${fmt(m.compW)} competitive` : ""),
      tile("Win rate", has ? `${wr}%` : dash, has ? `of ${plural(m.totalGames, "game")}` : "", has ? winRing : "", "has-art"),
      tile("Hours played", fmtHours(m.hours),
        inMatch > 0 ? `${fmtHours(inMatch)} of it in matches` : "with the game open"),
      tile("Best score", m.best > 0 ? fmt(m.best) : dash,
        m.best > 0 && m.bestSize ? `in a ${m.bestSize}-player game` : ""),
      tile("Average score", m.avgScore > 0 ? fmt(m.avgScore) : dash,
        spark ? "last 12 games" : "", spark, "has-art"),
    ];
    return `<section class="cst-card cst-hero">${levelBlock}<div class="cst-kpis">${kpis.join("")}</div></section>`;
  }

  function sparkline(vals) {
    if (vals.length < 3) return "";
    const W = 76, H = 30, lo = Math.min(...vals), hi = Math.max(...vals), span = Math.max(1, hi - lo);
    const pts = vals.map((v, i) => [3 + (i * (W - 6)) / (vals.length - 1), 4 + (H - 8) * (1 - (v - lo) / span)]);
    const [lx, ly] = pts[pts.length - 1];
    return `<svg class="cst-spark" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true">`
      + `<polyline points="${pts.map(p => p.map(x => x.toFixed(1)).join(",")).join(" ")}" fill="none" stroke="#9cc5ee" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`
      + `<circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="3.5" fill="${C.blue}" stroke="${C.surface}" stroke-width="1.5"/></svg>`;
  }

  function tideCard(m) {
    const games = m.recent.slice(0, 50);
    const rows = games.map(g => `<tr><td>${esc(shortDate(g.t))}</td><td>${g.win ? "Win" : (g.place ? ordinal(g.place) : "-")}</td>`
      + `<td>${fmt(g.score)}</td><td>${g.pc || "-"}</td><td>${esc(MODE[g.mode].label)}</td><td>${esc(g.strat || "-")}</td></tr>`).join("");
    const body = legend([{ label: "Win", color: C.gold }, { label: "Other finish", color: C.blue },
      { label: "5-game average", color: C.teal, line: true }])
      + `<div class="cst-chart" data-chart="tide"></div>`
      + `<details class="cst-table"><summary>Show as a table</summary><div class="cst-table-scroll"><table>`
      + `<thead><tr><th>Date</th><th>Finish</th><th>Score</th><th>Players</th><th>Mode</th><th>Strategy</th></tr></thead>`
      + `<tbody>${rows}</tbody></table></div></details>`;
    return card("cst-span-8", "Score Tide", `Your score in each of your last ${plural(games.length, "game")}, oldest to newest.`, body);
  }

  function placesCard(m) {
    const counted = m.recent.filter(g => g.place > 0 && g.mode !== "competitive");
    const body = counted.length
      ? `<div class="cst-chart" data-chart="places"></div>`
      : `<p class="cst-none">Finishing places show up once you have finished a casual game.</p>`;
    const wins = counted.filter(g => g.place === 1).length;
    const extra = counted.length ? `<div class="cst-stat-inline"><b>${pct(wins, counted.length)}%</b><span>finished 1st</span></div>` : "";
    return card("cst-span-4", "Where You Finish", counted.length ? `Placings in your last ${plural(counted.length, "game")}` : "", body, extra);
  }

  function sizesCard(m, d) {
    const body = `<div class="cst-sizes">`
      + `<div class="cst-sizes-chart">${legend([{ label: "Wins", color: C.gold }, { label: "Other games", color: C.blue }])}`
      + `<div class="cst-chart" data-chart="sizes"></div></div>`
      + `<div class="cst-size-detail" data-size-detail></div></div>`;
    return card("cst-span-12", "Table Sizes", "Your casual record at every table size. Pick a size to see it up close.", body);
  }

  function sizeDetailHtml(m, d) {
    const row = m.sizes.find(r => r.size === _size) || m.sizes[2];
    const chips = `<div class="cst-chips" role="group" aria-label="Table size">` + SIZES.map(k =>
      `<button type="button" class="cst-chip ${k === row.size ? "is-on" : ""}" data-size="${k}" aria-pressed="${k === row.size}">${k}P</button>`).join("") + `</div>`;
    const wr = row.games > 0 ? `${pct(row.wins, row.games)}%` : "-";
    const t = (label, value) => `<div class="cst-mini"><span>${label}</span><b>${value}</b></div>`;
    return chips
      + `<h4 class="cst-size-h">${row.size}-player games</h4>`
      + `<div class="cst-minis">`
      + t("Win rate", wr) + t("Games", fmt(row.games)) + t("Wins", fmt(row.wins))
      + t("Avg points", row.avg > 0 ? fmt(row.avg) : "-") + t("Best score", row.best > 0 ? fmt(row.best) : "-")
      + t("Hours played", row.hours == null ? fmtHours(m.hours) : fmtHours(row.hours))
      + `</div>`
      + `<div class="cst-size-strat"><span class="cst-size-strat-ico" data-strat-icon>${row.strat ? symbolsHtml(row.strat, d) : `<span class="cst-sym cst-sym-empty"></span>`}</span>`
      + `<div><span>Most played strategy</span><b>${esc(row.strat || (row.games > 0 ? "Not recorded yet" : "No games at this size yet"))}</b></div></div>`
      + `<div class="cst-size-recent"><div class="cst-size-recent-h"><span>Recent ${row.size}-player games</span>`
      + `<button type="button" class="cst-link" data-act="history">Full history</button></div><div data-size-recent></div></div>`;
  }

  function strategyCard(m, d) {
    if (!m.strat.length) {
      return card("cst-span-7", "Strategy Playbook", "", `<p class="cst-none">Confirm your strategy at the end of a game and it is counted here.</p>`);
    }
    const top = m.strat.slice(0, 8);
    const rest = m.strat.slice(8);
    const restCount = rest.reduce((a, e) => a + e.count, 0);
    const total = m.strat.reduce((a, e) => a + e.count, 0);
    const max = Math.max(...top.map(e => e.count), restCount);
    const bar = (label, count, symHtml, isTop) =>
      `<li class="cst-bar-row" tabindex="0" ${tipAttr(label, [plural(count, "game"), `${pct(count, total)}% of your strategies`])}>`
      + `${symHtml}<span class="cst-bar-label">${esc(label)}${isTop ? ` <em>Most played</em>` : ""}</span>`
      + `<span class="cst-bar-track"><span class="cst-bar-fill" style="width:${Math.max(2, (count / max) * 100).toFixed(1)}%"></span></span>`
      + `<span class="cst-bar-val">${fmt(count)} <small>${pct(count, total)}%</small></span></li>`;
    const body = `<ul class="cst-bars">`
      + top.map((e, i) => bar(e.label, e.count, symbolsHtml(e.label, d), i === 0)).join("")
      + (restCount > 0 ? bar(`${rest.length} other strategies`, restCount, `<span class="cst-sym cst-sym-empty" aria-hidden="true"></span>`, false) : "")
      + `</ul>`;
    const sub = m.stratFrom === "confirmed"
      ? `The plans you confirmed at the end of ${plural(total, "game")}.`
      : `What your last ${plural(total, "game")} were built as.`;
    return card("cst-span-7", "Strategy Playbook", sub, body);
  }

  function modesCard(m) {
    const total = MODE_ORDER.reduce((a, k) => a + (m.modes[k] || 0), 0);
    let mix = `<p class="cst-none">Modes show up after your first game.</p>`;
    if (total > 0) {
      const segs = MODE_ORDER.filter(k => m.modes[k] > 0);
      mix = `<div class="cst-stack" role="img" aria-label="${esc(segs.map(k => `${MODE[k].label} ${m.modes[k]}`).join(", "))}">`
        + segs.map(k => `<span class="cst-seg" tabindex="0" style="flex:${m.modes[k]};--k:${MODE[k].color}" `
          + `${tipAttr(MODE[k].label, [plural(m.modes[k], "game"), `${pct(m.modes[k], total)}% of your last ${total}`])}></span>`).join("")
        + `</div><ul class="cst-stack-legend">`
        + segs.map(k => `<li><i class="cst-key" style="--k:${MODE[k].color}"></i><span>${esc(MODE[k].label)}</span><b>${fmt(m.modes[k])}</b><small>${pct(m.modes[k], total)}%</small></li>`).join("")
        + `</ul>`;
    }
    const mh = m.matchHours, mhMax = Math.max(mh.normal, mh.competitive, 0.0001);
    const hourRow = (label, h, color) => `<li tabindex="0" ${tipAttr(label, [`${fmtHours(h)} in matches`])}>`
      + `<span class="cst-hbar-l">${label}</span><span class="cst-bar-track"><span class="cst-bar-fill" style="width:${Math.max(h > 0 ? 2 : 0, (h / mhMax) * 100).toFixed(1)}%;background:${color}"></span></span>`
      + `<span class="cst-bar-val">${fmtHours(h)}</span></li>`;
    const hours = (mh.normal + mh.competitive) > 0
      ? `<ul class="cst-hbars">${hourRow("Casual", mh.normal, C.blue)}${hourRow("Competitive", mh.competitive, C.coral)}</ul>`
      : `<p class="cst-none">Time in matches is counted from your next game.</p>`;
    const body = `<h4 class="cst-subh">${total > 0 ? `Modes in your last ${plural(total, "game")}` : "Modes"}</h4>${mix}`
      + `<h4 class="cst-subh">Time in matches</h4>${hours}`
      + `<div class="cst-minis cst-minis-2">`
      + `<div class="cst-mini"><span>Games you hosted</span><b>${fmt(m.hosted)}</b></div>`
      + `<div class="cst-mini"><span>Tournaments won</span><b>${fmt(m.tournaments)}</b></div></div>`;
    return card("cst-span-5", "How You Play", "", body);
  }

  function activityCard(m) {
    const days = new Set(m.streakDays);
    const byDow = [0, 0, 0, 0, 0, 0, 0];
    days.forEach(k => {
      const dt = new Date(k + "T12:00:00");
      if (!Number.isNaN(dt.getTime())) byDow[(dt.getDay() + 6) % 7]++;
    });
    const maxDow = Math.max(...byDow);
    const topDow = maxDow > 0 ? byDow.indexOf(maxDow) : -1;
    const dowFull = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"];
    const side = `<div class="cst-act-side">`
      + `<div class="cst-mini is-hot"><span>Current streak</span><b>${plural(m.streak, "day")}</b></div>`
      + `<div class="cst-mini"><span>Longest streak</span><b>${plural(m.streakLongest, "day")}</b></div>`
      + `<div class="cst-mini"><span>Days played</span><b>${fmt(days.size)}</b></div>`
      + `<div class="cst-dow"><span class="cst-dow-h">${topDow >= 0 ? `You play most on ${dowFull[topDow]}` : "Days of the week"}</span>`
      + `<div class="cst-chart" data-chart="dow"></div></div></div>`;
    const body = `<div class="cst-act"><div class="cst-act-main"><div class="cst-chart" data-chart="heat"></div>`
      + `<div class="cst-heat-legend" aria-hidden="true"><span>Less</span><i style="background:${HEAT_EMPTY}"></i>`
      + HEAT.map(c => `<i style="background:${c}"></i>`).join("") + `<span>More</span></div></div>${side}</div>`;
    return card("cst-span-12", "Tide Calendar", "The days you played, darker for the days you saved more games.", body);
  }

  function compCard(m, d) {
    const total = m.compW + m.compL + m.compD;
    const r = d.rank;
    if (!total && !r) {
      return card("cst-span-6 cst-comp", "Competitive", "",
        `<div class="cst-comp-empty"><p class="cst-none">No ranked games yet. Win Competitive games to climb from Bronze Barracuda to King of the Critters.</p>`
        + `<button type="button" class="cst-btn" data-act="competitive">Open Competitive</button></div>`);
    }
    const s = m.s;
    const toNext = r && r.nextCp != null ? `${fmt(r.nextCp - r.cp)} OP until ${esc(r.nextDiv)}` : "Top rank reached";
    const rankBlock = r ? `<div class="cst-rank">`
      + (r.icon ? `<img src="${esc(r.icon)}" alt="" class="cst-rank-img tier-${esc(r.tier)}" loading="lazy" decoding="async">` : "")
      + `<div class="cst-rank-txt"><b>${esc(r.division === "Unranked" ? "No rank yet" : r.division)}</b>`
      + `<span>${fmt(r.cp)} OP · ${toNext}</span>`
      + `<span class="cst-meter" role="img" aria-label="${n0(r.pct)}% of the way to the next division"><span style="width:${Math.max(0, Math.min(100, n0(r.pct)))}%"></span></span></div></div>` : "";
    const seg = (label, v, color) => v > 0
      ? `<span class="cst-seg" tabindex="0" style="flex:${v};--k:${color}" ${tipAttr(label, [plural(v, "game"), `${pct(v, total)}% of ${total}`])}></span>` : "";
    const wdl = total > 0 ? `<div class="cst-stack">${seg("Wins", m.compW, C.gold)}${seg("Draws", m.compD, C.teal)}${seg("Losses", m.compL, C.slate)}</div>`
      + `<ul class="cst-stack-legend">`
      + `<li><i class="cst-key" style="--k:${C.gold}"></i><span>Wins</span><b>${fmt(m.compW)}</b><small>${pct(m.compW, total)}%</small></li>`
      + `<li><i class="cst-key" style="--k:${C.teal}"></i><span>Draws</span><b>${fmt(m.compD)}</b><small>${pct(m.compD, total)}%</small></li>`
      + `<li><i class="cst-key" style="--k:${C.slate}"></i><span>Losses</span><b>${fmt(m.compL)}</b><small>${pct(m.compL, total)}%</small></li></ul>` : "";
    const mini = (label, v) => `<div class="cst-mini"><span>${label}</span><b>${v}</b></div>`;
    const body = rankBlock + `<h4 class="cst-subh">Record this season</h4>` + wdl
      + `<div class="cst-minis">`
      + mini("Best score", n0(s.highest_score_competitive) > 0 ? fmt(s.highest_score_competitive) : "-")
      + mini("Average score", n0(s.average_competitive_score) > 0 ? fmt(s.average_competitive_score) : "-")
      + mini("Win streak", fmt(s.competitive_streak))
      + mini("Best streak", fmt(s.competitive_best_streak))
      + `</div>`;
    return card("cst-span-6 cst-comp", "Competitive", "", body,
      `<button type="button" class="cst-link" data-act="competitive">Open Competitive</button>`);
  }

  function ladderCard(d) {
    const ladder = Array.isArray(d.ladder) ? d.ladder : [];
    if (!ladder.length) return "";
    const beaten = ladder.filter(t => t.beaten).length;
    const nextIdx = ladder.findIndex(t => !t.beaten);
    const body = `<ol class="cst-ladder">` + ladder.map((t, i) => {
      const state = t.beaten ? "is-beaten" : (i === nextIdx ? "is-next" : "is-locked");
      const status = t.beaten ? "Beaten" : (i === nextIdx ? "Up next" : "Not reached yet");
      return `<li class="cst-rung ${state}" tabindex="0" ${tipAttr(`${t.name}`, [`Rank ${t.tier}`, status])}>`
        + `<span class="cst-rung-art"><img src="${esc(t.img)}" alt="" loading="lazy" decoding="async">`
        + (t.beaten ? `<i class="cst-rung-check" aria-hidden="true">✓</i>` : "") + `</span>`
        + `<span class="cst-rung-tier">${esc(t.tier)}</span><span class="cst-sr">${esc(t.name)}: ${status}</span></li>`;
    }).join("") + `</ol>`
      + `<div class="cst-ladder-foot"><span class="cst-meter is-gold" role="img" aria-label="${beaten} of ${ladder.length} rungs beaten"><span style="width:${pct(beaten, ladder.length)}%"></span></span>`
      + `<span>${beaten === ladder.length ? "You have beaten the whole reef, Giant Squid and all." : nextIdx >= 0 ? `Next up: the ${esc(ladder[nextIdx].name)}` : ""}</span></div>`;
    return card("cst-span-6", "Head to Head Reef", `${beaten} of ${ladder.length} bots beaten`, body,
      `<button type="button" class="cst-link" data-act="play">Climb</button>`);
  }

  function collectionCard(m, d) {
    const meter = (label, done, total, art, cls) => {
      const p = pct(done, total);
      return `<div class="cst-collect ${cls || ""}"><img src="${esc(art)}" alt="" loading="lazy" decoding="async">`
        + `<div><div class="cst-collect-top"><span>${label}</span><b>${fmt(done)} <small>/ ${fmt(total)}</small></b></div>`
        + `<span class="cst-meter" role="img" aria-label="${p}%"><span style="width:${p}%"></span></span>`
        + `<small class="cst-collect-p">${p}% collected</small></div></div>`;
    };
    const a = d.achievements || { done: 0, total: 0 };
    const c = d.animals || { done: 0, total: 0 };
    const body = `<div class="cst-collects">`
      + (a.total ? meter("Achievements", a.done, a.total, "/avatars/manta-ray.png") : "")
      + (c.total ? meter("Critters unlocked", c.done, c.total, "/avatars/clownfish.png") : "")
      + `</div>`;
    return card("cst-span-12", "Collection", "", body);
  }

  function countersCard(m) {
    const s = m.s;
    const items = [
      ["Cards drawn from the deck", s.lifetime_deck_draws, "/card-back.png", true],
      ["Baitfish played", s.lifetime_baitfish_played, "/avatars/sardine.png"],
      ["Free baitfish taken", s.lifetime_free_baitfish, "/avatars/bunker.png"],
      ["Play Again turns", s.lifetime_play_again, "/avatars/flying-fish.png"],
      ["Sea Urchin draws", s.lifetime_sea_urchin_draws, "/avatars/sea-urchin.png"],
      ["Sea Star draws", s.lifetime_sea_star_draws, "/avatars/sea-star.png"],
      ["Sea Cucumber draws", s.lifetime_sea_cucumber_draws, "/avatars/sea-cucumber.png"],
      ["Sea Cucumbers played", s.lifetime_sea_cucumber_played, "/avatars/sea-cucumber.png"],
      ["Sea Anemone draws", s.lifetime_sea_anemone_draws, "/avatars/sea-anemone.png"],
      ["Sea Anemones played", s.lifetime_sea_anemone_plays, "/avatars/sea-anemone.png"],
    ];
    const body = `<ul class="cst-counters">` + items.map(([label, v, art, isCard]) =>
      `<li><img src="${esc(art)}" alt="" class="${isCard ? "is-card" : ""}" loading="lazy" decoding="async">`
      + `<b>${fmt(v)}</b><span>${label}</span></li>`).join("") + `</ul>`;
    return card("cst-span-12", "Around the Reef", "Lifetime counts from every game on this account.", body);
  }

  // ── charts ──────────────────────────────────────────────────────────────
  function svgOpen(W, H, label) {
    return `<svg class="cst-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">`;
  }

  function drawTide(host, m) {
    const games = m.recent.slice(0, 50).reverse();
    const W = Math.max(260, host.clientWidth);
    const H = W < 520 ? 210 : 250;
    if (!games.length) { host.innerHTML = `<p class="cst-none">No saved games yet.</p>`; return; }
    const padL = 40, padR = W < 520 ? 44 : 60, padT = 26, padB = 30;
    const pw = W - padL - padR, ph = H - padT - padB;
    const scores = games.map(g => g.score);
    let lo = Math.min(...scores), hi = Math.max(...scores);
    const step = niceStep((hi - lo) || 50, 4);
    let y0 = Math.max(0, Math.floor(lo / step) * step), y1 = Math.ceil(hi / step) * step;
    if (y1 <= y0) y1 = y0 + step;
    const N = games.length;
    const x = (i) => padL + (N === 1 ? pw / 2 : (i * pw) / (N - 1));
    const y = (v) => padT + ph * (1 - (v - y0) / (y1 - y0));
    const avg = scores.map((_, i) => {
      const w = scores.slice(Math.max(0, i - 4), i + 1);
      return w.reduce((a, v) => a + v, 0) / w.length;
    });
    let out = svgOpen(W, H, `Score in each of the last ${N} games, from ${fmt(lo)} to ${fmt(hi)}`);
    for (let v = y0; v <= y1 + 0.001; v += step) {
      out += `<line x1="${padL}" x2="${W - padR + 8}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" stroke="${C.grid}" stroke-width="1"/>`
        + `<text x="${padL - 8}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end" class="cst-axis">${fmt(v)}</text>`;
    }
    if (N > 1) {
      const line = games.map((g, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(g.score).toFixed(1)}`).join("");
      out += `<path d="${line}L${x(N - 1).toFixed(1)},${(padT + ph).toFixed(1)}L${x(0).toFixed(1)},${(padT + ph).toFixed(1)}Z" fill="${C.blue}" fill-opacity=".1"/>`;
      out += `<path d="${line}" fill="none" stroke="${C.blue}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" stroke-opacity=".55"/>`;
      out += `<path d="${avg.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("")}" fill="none" stroke="${C.teal}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    }
    out += `<line class="cst-cross" x1="0" x2="0" y1="${padT}" y2="${padT + ph}" stroke="${C.ink2}" stroke-width="1" opacity="0"/>`;
    games.forEach((g, i) => {
      out += `<circle cx="${x(i).toFixed(1)}" cy="${y(g.score).toFixed(1)}" r="4.5" fill="${g.win ? C.gold : C.blue}" stroke="${C.surface}" stroke-width="2"/>`;
    });
    out += `<circle class="cst-focus-dot" cx="0" cy="0" r="7" fill="none" stroke="${C.ink}" stroke-width="2" opacity="0"/>`;
    // Direct labels: the best game and the latest one, nothing else.
    const bi = scores.indexOf(hi), li = N - 1;
    const lab = (i, text, anchor, dx, dy) =>
      `<text x="${(x(i) + dx).toFixed(1)}" y="${(y(scores[i]) + dy).toFixed(1)}" text-anchor="${anchor}" class="cst-dlabel">${esc(text)}</text>`;
    out += lab(li, bi === li ? `${fmt(scores[li])} best` : fmt(scores[li]), "start", 10, 4);
    if (bi !== li) {
      const anchor = x(bi) < padL + 40 ? "start" : (x(bi) > W - padR - 40 ? "end" : "middle");
      const close = Math.abs(x(bi) - x(li)) < 70 && Math.abs(y(scores[bi]) - y(scores[li])) < 18;
      if (!close) out += lab(bi, `Best ${fmt(hi)}`, anchor, 0, -11);
    }
    out += `<text x="${padL}" y="${H - 8}" class="cst-axis">${esc(shortDate(games[0].t))}</text>`
      + `<text x="${W - padR}" y="${H - 8}" text-anchor="end" class="cst-axis">${esc(shortDate(games[N - 1].t))}</text>`;
    out += `<rect class="cst-hit" x="${padL - 12}" y="${padT - 10}" width="${pw + 24}" height="${ph + 20}" fill="transparent" tabindex="0" aria-label="Score chart. Use the arrow keys to step through games."/>`;
    out += `</svg>`;
    host.innerHTML = out;

    const svg = host.querySelector("svg");
    const hit = svg.querySelector(".cst-hit");
    const cross = svg.querySelector(".cst-cross");
    const dot = svg.querySelector(".cst-focus-dot");
    let cur = -1;
    const show = (i, clientX, clientY) => {
      cur = Math.max(0, Math.min(N - 1, i));
      const g = games[cur];
      cross.setAttribute("x1", x(cur)); cross.setAttribute("x2", x(cur)); cross.setAttribute("opacity", ".35");
      dot.setAttribute("cx", x(cur)); dot.setAttribute("cy", y(g.score)); dot.setAttribute("opacity", "1");
      const finish = g.win ? "Win" : (g.place ? `${ordinal(g.place)} of ${g.seats || g.pc}` : "Loss");
      const lines = [`${fmt(g.score)} points · ${finish}`, `${g.pc ? g.pc + " players · " : ""}${MODE[g.mode].label}`,
        g.strat ? `Strategy: ${g.strat}` : "", `5-game average ${fmt(avg[cur])}`];
      const r = svg.getBoundingClientRect();
      showTip(tipHtml(shortDate(g.t) || `Game ${cur + 1}`, lines),
        clientX != null ? clientX : r.left + x(cur), clientY != null ? clientY : r.top + y(g.score));
    };
    const fromEvent = (e) => {
      const r = svg.getBoundingClientRect();
      const px = e.clientX - r.left;
      return N === 1 ? 0 : Math.round(((px - padL) / pw) * (N - 1));
    };
    const clear = () => { cross.setAttribute("opacity", "0"); dot.setAttribute("opacity", "0"); hideTip(); };
    hit.addEventListener("pointermove", (e) => show(fromEvent(e), e.clientX, e.clientY));
    hit.addEventListener("pointerdown", (e) => show(fromEvent(e), e.clientX, e.clientY));
    hit.addEventListener("pointerleave", clear);
    hit.addEventListener("blur", clear);
    hit.addEventListener("focus", () => show(cur < 0 ? N - 1 : cur));
    hit.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        show((cur < 0 ? N - 1 : cur) + (e.key === "ArrowLeft" ? -1 : 1));
      }
    });
  }

  function drawPlaces(host, m) {
    const counted = m.recent.filter(g => g.place > 0 && g.mode !== "competitive");
    if (!counted.length) return;
    const bins = [0, 0, 0, 0, 0];
    counted.forEach(g => { bins[Math.min(5, g.place) - 1]++; });
    const labels = ["1st", "2nd", "3rd", "4th", "5th+"];
    const W = Math.max(220, host.clientWidth), H = 220;
    const padL = 8, padR = 8, padT = 26, padB = 44;
    const pw = W - padL - padR, ph = H - padT - padB;
    const band = pw / bins.length, bw = Math.min(26, band * 0.56);
    const max = Math.max(...bins, 1);
    let out = svgOpen(W, H, `Finishing places: ${bins.map((v, i) => `${labels[i]} ${v}`).join(", ")}`);
    out += `<line x1="${padL}" x2="${W - padR}" y1="${padT + ph}" y2="${padT + ph}" stroke="${C.grid}" stroke-width="1"/>`;
    bins.forEach((v, i) => {
      const cx = padL + band * i + band / 2;
      const h = (v / max) * ph;
      const top = padT + ph - h;
      const tip = tipAttr(`${labels[i]} place`, [plural(v, "game"), `${pct(v, counted.length)}% of ${counted.length}`]);
      out += `<g class="cst-mark" tabindex="0" ${tip}>`
        + `<rect x="${(cx - band / 2 + 2).toFixed(1)}" y="${padT - 6}" width="${(band - 4).toFixed(1)}" height="${ph + 6}" fill="transparent"/>`
        + (v > 0 ? `<path d="${colPath(cx - bw / 2, top, bw, h, 4)}" fill="${i === 0 ? C.gold : C.blue}"/>` : "")
        + `<text x="${cx.toFixed(1)}" y="${(top - 7).toFixed(1)}" text-anchor="middle" class="cst-dlabel">${fmt(v)}</text>`
        + `<text x="${cx.toFixed(1)}" y="${padT + ph + 18}" text-anchor="middle" class="cst-axis is-strong">${labels[i]}</text>`
        + `<text x="${cx.toFixed(1)}" y="${padT + ph + 34}" text-anchor="middle" class="cst-axis">${pct(v, counted.length)}%</text></g>`;
    });
    host.innerHTML = out + `</svg>`;
  }

  function drawSizes(host, m) {
    const W = Math.max(260, host.clientWidth);
    let H = 240;
    const box = host.closest(".cst-sizes");
    const detail = box && box.querySelector("[data-size-detail]");
    if (detail && detail.offsetHeight && Math.abs(detail.getBoundingClientRect().top - box.getBoundingClientRect().top) < 4) {
      const above = host.getBoundingClientRect().top - box.getBoundingClientRect().top;
      H = Math.max(240, Math.min(520, Math.round(detail.offsetHeight - above)));
    }
    const padL = 36, padR = 8, padT = 26, padB = 46;
    const pw = W - padL - padR, ph = H - padT - padB;
    const rows = m.sizes;
    const max = Math.max(1, ...rows.map(r => r.games));
    const step = niceStep(max, 3);
    const top = Math.ceil(max / step) * step;
    const band = pw / rows.length, bw = Math.min(28, band * 0.5);
    const y = (v) => padT + ph * (1 - v / top);
    let out = svgOpen(W, H, `Games and wins at each table size: ${rows.map(r => `${r.size} players ${r.games} games ${r.wins} wins`).join("; ")}`);
    for (let v = 0; v <= top + 0.001; v += step) {
      out += `<line x1="${padL}" x2="${W - padR}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" stroke="${C.grid}" stroke-width="1"/>`
        + `<text x="${padL - 8}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end" class="cst-axis">${fmt(v)}</text>`;
    }
    rows.forEach((r, i) => {
      const cx = padL + band * i + band / 2;
      const on = r.size === _size;
      const wins = Math.min(r.wins, r.games), other = Math.max(0, r.games - wins);
      const hW = (wins / top) * ph, hO = (other / top) * ph;
      const base = padT + ph;
      const gap = wins > 0 && other > 0 ? 2 : 0;
      const tip = tipAttr(`${r.size}-player games`, [plural(r.games, "game"), `${plural(wins, "win")}${r.games ? ` (${pct(wins, r.games)}%)` : ""}`, r.avg ? `${fmt(r.avg)} points on average` : ""]);
      out += `<g class="cst-mark cst-size-col ${on ? "is-on" : ""}" data-size="${r.size}" role="button" tabindex="0" aria-pressed="${on}" ${tip}>`
        + `<rect x="${(cx - band / 2 + 3).toFixed(1)}" y="${padT - 18}" width="${(band - 6).toFixed(1)}" height="${ph + 18 + padB - 6}" rx="12" fill="${on ? C.band : "transparent"}"/>`;
      if (wins > 0) {
        out += other > 0
          ? `<rect x="${(cx - bw / 2).toFixed(1)}" y="${(base - hW).toFixed(1)}" width="${bw.toFixed(1)}" height="${hW.toFixed(1)}" fill="${C.gold}"/>`
          : `<path d="${colPath(cx - bw / 2, base - hW, bw, hW, 4)}" fill="${C.gold}"/>`;
      }
      // The 2px surface gap comes out of the top segment, so the stack still
      // ends exactly at its total on the scale.
      if (other > 0) out += `<path d="${colPath(cx - bw / 2, base - hW - hO, bw, Math.max(1, hO - gap), 4)}" fill="${C.blue}"/>`;
      const capY = base - hW - hO;
      out += `<text x="${cx.toFixed(1)}" y="${(capY - 7).toFixed(1)}" text-anchor="middle" class="cst-dlabel ${r.games ? "" : "is-muted"}">${fmt(r.games)}</text>`
        + `<text x="${cx.toFixed(1)}" y="${base + 18}" text-anchor="middle" class="cst-axis is-strong">${r.size}P</text>`
        + `<text x="${cx.toFixed(1)}" y="${base + 34}" text-anchor="middle" class="cst-axis">${r.games ? pct(wins, r.games) + (band < 64 ? "%" : "% won") : "-"}</text></g>`;
    });
    host.innerHTML = out + `</svg>`;
  }

  function drawHeat(host, m) {
    const W = Math.max(240, host.clientWidth);
    const labelW = 30, gap = 3;
    const weeks = Math.max(10, Math.min(52, Math.floor((W - labelW + gap) / (13 + gap))));
    const cell = Math.max(9, Math.min(18, Math.floor((W - labelW) / weeks) - gap));
    const padT = 20;
    const H = padT + 7 * (cell + gap);
    const gamesByDay = {};
    m.recent.forEach(g => { if (g.t) { const k = dayKey(new Date(g.t)); gamesByDay[k] = (gamesByDay[k] || 0) + 1; } });
    const played = new Set(m.streakDays);
    const today = new Date(); today.setHours(12, 0, 0, 0);
    const dow = (today.getDay() + 6) % 7;
    const start = new Date(today); start.setDate(today.getDate() - dow - (weeks - 1) * 7);
    let out = svgOpen(W, H, `Calendar of the last ${weeks} weeks: ${played.size} days played in total`);
    [0, 2, 4].forEach(r => {
      out += `<text x="0" y="${padT + r * (cell + gap) + cell - 1}" class="cst-axis">${WEEKDAYS[r]}</text>`;
    });
    let lastMonth = -1;
    for (let w = 0; w < weeks; w++) {
      const colDay = new Date(start); colDay.setDate(start.getDate() + w * 7);
      const xw = labelW + w * (cell + gap);
      if (colDay.getMonth() !== lastMonth) {
        if (w < weeks - 1) out += `<text x="${xw}" y="12" class="cst-axis">${colDay.toLocaleDateString("en-US", { month: "short" })}</text>`;
        lastMonth = colDay.getMonth();
      }
      for (let r = 0; r < 7; r++) {
        const dt = new Date(start); dt.setDate(start.getDate() + w * 7 + r);
        if (dt > today) continue;
        const k = dayKey(dt);
        const g = gamesByDay[k] || 0;
        const isPlayed = g > 0 || played.has(k);
        const lvl = g >= 5 ? 3 : g >= 3 ? 2 : g >= 2 ? 1 : isPlayed ? 0 : -1;
        const label = dt.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
        const detail = g > 0 ? plural(g, "saved game") : (isPlayed ? "Played" : "No games");
        out += `<rect class="cst-mark" x="${xw}" y="${padT + r * (cell + gap)}" width="${cell}" height="${cell}" rx="3" `
          + `fill="${lvl < 0 ? HEAT_EMPTY : HEAT[lvl]}" ${tipAttr(label, [detail])}/>`;
      }
    }
    host.innerHTML = out + `</svg>`;
  }

  function drawDow(host, m) {
    const byDow = [0, 0, 0, 0, 0, 0, 0];
    new Set(m.streakDays).forEach(k => {
      const dt = new Date(k + "T12:00:00");
      if (!Number.isNaN(dt.getTime())) byDow[(dt.getDay() + 6) % 7]++;
    });
    const W = Math.max(180, host.clientWidth), H = 96, padT = 16, padB = 18;
    const ph = H - padT - padB, band = W / 7, bw = Math.min(18, band * 0.55);
    const max = Math.max(1, ...byDow);
    const top = byDow.indexOf(Math.max(...byDow));
    let out = svgOpen(W, H, `Days played by weekday: ${byDow.map((v, i) => `${WEEKDAYS[i]} ${v}`).join(", ")}`);
    byDow.forEach((v, i) => {
      const cx = band * i + band / 2, h = (v / max) * ph, yTop = padT + ph - h;
      out += `<g class="cst-mark" tabindex="0" ${tipAttr(WEEKDAYS[i], [plural(v, "day") + " played"])}>`
        + `<rect x="${(cx - band / 2 + 1).toFixed(1)}" y="0" width="${(band - 2).toFixed(1)}" height="${H}" fill="transparent"/>`
        + (v > 0 ? `<path d="${colPath(cx - bw / 2, yTop, bw, h, 4)}" fill="${i === top && v > 0 ? C.blue : "#9cc5ee"}"/>` : "")
        + `<text x="${cx.toFixed(1)}" y="${H - 3}" text-anchor="middle" class="cst-axis ${i === top && v > 0 ? "is-strong" : ""}">${WEEKDAYS[i].charAt(0)}</text></g>`;
    });
    host.innerHTML = out + `</svg>`;
  }

  function drawCharts(root, m) {
    const map = { tide: drawTide, places: drawPlaces, sizes: drawSizes, heat: drawHeat, dow: drawDow };
    root.querySelectorAll("[data-chart]").forEach(host => {
      const fn = map[host.dataset.chart];
      if (!fn) return;
      try { fn(host, m); } catch (err) {
        host.innerHTML = `<p class="cst-none">This chart could not be drawn.</p>`;
        try { console.error("[stats] chart failed:", host.dataset.chart, err); } catch (_) {}
      }
    });
  }

  function paintSizeDetail(root, m, d) {
    const box = root.querySelector("[data-size-detail]");
    if (!box) return;
    box.innerHTML = sizeDetailHtml(m, d);
    const row = m.sizes.find(r => r.size === _size);
    const recent = box.querySelector("[data-size-recent]");
    if (recent && typeof d.renderRecentGames === "function") {
      try { d.renderRecentGames(recent, row ? row.list.map(g => g.raw) : [], 4); } catch (_) {}
    }
    // The orange tube sponge has always hidden behind the 7-player strategy.
    // It is one of the four critters whose digits make the reef's code, so it
    // moved here with the rest of that tab.
    const ico = box.querySelector("[data-strat-icon]");
    if (ico && _size === 7 && typeof window.__fishPlaceSecretCritter === "function") {
      ico.innerHTML = "";
      const wrap = window.__fishPlaceSecretCritter("sponge", ico);
      const img = wrap && wrap.querySelector("img");
      if (img) { img.style.width = "40px"; img.style.height = "40px"; }
    }
  }

  function selectSize(size) {
    if (!_last) return;
    const root = document.getElementById(ROOT_ID);
    if (!root) return;
    _size = size;
    paintSizeDetail(root, _last.m, _last.d);
    const host = root.querySelector('[data-chart="sizes"]');
    if (host) drawSizes(host, _last.m);
  }

  // ── tooltip ─────────────────────────────────────────────────────────────
  let _tip = null;
  function tipEl() {
    if (_tip && document.body.contains(_tip)) return _tip;
    _tip = document.createElement("div");
    _tip.className = "cst-tip";
    _tip.setAttribute("role", "tooltip");
    _tip.hidden = true;
    document.body.appendChild(_tip);
    return _tip;
  }
  function showTip(html, cx, cy) {
    const t = tipEl();
    t.innerHTML = html;
    t.hidden = false;
    const w = t.offsetWidth, h = t.offsetHeight;
    let left = cx + 14, top = cy - h - 12;
    if (left + w > window.innerWidth - 8) left = cx - w - 14;
    if (left < 8) left = 8;
    if (top < 8) top = cy + 18;
    t.style.left = `${Math.round(left)}px`;
    t.style.top = `${Math.round(top)}px`;
  }
  function hideTip() { if (_tip) _tip.hidden = true; }

  function wire(root) {
    if (root.__cstWired) return;
    root.__cstWired = true;
    const target = (e) => e.target && e.target.closest ? e.target.closest("[data-tip]") : null;
    root.addEventListener("pointermove", (e) => {
      const t = target(e);
      if (t) showTip(t.getAttribute("data-tip"), e.clientX, e.clientY);
      else if (!(e.target.closest && e.target.closest(".cst-hit"))) hideTip();
    });
    root.addEventListener("pointerdown", (e) => {
      const t = target(e);
      if (t) showTip(t.getAttribute("data-tip"), e.clientX, e.clientY);
      else if (!(e.target.closest && e.target.closest(".cst-hit"))) hideTip();
    });
    // A finger never "leaves" a mark, so a tap anywhere else closes the tip.
    document.addEventListener("pointerdown", (e) => { if (!root.contains(e.target)) hideTip(); }, true);
    root.addEventListener("pointerleave", hideTip);
    root.addEventListener("focusin", (e) => {
      const t = target(e);
      if (!t) return;
      const r = t.getBoundingClientRect();
      showTip(t.getAttribute("data-tip"), r.left + r.width / 2, r.top);
    });
    root.addEventListener("focusout", hideTip);
    window.addEventListener("scroll", hideTip, { passive: true, capture: true });

    const act = (el) => {
      const a = el.getAttribute("data-act");
      if (a === "history" || a === "competitive") { if (typeof window._switchPhTab === "function") window._switchPhTab(a); }
      else if (a === "play") { const b = document.getElementById("stats-quickmatch-btn"); if (b) b.click(); }
    };
    root.addEventListener("click", (e) => {
      const el = e.target.closest ? e.target.closest("[data-size],[data-act]") : null;
      if (!el || !root.contains(el)) return;
      if (el.hasAttribute("data-size")) selectSize(Number(el.getAttribute("data-size")));
      else act(el);
    });
    root.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const el = e.target.closest ? e.target.closest("g[data-size]") : null;
      if (!el) return;
      e.preventDefault();
      selectSize(Number(el.getAttribute("data-size")));
      const again = root.querySelector(`g[data-size="${el.getAttribute("data-size")}"]`);
      if (again) again.focus();
    });

    if (typeof ResizeObserver === "function") {
      _ro = new ResizeObserver(() => {
        const w = root.clientWidth;
        if (!_last || !w || Math.abs(w - _roWidth) < 2) return;
        _roWidth = w;
        hideTip();
        drawCharts(root, _last.m);
      });
      _ro.observe(root);
    }
  }

  // ── entry points ────────────────────────────────────────────────────────
  function render() {
    const root = document.getElementById(ROOT_ID);
    if (!root) return;
    let d = null;
    try { d = typeof window.__ccStatsData === "function" ? window.__ccStatsData() : null; } catch (err) {
      try { console.error("[stats] snapshot failed", err); } catch (_) {}
    }
    if (!d) {
      root.innerHTML = `<div class="cst"><section class="cst-card"><p class="cst-none">Your stats are still loading.</p></section></div>`;
      return;
    }
    const m = model(d);
    if (!_size || !SIZES.includes(_size)) {
      const most = m.sizes.reduce((a, r) => (r.games > a.games ? r : a), { size: 4, games: 0 });
      _size = most.size;
    }
    _last = { m, d };
    hideTip();
    root.innerHTML = pageHtml(m, d);
    wire(root);
    _roWidth = root.clientWidth;
    // The detail first: the table-size chart sizes itself to its height.
    paintSizeDetail(root, m, d);
    drawCharts(root, m);
  }

  // Called by Player Home whenever the account's stats change. Only draws
  // when the page is actually on screen; opening the tab draws it fresh.
  function refresh() {
    const panel = document.getElementById(PANEL_ID);
    if (!panel || panel.style.display === "none" || !panel.offsetParent) return;
    render();
  }

  // A new identity is a new page: the picked size belonged to the last one.
  function reset() { _size = 0; _last = null; hideTip(); }

  window.__ccStatsRender = render;
  window.__ccStatsRefresh = refresh;
  window.__ccStatsReset = reset;
})();
