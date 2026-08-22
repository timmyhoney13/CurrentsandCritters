#!/usr/bin/env node
/* The GOLD BORDER rule for ★ star abilities, checked against the real source.
 *
 * Run:  node test_star_gold_border.js
 *
 * What a gold border on a hand card means, everywhere in the app: "discard this
 * card to fire the ★ ability of the card you are playing." That promise is only
 * keepable when the staged play is the use_star variant, the engine ignores the
 * payment's symbol on a plain play (apply_action: "Star only fires when the
 * player explicitly chose use_star=True"). Painting gold off the server's
 * star_symbol hint alone lit cards up for a star that never fired.
 *
 * These checks run the REAL helpers lifted out of preview-app.js (no browser
 * needed, they are pure functions over the legal-action payload), plus a scan
 * of the render/CSS so the rule cannot be quietly re-broken:
 *   1. starPayInfo(): fires/offered/sym/ability for every payload shape.
 *   2. Two-sided cards: symbol matching sees BOTH faces; the ★ text is read
 *      off the face actually being played, not faces[0].
 *   3. renderHand paints .star-sym-match only when the star really fires.
 *   4. The gold style exists in preview.css for the real game AND the tutorial.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = __dirname;
const APP = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");

let failures = 0;
let checks = 0;
function check(cond, label) {
  checks++;
  if (!cond) { failures++; console.log("  ✗ " + label); }
}

// ── Lift the real helpers out of preview-app.js ──────────────────────────────
// preview-app.js is one huge IIFE that needs the whole app to run, so pull out
// just the pure functions under test and evaluate them against a stub hand.
function grabFn(name) {
  const start = APP.indexOf(`\n  function ${name}(`);
  if (start < 0) throw new Error(`function ${name}() not found in preview-app.js`);
  let i = APP.indexOf("{", start);
  let depth = 0;
  for (let j = i; j < APP.length; j++) {
    const ch = APP[j];
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return APP.slice(start, j + 1); }
  }
  throw new Error(`unbalanced braces reading ${name}()`);
}

const sandbox = { handEntryMap: new Map(), console };
vm.createContext(sandbox);
vm.runInContext(
  [
    grabFn("starText"),
    grabFn("normalizeSymbol"),
    grabFn("entryHasSymbolMatch"),
    grabFn("_actionFace"),
    grabFn("_actionStarText"),
    grabFn("starPayInfo"),
  ].join("\n"),
  sandbox
);
const { starPayInfo, entryHasSymbolMatch, _actionStarText } = sandbox;

function setHand(entries) {
  sandbox.handEntryMap = new Map(entries.map(e => [Number(e.entry_uid), e]));
  vm.runInContext("", sandbox); // handEntryMap is read from the context each call
}
// Re-bind: the lifted functions close over the context's `handEntryMap` global,
// so assigning through the sandbox object is enough.

// ── 1. starPayInfo: fires / offered ─────────────────────────────────────────
console.log("1. starPayInfo() tells gold from not-gold");
{
  setHand([{ entry_uid: 26, faces: [{ uid: 26, name: "Red Beaded Anemone", symbol: "circle", text: "+3 per invertebrate | *Draw one*" }] }]);

  const starAct = {
    kind: "play_to_ocean", card_uid: 26, face_uid: 26, ocean_uid: 217,
    use_star: true, requires_symbol_match: true, required_symbol: "circle",
    star_symbol: "", cost_to_pay: 1,
  };
  let info = starPayInfo(starAct);
  check(info.fires === true, "use_star action with a required_symbol must fire the gold border");
  check(info.sym === "circle", `star symbol should be circle, got ${info.sym}`);
  check(info.ability === "Draw one", `ability should be "Draw one", got "${info.ability}"`);
  check(info.offered === false, "a firing star is not merely 'offered'");

  // Plain variant of the same card, ★ toggle off: the star is available to
  // switch to, but paying with a ● card would NOT fire it.
  const plainAct = { ...starAct, use_star: false, requires_symbol_match: false, required_symbol: "", star_symbol: "circle" };
  info = starPayInfo(plainAct);
  check(info.fires === false, "a plain play must never claim the star fires");
  check(info.offered === true, "a plain play with a star twin should report the star as offered");
  check(info.ability === "Draw one", "the offered note still names the ability");

  // Plain play of a card with no star at all.
  const noStar = { ...starAct, use_star: false, requires_symbol_match: false, required_symbol: "", star_symbol: "" };
  info = starPayInfo(noStar);
  check(info.fires === false && info.offered === false, "a card with no ★ reports neither fires nor offered");

  // Defensive: a malformed use_star action missing its symbol must not go gold.
  info = starPayInfo({ ...starAct, required_symbol: "" });
  check(info.fires === false, "use_star with an empty required_symbol must not paint gold");
  info = starPayInfo({ ...starAct, requires_symbol_match: false });
  check(info.fires === false, "use_star without requires_symbol_match must not paint gold");
  check(starPayInfo(null).fires === false, "no staged action means no gold border");
}

// ── 2. Two-sided cards ──────────────────────────────────────────────────────
console.log("2. two-sided cards: both faces pay, the played face owns the ★");
{
  // Real pair 61/62: Bunker (no ★, ♥ heart) / Red Beaded Anemone (★ Draw one, ♦).
  const pair = {
    entry_uid: 61,
    faces: [
      { uid: 61, name: "Bunker", symbol: "heart", text: "+1 per baitfish" },
      { uid: 62, name: "Red Beaded Anemone", symbol: "diamond", text: "+3 per invertebrate | *Draw one*" },
    ],
  };
  const payHeart  = { entry_uid: 9,  faces: [{ uid: 9,  name: "Horned Puffin", symbol: "heart",   text: "+3 | *play again*" }] };
  const payDiamnd = { entry_uid: 33, faces: [{ uid: 33, name: "Great Albatross", symbol: "diamond", text: "+6 | *Draw one*" }] };
  setHand([pair, payHeart, payDiamnd]);

  check(entryHasSymbolMatch(61, "heart") === true,  "a two-sided card pays on its FIRST face's symbol");
  check(entryHasSymbolMatch(61, "diamond") === true, "a two-sided card also pays on its SECOND face's symbol");
  check(entryHasSymbolMatch(61, "square") === false, "a two-sided card must not match a symbol neither face has");
  check(entryHasSymbolMatch(61, "") === false, "an empty symbol never matches");
  check(entryHasSymbolMatch(999, "heart") === false, "a uid that is not in hand never matches");
  check(entryHasSymbolMatch(9, "HEART") === true, "symbol matching is case-insensitive");

  // Playing the STARRED face (62) must name that face's ★, not faces[0]'s.
  const playSecondFace = {
    kind: "play_to_ocean", card_uid: 61, face_uid: 62, ocean_uid: 217,
    use_star: true, requires_symbol_match: true, required_symbol: "diamond", cost_to_pay: 1,
  };
  const info = starPayInfo(playSecondFace);
  check(info.fires === true, "playing the starred face of a pair fires the star");
  check(info.sym === "diamond", `the required symbol is the played face's (diamond), got ${info.sym}`);
  check(info.ability === "Draw one",
        `the ★ text must come from the played face, got "${info.ability}" (faces[0] Bunker has none)`);

  // Playing the PLAIN face must not borrow the other face's star text.
  check(_actionStarText({ card_uid: 61, face_uid: 61 }) === "",
        "the plain face of a pair must report no ★ text");
}

// ── 3. renderHand paints gold only when the star fires ──────────────────────
console.log("3. renderHand gates .star-sym-match on the star actually firing");
{
  const region = APP.slice(APP.indexOf("function renderHand("), APP.indexOf("_setupHandHover"));
  check(/const\s+_starPay\s*=\s*starPayInfo\(pendingPayAction\)/.test(region),
        "renderHand must derive its gold highlight from starPayInfo(pendingPayAction)");
  check(/if\s*\(_starPay\.fires\)/.test(region),
        "the .star-sym-match branch must be gated on _starPay.fires");
  check(!/pendingPayAction\?\.star_symbol/.test(region),
        "renderHand must not fall back to star_symbol, that paints gold for a star that never fires");
  check(/entryHasSymbolMatch\(entryUid,\s*_starPay\.sym\)/.test(region),
        "gold must go to cards whose symbol matches the star symbol");
  check(/beingPlayed/.test(region),
        "the card being played must be excluded from the gold payment highlight");
  check(/starPay:\s*_starPay\.fires/.test(region),
        "the hand render cache key must include the star payment, or swapping plays keeps stale borders");
}

// ── 4. The gold styles exist, in both the real game and the tutorial ────────
console.log("4. gold ★-payment styles exist and are gold");
{
  const rules = {
    ".pv-hand-card.star-sym-match": "the real game's ★ payment highlight",
    ".pv-hand-card.star-sym-match.pay-selected": "a selected ★ payment card",
    ".tut-hand-card.tut-star-pay": "the tutorial's ★ payment highlight",
  };
  for (const [sel, what] of Object.entries(rules)) {
    const i = CSS.indexOf(sel);
    check(i >= 0, `${what} (${sel}) is missing from preview.css`);
    if (i < 0) continue;
    const body = CSS.slice(i, CSS.indexOf("}", i));
    check(/var\(--gold\)|#[fF]0[cC]840|240,\s*200,\s*64|255,\s*215,\s*0/.test(body),
          `${what} (${sel}) is not gold`);
  }
  // The tutorial must not teach a different colour for the same gesture.
  const tut = APP.slice(APP.indexOf("function tutRenderHand("), APP.indexOf("function tutRenderStaging("));
  check(/star_play[\s\S]*?tut-star-pay/.test(tut),
        "the tutorial's star_play step must gold-highlight the matching-symbol cards");
  check(/pay_animal[\s\S]*?tut-star-pay/.test(tut),
        "the tutorial's pay_animal step must gold-highlight the ♥ cards that fire the ★");
  check(!/star_play[\s\S]{0,400}tut-pay-selectable/.test(tut),
        "the star_play step must not use the teal 'any card pays' colour");
  check(!/pay_animal[\s\S]{0,400}tut-discard-selectable/.test(tut),
        "the pay_animal step must not use the red discard colour for ★ payment");
  // …and the tutorial hand must always contain a ● card for the ★ lesson.
  const hand = /const TUT_INIT_HAND = \[([^\]]+)\]/.exec(APP);
  check(!!hand, "TUT_INIT_HAND not found");
  if (hand) {
    const uids = hand[1].split(",").map(s => Number(s.trim()));
    const cardsBlk = APP.slice(APP.indexOf("const TUT_CARDS = {"));
    const circles = uids.filter(u => {
      const m = new RegExp(`^\\s*${u}:\\s*\\{[^}]*symbol:"circle"`, "m").exec(cardsBlk);
      return !!m && u !== 26;
    });
    check(circles.length > 0,
          "the tutorial hand has no ● card besides the anemone, so its ★ step can't be paid honestly");
    const prot = /const TUT_PROTECTED_UIDS = \[([^\]]+)\]/.exec(APP);
    check(!!prot, "TUT_PROTECTED_UIDS not found");
    if (prot) {
      const protUids = prot[1].split(",").map(s => Number(s.trim()));
      check(circles.every(u => protUids.includes(u)),
            "the tutorial's ● payment card must be protected from earlier payment steps");
    }
  }
}

// ── 5. The border is gold ON SCREEN, not just in the stylesheet ─────────────
// A rule can exist and still lose to a later one (the cyan hover rule sits
// directly above the gold ★ rule and targets the same element at the same
// specificity), so measure the computed colour in a real browser.
console.log("5. computed colours in a real browser");
{
  const CHROME = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
  ].find(p => fs.existsSync(p));

  if (!CHROME) {
    console.log("  SKIP: no Chrome/Chromium found: stylesheet checks above still ran.");
  } else {
    const os = require("os");
    const { execFileSync } = require("child_process");
    const html = `<!doctype html><html><head><style>${CSS}</style></head><body>
<div id="pv-hand" class="payment-active">
  <div class="pv-hand-card" id="c-plain"><div class="pv-card-inner"></div></div>
  <div class="pv-hand-card star-sym-match" id="c-gold"><div class="pv-card-inner"></div></div>
  <div class="pv-hand-card star-sym-match pay-selected" id="c-goldsel"><div class="pv-card-inner"></div></div>
  <div class="pv-hand-card pay-selected" id="c-sel"><div class="pv-card-inner"></div></div>
  <div class="pv-hand-card star-sym-match hovered" id="c-goldhov"><div class="pv-card-inner"></div></div>
</div>
<div id="tut-hand-row">
  <div class="tut-hand-card tut-star-pay" id="t-gold"></div>
  <div class="tut-hand-card tut-pay-selectable" id="t-teal"></div>
</div>
<script>
const out = {};
for (const id of ["c-plain","c-gold","c-goldsel","c-sel","c-goldhov"]) {
  const cs = getComputedStyle(document.querySelector("#"+id+" .pv-card-inner"));
  out[id] = { border: cs.borderTopColor, shadow: cs.boxShadow };
}
for (const id of ["t-gold","t-teal"]) {
  const cs = getComputedStyle(document.getElementById(id));
  out[id] = { border: cs.borderTopColor, shadow: cs.boxShadow };
}
document.title = JSON.stringify(out);
<\/script></body></html>`;
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "star-gold-"));
    const file = path.join(dir, "t.html");
    fs.writeFileSync(file, html);
    let res = null;
    try {
      const dom = execFileSync(CHROME,
        ["--headless=new", "--disable-gpu", "--virtual-time-budget=2500", "--dump-dom", "file://" + file],
        { encoding: "utf8", maxBuffer: 1 << 26 });
      res = JSON.parse(/<title>([\s\S]*?)<\/title>/.exec(dom)[1].replace(/&quot;/g, '"'));
    } catch (e) {
      check(false, "headless Chrome could not measure the card colours: " + e.message);
    }
    if (res) {
      const GOLD = "rgb(240, 200, 64)"; // --gold #F0C840
      check(res["c-gold"].border === GOLD,
            `a symbol-matching payment card must render gold, got ${res["c-gold"].border}`);
      check(res["c-goldsel"].border === GOLD, "a selected ★ payment card stays gold");
      check(res["c-goldsel"].shadow.includes("inset"),
            "a selected ★ payment card needs its inset ring, or selected and unselected look alike");
      check(res["c-goldhov"].border === GOLD,
            "hovering a ★ payment card must keep it gold (the cyan hover rule must not win)");
      check(res["c-plain"].border !== GOLD, "a card that cannot pay for the ★ must not be gold");
      check(res["c-sel"].border !== GOLD, "an ordinary selected payment card is green, not gold");
      check(res["t-gold"].border === GOLD,
            `the tutorial's ★ payment card must be the SAME gold, got ${res["t-gold"].border}`);
      check(res["t-teal"].border !== GOLD, "the tutorial's 'any card pays' colour must stay teal");
    }
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch (_) {}
  }
}

console.log(`\nstar gold-border checks: ${checks}`);
if (failures) { console.log(`${failures} FAILED`); process.exit(1); }
console.log("gold borders OK");
