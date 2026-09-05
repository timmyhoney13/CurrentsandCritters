/* Currents and Critters: Clan Season Grand Prize banner (one module, both hosts).
 *
 * "$100 towards a board game of your choice, shipped to you, for the clan that
 * finishes #1 this season." That is the whole feature, and like Game Night it
 * has to be impossible to miss in more than one place:
 *
 *   • the marketing site (index.html)   → renders into <div id="cc-clan-prize">
 *   • the game's Player Home            → self-injects into the Overview panel,
 *                                         directly under the Game Night band
 *   • the Clans tab                     → clans-ui.js asks this file for the
 *                                         node and puts it above the season block
 *
 * ONE file serves all of them because Vercel rewrites /js/:file to
 * multiplayer/client/js/:file (see vercel.json), so the marketing host and the
 * game host load the same script. Built deliberately in the shape of
 * js/game-night.js: same band, same status pills, same self-injection, so the
 * two announcements read as a matched pair rather than two unrelated ideas.
 *
 * WHERE THE NUMBER COMES FROM, AND WHY THAT MATTERS MORE THAN IT LOOKS
 * Game Night can hard-code its schedule: nothing on the server disagrees with
 * it. A prize is different. It is a promise repeated on four screens, and the
 * clan coin rewards already proved what happens then: the podium advertised
 * 400/300/200 for months while the server paid 150/100/50, because the numbers
 * were typed in two places and only one of them paid.
 *
 * So the amount, the wording and the claim terms are SERVER fields
 * (SEASON_GRAND_PRIZE_* in clan_server.py) and ride in on every payload that
 * already carries a season. This file holds a fallback copy for one reason
 * only: on the marketing site the banner must paint immediately, and the game
 * server is on a free tier that can take 30 seconds to wake. A blank band where
 * the prize should be is worse than a band that is briefly not yet live.
 * test_clan_prize.js asserts the fallbacks below still equal the Python
 * constants, so the one drift this re-introduces cannot survive a test run.
 */
