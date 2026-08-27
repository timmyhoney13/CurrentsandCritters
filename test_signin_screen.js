#!/usr/bin/env node
/* The sign-in screen: the painting, the seam, and the pane that turns over.
 *
 * Three things this file exists to stop coming back:
 *
 *   1. THE PAINTING WAS CROPPED. It was object-fit:cover in a panel that is
 *      whatever shape the window is, so the birds came off the top, the coral
 *      off the bottom and both edges besides. It is CONTAINED now, inside a box
 *      that stops short of the diagonal, so the whole picture is on the screen
 *      at every window shape. Measured as: the drawn image keeps the artwork's
 *      own 1600x2000 ratio (nothing cropped), sits inside the viewport, and
 *      never crosses the cut.
 *
 *   2. A DARK SLIT DOWN THE MIDDLE. The diagonal leaves a wedge of screen
 *      between the painting's cut edge and the sign-in column, and that wedge
 *      used to be the step's own flat navy: a third colour on a screen with two
 *      sides. The column paints nothing now and the step paints the field
 *      across the whole screen, so the wedge IS the column. Measured off a real
 *      screenshot: the pixels in the wedge have to match the pixels of the
 *      column beside them.
 *
 *   3. CREATE AN ACCOUNT WAS A POP-UP. It is a second pane of the same column
 *      now: the ocean does not move, the form turns over. Measured as: the
 *      painting is in exactly the same place before and after, the old overlay
 *      is not in the document at all, and the pane that left is really gone
 *      rather than sitting underneath.
 *
 * Run:  node test_signin_screen.js      (needs Google Chrome / Chromium)
 */
"use strict";

const fs = require("fs");
const path = require("path");
const os = require("os");
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
  else { fail++; console.log("  ✗ FAIL: " + name + (extra != null ? "  → " + extra : "")); }
}

