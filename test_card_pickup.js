#!/usr/bin/env node
/* THE CARD YOU PICK UP TRAVELS TO YOUR HAND.
 *
 * Run:  node test_card_pickup.js      (needs Google Chrome installed)
 *
 * Asked for as: "make a good short pick up animation".
 *
 * Picking a card up used to be two unrelated events: the Pool slot you clicked
 * emptied, and somewhere else entirely your hand grew by one. Now the card
 * itself makes the trip: it lifts out of where it came from, sweeps into its
 * place in the fan, and the real hand card is held back until it lands, so you
 * never see two of the same card. A card off the DECK turns over on the way,
 * because face-down is how it left.
 *
 * Measured in real screen pixels in headless Chrome against the REAL
 * preview.css and the REAL pickUpToHand() / handCardLandingRect() /
 * computeHandTransforms() lifted out of preview-app.js. The flight is seeked
 * by currentTime rather than waited out, so every measurement is the geometry
 * the browser really computes, with no timing luck in it.
 *
 * What this pins:
 *   A. The flight hands over exactly where the arriving card is painted. That
 *      is its LAYOUT box: the hand fan is not applied to a card until its
 *      deal-in animation has ENDED, so for nearly half a second the card sits
 *      square in its slot. Handing over at its place in the fan instead would
 *      make it jump the height of the arc at the handover, and its live rect
 *      mid deal-in is half size and still rising, so that is no use either.
 *   B. The flight starts on the Pool slot the card came from, at that size.
 *   C. It is picked UP before it is carried: higher and bigger, and barely
 *      moved along, at the end of the lift.
 *   D. It lands centred on its hand card, at hand-card size.
 *   E. It is short. A pick-up happens on nearly every turn.
 *   F. The hand card is invisible while its clone is in the air and back the
 *      moment it lands, and its deal-in is left alone throughout, because
 *      applyHandLayout waits for that animation to END before it fans the card.
 *   G. A card off the deck leaves face-down and arrives face-up.
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
  console.log("SKIP: no Chrome/Chromium found: cannot measure the pick-up.");
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
const PLAY_RIPPLE    = grabFn("playRippleAt");
const PICK_UP        = grabFn("pickUpToHand");
const LAND_RECT      = grabFn("handCardLandingRect");
const FAN_APPLY      = grabFn("applyHandLayout");
const HAND_TFORMS    = grabFn("computeHandTransforms");
const PICK_CONSTS    = grabLine("const PICKUP_LIFT") + "\n" + grabLine("const PICKUP_MS");

// Sanity: the slices must still be the code we think they are, so a rename or
// a refactor fails loudly here instead of silently testing nothing.
for (const needle of ["pv-pickup", "visibility", "rotateY", "makeAnimCard"]) {
  if (!PICK_UP.includes(needle)) {
    console.log("FAIL: pickUpToHand() no longer mentions " + needle + ": testing the wrong code.");
    process.exit(1);
  }
}
// The premise of where the flight lands: applyHandLayout holds the fan transform
// back until a new card's deal-in animation has ended. If it ever stops doing
// that, the landing box below is the wrong answer and this says so.
if (!/deal-in[\s\S]*animationend[\s\S]*targetTransform/.test(FAN_APPLY)) {
  console.log("FAIL: applyHandLayout no longer waits for deal-in to end before it fans a card,");
  console.log("      so a pick-up should now be landing on the fanned position instead.");
  process.exit(1);
}

const PAGE = '<!doctype html><html><head><meta charset="utf-8"><title>pick up</title>\n'
+ "<style>" + CSS + "</style>\n"
+ '<style>\n'
+ '  html,body { margin:0; padding:0; overflow:hidden; }\n'
+ '  /* A hand, a Pool and a deck, placed where preview.html puts them, so the\n'
+ '     flight has real screen coordinates to start from and land on. */\n'
+ '  :root { --hand-card-w: 98px; --hand-card-h: 137px; }\n'
+ '  #pv-hand { position:fixed; left:50%; bottom:18px; transform:translateX(-50%);\n'
+ '             display:flex; align-items:flex-end; }\n'
+ '  #pv-draw-deck { position:fixed; left:24px; bottom:200px; width:70px; height:98px; }\n'
+ '  #pv-draw-deck .pv-deck-face { position:absolute; inset:0; background:#2A5AA0; }\n'
+ '  #pv-pool-wrap { position:fixed; left:50%; top:16px; transform:translateX(-50%);\n'
+ '                  display:flex; gap:6px; }\n'
+ '  #pv-pool-wrap .pv-pool-card { position:relative; width:70px; height:98px; background:#2a6; }\n'
+ '</style></head><body>\n'
+ '<div id="pv-anim-layer"></div>\n'
+ '<div id="pv-draw-deck"><div class="pv-deck-face"></div></div>\n'
+ '<div id="pv-pool-wrap">\n'
+ '  <div class="pv-pool-card" data-face-uid="201"></div>\n'
+ '  <div class="pv-pool-card" data-face-uid="202"></div>\n'
+ '</div>\n'
+ '<div id="pv-hand">\n'
+ '  <div class="pv-hand-card" data-entry-uid="1" data-face-uid="301"></div>\n'
+ '  <div class="pv-hand-card" data-entry-uid="2" data-face-uid="302"></div>\n'
+ '  <div class="pv-hand-card" data-entry-uid="3" data-face-uid="303"></div>\n'
+ '  <div class="pv-hand-card" data-entry-uid="4" data-face-uid="304"></div>\n'
+ '  <div class="pv-hand-card deal-in" data-entry-uid="5" data-face-uid="201"></div>\n'
+ '</div>\n'
+ '<div id="out">RUNNING</div>\n'
+ '<script>\n'
+ 'window.onerror=function(m,f,l){document.getElementById("out").textContent="FAIL threw: "+m+" @line "+l;};\n'
+ '</script>\n'
+ '<script>\n'
+ '(function () {\n'
+ '  // ── Stubs for what the slices close over ─────────────────────────\n'
+ '  var running = 0;\n'
+ '  function layer() { return document.getElementById("pv-anim-layer"); }\n'
+ '  function imagePathForUid(uid) { return "assets/cards/" + uid + ".png"; }\n'
+ '  function rectOf(el) {\n'
+ '    if (!el) return null;\n'
+ '    var r = el.getBoundingClientRect();\n'
+ '    return { left: r.left, top: r.top, width: r.width, height: r.height };\n'
+ '  }\n'
+ '  var _handCardEls = Array.prototype.slice.call(document.querySelectorAll("#pv-hand .pv-hand-card"));\n'
+ PICK_CONSTS + '\n'
+ HAND_TFORMS + '\n'
+ LAND_RECT + '\n'
+ MAKE_ANIM_CARD + '\n'
+ PLAY_RIPPLE + '\n'
+ PICK_UP + '\n'
+ '\n'
+ '  var results = [];\n'
+ '  function ok(cond, m) { results.push((cond ? "PASS " : "FAIL ") + m); }\n'
+ '  function note(m) { results.push("NOTE " + m); }\n'
+ '  function report() { document.getElementById("out").textContent = results.join("\\n"); }\n'
+ '  function ctr(r) { return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }\n'
+ '\n'
+ '  var BASE_W = 90, BASE_H = 126;   // .pv-anim-card, from preview.css\n'
+ '  var slot = document.querySelector(\'.pv-pool-card[data-face-uid="201"]\');\n'
+ '  var deckFace = document.querySelector("#pv-draw-deck .pv-deck-face");\n'
+ '  var poolCard = document.querySelector(\'#pv-hand .pv-hand-card[data-face-uid="201"]\');\n'
+ '  var deckCard = document.querySelector(\'#pv-hand .pv-hand-card[data-face-uid="303"]\');\n'
+ '  var liveCard = document.querySelector(\'#pv-hand .pv-hand-card[data-face-uid="304"]\');\n'
+ '\n'
+ '  // ── A. Where the flight hands over ──────────────────────────────\n'
+ '  // The arriving card is mid deal-in, scaled to little over half size and\n'
+ '  // still rising, and it is NOT in the fan yet (applyHandLayout waits for that\n'
+ '  // animation to end). Seek its deal-in to the end and the card is exactly\n'
+ '  // where the clone has to hand over: square in its own slot.\n'
+ '  var da = poolCard.getAnimations()[0];\n'
+ '  if (!da) { ok(false, "the arriving hand card is not playing its deal-in at all"); }\n'
+ '  else {\n'
+ '    da.pause();\n'
+ '    var mid = rectOf(poolCard);\n'
+ '    da.currentTime = da.effect.getTiming().duration;\n'
+ '    var surfaced = rectOf(poolCard);\n'
+ '    var landing  = handCardLandingRect(poolCard);\n'
+ '    var ls = ctr(landing), ss = ctr(surfaced);\n'
+ '    ok(Math.abs(ls.x - ss.x) <= 1 && Math.abs(ls.y - ss.y) <= 1\n'
+ '       && Math.abs(landing.width - surfaced.width) <= 1,\n'
+ '       "the flight hands over exactly where the arriving card is painted ("\n'
+ '       + Math.round(landing.width) + "px at " + Math.round(ls.x) + "," + Math.round(ls.y)\n'
+ '       + " against " + Math.round(surfaced.width) + "px at " + Math.round(ss.x) + ","\n'
+ '       + Math.round(ss.y) + ")");\n'
+ '    ok(Math.abs(mid.width - surfaced.width) > 20,\n'
+ '       "and not where the card merely IS mid-flight: " + Math.round(mid.width)\n'
+ '       + "px, against the " + Math.round(surfaced.width) + "px it surfaces to");\n'
+ '    da.currentTime = 0; da.play();\n'
+ '  }\n'
+ '  // What handing over at the fanned position would have cost, in the fan\n'
+ '  // maths the game itself uses: this is the jump the layout box avoids.\n'
+ '  var fanEnd = computeHandTransforms(10, -1)[9];\n'
+ '  var fanOff = Math.abs(Math.cos(fanEnd.angle * Math.PI / 180) * fanEnd.translateY);\n'
+ '  note("at the end of a ten-card fan the arc sits " + fanOff.toFixed(0)\n'
+ '       + "px from the layout box, which is the jump that would have been");\n'
+ '\n'
+ '  // ── E. Short ─────────────────────────────────────────────────────\n'
+ '  ok(PICKUP_MS <= 350, "the whole pick-up is " + PICKUP_MS + "ms, short enough to happen every turn");\n'
+ '\n'
+ '  // All three flights are started in this one turn, because a rAF asked for\n'
+ '  // from inside a rAF callback is never served in headless Chrome: the page\n'
+ '  // gets its frames and then stops being drawn. One frame has to do.\n'
+ '  var slotR = rectOf(slot), deckR = rectOf(deckFace);\n'
+ '  var poolTo = handCardLandingRect(poolCard);\n'
+ '  pickUpToHand(slotR, poolTo, poolCard, { faceUid: 201 });\n'
+ '  pickUpToHand(deckR, handCardLandingRect(deckCard), deckCard, { faceUid: 303, flip: true });\n'
+ '  pickUpToHand(slotR, handCardLandingRect(liveCard), liveCard, { faceUid: 202 });\n'
+ '\n'
+ '  // ── F (first half). The hand card waits, and keeps its deal-in ───\n'
+ '  ok(poolCard.style.visibility === "hidden",\n'
+ '     "the hand card is hidden the moment the clone is in the air");\n'
+ '  ok(poolCard.classList.contains("deal-in"),\n'
+ '     "and keeps its deal-in, which applyHandLayout waits to END before it fans the card");\n'
+ '\n'
+ '  // Two frames: one for the appendChild, one for the rAF each flight waits on.\n'
+ '  requestAnimationFrame(function () { requestAnimationFrame(function () {\n'
+ '    var clones = document.querySelectorAll("#pv-anim-layer .pv-anim-card.pv-pickup");\n'
+ '    if (clones.length < 3) { ok(false, "only " + clones.length + " of 3 cards went in the air"); report(); return; }\n'
+ '    var pc = clones[0], dc = clones[1];\n'
+ '    var pa = pc.getAnimations()[0], da = dc.getAnimations()[0];\n'
+ '    if (!pa || !da) { ok(false, "a card in the air was never animated"); report(); return; }\n'
+ '    pa.pause(); da.pause();\n'
+ '    ok(pa.effect.getTiming().duration === PICKUP_MS,\n'
+ '       "the flight lasts the whole pick-up (" + pa.effect.getTiming().duration + "ms)");\n'
+ '    function at(ms) { pa.currentTime = ms; return pc.getBoundingClientRect(); }\n'
+ '\n'
+ '    // ── B. It starts on the Pool slot it came from ─────────────────\n'
+ '    var r0 = at(0), c0 = ctr(r0), cs = ctr(slotR);\n'
+ '    ok(Math.abs(c0.x - cs.x) <= 1.5 && Math.abs(c0.y - cs.y) <= 1.5,\n'
+ '       "it starts centred on the Pool slot it came from");\n'
+ '    ok(Math.abs(r0.width - slotR.width) <= 2,\n'
+ '       "at the slot\\u2019s own size (" + Math.round(r0.width) + "px against "\n'
+ '       + Math.round(slotR.width) + "px)");\n'
+ '\n'
+ '    // ── C. Picked UP, then carried ─────────────────────────────────\n'
+ '    var rl = at(PICKUP_LIFT), cl = ctr(rl), ct = ctr(poolTo);\n'
+ '    var travel = Math.abs(ct.y - cs.y);\n'
+ '    ok(cl.y < c0.y - 6, "at the top of the lift it is higher than it started ("\n'
+ '       + (c0.y - cl.y).toFixed(1) + "px up)");\n'
+ '    ok(rl.width > r0.width + 4, "and bigger in the hand that took it ("\n'
+ '       + Math.round(r0.width) + "px to " + Math.round(rl.width) + "px)");\n'
+ '    ok(Math.abs(cl.y - c0.y) < travel * 0.25,\n'
+ '       "but has barely set off: a card is picked up before it is carried");\n'
+ '\n'
+ '    // ── D. It lands on its hand card ───────────────────────────────\n'
+ '    var re = at(PICKUP_MS), ce = ctr(re);\n'
+ '    ok(Math.abs(ce.x - ct.x) <= 2 && Math.abs(ce.y - ct.y) <= 2,\n'
+ '       "it lands centred on the hand card it becomes");\n'
+ '    ok(Math.abs(re.width - poolTo.width) <= 3,\n'
+ '       "at hand-card size (" + Math.round(re.width) + "px against the card\\u2019s "\n'
+ '       + Math.round(poolTo.width) + "px)");\n'
+ '    var mid = ctr(at(PICKUP_MS * 0.6));\n'
+ '    ok(mid.y > cl.y && mid.y < ce.y,\n'
+ '       "and it is on its way there in between, not jumping");\n'
+ '\n'
+ '    // ── G. Off the deck it turns over on the way ───────────────────\n'
+ '    ok(dc.classList.contains("face-down"), "a card off the deck leaves face-down");\n'
+ '    da.currentTime = 0;\n'
+ '    var m0 = new DOMMatrixReadOnly(getComputedStyle(dc).transform);\n'
+ '    da.currentTime = PICKUP_MS;\n'
+ '    var m1 = new DOMMatrixReadOnly(getComputedStyle(dc).transform);\n'
+ '    // rotateY(180deg) at scale S reads as m11 = -S, and the face child carries\n'
+ '    // its own rotateY(180deg): -S x -1 = +S, a face the right way round.\n'
+ '    // Positive m11 at the start is the BACK of the card.\n'
+ '    ok(m0.m11 > 0 && m1.m11 < 0,\n'
+ '       "and has turned a full 180deg by the time it arrives (m11 " + m0.m11.toFixed(2)\n'
+ '       + " to " + m1.m11.toFixed(2) + ")");\n'
+ '    var faceM11 = new DOMMatrixReadOnly(getComputedStyle(dc.querySelector(".pv-anim-face")).transform).m11;\n'
+ '    ok(Math.abs(faceM11 + 1) <= 0.01, "with the art the right way round, not mirrored");\n'
+ '\n'
+ '    // ── F (second half). The card comes back when it lands ─────────\n'
+ '    // Real time for this one, not seeking: the hand card is handed back by\n'
+ '    // the flight finishing, which a paused animation never does. The third\n'
+ '    // clone is the one left running.\n'
+ '    setTimeout(function () {\n'
+ '      ok(liveCard.style.visibility === "hidden",\n'
+ '         "mid-flight, the card in the fan is still its clone\\u2019s to show");\n'
+ '      setTimeout(function () {\n'
+ '        ok(liveCard.style.visibility !== "hidden",\n'
+ '           "and it is back the moment the clone lands");\n'
+ '        ok(!document.querySelector("#pv-anim-layer .pv-anim-card.pv-pickup"),\n'
+ '           "with nothing of any clone left behind in the air");\n'
+ '        report();\n'
+ '      }, PICKUP_MS + 400);\n'
+ '    }, Math.round(PICKUP_MS / 3));\n'
+ '  }); });\n'
+ '})();\n'
+ '</script></body></html>';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "card-pickup-"));
const file = path.join(tmp, "pickup.html");
fs.writeFileSync(file, PAGE);

let checks = 0, failures = 0;
const WINDOWS = [
  { label: "desktop 1512x857", size: "1512,857" },
  { label: "phone 390x844",    size: "390,844"  },
];

// Every measurement here hangs off requestAnimationFrame, and headless Chrome
// starves rAF outright on some runs: the page never gets a frame, #out is still
// RUNNING when the DOM is dumped, and nothing has been measured either way. A
// profile of its own per launch and a retry are both needed; see the same note
// in test_ocean_flip_reveal.js, which this follows.
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
console.log("The card you pick up travels to your hand.");
