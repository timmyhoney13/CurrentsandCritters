#!/usr/bin/env node
/* Browser check for CLICKING A NAME / HOVERING A BOARD SHOWING IT AT THE RIGHT SIZE.
 *
 * Run:  node test_board_fit.js        (needs Google Chrome installed)
 *
 * The bug: "when you click on someones name in the game it zooms way far out".
 * Both the enlarged board (#pv-board-focus, opened by clicking a seat pill or
 * an opponent card) and the hover peek (#pv-board-hover) are a wrapping row of
 * ocean hubs whose size comes from the --focus-scale CSS variable. That scale
 * used to be a guess from the ocean count alone (_boardFocusScaleFor): eight
 * oceans meant 0.60, on any screen, so a board with room to spare was drawn at
 * a third of the size it could have been. The peek was worse: fixed 50px cards
 * inside a max-width:540px / max-height:72vh panel that is pointer-events:none,
 * so a board too big for it was simply cut off with a scrollbar nobody could
 * reach.
 *
 * Both now measure the laid-out board and binary-search the largest scale that
 * fits. "Fits perfectly" is two properties, and every check below measures both
 * in real screen pixels in headless Chrome, against the REAL preview.css, the
 * REAL overlay markup from preview.html and the REAL board renderer and fitting
 * code sliced out of preview-app.js:
 *
 *   WHOLE    nothing is cut off: the board is inside its box in both directions
 *            and neither the box nor the panel needs scrolling to see it all.
 *   FULL     it is as big as it can be: nudging the scale up by 6% overflows
 *            (or it is already at the ceiling, where the card art goes soft).
 *
 * Boards run from one bare ocean to twelve with long stacked lanes, on a
 * desktop window and a phone-sized one, and the enlarged board is checked
 * against the old fixed table to prove the "way far out" case really moved.
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
  console.error("Google Chrome not found, cannot measure the real layout.");
  process.exit(1);
}

const CSS  = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const APP  = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");

let failures = 0;
const ok   = (m) => console.log("  ✓ " + m);
const fail = (m) => { console.log("  ✗ " + m); failures++; };

// ── Pull the REAL renderer + fitting code out of preview-app.js ──────────────
// preview-app.js is one 37k-line module that needs the whole app to boot, so we
// evaluate the two self-contained regions this feature lives in. Their only
// outside dependencies are the three card helpers stubbed on the page.
function slice(startMarker, endMarker, what) {
  const s = APP.indexOf(startMarker), e = APP.indexOf(endMarker);
  if (s < 0 || e < 0 || e <= s) {
    console.error(`FAIL: could not slice ${what} out of preview-app.js, the markers moved.`);
    process.exit(1);
  }
  return APP.slice(s, e);
}
const SCALE_MARK = "  // Cards get smaller as more oceans are on the board so everything stays";
const RENDER = slice("  function renderReadOnlyBoard(player) {", SCALE_MARK, "renderReadOnlyBoard");
const FIT    = slice(SCALE_MARK, "  // ── End game overlay ──", "the board fitting code");
for (const fn of ["_fitBoardScale", "_refitBoardFocus", "openBoardFocus", "showBoardHover"]) {
  if (!FIT.includes("function " + fn)) {
    console.error(`FAIL: sliced region is missing ${fn}().`);
    process.exit(1);
  }
}

// ── The page: the real overlay markup + the real stylesheet ──────────────────
const overlayMarkup = (() => {
  const grab = (id) => {
    const i = HTML.indexOf(`<div id="${id}">`);
    if (i < 0) return "";
    const end = HTML.indexOf("\n</div>", i);
    return HTML.slice(i, end + "\n</div>".length);
  };
  return grab("pv-board-focus") + "\n" + grab("pv-board-hover");
})();
if (!overlayMarkup.includes("pv-bh-content") || !overlayMarkup.includes("pv-board-focus-content")) {
  console.error("FAIL: could not lift the overlay markup out of preview.html");
  process.exit(1);
}

const page = `<!doctype html><html><head><meta charset="utf-8">
<title>board fit</title>
<style>${CSS}</style>
<style>
  /* A stand-in for the seat pill / opponent card the peek anchors to, at about
     its real size and in about its real place (left rail, mid screen). */
  #seat { position: fixed; left: 24px; top: 180px; width: 132px; height: 96px; background: #234; }
</style>
</head>
<body>
<div id="seat"></div>
${overlayMarkup}
<div id="out"></div>
<script>
// The card helpers the renderer needs. Card ART is irrelevant here: every
// box is sized by CSS, so a 1x1 transparent pixel lays out identically to the
// real image and does not need 300 files on disk.
const PX = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
function imagePathForUid() { return PX; }
function cardHalfPos() { return "center"; }
function openZoom() {}
function cl(el) { while (el.firstChild) el.removeChild(el.firstChild); }
// Watching somebody play hangs their hand under the enlarged board and polls
// their cursor onto it. This suite measures the board a SEATED player opens, so
// watching is off and neither one is drawn. (test_spectator_hands.js covers the
// watcher's version, including that the board is fitted to what is left after
// the hand has taken its height.)
function isSpectating() { return false; }
let _spectatorViewingIdx = null, _spectatorRoomId = "", _spectatorToken = "";
function apiFetch() { return Promise.resolve({ ok: false, data: {} }); }
${RENDER}
${FIT}

