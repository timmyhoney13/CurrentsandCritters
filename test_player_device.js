#!/usr/bin/env node
/* What device everyone is on, on every screen that names them.
 *
 * Run:  node test_player_device.js      (needs Google Chrome / Chromium)
 *
 * The answer is worked out in the browser (js/device-select.js), pushed to the
 * room, and relayed back to everybody on the seat list. This measures the far
 * end of that: what actually appears on screen, in real Chrome, driven by the
 * real render functions sliced out of preview-app.js, so a change to a
 * renderer shows up here instead of drifting away from a copy of it.
 *
 * The three surfaces the game shows people on, and the trap in each:
 *
 *  1. THE WAITING ROOM. Eight tiles, and each already carries a critter, a
 *     background, a Level, an XP bar, a record line and up to three chips. The
 *     device is one more thing in that footer, so the risk is not that it is
 *     missing, it is that adding it pushes a tile out of shape on a phone.
 *
 *  2. THE IN-GAME SEAT CLUSTER. Eight faces at 26px on a small screen. The
 *     badge rides on the avatar, and the avatar wrap is a CIRCLE with
 *     overflow:hidden (it must be, or an equipped background bleeds out of
 *     it), so a badge parented to that wrap gets sliced in half. It goes in a
 *     box around the wrap instead, and this pins that it is really visible.
 *
 *  3. THE FRIENDS TAB. Shown only for a friend who is ONLINE. The device on a
 *     profile is where that person was when they were last here; printed under
 *     "Offline" it reads as where they are now, which is a lie about somebody
 *     who might have been gone for a week.
 *
 * And the rule under all three: a device is TWO WORDS or nothing. A bot, an
 * open chair and a client too old to report one all draw as nothing at all,
 * never as a guess, because a wrong answer here is worse than no answer.
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
const DEVJS = fs.readFileSync(path.join(CLIENT, "js/device-select.js"), "utf8");
const SERVER = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");

// The app with its comments removed. A claim about what the app PRINTS has to
// be made against what it prints: a comment explaining the label reads the same
// to a regex as the code hard-coding one.
const APP_SAYS = APP
  .replace(/\/\*[\s\S]*?\*\//g, " ")
  .split("\n").filter(l => !/^\s*\/\//.test(l)).join("\n");

let pass = 0, fail = 0;
function check(cond, name, extra) {
  if (cond) { pass++; }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

// Slice one function out of the app by balancing its braces. `indent` is how
// deep it sits: the lobby and board renderers are top-level in the app's IIFE,
// the friends helpers live one scope further in.
function grabFn(name, indent) {
  const pad = " ".repeat(indent === undefined ? 2 : indent);
  const re = new RegExp("^" + pad + "(?:async )?function " + name + "\\s*\\(", "m");
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
//  1. ONE PLACE DECIDES HOW A DEVICE IS SAID
// ════════════════════════════════════════════════════════════════════════
console.log("\none table of words, not one per screen");
{
  check(/window\.ccDeviceLabel = function/.test(DEVJS),
        "device-select.js owns the wording");
  check(/computer:\s*\{[^}]*label:\s*"Computer"/.test(DEVJS), "…for a computer");
  check(/mobile:\s*\{[^}]*label:\s*"Mobile"/.test(DEVJS), "…and for a phone");
  // Every surface has to go through it, or four screens start inventing four
  // different words for the same two answers.
  const uses = (APP.match(/ccDeviceLabel\(/g) || []).length;
  check(uses >= 4, "every surface reads its wording from that one function",
        "found " + uses + " uses");
  check(!/["'`]💻 Computer["'`]/.test(APP_SAYS) && !/["'`]📱 Mobile["'`]/.test(APP_SAYS),
        "nothing hard-codes the label next to its own icon");

  // The device is detected, not asked for, and a real input event outranks the
  // opening guess. When that flip happens the room's copy of us is stale, so
  // the change has to be announced rather than waited on.
  check(/var changed = device !== _device/.test(DEVJS),
        "a real change is told apart from a re-apply");
  check(/window\.ccOnDeviceChange/.test(DEVJS), "…and announced");
  check(/window\.ccOnDeviceChange = function/.test(APP), "the app listens for it");
  const onChange = APP.slice(APP.indexOf("window.ccOnDeviceChange = function"),
                             APP.indexOf("window.ccOnDeviceChange = function") + 420);
  check(/_lastPushedDevice = ""/.test(onChange),
        "the send-once throttle is cleared first",
        "otherwise the ONE value that matters is the one never sent");
  check(/pushMySeatDevice\(\)/.test(onChange), "…then the room is told");
  check(/__fishReportPresenceDevice/.test(onChange), "…and so is the Friends tab");
}

// ════════════════════════════════════════════════════════════════════════
//  2. THE PUSH, AND WHY IT IS ITS OWN
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe room is told, on its own request");
{
  check(/function pushMySeatDevice/.test(APP), "there is a device push");
  const push = APP.slice(APP.indexOf("function pushMySeatDevice"),
                         APP.indexOf("window.__fishPushSeatDevice"));
  check(/\/device`/.test(push), "it posts to the room's device endpoint");
  check(/parts\[3\] == "device"/.test(SERVER), "…which the server serves");
  check(/if \(!dev\) return;/.test(push),
        "a client that has not worked one out yet says nothing at all",
        "an empty push would clear the chip it already has");
  check(/_lastPushedDevice/.test(push), "the same answer is not sent twice");
  check(/isSpectating\(\)/.test(push) && /spectator_token/.test(push),
        "a watcher reports under their own token, having no seat");
  check(/"device": spec\.get\("device"\) or ""/.test(SERVER),
        "…and the server lists watchers with it");

  // Pushed on the same beats as the icon and the background, so the answer is
  // kept current for as long as we are in the room rather than only at boot.
  check(/pushMySeatAvatar\(\); \} catch \(e\) \{\} try \{ pushMySeatBackground\(\); \} catch \(e\) \{\} try \{ pushMySeatDevice\(\)/.test(APP),
        "sent alongside the other two on every state tick");
  check(/__fishForgetPushedAvatar = function \(\) \{ _lastPushedAvatar = ""; _lastPushedBg = ""; _lastPushedDevice = ""/.test(APP),
        "and forgotten when the session changes hands",
        "the next person may be on the same machine, so the throttle would send nothing");
}

// ════════════════════════════════════════════════════════════════════════
//  3. THE FRIENDS TAB'S RULE: ONLINE ONLY
// ════════════════════════════════════════════════════════════════════════
console.log("\na friend's device is only shown while they are online");
{
  check(/function friendDeviceHtml/.test(APP), "the friend rows have one helper for it");
  const fd = grabFn("friendDeviceHtml", 4);
  check(/if \(!isOnline\) return "";/.test(fd),
        "an offline friend is shown no device at all",
        "the stored one is where they WERE, and would read as where they are");
  check(/escapeHtml\(/.test(fd), "what it prints is escaped");
  // Four row renderers paint a friend (Player Home's preview, the tab itself,
  // and the two reference-mode copies). All four have to call it, or the same
  // friend says different things on two screens.
  const uses = (APP.match(/friendDeviceHtml\(/g) || []).length;
  check(uses >= 5, "every friend row renderer uses it", "found " + uses);
  check(/In Game\$\{deviceHtml\}/.test(APP),
        "a friend who is in a game shows it too",
        "that is the moment it matters most");

  // It rides on the presence heartbeat rather than a second write.
  const pres = APP.slice(APP.indexOf("async function setOnlineStatus"),
                         APP.indexOf("function startPresencePing"));
  check(/payload\.device = dev/.test(pres), "the heartbeat carries the device");
  check(/if \(isOnline\) \{[\s\S]*?payload\.device = dev/.test(pres),
        "…only when the heartbeat says online",
        "a device stored beside offline is a fact about a browser that has gone");
  check(/window\.__fishReportPresenceDevice/.test(APP),
        "and a mid-session flip is written straight away, not 90 seconds later");
}

// ════════════════════════════════════════════════════════════════════════
//  4. BOTS AND EMPTY CHAIRS SAY NOTHING
// ════════════════════════════════════════════════════════════════════════
console.log("\nno answer beats a wrong one");
{
  const sd = grabFn("_wrSeatDevice");
  check(/if \(!s \|\| s\.kind === "ai"\) return null;/.test(sd), "a bot has no device");
  check(/window\.CC_DEVICE \|\| s\.device/.test(sd),
        "my own seat answers from the local detection first",
        "the relayed copy is a server round-trip behind my own first touch");
  check(/ccDeviceLabel/.test(sd), "and the answer is worded in the one place");
}

// ════════════════════════════════════════════════════════════════════════
//  5. IT ACTUALLY APPEARS  (the real renderers, in real Chrome)
// ════════════════════════════════════════════════════════════════════════
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => fs.existsSync(p));

function page() {
  const lobbyFns = ["_wrEl", "_wrChip", "_wrSeatDevice", "_wrDeviceChip", "_wrBgName",
                    "_wrNum", "_wrRemoveBtn", "_wrLock", "_wrSeatAvatarUrl", "_wrCounts",
                    "buildDifficultyBox", "_wrLoadPrestige", "_wrSeatCard", "_wrAddCard",
                    "_wrRenderCapacity", "renderSeatTilesInto"].map(f => grabFn(f)).join("\n\n");
  const gameFns = ["pvSeatHash", "pvSeatDefaultAvatar", "_applyAvBg",
                   "renderPlayerSeats"].map(f => grabFn(f)).join("\n\n");
  const friendFns = grabFn("friendDeviceHtml", 4);

  const a = HTML.indexOf('<div id="pv-waiting-room">');
  const b = HTML.indexOf("\n</div>", a) + "\n</div>".length;
  const lobby = HTML.slice(a, b);

  const seat = (i, kind, name, extra) => Object.assign({
    index: i, kind, claimed_name: name, is_host: i === 0, avatar: "", background: "",
    device: "", level: 0, xp: 0, xp_goal: 0, best: 0, games: 0, title: "",
    difficulty: "medium", is_away: false, kicked: false,
  }, extra || {});

  // One room, drawn twice: as a lobby and as a running game. Deliberately
  // mixed, because the interesting rows are the ones with no answer.
  const seats = [
    seat(0, "human", "TidePoolTim", { avatar: "/avatars/great-white-shark.png",
      background: "/backgrounds/bg-deep.png", device: "computer", level: 47,
      xp: 3120, xp_goal: 4600, best: 412, games: 96, title: "Reef Wanderer" }),
    seat(1, "human", "AVeryLongPlayerNameIndeed", { avatar: "/avatars/clownfish.png",
      background: "/backgrounds/bg-coral-reef.png", device: "mobile", level: 22,
      xp: 740, xp_goal: 2200, best: 268, games: 31, title: "Tide Watcher" }),
    // An old client: in the room, playing, and has never said what it is on.
    seat(2, "human", "KelpKaiya", { avatar: "/avatars/mandarin-goby.png",
      device: "", level: 63, xp: 2410, xp_goal: 4600, best: 455, games: 210 }),
    seat(3, "human", null),
    seat(4, "ai", "Bot 1"),
    seat(5, "ai", "Bot 2", { difficulty: "hard" }),
  ];
  const payload = {
    phase: "lobby",
    seats,
    room: { quick_play: false, competitive: false, tournament: false, ranked: false,
            allow_spectators: true, visibility: "private" },
    viewer: { seat_index: 0, is_host: true },
    chat_messages: [],
  };
  const players = [
    { index: 0, name: "TidePoolTim", score: 84, hand_count: 7, avatar: "/avatars/great-white-shark.png", background: "/backgrounds/bg-deep.png" },
    { index: 1, name: "AVeryLongPlayerNameIndeed", score: 61, hand_count: 6, avatar: "/avatars/clownfish.png", background: "/backgrounds/bg-coral-reef.png" },
    { index: 2, name: "KelpKaiya", score: 73, hand_count: 8, avatar: "/avatars/mandarin-goby.png" },
    { index: 4, name: "Bot 1", score: 55, hand_count: 5 },
  ];

  const stubs = `
const WR_SLOTS = 8, WR_MIN_TABLE = 2, WR_MAX_TABLE = 8;
let _wrTableBusy = false, _wrPrestigeAsking = false, _wrBgNames = null;
let _seatsRenderKey = "", canInteract = false;
const _wrPrestigeByName = {};
const latestPayload = ${JSON.stringify(payload)};
const _latestSeatsForSurf = latestPayload.seats;
window.CC_DEVICE = "computer";
function pvLiveAvatar() { return ""; }
function _avSrc(u) { return String(u || ""); }
function _bgSrc(u) { return String(u || ""); }
function isLikelyAiName(n) { return /^Bot /.test(String(n || "")); }
const PV_SEAT_AVATARS = [
  "/avatars/mullet.png", "/avatars/sardine.png", "/avatars/flying-fish.png",
  "/avatars/bunker.png", "/avatars/bonito.png", "/avatars/mahi-mahi.png",
  "/avatars/roosterfish.png", "/avatars/blue-tang.png", "/avatars/clownfish.png",
  "/avatars/blue-marlin.png", "/avatars/barracuda.png", "/avatars/manta-ray.png"];
function openBoardFocus() {}
function attachBoardHover() {}
function setTableSeats() {}
function lobbyKickPlayer() {}
function refreshWaitingRoomFromPayload() {}
function setBotDifficulty() {}
function showToast() {}
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
window.__fishMyAvatarUrl = () => "/avatars/great-white-shark.png";
window.__fishEquippedBackground = () => "/backgrounds/bg-deep.png";
window.__fishBgStyle = (u) => "background-image:url('" + u + "');background-size:cover;";
window.__fishBackgroundCatalog = () => ([
  { id: "bg-deep", name: "Deep Ocean", img: "/backgrounds/bg-deep.png" },
  { id: "bg-coral-reef", name: "Coral Reef", img: "/backgrounds/bg-coral-reef.png" }]);
`;

  const drive = `
// ── the waiting room ──
const wr = document.getElementById("pv-waiting-room");
wr.classList.add("open"); wr.dataset.wide = "1";
document.getElementById("wr-quick-setup").style.display = "none";
const list = document.getElementById("wr-players-list");
list.innerHTML = "";
renderSeatTilesInto(list, latestPayload.seats, true);

// ── the in-game seat cluster ──
renderPlayerSeats(${JSON.stringify(players)}, 1, 0);

// ── a friends list ──
const FR = [
  { nick: "OnlineOnAPhone", online: true, profile: { device: "mobile" } },
  { nick: "OnlineOnAPC", online: true, profile: { device: "computer" } },
  { nick: "OnlineButSilent", online: true, profile: {} },
  { nick: "OfflineWasOnAPhone", online: false, profile: { device: "mobile" } },
];
const frList = document.getElementById("cc-friends-probe");
FR.forEach(f => {
  const d = document.createElement("div");
  d.className = "ph-fr";
  d.dataset.nick = f.nick;
  d.innerHTML = '<div class="ph-fr-av"></div>'
    + '<div class="ph-fr-main"><div class="ph-fr-name">' + f.nick + '</div>'
    + '<div class="ph-fr-meta">Level 12 • Active now</div></div>'
    + '<div class="ph-fr-status ' + (f.online ? "ph-fr-online" : "ph-fr-offline") + '">'
    + '<div class="ph-fr-dot"></div>' + (f.online ? "Online" : "Offline")
    + friendDeviceHtml(f.profile, f.online) + '</div>';
  frList.appendChild(d);
});
`;

  const inner = `<!doctype html><html><head><meta charset="utf-8"><style>
${CSS}
html,body{margin:0;background:#0b2138;}
#pv-waiting-room{display:flex !important; position:static; min-height:auto;}
#cc-board-probe{display:flex; gap:8px; padding:10px; align-items:flex-start;}
#cc-friends-probe{background:#fff; max-width:520px;}
</style></head><body>
<div id="cc-board-probe">
  <div id="pv-seats-left" class="pv-seat-cluster"></div>
  <div id="pv-seats-right" class="pv-seat-cluster"></div>
</div>
<div id="cc-friends-probe"></div>
${lobby}
<script>${DEVJS}</scr` + `ipt>
<script>
// A throw in here would leave the page half-built and every check below would
// blame the render for a harness fault. Stash the reason where the measuring
// page can read it instead.
window.__bootErr = "";
window.onerror = function (m) { window.__bootErr = String(m); };
try {
${stubs}
${lobbyFns}

${gameFns}

${friendFns}
${drive}
} catch (e) { window.__bootErr = String((e && e.stack) || e); }
</scr` + `ipt></body></html>`;

  return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#111;}iframe{border:0;display:block;}</style></head><body>
<iframe id="f" width="1280" height="1400"></iframe><div id="out"></div>
<script>
// JSON.stringify does not escape "/", so the inner document's own closing
// script tag would close this one. Break every "</" so it stays a literal.
const SRC = ${JSON.stringify(inner).replace(/<\//g, "<\\/")};
const WIDTHS = [360, 390, 430, 768, 1024, 1280];
const L = [];
const f = document.getElementById("f");
const vis = (el) => {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  const cs = f.contentWindow.getComputedStyle(el);
  return r.width > 0 && r.height > 0 && cs.visibility !== "hidden"
      && cs.display !== "none" && Number(cs.opacity) > 0.05;
};

function measure(w) {
  const d = f.contentDocument;
  const ok = (c, m) => L.push((c ? "PASS " : "FAIL ") + w + "px: " + m);
  ok(!f.contentWindow.__bootErr, "the page built without throwing: "
     + String(f.contentWindow.__bootErr || "").slice(0, 300));

  // ── the waiting room's tiles ──
  const tiles = [...d.querySelectorAll("#wr-players-list .wr-seat")];
  const byName = (n) => tiles.find(t => (t.textContent || "").indexOf(n) >= 0);
  const tim = byName("TidePoolTim"), longy = byName("AVeryLong"), kelp = byName("KelpKaiya");
  const timChip = tim && tim.querySelector(".wr-chip-device-computer");
  const longChip = longy && longy.querySelector(".wr-chip-device-mobile");
  ok(!!timChip && vis(timChip), "the lobby tile says Computer, visibly");
  ok(!!longChip && vis(longChip), "…and Mobile on the one that reported it");
  ok(timChip && /Computer/.test(timChip.textContent), "the word is on the chip, not only an icon");
  ok(kelp && !kelp.querySelector("[class*=wr-chip-device]"),
     "a player who has never reported one gets no chip");
  ok(![...d.querySelectorAll(".wr-seat-bot")].some(t => t.querySelector("[class*=wr-chip-device]")),
     "and neither does a bot");
  // The chip must not push the tile out of shape.
  tiles.forEach((t, i) => {
    ok(t.scrollWidth <= t.clientWidth + 1, "tile " + i + " does not overflow sideways");
  });
  const foot = tim && tim.querySelector(".wr-seat-foot");
  ok(!foot || foot.scrollHeight <= foot.clientHeight + 1,
     "the footer row still fits its chips");

  // ── the in-game seat cluster ──
  const seatEls = [...d.querySelectorAll(".pv-seat[data-player-index]")];
  const s0 = seatEls.find(s => s.dataset.playerIndex === "0");
  const s1 = seatEls.find(s => s.dataset.playerIndex === "1");
  const s2 = seatEls.find(s => s.dataset.playerIndex === "2");
  const s4 = seatEls.find(s => s.dataset.playerIndex === "4");
  const b0 = s0 && s0.querySelector(".pv-seat-device");
  const b1 = s1 && s1.querySelector(".pv-seat-device");
  ok(!!b0 && vis(b0), "the in-game seat carries a device badge");
  ok(b0 && b0.classList.contains("pv-seat-device-computer"), "…the right one for a computer");
  ok(b1 && b1.classList.contains("pv-seat-device-mobile"), "…and for a phone");
  ok(s2 && !s2.querySelector(".pv-seat-device"), "silence draws nothing in a game either");
  ok(s4 && !s4.querySelector(".pv-seat-device"), "a bot's seat has no badge");
  // The trap: the avatar wrap is a circle with overflow:hidden. A badge inside
  // it would be sliced away. Measure that it is really on screen, whole.
  if (b0) {
    const br = b0.getBoundingClientRect();
    const wrap = s0.querySelector(".pv-seat-avatar-wrap").getBoundingClientRect();
    const box = s0.querySelector(".pv-seat-avbox").getBoundingClientRect();
    ok(br.width >= 8 && br.height >= 8, "the badge has real size");
    ok(br.right > wrap.right - 1 || br.bottom > wrap.bottom - 1,
       "it sits on the rim of the face, where a clipped one could not");
    ok(br.right <= box.right + 12 && br.bottom <= box.bottom + 12,
       "…and stays with its own seat");
    // Whatever is painted at the badge's middle has to belong to this seat.
    // (The badge itself takes no pointer events, by design, so the hit test
    // reports what is UNDER it: that is exactly the seat it rides on, and it
    // is another seat or a stray overlay that would be the bug.)
    const el = d.elementFromPoint((br.left + br.right) / 2, (br.top + br.bottom) / 2);
    ok(!!el && s0.contains(el), "it lands on its own seat and nothing else's");
    // And it must not widen that seat. The cluster is eight of these across a
    // phone, so a badge that added even a few pixels each would push the end
    // one off the screen.
    const sr = s0.getBoundingClientRect();
    ok(br.left >= sr.left - 0.5 && br.right <= sr.right + 0.5,
       "and stays inside the seat's own box, so it widens nothing");
  }

  // ── the friends rows ──
  const frRow = (n) => d.querySelector('#cc-friends-probe .ph-fr[data-nick="' + n + '"]');
  const phone = frRow("OnlineOnAPhone"), pc = frRow("OnlineOnAPC");
  const silent = frRow("OnlineButSilent"), off = frRow("OfflineWasOnAPhone");
  const phoneDev = phone && phone.querySelector(".ph-fr-device");
  ok(!!phoneDev && vis(phoneDev), "an online friend's device is shown");
  ok(phoneDev && /Mobile/.test(phoneDev.textContent), "…in words");
  ok(pc && /Computer/.test((pc.querySelector(".ph-fr-device") || {}).textContent || ""),
     "…and the other way for a computer");
  ok(silent && !silent.querySelector(".ph-fr-device"),
     "an online friend who has not reported one shows nothing");
  ok(off && !off.querySelector(".ph-fr-device"),
     "an OFFLINE friend never shows one, even with a device on file");
  [phone, pc, silent, off].forEach((r, i) => {
    if (r) ok(r.scrollWidth <= r.clientWidth + 1, "friend row " + i + " does not overflow");
  });
  const st = phone && phone.querySelector(".ph-fr-status");
  const nm = phone && phone.querySelector(".ph-fr-name");
  if (st && nm) {
    ok(nm.getBoundingClientRect().right <= st.getBoundingClientRect().left + 1,
       "the device under the status does not crowd the name off the row");
  }
}

let i = 0;
function step() {
  if (i >= WIDTHS.length) { document.getElementById("out").textContent = L.join("\\n"); return; }
  const w = WIDTHS[i++];
  f.width = w;
  f.srcdoc = SRC;
  f.onload = () => setTimeout(() => { try { measure(w); } catch (e) { L.push("FAIL " + w + "px: threw " + e); } step(); }, 90);
}
step();
</scr` + `ipt></body></html>`;
}

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: the render half did not run.");
} else {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ccdev-"));
  const file = path.join(dir, "probe.html");
  fs.writeFileSync(file, page());
  let dom = "";
  try {
    dom = execFileSync(CHROME, [
      "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      "--virtual-time-budget=9000", "--window-size=1400,1500",
      "--dump-dom", "file://" + file,
    ], { encoding: "utf8", maxBuffer: 1 << 28, stdio: ["ignore", "pipe", "ignore"] });
  } catch (e) {
    dom = "";
  }
  const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
  const lines = m ? m[1].split("\n").map(s => s.trim()).filter(Boolean) : [];
  console.log("\nwhat is really on the screen, at every width");
  check(lines.length > 0, "the probe page ran", lines.length ? "" : "no output from Chrome");
  lines.forEach(l => {
    const okLine = l.startsWith("PASS ");
    check(okLine, l.replace(/^(PASS|FAIL) /, ""));
  });
}

console.log("");
if (fail) {
  console.log(fail + " of " + (pass + fail) + " checks FAILED");
  process.exit(1);
}
console.log("All " + pass + " checks passed.");
