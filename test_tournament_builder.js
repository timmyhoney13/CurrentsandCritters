#!/usr/bin/env node
/* Tests for the canvas bracket BUILDER's pure design logic
 * (multiplayer/client/js/tournament-builder.js).
 *
 * Run:  node test_tournament_builder.js
 *
 * The builder generates designs — quick-start templates and "Add Round" — that the
 * SERVER has to accept. So every design produced here is checked twice: once by the
 * builder's own mirror of the rules, and once by the real Python validator in
 * tournament_engine.py (via test_tournament_builder_check.py). That parity is the
 * whole point: a bracket that builds cleanly must never be rejected on create.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = __dirname;

// ── the tiniest DOM the module needs to evaluate ────────────────────────────
function stubNode() {
  return {
    style: {}, dataset: {}, value: "", textContent: "", innerHTML: "",
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, appendChild() {}, setAttribute() {},
    querySelector: () => null, querySelectorAll: () => [],
  };
}
global.window = {};
global.document = {
  head: stubNode(), body: stubNode(),
  addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
  createElement: stubNode, createElementNS: stubNode,
};
global.setTimeout = () => 0;
global.clearTimeout = () => {};

eval(fs.readFileSync(path.join(ROOT, "multiplayer/client/js/tournament-builder.js"), "utf8"));
const TB = global.window.__ccTourneyBuilder;
const { buildTemplate, packRound, pendingFeeds, layers, toDesign, loadDesign } = TB._test;
const B = TB._state;

// ── tiny test harness ───────────────────────────────────────────────────────
let passed = 0;
const failures = [];
function test(name, fn) {
  try { fn(); passed += 1; }
  catch (e) { failures.push(`${name}\n    ${e.message}`); }
}
function eq(a, b, msg) {
  const sa = JSON.stringify(a), sb = JSON.stringify(b);
  if (sa !== sb) throw new Error(`${msg || "not equal"}: ${sa} !== ${sb}`);
}
function ok(v, msg) { if (!v) throw new Error(msg || "expected truthy"); }

// Batch every design through the real Python validator in one subprocess.
const pyQueue = [];
function checkOnServer(label, design) { pyQueue.push({ label, design: JSON.parse(JSON.stringify(design)) }); }
function flushServerChecks() {
  if (!pyQueue.length) return [];
  const out = execFileSync("python3", [path.join(ROOT, "test_tournament_builder_check.py")], {
    input: JSON.stringify(pyQueue), encoding: "utf8", cwd: ROOT,
  });
  return JSON.parse(out);
}

// ============================================================================
// packRound — the split that "Add Round" and the templates both use
// ============================================================================
test("packRound splits evenly and never makes a bye", () => {
  eq(packRound(8, 2), [2, 2, 2, 2]);
  eq(packRound(4, 2), [2, 2]);
  eq(packRound(16, 4), [4, 4, 4, 4]);
  // 5 into 1v1 would be [2,2,1] — a bye, illegal in a designed bracket — so the
  // odd player is folded into a 3-player match instead.
  eq(packRound(5, 2), [3, 2]);
  eq(packRound(6, 4), [3, 3]);
  eq(packRound(7, 2), [3, 2, 2]);
  eq(packRound(9, 2), [3, 2, 2, 2]);
});

test("packRound auto mode makes one Final when everyone fits", () => {
  eq(packRound(6, 0), [6]);
  eq(packRound(8, 0), [8]);
  eq(packRound(2, 0), [2]);
  eq(packRound(12, 0), [2, 2, 2, 2, 2, 2]);   // too many for one match -> 1v1s
});

test("packRound refuses impossible splits", () => {
  eq(packRound(1, 2), null);
  eq(packRound(0, 4), null);
});

test("packRound never exceeds the 8-player match cap", () => {
  for (let n = 2; n <= 32; n++) {
    for (let per = 0; per <= 8; per++) {
      const sizes = packRound(n, per);
      if (!sizes) continue;
      ok(sizes.every(s => s >= 2 && s <= 8), `n=${n} per=${per} -> ${sizes}`);
      eq(sizes.reduce((a, b) => a + b, 0), n, `n=${n} per=${per} sum`);
    }
  }
});

// ============================================================================
// buildTemplate — a whole bracket from (players, per match, top N advance)
// ============================================================================
test("single elimination template has the classic shape", () => {
  const built = buildTemplate(8, 2, 1);
  eq(built, { rounds: 3, matches: 7 });
  eq(layers().map(r => r.length), [4, 2, 1]);
  eq(B.matches[B.matches.length - 1].label, "Final");
  eq(B.matches[B.matches.length - 1].advance, 1);
});

test("top-2 template turns each match into a group", () => {
  const built = buildTemplate(16, 4, 2);
  eq(built, { rounds: 3, matches: 7 });
  eq(layers().map(r => r.length), [4, 2, 1]);
  const opening = layers()[0];
  ok(opening.every(m => m.slots.length === 4 && m.advance === 2), "opening: 4 players, top 2 out");
  eq(layers()[2][0].advance, 1, "the Final crowns exactly one champion");
});

test("every template's entry spots equal the requested field", () => {
  for (let n = 4; n <= 32; n++) {
    for (let per = 2; per <= 8; per++) {
      if (per > n) continue;
      for (let adv = 1; adv < per; adv++) {
        const built = buildTemplate(n, per, adv);
        if (!built) continue;
        const entries = B.matches.reduce(
          (a, m) => a + m.slots.filter(s => ["open", "human", "ai", "invite"].includes(s.kind)).length, 0);
        eq(entries, n, `players ${n}/${per}/top${adv}`);
        eq(TB.validate(), [], `validator on ${n}/${per}/top${adv}`);
        // exactly one Final, and it advances one champion
        const rows = layers();
        eq(rows[rows.length - 1].length, 1, `one Final for ${n}/${per}/top${adv}`);
        eq(rows[rows.length - 1][0].advance, 1);
        checkOnServer(`${n}/${per}/top${adv}`, toDesign());
      }
    }
  }
});

test("template respects the 32-match ceiling", () => {
  const built = buildTemplate(32, 2, 1);
  eq(built, { rounds: 5, matches: 31 });
  eq(TB.validate(), []);
});

// ============================================================================
// pendingFeeds — what "Add Round" will pick up
// ============================================================================
test("a finished bracket only has its champion 'advancing'", () => {
  buildTemplate(8, 2, 1);
  // The Final's winner is the champion — they advance to nowhere by design, which
  // is why "Add Round" refuses to act on a single pending finisher.
  const pend = pendingFeeds();
  eq(pend.length, 1);
  const rows = layers();
  eq(pend[0].mid, rows[rows.length - 1][0].id);
  eq(pend[0].rank, 1);
});

test("a bare round of matches offers every advancing finisher", () => {
  B.matches = [];
  B.seq = 0;
  loadDesign({
    matches: [
      { id: "a", advance: 2, slots: [{ kind: "open" }, { kind: "open" }, { kind: "open" }, { kind: "open" }] },
      { id: "b", advance: 2, slots: [{ kind: "open" }, { kind: "open" }, { kind: "open" }, { kind: "open" }] },
    ],
  });
  const pend = pendingFeeds();
  eq(pend.length, 4, "two matches sending their top 2 on");
  eq(pend.map(p => `${p.mid}#${p.rank}`), ["a#1", "a#2", "b#1", "b#2"]);
  // and that is exactly one 4-player Final
  eq(packRound(pend.length, 0), [4]);
});

test("loadDesign round-trips through toDesign", () => {
  buildTemplate(12, 3, 1);
  const before = toDesign();
  loadDesign(before);
  eq(toDesign(), before);
  eq(TB.validate(), []);
});

// ============================================================================
// report
// ============================================================================
const serverResults = flushServerChecks();
const serverBad = serverResults.filter(r => r.errors && r.errors.length);
if (serverBad.length) {
  failures.push(`Python validator rejected ${serverBad.length} builder design(s), e.g.\n    ` +
    `${serverBad[0].label}: ${serverBad[0].errors.join("; ")}`);
} else {
  passed += 1;
  console.log(`  server parity: ${serverResults.length} generated designs accepted by tournament_engine.py`);
}

console.log(`\n${passed} passed, ${failures.length} failed`);
if (failures.length) {
  failures.forEach(f => console.error("  FAIL " + f));
  process.exit(1);
}
