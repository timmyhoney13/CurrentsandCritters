#!/usr/bin/env node
/* THE FULL-SCREEN TOGGLE ON THE MENU.
 *
 * Run:  node test_fullscreen_chip.js      (needs Google Chrome / Chromium)
 *
 * What went wrong: the only full-screen control the menu had was a chip that
 * appeared in the BOTTOM-LEFT corner, and only for a player who had already
 * opted into full screen and then fallen out of it. So for almost everyone,
 * on almost every tab, the menu carried no full-screen control at all: there
 * was nothing to find, nothing to click, and nothing a tutorial could point at.
 *
 * The contract now:
 *   1. ONE CORNER, ALWAYS. #cc-fs-resume is fixed in the BOTTOM-RIGHT corner,
 *      it is on screen on every tab of the menu, and it does not move between
 *      them (same rectangle on Overview, Store, Clans, Friends, Messages).
 *   2. NOTHING COVERS IT. The top-most element at the chip's own centre is the
 *      chip, at every width: a control you cannot click is not a control.
 *   3. IT TOGGLES BOTH WAYS, and says which way it will go ("Full screen" /
 *      "Exit full screen").
 *   4. IT STAYS OUT OF A GAME, where the action bar carries its own
 *      "⛶ Full Screen" button and the bottom-right corner is the floating log.
 *   5. THE MAIN MENU TOUR TEACHES IT: a step targeting the chip, holding it
 *      open so its label can be read.
 *
 * The drive half runs the REAL setupGameWindowFullscreen() sliced out of
 * preview-app.js against the REAL preview.css and the REAL lobby markup, at
 * five widths, because a check at one window size is how the last corner bug
 * got through.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const HTML = read("preview.html");
const CSS  = read("css/preview.css");
const APP  = read("js/preview-app.js");
const TUT  = read("js/tutorials.js");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

// The one CSS rule that pins the chip.
const RULE = (() => {
  const a = CSS.indexOf("#cc-fs-resume {");
  return a < 0 ? "" : CSS.slice(a, CSS.indexOf("}", a) + 1);
})();

// ════════════════════════════════════════════════════════════════════════
//  SOURCE
// ════════════════════════════════════════════════════════════════════════
console.log("\npinned to the bottom-right corner");
{
  check("the chip exists in the page", /id="cc-fs-resume"/.test(HTML));
  check("it is position:fixed, so no tab panel can scroll it away",
        /position:\s*fixed/.test(RULE), RULE.slice(0, 80));
  check("anchored to the RIGHT edge", /\bright:\s*calc\(14px/.test(RULE));
  check("anchored to the BOTTOM edge", /\bbottom:\s*calc\(14px/.test(RULE));
  check("and NOT to the left edge (the old corner)", !/\bleft:\s*\d/.test(RULE), RULE);
  check("it clears the phone's safe areas on both axes",
        /env\(safe-area-inset-right/.test(RULE) && /env\(safe-area-inset-bottom/.test(RULE));
  check("still gated on being through the door",
        /body\.cc-signed-in #cc-fs-resume\.show \{ display: flex; \}/.test(CSS));
}

// The real toggle, sliced whole so the drive half runs the shipped code.
const FS_SRC = (() => {
  const a = APP.indexOf("(function setupGameWindowFullscreen() {");
  if (a < 0) return "";
  const b = APP.indexOf("\n  })();", a);
  return b < 0 ? "" : APP.slice(a, b + 8);
})();

console.log("\nthe toggle itself");
{
  check("setupGameWindowFullscreen is still one findable block", FS_SRC.length > 500,
        String(FS_SRC.length));
  check("it shows the chip whenever the game screen is DOWN",
        /classList\.toggle\("show", !inGame\(\)\)/.test(FS_SRC));
  check("visibility is no longer gated on having fallen out of full screen",
        !/wantsFullscreen && !isFs\(\) && !inGame\(\)/.test(FS_SRC));
  check("clicking it in full screen EXITS full screen", /if \(isFs\(\)\) \{[\s\S]{0,220}exitFs\(\)/.test(FS_SRC));
  check("clicking it out of full screen ENTERS it", /enterFs\(\)/.test(FS_SRC));
  check("the label follows the state",
        /isFs\(\) \? "Exit full screen" : "Full screen"/.test(FS_SRC) ||
        /fs \? "Exit full screen" : "Full screen"/.test(FS_SRC));
  check("the title and aria-label follow it too",
        /resume\.title = label/.test(FS_SRC) && /setAttribute\("aria-label", label\)/.test(FS_SRC));
  check("it re-syncs on both fullscreenchange spellings",
        /addEventListener\("fullscreenchange", syncFsChip\)/.test(FS_SRC) &&
        /addEventListener\("webkitfullscreenchange", syncFsChip\)/.test(FS_SRC));
  check("…and when the game screen opens or closes",
        /MutationObserver\(syncFsChip\)/.test(FS_SRC));
  check("it is synced once at boot, not left blank until something happens",
        /\n    syncFsChip\(\);/.test(FS_SRC));
  check("the in-game action bar keeps its own button",
        /id="pv-fullscreen-btn"/.test(HTML));
}

console.log("\nthe Main Menu tour teaches it");
{
  const steps = TUT.slice(TUT.indexOf("const MENU_STEPS = ["), TUT.indexOf("function runMenuTour"));
  const step = (() => {
    const a = steps.indexOf('{ target: "#cc-fs-resume"');
    return a < 0 ? "" : steps.slice(a, steps.indexOf("\n\n", a));
  })();
  check("a Main Menu tour step targets the chip", !!step);
  check("it says the corner it is in", /bottom-right corner/.test(step), step.slice(0, 120));
  check("it says it is on every tab", /every tab of the menu/i.test(step));
  check("it says the toggle goes back out again", /click it again/i.test(step));
  check("it holds the chip open so the label can be read",
        /fsChipPeek\(true\)/.test(step) && /function fsChipPeek/.test(TUT));
  check("…and every other step folds it back up",
        /fsChipPeek\(false\);\n  \}/.test(TUT));
  check("the peek state really opens the label in CSS",
        /#cc-fs-resume\.ccfs-peek \.ccfs-word \{ max-width: 190px/.test(CSS));
  check("the step sits inside MENU_STEPS, not another tour",
        steps.includes('target: "#cc-fs-resume"'));
}

// ════════════════════════════════════════════════════════════════════════
//  DRIVE
// ════════════════════════════════════════════════════════════════════════
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => fs.existsSync(p));

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: skipping the drive half.");
} else {
  const PORT = 9720 + (process.pid % 260);
  const SERVER_SRC = `
    const fs=require("fs"),path=require("path"),http=require("http");
    const ROOT=${JSON.stringify(ROOT)};
    const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",
      ".json":"application/json",".png":"image/png",".jpg":"image/jpeg",
      ".webp":"image/webp",".svg":"image/svg+xml",".ico":"image/x-icon"};
    http.createServer((req,res)=>{
      const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
      let f=path.join(ROOT,rel);
      if(!fs.existsSync(f)) f=path.join(ROOT,"multiplayer/client",rel);
      if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);res.end();return;}
      res.writeHead(200,{"Content-Type":MIME[path.extname(f)]||"application/octet-stream"});
      fs.createReadStream(f).pipe(res);
    }).listen(${PORT});
  `;

  const slice = (startMark, endMark) => {
    const a = HTML.indexOf(startMark);
    const b = HTML.indexOf(endMark, a + 10);
    return a < 0 || b < 0 ? "" : HTML.slice(a, b);
  };
  const LOBBY  = slice('<div id="auth-stats-lobby"', "<!-- ══");
  const SPLASH = slice('<div id="cc-fs-splash">', '<!-- ══ AUTH LOADING');

  // Every tab the menu has a panel for; the chip must be in the same corner on
  // all of them.
  const TABS = ["overview", "friends", "messages", "leaderboard", "store", "clans"];
  const WIDTHS = [390, 768, 1024, 1280, 1600];

  const PAGE = `<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="/multiplayer/client/css/preview.css">
<body class="cc-signed-in">
${SPLASH}
${LOBBY.replace('<div id="auth-stats-lobby" data-bg-tab="overview">',
                '<div id="auth-stats-lobby" class="visible" data-bg-tab="overview">')}
<div id="pv-game" style="display:none; flex-direction:column; height:100vh;"></div>
<div id="out">PENDING</div>
<script>
// The two things the real block leans on that live elsewhere in the app.
function ccReport() {}
function showToast() {}
${FS_SRC}
function box(el) { var r = el.getBoundingClientRect();
  return { l: Math.round(r.left), t: Math.round(r.top),
           r: Math.round(r.right), b: Math.round(r.bottom),
           w: Math.round(r.width), h: Math.round(r.height) }; }
function showTab(tab) {
  var lob = document.getElementById("auth-stats-lobby");
  lob.setAttribute("data-bg-tab", tab);
  [].forEach.call(document.querySelectorAll(".ph-panel"), function (p) {
    p.style.display = (p.id === "ph-panel-" + tab) ? "" : "none";
  });
}
setTimeout(function () {
  var res = { w: window.innerWidth, h: window.innerHeight, tabs: {} };
  try {
    var chip = document.getElementById("cc-fs-resume");
    var cs = getComputedStyle(chip);
    res.display = cs.display;
    res.position = cs.position;
    res.label = (chip.querySelector(".ccfs-word") || {}).textContent || "";
    res.title = chip.getAttribute("title") || "";
    res.shows = chip.classList.contains("show");
    ${JSON.stringify(TABS)}.forEach(function (tab) {
      showTab(tab);
      var b = box(chip);
      var cx = (b.l + b.r) / 2, cy = (b.t + b.b) / 2;
      var hit = document.elementFromPoint(cx, cy);
      res.tabs[tab] = {
        box: b,
        gapRight: Math.round(window.innerWidth - b.r),
        gapBottom: Math.round(window.innerHeight - b.b),
        onScreen: b.l >= 0 && b.t >= 0 && b.r <= window.innerWidth + 1 && b.b <= window.innerHeight + 1,
        // The chip or one of its own spans, i.e. nothing is painted over it.
        clickable: !!(hit && (hit === chip || chip.contains(hit))),
        hit: hit ? (hit.id || hit.className || hit.tagName) : "none"
      };
    });
    showTab("overview");
    // …and it gets out of the way of a game.
    document.getElementById("pv-game").style.display = "flex";
    setTimeout(function () {
      res.inGameShows = chip.classList.contains("show");
      res.inGameDisplay = getComputedStyle(chip).display;
      document.getElementById("out").textContent = JSON.stringify(res);
    }, 400);
    return;
  } catch (e) { res.err = String(e && e.message); }
  document.getElementById("out").textContent = JSON.stringify(res);
}, 1200);
</script>`;

  const FILE = "__fschip.html";
  fs.writeFileSync(path.join(ROOT, FILE), PAGE);
  const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });

  const rows = {};
  try {
    for (const w of WIDTHS) {
      for (let attempt = 0; attempt < 3 && !rows[w]; attempt++) {
        const dom = execFileSync(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
          "--hide-scrollbars", `--window-size=${w},860`, "--virtual-time-budget=20000",
          "--dump-dom", `http://localhost:${PORT}/${FILE}`],
          { encoding: "utf8", maxBuffer: 64e6, stdio: ["ignore", "pipe", "ignore"] });
        const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
        const raw = m ? m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                            .replace(/&lt;/g, "<").replace(/&gt;/g, ">") : "";
        if (raw && raw !== "PENDING") { try { rows[w] = JSON.parse(raw); } catch (_) {} }
      }
    }
  } finally {
    try { fs.unlinkSync(path.join(ROOT, FILE)); } catch (_) {}
    server.kill();
  }

  console.log("\nmeasured in a real browser, at five widths");
  WIDTHS.forEach((w) => {
    const r = rows[w];
    console.log(`  ── ${w}px ──`);
    if (!r || r.err) {
      fail++;
      console.log("  ✗ FAIL: the menu never reported" + (r && r.err ? ": " + r.err : ""));
      return;
    }
    check(`  the chip is on screen at all`, r.display === "flex", r.display);
    check(`  the real code turned it on`, r.shows === true);
    check(`  it is fixed`, r.position === "fixed", r.position);
    check(`  it reads "Full screen" out of full screen`, /^Full screen$/i.test(r.label.trim()), r.label);
    const first = r.tabs.overview;
    Object.entries(r.tabs).forEach(([tab, t]) => {
      check(`  ${tab}: fully on screen`, t.onScreen, JSON.stringify(t.box));
      check(`  ${tab}: in the BOTTOM-RIGHT corner`,
            t.gapRight >= 0 && t.gapRight <= 40 && t.gapBottom >= 0 && t.gapBottom <= 40,
            `right gap ${t.gapRight}, bottom gap ${t.gapBottom}`);
      check(`  ${tab}: nothing is painted over it`, t.clickable, t.hit);
      check(`  ${tab}: same rectangle as Overview`,
            t.box.l === first.box.l && t.box.t === first.box.t &&
            t.box.r === first.box.r && t.box.b === first.box.b,
            `${JSON.stringify(t.box)} vs ${JSON.stringify(first.box)}`);
    });
    check(`  it gets out of the way inside a game`,
          r.inGameShows === false && r.inGameDisplay === "none",
          `show=${r.inGameShows} display=${r.inGameDisplay}`);
  });
}

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
