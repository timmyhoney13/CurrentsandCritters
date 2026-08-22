#!/usr/bin/env node
/* Browser check for the bracket builder's CONNECTION LINES.
 *
 * Run:  node test_tournament_builder_lines.js       (needs Google Chrome installed)
 *
 * This is the regression test for "the lines are not connecting in the right
 * places": it renders the real builder in headless Chrome at several zoom levels
 * and measures, in SCREEN pixels, where each drawn SVG path actually starts and
 * ends versus the gold advance handle and the player spot it is supposed to join.
 * The bug it pins down was a missing `transform-origin` on the links SVG, which
 * slid every line away from its boxes by half the canvas at any zoom but 1, so
 * checking only at zoom 1 would have passed while the builder looked broken.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("SKIP: no Chrome/Chromium found: cannot run the line-geometry check.");
  process.exit(0);
}

const builderSrc = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/tournament-builder.js"), "utf8");

const page = `<!doctype html><html><head><meta charset="utf-8"><title>lines</title></head>
<body><div id="out">RUNNING</div>
<script>
window.__ccTourney = {
  toast(){},
  post(){ return new Promise((_, rej) => rej(new Error("offline"))); },
};
</script>
<script>${builderSrc}</script>
<script>
// How far a line endpoint may sit from the thing it connects, in CSS pixels.
// The handle is a 26px circle and rows are ~24px tall, so 6px is comfortably
// inside "visually attached" while still catching any real misalignment.
var TOL = 6;

function screenPoint(pathEl, len) {
  var p = pathEl.getPointAtLength(len);
  var m = pathEl.getScreenCTM();
  return { x: p.x * m.a + p.y * m.c + m.e, y: p.x * m.b + p.y * m.d + m.f };
}
function centre(el) {
  var r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}
function leftEdge(el) {
  var r = el.getBoundingClientRect();
  return { x: r.left, y: r.top + r.height / 2 };
}
function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }

function checkAtCurrentZoom(label, results) {
  var B = window.__ccTourneyBuilder._state;
  var svg = document.getElementById("ccTB-links");
  var paths = svg.querySelectorAll("path.ccTB-link:not(.ghost)");
  // Rebuild the expected (source handle -> target spot) list in DOM order, which
  // is the order drawLinks() appends them.
  var expected = [];
  B.matches.forEach(function (m) {
    m.slots.forEach(function (s, si) {
      if (!s.source) return;
      var srcBox = document.getElementById("ccTB-box-" + s.source);
      var dstBox = document.getElementById("ccTB-box-" + m.id);
      if (!srcBox || !dstBox) return;
      expected.push({
        port: srcBox.querySelector('.ccTB-port[data-rank="' + s.rank + '"]'),
        row: dstBox.querySelector('.ccTB-row[data-si="' + si + '"]'),
        from: s.source, to: m.id, si: si, rank: s.rank,
      });
    });
  });
  if (paths.length !== expected.length) {
    results.push("FAIL " + label + ": drew " + paths.length + " lines for " + expected.length + " connections");
    return;
  }
  if (!expected.length) { results.push("FAIL " + label + ": no connections to check"); return; }
  var worstStart = 0, worstEnd = 0, bad = 0;
  for (var i = 0; i < paths.length; i++) {
    var e = expected[i];
    if (!e.port || !e.row) { results.push("FAIL " + label + ": missing handle/spot for link " + i); return; }
    var total = paths[i].getTotalLength();
    var ds = dist(screenPoint(paths[i], 0), centre(e.port));
    var de = dist(screenPoint(paths[i], total), leftEdge(e.row));
    worstStart = Math.max(worstStart, ds);
    worstEnd = Math.max(worstEnd, de);
    if (ds > TOL || de > TOL) {
      bad++;
      if (bad === 1) {
        results.push("FAIL " + label + ": link " + e.from + "#" + e.rank + " -> " + e.to +
          " spot " + (e.si + 1) + " is " + ds.toFixed(1) + "px from its handle and " +
          de.toFixed(1) + "px from its spot (zoom " + B.zoom.toFixed(3) + ")");
      }
    }
  }
  if (!bad) {
    results.push("PASS " + label + " (zoom " + B.zoom.toFixed(3) + ", " + paths.length +
      " lines, worst start " + worstStart.toFixed(2) + "px / end " + worstEnd.toFixed(2) + "px)");
  }
}

function run() {
  var results = [];
  var TB = window.__ccTourneyBuilder;
  try {
    TB.open(null, function () {});           // starter design: 8-player 1v1 bracket
    var B = TB._state;
    // A mixed bracket exercises multi-rank handles too: 12 players, 4 per match,
    // top 2 advance -> two ports per opening match.
    TB._test.buildTemplate(12, 4, 2);
    TB._test.relabelAuto();

    [1, 0.6, 0.42, 1.35, 2].forEach(function (z) {
      B.zoom = z; B.panX = 37; B.panY = -21;
      // Full re-render, the same path the app takes.
      TB.open(TB.design(), function () {});
      B.zoom = z; B.panX = 37; B.panY = -21;
      TB._state.sel = null;
      // force a redraw at this exact zoom
      var ev = document.getElementById("ccTB-zin");
      TB.validate();
      (function reRender() {
        // render() is private; re-opening with the same design re-renders it.
        TB.open(TB.design(), function () {});
        B.zoom = z; B.panX = 37; B.panY = -21;
      })();
      // apply the transform + redraw by nudging zoom through the public control
      document.getElementById("ccTB-zin").click();
      document.getElementById("ccTB-zout").click();
      checkAtCurrentZoom("zoom=" + z, results);
    });
  } catch (err) {
    results.push("FAIL exception: " + (err && err.stack || err));
  }
  document.getElementById("out").textContent = results.join("\\n");
}
run();
</script></body></html>`;

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-lines-"));
const file = path.join(tmp, "lines.html");
fs.writeFileSync(file, page);

let dom;
try {
  dom = execFileSync(CHROME, [
    "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    "--window-size=1400,900", "--virtual-time-budget=6000",
    "--dump-dom", "file://" + file,
  ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 90000 });
} catch (e) {
  console.error("Chrome failed to run:", e.message);
  process.exit(1);
}

const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
const report = m ? m[1].replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&").trim() : "(no output)";
const lines = report.split("\n");
fs.rmSync(tmp, { recursive: true, force: true });

// The LIVE tournament bracket (tournament-ui.js) draws its connectors the same
// way, one SVG given the same transform as the grid of matches, and had the
// same missing origin. A full-app browser run is a lot of scaffolding for a CSS
// pairing, so assert the pairing itself: wherever a transformed layer and its
// lines SVG share a transform, they must share an origin.
const uiSrc = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/tournament-ui.js"), "utf8");
[
  { file: "tournament-ui.js", src: uiSrc, layer: ".ccT-bracket{", svg: "svg.ccT-lines{" },
  { file: "tournament-builder.js", src: builderSrc, layer: ".ccTB-canvas{", svg: "svg.ccTB-links{" },
].forEach(({ file, src, layer, svg }) => {
  const rule = (sel) => {
    const i = src.indexOf(sel);
    return i < 0 ? null : src.slice(i, src.indexOf("}", i));
  };
  const a = rule(layer), b = rule(svg);
  const has = (r) => r && /transform-origin\s*:\s*0\s+0/.test(r);
  if (!a || !b) lines.push(`FAIL ${file}: could not find ${layer} / ${svg}`);
  else if (has(a) !== has(b)) {
    lines.push(`FAIL ${file}: ${layer} and ${svg} share a transform but not a transform-origin` +
      `: connector lines will drift from their matches at any zoom but 1`);
  } else lines.push(`PASS ${file}: transformed layer and its lines SVG share transform-origin`);
});

console.log(lines.join("\n"));
const failed = lines.filter(l => l.startsWith("FAIL"));
console.log(`\n${lines.filter(l => l.startsWith("PASS")).length} passed, ${failed.length} failed`);
process.exit(failed.length ? 1 : 0);
