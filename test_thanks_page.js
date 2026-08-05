#!/usr/bin/env node
/* The page Stripe drops a paying customer on: /thanks (multiplayer/client/thanks.html).
 *
 * Run:  node test_thanks_page.js        (no browser, no network needed)
 *
 * Two things on this page can go wrong in ways money makes expensive:
 *
 *   1. THE BACK-TO-GAME BUTTON. It sends the buyer back to wherever they were
 *      standing, read out of localStorage. That value crosses an origin
 *      boundary (our page → Stripe → back), so it is treated as UNTRUSTED: it
 *      must only ever produce a same-site PATH. If "//evil.com" or
 *      "javascript:…" could get through, a paid checkout page becomes an open
 *      redirect. Returning to a bare "/" also has to work, and returning INTO
 *      the dedicated game window (/game?game_window=1) has to be preserved or
 *      the launcher opens a second window on top of the player's game.
 *
 *   2. THE CONFIRMATION. The page must only say "confirmed" when the SERVER
 *      says the webhook fulfilled that session — never on its own say-so.
 *
 * The real thanks.html is parsed and its real inline script is executed in a
 * stubbed DOM, so these assertions are against the shipped file, not a copy.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const HTML_PATH = path.join(__dirname, "multiplayer/client/thanks.html");
const HTML = fs.readFileSync(HTML_PATH, "utf8");

let pass = 0;
const failures = [];
// Checks run in order and are AWAITED. An earlier version called fn() without
// awaiting, so every async check printed ✓ before it had actually run.
const queue = [];
function check(name, fn) { queue.push([name, fn]); }
function section(title) { queue.push([null, title]); }
async function runAll() {
  for (const [name, fn] of queue) {
    if (name === null) { console.log("\n" + fn); continue; }
    try { await fn(); console.log("  ✓ " + name); pass++; }
    catch (e) { console.log("  ✗ " + name + " — " + e.message); failures.push(name); }
  }
  console.log(`\n${pass} passed, ${failures.length} failed`);
  if (failures.length) { failures.forEach(f => console.log("  FAILED: " + f)); process.exit(1); }
}
function eq(actual, expected, what) {
  if (actual !== expected) {
    throw new Error(`${what || "value"}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

// ── Pull the page's inline script out of the real file ─────────────────────
const scripts = [...HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
if (scripts.length !== 1) {
  console.error(`Expected exactly one inline <script> in thanks.html, found ${scripts.length}.`);
  process.exit(1);
}
const PAGE_SCRIPT = scripts[0];

// ── A DOM small enough to be obviously correct ─────────────────────────────
function makeEnv(opts) {
  opts = opts || {};
  const els = {};
  const mk = (id, attrs) => (els[id] = Object.assign({
    id, href: "", className: "", textContent: "", innerHTML: "",
    style: {}, classList: { add(){}, remove(){} },
  }, attrs || {}));
  // Mirror the DEFAULTS the real markup ships with, so a rejected/absent stored
  // URL is asserted against what the buyer would actually see, not against "".
  mk("backBtn", { href: "/" });
  mk("status", { className: "status is-wait" });
  ["status-text", "status-note", "lead", "claimLink"].forEach(id => mk(id));

  const store = Object.create(null);
  if (opts.stored !== undefined) store["cc_stripe_return"] = opts.stored;

  const fetchCalls = [];
  const ctx = {
    console,
    document: { getElementById: (id) => els[id] || null },
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    location: { search: opts.search || "" },
    URLSearchParams,
    Date,
    Number,
    JSON,
    encodeURIComponent,
    setTimeout: (fn) => { if (opts.runTimers) fn(); return 0; },
    fetch: (url) => {
      fetchCalls.push(url);
      if (!opts.serverReply) return Promise.reject(new Error("network down"));
      return Promise.resolve({ json: () => Promise.resolve(opts.serverReply) });
    },
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(PAGE_SCRIPT, ctx);
  return { els, store, fetchCalls, ctx };
}

const FRESH = (url) => JSON.stringify({ url, at: Date.now() });

section("back-to-game button");

check("with nothing stored it falls back to the site root", () => {
  const { els } = makeEnv({});
  eq(els.backBtn.href, "/", "href");
});

check("returns INTO the dedicated game window, not the launcher", () => {
  // The bug this guards: "/" is the LAUNCHER. A buyer who opened the store
  // inside the game window and came back to "/" would spawn a SECOND window.
  const { els } = makeEnv({ stored: FRESH("/game?game_window=1") });
  eq(els.backBtn.href, "/game?game_window=1", "href");
});

check("returns to an in-progress room link", () => {
  const { els } = makeEnv({ stored: FRESH("/play/ABCD") });
  eq(els.backBtn.href, "/play/ABCD", "href");
});

check("a stored entry is cleared after use (never reused later)", () => {
  const { store } = makeEnv({ stored: FRESH("/game?game_window=1") });
  eq(store["cc_stripe_return"], undefined, "stored value after read");
});

check("an expired entry is ignored", () => {
  const stale = JSON.stringify({ url: "/game?game_window=1",
                                 at: Date.now() - 7 * 60 * 60 * 1000 });
  const { els } = makeEnv({ stored: stale });
  eq(els.backBtn.href, "/", "href");
});

check("malformed storage does not break the button", () => {
  for (const junk of ["not json", "{}", '{"url":123}', '{"url":"/x"}', "null", "[]"]) {
    const { els } = makeEnv({ stored: junk });
    eq(els.backBtn.href, "/", `href for stored ${junk}`);
  }
});

section("open-redirect guard (the stored value is untrusted)");

check("protocol-relative and absolute URLs are refused", () => {
  for (const evil of ["//evil.com", "//evil.com/x", "https://evil.com",
                      "http://evil.com", "HTTPS://evil.com"]) {
    const { els } = makeEnv({ stored: FRESH(evil) });
    eq(els.backBtn.href, "/", `href for ${evil}`);
  }
});

check("script and data URLs are refused", () => {
  for (const evil of ["javascript:alert(1)", "data:text/html,<script>",
                      "vbscript:msgbox", "mailto:a@b.com"]) {
    const { els } = makeEnv({ stored: FRESH(evil) });
    eq(els.backBtn.href, "/", `href for ${evil}`);
  }
});

check("a bare relative path (no leading slash) is refused", () => {
  const { els } = makeEnv({ stored: FRESH("evil.com") });
  eq(els.backBtn.href, "/", "href");
});

section("payment confirmation");

check("with no session_id it never claims a confirmation", () => {
  const { els, fetchCalls } = makeEnv({ search: "" });
  eq(fetchCalls.length, 0, "server calls");
  if (/confirmed/i.test(els["status-text"].textContent)) {
    throw new Error("claimed 'confirmed' without asking the server: " + els["status-text"].textContent);
  }
  if (!els["status-text"].textContent) throw new Error("status left blank");
});

check("it asks the server about the session Stripe named", () => {
  const { fetchCalls } = makeEnv({ search: "?session_id=cs_live_abc123" });
  eq(fetchCalls.length, 1, "server calls");
  if (!fetchCalls[0].includes("/api/stripe/session-status")) {
    throw new Error("wrong endpoint: " + fetchCalls[0]);
  }
  if (!fetchCalls[0].includes("cs_live_abc123")) {
    throw new Error("session id not sent: " + fetchCalls[0]);
  }
});

check("a confirmed coin purchase is reported with its amount", async () => {
  const { els } = makeEnv({
    search: "?session_id=cs_live_abc123",
    serverReply: { ok: true, processed: true, kind: "coins", value: 5250, matched: true },
  });
  await new Promise(r => setImmediate(r));
  if (!els["status-text"].textContent.includes("5,250")) {
    throw new Error("coin count missing: " + els["status-text"].textContent);
  }
  eq(els.status.className, "status is-ok", "status class");
});

check("a confirmed tier purchase names the tier", async () => {
  const { els } = makeEnv({
    search: "?session_id=cs_live_abc123",
    serverReply: { ok: true, processed: true, kind: "tier", value: "tide-turner", matched: true },
  });
  await new Promise(r => setImmediate(r));
  if (!/Tide Turner/.test(els["status-text"].textContent)) {
    throw new Error("tier name missing: " + els["status-text"].textContent);
  }
});

check("a tier purchase names the Critter Coins it granted", async () => {
  // tierCoins comes from the server's SUPPORTER_TIER_GRANTS, so the page can
  // promise the amount without keeping its own copy to drift out of date.
  const { els } = makeEnv({
    search: "?session_id=cs_live_abc123",
    serverReply: { ok: true, processed: true, kind: "tier", value: "tide-turner",
                   tierCoins: 30000, matched: true },
  });
  await new Promise(r => setImmediate(r));
  const txt = els["status-text"].textContent;
  if (!/Tide Turner/.test(txt) || !txt.includes("30,000")) {
    throw new Error("tier coins missing: " + txt);
  }
});

check("a tier with no coin grant still reads cleanly", async () => {
  const { els } = makeEnv({
    search: "?session_id=cs_live_abc123",
    serverReply: { ok: true, processed: true, kind: "tier", value: "wave-warrior",
                   tierCoins: 0, matched: true },
  });
  await new Promise(r => setImmediate(r));
  const txt = els["status-text"].textContent;
  if (!/Wave Warrior/.test(txt) || /Critter Coins/.test(txt)) {
    throw new Error("zero-coin tier printed a coin line: " + txt);
  }
});

check("an unmatched payment points the buyer at the claim flow", async () => {
  const { els } = makeEnv({
    search: "?session_id=cs_live_abc123",
    serverReply: { ok: true, processed: true, kind: "coins", value: 1000, matched: false },
  });
  await new Promise(r => setImmediate(r));
  if (!/attach|claim|match/i.test(els["status-note"].textContent)) {
    throw new Error("no claim guidance for an unmatched payment: " + els["status-note"].textContent);
  }
});

check("a not-yet-processed session never shows a false confirmation", async () => {
  const { els } = makeEnv({
    search: "?session_id=cs_live_abc123",
    serverReply: { ok: true, processed: false },
  });
  await new Promise(r => setImmediate(r));
  eq(els.status.className, "status is-wait", "status class");
});

section("page wiring");

check("the redirect URL Stripe must be given is documented in the file", () => {
  if (!HTML.includes("{CHECKOUT_SESSION_ID}")) {
    throw new Error("thanks.html no longer documents the {CHECKOUT_SESSION_ID} placeholder");
  }
  if (!HTML.includes("/thanks?session_id=")) {
    throw new Error("thanks.html no longer shows the exact redirect URL to paste into Stripe");
  }
});

check("the store records a return URL before leaving for Stripe", () => {
  const app = fs.readFileSync(
    path.join(__dirname, "multiplayer/client/js/preview-app.js"), "utf8");
  if (!app.includes("cc_stripe_return")) {
    throw new Error("preview-app.js no longer saves cc_stripe_return — the back button will always fall back to '/'");
  }
  const i = app.indexOf("_phstRememberReturn()");
  const j = app.indexOf("window.location.href = _phstStripeUrl");
  if (i < 0 || j < 0 || i > j) {
    throw new Error("the return URL is not saved BEFORE navigating to Stripe");
  }
});

runAll();
