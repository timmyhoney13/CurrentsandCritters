#!/usr/bin/env node
/* The Streak leaderboard: both boards, in a real browser.
 *
 * Run:  node test_streak_leaderboard.js        (needs Google Chrome installed)
 *
 * The Leaderboard tab gained a "🔥 Streak" mode with a two-way toggle:
 * LONGEST STREAK EVER and LONGEST CURRENT STREAK. The one thing that makes it
 * hard is that stats.daily_streak in a user doc is only rewritten when a game
 * FINISHES, a player who stopped playing a week ago keeps their old number
 * frozen in Firestore, so ranking by the stored field alone would park them at
 * the top of the "current" board forever. Every number here is therefore
 * recomputed from stats.streak_days (the single source of truth, the same list
 * the header chip, the week dots and the yearly calendar read).
 *
 * This lifts the REAL functions out of preview-app.js and the REAL section
 * markup out of preview.html, puts them in headless Chrome with the REAL
 * preview.css, and drives them against canned Firestore snapshots:
 *
 *   1. LONGEST board ranks by best-run-ever, CURRENT board by the live run.
 *   2. A stale stored daily_streak is corrected, not trusted, the lapsed
 *      player drops off the current board and keeps their spot on longest.
 *   3. A day covered by a Streak Shield keeps a run continuous.
 *   4. The toggle re-sorts AND swaps the two metric column headers, live.
 *   5. Ties share a rank number.
 *   6. Blank/guest accounts are filtered out.
 *   7. The two queries are merged and deduped, and a doc missing
 *      stats.streak_longest (dropped by that orderBy) still ranks.
 *   8. Your own summary card shows your real numbers even when you are not in
 *      the top 25.
 *   9. No "undefined" / "NaN" / "[object Object]" in anything visible, and
 *      every row carries the data-uid the profile/add-friend clicks need.
 *  10. On a 390px phone the board scrolls inside its own box, never sideways.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

const CSS  = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const APP  = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");

// ── Source guards (run with or without Chrome) ───────────────────────────────
const srcLines = [];
function srcOk(cond, m) { srcLines.push((cond ? "PASS " : "FAIL ") + m); }

srcOk(/function _phLbRenderStreak\(/.test(APP),
      "preview-app.js: _phLbRenderStreak() exists");
srcOk(/_phLbMode === "streak"\)\s*await _phLbRenderStreak\(\)/.test(APP),
      "preview-app.js: renderPhLeaderboard() dispatches the streak mode");
srcOk(/sectionMap = \{[^}]*streak:"streak"/.test(APP) && /btnMap\s*=\s*\{[^}]*streak:"streak"/.test(APP),
      "preview-app.js: phLbSwitchMode() knows the streak section + button");
srcOk(/window\.phLbSwitchStreakTab = function/.test(APP),
      "preview-app.js: phLbSwitchStreakTab() is exposed for the toggle's onclick");
srcOk(/ALL_LB_TBODIES = \[[^\]]*"ph-lb-streak-tbody"/.test(APP),
      "preview-app.js: streak rows are wired into the profile / add-friend click delegation");
// The whole point: the current streak is derived, never read straight off the doc.
srcOk(/_computeStreakInfo\(stats\.streak_days\)/.test(APP.slice(APP.indexOf("function _phLbStreakNums"))),
      "preview-app.js: the board recomputes from stats.streak_days, not the stored daily_streak");
srcOk(/id="ph-lb-streak-btn"[^>]*phLbSwitchMode\('streak'\)/.test(HTML),
      "preview.html: the 🔥 Streak mode button is in the mode row");
srcOk(/id="ph-lb-streak-longest-tab"/.test(HTML) && /id="ph-lb-streak-current-tab"/.test(HTML),
      "preview.html: both toggle tabs exist (longest ever / longest current)");

if (srcLines.some(l => l.startsWith("FAIL"))) {
  console.log(srcLines.join("\n"));
  console.log("\nThe streak leaderboard is missing or unwired, the browser checks cannot run.");
  process.exit(1);
}
console.log(srcLines.join("\n") + "\n");

if (!CHROME) {
  console.log("SKIP: no Chrome/Chromium found, the browser half of this check did not run.");
  process.exit(0);
}

// ── Lift the real functions out of preview-app.js ────────────────────────────
function grabFn(name) {
  const re = new RegExp(`\\n( *)(?:async )?function ${name}\\(`);
  const m = re.exec(APP);
  if (!m) throw new Error(`function ${name}() not found in preview-app.js`);
  const start = m.index;
  const i = APP.indexOf("{", start);
  let depth = 0;
  for (let j = i; j < APP.length; j++) {
    const ch = APP[j];
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return APP.slice(start, j + 1); }
  }
  throw new Error(`unbalanced braces reading ${name}()`);
}
// window.NAME = function(...) {...}
function grabAssignedFn(name) {
  const marker = `window.${name} = function`;
  const start = APP.indexOf(marker);
  if (start < 0) throw new Error(`${marker} not found in preview-app.js`);
  const i = APP.indexOf("{", APP.indexOf("(", start));
  let depth = 0;
  for (let j = i; j < APP.length; j++) {
    const ch = APP[j];
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return APP.slice(start, j + 1) + ";"; }
  }
  throw new Error(`unbalanced braces reading ${name}`);
}

// const NAME = `...`;  (the add-friend cell's SVG icons)
function grabConst(name) {
  const re = new RegExp(`const ${name} = \`[\\s\\S]*?\`;`);
  const m = re.exec(APP);
  if (!m) throw new Error(`const ${name} not found in preview-app.js`);
  return m[0];
}

const LIFTED = [
  grabConst("_LB_ICON_ADD"),
  grabConst("_LB_ICON_FRIENDS"),
  // streak maths, the same source of truth the rest of the app uses
  grabFn("_streakLocalDateStr"),
  grabFn("_streakParseDate"),
  grabFn("_streakDayDiff"),
  grabFn("_computeStreakInfo"),
  grabFn("_streakAddDay"),
  // leaderboard row helpers
  grabFn("escHtmlPH"),
  grabFn("phLbAvatarImg"),
  grabFn("phLbAddCell"),
  grabFn("phLbMedal"),
  grabFn("phLbRankClass"),
  grabFn("phLbSetSumCard"),
  grabFn("phLbClearSumCards"),
  // The one query behind every board: Firestore for a signed-in player,
  // /api/leaderboard for a guest. This harness is signed in, so it takes the
  // Firestore branch against the fake _db below.
  grabFn("lbTopUsers"),
  // ...and the layer in front of it that repaints the last rows instantly and
  // re-queries behind the draw. Lifted for real, so "a redraw inside the
  // window costs no query" is checked against the shipped code.
  grabFn("_lbSnapKey"),
  grabFn("_lbPanelOnScreen"),
  grabFn("lbTopUsersWarm"),
  // the board itself
  grabFn("_phLbStreakNums"),
  grabFn("_phLbRenderStreak"),
  grabAssignedFn("phLbSwitchStreakTab"),
  grabAssignedFn("phLbSwitchMode"),
].join("\n");

// ── Lift the real section markup out of preview.html ─────────────────────────
function grabSection(id) {
  const open = HTML.indexOf(`<div id="${id}"`);
  if (open < 0) throw new Error(`#${id} not found in preview.html`);
  let depth = 0, j = open;
  const tag = /<\/?div\b[^>]*?(\/?)>/g;
  tag.lastIndex = open;
  let m;
  while ((m = tag.exec(HTML))) {
    if (m[0].startsWith("</")) { depth--; if (depth === 0) return HTML.slice(open, m.index + m[0].length); }
    else if (!m[1]) depth++;
    j = m.index;
  }
  throw new Error(`unbalanced divs reading #${id} (last at ${j})`);
}
const SECTION = grabSection("ph-lb-streak-section");
// The mode row too: the 🔥 Streak pill is the 5th (6th with Tournaments), and a
// pill row that overflows is exactly how a phone starts scrolling sideways.
const MODE_ROW = (() => {
  const open = HTML.indexOf(`<div class="ph-lb-mode-row">`);
  if (open < 0) throw new Error(".ph-lb-mode-row not found in preview.html");
  const end = HTML.indexOf("</div>", HTML.lastIndexOf("</button>", HTML.indexOf("<!-- ── CASUAL", open)));
  return HTML.slice(open, end + 6);
})();

// ── Canned user docs ─────────────────────────────────────────────────────────
// Dates are built relative to "today" so the test never rots.
const DAY = 86400000;
function dstr(offsetDays) {
  const d = new Date(Date.now() - offsetDays * DAY);
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
// A run of `len` consecutive days ending `endOffset` days ago.
function run(len, endOffset) {
  const out = [];
  for (let i = 0; i < len; i++) out.push(dstr(endOffset + i));
  return out.sort();
}

const USERS = [
  // Live 12-day run, and a 30-day run last year. Best ever = 30, current = 12.
  { id: "u_ava", nickname: "Ava", avatar_url: "/avatars/clownfish.png",
    stats: { streak_days: run(30, 200).concat(run(12, 0)), daily_streak: 12, streak_longest: 30 } },

  // THE STALE ONE. Stopped playing 9 days ago at the end of a 40-day run.
  // Firestore still says daily_streak: 40, the board must say 0 and drop them
  // from the current tab, while 40 stays their longest ever (rank 1).
  { id: "u_bo", nickname: "Bo", avatar_url: "/avatars/narwhal.png",
    stats: { streak_days: run(40, 9), daily_streak: 40, streak_longest: 40 } },

  // Shield case: played days 0-2 and 4-8, with day 3 written in by a Streak
  // Shield. That makes one unbroken 9-day run, alive today.
  { id: "u_cy", nickname: "Cy", avatar_url: "/avatars/lobster.png",
    streak_shield_days: [dstr(3)],
    stats: { streak_days: run(9, 0), daily_streak: 9, streak_longest: 9 } },

  // Tie with Cy on both metrics (9 / 9), they must share a rank number.
  { id: "u_di", nickname: "Di", avatar_url: "/avatars/mullet.png",
    stats: { streak_days: run(9, 0), daily_streak: 9, streak_longest: 9 } },

  // Alive from YESTERDAY (not today). A streak is not broken until you miss a
  // whole day, so this 6-day run still counts as current.
  { id: "u_eli", nickname: "Eli", avatar_url: "/avatars/mullet.png",
    stats: { streak_days: run(6, 1), daily_streak: 6, streak_longest: 6 } },

  // Truncated history: _streakAddDay keeps only the last 800 days, so this
  // player's record 60-day run has fallen off the list. streak_longest must be
  // honoured over the (now shorter) recomputed value, a best run is never lost.
  { id: "u_fay", nickname: "Fay", avatar_url: "/avatars/mullet.png",
    stats: { streak_days: run(4, 0), daily_streak: 4, streak_longest: 20 } },

  // Blank + guest accounts: never shown.
  { id: "u_blank", nickname: "   ", stats: { streak_days: run(50, 0), daily_streak: 50, streak_longest: 50 } },
  { id: "u_guest", nickname: "Player", stats: { streak_days: run(50, 0), daily_streak: 50, streak_longest: 50 } },

  // Me. Small streak, nowhere near the board, the summary card must still
  // show my real numbers.
  { id: "u_me", nickname: "Tim", avatar_url: "/avatars/mullet.png",
    stats: { streak_days: run(2, 0), daily_streak: 2, streak_longest: 3 } },
];

const page = `<!doctype html><html><head><meta charset="utf-8">
<title>streak leaderboard</title>
<style>${CSS}</style>
<style>*,*::before,*::after{transition:none!important;animation:none!important}body{margin:0}</style>
</head>
<body>
<div class="ph-panel"><div class="ph-scard ph-full">
${MODE_ROW}
${SECTION}
</div></div>
<div id="out">RUNNING</div>
<script>
var ERRORS = [];
window.onerror = function (m) { ERRORS.push(String(m)); };
// The render function swallows failures into console.error + a friendly cell;
// surface them so a broken board can never pass as "rendered".
console.error = function () {
  ERRORS.push(Array.prototype.map.call(arguments, function (a) {
    return (a && a.stack) ? a.stack : String(a);
  }).join(" "));
};

// ── Stubs for everything the lifted code reaches outside itself ─────────────
var USERS = ${JSON.stringify(USERS)};
function $a(id) { return document.getElementById(id); }
var _authUser = { uid: "u_me" };
var _lbFriendUids = new Set(["u_ava"]);
function normalizeAvatarUrl(u) { return u || ""; }
function animalByImg(u) { return u ? { img: u } : null; }
function getDefaultAvatar() { return "/avatars/mullet.png"; }
// Real avatar paths do not resolve under file://, and the row's onerror
// fallback would then re-fire forever. Keep the chosen path visible on the
// element (that is what is being checked) but point the load at a data URI.
var PX = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
function _avSrc(u) { window.__avPicked = (window.__avPicked || []).concat([u]); return PX; }
function normalizeBgUrl(u) { return u || ""; }
var _BG_BY_IMG = {};
function _bgStyle() { return ""; }

// A fake Firestore that behaves the way the real one does in the way that
// matters here: orderBy on a field DROPS documents that do not have it.
var QUERIES = [];
var _db = {
  collection: function () {
    return {
      orderBy: function (field, dir) {
        var f = field.split(".");
        var q = {
          limit: function () { return q; },
          get: function () {
            QUERIES.push(field);
            var docs = USERS
              .filter(function (u) {
                var v = u; for (var i = 0; i < f.length; i++) { v = v && v[f[i]]; }
                return v !== undefined && v !== null;
              })
              .map(function (u) {
                var v = u; for (var i = 0; i < f.length; i++) { v = v && v[f[i]]; }
                return { id: u.id, _v: Number(v), data: function () { return u; } };
              })
              .sort(function (a, b) { return dir === "desc" ? b._v - a._v : a._v - b._v; });
            return Promise.resolve({ docs: docs });
          }
        };
        return q;
      }
    };
  }
};

var _phLbStreakTab = "longest";
function renderPhLeaderboard() { return _phLbRenderStreak(); }

// The instant-paint layer's module-level state. The FUNCTIONS are lifted from
// preview-app.js above; only these three declarations are the harness's, the
// same way _authUser and _lbFriendUids are.
var _lbSnapshot   = new Map();
var _lbRefreshing = new Set();
var LB_SNAP_TTL_MS = 60 * 1000;

${LIFTED}

// ── Drive it ────────────────────────────────────────────────────────────────
function snapshot() {
  var tb = $a("ph-lb-streak-tbody");
  var rows = Array.prototype.slice.call(tb.querySelectorAll("tr[data-uid]")).map(function (tr) {
    var tds = tr.querySelectorAll("td");
    return {
      uid: tr.dataset.uid,
      rank: (tds[0].textContent || "").trim(),
      name: (tr.querySelector(".ph-lb-pname") || {}).textContent || "",
      primary: (tds[2].textContent || "").trim(),
      secondary: (tds[3].textContent || "").trim(),
      days: (tds[4].textContent || "").trim(),
      me: tr.className.indexOf("ph-lb-me") >= 0,
      addBtn: !!tr.querySelector(".ph-lb-add-btn[data-add-uid]"),
      friendBtn: !!tr.querySelector(".ph-lb-add-btn.lb-add-friends"),
    };
  });
  var sum = $a("ph-lb-streak-summary");
  var wrap = tb.closest(".ph-lb-table-wrap");
  return {
    rows: rows,
    text: tb.textContent,
    thPrimary: $a("ph-lb-streak-th-primary").textContent.trim(),
    thSecondary: $a("ph-lb-streak-th-secondary").textContent.trim(),
    sumShown: sum.style.display !== "none",
    sumRank: $a("ph-lb-streak-rank").textContent.trim(),
    sumTitle: $a("ph-lb-streak-sum-title").textContent.trim(),
    sumVals: $a("ph-lb-streak-sum-vals").textContent.trim(),
    tabLongestActive: $a("ph-lb-streak-longest-tab").classList.contains("active"),
    tabCurrentActive: $a("ph-lb-streak-current-tab").classList.contains("active"),
    scrollW: wrap.scrollWidth, clientW: wrap.clientWidth,
  };
}

(async function () {
  var out = { errors: ERRORS, queries: [] };
  try {
    // The section starts hidden; clicking the 🔥 Streak pill is what reveals it.
    out.hiddenBefore = $a("ph-lb-streak-section").style.display === "none";
    $a("ph-lb-streak-btn").click();
    await new Promise(function (r) { setTimeout(r, 60); });
    out.modeSwitch = {
      shown:    $a("ph-lb-streak-section").style.display !== "none",
      btnActive: $a("ph-lb-streak-btn").classList.contains("active"),
      xpBtnActive: $a("ph-lb-xp-btn").classList.contains("active"),
      rowScrollW: $a("ph-lb-streak-btn").parentElement.scrollWidth,
      rowClientW: $a("ph-lb-streak-btn").parentElement.clientWidth,
    };
    _lbSnapshot.clear();
    QUERIES.length = 0;   // the click already rendered once; count from here
    // Default tab: longest ever.
    await _phLbRenderStreak();
    out.longest = snapshot();
    // Toggle to current, through the real onclick entry point.
    phLbSwitchStreakTab("current", $a("ph-lb-streak-current-tab"));
    await new Promise(function (r) { setTimeout(r, 60); });
    out.current = snapshot();
    // …and back, to prove the toggle is not one-way. This board was drawn
    // moments ago, so it must come back INSTANTLY off the snapshot and cost no
    // second query: that is the whole speed fix, measured here.
    phLbSwitchStreakTab("longest", $a("ph-lb-streak-longest-tab"));
    await new Promise(function (r) { setTimeout(r, 60); });
    out.backToLongest = snapshot();
    out.queriesAfterToggleBack = QUERIES.slice();
    // The phases below are about the board reflecting data that changed
    // underneath it, so they need real reads: drop the snapshot the way
    // signing out does.
    _lbSnapshot.clear();
    // I fall off the fetched page (too many players ahead of me): the summary
    // card must still show MY numbers, from the profile already in memory.
    var ME = USERS.pop();
    window.__fishGetMyStats = function () { return ME.stats; };
    await _phLbRenderStreak();
    out.offBoard = snapshot();
    USERS.push(ME);
    delete window.__fishGetMyStats;
    _lbSnapshot.clear();
    // Empty state: nobody has any streak data at all.
    USERS = [];
    await _phLbRenderStreak();
    out.empty = { text: $a("ph-lb-streak-tbody").textContent.trim(),
                  sumShown: $a("ph-lb-streak-summary").style.display !== "none" };
    out.queries = QUERIES;
    out.pageScrollW = document.documentElement.scrollWidth;
    out.pageClientW = document.documentElement.clientWidth;
  } catch (e) {
    out.errors.push("driver: " + (e && e.message));
  }
  document.getElementById("out").textContent = "@@" + JSON.stringify(out) + "@@";
})();
</script>
</body></html>`;

const file = path.join(os.tmpdir(), `cc-streak-lb-${Date.now()}.html`);
fs.writeFileSync(file, page);

function runChrome(width, height) {
  const dom = execFileSync(CHROME, [
    "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    `--window-size=${width},${height}`, "--virtual-time-budget=20000",
    "--dump-dom", "file://" + file,
  ], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  const m = dom.match(/@@([\s\S]*?)@@/);
  if (!m) throw new Error("no result payload in the DOM dump:\n" + dom.slice(0, 2000));
  return JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                        .replace(/&lt;/g, "<").replace(/&gt;/g, ">"));
}

let pass = 0, fail = 0;
const check = (n, c, extra) => {
  if (c) { pass++; console.log("  ✓ " + n); }
  else { fail++; console.log("  ✗ FAIL: " + n + (extra ? "  → " + extra : "")); }
};
const JUNK = /undefined|NaN|\[object Object\]/;
const names = s => s.rows.map(r => r.name).join(",");
const rowFor = (s, uid) => s.rows.find(r => r.uid === uid);

console.log("desktop (1280×900):");
const D = runChrome(1280, 900);
check("no script errors", D.errors.length === 0, D.errors.join(" | "));

// ── 0. The mode pill opens the board ────────────────────────────────────────
check("the streak section starts hidden", D.hiddenBefore === true);
check("clicking the 🔥 Streak pill reveals the section and marks the pill active",
      D.modeSwitch.shown && D.modeSwitch.btnActive && !D.modeSwitch.xpBtnActive,
      JSON.stringify(D.modeSwitch));

// ── 1. One query per board, ordered by that board's own stored field ────────
check("the longest board queries stats.streak_longest, the current board stats.daily_streak",
      D.queries[0] === "stats.streak_longest" && D.queries[1] === "stats.daily_streak",
      JSON.stringify(D.queries));
// Five renders happen below; four of them read. The fifth (toggling back to a
// board drawn seconds earlier) is served from the snapshot, checked next. What
// this still pins is that no single render ever costs TWO passes over the user
// documents.
check("each render costs at most ONE query, never a second pass over big user docs",
      D.queries.length === 4, JSON.stringify(D.queries));
// Coming back to a board drawn seconds ago must not re-run its query: the rows
// are already right, and the round trip is what made the tab feel slow.
check("going back to a board just drawn costs NO second query",
      D.queriesAfterToggleBack.length === 2,
      JSON.stringify(D.queriesAfterToggleBack));
check("...and it really did repaint that board's rows",
      D.backToLongest.thPrimary === D.longest.thPrimary
      && D.backToLongest.rows.length === D.longest.rows.length,
      JSON.stringify(D.backToLongest.rows.length));

// ── 2. LONGEST board ────────────────────────────────────────────────────────
console.log("\nLongest streak ever:");
const L = D.longest;
if (process.env.CC_DEBUG) console.log(JSON.stringify(D, null, 1));
check("tab starts on Longest, headers say so",
      L.tabLongestActive && !L.tabCurrentActive &&
      L.thPrimary === "Longest Streak" && L.thSecondary === "Current",
      `${L.thPrimary} / ${L.thSecondary}`);
check("ranked by best run ever: Bo(40) > Ava(30) > Fay(20) > Cy/Di(9) > Eli(6) > Tim(3)",
      names(L) === "Bo,Ava,Fay,Cy,Di,Eli,Tim", names(L));
check("the lapsed player still owns the longest-ever crown",
      rowFor(L, "u_bo").rank.includes("🥇") && rowFor(L, "u_bo").primary.includes("40"),
      JSON.stringify(rowFor(L, "u_bo")));
check("…and their CURRENT column reads 0, not the stale 40 in their doc",
      rowFor(L, "u_bo").secondary === "0", rowFor(L, "u_bo").secondary);
check("a shield-covered day keeps the run whole (Cy = 9, not 3)",
      rowFor(L, "u_cy").primary.includes("9"), rowFor(L, "u_cy").primary);
check("a record that fell off the 800-day history is not lost (Fay = 20, not 4)",
      rowFor(L, "u_fay").primary.includes("20") && rowFor(L, "u_fay").secondary === "4",
      JSON.stringify(rowFor(L, "u_fay")));
// Cy and Di are level on both metrics, so they share rank 4 (the same way the
// Wins and Tournament boards render a tie) and the next player down skips to 6.
check("tied players share a rank (Cy and Di both #4)",
      rowFor(L, "u_cy").rank === rowFor(L, "u_di").rank && rowFor(L, "u_cy").rank === "#4",
      `${rowFor(L, "u_cy").rank} vs ${rowFor(L, "u_di").rank}`);
check("…and the tie pushes the next player to #6, not #5",
      rowFor(L, "u_eli").rank === "#6", rowFor(L, "u_eli").rank);
check("blank-name and guest accounts are filtered out",
      !rowFor(L, "u_blank") && !rowFor(L, "u_guest"), names(L));
check("no player appears twice", new Set(L.rows.map(r => r.uid)).size === L.rows.length, names(L));
check("days-played column is populated (Ava has 42 days on record)",
      rowFor(L, "u_ava").days === "42", rowFor(L, "u_ava").days);
check("no undefined / NaN / [object Object] in the table", !JUNK.test(L.text),
      (L.text.match(JUNK) || [])[0]);
check("my own row is marked, and carries no add-friend button",
      rowFor(L, "u_me").me && !rowFor(L, "u_me").addBtn);
check("an existing friend shows the friends icon, a stranger the add button",
      rowFor(L, "u_ava").friendBtn && rowFor(L, "u_bo").addBtn,
      `ava.friend=${rowFor(L, "u_ava").friendBtn} bo.add=${rowFor(L, "u_bo").addBtn}`);
check("every row carries data-uid (profile click + add-friend delegation)",
      L.rows.every(r => r.uid && r.uid.length > 0));

// ── 3. CURRENT board ────────────────────────────────────────────────────────
console.log("\nLongest current streak:");
const C = D.current;
check("toggle switched tabs and swapped the headers",
      C.tabCurrentActive && !C.tabLongestActive &&
      C.thPrimary === "Current Streak" && C.thSecondary === "Longest",
      `${C.thPrimary} / ${C.thSecondary}`);
check("ranked by the live run: Ava(12) > Cy/Di(9) > Eli(6) > Fay(4) > Tim(2)",
      names(C) === "Ava,Cy,Di,Eli,Fay,Tim", names(C));
check("THE STALE ONE IS GONE: Bo's frozen daily_streak:40 does not rank",
      !rowFor(C, "u_bo"), names(C));
check("a streak that last ran YESTERDAY is still alive (Eli = 6)",
      !!rowFor(C, "u_eli") && rowFor(C, "u_eli").primary.includes("6"),
      JSON.stringify(rowFor(C, "u_eli")));
check("the leader's longest-ever still shows in the secondary column (Ava = 30)",
      rowFor(C, "u_ava").secondary === "30", rowFor(C, "u_ava").secondary);
check("no undefined / NaN / [object Object] in the table", !JUNK.test(C.text),
      (C.text.match(JUNK) || [])[0]);
check("toggling back restores the longest board exactly",
      names(D.backToLongest) === names(L) && D.backToLongest.thPrimary === "Longest Streak",
      names(D.backToLongest));

// ── 4. Your summary card ────────────────────────────────────────────────────
console.log("\nYour summary card:");
check("shown on the longest board with my real numbers",
      L.sumShown && /Current\s*2 days/.test(L.sumVals) && /Longest\s*3 days/.test(L.sumVals),
      L.sumVals);
check("card title follows the toggle",
      L.sumTitle === "Your Longest Streak" && C.sumTitle === "Your Current Streak",
      `${L.sumTitle} / ${C.sumTitle}`);
check("my rank is a real number, not a dash (I am on the board)",
      /^#\d+$/.test(L.sumRank), L.sumRank);
const OB = D.offBoard;
check("off the board, the card still shows my numbers with a dash for rank",
      OB.sumShown && OB.sumRank === "-" && /Longest\s*3 days/.test(OB.sumVals),
      `${OB.sumRank} | ${OB.sumVals}`);
check("…and I am genuinely not in the rows for that case",
      !rowFor(OB, "u_me"), names(OB));
check("singular day is not written as '1 days'", !/\b1 days\b/.test(L.sumVals + " " + L.text),
      L.sumVals);

// ── 5. Empty state ──────────────────────────────────────────────────────────
console.log("\nEdge cases:");
check("with no data the board says so instead of blanking",
      /No streak data yet/.test(D.empty.text), D.empty.text);
check("…and the summary card hides", D.empty.sumShown === false);

// ── 6. Phone ────────────────────────────────────────────────────────────────
console.log("\nphone (390×844):");
const P = runChrome(390, 844);
check("module ran without errors", P.errors.length === 0, P.errors.join(" | "));
check("the same rows render on a phone", names(P.longest) === names(L), names(P.longest));
check("the page never scrolls sideways",
      P.pageScrollW <= P.pageClientW + 1, `scrollW=${P.pageScrollW} clientW=${P.pageClientW}`);
check("the mode pill row wraps instead of overflowing",
      P.modeSwitch.rowScrollW <= P.modeSwitch.rowClientW + 1,
      `scrollW=${P.modeSwitch.rowScrollW} clientW=${P.modeSwitch.rowClientW}`);
check("the table scrolls inside its own box",
      P.longest.scrollW >= P.longest.clientW, `scrollW=${P.longest.scrollW} clientW=${P.longest.clientW}`);

try { fs.unlinkSync(file); } catch (_) {}
console.log(`\n${"=".repeat(46)}\nRESULT: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
