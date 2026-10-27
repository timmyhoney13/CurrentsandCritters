#!/usr/bin/env node
/* The home page is a set of TABS, in a real browser.
 *
 * Run:  node test_page_tabs.js        (needs Google Chrome installed)
 *
 * index.html used to be one scroll about twelve thousand pixels long: the
 * hero, the sand band, how to play, Snap & Score, the whole cast of critters,
 * the plushy ballot, the oceans and the supporter tiers, all stacked. Each
 * band is its own page now, and one bar at the top switches between them.
 *
 * Two things can go wrong with that, and both are silent:
 *
 *   1. A PAGE NOBODY CAN REACH. The tab bar is the only way between them, so
 *      a button that does not light up, a row that scrolls off a phone with
 *      no way back, or a panel left hidden when its tab is lit, takes a whole
 *      band of the site off the air with nothing in the console to say so.
 *   2. A LINK THAT NOW POINTS INTO A HIDDEN PAGE. Every #hash on this site
 *      used to be a scroll position: the footer's #critters, the hero's
 *      "Learn the Rules", the tier card's "Email us a custom amount", and
 *      launch.html / shop.html / score.html all linking to index.html#how.
 *      None of them were rewritten, because the page answers a #hash by
 *      opening whichever tab holds that element. If that stops working the
 *      links do not break loudly, they just land on the wrong page, or on a
 *      page showing nothing at all.
 *
 * So this drives the REAL index.html in headless Chrome and checks, at a
 * phone width and a desktop one:
 *
 *   • every tab opens its own page, and exactly one page is on screen;
 *   • the bar is readable and tappable, on screen, and never scrolls the
 *     page sideways;
 *   • the open tab's pill is in view in a row that scrolls;
 *   • arriving at #how / #critters / #partner-form opens the right page;
 *   • every in-page link on the site lands on a band that is really visible;
 *   • the bar stays on screen once the page is scrolled.
 *
 * WHY CDP AND NOT --window-size: headless Chrome clamps its window to a 500px
 * minimum width, so --window-size=390,844 quietly lays the page out at 500 and
 * every "phone" assertion is really a second narrow-laptop assertion.
 * Emulation.setDeviceMetricsOverride sets the layout viewport below that floor
 * (the same reason, and the same fix, as test_partner_form.js).
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

/* ══════════════════════════════════════════════════════════════════════
   Static checks: the markup itself, before any browser.
   ══════════════════════════════════════════════════════════════════════ */

// The pages, in the order the bar names them. Every one of these is a band
// that used to be a stop on the scroll.
const TABS = ["home", "how", "snap", "critters", "plushy", "oceans", "sponsor", "partner"];

for (const t of TABS) {
  ok(INDEX.includes(`data-panel="${t}"`), `index.html: there is a page for ${t}`);
  ok(INDEX.includes(`data-tab="${t}"`), `index.html: ...and a tab button for it`);
}
ok((INDEX.match(/class="cc-panel"/g) || []).length === TABS.length,
   `index.html: ${TABS.length} pages and no more`);
ok((INDEX.match(/<\/div><!-- \/panel-/g) || []).length === TABS.length,
   "index.html: every page is closed");

// role="tablist" / role="tab" / role="tabpanel" is how a screen reader is
// told this is a set of pages rather than eight unrelated buttons.
ok(/role="tablist"/.test(INDEX), "index.html: the bar is a tablist");
ok((INDEX.match(/role="tab"/g) || []).length === TABS.length,
   "index.html: every button is a tab");
ok((INDEX.match(/role="tabpanel"/g) || []).length === TABS.length,
   "index.html: every page is a tabpanel");

// The bar must not be able to hide itself. The old nav did, on scroll-down,
// which is fine for a nav you use twice and wrong for the thing holding
// the pages: it would scroll the way between pages off the screen.
ok(!/nav\.classList\.add\('nav-hidden'\)/.test(INDEX),
   "index.html: the bar no longer hides itself on scroll-down");

