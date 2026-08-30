#!/usr/bin/env node
/* Browser check for THE BOTTOM OF THE SCREEN EATING THE GAME.
 *
 * Run:  node test_mobile_nothing_cut_off.js      (needs Google Chrome installed)
 *
 * test_mobile_layout.js already covers the hand fan on a phone. This one
 * covers the thing that test could not see, because it measured one hand-built
 * skeleton at whatever size headless Chrome felt like giving it: the game
 * COLUMN overflowing the window, on every device shape and in every phase.
 *
 * The bug. #pv-game is a flex column and #pv-table is the only child that can
 * shrink (flex:1; min-height:0). The notice bar, the turn banner, the guide
 * bar, the two phase banners, the action bar and the hand zone are all
 * flex-shrink:0. So the moment they add up to more than the window, the table
 * collapses to zero and the rest keeps going straight off the bottom edge of a
 * page whose html/body is overflow:hidden: no scrollbar, no gesture, no zoom
 * gets it back. Measured before the fix, with the guide bar up (which is any
 * turn of yours) and a phase banner showing (the draw prompt, or the hand-limit
 * discard prompt, which is exactly when you MUST tap a card):
 *
 *   320x568   iPhone SE            hand zone cut off by 59-157px, ALL 8-12 cards
 *   360x740   Galaxy S8, discard   cut off by 26px
 *   844x390   phone held sideways  cut off by 23-99px, cards off screen
 *
 * On a phone in mobile mode the widened game viewport (ccGameViewport) hid
 * this: it multiplies the layout height by 1.5 as well as the width, which
 * bought enough slack to paper over the overflow. Every other way onto a small
 * screen got the raw size and the cut-off, so this runs BOTH: the raw device
 * size, and the widened size the same device gets in mobile mode.
 *
 * And the card zoom, which on a phone is the only way to read a card at all
 * (the fan squeezes one to ~50-65 device px, and the .pv-tooltip carrying the
 * rules text needs a :hover a finger never produces). #pv-zoom-inner is a flex
 * column capped at 88vh holding a 70vh image plus an unbounded info panel and
 * two gaps, with no overflow rule, so the rules text ran off the bottom; the
 * image was capped at 380px on a screen offering 526; and the two fixed nav
 * arrows sat on top of the artwork they page through.
 *
 * What is checked, in real pixels, against the REAL preview.css and the REAL
 * markup sliced out of preview.html:
 *   1. COLUMN, no in-flow child of #pv-game ends below the bottom of the window.
 *   2. BOARD,  #pv-table keeps a non-zero height, so the oceans stay reachable.
 *   3. HAND,   every card fully on screen, and nothing outside #pv-hand is
 *              painted over one (a seat pill on a card is the untappable-hand
 *              bug, and it comes back at any width too narrow to flank in).
 *   4. ZOOM,   the modal, its image, its rules text and its close button are
 *              all fully on screen, and the nav arrows do not cover the card.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("SKIP: no Chrome/Chromium found: cannot run the cut-off check.");
  process.exit(0);
}

const CSS  = fs.readFileSync(path.join(CLIENT, "css/preview.css"), "utf8");
const APP  = fs.readFileSync(path.join(CLIENT, "js/preview-app.js"), "utf8");
const HTML = fs.readFileSync(path.join(CLIENT, "preview.html"), "utf8");

// ── Pull the REAL markup out of preview.html ────────────────────────────────
// Hand-writing a stand-in skeleton is what let the original bug hide: the
// skeleton had four of the column's children and the real page has eight.
// Balanced-tag scan, so it survives edits inside the markup.
const VOID = new Set(["br","img","input","hr","meta","link","source","area","base","col","embed","param","track","wbr"]);
function sliceEl(src, idAttr) {
  const at = src.indexOf(idAttr);
  if (at < 0) return null;
  const start = src.lastIndexOf("<", at);
  const tag = /<(\/?)([a-zA-Z][\w-]*)([^>]*?)(\/?)>/g;
  tag.lastIndex = start;
  let depth = 0, m;
  while ((m = tag.exec(src))) {
    const [, closing, name, , selfclose] = m;
    if (closing) { if (--depth === 0) return src.slice(start, m.index + m[0].length); continue; }
    if (selfclose || VOID.has(name.toLowerCase())) continue;
    depth++;
  }
  return null;
}
const GAME_HTML = sliceEl(HTML, 'id="pv-game"');
const ZOOM_HTML = sliceEl(HTML, 'id="pv-zoom-modal"');
if (!GAME_HTML || !ZOOM_HTML) {
  console.error("FAIL: could not slice #pv-game / #pv-zoom-modal out of preview.html");
  process.exit(1);
}

// ── Pull the real layout functions out of preview-app.js ────────────────────
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
  const m = src.match(new RegExp("^\\s*const\\s+" + name + "\\s*=.*$", "m"));
  return m ? m[0].trim() : null;
}
const NEEDED_FNS = [
  "computeHandTransforms", "handRoomPx", "fitHandFan", "handFanUnderhang",
  "applyHandLayout", "_handHitTestIdx", "_handCornerRadius",
  "updateBottomDockOffset", "fitGameColumn",
];
const NEEDED_CONSTS = [
  "HAND_BASE_OVERLAP", "HAND_MIN_STEP", "HAND_EDGE_SLACK",
  "_FIT_HAND_FLOOR_H", "_FIT_TABLE_FLOOR", "_FIT_HINT_FLOOR",
  "_FIT_HAND_HARD_FLOOR", "_FIT_HINT_HARD_FLOOR", "_FIT_ADVISORY",
];
const parts = [];
for (const n of NEEDED_FNS) {
  const c = sliceFn(APP, n);
  if (!c) { console.error(`FAIL: could not find function ${n}() in preview-app.js`); process.exit(1); }
  parts.push(c);
}
for (const n of NEEDED_CONSTS) {
  const c = sliceConst(APP, n);
  if (!c) { console.error(`FAIL: could not find const ${n} in preview-app.js`); process.exit(1); }
  parts.push(c);
}
// The module-level bindings the slices read but do not declare.
const SLICED = `
let _handRadiusPx = null;
let _handCardEls  = [];
let _handHoverIdx = -1;
let _fitColKey    = "";
${parts.join("\n")}
`;

// ── The device shapes, at BOTH the raw size and the mobile-widened size ─────
// ccGameViewport lays a phone out at deviceWidth x 1.5 (x 1.9 on a tablet,
// keyed off the SHORT edge) and scales it back down to fit, so the same phone
// has two very different layout boxes depending on whether device detection
// said "mobile". Both have to hold.
// The shapes that actually bracket the problem: the smallest phone, a common
// one, the short-and-wide sideways case, a tablet and a laptop. Every one of
// the first three was measurably cut off before the fix.
const DEVICES = [
  ["iPhone SE",           320, 568],
  ["iPhone 12/13/14",     390, 844],
  ["Galaxy S8",           360, 740],
  ["phone sideways",      844, 390],
  ["iPad mini",           768, 1024],
  ["laptop",             1440, 773],
];
// A phase banner is flex-shrink:0 and appears exactly when you must tap a card.
// "discard" stands in for the pool-pick prompt too: they are the same banner
// height, and discard is the harder of the two because it also renders EVERY
// card in hand rather than capping at twelve.
const PHASES = ["plain", "discard"];

function widened(w, h) {
  const zo = Math.min(w, h) >= 700 ? 1.9 : 1.5;
  const W = Math.round(w * zo);
  return { W, H: Math.round(h / (w / W)) };
}

const CASES = [];
for (const [name, w, h] of DEVICES) {
  for (const phase of PHASES) {
    for (const nCards of [12]) {   // the full hand: the case that overflows
      CASES.push({ name: name + " raw", W: w, H: h, phase, nCards });
      const g = widened(w, h);
      CASES.push({ name: name + " mobile", W: g.W, H: g.H, phase, nCards });
    }
  }
}

// ── One frame: the real column, filled the way the renderers fill it ────────
const CARD_IMG = JSON.stringify("file://" + path.join(CLIENT, "card-back.png"));
const frameDoc = (c) => `<!doctype html><html><head><meta charset="utf-8"><style>${CSS}</style>
<style>
  /* Only geometry is under test, so the artwork is a flat colour. */
  html, body { margin: 0; padding: 0; overflow: hidden; }
  .pv-card-inner, .pv-pool-card, .pv-deck-face, #pv-zoom-img { background: #2a5f8a; }
  /* Stand-in for the real card artwork: #pv-zoom-img is sized from the
     picture's own intrinsic dimensions, and this harness cannot load one into
     a document.write()n iframe. 720x1008 is the size a card face really
     renders at, given here as a ratio so the production max-width/max-height/
     dvh caps are still the things doing the clamping. */
  #pv-zoom-img { width: 720px; height: auto; aspect-ratio: 720 / 1008; }
