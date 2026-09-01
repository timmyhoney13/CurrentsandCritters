#!/usr/bin/env node
/* The Vote Kick / Skip Turn buttons and the waiting room's Table Setup.
 *
 * Run:  node test_vote_buttons_ui.js        (browser half needs Google Chrome)
 *
 * The client half of the same feature test_kick_and_skip_votes.py covers on the
 * server. Both votes sit in the action bar next to Surf's Up, which is the same
 * kind of control pointed the other way: that one says "I have stepped away",
 * these two are about somebody else.
 *
 * Two of these checks exist because of specific ways this UI can look finished
 * and do nothing:
 *
 *  • Nothing else redraws the action bar. The seat pills have a render key that
 *    repaints them; these buttons have no such thing, so unless they are
 *    repainted when a payload lands they show the tally from whenever they were
 *    last touched, on every client except the one that pressed.
 *
 *  • The kicked notice keeps arriving for as long as the server remembers the
 *    old token. Without a latch the removed player is thrown back to the menu
 *    on every single poll.
 *
 * The rest pins the shape of the thing: the two votes go to two different
 * endpoints, a button with nothing behind it is hidden rather than left dead,
 * Table Setup stays out of Quick Play / competitive / tournament rooms, and
 * every id the JS reaches for is really in preview.html with a style behind it.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
const APP  = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const CSS  = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");

let failures = 0;
let checks = 0;
function check(cond, label) {
  checks++;
  if (!cond) { failures++; console.log("  ✗ " + label); }
}

// ── Lift the real functions out of the app IIFE ──────────────────────────────
function grabFn(name) {
  const start = APP.indexOf(`\n  function ${name}(`);
  if (start < 0) throw new Error(`function ${name}() not found in preview-app.js`);
  let i = APP.indexOf("{", start);
  let depth = 0;
  for (let j = i; j < APP.length; j++) {
    const ch = APP[j];
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return APP.slice(start, j + 1); }
  }
  throw new Error(`unbalanced braces reading ${name}()`);
}

// ══ 1. The tallies drive the buttons ═════════════════════════════════════════
console.log("reading the tallies:");

const sandbox = { _latestVotes: { ballot_seat: null, kick: [], skip: null }, console };
vm.createContext(sandbox);
vm.runInContext([grabFn("_kickInfoFor"), grabFn("_skipInfoFor")].join("\n"), sandbox);

sandbox._latestVotes = {
  ballot_seat: 0,
  kick: [{ seat: 1, name: "Bo", votes: 1, needed: 2, mine: true, blocked: false },
         { seat: 2, name: "Cy", votes: 0, needed: 2, mine: false, blocked: false }],
  skip: { seat: 2, name: "Cy", votes: 1, needed: 2, mine: false, blocked: false },
};
check(sandbox._kickInfoFor(1).votes === 1, "a kick tally is found by seat");
check(sandbox._kickInfoFor(1).mine === true, "my own vote comes back marked mine");
check(sandbox._kickInfoFor(9) === null, "a seat with no ballot returns nothing");
check(sandbox._skipInfoFor(2).needed === 2, "the skip tally is found for the active seat");
check(sandbox._skipInfoFor(1) === null, "no skip is offered for a seat that is not up");

sandbox._latestVotes = { ballot_seat: null, kick: [], skip: null };
check(sandbox._kickInfoFor(1) === null && sandbox._skipInfoFor(1) === null,
      "an empty payload offers nothing at all");

// ══ 2. The buttons are repainted when tallies land ═══════════════════════════
console.log("the buttons actually refresh:");

// The repaint has to happen in the same block that stores the tallies, or the
// bar shows whatever it showed last time something else happened to touch it.
const votesSync = APP.slice(APP.indexOf('_latestVotes = (_v && typeof _v === "object")'),
                            APP.indexOf("const mySeat = (Number.isInteger(myIdx))"));
check(/updateVoteButtons\(\)/.test(votesSync),
      "every payload that carries tallies repaints the buttons");

const upd = APP.slice(APP.indexOf("function updateVoteButtons()"),
                      APP.indexOf('document.getElementById("pv-skip-turn-btn")?.addEventListener'));
check(/skipBtn\.style\.display = "none"/.test(upd),
      "no skip vote to cast: the button is hidden, not left dead in the bar");
check(/kickWrap\.style\.display = "none"/.test(upd),
      "no kick vote to cast: the button is hidden, not left dead in the bar");
check(/if \(_kickPickerOpen\) closeKickPicker\(\)/.test(upd),
      "an open picker is closed when the votes behind it go away");
check(/if \(_kickPickerOpen\) openKickPicker\(\)/.test(upd),
      "an open picker is kept in step with tallies that just landed");
check(/skipBtn\.disabled = Boolean\(skip\.blocked \|\| skip\.mine/.test(upd),
      "a skip vote already cast cannot be cast twice");
check(/skip\.votes\}\/\$\{skip\.needed\}/.test(upd),
      "the skip button shows its running tally");
check(/cast > 0 \? `🚫 Vote Kick \(\$\{cast\}\)`/.test(upd),
      "the kick button shows its running tally");

// ══ 3. The buttons sit next to Surf's Up ═════════════════════════════════════
console.log("where the buttons live:");

const bar = HTML.slice(HTML.indexOf('id="pv-payment-info"'),
                       HTML.indexOf('id="pv-fullscreen-btn"'));
["pv-skip-turn-btn", "pv-kick-wrap", "pv-kick-btn", "pv-kick-picker"].forEach(id => {
  check(bar.includes(`id="${id}"`), `#${id} is in the action bar`);
  check(APP.includes(id), `preview-app.js drives #${id}`);
});
check(bar.indexOf('id="pv-skip-turn-btn"') < bar.indexOf('id="pv-surf-btn"'),
      "Skip Turn sits next to Surf's Up");
check(bar.indexOf('id="pv-kick-btn"') < bar.indexOf('id="pv-surf-btn"'),
      "Vote Kick sits next to Surf's Up");
// The old per-seat ⋯ menu is gone: two competing places to cast the same vote
// is worse than one, and the pills were the harder one to find.
check(!/attachSeatVoteMenu|openSeatVoteMenu|pv-seat-vote-dots/.test(APP),
      "the old seat-pill vote menu is gone, not left as a second way in");
check(!/pv-seat-vote-dots|pv-seat-vote-menu/.test(CSS),
      "and its styles went with it");
// The seat pill still SAYS who was removed; that is a status, not a control.
check(/pv-seat-kicked-badge/.test(APP) && /\.pv-seat-kicked-badge/.test(CSS),
      "a removed player's seat still shows the Removed badge");

// ══ 4. Two votes, two endpoints, two rules ═══════════════════════════════════
console.log("the two votes stay apart:");

check(/_sendVote\("kick_player"/.test(APP), "Vote Kick posts to kick_player");
check(/_sendVote\("skip_turn"/.test(APP), "Skip Turn posts to skip_turn");
check(/undo: Boolean\(info\.mine\)/.test(APP),
      "pressing a kick row again takes the vote back rather than double-casting");
check(/everyone must agree/.test(APP), "the kick rows say the vote needs everyone");
check(/Needs half the other players/.test(APP), "the skip button says it needs half");
check(/Everyone else has to agree\. This is permanent\./.test(APP),
      "the picker spells out that a kick is permanent");
check(/the host runs the lobby/.test(APP),
      "a row the server marked blocked says why instead of failing when pressed");

// ══ 5. The kicked player is told, once ═══════════════════════════════════════
console.log("being removed:");

check(/if \(d\.kicked_notice && !_kickedHandled\)/.test(APP),
      "the kicked notice is latched so it fires once, not every poll");
const latchDecl = APP.indexOf("let _kickedHandled = false;");
const latchUse  = APP.indexOf("if (d.kicked_notice && !_kickedHandled)");
check(latchDecl > 0 && latchDecl < latchUse,
      "the latch is declared above the payload handler that reads it");
check(/_kickedHandled = false;\s*\n\s*_armPollTimer\(\)/.test(APP),
      "entering a new room re-arms the latch");
check(/function handleKickedOut[\s\S]{0,400}returnToMenu\(false\)/.test(APP),
      "a removed player goes home without a rejoin token");

// ══ 6. Setting the size of the table ════════════════════════════════════════
console.log("the size of the table:");

// This used to be a "Table Setup" panel: two +/- steppers sitting above the
// seat list. It is gone on purpose. The eight spots ARE the control now, so a
// stepper panel would be a second place to change the same thing, and the two
// could disagree about what the table is.
["wr-table-setup", "wr-table-total", "wr-table-note", "wr-humans-value",
 "wr-bots-value", "wr-humans-minus", "wr-humans-plus", "wr-bots-minus",
 "wr-bots-plus"].forEach(id => {
  check(!HTML.includes(`id="${id}"`), `the old #${id} stepper is gone from the markup`);
  check(!APP.includes(id), `…and nothing in the app still reaches for it`);
});
check(!/class="wr-step-btn"/.test(HTML), "no stepper buttons are left");
check(!/function updateTableSetup/.test(APP), "and no renderer for them");

// What replaced it: every spot up to eight is drawn, and a spot that is not in
// play carries a + that seats a BOT. Never a player seat: a person joins with
// the room code, and an empty human seat does not become a bot at kickoff, it
// stops the game starting at all.
check(/const WR_SLOTS = 8/.test(APP), "the room always draws eight spots");
check(/for \(let i = rows\.length; i < WR_SLOTS; i\+\+\) grid\.appendChild\(_wrAddCard/.test(APP),
      "every spot past the table's size is drawn as an add-a-bot spot");
check(/function _wrAddCard/.test(APP) && /Add a bot/.test(APP), "that spot offers a bot");
check(!/Add a player seat/.test(APP), "and never a player seat");
check(/setTableSeats\(ctx\.humans, ctx\.bots \+ 1\)/.test(APP),
      "pressing it asks for one more bot and the same human spots");

const setter = APP.slice(APP.indexOf("async function setTableSeats"),
                         APP.indexOf("async function setTableSeats") + 2000);
check(/WR_MIN_TABLE \|\| total > WR_MAX_TABLE/.test(setter), "2 to 8 at the table is enforced");
check(/COMP_FFA_MIN_PLAYERS/.test(setter), "a competitive table keeps its own floor");
check(/WR_MIN_TABLE = 2, WR_MAX_TABLE = 8/.test(APP), "the table is 2 to 8 players");
check(/lobby_seats/.test(APP), "the spots post to the lobby_seats endpoint");

// The rooms whose shape is not the host's to change get no + or - at all.
const shape = APP.slice(APP.indexOf("canShape: isHost"), APP.indexOf("canShape: isHost") + 220);
check(/!room\.quick_play && !room\.competitive && !room\.tournament/.test(shape),
      "Quick Play, competitive and bracket matches keep their own shape");
check(/class="wr-human-option"/.test(HTML) && /quickplay_seats/.test(APP),
      "Quick Play keeps its own fixed 2/3/4 chooser");

// ══ 7. Every class the JS makes has a style ══════════════════════════════════
console.log("styles exist:");

["pv-btn-skip", "pv-btn-kick", "pv-kick-wrap", "pv-kick-picker", "pv-vote-head",
 "pv-vote-row", "pv-vote-row-label", "pv-vote-row-hint", "pv-vote-foot",
 "pv-seat-kicked-badge"].forEach(cls => {
  check(CSS.includes("." + cls), `preview.css styles .${cls}`);
  check(APP.includes(cls) || HTML.includes(cls), `.${cls} is actually used`);
});
check(/\.pv-kick-wrap \{ position: relative;/.test(CSS),
      "the kick button's wrapper is the picker's positioning context");
// The action bar is pinned to the bottom of the screen, so a picker that
// opened downwards would open off the bottom of the window.
const pickerBlock = CSS.slice(CSS.indexOf(".pv-kick-picker {"),
                              CSS.indexOf(".pv-kick-picker.open"));
check(/bottom: calc\(100% \+ 8px\)/.test(pickerBlock), "the picker opens upwards");
const pickerZ = Number((pickerBlock.match(/z-index:\s*(\d+)/) || [])[1]);
check(pickerZ >= 40, `the picker clears the action bar's own overlays (z-index ${pickerZ})`);
// Skip Turn names its target, so a long player name makes a very wide button,
// and this bar wraps: one more wrapped row costs real height on a short screen.
check(/#pv-action-bar \.pv-btn-skip \{[\s\S]{0,120}max-width: 150px[\s\S]{0,120}text-overflow: ellipsis/.test(CSS),
      "a long player name cannot widen Skip Turn into another action-bar row");
check(/#pv-action-bar \.pv-btn-skip \{ max-width: 124px; \}/.test(CSS),
      "and it is capped harder again on a phone held sideways");

// ══ 8. Build stamps ══════════════════════════════════════════════════════════
console.log("cache busting:");

const build = JSON.parse(
  fs.readFileSync(path.join(ROOT, "multiplayer/client/version.json"), "utf8")).build;
check(new RegExp(`const APP_BUILD\\s*=\\s*"${build}"`).test(APP),
      "APP_BUILD matches version.json");
// /css and /js are served with a 1-day max-age, so the hand-written ?v= stamps
// in preview.html are the only thing that makes a changed file reach anybody.
const stale = (HTML.match(/\/(?:css|js)\/[^"']+\?v=([\d.-]+)/g) || [])
  .filter(u => !u.includes(build));
check(stale.length === 0,
      `every /css and /js cache-buster is on the current build (stale: ${stale.slice(0, 3).join(", ")})`);

// ══ 9. Measured in a real browser, at several widths ═════════════════════════
// A structural check says the markup and the styles exist. It cannot say the
// picker is on screen, or that the Table Setup row has not collapsed: a
// headless pass at ONE window size has hidden exactly that kind of bug here
// before, so every width the game is actually played at gets measured.
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium-browser",
].find(p => { try { return fs.existsSync(p); } catch { return false; } });

function votePage() {
  // The real stylesheet over the real markup, and the REAL clamping code lifted
  // straight out of the app, so this measures what ships rather than a copy of
  // it that could drift. The bar is laid out the way the game lays it out: the
  // buttons pushed hard to the right by a flex spacer, which is the position
  // that used to put the picker off the side of the window.
  const barHtml = HTML.slice(HTML.indexOf('<!-- The two votes about OTHER players'),
                             HTML.indexOf('id="pv-fullscreen-btn"'))
                      .replace(/style="display:none;"/g, "")
                      .replace(/<button class="pv-btn pv-btn-surf"[\s\S]*$/, "")
                  + '<button class="pv-btn pv-btn-surf">🏄 Surf\'s Up!!</button>';
  const page = `<!doctype html><html><head><meta charset="utf-8"><style>
${CSS}
body{margin:0;background:#0b1c2c;}
#pv-action-bar{position:fixed;bottom:0;left:0;right:0;display:flex;align-items:center;
  gap:8px;padding:8px;background:#0d2438;}
.wr-box{max-width:460px;margin:0 auto;}
</style></head><body>
<div id="pv-action-bar"><div style="flex:1;"></div>${barHtml}</div>
<div id="out"></div>
<script>
__CLAMP_FN__
function report(){
  const L=[];
  const ok=(c,m)=>L.push((c?"PASS ":"FAIL ")+m);
  const r=el=>el.getBoundingClientRect();

  // Both buttons must be real, tappable, and on screen next to Surf's Up.
  const skip=document.getElementById("pv-skip-turn-btn");
  const kick=document.getElementById("pv-kick-btn");
  const surf=document.querySelector(".pv-btn-surf");
  [["skip",skip],["kick",kick]].forEach(([n,el])=>{
    const b=r(el);
    ok(b.width>=60&&b.height>=24,n+" button is tappable ("+Math.round(b.width)+"x"+Math.round(b.height)+")");
    ok(b.left>=-1&&b.right<=innerWidth+1,n+" button is inside the window");
    ok(Math.abs(b.top-r(surf).top)<=2,n+" button is on the same row as Surf's Up");
  });
  ok(r(kick).right<=r(surf).left+1,"the vote buttons sit before Surf's Up, not on top of it");

  // Open the picker exactly the way the app does, then measure it.
  const picker=document.getElementById("pv-kick-picker");
  picker.innerHTML='<div class="pv-vote-head">Remove a player</div>'
    +'<button class="pv-vote-row"><span class="pv-vote-row-label">Bo</span><span class="pv-vote-row-hint">0/2 &middot; everyone must agree</span></button>'
    +'<button class="pv-vote-row"><span class="pv-vote-row-label">Cy</span><span class="pv-vote-row-hint">1/2 &middot; everyone must agree</span></button>'
    +'<div class="pv-vote-foot">Everyone else has to agree. This is permanent.</div>';
  picker.classList.add("open");
  _clampToWindow(picker,document.getElementById("pv-kick-wrap"));
  const pb=r(picker);
  ok(pb.width>140&&pb.height>70,"the picker has real size ("+Math.round(pb.width)+"x"+Math.round(pb.height)+")");
  ok(pb.left>=-1&&pb.right<=innerWidth+1,"the picker is inside the window (left "+Math.round(pb.left)+", right "+Math.round(pb.right)+" of "+innerWidth+")");
  ok(pb.top>=-1,"the picker is not cut off the top of the window");
  ok(pb.bottom<=r(kick).top+1,"the picker opens upwards, clear of the button");
  picker.querySelectorAll(".pv-vote-row").forEach((el,i)=>{
    const b=r(el);
    ok(b.height>=26,"picker row "+i+" is tappable ("+Math.round(b.height)+"px tall)");
    ok(b.width>=110,"picker row "+i+" is wide enough ("+Math.round(b.width)+"px)");
  });


  document.getElementById("out").textContent=L.join("\\n");
}
report();
</script></body></html>`;
  return page.replace("__CLAMP_FN__", grabFn("_clampToWindow"));
}

function runChrome(width, height) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-vote-"));
  const file = path.join(tmp, "vote.html");
  fs.writeFileSync(file, votePage());
  let dom;
  try {
    dom = execFileSync(CHROME, [
      "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      `--window-size=${width},${height}`, "--virtual-time-budget=6000",
      "--dump-dom", "file://" + file,
    ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 90000 });
  } catch (e) {
    console.error("Chrome failed to run:", e.message);
    process.exit(1);
  }
  fs.rmSync(tmp, { recursive: true, force: true });
  const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
  const text = m ? m[1].replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").trim() : "";
  if (!text) { console.log(`  ✗ no measurements came back at ${width}x${height}`); failures++; checks++; return; }
  text.split("\n").forEach(line => {
    check(line.startsWith("PASS "), `[${width}x${height}] ${line.replace(/^(PASS|FAIL) /, "")}`);
  });
}

if (!CHROME) {
  console.log("rendered sizes: SKIP (no Chrome found)");
} else {
  console.log("rendered sizes:");
  // The widths the game is really played at: phone in the game's zoomed-out
  // viewport, small laptop, the 1366x768 the lobby has overflowed at before,
  // and a big window.
  [[585, 1266], [1024, 768], [1366, 768], [1680, 1050]].forEach(([w, h]) => runChrome(w, h));
}

console.log("");
if (failures) {
  console.log(`FAILED ${failures} of ${checks} checks`);
  process.exit(1);
}
console.log(`All ${checks} checks passed.`);
