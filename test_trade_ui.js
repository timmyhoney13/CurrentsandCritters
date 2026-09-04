#!/usr/bin/env node
/* The trade screen's CLIENT half.
 *
 *   node test_trade_ui.js
 *
 * Two things are protected here.
 *
 * 1. THE DEAD END. A trade whose /api/trade/open failed used to leave the
 *    overlay open, saying "Something went wrong with the trade" (the map's
 *    generic fallback, because the server's real code had no sentence), with
 *    "Add item" still enabled because the renderer treated "no trade" as an
 *    OPEN trade. Every later tap then answered "Open a trade first", which is
 *    the one thing the player had already done. So: every server code has a
 *    sentence, a failed open offers Try again, and a missing trade is a status
 *    of its own rather than a fake open one.
 *
 * 2. SEASON PASS VOUCHERS as a tradable balance. They arrive with the Supporter
 *    Tiers and move like Critter Coins, which means every place the client
 *    REBUILDS an offer has to carry them: /api/trade/offer replaces a side
 *    wholesale, so a builder that forgets the field silently gives the
 *    vouchers back.
 *
 * preview-app.js is a 35k-line browser file, so the pure helpers are lifted out
 * and run in a sandbox, and the wiring is asserted against the real source and
 * the real markup.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC  = fs.readFileSync(path.join(__dirname, "multiplayer/client/js/preview-app.js"), "utf8");
const HTML = fs.readFileSync(path.join(__dirname, "multiplayer/client/preview.html"), "utf8");
const CSS  = fs.readFileSync(path.join(__dirname, "multiplayer/client/css/preview.css"), "utf8");

let passed = 0, failed = 0;
function check(name, cond, detail) {
  if (cond) { passed++; console.log("  ✓ " + name); }
  else { failed++; console.error("  ✗ FAIL: " + name + (detail ? ": " + detail : "")); }
}
function section(t) { console.log("\n" + t); }

function slice(startMarker, endMarker) {
  const i = SRC.indexOf(startMarker);
  if (i < 0) throw new Error(`marker not found in preview-app.js: ${startMarker}`);
  const j = SRC.indexOf(endMarker, i + startMarker.length);
  if (j < 0) throw new Error(`end marker not found after ${startMarker}: ${endMarker}`);
  return SRC.slice(i, j);
}

// ── lift the pure helpers ───────────────────────────────────────────────────
const code = [
  slice("function _trErrText(code) {", "\n    // POST to a /api/trade/"),
  slice("function _trOfferEmpty(o) {", "\n    // Render one side's items"),
].join("\n");
const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(code + "\nthis.API = { _trErrText, _trErrFull, _trOfferEmpty };", sandbox);
const { _trErrText, _trErrFull, _trOfferEmpty } = sandbox.API;

// ═══════════════════════════════════════════════════════════════════════════
section("every failure the server can report has a sentence of its own");
const GENERIC = "Something went wrong with the trade.";
// The exact codes multiplayer_server.py can put in `error` for a trade action.
// test_trade_logic.py derives this list from the server source and fails if a
// new one appears with no sentence; this half checks the sentences are useful.
const SERVER_CODES = [
  "open_failed", "offer_failed", "confirm_failed", "cancel_failed", "get_failed",
  "no_trade", "not_open", "not_participant", "already_completed", "changed",
  "bad_peer", "bad_participants", "bad_request", "unknown_action",
  "not_enough_coins", "not_enough_passes", "negative_coins", "negative_passes",
  "avatar_not_owned", "background_not_owned", "duplicate_avatar", "duplicate_background",
  "firestore_unavailable", "unauthorized", "auth", "network", "bad_response",
];
for (const c of SERVER_CODES) {
  check(`${c} says something specific`, _trErrText(c) !== GENERIC, _trErrText(c));
}
check("an unknown code still falls back to something", _trErrText("who_knows") === GENERIC);
check("a failed open points at the one action that helps",
      /try again/i.test(_trErrText("open_failed")), _trErrText("open_failed"));

section("the server's own account of a failure reaches the player");
check("detail is appended when the server sends one",
      _trErrFull({ error: "open_failed", detail: "PermissionDenied: nope" })
        .includes("PermissionDenied: nope"));
check("no detail means no empty brackets",
      !_trErrFull({ error: "open_failed" }).includes("("));
check("a blank detail is treated as none",
      !_trErrFull({ error: "open_failed", detail: "   " }).includes("("));

section("an offer counts Season Pass vouchers as something offered");
check("vouchers alone are not an empty offer",
      _trOfferEmpty({ coins: 0, passes: 2, avatars: [], backgrounds: [] }) === false);
check("nothing at all still is",
      _trOfferEmpty({ coins: 0, passes: 0, avatars: [], backgrounds: [] }) === true);
check("a missing passes field is still empty",
      _trOfferEmpty({ coins: 0, avatars: [], backgrounds: [] }) === true);
check("coins alone are still not empty",
      _trOfferEmpty({ coins: 5, passes: 0, avatars: [], backgrounds: [] }) === false);

// ═══════════════════════════════════════════════════════════════════════════
section("a trade that never opened is not painted as an open one");
const render = slice("function _trRender() {", "function _trOfferEmpty(o)");
check('the status falls back to "none", not "open"',
      /const status = _trState \? _trState\.status : "none";/.test(render));
check("the Add item button is driven by that status",
      /addBtn\.disabled = \(status !== "open"\)/.test(render));
check('there is a "none" arm that closes the footer down',
      /if \(status === "none"\)/.test(render));

const picker = slice("function _trShowPicker() {", "function _trHidePicker()");
// The dead-end sentence must be gone as a SHOWN string. It survives in the
// comments that explain why, which is the point of keeping them.
const shown = (t) => t.replace(/\/\/[^\n]*/g, "");
check("tapping Add item with no trade no longer says to open one",
      !/Open a trade first/.test(shown(picker)), picker);
