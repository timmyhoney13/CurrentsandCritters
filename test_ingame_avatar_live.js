#!/usr/bin/env node
/* Change your avatar mid-game and EVERY face in the game changes with you.
 * (multiplayer/client/js/preview-app.js)
 *
 * Run:  node test_ingame_avatar_live.js
 *
 * The server relays each player's current avatar on every state tick (proved
 * server-side by test_spectator_avatar.py + the /api/rooms/<id>/avatar route).
 * The client used to answer "what face is this person wearing?" a different
 * way on every surface, and most of those answers were frozen:
 *
 *   • the seat row read the relayed copy even for MY OWN seat, so my new icon
 *     sat behind the old one until a poll came back
 *   • a chat line kept the avatar baked into the message when it was sent
 *   • the draw notification and the final standings asked Firestore by
 *     nickname behind a 12-second cache, and drew a name-hash stranger for
 *     anyone without a profile doc
 *
 * Everything now resolves through pvLiveAvatar(). These tests pin that, and
 * pin the redraw guards that would otherwise leave a stale face on screen.
 *
 * The functions live inside an IIFE that needs a live DOM, so we lift their
 * exact source text out of the file and run it against stubs. If a function is
 * renamed or reshaped, extraction fails loudly rather than quietly testing
 * nothing.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const APP = fs.readFileSync(
  path.join(__dirname, "multiplayer", "client", "js", "preview-app.js"), "utf8");

let failures = 0, checks = 0;
function ok(cond, label) {
  checks++;
  if (cond) return;
  failures++; console.log("  ✗ " + label);
}
function eq(actual, expected, label) {
  ok(JSON.stringify(actual) === JSON.stringify(expected),
     label + "  (got " + JSON.stringify(actual) + ", want " + JSON.stringify(expected) + ")");
}

// ── Lift the functions under test out of the module ─────────────────────────
function extract(name) {
  const decl = "function " + name + "(";
  const start = APP.indexOf(decl);
  if (start < 0) throw new Error("could not find function " + name + " in preview-app.js");
  const open = APP.indexOf("{", APP.indexOf(")", start));
  let depth = 0, i = open;
  for (; i < APP.length; i++) {
    if (APP[i] === "{") depth++;
    else if (APP[i] === "}") { depth--; if (depth === 0) break; }
  }
  if (depth !== 0) throw new Error("unbalanced braces extracting " + name);
  return APP.slice(start, i + 1);
}
const AVATAR_TABLE = APP.match(/const PV_SEAT_AVATARS = \[[^\]]+\];/);
if (!AVATAR_TABLE) throw new Error("could not find PV_SEAT_AVATARS");

// ── A DOM small enough to reason about ──────────────────────────────────────
function El(tag) {
  const el = {
    tagName: tag, children: [], dataset: {}, style: {}, attrs: {},
    className: "", textContent: "", title: "", src: "", alt: "", loading: "",
    value: "", scrollTop: 0, scrollHeight: 0, onerror: null,
    classList: {
      _s: new Set(),
      add(c) { String(c).split(/\s+/).filter(Boolean).forEach(x => this._s.add(x)); },
      remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
      toggle(c, on) { on ? this.add(c) : this.remove(c); },
    },
    setAttribute(k, v) { el.attrs[k] = v; },
    getAttribute(k) { return el.attrs[k]; },
    appendChild(c) { el.children.push(c); return c; },
    removeChild(c) { el.children = el.children.filter(x => x !== c); return c; },
    insertBefore(c) { el.children.unshift(c); return c; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  Object.defineProperty(el, "firstChild", { get() { return el.children[0] || null; } });
  Object.defineProperty(el, "innerHTML", {
    get() { return ""; },
    set(v) { if (v === "") el.children = []; },
  });
  return el;
}
function findByClass(el, cls, out) {
  out = out || [];
  (el.children || []).forEach(c => {
    if (String(c.className || "").split(/\s+/).includes(cls)) out.push(c);
    findByClass(c, cls, out);
  });
  return out;
}
// The <img> src painted inside each element carrying `cls`.
function facesIn(el, cls) {
  return findByClass(el, cls).map(a => (a.children[0] && a.children[0].src) || null);
}

// ── Harness ─────────────────────────────────────────────────────────────────
function harness() {
  const els = {};
  const document = {
    getElementById(id) { return (els[id] = els[id] || El("div")); },
    createElement(tag) { return El(tag); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  const ctx = {
    els,
    myAvatar: "/avatars/clownfish.png",
    myBg: "",
    myName: "Tim",
    nickLookups: [],          // names the 12s-cache Firestore path was asked for
  };

  const src =
    AVATAR_TABLE[0] + "\n" +
    extract("pvSeatHash") + "\n" +
    extract("pvSeatDefaultAvatar") + "\n" +
    extract("noteLiveAvatars") + "\n" +
    extract("pvLiveAvatar") + "\n" +
    extract("pvLiveAvatarKey") + "\n" +
    extract("renderPlayerSeats") + "\n" +
    extract("renderChatMessages") + "\n" +
    "return { pvSeatDefaultAvatar, noteLiveAvatars, pvLiveAvatar, pvLiveAvatarKey," +
    "         renderPlayerSeats, renderChatMessages," +
    "         resetSeatKey: () => { _seatsRenderKey = null; } };";

  const fn = new Function(
    "document", "window", "ctx",
    // State the two renderers read out of the enclosing module.
    "let _seatsRenderKey = null;" +
    "let _latestSeatsForSurf = [];" +
    "let canInteract = false;" +
    "let _chatLastCount = -1, _chatLastAvatarKey = '';" +
    "let _chatRoomTotal = 0, _chatSeenCount = 0, _chatPanelOpen = false, _chatView = 'room';" +
    "let latestPayload = { viewer: { name: ctx.myName } };" +
    // Collaborators, stubbed to the smallest thing that still exercises the code.
    "function _avSrc(u) { return u; }" +
    "function _applyAvBg() {}" +
    "function cl(el) { el.children = []; }" +
    "function isLikelyAiName() { return false; }" +
    "function attachBoardHover() {}" +
    "function openBoardFocus() {}" +
    "function pvcUpdateBadges() {}" +
    "function pvcRenderList() {}" +
    src);

  const win = {
    __fishAvSrc: (p) => p,                     // the .webp sibling swap, off in tests
    __fishMyAvatarUrl: () => ctx.myAvatar,
    __fishEquippedBackground: () => ctx.myBg,
    __fishBackgroundCached: () => "",
    __fishApplyAvBg: () => {},
    __ccRefreshNames: () => {},
    __fishAvatarForNick: (n) => { ctx.nickLookups.push(n); return Promise.resolve(null); },
  };
  return { ctx, api: fn(document, win, ctx), win };
}

// Two players: me (seat 0) and someone else (seat 1).
function seats(myAv, theirAv) {
  return [
    { index: 0, name: "Tim",    score: 10, hand_count: 5, avatar: myAv,    background: "" },
    { index: 1, name: "Casey",  score: 12, hand_count: 6, avatar: theirAv, background: "" },
  ];
}

console.log("\n1. My own seat shows the icon I just picked, not the one the server still has");
{
  const h = harness();
  h.api.noteLiveAvatars(seats("/avatars/clownfish.png", "/avatars/osprey.png"), []);
  h.api.renderPlayerSeats(seats("/avatars/clownfish.png", "/avatars/osprey.png"), 0, 0);
  eq(facesIn(h.ctx.els["pv-seats-left"], "pv-seat-avatar-wrap").filter(Boolean),
     ["/avatars/clownfish.png", "/avatars/osprey.png"],
     "before the change, both seats wear what the server relayed");

  // I equip a new icon. The push is in flight, so the payload still carries the
  // old one for my seat, the render must not wait for the round trip.
  h.ctx.myAvatar = "/avatars/manta-ray.png";
  h.api.renderPlayerSeats(seats("/avatars/clownfish.png", "/avatars/osprey.png"), 0, 0);
  eq(facesIn(h.ctx.els["pv-seats-left"], "pv-seat-avatar-wrap").filter(Boolean),
     ["/avatars/manta-ray.png", "/avatars/osprey.png"],
     "my seat flips immediately; the other player's is untouched");
}

console.log("2. Another player's change lands on their seat on the next tick");
{
  const h = harness();
  h.api.renderPlayerSeats(seats("/avatars/clownfish.png", "/avatars/osprey.png"), 0, 0);
  h.api.renderPlayerSeats(seats("/avatars/clownfish.png", "/avatars/narwhal.png"), 0, 0);
  eq(facesIn(h.ctx.els["pv-seats-left"], "pv-seat-avatar-wrap").filter(Boolean),
     ["/avatars/clownfish.png", "/avatars/narwhal.png"],
     "the seat repaints: p.avatar is part of the render key");
}

console.log("3. pvLiveAvatar is the one answer, and mine is the local truth");
{
  const h = harness();
  h.api.noteLiveAvatars(seats("/avatars/clownfish.png", "/avatars/osprey.png"), []);
  eq(h.api.pvLiveAvatar("Casey"), "/avatars/osprey.png", "another player: the relayed seat icon");

  h.ctx.myAvatar = "/avatars/manta-ray.png";
  eq(h.api.pvLiveAvatar("Tim"), "/avatars/manta-ray.png",
     "me: my equipped icon wins over the round-trip-behind relayed copy");
  eq(h.api.pvLiveAvatar("Nobody"), "", "an unknown name resolves to nothing, so callers can fall back");
  eq(h.api.pvLiveAvatar(""), "", "an empty name never keys into the table");
}

console.log("4. A spectator keeps their face on the prefixed name their chat lines use");
{
  const h = harness();
  h.api.noteLiveAvatars([], [{ name: "Reader", avatar: "/avatars/narwhal.png" }]);
  eq(h.api.pvLiveAvatar("Reader"), "/avatars/narwhal.png", "by their plain name (spectator list)");
  eq(h.api.pvLiveAvatar("[Spectator] Reader"), "/avatars/narwhal.png",
     "and by the prefixed name the server puts on their chat lines");
}

console.log("5. Chat lines wear the sender's CURRENT face, not the one frozen into the message");
{
  const h = harness();
  const msgs = [
    { sender: "Casey", message: "hey",  ts: 1, avatar: "/avatars/osprey.png" },
    { sender: "Tim",   message: "hi",   ts: 2, avatar: "/avatars/clownfish.png" },
  ];
  h.api.noteLiveAvatars(seats("/avatars/clownfish.png", "/avatars/osprey.png"), []);
  h.api.renderChatMessages(msgs);
  eq(facesIn(h.ctx.els["pv-chat-messages"], "cm-avatar"),
     ["/avatars/osprey.png", "/avatars/clownfish.png"],
     "each line starts on the face its sender was wearing");

  // Casey changes icon. Not one new message has been sent.
  h.api.noteLiveAvatars(seats("/avatars/clownfish.png", "/avatars/narwhal.png"), []);
  h.api.renderChatMessages(msgs);
  eq(facesIn(h.ctx.els["pv-chat-messages"], "cm-avatar"),
     ["/avatars/narwhal.png", "/avatars/clownfish.png"],
     "Casey's line repaints even though the message count never moved");

  // And so does mine, the moment I pick a new one locally.
  h.ctx.myAvatar = "/avatars/manta-ray.png";
  h.api.renderChatMessages(msgs);
  eq(facesIn(h.ctx.els["pv-chat-messages"], "cm-avatar"),
     ["/avatars/narwhal.png", "/avatars/manta-ray.png"],
     "my own line follows my equipped icon straight away");
}

console.log("6. A sender with no live seat still gets the right face, then a sane default");
{
  const h = harness();
  h.api.noteLiveAvatars(seats("/avatars/clownfish.png", "/avatars/osprey.png"), []);
  h.api.renderChatMessages([
    // Someone who has since left the room: only the frozen copy is left.
    { sender: "Gone", message: "bye", ts: 1, avatar: "/avatars/lobster.png" },
    // A bot / an old client that never sent one: deterministic per-name default.
    { sender: "Bot Reef", message: "beep", ts: 2 },
  ]);
  const faces = facesIn(h.ctx.els["pv-chat-messages"], "cm-avatar");
  eq(faces[0], "/avatars/lobster.png", "the message's own copy is the fallback, not a stranger");
  ok(faces[1] && faces[1].startsWith("/avatars/"),
     "a sender with nothing at all still gets the name-hash default");
  eq(faces[1], h.api.pvSeatDefaultAvatar("Bot Reef"), "…the same default every other surface uses");
}

console.log("7. The redraw guards still skip work when genuinely nothing changed");
{
  const h = harness();
  const players = seats("/avatars/clownfish.png", "/avatars/osprey.png");
  h.api.renderPlayerSeats(players, 0, 0);
  const seatKids = h.ctx.els["pv-seats-left"].children;
  const firstWrap = seatKids[0];
  h.api.renderPlayerSeats(players, 0, 0);
  ok(h.ctx.els["pv-seats-left"].children[0] === firstWrap,
     "an identical seat payload does not rebuild the seat row");

  const msgs = [{ sender: "Casey", message: "hey", ts: 1, avatar: "/avatars/osprey.png" }];
  h.api.noteLiveAvatars(players, []);
  h.api.renderChatMessages(msgs);
  const line = h.ctx.els["pv-chat-messages"].children[0];
  h.api.renderChatMessages(msgs);
  ok(h.ctx.els["pv-chat-messages"].children[0] === line,
     "…and an unchanged chat list with unchanged faces does not rebuild either");
}

console.log("8. The notification and standings read the live face instead of the 12s nickname cache");
{
  // These two live inside DOM-heavy renderers; assert on their source so a
  // regression to __fishAvatarForNick-first can't slip back in.
  const dn = APP.slice(APP.indexOf("function showDrawNotif("),
                       APP.indexOf("function showDrawNotif(") + 6000);
  ok(/const liveAv = pvLiveAvatar\(playerName\)/.test(dn),
     "the draw notification resolves through pvLiveAvatar");
  ok(/if \(!isMine && !liveAv && typeof window\.__fishAvatarForNick/.test(dn),
     "…and only falls through to the nickname lookup when there is no live face");

  const eg = APP.slice(APP.indexOf("function renderEndGame("),
                       APP.indexOf("function renderEndGame(") + 24000);
  ok(/const liveAv = pvLiveAvatar\(p\.name\) \|\| String\(playerObj\?\.avatar \|\| ""\)/.test(eg),
     "the final standings resolve through pvLiveAvatar, backed by the state we just rendered");
  ok(/if \(!isMe && !liveAv\) \{/.test(eg),
     "…and only queue the async nickname lookup for a player with no live face");
}

console.log("9. The live table is rebuilt from every payload, before anything renders from it");
{
  const rp = APP.slice(APP.indexOf("function renderPayload("),
                       APP.indexOf("function renderPayload(") + 2000);
  ok(/_latestPlayers = players;\s*\n\s*\/\/[^\n]*\n\s*try \{ noteLiveAvatars\(players, payload\.spectators\); \}/.test(rp),
     "renderPayload refreshes the face table as soon as it has the players");
  const idxNote = APP.indexOf("noteLiveAvatars(players, payload.spectators)");
  const idxSeats = APP.indexOf("renderPlayerSeats(players, state.turn_index, myIdx)");
  ok(idxNote > 0 && idxSeats > idxNote, "…and it happens before the seat row is painted");

  // Rebuilt, not merged: a player who leaves must not leave their face behind.
  const h = harness();
  h.api.noteLiveAvatars(seats("/avatars/clownfish.png", "/avatars/osprey.png"), []);
  h.api.noteLiveAvatars([seats("/avatars/clownfish.png", "/avatars/osprey.png")[0]], []);
  eq(h.api.pvLiveAvatar("Casey"), "", "a departed player is dropped from the table");
}

console.log("\n" + (failures ? `✗ ${failures} failed / ${checks} checks`
                              : `✓ all ${checks} checks passed`));
process.exit(failures ? 1 : 0);
