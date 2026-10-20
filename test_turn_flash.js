#!/usr/bin/env node
/* The board announcement: YOUR TURN, and the ★ that outranks it.
 *
 * The turn banner STATES whose turn it is, for as long as the turn lasts. It
 * cannot announce the moment the turn arrives, because it looks the same a
 * second later as it did a minute later, and on a phone the banner is a
 * whole screen away from the hand you are looking at. This is the arrival.
 *
 * What this file holds to:
 *
 *   1. ONE ANNOUNCEMENT. A turn is never announced by two different-looking
 *      things at once: one element, one function, and a ★ outranks the plain
 *      turn, so a play-again turn says "PLAY AGAIN" and not both.
 *   2. IT BELONGS TO THIS GAME. The pale aqua of every other in-game dialog
 *      (#end-turn-box), navy Cinzel lettering, the table's gold only on a ★.
 *      Not a dark slab dropped over a bright table.
 *   3. IT IS CLEAN. Transform and opacity only, so it never costs a layout;
 *      it takes no clicks; ONE css animation owns its whole life, so no timer
 *      can strand it on the board; and it stands down for reduced motion.
 *
 * Run:  node test_turn_flash.js        (render half needs Google Chrome)
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const APP  = read("js/preview-app.js");
const HTML = read("preview.html");
const CSS  = read("css/preview.css");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

const FLASH_CSS = (() => {
  const i = CSS.indexOf("#pv-turn-flash {");
  return i < 0 ? "" : CSS.slice(i, CSS.indexOf("#pv-toasts {"));
})();
const DECIDE_FN = (() => {
  const i = APP.indexOf("function maybeFlashFreePlay(");
  return i < 0 ? "" : APP.slice(i, APP.indexOf("\n  // ── Action bar renderer", i));
})();

// ── 1. One announcement ─────────────────────────────────────────────────────
console.log("\nthe turn is announced once, by one thing");

check("the element exists", /id="pv-turn-flash"/.test(HTML));
check("…and reads itself out to a screen reader",
      /id="pv-turn-flash"[^>]*aria-live="polite"/.test(HTML));
check("one function draws every announcement",
      /function flashBoard\(variant, title, line\)/.test(APP));
check("maybeFlashFreePlay() decides which one", DECIDE_FN.length > 400);
check("an ordinary turn says YOUR TURN", /flashBoard\("tf-turn", "YOUR TURN"/.test(DECIDE_FN));
check("a ★ outranks it, so you never get both",
      /if \(!replay && species\.length === 0\) \{[\s\S]{0,400}flashBoard\("tf-turn"/.test(DECIDE_FN)
      && /return;/.test(DECIDE_FN));
check("…and the ★ ones are the other variant",
      (DECIDE_FN.match(/flashBoard\("tf-star"/g) || []).length === 2);
// Joining mid-game must not announce a turn that has been yours for a while.
// Two guards, and the sequence test below proves what each one is for:
// firstSeen (joining on a turn that was already yours) and sameTurn (a free
// play closing inside the turn you are already taking).
check("it is guarded against announcing a turn twice",
      /if \(!firstSeen && !sameTurn\) flashBoard\("tf-turn"/.test(DECIDE_FN));
check("it still fires on the CHANGE, not on the 1s poll",
      /if \(key === _prevFreePlayKey\) return;/.test(DECIDE_FN));

// ── 2. It belongs to this game ──────────────────────────────────────────────
console.log("\nit looks like the rest of the table");

check("its CSS block was found", FLASH_CSS.length > 600);
// The exact shell every other in-game dialog wears (#end-turn-box).
const DIALOG_BG = "linear-gradient(160deg, #e4f5fb 0%, #cae9f7 65%, #b0dbf1 100%)";
check("it is the in-game dialog's pale aqua, not a dark slab",
      FLASH_CSS.includes(DIALOG_BG),
      "an overlay over a bright table must not be a dark navy panel");
check("…with the same teal edge", /border:\s*2px solid rgba\(95,179,214,\.6\)/.test(FLASH_CSS));
check("the lettering is the table's navy", /\.tf-title[\s\S]{0,260}color:\s*var\(--cr-navy\)/.test(FLASH_CSS));
check("…in the game's heading face", /\.tf-title[\s\S]{0,160}font-family:\s*"Cinzel"/.test(FLASH_CSS));
check("gold is kept for the ★, where the table uses it",
      /\.tf-star[\s\S]{0,300}var\(--cr-gold\)/.test(FLASH_CSS));
// House rules for anything a player reads.
check("no em dashes in what it says", !/[—–]/.test(DECIDE_FN));
// ★ (U+2605) is the game's own typographic star, used by the turn banner and
// the guide bar, and it renders as a text glyph that obeys CSS colour, not as
// a colour emoji. Everything in the pictographic blocks is out.
check("no emoji in what it says",
      !/[\u{1F300}-\u{1FAFF}\u{FE0F}\u{1F000}-\u{1F2FF}]/u.test(DECIDE_FN));
check("…and the star it does use is the game's own",
      DECIDE_FN.includes("\u2605 PLAY AGAIN"));

// ── 3. It is clean ──────────────────────────────────────────────────────────
console.log("\nit is cheap to draw and it cleans up after itself");

check("it takes no clicks", /pointer-events:\s*none/.test(FLASH_CSS));
// Every animated property must be transform or opacity: anything else makes
// an every-turn animation cost a layout or a repaint of the board under it.
const animatedProps = (() => {
  const bad = new Set();
  const kf = FLASH_CSS.match(/@keyframes\s+tf-[a-z]+\s*\{[\s\S]*?\n    \}/g) || [];
  kf.forEach(block => {
    (block.match(/^\s*([a-z-]+):/gm) || []).forEach(decl => {
      const prop = decl.trim().replace(":", "");
      // animation-timing-function is timing metadata, not a property being
      // animated: it is how each leg of the swell is eased.
      if (!["transform", "opacity", "animation-timing-function"].includes(prop)) bad.add(prop);
    });
  });
  return [...bad];
})();
check(`the animations move nothing but transform and opacity (${JSON.stringify(animatedProps)})`,
      animatedProps.length === 0);
// Promoted while it is on screen, and NOT a moment longer: a permanent
// will-change pins a compositor layer for the whole match.
check("…and it is promoted only while it is showing",
      /#pv-turn-flash\.show \{[^}]*will-change:\s*transform,\s*opacity/.test(FLASH_CSS));
check("…never permanently", !/#pv-turn-flash \{[^}]*will-change/.test(FLASH_CSS));
check("one animation owns the whole life of it, hold and all",
      /@keyframes tf-swell/.test(FLASH_CSS) && /78%/.test(FLASH_CSS));
check("the class comes off at animationend, not on a timer",
      /addEventListener\("animationend"[\s\S]{0,220}classList\.remove\("show"\)/.test(APP));
check("…and no timer is left owning it", !/_freePlayFlashTimer/.test(APP));
check("a restart is forced cleanly, so a second turn re-plays it",
      /classList\.remove\("show", "tf-turn", "tf-star"\)[\s\S]{0,120}offsetWidth/.test(APP));
check("reduced motion stops it travelling but still says its piece",
      /prefers-reduced-motion[\s\S]{0,420}tf-fade/.test(FLASH_CSS));
check("the ★ holds longer than an every-turn announcement", (() => {
  const t = /\.tf-turn \{ animation: tf-swell (\d+)ms/.exec(FLASH_CSS);
  const s = /\.tf-star \{[\s\S]{0,320}?animation: tf-swell (\d+)ms/.exec(FLASH_CSS);
  return t && s && Number(s[1]) > Number(t[1]);
})());

// ── The real decision, run over a real sequence of turns ────────────────────
// The function is sliced out of preview-app.js and run against stubs, so this
// is the shipped rule deciding, not a second copy of it written here.
console.log("\nover a whole game, it speaks exactly when it should");
{
  const start = APP.indexOf("  function maybeFlashFreePlay(");
  const end   = APP.indexOf("\n  // ── Action bar renderer", start);
  if (start < 0) { console.log("  ✗ FAIL: maybeFlashFreePlay() is gone"); fail++; }
  else {
    const said = [];
    const sandbox = `
      let _prevFreePlayKey = "";
      let _prevTurnAnnounced = "";
      function flashBoard(variant, title, line) { said.push(variant + ":" + title); }
      ${APP.slice(start, end)}
      return maybeFlashFreePlay;
    `;
    const decide = new Function("said", sandbox)(said);
    const hand = { hand: [ { faces: [{ species: "Mammal", name: "Sea Otter" }] },
                           { faces: [{ species: "Coral",  name: "Blue Coral" }] } ] };
    const step = (opts) => decide(
      { is_replay_turn: !!opts.replay }, hand, !!opts.mine, opts.free || [], opts.turn);

    // Join while somebody else is playing, then take a normal turn.
    step({ mine: false, turn: 4 });
    step({ mine: true,  turn: 5 });
    step({ mine: true,  turn: 5 });          // the 1s poll, same turn
    step({ mine: false, turn: 6 });
    step({ mine: true,  turn: 7 });
    check(`a turn is announced once, as it arrives (${JSON.stringify(said)})`,
          said.join("|") === "tf-turn:YOUR TURN|tf-turn:YOUR TURN");

    // A ★ opens a free Mammal window inside turn 7, then it is spent.
    said.length = 0;
    step({ mine: true, turn: 7, free: ["Mammal"] });
    step({ mine: true, turn: 7, free: ["Mammal"] });   // poll
    step({ mine: true, turn: 7 });                     // free play used up
    check(`the ★ speaks once and closing it says nothing (${JSON.stringify(said)})`,
          said.join("|") === "tf-star:★ FREE MAMMAL");

    // A ★ "play again" hands over turn 8, and another straight after it.
    said.length = 0;
    step({ mine: true, replay: true, turn: 8 });
    step({ mine: true, replay: true, turn: 9 });
    check(`two extra turns in a row are both announced (${JSON.stringify(said)})`,
          said.join("|") === "tf-star:★ PLAY AGAIN|tf-star:★ PLAY AGAIN");

    // Joining a game where it is ALREADY your turn must stay quiet.
    said.length = 0;
    const fresh = new Function("said", sandbox)(said);
    fresh({ is_replay_turn: false }, hand, true, [], 12);
    check(`joining on your own turn announces nothing (${JSON.stringify(said)})`,
          said.length === 0);

    // It counts what you are holding, and says so when you hold none.
    said.length = 0;
    const f2 = new Function("said", sandbox)(said);
    f2({ is_replay_turn: false }, hand, false, [], 1);
    f2({ is_replay_turn: false }, hand, true, ["Baitfish"], 2);
    check(`a free play you cannot take still says so (${JSON.stringify(said)})`,
          said.join("") === "tf-star:★ FREE BAITFISH");
  }
}

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

  // The real element and the real stylesheet, over a stand-in for the dark
  // table it actually sits on.
  //
  // The animation is driven through the Web Animations API rather than by
  // waiting: getAnimations() hands back the very animation the stylesheet
  // started, and setting currentTime samples it at an exact moment. Waiting
  // on wall-clock time in headless Chrome measures whatever frame happened to
  // land, which is how this file first "proved" the panel was invisible.
  function sample(variant, title, line, atMs) {
    const html = `<!doctype html><html><head><meta charset="utf-8"><style>${CSS}</style>
<style>html,body{margin:0;height:100%;background:#06244a}</style></head>
<body>
<div id="pv-turn-flash" aria-live="polite" aria-atomic="true"></div>
<pre id="out">RUNNING</pre>
<script>
(function(){
  var el = document.getElementById("pv-turn-flash");
  var t = document.createElement("div"); t.className="tf-title"; t.textContent=${JSON.stringify(title)};
  el.appendChild(t);
  ${line ? `var s=document.createElement("div"); s.className="tf-line"; s.textContent=${JSON.stringify(line)}; el.appendChild(s);` : ""}
  el.classList.remove("show","tf-turn","tf-star");
  void el.offsetWidth;
  el.classList.add("show", ${JSON.stringify(variant)});

  var anims = el.getAnimations();
  var swell = anims.filter(function(a){
    return a.effect && a.effect.getTiming && a.effect.getTiming().duration > 100;
  });
  swell.forEach(function(a){ a.pause(); a.currentTime = ${atMs}; });
  var dur = swell.length ? swell[0].effect.getTiming().duration : 0;

  var r  = el.getBoundingClientRect();
  var cs = getComputedStyle(el), ts = getComputedStyle(t);
  var VW = innerWidth, VH = innerHeight;
  var cx = r.left + r.width/2, cy = r.top + r.height/2;
  var hit = (cx>0&&cy>0&&cx<VW&&cy<VH) ? document.elementFromPoint(cx, cy) : null;
  document.getElementById("out").textContent = JSON.stringify({
    animCount: anims.length,
    duration: dur,
    opacity: Number(cs.opacity).toFixed(2),
    transform: cs.transform,
    w: Math.round(r.width), h: Math.round(r.height),
    onScreen: r.top >= 0 && r.left >= 0 && r.right <= VW + 1 && r.bottom <= VH + 1,
    // Nothing may catch a click meant for the board underneath.
    clickFallsThrough: !hit || (hit !== el && !el.contains(hit)),
    // The panel is painted with a gradient, so backgroundColor is transparent
    // by design: the shell is in backgroundImage.
    bgImage: cs.backgroundImage.slice(0, 120),
    borderColor: cs.borderTopColor,
    titleColor: ts.color,
    title: t.textContent
  });
})();
</script></body></html>`;
    const f = path.join(os.tmpdir(), "cc_turn_flash_" + variant + "_" + atMs + ".html");
    fs.writeFileSync(f, html);
    const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
      "--hide-scrollbars", `--window-size=1280,${800 + CHROME_CHROME_PX}`,
      "--virtual-time-budget=5000", "--dump-dom", "file://" + f],
      { encoding: "utf8", maxBuffer: 64e6 });
    const m = /<pre id="out">([\s\S]*?)<\/pre>/.exec(dom);
    return JSON.parse((m ? m[1] : "{}").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
  }

  console.log("\nrendered: YOUR TURN, sampled while it is up");
  {
    const r = sample("tf-turn", "YOUR TURN", "", 700);
    check(`the stylesheet really started it (${r.animCount} animations, ${r.duration}ms)`,
          r.animCount >= 1 && r.duration === 1600);
    check(`it is on screen and fully opaque (${r.w}x${r.h}, opacity ${r.opacity})`,
          r.w > 120 && r.h > 30 && Number(r.opacity) > 0.9 && r.onScreen);
    check("it says YOUR TURN", r.title === "YOUR TURN");
    check("a click goes through it to the board", r.clickFallsThrough);
    check(`it wears the in-game dialog's pale aqua (${r.bgImage.slice(0, 48)}…)`,
          /228,\s*245,\s*251/.test(r.bgImage) || /e4f5fb/i.test(r.bgImage));
    check(`the lettering is the table's navy (${r.titleColor})`,
          r.titleColor === "rgb(26, 45, 90)");
  }

  console.log("\n…and the swell carries it in and out");
  {
    const start = sample("tf-turn", "YOUR TURN", "", 0);
    const end   = sample("tf-turn", "YOUR TURN", "", 1600);
    check(`it starts invisible, below where it lands (opacity ${start.opacity})`,
          Number(start.opacity) < 0.05);
    check(`…and finishes invisible again (opacity ${end.opacity})`,
          Number(end.opacity) < 0.05);
    check("it really travels (the two ends are not the same place)",
          start.transform !== end.transform);
  }

  console.log("\nthe ★ variant is bigger news and holds longer");
  {
    // Sampled at the exact moment the every-turn announcement has finished
    // and gone: the ★ must still be sitting there to be read.
    const r = sample("tf-star", "★ FREE MAMMAL", "3 in your hand, lit up below. Play one for free.", 1600);
    check(`it runs for longer (${r.duration}ms)`, r.duration === 2600);
    check(`it is still fully up when the plain one has gone (opacity ${r.opacity})`,
          Number(r.opacity) > 0.9);
    check("it is on screen", r.onScreen);
    check("a click still goes through it", r.clickFallsThrough);
    check(`its edge wears the table's gold (${r.borderColor})`,
          r.borderColor === "rgb(232, 179, 74)");
  }
}

console.log("\n" + "=".repeat(42));
console.log(`RESULT: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
