#!/usr/bin/env node
/* Competitive (free-for-all): CP by finishing place.
 *
 * Run:  node test_competitive_ffa_cp.js
 *
 * The second competitive mode is an ordinary 2-8 player game, people only, that
 * pays Competitive Points into the SAME comp_cp total and the same rank as
 * Competitive 1v1. What makes it its own thing is how it pays: by FINISHING
 * PLACE, on a straight line between two per-division end points, ffaTop (1st)
 * and ffaBottom (last).
 *
 * The design that has to survive every future retune of the table is:
 *
 *   • At the low ranks you cannot go backwards. Bronze and Silver keep a
 *     POSITIVE ffaBottom, so last place in an 8-player game still pays. A new
 *     player has nothing to lose by pressing play.
 *   • The higher you climb, the better you have to place just to break even.
 *     ffaBottom goes negative at Golden Grouper and keeps falling, so the
 *     break-even placement climbs monotonically through the table.
 *   • A big casual table never beats the 1v1 ladder: 1st place here is always
 *     worth less than a Competitive 1v1 win at the same rank.
 *
 * Those are properties of the NUMBERS, not of one hand-checked example, so this
 * test asserts them across every division and every legal table size. Retune
 * ffaTop/ffaBottom freely; break one of the three rules and this fails.
 *
 * The real _COMP_RANK_DIVS table and the real _compGetRankFromCp /
 * _compGetFfaCpDelta functions are sliced out of preview-app.js and executed,
 * so a change to those lines changes what this test runs.
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
  const same = got === want;
  checks++;
  if (!same) { failures++; console.log(`  ✗ ${label}\n      got  ${got}\n      want ${want}`); }
  else console.log("  ✓ " + label);
}

// ── Lift the real code out of preview-app.js ─────────────────────────────────
function grabFn(name, indent) {
  const pad = indent || "    ";
  const start = APP.indexOf(`\n${pad}function ${name}(`);
  if (start < 0) throw new Error(`function ${name}() not found in preview-app.js`);
  let depth = 0;
  for (let j = APP.indexOf("{", start); j < APP.length; j++) {
    const ch = APP[j];
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return APP.slice(start, j + 1); }
  }
  throw new Error(`unbalanced braces reading ${name}()`);
}
function grabBlock(from, to, what) {
  const a = APP.indexOf(from);
  if (a < 0) throw new Error(`could not find start of ${what}: ${from}`);
  const b = APP.indexOf(to, a);
  if (b < 0) throw new Error(`could not find end of ${what}: ${to}`);
  return APP.slice(a, b + to.length);
}

const TABLE      = grabBlock("const _COMP_RANK_DIVS = [", "\n    ];", "the rank table");
const FN_FROM_CP = grabFn("_compGetRankFromCp");
const FN_DELTA   = grabFn("_compGetCpDelta");
const FN_FFA     = grabFn("_compGetFfaCpDelta");

const sandbox = { window: {}, console };
vm.createContext(sandbox);
vm.runInContext(`${TABLE}\n${FN_FROM_CP}\n${FN_DELTA}\n${FN_FFA}\n`
  + "this.DIVS = _COMP_RANK_DIVS;"
  + "this.rankFromCp = _compGetRankFromCp;"
  + "this.cpDelta = _compGetCpDelta;"
  + "this.ffaDelta = _compGetFfaCpDelta;", sandbox);

const { DIVS, rankFromCp, cpDelta, ffaDelta } = sandbox;
// A CP value comfortably inside each division, to ask the delta functions with.
const probe = (d) => (d.maxCp === Infinity ? d.minCp + 50 : Math.floor((d.minCp + d.maxCp) / 2));
const SIZES = [2, 3, 4, 5, 6, 7, 8];

console.log("\n── the table carries a placement pair for every division ──");
check(DIVS.length === 16, `16 divisions (got ${DIVS.length})`);
check(DIVS.every(d => Number.isFinite(d.ffaTop) && Number.isFinite(d.ffaBottom)),
      "every division has a finite ffaTop and ffaBottom");
check(DIVS.every(d => d.ffaTop > d.ffaBottom),
      "1st place always beats last place in every division");

console.log("\n── low ranks: you cannot go backwards ──");
// This is the rule the mode was asked for: at the bottom of the ladder every
// placement pays, so a new player is never punished for showing up.
const SAFE = DIVS.filter(d => d.tier === "bronze" || d.tier === "silver");
check(SAFE.length === 6, `bronze + silver is 6 divisions (got ${SAFE.length})`);
for (const d of SAFE) {
  let worst = Infinity;
  for (const n of SIZES) for (let p = 1; p <= n; p++) worst = Math.min(worst, ffaDelta(probe(d), p, n, true));
  check(worst > 0, `${d.name}: every placement at every table size pays (worst = ${worst > 0 ? "+" : ""}${worst})`);
}
// An Unranked player (never played) is treated as the floor of the table, so
// their very first game cannot cost them anything either.
{
  let worst = Infinity;
  for (const n of SIZES) for (let p = 1; p <= n; p++) worst = Math.min(worst, ffaDelta(0, p, n, false));
  check(worst > 0, `Unranked: a first game always pays (worst = +${worst})`);
}

console.log("\n── high ranks: you have to place better to gain ──");
// Break-even placement, as a share of the table: the fraction of the field you
// must finish inside to come out ahead. It must never go DOWN as you climb.
function breakEvenShare(d) {
  const cp = probe(d);
  const n = 8;
  for (let p = 1; p <= n; p++) if (ffaDelta(cp, p, n, true) <= 0) return (p - 1) / n;
  return 1; // never negative at this rank
}
let prevShare = Infinity;
let tightened = false;
for (const d of DIVS) {
  const share = breakEvenShare(d);
  check(share <= prevShare + 1e-9,
        `${d.name}: break-even never gets EASIER than the rank below (${Math.round(share * 100)}% of the field)`);
  if (share < prevShare) tightened = true;
  prevShare = share;
}
check(tightened, "the break-even placement actually tightens somewhere up the ladder");
check(DIVS.filter(d => d.ffaBottom < 0).length > 0, "the upper ranks really can lose CP");
check(DIVS[DIVS.length - 1].ffaBottom < 0, "King of the Critters loses CP for a bad finish");
// At the very top, a mid-table finish is not good enough.
{
  const king = DIVS[DIVS.length - 1];
  check(ffaDelta(probe(king), 4, 8, true) < 0, "King: 4th of 8 is a CP LOSS");
  check(ffaDelta(probe(king), 1, 8, true) > 0, "King: 1st of 8 still gains");
}

console.log("\n── the free-for-all never out-pays the 1v1 ladder ──");
for (const d of DIVS) {
  const cp = probe(d);
  const win = cpDelta(cp, "win", true);
  const best = Math.max(...SIZES.map(n => ffaDelta(cp, 1, n, true)));
  check(best < win, `${d.name}: best free-for-all payout ${best} < 1v1 win ${win}`);
}

console.log("\n── placement is monotonic and bounded ──");
for (const d of DIVS) {
  const cp = probe(d);
  for (const n of SIZES) {
    let ok = true;
    for (let p = 2; p <= n; p++) if (ffaDelta(cp, p, n, true) > ffaDelta(cp, p - 1, n, true)) ok = false;
    check(ok, `${d.name} @ ${n} players: finishing worse never pays more`);
    eq(ffaDelta(cp, 1, n, true), d.ffaTop, `${d.name} @ ${n} players: 1st is exactly ffaTop`);
    eq(ffaDelta(cp, n, n, true), d.ffaBottom, `${d.name} @ ${n} players: last is exactly ffaBottom`);
  }
}

console.log("\n── nonsense in, zero out ──");
eq(ffaDelta(0, 1, 1, true), 0, "a one-player table has no placement to pay for");
eq(ffaDelta(0, 1, 0, true), 0, "a missing head count pays nothing");
eq(ffaDelta(0, 0, 4, true), 0, "a missing place pays nothing");
eq(ffaDelta(0, 9, 4, true), ffaDelta(0, 4, 4, true), "a place past the end of the table is last place");
eq(ffaDelta(0, -3, 4, true), 0, "a negative place is a caller bug, not a 1st place");

console.log("\n── a worked example, so the numbers are visible ──");
// 6-player table, one row per division: 1st / 3rd / last.
for (const d of DIVS) {
  const cp = probe(d);
  const row = [1, 3, 6].map(p => { const v = ffaDelta(cp, p, 6, true); return (v > 0 ? "+" : "") + v; });
  console.log(`     ${d.name.padEnd(30)} 1st ${row[0].padStart(4)}   3rd ${row[1].padStart(4)}   6th ${row[2].padStart(4)}`);
}

// ── The wiring: every link in the chain from the menu to the payout ──────────
// The CP maths above is worthless if the mode never reaches it. Each of these
// is one link, and each has exactly one line holding it together.
console.log("\n── the wiring, menu to payout ──");
{
  const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");
  check(/<option value="ranked">[^<]*Competitive<\/option>/.test(HTML),
        "the Mode dropdown offers Competitive");
  check(/<option value="competitive">[^<]*Competitive 1v1<\/option>/.test(HTML),
        "and names the old one Competitive 1v1, so the two are told apart");

  check(/ranked: _ncIsRanked/.test(APP),
        "creating a room sends the ranked flag to the server");
  check(/\(_ncIsCompetitive \|\| _ncIsRanked\) \? 0 :/.test(APP),
        "a ranked room is created with 0 bots");
  check(/if \(d && d\.room && typeof d\.room\.ranked === "boolean"\) rankedMode = d\.room\.ranked;/.test(APP),
        "rankedMode comes from the room's own flag on every state poll");
  check(/if \(rankedMode\) processRankedFfaGameEnd\(finalScores\);/.test(APP),
        "the end of a game runs the free-for-all payout");
  check(/const COMP_FFA_MIN_PLAYERS = 3;/.test(APP),
        "CP needs 3 people (COMP_FFA_MIN_PLAYERS)");
  check(/if \(humans\.length < COMP_FFA_MIN_PLAYERS\)/.test(APP),
        "and the payout actually checks it");

  // The two modes must not be confused for one another anywhere that matters.
  check(/if \(!rankedMode\) return;/.test(APP),
        "the free-for-all payout refuses to run outside a ranked room");
  check(/if \(!compMode\) return;/.test(APP),
        "the 1v1 payout still refuses to run outside a competitive room");
  // One rank, two ways to climb it: the free-for-all writes the same fields.
  const ffaStart = APP.indexOf("async function processRankedFfaGameEnd");
  const ffaBody = APP.slice(ffaStart, ffaStart + 9000);
  for (const field of ["stats.comp_cp", "stats.rank_competitive", "stats.competitive_wins",
                       "stats.competitive_losses", "stats.competitive_draws"]) {
    check(ffaBody.includes(`"${field}"`), `the free-for-all writes ${field} (one shared rank)`);
  }
  check(/_lastRankedFfaProcessed === gameKey/.test(APP),
        "the payout dedup is per GAME, so Play Again in the same room pays again");
}

// ── The season id has to be REACHABLE from the CP writers ────────────────────
// _compGetSeasonId is declared inside a late IIFE. Both end-of-game CP writers
// (the 1v1 one and the free-for-all one) live OUTSIDE that IIFE, so a bare
// `typeof _compGetSeasonId === "function"` out there is always false and the
// writer silently stamps its hardcoded fallback season onto comp_season_id.
// That fights the Competitive tab, which uses the real function and resets the
// season when the two disagree. It must be read off window.
console.log("\n── the season id is reachable from the CP writers ──");
{
  const exported = /window\._compGetSeasonId\s*=\s*_compGetSeasonId\s*;/.test(APP);
  check(exported, "_compGetSeasonId is exported to window");

  // Every guard for it, anywhere in the file, must go through window. A bare
  // one is either a scope bug or a line that will become one when it moves.
  const bare = [];
  APP.split("\n").forEach((line, i) => {
    if (/typeof\s+_compGetSeasonId\s*===/.test(line)) bare.push(i + 1);
  });
  check(bare.length === 0,
        `no bare 'typeof _compGetSeasonId' guards${bare.length ? " (lines " + bare.join(", ") + ")" : ""}`);

  // And the hardcoded fallback must never be the value that actually gets used:
  // it is there for a missing export, not as a season.
  const fallbacks = (APP.match(/\(\) => "2026-Q2"/g) || []).length;
  check(fallbacks <= 2, `the hardcoded season fallback stays a fallback (${fallbacks} left)`);
}

console.log(`\n${failures ? "✗ FAILED" : "✓ PASSED"}  ${checks - failures}/${checks} checks\n`);
process.exit(failures ? 1 : 0);
