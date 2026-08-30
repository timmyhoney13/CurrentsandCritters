#!/usr/bin/env node
/* The "Last Turn" pill in the top-right corner of the game.
 *
 * It reports the turn that just ended: who took it, and what they did. Three
 * things were wrong with it, and the server half of the story is in
 * test_turn_notif_actions.py (a duplicate method name meant the server shipped
 * an empty action list for every turn ever played). This file covers the
 * client half:
 *
 *   • THE FALLBACK LIED. With no server captions the client guessed, and its
 *     guess for "how many did they draw off the deck" was `2 - (cards that
 *     vanished from the pool)`. On a turn that played a card and drew nothing
 *     that is the number 2, so the pill announced "Drew 2 from deck" over a
 *     turn with no draw in it. Deck draws are now COUNTED off deck_remaining.
 *
 *   • IT WAS CLIPPED. .dn-action was `white-space: nowrap` inside a 320px pill,
 *     so "Played Green Sea Turtle → Great Barrier Reef" ended in an ellipsis
 *     halfway through the card's name. Lines now wrap.
 *
 *   • IT WAS GONE TOO FAST. A flat 5-6s for a turn that can be a dozen plays
 *     long. The hold now starts higher and grows with the number of lines.
 *
 * Two halves:
 *   • SOURCE, the real hold function and the real fallback arithmetic, sliced
 *     out of preview-app.js and executed. Reimplementing them here would only
 *     prove that the test can do arithmetic.
 *   • RENDER, real markup, real CSS, headless Chrome: a six-line turn is fully
 *     readable, nothing is clipped, and the pill stays on screen.
 *
 * Run:  node test_turn_notif_pill.js        (render half needs Google Chrome)
 */
"use strict";

const fs   = require("fs");
const os   = require("os");
const path = require("path");
const vm   = require("vm");
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

// ── Source: how long it stays up ────────────────────────────────────────────
console.log("\nit stays up long enough to read, and longer when there is more to read");

const holdStart = APP.indexOf("  const DN_HOLD_BASE_MS");
const holdEnd   = APP.indexOf("\n  }", APP.indexOf("function _drawNotifHoldMs")) + 4;
if (holdStart < 0 || holdEnd < 4) {
  console.log("FAIL: _drawNotifHoldMs() is gone from preview-app.js"); process.exit(1);
}
const holdCtx = vm.createContext({ Math, Number });
vm.runInContext(APP.slice(holdStart, holdEnd).replace(/^  /gm, ""), holdCtx);
const hold = (n) => vm.runInContext(`_drawNotifHoldMs(${n})`, holdCtx);

check(`a one-line turn beats the old flat 6s (${hold(1)}ms)`, hold(1) > 6000);
check("a busier turn is held longer than a quiet one", hold(4) > hold(1));
check("…and longer again", hold(6) > hold(4));
check(`it is capped, a huge turn cannot pin the pill open (${hold(50)}ms)`,
      hold(50) <= 20000 && hold(50) === hold(40));
check("a garbage line count still returns a sane hold",
      hold(0) >= 6000 && Number.isFinite(hold(NaN)) && hold(NaN) >= 6000);

check("both code paths use it, neither is left on a hard-coded number",
      (APP.match(/_drawNotifHoldMs\(/g) || []).length >= 3 &&
      !/_drawNotifTimer = setTimeout\([\s\S]{0,320}?\}, (5000|6000)\);/.test(APP));

// ── Source: the fallback stops inventing draws ──────────────────────────────
console.log("\nthe no-server-captions fallback measures instead of guessing");

check("the '2 minus the pool draws' guess is gone",
      !/Math\.max\(0,\s*2\s*-\s*drawnFromPool\.length\)/.test(APP));
check("deck draws are counted off deck_remaining",
      /_drawTurnStartDeck\s*-\s*deckNow/.test(APP));
check("…against a snapshot taken at the turn boundary",
      /_drawTurnStartDeck = Number\.isFinite/.test(APP));
check("…and that snapshot is cleared with the rest of the per-game state",
      /_drawTurnStartDeck=null/.test(APP));

