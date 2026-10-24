#!/usr/bin/env node
/* The four live numbers on the homepage: games, players, hours, donated.
 *
 * Run:  node test_site_stats.js        (needs Google Chrome)
 *
 * WHY THIS EXISTS
 * On 2026-10-24 the homepage showed 101 games, 18 players and 0 hours while
 * the server was serving 561 games, 106 players and 916 hours. The tile had
 * ALREADY animated to 561 and was then walked back down to 101, which a frame
 * by frame trace of the live page caught:
 *
 *     1080ms -> 561     the real number, animated in correctly
 *     1164ms -> 94      the floor's own animation, still running
 *     1497ms -> 101     and finishing last, so the stale number won
 *
 * animateTo() started a requestAnimationFrame loop per call and cancelled
 * nothing, so two loops wrote the same element and the SLOWER one decided the
 * answer. The floor animation is 1400ms and the live one 600ms, so the faster
 * the server answered, the more certainly its number was thrown away. Nothing
 * in the page looked broken; the number was just quietly wrong, and had been
 * for as long as the server was quick.
 *
 * So this drives the real index.html against a stubbed /api/stats, with the
 * timing that caused it, and checks the numbers that are on screen at the end:
 *
 *   1. AN INSTANT ANSWER WINS. The exact race above.
 *   2. A SLOW ANSWER WINS TOO, and a dead one leaves the floors.
 *   3. THE FLOORS ARE REAL NUMBERS. The server hibernates, so the floor is
 *      what most first-time visitors actually see. Hours had no floor at all
 *      and showed a flat 0.
 *   4. NOTHING WALKS BACKWARDS. A server whose own counter has reset (which
 *      has happened: its stats file lives on a disk that is not in force)
 *      must not drag the page below the floor.
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

/* ── what the server is really serving, at the time of writing ─────────── */
const REAL = { games: 561, players: 106, seconds: 3297600, hours: 916 };

/* ══════════════════════════════════════════════════════════════════════
   1. THE FLOORS, read out of the page itself
   ══════════════════════════════════════════════════════════════════════ */
function floorOf(name) {
  const m = INDEX.match(new RegExp("const " + name + "\\s*=\\s*(\\d+)"));
  return m ? Number(m[1]) : null;
}
const FLOORS = {
  games: floorOf("FLOOR_GAMES"),
  players: floorOf("FLOOR_PLAYERS"),
  hours: floorOf("FLOOR_HOURS"),
};
ok(FLOORS.games !== null && FLOORS.players !== null,
   "the page still carries a games and a players floor");
ok(FLOORS.hours !== null,
   "...and an HOURS floor, which it did not have while it was publishing 0");
// A floor below the real figure publishes a number that is simply wrong to
// every visitor who arrives while the free-tier server is asleep, which is
// most of them.
ok(FLOORS.games >= REAL.games,
   "the games floor is not below what the server serves: " + FLOORS.games + " vs " + REAL.games);
ok(FLOORS.players >= REAL.players,
   "the players floor is not below it either: " + FLOORS.players + " vs " + REAL.players);
ok(FLOORS.hours >= REAL.hours,
   "nor the hours floor: " + FLOORS.hours + " vs " + REAL.hours);

/* ══════════════════════════════════════════════════════════════════════
   2. BUILD.JS KEEPS THEM FRESH, which is the only reason they will still be
      true next month. It runs on every Vercel deploy, asks the live server
      and writes the answer in: upward only, and never failing the build.
   ══════════════════════════════════════════════════════════════════════ */
function buildWith(stats) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ccbuild-"));
  fs.copyFileSync(path.join(ROOT, "index.html"), path.join(dir, "index.html"));
  fs.copyFileSync(path.join(ROOT, "build.js"), path.join(dir, "build.js"));
  return new Promise(resolve => {
    const api = http.createServer((req, res) => {
      if (req.url.split("?")[0] !== "/api/stats" || stats === null) {
        res.statusCode = 503; return res.end("asleep");
      }
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify(stats));
    });
    api.listen(0, "127.0.0.1", () => {
      const env = Object.assign({}, process.env,
        { CC_RENDER_URL: `http://127.0.0.1:${api.address().port}` });
      const run = spawn("node", ["build.js"], { cwd: dir, env, stdio: ["ignore", "pipe", "pipe"] });
      let log = "";
      run.stdout.on("data", d => (log += d));
      run.stderr.on("data", d => (log += d));
      run.on("exit", code => {
        const out = fs.readFileSync(path.join(dir, "index.html"), "utf8");
        api.close(() => {
          try { fs.rmSync(dir, { recursive: true, force: true, maxRetries: 3 }); } catch (_) {}
          resolve({
            code,
            log,
            floors: {
              games: Number((out.match(/const FLOOR_GAMES\s*=\s*(\d+)/) || [])[1]),
              players: Number((out.match(/const FLOOR_PLAYERS\s*=\s*(\d+)/) || [])[1]),
              hours: Number((out.match(/const FLOOR_HOURS\s*=\s*(\d+)/) || [])[1]),
            },
          });
        });
      });
    });
  });
}

