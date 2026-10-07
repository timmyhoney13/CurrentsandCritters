#!/usr/bin/env node
/* THE FULL SCREEN BUTTON ON PLAYER HOME.
 *
 * Run:  node test_fullscreen_button.js      (needs Google Chrome / Chromium)
 *
 * Full screen used to be a PICTURE. The launch splash was one baked painting
 * (fullscreen-splash.png) covering the page after sign-in, with an invisible
 * click box positioned over a painted "Play Full Screen" as a percentage of
 * the stretched artwork, and a second invisible box over a painted sardine
 * that unlocked the Sardine avatar.
 *
 * It is a real button now: #ph-fullscreen, bottom-right of Player Home, on
 * every tab, wearing that tab's own frosted-glass recipe. The Sardine is
 * earned by completing a trade instead of by finding a fish in a painting.
 *
 * The contract:
 *   1. THE SPLASH IS GONE: no markup, no styling, no code, no art files, and
 *      no server route for the art.
 *   2. THE BUTTON EXISTS AND SAYS "Full Screen", and its label flips when the
 *      document is full screen.
 *   3. IT IS THEMED PER TAB: every tab that repaints Player Home repaints the
 *      button too, so none of them is left showing a button that belongs to a
 *      different page.
 *   4. THE SARDINE IS EARNED BY TRADING, once per completion, from both the
 *      trade screen and the trade card in a conversation.
 *   5. THE GAME KEEPS ITS OWN BUTTON in the action bar.
 *   6. DRIVEN: the REAL preview.html is loaded in a real browser, put into the
 *      signed-in Player Home state, and the button is found in the bottom-right
 *      corner, on every tab, at every desktop width -- and gone on a phone.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const HTML   = read("preview.html");
const CSS    = read("css/preview.css");
const APP    = read("js/preview-app.js");
const TUT    = read("js/tutorials.js");
const SERVER = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

// ════════════════════════════════════════════════════════════════════════
//  1. THE SPLASH IS GONE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe launch splash is gone, all of it");
// The ids and classes, in the markup. The explanatory comments are allowed to
// name what was removed, so the page is read with its comments stripped.
const HTML_CODE = HTML.replace(/<!--[\s\S]*?-->/g, "");
check("no splash markup", !/cc-fs-splash|ccfs-/.test(HTML_CODE));
check("no splash art in the page", !/fullscreen-splash/.test(HTML_CODE));
check("no splash code", !/cc-fs-splash|ccfs-|cc_fs_splash_dismissed|cc-fullscreen-wanted/.test(APP));
// With comments stripped: the block that explains the removal is allowed to
// name what it removed, and does.
const CSS_RULES = CSS.replace(/\/\*[\s\S]*?\*\//g, " ");
check("no splash styling", !/#cc-fs-splash|\.ccfs-/.test(CSS_RULES));
check("no tutorial step pointing at it", !/cc-fs-splash|ccfs-/.test(TUT));
check("the art files are deleted",
      !fs.existsSync(path.join(CLIENT, "fullscreen-splash.png"))
      && !fs.existsSync(path.join(CLIENT, "fullscreen-splash.webp")));
check("and the server no longer serves them", !/fullscreen-splash/.test(SERVER));

// ════════════════════════════════════════════════════════════════════════
//  2. THE BUTTON
// ════════════════════════════════════════════════════════════════════════
console.log("\nPlayer Home has a Full Screen button");
check("it is in the page, inside #auth-stats-lobby",
      /<button[^>]*id="ph-fullscreen"[^>]*class="ph-fsbtn"/.test(HTML)
      && HTML.indexOf('id="ph-fullscreen"') > HTML.indexOf('<div id="auth-stats-lobby"')
      && HTML.indexOf('id="ph-fullscreen"') < HTML.indexOf('<!-- ══ PUBLIC PROFILE MODAL'));
check("it says Full Screen", /id="ph-fullscreen"[\s\S]{0,260}?>Full Screen</.test(HTML));
check("it is wired up", /getElementById\("ph-fullscreen"\)/.test(APP));
check("the label flips while full screen",
      /isFs\(\) \? "Exit Full Screen" : "Full Screen"/.test(APP));
check("it asks the document for full screen, with the webkit spelling",
      /el\.requestFullscreen/.test(APP) && /el\.webkitRequestFullscreen/.test(APP));
check("and says so when the browser refuses",
      /Full screen was blocked by the browser/.test(APP));

console.log("\nit sits in the bottom-right corner, and not on a phone");
const base = (/\.ph-fsbtn \{([\s\S]*?)\}/.exec(CSS) || [, ""])[1];
check("fixed to the viewport", /position:\s*fixed/.test(base));
check("bottom right", /right:\s*16px/.test(base) && /bottom:\s*16px/.test(base));
check("hidden on a phone and on a touch screen",
      /@media \(max-width: 760px\), \(pointer: coarse\) \{\s*\.ph-fsbtn \{ display: none; \}/.test(CSS));

// ════════════════════════════════════════════════════════════════════════
//  3. ONE RECIPE PER TAB
// ════════════════════════════════════════════════════════════════════════
console.log("\nevery tab that repaints Player Home repaints the button too");
// The source of truth is the stylesheet itself: whichever tabs give .ph-scard
// its own look must give .ph-fsbtn one, or that tab shows a button that
// belongs to a different page.
const themed = [...new Set(
  [...CSS.matchAll(/#auth-stats-lobby\[data-bg-tab="([a-z]+)"\] \.ph-scard/g)].map(m => m[1])
)].sort();
const styled = [...new Set(
  [...CSS.matchAll(/#auth-stats-lobby\[data-bg-tab="([a-z]+)"\] \.ph-fsbtn/g)].map(m => m[1])
)].sort();
check("there is a themed tab to check at all", themed.length >= 8, String(themed.length));
themed.forEach(t => check(`  ${t} styles the button`, styled.includes(t)));
// The Critter Pass is the one DARK Player Home page, so it is called out by
// name: a pale button on the kelp forest is the exact bug this guards.
check("the dark Critter Pass page gets its own treatment",
      styled.includes("critterpass")
      && /\[data-bg-tab="critterpass"\] \.ph-fsbtn \{[^}]*color: #eaf7ff/.test(CSS));

// ════════════════════════════════════════════════════════════════════════
//  4. THE SARDINE MOVED ONTO A TRADE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe Sardine is earned by completing a trade");
check("one place does the once-per-completion work",
      /async function _trAfterTradeCompleted\(\) \{/.test(APP));
check("and it grants the Sardine",
      /_trAfterTradeCompleted\(\)[\s\S]{0,400}?__fishGrantHiddenCeph\?\.\("sardine", "\/avatars\/sardine\.png"/.test(APP));
check("the grant is awaited before the profile re-read, not after",
      /await window\.__fishGrantHiddenCeph[\s\S]{0,140}?await _trRefreshMyProfile\(\)/.test(APP));
check("the trade screen fires it on the open → completed edge",
      /status === "completed" && _trLastStatus !== "completed"\) _trAfterTradeCompleted\(\)/.test(APP));
check("the trade card in a chat fires it too",
      /if \(!overlayFollows\) _trAfterTradeCompleted\(\)/.test(APP));
check("nothing calls the old profile refresh on completion any more",
      !/_trLastStatus !== "completed"\) _trRefreshMyProfile/.test(APP)
      && !/if \(!overlayFollows\) _trRefreshMyProfile/.test(APP));

// ════════════════════════════════════════════════════════════════════════
//  5. THE GAME KEEPS ITS OWN
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe game's action bar is untouched");
check("it still has its own ⛶ Full Screen button",
      /id="pv-fullscreen-btn"[^>]*>⛶ Full Screen</.test(HTML)
      && /getElementById\("pv-fullscreen-btn"\)/.test(APP));

// ════════════════════════════════════════════════════════════════════════
//  6. DRIVE  (the real page, signed-in Player Home, every tab, five widths)
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
  const PORT = 9780 + (process.pid % 200);
  const SERVER_SRC = `
    const fs=require("fs"),path=require("path"),http=require("http");
    const ROOT=${JSON.stringify(CLIENT)};
    const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",
      ".json":"application/json",".png":"image/png",".jpg":"image/jpeg",
      ".webp":"image/webp",".svg":"image/svg+xml",".ico":"image/x-icon",".m4a":"audio/mp4"};
    http.createServer((req,res)=>{
      const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
      const f=path.join(ROOT,rel);
      if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);res.end();return;}
      res.writeHead(200,{"Content-Type":MIME[path.extname(f)]||"application/octet-stream"});
      fs.createReadStream(f).pipe(res);
    }).listen(${PORT});
  `;

  const TABS = ["overview", "howto", "competitive", "history", "friends",
                "messages", "achievements", "leaderboard", "clans",
                "levelpass", "critterpass", "store"];
  // 390 is a phone, where the button must NOT be there. The rest are windows
  // with a mouse, where it must.
  const WIDTHS = [390, 768, 1024, 1280, 1600];

  const DRIVER = `
<script>
(function () {
  function vis(el) {
    if (!el) return false;
    var cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden" || +cs.opacity === 0) return false;
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  var tick = 0;
  var iv = setInterval(function () {
    if (++tick < 40) return;
    clearInterval(iv);
    var res = { tabs: {} };
    try {
      document.body.classList.add("cc-signed-in");
      var ls = document.getElementById("auth-loading-screen");
      if (ls) { ls.classList.add("hidden"); ls.style.display = "none"; }
      var as = document.getElementById("auth-screen");
      if (as) { as.classList.add("hidden"); as.style.display = "none"; }
      var game = document.getElementById("pv-game"); if (game) game.style.display = "none";
      var lob = document.getElementById("auth-stats-lobby");
      res.lobby = !!lob;
      if (lob) { lob.classList.add("visible"); lob.style.display = ""; }
      res.lobbyShown = vis(lob);

      var btn = document.getElementById("ph-fullscreen");
      res.exists = !!btn;
      // The button cross-fades its glass when the tab changes (there is a
      // transition on background), so a computed style read in the same frame
      // as the switch reports the frame the fade STARTED from, which is the
      // previous tab's. Turning the transition off measures the resting look,
      // which is the thing being checked.
      var hadTransition = btn ? btn.style.transition : "";
      if (btn) btn.style.transition = "none";
      ${JSON.stringify(TABS)}.forEach(function (tab) {
        if (lob) lob.setAttribute("data-bg-tab", tab);
        [].forEach.call(document.querySelectorAll(".ph-panel"), function (p) {
          p.style.display = (p.id === "ph-panel-" + tab) ? "" : "none";
        });
        var r = btn ? btn.getBoundingClientRect() : null;
        var cs = btn ? getComputedStyle(btn) : null;
        res.tabs[tab] = {
          vis: vis(btn),
          text: btn ? btn.textContent.trim() : "",
          // How far the button's edges are from the window's bottom-right.
          gapR: r ? Math.round(innerWidth - r.right) : -1,
          gapB: r ? Math.round(innerHeight - r.bottom) : -1,
          bg: cs ? cs.backgroundColor : "",
          fg: cs ? cs.color : ""
        };
      });
      if (btn) btn.style.transition = hadTransition;

      // Off Player Home it must be gone: the sign-in screen and a game both
      // hide the whole lobby, so this is really a check that the button never
      // escaped its parent.
      if (lob) { lob.classList.remove("visible"); lob.style.display = "none"; }
      res.goneOffHome = !vis(document.getElementById("ph-fullscreen"));

      var bar = document.getElementById("pv-fullscreen-btn");
      res.gameBtn = bar ? bar.textContent.trim() : "";
    } catch (e) { res.err = String(e && e.message); }
    document.getElementById("out").textContent = JSON.stringify(res);
  }, 100);
})();
</script>`;

  const FILE = "__fsbtn.html";
  const PAGE = HTML.replace(/<\/body>/i, `<div id="out">PENDING</div>${DRIVER}</body>`);
  fs.writeFileSync(path.join(CLIENT, FILE), PAGE);
  const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });

  const rows = {};
  try {
    for (const w of WIDTHS) {
      for (let attempt = 0; attempt < 3 && !rows[w]; attempt++) {
        let dom = "";
        try {
          dom = execFileSync(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars", `--window-size=${w},860`, "--virtual-time-budget=20000",
            "--dump-dom", `http://localhost:${PORT}/${FILE}`],
            { encoding: "utf8", maxBuffer: 64e6, timeout: 90000, stdio: ["ignore", "pipe", "ignore"] });
        } catch (_) {}
        const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
        const raw = m ? m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                            .replace(/&lt;/g, "<").replace(/&gt;/g, ">") : "";
        if (raw && raw !== "PENDING") { try { rows[w] = JSON.parse(raw); } catch (_) {} }
      }
    }
  } finally {
    try { fs.unlinkSync(path.join(CLIENT, FILE)); } catch (_) {}
    server.kill();
  }

  console.log("\nthe real page, signed in, in a real browser, at five widths");
  WIDTHS.forEach((w) => {
    const r = rows[w];
    const phone = w <= 760;
    console.log(`  ── ${w}px ${phone ? "(phone: no button)" : ""} ──`);
    if (!r || r.err) {
      fail++;
      console.log("  ✗ FAIL: Player Home never reported" + (r && r.err ? ": " + r.err : ""));
      return;
    }
    check("  Player Home is actually on screen", r.lobby && r.lobbyShown);
    check("  the button is in the page", r.exists === true);
    const seen = new Set();
    Object.entries(r.tabs).forEach(([tab, t]) => {
      if (phone) {
        check(`  ${tab}: no button on a phone`, t.vis === false);
        return;
      }
      check(`  ${tab}: the button is on screen and says Full Screen`,
            t.vis === true && t.text === "Full Screen", `${t.vis} / "${t.text}"`);
      // 16px inset, with a pixel of slack for sub-pixel rounding.
      check(`  ${tab}: it is in the bottom-right corner`,
            t.gapR >= 15 && t.gapR <= 17 && t.gapB >= 15 && t.gapB <= 17,
            `right ${t.gapR}px, bottom ${t.gapB}px`);
      check(`  ${tab}: it is painted (a real background, not transparent)`,
            /^rgba?\(/.test(t.bg) && !/rgba\([^)]*,\s*0\)$/.test(t.bg), t.bg);
      seen.add(t.bg + "|" + t.fg);
    });
    if (!phone) {
      // Not every tab has to look DIFFERENT (several share the reef palette),
      // but if all twelve came out identical then no per-tab rule is landing
      // at all and the whole point of the per-tab block is gone.
      check("  the look really does change with the tab", seen.size >= 5,
            `${seen.size} distinct looks: ` + JSON.stringify(Object.fromEntries(Object.entries(r.tabs).map(([k,v])=>[k,v.bg+" / "+v.fg]))));
    }
    check("  the game's action bar still has ⛶ Full Screen",
          r.gameBtn === "⛶ Full Screen", r.gameBtn);
    check("  and the button is gone once Player Home is", r.goneOffHome === true);
  });
}

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
