#!/usr/bin/env node
/* The rotted pier piling.
 *
 * login-bg.png is one flat painting of eight oceans. In the pier, bottom left,
 * one plank is rust red among the blues and violets. Clicking that plank is
 * worth the Pier background.
 *
 * The interesting part is WHEN a player is allowed to find it: on the sign-in
 * screen, before anybody knows who they are. So the find cannot BE the reward.
 * It is a note on the device, and it is spent the first moment there is an
 * account to spend it on. That is the whole design, and it is what this file
 * pins:
 *
 *   1. THE BOX IS ON THE PLANK. Not near it, not over the pier in general: the
 *      pixel under the middle of the box is read back out of the artwork and
 *      has to be red. An art rebuild that moves the pier moves this test.
 *   2. IT IS INVISIBLE, SO IT IS GATED. Nothing on that screen may take a click
 *      before the artwork has painted, or the click is a stray one.
 *   3. IT SAYS BOTH THINGS: what you found, and what it is worth.
 *   4. NOBODY IS GRANTED ANYTHING ON THE SIGN-IN SCREEN, because there is
 *      nobody there. The find goes to localStorage.
 *   5. IT IS SPENT ON ONE ACCOUNT. revealLobby() is the one place both roads in
 *      end (signing into an old account, and finishing a new one), so the claim
 *      hangs there, and it writes to THAT account's unlocked_backgrounds.
 *   6. A FAILED WRITE KEEPS THE NOTE. Somebody who found it, read the message
 *      and lost their connection must not lose the piling with it.
 *   7. THE REWARD IS A REAL BACKGROUND, in the registry the gallery renders
 *      from and in the server's list of the eight.
 *
 * Run:  node test_pier_piling.js        (needs Google Chrome / Chromium)
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
const PY   = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra != null ? "  → " + extra : "")); }
}

// ══════════════════════════════════════════════════════════════════════════
//  SOURCE
// ══════════════════════════════════════════════════════════════════════════

console.log("\nthere is a plank on the pier, and it is a real button");
{
  check("the box exists on the sign-in screen",
        /id="auth-pier-secret" class="auth-pier-secret"/.test(HTML));
  check("…as a button, so it is reachable by keyboard and announced as one",
        /<button type="button" id="auth-pier-secret"/.test(HTML));
  check("…with a name, since it has no lettering of its own to be read",
        /aria-label="A rotted piling on the pier"/.test(HTML));
  check("it is positioned off the artwork's own coordinates, not eyeballed",
        /#auth-step-choose > \.auth-pier-secret \{[\s\S]{0,400}?left: 20\.63%;[\s\S]{0,200}?top:  76\.41%;/.test(CSS));
  check("…and keeps a minimum size, so a small window still leaves it tappable",
        /#auth-step-choose > \.auth-pier-secret \{[\s\S]{0,400}?width:  max\(26px,/.test(CSS));
  check("a cursor that wanders onto it is told it is on something",
        /#auth-step-choose > \.auth-pier-secret:hover,[\s\S]{0,300}?box-shadow:/.test(CSS));
  // Every box on this screen is invisible paint-over, so all of them wait.
  check("it cannot take a click before the artwork has painted",
        /#auth-step-choose:not\(\.is-armed\) > \.pv-btn,[\s\S]{0,300}?> \.auth-pier-secret \{ pointer-events: none; \}/.test(CSS));
  check("it is gone on a phone, where the pier is cropped off the screen",
        /@media \(max-aspect-ratio: 4\/5\)[\s\S]{0,2000}?#auth-step-choose > \.auth-pier-secret \{ display: none; \}/.test(CSS));
}

console.log("\nit says what you found, and what it is worth");
{
  check("both halves are in the one sentence",
        /You found the rotted pier piling! Sign in or create an account and the Pier background is yours\./.test(APP));
  check("…said through the chooser's own note, in the good-news tone",
        /setAuthMsg\("auth-choose-err",\s*\n\s*"You found the rotted pier piling![\s\S]{0,140}?"ok"\);/.test(APP));
  check("finding it is what puts the message up",
        /_pierBtn\.addEventListener\("click", \(\) => \{\s*\n\s*ccPierNoteFind\(\);/.test(APP));
}

console.log("\nnothing is granted on the sign-in screen, because nobody is there");
{
  check("the find is a note on the device",
        /const PIER_FIND_KEY = "cc_pier_piling_found_v1";/.test(APP)
        && /function ccPierNoteFind\(\) \{ try \{ localStorage\.setItem\(PIER_FIND_KEY, "1"\); \}/.test(APP));
  // The click handler must not reach for Firestore: on this screen there is no
  // account to write to, and a grant that ran here would belong to nobody.
  const CLICK = APP.slice(APP.indexOf('const _pierBtn = $a("auth-pier-secret");'),
                          APP.indexOf('const _pierBtn = $a("auth-pier-secret");') + 500);
  check("…and nothing else, no write, no grant",
        !/unlocked_backgrounds/.test(CLICK) && !/_db/.test(CLICK));
}

console.log("\nit is spent on ONE account, the first one to turn up");
{
  check("the claim hangs on revealLobby, the one place both roads in end",
        /function revealLobby[\s\S]{0,3000}?try \{ ccPierClaim\(\)\.catch\(\(\) => \{\}\); \} catch \(_\) \{\}/.test(APP));
  check("…on the account side of it, not the guest side",
        /if \(_authUser && _playerNickname\) \{[\s\S]{0,2200}?ccPierClaim\(\)\.catch/.test(APP));
  const CLAIM = APP.slice(APP.indexOf("async function ccPierClaim()"),
                          APP.indexOf('const _pierBtn = $a("auth-pier-secret");'));
  check("it does nothing at all without a find to spend",
        /if \(!ccPierFound\(\)\) return;/.test(CLAIM));
  check("…or without an account and a database to spend it on",
        /if \(!_authUser \|\| !_db\) return;/.test(CLAIM));
  // Viewing another player's collection swaps their unlocks into the globals;
  // a grant run in that state is written to MY account for THEIR gallery.
  check("…or while another player's collection is loaded",
        /if \(_galReadOnly\) return;/.test(CLAIM));
  check("it is written to THIS account's document, by uid",
        /_db\.collection\("users"\)\.doc\(_authUser\.uid\)\.update\(\{[\s\S]{0,200}?unlocked_backgrounds: firebase\.firestore\.FieldValue\.arrayUnion\(path\)/.test(CLAIM));
  check("…so it is one account's unlock, never a device-wide or global one",
        !/collection\("users"\)\.get\(\)/.test(CLAIM) && !/ALL_BACKGROUND/.test(CLAIM));
  check("an account that already owns the Pier just spends the note quietly",
        /_unlockedBackgrounds\.includes\(path\)\) \{\s*\n\s*try \{ localStorage\.removeItem\(PIER_FIND_KEY\)/.test(CLAIM));
  check("…and one that did not is told, once it is really theirs",
        /showToast\(/.test(CLAIM));
}

console.log("\na failed write keeps the note, so the find is not lost with the wifi");
{
  const CLAIM = APP.slice(APP.indexOf("async function ccPierClaim()"),
                          APP.indexOf('const _pierBtn = $a("auth-pier-secret");'));
  const catchIdx = CLAIM.indexOf("} catch (_) {");
  const clearIdx = CLAIM.lastIndexOf("localStorage.removeItem(PIER_FIND_KEY)");
  check("the write is awaited, so a failure is a failure and not a shrug",
        /await _db\.collection\("users"\)/.test(CLAIM));
  check("…and the note is cleared only AFTER it, never before",
        catchIdx > 0 && clearIdx > catchIdx,
        "clearing first would spend the find on a write that never landed");
  check("…the catch returns rather than falling through to the clear",
        /\} catch \(_\) \{\s*\n\s*return;/.test(CLAIM));
}

console.log("\nthe Pier is a real background, in both lists that have to know");
{
  check("it is in the gallery's registry",
        /\{ id:"bg-pier",[\s\S]{0,160}?img:"\/backgrounds\/bg-pier\.png"/.test(APP));
  check("…and in the server's list of the eight",
        /"\/backgrounds\/bg-pier\.png",/.test(PY));
  check("…and the art is actually on disk",
        fs.existsSync(path.join(CLIENT, "backgrounds/bg-pier.png")));
  check("the claim asks for that exact path",
        /const PIER_BG_IMG   = "\/backgrounds\/bg-pier\.png";/.test(APP));
}

// ══════════════════════════════════════════════════════════════════════════
//  DRIVE  (a real browser, reading the artwork back out of a canvas)
// ══════════════════════════════════════════════════════════════════════════
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

  function driver(body) {
    return `
<div id="out">PENDING</div>
<script>
(function () {
  var st = document.createElement("style");
  st.textContent = "*,*::before,*::after{transition:none!important;animation:none!important}";
  document.head.appendChild(st);
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
      try { localStorage.removeItem("cc_pier_piling_found_v1"); } catch (e) {}
      ${body}
    } catch (e) { log.err = String(e && e.message); done(); }
  }, 40);
})();
</script>`;
  }

  const tmp = [];
  function run(name, body, w, h, ok) {
    const f = path.join(CLIENT, name);
    fs.writeFileSync(f, HTML + driver(body));
    if (!tmp.includes(f)) tmp.push(f);
    let last = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      let dom = "";
      try {
        dom = execFileSync(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
          "--hide-scrollbars", `--window-size=${w},${h}`, "--virtual-time-budget=30000",
          "--dump-dom", `http://localhost:${PORT}/${name}?game_window=1`],
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

  const SIZES = [[1440, 900], [1920, 1080], [1280, 800], [1024, 768], [900, 520]];

  try {
    // ── 1. The pixel under the box is red ─────────────────────────────────
    // The box is measured against the ARTWORK, and then the artwork is read
    // back: the middle of the box is converted into image coordinates and that
    // pixel is sampled off a canvas. Nothing here trusts a number in the CSS.
    console.log("\nthe box is on the red plank, read back out of the painting");
    for (const [w, h] of SIZES) {
      const r = run("_pier_hit.html", `
        var img = document.querySelector(".auth-step-choose-img");
        var box = R("#auth-pier-secret");
        var ir  = R(".auth-step-choose-img");
        var c = document.createElement("canvas");
        c.width = img.naturalWidth; c.height = img.naturalHeight;
        c.getContext("2d").drawImage(img, 0, 0);
        log.nat = { w: img.naturalWidth, h: img.naturalHeight };
        // Where the middle of the box lands in the painting's own pixels.
        var ax = Math.round((box.l + box.w / 2 - ir.l) / ir.w * img.naturalWidth);
        var ay = Math.round((box.t + box.h / 2 - ir.t) / ir.h * img.naturalHeight);
        log.art = { x: ax, y: ay };
        var d = c.getContext("2d").getImageData(ax, ay, 1, 1).data;
        log.px = { r: d[0], g: d[1], b: d[2] };
        log.box = box;
        done();
      `, w, h, (r) => r.px);
      if (!r || !r.px) { check(`${w}x${h}: the harness could read the artwork`, false, r && (r.err || r.fatal)); continue; }
      check(`${w}x${h}: the middle of the box is a rust-red pixel, not blue water`,
            r.px.r > 120 && r.px.r - r.px.g > 30 && r.px.r - r.px.b > 30,
            `rgb(${r.px.r},${r.px.g},${r.px.b}) at art ${r.art.x},${r.art.y}`);
      check(`${w}x${h}: …and that is a point on the pier, not somewhere else`,
            r.art.x > 290 && r.art.x < 350 && r.art.y > 720 && r.art.y < 825,
            `art ${r.art.x},${r.art.y}`);
      check(`${w}x${h}: it is big enough to hit`, r.box.w >= 20 && r.box.h >= 30,
            `${Math.round(r.box.w)}x${Math.round(r.box.h)}`);
    }

    // ── 2. Clicking it says both things, and remembers ────────────────────
    console.log("\nclicking it says both things, and the find survives the click");
    {
      const r = run("_pier_click.html", `
        setTimeout(function () {
          var note = document.getElementById("auth-choose-err");
          // The harness has no Firebase behind it, so the screen may already be
          // saying something about that. Clear it through the real renderer
          // first: what is being measured is the click, not the environment.
          log.saidAtLoad = note.textContent.trim();
          window.__ccAuthNote("", "ok");
          log.before = note.classList.contains("is-on");
          log.storedBefore = localStorage.getItem("cc_pier_piling_found_v1");
          document.getElementById("auth-pier-secret").click();
          log.after = note.classList.contains("is-on");
          log.tone  = note.classList.contains("is-ok");
          log.said  = note.textContent.trim();
          log.stored = localStorage.getItem("cc_pier_piling_found_v1");
          done();
        }, 900);
      `, 1440, 900, (r) => r.after !== undefined);
      check("the note is quiet until the plank is found", r && r.before === false,
            r && ("it was saying: " + r.saidAtLoad));
      check("…and up once it is", r && r.after === true);
      check("…in the good-news tone, not the one used for a failed sign-in",
            r && r.tone === true);
      check("it says what was found", r && /found the rotted pier piling/i.test(r.said || ""));
      check("…and what it is worth, and how to get it",
            r && /sign in or create an account/i.test(r.said || "")
              && /pier background/i.test(r.said || ""));
      check("the find is written down, because there is nobody to give it to yet",
            r && r.stored === "1", r && String(r.stored));
    }

    // ── 3. Not on a phone, where the pier is off the screen ───────────────
    console.log("\non a phone the pier is cropped away, and so is the plank");
    {
      const r = run("_pier_phone.html", `
        var e = document.getElementById("auth-pier-secret");
        log.display = getComputedStyle(e).display;
        log.box = R("#auth-pier-secret");
        log.img = R(".auth-step-choose-img");
        done();
      `, 500, 900, (r) => r.display);
      check("the plank is not on the screen at all",
            r && r.display === "none", r && r.display);
      check("…and neither is the pier it is painted on",
            r && r.img && r.img.h < r.img.w * (1011 / 1556) * 0.9,
            r && r.img && `strip ${Math.round(r.img.h)} of ${Math.round(r.img.w * 1011 / 1556)}`);
    }
  } finally {
    try { process.kill(server.pid); } catch (_) {}
    tmp.forEach(f => { try { fs.unlinkSync(f); } catch (_) {} });
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
