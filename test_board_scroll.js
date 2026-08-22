#!/usr/bin/env node
/* Browser check for SCROLLING THE OCEANS (your board) up and down.
 *
 * Run:  node test_board_scroll.js        (needs Google Chrome installed)
 *
 * The bug this pins down: "on mobile you can not scroll down and look at the
 * bottom of the oceans". #pv-table was `overflow:hidden` unless the board was
 * zoomed IN past 100%, and #pv-table-inner is an auto-height grid, so once a
 * player had enough oceans to wrap onto more rows than fit, everything past the
 * bottom edge was simply clipped: unreachable by wheel, trackpad, finger or
 * pinch (pinch pans the visual viewport, it cannot reveal clipped content).
 *
 * Two halves, both measured in real screen pixels in headless Chrome against
 * the REAL preview.css and the REAL touch-drag shim from preview-app.js:
 *   1. LAYOUT, with a board too tall to fit (desktop and phone-sized windows),
 *      the bottom of the last ocean must be reachable by scrolling, the top must
 *      be reachable again, and there must be exactly ONE vertical scroller in
 *      the chain (nested scrollers make finger scrolling a coin flip).
 *   2. GESTURE, in Mobile mode a one-finger vertical swipe that starts on a
 *      draggable ocean card must scroll the board (native panning is off there:
 *      draggable cards carry touch-action:pinch-zoom so drags don't jitter), a
 *      press-and-hold must still start a real drag, and drags that were always
 *      instant (horizontal, pool cards, hand cards, non-overflowing board) must
 *      stay instant.
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
  console.log("SKIP: no Chrome/Chromium found: cannot run the board-scroll check.");
  process.exit(0);
}

const CSS = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const APP = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");

// ── Pull the real TOUCH-DRAG SHIM out of preview-app.js ──────────────────────
// preview-app.js is one 26k-line module that needs the whole app (auth, SSE,
// Firebase) to even boot, so the gesture half of this test evaluates just that
// one self-contained IIFE. Balanced-delimiter scan, skipping strings and
// comments, so it survives edits inside the shim.
function sliceIIFE(src, marker) {
  const start = src.indexOf(marker);
  if (start < 0) return null;
  let depth = 0, i = start;
  while (i < src.length) {
    const c = src[i];
    if (c === '"' || c === "'" || c === "`") {           // skip string literal
      const q = c; i++;
      while (i < src.length && src[i] !== q) { if (src[i] === "\\") i++; i++; }
      i++; continue;
    }
    if (c === "/" && src[i + 1] === "/") { while (i < src.length && src[i] !== "\n") i++; continue; }
    if (c === "/" && src[i + 1] === "*") { i = src.indexOf("*/", i); if (i < 0) return null; i += 2; continue; }
    if (c === "(" || c === "{" || c === "[") depth++;
    else if (c === ")" || c === "}" || c === "]") {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);   // matching ) of the leading (
    }
    i++;
  }
  return null;
}
const SHIM = sliceIIFE(APP, "(function setupTouchDrag() {");
if (!SHIM) {
  console.error("FAIL: could not find the setupTouchDrag shim in preview-app.js");
  process.exit(1);
}

