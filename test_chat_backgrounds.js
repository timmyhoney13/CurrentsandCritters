#!/usr/bin/env node
/* Tests for the per-conversation chat backgrounds on the Messages page
 * (multiplayer/client/js/preview-app.js).
 *
 * Run:  node test_chat_backgrounds.js
 *
 * Why this file exists, part 1: a chat background is stored as ANOTHER doc in
 * the same users/{uid}/messages subcollection as real messages, carrying
 * meta:true so it never renders as a bubble. But `meta:true` is also how a
 * GROUP roster doc is marked, so without care, giving a plain DM a wallpaper
 * would silently reclassify it as a group chat (wrong title, wrong send path,
 * wrong members). The guard is _msgIsGroupMeta(); these tests pin it down, plus
 * the "not a message / not unread" invariants every filter depends on.
 *
 * Part 2 (added when the picker was rebuilt): a chat wallpaper is a WIDE
 * painting, not the circular medallion the profile frame uses, and every
 * wallpaper is free so a conversation can be re-skinned as often as you like.
 * Both of those are easy to regress by editing one list, so the catalog is
 * checked against the files actually on disk.
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
const SRC = fs.readFileSync(path.join(CLIENT, "js", "preview-app.js"), "utf8");

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

// ── Lift the code under test out of the monolith ────────────────────────────
// Grabs `function <name>(...) { … }` by brace-matching from the opening brace.
function balanced(from, open, close) {
  let depth = 0, i = from;
  for (; i < SRC.length; i++) {
    if (SRC[i] === open) depth++;
    else if (SRC[i] === close) { depth--; if (depth === 0) return i; }
  }
  throw new Error("unbalanced " + open + close + " from index " + from);
}
function extract(name) {
  const decl = "function " + name + "(";
  const start = SRC.indexOf(decl);
  if (start < 0) throw new Error("could not find function " + name + " in preview-app.js");
  return SRC.slice(start, balanced(SRC.indexOf("{", SRC.indexOf(")", start)), "{", "}") + 1);
}
// Grabs `const <name> = [ … ];` (the real catalog, not a copy of it).
function extractArray(name) {
  const decl = "const " + name + " = [";
  const start = SRC.indexOf(decl);
  if (start < 0) throw new Error("could not find const " + name + " in preview-app.js");
  return SRC.slice(start, balanced(SRC.indexOf("[", start), "[", "]") + 1) + ";";
}

const FN_SOURCES = ["_msgIsGroupMeta", "_msgGroupMeta", "_msgRebuildConversations",
                    "_msgChatBgResolve", "_msgChatBgFor", "_msgChatBgMembers",
                    "_msgChatBgCacheLocal"].map(extract).join("\n");
const CATALOG = extractArray("CHAT_BACKGROUNDS");
// The alias map is built by a forEach right after the catalog; take it verbatim
// so the legacy-path aliases under test are the real ones.
const ALIAS_MATCH = SRC.match(/const _CHAT_BG_BY_IMG = \{\};\s*CHAT_BACKGROUNDS\.forEach\(b => \{[\s\S]*?\}\);/);
if (!ALIAS_MATCH) throw new Error("could not find the _CHAT_BG_BY_IMG builder in preview-app.js");

// ── Sandbox: the minimum state those functions close over ───────────────────
function makeEnv(messages, opts) {
  opts = opts || {};
  const env = {
    _msgAllMessages: messages,
    _msgConversations: [],
    _authUser: { uid: opts.me || "me" },
    _msgTs: (m) => (m && m.ts) || 0,
  };
  const body = `
    ${CATALOG}
    ${ALIAS_MATCH[0]}
    ${FN_SOURCES}
    return {
      isGroupMeta: _msgIsGroupMeta,
      rebuild: () => { _msgRebuildConversations(); return _msgConversations; },
      chatBgFor: _msgChatBgFor,
      chatBgResolve: _msgChatBgResolve,
      chatBgMembers: _msgChatBgMembers,
      cacheLocal: _msgChatBgCacheLocal,
      messages: () => _msgAllMessages,
      CHAT_BACKGROUNDS,
    };`;
  const keys = Object.keys(env);
  // eslint-disable-next-line no-new-func
  const factory = new Function(...keys, body);
  const api = factory(...keys.map(k => env[k]));
  api._env = env;
  return api;
}

const CATALOG_LIST = makeEnv([]).CHAT_BACKGROUNDS;
const KELP   = CATALOG_LIST.find(b => b.id === "chat-kelp").img;
const ARCTIC = CATALOG_LIST.find(b => b.id === "chat-arctic").img;
const DEEP   = CATALOG_LIST.find(b => b.id === "chat-deep").img;

// Handy doc builders matching the real schemas.
const dm   = (o) => Object.assign({ id: "m" + Math.random(), conv_id: "me__you",
                sender: "me", sender_name: "Me", receiver: "you", receiver_name: "You",
                text: "hi", ts: 1, read: true }, o);
const cbg  = (conv, img, ts) => ({ id: "cbg_" + conv, conv_id: conv, meta: true, chatbg: img,
                set_by: "you", set_by_name: "You", ts: ts || 5, read: true });
const gmeta = (conv, members, ts) => ({ id: "gm_" + conv, conv_id: conv, group: true, meta: true,
                name: "Reef Crew", members, owner: "me", ts: ts || 1, read: true });

console.log("\nchat background docs vs. group detection");
{
  // A DM that has been given a wallpaper must stay a DM.
  const api = makeEnv([
    dm({ ts: 2, text: "hey" }),
    cbg("me__you", KELP, 9),                         // written AFTER the message
  ]);
  const convs = api.rebuild();
  eq(convs.length, 1, "DM with a wallpaper is still one conversation");
  eq(!!convs[0].group, false, "DM with a wallpaper is NOT classified as a group");
  eq(convs[0].peerUid, "you", "peer is still resolved from the last real message");
  eq(convs[0].last_text, "hey", "wallpaper doc never becomes the conversation preview");
}
{
  // A real group keeps its roster even when a wallpaper doc is newer.
  const members = [{ uid: "me", name: "Me" }, { uid: "you", name: "You" }, { uid: "a", name: "Ann" }];
  const api = makeEnv([
    gmeta("g_1", members, 1),
    { id: "x", conv_id: "g_1", group: true, sender: "a", sender_name: "Ann", text: "yo", ts: 2, read: true },
    cbg("g_1", ARCTIC, 99),                          // newest doc in the conv
  ]);
  const convs = api.rebuild();
  eq(convs.length, 1, "group with a wallpaper is one conversation");
  eq(convs[0].group, true, "group is still a group");
  eq(convs[0].name, "Reef Crew", "newer wallpaper doc does not overwrite the group name");
  eq(convs[0].members.length, 3, "newer wallpaper doc does not wipe the member roster");
}
{
  // The roster check that hides groups you've left must not read a wallpaper doc.
  const api = makeEnv([
    gmeta("g_2", [{ uid: "you", name: "You" }], 1),   // I am NOT a member
    { id: "y", conv_id: "g_2", group: true, sender: "you", sender_name: "You", text: "bye", ts: 2, read: true },
    cbg("g_2", KELP, 50),
  ]);
  eq(api.rebuild().length, 0, "a group I was removed from stays hidden despite a wallpaper doc");
}
{
  // A conversation that ONLY has a wallpaper doc is not a phantom chat.
  const api = makeEnv([cbg("me__you", KELP, 3)]);
  eq(api.rebuild().length, 0, "a wallpaper alone does not create an empty conversation");
}
{
  // Wallpaper docs must never count as unread or as the last message.
  const api = makeEnv([
    dm({ ts: 2, sender: "you", receiver: "me", read: false, text: "unread!" }),
    cbg("me__you", DEEP, 40),
  ]);
  const c = api.rebuild()[0];
  eq(c.unread, 1, "unread count ignores the wallpaper doc");
  eq(c.last_text, "unread!", "last message is the real message, not the wallpaper");
}
{
  const api = makeEnv([]);
  eq(api.isGroupMeta({ meta: true, members: [] }), true, "a group roster doc IS group metadata");
  eq(api.isGroupMeta({ meta: true, chatbg: KELP }), false, "a wallpaper doc is NOT group metadata");
  eq(api.isGroupMeta({ meta: true, chatbg: "" }), false, "a CLEARED wallpaper doc is NOT group metadata");
  eq(api.isGroupMeta({ text: "hello" }), false, "a plain message is not group metadata");
  eq(api.isGroupMeta(null), false, "null is not group metadata");
}

console.log("\nreading a conversation's wallpaper");
{
  const api = makeEnv([
    cbg("me__you", KELP, 1),
    cbg("me__you", ARCTIC, 7),                       // newer wins
  ]);
  eq(api.chatBgFor("me__you"), ARCTIC, "the newest pick wins");
  eq(api.chatBgFor("me__other"), "", "another conversation is unaffected");
  eq(api.chatBgFor(null), "", "no conversation open → no wallpaper");
}
{
  const api = makeEnv([cbg("me__you", "", 9)]);
  eq(api.chatBgFor("me__you"), "", "an empty pick clears the wallpaper");
}
{
  // Defensive: a path that is not in our catalog is ignored rather than
  // injected into the page as a background URL.
  const api = makeEnv([cbg("me__you", "/not/a/background.png", 9)]);
  eq(api.chatBgFor("me__you"), "", "an unknown background path is ignored");
}
{
  // Wallpapers picked before the wide art existed were stored as the circular
  // medallion path. They must resolve FORWARD to the wide scene, not vanish.
  const api = makeEnv([]);
  CATALOG_LIST.filter(b => b.legacy).forEach(b => {
    eq(api.chatBgResolve(b.legacy), b.img, "legacy " + b.legacy + " resolves to " + b.img);
  });
  eq(api.chatBgResolve(""), "", "no pick resolves to no wallpaper");
  eq(api.chatBgResolve(null), "", "a null pick resolves to no wallpaper");
  eq(api.chatBgResolve("/backgrounds/bg-mangrove.png"), "",
     "a medallion with no wide painting resolves to nothing rather than a circle");
}
{
  const api = makeEnv([cbg("me__you", "/backgrounds/bg-kelp.png", 4)]);
  eq(api.chatBgFor("me__you"), KELP, "a conversation wearing the OLD kelp path paints the wide kelp art");
}

console.log("\nchanging the background, as many times as you like");
{
  // The bug this section exists for: after one pick, every later pick had to
  // stay possible. Nothing in the catalog is gated, so every tile is pickable
  // whatever the account owns.
  const api = makeEnv([]);
  CATALOG_LIST.forEach(b => {
    eq(api.chatBgResolve(b.img), b.img, b.name + " is pickable by anyone");
  });
  ok(!/_msgChatBgOwned|CHAT_BG_FREE/.test(SRC),
     "no ownership gate survives on chat wallpapers");
  ok(!/ccm-bgtile-lock/.test(SRC), "no locked-tile padlock is rendered in the picker");
}
{
  // Re-picking is just another write, so the same scene twice in a row is legal
  // and each pick lands in the local cache under the one deterministic doc id.
  const api = makeEnv([]);
  api.cacheLocal("me__you", "cbg_me__you", KELP);
  eq(api.chatBgFor("me__you"), KELP, "first pick paints immediately, before the snapshot");
  api.cacheLocal("me__you", "cbg_me__you", DEEP);
  eq(api.chatBgFor("me__you"), DEEP, "second pick replaces the first");
  api.cacheLocal("me__you", "cbg_me__you", DEEP);
  eq(api.chatBgFor("me__you"), DEEP, "picking the same scene again is not an error");
  api.cacheLocal("me__you", "cbg_me__you", "");
  eq(api.chatBgFor("me__you"), "", "picking No Background clears it");
  eq(api.messages().filter(m => m.id === "cbg_me__you").length, 1,
     "four picks leave ONE wallpaper doc, not four");
}
{
  // My own copy is always written, so my wallpaper can never be blocked by a
  // conv id the client cannot parse into members.
  const api = makeEnv([]);
  eq(api.chatBgMembers("me__you"), ["me", "you"], "a DM mirrors to both halves, me first");
  eq(api.chatBgMembers("you__me"), ["me", "you"], "conv id order does not decide who is written first");
  eq(api.chatBgMembers("room_ABCD"), ["me", "room_ABCD"], "an unparseable conv id still includes me");
  eq(api.chatBgMembers("me__me"), ["me"], "I am never written twice");
}
{
  const members = [{ uid: "me", name: "Me" }, { uid: "you", name: "You" }, { uid: "a", name: "Ann" }];
  const api = makeEnv([gmeta("g_9", members, 1)]);
  eq(api.chatBgMembers("g_9"), ["me", "you", "a"], "a group mirrors to its whole roster, me first");
}

console.log("\nthe wallpaper art on disk");
{
  // "The backgrounds are not correct" was the profile MEDALLIONS (720x720
  // circles with a ring vignette) being used as full-bleed chat wallpaper.
  // Every catalog entry must be the wide painting, and must ship the .webp
  // sibling the server content-negotiates, or clients get a stale/missing image.
  CATALOG_LIST.forEach(b => {
    const png  = path.join(CLIENT, b.img.replace(/^\//, ""));
    const webp = png.replace(/\.png$/, ".webp");
    ok(fs.existsSync(png),  b.name + " PNG exists: " + b.img);
    ok(fs.existsSync(webp), b.name + " ships a .webp sibling");
    if (!fs.existsSync(png)) return;
    // PNG header: width/height are big-endian uint32 at bytes 16 and 20.
    const head = Buffer.alloc(24);
    const fd = fs.openSync(png, "r"); fs.readSync(fd, head, 0, 24, 0); fs.closeSync(fd);
    const w = head.readUInt32BE(16), h = head.readUInt32BE(20);
    ok(w / h > 1.4, b.name + " is a WIDE scene, not a square medallion (" + w + "x" + h + ")");
    ok(w >= 1200, b.name + " is full size (" + w + "px wide)");
  });
  ok(/^[a-z0-9_\-]+\.png$/.test(path.basename(KELP)),
     "wallpaper filenames match the server's /backgrounds/ route pattern");
}
{
  // The picker renders CHAT_BACKGROUNDS, never the medallion catalog again.
  const sheet = SRC.slice(SRC.indexOf("function _msgRenderBgSheet("));
  const body  = sheet.slice(0, sheet.indexOf("\n    }\n"));
  ok(body.includes("CHAT_BACKGROUNDS"), "the picker is built from CHAT_BACKGROUNDS");
  ok(!body.includes("EXCLUSIVE_BACKGROUNDS"), "the picker no longer renders the profile medallions");
}

console.log("\n" + (failures ? "FAILED " + failures + "/" + checks : "PASSED " + checks + " checks"));
process.exit(failures ? 1 : 0);
