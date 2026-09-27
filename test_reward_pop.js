/* ================================================================
 * test_reward_pop.js, the card that pops up when a pass pays out.
 *
 * js/reward-pop.js is ONE animation shared by the two reward tracks:
 * the Critter Pass unlock, and a collected tier on either pass. It is
 * driven headlessly in a real browser, and driven THROUGH the two pass
 * modules as well, because the wiring is the half that breaks.
 *
 * What is actually being protected here:
 *
 *   1. The seam is OPTIONAL. __ccRewardPop() answers false when it
 *      cannot show a card, and both passes then say the same news as a
 *      plain toast. A payout that reports nothing at all is the one
 *      failure that costs a player something, so the last scenario in
 *      this file loads the passes WITHOUT reward-pop.js and proves the
 *      toast comes back.
 *   2. The animation is really running. A card that fades in with no
 *      pop, no rings and no sweep of light is the toast it replaced
 *      with extra steps, and CSS that was never served looks exactly
 *      like CSS that was never written. Every animation-name is read
 *      off the live element.
 *   3. It closes, three ways: a tap anywhere, Escape, and on its own.
 *      A card with no button on it that outlives its timer is a page
 *      nobody can get out of.
 *   4. No emoji, no picture, no icon: the reward's own words are the
 *      whole card, and that is checked in the stylesheet, in the module
 *      and in every string the two passes hand it.
 *   5. It fits a phone. The card is measured at 390px in an iframe,
 *      because headless Chrome clamps --window-size to about 500px and
 *      a 390px window is really a 500px one.
 *
 *   node test_reward_pop.js
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
const HTML   = read("preview.html");
const POPJS  = read("js/reward-pop.js");
const POPCSS = read("css/reward-pop.css");
const LPJS   = read("js/level-pass.js");
const CPJS   = read("js/critter-pass.js");
const VER    = JSON.parse(read("version.json"));

let pass = 0, fail = 0;
const check = (n, c, extra) => {
  if (c) { pass++; console.log("  ✓ " + n); }
  else { fail++; console.log("  ✗ FAIL: " + n + (extra !== undefined ? "  → " + extra : "")); }
};

// Emoji, as a property escape rather than a list somebody has to keep: a new
// pictograph nobody thought of still fails.
const EMOJI = /\p{Extended_Pictographic}/u;
const DASHES = /[—–]/;

// ══════════════════════════════════════════════════════════════════════════
//  STATIC WIRING
//  These run with or without Chrome: an animation nobody serves is not an
//  animation, however well it plays in a test harness.
// ══════════════════════════════════════════════════════════════════════════
console.log("\nwiring: the pop is served, and served before the passes that call it");
check("the module and the stylesheet both exist",
      fs.existsSync(path.join(CLIENT, "js", "reward-pop.js"))
      && fs.existsSync(path.join(CLIENT, "css", "reward-pop.css")));
const popTag = HTML.indexOf('src="/js/reward-pop.js');
const lpTag  = HTML.indexOf('src="/js/level-pass.js');
const cpTag  = HTML.indexOf('src="/js/critter-pass.js');
check("preview.html serves the stylesheet", HTML.includes('href="/css/reward-pop.css'));
check("preview.html serves the module", popTag !== -1);
check("…before both passes, so the seam exists before either can be opened",
      popTag !== -1 && popTag < lpTag && popTag < cpTag, `${popTag} / ${lpTag} / ${cpTag}`);
// The service worker is cache-first by URL, so the ?v= stamp is the only thing
// that fetches a changed file for a returning player.
check("both tags carry this build's cache stamp",
      HTML.includes(`/js/reward-pop.js?v=${VER.build}`)
      && HTML.includes(`/css/reward-pop.css?v=${VER.build}`), VER.build);
// Every /js/ and /css/ reference on the page carries the BUILD stamp (images
// and fonts carry their own, and always have). A file left on the old stamp is
// a file no returning player ever fetches again.
{
  const stamps = [...HTML.matchAll(/\/(?:js|css)\/[A-Za-z0-9._-]+\?v=([^"' ]+)/g)]
    .map(m => m[1]);
  const stale = [...new Set(stamps.filter(v => v !== VER.build))];
  check("…and every js and css stamp on the page is this build",
        stamps.length > 20 && stale.length === 0, stale.join(", "));
}
check("the boot meter scores it instead of counting it as nothing",
      HTML.includes('"/js/reward-pop.js":') && HTML.includes('"/css/reward-pop.css":'));

console.log("\nthe module decides nothing and asks nobody");
check("it never talks to a server", !/fetch\(|XMLHttpRequest|\/api\//.test(POPJS));
check("it holds no reward table: it is handed finished words",
      !/Critter Coins|Streak Shield|XP Boost/.test(POPJS));
check("what it prints is escaped", /esc\(o\.title\)/.test(POPJS) && /esc\(o\.detail\)/.test(POPJS));
check("a card with no title is refused rather than drawn empty",
      /if \(!o \|\| !o\.title\) return false;/.test(POPJS));
check("a full queue answers false, so the caller says it as a toast instead",
      /_queue\.length >= QUEUE_MAX\) return false;/.test(POPJS));

// ── The two passes call it through a guard, and always with a fallback ────
// Every pop() call is sliced out by matching parens rather than by a regex
// that would give up at the first line break, because these calls are three
// lines long and the fallback is the last of the three.
function popCalls(src) {
  const calls = [];
  let i = 0;
  while ((i = src.indexOf("pop({", i)) !== -1) {
    // Not function pop() itself, and not a longer name ending in "pop".
    const before = src[i - 1];
    if (/[A-Za-z0-9_$.]/.test(before || "")) { i += 5; continue; }
    let depth = 0, j = src.indexOf("(", i);
    for (; j < src.length; j++) {
      if (src[j] === "(") depth++;
      else if (src[j] === ")") { depth--; if (!depth) break; }
    }
    calls.push(src.slice(i, j + 1));
    i = j + 1;
  }
  return calls;
}
console.log("\nboth passes route their payouts through it, and both can live without it");
for (const [name, src] of [["level-pass.js", LPJS], ["critter-pass.js", CPJS]]) {
  check(`${name} calls it through a guard, once`,
        (src.match(/window\.__ccRewardPop/g) || []).length === 2
        && /window\.__ccRewardPop && window\.__ccRewardPop\(o\)/.test(src),
        (src.match(/window\.__ccRewardPop/g) || []).length);
  check(`…and says the same news as a toast when it answers false`,
        /if \(!shown\) toast\(fallback, "good"\);/.test(src));
  const calls = popCalls(src);
  // Each call is pop({ ... }, fallback). The object literal is cut off by
  // matching its braces, and what is left has to be a second argument with
  // something in it: a card asked for with no fallback is a payout that goes
  // unreported the day this file is not served.
  const fallbacks = calls.map(c => {
    const open = c.indexOf("{");
    let depth = 0, j = open;
    for (; j < c.length; j++) {
      if (c[j] === "{") depth++;
      else if (c[j] === "}") { depth--; if (!depth) break; }
    }
    return c.slice(j + 1).replace(/^\s*,\s*/, "").replace(/\)\s*$/, "").trim();
  });
  check(`…every call carries a fallback line`, calls.length > 0
        && fallbacks.every(f => f.length > 3), JSON.stringify(fallbacks.map(f => f.slice(0, 24))));
  check(`…and never an empty one`, fallbacks.every(f => !/^(""|''|``)$/.test(f)));
  // The announce block is where the emoji toasts used to be. It must not
  // report a payout twice, once as a card and once as a toast.
  const a = src.slice(src.indexOf("function announce(granted)"));
  const body = a.slice(0, a.indexOf("\n  }\n") + 4);
  check(`…and announce() reports a payout ONCE, through the card`,
        body.includes("pop({") && !/\btoast\(/.test(body));
  for (const c of calls) {
    check(`…no emoji in what ${name} hands the card`, !EMOJI.test(c), c.slice(0, 90));
    check(`…and no em dash either`, !DASHES.test(c), c.slice(0, 90));
  }
}

