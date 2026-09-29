#!/usr/bin/env node
/* The Vote Kick / Skip Turn votes, and the waiting room's Table Setup.
 *
 * Run:  node test_vote_buttons_ui.js
 *
 * The client half of the same feature test_kick_and_skip_votes.py covers on the
 * server. BOTH VOTES ARE TYPED NOW: /kick and /skip in the chat, and nothing
 * else. They were buttons in the action bar next to Surf's Up, Vote Kick with a
 * picker hanging off it, and the whole of that is gone. What the list offers,
 * how it names a player and what it posts is test_chat_slash_commands.js; this
 * file holds what is left over.
 *
 * What it pins:
 *
 *  • The buttons are GONE, not hidden. A control left in the markup with
 *    display:none is a second way in that nobody maintains, and half of this
 *    removal was in the stylesheet and the app rather than the page.
 *
 *  • The kicked notice keeps arriving for as long as the server remembers the
 *    old token. Without a latch the removed player is thrown back to the menu
 *    on every single poll.
 *
 *  • The two votes still go to two different endpoints, Table Setup stays out
 *    of Head to Head / competitive / tournament rooms, and the build stamp is
 *    on every /css and /js asset the page asks for.
 */
"use strict";

const fs = require("fs");
const path = require("path");

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

// ══ 1. The buttons are gone, all the way down ════════════════════════════════
console.log("no buttons are left behind:");

["pv-skip-turn-btn", "pv-kick-wrap", "pv-kick-btn", "pv-kick-picker"].forEach(id => {
  check(!HTML.includes(`id="${id}"`), `#${id} is gone from preview.html`);
  check(!APP.includes(id), `…and nothing in preview-app.js reaches for it`);
});
["pv-btn-skip", "pv-btn-kick", "pv-kick-wrap", "pv-kick-picker", "pv-vote-head",
 "pv-vote-row", "pv-vote-row-label", "pv-vote-row-hint", "pv-vote-foot"].forEach(cls => {
  check(!CSS.includes("." + cls), `preview.css no longer styles .${cls}`);
  check(!APP.includes(cls) && !HTML.includes(cls), `and nothing uses .${cls}`);
});
// The picker was more than a button: a menu, its rows, an outside-click
// listener, an Escape listener and the code that slid it back inside the
// window. None of that has anything left to do.
["closeKickPicker", "openKickPicker", "_kickRow", "_kickInfoFor", "_skipInfoFor",
 "_clampToWindow", "_kickPickerOpen", "updateVoteButtons"].forEach(fn => {
  check(!APP.includes(fn), `${fn}() is gone with the picker`);
});
// The old per-seat ⋯ menu went the same way, earlier, for the same reason.
check(!/attachSeatVoteMenu|openSeatVoteMenu|pv-seat-vote-dots/.test(APP),
      "the old seat-pill vote menu is still gone too");
check(!/pv-seat-vote-dots|pv-seat-vote-menu/.test(CSS),
      "and its styles with it");
// The seat pill still SAYS who was removed; that is a status, not a control.
check(/pv-seat-kicked-badge/.test(APP) && /\.pv-seat-kicked-badge/.test(CSS),
      "a removed player's seat still shows the Removed badge");

// ══ 2. Both votes are reachable, in the one place ════════════════════════════
console.log("the chat is where they are now:");

check(/const _SLASH_CMDS = \[/.test(APP), "the chat has a command table");
check(/cmd: "kick"/.test(APP) && /cmd: "skip"/.test(APP), "with both votes in it");
check(HTML.includes('id="pv-slash-menu"'), "and a list in the panel to name players from");
check(/placeholder="Type a message, or \/ for commands"/.test(HTML),
      "the message box says the slash does something");
check(/type \/ for commands/.test(HTML),
      "and so does the second line of the Messages button");
// The tallies still have to reach an open list, or it shows the numbers it
// opened with: nothing else redraws it.
const votesSync = APP.slice(APP.indexOf('_latestVotes = (_v && typeof _v === "object")'),
                            APP.indexOf("const mySeat = (Number.isInteger(myIdx))"));
check(/_slashRefresh\(\)/.test(votesSync),
      "every payload that carries tallies repaints an open list");

// ══ 4. Two votes, two endpoints, two rules ═══════════════════════════════════
console.log("the two votes stay apart:");

check(/_sendVote\("kick_player"/.test(APP), "/kick posts to kick_player");
check(/_sendVote\("skip_turn"/.test(APP), "/skip posts to skip_turn");
check(/undo: Boolean\(t\.mine\)/.test(APP),
      "running it again on a vote of mine takes it back rather than double-casting");
check(/everyone must agree/.test(APP), "a kick row says the vote needs everyone");
check(/Needs half the other players/.test(APP), "the skip command says it needs half");
check(/Every other player has to agree\. This is permanent\./.test(APP),
      "and the kick command spells out that it is permanent");
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
// play carries a + that asks what goes there, a seat for a person or a bot.
// The two are not the same at the Start button, which is why it asks: a bot
// fills its seat, an empty player seat stops the game starting until somebody
// takes it.
check(/const WR_SLOTS = 8/.test(APP), "the room always draws eight spots");
check(/for \(let i = rows\.length; i < WR_SLOTS; i\+\+\) grid\.appendChild\(_wrAddCard/.test(APP),
      "every spot past the table's size is drawn as an add spot");
check(/function _wrAddCard/.test(APP) && /Add a bot/.test(APP), "that spot offers a bot");
check(/Add a player seat/.test(APP), "…and a seat for a person");
check(/wr-add-menu/.test(APP), "the + asks which of the two rather than assuming");
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
      "Head to Head, competitive and bracket matches keep their own shape");
check(/class="wr-human-option"/.test(HTML) && /quickplay_seats/.test(APP),
      "Head to Head keeps its own fixed 2/3/4 chooser");

// ══ 7. Every class the list makes still has a style ═════════════════════════
console.log("styles exist:");

["pvs-head", "pvs-row", "pvs-chip", "pvs-name", "pvs-hint", "pvs-foot", "pvs-empty",
 "pv-seat-kicked-badge"].forEach(cls => {
  check(CSS.includes("." + cls), `preview.css styles .${cls}`);
  check(APP.includes(cls) || HTML.includes(cls), `.${cls} is actually used`);
});
// The chat panel sits at the bottom of the screen, so a list that opened
// downwards would open off the bottom of the window.
const menuBlock = CSS.slice(CSS.indexOf("    #pv-slash-menu {"), CSS.indexOf("#pv-slash-menu.open"));
check(/bottom: calc\(100% - 6px\)/.test(menuBlock), "the command list opens upwards");
check(/max-height: 190px/.test(menuBlock) && /overflow-y: auto/.test(menuBlock),
      "eight players cannot push it off the top of the panel");

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

// The browser half of this file went with the buttons: what it measured was
// the picker sliding back inside the window at four widths. The command list
// is laid out and driven in a real Chrome by test_chat_slash_commands.js.

console.log("");
if (failures) {
  console.log(`FAILED ${failures} of ${checks} checks`);
  process.exit(1);
}
console.log(`All ${checks} checks passed.`);
