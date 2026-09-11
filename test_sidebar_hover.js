#!/usr/bin/env node
/* Browser check for the TUCKED-AWAY left sidebar on Player Home.
 *
 * Run:  node test_sidebar_hover.js       (needs Google Chrome installed)
 *
 * On a computer with a mouse (≥1100px), the side menu sits off the left edge
 * until the cursor reaches for it, so every screen gets the full width. The
 * 16px strip at the edge (.ph-sidebar-edge) slides it out over the page; leaving
 * it slides it back; each tab steps out a little under the cursor. On a phone,
 * a tablet or a touch laptop none of that applies: the menu stays showing,
 * because a finger cannot hover and a hidden menu there is a lost menu.
 *
 * Driven with REAL mouse events over the Chrome DevTools Protocol (a :hover
 * rule cannot be checked any other way) against the REAL preview.css and the
 * REAL edge + sidebar markup sliced out of preview.html.
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

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (detail ? "  (" + detail + ")" : "")); }
}

// ── Source ──────────────────────────────────────────────────────────────
console.log("\nsource");
const EDGE_TAG = '<div class="ph-sidebar-edge" aria-hidden="true"></div>';
const SB_TAG   = '<div class="ph-sidebar" id="ph-sidebar">';
const SB_END   = '</div><!-- /.ph-sidebar -->';
const edgeAt = HTML.indexOf(EDGE_TAG), sbAt = HTML.indexOf(SB_TAG), endAt = HTML.indexOf(SB_END);
check("the edge strip is in preview.html", edgeAt >= 0);
// The reveal is `.ph-sidebar-edge:hover + .ph-sidebar`: anything between the
// two, even a comment-free empty div, silently breaks it.
check("...and is the element right before the sidebar",
      edgeAt >= 0 && sbAt > edgeAt && /^\s*$/.test(HTML.slice(edgeAt + EDGE_TAG.length, sbAt)));
check("the tutorial holds the menu out while a step points into it",
      /function coachHoldSidebar\(el\)/.test(TUT) && /classList\.toggle\("ph-sidebar-held"/.test(TUT));
check("...and lets go when the tour ends",
      /function endCoach\(\) \{\s*coachHoldSidebar\(null\);/.test(TUT));
check("the drawer is for a computer with a mouse only",
      /@media \(min-width: 1100px\) and \(hover: hover\) and \(pointer: fine\) \{\s*body\.cc-device-computer #auth-stats-lobby \.ph-sidebar-edge/.test(CSS));
check("a tab steps out only under a real mouse (not a sticky touch :hover)",
      /@media \(hover: hover\) and \(pointer: fine\) \{\s*\.ph-snav-item \{/.test(CSS));

if (edgeAt < 0 || sbAt < 0 || endAt < 0) {
  console.log(`\n${pass} passed, ${fail + 1} failed`);
  process.exit(1);
}
const MARKUP = HTML.slice(edgeAt, endAt + SB_END.length);

// ── Browser ─────────────────────────────────────────────────────────────
const page = (device) => `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sidebar hover</title>
<style>${CSS}</style>
<style>
  * { font-family: sans-serif !important; }
  html, body { margin: 0; padding: 0; height: 100%; }
  .ph-wrap { min-height: 1600px; }
</style>
</head><body class="cc-device-${device}">
<div id="auth-stats-lobby" class="visible" data-bg-tab="overview">
${MARKUP}
  <div class="ph-wrap"><button id="content-btn" style="margin:20px">content</button></div>
</div>
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
async function withPage(device, w, h, fn) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-sbhover-"));
  const file = path.join(tmp, "page.html");
  fs.writeFileSync(file, page(device));
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
      rect: (sel) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;var r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};})()`),
      mediaOk: () => ev("matchMedia('(hover: hover) and (pointer: fine)').matches"),
    };
    return await fn(api);
  } finally {
    try { ws && ws.close(); } catch (_) {}
    chrome.kill();
    await sleep(200);
    try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (_) {}
  }
}

// Long enough for the .2s close delay plus the .3s slide, with room to spare.
const SETTLE = 900;

(async () => {
  // ── A computer with a mouse ─────────────────────────────────────────
  for (const [w, h] of [[1440, 900], [1100, 800], [1920, 1080]]) {
    console.log(`\ncomputer with a mouse, ${w}x${h}`);
    await withPage("computer", w, h, async (p) => {
      if (!(await p.mediaOk())) {
        console.log("  SKIP: this Chrome does not report a hovering pointer, so the drawer is off here.");
        return;
      }
      await p.move(Math.round(w / 2), Math.round(h / 2));
      await sleep(SETTLE);
      let card = await p.rect(".ph-sidebar-nav-card");
      const wrap = await p.rect(".ph-wrap");
      const edge = await p.rect(".ph-sidebar-edge");
      check("closed: the whole menu, shadow and all, is off the left edge", card && card.right <= 0, card && `card right=${Math.round(card.right)}`);
      check("closed: the screen gets the full width", wrap && wrap.left <= 1 && wrap.width >= w - 1, wrap && `wrap ${Math.round(wrap.left)}+${Math.round(wrap.width)}`);
      check("closed: the edge strip runs the full height of the window", edge && edge.left === 0 && edge.width >= 12 && edge.height >= h - 1);
      const tab = await p.ev("(function(){var e=document.querySelector('.ph-sidebar-edge');var s=getComputedStyle(e,'::after');return {w:parseFloat(s.width),o:parseFloat(s.opacity)};})()");
      check("closed: the pull tab shows where the menu is", tab && tab.w > 0 && tab.o > 0.9);

      // Brushing past the edge on the way somewhere else must not open it.
      await p.move(4, Math.round(h / 2));
      await p.move(300, Math.round(h / 2));
      await sleep(SETTLE);
      card = await p.rect(".ph-sidebar-nav-card");
      check("a cursor that only brushes the edge does not throw it open", card && card.right <= 0, card && `card right=${Math.round(card.right)}`);

      await p.move(6, Math.round(h / 2));
      await sleep(SETTLE);
      card = await p.rect(".ph-sidebar-nav-card");
      check("hover the edge: the menu slides fully onto the screen", card && card.left >= 0 && card.left <= 30 && card.right <= 300, card && `card ${Math.round(card.left)}..${Math.round(card.right)}`);
      check("...at full height, top to bottom", card && card.top <= 30 && card.bottom >= h - 30, card && `card ${Math.round(card.top)}..${Math.round(card.bottom)}`);
      const hidden = await p.ev("parseFloat(getComputedStyle(document.querySelector('.ph-sidebar-edge'),'::after').opacity)");
      check("...and the pull tab gets out of the way", hidden !== undefined && hidden < 0.05, `opacity ${hidden}`);

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
      card = await p.rect(".ph-sidebar-nav-card");
      check("the menu stays out while you use it", card && card.left >= 0, card && `card left=${Math.round(card.left)}`);
      const row = await p.rect("#snav-history");
      check("a stepped-out tab still fits inside the white card", row && card && row.right <= card.right, row && card && `tab right=${Math.round(row.right)} card right=${Math.round(card.right)}`);

      // Slipping off for an instant must not make it flicker shut.
      await p.move(Math.round(card.right + 40), Math.round(h / 2));
      await sleep(60);
      await p.move(Math.round(card.right - 30), Math.round(h / 2));
      await sleep(SETTLE);
      card = await p.rect(".ph-sidebar-nav-card");
      check("a cursor that slips off for an instant does not shut it", card && card.left >= 0, card && `card left=${Math.round(card.left)}`);

      await p.move(Math.round(w / 2), Math.round(h / 2));
      await sleep(SETTLE);
      card = await p.rect(".ph-sidebar-nav-card");
      check("leave it: it slides back off the edge", card && card.right <= 0, card && `card right=${Math.round(card.right)}`);

      // Keyboard: Tab from the page into the menu opens it.
      await p.ev("(function(){var b=document.createElement('button');b.id='before-menu';b.textContent='x';var sb=document.getElementById('ph-sidebar');sb.parentNode.insertBefore(b,sb.previousElementSibling);b.focus();})()");
      await p.key("Tab", "Tab", 9);
      await sleep(SETTLE);
      const focused = await p.ev("document.activeElement && document.activeElement.id");
      card = await p.rect(".ph-sidebar-nav-card");
      check("tab into the menu with the keyboard: it opens", focused === "snav-overview" && card && card.left >= 0, `focus=${focused} card left=${card && Math.round(card.left)}`);
      await p.ev("document.activeElement.blur()");
      await sleep(SETTLE);

      // A mouse click that leaves a button focused must not pin it open.
      await p.move(6, Math.round(h / 2));
      await sleep(SETTLE);
      const c = await p.rect("#snav-friends");
      await p.move(Math.round(c.left + 30), Math.round(c.top + c.height / 2));
      await sleep(200);
      await p.click(Math.round(c.left + 30), Math.round(c.top + c.height / 2));
      const clicked = await p.ev("document.activeElement && document.activeElement.id");
      await p.move(Math.round(w / 2), Math.round(h / 2));
      await sleep(SETTLE);
      card = await p.rect(".ph-sidebar-nav-card");
      check("a clicked (mouse-focused) tab does not pin the menu open", clicked === "snav-friends" && card && card.right <= 0, `focus=${clicked} card right=${card && Math.round(card.right)}`);

      // The tutorial's hold.
      await p.ev("document.getElementById('ph-sidebar').classList.add('ph-sidebar-held')");
      await sleep(SETTLE);
      card = await p.rect(".ph-sidebar-nav-card");
      const how = await p.rect("#snav-howto");
      check("held by the tutorial: the menu is out with no cursor on it", card && card.left >= 0 && how && how.left >= 0 && how.right <= w, card && `card left=${Math.round(card.left)}`);
      await p.ev("document.getElementById('ph-sidebar').classList.remove('ph-sidebar-held')");
      await sleep(SETTLE);
      card = await p.rect(".ph-sidebar-nav-card");
      check("let go: it tucks away again", card && card.right <= 0, card && `card right=${Math.round(card.right)}`);

      // The streak block at the bottom has to be reachable in the drawer too:
      // the card is the scroller here, so scrolled to the end, the last button
      // is inside the card's painted box.
      await p.ev("document.getElementById('ph-sidebar').classList.add('ph-sidebar-held')");
      await sleep(SETTLE);
      await p.ev("(function(){var c=document.querySelector('.ph-sidebar-nav-card');c.scrollTop=c.scrollHeight;})()");
      await sleep(100);
      card = await p.rect(".ph-sidebar-nav-card");
      const btn = await p.rect("#ph-ss-details-btn");
      check("scrolled to the end, 'View streak details' is on the card and on screen",
            card && btn && btn.bottom <= card.bottom + 1 && btn.top >= card.top - 1 && btn.bottom <= h,
            btn && card && `button ${Math.round(btn.top)}..${Math.round(btn.bottom)} card ${Math.round(card.top)}..${Math.round(card.bottom)}`);
    });
  }

  // ── Touch: the menu stays out ───────────────────────────────────────
  for (const [label, w, h] of [["touch laptop / big tablet", 1440, 900], ["tablet", 820, 1100], ["phone", 390, 844]]) {
    console.log(`\n${label} (the app says mobile), ${w}x${h}`);
    await withPage("mobile", w, h, async (p) => {
      await p.move(Math.round(w / 2), Math.round(h / 2));
      await sleep(SETTLE);
      const card = await p.rect(".ph-sidebar-nav-card");
      const edge = await p.ev("getComputedStyle(document.querySelector('.ph-sidebar-edge')).display");
      check("the menu is on the screen without any hover", card && card.left >= 0 && card.right <= w && card.top < h, card && `card ${Math.round(card.left)}..${Math.round(card.right)}`);
      check("no edge strip, nothing tucked away", edge === "none", `display ${edge}`);
      const pos = await p.ev("getComputedStyle(document.getElementById('ph-sidebar')).position");
      check("the menu keeps its old place in the layout", pos === (w >= 1100 ? "sticky" : "static"), `position ${pos}`);
    });
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
