#!/usr/bin/env node
/* THE WHOLE CHAIN, FOR REAL: the page in a browser, the real plush_vote.py
 * behind it, and nothing stubbed in between.
 *
 * Run:  node test_plush_end_to_end.js      (needs Google Chrome and python3)
 *
 * WHY THIS EXISTS ON TOP OF THE OTHER TWO SUITES
 * test_plush_vote.py calls cast() directly: real server, no page.
 * test_plush_queue.js drives the real page with fetch() stubbed: real page,
 * no server. Both can pass while the two halves disagree about what a vote
 * is, and "did my friend's vote actually get saved" is a question about the
 * halves TOGETHER. So this one runs the real module behind a real HTTP server,
 * serves the real index.html from the same origin, drives several people
 * through one browser, and then reads the stored ballots back out.
 *
 * The five things it is here to answer, in order:
 *   1. SEVERAL PEOPLE CAN VOTE ON ONE DEVICE, one after another.
 *   2. THE PAGE UPDATES: the standings and the total it shows are the
 *      server's, after every vote.
 *   3. NOTHING OLD IS DELETED. Two ballots are seeded as if cast last week,
 *      one of them with an address, and they are compared field by field at
 *      the end. A vote cast earlier in the run is checked the same way.
 *   4. AN ADDRESS IS SAVED AND LANDS ON THE LIST: contact_rows(), the thing
 *      the admin tab reads, must name everybody who left one.
 *   5. A VOTE WITH NO ADDRESS STILL COUNTS: it is in the tally, and it is
 *      NOT on the list, because they did not ask to be told.
 *
 * The only thing swapped is the host in the page's own API constant, rewritten
 * to this test's server by a one-line fetch wrapper. Same JSON, same statuses,
 * same code on both sides of it.
 *
 * AGAINST AN OLDER SERVER: pass a directory holding another plush_vote.py and
 * it is imported instead of the repo's. Render deploys hours behind this page,
 * so "will the votes at the table be saved RIGHT NOW" is a question about the
 * version Render is actually running, not about main:
 *
 *   mkdir -p /tmp/oldpv && git show <sha>:plush_vote.py > /tmp/oldpv/plush_vote.py
 *   node test_plush_end_to_end.js /tmp/oldpv
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

/* ══════════════════════════════════════════════════════════════════════
   THE SERVER UNDER TEST: the real plush_vote module, the real routes, and
   the Firestore stand-in the Python suite already uses, so there is one
   definition of it. Written to a temp file because it is scaffolding for
   this one test, not a module anybody imports.
   ══════════════════════════════════════════════════════════════════════ */
const SERVER_PY = `
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = sys.argv[1]
sys.path.insert(0, ROOT)
# An alternative module directory goes FIRST, so a plush_vote.py from another
# deploy is the one under test while everything else still comes from the repo.
if len(sys.argv) > 2 and sys.argv[2]:
    sys.path.insert(0, sys.argv[2])
import plush_vote as pv
print("MODULE %s" % os.path.dirname(os.path.abspath(pv.__file__)), flush=True)
from test_plush_vote import FakeDB

DB = FakeDB()

# Two ballots as if they were cast last week, one of them with an address.
# Nothing in this run may change, move or remove either of them.
OLD_A = "aaaaaaaaaaaaaaaaaaaaaaaa"
OLD_B = "bbbbbbbbbbbbbbbbbbbbbbbb"
DB.store["plush_votes/" + OLD_A] = {
    "picks": ["narwhal", "lobster"], "note": "it has the best face", "name": "Jess",
    "contact": "jess@example.com", "contactKind": "email",
    "ip": "198.51.100.5", "created": 1000, "updated": 1000,
}
DB.store["plush_votes/" + OLD_B] = {
    "picks": ["sardine"], "note": "", "name": "",
    "contact": "", "contactKind": "",
    "ip": "198.51.100.6", "created": 1001, "updated": 1001,
}
BEFORE = json.loads(json.dumps(DB.store))

JOINED = []
def signup(email, **kw):
    JOINED.append(email)
    return {"ok": True, "message": "You are on the newsletter list."}

pv.init(get_firestore=lambda: DB, public_signup=signup, newsletter_source="Plushy Vote")

TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
         ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
         ".webp": "image/webp", ".woff2": "font/woff2", ".json": "application/json",
         ".m4a": "audio/mp4", ".mp4": "video/mp4", ".ico": "image/x-icon"}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/__dump":
            return self._send_json({
                "ballots": {k.split("/", 1)[1]: v for k, v in DB.store.items()
                            if k.startswith("plush_votes/")},
                "before": {k.split("/", 1)[1]: v for k, v in BEFORE.items()},
                "contacts": pv.contact_rows(),
                "tally": pv.tally(force=True),
                "joined": JOINED,
            })
        if pv.handle_get(self, parsed):
            return
        if parsed.path.startswith("/api/"):
            return self._send_json({})          # the page's other calls
        rel = parsed.path.lstrip("/") or "index.html"
        full = os.path.join(ROOT, rel)
        if not os.path.abspath(full).startswith(os.path.abspath(ROOT)) or not os.path.isfile(full):
            self.send_response(404); self.end_headers(); return
        with open(full, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(os.path.splitext(full)[1].lower(),
                                                   "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}
        if pv.handle_post(self, parsed, body):
            return
        self._send_json({"ok": False, "error": "no_route"}, status=404)

srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
print("PORT %d" % srv.server_address[1], flush=True)
srv.serve_forever()
`;

