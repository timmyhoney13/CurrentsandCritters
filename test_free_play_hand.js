#!/usr/bin/env node
/* A free play has to point at the cards it is talking about.
 *
 * A ★ can hand you "play a free Mammal", "play a free Baitfish", a free Game
 * Fish, Cephalopod, Crustacean, Invertebrate, Coral, or two free Yellowfin
 * Tuna. The game knew all of this and said almost none of it: a line of text
 * naming the species, pointing at a dropdown, while the actual Mammals sat
 * unmarked in a fanned hand of ten. Players missed free plays and ended the
 * turn without taking them.
 *
 * So:
 *   • THE WINDOW ANNOUNCES ITSELF, once, when it opens, and says what you got.
 *     Once: it is driven by the transition, not by the 1s poll, or it would
 *     re-fire forever.
 *   • THE CARDS IT MEANS LIGHT UP, in the hand, for as long as the window is
 *     open, matched on species AND on card name (Yellowfin Tuna is granted by
 *     name, not by species, and used to be the one free play nothing marked).
 *   • THE SERVER TELLS THE CLIENT ABOUT ALL OF THEM, Yellowfin Tuna included.
 *
 * Run:  node test_free_play_hand.js       (render half needs Google Chrome)
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
const SERVER = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");
const ENGINE = fs.readFileSync(path.join(ROOT, "fish_game_all_in_one.py"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

// ── 1. The server offers every free play the engine can grant ───────────────
console.log("\nthe server names every free play the engine can hand out");

// Every flag the engine sets in is_free_play_eligible() is a free play the
// player can take, so every one of them has to reach the client. This reads
// the ENGINE for the list rather than hard-coding it, so a new free-play card
// fails here instead of shipping with nothing marked.
const eligible = (() => {
  const i = ENGINE.indexOf("def is_free_play_eligible(");
  if (i < 0) return "";
  const j = ENGINE.indexOf("\ndef ", i + 10);
  return ENGINE.slice(i, j < 0 ? i + 3000 : j);
})();
check("is_free_play_eligible() was found in the engine", eligible.length > 200);

const engineFlags = [...new Set(
  (eligible.match(/flags\.get\("([a-z_]+)"/g) || []).map(s => s.slice(11, -1))
)];
check(`the engine's free-play flags were read (${engineFlags.join(", ")})`, engineFlags.length >= 7);

// The one table in multiplayer_server.py that turns those flags into the words
// the client shows. test_free_play_species.py drives the same table for real.
const fpsBlock = (() => {
  const i = SERVER.indexOf("_FREE_PLAY_SPECIES_FLAGS: List[");
  return i < 0 ? "" : SERVER.slice(i, SERVER.indexOf("\n]", i));
})();
check("the server's free-play table was found", fpsBlock.length > 200);
check("…and the payload is built from it, not from a second copy",
      /free_play_species: List\[str\] = free_play_species_for\(player\)/.test(SERVER));
for (const flag of engineFlags) {
  check(`  ${flag} reaches the client`, fpsBlock.includes(`"${flag}"`),
        "the engine lets this one be played free, the client is never told");
}
check("the Yellowfin Tuna is offered by name",
      /Yellowfin Tuna/.test(fpsBlock),
      "it is granted by card name, not species, so a species-only list misses it");

// ── 2. The client knows which cards those are ───────────────────────────────
console.log("\nthe client turns that list into the cards in your hand");

check("one helper answers 'what can I play free right now'",
      /function freePlayPicksNow\(/.test(APP));
check("the hand marks the cards it names", /free-play-pick/.test(APP));

// renderHand() skips the whole redraw when its cache key is unchanged, so a
// window that opens between two identical hands would never paint the glow.
const HAND_FN = (() => {
  const i = APP.indexOf("function renderHand(me, actions, mustDiscard, discardExcess) {");
  return i < 0 ? "" : APP.slice(i, APP.indexOf("\n  function ", i + 10));
})();
check("renderHand() was found", HAND_FN.length > 1000);
check("the free-play window is part of the hand's cache key",
      /key = JSON\.stringify\(\{[\s\S]{0,600}freePlay/.test(HAND_FN),
      "the glow would not appear until something else forced a redraw");
check("…and the hand really applies the class",
      /classList\.add\("free-play-pick"\)/.test(HAND_FN));
// Matched on BOTH, or the Yellowfin Tuna (granted by name) never lights up
// and a species-named window would miss nothing else.
const PICK_BLOCK = (() => {
  const i = HAND_FN.indexOf("_freePlayPicks.size");
  return i < 0 ? "" : HAND_FN.slice(i, i + 700);
})();
check("…matched on the card's species AND its name",
      /\.species/.test(PICK_BLOCK) && /\.name/.test(PICK_BLOCK), PICK_BLOCK.slice(0, 80));
check("…on either face of a two-sided card", /entry\.faces/.test(PICK_BLOCK));

// ── 3. It announces itself once ─────────────────────────────────────────────
console.log("\nit says what you got, once");

check("there is somewhere to say it", /id="pv-turn-flash"/.test(HTML));
check("one function owns every board announcement", /function flashBoard\(variant, title, line\)/.test(APP));
check("it is driven by the change, not by the poll",
      /_prevFreePlayKey/.test(APP),
      "a 1s poll would re-fire the flash forever");
// Scoped to the function, so a comment growing between the two does not
// quietly turn this check off.
const FLASH_FN = (() => {
  const i = APP.indexOf("function maybeFlashFreePlay(");
  return i < 0 ? "" : APP.slice(i, APP.indexOf("\n  // ── Action bar renderer", i));
})();
check("maybeFlashFreePlay() was found", FLASH_FN.length > 400);
check("a ★ play-again turn is announced too",
      /is_replay_turn/.test(FLASH_FN) && /PLAY AGAIN/.test(FLASH_FN));
check("…and a free play says how many of those cards you are holding",
      /held/.test(FLASH_FN) && /in your hand/.test(FLASH_FN));
check("…and says so plainly when you hold none",
      /No \$\{what\} in your hand/.test(FLASH_FN));
check("it is polite to a screen reader", /id="pv-turn-flash"[^>]*aria-live/.test(HTML));

// House rule: no em dashes in anything a player reads.
const flashFn = (() => {
  const i = APP.indexOf("function flashBoard(");
  return i < 0 ? "" : APP.slice(i, APP.indexOf("\n  function ", i + 10));
})();
check("no em dashes in what it says", !/[—–]/.test(flashFn), "player-facing strings use plain punctuation");

// ── 4. The guide bar points at the hand, not only at the dropdown ───────────
console.log("\nthe guide bar sends you to your hand");
const GUIDE_FN = (() => {
  const i = APP.indexOf("function renderGuideBar(");
  return i < 0 ? "" : APP.slice(i, APP.indexOf("\n  // ── \"Play again\"", i));
})();
check("renderGuideBar() was found", GUIDE_FN.length > 500);
check("it names the species", /speciesStr/.test(GUIDE_FN));
// Scoped to the line a player reads when the window is OPEN and they are
// holding something, not to the "no eligible card in hand" line below it.
const FP_LINE = (() => {
  const i = GUIDE_FN.indexOf("★ FREE PLAY,");
  return i < 0 ? "" : GUIDE_FN.slice(i, GUIDE_FN.indexOf("</span>", i));
})();
check("the open-window line was found", FP_LINE.length > 40);
check("…and it sends you to the lit cards in your hand",
      /lit up in your hand/.test(FP_LINE), FP_LINE.slice(0, 120));

// ── Render ──────────────────────────────────────────────────────────────────
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: skipping the render half.");
} else {
  const CHROME_CHROME_PX = 87;

  // The real picker, sliced out of preview-app.js, so this tests the shipped
  // matching rule and not a second copy of it written here.
  const pickStart = APP.indexOf("  function freePlayPicksNow(");
  const pickEnd   = pickStart < 0 ? -1 : APP.indexOf("\n  }", pickStart) + 4;
  if (pickStart < 0) { console.log("\nFAIL: freePlayPicksNow() is gone"); process.exit(1); }
  const PICKER = APP.slice(pickStart, pickEnd);

  // A hand of real species, three of which are Mammals.
  const HAND = [
    ["Humpback Whale", "Mammal"], ["Anchovy", "Baitfish"], ["Sea Otter", "Mammal"],
    ["Yellowfin Tuna", "Game Fish"], ["Giant Squid", "Cephalopod"], ["Spinner Dolphin", "Mammal"],
    ["Blue Coral", "Coral"], ["Sand Dollar", "Invertebrate"],
  ];

  const page = `<!doctype html><html><head><meta charset="utf-8"><style>${CSS}</style>
<style>html,body{margin:0;background:#06244a}
  #pv-hand{display:flex;padding:40px 20px}</style></head>
<body>
<div id="pv-hand"></div>
<div id="pv-turn-flash" aria-live="polite"></div>
<pre id="out">RUNNING</pre>
<script>
var latestPayload = { legal_actions: { free_play_species: ["Mammal"] } };
${PICKER}
(function(){
  var HAND = ${JSON.stringify(HAND)};
  var zone = document.getElementById("pv-hand");
  var picks = freePlayPicksNow();
  HAND.forEach(function(row){
    var c = document.createElement("div");
    c.className = "pv-hand-card";
    c.dataset.species = row[1];
    c.dataset.cardName = row[0];
    if (picks.has(String(row[1]).toLowerCase()) || picks.has(String(row[0]).toLowerCase())) {
      c.classList.add("free-play-pick");
    }
    var inner = document.createElement("div"); inner.className = "pv-card-inner";
    c.appendChild(inner); zone.appendChild(c);
  });
  function sty(el){ var s = getComputedStyle(el.querySelector(".pv-card-inner"));
    return { border: s.borderTopColor, shadow: s.boxShadow }; }
  // A free-play card that is ALSO mid-action: the action must win, or the
  // breathing gold paints straight over the hover and the discard.
  function withState(extra){
    var c = document.createElement("div");
    c.className = "pv-hand-card free-play-pick " + extra;
    c.dataset.species = "Mammal";
    var i2 = document.createElement("div"); i2.className = "pv-card-inner";
    c.appendChild(i2); zone.appendChild(c);
    var tag = getComputedStyle(c, "::after");
    return { border: getComputedStyle(i2).borderTopColor,
             anim: getComputedStyle(i2).animationName,
             tagShown: tag.display !== "none" };
  }
  var lit  = [].filter.call(zone.children, function(c){ return c.classList.contains("free-play-pick"); });
  var dark = [].filter.call(zone.children, function(c){ return !c.classList.contains("free-play-pick"); });
  // The tag that names it, drawn with ::after so no markup changes per card.
  var tag = lit.length ? getComputedStyle(lit[0], "::after") : null;
  var tagBox = lit.length ? lit[0].getBoundingClientRect() : null;
  // Added only now, so they cannot pollute the lit/dark census above.
  var stHover   = withState("hovered");
  var stDiscard = withState("discard-selected");
  var stPay     = withState("pay-selected");
  document.getElementById("out").textContent = JSON.stringify({
    litNames:  lit.map(function(c){ return c.dataset.cardName; }),
    darkNames: dark.map(function(c){ return c.dataset.cardName; }),
    litStyle:  lit.length  ? sty(lit[0])  : null,
    darkStyle: dark.length ? sty(dark[0]) : null,
    different: lit.length && dark.length
               && sty(lit[0]).border !== sty(dark[0]).border,
    tagContent: tag ? tag.content : "",
    // The fan overlaps each card by 28px of its RIGHT edge, so anything the
    // tag has to say must live in the visible LEFT strip.
    tagLeft: tag ? parseFloat(tag.left) : null,
    cardW: tagBox ? Math.round(tagBox.width) : null,
    stHover: stHover, stDiscard: stDiscard, stPay: stPay
  });
})();
</script></body></html>`;

  const f = path.join(os.tmpdir(), "cc_free_play_hand.html");
  fs.writeFileSync(f, page);
  const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
    "--hide-scrollbars", `--window-size=1280,${700 + CHROME_CHROME_PX}`,
    "--virtual-time-budget=5000", "--dump-dom", "file://" + f],
    { encoding: "utf8", maxBuffer: 64e6 });
  const m = /<pre id="out">([\s\S]*?)<\/pre>/.exec(dom);
  const r = JSON.parse((m ? m[1] : "{}").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));

  console.log("\nrendered: a free Mammal window over a real hand");
  check(`exactly the Mammals light up (${JSON.stringify(r.litNames)})`,
        Array.isArray(r.litNames) && r.litNames.length === 3
        && r.litNames.every(n => ["Humpback Whale", "Sea Otter", "Spinner Dolphin"].includes(n)));
  check("nothing else does", Array.isArray(r.darkNames) && r.darkNames.length === 5);
  check(`a lit card does not look like a dark one (${r.litStyle && r.litStyle.border} vs ${r.darkStyle && r.darkStyle.border})`,
        r.different);
  check("…and it is lit, not just outlined", r.litStyle && /rgb/.test(r.litStyle.shadow));
  check(`each one is tagged (${r.tagContent})`, typeof r.tagContent === "string" && /FREE/i.test(r.tagContent));
  check(`the tag sits in the strip the next card does not cover (left ${r.tagLeft}px of ${r.cardW}px)`,
        r.tagLeft !== null && r.tagLeft < (r.cardW - 28));

  console.log("\n…and it gets out of the way of whatever you are doing");
  check(`hovering one still shows the hover, not the gold (${r.stHover && r.stHover.border})`,
        r.stHover && r.stHover.border === "rgb(34, 216, 248)" && r.stHover.anim === "none");
  check(`choosing one to discard reads as a discard (${r.stDiscard && r.stDiscard.border})`,
        r.stDiscard && r.stDiscard.border === "rgb(232, 64, 87)" && r.stDiscard.anim === "none");
  check(`paying with one reads as a payment (${r.stPay && r.stPay.border})`,
        r.stPay && r.stPay.border === "rgb(66, 200, 120)" && r.stPay.anim === "none");
  check("…and the FREE tag goes once the card is spoken for",
        r.stDiscard && !r.stDiscard.tagShown && r.stPay && !r.stPay.tagShown);
  check("…but hovering keeps it, because hovering tells you nothing new",
        r.stHover && r.stHover.tagShown);

  // The same hand with a Yellowfin Tuna window: the one granted by NAME.
  const page2 = page.replace('free_play_species: ["Mammal"]', 'free_play_species: ["Yellowfin Tuna"]');
  const f2 = path.join(os.tmpdir(), "cc_free_play_hand_yft.html");
  fs.writeFileSync(f2, page2);
  const dom2 = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
    "--hide-scrollbars", `--window-size=1280,${700 + CHROME_CHROME_PX}`,
    "--virtual-time-budget=5000", "--dump-dom", "file://" + f2],
    { encoding: "utf8", maxBuffer: 64e6 });
  const m2 = /<pre id="out">([\s\S]*?)<\/pre>/.exec(dom2);
  const r2 = JSON.parse((m2 ? m2[1] : "{}").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));

  console.log("\nrendered: the Yellowfin Tuna window, granted by name");
  check(`only the Yellowfin Tuna lights up (${JSON.stringify(r2.litNames)})`,
        Array.isArray(r2.litNames) && r2.litNames.length === 1 && r2.litNames[0] === "Yellowfin Tuna");
  check("the other Game Fish-ish cards stay dark",
        Array.isArray(r2.darkNames) && r2.darkNames.length === 7);
}

console.log("\n" + "=".repeat(42));
console.log(`RESULT: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