// The real fallback arithmetic, run on a turn that PLAYED a card and drew
// nothing: the exact turn that used to be captioned "Drew 2 from deck".
(function () {
  const src = APP.slice(APP.indexOf("            const poolNow = Array.isArray(state.pool)"),
                        APP.indexOf("            const prevBoardUids"));
  check("the fallback block is still where the test slices it", src.length > 200);
  const ctx = vm.createContext({ Math, Number, Array, Set, console });
  const run = (startDeck, deckNow, poolStart, poolNow) => {
    ctx._drawTurnStartPool = poolStart.map(u => ({ entry_uid: u }));
    ctx._drawTurnStartDeck = startDeck;
    ctx.state = { pool: poolNow.map(u => ({ entry_uid: u })), deck_remaining: deckNow };
    // `const` in a vm script is a lexical binding, not a property on the
    // context, so the slice runs inside a function and hands its values back.
    return vm.runInContext("(function(){\n" + src.replace(/^ {12}/gm, "") +
      "\nreturn { pool: drawnFromPool.length, deck: deckCount };\n})()", ctx);
  };
  const played = run(40, 40, [1, 3], [1, 3]);
  check(`a turn that played a card and drew nothing reports no draws ` +
        `(pool ${played.pool}, deck ${played.deck})`,
        played.pool === 0 && played.deck === 0);
  const drewTwoDeck = run(40, 38, [1, 3], [1, 3]);
  check("two cards off the deck are reported as two", drewTwoDeck.deck === 2);
  const drewPoolAndDeck = run(40, 39, [1, 3], [3]);
  check("one from the pool and one from the deck is reported as exactly that",
        drewPoolAndDeck.pool === 1 && drewPoolAndDeck.deck === 1);
  const noSnapshot = run(null, 39, [1, 3], [1, 3]);
  check("with no deck snapshot it claims nothing rather than guessing",
        noSnapshot.deck === 0);
})();

// ── Source: the wording rules that belong to the client ─────────────────────
console.log("\nthe pill renders what the server sent");

check("server captions win; the diff fallback is only for a server without them",
      APP.indexOf("showTurnActionsNotif(_drawPrevPlayer, serverActions)") <
      APP.indexOf("showDrawNotif(_drawPrevPlayer, drawnFromPool"));
check("each action is its own block element, not a <br> inside one nowrap line",
      /class="dn-line dn-cards"/.test(APP) && !/\.join\("<br>"\)/.test(APP));
check("a very long turn is capped and says how many it did not list",
      /DN_MAX_LINES/.test(APP) && /\+\$\{hidden\} more/.test(APP));
