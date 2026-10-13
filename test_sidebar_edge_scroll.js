#!/usr/bin/env node
/* Browser check for HOVER-TO-WALK on the Player Home side menu.
 *
 * Run:  node test_sidebar_edge_scroll.js      (needs Google Chrome installed)
 *
 * The tab list inside the side menu is its own scroller, and on any window
 * shorter than about 1000px it is taller than the card: Competitive, the passes
 * and Store sit below its bottom edge, and Overview is above the top one once
 * you are down there. Reaching them meant finding the wheel or the thin
 * scrollbar. Now resting the cursor near an edge walks the list that way on its
 * own, faster the nearer the edge, and the edge it can still travel towards is
 * faded so you can see there is more list.
 *
 * Driven with REAL mouse moves over the Chrome DevTools Protocol (nothing else
 * can test a hover) against the REAL preview.css, the REAL sidebar markup
 * sliced out of preview.html and the REAL walk code sliced out of
 * preview-app.js. What is checked:
 *   1. the list walks DOWN with the cursor at its bottom edge, and stops dead
 *      at the end instead of fighting the clamp,
 *   2. it walks back UP from the logo strip above it, all the way to Overview,
 *   3. the calm middle of the list does NOT drift,
 *   4. the wheel still scrolls it, exactly as before,
 *   5. the faded edge follows where the list can still go,
 *   6. a finger or a pen never walks it, only a mouse,
 *   7. the pin is a button, not an edge, and a menu shut under the cursor
 *      (.ph-sidebar-resting) does not walk,
 *   8. on a tall window, where the whole list fits, nothing walks and nothing
 *      is faded.
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
  console.log("SKIP: no Chrome/Chromium found: cannot run the sidebar walk check.");
  process.exit(0);
}

const CSS  = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");
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

const WALK_START = APP.indexOf("(function initSidebarEdgeScroll() {");
const WALK_END = WALK_START >= 0 ? APP.indexOf("\n      })();", WALK_START) : -1;
check("preview-app.js has the walk", WALK_START >= 0 && WALK_END > WALK_START);
check("the tab list is still the scroller in the rail",
      /body\.cc-device-computer #auth-stats-lobby \.ph-sidebar-nav \{[^}]*overflow-y: auto/.test(CSS));
check("preview.css fades the end the list can still travel towards",
      /\.ph-sidebar-nav\.cc-scroll-more-up,\s*\n\s*\.ph-sidebar-nav\.cc-scroll-more-down \{/.test(CSS)
        && /--cc-nav-fade-top: 20px/.test(CSS) && /--cc-nav-fade-bottom: 20px/.test(CSS));

if (sbAt < 0 || endAt < 0 || WALK_START < 0 || WALK_END < 0) {
  console.log(`\n${pass} passed, ${fail + 1} failed`);
  process.exit(1);
}
const MARKUP  = HTML.slice(sbAt, endAt + SB_END.length);
const WALK_JS = APP.slice(WALK_START, WALK_END + "\n      })();".length);

// ── Browser ─────────────────────────────────────────────────────────────
const page = () => `<!doctype html><html><head><meta charset="utf-8">
<title>sidebar walk</title>
<style>${CSS}</style>
<style>
  * { font-family: sans-serif !important; }
  html, body { margin: 0; padding: 0; height: 100%; }
  .ph-wrap { min-height: 1600px; }
</style>
</head><body class="cc-device-computer">
<div id="auth-stats-lobby" class="visible" data-bg-tab="overview">
${MARKUP}
  <div class="ph-wrap"><button id="content-btn">content</button></div>
</div>
<script>
  document.getElementById("ph-ss-days").textContent = "12 Days";
  // The menu is held open, the way the tutorial holds it: this test is about the
  // list inside the open menu, not about the rail opening (test_sidebar_hover.js
  // owns that), and a held menu is open with or without a cursor on it.
  document.getElementById("ph-sidebar").classList.add("ph-sidebar-held");
${WALK_JS}
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
async function withPage(w, h, fn) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-sbwalk-"));
  const file = path.join(tmp, "page.html");
  fs.writeFileSync(file, page());
  const port = 9760 + (process.pid % 200) + (_seq++);
  const chrome = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    `--user-data-dir=${path.join(tmp, "profile")}`, `--window-size=${w},${h}`,
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
    for (let i = 0; i < 40; i++) { if (await ev("document.readyState === 'complete' && !!document.querySelector('.ph-sidebar-nav')")) break; await sleep(150); }
    const api = {
      ev,
      move: (x, y) => send("Input.dispatchMouseEvent", { type: "mouseMoved", x: Math.round(x), y: Math.round(y) }),
      wheel: (x, y, dy) => send("Input.dispatchMouseEvent", { type: "mouseWheel", x: Math.round(x), y: Math.round(y), deltaX: 0, deltaY: dy }),
      rect: (sel) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;var r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};})()`),
      mediaOk: () => ev("matchMedia('(hover: hover) and (pointer: fine)').matches"),
      top: () => ev("Math.round(document.querySelector('.ph-sidebar-nav').scrollTop)"),
      room: () => ev("Math.round(document.querySelector('.ph-sidebar-nav').scrollHeight - document.querySelector('.ph-sidebar-nav').clientHeight)"),
      cues: () => ev("(function(){var n=document.querySelector('.ph-sidebar-nav');return (n.classList.contains('cc-scroll-more-up')?'up':'')+(n.classList.contains('cc-scroll-more-down')?'down':'');})()"),
      inView: (sel) => ev(`(function(){var n=document.querySelector('.ph-sidebar-nav'),e=document.querySelector(${JSON.stringify(sel)});var a=n.getBoundingClientRect(),b=e.getBoundingClientRect();return b.top>=a.top-1&&b.bottom<=a.bottom+1;})()`),
      // A pointer event of our own making, to prove the pointerType gate: a
      // finger and a pen must never walk the list, a mouse must.
      fake: (type, kind, x, y) => ev(`(function(){document.querySelector('.ph-sidebar-nav').dispatchEvent(new PointerEvent(${JSON.stringify(type)},{pointerType:${JSON.stringify(kind)},clientX:${Math.round(x)},clientY:${Math.round(y)},bubbles:true}));return true;})()`),
      setTop: (v) => ev(`(function(){document.querySelector('.ph-sidebar-nav').scrollTop=${v};return true;})()`),
      cls: (add, name) => ev(`(function(){document.getElementById('ph-sidebar').classList.${add ? "add" : "remove"}(${JSON.stringify(name)});return true;})()`),
    };
    return await fn(api);
  } finally {
    try { ws && ws.close(); } catch (_) {}
    chrome.kill();
    await sleep(200);
    try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (_) {}
  }
}

async function until(p, fn, ms) {
  const t0 = Date.now();
  for (;;) {
    if (await fn()) return true;
    if (Date.now() - t0 > ms) return false;
    await sleep(80);
  }
}

// Park the cursor well away from the menu and let the walk stop.
async function away(p, w, h) {
  await p.move(Math.round(w * 0.7), Math.round(h / 2));
  await sleep(200);
}

(async () => {
  // ── Short windows: the list is taller than the card ─────────────────
  for (const [w, h] of [[1440, 700], [1440, 860]]) {
    console.log(`\ncomputer with a mouse, ${w}x${h}`);
    await withPage(w, h, async (p) => {
      if (!(await p.mediaOk())) {
        console.log("  SKIP: this Chrome does not report a hovering pointer, so the rail is off here.");
        return;
      }
      await away(p, w, h);
      const nav = await p.rect(".ph-sidebar-nav");
      const room = await p.room();
      check("the tab list is the scroller and there is more of it than fits", room > 20,
            `${room}px past the bottom edge`);
      check("at the top, the bottom edge is faded and the top one is not",
            (await p.cues()) === "down", `cues=${await p.cues()}`);

      // 3. The calm middle does not drift.
      await p.move(nav.left + nav.width / 2, nav.top + nav.height / 2);
      await sleep(700);
      check("the cursor in the middle of the list moves nothing", (await p.top()) === 0,
            `scrollTop=${await p.top()}`);

      // 1. Down from the bottom edge.
      await p.move(nav.left + nav.width / 2, nav.bottom - 4);
      const walked = await until(p, async () => (await p.top()) > 30, 1500);
      check("rest the cursor at the bottom of the list: it walks down", walked,
            `scrollTop=${await p.top()}`);
      const ended = await until(p, async () => (await p.top()) >= room - 1, 4000);
      check("...and keeps going to the end of the list", ended, `scrollTop=${await p.top()} of ${room}`);
      check("...so Store, the last tab, is in view", await p.inView("#snav-store"));
      const atEnd = await p.top();
      await sleep(600);
      check("...and then stops dead there, cursor still on the edge",
            (await p.top()) === atEnd && atEnd === room, `scrollTop=${await p.top()} of ${room}`);
      check("...the faded edge has swapped to the top", (await p.cues()) === "up", `cues=${await p.cues()}`);

      // 2. Back up from the logo strip above the list.
      const logo = await p.rect(".ph-sidebar-logo");
      await p.move(logo.left + logo.width / 2, logo.top + logo.height / 2);
      const home = await until(p, async () => (await p.top()) === 0, 4000);
      check("reach up to the logo: it walks back to the top", home, `scrollTop=${await p.top()}`);
      check("...so Overview, the first tab, is in view", await p.inView("#snav-overview"));
      check("...and the fade is back on the bottom edge", (await p.cues()) === "down", `cues=${await p.cues()}`);

      // 4. The wheel still works, and the faded edges follow it.
      await away(p, w, h);
      await p.move(nav.left + nav.width / 2, nav.top + nav.height / 2);
      await p.wheel(nav.left + nav.width / 2, nav.top + nav.height / 2, 120);
      const wheeled = await until(p, async () => (await p.top()) > 0, 1200);
      check("the wheel still scrolls the list", wheeled, `scrollTop=${await p.top()}`);
      // One notch is the whole list on a taller window, so what is faded is
      // whatever the list can still reach from where the wheel left it.
      const after = await p.top(), cue = await p.cues();
      check("...and the faded edges follow where it landed",
            cue.includes("up") && (after >= room - 1 ? !cue.includes("down") : cue.includes("down")),
            `scrollTop=${after} of ${room} cues=${cue}`);

      // 6. A finger and a pen are not a mouse.
      await away(p, w, h);
      await p.setTop(0);
      for (const kind of ["touch", "pen"]) {
        await p.fake("pointermove", kind, nav.left + nav.width / 2, nav.bottom - 4);
        await sleep(500);
        check(`a ${kind} at the bottom edge walks nothing`, (await p.top()) === 0, `scrollTop=${await p.top()}`);
      }
      await p.fake("pointermove", "mouse", nav.left + nav.width / 2, nav.bottom - 4);
      const byMouse = await until(p, async () => (await p.top()) > 30, 1500);
      check("...the same spot with a mouse does walk it", byMouse, `scrollTop=${await p.top()}`);
      await p.fake("pointerleave", "mouse", nav.left + nav.width / 2, nav.bottom - 4);

      // 7. The pin is a button, not an edge.
      await away(p, w, h);
      await p.setTop(Math.round(room / 2));
      const mid = await p.top();
      const pin = await p.rect("#ph-sidebar-pin");
      await p.move(pin.left + pin.width / 2, pin.top + pin.height / 2);
      await sleep(700);
      check("hovering the pin does not walk the list", (await p.top()) === mid,
            `scrollTop=${await p.top()} was ${mid}`);

      // 7b. The menu shut under the cursor stays put.
      await away(p, w, h);
      await p.cls(false, "ph-sidebar-held");
      await p.cls(true, "ph-sidebar-resting");
      await p.setTop(0);
      await p.move(nav.left + nav.width / 2, nav.bottom - 4);
      await sleep(700);
      check("a menu let go of under the cursor (resting) does not walk", (await p.top()) === 0,
            `scrollTop=${await p.top()}`);
      await p.cls(false, "ph-sidebar-resting");
      await p.cls(true, "ph-sidebar-held");
    });
  }

  // ── A tall window: it all fits, so there is nothing to walk ─────────
  console.log("\ncomputer with a mouse, 1440x1200 (the whole list fits)");
  await withPage(1440, 1200, async (p) => {
    if (!(await p.mediaOk())) { console.log("  SKIP: no hovering pointer here."); return; }
    await away(p, 1440, 1200);
    const room = await p.room();
    check("nothing hangs below the bottom edge", room <= 2, `${room}px of overflow`);
    check("so neither edge is faded", (await p.cues()) === "", `cues=${await p.cues()}`);
    const nav = await p.rect(".ph-sidebar-nav");
    await p.move(nav.left + nav.width / 2, nav.bottom - 4);
    await sleep(700);
    check("and the bottom edge walks nothing", (await p.top()) === 0, `scrollTop=${await p.top()}`);
  });

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
