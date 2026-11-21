#!/usr/bin/env node
/* The Store tab, in a real browser.
 *
 * Run:  node test_store_page.js        (needs Google Chrome installed)
 *
 * The Store sits right of Home: the Whale Shark and Loggerhead Sea Turtle
 * plushies on top (Reservations open, "$44.99 + shipping and applicable sales
 * tax", a quantity stepper, a Reserve button to /preorder and a drag-to-spin
 * 360° view), then the four Critter Coins packs. Checks:
 *
 *   • the tab is the one right after Home, and opens its page;
 *   • every Reserve button is a REAL link (works with JavaScript off) to
 *     /preorder carrying its plushie, and every Buy button a real link to the
 *     right product on the Squarespace shop, opening in a new tab with
 *     rel=noopener; all on screen and big enough to tap;
 *   • the coin packs carry the same price ⇄ coins pairs as the game's Store
 *     (preview-app.js PHST_COIN_PACKS), since Squarespace grants by product;
 *   • the 360° viewer spins by drag (mouse and touch), the arrows, the view
 *     chips and the arrow keys, and only one photo is ever showing.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const http = require("http");
const path = require("path");
const { spawn } = require("child_process");

const ROOT = __dirname;
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

const INDEX = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");

const lines = [];
const ok = (cond, label) => lines.push((cond ? "PASS " : "FAIL ") + label);

const APP = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const SHOP = "https://www.beardedsealstudios.com/shop/p/";

// ── Static ──
const tabs = [...INDEX.matchAll(/id="tabbtn-([a-z]+)"/g)].map(m => m[1]);
ok(tabs[0] === "home" && tabs[1] === "store", "the Store tab is right of Home: " + tabs.slice(0, 3).join(", "));
ok(/data-tab="store"[^>]*>Store</.test(INDEX), "...and it is labelled Store");
const PANEL = INDEX.slice(INDEX.indexOf('data-panel="store"'), INDEX.indexOf("<!-- /panel-store -->"));
ok(PANEL.length > 1000, "the Store page is there and closed");
for (const m of PANEL.matchAll(/src="(assets\/[^"?]+)[^"]*"/g)) ok(fs.existsSync(path.join(ROOT, m[1])), "the Store's image " + m[1] + " exists");
ok(PANEL.indexOf("Whale Shark") < PANEL.indexOf("Critter Coins</h3>") &&
   PANEL.indexOf("Loggerhead Sea Turtle") < PANEL.indexOf("Critter Coins</h3>"),
   "the plushies are at the top, above the coins");
ok((PANEL.match(/Reservations open/g) || []).length === 2, "both plushies say Reservations open");
ok((PANEL.match(/<b>\$44\.99<\/b> \+ shipping and applicable sales tax/g) || []).length === 2,
   "both plushies say $44.99 + shipping and applicable sales tax");
ok(!/Price: <b>\$\?<\/b>/.test(PANEL), "no plushie still says Price: $?");
for (const id of ["whale-shark", "loggerhead-sea-turtle"]) {
  ok(new RegExp(`<a class="[^"]*st-preorder[^"]*" href="/preorder\\?add=${id}">Reserve`).test(PANEL),
     `Reserve is a real link to /preorder?add=${id}`);
  ok(new RegExp(`data-ccpo-qty="${id}"`).test(PANEL), `the ${id} card has a quantity stepper slot`);
}
ok(/data-cc-preorder="compact"/.test(PANEL), "the free-shipping bar and running total sit under the plushies");
ok((PANEL.match(/data-spin360/g) || []).length === 2, "both plushies have a 360° viewer");
ok((PANEL.match(/class="st-frame/g) || []).length === 10, "...with five photos each (front, left, back, right, top)");

const packs = [...APP.matchAll(/\{ usd: (\d+),\s+coins: (\d+),[^}]*link: "([^"]+)"/g)]
  .map(m => ({ usd: +m[1], coins: +m[2], link: m[3] }));
ok(packs.length === 4, "the game lists four coin packs: " + packs.length);
for (const p of packs) {
  const shown = p.coins.toLocaleString("en-US");
  const re = new RegExp(`st-pack-coins">${shown}<[\\s\\S]*?st-pack-price">\\$${p.usd}\\.00<[\\s\\S]*?href="${p.link.replace(/[.?]/g, "\\$&")}" target="_blank" rel="noopener"`);
  ok(re.test(PANEL), `${shown} coins for $${p.usd} links to ${p.link.split("/").pop()}, same as the game`);
}

if (!CHROME) {
  console.log(lines.join("\n"));
  console.log("\nSKIP: no Chrome/Chromium found, the browser half did not run.");
  process.exit(lines.some(l => l.startsWith("FAIL")) ? 1 : 0);
}

const SCENARIOS = {
  store: `
    document.getElementById("tabbtn-store").click();
    await sleep(80);
    var shown = [].slice.call(document.querySelectorAll(".cc-panel")).filter(function (p) { return !p.hidden; });
    ok(shown.length === 1 && shown[0].id === "panel-store", "the Store tab opens the Store page");
    var VW = document.documentElement.clientWidth;
    ok(document.documentElement.scrollWidth <= VW + 2, "the Store does not scroll sideways at " + VW + "px");

    var btns = [].slice.call(document.querySelectorAll("#panel-store .st-preorder, #panel-store .st-buy"));
    ok(btns.length === 6, "two Reserve and four Buy buttons: " + btns.length);
    var bad = [];
    btns.forEach(function (b) {
      b.scrollIntoView({ block: "center" });
      var r = b.getBoundingClientRect();
      var hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if (r.height < 40 || r.width < 120) bad.push(b.textContent.trim() + " is " + Math.round(r.width) + "x" + Math.round(r.height));
      if (!hit || !b.contains(hit)) bad.push(b.textContent.trim() + " is covered by " + (hit && hit.className));
      if (r.left < 0 || r.right > VW) bad.push(b.textContent.trim() + " is off screen");
    });
    ok(bad.length === 0, "every button is on screen, uncovered and big enough to tap: " + bad.join("; "));

    // A real click on Reserve must try to open /preorder and nothing
    // on the page may swallow it. window.open is how Chrome opens a
    // target=_blank link, so watch for the navigation instead of following it.
    var pre = document.querySelector("#panel-store .st-preorder");
    var clicked = null;
    document.addEventListener("click", function (e) { clicked = e; }, { once: true });
    pre.addEventListener("click", function (e) { e.preventDefault(); }, { once: true });
    pre.click();
    ok(clicked && clicked.target === pre, "a click on Reserve reaches the link");
    ok(window.ccTabs.current === "store", "...and does not switch the site to another page");
  `,

  spin: `
    document.getElementById("tabbtn-store").click();
    await sleep(80);
    var viewers = window.ccSpin360 || [];
    ok(viewers.length === 2, "two 360° viewers started: " + viewers.length);
    var v = viewers[0], box = v.box;
    box.scrollIntoView({ block: "center" });
    await sleep(50);
    function on() { return [].slice.call(box.querySelectorAll(".st-frame.is-on")).map(function (f) { return +f.getAttribute("data-view"); }); }
    function visibleOne() {
      var vis = [].slice.call(box.querySelectorAll(".st-frame")).filter(function (f) { return getComputedStyle(f).opacity !== "0" && f.classList.contains("is-on"); });
      return vis.length;
    }
    v.set(0);
    ok(on().join() === "0" && visibleOne() === 1, "starts on the front, one photo showing");

    box.querySelector(".st-next").click();
    ok(on().join() === "1", "the right arrow spins to the next side: " + on());
    box.querySelector(".st-prev").click(); box.querySelector(".st-prev").click();
    ok(on().join() === "3", "the left arrow spins back round the ring: " + on());

    box.focus();
    box.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    ok(on().join() === "0", "→ key spins on, wrapping right round to the front: " + on());
    box.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
    ok(on().join() === "4", "↑ key shows the top: " + on());
    box.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
    ok(on().join() === "0", "↓ key comes back to the front");

    var chips = [].slice.call(box.parentNode.querySelectorAll(".st-chip"));
    chips[2].click();
    ok(on().join() === "2" && chips[2].classList.contains("is-on") && chips[2].getAttribute("aria-pressed") === "true",
       "the Back chip shows the back and lights up");

    function drag(type, dx) {
      v.set(0);
      var r = box.getBoundingClientRect();
      var x = r.left + r.width / 2, y = r.top + r.height / 2;
      var opts = { bubbles: true, cancelable: true, pointerId: 7, pointerType: type, isPrimary: true, button: 0, buttons: 1 };
      box.dispatchEvent(new PointerEvent("pointerdown", Object.assign({}, opts, { clientX: x, clientY: y })));
      for (var i = 1; i <= 10; i++)
        box.dispatchEvent(new PointerEvent("pointermove", Object.assign({}, opts, { clientX: x + dx * i / 10, clientY: y })));
      box.dispatchEvent(new PointerEvent("pointerup", Object.assign({}, opts, { clientX: x + dx, clientY: y, buttons: 0 })));
      return on().join();
    }
    ok(drag("mouse", 40) === "1", "a short mouse drag right turns it one side");
    ok(drag("mouse", 80) === "2", "a longer drag turns it two");
    ok(drag("mouse", -40) === "3", "dragging left turns it the other way");
    ok(drag("touch", 160) === "0", "a finger drag of a full turn comes all the way round");
    var sharkT = getComputedStyle(viewers[0].box.querySelector(".st-frame")).transform;
    var turtleT = getComputedStyle(viewers[1].box.querySelector(".st-frame")).transform;
    ok(sharkT.indexOf("matrix(0.82, 0, 0, 0.82") === 0, "the whale shark is pulled back to 82% so it matches the turtle: " + sharkT);
    ok(turtleT === "none" || turtleT.indexOf("matrix(1, 0, 0, 1") === 0, "the turtle stays at full size: " + turtleT);
    ok(getComputedStyle(viewers[0].box).backgroundColor === "rgb(255, 255, 255)", "the viewer is plain white, so the smaller photo has no visible edge");
    ok(getComputedStyle(box).touchAction === "pan-y", "an up/down swipe on it still scrolls the page");

    // Hands off: the auto-spin stops the moment someone touches it.
    var at = on().join();
    await sleep(1700);
    ok(on().join() === at, "once used, it stays where it was left");
    var other = viewers[1];
    ok(!other.box.classList.contains("was-used"), "the turtle's viewer is independent of the shark's");
  `,
};

/* ══════════════════════════════════════════════════════════════════════
   HARNESS
   ══════════════════════════════════════════════════════════════════════ */
