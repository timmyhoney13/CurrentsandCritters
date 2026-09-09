#!/usr/bin/env node
/* The red number over Messages, and the promise that reading clears it.
 *
 * Run:  node test_message_unread.js          (no browser needed)
 *
 * THE BUG THIS EXISTS FOR
 * Two accounts each sat on a "2" over Messages with every chat read. The count
 * and the list were computed from the same cache by two DIFFERENT rules: the
 * badge counted every unread doc in `_msgAllMessages`, while the conversation
 * list was built from a filtered view that legitimately drops rows — a doc with
 * no conv_id belongs to no conversation, and a group whose roster no longer
 * lists me is hidden on purpose. Anything in the gap between those two rules is
 * counted and unreachable: there is no chat to open that can clear it, so the
 * number is permanent and reading everything does nothing.
 *
 * THE INVARIANT
 * The badge is now the sum of the conversations actually shown, and anything
 * unread that no conversation accounts for is handed back as an ORPHAN to be
 * marked read at the source. So, for ANY set of message docs:
 *
 *     totalUnread === sum(visible rows' unread)          ← nothing uncountable
 *     every unread doc is either counted or an orphan    ← nothing lost
 *
 * The second half matters as much as the first. Making the badge agree with the
 * list would be easy by throwing the difference away; that would leave the docs
 * unread in Firestore for ever and the badge would come back on the next device
 * that read the cache with the old rule. They have to be cleaned up, so this
 * asserts they are named.
 *
 * The real _msgSummarize is lifted out of preview-app.js and run here, the same
 * way test_supporter_tiers_ui.js runs the real Store renderer, so this tests the
 * shipped code and not a copy of it that can drift.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const APP = fs.readFileSync(path.join(__dirname, "multiplayer/client/js/preview-app.js"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (detail != null ? "  [" + detail + "]" : "")); }
}

/* ── lift the real code out of the app's IIFE ─────────────────────────── */
function grabFn(name, indent) {
  const pad = " ".repeat(indent);
  const start = APP.indexOf(`\n${pad}function ${name}(`);
  if (start < 0) throw new Error(`function ${name}() not found`);
  let depth = 0;
  for (let j = APP.indexOf("{", start); j < APP.length; j++) {
    const ch = APP[j];
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return APP.slice(start, j + 1); }
  }
  throw new Error("unbalanced braces reading " + name);
}

const SRC = [
  grabFn("_msgIsGroupMeta", 4),
  grabFn("_msgTs", 4),
  grabFn("_msgSummarize", 4),
  "return _msgSummarize;",
].join("\n");
const summarize = new Function(SRC)();

/* ── message builders ─────────────────────────────────────────────────── */
const ME = "me-uid", THEM = "them-uid";
let seq = 0;
const at = (n) => ({ toMillis: () => n });
function dm(over) {
  seq++;
  return Object.assign({
    id: "m" + seq, conv_id: [ME, THEM].sort().join("__"),
    sender: THEM, sender_name: "Reef", receiver: ME, receiver_name: "Me",
    text: "hello " + seq, ts: at(1000 + seq), read: false,
  }, over || {});
}
function groupMsg(convId, over) {
  seq++;
  return Object.assign({
    id: "g" + seq, conv_id: convId, group: true,
    sender: THEM, sender_name: "Reef", text: "group " + seq, ts: at(2000 + seq), read: false,
  }, over || {});
}
function groupMeta(convId, members) {
  seq++;
  return { id: "gm_" + convId, conv_id: convId, group: true, meta: true,
           name: "The Pod", members, owner: THEM, ts: at(1), read: true };
}
const DMCONV = [ME, THEM].sort().join("__");

/* ══════════════════════════════════════════════════════════════════════
   1. THE ORDINARY CASE STILL WORKS
   ══════════════════════════════════════════════════════════════════════ */
console.log("\na plain DM with two unread");
{
  const msgs = [dm(), dm()];
  const s = summarize(msgs, ME, new Set());
  check("the badge says 2", s.totalUnread === 2, s.totalUnread);
  check("one conversation is listed", s.conversations.length === 1, s.conversations.length);
  check("…and it carries both", s.conversations[0].unread === 2, s.conversations[0].unread);
  check("nothing is orphaned", s.orphans.length === 0, JSON.stringify(s.orphans));
}

console.log("\nreading them clears the badge, before Firestore answers");
{
  const msgs = [dm(), dm()];
  // Exactly what _msgMarkConvRead does: mark locally, recount, repaint. The
  // docs still say read:false, because the write has not landed yet.
  const s = summarize(msgs, ME, new Set(msgs.map(m => m.id)));
  check("the badge is 0 the moment the chat is opened", s.totalUnread === 0, s.totalUnread);
  check("the conversation is still listed", s.conversations.length === 1);
  check("…showing no unread", s.conversations[0].unread === 0);
  check("and nothing is called an orphan", s.orphans.length === 0, JSON.stringify(s.orphans));
}

