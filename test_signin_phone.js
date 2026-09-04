#!/usr/bin/env node
/* The sign-in screen ON A PHONE, at the widths a phone actually has.
 *
 * This file exists because of a bug that every other sign-in test was blind
 * to, and blind to for a reason worth writing down.
 *
 * WHAT WAS WRONG. The stacked phone layout gives the painting a share of the
 * screen (--ao-band) and the sign-in column whatever is underneath. A share
 * is what the picture would LIKE; it is not what is going. The column below
 * it is a fixed stack of controls, so at 48vh of poster the bottom of that
 * stack was painted past the bottom edge of the screen. Measured against the
 * live site on 2026-09-04: CONTINUE WITH GOOGLE, PLAY AS GUEST and CREATE AN
 * ACCOUNT were ALL below the fold on every handset from a 320x568 up to a
 * 430x932, by 99px on the largest phone sold and 215px on the smallest, and
 * on a 320 even FORGOT PASSWORD? was gone. The column does scroll, so they
 * were reachable, but nothing on the screen said so: a new player, who is the
 * one person on that screen who needs CREATE AN ACCOUNT, met a screen whose
 * only visible way in was a sign-in form for the account they have not got.
 *
 * WHY NOTHING CAUGHT IT. test_account_signin.js and test_signin_screen.js
 * both drive Chrome with --window-size, and headless Chrome will not open a
 * window narrower than about 500px: every "phone" row in them is really a
 * second narrow-laptop row, and no phone width was ever measured. At the one
 * narrow size they did use, 500x900, the assertion was that Create Account is
 * "reachable by scrolling", which the broken screen passed. Reachable by
 * scrolling and on the screen are not the same claim, and for the way INTO a
 * game only the second one is worth making.
 *
 * So this file sets the layout viewport directly with
 * Emulation.setDeviceMetricsOverride, which has no floor, verifies the width
 * it actually got, and asks the only question that matters on a phone: can
 * you see every way in without going looking for one?
 *
 * Run:  node test_signin_phone.js        (needs Google Chrome / Chromium)
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const { spawn } = require("child_process");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const CSS = fs.readFileSync(path.join(CLIENT, "css/preview.css"), "utf8");
const APP = fs.readFileSync(path.join(CLIENT, "js/preview-app.js"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra != null ? "  → " + extra : "")); }
}
function done() {
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

// ══════════════════════════════════════════════════════════════════════════
//  SOURCE: the rule that keeps the way in on the screen
// ══════════════════════════════════════════════════════════════════════════
console.log("\nthe picture takes what is left, not a flat share");
{
  check("the band is bounded by the room the form needs",
        /--ao-band: max\([^;]*100dvh - var\(--ao-form-needs\)\)\);/.test(CSS));
  // The short-phone block RE-DECLARES --ao-band. A bound written once is a
  // bound switched off by the screens that need it most, which is exactly the
  // shape of the original bug.
  check("…on both declarations, or the shortest phones lose the rule again",
        (CSS.match(/--ao-band: max\([^;]*100dvh - var\(--ao-form-needs\)\)\);/g) || []).length >= 2);
  check("…each with a vh line under it, for a browser with no dvh",
        (CSS.match(/--ao-band: max\([^;]*100vh - var\(--ao-form-needs\)\)\);/g) || []).length >= 2);
  check("…and a floor, so the picture never becomes a hairline",
        /--ao-band: max\((\d+)px, min\(/.test(CSS));
  check("the pane switch scrolls the box that actually scrolls",
        /const box = \$a\("auth-step-choose"\);/.test(APP)
        && /box\.scrollTo\(\{ top, behavior: "smooth" \}\)/.test(APP)
        && !/querySelector\("\.ao-form"\);\n\s*if \(col && typeof col\.scrollTo/.test(APP));
  check("a disabled Create Account answers with the reason it is disabled",
        /const explainDisabled = \(paneId, goId, submit\)/.test(APP)
        && /explainDisabled\("ao-pane-create", "ao-new-go", ccPasswordCreate\);/.test(APP));
}

// ══════════════════════════════════════════════════════════════════════════
//  DRIVE
// ══════════════════════════════════════════════════════════════════════════
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => fs.existsSync(p));

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: skipping the drive half.");
  done();
}

// Every way into the game. Not "the ones that fit": the whole point of the
// file is that a screen which shows three of six is a screen that hides the
// only one a new player wants.
const WAYS_IN = ["ao-user", "ao-pass", "ao-signin", "ao-forgot",
                 "auth-choose-google-btn", "auth-guest-btn", "ao-create"];

// Real handsets, upright, smallest first. The 500x900 row is kept because it
// is the one the old tests could reach, so a regression there is comparable.
const PHONES = [
  [320, 568, "iPhone SE (1st gen)"],
  [360, 640, "small Android"],
  [375, 667, "iPhone SE (2nd/3rd)"],
  [360, 780, "Pixel 5"],
  [390, 844, "iPhone 14"],
  [393, 873, "Pixel 8"],
  [414, 896, "iPhone 11"],
  [430, 932, "iPhone 15 Pro Max"],
  [500, 900, "narrow window"],
  [768, 1024, "iPad portrait"],
];

const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
  ".webp": "image/webp", ".svg": "image/svg+xml", ".ico": "image/x-icon",
  ".m4a": "audio/mp4", ".woff2": "font/woff2" };

const SPORT = 8700 + (process.pid % 80);
const server = http.createServer((req, res) => {
  const rel = decodeURIComponent(req.url.split("?")[0]).replace(/^\/+/, "") || "preview.html";
  const f = path.join(CLIENT, rel);
  if (!f.startsWith(CLIENT) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) {
    res.writeHead(404); res.end(); return;
  }
  res.writeHead(200, { "Content-Type": MIME[path.extname(f)] || "application/octet-stream" });
  fs.createReadStream(f).pipe(res);
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function get(url, timeoutMs = 4000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let b = ""; res.on("data", (d) => b += d); res.on("end", () => resolve(b));
    });
    req.on("error", reject);
    req.setTimeout(timeoutMs, () => req.destroy(new Error("timeout")));
  });
}
async function waitFor(fn, ms, every = 250) {
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
    ws.addEventListener("message", (ev) => {
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
  // Bounded, always: one unanswered message otherwise hangs the run for as
  // long as the process is allowed to live.
  send(method, params, timeoutMs = 25000) {
    const id = ++this.id;
    return new Promise((resolve) => {
      const t = setTimeout(() => { this.waiting.delete(id); resolve(null); }, timeoutMs);
      this.waiting.set(id, (m) => { clearTimeout(t); resolve(m); });
      try { this.ws.send(JSON.stringify({ id, method, params: params || {} })); }
      catch (_) { clearTimeout(t); this.waiting.delete(id); resolve(null); }
    });
  }
  async eval(expr) {
    const r = await this.send("Runtime.evaluate",
      { expression: expr, returnByValue: true, awaitPromise: true });
    return r && r.result && r.result.result ? r.result.result.value : undefined;
  }
  close() { try { this.ws.close(); } catch (_) {} }
}

let _seq = 0;

async function measure(W, H) {
  const PORT = 9500 + ((process.pid + (_seq++) * 7) % 300);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "ccphone-"));
  // Its own port and its own profile, every time. A reused port hands
  // /json/list the dying browser's target and every override lands on nothing.
  const chrome = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    `--user-data-dir=${profile}`, "--window-size=900,1000",
    `--remote-debugging-port=${PORT}`, "about:blank",
  ], { stdio: "ignore", detached: true });

  let cdp = null;
  try {
    const target = await waitFor(async () => {
      const list = JSON.parse(await get(`http://127.0.0.1:${PORT}/json/list`));
      return list.find((t) => t.type === "page" && t.webSocketDebuggerUrl) || null;
    }, 25000);
    if (!target) return null;
    cdp = await Cdp.open(target.webSocketDebuggerUrl);
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
    await cdp.send("Page.addScriptToEvaluateOnNewDocument", { source:
      `try{sessionStorage.setItem("cc_device_type",${JSON.stringify(W < 820 ? "mobile" : "computer")});}catch(e){}` });

    // --window-size cannot make a phone: Chrome clamps it at ~500px and lays
    // the page out at the clamp. This sets the layout viewport itself, which
    // is the width the media queries read. Applied AND verified: a target
    // still coming up accepts the command and lays out at the old size anyway.
    for (let a = 0; a < 10; a++) {
      await cdp.send("Emulation.setDeviceMetricsOverride",
        { width: W, height: H, deviceScaleFactor: 1, mobile: false });
      await sleep(110);
    }
    await cdp.send("Page.navigate", { url: `http://127.0.0.1:${SPORT}/preview.html?game_window=1` });

    // The chooser, up, with no Firebase round trip: this is a question about
    // a layout, and a layout does not need an account to answer it.
    const up = await waitFor(async () => await cdp.eval(`(function(){
      var s=document.getElementById("auth-step-choose"); if(!s) return null;
      var ls=document.getElementById("auth-loading-screen"); if(ls) ls.classList.add("hidden");
      var as=document.getElementById("auth-screen"); if(as) as.classList.remove("hidden");
      var sp=document.getElementById("cc-fs-splash"); if(sp) sp.style.display="none";
      s.style.display=""; s.classList.add("is-armed");
      return getComputedStyle(s).display!=="none" ? 1 : null;})()`), 45000, 300);
    if (!up) return null;
    await sleep(900);

    const signin = JSON.parse(await cdp.eval(`(function(){
      var vw=innerWidth, vh=innerHeight;
      var out={vw:vw, vh:vh, sideways:document.documentElement.scrollWidth > vw+1, ctl:{}};
      ${JSON.stringify(WAYS_IN)}.forEach(function(id){
        var e=document.getElementById(id);
        if(!e){ out.ctl[id]=null; return; }
        var b=e.getBoundingClientRect(), cs=getComputedStyle(e);
        out.ctl[id]={ top:Math.round(b.top), bottom:Math.round(b.bottom),
                      w:Math.round(b.width), h:Math.round(b.height),
                      shown: cs.display!=="none" && cs.visibility!=="hidden" && Number(cs.opacity)>.05,
                      inside: b.left>=-1 && b.right<=vw+1 };
      });
      return JSON.stringify(out);})()`));

    // …then the create pane, opened the way a thumb opens it.
    await cdp.eval(`(function(){ var b=document.getElementById("auth-step-choose");
      if(b) b.scrollTop=0; document.getElementById("ao-create").click(); return 1;})()`);
    await sleep(1500);
    const create = JSON.parse(await cdp.eval(`(function(){
      var vh=innerHeight, box=document.getElementById("auth-step-choose");
      var pane=document.getElementById("ao-pane-create");
      var head=pane.querySelector(".ao-h");
      var out={ open: getComputedStyle(pane).display!=="none",
                headTop: head?Math.round(head.getBoundingClientRect().top):null,
                canReachGo:false, goVisible:false };
      var go=document.getElementById("ao-new-go");
      if(go){ var b=go.getBoundingClientRect();
        out.goVisible = b.top>=-1 && b.bottom<=vh+1;
        // Off the bottom is fine on a form this long, unreachable is not.
        out.canReachGo = out.goVisible ||
          (box.scrollHeight - box.clientHeight) >= (b.bottom - vh); }
      return JSON.stringify(out);})()`));

    return { signin, create };
  } finally {
    if (cdp) cdp.close();
    try { process.kill(-chrome.pid, "SIGKILL"); } catch (_) {}
    try { chrome.kill("SIGKILL"); } catch (_) {}
  }
}

(async () => {
  await new Promise((r) => server.listen(SPORT, r));
  try {
    console.log("\nevery way in is ON THE SCREEN, at the sizes a phone really is");
    for (const [W, H, NAME] of PHONES) {
      const r = await measure(W, H);
      const at = `${W}x${H} (${NAME})`;
      if (!r) { check(`${at}: the screen came up`, false); continue; }
      const s = r.signin;
      // The override is the whole method. If it did not take, every assertion
      // below is about some other window and the run is worthless.
      check(`${at}: the layout viewport really is that wide`, s.vw === W, `got ${s.vw}`);
      const missing = WAYS_IN.filter((id) => !s.ctl[id]);
      check(`${at}: every way in is in the document`, missing.length === 0, missing.join(", "));
      const below = WAYS_IN.filter((id) => s.ctl[id] && s.ctl[id].bottom > s.vh + 1)
                           .map((id) => `${id} +${s.ctl[id].bottom - s.vh}`);
      check(`${at}: nothing is painted below the bottom edge`, below.length === 0, below.join(", "));
      const above = WAYS_IN.filter((id) => s.ctl[id] && s.ctl[id].top < -1)
                           .map((id) => `${id} ${s.ctl[id].top}`);
      check(`${at}: …and nothing above the top one`, above.length === 0, above.join(", "));
      const unlit = WAYS_IN.filter((id) => s.ctl[id] && (!s.ctl[id].shown || !s.ctl[id].inside));
      check(`${at}: …each one shown, and inside the window`, unlit.length === 0, unlit.join(", "));
      const small = ["ao-signin", "auth-guest-btn", "auth-choose-google-btn"]
        .filter((id) => s.ctl[id] && s.ctl[id].h < 40);
      check(`${at}: …and big enough for a thumb`, small.length === 0, small.join(", "));
      check(`${at}: nothing pushes the page sideways`, !s.sideways);

      const c = r.create;
      check(`${at}: CREATE AN ACCOUNT opens the pane that makes one`, c.open);
      check(`${at}: …with its heading on the screen`,
            c.headTop != null && c.headTop >= -1 && c.headTop <= s.vh - 20,
            `heading at ${c.headTop}px of ${s.vh}`);
      // The poster belongs to the top of this screen, but not to the top of a
      // form somebody is filling in. Where the pane cannot be seen whole under
      // the picture, it comes up scrolled to its own heading instead; where it
      // fits, there is nothing to scroll and the picture stays. What is never
      // allowed is the third thing: a pane opened halfway down, which is what
      // happened while the scroll was aimed at .ao-form, a box that does not
      // scroll, and a pane inherited wherever the last one was left.
      check(`${at}: …at its own top whenever it does not fit under the picture`,
            c.goVisible || (c.headTop != null && c.headTop < 120),
            `heading at ${c.headTop}px with Create Account off screen`);
      check(`${at}: …and Create Account on the screen or one scroll away`, c.canReachGo);
    }
  } finally {
    server.close();
  }
  done();
})();
