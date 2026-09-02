#!/usr/bin/env node
/* Ocean Points (OP) on every screen it is printed on, at every device size.
 *
 * Run:  node test_ocean_points_devices.js      (needs Google Chrome installed)
 *
 * "Competitive Points (CP)" became "Ocean Points (OP)". A rename is the kind of
 * change that looks free and is not: the words sit in a rank card, a tab strip,
 * a table header, an end-of-game meta bar and a standalone leaderboard page,
 * all of which are laid out to fit their old contents. "OP" is the same two
 * characters as "CP", but "Ocean Points (OP) are earned in..." is not the same
 * sentence as the one it replaced, and the surfaces around it are the tightest
 * in the game: three sub-tabs across a 320px phone, a six-column table, a meta
 * bar that gains a SIXTH tile the moment a game is ranked.
 *
 * So this measures the real markup sliced out of preview.html and the real
 * preview.css, plus the whole standalone leaderboard.html, in real Chrome, at
 * every device shape the game is played on: iPhone SE through 16 Pro Max,
 * Android from a 360px Galaxy to a Pixel, iPad mini through 12.9" Pro in BOTH
 * orientations, and laptops/desktops from 1280 to 2560.
 *
 * WHY IFRAMES: headless Chrome clamps --window-size to about 500px wide, so a
 * 320px window is really a 500px one and a phone is never actually tested. An
 * iframe has its own viewport and media queries evaluate against ITS width, so
 * these really are 320px (the same trick test_level_pass_ui.js uses).
 *
 * Per device, per surface:
 *   1. NO SIDEWAYS SCROLL, the document never gets wider than the screen.
 *   2. NOTHING CLIPPED, every element carrying an OP string shows all of its
 *      text (scrollWidth fits clientWidth) and sits inside the viewport.
 *   3. NOTHING UNREACHABLE, every sub-tab in the comp tab strip is either on
 *      screen or in a strip you can actually scroll.
 *   4. THE WORDS ARE RIGHT, "OP" is on screen and no stray "CP" survives.
 * The strings used are the WORST CASE the app can produce, built from the real
 * _COMP_RANK_DIVS table: the longest division name, a four-figure OP total and
 * an 8-player placement label.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer", "client");
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("SKIP: no Chrome/Chromium found: cannot run the device check.");
  process.exit(0);
}

const HTML = fs.readFileSync(path.join(CLIENT, "preview.html"), "utf8");
const APP  = fs.readFileSync(path.join(CLIENT, "js", "preview-app.js"), "utf8");

// ── Slice a real block out of preview.html by id, balanced on its own tag ────
function sliceById(id, tag) {
  const t = tag || "div";
  const open = new RegExp(`<${t}[^>]*id="${id}"[^>]*>`);
  const m = open.exec(HTML);
  if (!m) throw new Error(`#${id} not found in preview.html`);
  let i = m.index + m[0].length, depth = 1;
  const re = new RegExp(`<${t}\\b|</${t}>`, "g");
  re.lastIndex = i;
  let hit;
  while ((hit = re.exec(HTML))) {
    depth += hit[0] === `</${t}>` ? -1 : 1;
    if (depth === 0) return HTML.slice(m.index, hit.index + hit[0].length);
  }
  throw new Error(`unbalanced <${t}> reading #${id}`);
}

const COMP_PANEL = sliceById("ph-panel-competitive");
const LB_COMP    = sliceById("ph-lb-comp-section");
const GS_META    = sliceById("gs-meta-bar");

// ── The real rank table, so the worst-case strings are ones the app can make ──
const TABLE = (() => {
  const a = APP.indexOf("const _COMP_RANK_DIVS = [");
  const b = APP.indexOf("\n    ];", a);
  if (a < 0 || b < 0) throw new Error("could not read _COMP_RANK_DIVS");
  return APP.slice(a, b + 7);
})();
const DIVS = (() => {
  const vm = require("vm");
  const box = { Infinity };
  vm.createContext(box);
  vm.runInContext(TABLE + "\nthis.D = _COMP_RANK_DIVS;", box);
  return box.D;
})();
const LONGEST = DIVS.map(d => d.name).sort((x, y) => y.length - x.length)[0];
const KING    = DIVS[DIVS.length - 1].name;

// ── Every device the game is actually played on ─────────────────────────────
const DEVICES = [
  // iPhone
  ["iPhone SE (1st)",        320,  568],
  ["iPhone SE 2/3, 8",       375,  667],
  ["iPhone 12/13/14",        390,  844],
  ["iPhone 15/16",           393,  852],
  ["iPhone 16 Pro",          402,  874],
  ["iPhone 15/16 Pro Max",   430,  932],
  ["iPhone 14 sideways",     844,  390],
  // Android
  ["Galaxy S8 / A54",        360,  740],
  ["Pixel 8",                384,  854],
  ["Pixel 7 / Galaxy S23",   412,  915],
  ["Galaxy S23 sideways",    915,  412],
  ["Galaxy Z Fold cover",    344,  882],
  // iPad, both ways up
  ["iPad mini 6",            744, 1133],
  ["iPad mini 6 sideways",  1133,  744],
  ["iPad 9.7 / mini",        768, 1024],
  ["iPad 9.7 sideways",     1024,  768],
  ["iPad 10.2",              810, 1080],
  ["iPad 10.2 sideways",    1080,  810],
  ["iPad Air",               820, 1180],
  ["iPad Air sideways",     1180,  820],
  ["iPad Pro 11",            834, 1194],
  ["iPad Pro 11 sideways",  1194,  834],
  ["iPad Pro 12.9",         1024, 1366],
  ["iPad Pro 12.9 sideways",1366, 1024],
  // Computers
  ["laptop 1280",           1280,  800],
  ["laptop 1366",           1366,  768],
  ["MacBook Air 1440",      1440,  900],
  ["MacBook Pro 14",        1512,  982],
  ["desktop 1600",          1600,  900],
  ["desktop 1080p",         1920, 1080],
  ["desktop 1440p",         2560, 1440],
];

// ── The harness page ────────────────────────────────────────────────────────
// One Chrome run. Two iframes per device: the sliced Player Home surfaces, and
// the whole standalone leaderboard.html served off the same static server.
const FILE = "_op_devices_drive.html";
const surfaces = {
  comp: COMP_PANEL, lb: LB_COMP, gs: GS_META,
};

const page = `<!doctype html><html><head><meta charset="utf-8">
<style>body{margin:0;background:#123;font-family:system-ui}
iframe{display:block;border:0;margin:0 0 6px;background:#fff}</style>
</head><body>
<div id="out">PENDING</div>
<script>
window.__DEV = ${JSON.stringify(DEVICES)};
window.__S   = ${JSON.stringify(surfaces)};
window.__W   = ${JSON.stringify({ longest: LONGEST, king: KING })};

// The worst case the app can print, in the elements that print it.
var FILL = [
  ["ph-comp-rank-name",     window.__W.longest],
  ["ph-comp-rank-tier",     "Emerald Emperor Penguin"],
  ["ph-comp-cp-val",        "1,234 OP"],
  ["ph-comp-cp-next-label", "66 OP until " + window.__W.king],
  ["ph-comp-king-cp",       "1,480 OP"],
  ["ph-comp-king-meta",     "128W · 47L"],
  ["ph-comp-king-pct",      "73% win rate"],
  ["ph-ss-rank-name",       window.__W.longest],
  ["ph-ss-rank-cp",         "1,234 OP"],
  ["gs-cp-gained",          "+26 OP → 1,234 OP · " + window.__W.king],
];

function frameDoc(css) {
  return '<!doctype html><html><head><meta charset="utf-8">'
    + '<meta name="viewport" content="width=device-width,initial-scale=1">'
    + '<link rel="stylesheet" href="/css/preview.css">'
    + '<style>body{margin:0;background:#dff1ff;font-family:Nunito,system-ui,sans-serif}'
    + '.wrap{padding:10px}</style></head><body class="cc-signed-in">'
    + '<div class="wrap">' + window.__S.comp + '</div>'
    + '<div class="wrap ph-scard ph-full">' + window.__S.lb + '</div>'
    + '<div class="wrap" id="gs-wrap-probe"><div id="gs-header">' + window.__S.gs + '</div></div>'
    + '</body></html>';
}

var frames = [];
window.__DEV.forEach(function (d) {
  ["home", "leaderboard"].forEach(function (kind) {
    var fr = document.createElement("iframe");
    fr.width = d[1]; fr.height = d[2];
    fr.dataset.dev = d[0]; fr.dataset.kind = kind; fr.dataset.w = d[1]; fr.dataset.h = d[2];
    if (kind === "leaderboard") fr.src = "/leaderboard.html";
    else fr.srcdoc = frameDoc();
    document.body.appendChild(fr);
    frames.push(fr);
  });
});

// Make the sliced surfaces visible the way the app does, and fill them with the
// worst-case text. Then measure.
function prepHome(d) {
  var show = function (id, disp) { var e = d.getElementById(id); if (e) e.style.display = disp || ""; };
  show("ph-panel-competitive"); show("ph-comp-ranked-dash");
  show("ph-lb-comp-section");   show("gs-cp-item");
  var st = d.getElementById("ph-comp-season-standings"); if (st) st.style.display = "";
  show("ph-comp-king-card");
  FILL.forEach(function (f) { var e = d.getElementById(f[0]); if (e) e.textContent = f[1]; });
  // One realistic row in the OP leaderboard table, so the six columns are
  // measured with content in them rather than empty.
  var tb = d.getElementById("ph-lb-cp-tbody");
  if (tb) tb.innerHTML = '<tr><td class="ph-lb-rank-cell">🥇</td>'
    + '<td><div class="ph-lb-player-cell"><span class="ph-lb-pname">Reefkeeper_2026</span></div></td>'
    + '<td class="ph-lb-score-cell">1,234 OP</td>'
    + '<td class="ph-lb-meta-cell">' + window.__W.king + '</td>'
    + '<td class="ph-lb-meta-cell">Bird + Lobster</td></tr>';
  // The empty state is a screen of its own: measure it too, below the dash.
  var empty = d.getElementById("ph-comp-ranked-empty");
  if (empty) empty.style.display = "";
  // The end-game meta bar gains a sixth tile when a game is ranked.
  var lbl = d.querySelector("#gs-cp-item .gs-meta-label");
  if (lbl) lbl.textContent = "OP Gained · 8th of 8";
}

// Everything that carries an OP string, plus the strips that have to hold them.
var OP_IDS = ["ph-comp-rank-name","ph-comp-cp-val","ph-comp-cp-next-label",
              "ph-comp-king-cp","ph-ss-rank-cp","gs-cp-gained"];

function box(el) { var r = el.getBoundingClientRect(); return { l: r.left, r: r.right, w: r.width, h: r.height }; }

function measure(fr) {
  var d = fr.contentDocument, w = fr.contentWindow;
  var res = { dev: fr.dataset.dev, kind: fr.dataset.kind, vw: w.innerWidth, err: null,
              sideways: 0, clipped: [], outside: [], tabs: [], text: "", missing: [] };
  try {
    res.sideways = d.documentElement.scrollWidth - w.innerWidth;
    var bodyText = (d.body.innerText || "").replace(/\\s+/g, " ");
    res.text = bodyText.slice(0, 4000);

    // 1. Nothing that carries an OP string may be cut off or off screen.
    var ids = fr.dataset.kind === "home" ? OP_IDS : [];
    ids.forEach(function (id) {
      var e = d.getElementById(id);
      if (!e) { res.missing.push(id); return; }
      var b = box(e);
      if (b.w === 0 && b.h === 0) { res.missing.push(id + " (not painted)"); return; }
      if (e.scrollWidth > e.clientWidth + 1) res.clipped.push(id + " " + e.scrollWidth + ">" + e.clientWidth);
      if (b.r > w.innerWidth + 1 || b.l < -1) res.outside.push(id + " [" + Math.round(b.l) + "," + Math.round(b.r) + "]");
    });

    // 2. Every OP-bearing cell in the leaderboard tables, whichever page.
    Array.prototype.forEach.call(d.querySelectorAll(".ph-lb-score-cell,.score-cell,th,.sub-tab,.ph-lb-comp-tab,.summary-label,.info-bar,.gs-meta-item,.ph-empty-panel"), function (e) {
      if (!/OP\\b/.test(e.textContent || "")) return;
      var b = box(e);
      if (b.w === 0 && b.h === 0) return;
      if (e.scrollWidth > e.clientWidth + 1) res.clipped.push((e.id || e.className) + " " + e.scrollWidth + ">" + e.clientWidth);
      if (b.r > w.innerWidth + 1) res.outside.push((e.id || e.className) + " right " + Math.round(b.r));
    });

    // 3. The comp sub-tab strip: every tab reachable, on any width.
    var strips = d.querySelectorAll(".ph-lb-comp-tabs,.sub-tabs");
    Array.prototype.forEach.call(strips, function (strip) {
      var sb = box(strip);
      var ov = w.getComputedStyle(strip).overflowX;
      var scrollable = ov === "auto" || ov === "scroll";
      Array.prototype.forEach.call(strip.children, function (t) {
        var tb = box(t);
        var cut = tb.r > sb.r + 1 || tb.l < sb.l - 1;
        res.tabs.push({ t: (t.textContent || "").trim().slice(0, 24),
                        cut: cut, scrollable: scrollable,
                        offR: Math.round(tb.r - sb.r) });
      });
    });
  } catch (e) { res.err = String(e && e.message || e); }
  return res;
}

// The standalone page opens on Casual. A player reaches the OP tables by
// pressing Competitive, so the harness presses it too rather than reaching in
// and unhiding a section the real page might never show.
function prepLeaderboard(d) {
  var tab = d.querySelector('.mode-tab[data-mode="competitive"]');
  if (tab) tab.click();
  var tb = d.getElementById("cp-tbody");
  if (tb) tb.innerHTML = '<tr><td><span class="rank-medal">🥇</span></td>'
    + '<td>Reefkeeper_2026</td>'
    + '<td class="score-cell top">1,234 OP</td>'
    + '<td>128</td><td>47</td><td>73%</td></tr>';
}

var loaded = 0;
frames.forEach(function (fr) {
  fr.addEventListener("load", function () {
    try {
      if (fr.dataset.kind === "home") prepHome(fr.contentDocument);
      else prepLeaderboard(fr.contentDocument);
    } catch (e) {}
    loaded++;
  });
});

function finish() {
  var out = frames.map(measure);
  document.getElementById("out").textContent = JSON.stringify(out);
}
// Give the stylesheet and leaderboard.html's own boot time to settle, then
// measure. Wrapped, so a throw inside one frame is reported rather than
// leaving the harness silent and the run looking like a hang.
setTimeout(function () {
  try { finish(); }
  catch (e) { document.getElementById("out").textContent =
    JSON.stringify([{ dev: "harness", kind: "harness", err: String(e && e.message || e) }]); }
}, 2500);
</script>
</body></html>`;

const drive = path.join(CLIENT, FILE);
fs.writeFileSync(drive, page);

// ── A static server over the real client directory ──────────────────────────
const PORT = 8000 + Math.floor(Math.random() * 900);
const SERVER_SRC = `
  const fs=require("fs"),path=require("path"),http=require("http");
  const ROOT=${JSON.stringify(CLIENT)};
  const TYPES={".html":"text/html",".js":"application/javascript",".css":"text/css",
               ".json":"application/json",".png":"image/png",".jpg":"image/jpeg",
               ".webp":"image/webp",".svg":"image/svg+xml"};
  http.createServer((req,res)=>{
    let p=decodeURIComponent(req.url.split("?")[0]);
    if(p==="/")p="/preview.html";
    const f=path.join(ROOT,p);
    if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);return res.end("no");}
    res.writeHead(200,{"content-type":TYPES[path.extname(f)]||"application/octet-stream"});
    res.end(fs.readFileSync(f));
  }).listen(${PORT});
`;
const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });

let rows = null;
try {
  execFileSync("sh", ["-c", "sleep 0.6"]);
  // Every host but localhost is mapped at the static server, so the fonts
  // leaderboard.html asks Google for answer instantly (404) instead of hanging
  // the virtual clock on a network fetch that never lands.
  const dom = execFileSync(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
    "--hide-scrollbars", "--window-size=1200,900", "--virtual-time-budget=60000",
    `--host-resolver-rules=MAP * 127.0.0.1:${PORT}, EXCLUDE localhost`,
    "--dump-dom", `http://localhost:${PORT}/${FILE}`],
    { encoding: "utf8", maxBuffer: 128e6, stdio: ["ignore", "pipe", "ignore"] });
  const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
  const raw = m ? m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                      .replace(/&lt;/g, "<").replace(/&gt;/g, ">") : "";
  if (!raw || raw === "PENDING") throw new Error("the harness never reported");
  rows = JSON.parse(raw);
} finally {
  if (!process.env.CC_KEEP) { try { fs.unlinkSync(drive); } catch (_) {} }
  try { server.kill(); } catch (_) {}
}

// ── Report ──────────────────────────────────────────────────────────────────
let pass = 0, fail = 0;
function check(ok, label, detail) {
  if (ok) { pass++; console.log("  ✓ " + label); }
  else { fail++; console.log("  ✗ " + label + (detail ? "\n      " + detail : "")); }
}

console.log(`\nOcean Points across ${DEVICES.length} devices (worst case: "${LONGEST}")\n`);

const byDev = {};
rows.forEach(r => { (byDev[r.dev] = byDev[r.dev] || []).push(r); });

DEVICES.forEach(([name, w, h]) => {
  const got = byDev[name] || [];
  console.log(`── ${name}  ${w}×${h} ──`);
  ["home", "leaderboard"].forEach(kind => {
    const r = got.find(x => x.kind === kind);
    if (!r) { check(false, `${kind}: reported`); return; }
    if (r.err) { check(false, `${kind}: measured without throwing`, r.err); return; }
    check(r.sideways <= 1, `${kind}: the page does not scroll sideways`,
          `document is ${r.sideways}px wider than the ${r.vw}px screen`);
    check(r.clipped.length === 0, `${kind}: no OP text is cut off`, r.clipped.join(", "));
    check(r.outside.length === 0, `${kind}: no OP text runs off the screen`, r.outside.join(", "));
    check(r.missing.length === 0, `${kind}: every OP field is painted`, r.missing.join(", "));
    const cutTabs = r.tabs.filter(t => t.cut && !t.scrollable);
    check(cutTabs.length === 0, `${kind}: every sub-tab is reachable`,
          cutTabs.map(t => `"${t.t}" +${t.offR}px`).join(", "));
    check(/\bOP\b/.test(r.text), `${kind}: the screen says OP`);
    check(!/\bCP\b/.test(r.text), `${kind}: no stray "CP" survives`,
          (/[^.]*\bCP\b[^.]*/.exec(r.text) || [""])[0].trim().slice(0, 120));
  });
});

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
