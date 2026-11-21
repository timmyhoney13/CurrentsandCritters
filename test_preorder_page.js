#!/usr/bin/env node
/* The plush pre-order page (/preorder) and the Store tab's cart, in a real browser,
 * against the REAL reservation rules (plush_reserve.py behind a stub server).
 *
 * Run:  node test_preorder_page.js        (needs Google Chrome and python3)
 *
 * Pins, at a phone width and a desktop width:
 *   • every price reads "$44.99", order 3 takes $5 off each (first 3 only) + "+ shipping and handling";
 *   • the free-shipping bar's four messages, its fill and its aria value, as
 *     plushies are added one at a time, in any mix;
 *   • the order summary: lines, subtotal, shipping (calculated -> Free at 3),
 *     sales tax always "Calculated at checkout", the total label;
 *   • a non-U.S. country never shows free shipping;
 *   • the form refuses to send without a plushie or with bad fields;
 *   • a real reservation is stored with SERVER-recomputed totals, a double
 *     click sends once, and reserving again updates rather than duplicates;
 *   • links carry quantities (?whale-shark=2), and the Store tab's steppers
 *     drive the same cart and link to /preorder with them;
 *   • nothing scrolls sideways, and the mobile dock appears with a cart.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const http = require("http");
const path = require("path");
const { spawn } = require("child_process");

const ROOT = __dirname;
const CHROME = ["/usr/bin/google-chrome", "/usr/bin/chromium",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find(p => fs.existsSync(p));
const CAT = JSON.parse(fs.readFileSync(path.join(ROOT, "plush/catalog.json"), "utf8"));
const lines = [];
const ok = (c, m) => lines.push((c ? "PASS " : "FAIL ") + m);
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ── static ──
ok(CAT.products.every(p => p.priceCents === 4499) && CAT.bundleDiscountCents === 500, "catalog: every plushie is $44.99, $5 off each on 3+");
ok(CAT.priceNote === "+ shipping and handling" && CAT.bundleMaxItems === 3, "catalog: the price note is exact");
ok(CAT.freeShippingMin === 3 && CAT.freeShippingCountries.join() === "US", "catalog: free standard shipping from 3, U.S. only");
const JS = fs.readFileSync(path.join(ROOT, "plush/preorder.js"), "utf8");
ok(!/\b(?:2[0-9]|3[0-8])\.99\b/.test(JS) && !/3999|4499/.test(JS), "preorder.js hard-codes no price: it reads the catalog");
ok(!/5% of every purchase/i.test(JS), "preorder.js states the pledge on no second basis");

if (!CHROME) { console.log(lines.join("\n") + "\nSKIP: no Chrome"); process.exit(0); }

// ── servers ──
const API_PORT = 18700 + (process.pid % 200);
const WEB_PORT = API_PORT + 300;
const api = spawn("python3", [path.join(ROOT, "test_plush_reserve.py"), "--serve", String(API_PORT)], { stdio: ["ignore", "pipe", "inherit"] });
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
  ".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg" };
const inject = `<script>window.CC_PREORDER_API="http://127.0.0.1:${API_PORT}";</script>`;
const web = http.createServer((req, res) => {
  let u = decodeURIComponent(req.url.split("?")[0]);
  if (/^https?:/.test(u)) u = "/";
  if (u === "/preorder") u = "/preorder.html";
  if (u === "/") u = "/index.html";
  const file = path.join(ROOT, path.normalize(u).replace(/^(\.\.[\/\\])+/, ""));
  if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); return res.end(); }
  let body = fs.readFileSync(file);
  if (file.endsWith(".html")) body = Buffer.from(String(body).replace("<head>", "<head>" + inject));
  res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" });
  res.end(body);
}).listen(WEB_PORT, "127.0.0.1");

function get(url) {
  return new Promise((resolve, reject) => {
    http.get(url, r => { let d = ""; r.on("data", c => d += c); r.on("end", () => resolve(d)); }).on("error", reject);
  });
}
async function waitFor(fn, ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) { try { const v = await fn(); if (v) return v; } catch (_) {} await sleep(150); }
  return null;
}

class Cdp {
  static open(url) {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(url);
      const c = new Cdp(); c.ws = ws; c.id = 0; c.pending = new Map();
      ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && c.pending.has(m.id)) { c.pending.get(m.id)(m); c.pending.delete(m.id); } };
      ws.onopen = () => resolve(c); ws.onerror = reject;
    });
  }
  send(method, params, ms) {
    const id = ++this.id;
    return new Promise(resolve => {
      const t = setTimeout(() => { this.pending.delete(id); resolve(null); }, ms || 20000);
      this.pending.set(id, m => { clearTimeout(t); resolve(m); });
      this.ws.send(JSON.stringify({ id, method, params: params || {} }));
    });
  }
  async eval(expr, ms) {
    const r = await this.send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true }, ms || 60000);
    if (!r || !r.result) return null;
    if (r.result.exceptionDetails) return ["FAIL page exception: " + JSON.stringify(r.result.exceptionDetails).slice(0, 300)];
    return r.result.result ? r.result.result.value : null;
  }
  close() { try { this.ws.close(); } catch (_) {} }
}

let seq = 0;
async function run(name, url, width, height, body, ready) {
  const port = 19400 + (process.pid % 300) + (seq++);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "ccpo-"));
  const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", "--no-first-run",
    "--no-default-browser-check", "--disable-extensions", `--user-data-dir=${profile}`, `--window-size=${width},${height}`,
    `--remote-debugging-port=${port}`, "about:blank"], { stdio: "ignore" });
  let cdp = null;
  try {
    const target = await waitFor(async () => JSON.parse(await get(`http://127.0.0.1:${port}/json/list`)).find(t => t.type === "page"), 25000);
    if (!target) return [`FAIL ${name}@${width}: Chrome never came up`];
    cdp = await Cdp.open(target.webSocketDebuggerUrl);
    await cdp.send("Runtime.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 600 });
    await cdp.send("Page.enable");
    await cdp.send("Page.navigate", { url: `http://127.0.0.1:${WEB_PORT}${url}` });
    const loaded = await waitFor(async () => (await cdp.eval(ready)) === true, 30000);
    if (!loaded) return [`FAIL ${name}@${width}: never became ready`];
    const out = await cdp.eval(`(async () => {
      const results = []; const ok = (c, m) => results.push((c ? "PASS " : "FAIL ") + m);
      const sleep = ms => new Promise(r => setTimeout(r, ms));
      const $ = s => document.querySelector(s), $$ = s => [].slice.call(document.querySelectorAll(s));
      const text = s => ($(s) || {}).textContent || "";
      document.documentElement.style.scrollBehavior = "auto";
      try { ${body} } catch (err) { results.push("FAIL exception: " + (err && err.stack || err)); }
      return results;
    })()`, 120000);
    if (!Array.isArray(out)) return [`FAIL ${name}@${width}: no output`];
    return out.map(l => l.replace(/^(PASS |FAIL )/, `$1${name}@${width}: `));
  } catch (err) {
    return [`FAIL ${name}@${width}: ${err && err.message}`];
  } finally {
    if (cdp) cdp.close();
    try { chrome.kill("SIGKILL"); } catch (_) {}
    await sleep(150);
    try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 150 }); } catch (_) {}
  }
}

const READY_FULL = `document.querySelectorAll('.ccpo-card').length === 2 && !!document.querySelector('.ccpo-ship-msg') && document.querySelector('.ccpo-ship-msg').textContent.length > 5`;

const FLOW = (email) => `
  try { localStorage.clear(); } catch (e) {}
  const VW = document.documentElement.clientWidth;
  ok(document.documentElement.scrollWidth <= VW + 1, "nothing scrolls sideways (" + document.documentElement.scrollWidth + " vs " + VW + ")");
  ok($$(".ccpo-card .ccpo-price").length === 2 && $$(".ccpo-card .ccpo-price").every(p => /\\$44\\.99\\s*\\+ shipping and handling/.test(p.textContent)),
     "both cards read $44.99 + shipping and handling");
  ok(/\\$44\\.99 each \\+ shipping and handling/.test(text(".ccpo-chips")), "the hero states the price the same way");
  ok(/50 cm/.test(text("#ccpo-whale-shark")) && /2\\.5 lbs/.test(text("#ccpo-whale-shark")) &&
     /Premium ultra-soft plush fabric/.test(text("#ccpo-whale-shark")) && /Securely enclosed weighted filling/.test(text("#ccpo-whale-shark")) &&
     /Straight from the deck: the Whale Shark/.test(text("#ccpo-whale-shark")), "the whale shark card carries every spec");
  ok(/50 cm/.test(text("#ccpo-loggerhead-sea-turtle")) && /2\\.5 lbs/.test(text("#ccpo-loggerhead-sea-turtle")) &&
     /Straight from the deck: the Loggerhead Sea Turtle/.test(text("#ccpo-loggerhead-sea-turtle")), "the loggerhead card carries every spec");
  ok(/5% goes to ocean conservation/.test(document.body.textContent), "the pledge is on the page");
  ok(/exclusive Fish critter/.test(document.body.textContent), "the in-game unlock is on the page");

  // start from an empty cart
  window.CCPreorder.setQty("whale-shark", 0); window.CCPreorder.setQty("loggerhead-sea-turtle", 0);
  await sleep(30);
  const msg = () => text(".ccpo-checkout .ccpo-ship-msg");
  const sub = () => { const dds = $$(".ccpo-totals dd"); return dds.map(d => d.textContent.trim()); };
  const plus = id => $("#ccpo-" + id + " .ccpo-qty-btn:last-child"), minus = id => $("#ccpo-" + id + " .ccpo-qty-btn:first-child");
  ok(msg() === "Add 3 plushies to unlock $5 off each + free shipping.", "0: " + msg());
  ok(minus("whale-shark").disabled, "minus is disabled at zero");
  ok(sub()[2] === "Calculated at checkout" && sub()[3] === "Calculated at checkout", "0: shipping and tax are calculated at checkout");
  ok($(".ccpo-totals dd.ccpo-deal").hidden, "0: no deal row with an empty cart");

  plus("whale-shark").click(); await sleep(30);
  ok(msg() === "You\\u2019re 2 plushies away from $5 off each + free shipping.", "1: " + msg());
  ok(sub()[0] === "$44.99" && /Add 2 more/.test(sub()[1]) && sub()[2] === "Calculated at checkout" && sub()[4] === "$44.99", "1: $44.99, no discount, shipping calculated, NOT free: " + sub().join(" | "));
  ok(/Total before shipping & tax/.test(text(".ccpo-totals")), "1: total is before shipping & tax");
  ok($(".ccpo-checkout .ccpo-ship-track").getAttribute("aria-valuenow") === "1", "1: the bar's aria value is 1");

  plus("loggerhead-sea-turtle").click(); await sleep(30);
  ok(msg() === "Add 1 more plushie to unlock $5 off each + free shipping.", "2: " + msg());
  ok(sub()[0] === "$89.98" && sub()[2] === "Calculated at checkout", "2: $89.98, shipping still calculated");

  plus("whale-shark").click(); await sleep(30);
  ok(msg() === "You\\u2019ve unlocked $5 off each + free shipping!", "3 (mixed): " + msg());
  ok(sub()[0] === "$134.97" && sub()[1] === "\u2212$15.00" && /Free standard shipping/.test(sub()[2]) && sub()[3] === "Calculated at checkout" && sub()[4] === "$119.97",
     "3: $134.97 less $15.00 = $119.97, free standard shipping, tax still calculated: " + sub().join(" | "));
  ok(/Total before tax/.test(text(".ccpo-totals")), "3: total is before tax");
  ok($(".ccpo-checkout .ccpo-ship").classList.contains("is-unlocked"), "3: the bar shows unlocked");
  const fillW = parseFloat($(".ccpo-checkout .ccpo-ship-fill").style.width);
  ok(fillW === 100, "3: the bar is full: " + fillW);
  const lines = $$(".ccpo-lines li").map(li => li.textContent.replace(/\\s+/g, " ").trim());
  ok(lines.length === 2 && /Whale Shark.*2 × \\$44\\.99.*\\$89\\.98/.test(lines[0]) && /Loggerhead.*1 × \\$44\\.99.*\\$44\\.99/.test(lines[1]),
     "3: the summary lists each line: " + lines.join(" / "));

  plus("loggerhead-sea-turtle").click(); await sleep(30);
  ok(sub()[0] === "$179.96" && sub()[1] === "\u2212$15.00" && /Free for 3/.test(sub()[2]) && sub()[4] === "$164.96",
     "4: the deal covers the first 3 only, $179.96 less $15.00 = $164.96, extra shipping at checkout: " + sub().join(" | "));
  ok(/full price/.test(msg()), "4: the bar says plushies past 3 are full price: " + msg());

  // typed quantity
  const inp = $("#ccpo-whale-shark .ccpo-qty-n");
  inp.focus(); inp.value = "5"; inp.dispatchEvent(new Event("input")); inp.dispatchEvent(new Event("change")); inp.blur();
  await sleep(30);
  ok(sub()[0] === "$314.93" && sub()[4] === "$299.93", "typing 5 whale sharks: " + sub().join(" | "));
  inp.focus(); inp.value = "99"; inp.dispatchEvent(new Event("input")); inp.dispatchEvent(new Event("change")); inp.blur();
  await sleep(30);
  ok(inp.value === "10", "a typed quantity is capped at 10: " + inp.value);

  // non-U.S.
  const country = $("#ccpo-country");
  country.value = "CA"; country.dispatchEvent(new Event("change")); await sleep(30);
  ok(sub()[2] === "Calculated at checkout" && /\\$5 off each/.test(msg()) && /outside the USA/.test(msg()), "Canada: $5 off each but no free shipping, and it says why: " + msg());
  country.value = "US"; country.dispatchEvent(new Event("change")); await sleep(30);

  window.CCPreorder.setQty("whale-shark", 2); window.CCPreorder.setQty("loggerhead-sea-turtle", 1); await sleep(30);

  // the dock
  window.scrollTo(0, 0); await sleep(500);
  ok($(".ccpo-dock").classList.contains("is-on"), "the cart dock shows while the summary is off screen");
  ok(/3 plushies · \\$119\\.97 · \\$5 off each \\+ free shipping/.test(text(".ccpo-dock-txt")), "the dock carries the count, subtotal and status: " + text(".ccpo-dock-txt"));

  // validation
  const form = $(".ccpo-form");
  const submit = () => form.dispatchEvent(new Event("submit", { cancelable: true }));
  submit(); await sleep(50);
  ok($("#ccpo-first").getAttribute("aria-invalid") === "true" && /first name/.test(text("#ccpo-first-err")), "an empty form is refused with field errors");
  ok(document.activeElement === $("#ccpo-first"), "focus goes to the first bad field");
  $("#ccpo-first").value = "Ada"; $("#ccpo-last").value = "Lovelace"; $("#ccpo-email").value = "not-an-email";
  submit(); await sleep(50);
  ok(/doesn't look right/.test(text("#ccpo-email-err")), "a bad email is refused");
  $("#ccpo-email").value = "${email}"; $("#ccpo-zip").value = "12";
  submit(); await sleep(50);
  ok(/5-digit ZIP/.test(text("#ccpo-zip-err")), "a bad ZIP is refused");
  $("#ccpo-zip").value = "37203";

  // the real thing, double-clicked
  submit(); submit();
  const done = await (async () => { for (let i = 0; i < 100; i++) { if ($(".ccpo-done")) return true; await sleep(100); } return false; })();
  ok(done, "the reservation is confirmed on the page");
  ok(/You’re on the list, Ada!/.test(text(".ccpo-done h3")), "it greets them: " + text(".ccpo-done h3"));
  ok(/^Reference CCR-[0-9A-F]{6}$/.test(text(".ccpo-ref")), "it gives a reference: " + text(".ccpo-ref"));
  ok(/\\$119\\.97/.test(text(".ccpo-done")) && /Free standard shipping/.test(text(".ccpo-done")) && /Sales tax calculated at checkout/.test(text(".ccpo-done")),
     "the confirmation repeats the server's totals");
  ok(/Nothing has been charged/.test(text(".ccpo-done")), "the confirmation says nothing was charged");
  const docs = await fetch(window.CC_PREORDER_API + "/__docs").then(r => r.json());
  const all = Object.values(docs).filter(d => d.email === "${email}");
  ok(all.length === 1 && all[0].subtotalCents === 11997 && all[0].freeShipping === true && all[0].count === 3,
     "the server stored ONE reservation with recomputed totals: " + JSON.stringify(all.map(d => [d.count, d.subtotalCents, d.freeShipping])));
  ok(all.length === 1 && all[0].revisions === 0, "a double click sent once (revisions=" + (all[0] || {}).revisions + ")");

  // change it
  [].slice.call(document.querySelectorAll(".ccpo-done button")).pop().click(); await sleep(30);
  ok(!$(".ccpo-done") && !form.hidden, "Change my reservation brings the form back");
  window.CCPreorder.setQty("whale-shark", 1); window.CCPreorder.setQty("loggerhead-sea-turtle", 0); await sleep(30);
  submit();
  for (let i = 0; i < 100 && !$(".ccpo-done"); i++) await sleep(100);
  ok(/Reservation updated!/.test(text(".ccpo-done h3")), "reserving again UPDATES: " + text(".ccpo-done h3"));
  const docs2 = await fetch(window.CC_PREORDER_API + "/__docs").then(r => r.json());
  const all2 = Object.values(docs2).filter(d => d.email === "${email}");
  ok(all2.length === 1 && all2[0].count === 1 && all2[0].freeShipping === false && all2[0].subtotalCents === 4499,
     "...one reservation, now 1 plushie at $44.99 with shipping calculated");
  ok(document.documentElement.scrollWidth <= VW + 1, "still nothing scrolls sideways");
`;

const LINK = `
  const n = id => document.querySelector("#ccpo-" + id + " .ccpo-qty-n").value;
  ok(n("whale-shark") === "2" && n("loggerhead-sea-turtle") === "1", "a link's quantities fill the cart: " + n("whale-shark") + "/" + n("loggerhead-sea-turtle"));
  ok(text(".ccpo-checkout .ccpo-ship-msg") === "You\\u2019ve unlocked $5 off each + free shipping!", "...and the bar agrees");
`;

const STORE = `
  try { localStorage.clear(); } catch (e) {}
  document.getElementById("tabbtn-store").click(); await sleep(150);
  const panel = document.getElementById("panel-store");
  ok((panel.textContent.match(/\\$44\\.99 \\+ shipping and handling/g) || []).length >= 2, "both store cards read $44.99 + shipping and handling");
  ok(/Order 3 plushies \\(any mix\\) and get free shipping and \\$5 off each in the USA/.test(panel.textContent), "the store carries the 3-plushie promotion");
  const steppers = panel.querySelectorAll("[data-ccpo-qty] .ccpo-qty");
  ok(steppers.length === 2, "each plush card has a quantity stepper: " + steppers.length);
  window.CCPreorder.setQty("whale-shark", 0); window.CCPreorder.setQty("loggerhead-sea-turtle", 0); await sleep(30);
  ok(/Add 3 plushies to unlock \\$5 off each \\+ free shipping\\./.test(text(".ccpo-compact .ccpo-ship-msg")), "the store bar starts at 0: " + text(".ccpo-compact .ccpo-ship-msg"));
  steppers[0].querySelector(".ccpo-qty-btn:last-child").click();
  steppers[1].querySelector(".ccpo-qty-btn:last-child").click();
  steppers[1].querySelector(".ccpo-qty-btn:last-child").click(); await sleep(30);
  ok(text(".ccpo-compact .ccpo-ship-msg") === "You\\u2019ve unlocked $5 off each + free shipping!", "1 whale + 2 turtles unlocks it on the store too");
  ok(/3 plushies · \\$119\\.97 \\+ tax/.test(text(".ccpo-compact-count")), "the store total: " + text(".ccpo-compact-count"));
  const go = document.querySelector(".ccpo-compact a.ccpo-btn");
  ok(/\\/preorder\\?whale-shark=1&loggerhead-sea-turtle=2$/.test(go.getAttribute("href")), "Reserve now carries the quantities: " + go.getAttribute("href"));
  const res = [].slice.call(panel.querySelectorAll(".st-preorder")).map(a => a.getAttribute("href"));
  ok(res.join() === "/preorder?add=whale-shark,/preorder?add=loggerhead-sea-turtle", "each card's Reserve is a real link: " + res.join());
  const VW = document.documentElement.clientWidth;
  ok(document.documentElement.scrollWidth <= VW + 1, "the Store does not scroll sideways");
`;

(async () => {
  const up = await waitFor(async () => /ok/.test(await get(`http://127.0.0.1:${API_PORT}/api/plush/reserve`)), 15000);
  if (!up) { console.log("FAIL the stub API never came up"); process.exit(1); }
  for (const [w, h] of [[1280, 900], [390, 844]]) {
    lines.push(...await run("flow", "/preorder", w, h, FLOW(`ada${w}@example.com`), READY_FULL));
  }
  lines.push(...await run("link", "/preorder?whale-shark=2&loggerhead-sea-turtle=1", 1280, 900, LINK, READY_FULL));
  for (const [w, h] of [[1280, 900], [390, 844]]) {
    lines.push(...await run("store", "/", w, h, STORE, `!!window.ccTabs && document.querySelectorAll('#panel-store [data-ccpo-qty] .ccpo-qty').length === 2`));
  }
  api.kill(); web.close();
  console.log(lines.join("\n"));
  const failed = lines.filter(l => l.startsWith("FAIL"));
  console.log(`\n${lines.length - failed.length} passed, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
})();
