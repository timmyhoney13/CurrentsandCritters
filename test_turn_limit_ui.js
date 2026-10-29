#!/usr/bin/env node
/* The turn clock, on screen: the control that sets it and the critter that runs it.
 *
 * Run:  node test_turn_limit_ui.js        (needs Google Chrome for the second half)
 *
 * The rule the host is given:
 *   • a Normal or Team game can turn the clock OFF, or pick any rung;
 *   • the rungs are 5-second steps with a 2-second one underneath them;
 *   • both competitive modes ALWAYS run a clock, never slower than 10 seconds,
 *     and may go below five.
 * multiplayer_server.py is what ENFORCES that (test_turn_limit.py), so the
 * first half of this file reads both files and fails if the modal is offering
 * numbers the server would not accept, which is the one way these two can
 * drift apart without anything looking broken.
 *
 * The second half runs the real clock, in headless Chrome, against the real
 * css/preview.css and the real markup out of preview.html:
 *   • the critter swims the bar left to right across the turn and the water he
 *     has crossed fills in behind him;
 *   • he is SMALL (the brief was "do not make him too big"), and he never
 *     swims off either end of his own bar;
 *   • the strip sits between the action bar and the hand, and appears and
 *     disappears with the clock rather than holding a gap open all game.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
const APP  = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");
const CSS  = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const PRESTIGE = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/prestige-ui.js"), "utf8");
const SRV  = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");

let failures = 0, checks = 0;
function check(cond, label) {
  checks++;
  if (cond) { console.log("  PASS " + label); }
  else { failures++; console.log("  FAIL " + label); }
}
function eq(got, want, label) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  check(g === w, label + (g === w ? "" : `\n        got  ${g}\n        want ${w}`));
}

// ── Lift real code out of preview-app.js ────────────────────────────────────
function sliceFn(src, name) {
  const start = src.search(new RegExp("\\bfunction\\s+" + name + "\\s*\\("));
  if (start < 0) return null;
  let i = src.indexOf("{", start), depth = 0;
  while (i < src.length) {
    const c = src[i];
    if (c === '"' || c === "'" || c === "`") {
      const q = c; i++;
      while (i < src.length && src[i] !== q) { if (src[i] === "\\") i++; i++; }
      i++; continue;
    }
    if (c === "/" && src[i + 1] === "/") { while (i < src.length && src[i] !== "\n") i++; continue; }
    if (c === "/" && src[i + 1] === "*") { i = src.indexOf("*/", i); if (i < 0) return null; i += 2; continue; }
    if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) return src.slice(start, i + 1); }
    i++;
  }
  return null;
}
function sliceConst(src, name) {
  const m = src.match(new RegExp("^\\s*const\\s+" + name + "\\s*=.*$", "m"));
  return m ? m[0].trim() : null;
}
// A const whose value is an object literal spread over many lines.
function sliceConstObj(src, name) {
  const start = src.search(new RegExp("^\\s*const\\s+" + name + "\\s*=\\s*\\{", "m"));
  if (start < 0) return null;
  let i = src.indexOf("{", start), depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") { depth--; if (depth === 0) return src.slice(start, j + 2).trim(); }
  }
  return null;
}

// ═══════════════════════════════════════════════════════════════════════════
//  1. THE CLIENT OFFERS WHAT THE SERVER ACCEPTS
// ═══════════════════════════════════════════════════════════════════════════
console.log("\nThe ladder the modal offers is the ladder the server keeps");

const srvChoices = (() => {
  const m = SRV.match(/^TURN_LIMIT_CHOICES[^=]*=\s*\(([^)]*)\)/m);
  if (!m) throw new Error("TURN_LIMIT_CHOICES not found in multiplayer_server.py");
  return m[1].split(",").map(s => s.trim()).filter(Boolean).map(Number);
})();
const srvNum = (name) => {
  const m = SRV.match(new RegExp("^" + name + "\\s*=\\s*(\\d+)", "m"));
  if (!m) throw new Error(name + " not found in multiplayer_server.py");
  return Number(m[1]);
};
const appChoices = JSON.parse(
  (sliceConst(APP, "TURN_LIMIT_CHOICES") || "").replace(/^const[^=]*=\s*/, "").replace(/;$/, ""));
