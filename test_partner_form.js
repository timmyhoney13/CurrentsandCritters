#!/usr/bin/env node
/* The Partner With Us form on the marketing site, in a real browser.
 *
 * Run:  node test_partner_form.js        (needs Google Chrome installed)
 *
 * The form replaced a mailto: link, and a mailto: at least could not be subtly
 * wrong. A form can: it can look perfect and post to nowhere, it can mail the
 * template's own blanks, it can lose what somebody typed the moment the server
 * is asleep, and it can be 40px too wide on the phone most of these enquiries
 * will be written on. So this drives the REAL index.html in headless Chrome,
 * with fetch() stubbed, and checks:
 *
 *   1. LAYOUT, at five widths from a 390px phone to a 1600px desktop: nothing
 *      in the form overflows the page and no control is too small to tap. A
 *      single-width check would have passed while the two-column grid was
 *      still forcing a phone sideways: a text input has ~180px of intrinsic
 *      width that a `1fr` column will not shrink below unless it is told to.
 *   2. THE TEMPLATE is filled in on load, follows the partnership type, and
 *      NEVER overwrites words somebody typed themselves.
 *   3. THE REFUSALS happen in the browser, before a round trip: no name, a bad
 *      address, and a message that is still the template with its blanks in.
 *   4. THE POST is the exact JSON partner_contact.py validates, sent to the
 *      GAME server (this page is on Vercel, which cannot send mail), and it
 *      carries the honeypot field.
 *   5. A FAILED SEND KEEPS THE MESSAGE: the fallback is a mailto: with every
 *      field already in it, and the address is on the page in plain text.
 *   6. SENT IS A ONE-WAY DOOR: the form is replaced, so one enquiry cannot be
 *      sent three times by an impatient click.
 *
 * WHY CDP AND NOT --window-size: headless Chrome clamps its window to a 500px
 * minimum width, so --window-size=390,844 quietly lays the page out at 500 and
 * every "phone" assertion is really a second narrow-laptop assertion.
 * Emulation.setDeviceMetricsOverride sets the layout viewport below that floor
 * (the same reason, and the same fix, as test_tutorials_ingame.js).
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
   Static checks: things that must be true of the file itself.
   ══════════════════════════════════════════════════════════════════════ */
ok(/id="partner-form"/.test(INDEX), "index.html: the form band exists");
ok(/API_BASE \+ "\/api\/partner\/contact"/.test(INDEX),
   "index.html: it posts to the game server's /api/partner/contact");
ok(/name="company_site"/.test(INDEX), "index.html: the honeypot field is present");
ok(/class="pf-hp"/.test(INDEX) && !/\.pf-hp\s*\{[^}]*display:\s*none/.test(INDEX),
   "index.html: the honeypot is off-screen, not display:none (bots skip hidden inputs)");

// The address itself, visible as text and not only inside an href, because
// somebody writing from their own work account has to be able to read it.
ok(/>\s*currentsandcritters@gmail\.com\s*</.test(INDEX),
   "index.html: the inbox address is printed on the page, not just linked");
ok(/id="pf-mailto"[\s\S]{0,200}mailto:currentsandcritters@gmail\.com/.test(INDEX),
   "index.html: ...and it is also a mailto: link");