/* ══════════════════════════════════════════════════════════════════════
   2. THE STUCK BADGE, THE CASE THIS WAS WRITTEN FOR
   ══════════════════════════════════════════════════════════════════════ */
console.log("\nunread in a group whose roster no longer lists me");
{
  const CONV = "grp-1";
  // The row is hidden on purpose: I am not in members any more. Under the old
  // rule these two still counted, and no chat on screen could clear them.
  const msgs = [
    groupMeta(CONV, [{ uid: THEM, name: "Reef" }, { uid: "third", name: "Kelp" }]),
    groupMsg(CONV), groupMsg(CONV),
  ];
  const s = summarize(msgs, ME, new Set());
  check("the group is not listed", s.conversations.length === 0, s.conversations.length);
  check("so the badge does NOT show a number nothing can clear",
        s.totalUnread === 0, s.totalUnread);
  check("both unread docs are named for sweeping, not silently dropped",
        s.orphans.length === 2, JSON.stringify(s.orphans));
}

console.log("\nunread that names no conversation at all");
{
  const msgs = [dm(), dm({ conv_id: undefined })];
  const s = summarize(msgs, ME, new Set());
  check("only the real conversation is listed", s.conversations.length === 1);
  check("the badge counts only what is on screen", s.totalUnread === 1, s.totalUnread);
  check("the conv-less doc is named for sweeping", s.orphans.length === 1, JSON.stringify(s.orphans));
}

console.log("\na DM whose only docs are not messages");
{
  // A chat-background doc is meta:true but not a group meta. It leaves a
  // conversation with nothing to show, so the row is dropped.
  const msgs = [{ id: "bg1", conv_id: DMCONV, meta: true, chatbg: "/bg/kelp.png",
                  sender: THEM, ts: at(5), read: false }];
  const s = summarize(msgs, ME, new Set());
  check("no conversation is listed", s.conversations.length === 0);
  check("and the badge stays at 0", s.totalUnread === 0, s.totalUnread);
  check("a background doc is not an unread message", s.orphans.length === 0, JSON.stringify(s.orphans));
}

/* ══════════════════════════════════════════════════════════════════════
   3. THINGS THAT ARE NOT UNREAD MESSAGES
   ══════════════════════════════════════════════════════════════════════ */
console.log("\nwhat must never reach the badge");
{
  const cases = [
    ["my own message", dm({ sender: ME, receiver: THEM })],
    ["a message I have read", dm({ read: true })],
    ["the live trade mirror doc", { id: "trade_x", conv_id: DMCONV, trade: true,
                                    trade_state: {}, ts: at(9), read: false }],
    ["a group meta doc", groupMeta("grp-2", [{ uid: ME, name: "Me" }])],
  ];
  for (const [label, doc] of cases) {
    const s = summarize([doc], ME, new Set());
    check(label + " does not count", s.totalUnread === 0, s.totalUnread);
    check("…and is not swept either", s.orphans.length === 0, JSON.stringify(s.orphans));
  }
}

console.log("\nthe trade log the server pings you with IS a real message");
{
  // system:true, posted into the DM by the server after a trade. It is unread,
  // it belongs to a conversation that exists, and opening that chat clears it.
  const log = dm({ id: "tradelog_abc", system: true, trade_log: true,
                   text: "✅ Reef confirmed the trade." });
  const s = summarize([log], ME, new Set());
  check("it counts", s.totalUnread === 1, s.totalUnread);
  check("in a conversation you can open", s.conversations.length === 1);
  check("so it is not an orphan", s.orphans.length === 0, JSON.stringify(s.orphans));
  const after = summarize([log], ME, new Set(["tradelog_abc"]));
  check("and opening that chat clears it", after.totalUnread === 0, after.totalUnread);
}

/* ══════════════════════════════════════════════════════════════════════
   4. THE INVARIANT, OVER RANDOM MESSAGE SETS
   A hand-written case only proves the shapes I thought of. This throws
   messy caches at it: mixed DMs and groups, groups I am and am not in,
   missing conv_ids, meta docs, trade docs, read and unread, mine and
   theirs. Two things must hold every single time.
   ══════════════════════════════════════════════════════════════════════ */