check("it points at Try again instead", /Try again/.test(shown(picker)));
check('the "no longer open" case is still separate',
      /no longer open/.test(shown(picker)));
check("that sentence is not shown anywhere else either",
      !/_trToast\("Open a trade first/.test(SRC));

section("a failed open offers a way out");
check("the retry button is in the markup", HTML.includes('id="cc-trade-retry"'));
check("it lives in a wrapper that starts hidden",
      /id="cc-trade-retry-wrap" style="display:none;"/.test(HTML));
check("it is wired", SRC.includes('on("cc-trade-retry", _trRetryOpen)'));
check("retrying re-opens with the same peer",
      /async function _trRetryOpen\(\)[\s\S]{0,200}_trOpen\(_trPeerUid, _trPeerName\)/.test(SRC));
check("it is styled, so it is not an unstyled button on a dark overlay",
      CSS.includes("#cc-trade-retry-wrap"));
const open = slice("async function _trOpen(peerUid, peerName) {", "// The Try again button");
check("a failed open shows it", /_trShowRetry\(true\)/.test(open));
check("a successful open hides it again", /_trShowRetry\(false\)/.test(open));
check("a failed open reports the server's detail", /_trErrFull\(res\)/.test(open));
check("closing the overlay puts it away",
      /async function _trClose\(\)[\s\S]{0,800}_trShowRetry\(false\)/.test(SRC));

// ═══════════════════════════════════════════════════════════════════════════
section("Season Pass vouchers are offerable");
check("the picker has a tab for them",
      /data-tab="passes"[^>]*>Season Passes</.test(HTML));
check("with its own count input", HTML.includes('id="cc-trade-pass-input"'));
check("and its own Set button", HTML.includes('id="cc-trade-pass-set"'));
check("both are wired", SRC.includes('on("cc-trade-pass-set"')
      && /passInput\.addEventListener\("keydown"/.test(SRC));
check("the tab renders its own body", /_trPickerTab === "passes"/.test(SRC));
check("a voucher row can be removed like a coin row",
      /r\.type === "passes"\) _trSetPasses\(0\)/.test(SRC));
check("the balance comes off the account document, not stats",
      /function _trMyPasses\(\)[\s\S]{0,200}critter_pass_vouchers/.test(SRC));
check("you cannot offer more than you hold",
      /if \(n > have\) \{[\s\S]{0,200}Season Pass voucher/.test(SRC));

section("every offer this client builds carries every field");
// /api/trade/offer REPLACES a side, so a builder that omits a field wipes it.
const copy = slice("function _trOfferCopy() {", "function _trToggleItem");
for (const f of ["coins", "passes", "avatars", "backgrounds"]) {
  check(`_trOfferCopy carries ${f}`, new RegExp(`${f}:`).test(copy));
}
for (const fn of ["_trToggleItem", "_trSetCoins", "_trSetPasses"]) {
  const body = slice(`function ${fn}(`, "\n    }\n");
  check(`${fn} starts from _trOfferCopy()`, body.includes("_trOfferCopy()"), body);
}
check("no mutation hand-rolls a partial offer literal",
      !/\{ coins: Number\(cur\.coins\) \|\| 0,/.test(SRC));

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
