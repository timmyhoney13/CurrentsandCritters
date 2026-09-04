/* ================================================================
 * test_critter_pass_ui.js, the client half of the Critter Pass, in a
 * real browser.
 *
 * js/critter-pass.js is driven headlessly against payloads produced by
 * the REAL critter_pass_server.py, so the two halves cannot drift apart
 * while both look green on their own.
 *
 * What is actually being protected here:
 *
 *   1. The bridge ENVELOPE. post() resolves to { ok, status, data } and
 *      a module that forgets to unwrap `.data` throws mid-render and
 *      leaves a BLANK tab with nothing in the console, the exact way
 *      the Clans tab shipped once. Every stub below returns the real
 *      envelope, never the bare payload, because a bare-payload stub
 *      hides that bug completely.
 *   2. The LOCKED state. A player who has not bought the pass must see
 *      the whole track and the price, and NO claim buttons anywhere.
 *      A Claim button on a locked pass is a promise the server refuses.
 *   3. The purchase card's numbers, which are the sales pitch: they all
 *      come off the served track, so a retune moves them together.
 *   4. __ccPassExtraSlots(), the seam the challenge strip reads
 *      synchronously on every repaint. Unloaded it must report zero,
 *      and it must clamp.
 *   5. Width, at FIVE sizes. A check at one window size once passed
 *      while every screen under 1100px was unusable.
 *
 * WHY THE WIDTHS ARE MEASURED IN IFRAMES
 * Headless Chrome silently clamps --window-size to about 500px wide, so
 * asking for a 390px window gets you a 500px one and a phone is never
 * actually tested. An iframe has its own viewport, and media queries
 * inside it evaluate against ITS width, so these really are 390px.
 *
 *   node test_critter_pass_ui.js
 * ================================================================ */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer", "client");

const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

const read = (rel) => fs.readFileSync(path.join(CLIENT, rel), "utf8");
// The server's own maximum, read out of the module rather than typed here.
const P_MAX_EXTRA = Number(
  (fs.readFileSync(path.join(ROOT, "critter_pass_server.py"), "utf8")
     .match(/^MAX_EXTRA_DAILY = (\d+)$/m) || [])[1]);
const APP = read("js/preview-app.js");
const HTML = read("preview.html");
const PASSJS = read("js/critter-pass.js");
const PASSCSS = read("css/critter-pass.css");

let pass = 0, fail = 0;
const check = (n, c, extra) => {
  if (c) { pass++; console.log("  ✓ " + n); }
  else { fail++; console.log("  ✗ FAIL: " + n + (extra !== undefined ? "  → " + extra : "")); }
};

// ══════════════════════════════════════════════════════════════════════════
//  STATIC WIRING
//  These run with or without Chrome, because a tab that is not wired in is
//  a feature nobody can reach however well it renders.
// ══════════════════════════════════════════════════════════════════════════
console.log("\nwiring: the tab exists and is reachable");
check("the sidebar has a Critter Pass button", HTML.includes('id="snav-critterpass"'));
check("it carries an unclaimed badge like the other tabs",
      HTML.includes('id="snav-critterpass-badge"') && HTML.includes('class="ph-snav-badge"'));
check("its panel and root exist",
      HTML.includes('id="ph-panel-critterpass"') && HTML.includes('id="cc-critter-pass-root"'));
check("it sits directly under the Level Pass, above the Store",
      HTML.indexOf('id="snav-levelpass"') < HTML.indexOf('id="snav-critterpass"')
      && HTML.indexOf('id="snav-critterpass"') < HTML.indexOf('id="snav-store"'));
check("the module and stylesheet are served",
      HTML.includes("/js/critter-pass.js") && HTML.includes("/css/critter-pass.css"));
check("the panel is in the tab map", APP.includes('critterpass:"ph-panel-critterpass"'));
check("switching to it renders it", APP.includes('if (name === "critterpass")  _renderCritterPassTab();'));
check("a module that never loads says so instead of staying blank",
      /function _renderCritterPassTab\(attempt\)/.test(APP)
      && APP.includes("The Critter Pass didn't finish loading"));
check("the bridge exists", APP.includes("window.__ccCritterPass = {"));
check("the bridge can open a confirm modal (4,000 coins is not a mis-tap)",
      /window\.__ccCritterPass = \{[\s\S]{0,1400}?modal:/.test(APP));
check("it is primed on sign-in", APP.includes("window.__ccCritterPassPrime && window.__ccCritterPassPrime()"));
check("it is reset on BOTH identity-change paths",
      (APP.match(/window\.__ccCritterPassReset && window\.__ccCritterPassReset\(\)/g) || []).length === 2);
check("a guest is told what the tab needs an account for",
      /critterpass:\s*"[^"]*account/.test(APP));

console.log("\nwiring: the kelp forest really is the background");
check("the page paints /backgrounds/kelp-forest.png",
      PASSCSS.includes('url("/backgrounds/kelp-forest.png")'));
check("that file exists", fs.existsSync(path.join(CLIENT, "backgrounds", "kelp-forest.png")));
check("a WebP sibling exists for it, like every other served image",
      fs.existsSync(path.join(CLIENT, "backgrounds", "kelp-forest.webp")));
