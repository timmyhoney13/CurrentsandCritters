#!/usr/bin/env node
/* PLAY AS A GUEST: the other half of first sign-in.
 *
 * Two people click two different buttons on the sign-in screen and are asked for the
 * same thing. One lands on CREATE YOUR USERNAME, the other lands here, so the
 * two screens are drawn the same way and this file pins that they stay that
 * way. What it really guards is the trap that made this card look homemade:
 *
 *   1. THE STEP'S BUTTON RULES MUST NOT REACH INTO THE CARD. #auth-step-choose
 *      is one painted image with two INVISIBLE buttons over it, so its rules
 *      say background:transparent, color:transparent, font-size:0 and
 *      position:absolute at left:36.9%. The guest card is a child of that same
 *      step, and a descendant selector applied all of it to Dive In: a
 *      transparent button, absolutely positioned off the card, gone. Every one
 *      of those rules is scoped with > now.
 *
 *   2. THE CARD CANNOT LIVE INSIDE THE PAINTED OCTAGON. It used to be pinned
 *      at 33%/41%/34% of a fixed-aspect step and drawn translucent, so the
 *      artwork's own SIGN IN OR CREATE AN ACCOUNT WITH GOOGLE read straight
 *      through it. It is a dialog over a scrim, which owes nothing to where
 *      the art happens to be.
 *
 *   3. THE SCRIM MUST BE FIXED, NOT ABSOLUTE. #auth-step-choose is letterboxed
 *      to the artwork's aspect ratio, so an absolute scrim stops at the navy
 *      bars and dims a band rather than the screen.
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
// The guest card now has a TWIN: #auth-account-overlay, the sign-in / create
// card, is the same card asking a different question, so every rule below is
// written for both ids at once. Reading this file for the guest card's rules
// therefore means dropping the twin's half of each selector list first, which
// is what this does. Nothing else changes: what is asserted below is still
// exactly the rule that reaches the guest card.
const CSS  = read("css/preview.css")
  .replace(/,\s*#auth-account-overlay[^,{]*?(?=\s*[,{])/g, "");
const APP  = read("js/preview-app.js");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

// The card's own markup, so "what is inside it" is a real question.
// Stops at the account card, which follows it inside the same step: what is
// asserted here is what is on the GUEST card, and the twin has its own notes,
// its own icons and its own second form.
const CARD = (() => {
  const a = HTML.indexOf('<div id="auth-guest-overlay"');
  let b = HTML.indexOf('<div id="auth-account-overlay"', a);
  if (b < 0) b = HTML.indexOf("<!-- Step 2a", a);
  return HTML.slice(a, b);
})();

// ════════════════════════════════════════════════════════════════════════
//  SOURCE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe step's invisible-button rules stop at its own children");
{
  // The whole bug in one assertion: any of these as a DESCENDANT selector puts
  // background:transparent / color:transparent / position:absolute on Dive In.
  check("no descendant .pv-btn rule under the step",
        !/#auth-step-choose(:not\([^)]*\))?\s+\.pv-btn\b/.test(CSS),
        "that rule paints the step's invisible buttons and would hit Dive In");
  check("no descendant .auth-btn-google rule either",
        !/#auth-step-choose(:not\([^)]*\))?\s+\.auth-btn-google\b/.test(CSS));
  check("no descendant .auth-err rule, which absolutely positions it at top:72%",
        !/#auth-step-choose\s+\.auth-err\b/.test(CSS));
  // The step's own things are addressed either as direct children or through
  // .ao-form-inner, which is the sign-in column. Neither reaches a card: both
  // cards are children of the step, and neither is inside that column.
  check("…they are addressed through the column instead, which no card is in",
        /#auth-step-choose > \.ao-form \{/.test(CSS)
        && /#auth-step-choose \.ao-form-inner > \.auth-err \{/.test(CSS)
        && !/<div id="auth-guest-overlay"[\s\S]{0,4000}?class="ao-form-inner"/.test(HTML));
  // The window is generous because the sign-in column, its two fields and the
  // lettering over the painting all sit between the step and its first button.
  // What is being pinned is that the
  // button is INSIDE the step and not nested in something else.
  check("…and the two ways in they aim at really are in that column",
        /<div class="ao-form-inner">[\s\S]{0,4200}?<button id="auth-choose-google-btn"[\s\S]{0,900}?<button id="auth-guest-btn"/.test(HTML));
}

console.log("\nit opens ON the sign-in painting, the screen it was called from");
{
  const OL = (() => {
    const a = CSS.indexOf("#auth-guest-overlay {");
    return a < 0 ? "" : CSS.slice(a, CSS.indexOf("#auth-guest-overlay .ago-card {", a));
  })();
  // The card opens ON the chooser, so the room must not change when it does:
  // the same painting, letterboxed the same way, with a scrim over it.
  check("the scenery is the sign-in painting, not a flat dark scrim",
        /#auth-guest-overlay::after[\s\S]{0,1100}?auth-ocean\.jpg/.test(OL));
  // cover, not contain. The sign-in painting is a tall panel that fills half a
  // wide screen; letterboxing it whole behind a card stands the card on two
  // navy bars.
  check("…covering the window, the way a room does",
        /#auth-guest-overlay::after[\s\S]{0,1100}?auth-ocean\.jpg[^;]*center \/ cover no-repeat/.test(OL));
  check("…over the same blurred copy the sign-in screen sits on",
        /#auth-guest-overlay::before[\s\S]{0,400}?auth-ocean\.jpg[^;]*center \/ cover/.test(OL)
        && /#auth-guest-overlay::before[\s\S]{0,400}?filter: blur\(34px\)/.test(OL));
  // A live form and three buttons underneath. Anything that still looks usable
  // behind an open card is the one way this screen can be misread, so it goes
  // under a pool of deeper water.
  check("…with the screen underneath sunk under a scrim so it cannot be misread",
        /#auth-guest-overlay::after[\s\S]{0,900}?radial-gradient\(ellipse 49% 66% at 50% 50%,\s*\n\s*rgba\(3,20,42,\.9/.test(OL));
  check("…and the oceans around it left bright, which is the point of using it",
        /rgba\(6,32,60,\.04\)/.test(OL));
  check("the kelp forest is gone from this screen",
        !/url\("\/backgrounds\/kelp-forest\.png"\)/.test(OL));
  // The pairing is read off the file as it really is: the loader above
  // deliberately strips ", #auth-account-overlay…" out of its copy, which is
  // the one thing that would hide a rule the two screens share.
  const RAW = read("css/preview.css");
  check("…and a guest and an account holder stand in the same room",
        /#auth-guest-overlay::after,\s*\n\s*#auth-account-overlay::after \{/.test(RAW));
  check("…as does CREATE YOUR USERNAME, the screen either of them opens onto",
        /#auth-screen \.auth-step-bg::after \{[\s\S]{0,1400}?auth-ocean\.jpg/.test(RAW));
  // A background that 404s is silent: the screen just goes flat navy. The
  // .webp sibling matters as much as the .png, because the server negotiates
  // it and every modern browser is served that one, not the PNG.
  check("the painting is really in the client",
        fs.existsSync(path.join(CLIENT, "auth-ocean.jpg")));
  check("…with its WebP sibling, which is what most browsers are actually sent",
        fs.existsSync(path.join(CLIENT, "auth-ocean.webp")));
  check("…under a vignette, so the eye goes to the card",
        /#auth-guest-overlay::after[\s\S]{0,900}?radial-gradient/.test(OL));
  check("the scenery cannot take a click meant for the card",
        (OL.match(/pointer-events: none/g) || []).length >= 2);
  check("…and the card stacks in FRONT of it",
        /#auth-guest-overlay \.ago-card \{[\s\S]{0,400}?position: relative; z-index: 1;/.test(CSS),
        "generated content on a positioned parent paints over an unpositioned child");
}

console.log("\nthe card is a dialog over a scrim, not a slab inside the octagon");
{
  check("it is fixed, so the scrim covers the screen and not the letterboxed step",
        /#auth-guest-overlay\s*\{[^}]*position:\s*fixed/.test(CSS),
        "#auth-step-choose is only as tall as the artwork's aspect ratio allows");
  check("…no percentage-of-the-artwork pinning left",
        !/#auth-guest-overlay\s*\{[^}]*\btop:\s*\d+%/.test(CSS)
        && !/#auth-guest-overlay\s*\{[^}]*\bleft:\s*\d+%/.test(CSS));
  check("…and it is centred by the layout, not by arithmetic",
        /#auth-guest-overlay\s*\{[^}]*align-items:\s*center/.test(CSS)
        && /#auth-guest-overlay\s*\{[^}]*justify-content:\s*center/.test(CSS));
  check("the card behind the text is opaque, so painted lettering cannot read through",
        /#auth-guest-overlay \.ago-card\s*\{[\s\S]*?rgba\(250,253,255,\.97\)/.test(CSS));
  check("it still lives inside the step, so showStep hiding the step hides it",
        /<div id="auth-step-choose"[\s\S]*?<div id="auth-guest-overlay"/.test(HTML));
}

console.log("\nit is dressed like CREATE YOUR USERNAME, because it is the same moment");
{
  check("same rounded face on the title, not the game's serif",
        /#auth-guest-overlay \.ago-title\s*\{[^}]*"Baloo 2"/.test(CSS));
  check("same butter-gold button with dark ink, not the dark mustard one",
        /#auth-guest-overlay \.pv-btn\.gold\s*\{[^}]*color:\s*#4a3208/.test(CSS));
  check("same sea-glass card gradient", /#auth-guest-overlay \.ago-card[\s\S]{0,700}?linear-gradient\(168deg/.test(CSS));
  check("same gold swash under the title", /#auth-guest-overlay \.ago-rule\s*\{/.test(CSS));
  check("same labelled field with a counter beside the label",
        /#auth-guest-overlay \.ago-lbl\s*\{/.test(CSS) && /#auth-guest-overlay \.ago-count\s*\{/.test(CSS));
  // The card asks one thing and says nothing else. The tinted row explaining
  // that a guest's stats live in this browser is gone, and so is its CSS: the
  // Player Home guest banners already say it, at the moment it matters.
  check("nothing on the card but the ask",
        !/class="ago-note"/.test(CARD) && !/in this browser/.test(CARD)
        && !/ago-note|ago-ico/.test(CSS));
  check("no emoji the platform picks its own art for", !/[\u{1F300}-\u{1FAFF}]/u.test(CARD));
  check("every rule is scoped to the card, so no other auth box is touched",
        (CSS.match(/^\s*#auth-guest-overlay/gm) || []).length > 20);
}

console.log("\nthe field says what maxlength is enforcing");
{
  check("the field is capped", /id="auth-guest-nick"[^>]*maxlength="15"/.test(HTML));
  check("…and a counter reads that cap out", /id="auth-guest-count"/.test(HTML));
  check("…from the RAW length, which is what maxlength counts",
        /function paintGuestCount[\s\S]{0,260}?\(inp\.value \|\| ""\)\.length/.test(APP));
  // Anchored on the two lines that matter, not on a character budget from the
  // top of the function: opening the card blanks the field, so the counter has
  // to be repainted right there or it keeps reading the last guest's name.
  check("…and it is repainted when opening the card writes the field",
        /\$a\("auth-guest-nick"\)\.value = [^\n]*;\s*\n\s*paintGuestCount\(\);/.test(APP));
  check("the field is 16px, so iOS does not zoom the page on focus",
        /#auth-guest-overlay \.pv-input\s*\{[^}]*font-size:\s*16px/.test(CSS));
}

console.log("\nopening and closing it is one pair of functions");
{
  check("open and close are named, not two copies of the same four lines",
        /function openGuestCard\(\)/.test(APP) && /function closeGuestCard\(\)/.test(APP));
  check("both painted buttons underneath are held inert while it is up",
        /function openGuestCard\(\)[\s\S]{0,400}?auth-guest-btn"\)\.style\.pointerEvents = "none"[\s\S]{0,200}?auth-choose-google-btn"\)\.style\.pointerEvents = "none"/.test(APP),
        "the scrim does not stop a click reaching an invisible box");
  check("…and released again on the way out",
        /function closeGuestCard\(\)[\s\S]{0,300}?pointerEvents = ""/.test(APP));
  check("Escape closes it, as a dialog should",
        /e\.key !== "Escape"[\s\S]{0,240}?closeGuestCard\(\)/.test(APP));
  check("a click on the scrim does NOT, because CONTINUE WITH GOOGLE shows through it",
        !/auth-guest-overlay"\)\.addEventListener\("click"/.test(APP));
  check("Back is a real button, so it is reachable by keyboard",
        /<button type="button" class="ago-back" id="auth-guest-back"/.test(CARD));
  check("the dialog announces itself as one",
        /id="auth-guest-overlay" role="dialog" aria-modal="true"/.test(HTML));
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

  // The card is opened the way a player opens it: by clicking PLAY AS GUEST.
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
      document.getElementById("auth-guest-btn").click();
      var i = document.getElementById("auth-guest-nick");
      i.value = "Loggerhead"; i.dispatchEvent(new Event("input"));
      window.__ccReady = 1;
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
      var ol = d.getElementById("auth-guest-overlay");
      var card = ol.querySelector(".ago-card");
      var btn = d.getElementById("auth-guest-go-btn");
      var back = d.getElementById("auth-guest-back");
      var cb = card.getBoundingClientRect(), bb = btn.getBoundingClientRect();
      var bs = w.getComputedStyle(btn), os = w.getComputedStyle(ol);
      function owns(el) {
        var r = el.getBoundingClientRect();
        var hit = d.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return !!hit && (hit === el || el.contains(hit));
      }
      res.push({
        w: sz[0], vw: w.innerWidth, vh: w.innerHeight,
        open: os.display !== "none",
        // The scrim is the whole screen, not the letterboxed artwork.
        scrimW: Math.round(ol.getBoundingClientRect().width),
        scrimH: Math.round(ol.getBoundingClientRect().height),
        cardL: Math.round(cb.left), cardR: Math.round(cb.right),
        // Dive In: painted, in flow, and the pixel at its centre is its own.
        btnStatic: bs.position === "static",
        btnPainted: bs.backgroundImage.indexOf("gradient") >= 0 &&
                    bs.color !== "rgba(0, 0, 0, 0)" && parseFloat(bs.fontSize) > 8,
        btnOwnsItself: owns(btn),
        backOwnsItself: owns(back),
        btnH: Math.round(bb.height),
        // Nothing on this screen may push the page sideways.
        sideways: d.documentElement.scrollWidth > w.innerWidth + 1,
        // The button has to be reachable, scrolling the card if need be.
        buttonReachable: card.scrollHeight >= btn.offsetTop + btn.offsetHeight,
        count: (d.getElementById("auth-guest-count") || {}).textContent
      });
    } catch (e) { res.push({ w: sz[0], err: String(e && e.message) }); }
  });
  document.getElementById("out").textContent = JSON.stringify(res);
}, 7000);
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
      check(at + " PLAY AS GUEST opened the card", !r.err && r.open, r.err);
      if (r.err || !r.open) return;
      check(at + " the scrim covers the whole screen, not just the artwork",
            r.scrimW >= r.vw && r.scrimH >= r.vh, `${r.scrimW}x${r.scrimH} vs ${r.vw}x${r.vh}`);
      check(at + " the card fits, centred, with a gutter on both sides",
            r.cardL >= 8 && r.vw - r.cardR >= 8 && Math.abs(r.cardL - (r.vw - r.cardR)) <= 2,
            `l=${r.cardL} r=${r.vw - r.cardR} vw=${r.vw}`);
      check(at + " Dive In is in the card's flow, not absolutely positioned by the step",
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
