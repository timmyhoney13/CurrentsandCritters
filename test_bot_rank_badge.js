#!/usr/bin/env node
/* Every bot at the table wears its rank, and you can actually see it.
 *
 * Run:  node test_bot_rank_badge.js        (needs Google Chrome / Chromium)
 *
 * The badge beside a bot's name is the one place, mid-game, where you can
 * learn WHICH of the nine you are up against, and in a Head to Head it is the
 * only place at all: that table starts without a lobby. "An S+ across the
 * table is a different game from a D" is the whole reason it is there.
 *
 * It was there, in the DOM, on every bot. It was not on the SCREEN. The name
 * row is 90px wide with overflow:hidden, and as one line of text the badge
 * was simply the last thing on the line and so the first thing off the edge.
 * Measured at 1440px before this test existed:
 *
 *     Cook (F) 100%   Ross (E) 100%   Zheng (D) 95%   Magellan (C) 11%
 *     FitzRoy (B) 61%   Vancouver (A) 0%   Shackleton (S) 0%
 *     Bougainville (S+) 0%
 *
 * which is exactly "some of the bots do not show their letter": the ones with
 * long names. A check that only asks whether the element EXISTS passes for
 * every one of those, so this one measures the box it really occupies against
 * the box that really clips it, at every width, with the real renderer, the
 * real stylesheet and the real ladder.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const CSS = fs.readFileSync(path.join(CLIENT, "css/preview.css"), "utf8");
const APP = fs.readFileSync(path.join(CLIENT, "js/preview-app.js"), "utf8");
const SERVER = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");
const ENGINE = fs.readFileSync(path.join(ROOT, "fish_game_all_in_one.py"), "utf8");

let pass = 0, fail = 0;
function check(cond, name, extra) {
  if (cond) { pass++; }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

function grabFn(name) {
  const re = new RegExp("^  (?:async )?function " + name + "\\s*\\(", "m");
  const m = re.exec(APP);
  if (!m) throw new Error("missing function: " + name);
  const i = APP.indexOf("{", m.index + m[0].length - 1);
  let d = 0;
  for (let j = i; j < APP.length; j++) {
    if (APP[j] === "{") d++;
    else if (APP[j] === "}" && --d === 0) return APP.slice(m.index, j + 1);
  }
  throw new Error("unbalanced: " + name);
}

// ── The real ladder and the real names, read out of the engine ─────────────
// Neither is retyped here: a test that invents its own rungs would go on
// passing after the ladder was re-tuned, and a test that invents its own bot
// names would stop measuring the thing that broke, which is name LENGTH.
const TIERS = (() => {
  const block = /BOT_GRADE_ANIMALS[^{]*\{([\s\S]*?)\n\}/.exec(ENGINE);
  if (!block) throw new Error("BOT_GRADE_ANIMALS not found in the engine");
  return [...block[1].matchAll(/"([^"]+)"\s*:\s*"[^"]+"/g)].map(m => m[1]);
})();
const NAMES = (() => {
  const block = /OCEAN_EXPLORER_NAMES[^(]*\(([\s\S]*?)\n\)/.exec(ENGINE);
  if (!block) throw new Error("OCEAN_EXPLORER_NAMES not found in the engine");
  return [...block[1].matchAll(/"([^"]+)"/g)].map(m => m[1]);
})();

console.log("the ladder this is measured against");
check(TIERS.length >= 9, "every rung of the ladder has a tier letter (" + TIERS.length + ")");
check(TIERS.includes("S+") && TIERS.includes("GS"),
      "including the two that are not single letters");
check(NAMES.length > 20, "the bot name pool was found (" + NAMES.length + " names)");
// The worst cases first: this bug was length, so the longest names are the
// ones worth putting on screen.
const LONGEST = NAMES.slice().sort((a, b) => b.length - a.length);
check(LONGEST[0].length >= 10,
      "the pool really does hold long names (" + LONGEST[0] + ")");

// ── Both sides of the wire still speak about ranks ─────────────────────────
console.log("\nwhat the server sends and what the client reads");
check(/"grade_tier":\s*\(str\(fish\.ai_difficulty_config/.test(SERVER),
      "the seat payload carries the tier for an AI seat");
check(/"difficulty":\s*fish\.normalize_bot_grade\(seat\.difficulty\)/.test(SERVER),
      "…and a normalised difficulty, which is never empty, as the backstop");
check(/grade_tier \|\| m\.grade \|\| m\.difficulty/.test(APP),
      "the client reads all three spellings, so a seat missing one still ranks");

// Every tier the ladder can produce must have a colour rule for the in-game
// badge, or a rank paints as an unstyled box.
console.log("\nevery rung has a badge colour");
const cssNoComments = CSS.replace(/\/\*[\s\S]*?\*\//g, " ");
TIERS.forEach((t) => {
  const cls = t.replace(/\+/g, "P").replace(/[^A-Za-z0-9_-]/g, "");
  check(new RegExp("\\.wr-tier-" + cls + "\\b").test(cssNoComments),
        "tier " + t + " has a .wr-tier-" + cls + " rule");
});

// ════════════════════════════════════════════════════════════════════════
//  IT IS REALLY ON THE SCREEN  (real renderer, real CSS, real Chrome)
// ════════════════════════════════════════════════════════════════════════
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => fs.existsSync(p));

function page() {
  const gameFns = ["pvSeatHash", "pvSeatDefaultAvatar", "_applyAvBg",
                   "_pvSeatRankKey", "bmTierLetter", "bmTierClass", "bmBadge",
                   "renderPlayerSeats"].map(grabFn).join("\n\n");

  // Seat 0 is a person; seats 1..7 are bots wearing the seven longest names
  // in the pool. `offset` rotates which rung each one plays at, so two passes
  // put every tier on screen.
  const build = (offset) => {
    const seats = [{ index: 0, kind: "human", device: "computer", is_away: false,
                     inactive_eligible: false, kicked: false }];
    const players = [{ index: 0, name: "Bartholomew", score: 31, hand_count: 5,
                       avatar: "/avatars/great-white-shark.png" }];
    for (let i = 1; i < 8; i++) {
      const tier = TIERS[(i - 1 + offset) % TIERS.length];
      seats.push({ index: i, kind: "ai", device: "", is_away: false,
                   inactive_eligible: false, kicked: false,
                   grade_tier: tier, grade: "", difficulty: "" });
      players.push({ index: i, name: LONGEST[i - 1], score: 12, hand_count: 7,
                     avatar: "" });
    }
    return { seats, players };
  };

  const variants = [build(0), build(7)].map(v => ({
    seats: JSON.stringify(v.seats),
    players: JSON.stringify(v.players),
  }));

  // bmTierLetter answers off the served ladder; hand it the real rungs.
  const grades = JSON.stringify(TIERS.map(t => ({ id: t, grade: t, tier: t })));

  const stubs = `
let _seatsRenderKey = "", canInteract = true;
let _latestSeatsForSurf = [];
const _bmGrades = ${grades};
window.CC_DEVICE = "computer";
function pvLiveAvatar() { return ""; }
function _avSrc(u) { return String(u || ""); }
function _bgSrc(u) { return String(u || ""); }
function isLikelyAiName(n) { return /^Bot /.test(String(n || "")); }
function ccDeviceLabel(d) {
  if (!d) return null;
  return { device: d, icon: "\\u{1F4BB}", label: d, short: "PC", initial: "C",
           text: "PC", title: "on a " + d };
}
const PV_SEAT_AVATARS = [
  "/avatars/mullet.png", "/avatars/sardine.png", "/avatars/flying-fish.png",
  "/avatars/bunker.png", "/avatars/bonito.png", "/avatars/mahi-mahi.png",
  "/avatars/roosterfish.png", "/avatars/blue-tang.png", "/avatars/clownfish.png",
  "/avatars/blue-marlin.png", "/avatars/barracuda.png", "/avatars/manta-ray.png"];
function openBoardFocus() {}
function attachBoardHover() {}
function bmAvatarForRank() { return ""; }
function showToast() {}
window.__fishMyAvatarUrl = () => "/avatars/great-white-shark.png";
window.__fishEquippedBackground = () => "";
window.__fishBgStyle = (u) => "";
window.__fishDrawForInactive = () => {};
window.__ccDraw = function (seats, players) {
  _latestSeatsForSurf = seats;
  _seatsRenderKey = "";
  renderPlayerSeats(players, 1, 0);
};
`;

  const inner = `<!doctype html><html><head><meta charset="utf-8"><style>
${CSS}
html,body{margin:0;background:#0b2138;}
#cc-board-probe{display:flex; gap:8px; padding:10px; align-items:flex-start;}
</style></head><body>
<div id="cc-board-probe">
  <div id="pv-seats-left" class="pv-seat-cluster"></div>
  <div id="pv-seats-right" class="pv-seat-cluster"></div>
</div>
<script>
window.__bootErr = "";
window.onerror = function (m) { window.__bootErr = String(m); };
try {
${stubs}
${gameFns}
} catch (e) { window.__bootErr = String((e && e.stack) || e); }
</scr` + `ipt></body></html>`;

  return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#111;}iframe{border:0;display:block;}</style></head><body>
<iframe id="f" width="1280" height="900"></iframe><div id="out"></div>
<script>
const SRC = ${JSON.stringify(inner).replace(/<\//g, "<\\/")};
const VARIANTS = ${JSON.stringify(variants)};
const WIDTHS = [360, 390, 430, 768, 1024, 1440];
const L = [];
const f = document.getElementById("f");

function measure(w, vi) {
  const d = f.contentDocument, win = f.contentWindow;
  const tag = w + "px/set" + vi;
  const ok = (c, m, x) => L.push((c ? "PASS " : "FAIL ") + tag + ": " + m
                                 + (!c && x ? "  [" + x + "]" : ""));
  ok(!win.__bootErr, "the page built without throwing: "
     + String(win.__bootErr || "").slice(0, 300));

  const seatEls = [...d.querySelectorAll(".pv-seat[data-player-index]")];
  ok(seatEls.length === 8, "all eight seats drew (" + seatEls.length + ")");

  const person = seatEls.find(s => s.dataset.playerIndex === "0");
  ok(!!person && !person.querySelector(".pv-seat-grade"),
     "a person wears no rank badge");
  ok(!!person && !!person.querySelector(".pv-seat-plabel"),
     "…and keeps the P# chip, which is what AFK reporting is typed against");

  const bots = seatEls.filter(s => s.dataset.playerIndex !== "0");
  ok(bots.length === 7, "seven bots on the table (" + bots.length + ")");

  bots.forEach((seat) => {
    const nameEl = seat.querySelector(".pv-seat-nametext");
    const who = nameEl ? nameEl.textContent : "seat " + seat.dataset.playerIndex;
    const gb = seat.querySelector(".pv-seat-grade");
    if (!gb) { ok(false, who + " has no rank badge at all"); return; }

    const cs = win.getComputedStyle(gb);
    ok(cs.display !== "none" && cs.visibility !== "hidden" && Number(cs.opacity) > 0.05,
       who + ": the badge is not hidden");
    ok(gb.textContent.trim().length > 0, who + ": the badge has a letter on it");

    const bb = gb.getBoundingClientRect();
    ok(bb.width >= 8 && bb.height >= 8,
       who + ": the badge has real size (" + Math.round(bb.width) + "x"
       + Math.round(bb.height) + ")");

    // THE CHECK THIS FILE EXISTS FOR. The name row clips its own overflow, so
    // "is in the DOM" and "is on the screen" are different questions. Compare
    // the badge's box against the box that clips it: anything less than all of
    // it means a player cannot read the rank.
    const row = gb.parentElement.getBoundingClientRect();
    const visible = Math.max(0, Math.min(row.right, bb.right) - Math.max(row.left, bb.left));
    const shown = Math.round(visible / Math.max(1, bb.width) * 100);
    ok(shown >= 100, who + " (" + gb.textContent + "): the whole badge is inside "
       + "the name row, not clipped off its edge", shown + "% of it is visible");

    // And inside the seat itself, so it cannot be sitting over a neighbour.
    const sr = seat.getBoundingClientRect();
    ok(bb.left >= sr.left - 0.5 && bb.right <= sr.right + 0.5,
       who + ": the badge stays inside its own seat",
       Math.round(bb.left) + "-" + Math.round(bb.right) + " vs seat "
       + Math.round(sr.left) + "-" + Math.round(sr.right));

    // The P# chip is for reporting a player AFK, and a bot can never be
    // reported (only the seat the game is waiting on can be, and that is
    // never a bot). Its room is what the name and the badge share.
    ok(!seat.querySelector(".pv-seat-plabel"),
       who + ": no P# chip on a bot, whose seat can never be reported");
  });

  // The cluster must not have grown to fit any of this.
  const widths = seatEls.map(s => s.getBoundingClientRect().width);
  ok(Math.max.apply(null, widths) - Math.min.apply(null, widths) < 0.6,
     "every seat is still the same width");
}

let wi = 0, vi = 0;
function step() {
  if (vi >= VARIANTS.length) {
    document.getElementById("out").textContent = L.join("\\n");
    return;
  }
  const w = WIDTHS[wi];
  f.width = w;
  f.srcdoc = SRC;
  f.onload = () => setTimeout(() => {
    try {
      const v = VARIANTS[vi];
      f.contentWindow.__ccDraw(JSON.parse(v.seats), JSON.parse(v.players));
      measure(w, vi);
    } catch (e) { L.push("FAIL " + w + "px/set" + vi + ": threw " + e); }
    wi++;
    if (wi >= WIDTHS.length) { wi = 0; vi++; }
    step();
  }, 90);
}
step();
</scr` + `ipt></body></html>`;
}

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: the render half did not run.");
} else {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ccrank-"));
  const file = path.join(dir, "probe.html");
  fs.writeFileSync(file, page());
  let dom = "";
  try {
    dom = execFileSync(CHROME, [
      "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      "--user-data-dir=" + path.join(dir, "profile"),
      "--virtual-time-budget=20000", "--window-size=1500,1000",
      "--dump-dom", "file://" + file,
    ], { encoding: "utf8", maxBuffer: 1 << 28, stdio: ["ignore", "pipe", "ignore"] });
  } catch (e) {
    dom = "";
  }
  const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
  const lines = m ? m[1].split("\n").map(s => s.trim()).filter(Boolean) : [];
  console.log("\nwhat is really on the screen, at every width, on every rung");
  check(lines.length > 0, "the probe page ran", lines.length ? "" : "no output from Chrome");
  lines.forEach(l => check(l.startsWith("PASS "), l.replace(/^(PASS|FAIL) /, "")));
}

console.log("");
if (fail) {
  console.log(fail + " of " + (pass + fail) + " checks FAILED");
  process.exit(1);
}
console.log("All " + pass + " checks passed.");
