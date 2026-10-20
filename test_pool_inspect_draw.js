#!/usr/bin/env node
/* Inspect has to hand you the card you clicked.
 *
 * The bug: "when you click inspect you can click on the cards in the pool and
 * it wont draw them for you". The Inspect grid was torn down and rebuilt from
 * scratch by renderPoolModal() on EVERY state render, and the state renders on
 * the 1s poll (2.5s plus every push on SSE). A browser only fires `click` on
 * the element that saw both the press and the release; when a poll landed in
 * between, the card the player pressed no longer existed, Chrome dispatched the
 * click to #pv-pm-grid instead, and nothing was listening there. The click
 * vanished: no draw, no zoom, no toast. At a 1s cadence and a ~100ms press
 * that is roughly one click in ten, and longer presses on a touch screen lose
 * more.
 *
 * The same teardown orphaned the click/double-click disambiguation timer. It
 * was a `let _pmTimer` inside the per-card loop, so a rebuild between the two
 * clicks of a double-click left the first click's 200ms timer with no live
 * element able to cancel it: the player got the zoom AND a card they never
 * asked to draw.
 *
 * The fix is to stop rebuilding what the player is aiming at. The grid carries
 * a signature of what it is showing (the pool's face uids in order, plus
 * whether clicks draw), and renderPoolModal() rebuilds only when that
 * signature changes. The timer is one modal-scoped timer, cleared on any
 * rebuild, so it can never outlive the cards it belongs to.
 *
 * Run:  node test_pool_inspect_draw.js      (render half needs Google Chrome)
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const APP    = read("js/preview-app.js");
const HTML   = read("preview.html");
const CSS    = read("css/preview.css");
const ENGINE = fs.readFileSync(path.join(ROOT, "fish_game_all_in_one.py"), "utf8");

let pass = 0, fail = 0;
function check(cond, name, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

// ── the slice of preview-app.js under test ──────────────────────────────────
function slice(from, to) {
  const a = APP.indexOf(from);
  const b = APP.indexOf(to, a < 0 ? 0 : a);
  if (a < 0 || b < 0) return "";
  return APP.slice(a, b);
}
const FN_MODAL = slice("  // \u2500\u2500 Pool overview modal", '  document.getElementById("pv-pool-expand-btn")');
const FN_DRAW  = slice("  function _drawFromPool(drawActions, faceUid) {", "  function renderPool(pool, actions)");

// ── 1. Source guards ────────────────────────────────────────────────────────
console.log("\nthe Inspect grid is not rebuilt out from under the pointer");

check(/function openPoolModal\(\)/.test(FN_MODAL) && /function renderPoolModal\(\)/.test(FN_MODAL),
      "openPoolModal/renderPoolModal were found in preview-app.js");
check(FN_DRAW.length > 100, "_drawFromPool() was found in preview-app.js");

check(
  /dataset\.poolSig|_pmGridSig/.test(FN_MODAL),
  "renderPoolModal() keeps a signature of what the grid is already showing"
);
// The teardown that caused the bug. It may only run when the signature changed.
const clearsGrid = /gridEl\.innerHTML\s*=\s*""/.test(FN_MODAL);
check(clearsGrid, "renderPoolModal() still has a rebuild path for a changed pool");
check(
  !/^\s*let _pmTimer = null;/m.test(FN_MODAL),
  "the click/double-click timer is not declared per card inside the loop"
);
check(
  /_pmClickTimer|_pmTimer/.test(FN_MODAL) && /clearTimeout\(_pm/.test(FN_MODAL),
  "a pending single-click timer is still cancelled by a double-click"
);

// The modal's gate and _drawFromPool() must agree on what a pool draw is, or a
// wired click lands on a toast. The engine only ever offers 0 or 1.
const engineDraws = [...new Set(
  (ENGINE.match(/Action\(kind="draw", draw_from_pool=(\d+)\)/g) || [])
    .map(s => s.match(/draw_from_pool=(\d+)/)[1])
)].sort();
check(
  engineDraws.join(",") === "0,1",
  "the engine offers exactly draw_from_pool 0 and 1", "saw " + engineDraws.join(",")
);
const gateOne = /Number\(a\.draw_from_pool\)\s*===?\s*1/.test(FN_MODAL);
check(
  gateOne,
  "the modal's canDrawPool gate matches _drawFromPool(), which needs draw_from_pool === 1"
);

// ── 2. The real render, real CSS, real mouse events ─────────────────────────
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

function modalMarkup() {
  const i = HTML.indexOf('<div id="pv-pool-modal"');
  const e = HTML.indexOf('<div id="pv-pm-empty"');
  if (i < 0 || e < 0) return "";
  const close = HTML.indexOf("</div>", HTML.indexOf("</div>", e) + 6) + 6;
  return HTML.slice(i, close) + "</div></div>";
}

function page() {
  const MARKUP = modalMarkup();
  return `<!doctype html><html><head><meta charset="utf-8"><style>${CSS}</style></head>
<body><div id="pv-pool-modal"></div><pre id="out"></pre>
<script>
document.getElementById("pv-pool-modal").outerHTML = ${JSON.stringify(MARKUP)};
const seen = [], res = [];
function note(c, m) { res.push((c ? "PASS " : "FAIL ") + m); }
let canInteract = false, _latestPool = [], _latestPoolActions = [];
// renderPool() keeps this fresh in the real app; the modal's click handler and
// the pool strip's both read it rather than a per-render snapshot.
let _poolDrawActions = null;
function setActions(a) { _latestPoolActions = a; _poolDrawActions = a.filter(x => x.kind === "draw"); }
const _pvAnim = { isBusy: () => false };
function imagePathForUid(u) { return "data:image/gif;base64,R0lGODlhAQABAAAAACw="; }
function openZoom(uid) { seen.push("ZOOM:" + uid); }
function showToast(m) { seen.push("TOAST:" + m); }
function submitAction(a) { seen.push("DRAW:" + JSON.stringify(a.pool_pick_uids)); }
${FN_MODAL}
${FN_DRAW}

// The engine's real first-draw action set (fish_game_all_in_one.py legal_actions)
const ACTIONS = [
  { kind: "draw", draw_from_pool: 0, index: 0 },
  { kind: "draw", draw_from_pool: 1, index: 1 },
  { kind: "play_to_ocean", card_uid: 501, index: 2 },
];
// Real pool entries: paired faces, as last_live_game_state.json has them.
const POOL = [
  { entry_uid: 79, faces: [{ uid: 79, name: "Flying Fish", species: "Baitfish", text: "" },
                           { uid: 80, name: "Mandarin Goby", species: "N/A", text: "" }] },
  { entry_uid: 9,  faces: [{ uid: 9,  name: "Horned Puffin", species: "Bird", text: "" }] },
  { entry_uid: 33, faces: [{ uid: 33, name: "Sea Star", species: "Invertebrate", text: "" }] },
  { entry_uid: 44, faces: [{ uid: 44, name: "Kelp", species: "N/A", text: "" }] },
];
const first = () => document.querySelector("#pv-pm-grid .pv-pm-card");
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
function at(el) {
  const r = el.getBoundingClientRect();
  return { bubbles: true, cancelable: true, view: window,
           clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
}
function press(el, o) { el.dispatchEvent(new MouseEvent("mousedown", o)); }
function release(el, o) { el.dispatchEvent(new MouseEvent("mouseup", o)); }
function fullClick(el) { const o = at(el); press(el, o); release(el, o); el.dispatchEvent(new MouseEvent("click", o)); }
function drew() { return seen.filter(s => s.startsWith("DRAW:")); }
function zoomed() { return seen.filter(s => s.startsWith("ZOOM:")); }

(async () => {
  // A. a plain click during the draw phase draws that card
  canInteract = true; _latestPool = POOL; setActions(ACTIONS);
  openPoolModal();
  const a = first();
  note(!!a, "the Inspect grid renders the pool");
  note(a && getComputedStyle(a).cursor === "pointer", "an Inspect card looks clickable");
  seen.length = 0;
  if (a) fullClick(a);
  await sleep(320);
  note(drew().length === 1 && drew()[0] === "DRAW:[79]",
       "a plain click in the draw phase draws THAT card", JSON.stringify(seen));

  // B. THE BUG: a 1s poll re-render landing between press and release
  seen.length = 0;
  openPoolModal();
  const b = first();
  const ob = at(b);
  press(b, ob);
  renderPoolModal();                      // the poll lands mid-click
  const b2 = first();
  note(b2 === b, "a poll re-render with an unchanged pool keeps the very card being pressed");
  // Chrome fires click on the nearest common ancestor when the pressed element is gone
  const target = (b2 === b) ? b : document.getElementById("pv-pm-grid");
  release(target, ob);
  target.dispatchEvent(new MouseEvent("click", ob));
  await sleep(320);
  note(drew().length === 1,
       "a click that straddles a poll re-render still draws the card", JSON.stringify(seen));

  // C. the pool really changing does rebuild the grid
  // (B drew a card, and a draw closes Inspect, so it is reopened here)
  seen.length = 0;
  _latestPool = POOL.slice(0, 3);
  openPoolModal();
  note(document.querySelectorAll("#pv-pm-grid .pv-pm-card").length === 3,
       "a pool that actually changed is redrawn");
  _latestPool = POOL;
  renderPoolModal();
  note(document.querySelectorAll("#pv-pm-grid .pv-pm-card").length === 4,
       "and redrawn again when it changes back");

  // D. losing the right to draw rewires the cards
  seen.length = 0;
  setActions(ACTIONS.filter(x => x.kind !== "draw"));
  renderPoolModal();
  fullClick(first());
  await sleep(320);
  note(drew().length === 0 && zoomed().length === 1,
       "with no draw left, a click zooms instead of drawing", JSON.stringify(seen));

  // E. a double-click in the draw phase zooms and must NOT draw
  seen.length = 0;
  setActions(ACTIONS);
  renderPoolModal();
  const e1 = first(); const oe = at(e1);
  e1.dispatchEvent(new MouseEvent("click", oe));
  e1.dispatchEvent(new MouseEvent("click", oe));
  e1.dispatchEvent(new MouseEvent("dblclick", oe));
  await sleep(320);
  note(zoomed().length === 1 && drew().length === 0,
       "a double-click zooms and draws nothing", JSON.stringify(seen));

  // F. the same double-click with a poll re-render between the two clicks
  seen.length = 0;
  renderPoolModal();
  const f1 = first(); const of_ = at(f1);
  f1.dispatchEvent(new MouseEvent("click", of_));
  renderPoolModal();                      // the poll lands between the clicks
  const f2 = first();
  f2.dispatchEvent(new MouseEvent("click", of_));
  f2.dispatchEvent(new MouseEvent("dblclick", of_));
  await sleep(320);
  note(drew().length === 0,
       "a double-click across a poll re-render does not draw a card you never picked",
       JSON.stringify(seen));

  // G. an empty pool says so, and refills cleanly
  seen.length = 0;
  _latestPool = [];
  openPoolModal();
  note(document.querySelectorAll("#pv-pm-grid .pv-pm-card").length === 0
       && document.getElementById("pv-pm-empty").style.display === "",
       "an empty pool shows the empty note");
  _latestPool = POOL;
  renderPoolModal();
  note(document.querySelectorAll("#pv-pm-grid .pv-pm-card").length === 4
       && document.getElementById("pv-pm-empty").style.display === "none",
       "and the grid comes back when the pool refills");

  // H. reopening the modal after a close still draws
  seen.length = 0;
  closePoolModal();
  openPoolModal();
  fullClick(first());
  await sleep(320);
  note(drew().length === 1, "reopening Inspect still draws on a click", JSON.stringify(seen));

  document.getElementById("out").textContent = res.join("\\n");
})().catch(e => {
  document.getElementById("out").textContent = "FAIL harness threw: " + (e && e.message);
});
</script></body></html>`;
}

if (!CHROME) {
  console.log("\n(skipping the render half: Google Chrome not found)");
} else if (!modalMarkup()) {
  check(false, "the pool modal markup was found in preview.html");
} else {
  console.log("\nthe real Inspect modal, real CSS, real mouse events");
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-inspect-"));
  const file = path.join(tmp, "inspect.html");
  fs.writeFileSync(file, page());
  let dom = "";
  try {
    dom = execFileSync(CHROME, [
      "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      "--window-size=1400,1000", "--virtual-time-budget=20000",
      "--dump-dom", "file://" + file,
    ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 120000,
         maxBuffer: 64 * 1024 * 1024 });
  } catch (e) {
    check(false, "Chrome ran", e.message);
  }
  const m = dom.match(/<pre id="out">([\s\S]*?)<\/pre>/);
  const body = m ? m[1].replace(/&quot;/g, '"').replace(/&lt;/g, "<")
                       .replace(/&gt;/g, ">").replace(/&amp;/g, "&") : "";
  if (!body.trim()) check(false, "measurements came back from Chrome", dom.slice(0, 400));
  else body.split("\n").filter(Boolean).forEach(line =>
    check(line.startsWith("PASS "), line.replace(/^(PASS|FAIL) /, "")));
  fs.rmSync(tmp, { recursive: true, force: true });
}

console.log(`\n${fail ? "FAILED" : "All"} ${fail ? fail + " of " + (pass + fail) : pass} checks${fail ? "" : " passed"}.`);
process.exit(fail ? 1 : 0);
