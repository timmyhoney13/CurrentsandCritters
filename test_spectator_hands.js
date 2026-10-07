#!/usr/bin/env node
/* A watcher sees the hand and the cursor, the client half.
 * (multiplayer/client/js/preview-app.js, multiplayer/client/preview.html)
 *
 * Run:  node test_spectator_hands.js
 *
 * Server side is test_spectator_hands.py. This half pins the four client jobs:
 * draw the watched player's hand read-only, place their cursor from the
 * fractions the server sends, work out which part of the table OUR cursor is
 * over before sending it, and send nothing at all when nobody is watching.
 *
 * The functions live inside an IIFE that needs a live DOM, so we lift their
 * exact source text out of the file and run it against stubs. If a function is
 * renamed or reshaped, extraction fails loudly rather than quietly testing
 * nothing.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const APP = fs.readFileSync(
  path.join(__dirname, "multiplayer", "client", "js", "preview-app.js"), "utf8");
const HTML = fs.readFileSync(
  path.join(__dirname, "multiplayer", "client", "preview.html"), "utf8");
const CSS = fs.readFileSync(
  path.join(__dirname, "multiplayer", "client", "css", "preview.css"), "utf8");

let failures = 0, checks = 0;
function ok(cond, label) {
  checks++;
  if (cond) return;
  failures++; console.log("  \u2717 " + label);
}
function eq(actual, expected, label) {
  ok(JSON.stringify(actual) === JSON.stringify(expected),
     label + "  (got " + JSON.stringify(actual) + ", want " + JSON.stringify(expected) + ")");
}

// ── Lift the functions under test out of the module ─────────────────────────
function extract(name) {
  const decl = "function " + name + "(";
  const start = APP.indexOf(decl);
  if (start < 0) throw new Error("could not find function " + name + " in preview-app.js");
  const open = APP.indexOf("{", APP.indexOf(")", start));
  let depth = 0, i = open;
  for (; i < APP.length; i++) {
    if (APP[i] === "{") depth++;
    else if (APP[i] === "}") { depth--; if (depth === 0) break; }
  }
  if (depth !== 0) throw new Error("unbalanced braces extracting " + name);
  return APP.slice(start, i + 1);
}

// ── A DOM small enough to reason about ──────────────────────────────────────
function El(tag) {
  const el = {
    tagName: tag, children: [], dataset: {}, style: {},
    className: "", textContent: "", title: "", src: "", alt: "", loading: "",
    scrollLeft: 0, scrollTop: 0, _rect: null, _listeners: {},
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    appendChild(c) { el.children.push(c); return c; },
    addEventListener(kind, fn) { (el._listeners[kind] = el._listeners[kind] || []).push(fn); },
    getBoundingClientRect() {
      return el._rect || { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
    },
    querySelectorAll(sel) { return matchAll(el, sel); },
    querySelector(sel) { return matchAll(el, sel)[0] || null; },
    click() { (el._listeners.click || []).forEach(f => f({ stopPropagation() {} })); },
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return ""; },
    set(v) { if (v === "") el.children = []; },
  });
  return el;
}
// Only the two selector shapes the code under test uses:
//   ".spec-hand-card.hovered"  and  '.spec-hand-card[data-face-uid="17"]'
function matchAll(root, sel) {
  const attr = /\[data-face-uid="([^"]*)"\]/.exec(sel);
  const classes = sel.replace(/\[[^\]]*\]/g, "").split(".").filter(Boolean);
  const out = [];
  (function walk(node) {
    (node.children || []).forEach(c => {
      const own = String(c.className || "").split(/\s+/).filter(Boolean);
      const hit = classes.every(k => own.includes(k) || c.classList.contains(k))
               && (!attr || String(c.dataset.faceUid) === attr[1]);
      if (hit) out.push(c);
      walk(c);
    });
  })(root);
  return out;
}
function descendants(root, cls) {
  return matchAll(root, "." + cls);
}

// ── Harness ─────────────────────────────────────────────────────────────────
function harness(opts = {}) {
  const els = {};
  const zooms = [];
  const document = {
    hidden: false,
    getElementById(id) { return (els[id] = els[id] || El("div")); },
    createElement(tag) { return El(tag); },
    querySelectorAll() { return []; },
    addEventListener() {},
    elementFromPoint() { return opts.pointAt || null; },
  };
  const src =
    extract("_focusStateKey") + "\n" +
    extract("_specRenderHand") + "\n" +
    extract("_specPaintPointer") + "\n" +
    extract("_ptrSharingOn") + "\n" +
    extract("_ptrZoneAt") + "\n" +
    "return { _focusStateKey, _specRenderHand, _specPaintPointer, _ptrSharingOn, _ptrZoneAt," +
    "  setPointers(p) { _specPointers = p; }," +
    "  setViewing(i) { _spectatorViewingIdx = i; }," +
    "  setWatched(n) { _specWatchedName = n; }," +
    "  setWatchers(n) { _ptrWatchers = n; } };";

  const ctx = { els, zooms };
  const fn = new Function(
    "document", "window", "ctx",
    "let _specPointers = [], _spectatorViewingIdx = null, _specWatchedName = '';" +
    "let _ptrWatchers = 0;" +
    "let roomId = " + JSON.stringify(opts.roomId ?? null) + ";" +
    "let latestPayload = " + JSON.stringify(opts.payload ?? null) + ";" +
    "function isSpectating() { return " + (opts.spectating ? "true" : "false") + "; }" +
    "function getSeatToken() { return " + JSON.stringify(opts.seatToken ?? "") + "; }" +
    "function cl(el) { el.children = []; }" +
    "function imagePathForUid(uid) { return '/cards/' + uid + '.jpg'; }" +
    "function openZoom(uid, name, text, species, hand, idx) {" +
    "  ctx.zooms.push({ uid, name, species, handLen: hand ? hand.length : 0, idx }); }" +
    "function _handCardAt() { return " + (opts.handCardAt ? "ctx.handCard" : "null") + "; }" +
    src);
  if (opts.handCardAt) ctx.handCard = { dataset: { faceUid: String(opts.handCardAt) } };
  // els is filled lazily by getElementById, exactly as the real document is, so
  // the test asks for an element the same way the code under test does.
  const el = (id) => document.getElementById(id);
  return { ctx, els, el, api: fn(document, {}, ctx) };
}

function card(uid, name, species) {
  return { entry_uid: uid, faces: [{ uid, name, species: species || "Fish", text: "", cost: 1 }] };
}

// ════════════════════════════════════════════════════════════════════════════
console.log("\n1. The watched player's hand is drawn, read-only");
{
  const h = harness({ spectating: true });
  h.api._specRenderHand({ index: 2, name: "Nansen",
    hand: [card(1, "Clownfish"), card(3, "Osprey", "Bird"), card(5, "Barracuda")] });

  ok(h.el("pv-spec-hand").classList.contains("visible"), "the hand panel is shown");
  const cards = descendants(h.el("pv-spec-hand-cards"), "spec-hand-card");
  eq(cards.length, 3, "one element per card in their hand");
  eq(cards.map(c => c.children[0].src),
     ["/cards/1.jpg", "/cards/3.jpg", "/cards/5.jpg"],
     "each one shows that card's own art");
  eq(cards.map(c => c.dataset.faceUid), ["1", "3", "5"],
     "and carries its face uid, which is what their cursor is reported against");
  eq(cards.map(c => c.dataset.idx), ["0", "1", "2"], "in hand order");
  eq(h.el("pv-spec-hand-title").textContent, "Nansen's Hand, 3 cards",
     "the title names whose hand it is, and how big");
  ok(/zoom/i.test(cards[0].title), "and each card says a click zooms it");

  // Read-only by construction: nothing a hand card can normally do is here.
  const handFn = extract("_specRenderHand");
  ok(!/draggable/.test(handFn), "no drag: a watcher cannot move their cards");
  ok(!/selectedPayment|selectedDiscard|submitAction|apiPost/.test(handFn),
     "no payment, no discard, no action: nothing here can reach the engine");
  ok(!/applyMySkin/.test(handFn),
     "and no prestige skin, which would imply they own one they don't");
}

console.log("2. Clicking a card in their hand zooms it, and pages the hand");
{
  const h = harness({ spectating: true });
  const hand = [card(1, "Clownfish"), card(3, "Osprey", "Bird")];
  h.api._specRenderHand({ index: 0, name: "Tim", hand });
  descendants(h.el("pv-spec-hand-cards"), "spec-hand-card")[1].click();
  eq(h.ctx.zooms, [{ uid: 3, name: "Osprey", species: "Bird", handLen: 2, idx: 1 }],
     "the zoom opens on the card clicked, with the whole hand for its arrows");
}

console.log("3. An empty hand, and nobody being watched");
{
  const h = harness({ spectating: true });
  h.api._specRenderHand({ index: 1, name: "Ada", hand: [] });
  ok(h.el("pv-spec-hand").classList.contains("visible"), "the panel still shows");
  eq(descendants(h.el("pv-spec-hand-cards"), "spec-hand-empty").length, 1,
     "with a line saying the hand is empty");
  eq(h.el("pv-spec-hand-title").textContent, "Ada's Hand",
     "and a title with no count in it");

  h.api._specRenderHand(null);
  ok(!h.el("pv-spec-hand").classList.contains("visible"),
     "no player being watched puts the panel away");
  eq(h.el("pv-spec-hand-cards").children.length, 0, "and empties it");
}

console.log("4. Their cursor lands on OUR copy of the box");
{
  const h = harness({ spectating: true });
  const overlay = h.el("pv-board-focus");
  overlay._rect = { left: 0, top: 0, right: 1000, bottom: 800, width: 1000, height: 800 };
  const handBox = h.el("pv-spec-hand-cards");
  handBox._rect = { left: 100, top: 600, right: 500, bottom: 700, width: 400, height: 100 };
  // The hand has to be inside the overlay for the hover lookup to find it.
  overlay.appendChild(handBox);
  h.api._specRenderHand({ index: 2, name: "Nansen", hand: [card(7, "King Salmon")] });
  h.api.setViewing(2);
  h.api.setWatched("Nansen");
  h.api.setPointers([{ index: 2, zone: "hand", nx: 0.5, ny: 0.25, hover_uid: 7 }]);
  h.api._specPaintPointer();

  const ghost = h.el("pv-spec-cursor");
  ok(ghost.classList.contains("visible"), "the cursor is shown");
  eq([ghost.style.left, ghost.style.top], ["300px", "625px"],
     "half way across the hand box and a quarter down it");
  eq(h.el("pv-spec-cursor-name").textContent, "Nansen",
     "the arrow wears their name, because eight seats can be on screen");
  eq(descendants(handBox, "spec-hand-card")
       .filter(c => c.classList.contains("hovered")).map(c => c.dataset.faceUid), ["7"],
     "and the card they are hovering is lifted");

  // Their cursor moves off the card: the lift goes with it.
  h.api.setPointers([{ index: 2, zone: "hand", nx: 0.9, ny: 0.5, hover_uid: 0 }]);
  h.api._specPaintPointer();
  eq(descendants(handBox, "spec-hand-card").filter(c => c.classList.contains("hovered")).length, 0,
     "nothing stays lifted once they are not on it");
  eq(ghost.style.left, "460px", "and the arrow has moved");

  // A scrolled overlay: an absolutely-positioned child is placed from its
  // UNSCROLLED corner, so the scroll offset has to go back on.
  overlay.scrollTop = 120;
  h.api.setPointers([{ index: 2, zone: "hand", nx: 0, ny: 0, hover_uid: 0 }]);
  h.api._specPaintPointer();
  eq(ghost.style.top, "720px", "the overlay's own scroll is accounted for");
  overlay.scrollTop = 0;

  // Their cursor over their board lands in the enlarged board instead.
  const boardBox = h.el("pv-board-focus-content");
  boardBox._rect = { left: 0, top: 0, right: 800, bottom: 400, width: 800, height: 400 };
  h.api.setPointers([{ index: 2, zone: "board", nx: 0.25, ny: 0.5, hover_uid: 0 }]);
  h.api._specPaintPointer();
  eq([ghost.style.left, ghost.style.top], ["200px", "200px"],
     "a cursor on their board is drawn on the board we are looking at");

  // Nothing to draw: off the table, watching somebody else, or the pool, which
  // this overlay has no copy of.
  h.api.setPointers([{ index: 2, zone: "", nx: 0, ny: 0, hover_uid: 0 }]);
  h.api._specPaintPointer();
  ok(!ghost.classList.contains("visible"), "off the table, and the cursor goes");
  h.api.setPointers([{ index: 5, zone: "hand", nx: 0.5, ny: 0.5, hover_uid: 0 }]);
  h.api._specPaintPointer();
  ok(!ghost.classList.contains("visible"), "another player's cursor is not drawn here");
  h.api.setPointers([{ index: 2, zone: "pool", nx: 0.5, ny: 0.5, hover_uid: 0 }]);
  h.api._specPaintPointer();
  ok(!ghost.classList.contains("visible"),
     "and a zone this overlay has no copy of draws nothing, rather than guessing");
  h.api.setPointers([]);
  h.api._specPaintPointer();
  ok(!ghost.classList.contains("visible"), "a player who is reporting nothing draws nothing");
}

console.log("5. Which part of the table our own cursor is over");
{
  const h = harness({ roomId: "ABC", seatToken: "t", payload: { room: { phase: "running" } } });
  h.el("pv-hand")._rect =
    { left: 200, top: 500, right: 600, bottom: 600, width: 400, height: 100 };
  h.el("pv-my-board")._rect =
    { left: 0, top: 0, right: 1000, bottom: 400, width: 1000, height: 400 };
  h.el("pv-pool-wrap")._rect =
    { left: 0, top: 410, right: 300, bottom: 470, width: 300, height: 60 };

  eq(h.api._ptrZoneAt(400, 550), { zone: "hand", nx: 0.5, ny: 0.5 },
     "a cursor in the hand is reported as a fraction of the hand");
  eq(h.api._ptrZoneAt(500, 200), { zone: "board", nx: 0.5, ny: 0.5 },
     "and one on the board as a fraction of the board");
  eq(h.api._ptrZoneAt(150, 440), { zone: "pool", nx: 0.5, ny: 0.5 },
     "and one on the pool as a fraction of the pool");
  ok(h.api._ptrZoneAt(900, 780) === null,
     "and one over nothing in particular is nothing, not a guess");

  // The fan is drawn well outside #pv-hand's own box, which is why
  // _setupHandHover hit-tests rather than trusting the event target. The
  // catchment here has to match, or the cursor jumps off the cards.
  const below = h.api._ptrZoneAt(400, 650);
  ok(below && below.zone === "hand",
     "a cursor on the part of the fan that hangs below the box is still the hand");
  const above = h.api._ptrZoneAt(400, 450);
  ok(above && above.zone === "hand",
     "and so is one on the card that has lifted above it");
  const edge = h.api._ptrZoneAt(170, 650);
  ok(edge && edge.nx >= 0 && edge.nx <= 1 && edge.ny >= 0 && edge.ny <= 1,
     "a position outside the box is clamped into it, never sent as -0.07");

  // A zone that is not on screen has no box to be a fraction of.
  const blank = harness({ roomId: "ABC", seatToken: "t",
                          payload: { room: { phase: "running" } } });
  ok(blank.api._ptrZoneAt(400, 550) === null,
     "a table that has not laid out yet reports no zone at all");
}

console.log("6. Nothing is sent unless somebody is watching");
{
  const base = { roomId: "ABC", seatToken: "t", payload: { room: { phase: "running" } } };
  const h = harness(base);
  ok(h.api._ptrSharingOn() === false, "no watchers, no sending");
  h.api.setWatchers(1);
  ok(h.api._ptrSharingOn() === true, "one watcher turns it on");

  const noSeat = harness({ ...base, seatToken: "" });
  noSeat.api.setWatchers(1);
  ok(noSeat.api._ptrSharingOn() === false, "no seat, nothing to share");

  const watching = harness({ ...base, spectating: true });
  watching.api.setWatchers(1);
  ok(watching.api._ptrSharingOn() === false,
     "a watcher's own cursor is nobody's business");

  const lobby = harness({ ...base, payload: { room: { phase: "lobby" } } });
  lobby.api.setWatchers(1);
  ok(lobby.api._ptrSharingOn() === false, "the lobby has no table to point at");

  const noRoom = harness({ ...base, roomId: null });
  noRoom.api.setWatchers(1);
  ok(noRoom.api._ptrSharingOn() === false, "and no room means no pushing");

  const bare = harness({ ...base, payload: null });
  bare.api.setWatchers(1);
  ok(bare.api._ptrSharingOn() === false,
     "a payload we have not had yet is not a running game");

  // The watcher count comes off the state poll, which every client already
  // makes: this is what keeps an unwatched game free.
  ok(/_ptrWatchers = payload\.spectators\.length/.test(APP),
     "the count is read straight off the spectator list in the poll");
  const send = extract("_ptrSend");
  ok(/if \(sig === _ptrLastSig\) return;/.test(send),
     "a cursor that has not moved is not re-sent");
  ok(/retries: 0/.test(send),
     "and a missed cursor frame is not retried: the next one is 140ms away");
  ok(/typeof mySeatIdx === "number"/.test(send),
     "the seat being drawn is named, for competitive's two hands");
  ok(/_ptrSharingOn\(\)/.test(extract("_ptrNote")),
     "and the move handler itself checks before it even queues a send");
}

console.log("7. The open view keeps up with the game");
{
  const h = harness({ spectating: true });
  const p = { index: 2, name: "Nansen", score: 10, board: [], hand: [card(1, "Clownfish")] };
  const k1 = h.api._focusStateKey(p);
  eq(h.api._focusStateKey({ ...p }), k1, "an unchanged player keys the same");
  ok(h.api._focusStateKey({ ...p, hand: [card(3, "Osprey")] }) !== k1,
     "a different hand does not");
  ok(h.api._focusStateKey({ ...p, score: 11 }) !== k1, "nor a changed score");
  ok(h.api._focusStateKey({ ...p, board: [{ ocean_uid: 201 }] }) !== k1,
     "nor a card landing on their board");

  const open = extract("openBoardFocus");
  ok(/if \(live && \(!isOpen \|\| !sameOne\)\) return;/.test(open),
     "a live refresh never opens the overlay, nor swaps it to another player");
  ok(/if \(live && isOpen && sameOne && key === _focusKey\) return;/.test(open),
     "and rebuilds nothing when nothing moved");
  ok(/openBoardFocus\(watched, \{ live: true \}\)/.test(APP),
     "every poll offers the open view the watched player's new state");
  const close = extract("closeBoardFocus");
  ok(/_specStopPointerPoll\(\)/.test(close) && /_specRenderHand\(null\)/.test(close),
     "closing it stops the cursor poll and puts the hand away");
  const poll = extract("_specStartPointerPoll");
  ok(/_spectatorViewingIdx == null/.test(poll) && /_specFocusOpen\(\)/.test(poll),
     "the cursor poll stops itself the moment nobody is being watched");
  ok(/document\.hidden/.test(poll), "and pauses while the tab is in the background");
  ok(/_specStopPointerPoll\(\); _specRenderHand\(null\);/.test(extract("_specShowPanel")),
     "and leaving the game entirely puts both away too");
}

console.log("8. The page, the stylesheet and the What's New entry");
{
  ["pv-spec-hand", "pv-spec-hand-title", "pv-spec-hand-cards",
   "pv-spec-cursor", "pv-spec-cursor-name"].forEach(id => {
    ok(HTML.includes(`id="${id}"`), `preview.html has #${id}`);
  });
  const focusBlock = HTML.slice(HTML.indexOf('<div id="pv-board-focus">'),
                                HTML.indexOf('<div id="pv-board-hover">'));
  ok(focusBlock.includes('id="pv-spec-hand"') && focusBlock.includes('id="pv-spec-cursor"'),
     "the hand and the cursor live inside the enlarged-board overlay");

  ok(/#pv-spec-hand\.visible/.test(CSS), "the panel has a shown state");
  ok(/\.spec-hand-card\.hovered/.test(CSS),
     "their hovered card has a look of its own");
  ok(/#pv-spec-cursor \{[^}]*pointer-events:\s*none/.test(CSS),
     "the ghost cursor never eats a click meant for a card");
  ok(/#pv-spec-hand-cards \{[^}]*overflow-x:\s*auto/.test(CSS),
     "a big hand scrolls sideways rather than pushing the board off screen");
  ok(/pv-spec-hand/.test(extract("_refitBoardFocus")),
     "and the board is fitted to what is left after the hand takes its height");

  // The changelog is player-facing text: the house rules apply.
  const log = APP.slice(APP.indexOf("const APP_CHANGELOG = ["),
                        APP.indexOf("function pad2("));
  ok(/Watching a player means watching their hand/.test(log),
     "What's New says the watching change");
  ok(/The bots have names/.test(log), "and that the bots have names");
  ok(/real person who went out and looked at the sea/.test(log),
     "and says who they are, not just what they are called");
  ok(!/\u2014/.test(log), "no em dashes in player-facing text");
  ok(!/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(log), "and no emoji");

  // Every name in the pool is in the entry: the point of the entry is that a
  // player can read who they are playing against.
  const PY = fs.readFileSync(path.join(__dirname, "fish_game_all_in_one.py"), "utf8");
  const pool = PY.slice(PY.indexOf("OCEAN_EXPLORER_NAMES: Tuple[str, ...] = ("));
  const names = [...pool.slice(0, pool.indexOf("\n)")).matchAll(/^\s*"([A-Za-z]+)",/gm)]
                  .map(m => m[1]);
  ok(names.length > 20, `the pool was found and read (${names.length} names)`);
  eq(names.filter(n => !log.includes(n)), [],
     "every explorer the game can name is named in the update");
}

console.log(`\nwatched hand + cursor checks: ${checks}`);
if (failures) { console.log(`${failures} FAILED`); process.exit(1); }
console.log("watched hands OK");
