#!/usr/bin/env node
/* The 💡 Help panel: the plan it recommends, and the combos it pairs.
 *
 * Run:  node test_strategy_recommendation.js
 *
 * Two complaints, one file.
 *
 * 1. "It is wrong most of the time, it is not the best."
 *    The old recommendation read ONE thing, the cards in your hand, and scored
 *    a plan by `matchedCards * 100 + matchedCopies`, a RAW COUNT. Raw counts
 *    belong to whichever plan lists the most cards: Birds lists 12, Game Fish
 *    lists 3, so a hand of three tuna and two gulls was told to play Birds. It
 *    also never once looked at the board, which is the half of the table you
 *    cannot take back and therefore the half that decides what you are really
 *    playing. Every term is a SHARE now, and the board carries the most weight.
 *
 * 2. "There is an almost infinite number of combos, make sure it has them all."
 *    Ten combos were hand-written, which left most cores with one partner or
 *    none. Every remaining core-to-core pair is generated in js/gamedata.js, so
 *    all 66 exist and every core has eleven partners to be paired with.
 *
 * The scoring, the snapshot and the combo ranking are sliced out of
 * preview-app.js by text and executed here, so a change to those lines changes
 * what this test runs.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = __dirname;
const APP = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const DATA = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/gamedata.js"), "utf8");

let failures = 0, checks = 0;
function check(cond, label) {
  checks++;
  if (!cond) { failures++; console.log("  ✗ " + label); }
  else console.log("  ✓ " + label);
}
function eq(got, want, label) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  check(g === w, label + (g === w ? "" : `\n      got  ${g}\n      want ${w}`));
}

// ── Lift the real code out of preview-app.js ─────────────────────────────────
function grabFn(name) {
  const re = new RegExp("\\n( +)(?:function|const) " + name + "\\b");
  const m = re.exec(APP);
  if (!m) throw new Error(`${name} not found in preview-app.js`);
  const start = m.index + 1;
  let i = APP.indexOf("{", start), depth = 0;
  for (let j = i; j < APP.length; j++) {
    if (APP[j] === "{") depth++;
    else if (APP[j] === "}") { depth--; if (depth === 0) return APP.slice(start, j + 1); }
  }
  throw new Error(`unbalanced braces reading ${name}`);
}
function grabLine(needle) {
  const i = APP.indexOf(needle);
  if (i < 0) throw new Error(`could not find: ${needle}`);
  const a = APP.lastIndexOf("\n", i) + 1;
  const b = APP.indexOf("\n", i);
  return APP.slice(a, b < 0 ? APP.length : b);
}

// The game data first: the strategies these functions rank are the real ones.
const W = {};
vm.runInNewContext(DATA, { window: W, console });

const SRC = `
  ${grabLine("function _stratPairKey(uid) {").replace(/^\s+/, "")}
  ${grabFn("_stratPairKey").split("\n").slice(1).join("\n")}
  ${grabFn("_isCore")}
  ${grabFn("_isCombo")}
  ${grabFn("_isCustom")}
  ${grabFn("_comboPairIdxs")}
  ${grabFn("_suggestedCombos")}
  ${grabFn("_tableSnapshot")}
  ${grabFn("_strategyFit")}
  ${grabFn("_rankStrategies")}
  ${grabLine("let _fits = null;")}
  ${grabFn("_fitsNow")}
  ${grabFn("_recoBannerHtml")}
  ${grabLine("const _hesc = (s) =>")}

  function resetFits() { _fits = null; }
  function setTable(t) {
    _latestPlayers = t.players || [];
    myIdx = t.myIdx == null ? 0 : t.myIdx;
    _latestPool = t.pool || [];
    _handRenderData = t.handRenderData || null;
    resetFits();
  }
  function rank() { return _rankStrategies(); }
  function setComp(seats) { compMode = !!seats; compMySeats = seats || []; resetFits(); }
  function banner() { resetFits(); return _recoBannerHtml(); }
  function suggested(labels) {
    _activeStrategies.clear();
    for (const l of labels) _activeStrategies.add(HELP_STRATEGIES.findIndex(s => s.label === l));
    resetFits();
    return _suggestedCombos();
  }
`;

const sandbox = {
  console,
  window: W,
  HELP_STRATEGIES: W.CC_BUILTIN_STRATEGIES,
  STRAT_COLORS: W.CC_STRAT_PRIMARY.map(f => W.CC_FAMILY_COLORS[f] || "#b388ff"),
  _COMBO_PAIR_LABELS: W.CC_COMBO_PAIR_LABELS,
  _activeStrategies: new Set(),
  _latestPlayers: [], _latestPool: [], _handRenderData: null, myIdx: 0,
  compMode: false, compMySeats: [],
  // The banner draws a tile; the art path is not what this test is about.
  _stratArtHtml: () => "<div></div>",
  imagePathForUid: (u) => "/x/" + u + ".png",
};
vm.createContext(sandbox);
vm.runInContext(SRC, sandbox);

// ── Table builders ───────────────────────────────────────────────────────────
const S = W.CC_BUILTIN_STRATEGIES;
const idxOf = (label) => S.findIndex(s => s.label === label);
const stratOf = (label) => S[idxOf(label)];
// A real uid for the Nth deck copy of a named card in a named plan.
function uidOf(label, name, copy) {
  const c = stratOf(label).cards.find(x => x.name === name);
  if (!c) throw new Error(`${label} does not list ${name}`);
  const u = c.uids[copy || 0];
  if (u == null) throw new Error(`${label}/${name} has no copy ${copy}`);
  return u;
}
const CORAL_REEF = 217; // an ocean base card, the thing every board starts with
// One ocean carrying `faces`, split across the four sides the way a real one is.
function ocean(faces, baseUid) {
  const b = baseUid || CORAL_REEF;
  const lanes = { up: [], down: [], left: [], right: [] };
  const order = ["up", "down", "left", "right"];
  faces.forEach((u, n) => lanes[order[n % 4]].push({ uid: u, name: "", species: "" }));
  return { ocean_uid: b, ocean: { uid: b, name: "Ocean" }, ...lanes };
}
const hand = (uids) => uids.map(u => ({ entry_uid: u, faces: [{ uid: u }] }));
const me = (board, cards) => ({ index: 0, name: "me", board: board, hand: hand(cards || []) });
const them = (board) => ({ index: 1, name: "them", board: board });

const topOf = (r) => S[r.order[0]].label;
const fitOf = (r, label) => r.byIdx.get(idxOf(label)).fit;

// ═════════════════════════════════════════════════════════════════════════════
console.log("\nIt reads the board, not just the hand");

{
  // A board that is unmistakably Birds, and a hand that is not. The old code
  // could not see the board at all, so it answered from the hand alone.
  const birds = ["Emperor Penguin", "Horned Puffin", "California Gull", "Peruvian Pelican", "Osprey"]
    .map(n => uidOf("Birds", n, 0));
  const coralHand = ["Staghorn Coral", "Elk Horn Coral"].map(n => uidOf("Coral", n, 0));
  sandbox.setTable({ players: [me([ocean(birds)], coralHand), them([])] });
  const r = sandbox.rank();
  const pair = W.CC_COMBO_PAIR_LABELS[topOf(r)] || [topOf(r)];
  check(pair.indexOf("Birds") !== -1,
    "a board of five birds recommends Birds (or a Birds combo), whatever the hand holds, got " + topOf(r));
  check(fitOf(r, "Birds") > fitOf(r, "Coral"),
    "the board outweighs a hand that points somewhere else");
}

{
  // Same hand, empty board: now the hand is all there is to go on and the
  // answer must change. A recommender that ignored one of the two would give
  // the same answer to both of these tables.
  const coralHand = ["Staghorn Coral", "Elk Horn Coral"].map(n => uidOf("Coral", n, 0));
  sandbox.setTable({ players: [me([], coralHand), them([])] });
  const r = sandbox.rank();
  check(fitOf(r, "Coral") > fitOf(r, "Birds"),
    "with no board, the hand decides, and a coral hand does not point at Birds");
}

console.log("\nA plan does not win by listing more cards");

{
  // Three tuna and two gulls. Three fifths of the hand is game fish, but Birds
  // lists twelve cards to Game Fish's three, so the old raw-count score read
  // Birds 202, Game Fish 103, and told you to play Birds.
  const h = [
    uidOf("Game Fish", "Yellowfin Tuna", 0), uidOf("Game Fish", "Yellowfin Tuna", 1),
    uidOf("Game Fish", "Yellowfin Tuna", 2),
    uidOf("Birds", "California Gull", 0), uidOf("Birds", "Emperor Penguin", 0),
  ];
  sandbox.setTable({ players: [me([], h), them([])] });
  const r = sandbox.rank();
  check(topOf(r) !== "Birds", "three tuna and two birds is not a Birds hand, got " + topOf(r));
  const oldWinner = fitOf(r, "Birds");
  const bigger = ["Game Fish", "Yellowfin Tuna Stack"].filter(l => fitOf(r, l) > oldWinner);
  check(bigger.length > 0, "the game-fish plans outrank Birds on that hand");

  // And the mirror: two tuna, three birds, should now favour the birds.
  const h2 = [
    uidOf("Game Fish", "Yellowfin Tuna", 0), uidOf("Game Fish", "Yellowfin Tuna", 1),
    uidOf("Birds", "California Gull", 0), uidOf("Birds", "Emperor Penguin", 0),
    uidOf("Birds", "Osprey", 0),
  ];
  sandbox.setTable({ players: [me([], h2), them([])] });
  const r2 = sandbox.rank();
  check(fitOf(r2, "Birds") > fitOf(r2, "Game Fish"),
    "flip the hand and the answer flips with it");
}

console.log("\nCards already on other boards are not cards you can have");

{
  const lobsters = [0, 1, 2].map(n => uidOf("Crustaceans", "Lobster", n));
  const table = { players: [me([ocean(lobsters)], []), them([])] };
  sandbox.setTable(table);
  const clear = sandbox.rank();
  // Same board of mine, but now the opponent is sitting on the rest of the
  // crustaceans. The plan is no worse a description of my board, it is a worse
  // plan to keep playing, and the ranking has to know the difference.
  const rest = stratOf("Crustaceans").cards
    .filter(c => c.species === "Crustacean")
    .flatMap(c => c.uids).filter(u => lobsters.indexOf(u) === -1);
  sandbox.setTable({ players: [me([ocean(lobsters)], []), them([ocean(rest.slice(0, 12))])] });
  const raided = sandbox.rank();
  check(fitOf(raided, "Crustaceans") < fitOf(clear, "Crustaceans"),
    "a plan whose copies are already down in front of other people scores lower");
}

console.log("\nCompetitive is two seats, one player");

{
  // One person owns seats 0 and 1. Reading only seat 0 would drop half my own
  // board AND count the other half against me as a rival's cards, so the same
  // twelve cards would score lower spread over my two seats than piled on one.
  const cephs = ["Common Octopus", "Cuttlefish", "Bobtail Squid"].map(n => uidOf("Cephalopods", n, 0));
  const more = [uidOf("Cephalopods", "Giant Squid", 0), uidOf("Cephalopods", "Common Octopus", 1)];
  const seat1 = { index: 1, name: "my other seat", board: [ocean(more)], hand: [] };
  sandbox.setComp(null);
  // The comparison board is TWO oceans as well, because a second ocean is
  // genuinely one more card on the board and would move the share on its own.
  sandbox.setTable({ players: [me([ocean(cephs), ocean(more)], []), them([])] });
  const piled = sandbox.rank();
  sandbox.setTable({ players: [me([ocean(cephs)], []), seat1] });
  const solo = sandbox.rank();          // seat 1 read as an opponent
  sandbox.setComp([0, 1]);
  sandbox.setTable({ players: [me([ocean(cephs)], []), seat1] });
  sandbox.setComp([0, 1]);
  const paired = sandbox.rank();
  check(fitOf(paired, "Cephalopods") > fitOf(solo, "Cephalopods"),
    "my partner seat counts as mine, not as a rival sitting on the cards I want");
  check(Math.abs(fitOf(paired, "Cephalopods") - fitOf(piled, "Cephalopods")) < 0.001,
    "and the same cards score the same across my two seats as they would on one");
  sandbox.setComp(null);
}

console.log("\nA combo needs BOTH halves to be live");

{
  // A board that is purely cephalopods. Every combo with Cephalopods in it
  // matches this board just as well AND lists twice the cards, so without a
  // balance rule the panel would tell a winning single plan to split itself.
  const cephs = ["Common Octopus", "Cuttlefish", "Bobtail Squid"].map(n => uidOf("Cephalopods", n, 0));
  sandbox.setTable({ players: [me([ocean(cephs)], []), them([])] });
  const r = sandbox.rank();
  eq(topOf(r), "Cephalopods", "one-family board recommends the single plan, not a lopsided combo");

  // Now put the other half on the board too, and the combo should come through.
  const moon = stratOf("Shooting the Moon").cards
    .filter(c => c.species === "Crosscurrent").slice(0, 3).map(c => c.uids[0]);
  sandbox.setTable({ players: [me([ocean(cephs.concat(moon))], []), them([])] });
  const both = sandbox.rank();
  check(fitOf(both, "Cephalopods + Shooting the Moon") > fitOf(r, "Cephalopods + Shooting the Moon"),
    "the same combo scores higher once both halves are actually on the board");
}

console.log("\nThe banner says what it read");

{
  const birds = ["Emperor Penguin", "Horned Puffin", "California Gull"].map(n => uidOf("Birds", n, 0));
  sandbox.setTable({ players: [me([ocean(birds)], [uidOf("Birds", "Osprey", 0)]), them([])] });
  const html = sandbox.banner();
  check(/Best strategy for the board you have built/.test(html),
    "with a board down, the banner says it is reading the board");
  check(/cards on your board/.test(html), "and names how much of that board it matched");
  check(/you're holding/.test(html), "and counts the hand as well");
  check(/% fit/.test(html), "and shows the number behind the claim");
  check(/hs2-reco-alts/.test(html), "and offers the runners-up, so the pick can be argued with");

  sandbox.setTable({ players: [me([], [uidOf("Birds", "Osprey", 0)]), them([])] });
  const handOnly = sandbox.banner();
  check(/Best strategy to play for your starting hand/.test(handOnly),
    "before a board exists it is honest about reading only the hand");

  sandbox.setTable({ players: [me([], []), them([])] });
  check(/Open this once you've been dealt your hand/.test(sandbox.banner()),
    "with nothing on the table it asks you to come back, it does not invent a pick");
}

// ═════════════════════════════════════════════════════════════════════════════
console.log("\nEvery pair of cores is a combo you can be offered");

const CORES = S.map((s, i) => [s, i]).filter(([s]) => s.tier === "Core").map(([s]) => s.label);
const COMBOS = S.filter(s => s.tier === "Combo");

eq(CORES.length, 12, "twelve core plans");
eq(COMBOS.length, (12 * 11) / 2, "every unordered pair of them is a combo");

check(COMBOS.filter(s => !s.generated).length === 10,
  "the ten hand-written combos survived, they were not regenerated over");

{
  // The whole point of the second complaint: pick any one core and there are
  // eleven partners waiting, not one.
  let worst = { n: Infinity, label: "" };
  for (const c of CORES) {
    const n = sandbox.suggested([c]).length;
    if (n < worst.n) worst = { n, label: c };
  }
  eq(worst.n, 11, "the thinnest core still pairs with eleven combos (" + worst.label + ")");
}

check(new Set(S.map(s => s.label)).size === S.length, "no two plans share a label");

{
  // Order matters to a reader: the combo that bridges BOTH plans you picked
  // must lead, and after that the ones that fit the cards in front of you.
  const h = ["Emperor Penguin", "California Gull"].map(n => uidOf("Birds", n, 0));
  sandbox.setTable({ players: [me([], h), them([])] });
  const list = sandbox.suggested(["Birds", "Crustaceans"]);
  const pairOf = (i) => W.CC_COMBO_PAIR_LABELS[S[i].label];
  const first = pairOf(list[0]);
  check(first.indexOf("Birds") !== -1 && first.indexOf("Crustaceans") !== -1,
    "the combo bridging both picked cores leads the list, got " + S[list[0]].label);
  for (const i of list) {
    const p = pairOf(i);
    if (!(p.indexOf("Birds") !== -1 || p.indexOf("Crustaceans") !== -1)) {
      check(false, S[i].label + " was suggested but bridges neither picked core");
      break;
    }
  }
  check(true, "every suggested combo actually bridges one of the picked cores");
}

console.log("\nA generated combo is made of its two halves, nothing invented");

{
  let bad = [];
  for (const s of COMBOS) {
    if (!s.generated) continue;
    const pair = W.CC_COMBO_PAIR_LABELS[s.label];
    const allowed = new Set(pair.flatMap(l => stratOf(l).cards.map(c => c.name)));
    for (const c of s.cards) if (!allowed.has(c.name)) bad.push(s.label + "/" + c.name);
    if (!s.blurb || !s.steps.length || !s.tips.length) bad.push(s.label + " is missing its text");
    if (s.cards.length !== new Set(s.cards.map(c => c.name)).size) bad.push(s.label + " lists a card twice");
  }
  eq(bad.slice(0, 5), [], "no generated combo invents a card, drops its text or repeats itself");
}

{
  // The card lists are the cores' own objects, so the uids a combo lights up
  // are the uids those cores light up, never a re-derived guess.
  const uids = new Set();
  for (const s of S) for (const c of s.cards) for (const u of c.uids) uids.add(Number(u));
  const bad = [...uids].filter(u => !(u >= 1 && u <= 269));
  eq(bad, [], "every uid in every plan is a real card in the deck");
}

{
  // Two-sided cards: a combo that bridges a family living on the back face has
  // to match by physical card, or half its cards never light up in your hand.
  const pairKey = (u) => (u >= 1 && u <= 188) ? (u % 2 === 1 ? u : u - 1) : u;
  const s = stratOf("Crustaceans + Coral");
  check(!!s, "a generated combo can be found by its label");
  const keys = new Set(s.cards.flatMap(c => c.uids).map(pairKey));
  const coralKeys = stratOf("Coral").cards.flatMap(c => c.uids).map(pairKey);
  check(coralKeys.every(k => keys.has(k)), "it carries every physical card of both halves");
}


// ═════════════════════════════════════════════════════════════════════════════
//  DRIVE  (a real browser: the panel this markup makes, at five window shapes)
// ═════════════════════════════════════════════════════════════════════════════
//
// The section above proves the ranking is right. This proves the panel that
// prints it fits on the screen. Two things here are new and both are the kind
// that look fine at one width: the fit badge and the runners-up row sharing the
// recommendation card, and a combo section that now holds twenty-one cards
// where it used to hold three.
//
// The markup is the REAL renderList() output, lifted out of preview-app.js and
// run against the real strategy data, poured into the real #pv-help-modal
// skeleton with the real css/preview.css over it.

const { execFileSync, spawn } = require("child_process");
const os = require("os");
const CLIENT = path.join(ROOT, "multiplayer/client");
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

console.log("\nThe panel it prints fits on the screen");

if (!CHROME) {
  console.log("  (skipped: no Chrome/Chromium found)");
} else {
  // Give the sandbox the handful of DOM handles renderList() writes through,
  // then run the real function and keep what it wrote.
  vm.runInContext(`
    ${grabLine("const HS_COMBO_PREVIEW = 3;")}
    let _curDetail = -1, _hsShowAllCombos = false;
    const modal = { classList: { add(){}, remove(){}, contains(){ return false; } } };
    const titleEl = {}, introEl = { innerHTML: "" }, listEl = { innerHTML: "" };
    function _getMostPlayedStrategyLocal() { return "Birds"; }
    function _familyLegendHtml() { return ""; }
    ${grabFn("renderList")}
    function listHtml(showAll) { _hsShowAllCombos = !!showAll; renderList(); return listEl.innerHTML; }
  `, sandbox);

  // A mid-game table: a board worth reading, a hand, a pool, and an opponent
  // sitting on cards I might want, so every part of the banner has something
  // to say (including the warning line, which only prints when it is true).
  const birdBoard = ["Emperor Penguin", "Horned Puffin", "California Gull"].map(n => uidOf("Birds", n, 0));
  const lobBoard = [0, 1].map(n => uidOf("Crustaceans", "Lobster", n));
  const myHand = [uidOf("Birds", "Osprey", 0), uidOf("Birds", "Great Albatross", 0),
                  uidOf("Crustaceans", "Mantis Shrimp", 0)];
  const oppBoard = stratOf("Birds").cards.filter(c => c.species === "Bird")
    .flatMap(c => c.uids).filter(u => birdBoard.indexOf(u) === -1).slice(0, 14);
  sandbox.setTable({
    players: [me([ocean(birdBoard.concat(lobBoard))], myHand), them([ocean(oppBoard)])],
    pool: hand([uidOf("Birds", "Peruvian Pelican", 0), uidOf("Coral", "Staghorn Coral", 0)]),
  });
  sandbox._activeStrategies.clear();
  sandbox._activeStrategies.add(idxOf("Birds"));
  sandbox._activeStrategies.add(idxOf("Crustaceans"));
  sandbox.resetFits();
  const PANEL = sandbox.listHtml(true);   // every combo expanded, the worst case

  check(/hs2-reco-warn/.test(PANEL), "the shared-out warning prints when the copies really are gone");
  check((PANEL.match(/class="hs2-combo/g) || []).length >= 20,
    "picking two cores offers twenty-plus combos, where it used to offer three");

  const PORT = 9970 + (process.pid % 300);
  const SERVER_SRC = `
    const fs=require("fs"),path=require("path"),http=require("http");
    const ROOT=${JSON.stringify(CLIENT)};
    const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",
      ".json":"application/json",".png":"image/png",".jpg":"image/jpeg",
      ".webp":"image/webp",".svg":"image/svg+xml"};
    http.createServer((req,res)=>{
      const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
      const f=path.join(ROOT,rel);
      if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);res.end();return;}
      res.writeHead(200,{"Content-Type":MIME[path.extname(f)]||"application/octet-stream"});
      fs.createReadStream(f).pipe(res);
    }).listen(${PORT});
  `;
  const PAGE = `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/css/preview.css">
<style>*,*::before,*::after{transition:none!important;animation:none!important}
  html,body{margin:0}
  /* The modal is normally opened by a class the app adds; open it here. */
  #pv-help-modal{display:flex!important}</style></head>
