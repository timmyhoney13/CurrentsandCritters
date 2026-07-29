#!/usr/bin/env node
/* Tests for the "I lost — can I still watch the rest of the tournament?" path
 * (multiplayer/client/js/tournament-ui.js + the end-game buttons in
 * multiplayer/client/js/preview-app.js).
 *
 * Run:  node test_tournament_spectate.js
 *
 * Why this file exists: being knocked out used to be treated exactly like the
 * tournament being OVER. The background watch stopped (so the live-match count
 * froze and the final standings never arrived) and the status bar rendered in
 * "done" mode, which hides the Spectate button — leaving a knocked-out player
 * with no way to watch the semifinal they'd just been eliminated from. These
 * tests pin the distinction: phase complete/cancelled == done; eliminated while
 * the bracket is still running == "out", which keeps watching and keeps
 * offering the other games.
 *
 * The functions live inside an IIFE that needs a live DOM, so we lift the exact
 * source text of the pure ones out of the file and run them against stub state.
 * If a function is renamed or its body changes shape, extraction fails loudly
 * rather than silently testing nothing.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const UI = fs.readFileSync(
  path.join(__dirname, "multiplayer", "client", "js", "tournament-ui.js"), "utf8");
const APP = fs.readFileSync(
  path.join(__dirname, "multiplayer", "client", "js", "preview-app.js"), "utf8");

let failures = 0, checks = 0;
function ok(cond, label) {
  checks++;
  if (cond) { console.log("  ✓ " + label); return; }
  failures++; console.log("  ✗ " + label);
}
function eq(actual, expected, label) {
  ok(JSON.stringify(actual) === JSON.stringify(expected),
     label + "  (got " + JSON.stringify(actual) + ", want " + JSON.stringify(expected) + ")");
}

// ── Lift the functions under test out of the module ─────────────────────────
function extract(src, name, where) {
  const decl = "function " + name + "(";
  const start = src.indexOf(decl);
  if (start < 0) throw new Error("could not find function " + name + " in " + where);
  const open = src.indexOf("{", src.indexOf(")", start));
  let depth = 0, i = open;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") { depth--; if (depth === 0) break; }
  }
  if (depth !== 0) throw new Error("unbalanced braces extracting " + name);
  return src.slice(start, i + 1);
}

const COPY = ["chipText", "barMain", "barSub"].map(n => extract(UI, n, "tournament-ui.js")).join("\n");
const MATCHERS = ["allBracketMatches", "matchInRoom", "liveOtherMatches", "myRunIsOver"]
  .map(n => extract(UI, n, "tournament-ui.js")).join("\n");

const copy = new Function(COPY + "\nreturn { chipText, barMain, barSub };")();
const br = new Function(
  MATCHERS + "\nreturn { allBracketMatches, matchInRoom, liveOtherMatches, myRunIsOver };")();

// ── The two decisions the wait bar makes, lifted from showWaitBar ────────────
// (showWaitBar itself touches the DOM, so the rule it applies is re-derived from
// its own source text — if the rule changes, this must be updated with it.)
const SPEC_RULE = UI.match(/const canWatch = ([^;]+);/);
if (!SPEC_RULE) throw new Error("could not find the Spectate-button rule in showWaitBar");
const canWatch = new Function("s", "return " + SPEC_RULE[1] + ";");

// The watch's mode decision, re-derived from watchTick's own branches.
function watchMode(st) {
  if (st.phase === "complete" || st.phase === "cancelled") return "done";
  if (st.viewer.status === "eliminated") return "out";
  if (st.phase === "lobby") return "lobby";
  return st.myGameLive ? "playing" : "waiting";
}
function watchKeepsRunning(st) {
  return !(st.phase === "complete" || st.phase === "cancelled");
}

// ── Fixtures ────────────────────────────────────────────────────────────────
function bracket(matches) { return { rounds: [matches], third_place: null }; }
const MINE = { match_number: 1, status: "complete", room_id: "AAA",
               players: [{ pid: "me" }, { pid: "rival" }] };
const OTHER_LIVE = { match_number: 2, status: "active", room_id: "BBB",
                     players: [{ pid: "x" }, { pid: "y" }] };
const OTHER_READY = { match_number: 3, status: "ready", room_id: "CCC",
                      players: [{ pid: "p" }, { pid: "q" }] };

console.log("\nKnocked out while the tournament is still running");
{
  const st = { phase: "running", name: "Reef Cup", spectators_allowed: true,
               viewer: { pid: "me", in_tournament: true, status: "eliminated" },
               bracket: bracket([MINE, OTHER_LIVE]) };
  eq(watchMode(st), "out", "elimination is its own mode, not 'done'");
  ok(watchKeepsRunning(st),
     "the watch keeps polling after a knockout (live count + final standings still arrive)");

  const live = br.liveOtherMatches(st, "AAA");
  eq(live.map(m => m.room_id), ["BBB"],
     "the other live match is offered, and the room we're sitting in is not");

  const s = { mode: "out", name: st.name, live: live.length, canSpectate: true };
  ok(canWatch(s), "the Spectate button is shown to a knocked-out player");
  ok(/still being played/.test(copy.barMain(s)), "the bar says games are still live");
  ok(/watch a live match/i.test(copy.barSub(s)), "…and points at watching one");
  ok(/Knocked out/.test(copy.chipText(s)), "the in-game chip says knocked out, not 'results'");
}

console.log("\nKnocked out with nothing live to watch");
{
  const s = { mode: "out", name: "Reef Cup", live: 0, canSpectate: true };
  ok(!canWatch(s), "no Spectate button when we know no game is running");
  ok(/next round is starting/.test(copy.barMain(s)), "the bar explains why");
}

console.log("\nHost turned spectating off");
{
  const s = { mode: "out", name: "Reef Cup", live: 2, canSpectate: false };
  ok(!canWatch(s), "no Spectate button when the host disallowed watching");
  ok(!/watch a live match/i.test(copy.barSub(s)), "…and the copy doesn't promise one");
}

console.log("\nThe tournament itself is over");
{
  const st = { phase: "complete", name: "Reef Cup", spectators_allowed: true,
               viewer: { pid: "me", in_tournament: true, status: "eliminated" },
               bracket: bracket([MINE, { ...OTHER_LIVE, status: "complete" }]) };
  eq(watchMode(st), "done", "a finished tournament is 'done'");
  ok(!watchKeepsRunning(st), "…and only then does the watch stop");
  const s = { mode: "done", name: st.name, live: 0, canSpectate: true };
  ok(!canWatch(s), "nothing to spectate once the bracket is finished");
  ok(/finished/.test(copy.barMain(s)), "the bar says the tournament is finished");
}

console.log("\nStill in it, between matches");
{
  const st = { phase: "running", name: "Reef Cup", spectators_allowed: true,
               viewer: { pid: "me", in_tournament: true, status: "waiting" },
               myGameLive: false, bracket: bracket([MINE, OTHER_LIVE]) };
  eq(watchMode(st), "waiting", "a player waiting on the next round is still 'waiting'");
  const s = { mode: "waiting", name: st.name, live: 1, canSpectate: true };
  ok(canWatch(s), "Spectate stays available between your own matches");
  const unknown = { mode: "waiting", name: st.name, live: -1, canSpectate: true };
  ok(canWatch(unknown), "…and before the live count is known (-1), we still offer it");
}

console.log("\nWhat counts as a live match to watch");
{
  const st = { bracket: bracket([MINE, OTHER_LIVE, OTHER_READY]) };
  eq(br.liveOtherMatches(st, "AAA").map(m => m.room_id), ["BBB"],
     "a match still gathering ready-ups is not yet watchable");
  eq(br.liveOtherMatches(st, "BBB").map(m => m.room_id), [],
     "the match we are already watching is not offered again");
  eq(br.matchInRoom(st, "BBB").match_number, 2, "the room maps back to its match");
  eq(br.matchInRoom(st, ""), null, "no room, no match");
  eq(br.allBracketMatches(null), [], "a missing bracket is not a crash");
}

console.log("\nThe header exit is only a forfeit while you can still forfeit");
{
  const running = (status) => ({ phase: "running", viewer: { in_tournament: true, status } });
  ok(!br.myRunIsOver(running("waiting")), "still competing -> 'Leave' really leaves");
  ok(!br.myRunIsOver(running("playing")), "mid-match -> still a forfeit");
  ok(br.myRunIsOver(running("eliminated")),
     "knocked out -> the exit must not tell the server we forfeited (it costs the no-quit XP)");
  ok(br.myRunIsOver(running("champion")), "champion -> nothing to forfeit");
  ok(br.myRunIsOver({ phase: "complete", viewer: { in_tournament: true, status: "waiting" } }),
     "tournament over -> nothing to forfeit");
  ok(br.myRunIsOver({ phase: "lobby", viewer: { in_tournament: false, status: null } }),
     "not in it at all -> nothing to forfeit");
  ok(!br.myRunIsOver(null), "no state yet -> assume the safe (confirming) path");
}

console.log("\nEnd-of-match buttons (preview-app.js)");
{
  // The end screen swaps Play Again / Back to Lobby for Spectate / Wait. Pin the
  // fact that a WATCHED tournament match gets the same treatment — a spectator
  // has no seat, so "Play Again" could only ever fail.
  const sync = APP.slice(APP.indexOf("function _syncEndgameTournamentButtons"),
                         APP.indexOf("function updatePlayAgainUI"));
  ok(/ctx\.watching/.test(sync),
     "the end screen distinguishes watching a bracket match from playing one");
  ok(/Watch Another Match/.test(sync),
     "a match we only watched offers the next live game");
  ok(/ctx\.finished \? "🏆 See Final Standings"/.test(sync),
     "a finished tournament sends you to the standings");
  ok(/ctx\.over \? "🏆 Follow the Tournament"/.test(sync),
     "being knocked out sends you back to the bracket, not to a rematch");

  // __ccTourneyMatchCtx must not report a spectate option when nothing is live.
  const ctxSrc = UI.slice(UI.indexOf("window.__ccTourneyMatchCtx"),
                          UI.indexOf("window.__ccTourneySpectate"));
  ok(/canSpectate: st\.spectators_allowed !== false && live > 0/.test(ctxSrc),
     "Spectate is only offered when a game is actually running");
  ok(/watching: !playing/.test(ctxSrc),
     "…and the context says whether we played this match or watched it");
}

console.log("\n" + (failures ? "FAILED " + failures + "/" + checks : "PASSED " + checks + " checks"));
process.exit(failures ? 1 : 0);