/* ══════════════════════════════════════════════════════════════════════
   3. THE PAGE, in a browser, against a stubbed server
   ══════════════════════════════════════════════════════════════════════ */
function serve(stats, delayMs) {
  const TYPES = {
    ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
    ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
    ".webp": "image/webp", ".woff2": "font/woff2", ".json": "application/json",
    ".m4a": "audio/mp4", ".mp4": "video/mp4", ".ico": "image/x-icon",
  };
  const server = http.createServer((req, res) => {
    const url = req.url.split("?")[0];
    const json = (obj, ms) => setTimeout(() => {
      const body = JSON.stringify(obj);
      res.setHeader("Content-Type", "application/json");
      res.setHeader("Cache-Control", "no-store");
      res.end(body);
    }, ms || 0);
    if (url === "/api/stats") {
      if (stats === null) return;                  // the hibernating server
      return json(stats, delayMs);
    }
    if (url.startsWith("/api/")) return json(url.indexOf("wall") > -1 ? [] : {});
    const rel = decodeURIComponent(url).replace(/^\/+/, "") || "index.html";
    const file = path.join(ROOT, rel);
    if (!file.startsWith(ROOT) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.statusCode = 404; return res.end("no");
    }
    res.setHeader("Content-Type", TYPES[path.extname(file).toLowerCase()] || "application/octet-stream");
    res.end(fs.readFileSync(file));
  });
  return new Promise(r => server.listen(0, "127.0.0.1", () => r({
    port: server.address().port,
    close: () => new Promise(d => server.close(() => d())),
  })));
}

const BOOT = port => `
(() => {
  const real = window.fetch.bind(window);
  window.fetch = (u, o) => real(
    String(u).replace("https://play.currentsandcritters.com", "http://127.0.0.1:${port}"), o);
  window.__tiles = () => Object.fromEntries(
    Array.from(document.querySelectorAll("[data-placeholder]"))
      .map(e => [e.dataset.placeholder, e.textContent.trim()]));
})();
`;

function getText(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, res => {
      let b = ""; res.on("data", d => (b += d)); res.on("end", () => resolve(b));
    });
    req.on("error", reject);
    req.setTimeout(6000, () => req.destroy(new Error("timeout")));
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

async function tilesAfter({ stats, delayMs, settleMs }) {
  const web = await serve(stats, delayMs);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "ccstats-"));
  const dbg = 9940 + (process.pid % 20) + Math.floor(Math.random() * 10);
  const chrome = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    `--user-data-dir=${profile}`, "--window-size=1200,900",
    `--remote-debugging-port=${dbg}`, "about:blank",
  ], { stdio: "ignore" });

  let ws = null;
  try {
    const target = await waitFor(async () => {
      const list = JSON.parse(await getText(`http://127.0.0.1:${dbg}/json/list`));
      return list.find(t => t.type === "page" && t.webSocketDebuggerUrl) || null;
    }, 25000);
    if (!target) throw new Error("Chrome never came up");
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      ws.addEventListener("open", res, { once: true });
      ws.addEventListener("error", rej, { once: true });
      setTimeout(() => rej(new Error("CDP timeout")), 15000);
    });
    let id = 0;
    const waiting = new Map();
    ws.addEventListener("message", ev => {
      let m; try { m = JSON.parse(ev.data); } catch (_) { return; }
      const w = waiting.get(m.id);
      if (w) { waiting.delete(m.id); w(m); }
    });
    const send = (method, params) => new Promise(res => {
      const i = ++id;
      const t = setTimeout(() => { waiting.delete(i); res(null); }, 40000);
      waiting.set(i, m => { clearTimeout(t); res(m); });
      ws.send(JSON.stringify({ id: i, method, params: params || {} }));
    });
    const ev = async expr => {
      const r = await send("Runtime.evaluate",
        { expression: expr, returnByValue: true, awaitPromise: true });
      return r && r.result && r.result.result ? r.result.result.value : null;
    };
    await send("Page.enable");
    await send("Runtime.enable");
    await send("Page.addScriptToEvaluateOnNewDocument", { source: BOOT(web.port) });
    await send("Page.navigate", { url: `http://127.0.0.1:${web.port}/index.html` });
    await waitFor(() => ev("!!window.__tiles && !!document.querySelector('[data-placeholder]')"), 25000);
    await sleep(settleMs);
    const out = await ev("JSON.stringify(window.__tiles())");
    return JSON.parse(out || "{}");
  } finally {
    try { if (ws) ws.close(); } catch (_) {}
    try { chrome.kill("SIGKILL"); } catch (_) {}
    await sleep(150);
    try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 150 }); }
    catch (_) {}
    await web.close();
  }
}

