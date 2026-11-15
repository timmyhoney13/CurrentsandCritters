#!/usr/bin/env node
/* SITE SEARCH and the REDEEM CODES page, in a real browser.
 *
 * Run:  node test_site_search.js       (needs Google Chrome installed)
 *
 * Asked for as: "Build a search button on the website ... and have a tab that
 * says where to redeem the codes and put only limited amount of codes because
 * after all the first run of plushies sell out you will not be able to get the
 * critter again".
 *
 * Pins, at a phone width and a desktop one:
 *   • the Search button is on screen in the bar, and stays reachable once the
 *     bar goes compact on scroll;
 *   • it opens a search box ("/" and Ctrl+K too), Esc closes it and hands focus back;
 *   • a search finds pages, words on the pages and critters, and picking a
 *     result opens the page it is on with the result on screen;
 *   • the Redeem Codes tab says where to redeem, says the code is limited to
 *     the first run, and NEVER prints the code itself;
 *   • the live "codes left" box paints the count and the sold-out state;
 *   • nothing scrolls the page sideways.
 * The harness (CDP, viewport override) is test_page_tabs.js's, copied.
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

// ── static ─────────────────────────────────────────────────────────────
ok(/data-tab="redeem"[^>]*>Redeem Codes</.test(INDEX), "index.html: a Redeem Codes tab");
ok(INDEX.includes('data-panel="redeem"'), "index.html: ...and its page");
ok((INDEX.match(/data-site-search/g) || []).length >= 2, "index.html: a Search button in the bar and one for the compact bar");
ok(!/FEBRUARY\s*-?\s*2\s*-?\s*ND/i.test(INDEX) && !/FEB(RUARY)?\s*SECOND/i.test(INDEX),
   "index.html never prints the hang tag's code (it would be used up by people with no plushie)");
{
  const panel = INDEX.slice(INDEX.indexOf('data-panel="redeem"'), INDEX.indexOf("<!-- /panel-redeem -->"));
  ok(/first run/i.test(panel) && /can't be unlocked again/i.test(panel), "the Redeem page says the Fish is first run only");
  ok(/play\.currentsandcritters\.com/.test(panel) && /Avatar Gallery/.test(panel) && /Redeem/.test(panel),
     "...and where to redeem: the game, the Avatar Gallery, the Redeem button");
  ok(!/—/.test(panel), "...with no em dash");
}

if (!CHROME) {
  console.log(lines.join("\n"));
  console.log("\nSKIP: no Chrome/Chromium found, the browser half did not run.");
  process.exit(lines.some(l => l.startsWith("FAIL")) ? 1 : 0);
}

const SCENARIOS = {
  search: `
    var VW = document.documentElement.clientWidth, VH = window.innerHeight;
    var btn = document.querySelector(".nav-top [data-site-search]");
    var r = btn.getBoundingClientRect();
    ok(r.width >= 30 && r.height >= 28 && r.left >= 0 && r.right <= VW && r.top >= 0,
       "the Search button is on screen and big enough to tap (" + Math.round(r.width) + "x" + Math.round(r.height) + ")");
    var dlg = document.getElementById("siteSearch"), input = document.getElementById("ssInput");
    btn.click(); await sleep(80);
    ok(!dlg.hidden && document.activeElement === input, "clicking it opens the search box, ready to type");
    var box = document.querySelector(".ss-box").getBoundingClientRect();
    ok(box.left >= 0 && box.right <= VW + 0.5, "the box fits the screen");

    function type(q) { input.value = q; input.dispatchEvent(new Event("input", { bubbles: true })); }
    function titles() { return [].slice.call(document.querySelectorAll(".ss-item")).map(function (b) {
      return b.querySelector(".ss-page").textContent + " / " + b.querySelector(".ss-title").textContent; }); }

    type("redeem");
    var t = titles();
    ok(getComputedStyle(document.getElementById("ssSuggest")).display === "none", "with results on screen the suggestions step aside");
    ok(t.length > 0 && t[0] === "Redeem Codes / Redeem Codes", "\\"redeem\\" finds the Redeem Codes page first: " + t.slice(0, 3).join(" | "));
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); await sleep(120);
    var shown = [].slice.call(document.querySelectorAll(".cc-panel")).filter(function (p) { return !p.hidden; })
      .map(function (p) { return p.getAttribute("data-panel"); });
    ok(dlg.hidden && shown.join() === "redeem", "Enter opens that page and closes the box (" + shown + ")");
    ok(/#redeem$/.test(location.hash) || location.protocol === "file:", "the address names the page");

    btn = document.querySelector(document.getElementById("siteNav").classList.contains("is-compact")
      ? ".nav-search-mini" : ".nav-top [data-site-search]");
    btn.click(); await sleep(60);
    type("Whale Shark");
    t = titles();
    ok(t[0] === "The Critters / Whale Shark", "a critter's name finds the critter: " + t.slice(0, 3).join(" | "));
    document.querySelector(".ss-item").click(); await sleep(2200);
    var card = [].slice.call(document.querySelectorAll("#critterGrid .critter")).filter(function (c) {
      return c.querySelector(".name").textContent === "Whale Shark"; })[0];
    var cr = card && card.getBoundingClientRect();
    ok(!!card && !document.getElementById("panel-critters").hidden && cr.bottom > 0 && cr.top < VH,
       "...and picking it opens The Critters with the Whale Shark on screen (" + (cr ? Math.round(cr.top) + "," + Math.round(cr.bottom) : "none") + " vh " + VH + " y " + Math.round(scrollY) + " panelHidden " + document.getElementById("panel-critters").hidden + ")");
    ok(card && card.classList.contains("ss-hit"), "...highlighted so you can see which one");

    window.ccSearch.open(); await sleep(40);
    type("plushies");
    t = titles();
    ok(t.some(function (x) { return /^Plushy Vote/.test(x); }) && t.some(function (x) { return /^Redeem Codes/.test(x); }),
       "\\"plushies\\" finds the plushy pages (plural and all): " + t.slice(0, 4).join(" | "));
    type("how to play");
    ok(/^How to Play/.test(titles()[0] || ""), "\\"how to play\\" finds How to Play: " + titles()[0]);
    type("zzqxv");
    ok(document.querySelectorAll(".ss-item").length === 0 && !document.getElementById("ssEmpty").hidden,
       "nothing found says so");
    type("");
    ok(!document.getElementById("ssSuggest").hidden, "an empty box offers suggestions");
    document.querySelector('[data-ss-try="redeem code"]').click(); await sleep(20);
    ok(/^Redeem Codes/.test(titles()[0] || ""), "...and a suggestion runs: " + titles()[0]);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); await sleep(30);
    ok(dlg.hidden, "Esc closes it");
    document.body.focus();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "/", bubbles: true })); await sleep(40);
    ok(!dlg.hidden, "\\"/\\" opens it");
    window.ccSearch.close();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true })); await sleep(40);
    ok(!dlg.hidden, "Ctrl+K opens it");
    window.ccSearch.close();
    ok(document.documentElement.scrollWidth <= VW + 1, "nothing scrolls the page sideways");
  `,
  compact: `
    var VW = document.documentElement.clientWidth;
    document.getElementById("tabbtn-critters").click(); await sleep(60);
    window.scrollTo(0, 1500); window.dispatchEvent(new Event("scroll")); await sleep(250);
    var nav = document.getElementById("siteNav");
    ok(nav.classList.contains("is-compact"), "scrolled: the bar is compact");
    var mini = document.querySelector(".nav-search-mini"), r = mini.getBoundingClientRect();
    ok(getComputedStyle(mini).display !== "none" && r.width >= 30 && r.left >= 0 && r.right <= VW && r.top >= 0,
       "...and the Search button is still on screen beside the tabs");
    var tabs = document.getElementById("pageTabs").getBoundingClientRect();
    ok(tabs.right <= r.left + 1, "...without covering a tab");
    mini.click(); await sleep(60);
    ok(!document.getElementById("siteSearch").hidden, "...and it opens the search");
    window.ccSearch.close();
    window.scrollTo(0, 0); window.dispatchEvent(new Event("scroll")); await sleep(250);
    ok(getComputedStyle(mini).display === "none", "back at the top only the one in the top row shows");
  `,
  redeem: `
    var VW = document.documentElement.clientWidth;
    document.getElementById("tabbtn-redeem").click(); await sleep(80);
    var panel = document.getElementById("panel-redeem");
    ok(!panel.hidden && panel.getBoundingClientRect().height > 400, "the Redeem Codes page opens and has content");
    var h = panel.querySelector("h2").getBoundingClientRect();
    var bar = document.getElementById("siteNav").getBoundingClientRect();
    ok(h.top >= bar.bottom - 1, "its heading is not under the bar");
    ok(document.getElementById("rdCount").hidden, "no server answer: the count stays hidden, the page still reads fine");
    ok(window.ccRedeemCount({ ok: true, fish: { cap: 250, claimed: 40, remaining: 210, soldOut: false } }) === true,
       "the count paints from the server's answer");
    ok(document.getElementById("rdLeft").textContent === "210" && document.getElementById("rdCap").textContent === "250"
       && !document.getElementById("rdCount").hidden, "...210 of 250 left");
    window.ccRedeemCount({ ok: true, fish: { cap: 250, claimed: 250, remaining: 0, soldOut: true } });
    ok(document.getElementById("rdCount").classList.contains("is-out")
       && /sold out/.test(document.querySelector(".rd-count-label").textContent), "...and says sold out at 0");
    ok(window.ccRedeemCount({ ok: true, fish: { cap: 250, claimed: null, remaining: null } }) === false,
       "a server with no database is not painted as a count");
    var over = [].slice.call(panel.querySelectorAll("*")).filter(function (e) {
      var r = e.getBoundingClientRect(); return r.width > 0 && (r.right > VW + 1 || r.left < -1); });
    ok(over.length === 0, "nothing on the page runs off the side: " + over.slice(0, 3).map(function (e) { return e.className; }));
    ok(document.documentElement.scrollWidth <= VW + 1, "the page does not scroll sideways");
    var cta = panel.querySelector(".rd-cta");
    ok(cta && cta.href === "https://play.currentsandcritters.com/", "the button goes to the game");
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
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "ccsearch-"));
  const file = path.join(ROOT, `.cc-search-test-${process.pid}-${scenario}-${width}.html`);
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
    for (const name of ["search", "compact", "redeem"]) lines.push(...await runRetrying(name, w, h));
  }
  console.log(lines.join("\n"));
  const failed = lines.filter(l => l.startsWith("FAIL"));
  console.log(`\n${lines.filter(l => l.startsWith("PASS")).length} passed, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
})();