console.log("\nthe invariant holds over 4000 random message caches");
{
  let rng = 20260909;
  const rand = () => (rng = (rng * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  const pick = (a) => a[Math.floor(rand() * a.length) % a.length];

  let badSum = 0, lost = 0, worstSum = null, worstLost = null;
  for (let run = 0; run < 4000; run++) {
    const msgs = [];
    const groups = ["grp-a", "grp-b"];
    // Some runs put me in a group's roster, some do not, some have no meta.
    groups.forEach(g => {
      const r = rand();
      if (r < 0.35) msgs.push(groupMeta(g, [{ uid: ME, name: "Me" }, { uid: THEM, name: "Reef" }]));
      else if (r < 0.7) msgs.push(groupMeta(g, [{ uid: THEM, name: "Reef" }]));
    });
    const n = 1 + Math.floor(rand() * 8);
    for (let i = 0; i < n; i++) {
      const kind = rand();
      if (kind < 0.4) {
        msgs.push(dm({ read: rand() < 0.4, sender: rand() < 0.25 ? ME : THEM,
                       conv_id: rand() < 0.12 ? undefined : DMCONV }));
      } else if (kind < 0.75) {
        msgs.push(groupMsg(pick(groups), { read: rand() < 0.4, sender: rand() < 0.25 ? ME : THEM }));
      } else if (kind < 0.85) {
        msgs.push({ id: "t" + (++seq), conv_id: DMCONV, trade: true, ts: at(seq), read: false, sender: THEM });
      } else if (kind < 0.95) {
        msgs.push({ id: "bg" + (++seq), conv_id: pick([DMCONV].concat(groups)), meta: true,
                    chatbg: "/bg/x.png", ts: at(seq), read: false, sender: THEM });
      } else {
        msgs.push(dm({ conv_id: "ghost-" + Math.floor(rand() * 3), read: rand() < 0.3 }));
      }
    }
    // Some runs have already had chats opened on this device.
    const local = new Set(msgs.filter(() => rand() < 0.2).map(m => m.id));

    const s = summarize(msgs, ME, local);

    // (a) The badge is exactly what the visible rows add up to.
    const sum = s.conversations.reduce((t, c) => t + (c.unread || 0), 0);
    if (sum !== s.totalUnread) { badSum++; worstSum = worstSum || { sum, total: s.totalUnread }; }

    // (b) Every unread doc is either counted in a visible row or named as an
    //     orphan. Nothing may simply vanish, or the badge is honest today and
    //     the docs stay unread for ever.
    const isRead = (m) => !!m.read || local.has(m.id);
    const unreadIds = msgs
      .filter(m => m && !m.meta && !m.trade && m.sender !== ME && !isRead(m))
      .map(m => m.id);
    const orphanSet = new Set(s.orphans);
    const visible = new Set(s.conversations.map(c => c.id));
    const unaccounted = unreadIds.filter(id => {
      if (orphanSet.has(id)) return false;
      const m = msgs.find(x => x.id === id);
      return !(m && m.conv_id && visible.has(m.conv_id));
    });
    if (unaccounted.length) { lost++; worstLost = worstLost || unaccounted.slice(0, 3); }
  }
  check("the badge always equals the sum of the rows on screen",
        badSum === 0, badSum + " runs differed, e.g. " + JSON.stringify(worstSum));
  check("no unread message is ever both uncounted and unswept",
        lost === 0, lost + " runs lost one, e.g. " + JSON.stringify(worstLost));
}

/* ══════════════════════════════════════════════════════════════════════
   5. THE WIRING AROUND IT
   The pure function is only half the fix: the app has to build the list
   before the badge, sweep what it hands back, and mark read locally so
   the number drops without waiting on Firestore.
   ══════════════════════════════════════════════════════════════════════ */
console.log("\nthe app is wired to use it");
check("the badge total is set from the summary, nowhere else",
      (APP.match(/_msgTotalUnread = /g) || []).length === 3
      && APP.includes("_msgTotalUnread = summary.totalUnread;"),
      (APP.match(/_msgTotalUnread = /g) || []).length + " assignments");
check("the snapshot builds the list first, then paints the badge",
      APP.indexOf("const summary = _msgRebuildConversations();")
      < APP.indexOf("_msgUpdateBadge();\n            // Anything unread"));
check("…and sweeps the orphans it hands back",
      APP.includes("_msgSweepOrphans(summary.orphans);"));
check("opening a chat marks read locally before the write",
      APP.includes("unread.forEach(m => _msgLocallyRead.add(m.id));")
      && APP.indexOf("unread.forEach(m => _msgLocallyRead.add(m.id));")
         < APP.indexOf('await _msgWriteRead(unread.map(m => m.id), "conversation opened");'));
check("the read writes are batched, not one round trip per message",
      APP.includes("const batch = _db.batch();") && APP.includes("await batch.commit();"));
check("a refused write is reported, not swallowed",
      APP.includes('console.warn("[msg] could not mark read'));
check("a doc confirmed read drops its local mark, so the set cannot grow for ever",
      APP.includes("if (m && m.read && _msgLocallyRead.has(m.id)) _msgLocallyRead.delete(m.id);"));
check("the in-game panel reads the same per-conversation number",
      APP.includes("const c = _msgConversations.find(x => x && x.id === convId);"));
check("signing out clears the local marks with everything else",
      APP.includes("_msgLocallyRead.clear();") && APP.includes("_msgSweptOrphans.clear();"));
check("the old raw-cache count is gone",
      !APP.includes("_msgTotalUnread = _msgAllMessages.filter("));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
