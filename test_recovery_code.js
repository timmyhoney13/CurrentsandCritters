#!/usr/bin/env node
/* The recovery code: the way back into an account that has no email.
 *
 * A username here is not an email. `mermaid_92` signs in to Firebase as
 * `mermaid_92@players.currentsandcritters.com`, an address at a domain that
 * receives nothing, so there is nobody to mail when a password is forgotten.
 * Linking a real address is offered and optional, and most players will never
 * do it. For them a forgotten password was the end of the account.
 *
 * A recovery code is the other door: username + code + a new password. What
 * this file pins is what you would pin about a password, because that is what
 * it is:
 *
 *   1. THE BROWSER NEVER KEEPS IT. The server stores only an HMAC, so the
 *      plaintext exists in one HTTP response and then on the player's paper.
 *      Nothing here may write it to localStorage, and the modal empties the
 *      element on the way out: a credential left in the DOM of a tab on a
 *      school computer is not a credential.
 *   2. IT IS SHOWN ONCE, AND THE SCREEN SAYS SO. The confirm button is dead
 *      until the checkbox is ticked, and the modal has no backdrop-click and
 *      no Escape, because a stray click is exactly how the only sight of it
 *      would be lost.
 *   3. EVERYBODY GETS ONE. New accounts at sign-up, older ones on their next
 *      sign-in, and never a Google account, which has no password here to be
 *      locked out of.
 *   4. SPENDING ONE IS ONE STEP. Getting in and choosing a new password happen
 *      together, because the code works once: a player who spent it without
 *      setting a password would be locked out again with nothing left.
 *   5. THE FRESH CODE IS SHOWN AFTER THE DOOR, NOT AT IT. Redeeming mints the
 *      replacement, and putting it up at the moment of redeeming would have it
 *      painted over by the sign-in and dismissed unread.
 *   6. IT IS TYPED OFF PAPER. The field grooms what is typed into it, and the
 *      alphabet has no 0/O or 1/I/L in it at all.
 *
 * Half source-level, half driven in a real browser against the real markup.
 *
 * Run:  node test_recovery_code.js        (needs Google Chrome / Chromium)
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const HTML = read("preview.html");
const CSS = read("css/preview.css");
const APP = read("js/preview-app.js");
const PY = fs.readFileSync(path.join(ROOT, "account_email.py"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra != null ? "  → " + extra : "")); }
}

// ══════════════════════════════════════════════════════════════════════════
//  1. THE BROWSER NEVER KEEPS IT
// ══════════════════════════════════════════════════════════════════════════
console.log("\nthe code is a credential, and is treated like one");
{
  // The single most important line in the feature: if this ever becomes a
  // localStorage write, the whole design is gone.
  check("nothing caches the code anywhere on the device",
        !/(localStorage|sessionStorage)\.setItem\([^)]*[Rr]ecovery[Cc]ode/.test(APP)
        && !/(localStorage|sessionStorage)\.setItem\([^)]*_rcShownCode/.test(APP)
        && !/cc_recovery_code/.test(APP));
  check("…and closing the modal takes it back out of the page",
        /function ccCloseRecoveryCode\(\)[\s\S]{0,420}?_rcShownCode = "";[\s\S]{0,200}?out\.textContent = "";/.test(APP));
  check("the server stores a hash, never the code",
        /"recovery_code_hash": digest/.test(PY)
        && !/"recovery_code":\s/.test(PY));
  check("…and the uid is inside the MAC, so a hash cannot be moved between accounts",
        /msg = "recovery-code:%s:%s" % \(uid, code\)/.test(PY));
  check("…compared in constant time, because this is a password comparison",
        /def code_matches[\s\S]{0,420}?hmac\.compare_digest\(want, have\)/.test(PY));
  check("the endpoint that reports state cannot hand the code back",
        /def recovery_code_state[\s\S]{0,700}?"has_code": bool\(profile\.get\("recovery_code_hash"\)\)/.test(PY)
        && !/def recovery_code_state[\s\S]{0,700}?"code":/.test(PY));
}

// ══════════════════════════════════════════════════════════════════════════
//  2. SHOWN ONCE, AND THE SCREEN SAYS SO
// ══════════════════════════════════════════════════════════════════════════
console.log("\nit is shown once, and the screen is built to be believed");
{
  check("there is a modal for it, with the code, a copy and a file",
        /id="rc-modal"/.test(HTML) && /id="rc-code"/.test(HTML)
        && /id="rc-copy"/.test(HTML) && /id="rc-download"/.test(HTML));
  check("…it says out loud that this is the only time",
        /only time it is shown/i.test(HTML));
  check("…the confirm button starts dead",
        /id="rc-done"[^>]*disabled/.test(HTML));
  check("…and only the checkbox brings it to life",
        /box\.addEventListener\("change", \(\) => \{ done\.disabled = !box\.checked; \}\)/.test(APP));
  check("…the button asks for the thing that matters, not for an OK",
        /written it down/i.test(HTML));
  // A stray click on the dark area would be the whole loss.
  check("no backdrop click and no Escape can dismiss it",
        !/\$a\("rc-modal"\)\.addEventListener\("click"/.test(APP)
        && !/rc-modal[\s\S]{0,300}?key === "Escape"/.test(APP));
  check("copy has a way through when the clipboard is refused",
        /navigator\.clipboard\.writeText\(_rcShownCode\)/.test(APP)
        && /execCommand/.test(APP));
  check("…and the code can be selected by hand in one tap",
        /\.rc-code \{[\s\S]{0,400}?user-select: all;/.test(CSS));
  check("the saved file carries the username too, since both are needed",
        /Username:\s+" \+ who/.test(APP) && /Recovery code: " \+ _rcShownCode/.test(APP));
}

// ══════════════════════════════════════════════════════════════════════════
//  3. EVERYBODY GETS ONE
// ══════════════════════════════════════════════════════════════════════════
console.log("\neverybody who can be locked out gets one");
{
  check("a brand-new account is given one at sign-up",
        /async function finishNicknameSetup\(nick, opts\)[\s\S]{0,6000}?void ccEnsureRecoveryCode\(\);/.test(APP));
  check("…and an account that predates the feature gets one on its next sign-in",
        /function revealRegisteredLobby[\s\S]{0,1900}?void ccEnsureRecoveryCode\(\);/.test(APP));
  check("…only once per page load, and never twice over",
        /if \(_rcEnsured && !o\.force\) return;[\s\S]{0,40}?_rcEnsured = true;/.test(APP));
  check("…and never for a Google account, which has no password to lose",
        /if \(!_authUser \|\| !_isPasswordAccount\(\)\) return;/.test(APP)
        && /def _is_password_account[\s\S]{0,300}?== "google":\n        return False/.test(PY));
  check("…nor is one issued on top of a code the player already has",
        /if \(!st \|\| !st\.eligible \|\| st\.has_code\) return;/.test(APP));
  // A missing recovery code must never be a reason a player cannot play.
  check("none of it can stop somebody getting into the game",
        /catch \(_\) \{ \/\* never in the way of playing \*\/ \}/.test(APP)
        && /try \{ void ccEnsureRecoveryCode\(\); \} catch \(_\) \{\}/.test(APP));
  check("Settings can make a new one, and warns that it kills the old one",
        /id="settings-rc-row"/.test(HTML) && /id="settings-rc-btn"/.test(HTML)
        && /current code stops working straight away/.test(APP));
  check("…and that row never claims to know the code itself",
        /_paintSettingsRcRow[\s\S]{0,1200}?val\.textContent = "None yet";/.test(APP)
        && !/_paintSettingsRcRow[\s\S]{0,1200}?st\.code/.test(APP));
}

// ══════════════════════════════════════════════════════════════════════════
//  4 & 5. SPENDING ONE
// ══════════════════════════════════════════════════════════════════════════
console.log("\nspending one gets you in and leaves you holding the next one");
{
  check("the pane asks for all three things at once",
        /id="ao-code-user"/.test(HTML) && /id="ao-code-code"/.test(HTML)
        && /id="ao-code-pass"/.test(HTML));
  check("…and it is a pane of the same column, so the painting never moves",
        /code:\s+"ao-pane-code",/.test(APP)
        && /AO_PANE_ORDER = \["signin", "forgot", "code", "create", "guest", "nickname"\]/.test(APP));
  check("…its status line is wiped with the others on the way through",
        /"auth-guest-err", "auth-nick-err", "ao-forgot-err", "ao-code-err"/.test(APP));
  check("…and it is reachable from the forgotten-password pane",
        /id="ao-forgot-code"/.test(HTML)
        && /\$a\("ao-forgot-code"\)\.addEventListener\("click", \(\) => ccChooserPane\("code"\)\)/.test(APP));
  // The green bar is the rule here too, and it is the SAME bar: one function,
  // not a second copy that drifts.
  check("the password bar is the same function the create pane uses",
        /function ccPaintPwMeter\(ids\)/.test(APP)
        && /ccPaintPwMeter\(\{ pass: "ao-code-pass", meter: "ao-code-meter"/.test(APP));
  check("…and the button obeys it",
        /id="ao-code-go"[^>]*disabled/.test(HTML));
  // ONE way in. The redeem does not invent a second sign-in.
  check("getting in afterwards runs the ordinary sign-in, not a second one",
        /await ccPasswordSignIn\(\{\}\);/.test(APP));
  check("…and the code and password do not stay in the fields",
        /function ccClearCodeFields[\s\S]{0,260}?"ao-code-code", "ao-code-pass"/.test(APP)
        && /ccClearCodeFields\(\);\n      await ccPasswordSignIn/.test(APP));
  check("the replacement is shown after the door, not at it",
        /_rcPendingCode = r\.code \|\| "";/.test(APP)
        && /if \(_rcPendingCode\) \{[\s\S]{0,400}?ccShowRecoveryCode\(fresh/.test(APP));
  check("…and the server really does mint it in the same call",
        /fresh = _write_new_code\(db, uid, now\)/.test(PY));
  check("…after setting the password, so a failure leaves them locked IN",
        PY.indexOf("fb_auth.update_user(uid, password=") < PY.indexOf("fresh = _write_new_code"));
  check("a code works once",
        /"recovery_code_used_at": int\(now\)/.test(PY));
  check("a wrong name and a wrong code read identically",
        /REDEEM_ANSWER_BAD/.test(PY)
        && (PY.match(/"message": REDEEM_ANSWER_BAD/g) || []).length >= 2);
  check("…and guessing is rate limited on the name as typed",
        /def _redeem_locked/.test(PY) && /REDEEM_MAX_FAILS/.test(PY));
}

// ══════════════════════════════════════════════════════════════════════════
//  6. IT IS TYPED OFF PAPER
// ══════════════════════════════════════════════════════════════════════════
console.log("\nit survives being written on paper and typed back in");
{
  check("the alphabet has no 0/O or 1/I/L in it",
        /CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"/.test(PY));
  check("…and the server reads back whatever separators and case were used",
        /re\.sub\(r"\[\\s\\-_\.\]\+", "", s\)/.test(PY) && /\.upper\(\)/.test(PY));
  check("the code is set in a monospace, spaced out, so it can be copied down",
        /\.rc-code \{[\s\S]{0,300}?font-family: ui-monospace/.test(CSS));
  check("…and the field it is typed back into matches it",
        /#auth-step-choose \.ao-field input\.rc-input \{[\s\S]{0,200}?font-family: ui-monospace/.test(CSS));
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
} else {
  const PORT = 9040 + (process.pid % 300);
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
  function seen(id) {
    var e = document.getElementById(id);
    if (!e) return { there: false };
    var b = e.getBoundingClientRect(), cs = getComputedStyle(e);
    return { there: true, laid: e.offsetParent !== null || cs.position === "fixed",
             shown: cs.display !== "none" && cs.visibility !== "hidden",
             w: Math.round(b.width), h: Math.round(b.height),
             inside: b.left >= -1 && b.right <= window.innerWidth + 1,
             mono: /mono|Menlo|Consolas/i.test(cs.fontFamily),
             text: (e.textContent || "").trim().slice(0, 60) };
  }
  function pane() {
    var names = ["signin","forgot","code","create","guest","nickname"], up = [];
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
    } catch (e) { log.oops = String(e && e.message); done(); }
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
      if (r.fatal || r.oops) continue;
      if (!ok || ok(r)) return r;
    }
    return last;
  }

  const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });
  try { execFileSync(process.execPath, ["-e", "setTimeout(()=>{},900)"]); } catch (_) {}

  try {
    // ── Getting to the pane, and typing a code into it ─────────────────────
    console.log("\nthe pane is reachable, and grooms a code as it is typed");
    {
      const r = run("_rc_pane.html", `
        document.getElementById("ao-forgot").click();
        setTimeout(function () {
          log.afterForgot = pane();
          document.getElementById("ao-forgot-code").click();
          setTimeout(function () {
            log.panes = pane();
            var f = document.getElementById("ao-code-code");
            // Typed off paper: no dashes, lower case, the way a person does it.
            "abcd".split("").concat("efgh".split("")).forEach(function (ch) {
              f.value += ch;
              f.dispatchEvent(new Event("input"));
            });
            log.typed = f.value;
            f.value = "abcd-efgh-jkmn-pqrs-zzzz";
            f.dispatchEvent(new Event("input"));
            log.capped = f.value;
            log.field = seen("ao-code-code");
            log.go    = seen("ao-code-go");
            log.goOff = document.getElementById("ao-code-go").disabled;
            done();
          }, 420);
        }, 420);
      `, 1440, 900, (r) => r && r.panes);

      check("Forgot password opens the forgot pane",
            r && Array.isArray(r.afterForgot) && r.afterForgot[0] === "forgot",
            r && JSON.stringify(r.afterForgot));
      check("…and Use Recovery Code turns the column over onto the code pane",
            r && Array.isArray(r.panes) && r.panes.length === 1 && r.panes[0] === "code",
            r && JSON.stringify(r.panes));
      check("typing plain letters grooms them into groups of four",
            r && r.typed === "ABCD-EFGH", r && r.typed);
      check("…and nothing longer than a real code can be typed",
            r && r.capped === "ABCD-EFGH-JKMN-PQRS", r && r.capped);
      check("…the field is monospaced, so a mistyped character is visible",
            r && r.field && r.field.mono, r && r.field && JSON.stringify(r.field));
      check("…and the button stays dead until there is a password worth having",
            r && r.goOff === true);
      check("the field is whole and on the screen",
            r && r.field && r.field.laid && r.field.w > 100 && r.field.inside,
            r && JSON.stringify(r.field));
    }

    // ── The modal ─────────────────────────────────────────────────────────
    console.log("\nthe modal will not let a code be clicked past");
    {
      const r = run("_rc_modal.html", `
        var m = document.getElementById("rc-modal");
        // Painted the way the app paints it, then measured.
        document.getElementById("rc-code").textContent = "ABCD-EFGH-JKMN-PQRS";
        m.classList.add("open");
        setTimeout(function () {
          log.code = seen("rc-code");
          log.copy = seen("rc-copy");
          log.save = seen("rc-download");
          log.doneBtn = seen("rc-done");
          var d = document.getElementById("rc-done"), b = document.getElementById("rc-ack-box");
          log.deadAtFirst = d.disabled;
          // A click on the dark area, which every other modal in the game takes
          // as "close". This one must not.
          m.click();
          log.stillOpenAfterBackdrop = m.classList.contains("open");
          document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
          log.stillOpenAfterEsc = m.classList.contains("open");
          // Clicking the dead button does nothing either.
          d.click();
          log.stillOpenAfterDeadClick = m.classList.contains("open");
          b.checked = true;
          b.dispatchEvent(new Event("change"));
          log.aliveAfterTick = !d.disabled;
          d.click();
          setTimeout(function () {
            log.closed = !m.classList.contains("open");
            log.emptied = (document.getElementById("rc-code").textContent || "").trim() === "";
            done();
          }, 120);
        }, 320);
      `, 1440, 900, (r) => r && r.code);

      check("the code is painted, large and monospaced",
            r && r.code && r.code.laid && r.code.mono && r.code.h > 24,
            r && JSON.stringify(r.code));
      check("…with Copy and Save beside it, both real buttons",
            r && r.copy && r.copy.laid && r.copy.w > 60
            && r && r.save && r.save.laid && r.save.w > 60,
            r && JSON.stringify([r.copy, r.save]));
      check("the confirm button starts dead",
            r && r.deadAtFirst === true);
      check("…a click on the backdrop does not throw the code away",
            r && r.stillOpenAfterBackdrop === true);
      check("…nor does Escape",
            r && r.stillOpenAfterEsc === true);
      check("…nor does clicking the dead button",
            r && r.stillOpenAfterDeadClick === true);
      check("ticking the box is what brings the button to life",
            r && r.aliveAfterTick === true);
      check("…and then it closes",
            r && r.closed === true);
      check("…taking the code back out of the page with it",
            r && r.emptied === true);
    }

    // ── Sizes ─────────────────────────────────────────────────────────────
    console.log("\nnothing is cut off, at any window a player might open");
    for (const [w, h] of [[1440, 900], [1024, 768], [500, 900], [820, 1180]]) {
      const at = `${w}x${h}:`;
      const r = run("_rc_size.html", `
        document.getElementById("ao-forgot").click();
        setTimeout(function () {
          document.getElementById("ao-forgot-code").click();
          setTimeout(function () {
            var out = {};
            ["ao-code-user","ao-code-code","ao-code-pass","ao-code-go"].forEach(function (id) {
              out[id] = seen(id);
            });
            log.ctl = out;
            var col = document.querySelector(".ao-form");
            var btn = document.getElementById("ao-code-go");
            log.reach = !!col && !!btn && col.scrollHeight >= btn.offsetTop + btn.offsetHeight;
            log.sideways = document.documentElement.scrollWidth <= window.innerWidth + 1;

            document.getElementById("rc-code").textContent = "ABCD-EFGH-JKMN-PQRS";
            document.getElementById("rc-modal").classList.add("open");
            setTimeout(function () {
              log.modalCode = seen("rc-code");
              var box = document.querySelector("#rc-modal .modal-box");
              var b = box.getBoundingClientRect();
              log.modalFits = b.top >= -1 && b.left >= -1 && b.right <= window.innerWidth + 1;
              done();
            }, 200);
          }, 420);
        }, 420);
      `, w, h, (r) => r && r.ctl);

      check(at + " every field on the pane is painted and whole",
            r && r.ctl && ["ao-code-user", "ao-code-code", "ao-code-pass", "ao-code-go"]
              .every(id => r.ctl[id] && r.ctl[id].laid && r.ctl[id].w > 80 && r.ctl[id].inside),
            r && JSON.stringify(r.ctl));
      check(at + " …the button is reachable",
            r && r.reach === true);
      check(at + " …nothing pushes the page sideways",
            r && r.sideways === true);
      check(at + " …and the code in the modal is readable and inside the box",
            r && r.modalCode && r.modalCode.laid && r.modalCode.w > 80 && r.modalFits === true,
            r && JSON.stringify([r.modalCode, r.modalFits]));
    }
  } finally {
    try { server.kill(); } catch (_) {}
    for (const f of tmp) { try { fs.unlinkSync(f); } catch (_) {} }
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
