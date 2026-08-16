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

// ── Source: opening the strip shows the week's REWARD ───────────────────────
// The reward card shipped with an inline display:none and nothing in the whole
// client ever cleared it, so every player who opened the strip saw three
// challenges and no reward — no Tide Sweep, no XP, no progress bar. The only
// reference to the element anywhere was its click handler.
console.log("\nopening the strip shows what the week pays");

const rewardTag = (/<button class="ph-cs-reward"[\s\S]*?>/.exec(HTML) || [""])[0];
check("the reward card is not hidden by inline style", !/display:\s*none/.test(rewardTag));
check("renderChallengeStrip decides whether the reward shows",
      /rewardEl\.style\.display = _csOpen \? "" : "none"/.test(APP));
check("the reward names its XP", /Weekly Tide Sweep · \+1,500 XP/.test(APP));
check("the placeholder count matches the 3 weeklies that exist",
      /id="ph-cs-reward-count">0 \/ 3 Completed</.test(HTML));
check("no stale '/ 5 Completed' left from the 5→3 change",
      !/\/ 5 Completed/.test(HTML));
check("a swept week reads as earned, not pending", /is-done/.test(APP) && /\.ph-cs-reward\.is-done/.test(CSS));
check("Perfect Week — the other reward — is stated too",
      /id="ph-cs-reward-sub"/.test(HTML) && /Perfect Week: play all 7 days/.test(APP));
