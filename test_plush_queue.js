#!/usr/bin/env node
/* The plushy vote's HELD BALLOTS, in a real browser, across a real reload.
 *
 * Run:  node test_plush_queue.js        (needs Google Chrome installed)
 *
 * WHY THIS EXISTS
 * Timothy reported two votes erased. The vote server is on a free tier that
 * sleeps and deploys hours behind the website, so a ballot that cannot be sent
 * is kept on the device and sent next visit. That holding pen was ONE slot:
 * the second person to vote on a phone being passed round a table overwrote
 * the first person's held ballot, the hand-off button deleted whatever was in
 * it while saying "the last vote is counted and safe", and a replayed ballot
 * was re-stamped with whichever ballot the device was on by then, which could
 * overwrite a DIFFERENT person's recorded vote on the server.
 *
 * None of that is visible from the server, from the markup, or from a source
 * grep: it only shows up as votes quietly missing from the tally. So this
 * drives the real index.html in headless Chrome with the vote API stubbed,
 * votes as three people at one table, reloads the page with the server back,
 * and checks that THREE ballots arrive with three different voter ids.
 *
 * WHY A LOCAL HTTP SERVER AND NOT file://
 * Chrome gives a file:// page an opaque origin with no usable localStorage,
 * and the page wraps every storage call in try/catch precisely so it still
 * works there. A queue test against no storage would pass while testing
 * nothing, so the page is served over http from a throwaway port.
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

const lines = [];
const ok = (cond, label) => lines.push((cond ? "PASS " : "FAIL ") + label);
const sleep = ms => new Promise(r => setTimeout(r, ms));

const INDEX = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
if (INDEX.indexOf('id="plushy"') === -1) {
  console.log("the plushy vote is not on the page; nothing to test");
  process.exit(0);
}

/* ── the fake vote server, installed before the page's own scripts ──────
   Its mode and everything it has received live in localStorage, so both
   survive the reload that is the whole point of the test. */
