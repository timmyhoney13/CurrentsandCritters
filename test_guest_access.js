#!/usr/bin/env node
/* A guest gets the whole game, minus the saving. Driven in a real browser.
 *
 * Three changes are pinned here, all of them about what a person meets before
 * they have decided to trust this game with an email address:
 *
 *   1. NO "CHOOSE YOUR DEVICE" SCREEN. It is detected instead: a touch means
 *      mobile, a mouse means computer. The screen is gone from the DOM, and a
 *      real input event outranks the opening guess in both directions.
 *   2. EVERY TAB OPENS FOR A GUEST. There used to be ten locked panels out of
 *      thirteen, each one a padlock and a Sign In button, which is a wall in
 *      front of a free game. Now each panel renders its real content and the
 *      ones that cannot save carry one line saying so.
 *   3. A GUEST WEARS ANY CRITTER THEY HAVE NOT BOUGHT. The Avatar Gallery
 *      opens, the earnable critters are selectable, and the six PAID ones
 *      (4 shop + 2 donation-code) stay locked, because a purchase needs an
 *      account to belong to.
 *
 * The panels are not merely "not throwing": each one is checked for real
 * rendered content, because a panel that silently renders nothing looks exactly
 * like a panel that works until someone opens it. Console errors are captured
 * for the whole session and any error at all fails the run.
 *
 * Run:  node test_guest_access.js        (needs Google Chrome)
 */
"use strict";

const fs = require("fs");
const path = require("path");
const http = require("http");
const { execFileSync, spawn } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (detail != null ? "  [" + detail + "]" : "")); }
}

const APP  = read("js/preview-app.js");
const HTML = read("preview.html");
const CSS  = read("css/preview.css");
const DEV  = read("js/device-select.js");
const TUT  = read("js/tutorials.js");

