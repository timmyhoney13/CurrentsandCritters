#!/usr/bin/env node
/* Game Night pays 1.75x XP, and it has to pay it at the right hour.
 *
 * Two halves, both running the REAL code:
 *
 *   1. THE CLOCK. js/game-night.js is evaluated in a vm with a stub window,
 *      and window.__ccGameNightXp() is asked about fixed instants either side
 *      of 7:00 and 9:00 PM America/Chicago, in summer AND winter, plus the
 *      Saturdays on both sides of a DST switch. Then a whole year is swept
 *      minute by minute: the bonus must be on for exactly 120 minutes on
 *      exactly 52 days, every one of them a Saturday. A multiplier that is
 *      live for 180 minutes twice a year is the bug this sweep exists to
 *      catch, and it is invisible to any test that only asks "is it on now?".
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
const MOMENTS = [
  ["a minute before the summer start", "2026-08-22T23:59:00Z", false],
  ["7:00 PM CDT exactly",              "2026-08-23T00:00:00Z", true],
  ["a minute before the end",          "2026-08-23T01:59:00Z", true],
  ["9:00 PM CDT exactly, over",        "2026-08-23T02:00:00Z", false],
  ["after midnight in Chicago",        "2026-08-23T07:00:00Z", false],
  ["a Wednesday evening",              "2026-08-20T01:00:00Z", false],
  ["a minute before the winter start", "2026-12-20T00:59:00Z", false],
  ["7:00 PM CST exactly",              "2026-12-20T01:00:00Z", true],
  ["9:00 PM CST exactly, over",        "2026-12-20T03:00:00Z", false],
  ["the Saturday before DST ends",     "2026-11-01T00:00:00Z", true],
  ["the Saturday after DST ends",      "2026-11-08T01:00:00Z", true],
  ["the Saturday before DST starts",   "2027-03-14T01:00:00Z", true],
];
for (const [label, iso, want] of MOMENTS) {
  const st = gnXp(new Date(iso));
  check(`${label} (${chicago(iso)}): ${want ? "1.75x" : "1x"}`,
        st.active === want && st.mult === (want ? 1.75 : 1),
        `active=${st.active} mult=${st.mult}`);
}

// The sweep. Anything that drifts, an offset baked in as UTC-6, a window that
// widens across a DST switch, a session that lands on Sunday for readers east
// of Chicago, shows up here and nowhere else.
//
// Two passes rather than one, because a whole year at minute resolution is
// ~525k timezone conversions and takes minutes to run: an HOURLY pass over the
// year proves no session ever lands on the wrong day, and a MINUTE pass over
// the evening of each Saturday proves each one is exactly 2 hours long.
{
  const dayOf = (d) => new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago", weekday: "short", year: "numeric",
    month: "2-digit", day: "2-digit",
  }).format(d);

  const HOUR = 3600000, MIN = 60000;
  const hourly = new Map();          // Chicago day → live hours seen
  const saturdays = [];
  for (let t = Date.UTC(2026, 0, 1); t < Date.UTC(2027, 0, 1); t += HOUR) {
    const d = new Date(t);
    const key = dayOf(d);
    if (key.startsWith("Sat") && !saturdays.includes(key)) saturdays.push(key);
    if (!gnXp(d).active) continue;
    hourly.set(key, (hourly.get(key) || 0) + 1);
  }
  const liveDays = [...hourly.keys()];
  check("across 2026 the bonus runs on 52 days", liveDays.length === 52, liveDays.length);
  check("…every one of them a Saturday in Chicago",
        liveDays.every(k => k.startsWith("Sat")),
        liveDays.filter(k => !k.startsWith("Sat")).join(", "));
  check("…and never a Saturday is skipped",
        saturdays.filter(k => !hourly.has(k)).length === 0,
        saturdays.filter(k => !hourly.has(k)).join(", "));

  // Minute resolution, 5:00 PM → 11:00 PM Chicago on each of those Saturdays,
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
  check("…and unbroken, one run from 7:00 to 9:00",
        gappy.length === 0, gappy.join(", "));
}

// A Date from another realm (this test's, an iframe's) must be understood, and
// a ms timestamp too. Answering about "now" instead would be silent and wrong.
check("an instant handed in from another realm is honoured",
      gnXp(new Date("2026-08-23T00:30:00Z")).active === true
      && gnXp(new Date("2026-08-20T00:30:00Z")).active === false);
check("a plain millisecond timestamp works the same",
      gnXp(Date.parse("2026-08-23T00:30:00Z")).active === true
      && gnXp(Date.parse("2026-08-20T00:30:00Z")).active === false);
check("garbage falls back to now, never to a bogus instant",
      typeof gnXp("nonsense").active === "boolean");

console.log("\nthe banner promises what the code pays");
check("the multiplier is one constant, not a number typed into the copy",
      /const XP_MULT = 1\.75;/.test(GN));
check("the label is derived from that constant", /XP_LABEL = String\(Number\(XP_MULT/.test(GN));
check("the chip is rendered from the label, never a literal",
      /class="ccGN-xp[^]{0,80}\$\{esc\(XP_LABEL\)\}/.test(GN));
check("the note says which XP it applies to",
      /Games, challenges and your daily bonus all pay \$\{esc\(XP_LABEL\)\} XP/.test(GN));
check("the chip has a style to be seen in", /\.ccGN-xp\s*\{/.test(read("css/game-night.css")));

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
const LIVE = { active: true, mult: 1.75, percent: 75, label: "1.75x" };
const OFF  = { active: false, mult: 1, percent: 0, label: "1.75x" };
const BOOST = { active: true, percent: 20, mult: 1.2 };

console.log("\n1.75x, applied to the XP a game actually pays");
{
  const plain = xpEngine();
  const live  = xpEngine({ night: LIVE });
  check("off Game Night nothing changes", plain(100).total === 100, plain(100).total);
  check("a 100 XP game pays 175 during Game Night", live(100).total === 175, live(100).total);
  check("the bonus is broken out for the end screen",
        live(100).gameNightBonus === 75 && live(100).gameNight === true,
        JSON.stringify(live(100)));
  check("the label rides along so the screen can print '1.75x'",
        live(100).gameNightLabel === "1.75x", live(100).gameNightLabel);
  check("an odd number rounds down, never up (57 → 99)",
        live(57).total === 99, live(57).total);
  check("zero XP stays zero", live(0).total === 0 && live(0).gameNightBonus === 0);
}

console.log("\nit stacks on top of the other two, in that order");
{
  const p2   = xpEngine({ prestige: 2, night: LIVE });
  const both = xpEngine({ prestige: 2, boost: BOOST, night: LIVE });
  const bOnly = xpEngine({ boost: BOOST, night: LIVE });
  // 100 → Prestige 2 (×1.5) = 150 → Game Night (×1.75) = 262
  check("Prestige first, then Game Night (100 → 150 → 262)",
        p2(100).total === 262, p2(100).total);
  check("…and the Prestige share is still reported on its own",
        p2(100).prestigeBonus === 50 && p2(100).gameNightBonus === 112,
        `${p2(100).prestigeBonus} / ${p2(100).gameNightBonus}`);
  // 100 → ×1.5 = 150 → ×1.2 = 180 → ×1.75 = 315
  check("all three stack (100 → 150 → 180 → 315)", both(100).total === 315, both(100).total);
  check("the boost's own line is unchanged by the new multiplier",
        both(100).boostBonus === 30 && both(100).boostPercent === 20,
        `${both(100).boostBonus} / ${both(100).boostPercent}`);
  check("bonus is still everything above base",
        both(100).bonus === both(100).total - 100, both(100).bonus);
  check("a boost with no Prestige still works (100 → 120 → 210)",
        bOnly(100).total === 210, bOnly(100).total);
}

console.log("\na bonus that cannot be read is never allowed to remove XP");
{
  const missing = xpEngine();                                   // module not loaded
  const broken  = xpEngine({ night: { active: true, mult: 0 } });
  const nan     = xpEngine({ night: { active: true, mult: "x" } });
  const lying   = xpEngine({ night: { active: false, mult: 1.75 } });
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
