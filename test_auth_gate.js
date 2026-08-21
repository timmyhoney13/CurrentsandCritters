#!/usr/bin/env node
/* The way into the game — the two screens you click before you have an account.
 *
 * The bug that prompted this file, reported from the live game: "when I click
 * play, sometimes it immediately prompts me to put in a username, acting like I
 * already clicked sign in as guest — which I did not."
 *
 * Nothing in the source explains it. Every handler is wired correctly, every
 * selector is valid, and #auth-guest-overlay is opened from exactly one place:
 * a click on #auth-guest-btn. The bug is entirely in the GEOMETRY, and it only
 * shows up in a real browser:
 *
 *   • CHOOSE YOUR DEVICE paints a laptop and a phone in the middle of the
 *     screen, over two invisible half-width click targets.
 *   • The sign-in screen paints PLAY AS GUEST in the middle of the screen, over
 *     an invisible click target of its own.
 *   • They are the same picture size and the two hot zones OVERLAP. Choose your
 *     device and the pixel under your cursor silently becomes PLAY AS GUEST, so
 *     the second half of a double-click lands on it.
 *
 * The same hazard has a slower twin: login-bg.png is the only thing that makes
 * those invisible boxes findable, and until it paints the player is clicking a
 * button they cannot see.
 *
 * So this file measures, in headless Chrome with the real markup, the real CSS
 * and the real preview-app.js:
 *   1. that the overlap is real (it is what the guard exists for),
 *   2. that a double-click on the device screen no longer reaches the guest
 *      prompt,
 *   3. that the shield lets go again, so a deliberate click still works,
 *   4. that an invisible button is inert until its artwork is on screen,
 *   5. and that none of it can leave a player facing two dead buttons.
 *
 * Run:  node test_auth_gate.js        (needs Google Chrome / Chromium)
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const HTML = read("preview.html");
const DEV  = read("js/device-select.js");
const APP  = read("js/preview-app.js");
const CSS  = read("css/preview.css");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

// ════════════════════════════════════════════════════════════════════════
//  SOURCE — the guards have to be where they say they are
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe guards exist at all");
{
  check("the device gate raises a click shield when it closes",
        /raiseClickShield\(\);/.test(DEV) && /function raiseClickShield/.test(DEV));
  check("…as an element, so it can only ever swallow a HUMAN click",
        /document\.createElement\("div"\)/.test(DEV) && !/addEventListener\("click"[^)]*true\)/.test(DEV));
  check("…and it always takes itself back down",
        /sh\.parentNode\.removeChild\(sh\)/.test(DEV));
  check("the sign-in chooser is armed rather than born live",
        /function armChooseStep/.test(APP) && /armChooseStep\(\);/.test(APP));
  check("…the CSS is what actually holds the buttons back",
        /#auth-step-choose:not\(\.is-armed\) \.pv-btn/.test(CSS)
        && /#auth-step-choose:not\(\.is-armed\) \.auth-btn-google/.test(CSS));
  check("…it arms on a slow image rather than only on a cached one",
        /addEventListener\("load", settle/.test(APP));
  check("…and it fails OPEN on an image that never arrives",
        /addEventListener\("error", settle/.test(APP) && /setTimeout\(arm, 4000\)/.test(APP));
}

// ════════════════════════════════════════════════════════════════════════
//  DRIVE — the real screens, in a real browser
// ════════════════════════════════════════════════════════════════════════
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => fs.existsSync(p));

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found — skipping the drive half.");
} else {
  const PORT = 9231 + (process.pid % 400);
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

  // A click, delivered the way the browser delivers one: to whatever is on top
  // at that pixel. That is the whole point — an element sitting over the page
  // (the shield) and a `pointer-events:none` button (the unarmed chooser) are
  // both invisible to the eye but very visible to elementFromPoint, so this
  // models a real click far better than calling .click() on an element by id.
  const CLICK_AT = `
  function clickAt(x, y) {
    var el = document.elementFromPoint(x, y);
    var hit = el ? (el.id || el.className || el.tagName) : "none";
    while (el && typeof el.click !== "function") el = el.parentElement;
    if (el) el.click();
    return hit;
  }
  // Where the artwork actually lands. #cc-device-screen paints choose-device.png
  // with background-size:contain, so on any window that is not exactly 16:9 the
  // picture is letterboxed inside the viewport and a percentage of the WINDOW is
  // not a percentage of the PICTURE.
  function artBox() {
    var W = window.innerWidth, H = window.innerHeight, AR = 1672 / 941;
    if (W / H > AR) { var h = H, w = H * AR; return { x: (W - w) / 2, y: 0, w: w, h: h }; }
    var w2 = W, h2 = W / AR; return { x: 0, y: (H - h2) / 2, w: w2, h: h2 };
  }
  // Centres of the painted COMPUTER laptop and MOBILE phone, measured off
  // choose-device.png (1672x941).
  function laptopPt() { var a = artBox(); return [a.x + a.w * 0.425, a.y + a.h * 0.57]; }
  function phonePt()  { var a = artBox(); return [a.x + a.w * 0.598, a.y + a.h * 0.57]; }
  function guestOpen() {
    var o = document.getElementById("auth-guest-overlay");
    return !!o && getComputedStyle(o).display !== "none";
  }
  function armed() {
    var s = document.getElementById("auth-step-choose");
    return !!s && s.classList.contains("is-armed");
  }
  function chooserUp() {
    var s = document.getElementById("auth-step-choose");
    return !!s && getComputedStyle(s).display !== "none"
        && !document.getElementById("auth-screen").classList.contains("hidden");
  }
  `;

  // Scenario driver. Waits for the device screen, then runs `body`.
  function driver(body, opts) {
    const o = opts || {};
    return `
<div id="out">PENDING</div>
<script>
(function () {
  var st = document.createElement("style");
  st.textContent = "*,*::before,*::after{transition:none!important;animation:none!important}";
  document.head.appendChild(st);
  var log = {}, out = document.getElementById("out");
  function done() { out.textContent = JSON.stringify(log); }
  ${CLICK_AT}
  ${o.breakArt ? 'try { document.querySelector(".auth-step-choose-img").src = "/definitely-not-here.png"; } catch (e) {}' : ""}
  var tick = 0;
  var iv = setInterval(function () {
    if (++tick > 400) { log.fatal = "timeout"; done(); clearInterval(iv); return; }
    try {
      var spl = document.getElementById("cc-fs-splash");
      if (spl && getComputedStyle(spl).display !== "none") spl.style.display = "none";
      var dev = document.getElementById("cc-device-screen");
      if (!dev || getComputedStyle(dev).display === "none") return;
      // device-select.js is a DEFERRED script and this driver is not, so the
      // click targets are not live the moment the markup exists. ccGetDevice is
      // the flag that says its IIFE has run and the two halves are wired; under
      // --virtual-time-budget the clock outruns the network, and clicking
      // before this point just silently does nothing.
      if (typeof window.ccGetDevice !== "function") return;
      if (tick < 25) return;               // let the app finish booting
      clearInterval(iv);
      ${body}
    } catch (e) { log.err = String(e && e.message); done(); }
  }, 40);
})();
</script>`;
  }

  const tmp = [];
  function writeTmp(name, body) { const f = path.join(CLIENT, name); fs.writeFileSync(f, body); tmp.push(f); return name; }

  // `ok` says whether the run got far enough to be worth asserting on. A page
  // that never finished loading is a harness miss, not a finding, so it is
  // retried rather than reported.
  function run(name, body, w, h, opts, ok) {
    writeTmp(name, HTML + driver(body, opts));
    let last = null;
    for (let attempt = 0; attempt < 4; attempt++) {
      const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", `--window-size=${w},${h}`, "--virtual-time-budget=60000",
        "--dump-dom", `http://localhost:${PORT}/${name}?game_window=1`],
        { encoding: "utf8", maxBuffer: 64e6, stdio: ["ignore", "pipe", "ignore"] });
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
  try { execFileSync(process.execPath, ["-e", "setTimeout(()=>{},800)"]); } catch (_) {}

  const SIZES = [[1440, 900], [1920, 1080], [1280, 800], [1024, 768], [430, 932], [820, 1180]];

  try {
    // ── 1. The hazard the guard exists for ────────────────────────────────
    // If this ever stops being true the artwork has moved and the shield is
    // guarding nothing — worth knowing, and worth reading this file again.
    console.log("\nthe two screens really do overlap (this is the hazard)");
    for (const [w, h] of SIZES) {
      const r = run("_gate_overlap.html", `
        var p = laptopPt(), q = phonePt();
        log.beforeLaptop = (function(){var e=document.elementFromPoint(p[0],p[1]);return e?(e.id||e.className):"none";})();
        clickAt(p[0], p[1]);                       // choose Computer
        setTimeout(function () {
          // Look UNDER the shield: what would this pixel have hit without it?
          var sh = document.getElementById("cc-gate-shield");
          if (sh) sh.style.display = "none";
          var step = document.getElementById("auth-step-choose");
          if (step) step.classList.add("is-armed");   // and past the arming gate
          var a = document.elementFromPoint(p[0], p[1]);
          var b = document.elementFromPoint(q[0], q[1]);
          log.underLaptop = a ? (a.id || a.className) : "none";
          log.underPhone  = b ? (b.id || b.className) : "none";
          done();
        }, 60);
      `, w, h, null, (r) => r.underLaptop !== undefined && !/cc-device-half/.test(r.underLaptop));
      if (!r) { check(`${w}x${h}: the harness reached the device screen`, false); continue; }
      check(`${w}x${h}: the laptop is a device button before the choice`,
            /cc-device-computer/.test(r.beforeLaptop || ""), r.beforeLaptop);
      check(`${w}x${h}: …and PLAY AS GUEST is what sits under that same pixel after it`,
            r.underLaptop === "auth-guest-btn", r.underLaptop);
      check(`${w}x${h}: …the MOBILE phone lands on it too`,
            r.underPhone === "auth-guest-btn", r.underPhone);
    }

    // ── 2. The reported bug ───────────────────────────────────────────────
    console.log("\nchoosing a device never asks you for a guest nickname");
    for (const [w, h] of SIZES) {
      const r = run("_gate_double.html", `
        var p = laptopPt(), q = phonePt();
        clickAt(p[0], p[1]);                        // choose Computer
        setTimeout(function () {
          log.second = clickAt(p[0], p[1]);         // the other half of a double-click
          log.third  = clickAt(q[0], q[1]);         // …and an impatient one on the phone
          setTimeout(function () {
            log.guestPrompt = guestOpen();
            log.chooserUp = chooserUp();
            done();
          }, 40);
        }, 90);
      `, w, h, null, (r) => r.chooserUp === true);
      if (!r) { check(`${w}x${h}: the harness reached the device screen`, false); continue; }
      check(`${w}x${h}: a double-click on CHOOSE YOUR DEVICE does not open the guest prompt`,
            r.guestPrompt === false, `second click hit ${r.second}`);
      check(`${w}x${h}: …and the stray click was swallowed by the shield`,
            r.second === "cc-gate-shield", r.second);
      check(`${w}x${h}: …the player is left on the sign-in screen, as intended`,
            r.chooserUp === true);
    }

    // ── 3. The guard has to let go again ──────────────────────────────────
    console.log("\n…but the guards let go, so a deliberate click still works");
    {
      const r = run("_gate_release.html", `
        var p = laptopPt();
        clickAt(p[0], p[1]);
        setTimeout(function () {
          log.shieldGone = !document.getElementById("cc-gate-shield");
          log.armed = armed();
          var gb = document.getElementById("auth-guest-btn");
          var rc = gb.getBoundingClientRect();
          log.hit = clickAt(rc.left + rc.width / 2, rc.top + rc.height / 2);
          setTimeout(function () { log.guestPrompt = guestOpen(); done(); }, 40);
        }, 1600);
      `, 1440, 900, null, (r) => r.shieldGone === true && r.armed === true);
      check("the click shield takes itself back down", r && r.shieldGone === true);
      check("…the sign-in chooser arms itself once the artwork is up", r && r.armed === true);
      check("…a real click reaches PLAY AS GUEST", r && r.hit === "auth-guest-btn", r && r.hit);
      check("…and it opens the guest nickname prompt, which is the whole point",
            r && r.guestPrompt === true);
    }

    // ── 4. An invisible button is not clickable before you can see it ─────
    console.log("\nan invisible button is inert until its artwork is on screen");
    {
      const r = run("_gate_unarmed.html", `
        var p = laptopPt();
        clickAt(p[0], p[1]);
        setTimeout(function () {
          var sh = document.getElementById("cc-gate-shield");
          if (sh) sh.parentNode.removeChild(sh);      // take the shield out of it
          var step = document.getElementById("auth-step-choose");
          step.classList.remove("is-armed");          // …as if the art had not painted
          var gb = document.getElementById("auth-guest-btn");
          var rc = gb.getBoundingClientRect();
          log.hit = clickAt(rc.left + rc.width / 2, rc.top + rc.height / 2);
          setTimeout(function () { log.guestPrompt = guestOpen(); done(); }, 40);
        }, 1200);
      `, 1440, 900, null, (r) => r.hit !== undefined && !/cc-device-half/.test(r.hit));
      check("an unpainted chooser cannot be clicked through",
            r && r.hit !== "auth-guest-btn", r && r.hit);
      check("…so no guest prompt comes from a blind click", r && r.guestPrompt === false);
    }

    // ── 5. Fail open, always ──────────────────────────────────────────────
    console.log("\nnothing here can leave a player with two dead buttons");
    {
      const r = run("_gate_failopen.html", `
        var p = laptopPt();
        clickAt(p[0], p[1]);
        setTimeout(function () {
          log.armed = armed();
          var gb = document.getElementById("auth-guest-btn");
          var rc = gb.getBoundingClientRect();
          log.hit = clickAt(rc.left + rc.width / 2, rc.top + rc.height / 2);
          setTimeout(function () { log.guestPrompt = guestOpen(); done(); }, 40);
        }, 5200);
      `, 1440, 900, { breakArt: true }, (r) => r.hit !== undefined && !/cc-device-half/.test(r.hit));
      check("an artwork that never loads still arms the buttons",
            r && r.armed === true);
      check("…and they work", r && r.guestPrompt === true, r && r.hit);
    }
  } finally {
    server.kill();
    tmp.forEach((f) => { try { fs.unlinkSync(f); } catch (_) {} });
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