// ── The page: the real in-game skeleton, filled with an overflowing board ────
const page = (nOceans) => `<!doctype html><html><head><meta charset="utf-8">
<title>board scroll</title>
<style>${CSS}</style>
<style>
  /* Stand-ins for the parts of the real screen this test does not build, kept
     at their real heights so the board gets a realistic slice of the window. */
  #pv-notice, #pv-action-bar { height: 44px; flex-shrink: 0; background: #123; }
</style>
</head>
<body class="cc-device-mobile">
<div id="pv-game" style="display:flex; flex-direction:column; height:100vh;">
  <div id="pv-notice"></div>
  <div id="pv-turn-banner" class="their-turn">Waiting…</div>
  <div id="pv-guide-bar"></div>
  <div id="pv-table">
   <div id="pv-table-inner">
    <div id="pv-opponents"></div>
    <div id="pv-center-strip">
      <div id="pv-pool-area">
        <div class="pv-section-label">The Pool</div>
        <div id="pv-pool-wrap"><div class="pv-pool-card" id="pool-card" draggable="true"
             style="width:84px;height:118px;background:#256;"></div></div>
      </div>
    </div>
    <div id="pv-my-board"></div>
   </div>
   <div id="pv-board-scroll-dock">
     <button type="button" id="pv-board-scroll-btn">&#9660; More oceans below</button>
   </div>
  </div>
  <div id="pv-action-bar"></div>
  <div id="pv-hand-zone"><div id="pv-hand"><div class="pv-hand-card" id="hand-card" draggable="true"
       style="width:98px;height:138px;background:#265;"></div></div></div>
</div>
<div id="pv-board-focus">
  <div id="pv-board-focus-label">Player's Board</div>
  <div id="pv-board-focus-content"></div>
</div>
<div id="out">RUNNING</div>
<script>
// Build ${nOceans} ocean hubs exactly the way renderMyBoard() does: four lanes
// (one card each) around a .pv-ocean-face, cards draggable like a movable animal.
(function buildBoard() {
  var board = document.getElementById("pv-my-board");
  for (var i = 0; i < ${nOceans}; i++) {
    var hub = document.createElement("div");
    hub.className = "pv-ocean-hub";
    hub.dataset.oceanUid = String(200 + i);
    ["up","down","left","right"].forEach(function (dir) {
      var lane = document.createElement("div");
      lane.className = "pv-lane-" + dir;
      var card = document.createElement("div");
      card.className = "pv-board-card movable";
      card.setAttribute("draggable", "true");
      card.dataset.faceUid = String(1000 + i);
      lane.appendChild(card);
      hub.appendChild(lane);
    });
    var center = document.createElement("div"); center.className = "pv-ocean-center";
    var face = document.createElement("div"); face.className = "pv-ocean-face";
    center.appendChild(face); hub.appendChild(center);
    board.appendChild(hub);
  }
})();
</script>
<script>${SHIM}()</script>
<script>
var TOL = 2;                 // px slop on edge comparisons
var results = [];
function pass(m) { results.push("PASS " + m); }
function fail(m) { results.push("FAIL " + m); }
function ok(cond, m) { cond ? pass(m) : fail(m); }

var table = document.getElementById("pv-table");
var board = document.getElementById("pv-my-board");
function hubs() { return board.querySelectorAll(".pv-ocean-hub"); }
function lastHub() { var h = hubs(); return h[h.length - 1]; }
function firstHub() { return hubs()[0]; }

// Every vertical scroller between an element and <body>.
function scrollers(from) {
  var out = [], el = from;
  while (el && el !== document.body) {
    var oy = getComputedStyle(el).overflowY;
    if (oy === "auto" || oy === "scroll") out.push(el);
    el = el.parentElement;
  }
  return out;
}
function liveScrollers(from) {
  return scrollers(from).filter(function (s) { return s.scrollHeight > s.clientHeight + 1; });
}
function scrollAll(from, to) {
  scrollers(from).concat([document.scrollingElement]).forEach(function (s) {
    if (s) s.scrollTop = to;
  });
}
// Bottom edge of the area the player can actually see the board through.
function viewBottom() {
  return Math.min(window.innerHeight, table.getBoundingClientRect().bottom);
}
function viewTop() {
  return Math.max(0, table.getBoundingClientRect().top);
}

function layoutChecks(label) {
  scrollAll(board, 0);
  var last = lastHub();
  var overflowing = last.getBoundingClientRect().bottom > viewBottom() + TOL;
  ok(overflowing, label + ": setup, a board of " + hubs().length +
     " oceans really does overflow the visible area (last ocean bottom " +
     last.getBoundingClientRect().bottom.toFixed(0) + " vs view bottom " + viewBottom().toFixed(0) + ")");
  if (!overflowing) return;

  // 1. the bottom of the last ocean must be reachable
  scrollAll(board, 1e6);
  var b = lastHub().getBoundingClientRect().bottom;
  ok(b <= viewBottom() + TOL, label + ": the BOTTOM of the last ocean is reachable by scrolling" +
     " (bottom " + b.toFixed(0) + " vs view bottom " + viewBottom().toFixed(0) + ")");

  // 2. and you must be able to get back up to the first one
  scrollAll(board, 0);
  var t = firstHub().getBoundingClientRect().top;
  ok(t >= viewTop() - TOL, label + ": scrolling back up reaches the FIRST ocean" +
     " (top " + t.toFixed(0) + " vs view top " + viewTop().toFixed(0) + ")");

  // 3. exactly one scroller: nested scrollers make a finger swipe scroll the
  //    wrong box and leave content stranded.
  var live = liveScrollers(board);
  ok(live.length === 1, label + ": exactly ONE vertical scroller for the board, found " +
     live.length + " [" + live.map(function (s) { return "#" + (s.id || s.className); }).join(", ") + "]");

  // 4. showing the pill must never take the overflow away again, it may only
  //    add room. Otherwise .pv-can-scroll flips the very test that sets it and
  //    the pill blinks in and out on every render.
  var dock = document.getElementById("pv-board-scroll-dock");
  var before = table.scrollHeight - table.clientHeight;
  table.classList.add("pv-can-scroll");
  var shown = getComputedStyle(dock).display !== "none";
  var after = table.scrollHeight - table.clientHeight;
  ok(shown && after >= before, label + ': the "more oceans" pill shows and only ADDS scroll room' +
     " (" + before + " -> " + after + "px, dock display " + getComputedStyle(dock).display + ")");

  // 5. and the pill must sit on empty water, not on top of the last oceans
  var btn = document.getElementById("pv-board-scroll-btn");
  scrollAll(board, 1e6);
  var pill = btn.getBoundingClientRect();
  var covered = Array.prototype.slice.call(hubs()).filter(function (h) {
    var r = h.getBoundingClientRect();
    return r.left < pill.right && r.right > pill.left && r.top < pill.bottom && r.bottom > pill.top;
  });
  ok(covered.length === 0, label + ": scrolled to the bottom, the pill covers no ocean (" +
     covered.length + " overlapped)");
  table.classList.remove("pv-can-scroll");
}

// Tapping another player's seat opens their whole board in #pv-board-focus.
// A big board has to be scrollable end to end there too.
function focusChecks(label) {
  var overlay = document.getElementById("pv-board-focus");
  var content = document.getElementById("pv-board-focus-content");
  var ro = document.createElement("div");
  ro.className = "pv-ro-board";
  // renderReadOnlyBoard() builds the same hubs, just with no drag handlers.
  Array.prototype.slice.call(document.querySelectorAll("#pv-my-board .pv-ocean-hub"))
    .forEach(function (h) {
      var c = h.cloneNode(true);
      c.querySelectorAll("[draggable]").forEach(function (d) { d.removeAttribute("draggable"); });
      ro.appendChild(c);
    });
  content.appendChild(ro);
  overlay.classList.add("open");

  function bottomOfView() { return Math.min(window.innerHeight, overlay.getBoundingClientRect().bottom); }
  var last = ro.querySelectorAll(".pv-ocean-hub");
  last = last[last.length - 1];
  var overflowing = last.getBoundingClientRect().bottom > bottomOfView() + TOL;
  ok(overflowing, label + ": setup, another player's board of " + ro.children.length +
     " oceans overflows the focus overlay");
  if (overflowing) {
    scrollAll(last, 1e6);
    var b = last.getBoundingClientRect().bottom;
    ok(b <= bottomOfView() + TOL, label + ": the BOTTOM of another player's oceans is reachable" +
       " (bottom " + b.toFixed(0) + " vs view bottom " + bottomOfView().toFixed(0) + ")");
  }
  overlay.classList.remove("open");
  content.innerHTML = "";
}

// ── gesture half ────────────────────────────────────────────────────────────
var dragStarts = 0;
document.addEventListener("dragstart", function () { dragStarts++; }, true);

function touch(type, el, x, y) {
  var t = new Touch({ identifier: 7, target: el, clientX: x, clientY: y, pageX: x, pageY: y });
  var list = (type === "touchend" || type === "touchcancel") ? [] : [t];
  el.dispatchEvent(new TouchEvent(type, {
    bubbles: true, cancelable: true, touches: list, targetTouches: list, changedTouches: [t],
  }));
}
function swipe(el, dx, dy, opts) {
  opts = opts || {};
  var r = el.getBoundingClientRect();
  var x = r.left + r.width / 2, y = r.top + r.height / 2;
  dragStarts = 0;
  var before = table.scrollTop;
  touch("touchstart", el, x, y);
  return new Promise(function (res) {
    setTimeout(function () {
      for (var i = 1; i <= 4; i++) touch("touchmove", el, x + dx * i / 4, y + dy * i / 4);
      touch("touchend", el, x + dx, y + dy);
      res({ drags: dragStarts, scrolled: table.scrollTop - before });
    }, opts.holdMs || 0);
  });
}
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

async function gestureChecks() {
  // A card low in the board, with plenty of room to scroll down.
  scrollAll(board, 0);
  var card = hubs()[2].querySelector(".pv-board-card");

  var a = await swipe(card, 0, -90);
  ok(a.scrolled > 30 && a.drags === 0,
     "mobile: a one-finger swipe UP on an ocean card scrolls the board down (moved " +
     a.scrolled.toFixed(0) + "px, " + a.drags + " drags started)");

  await sleep(60);
  var b = await swipe(card, 0, 90);
  ok(b.scrolled < -30 && b.drags === 0,
     "mobile: swiping back DOWN scrolls the board back up (moved " + b.scrolled.toFixed(0) + "px)");

  await sleep(60);
  scrollAll(board, 0);
  var c = await swipe(card, 0, -90, { holdMs: 420 });
  ok(c.drags === 1 && Math.abs(c.scrolled) < 3,
     "mobile: press-and-HOLD then move still starts a real card drag (" + c.drags +
     " drags, board moved " + c.scrolled.toFixed(0) + "px)");

  await sleep(60);
  scrollAll(board, 0);
  var d = await swipe(card, 90, 0);
  ok(d.drags === 1, "mobile: a HORIZONTAL swipe on an ocean card still drags instantly (" + d.drags + " drags)");

  await sleep(60);
  var e = await swipe(document.getElementById("pool-card"), 0, 80);
  ok(e.drags === 1, "mobile: dragging a POOL card down to the hand is still instant (" + e.drags + " drags)");

  await sleep(60);
  var f = await swipe(document.getElementById("hand-card"), 0, -80);
  ok(f.drags === 1, "mobile: dragging a HAND card up to the board is still instant (" + f.drags + " drags)");

  // A board that fits needs no scrolling, so nothing may change there.
  var keep = hubs()[0];
  var all = Array.prototype.slice.call(hubs());
  all.slice(1).forEach(function (h) { h.remove(); });
  await sleep(60);
  var g = await swipe(keep.querySelector(".pv-board-card"), 0, -80);
  ok(g.drags === 1, "mobile: with a board that FITS, a vertical swipe drags instantly as before (" +
     g.drags + " drags)");
}

async function run() {
  try {
    layoutChecks("layout " + window.innerWidth + "x" + window.innerHeight);
    focusChecks("focus " + window.innerWidth + "x" + window.innerHeight);
    await gestureChecks();
  } catch (err) {
    fail("exception: " + (err && err.stack || err));
  }
  document.getElementById("out").textContent = results.join("\\n");
}
run();
</script>
</body></html>`;

