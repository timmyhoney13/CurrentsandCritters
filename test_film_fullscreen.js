/* ================================================================
 * test_film_fullscreen.js — the Full Screen button under the How to
 * Play film.
 *
 * The film plays in three places and every one of them must be able to
 * fill the screen from a control in the page, not only from whatever
 * Chrome decided to leave on the video's own bar:
 *
 *   /rules        multiplayer/client/rules.html    (both hosts)
 *   the home page index.html                       (Vercel)
 *   the game      js/preview-app.js -> How to play (Render)
 *
 * The three markups are not copied into this file. They are cut out of
 * the real sources by their `data-film` wrapper, dropped on one page with
 * the real js/film-captions.js, and clicked in a real Chrome. A copy of
 * the markup would only ever prove the copy works.
 *
 * Headless Chrome will not hand a page the screen, so the Fullscreen API
 * is stubbed for the click half: what is being checked is that the button
 * asks for the right element in the right way, and repaints itself from
 * the browser's answer. The CSS half is checked against the real files.
 *
 * The layout half loads the REAL /rules and the REAL home page at 200%
 * browser zoom, which halves the CSS viewport: 1280 becomes 640, 1366
 * becomes 683. Two buttons on that line is the width this change spends.
 *
 *   node test_film_fullscreen.js
 * ================================================================ */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer", "client");

let passed = 0;
const failures = [];

function check(name, cond, detail) {
  if (cond) {
    passed++;
    console.log("  ✓ " + name);
  } else {
    failures.push(name + (detail ? "\n      " + detail : ""));
    console.log("  ✗ FAIL: " + name + (detail ? "\n      " + detail : ""));
  }
}

const read = (p) => fs.readFileSync(p, "utf8");

/* ── Cutting the real markup out ──────────────────────────────────── */

function filmBlock(src, label) {
  const at = src.indexOf("data-film>");
  if (at < 0) throw new Error(label + " has no data-film wrapper");
  const start = src.lastIndexOf("<div", at);
  if (start < 0) throw new Error(label + ": data-film is not on a <div>");
  const re = /<\/?div\b/gi;
  re.lastIndex = start;
  let depth = 0;
  let m;
  while ((m = re.exec(src))) {
    if (m[0][1] === "/") {
      depth--;
      if (depth === 0) return src.slice(start, src.indexOf(">", m.index) + 1);
    } else {
      depth++;
    }
  }
  throw new Error(label + ": unbalanced divs around the film");
}

const APP = read(path.join(CLIENT, "js", "preview-app.js"));
function grabFn(name) {
  const i = APP.indexOf("function " + name + "(");
  if (i < 0) throw new Error("no function " + name + " in preview-app.js");
  let depth = 0;
  let started = false;
  for (let j = APP.indexOf("{", i); j < APP.length; j++) {
    if (APP[j] === "{") { depth++; started = true; }
    else if (APP[j] === "}") { depth--; if (started && depth === 0) return APP.slice(i, j + 1); }
  }
  throw new Error("unbalanced " + name);
}
// _htpSpeciesHtml needs the whole card database; the film does not.
const quickHtml = new Function(
  "_htpSpeciesHtml",
  [grabFn("_htpStep"), grabFn("_htpQuickHtml")].join("\n") + "\nreturn _htpQuickHtml();"
)(() => "");

const rulesHtml = read(path.join(CLIENT, "rules.html"));
const indexHtml = read(path.join(ROOT, "index.html"));
const previewHtml = read(path.join(CLIENT, "preview.html"));
const previewCss = read(path.join(CLIENT, "css", "preview.css"));
const filmJs = read(path.join(CLIENT, "js", "film-captions.js"));
const BUILD = JSON.parse(read(path.join(CLIENT, "version.json"))).build;

const PLACES = [
  { name: "/rules", markup: filmBlock(rulesHtml, "rules.html"), page: rulesHtml, css: rulesHtml },
  { name: "the home page", markup: filmBlock(indexHtml, "index.html"), page: indexHtml, css: indexHtml },
  { name: "the game", markup: filmBlock(quickHtml, "preview-app.js"), page: previewHtml, css: previewCss },
];

/* ── 1. The button is there, in all three places ──────────────────── */
console.log("\nA Full Screen button under the film, in all three places");

