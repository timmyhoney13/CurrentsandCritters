#!/usr/bin/env node
/* "Generate Current →", the button that actually starts a game.
 *
 * It is ONE baked PNG with its own lettering painted in (nc-btn-full.png), and
 * the <span> inside it is deliberately `color: transparent` so the words are
 * not printed twice over the art. That is the right look and the wrong
 * failure: when the art does not paint, the button has no fill, no border and
 * no readable text, so it is not a broken button, it is NOTHING. The modal
 * shows four fields, a gap, and empty air where the only way forward used to
 * be. Players reported it as "the Generate Current button is gone".
 *
 * Three things keep it on screen:
 *   1. THE ART IS SERVED. It is on disk AND multiplayer_server.py has a route
 *      for it, which are two different facts (the server has no static root).
 *   2. THERE IS A BUTTON UNDER THE ART. A painted gold base layer in the same
 *      declaration, so a button with no artwork is still a button, and a JS
 *      load check that reveals the real lettering when the art is missing.
 *   3. IT IS REACHABLE ON A SHORT SCREEN. The box used to be `overflow:hidden`
 *      with no max-height, so on a phone held sideways the button sat below
 *      the bottom edge of a box that could not scroll.
 *
 * Run:  node test_new_current_button.js      (render half needs Google Chrome)
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const APP    = read("js/preview-app.js");
const HTML   = read("preview.html");
const CSS    = read("css/preview.css");
const SERVER = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

// The button's own CSS block, so every rule below is read from the real one.
const BTN_CSS = (() => {
  const i = CSS.indexOf("#nc-create-btn {");
  return i < 0 ? "" : CSS.slice(i, CSS.indexOf("}", i) + 1);
})();
const BOX_CSS = (() => {
  const i = CSS.indexOf("#new-current-box {");
  return i < 0 ? "" : CSS.slice(i, CSS.indexOf("}", i) + 1);
})();

// ── 1. The art is really served ─────────────────────────────────────────────
console.log("\nthe artwork is on disk AND routed");

check("the button is in the markup", /id="nc-create-btn"/.test(HTML));
check("…with the label the code rewrites", /class="nc-btn-label">Generate Current/.test(HTML));
check("its CSS block was found", BTN_CSS.length > 100);

const artUrl = (BTN_CSS.match(/url\(["']?([^"')]+)["']?\)/) || [])[1] || "";
check(`the art is addressed from the site root (${artUrl})`, artUrl.startsWith("/"),
      "a relative url in /css/preview.css resolves to /css/ and 404s");
const artFile = artUrl.replace(/^\//, "");
check(`${artFile} is in the client`, artFile && fs.existsSync(path.join(CLIENT, artFile)));
// multiplayer_server.py routes every path by hand: on disk is not the same as
// on the web, and a missing route is exactly how this button goes blank.
check("…and multiplayer_server.py has a route for it",
      artFile && SERVER.includes(artFile.replace(/\.png$/, "")),
      "the game server has no static root, every asset needs an explicit branch");

// ── 2. There is a button under the art ──────────────────────────────────────
console.log("\nwith no artwork it is still a button, not a hole");

// The label is transparent on purpose (the art carries the words), so the
// button MUST paint something of its own underneath, in the same declaration.
check("the label is transparent, because the art carries the lettering",
      /color:\s*transparent/.test(BTN_CSS));
check("the art is layered OVER a painted fill, so a missing image leaves a button",
      /background:[^;]*url\([^)]*\)[^;]*,[^;]*(linear-gradient|#[0-9a-fA-F]{3,8}|rgb)/.test(BTN_CSS),
      "background is a single url() layer with nothing behind it");

// And when the art is genuinely gone, the words have to come back, or the
// button is a gold blank that says nothing.
check("a class exists for 'the art did not load'", /#nc-create-btn\.nc-no-art/.test(CSS));
check("…and it makes the lettering readable again",
      /#nc-create-btn\.nc-no-art[^{]*\{[^}]*color:\s*(#|rgb|var)/.test(CSS));
check("something actually checks whether the art loaded",
      /nc-no-art/.test(APP), "nothing in preview-app.js ever sets the class");
check("…by listening for the image failing, not by guessing",
      /onerror[\s\S]{0,200}nc-no-art|addEventListener\("error"[\s\S]{0,200}nc-no-art/.test(APP));

// ── 3. Reachable on a short screen ──────────────────────────────────────────
console.log("\nthe box can always be scrolled to the button");

check("the box has a ceiling", /max-height:/.test(BOX_CSS));
check("…and scrolls instead of clipping what will not fit",
      /overflow-y:\s*auto/.test(BOX_CSS));

// ── Render ──────────────────────────────────────────────────────────────────
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: skipping the render half.");
} else {
  const CHROME_CHROME_PX = 87;

  const ms = HTML.indexOf('<div id="new-current-modal">');
  const me = HTML.indexOf("<!-- ══ BOT MATCH MODAL", ms);
  if (ms < 0 || me < 0) { console.log("\nFAIL: could not slice the New Current modal out of preview.html"); process.exit(1); }
  const MODAL = HTML.slice(ms, me).replace('<div id="new-current-modal">', '<div id="new-current-modal" class="open">');

  // Two runs of the same page: one where the art resolves, one where every
  // request for it fails. The second is the bug this file exists for.
  function pageFor(artLoads, extraScript) {
    // The page is loaded over file://, where the real stylesheet's
    // "/nc-btn-full.png" resolves to the filesystem root and fails. So the
    // BROKEN case needs no override at all: it is the real rule, really
    // failing. Only the working case swaps the first layer for a gold pixel,
    // and it re-states the fallback layer so the declaration still has both.
    const good = "data:image/gif;base64,R0lGODlhAQABAIAAAPzJWAAAACH5BAAAAAAALAAAAAABAAEAAAICRAEAOw==";
    const override = artLoads
      ? `#nc-create-btn { background-image: url("${good}"),
           linear-gradient(100deg, #fbbf58 0%, #fdc964 50%, #fcc55c 100%) !important; }`
      : "";
    return `<!doctype html><html><head><meta charset="utf-8"><style>${CSS}</style>
<style>html,body{margin:0;height:100%;background:#06306b}
  ${override}</style></head>
<body>${MODAL}<pre id="out">RUNNING</pre>
<script>
(function(){
  var btn = document.getElementById("nc-create-btn");
  var box = document.getElementById("new-current-box");
  var lbl = btn.querySelector(".nc-btn-label");
  ${extraScript || ""}
  function alpha(c){ var m=/rgba?\\(([^)]+)\\)/.exec(c); if(!m) return 1;
    var p=m[1].split(",").map(Number); return p.length>3?p[3]:1; }
  function done(){
    var b = btn.getBoundingClientRect();
    var cs = getComputedStyle(btn), ls = getComputedStyle(lbl);
    document.getElementById("out").textContent = JSON.stringify({
      w: Math.round(b.width), h: Math.round(b.height),
      // Does the player see ANY button body? Either a painted background
      // colour/gradient of its own, or a border.
      paintedFill: cs.backgroundImage.split(",").length > 1
                   || alpha(cs.backgroundColor) > 0.05
                   || (cs.borderStyle !== "none" && alpha(cs.borderTopColor) > 0.05),
      labelAlpha: alpha(ls.color),
      labelText: (lbl.textContent || "").trim(),
      // The one question that matters: is there anything to see at all?
      readable: alpha(ls.color) > 0.5
                || cs.backgroundImage.split(",").length > 1
                || alpha(cs.backgroundColor) > 0.05
    });
  }
  // Give the image every chance to settle (load or fail) before measuring.
  setTimeout(done, 1200);
})();
</script></body></html>`;
  }

  function run(label, page, w, h) {
    const f = path.join(os.tmpdir(), "cc_nc_btn_" + label.replace(/\W+/g, "_") + ".html");
    fs.writeFileSync(f, page);
    const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
      "--hide-scrollbars", `--window-size=${w},${h + CHROME_CHROME_PX}`,
      "--virtual-time-budget=6000", "--dump-dom", "file://" + f],
      { encoding: "utf8", maxBuffer: 64e6 });
    const m = /<pre id="out">([\s\S]*?)<\/pre>/.exec(dom);
    return JSON.parse((m ? m[1] : "{}").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
  }

  console.log("\nrendered with the artwork available");
  {
    const r = run("art", pageFor(true), 1280, 850);
    check(`the button has a size (${r.w}x${r.h})`, r.w > 100 && r.h > 40);
    check("the art is the face of it, so the label stays out of the way", r.labelAlpha < 0.5);
    check("the words are still there for a screen reader", r.labelText.startsWith("Generate Current"));
  }

  console.log("\nrendered with the artwork missing, the bug this file is for");
  {
    // The real page adds .nc-no-art from the image's own error event; here the
    // image is swapped by the stylesheet, so the class is applied directly and
    // what is tested is that the CLASS does its job.
    const r = run("noart", pageFor(false, 'btn.classList.add("nc-no-art");'), 1280, 850);
    check(`the button still has a size (${r.w}x${r.h})`, r.w > 100 && r.h > 40);
    check("it still paints a button body", r.paintedFill);
    check(`the lettering comes back and is opaque (alpha ${r.labelAlpha})`, r.labelAlpha > 0.5);
    check("there is SOMETHING on screen where the button is", r.readable);
  }

  console.log("\nreachable on a phone held sideways, with the tallest settings");
  {
    const tall = `
      document.getElementById("nc-mode").value = "team";
      document.getElementById("nc-team-count").closest(".nc-field").style.display = "";
      document.getElementById("nc-password-row").style.display = "block";
      document.getElementById("nc-current-err").textContent = "That join code is already in use.";
    `;
    const extra = tall + `
      var _origDone = null;`;
    const page = pageFor(true, tall);
    const f = path.join(os.tmpdir(), "cc_nc_btn_short.html");
    // Measure reachability: after scrolling the box to the bottom, is the
    // button inside the window?
    fs.writeFileSync(f, page.replace(
      "setTimeout(done, 1200);",
      `setTimeout(function(){
         box.scrollTop = box.scrollHeight;
         var b = btn.getBoundingClientRect();
         var vh = document.documentElement.clientHeight;
         var extra2 = { onScreen: b.top >= 0 && b.bottom <= vh + 1,
                        // A ceiling on a flex column squashes its rows rather
                        // than overflowing them, which "fixes" the scroll by
                        // flattening the button to a sliver. Full height, or
                        // it is not a button.
                        fullHeight: Math.round(b.height),
                        hitIsButton: (function(){
                          var el = document.elementFromPoint(b.left + b.width/2,
                                     Math.min(Math.max(b.top + b.height/2, 1), vh - 2));
                          return !!el && (el === btn || btn.contains(el));
                        })(),
                        scrollable: box.scrollHeight > box.clientHeight + 1,
                        boxFits: box.getBoundingClientRect().height <= vh + 1 };
         done();
         var o = JSON.parse(document.getElementById("out").textContent);
         Object.assign(o, extra2);
         document.getElementById("out").textContent = JSON.stringify(o);
       }, 1200);`));
    const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
      "--hide-scrollbars", "--window-size=844," + (390 + CHROME_CHROME_PX),
      "--virtual-time-budget=6000", "--dump-dom", "file://" + f],
      { encoding: "utf8", maxBuffer: 64e6 });
    const m = /<pre id="out">([\s\S]*?)<\/pre>/.exec(dom);
    const r = JSON.parse((m ? m[1] : "{}").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
    check("the box fits inside the window", r.boxFits);
    check("the button can be scrolled to", r.onScreen);
    check(`…at its full height, not squashed by the flex column (${r.fullHeight}px)`,
          r.fullHeight >= 70);
    check("…and the tap lands on the button", r.hitIsButton);
  }
}

console.log("\n" + "=".repeat(42));
console.log(`RESULT: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
