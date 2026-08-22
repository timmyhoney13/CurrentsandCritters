#!/usr/bin/env node
/* The end-game screen has to keep trying, and has to say when it hasn't.
 *
 * The bug: saveGameStats is built to be retried, a failed write never sets
 * _lastSavedWinner, and the comment promises "the next poll retries". That was
 * only true while the game was still moving. Once it is over, the room's
 * state_version stops changing, applyServerPayload drops every identical
 * payload as already-rendered, and renderEndGame, the only caller of
 * saveGameStats: is never reached again. So one second of lost network at the
 * wrong moment meant the XP, the streak and the game history were never
 * written, on a screen that displayed all three as if they had been.
 *
 * The fix has two halves and this pins both:
 *   • RETRY, the end screen drives its own timer instead of riding on the
 *     poll, re-arms the moment the device comes back online, and gives up only
 *     into a button the player can press.
 *   • SAY SO, three states, one of which is loud: saving (quiet), saved
 *     (nothing at all), failed (impossible to miss, with the retry in it).
 *
 * Run:  node test_endgame_save_retry.js        (render half needs Google Chrome)
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

// ── The retry actually exists, and is not the poll ──────────────────────────
console.log("\nthe end screen retries the save itself");

check("there is a watchdog", /function _endSaveWatch\(winner, finalScores, xp\)/.test(APP));
check("renderEndGame starts it right after the first save attempt",
      /saveGameStats\(winner, finalScores, totalXp\);[\s\S]{0,600}_endSaveWatch\(winner, finalScores, totalXp\);/.test(APP));
check("it is a real timer, not another hope that a poll will re-render",
      /_endSaveTimer = setInterval\(/.test(APP));
check("it stops the moment the save lands",
      /_endSaveState === "saved"[\s\S]{0,120}_endSaveStop\(\)/.test(APP));
check("it stands down for a session with no account to save to",
      /_endSaveState = "na"/.test(APP));
check("it never fights the attempt already in flight", /if \(_saveInFlight\) return;/.test(APP));
check("it gives up into a button rather than retrying forever",
      /_END_SAVE_MAX_TRIES/.test(APP));
check("coming back online retries immediately",
      /addEventListener\("online"[\s\S]{0,320}_endSaveRetryNow\(\)/.test(APP));
check("the retry also re-pulls room state, which the same drop interrupted",
      /_endSaveRetryNow[\s\S]{0,700}refreshState\(\)/.test(APP));

// The one place that decides whether the results landed: _lastSavedWinner is
// the only proof, and it is only set after a write completes.
check("the verdict is read from _lastSavedWinner, not from 'no exception was thrown'",
      /_endSaveState = \(_lastSavedWinner === winner\) \? "saved" : "failed"/.test(APP));
check("…in a finally, so every exit from the write is covered",
      /\} finally \{[\s\S]{0,420}_endSaveState = \(_lastSavedWinner === winner\)/.test(APP));

console.log("\nit is reset between games, not carried into the next one");
for (const [what, near] of [
  ["a fresh game clears it", "_lastSavedWinner = null;\n      try { _endSaveReset(); }"],
  ["leaving the game clears it", "try { _endSaveReset(); } catch (_) {}"],
]) check(what, APP.includes(near));

// ── What the player sees ────────────────────────────────────────────────────
console.log("\nand it says which of the three states it is in");

check("the banner is in the end-game header, above the fold", (() => {
  const h = HTML.indexOf('id="gs-header"');
  const s = HTML.indexOf('id="gs-save-state"');
  const b = HTML.indexOf('id="gs-body"');
  return h > 0 && s > h && s < b;
})());
check("it is announced to screen readers", /id="gs-save-state" role="status" aria-live="polite"/.test(HTML));
check("it carries its own retry button", /id="gs-save-retry"/.test(HTML));
check("hidden by default", /#gs-save-state \{[\s\S]{0,120}display: none;/.test(CSS));
check("only the saving and failed states are visible at all",
      /#gs-save-state\.saving \{\s*display: flex;/.test(CSS) &&
      /#gs-save-state\.failed \{\s*display: flex;/.test(CSS));
check("the failed state is the loud one",
      /#gs-save-state\.failed \{[\s\S]{0,200}rgba\(232,64,87/.test(CSS));
check("the retry button only exists in the failed state",
      /#gs-save-retry \{[\s\S]{0,60}display: none;/.test(CSS) &&
      /#gs-save-state\.failed #gs-save-retry \{ display: inline-block; \}/.test(CSS));
check("being offline is named as being offline, not as a mystery",
      /You're offline, your XP and stats aren't saved yet\./.test(APP));
check("a reachability failure says the results are not saved YET",
      /Couldn't reach the server, your XP and stats aren't saved yet\./.test(APP));
check("success says nothing (a per-game 'saved!' banner is noise)",
      /"saved" and "na" show nothing/.test(APP));
check("the spinner stops for reduced motion",
      /prefers-reduced-motion[\s\S]{0,120}#gs-save-spinner \{ animation: none/.test(CSS));

// ── Rendered ────────────────────────────────────────────────────────────────
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: skipping the render half.");
} else {
  console.log("\nrendered, through all three states");

  // The real banner markup, and the real _endSaveSyncBanner that drives it.
  const ms = HTML.indexOf('<div id="gs-save-state"');
  const me = HTML.indexOf("</div>", HTML.indexOf('id="gs-save-retry"')) + 6;
  const BANNER = HTML.slice(ms, me);
  const fnStart = APP.indexOf("  function _endSaveSyncBanner() {");
  const fnEnd   = APP.indexOf("\n  }", fnStart) + 4;
  if (ms < 0 || fnStart < 0) { console.log("FAIL: could not slice the banner"); process.exit(1); }
  const SYNC = "var _endSaveState = 'idle';\n" + APP.slice(fnStart, fnEnd);

  const page = `<!doctype html><html><head><meta charset="utf-8"><style>${CSS}</style>
<style>html,body{margin:0}</style></head><body>
<div id="pv-endgame-overlay" style="display:block"><div id="gs-wrap"><div id="gs-header">
${BANNER}
</div></div></div>
<div id="out">RUNNING</div>
<script>${SYNC}</script>
<script>
(function(){
  var box = document.getElementById("gs-save-state");
  var btn = document.getElementById("gs-save-retry");
  var txt = document.getElementById("gs-save-text");
  function rec(){
    return {
      boxShown: getComputedStyle(box).display !== "none",
      btnShown: getComputedStyle(btn).display !== "none",
      spinShown: getComputedStyle(document.getElementById("gs-save-spinner")).display !== "none",
      text: txt.textContent.trim(),
    };
  }
  var out = {};
  out.idle    = (_endSaveState = "idle",    _endSaveSyncBanner(), rec());
  out.pending = (_endSaveState = "pending", _endSaveSyncBanner(), rec());
  out.failed  = (_endSaveState = "failed",  _endSaveSyncBanner(), rec());
  out.saved   = (_endSaveState = "saved",   _endSaveSyncBanner(), rec());
  out.na      = (_endSaveState = "na",      _endSaveSyncBanner(), rec());
  document.getElementById("out").textContent = JSON.stringify(out);
})();
</script></body></html>`;

  const f = path.join(os.tmpdir(), "cc_endgame_save_state.html");
  fs.writeFileSync(f, page);
  const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
    "--hide-scrollbars", "--window-size=1440,987", "--virtual-time-budget=6000",
    "--dump-dom", "file://" + f], { encoding: "utf8", maxBuffer: 64e6 });
  const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
  const r = JSON.parse((m ? m[1] : "{}").replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/&#39;/g, "'"));

  check("idle:    nothing on screen",              r.idle    && !r.idle.boxShown);
  check("pending: a quiet line with a spinner",    r.pending && r.pending.boxShown && r.pending.spinShown && !r.pending.btnShown);
  check("pending: it says it is saving",           r.pending && /Saving your results/.test(r.pending.text));
  check("failed:  on screen, with the retry",      r.failed  && r.failed.boxShown && r.failed.btnShown);
  check("failed:  no spinner (nothing is spinning)", r.failed && !r.failed.spinShown);
  check("failed:  it says the stats are not saved", r.failed && /aren't saved yet/.test(r.failed.text));
  check("saved:   back to nothing on screen",      r.saved   && !r.saved.boxShown);
  check("guest:   nothing on screen either",       r.na      && !r.na.boxShown);
}

console.log("\n" + "=".repeat(42));
console.log(`RESULT: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
