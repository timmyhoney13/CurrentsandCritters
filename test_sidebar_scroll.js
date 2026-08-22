#!/usr/bin/env node
/* Browser check for the LEFT SIDEBAR on Player Home.
 *
 * Run:  node test_sidebar_scroll.js       (needs Google Chrome installed)
 *
 * The bug this pins down: on desktop the sidebar (#ph-sidebar) is the scroller
 *: `overflow-y:auto` under `max-height:calc(100vh - 40px)`, and the white
 * card inside it (.ph-sidebar-nav-card) was `flex:1; min-height:0`. On a short
 * screen the nav + Daily Streak section is TALLER than the viewport, and
 * `min-height:0` let the card shrink to the visible height while its own
 * content kept flowing out the bottom. So you scroll down to reach "View
 * streak details" and the white panel has already ended above it: the streak
 * block, its dots, the XP rows and the button all sit on the page background
 * with no card behind them.
 *
 * What is checked, in real screen pixels in headless Chrome against the REAL
 * preview.css and the REAL sidebar markup sliced out of preview.html:
 *   1. CARD COVERS CONTENT, the white card's painted box reaches at least as
 *      far down as its last child (the "View streak details" button), at every
 *      height, scrolled to the bottom.
 *   2. SCROLLED TO THE BOTTOM, with the sidebar scrolled all the way down,
 *      the button is on screen AND the card is painted behind it (hit-test at
 *      the button's corners lands inside the card).
 *   3. TALL SCREENS, when everything fits, the card still fills the sidebar
 *      (no short card floating above empty space).
 *   4. TUTORIAL, the Main Menu tour spotlights ".ph-sidebar-streak" and
 *      "#ph-ss-details-btn"; after the tour's scrollIntoView, both must be
 *      visible AND backed by the card.
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
  console.log("SKIP: no Chrome/Chromium found: cannot run the sidebar layout check.");
  process.exit(0);
}

const CSS  = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");

// ── Pull the real sidebar out of preview.html ───────────────────────────────
const SB_START = HTML.indexOf('<div class="ph-sidebar" id="ph-sidebar">');
const SB_END   = HTML.indexOf('</div><!-- /.ph-sidebar -->');
if (SB_START < 0 || SB_END < 0) {
  console.error("FAIL: could not find the .ph-sidebar block in preview.html");
  process.exit(1);
}
const SIDEBAR = HTML.slice(SB_START, SB_END + '</div><!-- /.ph-sidebar -->'.length);

// The tutorial spotlights these two, in this order (js/tutorials.js).
const TUT = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/tutorials.js"), "utf8");
for (const sel of ['".ph-sidebar-streak"', '"#ph-ss-details-btn"']) {
  if (!TUT.includes(sel)) {
    console.error(`FAIL: tutorials.js no longer targets ${sel}: update this test.`);
    process.exit(1);
  }
}

const page = (rescue) => `<!doctype html><html><head><meta charset="utf-8">
<title>sidebar scroll</title>
<style>${CSS}</style>
<style>
  /* Fonts are remote in the real app; keep the box model, not the typeface. */
  * { font-family: sans-serif !important; }
  html, body { margin: 0; padding: 0; height: 100%; }
  #auth-stats-lobby { display: flex; }
  /* Stand-in for the real content column: taller than any screen. */
  .ph-wrap { min-height: 2400px; }
  ${rescue ? "#ph-ss-rescue { display: flex !important; flex-direction: column; }" : ""}
</style>
</head><body>
<div id="auth-stats-lobby" class="visible" data-bg-tab="overview">
${SIDEBAR}
  <div class="ph-wrap"></div>
