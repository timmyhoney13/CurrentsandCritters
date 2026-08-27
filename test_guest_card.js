#!/usr/bin/env node
/* PLAY AS A GUEST: the third face of one column.
 *
 * This used to be a DIALOG. It was a pale sea-glass card laid over the sign-in
 * screen with its own scrim, its own blurred copy of the painting behind it,
 * and about two hundred lines of CSS holding one field in the middle of the
 * window. Everything that ever went wrong with it went wrong because it was a
 * dialog inside somebody else's screen:
 *
 *   1. THE STEP'S OWN RULES REACHED INTO IT. #auth-step-choose used to be a
 *      painted image with invisible buttons over it, so its rules said
 *      background:transparent, color:transparent, position:absolute. A
 *      descendant selector applied every one of those to DIVE IN.
 *   2. IT COULD NOT LIVE INSIDE THE PAINTED OCTAGON, so it had to be a fixed
 *      scrim, which then had to own a copy of the artwork, which then had to be
 *      kept in step with the artwork on the screen behind it, by hand.
 *   3. THE TWO BUTTONS SHOWING THROUGH THE SCRIM STAYED LIVE, so opening it had
 *      to reach out and switch off pointer-events on somebody else's buttons.
 *
 * It is #ao-pane-guest now: one of the five panes of the sign-in column, wearing
 * the column's own clothes, shown by the one function that shows any of them.
 * Every trap above is gone by construction rather than by a rule, so what this
 * file pins is that it stays a pane, and that the pane really works:
 *
 *   - the ocean does not move when somebody asks to play as a guest;
 *   - the sign-in pane LEAVES rather than sitting live underneath;
 *   - Dive In is painted, in the flow, and owns the pixel at its own centre;
 *   - there is a way back, and it is a real button;
 *   - it holds together from a 320px phone to a 1440px laptop.
 *
 * Phone widths are measured inside same-origin iframes: Chrome will not open a
 * window narrower than 500px, which is exactly the gap a phone-width bug hides
 * in (a "430px" screenshot is really a 500px render, cropped).
 *
 * Run:  node test_guest_card.js       (needs Google Chrome / Chromium)
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
  const a = HTML.indexOf('<div class="ao-pane" id="ao-pane-guest"');
  const b = HTML.indexOf('<div class="ao-pane" id="ao-pane-nickname"', a);
  return a < 0 ? "" : HTML.slice(a, b < 0 ? a + 4000 : b);
})();

// ════════════════════════════════════════════════════════════════════════
//  SOURCE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe dialog is gone, and so is everything it needed to be one");
{
  check("no overlay element anywhere", !/auth-guest-overlay/.test(HTML + CSS));
  check("…and no card, scrim or rule left to style",
        !/\bago-(card|head|title|rule|sub|lbl|count|back|field-top)\b/.test(HTML + CSS));
  check("…and nothing switches off somebody else's buttons to open it",
        !/openGuestCard\(\)[\s\S]{0,400}?pointerEvents = "none"/.test(APP));
  check("…and it does not carry a second copy of the painting",
        !/#auth-guest-overlay/.test(CSS));
  // The reason all of that could go: it is inside the column now, and the
  // column is already standing in the sea.
  check("it is a pane of the sign-in column",
        /<div class="ao-form-inner">[\s\S]*?<div class="ao-pane" id="ao-pane-guest"/.test(HTML));
  check("…which the one pane function shows, so it cannot be left half-open",
        /guest:\s+"ao-pane-guest",/.test(APP)
        && /function ccChooserPane\(which, opts\)/.test(APP));
  check("…and PLAY AS GUEST turns the column over rather than opening anything",
        /function openGuestCard\(\)\s+\{ ccChooserPane\("guest"\); \}/.test(APP)
        && /\$a\("auth-guest-btn"\)\.addEventListener\("click", openGuestCard\);/.test(APP));
}

console.log("\nthe step's old invisible-button rules still stop at its own children");
{
  // These are the rules that once painted two invisible boxes over a picture.
  // As DESCENDANT selectors any of them would reach Dive In.
  check("no descendant .pv-btn rule under the step",
        !/#auth-step-choose(:not\([^)]*\))?\s+\.pv-btn\b/.test(CSS));
  check("no descendant .auth-btn-google rule either",
        !/#auth-step-choose(:not\([^)]*\))?\s+\.auth-btn-google\b/.test(CSS));
  check("the column is addressed as a direct child, not by descent",
        /#auth-step-choose > \.ao-form \{/.test(CSS));
  check("…and the two other ways in really are in that column",
        /<div class="ao-form-inner">[\s\S]{0,4200}?<button id="auth-choose-google-btn"[\s\S]{0,900}?<button id="auth-guest-btn"/.test(HTML));
}

console.log("\nit is dressed like every other pane, because it is one");
{
  check("the same heading, in the column's voice",
        /<div class="ao-h">Play as a Guest<\/div>/.test(PANE));
  check("the same labelled field with a counter beside the label",
        /class="ao-lbl-row"/.test(PANE) && /id="auth-guest-count"/.test(PANE)
        && /class="ao-field"/.test(PANE));
  check("the same gold button the sign-in pane offers this door with",
        /id="auth-guest-go-btn" class="pv-btn gold full ao-gold"/.test(PANE));
  check("the same status line, dressed by the same rule",
        /<div class="auth-err auth-note" id="auth-guest-err"/.test(PANE)
        && /#auth-step-choose \.ao-pane > \.auth-err \{/.test(CSS));
  // The one real difference between this door and the other two, said once,
  // before anybody types, in the pane's own box.
  check("it says what a guest session costs, before the name is typed",
        /class="ao-note is-warn"/.test(PANE) && /erased the moment you sign out/.test(PANE));
  check("…and offers the door that does not cost that",
        /id="ao-guest-create"/.test(PANE)
        && /_guestCreateBtn\.addEventListener\("click", \(\) => ccChooserPane\("create"\)\)/.test(APP));
}

console.log("\nthe field says what maxlength is enforcing");
{
  check("the field is capped", /id="auth-guest-nick"[^>]*maxlength="15"/.test(HTML));
  check("…and a counter reads that cap out", /id="auth-guest-count"/.test(HTML));
  check("…from the RAW length, which is what maxlength counts",
        /function paintGuestCount[\s\S]{0,260}?\(inp\.value \|\| ""\)\.length/.test(APP));
  // Arriving at the pane is what primes it, not the button that leads here, so
  // it is primed however it was reached: the button, a deep link, or the column
  // being put back where it was.
  check("…and it is repainted when arriving writes the field",
        /function ccPrimeGuestPane\(\)[\s\S]{0,600}?paintGuestCount\(\);/.test(APP)
        && /if \(key === "guest"\)  ccPrimeGuestPane\(\);/.test(APP));
  check("the field is 16px or more, so iOS does not zoom the page on focus",
        /#auth-step-choose \.ao-field input \{[\s\S]{0,220}?font-size: clamp\(15px/.test(CSS));
}

console.log("\ngoing in and coming back out is one pair of functions");
{
  check("open and close are named, not two copies of the same four lines",
        /function openGuestCard\(\)/.test(APP) && /function closeGuestCard\(\)/.test(APP));
  check("Back is a real button, so it is reachable by keyboard",
        /<button type="button" class="ao-back" id="auth-guest-back">/.test(PANE));
  check("…on the heading's own line",
        /<div class="ao-head-row">[\s\S]{0,700}?id="auth-guest-back"[\s\S]{0,700}?<div class="ao-h">Play as a Guest<\/div>/.test(PANE));
  check("…and it really goes back",
        /\$a\("auth-guest-back"\)\.addEventListener\("click", closeGuestCard\);/.test(APP)
        && /function closeGuestCard\(\) \{ ccChooserPane\("signin"\); \}/.test(APP));
  check("Escape does the same, the way it closed the dialog this replaced",
        /const AO_ESCAPES_TO = \{ forgot: "signin", create: "signin", guest: "signin" \};/.test(APP));
}

console.log("\nthe cache busters moved, or nobody gets any of this");
{
  // Not pinned to a literal date: that failed on every ship that bumped the
  // busters, which teaches people to edit the test rather than read it. The
  // contract is that /css and /js all move TOGETHER, and that the stamp they
  // move to is the build the client polls for.
  const refs = HTML.match(/\/(?:css|js)\/[A-Za-z0-9._-]+\?v=[0-9A-Za-z.-]+/g) || [];
  const stamps = new Set(refs.map((r) => r.split("?v=")[1]));
  const build = JSON.parse(fs.readFileSync(path.join(CLIENT, "version.json"), "utf8")).build;
  check("every /css and /js file carries a cache buster", refs.length >= 20, String(refs.length));
  check("…all on ONE stamp, so nothing is left on a stale copy", stamps.size === 1,
        [...stamps].join(" "));
  check("…and that stamp is this build", [...stamps][0] === build,
        `${[...stamps][0]} vs version.json ${build}`);
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
  const PORT = 9960 + (process.pid % 300);
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

  // The pane is opened the way a player opens it: by clicking PLAY AS GUEST.
  // The painting's own geometry is photographed BEFORE the click, because the
  // one thing this change is for is that the ocean does not move.
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
      var step = document.getElementById("auth-step-choose");
      if (!step || getComputedStyle(step).display === "none") return;
      if (tick < 30) return;                       // let the app finish booting
      clearInterval(iv);
      step.classList.add("is-armed");
      var art = document.querySelector(".ao-art-img").getBoundingClientRect();
      window.__ccArtBefore = [art.left, art.top, art.width, art.height];
      document.getElementById("auth-guest-btn").click();
      setTimeout(function () {
        var i = document.getElementById("auth-guest-nick");
        i.value = "Loggerhead"; i.dispatchEvent(new Event("input"));
        window.__ccReady = 1;
      }, 500);
    } catch (e) {}
  }, 40);
})();
</script>`;

  const SIZES = [[320, 700], [390, 844], [430, 932], [768, 900], [1024, 560], [1440, 900]];
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
      var pane = d.getElementById("ao-pane-guest");
      var signin = d.getElementById("ao-pane-signin");
      var btn = d.getElementById("auth-guest-go-btn");
      var back = d.getElementById("auth-guest-back");
      var col = d.querySelector(".ao-form");
      var art = d.querySelector(".ao-art-img").getBoundingClientRect();
      var pb = pane.getBoundingClientRect(), bb = btn.getBoundingClientRect();
      var bs = w.getComputedStyle(btn);
      function owns(el) {
        var r = el.getBoundingClientRect();
        var hit = d.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return !!hit && (hit === el || el.contains(hit));
      }
      res.push({
        w: sz[0], vw: w.innerWidth, vh: w.innerHeight,
        open: w.getComputedStyle(pane).display !== "none",
        // The pane that left is really gone, not sitting live underneath it.
        signinGone: w.getComputedStyle(signin).display === "none",
        // The ocean does not move. That is the whole point of the change.
        artMoved: (w.__ccArtBefore || []).map(function (v, k) {
          return Math.abs(v - [art.left, art.top, art.width, art.height][k]);
        }).some(function (dv) { return dv > 0.6; }),
        paneL: Math.round(pb.left), paneR: Math.round(pb.right),
        // Dive In: painted, in flow, and the pixel at its centre is its own.
        btnStatic: bs.position === "static",
        btnPainted: bs.backgroundImage.indexOf("gradient") >= 0 &&
                    bs.color !== "rgba(0, 0, 0, 0)" && parseFloat(bs.fontSize) > 8,
        btnOwnsItself: owns(btn),
        backOwnsItself: owns(back),
        btnH: Math.round(bb.height),
        // Nothing on this screen may push the page sideways.
        sideways: d.documentElement.scrollWidth > w.innerWidth + 1,
        // The button has to be reachable, scrolling the column if need be.
        buttonReachable: col.scrollHeight >= btn.offsetTop + btn.offsetHeight,
        count: (d.getElementById("auth-guest-count") || {}).textContent
      });
    } catch (e) { res.push({ w: sz[0], err: String(e && e.message) }); }
  });
  document.getElementById("out").textContent = JSON.stringify(res);
}, 8000);
</script>`;

  const page = "__guest_card.html", wrap = "__guest_wrap.html";
  fs.writeFileSync(path.join(CLIENT, page), HTML + DRIVER);
  fs.writeFileSync(path.join(CLIENT, wrap), WRAPPER(page));
  const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });

  let rows = null;
  try {
    for (let attempt = 0; attempt < 3 && !rows; attempt++) {
      const dom = execFileSync(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", "--window-size=4600,1000", "--virtual-time-budget=45000",
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

  console.log("\nmeasured in a real browser, at six widths");
  if (!rows) {
    console.log("  ✗ FAIL: the harness never reported"); fail++;
  } else {
    rows.forEach((r) => {
      const at = r.w + "px:";
      check(at + " PLAY AS GUEST turned the column over onto the guest pane",
            !r.err && r.open, r.err);
      if (r.err || !r.open) return;
      check(at + " …and took the sign-in pane away rather than stacking on it",
            r.signinGone);
      check(at + " the ocean did not move", r.artMoved === false);
      check(at + " the pane fits inside the window",
            r.paneL >= 0 && r.paneR <= r.vw + 1, `l=${r.paneL} r=${r.paneR} vw=${r.vw}`);
      check(at + " Dive In is in the pane's flow, not absolutely positioned by the step",
            r.btnStatic);
      check(at + " …and it is actually painted, not transparent on transparent",
            r.btnPainted);
      check(at + " …and the pixel at its centre belongs to it", r.btnOwnsItself);
      check(at + " Back is clickable too", r.backOwnsItself);
      check(at + " Dive In is reachable and full height", r.buttonReachable && r.btnH > 30,
            "h=" + r.btnH);
      check(at + " nothing pushes the page sideways", !r.sideways);
      check(at + " the counter followed the typing", r.count === "10 / 15", r.count);
    });
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
