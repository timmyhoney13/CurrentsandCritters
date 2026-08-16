#!/usr/bin/env node
/* A spectator wears the icon they have equipped — the client half.
 * (multiplayer/client/js/preview-app.js)
 *
 * Run:  node test_spectator_avatar.js
 *
 * Server side is test_spectator_avatar.py. This half pins the three client
 * jobs: send the equipped look when we join, paint it in the spectator list,
 * and re-push it if the player equips something else while watching (a
 * spectator has no seat token, so the seat-only push used to silently do
 * nothing for them).
 *
 * The functions live inside an IIFE that needs a live DOM, so we lift their
 * exact source text out of the file and run it against stubs. If a function
 * is renamed or reshaped, extraction fails loudly rather than quietly testing
 * nothing.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const APP = fs.readFileSync(
  path.join(__dirname, "multiplayer", "client", "js", "preview-app.js"), "utf8");
const CSS = fs.readFileSync(
  path.join(__dirname, "multiplayer", "client", "css", "preview.css"), "utf8");

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
// The avatar table is data, not a function — lift it by name too.
const AVATAR_TABLE = APP.match(/const PV_SEAT_AVATARS = \[[^\]]+\];/);
if (!AVATAR_TABLE) throw new Error("could not find PV_SEAT_AVATARS");

// ── A DOM small enough to reason about ──────────────────────────────────────
function El(tag) {
  const el = {
    tagName: tag, children: [], dataset: {}, style: {},
    className: "", textContent: "", src: "", alt: "", loading: "", disabled: false,
    onerror: null,
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    appendChild(c) { el.children.push(c); return c; },
    insertBefore(c) { el.children.unshift(c); return c; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return ""; },
    set(v) { if (v === "") el.children = []; },
  });
  return el;
}
function findByClass(el, cls, out) {
  out = out || [];
  (el.children || []).forEach(c => {
    if (c.className === cls) out.push(c);
    findByClass(c, cls, out);
  });
  return out;
}

// ── Harness: the extracted functions plus stubs for everything they touch ───
function harness() {
  const posts = [];
  const bgCalls = [];
  const els = {};
  const document = {
    getElementById(id) { return (els[id] = els[id] || El("div")); },
    createElement(tag) { return El(tag); },
    querySelectorAll() { return []; },
  };
  const src =
    AVATAR_TABLE[0] + "\n" +
    extract("pvSeatHash") + "\n" +
    extract("pvSeatDefaultAvatar") + "\n" +
    extract("_specMyLook") + "\n" +
    extract("_specRenderSpectatorList") + "\n" +
    extract("pushMySeatAvatar") + "\n" +
    extract("pushMySeatBackground") + "\n" +
    "return { _specMyLook, _specRenderSpectatorList, pushMySeatAvatar, pushMySeatBackground," +
    "  state: () => ({ lastAvatar: _lastPushedAvatar, lastBg: _lastPushedBg }) };";

  const ctx = {
    posts, bgCalls, els, document,
    roomId: null, spectating: false, seatToken: "",
    spectatorRoomId: "", spectatorToken: "",
    myAvatar: "/avatars/clownfish.png", myBg: "",
  };
  const fn = new Function(
    "document", "window", "ctx",
    "let roomId = ctx.roomId;" +
    "let _spectatorRoomId = ctx.spectatorRoomId, _spectatorToken = ctx.spectatorToken;" +
    "let _lastPushedAvatar = '', _lastPushedBg = '';" +
    "function isSpectating() { return !!_spectatorToken; }" +
    "function getSeatToken() { return ctx.seatToken; }" +
    "function apiPost(url, body) { ctx.posts.push({ url, body }); return Promise.resolve({ ok: true, data: {} }); }" +
    "function _applyAvBg(wrap, bg) { ctx.bgCalls.push(bg); }" +
    src);

  const win = {
    __fishAvSrc: (p) => p,                       // the .webp sibling swap, off in tests
    __fishMyAvatarUrl: () => ctx.myAvatar,
    __fishEquippedBackground: () => ctx.myBg,
  };
  return { ctx, api: fn(document, win, ctx), win };
}

console.log("\n1. Joining as a spectator sends the equipped look");
{
  // joinAsSpectator itself is a DOM-heavy async function; the value it sends is
  // built by _specMyLook, which is the piece worth running.
  const h = harness();
  h.ctx.myAvatar = "/avatars/manta-ray.png";
  h.ctx.myBg = "/backgrounds/bg-coral-reef.png";
  eq(h.api._specMyLook(), { avatar: "/avatars/manta-ray.png", background: "/backgrounds/bg-coral-reef.png" },
     "our icon and background are read from the same bridges a seated player uses");

  const join = APP.slice(APP.indexOf("async function joinAsSpectator("),
                         APP.indexOf("async function leaveSpectator("));
  ok(/\/spectate`\s*,\s*\{\s*name,\s*avatar:\s*look\.avatar,\s*background:\s*look\.background\s*\}/.test(join),
     "the join POST carries name + avatar + background");
  ok(/_lastPushedAvatar = look\.avatar/.test(join) && /_lastPushedBg = /.test(join),
     "…and seeds the push throttles so the first poll doesn't re-send the same thing");
}

console.log("2. The spectator list paints each watcher's icon");
{
  const h = harness();
  h.api._specRenderSpectatorList([
    { name: "Tim", avatar: "/avatars/clownfish.png", background: "", kick_votes: 0 },
    { name: "Reader", avatar: "/avatars/osprey.png", background: "/backgrounds/bg-coral-reef.png", kick_votes: 0 },
  ]);
  const rows = findByClass(h.ctx.els["spec-list-rows"], "spec-list-row");
  eq(rows.length, 2, "one row per spectator");

  const imgs = findByClass(h.ctx.els["spec-list-rows"], "spec-avatar")
    .map(a => a.children[0] && a.children[0].src);
  eq(imgs, ["/avatars/clownfish.png", "/avatars/osprey.png"],
     "each row shows the icon that spectator has equipped");
  ok(rows[0].children[0].className === "spec-avatar",
     "the icon comes first in the row, before the name");
  ok(findByClass(h.ctx.els["spec-list-rows"], "spec-name").map(n => n.textContent)
       .join(",") === "Tim,Reader",
     "the names are still rendered");
  eq(h.ctx.bgCalls, ["", "/backgrounds/bg-coral-reef.png"],
     "an equipped background is applied behind the icon");
  ok(h.ctx.els["pv-spectator-list"].classList.contains("visible"),
     "the panel is shown when somebody is watching");
}

console.log("3. A spectator who never sent an icon still gets a face");
{
  const h = harness();
  h.api._specRenderSpectatorList([{ name: "Ghost", avatar: "", kick_votes: 0 }]);
  const img = findByClass(h.ctx.els["spec-list-rows"], "spec-avatar")[0].children[0];
  ok(/^\/avatars\/[a-z-]+\.png$/.test(img.src),
     "the name-hash default fills in, never a blank src  (got " + img.src + ")");
  // Same fallback the chat panel uses, so one person looks like one person.
  const h2 = harness();
  h2.api._specRenderSpectatorList([{ name: "Ghost", avatar: "", kick_votes: 0 }]);
  eq(findByClass(h2.ctx.els["spec-list-rows"], "spec-avatar")[0].children[0].src, img.src,
     "the default is deterministic for a given name");
}

console.log("4. An empty list hides the panel rather than showing an empty box");
{
  const h = harness();
  const panel = h.ctx.document.getElementById("pv-spectator-list");
  panel.classList.add("visible");
  h.api._specRenderSpectatorList([]);
  ok(!panel.classList.contains("visible"), "the panel is hidden again");
}

console.log("5. Equipping a new icon mid-watch reaches the server");
{
  // Already spectating: a spectator token, and deliberately no seat token.
  const posts = [];
  const fn = new Function("window", "ctx",
    "let roomId = 'ROOM1';" +
    "let _spectatorRoomId = 'ROOM1', _spectatorToken = 'spec-tok';" +
    "let _lastPushedAvatar = '', _lastPushedBg = '';" +
    "function isSpectating() { return !!_spectatorToken; }" +
    "function getSeatToken() { return ''; }" +
    "function apiPost(url, body) { ctx.posts.push({ url, body }); return Promise.resolve({ ok: true }); }" +
    extract("pushMySeatAvatar") + "\n" + extract("pushMySeatBackground") + "\n" +
    "return { pushMySeatAvatar, pushMySeatBackground };");
  const ctx = { posts };
  const win = { __fishMyAvatarUrl: () => ctx.avatar, __fishEquippedBackground: () => ctx.bg };
  ctx.avatar = "/avatars/clownfish.png"; ctx.bg = "";
  const api = fn(win, ctx);

  api.pushMySeatAvatar();
  eq(posts.length, 1, "a spectator with no seat token still pushes");
  eq(posts[0].url, "/api/rooms/ROOM1/avatar", "…to the room they are watching");
  eq(posts[0].body, { spectator_token: "spec-tok", avatar: "/avatars/clownfish.png" },
     "…under the spectator token, with no seat token invented");

  api.pushMySeatAvatar();
  eq(posts.length, 1, "pushing the same icon again is throttled away");

  ctx.avatar = "/avatars/manta-ray.png";
  api.pushMySeatAvatar();
  eq(posts.length, 2, "equipping something else does push");
  eq(posts[1].body.avatar, "/avatars/manta-ray.png", "…and it is the new icon");

  ctx.bg = "/backgrounds/bg-coral-reef.png";
  api.pushMySeatBackground();
  eq(posts[2].url, "/api/rooms/ROOM1/background", "the background goes to the background endpoint");
  eq(posts[2].body, { spectator_token: "spec-tok", background: "/backgrounds/bg-coral-reef.png" },
     "…also under the spectator token");
}

console.log("6. A seated player's push is unchanged");
{
  const posts = [];
  const fn = new Function("window", "ctx",
    "let roomId = 'ROOM2';" +
    "let _spectatorRoomId = '', _spectatorToken = '';" +
    "let _lastPushedAvatar = '', _lastPushedBg = '';" +
    "function isSpectating() { return !!_spectatorToken; }" +
    "function getSeatToken() { return ctx.seat; }" +
    "function apiPost(url, body) { ctx.posts.push({ url, body }); return Promise.resolve({ ok: true }); }" +
    extract("pushMySeatAvatar") + "\n" + extract("pushMySeatBackground") + "\n" +
    "return { pushMySeatAvatar, pushMySeatBackground, seen: () => _lastPushedAvatar };");
  const ctx = { posts, seat: "seat-tok" };
  const api = fn({ __fishMyAvatarUrl: () => "/avatars/osprey.png",
                   __fishEquippedBackground: () => "" }, ctx);
  api.pushMySeatAvatar();
  eq(posts[0], { url: "/api/rooms/ROOM2/avatar",
                 body: { seat_token: "seat-tok", avatar: "/avatars/osprey.png" } },
     "a seated player still pushes with their seat token");

  ctx.seat = "";                       // seat not claimed yet
  const posts2 = [];
  const ctx2 = { posts: posts2, seat: "" };
  const api2 = fn({ __fishMyAvatarUrl: () => "/avatars/osprey.png",
                    __fishEquippedBackground: () => "" }, ctx2);
  api2.pushMySeatAvatar();
  eq(posts2.length, 0, "with no seat and no spectator token, nothing is sent");
  eq(api2.seen(), "", "…and the throttle is not poisoned, so the real push still happens later");
}

console.log("7. The icon has somewhere to render");
{
  ok(/\.spec-list-row \.spec-avatar \{/.test(CSS), "the .spec-avatar box is styled");
  const rule = CSS.slice(CSS.indexOf(".spec-list-row .spec-avatar {"));
  ok(/position: relative/.test(rule.slice(0, 300)),
     "it is a positioning context, so the .cc-avbg background stays inside the circle");
  ok(/overflow:hidden/.test(rule.slice(0, 300)), "…and clips to it");
  ok(/\.spec-list-row \.spec-avatar img \{[^}]*object-fit:cover/.test(CSS),
     "the image fills the circle without stretching");
}

console.log(`\nspectator-icon client checks: ${checks}`);
if (failures) { console.log(`${failures} FAILED`); process.exit(1); }
console.log("spectator icons (client) OK");
