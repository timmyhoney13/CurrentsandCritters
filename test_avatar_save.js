#!/usr/bin/env node
/* Equipping an icon must SAVE THAT ICON.
 *
 *   node test_avatar_save.js
 *
 * The bug: ownership is decided by string equality against the account's
 * unlocked_icons, and `sanitizeSelectableAvatar`'s answer to "you do not own
 * this" is to hand back Mullet. So any stored entry that was not a byte-exact
 * match ("/avatars/narwhal.png?v=1", "/avatars/Narwhal.png", a stray space)
 * meant the player clicked their critter and MULLET was written to Firestore,
 * with the modal reporting a successful save. The server had always read those
 * same entries through `split("?")[0].lower()` (eight places in
 * multiplayer_server.py); only the client demanded an exact match.
 *
 * preview-app.js is a 25k-line browser file, so instead of loading it we lift
 * the REAL source of the pieces under test out of it and run them in a sandbox,
 * exactly as test_avatar_reearn.js does. The tests fail if the shipped source
 * changes.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC_PATH = path.join(__dirname, "multiplayer/client/js/preview-app.js");
const SRC = fs.readFileSync(SRC_PATH, "utf8");

let passed = 0, failed = 0;
function check(name, cond, detail) {
  if (cond) { passed++; console.log("  ✓ " + name); }
  else { failed++; console.error("  ✗ FAIL: " + name + (detail ? ": " + detail : "")); }
}
function eq(name, got, want) {
  check(name, got === want, `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}
function section(t) { console.log("\n" + t); }

function slice(startMarker, endMarker) {
  const i = SRC.indexOf(startMarker);
  if (i < 0) throw new Error(`marker not found in preview-app.js: ${startMarker}`);
  const j = SRC.indexOf(endMarker, i + startMarker.length);
  if (j < 0) throw new Error(`end marker not found after ${startMarker}: ${endMarker}`);
  return SRC.slice(i, j);
}

const code = [
  slice("const ANIMAL_AVATARS = [", "\n  ];") + "\n];",
  slice("function normalizeAvatarUrl(raw) {", "// Every list of owned icons"),
  slice("function normalizeIconList(arr) {", "function getDefaultAvatar"),
  slice("function getDefaultAvatar(seed) {", "// ── What a guest may wear"),
  slice('const PAID_UNLOCK_TYPES = ["shop", "code"];', "function isAvatarUnlocked"),
  slice("function isAvatarUnlocked(img) {", '// "Have you EARNED this critter?"'),
  slice("function sanitizeSelectableAvatar(url, seed) {", "function guestStatsKeyForNickname"),
  slice("function hasStoredSelectableAvatar(profile) {", "function _avatarStatsAndLevel"),
  `;globalThis.AVATAR_OPTIONS = ANIMAL_AVATARS
     .filter(a => a.unlock && a.unlock.type === "starter").map(a => a.img);`,
].join("\n");

const S = {
  DEFAULT_AVATAR_IMG: "/avatars/mullet.png",
  _unlockedIcons: [],
  _galReadOnly: false,
  _authUser: { uid: "acct1" },
  _avatarOwnedOnly: false,
  _guestSessionActive: false,
  console,
};
vm.createContext(S);
vm.runInContext(code, S);

const run = (expr) => vm.runInContext(expr, S);
const norm = (u) => run(`normalizeAvatarUrl(${JSON.stringify(u)})`);
const iconList = (a) => run(`normalizeIconList(${JSON.stringify(a)})`);
function sanitize(pick, storedList) {
  S._unlockedIcons = iconList(storedList);
  return run(`sanitizeSelectableAvatar(${JSON.stringify(pick)}, "seed")`);
}
function keepsOnSignIn(storedAvatar, storedList) {
  S._unlockedIcons = iconList(storedList);
  const profile = { avatar_url: storedAvatar, unlocked_icons: storedList };
  return run(`hasStoredSelectableAvatar(${JSON.stringify(profile)})`);
}

// ── the starter set is exactly one icon, which is WHY this mattered ────────
section("Only Mullet is a starter, so every other icon lives or dies by unlocked_icons");
eq("AVATAR_OPTIONS is just Mullet",
   run("JSON.stringify(AVATAR_OPTIONS)"), '["/avatars/mullet.png"]');

// ── normalisation ─────────────────────────────────────────────────────────
section("normalizeAvatarUrl canonicalises our own avatar paths");
eq("a clean path is unchanged",   norm("/avatars/narwhal.png"),  "/avatars/narwhal.png");
eq("a ?v= cachebust is stripped", norm("/avatars/narwhal.png?v=2026-01-01"), "/avatars/narwhal.png");
eq("a #hash is stripped",         norm("/avatars/narwhal.png#x"), "/avatars/narwhal.png");
eq("case is folded",              norm("/avatars/Narwhal.PNG"),  "/avatars/narwhal.png");
eq("a relative path is absolute", norm("avatars/narwhal.png"),   "/avatars/narwhal.png");
eq("surrounding space is trimmed", norm("  /avatars/narwhal.png  "), "/avatars/narwhal.png");
eq("junk is not invented into a path", norm(""), "");

section("An EXTERNAL url keeps its query: a Google photoURL carries its size there");
const g = "https://lh3.googleusercontent.com/a/ACg8ocK=s96-c";
eq("google photoURL untouched", norm(g), g);
const gq = "https://example.com/pic.png?width=96";
eq("external ?query untouched", norm(gq), gq);

section("normalizeIconList canonicalises and de-duplicates a stored list");
eq("mixed shapes collapse to one entry",
   JSON.stringify(iconList(["/avatars/narwhal.png?v=1", "/avatars/Narwhal.png",
                            " /avatars/narwhal.png "])),
   '["/avatars/narwhal.png"]');
eq("non-avatar junk is dropped",
   JSON.stringify(iconList(["/backgrounds/reef.png", null, 7, "", "/avatars/lobster.png"])),
   '["/avatars/lobster.png"]');
eq("a non-array is an empty list", JSON.stringify(iconList("nope")), "[]");

// ── THE BUG: the pick a player clicks must be the pick that is saved ──────
section("The icon you click is the icon that gets saved, whatever shape it is stored in");
const shapes = {
  "stored clean":            ["/avatars/narwhal.png"],
  "stored with ?v=":         ["/avatars/narwhal.png?v=2026-01-01"],
  "stored MixedCase":        ["/avatars/Narwhal.png"],
  "stored with whitespace":  [" /avatars/narwhal.png "],
  "stored relative":         ["avatars/narwhal.png"],
};
for (const [label, stored] of Object.entries(shapes)) {
  eq(label, sanitize("/avatars/narwhal.png", stored), "/avatars/narwhal.png");
}

section("…and sign-in does not judge that stored avatar invalid (which wiped it to Mullet)");
for (const [label, stored] of Object.entries(shapes)) {
  check(label, keepsOnSignIn("/avatars/narwhal.png", stored) === true,
        "sign-in would overwrite avatar_url with Mullet");
}
check("a ?v= on the EQUIPPED avatar is fine too",
      keepsOnSignIn("/avatars/narwhal.png?v=9", ["/avatars/narwhal.png"]) === true);

// ── the ownership guard must SURVIVE the fix ──────────────────────────────
section("The ownership guard still holds: you cannot equip what you do not own");
eq("an unowned icon still falls back to Mullet",
   sanitize("/avatars/narwhal.png", []), "/avatars/mullet.png");
eq("owning a DIFFERENT icon does not unlock this one",
   sanitize("/avatars/narwhal.png", ["/avatars/lobster.png"]), "/avatars/mullet.png");
eq("Mullet itself is always allowed",
   sanitize("/avatars/mullet.png", []), "/avatars/mullet.png");
eq("a path outside the catalogue is refused",
   sanitize("/avatars/not-a-real-critter.png", []), "/avatars/mullet.png");
eq("a non-avatar path is refused",
   sanitize("/backgrounds/reef.png", []), "/avatars/mullet.png");
check("an unowned icon is still judged invalid at sign-in",
      keepsOnSignIn("/avatars/narwhal.png", []) === false);

// ── the source-level contracts the runtime cannot show without a DOM ─────
section("Source contracts");
check("no code builds an icon list with a raw startsWith filter any more",
      !/unlocked_icons\s*\.\s*(filter|map)\s*\(/.test(SRC),
      "an un-normalised icon list is a pick that silently saves as Mullet");
check("applyAvatarSelection reports success",
      /return true;\s*\n\s*\}\s*\n\s*\/\/ ── Giant Squid challenge modal/.test(SRC),
      "without a truthy return, a caller cannot tell a refusal from a save");
check("saveAvatarSelection refuses to report a save that did not happen",
      /const saved = await applyAvatarSelection\(selected\);[\s\S]{0,200}?if \(saved === false\)/.test(SRC));
check("the picker no longer silently swaps a click for Mullet",
      /if \(want && safe !== want\)/.test(SRC),
      "the click handler must surface the refusal, not select something else");

console.log(`\n══ RESULT: ${passed} passed, ${failed} failed ══`);
process.exit(failed ? 1 : 0);