function runChrome(width, height, nOceans) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-boardscroll-"));
  const file = path.join(tmp, "board.html");
  fs.writeFileSync(file, page(nOceans));
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
  return report.split("\n").map(l => l.replace(/^(PASS|FAIL) /, `$1 [${width}x${height}] `));
}

const lines = [];
// Phone in the game's own zoomed-out viewport (390px device / 1.5 = 585 CSS px
// wide, 844 / 1.5 -> 1266 CSS px tall), then a laptop window. Ocean counts that
// a real game reaches (the deck holds far more oceans than this).
lines.push(...runChrome(585, 1266, 10));
lines.push(...runChrome(1440, 860, 14));

// The CSS pairing behind the bug: whatever ends up owning board overflow, the
// board must never be left clipped with no scroller at 100% zoom.
const tableRule = (() => {
  const i = CSS.indexOf("#pv-table {");
  return i < 0 ? "" : CSS.slice(i, CSS.indexOf("}", i));
})();
if (/overflow\s*:\s*hidden/.test(tableRule)) {
  lines.push("FAIL preview.css: #pv-table is overflow:hidden, a board taller than the table is clipped away");
} else if (/overflow(-y)?\s*:\s*(auto|scroll)/.test(tableRule)) {
  lines.push("PASS preview.css: #pv-table scrolls vertically at every board zoom");
} else {
  lines.push("FAIL preview.css: #pv-table declares no vertical overflow behaviour");
}

console.log(lines.join("\n"));
const failed = lines.filter(l => l.startsWith("FAIL"));
console.log(`\n${lines.filter(l => l.startsWith("PASS")).length} passed, ${failed.length} failed`);
process.exit(failed.length ? 1 : 0);
