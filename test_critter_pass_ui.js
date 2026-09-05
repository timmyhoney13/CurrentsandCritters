/* ================================================================
 * test_critter_pass_ui.js, the client half of the Critter Pass, in a
 * real browser.
 *
 * js/critter-pass.js is driven headlessly against payloads produced by
 * the REAL critter_pass_server.py, so the two halves cannot drift apart
 * while both look green on their own.
 *
 * What is actually being protected here:
 *
 *   1. The bridge ENVELOPE. post() resolves to { ok, status, data } and
 *      a module that forgets to unwrap `.data` throws mid-render and
 *      leaves a BLANK tab with nothing in the console, the exact way
 *      the Clans tab shipped once. Every stub below returns the real
 *      envelope, never the bare payload, because a bare-payload stub
 *      hides that bug completely.
 *   2. The LOCKED state. A player who has not bought the pass must see
 *      the whole track and the price, and NO claim buttons anywhere.
 *      A Claim button on a locked pass is a promise the server refuses.
 *   3. The purchase card's numbers, which are the sales pitch: they all
 *      come off the served track, so a retune moves them together.
 *   4. __ccPassExtraSlots(), the seam the challenge strip reads
 *      synchronously on every repaint. Unloaded it must report zero,
 *      and it must clamp.
 *   5. Width, at FIVE sizes. A check at one window size once passed
 *      while every screen under 1100px was unusable.
 *
 * WHY THE WIDTHS ARE MEASURED IN IFRAMES
 * Headless Chrome silently clamps --window-size to about 500px wide, so
 * asking for a 390px window gets you a 500px one and a phone is never
 * actually tested. An iframe has its own viewport, and media queries
 * inside it evaluate against ITS width, so these really are 390px.
 *
 *   node test_critter_pass_ui.js
 * ================================================================ */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer", "client");

const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

const read = (rel) => fs.readFileSync(path.join(CLIENT, rel), "utf8");
// The server's own maximum, read out of the module rather than typed here.
const P_MAX_EXTRA = Number(
  (fs.readFileSync(path.join(ROOT, "critter_pass_server.py"), "utf8")
     .match(/^MAX_EXTRA_DAILY = (\d+)$/m) || [])[1]);
const APP = read("js/preview-app.js");
const HTML = read("preview.html");
const PASSJS = read("js/critter-pass.js");
const PASSCSS = read("css/critter-pass.css");

let pass = 0, fail = 0;
const check = (n, c, extra) => {
  if (c) { pass++; console.log("  ✓ " + n); }
  else { fail++; console.log("  ✗ FAIL: " + n + (extra !== undefined ? "  → " + extra : "")); }
};

// ══════════════════════════════════════════════════════════════════════════
//  STATIC WIRING
//  These run with or without Chrome, because a tab that is not wired in is
//  a feature nobody can reach however well it renders.
// ══════════════════════════════════════════════════════════════════════════
console.log("\nwiring: the tab exists and is reachable");
check("the sidebar has a Critter Pass button", HTML.includes('id="snav-critterpass"'));
check("it carries an unclaimed badge like the other tabs",
      HTML.includes('id="snav-critterpass-badge"') && HTML.includes('class="ph-snav-badge"'));
check("its panel and root exist",
      HTML.includes('id="ph-panel-critterpass"') && HTML.includes('id="cc-critter-pass-root"'));
check("it sits directly under the Level Pass, above the Store",
      HTML.indexOf('id="snav-levelpass"') < HTML.indexOf('id="snav-critterpass"')
      && HTML.indexOf('id="snav-critterpass"') < HTML.indexOf('id="snav-store"'));
check("the module and stylesheet are served",
      HTML.includes("/js/critter-pass.js") && HTML.includes("/css/critter-pass.css"));
check("the panel is in the tab map", APP.includes('critterpass:"ph-panel-critterpass"'));
check("switching to it renders it", APP.includes('if (name === "critterpass")  _renderCritterPassTab();'));
check("a module that never loads says so instead of staying blank",
      /function _renderCritterPassTab\(attempt\)/.test(APP)
      && APP.includes("The Critter Pass didn't finish loading"));
check("the bridge exists", APP.includes("window.__ccCritterPass = {"));
check("the bridge can open a confirm modal (4,000 coins is not a mis-tap)",
      /window\.__ccCritterPass = \{[\s\S]{0,1400}?modal:/.test(APP));
check("it is primed on sign-in", APP.includes("window.__ccCritterPassPrime && window.__ccCritterPassPrime()"));
check("it is reset on BOTH identity-change paths",
      (APP.match(/window\.__ccCritterPassReset && window\.__ccCritterPassReset\(\)/g) || []).length === 2);
check("a guest is told what the tab needs an account for",
      /critterpass:\s*"[^"]*account/.test(APP));

console.log("\nwiring: the kelp forest really is the background");
check("the page paints /backgrounds/kelp-forest.png",
      PASSCSS.includes('url("/backgrounds/kelp-forest.png")'));
check("that file exists", fs.existsSync(path.join(CLIENT, "backgrounds", "kelp-forest.png")));
check("a WebP sibling exists for it, like every other served image",
      fs.existsSync(path.join(CLIENT, "backgrounds", "kelp-forest.webp")));