for (const place of PLACES) {
  check(place.name + ": has a Full Screen button inside the data-film wrapper",
    /data-film-full/.test(place.markup));
  check(place.name + ": it is a real <button type=\"button\">",
    /<button\s+type="button"[^>]*data-film-full/.test(place.markup),
    "a <div> here is unreachable by keyboard and invisible to a screen reader");
  check(place.name + ": it starts in the off position",
    new RegExp('data-film-full[^>]*aria-pressed="false"|aria-pressed="false"[^>]*data-film-full')
      .test(place.markup.replace(/\s+/g, " ")));
  check(place.name + ": it says Full Screen",
    /Full Screen<\/button>/.test(place.markup));
  check(place.name + ": it sits on the same line as the captions switch",
    place.markup.indexOf("data-film-captions") < place.markup.indexOf("data-film-full"),
    "both controls belong in the film's caption line, captions first");
}

/* ── 2. One implementation, loaded by every page ──────────────────── */
console.log("\nOne implementation, shared by all three");

check("the shared module handles data-film-full",
  /data-film-full/.test(filmJs));
check("it asks the VIDEO for full screen, not the frame around it",
  /video\.requestFullscreen/.test(filmJs),
  "full screen on the <figure> loses the browser's own player, captions menu and all");
check("it falls back to the webkit element API",
  /webkitRequestFullscreen/.test(filmJs));
check("…and to the iPhone's own player, which has no element full screen",
  /webkitEnterFullscreen/.test(filmJs));
