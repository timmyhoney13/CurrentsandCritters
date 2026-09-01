#!/usr/bin/env node
/* Competitive (free-for-all): CP by finishing place.
 *
 * Run:  node test_competitive_ffa_cp.js
 *
 * The second competitive mode is an ordinary 3-8 player game, people only, that
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

// ── Three people is the FLOOR, not just the payout's cutoff ─────────────────
// A two-person table in this mode is Competitive 1v1 with extra steps, and the
// easiest thing in the game to farm with a friend. So the size is refused at
// every place a host can set it: the New Current modal, the lobby's Table Setup
// and, because a rule enforced only in the client is not a rule, both of the
// server doors those two talk to.
console.log("\n── three people is the floor ──");
{
  const SERVER = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");

  // One number, two languages. If they drift, the lobby offers the host a table
  // the server then rejects.
  const clientMin = (APP.match(/const COMP_FFA_MIN_PLAYERS = (\d+);/) || [])[1];
  const serverMin = (SERVER.match(/^COMP_FFA_MIN_PLAYERS = (\d+)$/m) || [])[1];
  eq(clientMin, "3", "the client floor is 3");
  eq(serverMin, clientMin, "the server mirrors the same number");

  // Door 1: creating the room.
  check(/_ncIsRanked && \(human < COMP_FFA_MIN_PLAYERS \|\| human > 8\)/.test(APP),
        "the New Current modal refuses a competitive table under the floor");
  check(/_humans < COMP_FFA_MIN_PLAYERS \|\| _humans > 8/.test(APP),
        "and opens the field at a legal size when the mode is chosen");
  check(/if \(totalEl\) totalEl\.min = String\(COMP_FFA_MIN_PLAYERS\);/.test(APP),
        "the humans field's own min moves with the mode");
  check(/if \(totalEl\) totalEl\.min = "1";/.test(APP),
        "and drops back for every other mode (switching away must not keep it)");
  check(/if total_players < COMP_FFA_MIN_PLAYERS:/.test(SERVER),
        "the server's create handler refuses it too");

  // Door 2: resizing the lobby. The seat spots stop at the floor, the request
  // they would have sent is refused anyway, and so is the same request typed
  // by hand. (This used to be a Table Setup panel with +/- steppers. The eight
  // spots are the control now, so the floor is enforced where they ask.)
  check(/ctx\.humans > Math\.max\(1, ctx\.filled\)/.test(APP),
        "a spot somebody is sitting in is never taken off the table");
  check(/!ctx\.room\.ranked/.test(APP),
        "and a competitive room is offered no bot at all");
  check(/const minHumans = latestPayload\?\.room\?\.ranked \? COMP_FFA_MIN_PLAYERS : 1;/.test(APP),
        "and the resize request itself is held to it");
  check(/if self\.ranked and want_humans < COMP_FFA_MIN_PLAYERS:/.test(SERVER),
        "configure_lobby_seats refuses to shrink a competitive lobby under it");

  // Door 3: the rematch. A table only loses people here, so this is the one
  // place the size falls without anybody resizing it.
  check(/if self\._ranked_shortfall_locked\(len\(active\)\):/.test(SERVER),
        "Play Again refuses to start a competitive rematch under the floor");
  check(/"needs_players": self\._ranked_shortfall_locked\(/.test(SERVER),
        "and the state payload says how many more people it wants");
  check(/const needs = Number\(pa\.needs_players \|\| 0\);/.test(APP),
        "the end screen reads it");
  check(/Waiting for \$\{needs\} more player/.test(APP),
        "and says what the room is waiting for instead of a stuck ready tally");

  // Door 4: the start button, for a room that predates the rule.
  check(/short = self\._ranked_shortfall_locked\(\n\s+sum\(1 for seat in self\.seats if seat\.kind == "human"\)\)/.test(SERVER),
        "start_game refuses to launch a competitive game under the floor");

  // The floor is the ROOM's rule now, but the payout keeps its own check: rooms
  // made before this rule existed are still out there.
  check(/if \(humans\.length < COMP_FFA_MIN_PLAYERS\)/.test(APP),
        "and the payout still checks it last");
}

// ── A finished free-for-all has to SHOW UP in competitive history ───────────
// The rank moved, so the game has to be visible behind it. Both competitive
// modes write into the one competitive ledger now, and the two records are
// different shapes: 1v1 has p1_name/p2_name, the free-for-all has a players
// list of 3 to 8 people. _compHistoryEntry is the one reader that knows both,
// and it is RUN here, not just grepped for.
console.log("\n── a free-for-all shows up in competitive history ──");
{
  const SERVER = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");

  // The server half: the game is written into the competitive ledger, marked
  // ranked at write time (the ROOM was competitive, so the game was), and each
  // player's own client stamps their own CP onto their own row.
  check(/elif self\.ranked:\n\s+self\._save_ranked_game\(gs, ms, standings\)/.test(SERVER),
        "a finished ranked game is saved into the competitive ledger");
  check(/"mode": "ranked",\n\s+"ranked": True,/.test(SERVER),
        "and is marked ranked at write time, with no client confirmation");
  check(/def _stamp_ranked_ffa_result/.test(SERVER),
        "each player can stamp their own CP onto their own row");
  check(/if isinstance\(record\.get\("players"\), list\):/.test(SERVER),
        "ranked_result tells the two record shapes apart");
  check(/name:\s+myNick,/.test(APP) && /apiPost\("\/api\/competitive\/ranked_result"/.test(APP),
        "the free-for-all payout reports the result to the server");

  // The client half, executed: one reader, both shapes.
  const ENTRY = grabFn("_compHistoryEntry");
  const ORD   = grabFn("_ordinal", "  ");
  const box = { console };
  vm.createContext(box);
  vm.runInContext(`${ORD}\n${ENTRY}\nthis.entry = _compHistoryEntry;`, box);
  const entry = box.entry;

  const ffa = {
    recorded_unix: 100, ranked: true, players: [
      { name: "Ann", score: 90, place: 1, cp_delta: 12, rank_after: "Golden Grouper I" },
      { name: "Tim", score: 60, place: 2 },
      { name: "Bo",  score: 40, place: 3 },
      { name: "Cid", score: 10, place: 4 },
    ],
  };
  eq(entry(ffa, "Tim").label, "2nd of 4", "a free-for-all row reads as a placement, not an opponent");
  eq(entry(ffa, "Tim").result, "draw", "the middle of the table is a draw");
  eq(entry(ffa, "Ann").result, "win",  "first is a win");
  eq(entry(ffa, "Cid").result, "loss", "last is a loss");
  eq(entry(ffa, "Ann").cpDelta, 12,    "the CP the game paid rides on the row");
  eq(entry(ffa, "Ann").rankAfter, "Golden Grouper I", "so does the rank it left you at");
  eq(entry(ffa, "Tim").myScore, 60,    "the score shown is mine");
  eq(entry(ffa, "Stranger").mine, false, "somebody else's game is not mine");
  eq(entry(ffa, "").mine, false,       "and neither is anyone's, if I have no name yet");

  const solo = { recorded_unix: 1, players: [{ name: "Ann", score: 5, place: 1 }] };
  eq(entry(solo, "Ann").result, "draw", "a one-person table is neither a win nor a loss");

  // The 1v1 shape still reads exactly as it did.
  const duel = {
    recorded_unix: 200, ranked: true, is_draw: false, winner: "Tim",
    p1_name: "Tim", p2_name: "Bo", p1_best_score: 88, p2_best_score: 70,
    p1_cp_delta: 25, p1_rank_after: "Diamond Dolphin III",
  };
  eq(entry(duel, "Tim").label, "vs Bo", "a 1v1 row still names the opponent");
  eq(entry(duel, "Tim").result, "win",  "and still reads the winner");
  eq(entry(duel, "Bo").result, "loss",  "from both sides");
  eq(entry(duel, "Bo").oppScore, 88,    "with the opponent's score");
  eq(entry(duel, "Nobody").mine, false, "and still filters to my own games");

  // Both history lists must go through it, or one of them drops a whole mode.
  check(/data\.games\.map\(g => \[g, _compHistoryEntry\(g, myName\)\]\)/.test(APP),
        "the History tab's competitive list reads both shapes");
  check(/games\.map\(g => \[g, _compHistoryEntry\(g, myName\)\]\)/.test(APP),
        "so does the Competitive tab");

  // And the Quick Stats counter, which only ever counted the 1v1 ladder.
  check(/name === "competitive" \|\| name === "ranked"/.test(APP),
        "a saved game in either mode counts as competitive");
  check(/if \(compRecordGames > compGames\) compGames = compRecordGames;/.test(APP),
        "Competitive Games never shows fewer games than the rank's own record");
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
