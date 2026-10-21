#!/usr/bin/env node
/* Whose score did you just save?
 *
 * saveGameStats has to find the player's own row in final_scores before it can
 * write their score, placement and XP. It resolved that row by NAME through a
 * five-step fallback chain, and nothing makes a name unique inside a room: a
 * guest who types nothing is called "Player", so two of them at one table are
 * both "Player". Every step of the chain then returns the SAME row to both of
 * them. One player saves the other's score and neither is told.
 *
 * The server has always published the answer: every final_scores row carries
 * the seat_index it belongs to ("so results stay mappable without trusting the
 * name", _publish_forfeit_result), and viewer.seat_index is the same number,
 * which the client already keeps as myIdx. The chain just never asked.
 *
 * This runs the REAL resolution chain, sliced out of preview-app.js, against a
 * table where two seats share one name.
 *
 * Run:  node test_endgame_my_entry.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const APP = fs.readFileSync(
  path.join(__dirname, "multiplayer/client/js/preview-app.js"), "utf8");

let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name); }
}

// ── The chain itself, out of the shipping source ───────────────────────────
// Sliced rather than retyped: a copy of the logic in here would keep passing
// after the real one changed, which is the one thing this test exists to stop.
const START = "    let myEntry =";
const END   = "    if (!myEntry) {";
const iStart = APP.indexOf(START);
const iEnd   = APP.indexOf(END, iStart);
if (iStart < 0 || iEnd < 0) {
  console.log("could not find the myEntry resolution chain in preview-app.js");
  process.exit(1);
}
const CHAIN = APP.slice(iStart, iEnd);

/** Run the real chain with one table's worth of state. */
function resolve({ finalScores, myNick, myIdx, compMode, players }) {
  const _fs = Array.isArray(finalScores) ? finalScores : [];
  const nickLower = (myNick || "").trim().toLowerCase();
  const _latestPlayers = players || [];
  const latestPayload = { viewer: {} };
  // Bots are named for people now, so the chain asks the seat snapshot.
  const _isBotEntry = (p) => {
    const lp = _latestPlayers.find(q => String(q.name || "") === String(p.name || ""));
    return !!(lp && lp.kind === "ai");
  };
  const body = CHAIN + "\n; return myEntry;";
  // eslint-disable-next-line no-new-func
  const run = new Function(
    "_fs", "myNick", "nickLower", "myIdx", "compMode",
    "_latestPlayers", "latestPayload", "_isBotEntry", body);
  return run(_fs, myNick, nickLower, myIdx, compMode,
             _latestPlayers, latestPayload, _isBotEntry);
}

// ── Two guests called "Player" at the same table ───────────────────────────
console.log("\ntwo seats, one name: each player gets their OWN row");

// Sorted by score, the way the server publishes final_scores.
const twoGuests = [
  { name: "Player", score: 31, seat_index: 2 },
  { name: "Reef",   score: 24, seat_index: 1 },
  { name: "Player", score: 9,  seat_index: 0 },
];
const guestSeats = [
  { index: 0, name: "Player", kind: "human" },
  { index: 1, name: "Reef",   kind: "human" },
  { index: 2, name: "Player", kind: "human" },
];

const seat0 = resolve({ finalScores: twoGuests, myNick: "Player", myIdx: 0,
                        compMode: false, players: guestSeats });
const seat2 = resolve({ finalScores: twoGuests, myNick: "Player", myIdx: 2,
                        compMode: false, players: guestSeats });

check("the guest in seat 0 saves seat 0's score (9), not the other Player's",
      !!seat0 && seat0.seat_index === 0 && seat0.score === 9);
check("the guest in seat 2 saves seat 2's score (31)",
      !!seat2 && seat2.seat_index === 2 && seat2.score === 31);
check("the two of them do not save the same row",
      !!seat0 && !!seat2 && seat0 !== seat2);

// ── The ordinary table still resolves exactly as it did ────────────────────
console.log("\nunique names are unaffected");

const normal = [
  { name: "Otter", score: 40, seat_index: 1 },
  { name: "Reef",  score: 12, seat_index: 0 },
];
const normalSeats = [
  { index: 0, name: "Reef",  kind: "human" },
  { index: 1, name: "Otter", kind: "human" },
];
const otter = resolve({ finalScores: normal, myNick: "Otter", myIdx: 1,
                        compMode: false, players: normalSeats });
check("a unique nickname finds its own row", !!otter && otter.score === 40);

// A payload with no seat_index at all (an older room checkpoint, a forfeit
// tally built before the field existed) must still resolve by name.
const noSeats = [{ name: "Otter", score: 40 }, { name: "Reef", score: 12 }];
const byName = resolve({ finalScores: noSeats, myNick: "Otter", myIdx: 1,
                         compMode: false, players: normalSeats });
check("no seat_index in the payload falls back to the name", !!byName && byName.score === 40);

// Case drift between the account nickname and the seat name.
const drift = resolve({ finalScores: normal, myNick: "otter", myIdx: null,
                        compMode: false, players: normalSeats });
check("case-insensitive nickname still works", !!drift && drift.score === 40);

// The last-resort rule: one human among bots is me, whatever I am called.
const vsBots = [
  { name: "Mako",  score: 50, seat_index: 1 },
  { name: "Otter", score: 20, seat_index: 0 },
];
const botSeats = [
  { index: 0, name: "Otter", kind: "human" },
  { index: 1, name: "Mako",  kind: "ai" },
];
const lone = resolve({ finalScores: vsBots, myNick: "Renamed", myIdx: null,
                       compMode: false, players: botSeats });
check("the only human in the scores is still me when nothing else matches",
      !!lone && lone.name === "Otter");

// ── Competitive keeps the name path ────────────────────────────────────────
// One person owns TWO seats there ({0,1} or {2,3}) and so has two rows of
// their own; the hands are named apart ("Otter", "Otter 2") and the nickname
// already picks the right one. The seat step is skipped so that mode is
// untouched.
console.log("\ncompetitive 1v1 is left exactly as it was");

const comp = [
  { name: "Otter 2", score: 30, seat_index: 1 },
  { name: "Otter",   score: 25, seat_index: 0 },
  { name: "Mako",    score: 10, seat_index: 2 },
];
const compSeats = [
  { index: 0, name: "Otter",   kind: "human" },
  { index: 1, name: "Otter 2", kind: "human" },
  { index: 2, name: "Mako",    kind: "human" },
];
const compMe = resolve({ finalScores: comp, myNick: "Otter", myIdx: 1,
                         compMode: true, players: compSeats });
check("competitive resolves by nickname, not by the viewer's current hand",
      !!compMe && compMe.name === "Otter");

// ── The chain asks the seat first ──────────────────────────────────────────
console.log("\nthe seat is asked before any name");

check("the chain reads seat_index off the final_scores rows",
      /p\.seat_index/.test(CHAIN));
check("it compares against myIdx (viewer.seat_index)",
      /Number\(p\.seat_index\) === Number\(myIdx\)/.test(CHAIN));
check("the seat step comes before the exact-nickname step",
      CHAIN.indexOf("p.seat_index") >= 0
      && CHAIN.indexOf("p.name === myNick") >= 0
      && CHAIN.indexOf("p.seat_index") < CHAIN.indexOf("p.name === myNick"));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