const appNum = (name) => Number((sliceConst(APP, name) || "").replace(/[^0-9]/g, ""));

eq(appChoices, srvChoices, "the client's rungs are the server's rungs");
check(srvChoices[0] === 2, "the bottom rung is 2 seconds");
check(srvChoices.slice(1).every(c => c % 5 === 0), "every rung above it is a 5-second step");
check(appNum("TURN_LIMIT_COMP_MAX") === srvNum("TURN_LIMIT_COMP_MAX")
      && srvNum("TURN_LIMIT_COMP_MAX") === 10,
      "a competitive turn can never be set above 10 seconds");
check(appNum("TURN_LIMIT_COMP_DEFAULT") === srvNum("TURN_LIMIT_COMP_DEFAULT")
      && srvNum("TURN_LIMIT_COMP_DEFAULT") === 10,
      "and it opens at 10");
check(/turn_limit_seconds:\s*turnLimitSeconds/.test(APP),
      "the create request carries the host's choice");
check(/_ncFillTurnLimit\(_ncIsCompetitive \|\| _ncIsRanked\)/.test(APP),
      "both competitive modes rebuild the list, not just the 1v1 one");
// The tour opens this very modal, so whatever the host last picked for a real
// game is still sitting in the control when the walkthrough starts. Both ends
// drop it, because either one alone leaves a guided tutorial on a clock.
check(/if \(_isTutGame\) turnLimitSeconds = 0;/.test(APP),
      "the tutorial sends no clock");
check(/turn_limit_seconds = 0 if tutorial else/.test(SRV),
      "and the server refuses to put a tutorial room on one");

// ── The two turns the player did not plan for ──────────────────────────────
// One clock per TURN, and the game hands out two turns nobody chose: a ★
// "play again" (another turn at the same seat, which the ENGINE spends inside
// the same engine turn, so the turn-boundary arm never fires for it) and
// picking a card up to play it (half a play, where the other half is the one
// a clock must never make for you). test_turn_limit.py measures both on a
// live room; these two are the wiring that has to agree across the wire.
console.log("\nThe two turns that get a clock of their own");

check(/elif is_replay_turn:\s*\n\s*#/.test(SRV) &&
      /elif is_replay_turn:[\s\S]{0,900}?_arm_turn_clock_locked\(seat_index\)/.test(SRV),
      "a ★ play again arms the clock afresh, where the turn boundary cannot");
check(/def reset_turn_clock_for_play/.test(SRV),
      "the server has a way to give one fresh budget to a staged play");
check(/self\._turn_clock_play_grace_used = False[\s\S]{0,400}?def _turn_clock_deadline_for/.test(
        SRV.slice(SRV.indexOf("def _arm_turn_clock_locked"))),
      "…and arming the clock hands that one budget back, so it is once a turn");
check(/parts\[3\] == "turn_clock_play"/.test(SRV),
      "it is reachable over the wire");
