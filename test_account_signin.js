#!/usr/bin/env node
/* The second way in: a username and a password.
 *
 * Google used to be the only door. "Not everyone has a Google account" is not
 * an edge case, it is most of a school, so the sign-in screen now offers a
 * choice and Google is one half of it.
 *
 * What this file pins, in the order a player meets it:
 *
 *   1. THE ARTWORK IS SCENERY. It used to BE the screen: login-bg.png was one
 *      flat image carrying eight oceans, the title, an octagon and two buttons,
 *      with invisible click boxes positioned over the painted pair in the
 *      artwork's own 1556x1011 coordinates. A box that drifted was a button a
 *      player aimed at and missed, and a phone got a whole second octagon
 *      redrawn in CSS because the printed one came out 141px across.
 *
 *      auth-ocean.jpg carries no words at all. The screen is two halves split
 *      at the middle: the painting on the left with real type over it, every
 *      way in on the right. So what this file measures is not where a box
 *      landed but that each control is there, whole, and reachable at seven
 *      window sizes, and that a narrow window stacks the halves.
 *   2. THERE IS ONE GOOGLE PATH AND ONE PASSWORD PATH. The screen carries its
 *      own username and password field, and signs in through the same function
 *      the card's form uses. CREATE AN ACCOUNT opens the card on the create
 *      pane.
 *   3. THE CARD ASKS FIRST ("do you already have one?"), then shows the form
 *      for the answer. Google is at the bottom of BOTH forms.
 *   4. THE GREEN BAR IS THE RULE, not decoration: Create Account is disabled
 *      until the password scores 3 of 4, which is the score at which the bar
 *      turns green. The two can never disagree, because the button reads the
 *      same function the bar does.
 *   5. A USERNAME IS AN ACCOUNT. Firebase only knows emails, so the username
 *      becomes one; that is what makes "this username is taken" a real,
 *      service-enforced answer rather than a query two people can win.
 *   6. A GUEST KEEPS NOTHING, AND LOSES NOTHING BY ACCIDENT. Signing out
 *      erases the lot. Backing out of a sign-in does not, because an attempt
 *      is not a sign-out.
 *   7. A GUEST CAN TAKE THEIR AFTERNOON WITH THEM. The offer is at the end of
 *      a game, and it is applied only to an account that is being created.
 *
 * Half source-level, half driven in a real browser against the real markup.
 *
 * Run:  node test_account_signin.js        (needs Google Chrome / Chromium)
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
  else { fail++; console.log("  ✗ FAIL: " + name + (extra != null ? "  → " + extra : "")); }
}

// ══════════════════════════════════════════════════════════════════════════
//  SOURCE
// ══════════════════════════════════════════════════════════════════════════

console.log("\nthe artwork is scenery, and every way in is real markup");
{
  check("the sign-in screen is the painting with no words baked into it",
        /auth-ocean\.jpg/.test(HTML) && /class="ao-art-img"/.test(HTML)
        && !/class="auth-step-choose-img"/.test(HTML));
  // The old screen WAS login-bg.png: the title, the panel and both buttons were
  // painted into one image with invisible boxes over them. Every one of those
  // words is real type now, which is why none of the artwork-coordinate
  // machinery below it exists any more.
  check("…so the title is text a browser can set, not pixels",
        /<h1 class="ao-title">Currents<\/h1>/.test(HTML)
        && /#auth-step-choose \.ao-title \{[\s\S]{0,200}?font-family: "Cinzel"/.test(CSS));
  check("…and the tagline is the game's own line",
        />Build Your Ocean\. Rule the Current\.</.test(HTML)
        && />Create powerful marine combinations and outplay the opponents\.</.test(HTML));
  check("every way in is on the one screen",
        /id="ao-user"/.test(HTML) && /id="ao-pass"/.test(HTML)
        && /id="ao-signin"/.test(HTML) && /id="auth-choose-google-btn"/.test(HTML)
        && /id="auth-guest-btn"/.test(HTML) && /id="ao-create"/.test(HTML));
  check("…and their labels are shown, not clipped to a pixel",
        /class="auth-btn-label"/.test(HTML)
        && !/#auth-step-choose[\s\S]{0,120}?\.auth-btn-label \{[\s\S]{0,240}?clip-path: inset\(50%\)/.test(CSS));
  check("the octagon redrawn for a phone is gone with the paint that needed it",
        !/class="auth-oct"/.test(HTML) && !/auth-oct-copy/.test(HTML)
        && !/--oct-size/.test(CSS) && !/--oct-cx/.test(CSS));
  check("the split is the halfway line, and the art is half of it",
        /#auth-step-choose > \.ao-art \{[\s\S]{0,300}?width: 50%;/.test(CSS)
        && /#auth-step-choose > \.ao-form \{[\s\S]{0,300}?width: 50%;/.test(CSS));
  check("…and a narrow window stacks them instead of halving them",
        /@media \(max-width: 820px\), \(max-aspect-ratio: 4\/5\)[\s\S]{0,1400}?#auth-step-choose > \.ao-art \{[\s\S]{0,200}?width: 100%;/.test(CSS));
  // A form you cannot see yet is only ever clicked by accident.
  check("nothing in the column is clickable until the artwork has painted",
        /#auth-step-choose:not\(\.is-armed\) > \.ao-form/.test(CSS)
        && /function armChooseStep\(\)/.test(APP));
}

console.log("\nthere is one Google path, and one password path");
{
  // This button spent one release as the only way in and one as a door onto
  // the account card. Now that the screen carries its own username and password
  // field, Google is one door among several and does the thing it says.
  check("CONTINUE WITH GOOGLE runs Google",
        /chooseAccountBtn\.addEventListener\("click", \(\) => \{ void ccGoogleSignIn\("auth-choose-err"\); \}\)/.test(APP));
  check("…through the one path the cards use too",
        /async function ccGoogleSignIn\(errId\)/.test(APP)
        && /\$a\("aao-in-google"\)\.addEventListener\("click",  \(\) => \{ void ccGoogleSignIn\("aao-in-err"\); \}\)/.test(APP));
  check("CREATE AN ACCOUNT opens the card on the pane that makes one",
        /\$a\("ao-create"\)\.addEventListener\("click", \(\) => openAccountCard\("create"\)\)/.test(APP));
  // Two forms ask for a username and a password now. They must be one function
  // taking two sets of fields, not two copies drifting apart.
  check("the screen's own form signs in through the card's own function",
        /async function ccPasswordSignIn\(ids\)/.test(APP)
        && /user: "ao-user", pass: "ao-pass", err: "auth-choose-err", go: "ao-signin"/.test(APP));
  check("…and Enter in either field submits it",
        /onEnter\("ao-user", \(\) => \{ void inlineSignIn\(\); \}\)/.test(APP)
        && /onEnter\("ao-pass", \(\) => \{ void inlineSignIn\(\); \}\)/.test(APP));
  // There is no email on an account here, so there is nothing to send a reset
  // to. A link that goes nowhere is worse than saying so.
  check("FORGOT PASSWORD says the true thing instead of going nowhere",
        /\$a\("ao-forgot"\)\.addEventListener\("click", \(\) => setAuthMsg\("auth-choose-err",[\s\S]{0,220}?no email address/.test(APP));
  check("the card is a child of the step, so showStep hides it with the screen",
        /<div id="auth-step-choose"[\s\S]*?<div id="auth-account-overlay"/.test(HTML));
  check("…and showStep really does close it",
        /\["auth-guest-overlay", "auth-account-overlay"\]\.forEach/.test(APP));
  check("it announces itself as a dialog",
        /id="auth-account-overlay" role="dialog" aria-modal="true"/.test(HTML));
  check("it wears the guest card's clothes rather than a second set",
        /#auth-guest-overlay \.ago-card,\s*\n\s*#auth-account-overlay \.ago-card/.test(CSS));
}

console.log("\nit asks first, then shows the form for the answer");
{
  ["aao-pane-ask", "aao-pane-signin", "aao-pane-create"].forEach(id =>
    check(`the ${id.replace("aao-pane-", "")} pane exists`, new RegExp(`id="${id}"`).test(HTML)));
  check("one function decides which pane is showing",
        /function ccAccountPane\(which\)/.test(APP));
  check("Google is offered from BOTH forms",
        /id="aao-in-google"/.test(HTML) && /id="aao-new-google"/.test(HTML));
  check("…through the same two functions the chooser used to call",
        /beginGameWindowGoogleSignIn\(errId\)[\s\S]{0,200}?beginCleanGoogleSignIn\(errId\)/.test(APP));
  check("a password field is a password field",
        (HTML.match(/type="password"/g) || []).length >= 3);
  check("closing the card does not leave a password sitting in the page",
        /\["aao-in-pass", "aao-new-pass", "aao-new-pass2"\]\.forEach/.test(APP));
  check("Escape closes it, a click on the scrim does not",
        /if \(!ol\.classList\.contains\("visible"\)\) return;/.test(APP)
        && !/auth-account-overlay"\)\.addEventListener\("click"/.test(APP));
}

console.log("\nthe green bar is the rule, not a decoration");
{
  check("there is a bar", /id="aao-meter"/.test(HTML) && /aao-meter-fill/.test(HTML));
  check("it turns green at 3 and stays green at 4",
        /\.aao-meter\[data-s="3"\] \.aao-meter-fill \{[^}]*background: #4fae54/.test(CSS)
        && /\.aao-meter\[data-s="4"\] \.aao-meter-fill \{[^}]*background: #1e9b62/.test(CSS));
  check("…and is red then amber below it",
        /\.aao-meter\[data-s="1"\] \.aao-meter-fill \{[^}]*background: #d4553f/.test(CSS)
        && /\.aao-meter\[data-s="2"\] \.aao-meter-fill \{[^}]*background: #e0932c/.test(CSS));
  check("the button is gated on the same score the bar paints",
        /go\.disabled = !\(raw && r\.score >= CC_PW_MIN_SCORE\)/.test(APP));
  check("…and the submit re-checks it, so a re-enabled button cannot smuggle one through",
        /if \(strength\.score < CC_PW_MIN_SCORE\)/.test(APP));
  check("a disabled Create Account looks disabled",
        /#auth-account-overlay \.pv-btn\.gold:disabled/.test(CSS));
  check("the two passwords have to match", /if \(pass !== pass2\)/.test(APP));
}

console.log("\na username is an account");
{
  check("the username becomes the login address",
        /const ccLoginEmail = \(name\) =>/.test(APP)
        && /CC_LOGIN_DOMAIN = "players\.currentsandcritters\.com"/.test(APP));
  check("…so 'that username is taken' is Firebase's answer, not a query's",
        /"auth\/email-already-in-use": "That username is taken/.test(APP));
  check("the login name is stricter than a nickname, because an address cannot hold a space",
        /function validateLoginName\(name\)/.test(APP)
        && /no spaces/.test(APP));
  check("a synthetic address is never filed as an email anybody can be reached at",
        /isPasswordAccount \? "" : \(_authUser\.email \|\| ""\)/.test(APP));
  check("…and the name they type to get back in is stored, and shown in Settings",
        /login_username: nick/.test(APP) && /"Sign in as " \+ loginName/.test(APP));
  check("account creation reuses the one path an account is born on",
        /const staged = takePendingSignup\(\);[\s\S]{0,400}?await finishNicknameSetup\(staged\.nick\)/.test(APP));
  check("…and the chosen name survives the launcher's navigation into the game window",
        /CC_PENDING_SIGNUP_KEY = "cc_pending_signup_v1"/.test(APP)
        && /localStorage\.setItem\(CC_PENDING_SIGNUP_KEY/.test(APP));
  check("a server without Email/Password switched on says exactly that",
        /Enable Email\/Password in Firebase console/.test(APP));
}

console.log("\na guest keeps nothing, and loses nothing by accident");
{
  check("signing out erases the guest's own progress",
        /function signOutGuestForGood\(\)/.test(APP)
        && /if \(wasGuest\) purgeGuestData\(\);/.test(APP));
  check("…both Sign Out buttons go through it",
        (APP.match(/signOutGuestForGood\(\);/g) || []).length >= 2);
  check("…and it says so first, when there is something to lose",
        /function confirmGuestSignOut\(\)/.test(APP) && /if \(!games\) return true;/.test(APP));
  // The bug this replaced: clearGuestSessionStorage ran at the START of every
  // sign-in attempt, so closing the Google window cost a guest their session.
  check("a sign-in ATTEMPT is not a sign-out",
        /function clearGuestSessionStorage\(\)[\s\S]{0,900}?LAST_GUEST_NICK_KEY, nick/.test(APP)
        && !/function clearGuestSessionStorage\(\) \{[\s\S]{0,200}?purgeGuestData\(\)/.test(APP));
  check("…so PLAY AS GUEST hands the nickname back",
        /localStorage\.getItem\(LAST_GUEST_NICK_KEY\)/.test(APP));
  check("the device-wide caches are only swept for a GUEST sign-out",
        /function purgeGuestProgressBlobs\(\)/.test(APP)
        && /purgeGuestData\(\) \{\s*\n\s*purgeGuestProgressBlobs\(\);/.test(APP));
  check("…and arriving in an account ends the guest session for real",
        (APP.match(/purgeGuestProgressBlobs\(\);/g) || []).length >= 3);
  check("Player Home says what a guest session is worth",
        /erased the moment you sign out/.test(HTML));
}

console.log("\na guest can take their afternoon with them");
{
  check("the offer is at the end of a game", /id="pv-endgame-signup"/.test(HTML));
  check("…for guests only, and never inside a tournament match",
        /function _syncEndgameGuestButton\(\)/.test(APP)
        && /btn\.style\.display = \(isGuest && !inTourney\)/.test(APP));
  check("…and it leaves the game the way BACK TO LOBBY leaves it",
        /pv-endgame-signup"\)\?\.addEventListener[\s\S]{0,700}?returnToMenu\(\);/.test(APP));
  check("the session is photographed BEFORE the sign-in that ends it",
        /stageGuestMigration\(\);\s*\n\s*stagePendingSignup\(nick, ref\);\s*\n\s*clearGuestSessionStorage\(\);/.test(APP));
  check("…and it survives the trip through a page navigation",
        /GUEST_MIGRATE_KEY = "cc_guest_migration_v1"/.test(APP));
  check("nothing is carried for a guest who never finished a game",
        /if \(!stats \|\| Number\(stats\.completed_games \|\| 0\) <= 0\) return false;/.test(APP));
  check("it is poured into a NEW profile only",
        /await applyGuestMigration\(_authUser\.uid\)/.test(APP)
        && /clearGuestMigration\(\);\s*\n\s*purgeGuestProgressBlobs\(\);/.test(APP));
  check("…through the write that can never downgrade an account",
        /await safeWriteProfile\(uid, icons\.length \? \{ stats: clean/.test(APP));
  check("…and a hand-edited blob cannot write NaN into a live account",
        /if \(typeof v === "number"\) \{ if \(Number\.isFinite\(v\)\) dst\[k\] = v; \}/.test(APP));
  check("a staged snapshot expires rather than waiting around for a stranger",
        /GUEST_MIGRATE_TTL_MS/.test(APP));
}

console.log("\nthe Store stopped protesting its own innocence");
{
  check("the 'Stripe never sees your card' paragraph is gone",
        !/Checkout is hosted securely by/.test(APP));
  check("…and so is the bit that assumed everyone signs in with Google",
        !/linked to your signed-in Google account/.test(APP));
  check("nothing else in the game tells a player to use Google specifically",
        !/Sign in with Google/.test(APP)
        && !/Sign in with Google/.test(read("js/level-pass.js"))
        && !/Sign in with Google/.test(read("js/referral.js"))
        && !/Sign in with Google/.test(read("js/discord-reward.js")));
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

  const HELPERS = `
  function R(sel){ var e=document.querySelector(sel); if(!e) return null;
    var r=e.getBoundingClientRect();
    return {l:r.left,t:r.top,w:r.width,h:r.height,r:r.right,b:r.bottom}; }
  function vis(id){ var e=document.getElementById(id);
    return !!e && getComputedStyle(e).display !== "none"; }
  function cardOpen(){ var o=document.getElementById("auth-account-overlay");
    return !!o && o.classList.contains("visible"); }
  function typePw(v){ var p=document.getElementById("aao-new-pass");
    p.value=v; p.dispatchEvent(new Event("input")); }
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
  ${HELPERS}
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

  // Chrome will not open a window narrower than about 500px, so the smallest
  // real phone is measured at the width its layout viewport actually gets.
  const SIZES = [[1440, 900], [1920, 1080], [1280, 800], [1024, 768], [500, 900], [820, 1180], [900, 520]];

  try {
    // ── 1. Every way in is a real, whole, reachable control ───────────────
    // There is nothing to "land on" any more: the buttons are not boxes over
    // paint, they are buttons. What can still go wrong is a control that is
    // clipped, off the screen, or pushing the page sideways at some size
    // nobody opened, so that is what is measured, at seven of them.
    console.log("\nevery way in is a real control at every window size");
    for (const [w, h] of SIZES) {
      const r = run("_acc_cover.html", `
        var ids = ["ao-user","ao-pass","ao-signin","auth-choose-google-btn","auth-guest-btn","ao-create"];
        log.ctl = {};
        ids.forEach(function (id) {
          var e = document.getElementById(id);
          if (!e) { log.ctl[id] = null; return; }
          var b = e.getBoundingClientRect(), cs = getComputedStyle(e);
          log.ctl[id] = {
            w: Math.round(b.width), h: Math.round(b.height),
            shown: cs.display !== "none" && cs.visibility !== "hidden",
            inside: b.left >= -1 && b.right <= window.innerWidth + 1,
            ovf: e.scrollWidth > e.clientWidth + 1,
          };
        });
        // The tap target for a field is the row it sits in, not the bare input:
        // the input is one line of text inside a 60px row.
        log.rows = Array.prototype.map.call(document.querySelectorAll("#auth-step-choose .ao-field"),
          function (e) { return Math.round(e.getBoundingClientRect().height); });
        log.guestName = document.getElementById("auth-guest-btn").textContent.trim();
        log.acctName  = document.getElementById("auth-choose-google-btn").textContent.trim();
        log.art = R(".ao-art-img");
        log.docW = document.documentElement.scrollWidth;
        log.vw = window.innerWidth;
        done();
      `, w, h, (r) => r.ctl && r.ctl["ao-signin"]);
      if (!r || !r.ctl) { check(`${w}x${h}: the harness reached the sign-in screen`, false); continue; }

      Object.keys(r.ctl).forEach((id) => {
        const c = r.ctl[id];
        check(`${w}x${h}: ${id} is there, whole, and on the screen`,
              !!c && c.shown && c.w >= 40 && c.h >= 16 && c.inside && !c.ovf,
              c ? `${c.w}x${c.h} inside=${c.inside} clipped=${c.ovf}` : "missing");
      });
      check(`${w}x${h}: both fields are a row big enough to tap`,
            r.rows && r.rows.length === 2 && r.rows.every((x) => x >= 44), String(r.rows));
      check(`${w}x${h}: the painting is on the screen too`, !!r.art && r.art.w > 0 && r.art.h > 0);
      check(`${w}x${h}: both buttons still say what they do`,
            r.guestName === "Play as Guest" && r.acctName === "Continue with Google",
            `${r.guestName} / ${r.acctName}`);
      check(`${w}x${h}: nothing pushes the page sideways`, r.docW <= r.vw + 1,
            `${r.docW} > ${r.vw}`);
    }

    // ── 2. CREATE AN ACCOUNT opens the card ───────────────────────────────
    console.log("\nCREATE AN ACCOUNT opens the card");
    {
      const r = run("_acc_open.html", `
        setTimeout(function () {
          log.before = cardOpen();
          document.getElementById("ao-create").click();
          log.after = cardOpen();
          log.ask    = vis("aao-pane-ask");
          log.signin = vis("aao-pane-signin");
          log.create = vis("aao-pane-create");
          // The ways in underneath must be inert while the card is up.
          log.guestInert = getComputedStyle(document.getElementById("auth-guest-btn")).pointerEvents === "none";
          // …and the card, not the artwork, owns the pixel at its own centre.
          var c = R("#auth-account-overlay .ago-card");
          var el = document.elementFromPoint(c.l + c.w / 2, c.t + 12);
          log.topAtCard = !!(el && el.closest && el.closest("#auth-account-overlay"));
          done();
        }, 1500);
      `, 1440, 900, (r) => r.after !== undefined);
      check("the card is shut until it is asked for", r && r.before === false);
      check("…the button opens it", r && r.after === true);
      // Straight to the form that makes an account: the player answered the
      // question by clicking a button that says which answer it is.
      check("…on the form that makes one, not back on the question",
            r && r.create === true && r.ask === false && r.signin === false);
      check("…with the ways in underneath held inert", r && r.guestInert === true);
      check("…and it is really in front", r && r.topAtCard === true);
    }

    // ── 3. Both answers reach their form, and Back comes back ─────────────
    console.log("\nboth answers reach a form, and Back really returns");
    {
      const r = run("_acc_panes.html", `
        setTimeout(function () {
          document.getElementById("auth-choose-google-btn").click();
          document.getElementById("aao-go-signin").click();
          log.signin = vis("aao-pane-signin");
          log.signinHasGoogle = !!document.querySelector("#aao-pane-signin #aao-in-google");
          document.getElementById("aao-in-back").click();
          log.backToAsk = vis("aao-pane-ask");
          document.getElementById("aao-go-create").click();
          log.create = vis("aao-pane-create");
          log.createHasGoogle = !!document.querySelector("#aao-pane-create #aao-new-google");
          // Only ever one pane on screen.
          log.onlyOne = ["aao-pane-ask","aao-pane-signin","aao-pane-create"].filter(vis).length;
          document.getElementById("aao-ask-back") && null;
          document.getElementById("aao-new-back").click();
          document.getElementById("aao-ask-back").click();
          log.closed = !cardOpen();
          log.guestLive = getComputedStyle(document.getElementById("auth-guest-btn")).pointerEvents !== "none";
          done();
        }, 1500);
      `, 1440, 900, (r) => r.signin !== undefined);
      check("'I have an account' opens the sign-in form", r && r.signin === true);
      check("…with Google under it", r && r.signinHasGoogle === true);
      check("Back returns to the question", r && r.backToAsk === true);
      check("'Create an account' opens the create form", r && r.create === true);
      check("…with Google under that too", r && r.createHasGoogle === true);
      check("only one pane is ever on screen", r && r.onlyOne === 1);
      check("backing all the way out closes the card", r && r.closed === true);
      check("…and gives the buttons underneath back", r && r.guestLive === true);
    }

    // ── 4. The green bar and the button are the same rule ─────────────────
    console.log("\nthe bar and the button are one rule, measured together");
    {
      const r = run("_acc_meter.html", `
        setTimeout(function () {
          document.getElementById("auth-choose-google-btn").click();
          document.getElementById("aao-go-create").click();
          var bar = document.getElementById("aao-meter");
          var go  = document.getElementById("aao-new-go");
          function probe(pw){
            typePw(pw);
            return { s: bar.dataset.s, off: go.disabled,
                     fill: getComputedStyle(document.getElementById("aao-meter-fill")).backgroundColor,
                     word: document.getElementById("aao-meter-word").textContent };
          }
          log.empty  = probe("");
          log.short  = probe("fish");
          log.easy   = probe("password123");
          log.plain  = probe("kelpforest");
          log.good   = probe("Kelpforest7");
          log.strong = probe("Kelpforest7!x");
          // The rules, asked by name.
          log.scores = ["", "fish", "password123", "kelpforest", "Kelpforest7", "Kelpforest7!x"]
            .map(function(p){ return window.__ccPwScore(p).score; });
          log.names = {
            spaces: window.__ccLoginNameCheck("tide pool"),
            short:  window.__ccLoginNameCheck("ab"),
            edge:   window.__ccLoginNameCheck(".tidepool"),
            good:   window.__ccLoginNameCheck("tide_pool-9"),
          };
          log.email = window.__ccLoginEmail("TidePool");
          done();
        }, 1500);
      `, 1440, 900, (r) => r.strong !== undefined);
      const ok = !!(r && r.strong);
      check("an empty field offers nothing to submit", ok && r.empty.off === true);
      check("…and neither does a short one", ok && r.short.off === true && r.short.s === "0");
      check("a password off every guessing list is pinned to the bottom",
            ok && r.easy.s === "1" && r.easy.off === true, ok && r.easy.word);
      check("a long plain word is not enough either", ok && r.plain.off === true, ok && r.plain.s);
      check("a real one unlocks the button", ok && r.good.off === false, ok && r.good.s);
      check("…at exactly the point the bar turns green",
            ok && r.good.s === "3" && /79,\s*174,\s*84/.test(r.good.fill), ok && r.good.fill);
      check("…and a stronger one fills it", ok && r.strong.s === "4" && r.strong.off === false);
      // Pinned outright, because "roughly stronger" is how a strength meter
      // ends up calling a lower-case dictionary word "good". Empty and a
      // 4-letter word score 0 (too short); "password123" and "kelpforest"
      // both score 1, one for being on every list and one for being a single
      // run of lower case; only mixing kinds and length gets past the gate.
      check("the scores hold, weakest to strongest",
            ok && JSON.stringify(r.scores) === JSON.stringify([0, 0, 1, 1, 3, 4]),
            ok && JSON.stringify(r.scores));
      check("a login name cannot hold a space", ok && /no spaces/.test(r.names.spaces || ""));
      check("…or be two characters", ok && /at least 3/.test(r.names.short || ""));
      check("…or start on a dot", ok && /Start and end/.test(r.names.edge || ""));
      check("…and an ordinary one is fine", ok && r.names.good === "");
      check("the username becomes one lower-case address",
            ok && r.email === "tidepool@players.currentsandcritters.com", ok && r.email);
    }

    // ── 5. The create form fits on the screen it opens on ─────────────────
    console.log("\nCreate Account is on the screen, not below a scroll");
    for (const [w, h] of [[1440, 900], [1280, 800], [1024, 768], [500, 900]]) {
      const r = run("_acc_fold.html", `
        setTimeout(function () {
          document.getElementById("auth-choose-google-btn").click();
          document.getElementById("aao-go-create").click();
          log.go   = R("#aao-new-go");
          log.card = R("#auth-account-overlay .ago-card");
          log.vh   = window.innerHeight;
          log.vw   = window.innerWidth;
          log.docW = document.documentElement.scrollWidth;
          log.refOpen = vis("aao-ref-wrap");
          done();
        }, 1500);
      `, w, h, (r) => r.go);
      if (!r || !r.go) { check(`${w}x${h}: the create form rendered`, false); continue; }
      check(`${w}x${h}: Create Account is visible without scrolling the card`,
            r.go.b <= r.card.b + 1 && r.go.b <= r.vh + 1,
            `button bottom ${Math.round(r.go.b)}, card bottom ${Math.round(r.card.b)}, window ${r.vh}`);
      check(`${w}x${h}: the friend code stays folded away until asked for`, r.refOpen === false);
      check(`${w}x${h}: the card fits sideways`, r.docW <= r.vw + 1);
    }

    // ── 6. What a guest is told, and what happens to them ─────────────────
    console.log("\nthe guest card hands a returning guest their name back");
    {
      const r = run("_acc_guest.html", `
        setTimeout(function () {
          localStorage.setItem("cc_last_guest_nick", "Tidepool");
          document.getElementById("auth-guest-btn").click();
          log.prefill = document.getElementById("auth-guest-nick").value;
          log.count   = document.getElementById("auth-guest-count").textContent;
          done();
        }, 1500);
      `, 1440, 900, (r) => r.prefill !== undefined);
      check("PLAY AS GUEST remembers the last nickname used here",
            r && r.prefill === "Tidepool", r && r.prefill);
      check("…and the counter agrees with it", r && r.count === "8 / 15", r && r.count);
    }
  } finally {
    server.kill();
    tmp.forEach((f) => { try { fs.unlinkSync(f); } catch (_) {} });
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
