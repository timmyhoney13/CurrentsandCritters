#!/usr/bin/env node
/* Tutorials: short videos, nothing to click through.
 *
 * The tutorials were click-through tours (a popup per step, and a rigged game
 * the player had to drive to reach the next one). They are videos now, each
 * about a minute, and Competitive 1v1 is no longer one of them. This file
 * holds that in place.
 *
 *   • SOURCE: js/tutorials.js lists the videos and nothing else; every file it
 *     names is really in multiplayer/client/video under a name the server's
 *     /video route accepts; every video is short (read from the MP4 itself,
 *     not from the label), its label says how long it really is, and its
 *     captions are real WebVTT with no em dashes (captions are on screen).
 *   • DRIVE: the real preview.html, CSS and preview-app.js in headless Chrome,
 *     as a guest. Open the chooser, see eight videos in three groups (the main menu on its own first), play one,
 *     check it really plays and hushes the lobby music, run it to the end, and
 *     see it ticked off and remembered. At a laptop width and a phone width.
 *
 * Run:  node test_tutorials.js            (drive half needs Google Chrome)
 *       node test_tutorials.js source     (source half only)
 */
"use strict";

const fs   = require("fs");
const os   = require("os");
const path = require("path");
const http = require("http");
const { spawn } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const TUT    = read("js/tutorials.js");
const HTML   = read("preview.html");
const APP    = read("js/preview-app.js");
const SERVER = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (detail ? `  (${detail})` : "")); }
}
function done() {
  console.log("\n" + "=".repeat(42));
  console.log(`RESULT: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

// ── The list, read out of the source ───────────────────────────────────
const LIST_AT = TUT.indexOf("const VIDEOS = [");
const LIST_END = TUT.indexOf("];", LIST_AT);
const LIST = LIST_AT >= 0 ? TUT.slice(LIST_AT, LIST_END) : "";
const VIDEOS = [];
for (const m of LIST.matchAll(/\{\s*key:\s*"([^"]+)",\s*group:\s*"([^"]+)",\s*file:\s*"([^"]+)",\s*len:\s*"([^"]+)",\s*title:\s*"([^"]+)",\s*desc:\s*"([^"]+)"\s*\}/g)) {
  VIDEOS.push({ key: m[1], group: m[2], file: m[3], len: m[4], title: m[5], desc: m[6] });
}

// Length of an MP4 in seconds, from its mvhd box (no ffprobe needed).
function mp4Seconds(file) {
  const b = fs.readFileSync(file);
  const at = b.indexOf("mvhd");
  if (at < 0) return NaN;
  const v = b[at + 4];
  if (v === 1) return Number(b.readBigUInt64BE(at + 4 + 4 + 16 + 4)) / b.readUInt32BE(at + 4 + 4 + 16);
  return b.readUInt32BE(at + 4 + 4 + 8 + 4) / b.readUInt32BE(at + 4 + 4 + 8);
}
const mmss = s => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

console.log("\nthe tutorials are videos");
check("js/tutorials.js lists its videos", VIDEOS.length > 0);
check("eight of them", VIDEOS.length === 8, `${VIDEOS.length}`);
check("the click-through tours are gone (no coach, no spotlight, no steps)",
      !/runCoach|#tut3-coach|tut3-hole|_STEPS\s*=/.test(TUT));
check("no tutorial drives a rigged game any more",
      !/__ccTutorialGame|__ccTutorialVariant/.test(TUT));
check("Competitive 1v1 is not a tutorial", !/competitive/i.test(TUT));
check("every key is different", new Set(VIDEOS.map(v => v.key)).size === VIDEOS.length);
check("the Osprey needs every one of them",
      /function allDone\(\) \{ return doneCount\(\) === TOTAL; \}/.test(TUT) && /const KEYS = VIDEOS\.map\(v => v\.key\)/.test(TUT));
check("the achievement says how many",
      APP.includes(`label:"Watch all ${VIDEOS.length} tutorial videos."`));
check("the How to Play tab no longer promises a dealt hand",
      !/The tutorial deals you a real hand/.test(APP) && /Watch the tutorials/.test(APP));
check("a video quiets the lobby's theme song, through the app's own hook",
      /window\.__ccHushTheme = \(on\) =>/.test(APP) && /window\.__ccHushTheme\(on\)/.test(TUT));

console.log("\nevery file is there, and short");
const ROUTE = /re\.fullmatch\(r"\/video\/\(\[a-z0-9_-\]\+\\\.\(\?:mp4\|jpg\|vtt\)\)"/.test(SERVER);
check("the server's /video route is the one this test assumes", ROUTE);
const MAX = 75;
for (const v of VIDEOS) {
  const base = path.join(CLIENT, "video", v.file);
  const ok = ["mp4", "jpg", "vtt"].every(x => fs.existsSync(`${base}.${x}`));
  check(`${v.file}: mp4, poster and captions are all in client/video`, ok);
  if (!ok) continue;
  check(`${v.file}: a name the /video route serves`, /^[a-z0-9_-]+$/.test(v.file));
  const secs = mp4Seconds(`${base}.mp4`);
  check(`${v.file}: ${secs.toFixed(1)}s, not too long (${MAX}s at most)`, secs > 20 && secs <= MAX);
  check(`${v.file}: the label "${v.len}" is its real length`, Math.abs(secs - (+v.len.split(":")[0] * 60 + +v.len.split(":")[1])) <= 1, mmss(secs));
  const vtt = fs.readFileSync(`${base}.vtt`, "utf8");
  const cues = (vtt.match(/-->/g) || []).length;
  check(`${v.file}: real WebVTT captions (${cues} lines)`, vtt.startsWith("WEBVTT") && cues >= 5);
  const last = [...vtt.matchAll(/--> (\d+):(\d+):([\d.]+)/g)].pop();
  const lastEnd = last ? +last[1] * 3600 + +last[2] * 60 + +last[3] : 0;
  check(`${v.file}: the captions finish inside the video`, lastEnd > 0 && lastEnd <= secs + 0.05, `${lastEnd} vs ${secs}`);
  check(`${v.file}: no em dash on screen`, !/—/.test(vtt + v.title + v.desc));
  const mb = fs.statSync(`${base}.mp4`).size / 1e6;
  check(`${v.file}: ${mb.toFixed(1)} MB, light enough to stream`, mb < 15);
}

console.log("\nthe page");
check("preview.html still loads js/tutorials.js", /"\/js\/tutorials\.js\?v=[^"]+"/.test(HTML));
check("the Tutorial tile still opens the chooser", /window\.__openTutorialChooser\(\)/.test(APP));
check("no emoji in the chooser (the game has none)",
      !/[\u{1F300}-\u{1FAFF}]/u.test(TUT));

if (process.argv[2] === "source") done();

// ════════════════════════════════════════════════════════════════════════
//  DRIVE
// ════════════════════════════════════════════════════════════════════════
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome", "/usr/bin/chromium",
].find(p => fs.existsSync(p));
if (!CHROME) { console.log("\nSKIP: no Chrome found, the chooser was not driven."); done(); }

const PORT = 9411 + (process.pid % 60);
const DEBUG_BASE = 9611 + (process.pid % 40) * 4;
const sleep = ms => new Promise(r => setTimeout(r, ms));
function get(url, timeoutMs = 4000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, res => { let b = ""; res.on("data", d => b += d); res.on("end", () => resolve(b)); });
    req.on("error", reject);
    req.setTimeout(timeoutMs, () => req.destroy(new Error("timeout")));
  });
}
async function waitFor(fn, ms, every = 250) {
  const until = Date.now() + ms;
  while (Date.now() < until) { try { const v = await fn(); if (v) return v; } catch (_) {} await sleep(every); }
  return null;
}
class Cdp {
  constructor(ws) { this.ws = ws; this.id = 0; this.waiting = new Map(); }
  static async open(url) {
    const ws = new WebSocket(url), c = new Cdp(ws);
    ws.addEventListener("message", ev => { let m; try { m = JSON.parse(ev.data); } catch (_) { return; } const w = c.waiting.get(m.id); if (w) { c.waiting.delete(m.id); w(m); } });
    await new Promise((res, rej) => { ws.addEventListener("open", res, { once: true }); ws.addEventListener("error", rej, { once: true }); setTimeout(() => rej(new Error("CDP timeout")), 15000); });
    return c;
  }
  send(method, params, timeoutMs = 20000) {
    const id = ++this.id;
    return new Promise(resolve => {
      const t = setTimeout(() => { this.waiting.delete(id); resolve(null); }, timeoutMs);
      this.waiting.set(id, m => { clearTimeout(t); resolve(m); });
      try { this.ws.send(JSON.stringify({ id, method, params: params || {} })); } catch (_) { clearTimeout(t); resolve(null); }
    });
  }
  async eval(expr) {
    const r = await this.send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    return r && r.result && r.result.result ? r.result.result.value : undefined;
  }
  close() { try { this.ws.close(); } catch (_) {} }
}

// The real page, with the API base pointed at nothing: a guest needs no server.
const tmp = [];
function writeTmp(name, body) { const f = path.join(CLIENT, name); fs.writeFileSync(f, body); tmp.push(f); return name; }
writeTmp("_tutv_app.js", APP.replace(/window\.__FISH_API_BASE__\s*=\s*"[^"]*";/, `window.__FISH_API_BASE__ = "http://127.0.0.1:9";`));
const PAGE = writeTmp("_tutv_page.html", HTML
  .replace('<script defer src="/js/device-select.js', `<script>try{sessionStorage.setItem("cc_device_type","computer");}catch(e){}</script>\n<script defer src="/js/device-select.js`)
  .replace(/\/js\/preview-app\.js\?[^"]*/, "/_tutv_app.js"));

const SERVER_SRC = `
  const fs=require("fs"),path=require("path"),http=require("http");
  const ROOT=${JSON.stringify(CLIENT)};
  const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",".json":"application/json",".png":"image/png",
    ".jpg":"image/jpeg",".webp":"image/webp",".svg":"image/svg+xml",".m4a":"audio/mp4",".mp4":"video/mp4",".vtt":"text/vtt",".woff2":"font/woff2"};
  http.createServer((req,res)=>{
    const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
    const f=path.join(ROOT,rel);
    if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);res.end();return;}
    const size=fs.statSync(f).size, type=MIME[path.extname(f)]||"application/octet-stream";
    const m=/bytes=(\\d*)-(\\d*)/.exec(req.headers.range||"");
    if(m){const s=m[1]?+m[1]:0,e=m[2]?Math.min(+m[2],size-1):size-1;
      res.writeHead(206,{"Content-Type":type,"Accept-Ranges":"bytes","Content-Range":"bytes "+s+"-"+e+"/"+size,"Content-Length":e-s+1});
      fs.createReadStream(f,{start:s,end:e}).pipe(res);return;}
    res.writeHead(200,{"Content-Type":type,"Accept-Ranges":"bytes","Content-Length":size});
    fs.createReadStream(f).pipe(res);
  }).listen(${PORT});`;

let seq = 0;
async function drive(w, h, label) {
  const port = DEBUG_BASE + (seq++);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "tutv-"));
  const chrome = spawn(CHROME, ["--headless=new", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
    "--disable-extensions", "--autoplay-policy=no-user-gesture-required", "--mute-audio", `--user-data-dir=${profile}`,
    `--window-size=${Math.max(w, 500)},${h}`, `--remote-debugging-port=${port}`,
    `http://localhost:${PORT}/${PAGE}?game_window=1`], { stdio: "ignore" });
  let cdp = null;
  try {
    const target = await waitFor(async () => (JSON.parse(await get(`http://127.0.0.1:${port}/json/list`))).find(t => t.type === "page" && t.webSocketDebuggerUrl), 20000);
    check(`${label}: Chrome came up`, !!target);
    if (!target) return;
    cdp = await Cdp.open(target.webSocketDebuggerUrl);
    await cdp.send("Runtime.enable");
    for (let i = 0; i < 10; i++) {
      await cdp.send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
      if (await cdp.eval(`innerWidth + "x" + innerHeight`) === `${w}x${h}`) break;
      await sleep(300);
    }
    // Into the lobby as a guest, the way a first-time player gets there.
    const lobby = await waitFor(() => cdp.eval(`(function(){
      function q(s){return document.querySelector(s);}
      function vis(el){ if(!el) return false; var r=el.getBoundingClientRect(); return r.width>0 && getComputedStyle(el).display!=="none"; }
      var spl=q("#cc-fs-splash"); if(spl && getComputedStyle(spl).display!=="none"){ var b=q("#ccfs-window"); if(b) b.click(); spl.style.display="none"; }
      var dev=q("#cc-device-screen"); if(dev && getComputedStyle(dev).display!=="none"){ var d=q('[data-cc-device="computer"]'); if(d) d.click(); }
      var lob=q("#auth-stats-lobby"); if(lob && lob.classList.contains("visible")) return true;
      var go=q("#auth-guest-go-btn"), nk=q("#auth-guest-nick");
      if(go && vis(go)){ if(nk){ nk.value="Tester"; nk.dispatchEvent(new Event("input",{bubbles:true})); } go.click(); return false; }
      var g=q("#auth-guest-btn"); if(g && vis(g)) g.click();
      else { window.__tb=(window.__tb||0)+1; if(window.__tb>40){ var ls=q("#auth-loading-screen"); if(ls) ls.classList.add("hidden"); var as=q("#auth-screen"); if(as) as.classList.remove("hidden"); } }
      return false; })()`), 45000, 400);
    check(`${label}: reached the lobby as a guest`, !!lobby);
    if (!lobby) return;

    // Record every hush the chooser asks for.
    await cdp.eval(`(function(){ var real = window.__ccHushTheme; window.__hushLog = []; window.__ccHushTheme = function(on){ window.__hushLog.push(!!on); if (real) real(on); }; })()`);
    await cdp.eval(`document.getElementById("stats-tutorial-btn").click()`);
    const open = await waitFor(() => cdp.eval(`!!document.querySelector("#tut3-chooser.open .tut3-vid")`), 8000);
    check(`${label}: the Tutorial tile opens the video list`, !!open);
    if (!open) return;
    const list = JSON.parse(await cdp.eval(`JSON.stringify({
      groups: [...document.querySelectorAll("#tut3-chooser .tut3-group-hd")].map(e => e.textContent),
      vids: [...document.querySelectorAll("#tut3-chooser .tut3-vid")].map(e => e.getAttribute("data-key")),
      text: document.querySelector("#tut3-chooser").innerText,
      overflowX: document.querySelector("#tut3-chooser .tut3-card").scrollWidth > document.querySelector("#tut3-chooser .tut3-card").clientWidth + 1,
      cardW: document.querySelector("#tut3-chooser .tut3-card").getBoundingClientRect().width,
      thumbs: [...document.querySelectorAll("#tut3-chooser .tut3-thumb img")].length })`));
    check(`${label}: eight videos, in three groups`, list.vids.length === 8 && list.groups.length === 3, JSON.stringify(list.groups));
    check(`${label}: the main menu video stands alone at the top`, list.vids[0] === "menu" && /start here/i.test(list.groups[0] || ""), JSON.stringify(list.groups));
    check(`${label}: the list is the one in the source, in order`, list.vids.join() === VIDEOS.map(v => v.key).join());
    check(`${label}: every video shows its length`, VIDEOS.every(v => list.text.includes(v.len)));
    check(`${label}: no Competitive tutorial on screen`, !/competitive/i.test(list.text));
    check(`${label}: nothing to click through, and it says so`, /Nothing to click through/.test(list.text));
    check(`${label}: the card fits the window`, !list.overflowX && list.cardW <= w);

    // Play the first one.
    await cdp.eval(`document.querySelector('#tut3-chooser .tut3-vid[data-key="${VIDEOS[0].key}"]').click()`);
    const playing = await waitFor(() => cdp.eval(`(function(){ var v = document.getElementById("tut3-video"); return v && v.readyState >= 2 && v.currentTime > 0.3 && !v.paused; })()`), 15000);
    check(`${label}: the video really plays (frames decoded, time moving)`, !!playing);
    const info = JSON.parse(await cdp.eval(`JSON.stringify((function(){ var v = document.getElementById("tut3-video"); var r = v.getBoundingClientRect();
      return { src: v.currentSrc, dur: v.duration, w: r.width, h: r.height, tracks: v.textTracks.length, mode: v.textTracks[0] && v.textTracks[0].mode,
               title: document.querySelector(".tut3-player-hd h3").textContent, next: (document.getElementById("tut3-next") || {}).textContent || "" }; })())`));
    check(`${label}: it is the file the list names`, info.src.endsWith(`/video/${VIDEOS[0].file}.mp4`), info.src);
    check(`${label}: 16:9 and big enough to read`, Math.abs(info.w / info.h - 16 / 9) < 0.03 && info.w >= Math.min(w - 60, 600), `${info.w}x${info.h}`);
    check(`${label}: captions are there, and off until switched on`, info.tracks === 1 && info.mode !== "showing");
    check(`${label}: it offers the next video`, info.next.includes(VIDEOS[1].title));
    check(`${label}: the lobby music is hushed while it plays`, (await cdp.eval(`window.__hushLog.includes(true)`)) === true);

    // Run it to the end.
    await cdp.eval(`(function(){ var v = document.getElementById("tut3-video"); v.currentTime = Math.max(0, v.duration - 1.2); v.play(); })()`);
    const watched = await waitFor(() => cdp.eval(`(JSON.parse(localStorage.getItem("tut3_done_v1") || "{}"))[${JSON.stringify(VIDEOS[0].key)}] === true`), 10000);
    check(`${label}: watching to the end ticks it off`, !!watched);
    check(`${label}: and the Next button lights up`, !!(await waitFor(() => cdp.eval(`!!document.querySelector("#tut3-next.tut3-ready")`), 3000)));

    // Next, then back to the list.
    await cdp.eval(`document.getElementById("tut3-next").click()`);
    const second = await waitFor(() => cdp.eval(`(function(){ var v = document.getElementById("tut3-video"); return v && v.currentSrc.endsWith("/video/${VIDEOS[1].file}.mp4"); })()`), 8000);
    check(`${label}: Next plays the next video`, !!second);
    await cdp.eval(`document.getElementById("tut3-all").click()`);
    const back = JSON.parse(await cdp.eval(`JSON.stringify({ video: !!document.getElementById("tut3-video"), ticked: !!document.querySelector('#tut3-chooser .tut3-vid[data-key="${VIDEOS[0].key}"] .tut3-vid-done'), count: (document.querySelector(".tut3-reward") || {}).textContent || "" })`));
    check(`${label}: All videos stops the video and goes back`, !back.video);
    check(`${label}: the watched one is ticked in the list`, back.ticked);
    check(`${label}: the Osprey count moved`, back.count.includes(`(1/${VIDEOS.length})`), back.count);
    check(`${label}: leaving the player gives the music back`, (await cdp.eval(`window.__hushLog[window.__hushLog.length - 1]`)) === false);
    await cdp.eval(`document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))`);
    check(`${label}: Escape closes it`, (await cdp.eval(`!document.querySelector("#tut3-chooser.open")`)) === true);
  } finally {
    if (cdp) cdp.close();
    try { chrome.kill("SIGKILL"); } catch (_) {}
    await new Promise(r => { chrome.once("exit", r); setTimeout(r, 4000); });
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (_) {}
  }
}

(async () => {
  const stat = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });
  try {
    await waitFor(async () => (await get(`http://127.0.0.1:${PORT}/${PAGE}`, 2000)).length > 0, 10000);
    console.log("\na laptop (1280x800)");
    await drive(1280, 800, "laptop");
    console.log("\na phone (390x844)");
    await drive(390, 844, "phone");
  } finally {
    try { stat.kill(); } catch (_) {}
    for (const f of tmp) try { fs.unlinkSync(f); } catch (_) {}
  }
  done();
})();
