#!/usr/bin/env node
/* CREATE YOUR USERNAME: the first screen you fill in.
 *
 * It opens the instant the sign-in screen closes, so it is dressed to be the next
 * frame of that artwork rather than a form on a navy void: the sign-in painting
 * behind a pale sea-glass card, navy lettering, one warm gold button. Scenery
 * only, no critters.
 *
 * Three things here are easy to break and impossible to see in a diff:
 *
 *   1. THE BACKDROP CANNOT BE A CHILD OF THE CARD. A child with a negative
 *      z-index paints on top of its OWN parent's background, so parking the
 *      backdrop inside #auth-step-nickname blanked the card it was meant to sit
 *      behind: sharp inputs and a button floating on bare artwork. It is a
 *      sibling of the four steps, revealed by .on-nickname.
 *
 *   2. .on-nickname IS THE ONLY THING SCOPING IT. The other three steps bring
 *      artwork of their own (or none), and a second seabed under the sign-in art
 *      would be a mess, so showStep() must clear the class as well as set it.
 *
 *   3. THE GUTTER IS PADDING ON THE SCREEN, NOT vw ARITHMETIC. The card used
 *      calc(100vw - 28px), which is only the width of the screen when nothing
 *      else claims a scrollbar and the layout viewport is what 100vw says it
 *      is. Headless Chrome refuses to open a window under 500px wide, which is
 *      exactly how a phone-width bug survives a "we tested it" screenshot, so
 *      the phone widths here are measured inside same-origin iframes.
 *
 * Run:  node test_username_screen.js      (needs Google Chrome / Chromium)
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

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

// The markup of the step itself, so "is it inside the card" is a real question.
const STEP = (() => {
  const a = HTML.indexOf('<div id="auth-step-nickname"');
  const b = HTML.indexOf("<!-- Step 4: launch", a);
  return HTML.slice(a, b);
})();

// ════════════════════════════════════════════════════════════════════════
//  SOURCE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe backdrop is scenery, and it is not inside the card");
{
  check("there is a backdrop at all", /class="auth-step-bg"/.test(HTML));
  // First sign-in is ONE room: the chooser, the guest card, the account card
  // and this screen all stand in the same painting, so the player never gets a
  // room change they did not ask for.
  check("…it is the sign-in painting, the same one every step before it shows",
        /#auth-screen \.auth-step-bg::after \{[\s\S]{0,1400}?auth-ocean\.jpg/.test(CSS));
  check("…covering the window as the cards cover it, over its blurred copy",
        /#auth-screen \.auth-step-bg::after[\s\S]{0,1400}?auth-ocean\.jpg[^;]*center \/ cover/.test(CSS)
        && /#auth-screen \.auth-step-bg::before \{[\s\S]{0,300}?auth-ocean\.jpg[^;]*center \/ cover/.test(CSS));
  // The screen behind carries a live sign-in form. Behind a live card that is a
  // second screen asking to be misread.
  check("…with the screen behind it sunk under a scrim",
        /#auth-screen \.auth-step-bg::after[\s\S]{0,1400}?radial-gradient\(ellipse 49% 66% at 50% 50%/.test(CSS));
  check("the kelp forest is gone: it was a second painting for one step",
        !/url\("\/backgrounds\/kelp-forest\.png"\)/.test(CSS));
  check("…and it is NOT a child of the card it sits behind",
        !/auth-step-bg/.test(STEP),
        "a negative-z child paints over its own parent's background");
  check("…so it is styled as a child of the screen instead",
        /#auth-screen\s+\.auth-step-bg\s*\{/.test(CSS));
  check("no critters swim in it, it is one still image",
        !/auth-step-bg[\s\S]{0,900}?animation:/.test(CSS));
}

console.log("\n.on-nickname is the only thing that shows it");
{
  check("showStep sets it for this step",
        /classList\.toggle\("on-nickname", stepId === "auth-step-nickname"\)/.test(APP));
  check("…which also CLEARS it for the other three",
        /classList\.toggle\("on-nickname"/.test(APP) && !/classList\.add\("on-nickname"\)/.test(APP));
  check("…and the CSS keeps it hidden until then",
        /#auth-screen\s+\.auth-step-bg\s*\{[^}]*display:\s*none/.test(CSS)
        && /#auth-screen\.on-nickname\s+\.auth-step-bg\s*\{[^}]*display:\s*block/.test(CSS));
}

console.log("\nthe card is dressed like the artwork before it, not like the game's dark panels");
{
  check("the title is set in the artwork's rounded face, not the serif",
        /#auth-step-nickname \.auth-title\s*\{[^}]*"Baloo 2"/.test(CSS));
  check("the gold button is the light butter gold with dark ink",
        /#auth-step-nickname \.pv-btn\.gold\s*\{[^}]*color:\s*#4a3208/.test(CSS));
  check("every rule is scoped to this step, so the other auth boxes are untouched",
        (CSS.match(/^\s*(#auth-step-nickname|#auth-screen\.on-nickname|#auth-screen \.auth-step-bg)/gm) || []).length > 12);
  check("…including the friend-code box, which must beat css/level-pass.css",
        /#auth-step-nickname \.auth-ref-box\s*\{/.test(CSS),
        "level-pass.css loads later, so a bare .auth-ref-box selector would lose");
}

console.log("\nthe gutter is padding on the screen, not vw arithmetic on the card");
{
  check("no 100vw width maths on the card",
        !/#auth-step-nickname[^}]*100vw/.test(CSS));
  check("…the screen carries the gutter instead",
        /#auth-screen\.on-nickname\s*\{[^}]*padding:/.test(CSS));
  check("a short window scrolls the card instead of clipping the button off it",
        /#auth-step-nickname\.auth-box\s*\{[\s\S]*?overflow-y:\s*auto/.test(CSS));
}

console.log("\nthe copy calls the thing what the screen calls it");
{
  check("the validator takes the noun rather than hard-coding one",
        /function validateNick\(nick, noun\)/.test(APP));
  check("…this screen asks it for \"Username\"",
        /validateNick\(nick, "Username"\)/.test(APP));
  check("…so does Settings, which also says Username",
        /validateNick\(newNick, "Username"\)/.test(APP));
  check("…and the guest screen, which says Nickname, still gets Nickname",
        /const err = validateNick\(nick\);/.test(APP));
  check("nothing on this screen still says \"nickname\" to the player",
        !/auth-nick-err", "[^"]*nickname/i.test(APP));
}

console.log("\nthe character counter tells you what maxlength is enforcing");
{
  check("the field is capped", /id="auth-nick-input"[^>]*maxlength="15"/.test(HTML));
  check("…and a counter reads that cap out", /id="auth-nick-count"/.test(HTML));
  check("…from the RAW length, which is what maxlength counts",
        /function paintNickCount[\s\S]{0,260}?\(inp\.value \|\| ""\)\.length/.test(APP));
  check("…repainted when the step pre-fills the field behind its back",
        /nickInput\.value = hint;\s*\n\s*paintNickCount\(\);/.test(APP));
}

console.log("\nthe cache busters moved, or nobody gets any of this");
{
  // Pinned to a LITERAL date this used to fail on every ship that bumped the
  // busters, which taught people to edit the test instead of reading it. The
  // real contract is not which date it is, it is that /css and /js all move
  // TOGETHER: one file left behind is one file served from a day-old cache.
  const refs = HTML.match(/\/(?:css|js)\/[A-Za-z0-9._-]+\?v=[0-9A-Za-z.-]+/g) || [];
  const stamps = new Set(refs.map((r) => r.split("?v=")[1]));
  check("every /css and /js file carries a cache buster", refs.length >= 20, String(refs.length));
  check("…and they all share ONE stamp, so nothing is left on a stale copy",
        stamps.size === 1, [...stamps].join(" "));
  const stamp = [...stamps][0] || "";
  check("preview.css and preview-app.js are both carrying it",
        HTML.includes(`css/preview.css?v=${stamp}`) && HTML.includes(`js/preview-app.js?v=${stamp}`),
        stamp);
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
  const PORT = 9640 + (process.pid % 300);
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

  // Firebase resolves over the network and there is no real account here, so
  // the step is opened the way showStep() opens it and then left alone.
  const DRIVER = `
<script>
(function () {
  var tick = 0;
  var iv = setInterval(function () {
    if (++tick > 500) { clearInterval(iv); return; }
    try {
      var spl = document.getElementById("cc-fs-splash"); if (spl) spl.style.display = "none";
      var ls = document.getElementById("auth-loading-screen");
      if (ls) { ls.classList.add("hidden"); ls.style.display = "none"; }
      var as = document.getElementById("auth-screen");
      if (as) { as.classList.remove("hidden"); as.classList.add("on-nickname"); }
      ["auth-step-choose","auth-step-guest","auth-step-launch"].forEach(function (id) {
        var e = document.getElementById(id); if (e) e.style.display = "none";
      });
      var n = document.getElementById("auth-step-nickname"); if (n) n.style.display = "";
      var i = document.getElementById("auth-nick-input");
      if (i && !i.value) { i.value = "Loggerhead"; i.dispatchEvent(new Event("input")); }
      if (tick > 30) { clearInterval(iv); window.__ccReady = 1; }
    } catch (e) {}
  }, 40);
})();
</script>`;

  // Every width in one page, each in its own iframe, because Chrome will not
  // open a window narrower than 500px and a phone-width bug hides in that gap.
  const SIZES = [[320, 700], [390, 844], [430, 932], [768, 900], [1440, 900]];
  const WRAPPER = (page) => `<!doctype html><meta charset="utf-8">
<style>body{margin:0;display:flex}iframe{border:0}</style>
${SIZES.map(([w, h], i) => `<iframe id="f${i}" width="${w}" height="${h}" src="/${page}?game_window=1"></iframe>`).join("")}
<div id="out">PENDING</div>
<script>
setTimeout(function () {
  var sizes = ${JSON.stringify(SIZES)}, res = [];
  sizes.forEach(function (sz, i) {
    try {
      var fr = document.getElementById("f" + i), d = fr.contentDocument, w = fr.contentWindow;
      var card = d.getElementById("auth-step-nickname"), btn = d.getElementById("auth-nick-go-btn");
      var cb = card.getBoundingClientRect(), bb = btn.getBoundingClientRect();
      var back = d.querySelector("#auth-screen .auth-step-bg");
      var cs = w.getComputedStyle(card);
      res.push({
        w: sz[0], vw: w.innerWidth,
        cardL: Math.round(cb.left), cardR: Math.round(cb.right), cardW: Math.round(cb.width),
        btnW: Math.round(bb.width),
        // A see-through card means the backdrop is painting over it again.
        cardOpaque: cs.backgroundImage !== "none" || cs.backgroundColor !== "rgba(0, 0, 0, 0)",
        // The backdrop must be BEHIND the card and still cover the screen.
        kelpBehind: w.getComputedStyle(back).display === "block" &&
                    Math.round(back.getBoundingClientRect().width) >= w.innerWidth,
        // Nothing on this screen may push the page sideways.
        sideways: d.documentElement.scrollWidth > w.innerWidth + 1,
        // The button has to be reachable, scrolling the card if need be.
        buttonReachable: btn.getBoundingClientRect().height > 20 &&
                         card.scrollHeight >= btn.offsetTop + btn.offsetHeight,
        count: (d.getElementById("auth-nick-count") || {}).textContent
      });
    } catch (e) { res.push({ w: sz[0], err: String(e && e.message) }); }
  });
  document.getElementById("out").textContent = JSON.stringify(res);
}, 7000);
</script>`;

  const page = "__uname_step.html", wrap = "__uname_wrap.html";
  fs.writeFileSync(path.join(CLIENT, page), HTML + DRIVER);
  fs.writeFileSync(path.join(CLIENT, wrap), WRAPPER(page));
  const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });

  let rows = null;
  try {
    for (let attempt = 0; attempt < 3 && !rows; attempt++) {
      const dom = execFileSync(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", "--window-size=3400,1000", "--virtual-time-budget=45000",
        "--dump-dom", `http://localhost:${PORT}/${wrap}`],
        { encoding: "utf8", maxBuffer: 64e6, stdio: ["ignore", "pipe", "ignore"] });
      const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
      const raw = m ? m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                          .replace(/&lt;/g, "<").replace(/&gt;/g, ">") : "";
      if (raw && raw !== "PENDING") { try { rows = JSON.parse(raw); } catch (_) {} }
    }
  } finally {
    [page, wrap].forEach((f) => { try { fs.unlinkSync(path.join(CLIENT, f)); } catch (_) {} });
    server.kill();
  }

  console.log("\nmeasured in a real browser, at five widths");
  if (!rows) {
    console.log("  ✗ FAIL: the harness never reported"); fail++;
  } else {
    rows.forEach((r) => {
      const at = r.w + "px:";
      check(at + " the step rendered", !r.err, r.err);
      if (r.err) return;
      check(at + " the card paints its own surface (the backdrop is not over it)", r.cardOpaque);
      check(at + " the backdrop is behind it and covers the screen", r.kelpBehind);
      check(at + " the card fits, centred, with a gutter on both sides",
            r.cardL >= 8 && r.vw - r.cardR >= 8 && Math.abs(r.cardL - (r.vw - r.cardR)) <= 2,
            `l=${r.cardL} r=${r.vw - r.cardR} vw=${r.vw}`);
      check(at + " nothing pushes the page sideways", !r.sideways);
      check(at + " Create Username is reachable", r.buttonReachable);
      check(at + " the counter followed the typing", r.count === "10 / 15", r.count);
    });
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
