#!/usr/bin/env node
/* THE OCEANS SIT IN THE MIDDLE OF YOUR BOARD, AND THE SPOT WHERE AN OCEAN GOES
 * IS ALWAYS THERE.
 *
 * Run:  node test_ocean_center_dropzone.js      (needs Google Chrome installed)
 *
 * Reported as: "the oceans when you play them they stay in the middle of your
 * board, and even if you do not have an ocean down the drag-ocean zone is
 * still there."
 *
 * Two separate bugs, both measured in real screen pixels in headless Chrome
 * against the REAL preview.css and the REAL renderMyBoard() lifted out of
 * preview-app.js.
 *
 * 1. OFF-CENTRE OCEANS. #pv-my-board was a single `justify-content: center`
 *    flex row holding BOTH the "where an ocean goes" drop zone and the ocean
 *    hubs, so the two were centred together as one group and the oceans were
 *    pushed aside by half the zone. Measured before the fix on an 1180px
 *    board: every ocean sat 92.1px right of the middle, at 1, 2, 3 and 5
 *    oceans alike. Two things made that worse than a fixed offset:
 *      - the zone comes and goes with whose turn it is and with what is in
 *        hand, so the whole board slid 92px sideways and back as it appeared
 *        and vanished, mid-game, repeatedly;
 *      - once the oceans wrapped to a second row, row 1 (sharing its line with
 *        the zone) was centred 92px right while row 2 (not sharing) was
 *        centred on 0, so the board looked stepped.
 *    The zone also got STRETCHED by the default align-items to the height of
 *    the ocean beside it: the compact "Add another ocean" zone was rendering
 *    297px tall.
 *
 * 2. NO SPOT AT ALL ON AN EMPTY BOARD. The zone was drawn only when the server
 *    was currently offering a play_ocean action. legal_actions is populated for
 *    the ACTIVE seat only, so on anyone else's turn, and on your own turn
 *    holding no ocean card or unable to pay for the one you hold, #pv-my-board
 *    rendered completely empty: a player who had not played an ocean yet was
 *    looking at blank water with nothing at all to say where their first ocean
 *    goes.
 *
 * What this pins:
 *   A. Every ocean row is centred on the board's own axis, to within 1px, at
 *      1/2/3/5/8 oceans, with the zone up and with it gone, on desktop and
 *      phone widths. Playing an ocean or losing the zone never moves them.
 *   B. Multi-row boards agree: all rows share one centre axis.
 *   C. The zone is centred too, and is never taller than its own content.
 *   D. An empty board ALWAYS draws the zone, including with zero legal
 *      actions, and draws it idle then (no click/drop handlers).
 *   E. With oceans down, the zone is still interactive when play_ocean is
 *      legal, and the real drag-and-drop path still submits the right action.
 *   F. No hub ever strays outside the board box.
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
  console.log("SKIP: no Chrome/Chromium found: cannot measure the board layout.");
  process.exit(0);
}

const CSS = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const APP = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");

// ── Lift the REAL renderMyBoard() out of preview-app.js ─────────────────────
// preview-app.js is one 26k-line IIFE that needs auth, SSE and Firebase to
// boot, and renderMyBoard is private to it, so there is no way to call the
// shipped function in place. Slice the one function out by balanced braces and
// run it against stubs for what it closes over. Everything this test measures
// (which elements get built, into which container, with which classes and
// handlers) is inside the slice, so the slice is the thing that ships.
function grabFn(name) {
  const start = APP.indexOf(`\n  function ${name}(`);
  if (start < 0) throw new Error(`function ${name}() not found in preview-app.js`);
  let i = APP.indexOf("{", start);
  let depth = 0;
  for (let j = i; j < APP.length; j++) {
    const ch = APP[j];
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return APP.slice(start, j + 1); }
  }
  throw new Error(`unbalanced braces reading ${name}()`);
}

const RENDER_MY_BOARD = grabFn("renderMyBoard");
const CARD_HALF_POS   = grabFn("cardHalfPos");

// Sanity: the slice must still be the function we think it is, so a rename or
// a refactor fails loudly here instead of silently testing nothing.
for (const needle of ["pv-ocean-row", "pv-ocean-drop-zone", "first-ocean-drop", "canPlayOcean"]) {
  if (!RENDER_MY_BOARD.includes(needle)) {
    console.log(`FAIL: renderMyBoard() no longer mentions ${needle}: this test is testing the wrong code.`);
    process.exit(1);
  }
}

const PAGE = `<!doctype html><html><head><meta charset="utf-8"><title>board</title>
<style>${CSS}</style>
<style>
  /* The page chrome renderMyBoard does not build, sized the way preview.html
     lays it out, so #pv-my-board gets its real width and its real grid row. */
  html,body { margin:0; padding:0; }
  #pv-game { display:flex; flex-direction:column; height:100vh; }
  #pv-table { flex:1; min-height:0; }
  /* Card art never loads over file://, and these boxes are CSS-sized anyway;
     paint them so a stray element is visible in a screenshot. */
  .pv-ocean-face { background:#58a; }
  .pv-board-card { background:#2a6; }
</style></head><body>
<div id="pv-game">
  <div id="pv-table"><div id="pv-table-inner">
    <div id="pv-opponents"></div>
    <div id="pv-center-strip"></div>
    <div id="pv-my-board"></div>
  </div></div>
  <div id="pv-action-bar"></div>
  <div id="pv-hand-zone"><div id="pv-hand"></div></div>
</div>
<input type="checkbox" id="pv-star-toggle" style="display:none">
<div id="out">RUNNING</div>
<script>
window.onerror=function(m,f,l,c){document.getElementById("out").textContent="FAIL threw: "+m+" @line "+l;};
</script>
<script>
// ── Stubs for what renderMyBoard() closes over ───────────────────────────
var CALLS = { openCTP: 0, submitAction: [], toast: [], playOrConfirm: [], zoom: 0 };
function cl(el) { while (el.firstChild) el.removeChild(el.firstChild); }
function closeCTP() {}
function openCTP(title, acts, slotEl) { CALLS.openCTP++; }
function showToast(msg, kind) { CALLS.toast.push(String(msg)); }
function submitAction(act) { CALLS.submitAction.push(act); }
function _playOrConfirmStarSkip(act, actions) { CALLS.playOrConfirm.push(act); }
function openZoom() { CALLS.zoom++; }
function _blockedPlayMessage() { return "blocked"; }
function _clearActionPreview() {}
function applyMySkin() {}
function imagePathForUid(uid, size) { return "assets/cards/" + uid + ".png"; }
function updateBoardScrollAffordance() {}
var _pendingMoveAnim = null, _myBoardRenderKey = null, _prevBoardCards = new Set();
var _boardMoveDragSrc = null, _dropdownArmedPreview = false;
var _pvAnim = { _suppressFlipIn: false };
${CARD_HALF_POS}
${RENDER_MY_BOARD}

// ── Measurement ──────────────────────────────────────────────────────────
var results = [];
function ok(cond, m) { results.push((cond ? "PASS " : "FAIL ") + m); }
function note(m) { results.push("NOTE " + m); }

var board = document.getElementById("pv-my-board");

function render(me, actions, isMyTurn) {
  _myBoardRenderKey = null;          // defeat the render-skip key between cases
  renderMyBoard(me, actions, isMyTurn);
}
// A board payload shaped like the server's. Ocean uids 217..224 are the eight
// real ocean cards; face uids 101..188 are the left/right animal halves
// (cardHalfPos decides which half of the art shows).
function boardOf(n, cardsPerLane) {
  var out = [];
  for (var i = 0; i < n; i++) {
    var o = { ocean_uid: 1000 + i, ocean: { uid: 217 + (i % 8), name: "Ocean " + i, text: "" },
              up: [], down: [], left: [], right: [] };
    if (cardsPerLane) {
      ["up", "down", "left", "right"].forEach(function (dir, d) {
        for (var k = 0; k < cardsPerLane; k++) {
          o[dir].push({ uid: 101 + d * 2 + k, face_uid: 101 + d * 2 + k, name: "Animal " + d + k });
        }
      });
    }
    out.push(o);
  }
  return out;
}
function oceanActions(n) {
  // One play_ocean per ocean card in hand, the shape the server sends.
  var out = [];
  for (var i = 0; i < n; i++) {
    out.push({ index: i, kind: "play_ocean", card_uid: 500 + i, face_uid: 500 + i, use_star: false });
  }
  return out;
}
function rowsOfFaces() {
  var br = board.getBoundingClientRect();
  var mid = br.left + br.width / 2;
  var buckets = {};
  Array.prototype.slice.call(board.querySelectorAll(".pv-ocean-face")).forEach(function (f) {
    var r = f.getBoundingClientRect();
    var k = Math.round(r.top);
    (buckets[k] = buckets[k] || []).push(r.left + r.width / 2);
  });
  return Object.keys(buckets).sort(function (a, b) { return a - b; }).map(function (k) {
    var cs = buckets[k];
    var rowMid = (Math.min.apply(null, cs) + Math.max.apply(null, cs)) / 2;
    return { top: +k, count: cs.length, offset: rowMid - mid };
  });
}
function dzRect() {
  var dz = document.getElementById("pv-ocean-drop-zone");
  if (!dz) return null;
  var r = dz.getBoundingClientRect(), br = board.getBoundingClientRect();
  return { el: dz, w: r.width, h: r.height, offset: (r.left + r.width / 2) - (br.left + br.width / 2) };
}

var TOL = 1.0;    // px: sub-pixel rounding only

// ── A. Oceans centred at every count, with the zone and without it ────────
[1, 2, 3, 5, 8].forEach(function (n) {
  [true, false].forEach(function (withZone) {
    render({ board: boardOf(n, 1) }, withZone ? oceanActions(2) : [], withZone);
    var rows = rowsOfFaces();
    var worst = 0;
    rows.forEach(function (r) { worst = Math.max(worst, Math.abs(r.offset)); });
    ok(rows.length > 0 && worst <= TOL,
       n + " ocean(s), zone " + (withZone ? "up" : "gone") + ": every row centred on the board (worst "
       + worst.toFixed(1) + "px off, " + rows.length + " row(s))");
  });
});

// ── B. The zone appearing or vanishing must not move the oceans ───────────
[1, 3, 5, 8].forEach(function (n) {
  render({ board: boardOf(n, 1) }, oceanActions(2), true);
  var withZone = Array.prototype.slice.call(board.querySelectorAll(".pv-ocean-face")).map(function (f) {
    var r = f.getBoundingClientRect(); return r.left + r.width / 2;
  });
  var hadZone = !!document.getElementById("pv-ocean-drop-zone");
  render({ board: boardOf(n, 1) }, [], false);
  var without = Array.prototype.slice.call(board.querySelectorAll(".pv-ocean-face")).map(function (f) {
    var r = f.getBoundingClientRect(); return r.left + r.width / 2;
  });
  var moved = 0;
  for (var i = 0; i < Math.min(withZone.length, without.length); i++) {
    moved = Math.max(moved, Math.abs(withZone[i] - without[i]));
  }
  ok(hadZone && withZone.length === n && without.length === n && moved <= TOL,
     n + " ocean(s): losing the drop zone moves no ocean sideways (worst " + moved.toFixed(1) + "px)");
});

// ── B2. Multi-row boards share one centre axis ────────────────────────────
render({ board: boardOf(8, 1) }, oceanActions(2), true);
var r8 = rowsOfFaces();
if (r8.length >= 2) {
  var spread = 0;
  r8.forEach(function (a) { r8.forEach(function (b) { spread = Math.max(spread, Math.abs(a.offset - b.offset)); }); });
  ok(spread <= TOL, "8 oceans wrap onto " + r8.length + " rows and all share one centre axis (spread "
     + spread.toFixed(1) + "px)");
} else {
  note("8 oceans fit on one row at this width: the multi-row axis check needs a narrower board");
}

// ── C. The zone is centred, and not stretched to an ocean's height ────────
render({ board: boardOf(2, 1) }, oceanActions(2), true);
var dz = dzRect();
ok(dz && Math.abs(dz.offset) <= TOL,
   "the compact zone is centred on the board (" + (dz ? dz.offset.toFixed(1) : "missing") + "px off)");
ok(dz && dz.h < 90,
   "the compact zone is its own height, not stretched to an ocean's (" + (dz ? Math.round(dz.h) : "?") + "px)");
var hubH = board.querySelector(".pv-ocean-hub").getBoundingClientRect().height;
ok(dz && dz.h < hubH * 0.6, "and is well under the hub beside it (" + Math.round(dz.h) + "px vs "
   + Math.round(hubH) + "px hub)");

render({ board: [] }, oceanActions(2), true);
var dz0 = dzRect();
ok(dz0 && Math.abs(dz0.offset) <= TOL,
   "the first-ocean zone is centred on an empty board (" + (dz0 ? dz0.offset.toFixed(1) : "missing") + "px off)");

// ── D. An empty board ALWAYS has the spot, even with nothing legal ────────
[
  ["not my turn",                 [],              false],
  ["my turn, nothing playable",   [],              true ],
  ["my turn, only other actions", [{ index: 0, kind: "draw" }, { index: 1, kind: "end_turn" }], true],
].forEach(function (c) {
  render({ board: [] }, c[1], c[2]);
  var z = document.getElementById("pv-ocean-drop-zone");
  ok(!!z, "empty board, " + c[0] + ": the spot where an ocean goes is still drawn");
  ok(!!z && z.classList.contains("first-ocean-drop"),
     "empty board, " + c[0] + ": and it is the big first-ocean size");
  ok(!!z && z.classList.contains("idle"),
     "empty board, " + c[0] + ": drawn idle, since there is nothing to accept yet");
  // Idle means no handlers: clicking must not open the picker, dropping must
  // not submit anything.
  var before = CALLS.openCTP;
  if (z) z.click();
  ok(CALLS.openCTP === before, "empty board, " + c[0] + ": clicking the idle spot opens no card picker");
  ok(!!z && z.textContent.indexOf("Your ocean goes here") >= 0,
     "empty board, " + c[0] + ": it says where the ocean goes");
  ok(!!z && z.textContent.indexOf("\\u2014") < 0 && z.textContent.indexOf("\\u2013") < 0,
     "empty board, " + c[0] + ": no em/en dash in the player-facing text");
});

// Empty board, play_ocean legal: the live invitation, not the idle spot.
render({ board: [] }, oceanActions(1), true);
var zLive = document.getElementById("pv-ocean-drop-zone");
ok(!!zLive && !zLive.classList.contains("idle"),
   "empty board with an ocean to play: the spot is live, not idle");
ok(!!zLive && zLive.textContent.indexOf("Start your ocean here") >= 0,
   "empty board with an ocean to play: it still says \\"Start your ocean here\\"");
var beforeCTP = CALLS.openCTP;
zLive.click();
ok(CALLS.openCTP === beforeCTP + 1, "and clicking it opens the ocean card picker");

// ── E. Oceans down: the zone is conditional, and the drop still works ─────
render({ board: boardOf(2, 1) }, [], false);
ok(!document.getElementById("pv-ocean-drop-zone"),
   "oceans down and no ocean to play: no \\"Add another ocean\\" clutter");
ok(board.querySelectorAll(".pv-ocean-hub").length === 2,
   "oceans down and no ocean to play: the oceans are still drawn");

render({ board: boardOf(2, 1) }, oceanActions(1), true);
var zAdd = document.getElementById("pv-ocean-drop-zone");
ok(!!zAdd && !zAdd.classList.contains("idle") && zAdd.textContent.indexOf("Add another ocean") >= 0,
   "oceans down with an ocean to play: the zone is back and live");
// Real drag-and-drop through the zone's own handlers.
var payload = JSON.stringify({ entryUid: 500, faceUid: 500 });
var dt = { data: { "application/x-fish-card": payload },
           getData: function (k) { return this.data[k] || ""; },
           setData: function (k, v) { this.data[k] = v; },
           dropEffect: "" };
var ev = new Event("dragover", { bubbles: true }); ev.dataTransfer = dt;
zAdd.dispatchEvent(ev);
ok(zAdd.classList.contains("drag-over"), "dragging an ocean over the zone lights it up");
var dropEv = new Event("drop", { bubbles: true }); dropEv.dataTransfer = dt;
CALLS.playOrConfirm.length = 0;
zAdd.dispatchEvent(dropEv);
ok(CALLS.playOrConfirm.length === 1 && CALLS.playOrConfirm[0].kind === "play_ocean",
   "dropping it on the zone plays that ocean (" + CALLS.playOrConfirm.length + " action)");
ok(!zAdd.classList.contains("drag-over"), "and the highlight clears on drop");

// ── F. Nothing strays outside the board box ───────────────────────────────
render({ board: boardOf(8, 2) }, oceanActions(2), true);
var br = board.getBoundingClientRect();
var strayed = Array.prototype.slice.call(board.querySelectorAll(".pv-ocean-hub")).filter(function (h) {
  var r = h.getBoundingClientRect();
  return r.left < br.left - 1 || r.right > br.right + 1;
});
ok(strayed.length === 0, "8 full oceans all wrap inside the board (" + strayed.length + " outside)");
var row = document.getElementById("pv-ocean-row");
ok(!!row && row.querySelectorAll(".pv-ocean-hub").length === 8,
   "every hub lives in #pv-ocean-row, the oceans' own centred line");
ok(!!row && !row.contains(document.getElementById("pv-ocean-drop-zone")),
   "and the drop zone is NOT in that row, so it cannot push the oceans aside");

// ── The empty row must not reserve height on a board with no oceans ───────
render({ board: [] }, oceanActions(1), true);
var emptyRow = document.getElementById("pv-ocean-row");
ok(!!emptyRow, "the ocean row is in the DOM even with no oceans (so the render-skip key holds)");
ok(!!emptyRow && emptyRow.getBoundingClientRect().height === 0,
   "but it reserves no height there (" + (emptyRow ? emptyRow.getBoundingClientRect().height : "?") + "px)");

document.getElementById("out").textContent = results.join("\\n");
</script></body></html>`;

// ── Run it at a desktop width and a phone width ─────────────────────────────
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ocean-centre-"));
const file = path.join(tmp, "board.html");
fs.writeFileSync(file, PAGE);

let checks = 0, failures = 0;
const WINDOWS = [
  { label: "desktop 1512x857", size: "1512,857" },
  { label: "phone 390x844",    size: "390,844"  },
];

for (const w of WINDOWS) {
  console.log(`\n── ${w.label} ───────────────────────────────────────────`);
  const dom = execFileSync(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    "--window-size=" + w.size,
    "--virtual-time-budget=4000",
    "--dump-dom", "file://" + file,
  ], { encoding: "utf8", maxBuffer: 1 << 28, stdio: ["ignore", "pipe", "ignore"] });

  const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
  if (!m) { console.log("  FAIL: the page never reported (no #out)."); failures++; continue; }
  const lines = m[1].replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&")
                    .replace(/&quot;/g, '"').split("\n").filter(Boolean);
  if (lines.length === 1 && lines[0] === "RUNNING") {
    console.log("  FAIL: the measurement script threw before reporting.");
    failures++; continue;
  }
  for (const line of lines) {
    if (line.startsWith("PASS ")) { checks++; console.log("  ✓ " + line.slice(5)); }
    else if (line.startsWith("FAIL ")) { checks++; failures++; console.log("  ✗ " + line.slice(5)); }
    else console.log("  · " + line.replace(/^NOTE /, ""));
  }
}

console.log(`\n${checks - failures}/${checks} checks passed.`);
if (failures) { console.log(`${failures} FAILED`); process.exit(1); }
console.log("The oceans stay in the middle, and the spot where an ocean goes is always there.");
