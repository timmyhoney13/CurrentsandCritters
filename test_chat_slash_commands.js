#!/usr/bin/env node
/* The Messages button in the action bar, and /kick + /skip typed in the chat.
 *
 * Run:  node test_chat_slash_commands.js    (the last part wants Google Chrome)
 *
 * ONE PLACE, not three. Vote Kick and Skip Turn were buttons of their own in
 * the action bar, with a picker hanging off one of them, at the far end of the
 * screen from the chat a player is already in when they want one. The buttons
 * are gone; the commands are the whole of both votes now.
 *
 *  • The Messages button is the action bar's one gold control, immediately left
 *    of Surf's Up. Checked here: it really is in the bar and really left of
 *    Surf's Up, it is gone from the Board Size cluster along with the separator
 *    that held it, the two vote buttons and their picker are gone from the
 *    markup, the app and the stylesheet alike, and nothing still reaches for
 *    any of it.
 *
 *  • Typing / in the message box lists the commands, and taking one turns the
 *    same list into the players it can name. The list is built from
 *    payload.votes, the same tallies the server works out per viewer, so it can
 *    never offer a bot, a spectator, a removed player, you, or your own second
 *    hand in competitive.
 *
 * The parts that can look finished and do nothing, each with a check below:
 *
 *  1. THE LIST GOES STALE. Tallies land every poll. An open list of players has
 *     to be repainted by the same code that repaints the buttons, or it shows
 *     the numbers it opened with.
 *  2. THE COMMAND GETS BROADCAST. "/kick Bobb" with the name misspelled must be
 *     answered, not said out loud to the table. And a message that merely opens
 *     with a slash ("/2 cards left") is a message.
 *  3. ONE TAP MUST NOT REMOVE A PLAYER. Taking a row fills the box in. Enter
 *     sends it. A kick is permanent and the list moves under you.
 *  4. THE NAME HAS TO RESOLVE. P2, 2, "bo" and "Bob" are all Bob in seat 1, and
 *     a name that matches two players asks which rather than picking one.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const APP  = fs.readFileSync(path.join(CLIENT, "js/preview-app.js"), "utf8");
const CSS  = fs.readFileSync(path.join(CLIENT, "css/preview.css"), "utf8");
const HTML = fs.readFileSync(path.join(CLIENT, "preview.html"), "utf8");

// Claims about what the app SAYS are made against what it can print: a comment
// reads the same to a regex as code.
const APP_SAYS = APP.replace(/\/\*[\s\S]*?\*\//g, " ")
  .split("\n").filter(l => !/^\s*\/\//.test(l)).join("\n");

let pass = 0, fail = 0;
function check(cond, name, extra) {
  if (cond) pass++;
  else { fail++; console.log("  x FAIL: " + name + (extra ? "   -> " + extra : "")); }
}
function section(t) { console.log("\n" + t); }

// ══ A tiny DOM, enough to run the real list ══════════════════════════════════
function mkNode(tag) {
  const n = {
    tag, className: "", textContent: "", type: "", value: "",
    children: [], attrs: {}, handlers: {}, parentNode: null,
    get firstChild() { return this.children[0] || null; },
    appendChild(c) { this.children.push(c); c.parentNode = this; return c; },
    removeChild(c) { this.children = this.children.filter(k => k !== c); return c; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); },
    fire(t, ev) { (this.handlers[t] || []).forEach(f => f(ev || { preventDefault() {} })); },
    contains(x) { return x === this || this.children.some(c => c.contains && c.contains(x)); },
    focus() {}, setSelectionRange() {},
  };
  const cls = () => String(n.className || "").split(/\s+/).filter(Boolean);
  const set = (s) => { n.className = Array.from(s).join(" "); };
  n.classList = {
    add: (...c) => { const s = new Set(cls()); c.forEach(x => s.add(x)); set(s); },
    remove: (...c) => { const s = new Set(cls()); c.forEach(x => s.delete(x)); set(s); },
    toggle: (c, on) => {
      const s = new Set(cls());
      const want = (on === undefined) ? !s.has(c) : Boolean(on);
      if (want) s.add(c); else s.delete(c);
      set(s);
    },
    contains: (c) => cls().indexOf(c) >= 0,
  };
  return n;
}

// The slash block, lifted whole out of the app: the command table, the state it
// keeps and every function that reads it. The DOM wiring below it is checked by
// reading the source, further down.
const BLOCK_FROM = APP.indexOf("  const _SLASH_CMDS = [");
const BLOCK_TO   = APP.indexOf("  // ── Wiring: the message box,");
if (BLOCK_FROM < 0 || BLOCK_TO < 0 || BLOCK_TO < BLOCK_FROM) {
  console.log("could not find the slash-command block in preview-app.js");
  process.exit(1);
}
const BLOCK = APP.slice(BLOCK_FROM, BLOCK_TO);

function makeEnv(votes, opts) {
  const o = opts || {};
  const els = {
    "pv-slash-menu": mkNode("div"),
    "pv-chat-text": mkNode("textarea"),
  };
  const toasts = [], sent = [];
  const sandbox = {
    console, Boolean, String, Number, Math, JSON, Array, Object, Set, RegExp, Error,
    document: { getElementById: (id) => els[id] || null, createElement: mkNode },
    _pg: (id) => els[id] || null,
    cl: (el) => { while (el.firstChild) el.removeChild(el.firstChild); },
    _chatPanelOpen: o.panelOpen === undefined ? true : o.panelOpen,
    _chatView: o.view === undefined ? "room" : o.view,
    _latestVotes: votes,
    showToast: (m, k) => toasts.push(String(m)),
    _sendVote: (p, body, msg) => sent.push({ path: p, body }),
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(BLOCK, sandbox, { filename: "slash-block.js" });
  const ev = (code) => vm.runInContext(code, sandbox);
  const type = (s) => { els["pv-chat-text"].value = s; ev("_slashMuted = false; _slashRefresh();"); };
  const rows = () => els["pv-slash-menu"].children.filter(c => /\bpvs-row\b/.test(c.className));
  const textOf = (r) => ({
    chip: r.children[0].textContent,
    name: r.children[1].children[0].textContent,
    hint: r.children[1].children[1].textContent,
    dead: /\bdead\b/.test(r.className),
    on: /\bon\b/.test(r.className.split(/\s+/).join(" ")),
  });
  const plain = (c) => els["pv-slash-menu"].children
    .filter(x => new RegExp("\\b" + c + "\\b").test(x.className))
    .map(x => x.textContent);
  return { sandbox, ev, els, toasts, sent, type, rows, textOf, plain,
           open: () => els["pv-slash-menu"].classList.contains("open") };
}

const VOTES = {
  ballot_seat: 0,
  kick: [
    { seat: 1, name: "Bob",   votes: 1, needed: 3, mine: true,  blocked: false },
    { seat: 2, name: "Bonny", votes: 0, needed: 3, mine: false, blocked: false },
    { seat: 3, name: "Hosty", votes: 0, needed: 3, mine: false, blocked: true  },
  ],
  skip: { seat: 2, name: "Bonny", votes: 1, needed: 2, mine: false, blocked: false },
};
const NO_VOTES = { ballot_seat: null, kick: [], skip: null };

// ══ 1. What counts as a command ══════════════════════════════════════════════
section("a slash is a command, a sentence is a sentence:");
{
  const e = makeEnv(VOTES);
  const P = (s) => e.ev("JSON.stringify(_slashParse(" + JSON.stringify(s) + "))");
  const parse = (s) => JSON.parse(P(s) === undefined ? "null" : P(s));

  check(parse("/") && parse("/").stage === "cmd", "a bare slash opens the command list");
  check(parse("/k").stage === "cmd" && parse("/k").filter === "k", "a half-typed command narrows the list");
  check(parse("/KICK").stage === "kick", "the command is not case sensitive");
  check(parse("/kick").stage === "kick" && parse("/kick").filter === "",
        "the command spelled out in full moves straight on to the players");
  check(parse("/kick ").stage === "kick", "a trailing space does the same");
  check(parse("/kick Bo").filter === "Bo", "what follows it is the name being typed");
  check(parse("/skip").stage === "skip", "and the same for /skip");
  check(parse("hello") === null, "an ordinary message is not a command");
  check(parse("") === null, "nor is an empty box");
  check(parse("/2 cards left") === null, "nor a sentence that happens to open with a slash");
  check(parse("/kicker off") === null, "nor a word that only starts like one");
  check(parse("/kicker") && parse("/kicker").stage === "cmd",
        "but /kicker on its own is answered rather than broadcast");
}

// ══ 2. The list is what the server says you may vote on ══════════════════════
section("the list offers exactly what the payload allows:");
{
  const e = makeEnv(VOTES);
  e.type("/");
  check(e.open(), "typing a slash opens the list");
  check(e.rows().length === 2, "both commands are offered", String(e.rows().length));
  check(e.textOf(e.rows()[0]).name === "/kick" && e.textOf(e.rows()[1]).name === "/skip",
        "which are /kick and /skip");

  e.type("/s");
  check(e.rows().length === 1 && e.textOf(e.rows()[0]).name === "/skip",
        "a letter narrows the commands to one");

  e.type("/kick");
  const r = e.rows().map(e.textOf);
  check(r.length === 3, "every player this viewer may vote on is listed", String(r.length));
  check(r.map(x => x.chip).join(",") === "P2,P3,P4",
        "each one is labelled by seat, the way the seat pills are", r.map(x => x.chip).join(","));
  check(r.map(x => x.name).join(",") === "Bob,Bonny,Hosty", "with the player's own name beside it");
  check(/1\/3/.test(r[0].hint) && /takes it back/.test(r[0].hint),
        "a vote already cast says so, and that pressing it again takes it back");
  check(/everyone must agree/.test(r[1].hint), "the others say the vote needs everyone");
  check(r[2].dead === true && /host runs the lobby/.test(r[2].hint),
        "a player the server blocked is shown, greyed, with the reason");
  check(e.plain("pvs-foot").join(" ").includes("permanent"),
        "the list spells out that a kick is permanent");

  e.type("/kick bo");
  check(e.rows().length === 2, "a name being typed narrows the players", String(e.rows().length));
  e.type("/kick bob");
  check(e.rows().length === 1 && e.textOf(e.rows()[0]).name === "Bob", "and narrows them to one");
  e.type("/kick P4");
  check(e.rows().length === 1 && e.textOf(e.rows()[0]).name === "Hosty", "a seat label finds its player");
  e.type("/kick nobody");
  check(e.rows().length === 0 && e.plain("pvs-empty").join(" ").includes("nobody"),
        "a name nobody has says so instead of listing everyone");

  e.type("/skip");
  check(e.rows().length === 1 && e.textOf(e.rows()[0]).name === "Bonny",
        "/skip offers the one player whose turn it is");
}
{
  const e = makeEnv(NO_VOTES);
  e.type("/kick");
  check(e.rows().length === 0 && /bots/.test(e.plain("pvs-empty").join(" ")),
        "a game against bots says there is nobody to vote out");
  e.type("/skip");
  check(e.rows().length === 0 && /no turn/.test(e.plain("pvs-empty").join(" ")),
        "and no turn to skip when the payload carries none");
}
{
  const e = makeEnv(VOTES, { view: "conv" });
  e.type("/kick");
  check(!e.open(), "the list never opens over a direct message");
  const e2 = makeEnv(VOTES, { panelOpen: false });
  e2.type("/kick");
  check(!e2.open(), "nor while the chat panel is shut");
}

// ══ 3. Taking a row fills the box in, and never votes ════════════════════════
section("taking a row types for you, it does not vote:");
{
  const e = makeEnv(VOTES);
  e.type("/");
  e.rows()[0].fire("click");
  check(e.els["pv-chat-text"].value === "/kick ", "taking /kick writes it into the box",
        JSON.stringify(e.els["pv-chat-text"].value));
  check(e.open() && e.rows().length === 3, "and the list becomes the players it can name");
  check(e.sent.length === 0, "nothing has been voted on yet");

  e.rows()[1].fire("click");
  check(e.els["pv-chat-text"].value === "/kick Bonny", "taking a player fills the name in");
  check(e.sent.length === 0, "still nothing voted on: Enter is what sends it");
  check(!e.open(), "the list goes away once there is nothing left to fill in");

  e.type("/kick");
  const before = e.els["pv-chat-text"].value;
  e.rows()[2].fire("click");
  check(e.els["pv-chat-text"].value === before, "a blocked player cannot be taken at all");
}

// ══ 4. Arrows walk the list, skipping what cannot be taken ═══════════════════
section("the keyboard walks the list:");
{
  const e = makeEnv(VOTES);
  e.type("/kick");
  check(e.ev("_slashPick") === 0, "it starts on the first row");
  e.ev("_slashMove(1)");
  check(e.ev("_slashPick") === 1, "down moves down one");
  e.ev("_slashMove(1)");
  check(e.ev("_slashPick") === 0, "and steps over the blocked row rather than landing on it",
        String(e.ev("_slashPick")));
  e.ev("_slashMove(-1)");
  check(e.ev("_slashPick") === 1, "up wraps round the same way");
  check(/\bon\b/.test(e.rows()[1].className), "the row it is on is marked for the eye");
}

// ══ 5. Running it: the right endpoint, the right seat ════════════════════════
section("running the command:");
{
  const e = makeEnv(VOTES);
  check(e.ev('_slashRun("/kick Bonny")') === true, "a complete kick command is consumed");
  check(e.sent.length === 1 && e.sent[0].path === "kick_player", "it posts to kick_player");
  check(e.sent[0].body.target_seat_index === 2, "at the seat that name belongs to");
  check(e.sent[0].body.undo === false, "as a new vote");

  const e2 = makeEnv(VOTES);
  e2.ev('_slashRun("/kick P2")');
  check(e2.sent[0].body.target_seat_index === 1 && e2.sent[0].body.undo === true,
        "a vote of mine, named by seat, is sent as taking that vote back");

  const e3 = makeEnv(VOTES);
  e3.ev('_slashRun("/skip")');
  check(e3.sent.length === 1 && e3.sent[0].path === "skip_turn"
        && e3.sent[0].body.target_seat_index === 2,
        "/skip needs no name: there is only one player whose turn it is");

  const e4 = makeEnv(VOTES);
  check(e4.ev('_slashRun("/kick")') === true, "/kick with nobody named is still consumed");
  check(e4.sent.length === 0 && /which player/i.test(e4.toasts.join(" ")),
        "it asks which player instead of picking one", e4.toasts.join(" | "));
  check(e4.ev("_slashRows.length") === 3, "and puts the list back up to be picked from");

  const e5 = makeEnv(VOTES);
  check(e5.ev('_slashRun("/kick bo")') === true, "a name that matches two players is consumed");
  check(e5.sent.length === 0 && /P2|P3/.test(e5.toasts.join(" ")),
        "and names the ones it could have meant");
  check(e5.els["pv-chat-text"].value === "/kick bo",
        "keeping what was typed, so the list stays narrowed to those two",
        JSON.stringify(e5.els["pv-chat-text"].value));
  check(e5.ev("_slashRows.length") === 2, "which is what the list comes back showing",
        String(e5.ev("_slashRows.length")));

  const e5b = makeEnv(VOTES);
  e5b.ev('_slashRun("/kick Bobb")');
  check(e5b.els["pv-chat-text"].value === "/kick ",
        "a name nobody has is taken back off, ready to be retyped",
        JSON.stringify(e5b.els["pv-chat-text"].value));

  const e6 = makeEnv(VOTES);
  e6.ev('_slashRun("/kick Bobb")');
  check(e6.sent.length === 0 && /Bobb/.test(e6.toasts.join(" ")),
        "a misspelled name is answered, not sent to the table");

  const e7 = makeEnv(VOTES);
  e7.ev('_slashRun("/kick Hosty")');
  check(e7.sent.length === 0 && /host runs the lobby/.test(e7.toasts.join(" ")),
        "a blocked player says why rather than failing at the server");

  const e8 = makeEnv(VOTES);
  e8.ev('_slashRun("/wave")');
  check(e8.sent.length === 0 && /\/kick and \/skip/.test(e8.toasts.join(" ")),
        "an unknown command names the two that exist");

  const e9 = makeEnv(NO_VOTES);
  e9.ev('_slashRun("/kick Bob")');
  check(e9.sent.length === 0 && /nobody/i.test(e9.toasts.join(" ")),
        "with nobody to vote on, it says so rather than posting");

  const e10 = makeEnv(VOTES);
  check(e10.ev('_slashRun("hello table")') === false, "an ordinary message is not consumed");
  check(e10.ev('_slashRun("/2 cards left")') === false, "nor one that opens with a slash and a digit");
  check(e10.sent.length === 0 && e10.toasts.length === 0, "and neither one says anything");
}

// ══ 6. The wiring: nothing above is reachable unless this holds ══════════════
section("the wiring behind it:");
{
  const send = APP.slice(APP.indexOf("async function sendChatMessage()"),
                         APP.indexOf("_gameAchTracker.chatMsgs"));
  check(/if \(_slashRun\(text\)\)/.test(send), "sending a message runs the command instead, if it is one");
  check(send.indexOf("_slashRun(text)") < send.indexOf("CC_PROFANITY.clean"),
        "before the message is cleaned and posted, not after");
  check(/if \(_slashRun\(text\)\) return;/.test(send),
        "and nothing else happens to a message that was really a command");

  const keys = APP.slice(APP.indexOf('_pg("pv-chat-text").addEventListener("keydown"'),
                         APP.indexOf("SLASH COMMANDS IN CHAT"));
  check(/if \(_slashIsOpen\(\)\)/.test(keys), "the open list borrows the keys");
  check(/ArrowDown|ArrowUp/.test(keys) && /_slashMove/.test(keys), "arrows walk it");
  check(/e\.key === "Tab"/.test(keys) && /_slashTake/.test(keys), "Tab completes the highlighted row");
  check(/e\.key === "Escape"[\s\S]{0,80}_slashClose\(true\)/.test(keys), "Escape puts it away");
  check(/row\.insert !== e\.target\.value/.test(keys),
        "Enter only completes while there is something left to complete");
  check(/if \(e\.key === "Enter" && !e\.shiftKey\) \{ e\.preventDefault\(\); sendChatMessage\(\); \}/.test(keys),
        "otherwise Enter sends, which is what runs the command");

  const votesSync = APP.slice(APP.indexOf('_latestVotes = (_v && typeof _v === "object")'),
                              APP.indexOf("const mySeat = (Number.isInteger(myIdx))"));
  check(/_slashRefresh\(\)/.test(votesSync),
        "every payload that carries tallies repaints an open list");
  check(!/updateVoteButtons/.test(APP),
        "and nothing repaints buttons that are not there any more");

  const badges = APP.slice(APP.indexOf("function pvcUpdateBadges()"), APP.indexOf("function pvcBackDotShould"));
  check(/classList\.toggle\("has-unread", total > 0\)/.test(badges),
        "the unread pulse is decided where the count is");

  const closeP = APP.slice(APP.indexOf("function pvcClosePanel()"), APP.indexOf("function pvcTogglePanel"));
  check(/_slashClose\(\)/.test(closeP), "closing the panel puts the list down");
  const showV = APP.slice(APP.indexOf("function pvcShowView(view)"), APP.indexOf("function pvcShowView(view)") + 700);
  check(/view !== "room"[\s\S]{0,40}_slashClose\(\)/.test(showV),
        "and so does leaving the room view");

  const wiring = APP.slice(APP.indexOf("  // ── Wiring: the message box,"),
                           APP.indexOf("  // ── Wiring: the message box,") + 1600);
  check(/box\.addEventListener\("input"/.test(wiring), "every keystroke redraws the list");
  check(/box\.addEventListener\("blur"/.test(wiring), "clicking away puts it down");
  check(!/pv-chat-slash-btn/.test(APP) && !HTML.includes("pv-chat-slash-btn"),
        "the / button beside the box is gone, markup and wiring both");
  check(!/#pv-chat-slash-btn/.test(CSS), "and its style went with it");
  check(/mousedown", \(e\) => e\.preventDefault\(\)/.test(BLOCK),
        "a row does not steal the caret out of the box");
}

// ══ 7. Where the chat button lives now ══════════════════════════════════════
section("the Messages button is in the action bar:");
{
  const barStart = HTML.indexOf('<div id="pv-action-bar">');
  const barEnd   = HTML.indexOf('<!-- Click-to-place card picker panel -->');
  check(barStart > 0 && barEnd > barStart, "the action bar is where it always was");
  const bar = HTML.slice(barStart, barEnd);
  check(bar.includes('id="pv-chat-btn"'), "the messages button is in it");
  check(bar.indexOf('id="pv-chat-btn"') < bar.indexOf('id="pv-surf-btn"'),
        "immediately left of Surf's Up");
  check(/class="pv-btn pv-btn-chat"/.test(bar), "styled as an action-bar button");
  check(bar.includes('id="pv-chat-badge"'), "and it still carries the unread badge");
  check(/style="display:none;"/.test(bar.slice(bar.indexOf('id="pv-chat-btn"') - 200,
                                                bar.indexOf('id="pv-chat-btn"') + 200)),
        "hidden until a game is running, the way it was before");

  const clusterAt = HTML.indexOf('<div id="bs-ctrl"');
  const cluster = HTML.slice(clusterAt, HTML.indexOf("</div>", HTML.indexOf('id="bs-minus"')));
  check(!cluster.includes("pv-chat-btn"), "it is gone from the Board Size cluster");
  check(!HTML.includes("bs-chat-sep"), "and so is the separator that sat above it");
  check(!CSS.includes("bs-chat-sep"), "with its style");
  check(!APP.includes("bs-chat-sep"), "and nothing in the app reaches for it");
  check(!/#pv-chat-btn \{/.test(CSS), "the old 44px corner square style is gone");
  check(!CSS.includes("pvc-btn-ico"), "along with the icon slot it never used");

  // The label and the second line that teaches the commands. The second line
  // is load-bearing now that the two vote buttons are gone: it is the only
  // thing on screen that says where they went.
  check(/<span class="pvc-btn-lbl">Messages 💬<\/span>/.test(bar),
        "it says Messages, with the speech balloon after the word");
  check(/type \/ for commands/.test(bar), "and says what the slash does");
  check(/aria-label="Open messages"/.test(bar), "a screen reader is told the same word");
}

// ══ 7b. And the two vote buttons are gone, not hidden ══════════════════════
section("Vote Kick and Skip Turn are buttons nowhere:");
{
  ["pv-skip-turn-btn", "pv-kick-wrap", "pv-kick-btn", "pv-kick-picker"].forEach(id => {
    check(!HTML.includes('id="' + id + '"'), "#" + id + " is gone from preview.html");
    check(!APP.includes(id), "…and nothing in the app reaches for it");
  });
  ["pv-btn-skip", "pv-btn-kick", "pv-kick-wrap", "pv-kick-picker", "pv-vote-head",
   "pv-vote-row", "pv-vote-foot"].forEach(cls => {
    check(!CSS.includes("." + cls), "preview.css no longer styles ." + cls);
  });
  ["closeKickPicker", "openKickPicker", "_kickRow", "_kickInfoFor", "_skipInfoFor",
   "_clampToWindow", "_kickPickerOpen"].forEach(fn => {
    check(!APP.includes(fn), "the picker's " + fn + " went with them");
  });
  // What has to survive: the poster the commands call, and the seat badge that
  // says somebody was removed, which is a status and not a control.
  check(/async function _sendVote\(path, body, okMsg\)/.test(APP),
        "the one thing that posts a vote is still here");
  check(/_sendVote\("kick_player"/.test(APP) && /_sendVote\("skip_turn"/.test(APP),
        "and both votes still go to their own endpoint");
  check(/pv-seat-kicked-badge/.test(APP) && /\.pv-seat-kicked-badge/.test(CSS),
        "a removed player's seat still shows the Removed badge");
}

section("it matches the game, and it is hard to miss:");
{
  const btn = CSS.slice(CSS.indexOf("    .pv-btn-chat {"), CSS.indexOf("#pv-slash-menu {"));
  check(btn.length > 200, "the button has a style of its own");
  check(/var\(--cr-gold\)/.test(btn) && /var\(--cr-gold-deep\)/.test(btn),
        "in the in-game reef gold, not another blue");
  check(/color: var\(--cr-navy\)/.test(btn), "with the in-game navy on it, like the rest of the bar");
  check(/\.pv-btn-chat\.has-unread \{\s*animation: chat-unread-pulse/.test(CSS),
        "a waiting message pulses the button");
  check(/prefers-reduced-motion: reduce\) \{\s*\.pv-btn-chat\.has-unread \{ animation: none/.test(CSS),
        "and holds still for anyone who asked for that");
  check(/var\(--cr-cream\)/.test(btn), "the badge ring is the cream of the bar it sits on");
  check(/#pv-action-bar \.pv-btn-chat \.pvc-btn-sub \{ display: none; \}/.test(CSS),
        "a short screen drops the second line, exactly as Strategy does");
  // Nothing in the bar may be gold twice over: Strategy is cream with a gold
  // edge, this is the only solid gold control.
  check(/#pv-help-btn \{[\s\S]{0,400}var\(--cr-cream\)/.test(CSS),
        "Strategy is still the cream one at the other end");

  const menu = CSS.slice(CSS.indexOf("    #pv-slash-menu {"), CSS.indexOf("    #pv-chat-panel {"));
  check(/position: absolute/.test(menu) && /bottom: calc\(100% - 6px\)/.test(menu),
        "the list opens upwards, over the messages, out of the input row");
  check(/#pv-chat-input-row \{[\s\S]{0,260}position: relative/.test(CSS),
        "which only works because the input row is what it is positioned against");
  check(/max-height: 190px/.test(menu) && /overflow-y: auto/.test(menu),
        "eight players at a table cannot push it off the top of the panel");
  check(/#pv-slash-menu\.open \{ display: flex; \}/.test(CSS), "and it is hidden until it is opened");
  [".pvs-head", ".pvs-row", ".pvs-chip", ".pvs-name", ".pvs-hint", ".pvs-foot", ".pvs-empty"].forEach(c => {
    check(CSS.includes(c + " {") || CSS.includes(c + ",") || CSS.includes(c + ":"),
          "every part of a row is styled (" + c + ")");
  });
  check(/@media \(pointer: coarse\)[\s\S]{0,900}\.pvs-name \{ font-size: 12\.5px/.test(CSS),
        "a row of the list is readable on a phone");
}

// ══ 8. Every id the JS reaches for is really in the markup ══════════════════
section("nothing reaches for an id that is not there:");
["pv-slash-menu", "pv-chat-text", "pv-chat-btn", "pv-chat-badge"].forEach(id => {
  check(HTML.includes('id="' + id + '"'), "#" + id + " is in preview.html");
  check(APP.includes(id), "...and preview-app.js drives it");
});

// ══ 9. The build stamp, or none of this reaches a returning browser ═════════
section("the build stamp:");
{
  const VER = JSON.parse(fs.readFileSync(path.join(CLIENT, "version.json"), "utf8"));
  const SW  = fs.readFileSync(path.join(CLIENT, "sw.js"), "utf8");
  const PRIV = fs.readFileSync(path.join(CLIENT, "privacy.html"), "utf8");
  const build = (APP.match(/const APP_BUILD\s*=\s*"([^"]+)"/) || [])[1];
  check(Boolean(build), "the app carries a build");
  check(VER.build === build, "version.json agrees with it", VER.build + " vs " + build);
  // Only the CODE stamps are the build: art carries its own per-file ?v=, since
  // a photograph does not change when the app does.
  const codeStamps = (h) => new Set((h.match(/\/(?:js|css)\/[\w.-]+\?v=([^"'\s>]+)/g) || [])
    .map(m => m.slice(m.indexOf("?v=") + 3)));
  const stamps = codeStamps(HTML);
  check(stamps.size === 1 && stamps.has(build),
        "every script and stylesheet in preview.html asks for this build",
        Array.from(stamps).join(","));
  const pstamps = codeStamps(PRIV);
  check(pstamps.size >= 1 && Array.from(pstamps).every(v => v === build),
        "and so does privacy.html", Array.from(pstamps).join(","));
  check((HTML.match(new RegExp(build.replace(/\./g, "\\."), "g")) || []).length >= 30,
        "the whole page was restamped, not just the file that changed");
  check(/APP_CACHE = "fish-multiplayer-v(\d+)"/.test(SW), "the service worker cache is named");
}

// ══ 10. House rules ════════════════════════════════════════════════════════
section("house rules:");
{
  const lits = (BLOCK.match(/"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`/g) || []);
  const em = lits.find(l => l.includes("—"));
  check(!em, "nothing the command list prints has an em dash in it", em);
  const EMOJI = /[\u{1F300}-\u{1FAFF}\u{1F000}-\u{1F0FF}\u{2600}-\u{27BF}]/gu;
  const emo = lits.find(l => EMOJI.test(l));
  check(!emo, "and no emoji in what the command list prints", emo);
  // The button is the exception Timothy asked for: one speech balloon, after
  // the word, and nothing else in the bar wears one.
  const barSlice = HTML.slice(HTML.indexOf('id="pv-chat-btn"'), HTML.indexOf('id="pv-surf-btn"'));
  const found = barSlice.match(EMOJI) || [];
  check(found.join("") === "💬", "the only emoji on the button is the speech balloon", found.join(""));
  check(/>Messages 💬</.test(barSlice), "and it comes after the word, not before it");
}

// ══ 11. And now for real, in a browser ═════════════════════════════════════
// Everything above reads the source or runs it against a DOM made of objects.
// This lays the REAL action bar out with the REAL stylesheet at three window
// widths and drives the list with real events, because the two things most
// likely to be wrong here are not logic: a button that is in the markup but
// laid out behind something, and a list that opens off the top of the panel.
section("in a browser, at three window widths:");
{
  const os = require("os");
  const { execFileSync } = require("child_process");
  const CHROME = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome", "/usr/bin/chromium",
  ].find(p => fs.existsSync(p));
  if (!CHROME) {
    console.log("  SKIP: no Chrome/Chromium found, the layout half did not run.");
  } else {
    const cut = (src, a, b, what) => {
      const i = src.indexOf(a), j = src.indexOf(b);
      if (i < 0 || j < 0 || j < i) throw new Error("cannot slice " + what);
      return src.slice(i, j);
    };
    const BAR   = cut(HTML, '<div id="pv-action-bar">', "<!-- Click-to-place card picker panel -->", "bar");
    const PANEL = cut(HTML, '<div id="pv-chat-panel"', "<!-- ══ MESSAGING DRAWER", "panel");
    const KEYS  = cut(APP, '  _pg("pv-chat-text").addEventListener("keydown"',
                           "  const _SLASH_CMDS = [", "keydown");
    // BLOCK stops short of the DOM wiring on purpose (the object-DOM half above
    // has no elements to wire). The browser has them, so it gets the wiring too,
    // and the list is driven through the real input and keydown listeners.
    const WIRED = cut(APP, "  const _SLASH_CMDS = [", "  // \u2500\u2500 Draggable panel", "wired block");
    const RUNTIME = KEYS + WIRED;

    const page = `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#0D2A52}
#stage{position:absolute;left:0;right:0;bottom:0}
${CSS}
</style></head><body>
<div id="stage">${BAR}</div>
${PANEL}
<div id="out">RUNNING</div>
<script>
const _pg = (id) => document.getElementById(id);
function cl(el){ while(el.firstChild) el.removeChild(el.firstChild); }
let _chatPanelOpen = true, _chatView = "room";
const TOASTS = [], SENT = [];
function showToast(m){ TOASTS.push(String(m)); }
function _sendVote(p, body){ SENT.push({path:p, body:body}); }
function sendChatMessage(){ _slashRun(String(_pg("pv-chat-text").value||"").trim()); }
let _latestVotes = ${JSON.stringify(VOTES)};
${RUNTIME}
<\/script>
<script>
(function(){
  const out = { fail: [], info: {} };
  const R = (id) => document.getElementById(id).getBoundingClientRect();
  const el = (id) => document.getElementById(id);
  const bad = (m) => out.fail.push(m);
  // What a running match shows.
  el("pv-chat-btn").style.display = "";

  const bar = R("pv-action-bar"), chat = R("pv-chat-btn"), surf = R("pv-surf-btn");
  out.info.chat = [chat.left,chat.top,chat.width,chat.height].map(Math.round).join(",");
  if (chat.left < bar.left - 1 || chat.right > bar.right + 1) bad("the chat button is outside the action bar");
  if (chat.top < bar.top - 1 || chat.bottom > bar.bottom + 1) bad("the chat button overflows the bar");
  // The bar wraps on a narrow window: "before" is the same row further left,
  // or an earlier row.
  const before = (a,b) => (Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top) > 4)
    ? a.right <= b.left + 1 : a.top < b.top - 4;
  if (!before(chat, surf)) bad("the messages button does not come before Surf's Up");
  if (chat.height < 30 || chat.width < 80) bad("the messages button is too small to be prominent: " + out.info.chat);
  const hit = document.elementFromPoint(chat.left + chat.width/2, chat.top + chat.height/2);
  if (!hit || !el("pv-chat-btn").contains(hit)) bad("something covers the messages button: " + (hit && hit.id));
  const cs = getComputedStyle(el("pv-chat-btn"));
  if (!/232,\\s*179,\\s*74/.test(cs.backgroundImage)) bad("it is not painted the reef gold: " + cs.backgroundImage);
  if (!/26,\\s*45,\\s*90/.test(cs.color)) bad("its label is not the reef navy: " + cs.color);
  const abar = el("pv-action-bar");
  if (abar.scrollWidth > abar.clientWidth + 2)
    bad("the action bar overflows sideways: " + abar.scrollWidth + " > " + abar.clientWidth);

  el("pv-chat-panel").classList.add("open");
  const box = el("pv-chat-text"), menu = el("pv-slash-menu");
  const typeIn = (s) => { box.value = s; box.dispatchEvent(new Event("input", {bubbles:true})); };
  const key = (k) => box.dispatchEvent(new KeyboardEvent("keydown", {key:k, bubbles:true, cancelable:true}));
  const rows = () => Array.prototype.slice.call(menu.querySelectorAll(".pvs-row"));
  const named = () => rows().map(r => r.querySelector(".pvs-name").textContent).join(",");

  if (document.getElementById("pv-chat-slash-btn")) bad("the / button is still in the panel");
  typeIn("/");
  if (!menu.classList.contains("open") || getComputedStyle(menu).display === "none")
    bad("the list did not open on a slash");
  const mr = menu.getBoundingClientRect(), ir = R("pv-chat-input-row"), pr = R("pv-chat-panel");
  out.info.menu = [mr.left,mr.top,mr.width,mr.height].map(Math.round).join(",");
  if (!(mr.bottom <= ir.top + 8)) bad("the list is not above the message box");
  if (mr.left < pr.left - 1 || mr.right > pr.right + 1) bad("the list hangs off the side of the panel");
  if (mr.top < pr.top - 1) bad("the list runs off the top of the panel");
  if (mr.height < 40) bad("the list has no height: " + mr.height);
  if (named() !== "/kick,/skip") bad("the commands are wrong: " + named());

  rows()[0].dispatchEvent(new MouseEvent("click", {bubbles:true, cancelable:true}));
  if (box.value !== "/kick ") bad("taking /kick wrote: " + JSON.stringify(box.value));
  const chips = rows().map(r => r.querySelector(".pvs-chip").textContent + ":" +
                                r.querySelector(".pvs-name").textContent).join(" ");
  out.info.players = chips;
  if (chips !== "P2:Bob P3:Bonny P4:Hosty") bad("the players are wrong: " + chips);
  if (SENT.length) bad("taking a row voted on its own");
  if (!rows()[2].classList.contains("dead")) bad("the blocked player is not marked");
  if (Number(getComputedStyle(rows()[2]).opacity) > 0.85) bad("the blocked player is not greyed");

  typeIn("/kick bon");
  if (named() !== "Bonny") bad("typing a name did not narrow the list: " + named());
  typeIn("/kick");
  key("ArrowDown");
  const on = menu.querySelector(".pvs-row.on");
  if (!on || on.querySelector(".pvs-name").textContent !== "Bonny") bad("the arrow did not move the highlight");
  key("Enter");
  if (box.value !== "/kick Bonny") bad("Enter did not complete the name: " + JSON.stringify(box.value));
  if (SENT.length) bad("completing a name voted straight away");
  if (menu.classList.contains("open")) bad("the list stayed up with nothing left to pick");
  key("Enter");
  if (SENT.length !== 1 || SENT[0].path !== "kick_player" || SENT[0].body.target_seat_index !== 2)
    bad("the second Enter did not cast the vote: " + JSON.stringify(SENT));
  if (box.value !== "") bad("the box was not cleared after the vote: " + JSON.stringify(box.value));

  typeIn("/skip");
  if (rows().length !== 1) bad("/skip offered " + rows().length + " players");
  key("Escape");
  if (menu.classList.contains("open")) bad("Escape did not put the list away");
  typeIn("/skip Bonny"); key("Enter");
  if (!SENT[1] || SENT[1].path !== "skip_turn") bad("skip did not post: " + JSON.stringify(SENT));

  document.getElementById("out").textContent =
    (out.fail.length ? "FAIL\\n" + out.fail.join("\\n") : "OK") + "\\n" + JSON.stringify(out.info);
})();
<\/script></body></html>`;

    // 1280 is a laptop, 900 trips the short-screen rules, 540 is the phone
    // viewport the game lays itself out at (device width x 1.5).
    [[1280, 900], [900, 900], [540, 1170]].forEach(([w, h]) => {
      const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-slash-"));
      const f = path.join(tmp, "p.html");
      fs.writeFileSync(f, page);
      let dom = "";
      try {
        dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
          `--window-size=${w},${h}`, "--virtual-time-budget=9000", "--dump-dom", "file://" + f],
          { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 90000 });
      } catch (e) {
        check(false, `Chrome ran at ${w}px`, e.message);
      }
      fs.rmSync(tmp, { recursive: true, force: true });
      const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
      const rep = m ? m[1].replace(/&lt;/g, "<").replace(/&gt;/g, ">")
                          .replace(/&quot;/g, '"').replace(/&amp;/g, "&").trim() : "(no output)";
      const lines = rep.split("\n");
      check(lines[0] === "OK", `everything holds at ${w}px`, lines.slice(0, 4).join(" | "));
    });
  }
}

console.log("\n" + pass + " passed, " + fail + " failed");
process.exit(fail ? 1 : 0);