check("server text is escaped before it is put in innerHTML",
      /replace\(\/&\/g,"&amp;"\)\.replace\(\/</.test(APP));

// ── Source: the CSS contract ────────────────────────────────────────────────
console.log("\nthe pill's CSS lets the words fit");

check("the markup is there", /id="draw-notif"/.test(HTML) && /id="dn-action"/.test(HTML));
check("it is pinned to the top-right corner",
      /#draw-notif \{[\s\S]{0,200}position: fixed;[\s\S]{0,60}top: 80px; right: 18px;/.test(CSS));
check("the action text wraps instead of ending in an ellipsis",
      /\.dn-action \{[\s\S]{0,400}white-space: normal;[\s\S]{0,80}overflow-wrap: anywhere/.test(CSS) &&
      !/\.dn-action \{[\s\S]{0,400}text-overflow: ellipsis/.test(CSS));
check("each action line is a block", /\.dn-line \{ display: block; \}/.test(CSS));
check("the avatar stays at the top of a multi-line pill",
      /#draw-notif \{[\s\S]{0,400}align-items: flex-start;/.test(CSS));

// ── Render ──────────────────────────────────────────────────────────────────
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: skipping the render half.");
} else {
  const CHROME_CHROME_PX = 87;

  // The real pill, sliced out of preview.html.
  const ns = HTML.indexOf('<div id="draw-notif"');
  const ne = HTML.indexOf("</div>", HTML.indexOf('<span class="dn-action"', ns)) + 6;
  const PILL = HTML.slice(ns, HTML.indexOf("</div>", ne) + 6);
  if (ns < 0 || !/dn-action/.test(PILL)) {
    console.log("\nFAIL: could not slice #draw-notif out of preview.html"); process.exit(1);
  }

  // The longest realistic turn: a Loggerhead Sea Turtle chain, with the longest
  // real card and ocean names in the deck.
  const LINES = [
    "Played ocean: Great Barrier Reef",
    "Played Green Sea Turtle → Great Barrier Reef ★",
    "Played Yellowfin Tuna → Great Barrier Reef",
    "Drew Emperor Penguin / Staghorn Coral from the pool",
    "Drew 1 card from the deck",
    "+3 more",
  ];

  const page = `<!doctype html><html><head><meta charset="utf-8"><style>${CSS}</style>
<style>html,body{margin:0;height:100%;background:#06203a}
  /* Measure the SETTLED pill. .visible animates transform from translateX(32px),
     and a rect read on the same frame would be the slide-in start position, not
     where the pill actually rests. */
  *{transition:none !important;animation:none !important}</style></head>
<body>${PILL}<div id="out">RUNNING</div>
<script>
(function(){
  var el = document.getElementById("draw-notif");
  var VW = document.documentElement.clientWidth, VH = document.documentElement.clientHeight;
  var hiddenOpacity = getComputedStyle(el).opacity;
  document.getElementById("dn-name").textContent = "Tim";
  document.getElementById("dn-action").innerHTML =
    ${JSON.stringify(LINES)}.map(function(l){ return '<span class="dn-line dn-cards">' + l + '</span>'; }).join("");
  el.classList.add("visible");
  var box = el.getBoundingClientRect();
  var act = document.getElementById("dn-action");
  var lines = [].slice.call(el.querySelectorAll(".dn-line"));
  document.getElementById("out").textContent = JSON.stringify({
    VW: VW, VH: VH,
    hiddenOpacity: hiddenOpacity,
    onScreen: box.width > 0 && box.top >= 0 && box.left >= 0 &&
              box.right <= VW + 0.5 && box.bottom <= VH + 0.5,
    // Nothing is cut off horizontally, and nothing is cut off vertically.
    clippedX: Math.round(act.scrollWidth - act.clientWidth),
    clippedY: Math.round(el.scrollHeight - el.clientHeight),
    lineCount: lines.length,
    // Every line got real height, i.e. each is its own row.
    distinctTops: (function(){ var s = {}; lines.forEach(function(n){ s[Math.round(n.getBoundingClientRect().top)] = 1; }); return Object.keys(s).length; })(),
    // The avatar is beside the FIRST line, not floating in the middle.
    avatarTopVsFirstLine: Math.round(
      document.getElementById("dn-avatar-wrap").getBoundingClientRect().top -
      el.getBoundingClientRect().top),
    // Ellipsis truncation would leave a line narrower than its own text.
    anyLineClipped: lines.some(function(n){ return n.scrollWidth - n.clientWidth > 1; }),
    widest: Math.round(Math.max.apply(null, lines.map(function(n){ return n.getBoundingClientRect().width; }))),
  });
})();
</script></body></html>`;

  const f = path.join(os.tmpdir(), "cc_turn_notif_pill.html");
  fs.writeFileSync(f, page);

  for (const [label, w, h] of [
    ["laptop (1440x900)",         1440, 900],
    ["small laptop (1024x768)",   1024, 768],
    ["phone sideways (1266x498)", 1266, 498],
    ["phone upright (390x844)",    390, 844],
  ]) {
    console.log("\nrendered on a " + label);
    const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
      "--hide-scrollbars", `--window-size=${w},${h + CHROME_CHROME_PX}`,
      "--virtual-time-budget=4000", "--dump-dom", "file://" + f],
      { encoding: "utf8", maxBuffer: 64e6 });
    const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
    const r = JSON.parse((m ? m[1] : "{}").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));

    check("it is invisible until a turn ends", r.hiddenOpacity === "0");
    check("shown, the whole pill is on screen", r.onScreen === true);
    check(`no line is cut off with an ellipsis (widest line ${r.widest}px)`,
          r.anyLineClipped === false);
    check(`nothing overflows the pill (x ${r.clippedX}px, y ${r.clippedY}px)`,
          r.clippedX <= 0 && r.clippedY <= 0);
    check(`all ${LINES.length} actions are shown, each on its own row ` +
          `(${r.distinctTops} rows)`,
          r.lineCount === LINES.length && r.distinctTops === LINES.length);
    check(`the avatar sits beside the first line (${r.avatarTopVsFirstLine}px from the top)`,
          r.avatarTopVsFirstLine >= 0 && r.avatarTopVsFirstLine <= 24);
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
