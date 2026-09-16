#!/usr/bin/env node
/* Browser check for the ICON RAIL side menu on Player Home.
 *
 * Run:  node test_sidebar_hover.js       (needs Google Chrome installed)
 *
 * On a computer with a mouse (≥1100px), the side menu is a slim strip of icons
 * down the left edge. Reach for it and it widens into the full menu, and the
 * page slides right to make room: it never covers anything. Leave it and it
 * narrows back. The pin on its edge keeps it open. On a phone, a tablet or a
 * touch laptop none of that applies: the menu stays showing, because a finger
 * cannot hover.
 *
 * Driven with REAL mouse events over the Chrome DevTools Protocol (a :hover
 * rule cannot be checked any other way) against the REAL preview.css, the
 * REAL sidebar markup sliced out of preview.html and the REAL pin code sliced
 * out of preview-app.js.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const { spawn } = require("child_process");

const ROOT = __dirname;
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("SKIP: no Chrome/Chromium found: cannot run the sidebar hover check.");
  process.exit(0);
}

const CSS  = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");
const TUT  = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/tutorials.js"), "utf8");
const APP  = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (detail ? "  (" + detail + ")" : "")); }
}

// ── Source ──────────────────────────────────────────────────────────────
console.log("\nsource");
const SB_TAG = '<div class="ph-sidebar" id="ph-sidebar">';
const SB_END = '</div><!-- /.ph-sidebar -->';
const sbAt = HTML.indexOf(SB_TAG), endAt = HTML.indexOf(SB_END);
check("the sidebar is in preview.html", sbAt >= 0 && endAt > sbAt);
check("the old edge strip is gone", !/ph-sidebar-edge/.test(HTML) && !/ph-sidebar-edge/.test(CSS));
check("the pin is inside the sidebar",
      sbAt >= 0 && HTML.slice(sbAt, endAt).includes('id="ph-sidebar-pin"'));
check("the tutorial holds the menu open while a step points into it",
      /function coachHoldSidebar\(el\)/.test(TUT) && /classList\.toggle\("ph-sidebar-held"/.test(TUT));
check("...and lets go when the tour ends",
      /function endCoach\(\) \{\s*coachHoldSidebar\(null\);/.test(TUT));
check("...and re-aims once the rail has finished sliding the page",
      /e\.target\.id === "ph-sidebar" && e\.propertyName === "width"\) positionCoach\(\)/.test(TUT));
check("the rail is for a computer with a mouse only",
      /@media \(min-width: 1100px\) and \(hover: hover\) and \(pointer: fine\) \{\s*body\.cc-device-computer #auth-stats-lobby \{\s*--ph-rail-w: 94px;/.test(CSS));
check("a tab steps out only under a real mouse (not a sticky touch :hover)",
      /@media \(hover: hover\) and \(pointer: fine\) \{\s*\.ph-snav-item \{/.test(CSS));

const PIN_START = APP.indexOf("(function initSidebarPin() {");
const PIN_END = PIN_START >= 0 ? APP.indexOf("\n      })();", PIN_START) : -1;
check("preview-app.js wires the pin", PIN_START >= 0 && PIN_END > PIN_START);

if (sbAt < 0 || endAt < 0 || PIN_START < 0 || PIN_END < 0) {
  console.log(`\n${pass} passed, ${fail + 1} failed`);
  process.exit(1);
}
const MARKUP = HTML.slice(sbAt, endAt + SB_END.length);
const PIN_JS = APP.slice(PIN_START, PIN_END + "\n      })();".length);

// ── Browser ─────────────────────────────────────────────────────────────
const page = (device, pinned) => `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sidebar rail</title>
<style>${CSS}</style>
<style>
  * { font-family: sans-serif !important; }
  html, body { margin: 0; padding: 0; height: 100%; }
  .ph-wrap { min-height: 1600px; }
  #content-row { display: flex; gap: 10px; }
  #content-row div { flex: 1; height: 60px; background: #cde; }
</style>
</head><body class="cc-device-${device}">
<div id="auth-stats-lobby" class="visible" data-bg-tab="overview">
${MARKUP}
  <div class="ph-wrap"><button id="content-btn">content</button><div id="content-row"><div></div><div></div><div></div><div></div></div></div>
</div>
<script>
  try { localStorage.setItem("cc_sidebar_pinned", ${pinned ? '"1"' : '"0"'}); } catch (_) {}
  // Two counts showing, the way preview-app.js shows them.
  document.getElementById("snav-friend-badge").textContent = "2";
  var m = document.getElementById("msg-unread-badge"); m.textContent = "3"; m.style.display = "";
  document.getElementById("ph-ss-days").textContent = "12 Days";
  ${PIN_JS}
</script>
</body></html>`;

const sleep = ms => new Promise(r => setTimeout(r, ms));
function get(url) {
  return new Promise((resolve, reject) => {
    const r = http.get(url, res => { let b = ""; res.on("data", d => b += d); res.on("end", () => resolve(b)); });
    r.on("error", reject);
    r.setTimeout(3000, () => r.destroy(new Error("timeout")));
  });
}

let _seq = 0;
async function withPage(device, w, h, fn, pinned) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-sbhover-"));
  const file = path.join(tmp, "page.html");
  fs.writeFileSync(file, page(device, pinned));
  const port = 9420 + (process.pid % 300) + (_seq++);
  const chrome = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    `--user-data-dir=${path.join(tmp, "profile")}`, `--window-size=${Math.max(w, 500)},${h}`,
    `--remote-debugging-port=${port}`, "about:blank",
  ], { stdio: "ignore" });
  let ws = null;
  try {
    let target = null;
    for (let i = 0; i < 80 && !target; i++) {
      try { target = JSON.parse(await get(`http://127.0.0.1:${port}/json/list`)).find(t => t.type === "page" && t.webSocketDebuggerUrl); } catch (_) {}
      if (!target) await sleep(250);
    }
    if (!target) throw new Error("Chrome never offered a page to drive");
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.addEventListener("open", res, { once: true }); ws.addEventListener("error", rej, { once: true }); });
    let id = 0; const waiting = new Map();
    ws.addEventListener("message", ev => { const m = JSON.parse(ev.data); const cb = waiting.get(m.id); if (cb) { waiting.delete(m.id); cb(m); } });
    const send = (method, params = {}) => new Promise(resolve => {
      const i = ++id;
      const t = setTimeout(() => { waiting.delete(i); resolve(null); }, 15000);
      waiting.set(i, m => { clearTimeout(t); resolve(m); });
      ws.send(JSON.stringify({ id: i, method, params }));
    });
    const ev = async (expr) => {
      const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
      return r && r.result && r.result.result ? r.result.result.value : undefined;
    };
    await send("Page.enable");
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url: "file://" + file });
    for (let i = 0; i < 40; i++) { if (await ev("document.readyState === 'complete' && !!document.getElementById('ph-sidebar')")) break; await sleep(150); }
    const rectJs = (sel) => `(function(){var e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;var r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};})()`;
    const api = {
      ev,
      move: (x, y) => send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y }),
      click: async (x, y) => {
        await send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
        await send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
      },
      key: (key, code, keyCode) => Promise.all([
        send("Input.dispatchKeyEvent", { type: "keyDown", key, code, windowsVirtualKeyCode: keyCode }),
        send("Input.dispatchKeyEvent", { type: "keyUp", key, code, windowsVirtualKeyCode: keyCode }),
      ]),
      rect: (sel) => ev(rectJs(sel)),
      mediaOk: () => ev("matchMedia('(hover: hover) and (pointer: fine)').matches"),
      // Alpha of an element's computed text colour (0 = the words are hidden).
      alpha: (sel, pseudo) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});var c=getComputedStyle(e${pseudo ? ",'" + pseudo + "'" : ""}).color;if(c==='transparent')return 0;var m=c.match(/rgba?\\(([^)]+)\\)/);if(!m)return -1;var p=m[1].split(/[ ,\\/]+/).filter(Boolean);return p.length>3?parseFloat(p[3]):1;})()`),
      padLeft: () => ev("parseFloat(getComputedStyle(document.querySelector('.ph-wrap')).paddingLeft)"),
      iconCenters: () => ev("[...document.querySelectorAll('.ph-snav-item svg')].map(function(s){var r=s.getBoundingClientRect();return Math.round(r.left+r.width/2)+','+Math.round(r.top+r.height/2)})"),
      noSideScroll: () => ev("document.documentElement.scrollWidth <= innerWidth"),
    };
    return await fn(api);
  } finally {
    try { ws && ws.close(); } catch (_) {}
    chrome.kill();
    await sleep(200);
    try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (_) {}
  }
}

// Longer than the .2s close delay plus the .4s slide, with room to spare.
const SETTLE = 1000;
const RAIL = 94, OPEN = 240;

async function isClosed(p, label) {
  const card = await p.rect(".ph-sidebar-nav-card");
  check(label, card && Math.abs(card.right - RAIL) <= 1, card && `card right=${Math.round(card.right)}`);
  return card;
}
async function isOpen(p, label) {
  const card = await p.rect(".ph-sidebar-nav-card");
  check(label, card && Math.abs(card.right - OPEN) <= 1, card && `card right=${Math.round(card.right)}`);
  return card;
}

(async () => {
  // ── A computer with a mouse ─────────────────────────────────────────
  for (const [w, h] of [[1440, 900], [1100, 700], [1920, 1080]]) {
    console.log(`\ncomputer with a mouse, ${w}x${h}`);
    await withPage("computer", w, h, async (p) => {
      if (!(await p.mediaOk())) {
        console.log("  SKIP: this Chrome does not report a hovering pointer, so the rail is off here.");
        return;
      }
      await p.move(Math.round(w / 2), Math.round(h / 2));
      await sleep(SETTLE);

      // Closed: the rail.
      let card = await isClosed(p, "closed: the rail is a 76px card on the left edge");
      check("...at full height, top to bottom", card && card.left >= 16 && card.top <= 30 && card.bottom >= h - 30,
            card && `card ${Math.round(card.left)},${Math.round(card.top)}..${Math.round(card.bottom)}`);
      let btn = await p.rect("#content-btn");
      check("closed: the page starts clear of the rail", btn && btn.left >= card.right + 16, btn && `content left=${Math.round(btn.left)}`);
      check("closed: no sideways scroll", await p.noSideScroll());
      check("closed: tab words are hidden", (await p.alpha("#snav-history")) === 0);
      check("closed: ...the icons are not", (await p.alpha("#snav-history svg")) > 0.9);
      const closedIcons = await p.iconCenters();
      const ic = closedIcons && closedIcons[0].split(",").map(Number);
      check("closed: each icon sits dead centre in the rail", ic && Math.abs(ic[0] - (card.left + card.width / 2)) <= 1,
            ic && `icon x=${ic[0]} card centre=${card.left + card.width / 2}`);
      check("closed: the logo shows CandC, not the full name",
            (await p.alpha(".ph-sidebar-logo")) === 0 && (await p.ev("parseFloat(getComputedStyle(document.querySelector('.ph-sidebar-logo'),'::after').opacity)")) > 0.9);
      const dot = await p.rect("#msg-unread-badge");
      const msgIcon = await p.rect("#snav-messages svg");
      check("closed: a count is a small red dot on its icon",
            dot && msgIcon && dot.width <= 12 && dot.height <= 12 && dot.left >= msgIcon.left && dot.right <= card.right && dot.top < msgIcon.top + 6,
            dot && `dot ${Math.round(dot.left)},${Math.round(dot.top)} ${Math.round(dot.width)}x${Math.round(dot.height)}`);
      const star = await p.rect(".ph-ss-star-img");
      const days = await p.rect("#ph-ss-days");
      check("closed: the streak star and day count sit inside the rail",
            star && days && star.left >= card.left && star.right <= card.right && days.left >= card.left && days.right <= card.right,
            star && days && `star ${Math.round(star.left)}..${Math.round(star.right)} days ${Math.round(days.left)}..${Math.round(days.right)}`);
      check("closed: the rest of the streak block is hidden and cannot be clicked",
            (await p.ev("getComputedStyle(document.getElementById('ph-ss-details-btn')).visibility")) === "hidden");
      check("closed: no pin on the rail (it would run from the cursor as the menu opened)",
            (await p.ev("getComputedStyle(document.getElementById('ph-sidebar-pin')).visibility")) === "hidden");

      // Brushing past the rail on the way somewhere else must not open it.
      await p.move(40, Math.round(h / 2));
      await p.move(Math.round(w / 2), Math.round(h / 2));
      await sleep(SETTLE);
      await isClosed(p, "a cursor that only brushes the rail does not throw it open");

      // Open. The cursor goes onto the logo, not a tab, so no tab is stepped
      // out when the icons are compared below.
      const logo = await p.rect(".ph-sidebar-logo");
      await p.move(40, Math.round(logo.top + logo.height / 2));
      await sleep(SETTLE);
      card = await isOpen(p, "hover the rail: it widens to the full 222px menu");
      btn = await p.rect("#content-btn");
      check("...and the page slides right instead of being covered", btn && btn.left >= card.right + 16, btn && `content left=${Math.round(btn.left)} card right=${Math.round(card.right)}`);
      check("...with no sideways scroll", await p.noSideScroll());
      check("...the words fade in", (await p.alpha("#snav-history")) > 0.9);
      check("...the full logo is back", (await p.alpha(".ph-sidebar-logo")) > 0.9);
      check("...and it reads Currents and Critters, spelled out",
            (await p.ev("document.querySelector('.ph-sidebar-logo').textContent.trim()")) === "Currents and Critters");
      const openIcons = await p.iconCenters();
      check("...and not one icon moved, across or down", JSON.stringify(openIcons) === JSON.stringify(closedIcons),
            closedIcons && openIcons && closedIcons.map((c, i) => c === openIcons[i] ? null : `#${i} ${c} -> ${openIcons[i]}`).filter(Boolean).join("; "));
      const pill = await p.rect("#msg-unread-badge");
      const msgRow = await p.rect("#snav-messages");
      check("...the dot is a count again, at the end of its tab",
            pill && msgRow && pill.height >= 16 && pill.right <= msgRow.right && pill.right >= msgRow.right - 16,
            pill && `badge ${Math.round(pill.left)}..${Math.round(pill.right)} ${Math.round(pill.height)}h`);
      const openPin = await p.rect("#ph-sidebar-pin");
      check("...the pin shows on the open menu's edge",
            (await p.ev("getComputedStyle(document.getElementById('ph-sidebar-pin')).visibility")) === "visible"
              && openPin && openPin.left < card.right && openPin.right > card.right,
            openPin && `pin ${Math.round(openPin.left)}..${Math.round(openPin.right)}`);
      check("...and the whole streak block shows",
            (await p.ev("getComputedStyle(document.getElementById('ph-ss-details-btn')).visibility")) === "visible");

      // Onto a tab: it steps out to meet the cursor, and the menu stays open.
      const t = await p.rect("#snav-history");
      await p.move(Math.round(t.left + 40), Math.round(t.top + t.height / 2));
      await sleep(500);
      const moved = await p.ev("(function(){var e=document.getElementById('snav-history');return new DOMMatrixReadOnly(getComputedStyle(e).transform).m41;})()");
      check("hover a tab: it steps out", moved >= 6 && moved <= 12, `translateX ${moved}`);
      const still = await p.ev("(function(){var e=document.getElementById('snav-overview');return new DOMMatrixReadOnly(getComputedStyle(e).transform).m41;})()");
      check("...and only that one", still === 0, `overview translateX ${still}`);
      const bar = await p.ev("(function(){var e=document.getElementById('snav-history');return new DOMMatrixReadOnly(getComputedStyle(e,'::before').transform).d;})()");
      check("...with its bar lit", bar > 0.9, `scaleY ${bar}`);
      card = await isOpen(p, "the menu stays open while you use it");
      const row = await p.rect("#snav-history");
      check("a stepped-out tab still fits inside the white card", row && card && row.right <= card.right, row && card && `tab right=${Math.round(row.right)} card right=${Math.round(card.right)}`);

      // Slipping off for an instant must not make it flicker shut.
      await p.move(Math.round(card.right + 60), Math.round(h / 2));
      await sleep(60);
      await p.move(Math.round(card.right - 30), Math.round(h / 2));
      await sleep(SETTLE);
      await isOpen(p, "a cursor that slips off for an instant does not shut it");

      await p.move(Math.round(w / 2), Math.round(h / 2));
      await sleep(SETTLE);
      await isClosed(p, "leave it: it narrows back to the rail");
      check("...and the page slides back", Math.abs((await p.padLeft()) - (RAIL + 22)) <= 1, `padding-left ${await p.padLeft()}`);
      check("...and the words are hidden again", (await p.alpha("#snav-history")) === 0);

      // Keyboard: Tab from the page into the menu opens it.
      await p.ev("(function(){var b=document.createElement('button');b.id='before-menu';b.textContent='x';var sb=document.getElementById('ph-sidebar');sb.parentNode.insertBefore(b,sb);b.focus();})()");
      await p.key("Tab", "Tab", 9);
      await sleep(SETTLE);
      const focused = await p.ev("document.activeElement && document.activeElement.id");
      card = await p.rect(".ph-sidebar-nav-card");
      check("tab into the menu with the keyboard: it opens", focused === "snav-overview" && card && Math.abs(card.right - OPEN) <= 1, `focus=${focused} card right=${card && Math.round(card.right)}`);
      await p.ev("document.activeElement.blur()");
      await sleep(SETTLE);

      // A mouse click that leaves a button focused must not pin it open.
      await p.move(40, Math.round(h / 2));
      await sleep(SETTLE);
      const c = await p.rect("#snav-friends");
      await p.move(Math.round(c.left + 30), Math.round(c.top + c.height / 2));
      await sleep(200);
      await p.click(Math.round(c.left + 30), Math.round(c.top + c.height / 2));
      const clicked = await p.ev("document.activeElement && document.activeElement.id");
      await p.move(Math.round(w / 2), Math.round(h / 2));
      await sleep(SETTLE);
      card = await p.rect(".ph-sidebar-nav-card");
      check("a clicked (mouse-focused) tab does not pin the menu open", clicked === "snav-friends" && card && Math.abs(card.right - RAIL) <= 1, `focus=${clicked} card right=${card && Math.round(card.right)}`);

      // The pin.
      await p.move(40, Math.round(h / 2));
      await sleep(SETTLE);
      let pr = await p.rect("#ph-sidebar-pin");
      await p.move(Math.round(pr.left + pr.width / 2), Math.round(pr.top + pr.height / 2));
      await sleep(100);
      await p.click(Math.round(pr.left + pr.width / 2), Math.round(pr.top + pr.height / 2));
      await p.move(Math.round(w / 2), Math.round(h / 2));
      await sleep(SETTLE);
      await isOpen(p, "pin it: the menu stays open with the cursor gone");
      check("...the pin says so", (await p.ev("document.getElementById('ph-sidebar-pin').getAttribute('aria-pressed')")) === "true");
      check("...and this device remembers it", (await p.ev("localStorage.getItem('cc_sidebar_pinned')")) === "1");
      pr = await p.rect("#ph-sidebar-pin");
      check("...the pin is still on the open menu's edge", pr && pr.left < OPEN && pr.right > OPEN, pr && `pin ${Math.round(pr.left)}..${Math.round(pr.right)}`);
      await p.move(Math.round(pr.left + pr.width / 2), Math.round(pr.top + pr.height / 2));
      await sleep(100);
      await p.click(Math.round(pr.left + pr.width / 2), Math.round(pr.top + pr.height / 2));
      await sleep(SETTLE);
      await isClosed(p, "unpin it: the menu closes straight away, cursor still on it");
      check("...and remembers that", (await p.ev("localStorage.getItem('cc_sidebar_pinned')")) === "0");
      await p.move(Math.round(w / 2), Math.round(h / 2));
      await sleep(300);
      await p.move(40, Math.round(h / 2));
      await sleep(SETTLE);
      await isOpen(p, "...and hovering opens it again once the cursor has left and come back");
      await p.move(Math.round(w / 2), Math.round(h / 2));
      await sleep(SETTLE);

      // The tutorial's hold.
      await p.ev("document.getElementById('ph-sidebar').classList.add('ph-sidebar-held')");
      await sleep(SETTLE);
      card = await isOpen(p, "held by the tutorial: the menu is open with no cursor on it");
      const how = await p.rect("#snav-howto");
      check("...with the tab it points at fully in view", how && how.left >= 0 && how.right <= card.right);
      await p.ev("document.getElementById('ph-sidebar').classList.remove('ph-sidebar-held')");
      await sleep(SETTLE);
      await isClosed(p, "let go: it narrows back to the rail");

      // The streak block at the bottom has to be reachable: the card is the
      // scroller here, so scrolled to the end, the last button is inside the
      // card's painted box.
      await p.ev("document.getElementById('ph-sidebar').classList.add('ph-sidebar-held')");
      await sleep(SETTLE);
      await p.ev("(function(){var c=document.querySelector('.ph-sidebar-nav-card');c.scrollTop=c.scrollHeight;})()");
      await sleep(100);
      card = await p.rect(".ph-sidebar-nav-card");
      const sbtn = await p.rect("#ph-ss-details-btn");
      check("scrolled to the end, 'View streak details' is on the card and on screen",
            card && sbtn && sbtn.bottom <= card.bottom + 1 && sbtn.top >= card.top - 1 && sbtn.bottom <= h && sbtn.right <= card.right,
            sbtn && card && `button ${Math.round(sbtn.top)}..${Math.round(sbtn.bottom)} card ${Math.round(card.top)}..${Math.round(card.bottom)}`);
    });
  }

  // ── Pinned on a previous visit ──────────────────────────────────────
  console.log("\ncomputer with a mouse, pinned last time, 1440x900");
  await withPage("computer", 1440, 900, async (p) => {
    if (!(await p.mediaOk())) { console.log("  SKIP: no hovering pointer here."); return; }
    await p.move(900, 450);
    await sleep(SETTLE);
    await isOpen(p, "the menu opens already pinned");
    check("...and the pin shows it", (await p.ev("document.getElementById('ph-sidebar-pin').getAttribute('aria-pressed')")) === "true");
  }, true);

  // ── Touch: the menu stays out ───────────────────────────────────────
  for (const [label, w, h] of [["touch laptop / big tablet", 1440, 900], ["tablet", 820, 1100], ["phone", 390, 844]]) {
    console.log(`\n${label} (the app says mobile), ${w}x${h}`);
    await withPage("mobile", w, h, async (p) => {
      await p.move(Math.round(w / 2), Math.round(h / 2));
      await sleep(SETTLE);
      const card = await p.rect(".ph-sidebar-nav-card");
      check("the menu is on the screen without any hover", card && card.left >= 0 && card.right <= w && card.top < h, card && `card ${Math.round(card.left)}..${Math.round(card.right)}`);
      check("the words show", (await p.alpha("#snav-history")) > 0.9);
      const pin = await p.ev("getComputedStyle(document.getElementById('ph-sidebar-pin')).display");
      check("no pin", pin === "none", `display ${pin}`);
      const pos = await p.ev("getComputedStyle(document.getElementById('ph-sidebar')).position");
      check("the menu keeps its old place in the layout", pos === (w >= 1100 ? "sticky" : "static"), `position ${pos}`);
    });
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