// The old dead end must be gone from the Partner button.
ok(/<a class="btn btn-mint btn-lg" href="#partner-form">Partner With Us/.test(INDEX),
   "index.html: the Partner With Us button opens the form instead of a mailto:");

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
const SCENARIOS = {
  layout: `
    var band = document.getElementById("partner-form");
    var VW = document.documentElement.clientWidth;
    var W = band.getBoundingClientRect();
    ok(W.right <= VW + 2 && W.left >= -2, "the form band fits the " + VW + "px page");
    ok(document.documentElement.scrollWidth <= VW + 2,
       "the page does not scroll sideways at " + VW + "px (" +
       document.documentElement.scrollWidth + ")");
    var bad = [];
    Array.prototype.forEach.call(
      band.querySelectorAll("input:not([tabindex='-1']), select, textarea, button"),
      function (el) {
        var r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) return;            // the honeypot
        if (r.right > VW + 2 || r.left < -2) bad.push((el.id || el.tagName) + " ends at " + Math.round(r.right));
        if (r.width < 44 || r.height < 32) bad.push((el.id || el.tagName) + " is only " + Math.round(r.width) + "x" + Math.round(r.height));
      });
    ok(bad.length === 0, "every control at " + VW + "px is on screen and big enough to tap: " + bad.join(", "));
    var msg = document.getElementById("pf-message").getBoundingClientRect();
    ok(msg.height >= 150, "the message box is big enough to write in at " + VW + "px (" + Math.round(msg.height) + "px)");
    // Nothing may sit on top of the controls: the section is full of
    // absolutely-positioned decoration, and a field a tap cannot reach is a
    // field nobody fills in. elementFromPoint answers in VIEWPORT coordinates,
    // so each control is scrolled into view before it is asked about (the page
    // sets scroll-behavior:smooth, which would answer about the old position).
    document.documentElement.style.scrollBehavior = "auto";
    var covered = [];
    var ids = ["pf-name", "pf-email", "pf-kind", "pf-message", "pf-send", "pf-restore"];
    for (var i = 0; i < ids.length; i++) {
      var el = document.getElementById(ids[i]);
      el.scrollIntoView({ block: "center" });
      await sleep(30);
      var r = el.getBoundingClientRect();
      var top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if (top !== el && !el.contains(top)) {
        covered.push(ids[i] + " under " + (top ? (top.id || top.className || top.tagName) : "nothing"));
      }
    }
    ok(covered.length === 0, "every field can actually be tapped at " + VW + "px: " + covered.join(", "));
  `,

  template: `
    var msg = document.getElementById("pf-message");
    var kind = document.getElementById("pf-kind");
    ok(msg.value.indexOf("Hi Tim") === 0, "the template is already in the box on load");
    ok(/_{3,}/.test(msg.value), "...with blanks to fill in");

    var first = msg.value;
    kind.value = "retail";
    kind.dispatchEvent(new Event("change"));
    ok(msg.value !== first, "changing the partnership type rewrites the template");
    ok(/copies to start/.test(msg.value), "...to the one for that type");

    // Somebody's own words are theirs.
    msg.value = "We would like to stock this in 40 stores.";
    kind.value = "creator";
    kind.dispatchEvent(new Event("change"));
    ok(msg.value === "We would like to stock this in 40 stores.",
       "typed words are never overwritten by a template change");

    document.getElementById("pf-restore").click();
    ok(/_{3,}/.test(msg.value), "Reset the template puts a fresh template back");

    // Every partnership type has one, and it is about that type.
    // A template counts as a template if it opens the letter AND leaves the
    // sender something to fill in. There are two ways it says that now: the
    // quiet ____ blanks, and the shouted [INSERT ... HERE] the over-$100 one
    // uses because at that size a blank the sender skims past is a real cost.
    // Matching only ____ would fail a template that has a validate() rule of
    // its own refusing to send while its placeholders are still there.
    var blanks = function (v) { return /_{3,}/.test(v) || v.indexOf("[INSERT") >= 0; };
    var missing = [];
    Array.prototype.forEach.call(kind.options, function (opt) {
      kind.value = opt.value;
      document.getElementById("pf-restore").click();
      if (msg.value.indexOf("Hi Tim") !== 0 || !blanks(msg.value)) missing.push(opt.value);
    });
    ok(missing.length === 0, "every partnership type has a template: " + missing.join(","));
  `,

  /* The way in from the tier cards: the two tiers whose checkout is not
     switched on yet, and every amount above the top tier. None of them has a
     Buy button, so this form IS their product page. If the link stops filling
     the box in, the reader lands on an empty form with no idea what to write,
     which is exactly the moment the biggest contributions are lost. */
  enquiry: `
    var msg  = document.getElementById("pf-message");
    var kind = document.getElementById("pf-kind");
    var hint = document.querySelector(".pf-hint");

    var custom = document.querySelector('[data-tier-enquiry="custom"]');
    ok(!!custom, "the over-$100 card has a way into the form");
    custom.click();
    await sleep(500);
    ok(kind.value === "major", "it picks the over-$100 partnership type: " + kind.value);
    ok(msg.value.indexOf("[INSERT YOUR NAME HERE]") > 0, "the template says where the name goes");
    ok(msg.value.indexOf("[INSERT AMOUNT HERE") > 0, "...and where the amount goes");
    ok(hint && hint.textContent.indexOf("[INSERT") > 0,
       "the hint names the blanks THIS template has, not the ____ it doesn't");

    var riptide = document.querySelector('[data-tier-enquiry="Riptide ($75)"]');
    ok(!!riptide, "a locked tier card offers the same door");
    riptide.click();
    await sleep(500);
    ok(msg.value.indexOf("the Riptide ($75) tier") > 0,
       "...and the message already names the tier being asked for");

    var form = document.getElementById("pf-form");
    var status = document.getElementById("pf-status");
    var posted = 0;
    window.fetch = function () {
      posted++;
      return Promise.resolve({ json: function () { return Promise.resolve({ ok: true }); } });
    };
    document.getElementById("pf-name").value  = "Alex Rivera";
    document.getElementById("pf-email").value = "alex@blueharbor.org";
    form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    ok(posted === 0 && /INSERT/i.test(status.textContent),
       "a message still holding [INSERT ...] is refused, not mailed: " + status.textContent);
  `,

  refusals: `
    var form = document.getElementById("pf-form");
    var status = document.getElementById("pf-status");
    var posted = 0;
    window.fetch = function () { posted++; return Promise.resolve({ json: function () { return Promise.resolve({ ok: true }); } }); };
    function submit() { form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true })); }

    document.getElementById("pf-name").value = "";
    submit();
    ok(posted === 0 && /name/i.test(status.textContent), "no name is refused here, not at the server: " + status.textContent);

    document.getElementById("pf-name").value = "Alex Rivera";
    document.getElementById("pf-email").value = "not-an-address";
    submit();
    ok(posted === 0 && /email/i.test(status.textContent), "a junk address is refused: " + status.textContent);

    document.getElementById("pf-email").value = "alex@blueharbor.org";
    submit();   // the message is still the untouched template
    ok(posted === 0 && /blank/i.test(status.textContent),
       "the template's own blanks are refused rather than mailed: " + status.textContent);

    document.getElementById("pf-message").value = "Hi";
    submit();
    ok(posted === 0, "a two-word message is refused");
    ok(!!document.querySelector(".pf-bad"), "the field that needs fixing is marked");
  `,

  posts: `
    var form = document.getElementById("pf-form");
    var seen = null, calls = 0;
    window.fetch = function (url, opts) {
      calls++; seen = { url: url, opts: opts };
      return Promise.resolve({ json: function () { return Promise.resolve({ ok: true, message: "Thanks!" }); } });
    };
    document.getElementById("pf-name").value = "Alex Rivera";
    document.getElementById("pf-org").value = "Blue Harbor Aquarium";
    document.getElementById("pf-email").value = "alex@blueharbor.org";
    document.getElementById("pf-link").value = "blueharbor.org";
    document.getElementById("pf-kind").value = "conservation";
    document.getElementById("pf-message").value = "We run a coastal education program and would love to work together this summer.";
    form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    // A second click while the first is in flight must not send twice.
    form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));

    ok(!!seen, "submitting posts");
    ok(calls === 1, "a double click sends one enquiry, not two (" + calls + ")");
    ok(seen && seen.url === "https://play.currentsandcritters.com/api/partner/contact",
       "...to the game server, the only host that can send mail: " + (seen && seen.url));
    ok(seen && seen.opts.method === "POST" && /application\\/json/.test(seen.opts.headers["Content-Type"]),
       "...as JSON");
    var body = seen ? JSON.parse(seen.opts.body) : {};
    var want = ["name", "org", "email", "link", "kind", "message", "company_site"];
    var missing = want.filter(function (k) { return !(k in body); });
    ok(missing.length === 0, "...with every field the server validates: missing " + missing.join(","));
    ok(body.kind === "conservation" && body.email === "alex@blueharbor.org" && body.org === "Blue Harbor Aquarium",
       "...carrying what was typed");
    ok(body.company_site === "", "...and an empty honeypot from a real person");

    await sleep(80);
    ok(!document.getElementById("pf-form"), "a sent enquiry replaces the form, so it cannot be sent again");
    ok(!!document.querySelector(".pf-sent"), "...with a confirmation in its place");
  `,

  fallback: `
    var form = document.getElementById("pf-form");
    var status = document.getElementById("pf-status");
    window.fetch = function () { return Promise.reject(new Error("offline")); };
    document.getElementById("pf-name").value = "Alex Rivera";
    document.getElementById("pf-email").value = "alex@blueharbor.org";
    document.getElementById("pf-message").value = "We would love to stock this in our aquarium shop this season.";
    form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    await sleep(80);

    var link = status.querySelector("a");
    ok(!!link, "a send that could not happen offers a way out: " + status.textContent);
    var href = link ? decodeURIComponent(link.getAttribute("href")) : "";
    ok(/^mailto:currentsandcritters@gmail\\.com/.test(href), "...a mailto: to the same inbox");
    ok(href.indexOf("We would love to stock this") > -1, "...with the message still in it");
    ok(href.indexOf("alex@blueharbor.org") > -1, "...and the address to reply to");
    ok(!!document.getElementById("pf-form") && !document.getElementById("pf-send").disabled,
       "...and the form is still there to try again");

    // The server refusing for a reason the reader can fix is NOT a mailto:
    // moment: it would send them off to email a message they could just correct.
    window.fetch = function () {
      return Promise.resolve({ json: function () { return Promise.resolve({ ok: false, error: "bad", message: "Please tell us your name." }); } });
    };
    form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    await sleep(80);
    ok(!status.querySelector("a") && /name/i.test(status.textContent),
       "a fixable refusal from the server is shown as-is: " + status.textContent);
  `,
};