const BOOT = `
(() => {
  const RECEIVED = "__cc_received", MODE = "__cc_mode", BALLOTS = "__cc_ballots";
  const get = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  const set = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
  const reply = (status, data) => Promise.resolve({
    status: status,
    ok: status >= 200 && status < 300,
    json: () => Promise.resolve(data),
  });

  window.__cc = {
    mode: m => set(MODE, m),
    received: () => JSON.parse(get(RECEIVED) || "[]"),
    ballots: () => Object.keys(JSON.parse(get(BALLOTS) || "{}")).length,
    contact: v => {
      const box = document.getElementById("plushContact");
      box.value = v;
      box.dispatchEvent(new Event("input"));     // the page watches for typing
    },
    contactValue: () => document.getElementById("plushContact").value,
    sendLabel: () => document.getElementById("plushSend").textContent,
    picked: () => Array.prototype.map.call(
      document.querySelectorAll('.plush-pick[aria-pressed="true"]'), t => t.dataset.f),
    pending: () => get("cc-plush-pending"),
    voter: () => get("cc-plush-voter"),
    seed: (k, v) => set(k, v),
    // A scenario must start on a device that has never voted: leftover picks
    // in cc-plush-picks are pre-selected on load and end up inside the next
    // scenario's ballot.
    reset: () => {
      ["cc-plush-voter", "cc-plush-picks", "cc-plush-contact", "cc-plush-pending",
       "cc-plush-tally", RECEIVED, BALLOTS, "__cc_tallies"]
        .forEach(k => { try { localStorage.removeItem(k); } catch (e) {} });
      set(MODE, "up");
    },
    pick: slug => {
      const tile = document.querySelector('.plush-pick[data-f="' + slug + '"]');
      if (!tile) throw new Error("no tile for " + slug);
      tile.click();
    },
    send: () => document.getElementById("plushSend").click(),
    sendDisabled: () => document.getElementById("plushSend").disabled,
    next: () => document.getElementById("plushNext").click(),
    nextHidden: () => document.getElementById("plushNext").hidden,
    say: () => document.getElementById("plushSay").textContent,
    // The standings panel, which has to be on screen for everybody and must
    // never be emptied by anything that happens afterwards.
    boardOn: () => document.getElementById("plushResults").classList.contains("on"),
    boardRows: () => document.querySelectorAll(".plush-row").length,
    boardTotal: () => document.getElementById("plushTotal").textContent,
    boardTop: () => {
      const row = document.querySelector(".plush-row .who");
      return row ? row.textContent : "";
    },
    tallyCalls: () => Number(get("__cc_tallies") || 0),
    // The fold the two optional questions live in.
    moreOpen: () => document.getElementById("plushMore").open,
  };

  window.fetch = (url, opts) => {
    url = String(url);
    const mode = get(MODE) || "up";
    if (url.indexOf("/api/plush/vote") > -1) {
      const body = opts && opts.body ? JSON.parse(opts.body) : {};
      if (mode === "asleep") return Promise.reject(new Error("offline"));
      if (mode === "404") return reply(404, null);
      if (mode === "429") return reply(429, { ok: false, error: "too_many_requests",
                                              message: "That is a lot of votes at once." });
      if (mode === "400") return reply(400, { ok: false, error: "no_picks",
                                              message: "Pick at least one critter first." });
      // An address nobody can read names no ballot: the server counts the
      // vote as the device's and says the reminder did not take.
      const unreadable = mode === "warn";
      const contact = unreadable ? "" : String(body.contact || "").trim().toLowerCase();
      const all = JSON.parse(get(RECEIVED) || "[]");
      all.push({ voter: body.voter, picks: body.picks, contact: contact });
      set(RECEIVED, JSON.stringify(all));
      // The real server keys a ballot by the ADDRESS when there is one and by
      // the browser's own id when there is not (plush_vote.ballot_key), so the
      // stub has to as well, or "how many votes are in" means nothing here.
      const ballots = JSON.parse(get(BALLOTS) || "{}");
      // mode "old" is the server as Render is still running it: it keys every
      // ballot by the device, whatever address the vote carries.
      const key = (mode === "old" ? "" : contact) || body.voter;
      const changed = Object.prototype.hasOwnProperty.call(ballots, key);
      ballots[key] = { picks: body.picks, contact: contact };
      set(BALLOTS, JSON.stringify(ballots));
      const counts = {};
      Object.keys(ballots).forEach(k =>
        (ballots[k].picks || []).forEach(p => { counts[p] = (counts[p] || 0) + 1; }));
      const out = { ok: true, yours: body.picks, contact: contact, changed: changed,
                    counts: counts, firsts: {}, ballots: Object.keys(ballots).length };
      if (unreadable) {
        out.contactError = "That did not look like an email address or a phone " +
                           "number, so the vote counted but the reminder did not.";
      }
      return reply(200, out);
    }
    if (url.indexOf("/api/plush/tally") > -1) {
      set("__cc_tallies", String(Number(get("__cc_tallies") || 0) + 1));
      // "asleep" and "404" are the server being away, and the page is
      // required to keep showing the standings it already has rather than
      // blanking them.
      if (mode === "asleep") return Promise.reject(new Error("offline"));
      if (mode === "404") return reply(404, null);
      const all = JSON.parse(get(RECEIVED) || "[]");
      const counts = {};
      all.forEach(b => (b.picks || []).forEach(p => { counts[p] = (counts[p] || 0) + 1; }));
      return reply(200, { ok: true, enabled: true, counts: counts, firsts: {},
                          ballots: all.length });
    }
    // Everything else on the page (live stats, Firebase) is not this test's
    // business, and must not look like a network failure either.
    return reply(200, {});
  };
})();
`;

/* ══════════════════════════════════════════════════════════════════════
   HARNESS: a throwaway static server, one Chrome with its own profile,
   and CDP. Two Chromes sharing a profile starve each other's rAF, so the
   profile is per run (same reason as test_tutorials_ingame.js).
   ══════════════════════════════════════════════════════════════════════ */
const TYPES = {
  ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
  ".svg": "image/svg+xml", ".webp": "image/webp", ".json": "application/json",
  ".woff2": "font/woff2", ".ico": "image/x-icon", ".m4a": "audio/mp4",
  ".mp4": "video/mp4", ".webm": "video/webm",
};

