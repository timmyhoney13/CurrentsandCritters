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
  favorite_votes: { "/avatars/clownfish.png": 2, "/avatars/narwhal.png": 1 },
  my_vote: "/avatars/clownfish.png",
  // Critters somebody in the clan has unlocked: the only ones the clan may
  // wear, so the only ones the icon picker and the season vote may offer.
  icon_pool: ["/avatars/clownfish.png", "/avatars/mullet.png", "/avatars/narwhal.png"],
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
  // My own unlocks — the critters offered when I found a clan (I'd be its only
  // member, so my unlocks are the whole clan's choice).
  my_unlocked: ["/avatars/clownfish.png", "/avatars/lobster.png", "/avatars/mullet.png",
                "/avatars/narwhal.png"],
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
  // The server resolves the friend code and hands the name back for the toast.
  "/api/clan/invite": { ok: true, name: "LemmeSeeThemToes" },
  // A vote comes back with the RECOUNTED tally, so the client repaints from
  // this alone — no follow-up /home or /get. Narwhal overtakes clownfish here.
  "/api/clan/vote-critter": { ok: true, my_vote: "/avatars/narwhal.png",
    favorite_critter: "/avatars/narwhal.png",
    favorite_votes: { "/avatars/narwhal.png": 2, "/avatars/clownfish.png": 1 } },
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
<style>body{margin:0;font-family:"Nunito",sans-serif;} #cc-clans-root{padding:8px;}
/* preview.css hides the lobby and every .ph-panel until sign-in picks a tab.
   Left hidden, EVERY getBoundingClientRect() here reads 0 — the overflow and
   scroll checks pass because nothing is laid out at all, and no geometry can
   be measured. Force the two wrappers visible and change nothing else. */
#auth-stats-lobby{display:block!important} .ph-panel{display:block!important}</style>
</head><body>
<div id="auth-stats-lobby" data-bg-tab="clans">
  <div class="ph-panel" id="ph-panel-clans"><div id="cc-clans-root"></div></div>