// ── Board fixtures ──────────────────────────────────────────────────────────
let _uid = 100;
const card = () => ({ uid: ++_uid, face_uid: _uid, name: "Critter " + _uid, species: "Fish", text: "" });
const cards = (n) => Array.from({ length: n }, card);
// lanes: how many creatures hang off each ocean (stacked lanes are the case
// that used to overflow even when the ocean count said there was room).
function board(oceans, lanes) {
  return Array.from({ length: oceans }, () => ({
    ocean: { uid: ++_uid, name: "Ocean " + _uid, text: "" },
    up: cards(lanes), down: cards(lanes), left: cards(lanes), right: cards(lanes),
  }));
}
const player = (name, oceans, lanes) => ({ name, index: 2, board: board(oceans, lanes) });

// The table this replaced, so the report can say what actually changed.
function oldScaleFor(n) {
  if (n <= 1) return 1.65;
  if (n === 2) return 1.45;
  if (n === 3) return 1.25;
  if (n === 4) return 1.05;
  if (n === 5) return 0.90;
  if (n === 6) return 0.78;
  if (n === 7) return 0.68;
  return 0.60;
}

(async () => {
  const R = [];
  const rec = (name, pass, detail) => R.push((pass ? "PASS" : "FAIL") + " :: " + name + (detail ? " :: " + detail : ""));
  // setTimeout, not requestAnimationFrame: headless Chrome has no compositor
  // driving frames, so a rAF await never resolves there.
  const settle = () => new Promise(r => setTimeout(r, 30));
  const num = (v) => Math.round(v * 100) / 100;
  const scaleOf = (el) => parseFloat(getComputedStyle(el).getPropertyValue("--focus-scale")) || 0;

  const focus   = document.getElementById("pv-board-focus");
  const fLabel  = document.getElementById("pv-board-focus-label");
  const fContent= document.getElementById("pv-board-focus-content");
  const pop     = document.getElementById("pv-board-hover");
  const pContent= document.getElementById("pv-bh-content");
  const seat    = document.getElementById("seat");

  // "Would 6% more overflow?" — the FULL half of fitting perfectly. Applied to
  // the host, measured, then put back exactly as the app left it.
  function overflowsAt(host, scale, availW, availH) {
    const was = host.style.getPropertyValue("--focus-scale");
    host.style.setProperty("--focus-scale", String(scale));
    const over = host.scrollWidth > Math.floor(availW) || host.scrollHeight > Math.floor(availH);
    host.style.setProperty("--focus-scale", was);
    return over;
  }

  // The box the enlarged board is fitted into, read back from the live overlay.
  function focusBox() {
    const cs = getComputedStyle(focus);
    const padX = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
    const padY = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
    const ls = getComputedStyle(fLabel);
    const labelH = fLabel.getBoundingClientRect().height
                 + (parseFloat(ls.marginTop) || 0) + (parseFloat(ls.marginBottom) || 0);
    return { w: focus.clientWidth - padX, h: focus.clientHeight - padY - labelH };
  }

  const FOCUS_MAX = 2.4, PEEK_MAX = 1.30;

  // ── 1. CLICKING A NAME: the enlarged board ────────────────────────────────
  for (const [oceans, lanes] of [[1,0],[3,0],[3,3],[5,1],[8,0],[8,2],[12,2]]) {
    const p = player("Alice", oceans, lanes);
    openBoardFocus(p);
    await settle();
    const box = focusBox();
    const s = scaleOf(fContent);
    const w = fContent.scrollWidth, h = fContent.scrollHeight;
    const tag = oceans + " ocean" + (oceans === 1 ? "" : "s") + (lanes ? " x " + lanes + "-deep lanes" : "");

    rec("enlarged board is whole, " + tag,
        w <= Math.floor(box.w) && h <= Math.floor(box.h),
        "board " + num(w) + "x" + num(h) + " in " + num(box.w) + "x" + num(box.h) + ", scale " + num(s));
    rec("enlarged board fills the window, " + tag,
        s >= FOCUS_MAX - 0.001 || overflowsAt(fContent, s * 1.06, box.w, box.h),
        "scale " + num(s) + (s >= FOCUS_MAX - 0.001 ? " (at the ceiling)" : "")
          + ", uses " + Math.round(100 * Math.max(w / box.w, h / box.h)) + "% of the box");
    // The overlay itself must not need scrolling: that is what "cut off" looks
    // like to a player who does not notice the scrollbar.
    rec("enlarged board needs no scrolling, " + tag,
        focus.scrollHeight <= focus.clientHeight,
        "overlay " + focus.scrollHeight + " vs " + focus.clientHeight);
    // The regression this fixes is a board drawn small on a screen with room
    // to spare. On a phone the old table was too big the other way (it is what
    // forced the overlay to scroll), so only a roomy window has anything to say.
    if (oceans >= 3 && box.w >= 900) {
      rec("enlarged board is bigger than the old fixed table, " + tag,
          s > oldScaleFor(oceans) + 0.01,
          "was " + oldScaleFor(oceans) + ", now " + num(s));
    }
    closeBoardFocus();
  }

  // ── 2. HOVERING A BOARD: the peek ─────────────────────────────────────────
  for (const [oceans, lanes] of [[1,0],[3,0],[5,1],[8,0],[8,2],[12,2]]) {
    const p = player("Bob", oceans, lanes);
    showBoardHover(p, seat);
    await settle();
    const s = scaleOf(pContent);
    const r = pop.getBoundingClientRect();
    const sr = seat.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    const tag = oceans + " ocean" + (oceans === 1 ? "" : "s") + (lanes ? " x " + lanes + "-deep lanes" : "");

    rec("peek board is whole, " + tag,
        pContent.scrollWidth <= pContent.clientWidth &&
        pContent.scrollHeight <= pContent.clientHeight &&
        pop.scrollHeight <= pop.clientHeight && pop.scrollWidth <= pop.clientWidth,
        "content " + pContent.scrollWidth + "x" + pContent.scrollHeight
          + " in " + pContent.clientWidth + "x" + pContent.clientHeight + ", scale " + num(s));
    rec("peek is fully on screen, " + tag,
        r.left >= -0.5 && r.top >= -0.5 && r.right <= vw + 0.5 && r.bottom <= vh + 0.5,
        "peek " + num(r.left) + "," + num(r.top) + " " + num(r.width) + "x" + num(r.height) + " in " + vw + "x" + vh);
    rec("peek does not cover the board it is a peek at, " + tag,
        r.left >= sr.right - 0.5 || r.right <= sr.left + 0.5 || r.top >= sr.bottom - 0.5 || r.bottom <= sr.top + 0.5,
        "seat " + num(sr.left) + "-" + num(sr.right) + ", peek " + num(r.left) + "-" + num(r.right));
    rec("peek fills the room it has, " + tag,
        s >= PEEK_MAX - 0.001 ||
        overflowsAt(pContent, s * 1.06, pContent.clientWidth, pContent.clientHeight),
        "scale " + num(s) + (s >= PEEK_MAX - 0.001 ? " (at the ceiling)" : ""));
    hideBoardHover();
  }

  // ── 3. A board with nothing on it still reads as a board ──────────────────
  openBoardFocus({ name: "Cleo", index: 3, board: [] });
  await settle();
  rec("an empty board opens without overflowing",
      fContent.scrollHeight <= Math.floor(focusBox().h), String(fContent.scrollHeight));
  closeBoardFocus();

  // ── 4. Resizing the window re-fits the board that is already open ─────────
  {
    const p = player("Dag", 8, 1);
    openBoardFocus(p);
    await settle();
    const before = scaleOf(fContent);
    // Shrink the box the way a resize would, then fire the app's own handler.
    focus.style.paddingBottom = Math.round(window.innerHeight * 0.45) + "px";
    window.dispatchEvent(new Event("resize"));
    await settle();
    const after = scaleOf(fContent);
    const box = focusBox();
    rec("resizing re-fits the open board",
        after < before - 0.01 && fContent.scrollHeight <= Math.floor(box.h),
        "scale " + num(before) + " -> " + num(after));
    focus.style.paddingBottom = "";
    closeBoardFocus();
  }

  document.getElementById("out").textContent = R.join("\\n");
})();
</script>
</body></html>`;

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-fit-"));
const file = path.join(tmp, "fit.html");
fs.writeFileSync(file, page);

function run(w, h) {
  try {
    return execFileSync(CHROME, [
      "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      `--window-size=${w},${h}`, "--virtual-time-budget=20000",
      "--dump-dom", "file://" + file,
    ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 120000 });
  } catch (err) {
    console.error("Chrome failed to run:", err.message);
    process.exit(1);
  }
}

// Desktop and phone: the same board has to fit both, and the phone is where a
// board that is merely "small enough" stops being readable.
for (const [w, h, label] of [[1440, 900, "desktop 1440x900"], [390, 844, "phone 390x844"]]) {
  const dom = run(w, h);
  const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
  const lines = m ? m[1].split("\n").map(l => l.trim()).filter(Boolean) : [];
  console.log(`\nBOARD FIT — ${label} (headless Chrome, real preview.css)`);
  if (!lines.length) {
    fail("the page produced no results, the sliced code threw before finishing");
  } else {
    for (const line of lines) {
      const pass = line.startsWith("PASS");
      const text = line.replace(/^(PASS|FAIL) :: /, "");
      pass ? ok(text) : fail(text);
    }
  }
}

fs.rmSync(tmp, { recursive: true, force: true });

console.log("");
if (failures) {
  console.log(`${failures} check(s) failed.`);
  process.exit(1);
}
console.log("All board-fit checks passed.");