</div>
<div id="out" style="display:none;"></div>
<script>
(function () {
  const lines = [];
  const say = (s) => lines.push(s);
  const sb   = document.getElementById("ph-sidebar");
  const card = document.querySelector(".ph-sidebar-nav-card");
  const btn  = document.getElementById("ph-ss-details-btn");
  const strk = document.querySelector(".ph-sidebar-streak");

  say("SIDEBAR scrollH=" + Math.round(sb.scrollHeight) + " clientH=" + Math.round(sb.clientHeight));

  // Scroll the sidebar to the very bottom, the way a player would to reach
  // "View streak details", and the way the tutorial's scrollIntoView does.
  sb.scrollTop = sb.scrollHeight;

  const cr = card.getBoundingClientRect();
  const br = btn.getBoundingClientRect();
  const sr = strk.getBoundingClientRect();
  say("CARD top=" + Math.round(cr.top) + " bottom=" + Math.round(cr.bottom) + " height=" + Math.round(cr.height));
  say("BTN  top=" + Math.round(br.top) + " bottom=" + Math.round(br.bottom));
  say("STRK top=" + Math.round(sr.top) + " bottom=" + Math.round(sr.bottom));
  say("SBBOX top=" + Math.round(sb.getBoundingClientRect().top) + " bottom=" + Math.round(sb.getBoundingClientRect().bottom));

  // Is the card actually PAINTED behind a point? DOM containment proves
  // nothing here, the streak block stayed a CHILD of the card while spilling
  // out the bottom of it, so this asks the only question that matters: is the
  // point inside the card's own painted box?
  function backedByCard(x, y) {
    const r = card.getBoundingClientRect();
    return x >= r.left - 1 && x <= r.right + 1 && y >= r.top - 1 && y <= r.bottom + 1;
  }
  for (const [name, r] of [["STRK", sr], ["BTN", br]]) {
    const pts = [
      ["tl", r.left + 2,  r.top + 2],
      ["tr", r.right - 2, r.top + 2],
      ["bl", r.left + 2,  r.bottom - 2],
      ["br", r.right - 2, r.bottom - 2],
    ];
    for (const [tag, x, y] of pts) {
      say("HIT " + name + "." + tag + "=" + (backedByCard(x, y) ? "card" : "BARE"));
    }
  }
  // ── The tutorial pass ────────────────────────────────────────────────
  // The Main Menu tour walks to ".ph-sidebar-streak" and then to
  // "#ph-ss-details-btn", and reaches each one exactly the way tutorials.js
  // does: scrollIntoView({block:"center"}). Whatever that lands on is what the
  // player sees through the spotlight hole, so it must be on screen inside the
  // sidebar AND painted on the white card, all four corners of it.
  for (const [name, el] of [["TUT-STRK", strk], ["TUT-BTN", btn]]) {
    el.scrollIntoView({ block: "center", behavior: "instant" });
    const r = el.getBoundingClientRect();
    const sbr = sb.getBoundingClientRect();
    const onScreen = r.top >= sbr.top - 1 && r.bottom <= sbr.bottom + 1;
    say(name + " top=" + Math.round(r.top) + " bottom=" + Math.round(r.bottom) +
        " onscreen=" + (onScreen ? "yes" : "NO"));
    const pts = [
      ["tl", r.left + 2,  r.top + 2],
      ["tr", r.right - 2, r.top + 2],
      ["bl", r.left + 2,  r.bottom - 2],
      ["br", r.right - 2, r.bottom - 2],
    ];
    for (const [tag, x, y] of pts) {
      say("HIT " + name + "." + tag + "=" + (backedByCard(x, y) ? "card" : "BARE"));
    }
  }

  document.getElementById("out").textContent = lines.join("\\n");
})();
</script>
</body></html>`;

function run(width, height, rescue) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-sidebar-"));
  const file = path.join(tmp, "sidebar.html");
  fs.writeFileSync(file, page(rescue));
  let dom;
  try {
    dom = execFileSync(CHROME, [
      "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      `--window-size=${width},${height}`, "--virtual-time-budget=6000",
      "--dump-dom", "file://" + file,
    ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 90000 });
  } catch (e) {
    console.error("Chrome failed to run:", e.message);
    process.exit(1);
  }
  fs.rmSync(tmp, { recursive: true, force: true });
  const m = dom.match(/<div id="out"[^>]*>([\s\S]*?)<\/div>/);
  const report = m
    ? m[1].replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").trim()
    : "(no output)";
  return report.split("\n");
}

// Heights a real laptop actually has: a 13" MacBook is 1440x800 in CSS px with
// the browser chrome taken off, and a short window is shorter still.
const SIZES = [
  // 1100-1180px wide is the awkward band: the desktop sidebar is on (min-width
  // 1100) while the max-width:1180 rules have turned the nav into two columns.
  [1100, 800],
  [1180, 800],
  [1440, 700],
  [1440, 800],
  [1512, 900],
  [1440, 950],    // just short: the sidebar overflows by a hair
  [1440, 1000],   // just tall enough: the changeover between the two branches
  [1600, 1200],   // tall: everything fits, card must still fill the sidebar
  // Worst case: the Streak Rescue countdown is showing (it only appears while a
  // broken run can still be saved), which makes the sidebar taller still.
  [1440, 800, "rescue"],
];

let failures = 0;
for (const [w, h, rescue] of SIZES) {
  const out = run(w, h, rescue);
  const get = (p) => out.find(l => l.startsWith(p)) || "";
  const num = (line, key) => {
    const m = line.match(new RegExp(key + "=(-?\\d+)"));
    return m ? parseInt(m[1], 10) : NaN;
  };
  const sbLine = get("SIDEBAR");
  const cardL  = get("CARD");
  const btnL   = get("BTN");
  const sbBox  = get("SBBOX");
  const scrollH = num(sbLine, "scrollH"), clientH = num(sbLine, "clientH");
  const cardBottom = num(cardL, "bottom");
  const btnBottom  = num(btnL, "bottom");
  const sbBottom   = num(sbBox, "bottom");
  const bare = out.filter(l => l.startsWith("HIT") && l.endsWith("BARE"));

  const problems = [];
  // 1. the white card must reach past its own last child
  if (!(cardBottom >= btnBottom)) {
    problems.push(`card ends at ${cardBottom} but its last button ends at ${btnBottom} (${btnBottom - cardBottom}px of bare page)`);
  }
  // 2. nothing in the streak block may sit on the page background
  if (bare.length) problems.push(`not painted on the card: ${bare.map(l => l.split(" ")[1].split("=")[0]).join(", ")}`);
  // 3. scrolled to the bottom, the button must be on screen inside the sidebar
  if (!(btnBottom <= sbBottom + 1)) {
    problems.push(`scrolled to the bottom, the button still ends ${btnBottom - sbBottom}px below the sidebar`);
  }
  // 4. when it all fits, the card must still fill the sidebar (no gap under it)
  if (scrollH <= clientH + 1 && cardBottom < sbBottom - 2) {
    problems.push(`nothing to scroll, yet the card stops ${sbBottom - cardBottom}px short of the sidebar bottom`);
  }
  // 5. the tutorial's two sidebar steps: scrolled to by the real tour, both
  //    must end up on screen and fully backed by the white card.
  for (const step of ["TUT-STRK", "TUT-BTN"]) {
    const line = get(step + " ");
    if (!line) { problems.push(`the tutorial step ${step} never reported`); continue; }
    if (!line.includes("onscreen=yes")) {
      problems.push(`tutorial: after scrolling to it, ${step} is not fully inside the sidebar`);
    }
  }

  const scrolls = (scrollH > clientH + 1 ? "scrolls" : "fits") + (rescue ? ", rescue showing" : "");
  if (problems.length) {
    failures++;
    console.log(`FAIL  ${w}x${h} (${scrolls})`);
    for (const p of problems) console.log(`        - ${p}`);
    for (const l of out) console.log(`        | ${l}`);
  } else {
    console.log(`PASS  ${w}x${h} (${scrolls})  card ${cardBottom} >= button ${btnBottom}`);
  }
}

if (failures) {
  console.log(`\n${failures} size(s) failed: the sidebar card does not cover its own content.`);
  process.exit(1);
}
console.log("\nAll sizes pass: the white sidebar card covers the Daily Streak section down to the last button.");
