#!/usr/bin/env node
/* WHEN YOU PLAY AN OCEAN, THE CARD IT FLIPS OFF THE DECK FLIPS OUT AT 200%.
 *
 * Run:  node test_ocean_flip_reveal.js      (needs Google Chrome installed)
 *
 * Asked for as: "bring back whenever you play an ocean a card flips out 200%".
 * The rule came back in the engine (test_ocean_play_flip.py pins that); this
 * pins what the player actually sees. The flipped card is the one card on the
 * table nobody has seen, so it does not slide quietly into the Pool: it turns
 * face-up out of the deck, swells to double size in open water, holds there
 * long enough to be read, and only then settles into its Pool slot, which
 * stays empty until it lands.
 *
 * Measured in real screen pixels in headless Chrome against the REAL
 * preview.css and the REAL flipOutFromDeck() / applyPendingRippleReveals()
 * lifted out of preview-app.js. The animation is seeked by currentTime rather
 * than waited out, so every measurement is the geometry the browser really
 * computes, with no timing luck in it.
 *
 * What this pins:
 *   A. The card leaves the deck at its own size, face-down, over the deck.
 *   B. At the hold it is 200% of card size, and the composite transform is
 *      face-UP (the face is not mirrored), which is the whole point of a
 *      reveal you are meant to read.
 *   C. The hold is inside the viewport, including at phone width, where 200%
 *      would not fit and the clamp takes over (but never below 115%).
 *   D. It lands on the Pool slot it was headed for, at the slot's size.
 *   E. The reveal wears the gold of the game's own reveals, not the blue of a
 *      card merely in flight.
 *   F. The Pool slot the card is headed for stays hidden until the flight is
 *      over, while an ordinary Pool arrival (a paid or discarded card) ripples
 *      in straight away.
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
  console.log("SKIP: no Chrome/Chromium found: cannot measure the flip.");
  process.exit(0);
}

const CSS = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const APP = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");

// ── Lift the REAL functions out of preview-app.js ───────────────────────────
// They are private to the one 26k-line IIFE that needs auth, SSE and Firebase
// to boot, so they are sliced out by balanced braces and run against stubs for
// what they close over. Everything measured here is inside the slices.
function grabFn(name) {
  const re = new RegExp("\\n\\s*function " + name + "\\(");
  const m = APP.match(re);
  if (!m) throw new Error("function " + name + "() not found in preview-app.js");
  const start = m.index;
  // Walk the parameter list first: a default like `opts = {}` would otherwise
  // look like the body and end the slice on its own closing brace.
  let i = APP.indexOf("(", start), paren = 0;
  for (; i < APP.length; i++) {
    if (APP[i] === "(") paren++;
    else if (APP[i] === ")") { paren--; if (paren === 0) break; }
  }
  let depth = 0;
  for (let j = APP.indexOf("{", i); j < APP.length; j++) {
    const ch = APP[j];
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return APP.slice(start, j + 1); }
  }
  throw new Error("unbalanced braces reading " + name + "()");
}

function grabLine(needle) {
  const i = APP.indexOf(needle);
  if (i < 0) throw new Error("line not found in preview-app.js: " + needle);
  return APP.slice(i, APP.indexOf("\n", i));
}

const MAKE_ANIM_CARD = grabFn("makeAnimCard");
const FLIP_OUT       = grabFn("flipOutFromDeck");
const PLAY_RIPPLE    = grabFn("playRippleAt");
const APPLY_REVEALS  = grabFn("applyPendingRippleReveals");
const QUEUE_REVEAL   = grabFn("queuePoolReveal");
const FLIP_CONSTS    = grabLine("const OCEAN_FLIP_OUT") + "\n" + grabLine("const OCEAN_FLIP_MS");

// Sanity: the slices must still be the code we think they are, so a rename or
// a refactor fails loudly here instead of silently testing nothing.
for (const needle of ["ocean-flip", "rotateY(180deg)", "makeAnimCard", "window.innerWidth"]) {
  if (!FLIP_OUT.includes(needle)) {
    console.log("FAIL: flipOutFromDeck() no longer mentions " + needle + ": testing the wrong code.");
    process.exit(1);
  }
}
if (!APPLY_REVEALS.includes("surfacing") || !APPLY_REVEALS.includes("Math.max(delay")) {
  console.log("FAIL: applyPendingRippleReveals() no longer holds a reveal back: testing the wrong code.");
  process.exit(1);
}

const PAGE = '<!doctype html><html><head><meta charset="utf-8"><title>ocean flip</title>\n'
+ "<style>" + CSS + "</style>\n"
+ '<style>\n'
+ '  html,body { margin:0; padding:0; overflow:hidden; }\n'
+ '  /* The deck and a Pool slot, placed where preview.html puts them, so the\n'
+ '     flight has real screen coordinates to start from and land on. */\n'
+ '  #pv-draw-deck { position:fixed; left:24px; bottom:24px; width:70px; height:98px; }\n'
+ '  #pv-draw-deck .pv-deck-face { position:absolute; inset:0; background:#2A5AA0; }\n'
+ '  #pv-pool-wrap { position:fixed; left:50%; top:16px; transform:translateX(-50%);\n'
+ '                  display:flex; gap:6px; }\n'
+ '  #pv-pool-wrap .pv-pool-card { position:relative; width:70px; height:98px; background:#2a6; }\n'
+ '</style></head><body>\n'
+ '<div id="pv-anim-layer"></div>\n'
+ '<div id="pv-draw-deck"><div class="pv-deck-face"></div></div>\n'
+ '<div id="pv-pool-wrap">\n'
+ '  <div class="pv-pool-card" data-face-uid="101"></div>\n'
+ '  <div class="pv-pool-card" data-face-uid="102"></div>\n'
+ '</div>\n'
+ '<div id="out">RUNNING</div>\n'
+ '<script>\n'
+ 'window.onerror=function(m,f,l){document.getElementById("out").textContent="FAIL threw: "+m+" @line "+l;};\n'
+ '</script>\n'
+ '<script>\n'
+ '(function () {\n'
+ '  // ── Stubs for what the slices close over ─────────────────────────\n'
+ '  var running = 0;\n'
+ '  var pendingPoolReveals = new Map();\n'
+ '  function layer() { return document.getElementById("pv-anim-layer"); }\n'
+ '  function imagePathForUid(uid) { return "assets/cards/" + uid + ".png"; }\n'
+ '  function rectOf(el) {\n'
+ '    if (!el) return null;\n'
+ '    var r = el.getBoundingClientRect();\n'
+ '    return { left: r.left, top: r.top, width: r.width, height: r.height };\n'
+ '  }\n'
+ FLIP_CONSTS + '\n'
+ MAKE_ANIM_CARD + '\n'
+ PLAY_RIPPLE + '\n'
+ FLIP_OUT + '\n'
+ QUEUE_REVEAL + '\n'
+ APPLY_REVEALS + '\n'
+ '\n'
+ '  var results = [];\n'
+ '  function ok(cond, m) { results.push((cond ? "PASS " : "FAIL ") + m); }\n'
+ '  function note(m) { results.push("NOTE " + m); }\n'
+ '  function report() { document.getElementById("out").textContent = results.join("\\n"); }\n'
+ '\n'
+ '  var BASE_W = 90, BASE_H = 126;   // .pv-anim-card, from preview.css\n'
+ '  var vw = window.innerWidth, vh = window.innerHeight;\n'
+ '  var deck = document.getElementById("pv-draw-deck").querySelector(".pv-deck-face");\n'
+ '  var slot = document.querySelector(\'.pv-pool-card[data-face-uid="101"]\');\n'
+ '  var deckR = rectOf(deck), slotR = rectOf(slot);\n'
+ '\n'
+ '  flipOutFromDeck(deckR, slotR, { faceUid: 101 });\n'
+ '\n'
+ '  // Two frames: one for the appendChild, one for the rAF the flight waits on.\n'
+ '  requestAnimationFrame(function () { requestAnimationFrame(function () {\n'
+ '    var card = document.querySelector("#pv-anim-layer .pv-anim-card");\n'
+ '    if (!card) { ok(false, "no card was ever put in the air"); report(); return; }\n'
+ '    ok(card.classList.contains("ocean-flip"), "the card in the air is marked as an Ocean flip");\n'
+ '    ok(card.classList.contains("face-down"), "it leaves the deck face-down, as a deck card is");\n'
+ '    var anims = card.getAnimations();\n'
+ '    if (!anims.length) { ok(false, "the card was never animated"); report(); return; }\n'
+ '    var anim = anims[0];\n'
+ '    anim.pause();\n'
+ '    var total = anim.effect.getTiming().duration;\n'
+ '    ok(total === OCEAN_FLIP_MS, "the flight lasts the whole reveal (" + total + "ms)");\n'
+ '\n'
+ '    function at(ms) { anim.currentTime = ms; return card.getBoundingClientRect(); }\n'
+ '    function m11At(ms) {\n'
+ '      anim.currentTime = ms;\n'
+ '      var m = new DOMMatrixReadOnly(getComputedStyle(card).transform);\n'
+ '      return m.m11;\n'
+ '    }\n'
+ '\n'
+ '    // ── A. It starts on the deck, at its own size ──────────────────\n'
+ '    var r0 = at(0);\n'
+ '    ok(Math.abs(r0.width - BASE_W) <= 1 && Math.abs(r0.height - BASE_H) <= 1,\n'
+ '       "it starts at card size (" + Math.round(r0.width) + "x" + Math.round(r0.height) + ")");\n'
+ '    var dCx = deckR.left + deckR.width / 2, dCy = deckR.top + deckR.height / 2;\n'
+ '    ok(Math.abs((r0.left + r0.width / 2) - dCx) <= 1.5 && Math.abs((r0.top + r0.height / 2) - dCy) <= 1.5,\n'
+ '       "and it starts centred on the deck itself");\n'
+ '\n'
+ '    // ── B. The hold: 200% of card size, and readable ───────────────\n'
+ '    var hold = at(OCEAN_FLIP_OUT + 10);\n'
+ '    var scale = hold.width / BASE_W;\n'
+ '    var roomFor2 = (vw * 0.72) / BASE_W >= 2 && (vh * 0.52) / BASE_H >= 2;\n'
+ '    if (roomFor2) {\n'
+ '      ok(Math.abs(scale - 2) <= 0.02, "it holds at 200% of card size (" + scale.toFixed(2)\n'
+ '         + "x, " + Math.round(hold.width) + "x" + Math.round(hold.height) + "px)");\n'
+ '    } else {\n'
+ '      ok(scale > 1.15 && scale < 2.01, "the screen cannot hold 200%, so it swells as far as it can ("\n'
+ '         + scale.toFixed(2) + "x)");\n'
+ '    }\n'
+ '    // rotateY(180deg) at scale S reads as m11 = -S on the card, and the face\n'
+ '    // child carries its own rotateY(180deg): -S x -1 = +S, a face the right\n'
+ '    // way round. A card held face-DOWN or mirrored would fail this.\n'
+ '    var cardM11 = m11At(OCEAN_FLIP_OUT + 10);\n'
+ '    ok(Math.abs(cardM11 + scale) <= 0.05,\n'
+ '       "it has turned a full 180deg by the hold, so the face is what you see (m11 "\n'
+ '       + cardM11.toFixed(2) + ")");\n'
+ '    var faceM11 = new DOMMatrixReadOnly(getComputedStyle(card.querySelector(".pv-anim-face")).transform).m11;\n'
+ '    ok(Math.abs(faceM11 + 1) <= 0.01,\n'
+ '       "and the face is pre-flipped, so the art is not held up mirrored");\n'
+ '    var held = at(OCEAN_FLIP_OUT + OCEAN_FLIP_HOLD - 10);\n'
+ '    ok(Math.abs((held.width / BASE_W) - scale) <= 0.02,\n'
+ '       "it is still that big " + OCEAN_FLIP_HOLD + "ms later, so there is time to read it");\n'
+ '\n'
+ '    // ── C. The hold is on screen ───────────────────────────────────\n'
+ '    ok(hold.left >= -0.5 && hold.top >= -0.5 && hold.right <= vw + 0.5 && hold.bottom <= vh + 0.5,\n'
+ '       "the swollen card is wholly on screen (" + Math.round(hold.left) + ","\n'
+ '       + Math.round(hold.top) + " to " + Math.round(hold.right) + "," + Math.round(hold.bottom) + ")");\n'
+ '\n'
+ '    // ── D. It lands on the Pool slot ───────────────────────────────\n'
+ '    var end = at(OCEAN_FLIP_MS);\n'
+ '    var sCx = slotR.left + slotR.width / 2, sCy = slotR.top + slotR.height / 2;\n'
+ '    ok(Math.abs((end.left + end.width / 2) - sCx) <= 2 && Math.abs((end.top + end.height / 2) - sCy) <= 2,\n'
+ '       "it lands centred on the Pool slot it was headed for");\n'
+ '    ok(Math.abs(end.width - slotR.width) <= 3,\n'
+ '       "at the slot\\u2019s own size (" + Math.round(end.width) + "px against the slot\\u2019s "\n'
+ '       + Math.round(slotR.width) + "px)");\n'
+ '\n'
+ '    // ── E. The look of a reveal ────────────────────────────────────\n'
+ '    var plain = makeAnimCard(102, false);\n'
+ '    layer().appendChild(plain);\n'
+ '    var goldish = getComputedStyle(card).borderTopColor;\n'
+ '    ok(goldish !== getComputedStyle(plain).borderTopColor,\n'
+ '       "a flipped card is not dressed like a card merely in flight (" + goldish + ")");\n'
+ '    ok((getComputedStyle(card).boxShadow || "").split("rgb").length >= 3,\n'
+ '       "and it carries its own glow");\n'
+ '    plain.remove();\n'
+ '\n'
+ '    // ── F. The Pool slot waits for it ──────────────────────────────\n'
+ '    queuePoolReveal(101, OCEAN_FLIP_MS - 80);   // the flipped card\n'
+ '    queuePoolReveal(102, 0);                    // an ordinary paid card\n'
+ '    applyPendingRippleReveals();\n'
+ '    var el1 = document.querySelector(\'.pv-pool-card[data-face-uid="101"]\');\n'
+ '    var el2 = document.querySelector(\'.pv-pool-card[data-face-uid="102"]\');\n'
+ '    ok(el1.style.opacity === "0" && el2.style.opacity === "0",\n'
+ '       "both new Pool cards start hidden, so nothing is revealed twice");\n'
+ '    setTimeout(function () {\n'
+ '      ok(el2.style.opacity !== "0", "the paid card ripples in straight away");\n'
+ '      ok(el1.style.opacity === "0", "the flipped card\\u2019s slot is still empty while it is in the air");\n'
+ '      setTimeout(function () {\n'
+ '        ok(el1.style.opacity !== "0", "and it fills the moment the card lands");\n'
+ '        ok(el1.classList.contains("surfacing") || el1.style.opacity === "",\n'
+ '           "arriving with the Pool\\u2019s own surfacing ripple");\n'
+ '        report();\n'
+ '      }, OCEAN_FLIP_MS);\n'
+ '    }, 300);\n'
+ '  }); });\n'
+ '})();\n'
+ '</script></body></html>';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ocean-flip-"));
const file = path.join(tmp, "flip.html");
fs.writeFileSync(file, PAGE);

