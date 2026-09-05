/* ================================================================
 * test_level_pass_ui.js, the client half of the Level Pass, the
 * referral card and the Game Night banner, in a real browser.
 *
 * The three modules are driven headlessly against payloads produced by
 * the REAL level_pass_server.py / referral_server.py, so the two halves
 * cannot drift apart while both look green on their own.
 *
 * What is actually being protected here:
 *
 *   1. The bridge ENVELOPE. post() resolves to { ok, status, data } and
 *      a module that forgets to unwrap `.data` throws mid-render and
 *      leaves a BLANK tab with nothing in the console, the exact way
 *      the Clans tab shipped once. Every stub below returns the real
 *      envelope, never the bare payload, because a bare-payload stub
 *      hides that bug completely.
 *   2. "N XP to go" / "N XP until …", the whole point of both surfaces.
 *      A locked tier that says nothing is the feature missing.
 *   3. Claim buttons appearing ONLY on tiers that are reached and
 *      unclaimed. A Claim button on a locked tier is a promise the
 *      server will refuse.
 *   4. Width, at FIVE sizes. A check at one window size once passed
 *      while every screen under 1100px was unusable.
 *
 * WHY THE WIDTHS ARE MEASURED IN IFRAMES
 * Headless Chrome silently clamps --window-size to about 500px wide, so
 * asking for a 390px window gets you a 500px one and a phone is never
 * actually tested. An iframe has its own viewport, and media queries
 * inside it evaluate against ITS width, so these really are 390px.
 *
 *   node test_level_pass_ui.js
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

if (!CHROME) {
  console.log("SKIP: no Chrome/Chromium found: cannot run the level-pass render check.");
  process.exit(0);
}

const read = (rel) => fs.readFileSync(path.join(CLIENT, rel), "utf8");
const SRC = {
  passCss: read("css/level-pass.css"),
  gnCss: read("css/game-night.css"),
  passJs: read("js/level-pass.js"),
  refJs: read("js/referral.js"),
  gnJs: read("js/game-night.js"),
};

const WIDTHS = [1440, 1280, 1024, 820, 390];

// ── Real payloads, straight out of the Python servers ─────────────────────
// This is the seam that matters: shapes invented by hand in a test file drift
// from the server the moment somebody renames a field.
function serverPayloads() {
  const script = `
import json, sys, time
sys.path.insert(0, ${JSON.stringify(ROOT)})
import level_pass_server as lp, referral_server as rs
from test_level_pass_server import (FakeDb, ArrayUnion, LEVEL_TOTALS,
                                    level_progress, BACKGROUNDS, xp_for_level)

db = FakeDb()
lp.init(get_firestore=lambda: db, verify_token=lambda t: None,
        level_for_xp=level_progress, level_totals=LEVEL_TOTALS,
        background_paths=list(BACKGROUNDS))
lp._transactional = lambda: (lambda fn: fn)
lp._array_union = lambda: ArrayUnion

# A player mid-track: level 22, the early tiers already claimed, one boost held
# and one Weekly Swap, so every chip state gets exercised.
db.collection("users")._docs["u1"] = {
    "nickname": "Reef Boss",
    "stats": {"total_xp": xp_for_level(22) + 700, "critter_coins": 1234},
    "xp_boosts": 1, "weekly_reroll_tokens": 1, "streak_shields": 2,
}
for t in lp.track():
    if t["claimable"] and t["level"] <= 5:
        lp.claim(db, "u1", t["id"])
state = lp.state_payload("u1")

# A boosted player, for the live chip.
db.collection("users")._docs["u2"] = {
    "nickname": "Boosted",
    "stats": {"total_xp": xp_for_level(40), "critter_coins": 10},
    "xp_boost_until": int(time.time() * 1000) + 5 * 3600 * 1000,
}
boosted = lp.state_payload("u2")

# A brand-new account, for the empty end of the track.
db.collection("users")._docs["u3"] = {
    "nickname": "Fresh", "stats": {"total_xp": 0, "critter_coins": 0},
}
fresh = lp.state_payload("u3")

# THE OUTAGE PAYLOAD, produced by the real server with a Firestore that
# refuses: level 1, accountRead False. Built here rather than hand-edited so
# a rename of the flag fails this test instead of quietly disarming it.
class _Refuses:
    def collection(self, *a, **k):
        raise RuntimeError("429 Quota exceeded")

_real_get = lp._get_firestore
lp._get_firestore = lambda: _Refuses()
unreadable = lp.state_payload("u1")
lp._get_firestore = _real_get
assert unreadable["accountRead"] is False, unreadable["accountRead"]
assert unreadable["level"] == 1, unreadable["level"]

# What the APP knows while that read is failing: this account is level 39.
live_total_xp = xp_for_level(39) + 1550

rdb = FakeDb()
rs.init(get_firestore=lambda: rdb, verify_token=lambda t: None,
        background_paths=list(BACKGROUNDS))
rdb.collection("users")._docs["r1"] = {
    "nickname": "Reef Boss", "friend_code": "4985",
    "stats": {"critter_coins": 300}, "referral_count": 3,
}
referral = rs.state_payload("r1")

print("@@" + json.dumps({"state": state, "boosted": boosted, "fresh": fresh,
                         "unreadable": unreadable, "liveTotalXp": live_total_xp,
                         "referral": referral}) + "@@")
`;
  const out = execFileSync("python3", ["-c", script],
    { encoding: "utf8", cwd: ROOT, maxBuffer: 32 * 1024 * 1024 });
  const m = out.match(/@@([\s\S]*?)@@/);
  if (!m) throw new Error("no payload from the python servers:\n" + out);
  return JSON.parse(m[1]);
}

const P = serverPayloads();

// ── The harness page ──────────────────────────────────────────────────────
// One Chrome run. The outer page builds one iframe per width, writes the whole
// harness into each, and collects the results once they all report in.
const page = `<!doctype html><html><head><meta charset="utf-8">
<style>body{margin:0;background:#eef;font-family:Nunito,sans-serif}
 iframe{display:block;border:0;height:1400px;margin:0 0 8px}</style>
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
    + '<style>' + S.passCss + '</style>'
    + '<style>' + S.gnCss + '</style>'
    + '</head><body>'
    + '<div class="panel" id="ph-panel-overview"></div>'
    + '<button><span id="snav-levelpass-badge" style="display:none">0</span></button>'
    + '<div class="panel"><div id="cc-level-pass-root"></div></div>'
    + '<div class="panel" id="ph-panel-friends"></div>'
    + '<scr' + 'ipt>' + BOOT + '</scr' + 'ipt>'
    + '<scr' + 'ipt>' + S.passJs + '</scr' + 'ipt>'
    + '<scr' + 'ipt>' + S.refJs + '</scr' + 'ipt>'
    + '<scr' + 'ipt>' + S.gnJs + '</scr' + 'ipt>'
    + '<scr' + 'ipt>' + MAIN + '</scr' + 'ipt>'
    + '</body></html>';
}

// Bridges. post() resolves to the ENVELOPE the real apiPost returns:
// { ok, status, data }, NOT the bare body. A stub returning the bare body
// would let an unwrap bug sail straight through this test.
const BOOT = \`
  window.__toasts = [];
  window.__posts = [];
  function envelope(data) { return { ok: true, status: 200, data: data }; }
  window.__ccWeekStartMs = function () {
    const now = new Date(), day = now.getDay();
    const since = day === 0 ? 6 : day - 1;
    return new Date(now.getFullYear(), now.getMonth(), now.getDate() - since).getTime();
  };
  window.__ccLevelPass = {
    idToken: async () => "tok",
    avSrc: (u) => u,
    toast: (m, t) => window.__toasts.push([m, t]),
    onGranted: () => { window.__granted = (window.__granted || 0) + 1; },
    post: async (p, b) => {
      window.__posts.push([p, b]);
      if (p === "/api/pass/state") return envelope(window.__PASS_STATE);
      if (p === "/api/pass/claim") {
        // The real server records the claim, and the client re-reads rather
        // than guessing. Record it here too, so the badge is tested against a
        // state that actually changed.
        const st = window.__PASS_STATE;
        if (!st.claimed.includes(b.tier)) st.claimed = st.claimed.concat([b.tier]);
        return envelope({ ok: true, tier: b.tier, level: 2,
          granted: { type: "coins", coins: 50 }, inventory: st.inventory });
      }
      return envelope({ ok: false, error: "server_error" });
    },
  };
  window.__ccReferral = {
    idToken: async () => "tok",
    toast: (m, t) => window.__toasts.push([m, t]),
    onRedeemed: () => {},
    post: async (p) => {
      if (p === "/api/referral/state") return envelope(window.__REF_STATE);
      return envelope({ ok: false, error: "server_error" });
    },
  };
\`;

const MAIN = \`
(async () => {
  const out = { errors: [], pass: {}, boost: {}, fresh: {}, referral: {}, gn: {}, layout: {} };
  const txt = (el) => (el ? (el.textContent || "").replace(/\\\\s+/g, " ").trim() : "");
  try {
    window.__PASS_STATE = parent.__PAYLOADS.state;
    window.__REF_STATE = parent.__PAYLOADS.referral;

    await window.__ccLevelPassRender();
    const root = document.getElementById("cc-level-pass-root");
    const tiers = [...root.querySelectorAll(".ccLP-tier")];
    out.pass = {
      tiers: tiers.length,
      ready: root.querySelectorAll(".ccLP-tier.is-ready").length,
      claimed: root.querySelectorAll(".ccLP-tier.is-claimed").length,
      locked: root.querySelectorAll(".ccLP-tier.is-locked").length,
      milestones: root.querySelectorAll(".ccLP-tier.is-milestone").length,
      claimBtns: root.querySelectorAll(".ccLP-claim").length,
      claimAll: txt(root.querySelector(".ccLP-claimall")),
      next: txt(root.querySelector(".ccLP-next")),
      togo: [...root.querySelectorAll(".ccLP-tier-togo")].map(txt).slice(0, 3),
      level: txt(root.querySelector(".ccLP-lvl-num")),
      barTxt: txt(root.querySelector(".ccLP-bar-txt")),
      chips: [...root.querySelectorAll(".ccLP-chip-txt")].map(txt),
      claimOnLocked: root.querySelectorAll(".ccLP-tier.is-locked .ccLP-claim").length,
      claimOnClaimed: root.querySelectorAll(".ccLP-tier.is-claimed .ccLP-claim").length,
      railScrolls: (() => { const r = document.getElementById("ccLP-rail");
                            return r ? r.scrollWidth > r.clientWidth : false; })(),
      heights: [...new Set(tiers.slice(0, 12).map(t => Math.round(t.getBoundingClientRect().height)))],
      minTierW: Math.min(...tiers.map(t => Math.round(t.getBoundingClientRect().width))),
    };

    // ── The sidebar's unclaimed badge ────────────────────────────────
    // The number a player sees on the Level Pass tab before they open it. It
    // has to match what is actually claimable, come DOWN when one is claimed,
    // and disappear when there is nothing left. A badge that cannot be cleared
    // is the failure this measures.
    const badge = document.getElementById("snav-levelpass-badge");
    const badgeNow = () => (badge.style.display === "none" ? null : (badge.textContent || "").trim());
    out.badge = { atLoad: badgeNow(), readyAtLoad: out.pass.ready };

    const firstClaim = document.querySelector(".ccLP-claim");
    if (firstClaim) {
      firstClaim.click();
      // The claim posts, resyncs and repaints; give the microtasks a turn.
      for (let i = 0; i < 8; i++) await new Promise(r => setTimeout(r, 0));
      out.badge.afterOneClaim = badgeNow();
      out.badge.readyAfter = document.querySelectorAll(".ccLP-tier.is-ready").length;
    }

    // Nothing claimable at all: the badge must be GONE, not a zero.
    window.__PASS_STATE = parent.__PAYLOADS.fresh;
    await window.__ccLevelPassSync();
    out.badge.whenNothingReady = badgeNow();
    out.badge.rawTextWhenHidden = (badge.textContent || "").trim();

    // Signing out drops the cached state, and the badge with it.
    window.__ccLevelPassReset();
    out.badge.afterSignOut = badgeNow();

    // A brand-new account: nothing claimed, everything ahead.
    window.__PASS_STATE = parent.__PAYLOADS.fresh;
    await window.__ccLevelPassSync();
    out.fresh = {
      ready: document.querySelectorAll(".ccLP-tier.is-ready").length,
      claimBtns: document.querySelectorAll(".ccLP-claim").length,
      allclear: txt(document.querySelector(".ccLP-allclear")),
      next: txt(document.querySelector(".ccLP-next")),
    };

    // ── A REFUSING DATABASE ──────────────────────────────────────────
    // The server could not read the account, so its payload carries level 1
    // and accountRead:false. The app itself is NOT in the dark: it loaded the
    // profile at sign-in and the header is painting Level 39 right now. The
    // pass has to agree with the header, because "the Level Pass says I am
    // Level 1" is what a whole day of Firestore refusals looked like.
    window.__fishGetMyStats = () => ({ total_xp: parent.__PAYLOADS.liveTotalXp });
    window.__fishStoredTotalXp = (st) => Number(st && st.total_xp) || 0;
    window.__PASS_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.unreadable));
    await window.__ccLevelPassSync();
    out.unreadable = {
      level: txt(document.querySelector(".ccLP-lvl-num")),
      barTxt: txt(document.querySelector(".ccLP-bar-txt")),
      ready: document.querySelectorAll(".ccLP-tier.is-ready").length,
    };

    // …and a read that SUCCEEDED stays authoritative, even though the same
    // live stats are still sitting there. A browser copy does not get to
    // overrule the account the payout is derived from.
    window.__PASS_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.fresh));
    await window.__ccLevelPassSync();
    out.serverWins = { level: txt(document.querySelector(".ccLP-lvl-num")) };
    delete window.__fishGetMyStats;
    delete window.__fishStoredTotalXp;

    window.__PASS_STATE = parent.__PAYLOADS.boosted;
    await window.__ccLevelPassSync();
    out.boost = {
      chip: txt(document.querySelector(".ccLP-chip.is-live")),
      apiActive: window.__ccPassBoost().active,
      apiMult: window.__ccPassBoost().mult,
      apiPercent: window.__ccPassBoost().percent,
    };

    await window.__ccReferralRender();
    const rf = document.getElementById("cc-referral-root");
    out.referral = {
      text: txt(rf),
      code: txt(rf.querySelector(".ccRF-code")),
      goal: txt(rf.querySelector(".ccRF-prog-goal")),
      hasInput: !!rf.querySelector("#ccRF-code"),
      barPct: (rf.querySelector(".ccRF-bar-fill") || {}).style
              ? rf.querySelector(".ccRF-bar-fill").style.width : "",
      selfInjected: rf.parentElement && rf.parentElement.id === "ph-panel-friends",
    };

    window.__ccGameNightRender();
    const gn = document.getElementById("cc-game-night");
    out.gn = {
      text: txt(gn),
      href: (gn.querySelector(".ccGN-btn") || {}).href || "",
      btnLabel: txt(gn.querySelector(".ccGN-btn")),
      xpChip: txt(gn.querySelector(".ccGN-xp")),
      selfInjected: gn.parentElement && gn.parentElement.id === "ph-panel-overview",
      firstChild: gn.parentElement && gn.parentElement.firstElementChild === gn,
    };

    // ── Layout, measured at THIS iframe's real width ──────────────────
    const vw = window.innerWidth;
    const overflow = [];
    document.querySelectorAll("#cc-level-pass-root *, #cc-referral-root *, #cc-game-night *")
      .forEach(el => {
        // Anything inside a scroller is allowed to be wider than the window,
        // that is what the scroller is for. Everything else is a real overflow.
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
    out.layout = {
      vw,
      docScrollsSideways: document.documentElement.scrollWidth > vw + 1,
      overflow: overflow.slice(0, 6),
      passHead: (() => { const h = document.querySelector(".ccLP-head");
                         return h ? Math.round(h.getBoundingClientRect().width) : 0; })(),
      gnBtnW: (() => { const b = document.querySelector(".ccGN-btn");
                       return b ? Math.round(b.getBoundingClientRect().width) : 0; })(),
      refBtnW: (() => { const b = document.getElementById("ccRF-go");
                        return b ? Math.round(b.getBoundingClientRect().width) : 0; })(),
      // The RSVP button must stay a real tap target, not a sliver.
      gnBtnH: (() => { const b = document.querySelector(".ccGN-btn");
                       return b ? Math.round(b.getBoundingClientRect().height) : 0; })(),
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
    // Wait for that frame's own async render to finish.
    for (let i = 0; i < 400 && !ifr.contentWindow.__RESULT; i++) {
      await new Promise(r => setTimeout(r, 25));
    }
    results[w] = ifr.contentWindow.__RESULT || { errors: ["never reported in"] };
  }
  document.getElementById("RESULT").textContent = "@@" + JSON.stringify(results) + "@@";
})();
</script>
</body></html>`;

const file = path.join(os.tmpdir(), `cc_pass_ui_${Date.now()}.html`);
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

let pass = 0, fail = 0;
const check = (n, c, extra) => {
  if (c) { pass++; console.log("  ✓ " + n); }
  else { fail++; console.log("  ✗ FAIL: " + n + (extra !== undefined ? "  → " + extra : "")); }
};

// ══════════════════════════════════════════════════════════════════════════
const D = R[1280];
console.log("\ndesktop (1280px):");
check("all three modules ran without throwing", D.errors.length === 0, D.errors.join(" | "));
check("the iframe really is 1280 wide", D.layout.vw === 1280, D.layout.vw);

console.log("\n  Level Pass:");
const serverTiers = P.state.track.length;
check("every server tier rendered", D.pass.tiers === serverTiers,
      `rendered ${D.pass.tiers} of ${serverTiers}`);
check("the player's level is shown", D.pass.level === "22", D.pass.level);
check("milestone critters render as milestones",
      D.pass.milestones === P.state.track.filter(t => t.type === "critter").length,
      D.pass.milestones);
check("tiers already claimed show as claimed", D.pass.claimed > 0, D.pass.claimed);
check("tiers reached but unclaimed offer a Claim button", D.pass.ready > 0, D.pass.ready);
check("a Claim button never sits on a locked tier", D.pass.claimOnLocked === 0, D.pass.claimOnLocked);
check("a Claim button never sits on a claimed tier", D.pass.claimOnClaimed === 0, D.pass.claimOnClaimed);
check("Claim buttons match the ready tiers exactly",
      D.pass.claimBtns === D.pass.ready, `${D.pass.claimBtns} vs ${D.pass.ready}`);
check("the Claim-all button counts the ready tiers",
      D.pass.claimAll.includes(String(D.pass.ready)), D.pass.claimAll);
check("the header says how much XP until the next thing",
      /[\d,]+ XP until/.test(D.pass.next), D.pass.next);
check("the next thing is named, not just numbered",
      /until .+/.test(D.pass.next) && D.pass.next.length > 20, D.pass.next);
check("locked tiers each say how much XP is left",
      D.pass.togo.length === 3 && D.pass.togo.every(t => /[\d,]+ XP to go/.test(t)),
      JSON.stringify(D.pass.togo));
check("the level bar states its own numbers",
      /[\d,]+ \/ [\d,]+ XP to Level 23/.test(D.pass.barTxt), D.pass.barTxt);

// Assert against the SERVER's own inventory, never a number typed in here,
// the fixture claims a few tiers before snapshotting, so any hard-coded
// figure is a value from before the payout it is supposed to be checking.
const chipText = D.pass.chips.join(" | ");
const INV = P.state.inventory;
check("the coin balance matches the server's",
      chipText.includes(INV.coins.toLocaleString("en-US")),
      `want ${INV.coins} · got ${chipText}`);
check("streak shields match the server's",
      new RegExp(`${INV.shields} Streak Shield`).test(chipText),
      `want ${INV.shields} · got ${chipText}`);
check("held XP boosts match the server's",
      new RegExp(`${INV.boosts} XP Boost`).test(chipText),
      `want ${INV.boosts} · got ${chipText}`);
check("held Weekly Swaps match the server's",
      new RegExp(`${INV.rerolls} Weekly Swap`).test(chipText),
      `want ${INV.rerolls} · got ${chipText}`);
check("the rail scrolls inside itself", D.pass.railScrolls === true);
check("tier cards are all one height (the track line stays aligned)",
      D.pass.heights.length === 1, JSON.stringify(D.pass.heights));

console.log("\n  a brand-new account:");
check("nothing is claimable at level 1", D.fresh.ready === 0, D.fresh.ready);
check("no Claim buttons are offered", D.fresh.claimBtns === 0, D.fresh.claimBtns);
check("it says so instead of showing an empty button",
      /Nothing to claim/i.test(D.fresh.allclear), D.fresh.allclear);
check("it still says what is coming next", /XP until/.test(D.fresh.next), D.fresh.next);

console.log("\n  when the database refuses (the level the app already knows):");
check("the badge is the account's real level, not 1",
      D.unreadable.level === "39", D.unreadable.level);
check("the XP bar counts to the next real level, not level 2",
      /to Level 40$/.test(D.unreadable.barTxt), D.unreadable.barTxt);
check("the track unlocks the tiers that level has earned",
      D.unreadable.ready > 0, D.unreadable.ready);
check("a read that SUCCEEDED still wins over the browser's copy",
      D.serverWins.level === "1", D.serverWins.level);

console.log("\n  XP boost:");
check("a running boost paints a live chip", /\+20% XP/.test(D.boost.chip), D.boost.chip);
check("the live chip counts down", /left/.test(D.boost.chip), D.boost.chip);
check("__ccPassBoost() reports active", D.boost.apiActive === true);
check("__ccPassBoost() hands back the multiplier, not just a percent",
      D.boost.apiMult === 1.2, D.boost.apiMult);
check("the percent matches the server's", D.boost.apiPercent === P.boosted.boostPercent,
      `${D.boost.apiPercent} vs ${P.boosted.boostPercent}`);

console.log("\n  the sidebar's unclaimed badge:");
check("it shows a count as soon as the pass syncs",
      D.badge.atLoad !== null && Number(D.badge.atLoad) > 0, JSON.stringify(D.badge));
check("…and the count is the number of rewards actually waiting",
      Number(D.badge.atLoad) === D.badge.readyAtLoad,
      `badge ${D.badge.atLoad} vs ${D.badge.readyAtLoad} ready tiers`);
check("claiming one takes the number down by one",
      Number(D.badge.afterOneClaim) === Number(D.badge.atLoad) - 1,
      `${D.badge.atLoad} -> ${D.badge.afterOneClaim}`);
check("…and the track agrees a tier left the ready pile",
      D.badge.readyAfter === D.badge.readyAtLoad - 1,
      `${D.badge.readyAtLoad} -> ${D.badge.readyAfter}`);
check("with nothing to claim the badge is hidden, not a zero",
      D.badge.whenNothingReady === null && D.badge.rawTextWhenHidden === "",
      JSON.stringify(D.badge));
check("signing out clears it without waiting on the network",
      D.badge.afterSignOut === null, JSON.stringify(D.badge));

console.log("\n  Invite a Friend:");
check("the card found its own home in the Friends panel", D.referral.selfInjected === true);
// The amounts come from the SERVER payload the card was rendered from, not
// from numbers typed here: they are an economy dial, and the card's job is to
// state whatever the server currently pays, exactly.
const REF = P.referral;
const refPct = Math.round(((REF.referrals % REF.backgroundEvery) / REF.backgroundEvery) * 100) + "%";
check(`the reward is stated (${REF.coins} coins)`,
      new RegExp("you both get " + REF.coins, "i").test(D.referral.text),
      D.referral.text.slice(0, 140));
check("the player's own friend code is shown", D.referral.code === "4985", D.referral.code);
check("progress toward the next background is counted",
      new RegExp(REF.toNextBackground + " more → free background").test(D.referral.goal),
      D.referral.goal);
check(`the progress bar reflects ${REF.referrals} of ${REF.backgroundEvery}`,
      D.referral.barPct === refPct, `${D.referral.barPct} want ${refPct}`);
check("a fresh account can enter a code", D.referral.hasInput === true);

console.log("\n  Game Night:");
check("the banner put itself on Player Home", D.gn.selfInjected === true);
check("it is the FIRST thing in the Overview panel", D.gn.firstChild === true);
check("the schedule is stated in full, both nights",
      /Every Wednesday & Saturday, 7:00–9:00 PM CST/.test(D.gn.text), D.gn.text.slice(0, 160));
check("RSVP is offered", /RSVP/i.test(D.gn.btnLabel), D.gn.btnLabel);
check("the RSVP link points at Discord", /discord\.gg/.test(D.gn.href), D.gn.href);
check("RSVP is described as recommended, not required",
      /isn't mandatory/i.test(D.gn.text) && /recommended/i.test(D.gn.text), D.gn.text);
check("it says when the next one starts, and which night it is",
      /(Wednesday|Saturday) · starts in|Live right now/.test(D.gn.text), D.gn.text);
// The XP bonus is half the reason to turn up, so it is a chip of its own next
// to the countdown, not a clause buried in the note.
check("the 1.5x XP bonus is on a chip of its own",
      /1\.5x XP/.test(D.gn.xpChip), D.gn.xpChip);
check("…and the note says what it applies to",
      /Games, challenges and your daily bonus all pay 1\.5x XP/.test(D.gn.text),
      D.gn.text.slice(0, 220));

// ══════════════════════════════════════════════════════════════════════════
console.log("\nwidths (real iframe viewports):");
for (const w of WIDTHS) {
  const F = R[w] || { errors: ["missing"] };
  const L = F.layout || {};
  const label = `${w}px`;
  check(`${label}: nothing throws`, (F.errors || []).length === 0, (F.errors || []).join(" | "));
  check(`${label}: the viewport really is ${w}`, L.vw === w, L.vw);
  check(`${label}: the page never scrolls sideways`, L.docScrollsSideways === false);
  check(`${label}: nothing overflows the window`, (L.overflow || []).length === 0,
        JSON.stringify(L.overflow));
  check(`${label}: the pass header fits`, L.passHead > 0 && L.passHead <= w, L.passHead);
  check(`${label}: the RSVP button is a real tap target`,
        L.gnBtnW >= 90 && L.gnBtnH >= 34, `${L.gnBtnW}×${L.gnBtnH}`);
  check(`${label}: the referral Claim button is a real tap target`, L.refBtnW >= 80, L.refBtnW);
  check(`${label}: tier cards keep a usable width`, F.pass.minTierW >= 140, F.pass.minTierW);
}

// ══════════════════════════════════════════════════════════════════════════
// WIRING. The modules above render perfectly into a test page; the thing that
// actually breaks in production is a renderer pointing at an id the real
// markup does not have, or a script tag nobody added. Both are static facts,
// so check them statically.
// ══════════════════════════════════════════════════════════════════════════
console.log("\nwiring (real preview.html / preview-app.js):");
{
  const HTML = fs.readFileSync(path.join(CLIENT, "preview.html"), "utf8");
  const APP = fs.readFileSync(path.join(CLIENT, "js", "preview-app.js"), "utf8");
  const VER = JSON.parse(fs.readFileSync(path.join(CLIENT, "version.json"), "utf8"));

  // Every element the three modules look up by id must exist in the markup.
  for (const id of ["cc-level-pass-root", "ph-panel-levelpass", "snav-levelpass",
                    "snav-levelpass-badge", "auth-ref-input"]) {
    check(`preview.html declares #${id}`, HTML.includes(`id="${id}"`));
  }

  // …and every file they live in must actually be loaded.
  for (const f of ["js/level-pass.js", "js/referral.js", "js/game-night.js",
                   "css/level-pass.css", "css/game-night.css"]) {
    check(`preview.html loads ${f}`, HTML.includes(`/${f}?v=`), f);
  }

  // A stale ?v= on /js or /css is a whole day of browsers running the old app
  // against the new API, because THOSE paths carry a 1-day max-age. Images do
  // not: they are pinned to whenever the art itself last changed and must NOT
  // be dragged along by an unrelated code deploy.
  const codeStamps = [...HTML.matchAll(/["'](?:\/(?:css|js)\/[^"']+?)\?v=([\d.-]+)["']/g)]
    .map(m => m[1]);
  const unique = [...new Set(codeStamps)];
  check("every /css and /js cache-buster carries this build's stamp",
        codeStamps.length > 0 && unique.length === 1 && unique[0] === VER.build,
        `version.json=${VER.build} · found ${JSON.stringify(unique)}`);
  check("APP_BUILD matches version.json",
        APP.includes(`const APP_BUILD   = "${VER.build}"`), VER.build);

  // The tab has to exist in all three places or it is a dead sidebar button.
  check("the Level Pass tab is in the panels map", /levelpass:"ph-panel-levelpass"/.test(APP));
  // The tab is no longer locked for guests: they see their level and the whole
  // reward track, and the note says the part that needs an account (claiming).
  check("the Level Pass tab opens for a guest, with a note about claiming",
        /levelpass:\s*"[^"]*[Cc]laiming[^"]*account/.test(APP)
        && !/GUEST_GATE_MSGS/.test(APP));
  check("switchTab renders the Level Pass", /name === "levelpass"\)\s*_renderLevelPassTab/.test(APP));
  check("_renderLevelPassTab is defined", /function _renderLevelPassTab\(/.test(APP));

  // The bridges the modules refuse to work without.
  check("__ccLevelPass bridge is defined", /window\.__ccLevelPass\s*=/.test(APP));
  check("__ccReferral bridge is defined", /window\.__ccReferral\s*=/.test(APP));

  // The two exports level-pass.js calls into.
  check("__ccWeekStartMs is exported", /window\.__ccWeekStartMs\s*=/.test(APP));
  check("renderChallengeStrip is exported", /window\.renderChallengeStrip\s*=/.test(APP));

  // The Avatar Gallery's twin of the pass rail was removed in 1.6.74. Its
  // markup, its CSS and its renderer all have to go together: a leftover host
  // element is an empty card in the gallery, and leftover CSS is dead weight
  // shipped to every player.
  const LPCSS = fs.readFileSync(path.join(CLIENT, "css", "level-pass.css"), "utf8");
  check("the gallery level track renderer is gone", !/function _galRenderLevelTrack\(/.test(APP));
  check("…and nothing still calls it", !/_galRenderLevelTrack\(\)/.test(APP));
  check("…and its host element is gone from preview.html", !HTML.includes('id="gal-level-track"'));
  check("…and its CSS rules went with it", !/^\.galLT/m.test(LPCSS));

  // The XP boost has to reach EVERY XP path, which means going through the one
  // multiplier, not a second one bolted on beside it.
  check("the boost is folded into prestigeXp", /boostBonus/.test(APP));
  check("the daily login bonus is boosted too",
        /passBoostNow\(\)\.mult/.test(APP));
  check("the end-game breakdown names the boost", /XP Boost: \+/.test(APP));

  // The referral is redeemed only AFTER the account document exists.
  const setupIdx = APP.indexOf("async function finishNicknameSetup");
  const saveIdx = APP.indexOf("saveNewProfile(_authUser.uid", setupIdx);
  const redeemIdx = APP.indexOf("__ccReferralRedeem", setupIdx);
  check("sign-up redeems a friend code", redeemIdx > 0);
  check("…and only after the profile is saved", saveIdx > 0 && redeemIdx > saveIdx,
        `save@${saveIdx} redeem@${redeemIdx}`);

  // Weekly Swap.
  check("the swap button is gated on a live token", /_csSwapUnlocked\(\)/.test(APP));
  check("a completed challenge can never be swapped",
        /!c\.completed && _csSwapUnlocked\(\)/.test(APP));
  check("the swap uses the real slot, not the row position", /data-swapslot=/.test(APP)
        && /slotPos/.test(APP));
  check("swap clicks are delegated (cards are rebuilt every render)",
        /closest\(".ph-cs-swap"\)/.test(APP));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
