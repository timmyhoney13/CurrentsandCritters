#!/usr/bin/env node
/* "＋ Invite a friend" sends the room down the game's own chat.
 *
 * The open seat's invite button used to reach over and press Copy Invite Link.
 * That only helps a player who already has somewhere to paste it, and it said
 * nothing about having done anything. It now opens a sheet: an editable note,
 * the chats you already have as one-tap picks, a search for anybody else, and
 * one Send that puts the invite in each of their message threads.
 *
 * Four things here are easy to break and expensive to notice:
 *
 *  1. ONE COMPOSER. The recipient picker is window.FishCompose with the "pvc"
 *     prefix, the SAME widget (and the same styles) as the chat panel's. A
 *     second hand-rolled picker would drift from it. Its own submit button and
 *     group-name field are hidden, because this sheet has its own Send.
 *
 *  2. ONE PERSON, ONE INVITE. A friend can be both a search result and a
 *     recent chat. Picking them twice must still send once, or the invite
 *     arrives doubled in the same thread.
 *
 *  3. A GROUP IS NOT A DM. A tapped group chat goes through sendGroup(convId),
 *     a person through sendDM(uid). Crossing those wires silently drops the
 *     message: sendGroup refuses a conv it has no member list for.
 *
 *  4. SENDING NEEDS AN ACCOUNT. A guest has no Firestore identity to send
 *     from, so the sheet offers the link and says why, instead of a Send
 *     button that fails.
 *
 * The functions live inside the app's IIFE and need a live DOM, so their exact
 * source text is lifted out and run against stubs. If they are renamed or
 * reshaped, extraction fails loudly rather than quietly testing nothing.
 *
 * Run:  node test_invite_friend_chat.js
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const CLIENT = path.join(__dirname, "multiplayer", "client");
const APP  = fs.readFileSync(path.join(CLIENT, "js/preview-app.js"), "utf8");
const HTML = fs.readFileSync(path.join(CLIENT, "preview.html"), "utf8");
const CSS  = fs.readFileSync(path.join(CLIENT, "css/preview.css"), "utf8");

// Slice a brace-balanced chunk starting at a marker.
function grab(marker) {
  const i = APP.indexOf(marker);
  if (i < 0) throw new Error("missing in preview-app.js: " + marker);
  const open = APP.indexOf("{", i + marker.length - 1);
  let d = 0;
  for (let j = open; j < APP.length; j++) {
    if (APP[j] === "{") d++;
    else if (APP[j] === "}" && --d === 0) return APP.slice(i, j + 1);
  }
  throw new Error("unbalanced: " + marker);
}

const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((f) => fs.existsSync(f));

let pass = 0, fail = 0;
function check(cond, name, extra) {
  if (cond) { pass++; }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}
function eq(actual, expected, name) {
  check(JSON.stringify(actual) === JSON.stringify(expected), name,
        "got " + JSON.stringify(actual) + ", want " + JSON.stringify(expected));
}

// ── The invite block, lifted whole ──────────────────────────────────────
const START = APP.indexOf("  function wrInviteCode() {");
const END   = APP.indexOf("  // Legacy #pv-create-btn handler removed");
if (START < 0 || END < 0 || END < START) {
  throw new Error("could not slice the invite block out of preview-app.js");
}
const BLOCK = APP.slice(START, END);

// ════════════════════════════════════════════════════════════════════════
//  1. THE BUTTON OPENS THE SHEET
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe open seat's invite button opens the sheet");
{
  const seat = APP.slice(APP.indexOf('_wrEl("button", "wr-seat-invite"'),
                         APP.indexOf('_wrEl("button", "wr-seat-invite"') + 400);
  check(/wrInviteOpen\(\)/.test(seat), "pressing it opens the invite sheet");
  check(!/wr-copy-btn/.test(seat),
        "it no longer reaches over and presses Copy Invite Link",
        "a copied link is not an invite anybody received");
  check(/inv\.title\s*=/.test(seat), "and it says what it does on hover");
}

// ════════════════════════════════════════════════════════════════════════
//  2. THE MARKUP AND ITS STYLES
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe sheet is in the markup, inside the waiting room");
{
  ["wr-invite", "wri-close", "wri-compose", "wri-recents", "wri-text",
   "wri-link", "wri-copy", "wri-send", "wri-status", "wri-guest",
   "wri-who"].forEach(id => {
    check(HTML.includes(`id="${id}"`), `#${id} exists`);
    check(BLOCK.includes(id), `…and the controller reaches for #${id}`);
  });
  // It is a CHILD of the waiting room, so leaving the room takes it with it.
  const room = HTML.slice(HTML.indexOf('<div id="pv-waiting-room">'),
                          HTML.indexOf('<!-- ══ GAME ══'));
  check(room.includes('id="wr-invite"'), "it lives inside #pv-waiting-room",
        "a sibling would be left painted over the game after Leave");
  check(/id="wr-invite"[^>]*style="display:none;"/.test(HTML), "it starts closed");
  check(/role="dialog"/.test(room) && /aria-modal="true"/.test(room), "and it is a dialog");

  ["#wr-invite", ".wri-sheet", ".wri-head", ".wri-body", ".wri-foot",
   ".wri-recent", ".wri-status"].forEach(sel => {
    check(CSS.includes(sel), `${sel} is styled`);
  });
  check(/#wri-compose \.pvc-compose-go[^}]*display: none/.test(CSS),
        "the borrowed composer's own submit button is hidden",
        "two Send buttons in one sheet is one too many");
  check(/#wri-compose[^}]*\.pvc-compose-name[^}]*display: none/.test(CSS),
        "…and so is its group-name field");
}

// ════════════════════════════════════════════════════════════════════════
//  3. ONE COMPOSER, NOT A SECOND COPY
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe recipient picker is the chat panel's own");
{
  check(/window\.FishCompose\(host, "pvc"/.test(BLOCK),
        "it is built by FishCompose with the chat panel's prefix",
        "a private prefix would need a private copy of every compose style");
  check(/window\.__fishMsg/.test(BLOCK), "and sending goes through the __fishMsg bridge");
  check(!/firebase\./.test(BLOCK), "the sheet never touches Firestore itself");
}

// ════════════════════════════════════════════════════════════════════════
//  4. WHAT IT ACTUALLY DOES
// ════════════════════════════════════════════════════════════════════════

// ── A DOM small enough to reason about ──────────────────────────────────
function El(tag) {
  const el = {
    tagName: tag, children: [], style: {}, attrs: {}, listeners: {},
    className: "", textContent: "", title: "", value: "", type: "",
    src: "", alt: "", disabled: false,
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
      toggle(c, on) { if (on === undefined) on = !this._s.has(c); on ? this.add(c) : this.remove(c); },
    },
    setAttribute(k, v) { el.attrs[k] = v; },
    getAttribute(k) { return el.attrs[k]; },
    appendChild(c) { el.children.push(c); return c; },
    addEventListener(ev, cb) { (el.listeners[ev] = el.listeners[ev] || []).push(cb); },
    fire(ev, arg) { (el.listeners[ev] || []).forEach(cb => cb(arg || { target: el })); },
    click() { el.fire("click"); },
    get lastElementChild() { return el.children[el.children.length - 1] || null; },
    get innerHTML() { return ""; },
    set innerHTML(v) { if (!v) el.children = []; },
  };
  return el;
}

function sandbox(opts) {
  opts = opts || {};
  const ids = {};
  const byId = (id) => (ids[id] = ids[id] || El("div"));
  ["wr-code-display", "wr-copy-btn", "wr-invite", "wri-close", "wri-compose",
   "wri-recents", "wri-text", "wri-link", "wri-copy", "wri-send", "wri-status",
   "wri-guest", "wri-who"].forEach(byId);
  ids["wr-code-display"].textContent = "REEF";
  // Send is an icon span plus a label span, exactly as the markup builds it:
  // the controller relabels lastElementChild, so a bare stub would test nothing.
  ids["wri-send"].appendChild(El("span")).textContent = "\u{1F4AC}";
  ids["wri-send"].appendChild(El("span")).textContent = "Send in chat";

  const doc = {
    getElementById: (id) => (Object.prototype.hasOwnProperty.call(ids, id) ? ids[id] : null),
    createElement: (t) => El(t),
    addEventListener() {},
  };
  const toasts = [];
  const composeRecipients = opts.searched || [];
  const win = {
    __fishMsg: opts.msg || null,
    FishCompose: (host, prefix, onStart, onChange) => ({
      _prefix: prefix,
      getRecipients: () => composeRecipients.slice(),
      reset() {}, focus() {}, hasRecipients: () => composeRecipients.length > 0,
      _onStart: onStart, _onChange: onChange,
    }),
  };
  const _wrEl = (tag, cls, text) => {
    const e = El(tag);
    if (cls) { e.className = cls; String(cls).split(/\s+/).filter(Boolean).forEach(c => e.classList.add(c)); }
    if (text != null) e.textContent = text;
    return e;
  };
  const api = new Function(
    "document", "window", "location", "roomId", "_wrEl", "showToast", "navigator", "setTimeout",
    BLOCK + "\n; return { wrInviteCode, wrInviteLink, wrInviteDefaultText, wrInviteTargets," +
    " wrInviteSync, wrInviteRenderRecents, wrInviteOpen, wrInviteClose, wrInviteSend, wrInviteIsOpen };"
  )(doc, win, { origin: "https://x.test" }, opts.roomId || "REEF", _wrEl,
    (m, k) => toasts.push([m, k]), { clipboard: { writeText: () => Promise.resolve() } },
    () => 0);   // no timers: nothing in a test should close itself later
  return { api, ids, toasts, win };
}

// A messaging bridge that records instead of writing.
function fakeMsg(opts) {
  opts = opts || {};
  const sent = { dm: [], group: [] };
  return {
    sent,
    ready: () => opts.ready !== false,
    isGuest: () => opts.guest === true,
    ensureListener() {},
    conversations: () => (opts.convs || []).slice(),
    avatarForNick: () => Promise.resolve(null),
    sendDM: async (uid, name, text) => { sent.dm.push([uid, name, text]); return opts.fails !== true; },
    sendGroup: async (convId, text) => { sent.group.push([convId, text]); return opts.fails !== true; },
  };
}

(async function main() {

console.log("\nthe note it writes");
{
  const { api, ids } = sandbox({ msg: fakeMsg() });
  eq(api.wrInviteLink(), "https://x.test/play/REEF", "the link is the room's own play link");
  check(api.wrInviteDefaultText().includes("REEF"), "the note carries the room code");
  check(api.wrInviteDefaultText().includes("https://x.test/play/REEF"), "…and the link");
  api.wrInviteOpen();
  check(ids["wri-text"].value === api.wrInviteDefaultText(), "the sheet opens with it filled in");
  check(ids["wri-link"].textContent === api.wrInviteLink(), "and shows the bare link under it");
  check(api.wrInviteIsOpen(), "the sheet is open");
  api.wrInviteClose();
  check(!api.wrInviteIsOpen(), "and closes again");
}

console.log("\none person, one invite");
{
  const peer = { uid: "u1", name: "Nel" };
  const msg = fakeMsg({ convs: [{ id: "c1", peerUid: "u1", peerName: "Nel" }] });
  const { api, ids } = sandbox({ msg, searched: [peer] });
  api.wrInviteOpen();
  // Tap the same person's existing chat as well as searching for them.
  const row = recentRow(ids);
  check(!!row, "the existing chat is offered as a one-tap pick");
  row.click();
  eq(api.wrInviteTargets().length, 1, "picked twice, they are still one recipient");
  await api.wrInviteSend();
  eq(msg.sent.dm.length, 1, "so exactly one direct message is sent");
  eq(msg.sent.dm[0][0], "u1", "…to them");
  check(msg.sent.dm[0][2].includes("REEF"), "…carrying the room");
}

console.log("\na group chat is not a direct message");
{
  const msg = fakeMsg({ convs: [{ id: "g_ab", group: true, name: "The Crew" }] });
  const { api, ids } = sandbox({ msg });
  api.wrInviteOpen();
  recentRow(ids).click();
  const t = api.wrInviteTargets();
  eq(t.length, 1, "the group is one recipient");
  check(t[0].group === true && t[0].convId === "g_ab", "…addressed by its conversation id");
  await api.wrInviteSend();
  eq(msg.sent.group.length, 1, "it goes through sendGroup");
  eq(msg.sent.dm.length, 0, "and never through sendDM");
  eq(msg.sent.group[0][0], "g_ab", "…on the group's conv id");
}

console.log("\nseveral friends at once, one message each");
{
  const msg = fakeMsg({ convs: [{ id: "c1", peerUid: "u1", peerName: "Nel" }] });
  const { api, ids } = sandbox({ msg, searched: [{ uid: "u2", name: "Wren" }] });
  api.wrInviteOpen();
  recentRow(ids).click();
  eq(api.wrInviteTargets().length, 2, "two different people are two recipients");
  check(/2 chats/.test(ids["wri-send"].lastElementChild.textContent),
        "and the button counts them");
  await api.wrInviteSend();
  eq(msg.sent.dm.map(d => d[0]).sort(), ["u1", "u2"], "both get their own message");
}

console.log("\nthe Send button only lights when it can send");
{
  const msg = fakeMsg({ convs: [{ id: "c1", peerUid: "u1", peerName: "Nel" }] });
  const { api, ids } = sandbox({ msg });
  api.wrInviteOpen();
  check(ids["wri-send"].disabled === true, "nobody picked yet, so Send is off");
  recentRow(ids).click();
  check(ids["wri-send"].disabled === false, "picking a friend turns it on");
  check(/Send/.test(ids["wri-send"].lastElementChild.textContent), "and it says Send");
  ids["wri-text"].value = "   ";
  api.wrInviteSync();
  check(ids["wri-send"].disabled === true, "an empty note turns it off again",
        "a blank message is a message nobody can act on");
  await api.wrInviteSend();
  eq(msg.sent.dm.length, 0, "and pressing it anyway sends nothing");
}

console.log("\na guest is told why, not handed a button that fails");
{
  const msg = fakeMsg({ guest: true });
  const { api, ids } = sandbox({ msg });
  api.wrInviteOpen();
  check(ids["wri-guest"].style.display !== "none", "the sign-in note is shown");
  check(ids["wri-who"].style.display === "none", "the picker is not");
  check(ids["wri-send"].disabled === true, "and Send stays off");
  check(ids["wri-text"].value.length > 0, "the note and the link are still there to copy");
  await api.wrInviteSend();
  eq(msg.sent.dm.length, 0, "nothing is written on their behalf");
}

console.log("\nwhen it does not send, it says so");
{
  const msg = fakeMsg({ fails: true, convs: [{ id: "c1", peerUid: "u1", peerName: "Nel" }] });
  const { api, ids } = sandbox({ msg });
  api.wrInviteOpen();
  recentRow(ids).click();
  await api.wrInviteSend();
  check(ids["wri-status"].classList.contains("err"), "the failure is reported on the sheet");
  check(/copy/i.test(ids["wri-status"].textContent), "…with the way round it");
  check(api.wrInviteIsOpen(), "and the sheet stays open, so the note is not lost");
}

console.log("\nand when it does, it names who got it");
{
  const msg = fakeMsg({ convs: [{ id: "c1", peerUid: "u1", peerName: "Nel" }] });
  const { api, ids, toasts } = sandbox({ msg });
  api.wrInviteOpen();
  recentRow(ids).click();
  await api.wrInviteSend();
  check(/Nel/.test(ids["wri-status"].textContent), "the confirmation names the friend",
        "'sent' with no name is the kind of line a player re-sends");
  check(!ids["wri-status"].classList.contains("err"), "…and it is not painted as an error");
  eq(toasts.length, 1, "and one toast is raised");
}

renderHalf();

console.log(fail === 0 ? `\nAll ${pass} checks passed.` : `\n${fail} of ${pass + fail} checks FAILED.`);
process.exit(fail === 0 ? 0 : 1);

})();

// ════════════════════════════════════════════════════════════════════════
//  5. THE REAL RENDER, PHONE THROUGH DESKTOP
// ════════════════════════════════════════════════════════════════════════
// A sheet that measures fine at one width is a sheet nobody has opened on a
// phone. It is drawn from the real markup and the real stylesheet inside an
// iframe that really is the width it says, at every width a player has.
function page() {
  const a = HTML.indexOf('<div id="pv-waiting-room">');
  const b = HTML.indexOf("\n</div>", a) + "\n</div>".length;
  const lobby = HTML.slice(a, b);
  const src = [grab("function _wrEl(tag, cls, text)"),
               grab("window.FishCompose = function"), BLOCK].join("\n\n");

  const stubs = `
let roomId = "REEF";
function showToast() {}
window.__fishMsg = {
  ready: () => true, isGuest: () => false, ensureListener() {},
  conversations: () => ([
    { id: "c1", peerUid: "u1", peerName: "Nel" },
    { id: "c2", peerUid: "u2", peerName: "AVeryLongPlayerNameIndeed" },
    { id: "g_ab", group: true, name: "The Crew" }]),
  avatarForNick: () => Promise.resolve(null),
  searchPlayers: () => Promise.resolve([]),
  sendDM: async () => true, sendGroup: async () => true,
};
document.getElementById("wr-code-display").textContent = "REEF";
`;
  const drive = `
window.__openInvite = () => wrInviteOpen();
window.__pickAll = () => [...document.querySelectorAll(".wri-recent")].forEach(r => r.click());
`;

  const inner = `<!doctype html><html><head><meta charset="utf-8"><style>
${CSS}
html,body{margin:0;} #pv-waiting-room{display:flex !important; position:static; min-height:100vh;}
</style></head><body>${lobby}<script>${stubs}\n${src}\n${drive}</scr` + `ipt></body></html>`;

  return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#111;}iframe{border:0;display:block;}</style></head><body>
<iframe id="f" width="1280" height="900"></iframe><div id="out"></div>
<script>
const SRC = ${JSON.stringify(inner).replace(/<\//g, "<\\/")};
const WIDTHS = [360, 390, 430, 600, 820, 1024, 1280];
const L = [];
const f = document.getElementById("f");
function measure(w) {
  const d = f.contentDocument, win = f.contentWindow;
  const ok = (c, m) => L.push((c ? "PASS " : "FAIL ") + w + "px: " + m);
  const r = el => el.getBoundingClientRect();
  const vw = win.innerWidth, vh = win.innerHeight;

  const wrap = d.getElementById("wr-invite");
  ok(win.getComputedStyle(wrap).display === "none", "the sheet starts closed");
  win.__openInvite();
  ok(win.getComputedStyle(wrap).display !== "none", "the invite button opens it");

  const sheet = d.querySelector(".wri-sheet"), sb = r(sheet);
  ok(sb.width >= 260, "the sheet has real width (" + Math.round(sb.width) + ")");
  ok(sb.left >= -1 && sb.right <= vw + 1, "it fits the window sideways");
  ok(sb.height <= vh + 1, "…and does not run off the bottom (" + Math.round(sb.height) + " in " + vh + ")");
  ok(d.documentElement.scrollWidth <= vw + 1, "nothing scrolls sideways");

  win.__pickAll();
  const rows = [...d.querySelectorAll(".wri-recent")];
  ok(rows.length === 3, "all three existing chats are offered (" + rows.length + ")");
  rows.forEach((el, i) => {
    const b = r(el);
    ok(b.height >= 30, "chat " + i + " is tappable (" + Math.round(b.height) + "px tall)");
    ok(b.left >= sb.left - 1 && b.right <= sb.right + 1, "chat " + i + " stays inside the sheet");
    ok(el.classList.contains("on"), "chat " + i + " shows it is picked");
  });

  ["wri-text", "wri-send", "wri-copy", "wri-close"].forEach(id => {
    const b = r(d.getElementById(id));
    ok(b.width >= 24 && b.height >= 24, "#" + id + " is tappable (" + Math.round(b.width) + "x" + Math.round(b.height) + ")");
    ok(b.left >= sb.left - 1 && b.right <= sb.right + 1, "#" + id + " stays inside the sheet");
  });
  ok(!d.getElementById("wri-send").disabled, "with friends picked, Send is live");

  // The borrowed composer is here and usable, and its own submit is hidden so
  // there is one Send on the sheet, not two.
  const inp = d.querySelector("#wri-compose .pvc-compose-input");
  ok(!!inp, "the shared recipient picker rendered");
  if (inp) {
    const b = r(inp);
    ok(b.width >= 80 && b.height >= 16, "…and can be typed in (" + Math.round(b.width) + "px)");
    ok(b.right <= sb.right + 1, "…without escaping the sheet");
  }
  const go = d.querySelector("#wri-compose .pvc-compose-go");
  ok(!go || win.getComputedStyle(go).display === "none", "the composer's own submit button is hidden");

  // Nothing at all escapes the sheet sideways.
  [...d.querySelectorAll(".wri-sheet *")].forEach(el => {
    const b = r(el);
    if (b.width > 0) ok(b.right <= sb.right + 1 && b.left >= sb.left - 1,
                        "inside the sheet: " + (el.id || el.className || el.tagName));
  });

  d.getElementById("wri-close").click();
  ok(win.getComputedStyle(wrap).display === "none", "and the ✕ closes it again");
}
f.onload = () => {
  WIDTHS.forEach(w => {
    f.width = String(w);
    f.contentWindow.document.body.offsetHeight;
    try { measure(w); } catch (e) { L.push("FAIL " + w + "px: threw " + e.message); }
  });
  document.getElementById("out").textContent = L.join("\\n");
};
f.srcdoc = SRC;
</scr` + `ipt></body></html>`;
}

function renderHalf() {
  if (!CHROME) {
    console.log("\nSKIP: no Chrome/Chromium found: skipping the layout half.");
    return;
  }
  console.log("\nthe real render, phone through desktop");
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-invite-"));
  const file = path.join(tmp, "invite.html");
  fs.writeFileSync(file, page());
  let dom = "";
  try {
    dom = execFileSync(CHROME, [
      "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      "--window-size=1800,1100", "--virtual-time-budget=20000",
      "--dump-dom", "file://" + file,
    ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 120000,
         maxBuffer: 64 * 1024 * 1024 });
  } catch (e) {
    check(false, "Chrome ran", e.message);
  }
  const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
  if (!m || !m[1].trim()) check(false, "measurements came back from the iframe");
  else m[1].split("\n").filter(Boolean).forEach(line => {
    check(line.startsWith("PASS "), line.replace(/^(PASS|FAIL) /, ""));
  });
  fs.rmSync(tmp, { recursive: true, force: true });
}

// The one recent-chat row the sheet drew.
function recentRow(ids) {
  return ids["wri-recents"].children.find(c => c.classList.contains("wri-recent"));
}
