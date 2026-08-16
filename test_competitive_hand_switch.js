#!/usr/bin/env node
/* Competitive mode: the turn must actually SWITCH to your other hand.
 *
 * Run:  node test_competitive_hand_switch.js
 *
 * Competitive is a 4-seat game where each human owns TWO seats (P1 = {0,1},
 * P2 = {2,3}) and the engine interleaves them 0 → 2 → 1 → 3. One person is
 * therefore playing two hands, and the client has to show whichever of them the
 * turn is on.
 *
 * The server decides that now (state_view returns the ACTIVE hand's view for
 * either of a player's tokens — see _competitive_same_owner), because the
 * client-side version of it was a race: a poll in flight, a corrective fetch
 * that never went out, or a client that came back from a refresh holding only
 * one of its two tokens, and the match froze on hand 1 while the banner said
 * hand 2 was up. What is left for the client is to not throw the switched view
 * away, and these checks pin that down against the REAL buildStateUrl() /
 * applyServerPayload() / compAdoptFromPayload() lifted out of preview-app.js:
 *
 *   • one state_version can arrive as two different views (they differ by
 *     viewer seat), so the render gate must be keyed on version + viewer seat;
 *   • re-entering a competitive room by any generic path (?room= URL, the
 *     "Rejoin →" card, a plain refresh) must rebuild competitive mode from the
 *     payload instead of playing on as a one-seat player;
 *   • a click must never act for a hand that is not on screen.
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

// ── Lift the real functions out of preview-app.js ────────────────────────────
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

// ── Fake server ──────────────────────────────────────────────────────────────
// Mirrors multiplayer_server.GameRoom for a competitive room: 4 human seats,
// turn order [0,2,1,3], state_version bumped once per turn boundary, and a
// state_view that returns the ACTIVE hand's view to either token of the player
// who owns it (verified against multiplayer_server.py's state_view).
const TURN_ORDER = [0, 2, 1, 3];
const TOKENS = { 0: "tok0", 1: "tok1", 2: "tok2", 3: "tok3" };
const SEAT_BY_TOKEN = { tok0: 0, tok1: 1, tok2: 2, tok3: 3 };
const sameOwner = (a, b) => Math.floor(a / 2) === Math.floor(b / 2);

function makeServer() {
  return {
    turn: 0,
    version: 10,
    get activeSeat() { return TURN_ORDER[this.turn % 4]; },
    endTurn() { this.turn++; this.version += 20; },
    stateFor(token) {
      const tokenSeat = SEAT_BY_TOKEN[token];
      if (tokenSeat === undefined) return { ok: false, error: "seat token invalid" };
      const active = this.activeSeat;
      // The one rule this whole file is about, in its two halves (mirrors
      // multiplayer_server._competitive_view_seat_locked):
      //  • the turn is on one of my hands → my poll shows me THAT hand,
      //    whichever of my two tokens I polled with;
      //  • the opponent is playing → my poll shows me the hand of mine that is
      //    up NEXT, so the switch happens the moment my own turn ends and never
      //    waits on the opponent's clock.
      let viewerSeat = tokenSeat;
      if (sameOwner(tokenSeat, active)) {
        viewerSeat = active;
      } else {
        const g = TURN_ORDER.indexOf(active);
        for (let step = 1; step <= TURN_ORDER.length; step++) {
          const nxt = TURN_ORDER[(g + step) % TURN_ORDER.length];
          if (sameOwner(nxt, tokenSeat)) { viewerSeat = nxt; break; }
        }
      }
      return {
        ok: true,
        version: this.version,
        active_action_seat: active,
        room: { competitive: true, phase: "running" },
        seats: [
          { index: 0, claimed_name: "Otter" },   { index: 1, claimed_name: "Otter 2" },
          { index: 2, claimed_name: "Heron" },   { index: 3, claimed_name: "Heron 2" },
        ],
        viewer: { seat_index: viewerSeat, can_act: viewerSeat === active },
        legal_actions: { actions: viewerSeat === active ? [{ kind: "end_turn" }] : [] },
        state: {
          players: [0, 1, 2, 3].map(i => ({
            index: i, name: "P" + i, score: 0,
            hand: i === viewerSeat ? [{ uid: 100 + i }] : [],
          })),
        },
      };
    },
  };
}

// ── Client harness ───────────────────────────────────────────────────────────
// Drives the real buildStateUrl/applyServerPayload/compAdoptFromPayload the way
// the 1 s poll timer does, with a deterministic setTimeout queue.
function makeClient(server, mySeats, opts) {
  const options = opts || {};
  const rendered = [];   // every payload that reached renderPayload()
  const timers = [];
  const tokens = {};
  for (const s of mySeats) tokens[s] = TOKENS[s];

  const sandbox = {
    console,
    URLSearchParams,
    roomId: "COMPTS",
    compMode: options.compMode !== false,
    compMySeats: mySeats.slice(),
    compTokens: tokens,
    compHostToken: "",
    compHandNames: {},
    compP1Name: "Player 1",
    compP2Name: "Player 2",
    _compHsSuppressed: false,
    _compPrevActiveSeat: 7,
    sseSource: options.sseSource || null,
    latestPayload: null,
    _lastStateVersion: -1,
    _lastRenderedKey: "",
    _compRefetchKey: "",
    isSpectating: () => false,
    compLoadHandNames: () => {},
    getSeatToken: () => options.heldToken || TOKENS[mySeats[0]] || "",
    getHostToken: () => "",
    apiUrl: (p) => p,
    renderPayload: (d) => { rendered.push(d); },
    setTimeout: (fn) => { timers.push(fn); },
    refreshStateAfterAction: () => { sandbox.refreshState(); },
    refreshState: () => {
      const url = sandbox.buildStateUrl();
      const token = new URL("http://x" + url).searchParams.get("seat_token");
      sandbox.applyServerPayload(server.stateFor(token));
    },
  };
  vm.createContext(sandbox);
  vm.runInContext([
    grabFn("buildStateUrl"),
    grabFn("applyServerPayload"),
    grabFn("compAdoptFromPayload"),
    grabFn("_compHandoffPending"),
    grabFn("_viewerCanActNow"),
    grabFn("_isMyTurnForAction"),
  ].join("\n"), sandbox);

  return {
    sandbox,
    rendered,
    // One tick of the 1 s poll timer, then drain whatever the payload scheduled.
    poll() {
      sandbox.refreshState();
      let guard = 0;
      while (timers.length) {
        if (++guard > 20) throw new Error("re-fetch loop did not settle (runaway setTimeout)");
        timers.shift()();
      }
    },
  };
}

// The view the client is actually showing = the last payload it rendered.
function shownSeat(client) {
  const last = client.rendered[client.rendered.length - 1];
  return last ? last.viewer.seat_index : null;
}
function shownCanAct(client) {
  const last = client.rendered[client.rendered.length - 1];
  return last ? last.viewer.can_act : null;
}
function shownHandOf(client, seat) {
  const last = client.rendered[client.rendered.length - 1];
  return last.state.players.find(p => p.index === seat).hand.length;
}

// ── 1. P1 (seats 0+1) plays a full round ─────────────────────────────────────
console.log("1. P1 owns seats 0+1 — the view follows whichever hand is active");
{
  const server = makeServer();
  const p1 = makeClient(server, [0, 1]);

  // Turn 1 — seat 0 (my hand 1).
  p1.poll();
  check(shownSeat(p1) === 0 && shownCanAct(p1) === true,
        "hand 1's turn: client shows seat 0 with YOUR TURN");

  // Hand 1 ends its turn → seat 2 (the opponent).
  server.endTurn();
  p1.poll();
  check(shownCanAct(p1) === false, "opponent's turn: client shows waiting");
  check(shownSeat(p1) === 1,
        "opponent's turn: the board has ALREADY moved to my hand 2 (the hand I play next)");
  check(shownHandOf(p1, 1) > 0 && shownHandOf(p1, 0) === 0,
        "opponent's turn: it is hand 2's real cards on screen, not hand 1's leftovers");

  // Opponent ends → seat 1 = MY OTHER HAND. This is the switch that broke.
  server.endTurn();
  p1.poll();
  check(shownSeat(p1) === 1,
        "hand 2's turn: client switched its view to seat 1 (not stuck on hand 1)");
  check(shownCanAct(p1) === true,
        "hand 2's turn: the rendered view says YOUR TURN");
  check((p1.rendered[p1.rendered.length - 1].legal_actions.actions || []).length > 0,
        "hand 2's turn: the rendered view carries hand 2's legal actions");
  check(shownHandOf(p1, 1) > 0,
        "hand 2's turn: the rendered view carries hand 2's cards");
  check(shownHandOf(p1, 0) === 0,
        "hand 2's turn: hand 1's cards are gone from the view (one hand at a time)");

  // Nothing changes server-side → no repeated re-fetch storm.
  const before = p1.rendered.length;
  p1.poll(); p1.poll();
  check(p1.rendered.length === before,
        "idle polls at the same version+seat do not re-render");

  // Hand 2 ends → seat 3 (opponent hand 2), then back around to seat 0.
  server.endTurn();
  p1.poll();
  check(shownCanAct(p1) === false, "opponent hand 2's turn: waiting again");
  check(shownSeat(p1) === 0,
        "opponent hand 2's turn: the board is already back on my hand 1, which plays next");
  server.endTurn();
  p1.poll();
  check(shownSeat(p1) === 0 && shownCanAct(p1) === true,
        "back to hand 1: client switched its view back to seat 0");
}

// ── 2. P2 (seats 2+3) — the mirror case ──────────────────────────────────────
console.log("2. P2 owns seats 2+3 — same switch, opposite side of the table");
{
  const server = makeServer();
  const p2 = makeClient(server, [2, 3]);

  p2.poll();                                     // seat 0 active (opponent)
  check(shownCanAct(p2) === false, "P1's hand 1 turn: P2 waits");
  check(shownSeat(p2) === 2, "P1's hand 1 turn: P2 is looking at the hand P2 plays next");
  server.endTurn(); p2.poll();                   // seat 2 = P2 hand 1
  check(shownSeat(p2) === 2 && shownCanAct(p2) === true, "P2 hand 1 gets its turn");
  server.endTurn(); p2.poll();                   // seat 1 (opponent)
  check(shownCanAct(p2) === false, "P1's hand 2 turn: P2 waits");
  check(shownSeat(p2) === 3, "P1's hand 2 turn: P2 is already on hand 2, which is up next");
  server.endTurn(); p2.poll();                   // seat 3 = P2 hand 2
  check(shownSeat(p2) === 3 && shownCanAct(p2) === true,
        "P2 hand 2 gets its turn (the second-hand switch, mirrored)");
}

// ── 3. Back from a refresh: ONE token, no competitive state ──────────────────
console.log("3. re-entering the match (refresh / Rejoin / ?room= URL) restores both hands");
{
  const server = makeServer();
  // What the generic room-entry paths leave behind: one seat token, compMode
  // off, no seat pair — and an SSE stream competitive must not run on.
  const fakeSse = { closed: false, close() { this.closed = true; } };
  const back = makeClient(server, [0], {
    compMode: false, heldToken: TOKENS[0], sseSource: fakeSse,
  });
  back.sandbox.compMySeats = [];
  back.sandbox.compTokens = {};

  back.poll();
  check(back.sandbox.compMode === true,
        "the payload says the room is competitive → competitive mode is rebuilt");
  check(JSON.stringify(back.sandbox.compMySeats) === "[0,1]",
        "the viewer's seat names the pair it belongs to");
  check(back.sandbox.compTokens[0] && back.sandbox.compTokens[1],
        "both hands are addressable with the one token that survived");
  check(back.sandbox.compP1Name === "Otter" && back.sandbox.compP2Name === "Heron",
        "both sides are named from the seats, not left as 'Player 1'");
  check(fakeSse.closed === true,
        "the SSE stream is closed (competitive is poll-only; it would fight the poll)");

  // …and the restored client plays both hands.
  server.endTurn(); back.poll();                 // opponent
  server.endTurn(); back.poll();                 // my hand 2
  check(shownSeat(back) === 1 && shownCanAct(back) === true,
        "the rejoined client is handed its second hand's turn");

  // A casual room must never be adopted as competitive.
  const casual = makeClient(server, [0], { compMode: false, heldToken: TOKENS[0] });
  casual.sandbox.compMySeats = [];
  casual.sandbox.compTokens = {};
  casual.sandbox.compAdoptFromPayload({
    room: { competitive: false }, viewer: { seat_index: 0 }, seats: [],
  });
  check(casual.sandbox.compMode === false, "a non-competitive room is left alone");
}

// ── 4. A click can never act for a hand that is not on screen ────────────────
console.log("4. the handoff window: it is my turn, but not the hand I am looking at");
{
  const server = makeServer();
  const p1 = makeClient(server, [0, 1]);
  // Hand 2 is active while the payload on screen is still hand 1's.
  p1.sandbox.latestPayload = {
    active_action_seat: 1,
    viewer: { seat_index: 0, can_act: false },
  };
  check(p1.sandbox._compHandoffPending() === true, "the handoff is detected");
  check(p1.sandbox._isMyTurnForAction() === false,
        "no action is accepted for the hand whose cards are not on screen");
  check(p1.sandbox._compRefetchKey === "",
        "the corrective fetch is re-armed instead of being skipped for this version");

  // Once the switched view has landed, the same hand plays normally.
  p1.sandbox.latestPayload = {
    active_action_seat: 1,
    viewer: { seat_index: 1, can_act: true },
  };
  check(p1.sandbox._compHandoffPending() === false && p1.sandbox._isMyTurnForAction() === true,
        "with hand 2 on screen, hand 2 can play");
}

// ── 5. Source invariants (so the gate cannot be quietly re-broken) ───────────
console.log("5. preview-app.js keeps the per-seat render gate");
{
  check(!/_lastRenderedVersion/.test(APP),
        "the version-only render gate (_lastRenderedVersion) is gone");
  check(/_lastRenderedKey/.test(APP),
        "renders are gated on a version+viewer-seat key (_lastRenderedKey)");
  const applySrc = grabFn("applyServerPayload");
  check(/viewer[\s\S]{0,40}seat_index/.test(applySrc),
        "applyServerPayload's render gate reads viewer.seat_index");
  check(/compAdoptFromPayload/.test(applySrc),
        "every payload gets the chance to restore competitive mode");
  // submitAction must send the token of the seat the server will act as.
  const submitSrc = APP.slice(APP.indexOf("\n  async function submitAction("),
                              APP.indexOf("\n  async function submitAction(") + 4000);
  check(/active_action_seat/.test(submitSrc),
        "submitAction picks its competitive token from the ACTIVE seat");
}

// ── 6. The hand-switch SCREEN is gone ────────────────────────────────────────
// A full-screen "Your Turn — Hand 2" card used to slide in and hold the board
// for 2.2 s every time the turn reached the player's other hand. The switch is
// instant now (and already done before the opponent finishes), so the overlay
// was one more thing to tap through. It has to be gone from all three files, or
// its leftovers throw on a missing element.
console.log("6. no hand-switch overlay is left anywhere");
{
  const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");
  const CSS  = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
  check(!/comp-handswitch/.test(HTML), "preview.html has no #comp-handswitch element");
  check(!/comp-handswitch|hs-progress|comp-hs-bar/.test(CSS), "preview.css has no overlay styling");
  check(!/comp-handswitch|showCompHandSwitch|checkCompHandSwitch|_compHsSuppressed/.test(APP),
        "preview-app.js neither shows nor hides an overlay that no longer exists");
  check(!/getElementById\("hs-[a-z-]+"\)/.test(APP),
        "no code reaches for the overlay's inner elements");
}

console.log(`\n${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
