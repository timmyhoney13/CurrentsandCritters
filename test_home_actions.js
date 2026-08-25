#!/usr/bin/env node
/* Player Home: the four action cards, and the Friends tab's reef.
 *
 * 1. THE CARDS ARE THEIR ARTWORK. Quick Match, Create Game, Join Game and
 *    Tutorial are each ONE baked PNG, with the coral creature and the
 *    lettering painted into it. That art is the row players know, so this
 *    guards it: the markup points at the four files, the files are on disk,
 *    and, in a real browser, every one of them actually DECODES and covers its
 *    card. A missing PNG is otherwise silent, the card just goes flat.
 *
 *    (They were briefly replaced by a gradient and a text label. They are the
 *    artwork again. If you are about to swap them for markup, that is the
 *    change this file is here to make you do on purpose.)
 *
 *    The image sits on top of the card's own gradient and takes no clicks
 *    (pointer-events: none), so the button underneath keeps working. The ids
 *    every handler binds to are checked too, they are the contract.
 *
 * 2. FRIENDS STANDS ON THE REEF. Not the round avatar background of the same
 *    reef (a circle on a pale square, all open water in the middle when it is
 *    blown up to fill a page), and it is attached FIXED: with the default
 *    scroll attachment the art is stretched over the panel's whole scroll
 *    height, so only its top strip is ever on screen.
 *
 * Run:  node test_home_actions.js      (needs Google Chrome / Chromium)
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

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

const ROW = (() => {
  const a = HTML.indexOf('<div class="ph-actions">');
  return a < 0 ? "" : HTML.slice(a, HTML.indexOf("</div>", HTML.indexOf("stats-tutorial-btn")) + 60);
})();

// ════════════════════════════════════════════════════════════════════════
//  SOURCE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe four cards are their artwork");
{
  const WANT = [
    ["stats-quickmatch-btn",  "action-card-quickmatch.png", "Quick Match"],
    ["stats-create-btn",      "action-card-create.png",     "Create Game"],
    ["stats-join-toggle-btn", "action-card-join.png",       "Join Game"],
    ["stats-tutorial-btn",    "action-card-tutorial.png",   "Tutorial"],
  ];
  check("four image cards in the row",
        (ROW.match(/class="ph-action-card ph-action-img-card"/g) || []).length === 4,
        String((ROW.match(/ph-action-img-card/g) || []).length));
  WANT.forEach(([id, png, label]) => {
    check(`${label} is its own artwork`,
          new RegExp(`id="${id}"[\\s\\S]{0,200}?src="/${png.replace(".", "\\.")}`).test(ROW),
          png);
    // A card whose PNG is not in the build is a card that silently goes flat.
    check(`  …and ${png} is really in the client`,
          fs.existsSync(path.join(CLIENT, png)));
  });
  check("the ids handlers bind to are untouched",
        WANT.every(([id]) => new RegExp(`id="${id}"`).test(ROW)));
  check("the artwork covers the card and is not cropped",
        /\.ph-action-img-card \{[\s\S]*?aspect-ratio: 472 \/ 304/.test(CSS)
        && /\.ph-action-img \{[\s\S]*?object-fit: cover/.test(CSS));
  check("…and takes no clicks, so the button underneath still works",
        /\.ph-action-img \{[\s\S]*?pointer-events: none/.test(CSS));
  check("the scrim is off on an image card (the lettering is in the art)",
        /\.ph-action-img-card::before \{ display: none/.test(CSS));
}

console.log("\nthe Friends tab stands on the game's own Coral Reef");
{
  const block = (() => {
    const a = CSS.indexOf('#auth-stats-lobby[data-bg-tab="friends"] {');
    return a < 0 ? "" : CSS.slice(a, a + 400);
  })();
  check("it is the reef board art", /ph-bg-game-art\.png/.test(block));
  check("…not the round avatar background of the same reef",
        !/backgrounds\/bg-coral-reef\.png/.test(CSS),
        "that art is a circle on a pale square, whose rim creeps in at the corners");
  check("…and not the old stock underwater scene",
        !/ph-bg-friends-art\.png/.test(CSS));
  check("it is fixed, so it fits the window instead of the scroll height",
        /no-repeat fixed/.test(block));
  check("nothing else on that tab paints over it",
        /\[data-bg-tab="friends"\]::before,\s*\n\s*#auth-stats-lobby\[data-bg-tab="friends"\]::after \{\s*\n\s*content: none/.test(CSS));
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
  const PORT = 9680 + (process.pid % 300);
  const SERVER_SRC = `
    const fs=require("fs"),path=require("path"),http=require("http");
    const ROOT=${JSON.stringify(ROOT)};
    const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",
      ".json":"application/json",".png":"image/png",".jpg":"image/jpeg",
      ".webp":"image/webp",".svg":"image/svg+xml",".ico":"image/x-icon"};
    http.createServer((req,res)=>{
      const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
      // Two roots, because the shipped page addresses assets BOTH ways: the
      // tab art as /multiplayer/client/..., and the card art as /action-card-
      // ....png, which the real server resolves against multiplayer/client.
      // Serving only the repo root 404s every card PNG and the cards go flat,
      // which is exactly the failure this suite is supposed to catch for real.
      let f=path.join(ROOT,rel);
      if(!fs.existsSync(f)) f=path.join(ROOT,"multiplayer/client",rel);
      if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);res.end();return;}
      res.writeHead(200,{"Content-Type":MIME[path.extname(f)]||"application/octet-stream"});
      fs.createReadStream(f).pipe(res);
    }).listen(${PORT});
  `;

  // Player Home on its own: the lobby markup, the game's stylesheet, no app.
  // Served from the repo root, because the tab art is addressed as
  // /multiplayer/client/... exactly as the shipped page addresses it.
  const LOBBY = (() => {
    const a = HTML.indexOf('<div id="auth-stats-lobby"');
    const b = HTML.indexOf("<!-- ══", a + 10);
    return HTML.slice(a, b);
  })();
  const PAGE = (tab) => `<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="/multiplayer/client/css/preview.css">
<body>
${LOBBY.replace('<div id="auth-stats-lobby" data-bg-tab="overview">',
                `<div id="auth-stats-lobby" class="visible" data-bg-tab="${tab}">`)}
<div id="out">PENDING</div>
<script>
setTimeout(function () {
  var res = { tab: ${JSON.stringify(tab)} };
  try {
    var cards = [].slice.call(document.querySelectorAll(".ph-action-card"));
    res.cards = cards.map(function (c) {
      var img = c.querySelector("img.ph-action-img");
      var cb = c.getBoundingClientRect();
      var ib = img ? img.getBoundingClientRect() : null;
      var cs = img ? getComputedStyle(img) : null;
      return {
        hasImg: !!img,
        src: img ? (img.getAttribute("src") || "") : "",
        alt: img ? (img.alt || "") : "",
        // naturalWidth is the only thing that separates "the art is there"
        // from "the file 404'd and the card went flat".
        loaded: !!(img && img.complete && img.naturalWidth > 0),
        clickThrough: cs ? cs.pointerEvents === "none" : false,
        covers: !!(ib && cb && ib.width >= cb.width - 1 && ib.height >= cb.height - 1),
        w: Math.round(cb.width), h: Math.round(cb.height)
      };
    });
    var lob = document.getElementById("auth-stats-lobby");
    var bs = getComputedStyle(lob);
    res.bgImage = bs.backgroundImage;
    res.bgAttach = bs.backgroundAttachment;
  } catch (e) { res.err = String(e && e.message); }
  document.getElementById("out").textContent = JSON.stringify(res);
}, 2500);
</script>`;

  const pages = { overview: "__home_ov.html", friends: "__home_fr.html" };
  Object.entries(pages).forEach(([tab, f]) => fs.writeFileSync(path.join(ROOT, f), PAGE(tab)));
  const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });

  const rows = {};
  try {
    for (const [tab, f] of Object.entries(pages)) {
      for (let attempt = 0; attempt < 3 && !rows[tab]; attempt++) {
        const dom = execFileSync(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
          "--hide-scrollbars", "--window-size=1440,900", "--virtual-time-budget=20000",
          "--dump-dom", `http://localhost:${PORT}/${f}`],
          { encoding: "utf8", maxBuffer: 64e6, stdio: ["ignore", "pipe", "ignore"] });
        const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
        const raw = m ? m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                            .replace(/&lt;/g, "<").replace(/&gt;/g, ">") : "";
        if (raw && raw !== "PENDING") { try { rows[tab] = JSON.parse(raw); } catch (_) {} }
      }
    }
  } finally {
    Object.values(pages).forEach((f) => { try { fs.unlinkSync(path.join(ROOT, f)); } catch (_) {} });
    server.kill();
  }

  console.log("\nmeasured in a real browser");
  const ov = rows.overview;
  if (!ov || ov.err) { console.log("  ✗ FAIL: Player Home never reported" + (ov && ov.err ? ": " + ov.err : "")); fail++; }
  else {
    check("four action cards", ov.cards.length === 4, String(ov.cards.length));
    const want = ["Quick Match", "Create Game", "Join Game", "Tutorial"];
    ov.cards.forEach((c, i) => {
      check(`card ${i + 1} is the ${want[i]} artwork`,
            c.hasImg && new RegExp(want[i].split(" ")[0], "i").test(c.src), c.src);
      check(`  …and the image really decoded`, c.loaded,
            c.loaded ? "" : "the PNG did not load: the card is blank");
      check(`  …it covers the whole card`, c.covers, `${c.w}x${c.h}`);
      check(`  …clicks pass through to the button`, c.clickThrough);
      check(`  …at a real size`, c.w > 150 && c.h > 90, `${c.w}x${c.h}`);
    });
  }
  const fr = rows.friends;
  if (!fr || fr.err) { console.log("  ✗ FAIL: the Friends tab never reported"); fail++; }
  else {
    check("Friends paints the reef board art",
          /ph-bg-game-art/.test(fr.bgImage || ""), fr.bgImage);
    check("…fixed to the window, not stretched down the scroll",
          fr.bgAttach === "fixed", fr.bgAttach);
    check("…and its cards still wear their artwork", (fr.cards || []).every((c) => c.hasImg && c.loaded));
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