</div>
<pre id="RESULT"></pre>
<script>
const RESPONSES = ${JSON.stringify(RESPONSES)};
const HOME_JSON = JSON.parse(JSON.stringify(RESPONSES["/api/clan/home"]));
window.__ccToasts = [];
window.__ccPosts = [];
window.__ccClans = {
  ENABLED: true, APP_BUILD: "test",
  // The REAL bridge (preview-app's apiPost) resolves to an ENVELOPE —
  // { ok, status, data } — not the server payload. Stubbing the payload
  // directly is what let the blank-tab bug through every suite.
  get:  async (p) => ({ ok: true, status: 200, data: RESPONSES[p] || { ok: true } }),
  post: async (p, b) => {
    window.__ccPosts.push({ p, b });
    // A deliberately slow endpoint, so the suite can watch what is on screen
    // WHILE the server is still thinking — which is the whole point of
    // painting from cache first.
    const d = (window.__ccDelay || {})[p] || 0;
    if (d) await new Promise(r => setTimeout(r, d));
    return { ok: true, status: 200, data: RESPONSES[p] || { ok: true } };
  },
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
      iconTiles: q(".ccC-iconpick .ic"),
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
    // Countdown liveness, measured while we're still ON the home screen (the
    // only screens with a season block are home and the leaderboard).
    const cd1 = (document.querySelector(".ccC-count") || {}).innerText || "";
    await wait(1200);
    const cd1b = (document.querySelector(".ccC-count") || {}).innerText || "";
    out.countdown = { first: cd1, later: cd1b,
                      ticked: !!cd1 && !!cd1b && cd1 !== cd1b };

    // Home → my clan profile (Overview). /home already returned this exact
    // profile as my_clan_full, so opening MY OWN clan must not go to the server
    // again — that second round trip is what made the tab feel slow.
    const postsBeforeOpen = window.__ccPosts.length;
    const myRow = document.querySelector(".ccC-myclan");
    if (myRow) myRow.click();
    await wait(200);
    out.openMyClan = {
      calls: window.__ccPosts.slice(postsBeforeOpen).map(c => c.p),
      loadingShown: /Loading clan/i.test(txt()),
    };
    snapshot("profile");
    const voteSec = () => [...document.querySelectorAll(".ccC-sec")]
      .find(s => /Favorite clan critter/i.test(s.innerText || "")) || null;
    out.voteText = ((voteSec() || {}).innerText) || "";

    // Cast a vote. One request, repainted in place from its response: the
    // section must not blank out, the screen must not be rebuilt, and the two
    // follow-up fetches it used to make (/home then /get) must be gone.
    const postsBeforeVote = window.__ccPosts.length;
    const tiles = [...(voteSec() || document).querySelectorAll(".ccC-iconpick .ic")];
    const narwhal = tiles.find(t => /narwhal/i.test(t.innerHTML));
    if (narwhal) { narwhal.click(); narwhal.click(); }   // double click = one ballot
    await wait(250);
    out.vote = {
      calls: window.__ccPosts.slice(postsBeforeVote).map(c => c.p),
      sent: (window.__ccPosts.filter(c => c.p === "/api/clan/vote-critter").pop() || {}).b || null,
      text: ((voteSec() || {}).innerText) || "",
      tiles: [...(voteSec() || document).querySelectorAll(".ccC-iconpick .ic")].length,
      selected: [...(voteSec() || document).querySelectorAll(".ccC-iconpick .ic.sel")]
        .map(t => t.innerHTML).join(" "),
    };

    // Profile → Members tab (2nd sub-tab)
    const tabs = [...document.querySelectorAll(".ph-lb-mode-btn")];
    const memTab = tabs.find(b => /Members/i.test(b.textContent));
    if (memTab) memTab.click();
    await wait(200);
    snapshot("members");
    // The invite box asks for a friend code, so the placeholder carries as much
    // of that instruction as the heading does — and innerText can't see it.
    out.invitePlaceholder = [...document.querySelectorAll("#cc-clans-root input")]
      .map(i => i.placeholder || "").join(" | ");
    // Actually use it: type a code, press the button, see what goes to the
    // server and what the player is told.
    const codeInput = [...document.querySelectorAll("#cc-clans-root input")]
      .find(i => /friend code/i.test(i.placeholder || ""));
    const sendBtn = [...document.querySelectorAll("#cc-clans-root button")]
      .find(b => /send invite/i.test(b.textContent || ""));
    if (codeInput && sendBtn) {
      codeInput.value = "2809";
      sendBtn.click();
      await wait(200);
      out.invite = {
        sent: (window.__ccPosts.filter(c => c.p === "/api/clan/invite").pop() || {}).b || null,
        toast: window.__ccToasts.filter(t => /invite sent/i.test(t)).pop() || "",
        cleared: codeInput.value,
      };
    }

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

    // Clan chat (server room chat + clan messages share this pane)
    await window.__ccClansRender();
    await wait(150);
    const myRow2 = document.querySelector(".ccC-myclan");
    if (myRow2) myRow2.click();
    await wait(200);
    const chatTab = [...document.querySelectorAll(".ph-lb-mode-btn")].find(b => /Chat/i.test(b.textContent));
    if (chatTab) chatTab.click();
    await wait(350);
    snapshot("chat");
    out.chat = {
      msgs: q(".ccC-msg"), mine: q(".ccC-msg.me"),
      system: q(".ccC-msg.sys"), announce: q(".ccC-msg.ann"),
      input: !!document.querySelector(".ccC-chat-in input"),
      // the log must scroll inside its own box, never grow the page
      logScrolls: (() => { const l = document.querySelector(".ccC-chat-log");
        return !!l && l.scrollHeight >= l.clientHeight; })(),
    };

    // ── Opening the tab must not wait on the network ───────────────────
    // The first open cached this account's home payload, so a SECOND open has
    // to paint from it immediately and let the fetch land behind it. With a
    // 900ms server, the screen must already be real at 120ms — not sitting on
    // "Loading clans…", which is what "the clan tab takes forever" was.
    out.instant = {};
    window.__ccDelay = { "/api/clan/home": 900 };
    window.__ccClansRender();                      // deliberately NOT awaited
    await wait(120);
    out.instant.early = txt().trim();
    out.instant.earlyLen = out.instant.early.length;
    out.instant.earlyLoading = /Loading clans/i.test(out.instant.early);
    await wait(1100);
    out.instant.late = txt().trim().length;
    window.__ccDelay = {};

    // Saving clan settings must cost ONE request and must never blank the
    // panel: it used to null the profile, fetch /home, then let renderClan
    // fetch /get as well — two sequential round trips with "Loading clan…"
    // in between.
    await window.__ccClansRender(); await wait(200);
    const settingsRow = document.querySelector(".ccC-myclan");
    if (settingsRow) settingsRow.click();
    await wait(250);
    const setTab = [...document.querySelectorAll(".ph-lb-mode-btn")]
      .find(b => /Settings/i.test(b.textContent));
    out.settings = { found: !!setTab };
    if (setTab) {
      setTab.click(); await wait(200);
      const saveBtn = [...document.querySelectorAll(".ccC-btn")]
        .find(b => /Save settings/i.test(b.textContent));
      out.settings.foundSave = !!saveBtn;
      if (saveBtn) {
        const before = window.__ccPosts.length;
        saveBtn.click();
        await wait(300);
        out.settings.calls = window.__ccPosts.slice(before).map(c => c.p);
        out.settings.blanked = /Loading clan/i.test(txt());
      }
    }

    // A player with NO clan sees the join/create call to action instead, and
    // that is the only path to the create form (you can't create while in a
    // clan) — re-render with a clanless payload and walk it.
    RESPONSES["/api/clan/home"] = Object.assign({}, HOME_JSON,
      { my_clan: null, my_clan_full: null, invites: [] });
    await window.__ccClansRender();
    await wait(300);
    snapshot("noclan");
    // The two things a clanless player came here to do must be the FIRST thing
    // on the tab — above the season block and the top-3 podium, not scrolled
    // off the bottom under a leaderboard they have no part in yet.
    {
      const bar  = document.querySelector(".ccC-cta");
      const join = document.querySelector(".ccC-cta-btn.join");
      const make = document.querySelector(".ccC-cta-btn.make");
      const pod  = document.querySelector(".ccC-podium");
      const seas = document.querySelector(".ccC-season, .ccC-seasonbox");
      const box  = (n) => n ? n.getBoundingClientRect() : null;
      const br = box(bar), jr = box(join), mr = box(make), pr = box(pod), sr = box(seas);
      out.cta = {
        found: !!bar, joinFound: !!join, makeFound: !!make,
        joinText: join ? join.textContent.replace(/\\s+/g, " ").trim() : "",
        makeText: make ? make.textContent.replace(/\\s+/g, " ").trim() : "",
        // Big enough to read as a button, not a link.
        joinH: jr ? Math.round(jr.height) : 0,
        makeH: mr ? Math.round(mr.height) : 0,
        onScreen: !!(jr && mr && jr.top >= 0 && jr.height > 0 && mr.height > 0),
        abovePodium: !!(br && pr) ? br.top < pr.top : null,
        aboveSeason: !!(br && sr) ? br.top < sr.top : null,
        topOfTab: br ? Math.round(br.top) : -1,
      };
      if (join) {
        join.click(); await wait(250);
        out.cta.joinOpensBrowse = !!document.querySelector("input.ccC-inp, .ccC-member");
        await window.__ccClansRender(); await wait(250);
      }
    }
    // Joining must be possible from THIS screen: the open clans are listed
    // here with their own Join buttons, and pressing one joins.
    const joinBtn = [...document.querySelectorAll(".ccC-member .ccC-btn")]
      .find(b => b.textContent.trim() === "Join");
    out.noClanJoin = {
      rows: q(".ccC-member"),
      hasJoinBtn: !!joinBtn,
      seeAll: [...document.querySelectorAll(".ccC-btn")].some(b => /See all clans/i.test(b.textContent)),
    };
    if (joinBtn) {
      const before = window.__ccPosts.length;
      joinBtn.click();
      await wait(300);
      out.noClanJoin.posted = window.__ccPosts.slice(before).map(c => c.p);
    }
    RESPONSES["/api/clan/home"] = Object.assign({}, HOME_JSON,
      { my_clan: null, my_clan_full: null, invites: [] });
    await window.__ccClansRender();
    await wait(300);
    const mk = [...document.querySelectorAll(".ccC-btn")].find(b => /Create a Clan/i.test(b.textContent));
    out.foundCreateBtn = !!mk;
    if (mk) mk.click();
    await wait(300);
    snapshot("create");
    out.create = {
      icons: q(".ccC-iconpick .ic"),
      privacy: [...document.querySelectorAll(".ccC-btn")].filter(b => /Public|Request|Invite Only/.test(b.textContent)).length,
      hasName: !!document.querySelector("input.ccC-inp"),
      hasDesc: !!document.querySelector("textarea"),
    };
    // Password mode: the option exists, and its field only appears when it is
    // the mode being used — an always-visible password box on a public clan is
    // a question nobody can answer.
    {
      const pwBtn = [...document.querySelectorAll(".ccC-btn")]
        .find(b => /Password/.test(b.textContent));
      const pwField = () => [...document.querySelectorAll(".ccC-field")]
        .find(f => /Clan password/.test((f.querySelector("label") || {}).textContent || ""));
      const shown = (f) => !!f && getComputedStyle(f).display !== "none";
      out.createPw = { optionFound: !!pwBtn, hiddenByDefault: !shown(pwField()) };
      if (pwBtn) {
        pwBtn.click(); await wait(120);
        out.createPw.shownWhenPicked = shown(pwField());
        const inp = pwField() && pwField().querySelector("input");
        out.createPw.hasInput = !!inp;
        // Switching back to a passwordless mode hides the field again. Checked
        // BEFORE founding — a successful create leaves this screen entirely.
        const pubBtn = [...document.querySelectorAll(".ccC-btn")]
          .find(b => /🌊 Public/.test(b.textContent));
        if (pubBtn) {
          pubBtn.click(); await wait(120);
          out.createPw.hiddenAgain = !shown(pwField());
          if (pwBtn) { pwBtn.click(); await wait(120); }   // back to Password to found one
        }
        // Founding with password mode must actually send the password.
        if (inp) {
          inp.value = "seahorse7";
          const nameInp = document.querySelector("input.ccC-inp");
          if (nameInp) { nameInp.value = "Locked Lagoon"; }
          const tile = document.querySelector(".ccC-iconpick .ic");
          if (tile) tile.click();
          const found = [...document.querySelectorAll(".ccC-btn")]
            .find(b => /Found this Clan/.test(b.textContent));
          if (found) {
            const before = window.__ccPosts.length;
            found.click(); await wait(300);
            const call = window.__ccPosts.slice(before)
              .find(c => /\\/api\\/clan\\/create$/.test(c.p));
            out.createPw.sentPrivacy = call && call.b ? call.b.privacy : null;
            out.createPw.sentPassword = call && call.b ? call.b.password : null;
          }
        }
      }
    }

    // Joining a PASSWORD clan asks for the word, then joins with it. Nothing on
    // this side checks the password — it only carries it to the server.
    {
      RESPONSES["/api/clan/browse"] = {
        ok: true, season: HOME_JSON.season,
        rows: [{ id: "pw1", name: "Locked Lagoon", icon: "/avatars/clownfish.png",
                 description: "Password clan", privacy: "password", has_password: true,
                 member_count: 3, max_members: 25, points: 40, rank: 4, level: 2 }],
        recommended: [],
      };
      await window.__ccClansRender(); await wait(250);
      const browseBtn = [...document.querySelectorAll(".ccC-cta-btn, .ccC-btn")]
        .find(b => /Join a Clan|Browse Clans|Find a Clan/i.test(b.textContent));
      if (browseBtn) { browseBtn.click(); await wait(350); }
      const rowBtn = [...document.querySelectorAll(".ccC-member .ccC-btn")]
        .find(b => /Join/.test(b.textContent));
      out.pwJoin = { rowBtnText: rowBtn ? rowBtn.textContent.trim() : "" };
      if (rowBtn) {
        const before = window.__ccPosts.length;
        rowBtn.click(); await wait(250);
        // The press must NOT have joined anything on its own.
        out.pwJoin.postedOnPress = window.__ccPosts.slice(before).map(c => c.p);
        const modal = document.querySelector(".ccC-modal-bg");
        out.pwJoin.askedForPassword = !!modal;
        const inp = modal && modal.querySelector("input");
        const go  = modal && [...modal.querySelectorAll(".ccC-btn")]
          .find(b => /Join clan/.test(b.textContent));
        if (inp && go) {
          const b2 = window.__ccPosts.length;
          inp.value = "seahorse7";
          go.click(); await wait(300);
          const call = window.__ccPosts.slice(b2).find(c => /\\/api\\/clan\\/join$/.test(c.p));
          out.pwJoin.sentPassword = call && call.b ? call.b.password : null;
          out.pwJoin.sentClanId   = call && call.b ? call.b.clan_id : null;
        }
      }
    }
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
    `--window-size=${width},${height}`, "--virtual-time-budget=20000",
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
// The season vote decides the clan's icon on the Clans tab, so it may only
// offer critters the clan has unlocked (pool: clownfish + narwhal of the three
// the game offers) and it has to say what winning means.
check("profile: the vote offers only the clan's unlocked critters",
      prof.counts.iconTiles === 2, "tiles=" + prof.counts.iconTiles);
check("profile: the vote says the winner becomes the tab icon",
      (prof.text || "").includes("Clans tab"));
check("profile: the vote shows the running tally",
      /2 votes/.test(prof.text || ""));
check("profile: no em dashes in the vote section", D.voteText && !/—/.test(D.voteText),
      D.voteText);
check("profile: no placeholder junk in text", prof.bad.length === 0, prof.bad.join(","));

// Speed, as behaviour. Opening your own clan is served from the /home payload
// that drew the screen you clicked, and casting a vote is ONE request repainted
// in place — not a vote plus a /home plus a /get with the panel blanked out in
// between. Both of these were the "clans take forever / voting bugs out" bug.
const openCalls = (D.openMyClan || {}).calls || [];
check("open my own clan: no second round trip to the server",
      openCalls.length === 0, openCalls.join(","));
check("open my own clan: never shows a loading placeholder",
      (D.openMyClan || {}).loadingShown === false);
const V = D.vote || {};
check("vote: exactly one request goes out for one ballot",
      (V.calls || []).join(",") === "/api/clan/vote-critter", (V.calls || []).join(","));
check("vote: a double click still casts one ballot",
      (V.calls || []).length === 1, (V.calls || []).join(","));
check("vote: the icon voted for is what was sent",
      V.sent && V.sent.icon === "/avatars/narwhal.png", JSON.stringify(V.sent));
check("vote: the new winner is painted from the vote's own response",
      /Narwhal/.test(V.text || "") && /2 votes/.test(V.text || ""), V.text);
check("vote: the section is still there afterwards (never blanks)",
      V.tiles === 2, "tiles=" + V.tiles);
check("vote: my pick is the one shown as selected",
      /narwhal/i.test(V.selected || ""), V.selected);

const mem = D.screens.members || { counts: {}, bad: [] };
check("members: every member row rendered", mem.counts.members >= 3, "members=" + mem.counts.members);
check("members: roles shown", (mem.text || "").includes("Owner") && (mem.text || "").includes("Captain"));
check("members: custom role shown", (mem.text || "").includes("Reef Keeper"));
check("members: MVP chip shown", (mem.text || "").includes("MVP"));
check("members: join request shown", (mem.text || "").includes("Dana"));
check("members: former contributor shown", (mem.text || "").includes("Quinn"));
check("members: no placeholder junk in text", mem.bad.length === 0, mem.bad.join(","));
check("members: invite box asks for a friend code", /friend code/i.test(mem.text || ""));
check("members: invite box no longer asks for a username",
      !/username/i.test(mem.text || ""));
check("members: the input itself says friend code",
      /friend code/i.test(D.invitePlaceholder || ""), "placeholders=" + (D.invitePlaceholder || ""));

// Typing a code and pressing the button, in a real browser, end to end.
const inv = D.invite || {};
check("invite: the box was found and used", !!inv.sent, JSON.stringify(inv));
check("invite: the typed code is sent as to_code", (inv.sent || {}).to_code === "2809",
      JSON.stringify(inv.sent));
check("invite: no made-up username tags along",
      !(inv.sent || {}).to_name && !(inv.sent || {}).to_uid, JSON.stringify(inv.sent));
check("invite: the toast names who the code resolved to",
      /LemmeSeeThemToes/.test(inv.toast || ""), "toast=" + (inv.toast || ""));
check("invite: the box clears, ready for the next code", inv.cleared === "");

const log = D.screens.log || { counts: {}, bad: [] };
check("activity log: rows rendered", log.counts.activity >= 3, "rows=" + log.counts.activity);
check("activity log: trade line hides the items",
      (log.text || "").includes("completed a clan trade") && !/coins/i.test(log.text || ""));

const lb = D.screens.leaderboard || { counts: {}, bad: [] };
check("leaderboard: a row per clan", lb.counts.rows === 3, "rows=" + lb.counts.rows);
check("leaderboard: featured top three", lb.counts.podium === 3);
check("leaderboard: no placeholder junk in text", lb.bad.length === 0, lb.bad.join(","));

const nc = D.screens.noclan || { text: "" };
check("no clan: shows the join/create call to action",
      (nc.text || "").includes("not in a clan"));
check("no clan: offers the create button", D.foundCreateBtn === true);
const ncj = D.noClanJoin || {};
check("no clan: the clans you can join are listed right there",
      ncj.rows >= 1, "rows=" + ncj.rows);
check("no clan: each one has its own Join button", ncj.hasJoinBtn === true);
check("no clan: and a way through to the full list", ncj.seeAll === true);
check("no clan: pressing Join actually joins",
      (ncj.posted || []).includes("/api/clan/join"), (ncj.posted || []).join(","));

const cr = D.create || {};
check("create: every critter offered as an icon", cr.icons >= 3, "icons=" + cr.icons);
check("create: all three membership settings offered", cr.privacy >= 3, "privacy=" + cr.privacy);
check("create: name + description fields present", cr.hasName && cr.hasDesc);

// ── The two doors, first thing on the tab ──────────────────────────────────
// "make it easier to create a clan, have a create clan button and a join clan
// easier to see when you click on the clan tab."
const cta = D.cta || {};
check("no clan: a Join and a Create button are both on the tab",
      cta.found === true && cta.joinFound === true && cta.makeFound === true,
      JSON.stringify(cta));
check("no clan: they say what they are",
      /Join a Clan/.test(cta.joinText || "") && /Create a Clan/.test(cta.makeText || ""),
      `${cta.joinText} | ${cta.makeText}`);
check("no clan: they are on screen without scrolling", cta.onScreen === true);
check(`no clan: they are real buttons, not links (${cta.joinH}px / ${cta.makeH}px tall)`,
      cta.joinH >= 44 && cta.makeH >= 44);
check(`no clan: they come BEFORE the top-3 podium (top=${cta.topOfTab}px)`,
      cta.abovePodium === true);
check("no clan: they come before the season block too", cta.aboveSeason === true);
check("no clan: pressing Join opens the clan browser", cta.joinOpensBrowse === true);

// ── Password clans: create side ────────────────────────────────────────────
const cpw = D.createPw || {};
check("create: 🔑 Password is offered as a membership setting", cpw.optionFound === true);
check("create: the password box is hidden until Password is picked",
      cpw.hiddenByDefault === true);
check("create: picking Password reveals the box", cpw.shownWhenPicked === true && cpw.hasInput === true);
check("create: leaving Password hides it again", cpw.hiddenAgain === true);
check("create: founding sends privacy=password with the typed password",
      cpw.sentPrivacy === "password" && cpw.sentPassword === "seahorse7",
      JSON.stringify(cpw));

// ── Password clans: join side ──────────────────────────────────────────────
const pwj = D.pwJoin || {};
check("join: a password clan's row offers a Join button", /Join/.test(pwj.rowBtnText || ""),
      "text=" + pwj.rowBtnText);
check("join: pressing it asks for the password instead of joining blind",
      pwj.askedForPassword === true
      && !(pwj.postedOnPress || []).some(p => /\/api\/clan\/join$/.test(p)),
      JSON.stringify(pwj.postedOnPress));
check("join: the typed password is sent to the server with the clan id",
      pwj.sentPassword === "seahorse7" && pwj.sentClanId === "pw1", JSON.stringify(pwj));

const ch = D.chat || {};
check("chat: messages rendered", ch.msgs >= 3, "msgs=" + ch.msgs);
check("chat: own messages styled apart", ch.mine >= 1);
check("chat: system + announcement lines styled apart", ch.system >= 1 && ch.announce >= 1);
check("chat: composer present", !!ch.input);
check("chat: log scrolls inside its own box", !!ch.logScrolls);

check("season countdown is live", D.countdown && D.countdown.ticked, JSON.stringify(D.countdown));

// ── Speed: the tab must not sit on a spinner waiting for the server ────────
const I = D.instant || {};
check("re-opening Clans paints instantly, without waiting for the server",
      I.earlyLen > 200 && !I.earlyLoading,
      `len=${I.earlyLen} loading=${I.earlyLoading}`);
check("...and the real payload still repaints it when it lands",
      I.late > 200, `late=${I.late}`);

// ── Speed: a save is ONE request, and never blanks the panel ───────────────
const SET = D.settings || {};
check("the clan Settings tab is reachable", !!SET.found && !!SET.foundSave);
check("saving settings costs ONE round trip, not two",
      Array.isArray(SET.calls)
      && SET.calls.filter(p => /\/api\/clan\/(home|get)$/.test(p)).length === 1,
      JSON.stringify(SET.calls));
check("...and the panel never blanks to \"Loading clan…\" while it saves",
      SET.blanked === false, String(SET.blanked));

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
