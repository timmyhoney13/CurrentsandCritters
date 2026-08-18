#!/usr/bin/env node
/* Tutorials — every step of every tour has to actually work.
 *
 * The bug that prompted this file: the Main Menu Tour's History step handed the
 * learner a "View a sample match" button and then refused to advance until they
 * had tapped all three opponents inside the match modal — with the tutorial
 * popup sitting over the very chips it told them to tap. Next stayed disabled,
 * so the only way out was Skip, which throws away the whole tutorial. "It
 * doesn't work and is confusing" is exactly right, and no source grep finds it:
 * the selectors are valid, the handlers are wired, only the running tour is
 * broken.
 *
 * Two halves:
 *   • SOURCE — the invariants that keep a step honest. Chiefly: an interactive
 *     step must never be able to trap the player (there is a timer that
 *     un-disables Next), and every hard-coded CSS id a step points at has to
 *     exist in the real markup.
 *   • DRIVE — the REAL preview.html, real CSS, real preview-app.js in headless
 *     Chrome, walking the Main Menu and Competitive tours the way a player
 *     does: press Next when it is offered, otherwise click whatever the
 *     spotlight is on. Asserted for the two tours that need no game server, as
 *     a guest AND signed in (the app locks a lot of the menu behind sign-in),
 *     and at five window widths.
 *
 * Run:  node test_tutorials.js        (drive half needs Google Chrome)
 */
"use strict";

const fs   = require("fs");
const os   = require("os");
const path = require("path");
const { execFileSync, spawn } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const TUT  = read("js/tutorials.js");
const HTML = read("preview.html");
const APP  = read("js/preview-app.js");

let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name); }
}

// ════════════════════════════════════════════════════════════════════════
//  SOURCE
// ════════════════════════════════════════════════════════════════════════
console.log("\nno step can trap the player");

// An interactive step disables Next until the player performs the action. If
// the action is impossible — a screen gated behind sign-in, a rigged card the
// server did not deal, a control that never rendered — that is a dead end.
check("interactive steps get a timer that re-enables Next", /coachStuck\s*=\s*setInterval/.test(TUT));
check("the timer un-disables Next rather than auto-advancing",
      /b\.disabled\s*=\s*false;\s*b\.textContent\s*=\s*"Skip this step →"/.test(TUT));
