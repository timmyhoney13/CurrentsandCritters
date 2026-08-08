#!/usr/bin/env node
/* Weekly Challenges — the strip that starts closed, and the daily system that
 * is gone (1.6.54).
 *
 * Two halves:
 *   • SOURCE — daily challenges left no stumps behind: no pool, no state, no
 *     reporting hook, no unearnable "Daily Tide Sweep" achievement, and no
 *     observer state that only ever fed a daily. A leftover call to a hook that
 *     no longer exists is silent (every call site used `?.`), which is exactly
 *     the kind of dead code that gets copied forward.
 *   • RENDER — the real preview.html markup under the real preview.css in
 *     headless Chrome: closed, the strip is its header and nothing else; open,
 *     the three challenges are actually on screen. That is the whole feature,
 *     and a stubbed DOM cannot see it.
 *
 * Run:  node test_weekly_challenges.js        (render half needs Google Chrome)
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const APP  = read("js/preview-app.js");
const HTML = read("preview.html");
const CSS  = read("css/preview.css");
const TUT  = read("js/tutorials.js");

let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name); }
}

// ── Source: the daily system is gone, not just hidden ───────────────────────
console.log("\ndaily challenges are removed");

for (const stump of [
  "_DAILY_CHALLENGES", "_reportDailyChallengeProgress", "_getCurrentDailySlots",
  "_loadDailyState", "cc_daily_state_v1", "daily_tide_sweep",
]) {
  check(`no trace of ${stump}`, !APP.includes(stump));
}

// Observer state that existed ONLY to feed a daily. Each of these was updated
// every payload; left behind they cost work and read as live tracking.
for (const dead of [
  "_chObsPoolWatcherStreak", "_chObsCompSelfDiscarded", "_chObsPoolJustResetAt",
  "_chObsSurfaceStreak", "_chObsFloorStreak", "_chObsPrevMyHandCount",
  "_CH_FIRST_OCEAN_MAP", "_challObserveStarCombo",
]) {
  check(`observer state ${dead} went with it`, !APP.includes(dead));
}

check("the play-again-pending flag is gone with the challenge it fed",
      !APP.includes("cc_play_again_pending"));

// ── Source: the weekly system is intact ─────────────────────────────────────
console.log("\nweekly challenges still do everything they did");

check("the weekly pool is still there", /const _WEEKLY_CHALLENGES = \[/.test(APP));
const weeklyPool  = APP.slice(APP.indexOf("const _WEEKLY_CHALLENGES = ["));
const weeklyCount = (weeklyPool.slice(0, weeklyPool.indexOf("\n    ];"))
  .match(/^\s*\{ id: "\w+",\s+name: "/gm) || []).length;
check("the weekly pool still has its 56 challenges (" + weeklyCount + ")", weeklyCount === 56);
check("weekly progress is still reported", APP.includes("window._reportWeeklyChallengeProgress = reportWeeklyChallengeProgress"));
check("the weekly Tide Sweep achievement still fires", APP.includes('"weekly_tide_sweep"'));
// Challenges that share their detector with a removed daily must survive it.
for (const id of ["stolen_setup", "good_sport_week", "balanced_ocean", "star_storm", "new_currents"]) {
  check(`${id} survived the daily it shared a detector with`, APP.includes(`"${id}"`));
}
// The end-game screen credits weeklies only, and reads a weekly-only snapshot.
check("the end-game snapshot is weekly-only", /return \{ weekly \};/.test(APP));
check("no end-game 'Daily Challenge' reward row", !APP.includes('"Daily Challenge"'));

// ── Source: the strip ships closed ──────────────────────────────────────────
console.log("\nthe strip ships closed");

check("markup starts collapsed", /class="ph-cs-strip is-collapsed"/.test(HTML));
check("the header is the toggle button", /id="ph-cs-header-btn"/.test(HTML));
check("closed is announced to screen readers", /aria-expanded="false"/.test(HTML));
check("the header controls the body it hides", /aria-controls="ph-cs-body"/.test(HTML));
check("the cards live inside the collapsible body",
      HTML.indexOf('id="ph-cs-body"') < HTML.indexOf('id="ph-cs-cards"'));
check("the old Daily ↔ Weekly toggle button is gone", !HTML.includes("ph-cs-toggle-btn"));
check("the strip's open state is remembered", APP.includes('"cc_cs_open"'));
check("the closed header still says how many are done",
      /complete this week — tap to see them/.test(APP));
check("CSS hides the body when collapsed",
      /\.ph-cs-strip\.is-collapsed \.ph-cs-body \{ display: none; \}/.test(CSS));

console.log("\nthe in-game panel starts tucked away");
check("minimised unless the player said otherwise",
      /localStorage\.getItem\(_IGCP_MINIMIZED_KEY\) !== "0"/.test(APP));
check("it shows Weekly, with no view to switch", !APP.includes("_IGCP_VIEW_KEY"));
check("its header markup says Weekly", /id="igcp-title">Weekly Challenges</.test(HTML));

console.log("\nthe tutorial teaches the new gesture");
check("the tour opens the strip by its header", TUT.includes('target: "#ph-cs-header-btn"'));
check("the tour waits for it to actually open", TUT.includes("gtChallengesOpen"));
check("no Daily Challenges step is left", !/badge: "Daily Challenges"/.test(TUT));

// ── Render: what the player sees ────────────────────────────────────────────
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found — skipping the render half.");
} else {
  console.log("\nrendered, closed then open");

  const start = HTML.indexOf('<div class="ph-cs-strip');
  const end   = HTML.indexOf('<div class="ph-main">', start);
  const STRIP = HTML.slice(start, end);

  const CARD = `<div class="ph-cs-card is-weekly"><div class="ph-cs-card-top">
    <div class="ph-cs-card-icon">🏁</div><div class="ph-cs-card-meta">
    <div class="ph-cs-card-type">WEEKLY</div><div class="ph-cs-card-name">Weekly Finisher</div>
    </div></div><div class="ph-cs-card-req">Finish 10 games this week.</div>
    <div class="ph-cs-card-foot"><div class="ph-cs-card-bar"><div class="ph-cs-card-fill" style="width:40%"></div></div>
    <span class="ph-cs-card-progress">4/10</span><span class="ph-cs-card-xp">+1,000 XP</span></div></div>`;

  // The lobby and its panels are hidden until sign-in picks one; force the two
  // wrappers visible so the strip is measured, and change nothing else.
  const page = `<!doctype html><html><head><meta charset="utf-8"><style>${CSS}</style>
<style>#auth-stats-lobby{display:block!important}.ph-panel{display:block!important}body{margin:0}</style>
</head><body><div id="auth-stats-lobby"><div class="ph-panel">${STRIP}</div></div>
<div id="out">RUNNING</div>
<script>
(function(){
  const strip = document.getElementById("ph-cs-strip");
  const body  = document.getElementById("ph-cs-body");
  const hdr   = document.getElementById("ph-cs-header-btn");
  const cards = document.getElementById("ph-cs-cards");
  const rec = () => ({
    bodyDisplay: getComputedStyle(body).display,
    bodyH: Math.round(body.getBoundingClientRect().height),
    hdrW:  Math.round(hdr.getBoundingClientRect().width),
    stripW: Math.round(strip.getBoundingClientRect().width),
    stripH: Math.round(strip.getBoundingClientRect().height),
    cardsSeen: [...cards.querySelectorAll(".ph-cs-card")]
      .filter(c => c.getBoundingClientRect().height > 20).length,
    noSideScroll: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  });
  const out = { closed: rec() };
  // Exactly what renderChallengeStrip does when the header is pressed.
  cards.innerHTML = ${JSON.stringify(CARD)}.repeat(3);
  strip.classList.remove("is-collapsed");
  out.open = rec();
  document.getElementById("out").textContent = JSON.stringify(out);
})();
</script></body></html>`;

  const f = path.join(os.tmpdir(), "cc_weekly_strip.html");
  fs.writeFileSync(f, page);
  const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
    "--hide-scrollbars", "--window-size=1440,900", "--virtual-time-budget=8000",
    "--dump-dom", "file://" + f], { encoding: "utf8", maxBuffer: 64e6 });
  const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
  const r = JSON.parse((m ? m[1] : "{}").replace(/&quot;/g, '"'));

  check("closed: the body is not rendered at all", r.closed && r.closed.bodyDisplay === "none");
  check("closed: no challenge is on screen", r.closed && r.closed.cardsSeen === 0);
  check("closed: the header spans the strip", r.closed && r.closed.hdrW > r.closed.stripW - 60);
  check("closed: the strip is one bar tall", r.closed && r.closed.stripH < 110);
  check("open: all three challenges are visible", r.open && r.open.cardsSeen === 3);
  check("open: the strip grew to hold them", r.open && r.open.stripH > r.closed.stripH);
  check("open: the header steps aside for the cards", r.open && r.open.hdrW < r.closed.hdrW);
  check("neither state scrolls the page sideways",
        r.closed && r.open && r.closed.noSideScroll && r.open.noSideScroll);
}

console.log("\n" + "=".repeat(42));
console.log(`RESULT: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
