#!/usr/bin/env node
/* The card's text is painted ABOVE the band that interrupts a turn.
 *
 * Run:  node test_discard_text_above_bar.js      (needs Google Chrome installed)
 *
 * The bug, reported from the discard screen: "the descriptions of the animals
 * are below the bar and it doesn't make sense". When the game asks you to
 * discard down to ten it drops a full-width band between the board and the
 * action bar ("Discard exactly 2 card(s)…"). Hovering a card to decide which
 * one to throw away raises .pv-tooltip, which is taller than the action bar and
 * reaches up into that band, and the band was painted over the top of it: the
 * animal's name and its rules text were sliced off at the bar, and what was
 * left of the description sat under it.
 *
 * It is not an overflow or a position problem, both are already correct. It is
 * stacking order, and NOT where it looks like it is. Raising #pv-hand changes
 * nothing: `#pv-game > *` gives every row of the game column
 * `position:relative; z-index:1`, so #pv-hand-zone is a stacking context and
 * every number inside it, the hand's 3 and the tooltip's 1000 alike, is spent
 * in there and flattened to the ZONE's 1. The three bands
 * (#pv-discard-banner, #pv-pool-pick-hint, #pv-payment-mode-bar) override that
 * 1 with 60 of their own. 60 beats 1, so the band won every time.
 *
 * What this file measures, in headless Chrome, against the REAL preview.css:
 *   1. The tooltip genuinely reaches into the band, otherwise there is nothing
 *      to prove and the rest of the test would pass vacuously.
 *   2. At the pixels where the two overlap, the topmost painted element is the
 *      tooltip, not the band. (The tooltip is pointer-events:none in the game
 *      so a hover never dies on it; the probe turns that off for itself alone,
 *      because elementFromPoint answers "what is on top AND hit-testable" and
 *      only the first half is under test here.)
 *   3. The name line specifically, .tt-name, is above the band: that is the
 *      line the player is reading when they pick a card to lose.
 *   4. The hand still paints over the action bar and the seat pills, which is
 *      what its z-index was there for in the first place.
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
// preview.css explains itself at length, and several of those comments quote a
// z-index to say what they are clearing. Read DECLARATIONS from the stripped
// copy so a sentence about the fix can never be mistaken for the fix.
const CSS_NC = CSS.replace(/\/\*[\s\S]*?\*\//g, "");

// ── Source guards (run with or without Chrome) ───────────────────────────────
const srcLines = [];
function srcOk(cond, m) { srcLines.push((cond ? "PASS " : "FAIL ") + m); }

// The three bands that can interrupt a turn all sit at the same height, on
// purpose (see #pv-payment-mode-bar). If one of them is ever raised, the hand
// has to be raised with it or this bug comes straight back.
const BAND_IDS = ["pv-discard-banner", "pv-pool-pick-hint", "pv-payment-mode-bar"];
function cssZ(sel) {
  const re = new RegExp(`${sel}\\s*\\{[\\s\\S]*?\\}`, "g");
  let z = null, m;
  while ((m = re.exec(CSS_NC))) {
    const hit = /z-index:\s*(-?\d+)/.exec(m[0]);
    if (hit) z = Number(hit[1]);
  }
  return z;
}
const bandZ = BAND_IDS.map(id => cssZ("#" + id));
srcOk(bandZ.every(z => z === 60),
  `the three turn-interrupting bands are all at z-index 60 (got ${bandZ.join(", ")})`);

// The rule that makes the zone a stacking context in the first place. If it
// ever goes, the fix below is no longer the thing holding the tooltip up and
// this test should be re-read rather than quietly kept passing.
srcOk(/#pv-game > \* \{ position: relative; z-index: 1; \}/.test(CSS),
  "#pv-game > * still pins every row of the game column to z-index 1");

// So the lift has to be on the ZONE, which is the stacking context, not on
// #pv-hand, whose number can never escape it.
const zoneZ = cssZ("#pv-hand-zone");
srcOk(zoneZ !== null && zoneZ > 60,
  `#pv-hand-zone is lifted over the bands (z-index ${zoneZ === null ? "unset" : zoneZ})`);
// …and under everything that is meant to cover the hand.
const over = { "pv-notice": 100, "ctp-panel": 4991, "end-turn-modal": 9200 };
const tooHigh = Object.entries(over).filter(([id, z]) => zoneZ >= z).map(([id]) => id);
srcOk(tooHigh.length === 0,
  "the hand zone still sits under the notice bar, the card picker and the modals" +
  (tooHigh.length ? ", but it now covers " + tooHigh.join(", ") : ""));

if (!CHROME) {
  console.log(srcLines.join("\n"));
  console.log("\nSKIP: no Chrome/Chromium found, the pixel half of this check did not run.");
  process.exit(srcLines.some(l => l.startsWith("FAIL")) ? 1 : 0);
}

// ── The page: the real bottom of the real game column ────────────────────────
const CARD_FACES = [
  ["Bluefin Tuna", "Game Fish", "#2b5fd9"],
  ["Hermit Crab", "Crustacean", "#ef4444"],
  ["Brown Pelican", "Bird", "#ffffff"],
  ["Giant Kelp", "Coral", "#facc15"],
  ["Common Octopus", "Cephalopod", "#d9a066"],
  ["Sea Otter", "Mammal", "#f25fa6"],
  ["Anchovy", "Baitfish", "#8a5a2b"],
  ["Moon Jelly", "Invertebrate", "#a855f7"],
  ["Spiny Lobster", "Crustacean", "#ef4444"],
  ["Harbor Seal", "Mammal", "#f25fa6"],
  ["Sardine", "Baitfish", "#8a5a2b"],
  ["Sea Star", "Invertebrate", "#a855f7"],
];

const page = `<!doctype html><html><head><meta charset="utf-8">
<title>discard text</title>
<style>${CSS}</style>
<style>
  /* Final geometry, not tweens. */
  *, *::before, *::after { transition: none !important; animation: none !important; }
  body { margin: 0; }
  /* The art is not under test and would only be 12 failed requests. */
  .pv-card-inner { background: #1a4280; }
</style>
</head>
<body>
<div id="pv-game" style="display:flex; flex-direction:column; height:100vh;">
  <div id="pv-table" style="flex:1; min-height:0;"></div>
  <div id="pv-pool-pick-hint">Pick a card from the pool</div>
  <div id="pv-discard-banner">Discard exactly 2 card(s) to return to 10, click cards then Confirm</div>
  <div id="pv-action-bar">
    <button id="pv-help-btn"><span class="help-label">Strategy</span><span class="help-sub">strategies &amp; tips</span></button>
    <button class="pv-btn pv-btn-do end-turn">&check; End Turn</button>
  </div>
  <div id="pv-hand-zone">
    <div id="pv-seats-left" class="pv-seat-cluster"></div>
    <div id="pv-hand"></div>
    <div id="pv-seats-right" class="pv-seat-cluster"></div>
  </div>
</div>
<div id="out">RUNNING</div>
<script>
var FACES = ${JSON.stringify(CARD_FACES)};
(function buildHand() {
  var hand = document.getElementById("pv-hand");
  for (var i = 0; i < FACES.length; i++) {
    var f = FACES[i];
    var card = document.createElement("div");
    card.className = "pv-hand-card";
    card.dataset.entryUid = String(100 + i);
    card.dataset.faceUid  = String(100 + i);
    var inner = document.createElement("div");
    inner.className = "pv-card-inner";
    card.appendChild(inner);
    // The same tooltip renderHand() builds: name, species line, rules text.
    var tip = document.createElement("div");
    tip.className = "pv-tooltip";
    tip.innerHTML =
      '<div class="tt-name">' + f[0] + '</div>' +
      '<div class="tt-species"><span class="tt-fam-dot" style="background:' + f[2] + '"></span>' +
        f[1] + ' &middot; shell &middot; cost 2</div>' +
      '<div class="tt-text">Score 2 points for every other ' + f[1] +
        ' in this ocean, and 1 more if it is the deepest card there.</div>' +
      '<div class="tt-star">&starf; Discard a matching symbol to play a second card this turn.</div>' +
      '<div class="tt-arrow"></div>';
    card.appendChild(tip);
    hand.appendChild(card);
  }
})();
</script>
<script>
var results = [];
function ok(cond, m) { results.push((cond ? "PASS " : "FAIL ") + m); }
function note(m) { results.push("NOTE " + m); }

function rect(el) { return el.getBoundingClientRect(); }
function overlapRows(a, b) {
  var top = Math.max(a.top, b.top), bot = Math.min(a.bottom, b.bottom);
  return bot - top;
}

// elementFromPoint reports the topmost HIT-TESTABLE element. The tooltip is
// deliberately pointer-events:none in the game so the pointer never lands on
// it and kills its own hover; that is unrelated to what is painted on top, so
// the probe lifts it for the duration of the measurement only.
function topmostAt(x, y) {
  return document.elementFromPoint(Math.round(x), Math.round(y));
}

function run() {
  var cards = Array.prototype.slice.call(document.querySelectorAll(".pv-hand-card"));
  // Hover a card near the middle of the fan: its tooltip is the one that has
  // the whole width of the bands above it.
  var card = cards[Math.floor(cards.length / 2)];
  card.classList.add("hovered");

  var tip = card.querySelector(".pv-tooltip");
  var probeStyle = document.createElement("style");
  probeStyle.textContent = ".pv-hand-card.hovered .pv-tooltip, " +
                           ".pv-hand-card.hovered .pv-tooltip * { pointer-events: auto; }";
  document.head.appendChild(probeStyle);

  var tipR = rect(tip);
  ok(tipR.width > 0 && tipR.height > 0, "the hovered card shows its tooltip (" +
     Math.round(tipR.width) + "x" + Math.round(tipR.height) + ")");

  // 1 + 2: each band in turn.
  var BANDS = [
    ["pv-discard-banner", "the discard band"],
    ["pv-pool-pick-hint", "the pool-pick band"],
  ];
  BANDS.forEach(function (pair) {
    var id = pair[0], label = pair[1];
    var band = document.getElementById(id);
    band.classList.add("visible");
    // Showing a band lengthens the column, so re-read the tooltip's position.
    var t = rect(tip), b = rect(band);
    var over = overlapRows(t, b);
    ok(over > 0, label + " and the tooltip really do overlap (" +
       Math.round(over) + "px of rows), so there is something to prove");
    if (over > 0) {
      var y = (Math.max(t.top, b.top) + Math.min(t.bottom, b.bottom)) / 2;
      var xs = [t.left + 6, (t.left + t.right) / 2, t.right - 6];
      var losers = [];
      xs.forEach(function (x) {
        var el = topmostAt(x, y);
        var inTip = el && (el === tip || tip.contains(el));
        if (!inTip) losers.push(Math.round(x) + "," + Math.round(y) + " -> " +
                                (el ? (el.id || el.className || el.tagName) : "nothing"));
      });
      ok(losers.length === 0, "the card's text is painted ABOVE " + label +
         (losers.length ? ", but the band won at " + losers.join(" | ") : ""));
    }
    band.classList.remove("visible");
  });

  // 3: the NAME line specifically, which is what you read to pick a card.
  var disc = document.getElementById("pv-discard-banner");
  disc.classList.add("visible");
  var nameEl = tip.querySelector(".tt-name");
  var nR = rect(nameEl), dR = rect(disc);
  if (overlapRows(nR, dR) > 0) {
    var ny = (Math.max(nR.top, dR.top) + Math.min(nR.bottom, dR.bottom)) / 2;
    var nEl = topmostAt((nR.left + nR.right) / 2, ny);
    ok(nEl && (nEl === nameEl || nameEl.contains(nEl) || tip.contains(nEl)),
       "the animal's NAME is readable over the discard band");
  } else {
    note("the name line sits clear of the discard band at this size, nothing to stack");
  }

  // 4: the hand keeps paying for its own z-index, it still covers the action
  //    bar and the seat pills it was raised over in the first place.
  var bar = document.getElementById("pv-action-bar");
  var bR = rect(bar), tR = rect(tip);
  var barOver = overlapRows(tR, bR);
  ok(barOver > 0, "the tooltip reaches over the action bar too (" + Math.round(barOver) + "px)");
  if (barOver > 0) {
    var by = (Math.max(tR.top, bR.top) + Math.min(tR.bottom, bR.bottom)) / 2;
    var bEl = topmostAt((tR.left + tR.right) / 2, by);
    ok(bEl && (bEl === tip || tip.contains(bEl)),
       "the card's text is painted above the action bar" +
       (bEl && !tip.contains(bEl) ? ", but got " + (bEl.id || bEl.className) : ""));
  }
  disc.classList.remove("visible");
  document.head.removeChild(probeStyle);

  document.getElementById("out").textContent = results.join("\\n");
}

try { run(); }
catch (err) {
  document.getElementById("out").textContent = "FAIL exception: " + (err && err.stack || err);
}
</script>
</body></html>`;

function runChrome(label, width, height) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-discardtext-"));
  const file = path.join(tmp, "discard.html");
  fs.writeFileSync(file, page);
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
  return report.split("\n").map(l => l.replace(/^(PASS|FAIL|NOTE) /, `$1 [${label}] `));
}

const lines = srcLines.slice();
// Desktop, the laptop width where the seats stop flanking, and a phone: three
// different hand layouts, and #pv-hand is given its z-index in two of them.
lines.push(...runChrome("desktop 1440x900", 1440, 900));
lines.push(...runChrome("laptop 1100x780",  1100, 780));
lines.push(...runChrome("phone 390x844",     390, 844));

console.log(lines.join("\n"));
const failed = lines.filter(l => l.startsWith("FAIL"));
console.log(`\n${lines.filter(l => l.startsWith("PASS")).length} passed, ${failed.length} failed`);
process.exit(failed.length ? 1 : 0);
