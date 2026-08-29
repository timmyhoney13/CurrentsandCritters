#!/usr/bin/env node
/* The staghorn coral, and what is left of it.
 *
 * THE SECRET IS RETIRED. It was an invisible box over a coral painted into the
 * bottom left of the sign-in artwork, and clicking it was worth the Coral Reef
 * background. The artwork was replaced in 1.7.0 by a painting that carries its
 * own title and has no coral and no kelp in it, so the box had nothing left to
 * sit on: it was over open water, under a message about a coral that is not
 * there. The button, its copy and the geometry that placed it are gone.
 *
 * What this file pins now is the half that MUST NOT go with it. The find was
 * never the reward: it was a note on the device, spent the first moment there
 * was an account to spend it on. Somebody may have clicked that coral as a
 * guest last week and still not made an account. Their note is still in
 * localStorage, and it is still owed:
 *
 *   1. THE BOX IS GONE, from the markup, the CSS and the app, and nothing
 *      writes a new note any more.
 *   2. THE CLAIM IS NOT GONE. ccCoralClaim() still reads the note and still
 *      hangs on revealLobby(), the one place both roads in end.
 *   3. IT IS SPENT ON ONE ACCOUNT, written to THAT account's document by uid.
 *   4. A FAILED WRITE KEEPS THE NOTE, so a find is not lost with the wifi.
 *   5. THE REWARD IS A REAL BACKGROUND, in the registry the gallery renders
 *      from and in the server's list of the eight, so an account that is paid
 *      out has somewhere to put it.
 *
 * (The secret replaced a rotted pier piling in 1.6.97, and was retired with
 * the artwork in 1.7.0.)
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

console.log("\nthe box is gone, and nothing writes a new note");
{
  check("the button is out of the markup",
        !/auth-coral-secret/.test(HTML) && !/A staghorn coral in the kelp/.test(HTML));
  check("…and out of the CSS",
        !/auth-coral-secret/.test(CSS));
  check("…and the geometry that placed it on the painting is gone",
        !/CORAL_SPOT/.test(APP) && !/placeCoralBox/.test(APP));
  check("…including the resize handler that kept it in place",
        !/window\.addEventListener\("resize", placeCoralBox\)/.test(APP));
  check("nothing writes a find any more",
        !/ccCoralNoteFind/.test(APP)
        && !/localStorage\.setItem\(CORAL_FIND_KEY/.test(APP));
  check("…and the message about a coral that is not in the picture is gone",
        !/You found the staghorn coral!/.test(APP));
  check("the column is still held inert until the artwork has painted",
        /#auth-step-choose:not\(\.is-armed\) > \.ao-form \{ pointer-events: none; \}/.test(CSS)
        && /function armChooseStep\(\)/.test(APP));
  check("the plank it replaced is still gone from every file that knew about it",
        !/auth-pier-secret|PIER_SPOT|ccPierClaim|cc_pier_piling_found/.test(HTML + CSS + APP));
}

console.log("\nbut a find already made is still owed, and still read");
{
  check("the note's key is unchanged, so an old find is still recognised",
        /const CORAL_FIND_KEY = "cc_staghorn_coral_found_v1";/.test(APP));
  check("…and it is still read",
        /function ccCoralFound\(\)    \{ try \{ return localStorage\.getItem\(CORAL_FIND_KEY\) === "1"; \}/.test(APP));
  check("…by a claim that is still in the file",
        /async function ccCoralClaim\(\)/.test(APP));
}

console.log("\nit is spent on ONE account, the first one to turn up");
{
  check("the claim hangs on revealLobby, the one place both roads in end",
        /function revealLobby[\s\S]{0,3000}?try \{ ccCoralClaim\(\)\.catch\(\(\) => \{\}\); \} catch \(_\) \{\}/.test(APP));
  check("…on the account side of it, not the guest side",
        /if \(_authUser && _playerNickname\) \{[\s\S]{0,2200}?ccCoralClaim\(\)\.catch/.test(APP));
  const CLAIM = APP.slice(APP.indexOf("async function ccCoralClaim()"),
                          APP.indexOf("// Escape backs out of a pane you went INTO"));
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
                          APP.indexOf("// Escape backs out of a pane you went INTO"));
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
//  DRIVE  (a real browser: the box is really gone, at every window shape)
// ══════════════════════════════════════════════════════════════════════════
//
// The source half proves the markup no longer contains it. This proves the
// screen no longer contains it: an invisible button that survived in some
// branch of the layout would be worse than the one that was removed, because
// it would take clicks over open water and say a coral had been found.
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
  const tmp = [];
  function run(name, body, w, h, ok) {
    const f = path.join(CLIENT, name);
    fs.writeFileSync(f, HTML + `
<div id="out">PENDING</div>
<script>
(function () {
  var st=document.createElement("style");
  st.textContent="*,*::before,*::after{transition:none!important;animation:none!important}";
  document.head.appendChild(st);
  var log = {}, out = document.getElementById("out");
  function done() { out.textContent = JSON.stringify(log); }
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
</script>`);
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
  try {
    console.log("\nthere is nothing invisible left on the painting");
    // Both layouts, because the box used to be styled in both: the wide split
    // and the stacked phone band.
    for (const [w, h] of [[1440, 900], [1920, 1080], [1024, 768], [430, 900]]) {
      const r = run("_coral_gone.html", `
        log.btn = !!document.getElementById("auth-coral-secret");
        log.any = document.querySelectorAll(".auth-coral-secret").length;
        var art = document.querySelector(".ao-art");
        // Anything inside the painting's panel that takes clicks and shows
        // nothing would be the same trap under another name.
        var ghosts = 0;
        if (art) art.querySelectorAll("button, a, [role=button]").forEach(function (el) {
          var cs = getComputedStyle(el);
          if (cs.pointerEvents !== "none" && !el.textContent.trim()) ghosts++;
        });
        log.ghosts = ghosts;
        log.stored = localStorage.getItem("cc_staghorn_coral_found_v1");
        done();
      `, w, h, (r) => r.btn !== undefined);
      check(`${w}x${h}: the coral button is not on the screen`,
            r && r.btn === false && r.any === 0, r && `btn=${r.btn} matches=${r.any}`);
      check(`${w}x${h}: …and nothing else invisible takes clicks on the painting`,
            r && r.ghosts === 0, r && `${r.ghosts} silent click targets`);
      check(`${w}x${h}: …and simply opening the screen writes no find`,
            r && (r.stored === null || r.stored === undefined), r && String(r.stored));
    }
  } finally {
    try { process.kill(server.pid); } catch (_) {}
    tmp.forEach(f => { try { fs.unlinkSync(f); } catch (_) {} });
  }
}


console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