const num = v => Number(String(v || "").replace(/[^0-9.]/g, ""));

(async () => {
  if (!CHROME) {
    console.log(lines.join("\n"));
    console.log("\nSKIP: no Chrome/Chromium found, the browser half did not run.");
    process.exit(lines.some(l => l.startsWith("FAIL")) ? 1 : 0);
  }
  const live = { ok: true, games_played: REAL.games, players_total: REAL.players,
                 registered_players: 77, play_seconds: REAL.seconds };

  try {
    /* ── build.js, before the browser work ──────────────────────────── */
    let b = await buildWith({ ok: true, games_played: 999, players_total: 150,
                              registered_players: 90, play_seconds: 3600 * 1234 });
    ok(b.code === 0 && b.floors.games === 999 && b.floors.players === 150 &&
       b.floors.hours === 1234,
       "a deploy writes the live numbers into the floors: " + JSON.stringify(b.floors));

    b = await buildWith({ ok: true, games_played: 12, players_total: 2, play_seconds: 60 });
    ok(b.code === 0 && b.floors.games === FLOORS.games &&
       b.floors.players === FLOORS.players && b.floors.hours === FLOORS.hours,
       "a server whose counter has reset cannot lower them: " + JSON.stringify(b.floors));

    b = await buildWith(null);
    ok(b.code === 0 && b.floors.games === FLOORS.games && b.floors.hours === FLOORS.hours,
       "a sleeping server leaves them committed and does not fail the build: " +
       JSON.stringify(b.floors));
    ok(/left as committed|stays/.test(b.log), "...and the build log says so");

    /* ── the race that lost the real numbers ────────────────────────── */
    let t = await tilesAfter({ stats: live, delayMs: 0, settleMs: 3000 });
    ok(num(t.games) === REAL.games,
       "an instant answer survives the floor animation: games=" + t.games);
    ok(num(t.players) === REAL.players, "...players=" + t.players);
    ok(num(t.hours) === REAL.hours, "...hours=" + t.hours);

    /* ── and a slow one, which is the case that used to work ────────── */
    t = await tilesAfter({ stats: live, delayMs: 2000, settleMs: 4000 });
    ok(num(t.games) === REAL.games && num(t.hours) === REAL.hours,
       "a slow answer lands too: games=" + t.games + " hours=" + t.hours);

    /* ── the server asleep: the floors are what a visitor sees ──────── */
    t = await tilesAfter({ stats: null, delayMs: 0, settleMs: 3000 });
    ok(num(t.games) === FLOORS.games && num(t.players) === FLOORS.players,
       "a hibernating server leaves the floors: games=" + t.games + " players=" + t.players);
    ok(num(t.hours) === FLOORS.hours && num(t.hours) > 0,
       "...including a real hours figure, not a 0: hours=" + t.hours);

    /* ── a server whose counter has reset must not drag the page down ─ */
    t = await tilesAfter({
      stats: { ok: true, games_played: 101, players_total: 18, play_seconds: 0 },
      delayMs: 0, settleMs: 3000 });
    ok(num(t.games) >= FLOORS.games,
       "a reset server does not walk the games count backwards: " + t.games);
    ok(num(t.players) >= FLOORS.players, "...nor the players: " + t.players);
    ok(num(t.hours) >= FLOORS.hours, "...nor the hours: " + t.hours);
  } catch (err) {
    lines.push("FAIL the suite threw: " + (err && err.message || err));
  }

  console.log(lines.join("\n"));
  const failed = lines.filter(l => l.startsWith("FAIL"));
  console.log(`\n${lines.filter(l => l.startsWith("PASS")).length} passed, ${failed.length} failed`);
  process.exit(failed.length ? 1 : 0);
})();
