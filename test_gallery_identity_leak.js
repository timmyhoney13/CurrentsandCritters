#!/usr/bin/env node
/* Looking at someone else's collection must never rename you.
 *
 *   node test_gallery_identity_leak.js
 *
 * THE BUG (reported 2026-09-05): open another player's Critter Gallery from
 * their public profile, close it, and MY Player Home header was wearing THEIR
 * name and THEIR critter, and stayed that way.
 *
 * Anatomy, because the guard that was supposed to stop this was already there
 * and was not enough:
 *
 *   1. openPublicCritterGallery SWAPS the module globals (_activeProfile,
 *      _unlockedIcons, _playerNickname) to the other player and sets
 *      _galReadOnly. Everything downstream tells us apart by asking whether
 *      the profile's uid is _authUser.uid.
 *   2. setOnlineStatus, the 90-second presence heartbeat, patched the profile
 *      in memory with `{ ...(_activeProfile || {}), uid, online: true }` and
 *      was NOT read-only aware. That spread stamps MY uid onto THEIR data, so
 *      from that moment the uid check answers "yes, this is mine" about a
 *      document that is entirely somebody else's.
 *   3. syncStatsHeader then took their nickname as _playerNickname and painted
 *      their avatar into my header. Closing the gallery restored the globals
 *      and repainted nothing, so the screen kept the other player's identity.
 *
 * So the fix is three-part and all three parts are pinned here: the heartbeat
 * does not launder a swapped profile, the header declines to paint at all
 * while read-only, and leaving read-only REPAINTS from my restored profile.
 *
 * As with test_avatar_save.js / test_avatar_reearn.js, the real functions are
 * lifted out of the shipped preview-app.js and run in a sandbox, so the tests
 * fail if the shipped source stops doing this.
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

// ── the real functions under test ─────────────────────────────────────────
const code = [
  slice("    function _galRestoreMine() {", "    function _galRenderHeader() {"),
  slice("    async function setOnlineStatus(uid, isOnline) {", "    function startPresencePing"),
  slice("    function renderStatsAvatar(profile, nickname) {",
        "    // Re-apply my equipped background to all currently-visible own-avatar spots"),
  slice("    function resolveHeaderLevel(statsSource) {", "    // Pop a one-by-one"),
  slice("    function _popNewlyGrantedIcons(uid, icons) {", "    function syncStatsHeader"),
  slice("    function syncStatsHeader(profile) {", "    function setStatsAvatarClickable"),
].join("\n");

// ── a DOM small enough to read, big enough for the painters ───────────────
const els = {};
function el(id) {
  return els[id] || (els[id] = {
    id, textContent: "", src: "", style: {},
    _cls: new Set(),
    classList: { add: c => els[id]._cls.add(c), remove: c => els[id]._cls.delete(c),
                 contains: c => els[id]._cls.has(c) },
    setAttribute() {}, removeAttribute(k) { if (k === "src") els[id].src = ""; },
    contains: () => false,
  });
}

const painted = { header: 0, overview: 0 };

const S = {
  console,
  DEFAULT_AVATAR_IMG: "/avatars/mullet.png",
  ACHIEVEMENT_DEFS: [], ANIMAL_AVATARS: [],
  // globals the swap moves
  _galReadOnly: false, _galSavedState: null,
  _activeProfile: null, _unlockedIcons: [], _unlockedBackgrounds: [],
  _playerNickname: "", _friendCode: "", _pubProfileData: null,
  _authUser: null, _guestSessionActive: false, _guestAvatarUrl: "",
  _db: null,
  // helpers the lifted code leans on, stubbed to the identity of what they read
  $a: el,
  document: { activeElement: null, body: { style: {} } },
  window: {},
  firebase: { firestore: { FieldValue: { serverTimestamp: () => "TS" } } },
  setTimeout: (fn) => fn,
  safeInitial: (s) => String(s || "?").charAt(0).toUpperCase(),
  resolveAvatarUrl: (p) => (p && p.avatar_url) || "",
  sanitizeSelectableAvatar: (u) => u || "/avatars/mullet.png",
  _avSrc: (u) => u,
  _applyAvBg() {}, _galEquippedBg: () => "",
  normalizeIconList: (a) => (Array.isArray(a) ? a.slice() : []),
  normalizeBgUrl: (s) => s,
  formatFriendCodeLabel: (n, c) => (c ? "Friend code: " + c : ""),
  formatLastActiveLabel: () => "",
  getStoredTotalXp: (s) => Number((s && s.total_xp) || 0),
  getLevelProgressFromTotalXp: () => ({ level: 1 }),
  loadGuestStats: () => ({}),
  defaultGuestStats: () => ({}),
  _computeStreakInfo: () => ({ current: 0 }),
  renderPhOverview() { painted.overview++; },
  openAvatarGallery() {},
};
S.window = S;
S.globalThis = S;
vm.createContext(S);
vm.runInContext(code, S);

// syncStatsHeader is the thing we watch; count its paints without losing it
const realSync = S.syncStatsHeader;
S.syncStatsHeader = function (p) { painted.header++; return realSync(p); };

// openPublicCritterGallery calls openAvatarGallery + hides an overlay; the swap
// itself is all this test needs, so run the real swap body directly.
vm.runInContext(slice("    function openPublicCritterGallery(profile) {",
                      "    // Safety net:"), S);

const ME = {
  uid: "me-uid", nickname: "Tim", avatar_url: "/avatars/narwhal.png",
  friend_code: "1234", unlocked_icons: ["/avatars/narwhal.png"], stats: { total_xp: 900 },
};
const THEM = {
  uid: "them-uid", nickname: "Shelly", avatar_url: "/avatars/lobster.png",
  friend_code: "9999", unlocked_icons: ["/avatars/lobster.png"], stats: { total_xp: 5 },
};

function signInAsMe() {
  S._authUser = { uid: ME.uid };
  S._activeProfile = { ...ME };
  S._unlockedIcons = ME.unlocked_icons.slice();
  S._unlockedBackgrounds = [];
  S._playerNickname = "Tim";
  S._friendCode = "1234";
  S._galReadOnly = false; S._galSavedState = null;
  S._db = null;
  painted.header = 0; painted.overview = 0;
  S.syncStatsHeader(S._activeProfile);
}
const run = (expr) => vm.runInContext(expr, S);
const headerName   = () => el("stats-lobby-nick").textContent;
const headerAvatar = () => el("stats-avatar-img").src;

// ── the leak itself ───────────────────────────────────────────────────────
section("The presence heartbeat must not launder a swapped-in profile");

signInAsMe();
eq("my header starts as me", headerName(), "Tim");
eq("wearing my critter", headerAvatar(), "/avatars/narwhal.png");

run(`openPublicCritterGallery(${JSON.stringify(THEM)})`);
check("read-only is on", S._galReadOnly === true);
eq("the globals are theirs while I look", S._activeProfile.nickname, "Shelly");

// The heartbeat that used to do the damage. It writes presence to Firestore
// (stubbed) and then decides whether to patch the profile in memory.
let wroteUid = null, wrotePayload = null;
S._db = { collection: () => ({ doc: (u) => ({ update: async (p) => { wroteUid = u; wrotePayload = p; } }) }) };
S.window.CC_DEVICE = "Computer";

(async () => {
  await run("setOnlineStatus('me-uid', true)");

  eq("the presence WRITE still happens, against my own uid", wroteUid, "me-uid");
  check("and carries only presence fields",
        Object.keys(wrotePayload).sort().join(",") === "device,last_active,online",
        JSON.stringify(wrotePayload));

  check("_activeProfile is NOT patched while read-only", S._activeProfile.uid === "them-uid",
        `uid became ${S._activeProfile.uid}`);
  eq("so their profile never gets stamped with my uid", S._activeProfile.nickname, "Shelly");
  eq("my nickname global is untouched by the heartbeat", S._playerNickname, "Shelly");

  section("The header refuses to paint at all while read-only");

  painted.header = 0;
  run("syncStatsHeader(_activeProfile)");
  eq("the header still says my name", headerName(), "Tim");
  eq("and still wears my critter", headerAvatar(), "/avatars/narwhal.png");

  // Even handed their profile directly, and even with my uid forged onto it,
  // the read-only guard is what stops the paint. This is the belt to the
  // heartbeat's braces: one of the two failing is not enough to leak.
  run(`syncStatsHeader(${JSON.stringify({ ...THEM, uid: ME.uid })})`);
  eq("a forged uid cannot get their name onto my header", headerName(), "Tim");
  eq("nor their critter", headerAvatar(), "/avatars/narwhal.png");

  section("Leaving read-only repaints from MY profile");

  painted.header = 0; painted.overview = 0;
  const restored = run("_galRestoreMine()");
  eq("_galRestoreMine reports it did the restore", restored, true);
  eq("read-only is off", S._galReadOnly, false);
  eq("my profile is back", S._activeProfile.uid, "me-uid");
  eq("my nickname is back", S._playerNickname, "Tim");
  eq("my icons are back", JSON.stringify(S._unlockedIcons),
     JSON.stringify(["/avatars/narwhal.png"]));
  check("and it REPAINTED the header rather than leaving the screen stale",
        painted.header >= 1);
  check("and the Player Home overview with it", painted.overview >= 1);
  eq("the header reads me", headerName(), "Tim");
  eq("the header wears my critter", headerAvatar(), "/avatars/narwhal.png");

  eq("a second call is a no-op", run("_galRestoreMine()"), false);

  section("The repaint heals a header that something else got wrong");

  // Simulate the pre-fix damage directly: their name and critter painted in.
  signInAsMe();
  run(`openPublicCritterGallery(${JSON.stringify(THEM)})`);
  el("stats-lobby-nick").textContent = "Shelly";
  el("stats-avatar-img").src = "/avatars/lobster.png";
  S._playerNickname = "Shelly";
  run("_galRestoreMine()");
  eq("closing puts my name back on screen", headerName(), "Tim");
  eq("and my critter back on screen", headerAvatar(), "/avatars/narwhal.png");

  section("Viewing two players in a row still restores ME, not the first of them");

  signInAsMe();
  run(`openPublicCritterGallery(${JSON.stringify(THEM)})`);
  run(`openPublicCritterGallery(${JSON.stringify({ ...THEM, uid: "third", nickname: "Marlow", avatar_url: "/avatars/bonito.png" })})`);
  run("_galRestoreMine()");
  eq("back to me", S._playerNickname, "Tim");
  eq("not to the player in between", headerName(), "Tim");
  eq("wearing mine", headerAvatar(), "/avatars/narwhal.png");

  section("Signed out, nothing changes for a guest");

  S._authUser = null; S._guestSessionActive = true;
  S._galReadOnly = false; S._galSavedState = null;
  S._playerNickname = "Guesty";
  S._guestAvatarUrl = "/avatars/bonito.png";
  S._activeProfile = { nickname: "Guesty", avatar_url: "/avatars/bonito.png" };
  run("syncStatsHeader(_activeProfile)");
  eq("a guest still paints their own name", headerName(), "Guesty");
  eq("and their own pick", headerAvatar(), "/avatars/bonito.png");

  // ── source-level invariants, so a refactor cannot quietly undo this ─────
  section("The shipped source keeps the three guards");

  const presence = slice("    async function setOnlineStatus(uid, isOnline) {",
                         "    function startPresencePing");
  check("setOnlineStatus gates its in-memory patch on !_galReadOnly",
        /_authUser\.uid === uid && !_galReadOnly/.test(presence), presence.slice(-400));

  const header = slice("    function syncStatsHeader(profile) {", "    function setStatsAvatarClickable");
  check("syncStatsHeader returns early while read-only",
        /if \(_galReadOnly\) return;/.test(header));
  check("and its early return comes BEFORE it reads _playerNickname",
        header.indexOf("if (_galReadOnly) return;") < header.indexOf("_playerNickname ="));
  check("a null headerProfile no longer falls back to a stranger's profile",
        /renderStatsAvatar\(headerProfile \|\| \(_authUser \? null : _activeProfile\), nick\)/.test(header),
        "the `headerProfile || _activeProfile` fallback is back");

  // Two assignments and no more: _galRestoreMine, and the sign-out identity
  // wipe (which has nothing to restore TO, so it cannot go through the
  // helper). A third would be a hand-rolled exit that skips the repaint,
  // which is exactly the shape of the bug this file exists for.
  const clears = (SRC.match(/^\s*_galReadOnly = false;/gm) || []).length;
  check("only two places clear read-only, and neither is a hand-rolled exit",
        clears === 2, String(clears));
  check("closeAvatarGallery restores through it",
        /if \(_galRestoreMine\(\)\) \{/.test(
          slice("    function closeAvatarGallery() {", "    // Open the REAL avatar gallery")));
  check("__fishExitGalleryViewOnly restores through it too",
        /if \(!_galRestoreMine\(\)\) return;/.test(
          slice("    window.__fishExitGalleryViewOnly = function () {", "    function _galRenderHeader")));
  check("_galRestoreMine repaints the header",
        /syncStatsHeader\(_activeProfile\)/.test(
          slice("    function _galRestoreMine() {", "    function closeAvatarGallery()")));

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
