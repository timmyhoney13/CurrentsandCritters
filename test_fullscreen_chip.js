#!/usr/bin/env node
/* NO FULL-SCREEN BUTTON ON THE MENU.
 *
 * Run:  node test_fullscreen_chip.js      (needs Google Chrome / Chromium)
 *
 * The menu used to carry a ⛶ "Full screen" chip (#cc-fs-resume) pinned to the
 * bottom-right corner of every tab once the player had signed in. It has been
 * taken out. Inside a game nothing changes: the action bar keeps its own
 * "⛶ Full Screen" button (#pv-fullscreen-btn).
 *
 * The contract:
 *   1. THE CHIP IS GONE FROM THE SOURCE: no markup, no styling, no code, and no
 *      Main Menu tutorial step pointing at it.
 *   2. NOTHING ON THE SIGNED-IN MENU SAYS FULL SCREEN. The REAL preview.html is
 *      loaded in a real browser, put into the signed-in menu state, and every
 *      visible element on every tab is read (text, title, aria-label) at five
 *      widths. Only the game screen and the launch splash are exempt.
 *   3. THE GAME KEEPS ITS OWN BUTTON in the action bar.
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

// ════════════════════════════════════════════════════════════════════════
//  SOURCE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe menu's full-screen chip is gone from the source");
check("no chip in the page", !/cc-fs-resume/.test(HTML));
check("no chip styling", !/cc-fs-resume|ccfs-word|ccfs-glyph|ccfs-peek/.test(CSS));
check("no chip code", !/cc-fs-resume|syncFsChip/.test(APP));
check("no tutorial step for it", !/cc-fs-resume|fsChipPeek/.test(TUT));
check("the game's action bar keeps its own Full Screen button",
      /id="pv-fullscreen-btn"[^>]*>⛶ Full Screen</.test(HTML)
      && /getElementById\("pv-fullscreen-btn"\)/.test(APP));

// ════════════════════════════════════════════════════════════════════════
//  DRIVE  (the real page, signed-in menu, every tab, five widths)
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

  const TABS = ["overview", "friends", "messages", "leaderboard", "store", "clans"];
  const WIDTHS = [390, 768, 1024, 1280, 1600];

  // Firebase never resolves here, so the menu is put on screen the way
  // revealLobby() leaves it: signed-in class on, sign-in and loading screens
  // away, the lobby visible. Then every visible element on every tab is read.
  const DRIVER = `
<script>
(function () {
  var FS = /full[\\s-]?screen|\\u26F6/i;
  function visible(el) {
    var cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden" || +cs.opacity === 0) return false;
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0
        && r.left < innerWidth && r.top < innerHeight;
  }
  function exempt(el) {
    return !!el.closest("#pv-game, #cc-fs-splash, #out, script, style, noscript");
  }
  function scan() {
    var hits = [];
    [].forEach.call(document.body.querySelectorAll("*"), function (el) {
      if (exempt(el) || !visible(el)) return;
      var own = [].map.call(el.childNodes, function (n) { return n.nodeType === 3 ? n.textContent : ""; }).join("");
      var said = [own, el.getAttribute("title") || "", el.getAttribute("aria-label") || ""].join(" | ");
      if (FS.test(said)) hits.push((el.id ? "#" + el.id : el.tagName.toLowerCase() + "." + el.className) + " : " + said.trim().slice(0, 80));
    });
    return hits;
  }
  var tick = 0;
  var iv = setInterval(function () {
    if (++tick < 40) return;
    clearInterval(iv);
    var res = { tabs: {} };
    try {
      document.body.classList.add("cc-signed-in");
      var spl = document.getElementById("cc-fs-splash"); if (spl) spl.classList.remove("show");
      var ls = document.getElementById("auth-loading-screen");
      if (ls) { ls.classList.add("hidden"); ls.style.display = "none"; }
      var as = document.getElementById("auth-screen");
      if (as) { as.classList.add("hidden"); as.style.display = "none"; }
      var lob = document.getElementById("auth-stats-lobby");
      res.lobby = !!lob;
      if (lob) { lob.classList.add("visible"); lob.style.display = ""; }
      var game = document.getElementById("pv-game"); if (game) game.style.display = "none";
      res.chipExists = !!document.getElementById("cc-fs-resume");
      ${JSON.stringify(TABS)}.forEach(function (tab) {
        if (lob) lob.setAttribute("data-bg-tab", tab);
        [].forEach.call(document.querySelectorAll(".ph-panel"), function (p) {
          p.style.display = (p.id === "ph-panel-" + tab) ? "" : "none";
        });
        res.tabs[tab] = scan();
      });
      res.lobbyShown = !!(lob && visible(lob));
      var bar = document.getElementById("pv-fullscreen-btn");
      res.gameBtn = bar ? bar.textContent.trim() : "";
    } catch (e) { res.err = String(e && e.message); }
    document.getElementById("out").textContent = JSON.stringify(res);
  }, 100);
})();
</script>`;

  const FILE = "__nofschip.html";
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
            "--dump-dom", `http://localhost:${PORT}/${FILE}?game_window=1`],
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
    console.log(`  ── ${w}px ──`);
    if (!r || r.err) {
      fail++;
      console.log("  ✗ FAIL: the menu never reported" + (r && r.err ? ": " + r.err : ""));
      return;
    }
    check("  the signed-in menu is actually on screen", r.lobby && r.lobbyShown);
    check("  there is no #cc-fs-resume element at all", r.chipExists === false);
    Object.entries(r.tabs).forEach(([tab, hits]) =>
      check(`  ${tab}: nothing on the menu offers full screen`, hits.length === 0, hits.join(" ; ")));
    check("  the game's action bar still has ⛶ Full Screen", r.gameBtn === "⛶ Full Screen", r.gameBtn);
  });
}

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail ? 1 : 0);
