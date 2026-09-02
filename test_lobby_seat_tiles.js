#!/usr/bin/env node
/* The waiting room: eight spots, the chat, and your own look.
 *
 * The layout half does NOT hand-copy the markup. It slices the real render
 * functions out of preview-app.js, drives them with a fake room, and measures
 * what they actually produce, so a change to the render shows up here instead
 * of quietly drifting away from a mock-up that still passes.
 *
 * Four things are easy to break and expensive to notice:
 *
 *  1. ADDING IS A BOT. Every spot up to eight is drawn, and a spot not in play
 *     carries a + that seats a bot. There is no "add a player seat" and there
 *     must not be: an unclaimed human seat does not become a bot at kickoff,
 *     it stops the game starting at all (start_game refuses while one is open,
 *     see test_lobby_seat_tiles.py). A person joins with the room code.
 *
 *  2. ONE PLACE TO SET THE TABLE. The old Table Setup steppers are gone; the
 *     spots are the control. Two places to change the same thing can disagree
 *     about what the table is.
 *
 *  3. THE REMOVE BUTTON HAS TO BE FINDABLE. It is the destructive control on
 *     the screen, so hover-only, low contrast or a small hit target are all
 *     ways of making it something players press by accident or cannot find.
 *
 *  4. IT HAS TO FIT. Eight spots plus a chat panel, on anything from a phone
 *     to a desktop. Headless Chrome refuses a window under 500px, so the sizes
 *     are measured inside an iframe, which really is the width it says.
 *
 * Run:  node test_lobby_seat_tiles.js      (needs Google Chrome / Chromium)
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const HTML = fs.readFileSync(path.join(CLIENT, "preview.html"), "utf8");
const CSS = fs.readFileSync(path.join(CLIENT, "css/preview.css"), "utf8");
const APP = fs.readFileSync(path.join(CLIENT, "js/preview-app.js"), "utf8");
const SERVER = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");

let pass = 0, fail = 0;
function check(cond, name, extra) {
  if (cond) { pass++; }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

// The app with its comments removed. Claims about what the app SAYS have to be
// made against what it says: a comment denying "it becomes a bot" reads the
// same to a regex as the copy making that claim.
const APP_SAYS = APP
  .replace(/\/\*[\s\S]*?\*\//g, " ")
  .split("\n").filter(l => !/^\s*\/\//.test(l)).join("\n");

// The whole lobby block, so a claim about it can't be satisfied by some
// unrelated corner of a 30k-line file.
const LOBBY = APP.slice(APP.indexOf("  // ══ Waiting room ═"),
                        APP.indexOf("  // The arguments of the last lobby paint"));

// Slice one top-level function out by balancing its braces.
function grabFn(name) {
  const re = new RegExp("^  (?:async )?function " + name + "\\s*\\(", "m");
  const m = re.exec(APP);
  if (!m) throw new Error("missing function: " + name);
  const i = APP.indexOf("{", m.index + m[0].length - 1);
  let d = 0;
  for (let j = i; j < APP.length; j++) {
    if (APP[j] === "{") d++;
    else if (APP[j] === "}" && --d === 0) return APP.slice(m.index, j + 1);
  }
  throw new Error("unbalanced: " + name);
}

// ════════════════════════════════════════════════════════════════════════
//  1. ADDING IS A BOT, AND THERE ARE ALWAYS EIGHT SPOTS
// ════════════════════════════════════════════════════════════════════════
console.log("\neight spots, and the + seats a bot");
{
  check(/const WR_SLOTS = 8/.test(APP), "the room draws eight spots");
  check(/for \(let i = rows\.length; i < WR_SLOTS; i\+\+\) grid\.appendChild\(_wrAddCard/.test(LOBBY),
        "every spot past the table's size is drawn as an add spot");
  // Pressing the + asks WHICH, because both answers are real and they behave
  // differently at the Start button: a bot fills its seat, an empty player seat
  // holds the game up until somebody takes it.
  check(/wr-add-menu/.test(LOBBY) && /Spot /.test(LOBBY), "pressing it opens a chooser");
  check(/Add a player seat/.test(LOBBY), "…offering a seat for a person");
  check(/Add a bot/.test(LOBBY), "…and a bot");
  check(/setTableSeats\(ctx\.humans \+ 1, ctx\.bots\)/.test(LOBBY),
        "the player seat asks for one more human spot and the same bots");
  check(/setTableSeats\(ctx\.humans, ctx\.bots \+ 1\)/.test(LOBBY),
        "the bot asks for one more bot and the same human spots");
  check(/ctx\.room\.ranked[\s\S]{0,160}?bot\.disabled = true/.test(LOBBY),
        "a competitive room is people only, so the bot option is shown unavailable",
        "the server refuses it either way; this stops the host asking");
  check(/wr-add-cancel/.test(LOBBY), "and the chooser can be backed out of");

  check(/Start is locked until/.test(LOBBY), "an open seat says the start is locked");
  check(/Make it a bot/.test(LOBBY) && /setTableSeats\(ctx\.humans - 1, ctx\.bots \+ 1\)/.test(LOBBY),
        "…and offers the host the one way past it");
  check(!/(becomes?|turns? into) an? bot when (you|the host) (cast|start)|bot when you cast off|becomes? a bot at (the )?start/i.test(APP_SAYS),
        "nothing claims an open seat becomes a bot at kickoff",
        "Quick Play's own copy is fine: it converts spare seats when the host picks");
  // The lobby-wide caption that used to spell this out is gone (its spot is the
  // Watch instead button now); the open seat tile itself carries both ways past.
}

// ════════════════════════════════════════════════════════════════════════
//  2. ONE PLACE TO SET THE TABLE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe spots are the only seat control");
{
  ["wr-table-setup", "wr-humans-value", "wr-bots-value", "wr-humans-minus",
   "wr-bots-plus", "wr-table-note"].forEach(id => {
    check(!HTML.includes(`id="${id}"`), `the old #${id} stepper is gone`);
    check(!APP.includes(id), `…and nothing reaches for #${id}`);
  });
  check(!/function updateTableSetup/.test(APP), "its renderer is gone too");
  check(/async function setTableSeats/.test(APP), "one function asks the server for a table");
  check(/lobby_seats/.test(APP), "…on the lobby_seats endpoint");
}

// ════════════════════════════════════════════════════════════════════════
//  3. THE HOST'S REMOVAL
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe host removes people, bots and empty spots");
{
  check(/async function lobbyKickPlayer/.test(APP), "there is a lobby removal");
  check(/\/lobby_kick`/.test(APP), "it posts to lobby_kick");
  check(/lobby_kick/.test(SERVER) && /def lobby_remove_player/.test(SERVER),
        "the server has that endpoint and its method");
  check(/host_token: getHostToken\(\)[\s\S]{0,120}?target_seat_index: seatIndex/.test(APP),
        "sent with host authorisation and a seat index");
  check(/window\.confirm\(/.test(APP.slice(APP.indexOf("async function lobbyKickPlayer"),
                                           APP.indexOf("async function watchFromLobby"))),
        "removing a person asks first: it is permanent");

  check(/ctx\.canShape && isAI[\s\S]{0,140}?Remove a bot/.test(LOBBY), "a bot spot has a minus");
  check(/ctx\.canShape && isOpen[\s\S]{0,200}?Take this seat off the table/.test(LOBBY),
        "an empty seat can be taken off the table");
  check(/ctx\.canShape && !isMine && s\.claimed_name && !s\.is_host/.test(LOBBY),
        "a seated player gets one only from the host, never on the host or on me");
  check(/canShape: isHost &&/.test(LOBBY), "nobody but the host sees any of them");
  check(/!room\.quick_play && !room\.competitive && !room\.tournament/.test(LOBBY),
        "…and not in rooms whose shape is not the host's to change");
  check(/_wrLock\(/.test(LOBBY), "a seat that cannot be removed shows a lock instead");
}

// ════════════════════════════════════════════════════════════════════════
//  4. WHAT A SPOT SHOWS
// ════════════════════════════════════════════════════════════════════════
console.log("\na spot shows the player everyone knows");
{
  check(/s\.background/.test(LOBBY) && /__fishBgStyle/.test(LOBBY),
        "the equipped background paints behind the critter");
  check(/_wrSeatAvatarUrl/.test(LOBBY) && /pvLiveAvatar/.test(LOBBY),
        "the critter comes off the seat, falling back to the live table");
  check(/⭐ Lv/.test(LOBBY), "the Level is on the spot");
  check(/XP to Level/.test(LOBBY), "so is the XP bar's caption");
  check(/🏆 Best/.test(LOBBY), "and the record line");
  check(/__ccPrestigeBadgeHtml/.test(LOBBY), "the Prestige badge is the game's real one");
  check(/__ccPrestigeLookupByName/.test(LOBBY), "looked up from the Prestige service");
  check(/_wrBgName/.test(LOBBY) && /__fishBackgroundCatalog/.test(APP),
        "a background is named from the catalogue, not filed down from its filename");
  ["avatar", "background", "level", "xp", "xp_goal", "best", "games", "title"].forEach(f => {
    check(new RegExp(`"${f}": `).test(SERVER), `the server sends ${f} with the seat list`);
  });
  check(/wr-seat-edit/.test(LOBBY) && /__fishOpenAvatarGallery/.test(LOBBY),
        "you can change your own critter from your own spot");
  check(/isMine/.test(LOBBY), "…and only from your own");
}

// ════════════════════════════════════════════════════════════════════════
//  5. CHAT, AND YOUR OWN LOOK
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe chat is in the room");
{
  ["wr-chat-log", "wr-chat-text", "wr-chat-send", "wr-emote-row", "wr-chat-here",
   "wr-critter-row", "wr-bg-row"].forEach(id =>
    check(HTML.includes(`id="${id}"`), `preview.html has #${id}`));
  check(/function _wrRenderChat/.test(APP), "the lobby paints its own chat log");
  check(/latestPayload\.chat_messages/.test(LOBBY), "…from the room's own messages");
  check(/async function _wrSendChat/.test(APP), "and can send");
  check(/\/chat`/.test(APP), "…on the room chat endpoint");
  check(/spectate_chat/.test(APP), "a spectator sends on theirs");
  check(/CC_PROFANITY\.clean/.test(APP.slice(APP.indexOf("async function _wrSendChat"),
                                             APP.indexOf("async function _wrSendChat") + 900)),
        "what is typed is filtered before it is sent");
  check(/_wrChatFingerprint/.test(APP), "the log repaints only when it changed",
        "it runs on every poll");
  check(HTML.includes('id="wr-chat-badge"'), "the unread badge is still there");
  check(/function _wrRenderLook/.test(APP), "the look shelf is drawn");
  check(/__fishEquipAvatar/.test(APP) && /__fishEquipBackground/.test(APP),
        "equipping from it goes through the account's own equip");
  check(/window\.__fishEquipBackground = async/.test(APP), "that bridge exists");
  check(/window\.__fishGetUnlockedIcons = /.test(APP), "so does the unlocked-critter one");
}

// ════════════════════════════════════════════════════════════════════════
//  6. WATCH INSTEAD
// ════════════════════════════════════════════════════════════════════════
console.log("\nwatch instead of playing");
{
  check(HTML.includes('id="wr-spectate-btn"'), "preview.html has the button");
  const watch = APP.slice(APP.indexOf("async function watchFromLobby"),
                          APP.indexOf("function _wrSeatCard"));
  check(/\/leave`/.test(watch), "it gives the seat up");
  check(/joinAsSpectator\(rid\)/.test(watch), "then joins as a spectator");
  check(/setSeatToken\(""\)/.test(watch), "and lets go of the seat token");
  // A seat given up must not come back on its own: the rejoin mirror would
  // offer it, and a host keeping their host token would still be handed Start
  // Game for a game they are no longer in.
  check(/setHostToken\(""\)/.test(watch), "and the host token with it");
  check(/clearRejoinToken\(rid\)/.test(watch), "and the 8-minute rejoin mirror");
  check(/allow_spectators !== false/.test(APP), "hidden in a room that takes no spectators");

  // It stands where the "N seats are still empty" caption used to. That caption
  // is gone: the Start button already counts the missing players.
  check(!HTML.includes('id="wr-caption"'), "the empty-seat caption is gone from the markup");
  check(!/wr-caption/.test(APP), "and nothing writes to it");
  check(!/wr-caption/.test(CSS), "and it is not styled any more");
  check(/seats are still empty/.test(APP) === false,
        "the empty-seat sentence is not printed anywhere");
  const spec = APP.slice(APP.indexOf('const specBtn = document.getElementById("wr-spectate-btn")'),
                         APP.indexOf('document.getElementById("wr-copy-btn").addEventListener'));
  check(/specBtn\.disabled = alone/.test(spec),
        "the last person seated gets it greyed, not vanished");
  check(/filled <= 1/.test(spec), "…which is what 'alone' means");
  check(/#wr-spectate-btn:disabled/.test(CSS), "and a disabled style exists");

  // The lobby chat is people talking. Nothing else.
  const chat = APP.slice(APP.indexOf("function _wrRenderChat"),
                         APP.indexOf("async function _wrSendChat"));
  check(/\.filter\(m => !\(m && \(m\.system/.test(chat), "system chat is filtered out of the lobby log");
  check(!/wr-chat-sys/.test(chat), "so no system line is ever built");

  // Leaving is pressed by spectators too now that watching is one click away.
  const leaveBtn = APP.slice(APP.indexOf('document.getElementById("wr-leave-btn").addEventListener'),
                             APP.indexOf('document.getElementById("wr-leave-btn").addEventListener') + 900);
  check(/isSpectating\(\)[\s\S]{0,120}leaveSpectator\(\)/.test(leaveBtn),
        "a spectator's Leave releases the spectator slot, not a seat it never had");
}

// ════════════════════════════════════════════════════════════════════════
//  7. THE ART IS REALLY SERVED
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe tide pool behind the lobby");
{
  check(fs.existsSync(path.join(CLIENT, "lobby-tide-pool.png")), "the art is in the client");
  check(fs.existsSync(path.join(CLIENT, "lobby-tide-pool.webp")), "…with its WebP sibling");
  check(/lobby-tide-pool\.png\?v=/.test(CSS), "the CSS points at it, cache-busted");
  // A new client PNG missing from the server's allowlist 404s in production and
  // nowhere else: the page just renders on the fallback gradient.
  check(/lobby-tide-pool/.test(SERVER), "the server's client-asset route serves it",
        "add it to the PNG allowlist regex or it 404s in production");
}

// ════════════════════════════════════════════════════════════════════════
//  8. EVERY CLASS THE RENDER MAKES HAS A STYLE
// ════════════════════════════════════════════════════════════════════════
console.log("\nstyles exist");
{
  ["wr-seat-grid", "wr-seat", "wr-seat-bot", "wr-seat-open", "wr-seat-you",
   "wr-seat-top", "wr-seat-av", "wr-seat-avbg", "wr-seat-av-empty", "wr-seat-edit",
   "wr-seat-id", "wr-seat-name", "wr-seat-pbadge", "wr-seat-chips", "wr-seat-blurb",
   "wr-seat-sublabel", "wr-seat-openbody", "wr-seat-invite", "wr-seat-stat",
   "wr-chip", "wr-chip-lvl", "wr-chip-host", "wr-chip-ready", "wr-chip-bot",
   "wr-chip-you", "wr-chip-wait", "wr-chip-bg", "wr-seat-foot", "wr-seat-hint",
   "wr-seat-tobot", "wr-seat-remove", "wr-seat-lock", "wr-seat-add",
   "wr-seat-add-plus", "wr-seat-add-t", "wr-seat-add-s", "wr-xp", "wr-xp-bar",
   "wr-xp-fill", "wr-xp-txt", "wr-pip", "wr-chat-line", "wr-chat-av", "wr-chat-body",
   "wr-chat-who", "wr-chat-msg", "wr-chat-empty", "wr-emote",
   "wr-look-tile", "wr-look-more", "wr-look-bg",
   "wr-add-menu", "wr-add-menu-h", "wr-add-opt", "wr-add-cancel"].forEach(cls => {
    check(APP.includes(cls), `the render makes .${cls}`);
    check(new RegExp("\\." + cls + "[\\s,{:.]").test(CSS), `.${cls} has a style`);
  });
  ["wr-capacity", "wr-cap-count", "wr-cap-pips", "wr-cap-legend", "wr-deck",
   "wr-chat-panel", "wr-chat-head", "wr-chat-log", "wr-chat-foot", "wr-chat-input",
   "wr-deck-right", "wr-start-row", "wr-start-note", "wr-look", "wr-look-head",
   "wr-look-row", "wr-head", "wr-head-tools"].forEach(cls => {
    check(HTML.includes(cls), `the markup has .${cls}`);
    check(new RegExp("\\." + cls + "[\\s,{:.]").test(CSS), `.${cls} has a style`);
  });
  // Exactly one rule may own the chat button: an older full-width one used to
  // sit later in the sheet and quietly win.
  check((CSS.match(/#wr-chat-btn \{/g) || []).length === 1,
        "#wr-chat-btn is styled in exactly one place");
}

// ════════════════════════════════════════════════════════════════════════
//  9. IT ACTUALLY FITS  (the real render, in real Chrome, at real widths)
// ════════════════════════════════════════════════════════════════════════
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => fs.existsSync(p));

function page() {
  const fns = ["_wrEl", "_wrChip", "_wrBgName", "_wrNum", "_wrRemoveBtn", "_wrLock",
               "_wrSeatAvatarUrl", "_wrCounts", "buildDifficultyBox", "_wrLoadPrestige",
               "_wrSeatCard", "_wrAddCard", "_wrRenderCapacity", "renderSeatTilesInto",
               "_wrChatAvatar", "_wrRenderChat"].map(grabFn).join("\n\n");

  const a = HTML.indexOf('<div id="pv-waiting-room">');
  const b = HTML.indexOf("\n</div>", a) + "\n</div>".length;
  const lobby = HTML.slice(a, b);

  const seat = (i, kind, name, extra) => Object.assign({
    index: i, kind, claimed_name: name, is_host: i === 0, avatar: "", background: "",
    level: 0, xp: 0, xp_goal: 0, best: 0, games: 0, title: "", difficulty: "medium",
  }, extra || {});
  const payload = {
    phase: "lobby",
    seats: [
      seat(0, "human", "TidePoolTim", { avatar: "/avatars/great-white-shark.png",
        background: "/backgrounds/bg-deep.png", level: 47, xp: 3120, xp_goal: 4600,
        best: 412, games: 96, title: "Reef Wanderer" }),
      // A deliberately long name: a lobby is exactly where somebody turns up
      // called something that does not fit.
      seat(1, "human", "AVeryLongPlayerNameIndeed", { avatar: "/avatars/clownfish.png",
        background: "/backgrounds/bg-coral-reef.png", level: 22, xp: 740, xp_goal: 2200,
        best: 268, games: 31, title: "Tide Watcher" }),
      seat(2, "human", "KelpKaiya", { avatar: "/avatars/mandarin-goby.png",
        background: "/backgrounds/bg-kelp.png", level: 63, xp: 2410, xp_goal: 4600,
        best: 455, games: 210, title: "Deep Diver" }),
      seat(3, "human", null),
      seat(4, "ai", "Bot 1"),
      seat(5, "ai", "Bot 2", { difficulty: "hard" }),
    ],
    room: { quick_play: false, competitive: false, tournament: false, ranked: false,
            allow_spectators: true, visibility: "private" },
    viewer: { seat_index: 0, is_host: true },
    chat_messages: [
      { sender: "System", message: "ReefRunner joined the room", system: true, ts: 1 },
      { sender: "AVeryLongPlayerNameIndeed", message: "gm! first game today", ts: 2 },
      { sender: "KelpKaiya", message: "leave the 4th open, sam's on his way", ts: 3 },
    ],
  };

  const stubs = `
const WR_SLOTS = 8, WR_MIN_TABLE = 2, WR_MAX_TABLE = 8;
let _wrTableBusy = false, _wrPrestigeAsking = false, _wrChatFingerprint = "", _wrBgNames = null;
const _wrPrestigeByName = { tidepooltim: { level: 3 }, kelpkaiya: { level: 5 } };
const latestPayload = ${JSON.stringify(payload)};
function pvLiveAvatar() { return ""; }
function pvLiveAvatarKey() { return "k"; }
function _avSrc(u) { return String(u || ""); }
function _bgSrc(u) { return String(u || ""); }
function setTableSeats() {}
function lobbyKickPlayer() {}
function refreshWaitingRoomFromPayload() {}
function setBotDifficulty() {}
function showToast() {}
window.__fishBgStyle = (u) => "background-image:url('" + u + "');background-size:cover;";
window.__fishBackgroundCatalog = () => ([
  { id: "bg-deep", name: "Deep Ocean", img: "/backgrounds/bg-deep.png" },
  { id: "bg-kelp", name: "Kelp Forest", img: "/backgrounds/bg-kelp.png" },
  { id: "bg-coral-reef", name: "Coral Reef", img: "/backgrounds/bg-coral-reef.png" }]);
window.__ccPrestigeBadgeHtml = (n) =>
  '<span class="cc-pbadge' + (n >= 5 ? " t5" : "") + '"><svg viewBox="0 0 16 16"></svg><span>' + n + '</span></span>';
`;

  const drive = `
const wr = document.getElementById("pv-waiting-room");
wr.classList.add("open"); wr.dataset.wide = "1";
document.getElementById("wr-quick-setup").style.display = "none";
document.getElementById("wr-code-display").textContent = "R7KQ";
document.getElementById("wr-btn-text").textContent = "Waiting for players... (3 of 4 joined)";
document.getElementById("wr-spectate-btn").style.display = "inline-flex";
const list = document.getElementById("wr-players-list");
list.innerHTML = '<div class="wr-players-title">Players in Room</div>';
renderSeatTilesInto(list, latestPayload.seats, true);
_wrRenderChat();
["👋","🦀","🐙","🔥"].forEach(e => {
  const b = document.createElement("button");
  b.type = "button"; b.className = "wr-emote"; b.textContent = e;
  document.getElementById("wr-emote-row").appendChild(b);
});
// Levers the measuring page pulls, so it drives the real render rather than
// asserting against a copy of it.
window.__setChatCount = (n) => {
  const out = [{ sender: "System", message: "ReefRunner joined the room", system: true, ts: 0 }];
  for (let i = 0; i < n; i++) {
    out.push({ sender: ["ReefRunner","KelpKaiya","TidePoolTim"][i % 3],
               message: "message " + (i + 1) + ", long enough to wrap on a narrow lobby", ts: i + 1 });
  }
  latestPayload.chat_messages = out;
  _wrChatFingerprint = "";
  _wrRenderChat();
};
window.__redrawSpots = () => {
  const l = document.getElementById("wr-players-list");
  l.innerHTML = '<div class="wr-players-title">Players in Room</div>';
  renderSeatTilesInto(l, latestPayload.seats, true);
};
`;

  const inner = `<!doctype html><html><head><meta charset="utf-8"><style>
${CSS}
html,body{margin:0;} #pv-waiting-room{display:flex !important; position:static; min-height:100vh;}
</style></head><body>${lobby}<script>${stubs}\n${fns}\n${drive}</scr` + `ipt></body></html>`;

  return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#111;}iframe{border:0;display:block;}</style></head><body>
<iframe id="f" width="1280" height="1000"></iframe><div id="out"></div>
<script>
// JSON.stringify does not escape "/", so the inner document's own closing
// script tag would close this one. Break every "</" so it stays a literal.
const SRC = ${JSON.stringify(inner).replace(/<\//g, "<\\/")};
const WIDTHS = [360, 390, 430, 600, 820, 1024, 1280, 1680];
const L = [];
const f = document.getElementById("f");
function measure(w) {
  const d = f.contentDocument, win = f.contentWindow;
  const ok = (c, m) => L.push((c ? "PASS " : "FAIL ") + w + "px: " + m);
  const r = el => el.getBoundingClientRect();
  const vw = win.innerWidth;
  ok(vw === w, "the iframe really is " + w + "px wide (got " + vw + ")");
  ok(d.documentElement.scrollWidth <= vw + 1,
     "nothing scrolls sideways (content " + d.documentElement.scrollWidth + " in " + vw + ")");

  const tiles = [...d.querySelectorAll(".wr-seat")];
  ok(tiles.length === 8, "all eight spots rendered (" + tiles.length + ")");
  tiles.forEach((el, i) => {
    const b = r(el);
    ok(b.width >= 130 && b.height >= 80, "spot " + i + " has real size (" + Math.round(b.width) + "x" + Math.round(b.height) + ")");
    ok(b.left >= -1 && b.right <= vw + 1, "spot " + i + " is inside the window");
  });
  ok(d.querySelectorAll(".wr-seat-add").length === 2, "the two spots not in play are add spots");

  const rem = [...d.querySelectorAll(".wr-seat-remove")];
  ok(rem.length === 5, "five removable spots got a minus (" + rem.length + ")");
  rem.forEach((el, i) => {
    const b = r(el), st = win.getComputedStyle(el);
    ok(b.width >= 24 && b.height >= 24, "minus " + i + " is tappable (" + Math.round(b.width) + "x" + Math.round(b.height) + ")");
    ok(st.opacity === "1" && st.visibility === "visible" && st.display !== "none",
       "minus " + i + " is visible without hovering");
    ok(/gradient/.test(st.backgroundImage), "minus " + i + " is painted, not a bare glyph");
    const tb = r(el.closest(".wr-seat"));
    ok(b.right <= tb.right + 1 && b.top >= tb.top - 1, "minus " + i + " sits inside its own spot");
  });
  ok(!d.querySelector(".wr-seat-you .wr-seat-remove"), "my own seat has no minus");
  ok(!d.querySelector(".wr-seat-add .wr-seat-remove"), "an add spot has no minus");
  ok(!!d.querySelector(".wr-seat-you .wr-seat-lock"), "my own seat shows a lock instead");

  [...d.querySelectorAll(".wr-seat-name")].forEach((el, i) => {
    ok(r(el).right <= r(el.closest(".wr-seat")).right + 1, "name " + i + " stays inside its spot");
  });
  [...d.querySelectorAll(".wr-seat-stat, .wr-xp-txt")].forEach((el, i) => {
    ok(r(el).right <= r(el.closest(".wr-seat")).right + 1, "stat line " + i + " stays inside its spot");
  });

  const pen = d.querySelector(".wr-seat-edit");
  ok(pen && r(pen).width >= 20, "the change-critter pencil is tappable");

  const log = d.getElementById("wr-chat-log"), input = d.getElementById("wr-chat-text");
  ok(r(log).height >= 60, "the chat log has real height (" + Math.round(r(log).height) + "px)");
  ok(d.querySelectorAll(".wr-chat-line").length === 2, "both chat lines rendered");
  // The room's own bookkeeping ("Host set the table to 3 human players and 0
  // bots", joins, leaves) is drawn as seat tiles, not as chat.
  ok(d.querySelectorAll(".wr-chat-sys").length === 0, "and no System line in the lobby chat");
  ok(!d.getElementById("wr-chat-log").textContent.includes("joined the room"),
     "the System text is nowhere in the log");
  ok(r(input).width >= 90 && r(input).height >= 26, "the chat box is usable (" + Math.round(r(input).width) + "px)");
  const pb = r(d.querySelector(".wr-chat-panel"));
  [...d.querySelectorAll(".wr-chat-panel *")].forEach(el => {
    const b = r(el);
    if (b.width > 0) ok(b.right <= pb.right + 1, "nothing escapes the chat panel: " + (el.id || el.className));
  });

  ["wr-spectate-btn", "wr-leave-btn", "wr-start-btn", "wr-chat-send", "wr-copy-btn"].forEach(id => {
    const b = r(d.getElementById(id));
    ok(b.width >= 60 && b.height >= 26, id + " is tappable (" + Math.round(b.width) + "x" + Math.round(b.height) + ")");
    ok(b.right <= vw + 1, id + " is on screen");
  });
  ok(r(d.getElementById("wr-start-btn")).height <= 170, "the Start button stays a button, not a banner");
  const bt = d.querySelector(".wr-btn-text"), sb = r(d.getElementById("wr-start-btn"));
  ok(r(bt).right <= sb.right + 1 && r(bt).bottom <= sb.bottom + 2, "its lettering stays inside it");
  const box = r(d.querySelector(".wr-box"));
  ok(box.left >= -1 && box.right <= vw + 1, "the lobby box fits the window");

  // ── the chat must not grow with the conversation ──
  // Six messages used to push the panel open and drag the whole right-hand
  // column out of shape. It is a fixed pane now: the log scrolls inside it.
  const panel = d.querySelector(".wr-chat-panel"), deck = d.querySelector(".wr-deck");
  win.__setChatCount(3);
  const panel3 = Math.round(r(panel).height), deck3 = Math.round(r(deck).height);
  win.__setChatCount(30);
  const panel30 = Math.round(r(panel).height), deck30 = Math.round(r(deck).height);
  ok(panel3 === panel30, "thirty messages leave the chat panel the same height (" + panel3 + " vs " + panel30 + ")");
  ok(deck3 === deck30, "…and the deck around it (" + deck3 + " vs " + deck30 + ")");
  const cl = d.getElementById("wr-chat-log");
  ok(cl.scrollHeight > cl.clientHeight + 2, "the messages scroll inside the log instead");
  // Put the log back the way the next width expects to find it.
  win.__setChatCount(2);

  // ── the + asks which ──
  const addTile = d.querySelector(".wr-seat-add");
  ok(!!addTile, "there is a spot to add to");
  addTile.click();
  const opts = [...addTile.querySelectorAll(".wr-add-opt")];
  ok(opts.length === 2, "pressing + offers two answers (" + opts.length + ")");
  ok(/player seat/i.test(opts.map(o => o.textContent).join(" ")), "…a seat for a person");
  ok(/bot/i.test(opts.map(o => o.textContent).join(" ")), "…and a bot");
  opts.forEach((o, i) => {
    const b = r(o);
    ok(b.width >= 80 && b.height >= 28, "option " + i + " is tappable (" + Math.round(b.width) + "x" + Math.round(b.height) + ")");
    ok(b.right <= r(addTile).right + 1 && b.left >= r(addTile).left - 1,
       "option " + i + " stays inside its own spot");
  });
  const cancel = addTile.querySelector(".wr-add-cancel");
  ok(!!cancel, "the chooser can be backed out of");
  cancel.click();
  ok(!!addTile.querySelector(".wr-seat-add-plus"), "…and the + comes back");
  win.__redrawSpots();
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

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: skipping the layout half.");
} else {
  console.log("\nthe real render, phone through desktop");
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-lobby-"));
  const file = path.join(tmp, "lobby.html");
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
  if (!m || !m[1].trim()) {
    check(false, "measurements came back from the iframe");
  } else {
    m[1].split("\n").filter(Boolean).forEach(line => {
      check(line.startsWith("PASS "), line.replace(/^(PASS|FAIL) /, ""));
    });
  }
  fs.rmSync(tmp, { recursive: true, force: true });
}

console.log(`\n${fail ? "FAILED" : "All"} ${fail ? fail + " of " + (pass + fail) : pass} checks${fail ? "" : " passed"}.`);
process.exit(fail ? 1 : 0);