// Under 900px styles.css hides .nav .links outright. The tab row is a
// different element for exactly that reason, and if it ever picks up that
// class name again a phone loses its navigation completely.
ok(!/class="nav-tabs[^"]*\blinks\b/.test(INDEX),
   "index.html: the tab row is not .links, which is display:none on a phone");

if (!CHROME) {
  console.log(lines.join("\n"));
  console.log("\nSKIP: no Chrome/Chromium found, the browser half did not run.");
  process.exit(lines.some(l => l.startsWith("FAIL")) ? 1 : 0);
}

/* ══════════════════════════════════════════════════════════════════════
   The scenarios. Each is the body of an async function with ok(), sleep()
   and the real page in scope. They run against a copy of index.html placed
   in the project ROOT, so every relative asset (styles.css, the card art,
   the fonts) resolves exactly as it does in production: a copy in /tmp
   would load with no stylesheet at all, and a layout assertion against no
   stylesheet asserts nothing.
   ══════════════════════════════════════════════════════════════════════ */
const TABS_JS = JSON.stringify(TABS);

const SCENARIOS = {
  /* One page at a time, and the lit tab is the page you got. */
  switching: `
    var NAMES = ${TABS_JS};
    var bad = [];
    for (var i = 0; i < NAMES.length; i++) {
      var name = NAMES[i];
      document.getElementById("tabbtn-" + name).click();
      await sleep(60);
      var shown = [].slice.call(document.querySelectorAll(".cc-panel"))
        .filter(function (p) { return !p.hidden; })
        .map(function (p) { return p.getAttribute("data-panel"); });
      var lit = [].slice.call(document.querySelectorAll(".ptab.is-on"))
        .map(function (b) { return b.getAttribute("data-tab"); });
      if (shown.length !== 1 || shown[0] !== name) bad.push(name + " showed [" + shown + "]");
      if (lit.length !== 1 || lit[0] !== name) bad.push(name + " lit [" + lit + "]");
    }
    ok(bad.length === 0, "each tab opens its own page and only that one: " + bad.join("; "));

    // aria-selected is the only thing a screen reader has to go on, and it is
    // easy to set on the new tab without clearing it on the old one.
    var sel = [].slice.call(document.querySelectorAll('.ptab[aria-selected="true"]'));
    ok(sel.length === 1 && sel[0].getAttribute("data-tab") === "partner",
       "exactly one tab is marked selected: " + sel.map(function (b) { return b.getAttribute("data-tab"); }));

    // A page change starts at the top of the new page. Landing halfway down
    // a page you have never seen reads as a broken link.
    document.getElementById("tabbtn-critters").click();
    await sleep(60);
    window.scrollTo(0, 1200);
    await sleep(60);
    document.getElementById("tabbtn-oceans").click();
    await sleep(120);
    ok(window.scrollY < 4, "switching pages starts the new one at its top (" + Math.round(window.scrollY) + ")");

    // Every page must actually have something on it: an empty panel is what
    // a mis-placed closing tag looks like from the outside.
    var empty = [];
    for (var j = 0; j < NAMES.length; j++) {
      document.getElementById("tabbtn-" + NAMES[j]).click();
      await sleep(50);
      var panel = document.querySelector('[data-panel="' + NAMES[j] + '"]');
      if (panel.getBoundingClientRect().height < 200) empty.push(NAMES[j]);
    }
    ok(empty.length === 0, "no page is empty: " + empty.join(", "));
  `,

  /* The buttons themselves: visible, readable, big enough for a thumb, and
     on screen. This is the half the whole redesign is judged on. */
  bar: `
    var VW = document.documentElement.clientWidth;
    var bar = document.getElementById("pageTabs");
    var nav = document.getElementById("siteNav");
    var r = nav.getBoundingClientRect();
    ok(r.width > 0 && r.height > 0, "the bar is on the page");
    ok(r.left >= -2 && r.right <= VW + 2,
       "the bar fits the " + VW + "px page (" + Math.round(r.left) + "…" + Math.round(r.right) + ")");
    ok(document.documentElement.scrollWidth <= VW + 2,
       "the page does not scroll sideways at " + VW + "px (" +
       document.documentElement.scrollWidth + ")");

    var pills = [].slice.call(document.querySelectorAll(".ptab"));
    ok(pills.length === ${TABS.length}, "all ${TABS.length} buttons are there: " + pills.length);

    var small = [], blank = [];
    pills.forEach(function (b) {
      var pr = b.getBoundingClientRect();
      var cs = getComputedStyle(b);
      if (pr.height < 30 || pr.width < 40) small.push(b.textContent.trim() + " is " + Math.round(pr.width) + "x" + Math.round(pr.height));
      if (cs.display === "none" || cs.visibility === "hidden" || !b.textContent.trim()) blank.push(b.id);
    });
    ok(small.length === 0, "every button is big enough to tap: " + small.join(", "));
    ok(blank.length === 0, "every button is visible and labelled: " + blank.join(", "));

    // The open one has to LOOK open. Same colour as the rest is the failure
    // this catches: the bar still works and nothing says where you are.
    var on = document.querySelector(".ptab.is-on");
    var off = pills.find(function (b) { return !b.classList.contains("is-on"); });
    var onBg = getComputedStyle(on).backgroundImage + "|" + getComputedStyle(on).backgroundColor;
    var offBg = getComputedStyle(off).backgroundImage + "|" + getComputedStyle(off).backgroundColor;
    ok(onBg !== offBg, "the open tab is painted differently from the rest");
    ok(getComputedStyle(on).color !== getComputedStyle(off).color,
       "...and its label is a different colour too");

    // In a row that scrolls, the open tab must be IN the part you can see.
    // Opening a page and being shown a bar that does not include it is the
    // phone-sized version of losing your place.
    var cut = [];
    for (var i = 0; i < pills.length; i++) {
      pills[i].click();
      await sleep(80);
      var pr = pills[i].getBoundingClientRect(), br = bar.getBoundingClientRect();
      if (pr.left < br.left - 1 || pr.right > br.right + 1) {
        cut.push(pills[i].textContent.trim() + " at " + Math.round(pr.left) + "…" + Math.round(pr.right) +
                 " in " + Math.round(br.left) + "…" + Math.round(br.right));
      }
    }
    ok(cut.length === 0, "the open tab is always in view in the row: " + cut.join("; "));

    // Nothing may sit on top of the buttons. elementFromPoint answers in
    // viewport coordinates, and the bar is fixed, so no scrolling is needed.
    //
    // Only the pills CURRENTLY IN the row's visible box are asked about. On a
    // phone the row scrolls, so the ones scrolled off the end sit at negative
    // viewport coordinates where elementFromPoint answers null for everything
    // and every pill looks "covered by nothing". The row is put back to each
    // end in turn, because what is reachable differs between them.
    var covered = [];
    [0, 99999].forEach(function (at) {
      bar.scrollLeft = at;
      var br = bar.getBoundingClientRect();
      pills.forEach(function (b) {
        var pr = b.getBoundingClientRect();
        if (pr.width < 1) return;
        var cx = pr.left + pr.width / 2, cy = pr.top + pr.height / 2;
        if (cx < br.left || cx > br.right) return;          // scrolled out of the row
        var top = document.elementFromPoint(cx, cy);
        if (top !== b && !b.contains(top)) {
          covered.push(b.textContent.trim() + " under " + (top ? (top.id || top.className || top.tagName) : "nothing"));
        }
      });
    });
    bar.scrollLeft = 0;
    ok(covered.length === 0, "every button can actually be tapped: " + covered.join(", "));

    // The first band of a page has to clear the bar it sits under. Its
    // heading disappearing behind the bar is the classic fixed-header bug.
    var missed = [];
    ${TABS_JS}.forEach(function (name) {
      document.getElementById("tabbtn-" + name).click();
      var panel = document.querySelector('[data-panel="' + name + '"]');
      var head = panel.querySelector("h1, h2");
      if (!head) return;
      var hr = head.getBoundingClientRect(), nr = nav.getBoundingClientRect();
      if (hr.top < nr.bottom) missed.push(name + " (heading at " + Math.round(hr.top) + ", bar ends " + Math.round(nr.bottom) + ")");
    });
    ok(missed.length === 0, "no page's first heading starts under the bar: " + missed.join(", "));
  `,

  /* The bar does not leave once you start reading. */
  scrolled: `
    var nav = document.getElementById("siteNav");
    document.getElementById("tabbtn-critters").click();   // the long one
    await sleep(80);
    window.scrollTo(0, 2000);
    await sleep(300);
    var r = nav.getBoundingClientRect();
    var cs = getComputedStyle(nav);
    ok(r.height > 0 && r.top >= -1 && r.top < 80 && cs.opacity === "1",
       "the bar is still on screen 2000px down (top " + Math.round(r.top) + ", opacity " + cs.opacity + ")");
    var pills = document.querySelectorAll(".ptab");
    var gone = [].slice.call(pills).filter(function (b) { return b.getBoundingClientRect().height < 20; });
    ok(gone.length === 0, "...and so is every button on it: " + gone.length + " missing");

    // Scrolled back up, the brand and the email button come back.
    window.scrollTo(0, 0);
    await sleep(300);
    var top = document.querySelector(".nav-top").getBoundingClientRect();
    ok(top.height > 0, "at the top of a page the whole bar is back");
  `,

  /* Every #hash on the site, from wherever it is written. */
  links: `
    function visible(el) {
      if (!el) return false;
      var r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    }

    // 1. Arriving at a link somebody shared, or one from another page of the
    //    site (launch.html, shop.html and score.html all link in here).
    //    The third field is whether the target should have a box of its own.
    //    #game-night is an empty shell until /js/game-night.js fills it, and
    //    this suite runs the page off file://, where an absolute /js path
    //    cannot resolve. Which page it opens is still worth checking: it is
    //    the case of an id that is NOT the page's own name.
    var arrivals = [
      ["#how", "how", true], ["#critters", "critters", true], ["#oceans", "oceans", true],
      ["#plushy", "plushy", true], ["#snap", "snap", true], ["#sponsor", "sponsor", true],
      ["#home", "home", true], ["#partner-form", "partner", true], ["#people-care", "home", true],
      ["#game-night", "home", false], ["#partner", "partner", true]
    ];
    var wrong = [];
    for (var i = 0; i < arrivals.length; i++) {
      window.ccTabs.open("snap");                 // somewhere else first
      await sleep(40);
      window.ccTabs.openFor(arrivals[i][0], true);
      await sleep(120);
      if (window.ccTabs.current !== arrivals[i][1]) {
        wrong.push(arrivals[i][0] + " opened " + window.ccTabs.current + ", wanted " + arrivals[i][1]);
      }
      var target = document.getElementById(arrivals[i][0].slice(1));
      if (arrivals[i][2] && !visible(target)) wrong.push(arrivals[i][0] + " landed on something hidden");
    }
    ok(wrong.length === 0, "every shared link opens the page that holds it: " + wrong.join("; "));

    // 2. Every in-page link written anywhere on the page, clicked for real.
    //    This is the check that would have caught the tier card's "Email us a
    //    custom amount", which now has to cross from Support Us to Partner.
    var hrefs = [].slice.call(document.querySelectorAll('a[href^="#"]'))
      .map(function (a) { return a.getAttribute("href"); })
      .filter(function (h) { return h && h !== "#"; });
    var uniq = hrefs.filter(function (h, i) { return hrefs.indexOf(h) === i; });
    ok(uniq.length >= 4, "the page still has in-page links to check: " + uniq.join(" "));

    var dead = [];
    for (var j = 0; j < uniq.length; j++) {
      var href = uniq[j];
      window.ccTabs.open("home");
      await sleep(40);
      // Find the link again each time: opening a page hides the one before.
      var link = null;
      var all = document.querySelectorAll('a[href="' + href + '"]');
      for (var k = 0; k < all.length; k++) { link = all[k]; if (visible(link)) break; }
      if (!link) { dead.push(href + " is on no page"); continue; }
      link.click();
      await sleep(250);
      var el = document.getElementById(href.slice(1));
      if (!el) { dead.push(href + " points at nothing"); continue; }
      if (!visible(el)) dead.push(href + " landed on a hidden band");
    }
    ok(dead.length === 0, "every in-page link lands somewhere you can see: " + dead.join("; "));

    // 3. The tier card's door into the form, which crosses pages now.
    window.ccTabs.open("sponsor");
    await sleep(60);
    var custom = document.querySelector('[data-tier-enquiry="custom"]');
    ok(!!custom && visible(custom), "the over-$100 card's way in is on the Support Us page");
    custom.click();
    await sleep(500);
    ok(window.ccTabs.current === "partner", "...and it crosses to the Partner page: " + window.ccTabs.current);
    ok(visible(document.getElementById("partner-form")), "...with the form really on screen");
    ok(document.getElementById("pf-kind").value === "major",
       "...still carrying the over-$100 partnership type: " + document.getElementById("pf-kind").value);

    // 4. "Skip to the main content". It is the first thing a keyboard reaches,
    //    and it used to point at #home, which is the name of a PAGE now:
    //    skipping the bar would have carried the reader off whatever they
    //    were reading and onto the front page.
    window.ccTabs.open("oceans");
    await sleep(60);
    var skip = document.querySelector(".skip-link");
    ok(!!skip, "the skip link is still there");
    skip.click();
    await sleep(150);
    ok(window.ccTabs.current === "oceans",
       "skipping the bar leaves you on the page you were reading: " + window.ccTabs.current);
    ok(document.activeElement && document.activeElement.id === "ccTabs",
       "...with the focus in the content: " + (document.activeElement && document.activeElement.id));
    ok(location.hash === "#oceans",
       "...and the address still names that page, so a reload brings it back: " + location.hash);

    // 5. The keyboard. A tablist that only answers a mouse is half a control.
    window.ccTabs.open("home");
    await sleep(60);
    var first = document.getElementById("tabbtn-home");
    first.focus();
    first.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    await sleep(120);
    ok(window.ccTabs.current === "how", "right arrow moves to the next page: " + window.ccTabs.current);
    document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    await sleep(120);
    ok(window.ccTabs.current === "home", "left arrow moves back: " + window.ccTabs.current);
    document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true }));
    await sleep(120);
    ok(window.ccTabs.current === "partner", "End goes to the last page: " + window.ccTabs.current);
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
  // A phone and a desktop. The bar is two rows and scrolls sideways on one
  // and a single row on the other, and the interesting failures are at the
  // narrow end: eight pills do not fit a 390px screen by any arrangement.
  for (const [w, h] of [[390, 844], [1280, 900]]) {
    for (const name of ["bar", "switching"]) {
      lines.push(...await runRetrying(name, w, h));
    }
  }
  // These are about behaviour, not width, so once each is enough.
  lines.push(...await runRetrying("links", 1280, 900));
  lines.push(...await runRetrying("scrolled", 390, 844));
  lines.push(...await runRetrying("scrolled", 1280, 900));

  console.log(lines.join("\n"));
  const failed = lines.filter(l => l.startsWith("FAIL"));
  console.log(`\n${lines.filter(l => l.startsWith("PASS")).length} passed, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
})();