let checks = 0, failures = 0;
const WINDOWS = [
  { label: "desktop 1512x857", size: "1512,857" },
  { label: "phone 390x844",    size: "390,844"  },
];

// Every measurement here hangs off requestAnimationFrame, and headless Chrome
// starves rAF outright on some runs: the page never gets a frame, #out is
// still RUNNING when the DOM is dumped, and nothing has been measured either
// way. Two things fix it, and both are needed.
//
// The first is a profile of its own per launch. Sharing the default profile is
// what made the SECOND window the unreliable one: it opens while the first has
// only just let go of that directory, and it is the phone pass, always, that
// came back empty. Five other suites here already launch Chrome this way.
//
// The second is to retry, because a starved frame is still possible. A run
// that produced no frame is not a result and is thrown away rather than read
// as a failure, the way test_account_signin.js retries a render that came back
// PENDING. Raising --virtual-time-budget is NOT one of the fixes: 8s and 20s
// flake at the same rate, because the budget was never what ran out.
function render(w) {
  for (let attempt = 0; attempt < 3; attempt++) {
    let dom = "";
    const profile = fs.mkdtempSync(path.join(tmp, "profile-"));
    try {
      dom = execFileSync(CHROME, [
        "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--no-first-run", "--no-default-browser-check", "--disable-extensions",
        "--user-data-dir=" + profile,
        "--window-size=" + w.size,
        "--virtual-time-budget=8000",
        "--dump-dom", "file://" + file,
      ], { encoding: "utf8", maxBuffer: 1 << 28, stdio: ["ignore", "pipe", "ignore"],
           timeout: 90000 });
    } catch (_) { continue; }
    const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
    if (!m) continue;
    const lines = m[1].replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&")
                      .replace(/&quot;/g, '"').split("\n").filter(Boolean);
    if (!lines.length) continue;
    if (lines.length === 1 && lines[0] === "RUNNING") continue;
    return lines;
  }
  return null;
}

for (const w of WINDOWS) {
  console.log("\n── " + w.label + " ───────────────────────────────────────");
  const lines = render(w);
  if (!lines) {
    console.log("  FAIL: the measurement script never finished, 3 attempts.");
    failures++; continue;
  }
  for (const line of lines) {
    if (line.startsWith("PASS ")) { checks++; console.log("  ✓ " + line.slice(5)); }
    else if (line.startsWith("FAIL ")) { checks++; failures++; console.log("  ✗ " + line.slice(5)); }
    else console.log("  · " + line.replace(/^NOTE /, ""));
  }
}

console.log("\n" + (checks - failures) + "/" + checks + " checks passed.");
if (failures) { console.log(failures + " FAILED"); process.exit(1); }
console.log("Play an Ocean and the card it flips off the deck flips out at 200%.");