/* The one thing swapped: the page's API host, pointed at this server. */
const BOOT = `
(() => {
  const real = window.fetch.bind(window);
  window.fetch = (u, o) => real(
    String(u).replace("https://play.currentsandcritters.com", ""), o);
  window.__e2e = {
    pick: slug => document.querySelector('.plush-pick[data-f="' + slug + '"]').click(),
    send: () => document.getElementById("plushSend").click(),
    next: () => document.getElementById("plushNext").click(),
    contact: v => {
      const box = document.getElementById("plushContact");
      box.value = v;
      box.dispatchEvent(new Event("input"));
    },
    news: on => {
      const box = document.getElementById("plushNews");
      box.checked = !!on;
      box.dispatchEvent(new Event("change"));
    },
    say: () => document.getElementById("plushSay").textContent,
    total: () => document.getElementById("plushTotal").textContent,
    board: () => Array.prototype.map.call(
      document.querySelectorAll(".plush-row"),
      r => r.querySelector(".who").textContent + "=" + r.querySelector(".n").textContent),
    sendLabel: () => document.getElementById("plushSend").textContent,
    voter: () => localStorage.getItem("cc-plush-voter"),
    contactValue: () => document.getElementById("plushContact").value,
  };
})();
`;

/* ── harness ──────────────────────────────────────────────────────────── */
function getText(url, timeoutMs = 8000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, res => {
      let b = ""; res.on("data", d => (b += d)); res.on("end", () => resolve(b));
    });
    req.on("error", reject);
    req.setTimeout(timeoutMs, () => req.destroy(new Error("timeout")));
  });
}

async function waitFor(fn, ms, every = 120) {
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
  async ev(expr) {
    const r = await this.send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true }, 30000);
    if (!r || !r.result) throw new Error("no answer: " + expr.slice(0, 50));
    if (r.result.exceptionDetails) {
      throw new Error("page threw: " +
        JSON.stringify(r.result.exceptionDetails.exception || {}).slice(0, 240));
    }
    return r.result.result ? r.result.result.value : undefined;
  }
  close() { try { this.ws.close(); } catch (_) {} }
}

