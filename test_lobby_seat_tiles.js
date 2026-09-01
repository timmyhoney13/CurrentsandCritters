#!/usr/bin/env node
/* The waiting room's seat tiles: what they say, and that they really fit.
 *
 * The lobby is now one tile per seat, carrying the player's critter, the
 * background behind it, their Level and their Prestige badge. Three things
 * about it are easy to break and expensive to notice:
 *
 *  1. ADDING IS A BOT. There is no "add a player seat" button any more, and
 *     there must not be one: an unclaimed human seat does not become a bot at
 *     kickoff, it stops the game starting at all (start_game refuses while one
 *     is open, see test_lobby_seat_tiles.py). A person joins with the room
 *     code. So the only thing the lobby can add is a bot, and the only thing
 *     it may say about an open seat is that it is holding the game up.
 *
 *  2. THE REMOVE BUTTON HAS TO BE FINDABLE. It is the destructive control on
 *     the screen. Hover-only, low contrast or a 12px hit target all make it
 *     something players hit by accident or cannot find at all, so its size and
 *     its always-on paint are pinned here.
 *
 *  3. IT HAS TO FIT. A lobby holds up to 8 seats plus the add button, on
 *     anything from a phone to a desktop. Checking one window size passes
 *     while every other one is broken, so the layout half runs at four.
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

// The app with its comments taken out. Claims about what the app SAYS have to
// be made against what it says, not against a comment explaining the rule (a
// comment denying "it becomes a bot" reads the same to a regex as the copy
// making that claim).
const APP_SAYS = APP
  .replace(/\/\*[\s\S]*?\*\//g, " ")
  .split("\n").filter(l => !/^\s*\/\//.test(l)).join("\n");

// The render function, sliced out so a claim about it can't accidentally be
// satisfied by some unrelated corner of a 30k-line file.
const RENDER = APP.slice(APP.indexOf("function renderSeatTilesInto"),
                         APP.indexOf("let _wrLastArgs = null;"));

// ════════════════════════════════════════════════════════════════════════
//  1. ADDING IS A BOT
// ════════════════════════════════════════════════════════════════════════
console.log("\nadding a seat means adding a bot");
{
  check(/wr-seat-add/.test(RENDER) && /Add a bot/.test(RENDER),
        "the add tile offers a bot");
  check(!/Add a player seat/i.test(RENDER),
        "there is no 'add a player seat' button",
        "an empty human seat blocks the start, it never becomes a bot");
  check(/setTableSeats\(humans, bots \+ 1\)/.test(RENDER),
        "the add tile asks for one more bot and the same human spots");
  check(/canAddBot[\s\S]{0,200}?WR_MAX_TABLE/.test(RENDER),
        "it stops at the table's ceiling");
  check(/!room\.ranked/.test(RENDER),
        "a competitive room is people only, so it offers no bot");

  // The open seat says the true thing and offers the true way out.
  check(/Start is locked until/.test(RENDER),
        "an open seat says the start is locked");
  check(/Make it a bot/.test(RENDER) && /setTableSeats\(humans - 1, bots \+ 1\)/.test(RENDER),
        "…and offers the host the one way past it");
  check(!/(becomes?|turns? into) an? bot when (you|the host) (cast|start)|bot when you cast off|becomes? a bot at (the )?start/i.test(APP_SAYS),
        "nothing in the app claims an open seat becomes a bot at kickoff",
        "Quick Play's own copy is fine: it converts spare seats when the host picks, not at kickoff");

  // The caption on the box has to agree with all of that.
  check(HTML.includes('id="wr-caption"'), "preview.html has #wr-caption");
  check(/can't start until somebody sits in it, or the host turns it into a bot/.test(APP),
        "the caption spells out both ways past an empty seat");
}

// ════════════════════════════════════════════════════════════════════════
//  2. THE HOST'S REMOVAL
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe host can remove people, bots and empty seats");
{
  check(/async function lobbyKickPlayer/.test(APP), "there is a lobby removal");
  check(/\/lobby_kick`/.test(APP), "it posts to the lobby_kick endpoint");
  check(/lobby_kick/.test(SERVER) && /def lobby_remove_player/.test(SERVER),
        "the server has that endpoint and its method");
  check(/host_token: getHostToken\(\)[\s\S]{0,120}?target_seat_index: seatIndex/.test(APP),
        "the removal is sent with host authorisation and a seat index");
  check(/window\.confirm\(/.test(APP.slice(APP.indexOf("async function lobbyKickPlayer"),
                                           APP.indexOf("async function watchFromLobby"))),
        "removing a person asks first: it is permanent");

  // Who gets a minus, and who must never get one.
  check(/canShape && isAI[\s\S]{0,160}?Remove a bot/.test(RENDER),
        "a bot tile has a remove button");
  check(/canShape && isOpen[\s\S]{0,200}?Remove this seat/.test(RENDER),
        "an empty seat can be taken off the table");
  check(/canShape && !isAI && !isMine && s\.claimed_name && !s\.is_host/.test(RENDER),
        "a seated player gets one only from the host, and never on the host or on me");
  check(/const canShape = isHost &&/.test(RENDER),
        "nobody but the host sees any of them");
  check(/!room\.quick_play && !room\.competitive && !room\.tournament/.test(RENDER),
        "…and not in the rooms whose shape is not the host's to change");
}

// ════════════════════════════════════════════════════════════════════════
//  3. WHAT A TILE SHOWS
// ════════════════════════════════════════════════════════════════════════
console.log("\na tile shows the player everyone knows");
{
  check(/s\.background/.test(RENDER) && /__fishBgStyle/.test(RENDER),
        "the equipped background paints behind the critter");
  check(/_wrSeatAvatarUrl/.test(APP) && /pvLiveAvatar/.test(APP),
        "the critter comes off the seat, falling back to the live table");
  check(/⭐ Level/.test(RENDER), "the Level is on the tile");
  check(/__ccPrestigeBadgeHtml/.test(RENDER),
        "the Prestige badge is the game's real one, not a re-drawing");
  check(/__ccPrestigeLookupByName/.test(APP),
        "Prestige is looked up from the Prestige service, not taken from the seat");
  check(/"avatar": seat\.avatar or ""/.test(SERVER)
        && /"background": seat\.background or ""/.test(SERVER)
        && /"level": int\(getattr\(seat, "level", 0\) or 0\)/.test(SERVER),
        "the server sends that look with the seat list");
  check(/wr-seat-edit/.test(RENDER) && /__fishOpenAvatarGallery/.test(RENDER),
        "you can change your own critter from your own seat");
  check(/isMine/.test(RENDER), "…and only from your own");
}

// ════════════════════════════════════════════════════════════════════════
//  4. WATCH INSTEAD
// ════════════════════════════════════════════════════════════════════════
console.log("\nwatch instead of playing");
{
  check(HTML.includes('id="wr-spectate-btn"'), "preview.html has the button");
  check(/async function watchFromLobby/.test(APP), "and a handler for it");
  const watch = APP.slice(APP.indexOf("async function watchFromLobby"),
                          APP.indexOf("function renderSeatTilesInto"));
  check(/\/leave`/.test(watch), "it gives the seat up");
  check(/joinAsSpectator\(rid\)/.test(watch), "then joins the room as a spectator");
  check(/setSeatToken\(""\)/.test(watch), "and lets go of the seat token");
  check(/filled > 1/.test(APP),
        "it is hidden for the last person seated: leaving then closes the room");
  check(/allow_spectators !== false/.test(APP),
        "and hidden in a room that does not take spectators");
}

// ════════════════════════════════════════════════════════════════════════
//  5. THE ART IS REALLY SERVED
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe tide pool behind the lobby");
{
  check(fs.existsSync(path.join(CLIENT, "lobby-tide-pool.png")), "the art is in the client");
  check(fs.existsSync(path.join(CLIENT, "lobby-tide-pool.webp")),
        "…with the WebP sibling every other client image has");
  check(/lobby-tide-pool\.png\?v=/.test(CSS), "the CSS points at it, cache-busted");
  // A new client PNG that is not in the server's allowlist 404s in production
  // and nowhere else: the page just renders on the fallback gradient.
  check(/lobby-tide-pool\|/.test(SERVER) || /lobby-tide-pool/.test(SERVER),
        "the server's client-asset route serves it",
        "add it to the PNG allowlist regex or it 404s in production");
  check(/allow_webp=True/.test(SERVER), "that route hands over the WebP when it can");
}

// ════════════════════════════════════════════════════════════════════════
//  6. EVERY CLASS THE JS MAKES HAS A STYLE
// ════════════════════════════════════════════════════════════════════════
console.log("\nstyles exist");
{
  ["wr-seat-grid", "wr-seat", "wr-seat-bot", "wr-seat-open", "wr-seat-you",
   "wr-seat-top", "wr-seat-av", "wr-seat-avbg", "wr-seat-av-empty", "wr-seat-edit",
   "wr-seat-id", "wr-seat-name", "wr-seat-pbadge", "wr-seat-chips",
   "wr-chip", "wr-chip-lvl", "wr-chip-host", "wr-chip-ready", "wr-chip-bot",
   "wr-chip-you", "wr-chip-wait", "wr-seat-foot", "wr-seat-hint", "wr-seat-tobot",
   "wr-seat-remove", "wr-seat-add", "wr-seat-add-plus", "wr-seat-add-t",
   "wr-seat-add-s"].forEach(cls => {
    check(APP.includes(cls), `the JS makes .${cls}`);
    check(new RegExp("\\." + cls + "[\\s,{:]").test(CSS), `.${cls} has a style`);
  });
  // Markup-side classes and ids, styled the same way.
  check(HTML.includes('class="wr-foot"') && /\.wr-foot[\s,{:]/.test(CSS),
        ".wr-foot is in the markup and has a style");
  check(/#wr-spectate-btn[\s,{:]/.test(CSS), "#wr-spectate-btn has a style");
  check(/#wr-leave-btn[\s,{:]/.test(CSS),
        "#wr-leave-btn is styled in the sheet, not by an inline style attribute");
}

// ════════════════════════════════════════════════════════════════════════
//  7. IT ACTUALLY FITS  (real Chrome, four window sizes)
// ════════════════════════════════════════════════════════════════════════
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => fs.existsSync(p));

// One tile of each kind, built exactly the way renderSeatTilesInto builds them.
function tile(kind, i) {
  if (kind === "add") {
    return '<button type="button" class="wr-seat wr-seat-add">'
      + '<span class="wr-seat-add-plus">+</span>'
      + '<span class="wr-seat-add-t">Add a bot</span>'
      + '<span class="wr-seat-add-s">Fills a seat straight away</span></button>';
  }
  const remove = '<button type="button" class="wr-seat-remove"><span>−</span></button>';
  if (kind === "open") {
    return '<div class="wr-seat wr-seat-open">' + remove
      + '<div class="wr-seat-top"><div class="wr-seat-av wr-seat-av-empty">' + i + "</div>"
      + '<div class="wr-seat-id"><div class="wr-seat-name"><span>Seat ' + i + "</span></div>"
      + '<div class="wr-seat-chips"><span class="wr-chip wr-chip-wait">Waiting for a player</span></div>'
      + "</div></div>"
      + '<div class="wr-seat-foot"><div class="wr-seat-hint">Start is locked until somebody sits here.</div>'
      + '<button class="wr-seat-tobot">🤖 Make it a bot</button></div></div>';
  }
  if (kind === "bot") {
    return '<div class="wr-seat wr-seat-bot">' + remove
      + '<div class="wr-seat-top"><div class="wr-seat-av"><img alt=""></div>'
      + '<div class="wr-seat-id"><div class="wr-seat-name"><span>Bot ' + i + "</span></div>"
      + '<div class="wr-seat-chips"><span class="wr-chip wr-chip-bot">🤖 Bot</span></div></div></div>'
      + '<div class="wr-seat-foot"><div class="wr-diff-box">'
      + '<button class="wr-diff-pill wr-diff-easy">Easy</button>'
      + '<button class="wr-diff-pill wr-diff-medium active">Medium</button>'
      + '<button class="wr-diff-pill wr-diff-hard">Hard</button></div></div></div>';
  }
  const you = kind === "you";
  // A deliberately long name: a lobby is exactly where somebody shows up
  // called something that does not fit.
  const name = you ? "TidePoolTim" : "AVeryLongPlayerNameIndeed" + i;
  return '<div class="wr-seat' + (you ? " wr-seat-you" : "") + '">'
    + (you ? "" : remove)
    + '<div class="wr-seat-top"><div class="wr-seat-av"><div class="wr-seat-avbg"></div><img alt="">'
    + (you ? '<button class="wr-seat-edit">✏️</button>' : "") + "</div>"
    + '<div class="wr-seat-id"><div class="wr-seat-name"><span>' + name + "</span>"
    + '<span class="wr-seat-pbadge"><span class="cc-pbadge"><svg viewBox="0 0 16 16"></svg><span>3</span></span></span>'
    + "</div>"
    + '<div class="wr-seat-chips"><span class="wr-chip wr-chip-lvl">⭐ Level 47</span>'
    + (you ? '<span class="wr-chip wr-chip-host">👑 Host</span><span class="wr-chip wr-chip-you">You</span>'
           : '<span class="wr-chip wr-chip-ready">✓ In</span>')
    + "</div></div></div>"
    + (you ? '<div class="wr-seat-foot"><div class="wr-seat-hint">This is you. Tap your critter to change it.</div></div>' : "")
    + "</div>";
}

// The whole lobby, measured inside an IFRAME rather than by resizing the
// window. Headless Chrome refuses to give a window narrower than 500px, so a
// --window-size=390 run silently lays out at 500 and a phone-width check that
// looks green has never actually run at phone width. An iframe's document gets
// the width we ask for, so these numbers are real.
function page() {
  const tiles = [tile("you", 1), tile("player", 2), tile("player", 3), tile("bot", 4),
                 tile("bot", 5), tile("open", 6), tile("open", 7), tile("player", 8),
                 tile("add")].join("");
  const lobby = `<div id="pv-waiting-room" class="open" data-wide="1">
  <div class="wr-box">
    <h2 id="wr-title">Game Lobby</h2>
    <p class="wr-subtitle">Your crew can join from the Public games list.</p>
    <div class="wr-players" id="wr-players-list">
      <div class="wr-players-title">Players in Room</div>
      <div class="wr-seat-grid">${tiles}</div>
    </div>
    <p class="wr-caption" id="wr-caption">One seat is still empty. The game can't start until somebody sits in it, or the host turns it into a bot.</p>
    <button id="wr-start-btn"><span class="wr-btn-coral"></span><span class="wr-btn-text">Waiting for players...</span></button>
    <div class="wr-foot">
      <button type="button" id="wr-spectate-btn" style="display:inline-flex;">👁 Watch instead</button>
      <button id="wr-leave-btn">✕ Leave Room</button>
    </div>
  </div>
</div>`;
  const inner = `<!doctype html><html><head><meta charset="utf-8"><style>
${CSS}
html,body{margin:0;}
#pv-waiting-room{display:flex !important; position:static; min-height:100vh;}
</style></head><body>${lobby}</body></html>`;

  return `<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;background:#111;} iframe{border:0;display:block;}
</style></head><body>
<iframe id="f" width="1280" height="900"></iframe>
<div id="out"></div>
<script>
const SRC = ${JSON.stringify(inner)};
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
  ok(tiles.length === 9, "all nine tiles rendered (" + tiles.length + ")");
  tiles.forEach((el, i) => {
    const b = r(el);
    ok(b.width >= 130 && b.height >= 80, "tile " + i + " has real size (" + Math.round(b.width) + "x" + Math.round(b.height) + ")");
    ok(b.left >= -1 && b.right <= vw + 1, "tile " + i + " is inside the window");
  });

  const rem = [...d.querySelectorAll(".wr-seat-remove")];
  ok(rem.length === 7, "seven removable seats got a minus (" + rem.length + ")");
  rem.forEach((el, i) => {
    const b = r(el), st = win.getComputedStyle(el);
    ok(b.width >= 24 && b.height >= 24, "minus " + i + " is tappable (" + Math.round(b.width) + "x" + Math.round(b.height) + ")");
    ok(st.opacity === "1" && st.visibility === "visible" && st.display !== "none",
       "minus " + i + " is visible without hovering");
    ok(/gradient/.test(st.backgroundImage) || !/rgba\(0, 0, 0, 0\)/.test(st.backgroundColor),
       "minus " + i + " is painted, not a bare glyph");
    const tb = r(el.closest(".wr-seat"));
    ok(b.right <= tb.right + 1 && b.top >= tb.top - 1, "minus " + i + " sits inside its own tile");
  });
  ok(!d.querySelector(".wr-seat-you .wr-seat-remove"), "my own seat has no minus");
  ok(!d.querySelector(".wr-seat-add .wr-seat-remove"), "the add button has no minus");

  [...d.querySelectorAll(".wr-seat-name")].forEach((el, i) => {
    ok(r(el).right <= r(el.closest(".wr-seat")).right + 1, "name " + i + " stays inside its tile");
  });

  const pen = d.querySelector(".wr-seat-edit");
  ok(pen && r(pen).width >= 20 && r(pen).height >= 20, "the change-critter pencil is tappable");

  ["wr-spectate-btn", "wr-leave-btn", "wr-start-btn"].forEach(id => {
    const b = r(d.getElementById(id));
    ok(b.width >= 80 && b.height >= 26, id + " is tappable (" + Math.round(b.width) + "x" + Math.round(b.height) + ")");
    ok(b.right <= vw + 1, id + " is on screen");
  });
  // The Start button is a 920x175 painting: in the wide box it grows with its
  // container, and a 200px-tall banner is not a button.
  ok(r(d.getElementById("wr-start-btn")).height <= 150, "the Start button stays a button, not a banner");
  const box = r(d.querySelector(".wr-box"));
  ok(box.left >= -1 && box.right <= vw + 1, "the lobby box fits the window");
}

// Load the lobby ONCE, then walk the widths. Resizing an iframe re-lays-out
// its document synchronously, so there is nothing to wait for and no reload to
// race with the DOM dump.
f.onload = () => {
  WIDTHS.forEach(w => {
    f.width = String(w);
    f.contentWindow.document.body.offsetHeight;   // force the reflow
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
  console.log("\nlayout, phone through desktop");
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-lobby-"));
  const file = path.join(tmp, "lobby.html");
  fs.writeFileSync(file, page());
  let dom = "";
  try {
    dom = execFileSync(CHROME, [
      "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      "--window-size=1800,1000", "--virtual-time-budget=20000",
      "--dump-dom", "file://" + file,
    ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 120000,
         // The page carries the whole stylesheet inline and dumps the iframe's
         // document with it, so the default 1MB pipe is nowhere near enough.
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