(function () {
  "use strict";

  // ── Fallbacks: MUST equal SEASON_GRAND_PRIZE_* in clan_server.py ─────────
  // Only ever shown before the server has answered (or if it never does).
  // test_clan_prize.js reads both files and fails if these drift.
  const PRIZE_USD_FALLBACK   = 100;
  const PRIZE_WHAT_FALLBACK  = "a board game of their choice, shipped to them";
  const PRIZE_CLAIM_FALLBACK =
    "The winning clan's owner is contacted after the season is finalized, picks " +
    "the game with their clan, and gives one shipping address. Claim within 30 " +
    "days. One prize per clan, per season, shipped to one address.";

  // The last stretch of a season, when the standings are still winnable and the
  // countdown stops being background information and starts being a reason to
  // play tonight. The pill turns urgent below this.
  const FINAL_STRETCH_DAYS = 7;

  // Where the game lives, for the marketing site's CTA.
  const PLAY_URL = (typeof window.CC_CLAN_PRIZE_PLAY_URL === "string" && window.CC_CLAN_PRIZE_PLAY_URL)
    ? window.CC_CLAN_PRIZE_PLAY_URL
    : "https://play.currentsandcritters.com/";

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ── Reading the season ───────────────────────────────────────────────────
  // Never trusts a field to be there: this same function renders the banner
  // from a live payload, from a payload an older server built before the prize
  // existed, and from nothing at all while the server is still waking up.
  function prizeOf(season) {
    const s = season || {};
    const usd = Number(s.grand_prize_usd);
    return {
      usd: Number.isFinite(usd) && usd > 0 ? usd : PRIZE_USD_FALLBACK,
      what: s.grand_prize_what || PRIZE_WHAT_FALLBACK,
      claim: s.grand_prize_claim || PRIZE_CLAIM_FALLBACK,
    };
  }

  // "$100", and "$1,250" if it is ever raised past a thousand.
  function money(n) {
    try { return "$" + Number(n).toLocaleString("en-US"); }
    catch (_) { return "$" + n; }
  }

  // ── Countdown ────────────────────────────────────────────────────────────
  // Seconds, because season.ends_ts is a UNIX timestamp in seconds (the whole
  // clan API speaks seconds; treating it as ms is a countdown 1000x too long).
  function timeLeft(endTs) {
    let s = Math.max(0, Math.floor(Number(endTs || 0) - Date.now() / 1000));
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600);  s -= h * 3600;
    const m = Math.floor(s / 60);
    return { d, h, m, total: Math.max(0, Math.floor(Number(endTs || 0) - Date.now() / 1000)) };
  }
  function countdownText(endTs) {
    const t = timeLeft(endTs);
    if (t.total <= 0) return "Season over: standings being finalized";
    if (t.d > 0) return `${t.d}d ${t.h}h left`;
    if (t.h > 0) return `${t.h}h ${t.m}m left`;
    return `${t.m}m left`;
  }

  // ── Who is winning ───────────────────────────────────────────────────────
  // Rows are the leaderboard's, already sorted by the server. A clan on zero
  // points is NOT the leader: printing "leading: some clan, 0 pts" the day a
  // season opens makes the prize look already spoken for by whoever happened to
  // be created first.
  function leaderOf(rows) {
    if (!Array.isArray(rows) || !rows.length) return null;
    const top = rows[0];
    if (!top || !(Number(top.points) > 0)) return null;
    return top;
  }

  // Runner-up gap, the line that turns a standing into a race.
  //
  // Only when there is a real runner-up. Early in a season the leader is often
  // the ONLY clan that has scored, and "2nd is 386 behind" against a clan on
  // zero is not a race, it is the leader's own score written twice. Said that
  // way it also makes the prize look out of reach on the day it is easiest to
  // win, which is the opposite of the point.
  function gapText(rows) {
    if (!Array.isArray(rows) || rows.length < 2) return "";
    const second = Number(rows[1].points);
    if (!(second > 0)) return "";
    const gap = Number(rows[0].points) - second;
    if (!Number.isFinite(gap) || gap < 0) return "";
    if (gap === 0) return "2nd is level with them";
    return `2nd is ${gap.toLocaleString()} behind`;
  }

  function avSrc(u) {
    // The game cache-busts avatar art through its own helper; the marketing
    // site has no such helper and /avatars/:file is rewritten for it anyway.
    try { if (window.__fishAvSrc) return window.__fishAvSrc(u); } catch (_) {}
    return u;
  }

  // ── Render ───────────────────────────────────────────────────────────────
  // opts.cta:  "play"  → link out to the game (marketing site)
  //            "clans" → button that opens the Clans tab (in-game)
  //            "none"  → no CTA (already ON the Clans tab)
  //
  // opts.standings: false ON THE CLANS TAB, where the leader and the season
  // clock are both already on the screen, in the podium and the season block
  // directly below this banner. Repeating them here names the same clan twice
  // within a few hundred pixels, which is the duplicate-clan problem the tab
  // was already fixed for once (see renderHome: the hero card and the podium).
  // Everywhere else the standings are the whole reason the banner is live
  // rather than a poster, so they default ON.
  function innerHtml(season, rows, opts) {
    const o = opts || {};
    const showStandings = o.standings !== false;
    const p = prizeOf(season);
    const s = season || {};
    const lead = leaderOf(rows);
    const left = timeLeft(s.ends_ts);
    const final = s.ends_ts && left.total > 0 && left.d < FINAL_STRETCH_DAYS;

    const seasonBit = s.number
      ? `Season ${esc(s.number)}${s.name ? " · " + esc(s.name) : ""}`
      : "this season";

    const countPill = s.ends_ts
      ? `<span class="${final ? "ccPrize-final" : "ccPrize-count"}">${
          final ? "🔥 Final stretch: " : "⏳ "}${esc(countdownText(s.ends_ts))}</span>`
      : "";

    // The leader, or an honest statement that nobody is one yet.
    //
    // "No rows" and "no answer yet" are NOT the same thing and must not print
    // the same sentence. An array (even an empty one) means the server told us
    // the standings; no array means it has not answered, and on the marketing
    // site that is the normal state for the first 30 seconds while the free
    // tier wakes up. Saying "nobody has scored yet" then is a claim about the
    // season made by a banner that has not looked at it.
    const heard = Array.isArray(rows);
    const gap = lead ? gapText(rows) : "";
    const leadPill = lead
      ? `<span class="ccPrize-lead">${lead.icon
            ? `<img src="${esc(avSrc(lead.icon))}" alt="" loading="lazy" decoding="async">` : "🥇"}
           <span class="nm">${esc(lead.name)}</span> ${esc(Number(lead.points).toLocaleString())} pts${
           gap ? ` · ${esc(gap)}` : ""}</span>`
      : heard
        ? `<span class="ccPrize-count">🥇 Nobody has scored yet: first points take the lead</span>`
        : "";

    let cta = "";
    if (o.cta === "play") {
      cta = `<div class="ccPrize-cta"><a class="ccPrize-btn" href="${esc(PLAY_URL)}">Join a clan</a></div>`;
    } else if (o.cta === "clans") {
      cta = `<div class="ccPrize-cta"><button type="button" class="ccPrize-btn" data-cc-clan-prize-go="1">Open Clans</button></div>`;
    }

    return `
      <div class="ccPrize-inner">
        <div class="ccPrize-ico" aria-hidden="true">🏆</div>
        <div class="ccPrize-body">
          <div class="ccPrize-title"><span class="amt">${esc(money(p.usd))}</span> to the #1 clan</div>
          <div class="ccPrize-what">
            <b>${esc(money(p.usd))} towards ${esc(p.what)}</b>, for the clan that
            finishes 1st in ${seasonBit}.
          </div>
          <div class="ccPrize-status">${showStandings ? leadPill + countPill : ""}</div>
          <div class="ccPrize-note">${esc(p.claim)}</div>
        </div>
        ${cta}
      </div>`;
  }

  // A ready-made .ccPrize element with its own self-cancelling minute timer, for
  // callers that already hold a season payload (clans-ui.js). Self-cancelling
  // the same way the Clans season block does it: the tab detaches the node
  // without telling anyone, so the timer has to notice on its own. It may only
  // do that AFTER the node has been on screen once, because callers build the
  // whole card before attaching it and the first tick legitimately runs
  // detached.
  function prizeNode(season, rows, opts) {
    const n = document.createElement("div");
    n.className = "ccPrize";
    let attached = false;
    const tick = () => {
      if (document.body.contains(n)) attached = true;
      else if (attached) { clearInterval(t); return; }
      n.innerHTML = innerHtml(season, rows, opts);
    };
    tick();
    const t = setInterval(tick, 60000);
    return n;
  }

  // ── The self-rendering banner (marketing site + Player Home) ─────────────
  // Both hosts read the SAME public endpoint. /api/clan/leaderboard is a GET
  // that needs no auth (it is in the server's no-standings-change read set), so
  // this works signed out, on the marketing site, and for a guest.
  function apiUrl(path) {
    // In the game, preview-app.js owns the base (and honours ?api_base= and the
    // saved override, so a dev build points at a dev server).
    try { if (typeof window.__ccApiUrl === "function") return window.__ccApiUrl(path); } catch (_) {}
    const base = (typeof window.CC_CLAN_PRIZE_API === "string" && window.CC_CLAN_PRIZE_API)
      ? window.CC_CLAN_PRIZE_API
      : (typeof window.__FISH_API_BASE__ === "string" && window.__FISH_API_BASE__)
        ? window.__FISH_API_BASE__
        : "https://play.currentsandcritters.com";
    return String(base).replace(/\/+$/, "") + path;
  }

  let _live = null;      // last { season, rows } the server gave us

  async function refresh() {
    try {
      // The free-tier server can take ~30s to wake, the same allowance the
      // marketing site's own stats fetch makes. AbortSignal.timeout is not in
      // Safari < 16.4, so the controller is manual.
      const ctrl = new AbortController();
      const tid = setTimeout(() => ctrl.abort(), 30000);
      const resp = await fetch(apiUrl("/api/clan/leaderboard"), { signal: ctrl.signal })
        .finally(() => clearTimeout(tid));
      if (!resp.ok) return;
      const data = await resp.json();
      if (!data || !data.ok || !data.season) return;
      _live = { season: data.season, rows: data.rows || [] };
      tick();
    } catch (_) { /* server asleep: the fallback banner is already painted */ }
  }

  // The banner's home on each host.
  //   • A page that declares <div id="cc-clan-prize"> gets it exactly there.
  //   • Player Home has none, so the banner inserts itself into the Overview
  //     panel directly UNDER the Game Night band (game-night.js puts itself at
  //     the very top). Above it would push the schedule down; anywhere lower is
  //     below the fold on a phone.
  function host() {
    let el = document.getElementById("cc-clan-prize");
    if (el) return el;
    const overview = document.getElementById("ph-panel-overview");
    if (!overview) return null;
    el = document.createElement("div");
    el.id = "cc-clan-prize";
    el.className = "ccPrize";
    const gn = document.getElementById("cc-game-night");
    if (gn && gn.parentNode === overview) overview.insertBefore(el, gn.nextSibling);
    else overview.insertBefore(el, overview.firstChild);
    return el;
  }

  function tick() {
    const el = host();
    if (!el) return;
    el.classList.add("ccPrize");
    // In the game the CTA opens the Clans tab; on the marketing site it sends
    // the reader to the game, which is the only action available there.
    const inGame = !!document.getElementById("ph-panel-overview");
    el.innerHTML = innerHtml(_live && _live.season, _live && _live.rows,
                             { cta: inGame ? "clans" : "play" });
  }

  // One delegated listener rather than one per repaint: the banner rebuilds
  // itself every minute, and a listener bound to the button would be re-bound
  // with it forever.
  //
  // It opens the tab by clicking Player Home's own tab button rather than
  // calling into preview-app.js. switchTab() is private to that file and does
  // a good deal more than show a panel (guest notes, backgrounds, resetting
  // the hidden critters), so the ONE way in that stays correct is the one the
  // player has: the button itself.
  document.addEventListener("click", (e) => {
    const btn = e.target && e.target.closest && e.target.closest("[data-cc-clan-prize-go]");
    if (!btn) return;
    const tab = document.querySelector('#ph-tabs .ph-tab[data-tab="clans"]')
             || document.querySelector('[data-tab="clans"]');
    if (tab) tab.click();
  });

  let _timer = null;
  function start() {
    tick();
    if (!_live) refresh();
    if (_timer) clearInterval(_timer);
    // A minute for the clock (it is shown in days/hours), five for the
    // standings: the leader does not change often enough to poll harder, and
    // this runs on the marketing site's front page.
    let n = 0;
    _timer = setInterval(() => {
      if (document.hidden) return;
      tick();
      if (++n % 5 === 0) refresh();
    }, 60000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  // Player Home builds its panels after auth resolves, so the Overview panel
  // may not exist at load: preview-app.js re-runs this when the tab is opened,
  // exactly as it does for Game Night. Harmless if it lands early.
  window.__ccClanPrizeRender = start;

  // The seam clans-ui.js renders through, so the Clans tab's copy of the
  // banner is the same copy, from the same file.
  window.__ccClanPrize = {
    html: innerHtml,
    node: prizeNode,
    render: start,
    prizeOf: prizeOf,
    PRIZE_USD_FALLBACK: PRIZE_USD_FALLBACK,
    PRIZE_WHAT_FALLBACK: PRIZE_WHAT_FALLBACK,
    PRIZE_CLAIM_FALLBACK: PRIZE_CLAIM_FALLBACK,
  };
})();
