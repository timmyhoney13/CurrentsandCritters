#!/usr/bin/env node
/* Player Home: the four action cards, and the Friends tab's reef.
 *
 * 1. THE CARDS ARE A COLOUR AND A NAME. Each one used to be a single baked PNG
 *    with a cartoon coral creature and the lettering painted into it. The art
 *    is gone; the colours it was painted in are now the card's own gradient and
 *    the name is real text.
 *
 *    The trap that made this dangerous: a SECOND .ph-action-label rule, ~2600
 *    lines further down the file and left over from an older light-card
 *    design, paints the label navy. While the cards were images nothing showed
 *    it. The moment they became text, every name went dark-on-dark and was
 *    unreadable. So this test measures the rendered colour, it does not read
 *    the stylesheet and hope.
 *
 *    The other trap is order: the gradients are nth-child, and the markup order
 *    (Quick Match, Create, Join, Tutorial) is what maps a card to its colour.
 *    Reorder the markup and every card changes identity.
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
console.log("\nthe cards are a colour and a name, and nothing else");
{
  check("no baked card images left in the markup",
        !/ph-action-img/.test(HTML) && !/action-card-\w+\.png/.test(HTML));
  check("…nor rules for them in the stylesheet",
        !/ph-action-img/.test(CSS));
  check("every card is a name and a subtitle",
        (ROW.match(/class="ph-action-label"/g) || []).length === 4
        && (ROW.match(/class="ph-action-sub"/g) || []).length === 4);
  check("…and the names are the four the buttons still answer to",
        />Quick Match</.test(ROW) && />Create Game</.test(ROW)
        && />Join Game</.test(ROW) && />Tutorial</.test(ROW));
  check("the ids handlers bind to are untouched",
        /id="stats-quickmatch-btn"/.test(ROW) && /id="stats-create-btn"/.test(ROW)
        && /id="stats-join-toggle-btn"/.test(ROW) && /id="stats-tutorial-btn"/.test(ROW));
  check("the colours still follow the markup order: blue, purple, red, green",
        /nth-child\(1\) \{ background: linear-gradient\(145deg, #6fd6f2/.test(CSS)
        && /nth-child\(2\) \{ background: linear-gradient\(145deg, #cb9af2/.test(CSS)
        && /nth-child\(3\) \{ background: linear-gradient\(145deg, #ff8b78/.test(CSS)
        && /nth-child\(4\) \{ background: linear-gradient\(145deg, #b2d965/.test(CSS));
  check("…in the per-tab override too, or a card changes colour per tab",
        /\[data-bg-tab\] \.ph-action-card:nth-child\(2\) \{ background: linear-gradient\(145deg, #cb9af2/.test(CSS)
        && /\[data-bg-tab\] \.ph-action-card:nth-child\(3\) \{ background: linear-gradient\(145deg, #ff8b78/.test(CSS));
  check("there is only ONE rule that colours the label",
        (CSS.match(/^\s*\.ph-action-label \{/gm) || []).length === 1,
        "a second, later one repaints it navy and the name disappears");
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
      const f=path.join(ROOT,rel);
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
      var lbl = c.querySelector(".ph-action-label");
      var cs = getComputedStyle(lbl), cc = getComputedStyle(c);
      var cb = c.getBoundingClientRect(), lb = lbl.getBoundingClientRect();
      // "Is the name light on a dark card" as a number, not as a stylesheet read.
      var lum = function (s) {
        var m = /rgba?\\((\\d+), ?(\\d+), ?(\\d+)/.exec(s) || [0,0,0,0];
        return (0.2126*+m[1] + 0.7152*+m[2] + 0.0722*+m[3]) / 255;
      };
      return {
        name: lbl.textContent.trim(),
        ink: cs.color, inkLum: +lum(cs.color).toFixed(3),
        painted: cc.backgroundImage.indexOf("gradient") >= 0,
        hasImg: !!c.querySelector("img"),
        w: Math.round(cb.width), h: Math.round(cb.height),
        inside: lb.left >= cb.left - 1 && lb.right <= cb.right + 1
                && lb.bottom <= cb.bottom + 1
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
      check(`card ${i + 1} is ${want[i]}`, c.name === want[i], c.name);
      check(`  …painted, with no image on it`, c.painted && !c.hasImg);
      check(`  …its name is light ink, readable on the gradient`, c.inkLum > .85, c.ink);
      check(`  …and the name sits inside the card`, c.inside);
      check(`  …at a real size`, c.w > 150 && c.h > 120, `${c.w}x${c.h}`);
    });
  }
  const fr = rows.friends;
  if (!fr || fr.err) { console.log("  ✗ FAIL: the Friends tab never reported"); fail++; }
  else {
    check("Friends paints the reef board art",
          /ph-bg-game-art/.test(fr.bgImage || ""), fr.bgImage);
    check("…fixed to the window, not stretched down the scroll",
          fr.bgAttach === "fixed", fr.bgAttach);
    check("…and its cards keep their own colours", (fr.cards || []).every((c) => c.painted));
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
