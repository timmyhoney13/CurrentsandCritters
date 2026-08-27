#!/usr/bin/env node
/* CREATE YOUR USERNAME: the first thing a new account fills in.
 *
 * It used to be a SCREEN of its own. Signing in with Google threw the sign-in
 * painting away, built a second room out of a blurred copy of the same sea, and
 * stood a pale sea-glass card in the middle of it to ask one question. Three of
 * the four things that ever went wrong here went wrong because of that room:
 * the backdrop was a sibling of four steps that a negative-z child would have
 * painted over, one class was the only thing scoping it, and the gutter had to
 * be padding on the screen because the card was sized in vw.
 *
 * All of that is gone. It is #ao-pane-nickname: the fifth pane of the sign-in
 * column, in the room the player was already standing in. The ocean does not
 * move between "sign in" and "pick your name", and there is no second painting
 * to keep in step with the first, because there is only one painting.
 *
 * What survived the move, and is what this file pins now:
 *
 *   1. showStep("auth-step-nickname") STILL MEANS WHAT IT MEANT. Half a dozen
 *      places in the app say it; exactly one place knows it now turns a pane
 *      over instead of swapping a screen.
 *   2. THE IDS ARE UNCHANGED. referral.js writes #auth-ref-coins, the rename
 *      price is written into #auth-nick-price, and the server test reads both
 *      straight out of this HTML.
 *   3. IT HOLDS TOGETHER FROM A 320px PHONE UP. Headless Chrome refuses to open
 *      a window under 500px wide, which is exactly how a phone-width bug
 *      survives a "we tested it" screenshot, so the phone widths here are
 *      measured inside same-origin iframes.
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

// The pane's own markup, so "what is on it" is a real question.
const PANE = (() => {
  const a = HTML.indexOf('<div class="ao-pane" id="ao-pane-nickname"');
  const b = HTML.indexOf('id="auth-choose-err"', a);
  return a < 0 ? "" : HTML.slice(a, b < 0 ? a + 5000 : b);
})();

// ════════════════════════════════════════════════════════════════════════
//  SOURCE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe second room is gone, and so is everything that held it up");
{
  check("there is no screen of its own left", !/id="auth-step-nickname"/.test(HTML));
  check("…nor a backdrop element that existed only to sit behind it",
        !/class="auth-step-bg"/.test(HTML) && !/auth-step-bg/.test(CSS));
  check("…nor the one class that used to reveal it", !/on-nickname/.test(CSS + APP));
  check("…and no second copy of the painting to keep in step with the first",
        (CSS.match(/auth-ocean\.jpg/g) || []).length <= 4,
        "one for the art, one for its blurred bed, one for the seam: not a room per step");
  // What replaced it.
  check("it is a pane of the sign-in column",
        /<div class="ao-form-inner">[\s\S]*?<div class="ao-pane" id="ao-pane-nickname"/.test(HTML));
  check("…shown by the one function that shows any of them",
        /nickname: "ao-pane-nickname",/.test(APP)
        && /function ccChooserPane\(which, opts\)/.test(APP));
}

console.log("\nshowStep still means what it always meant");
{
  // Half a dozen places in the app say showStep("auth-step-nickname"). Exactly
  // one place knows that it turns a pane over now rather than swapping a screen.
  check("the two dead step names map to the panes that replaced them",
        /const STEP_AS_PANE = \{ "auth-step-nickname": "nickname", "auth-step-guest": "guest" \};/.test(APP));
  check("…and the app still asks for them by name",
        /showStep\("auth-step-nickname"\);/.test(APP));
  check("…so the real step shown is the chooser, with that pane up",
        /const realStep = asPane \? "auth-step-choose" : stepId;/.test(APP)
        && /ccChooserPane\(asPane \|\| "signin", \{ quiet: !asPane \}\)/.test(APP));
  // Arriving at "pick your name" straight off a Google sign-in is a step
  // FORWARD through one screen, so it animates. Landing back on the chooser
  // from anywhere else is a screen arriving, so it does not.
  check("…and a named pane arrives with the movement, an unnamed one without it",
        /quiet: !asPane/.test(APP));
}

console.log("\nit is dressed like every other pane, because it is one");
{
  check("the same heading, in the column's voice",
        /<div class="ao-h">Create Your Username<\/div>/.test(PANE));
  check("the same labelled field with a counter beside the label",
        /class="ao-lbl-row"/.test(PANE) && /id="auth-nick-count"/.test(PANE));
  check("the same gold button first sign-in is finished with",
        /id="auth-nick-go-btn" class="pv-btn gold full ao-gold"/.test(PANE));
  check("the same status line, dressed by the same rule",
        /<div class="auth-err auth-note" id="auth-nick-err"/.test(PANE)
        && /#auth-step-choose \.ao-pane > \.auth-err \{/.test(CSS));
  check("…and it borrows the create pane's tighter rhythm, being the other tall one",
        /#auth-step-choose #ao-pane-nickname \.ao-field \{ height: clamp\(44px/.test(CSS));
  // There is deliberately no way back off this pane: by the time it is up the
  // account exists and it needs a name.
  check("there is no Back on it, because there is nowhere to go",
        !/ao-back/.test(PANE));
  check("…and Escape does not offer one either",
        !/AO_ESCAPES_TO = \{[^}]*nickname:/.test(APP));
}

console.log("\nthe two things other files read out of this pane are still here");
{
  // referral.js writes the coin figure into it, the app writes the rename price
  // into it, and test_referral_server.py reads the first straight out of this
  // HTML. Renaming either id breaks something that never mentions this screen.
  check("the rename price still has its slot", /id="auth-nick-price"/.test(PANE));
  check("…quoted from the ONE constant Settings charges",
        /priceEl\.textContent = String\(PHST_RENAME_COIN_PRICE\)/.test(APP));
  check("the referral coin figure still has its slot", /id="auth-ref-coins"/.test(PANE));
  check("…and the friend-code field is still optional and still here",
        /id="auth-ref-input"/.test(PANE) && /class="ao-optional">Optional/.test(PANE));
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
  // the pane is opened the way the app opens it: through showStep, by the name
  // the rest of the app still calls this screen.
  const DRIVER = `
<script>
(function () {
  var st = document.createElement("style");
  st.textContent = "*,*::before,*::after{transition:none!important;animation:none!important}";
  document.head.appendChild(st);
  var tick = 0;
  var iv = setInterval(function () {
    if (++tick > 500) { clearInterval(iv); return; }
    try {
      var spl = document.getElementById("cc-fs-splash"); if (spl) spl.style.display = "none";
      var ls = document.getElementById("auth-loading-screen");
      if (ls) { ls.classList.add("hidden"); ls.style.display = "none"; }
      var as = document.getElementById("auth-screen"); if (as) as.classList.remove("hidden");
      var lz = document.getElementById("auth-step-launch"); if (lz) lz.style.display = "none";
      var step = document.getElementById("auth-step-choose");
      if (!step) return;
      step.style.display = ""; step.classList.add("is-armed");
      if (tick < 30 || !window.__ccChooserPane) return;
      clearInterval(iv);
      var art = document.querySelector(".ao-art-img").getBoundingClientRect();
      window.__ccArtBefore = [art.left, art.top, art.width, art.height];
      window.__ccChooserPane("nickname");
      setTimeout(function () {
        var i = document.getElementById("auth-nick-input");
        if (i) { i.value = "Loggerhead"; i.dispatchEvent(new Event("input")); }
        window.__ccReady = 1;
      }, 500);
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
      var pane = d.getElementById("ao-pane-nickname"), btn = d.getElementById("auth-nick-go-btn");
      var signin = d.getElementById("ao-pane-signin");
      var col = d.querySelector(".ao-form");
      var pb = pane.getBoundingClientRect(), bb = btn.getBoundingClientRect();
      var art = d.querySelector(".ao-art-img").getBoundingClientRect();
      var bs = w.getComputedStyle(btn);
      res.push({
        w: sz[0], vw: w.innerWidth,
        up: w.getComputedStyle(pane).display !== "none",
        // The pane it replaced is really gone, not sitting live underneath it.
        signinGone: w.getComputedStyle(signin).display === "none",
        // The ocean does not move. That is the whole point of the change.
        artMoved: (w.__ccArtBefore || []).map(function (v, k) {
          return Math.abs(v - [art.left, art.top, art.width, art.height][k]);
        }).some(function (dv) { return dv > 0.6; }),
        paneL: Math.round(pb.left), paneR: Math.round(pb.right),
        // Create Username: painted, in flow, full height.
        btnStatic: bs.position === "static",
        btnPainted: bs.backgroundImage.indexOf("gradient") >= 0 &&
                    bs.color !== "rgba(0, 0, 0, 0)" && parseFloat(bs.fontSize) > 8,
        // Nothing on this screen may push the page sideways.
        sideways: d.documentElement.scrollWidth > w.innerWidth + 1,
        // The button has to be reachable, scrolling the column if need be.
        buttonReachable: bb.height > 20 &&
                         col.scrollHeight >= btn.offsetTop + btn.offsetHeight,
        count: (d.getElementById("auth-nick-count") || {}).textContent
      });
    } catch (e) { res.push({ w: sz[0], err: String(e && e.message) }); }
  });
  document.getElementById("out").textContent = JSON.stringify(res);
}, 8000);
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
      check(at + " asking for a username put the pane up", !r.err && r.up, r.err);
      if (r.err || !r.up) return;
      check(at + " …and took the sign-in pane away rather than stacking on it", r.signinGone);
      check(at + " the ocean did not move", r.artMoved === false);
      check(at + " the pane fits inside the window",
            r.paneL >= 0 && r.paneR <= r.vw + 1, `l=${r.paneL} r=${r.paneR} vw=${r.vw}`);
      check(at + " Create Username is in the pane's flow, not positioned by the step",
            r.btnStatic);
      check(at + " …and it is actually painted, not transparent on transparent", r.btnPainted);
      check(at + " nothing pushes the page sideways", !r.sideways);
      check(at + " Create Username is reachable", r.buttonReachable);
      check(at + " the counter followed the typing", r.count === "10 / 15", r.count);
    });
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
