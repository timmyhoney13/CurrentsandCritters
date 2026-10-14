/* ================================================================
 * test_film_captions.js — the Captions switch under the How to Play film.
 *
 * The film plays in three places and every one of them must behave the
 * same way: captions START OFF, and one visible control turns them on and
 * off again.
 *
 *   /rules        multiplayer/client/rules.html    (both hosts)
 *   the home page index.html                       (Vercel)
 *   the game      js/preview-app.js -> How to play (Render)
 *
 * The three markups are not copied into this file. They are cut out of the
 * real sources by their `data-film` wrapper, dropped on one page with the
 * real js/film-captions.js, and clicked in a real Chrome. A copy of the
 * markup would only ever prove the copy works.
 *
 *   node test_film_captions.js
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

// The <div …data-film> element and everything inside it, by counting divs.
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

// The Quick Start pane the game builds, run for real out of preview-app.js.
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
// _htpSpeciesHtml needs the whole card database; the film does not, and
// this test is about the film.
const quickHtml = new Function(
  "_htpSpeciesHtml",
  [grabFn("_htpStep"), grabFn("_htpQuickHtml")].join("\n") + "\nreturn _htpQuickHtml();"
)(() => "");

const rulesHtml = read(path.join(CLIENT, "rules.html"));
const indexHtml = read(path.join(ROOT, "index.html"));
const previewHtml = read(path.join(CLIENT, "preview.html"));

const PLACES = [
  { name: "/rules", markup: filmBlock(rulesHtml, "rules.html"), page: rulesHtml },
  { name: "the home page", markup: filmBlock(indexHtml, "index.html"), page: indexHtml },
  { name: "the game", markup: filmBlock(quickHtml, "preview-app.js"), page: previewHtml },
];

/* ── 1. Captions start off, everywhere ────────────────────────────── */
console.log("\nCaptions start off in all three places");

for (const place of PLACES) {
  check(place.name + ": the caption track carries no `default`",
    /<track\b[^>]*>/.test(place.markup) && !/<track\b[^>]*\bdefault\b/.test(place.markup),
    "`default` on the <track> is what switches captions on before anyone asks");
  check(place.name + ": the switch starts in the off position",
    /aria-pressed="false"/.test(place.markup) && /Captions off/.test(place.markup));
  check(place.name + ": the caption line no longer promises captions are on",
    !/with captions/i.test(place.markup));
}

/* ── 2. One switch, and every page loads the one file ─────────────── */
console.log("\nOne control, one implementation");

for (const place of PLACES) {
  check(place.name + ": has a captions button inside the data-film wrapper",
    /data-film-captions/.test(place.markup));
  check(place.name + ": the button is a real <button type=\"button\">",
    /<button\s+type="button"[^>]*data-film-captions/.test(place.markup),
    "a <div> here is unreachable by keyboard and invisible to a screen reader");
}

check("the shared module exists",
  fs.existsSync(path.join(CLIENT, "js", "film-captions.js")));

