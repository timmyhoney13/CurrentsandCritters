#!/usr/bin/env node
/* The "play again" callout over the End Turn button.
 *
 * A Hermit Crab, a Sea Turtle or any ★ "play again" hands you a second turn.
 * The turn banner announces it — at the TOP of the screen — while the thing you
 * have to do to hand the turn on is at the BOTTOM, and on a phone those two are
 * a whole screen apart. Players sat on an extra turn waiting for the game to
 * move on by itself. The callout bobs directly over End Turn for exactly as
 * long as the extra turn lasts.
 *
 * Two halves:
 *   • SOURCE — it is driven by legal_actions.is_replay_turn, and every state
 *     that takes End Turn off the bar takes the callout with it. A callout
 *     pointing at a button that isn't there is worse than no callout.
 *   • RENDER — real markup, real CSS, headless Chrome: hidden by default, and
 *     when shown it sits ABOVE the End Turn button, horizontally over it, and
 *     inside the screen — at a laptop size and at a phone-held-sideways size.
 *
 * Run:  node test_play_again_callout.js       (render half needs Google Chrome)
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const APP  = read("js/preview-app.js");
const HTML = read("preview.html");
const CSS  = read("css/preview.css");

let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name); }
}

// ── Source ──────────────────────────────────────────────────────────────────
console.log("\nthe callout is wired to the extra turn, and to nothing else");

check("the markup exists", /id="pv-play-again-callout"/.test(HTML));
check("it hangs off the action bar, not off the button",
      /#pv-action-bar \{ position: relative; \}/.test(CSS) &&
      /#pv-play-again-callout \{[\s\S]{0,200}position: absolute;[\s\S]{0,120}bottom: calc\(100% \+ 7px\)/.test(CSS));
check("…and it is a direct child of the bar in the markup", (() => {
  const b = HTML.indexOf('<div id="pv-action-bar"');
  const c = HTML.indexOf('id="pv-play-again-callout"');
  const e = HTML.indexOf("<!-- Click-to-place card picker panel -->", b);
  return b > 0 && c > b && c < e;
})());
check("it says what to do, and names the button it points at",
      /tap <b>✓ End Turn<\/b>/.test(HTML));

check("one function owns showing and hiding it", /function setPlayAgainCallout\(on\)/.test(APP));
check("it does not restart its animation on every 1s poll",
      /if \(want === _playAgainCalloutOn\) return;/.test(APP));
check("the extra turn turns it on", /setPlayAgainCallout\(isReplayTurn && !_staleWindow\)/.test(APP));
check("the FINAL turn of the game still gets it if that turn is a replay",
      /setPlayAgainCallout\(Boolean\(lw\.is_replay_turn\) && !_staleWindow\)/.test(APP));

// Every branch that can leave End Turn unusable must clear it.
const offSites = (APP.match(/setPlayAgainCallout\(false\)/g) || []).length;
check(`every state that takes End Turn away clears it (${offSites} sites)`, offSites >= 7);
// Scoped to the function each branch really lives in — several of these
// conditions appear more than once in a 28k-line file.
const BAR_FN = (() => {
  const i = APP.indexOf("function renderActionBar(actions, isMyTurn");
  return i < 0 ? "" : APP.slice(i, APP.indexOf("\n  function ", i + 10));
})();
const LEAVE_FN = (() => {
  const i = APP.indexOf("function _leaveGameCleanup(");
  return i < 0 ? "" : APP.slice(i, APP.indexOf("\n  function ", i + 10));
})();
check("renderActionBar() was found", BAR_FN.length > 500);
check("_leaveGameCleanup() was found", LEAVE_FN.length > 500);
for (const [what, hay, near] of [
  ["ending the turn optimistically", APP,      'if (action.kind === "end_turn")'],
  ["payment mode",                   BAR_FN,   "if (pendingPayAction) {"],
  ["a forced discard",               BAR_FN,   "if (mustDiscard) {"],
  ["the Tarpon discard phase",       BAR_FN,   "if (tarponActive) {"],
  ["it stops being your turn",       APP,      "_lastActionMs = 0; // confirmed not our turn"],
]) {
  const i = hay.indexOf(near);
  check(`cleared when ${what}`, i >= 0 && hay.slice(Math.max(0, i - 300), i + 900).includes("setPlayAgainCallout(false)"));
}
check("cleared when leaving the game", LEAVE_FN.includes("setPlayAgainCallout(false)"));

check("tapping the words goes through the real button, so the confirm modal is identical",
      /pv-play-again-callout"\)\?\.addEventListener\("click"[\s\S]{0,260}endBtn\.click\(\)/.test(APP));
check("a disabled End Turn swallows the tap the same way",
      /if \(endBtn && !endBtn\.disabled\) endBtn\.click\(\)/.test(APP));

check("its position is measured, not declared (the bar wraps, so the column moves)",
      /function _positionPlayAgainCallout\(\)/.test(APP) &&
      /const btnMid = btnR\.left \+ btnR\.width \/ 2;/.test(APP));
check("the pointer is clamped to the bubble, so it can never detach from it",
      /arrow\.style\.left = Math\.round\(Math\.max\(12, Math\.min\(w - 12/.test(APP));
check("it is re-placed when the window changes shape",
      /addEventListener\("resize"[\s\S]{0,120}_positionPlayAgainCallout/.test(APP));

console.log("\nit is readable and it does not spin forever");
check("it bobs", /@keyframes pac-bob/.test(CSS));
check("reduced motion stops the bob", /prefers-reduced-motion[\s\S]{0,160}#pv-play-again-callout \{ animation: none/.test(CSS));
check("touch screens get it bigger (the game is scaled down ~0.67 there)",
      /@media \(pointer: coarse\)[\s\S]{0,140}#pv-play-again-callout \{ font-size: 14px/.test(CSS));

// ── Render ──────────────────────────────────────────────────────────────────
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found — skipping the render half.");
} else {
  // Headless Chrome hands the page 87px less than --window-size.
  const CHROME_CHROME_PX = 87;

  // The REAL edge-clamp, sliced out of preview-app.js — a reimplementation here
  // would only prove that the test can do arithmetic.
  const clampStart = APP.indexOf("  function _positionPlayAgainCallout() {");
  const clampEnd   = APP.indexOf("\n  }", clampStart) + 4;
  if (clampStart < 0) { console.log("\nFAIL: _positionPlayAgainCallout() is gone"); process.exit(1); }
  const CLAMP = "var _playAgainCalloutOn = true;\n" + APP.slice(clampStart, clampEnd)
    + "\nfunction clamp(){ _positionPlayAgainCallout(); }";

  // The real action bar, sliced out of preview.html.
  const bs = HTML.indexOf('<div id="pv-action-bar"');
  const be = HTML.indexOf('<!-- Click-to-place card picker panel -->', bs);
  const BAR = HTML.slice(bs, be);
  if (bs < 0 || be < 0) { console.log("\nFAIL: could not slice #pv-action-bar out of preview.html"); process.exit(1); }

  const page = `<!doctype html><html><head><meta charset="utf-8"><style>${CSS}</style>
<style>html,body{margin:0;height:100%}
  /* The bar sits at the bottom of the game screen, as it does in the real one. */
  #stage{position:fixed;left:0;right:0;bottom:0}</style></head>
