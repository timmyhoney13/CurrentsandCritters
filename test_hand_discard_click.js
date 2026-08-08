#!/usr/bin/env node
/* Clicking a card in the hand gives you THAT card — measured in real pixels.
 *
 * Run:  node test_hand_discard_click.js        (needs Google Chrome installed)
 *
 * The bug: "it does not discard the one you want". A hand card is PAINTED by a
 * CSS transform — rotate(angle) then translateY(lift) along its own rotated
 * axis — so at the edges of a wide fan the card sits up to ~25 px sideways and
 * ~65 px below its layout box. Hover hit-tested the LAYOUT box (offsetLeft /
 * offsetTop, deliberately, to stop a lift/flicker feedback loop) while the
 * browser dispatched the click by the PAINTED box. Two different answers to
 * "which card is under the pointer": the card that lifted was not the card that
 * got clicked. It only shows up in a big fan — and the hand-limit discard screen
 * renders EVERY card (11, 13, 16…), which is exactly where players hit it.
 *
 * The fix is one shared hit test, _handHitTestIdx, over each card's BASE
 * transformed quad (the shape it has when nothing is hovered — stable under the
 * hover lift, so no flicker, and it is what the player is aiming at). Hover,
 * click and dragstart all route through it.
 *
 * What this file measures, in headless Chrome, against the REAL preview.css and
 * the REAL functions lifted from preview-app.js, for hands of 11–16 cards:
 *   1. AGREEMENT — for every point where Chrome itself paints card i on top
 *      (document.elementFromPoint), _handHitTestIdx must answer i. Every point,
 *      every card, no exceptions.
 *   2. WHAT LIFTS IS WHAT YOU GET — hover a point, the card lifts, click the same
 *      point: the routed click handler must fire for the card that lifted, even
 *      though it has moved out from under the pointer and Chrome now dispatches
 *      the event to a neighbour (or to the hand background).
 *   3. STABILITY — hovering must not change any card's hit shape (that is the
 *      no-flicker property the old layout-rect test was protecting).
 *   4. The old layout-rect model is reported as a baseline, so the numbers show
 *      how wrong it was, and a source grep guards against it coming back.
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

const CSS = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const APP = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");

// ── Source guards (run with or without Chrome) ───────────────────────────────
const srcLines = [];
function srcOk(cond, m) { srcLines.push((cond ? "PASS " : "FAIL ") + m); }

srcOk(
  /function _handHitTestIdx\(/.test(APP),
  "preview-app.js: _handHitTestIdx() exists — one hit test shared by hover and click"
);
srcOk(
  /function _handCardAt\(/.test(APP) && /_handCardAt\(ev\.clientX, ev\.clientY\)/.test(APP),
  "preview-app.js: clicks are re-aimed through _handCardAt(ev.clientX, ev.clientY)"
);
srcOk(
  /card\.__ccHandClick = onCardClick/.test(APP),
  "preview-app.js: each card carries its own handler so a mis-dispatched click can be routed to it"
);
// The old model: a hit test built from offsetLeft/offsetTop. It must not come back.
srcOk(
  !/offsetLeft[\s\S]{0,400}?cx >= l/.test(APP),
  "preview-app.js: no layout-rect (offsetLeft/offsetTop) hit test remains in the hand"
);
srcOk(
  /_handHitTestIdx\(cx, cy\)/.test(APP),
  "preview-app.js: the hover listener delegates to the shared hit test"
);

// The pixel half runs the real functions, so it can only run if they are there.
if (srcLines.some(l => l.startsWith("FAIL"))) {
  console.log(srcLines.join("\n"));
  console.log("\nThe shared hand hit test is missing or bypassed — the pixel checks cannot run.");
  process.exit(1);
}
if (!CHROME) {
  console.log(srcLines.join("\n"));
  console.log("\nSKIP: no Chrome/Chromium found — the pixel half of this check did not run.");
  process.exit(0);
}

// ── Lift the real geometry functions out of preview-app.js ───────────────────
function grabFn(name) {
  const start = APP.indexOf(`\n  function ${name}(`);
  if (start < 0) throw new Error(`function ${name}() not found in preview-app.js`);
  const i = APP.indexOf("{", start);
  let depth = 0;
  for (let j = i; j < APP.length; j++) {
    const ch = APP[j];
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return APP.slice(start, j + 1); }
  }
  throw new Error(`unbalanced braces reading ${name}()`);
}
function grabConst(name) {
  const m = APP.match(new RegExp(`^\\s*const\\s+${name}\\s*=.*$`, "m"));
  if (!m) throw new Error(`const ${name} not found in preview-app.js`);
  return m[0].trim();
}
const GEOM = [
  grabFn("computeHandTransforms"),
  grabFn("_handCornerRadius"),
  grabFn("_handHitTestIdx"),
  grabFn("_handCardAt"),
  // applyHandLayout squeezes the fan to the width the hand really has and
  // reserves the arc's underhang, so both helpers have to come along.
  grabConst("HAND_BASE_OVERLAP"),
  grabConst("HAND_MIN_STEP"),
  grabConst("HAND_EDGE_SLACK"),
  grabFn("handRoomPx"),
  grabFn("fitHandFan"),
  grabFn("handFanUnderhang"),
  grabFn("applyHandLayout"),
].join("\n");

// ── The page ─────────────────────────────────────────────────────────────────
const page = (n) => `<!doctype html><html><head><meta charset="utf-8">
<title>hand click</title>
<style>${CSS}</style>
<style>
  /* Freeze every animation: this test measures final geometry, not tweens. */
  *, *::before, *::after { transition: none !important; animation: none !important; }
  body { margin: 0; background: #05202f; }
  /* The hand strip sits where the real game puts it, with room above for the
     hover lift and below for the fan's outer cards. */
  #pv-hand-zone { position: absolute; left: 0; right: 0; bottom: 40px; }
</style>
</head>
<body>
<div id="pv-hand-zone"><div id="pv-hand"></div></div>
<div id="out">RUNNING</div>
<script>
// Build a hand of ${n} cards the way renderHand() does: .pv-hand-card carrying
// data-entry-uid, with a .pv-card-inner child (the visible art).
(function buildHand() {
  var zone = document.getElementById("pv-hand");
  for (var i = 0; i < ${n}; i++) {
    var card = document.createElement("div");
    card.className = "pv-hand-card";
    card.dataset.entryUid = String(100 + i);
    card.dataset.faceUid  = String(100 + i);
    card.dataset.idx = String(i);
    var inner = document.createElement("div");
    inner.className = "pv-card-inner";
    card.appendChild(inner);
    zone.appendChild(card);
  }
})();
</script>
<script>
var _handCardEls = [];
var _handRadiusPx = null;   // module-level cache the lifted _handCornerRadius() fills
${GEOM}
</script>
<script>
var results = [];
function ok(cond, m) { results.push((cond ? "PASS " : "FAIL ") + m); }

var zone = document.getElementById("pv-hand");
_handCardEls = Array.prototype.slice.call(zone.querySelectorAll(".pv-hand-card[data-entry-uid]"));
var N = _handCardEls.length;

// The click routing from renderHand(): each card owns a handler, and the bound
// listener re-aims through the shared hit test before calling one.
var clicked = -1;
_handCardEls.forEach(function (card, i) {
  card.__ccHandClick = function () { clicked = i; };
  card.addEventListener("click", function (ev) {
    ev.stopPropagation();
    var aimed = (ev.clientX || ev.clientY) ? _handCardAt(ev.clientX, ev.clientY) : null;
    ((aimed && aimed.__ccHandClick) || card.__ccHandClick)(ev);
  });
});
// …and the background fallback, for when the aimed card lifted away and left
// nothing under the pointer (same allowlist as the real one).
var FALLTHROUGH = { "pv-hand": 1, "pv-hand-zone": 1, "pv-game": 1 };
document.addEventListener("click", function (ev) {
  var t = ev.target;
  if (!t || (t.closest && t.closest(".pv-hand-card"))) return;
  var isBg = t === document.body || t === document.documentElement || (t.id && FALLTHROUGH[t.id]);
  if (!isBg) return;
  var aimed = _handCardAt(ev.clientX, ev.clientY);
  if (aimed && aimed.__ccHandClick) { ev.stopPropagation(); aimed.__ccHandClick(ev); }
}, true);

// The model this replaces: hit-test the un-transformed layout box.
function layoutHitIdx(cx, cy) {
  var zr = zone.getBoundingClientRect();
  for (var i = _handCardEls.length - 1; i >= 0; i--) {
    var el = _handCardEls[i];
    var l = zr.left + el.offsetLeft, t = zr.top + el.offsetTop;
    if (cx >= l && cx <= l + el.offsetWidth && cy >= t && cy <= t + el.offsetHeight) return i;
  }
  return -1;
}

// How far inside card i's painted quad a point is, in px (negative = outside).
// Same inverse transform the real hit test uses, without the edge slack.
function insetOf(i, cx, cy) {
  var el = _handCardEls[i], t = computeHandTransforms(_handCardEls.length, -1)[i];
  var zr = zone.getBoundingClientRect(), w = el.offsetWidth, h = el.offsetHeight;
  var ox = zr.left + el.offsetLeft + w / 2, oy = zr.top + el.offsetTop + h / 2;
  var a = (t.angle || 0) * Math.PI / 180, ca = Math.cos(-a), sa = Math.sin(-a);
  var dx = cx - ox - (t.translateX || 0), dy = cy - oy;
  var lx = dx * ca - dy * sa, ly = dx * sa + dy * ca - (t.translateY || 0);
  return Math.min(w / 2 - Math.abs(lx), h / 2 - Math.abs(ly));
}

// Points where Chrome paints card i on top, sampled over its rendered box, and
// where it is UNAMBIGUOUS which card is being pointed at: at least 2 px inside
// card i, and at least 2 px outside every card drawn in front of it. Blink
// rasterises these rotated cards on composited layers snapped to whole device
// pixels, so its own hit region wobbles about a pixel either side of the exact
// quad — inside that band the browser cannot say which card it means either, and
// no player can aim there. Everywhere else, agreement must be perfect.
var EDGE = 2;
function paintedPoints(i) {
  var card = _handCardEls[i];
  var r = card.getBoundingClientRect();
  var pts = [];
  for (var fx = 0.12; fx <= 0.88; fx += 0.076) {
    for (var fy = 0.10; fy <= 0.90; fy += 0.08) {
      var x = Math.round(r.left + r.width * fx);
      var y = Math.round(r.top + r.height * fy);
      if (x < 0 || y < 0 || x >= window.innerWidth || y >= window.innerHeight) continue;
      var hit = document.elementFromPoint(x, y);
      var owner = hit && hit.closest ? hit.closest(".pv-hand-card") : null;
      if (owner !== card) continue;
      if (insetOf(i, x, y) < EDGE) continue;
      var ambiguous = false;
      for (var j = i + 1; j < _handCardEls.length; j++) {
        if (insetOf(j, x, y) > -EDGE) { ambiguous = true; break; }
      }
      if (!ambiguous) pts.push([x, y]);
    }
  }
  return pts;
}

// A FIXED grid over the whole hand area (never recomputed from a card's live
// rect, which moves when a card lifts). Probing it tells us the hit map.
var PROBE = (function () {
  var zr = zone.getBoundingClientRect(), pts = [];
  for (var x = Math.round(zr.left) - 40; x < zr.right + 40; x += 7)
    for (var y = Math.round(zr.top) - 80; y < zr.bottom + 100; y += 7)
      pts.push([x, y]);
  return pts;
})();
function hitMap() {
  return PROBE.map(function (p) { return _handHitTestIdx(p[0], p[1]); }).join(",");
}

function run() {
  applyHandLayout(-1);

  // 1 — AGREEMENT with what Chrome actually paints.
  var total = 0, wrong = 0, wrongLayout = 0, worstCard = -1, worstMiss = 0;
  var perCardOk = 0;
  for (var i = 0; i < N; i++) {
    var pts = paintedPoints(i);
    if (!pts.length) { ok(false, "card " + i + " of " + N + " has no visible surface to click"); continue; }
    var miss = 0;
    for (var p = 0; p < pts.length; p++) {
      total++;
      if (_handHitTestIdx(pts[p][0], pts[p][1]) !== i) { wrong++; miss++; }
      if (layoutHitIdx(pts[p][0], pts[p][1]) !== i) wrongLayout++;
    }
    if (miss === 0) perCardOk++;
    else if (miss > worstMiss) { worstMiss = miss; worstCard = i; }
  }
  ok(wrong === 0, N + " cards: the hit test agrees with the painted card at all " + total +
     " sampled points" + (wrong ? " (" + wrong + " wrong, worst card #" + worstCard + ")" : ""));
  ok(perCardOk === N, N + " cards: every single card is hit-testable over its whole visible face");
  results.push("NOTE " + N + " cards: the old layout-box model was wrong at " + wrongLayout +
               "/" + total + " of those points");

  // 2 — WHAT LIFTS IS WHAT YOU GET.
  var bad = [];
  for (var c = 0; c < N; c++) {
    applyHandLayout(-1);
    var pts = paintedPoints(c);
    if (!pts.length) continue;
    // Aim at the middle of the card's visible face, the way a player does.
    var pt = pts[Math.floor(pts.length / 2)];
    // Hover: the shared test decides who lifts (this is what the pointermove
    // listener does), then the fan re-lays out around it.
    var hoverIdx = _handHitTestIdx(pt[0], pt[1]);
    applyHandLayout(hoverIdx);
    // Click at the SAME pixel. Chrome dispatches to whatever is there now.
    clicked = -1;
    var target = document.elementFromPoint(pt[0], pt[1]) || zone;
    target.dispatchEvent(new MouseEvent("click", {
      bubbles: true, cancelable: true, clientX: pt[0], clientY: pt[1]
    }));
    if (hoverIdx !== c || clicked !== c) {
      bad.push("card " + c + " (lifted " + hoverIdx + ", clicked " + clicked + ")");
    }
  }
  ok(bad.length === 0, N + " cards: hovering then clicking the same pixel selects the card that lifted" +
     (bad.length ? " — wrong for " + bad.join("; ") : ""));

  // 3 — STABILITY: the hover lift must not move any card's hit shape.
  applyHandLayout(-1);
  var base = hitMap(), stable = true;
  for (var h = 0; h < N; h++) {
    applyHandLayout(h);
    if (hitMap() !== base) { stable = false; break; }
  }
  ok(stable, N + " cards: the hit map over " + PROBE.length +
     " fixed points is identical whether or not a card is hovered (no lift/flicker loop)");
  applyHandLayout(-1);

  document.getElementById("out").textContent = results.join("\\n");
}

try { run(); }
catch (err) {
  document.getElementById("out").textContent = "FAIL exception: " + (err && err.stack || err);
}
</script>
</body></html>`;

function runChrome(width, height, n) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-handclick-"));
  const file = path.join(tmp, "hand.html");
  fs.writeFileSync(file, page(n));
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
  return report.split("\n").map(l => l.replace(/^(PASS|FAIL|NOTE) /, `$1 [${n} cards] `));
}

const lines = srcLines.slice();
// Hand sizes the discard screen really renders: over the ten-card limit by 1–6.
// (renderHand shows EVERY card once you are over the limit, so the fan is at its
// widest exactly when you are being asked to pick one to throw away.)
for (const n of [11, 12, 13, 14, 16]) lines.push(...runChrome(1440, 900, n));
// And a laptop-narrow window, where the fan is squeezed hardest.
lines.push(...runChrome(1024, 780, 13));

console.log(lines.join("\n"));
const failed = lines.filter(l => l.startsWith("FAIL"));
console.log(`\n${lines.filter(l => l.startsWith("PASS")).length} passed, ${failed.length} failed`);
process.exit(failed.length ? 1 : 0);