// ══════════════════════════════════════════════════════════════════════════
// Source-level: the shapes that must be gone, and the rules that replaced them
// ══════════════════════════════════════════════════════════════════════════
console.log("\nthe device chooser is gone, not hidden");
check("no device screen in the page", !/id="cc-device-screen"/.test(HTML));
check("no device halves either", !/cc-device-half/.test(HTML));
check("its stylesheet block went with it", !/#cc-device-screen\s*\{/.test(CSS));
check("the baked artwork is no longer referenced", !/choose-device\.png/.test(HTML + CSS));
check("mobile MODE survives (it is what the touch shim keys off)",
      /body\.cc-device-mobile \[draggable="true"\]/.test(CSS));
check("the click shield is gone with the screen that needed it",
      !/cc-gate-shield/.test(DEV + HTML));
check("boot no longer waits on a human choice",
      /window\.ccDeviceReady = Promise\.resolve/.test(DEV));

console.log("\ntouch means mobile, a mouse means computer");
check("the decision reads the pointer type off the event",
      /pointerdown[\s\S]{0,220}pointerType/.test(DEV));
check("touch and pen both count as touch", /t === "touch" \|\| t === "pen"/.test(DEV));
check("a real input outranks the opening guess",
      /function sawInput\([\s\S]{0,400}apply\(device\)/.test(DEV));
check("the ghost mouse event a tap generates is ignored",
      /_lastTouchMs < GHOST_MS/.test(DEV));
check("a stated choice (ccSetDevice) is never overruled",
      /if \(readManual\(\)\) return;/.test(DEV));
check("the mode is never flipped mid-gesture",
      /_gestureOpen && !startsGesture/.test(DEV));
check("changing mode rebuilds the in-game viewport",
      /ccRefreshGameViewport/.test(DEV));
check("listeners are passive so they cannot eat game input",
      /capture: true, passive: true/.test(DEV));

console.log("\nthe guest padlock wall is gone");
check("no gated-tab table any more", !/GUEST_GATE_MSGS/.test(APP));
check("no panel-covering gate element", !/ph-guest-gate/.test(APP + CSS));
check("panels are no longer hidden behind a cover rule",
      !/is-guest-gated > \*/.test(CSS));
check("what replaced it is a note, not a door", /ph-guest-note/.test(APP) && /\.ph-guest-note \{/.test(CSS));
check("the note explains the saving, which is the real difference",
      /saved in this browser only/.test(APP));

console.log("\nguest avatars: everything except what you buy");
check("paid means shop or donation code, in one place",
      /const PAID_UNLOCK_TYPES = \["shop", "code"\];/.test(APP));
check("the rule is 'not paid', so new earnable critters open automatically",
      /return !isPaidAvatar\(n\);/.test(APP));
check("the gallery no longer refuses guests",
      !/Guests can't open the avatar collection/.test(APP));
check("nor does the picker", !/Guests cannot change their avatar/.test(APP));
check("a guest's pick persists to this browser", /localStorage\.setItem\(GUEST_AVATAR_KEY/.test(APP));
check("viewing someone else's collection still shows THEIR unlocks",
      /_isGuestSession\(\) && !_galReadOnly/.test(APP));
check("a new ACCOUNT still only gets what it owns",
      /_withOwnedOnly\(\(\) => sanitizeSelectableAvatar\(_signupAvatarUrl/.test(APP));
check("a returning guest is marked a guest before their avatar is validated",
      /if \(savedGuestNick\) _guestSessionActive = true;[\s\S]{0,320}savedGuestAvatar = sanitizeSelectableAvatar/.test(APP));

console.log("\nthe tutorial matches what a guest now sees");
check("the gallery steps are no longer skipped for guests",
      (TUT.match(/skipIf: tutIsGuest/g) || []).length === 1);
check("the one that remains is the friend code, which a guest has none of",
      /ph-fc-display[\s\S]{0,120}skipIf: tutIsGuest/.test(TUT));
check("the guest notice says what is actually different now",
      /the whole menu is open to you/.test(TUT) && /no saved profile|not get is a <strong>saved profile/.test(TUT));

if (!CHROME) {
  console.log("\nSKIP: no Chrome found, the live guest walkthrough did not run.");
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

// ══════════════════════════════════════════════════════════════════════════
// Live: boot the real app as a guest and walk the whole menu
// ══════════════════════════════════════════════════════════════════════════
const TABS = ["overview","howto","normal","competitive","history","friends",
              "messages","achievements","leaderboard","clans","prestige",
              "levelpass","store"];

// The six that cost money. Everything else in the catalogue is earnable.
const PAID_IDS = ["summer-skin-gull","summer-skin-hermit-crab","summer-skin-goby",
                  "fourth-of-july","amberjack","fish"];

const DRIVER = `
<script>
(function () {
  var out = { errors: [], tabs: {}, gallery: null, device: {}, phase: "boot" };
  window.addEventListener("error", function (e) {
    // The stack matters: this harness is the first thing that ever walks a
    // guest through every panel, so an error here is usually a panel that has
    // never been rendered without an account before.
    // NOTE: this whole block lives inside a JS template literal, so a
    // backslash-n written here (in code OR in a comment) becomes a REAL line
    // break in the generated page, which is a syntax error that silently kills
    // the entire driver and reports as "no result". fromCharCode(10) instead.
    var st = (e && e.error && e.error.stack)
      ? String(e.error.stack).split(String.fromCharCode(10)).slice(0, 4).join(" << ") : "";
    out.errors.push("window.onerror: " + (e && e.message) + (st ? "  @ " + st : ""));
  });
  window.addEventListener("unhandledrejection", function (e) {
    var r = e && e.reason;
    out.errors.push("unhandled: " + ((r && r.message) || String(r)));
  });
  var _err = console.error;
  console.error = function () {
    try {
      var s = Array.prototype.map.call(arguments, function (a) {
        return (a && a.message) ? a.message : String(a);
      }).join(" ");
      // Firestore/network noise is expected: there is no signed-in session and
      // no backend in this harness. Anything else is a real error.
      // Expected in this harness: no backend, no signed-in session, and the
      // card art / firebase-config live outside the served client directory.
      if (!/permission|network|firestore|Failed to fetch|ERR_|auth\\/|Quota|CONFIGURATION|resource_load_failed/i.test(s)) {
        out.errors.push("console.error: " + s.slice(0, 200));
      }
    } catch (_) {}
    return _err.apply(console, arguments);
  };

  function q(s) { return document.querySelector(s); }
  function vis(el) { return !!(el && el.offsetParent !== null); }
  // offsetParent is ALWAYS null for a position:fixed element, so the avatar
  // gallery (a fixed overlay) reads as hidden under vis() even when it is open.
  // Measure its box instead. Used only for the overlay; vis() is right for the
  // ordinary flow elements and is what the other harnesses use.
  function visBox(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    var cs = getComputedStyle(el);
    return cs.display !== "none" && cs.visibility !== "hidden" && Number(cs.opacity) > 0.01;
  }
  function click(el) { el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window })); }
  // The marker exists from the very first tick so a stall can be told apart
  // from a script that never ran, and it carries the live phase so a stall
  // says WHERE it stalled instead of just "no result".
  function mark() {
    var d = document.getElementById("out");
    if (!d) { d = document.createElement("div"); d.id = "out"; document.body.appendChild(d); }
    return d;
  }
  function finish() { mark().textContent = JSON.stringify(out); }
  function beat() { if (!out.done) mark().textContent = JSON.stringify({ stalled: out.phase, tick: out.tick, errors: out.errors.slice(0, 4) }); }

  // The device screen is checked immediately (it must never exist at all).
  // The FLAGS are set by a deferred script, so they are read on the first tick
  // instead: still before any input, which is the thing being tested.
  out.device.screenInDom = !!document.getElementById("cc-device-screen");
  var deviceRead = false;

  var phase = 1, tick = 0, tabIdx = 0, guard = 0;
  var iv = setInterval(function () {
    tick++;
    out.tick = tick; out.phase = "phase" + phase + ":tab" + tabIdx + ":g" + guard;
    if (tick % 20 === 0) beat();
    if (tick > 1400) { out.phase = "timeout:" + phase; finish(); clearInterval(iv); return; }
    try {
      if (!deviceRead && window.CC_DEVICE) {
        deviceRead = true;
        out.device.atBoot = window.CC_DEVICE || "";
        out.device.isComputerAtBoot = window.CC_IS_COMPUTER === true;
      }
      if (phase === 1) {
        if (tick > 25) {
          var ls = q("#auth-loading-screen"); if (ls) ls.classList.add("hidden");
          var as = q("#auth-screen"); if (as) as.classList.remove("hidden");
        }
        var g = q("#auth-guest-btn");
        if (g && vis(g)) { click(g); phase = 2; }
        return;
      }
      if (phase === 2) {
        var go = q("#auth-guest-go-btn"), nk = q("#auth-guest-nick");
        if (go && vis(go)) {
          if (nk) { nk.value = "GuestBot"; nk.dispatchEvent(new Event("input", { bubbles: true })); }
          click(go); phase = 3;
        }
        return;
      }
      if (phase === 3) {
        var lob = q("#auth-stats-lobby");
        if (lob && lob.classList.contains("visible")) { phase = 4; guard = 0; }
        return;
      }
      // ── Walk every tab ──────────────────────────────────────────────
      if (phase === 4) {
        var name = ${JSON.stringify(TABS)}[tabIdx];
        if (!name) { phase = 5; guard = 0; return; }
        var btn = document.querySelector('.ph-snav-item[data-tab="' + name + '"]');
        if (!btn) { out.tabs[name] = { missing: true }; tabIdx++; return; }
        if (guard === 0) { click(btn); guard = 1; return; }
        guard++;
        if (guard < 14) return;              // let async renders land
        var panel = document.getElementById("ph-panel-" + (name === "normal" ? "normal" : name));
        var gate = panel ? panel.querySelector(".ph-guest-gate") : null;
        var note = panel ? panel.querySelector(".ph-guest-note") : null;
        // "Real content" = what a person would actually see in the panel,
        // ignoring the guest note itself.
        var body = "";
        if (panel) {
          Array.prototype.forEach.call(panel.children, function (c) {
            if (!c.classList.contains("ph-guest-note")) body += (c.innerText || c.textContent || "");
          });
        }
        out.tabs[name] = {
          visible: vis(panel),
          hasGate: !!gate,
          hasNote: !!note && vis(note),
          noteText: note ? (note.innerText || "").slice(0, 90) : "",
          contentChars: body.replace(/\\s+/g, " ").trim().length,
        };
        tabIdx++; guard = 0;
        return;
      }
      // ── The Avatar Gallery ──────────────────────────────────────────
      if (phase === 5) {
        if (guard === 0) {
          var av = q("#stats-avatar");
          if (!av) { out.gallery = { missing: true }; phase = 6; return; }
          click(av); guard = 1; return;
        }
        guard++;
        if (guard < 16) return;
        var gal = q("#avatar-gallery");
        var open = !!gal && gal.classList.contains("open") && visBox(gal);
        var tiles = document.querySelectorAll("[data-avatar-id]");
        var locked = [], unlocked = [];
        Array.prototype.forEach.call(tiles, function (t) {
          var id = t.getAttribute("data-avatar-id");
          (t.classList.contains("gal-locked") ? locked : unlocked).push(id);
        });
        out.gallery = { open: open, tiles: tiles.length, locked: locked, unlocked: unlocked };
        phase = 6; guard = 0;
        return;
      }
      // ── Device detection from a real event ──────────────────────────
      if (phase === 6) {
        // A REAL TouchEvent, with a touches list, because that is what a phone
        // sends and the app's drag shim reads it. A bare Event("touchstart")
        // would test a shape that never occurs.
        var target = document.body;
        var ev;
        try {
          var touch = new Touch({ identifier: 1, target: target, clientX: 10, clientY: 10 });
          ev = new TouchEvent("touchstart", {
            bubbles: true, cancelable: true, touches: [touch],
            targetTouches: [touch], changedTouches: [touch],
          });
        } catch (_) {
          ev = new Event("touchstart", { bubbles: true });
        }
        target.dispatchEvent(ev);
        out.device.afterTouch = window.CC_DEVICE || "";
        out.device.bodyMobile = document.body.classList.contains("cc-device-mobile");
        target.dispatchEvent(new Event("touchend", { bubbles: true }));
        phase = 7; guard = 0;
        return;
      }
      if (phase === 7) {
        guard++;
        // The ghost-click guard means a mouse event right after a touch is
        // ignored; once it has expired a real mouse says computer again.
        if (guard < 25) return;
        var ev = new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window });
        document.dispatchEvent(ev);
        out.device.afterMouse = window.CC_DEVICE || "";
        out.phase = "done"; out.done = true;
        finish(); clearInterval(iv);
      }
    } catch (e) {
      out.errors.push("driver: " + (e && e.message));
      out.phase = "threw"; finish(); clearInterval(iv);
    }
  }, 60);
})();
</script>`;

const PORT = 8460 + (process.pid % 400);
const SERVER_SRC = `
  const fs=require("fs"),path=require("path"),http=require("http");
  const ROOT=${JSON.stringify(CLIENT)};
  const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",
    ".json":"application/json",".png":"image/png",".jpg":"image/jpeg",
    ".webp":"image/webp",".svg":"image/svg+xml",".ico":"image/x-icon"};
  http.createServer((req,res)=>{
    const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
    const f=path.join(ROOT,rel);
    if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);res.end();return;}
    res.writeHead(200,{"Content-Type":MIME[path.extname(f)]||"application/octet-stream"});
    fs.createReadStream(f).pipe(res);
  }).listen(${PORT});
`;

const tmpFiles = [];
function writeTmp(name, body) {
  const f = path.join(CLIENT, name);
  fs.writeFileSync(f, body);
  tmpFiles.push(f);
  return name;
}

const pageName = writeTmp("_guest_drive.html", HTML + DRIVER);
const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });
try { execFileSync(process.execPath, ["-e", "setTimeout(()=>{},700)"]); } catch (_) {}

let D = null;
try {
  for (let attempt = 0; attempt < 3 && !D; attempt++) {
    const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
      "--hide-scrollbars", "--window-size=1440,900", "--virtual-time-budget=240000",
      "--dump-dom", `http://localhost:${PORT}/${pageName}?game_window=1`],
      { encoding: "utf8", maxBuffer: 64e6, stdio: ["ignore", "pipe", "ignore"] });
    const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
    if (!m) { try { fs.writeFileSync("/tmp/_guest_dump.html", dom); } catch (_) {} continue; }
    const raw = m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                    .replace(/&lt;/g, "<").replace(/&gt;/g, ">");
    try { D = JSON.parse(raw); } catch (_) {}
  }
} finally {
  try { server.kill(); } catch (_) {}
  if (process.env.CC_KEEP_TMP) console.log("kept:", tmpFiles.join(" "));
  else for (const f of tmpFiles) { try { fs.unlinkSync(f); } catch (_) {} }
}

console.log("\nliving guest session (real app, real browser)");
if (!D) {
  check("the harness reached the lobby as a guest", false, "no result");
} else {
  check("the walkthrough ran to the end", D.phase === "done", D.phase);
  check("nothing threw anywhere in the session",
        (D.errors || []).length === 0, (D.errors || []).slice(0, 3).join(" | "));

  console.log("\n  the device was worked out, never asked");
  check("no device screen was ever in the DOM", D.device.screenInDom === false);
  check("a device was decided before anything rendered",
        D.device.atBoot === "computer" || D.device.atBoot === "mobile", D.device.atBoot);
  check("headless Chrome (a mouse machine) reads as computer",
        D.device.isComputerAtBoot === true, D.device.atBoot);
  check("one touch switches it to mobile", D.device.afterTouch === "mobile", D.device.afterTouch);
  check("…and turns on the touch drag shim", D.device.bodyMobile === true);
  check("a real mouse event switches it back", D.device.afterMouse === "computer", D.device.afterMouse);

  console.log("\n  every tab, as a guest");
  for (const t of TABS) {
    const r = D.tabs[t] || {};
    check(`${t}: opens`, r.visible === true, JSON.stringify(r));
    check(`${t}: is not behind a padlock`, r.hasGate === false);
    check(`${t}: has real content in it`, (r.contentChars || 0) > 40, r.contentChars);
  }

  console.log("\n  the panels that cannot save say so");
  for (const t of ["normal", "history", "achievements", "friends", "messages"]) {
    const r = D.tabs[t] || {};
    check(`${t}: carries one honest line`, r.hasNote === true, r.noteText);
  }

  console.log("\n  the Avatar Gallery, as a guest");
  const G = D.gallery || {};
  check("it opens at all", G.open === true, JSON.stringify(G).slice(0, 120));
  check("it is populated", (G.tiles || 0) > 20, G.tiles);
  const lockedSet = new Set(G.locked || []);
  const unlockedSet = new Set(G.unlocked || []);
  for (const id of PAID_IDS) {
    check(`${id} stays locked (it is bought, not earned)`,
          lockedSet.has(id), lockedSet.has(id) ? "" : "not locked");
  }
  check("earnable critters are wearable by a guest",
        (G.unlocked || []).length >= 20, (G.unlocked || []).length);
  check("the starter is among them", unlockedSet.has("mullet"));
  check("nothing paid slipped into the unlocked pile",
        !PAID_IDS.some(id => unlockedSet.has(id)),
        PAID_IDS.filter(id => unlockedSet.has(id)).join(", "));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