check(/\/turn_clock_play`/.test(APP),
      "and the browser asks for it at the same address");
check(/_turnClockAskForPlay\(\)/.test(APP) &&
      /function _turnClockAskForPlay/.test(APP),
      "the ask is made when a card is picked up to be paid for");
check(/Number\(_turnClockState\(\)\.roomLimit\) > 0/.test(APP),
      "a table with no clock spends no request on it");

// ── Where the bar lives, and how big its passenger is ──────────────────────
console.log("\nWhere the clock sits, and how big the critter is");

check(HTML.indexOf('id="pv-turn-timer"') > HTML.indexOf('id="pv-action-bar"'),
      "the clock is below the action bar");
check(HTML.indexOf('id="pv-turn-timer"') < HTML.indexOf('id="pv-hand-zone"'),
      "and above the cards, which is where it was asked for");

const CSS_NC = CSS.replace(/\/\*[\s\S]*?\*\//g, "");   // comments quote sizes too
const critterRule = CSS_NC.match(/\.ptt-critter\s*\{([^}]*)\}/);
check(!!critterRule, "the critter has a rule of its own");
const critterH = critterRule ? Number((critterRule[1].match(/height:\s*(\d+)px/) || [])[1]) : NaN;
check(critterH > 0 && critterH <= 24,
      `he is a little critter, not a big one (${critterH}px tall)`);
check(/#pv-turn-timer\s*\{[^}]*display:\s*none/.test(CSS_NC),
      "the strip takes no height at all in a game with no clock");

// ── The families, named on the strategy tiles ──────────────────────────────
console.log("\nEvery strategy says what family it is");

check(/_stratFamiliesHtml\(i, s\)/.test(APP),
      "a strategy tile is given its families");
check((APP.match(/_stratFamiliesHtml\(i, s\)/g) || []).length >= 3,
      "the core tile, the combo tile and the open plan all get them");
check(/_famChipHtml\(c\.species, "hd-card-fam"\)/.test(APP),
      "and every card inside a plan says its own family");
check(/CC_FAMILY_LABEL/.test(APP),
      "the names come off the one table gamedata.js publishes");

// ═══════════════════════════════════════════════════════════════════════════
//  2. THE CLOCK, RUNNING (a real browser, the real CSS, the real markup)
// ═══════════════════════════════════════════════════════════════════════════
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

console.log("\nThe clock on screen");

if (!CHROME) {
  console.log("  (skipped: no Chrome/Chromium found)");
} else {
  // The real markup for both pieces, cut out of preview.html rather than
  // retyped, so this test cannot pass against a page that no longer exists.
  const grabBlock = (startNeedle, endNeedle) => {
    const a = HTML.indexOf(startNeedle);
    const b = HTML.indexOf(endNeedle, a);
    if (a < 0 || b < 0) throw new Error("could not find " + startNeedle + " in preview.html");
    return HTML.slice(a, b + endNeedle.length);
  };
  const MODAL  = grabBlock('<div class="nc-fields-row">', '<div id="nc-turn-limit-note"></div>');
  // The strip is taken by its own two ends rather than by a closing tag,
  // because three of its four tags close with the same four characters.
  const tStart = HTML.indexOf('<div id="pv-turn-timer"');
  const tEnd   = HTML.indexOf('<span class="ptt-secs"></span>', tStart);
  const TIMER_HTML = HTML.slice(tStart, tEnd) + '<span class="ptt-secs"></span>\n  </div>';

  const SLICES = [
    "turnLimitLabel", "_ncFillTurnLimit", "_ncTurnLimitNote",
    "_turnClockState", "_turnClockSlug", "_turnClockShow", "_turnClockApply", "_turnClockFrame",
    "_turnClockAskForPlay",
  ].map(n => {
    const c = sliceFn(APP, n);
    if (!c) { console.error("FAIL: could not find function " + n + "() in preview-app.js"); process.exit(1); }
    return c;
  });
  const CONSTS = ["TURN_LIMIT_CHOICES", "TURN_LIMIT_COMP_MAX", "TURN_LIMIT_COMP_DEFAULT"]
    .map(n => sliceConst(APP, n));
  // The facing rule is prestige-ui.js's, and the clock reads it off window, so
  // the real table is what decides whether this critter is mirrored.
  const FACING = [sliceConstObj(PRESTIGE, "FACING"), sliceFn(PRESTIGE, "facingClass")].join("\n");
  if (!sliceConstObj(PRESTIGE, "FACING")) {
    console.error("FAIL: could not find the FACING table in prestige-ui.js");
    process.exit(1);
  }

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-turnclock-"));
  fs.writeFileSync(path.join(tmp, "preview.css"), CSS);
  // The real art, so "do not make him too big" is measured against the real
  // shape of a critter and not against a broken image with no width.
  fs.mkdirSync(path.join(tmp, "avatars"), { recursive: true });
  for (const slug of ["spinner-dolphin", "mullet"]) {
    fs.copyFileSync(path.join(ROOT, "multiplayer/client/avatars", slug + ".png"),
                    path.join(tmp, "avatars", slug + ".png"));
  }
  const PAGE = `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="preview.css">
<style>html,body{margin:0;background:#07203f}
  /* the column the strip really lives in, so its width is a real width */
  #pv-game{display:flex;flex-direction:column;width:900px}
  #pv-game > *{position:relative;z-index:1}
  #pv-hand-zone{height:120px;background:#0a1e44}</style>
</head><body>
<div id="pv-game">
  <div id="pv-action-bar" style="height:46px;background:#123"></div>
  ${TIMER_HTML}
  <div id="pv-hand-zone"></div>
</div>
<div id="modal">${MODAL}</div>
<div id="out">RUNNING</div>
<script>
${CONSTS.join("\n")}
${FACING}
window.__ccCritterFacing = facingClass;
// Stand-ins for the handful of module-level helpers the sliced functions call.
var _ncIsCompetitive = false, _ncIsRanked = false;
var FIT_CALLS = 0;
function fitGameColumn() { FIT_CALLS++; }
function updateBottomDockOffset() {}
function _avSrc(u) { return String(u).split("/avatars/").join("avatars/"); }
var FAKE_NOW = 0;
function serverNowMs() { return FAKE_NOW; }
// Picking a card up to play it asks the server for a fresh budget. Stand in
// for the three module-level things that ask does it with, and keep the calls
// so the page can say whether one was really made.
var ASKS = [];
var roomId = "ABCDE";
function getSeatToken() { return "seat-token-1"; }
function apiPost(path, body) {
  ASKS.push({ path: path, body: body });
  return Promise.resolve({ ok: true, data: { deadline_ms: FAKE_NOW + 60000 } });
}
// The frames are driven by hand so the clock can be read at exact moments.
var _rafId = 0;
window.requestAnimationFrame = function () { return ++_rafId; };
window.cancelAnimationFrame = function () {};
${SLICES.join("\n")}

var R = { fail: [], info: {} };
function want(cond, msg) { if (!cond) R.fail.push(msg); }
function round(n) { return Math.round(n * 10) / 10; }

// ── the control ──────────────────────────────────────────────────────────
var sel = document.getElementById("nc-turn-limit");
_ncFillTurnLimit(false);
var casual = [].map.call(sel.options, function (o) { return o.value; });
R.info.casual = casual;
want(casual[0] === "0", "a Normal game cannot turn the clock off");
want(sel.value === "0", "a Normal game opens with a clock already running");
want(casual.slice(1).join(",") === TURN_LIMIT_CHOICES.join(","),
     "a Normal game is not offered every rung");

sel.value = "30";
_ncFillTurnLimit(true);
var comp = [].map.call(sel.options, function (o) { return o.value; });
R.info.comp = comp;
want(comp.indexOf("0") < 0, "a competitive game can be taken off the clock");
want(comp.join(",") === "2,5,10", "a competitive game is offered the wrong rungs: " + comp.join(","));
want(sel.value === "10", "30 seconds did not fall back to the 10-second ceiling");
want(/always on a clock/i.test(document.getElementById("nc-turn-limit-note").textContent),
     "the modal does not say why a competitive clock cannot be turned off");

sel.value = "2";
_ncFillTurnLimit(true);
want(sel.value === "2", "a competitive host's choice of 2 seconds was not kept");
_ncFillTurnLimit(false);
want(sel.value === "2", "switching back to Normal threw the chosen 2 seconds away");

// ── the clock ────────────────────────────────────────────────────────────
var bar = document.getElementById("pv-turn-timer");
var track = bar.querySelector(".ptt-track");
var critter = bar.querySelector(".ptt-critter");
var spent = bar.querySelector(".ptt-spent");

want(getComputedStyle(bar).display === "none", "the strip is showing before any clock exists");
var fitBefore = FIT_CALLS;

FAKE_NOW = 1000000;
var LIMIT = 10;
var SEATS = [{ index: 0, avatar: "/avatars/mullet.png" },
             { index: 1, avatar: "/avatars/spinner-dolphin.png" }];
var ROOM = { phase: "running", turn_limit_seconds: LIMIT };
_turnClockApply({
  room: ROOM, seats: SEATS,
  turn_timer: { seat: 1, limit: LIMIT, deadline_ms: FAKE_NOW + LIMIT * 1000 },
});
_turnClockFrame();
want(getComputedStyle(bar).display === "flex", "the strip did not appear when a clock started");
want(FIT_CALLS > fitBefore, "the column was not re-measured when the strip appeared");
want(/spinner-dolphin/.test(critter.getAttribute("src")),
     "the critter is not the one the player on the clock is wearing: " + critter.getAttribute("src"));
// spinner-dolphin is drawn facing LEFT, and he is swimming right, so he is
// mirrored. (prestige-ui.js's FACING table is the authority on that.)
want(critter.className.indexOf("faces-right") < 0 && critter.className.indexOf("faces-none") < 0,
     "a left-facing critter was not mirrored to swim forwards");
// A horizontal mirror is a matrix whose first component is negative.
want(getComputedStyle(critter).transform.indexOf("matrix(-") === 0,
     "the mirror never reached the pixels: " + getComputedStyle(critter).transform);

// Everything from here needs the critter's real width, so it waits for the
// art. Chrome's virtual time budget covers the load.
function measureClock() {
function at(msElapsed) {
  FAKE_NOW = 1000000 + msElapsed;
  _turnClockFrame();
  var t = track.getBoundingClientRect(), c = critter.getBoundingClientRect();
  return {
    pct: parseFloat(bar.style.getPropertyValue("--ptt-pct")),
    secs: bar.querySelector(".ptt-secs").textContent,
    cls: bar.className,
    cx: c.left + c.width / 2, cw: c.width, ch: c.height,
    spentW: spent.getBoundingClientRect().width,
    trackL: t.left, trackR: t.right, trackW: t.width,
  };
}

var a0 = at(0), a5 = at(5000), a10 = at(10000), aOver = at(14000);
R.info.pcts = [a0.pct, a5.pct, a10.pct, aOver.pct];
R.info.secs = [a0.secs, a5.secs, a10.secs];
R.info.critter = { w: round(a0.cw), h: round(a0.ch) };

want(a0.pct === 0, "the turn does not start with a full bar: " + a0.pct);
want(Math.abs(a5.pct - 50) < 1.5, "half the turn is not half the bar: " + a5.pct);
want(a10.pct === 100 && aOver.pct === 100, "the bar overruns its own end: " + aOver.pct);
want(a5.spentW > a0.spentW && a10.spentW > a5.spentW,
     "the water behind the critter does not fill in as the turn is spent");

want(a0.secs === "10s", "the readout does not start at the limit: " + a0.secs);
want(a5.secs === "5.0s", "under ten seconds the readout does not show tenths: " + a5.secs);
want(a10.secs === "0.0s" && aOver.secs === "0.0s",
     "the readout runs past zero: " + aOver.secs);

want(a0.cls.indexOf("low") < 0 && a0.cls.indexOf("warn") < 0,
     "a fresh turn is already warning");
want(at(6000).cls.indexOf("warn") >= 0, "past halfway the clock does not turn gold");
want(at(9000).cls.indexOf("low") >= 0, "in the last quarter the clock does not turn coral");

// He swims the WHOLE bar, and never off either end of it.
want(Math.abs(a0.cx - a0.trackL) < 1.5, "he does not start at the left edge");
want(Math.abs(a10.cx - a10.trackR) < 1.5, "he does not finish at the right edge");
want(a10.cx - a0.cx > a0.trackW * 0.9, "he barely moves across the turn");
// "Do not make him too big": he is a fraction of the strip he is swimming in.
want(a0.ch > 0 && a0.ch <= 24, "the critter is " + round(a0.ch) + "px tall");
want(a0.cw <= a0.trackW * 0.1, "the critter is wider than a tenth of his own bar");

// ── Picking a card up asks for a fresh budget ─────────────────────────────
// Dropping a card on a slot stages the play; paying for it is the rest of the
// move, and the clock must not make that one for you. The browser cannot hold
// its own countdown back (the deadline is the server's), so it asks. A table
// with no clock must not spend a request on it.
var LIVE = { room: ROOM, seats: SEATS,
             turn_timer: { seat: 1, limit: LIMIT, deadline_ms: FAKE_NOW + LIMIT * 1000 } };
_turnClockApply(LIVE);
ASKS.length = 0;
_turnClockAskForPlay();
want(ASKS.length === 1, "picking a card up asked nobody for the time to play it");
// A plain compare, not a regex: this file is a template literal, so an
// escaped slash inside one would reach the page as a bare slash and end the
// pattern early.
want(ASKS.length > 0 && ASKS[0].path === "/api/rooms/ABCDE/turn_clock_play",
     "it asked at the wrong address: " + (ASKS[0] || {}).path);
want(ASKS.length > 0 && ASKS[0].body && ASKS[0].body.seat_token === "seat-token-1",
     "it asked without saying which seat was asking");

_turnClockApply({ room: { phase: "running", turn_limit_seconds: 0 }, seats: SEATS, turn_timer: null });
ASKS.length = 0;
_turnClockAskForPlay();
want(ASKS.length === 0, "a table with no clock still spent a request on the turn clock");
_turnClockApply(LIVE);          // back on the clock for the checks below

// Between two turns, and all through a bot's turn, nobody is on the clock.
// The strip must HOLD ITS PLACE: a 27px row that comes and goes at every
// handoff shunts the board and the hand up and down all game.
var fitIdleBefore = FIT_CALLS;
_turnClockApply({ room: ROOM, seats: SEATS, turn_timer: null });
want(getComputedStyle(bar).display === "flex",
     "the strip vanished between two turns instead of going idle");
want(bar.className.indexOf("idle") >= 0, "an idle clock is not marked idle");
want(FIT_CALLS === fitIdleBefore, "going idle re-measured the whole column");
want(bar.querySelector(".ptt-secs").textContent === LIMIT + "s",
     "an idle clock does not say what the limit is: " + bar.querySelector(".ptt-secs").textContent);
want(parseFloat(bar.style.getPropertyValue("--ptt-pct")) === 0,
     "an idle clock left the critter where the last turn ended");
want(bar.className.indexOf("low") < 0 && bar.className.indexOf("warn") < 0,
     "an idle clock is still wearing the last turn's warning");

// And it goes away with the game.
_turnClockApply({ room: { phase: "ended", turn_limit_seconds: LIMIT }, seats: SEATS, turn_timer: null });
want(getComputedStyle(bar).display === "none", "the strip stayed up after the game ended");
_turnClockApply({ room: { phase: "running", turn_limit_seconds: 0 }, seats: SEATS, turn_timer: null });
want(getComputedStyle(bar).display === "none", "a game with no clock still shows the strip");
_turnClockApply(null);
want(getComputedStyle(bar).display === "none", "a payload-less teardown left the strip up");

}

var done = false;
function finish() {
  if (done) return;
  done = true;
  try { measureClock(); } catch (e) { R.fail.push("measuring threw: " + (e && e.message || e)); }
  document.getElementById("out").textContent = JSON.stringify(R);
}
if (critter.complete && critter.naturalWidth) finish();
else {
  critter.addEventListener("load", finish);
  critter.addEventListener("error", function () {
    R.fail.push("the critter art did not load: " + critter.getAttribute("src"));
    finish();
  });
  setTimeout(finish, 4000);
}
<\/script></body></html>`;
  fs.writeFileSync(path.join(tmp, "page.html"), PAGE);

  let dom = "";
  try {
    dom = execFileSync(CHROME, [
      "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      // Its OWN profile: two Chromes sharing the default one starve each
      // other's frames and the second one measures nothing.
      "--user-data-dir=" + path.join(tmp, "chrome-profile"),
      "--window-size=1200,900", "--virtual-time-budget=15000", "--dump-dom",
      "file://" + path.join(tmp, "page.html"),
    ], { encoding: "utf8", maxBuffer: 1 << 28, stdio: ["ignore", "pipe", "ignore"] });
  } catch (e) {
    console.log("  FAIL could not run Chrome: " + (e && e.message));
    failures++;
  }
  const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
  if (!m || m[1].trim() === "RUNNING") {
    console.log("  FAIL the page produced no measurements");
    failures++;
  } else {
    const R = JSON.parse(m[1]
      .replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&"));
    for (const f of R.fail) { failures++; checks++; console.log("  FAIL " + f); }
    if (!R.fail.length) {
      checks++;
      console.log("  PASS the control offers " + R.info.casual.length + " settings casually and "
        + R.info.comp.join("/") + " in competitive");
      checks++;
      console.log("  PASS the critter (" + R.info.critter.w + "x" + R.info.critter.h
        + "px) swims the bar: " + R.info.pcts.join("% -> ") + "%, reading "
        + R.info.secs.join(" -> "));
    }
  }
  if (!process.env.CC_KEEP_TMP) { try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (_) {} }
  else console.log("  (page kept at " + tmp + ")");
}

console.log(`\n${checks - failures} passed, ${failures} failed`);
process.exit(failures ? 1 : 0);
