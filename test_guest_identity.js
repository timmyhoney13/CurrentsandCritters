#!/usr/bin/env node
/* A GUEST IS A DIFFERENT PERSON. Driven in a real browser, both halves.
 *
 * Signing out does not reload the page, and Player Home is not redrawn from
 * Firestore on every paint: it is drawn from module-level caches. Nothing ever
 * emptied them. So signing out of an account and tapping PLAY AS GUEST handed
 * the guest the account that had just left, and the guest's own empty numbers
 * could not push it out, because two of those caches deliberately refuse to
 * let an empty read overwrite good data. What a guest actually saw was the
 * last account's hours, its game history and its unlocked achievements.
 *
 * The harness signs a real account in against a stubbed Firebase, walks its
 * Player Home, signs out, plays as a guest, and walks the same tabs again.
 * Everything the account had must be gone from the second walk.
 *
 * It also pins the four things a guest is entitled to and was not getting:
 *
 *   1. THE CRITTER THEY EQUIPPED. Guests have been able to choose one since
 *      the padlock wall came down, but __fishMyAvatarUrl(), the face every
 *      other player sees, still answered "/avatars/mullet.png" for anyone
 *      without an account. A guest equipped a critter, watched Player Home
 *      change, sat down at a table and was a Mullet again.
 *   2. THE LEADERBOARDS. Every board read the users collection straight from
 *      the browser, which a guest holds no session for, so all six came back
 *      permission-denied. They come from /api/leaderboard now.
 *   3. THE LEVEL PASS AND PRESTIGE. Both are published reward ladders, and
 *      both refused to describe themselves to somebody without an account.
 *   4. A COMPETITIVE TAB WITH NO STRAY "0 0" ON IT. Three legacy divs with no
 *      labels sat at the bottom of the panel, and renderStats() un-hid them.
 *
 * Run:  node test_guest_identity.js        (needs Google Chrome)
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (detail != null ? "  [" + String(detail).slice(0, 160) + "]" : "")); }
}

const APP  = read("js/preview-app.js");
const HTML = read("preview.html");
const PASS = read("js/level-pass.js");
const PRES = read("js/prestige-ui.js");
const SRV  = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");
const PSRV = fs.readFileSync(path.join(ROOT, "prestige_server.py"), "utf8");

// ══════════════════════════════════════════════════════════════════════════
// Source-level: the rules, so a future edit that breaks one says which one
// ══════════════════════════════════════════════════════════════════════════
console.log("\none identity at a time");
check("the person at the keyboard has a name", /function _ccIdentityName\(\)/.test(APP));
check("an account and a guest can never collide on it",
      /return "acct:" \+ _authUser\.uid;/.test(APP) && /return "guest:" \+ String\(_playerNickname/.test(APP));
check("changing it empties every cache that belongs to a person",
      /function _ccBecomeIdentity\(\)[\s\S]{0,320}_ccWipeIdentityCaches\(\);/.test(APP));
check("the wipe runs only on a REAL change", /if \(now === _ccIdentityKey\) return false;/.test(APP));
check("both roads into the lobby go through it",
      /function revealLobby\([\s\S]{0,700}?_ccBecomeIdentity\(\);/.test(APP));
for (const [what, re] of [
  ["the stats blob",        /_phStats = null;\s*\n\s*_phStatsRaw = null;/],
  ["the achievements",      /_userAchievements = \{\};\s*\n\s*_achLoadedUid = null;/],
  ["the unlocked critters", /_unlockedIcons = \[\];\s*\n\s*_unlockedBackgrounds = \[\];/],
  ["the friend list",       /_lbFriendUids = new Set\(\);/],
  ["the Level Pass",        /window\.__ccLevelPassReset && window\.__ccLevelPassReset\(\)/],
  ["Prestige",              /window\.__ccPrestigeReset && window\.__ccPrestigeReset\(\)/],
  ["the seat-avatar push",  /window\.__fishForgetPushedAvatar && window\.__fishForgetPushedAvatar\(\)/],
]) check(what + " goes with it", re.test(APP));
check("Prestige really has a reset to call", /window\.__ccPrestigeReset = function/.test(PRES));
check("the sticky stats cache remembers WHOSE it is", /_phStatsOwner !== _ccIdentityKey/.test(APP));
check("…and refuses to be re-applied to anybody else",
      /_phStatsOwner === _ccIdentityKey\s*\n\s*&& \(\(_phStatsRaw\.completed_games/.test(APP));

console.log("\nprogress filed on the device is filed under a name");
check("there is one helper, not a scattering of key strings", /function ccScopedKey\(base\)/.test(APP));
check("an account adopts its pre-scoping progress once, so nobody loses a streak",
      /who\.startsWith\("acct:"\) && localStorage\.getItem\(key\) === null/.test(APP));
for (const key of ["_DAILY_STATE_KEY", "_WEEKLY_STATE_KEY", "STREAK_KEY", "SEEN_KEY",
                   "GAL_NEW_KEY", "NOTIF_KEY"]) {
  check(`${key} is scoped on read and write`,
        new RegExp(`getItem\\(ccScopedKey\\(${key}\\)\\)`).test(APP)
        && new RegExp(`setItem\\(ccScopedKey\\(${key}\\),`).test(APP));
}
check("a guest sign-out sweeps only the guest's copies",
      /CC_SCOPED_KEYS\.map\(k => k \+ "::guest:"\)/.test(APP));
check("…and no longer deletes the shared keys an account may still hold",
      !/GUEST_WIPE_KEYS/.test(APP));

console.log("\nthe critter a guest equipped is the critter they wear");
check("the mullet is no longer hard-coded for everyone without an account",
      !/\/\/ Guests are always the Mullet, they cannot choose an avatar\./.test(APP));
check("a guest's own pick is what the bridge answers",
      /const guest = !_authUser && _guestSessionActive;[\s\S]{0,600}guest \? \(_guestAvatarUrl \|\| fromProfile/.test(APP));
check("the profile card shows it too", !/\/\/ Guests are locked to the Mullet, never show a chosen\/saved avatar\./.test(APP));
check("and the gallery still opens from it", /setStatsAvatarClickable\(!!\(_authUser \|\| _guestSessionActive\)\)/.test(APP));
check("equipping still pushes the new face to the table",
      /window\.__fishPushSeatAvatar === "function"\) window\.__fishPushSeatAvatar\(\)/.test(APP));

console.log("\nwearable is not the same question as earned");
check("there is a separate test for having earned a critter", /function isAvatarEarned\(img\)/.test(APP));
check("it counts starters and real unlocks only",
      /function isAvatarEarned[\s\S]{0,320}return _unlockedIcons\.includes\(n\);/.test(APP));
check("the Overview counter asks it", (APP.match(/isAvatarEarned\(a\.img\)/g) || []).length >= 4);
check("the gallery still asks the wearable one, so a guest can still dress up",
      /if \(_isGuestSession\(\) && !_galReadOnly\) return !isPaidAvatar\(n\);/.test(APP));

console.log("\nthe leaderboards open for a guest");
check("one query stands behind every board", /async function lbTopUsers\(field, limit\)/.test(APP));
check("a signed-in player still reads Firestore directly",
      /if \(_db && _authUser\) \{\s*\n\s*const snap = await _db\.collection\("users"\)\.orderBy\(field, "desc"\)/.test(APP));
check("a guest is served the same rows by the server",
      /\/api\/leaderboard\?board=\$\{encodeURIComponent\(field\)\}/.test(APP));
check("the tab no longer bails out when there is no _db", !/async function renderPhLeaderboard\(\) \{\s*\n\s*if \(!_db\) return;/.test(APP));
check("the server has the endpoint", /if parsed\.path == "\/api\/leaderboard":/.test(SRV));
check("only whitelisted fields may be sorted on", /if board not in _LB_BOARD_FIELDS:/.test(SRV));
check("rows are rebuilt from a whitelist, so nothing private can ride along",
      /stats = \{k: src\[k\] for k in _LB_STATS_FIELDS if k in src\}/.test(SRV));
// Read the whitelist TUPLE itself, not "everything up to the next global":
// that marker was _LB_CACHE, which no longer exists, and indexOf(-1) quietly
// turned the slice into the whole file, so the check failed on an unrelated
// mention of email hundreds of lines away.
const _LB_WHITELIST = SRV.slice(SRV.indexOf("_LB_STATS_FIELDS"),
                                SRV.indexOf("_LB_TTL_SEC"));
check("the whitelist was actually found", _LB_WHITELIST.includes("total_xp")
      && _LB_WHITELIST.length < 2000, String(_LB_WHITELIST.length));
check("no email is in that whitelist", !/"email"/.test(_LB_WHITELIST));
check("the dev account is dropped, as it is everywhere else",
      /_fetch_leaderboard_rows[\s\S]{0,900}currentsandcritters@gmail\.com/.test(SRV));
check("'your rank' cards are cleared when the session changes hands",
      /function phLbClearSumCards\(\)/.test(APP) && /phLbClearSumCards\(\);/.test(APP));

console.log("\nthe two reward ladders describe themselves to a guest");
check("the Level Pass draws its track for a guest", /if \(!_state\.signedIn && !isGuestView\(\)\)/.test(PASS));
check("at the guest's own level, off the served curve", /function guestLevelFromXp\(totalXp\)/.test(PASS));
check("claiming is the one thing that needs an account", /foot = `<span class="ccLP-tier-lock">Sign in to claim<\/span>`/.test(PASS));
check("Prestige state is readable signed-out on the server",
      /if action == "state" and not uid:/.test(PSRV));
check("…built from an EMPTY account, so it carries no one's data",
      /payload = _state_payload\("", \{\}\)/.test(PSRV));
check("the client stops refusing to ask", /if \(!body\.idToken && action !== "state"\) return \{ ok: false, error: "unauthorized" \};/.test(PRES));
check("a guest's real level is folded into the ladder", /function applyGuestProgress\(\)/.test(PRES));
check("but a run is never started from a browser", /S\.state\.can_prestige = false;/.test(PRES));

console.log("\nthe stray \"0 0\" on the Competitive tab");
check("the unlabelled legacy divs are gone from the page", !/id="stats-comp-block"/.test(HTML));
check("stat-comp-top went with them", !/stat-comp-top/.test(HTML + APP));
check("stat-comp-wins too", !/stat-comp-wins/.test(HTML + APP));
check("nothing un-hides them any more", !/compBlock\.style\.display/.test(APP));
check("the always-hidden summary grid went as well", !/stats-summary-grid/.test(HTML + APP));
check("the Normal tab's real block is untouched", /normalBlock\.style\.display = completed \? "" : "none";/.test(APP));

console.log("\ncompetitive history is MY history");
// The filter now runs through _compHistoryEntry, which reads BOTH competitive
// record shapes (1v1's p1_name/p2_name and the free-for-all's players list) and
// answers whether this player was in the game. Same rule, one more mode.
check("the server's whole ledger is filtered to my seat name",
      /const myGames = myName\s*\n\s*\? data\.games\.map\(g => \[g, _compHistoryEntry\(g, myName\)\]\)\.filter\(\(\[, e\]\) => e\.mine\)/.test(APP));
check("and a nameless session lists nothing rather than everything",
      /: \[\];\s*\n\s*if \(!myGames\.length\) \{/.test(APP));

if (!CHROME) {
  console.log("\nSKIP: no Chrome found, the live account→guest walkthrough did not run.");
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

// ══════════════════════════════════════════════════════════════════════════
// Live: sign a real account in, sign out, play as a guest, compare
// ══════════════════════════════════════════════════════════════════════════
// A stub Firebase, because the point of the test is what the APP does with an
// account, not what Firebase does. One users/ doc, one auth user, an in-memory
// document store that understands the handful of operations the sign-in path
// performs. The real compat SDK is stripped out of the page: it loads from a
// CDN, and when it is present it overwrites this stub and every sign-in fails.
const STUB = `
window.__FISH_FIREBASE_CONFIG = { apiKey: "test-api-key", projectId: "test-project" };
(function () {
  var STORE = window.__STUB_DOCS || (window.__STUB_DOCS = {});
  function clone(o) { try { return JSON.parse(JSON.stringify(o)); } catch (_) { return o; } }
  function isOp(v) { return v && typeof v === "object" && v.__op; }
  function applyOps(base, patch) {
    var out = base ? clone(base) : {};
    (function walk(dst, src) {
      Object.keys(src || {}).forEach(function (k) {
        var v = src[k];
        if (isOp(v)) {
          if (v.__op === "inc") dst[k] = Number(dst[k] || 0) + v.n;
          else if (v.__op === "ts") dst[k] = Date.now();
          else if (v.__op === "del") delete dst[k];
          else if (v.__op === "union") { var a = Array.isArray(dst[k]) ? dst[k] : []; v.vals.forEach(function (x) { if (a.indexOf(x) < 0) a.push(x); }); dst[k] = a; }
          return;
        }
        if (v && typeof v === "object" && !Array.isArray(v)) { dst[k] = (dst[k] && typeof dst[k] === "object") ? dst[k] : {}; walk(dst[k], v); return; }
        dst[k] = v;
      });
    })(out, patch);
    return out;
  }
  function expandDots(patch) {
    var out = {};
    Object.keys(patch || {}).forEach(function (k) {
      if (k.indexOf(".") < 0) { out[k] = patch[k]; return; }
      var parts = k.split("."), cur = out;
      for (var i = 0; i < parts.length - 1; i++) { cur[parts[i]] = cur[parts[i]] || {}; cur = cur[parts[i]]; }
      cur[parts[parts.length - 1]] = patch[k];
    });
    return out;
  }
  function snap(p) {
    var d = STORE[p];
    return { exists: d !== undefined, id: p.split("/").pop(), data: function () { return d ? clone(d) : undefined; }, ref: docRef(p) };
  }
  function docRef(p) {
    return {
      id: p.split("/").pop(), path: p,
      get: function () { return Promise.resolve(snap(p)); },
      set: function (d, o) { STORE[p] = (o && o.merge) ? applyOps(STORE[p], expandDots(d)) : applyOps(null, expandDots(d)); return Promise.resolve(); },
      update: function (d) { STORE[p] = applyOps(STORE[p] || {}, expandDots(d)); return Promise.resolve(); },
      delete: function () { delete STORE[p]; return Promise.resolve(); },
      collection: function (n) { return collRef(p + "/" + n); },
      onSnapshot: function (cb) { try { cb({ docs: [], empty: true, forEach: function () {} }); } catch (_) {} return function () {}; },
    };
  }
  function query(base, filters, lim, order) {
    return {
      where: function (f, op, v) { return query(base, filters.concat([[f, op, v]]), lim, order); },
      limit: function (n) { return query(base, filters, n, order); },
      orderBy: function (f, d) { return query(base, filters, lim, [f, d]); },
      onSnapshot: function (cb) { try { cb({ docs: [], empty: true, size: 0, docChanges: function () { return []; }, forEach: function () {} }); } catch (_) {} return function () {}; },
      get: function () {
        var docs = Object.keys(STORE)
          .filter(function (p) { return p.indexOf(base + "/") === 0 && p.slice(base.length + 1).indexOf("/") < 0; })
          .map(function (p) { return snap(p); })
          .filter(function (s) {
            return filters.every(function (f) {
              var parts = f[0].split("."), v = s.data();
              for (var i = 0; i < parts.length && v != null; i++) v = v[parts[i]];
              return v === f[2];
            });
          });
        if (lim != null) docs = docs.slice(0, lim);
        return Promise.resolve({ docs: docs, empty: docs.length === 0, size: docs.length, forEach: function (fn) { docs.forEach(fn); } });
      },
    };
  }
  function collRef(base) {
    var c = query(base, [], null, null);
    c.doc = function (id) { return docRef(base + "/" + (id || ("auto" + Math.random().toString(36).slice(2)))); };
    c.add = function (d) { var r = c.doc(); return r.set(d).then(function () { return r; }); };
    return c;
  }
  var _user = null, _cbs = [];
  function fire() { _cbs.forEach(function (cb) { try { cb(_user); } catch (_) {} }); }
  var auth = {
    get currentUser() { return _user; },
    setPersistence: function () { return Promise.resolve(); },
    onAuthStateChanged: function (cb) { _cbs.push(cb); setTimeout(function () { try { cb(_user); } catch (_) {} }, 0); return function () {}; },
    signOut: function () { _user = null; setTimeout(fire, 0); return Promise.resolve(); },
    getRedirectResult: function () { return Promise.resolve({ user: null }); },
    signInWithPopup: function () { return Promise.resolve({ user: _user }); },
    signInWithRedirect: function () { return Promise.resolve(); },
  };
  window.__stubSignIn = function (u) { _user = u; fire(); };
  var db = {
    settings: function () {}, collection: collRef,
    batch: function () { var ops = []; return {
      set: function (r, d, o) { ops.push(function () { return r.set(d, o); }); return this; },
      update: function (r, d) { ops.push(function () { return r.update(d); }); return this; },
      delete: function (r) { ops.push(function () { return r.delete(); }); return this; },
      commit: function () { return Promise.all(ops.map(function (f) { return f(); })); } }; },
    runTransaction: function (fn) { return fn({
      get: function (r) { return r.get(); },
      set: function (r, d, o) { r.set(d, o); return this; },
      update: function (r, d) { r.update(d); return this; },
      delete: function (r) { r.delete(); return this; } }); },
  };
  var fbAuth = function () { return auth; };
  fbAuth.Auth = { Persistence: { SESSION: "session", LOCAL: "local", NONE: "none" } };
  fbAuth.GoogleAuthProvider = function () { this.setCustomParameters = function () {}; };
  var fbStore = function () { return db; };
  fbStore.FieldValue = {
    increment: function (n) { return { __op: "inc", n: n }; },
    serverTimestamp: function () { return { __op: "ts" }; },
    delete: function () { return { __op: "del" }; },
    arrayUnion: function () { return { __op: "union", vals: Array.prototype.slice.call(arguments) }; },
    arrayRemove: function () { return { __op: "union", vals: [] }; },
  };
  fbStore.Timestamp = {
    now: function () { return { toDate: function () { return new Date(); }, toMillis: function () { return Date.now(); } }; },
    fromDate: function (d) { return { toDate: function () { return d; }, toMillis: function () { return d.getTime(); } }; },
  };
  window.firebase = { initializeApp: function () {}, apps: [], auth: fbAuth, firestore: fbStore };
})();`;

// An account with a life behind it: hours, games, history, three achievements
// and two critters. Every one of these is something the guest must NOT see.
const SEED = `
<script>
window.__STUB_DOCS = {
  "users/ACC-UID-1": {
    nickname: "RealAccount", nickname_lower: "realaccount", friend_code: "4242",
    email: "real@example.com", avatar_url: "/avatars/great-white-shark.png",
    unlocked_icons: ["/avatars/great-white-shark.png", "/avatars/orca.png"],
    stats: {
      completed_games: 77, hours_played: 42, total_score: 9100, normal_wins: 31,
      total_xp: 250000, highest_score: 310, highest_score_normal: 310,
      normal_games_by_size: { "4": 60, "6": 17 },
      normal_wins_by_size: { "4": 25, "6": 6 },
      total_score_by_size: { "4": 7000, "6": 2100 },
      normal_playtime_by_size: { "4": 30, "6": 12 },
      competitive_wins: 9, competitive_losses: 4, competitive_draws: 1,
      comp_cp: 380, comp_season_id: "S-OLD", highest_score_competitive: 240,
      recent_games: [
        { s: 310, r: 1, pc: 4, mode: "normal", t: 1750000000000, name: "Ocean Run" },
        { s: 220, r: 2, pc: 6, mode: "normal", t: 1750000100000, name: "Kelp Cup" }
      ],
      critter_coins: 5000, daily_streak: 12
    },
    achievements: {
      cast_off:      { completed: true, unlockedAt: 1750000000000 },
      first_catch:   { completed: true, unlockedAt: 1750000000000 },
      ranked_waters: { completed: true, unlockedAt: 1750000000000 }
    }
  }
};
</script>
<script>${STUB}</script>`;

const TABS = ["overview", "history", "achievements", "competitive"];
const DRIVER = `
<script>
(function () {
  var out = { errors: [], account: {}, guest: {}, phase: "boot" };
  window.addEventListener("error", function (e) { out.errors.push("onerror: " + (e && e.message)); });
  window.addEventListener("unhandledrejection", function (e) {
    var r = e && e.reason; out.errors.push("unhandled: " + ((r && r.message) || String(r)));
  });
  function q(s) { return document.querySelector(s); }
  function vis(el) { return !!(el && el.offsetParent !== null); }
  function click(el) { el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window })); }
  function mark() { var d = document.getElementById("out"); if (!d) { d = document.createElement("div"); d.id = "out"; document.body.appendChild(d); } return d; }
  function finish() { mark().textContent = JSON.stringify(out); }
  function panelText(n) { var p = document.getElementById("ph-panel-" + n); return p ? (p.innerText || "").replace(/\\s+/g, " ").trim() : ""; }

  var phase = 1, tick = 0, tabIdx = 0, guard = 0, bag = null;
  var iv = setInterval(function () {
    tick++; out.phase = "p" + phase + ":t" + tabIdx + ":g" + guard;
    if (tick > 3000) { out.phase = "timeout:" + out.phase; finish(); clearInterval(iv); return; }
    try {
      if (phase === 1) {
        if (tick < 8) return;
        window.__stubSignIn({ uid: "ACC-UID-1", email: "real@example.com", displayName: "Real Account",
                              providerData: [{ providerId: "google.com" }] });
        phase = 2; return;
      }
      if (phase === 2) {
        var lob = q("#auth-stats-lobby");
        if (lob && lob.classList.contains("visible")) { phase = 3; guard = 0; tabIdx = 0; bag = out.account; }
        else if (++guard > 400) { out.errors.push("account lobby never appeared"); finish(); clearInterval(iv); }
        return;
      }
      if (phase === 3 || phase === 7) {
        var name = ${JSON.stringify(TABS)}[tabIdx];
        if (!name) { phase = (phase === 3) ? 4 : 8; guard = 0; return; }
        var btn = document.querySelector('.ph-snav-item[data-tab="' + name + '"]');
        if (!btn) { bag[name] = "NO BTN"; tabIdx++; return; }
        if (guard === 0) { click(btn); guard = 1; return; }
        guard++; if (guard < 26) return;
        bag[name] = panelText(name);
        tabIdx++; guard = 0; return;
      }
      if (phase === 4) {
        bag.myAvatar = window.__fishMyAvatarUrl();
        var so = q("#stats-signout-btn") || q("#auth-signout-btn");
        if (!so) { out.errors.push("no sign-out button"); finish(); clearInterval(iv); return; }
        click(so); phase = 5; guard = 0; return;
      }
      if (phase === 5) {
        if (++guard < 12) return;
        var g = q("#auth-guest-btn");
        if (g && vis(g)) { click(g); phase = 6; guard = 0; }
        else if (guard > 120) { out.errors.push("PLAY AS GUEST never appeared"); finish(); clearInterval(iv); }
        return;
      }
      if (phase === 6) {
        var go = q("#auth-guest-go-btn"), nk = q("#auth-guest-nick");
        if (go && vis(go)) {
          if (nk) { nk.value = "FreshGuest"; nk.dispatchEvent(new Event("input", { bubbles: true })); }
          click(go); phase = 6.5; guard = 0;
        }
        return;
      }
      if (phase === 6.5) {
        var lob2 = q("#auth-stats-lobby");
        if (lob2 && lob2.classList.contains("visible")) { phase = 7; guard = 0; tabIdx = 0; bag = out.guest; }
        else if (++guard > 200) { out.errors.push("guest lobby never appeared"); finish(); clearInterval(iv); }
        return;
      }
      // ── The guest equips a critter, through the real gallery ──────────
      if (phase === 8) {
        out.guest.avatarBefore = window.__fishMyAvatarUrl();
        var av = q("#stats-avatar");
        out.guest.avatarClickable = !!(av && av.classList.contains("ph-avatar-clickable"));
        if (!av) { out.errors.push("no profile avatar"); phase = 10; return; }
        click(av); phase = 8.5; guard = 0; return;
      }
      if (phase === 8.5) {
        if (++guard < 14) return;
        var tile = document.querySelector('[data-avatar-id="blue-tang"]')
                || document.querySelector('[data-avatar-id]:not(.gal-locked)');
        if (!tile) { out.errors.push("gallery had no wearable tile"); phase = 10; return; }
        out.guest.pickedId = tile.getAttribute("data-avatar-id");
        click(tile); phase = 8.7; guard = 0; return;
      }
      if (phase === 8.7) {
        if (++guard < 10) return;
        var yes = q("#gal-equip-yes");
        if (!yes) { out.errors.push("no equip button for a guest"); phase = 10; return; }
        click(yes); phase = 9; guard = 0; return;
      }
      if (phase === 9) {
        if (++guard < 16) return;
        out.guest.avatarAfter = window.__fishMyAvatarUrl();
        out.guest.cardImg = (q("#stats-avatar-img") || {}).src || "";
        phase = 10; return;
      }
      if (phase === 10) {
        out.guest.nick = (q("#stats-lobby-nick") || {}).textContent || "";
        out.guest.hdrXp = (q("#hdr-total-xp") || {}).textContent || "";
        out.guest.hdrCoins = (q("#hdr-coins") || {}).textContent || "";
        out.phase = "done"; finish(); clearInterval(iv);
      }
    } catch (e) { out.errors.push("driver: " + (e && e.message)); out.phase = "threw"; finish(); clearInterval(iv); }
  }, 60);
})();
</script>`;

const PORT = 8560 + (process.pid % 300);
const SERVER_SRC = `
  const fs=require("fs"),path=require("path"),http=require("http");
  const ROOT=${JSON.stringify(CLIENT)};
  const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",".json":"application/json",
    ".png":"image/png",".jpg":"image/jpeg",".webp":"image/webp",".svg":"image/svg+xml",".ico":"image/x-icon"};
  http.createServer((req,res)=>{
    const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
    const f=path.join(ROOT,rel);
    if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);res.end();return;}
    res.writeHead(200,{"Content-Type":MIME[path.extname(f)]||"application/octet-stream"});
    fs.createReadStream(f).pipe(res);
  }).listen(${PORT});
`;

const page = HTML
  .replace(/<script[^>]*src="https:\/\/www\.gstatic\.com\/firebasejs[^"]*"[^>]*><\/script>/g, "")
  .replace(/<script[^>]*src="\/firebase-config\.js"[^>]*><\/script>/g, "")
  .replace("</head>", SEED + "</head>") + DRIVER;

const pageFile = path.join(CLIENT, "_guest_identity_drive.html");
fs.writeFileSync(pageFile, page);
const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });
try { execFileSync(process.execPath, ["-e", "setTimeout(()=>{},700)"]); } catch (_) {}

let D = null;
try {
  for (let attempt = 0; attempt < 2 && !D; attempt++) {
    const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      "--window-size=1440,900", "--virtual-time-budget=300000", "--dump-dom",
      `http://localhost:${PORT}/_guest_identity_drive.html?game_window=1`],
      { encoding: "utf8", maxBuffer: 64e6, stdio: ["ignore", "pipe", "ignore"] });
    const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
    if (!m) continue;
    const raw = m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                    .replace(/&lt;/g, "<").replace(/&gt;/g, ">");
    try { D = JSON.parse(raw); } catch (_) {}
  }
} finally {
  try { server.kill(); } catch (_) {}
  if (!process.env.CC_KEEP_TMP) { try { fs.unlinkSync(pageFile); } catch (_) {} }
}

console.log("\nan account signs in, signs out, and a guest sits down (real browser)");
if (!D) {
  check("the walkthrough produced a result", false, "no result");
} else {
  const A = D.account || {}, G = D.guest || {};
  check("it ran to the end", D.phase === "done", D.phase);
  check("nothing threw", (D.errors || []).length === 0, (D.errors || []).join(" | "));

  console.log("\n  the account really was loaded first (or the rest proves nothing)");
  check("its hours are on screen", /Hours Played 42 hrs/.test(A.overview || ""), (A.overview || "").slice(0, 90));
  check("its games are on screen", /Total Games 77/.test(A.overview || ""));
  check("its achievements are on screen", /3 \/ 59 Completed/.test(A.achievements || ""));
  check("its history is on screen", /310 pts/.test(A.history || ""));
  check("its critter is on its face", A.myAvatar === "/avatars/great-white-shark.png", A.myAvatar);

  console.log("\n  and none of it followed the guest in");
  check("the guest's hours are their own", /Hours Played 0 hrs/.test(G.overview || ""), (G.overview || "").slice(0, 140));
  check("no 42 hours anywhere on their Player Home", !/42 hrs/.test(G.overview || ""));
  check("their games start at zero", /Total Games 0/.test(G.overview || ""));
  check("no 77 games", !/Total Games 77/.test(G.overview || ""));
  check("their wins start at zero", /Total Wins 0/.test(G.overview || ""));
  check("their achievements start at zero", /0 \/ 59 Completed/.test(G.achievements || ""));
  check("nothing is marked unlocked", !/✓ Unlocked/.test(G.achievements || ""), (G.achievements || "").slice(0, 120));
  check("their game history is empty", /No games completed yet\./.test(G.history || ""));
  check("the account's two games are not in it", !/310 pts/.test(G.history || ""));
  check("no competitive matches either", /No competitive games yet\./.test(G.history || ""));
  check("their name is their own", (G.nick || "").trim() === "FreshGuest", G.nick);
  check("the header XP chip is not the account's", !/249|250/.test(G.hdrXp || ""), G.hdrXp);
  check("nor the coin chip", (G.hdrCoins || "").trim() === "0", G.hdrCoins);

  console.log("\n  a fresh guest has EARNED nothing, whatever they may wear");
  check("no wall of unlocked critters", !/Animals Unlocked (1[0-9]|[2-9][0-9]) \//.test(G.overview || ""),
        (/Animals Unlocked [^🏅]*/.exec(G.overview || "") || [""])[0]);

  console.log("\n  the Competitive tab has no stray numbers on it");
  const compTail = (G.competitive || "").replace(/.*to earn CP\./, "").trim();
  check("nothing follows the empty state", compTail === "", compTail.slice(0, 60));
  const acctTail = (A.competitive || "").replace(/.*to earn CP\./, "").trim();
  check("nor on the account's", acctTail === "", acctTail.slice(0, 60));

  console.log("\n  the critter a guest equips is the critter they wear");
  check("the profile avatar opens the gallery for them", G.avatarClickable === true);
  check("the gallery offers them something to equip", !!G.pickedId, G.pickedId);
  check("equipping changes the face the table sees",
        !!G.avatarAfter && G.avatarAfter !== G.avatarBefore, `${G.avatarBefore} → ${G.avatarAfter}`);
  check("it is the critter they picked",
        !!G.pickedId && String(G.avatarAfter || "").indexOf(G.pickedId) >= 0, `${G.pickedId} vs ${G.avatarAfter}`);
  check("and the profile card is wearing it too",
        !!G.avatarAfter && String(G.cardImg || "").indexOf(G.avatarAfter) >= 0, G.cardImg);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