<body><div id="stage">${BAR}</div><div id="out">RUNNING</div>
<script>${CLAMP}</script>
<script>
(function(){
  var callout = document.getElementById("pv-play-again-callout");
  var endBtn  = document.getElementById("pv-end-turn-inline");
  var VW = document.documentElement.clientWidth, VH = document.documentElement.clientHeight;
  function rec() {
    var c = callout.getBoundingClientRect(), b = endBtn.getBoundingClientRect();
    return {
      shown: getComputedStyle(callout).display !== "none",
      onScreen: c.width > 0 && c.top >= 0 && c.left >= 0 && c.right <= VW && c.bottom <= VH,
      // "over the button": above the whole bar, and horizontally overlapping
      // the button's column.
      above: c.bottom <= document.getElementById("pv-action-bar").getBoundingClientRect().top + 1,
      overlapsX: c.left < b.right && b.left < c.right,
      gap: Math.round(document.getElementById("pv-action-bar").getBoundingClientRect().top - c.bottom),
      // Nothing else is painted on top of the words.
      topAtCentre: (function(){
        var el = document.elementFromPoint(
          Math.max(1, Math.min(VW - 1, c.left + c.width / 2)),
          Math.max(1, Math.min(VH - 1, c.top + c.height / 2)));
        if (!el) return "nothing";
        return (el.closest && el.closest("#pv-play-again-callout"))
          ? "the callout" : (el.id || el.className || el.tagName);
      })(),
    };
  }
  var out = { VW: VW, VH: VH, hidden: rec() };
  callout.classList.add("show");
  clamp();
  out.shown = rec();
  // The arrow must still land on the button after the clamp.
  var ar = callout.querySelector(".pac-arrow").getBoundingClientRect();
  var eb = endBtn.getBoundingClientRect();
  out.arrowOnButton = ar.left >= eb.left - 2 && ar.right <= eb.right + 2;
  // The whole point of hanging it off the BAR: on a narrow screen the bar
  // wraps onto two or three rows, and a bubble anchored to End Turn lands on
  // top of Play Card. Nothing in the bar may be underneath it.
  out.covers = (function(){
    var c = callout.getBoundingClientRect(), hit = [];
    [].forEach.call(document.querySelectorAll("#pv-action-bar button, #pv-action-bar select, #pv-action-bar label"), function(n){
      if (n === callout || callout.contains(n)) return;
      var b = n.getBoundingClientRect();
      if (b.width && c.left < b.right && b.left < c.right && c.top < b.bottom && b.top < c.bottom) {
        hit.push(n.id || n.className || n.tagName);
      }
    });
    return hit;
  })();
  document.getElementById("out").textContent = JSON.stringify(out);
})();
</script></body></html>`;

  const f = path.join(os.tmpdir(), "cc_play_again_callout.html");
  fs.writeFileSync(f, page);

  for (const [label, w, h] of [
    ["laptop (1440x900)",           1440, 900],
    ["phone sideways (1266x498)",   1266, 498],
    ["phone upright (585x1179)",     585, 1179],
  ]) {
    console.log("\nrendered on a " + label);
    const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
      "--hide-scrollbars", `--window-size=${w},${h + CHROME_CHROME_PX}`,
      "--virtual-time-budget=6000", "--dump-dom", "file://" + f],
      { encoding: "utf8", maxBuffer: 64e6 });
    const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
    const r = JSON.parse((m ? m[1] : "{}").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));

    check("it is not there until the extra turn is", r.hidden && !r.hidden.shown);
    check("shown, it is fully on screen", r.shown && r.shown.onScreen);
    check("it sits above the End Turn button", r.shown && r.shown.above);
    check("…and horizontally over it", r.shown && r.shown.overlapsX);
    check(`…close enough above the bar to read as pointing at it (${r.shown && r.shown.gap}px)`,
          r.shown && r.shown.gap >= 0 && r.shown.gap <= 90);
      check(`nothing is painted over the words (top element: ${r.shown && r.shown.topAtCentre})`,
          r.shown && r.shown.topAtCentre === "the callout");
    check("the pointer still lands on the End Turn button after the edge clamp", r.arrowOnButton);
    check(`it covers no other control in the bar (hit: ${JSON.stringify(r.covers)})`,
          Array.isArray(r.covers) && r.covers.length === 0);
  }
}

console.log("\n" + "=".repeat(42));
console.log(`RESULT: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
