#!/usr/bin/env node
/* A REFUSED PROFILE READ IS NOT AN EMPTY ACCOUNT.
 *
 *   node test_profile_read_failure.js
 *
 * The bug, as players hit it: after a competitive match your name on Player
 * Home turned into "Player", the game announced a pile of critters you had
 * owned for weeks, and the next time you signed in you were wearing the
 * Mullet.
 *
 * None of that was the account changing. loadProfile() answers `null` both for
 * "this account has no document" and for "Firestore refused the read", and
 * loadAndRenderStats painted the second as the first: _activeProfile went
 * null, _unlockedIcons went empty, and it carried on to the end of the
 * function. From those empty caches the three symptoms follow exactly:
 *
 *   • syncStatsHeader(null) blanks the nickname            → "Player"
 *   • nothing is in _unlockedIcons, so every owned critter
 *     reads as locked and is re-granted and re-announced   → the unlock flood
 *   • hasStoredSelectableAvatar(null) is false, so the
 *     "no valid avatar" migration WRITES Mullet to the doc → the Mullet
 *
 * Competitive is where it showed because the end of a ranked match already has
 * saveGameStats' read and write, processRankedGameEnd's read and write, the
 * achievements read and the ranked_result POST in flight against this read.
 *
 * preview-app.js is a 39k-line browser file, so instead of loading it we lift
 * the REAL source of the pieces under test out of it and run them in a
 * sandbox, exactly as test_avatar_save.js and test_avatar_reearn.js do. The
 * tests fail if the shipped source changes.
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
  check(name, JSON.stringify(got) === JSON.stringify(want),
        `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}
function section(t) { console.log("\n" + t); }
function die(e) { console.error("  ✗ threw: " + (e && e.stack || e)); process.exit(1); }

function slice(startMarker, endMarker) {
  const i = SRC.indexOf(startMarker);
  if (i < 0) throw new Error(`marker not found in preview-app.js: ${startMarker}`);
  const j = SRC.indexOf(endMarker, i + startMarker.length);
  if (j < 0) throw new Error(`end marker not found after ${startMarker}: ${endMarker}`);
  return SRC.slice(i, j);
}

// ── The real function under test, plus the real helpers it decides with ─────
const code = [
  slice("const ANIMAL_AVATARS = [", "\n  ];") + "\n];",
  slice("function normalizeAvatarUrl(raw) {", "// Every list of owned icons"),
  slice("function normalizeIconList(arr) {", "function getDefaultAvatar"),
  slice("function getDefaultAvatar(seed) {", "// ── What a guest may wear"),
  slice('const PAID_UNLOCK_TYPES = ["shop", "code"];', "function isAvatarUnlocked"),
  slice("function isAvatarUnlocked(img) {", '// "Have you EARNED this critter?"'),
  slice("function sanitizeSelectableAvatar(url, seed) {", "function guestStatsKeyForNickname"),
  slice("function hasStoredSelectableAvatar(profile) {", "function _avatarStatsAndLevel"),
  slice("const _STATS_LOAD_RETRIES", "function setProfileLevelCard(stats) {"),
  `;globalThis.AVATAR_OPTIONS = ANIMAL_AVATARS
     .filter(a => a.unlock && a.unlock.type === "starter").map(a => a.img);
   globalThis.loadAndRenderStats = loadAndRenderStats;`,
].join("\n");

// Everything loadAndRenderStats reaches for, recorded rather than performed.
function makeSandbox(reads) {
  const log = { headerCalls: [], avatarWrites: [], statsInit: 0, reads: 0 };
  const S = {
    console: { log(){}, warn(){}, error(){} },
    // Resolve backoff waits at once so the retries don't make the test slow.
    setTimeout: (fn) => { if (typeof fn === "function") { try { fn(); } catch {} } return 0; },
    DEFAULT_AVATAR_IMG: "/avatars/mullet.png",
    _statsLoadSeq: 0,
    _authUser: { uid: "acct1" },
    _db: {},
    _galReadOnly: false,
    _avatarOwnedOnly: false,
    _guestSessionActive: false,
    // The caches the bug emptied. Seeded with a real, signed-in player.
    _activeProfile: {
      uid: "acct1", nickname: "ReefKeeper", friend_code: "AB12",
      avatar_url: "/avatars/narwhal.png",
      unlocked_icons: ["/avatars/narwhal.png"],
      stats: { completed_games: 407, total_xp: 90000 },
    },
    _unlockedIcons: ["/avatars/narwhal.png"],
    _unlockedBackgrounds: ["/backgrounds/reef.png"],
    _log: log,
    // ── stubs ──
    loadProfileWithReason: async () => { log.reads++; return reads.shift() || { profile: null, reason: "error" }; },
    initStatsIfAbsent: async () => { log.statsInit++; },
    defaultGuestStats: () => ({ completed_games: 0 }),
    normalizeBgUrl: (u) => String(u || ""),
    _popNewlyGrantedIcons: () => {},
    syncStatsHeader: (p) => { log.headerCalls.push(p ? (p.nickname ?? null) : null); },
    renderStats: () => {},
    applyAvatarSelection: async (u) => { log.avatarWrites.push(u); },
    window: {},
  };
  S.globalThis = S;
  vm.createContext(S);
  vm.runInContext(code, S);
  return S;
}

const OK = (over) => ({
  reason: "ok",
  profile: {
    uid: "acct1", nickname: "ReefKeeper", friend_code: "AB12",
    avatar_url: "/avatars/narwhal.png",
    unlocked_icons: ["/avatars/narwhal.png"],
    stats: { completed_games: 408, total_xp: 91000 },
    ...(over || {}),
  },
});
const ERR = { profile: null, reason: "error" };
const MISSING = { profile: null, reason: "notfound" };

// ══ 1. Firestore refuses the read ═══════════════════════════════════════════
section("A refused read leaves the signed-in player exactly as they were");
{
  const S = makeSandbox([ERR, ERR, ERR, ERR, ERR, ERR]);
  vm.runInContext("loadAndRenderStats('acct1')", S).catch(die).then(() => {
    eq("the profile is not thrown away", S._activeProfile.nickname, "ReefKeeper");
    eq("the unlocked critters are not forgotten", S._unlockedIcons, ["/avatars/narwhal.png"]);
    eq("the unlocked backgrounds are not forgotten", S._unlockedBackgrounds, ["/backgrounds/reef.png"]);
    eq("the equipped critter is untouched", S._activeProfile.avatar_url, "/avatars/narwhal.png");

    section("…so none of the three symptoms can happen");
    check("the header is never handed a null profile (which renders \"Player\")",
          !S._log.headerCalls.includes(null),
          "headerCalls=" + JSON.stringify(S._log.headerCalls));
    eq("no Mullet is written to the account", S._log.avatarWrites, []);
    check("the read was actually retried, not given up on",
          S._log.reads > 1, "reads=" + S._log.reads);
    check("a refused read never initialises stats over a real account",
          S._log.statsInit === 0, "statsInit=" + S._log.statsInit);
    stage2();
  });
}

// ══ 2. A read that succeeds still works ═════════════════════════════════════
function stage2() {
  section("A read that succeeds still paints the profile it read");
  const S = makeSandbox([OK()]);
  vm.runInContext("loadAndRenderStats('acct1')", S).catch(die).then(() => {
    eq("the fresh profile lands", S._activeProfile.stats.completed_games, 408);
    eq("the uid is stamped on it", S._activeProfile.uid, "acct1");
    eq("the unlocked list is refreshed", S._unlockedIcons, ["/avatars/narwhal.png"]);
    eq("the header got a real name", S._log.headerCalls, ["ReefKeeper"]);
    eq("nothing demotes a critter the player owns", S._log.avatarWrites, []);
    stage3();
  });
}

// ══ 3. The migration still does its job when it really applies ══════════════
function stage3() {
  section("An avatar this account genuinely does not own is still defaulted to Mullet");
  const S = makeSandbox([OK({ avatar_url: "/avatars/narwhal.png", unlocked_icons: [] })]);
  vm.runInContext("loadAndRenderStats('acct1')", S).catch(die).then(() => {
    eq("the Mullet migration still fires on a real read",
       S._log.avatarWrites, ["/avatars/mullet.png"]);

    section("…but it never rewrites a Mullet that is already a Mullet");
    const S2 = makeSandbox([OK({ avatar_url: "/avatars/mullet.png", unlocked_icons: [] })]);
    vm.runInContext("loadAndRenderStats('acct1')", S2).catch(die).then(() => {
      eq("no pointless write", S2._log.avatarWrites, []);
      stage4();
    });
  });
}

// ══ 4. A brand-new account is still initialised ═════════════════════════════
function stage4() {
  section("A genuinely absent document is still initialised (that path must survive)");
  const S = makeSandbox([MISSING, OK()]);
  vm.runInContext("loadAndRenderStats('acct1')", S).catch(die).then(() => {
    eq("stats were initialised exactly once", S._log.statsInit, 1);
    eq("and the new profile was read back", S._activeProfile.stats.completed_games, 408);

    section("A document that is absent AND then unreadable writes nothing");
    const S2 = makeSandbox([MISSING, ERR]);
    vm.runInContext("loadAndRenderStats('acct1')", S2).catch(die).then(() => {
      eq("the profile is kept", S2._activeProfile.nickname, "ReefKeeper");
      eq("no Mullet is written", S2._log.avatarWrites, []);
      stage5();
    });
  });
}

// ══ 5. Source guarantees that a sandbox cannot show ═════════════════════════
function stage5() {
  section("The guards that make the above true are in the shipped source");

  check("equipping refuses a substitute instead of silently wearing the Mullet",
        /if \(selected !== wanted\) \{[\s\S]{0,260}return false;/.test(SRC));

  check("an achievement check bails out when the records did not load",
        (SRC.match(/if \(!\(await _achievementsReady\(uid\)\)\) return;/g) || []).length === 6,
        "expected all six check sites gated");

  check("_achievementsReady answers false rather than an empty record set",
        /async function _achievementsReady\(uid\) \{[\s\S]{0,400}return false;\n\s*\}/.test(SRC));

  check("loadUserAchievements reports whether it actually loaded",
        /_achLoadedUid = uid;\n\s*return true;/.test(SRC));

  check("a token refresh for the same account no longer blanks the profile",
        /const _identityChanged = _ccBecomeIdentity\(\);\n\s*if \(_identityChanged\) \{\n\s*_playerNickname = "";/.test(SRC));

  check("revealLobby keeps the uid on the profile it hands the header",
        /if \(_authUser && _activeProfile\) _activeProfile\.uid = _authUser\.uid;\n\s*\$a\("auth-loading-screen"\)\.classList\.add\("hidden"\);\n\s*\$a\("auth-screen"\)\.classList\.add\("hidden"\);\n\s*showStatsLobby\(\);/.test(SRC));

  check("the retroactive unlock sweep will not re-grant on unread records",
        /met = _achOk && _isDone\(u\.achId\)/.test(SRC));

  check("loadAndRenderStats no longer reaches loadProfile's ambiguous null",
        !/async function loadAndRenderStats[\s\S]{0,2000}await loadProfile\(uid\)/.test(SRC));

  done();
}

function done() {
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}
