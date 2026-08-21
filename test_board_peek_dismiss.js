#!/usr/bin/env node
/* Browser check for THE BOARD PEEK NEVER GETTING STUCK ON SCREEN.
 *
 * Run:  node test_board_peek_dismiss.js        (needs Google Chrome installed)
 *
 * The bug: "when you hover over someone's board and then go away, or the game
 * ends, the screen can still be there." The peek (#pv-board-hover) is a
 * position:fixed panel anchored to a seat pill / opponent card, and it was only
 * ever hidden by that element's own mouseleave. But every one of those elements
 * is destroyed and rebuilt whenever the seats or opponents re-render — and a
 * removed element never fires mouseleave. So a peek that was open when the state
 * ticked stayed open forever: over the table, over the end screen, and back out
 * to Player Home, since nothing on the way out cleared it either.
 *
 * Every check below drives the REAL peek code sliced out of preview-app.js
 * against the REAL preview.css in headless Chrome, so the dismissal paths are
 * measured, not assumed:
 *   • hover shows it, mouseleave hides it (the path that already worked)
 *   • the anchor being re-rendered away under a pointer that never moved hides
 *     it — and re-anchors instead when a fresh anchor took its place
 *   • pointer off the anchor without a mouseleave, click, scroll, tab-away
 *   • Escape and the one closeBoardFocus() exit clear peek AND enlarged board
 *   • touch devices (hover:none) never open a peek that could not be closed
 * The two exits that need the whole app to run — game over and leaving the game
 * — are checked at the source level: both must call closeBoardFocus().
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
const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");

let failures = 0;
const ok   = (m) => console.log("  ✓ " + m);
const fail = (m) => { console.log("  ✗ " + m); failures++; };

// ── 1. SOURCE: the two exits that only exist inside the running app ──────────
// renderEndGame's first reveal and _leaveGameCleanup are unreachable from a
// standalone page (they need SSE, Firebase, a live room), so pin them here:
// they are what stops a peek/board following the player onto the end screen or
// out to Player Home.
console.log("\nGAME-OVER + LEAVE-GAME WIRING (source)");
{
  const endReveal = APP.match(/if \(!_endRevealMs\) \{[\s\S]{0,400}?\n    \}/);
  if (endReveal && /closeBoardFocus\(\)/.test(endReveal[0])) {
    ok("game over (first end-screen reveal) calls closeBoardFocus()");
  } else {
    fail("game over does NOT clear the peek/enlarged board");
  }
  // Once only: from the end screen you can open a board again, and the
  // end-game poll re-runs renderEndGame every tick.
  if (endReveal && /_endRevealMs = Date\.now\(\)/.test(endReveal[0])) {
    ok("it fires on the first reveal only, not on every end-game poll");
  } else {
    fail("end-screen close is not guarded to the first reveal");
  }
  const leave = APP.match(/function _leaveGameCleanup\([\s\S]*?\n  \}\n/);
  if (leave && /closeBoardFocus\(\)/.test(leave[0])) {
    ok("_leaveGameCleanup() calls closeBoardFocus() on the way to Player Home");
  } else {
    fail("leaving the game does NOT clear the peek/enlarged board");
  }
}

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found — cannot run the browser half.");
  process.exit(failures ? 1 : 0);
}

// ── Pull the REAL peek + board-focus code out of preview-app.js ──────────────
// preview-app.js is one 26k-line module that needs the whole app to boot, so we
// evaluate the one self-contained region: openBoardFocus through the Escape
// handler. Its only outside dependency is renderReadOnlyBoard, stubbed below.
const START = "  function _boardFocusScaleFor(oceanCount) {";
const END   = "  // ── End game overlay ──";
const s = APP.indexOf(START), e = APP.indexOf(END);
if (s < 0 || e < 0 || e <= s) {
  console.error("FAIL: could not slice the board peek code out of preview-app.js");
  process.exit(1);
}
const PEEK = APP.slice(s, e);
for (const fn of ["showBoardHover", "hideBoardHover", "attachBoardHover", "closeBoardFocus"]) {
  if (!PEEK.includes("function " + fn)) {
    console.error(`FAIL: sliced region is missing ${fn}() — the markers moved.`);
    process.exit(1);
  }
}

// ── The page: the real overlay markup + the real stylesheet ──────────────────
const overlayMarkup = (() => {
  const grab = (id) => {
    const i = HTML.indexOf(`<div id="${id}">`);
    if (i < 0) return "";
    // Overlay blocks in preview.html end at the first line that closes them.
    const end = HTML.indexOf("\n</div>", i);
    return HTML.slice(i, end + "\n</div>".length);
  };
  return grab("pv-board-focus") + "\n" + grab("pv-board-hover");
})();
if (!overlayMarkup.includes("pv-board-hover") || !overlayMarkup.includes("pv-board-focus-close")) {
  console.error("FAIL: could not lift the overlay markup out of preview.html");
  process.exit(1);
}

const page = `<!doctype html><html><head><meta charset="utf-8">
<title>board peek dismiss</title>
<style>${CSS}</style>
<style>
  /* Stand-ins for the seat pills / opponent cards the peek anchors to, at
     roughly their real size and away from the screen edges. */
  .seat { position: fixed; width: 120px; height: 90px; background: #234; }
  #seatA { left: 40px; top: 120px; }
  #seatB { left: 40px; top: 260px; }
  #outside { position: fixed; left: 700px; top: 500px; width: 200px; height: 120px; background: #333; }
  #scroller { position: fixed; left: 700px; top: 40px; width: 200px; height: 100px; overflow: auto; }
</style>
</head>
<body>
<div id="seatA" class="seat"></div>
<div id="seatB" class="seat"></div>
<div id="outside"></div>
<div id="scroller"><div style="height:600px"></div></div>
${overlayMarkup}
<div id="out"></div>
<script>
// Stub for the one dependency the sliced region has on the rest of the app.
function renderReadOnlyBoard(player) {
  const d = document.createElement("div");
  d.className = "pv-ro-board";
  d.style.cssText = "width:220px;height:140px;";
  d.textContent = (player && player.name) || "";
  return d;
}
${PEEK}
window.__t = { attachBoardHover, showBoardHover, hideBoardHover, openBoardFocus, closeBoardFocus };
</script>
<script>
(async () => {
  const T = window.__t;
  const R = [];
  const rec = (name, pass, detail) => R.push((pass ? "PASS" : "FAIL") + " :: " + name + (detail ? " :: " + detail : ""));
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  const pop = () => document.getElementById("pv-board-hover");
  const shown = () => pop().classList.contains("visible");
  const title = () => document.getElementById("pv-bh-title").textContent;
  const focusOpen = () => document.getElementById("pv-board-focus").classList.contains("open");
  const enter = (el) => el.dispatchEvent(new MouseEvent("mouseenter", { bubbles: false }));
  const leave = (el) => el.dispatchEvent(new MouseEvent("mouseleave", { bubbles: false }));
  const moveOver = (el) => {
    const r = el.getBoundingClientRect();
    el.dispatchEvent(new MouseEvent("mousemove", {
      bubbles: true, clientX: Math.round(r.left + r.width / 2), clientY: Math.round(r.top + r.height / 2),
    }));
  };
  const mkSeat = (id, top, player) => {
    const el = document.createElement("div");
    el.id = id; el.className = "seat"; el.style.top = top + "px"; el.style.left = "40px";
    document.body.appendChild(el);
    T.attachBoardHover(el, player);
    return el;
  };

  const alice = { name: "Alice", index: 1, board: [] };
  const bob   = { name: "Bob",   index: 2, board: [] };
  let A = document.getElementById("seatA");
  const B = document.getElementById("seatB");
  T.attachBoardHover(A, alice);
  T.attachBoardHover(B, bob);

  // 1. Hovering a board opens the peek (the feature still works).
  enter(A); await wait(200);
  rec("hovering a seat opens the peek", shown() && /Alice/.test(title()), title());

  // 2. Moving off it the normal way closes it.
  leave(A); await wait(50);
  rec("mouseleave closes the peek", !shown());

  // 3. THE BUG: the anchor is re-rendered away while the pointer sits still.
  //    No mouse event of any kind follows — that is exactly why it used to stay.
  enter(A); await wait(200);
  const wasOpen = shown();
  A.remove();
  await wait(500);
  rec("peek closes when its seat is re-rendered away (no mouse events)", wasOpen && !shown());

  // 4. ...but when a fresh seat took its place under the pointer, the peek
  //    re-anchors to it instead of flickering away.
  A = mkSeat("seatA", 120, alice);
  moveOver(A);            // establish a pointer position over the seat
  enter(A); await wait(200);
  const openBefore = shown();
  A.remove();
  const A2 = mkSeat("seatA", 120, bob);   // same spot, rebuilt with new data
  await wait(500);
  rec("peek re-anchors to the rebuilt seat under the pointer",
      openBefore && shown() && /Bob/.test(title()), title());

  // 5. Pointer moves off the anchor without any mouseleave reaching us.
  const outside = document.getElementById("outside");
  moveOver(outside); await wait(50);
  rec("peek closes once the pointer is over something else", !shown());

  // 6. Clicking anywhere closes it.
  moveOver(A2); enter(A2); await wait(200);
  const c1 = shown();
  outside.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  await wait(50);
  rec("clicking off the peek closes it", c1 && !shown());

  // 7. Scrolling closes it (it is position:fixed — it would detach from the seat).
  enter(A2); await wait(200);
  const c2 = shown();
  document.getElementById("scroller").dispatchEvent(new Event("scroll", { bubbles: false }));
  await wait(50);
  rec("scrolling closes it", c2 && !shown());

  // 8. Tabbing away / losing the window closes it.
  enter(A2); await wait(200);
  const c3 = shown();
  window.dispatchEvent(new Event("blur"));
  await wait(50);
  rec("leaving the window closes it", c3 && !shown());

  // 9. Opening the enlarged board never leaves the peek behind it.
  enter(A2); await wait(200);
  const c4 = shown();
  T.openBoardFocus(bob);
  await wait(20);
  rec("opening the enlarged board closes the peek", c4 && focusOpen() && !shown());

  // 10. Escape closes the enlarged board.
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await wait(20);
  rec("Escape closes the enlarged board", !focusOpen());

  // 11. The one exit used by game-over and leaving the game clears both.
  T.openBoardFocus(alice);
  T.showBoardHover(alice, A2);
  await wait(20);
  const bothUp = focusOpen() && shown();
  T.closeBoardFocus();
  await wait(20);
  rec("closeBoardFocus() clears the enlarged board AND the peek", bothUp && !focusOpen() && !shown());

  // 12. Touch: :hover sticks to the last thing tapped and no mouseleave ever
  //     arrives, so a peek there could not be closed. It must never open.
  const realMM = window.matchMedia;
  window.matchMedia = (q) => (/hover:\\s*hover/.test(q) ? { matches: false, media: q } : realMM.call(window, q));
  enter(A2); await wait(300);
  rec("no peek on a touch-only device", !shown());
  window.matchMedia = realMM;

  document.getElementById("out").textContent = R.join("\\n");
})();
</script>
</body></html>`;

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-peek-"));
const file = path.join(tmp, "peek.html");
fs.writeFileSync(file, page);

let dom = "";
try {
  dom = execFileSync(CHROME, [
    "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    "--window-size=1280,900", "--virtual-time-budget=12000",
    "--dump-dom", "file://" + file,
  ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 90000 });
} catch (err) {
  console.error("Chrome failed to run:", err.message);
  process.exit(1);
}
fs.rmSync(tmp, { recursive: true, force: true });

const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
const lines = m ? m[1].split("\n").map(l => l.trim()).filter(Boolean) : [];
console.log("\nPEEK DISMISSAL (headless Chrome, real preview.css)");
if (!lines.length) {
  fail("the page produced no results — the sliced peek code threw before finishing");
} else {
  for (const line of lines) {
    const pass = line.startsWith("PASS");
    const text = line.replace(/^(PASS|FAIL) :: /, "");
    pass ? ok(text) : fail(text);
  }
}

console.log(failures ? `\n${failures} check(s) failed.` : "\nAll board-peek dismissal checks passed.");
process.exit(failures ? 1 : 0);