</style></head><body class="cc-device-mobile">
${GAME_HTML}
${ZOOM_HTML}
<div id="out">RUNNING</div>
<script>
(function build() {
  document.getElementById("pv-game").style.display = "flex";
  document.getElementById("pv-notice").innerHTML =
    '<button id="pv-menu-btn">&#9776; Menu</button><span class="room-badge">ROOM 4821</span>' +
    '<span>Turn 7 &middot; 129 cards in the deck</span>';
  var banner = document.getElementById("pv-turn-banner");
  banner.className = "my-turn"; banner.textContent = "YOUR TURN";
  // The guide bar is up on every turn of yours, which is every turn you tap a card on.
  var guide = document.getElementById("pv-guide-bar");
  guide.className = "visible";
  guide.innerHTML = '<div class="guide-step"><span class="gs done">1. Draw a card</span>' +
    '<span class="gs active">2. Play a card to an ocean</span>' +
    '<span class="gs">3. Pay its cost with symbols</span><span class="gs">4. End your turn</span></div>';
  ${c.phase === "discard" ? `var db = document.getElementById("pv-discard-banner");
      db.className = "visible";
      db.textContent = "Hand limit reached: choose 3 cards to discard, then press Confirm Discard";` : ""}
  ${c.phase === "poolpick" ? `var ph = document.getElementById("pv-pool-pick-hint");
      ph.className = "visible";
      ph.textContent = "Draw phase: take a card from the Pool, or draw from the Deck";` : ""}
  var pool = document.getElementById("pv-pool-wrap");
  for (var p = 0; p < 5; p++) {
    var pc = document.createElement("div"); pc.className = "pv-pool-card";
    pc.setAttribute("draggable", "true"); pool.appendChild(pc);
  }
  // renderPlayerSeats() always lays out 8 slots: 0-3 left, 4-7 right.
  var L = document.getElementById("pv-seats-left"), R = document.getElementById("pv-seats-right");
  for (var s = 0; s < 8; s++) {
    var seat = document.createElement("div");
    seat.className = "pv-seat" + (s >= 4 ? " pv-seat-empty" : "") + (s === 0 ? " active-turn" : "");
    var aw = document.createElement("div"); aw.className = "pv-seat-avatar-wrap"; aw.textContent = "@";
    var nm = document.createElement("div"); nm.className = "pv-seat-name";
    var pl = document.createElement("span"); pl.className = "pv-seat-plabel"; pl.textContent = "P" + (s + 1);
    nm.appendChild(pl); nm.appendChild(document.createTextNode(s >= 4 ? "Empty" : "Player " + (s + 1)));
    var sc = document.createElement("div");
    sc.className = "pv-seat-score" + (s >= 4 ? " pv-seat-score-empty" : "");
    sc.textContent = "12 pts";
    if (s === 0) { var me = document.createElement("div"); me.className = "pv-seat-me-badge"; me.textContent = "YOU"; seat.appendChild(me); }
    seat.appendChild(aw); seat.appendChild(nm); seat.appendChild(sc);
    (s < 4 ? L : R).appendChild(seat);
  }
  var hand = document.getElementById("pv-hand");
  for (var i = 0; i < ${c.nCards}; i++) {
    var card = document.createElement("div");
    card.className = "pv-hand-card"; card.setAttribute("draggable", "true");
    card.dataset.entryUid = String(500 + i); card.dataset.faceUid = String(500 + i);
    var inner = document.createElement("div"); inner.className = "pv-card-inner";
    var im = document.createElement("img"); im.alt = ""; inner.appendChild(im);
    card.appendChild(inner);
    var tip = document.createElement("div"); tip.className = "pv-tooltip";
    tip.innerHTML = '<div class="tt-name">Card</div>';
    card.appendChild(tip);
    hand.appendChild(card);
  }
  document.getElementById("ig-challenge-panel").style.display = "block";
  document.getElementById("bs-ctrl").style.display = "flex";
})();
<\/script>
<script>${SLICED}<\/script>
<script>
window.__measure = function () {
  var out = { fail: [], info: {} };
  var VW = document.documentElement.clientWidth;
  var VH = document.documentElement.clientHeight;
  var TOL = 2;
  out.info.vp = VW + "x" + VH;

  _handCardEls = Array.prototype.slice.call(document.querySelectorAll(".pv-hand-card[data-entry-uid]"));
  fitGameColumn(true);
  applyHandLayout(-1);
  updateBottomDockOffset();

  // 1. COLUMN: nothing in flow ends below the bottom of the window.
  var game = document.getElementById("pv-game");
  var table = document.getElementById("pv-table");
  var over = [];
  for (var k = 0; k < game.children.length; k++) {
    var kid = game.children[k];
    var cs = getComputedStyle(kid);
    if (cs.display === "none" || cs.position === "fixed" || cs.position === "absolute") continue;
    var r = kid.getBoundingClientRect();
    if (r.bottom > VH + TOL) over.push((kid.id || kid.className) + " ends " + Math.round(r.bottom - VH) + "px past the bottom");
  }
  if (over.length) out.fail.push("cut off: " + over.join("; "));

  // 2. BOARD: the oceans stay reachable.
  var th = Math.round(table.getBoundingClientRect().height);
  out.info.table = th;
  if (th < 1) out.fail.push("#pv-table collapsed to 0px: the board is unreachable");

  // 3. HAND: every card on screen, and nothing outside #pv-hand painted over one.
  var cards = _handCardEls, off = [], stolen = [];
  cards.forEach(function (cd, i) {
    var r = cd.getBoundingClientRect();
    if (r.left < -TOL || r.right > VW + TOL || r.top < -TOL || r.bottom > VH + TOL) off.push(i);
    var x = Math.max(1, Math.min(VW - 1, r.left + r.width / 2));
    var y = Math.max(1, Math.min(VH - 1, r.top + r.height / 2));
    var top = document.elementFromPoint(x, y);
    if (!(top && top.closest && top.closest("#pv-hand"))) {
      stolen.push(i + " under " + (top ? (top.id || top.className || top.tagName) : "nothing"));
    }
  });
  if (off.length) out.fail.push(off.length + " of " + cards.length + " hand cards off screen (" + off.join(",") + ")");
  if (stolen.length) out.fail.push(stolen.length + " hand cards covered: " + stolen.join("; "));
  out.info.cardW = cards.length ? Math.round(cards[0].getBoundingClientRect().width) : 0;

  // 4. ZOOM: the one place a card is readable. Longest real rules text.
  var modal = document.getElementById("pv-zoom-modal");
  document.getElementById("pv-zm-name").textContent = "Loggerhead Sea Turtle";
  document.getElementById("pv-zm-species").textContent = "Reptiles \\u00b7 Triangle \\u00b7 cost 3";
  document.getElementById("pv-zm-text").textContent =
    "When you play this card you may play any number of additional cards from " +
    "your hand into the same ocean this turn, paying each of their costs as " +
    "normal. This does not end your turn, and cards played this way still " +
    "trigger their own abilities in the order you play them.";
  var st = document.getElementById("pv-zm-star");
  st.style.display = "block";
  st.textContent = "\\u2605 Discard a Circle card to draw two cards from the deck.";
  var img = document.getElementById("pv-zoom-img");
  // A REAL card image, because #pv-zoom-img is sized from the picture's own
  // intrinsic dimensions (width/height are auto). A data-URI SVG has no
  // intrinsic size in headless Chrome and the element measures as its border.
  img.src = ${CARD_IMG};
  document.getElementById("pv-zoom-prev").style.display = "flex";
  document.getElementById("pv-zoom-next").style.display = "flex";
  modal.classList.add("open");

  var zoomBoxes = ["pv-zoom-inner", "pv-zoom-img", "pv-zoom-info", "pv-zoom-close"];
  var zoff = [];
  zoomBoxes.forEach(function (id) {
    var e = document.getElementById(id); if (!e) return;
    var r = e.getBoundingClientRect();
    if (r.bottom > VH + TOL || r.top < -TOL || r.left < -TOL || r.right > VW + TOL) {
      zoff.push("#" + id + " (" + Math.round(r.left) + "," + Math.round(r.top) + " to " +
                Math.round(r.right) + "," + Math.round(r.bottom) + ")");
    }
  });
  if (zoff.length) out.fail.push("zoom off screen: " + zoff.join("; "));

  // The arrows must not sit on the artwork they page through.
  var ir = document.getElementById("pv-zoom-img").getBoundingClientRect();
  ["pv-zoom-prev", "pv-zoom-next"].forEach(function (id) {
    var r = document.getElementById(id).getBoundingClientRect();
    if (r.left < ir.right - TOL && ir.left < r.right - TOL &&
        r.top < ir.bottom - TOL && ir.top < r.bottom - TOL) {
      out.fail.push("#" + id + " is painted on top of the card art");
    }
  });
  out.info.zoomW = Math.round(ir.width);
  // A zoom that is not bigger than the card in your hand is not a zoom.
  if (out.info.cardW && ir.width < out.info.cardW * 1.6) {
    out.fail.push("the zoomed card is only " + Math.round(ir.width) + "px wide vs " +
                  out.info.cardW + "px in the hand: not readably bigger");
  }
  modal.classList.remove("open");
  return out;
};
<\/script>
</body></html>`;

// ── Drive every case in one Chrome, each in its own exactly-sized iframe ────
// --window-size does NOT give you the viewport you asked for below ~500px:
// headless Chrome silently clamps it, so every phone case would quietly run at
// the wrong width. An iframe has its own viewport and its own media queries.
// contentDocument.write() keeps it same-origin; a file:// src would not be.
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-cutoff-"));
fs.writeFileSync(path.join(tmp, "docs.js"),
  "window.__DOCS=" + JSON.stringify(CASES.map(frameDoc)) + ";" +
  "window.__CASES=" + JSON.stringify(CASES) + ";");
fs.writeFileSync(path.join(tmp, "host.html"), `<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0"><div id="out">RUNNING</div><div id="frames"></div>
<script src="docs.js"><\/script>
<script>
(function () {
  var host = document.getElementById("frames");
  window.__CASES.forEach(function (c, i) {
    var f = document.createElement("iframe");
    f.id = "if" + i; f.style.border = "0"; f.style.display = "block";
    f.width = c.W; f.height = c.H; f.setAttribute("scrolling", "no");
    host.appendChild(f);
    var d = f.contentDocument; d.open(); d.write(window.__DOCS[i]); d.close();
  });
  setTimeout(function () {
    var res = [];
    window.__CASES.forEach(function (c, i) {
      var m;
      try { m = document.getElementById("if" + i).contentWindow.__measure(); }
      catch (e) { m = { fail: ["harness error: " + (e && e.message || e)], info: {} }; }
      res.push({ c: c, m: m });
    });
    document.getElementById("out").textContent = JSON.stringify(res);
  }, 2200);
})();
<\/script></body></html>`);

let dom;
try {
  dom = execFileSync(CHROME, [
    "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    "--window-size=1600,1200", "--virtual-time-budget=25000", "--dump-dom",
    "file://" + path.join(tmp, "host.html"),
  ], { encoding: "utf8", maxBuffer: 1 << 28, stdio: ["ignore", "pipe", "ignore"] });
} catch (e) {
  console.error("FAIL: could not run Chrome: " + (e && e.message));
  process.exit(1);
}
const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
if (!m || m[1].trim() === "RUNNING") {
  console.error("FAIL: the harness produced no measurements");
  process.exit(1);
}
const data = JSON.parse(m[1]
  .replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&"));

let passed = 0, failed = 0;
for (const { c, m } of data) {
  const label = `${c.name} ${c.phase}/${c.nCards}c ${m.info.vp || c.W + "x" + c.H}`;
  if (m.fail.length) {
    failed++;
    console.log(`FAIL ${label}: ${m.fail.join(" | ")}`);
  } else {
    passed++;
    console.log(`PASS ${label}: nothing cut off (board ${m.info.table}px, hand card ${m.info.cardW}px, zoom ${m.info.zoomW}px)`);
  }
}
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