const sleep = ms => new Promise(r => setTimeout(r, ms));
const DEBUG_PORT_BASE = 9511 + (process.pid % 40) * 10;
let runSeq = 0;

function get(url, timeoutMs = 4000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, res => {
      let b = ""; res.on("data", d => (b += d)); res.on("end", () => resolve(b));
    });
    req.on("error", reject);
    req.setTimeout(timeoutMs, () => req.destroy(new Error("timeout")));
  });
}

async function waitFor(fn, ms, every = 200) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    try { const v = await fn(); if (v) return v; } catch (_) {}
    await sleep(every);
  }
  return null;
}

class Cdp {
  constructor(ws) { this.ws = ws; this.id = 0; this.waiting = new Map(); }
  static async open(wsUrl) {
    const ws = new WebSocket(wsUrl);
    const c = new Cdp(ws);
    ws.addEventListener("message", ev => {
      let m; try { m = JSON.parse(ev.data); } catch (_) { return; }
      const w = c.waiting.get(m.id);
      if (w) { c.waiting.delete(m.id); w(m); }
    });
    await new Promise((res, rej) => {
      ws.addEventListener("open", res, { once: true });
      ws.addEventListener("error", rej, { once: true });
      setTimeout(() => rej(new Error("CDP connect timed out")), 15000);
    });
    return c;
  }
  // Every call is bounded: one unanswered message must not hang the run.
  send(method, params, timeoutMs = 20000) {
    const id = ++this.id;
    return new Promise(resolve => {
      const t = setTimeout(() => { this.waiting.delete(id); resolve(null); }, timeoutMs);
      this.waiting.set(id, m => { clearTimeout(t); resolve(m); });
      try { this.ws.send(JSON.stringify({ id, method, params: params || {} })); }
      catch (_) { clearTimeout(t); this.waiting.delete(id); resolve(null); }
    });
  }
  async evalAsync(expr) {
    const r = await this.send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true }, 60000);
    if (!r || !r.result) return null;
    if (r.result.exceptionDetails) {
      return ["FAIL page exception: " + JSON.stringify(r.result.exceptionDetails.exception || {}).slice(0, 200)];
    }
    return r.result.result ? r.result.result.value : null;
  }
  close() { try { this.ws.close(); } catch (_) {} }
}