<body class="cc-in-game">
  <div id="pv-help-modal" class="open">
    <div id="pv-help-box">
      <div id="pv-help-header">
        <button id="pv-help-back">‹ Back</button>
        <h2 id="pv-help-title">Strategies</h2>
        <button id="pv-help-close">✕</button>
      </div>
      <p id="pv-help-intro">Choose a core strategy to build around.</p>
      <div id="pv-help-list">__PANEL__</div>
      <div id="pv-help-detail"></div>
    </div>
  </div>
<div id="out">PENDING</div>
<script>
(function(){
  var log = {};
  function box(sel){ var e=document.querySelector(sel); return e?e.getBoundingClientRect():null; }
  var list = document.getElementById("pv-help-list");
  var card = document.querySelector(".hs2-reco-card");
  log.w = window.innerWidth;
  // Nothing may push the panel sideways: a horizontal scrollbar in a modal is
  // content the player never finds.
  log.listOverflow = list ? Math.round(list.scrollWidth - list.clientWidth) : -1;
  log.bodyOverflow = Math.round(document.documentElement.scrollWidth - window.innerWidth);
  var cb = card ? card.getBoundingClientRect() : null;
  log.recoW = cb ? Math.round(cb.width) : 0;
  log.recoH = cb ? Math.round(cb.height) : 0;
  log.recoLeft = cb ? Math.round(cb.left) : 0;
  log.recoRight = cb ? Math.round(cb.right) : 0;
  // The fit badge, the warning and every runner-up chip must sit INSIDE the
  // recommendation card, not hang off its edge.
  log.escapes = 0; log.zero = 0;
  ["\\.hs2-reco-fit", "\\.hs2-reco-warn", "\\.hs2-reco-alt"].forEach(function(sel){
    document.querySelectorAll(sel.replace(/\\\\/g,"")).forEach(function(el){
      var r = el.getBoundingClientRect();
      if (!r.width || !r.height) log.zero++;
      if (cb && (r.right > cb.right + 1 || r.left < cb.left - 1)) log.escapes++;
    });
  });
  log.alts = document.querySelectorAll(".hs2-reco-alt").length;
  // Every combo card must be a card: readable width, real height, on screen.
  var thin = 0, combos = document.querySelectorAll(".hs2-combo");
  combos.forEach(function(el){
    var r = el.getBoundingClientRect();
    if (r.width < 120 || r.height < 80) thin++;
  });
  log.combos = combos.length; log.thinCombos = thin;
  // The combo panel scrolls inside itself rather than growing the page.
  var panel = document.querySelectorAll(".hs2-panel");
  log.panelsScroll = 0;
  panel.forEach(function(p){ if (getComputedStyle(p).overflowY === "auto") log.panelsScroll++; });
  document.getElementById("out").textContent = JSON.stringify(log);
})();
</script></body></html>`;

  const pageFile = path.join(CLIENT, "__strat_panel_probe.html");
  fs.writeFileSync(pageFile, PAGE.replace("__PANEL__", PANEL));
  const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });
  try { execFileSync(process.execPath, ["-e", "setTimeout(()=>{},900)"]); } catch (_) {}
  try {
    // Five shapes, not one: the desktop the panel was designed on, the laptop
    // most people actually play on, the width the two-column bottom row folds
    // at, a small laptop, and a phone.
    //   (500 is the phone row: headless Chrome refuses to open a window
    //   narrower than about 500px, so that is the narrowest shape that can
    //   honestly be measured here.)
    for (const [w, h] of [[1920, 1080], [1440, 900], [1180, 820], [1024, 768], [500, 900]]) {
      let r = null;
      for (let attempt = 0; attempt < 3 && !r; attempt++) {
        let dom = "";
        try {
          dom = execFileSync(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars", `--window-size=${w},${h}`, "--virtual-time-budget=20000",
            "--dump-dom", `http://localhost:${PORT}/__strat_panel_probe.html`],
            { encoding: "utf8", maxBuffer: 64e6, stdio: ["ignore", "pipe", "ignore"], timeout: 90000 });
        } catch (_) { continue; }
        const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
        const raw = m ? m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                            .replace(/&lt;/g, "<").replace(/&gt;/g, ">") : "";
        if (!raw || raw === "PENDING") continue;
        try { r = JSON.parse(raw); } catch (_) { /* retry */ }
      }
      if (!r) { check(false, `${w}x${h}: the panel never reported back`); continue; }
      const at = `${w}x${h}: `;
      check(r.listOverflow <= 1 && r.bodyOverflow <= 1,
        at + "nothing pushes the panel sideways (list " + r.listOverflow + "px, page " + r.bodyOverflow + "px)");
      check(r.recoW > 200 && r.recoH > 80, at + "the recommendation card is a card (" + r.recoW + "x" + r.recoH + ")");
      check(r.recoLeft >= -1 && r.recoRight <= r.w + 1,
        at + "and both its edges are on screen (" + r.recoLeft + "-" + r.recoRight + " inside " + r.w + ")");
      check(r.alts === 3, at + "all three runners-up are drawn, got " + r.alts);
      check(r.escapes === 0 && r.zero === 0,
        at + "the fit badge, the warning and the runners-up stay inside it (" + r.escapes + " escaped, " + r.zero + " collapsed)");
      check(r.thinCombos === 0, at + "no combo card is squeezed to a sliver (" + r.thinCombos + " of " + r.combos + ")");
      check(r.panelsScroll >= 2, at + "the two panels scroll their own overflow, they do not stretch the page");
    }
  } finally {
    try { server.kill(); } catch (_) {}
    try { fs.unlinkSync(pageFile); } catch (_) {}
  }
}

console.log("\n" + "─".repeat(60));
console.log(`${checks - failures} passed, ${failures} failed`);
process.exit(failures ? 1 : 0);
