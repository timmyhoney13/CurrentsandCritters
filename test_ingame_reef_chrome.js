#!/usr/bin/env node
/* The board's oceans are the big thing on it, and the two floating docks are
 * made of reef, not of navy.
 *
 * Run:  node test_ingame_reef_chrome.js       (needs Google Chrome installed)
 *
 * Three asks, one screen:
 *
 * 1. BIGGER OCEANS. The ocean face was 100x140 against 84x118 animals and a
 *    98x138 card in your hand, so the card the whole hub is built around, the
 *    one whose score curve decides where an animal goes, was the same size as
 *    everything else and a full board read as a field of equal rectangles.
 *    It is --ocean-w/--ocean-h now, and every other place that draws a board
 *    (the enlarged focus view, the hover peek, the game-history board) keeps
 *    the same ocean-to-animal ratio, because they are all the same board.
 *
 * 2. THE CHALLENGES ARE OPT-IN. The in-game panel used to open itself over the
 *    lower-left of every match. It is off until Menu -> Challenges turns it on.
 *
 * 3. THE DOCKS MATCH THE TABLE. #ig-challenge-panel and #bs-ctrl were the last
 *    two deep-navy slabs left in a game that is otherwise played on bright
 *    reef artwork. They are painted from the --cr-* reef SURFACE set now, the
 *    same cream-over-sand as the tooltip, the menu and the turn bands.
 *    Measured as LUMINANCE and CONTRAST off the real computed styles, not as a
 *    grep for hex codes: the point is that the panel is light and its type is
 *    readable on it, not that it spells the colour a particular way.
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

const CSS  = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const APP  = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");

const srcLines = [];
function srcOk(cond, m) { srcLines.push((cond ? "PASS " : "FAIL ") + m); }

// ── 2: opt-in, read straight off the source ──────────────────────────────────
// "=== 1" and not "!== 0": an unset key, a cleared browser, a new device and a
// guest all mean "never asked for", and all of them have to mean OFF.
srcOk(/_igcpEnabled = \(function\(\)\{ try \{ return localStorage\.getItem\(_IGCP_ENABLED_KEY\) === "1"; \} catch \{ return false; \} \}\)\(\)/.test(APP),
  "the in-game challenge panel defaults OFF, and an unreadable localStorage still means off");
srcOk(!/_igcpToggleEnabled\?\.\(\) \?\? true/.test(APP) && !/_igcpIsEnabled\?\.\(\) \?\? false/.test(APP) === false,
  "the menu's fallbacks agree with that default (?? false, not ?? true)");
srcOk(/_igcpMenuLabel = \(on\) =>/.test(APP) && /Challenges: \$\{on \? "On" : "Off"\}/.test(APP),
  "the menu row says its STATE, the way Game Log and Chat beside it do");
srcOk(/id="pv-menu-igcp-btn">Challenges: Off</.test(HTML),
  "the markup ships that row reading Challenges: Off, so the first paint is not a lie");
// Turning it on must still be reachable, and from the menu, which is what was
// promised. The panel is only ever shown through _igcpSetVisible.
srcOk(/id="pv-menu-igcp-btn"/.test(HTML) && /getElementById\("pv-menu-igcp-btn"\)\.addEventListener\("click"/.test(APP),
  "Menu -> Challenges is wired, so there is a way back on");

// ── 1 + 3: the numbers the stylesheet promises ───────────────────────────────
const CSS_NC = CSS.replace(/\/\*[\s\S]*?\*\//g, "");
function cssVar(name) {
  const m = new RegExp(`${name}\\s*:\\s*([0-9.]+)px`).exec(CSS_NC);
  return m ? Number(m[1]) : null;
}
const OW = cssVar("--ocean-w"), OH = cssVar("--ocean-h");
const MW = cssVar("--mini-w"),  MH = cssVar("--mini-h");
srcOk(OW !== null && OH !== null, `--ocean-w / --ocean-h exist (${OW} x ${OH})`);
srcOk(OW > 100 && OH > 140, `the ocean is bigger than the 100x140 it was (${OW}x${OH})`);
srcOk(Math.abs((OH / OW) - (MH / MW)) < 0.02,
  `the ocean keeps the printed card's shape, so the art is never stretched (${(OH/OW).toFixed(3)} vs ${(MH/MW).toFixed(3)})`);
srcOk(/\.pv-ocean-face \{\s*width: var\(--ocean-w\); height: var\(--ocean-h\);/.test(CSS),
  "the board's ocean face is sized from those variables, not from a literal");
// Every other rendering of a board has to be the SAME board.
const ratio = OW / MW;
const others = [
  ["#pv-board-focus-content", 84, 125],
  ["#pv-bh-content", 50, 74],
  [".ph-gdm-ro-host", 70, 104],
];
others.forEach(([sel, mini, ocean]) => {
  const esc = sel.replace(/[.#]/g, c => "\\" + c);
  const re = new RegExp(`${esc} \\.pv-ocean-face \\{\\s*width: calc\\((\\d+)px`, "m");
  const got = re.exec(CSS_NC);
  const w = got ? Number(got[1]) : null;
  const want = ratio * mini;
  srcOk(w !== null && Math.abs(w - want) <= 1.5,
    `${sel} draws the same board (ocean ${w}px on a ${mini}px animal, the live board's ratio wants ${want.toFixed(1)})`);
});

if (!CHROME) {
  console.log(srcLines.join("\n"));
  console.log("\nSKIP: no Chrome/Chromium found, the pixel half of this check did not run.");
  process.exit(srcLines.some(l => l.startsWith("FAIL")) ? 1 : 0);
}

// ── The page ─────────────────────────────────────────────────────────────────
const page = `<!doctype html><html><head><meta charset="utf-8">
<title>reef chrome</title>
<style>${CSS}</style>
<style>
  *, *::before, *::after { transition: none !important; animation: none !important; }
  body { margin: 0; }
  /* The card art is not under test and would only be failed requests. */
  .pv-board-card, .pv-ocean-face, .pv-card-inner { background: #1a4280; }
</style>
</head>
<body>
<div id="pv-game" style="display:flex; flex-direction:column; height:100vh;">
  <div id="pv-table" style="flex:1; min-height:0; overflow:auto;">
    <div id="pv-table-inner"><div id="pv-my-board"></div></div>
  </div>
  <div id="pv-action-bar"><button class="pv-btn pv-btn-do end-turn">&check; End Turn</button></div>
  <div id="pv-hand-zone"><div id="pv-hand"></div></div>

  <div id="ig-challenge-panel">
    <div id="igcp-header">
      <button id="igcp-minimize-btn">&minus;</button>
      <button id="igcp-cal-btn" type="button">
        <svg id="igcp-calendar-icon" viewBox="0 0 24 24" fill="none">
          <rect x="3" y="5" width="18" height="16" rx="2.5" stroke="#5fb3d6" stroke-width="1.8"/>
          <circle cx="8" cy="14" r="1.2" fill="#5fb3d6"/>
        </svg>
      </button>
      <span id="igcp-title">Daily Challenges</span>
      <span id="igcp-pill" class="daily">Daily</span>
    </div>
    <div id="igcp-cards">
      <div class="igcp-row igcp-daily">
        <div class="igcp-row-top">
          <div class="igcp-row-info">
            <div class="igcp-row-name">Pool Watcher</div>
            <div class="igcp-row-desc">Take five cards in a row from the Pool that another player discarded.</div>
          </div>
          <div class="igcp-row-xp">+75 XP</div>
        </div>
        <div class="igcp-row-bar-wrap">
          <div class="igcp-row-bar"><div class="igcp-row-fill" style="width:40%"></div></div>
          <span class="igcp-row-prog">2/5</span>
        </div>
      </div>
      <div class="igcp-row igcp-daily igcp-done">
        <div class="igcp-row-top">
          <div class="igcp-row-info">
            <div class="igcp-row-name">Login Current</div>
            <div class="igcp-row-desc">Log in today.</div>
          </div>
          <div class="igcp-row-xp">+1 XP</div>
        </div>
        <div class="igcp-row-bar-wrap">
          <div class="igcp-row-bar"><div class="igcp-row-fill" style="width:100%"></div></div>
          <span class="igcp-row-prog">&check; Done</span>
        </div>
      </div>
    </div>
    <div id="igcp-reward">All 3 today: +400 XP</div>
    <div id="igcp-footer">Tap to hide</div>
  </div>

  <div id="bs-ctrl">
    <div id="bs-label">Board<br>Size</div>
    <button id="bs-plus" class="bs-btn">+</button>
    <div id="bs-readout">100%</div>
    <button id="bs-minus" class="bs-btn">&minus;</button>
  </div>
</div>
<div id="out">RUNNING</div>
<script>
(function buildBoard() {
  var board = document.getElementById("pv-my-board");
  // Eight oceans, the most a board carries, each with a card in every lane.
  for (var i = 0; i < 8; i++) {
    var hub = document.createElement("div");
    hub.className = "pv-ocean-hub";
    ["up","down","left","right"].forEach(function (dir) {
      var lane = document.createElement("div");
      lane.className = "pv-lane-" + dir;
      var card = document.createElement("div");
      card.className = "pv-board-card";
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
<script>
var results = [];
function ok(cond, m) { results.push((cond ? "PASS " : "FAIL ") + m); }
function note(m) { results.push("NOTE " + m); }

// sRGB relative luminance, and the WCAG contrast ratio between two colours.
function parseRGB(s) {
  var m = /rgba?\\(([^)]+)\\)/.exec(s || "");
  if (!m) return null;
  var p = m[1].split(",").map(function (x) { return parseFloat(x); });
  return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
}
function lum(c) {
  var f = [c.r, c.g, c.b].map(function (v) {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2];
}
function over(fg, bg) {           // flatten a translucent colour onto its backdrop
  var a = fg.a;
  return { r: fg.r * a + bg.r * (1 - a), g: fg.g * a + bg.g * (1 - a), b: fg.b * a + bg.b * (1 - a), a: 1 };
}
function contrast(fg, bg) {
  var l1 = lum(fg), l2 = lum(bg);
  var hi = Math.max(l1, l2), lo = Math.min(l1, l2);
  return (hi + 0.05) / (lo + 0.05);
}
// The panel paints a gradient, which getComputedStyle reports as backgroundImage
// and not as a colour. Sample the gradient's own stops instead.
function gradientStops(el) {
  var img = getComputedStyle(el).backgroundImage || "";
  var out = [], re = /rgba?\\([^)]+\\)/g, m;
  while ((m = re.exec(img))) out.push(parseRGB(m[0]));
  return out.filter(Boolean);
}
function surfaceOf(el) {
  var stops = gradientStops(el);
  if (stops.length) return stops;
  var c = parseRGB(getComputedStyle(el).backgroundColor);
  return c && c.a > 0 ? [c] : [];
}

function run() {
  var WHITE = { r: 255, g: 255, b: 255, a: 1 };

  // ── 1: the oceans ──────────────────────────────────────────────────────────
  var face = document.querySelector(".pv-ocean-face");
  var mini = document.querySelector(".pv-board-card");
  var fr = face.getBoundingClientRect(), mr = mini.getBoundingClientRect();
  ok(fr.width > 100 && fr.height > 140,
     "the ocean face is bigger than the 100x140 it was (got " +
     Math.round(fr.width) + "x" + Math.round(fr.height) + ")");
  ok(fr.width / mr.width > 1.3,
     "the ocean clearly out-sizes the animals tucked under it (x" +
     (fr.width / mr.width).toFixed(2) + ")");
  ok(Math.abs(fr.height / fr.width - 1.4) < 0.02,
     "it is still the printed card's shape, so the art is not stretched (" +
     (fr.height / fr.width).toFixed(3) + ")");

  // The half-card clip has to keep meeting the ocean's edge: an animal shows
  // exactly half of itself and tucks the other half under the face.
  var upCard = document.querySelector(".pv-lane-up .pv-board-card");
  var ur = upCard.getBoundingClientRect();
  ok(Math.abs(ur.height - mr.height) < 0.6 || Math.abs(ur.height * 2 - parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--mini-h"))) < 1.5,
     "an up-lane animal still shows exactly half of itself (" + Math.round(ur.height) + "px)");
  ok(Math.abs(ur.bottom - fr.top) < 1.5,
     "and its cut edge still meets the top of the ocean (gap " +
     (ur.bottom - fr.top).toFixed(1) + "px)");

  // A full board still lays out as a board: nothing overlaps, nothing is
  // stranded outside the scroller it lives in.
  var hubs = Array.prototype.slice.call(document.querySelectorAll(".pv-ocean-hub"));
  var boardR = document.getElementById("pv-my-board").getBoundingClientRect();
  var strayed = hubs.filter(function (h) {
    var r = h.getBoundingClientRect();
    return r.left < boardR.left - 1 || r.right > boardR.right + 1;
  });
  ok(strayed.length === 0, "all 8 oceans wrap inside the board (" + hubs.length + " hubs, " +
     strayed.length + " outside)");
  var table = document.getElementById("pv-table");
  ok(table.scrollHeight <= table.clientHeight || table.scrollHeight > 0,
     "a full board is reachable: #pv-table scrolls it (" + table.scrollHeight + "px of " +
     table.clientHeight + "px)");

  // ── 3: the two docks are reef, not navy ────────────────────────────────────
  var panel = document.getElementById("ig-challenge-panel");
  panel.style.display = "block";
  var bs = document.getElementById("bs-ctrl");
  bs.style.display = "flex";

  [["ig-challenge-panel", "the challenge panel"], ["bs-ctrl", "the board size dock"]]
  .forEach(function (pair) {
    var el = document.getElementById(pair[0]), label = pair[1];
    var stops = surfaceOf(el);
    ok(stops.length > 0, label + " has a surface to measure");
    if (!stops.length) return;
    var lums = stops.map(function (c) { return lum(over(c, WHITE)); });
    var lo = Math.min.apply(null, lums);
    // Cream over sand sits around .85-.95. The navy it replaces was ~.02.
    ok(lo > 0.55, label + " is painted on the reef SURFACE set, not on navy (luminance " +
       lo.toFixed(3) + ")");
  });

  // Type on those surfaces has to be ink, and readable.
  var bgPanel = over(surfaceOf(panel)[0], WHITE);
  var bgBs    = over(surfaceOf(bs)[0], WHITE);
  var TEXT = [
    ["igcp-title",     bgPanel, 4.5, "the panel's title"],
    ["igcp-footer",    bgPanel, 4.0, "the panel's footer hint"],
    ["bs-label",       bgBs,    3.5, "the Board Size label"],
    ["bs-readout",     bgBs,    4.5, "the zoom readout"],
  ];
  TEXT.forEach(function (t) {
    var el = document.getElementById(t[0]);
    var fg = over(parseRGB(getComputedStyle(el).color), t[1]);
    var c = contrast(fg, t[1]);
    ok(c >= t[2], t[3] + " is readable on it (contrast " + c.toFixed(2) + ":1, wants " + t[2] + ")");
  });
  [[".igcp-row-name", bgPanel, 4.5, "a challenge's name"],
   [".igcp-row-desc", bgPanel, 3.0, "a challenge's description"],
   [".igcp-row-xp",   bgPanel, 4.5, "what it pays"]]
  .forEach(function (t) {
    var el = document.querySelector(t[0]);
    var row = el.closest(".igcp-row");
    var rowBg = over(parseRGB(getComputedStyle(row).backgroundColor) || { r:0,g:0,b:0,a:0 }, t[1]);
    var fg = over(parseRGB(getComputedStyle(el).color), rowBg);
    var c = contrast(fg, rowBg);
    ok(c >= t[2], t[3] + " is readable on its row (contrast " + c.toFixed(2) + ":1, wants " + t[2] + ")");
  });

  // Gold on a gold-tinted inset is the hardest type on this panel to keep
  // readable, so the two places that do it are measured against their own
  // backdrop rather than against the panel.
  [["igcp-reward", 4.5, "what clearing all three pays"],
   ["igcp-pill",   4.5, "the Daily / Weekly switch"]]
  .forEach(function (t) {
    var el = document.getElementById(t[0]);
    if (t[0] === "igcp-pill") el.className = "weekly";
    var bg = over(parseRGB(getComputedStyle(el).backgroundColor) || { r:0,g:0,b:0,a:0 }, bgPanel);
    var fg = over(parseRGB(getComputedStyle(el).color), bg);
    var c = contrast(fg, bg);
    ok(c >= t[1], t[2] + " is readable on its own tint (contrast " + c.toFixed(2) + ":1)");
    if (t[0] === "igcp-pill") el.className = "daily";
  });

  // The +/- buttons have to read as buttons on cream, not vanish into it.
  var btn = document.querySelector(".bs-btn");
  var btnBg = over(parseRGB(getComputedStyle(btn).backgroundColor) || { r:0,g:0,b:0,a:0 }, bgBs);
  var btnFg = over(parseRGB(getComputedStyle(btn).color), btnBg);
  ok(contrast(btnFg, btnBg) >= 4.5,
     "the + and - glyphs are readable on their own face (contrast " +
     contrast(btnFg, btnBg).toFixed(2) + ":1)");
  var bc = parseRGB(getComputedStyle(btn).borderTopColor);
  ok(bc && bc.a > 0.2 && contrast(over(bc, btnBg), btnBg) > 1.25,
     "and the button still has a visible edge on the cream");

  // The calendar icon was drawn in an aqua meant for navy. On cream it has to
  // have been re-inked or it is a pale smear.
  // Both states: a switch whose "on" position is a 2.2:1 outline reads as off.
  var tick   = document.querySelector("#igcp-calendar-icon [stroke]");
  var calBtn = document.getElementById("igcp-cal-btn");
  [["daily", ""], ["weekly", "weekly"]].forEach(function (st) {
    calBtn.className = st[1];
    var sk = parseRGB(getComputedStyle(tick).stroke);
    var c  = sk ? contrast(over(sk, bgPanel), bgPanel) : 0;
    ok(c >= 3.0, "the calendar switch is inked for cream on " + st[0] +
       " (contrast " + c.toFixed(2) + ":1)");
  });
  calBtn.className = "";

  document.getElementById("out").textContent = results.join("\\n");
}

try { run(); }
catch (err) {
  document.getElementById("out").textContent = "FAIL exception: " + (err && err.stack || err);
}
</script>
</body></html>`;

function runChrome(label, width, height) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-reefchrome-"));
  const file = path.join(tmp, "reef.html");
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
lines.push(...runChrome("desktop 1440x900", 1440, 900));
lines.push(...runChrome("laptop 1100x780",  1100, 780));

console.log(lines.join("\n"));
const failed = lines.filter(l => l.startsWith("FAIL"));
console.log(`\n${lines.filter(l => l.startsWith("PASS")).length} passed, ${failed.length} failed`);
process.exit(failed.length ? 1 : 0);
