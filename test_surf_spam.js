#!/usr/bin/env node
/* The chat panel keeps up once the room passes the message cap.
 *
 * Run:  node test_surf_spam.js          (no browser needed)
 *
 * THE BUG THIS EXISTS FOR
 * A player mashed Surf's Up!! and chat died for the whole table, permanently.
 * The server half of that is in test_surf_spam.py (one System line per press).
 * This is the half that made it unrecoverable.
 *
 * The live payload carries only the last 80 messages
 * (multiplayer_server.py, "chat_messages": self.chat_messages[-80:]).
 * renderChatMessages decided whether to repaint with:
 *
 *     if (msgs.length === _chatLastCount && avatarKey unchanged) return;
 *
 * Length ALONE. Below the cap that works, because every new message makes the
 * array longer. At the cap the array stops growing: the window slides instead,
 * the length is pinned at 80 for ever, and the guard matches on every single
 * poll from then on. The panel returns before painting and never shows another
 * message. Nothing the players do brings it back, because nothing they can do
 * makes the array a different LENGTH.
 *
 * Spamming the button was simply the quickest way to 80. A long, chatty game
 * got there on its own, which is the more worrying version of the same bug.
 *
 * THE INVARIANT
 * The guard is a fingerprint of the CONTENT in the window, not its size. So:
 *
 *     the window changed       → the panel repaints   ← nothing is ever lost
 *     the window did not       → the panel is skipped ← the poll stays cheap
 *
 * Both halves matter. Repainting unconditionally would fix the freeze and
 * rebuild the whole log on every poll tick, several times a second, which is
 * what the guard was there to prevent in the first place (and what check 7 of
 * test_ingame_avatar_live.js pins).
 *
 * The real renderChatMessages is lifted out of preview-app.js and run here,
 * the same way test_ingame_avatar_live.js runs it, so this tests the shipped
 * code and not a copy of it that can drift.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const APP = fs.readFileSync(
  path.join(__dirname, "multiplayer/client/js/preview-app.js"), "utf8");

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

/* ── a DOM just big enough for the chat log ───────────────────────────── */
function harness() {
  function el(tag) {
    return {
      tag, className: "", textContent: "", style: {}, children: [],
      _src: "",
      set src(v) { this._src = v; }, get src() { return this._src; },
      set alt(v) {}, set loading(v) {}, set onerror(v) {}, set title(v) {},
      appendChild(c) { this.children.push(c); return c; },
      set scrollTop(v) {}, get scrollTop() { return 0; }, get scrollHeight() { return 0; },
    };
  }
  const log = el("div");
  const ctx = {
    document: {
      getElementById: (id) => (id === "pv-chat-messages" ? log : null),
      createElement: el,
    },
    cl: (node) => { node.children = []; },
    pvLiveAvatarKey: () => ctx._avKey,
    pvLiveAvatar: () => "/avatars/mullet.png",
    pvSeatDefaultAvatar: () => "/avatars/mullet.png",
    _applyAvBg: () => {},
    pvcUpdateBadges: () => { ctx.badgeCalls++; },
    pvcRenderList: () => {},
    window: {},
    _avKey: "faces-v1",
    badgeCalls: 0,
    _chatRoomTotal: 0,
    _chatLastCount: -1,
    _chatLastAvatarKey: null,
    _chatLastFingerprint: "",
    _chatPanelOpen: true,
    _chatView: "room",
    _chatSeenCount: 0,
    log,
  };
  const names = Object.keys(ctx);
  ctx.renderChatMessages = new Function(
    ...names, grabFn("renderChatMessages", 2) + "; return renderChatMessages;"
  )(...names.map((n) => ctx[n]));
  ctx.textsOnScreen = () =>
    log.children.map((d) => {
      const main = d.children[1];
      const body = main && main.children[1];
      return body ? body.textContent : "";
    });
  return ctx;
}

/* The window the server actually sends (multiplayer_server.py:12448). */
const CAP = 80;

console.log("1. Below the cap, nothing has changed about how chat behaves");
{
  const h = harness();
  const all = [];
  for (let i = 1; i <= 5; i++) all.push({ sender: "Otter", message: "hello " + i, ts: i });
  h.renderChatMessages(all.slice(-CAP));
  check("a short conversation paints in full",
        h.textsOnScreen().join("|") === "hello 1|hello 2|hello 3|hello 4|hello 5",
        h.textsOnScreen().join("|"));

  all.push({ sender: "Shark", message: "hello 6", ts: 6 });
  h.renderChatMessages(all.slice(-CAP));
  check("a new message below the cap shows up",
        h.textsOnScreen().includes("hello 6"));
}

console.log("2. An unchanged window is still skipped (the guard still guards)");
{
  const h = harness();
  const msgs = [{ sender: "Casey", message: "hey", ts: 1 }];
  h.renderChatMessages(msgs);
  const line = h.log.children[0];
  h.renderChatMessages(msgs);
  check("an identical chat list does not rebuild the log",
        h.log.children[0] === line);
  h.renderChatMessages(msgs.slice());
  check("...nor does an equal-but-separate copy of it",
        h.log.children[0] === line);
}

