#!/usr/bin/env node
/* The Vote Kick / Skip Turn buttons and the waiting room's Table Setup.
 *
 * Run:  node test_vote_buttons_ui.js
 *
 * The client half of the same feature test_kick_and_skip_votes.py covers on the
 * server. Two of these checks exist because of specific ways this UI can look
 * finished and do nothing:
 *
 *  • The seat pills are redrawn off a render KEY. Name, score, avatar and whose
 *    turn it is are all unchanged by a vote landing, so unless the tallies are
 *    part of that key the button renders once and then shows a stale count
 *    forever, on every client except the one that pressed it.
 *
 *  • The kicked notice keeps arriving for as long as the server remembers the
 *    old token. Without a latch the removed player is thrown back to the menu
 *    on every single poll.
 *
 * The rest pins the shape of the thing: the two votes go to two different
 * endpoints, the menu is never offered on your own seat, Table Setup stays out
 * of Quick Play / competitive / tournament rooms, and every id the JS reaches
 * for is actually in preview.html with a style behind it.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

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

const sandbox = {
  _latestVotes: { ballot_seat: null, kick: [], skip: null },
  console,
};
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
      "an empty payload offers nothing, so the ⋯ handle never appears");

// ══ 2. The render key ════════════════════════════════════════════════════════
console.log("the seat pills actually redraw:");

const seatsKeyBlock = APP.slice(APP.indexOf("const _seatsKey = JSON.stringify"),
                                APP.indexOf("if (_seatsKey === _seatsRenderKey"));
check(/_latestVotes/.test(seatsKeyBlock),
      "the vote tallies are part of the seat render key");
check(/kk:\s*Boolean\(_sm&&_sm\.kicked\)/.test(seatsKeyBlock),
      "a seat becoming kicked is part of the seat render key");

// ══ 3. The menu is only ever on somebody else's seat ═════════════════════════
console.log("where the menu is allowed:");

check(/if \(!isMe && !\(_seatMeta && _seatMeta\.kicked\)\) \{[\s\S]{0,80}attachSeatVoteMenu/.test(APP),
      "the vote menu is attached only to another player's live seat");
check(/function attachSeatVoteMenu[\s\S]{0,220}if \(!_kickInfoFor\(p\.index\) && !_skipInfoFor\(p\.index\)\) return;/.test(APP),
      "no ballot to cast means no ⋯ handle at all");

// ══ 4. Two votes, two endpoints, two rules ═══════════════════════════════════
console.log("the two votes stay apart:");

check(/_sendVote\("kick_player"/.test(APP), "Vote Kick posts to kick_player");
check(/_sendVote\("skip_turn"/.test(APP), "Skip Turn posts to skip_turn");
check(/undo: Boolean\(info\.mine\)/.test(APP),
      "pressing Vote Kick again takes the vote back rather than double-casting");
check(/everyone must agree/.test(APP), "the kick row says it needs everyone");
check(/half the table/.test(APP), "the skip row says it needs half");
check(/A kick is permanent\. A skip costs one turn\./.test(APP),
      "the menu spells out the difference between the two");

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

// ══ 6. Table Setup ═══════════════════════════════════════════════════════════
console.log("table setup:");

const tableIds = ["wr-table-setup", "wr-table-total", "wr-table-note",
                  "wr-humans-value", "wr-bots-value",
                  "wr-humans-minus", "wr-humans-plus",
                  "wr-bots-minus", "wr-bots-plus"];
tableIds.forEach(id => {
  check(HTML.includes(`id="${id}"`), `preview.html has #${id}`);
  check(APP.includes(id), `preview-app.js drives #${id}`);
});
check(/class="wr-step-btn"[\s\S]{0,200}data-kind="humans"/.test(HTML),
      "the human steppers carry the kind the handler reads");
check((HTML.match(/class="wr-step-btn"/g) || []).length === 4,
      "four steppers: more/fewer humans, more/fewer bots");

const allowed = APP.slice(APP.indexOf("function updateTableSetup"),
                          APP.indexOf("async function stepTableSeats"));
check(/!isQuickPlay/.test(allowed), "Table Setup stays out of Quick Play");
check(/!isCompetitive/.test(allowed), "Table Setup stays out of competitive rooms");
check(/!room\.tournament/.test(allowed), "Table Setup stays out of a bracket match");
check(/Math\.max\(1, filled\)/.test(allowed),
      "the floor on human spots is however many people have joined");
check(/WR_MIN_TABLE = 2, WR_MAX_TABLE = 8/.test(APP), "the table is 2 to 8 players");
check(/updateTableSetup\(seats, isHost, isQuickPlay, isComp\)/.test(APP),
      "the waiting room renders it on every update");
check(/lobby_seats/.test(APP), "the steppers post to the lobby_seats endpoint");

// The Quick Play chooser is untouched.
check(/class="wr-human-option"/.test(HTML) && /quickplay_seats/.test(APP),
      "Quick Play keeps its own fixed 2/3/4 chooser");

// ══ 7. Every class the JS makes has a style ══════════════════════════════════
console.log("styles exist:");

["pv-seat-vote-dots", "pv-seat-vote-menu", "pv-vote-head", "pv-vote-row",
 "pv-vote-row-label", "pv-vote-row-hint", "pv-vote-foot", "pv-vote-kick",
 "pv-seat-kicked-badge", "wr-table-setup", "wr-stepper-row", "wr-stepper-label",
 "wr-step-btn", "wr-step-value"].forEach(cls => {
  check(CSS.includes("." + cls), `preview.css styles .${cls}`);
  check(APP.includes(cls) || HTML.includes(cls), `.${cls} is actually used`);
});
// The pill is the positioning context for the ⋯ handle and the menu.
check(/\.pv-seat \{ position: relative; \}/.test(CSS),
      "the seat pill is a positioning context, so the menu lands on it");
// The pill's own badges sit at z-index 5-7, so the menu has to clear them or
// it opens underneath "YOU" and the Removed badge.
const menuBlock = CSS.slice(CSS.indexOf(".pv-seat-vote-menu {"),
                            CSS.indexOf(".pv-vote-head {"));
const menuZ = Number((menuBlock.match(/z-index:\s*(\d+)/) || [])[1]);
check(menuZ > 7, `the menu sits above the pill's own badges (z-index ${menuZ})`);

// ══ 8. The build was bumped ══════════════════════════════════════════════════
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
// menu is on screen, or that the Table Setup row has not collapsed: a headless
// pass at ONE window size has hidden exactly that kind of bug here before, so
// every width the game is actually played at gets measured.
const os = require("os");
const { execFileSync } = require("child_process");

const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium-browser",
].find(p => { try { return fs.existsSync(p); } catch { return false; } });

function votePage() {
  // The real stylesheet over the real markup, and the REAL positioning code
  // lifted straight out of the app, so this measures what ships rather than a
  // copy of it that could drift.
  const seat = (id, style) => `
    <div class="pv-seat" id="${id}" style="position:absolute;${style}width:120px;height:120px;background:#123;">
      <div class="pv-seat-name">Cy</div>
      <button class="pv-seat-vote-dots">⋯</button>
      <div class="pv-seat-vote-menu">
        <div class="pv-vote-head">Cy</div>
        <button class="pv-vote-row pv-vote-skip"><span class="pv-vote-row-label">Skip Turn</span><span class="pv-vote-row-hint">1/2 · half the table</span></button>
        <button class="pv-vote-row pv-vote-kick"><span class="pv-vote-row-label">Vote Kick</span><span class="pv-vote-row-hint">0/2 · everyone must agree</span></button>
        <div class="pv-vote-foot">A kick is permanent. A skip costs one turn.</div>
      </div>
      <div class="pv-seat-kicked-badge">Removed</div>
    </div>`;
  // Seat pills live in clusters pinned to BOTH screen edges: the left cluster
  // is where the menu used to hang off the side of the window entirely.
  const menuHtml = seat("seat-left", "left:0;top:0;") + seat("seat-right", "right:0;top:140px;");
  const setupHtml = HTML.slice(HTML.indexOf('<div class="wr-table-setup"'),
                               HTML.indexOf('<div class="wr-players"'))
                        .replace('style="display:none;"', '');
  const page = `<!doctype html><html><head><meta charset="utf-8"><style>
${CSS}
body{margin:0;background:#0b1c2c;}
.wr-box{max-width:460px;margin:0 auto;}
</style></head><body>
${menuHtml}
<div class="wr-box">${setupHtml}</div>
<div id="out"></div>
<script>
__POSITION_FN__
function report(){
  const L=[];
  const ok=(c,m)=>L.push((c?"PASS ":"FAIL ")+m);
  const r=el=>el.getBoundingClientRect();
  ["seat-left","seat-right"].forEach(sid=>{
    const seatEl=document.getElementById(sid);
    const menuEl=seatEl.querySelector(".pv-seat-vote-menu");
    // Exactly what openSeatVoteMenu does after it appends the menu.
    _positionVoteMenu(menuEl,seatEl);
    const menu=r(menuEl), dots=r(seatEl.querySelector(".pv-seat-vote-dots"));
    ok(menu.width>120&&menu.height>60,sid+": the vote menu has real size ("+Math.round(menu.width)+"x"+Math.round(menu.height)+")");
    ok(menu.left>=-1&&menu.right<=innerWidth+1,sid+": the vote menu is inside the window (left "+Math.round(menu.left)+", right "+Math.round(menu.right)+" of "+innerWidth+")");
    ok(dots.width>=16&&dots.height>=16,sid+": the ⋯ handle is big enough to hit ("+Math.round(dots.width)+"px)");
    menuEl.querySelectorAll(".pv-vote-row").forEach((el,i)=>{
      const b=r(el);
      ok(b.height>=26,sid+": vote row "+i+" is tappable ("+Math.round(b.height)+"px tall)");
      ok(b.width>=110,sid+": vote row "+i+" is wide enough ("+Math.round(b.width)+"px)");
    });
  });
  // Table Setup: the steppers must be real buttons on a real row.
  const setup=document.getElementById("wr-table-setup");
  ok(setup&&r(setup).height>80,"Table Setup has real height ("+Math.round(setup?r(setup).height:0)+"px)");
  const btns=[...document.querySelectorAll(".wr-step-btn")];
  ok(btns.length===4,"four steppers rendered");
  btns.forEach((el,i)=>{
    const b=r(el);
    ok(b.width>=24&&b.height>=24,"stepper "+i+" is tappable ("+Math.round(b.width)+"x"+Math.round(b.height)+")");
    ok(b.right<=innerWidth+1,"stepper "+i+" is on screen");
  });
  // The two rows must not overlap each other.
  const rows=[...document.querySelectorAll(".wr-stepper-row")].map(r);
  if(rows.length===2) ok(rows[0].bottom<=rows[1].top+1,"the two stepper rows do not overlap");
  document.getElementById("out").textContent=L.join("\\n");
}
report();
</script></body></html>`;
  return page.replace("__POSITION_FN__", grabFn("_positionVoteMenu"));
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
  // viewport, small laptop, the 1366x768 that the lobby has overflowed at
  // before, and a big window.
  [[585, 1266], [1024, 768], [1366, 768], [1680, 1050]].forEach(([w, h]) => runChrome(w, h));
}

console.log("");
if (failures) {
  console.log(`FAILED ${failures} of ${checks} checks`);
  process.exit(1);
}
console.log(`All ${checks} checks passed.`);
