#!/usr/bin/env node
/* The way into the game: the one screen you click before you have an account.
 *
 * There used to be two, and the pair of them caused the reported bug: "when I
 * click play, sometimes it immediately prompts me to put in a username, acting
 * like I already clicked sign in as guest, which I did not."
 *
 *   • CHOOSE YOUR DEVICE painted a laptop and a phone over two invisible
 *     half-width click targets.
 *   • The sign-in screen paints PLAY AS GUEST over an invisible click target of
 *     its own, in the SAME place.
 *   • So dismissing the first screen left the cursor on the second one's button,
 *     and the second half of a double-click opened the guest prompt.
 *
 * THE DEVICE SCREEN IS NOW GONE (js/device-select.js detects the device from
 * real input instead), which retires that whole class of bug rather than
 * guarding it: there is no longer a screen to click through, so there is no
 * click to land on what comes next. The click shield that used to absorb it is
 * gone with it. This file now pins that it STAYS gone, and keeps the half of
 * the hazard that is still real:
 *
 *   login-bg.png is the only thing that makes the sign-in screen's invisible
 *   boxes findable, so until it paints, the player is clicking a button they
 *   cannot see. #auth-step-choose is held inert until the artwork is up, and
 *   fails OPEN if it never arrives, so nobody is ever left with dead buttons.
 *
 * Measured in headless Chrome against the real markup, CSS and preview-app.js.
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
//  SOURCE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe screen that caused the collision is gone");
{
  check("no device screen in the markup", !/id="cc-device-screen"/.test(HTML));
  check("no invisible device halves", !/cc-device-half/.test(HTML + CSS));
  check("no click shield, because there is no longer a click to swallow",
        !/cc-gate-shield/.test(DEV + APP + CSS));
  check("the device is detected instead of asked",
        /function guess\(\)/.test(DEV) && /pointerType/.test(DEV));
  check("…and nothing blocks the boot waiting for an answer",
        /window\.ccDeviceReady = Promise\.resolve/.test(DEV));
}

console.log("\nthe guard that is still needed is still there");
{
  check("the sign-in chooser is armed rather than born live",
        /function armChooseStep/.test(APP) && /armChooseStep\(\);/.test(APP));
  check("…the CSS is what actually holds the buttons back",
        /#auth-step-choose:not\(\.is-armed\) > \.pv-btn/.test(CSS)
        && /#auth-step-choose:not\(\.is-armed\) > \.auth-btn-google/.test(CSS));
  // > and not a descendant selector. The guest card is also a child of this
  // step, and these rules strip a button of its colour and its background: a
  // descendant selector reached into the card and made Dive In invisible.
  check("…and it holds back the step's OWN buttons, not the guest card's",
        !/#auth-step-choose(:not\(\.is-armed\))? \.pv-btn\b/.test(CSS)
        && !/#auth-step-choose \.auth-btn-google\b/.test(CSS));
  check("…it arms on a slow image rather than only on a cached one",
        /addEventListener\("load", settle/.test(APP));
  check("…and it fails OPEN on an image that never arrives",
        /addEventListener\("error", settle/.test(APP) && /setTimeout\(arm, 4000\)/.test(APP));
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

  // A click delivered the way the browser delivers one: to whatever is on top
  // at that pixel. A `pointer-events:none` button (the unarmed chooser) is
  // invisible to the eye but very visible to elementFromPoint, so this models a
  // real click far better than calling .click() on an element by id.
  const HELPERS = `
  function clickAt(x, y) {
    var el = document.elementFromPoint(x, y);
    var hit = el ? (el.id || el.className || el.tagName) : "none";
    while (el && typeof el.click !== "function") el = el.parentElement;
    if (el) el.click();
    return hit;
  }
  function guestOpen() {
    var o = document.getElementById("auth-guest-overlay");
    return !!o && getComputedStyle(o).display !== "none";
  }
  function armed() {
    var s = document.getElementById("auth-step-choose");
    return !!s && s.classList.contains("is-armed");
  }
  function guestBtnPt() {
    var gb = document.getElementById("auth-guest-btn");
    var rc = gb.getBoundingClientRect();
    return [rc.left + rc.width / 2, rc.top + rc.height / 2];
  }
  `;

  // Waits for the sign-in chooser to be on screen, then runs `body`.
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
  ${HELPERS}
  ${o.breakArt ? 'try { document.querySelector(".auth-step-choose-img").src = "/definitely-not-here.png"; } catch (e) {}' : ""}
  var tick = 0;
  var iv = setInterval(function () {
    if (++tick > 400) { log.fatal = "timeout"; done(); clearInterval(iv); return; }
    try {
      var spl = document.getElementById("cc-fs-splash");
      if (spl && getComputedStyle(spl).display !== "none") spl.style.display = "none";
      // Firebase resolves over the network; the sign-in screen must not wait
      // on it here or the clock outruns the request under virtual time.
      if (tick > 20) {
        var ls = document.getElementById("auth-loading-screen");
        if (ls) ls.classList.add("hidden");
        var as = document.getElementById("auth-screen");
        if (as) as.classList.remove("hidden");
      }
      var step = document.getElementById("auth-step-choose");
      if (!step || getComputedStyle(step).display === "none") return;
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
  // that never finished loading is a harness miss, not a finding.
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
    // ── 1. Nothing stands in front of the sign-in screen any more ─────────
    // The old bug needed something ELSE to be on top of PLAY AS GUEST first.
    // Measured at every size: the first screen a player meets is the sign-in
    // screen, and the pixel over its painted button belongs to that button.
    console.log("\nthe sign-in screen is the first thing there is");
    for (const [w, h] of SIZES) {
      const r = run("_gate_first.html", `
        log.deviceScreen = !!document.getElementById("cc-device-screen");
        log.shield = !!document.getElementById("cc-gate-shield");
        log.device = window.CC_DEVICE || "";
        var p = guestBtnPt();
        log.overGuestBtn = (function(){var e=document.elementFromPoint(p[0],p[1]);return e?(e.id||e.className):"none";})();
        log.guestPrompt = guestOpen();
        done();
      `, w, h, null, (r) => r.overGuestBtn !== undefined);
      if (!r) { check(`${w}x${h}: the harness reached the sign-in screen`, false); continue; }
      check(`${w}x${h}: no device screen exists to be clicked through`,
            r.deviceScreen === false);
      check(`${w}x${h}: no click shield is needed either`, r.shield === false);
      check(`${w}x${h}: a device was decided without asking`,
            r.device === "computer" || r.device === "mobile", r.device);
      check(`${w}x${h}: nothing covers PLAY AS GUEST`,
            r.overGuestBtn === "auth-guest-btn", r.overGuestBtn);
      check(`${w}x${h}: and nobody has been asked for a nickname yet`,
            r.guestPrompt === false);
    }

    // ── 2. A deliberate click works ───────────────────────────────────────
    console.log("\na real click reaches PLAY AS GUEST");
    {
      const r = run("_gate_click.html", `
        setTimeout(function () {
          log.armed = armed();
          var p = guestBtnPt();
          log.hit = clickAt(p[0], p[1]);
          setTimeout(function () { log.guestPrompt = guestOpen(); done(); }, 40);
        }, 1600);
      `, 1440, 900, null, (r) => r.armed === true);
      check("the sign-in chooser arms itself once the artwork is up", r && r.armed === true);
      check("…a real click reaches PLAY AS GUEST", r && r.hit === "auth-guest-btn", r && r.hit);
      check("…and it opens the guest nickname prompt, which is the whole point",
            r && r.guestPrompt === true);
    }

    // ── 3. An invisible button is not clickable before you can see it ─────
    console.log("\nan invisible button is inert until its artwork is on screen");
    {
      const r = run("_gate_unarmed.html", `
        setTimeout(function () {
          var step = document.getElementById("auth-step-choose");
          step.classList.remove("is-armed");          // as if the art had not painted
          var p = guestBtnPt();
          log.hit = clickAt(p[0], p[1]);
          setTimeout(function () { log.guestPrompt = guestOpen(); done(); }, 40);
        }, 1200);
      `, 1440, 900, null, (r) => r.hit !== undefined);
      check("an unpainted chooser cannot be clicked through",
            r && r.hit !== "auth-guest-btn", r && r.hit);
      check("…so no guest prompt comes from a blind click", r && r.guestPrompt === false);
    }

    // ── 4. Fail open, always ──────────────────────────────────────────────
    console.log("\nnothing here can leave a player with two dead buttons");
    {
      const r = run("_gate_failopen.html", `
        setTimeout(function () {
          log.armed = armed();
          var p = guestBtnPt();
          log.hit = clickAt(p[0], p[1]);
          setTimeout(function () { log.guestPrompt = guestOpen(); done(); }, 40);
        }, 5200);
      `, 1440, 900, { breakArt: true }, (r) => r.hit !== undefined);
      check("an artwork that never loads still arms the buttons", r && r.armed === true);
      check("…and they work", r && r.guestPrompt === true, r && r.hit);
    }
  } finally {
    server.kill();
    tmp.forEach((f) => { try { fs.unlinkSync(f); } catch (_) {} });
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
