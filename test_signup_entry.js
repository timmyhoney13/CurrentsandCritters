#!/usr/bin/env node
/* Signing up has to end with the player inside THEIR OWN account.
 *
 * Creating the Firebase user is the easy half and it already worked. This file
 * is about the half after it, where a new account can still fail to become a
 * player, and about the two ways it did:
 *
 *   1. A FAILURE NOBODY COULD SEE. The username-and-password sign-up finishes
 *      through finishNicknameSetup, the same path a Google sign-up takes. But
 *      it runs with the CREATE pane on screen, and finishNicknameSetup reports
 *      failure into #auth-nick-err, which lives on the NICKNAME pane. So a
 *      profile write that failed said so into a pane that was not displayed:
 *      the player sat under "Creating your account…" for good, holding a
 *      Firebase account with no profile behind it, and every retry from the
 *      create pane answered "that username is taken", because by then it was.
 *
 *      The fix brings the pane that owns the message up, carrying the name.
 *      That makes the failure readable AND retryable, because Create Username
 *      runs the same function again onto the same account. The ORDER inside
 *      the catch is the whole thing: ccChooserPane wipes #auth-nick-err on the
 *      way through, so a message set before the pane switch would be swept
 *      away by its own fix.
 *
 *   2. A NEW ACCOUNT THAT LOADED SOMEBODY ELSE'S. loadProfileWithFallbacks
 *      ends with a lookup by DISPLAY NAME, for a Google account whose UID
 *      moved. Display names are not unique: only nickname + friend_code is.
 *      A password account sets its displayName to the LOGIN name and stores
 *      `email: ""`, so the email tie-break in that step can never fire and the
 *      loser is handed docs[0] — a stranger's profile, which safeWriteProfile
 *      then copies onto the brand-new UID. Sign up as `shark` while any player
 *      is called `shark`, and you arrive in their Player Home wearing their
 *      coins. A password account's UID never moves, so that step is pure
 *      downside for it and is now skipped.
 *
 * Half source-level, half driven in a real browser against the real markup and
 * the real CSS, because "the message was invisible" is a question about CSS.
 *
 * Run:  node test_signup_entry.js        (needs Google Chrome / Chromium)
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const HTML = read("preview.html");
const APP = read("js/preview-app.js");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra != null ? "  → " + extra : "")); }
}

// The body of finishNicknameSetup, so the checks below read the real function
// and not some other part of the file that happens to say the same words.
const FINISH = (() => {
  const i = APP.indexOf("async function finishNicknameSetup(");
  if (i < 0) return "";
  return APP.slice(i, APP.indexOf("\n    $a(\"auth-nick-input\").addEventListener", i));
})();

// ══════════════════════════════════════════════════════════════════════════
//  1. A FAILED SIGN-UP HAS TO SAY SO SOMEWHERE THE PLAYER IS LOOKING
// ══════════════════════════════════════════════════════════════════════════
console.log("\na sign-up that fails says so on a pane that is on screen");
{
  check("finishNicknameSetup takes the caller's word for which pane is up",
        /async function finishNicknameSetup\(nick, opts\)/.test(APP));
  check("…and the username sign-up, which runs it under the CREATE pane, says so",
        /await finishNicknameSetup\(staged\.nick, \{ surfacePane: true \}\);/.test(APP));
  check("…while the nickname pane's own button still calls it plainly",
        /void finishNicknameSetup\(nick\);/.test(APP));

  // The failure has to put the pane up, not just write into it.
  check("a failed profile save brings the nickname pane up",
        /showStep\("auth-step-nickname"\);[\s\S]{0,120}?setAuthMsg\("auth-nick-err"/.test(FINISH));
  check("…carrying the name they already chose, so the retry is one click",
        /if \(opts && opts\.surfacePane\) \{[\s\S]{0,200}?inp\.value = nick;/.test(FINISH));
  check("…and the counter is repainted, the field having been filled behind its back",
        /inp\.value = nick;[\s\S]{0,120}?paintNickCount\(\);/.test(FINISH));

  // THE ORDER. ccChooserPane wipes this exact line on the way through, so a
  // message written before the pane switch is a message the fix deletes.
  const iPane = FINISH.indexOf('showStep("auth-step-nickname")');
  const iMsg  = FINISH.indexOf('setAuthMsg("auth-nick-err", "Could not save your username');
  check("the pane comes up BEFORE the message, because the pane switch wipes it",
        iPane > 0 && iMsg > 0 && iPane < iMsg, `pane@${iPane} msg@${iMsg}`);
  // Matched loosely on the list, which grows: "ao-code-err" joined it when the
  // recovery-code pane did. What matters is that auth-nick-err is IN it, since
  // that is what makes the ordering above load-bearing.
  check("…and ccChooserPane really is what wipes it, which is why the order matters",
        /\[(?:"[a-z-]+", )*"auth-nick-err"(?:, "[a-z-]+")*\]\.forEach\(id => \{[\s\S]{0,80}?setAuthMsg\(id, "", true\)/.test(APP));

  // The account exists by now, so nothing here may hand the player back to a
  // create pane that can only answer "that username is taken".
  check("the failed sign-up is never sent back to the create pane",
        !/surfacePane[\s\S]{0,400}?ccChooserPane\("create"/.test(FINISH));
}

// ══════════════════════════════════════════════════════════════════════════
//  2. A NEW ACCOUNT IS NEVER SOMEBODY ELSE'S ACCOUNT
// ══════════════════════════════════════════════════════════════════════════
console.log("\na brand-new account never loads a stranger's profile");
{
  const LOAD = (() => {
    const i = APP.indexOf("async function loadProfileWithFallbacks(");
    return i < 0 ? "" : APP.slice(i, APP.indexOf("\n    async function saveNewProfile(", i));
  })();

  check("the display-name lookup is switched off for a username account",
        /const isPasswordAccount = String\(user\.email \|\| ""\)\.toLowerCase\(\)\.endsWith\("@" \+ CC_LOGIN_DOMAIN\);/.test(LOAD));
  check("…which is exactly what decides whether there is a hint to look up",
        /const hint = isPasswordAccount \? "" : \(user\.displayName \|\| ""\)\.trim\(\);/.test(LOAD));
  check("…and the lookup still runs for a Google account, whose UID can move",
        /if \(hint\) \{[\s\S]{0,400}?nickname_lower/.test(LOAD));

  // The premise of the bug, pinned so it cannot quietly stop being true.
  check("the sign-up really does put the login name on displayName",
        /cred\.user\.updateProfile\(\{ displayName: nick \}\)/.test(APP));
  check("…and a password account really does store an empty email",
        /isPasswordAccount \? "" : \(_authUser\.email \|\| ""\)/.test(APP));
  check("…so the email tie-break in that step could never have saved anyone",
        /nickSnap\.docs\.find\(d => d\.data\(\)\.email === email\) \|\| nickSnap\.docs\[0\]/.test(LOAD));
  // And nothing anywhere makes a display nickname unique: only nick+code is.
  check("nothing claims display nicknames are unique",
        /friend_lookup"\)\.doc\(fcKey\)/.test(APP)
        && /const fcKey = nick\.toLowerCase\(\) \+ "_" \+ code;/.test(APP));
}

// ══════════════════════════════════════════════════════════════════════════
//  DRIVE: the invisibility was a CSS fact, so it is measured in a browser
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
  const PORT = 9340 + (process.pid % 300);
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
  // "Can the player see this?" is the only question this file asks, so it is
  // asked properly: laid out, not display:none anywhere up the tree, not
  // transparent, and actually of a size.
  function seen(id) {
    var e = document.getElementById(id);
    if (!e) return { there: false };
    var b = e.getBoundingClientRect(), cs = getComputedStyle(e);
    return {
      there: true,
      laid: e.offsetParent !== null || cs.position === "fixed",
      shown: cs.display !== "none" && cs.visibility !== "hidden" && Number(cs.opacity) > 0.01,
      w: Math.round(b.width), h: Math.round(b.height),
      inside: b.top >= -1 && b.bottom <= window.innerHeight + 1,
      text: (e.textContent || "").trim().slice(0, 60)
    };
  }
  function pane() {
    var names = ["signin","forgot","create","guest","nickname"], up = [];
    names.forEach(function (n) {
      var e = document.getElementById("ao-pane-" + n);
      if (e && getComputedStyle(e).display !== "none") up.push(n);
    });
    return up;
  }
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

  try {
    // ── The premise AND the fix, in one run ────────────────────────────────
    // Driven through the real setAuthMsg: the Create Username handler writes
    // into #auth-nick-err by itself when there is no signed-in user, which is
    // exactly the situation here, so what gets measured is the app's own
    // renderer painting the app's own element.
    console.log("\nthe failure message is readable on the pane that owns it");
    {
      const r = run("_su_note.html", `
        window.__fishGoToSignIn({ pane: "nickname" });
        var inp = document.getElementById("auth-nick-input");
        inp.value = "reeftest";
        inp.dispatchEvent(new Event("input"));
        setTimeout(function () {
          // The real handler, writing the real message into the real line.
          document.getElementById("auth-nick-go-btn").click();
          setTimeout(function () {
            log.panes = pane();
            log.note  = seen("auth-nick-err");
            log.input = seen("auth-nick-input");
            log.go    = seen("auth-nick-go-btn");
            log.value = inp.value;
            log.count = (document.getElementById("auth-nick-count") || {}).textContent;

            // …and now the pane the username sign-up actually runs under. The
            // same line, and no way at all to read it.
            window.__fishGoToSignIn({ pane: "create" });
            setTimeout(function () {
              log.panes2 = pane();
              log.note2  = seen("auth-nick-err");
              done();
            }, 400);
          }, 200);
        }, 400);
      `, 1440, 900, (r) => r && r.panes && r.panes2);

      check("the nickname pane is the one on screen",
            r && Array.isArray(r.panes) && r.panes.length === 1 && r.panes[0] === "nickname",
            r && JSON.stringify(r.panes));
      check("…the name they chose is still in the field",
            r && r.value === "reeftest", r && r.value);
      check("…and the counter agrees with it, rather than still reading 0",
            r && /8\s*\/\s*15/.test(String(r.count || "")), r && r.count);
      check("…the message the app wrote is laid out and painted",
            r && r.note && r.note.laid && r.note.shown && r.note.h > 0,
            r && JSON.stringify(r.note));
      check("…and it says something, rather than being an empty box",
            r && r.note && r.note.text.length > 0, r && r.note && r.note.text);
      check("…the field is reachable, whole, and on the screen",
            r && r.input && r.input.laid && r.input.w > 100 && r.input.inside,
            r && JSON.stringify(r.input));
      // The retry is the entire point: this button runs finishNicknameSetup
      // again, onto the account that already exists.
      check("…and Create Username is there to run it again",
            r && r.go && r.go.laid && r.go.shown && r.go.w > 100 && r.go.h > 20 && r.go.inside,
            r && JSON.stringify(r.go));
      check("…saying so in words",
            r && r.go && /Create Username/.test(r.go.text), r && r.go && r.go.text);

      // THE BUG, measured: under the create pane that same line is unreadable.
      check("under the CREATE pane, only the create pane is up",
            r && Array.isArray(r.panes2) && r.panes2.length === 1 && r.panes2[0] === "create",
            r && JSON.stringify(r.panes2));
      check("…and the nickname pane's message cannot be read there at all",
            r && r.note2 && !r.note2.laid, r && JSON.stringify(r.note2));
    }

    // A phone is where a retry button hides below the fold. Reachability is
    // judged the way test_username_screen.js judges it: inside the column's
    // own scroll length, since that column is what scrolls.
    console.log("\nand the retry is reachable on a phone");
    {
      const r = run("_su_phone.html", `
        window.__fishGoToSignIn({ pane: "nickname" });
        setTimeout(function () {
          document.getElementById("auth-nick-go-btn").click();
          setTimeout(function () {
            var btn = document.getElementById("auth-nick-go-btn");
            var col = document.querySelector(".ao-form");
            log.note = seen("auth-nick-err");
            log.go   = seen("auth-nick-go-btn");
            log.reach = !!col && !!btn
                     && col.scrollHeight >= btn.offsetTop + btn.offsetHeight;
            log.sideways = document.documentElement.scrollWidth <= window.innerWidth + 1;
            done();
          }, 200);
        }, 400);
      `, 500, 900, (r) => r && r.go);

      check("the message is laid out and painted on a narrow window too",
            r && r.note && r.note.laid && r.note.shown,
            r && JSON.stringify(r.note));
      check("…Create Username is painted and a real tap target",
            r && r.go && r.go.laid && r.go.w > 100 && r.go.h >= 36,
            r && JSON.stringify(r.go));
      check("…and reachable, scrolling the column if need be",
            r && r.reach === true, r && JSON.stringify({ reach: r.reach }));
      check("…while nothing pushes the page sideways",
            r && r.sideways === true);
    }
  } finally {
    try { server.kill(); } catch (_) {}
    for (const f of tmp) { try { fs.unlinkSync(f); } catch (_) {} }
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