function serve() {
  const server = http.createServer((req, res) => {
    const rel = decodeURIComponent(req.url.split("?")[0].split("#")[0]).replace(/^\/+/, "");
    const file = path.join(ROOT, rel || "index.html");
    if (!file.startsWith(ROOT)) { res.statusCode = 403; return res.end(); }
    fs.readFile(file, (err, data) => {
      if (err) { res.statusCode = 404; return res.end("not found"); }
      res.setHeader("Content-Type", TYPES[path.extname(file).toLowerCase()] || "application/octet-stream");
      res.end(data);
    });
  });
  return new Promise(r => server.listen(0, "127.0.0.1", () => r({
    port: server.address().port,
    close: () => new Promise(d => server.close(() => d())),
  })));
}

function getJson(url, timeoutMs = 4000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, res => {
      let b = ""; res.on("data", d => (b += d)); res.on("end", () => resolve(b));
    });
    req.on("error", reject);
    req.setTimeout(timeoutMs, () => req.destroy(new Error("timeout")));
  });
}

async function waitFor(fn, ms, every = 100) {
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
  send(method, params, timeoutMs = 20000) {
    const id = ++this.id;
    return new Promise(resolve => {
      const t = setTimeout(() => { this.waiting.delete(id); resolve(null); }, timeoutMs);
      this.waiting.set(id, m => { clearTimeout(t); resolve(m); });
      try { this.ws.send(JSON.stringify({ id, method, params: params || {} })); }
      catch (_) { clearTimeout(t); this.waiting.delete(id); resolve(null); }
    });
  }
  async evaluate(expr) {
    const r = await this.send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true }, 30000);
    if (!r || !r.result) throw new Error("no answer from the page: " + expr.slice(0, 60));
    if (r.result.exceptionDetails) {
      throw new Error("page threw: " +
        JSON.stringify(r.result.exceptionDetails.exception || {}).slice(0, 300));
    }
    return r.result.result ? r.result.result.value : undefined;
  }
  close() { try { this.ws.close(); } catch (_) {} }
}

/* One page session. `load()` navigates (or re-navigates: a reload is how a
   held ballot gets its next chance) and waits for the vote script to be ready. */
async function session(port) {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "ccpq-"));
  const dbg = 9500 + (process.pid % 60) * 7;
  const chrome = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    `--user-data-dir=${profile}`, "--window-size=900,1200",
    `--remote-debugging-port=${dbg}`, "about:blank",
  ], { stdio: "ignore" });

  const target = await waitFor(async () => {
    const list = JSON.parse(await getJson(`http://127.0.0.1:${dbg}/json/list`));
    return list.find(t => t.type === "page" && t.webSocketDebuggerUrl) || null;
  }, 25000);
  if (!target) throw new Error("Chrome never came up");

  const cdp = await Cdp.open(target.webSocketDebuggerUrl);
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  await cdp.send("Page.addScriptToEvaluateOnNewDocument", { source: BOOT });

  const url = `http://127.0.0.1:${port}/index.html`;
  return {
    cdp,
    async load() {
      await cdp.send("Page.navigate", { url });
      const ready = await waitFor(() => cdp.evaluate(
        "!!document.querySelector('.plush-pick') && !!window.__cc"), 25000);
      if (!ready) throw new Error("the vote never finished loading");
      await sleep(250);          // let the on-load replay start
    },
    ev: expr => cdp.evaluate(expr),
    async stop() {
      cdp.close();
      try { chrome.kill("SIGKILL"); } catch (_) {}
      await sleep(150);
      try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 150 }); }
      catch (_) {}
    },
  };
}

/* The queue as the page stores it, normalised: one entry per held ballot,
   whatever shape the key is in. */
function queueOf(raw) {
  if (!raw) return [];
  let v;
  try { v = JSON.parse(raw); } catch (_) { return ["UNPARSEABLE"]; }
  if (Array.isArray(v)) return v;
  return [v];                   // the old one-slot shape
}