async function run(scenario, width, height) {
  // One debug port PER RUN, never reused: Chrome does not release a port the
  // instant it is killed, and /json/list would hand back the DYING browser's
  // target, whose Emulation overrides land on nothing.
  const port = DEBUG_PORT_BASE + (runSeq++);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "cctabs-"));
  const file = path.join(ROOT, `.cc-tabs-test-${process.pid}-${scenario}-${width}.html`);
  fs.writeFileSync(file, INDEX);

  const chrome = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    `--user-data-dir=${profile}`, `--window-size=${Math.max(width, 500)},${height}`,
    `--remote-debugging-port=${port}`, "file://" + file,
  ], { stdio: "ignore" });

  let cdp = null;
  try {
    const target = await waitFor(async () => {
      const list = JSON.parse(await get(`http://127.0.0.1:${port}/json/list`));
      return list.find(t => t.type === "page" && t.webSocketDebuggerUrl) || null;
    }, 25000);
    if (!target) return [`FAIL ${scenario}@${width}: Chrome never came up`];
    cdp = await Cdp.open(target.webSocketDebuggerUrl);
    await cdp.send("Runtime.enable");

    // /json/list answers as soon as there is a target, which is BEFORE the
    // page it was launched with has parsed. Wait for the real page, by the
    // thing this suite is about, not for a timer.
    const loaded = await waitFor(async () => {
      const r = await cdp.send("Runtime.evaluate", {
        expression: "document.readyState === 'complete' && !!window.ccTabs",
        returnByValue: true,
      });
      return r && r.result && r.result.result && r.result.result.value === true;
    }, 30000);
    if (!loaded) return [`FAIL ${scenario}@${width}: the page never finished loading`];

    // The override is applied AND verified: a page target still coming up
    // accepts the command and lays out at the old size anyway, and a run that
    // proceeds is silently testing the wrong width.
    let real = 0;
    for (let attempt = 0; attempt < 12; attempt++) {
      await cdp.send("Emulation.setDeviceMetricsOverride",
        { width, height, deviceScaleFactor: 1, mobile: false });
      const r = await cdp.send("Runtime.evaluate",
        { expression: "document.documentElement.clientWidth", returnByValue: true });
      real = (r && r.result && r.result.result && r.result.result.value) || 0;
      if (real === width) break;
      await sleep(200);
    }
    if (real !== width) {
      return [`FAIL ${scenario}: the viewport is ${real}px, not the ${width}px asked for`];
    }

    // The bar's height is measured on load and after the webfonts land, and
    // the first band's top padding comes off it. Measuring before that has
    // settled compares a laid-out heading against a bar that is still
    // growing, so give both the fonts and the resize handler their moment.
    await cdp.send("Runtime.evaluate", { expression: "window.dispatchEvent(new Event('resize'))" });
    await sleep(600);

    const out = await cdp.evalAsync(`(async () => {
      const results = [];
      const ok = (c, m) => results.push((c ? "PASS " : "FAIL ") + m);
      const sleep = ms => new Promise(r => setTimeout(r, ms));
      // scroll-behavior:smooth would have every measurement below answering
      // about where the page used to be.
      document.documentElement.style.scrollBehavior = "auto";
      try {
        ${SCENARIOS[scenario]}
      } catch (err) {
        results.push("FAIL exception in ${scenario}: " + (err && err.message || err));
      }
      return results;
    })()`);
    if (!Array.isArray(out) || !out.length) return [`FAIL ${scenario}@${width}: no output`];
    return out.map(l => l.replace(/^(PASS |FAIL )/, `$1${width}px: `));
  } catch (err) {
    return [`FAIL ${scenario}@${width}: ${err && err.message}`];
  } finally {
    if (cdp) cdp.close();
    try { chrome.kill("SIGKILL"); } catch (_) {}
    fs.rmSync(file, { force: true });
    // Chrome keeps writing into its profile for a moment after it is killed,
    // so a plain rmSync races it and throws ENOTEMPTY. A leftover temp profile
    // is not worth failing a run over either.
    await sleep(150);
    try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 150 }); }
    catch (_) {}
  }
}

// Chrome not coming up at all is a flake of the harness, not an answer about
// the page, and it has cost a clean run before. One retry, and only for that.
async function runRetrying(scenario, width, height) {
  let out = await run(scenario, width, height);
  if (out.some(l => /Chrome never came up|never finished loading/.test(l))) {
    await sleep(500);
    out = await run(scenario, width, height);
  }
  return out;
}


(async () => {
  for (const [w, h] of [[390, 844], [1280, 900]]) {
    lines.push(...await runRetrying("store", w, h));
  }
  lines.push(...await runRetrying("spin", 1280, 900));
  lines.push(...await runRetrying("spin", 390, 844));
  console.log(lines.join("\n"));
  const failed = lines.filter(l => l.startsWith("FAIL"));
  console.log(`\n${lines.filter(l => l.startsWith("PASS")).length} passed, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
})();