console.log("3. Somebody changing their face still repaints (no regression)");
{
  const h = harness();
  const msgs = [{ sender: "Casey", message: "hey", ts: 1 }];
  h.renderChatMessages(msgs);
  const line = h.log.children[0];
  h._avKey = "faces-v2";
  h.renderChatMessages(msgs);
  check("a new avatar key repaints even though nothing was said",
        h.log.children[0] !== line);
}

console.log("4. AT THE CAP: the window slides and the panel keeps up");
{
  const h = harness();
  const all = [];
  for (let i = 1; i <= 5; i++) all.push({ sender: "Otter", message: "hello " + i, ts: i });
  h.renderChatMessages(all.slice(-CAP));

  // Someone leans on Surf's Up!!: one System line per press, straight past 80.
  for (let i = 0; i < 90; i++) {
    all.push({
      sender: "System", ts: 100 + i,
      message: i % 2 ? "Otter is back." : "Otter is on Surf's Up, Away",
    });
  }
  h.renderChatMessages(all.slice(-CAP));
  check("the log is sitting at the cap", h.log.children.length === CAP,
        String(h.log.children.length));

  // ...and now somebody tries to actually talk.
  all.push({ sender: "Shark", message: "CAN ANYONE SEE THIS", ts: 9999 });
  const window2 = all.slice(-CAP);
  check("the server really is sending it",
        window2.some((m) => m.message === "CAN ANYONE SEE THIS"));
  h.renderChatMessages(window2);
  check("the panel shows it",
        h.textsOnScreen().includes("CAN ANYONE SEE THIS"));
  check("...and it is the newest line, at the bottom",
        h.textsOnScreen()[h.log.children.length - 1] === "CAN ANYONE SEE THIS");
}

console.log("5. AT THE CAP: it keeps up message after message, not just once");
{
  const h = harness();
  const all = [];
  for (let i = 0; i < CAP; i++) all.push({ sender: "System", message: "noise " + i, ts: i });
  h.renderChatMessages(all.slice(-CAP));
  let seen = 0;
  for (let i = 0; i < 25; i++) {
    all.push({ sender: "Shark", message: "real " + i, ts: 1000 + i });
    h.renderChatMessages(all.slice(-CAP));
    if (h.textsOnScreen().includes("real " + i)) seen++;
  }
  check("all 25 later messages appeared", seen === 25, seen + "/25");
}

console.log("6. Two messages in the same second are not mistaken for one");
{
  // Clients poll; a burst can land with identical timestamps. A fingerprint
  // built only from the last ts would miss the second one.
  const h = harness();
  const all = [];
  for (let i = 0; i < CAP; i++) all.push({ sender: "System", message: "noise " + i, ts: i });
  all.push({ sender: "Shark", message: "first", ts: 500 });
  h.renderChatMessages(all.slice(-CAP));
  all.push({ sender: "Ray", message: "second", ts: 500 });
  h.renderChatMessages(all.slice(-CAP));
  check("the second same-second message still shows",
        h.textsOnScreen().includes("second"));
}

console.log("7. The repaint guard no longer trusts length alone");
{
  const src = grabFn("renderChatMessages", 2);
  check("the guard is not a bare length comparison",
        !/msgs\.length\s*===\s*_chatLastCount\s*&&/.test(src),
        "still comparing msgs.length === _chatLastCount");
  check("the guard is built from a fingerprint",
        /_chatLastFingerprint|_chatLastKey|_chatFingerprint/.test(src));
  // The suites that lift this function out run it on its own, so it must not
  // reach for a helper defined elsewhere in the file.
  check("the fingerprint is computed inline, so extraction still works",
        !/_chatFingerprintOf\s*\(|chatFingerprint\s*\(/.test(src));
}

console.log("8. The button itself cannot be held down");
{
  const i = APP.indexOf('getElementById("pv-surf-btn")?.addEventListener');
  check("the Surf's Up!! handler is where it was", i > 0);
  const handler = APP.slice(i, i + 2600);
  check("there is a cooldown, not just an in-flight guard",
        /_surfCooldown|_surfLastPress|SURF_COOLDOWN/.test(handler));
  check("the in-flight guard is still there too",
        /_surfBtnInFlight/.test(handler));
  // Away cannot play a card and the table is waiting, so the one press that
  // must never be held up is the one that ends it. The server agrees: see
  // "coming back is never refused" in test_surf_spam.py.
  check("coming back is exempt from the cooldown",
        /!_imAway\s*&&\s*_sinceLast\s*<\s*SURF_COOLDOWN_MS/.test(handler));
  check("...and only stepping away starts the clock",
        /if\s*\(want\)\s*_surfLastPress\s*=/.test(handler));
}

console.log();
if (fail) { console.log(`${fail} of ${pass + fail} checks FAILED`); process.exit(1); }
console.log(`all ${pass} checks passed`);
