#!/usr/bin/env node
/* The staghorn coral.
 *
 * The sign-in painting is one picture of kelp water. In the kelp, bottom left,
 * a staghorn coral stands warm rose among the blues and greens. Clicking that
 * coral is worth the Coral Reef background.
 *
 * The interesting part is WHEN a player is allowed to find it: on the sign-in
 * screen, before anybody knows who they are. So the find cannot BE the reward.
 * It is a note on the device, and it is spent the first moment there is an
 * account to spend it on. That is the whole design, and it is what this file
 * pins:
 *
 *   1. THE BOX IS ON THE CORAL. Not near it, not over the kelp in general: the
 *      artwork under the box is read back out of a canvas, and the coral has to
 *      be under it and centred in it. An art rebuild that moves the colony
 *      moves this test.
 *   2. IT IS INVISIBLE, SO IT IS GATED. Nothing on that screen may take a click
 *      before the artwork has painted, or the click is a stray one.
 *   3. IT SAYS BOTH THINGS: what you found, and what it is worth.
 *   4. NOBODY IS GRANTED ANYTHING ON THE SIGN-IN SCREEN, because there is
 *      nobody there. The find goes to localStorage.
 *   5. IT IS SPENT ON ONE ACCOUNT. revealLobby() is the one place both roads in
 *      end (signing into an old account, and finishing a new one), so the claim
 *      hangs there, and it writes to THAT account's unlocked_backgrounds.
 *   6. A FAILED WRITE KEEPS THE NOTE. Somebody who found it, read the message
 *      and lost their connection must not lose the coral with it.
 *   7. THE REWARD IS A REAL BACKGROUND, in the registry the gallery renders
 *      from and in the server's list of the eight.
 *
 * (This replaced the rotted pier piling in 1.6.97: same machinery, a coral
 * instead of a plank, and the Coral Reef background instead of the Pier.)
 *
 * Run:  node test_coral_secret.js       (needs Google Chrome / Chromium)
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

console.log("\nthere is a coral in the kelp, and it is a real button");
{
  check("the box exists on the sign-in screen",
        /id="auth-coral-secret" class="auth-coral-secret"/.test(HTML));
  check("…as a button, so it is reachable by keyboard and announced as one",
        /<button type="button" id="auth-coral-secret"/.test(HTML));
  check("…with a name, since it has no lettering of its own to be read",
        /aria-label="A staghorn coral in the kelp"/.test(HTML));
  // It lives inside the panel, not beside it: on the phone layout the screen
  // is a scrolling page, and a box placed against the page slides off the
  // picture the moment anybody scrolls.
  check("it lives inside the painting's own panel, so scrolling cannot move it",
        /<div class="ao-art">[\s\S]{0,1400}?<button type="button" id="auth-coral-secret"[\s\S]{0,200}?<\/div>/.test(HTML));
  // The art is object-fit:contain now, so how big the painting is drawn
  // changes with every window shape. CSS cannot read that fit, so the box is
  // measured off the image itself.
  check("it is placed from the painting's own geometry, not eyeballed in CSS",
        /const CORAL_SPOT = \{ x: \.126, y: \.952/.test(APP)
        && /function placeCoralBox\(\)/.test(APP));
  check("…measured as CONTAINED art: the smaller ratio, not the larger",
        /const sc = Math\.min\(ir\.width \/ img\.naturalWidth, ir\.height \/ img\.naturalHeight\);/.test(APP));
  check("…against the panel it sits in, not the page that scrolls",
        /const ir = img\.getBoundingClientRect\(\), pr = art\.getBoundingClientRect\(\);/.test(APP));
  check("…and it is placed again when the window changes shape",
        /window\.addEventListener\("resize", placeCoralBox\)/.test(APP)
        && /step\.classList\.add\("is-armed"\);\s*\n\s*placeCoralBoxSoon\(\);/.test(APP));
  // naturalWidth is 0 until the art has DECODED, not merely arrived. Asking
  // once, on arming, can lose that race, and losing it leaves the box on its
  // CSS fallback: the corner, the wrong pixels.
  check("…and it keeps asking until the art has decoded",
        /function placeCoralBoxSoon\(tries\)/.test(APP)
        && /_coralPlaceTimer = setTimeout\(\(\) => placeCoralBoxSoon\(left\), 120\)/.test(APP)
        && /img\.addEventListener\("load", \(\) => \{ placeCoralBoxSoon\(\); settle\(\); \}/.test(APP));
  check("…keeping a minimum size, so a small window still leaves it tappable",
        /Math\.max\(40, CORAL_SPOT\.w/.test(APP) && /Math\.max\(30, CORAL_SPOT\.h/.test(APP));
  check("…and it never guesses at art that has not decoded yet",
        /if \(!step \|\| !btn \|\| !img \|\| !art \|\| !img\.naturalWidth\) return false;/.test(APP));
  check("a cursor that wanders onto it is told it is on something",
        /#auth-step-choose \.ao-art > \.auth-coral-secret:hover,[\s\S]{0,300}?box-shadow:/.test(CSS));
  // Every box on this screen is invisible paint-over, so all of them wait.
  check("it cannot take a click before the artwork has painted",
        /#auth-step-choose:not\(\.is-armed\) > \.ao-form,[\s\S]{0,200}?\.auth-coral-secret \{ pointer-events: none; \}/.test(CSS));
  check("the plank it replaced is gone from every file that knew about it",
        !/auth-pier-secret|PIER_SPOT|ccPierClaim|cc_pier_piling_found/.test(HTML + CSS + APP));
}

console.log("\nit says what you found, and what it is worth");
{
  check("both halves are in the one sentence",
        /You found the staghorn coral! Sign in or create an account and the Coral Reef background is yours\./.test(APP));
  check("…said through the chooser's own note, in the good-news tone",
        /setAuthMsg\("auth-choose-err",\s*\n\s*"You found the staghorn coral![\s\S]{0,160}?"ok"\);/.test(APP));
  check("finding it is what puts the message up",
        /_coralBtn\.addEventListener\("click", \(\) => \{\s*\n\s*ccCoralNoteFind\(\);/.test(APP));
}

console.log("\nnothing is granted on the sign-in screen, because nobody is there");
{
  check("the find is a note on the device",
        /const CORAL_FIND_KEY = "cc_staghorn_coral_found_v1";/.test(APP)
        && /function ccCoralNoteFind\(\) \{ try \{ localStorage\.setItem\(CORAL_FIND_KEY, "1"\); \}/.test(APP));
  // The click handler must not reach for Firestore: on this screen there is no
  // account to write to, and a grant that ran here would belong to nobody.
  const at = APP.indexOf('const _coralBtn = $a("auth-coral-secret");');
  const CLICK = APP.slice(at, at + 500);
  check("…and nothing else, no write, no grant",
        !/unlocked_backgrounds/.test(CLICK) && !/_db/.test(CLICK));
}

console.log("\nit is spent on ONE account, the first one to turn up");
{
  check("the claim hangs on revealLobby, the one place both roads in end",
        /function revealLobby[\s\S]{0,3000}?try \{ ccCoralClaim\(\)\.catch\(\(\) => \{\}\); \} catch \(_\) \{\}/.test(APP));
  check("…on the account side of it, not the guest side",
        /if \(_authUser && _playerNickname\) \{[\s\S]{0,2200}?ccCoralClaim\(\)\.catch/.test(APP));
  const CLAIM = APP.slice(APP.indexOf("async function ccCoralClaim()"),
                          APP.indexOf('const _coralBtn = $a("auth-coral-secret");'));
  check("it does nothing at all without a find to spend",
        /if \(!ccCoralFound\(\)\) return;/.test(CLAIM));
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
  check("an account that already owns the reef just spends the note quietly",
        /_unlockedBackgrounds\.includes\(path\)\) \{\s*\n\s*try \{ localStorage\.removeItem\(CORAL_FIND_KEY\)/.test(CLAIM));
  check("…and one that did not is told, once it is really theirs",
        /showToast\(/.test(CLAIM));
}

console.log("\na failed write keeps the note, so the find is not lost with the wifi");
{
  const CLAIM = APP.slice(APP.indexOf("async function ccCoralClaim()"),
                          APP.indexOf('const _coralBtn = $a("auth-coral-secret");'));
  const catchIdx = CLAIM.indexOf("} catch (_) {");
  const clearIdx = CLAIM.lastIndexOf("localStorage.removeItem(CORAL_FIND_KEY)");
  check("the write is awaited, so a failure is a failure and not a shrug",
        /await _db\.collection\("users"\)/.test(CLAIM));
  check("…and the note is cleared only AFTER it, never before",
        catchIdx > 0 && clearIdx > catchIdx,
        "clearing first would spend the find on a write that never landed");
  check("…the catch returns rather than falling through to the clear",
        /\} catch \(_\) \{\s*\n\s*return;/.test(CLAIM));
}

console.log("\nthe Coral Reef is a real background, in both lists that have to know");
{
  check("it is in the gallery's registry",
        /\{ id:"bg-coral-reef",[\s\S]{0,160}?img:"\/backgrounds\/bg-coral-reef\.png"/.test(APP));
  check("…and in the server's list of the eight",
        /"\/backgrounds\/bg-coral-reef\.png",/.test(PY));
  check("…and the art is actually on disk",
        fs.existsSync(path.join(CLIENT, "backgrounds/bg-coral-reef.png")));
  check("the claim asks for that exact path",
        /const CORAL_BG_IMG   = "\/backgrounds\/bg-coral-reef\.png";/.test(APP));
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
      try { localStorage.removeItem("cc_staghorn_coral_found_v1"); } catch (e) {}
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

  // Five window shapes, including a phone: the box has to land on the coral in
  // all of them, which is the whole reason it is measured and not written down.
  const SIZES = [[1440, 900], [1920, 1080], [1280, 800], [1024, 768], [430, 900]];

  // The reading half, shared by every size. The box is measured against the
  // ARTWORK and then the artwork is read back: the box rectangle is converted
  // into image coordinates and those pixels are sampled off a canvas.
  const READ = `
    var img = document.querySelector(".ao-art-img");
    var box = R("#auth-coral-secret");
    var ir  = R(".ao-art-img");
    var c = document.createElement("canvas");
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    var cx2 = c.getContext("2d");
    cx2.drawImage(img, 0, 0);
    log.nat = { w: img.naturalWidth, h: img.naturalHeight };
    // object-fit: contain. The painting is scaled to FIT its box and centred
    // in it, so a screen pixel maps back through the smaller ratio and the
    // letterbox that leaves.
    var sc = Math.min(ir.w / img.naturalWidth, ir.h / img.naturalHeight);
    var ox = ir.l + (ir.w - img.naturalWidth  * sc) / 2;
    var oy = ir.t + (ir.h - img.naturalHeight * sc) / 2;
    var ax = Math.round((box.l - ox) / sc), ay = Math.round((box.t - oy) / sc);
    var aw = Math.max(2, Math.round(box.w / sc)), ah = Math.max(2, Math.round(box.h / sc));
    log.art = { x: ax, y: ay, w: aw, h: ah };
    log.box = box;
    var d = cx2.getImageData(ax, ay, aw, ah).data;
    // Coral is the one warm thing down there: everything else is blue water or
    // green kelp, and both have more blue in them than red.
    var warm = 0, sx = 0, sy = 0, n = 0;
    for (var i = 0; i < d.length; i += 4) {
      var p = i / 4, px = p % aw, py = (p / aw) | 0;
      if (d[i] - d[i + 2] > 10 && d[i] > 90) { warm++; sx += px; sy += py; }
      n++;
    }
    log.warm = warm / Math.max(1, n);
    log.cx = warm ? (sx / warm) / aw : -1;
    log.cy = warm ? (sy / warm) / ah : -1;
  `;

  try {
    console.log("\nthe box is on the coral, read back out of the painting");
    for (const [w, h] of SIZES) {
      const r = run("_coral_hit.html", READ + "done();", w, h, (r) => r.warm != null);
      if (!r || r.warm == null) { check(`${w}x${h}: the harness could read the artwork`, false, r && (r.err || r.fatal)); continue; }
      check(`${w}x${h}: the box is over the coral, not open water`,
            r.warm > 0.07, `only ${(r.warm * 100).toFixed(1)}% of it is coral`);
      check(`${w}x${h}: …and the coral is CENTRED in it, not clipped by one edge`,
            r.cx > 0.32 && r.cx < 0.68 && r.cy > 0.30 && r.cy < 0.70,
            `centre of mass at ${r.cx.toFixed(2)}, ${r.cy.toFixed(2)} of the box`);
      check(`${w}x${h}: …and that is the bottom-left corner of the painting`,
            r.art.x > 40 && r.art.x < 200 && r.art.y > 1740 && r.art.y < 1960,
            `art ${r.art.x},${r.art.y}`);
      check(`${w}x${h}: it is big enough to hit`, r.box.w >= 36 && r.box.h >= 28,
            `${Math.round(r.box.w)}x${Math.round(r.box.h)}`);
    }

    console.log("\nclicking it says both things, and the find survives the click");
    {
      const r = run("_coral_click.html", `
        setTimeout(function () {
          var note = document.getElementById("auth-choose-err");
          // The harness has no Firebase behind it, so the screen may already be
          // saying something about that. Clear it through the real renderer
          // first: what is being measured is the click, not the environment.
          log.saidAtLoad = note.textContent.trim();
          window.__ccAuthNote("", "ok");
          log.before = note.classList.contains("is-on");
          document.getElementById("auth-coral-secret").click();
          log.after = note.classList.contains("is-on");
          log.tone  = note.classList.contains("is-ok");
          log.said  = note.textContent.trim();
          log.stored = localStorage.getItem("cc_staghorn_coral_found_v1");
          done();
        }, 900);
      `, 1440, 900, (r) => r.after !== undefined);
      check("the note is quiet until the coral is found", r && r.before === false,
            r && ("it was saying: " + r.saidAtLoad));
      check("…and up once it is", r && r.after === true);
      check("…in the good-news tone, not the one used for a failed sign-in",
            r && r.tone === true);
      check("it says what was found", r && /found the staghorn coral/i.test(r.said || ""));
      check("…and what it is worth, and how to get it",
            r && /sign in or create an account/i.test(r.said || "")
              && /coral reef background/i.test(r.said || ""));
      check("the find is written down, because there is nobody to give it to yet",
            r && r.stored === "1", r && String(r.stored));
    }

    console.log("\non a phone the coral is on the screen, so the secret is too");
    {
      const r = run("_coral_phone.html", `
        var e = document.getElementById("auth-coral-secret");
        log.display = getComputedStyle(e).display;
        log.pe = getComputedStyle(e).pointerEvents;
        done();
      `, 430, 900, (r) => r.display);
      check("the box is not hidden away down here", r && r.display !== "none", r && r.display);
      check("…and it really takes clicks once the screen is armed",
            r && r.pe !== "none", r && r.pe);
    }
  } finally {
    try { process.kill(server.pid); } catch (_) {}
    tmp.forEach(f => { try { fs.unlinkSync(f); } catch (_) {} });
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