for (const [label, page] of [["rules.html", rulesHtml], ["index.html", indexHtml],
                             ["preview.html", previewHtml]]) {
  check(label + " loads js/film-captions.js",
    /<script[^>]+src="\/js\/film-captions\.js\?v=/.test(page));
}

check("the game's copy is cache-busted on the current build", (() => {
  const build = JSON.parse(read(path.join(CLIENT, "version.json"))).build;
  return previewHtml.includes('/js/film-captions.js?v=' + build);
})(), "preview.html must ask for the build in version.json, or players keep the old file");

check("nobody re-implemented the toggle a second time",
  !/data-film-captions/.test(APP.replace(/'<button type="button" class="htp-film-cc" data-film-captions'/, "")) ||
  (APP.match(/addEventListener\("click"[^)]*film-captions/g) || []).length === 0,
  "the click handler belongs in js/film-captions.js, not in preview-app.js");

/* ── 3. Click it, in a real browser ───────────────────────────────── */
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => fs.existsSync(p));

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: skipping the click half.");
} else {
  const PORT = 9820 + (process.pid % 150);
  const SERVER_SRC = `
    const fs=require("fs"),path=require("path"),http=require("http");
    const ROOT=${JSON.stringify(CLIENT)};
    const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",
      ".json":"application/json",".vtt":"text/vtt",".mp4":"video/mp4",
      ".jpg":"image/jpeg",".png":"image/png",".svg":"image/svg+xml"};
    http.createServer((req,res)=>{
      const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
      const f=path.join(ROOT,rel);
      if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);res.end();return;}
      res.writeHead(200,{"Content-Type":MIME[path.extname(f)]||"application/octet-stream"});
      fs.createReadStream(f).pipe(res);
    }).listen(${PORT});
  `;

  const DRIVER = `<script>
(function () {
  function row(block) {
    var video = block.querySelector("video");
    var btn = block.querySelector("[data-film-captions]");
    var track = video.textTracks[0];
    var r = { label: block.getAttribute("data-film-name") };
    r.startMode = track.mode;
    r.startText = btn.textContent.trim();
    r.startPressed = btn.getAttribute("aria-pressed");
    btn.click();
    r.onMode = track.mode;
    r.onText = btn.textContent.trim();
    r.onPressed = btn.getAttribute("aria-pressed");
    btn.click();
    r.offMode = track.mode;
    r.offText = btn.textContent.trim();
    r.offPressed = btn.getAttribute("aria-pressed");
    return r;
  }
  function finish(res) {
    document.getElementById("out").textContent = JSON.stringify(res);
  }
  setTimeout(function () {
    var res = { rows: [] };
    try {
      var blocks = document.querySelectorAll("[data-film]");
      res.blocks = blocks.length;
      for (var i = 0; i < blocks.length; i++) res.rows.push(row(blocks[i]));
      // And the other direction: the browser's own captions menu sets the
      // mode without touching our button, which must still repaint.
      var first = blocks[0];
      first.querySelector("video").textTracks[0].mode = "showing";
      setTimeout(function () {
        res.nativeText = first.querySelector("[data-film-captions]").textContent.trim();
        res.nativePressed = first.querySelector("[data-film-captions]").getAttribute("aria-pressed");
        finish(res);
      }, 120);
    } catch (e) {
      res.err = String((e && e.message) || e);
      finish(res);
    }
  }, 250);
})();
</script>`;

  const page = "<!doctype html><html><head><meta charset=\"utf-8\"><title>film captions</title></head><body>"
    + PLACES.map((p) => p.markup.replace("data-film>", 'data-film data-film-name="' + p.name + '">')).join("\n")
    + '<div id="out">PENDING</div>'
    + '<script src="/js/film-captions.js"></script>'
    + DRIVER
    + "</body></html>";

  const FILE = "__filmcc.html";
  fs.writeFileSync(path.join(CLIENT, FILE), page);
  const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });

  let res = null;
  try {
    for (let attempt = 0; attempt < 3 && !res; attempt++) {
      let dom = "";
      try {
        dom = execFileSync(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
          "--hide-scrollbars", "--window-size=1100,900", "--virtual-time-budget=20000",
          "--dump-dom", `http://localhost:${PORT}/${FILE}`],
          { encoding: "utf8", maxBuffer: 64e6, timeout: 90000, stdio: ["ignore", "pipe", "ignore"] });
      } catch (_) { /* retry */ }
      const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
      const raw = m ? m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                          .replace(/&lt;/g, "<").replace(/&gt;/g, ">") : "";
      if (raw && raw !== "PENDING") { try { res = JSON.parse(raw); } catch (_) {} }
    }
  } finally {
    try { fs.unlinkSync(path.join(CLIENT, FILE)); } catch (_) {}
    server.kill();
  }

  console.log("\nClicking the switch, in a real browser");
  if (!res || res.err) {
    check("the page reported", false, res && res.err ? res.err : "no JSON came back");
  } else {
    check("all three films are on the page", res.blocks === 3, "found " + res.blocks);
    for (const r of res.rows) {
      check(r.label + ": the track is disabled before anyone clicks",
        r.startMode === "disabled",
        "mode was " + r.startMode + " — the .vtt is being fetched and shown unasked");
      check(r.label + ": it says so, off and unpressed",
        r.startText === "Captions off" && r.startPressed === "false",
        r.startText + " / aria-pressed=" + r.startPressed);
      check(r.label + ": one click shows the captions",
        r.onMode === "showing" && r.onText === "Captions on" && r.onPressed === "true",
        r.onMode + " / " + r.onText + " / aria-pressed=" + r.onPressed);
      check(r.label + ": a second click hides them again",
        r.offMode === "hidden" && r.offText === "Captions off" && r.offPressed === "false",
        r.offMode + " / " + r.offText + " / aria-pressed=" + r.offPressed);
    }
    check("the button follows the browser's own captions menu",
      res.nativeText === "Captions on" && res.nativePressed === "true",
      res.nativeText + " / aria-pressed=" + res.nativePressed);
  }
}

console.log("\n" + "─".repeat(60));
console.log(passed + " passed, " + failures.length + " failed");
if (failures.length) {
  console.log("\nFailures:\n  " + failures.join("\n  "));
  process.exit(1);
}