/* ══════════════════════════════════════════════════════════════════════
   HARNESS
   ══════════════════════════════════════════════════════════════════════ */
const sleep = ms => new Promise(r => setTimeout(r, ms));
const DEBUG_PORT_BASE = 9411 + (process.pid % 40) * 10;
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
      { expression: expr, returnByValue: true, awaitPromise: true }, 40000);
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
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "ccpf-"));
  const file = path.join(ROOT, `.cc-partner-test-${process.pid}-${scenario}-${width}.html`);
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
    // page it was launched with has parsed. Evaluating then runs against an
    // empty document, every getElementById is null, and the run fails with
    // exceptions that have nothing to do with the form. So wait for the real
    // page, by name, not for a timer.
    const loaded = await waitFor(async () => {
      const r = await cdp.send("Runtime.evaluate", {
        expression: "document.readyState === 'complete' && !!document.getElementById('pf-form')",
        returnByValue: true,
      });
      return r && r.result && r.result.result && r.result.result.value === true;
    }, 30000);
    if (!loaded) return [`FAIL ${scenario}@${width}: the page never finished loading`];

    // The override is applied AND verified: a page target still coming up
    // accepts the command and lays out at the old size anyway, and a run that
    // proceeds is silently testing the wrong width, which is the whole failure
    // this override exists to end.
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

    // Let the page's own scripts (and the fonts) settle before measuring.
    await sleep(400);

    const out = await cdp.evalAsync(`(async () => {
      const results = [];
      const ok = (c, m) => results.push((c ? "PASS " : "FAIL ") + m);
      const sleep = ms => new Promise(r => setTimeout(r, ms));
      try {
        ${SCENARIOS[scenario]}
      } catch (err) {
        results.push("FAIL exception in ${scenario}: " + (err && err.message || err));
      }
      return results;
    })()`);
    if (!Array.isArray(out) || !out.length) return [`FAIL ${scenario}@${width}: no output`];
    return out;
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

(async () => {
  // Layout at five real widths: an iPhone SE, an iPhone 15, an iPad, a laptop
  // and a wide desktop. Everything else runs once on the phone, because that
  // is where a form is hardest and where these enquiries get written.
  for (const [w, h] of [[375, 812], [393, 852], [768, 1024], [1280, 800], [1600, 900]]) {
    lines.push(...await run("layout", w, h));
  }
  for (const name of ["template", "enquiry", "refusals", "posts", "fallback"]) {
    lines.push(...await run(name, 393, 852));
  }

  console.log(lines.join("\n"));
  const failed = lines.filter(l => l.startsWith("FAIL"));
  console.log(`\n${lines.filter(l => l.startsWith("PASS")).length} passed, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
})();
