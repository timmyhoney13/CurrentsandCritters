#!/usr/bin/env node
/* Tests for the per-conversation chat backgrounds added to the Messages page
 * (multiplayer/client/js/preview-app.js).
 *
 * Run:  node test_chat_backgrounds.js
 *
 * Why this file exists: a chat background is stored as ANOTHER doc in the same
 * users/{uid}/messages subcollection as real messages, carrying meta:true so it
 * never renders as a bubble. But `meta:true` is also how a GROUP roster doc is
 * marked — so without care, giving a plain DM a wallpaper would silently
 * reclassify it as a group chat (wrong title, wrong send path, wrong members).
 * The guard is _msgIsGroupMeta(); these tests pin it down, plus the "not a
 * message / not unread" invariants every filter depends on.
 *
 * The functions live inside a 26k-line IIFE that needs Firebase to load, so we
 * lift the exact source text of the ones under test out of the file and run
 * them against stub state. If a function is renamed or its body changes shape,
 * extraction fails loudly rather than silently testing nothing.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "multiplayer", "client", "js", "preview-app.js"), "utf8");

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
// Grabs `function <name>(...) { … }` by brace-matching from the opening brace.
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

const SOURCES = ["_msgIsGroupMeta", "_msgRebuildConversations", "_msgChatBgFor", "_msgChatBgOwned"]
  .map(extract).join("\n");

// Catalog + free list must match the real ones, so read them from the source too.
const CATALOG_MATCH = SRC.match(/const CHAT_BG_FREE = (\[[^\]]*\]);/);
if (!CATALOG_MATCH) throw new Error("could not find CHAT_BG_FREE in preview-app.js");

// ── Sandbox: the minimum state those four functions close over ──────────────
function makeEnv(messages, opts) {
  opts = opts || {};
  const env = {
    _msgAllMessages: messages,
    _msgConversations: [],
    _authUser: { uid: opts.me || "me" },
    _unlockedBackgrounds: opts.unlocked || [],
    _BG_BY_IMG: {
      "/backgrounds/bg-kelp.png":   { id: "bg-kelp",   name: "Kelp Forest" },
      "/backgrounds/bg-arctic.png": { id: "bg-arctic", name: "Arctic Ocean" },
      "/backgrounds/bg-deep.png":   { id: "bg-deep",   name: "Deep Ocean" },
    },
    _msgTs: (m) => (m && m.ts) || 0,
  };
  const body = `
    ${CATALOG_MATCH[0]}
    ${SOURCES}
    return {
      isGroupMeta: _msgIsGroupMeta,
      rebuild: () => { _msgRebuildConversations(); return _msgConversations; },
      chatBgFor: _msgChatBgFor,
      chatBgOwned: _msgChatBgOwned,
      CHAT_BG_FREE,
    };`;
  const keys = Object.keys(env);
  // eslint-disable-next-line no-new-func
  const factory = new Function(...keys, body);
  const api = factory(...keys.map(k => env[k]));
  api._env = env;
  return api;
}

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
    cbg("me__you", "/backgrounds/bg-kelp.png", 9),   // written AFTER the message
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
    cbg("g_1", "/backgrounds/bg-arctic.png", 99),    // newest doc in the conv
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
    cbg("g_2", "/backgrounds/bg-kelp.png", 50),
  ]);
  eq(api.rebuild().length, 0, "a group I was removed from stays hidden despite a wallpaper doc");
}
{
  // A conversation that ONLY has a wallpaper doc is not a phantom chat.
  const api = makeEnv([cbg("me__you", "/backgrounds/bg-kelp.png", 3)]);
  eq(api.rebuild().length, 0, "a wallpaper alone does not create an empty conversation");
}
{
  // Wallpaper docs must never count as unread or as the last message.
  const api = makeEnv([
    dm({ ts: 2, sender: "you", receiver: "me", read: false, text: "unread!" }),
    cbg("me__you", "/backgrounds/bg-deep.png", 40),
  ]);
  const c = api.rebuild()[0];
  eq(c.unread, 1, "unread count ignores the wallpaper doc");
  eq(c.last_text, "unread!", "last message is the real message, not the wallpaper");
}
{
  const api = makeEnv([]);
  eq(api.isGroupMeta({ meta: true, members: [] }), true, "a group roster doc IS group metadata");
  eq(api.isGroupMeta({ meta: true, chatbg: "/backgrounds/bg-kelp.png" }), false, "a wallpaper doc is NOT group metadata");
  eq(api.isGroupMeta({ meta: true, chatbg: "" }), false, "a CLEARED wallpaper doc is NOT group metadata");
  eq(api.isGroupMeta({ text: "hello" }), false, "a plain message is not group metadata");
  eq(api.isGroupMeta(null), false, "null is not group metadata");
}

console.log("\nreading a conversation's wallpaper");
{
  const api = makeEnv([
    cbg("me__you", "/backgrounds/bg-kelp.png", 1),
    cbg("me__you", "/backgrounds/bg-arctic.png", 7),   // newer wins
  ]);
  eq(api.chatBgFor("me__you"), "/backgrounds/bg-arctic.png", "the newest pick wins");
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

console.log("\nwho may set which background");
{
  const free = makeEnv([]).CHAT_BG_FREE;
  ok(free.indexOf("/backgrounds/bg-kelp.png") !== -1, "Kelp Forest is a free chat background");
  ok(free.indexOf("/backgrounds/bg-arctic.png") !== -1, "Arctic Ocean is a free chat background");

  const nobody = makeEnv([], { unlocked: [] });
  eq(nobody.chatBgOwned(""), true, "everyone may clear the background");
  eq(nobody.chatBgOwned("/backgrounds/bg-kelp.png"), true, "Kelp Forest needs no unlock");
  eq(nobody.chatBgOwned("/backgrounds/bg-arctic.png"), true, "Arctic Ocean needs no unlock");
  eq(nobody.chatBgOwned("/backgrounds/bg-deep.png"), false, "Deep Ocean is locked until unlocked");

  const owner = makeEnv([], { unlocked: ["/backgrounds/bg-deep.png"] });
  eq(owner.chatBgOwned("/backgrounds/bg-deep.png"), true, "an unlocked background may be used");
}

console.log("\n" + (failures ? "FAILED " + failures + "/" + checks : "PASSED " + checks + " checks"));
process.exit(failures ? 1 : 0);
