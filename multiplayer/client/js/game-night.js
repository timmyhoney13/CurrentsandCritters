/* Currents and Critters: Game Night (one module, both hosts).
 *
 * "Game Night is every Saturday, 7–9 PM CST. Games, challenges and the daily
 * bonus pay 1.5x XP while it runs. RSVP here. RSVP isn't required, but it's
 * recommended." That is the whole feature, and it has to be impossible to miss
 * in two places:
 *
 *   • the marketing site (index.html)  → renders into <div id="cc-game-night">
 *   • the game's Player Home            → self-injects at the TOP of the
 *                                         Overview panel, above everything else
 *
 * ONE file serves both because Vercel rewrites /js/:file to
 * multiplayer/client/js/:file (see vercel.json), so the marketing host and the
 * game host load the same script. Two copies of an event time is how a site
 * ends up advertising last season's schedule on one of them.
 *
 * WHY THERE IS TIME-ZONE CODE IN HERE AT ALL
 * "7 PM CST" is not a time to anyone outside that zone, and "CST" drifts by an
 * hour twice a year, the zone people mean is America/Chicago, which is CST in
 * winter and CDT in summer. So the next occurrence is computed against the
 * real IANA zone and ALSO shown in the reader's own local time. Nothing here
 * hard-codes UTC-6.
 *
 * WHY THE XP MULTIPLIER LIVES HERE TOO
 * The bonus is only on while the session is live, and "is it live?" is exactly
 * what nextSession() already answers, against the real zone and DST. Putting
 * the multiplier anywhere else would mean a SECOND copy of the schedule, which
 * is how a bonus ends up paying out an hour late twice a year. The app reads
 * it through window.__ccGameNightXp(), a synchronous call (an XP grant can
 * never await), and treats a missing module as "no bonus", the safe direction.
 */