check("the art is under a scrim, so white text on it is legible",
      /--cp-scrim-top/.test(PASSCSS)
      && /linear-gradient\(180deg, var\(--cp-scrim-top\)/.test(PASSCSS));
check("it does not borrow the Level Pass's class names",
      !/\.ccLP-/.test(PASSCSS) && !/ccLP/.test(PASSJS));

console.log("\nwiring: the challenge-slot seam");
check("the pass publishes __ccPassExtraSlots", PASSJS.includes("window.__ccPassExtraSlots = function"));
check("the challenge strip reads it", APP.includes("window.__ccPassExtraSlots ? window.__ccPassExtraSlots()"));
check("the slot count is derived, not hard-coded, for dailies",
      APP.includes("_rollDailyIndices(_dailySlotCount())"));
check("…and for weeklies", APP.includes("_rollWeeklyIndices(_weeklySlotCount())"));
check("a stored day with more than three slots is still valid",
      !/obj\.slots\.length === 3/.test(APP));
check("slots are reconciled on every load, not only when the day rolls",
      /function _csReconcileSlots\(state, want, poolLen\)/.test(APP)
      && (APP.match(/if \(_csReconcileSlots\(state,/g) || []).length === 2);
check("the Tide Sweep still asks for the BASE three, however many you own",
      APP.includes("sweepTarget:  _CS_BASE_SLOTS")
      && APP.includes("sweepTarget:    _CS_BASE_SLOTS")
      && !/completedCount >= 3\b/.test(APP));
check("the strip's copy counts the slots you actually have",
      APP.includes("_csCountWord(total)") && !APP.includes("`Three fresh challenges every day."));
check("the Tide Sweep bar and its caption measure the same thing",
      /const sweepDone = Math\.min\(done, sweepN\);/.test(APP)
      && /sweepPct = Math\.min\(100, Math\.round\(\(sweepDone \/ Math\.max\(1, sweepN\)\)/.test(APP)
      && APP.includes("for the Sweep"));
check("a shrink keeps completed slots before incomplete ones",
      /const done = state\.slots\.filter\(s => s\.completedAt\);[\s\S]{0,200}slice\(0, target\)/.test(APP));

// ══════════════════════════════════════════════════════════════════════════
//  THE SLOTS THEMSELVES, RUN FOR REAL
//  The section above proves the WIRING with source regexes. This runs the
//  shipped slot code: the real _DAILY_CHALLENGES / _WEEKLY_CHALLENGES pools and
//  the real load / reconcile / meta functions are lifted out of preview-app.js
//  by their own markers and executed against a localStorage stub, with
//  __ccPassExtraSlots answering whatever this test says it does.
//
//  Rename one of those functions or move a marker and the lift throws. That is
//  intentional: a regex cannot tell you that six slots actually appear.
// ══════════════════════════════════════════════════════════════════════════
console.log("\nthe slots, run for real");

function lift(from, to) {
  const a = APP.indexOf(from);
  const b = APP.indexOf(to, a + 1);
  if (a < 0 || b < 0) throw new Error(`could not lift ${JSON.stringify(from)} .. ${JSON.stringify(to)}`);
  return APP.slice(a, b);
}

const slotSrc = [
  lift("    const _DAILY_CHALLENGES = [", "\n    const _WEEKLY_CHALLENGES = ["),
  lift("    const _WEEKLY_CHALLENGES = [", "\n    // Meta rewards, auto-fired"),
  lift("    // ── Extra challenge slots (a Critter Pass reward)",
       "    // ── Weekly Swap (a Level Pass reward)"),
  lift('    const _WEEKLY_STATE_KEY = "cc_weekly_state_v1";',
       "    // Mark today as a played day."),
].join("\n");

function runSlots(extra) {
  const vm = require("vm");
  const store = new Map();
  const sandbox = {
    console,
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    ccScopedKey: (k) => "u1::" + k,
    Math, Date, JSON, Number, Object, Array, Set, String,
  };
  sandbox.window = sandbox;
  sandbox.__ccPassExtraSlots = () => extra;
  vm.createContext(sandbox);
  vm.runInContext("(function(){\n" + slotSrc + "\n"
    + "window.__T = { _loadDailyState, _saveDailyState, _getDailyDisplaySlots,"
    + " _getDailyMeta, _loadWeeklyState, _saveWeeklyState, _getWeeklyDisplaySlots,"
    + " _getWeeklyMeta, _DAILY_CHALLENGES, _WEEKLY_CHALLENGES,"
    + " _CS_BASE_SLOTS, _CS_MAX_EXTRA };\n})();", sandbox);
  return { T: sandbox.__T, store };
}

// ── No pass at all: three and three, exactly as before ────────────────────
{
  const { T } = runSlots({ daily: 0, weekly: 0 });
  check("with no pass, a fresh day rolls three dailies", T._getDailyDisplaySlots().length === 3);
  check("with no pass, a fresh week rolls three weeklies", T._getWeeklyDisplaySlots().length === 3);
  const dm = T._getDailyMeta(), wm = T._getWeeklyMeta();
  check("the daily meta counts three", dm.totalCount === 3, dm.totalCount);
  check("the weekly meta counts three", wm.totalCount === 3, wm.totalCount);
  check("the sweep target is three", dm.sweepTarget === 3 && wm.sweepTarget === 3);
  check("three unique dailies, not the same job three times",
        new Set(T._getDailyDisplaySlots().map(s => s.id)).size === 3);
}

// ── The full pass: six and six ────────────────────────────────────────────
{
  const { T } = runSlots({ daily: 3, weekly: 3 });
  const d = T._getDailyDisplaySlots(), w = T._getWeeklyDisplaySlots();
  check("with the pass, a fresh day rolls SIX dailies", d.length === 6, d.length);
  check("with the pass, a fresh week rolls SIX weeklies", w.length === 6, w.length);
  check("all six dailies are different challenges", new Set(d.map(s => s.id)).size === 6);
  check("all six weeklies are different challenges", new Set(w.map(s => s.id)).size === 6);
  check("every one of them is a real challenge with XP on it",
        d.concat(w).every(s => s.id && s.name && Number(s.xp) > 0));
  const dm = T._getDailyMeta();
  check("the meta reports six", dm.totalCount === 6, dm.totalCount);
  check("…but the Tide Sweep still only asks for three", dm.sweepTarget === 3, dm.sweepTarget);
}

// ── The tier is claimed mid-day: the slots GROW, keeping progress ─────────
{
  const { T, store } = runSlots({ daily: 0, weekly: 0 });
  const st = T._loadDailyState();
  st.slots[0].progress = 2;
  st.slots[0].completedAt = 111;
  T._saveDailyState(st);
  const kept = st.slots.map(s => s.idx);

  // Same stored day, same browser, now with two daily tiers claimed.
  const { T: T2 } = (() => {
    const r = runSlots({ daily: 2, weekly: 0 });
    // Re-seed the fresh sandbox with the day we just saved.
    r.store.set("u1::cc_daily_state_v1", store.get("u1::cc_daily_state_v1"));
    return r;
  })();
  const grown = T2._loadDailyState();
  check("claiming a tier grows the day from three slots to five",
        grown.slots.length === 5, grown.slots.length);
  check("the three that were already there are untouched",
        kept.every(idx => grown.slots.some(s => s.idx === idx)));
  check("the completed one is still completed",
        grown.slots.some(s => s.completedAt === 111 && s.progress === 2));
  check("the new ones start empty",
        grown.slots.filter(s => !kept.includes(s.idx)).every(s => !s.completedAt && !s.progress));
  check("no challenge is duplicated by the growth",
        new Set(grown.slots.map(s => s.idx)).size === grown.slots.length);
  const meta = T2._getDailyMeta();
  check("one of five done, and the sweep still wants three",
        meta.completedCount === 1 && meta.totalCount === 5 && meta.sweepTarget === 3,
        JSON.stringify(meta));
}

// ── Signing out: the slots shrink, and completed work survives ────────────
{
  const { T, store } = runSlots({ daily: 3, weekly: 0 });
  const st = T._loadDailyState();
  check("started with six", st.slots.length === 6, st.slots.length);
  // Complete the LAST two, the ones a naive trim-from-the-end would delete.
  st.slots[4].completedAt = 222; st.slots[4].progress = 9;
  st.slots[5].completedAt = 333; st.slots[5].progress = 9;
  T._saveDailyState(st);

  const r = runSlots({ daily: 0, weekly: 0 });
  r.store.set("u1::cc_daily_state_v1", store.get("u1::cc_daily_state_v1"));
  const shrunk = r.T._loadDailyState();
  check("without the pass it drops back to three", shrunk.slots.length === 3, shrunk.slots.length);
  check("both completed challenges survived the shrink",
        shrunk.slots.filter(s => s.completedAt).length === 2,
        JSON.stringify(shrunk.slots.map(s => s.completedAt)));
  check("…and the sweep still reads two of three",
        r.T._getDailyMeta().completedCount === 2);
}

// ── A tampered store cannot buy slots the server never granted ────────────
{
  const { T, store } = runSlots({ daily: 3, weekly: 3 });
  T._loadDailyState();
  const raw = JSON.parse(store.get("u1::cc_daily_state_v1"));
  raw.slots = raw.slots.concat(raw.slots).slice(0, 12);
  store.set("u1::cc_daily_state_v1", JSON.stringify(raw));
  const r = runSlots({ daily: 3, weekly: 3 });
  r.store.set("u1::cc_daily_state_v1", store.get("u1::cc_daily_state_v1"));
  check("a hand-edited twelve-slot day is refused and re-rolled to six",
        r.T._loadDailyState().slots.length === 6, r.T._loadDailyState().slots.length);
}

// ── An unloaded pass is three, never a crash ──────────────────────────────
{
  const vmMod = require("vm");
  const { T } = (() => {
    const r = runSlots({ daily: 1, weekly: 1 });
    return r;
  })();
  check("the base and the max are the numbers the server was told",
        T._CS_BASE_SLOTS === 3 && T._CS_MAX_EXTRA === P_MAX_EXTRA,
        `${T._CS_BASE_SLOTS} / ${T._CS_MAX_EXTRA}`);
  check("one claimed tier is four slots", T._getDailyDisplaySlots().length === 4);
  void vmMod;
}

if (!CHROME) {
  console.log(`\n${pass} passed, ${fail} failed  (SKIPPED the render half: no Chrome/Chromium found)`);
  process.exit(fail ? 1 : 0);
}

// ── Real payloads, straight out of the Python server ──────────────────────
// This is the seam that matters: shapes invented by hand in a test file drift
// from the server the moment somebody renames a field.
function serverPayloads() {
  const script = `
import json, sys
sys.path.insert(0, ${JSON.stringify(ROOT)})
import critter_pass_server as cp
from test_level_pass_server import FakeDb, ArrayUnion, LEVEL_TOTALS, level_progress, xp_for_level

BG = ["/backgrounds/bg-kelp.png", "/backgrounds/bg-coral-reef.png"]
db = FakeDb()
cp.init(get_firestore=lambda: db, verify_token=lambda t: None,
        level_for_xp=level_progress, level_totals=LEVEL_TOTALS, background_paths=list(BG))
cp._transactional = lambda: (lambda fn: fn)
cp._array_union = lambda: ArrayUnion

# LOCKED: level 30, plenty of coins, has NOT bought the pass. The state the
# page is in for everybody the first time they open it.
db.collection("users")._docs["locked"] = {
    "nickname": "Reef Boss",
    "stats": {"total_xp": xp_for_level(30) + 500, "critter_coins": 6200},
}
locked = cp.state_payload("locked")

# LOCKED AND SHORT: the same player, 12 coins to their name.
db.collection("users")._docs["broke"] = {
    "nickname": "Skint", "stats": {"total_xp": xp_for_level(30), "critter_coins": 12},
}
broke = cp.state_payload("broke")

# OWNED, mid-track: bought it, claimed everything up to level 12, two extra
# challenge slots already banked.
db.collection("users")._docs["owner"] = {
    "nickname": "Pass Holder",
    "stats": {"total_xp": xp_for_level(30) + 500, "critter_coins": 2200},
    "critter_pass_seasons": [cp.SEASON_ID],
    "unlocked_icons": ["/avatars/narwhal.png", "/avatars/orca.png"],
}
for t in cp.track():
    if t["level"] <= 12:
        cp.claim(db, "owner", t["id"])
owner = cp.state_payload("owner")

# MAXED: level 100, whole track claimed.
db.collection("users")._docs["maxed"] = {
    "nickname": "Done",
    "stats": {"total_xp": xp_for_level(100), "critter_coins": 0},
    "critter_pass_seasons": [cp.SEASON_ID],
    "unlocked_icons": ["/avatars/a%d.png" % i for i in range(1, 15)],
}
cp.claim_all(db, "maxed")
maxed = cp.state_payload("maxed")

# SIGNED OUT.
out = cp.state_payload(None)

print("@@" + json.dumps({"locked": locked, "broke": broke, "owner": owner,
                         "maxed": maxed, "out": out,
                         "price": cp.CRITTER_PASS_PRICE,
                         "coinTotal": cp.coin_total(),
                         "xpTotal": cp.xp_total(),
                         "maxExtraDaily": cp.MAX_EXTRA_DAILY}) + "@@")
`;
  const out = execFileSync("python3", ["-c", script],
    { encoding: "utf8", cwd: ROOT, maxBuffer: 32 * 1024 * 1024 });
  const m = out.match(/@@([\s\S]*?)@@/);
  if (!m) throw new Error("no payload from critter_pass_server:\n" + out);
  return JSON.parse(m[1]);
}

const P = serverPayloads();
const SRC = { css: PASSCSS, js: PASSJS };
const WIDTHS = [1440, 1280, 1024, 820, 390];

// ── The harness page ──────────────────────────────────────────────────────
// One Chrome run. The outer page builds one iframe per width, writes the whole
// harness into each, and collects the results once they all report in.
const page = `<!doctype html><html><head><meta charset="utf-8">
<style>body{margin:0;background:#eef;font-family:Nunito,sans-serif}
 iframe{display:block;border:0;height:1600px;margin:0 0 8px}</style>
</head><body>
<div id="RESULT" style="display:none"></div>
<script>
window.__SRC = ${JSON.stringify(SRC)};
window.__PAYLOADS = ${JSON.stringify(P)};
window.__WIDTHS = ${JSON.stringify(WIDTHS)};

function innerHtml() {
  const S = window.__SRC;
  return '<!doctype html><html><head><meta charset="utf-8">'
    + '<style>body{margin:0;font-family:Nunito,sans-serif;background:#dff1ff}'
    + '.panel{padding:16px}</style>'
    + '<style>' + S.css + '</style>'
    + '</head><body>'
    + '<button><span id="snav-critterpass-badge" style="display:none">0</span></button>'
    + '<div class="panel"><div id="cc-critter-pass-root"></div></div>'
    + '<scr' + 'ipt>' + BOOT + '</scr' + 'ipt>'
    + '<scr' + 'ipt>' + S.js + '</scr' + 'ipt>'
    + '<scr' + 'ipt>' + MAIN + '</scr' + 'ipt>'
    + '</body></html>';
}

// The bridge. post() resolves to the ENVELOPE the real apiPost returns:
// { ok, status, data }, NOT the bare body. A stub returning the bare body
// would let an unwrap bug sail straight through this test.
const BOOT = \`
  window.__toasts = [];
  window.__posts = [];
  window.__modals = [];
  window.__modalAnswer = { action: "confirm", selected: [] };
  function envelope(data) { return { ok: true, status: 200, data: data }; }
  window.__ccCritterPass = {
    idToken: async () => "tok",
    avSrc: (u) => u,
    toast: (m, t) => window.__toasts.push([m, t]),
    onGranted: () => { window.__granted = (window.__granted || 0) + 1; },
    modal: async (o) => { window.__modals.push(o); return window.__modalAnswer; },
    post: async (p, b) => {
      window.__posts.push([p, b]);
      if (p === "/api/critterpass/state") return envelope(window.__CP_STATE);
      if (p === "/api/critterpass/buy") {
        // The real server flips ownership and the client re-reads rather than
        // guessing, so flip it here too.
        window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.owner));
        return envelope({ ok: true, season: "S1", paid: parent.__PAYLOADS.price });
      }
      if (p === "/api/critterpass/claim") {
        const st = window.__CP_STATE;
        if (!st.claimed.includes(b.tier)) st.claimed = st.claimed.concat([b.tier]);
        return envelope({ ok: true, tier: b.tier, level: 4,
          granted: { type: "coins", coins: 100 }, inventory: st.inventory });
      }
      return envelope({ ok: false, error: "server_error" });
    },
  };
\`;

const MAIN = \`
(async () => {
  const out = { errors: [], locked: {}, broke: {}, owner: {}, maxed: {},
                slots: {}, badge: {}, buy: {}, layout: {} };
  const txt = (el) => (el ? (el.textContent || "").replace(/\\\\s+/g, " ").trim() : "");
  const $$ = (s) => [...document.querySelectorAll(s)];
  try {
    // ── Before ANY state has loaded, the seam must answer zero ───────
    out.slots.beforeLoad = window.__ccPassExtraSlots();

    // ── LOCKED ───────────────────────────────────────────────────────
    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.locked));
    await window.__ccCritterPassRender();
    const root = document.getElementById("cc-critter-pass-root");
    out.locked = {
      tiers: $$(".ccCP-tier").length,
      claimBtns: $$(".ccCP-claim").length,
      claimAll: $$("#ccCP-claimall").length,
      waiting: $$(".ccCP-tier.is-waiting").length,
      locked: $$(".ccCP-tier.is-locked").length,
      buyCard: $$(".ccCP-buycard").length,
      buyEnabled: !!(document.getElementById("ccCP-buy") && !document.getElementById("ccCP-buy").disabled),
      buyText: txt(document.getElementById("ccCP-buy")),
      buyTitle: txt(document.querySelector(".ccCP-buy-title")),
      buySub: txt(document.querySelector(".ccCP-buy-sub")),
      highlights: $$(".ccCP-hl").map(txt),
      finaleArt: !!document.querySelector(".ccCP-buy-art img"),
      level: txt(document.querySelector(".ccCP-lvl-num")),
      ownedPill: $$(".ccCP-owned-pill").length,
      next: txt(document.querySelector(".ccCP-next")),
      togo: $$(".ccCP-tier-togo").slice(0, 2).map(txt),
      rootClass: (root.querySelector(".ccCP") || {}).className || "",
    };
    out.badge.whenLocked = document.getElementById("snav-critterpass-badge").style.display;
    out.slots.whenLocked = window.__ccPassExtraSlots();

    // ── LOCKED AND SHORT OF COINS ────────────────────────────────────
    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.broke));
    await window.__ccCritterPassSync();
    out.broke = {
      buyDisabled: !!(document.getElementById("ccCP-buy") && document.getElementById("ccCP-buy").disabled),
      note: txt(document.querySelector(".ccCP-buy-note")),
      claimBtns: $$(".ccCP-claim").length,
    };

    // ── BUYING IT ────────────────────────────────────────────────────
    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.locked));
    await window.__ccCritterPassSync();
    const buyBtn = document.getElementById("ccCP-buy");
    if (buyBtn) {
      window.__modalAnswer = { action: "cancel", selected: [] };
      buyBtn.click();
      for (let i = 0; i < 12; i++) await new Promise(r => setTimeout(r, 0));
      out.buy.cancelledPosts = window.__posts.filter(p => p[0] === "/api/critterpass/buy").length;
      out.buy.askedFirst = window.__modals.length;
      out.buy.modalMentionsPrice = /4,000/.test(JSON.stringify(window.__modals[0] || {}));

      window.__modalAnswer = { action: "confirm", selected: [] };
      const b2 = document.getElementById("ccCP-buy");
      if (b2) b2.click();
      for (let i = 0; i < 24; i++) await new Promise(r => setTimeout(r, 0));
      out.buy.confirmedPosts = window.__posts.filter(p => p[0] === "/api/critterpass/buy").length;
      out.buy.buyCardGone = $$(".ccCP-buycard").length === 0;
      out.buy.owned = window.__ccCritterPassOwned();
    }

    // ── OWNED, mid-track ─────────────────────────────────────────────
    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.owner));
    await window.__ccCritterPassSync();
    out.owner = {
      buyCard: $$(".ccCP-buycard").length,
      claimBtns: $$(".ccCP-claim").length,
      ready: $$(".ccCP-tier.is-ready").length,
      claimed: $$(".ccCP-tier.is-claimed").length,
      waiting: $$(".ccCP-tier.is-waiting").length,
      claimOnLocked: $$(".ccCP-tier.is-locked .ccCP-claim").length,
      claimOnClaimed: $$(".ccCP-tier.is-claimed .ccCP-claim").length,
      ownedPill: txt(document.querySelector(".ccCP-owned-pill")),
      claimAll: txt(document.querySelector("#ccCP-claimall")),
      chips: $$(".ccCP-chip-txt").map(txt),
      perks: $$(".ccCP-tier.is-perk").length,
      finale: $$(".ccCP-tier.is-finale").length,
      railScrolls: (() => { const r = document.getElementById("ccCP-rail");
                            return r ? r.scrollWidth > r.clientWidth : false; })(),
      heights: [...new Set($$(".ccCP-tier").slice(0, 12)
                  .map(t => Math.round(t.getBoundingClientRect().height)))],
      minTierW: Math.min(...$$(".ccCP-tier").map(t => Math.round(t.getBoundingClientRect().width))),
    };
    out.badge.whenOwned = (() => {
      const b = document.getElementById("snav-critterpass-badge");
      return b.style.display === "none" ? null : txt(b);
    })();
    out.slots.whenOwned = window.__ccPassExtraSlots();

    // The badge must come DOWN when a reward is claimed, or it reads as broken.
    const firstClaim = document.querySelector(".ccCP-claim");
    if (firstClaim) {
      firstClaim.click();
      for (let i = 0; i < 12; i++) await new Promise(r => setTimeout(r, 0));
      out.badge.afterOneClaim = (() => {
        const b = document.getElementById("snav-critterpass-badge");
        return b.style.display === "none" ? null : txt(b);
      })();
    }

    // ── MAXED ────────────────────────────────────────────────────────
    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.maxed));
    await window.__ccCritterPassSync();
    out.maxed = {
      claimBtns: $$(".ccCP-claim").length,
      claimed: $$(".ccCP-tier.is-claimed").length,
      allclear: txt(document.querySelector(".ccCP-allclear")),
      next: txt(document.querySelector(".ccCP-next")),
      badge: document.getElementById("snav-critterpass-badge").style.display,
    };
    out.slots.whenMaxed = window.__ccPassExtraSlots();

    // A tampered cache must not be able to ask for nine daily challenges.
    window.__CP_STATE.inventory.extraDaily = 99;
    await window.__ccCritterPassSync();
    out.slots.clamped = window.__ccPassExtraSlots();

    // Signing out drops it all, including the extra slots.
    window.__ccCritterPassReset();
    out.slots.afterSignOut = window.__ccPassExtraSlots();
    out.badge.afterSignOut = document.getElementById("snav-critterpass-badge").style.display;

    // ── Layout, measured at THIS iframe's real width ─────────────────
    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.locked));
    await window.__ccCritterPassSync();
    const vw = window.innerWidth;
    const overflow = [];
    document.querySelectorAll("#cc-critter-pass-root *").forEach(el => {
      // Anything inside a scroller is allowed to be wider than the window,
      // that is what the scroller is for. Everything else is a real overflow.
      // Only auto/scroll counts. "hidden" was in this list and made the whole
      // check vacuous: .ccCP itself is overflow:hidden, so EVERY descendant was
      // excused and the overflow array was always empty. Content clipped by a
      // hidden parent is a bug too, so it belongs in the count.
      let p = el.parentElement, inScroller = false;
      while (p && p !== document.body) {
        const ov = getComputedStyle(p).overflowX;
        if (ov === "auto" || ov === "scroll") { inScroller = true; break; }
        p = p.parentElement;
      }
      if (inScroller) return;
      const r = el.getBoundingClientRect();
      if (r.right > vw + 1) {
        overflow.push(((el.className || "") + "").split(" ")[0] + "@" + Math.round(r.right));
      }
    });
    const page = document.querySelector(".ccCP");
    const buy = document.getElementById("ccCP-buy");
    out.layout = {
      vw,
      docScrollsSideways: document.documentElement.scrollWidth > vw + 1,
      overflow: overflow.slice(0, 6),
      pageW: page ? Math.round(page.getBoundingClientRect().width) : 0,
      // The kelp art really is painted, at every width.
      hasKelp: page ? /kelp-forest/.test(getComputedStyle(page).backgroundImage) : false,
      buyW: buy ? Math.round(buy.getBoundingClientRect().width) : 0,
      buyH: buy ? Math.round(buy.getBoundingClientRect().height) : 0,
      // The header has to stay inside the page, not spill out of the art.
      headW: (() => { const h = document.querySelector(".ccCP-head");
                      return h ? Math.round(h.getBoundingClientRect().width) : 0; })(),
    };
  } catch (e) {
    out.errors.push("THREW: " + (e && e.message ? e.message : String(e)));
  }
  window.__RESULT = out;
})();
\`;

(async () => {
  const results = {};
  for (const w of window.__WIDTHS) {
    const ifr = document.createElement("iframe");
    ifr.width = w;
    document.body.appendChild(ifr);
    const doc = ifr.contentDocument;
    doc.open(); doc.write(innerHtml()); doc.close();
    for (let i = 0; i < 500 && !ifr.contentWindow.__RESULT; i++) {
      await new Promise(r => setTimeout(r, 25));
    }
    results[w] = ifr.contentWindow.__RESULT || { errors: ["never reported in"] };
  }
  document.getElementById("RESULT").textContent = "@@" + JSON.stringify(results) + "@@";
})();
</script>
</body></html>`;

const file = path.join(os.tmpdir(), `cc_critterpass_ui_${Date.now()}.html`);
fs.writeFileSync(file, page);

const dom = execFileSync(CHROME, [
  "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
  "--window-size=1600,1200", "--virtual-time-budget=60000",
  "--dump-dom", "file://" + file,
], { encoding: "utf8", maxBuffer: 128 * 1024 * 1024, stdio: ["pipe", "pipe", "ignore"] });

const m = dom.match(/@@([\s\S]*?)@@/);
if (!m) { console.error("no result payload in the DOM dump"); process.exit(1); }
const R = JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                         .replace(/&lt;/g, "<").replace(/&gt;/g, ">"));

// ══════════════════════════════════════════════════════════════════════════
const D = R[1280];
console.log("\ndesktop (1280px): the module runs");
check("the module rendered without throwing", D.errors.length === 0, D.errors.join(" | "));
check("the iframe really is 1280 wide", D.layout.vw === 1280, D.layout.vw);

console.log("\nlocked: the whole track, readable, with nothing claimable");
const serverTiers = P.locked.track.length;
check("every server tier rendered", D.locked.tiers === serverTiers,
      `rendered ${D.locked.tiers} of ${serverTiers}`);
check("NO claim buttons anywhere", D.locked.claimBtns === 0, D.locked.claimBtns);
check("no Claim-all button either", D.locked.claimAll === 0);
check("no Unlocked pill", D.locked.ownedPill === 0);
check("tiers already reached say what to do about it", D.locked.waiting > 0, D.locked.waiting);
check("tiers not yet reached are locked", D.locked.locked > 0, D.locked.locked);
check("waiting + locked accounts for the whole track",
      D.locked.waiting + D.locked.locked === serverTiers,
      `${D.locked.waiting}+${D.locked.locked} vs ${serverTiers}`);
check("the player's own level is shown", D.locked.level === "30", D.locked.level);
check("locked tiers still say how much XP is to go",
      D.locked.togo.length === 2 && D.locked.togo.every(t => /XP to go$/.test(t)),
      JSON.stringify(D.locked.togo));
check("the page knows it is locked", /is-locked/.test(D.locked.rootClass), D.locked.rootClass);

console.log("\nlocked: the purchase card is the sales pitch");
check("the purchase card is shown", D.locked.buyCard === 1);
check("the button is live for somebody who can afford it", D.locked.buyEnabled);
check("the price on the button is the server's price",
      D.locked.buyText.includes(P.price.toLocaleString()),
      `${D.locked.buyText} vs ${P.price}`);
check("the pitch counts the real number of rewards, not a typed one",
      D.locked.buySub.includes(`${serverTiers} rewards`),
      `${D.locked.buySub} vs ${serverTiers}`);
check("the pitch names the coins the track pays back",
      D.locked.buySub.includes(P.coinTotal.toLocaleString()),
      `${D.locked.buySub} vs ${P.coinTotal}`);
check("8,500 really is what the track pays", P.coinTotal === 8500, P.coinTotal);
check("4,000 really is what it costs", P.price === 4000, P.price);
const hl = D.locked.highlights.join(" | ");
check("the highlights name the coin total", hl.includes(P.coinTotal.toLocaleString()), hl);
check("…the XP total", hl.includes(P.xpTotal.toLocaleString()), hl);
check("…the extra daily challenges", /\+3\s*daily challenges/i.test(hl), hl);
check("…the extra weekly challenges", /\+3\s*weekly challenges/i.test(hl), hl);
check("…the emotes", /emote/i.test(hl), hl);
check("…and the Level 100 critter", /Level 100/.test(hl), hl);
check("the finale critter's art is on the card", D.locked.finaleArt);
check("the badge is hidden while the pass is locked", D.badge.whenLocked === "none");

console.log("\nlocked and short of coins");
check("the button is disabled", D.broke.buyDisabled);
check("it says how many coins short", /short/i.test(D.broke.note), D.broke.note);
check("still no claim buttons", D.broke.claimBtns === 0);

console.log("\nbuying it");
check("it asks before it spends 4,000 coins", D.buy.askedFirst >= 1, D.buy.askedFirst);
check("the question names the price", D.buy.modalMentionsPrice === true);
check("cancelling posts nothing", D.buy.cancelledPosts === 0, D.buy.cancelledPosts);
check("confirming posts exactly one buy", D.buy.confirmedPosts === 1, D.buy.confirmedPosts);
check("the purchase card goes away once it is bought", D.buy.buyCardGone === true);
check("__ccCritterPassOwned() reports the truth after a buy", D.buy.owned === true);

console.log("\nowned, mid-track");
check("the purchase card is gone", D.owner.buyCard === 0);
check("the Unlocked pill is shown", D.owner.ownedPill === "Unlocked", D.owner.ownedPill);
check("nothing says 'unlock to claim' any more", D.owner.waiting === 0, D.owner.waiting);
check("reached-and-unclaimed tiers have a Claim button",
      D.owner.claimBtns > 0 && D.owner.claimBtns === D.owner.ready,
      `${D.owner.claimBtns} buttons vs ${D.owner.ready} ready`);
check("no Claim button on a locked tier", D.owner.claimOnLocked === 0);
check("no Claim button on a claimed tier", D.owner.claimOnClaimed === 0);
check("already-claimed tiers show as claimed",
      D.owner.claimed === P.owner.claimed.length,
      `${D.owner.claimed} vs ${P.owner.claimed.length}`);
check("Claim-all offers the right count",
      D.owner.claimAll === `Claim ${D.owner.ready} reward${D.owner.ready === 1 ? "" : "s"}`,
      D.owner.claimAll);
check("the six perk tiers read as perks", D.owner.perks === 6, D.owner.perks);
check("the level-100 critter reads as the finale", D.owner.finale === 1, D.owner.finale);
check("the chips report the extra challenge slots",
      D.owner.chips.join(" | ").includes("Daily Challenge")
      && D.owner.chips.join(" | ").includes("Weekly Challenge"),
      D.owner.chips.join(" | "));
check("the rail scrolls rather than squashing 58 tiers", D.owner.railScrolls === true);
check("every tier card is the same height, so the track line lines up",
      D.owner.heights.length === 1, JSON.stringify(D.owner.heights));

console.log("\nthe sidebar badge");
check("it shows the claimable count when the pass is owned",
      D.badge.whenOwned === String(D.owner.ready), `${D.badge.whenOwned} vs ${D.owner.ready}`);
check("it comes DOWN when a reward is claimed",
      Number(D.badge.afterOneClaim) === D.owner.ready - 1,
      `${D.badge.afterOneClaim} vs ${D.owner.ready - 1}`);
check("it is gone, not zero, on a fully-claimed pass", D.maxed.badge === "none");
check("it is gone on sign-out", D.badge.afterSignOut === "none");

console.log("\nmaxed out");
check("nothing left to claim", D.maxed.claimBtns === 0);
check("every tier reads as claimed", D.maxed.claimed === serverTiers,
      `${D.maxed.claimed} of ${serverTiers}`);
check("it says so instead of offering a dead button",
      /Nothing to claim/i.test(D.maxed.allclear), D.maxed.allclear);
check("the headline stops asking for XP that does not exist",
      /yours/i.test(D.maxed.next), D.maxed.next);

console.log("\n__ccPassExtraSlots(), the seam the challenge strip reads");
check("zero before anything has loaded",
      D.slots.beforeLoad.daily === 0 && D.slots.beforeLoad.weekly === 0,
      JSON.stringify(D.slots.beforeLoad));
check("zero for a player who has not bought the pass",
      D.slots.whenLocked.daily === 0 && D.slots.whenLocked.weekly === 0,
      JSON.stringify(D.slots.whenLocked));
check("the claimed slots for an owner who has them",
      D.slots.whenOwned.daily === P.owner.inventory.extraDaily
      && D.slots.whenOwned.weekly === P.owner.inventory.extraWeekly,
      JSON.stringify(D.slots.whenOwned));
check("all three of each on a maxed pass",
      D.slots.whenMaxed.daily === P.maxExtraDaily && D.slots.whenMaxed.weekly === P.maxExtraDaily,
      JSON.stringify(D.slots.whenMaxed));
check("a tampered count is clamped to the server's maximum",
      D.slots.clamped.daily === P.maxExtraDaily, JSON.stringify(D.slots.clamped));
check("back to zero on sign-out, so the next account inherits nothing",
      D.slots.afterSignOut.daily === 0 && D.slots.afterSignOut.weekly === 0,
      JSON.stringify(D.slots.afterSignOut));

// ══════════════════════════════════════════════════════════════════════════
console.log("\nthe page, at every screen width");
const MIN_TIER_W = 140;   // below this a tier's name and blurb stop being readable
const MIN_TAP = 40;       // a button below this is not a tap target
for (const w of WIDTHS) {
  const r = R[w] || { errors: ["missing"] };
  const label = `${String(w).padStart(4)}px`;
  check(`${label}: rendered without throwing`, (r.errors || []).length === 0,
        (r.errors || []).join(" | "));
  if (!r.layout) continue;
  check(`${label}: the iframe really is that wide`, r.layout.vw === w, r.layout.vw);
  check(`${label}: the page does not scroll sideways`,
        r.layout.docScrollsSideways === false);
  check(`${label}: nothing spills out of the page`,
        (r.layout.overflow || []).length === 0, (r.layout.overflow || []).join(", "));
  check(`${label}: the kelp forest is painted`, r.layout.hasKelp === true);
  check(`${label}: the header fits inside the page`,
        r.layout.headW > 0 && r.layout.headW <= r.layout.pageW,
        `${r.layout.headW} in ${r.layout.pageW}`);
  check(`${label}: the Unlock button is a real tap target`,
        r.layout.buyH >= MIN_TAP, r.layout.buyH + "px tall");
  check(`${label}: tiers stay wide enough to read`,
        (r.owner || {}).minTierW >= MIN_TIER_W, (r.owner || {}).minTierW + "px");
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
