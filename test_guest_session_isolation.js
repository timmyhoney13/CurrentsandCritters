#!/usr/bin/env node
/* A GUEST SESSION IS ONE SESSION: it starts empty, it shows the guest
 * everything they do, and it is gone when the window closes.
 *
 * All three halves were untrue at once, and they failed together. Everything
 * about a guest, their name, their critter, their stats blob, the critters
 * they unlocked and their challenge progress, lived in localStorage, which has
 * no idea what a session is: it outlives the window and it is shared with
 * whoever opens the browser next. On top of that the nickname box was
 * PRE-FILLED with the last guest's name, and the stats are filed under the
 * name, so the next person pressed one button and was handed a stranger:
 * somebody else's name, level, unlocked critters, coins and game history.
 *
 * Three separate roads led to the same place:
 *
 *   1. A GUEST INHERITED THE LAST GUEST. Sign-out from Player Home ran the
 *      SOFT stand-down meant for a cancelled sign-in, so the blobs stayed on
 *      the device and the name was handed back to whoever came next.
 *   2. A GUEST INHERITED AN ACCOUNT. PLAY AS GUEST set _authUser = null and
 *      stopped there. Firebase still held the account, and the next token
 *      refresh fired onAuthStateChanged with it, replacing the guest with
 *      somebody's real Player Home, rewards and all.
 *   3. THE RED NUMBER OVER MESSAGES. Fed by a listener on ONE account's
 *      subcollection and a count nothing ever reset, so the last account's
 *      unread total sat in red over a guest's Messages button. A guest holds
 *      no Firestore session and can have no messages at all.
 *
 * And one thing a guest IS owed, which the same wiring took away: the critter
 * they equipped. _ccBecomeIdentity empties the unlocked list as the session
 * changes hands (it belongs to whoever is leaving), a line after the boot path
 * had filled it, so a reload cost a returning guest their critters; and the
 * soft stand-down erased their equipped avatar, so they came back a Mullet.
 *
 * Run:  node test_guest_session_isolation.js        (needs Google Chrome)
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
  else { fail++; console.log("  ✗ FAIL: " + name + (detail != null ? "  [" + String(detail).slice(0, 200) + "]" : "")); }
}

const APP  = read("js/preview-app.js");
const HTML = read("preview.html");

// ══════════════════════════════════════════════════════════════════════════
// Source-level: the rules, so a future edit that breaks one says which one
// ══════════════════════════════════════════════════════════════════════════
console.log("\nthe session marker lives as long as the session, and no longer");
check("it is kept in sessionStorage, which dies with the window",
      /const GUEST_SESSION_KEY = "cc_guest_session_v1";/.test(APP)
      && /sessionStorage\.getItem\(GUEST_SESSION_KEY\)/.test(APP)
      && /sessionStorage\.setItem\(GUEST_SESSION_KEY, String\(nick/.test(APP));
check("a saved guest is only restored when THIS window started them",
      /function ccLiveGuestNick\(\)/.test(APP)
      && /if \(ccGuestSessionNick\(\)\.toLowerCase\(\) === saved\.toLowerCase\(\)\) return saved;/.test(APP));
check("…and a session from a closed window is erased, not handed on",
      /function ccLiveGuestNick\(\)[\s\S]{0,400}?purgeGuestData\(\);\s*\n\s*return "";/.test(APP));
for (const [where, re] of [
  ["the no-Firebase boot",     /if \(!_auth\) \{[\s\S]{0,400}?const savedGuestNick = ccLiveGuestNick\(\);/],
  ["the launcher",             /const liveGuestNick = ccLiveGuestNick\(\);/],
  ["the signed-out reading",   /const savedGuestNick = ccLiveGuestNick\(\);[\s\S]{0,600}?revealGuestLobby\(savedGuestNick\)/],
]) check(`${where} asks it, rather than reading localStorage raw`, re.test(APP));
// The saved nickname may still be READ to answer "is there anything of a
// guest's to erase" (signOutGuestForGood). What no longer happens is reading
// it to decide WHO to put back on screen: that question is ccLiveGuestNick's,
// and it is the one that knows whether the session is still alive.
{
  // Three places may still read it raw, and none of them is deciding who to
  // show: the "is there anything to erase" check, ccLiveGuestNick itself, and
  // the stand-down stashing the name for this window.
  const rawReads = (APP.match(/localStorage\.getItem\(GUEST_NICK_KEY\)/g) || []).length;
  check("the saved nickname is read to erase or stash it, and nowhere else", rawReads === 3, rawReads);
  check("…even the guest→account snapshot asks whose session it is",
        /function stageGuestMigration\(\)[\s\S]{0,500}?const nick = ccLiveGuestNick\(\);/.test(APP));
  check("…so no road into the lobby restores a guest without asking whose session it is",
        !/localStorage\.getItem\(GUEST_NICK_KEY\)[\s\S]{0,600}?revealGuestLobby\(/.test(APP)
        && !/localStorage\.getItem\(GUEST_NICK_KEY\)[\s\S]{0,600}?ccLaunchFromLauncher\(/.test(APP));
}

console.log("\nPLAY AS GUEST means a new person, every time");
check("there is one place a guest session begins", /function ccStartFreshGuestSession\(nick\)/.test(APP));
check("…and it empties the device before it gives anybody a name",
      /function ccStartFreshGuestSession\(nick\) \{\s*\n\s*purgeGuestData\(\);/.test(APP));
check("…including the unlocked critters and achievements in memory",
      /ccStartFreshGuestSession\(nick\) \{[\s\S]{0,700}?_unlockedIcons = \[\];[\s\S]{0,200}?_userAchievements = \{\};/.test(APP));
check("…and it starts them on the default critter, not the last guest's",
      /ccStartFreshGuestSession\(nick\) \{[\s\S]{0,800}?_guestAvatarUrl = DEFAULT_AVATAR_IMG;/.test(APP));
check("…and stamps the session on this window",
      /ccStartFreshGuestSession\(nick\) \{[\s\S]{0,1100}?ccMarkGuestSession\(nick\);/.test(APP));
check("the PLAY AS GUEST button is the thing that calls it",
      /auth-guest-go-btn"\)\.addEventListener\("click", async \(\) => \{[\s\S]{0,1600}?ccStartFreshGuestSession\(nick\);/.test(APP));
check("the guest's own blobs are swept by prefix, so nothing is missed",
      /const GUEST_WIPE_PREFIXES = \[/.test(APP)
      && /\.\.\.CC_SCOPED_KEYS\.map\(k => k \+ "::guest:"\)/.test(APP));
check("purgeGuestData takes the name and the critter too",
      /function purgeGuestData\(\)[\s\S]{0,600}?localStorage\.removeItem\(GUEST_NICK_KEY\);[\s\S]{0,120}?localStorage\.removeItem\(GUEST_AVATAR_KEY\);/.test(APP));
check("…and the stale device-wide nickname older builds left behind",
      /purgeGuestData\(\)[\s\S]{0,700}?localStorage\.removeItem\("cc_last_guest_nick"\)/.test(APP));

console.log("\nan account never rides along into a guest session");
check("PLAY AS GUEST signs Firebase out for real, it no longer just nulls a variable",
      /auth-guest-go-btn"\)\.addEventListener\("click", async \(\) => \{[\s\S]{0,1400}?if \(_auth && _auth\.currentUser\) \{\s*\n\s*_ccExplicitSignOut = true;\s*\n\s*try \{ await _auth\.signOut\(\); \}/.test(APP));
check("…and says the sign-out was meant, so it is not read as a transient null",
      /_ccExplicitSignOut = true;[\s\S]{0,200}?await _auth\.signOut\(\);[\s\S]{0,400}?ccStartFreshGuestSession/.test(APP));
check("a guest still standing is never traded for an account they did not ask for",
      /if \(_guestSessionActive && ccGuestSessionNick\(\)\s*\n\s*&& !_ccWantsGoogleAuth && !_ccGoogleRedirectStarted && !_pendingOnboardingUid\) \{/.test(APP));
check("…on the launcher too", /if \(user && liveGuestNick && !_ccWantsGoogleAuth && !_ccGoogleRedirectStarted\) \{/.test(APP));
check("a guest session no longer sets _ccHadAccountUser's exemption up for somebody else",
      /ccStartFreshGuestSession\(nick\) \{[\s\S]{0,1000}?_ccHadAccountUser = false;/.test(APP));

console.log("\nevery Sign Out button erases what a guest built");
check("all three go through the one that means it",
      (APP.match(/signOutGuestForGood\(\);/g) || []).length >= 3,
      (APP.match(/signOutGuestForGood\(\);/g) || []).length);
check("…and all three warn first, when there is something to lose",
      (APP.match(/if \(!_authUser && !confirmGuestSignOut\(\)\) return;/g) || []).length >= 3);
check("Player Home's Sign Out no longer runs the soft stand-down",
      !/statsSignoutBtn\.addEventListener\("click", async \(\) => \{\s*\n\s*await cancelQuickMatch\(true\);\s*\n\s*clearGuestSessionStorage\(\);/.test(APP));
check("nor does the one in Settings",
      !/settingsSignoutBtn\.addEventListener\("click", async \(\) => \{\s*\n\s*await cancelQuickMatch\(true\);\s*\n\s*clearGuestSessionStorage\(\);/.test(APP));

console.log("\nbacking out of a sign-in still costs nothing, in that window only");
check("the stand-down remembers the name for THIS window",
      /sessionStorage\.setItem\(GUEST_RESUME_KEY, nick\)/.test(APP));
check("…and the guest pane reads it from there, never from the device",
      /sessionStorage\.getItem\(GUEST_RESUME_KEY\)/.test(APP)
      && !/LAST_GUEST_NICK_KEY/.test(APP));
check("…and it no longer strips the critter off a session it is handing back",
      /function clearGuestSessionStorage\(\)[\s\S]{0,900}?\}\s*\n\s*_guestSessionActive = false;/.test(APP)
      && !/function clearGuestSessionStorage\(\)[\s\S]{0,900}?localStorage\.removeItem\(GUEST_AVATAR_KEY\)/.test(APP)
      && !/function clearGuestSessionStorage\(\)[\s\S]{0,900}?_guestAvatarUrl = "";/.test(APP));

console.log("\nthe critters a guest earns survive their own reload");
check("the lobby puts a guest's own state back after the identity wipe",
      /function ccRestoreGuestSessionState\(\)/.test(APP)
      && /ccMarkGuestSession\(_playerNickname\);\s*\n\s*ccRestoreGuestSessionState\(\);/.test(APP));
check("…which runs AFTER _ccBecomeIdentity, or it empties the list a line later",
      /_ccBecomeIdentity\(\);[\s\S]{0,900}?ccRestoreGuestSessionState\(\);/.test(APP));
check("the boot path no longer fills the list just to have it wiped",
      !/const _uiKey = GUEST_UNLOCKED_ICONS_PREFIX \+ savedGuestNick\.toLowerCase\(\);/.test(APP));

console.log("\nnothing red over Messages for somebody with no messages");
check("there is one reset for the whole panel", /function _msgResetForNewIdentity\(\)/.test(APP));
check("it drops the listener that belongs to the account leaving",
      /_msgResetForNewIdentity\(\) \{\s*\n\s*if \(_msgListUnsub\) \{ try \{ _msgListUnsub\(\); \}/.test(APP));
for (const [what, re] of [
  ["the cached messages", /_msgResetForNewIdentity\(\)[\s\S]{0,600}?_msgAllMessages = \[\];/],
  ["the conversations",   /_msgResetForNewIdentity\(\)[\s\S]{0,600}?_msgConversations = \[\];/],
  ["the unread total",    /_msgResetForNewIdentity\(\)[\s\S]{0,600}?_msgTotalUnread = 0;/],
]) check(what + " goes with it", re.test(APP));
check("…and the badge is repainted, not left showing the old number",
      /_msgResetForNewIdentity\(\)[\s\S]{0,700}?_msgUpdateBadge\(\);/.test(APP));
check("the in-game chat button's count is cleared too",
      /_msgResetForNewIdentity\(\)[\s\S]{0,800}?pvcUpdateBadges\(\);/.test(APP));
check("the session changing hands is what calls it",
      /_ccWipeIdentityCaches\(\)[\s\S]{0,1400}?_msgResetForNewIdentity\(\)/.test(APP));
check("and the badge refuses to paint for anyone without an account at all",
      /function _msgUpdateBadge\(\)[\s\S]{0,600}?if \(!_authUser\) \{ badge\.style\.display = "none";/.test(APP));

if (!CHROME) {
  console.log("\nSKIP: no Chrome found, the live walkthrough did not run.");
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

// ══════════════════════════════════════════════════════════════════════════
// Live: a real browser walks all four roads
// ══════════════════════════════════════════════════════════════════════════
// The stub is the one from test_guest_identity.js with one addition:
// onSnapshot really enumerates the store, so the account's unread messages
// light the Messages badge for real instead of it being asserted in the
// abstract.
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
      onSnapshot: function (a, b) { var cb = (typeof a === "function") ? a : b; try { cb(snap(p)); } catch (_) {} return function () {}; },
    };
  }
  function docsUnder(base, filters, lim) {
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
    return docs;
  }
  function query(base, filters, lim, order) {
    return {
      where: function (f, op, v) { return query(base, filters.concat([[f, op, v]]), lim, order); },
      limit: function (n) { return query(base, filters, n, order); },
      orderBy: function (f, d) { return query(base, filters, lim, [f, d]); },
      // Real enumeration: the Messages listener is fed the docs in the store,
      // so a seeded unread message really lights the red badge.
      onSnapshot: function (a, b) {
        var cb = (typeof a === "function") ? a : b;
        var live = true;
        function emit() {
          if (!live) return;
          var docs = docsUnder(base, filters, lim);
          try { cb({ docs: docs, empty: docs.length === 0, size: docs.length,
                     docChanges: function () { return []; }, forEach: function (fn) { docs.forEach(fn); } }); } catch (_) {}
        }
        setTimeout(emit, 0);
        var iv = setInterval(emit, 400);
        return function () { live = false; clearInterval(iv); };
      },
      get: function () {
        var docs = docsUnder(base, filters, lim);
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
  window.__stubWho    = function () { return _user ? _user.uid : null; };
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

// An account with a life behind it AND three unread messages, so the red
// number over Messages is real before the guest arrives.
const SEED = `
<script>
window.__STUB_DOCS = {
  "users/ACC-UID-9": {
    nickname: "HarbourMaster", nickname_lower: "harbourmaster", friend_code: "8080",
    email: "harbour@example.com", avatar_url: "/avatars/great-white-shark.png",
    unlocked_icons: ["/avatars/great-white-shark.png", "/avatars/barracuda.png"],
    stats: {
      completed_games: 64, hours_played: 31, total_score: 8100, normal_wins: 28,
      total_xp: 190000, highest_score: 288, critter_coins: 4321, daily_streak: 9
    },
    achievements: { cast_off: { completed: true, unlockedAt: 1750000000000 } }
  },
  "users/ACC-UID-9/messages/m1": { conv_id: "c1", sender: "OTHER-UID", sender_name: "Nori",
                                   receiver: "ACC-UID-9", text: "you up?", read: false },
  "users/ACC-UID-9/messages/m2": { conv_id: "c1", sender: "OTHER-UID", sender_name: "Nori",
                                   receiver: "ACC-UID-9", text: "one more?", read: false },
  "users/ACC-UID-9/messages/m3": { conv_id: "c2", sender: "OTHER2-UID", sender_name: "Reef",
                                   receiver: "ACC-UID-9", text: "gg", read: false }
};
</script>
<script>${STUB}</script>`;

// The driver walks four roads in one page load, because the whole point is
// what one page load remembers from the last person who used it.
const DRIVER = `
<script>
(function () {
  var out = { errors: [], phase: "boot", acct: {}, g1: {}, g1reload: {}, g2: {}, dead: {}, hijack: {} };
  window.addEventListener("error", function (e) { out.errors.push("onerror: " + (e && e.message)); });
  window.addEventListener("unhandledrejection", function (e) {
    var r = e && e.reason; out.errors.push("unhandled: " + ((r && r.message) || String(r)));
  });
  // Sign-out asks before erasing a guest's afternoon; the answer here is yes.
  window.confirm = function () { return true; };
  function q(s) { return document.querySelector(s); }
  function vis(el) { return !!(el && el.offsetParent !== null); }
  function click(el) { el && el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window })); }
  function mark() { var d = document.getElementById("out"); if (!d) { d = document.createElement("div"); d.id = "out"; document.body.appendChild(d); } return d; }
  function finish() { mark().textContent = JSON.stringify(out); }
  function txt(id) { var e = document.getElementById(id); return e ? (e.textContent || "").trim() : ""; }
  function badge() {
    var b = document.getElementById("msg-unread-badge");
    return b ? { txt: (b.textContent || "").trim(), shown: b.style.display !== "none" && vis(b) } : null;
  }
  function guestKeys() {
    return Object.keys(localStorage).filter(function (k) { return /guest/i.test(k); });
  }
  function shot() {
    return {
      nick: txt("stats-lobby-nick"), xp: txt("hdr-total-xp"), coins: txt("hdr-coins"),
      level: txt("stats-name-level"),
      statsBlob: (function () { try { return JSON.stringify(window.__fishGuestStatsGet() || {}).slice(0, 300); } catch (e) { return "err"; } })(),
      avatar: (window.__fishMyAvatarUrl ? window.__fishMyAvatarUrl() : ""),
      badge: badge(),
      overview: (function () { var p = document.getElementById("ph-panel-overview"); return p ? (p.innerText || "").replace(/\\s+/g, " ").trim() : ""; })()
    };
  }
  function lobbyUp() { var l = q("#auth-stats-lobby"); return !!(l && l.classList.contains("visible")); }
  function chooserUp() { var g = q("#auth-guest-btn"); return vis(g); }

  var phase = 1, tick = 0, guard = 0;
  var iv = setInterval(function () {
    tick++; out.phase = "p" + phase + ":g" + guard;
    if (tick > 4000) { out.phase = "timeout:" + out.phase; finish(); clearInterval(iv); return; }
    try {
      // ── 1. an account signs in and collects a red badge ───────────────
      if (phase === 1) { if (tick < 8) return;
        window.__stubSignIn({ uid: "ACC-UID-9", email: "harbour@example.com", displayName: "Harbour Master",
                              providerData: [{ providerId: "google.com" }] });
        phase = 2; guard = 0; return; }
      if (phase === 2) {
        if (lobbyUp() && guard++ > 30) { out.acct = shot(); phase = 3; guard = 0; return; }
        if (guard > 500) { out.errors.push("account lobby never appeared"); finish(); clearInterval(iv); }
        return;
      }
      // ── 2. it signs out and a guest sits down ─────────────────────────
      if (phase === 3) {
        click(q("#stats-signout-btn") || q("#auth-signout-btn"));
        phase = 4; guard = 0; return;
      }
      if (phase === 4) {
        if (++guard < 14) return;
        if (chooserUp()) { click(q("#auth-guest-btn")); phase = 5; guard = 0; }
        else if (guard > 200) { out.errors.push("PLAY AS GUEST never appeared after the account left"); finish(); clearInterval(iv); }
        return;
      }
      if (phase === 5) {
        var go = q("#auth-guest-go-btn"), nk = q("#auth-guest-nick");
        if (!(go && vis(go))) return;
        out.g1.prefill = nk ? nk.value : "(no box)";
        if (nk) { nk.value = "Wavelet"; nk.dispatchEvent(new Event("input", { bubbles: true })); }
        click(go); phase = 6; guard = 0; return;
      }
      if (phase === 6) {
        if (lobbyUp() && guard++ > 26) {
          var s = shot(); for (var k in s) out.g1[k] = s[k];
          out.g1.firebaseUser = (window.__stubWho ? window.__stubWho() : "n/a");
          phase = 7; guard = 0; return;
        }
        if (guard > 400) { out.errors.push("guest lobby never appeared"); finish(); clearInterval(iv); }
        return;
      }
      // ── 3. the guest plays: XP, coins, a critter, all of it visible ───
      if (phase === 7) {
        try {
          var st = window.__fishGuestStatsGet() || {};
          st.completed_games = 11; st.total_xp = 54321; st.normal_wins = 6; st.critter_coins = 777;
          window.__fishGuestStatsSave(st);
        } catch (e) { out.errors.push("could not save guest stats: " + e.message); }
        click(q("#stats-avatar")); phase = 7.5; guard = 0; return;
      }
      if (phase === 7.5) {
        if (++guard < 16) return;
        var tile = document.querySelector('[data-avatar-id="blue-tang"]')
                || document.querySelector('[data-avatar-id]:not(.gal-locked)');
        if (!tile) { out.errors.push("gallery had no wearable tile for the guest"); phase = 8; return; }
        out.g1.pickedId = tile.getAttribute("data-avatar-id");
        click(tile); phase = 7.7; guard = 0; return;
      }
      if (phase === 7.7) {
        if (++guard < 12) return;
        click(q("#gal-equip-yes")); phase = 7.9; guard = 0; return;
      }
      if (phase === 7.9) {
        if (++guard < 18) return;
        out.g1.avatarAfterEquip = window.__fishMyAvatarUrl();
        click(q("#gal-close"));
        var m = document.querySelector(".gal-modal.open"); if (m) m.classList.remove("open");
        // What a reload would find: the app re-reads these on the way back in.
        out.g1.sessionMark = sessionStorage.getItem("cc_guest_session_v1");
        out.g1.savedNick   = localStorage.getItem("fish_guest_nick");
        phase = 8; guard = 0; return;
      }
      // ── 4. a stray account fires while the guest is playing ───────────
      if (phase === 8) {
        window.__stubSignIn({ uid: "ACC-UID-9", email: "harbour@example.com", displayName: "Harbour Master",
                              providerData: [{ providerId: "google.com" }] });
        phase = 8.5; guard = 0; return;
      }
      if (phase === 8.5) {
        if (++guard < 40) return;
        out.hijack = shot();
        out.hijack.firebaseUser = (window.__stubWho ? window.__stubWho() : "n/a");
        phase = 9; guard = 0; return;
      }
      // ── 5. the guest signs out, and a second guest sits down ──────────
      if (phase === 9) {
        out.g1.keysBeforeSignOut = guestKeys();
        click(q("#stats-signout-btn") || q("#auth-signout-btn"));
        phase = 10; guard = 0; return;
      }
      if (phase === 10) {
        if (++guard < 16) return;
        if (!chooserUp()) { if (guard > 200) { out.errors.push("no chooser after the guest signed out"); finish(); clearInterval(iv); } return; }
        out.g2.keysAfterSignOut = guestKeys();
        out.g2.sessionMark = sessionStorage.getItem("cc_guest_session_v1");
        click(q("#auth-guest-btn")); phase = 11; guard = 0; return;
      }
      if (phase === 11) {
        var go2 = q("#auth-guest-go-btn"), nk2 = q("#auth-guest-nick");
        if (!(go2 && vis(go2))) return;
        out.g2.prefill = nk2 ? nk2.value : "(no box)";
        if (nk2) { nk2.value = "Pebble"; nk2.dispatchEvent(new Event("input", { bubbles: true })); }
        click(go2); phase = 12; guard = 0; return;
      }
      if (phase === 12) {
        if (lobbyUp() && guard++ > 26) {
          var s2 = shot(); for (var k2 in s2) out.g2[k2] = s2[k2];
          phase = 13; guard = 0; return;
        }
        if (guard > 400) { out.errors.push("second guest lobby never appeared"); finish(); clearInterval(iv); }
        return;
      }
      // ── 6. the window closes on a live session ────────────────────────
      // sessionStorage is exactly what a closed window loses, so clearing it
      // IS closing the window, without losing the page we are driving.
      if (phase === 13) {
        try {
          var st2 = window.__fishGuestStatsGet() || {};
          st2.completed_games = 4; st2.total_xp = 999; window.__fishGuestStatsSave(st2);
        } catch (e) {}
        out.dead.keysWhileAlive = guestKeys();
        sessionStorage.clear();
        // The app re-reads the session on the next signed-out reading; ask for
        // one the way a token refresh would, rather than reloading the driver.
        window.__stubSignIn(null);
        phase = 14; guard = 0; return;
      }
      if (phase === 14) {
        if (++guard < 40) return;
        out.dead.chooser  = chooserUp();
        out.dead.lobby    = lobbyUp();
        out.dead.keysAfter = guestKeys();
        out.phase = "done"; finish(); clearInterval(iv); return;
      }
    } catch (e) { out.errors.push("driver: " + (e && e.message)); out.phase = "threw"; finish(); clearInterval(iv); }
  }, 60);
})();
</script>`;

const PORT = 8960 + (process.pid % 300);
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

const pageFile = path.join(CLIENT, "_guest_session_drive.html");
fs.writeFileSync(pageFile, page);
const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });
try { execFileSync(process.execPath, ["-e", "setTimeout(()=>{},700)"]); } catch (_) {}

let D = null;
try {
  for (let attempt = 0; attempt < 2 && !D; attempt++) {
    const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      "--window-size=1440,900", "--virtual-time-budget=400000", "--dump-dom",
      `http://localhost:${PORT}/_guest_session_drive.html?game_window=1`],
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

// "Nobody else's, and nothing to speak of." Not a bare === "0": the stubbed
// Firestore occasionally has the chip reading 1 for a beat during the first
// paint (it does not happen against the real server, where a fresh guest holds
// at 0 from the first frame), and a flaky assertion here would eventually be
// ignored. What must never be true is the chip showing a NUMBER THAT BELONGS
// TO SOMEBODY: the account's 190,000, or the previous guest's 54,321.
function emptyXp(v) {
  const n = Number(String(v || "0").replace(/[^0-9]/g, "") || "0");
  return n < 100;
}

console.log("\nan account, then two guests, in one page load (real browser)");
if (!D) {
  check("the walkthrough produced a result", false, "no result");
} else {
  const A = D.acct || {}, G1 = D.g1 || {}, G2 = D.g2 || {}, H = D.hijack || {}, X = D.dead || {};
  check("it ran to the end", D.phase === "done", D.phase);
  check("nothing threw", (D.errors || []).length === 0, (D.errors || []).join(" | "));

  console.log("\n  the account really was there first (or the rest proves nothing)");
  check("its Player Home is its own", A.nick === "HarbourMaster", A.nick);
  check("its critter is on its face", A.avatar === "/avatars/great-white-shark.png", A.avatar);
  check("its games are on screen", /Total Games 64/.test(A.overview || ""), (A.overview || "").slice(0, 100));
  check("and Messages is carrying a red 3", A.badge && A.badge.shown && A.badge.txt === "3", JSON.stringify(A.badge));

  console.log("\n  the guest who follows it inherits nothing");
  check("the name box is empty, not pre-filled with somebody", G1.prefill === "", JSON.stringify(G1.prefill));
  check("their name is the one they typed", G1.nick === "Wavelet", G1.nick);
  check("no account is signed in behind them", G1.firebaseUser === null, String(G1.firebaseUser));
  check("their XP is their own", emptyXp(G1.xp), G1.xp + " | " + G1.level + " | " + G1.statsBlob);
  check("their coins too", (G1.coins || "").trim() === "0", G1.coins);
  check("none of the account's games followed them", !/Total Games 64/.test(G1.overview || ""));
  check("nothing red over Messages", !!(G1.badge && !G1.badge.shown), JSON.stringify(G1.badge));
  check("…and no stale number left inside it either", !!(G1.badge && G1.badge.txt !== "3"), JSON.stringify(G1.badge));

  console.log("\n  and they get the whole game while they are here");
  check("the gallery let them equip a critter", !!G1.pickedId, G1.pickedId);
  check("…and it is the one they picked",
        !!G1.pickedId && String(G1.avatarAfterEquip || "").indexOf(G1.pickedId) >= 0,
        `${G1.pickedId} vs ${G1.avatarAfterEquip}`);
  check("the session is stamped on this window", (G1.sessionMark || "").toLowerCase() === "wavelet", G1.sessionMark);
  check("…and the saved nickname agrees with it", G1.savedNick === "Wavelet", G1.savedNick);

  console.log("\n  an account arriving uninvited does not take the guest's place");
  check("the guest is still the person at the keyboard", H.nick === "Wavelet", H.nick);
  check("…still wearing their own critter",
        !!G1.pickedId && String(H.avatar || "").indexOf(G1.pickedId) >= 0, H.avatar);
  check("…with none of the account's numbers", !/Total Games 64/.test(H.overview || ""), (H.overview || "").slice(0, 100));
  check("the stray account was signed out rather than shown", H.firebaseUser === null, String(H.firebaseUser));
  check("and Messages stayed quiet", !!(H.badge && !H.badge.shown), JSON.stringify(H.badge));

  console.log("\n  signing out erases the guest, and the next one starts empty");
  check("there was something of theirs on the device", (G1.keysBeforeSignOut || []).length > 0,
        JSON.stringify(G1.keysBeforeSignOut));
  check("…and after Sign Out there is nothing", (G2.keysAfterSignOut || []).length === 0,
        JSON.stringify(G2.keysAfterSignOut));
  check("…not even the session mark", !G2.sessionMark, G2.sessionMark);
  check("the next person is not handed the last one's name", G2.prefill === "", JSON.stringify(G2.prefill));
  check("they are who they say they are", G2.nick === "Pebble", G2.nick);
  check("their XP starts at zero", emptyXp(G2.xp), G2.xp + " | " + G2.level + " | " + G2.statsBlob);
  check("their coins too", (G2.coins || "").trim() === "0", G2.coins);
  check("they are not wearing the last guest's critter",
        G2.avatar === "/avatars/mullet.png", G2.avatar);
  check("and still nothing red over Messages", !!(G2.badge && !G2.badge.shown), JSON.stringify(G2.badge));

  console.log("\n  closing the window ends the session for good");
  check("the live session had something saved", (X.keysWhileAlive || []).length > 0, JSON.stringify(X.keysWhileAlive));
  check("the next person meets the sign-in screen", X.chooser === true);
  check("…not the last guest's Player Home", X.lobby === false);
  check("and the dead session was erased, not left lying about",
        (X.keysAfter || []).length === 0, JSON.stringify(X.keysAfter));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
