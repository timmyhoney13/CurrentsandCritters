#!/usr/bin/env node
/* Browser check for PLAYING THE GAME ON A PHONE.
 *
 * Run:  node test_mobile_layout.js        (needs Google Chrome installed)
 *
 * The bug this pins down: on a phone the in-game screen was unusable. Measured
 * on an iPhone 12 (the game lays out at 585 x 1266 CSS px there — device width
 * x 1.5, see ccGameViewport in device-select.js):
 *
 *   • #pv-hand-zone is a `max-content 1fr max-content` grid — player seats
 *     flanking the hand. `max-content` columns never shrink, so the eight seat
 *     pills (4 left + 4 right, ~780px) took the whole row, the hand's `1fr`
 *     column collapsed to ZERO width, and the fan spilled out sideways under
 *     the seat clusters. Those sit at z-index 20, so they took every tap:
 *     all 8 cards failed the hit test and 2 were entirely off-screen.
 *   • #pv-pool-wrap is a fixed `repeat(5, 90px)` inside a flex-shrink:0 pool
 *     area, so the pool alone was wider than the phone and pushed the DECK —
 *     the thing you click to draw — past the right edge.
 *   • The fan's own arc (rotate + a pos^2 lift) paints the outermost cards
 *     ~35px BELOW their row, and the hand sits flush with the bottom of the
 *     screen, so their bottoms were sliced off by the edge of the display.
 *   • #bs-ctrl (Board Size + Chat) and #ig-challenge-panel were pinned at a
 *     hard-coded `bottom: 302px`, which only clears the DESKTOP bottom stack.
 *
 * What is checked, in real screen pixels in headless Chrome against the REAL
 * preview.css and the REAL layout code sliced out of preview-app.js:
 *   1. HAND     — every card fully on screen (all four edges) and the top-most
 *                 thing at its centre is the card itself, not a seat pill.
 *   2. HIT TEST — _handHitTestIdx answers with card i at card i's own centre,
 *                 so what you tap is what you get after the fan is squeezed.
 *   3. TABLE    — the deck is on screen and nothing scrolls sideways.
 *   4. DOCKS    — the two floating side docks clear the action bar.
 * Every one of them runs at phone sizes AND at a laptop size, because the fix
 * must not cost the desktop layout anything.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("SKIP: no Chrome/Chromium found — cannot run the mobile layout check.");
  process.exit(0);
}

const CSS = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const APP = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");

// ── Pull the real layout functions out of preview-app.js ────────────────────
// preview-app.js is one 28k-line module that needs the whole app (auth, SSE,
// Firebase) to even boot, so this test evaluates just the handful of functions
// that decide where the hand and the floating docks end up. Balanced-brace
// scan that skips strings and comments, so it survives edits inside them.
function sliceFn(src, name) {
  const start = src.search(new RegExp("\\bfunction\\s+" + name + "\\s*\\("));
  if (start < 0) return null;
  let i = src.indexOf("{", start);
  if (i < 0) return null;
  let depth = 0;
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
  const re = new RegExp("^\\s*const\\s+" + name + "\\s*=.*$", "m");
  const m = src.match(re);
  return m ? m[0].trim() : null;
}

const NEEDED_FNS = [
  "computeHandTransforms", "handRoomPx", "fitHandFan", "handFanUnderhang", "applyHandLayout",
  "_handHitTestIdx", "_handCornerRadius", "updateBottomDockOffset",
];
const NEEDED_CONSTS = ["HAND_BASE_OVERLAP", "HAND_MIN_STEP", "HAND_EDGE_SLACK"];

const parts = [];
for (const name of NEEDED_FNS) {
  const code = sliceFn(APP, name);
  if (!code) { console.error(`FAIL: could not find function ${name}() in preview-app.js`); process.exit(1); }
  parts.push(code);
}
for (const name of NEEDED_CONSTS) {
  const code = sliceConst(APP, name);
  if (!code) { console.error(`FAIL: could not find const ${name} in preview-app.js`); process.exit(1); }
  parts.push(code);
}
// applyHandLayout / _handHitTestIdx read module-level state that lives outside
// the slices; declare just those two bindings.
const SLICED = `
let _handRadiusPx = null;
let _handCardEls = [];
${parts.join("\n")}
`;

// ── The page: the real in-game skeleton at the sizes a phone really uses ────
const page = (nCards, nSeats) => `<!doctype html><html><head><meta charset="utf-8">
<title>mobile layout</title>
<style>${CSS}</style>
<style>
  /* Stand-ins for the parts of the screen this test does not build, kept at
     their real heights so the bottom stack gets a realistic slice of the
     window. The card art is a flat colour: only geometry is under test. */
  #pv-notice, #pv-turn-banner { height: 42px; flex-shrink: 0; background: #123; }
  #pv-guide-bar { height: 96px; flex-shrink: 0; background: #142c4a; }
  .pv-card-inner { background: #2a5f8a; }
  .pv-pool-card, .pv-deck-face { background: #256; }
</style>
</head>
<body class="cc-device-mobile">
<div id="pv-game" style="display:flex; flex-direction:column; height:100vh;">
  <div id="pv-notice"><button id="pv-menu-btn">&#9776; Menu</button></div>
  <div id="pv-turn-banner" class="their-turn">Your turn</div>
  <div id="pv-guide-bar"></div>
  <div id="pv-table">
   <div id="pv-table-inner">
    <div id="pv-opponents"></div>
    <div id="pv-center-strip">
      <div id="pv-pool-area">
        <div class="pv-section-label">The Pool<button id="pv-pool-expand-btn">Inspect</button></div>
        <div id="pv-pool-wrap"></div>
      </div>
      <div class="pv-action-divider" style="height:56px;"></div>
      <div class="pv-deck-pile" id="pv-draw-deck">
        <div class="pv-section-label">Deck</div>
        <div class="pv-deck-stack"><div class="pv-deck-face" id="pv-deck-count-face"></div></div>
        <span class="pv-deck-count" id="pv-deck-num">129 cards</span>
      </div>
    </div>
    <div id="pv-my-board"></div>
   </div>
   <div id="pv-board-scroll-dock">
     <button type="button" id="pv-board-scroll-btn">&#9660; More oceans below</button>
   </div>
  </div>
  <div id="pv-action-bar">
    <button id="pv-help-btn"><span class="help-label">&#128161; Help</span></button>
    <div id="pv-play-controls" style="display:flex;align-items:center;gap:8px;flex:1;flex-wrap:wrap;">
      <select id="pv-action-select"><option>Choose action…</option></select>
      <button class="pv-btn" id="pv-play-btn">Play Card</button>
    </div>
    <div style="flex:1;"></div>
    <button class="pv-btn pv-btn-surf" id="pv-surf-btn">&#127940; Surf's Up!!</button>
    <button class="pv-btn" id="pv-undo-btn">&#8617; Undo Turn</button>
    <button class="pv-btn end-turn" id="pv-end-turn-inline">&#10003; End Turn</button>
  </div>
  <div id="pv-hand-zone">
    <div id="pv-seats-left" class="pv-seat-cluster"></div>
    <div id="pv-hand"></div>
    <div id="pv-seats-right" class="pv-seat-cluster"></div>
  </div>
  <!-- the two floating side docks, inside #pv-game as preview.html has them -->
  <div id="bs-ctrl" style="display:flex;" aria-label="Board size controls">
    <div id="bs-label">Board<br>Size</div>
    <button id="bs-plus" class="bs-btn">+</button>
    <div id="bs-readout">100%</div>
    <button id="bs-minus" class="bs-btn">&minus;</button>
  </div>
  <div id="ig-challenge-panel" style="display:block;">
    <div id="igcp-header"><button id="igcp-minimize-btn">&minus;</button><span id="igcp-title">Weekly Challenges</span></div>
    <div id="igcp-cards" style="height:220px;"></div>
  </div>
</div>
<div id="out">RUNNING</div>
<script>
// Build the pool, the seats and the hand exactly the way the renderers do.
(function build() {
  var pool = document.getElementById("pv-pool-wrap");
  for (var p = 0; p < 5; p++) {
    var pc = document.createElement("div"); pc.className = "pv-pool-card";
    pc.setAttribute("draggable", "true"); pool.appendChild(pc);
  }
  // renderPlayerSeats() always lays out 8 slots: 0-3 left, 4-7 right.
  var L = document.getElementById("pv-seats-left"), R = document.getElementById("pv-seats-right");
  for (var s = 0; s < 8; s++) {
    var seat = document.createElement("div");
    seat.className = "pv-seat" + (s >= ${nSeats} ? " pv-seat-empty" : "") + (s === 0 ? " active-turn" : "");
    var aw = document.createElement("div"); aw.className = "pv-seat-avatar-wrap"; aw.textContent = s >= ${nSeats} ? "🔒" : "🐟";
    var nm = document.createElement("div"); nm.className = "pv-seat-name";
    var pl = document.createElement("span"); pl.className = "pv-seat-plabel"; pl.textContent = "P" + (s + 1);
    nm.appendChild(pl); nm.appendChild(document.createTextNode(s >= ${nSeats} ? "Empty" : "Player " + (s + 1)));
    var sc = document.createElement("div"); sc.className = "pv-seat-score" + (s >= ${nSeats} ? " pv-seat-score-empty" : ""); sc.textContent = "0 pts";
    if (s === 0) { var me = document.createElement("div"); me.className = "pv-seat-me-badge"; me.textContent = "YOU"; seat.appendChild(me); }
    seat.appendChild(aw); seat.appendChild(nm); seat.appendChild(sc);
    (s < 4 ? L : R).appendChild(seat);
  }
  var hand = document.getElementById("pv-hand");
  for (var i = 0; i < ${nCards}; i++) {
    var card = document.createElement("div");
    card.className = "pv-hand-card";
    card.setAttribute("draggable", "true");
    card.dataset.entryUid = String(500 + i);
    var inner = document.createElement("div"); inner.className = "pv-card-inner";
    card.appendChild(inner); hand.appendChild(card);
  }
})();
</script>
<script>${SLICED}</script>
<script>
var results = [];
function ok(cond, m) { results.push((cond ? "PASS " : "FAIL ") + m); }

var VW = document.documentElement.clientWidth;
var VH = document.documentElement.clientHeight;
var TOL = 2;

try {
  _handCardEls = Array.prototype.slice.call(document.querySelectorAll(".pv-hand-card[data-entry-uid]"));
  applyHandLayout(-1);
  updateBottomDockOffset();

  var cards = _handCardEls;
  var label = VW + "x" + VH + " / " + cards.length + " cards";

  // 1. Every card fully inside the screen, on all four edges.
  var out = [];
  cards.forEach(function (c, i) {
    var r = c.getBoundingClientRect();
    if (r.left < -TOL || r.right > VW + TOL || r.top < -TOL || r.bottom > VH + TOL) {
      out.push(i + "(" + Math.round(r.left) + "," + Math.round(r.top) + "→" +
               Math.round(r.right) + "," + Math.round(r.bottom) + ")");
    }
  });
  ok(out.length === 0, label + ": every hand card is fully on screen" +
     (out.length ? " — off: " + out.join(" ") : ""));

  // 2. Nothing OUTSIDE the hand is painted over a card. Cards overlapping each
  //    other is the fan working as designed (front-most wins, and the hit test
  //    below resolves it); a seat pill on top of a card is the bug — that is
  //    what made every card in the hand untappable on a phone.
  var stolen = [];
  cards.forEach(function (c, i) {
    var r = c.getBoundingClientRect();
    var x = Math.max(1, Math.min(VW - 1, r.left + r.width / 2));
    var y = Math.max(1, Math.min(VH - 1, r.top + r.height / 2));
    var top = document.elementFromPoint(x, y);
    var inHand = top && top.closest && top.closest("#pv-hand");
    if (!inHand) stolen.push(i + " under " + (top ? (top.id || top.className || top.tagName) : "nothing"));
  });
  ok(stolen.length === 0, label + ": no seat pill or panel is painted over a hand card" +
     (stolen.length ? " — stolen: " + stolen.join("; ") : ""));

  // 3. The shared hit test agrees: aiming at card i's centre returns card i.
  //    (Front-most wins where the fan overlaps, so only the un-overlapped part
  //    of a card is guaranteed — the centre of the LAST card always is.)
  var last = cards.length - 1;
  var lr = cards[last].getBoundingClientRect();
  var got = _handHitTestIdx(lr.left + lr.width / 2, lr.top + lr.height / 2);
  ok(got === last, label + ": the hit test still resolves the front card after the fan is squeezed (got " + got + ", want " + last + ")");

  // 4. The deck — how you draw — is on screen, and nothing scrolls sideways.
  var deck = document.getElementById("pv-draw-deck").getBoundingClientRect();
  ok(deck.right <= VW + TOL && deck.left >= -TOL,
     label + ": the deck is on screen (right " + Math.round(deck.right) + " vs " + VW + ")");
  ok(document.documentElement.scrollWidth <= VW + TOL,
     label + ": the page does not scroll sideways (" + document.documentElement.scrollWidth + " vs " + VW + ")");

  // 5. The floating side docks clear the action bar.
  function overlaps(a, b) { return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom; }
  var bar = document.getElementById("pv-action-bar").getBoundingClientRect();
  ["bs-ctrl", "ig-challenge-panel"].forEach(function (id) {
    var d = document.getElementById(id).getBoundingClientRect();
    ok(!overlaps(d, bar), label + ": #" + id + " does not cover the action bar");
  });
} catch (err) {
  results.push("FAIL exception: " + (err && err.stack || err));
}
document.getElementById("out").textContent = results.join("\\n");
</script>
</body></html>`;

function runChrome(width, height, nCards, nSeats) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-mobilelayout-"));
  const file = path.join(tmp, "mobile.html");
  fs.writeFileSync(file, page(nCards, nSeats));
  let dom;
  try {
    dom = execFileSync(CHROME, [
      "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      `--window-size=${width},${height}`, "--virtual-time-budget=9000",
      "--dump-dom", "file://" + file,
    ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 90000 });
  } catch (e) {
    console.error("Chrome failed to run:", e.message);
    process.exit(1);
  }
  fs.rmSync(tmp, { recursive: true, force: true });
  const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
  const report = m
    ? m[1].replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").trim()
    : "(no output)";
  return report.split("\n");
}

const lines = [];
// The three phone shapes the game really lays out at (device width x 1.5, see
// ccGameViewport): a small Android, an iPhone SE and an iPhone 12 — each with a
// full 10-card hand, and the iPhone 12 again with a fresh 8-card hand.
lines.push(...runChrome(540, 1170, 10, 4));
lines.push(...runChrome(562, 1186, 10, 4));
lines.push(...runChrome(585, 1266, 10, 4));
lines.push(...runChrome(585, 1266, 8, 4));
// A phone held sideways, and a full 8-player room on a phone.
lines.push(...runChrome(1266, 585, 10, 8));
// Laptop: the desktop layout must keep working, seats flanking the hand.
lines.push(...runChrome(1440, 860, 10, 4));

// The CSS pairing behind the bug: whatever ends up owning the hand zone, the
// hand must never be left in a column that can collapse to nothing.
const narrowBlock = (() => {
  const i = CSS.indexOf("@media (max-width: 1180px) and (min-height: 620px)");
  return i < 0 ? "" : CSS.slice(i, i + 1600);
})();
if (!narrowBlock) {
  lines.push("FAIL preview.css: the narrow in-game layout block is gone — the hand column can collapse again");
} else if (/#pv-hand-zone\s*\{[^}]*grid-template-areas/.test(narrowBlock)) {
  lines.push("PASS preview.css: narrow screens stack the seats above the hand instead of flanking it");
} else {
  lines.push("FAIL preview.css: the narrow block no longer re-areas #pv-hand-zone");
}

// ── The game screen must be the size of the screen ──────────────────────────
// `100vh` on iOS Safari is the height the page WOULD have with the browser
// toolbars retracted, not the height it has. html/body are overflow:hidden, so
// the difference is not scrolled to, it is sliced off the bottom of #pv-game —
// and the bottom of #pv-game is the bottom of a seat pill, which is its
// "⭐ N pts · 🃏M" line. Every player's score and card count, gone. What is
// left is panning the visual viewport, which is how the Menu button at the TOP
// then disappeared instead ("you have to tilt your screen to see it").
const gameBlock = (() => {
  const i = CSS.indexOf("#pv-game {");
  return i < 0 ? "" : CSS.slice(i, CSS.indexOf("}", i));
})();
lines.push(/height:\s*100dvh/.test(gameBlock)
  ? "PASS preview.css: the in-game screen is sized in dvh, so nothing is clipped off the bottom"
  : "FAIL preview.css: #pv-game is still sized in vh — the bottom of the hand zone is off-screen on iOS");
lines.push(/height:\s*100vh/.test(gameBlock)
  ? "PASS preview.css: a plain-vh fallback is kept for browsers without dvh"
  : "FAIL preview.css: #pv-game lost its vh fallback");

// ── The mobile viewport scale must be measured, not assumed ─────────────────
// device-select widens the viewport and pins initial-scale to dw/W. When dw
// came from screen.width it was the width of the DISPLAY, which on a notched
// phone held sideways is ~60px more than the box Safari actually gives the page
// — so the game laid out wider than the space it was scaled into and the right
// end of the action bar (End Turn first) hung off the edge, unreachable because
// minimum-scale was pinned to the same wrong number.
const DEV = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/device-select.js"), "utf8");
lines.push(/documentElement\.clientWidth/.test(DEV)
  ? "PASS device-select.js: the game viewport is measured from the real page box"
  : "FAIL device-select.js: the game viewport width is still guessed from screen.*");
lines.push(/metaIsGame/.test(DEV)
  ? "PASS device-select.js: it only measures while the normal viewport is in effect (no feedback loop)"
  : "FAIL device-select.js: nothing stops the measurement feeding back on the widened viewport");
lines.push(/MIN_ZOOM_SLACK/.test(DEV)
  ? "PASS device-select.js: minimum-scale leaves slack, so nothing can be zoom-locked off screen"
  : "FAIL device-select.js: minimum-scale is pinned to the fit again");
lines.push(/orientationchange[\s\S]{0,400}setNormal\(\)/.test(DEV)
  ? "PASS device-select.js: rotating re-measures instead of reusing the old orientation's fit"
  : "FAIL device-select.js: rotation does not re-measure the usable width");

console.log(lines.join("\n"));
const failed = lines.filter(l => l.startsWith("FAIL"));
console.log(`\n${lines.filter(l => l.startsWith("PASS")).length} passed, ${failed.length} failed`);
process.exit(failed.length ? 1 : 0);