check("the Perfect Week line is styled", /\.ph-cs-reward-sub \{/.test(CSS));
// Same omission in the in-game panel: three jobs listed, no pay stated.
check("the in-game panel has a reward line", /id="igcp-reward"/.test(HTML) && /#igcp-reward \{/.test(CSS));
check("the in-game reward line is filled in on open",
      /igcp-rw-done/.test(APP) && /Weekly Tide Sweep/.test(APP));
check("it is hidden with the rest when minimised",
      /#ig-challenge-panel\.igcp-minimized #igcp-reward/.test(CSS));
// It sits OUTSIDE the scroll box, so it spends the same fixed budget the cards
// do — the sideways-phone case loses a card if it is not paid for.
check("the tight-screen budget pays for the reward line",
      /#igcp-cards \{ max-height: max\(56px, calc\(var\(--igcp-room\) - 88px\)\)/.test(CSS));

console.log("\nthe in-game panel starts tucked away");
check("minimised unless the player said otherwise",
      /localStorage\.getItem\(_IGCP_MINIMIZED_KEY\) !== "0"/.test(APP));
check("it shows Weekly, with no view to switch", !APP.includes("_IGCP_VIEW_KEY"));
check("its header markup says Weekly", /id="igcp-title">Weekly Challenges</.test(HTML));
// The footer has looked like a button since it shipped (pointer cursor, hover
// colour) and is the only control guaranteed to be on screen when the panel is
// taller than the room above the bottom UI — the header is what goes off the
// top. It must actually do something.
check("the footer closes the panel too", /footEl\.addEventListener\("click"/.test(APP));
check("opening it can never leave an empty box", /igcp-empty/.test(APP) && /igcp-empty/.test(CSS));
// vh over-reports the room on iOS (it is the height with the toolbars hidden),
// which is exactly the screen where the room is tightest.
check("the panel measures the room in dvh, not vh",
      /--igcp-room:\s*max\(120px,\s*calc\(100dvh\s*-\s*var\(--pv-bottom-ui/.test(CSS));
check("the 120px top reserve that squeezed the cards to a sliver is gone",
      !/#igcp-cards\s*\{\s*max-height:\s*calc\(100vh - var\(--pv-bottom-ui, 302px\) - 190px\)/.test(CSS));

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
  const rew = document.getElementById("ph-cs-reward-btn");
  const rsub = document.getElementById("ph-cs-reward-sub");
  const onScreen = (n) => {
    const b = n.getBoundingClientRect();
    return b.width > 0 && b.height > 0 && getComputedStyle(n).display !== "none"
        && b.top >= 0 && b.left >= 0;
  };
  const rec = () => ({
    bodyDisplay: getComputedStyle(body).display,
    bodyH: Math.round(body.getBoundingClientRect().height),
    hdrW:  Math.round(hdr.getBoundingClientRect().width),
    stripW: Math.round(strip.getBoundingClientRect().width),
    stripH: Math.round(strip.getBoundingClientRect().height),
    cardsSeen: [...cards.querySelectorAll(".ph-cs-card")]
      .filter(c => c.getBoundingClientRect().height > 20).length,
    // The reward is the point of doing the challenges — measure it like a card.
    rewardSeen: onScreen(rew),
    rewardH: Math.round(rew.getBoundingClientRect().height),
    rewardSubSeen: onScreen(rsub),
    // Every card's own width. A card can be "on screen" and still be a
    // 26px sliver you cannot read a single word of.
    cardWs: [...cards.querySelectorAll(".ph-cs-card")]
      .map(c => Math.round(c.getBoundingClientRect().width)),
    rewardTop:  Math.round(rew.getBoundingClientRect().top),
    cardsBottom: Math.round(cards.getBoundingClientRect().bottom),
    // \\s, not \\s: this string is a Node template literal, so a lone \\s would
    // reach the page as a bare "s" and the regex would eat the letter s.
    rewardText: (rew.textContent || "").replace(/\\s+/g, " ").trim(),
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
  // The bug this file did not catch: the challenges appeared and the reward
  // they are for did not, because of an inline display:none nothing cleared.
  check("closed: the reward is put away with the cards", r.closed && r.closed.rewardSeen === false);
  check(`open: the reward card is really on screen (${r.open && r.open.rewardH}px tall)`,
        r.open && r.open.rewardSeen === true && r.open.rewardH > 40);
  check("open: the Perfect Week line is on screen too", r.open && r.open.rewardSubSeen === true);
  check(`open: the reward says what it pays (${JSON.stringify(r.open && r.open.rewardText)})`,
        r.open && /Tide Sweep/.test(r.open.rewardText) && /1,500 XP/.test(r.open.rewardText));
  check("neither state scrolls the page sideways",
        r.closed && r.open && r.closed.noSideScroll && r.open.noSideScroll);

  // ── The open strip at every width ─────────────────────────────────────────
  // This file measured ONE window size (1440x900) and passed, while every
  // player on anything narrower opened the strip onto three 26px slivers.
  // The responsive rules were written when the header, the cards and the
  // reward were all children of .ph-cs-strip (which wraps); the cards and the
  // reward later moved into .ph-cs-body — a flex row that did NOT wrap — so
  // "cards 100%, reward 100%" shared one line, and the reward (flex-shrink 0)
  // kept all of it. Reading a challenge needs REAL WIDTH, so measure it.
  console.log("\nthe open strip, at every screen width");

  const MIN_CARD_W = 140;   // below this the name + requirement stop being readable
  for (const [label, w, h] of [
    ["desktop      ", 1440, 900],
    ["small laptop ", 1180, 800],
    ["tablet       ", 1024, 768],
    ["small tablet ",  820, 1180],
    ["phone        ",  390, 844],
  ]) {
    const dom2 = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
      "--hide-scrollbars", `--window-size=${w},${h}`, "--virtual-time-budget=8000",
      "--dump-dom", "file://" + f], { encoding: "utf8", maxBuffer: 64e6 });
    const m2 = /<div id="out">([\s\S]*?)<\/div>/.exec(dom2);
    const r2 = JSON.parse((m2 ? m2[1] : "{}").replace(/&quot;/g, '"'));
    const o  = r2.open || {};
    const ws = o.cardWs || [];
    const minW = ws.length ? Math.min(...ws) : 0;
    check(`${label}(${w}px): all three challenges are on screen`, o.cardsSeen === 3);
    check(`${label}(${w}px): each card is wide enough to read (${minW}px)`,
          ws.length === 3 && minW >= MIN_CARD_W);
    check(`${label}(${w}px): the reward is on screen with them`, o.rewardSeen === true);
    check(`${label}(${w}px): the reward never sits on top of the cards`,
          o.rewardTop >= o.cardsBottom - 1 || w > 1320);
    check(`${label}(${w}px): the page does not scroll sideways`, o.noSideScroll === true);
  }

  // ── The in-game panel, on the screen where it broke ───────────────────────
  // A phone held SIDEWAYS is the tight case: the game lays out at ~1266x498
  // and the measured bottom stack (action bar + hand zone) is 274px, so there
  // are 224px of room above it. The old rule reserved 120px of that for the
  // banner and gave #igcp-cards `100vh - bottom - 190px` = 34px — a sliver of
  // one row, which is what "click Weekly Challenges and none of them pop up"
  // actually looked like. Same markup, same CSS, real pixels.
  console.log("\nthe in-game panel, opened on a phone held sideways");

  const ps = HTML.indexOf('<div id="ig-challenge-panel">');
  const pe = HTML.indexOf("</div>", HTML.indexOf('id="igcp-footer"')) + 6;
  const IGCP = HTML.slice(ps, pe) + "\n</div>";

  const IROW = `<div class="igcp-row"><div class="igcp-row-top">
    <div class="igcp-row-icon">🏁</div><div class="igcp-row-info">
    <div class="igcp-row-name">Weekly Finisher</div>
    <div class="igcp-row-desc">Finish 10 games this week.</div></div>
    <div class="igcp-row-xp">+1,000 XP</div></div>
    <div class="igcp-row-bar-wrap"><div class="igcp-row-bar"><div class="igcp-row-fill" style="width:40%"></div></div>
    <span class="igcp-row-prog">4/10</span></div></div>`;

  // 274px is what test_mobile_layout.js measures for the real bottom stack at
  // this size; the panel is anchored on it, so the test has to use it too.
  const igPage = (bottomUi) => `<!doctype html><html><head><meta charset="utf-8"><style>${CSS}</style>
<style>html,body{margin:0;height:100%}:root{--pv-bottom-ui:${bottomUi}px}</style></head>
<body>${IGCP}<div id="out">RUNNING</div>
<script>
(function(){
  var panel = document.getElementById("ig-challenge-panel");
  var cards = document.getElementById("igcp-cards");
  var foot  = document.getElementById("igcp-footer");
  var rew   = document.getElementById("igcp-reward");
  panel.style.display = "block";
  panel.classList.add("igcp-minimized");
  var VH = document.documentElement.clientHeight;
  function rec() {
    var p = panel.getBoundingClientRect();
    var rows = [].slice.call(cards.querySelectorAll(".igcp-row"));
    var clip = cards.getBoundingClientRect();
    var rb = rew.getBoundingClientRect();
    return {
      panelTop: Math.round(p.top), panelBottom: Math.round(p.bottom),
      cardsH: Math.round(clip.height),
      // The reward line must be on screen with the cards, never off the bottom.
      rewardSeen: getComputedStyle(rew).display !== "none" && rb.height > 0
                  && rb.top >= 0 && rb.bottom <= VH,
      // A row counts only if it is inside the panel's own clip box AND on screen.
      rowsSeen: rows.filter(function(r){
        var b = r.getBoundingClientRect();
        return b.height > 20 && b.top >= clip.top - 1 && b.bottom <= clip.bottom + 1
            && b.top >= 0 && b.bottom <= VH;
      }).length,
      footOnScreen: foot.getBoundingClientRect().bottom <= VH && foot.getBoundingClientRect().top >= 0,
    };
  }
  var out = { VH: VH, closed: rec() };
  // Exactly what renderIgChallengePanel does when the header is tapped.
  cards.innerHTML = ${JSON.stringify(IROW)}.repeat(3);
  rew.textContent = "🗝️ All 3 = Weekly Tide Sweep · +1,500 XP  (1/3)";
  panel.classList.remove("igcp-minimized");
  out.open = rec();
  document.getElementById("out").textContent = JSON.stringify(out);
})();
</script></body></html>`;

  // Headless Chrome hands the page 87px less than --window-size, so ask for
  // the size that MEASURES as the one under test (same trick, same numbers, as
  // test_mobile_layout.js).
  const CHROME_CHROME_PX = 87;
  for (const [label, w, h, bottomUi, wantRows] of [
    ["sideways phone (1266x498, 274px bottom stack)", 1266,  498, 274, 2],
    ["upright phone  (585x1179, 456px bottom stack)",  585, 1179, 456, 3],
    ["laptop         (1440x900, 302px bottom stack)", 1440,  900, 302, 3],
  ]) {
    const f2 = path.join(os.tmpdir(), `cc_igcp_${w}x${h}.html`);
    fs.writeFileSync(f2, igPage(bottomUi));
    const dom2 = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
      "--hide-scrollbars", `--window-size=${w},${h + CHROME_CHROME_PX}`, "--virtual-time-budget=6000",
      "--dump-dom", "file://" + f2], { encoding: "utf8", maxBuffer: 64e6 });
    const m2 = /<div id="out">([\s\S]*?)<\/div>/.exec(dom2);
    const g = JSON.parse((m2 ? m2[1] : "{}").replace(/&quot;/g, '"'));

    check(`${label}: the harness really got ${h}px of viewport (got ${g.VH})`, g.VH === h);
    check(`${label}: closed shows no challenge`, g.closed && g.closed.rowsSeen === 0);
    check(`${label}: opened shows at least ${wantRows} challenge(s) (got ${g.open && g.open.rowsSeen})`,
          g.open && g.open.rowsSeen >= wantRows);
    check(`${label}: the cards box is a readable height (got ${g.open && g.open.cardsH}px)`,
          g.open && g.open.cardsH >= 56);
    // The close control has to stay reachable: the header can ride off the top
    // of a short screen, the bottom-anchored footer never can.
    check(`${label}: the panel can still be closed`, g.open && g.open.footOnScreen);
    check(`${label}: minimised hides the reward line`, g.closed && g.closed.rewardSeen === false);
    check(`${label}: opened shows what the week pays`, g.open && g.open.rewardSeen === true);
  }
}

console.log("\n" + "=".repeat(42));
console.log(`RESULT: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
