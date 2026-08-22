#!/usr/bin/env node
/* Tutorials, every step of every tour has to actually work.
 *
 * The bug that prompted this file: the Main Menu Tour's History step handed the
 * learner a "View a sample match" button and then refused to advance until they
 * had tapped all three opponents inside the match modal, with the tutorial
 * popup sitting over the very chips it told them to tap. Next stayed disabled,
 * so the only way out was Skip, which throws away the whole tutorial. "It
 * doesn't work and is confusing" is exactly right, and no source grep finds it:
 * the selectors are valid, the handlers are wired, only the running tour is
 * broken.
 *
 * Two halves:
 *   • SOURCE, the invariants that keep a step honest. Chiefly: an interactive
 *     step must never be able to trap the player (there is a timer that
 *     un-disables Next), and every hard-coded CSS id a step points at has to
 *     exist in the real markup.
 *   • DRIVE, the REAL preview.html, real CSS, real preview-app.js in headless
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
console.log("\na step is done, not skipped");

// A step used to un-disable its own Next after 15 seconds even when the thing
// it asked for was sitting right there under the spotlight, so "close the card
// viewer" could be skipped, and then the viewer covered every step after it.
// Now the countdown only runs while the target is NOT usable, which is the
// genuine dead end (gated behind sign-in, never rendered, disabled).
check("the escape is armed only while the target is unusable",
      /if \(coachIsUsable\(t\)\) \{ waited = 0; return; \}/.test(TUT));
check("...and it is no longer a per-step opt-in", !/mustAct/.test(TUT));
check("interactive steps still get the dead-end timer", /coachStuck\s*=\s*setInterval/.test(TUT));
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
check("a disabled control does not count as usable, so a real dead end still gives way",
      /function coachIsUsable\(el\)[\s\S]{0,220}?el\.disabled/.test(TUT));
check("the timer is cleared when the tour ends", /if \(coachStuck\) \{ clearInterval\(coachStuck\)/.test(TUT));

console.log("\n← Back takes you back to the step, not to a picture of it");

// Back used to hand over a step the player could read but not perform: the
// catch-layer went straight back to swallowing every click, so "click your
// avatar" on a back-navigated step did nothing at all.
check("a back-navigated step is still interactive",
      /const isInteractive = !!step\.interactive;/.test(TUT));
check("...so clicks still reach the page on it",
      /catchEl\.style\.pointerEvents = isInteractive \? "none" : "auto"/.test(TUT));
check("...and doing the action still advances the tour (the poll is armed, not skipped)",
      /if \(typeof step\.advanceWhen === "function"\) \{[\s\S]{0,400}?if \(goingBack\) \{ try \{ armed = !step\.advanceWhen\(\); \}/.test(TUT));
check("an already-satisfied condition does not bounce them forward again (the latch)",
      /if \(!armed\) \{ if \(!ok\) armed = true; positionCoach\(\); return; \}/.test(TUT));
check("Next is not re-locked on a step already done once",
      /const lockNext = isInteractive && !step\.allowNext && !goingBack;/.test(TUT));
check("...and going forward again re-locks it, so Back is not a way round a step",
      /\} else if \(lockNext\) \{/.test(TUT));

console.log("\nsteps that cannot apply are skipped, not shown broken");

// A guest is refused the Avatar Gallery outright (two separate guards in the
// app), and the Friends card is hidden behind the guest gate, so the steps
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
check(`no step points at an id that does not exist${missing.length ? ": " + missing.join(", ") : ""}`,
      missing.length === 0);

// Playing a card is not drag-only: the action dropdown does the same job, and
// on a phone it is the easier one.
check("card-play steps offer the dropdown as well as dragging",
      (TUT.match(/Choose action…/g) || []).length >= 4);

console.log("\nthe 'Click <which way> →' label points the right way");

// Next doubles as a signpost while an interactive step waits for the real
// click. It used to say "Click above" always, but the popup sits BELOW its
// target when there is room and ABOVE it when there is not, so on a laptop the
// waiting-room "Start Game" step pointed the player at the empty air above a
// button that was underneath the popup.
check("the label is not hard-coded to one direction",
      !/nextBtn\.textContent = "Click above →"/.test(TUT));
check("there is a label for each side", /POINTER_LABEL = \{[\s\S]{0,240}?above:[\s\S]{0,240}?below:[\s\S]{0,240}?left:[\s\S]{0,240}?right:/.test(TUT));
check("only positionCoach decides it, because only it has measured",
      /function coachSetPointer\(dir\)/.test(TUT));
// Every early return in positionCoach is a different placement, and each one
// has to say which way it just put the popup.
{
  const body = /function positionCoach\(\) \{[\s\S]*?\n  \}\n/.exec(TUT);
  const n = body ? (body[0].match(/coachSetPointer\(/g) || []).length : 0;
  const returns = body ? (body[0].match(/\n      return;|\n        return;/g) || []).length : 0;
  check(`every placement branch sets the pointer (${n} setters, ${returns} early returns)`,
        n >= returns + 1);
}
check("...and a step going backwards does not keep the old direction",
      /coachAwaitingAct = false;\n    clearCoachGlows\(\);/.test(TUT));
check("once it becomes 'Skip this step →' the direction stops overwriting it",
      /if \(b && b\.disabled\) b\.textContent = POINTER_LABEL\[coachPointer\]/.test(TUT));

console.log("\nthe step that starts the game has to be done, not skipped");

// Skipping "Start Game" leaves every following step pointing into a game that
// never started. It is now covered by the general rule (an interactive step
// cannot be skipped while its target is usable) rather than a per-step flag.
check("all three tours really do gate on Start Game",
      (TUT.match(/target: "#wr-start-btn"/g) || []).length === 3);
check("...and each of those steps is interactive",
      (TUT.match(/target: "#wr-start-btn", badge: "[^"]*", title: "Start the Game", interactive: true/g) || []).length === 3);
check("the button the gate waits on is the real one, and it does get enabled",
      /id="wr-start-btn"/.test(HTML) && /btn\.disabled = false;[\s\S]{0,200}?"Start Game"/.test(APP));
check("a gate still gives way when the room cannot start (non-host / not full)",
      /btn\.disabled = true;[\s\S]{0,120}?Waiting for host to start/.test(APP));

console.log("\nthe highlight shows WHICH card, not just that one is somewhere here");

// Against a dimmed table a 2px outline leaves the spotlighted card exactly the
// same colour as every card the player must not touch.
check("the spotlight fills its hole, not only its border",
      /#tut3-hole \{[^}]*background:rgba\(/.test(TUT) && /#tut3-hole \{[^}]*inset 0 0 \d+px/.test(TUT));
check("...and clears the fill when there is no target",
      /#tut3-hole\.nohole \{[^}]*background:transparent!important/.test(TUT));
check("a secondary glow ring is filled too",
      /\.tut3-glow-ring \{[^}]*background:rgba\(255,213,116/.test(TUT) && /\.tut3-glow-ring \{[^}]*inset 0 0 \d+px rgba\(255,213,116/.test(TUT));
check("neither highlight can swallow the click it is asking for",
      /#tut3-hole \{[^}]*pointer-events:none/.test(TUT) && /\.tut3-glow-ring \{[^}]*pointer-events:none/.test(TUT));

console.log("\n\"play this card\" also shows WHERE it goes");

check("the engine understands step.dragDemo", /function applyCoachDrags\(\)/.test(TUT));
check("the ghost carries the real card art", /const img = from\.querySelector\("img"\)/.test(TUT));
check("it flies from the hand card to the destination",
      /--tut3-dx/.test(TUT) && /--tut3-dy/.test(TUT) && /@keyframes tut3-drag-fly/.test(TUT));
check("it is rebuilt only when the two rects move, so it never freezes on frame 1",
      /if \(sig === _dragSig\) return;/.test(TUT));
check("it is repositioned with everything else", /applyCoachGlows\(\);\s*\n\s*applyCoachDrags\(\);/.test(TUT));
check("it is torn down with the tour", /clearCoachDrags\(\);/.test(TUT));
{
  const demos = (TUT.match(/dragDemo: \{/g) || []).length;
  check(`every guided play demonstrates the drag (${demos} of them)`, demos >= 6);
}
// A card goes in ONE of an Ocean's four spots. Glowing the whole hub says
// "one of these four", which is the question, not the answer.
check("the destination is a single lane, not the whole ocean",
      /function blReefLaneEl\(dir\)/.test(TUT) && /\.pv-lane-\$\{dir\}/.test(TUT));
check("...and lanes are what the board really renders",
      /lane\.className = `pv-lane-\$\{dir\}`/.test(APP));
check("Tutorial 2 asks the SERVER where its creature may go, rather than guessing",
      /function gtSlotElForEntry\(entryUid\)/.test(TUT) && /window\.__ccLegalActions/.test(TUT));
check("__ccLegalActions is a real export", /window\.__ccLegalActions = \(\)/.test(APP));
check("face_direction is really on a legal action", /String\(a\.face_direction\|\|""\)\.toLowerCase\(\)===dir/.test(APP));

console.log("\nthe tutorials say true things about the game");

// Playing a card ends your turn. The B-Lob tour used to stop after every play
// and tell the player to press End Turn, an instruction for a button that had
// already done its job, three times in one tutorial.
check("no B-Lob step tells the player to end a turn the game already ended",
      !/title: "End Your Turn"[^}]*badge: "Turn/.test(TUT) && (TUT.match(/title: "End Your Turn"/g) || []).length === 1);
check("the one surviving End Turn step is the LESSON, not an instruction",
      /title: "End Your Turn",\s*\n\s*text: "Most of the time you never touch this\./.test(TUT));
check("...and it names the two cards that actually need it",
      /Loggerhead Sea Turtle<\/strong> and <strong>Hermit Crab/.test(TUT));
{
  // Those two, and only those two, are what the server calls an open play
  // window, the same line test_end_turn_callout_cards.py pins from the deck.
  const server = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");
  check("...which is the same rule the server enforces",
        /is_open_play_window = bool\(fish\.has_multi_play_window\(player\)\)/.test(server));
}
check("the player is told what to do instead: nothing, the bots are playing",
      /title: "Waiting for the Others"/.test(TUT) && /ends your turn for you/.test(TUT));
check("a play step says live whether it is even your turn yet",
      /function tutTurnNote\(\)/.test(TUT) && (TUT.match(/liveNote: tutTurnNote/g) || []).length >= 4);
check("...and that line keeps ticking on a back-navigated step",
      /coachTick = setInterval\(applyCoachLive, \d+\)/.test(TUT));
check("...and is cleared with the tour", /if \(coachTick\) \{ clearInterval\(coachTick\); coachTick = null; \}/.test(TUT));

// A card is two animals. Nothing in any tutorial said so.
check("the two-sided deck is explained", /title: "Every Card Is Two Animals"/.test(TUT));
check("...including what the side decides about placement",
      /title: "Which Side, Which Spot"/.test(TUT) && /Ocean Floor<\/strong> \(the bottom spot/.test(TUT));
check("Surf's Up gets a real explanation, not one line",
      /title: "Surf's Up!!"/.test(TUT) &&
      /nobody can vote you afk/i.test(TUT) && /your turn parks/i.test(TUT));
check("the card viewer's ✕ is highlighted when the step says to click it",
      /glow: \["#pv-zoom-close"\], badge: "Cards", title: "Flip & Close"/.test(TUT));
check("...and #pv-zoom-close is the real close button", /id="pv-zoom-close"/.test(HTML));

console.log("\nno em dashes in anything the player reads");
{
  const bad = [];
  const re = /(?:text|title|badge|label|cta)\s*:\s*"((?:[^"\\]|\\.)*)"/g;
  let m;
  while ((m = re.exec(TUT))) if (/[—–]/.test(m[1])) bad.push(m[1].slice(0, 60));
  check(`no step text uses an em or en dash${bad.length ? ": " + bad.join(" | ") : ""}`, bad.length === 0);
}

console.log("\nthe tutorial room code looks like a room code");
{
  // "TUT" + a base-36 timestamp, sliced to the 12-char maximum, was the first
  // room code every learner ever saw, more than twice the length of a real one.
  check("the tutorial no longer mints a 12-character code", !/\("TUT" \+ tutSuffix\)/.test(APP));
  check("it uses the house length instead", /rid = freshRoomCode\(5\);/.test(APP));
  const real = /let rid = Math\.random\(\)\.toString\(36\)\.slice\(2,(\d)\)\.toUpperCase\(\)/.exec(APP);
  check(`...which is the same length an ordinary room gets (${real ? Number(real[1]) - 2 : "?"})`,
        !!real && Number(real[1]) - 2 === 5);
  check("the server agrees that is the house length", /ROOM_ID_LENGTH = 5/.test(fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8")));
  check("...and it is still a valid room id (4-12 uppercase alphanumerics)",
        /const n = Math\.max\(4, Math\.min\(12,/.test(APP));
  check("the tutorial room is still private, which is what keeps strangers out",
        /if \(_isTutGame\) \{[\s\S]{0,1200}?visibility = "private";/.test(APP));
  // The Online tour stops on the code box and says "this is your room code".
  // Overriding it anyway meant the very next screen showed a different one.
  check("a code the tutorial showed the player is the code the room gets",
        /const tutKeepsTypedCode = \(visibility === "private"\);/.test(APP) &&
        /if \(!tutKeepsTypedCode\) rid = freshRoomCode\(5\);/.test(APP));
  check("...and the tour puts a real 5-character code in that box",
        /function tutRoomCode\(\)/.test(TUT) && /new Uint32Array\(5\)/.test(TUT));
  check("...the same one for the code step and the create step",
        (TUT.match(/tutFillRoomCode/g) || []).length >= 3 && !/FISHY/.test(TUT));
  check("...regenerated per run, so taking the tour twice cannot reuse a live code",
        /_tutRoomCode = "";/.test(TUT));
}

console.log("\nthe tutorial ends by ending");
{
  check("no tutorial offers to keep playing the rigged practice game",
        !/Keep Playing/.test(TUT));
  check("...and the terminal choice menu is gone with it",
        !/t3-choice/.test(TUT) && !/step\.choices/.test(TUT));
  check("Tutorial 2 finishes through the normal Finish button",
        /runCoach\(GAME_STEPS, async \(\) => \{[\s\S]{0,200}?setDone\("game"\)/.test(TUT));
  check("Tutorial 3 too", /runCoach\(BLOB_STEPS, async \(\) => \{[\s\S]{0,200}?setDone\("practice"\)/.test(TUT));
  check("...and both leave the practice match behind",
        (TUT.match(/if \(window\.__tutLeaveGame\) await window\.__tutLeaveGame\(\)/g) || []).length >= 3);
}

console.log("\nthe popup gets out of its own way");
check("it sits beside the target when it fits neither below nor above",
      /Sit\s*\n?\s*\/\/ BESIDE it if there is room|roomRight >= popW \|\| roomLeft >= popW/.test(TUT));
check("a step is re-positioned once async content lands", /coachSettle = setTimeout\(positionCoach/.test(TUT));

// ════════════════════════════════════════════════════════════════════════
//  DRIVE, the real app, in headless Chrome
// ════════════════════════════════════════════════════════════════════════
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: skipping the drive half.");
} else {
  // The driver: pick a device, sign in as a guest, open the chooser, start a
  // tour, then behave like a player: press Next when it is offered, otherwise
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
        // Which way IS the target, measured, so the "Click above →" label can
        // be checked against reality rather than against the source.
        var side = "none";
        if (!hole.classList.contains("nohole")) {
          if (pop.top >= hr.bottom - 1) side = "above";
          else if (pop.bottom <= hr.top + 1) side = "below";
          else if (pop.left >= hr.right - 1) side = "left";
          else if (pop.right <= hr.left + 1) side = "right";
          else side = "overlap";
        }
        log.push({
          step: countTxt, title: title,
          hasTarget: !hole.classList.contains("nohole"),
          targetW: Math.round(hr.width), targetH: Math.round(hr.height),
          popCoversTarget: !hole.classList.contains("nohole") && over,
          popOffscreen: pop.left < -1 || pop.right > window.innerWidth + 1
                     || pop.top < -1 || pop.bottom > window.innerHeight + 1,
          nextDisabled: !!nextBtn.disabled,
          nextLabel: nextBtn.textContent,
          targetSide: side,
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
        log.push({ stuck: countTxt + ": " + title });
        nextBtn.disabled = false; click(nextBtn);    // force on, so the rest is still audited
      }
    } catch (e) { log.push({ err: String(e && e.message) }); }
  }, 60);
})();
</script>`;

  // Serve the client directory: the app loads its scripts by absolute path.
  // It has to be a SEPARATE process: execFileSync below blocks this one's event
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
  // has to be faked, the real gates read a module-scoped _authUser no test can
  // set, so the harness serves a copy of the app with exactly those three
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
        check(`${tourName} ${who}: no step traps the player${stuck.length ? ": " + stuck.join(" | ") : ""}`,
              stuck.length === 0);
        check(`${tourName} ${who}: every step was reached (${steps.length} steps)`, steps.length >= 12);

        // A step that spotlights nothing is only fine when it is meant to talk
        // to the whole screen; a step that asks for a CLICK must have something
        // to click, or Next stays disabled and the player is stranded.
        const blind = steps.filter(s => s.nextDisabled && !s.hasTarget).map(s => s.title);
        check(`${tourName} ${who}: nothing to click is never asked for${blind.length ? ": " + blind.join(", ") : ""}`,
              blind.length === 0);

        const covered = steps.filter(s => s.nextDisabled && s.popCoversTarget).map(s => s.title);
        check(`${tourName} ${who}: the popup never covers the thing you must click${covered.length ? ": " + covered.join(", ") : ""}`,
              covered.length === 0);

        // "Click above →" over a target that is below the popup is a wrong
        // instruction, and the player follows it.
        const misPointed = steps.filter(s => {
          const m = /^Click (above|below|left|right)/.exec(s.nextLabel || "");
          return m && s.targetSide !== "overlap" && s.targetSide !== "none" && m[1] !== s.targetSide;
        }).map(s => `${s.title}: says ${(/^Click (\w+)/.exec(s.nextLabel) || [])[1]}, target is ${s.targetSide}`);
        check(`${tourName} ${who}: "Click <way>" always points at the target${misPointed.length ? ": " + misPointed.join(", ") : ""}`,
              misPointed.length === 0);

        const off = steps.filter(s => s.popOffscreen).map(s => s.title);
        check(`${tourName} ${who}: the popup is always fully on screen${off.length ? ": " + off.join(", ") : ""}`,
              off.length === 0);

        // A spotlight thinner than a finger is not a spotlight.
        const slivers = steps.filter(s => s.hasTarget && (s.targetW < 24 || s.targetH < 10)).map(s => `${s.title} ${s.targetW}x${s.targetH}`);
        check(`${tourName} ${who}: no spotlight is a sliver${slivers.length ? ": " + slivers.join(", ") : ""}`,
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
      check(`${label} (${w}x${h}): runs to the end with no step trapping the player${stuck.length ? ": " + stuck.join(" | ") : ""}`,
            rows.some(r => r.finished) && stuck.length === 0);
      check(`${label} (${w}x${h}): the popup stays on screen`,
            steps.every(s => !s.popOffscreen));
      check(`${label} (${w}x${h}): the popup never covers a required click`,
            steps.every(s => !(s.nextDisabled && s.popCoversTarget)));
      // The direction flips with the window size, this is the width the
      // hard-coded "Click above →" got wrong.
      check(`${label} (${w}x${h}): "Click <way>" still points at the target`,
            steps.every(s => {
              const m = /^Click (above|below|left|right)/.exec(s.nextLabel || "");
              return !m || s.targetSide === "overlap" || s.targetSide === "none" || m[1] === s.targetSide;
            }));
    }
  } finally {
    try { server.kill(); } catch (_) {}
    tmp.forEach(f => { try { fs.unlinkSync(f); } catch (_) {} });
  }
}

console.log("\n" + "=".repeat(42));
console.log(`RESULT: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
