#!/usr/bin/env node
/* Game Night pays 1.5x XP, and it has to pay it at the right hour.
 *
 * Two halves, both running the REAL code:
 *
 *   1. THE CLOCK. js/game-night.js is evaluated in a vm with a stub window,
 *      and window.__ccGameNightXp() is asked about fixed instants either side
 *      of 8:00 and 10:00 PM America/Chicago, in summer AND winter, plus the
 *      Wednesdays on either side of a DST switch. Then a whole year is swept
 *      minute by minute: the bonus must be on for exactly 120 minutes on every
 *      Wednesday, and on no other day. A multiplier that is live for 180
 *      minutes twice a year is the bug this sweep exists to catch, and it is
 *      invisible to any test that only asks "is it on now?". The days are
 *      counted from the calendar rather than pinned to a literal, so a night
 *      could not be added or dropped by loosening the number the test asserts.
 *
 *      Game Night went to ONE night, Wednesday, 8-10 PM, on 2026-09-30. Every
 *      instant below moved with it: the old fixtures were 7-9 PM and half of
 *      them were Saturdays, so a test left unchanged would have gone on
 *      certifying a schedule nobody plays on. Saturday is asserted OFF now, at
 *      exactly the hours it used to run, which is what catches a leftover 6 in
 *      NIGHTS.
 *
 *   2. THE ARITHMETIC. prestigeLevelNow / passBoostNow / gameNightXpNow /
 *      prestigeXp are lifted verbatim out of js/preview-app.js and run against
 *      stubbed seams, so the stacking order (Prestige, then the Level Pass
 *      boost, then Game Night) is checked as it is written, not as it is
 *      remembered. The breakdown fields matter as much as the total: they are
 *      what the end screen prints, and a bonus nobody can see reads as XP that
 *      never arrived.
 *
 * The two directions that must never break:
 *   • a missing/broken game-night.js means 1x, never 0x. A missed bonus is a
 *     smaller wrong than XP that gets deleted.
 *   • the multiplier is read at grant time, so it can never be stale.
 *
 * Run:  node test_game_night_xp.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const APP = read("js/preview-app.js");
const GN  = read("js/game-night.js");
// The same source with its comments removed. The checks below ask whether a
// time or a day is TYPED into the rendered copy rather than derived from the
// constants, and the comments legitimately quote both.
const GN_CODE = GN.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/[^\n]*$/gm, "");

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (detail != null ? "  [" + detail + "]" : "")); }
}

// ── 1. The clock ────────────────────────────────────────────────────────────
// A stub window is enough: the module only needs Intl, timers and a document
// with no host element (so it renders nothing and just exports the seam).
function loadGameNight() {
  const doc = {
    readyState: "complete", hidden: false,
    addEventListener() {}, getElementById() { return null; },
    createElement() { return { classList: { add() {} }, style: {} }; },
  };
  const win = { document: doc };
  const ctx = vm.createContext({
    window: win, document: doc, console,
    setInterval: () => 0, clearInterval: () => {},
  });
  vm.runInContext(GN, ctx);
  return win;
}

const gnWin = loadGameNight();
const gnXp  = gnWin.__ccGameNightXp;

console.log("\nthe bonus is on exactly when Game Night is");
check("the module exports a synchronous XP seam", typeof gnXp === "function");

const chicago = (iso) => new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Chicago", weekday: "short", month: "short", day: "numeric",
  hour: "numeric", minute: "2-digit", timeZoneName: "short",
}).format(new Date(iso));

// [label, instant (UTC), should the bonus be live?]
// Chicago is UTC-5 on CDT and UTC-6 on CST, so 8:00 PM is 01:00Z in summer and
// 02:00Z in winter. Every instant is written in UTC and printed back in Chicago
// time by the check itself, so a wrong conversion here cannot hide.
const MOMENTS = [
  // Summer: 8-10 PM CDT.
  ["a minute before the summer start", "2026-08-20T00:59:00Z", false],
  ["8:00 PM CDT exactly",              "2026-08-20T01:00:00Z", true],
  ["a Wednesday evening",              "2026-08-20T01:30:00Z", true],
  ["a minute before the end",          "2026-08-20T02:59:00Z", true],
  ["10:00 PM CDT exactly, over",       "2026-08-20T03:00:00Z", false],
  ["after midnight in Chicago",        "2026-08-20T07:00:00Z", false],
  // Winter: 8-10 PM CST, an hour later in UTC. An offset baked in as UTC-5 or
  // UTC-6 passes one of these two blocks and fails the other.
  ["a minute before the winter start", "2026-12-17T01:59:00Z", false],
  ["8:00 PM CST exactly",              "2026-12-17T02:00:00Z", true],
  ["a minute before the winter end",   "2026-12-17T03:59:00Z", true],
  ["10:00 PM CST exactly, over",       "2026-12-17T04:00:00Z", false],
  // Either side of both DST switches, when the offset moves under the schedule.
  ["the Wednesday before DST ends",    "2026-10-29T01:00:00Z", true],
  ["the Wednesday after DST ends",     "2026-11-05T02:00:00Z", true],
  ["the Wednesday before DST starts",  "2027-03-11T02:00:00Z", true],
  ["the Wednesday after DST starts",   "2027-03-18T01:00:00Z", true],
  // Saturday, at exactly the hours it used to run and the hours it would run
  // now. It is a Wednesday-only night since 2026-09-30, so both are OFF: this
  // is the pair that catches a 6 left behind in NIGHTS.
  ["Saturday at the old 7 PM",         "2026-08-23T00:00:00Z", false],
  ["Saturday at 8 PM",                 "2026-08-23T01:00:00Z", false],
  ["Saturday at 9 PM",                 "2026-08-23T02:00:00Z", false],
  ["a Saturday in winter, at 8 PM",    "2026-12-20T02:00:00Z", false],
  // Every other day, at exactly the hour the night runs.
  ["a Thursday evening",               "2026-08-21T01:00:00Z", false],
  ["a Friday evening",                 "2026-08-22T01:00:00Z", false],
  ["a Sunday evening",                 "2026-08-24T01:00:00Z", false],
  ["a Monday evening",                 "2026-08-25T01:00:00Z", false],
  ["a Tuesday evening",                "2026-08-26T01:00:00Z", false],
  // The hour the night USED to start, on the night it still runs. Moving the
  // window is the whole change; leaving 19 in START_HOUR fails right here.
  ["Wednesday at the old 7 PM start",  "2026-08-20T00:00:00Z", false],
  ["Wednesday at the old 9 PM end",    "2026-08-20T02:00:00Z", true],
];
for (const [label, iso, want] of MOMENTS) {
  const st = gnXp(new Date(iso));
  check(`${label} (${chicago(iso)}): ${want ? "1.5x" : "1x"}`,
        st.active === want && st.mult === (want ? 1.5 : 1),
        `active=${st.active} mult=${st.mult}`);
}

// The sweep. Anything that drifts, an offset baked in as UTC-6, a window that
// widens across a DST switch, a session that lands on Sunday for readers east
// of Chicago, shows up here and nowhere else.
//
// Two passes rather than one, because a whole year at minute resolution is
// ~525k timezone conversions and takes minutes to run: an HOURLY pass over the
// year proves no session ever lands on the wrong day, and a MINUTE pass over
// the evening of each Wednesday proves each one is exactly 2 hours long.
{
  const dayOf = (d) => new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago", weekday: "short", year: "numeric",
    month: "2-digit", day: "2-digit",
  }).format(d);

  const HOUR = 3600000, MIN = 60000;
  const NIGHT = ["Wed"];             // the schedule this test is holding to
  const isNight = (k) => NIGHT.some(n => k.startsWith(n));
  const hourly = new Map();          // Chicago day → live hours seen
  // Every Wednesday the sweep actually covers the evening of, built from the
  // calendar rather than a literal count, so a night dropped from the module
  // cannot be papered over by editing a number here. The first day sampled is
  // 6 PM Chicago (the sweep starts at midnight UTC) and the last is a Thursday,
  // so every Wednesday seen has its whole 8-10 PM window inside.
  const nights = [];
  for (let t = Date.UTC(2026, 0, 1); t < Date.UTC(2027, 0, 1); t += HOUR) {
    const d = new Date(t);
    const key = dayOf(d);
    if (isNight(key) && !nights.includes(key)) nights.push(key);
    if (!gnXp(d).active) continue;
    hourly.set(key, (hourly.get(key) || 0) + 1);
  }
  const liveDays = [...hourly.keys()];
  const wed = liveDays.filter(k => k.startsWith("Wed")).length;
  const sat = liveDays.filter(k => k.startsWith("Sat")).length;
  check("across 2026 the bonus runs every Wednesday, all 52 of them",
        wed >= 52, `${wed} Wednesdays`);
  check("…and on no Saturday at all any more",
        sat === 0, `${sat} Saturdays still live`);
  check("…every live day is a Wednesday in Chicago",
        liveDays.every(isNight),
        liveDays.filter(k => !isNight(k)).join(", "));
  check("…and not one of the year's nights is skipped",
        nights.filter(k => !hourly.has(k)).length === 0,
        nights.filter(k => !hourly.has(k)).join(", "));
  check("…nor is a day that is not a night ever live",
        liveDays.length === nights.length, `${liveDays.length} live / ${nights.length} nights`);

  // Minute resolution, 6:00 PM → midnight Chicago on each of those nights,
  // found by walking back from the hour the sweep saw it live.
  let wrongLen = [], gappy = [];
  for (const [t0] of (() => {
    const seen = new Map();
    for (let t = Date.UTC(2026, 0, 1); t < Date.UTC(2027, 0, 1); t += HOUR) {
      const d = new Date(t);
      if (gnXp(d).active) { const k = dayOf(d); if (!seen.has(k)) seen.set(k, t); }
    }
    return [...seen.entries()].map(([k, t]) => [t, k]);
  })()) {
    let live = 0, runs = 0, wasLive = false;
    for (let t = t0 - 2 * HOUR; t <= t0 + 4 * HOUR; t += MIN) {
      const on = gnXp(new Date(t)).active;
      if (on) live++;
      if (on && !wasLive) runs++;
      wasLive = on;
    }
    const key = dayOf(new Date(t0));
    if (live !== 120) wrongLen.push(`${key}=${live}min`);
    if (runs !== 1) gappy.push(`${key}=${runs} runs`);
  }
  check("…each session is exactly 120 minutes long, DST included",
        wrongLen.length === 0, wrongLen.join(", "));
  check("…and unbroken, one run from 8:00 to 10:00",
        gappy.length === 0, gappy.join(", "));
}

// A Date from another realm (this test's, an iframe's) must be understood, and
// a ms timestamp too. Answering about "now" instead would be silent and wrong.
// Wednesday 8:30 PM against THURSDAY 8:30 PM: the "off" instant has to be a day
// that is not a night at all, not merely an hour outside the window.
check("an instant handed in from another realm is honoured",
      gnXp(new Date("2026-08-20T01:30:00Z")).active === true
      && gnXp(new Date("2026-08-21T01:30:00Z")).active === false);
check("a plain millisecond timestamp works the same",
      gnXp(Date.parse("2026-08-20T01:30:00Z")).active === true
      && gnXp(Date.parse("2026-08-21T01:30:00Z")).active === false);
check("garbage falls back to now, never to a bogus instant",
      typeof gnXp("nonsense").active === "boolean");

console.log("\nthe banner promises what the code pays");
check("the multiplier is one constant, not a number typed into the copy",
      /const XP_MULT = 1\.5;/.test(GN));
check("the label is derived from that constant", /XP_LABEL = String\(Number\(XP_MULT/.test(GN));
check("the chip is rendered from the label, never a literal",
      /class="ccGN-xp[^]{0,80}\$\{esc\(XP_LABEL\)\}/.test(GN));
check("the note says which XP it applies to",
      /Games, challenges and your daily bonus all pay \$\{esc\(XP_LABEL\)\} XP/.test(GN));
check("the chip has a style to be seen in", /\.ccGN-xp\s*\{/.test(read("css/game-night.css")));
// The headline, the hours and the countdown are written from the schedule
// constants, not typed out beside them. A banner that still reads "Every
// Saturday, 7-9" while the bonus pays Wednesday 8-10 is the exact failure this
// pins, and it is the one a reader acts on before anybody notices.
check("the night is one list, not a weekday and some prose",
      /const NIGHTS = \[3\];/.test(GN));
check("…and Saturday is out of it", !/const NIGHTS = \[[^\]]*6/.test(GN));
check("the hours are two constants, and they are 8 PM to 10 PM",
      /const START_HOUR = 20;/.test(GN) && /const END_HOUR = 22;/.test(GN));
check("the headline is built from the list and the hours",
      /SCHEDULE_LABEL = "Every "/.test(GN)
      && /\$\{esc\(SCHEDULE_LABEL\)\}, \$\{esc\(WINDOW_LABEL\)\} CST/.test(GN));
check("the hours label is derived from START_HOUR/END_HOUR, never typed",
      /WINDOW_LABEL = _ampm\(START_HOUR\)/.test(GN)
      && !/8:00–10:00/.test(GN_CODE));
check("no literal 'Every Saturday' is left in the copy",
      !/Every Saturday,/.test(GN));
// The pill named the night while there were two of them. With one, it is the
// countdown alone, and the day name is reached for only if NIGHTS grows again.
check("the countdown names the night only when there is more than one",
      /NIGHTS\.length > 1 \? esc\(DAY_NAMES\[dow\][^)]*\)/.test(GN)
      && /class="ccGN-count">\$\{nightBit\}starts in/.test(GN));

console.log("\nthere is no RSVP, there is a door");
// The night is one voice channel. That sentence and that link are the only
// instruction it has, so both are pinned: the copy, the channel name, and the
// invite itself, which is the one thing here that cannot be derived from
// anything else in the file.
check("the band says where to turn up",
      /Anyone interested will be in the \$\{esc\(VOICE_CHANNEL\)\} voice chat in/.test(GN));
check("the channel is named once, as a constant",
      /const VOICE_CHANNEL = "General Current";/.test(GN));
check("the invite is the live one",
      /const DISCORD_URL = "https:\/\/discord\.gg\/z3yz5HQ6q";/.test(GN));
check("the link is rendered from that constant, not typed into the markup",
      /href="\$\{esc\(DISCORD_URL\)\}"/.test(GN));
check("it reads 'Click here'", />Click here<\/a>/.test(GN));
check("it opens in its own tab and keeps the opener to itself",
      /target="_blank"/.test(GN) && /rel="noopener noreferrer"/.test(GN));
check("no RSVP is left anywhere in the rendered band", !/RSVP/i.test(GN_CODE));
check("the link has a style to be seen in",
      /\.ccGN-link\s*[,{]/.test(read("css/game-night.css"))
      && /\.ccGN-join\s*\{/.test(read("css/game-night.css")));

// ── 2. The arithmetic ───────────────────────────────────────────────────────
// Lift the four real functions out of the app rather than restating them.
function lift(name) {
  const start = APP.indexOf("\n  function " + name + "(");
  if (start < 0) throw new Error("cannot find function " + name + " in preview-app.js");
  let i = APP.indexOf("{", start), depth = 0;
  for (; i < APP.length; i++) {
    if (APP[i] === "{") depth++;
    else if (APP[i] === "}") { depth--; if (depth === 0) return APP.slice(start, i + 1); }
  }
  throw new Error("unbalanced braces reading " + name);
}
const XP_SRC = ["prestigeLevelNow", "passBoostNow", "gameNightXpNow", "prestigeXp"]
  .map(lift).join("\n");

function xpEngine({ prestige = 0, boost = null, night = null } = {}) {
  const win = {};
  if (prestige) win.__ccPrestigeState = () => ({ prestige: { level: prestige } });
  if (boost) win.__ccPassBoost = () => boost;
  if (night) win.__ccGameNightXp = () => night;
  const ctx = vm.createContext({ window: win, console, Number, Math, String });
  vm.runInContext(XP_SRC + "\n; this.prestigeXp = prestigeXp;", ctx);
  return ctx.prestigeXp;
}
const LIVE = { active: true, mult: 1.5, percent: 50, label: "1.5x" };
const OFF  = { active: false, mult: 1, percent: 0, label: "1.5x" };
const BOOST = { active: true, percent: 20, mult: 1.2 };

console.log("\n1.5x, applied to the XP a game actually pays");
{
  const plain = xpEngine();
  const live  = xpEngine({ night: LIVE });
  check("off Game Night nothing changes", plain(100).total === 100, plain(100).total);
  check("a 100 XP game pays 150 during Game Night", live(100).total === 150, live(100).total);
  check("the bonus is broken out for the end screen",
        live(100).gameNightBonus === 50 && live(100).gameNight === true,
        JSON.stringify(live(100)));
  check("the label rides along so the screen can print '1.5x'",
        live(100).gameNightLabel === "1.5x", live(100).gameNightLabel);
  check("an odd number rounds down, never up (57 → 85)",
        live(57).total === 85, live(57).total);
  check("zero XP stays zero", live(0).total === 0 && live(0).gameNightBonus === 0);
}

console.log("\nit stacks on top of the other two, in that order");
{
  const p2   = xpEngine({ prestige: 2, night: LIVE });
  const both = xpEngine({ prestige: 2, boost: BOOST, night: LIVE });
  const bOnly = xpEngine({ boost: BOOST, night: LIVE });
  // 100 → Prestige 2 (×1.5) = 150 → Game Night (×1.5) = 225
  check("Prestige first, then Game Night (100 → 150 → 225)",
        p2(100).total === 225, p2(100).total);
  check("…and the Prestige share is still reported on its own",
        p2(100).prestigeBonus === 50 && p2(100).gameNightBonus === 75,
        `${p2(100).prestigeBonus} / ${p2(100).gameNightBonus}`);
  // 100 → ×1.5 = 150 → ×1.2 = 180 → ×1.5 = 270
  check("all three stack (100 → 150 → 180 → 270)", both(100).total === 270, both(100).total);
  check("the boost's own line is unchanged by the new multiplier",
        both(100).boostBonus === 30 && both(100).boostPercent === 20,
        `${both(100).boostBonus} / ${both(100).boostPercent}`);
  check("bonus is still everything above base",
        both(100).bonus === both(100).total - 100, both(100).bonus);
  check("a boost with no Prestige still works (100 → 120 → 180)",
        bOnly(100).total === 180, bOnly(100).total);
}

console.log("\na bonus that cannot be read is never allowed to remove XP");
{
  const missing = xpEngine();                                   // module not loaded
  const broken  = xpEngine({ night: { active: true, mult: 0 } });
  const nan     = xpEngine({ night: { active: true, mult: "x" } });
  const lying   = xpEngine({ night: { active: false, mult: 1.5 } });
  const thrower = (() => {
    const win = { get __ccGameNightXp() { throw new Error("boom"); } };
    const ctx = vm.createContext({ window: win, console, Number, Math, String });
    vm.runInContext(XP_SRC + "\n; this.prestigeXp = prestigeXp;", ctx);
    return ctx.prestigeXp;
  })();
  check("no game-night.js at all → 1x", missing(100).total === 100, missing(100).total);
  check("a 0 multiplier → 1x, not zero XP", broken(100).total === 100, broken(100).total);
  check("a non-numeric multiplier → 1x", nan(100).total === 100, nan(100).total);
  check("not live means not paid, whatever mult says", lying(100).total === 100, lying(100).total);
  check("a seam that throws → 1x", thrower(100).total === 100, thrower(100).total);
}

console.log("\nevery XP path goes through it");
check("the game's XP award is the prestigeXp total",
      /const _pxGame\s+= prestigeXp\(xpAward\);/.test(APP));
check("the daily login bonus, multiplied by hand, includes Game Night",
      /_streakBonusXp \* \(1 \+ prestigeLevelNow\(\) \* 0\.25\)\s*\*\s*passBoostNow\(\)\.mult \* gameNightXpNow\(\)\.mult/
        .test(APP));
check("challenge / meta / event XP rides the same prestigeXp hook",
      /__fishGrantXp = async function[\s\S]{0,900}window\.__fishPrestigeXp\(amount\)/.test(APP));
check("the end screen prints the Game Night line",
      /_pxEnd\.gameNightBonus > 0[\s\S]{0,200}Game Night: \+/.test(APP));
check("the app reads the multiplier through the one exported seam",
      /window\.__ccGameNightXp && window\.__ccGameNightXp\(\)/.test(APP));
check("game-night.js is loaded by the app host",
      /<script[^>]+js\/game-night\.js/.test(read("preview.html")));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