console.log("\nthe card itself is words, and nothing else");
check("no emoji in the module", !EMOJI.test(POPJS));
check("no emoji in the stylesheet", !EMOJI.test(POPCSS));
check("no em dash in either", !DASHES.test(POPJS) && !DASHES.test(POPCSS));
check("the markup has no img, no svg and no icon slot",
      !/<img|<svg|ccRP-ico/.test(POPJS));

console.log("\nit sits under anything a player is answering");
const zIndex = Number((POPCSS.match(/#cc-reward-pop\s*\{[\s\S]*?z-index:\s*(\d+)/) || [])[1]);
check("it has a z-index", Number.isFinite(zIndex), zIndex);
check("…under the perk modal (9900), which is where a question would be",
      zIndex < 9900, zIndex);
check("…and under the toast rail (10001), so a warning beside it is readable",
      zIndex < 10001, zIndex);

console.log("\none timer, not two");
const HOLD_MS = Number((POPJS.match(/const HOLD_MS = (\d+);/) || [])[1]);
const OUT_MS = Number((POPJS.match(/const OUT_MS = (\d+);/) || [])[1]);
check("HOLD_MS is declared in the module", HOLD_MS > 500, HOLD_MS);
check("…and handed to the hairline from there, not typed into the CSS twice",
      POPJS.includes('style="animation-duration:')
      && !/\.ccRP-hair\s*\{[^}]*animation-duration/.test(POPCSS), HOLD_MS);
check("the fade-out is torn down on a timer, not on animationend",
      OUT_MS > 0 && !/addEventListener\(\s*["']animationend/.test(POPJS), OUT_MS);
check("…and the CSS fade-out is the same length",
      new RegExp("ccRP-unfade \\." + String(OUT_MS).replace(/0$/, "") + "s").test(POPCSS)
      || POPCSS.includes("ccRP-unfade ." + (OUT_MS / 1000).toFixed(2).slice(1) + "s"),
      OUT_MS);

console.log("\nreduced motion still gets the news");
check("there is a prefers-reduced-motion block",
      /@media \(prefers-reduced-motion: reduce\)/.test(POPCSS));
check("…and it drops the motion rather than flashing it past",
      /\.ccRP-ring, \.ccRP-sheen, \.ccRP-hair \{ display: none; \}/.test(POPCSS));

// ── .ccRP AND --rp-* BELONG TO THIS FILE ALONE ───────────────────────────
// css/clan-prize.css was once prefixed .ccCP, the same as the Critter Pass,
// and its token landed on the pass's own wrapper and painted every word on it
// cream. Both pass pages load this stylesheet, so the same trap is open here.
console.log("\nthe namespace is its own");
const OTHER_CSS = fs.readdirSync(path.join(CLIENT, "css"))
  .filter(f => f.endsWith(".css") && f !== "reward-pop.css");
const rules = (f) => read("css/" + f).replace(/\/\*[\s\S]*?\*\//g, "");
const squatters = OTHER_CSS.filter(f => /(^|[\s,>+~{])\.ccRP[\s.,:{[>+~-]/.test(rules(f)));
check("no other stylesheet styles a .ccRP class", squatters.length === 0, squatters.join(", "));
const tokenSquatters = OTHER_CSS.filter(f => /--rp-[a-z0-9-]+\s*:/.test(rules(f)));
check("no other stylesheet defines a --rp-* token", tokenSquatters.length === 0,
      tokenSquatters.join(", "));
check("and it reaches into neither pass's classes",
      !/\.ccLP-|\.ccCP-/.test(POPCSS) && !/ccLP|ccCP/.test(POPJS));

if (!CHROME) {
  console.log(`\n${pass} passed, ${fail} failed  (SKIPPED the render half: no Chrome/Chromium found)`);
  process.exit(fail ? 1 : 0);
}

// ── Real payloads, straight out of the Python servers ─────────────────────
// Same seam the two pass tests use: shapes invented by hand in a test file
// drift from the server the moment somebody renames a field.
function serverPayloads() {
  const script = `
import json, sys
sys.path.insert(0, ${JSON.stringify(ROOT)})
import level_pass_server as lp, critter_pass_server as cp
from test_level_pass_server import (FakeDb, ArrayUnion, LEVEL_TOTALS,
                                    level_progress, BACKGROUNDS, xp_for_level)

ldb = FakeDb()
lp.init(get_firestore=lambda: ldb, verify_token=lambda t: None,
        level_for_xp=level_progress, level_totals=LEVEL_TOTALS,
        background_paths=list(BACKGROUNDS))
lp._transactional = lambda: (lambda fn: fn)
lp._array_union = lambda: ArrayUnion
ldb.collection("users")._docs["u1"] = {
    "nickname": "Reef Boss",
    "stats": {"total_xp": xp_for_level(22) + 700, "critter_coins": 1234},
}
levelpass = lp.state_payload("u1")

cdb = FakeDb()
cp.init(get_firestore=lambda: cdb, verify_token=lambda t: None,
        level_for_xp=level_progress, level_totals=LEVEL_TOTALS,
        background_paths=list(BACKGROUNDS))
cp._transactional = lambda: (lambda fn: fn)
cp._array_union = lambda: ArrayUnion

# LOCKED, with the coins to unlock it: the state the buy animation is for.
cdb.collection("users")._docs["locked"] = {
    "nickname": "Reef Boss",
    "stats": {"total_xp": xp_for_level(30) + 500, "critter_coins": 6200},
}
locked = cp.state_payload("locked")

# OWNED, mid-track, nothing claimed: every reached tier has a Claim on it.
cdb.collection("users")._docs["owner"] = {
    "nickname": "Pass Holder",
    "stats": {"total_xp": xp_for_level(12) + 500, "critter_coins": 2200},
    "critter_pass_seasons": [cp.SEASON_ID],
    cp.SEASON_FIELD: {cp.SEASON_ID: {"xp": cp.season_xp_to_reach(30),
                                     "mark": xp_for_level(12) + 500}},
}
owner = cp.state_payload("owner")

print("@@" + json.dumps({"levelpass": levelpass, "locked": locked,
                         "owner": owner,
                         "price": cp.CRITTER_PASS_PRICE}) + "@@")
`;
  const out = execFileSync("python3", ["-c", script],
    { encoding: "utf8", cwd: ROOT, maxBuffer: 32 * 1024 * 1024 });
  const m = out.match(/@@([\s\S]*?)@@/);
  if (!m) throw new Error("no payload from the python servers:\n" + out);
  return JSON.parse(m[1]);
}

const P = serverPayloads();
const SRC = {
  popJs: POPJS, popCss: POPCSS,
  lpJs: LPJS, cpJs: CPJS,
  passCss: read("css/level-pass.css") + "\n" + read("css/critter-pass.css"),
};

// ══════════════════════════════════════════════════════════════════════════
//  THE HARNESS
//  Four frames. Each is its own document because reward-pop.js and both pass
//  modules are IIFEs that register window globals: two copies in one document
//  would fight over them.
// ══════════════════════════════════════════════════════════════════════════
const page = `<!doctype html><html><head><meta charset="utf-8">
<style>body{margin:0;background:#eef;font-family:Nunito,sans-serif}
 iframe{display:block;border:0;height:1000px;margin:0 0 8px}</style>
</head><body>
<div id="RESULT" style="display:none"></div>
<script>
window.__SRC = ${JSON.stringify(SRC)};
window.__PAYLOADS = ${JSON.stringify(P)};

// The bridges. post() resolves to the ENVELOPE the real apiPost returns:
// { ok, status, data }, NOT the bare body, because a bare-payload stub would
// let an unwrap bug sail straight through.
var BOOT = [
  'window.__toasts = [];',
  'window.__posts = [];',
  'window.__modalAnswer = { action: "confirm", selected: [] };',
  'function envelope(d) { return { ok: true, status: 200, data: d }; }',
  'window.__ccWeekStartMs = function () { return Date.now() - 864e5; };',
  'window.__ccCritterPass = {',
  '  idToken: async () => "tok", avSrc: (u) => u,',
  '  toast: (m, t) => window.__toasts.push([m, t]),',
  '  onGranted: () => {},',
  '  modal: async (o) => window.__modalAnswer,',
  '  post: async (p, b) => {',
  '    window.__posts.push([p, b]);',
  '    if (p === "/api/critterpass/state") return envelope(window.__CP_STATE);',
  '    if (p === "/api/critterpass/buy") {',
  '      window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.owner));',
  '      return envelope({ ok: true, season: "S1" });',
  '    }',
  '    if (p === "/api/critterpass/claim") {',
  '      var st = window.__CP_STATE;',
  '      if (!st.claimed.includes(b.tier)) st.claimed = st.claimed.concat([b.tier]);',
  '      return envelope({ ok: true, tier: b.tier,',
  '        granted: { type: "coins", coins: 100 }, inventory: st.inventory });',
  '    }',
  '    if (p === "/api/critterpass/claim-all") {',
  '      return envelope({ ok: true, count: 3, more: false, skipped: [],',
  '        claimed: [1,2,3].map(function (n) {',
  '          return { tier: "T" + n, granted: { type: "coins", coins: 10 } }; }) });',
  '    }',
  '    return envelope({ ok: true });',
  '  }',
  '};',
  'window.__ccLevelPass = {',
  '  idToken: async () => "tok", avSrc: (u) => u,',
  '  toast: (m, t) => window.__toasts.push([m, t]),',
  '  onGranted: () => {},',
  '  modal: async (o) => window.__modalAnswer,',
  '  post: async (p, b) => {',
  '    window.__posts.push([p, b]);',
  '    if (p === "/api/pass/state") return envelope(window.__LP_STATE);',
  '    if (p === "/api/pass/claim") {',
  '      var st = window.__LP_STATE;',
  '      if (!st.claimed.includes(b.tier)) st.claimed = st.claimed.concat([b.tier]);',
  '      return envelope({ ok: true, tier: b.tier,',
  '        granted: { type: "coins", coins: 300 }, inventory: st.inventory });',
  '    }',
  '    return envelope({ ok: true });',
  '  }',
  '};'
].join("\\n");

function innerHtml(o) {
  var S = window.__SRC;
  return '<!doctype html><html><head><meta charset="utf-8">'
    + '<style>body{margin:0;font-family:Nunito,sans-serif;background:#dff1ff}'
    + '.panel{padding:16px}</style>'
    + '<style>' + S.popCss + '</style>'
    + (o.passes ? '<style>' + S.passCss + '</style>' : '')
    + '</head><body>'
    + '<button><span id="snav-levelpass-badge" style="display:none">0</span></button>'
    + '<button><span id="snav-critterpass-badge" style="display:none">0</span></button>'
    + '<div class="panel" id="ph-panel-overview"></div>'
    + '<div class="panel"><div id="cc-level-pass-root"></div></div>'
    + '<div class="panel"><div id="cc-critter-pass-root"></div></div>'
    + '<div class="panel" id="ph-panel-friends"></div>'
    + (o.pop ? '<scr' + 'ipt>' + S.popJs + '</scr' + 'ipt>' : '')
    + '<scr' + 'ipt>' + BOOT + '</scr' + 'ipt>'
    + (o.passes ? '<scr' + 'ipt>' + S.lpJs + '</scr' + 'ipt>' : '')
    + (o.passes ? '<scr' + 'ipt>' + S.cpJs + '</scr' + 'ipt>' : '')
    + '<scr' + 'ipt>' + o.main + '</scr' + 'ipt>'
    + '</body></html>';
}
</script>
<script>
// ── Scenario 1: the module's own contract ────────────────────────────────
var MAIN_POP = [
 '(async () => {',
 '  const out = { errors: [] };',
 '  const rest = (n) => new Promise(r => setTimeout(r, n || 0));',
 '  const q = (s) => document.querySelector(s);',
 '  const txt = (s) => { const e = q(s); return e ? (e.textContent || "").replace(/\\\\s+/g, " ").trim() : null; };',
 '  const anim = (s) => { const e = q(s); return e ? getComputedStyle(e).animationName : null; };',
 '  const waitCard = async () => { for (let i = 0; i < 90; i++) { if (q("#cc-reward-pop .ccRP-card")) return true; await rest(25); } return false; };',
 '  const waitGone = async () => { for (let i = 0; i < 300; i++) { if (!q("#cc-reward-pop .ccRP-card")) return true; await rest(25); } return false; };',
 '  try {',
 '    out.registered = typeof window.__ccRewardPop === "function";',
 '    out.refusesEmpty = window.__ccRewardPop({}) === false;',
 '    out.nothingDrawnYet = !document.getElementById("cc-reward-pop");',
 '    out.accepted = window.__ccRewardPop({ kind: "reward", eyebrow: "Level Pass Reward",',
 '      title: "+250 Critter Coins", detail: "Added to your balance." });',
 '    out.appeared = await waitCard();',
 '    const ov = document.getElementById("cc-reward-pop");',
 '    out.open = !!(ov && ov.classList.contains("ccRP-open"));',
 '    out.display = ov ? getComputedStyle(ov).display : "";',
 '    out.role = ov ? ov.getAttribute("role") : "";',
 '    out.live = ov ? ov.getAttribute("aria-live") : "";',
 '    out.focusables = ov ? ov.querySelectorAll("button, a[href], input, [tabindex]").length : -1;',
 '    out.eyebrow = txt("#cc-reward-pop .ccRP-eyebrow");',
 '    out.title = txt("#cc-reward-pop .ccRP-title");',
 '    out.detail = txt("#cc-reward-pop .ccRP-detail");',
 '    out.pictures = ov ? ov.querySelectorAll("img, svg").length : -1;',
 '    out.anim = {',
 '      overlay: anim("#cc-reward-pop"), card: anim("#cc-reward-pop .ccRP-card"),',
 '      ring: anim("#cc-reward-pop .ccRP-ring"), sheen: anim("#cc-reward-pop .ccRP-sheen"),',
 '      title: anim("#cc-reward-pop .ccRP-title"), hair: anim("#cc-reward-pop .ccRP-hair"),',
 '      rings: ov ? ov.querySelectorAll(".ccRP-ring").length : -1,',
 '      hairMs: (() => { const e = q("#cc-reward-pop .ccRP-hair"); return e ? getComputedStyle(e).animationDuration : ""; })(),',
 '    };',
 '    ov.click();',
 '    out.goneOnTap = await waitGone();',
 '    out.stillOpenAfterTap = ov.classList.contains("ccRP-open");',
 '    window.__ccRewardPop({ title: "Streak Shield", detail: "It covers one missed day." });',
 '    out.reopened = await waitCard();',
 '    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));',
 '    out.goneOnEscape = await waitGone();',
 '    window.__ccRewardPop({ title: "Weekly Swap" });',
 '    await waitCard();',
 '    const t0 = Date.now();',
 '    out.goneByItself = await waitGone();',
 '    out.heldMs = Date.now() - t0;',
 '    const answers = [];',
 '    for (let i = 1; i <= 5; i++) answers.push(window.__ccRewardPop({ title: "Reward " + i }));',
 '    out.queue = answers;',
 '  } catch (e) { out.errors.push("THREW: " + (e && e.message ? e.message : String(e))); }',
 '  window.__RESULT = out;',
 '})();'
].join("\\n");

// ── Scenario 2: a phone ──────────────────────────────────────────────────
var MAIN_PHONE = [
 '(async () => {',
 '  const out = { errors: [] };',
 '  const rest = (n) => new Promise(r => setTimeout(r, n || 0));',
 '  const waitCard = async () => { for (let i = 0; i < 90; i++) { if (document.querySelector("#cc-reward-pop .ccRP-card")) return true; await rest(25); } return false; };',
 '  const measure = async (kind) => {',
 '    window.__ccRewardPop({ kind: kind, eyebrow: "Critter Pass", title: "Pass Unlocked",',
 '      detail: "You are at Pass Level 1. Every reward on the track is yours to climb for." });',
 '    await waitCard();',
 '    const c = document.querySelector("#cc-reward-pop .ccRP-card");',
 '    const r = c.getBoundingClientRect();',
 '    const o = { w: Math.round(r.width), h: Math.round(r.height),',
 '                left: Math.round(r.left), right: Math.round(r.right),',
 '                vw: window.innerWidth,',
 '                docScrolls: document.documentElement.scrollWidth > window.innerWidth + 1,',
 '                titlePx: Math.round(parseFloat(getComputedStyle(document.querySelector(".ccRP-title")).fontSize)) };',
 '    document.getElementById("cc-reward-pop").click();',
 '    for (let i = 0; i < 200 && document.querySelector("#cc-reward-pop .ccRP-card"); i++) await rest(25);',
 '    return o;',
 '  };',
 '  try {',
 '    out.reward = await measure("reward");',
 '    out.unlock = await measure("unlock");',
 '  } catch (e) { out.errors.push("THREW: " + (e && e.message ? e.message : String(e))); }',
 '  window.__RESULT = out;',
 '})();'
].join("\\n");

// ── Scenarios 3 and 4: the two passes, with the pop and without it ───────
var MAIN_PASSES = [
 '(async () => {',
 '  const out = { errors: [] };',
 '  const rest = (n) => new Promise(r => setTimeout(r, n || 0));',
 '  const q = (s) => document.querySelector(s);',
 '  const txt = (s) => { const e = q(s); return e ? (e.textContent || "").replace(/\\\\s+/g, " ").trim() : null; };',
 '  const waitCard = async () => { for (let i = 0; i < 120; i++) { if (q("#cc-reward-pop .ccRP-card")) return true; await rest(25); } return false; };',
 '  const settle = async () => { for (let i = 0; i < 60; i++) await rest(10); };',
 '  const readPop = () => ({',
 '    shown: !!q("#cc-reward-pop .ccRP-card"),',
 '    eyebrow: txt("#cc-reward-pop .ccRP-eyebrow"),',
 '    title: txt("#cc-reward-pop .ccRP-title"),',
 '    detail: txt("#cc-reward-pop .ccRP-detail"),',
 '    unlockKind: !!q("#cc-reward-pop .ccRP-is-unlock"),',
 '    cards: document.querySelectorAll("#cc-reward-pop .ccRP-card").length,',
 '  });',
 '  const shut = async () => { const ov = document.getElementById("cc-reward-pop");',
 '    if (ov) { ov.click(); for (let i = 0; i < 300 && q("#cc-reward-pop .ccRP-card"); i++) await rest(25); } };',
 '  try {',
 '    // ── A single Critter Pass tier ──────────────────────────────',
 '    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.owner));',
 '    await window.__ccCritterPassRender();',
 '    window.__toasts.length = 0;',
 '    const claim = q(".ccCP-claim");',
 '    out.hadClaimButton = !!claim;',
 '    if (claim) { claim.click(); await waitCard(); await settle(); }',
 '    out.cpTier = readPop();',
 '    out.cpTierToasts = window.__toasts.map(t => t[0]);',
 '    await shut();',
 '    // ── The whole track at once: ONE card, not one per tier ─────',
 '    window.__toasts.length = 0;',
 '    const all = document.getElementById("ccCP-claimall");',
 '    out.hadClaimAll = !!all;',
 '    if (all) { all.click(); await waitCard(); await settle(); }',
 '    out.cpAll = readPop();',
 '    out.cpAllToasts = window.__toasts.map(t => t[0]);',
 '    await shut();',
 '    // ── The unlock ──────────────────────────────────────────────',
 '    window.__CP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.locked));',
 '    await window.__ccCritterPassSync();',
 '    window.__toasts.length = 0;',
 '    const buy = document.getElementById("ccCP-buy");',
 '    out.hadBuyButton = !!buy;',
 '    if (buy) { buy.click(); await waitCard(); await settle(); }',
 '    out.cpBuy = readPop();',
 '    out.cpBuyToasts = window.__toasts.map(t => t[0]);',
 '    await shut();',
 '    // ── A single Level Pass tier ────────────────────────────────',
 '    window.__LP_STATE = JSON.parse(JSON.stringify(parent.__PAYLOADS.levelpass));',
 '    await window.__ccLevelPassRender();',
 '    window.__toasts.length = 0;',
 '    const lp = q(".ccLP-claim:not([data-choose])");',
 '    out.hadLevelClaim = !!lp;',
 '    if (lp) { lp.click(); await waitCard(); await settle(); }',
 '    out.lpTier = readPop();',
 '    out.lpTierToasts = window.__toasts.map(t => t[0]);',
 '    await shut();',
 '    out.popLoaded = typeof window.__ccRewardPop === "function";',
 '  } catch (e) { out.errors.push("THREW: " + (e && e.message ? e.message : String(e))); }',
 '  window.__RESULT = out;',
 '})();'
].join("\\n");
</script>
<script>
(async () => {
  const results = {};
  const run = async (key, opts, width) => {
    const ifr = document.createElement("iframe");
    ifr.width = width;
    document.body.appendChild(ifr);
    const doc = ifr.contentDocument;
    doc.open(); doc.write(innerHtml(opts)); doc.close();
    for (let i = 0; i < 4000 && !ifr.contentWindow.__RESULT; i++) {
      await new Promise(r => setTimeout(r, 25));
    }
    results[key] = ifr.contentWindow.__RESULT || { errors: ["never reported in"] };
  };

  await run("pop",    { pop: true,  passes: false, main: MAIN_POP },    1280);
  await run("phone",  { pop: true,  passes: false, main: MAIN_PHONE },   390);
  await run("passes", { pop: true,  passes: true,  main: MAIN_PASSES }, 1280);
  // The SAME harness with the module left out: the news has to come back as a
  // toast, or a player who claimed a reward is told nothing at all.
  await run("nopop",  { pop: false, passes: true,  main: MAIN_PASSES }, 1280);

  document.getElementById("RESULT").textContent = "@@" + JSON.stringify(results) + "@@";
})();
</script>
</body></html>`;

const file = path.join(os.tmpdir(), `cc_reward_pop_${Date.now()}.html`);
fs.writeFileSync(file, page);

const dom = execFileSync(CHROME, [
  "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
  "--window-size=1600,1200", "--virtual-time-budget=120000",
  "--dump-dom", "file://" + file,
], { encoding: "utf8", maxBuffer: 128 * 1024 * 1024, stdio: ["pipe", "pipe", "ignore"] });

const m = dom.match(/@@([\s\S]*?)@@/);
if (!m) { console.error("no result payload in the DOM dump"); process.exit(1); }
const R = JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                         .replace(/&lt;/g, "<").replace(/&gt;/g, ">"));

// ══════════════════════════════════════════════════════════════════════════
console.log("\nthe card, in a browser");
{
  const A = R.pop || { errors: ["no result"] };
  check("the scenario reported in", (A.errors || []).length === 0, (A.errors || []).join(" | "));
  check("the module registers itself", A.registered === true);
  check("nothing is drawn until something is claimed", A.nothingDrawnYet === true);
  check("a card with no title is refused", A.refusesEmpty === true);
  check("a real one is accepted and appears", A.accepted === true && A.appeared === true,
        `${A.accepted} / ${A.appeared}`);
  check("…as a flex overlay that is actually displayed",
        A.open === true && A.display === "flex", `${A.open} / ${A.display}`);
  check("…carrying the words it was handed",
        A.eyebrow === "Level Pass Reward" && A.title === "+250 Critter Coins"
        && A.detail === "Added to your balance.",
        `${A.eyebrow} | ${A.title} | ${A.detail}`);
  check("…with no picture and no icon on it", A.pictures === 0, A.pictures);
  check("…and no emoji in any of it",
        !EMOJI.test([A.eyebrow, A.title, A.detail].join(" ")));

  console.log("\n  the animation is really running:");
  const an = A.anim || {};
  check("the card pops rather than appearing", an.card === "ccRP-pop", an.card);
  check("the overlay fades in behind it", an.overlay === "ccRP-fade", an.overlay);
  check("two rings go out of it", an.rings === 2 && an.ring === "ccRP-ring",
        `${an.rings} / ${an.ring}`);
  check("light sweeps across it once", an.sheen === "ccRP-sheen", an.sheen);
  check("the words rise in", an.title === "ccRP-rise", an.title);
  check("a hairline winds it down", an.hair === "ccRP-hair", an.hair);
  check("…over exactly HOLD_MS, handed over from the module",
        an.hairMs === (HOLD_MS / 1000) + "s", `${an.hairMs} vs ${HOLD_MS}ms`);

  console.log("\n  and it closes, three ways:");
  check("it takes no focus and traps none", A.focusables === 0, A.focusables);
  check("…it is announced rather than asked",
        A.role === "status" && A.live === "polite", `${A.role} / ${A.live}`);
  check("a tap anywhere closes it", A.goneOnTap === true && A.stillOpenAfterTap === false,
        `${A.goneOnTap} / ${A.stillOpenAfterTap}`);
  check("Escape closes it", A.reopened === true && A.goneOnEscape === true,
        `${A.reopened} / ${A.goneOnEscape}`);
  check("and it closes itself, without anybody touching it", A.goneByItself === true);
  check("…after about HOLD_MS, not instantly and not forever",
        A.heldMs >= HOLD_MS * 0.7 && A.heldMs < HOLD_MS + 4000, A.heldMs);
  check("four cards queue and the fifth is refused, so the caller toasts it",
        JSON.stringify(A.queue) === JSON.stringify([true, true, true, true, false]),
        JSON.stringify(A.queue));
}

console.log("\nthe card on a phone (390px, measured in its own viewport)");
{
  const B = R.phone || { errors: ["no result"] };
  check("the scenario reported in", (B.errors || []).length === 0, (B.errors || []).join(" | "));
  for (const [name, m2] of [["a reward", B.reward], ["the unlock", B.unlock]]) {
    check(`${name} card fits the screen`,
          m2 && m2.w > 240 && m2.w <= m2.vw - 20, m2 && `${m2.w} in ${m2.vw}`);
    check(`…with a gutter on both sides`, m2 && m2.left >= 8 && m2.right <= m2.vw - 8,
          m2 && `${m2.left} .. ${m2.right} of ${m2.vw}`);
    check(`…and the page does not scroll sideways because of it`,
          m2 && m2.docScrolls === false);
    check(`…and its headline is still a headline`, m2 && m2.titlePx >= 18, m2 && m2.titlePx);
  }
}

console.log("\nthrough the two passes, the way a player gets there");
{
  const C = R.passes || { errors: ["no result"] };
  check("the scenario reported in", (C.errors || []).length === 0, (C.errors || []).join(" | "));
  check("reward-pop.js is loaded in this frame", C.popLoaded === true);

  const t = C.cpTier || {};
  check("collecting a Critter Pass tier pops a card",
        C.hadClaimButton === true && t.shown === true, `${C.hadClaimButton} / ${t.shown}`);
  check("…naming the pass and the reward",
        t.eyebrow === "Critter Pass Reward" && /Critter Coins/.test(t.title || ""),
        `${t.eyebrow} | ${t.title}`);
  check("…and the same news is NOT also thrown as a toast",
        (C.cpTierToasts || []).length === 0, JSON.stringify(C.cpTierToasts));

  const a = C.cpAll || {};
  check("a whole-track sweep pops ONE card, not one per tier",
        C.hadClaimAll === true && a.cards === 1, `${C.hadClaimAll} / ${a.cards}`);
  check("…and it counts them", /Claimed 3 Rewards/.test(a.title || ""), a.title);
  check("…without a duplicate toast", (C.cpAllToasts || []).length === 0,
        JSON.stringify(C.cpAllToasts));

  const b = C.cpBuy || {};
  check("unlocking the Critter Pass pops the bigger card",
        C.hadBuyButton === true && b.shown === true && b.unlockKind === true,
        `${C.hadBuyButton} / ${b.shown} / ${b.unlockKind}`);
  check("…and it says the climb starts at Pass Level 1",
        /Unlocked/i.test(b.title || "") && /Pass Level 1/.test(b.detail || ""),
        `${b.title} | ${b.detail}`);
  check("…with no duplicate toast", (C.cpBuyToasts || []).length === 0,
        JSON.stringify(C.cpBuyToasts));

  const l = C.lpTier || {};
  check("collecting a Level Pass tier pops a card too",
        C.hadLevelClaim === true && l.shown === true, `${C.hadLevelClaim} / ${l.shown}`);
  check("…named for the free track, not the paid one",
        l.eyebrow === "Level Pass Reward", l.eyebrow);
  check("…and it is not the unlock card", l.unlockKind === false);
  check("…with no duplicate toast", (C.lpTierToasts || []).length === 0,
        JSON.stringify(C.lpTierToasts));
  // Nothing on any of these cards is an emoji, measured on the rendered words.
  const words = [t, a, b, l].map(x => [x.eyebrow, x.title, x.detail].join(" ")).join(" ");
  check("no emoji reached the screen", !EMOJI.test(words), words.slice(0, 120));
  check("and no em dash either", !DASHES.test(words), words.slice(0, 120));
}

console.log("\nand with reward-pop.js never served, the news still lands");
{
  const D = R.nopop || { errors: ["no result"] };
  check("the scenario reported in", (D.errors || []).length === 0, (D.errors || []).join(" | "));
  check("the module really is absent", D.popLoaded === false);
  check("no card is drawn", (D.cpTier || {}).shown !== true);
  check("a collected Critter Pass tier is said as a toast instead",
        (D.cpTierToasts || []).some(x => /Critter Coins/.test(x)),
        JSON.stringify(D.cpTierToasts));
  check("a whole-track sweep too", (D.cpAllToasts || []).some(x => /^Claimed 3 reward/.test(x)),
        JSON.stringify(D.cpAllToasts));
  check("so is the unlock", (D.cpBuyToasts || []).some(x => /Critter Pass unlocked/.test(x)),
        JSON.stringify(D.cpBuyToasts));
  check("and so is a Level Pass tier",
        (D.lpTierToasts || []).some(x => /Critter Coins/.test(x)),
        JSON.stringify(D.lpTierToasts));
  check("no toast is an emoji toast any more",
        !EMOJI.test([].concat(D.cpTierToasts || [], D.cpAllToasts || [],
                              D.cpBuyToasts || [], D.lpTierToasts || []).join(" ")));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