check("a step with nothing on screen to click gives up sooner",
      /STUCK_NO_TARGET_MS\s*=\s*\d+/.test(TUT) && /STUCK_WITH_TARGET_MS\s*=\s*\d+/.test(TUT));
{
  const noTarget = Number((/STUCK_NO_TARGET_MS\s*=\s*(\d+)/.exec(TUT) || [])[1]);
  const withTarget = Number((/STUCK_WITH_TARGET_MS\s*=\s*(\d+)/.exec(TUT) || [])[1]);
  check(`the no-target wait (${noTarget}ms) is shorter than the with-target wait (${withTarget}ms)`,
        noTarget > 0 && withTarget > noTarget);
}
check("the timer is cleared when the tour ends", /if \(coachStuck\) \{ clearInterval\(coachStuck\)/.test(TUT));

console.log("\nsteps that cannot apply are skipped, not shown broken");

// A guest is refused the Avatar Gallery outright (two separate guards in the
// app), and the Friends card is hidden behind the guest gate — so the steps
// built on them are skipped rather than left to stall.
check("the app really does refuse a guest the gallery (openAvatarGallery)",
      /Guests can't open the avatar collection[\s\S]{0,80}if \(!_authUser\)/.test(APP));
check("...and again on the avatar's own click handler",
      /only signed-in players open the gallery\.\s*\n\s*if \(_authUser\)/.test(APP));
check("Friends is one of the guest-gated panels", /GUEST_GATE_MSGS = \{[\s\S]{0,400}?friends:/.test(APP));
check("the engine understands step.skipIf", /function coachSkipped\(step\)/.test(TUT));
check("skipping works travelling backwards too", /function coachNextIdx\(from, back\)/.test(TUT));
check("the tour detects a guest from the app's own auth bridge",
      /function tutIsGuest\(\)[\s\S]{0,160}window\.__fishAuthUser/.test(TUT));
check("__fishAuthUser is a real export", /window\.__fishAuthUser\s*=\s*\(\)\s*=>\s*_authUser/.test(APP));
check("the gallery steps are guest-skipped", (TUT.match(/skipIf: tutIsGuest/g) || []).length >= 6);
check("a guest is told up front what is locked", /skipIf: \(\) => !tutIsGuest\(\)/.test(TUT));
check("Step N of M counts only the steps this player is shown",
      /const live = coachLiveIdxs\(\);[\s\S]{0,220}Step \$\{pos \+ 1\} of \$\{live\.length\}/.test(TUT));
check("'Finish ✓' is decided by the last LIVE step, not the array length",
      /function coachIsLast\(i\)/.test(TUT) && !/i >= coachSteps\.length - 1/.test(TUT));

console.log("\nthe History step no longer gates on clicking into a game");

check("the sample match opens itself", /before: \(\) => \{ closeMenuOverlays\(\); showSampleMatch\(\); \}/.test(TUT));
check("Next is available straight away on it", /title: "A Real Past Match",\s*\n\s*interactive: true, allowNext: true/.test(TUT));
check("the 'tap every opponent first' gate is gone", !/tutSampleAllOppsViewed/.test(TUT));
check("...and so is the state it needed", !/_tutSampleViewed/.test(TUT) && !/_tutSampleOpponents/.test(TUT));
check("the spotlight sits on the row of players", /target: "#ph-gdm-players"/.test(TUT));
check("#ph-gdm-players is what the modal really fills",
      /getElementById\("ph-gdm-players"\)/.test(APP) && /id="ph-gdm-players"/.test(HTML));
// Poking a card inside that board opens the full-screen card viewer, which then
// sits over every step that follows.
check("the card viewer is cleared up by closeMenuOverlays",
      /closeMenuOverlays[\s\S]{0,1400}?getElementById\("pv-zoom-modal"\)/.test(TUT));

console.log("\nsteps point at things that are actually on screen");

// The guide bar only exists while it is YOUR turn, and turn order is random.
check("the guide bar is only rendered on your turn (renderGuideBar)",
      /if \(!isMyTurn\) \{\s*\n\s*bar\.classList\.remove\("visible"\);/.test(APP));
check("...so no step hard-targets #pv-guide-bar", !/target: "#pv-guide-bar"/.test(TUT));
check("...it resolves the bar only when it is showing", /function gtGuideBarEl\(\)/.test(TUT));
check("...and the text explains the wait", /turn order is random/i.test(TUT));

// Every hard-coded id a step points at must exist in the markup.
const stepIds = [...TUT.matchAll(/target: "#([a-zA-Z0-9_-]+)"/g)].map(m => m[1]);
check(`every step id was collected (${stepIds.length} of them)`, stepIds.length > 30);
const missing = [...new Set(stepIds)].filter(id =>
  !HTML.includes(`id="${id}"`) && !APP.includes(`"${id}"`));
check(`no step points at an id that does not exist${missing.length ? " — " + missing.join(", ") : ""}`,
      missing.length === 0);

// Playing a card is not drag-only: the action dropdown does the same job, and
// on a phone it is the easier one.
check("card-play steps offer the dropdown as well as dragging",
      (TUT.match(/Choose action…/g) || []).length >= 4);

console.log("\nthe popup gets out of its own way");
check("it sits beside the target when it fits neither below nor above",
      /Sit\s*\n?\s*\/\/ BESIDE it if there is room|roomRight >= popW \|\| roomLeft >= popW/.test(TUT));
check("a step is re-positioned once async content lands", /coachSettle = setTimeout\(positionCoach/.test(TUT));

// ════════════════════════════════════════════════════════════════════════
//  DRIVE — the real app, in headless Chrome
// ════════════════════════════════════════════════════════════════════════
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found — skipping the drive half.");
} else {
  // The driver: pick a device, sign in as a guest, open the chooser, start a
  // tour, then behave like a player — press Next when it is offered, otherwise
  // click whatever the spotlight is sitting on. Records, for every step, the
  // things a player would notice: is anything highlighted, can I get past it,
  // is the popup covering the thing I am told to click, is it even on screen.
  const DRIVER = (tourKey) => `
<div id="out">PENDING</div>
<script>
(function () {
  // Under --virtual-time-budget the compositor never advances a CSS transition,
  // so every measured rect would be the one from the step before.
  var st = document.createElement("style");
  st.textContent = "*,*::before,*::after{transition:none!important;animation:none!important}html{scroll-behavior:auto!important}";
  document.head.appendChild(st);
  var _sIV = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = function (o) {
    try { return _sIV.call(this, { block: (o && o.block) || "center", behavior: "instant" }); }
    catch (e) { return _sIV.call(this, true); }
  };

  var log = [], out = document.getElementById("out");
  function q(s) { return document.querySelector(s); }
  function vis(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    var cs = getComputedStyle(el);
    return cs.display !== "none" && cs.visibility !== "hidden";
  }
  function click(el) { if (el) try { el.click(); } catch (e) {} }
  function finish() { out.textContent = JSON.stringify(log); }

  var phase = 0, tick = 0, guard = 0, lastStep = "";
  var iv = setInterval(function () {
    if (++tick > 4000) { log.push({ fatal: "timeout in phase " + phase }); finish(); clearInterval(iv); return; }
    try {
      // The full-screen launch splash covers the page until a real click.
      var spl = q("#cc-fs-splash");
      if (spl && getComputedStyle(spl).display !== "none") { click(q("#ccfs-window")); spl.style.display = "none"; }
      var dev = q("#cc-device-screen");
      if (dev && getComputedStyle(dev).display !== "none") {
        click(q('[data-cc-device="computer"]'));
        if (tick > 20) dev.classList.add("cc-device-hidden");
      }
      if (phase === 0) { phase = 1; return; }
      if (phase === 1) {
        // Firebase resolves over the network; don't wait on it to show the form.
        if (tick > 25) {
          var ls = q("#auth-loading-screen"); if (ls) ls.classList.add("hidden");
          var as = q("#auth-screen"); if (as) as.classList.remove("hidden");
        }
        var g = q("#auth-guest-btn");
        if (g && vis(g)) { click(g); phase = 2; }
        return;
      }
      if (phase === 2) {
        var go = q("#auth-guest-go-btn"), nk = q("#auth-guest-nick");
        if (go && vis(go)) {
          if (nk) { nk.value = "TutBot"; nk.dispatchEvent(new Event("input", { bubbles: true })); }
          click(go); phase = 3;
        }
        return;
      }
      if (phase === 3) {
        var lob = q("#auth-stats-lobby");
        if (lob && lob.classList.contains("visible")) {
          SIGNIN_HOOK
          if (typeof window.__openTutorialChooser !== "function") {
            log.push({ fatal: "__openTutorialChooser missing" }); finish(); clearInterval(iv); return;
          }
          window.__openTutorialChooser(); phase = 4;
        }
        return;
      }
      if (phase === 4) { var o = q('#tut3-chooser .tut3-opt[data-key="${tourKey}"]'); if (o) { click(o); phase = 5; } return; }

      var coach = q("#tut3-coach");
      if (!coach || !coach.classList.contains("open")) { log.push({ finished: true }); finish(); clearInterval(iv); return; }
      var countTxt = (q("#tut3-count") || {}).textContent || "";
      var title = (q("#tut3-title") || {}).textContent || "";
      var hole = q("#tut3-hole"), nextBtn = q("#tut3-next");
      var choices = document.querySelectorAll("#tut3-text [data-choice]");
      if (countTxt !== lastStep) { lastStep = countTxt; guard = 0; }
      guard++;
      if (guard < 8) return;                       // let the step settle
      if (guard === 8) {
        var hr = hole.getBoundingClientRect(), pop = q("#tut3-pop").getBoundingClientRect();
        var over = !(pop.right < hr.left || pop.left > hr.right || pop.bottom < hr.top || pop.top > hr.bottom);
        log.push({
          step: countTxt, title: title,
          hasTarget: !hole.classList.contains("nohole"),
          targetW: Math.round(hr.width), targetH: Math.round(hr.height),
          popCoversTarget: !hole.classList.contains("nohole") && over,
          popOffscreen: pop.left < -1 || pop.right > window.innerWidth + 1
                     || pop.top < -1 || pop.bottom > window.innerHeight + 1,
          nextDisabled: !!nextBtn.disabled,
        });
      }
      if (choices.length) { log.push({ finished: true, terminal: countTxt }); finish(); clearInterval(iv); return; }
      if (!nextBtn.disabled) { click(nextBtn); return; }
      if (guard === 9 && q("#tut3-cta")) { click(q("#tut3-cta")); return; }
      if (guard % 6 === 0 && guard <= 60) {
        var hr2 = hole.getBoundingClientRect();
        if (hr2.width > 2 && hr2.height > 2) {
          var el = document.elementFromPoint(hr2.left + hr2.width / 2, hr2.top + hr2.height / 2);
          while (el && typeof el.click !== "function") el = el.parentElement;
          if (el && !coach.contains(el)) { click(el); return; }
        }
      }
      if (guard > 62) {
        log.push({ stuck: countTxt + " — " + title });
        nextBtn.disabled = false; click(nextBtn);    // force on, so the rest is still audited
      }
    } catch (e) { log.push({ err: String(e && e.message) }); }
  }, 60);
})();
</script>`;

  // Serve the client directory: the app loads its scripts by absolute path.
  // It has to be a SEPARATE process — execFileSync below blocks this one's event
  // loop, so a server running in here would never answer a single request.
  const PORT = 8931 + (process.pid % 500);
  const SERVER_SRC = `
    const fs=require("fs"),path=require("path"),http=require("http");
    const ROOT=${JSON.stringify(CLIENT)};
    const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",
      ".json":"application/json",".png":"image/png",".jpg":"image/jpeg",
      ".webp":"image/webp",".svg":"image/svg+xml",".ico":"image/x-icon"};
    http.createServer((req,res)=>{
      const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
      const f=path.join(ROOT,rel);
      if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);res.end();return;}
      res.writeHead(200,{"Content-Type":MIME[path.extname(f)]||"application/octet-stream"});
      fs.createReadStream(f).pipe(res);
    }).listen(${PORT});
  `;

  // Two profiles. Guest is what the app hands a signed-out player. "Signed in"
  // has to be faked — the real gates read a module-scoped _authUser no test can
  // set — so the harness serves a copy of the app with exactly those three
  // gates opened, and tells the tour it is signed in.
  function harnessApp() {
    let a = APP;
    a = a.replace("      // Guests can't open the avatar collection, locked to the Mullet.\n      if (!_authUser) {",
                  "      // Guests can't open the avatar collection, locked to the Mullet.\n      if (false) {");
    a = a.replace("        // Guests are locked to the Mullet, only signed-in players open the gallery.\n        if (_authUser) {",
                  "        // Guests are locked to the Mullet, only signed-in players open the gallery.\n        if (true) {");
    a = a.replace(/      const GUEST_GATE_MSGS = \{[\s\S]*?\n      \};\n/, "      const GUEST_GATE_MSGS = {};\n");
    return a;
  }

  const tmp = [];
  function writeTmp(name, body) { const f = path.join(CLIENT, name); fs.writeFileSync(f, body); tmp.push(f); return name; }
  writeTmp("_tut_app.js", harnessApp());

  function drivePage(tourKey, signedIn) {
    let page = HTML;
    // Choose the device before any deferred script runs.
    page = page.replace('<script defer src="/js/device-select.js',
      '<script>try{sessionStorage.setItem("cc_device_type","computer");}catch(e){}</script>\n<script defer src="/js/device-select.js');
    if (signedIn) page = page.replace(/\/js\/preview-app\.js\?[^"]*/, "/_tut_app.js");
    return page + DRIVER(tourKey).replace("SIGNIN_HOOK",
      signedIn ? 'window.__fishAuthUser = function () { return { uid: "harness" }; };' : "");
  }

  function run(tourKey, signedIn, w, h) {
    const name = `_tut_drive_${tourKey}_${signedIn ? "in" : "guest"}.html`;
    writeTmp(name, drivePage(tourKey, signedIn));
    for (let attempt = 0; attempt < 4; attempt++) {
      const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", `--window-size=${w},${h}`, "--virtual-time-budget=400000",
        "--dump-dom", `http://localhost:${PORT}/${name}?game_window=1`],
        // Chrome writes a wall of unrelated GCM/web-app noise to stderr.
        { encoding: "utf8", maxBuffer: 64e6, stdio: ["ignore", "pipe", "ignore"] });
      const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
      const raw = m ? m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">") : "";
      if (!raw || raw === "PENDING") continue;
      let rows; try { rows = JSON.parse(raw); } catch (_) { continue; }
      // A network-timing miss in the sign-in screen is not a tutorial failure.
      if (rows.length === 1 && rows[0].fatal) continue;
      return rows;
    }
    return null;
  }

  const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore", detached: false });
  // Give it a moment to bind before the first page load.
  try { execFileSync(process.execPath, ["-e", "setTimeout(()=>{},700)"]); } catch (_) {}
  try {
    for (const [tourKey, tourName] of [["menu", "Main Menu Tour"], ["competitive", "Competitive 1v1"]]) {
      for (const signedIn of [false, true]) {
        const who = signedIn ? "signed in" : "as a guest";
        console.log(`\n${tourName}, ${who} (1440x900)`);
        const rows = run(tourKey, signedIn, 1440, 900);
        if (!rows) { check(`${tourName} ${who}: the harness reached the tour`, false); continue; }
        const steps = rows.filter(r => r.step);
        const stuck = rows.filter(r => r.stuck).map(r => r.stuck);
        const done  = rows.some(r => r.finished);

        check(`${tourName} ${who}: runs to the end`, done);
        check(`${tourName} ${who}: no step traps the player${stuck.length ? " — " + stuck.join(" | ") : ""}`,
              stuck.length === 0);
        check(`${tourName} ${who}: every step was reached (${steps.length} steps)`, steps.length >= 12);

        // A step that spotlights nothing is only fine when it is meant to talk
        // to the whole screen; a step that asks for a CLICK must have something
        // to click, or Next stays disabled and the player is stranded.
        const blind = steps.filter(s => s.nextDisabled && !s.hasTarget).map(s => s.title);
        check(`${tourName} ${who}: nothing to click is never asked for${blind.length ? " — " + blind.join(", ") : ""}`,
              blind.length === 0);

        const covered = steps.filter(s => s.nextDisabled && s.popCoversTarget).map(s => s.title);
        check(`${tourName} ${who}: the popup never covers the thing you must click${covered.length ? " — " + covered.join(", ") : ""}`,
              covered.length === 0);

        const off = steps.filter(s => s.popOffscreen).map(s => s.title);
        check(`${tourName} ${who}: the popup is always fully on screen${off.length ? " — " + off.join(", ") : ""}`,
              off.length === 0);

        // A spotlight thinner than a finger is not a spotlight.
        const slivers = steps.filter(s => s.hasTarget && (s.targetW < 24 || s.targetH < 10)).map(s => `${s.title} ${s.targetW}x${s.targetH}`);
        check(`${tourName} ${who}: no spotlight is a sliver${slivers.length ? " — " + slivers.join(", ") : ""}`,
              slivers.length === 0);

        if (tourKey === "menu") {
          const titles = steps.map(s => s.title);
          check(`${tourName} ${who}: the sample match is opened for the player`,
                titles.includes("A Real Past Match"));
          const sample = steps.find(s => s.title === "A Real Past Match");
          check(`${tourName} ${who}: ...and you can move on without touching it`,
                !!sample && sample.nextDisabled === false && sample.hasTarget === true);
          const gallery = titles.includes("Your Avatar Gallery");
          check(`${tourName} ${who}: the gallery steps are ${signedIn ? "shown" : "skipped"}`,
                gallery === signedIn);
          if (signedIn) {
            check(`${tourName} ${who}: the friend code is on screen for its step`,
                  !!steps.find(s => s.title === "Your Friend Code" && s.hasTarget));
          } else {
            check(`${tourName} ${who}: the guest is told what is locked`,
                  titles.includes("You are playing as a guest"));
          }
        }
      }
    }

    // ── Every width, not just the one the laptop happens to be ─────────────
    // A tour that passes at 1440 and strands a phone player is not fixed.
    console.log("\nMain Menu Tour at every width (signed in)");
    for (const [w, h, label] of [[1180, 900, "narrow laptop"], [1024, 900, "small laptop"],
                                 [820, 1100, "tablet"], [390, 844, "phone"]]) {
      const rows = run("menu", true, w, h);
      if (!rows) { check(`${label} ${w}x${h}: the harness reached the tour`, false); continue; }
      const steps = rows.filter(r => r.step);
      const stuck = rows.filter(r => r.stuck).map(r => r.stuck);
      check(`${label} (${w}x${h}): runs to the end with no step trapping the player${stuck.length ? " — " + stuck.join(" | ") : ""}`,
            rows.some(r => r.finished) && stuck.length === 0);
      check(`${label} (${w}x${h}): the popup stays on screen`,
            steps.every(s => !s.popOffscreen));
      check(`${label} (${w}x${h}): the popup never covers a required click`,
            steps.every(s => !(s.nextDisabled && s.popCoversTarget)));
    }
  } finally {
    try { server.kill(); } catch (_) {}
    tmp.forEach(f => { try { fs.unlinkSync(f); } catch (_) {} });
  }
}

console.log("\n" + "=".repeat(42));
console.log(`RESULT: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