(async () => {
  if (!CHROME) { console.log("SKIP: no Chrome/Chromium found"); process.exit(0); }

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cce2e-"));
  const serverFile = path.join(tmp, "plush_e2e_server.py");
  fs.writeFileSync(serverFile, SERVER_PY);

  const MODULE_DIR = process.argv[2] || "";
  const py = spawn("python3", [serverFile, ROOT, MODULE_DIR],
                   { stdio: ["ignore", "pipe", "pipe"] });
  let stderr = "";
  py.stderr.on("data", d => (stderr += d));
  let moduleFrom = "";
  const port = await new Promise((resolve, reject) => {
    let buf = "";
    py.stdout.on("data", d => {
      buf += d;
      const seen = buf.match(/MODULE (.+)/);
      if (seen) moduleFrom = seen[1].trim();
      const m = buf.match(/PORT (\d+)/);
      if (m) resolve(Number(m[1]));
    });
    py.on("exit", () => reject(new Error("the server died: " + stderr.slice(0, 400))));
    setTimeout(() => reject(new Error("the server never printed a port: " + stderr.slice(0, 400))), 15000);
  });

  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "cce2ep-"));
  const dbg = 9700 + (process.pid % 50) * 5;
  const chrome = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    `--user-data-dir=${profile}`, "--window-size=900,1200",
    `--remote-debugging-port=${dbg}`, "about:blank",
  ], { stdio: "ignore" });

  let cdp = null;
  const dump = async () => JSON.parse(await getText(`http://127.0.0.1:${port}/__dump`));

  try {
    const target = await waitFor(async () => {
      const list = JSON.parse(await getText(`http://127.0.0.1:${dbg}/json/list`));
      return list.find(t => t.type === "page" && t.webSocketDebuggerUrl) || null;
    }, 25000);
    if (!target) throw new Error("Chrome never came up");
    cdp = await Cdp.open(target.webSocketDebuggerUrl);
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Page.addScriptToEvaluateOnNewDocument", { source: BOOT });
    await cdp.send("Page.navigate", { url: `http://127.0.0.1:${port}/index.html` });
    const ready = await waitFor(() => cdp.ev(
      "!!document.querySelector('.plush-pick') && !!window.__e2e"), 25000);
    if (!ready) throw new Error("the page never finished loading");

    lines.push("     (plush_vote.py under test: " + (moduleFrom || "the repo") + ")");
    const start = await dump();
    ok(Object.keys(start.ballots).length === 2, "two ballots were already in the box");

    /* ── 1. three people, one device ──────────────────────────────────── */
    // Person 1 leaves no address at all.
    const v1 = await cdp.ev("window.__e2e.voter()");
    await cdp.ev('window.__e2e.pick("manta-ray"); window.__e2e.pick("whale-shark")');
    await cdp.ev("window.__e2e.send()");
    await waitFor(async () => /counted/i.test(await cdp.ev("window.__e2e.say()")), 10000);
    let d = await dump();
    ok(Object.keys(d.ballots).length === 3,
       "person 1 votes with no address and it is saved (" +
       Object.keys(d.ballots).length + " ballots)");
    ok(d.tally.counts["manta-ray"] === 1 && d.tally.counts["whale-shark"] === 1,
       "...both their picks are counted");
    ok(/3 votes in so far/.test(await cdp.ev("window.__e2e.total()")),
       "...and the page says so: " + (await cdp.ev("window.__e2e.total()")));
    const v2 = await cdp.ev("window.__e2e.voter()");
    ok(v2 && v2 !== v1, "...and the phone is ready for the next person");

    // Person 2 leaves their email.
    await cdp.ev('window.__e2e.pick("great-white-shark")');
    await cdp.ev('window.__e2e.contact("Sam@Example.com")');
    await cdp.ev("window.__e2e.send()");
    await waitFor(async () => /counted/i.test(await cdp.ev("window.__e2e.say()")), 10000);
    d = await dump();
    ok(Object.keys(d.ballots).length === 4,
       "person 2 votes with their email on the SAME phone (" +
       Object.keys(d.ballots).length + " ballots)");
    ok(d.contacts.some(r => r.contact === "sam@example.com"),
       "...and their address is on the list: " +
       d.contacts.map(r => r.contact).join(", "));
    ok((await cdp.ev("window.__e2e.contactValue()")) === "sam@example.com",
       "...the box holds what the server kept");
    ok(/update my vote/i.test(await cdp.ev("window.__e2e.sendLabel()")),
       "...and that ballot is theirs to change");

    // Person 3 takes the phone: the hand-off, then their own email.
    await cdp.ev("window.__e2e.next()");
    await cdp.ev('window.__e2e.pick("sardine")');
    await cdp.ev('window.__e2e.contact("alex@blueharbor.org")');
    await cdp.ev("window.__e2e.send()");
    await waitFor(async () => /counted/i.test(await cdp.ev("window.__e2e.say()")), 10000);
    d = await dump();
    ok(Object.keys(d.ballots).length === 5,
       "person 3 votes with a different email (" +
       Object.keys(d.ballots).length + " ballots)");
    ok(d.contacts.some(r => r.contact === "alex@blueharbor.org"),
       "...and their address is on the list too");
    ok(d.tally.counts["sardine"] === 2,
       "...their pick is counted beside the ballot that was already in the box: sardine=" +
       d.tally.counts["sardine"]);

    /* ── 2. the page shows the server's numbers ───────────────────────── */
    ok(/5 votes in so far/.test(await cdp.ev("window.__e2e.total()")),
       "the total on screen is the server's: " + (await cdp.ev("window.__e2e.total()")));
    const board = await cdp.ev("window.__e2e.board()");
    ok(board.some(r => /^Sardine=2/.test(r)),
       "the standings on screen are the server's: " + board.join(" "));

    /* ── 3. changing your mind is not another vote ────────────────────── */
    await cdp.ev('window.__e2e.pick("lobster")');
    await cdp.ev("window.__e2e.send()");
    await waitFor(async () => /updated/i.test(await cdp.ev("window.__e2e.say()")), 10000);
    d = await dump();
    ok(Object.keys(d.ballots).length === 5,
       "the same address sending again is still five votes (" +
       Object.keys(d.ballots).length + ")");
    ok(d.contacts.filter(r => r.contact === "alex@blueharbor.org").length === 1,
       "...and on the list once, not twice");

    /* ── 4. nothing that was already in the box moved ─────────────────── */
    const untouched = Object.keys(start.ballots).filter(id =>
      JSON.stringify(d.ballots[id]) === JSON.stringify(start.ballots[id]));
    ok(untouched.length === 2,
       "both older ballots are byte for byte what they were (" + untouched.length + "/2)");
    ok(d.contacts.some(r => r.contact === "jess@example.com"),
       "...and the address one of them carried is still on the list");
    const jess = d.contacts.find(r => r.contact === "jess@example.com") || {};
    ok(jess.name === "Jess" && String(jess.note || "").indexOf("best face") > -1,
       "...with the name and the note they left: " + JSON.stringify(jess.note || ""));

    /* ── 5. the vote with no address counts, and is not on the list ──── */
    const anon = Object.entries(d.ballots).find(([id, b]) =>
      id === v1 && !b.contact);
    ok(!!anon, "person 1's ballot is stored under their own id with no address");
    ok(anon && anon[1].picks.join("+") === "manta-ray+whale-shark",
       "...with their picks: " + (anon ? anon[1].picks.join("+") : "gone"));
    ok(!d.contacts.some(r => !r.contact),
       "...and nobody without an address is on the list to be told");
    ok(d.contacts.length === 3,
       "the list is exactly the three who asked to be told (" + d.contacts.length + ")");
    ok(d.tally.ballots === 5 && Object.keys(d.ballots).length === 5,
       "and the count the page reads is the number of ballots stored");

    /* ── the newsletter tick is still its own, separate answer ────────── */
    await cdp.ev("window.__e2e.next()");
    await cdp.ev('window.__e2e.pick("humuhumunukunukuapuaa")');
    await cdp.ev('window.__e2e.contact("newsy@example.com")');
    await cdp.ev("window.__e2e.news(true)");
    await cdp.ev("window.__e2e.send()");
    await waitFor(async () => /counted/i.test(await cdp.ev("window.__e2e.say()")), 10000);
    d = await dump();
    ok(d.joined.indexOf("newsy@example.com") > -1,
       "ticking the newsletter box signs that address up: " + JSON.stringify(d.joined));
    ok(d.contacts.some(r => r.contact === "newsy@example.com"),
       "...and they are on the who-to-tell list as well");
    ok(Object.keys(d.ballots).length === 6, "...and their vote is the sixth ballot");
  } catch (err) {
    lines.push("FAIL the suite threw: " + (err && err.message || err));
  } finally {
    if (cdp) cdp.close();
    try { chrome.kill("SIGKILL"); } catch (_) {}
    try { py.kill("SIGKILL"); } catch (_) {}
    await sleep(200);
    for (const dir of [tmp, profile]) {
      try { fs.rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 150 }); }
      catch (_) {}
    }
  }

  console.log(lines.join("\n"));
  const failed = lines.filter(l => l.startsWith("FAIL"));
  console.log(`\n${lines.filter(l => l.startsWith("PASS")).length} passed, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
})();