(async () => {
  if (!CHROME) {
    console.log("SKIP: no Chrome/Chromium found");
    process.exit(0);
  }
  const web = await serve();
  const s = await session(web.port);

  try {
    /* ══════════════════════════════════════════════════════════════════
       ONE PHONE, THREE PEOPLE, SERVER ASLEEP. This is the table Timothy
       described, and the votes that went missing.
       ══════════════════════════════════════════════════════════════════ */
    await s.load();
    await s.ev("window.__cc.reset()");
    await s.load();
    await s.ev('window.__cc.mode("asleep")');

    await s.ev('window.__cc.pick("sardine"); window.__cc.pick("lobster")');
    await s.ev("window.__cc.send()");
    await sleep(300);
    ok(/saved on this device/i.test(await s.ev("window.__cc.say()")),
       "person 1: a vote the server cannot take is kept, and says so");
    let q = queueOf(await s.ev("window.__cc.pending()"));
    ok(q.length === 1, "person 1's ballot is held (" + q.length + ")");
    const first = q[0] && q[0].voter;

    ok((await s.ev("window.__cc.nextHidden()")) === false,
       "the hand-off is offered without anybody knowing a URL flag");
    await s.ev("window.__cc.next()");
    q = queueOf(await s.ev("window.__cc.pending()"));
    ok(q.length === 1, "handing the phone on does NOT delete the unsent vote (" + q.length + ")");

    await s.ev('window.__cc.pick("manta-ray")');
    await s.ev("window.__cc.send()");
    await sleep(300);
    q = queueOf(await s.ev("window.__cc.pending()"));
    ok(q.length === 2, "person 2's ballot is held BESIDE person 1's (" + q.length + ")");

    await s.ev("window.__cc.next()");
    await s.ev('window.__cc.pick("whale-shark")');
    await s.ev("window.__cc.send()");
    await sleep(300);
    q = queueOf(await s.ev("window.__cc.pending()"));
    ok(q.length === 3, "all three votes at the table are held (" + q.length + ")");
    const ids = new Set(q.map(b => b && b.voter));
    ok(ids.size === 3, "...each as its own ballot, with its own voter id (" + ids.size + ")");
    ok(first && ids.has(first), "...and person 1's id is still one of them");

    /* The server comes back. Every held ballot must reach it. */
    await s.ev('window.__cc.mode("up")');
    await s.load();
    const got = await waitFor(async () => {
      const r = await s.ev("window.__cc.received()");
      return r && r.length >= 3 ? r : null;
    }, 8000);
    const received = got || (await s.ev("window.__cc.received()"));
    ok(received.length === 3,
       "all three held votes are sent on the next visit (" + received.length + ")");
    const sentIds = new Set(received.map(b => b.voter));
    ok(sentIds.size === 3,
       "...as three ballots, not one overwriting the next (" + sentIds.size + ")");
    const flat = received.map(b => (b.picks || []).join("+")).sort().join(" | ");
    ok(flat === "manta-ray | sardine+lobster | whale-shark",
       "...carrying what each person actually picked: " + flat);
    ok(received.every(b => b.voter && /^[a-f0-9]{16,40}$/.test(b.voter)),
       "...each with a well-formed voter id the server will accept");
    q = queueOf(await s.ev("window.__cc.pending()"));
    ok(q.length === 0, "the holding pen is empty once they are all in (" + q.length + ")");
    ok(/counted/i.test(await s.ev("window.__cc.say()")),
       "...and the page says so: " + (await s.ev("window.__cc.say()")));

    /* ══════════════════════════════════════════════════════════════════
       A VOTE THAT SENDS MUST NOT TAKE SOMEBODY ELSE'S HELD VOTE WITH IT
       ══════════════════════════════════════════════════════════════════ */
    await s.ev("window.__cc.reset()");
    await s.load();
    await s.ev('window.__cc.mode("asleep")');
    await s.ev('window.__cc.pick("sailfish")');
    await s.ev("window.__cc.send()");
    await sleep(300);
    await s.ev("window.__cc.next()");
    await s.ev('window.__cc.mode("up")');
    await s.ev('window.__cc.pick("blue-marlin")');
    await s.ev("window.__cc.send()");
    await sleep(500);
    q = queueOf(await s.ev("window.__cc.pending()"));
    let live = await s.ev("window.__cc.received()");
    ok(live.length === 1 && (live[0].picks || [])[0] === "blue-marlin",
       "a vote sent while the server is up goes straight in");
    ok(q.length === 1 && (q[0].picks || []).join("+") === "sailfish",
       "...and the held vote beside it is untouched: " + JSON.stringify(q));

    await s.load();
    live = await waitFor(async () => {
      const r = await s.ev("window.__cc.received()");
      return r && r.length >= 2 ? r : null;
    }, 8000) || (await s.ev("window.__cc.received()"));
    ok(live.length === 2, "...and it arrives on the next visit (" + live.length + ")");
    ok(new Set(live.map(b => b.voter)).size === 2,
       "...as a second ballot, not an edit of the first");

    /* ══════════════════════════════════════════════════════════════════
       REFUSALS: "busy, try later" keeps the vote; "this ballot is wrong"
       does not keep it forever.
       ══════════════════════════════════════════════════════════════════ */
    await s.ev("window.__cc.reset()");
    await s.load();
    await s.ev('window.__cc.mode("asleep")');
    await s.ev('window.__cc.pick("great-white-shark")');
    await s.ev("window.__cc.send()");
    await sleep(300);
    ok(queueOf(await s.ev("window.__cc.pending()")).length === 1, "a ballot is held");

    await s.ev('window.__cc.mode("429")');
    await s.load();
    await sleep(800);
    q = queueOf(await s.ev("window.__cc.pending()"));
    ok(q.length === 1,
       "a held vote the server was too busy for is KEPT, not thrown away (" + q.length + ")");

    await s.ev('window.__cc.mode("400")');
    await s.load();
    await sleep(800);
    q = queueOf(await s.ev("window.__cc.pending()"));
    ok(q.length === 0,
       "a ballot the server refuses on its merits is not held forever (" + q.length + ")");

    /* ══════════════════════════════════════════════════════════════════
       A DEVICE THAT ALREADY HOLDS THE OLD ONE-SLOT SHAPE still gets its
       vote counted: this ships to phones that have one sitting in them.
       ══════════════════════════════════════════════════════════════════ */
    await s.ev("window.__cc.reset()");
    await s.ev(`window.__cc.seed("cc-plush-pending", JSON.stringify(
        { voter: "abcdef0123456789abcd", picks: ["common-octopus", "sardine"], contact: "" }))`);
    await s.ev('window.__cc.mode("up")');
    await s.load();
    const legacy = await waitFor(async () => {
      const r = await s.ev("window.__cc.received()");
      return r && r.length >= 1 ? r : null;
    }, 8000) || [];
    ok(legacy.length === 1 && legacy[0].voter === "abcdef0123456789abcd",
       "a ballot held in the OLD single-slot shape is still sent, under its own id: " +
       JSON.stringify(legacy[0] || null));
    ok((legacy[0] && (legacy[0].picks || []).join("+")) === "common-octopus+sardine",
       "...with its picks intact");
    ok(queueOf(await s.ev("window.__cc.pending()")).length === 0,
       "...and is then cleared");
    /* ══════════════════════════════════════════════════════════════════
       THE ADDRESS IS THE BALLOT, so several people can vote on one phone:
       no address, or their own. The server decides which ballot a vote is
       (test_plush_vote.py pins that); what only a browser can show is what
       the DEVICE does after each vote, which is the half that erased votes.
       ══════════════════════════════════════════════════════════════════ */
    await s.ev("window.__cc.reset()");
    await s.load();
    await s.ev('window.__cc.mode("up")');

    const voter1 = await s.ev("window.__cc.voter()");
    await s.ev('window.__cc.pick("sardine")');
    await s.ev("window.__cc.send()");
    await sleep(400);
    ok((await s.ev("window.__cc.ballots()")) === 1, "a vote with the box empty counts");
    const voter2 = await s.ev("window.__cc.voter()");
    ok(voter2 && voter2 !== voter1,
       "...and the phone is ready for the next person by itself");
    ok((await s.ev("window.__cc.picked()")).length === 0,
       "...with the last person's picks cleared off the board");
    ok(/next person/i.test(await s.ev("window.__cc.say()")),
       "...and it says so: " + (await s.ev("window.__cc.say()")));

    // Person 2 just taps and sends. No button to find, no URL flag to know.
    await s.ev('window.__cc.pick("lobster")');
    await s.ev("window.__cc.send()");
    await sleep(400);
    ok((await s.ev("window.__cc.ballots()")) === 2,
       "the second person on the same phone is a second vote (" +
       (await s.ev("window.__cc.ballots()")) + ")");

    // Person 3 leaves their own address, so their ballot stays theirs.
    await s.ev('window.__cc.pick("manta-ray")');
    await s.ev('window.__cc.contact("Alex@BlueHarbor.org")');
    await s.ev("window.__cc.send()");
    await sleep(400);
    ok((await s.ev("window.__cc.ballots()")) === 3, "a vote with an address is a third vote");
    ok((await s.ev("window.__cc.contactValue()")) === "alex@blueharbor.org",
       "...the box keeps the address the server kept: " +
       (await s.ev("window.__cc.contactValue()")));
    ok(/update my vote/i.test(await s.ev("window.__cc.sendLabel()")),
       "...and that ballot is still theirs to change: " +
       (await s.ev("window.__cc.sendLabel()")));
    ok((await s.ev("window.__cc.nextHidden()")) === false,
       "...with the hand-off there for when they pass the phone on");

    // ...and changing their mind is not a fourth vote.
    await s.ev('window.__cc.pick("whale-shark")');
    await s.ev("window.__cc.send()");
    await sleep(400);
    ok((await s.ev("window.__cc.ballots()")) === 3,
       "the same address sending again is one person changing their mind (" +
       (await s.ev("window.__cc.ballots()")) + ")");

    // A different address on the same phone is somebody else.
    await s.ev('window.__cc.contact("sam@blueharbor.org")');
    await s.ev("window.__cc.send()");
    await sleep(400);
    ok((await s.ev("window.__cc.ballots()")) === 4,
       "a different address on the same phone is another vote (" +
       (await s.ev("window.__cc.ballots()")) + ")");
    ok((await s.ev("window.__cc.received()")).length === 5,
       "five sends, nothing retried and nothing lost");

    /* THE DEPLOY WINDOW: this page is live on Vercel within a minute and the
       vote server trails it by hours. Two people on one phone, each with their
       own address, must still be two votes on a server that has never heard of
       address-keyed ballots. */
    await s.ev("window.__cc.reset()");
    await s.load();
    await s.ev('window.__cc.mode("old")');
    await s.ev('window.__cc.pick("sardine")');
    await s.ev('window.__cc.contact("alex@blueharbor.org")');
    await s.ev("window.__cc.send()");
    await sleep(400);
    ok((await s.ev("window.__cc.ballots()")) === 1, "person 1 votes with their address");
    await s.ev('window.__cc.contact("sam@blueharbor.org")');
    await s.ev('window.__cc.pick("lobster")');
    await s.ev("window.__cc.send()");
    await sleep(400);
    ok((await s.ev("window.__cc.ballots()")) === 2,
       "a second address on one phone is a second vote even on the OLD server (" +
       (await s.ev("window.__cc.ballots()")) + ")");
    const old1 = (await s.ev("window.__cc.received()"))[0];
    const old2 = (await s.ev("window.__cc.received()"))[1];
    ok(old1.voter !== old2.voter,
       "...because the page opens a new ballot the moment the address changes");

    /* An address nobody can read: the VOTE still counts, and the page says
       the reminder did not take rather than thanking them for nothing. */
    await s.ev("window.__cc.reset()");
    await s.load();
    await s.ev('window.__cc.mode("warn")');
    await s.ev('window.__cc.pick("common-octopus")');
    await s.ev('window.__cc.contact("call me maybe")');
    await s.ev("window.__cc.send()");
    await sleep(400);
    ok((await s.ev("window.__cc.ballots()")) === 1, "the vote is counted anyway");
    ok(/reminder did not/i.test(await s.ev("window.__cc.say()")),
       "...and the voter is told the reminder did not: " +
       (await s.ev("window.__cc.say()")));
    ok(queueOf(await s.ev("window.__cc.pending()")).length === 0,
       "...and nothing is left queued, because it was not a failure to send");

    /* ══════════════════════════════════════════════════════════════════
       THE STANDINGS ARE ALWAYS UP.

       Timothy: "we had people vote before and it is not coming up." They
       had. Twenty-three ballots were on the server, and the page asked for
       them only when `sent.length` was non-zero, meaning only on a browser
       that had voted AND still held its own ballot in localStorage. A first
       visit showed nothing; so did a second visit after voting with the
       address box left empty, because an empty box hands the device to the
       next person and clears the local ballot. The result of the poll was
       invisible from the page that collected it.

       Three things have to hold, and none of them are visible from the
       markup or from the server:
         1. A BROWSER THAT HAS NEVER VOTED sees the standings.
         2. A VOTE DOES NOT EMPTY THEM, it updates them.
         3. A SERVER THAT IS AWAY LEAVES THEM UP. Nothing is ever replaced
            by a blank board or a zero.
       ══════════════════════════════════════════════════════════════════ */
    await s.ev("window.__cc.reset()");
    await s.load();
    await sleep(400);

    // 1. Never voted, and the board is up: the snapshot build.js writes into
    //    index.html carries it before the server has said a word.
    ok((await s.ev("window.__cc.boardOn()")) === true,
       "a browser that has never voted still sees the standings");
    const coldRows = await s.ev("window.__cc.boardRows()");
    ok(coldRows > 0, "...with real rows on it (" + coldRows + ")");
    ok(/vote/.test(await s.ev("window.__cc.boardTotal()")),
       "...and how many votes are in: " + (await s.ev("window.__cc.boardTotal()")));
    ok((await s.ev("window.__cc.tallyCalls()")) >= 1,
       "...and it asked the server too, without being voted on first");

    // 2. A vote updates them. The stub has had no ballots since the reset, so
    //    the live answer is one ballot and the board must follow it exactly,
    //    not keep showing the snapshot.
    await s.ev('window.__cc.pick("mandarin-gobie")');
    await s.ev("window.__cc.send()");
    await sleep(400);
    ok((await s.ev("window.__cc.boardOn()")) === true, "voting does not take the standings down");
    ok(/^1 vote in so far/.test(await s.ev("window.__cc.boardTotal()")),
       "...they follow the server: " + (await s.ev("window.__cc.boardTotal()")));
    ok(/mandarin/i.test(await s.ev("window.__cc.boardTop()")),
       "...and the vote just cast is on the board: " + (await s.ev("window.__cc.boardTop()")));

    // 3. The server goes away. The board must hold what it has: this browser
    //    cached the last live tally, so a cold start shows that and not a
    //    blank panel or a zero.
    await s.ev('window.__cc.mode("asleep")');
    await s.load();
    await sleep(500);
    ok((await s.ev("window.__cc.boardOn()")) === true,
       "a sleeping server leaves the standings up");
    ok((await s.ev("window.__cc.boardRows()")) > 0,
       "...with rows still on them (" + (await s.ev("window.__cc.boardRows()")) + ")");
    ok(/^1 vote in so far/.test(await s.ev("window.__cc.boardTotal()")),
       "...showing the last real figures this browser was given: " +
       (await s.ev("window.__cc.boardTotal()")));

    // A 404, which is the website live and the vote server a deploy behind.
    await s.ev('window.__cc.mode("404")');
    await s.load();
    await sleep(500);
    ok((await s.ev("window.__cc.boardOn()")) === true &&
       (await s.ev("window.__cc.boardRows()")) > 0,
       "a vote server a deploy behind leaves them up too");

    /* ══════════════════════════════════════════════════════════════════
       THE FOLD. The two optional questions are folded away so the pick bar
       stops covering the critters, and that must never hide an address
       somebody has already given from the person who gave it.
       ══════════════════════════════════════════════════════════════════ */
    await s.ev("window.__cc.reset()");
    await s.load();
    await sleep(300);
    ok((await s.ev("window.__cc.moreOpen()")) === false,
       "a first-time voter gets the short bar, with the questions folded");
    await s.ev('window.__cc.pick("sardine")');
    await s.ev('window.__cc.contact("reef@example.com")');
    await s.ev("window.__cc.send()");
    await sleep(400);
    ok((await s.ev("window.__cc.moreOpen()")) === true,
       "an address the server kept is shown, not folded away");
    await s.load();
    await sleep(400);
    ok((await s.ev("window.__cc.contactValue()")) === "reef@example.com",
       "...and it is still there on the next visit: " + (await s.ev("window.__cc.contactValue()")));
    ok((await s.ev("window.__cc.moreOpen()")) === true,
       "...with the fold opened for them, so they can see and change it");
  } catch (err) {
    lines.push("FAIL the suite threw: " + (err && err.message || err));
  } finally {
    await s.stop();
    await web.close();
  }

  console.log(lines.join("\n"));
  const failed = lines.filter(l => l.startsWith("FAIL"));
  console.log(`\n${lines.filter(l => l.startsWith("PASS")).length} passed, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
})();