// ══════════════════════════════════════════════════════════════════════════
//  SOURCE
// ══════════════════════════════════════════════════════════════════════════
console.log("\nthe whole painting, at every window shape");
{
  check("the art is contained, not cropped",
        /#auth-step-choose \.ao-art-img \{[\s\S]{0,260}?object-fit: contain;/.test(CSS));
  check("…inside a box that stops short of the diagonal",
        /#auth-step-choose \.ao-art-img \{[\s\S]{0,200}?width: 88%;/.test(CSS));
  check("…standing in its own water, so the room around it is not a border",
        /#auth-step-choose > \.ao-art::before \{[\s\S]{0,300}?auth-ocean\.jpg[\s\S]{0,120}?filter: blur/.test(CSS));
  check("the phone band shows all of it too, not a slice of the top",
        /#auth-step-choose \.ao-art-img \{ width: 100%; \}/.test(CSS)
        && !/object-position: 50% 2%/.test(CSS));
}

console.log("\none surface on the right, with no slit down the middle");
{
  check("the step paints the column's field across the whole screen",
        /#auth-step-choose\.auth-box \{[\s\S]{0,1400}?linear-gradient\(168deg, #0a1c33 0%, #061527 50%, #040f1e 100%\) !important;/.test(CSS));
  check("…and the column itself paints nothing on top of it",
        /#auth-step-choose > \.ao-form \{[\s\S]{0,600}?background: transparent;/.test(CSS));
  check("the flat navy that used to fill the wedge is gone",
        !/background: #04101f !important;/.test(CSS));
}

console.log("\ncreate an account is a pane, not a pop-up");
{
  check("the old dialog is not in the document at all",
        !/auth-account-overlay/.test(HTML + CSS + APP) && !/aao-/.test(HTML + CSS + APP));
  check("there are two panes in the one column",
        /id="ao-pane-signin"/.test(HTML) && /id="ao-pane-create"/.test(HTML));
  check("…and one function decides which is showing",
        /function ccChooserPane\(which, opts\)/.test(APP)
        && /const AO_PANES = \{ signin: "ao-pane-signin", create: "ao-pane-create" \};/.test(APP));
  check("CREATE AN ACCOUNT turns the column over rather than opening anything",
        /\$a\("ao-create"\)\.addEventListener\("click", \(\) => ccChooserPane\("create"\)\);/.test(APP));
  check("…and there is a way back to signing in",
        /\$a\("ao-back-signin"\)\.addEventListener\("click", \(\) => \{[\s\S]{0,140}?ccChooserPane\("signin"\);/.test(APP));
  check("the pane arrives with an animation, and leaves with one",
        /@keyframes aoPaneIn \{/.test(CSS) && /@keyframes aoPaneOut \{/.test(CSS)
        && /#auth-step-choose \.ao-pane \{ animation: aoPaneIn/.test(CSS));
  check("…which somebody who asked for less motion does not get",
        /@media \(prefers-reduced-motion: reduce\) \{[\s\S]{0,220}?\.ao-pane[\s\S]{0,120}?animation-duration: \.01ms;/.test(CSS));
  check("a password is never left sitting in a field on a shared computer",
        /function ccClearCreateFields\(\)/.test(APP)
        && /\["ao-new-pass", "ao-new-pass2"\]\.forEach/.test(APP));
  check("an email can be linked while the account is being made",
        /id="ao-new-email"/.test(HTML) && /const email = \(\$a\("ao-new-email"\)\.value \|\| ""\)\.trim\(\);/.test(APP));
  check("…and a typo in it stops the sign-up rather than being kept",
        /if \(email && !ccValidEmail\(email\)\) \{/.test(APP));
}

// ══════════════════════════════════════════════════════════════════════════
//  DRIVE
// ══════════════════════════════════════════════════════════════════════════
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => fs.existsSync(p));

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: skipping the drive half.");
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

const PORT = 9310 + (process.pid % 300);
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

function driver(body, opts) {
  const noAnim = (opts && opts.animate) ? "" :
    'var st=document.createElement("style");' +
    'st.textContent="*,*::before,*::after{transition:none!important;animation:none!important}";' +
    'document.head.appendChild(st);';
  return `
<div id="out">PENDING</div>
<script>
(function () {
  ${noAnim}
  var log = {}, out = document.getElementById("out");
  function done() { out.textContent = JSON.stringify(log); }
  function R(sel){ var e=document.querySelector(sel); if(!e) return null;
    var r=e.getBoundingClientRect();
    return {l:r.left,t:r.top,w:r.width,h:r.height,r:r.right,b:r.bottom}; }
  var tick = 0;
  var iv = setInterval(function () {
    if (++tick > 400) { log.fatal = "timeout"; done(); clearInterval(iv); return; }
    try {
      var spl = document.getElementById("cc-fs-splash");
      if (spl && getComputedStyle(spl).display !== "none") spl.style.display = "none";
      if (tick > 20) {
        var ls = document.getElementById("auth-loading-screen");
        if (ls) ls.classList.add("hidden");
        var as = document.getElementById("auth-screen");
        if (as) as.classList.remove("hidden");
      }
      var step = document.getElementById("auth-step-choose");
      if (!step || getComputedStyle(step).display === "none") return;
      if (tick < 25) return;
      clearInterval(iv);
      step.classList.add("is-armed");
      ${body}
    } catch (e) { log.err = String(e && e.message); done(); }
  }, 40);
})();
</script>`;
}

const tmp = [];
function run(name, body, w, h, ok, opts) {
  const f = path.join(CLIENT, name);
  fs.writeFileSync(f, HTML + driver(body, opts));
  if (!tmp.includes(f)) tmp.push(f);
  let last = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    const args = ["--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      `--window-size=${w},${h}`, "--virtual-time-budget=30000"];
    if (opts && opts.shot) args.push(`--screenshot=${opts.shot}`);
    args.push("--dump-dom", `http://localhost:${PORT}/${name}?game_window=1`);
    let dom = "";
    try {
      dom = execFileSync(CHROME, args,
        { encoding: "utf8", maxBuffer: 64e6, stdio: ["ignore", "pipe", "ignore"], timeout: 90000 });
    } catch (_) { continue; }
    const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
    const raw = m ? m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                        .replace(/&lt;/g, "<").replace(/&gt;/g, ">") : "";
    if (!raw || raw === "PENDING") continue;
    let r; try { r = JSON.parse(raw); } catch (_) { continue; }
    last = r;
    if (r.fatal || r.err) continue;
    if (!ok || ok(r)) return r;
  }
  return last;
}

const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });
try { execFileSync(process.execPath, ["-e", "setTimeout(()=>{},900)"]); } catch (_) {}

const ART_RATIO = 1600 / 2000;

try {
  // ── 1. The whole painting, at six shapes ──────────────────────────────
  console.log("\nthe painting is all there, and inside the screen, at every size");
  for (const [w, h] of [[1920, 1080], [1440, 900], [1280, 1024], [1100, 620], [1024, 768], [430, 860]]) {
    const r = run("_ss_fit.html", `
      log.img  = R(".ao-art-img");
      log.art  = R(".ao-art");
      log.vw = innerWidth; log.vh = innerHeight;
      log.overflowX = document.documentElement.scrollWidth > innerWidth + 1;
      done();
    `, w, h, (r) => r.img);
    if (!r || !r.img) { check(`${w}x${h}: the harness could measure the art`, false, r && (r.err || r.fatal)); continue; }
    const ratio = r.img.w / r.img.h;
    // A contained image is drawn at its own ratio inside its box; the BOX is
    // what the rect reports, so what is measured is the drawn picture: the box
    // is exactly as tall as the panel and the picture fills one axis of it.
    const drawnW = Math.min(r.img.w, r.img.h * ART_RATIO);
    const drawnH = drawnW / ART_RATIO;
    check(`${w}x${h}: the whole picture fits inside its box (nothing cropped)`,
          drawnW <= r.img.w + 1 && drawnH <= r.img.h + 1,
          `drawn ${Math.round(drawnW)}x${Math.round(drawnH)} in ${Math.round(r.img.w)}x${Math.round(r.img.h)}`);
    check(`${w}x${h}: …and it is on the screen, not off an edge of it`,
          r.img.l >= -1 && r.img.t >= -1 && r.img.r <= r.vw + 1 && r.img.b <= r.vh + 1,
          `${JSON.stringify(r.img)} in ${r.vw}x${r.vh}`);
    check(`${w}x${h}: …and the drawn picture is a real size, not a sliver`,
          drawnW > Math.min(120, r.vw * 0.16), `${Math.round(drawnW)}px wide`);
    check(`${w}x${h}: the page never scrolls sideways`, r.overflowX === false);
  }

  // ── 2. The seam, off a real screenshot ────────────────────────────────
  console.log("\nthere is no dark slit between the two sides");
  {
    const shot = path.join(os.tmpdir(), `cc_signin_${process.pid}.png`);
    const r = run("_ss_seam.html", `
      log.art = R(".ao-art");
      log.form = R(".ao-form");
      log.stepBg = getComputedStyle(document.getElementById("auth-step-choose")).backgroundImage;
      log.formBg = getComputedStyle(document.querySelector(".ao-form")).backgroundImage;
      done();
    `, 1440, 900, (r) => r.art, { shot });
    check("the column declares no background of its own", r && r.formBg === "none", r && r.formBg);
    check("…and the step declares the gradient field", r && /linear-gradient/.test(r.stepBg || ""));
    if (fs.existsSync(shot)) {
      // The wedge is between the painting's cut edge and the column's left
      // edge. Sample a row across it and compare it to the column just to the
      // right: one surface means one colour.
      const py = `
import json, sys
from PIL import Image
im = Image.open(${JSON.stringify(shot)}).convert("RGB")
W, H = im.size
px = im.load()
out = []
for frac in (0.30, 0.55, 0.80, 0.95):
    y = int(H * frac)
    # the cut runs from 50% at the top to 44% at the bottom, in CSS pixels;
    # the screenshot may be scaled, so work in fractions of the width.
    cut = (0.50 - 0.06 * frac)
    x_wedge0 = int(W * (cut + 0.012))     # clear of the lit edge line
    x_wedge1 = int(W * 0.495)
    col = [px[x, y] for x in range(int(W * 0.52), int(W * 0.60))]
    wedge = [px[x, y] for x in range(x_wedge0, x_wedge1)]
    if not wedge or not col: continue
    def mean(v): return [sum(c[i] for c in v) / len(v) for i in range(3)]
    mw, mc = mean(wedge), mean(col)
    darkest = min(sum(c) for c in wedge) / 3.0
    colmean = sum(mc) / 3.0
    out.append({"y": frac, "wedge": mw, "col": mc,
                "diff": max(abs(a - b) for a, b in zip(mw, mc)),
                "darkest": darkest, "colmean": colmean})
print(json.dumps(out))
`;
      let rows = [];
      try { rows = JSON.parse(execFileSync("python3", ["-c", py], { encoding: "utf8" })); }
      catch (e) { check("the screenshot could be read", false, String(e && e.message)); }
      for (const row of rows) {
        check(`at ${Math.round(row.y * 100)}% down: the wedge is the same colour as the column`,
              row.diff < 9,
              `wedge ${row.wedge.map(Math.round)} vs column ${row.col.map(Math.round)}`);
        check(`…and nothing in it is a dark slit`,
              row.darkest > row.colmean - 9,
              `darkest ${row.darkest.toFixed(1)} vs column ${row.colmean.toFixed(1)}`);
      }
      try { fs.unlinkSync(shot); } catch (_) {}
    } else {
      check("a screenshot was taken", false, "chrome wrote no file");
    }
  }

  // ── 3. The pane turns over, and the ocean does not move ───────────────
  console.log("\ncreate an account turns the column over, and the ocean stays put");
  {
    const r = run("_ss_pane.html", `
      log.overlayGone = !document.getElementById("auth-account-overlay");
      log.artBefore = R(".ao-art-img");
      log.signinBefore = getComputedStyle(document.getElementById("ao-pane-signin")).display;
      document.getElementById("ao-create").click();
      setTimeout(function () {
        log.artAfter = R(".ao-art-img");
        log.signinAfter = getComputedStyle(document.getElementById("ao-pane-signin")).display;
        log.createAfter = getComputedStyle(document.getElementById("ao-pane-create")).display;
        var cr = R("#ao-pane-create");
        log.createOnScreen = !!cr && cr.w > 100 && cr.h > 100;
        log.hasEmail = !!document.getElementById("ao-new-email");
        // …and back again
        document.getElementById("ao-back-signin").click();
        setTimeout(function () {
          log.backSignin = getComputedStyle(document.getElementById("ao-pane-signin")).display;
          log.backCreate = getComputedStyle(document.getElementById("ao-pane-create")).display;
          log.artBack = R(".ao-art-img");
          done();
        }, 600);
      }, 600);
    `, 1440, 900, (r) => r.backSignin, { animate: true });
    check("the old pop-up is not in the document", r && r.overlayGone === true);
    check("clicking CREATE AN ACCOUNT puts the create pane up",
          r && r.createAfter !== "none" && r.createOnScreen === true);
    check("…and takes the sign-in pane away rather than stacking on it",
          r && r.signinAfter === "none", r && r.signinAfter);
    check("…with the email field on it", r && r.hasEmail === true);
    check("the painting does not move by a single pixel",
          r && r.artBefore && r.artAfter
            && Math.abs(r.artBefore.l - r.artAfter.l) < 0.5
            && Math.abs(r.artBefore.t - r.artAfter.t) < 0.5
            && Math.abs(r.artBefore.w - r.artAfter.w) < 0.5
            && Math.abs(r.artBefore.h - r.artAfter.h) < 0.5,
          r && JSON.stringify([r.artBefore, r.artAfter]));
    check("going back returns to the sign-in pane",
          r && r.backSignin !== "none" && r.backCreate === "none");
    check("…and the painting still has not moved",
          r && r.artBack && Math.abs(r.artBefore.l - r.artBack.l) < 0.5
            && Math.abs(r.artBefore.w - r.artBack.w) < 0.5);
  }
} finally {
  try { process.kill(server.pid); } catch (_) {}
  tmp.forEach(f => { try { fs.unlinkSync(f); } catch (_) {} });
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
