#!/usr/bin/env node
/* Competitive: each of your TWO hands keeps its own card arrangement.
 *
 * Run:  node test_competitive_hand_order.js
 *
 * You can drag cards around inside your hand to arrange them, and that
 * arrangement is client-side only, the server never hears about it, so it
 * lives in one place in preview-app.js and nowhere else.
 *
 * The bug this pins down: that place used to be a SINGLE array, `_handOrder`.
 * That is fine for a normal game, where a client only ever draws one hand. It
 * is wrong for competitive, where ONE person owns TWO seats (P1 = {0,1},
 * P2 = {2,3}) and the renderer swaps between their hands every time the turn
 * passes between them. Drawing hand B ran
 *
 *     _handOrder = _handOrder.filter(uid => allEntryUids.includes(uid))
 *
 * over hand B's uids, which threw away every uid of hand A. Arrange hand A →
 * play hand B → come back to hand A and it had collapsed back into raw server
 * order. Every reorder you made was lost the moment your other hand played.
 *
 * The fix is `_handOrders`, a Map keyed by SEAT, so each hand keeps its own
 * arrangement for the whole game (a normal game simply only ever has one key),
 * plus one subtlety: the arrays are mutated IN PLACE, never reassigned, because
 * each card's drop handler captures its hand's array at render time and must
 * still be holding the live array several renders later.
 *
 * Everything below runs the REAL code, the ordering block, the seat-key
 * lookup and the drop-handler reorder are sliced out of preview-app.js by text
 * and executed, so a change to those lines changes what this test runs.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = __dirname;
const APP_PATH = path.join(ROOT, "multiplayer/client/js/preview-app.js");
const APP = fs.readFileSync(APP_PATH, "utf8");

let failures = 0;
let checks = 0;
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
// Slice from the first line containing `from` up to and including the line
// containing the first `to` after it.
function grabBlock(from, to, what) {
  const a = APP.indexOf(from);
  if (a < 0) throw new Error(`could not find start of ${what}: ${from}`);
  const b = APP.indexOf(to, a);
  if (b < 0) throw new Error(`could not find end of ${what}: ${to}`);
  return APP.slice(a, b + to.length);
}

// The whole line a needle sits on.
function grabLine(needle, what) {
  const i = APP.indexOf(needle);
  if (i < 0) throw new Error(`could not find ${what}: ${needle}`);
  const a = APP.lastIndexOf("\n", i) + 1;
  const b = APP.indexOf("\n", i);
  return APP.slice(a, b < 0 ? APP.length : b);
}

const DECL_ORDERS   = grabLine("const _handOrders = new Map();", "the _handOrders map");
const FN_ORDER_FOR  = grabFn("handOrderFor");
const FN_KEY_FOR    = grabFn("handOrderKeyFor");
const LOOKUP_BLOCK  = grabBlock("const handSeatKey = handOrderKeyFor(me);",
                                "const handOrder   = handOrderFor(handSeatKey);",
                                "the per-hand order lookup in renderHand");
const MAINTAIN_BLOCK = grabBlock("// Maintain this hand's display order",
                                 "return handOrder.indexOf(ua) - handOrder.indexOf(ub);\n    });",
                                 "the order-maintenance block in renderHand");
const REORDER_BLOCK = grabBlock("const srcIdx = order.indexOf(srcUid);",
                                "order.splice(newDst, 0, srcUid);\n        }",
                                "the in-hand reorder in the drop handler");

// ── Build a sandbox that runs those exact lines ──────────────────────────────
// renderHand's real job around this code is: take the server's hand, slice it
// to what is visible, apply the saved arrangement, and paint. We keep the
// arrangement half verbatim and stub only the painting.
const SRC = `
  ${DECL_ORDERS}
  ${FN_ORDER_FOR}
  ${FN_KEY_FOR}

  // The arrangement half of renderHand(), verbatim.
  function renderHandOrder(me) {
    const visible = (me.hand || []).slice();
    ${LOOKUP_BLOCK}
    ${MAINTAIN_BLOCK}
    return { seatKey: handSeatKey, order: handOrder, visible };
  }

  // Handles to the private state, for the checks below.
  function _ordersMap() { return _handOrders; }

  // The in-hand reorder half of the drop handler, verbatim.
  function dropReorder(order, srcUid, entryUid) {
    ${REORDER_BLOCK}
  }
`;
const sandbox = { myIdx: null, console };
vm.createContext(sandbox);
vm.runInContext(SRC, sandbox, { filename: "lifted-from-preview-app.js" });

// ── A competitive game: I own seats 0 and 1 ──────────────────────────────────
// Server hand order is what the server sends; the arrangement is mine.
const HAND_A = [101, 102, 103, 104, 105];   // seat 0
const HAND_B = [201, 202, 203, 204, 205];   // seat 1
const seat = (index, uids) => ({ index, hand: uids.map(u => ({ entry_uid: u, faces: [{ uid: u }] })) });
const uidsOf = r => r.visible.map(e => Number(e.entry_uid));

console.log("\nCompetitive: two hands, two arrangements");

// Draw hand A (seat 0) and arrange it: drag the last card to the front.
let a = sandbox.renderHandOrder(seat(0, HAND_A));
eq(uidsOf(a), HAND_A, "seat 0 first render follows server order");
sandbox.dropReorder(a.order, 105, 101);
a = sandbox.renderHandOrder(seat(0, HAND_A));
eq(uidsOf(a), [105, 101, 102, 103, 104], "seat 0: dragging 105 in front of 101 sticks");

// The turn passes to my other hand. Drawing it must not disturb hand A.
let b = sandbox.renderHandOrder(seat(1, HAND_B));
eq(uidsOf(b), HAND_B, "seat 1 first render follows server order");
sandbox.dropReorder(b.order, 201, 204);
b = sandbox.renderHandOrder(seat(1, HAND_B));
eq(uidsOf(b), [202, 203, 201, 204, 205], "seat 1: its own drag sticks");

// ── THE REGRESSION: come back to the first hand ──────────────────────────────
a = sandbox.renderHandOrder(seat(0, HAND_A));
eq(uidsOf(a), [105, 101, 102, 103, 104],
   "seat 0 KEEPS its arrangement after seat 1 played (the bug: it reverted to server order)");

// And bouncing back and forth keeps both, indefinitely.
for (let turn = 0; turn < 6; turn++) {
  a = sandbox.renderHandOrder(seat(0, HAND_A));
  b = sandbox.renderHandOrder(seat(1, HAND_B));
}
eq(uidsOf(a), [105, 101, 102, 103, 104], "seat 0 arrangement survives 6 more hand switches");
eq(uidsOf(b), [202, 203, 201, 204, 205], "seat 1 arrangement survives 6 more hand switches");

// Two hands, two independent entries in the map, not one shared array.
check(sandbox._ordersMap().size === 2, "_handOrders holds one order per seat, not one shared array");

console.log("\nThe arrangement survives the hand actually changing");

// Draw a card: it lands at the END, the arrangement is otherwise untouched.
a = sandbox.renderHandOrder(seat(0, [...HAND_A, 106]));
eq(uidsOf(a), [105, 101, 102, 103, 104, 106], "a drawn card is appended, arrangement kept");

// Play a card out of the middle: it leaves, the rest hold their places.
a = sandbox.renderHandOrder(seat(0, [101, 103, 104, 105, 106]));
eq(uidsOf(a), [105, 101, 103, 104, 106], "playing 102 removes only 102");

// Seat 1 is still exactly where I left it through all of that.
b = sandbox.renderHandOrder(seat(1, HAND_B));
eq(uidsOf(b), [202, 203, 201, 204, 205], "seat 1 untouched by everything seat 0 did");

console.log("\nThe drop handler's captured array stays live");

// Each card's drop handler closes over its hand's order array at RENDER time.
// Several renders (of both hands) happen before the player finishes a drag, so
// that captured reference has to still be the array the renderer reads, which
// is only true while the arrays are spliced in place and never reassigned.
const captured = sandbox.renderHandOrder(seat(0, [101, 103, 104, 105, 106])).order;
sandbox.renderHandOrder(seat(1, HAND_B));
sandbox.renderHandOrder(seat(0, [101, 103, 104, 105, 106]));
sandbox.renderHandOrder(seat(1, HAND_B));
sandbox.dropReorder(captured, 106, 105);          // drop lands on the OLD reference
a = sandbox.renderHandOrder(seat(0, [101, 103, 104, 105, 106]));
eq(uidsOf(a), [106, 105, 101, 103, 104],
   "a drop that closed over an earlier render still reorders the live hand");

console.log("\nA normal (one-seat) game is unchanged");

const solo = new vm.createContext({ myIdx: 3, console });
vm.runInContext(SRC, solo, { filename: "lifted-from-preview-app.js" });
let s = solo.renderHandOrder(seat(3, HAND_A));
eq(uidsOf(s), HAND_A, "solo: server order first");
solo.dropReorder(s.order, 103, 101);
s = solo.renderHandOrder(seat(3, HAND_A));
eq(uidsOf(s), [103, 101, 102, 104, 105], "solo: reorder sticks");
s = solo.renderHandOrder(seat(3, [101, 102, 104, 105, 106, 107]));
eq(uidsOf(s), [101, 102, 104, 105, 106, 107], "solo: 103 played, draws appended, rest kept");
check(solo._ordersMap().size === 1, "solo: exactly one order in the map");

console.log("\nThe old shared-array behaviour really was broken");

// Same code, but with every hand answering to ONE key, which is precisely
// what a single `_handOrder` array was. If this still preserved the order, the
// checks above would not be testing anything.
const shared = { myIdx: null, console };
vm.createContext(shared);
vm.runInContext(SRC + "\n handOrderKeyFor = function () { return 0; };", shared,
                { filename: "lifted-from-preview-app.js" });
shared.dropReorder(shared.renderHandOrder(seat(0, HAND_A)).order, 105, 101);
eq(uidsOf(shared.renderHandOrder(seat(0, HAND_A))), [105, 101, 102, 103, 104],
   "shared-array model: the reorder does stick while only one hand is drawn");
shared.renderHandOrder(seat(1, HAND_B));   // the other hand plays
check(JSON.stringify(uidsOf(shared.renderHandOrder(seat(0, HAND_A)))) !== JSON.stringify([105, 101, 102, 103, 104]),
      "shared-array model: the arrangement IS lost after the other hand, the bug reproduces");

// ── Source guards ────────────────────────────────────────────────────────────
console.log("\nSource guards");

check(!/\b_handOrder\b/.test(APP),
      "preview-app.js: the single shared `_handOrder` array is gone");
check(/_handOrders\.clear\(\)/.test(APP),
      "leaving a game clears every hand's arrangement (_handOrders.clear())");
check(/handSeat: handSeatKey/.test(APP),
      "the renderHand cache key carries the seat, so switching hands always repaints");
check(/handOrder: handOrder\.join\(","\)/.test(APP),
      "the renderHand cache key carries THIS hand's order, so a drag repaints");
check(/handOrder\.splice\(i, 1\)/.test(APP) && !/handOrder = handOrder\.filter/.test(APP),
      "departed cards are spliced out in place, the array is never reassigned");

// ── Result ───────────────────────────────────────────────────────────────────
console.log(`\n${checks - failures}/${checks} checks passed`);
if (failures) { console.log(`${failures} FAILED`); process.exit(1); }
console.log("competitive hand order OK");
