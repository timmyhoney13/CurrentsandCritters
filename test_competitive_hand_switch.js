#!/usr/bin/env node
/* Competitive mode: the turn must actually SWITCH to your other hand.
 *
 * Run:  node test_competitive_hand_switch.js
 *
 * Competitive is a 4-seat game where each human owns TWO seats (P1 = {0,1},
 * P2 = {2,3}) and the engine interleaves them 0 → 2 → 1 → 3. One client
 * therefore holds two seat tokens and has to re-fetch the room state with the
 * token of whichever of its hands is currently active.
 *
 * The trap this file guards: the SAME server state_version can legitimately
 * arrive as two DIFFERENT views. `state_view` is per-token — viewer.can_act,
 * legal_actions and the private hand all come from the polling seat — so
 * version 59 fetched with hand 1's token says "waiting…" while version 59
 * fetched with hand 2's token says "YOUR TURN" with hand 2's cards. A render
 * gate keyed on the version ALONE silently dropped the second one, which froze
 * the client on hand 1's board and made competitive look like it never passed
 * the turn to your other hand.
 *
 * These checks run the REAL buildStateUrl() + applyServerPayload() lifted out of
 * preview-app.js against a fake server that reproduces the live server's
 * behaviour (verified against multiplayer_server.py: one version per turn
 * boundary, per-token viewer/legal_actions/hand).
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
// per-token state_view (each token sees ITS OWN viewer/legal_actions/hand).
const TURN_ORDER = [0, 2, 1, 3];
const TOKENS = { 0: "tok0", 1: "tok1", 2: "tok2", 3: "tok3" };
const SEAT_BY_TOKEN = { tok0: 0, tok1: 1, tok2: 2, tok3: 3 };

function makeServer() {
  return {
    turn: 0,
    version: 10,
    get activeSeat() { return TURN_ORDER[this.turn % 4]; },
    endTurn() { this.turn++; this.version += 20; },
    stateFor(token) {
      const viewerSeat = SEAT_BY_TOKEN[token];
      if (viewerSeat === undefined) return { ok: false, error: "seat token invalid" };
      const active = this.activeSeat;
      return {
        ok: true,
        version: this.version,
        active_action_seat: active,
        viewer: { seat_index: viewerSeat, can_act: viewerSeat === active },
        // Only the ACTIVE seat gets a playable action list — this is what makes
        // two same-version payloads genuinely different documents.
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
// Drives the real buildStateUrl/applyServerPayload the way the 1 s poll timer
// does, with a deterministic setTimeout queue so the re-fetch is observable.
function makeClient(server, mySeats) {
  const rendered = [];   // every payload that reached renderPayload()
  const timers = [];

  const sandbox = {
    console,
    URLSearchParams,
    roomId: "COMPTS",
    compMode: true,
    compMySeats: mySeats.slice(),
    compTokens: { [mySeats[0]]: TOKENS[mySeats[0]], [mySeats[1]]: TOKENS[mySeats[1]] },
    compHostToken: "",
    latestPayload: null,
    _lastStateVersion: -1,
    _lastRenderedVersion: -1,
    _lastRenderedKey: "",
    _compRefetchKey: "",
    getSeatToken: () => TOKENS[mySeats[0]],
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
  vm.runInContext([grabFn("buildStateUrl"), grabFn("applyServerPayload")].join("\n"), sandbox);

  return {
    sandbox,
    rendered,
    // One tick of the 1 s poll timer, then drain whatever the payload scheduled
    // (the competitive re-fetch fires from a setTimeout).
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

  // Opponent ends → seat 1 = MY OTHER HAND. This is the switch that broke.
  server.endTurn();
  p1.poll();
  check(shownSeat(p1) === 1,
        "hand 2's turn: client switched its view to seat 1 (not stuck on hand 1)");
  check(shownCanAct(p1) === true,
        "hand 2's turn: the rendered view says YOUR TURN");
  check((p1.rendered[p1.rendered.length - 1].legal_actions.actions || []).length > 0,
        "hand 2's turn: the rendered view carries hand 2's legal actions");
  check(p1.rendered[p1.rendered.length - 1].state.players
          .find(p => p.index === 1).hand.length > 0,
        "hand 2's turn: the rendered view carries hand 2's cards");

  // Nothing changes server-side → no repeated re-fetch storm.
  const before = p1.rendered.length;
  p1.poll(); p1.poll();
  check(p1.rendered.length === before,
        "idle polls at the same version+seat do not re-render");

  // Hand 2 ends → seat 3 (opponent hand 2), then back around to seat 0.
  server.endTurn();
  p1.poll();
  check(shownCanAct(p1) === false, "opponent hand 2's turn: waiting again");
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
  server.endTurn(); p2.poll();                   // seat 2 = P2 hand 1
  check(shownSeat(p2) === 2 && shownCanAct(p2) === true, "P2 hand 1 gets its turn");
  server.endTurn(); p2.poll();                   // seat 1 (opponent)
  check(shownCanAct(p2) === false, "P1's hand 2 turn: P2 waits");
  server.endTurn(); p2.poll();                   // seat 3 = P2 hand 2
  check(shownSeat(p2) === 3 && shownCanAct(p2) === true,
        "P2 hand 2 gets its turn (the second-hand switch, mirrored)");
}

// ── 3. A dead token must not spin the re-fetch forever ───────────────────────
console.log("3. a seat whose token no longer resolves cannot loop the client");
{
  const server = makeServer();
  const p1 = makeClient(server, [0, 1]);
  // Hand 2's token is present but the server hands back the wrong viewer for it
  // (expired/rotated token). The client must give up, not re-fetch endlessly.
  server.stateFor = function (token) {
    const active = this.activeSeat;
    return {
      ok: true, version: this.version, active_action_seat: active,
      viewer: { seat_index: 0, can_act: active === 0 },
      legal_actions: { actions: [] },
      state: { players: [] },
    };
  };
  server.turn = 2; // seat 1 active — one of mine, but unreachable
  let threw = false;
  try { p1.poll(); } catch (e) { threw = true; }
  check(!threw, "a token that never resolves to the active seat settles instead of looping");
}

// ── 4. Source invariants (so the gate cannot be quietly re-broken) ───────────
console.log("4. preview-app.js keeps the per-seat render gate");
{
  check(!/_lastRenderedVersion/.test(APP),
        "the version-only render gate (_lastRenderedVersion) is gone");
  check(/_lastRenderedKey/.test(APP),
        "renders are gated on a version+viewer-seat key (_lastRenderedKey)");
  const applySrc = grabFn("applyServerPayload");
  check(/viewer[\s\S]{0,40}seat_index/.test(applySrc),
        "applyServerPayload's render gate reads viewer.seat_index");
  // submitAction must send the token of the seat the server will act as.
  const submitSrc = APP.slice(APP.indexOf("\n  async function submitAction("),
                              APP.indexOf("\n  async function submitAction(") + 4000);
  check(/active_action_seat/.test(submitSrc),
        "submitAction picks its competitive token from the ACTIVE seat");
}

console.log(`\n${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