(function () {
  "use strict";

  // ── The one place the event is defined ───────────────────────────────────
  // RSVP points at the Discord server, where the scheduled event lives. Set
  // window.CC_GAME_NIGHT_RSVP_URL before this script to point it somewhere
  // else (a specific event permalink, a form) without touching this file.
  const RSVP_URL = (typeof window.CC_GAME_NIGHT_RSVP_URL === "string" && window.CC_GAME_NIGHT_RSVP_URL)
    ? window.CC_GAME_NIGHT_RSVP_URL
    : "https://discord.gg/T9V2eqxf8";

  const ZONE = "America/Chicago";   // what "CST" means to a person
  const WEEKDAY = 6;                // 0=Sun … 6=Sat
  const START_HOUR = 19;            // 7:00 PM
  const END_HOUR = 21;              // 9:00 PM

  // XP pays this much while the session is live, everywhere the Prestige bonus
  // and the Level Pass boost apply (games, the daily login bonus, daily/weekly
  // challenges and their metas, events, clan challenge XP). One number, read
  // by the banner (so the promise is written from the same constant that pays
  // it) and by the app's XP grant.
  const XP_MULT = 1.5;
  const XP_PERCENT = Math.round((XP_MULT - 1) * 100);   // 50, for "+50%"
  // "1.5x" without a trailing zero, and "2x" if it is ever a round number.
  const XP_LABEL = String(Number(XP_MULT.toFixed(2))) + "x";

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ── Time zone maths ──────────────────────────────────────────────────────
  // The offset of ZONE at a given instant, in ms. Derived by formatting the
  // instant in that zone and reading the wall clock back, the only way to get
  // this right across DST without shipping a tz database.
  function zoneOffsetMs(date) {
    try {
      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: ZONE, hour12: false,
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
      }).formatToParts(date).reduce((a, p) => (a[p.type] = p.value, a), {});
      const asUTC = Date.UTC(
        +parts.year, +parts.month - 1, +parts.day,
        +parts.hour % 24, +parts.minute, +parts.second);
      return asUTC - date.getTime();
    } catch (_) {
      return -6 * 3600000;   // last resort: plain CST
    }
  }

  // The instant at which a given ZONE wall-clock time occurs. Guess with the
  // offset at the guess, then correct once, a single pass is enough because
  // the offset only ever moves by an hour and never twice inside one day.
  function instantFor(y, m, d, hour) {
    const guess = new Date(Date.UTC(y, m, d, hour, 0, 0) + 6 * 3600000);
    const off = zoneOffsetMs(guess);
    const exact = new Date(Date.UTC(y, m, d, hour, 0, 0) - off);
    const off2 = zoneOffsetMs(exact);
    return off2 === off ? exact : new Date(Date.UTC(y, m, d, hour, 0, 0) - off2);
  }

  // Today's date AS SEEN IN ZONE, not the viewer's date. A player in Sydney
  // is already on Sunday while Game Night is still running in Chicago.
  function zoneToday(now) {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: ZONE, hour12: false, weekday: "short",
      year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(now).reduce((a, p) => (a[p.type] = p.value, a), {});
    const dowIndex = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
    return {
      y: +parts.year, m: +parts.month - 1, d: +parts.day,
      dow: dowIndex[parts.weekday] != null ? dowIndex[parts.weekday] : 6,
    };
  }

  // { start, end, live } for the session that is either running now or next.
  function nextSession(now) {
    const t = zoneToday(now);
    // This week's Saturday, measured from the Chicago date.
    let delta = (WEEKDAY - t.dow + 7) % 7;
    let start = instantFor(t.y, t.m, t.d + delta, START_HOUR);
    let end = instantFor(t.y, t.m, t.d + delta, END_HOUR);
    // Saturday, but the session already finished → roll to next week. (While
    // it is RUNNING we deliberately keep it: "live now" is the whole point.)
    if (now >= end) {
      delta += 7;
      start = instantFor(t.y, t.m, t.d + delta, START_HOUR);
      end = instantFor(t.y, t.m, t.d + delta, END_HOUR);
    }
    return { start, end, live: now >= start && now < end };
  }

  // ── The XP bonus seam ────────────────────────────────────────────────────
  // { active, mult, percent, label, start, end }. SYNCHRONOUS on purpose: the
  // app calls this from inside an XP grant, which can never wait on anything.
  // `now` is injectable so a test can ask about a Saturday evening without one.
  // Coerced through Number rather than tested with `instanceof Date`: a Date
  // handed in from another realm (an iframe, a test harness) is not an instance
  // of THIS realm's Date, and silently answering about the wrong instant is the
  // one failure mode that would be invisible.
  function xpState(now) {
    const ms = (now == null) ? Date.now() : Number(now);
    const at = new Date(Number.isFinite(ms) ? ms : Date.now());
    let live = false, start = null, end = null;
    try {
      const s = nextSession(at);
      live = !!s.live; start = s.start; end = s.end;
    } catch (_) { live = false; }
    return {
      active: live,
      mult: live ? XP_MULT : 1,
      percent: live ? XP_PERCENT : 0,
      label: XP_LABEL,
      start, end,
    };
  }
  window.__ccGameNightXp = xpState;

  // "7:00 PM" in the reader's own zone, so nobody has to do the arithmetic.
  function localWindow(start, end) {
    try {
      const f = new Intl.DateTimeFormat(undefined,
        { hour: "numeric", minute: "2-digit" });
      const zone = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" })
        .formatToParts(start).find(p => p.type === "timeZoneName");
      return `${f.format(start)}–${f.format(end)}${zone ? " " + zone.value : ""}`;
    } catch (_) { return ""; }
  }

  function countdown(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }

  // ── Render ───────────────────────────────────────────────────────────────
  function innerHtml() {
    const now = new Date();
    const { start, end, live } = nextSession(now);
    const local = localWindow(start, end);
    // Only worth showing when the reader is NOT already on Chicago time,
    // otherwise it repeats the headline back at them, and in summer it does it
    // in a DIFFERENT abbreviation ("7-9 PM CST · that's 7-9 PM CDT for you"),
    // which reads like a contradiction.
    //
    // Compared in whole MINUTES on purpose. zoneOffsetMs() is built from a
    // seconds-resolution wall clock minus a millisecond-resolution instant, so
    // it carries the current millisecond as noise and is essentially never
    // exactly equal to a round minute offset, which made this test always true
    // and showed the chip to Chicago readers too.
    const zoneMins = Math.round(zoneOffsetMs(now) / 60000);
    const readerMins = -new Date().getTimezoneOffset();
    const localBit = (local && zoneMins !== readerMins)
      ? `<span class="ccGN-local">that's ${esc(local)} for you</span>` : "";

    const when = live
      ? `<span class="ccGN-live">● Live right now, until ${esc(
          new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(end))}</span>`
      : `<span class="ccGN-count">Starts in ${esc(countdown(start - now))}</span>`;

    // The reward, stated from the same constant that pays it. It is a separate
    // chip rather than a line of the note so it survives the narrow layout,
    // where the note wraps to three lines and stops being read.
    const xpChip = `<span class="ccGN-xp${live ? " is-live" : ""}">⚡ ${esc(XP_LABEL)} XP${
      live ? " right now" : " all night"}</span>`;

    return `
      <div class="ccGN-inner${live ? " is-live" : ""}">
        <div class="ccGN-ico" aria-hidden="true">🎲</div>
        <div class="ccGN-body">
          <div class="ccGN-title">Game Night</div>
          <div class="ccGN-when">
            <b>Every Saturday, 7:00–9:00 PM CST</b>
            ${localBit}
          </div>
          <div class="ccGN-status">${when}${xpChip}</div>
          <div class="ccGN-note">
            Games, challenges and your daily bonus all pay ${esc(XP_LABEL)} XP while
            it runs. RSVP isn't mandatory, but it's recommended.
          </div>
        </div>
        <div class="ccGN-cta">
          <a class="ccGN-btn" href="${esc(RSVP_URL)}" target="_blank" rel="noopener noreferrer">RSVP here</a>
        </div>
      </div>`;
  }

  function paint(host) {
    if (!host) return;
    host.innerHTML = innerHtml();
  }

  // The banner's home on each host.
  //   • A page that declares <div id="cc-game-night"> gets it exactly there.
  //   • Player Home has no such div (preview.html is shared with a lot of
  //     other work), so the banner inserts ITSELF at the top of the Overview
  //     panel: above the challenge strip, which is what "easy to see" means
  //     on that screen.
  function host() {
    let el = document.getElementById("cc-game-night");
    if (el) return el;
    const overview = document.getElementById("ph-panel-overview");
    if (!overview) return null;
    el = document.createElement("div");
    el.id = "cc-game-night";
    el.className = "ccGN";
    overview.insertBefore(el, overview.firstChild);
    return el;
  }

  let _timer = null;
  function tick() {
    const el = host();
    if (!el) return;
    el.classList.add("ccGN");
    paint(el);
  }

  function start() {
    tick();
    if (_timer) clearInterval(_timer);
    // A minute is plenty: the countdown is shown in days/hours, and the only
    // moment that matters to the second is the flip to "live", which a minute
    // catches well enough.
    _timer = setInterval(() => { if (!document.hidden) tick(); }, 60000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  // Player Home builds its panels after auth resolves, so the Overview panel
  // may not exist at load. Re-running on demand is what the app calls once the
  // lobby is on screen; harmless if it lands early.
  window.__ccGameNightRender = start;
})();