check("it follows Esc and F11 through fullscreenchange",
  /addEventListener\("fullscreenchange"/.test(filmJs) &&
  /addEventListener\("webkitfullscreenchange"/.test(filmJs));
check("nobody re-implemented the toggle in preview-app.js",
  !/data-film-full[^]*?addEventListener/.test("") &&
  (APP.match(/addEventListener\([^)]*film-full/g) || []).length === 0,
  "the click handler belongs in js/film-captions.js");
check("the game syncs the pair when it builds the pane",
  /CC_SYNC_FILM_CAPTIONS/.test(APP),
  "the buttons go in as a string long after film-captions.js ran");

for (const [label, page] of [["rules.html", rulesHtml], ["index.html", indexHtml],
                             ["preview.html", previewHtml]]) {
  check(label + " loads js/film-captions.js on this build",
    page.includes("/js/film-captions.js?v=" + BUILD),
    "must ask for " + BUILD + ", or players keep the old file");
}

/* ── 3. The CSS that makes full screen look like full screen ──────── */
console.log("\nThe video really fills the screen, in all three stylesheets");

// Author width/height beat the UA's fullscreen rule, so without these the
// video comes up as a 16:9 band in the middle of a black page.
const FS_CSS = [
  { name: "/rules", css: rulesHtml, sel: ".film-frame video" },
  { name: "the home page", css: indexHtml, sel: ".l60-film-frame video" },
  { name: "the game", css: previewCss, sel: ".htp-film-video" },
];
for (const s of FS_CSS) {
  const std = s.css.indexOf(s.sel + ":fullscreen");
  const wk = s.css.indexOf(s.sel + ":-webkit-full-screen");
  check(s.name + ": has a :fullscreen rule for the video", std > -1);
  check(s.name + ": and the prefixed one Safari needs", wk > -1);
  check(s.name + ": the two are separate rules, not one selector list",
    std > -1 && wk > -1 && !new RegExp(s.sel.replace(/[.\s]/g, "\\$&") +
      ":fullscreen\\s*,").test(s.css),
    "a browser throws away a whole rule it cannot parse, and only Safari knows the prefixed name");
  const body = std > -1 ? s.css.slice(std, s.css.indexOf("}", std)) : "";
  check(s.name + ": it gives the video the whole height",
    /height:\s*100%/.test(body) && /max-height:\s*none/.test(body), body.trim());
  check(s.name + ": and letterboxes rather than crops",
    /object-fit:\s*contain/.test(body), body.trim());
}
check("the game's aspect-ratio is released in full screen",
  /aspect-ratio:\s*auto/.test(previewCss.slice(previewCss.indexOf(".htp-film-video:fullscreen"),
    previewCss.indexOf(".htp-film-video:fullscreen") + 400)),
  "aspect-ratio: 16/9 would letterbox the letterbox");

/* ── A browser, or nothing ────────────────────────────────────────── */
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => fs.existsSync(p));

const PORT = 9660 + (process.pid % 150);
// The home page lives at the repo root and its /css, /js and /video come
// out of the client folder, which is what vercel.json rewrites do too.
const SERVER_SRC = `
  const fs=require("fs"),path=require("path"),http=require("http");
  const DIRS=[${JSON.stringify(CLIENT)},${JSON.stringify(ROOT)}];
  // The repo has two index.html: the home page at the root and the classic
  // game client in client/. /home.html is the home page, spelt so it cannot
  // come out as the other one.
  const HOME=${JSON.stringify(path.join(ROOT, "index.html"))};
  const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",
    ".json":"application/json",".vtt":"text/vtt",".mp4":"video/mp4",
    ".jpg":"image/jpeg",".png":"image/png",".svg":"image/svg+xml",
    ".webp":"image/webp",".ico":"image/x-icon",".m4a":"audio/mp4"};
  http.createServer((req,res)=>{
    const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
    if(rel==="home.html"){
      res.writeHead(200,{"Content-Type":"text/html"});
      fs.createReadStream(HOME).pipe(res);
      return;
    }
    for(const root of DIRS){
      const f=path.join(root,rel);
      if(f.startsWith(root)&&fs.existsSync(f)&&!fs.statSync(f).isDirectory()){
        res.writeHead(200,{"Content-Type":MIME[path.extname(f)]||"application/octet-stream"});
        fs.createReadStream(f).pipe(res);
        return;
      }
    }
    res.writeHead(404);res.end();
  }).listen(${PORT});
`;

// One Chrome run per page: load it, wait, read the JSON the driver left in
// #out of the DOM.
function runPage(file, width, height) {
  for (let attempt = 0; attempt < 3; attempt++) {
    let dom = "";
    try {
      dom = execFileSync(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", `--window-size=${width},${height}`,
        "--virtual-time-budget=25000", "--dump-dom",
        `http://localhost:${PORT}/${file}`],
        { encoding: "utf8", maxBuffer: 64e6, timeout: 90000, stdio: ["ignore", "pipe", "ignore"] });
    } catch (_) { /* retry */ }
    const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
    const raw = m ? m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                        .replace(/&lt;/g, "<").replace(/&gt;/g, ">") : "";
    if (raw && raw !== "PENDING") { try { return JSON.parse(raw); } catch (_) {} }
  }
  return null;
}

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: skipping the browser half.");
} else {
  const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });
  const tmp = [];
  const write = (name, html) => {
    const p = path.join(CLIENT, name);
    fs.writeFileSync(p, html);
    tmp.push(p);
    return name;
  };

  try {
    /* ── 4. Click it, with the Fullscreen API stubbed ─────────────── */
    // Headless Chrome will not give a page the screen, and a scripted click
    // is not the gesture the real API wants. So the API is replaced by one
    // that records, and what is tested is the asking and the repainting.
    const CLICK_DRIVER = `<script>
(function () {
  var calls = [];
  var current = null;
  Object.defineProperty(document, "fullscreenElement", {
    get: function () { return current; }, configurable: true
  });
  HTMLVideoElement.prototype.requestFullscreen = function () {
    calls.push({ on: this.tagName + "." + (this.className || "-"), kind: "request" });
    current = this;
    document.dispatchEvent(new Event("fullscreenchange"));
    return Promise.resolve();
  };
  document.exitFullscreen = function () {
    calls.push({ kind: "exit" });
    current = null;
    document.dispatchEvent(new Event("fullscreenchange"));
    return Promise.resolve();
  };
  function row(block) {
    var video = block.querySelector("video");
    var btn = block.querySelector("[data-film-full]");
    var r = { label: block.getAttribute("data-film-name") };
    r.start = btn.textContent.trim();
    r.startPressed = btn.getAttribute("aria-pressed");
    r.hidden = btn.hidden;
    calls = [];
    btn.click();
    r.asked = calls.slice();
    r.onVideo = current === video;
    r.on = btn.textContent.trim();
    r.onPressed = btn.getAttribute("aria-pressed");
    calls = [];
    btn.click();
    r.back = calls.slice();
    r.off = btn.textContent.trim();
    r.offPressed = btn.getAttribute("aria-pressed");
    r.cleared = current === null;
    return r;
  }
  setTimeout(function () {
    var res = { rows: [] };
    try {
      var blocks = document.querySelectorAll("[data-film]");
      res.blocks = blocks.length;
      for (var i = 0; i < blocks.length; i++) res.rows.push(row(blocks[i]));

      // THE TRAP. The game has its own Full Screen button that puts the
      // whole document up. The film's button must not read that as the film
      // being full screen, or one click drops the player out of it.
      var first = blocks[0];
      current = document.documentElement;
      document.dispatchEvent(new Event("fullscreenchange"));
      var btn = first.querySelector("[data-film-full]");
      res.whileDocFs = btn.textContent.trim();
      calls = [];
      btn.click();
      res.docFsCalls = calls.slice();
      res.docFsStillUp = current === document.documentElement
        || (current && current.tagName === "VIDEO");
    } catch (e) { res.err = String((e && e.message) || e); }
    document.getElementById("out").textContent = JSON.stringify(res);
  }, 350);
})();
</script>`;

    const clickPage = "<!doctype html><html><head><meta charset=\"utf-8\">"
      + "<title>film full screen</title></head><body>"
      + PLACES.map((p) => p.markup.replace("data-film>", 'data-film data-film-name="' + p.name + '">')).join("\n")
      + '<div id="out">PENDING</div>'
      + '<script src="/js/film-captions.js"></script>'
      + CLICK_DRIVER
      + "</body></html>";

    const res = runPage(write("__filmfs.html", clickPage), 1280, 900);

    console.log("\nClicking Full Screen, in a real browser");
    if (!res || res.err) {
      check("the page reported", false, res && res.err ? res.err : "no JSON came back");
    } else {
      check("all three films are on the page", res.blocks === 3, "found " + res.blocks);
      for (const r of res.rows) {
        check(r.label + ": the button is on the line, not hidden",
          r.hidden === false, "hidden=" + r.hidden);
        check(r.label + ": it starts saying Full Screen, unpressed",
          r.start === "Full Screen" && r.startPressed === "false",
          r.start + " / aria-pressed=" + r.startPressed);
        check(r.label + ": one click asks for full screen on the <video>",
          r.asked.length === 1 && r.asked[0].kind === "request" && r.onVideo === true,
          JSON.stringify(r.asked) + " / on the video: " + r.onVideo);
        check(r.label + ": and the button says so",
          r.on === "Exit Full Screen" && r.onPressed === "true",
          r.on + " / aria-pressed=" + r.onPressed);
        check(r.label + ": a second click comes back out",
          r.back.length === 1 && r.back[0].kind === "exit" && r.cleared === true,
          JSON.stringify(r.back));
        check(r.label + ": and the button says that too",
          r.off === "Full Screen" && r.offPressed === "false",
          r.off + " / aria-pressed=" + r.offPressed);
      }
      check("a full-screen DOCUMENT is not read as a full-screen film",
        res.whileDocFs === "Full Screen", res.whileDocFs);
      check("…and clicking the film's button then does not drop out of it",
        Array.isArray(res.docFsCalls) && res.docFsCalls.length === 1
          && res.docFsCalls[0].kind === "request",
        JSON.stringify(res.docFsCalls));
    }

    /* ── 5. 200% browser zoom ─────────────────────────────────────── */
    // 200% zoom halves the CSS viewport, so these are the real windows:
    // 1280 -> 640, 1366 -> 683, 1024 -> 512. Measured in an iframe,
    // because an iframe really is the width it says it is, and with
    // allowfullscreen so the button is not hidden as unsupported.
    const ZOOM = [[640, 800, "1280 at 200%"], [683, 768, "1366 at 200%"],
                  [512, 700, "1024 at 200%"]];

    const gamePane = "<!doctype html><html><head><meta charset=\"utf-8\">"
      + '<link rel="stylesheet" href="/css/preview.css">'
      + '</head><body class="cc-signed-in">'
      + '<div class="ph-panel" style="display:block"><div class="htp-body">'
      + PLACES[2].markup
      + "</div></div>"
      + '<script src="/js/film-captions.js"></script>'
      + "</body></html>";

    const SURFACES = [
      { name: "/rules", url: "rules.html" },
      { name: "the home page", url: "home.html" },
      { name: "the game", url: write("__filmfsgame.html", gamePane) },
    ];

    const ZOOM_DRIVER = (surfaces) => `<script>
(function () {
  var SURFACES = ${JSON.stringify(surfaces)};
  var out = { rows: [] };
  var left = SURFACES.length;
  SURFACES.forEach(function (s, i) {
    var fr = document.createElement("iframe");
    fr.setAttribute("allowfullscreen", "");
    fr.width = String(innerWidth);
    fr.height = String(innerHeight);
    fr.src = "/" + s.url;
    fr.onload = function () {
      setTimeout(function () {
        var row = { name: s.name };
        try {
          var d = fr.contentDocument, w = fr.contentWindow;
          row.vw = w.innerWidth;
          var film = d.querySelector("[data-film]");
          if (!film) { row.err = "no film on the page"; return done(row); }
          var cc = film.querySelector("[data-film-captions]");
          var fsb = film.querySelector("[data-film-full]");
          var vid = film.querySelector("video");
          function box(el) {
            if (!el) return null;
            var r = el.getBoundingClientRect(), cs = w.getComputedStyle(el);
            return { l: Math.round(r.left), r: Math.round(r.right),
                     t: Math.round(r.top), b: Math.round(r.bottom),
                     wd: Math.round(r.width), ht: Math.round(r.height),
                     disp: cs.display, vis: cs.visibility, hidden: !!el.hidden,
                     text: (el.textContent || "").trim(), fs: cs.fontSize };
          }
          row.cc = box(cc);
          row.fs = box(fsb);
          row.video = box(vid);
          row.docOverflow = d.documentElement.scrollWidth - d.documentElement.clientWidth;
        } catch (e) { row.err = String((e && e.message) || e); }
        done(row);
      }, 500);
    };
    document.body.appendChild(fr);
  });
  function done(row) {
    out.rows.push(row);
    if (--left === 0) document.getElementById("out").textContent = JSON.stringify(out);
  }
})();
</script>`;

    console.log("\nAt 200% browser zoom, where the line is half as wide");
    for (const [w, h, label] of ZOOM) {
      const page = "<!doctype html><html><head><meta charset=\"utf-8\">"
        + "<style>html,body{margin:0}iframe{display:block;border:0}</style>"
        + "</head><body>"
        + '<div id="out">PENDING</div>'
        + ZOOM_DRIVER(SURFACES)
        + "</body></html>";
      const zres = runPage(write("__filmfszoom.html", page), w, h);
      console.log("  ── " + label + " (" + w + " CSS px) ──");
      if (!zres || zres.err || !zres.rows) {
        check("  the page reported", false, zres && zres.err ? zres.err : "no JSON came back");
        continue;
      }
      for (const row of zres.rows) {
        if (row.err) { check("  " + row.name + ": measured", false, row.err); continue; }
        const b = row.fs;
        check("  " + row.name + ": the Full Screen button is on screen",
          !!b && !b.hidden && b.disp !== "none" && b.vis !== "hidden" && b.wd > 0 && b.ht > 0,
          JSON.stringify(b));
        check("  " + row.name + ": and it is not cut off at the edge",
          !!b && b.l >= -1 && b.r <= row.vw + 1,
          b ? b.l + "…" + b.r + " in " + row.vw + "px" : "missing");
        check("  " + row.name + ": it still says Full Screen",
          !!b && b.text === "Full Screen", b ? b.text : "missing");
        check("  " + row.name + ": the captions switch is still on screen too",
          !!row.cc && row.cc.wd > 0 && row.cc.l >= -1 && row.cc.r <= row.vw + 1,
          JSON.stringify(row.cc));
        check("  " + row.name + ": the two buttons do not overlap",
          !!b && !!row.cc && (b.l >= row.cc.r - 1 || b.t >= row.cc.b - 1
            || row.cc.l >= b.r - 1 || row.cc.t >= b.b - 1),
          "cc " + JSON.stringify(row.cc) + " vs fs " + JSON.stringify(b));
        check("  " + row.name + ": the video is not cut off either",
          !!row.video && row.video.l >= -1 && row.video.r <= row.vw + 1,
          JSON.stringify(row.video));
        check("  " + row.name + ": and the page has no sideways scroll",
          row.docOverflow <= 1, row.docOverflow + "px wider than the window");
      }
    }
  } finally {
    for (const p of tmp) { try { fs.unlinkSync(p); } catch (_) {} }
    server.kill();
  }
}

console.log("\n" + "─".repeat(60));
console.log(passed + " passed, " + failures.length + " failed");
if (failures.length) {
  console.log("\nFailures:\n  " + failures.join("\n  "));
  process.exit(1);
}
