#!/usr/bin/env node
/* Tests for the per-game chat mute toggle (multiplayer/client/js/preview-app.js
 * + the 🔔 button / menu in multiplayer/client/preview.html).
 *
 * Run:  node test_chat_mute.js
 *
 * Why this file exists: "mute" here must silence NOTIFICATIONS ONLY — the 💬
 * button badge, the lobby badge and the back-arrow dot — while the messages
 * themselves keep arriving and the unread counters keep counting. If muting
 * ever zeroed a counter instead of hiding a badge, unmuting (or the next game)
 * would show a wrong number and messages received while muted would look read.
 * These tests pin that, plus the scope rule: the choice belongs to ONE room and
 * a different room starts unmuted.
 *
 * The functions live inside a 26k-line IIFE that needs Firebase to load, so we
 * lift the exact source text of the ones under test out of the file and run
 * them against stub state. If a function is renamed or its body changes shape,
 * extraction fails loudly rather than silently testing nothing.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const CLIENT = path.join(__dirname, "multiplayer", "client");
const SRC  = fs.readFileSync(path.join(CLIENT, "js", "preview-app.js"), "utf8");
const HTML = fs.readFileSync(path.join(CLIENT, "preview.html"), "utf8");
const CSS  = fs.readFileSync(path.join(CLIENT, "css", "preview.css"), "utf8");

let failures = 0, checks = 0;
function ok(cond, label) {
  checks++;
  if (cond) { console.log("  ✓ " + label); return; }
  failures++; console.log("  ✗ " + label);
}
function eq(actual, expected, label) {
  ok(JSON.stringify(actual) === JSON.stringify(expected),
     label + "  (got " + JSON.stringify(actual) + ", want " + JSON.stringify(expected) + ")");
}

// ── Lift the functions under test out of the monolith ───────────────────────
function extract(name) {
  const decl = "function " + name + "(";
  const start = SRC.indexOf(decl);
  if (start < 0) throw new Error("could not find function " + name + " in preview-app.js");
  const open = SRC.indexOf("{", SRC.indexOf(")", start));
  let depth = 0, i = open;
  for (; i < SRC.length; i++) {
    if (SRC[i] === "{") depth++;
    else if (SRC[i] === "}") { depth--; if (depth === 0) break; }
  }
  if (depth !== 0) throw new Error("unbalanced braces extracting " + name);
  return SRC.slice(start, i + 1);
}
function grab(re, what) {
  const m = SRC.match(re);
  if (!m) throw new Error("could not find " + what + " in preview-app.js");
  return m[0];
}

const CONSTS = [
  grab(/const _CHAT_MUTE_MODES = \[[^\]]*\];/, "_CHAT_MUTE_MODES"),
  grab(/const _chatMuteKey = [^\n]*;/,         "_chatMuteKey"),
  grab(/const _CHAT_MUTE_LABEL = \{[\s\S]*?\n  \};/, "_CHAT_MUTE_LABEL"),
].join("\n");

const FUNCS = ["_chatMuted", "pvcRoomUnread", "pvcOtherUnread", "pvcUpdateBadges",
               "pvcBackDotShould", "pvcMuteSync", "pvcSetMute", "pvcRenderMute",
               "pvcMuteMenuOpen", "pvcMuteMenuIsOpen"].map(extract).join("\n");

// ── Stub DOM: only what the lifted functions touch ──────────────────────────
function mkEl(id) {
  return {
    id, textContent: "", title: "", dataset: {},
    style: { display: "" },
    attrs: {},
    _cls: new Set(),
    classList: {
      add:    function (c) { this._o._cls.add(c); },
      remove: function (c) { this._o._cls.delete(c); },
      toggle: function (c, on) { on ? this._o._cls.add(c) : this._o._cls.delete(c); },
      contains: function (c) { return this._o._cls.has(c); },
    },
    setAttribute(k, v) { this.attrs[k] = v; },
    querySelectorAll() { return this.rows || []; },
  };
}
function makeEnv(opts) {
  opts = opts || {};
  const els = {};
  ["pv-chat-badge", "wr-chat-badge", "pv-chat-back-dot", "pv-chat-mute", "pv-chat-mute-menu"]
    .forEach(id => { els[id] = mkEl(id); els[id].classList._o = els[id]; });
  // the four menu rows, read straight out of the real markup
  els["pv-chat-mute-menu"].rows = HTML_MUTE_MODES.map(m => {
    const r = mkEl("row-" + m); r.classList._o = r; r.dataset.mute = m; return r;
  });

  const store = Object.assign({}, opts.store || {});
  const env = {
    roomId: opts.roomId === undefined ? "AAAAA" : opts.roomId,
    _chatMuteMode: opts.mode || "none",
    _chatMuteRoom: opts.muteRoom === undefined ? null : opts.muteRoom,
    _chatPanelOpen: !!opts.panelOpen,
    _chatView: opts.view || "room",
    _chatRoomTotal: opts.roomTotal || 0,
    _chatSeenCount: opts.seen || 0,
    _otherUnread: opts.otherUnread || 0,
    _pg: (id) => els[id] || null,
    _pvcMsg: () => ({ totalUnread: () => env._otherUnread }),
    pvcEnsureSubscribed: () => {},
    sessionStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
    },
    els, store,
  };
  const runner = new Function("env", `
    with (env) {
      ${CONSTS}
      ${FUNCS}
      return {
        pvcUpdateBadges, pvcBackDotShould, pvcMuteSync, pvcSetMute, pvcRenderMute,
        pvcMuteMenuOpen, pvcMuteMenuIsOpen, _chatMuted,
        state: () => ({ mode: _chatMuteMode, room: _chatMuteRoom }),
        setRoom: (r) => { roomId = r; },
      };
    }
  `);
  // `with` reads/writes hit env, so mutations inside the lifted code are visible.
  return Object.assign(env, { api: runner(env) });
}
const badge = (env, id) => (env.els[id].style.display === "none" ? null : env.els[id].textContent);

// ── The mute modes the UI actually offers ───────────────────────────────────
const HTML_MUTE_MODES = (HTML.match(/data-mute="([a-z]+)"/g) || [])
  .map(s => s.replace(/.*"([a-z]+)"/, "$1"));

console.log("\nMarkup + styling");
{
  ok(/id="pv-chat-mute"/.test(HTML), "🔔 mute button exists in the chat panel header");
  ok(HTML.indexOf('id="pv-chat-mute"') < HTML.indexOf('id="pv-chat-close"'),
     "mute button sits before the ✕ close button (inside .pvc-hdr-actions)");
  ok(/id="pv-chat-mute-menu"/.test(HTML), "mute menu exists");
  eq(HTML_MUTE_MODES, ["none", "game", "dm", "all"],
     "menu offers exactly: notify me / this game's chat / DMs / both");
  const modesInJs = (SRC.match(/const _CHAT_MUTE_MODES = \[([^\]]*)\]/)[1].match(/"([a-z]+)"/g) || [])
    .map(s => s.replace(/"/g, ""));
  eq(modesInJs, HTML_MUTE_MODES, "every menu row maps to a mode the JS accepts");
  ok(/#pv-chat-mute-menu\.open\s*\{[^}]*display:\s*block/.test(CSS),
     "the menu is hidden until .open (CSS)");
  ok(/#pv-chat-mute-menu\s*\{[\s\S]*?display:\s*none/.test(CSS), "menu defaults to display:none");
  ok(/#pv-chat-mute\.muted\s*\{/.test(CSS), "muted button gets its own colour");
  // The panel clips its children, so the menu must fit inside the 330px panel.
  const w = Number((CSS.match(/#pv-chat-mute-menu\s*\{[\s\S]*?width:\s*(\d+)px/) || [])[1]);
  ok(w > 0 && w <= 300, "menu is narrow enough not to be clipped by the panel (" + w + "px)");
}

console.log("\n_chatMuted() covers each category");
{
  const cases = {
    none: { game: false, dm: false },
    game: { game: true,  dm: false },
    dm:   { game: false, dm: true  },
    all:  { game: true,  dm: true  },
  };
  Object.keys(cases).forEach(mode => {
    const env = makeEnv({ mode });
    eq({ game: env.api._chatMuted("game"), dm: env.api._chatMuted("dm") }, cases[mode],
       'mode "' + mode + '" mutes ' + JSON.stringify(cases[mode]));
  });
}

console.log("\nBadges: what each mode silences");
{
  // 3 unread room messages + 2 unread DMs, panel closed.
  const base = { roomTotal: 3, seen: 0, otherUnread: 2, muteRoom: "AAAAA" };
  const want = {
    none: { chat: "5", lobby: "3" },
    game: { chat: "2", lobby: null },
    dm:   { chat: "3", lobby: "3" },
    all:  { chat: null, lobby: null },
  };
  Object.keys(want).forEach(mode => {
    const env = makeEnv(Object.assign({ mode }, base));
    env.api.pvcUpdateBadges();
    eq({ chat: badge(env, "pv-chat-badge"), lobby: badge(env, "wr-chat-badge") }, want[mode],
       'mode "' + mode + '" → chat/lobby badge');
  });
}

console.log("\nMuting hides the badge — it never eats the messages");
{
  const env = makeEnv({ mode: "all", muteRoom: "AAAAA", roomTotal: 4, seen: 0, otherUnread: 3 });
  env.api.pvcUpdateBadges();
  eq(badge(env, "pv-chat-badge"), null, "muted: no badge");
  eq(env._chatRoomTotal - env._chatSeenCount, 4, "…but the room's unread count is untouched");
  eq(env._pvcMsg().totalUnread(), 3, "…and the DM unread count is untouched");
  env.api.pvcSetMute("none");                       // unmute mid-game
  eq(badge(env, "pv-chat-badge"), "7", "unmuting restores the FULL count (4 room + 3 DM)");
  eq(badge(env, "wr-chat-badge"), "4", "…and the lobby badge comes back too");
}

console.log("\nOpening the chat still clears unread while muted");
{
  const env = makeEnv({ mode: "game", muteRoom: "AAAAA", roomTotal: 6, seen: 0,
                        panelOpen: true, view: "room" });
  env._chatSeenCount = env._chatRoomTotal;          // what pvcShowView("room") does
  env.api.pvcUpdateBadges();
  eq(badge(env, "pv-chat-badge"), null, "read-while-muted leaves nothing pending");
  env._chatPanelOpen = false;
  env.api.pvcSetMute("none");
  eq(badge(env, "pv-chat-badge"), null, "unmuting after reading does NOT resurrect old messages");
}

console.log("\n9+ cap survives muting one side");
{
  const env = makeEnv({ mode: "dm", muteRoom: "AAAAA", roomTotal: 12, seen: 0, otherUnread: 40 });
  env.api.pvcUpdateBadges();
  eq(badge(env, "pv-chat-badge"), "9+", "12 room unread with DMs muted still caps at 9+");
}

console.log("\nBack-arrow dot follows the same mute");
{
  const mk = (o) => makeEnv(Object.assign({ muteRoom: "AAAAA", panelOpen: true }, o));
  ok(mk({ view: "room", otherUnread: 2, mode: "none" }).api.pvcBackDotShould(),
     "room view + unread DM → dot");
  ok(!mk({ view: "room", otherUnread: 2, mode: "dm" }).api.pvcBackDotShould(),
     "…silenced when DMs are muted");
  ok(mk({ view: "conv", roomTotal: 3, seen: 0, mode: "none" }).api.pvcBackDotShould(),
     "conv view + unread game chat → dot");
  ok(!mk({ view: "conv", roomTotal: 3, seen: 0, mode: "game" }).api.pvcBackDotShould(),
     "…silenced when the game chat is muted");
  ok(mk({ view: "conv", roomTotal: 3, seen: 0, otherUnread: 1, mode: "game" }).api.pvcBackDotShould(),
     "…but an unmuted DM still shows the dot");
  ok(!mk({ view: "list", otherUnread: 5, mode: "none" }).api.pvcBackDotShould(),
     "list view never shows the dot (unchanged)");
}

console.log("\nScope: the choice belongs to ONE game");
{
  const env = makeEnv({ roomId: "AAAAA", muteRoom: null });
  env.api.pvcMuteSync();
  eq(env.api.state(), { mode: "none", room: "AAAAA" }, "a fresh room starts unmuted");
  env.api.pvcSetMute("all");
  eq(env.store["ccChatMute:AAAAA"], "all", "the choice is stored under THIS room's id");

  env.api.setRoom("BBBBB");                          // joined a different game
  env.api.pvcMuteSync();
  eq(env.api.state(), { mode: "none", room: "BBBBB" }, "a different game starts unmuted again");

  env.api.setRoom("AAAAA");                          // rejoined / refreshed the same game
  env.api.pvcMuteSync();
  eq(env.api.state(), { mode: "all", room: "AAAAA" }, "rejoining the same game keeps the mute");
}
{
  const env = makeEnv({ roomId: "AAAAA", muteRoom: "AAAAA", mode: "all" });
  env.api.pvcMuteSync();
  eq(env.api.state(), { mode: "all", room: "AAAAA" }, "re-sync on the same room is a no-op");
  const env2 = makeEnv({ roomId: "CCCCC", muteRoom: null, store: { "ccChatMute:CCCCC": "bogus" } });
  env2.api.pvcMuteSync();
  eq(env2.api.state().mode, "none", "a corrupt stored value falls back to unmuted");
  const env3 = makeEnv({ roomId: "AAAAA", muteRoom: "AAAAA", mode: "none" });
  env3.api.pvcSetMute("nonsense");
  eq(env3.api.state().mode, "none", "pvcSetMute ignores a mode that isn't on the menu");
}

console.log("\nButton + menu reflect the state");
{
  const env = makeEnv({ mode: "none", muteRoom: "AAAAA" });
  env.api.pvcRenderMute();
  const btn = env.els["pv-chat-mute"];
  eq(btn.textContent, "🔔", "unmuted shows the bell");
  ok(!btn._cls.has("muted"), "unmuted has no .muted class");
  env.api.pvcSetMute("game");
  eq(btn.textContent, "🔕", "muted shows the crossed bell");
  ok(btn._cls.has("muted"), "muted adds .muted");
  ok(/muted/i.test(btn.title) && /change/i.test(btn.title), "tooltip says what is muted");
  const rows = env.els["pv-chat-mute-menu"].rows;
  eq(rows.filter(r => r._cls.has("sel")).map(r => r.dataset.mute), ["game"],
     "exactly one menu row is checked, and it is the active mode");
  eq(rows.find(r => r.dataset.mute === "game").attrs["aria-checked"], "true", "aria-checked follows");
  eq(rows.find(r => r.dataset.mute === "none").attrs["aria-checked"], "false", "…on both sides");
}
{
  const env = makeEnv({});
  ok(!env.api.pvcMuteMenuIsOpen(), "menu starts closed");
  env.api.pvcMuteMenuOpen(true);
  ok(env.api.pvcMuteMenuIsOpen() && env.els["pv-chat-mute-menu"]._cls.has("open"), "opens");
  eq(env.els["pv-chat-mute"].attrs["aria-expanded"], "true", "aria-expanded tracks the menu");
  env.api.pvcMuteMenuOpen(false);
  ok(!env.api.pvcMuteMenuIsOpen(), "closes");
}

console.log("\nWiring inside preview-app.js");
{
  ok(/pvcMuteSync\(\);\s*\/\/ adopt this room/.test(SRC), "pvcUpdateBadges syncs the room's choice");
  ok(/_chatMuteMode = "none"; _chatMuteRoom = null;/.test(SRC),
     "leaving a game drops the mute (next game starts unmuted)");
  // The invariant is that pvcClosePanel closes the mute menu — NOT that the two
  // statements sit on adjacent lines. The original regex required adjacency and
  // went red the moment the emote tray added a line between them, reporting a
  // broken menu that was never broken.
  {
    const closePanel = extract("pvcClosePanel");
    ok(/pvcMuteMenuOpen\(false\)/.test(closePanel) && /_chatPanelOpen = false/.test(closePanel),
       "closing the panel closes the menu");
  }
  ok(/mbtn\.addEventListener\("click"/.test(SRC), "the button opens the menu");
  ok(/pvcSetMute\(row\.dataset\.mute\)/.test(SRC), "picking a row applies that mode");
  ok(/document\.addEventListener\("click", \(\) => \{ if \(pvcMuteMenuIsOpen\(\)\)/.test(SRC),
     "clicking elsewhere dismisses the menu");
  // Muting must not touch the send/receive path.
  ok(!/_chatMute/.test(extract("sendChatMessage")), "sending is untouched by mute");
  ok(!/_chatMute/.test(extract("renderChatMessages")), "message rendering is untouched by mute");
}

console.log("\n" + (failures ? "FAILED " + failures + "/" + checks : "All " + checks + " checks passed"));
process.exit(failures ? 1 : 0);
