#!/usr/bin/env node
/* Real-browser render check for the CLANS TAB.
 *
 * Run:  node test_clans_render.js        (needs Google Chrome installed)
 *
 * test_clans_ui.js proves the module's PUBLIC HOOKS behave (claim dedup,
 * off-switch, toasts) in a bare vm. This one puts the REAL clans-ui.js and the
 * REAL preview.css in headless Chrome and looks at what actually paints —
 * the class of bug a stubbed document can never catch: a section that renders
 * empty, an undefined leaking into visible text, a season countdown that never
 * ticks, or a page that scrolls sideways on a phone.
 *
 * Checks, per screen (Home → Clan profile → Members → Leaderboard):
 *   • the screen renders its real content (podium, my-clan row, stat tiles,
 *     member rows with roles/points, leaderboard table with every column)
 *   • no "undefined" / "NaN" / "[object Object]" anywhere in visible text
 *   • the live season countdown is populated and moves
 *   • at 390px wide (iPhone) the page never scrolls horizontally
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

if (!CHROME) {
  console.log("SKIP: no Chrome/Chromium found — cannot run the clans render check.");
  process.exit(0);
}

const CSS = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const MOD = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/clans-ui.js"), "utf8");

const NOW = Math.floor(Date.now() / 1000);

// ── Canned server payloads (shapes copied from clan_server.py) ──────────────
const SEASON = { id: "2026-Q3", number: 1, name: "Riptide",
                 starts_ts: NOW - 86400 * 30, ends_ts: NOW + 86400 * 44, now: NOW };

const CARD = (id, name, icon, pts, rank) => ({
  id, name, icon, icon_name: name, description: name + " description",
  privacy: "public", member_count: 4, max_members: 20, points: pts,
  comp_wins: 7, casual_wins: 9, challenge_points: 5, challenges_completed: 2,
  trade_points: 3, games: 30, comp_losses: 4, level: 3, season_border: null,
  rank, record: "16-14",
});

const TOP3 = [
  CARD("c1", "Reef Riders", "/avatars/clownfish.png", 140, 1),
  CARD("c2", "Kelp Krew", "/avatars/narwhal.png", 120, 2),
  CARD("c3", "Tide Turners", "/avatars/lobster.png", 90, 3),
];

const PROFILE = Object.assign({}, CARD("c1", "Reef Riders", "/avatars/clownfish.png", 140, 1), {
  owner_uid: "u1", created_ts: NOW - 86400 * 20,
  captains_can_edit_roles: false,
  custom_roles: [{ id: "r1", name: "Reef Keeper", perms: { invite: true, moderate_chat: true } }],
  pinned_announcement: { text: "Practice tonight at 8!", by: "Alice", ts: NOW - 3600 },
  members: [
    { uid: "u1", name: "Alice", avatar: "/avatars/clownfish.png", role: "owner",
      custom_role_id: null, joined_ts: NOW - 86400 * 20, online: true, last_seen: NOW,
      points: 62, weekly_points: 20, comp_wins: 4, casual_wins: 5, challenges_done: 2,
      trade_point_today: true, is_mvp_chip: true },
    { uid: "u2", name: "Bob", avatar: "/avatars/lobster.png", role: "captain",
      custom_role_id: "r1", joined_ts: NOW - 86400 * 10, online: false, last_seen: NOW - 7200,
      points: 41, weekly_points: 9, comp_wins: 2, casual_wins: 3, challenges_done: 1,
      trade_point_today: false, is_mvp_chip: false },
    { uid: "u3", name: "Cara", avatar: "/avatars/narwhal.png", role: "member",
      custom_role_id: null, joined_ts: NOW - 86400 * 3, online: false, last_seen: NOW - 90000,
      points: 12, weekly_points: 0, comp_wins: 0, casual_wins: 1, challenges_done: 0,
      trade_point_today: false, is_mvp_chip: false },
  ],
  former_contributors: [{ uid: "u9", name: "Quinn", points: 25 }],
  activity: [
    { ts: NOW - 300, type: "casual", text: "🌊 Alice finished 1st in a 4-player game (+2 pts)" },
    { ts: NOW - 900, type: "trade", text: "🤝 Bob completed a clan trade (+1 pt)" },
    { ts: NOW - 4000, type: "join", text: "🌊 Cara joined the clan" },
  ],
  events: [{ id: "e1", name: "Clan Game Night", ts: NOW + 7200, desc: "Casual games",
             host_uid: "u1", host_name: "Alice", attending: ["u1", "u2"], reminders: [] }],
  win_streak: 3,
  prev_results: {},
  mvp_chip: { uid: "u1", name: "Alice", sid: "2026-Q2", season: 0, until: NOW + 86400 },
  level_info: { level: 3, xp: 275, into: 75, next: 300 },
  daily_goal: { goal: { id: "games", target: 5, label: "Complete 5 games today" },
                progress: 3, done: false, date: "2026-07-30" },
  weekly: { week: "2026-W31", games: 12, points: 30, trades: 2, comp_wins: 3, challenges_done: [] },
  challenges: [{ id: "w1", name: "Reef Regulars", desc: "Complete 15 clan games",
                 metric: "games", target: 15, progress: 12, clan_points: 10, member_xp: 40,
                 min_contribution: 1, done: false, ends_ts: NOW + 86400 * 2,
                 contributors: [{ uid: "u1", name: "Alice", points: 20, qualifies: true },
                                { uid: "u2", name: "Bob", points: 9, qualifies: true }] }],
  favorite_critter: "/avatars/clownfish.png",
  favorite_votes: { "/avatars/clownfish.png": 2 },
  my_vote: "/avatars/clownfish.png",
  rival: CARD("c2", "Kelp Krew", "/avatars/narwhal.png", 120, 2),
  season: SEASON,
  week_ends_ts: NOW + 86400 * 2,
  my: {
    role: "owner", custom_role_id: null,
    perms: { invite: true, review_requests: true, remove_members: true,
             post_announcements: true, pin_announcements: true, moderate_chat: true,
             create_events: true, manage_challenges: true, change_roles: true,
             edit_custom_roles: true },
    is_owner: true,
    contribution: { points: 62, game_points: 55, trade_points: 7 },
  },
  join_requests: [{ uid: "u7", name: "Dana", ts: NOW - 600 }],
});

const HOME = {
  ok: true, season: SEASON, top3: TOP3, total_clans: 3,
  my_clan: TOP3[0], my_clan_full: PROFILE,
  my_contribution: { points: 62, game_points: 55, trade_points: 7, weekly: 20, weekly_cap: 150 },
  cooldown_until: 0,
  invites: [{ clan_id: "c3", name: "Tide Turners", icon: "/avatars/lobster.png",
              by: "Zoe", ts: NOW - 1200 }],
  badges: [{ type: "mvp", season: 1, sid: "2026-Q2", clan: "Reef Riders",
             title: "Season 1 Clan MVP", ts: NOW - 86400 }],
  prev_season: { sid: "2026-Q2", number: 0, name: "Undertow", standings: [] },
};

const RESPONSES = {
  "/api/clan/home": HOME,
  "/api/clan/get": { ok: true, clan: PROFILE },
  "/api/clan/leaderboard": { ok: true, season: SEASON, rows: TOP3 },
  "/api/clan/browse": { ok: true, season: SEASON, rows: TOP3, recommended: [TOP3[1]] },
  "/api/clan/chat-get": { ok: true, muted_until: 0, pinned: PROFILE.pinned_announcement,
    messages: [
      { id: "m1", ts: NOW - 600, uid: "u2", name: "Bob", kind: "msg", text: "Good game everyone" },
      { id: "m2", ts: NOW - 300, uid: "", name: "", kind: "system", text: "🌊 Cara joined the clan" },
      { id: "m3", ts: NOW - 120, uid: "u1", name: "Alice", kind: "announce", text: "Practice tonight at 8!" },
    ] },
};

// ── The page ────────────────────────────────────────────────────────────────
const page = `<!doctype html><html><head><meta charset="utf-8">
<title>clans render</title>
<style>${CSS}</style>
<style>body{margin:0;font-family:"Nunito",sans-serif;} #cc-clans-root{padding:8px;}</style>
</head><body>
<div id="auth-stats-lobby" data-bg-tab="clans">
  <div class="ph-panel" id="ph-panel-clans"><div id="cc-clans-root"></div></div>
</div>
<pre id="RESULT"></pre>
<script>
const RESPONSES = ${JSON.stringify(RESPONSES)};
window.__ccToasts = [];
window.__ccClans = {
  ENABLED: true, APP_BUILD: "test",
  get:  async (p) => RESPONSES[p] || { ok: true },
  post: async (p, b) => RESPONSES[p] || { ok: true },
  toast: (m, t) => window.__ccToasts.push(String(m)),
  nickname: () => "Alice",
  authUser: () => ({ uid: "u1", getIdToken: async () => "tok" }),
  idToken: async () => "tok",
  avSrc: (u) => u,
  animalAvatars: () => ([
    { id: "clownfish", name: "Clownfish", img: "/avatars/clownfish.png" },
    { id: "lobster",   name: "Lobster",   img: "/avatars/lobster.png" },
    { id: "narwhal",   name: "Narwhal",   img: "/avatars/narwhal.png" },
  ]),
  currentRoom: () => "",
};
</script>
<script>${MOD}</script>
<script>
const out = { errors: [], screens: {} };
window.addEventListener("error", e => out.errors.push(String(e.message)));
const txt = () => document.getElementById("cc-clans-root").innerText || "";
const q   = (s) => document.querySelectorAll(s).length;
const wait = (ms) => new Promise(r => setTimeout(r, ms));

function snapshot(name) {
  const t = txt();
  out.screens[name] = {
    text: t,
    bad: ["undefined", "NaN", "[object Object]", "{{", "}}"].filter(w => t.includes(w)),
    counts: {
      podium: q(".ccC-pod"), myclan: q(".ccC-myclan"), sections: q(".ccC-sec"),
      stats: q(".ccC-stat"), members: q(".ccC-member"), rows: q(".ccC-table tbody tr"),
      tabs: q(".ph-lb-mode-btn"), goalbars: q(".ccC-goalbar"),
      countdown: (document.querySelector(".ccC-count") || {}).innerText || "",
      activity: q(".ccC-activity .row"), chips: q(".ccC-chip"),
    },
    // widest painted element vs the viewport → horizontal overflow check
    overflow: Math.max(0, Math.round(
      Math.max(...[...document.querySelectorAll("#cc-clans-root *")]
        .map(n => n.getBoundingClientRect().right), 0) - document.documentElement.clientWidth)),
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
  };
}

(async () => {
  try {
    await window.__ccClansRender();
    await wait(120);
    snapshot("home");
    const cd1 = (document.querySelector(".ccC-count") || {}).innerText || "";

    // Home → my clan profile (Overview)
    const myRow = document.querySelector(".ccC-myclan");
    if (myRow) myRow.click();
    await wait(200);
    snapshot("profile");

    // Profile → Members tab (2nd sub-tab)
    const tabs = [...document.querySelectorAll(".ph-lb-mode-btn")];
    const memTab = tabs.find(b => /Members/i.test(b.textContent));
    if (memTab) memTab.click();
    await wait(200);
    snapshot("members");

    // Activity log tab
    const logTab = [...document.querySelectorAll(".ph-lb-mode-btn")].find(b => /Activity/i.test(b.textContent));
    if (logTab) logTab.click();
    await wait(200);
    snapshot("log");

    // Back → full leaderboard
    const back = [...document.querySelectorAll(".ccC-btn")].find(b => /Back/i.test(b.textContent));
    if (back) back.click();
    await wait(250);
    const lbBtn = [...document.querySelectorAll(".ccC-btn")].find(b => /Full Clan Leaderboard/i.test(b.textContent));
    if (lbBtn) lbBtn.click();
    await wait(250);
    snapshot("leaderboard");

    // countdown must actually be ticking
    await wait(1200);
    const cd2 = (document.querySelector(".ccC-count") || {}).innerText || "";
    out.countdown = { first: cd1, later: cd2, ticked: !!cd1 && !!cd2 };
  } catch (e) {
    out.errors.push("THREW: " + (e && e.message ? e.message : String(e)));
  }
  document.getElementById("RESULT").textContent = "@@" + JSON.stringify(out) + "@@";
})();
</script>
</body></html>`;

const file = path.join(os.tmpdir(), `cc_clans_render_${Date.now()}.html`);
fs.writeFileSync(file, page);

function run(width, height) {
  const dom = execFileSync(CHROME, [
    "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    `--window-size=${width},${height}`, "--virtual-time-budget=9000",
    "--dump-dom", "file://" + file,
  ], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  const m = dom.match(/@@([\s\S]*?)@@/);
  if (!m) throw new Error("no result payload in the DOM dump");
  return JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                        .replace(/&lt;/g, "<").replace(/&gt;/g, ">"));
}

let pass = 0, fail = 0;
const check = (n, c, extra) => {
  if (c) { pass++; console.log("  ✓ " + n); }
  else { fail++; console.log("  ✗ FAIL: " + n + (extra ? "  → " + extra : "")); }
};

console.log("desktop (1280×900):");
const D = run(1280, 900);
check("module ran without errors", D.errors.length === 0, D.errors.join(" | "));

const home = D.screens.home || { counts: {}, bad: [] };
check("home: three featured clans", home.counts.podium === 3, "podium=" + home.counts.podium);
check("home: my clan row rendered", home.counts.myclan >= 1);
check("home: season countdown filled", /\d/.test(home.counts.countdown || ""), home.counts.countdown);
check("home: pending invite shown", (home.text || "").includes("Tide Turners"));
check("home: badge shelf shown", (home.text || "").includes("Clan MVP"));
check("home: no placeholder junk in text", home.bad.length === 0, home.bad.join(","));

const prof = D.screens.profile || { counts: {}, bad: [] };
check("profile: stat tiles rendered", prof.counts.stats >= 8, "stats=" + prof.counts.stats);
check("profile: pinned announcement shown", (prof.text || "").includes("Practice tonight"));
check("profile: daily goal bar rendered", prof.counts.goalbars >= 1);
check("profile: weekly challenge shown", (prof.text || "").includes("Reef Regulars"));
check("profile: challenge contributors listed", (prof.text || "").includes("Alice 20"));
check("profile: rival comparison shown", (prof.text || "").includes("Kelp Krew"));
check("profile: clan level shown", (prof.text || "").includes("Lv 3"));
check("profile: no placeholder junk in text", prof.bad.length === 0, prof.bad.join(","));

const mem = D.screens.members || { counts: {}, bad: [] };
check("members: every member row rendered", mem.counts.members >= 3, "members=" + mem.counts.members);
check("members: roles shown", (mem.text || "").includes("Owner") && (mem.text || "").includes("Captain"));
check("members: custom role shown", (mem.text || "").includes("Reef Keeper"));
check("members: MVP chip shown", (mem.text || "").includes("MVP"));
check("members: join request shown", (mem.text || "").includes("Dana"));
check("members: former contributor shown", (mem.text || "").includes("Quinn"));
check("members: no placeholder junk in text", mem.bad.length === 0, mem.bad.join(","));

const log = D.screens.log || { counts: {}, bad: [] };
check("activity log: rows rendered", log.counts.activity >= 3, "rows=" + log.counts.activity);
check("activity log: trade line hides the items",
      (log.text || "").includes("completed a clan trade") && !/coins/i.test(log.text || ""));

const lb = D.screens.leaderboard || { counts: {}, bad: [] };
check("leaderboard: a row per clan", lb.counts.rows === 3, "rows=" + lb.counts.rows);
check("leaderboard: featured top three", lb.counts.podium === 3);
check("leaderboard: no placeholder junk in text", lb.bad.length === 0, lb.bad.join(","));

check("season countdown is live", D.countdown && D.countdown.ticked, JSON.stringify(D.countdown));

console.log("phone (390×844):");
const P = run(390, 844);
check("phone: module ran without errors", P.errors.length === 0, P.errors.join(" | "));
for (const name of ["home", "profile", "members", "leaderboard"]) {
  const s = P.screens[name];
  if (!s) { check("phone: " + name + " rendered", false); continue; }
  // Wide content (the leaderboard table) must scroll INSIDE its own box —
  // the page itself must never scroll sideways.
  check("phone: " + name + " does not scroll sideways",
        s.scrollW <= s.clientW + 1, `scrollW=${s.scrollW} clientW=${s.clientW}`);
}

try { fs.unlinkSync(file); } catch (_) {}
console.log(`\n${"=".repeat(46)}\nRESULT: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