check("the art is under a scrim, so white text on it is legible",
      /--cp-scrim-top/.test(PASSCSS)
      && /linear-gradient\(180deg, var\(--cp-scrim-top\)/.test(PASSCSS));
check("it does not borrow the Level Pass's class names",
      !/\.ccLP-/.test(PASSCSS) && !/ccLP/.test(PASSJS));

// ── .ccCP AND --cp-* BELONG TO THIS PAGE ALONE ────────────────────────────
// css/clan-prize.css was also prefixed .ccCP, and both files load on Player
// Home. Its `.ccCP { --cp-ink: #fff6e2 }` landed on the pass's own wrapper,
// one level BELOW #cc-critter-pass-root, and a custom property set on the
// closer element wins by inheritance however specific the outer selector is:
// every word on the pass reading var(--cp-ink) came out cream on a cream
// card at 1.05:1. Its .ccCP-title also loads later, so it took the title's
// font and size with it. None of the contrast arithmetic below can see that,
// because it reads this stylesheet alone; only a namespace check can.
const OTHER_CSS = fs.readdirSync(path.join(CLIENT, "css"))
  .filter(f => f.endsWith(".css") && f !== "critter-pass.css");
// Comments stripped first: clan-prize.css names .ccCP in its header precisely
// to say it must never use it again, and that sentence is not a selector.
const rules = (f) => read("css/" + f).replace(/\/\*[\s\S]*?\*\//g, "");
const squatters = OTHER_CSS.filter(f => /(^|[\s,>+~{])\.ccCP[\s.,:{[>+~-]/.test(rules(f)));
check("no other stylesheet styles a .ccCP class", squatters.length === 0, squatters.join(", "));
const tokenSquatters = OTHER_CSS.filter(f => /--cp-[a-z0-9-]+\s*:/.test(rules(f)));
check("no other stylesheet defines a --cp-* token", tokenSquatters.length === 0,
      tokenSquatters.join(", "));

// ══════════════════════════════════════════════════════════════════════════
//  NOTHING ON THIS PAGE IS WRITTEN IN A TAN
//  The pass is a gold-branded surface, and gold ink is the trap: #c89320 on a
//  cream card is 2.7:1 and #ffe07a on the scrim reads as a washed-out tan.
//  Gold is allowed on BORDERS, BACKGROUNDS and BUTTON FILLS, where it is a
//  surface. It is not allowed to be a word. This reads the stylesheet's real
//  `color:` declarations rather than grepping for a list of hexes somebody has
//  to remember to update, so a tan reintroduced under a new name still fails.
// ══════════════════════════════════════════════════════════════════════════
console.log("\nlegibility: no tan text anywhere on the pass");

const cssVars = {};
for (const m of PASSCSS.matchAll(/(--cp-[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
  cssVars[m[1]] = m[2].trim();
}
const hex = (c) => {
  c = String(c).trim();
  const v = /^var\((--[a-z0-9-]+)\)$/.exec(c);
  if (v) return hex(cssVars[v[1]] || "");
  if (/^#[0-9a-f]{3}$/i.test(c)) return "#" + c.slice(1).split("").map(x => x + x).join("");
  return /^#[0-9a-f]{6}$/i.test(c) ? c.toLowerCase() : null;
};
const rgb = (h) => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255);
const hsl = (h) => {
  const [r, g, b] = rgb(h), mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  const l = (mx + mn) / 2;
  if (!d) return { h: 0, s: 0, l };
  const s = d / (1 - Math.abs(2 * l - 1));
  const hu = mx === r ? ((g - b) / d + (g < b ? 6 : 0)) : mx === g ? ((b - r) / d + 2) : ((r - g) / d + 4);
  return { h: hu * 60, s, l };
};
const lum = (h) => {
  const f = (c) => c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  const [r, g, b] = rgb(h).map(f);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a, b) => {
  const la = lum(a), lb = lum(b), hi = Math.max(la, lb), lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
};
// Tan/gold/beige: an orange-yellow hue with enough saturation to read as a
// colour and enough lightness that it is not simply a dark brown-black.
const isTan = (h) => {
  const c = hsl(h);
  return c.h >= 20 && c.h <= 68 && c.s >= 0.18 && c.l >= 0.30;
};

// Every `color:` declaration in the stylesheet, excluding border-color.
const fontColors = [];
for (const m of PASSCSS.matchAll(/(^|[^-\w])color:\s*([^;!]+)/gm)) {
  const h = hex(m[2]);
  if (h) fontColors.push(h);
}
check("the stylesheet's font colours were actually found", fontColors.length > 20, fontColors.length);
const tans = [...new Set(fontColors)].filter(isTan);
check("not one of them is a tan", tans.length === 0, tans.join(", "));
check("the gold IS still used, just never as a word",
      /border-color: var\(--cp-gold\)/.test(PASSCSS)
      && /background: linear-gradient\(180deg, var\(--cp-gold-lt\)/.test(PASSCSS));

// The specific pairings that were unreadable, pinned as contrast so a future
// "just a shade lighter" cannot walk them back.
const CREAM = "#fffdf6";   // the purchase card / a cream tier
const SCRIM = "#0d3a4f";   // the darkened kelp behind the header
const contrastPairs = [
  ["the purchase card's accent number", hex(cssVars["--cp-ink"]), CREAM, 4.5],
  ["the season kicker", hex(cssVars["--cp-ink"]), CREAM, 4.5],
  ["'Unlock to claim' on a cream tier", hex(cssVars["--cp-blue"]), CREAM, 4.5],
  ["the tier blurbs' small print", hex(cssVars["--cp-ink-dim"]), CREAM, 4.5],
  ["the caption under a tier card", hex(cssVars["--cp-ink-soft"]), CREAM, 4.5],
  ["the header's XP number on the kelp", "#8fe4ff", SCRIM, 4.5],
];
for (const [what, fg, bg, min] of contrastPairs) {
  const r = ratio(fg, bg);
  check(`${what} passes contrast (${fg})`, r >= min, `${r.toFixed(2)}:1, want ${min}`);
}

// ══════════════════════════════════════════════════════════════════════════
//  THE 30-DAY SEASON CLOCK
// ══════════════════════════════════════════════════════════════════════════
console.log("\nwiring: the season counts down 30 days");
check("the server owns the countdown, the browser only prints it",
      PASSJS.includes("_state.seasonDaysLeft")
      && !/Date\.now\(\)\s*[-+][\s\S]{0,40}season/i.test(PASSJS));
check("a payload without the field draws no countdown at all",
      /_state\.seasonDaysLeft == null\) return "";/.test(PASSJS));
check("a finished season promises a new one instead of a lock",
      /New season coming soon/.test(PASSJS));
check("'1 day' is singular", /d === 1 \? "" : "s"/.test(PASSJS));
check("the chip has its own style, not tan", /\.ccCP-season-left \{/.test(PASSCSS));

console.log("\nwiring: the challenge-slot seam");
check("the pass publishes __ccPassExtraSlots", PASSJS.includes("window.__ccPassExtraSlots = function"));
check("the challenge strip reads it", APP.includes("window.__ccPassExtraSlots ? window.__ccPassExtraSlots()"));
check("the slot count is derived, not hard-coded, for dailies",
      APP.includes("_rollDailyIndices(_dailySlotCount())"));
check("…and for weeklies", APP.includes("_rollWeeklyIndices(_weeklySlotCount())"));
check("a stored day with more than three slots is still valid",
      !/obj\.slots\.length === 3/.test(APP));
check("slots are reconciled on every load, not only when the day rolls",
      /function _csReconcileSlots\(state, want, poolLen\)/.test(APP)
      && (APP.match(/if \(_csReconcileSlots\(state,/g) || []).length === 2);
check("the Tide Sweep still asks for the BASE three, however many you own",
      APP.includes("sweepTarget:  _CS_BASE_SLOTS")
      && APP.includes("sweepTarget:    _CS_BASE_SLOTS")
      && !/completedCount >= 3\b/.test(APP));
check("the strip's copy counts the slots you actually have",
      APP.includes("_csCountWord(total)") && !APP.includes("`Three fresh challenges every day."));
check("the Tide Sweep bar and its caption measure the same thing",
      /const sweepDone = Math\.min\(done, sweepN\);/.test(APP)
      && /sweepPct = Math\.min\(100, Math\.round\(\(sweepDone \/ Math\.max\(1, sweepN\)\)/.test(APP)
      && APP.includes("for the Sweep"));
check("a shrink keeps completed slots before incomplete ones",
      /const done = state\.slots\.filter\(s => s\.completedAt\);[\s\S]{0,200}slice\(0, target\)/.test(APP));

// ══════════════════════════════════════════════════════════════════════════
//  THE SLOTS THEMSELVES, RUN FOR REAL
//  The section above proves the WIRING with source regexes. This runs the
//  shipped slot code: the real _DAILY_CHALLENGES / _WEEKLY_CHALLENGES pools and
//  the real load / reconcile / meta functions are lifted out of preview-app.js
//  by their own markers and executed against a localStorage stub, with
//  __ccPassExtraSlots answering whatever this test says it does.
//
//  Rename one of those functions or move a marker and the lift throws. That is
//  intentional: a regex cannot tell you that six slots actually appear.
// ══════════════════════════════════════════════════════════════════════════
console.log("\nthe slots, run for real");

function lift(from, to) {
  const a = APP.indexOf(from);
  const b = APP.indexOf(to, a + 1);
  if (a < 0 || b < 0) throw new Error(`could not lift ${JSON.stringify(from)} .. ${JSON.stringify(to)}`);
  return APP.slice(a, b);
}

const slotSrc = [
  lift("    const _DAILY_CHALLENGES = [", "\n    const _WEEKLY_CHALLENGES = ["),
  lift("    const _WEEKLY_CHALLENGES = [", "\n    // Meta rewards, auto-fired"),
  lift("    // ── Extra challenge slots (a Critter Pass reward)",
       "    // ── Weekly Swap (a Level Pass reward)"),
  lift('    const _WEEKLY_STATE_KEY = "cc_weekly_state_v1";',
       "    // Mark today as a played day."),
].join("\n");

function runSlots(extra) {
  const vm = require("vm");
  const store = new Map();
  const sandbox = {
    console,
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    ccScopedKey: (k) => "u1::" + k,
    Math, Date, JSON, Number, Object, Array, Set, String,
  };
  sandbox.window = sandbox;
  sandbox.__ccPassExtraSlots = () => extra;
  vm.createContext(sandbox);
  vm.runInContext("(function(){\n" + slotSrc + "\n"
    + "window.__T = { _loadDailyState, _saveDailyState, _getDailyDisplaySlots,"
    + " _getDailyMeta, _loadWeeklyState, _saveWeeklyState, _getWeeklyDisplaySlots,"
    + " _getWeeklyMeta, _DAILY_CHALLENGES, _WEEKLY_CHALLENGES,"
    + " _CS_BASE_SLOTS, _CS_MAX_EXTRA };\n})();", sandbox);
  return { T: sandbox.__T, store };
}

// ── No pass at all: three and three, exactly as before ────────────────────
{
  const { T } = runSlots({ daily: 0, weekly: 0 });
  check("with no pass, a fresh day rolls three dailies", T._getDailyDisplaySlots().length === 3);
  check("with no pass, a fresh week rolls three weeklies", T._getWeeklyDisplaySlots().length === 3);
  const dm = T._getDailyMeta(), wm = T._getWeeklyMeta();
  check("the daily meta counts three", dm.totalCount === 3, dm.totalCount);
  check("the weekly meta counts three", wm.totalCount === 3, wm.totalCount);
  check("the sweep target is three", dm.sweepTarget === 3 && wm.sweepTarget === 3);
  check("three unique dailies, not the same job three times",
        new Set(T._getDailyDisplaySlots().map(s => s.id)).size === 3);
}

// ── The full pass: six and six ────────────────────────────────────────────
{
  const { T } = runSlots({ daily: 3, weekly: 3 });
  const d = T._getDailyDisplaySlots(), w = T._getWeeklyDisplaySlots();
  check("with the pass, a fresh day rolls SIX dailies", d.length === 6, d.length);
  check("with the pass, a fresh week rolls SIX weeklies", w.length === 6, w.length);
  check("all six dailies are different challenges", new Set(d.map(s => s.id)).size === 6);
  check("all six weeklies are different challenges", new Set(w.map(s => s.id)).size === 6);
  check("every one of them is a real challenge with XP on it",
        d.concat(w).every(s => s.id && s.name && Number(s.xp) > 0));
  const dm = T._getDailyMeta();
  check("the meta reports six", dm.totalCount === 6, dm.totalCount);
  check("…but the Tide Sweep still only asks for three", dm.sweepTarget === 3, dm.sweepTarget);
}

// ── The tier is claimed mid-day: the slots GROW, keeping progress ─────────
{
  const { T, store } = runSlots({ daily: 0, weekly: 0 });
  const st = T._loadDailyState();
  st.slots[0].progress = 2;
  st.slots[0].completedAt = 111;
  T._saveDailyState(st);
  const kept = st.slots.map(s => s.idx);

  // Same stored day, same browser, now with two daily tiers claimed.
  const { T: T2 } = (() => {
    const r = runSlots({ daily: 2, weekly: 0 });
    // Re-seed the fresh sandbox with the day we just saved.
    r.store.set("u1::cc_daily_state_v1", store.get("u1::cc_daily_state_v1"));
    return r;
  })();
  const grown = T2._loadDailyState();
  check("claiming a tier grows the day from three slots to five",
        grown.slots.length === 5, grown.slots.length);
  check("the three that were already there are untouched",
        kept.every(idx => grown.slots.some(s => s.idx === idx)));
  check("the completed one is still completed",
        grown.slots.some(s => s.completedAt === 111 && s.progress === 2));
  check("the new ones start empty",
        grown.slots.filter(s => !kept.includes(s.idx)).every(s => !s.completedAt && !s.progress));
  check("no challenge is duplicated by the growth",
        new Set(grown.slots.map(s => s.idx)).size === grown.slots.length);
  const meta = T2._getDailyMeta();
  check("one of five done, and the sweep still wants three",
        meta.completedCount === 1 && meta.totalCount === 5 && meta.sweepTarget === 3,
        JSON.stringify(meta));
}

// ── Signing out: the slots shrink, and completed work survives ────────────
{
  const { T, store } = runSlots({ daily: 3, weekly: 0 });
  const st = T._loadDailyState();
  check("started with six", st.slots.length === 6, st.slots.length);
  // Complete the LAST two, the ones a naive trim-from-the-end would delete.
  st.slots[4].completedAt = 222; st.slots[4].progress = 9;
  st.slots[5].completedAt = 333; st.slots[5].progress = 9;
  T._saveDailyState(st);

  const r = runSlots({ daily: 0, weekly: 0 });
  r.store.set("u1::cc_daily_state_v1", store.get("u1::cc_daily_state_v1"));
  const shrunk = r.T._loadDailyState();
  check("without the pass it drops back to three", shrunk.slots.length === 3, shrunk.slots.length);
  check("both completed challenges survived the shrink",
        shrunk.slots.filter(s => s.completedAt).length === 2,
        JSON.stringify(shrunk.slots.map(s => s.completedAt)));
  check("…and the sweep still reads two of three",
        r.T._getDailyMeta().completedCount === 2);
}

// ── A tampered store cannot buy slots the server never granted ────────────
{
  const { T, store } = runSlots({ daily: 3, weekly: 3 });
  T._loadDailyState();
  const raw = JSON.parse(store.get("u1::cc_daily_state_v1"));
  raw.slots = raw.slots.concat(raw.slots).slice(0, 12);
  store.set("u1::cc_daily_state_v1", JSON.stringify(raw));
  const r = runSlots({ daily: 3, weekly: 3 });
  r.store.set("u1::cc_daily_state_v1", store.get("u1::cc_daily_state_v1"));
  check("a hand-edited twelve-slot day is refused and re-rolled to six",
        r.T._loadDailyState().slots.length === 6, r.T._loadDailyState().slots.length);
}

// ── An unloaded pass is three, never a crash ──────────────────────────────
{
  const vmMod = require("vm");
  const { T } = (() => {
    const r = runSlots({ daily: 1, weekly: 1 });
    return r;
  })();
  check("the base and the max are the numbers the server was told",
        T._CS_BASE_SLOTS === 3 && T._CS_MAX_EXTRA === P_MAX_EXTRA,
        `${T._CS_BASE_SLOTS} / ${T._CS_MAX_EXTRA}`);
  check("one claimed tier is four slots", T._getDailyDisplaySlots().length === 4);
  void vmMod;
}

if (!CHROME) {
  console.log(`\n${pass} passed, ${fail} failed  (SKIPPED the render half: no Chrome/Chromium found)`);
  process.exit(fail ? 1 : 0);
}

// ── Real payloads, straight out of the Python server ──────────────────────
// This is the seam that matters: shapes invented by hand in a test file drift
// from the server the moment somebody renames a field.
function serverPayloads() {
  const script = `
import json, sys
sys.path.insert(0, ${JSON.stringify(ROOT)})
import critter_pass_server as cp
from test_level_pass_server import FakeDb, ArrayUnion, LEVEL_TOTALS, level_progress, xp_for_level

BG = ["/backgrounds/bg-kelp.png", "/backgrounds/bg-coral-reef.png"]
db = FakeDb()
cp.init(get_firestore=lambda: db, verify_token=lambda t: None,
        level_for_xp=level_progress, level_totals=LEVEL_TOTALS, background_paths=list(BG))
cp._transactional = lambda: (lambda fn: fn)
cp._array_union = lambda: ArrayUnion

# LOCKED: level 30, plenty of coins, has NOT bought the pass. The state the
# page is in for everybody the first time they open it.
db.collection("users")._docs["locked"] = {
    "nickname": "Reef Boss",
    "stats": {"total_xp": xp_for_level(30) + 500, "critter_coins": 6200},
}
locked = cp.state_payload("locked")

# VOUCHER HOLDER: no coins at all, but two Season Pass vouchers from a
# Supporter Tier. The purchase card must offer to REDEEM, never to spend.
db.collection("users")._docs["voucher"] = {
    "nickname": "Backer",
    "stats": {"total_xp": xp_for_level(30), "critter_coins": 0},
    "critter_pass_vouchers": 2,
}
voucher = cp.state_payload("voucher")

# LOCKED AND SHORT: the same player, 12 coins to their name.
db.collection("users")._docs["broke"] = {
    "nickname": "Skint", "stats": {"total_xp": xp_for_level(30), "critter_coins": 12},
}
broke = cp.state_payload("broke")

# OWNED, mid-track: bought it, PASS level 30 on an ACCOUNT level 12, and
# claimed everything up to 12, so tiers 13-30 are sitting there ready. The two
# levels are deliberately BOTH different and crossed over: the rail has to be
# drawn from the pass level, the chip from the account level, and a fixture
# where they happened to match would prove neither.
db.collection("users")._docs["owner"] = {
    "nickname": "Pass Holder",
    "stats": {"total_xp": xp_for_level(12) + 500, "critter_coins": 2200},
    "critter_pass_seasons": [cp.SEASON_ID],
    cp.SEASON_FIELD: {cp.SEASON_ID: {"xp": cp.season_xp_to_reach(30),
                                     "mark": xp_for_level(12) + 500}},
    "unlocked_icons": ["/avatars/narwhal.png", "/avatars/orca.png"],
}
for t in cp.track():
    if t["level"] <= 12:
        cp.claim(db, "owner", t["id"])
owner = cp.state_payload("owner")

# MAXED: PASS level 100, whole track claimed.
db.collection("users")._docs["maxed"] = {
    "nickname": "Done",
    "stats": {"total_xp": xp_for_level(100), "critter_coins": 0},
    "critter_pass_seasons": [cp.SEASON_ID],
    cp.SEASON_FIELD: {cp.SEASON_ID: {"xp": cp.SEASON_XP_TO_MAX,
                                     "mark": xp_for_level(100)}},
    "unlocked_icons": ["/avatars/a%d.png" % i for i in range(1, 15)],
}
# claim_all pays a bounded batch and reports "more", so a full sweep is a loop:
# exactly what js/critter-pass.js does.
for _ in range(12):
    r = cp.claim_all(db, "maxed")
    if not r.get("more") or not r.get("count"):
        break
maxed = cp.state_payload("maxed")

# SIGNED OUT.
out = cp.state_payload(None)

print("@@" + json.dumps({"locked": locked, "broke": broke, "owner": owner,
                         "voucher": voucher,
                         "maxed": maxed, "out": out,
                         "price": cp.CRITTER_PASS_PRICE,
                         "coinTotal": cp.coin_total(),
                         "xpTotal": cp.xp_total(),
                         "seasonDays": cp.SEASON_DAYS,
                         "passMaxLevel": cp.PASS_MAX_LEVEL,
                         "seasonXpPerLevel": cp.SEASON_XP_PER_LEVEL,
                         "seasonXpPerDay": cp.SEASON_XP_PER_DAY,
                         "maxExtraDaily": cp.MAX_EXTRA_DAILY}) + "@@")
`;
  const out = execFileSync("python3", ["-c", script],
    { encoding: "utf8", cwd: ROOT, maxBuffer: 32 * 1024 * 1024 });
  const m = out.match(/@@([\s\S]*?)@@/);
  if (!m) throw new Error("no payload from critter_pass_server:\n" + out);
  return JSON.parse(m[1]);
}

const P = serverPayloads();
// EVERY stylesheet Player Home loads, in preview.html's own order. The harness
// used to inject critter-pass.css alone, and that is exactly how the pass
// shipped with every word on it painted cream: clan-prize.css was prefixed
// .ccCP too, its `--cp-ink: #fff6e2` landed on the pass's own wrapper, and it
// loads last. A one-stylesheet harness cannot see a cross-file bleed, so it
// gets all of them and measures the result.
const CSS_ORDER = [...HTML.matchAll(/<link[^>]+href="\/css\/([^"?]+)/g)].map(m => m[1]);
const ALLCSS = CSS_ORDER
  .filter(f => fs.existsSync(path.join(CLIENT, "css", f)))
  .map(f => "/* " + f + " */\n" + read("css/" + f))
  .join("\n");
const SRC = { css: ALLCSS, js: PASSJS };
const WIDTHS = [1440, 1280, 1024, 820, 390];

// ── The harness page ──────────────────────────────────────────────────────
// One Chrome run. The outer page builds one iframe per width, writes the whole
// harness into each, and collects the results once they all report in.
const page = `<!doctype html><html><head><meta charset="utf-8">
<style>body{margin:0;background:#eef;font-family:Nunito,sans-serif}
 iframe{display:block;border:0;height:1600px;margin:0 0 8px}</style>
</head><body>
<div id="RESULT" style="display:none"></div>
<script>
window.__SRC = ${JSON.stringify(SRC)};
window.__PAYLOADS = ${JSON.stringify(P)};
window.__WIDTHS = ${JSON.stringify(WIDTHS)};

function innerHtml() {
  const S = window.__SRC;
  return '<!doctype html><html><head><meta charset="utf-8">'
    + '<style>body{margin:0;font-family:Nunito,sans-serif;background:#dff1ff}'
    + '.panel{padding:16px}</style>'
    + '<style>' + S.css + '</style>'
    + '</head><body>'
    + '<button><span id="snav-critterpass-badge" style="display:none">0</span></button>'
    + '<div class="panel"><div id="cc-critter-pass-root"></div></div>'
    + '<scr' + 'ipt>' + BOOT + '</scr' + 'ipt>'
    + '<scr' + 'ipt>' + S.js + '</scr' + 'ipt>'
    + '<scr' + 'ipt>' + MAIN + '</scr' + 'ipt>'
    + '</body></html>';
}

// The bridge. post() resolves to the ENVELOPE the real apiPost returns:
// { ok, status, data }, NOT the bare body. A stub returning the bare body
// would let an unwrap bug sail straight through this test.
const BOOT = \`
  window.__toasts = [];
  window.__posts = [];
  window.__modals = [];
  window.__modalAnswer = { action: "confirm", selected: [] };
  function envelope(data) { return { ok: true, status: 200, data: data }; }
  window.__ccCritterPass = {
    idToken: async () => "tok",
    avSrc: (u) => u,
    toast: (m, t) => window.__toasts.push([m, t]),
    onGranted: () => { window.__granted = (window.__granted || 0) + 1; },
    modal: async (o) => { window.__modals.push(o); return window.__modalAnswer; },
    post: async (p, b) => {
      window.__posts.push([p, b]);
      if (p === "/api/critterpass/state") return envelope(window.__CP_STATE);
      if (p === "/api/critterpass/buy") {
        // The real server flips ownership and the client re-reads rather than
        // guessing, so flip it here too.
        window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.owner));
        return envelope({ ok: true, season: "S1", paid: parent.__PAYLOADS.price });
      }
      if (p === "/api/critterpass/claim") {
        const st = window.__CP_STATE;
        if (!st.claimed.includes(b.tier)) st.claimed = st.claimed.concat([b.tier]);
        return envelope({ ok: true, tier: b.tier, level: 4,
          granted: { type: "coins", coins: 100 }, inventory: st.inventory });
      }
      if (p === "/api/critterpass/claim-all") {
        // A server that pays a bounded batch and says there is more, the way
        // the real one does on a 100-tier track. Round 2 PAYS the tier round 1
        // refused (claiming the Level 100 critter gives the emote tiers
        // something new to draw from), which is the case the client has to
        // stop warning about.
        window.__caCalls = (window.__caCalls || 0) + 1;
        const mk = (n, from) => Array.from({ length: n }, (_, i) =>
          ({ tier: "T" + (from + i), granted: { type: "coins", coins: 10 } }));
        if (window.__caCalls === 1) {
          return envelope({ ok: true, count: 25, more: true, claimed: mk(25, 0),
            skipped: [{ tier: "L7", error: "emotes_full" }] });
        }
        if (window.__caCalls === 2) {
          return envelope({ ok: true, count: 25, more: true,
            claimed: mk(24, 100).concat([{ tier: "L7", granted: { type: "emote" } }]),
            skipped: [] });
        }
        return envelope({ ok: true, count: 5, more: false, claimed: mk(5, 200),
          skipped: [{ tier: "L26", error: "backgrounds_full" }] });
      }
      return envelope({ ok: false, error: "server_error" });
    },
  };
\`;

const MAIN = \`
(async () => {
  const out = { errors: [], locked: {}, broke: {}, owner: {}, maxed: {},
                slots: {}, badge: {}, buy: {}, layout: {} };
  const txt = (el) => (el ? (el.textContent || "").replace(/\\\\s+/g, " ").trim() : "");
  const $$ = (s) => [...document.querySelectorAll(s)];
  try {
    // ── EVERY WORD ON THE PAGE, MEASURED ─────────────────────────────
    // Not the stylesheet's list of colour declarations: the COMPUTED colour
    // of every text node against the background actually painted behind it,
    // which is the only check that sees a token overwritten by another file, a
    // colour inherited from somewhere unexpected, or a card whose background
    // moved out from under its ink.
    const auditInk = () => {
      const rgb = (c) => (String(c).match(/[\\\\d.]+/g) || []).slice(0, 3).map(Number);
      const alpha = (c) => { const m = String(c).match(/[\\\\d.]+/g) || []; return m.length > 3 ? Number(m[3]) : 1; };
      const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
      const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
      const ratio = (a, b) => { const la = lum(a), lb = lum(b), hi = Math.max(la, lb), lo = Math.min(la, lb);
                                return (hi + 0.05) / (lo + 0.05); };
      // The background a reader actually sees. Almost every surface on this
      // page is a GRADIENT, so reading background-color alone walks straight
      // past the cream card and lands on the kelp underneath, which is how a
      // first pass at this check called black-on-cream a failure. Take the
      // nearest ancestor that paints anything opaque, gradient stops
      // included, and score the ink against the WORST stop: a card whose
      // ink only works at one end of its own gradient is not readable.
      const bgAt = (el, ink) => {
        for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
          const cs = getComputedStyle(n);
          const cand = (cs.backgroundImage.match(/rgba?\\([^)]+\\)/g) || [])
            .concat(cs.backgroundColor)
            .filter(c => alpha(c) > 0.5)
            .map(rgb);
          if (cand.length) {
            return cand.reduce((w, c) => ratio(ink, c) < ratio(ink, w) ? c : w);
          }
        }
        return [255, 255, 255];
      };
      const rows = [];
      const walk = document.createTreeWalker(
        document.getElementById("cc-critter-pass-root"), NodeFilter.SHOW_TEXT);
      for (let t = walk.nextNode(); t; t = walk.nextNode()) {
        const words = (t.nodeValue || "").replace(/\\\\s+/g, " ").trim();
        // Emoji carry their own colour; a contrast ratio on one measures
        // nothing. Only text with real letters or digits is scored.
        if (!words || !/[A-Za-z0-9]/.test(words)) continue;
        const el = t.parentElement;
        if (!el) continue;
        const cs = getComputedStyle(el);
        if (cs.visibility === "hidden" || cs.display === "none") continue;
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) continue;
        const ink = rgb(cs.color);
        const bg = bgAt(el, ink);
        const px = parseFloat(cs.fontSize) || 16;
        const bold = (parseInt(cs.fontWeight, 10) || 400) >= 700;
        // WCAG large text: 24px, or 18.66px bold.
        const large = px >= 24 || (bold && px >= 18.66);
        rows.push({
          cls: ((el.className || "") + "").split(" ")[0] || el.tagName,
          words: words.slice(0, 60),
          color: cs.color,
          bg: "rgb(" + bg.join(", ") + ")",
          px: Math.round(px * 10) / 10,
          large,
          shadow: cs.textShadow !== "none",
          r: Math.round(ratio(ink, bg) * 100) / 100,
        });
      }
      return rows;
    };

    // ── Before ANY state has loaded, the seam must answer zero ───────
    out.slots.beforeLoad = window.__ccPassExtraSlots();

    // ── LOCKED ───────────────────────────────────────────────────────
    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.locked));
    await window.__ccCritterPassRender();
    const root = document.getElementById("cc-critter-pass-root");
    out.locked = {
      tiers: $$(".ccCP-tier").length,
      claimBtns: $$(".ccCP-claim").length,
      claimAll: $$("#ccCP-claimall").length,
      waiting: $$(".ccCP-tier.is-waiting").length,
      locked: $$(".ccCP-tier.is-locked").length,
      buyCard: $$(".ccCP-buycard").length,
      buyEnabled: !!(document.getElementById("ccCP-buy") && !document.getElementById("ccCP-buy").disabled),
      buyText: txt(document.getElementById("ccCP-buy")),
      buyTitle: txt(document.querySelector(".ccCP-buy-title")),
      buySub: txt(document.querySelector(".ccCP-buy-sub")),
      highlights: $$(".ccCP-hl").map(txt),
      finaleArt: !!document.querySelector(".ccCP-buy-art img"),
      level: txt(document.querySelector(".ccCP-lvl-num")),
      levelWord: txt(document.querySelector(".ccCP-lvl-word")),
      barTxt: txt(document.querySelector(".ccCP-bar-txt")),
      chips: $$(".ccCP-chip").map(txt).join(" | "),
      ownedPill: $$(".ccCP-owned-pill").length,
      next: txt(document.querySelector(".ccCP-next")),
      season: txt(document.querySelector(".ccCP-season")),
      seasonLeft: txt(document.querySelector(".ccCP-season-left")),
      togo: $$(".ccCP-tier-togo").slice(0, 2).map(txt),
      rootClass: (root.querySelector(".ccCP") || {}).className || "",
    };
    out.badge.whenLocked = document.getElementById("snav-critterpass-badge").style.display;
    out.slots.whenLocked = window.__ccPassExtraSlots();

    // ── LOCKED AND SHORT OF COINS ────────────────────────────────────
    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.broke));
    await window.__ccCritterPassSync();
    out.broke = {
      buyDisabled: !!(document.getElementById("ccCP-buy") && document.getElementById("ccCP-buy").disabled),
      note: txt(document.querySelector(".ccCP-buy-note")),
      claimBtns: $$(".ccCP-claim").length,
    };

    // ── A SEASON PASS VOUCHER IN HAND ────────────────────────────────
    // The Supporter Tier way in. The card must offer to REDEEM, must not quote
    // a coin price to somebody who is not paying one, and must post
    // { voucher: true } so the server spends the voucher and not the wallet.
    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.voucher));
    await window.__ccCritterPassSync();
    out.voucher = {
      inventory: (window.__CP_STATE.inventory || {}).vouchers,
      btn: txt(document.getElementById("ccCP-buy")),
      btnDisabled: !!(document.getElementById("ccCP-buy") && document.getElementById("ccCP-buy").disabled),
      note: txt(document.querySelector(".ccCP-buy-note")),
      sub: txt(document.querySelector(".ccCP-buy-sub")),
      chip: $$(".ccCP-chip").map(txt).join(" | "),
    };
    {
      const vb = document.getElementById("ccCP-buy");
      window.__modals.length = 0;
      window.__posts.length = 0;
      window.__modalAnswer = { action: "confirm", selected: [] };
      if (vb) vb.click();
      for (let i = 0; i < 24; i++) await new Promise(r => setTimeout(r, 0));
      const buys = window.__posts.filter(p => p[0] === "/api/critterpass/buy");
      out.voucher.posted = buys.length;
      out.voucher.postedVoucherFlag = !!(buys[0] && buys[0][1] && buys[0][1].voucher);
      out.voucher.modal = JSON.stringify(window.__modals[0] || {});
      // Leave the recorders empty so the coin-purchase block below still counts
      // only its own modals and posts.
      window.__modals.length = 0;
      window.__posts.length = 0;
    }

    // ── BUYING IT ────────────────────────────────────────────────────
    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.locked));
    await window.__ccCritterPassSync();
    const buyBtn = document.getElementById("ccCP-buy");
    if (buyBtn) {
      window.__modalAnswer = { action: "cancel", selected: [] };
      buyBtn.click();
      for (let i = 0; i < 12; i++) await new Promise(r => setTimeout(r, 0));
      out.buy.cancelledPosts = window.__posts.filter(p => p[0] === "/api/critterpass/buy").length;
      out.buy.askedFirst = window.__modals.length;
      out.buy.modalMentionsPrice = /4,000/.test(JSON.stringify(window.__modals[0] || {}));

      window.__modalAnswer = { action: "confirm", selected: [] };
      const b2 = document.getElementById("ccCP-buy");
      if (b2) b2.click();
      for (let i = 0; i < 24; i++) await new Promise(r => setTimeout(r, 0));
      out.buy.confirmedPosts = window.__posts.filter(p => p[0] === "/api/critterpass/buy").length;
      out.buy.buyCardGone = $$(".ccCP-buycard").length === 0;
      out.buy.owned = window.__ccCritterPassOwned();
    }

    // ── OWNED, mid-track ─────────────────────────────────────────────
    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.owner));
    await window.__ccCritterPassSync();
    out.owner = {
      buyCard: $$(".ccCP-buycard").length,
      claimBtns: $$(".ccCP-claim").length,
      ready: $$(".ccCP-tier.is-ready").length,
      claimed: $$(".ccCP-tier.is-claimed").length,
      waiting: $$(".ccCP-tier.is-waiting").length,
      claimOnLocked: $$(".ccCP-tier.is-locked .ccCP-claim").length,
      claimOnClaimed: $$(".ccCP-tier.is-claimed .ccCP-claim").length,
      ownedPill: txt(document.querySelector(".ccCP-owned-pill")),
      claimAll: txt(document.querySelector("#ccCP-claimall")),
      level: txt(document.querySelector(".ccCP-lvl-num")),
      levelWord: txt(document.querySelector(".ccCP-lvl-word")),
      barTxt: txt(document.querySelector(".ccCP-bar-txt")),
      next: txt(document.querySelector(".ccCP-next")),
      togo: $$(".ccCP-tier-togo").slice(0, 2).map(txt),
      foot: txt(document.querySelector(".ccCP-foot-note")),
      chips: $$(".ccCP-chip-txt").map(txt),
      perks: $$(".ccCP-tier.is-perk").length,
      finale: $$(".ccCP-tier.is-finale").length,
      railScrolls: (() => { const r = document.getElementById("ccCP-rail");
                            return r ? r.scrollWidth > r.clientWidth : false; })(),
      heights: [...new Set($$(".ccCP-tier").slice(0, 12)
                  .map(t => Math.round(t.getBoundingClientRect().height)))],
      minTierW: Math.min(...$$(".ccCP-tier").map(t => Math.round(t.getBoundingClientRect().width))),
    };
    // The gold "ready" and teal "claimed" circles only exist on an UNLOCKED
    // pass, so a locked-only audit never sees the two states most likely to
    // put white on a light fill.
    out.inkOwner = auditInk();
    out.badge.whenOwned = (() => {
      const b = document.getElementById("snav-critterpass-badge");
      return b.style.display === "none" ? null : txt(b);
    })();
    out.slots.whenOwned = window.__ccPassExtraSlots();

    // The badge must come DOWN when a reward is claimed, or it reads as broken.
    const firstClaim = document.querySelector(".ccCP-claim");
    if (firstClaim) {
      firstClaim.click();
      for (let i = 0; i < 12; i++) await new Promise(r => setTimeout(r, 0));
      out.badge.afterOneClaim = (() => {
        const b = document.getElementById("snav-critterpass-badge");
        return b.style.display === "none" ? null : txt(b);
      })();
    }

    // ── CLAIM ALL, which the server pays in bounded batches ──────────
    window.__toasts.length = 0;
    window.__caCalls = 0;
    const allBtn = document.getElementById("ccCP-claimall");
    if (allBtn) {
      allBtn.click();
      for (let i = 0; i < 60; i++) await new Promise(r => setTimeout(r, 0));
    }
    out.claimAll = {
      calls: window.__caCalls,
      toasts: window.__toasts.map(t => t[0]),
      kinds: window.__toasts.map(t => t[1]),
    };

    // ── MAXED ────────────────────────────────────────────────────────
    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.maxed));
    await window.__ccCritterPassSync();
    out.maxed = {
      claimBtns: $$(".ccCP-claim").length,
      claimed: $$(".ccCP-tier.is-claimed").length,
      allclear: txt(document.querySelector(".ccCP-allclear")),
      next: txt(document.querySelector(".ccCP-next")),
      badge: document.getElementById("snav-critterpass-badge").style.display,
    };
    out.slots.whenMaxed = window.__ccPassExtraSlots();

    // A tampered cache must not be able to ask for nine daily challenges.
    window.__CP_STATE.inventory.extraDaily = 99;
    await window.__ccCritterPassSync();
    out.slots.clamped = window.__ccPassExtraSlots();

    // Signing out drops it all, including the extra slots.
    window.__ccCritterPassReset();
    out.slots.afterSignOut = window.__ccPassExtraSlots();
    out.badge.afterSignOut = document.getElementById("snav-critterpass-badge").style.display;

    // ── Layout, measured at THIS iframe's real width ─────────────────
    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.locked));
    await window.__ccCritterPassSync();
    const vw = window.innerWidth;
    const overflow = [];
    document.querySelectorAll("#cc-critter-pass-root *").forEach(el => {
      // Anything inside a scroller is allowed to be wider than the window,
      // that is what the scroller is for. Everything else is a real overflow.
      // Only auto/scroll counts. "hidden" was in this list and made the whole
      // check vacuous: .ccCP itself is overflow:hidden, so EVERY descendant was
      // excused and the overflow array was always empty. Content clipped by a
      // hidden parent is a bug too, so it belongs in the count.
      let p = el.parentElement, inScroller = false;
      while (p && p !== document.body) {
        const ov = getComputedStyle(p).overflowX;
        if (ov === "auto" || ov === "scroll") { inScroller = true; break; }
        p = p.parentElement;
      }
      if (inScroller) return;
      const r = el.getBoundingClientRect();
      if (r.right > vw + 1) {
        overflow.push(((el.className || "") + "").split(" ")[0] + "@" + Math.round(r.right));
      }
    });
    // ── THE BADGE HAS TO BE READABLE ─────────────────────────────────
    // "PASS LEVEL" is the smallest type on the page and it sits on the
    // lightest part of a green badge, which is how it shipped as white on
    // #37c48c: 2.2:1, a smudge at every width. Measured rather than eyeballed,
    // against the badge's own painted gradient, because the next person to
    // brighten that green will not think of this.
    out.lvlBadge = (() => {
      const word = document.querySelector(".ccCP-lvl-word");
      const badge = document.querySelector(".ccCP-lvl-badge");
      if (!word || !badge) return null;
      const rgb = (c) => (String(c).match(/[\\\\d.]+/g) || []).slice(0, 3).map(Number);
      const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
      const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
      const ratio = (a, b) => { const la = lum(a), lb = lum(b), hi = Math.max(la, lb), lo = Math.min(la, lb);
                                return (hi + 0.05) / (lo + 0.05); };
      const cs = getComputedStyle(word);
      // Both ends of the gradient: the word sits at the TOP, which is the end
      // that was failing, so the worst of the two is the one that counts.
      const stops = (getComputedStyle(badge).backgroundImage.match(/rgba?\\\\([^)]+\\\\)/g) || []);
      const ink = rgb(cs.color);
      const rs = stops.map(st => ratio(ink, rgb(st)));
      return {
        stops: stops.length,
        opacity: Number(cs.opacity),
        worst: rs.length ? Math.min(...rs) : 0,
        shadow: cs.textShadow !== "none",
      };
    })();

    out.ink = auditInk();

    const page = document.querySelector(".ccCP");
    const buy = document.getElementById("ccCP-buy");
    out.layout = {
      vw,
      docScrollsSideways: document.documentElement.scrollWidth > vw + 1,
      overflow: overflow.slice(0, 6),
      pageW: page ? Math.round(page.getBoundingClientRect().width) : 0,
      // The kelp art really is painted, at every width.
      hasKelp: page ? /kelp-forest/.test(getComputedStyle(page).backgroundImage) : false,
      buyW: buy ? Math.round(buy.getBoundingClientRect().width) : 0,
      buyH: buy ? Math.round(buy.getBoundingClientRect().height) : 0,
      // The header has to stay inside the page, not spill out of the art.
      headW: (() => { const h = document.querySelector(".ccCP-head");
                      return h ? Math.round(h.getBoundingClientRect().width) : 0; })(),
    };
  } catch (e) {
    out.errors.push("THREW: " + (e && e.message ? e.message : String(e)));
  }
  window.__RESULT = out;
})();
\`;

(async () => {
  const results = {};
  for (const w of window.__WIDTHS) {
    const ifr = document.createElement("iframe");
    ifr.width = w;
    document.body.appendChild(ifr);
    const doc = ifr.contentDocument;
    doc.open(); doc.write(innerHtml()); doc.close();
    for (let i = 0; i < 500 && !ifr.contentWindow.__RESULT; i++) {
      await new Promise(r => setTimeout(r, 25));
    }
    results[w] = ifr.contentWindow.__RESULT || { errors: ["never reported in"] };
  }
  document.getElementById("RESULT").textContent = "@@" + JSON.stringify(results) + "@@";
})();
</script>
</body></html>`;

const file = path.join(os.tmpdir(), `cc_critterpass_ui_${Date.now()}.html`);
fs.writeFileSync(file, page);

const dom = execFileSync(CHROME, [
  "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
  "--window-size=1600,1200", "--virtual-time-budget=60000",
  "--dump-dom", "file://" + file,
], { encoding: "utf8", maxBuffer: 128 * 1024 * 1024, stdio: ["pipe", "pipe", "ignore"] });

const m = dom.match(/@@([\s\S]*?)@@/);
if (!m) { console.error("no result payload in the DOM dump"); process.exit(1); }
const R = JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                         .replace(/&lt;/g, "<").replace(/&gt;/g, ">"));

// ══════════════════════════════════════════════════════════════════════════
const D = R[1280];

// ══════════════════════════════════════════════════════════════════════════
//  EVERY WORD ON THE PAGE, AT EVERY WIDTH
//  The measured half of the "no tan" rules above. Those read the stylesheet;
//  this reads the pixels, with every Player Home stylesheet loaded, so a
//  colour that arrives from another file, from inheritance, or from a token
//  somebody else redefined still fails here.
// ══════════════════════════════════════════════════════════════════════════
console.log("\nlegibility: every rendered word, measured against its background");
{
  // 4.5:1 is the AA floor for body text, 3:1 for large text. Text written
  // straight onto the kelp carries a shadow that the maths cannot see, so it
  // is held to the large-text floor and no lower.
  const floor = (row) => (row.large || row.shadow ? 3 : 4.5);
  let worst = null, rowsSeen = 0;
  const failures = [];
  for (const w of WIDTHS) {
    for (const row of (R[w].ink || []).concat(R[w].inkOwner || [])) {
      rowsSeen++;
      if (!worst || row.r < worst.r) worst = { ...row, w };
      // Keyed on the STYLE, not the words: a hundred tier numbers painted the
      // same wrong colour are one problem, and a list that says so is the one
      // somebody can act on.
      if (row.r < floor(row)) failures.push(`.${row.cls} ${row.color} on ${row.bg} = ${row.r}:1 (${row.px}px${row.large ? " large" : ""})`);
    }
  }
  check("every word on the page was measured", rowsSeen > 400, rowsSeen);
  const uniq = [...new Set(failures)];
  check("not one of them fails contrast", uniq.length === 0,
        `${failures.length} across ${rowsSeen} words\n      ` + uniq.slice(0, 20).join("\n      "));
  check("…and the worst one on the whole page still has room",
        worst && worst.r >= 3, worst && `.${worst.cls} "${worst.words}" ${worst.r}:1`);
  // The specific thing that was wrong: the cards' ink is BLACK, and it is
  // black because a navy on cream is the shade that keeps getting called
  // washed out. Read off the rendered title, not the stylesheet.
  const title = (R[1280].ink || []).find(r => r.cls === "ccCP-buy-title");
  check("'Unlock the Critter Pass' is painted black", title && title.color === "rgb(0, 0, 0)",
        title && title.color);
  check("…on a card it reads at better than 15:1", title && title.r >= 15, title && title.r);
}

console.log("\ndesktop (1280px): the module runs");
check("the module rendered without throwing", D.errors.length === 0, D.errors.join(" | "));
check("the iframe really is 1280 wide", D.layout.vw === 1280, D.layout.vw);

console.log("\nlocked: the whole track, readable, with nothing claimable");
const serverTiers = P.locked.track.length;
check("every server tier rendered", D.locked.tiers === serverTiers,
      `rendered ${D.locked.tiers} of ${serverTiers}`);
check("NO claim buttons anywhere", D.locked.claimBtns === 0, D.locked.claimBtns);
check("no Claim-all button either", D.locked.claimAll === 0);
check("no Unlocked pill", D.locked.ownedPill === 0);
check("tiers already reached say what to do about it", D.locked.waiting > 0, D.locked.waiting);
check("tiers not yet reached are locked", D.locked.locked > 0, D.locked.locked);
check("waiting + locked accounts for the whole track",
      D.locked.waiting + D.locked.locked === serverTiers,
      `${D.locked.waiting}+${D.locked.locked} vs ${serverTiers}`);
// THE headline of the whole change: the badge is the PASS level, which is its
// own curve. A level-30 account that has not unlocked the pass is looking at
// Pass Level 1, and the track it is being sold really does start there.
check("the badge is labelled as the PASS level, not just 'Level'",
      /pass level/i.test(D.locked.levelWord), D.locked.levelWord);
check("a non-owner is at Pass Level 1 whatever their account level is",
      D.locked.level === "1" && P.locked.level === 30,
      `badge ${D.locked.level}, account ${P.locked.level}`);
check("the account level is still shown, so Pass Level 1 cannot read as a bug",
      /Account Level 30/.test(D.locked.chips), D.locked.chips);
check("the bar counts XP towards the next PASS level",
      new RegExp(`/ ${P.seasonXpPerLevel.toLocaleString()} XP to Pass Level 2$`).test(D.locked.barTxt),
      D.locked.barTxt);
check("the season name is shown", /Season 1/.test(D.locked.season), D.locked.season);
check("the countdown says how long is left in the season",
      new RegExp(`^${P.locked.seasonDaysLeft} days? left in the season$`).test(D.locked.seasonLeft),
      `${D.locked.seasonLeft} vs ${P.locked.seasonDaysLeft} from the server`);
check("that is a 30-day season", P.seasonDays === 30, P.seasonDays);
check("the countdown never exceeds the season length",
      P.locked.seasonDaysLeft <= P.seasonDays, P.locked.seasonDaysLeft);
check("locked tiers still say how much XP is to go",
      D.locked.togo.length === 2 && D.locked.togo.every(t => /XP to go$/.test(t)),
      JSON.stringify(D.locked.togo));
check("the page knows it is locked", /is-locked/.test(D.locked.rootClass), D.locked.rootClass);

console.log("\nlocked: the purchase card is the sales pitch");
check("the purchase card is shown", D.locked.buyCard === 1);
check("the button is live for somebody who can afford it", D.locked.buyEnabled);
check("the price on the button is the server's price",
      D.locked.buyText.includes(P.price.toLocaleString()),
      `${D.locked.buyText} vs ${P.price}`);
// The pitch is ONE sentence: what it costs and what it hands back. It used to
// open with a paragraph about the tier count, the XP a Pass Level costs and
// the 30-day climb, all three of which the highlight row underneath already
// says one line at a time. Whatever else moves onto this card, it does not go
// back into this paragraph.
check("the pitch names the coins the track pays back",
      D.locked.buySub.includes(P.coinTotal.toLocaleString()),
      `${D.locked.buySub} vs ${P.coinTotal}`);
check("…and the price being paid for them",
      D.locked.buySub.includes(P.price.toLocaleString()),
      `${D.locked.buySub} vs ${P.price}`);
check("…in one sentence, not a paragraph",
      D.locked.buySub.length < 90 && (D.locked.buySub.match(/\./g) || []).length === 1,
      `${D.locked.buySub.length} chars: ${D.locked.buySub}`);
check("…and the prose it replaced is gone",
      !/One payment|climb of its own|not the years/.test(D.locked.buySub), D.locked.buySub);
// The claim the paragraph used to make is still TRUE and still visible: the
// track really does put one tier on every level, and the rail draws them all.
check("there really is a reward on every one of the levels",
      serverTiers === P.passMaxLevel
      && new Set(P.locked.track.map(t => t.level)).size === serverTiers,
      `${serverTiers} tiers over ${P.passMaxLevel} levels`);
check("8,500 really is what the track pays", P.coinTotal === 8500, P.coinTotal);
check("4,000 really is what it costs", P.price === 4000, P.price);
const hl = D.locked.highlights.join(" | ");
check("the highlights name the coin total", hl.includes(P.coinTotal.toLocaleString()), hl);
check("…the XP total", hl.includes(P.xpTotal.toLocaleString()), hl);
check("…the extra daily challenges", /\+3\s*daily challenges/i.test(hl), hl);
check("…the extra weekly challenges", /\+3\s*weekly challenges/i.test(hl), hl);
check("…the emotes", /emote/i.test(hl), hl);
check("…and the Level 100 critter", /Level 100/.test(hl), hl);
// The 30-day climb is the pitch now, so it has to be ON the card and it has to
// be the SERVER's numbers. A hard-coded "30 days" in the markup would keep
// promising thirty after a retune moved the curve.
check("…the 30-day climb leads the highlights",
      new RegExp(`~${P.seasonDays} days`).test(hl), hl);
check("…and it quotes the server's own daily rate",
      hl.includes(P.seasonXpPerDay.toLocaleString()), hl);
// What a Pass Level costs left the paragraph, so it has to still be somewhere
// a player can read it: the progress bar says it to everybody, and the foot
// note says it again to an owner.
check("the page still says what a Pass Level costs",
      D.locked.barTxt.includes(P.seasonXpPerLevel.toLocaleString() + " XP"),
      D.locked.barTxt);
check("…and says it again in the owner's foot note",
      D.owner.foot.includes(P.seasonXpPerLevel.toLocaleString() + " XP"), D.owner.foot);
check("how long the whole track takes is on the card as a highlight",
      new RegExp(`~${P.seasonDays} days`).test(hl), hl);
check("the pitch no longer promises a head start it cannot give",
      !/already earned counts|already reached|already passed/i.test(D.locked.buySub),
      D.locked.buySub);
check("the pass curve really is far cheaper than the account curve",
      P.seasonXpPerLevel * (P.passMaxLevel - 1) < P.locked.levelTotals[99] / 3,
      `${P.seasonXpPerLevel * (P.passMaxLevel - 1)} vs ${P.locked.levelTotals[99]}`);
check("the finale critter's art is on the card", D.locked.finaleArt);
check("the badge is hidden while the pass is locked", D.badge.whenLocked === "none");

console.log("\nlocked and short of coins");
check("the button is disabled", D.broke.buyDisabled);
check("it says how many coins short", /short/i.test(D.broke.note), D.broke.note);
check("still no claim buttons", D.broke.claimBtns === 0);

console.log("\nholding a Season Pass voucher");
check("the served state carries the voucher count", D.voucher.inventory === 2, D.voucher.inventory);
check("the button offers to REDEEM, not to buy",
      /redeem/i.test(D.voucher.btn) && /voucher/i.test(D.voucher.btn), D.voucher.btn);
check("with no coins at all, the button is still live", D.voucher.btnDisabled === false);
check("nothing on the card says the player is short of coins",
      !/short/i.test(D.voucher.note), D.voucher.note);
check("the card does not quote the coin price to a voucher holder",
      !/4,000/.test(D.voucher.sub), D.voucher.sub);
check("it says how many vouchers are in hand", /\b2\b/.test(D.voucher.note), D.voucher.note);
check("a chip counts them in the header",
      /Season Pass Voucher/i.test(D.voucher.chip), D.voucher.chip);
check("it asks before spending one", /redeem/i.test(D.voucher.modal), D.voucher.modal);
check("the question is about a voucher, not about coins",
      !/4,000/.test(D.voucher.modal), D.voucher.modal);
check("confirming posts exactly one buy", D.voucher.posted === 1, D.voucher.posted);
check("and it posts voucher:true, so the wallet is never charged",
      D.voucher.postedVoucherFlag === true);

console.log("\nbuying it");
check("it asks before it spends 4,000 coins", D.buy.askedFirst >= 1, D.buy.askedFirst);
check("the question names the price", D.buy.modalMentionsPrice === true);
check("cancelling posts nothing", D.buy.cancelledPosts === 0, D.buy.cancelledPosts);
check("confirming posts exactly one buy", D.buy.confirmedPosts === 1, D.buy.confirmedPosts);
check("the purchase card goes away once it is bought", D.buy.buyCardGone === true);
check("__ccCritterPassOwned() reports the truth after a buy", D.buy.owned === true);

console.log("\nowned, mid-track");
check("the purchase card is gone", D.owner.buyCard === 0);
check("the Unlocked pill is shown", D.owner.ownedPill === "Unlocked", D.owner.ownedPill);
check("nothing says 'unlock to claim' any more", D.owner.waiting === 0, D.owner.waiting);
check("reached-and-unclaimed tiers have a Claim button",
      D.owner.claimBtns > 0 && D.owner.claimBtns === D.owner.ready,
      `${D.owner.claimBtns} buttons vs ${D.owner.ready} ready`);
check("no Claim button on a locked tier", D.owner.claimOnLocked === 0);
check("no Claim button on a claimed tier", D.owner.claimOnClaimed === 0);
check("already-claimed tiers show as claimed",
      D.owner.claimed === P.owner.claimed.length,
      `${D.owner.claimed} vs ${P.owner.claimed.length}`);
check("Claim-all offers the right count",
      D.owner.claimAll === `Claim ${D.owner.ready} reward${D.owner.ready === 1 ? "" : "s"}`,
      D.owner.claimAll);
// The owner fixture is PASS level 30 on an ACCOUNT level 12: crossed over, so
// a renderer that read the wrong one would be caught either way round.
check("an owner's badge is their PASS level, not their account level",
      D.owner.level === String(P.owner.passLevel) && P.owner.passLevel === 30
        && P.owner.level === 12,
      `badge ${D.owner.level}, pass ${P.owner.passLevel}, account ${P.owner.level}`);
check("an owner sees their account level on the chip too",
      D.owner.chips.join(" | ").includes("Account Level 12"), D.owner.chips.join(" | "));
check("the bar counts XP towards the next PASS level",
      /XP to Pass Level 31$/.test(D.owner.barTxt), D.owner.barTxt);
check("the next-up line names a Pass Level",
      /Pass Level 31/.test(D.owner.next), D.owner.next);
// A locked tier's "N XP to go" has to be measured on the pass curve. On the
// account curve the same card would quote thousands, for a level that really
// costs seasonXpPerLevel.
check("a locked tier's XP-to-go is on the PASS curve",
      D.owner.togo.length > 0 && D.owner.togo.every(t => {
        const n = Number((t.match(/([\d,]+)/) || [])[1].replace(/,/g, ""));
        return n > 0 && n <= P.seasonXpPerLevel * (P.passMaxLevel - 30);
      }), D.owner.togo.join(" | "));
check("the foot note explains the pass climbs on its own curve",
      /own climb/i.test(D.owner.foot) && D.owner.foot.includes(P.seasonXpPerLevel.toLocaleString()),
      D.owner.foot);
check("the six perk tiers read as perks", D.owner.perks === 6, D.owner.perks);
check("the level-100 critter reads as the finale", D.owner.finale === 1, D.owner.finale);
check("the chips report the extra challenge slots",
      D.owner.chips.join(" | ").includes("Daily Challenge")
      && D.owner.chips.join(" | ").includes("Weekly Challenge"),
      D.owner.chips.join(" | "));
check(`the rail scrolls rather than squashing ${serverTiers} tiers`, D.owner.railScrolls === true);
check("there is a reward on every one of the 100 levels",
      serverTiers === 100
      && JSON.stringify(P.locked.track.map(t => t.level)) === JSON.stringify(
           Array.from({ length: 100 }, (_, i) => i + 1)),
      serverTiers);
check("every tier card is the same height, so the track line lines up",
      D.owner.heights.length === 1, JSON.stringify(D.owner.heights));

console.log("\nclaim all, paid in bounded batches");
check("it keeps asking until the server stops saying there is more",
      D.claimAll.calls === 3, D.claimAll.calls);
check("it reports the running total ONCE, not once per batch",
      D.claimAll.toasts.filter(t => /^Claimed /.test(t)).length === 1,
      JSON.stringify(D.claimAll.toasts));
check("the total is every batch added up (25 + 25 + 5)",
      /^Claimed 55 rewards/.test(D.claimAll.toasts.find(t => /^Claimed /.test(t)) || ""),
      JSON.stringify(D.claimAll.toasts));
check("a refusal that a LATER batch paid is not warned about",
      !D.claimAll.toasts.some(t => /emote for every critter/.test(t)),
      JSON.stringify(D.claimAll.toasts));
check("a refusal that really stood IS warned about",
      D.claimAll.toasts.some(t => /every background/.test(t)),
      JSON.stringify(D.claimAll.toasts));
check("exactly one warning, not one per refusing tier",
      D.claimAll.kinds.filter(k => k === "warn").length === 1,
      JSON.stringify(D.claimAll.kinds));

console.log("\nthe sidebar badge");
check("it shows the claimable count when the pass is owned",
      D.badge.whenOwned === String(D.owner.ready), `${D.badge.whenOwned} vs ${D.owner.ready}`);
check("it comes DOWN when a reward is claimed",
      Number(D.badge.afterOneClaim) === D.owner.ready - 1,
      `${D.badge.afterOneClaim} vs ${D.owner.ready - 1}`);
check("it is gone, not zero, on a fully-claimed pass", D.maxed.badge === "none");
check("it is gone on sign-out", D.badge.afterSignOut === "none");

console.log("\nmaxed out");
check("nothing left to claim", D.maxed.claimBtns === 0);
check("every tier reads as claimed", D.maxed.claimed === serverTiers,
      `${D.maxed.claimed} of ${serverTiers}`);
check("it says so instead of offering a dead button",
      /Nothing to claim/i.test(D.maxed.allclear), D.maxed.allclear);
check("the headline stops asking for XP that does not exist",
      /yours/i.test(D.maxed.next), D.maxed.next);

console.log("\n__ccPassExtraSlots(), the seam the challenge strip reads");
check("zero before anything has loaded",
      D.slots.beforeLoad.daily === 0 && D.slots.beforeLoad.weekly === 0,
      JSON.stringify(D.slots.beforeLoad));
check("zero for a player who has not bought the pass",
      D.slots.whenLocked.daily === 0 && D.slots.whenLocked.weekly === 0,
      JSON.stringify(D.slots.whenLocked));
check("the claimed slots for an owner who has them",
      D.slots.whenOwned.daily === P.owner.inventory.extraDaily
      && D.slots.whenOwned.weekly === P.owner.inventory.extraWeekly,
      JSON.stringify(D.slots.whenOwned));
check("all three of each on a maxed pass",
      D.slots.whenMaxed.daily === P.maxExtraDaily && D.slots.whenMaxed.weekly === P.maxExtraDaily,
      JSON.stringify(D.slots.whenMaxed));
check("a tampered count is clamped to the server's maximum",
      D.slots.clamped.daily === P.maxExtraDaily, JSON.stringify(D.slots.clamped));
check("back to zero on sign-out, so the next account inherits nothing",
      D.slots.afterSignOut.daily === 0 && D.slots.afterSignOut.weekly === 0,
      JSON.stringify(D.slots.afterSignOut));

// ══════════════════════════════════════════════════════════════════════════
console.log("\nthe page, at every screen width");
const MIN_TIER_W = 140;   // below this a tier's name and blurb stop being readable
const MIN_TAP = 40;       // a button below this is not a tap target
const MIN_INK = 4.5;      // WCAG AA for small text, and this word is the smallest
for (const w of WIDTHS) {
  const r = R[w] || { errors: ["missing"] };
  const label = `${String(w).padStart(4)}px`;
  check(`${label}: rendered without throwing`, (r.errors || []).length === 0,
        (r.errors || []).join(" | "));
  if (!r.layout) continue;
  check(`${label}: the iframe really is that wide`, r.layout.vw === w, r.layout.vw);
  check(`${label}: the page does not scroll sideways`,
        r.layout.docScrollsSideways === false);
  check(`${label}: nothing spills out of the page`,
        (r.layout.overflow || []).length === 0, (r.layout.overflow || []).join(", "));
  check(`${label}: the kelp forest is painted`, r.layout.hasKelp === true);
  check(`${label}: the header fits inside the page`,
        r.layout.headW > 0 && r.layout.headW <= r.layout.pageW,
        `${r.layout.headW} in ${r.layout.pageW}`);
  check(`${label}: the Unlock button is a real tap target`,
        r.layout.buyH >= MIN_TAP, r.layout.buyH + "px tall");
  check(`${label}: tiers stay wide enough to read`,
        (r.owner || {}).minTierW >= MIN_TIER_W, (r.owner || {}).minTierW + "px");
  // The word on the badge, at every width. It is 9.6px at the widest and
  // 8.3px on a phone: small type, so it is held to the full 4.5:1 rather than
  // the large-text 3:1, on the DARKEST it ever gets against its own gradient.
  const b = r.lvlBadge;
  check(`${label}: "PASS LEVEL" is readable on the badge`,
        !!b && Number(b.worst) >= MIN_INK, JSON.stringify(b));
  check(`${label}: …and is not faded by an opacity on top of that`,
        !!b && b.opacity === 1, b ? b.opacity : "no badge");
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
