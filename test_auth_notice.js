#!/usr/bin/env node
/* The sign-in screen's one status line, and the corner chip that used to sit
 * across the top of everything.
 *
 * Two things a player sees when they DON'T sign in:
 *
 *   1. CLOSING THE GOOGLE WINDOW IS NOT AN ERROR. Backing out of the popup put
 *      "Sign-in popup was closed." on login-bg.png in bare red 12px text, in
 *      the one colour this game uses for nothing else. A decision, painted as a
 *      fault. It is a sea-glass note now (icon + navy lettering on the same
 *      pale card as CREATE YOUR USERNAME), it says something a person would
 *      say, and the calm ones fade themselves out. Three things break it and
 *      none of them show in a diff:
 *        - the note is styled by "#auth-step-choose > .auth-err", so it must
 *          keep the > form (a descendant rule reaches into the guest card and
 *          makes Dive In invisible: see test_guest_card.js);
 *        - .auth-note on the element is the ONLY switch that routes setAuthMsg
 *          through the dressed renderer, and setAuthMsg overwrites className on
 *          every other host, so losing the class silently restores red text;
 *        - it hides with opacity+visibility, not display, so an empty note
 *          still has a box: it must never take a click meant for the artwork.
 *
 *   2. THE FULL-SCREEN NUDGE IS NOT PART OF SIGNING IN. #cc-fs-resume was a
 *      210px orange pill pinned top-centre at z-index 100000, which is on top
 *      of the notice bar in-game and on top of the artwork on the sign-in
 *      screen. It is a small ocean-glass chip in the bottom-left corner now,
 *      gated on body.cc-signed-in, so it cannot appear before a player is in.
 *
 * Run:  node test_auth_notice.js      (needs Google Chrome / Chromium)
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const HTML = read("preview.html");
const CSS  = read("css/preview.css");
const APP  = read("js/preview-app.js");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

// ════════════════════════════════════════════════════════════════════════
//  SOURCE
// ════════════════════════════════════════════════════════════════════════
console.log("\nbacking out of sign-in is not a fault, and is not written as one");
{
  check("the old bare line is gone",
        !/"Sign-in popup was closed\."/.test(APP)
        && !/"Sign-in cancelled\."/.test(APP));
  check("…replaced by something a person would say",
        /No harm done, that sign-in window closed\./.test(APP));
  check("the cancel codes are named once, in a list",
        /const AUTH_CANCEL_CODES = \[/.test(APP)
        && /"auth\/popup-closed-by-user",/.test(APP)
        && /"auth\/popup-cancelled-by-user",/.test(APP)
        && /"auth\/cancelled-popup-request",/.test(APP));
  check("…and every place that shows a sign-in failure asks that list",
        (APP.match(/isAuthCancel\(e\.code\) \? "info" : "warn"/g) || []).length === 3,
        "the two Google sign-in paths and the redirect result");
  check("a player closing a window is not filed in the error log as a failure",
        /if \(!isAuthCancel\(e\.code\)\) ccReport\("firebase_google_signin_failed"/.test(APP));
  check("nothing calls the browser's own error text at the player",
        !/e\.message/.test(APP.slice(APP.indexOf("function beginCleanGoogleSignIn"),
                                     APP.indexOf("function beginCleanGoogleSignIn") + 1600)));
}

console.log("\nthe note is rendered, not just coloured");
{
  // The renderer alone: elsewhere in a 30k-line file, "msg" is a class name.
  const NOTICE = APP.slice(APP.indexOf("function setAuthNotice"),
                           APP.indexOf("window.__ccAuthNote"));
  check("setAuthMsg takes a tone, not only a boolean",
        /function setAuthMsg\(id, msg, tone\)/.test(APP)
        && /typeof tone === "string" \? tone : \(tone \? "ok" : "warn"\)/.test(APP));
  check("…and .auth-note is what routes it to the dressed renderer",
        /el\.classList\.contains\("auth-note"\)\) \{ setAuthNotice\(/.test(APP));
  check("…which the element on the chooser screen actually carries",
        /class="auth-err auth-note" id="auth-choose-err"/.test(HTML));
  check("…and which is announced, since it is a status line",
        /id="auth-choose-err" role="status" aria-live="polite"/.test(HTML));
  check("the only markup written is the icon this file owns",
        /el\.innerHTML = AUTH_NOTE_ICON\[kind\];/.test(APP)
        && /const AUTH_NOTE_ICON = \{/.test(APP));
  check("…every word from Firebase or a player goes in as text",
        /say\.textContent = msg;/.test(NOTICE)
        && !/innerHTML\s*=\s*[^;]*\bmsg\b/.test(NOTICE),
        "the message must never be written as markup");
  check("a calm note takes itself away again",
        /if \(kind === "info"\) _authNoteTimer = setTimeout\(/.test(APP));
  check("…and a warning does not, so it is still there when you look",
        /_authNoteTimer\) \{ clearTimeout\(_authNoteTimer\)/.test(APP),
        "the timer must also be cancelled when a new note replaces an old one");
  check("backing out of the guest card clears what the chooser was saying",
        /function closeGuestCard\(\)[\s\S]{0,400}?setAuthMsg\("auth-choose-err", "", true\);/.test(APP));
}

console.log("\nthe note is dressed like the screen it lies on");
{
  const block = (() => {
    const a = CSS.indexOf("#auth-step-choose > .auth-err {");
    return a < 0 ? "" : CSS.slice(a, CSS.indexOf("/* ══ GUEST SIGN-IN CARD", a));
  })();
  check("there is a rule for it at all", block.length > 200);
  check("it keeps the direct-child form the guest card depends on",
        /#auth-step-choose > \.auth-err/.test(CSS)
        && !/#auth-step-choose\s+\.auth-err\b/.test(CSS),
        "a descendant rule here absolutely positions Dive In off the card");
  check("no red text left on the artwork",
        !/#9a2d37/.test(block) && !/color:\s*var\(--red\)/.test(block));
  check("…it is the sea glass and navy ink of the cards either side of it",
        /color:\s*#123a68/.test(block) && /rgba\(250,253,255/.test(block));
  check("…with the tone carried by the icon, teal or gold",
        /is-info \.auth-note-ico \{ color: #1c7fab/.test(block)
        && /is-warn \.auth-note-ico \{ color: #b07a10/.test(block));
  check("it hides by fading, not by display, so it can fade both ways",
        /visibility:\s*hidden/.test(block) && /opacity:\s*0/.test(block)
        && !/display:\s*none/.test(block));
  check("…and never takes a click meant for the artwork",
        /pointer-events:\s*none/.test(block));
  check("it is centred on the same column as the two painted buttons",
        /left:\s*50%/.test(block) && /translateX\(-50%\)/.test(block));
  check("a reduced-motion player gets no animation",
        /prefers-reduced-motion[\s\S]{0,200}?#auth-step-choose > \.auth-err/.test(block));
}

console.log("\nthe guest card says its piece the same way");
{
  check("the message row is tinted and rounded, not a naked red line",
        /#auth-guest-overlay \.auth-err:not\(:empty\)[\s\S]{0,180}?background:/.test(CSS));
  check("…and an empty one still draws nothing",
        /#auth-guest-overlay \.auth-err:not\(:empty\),/.test(CSS),
        ":empty is the guard, because setAuthMsg writes \"\" rather than removing it");
}

console.log("\nthe full-screen chip is out of the way, and only exists once you are in");
{
  const block = (() => {
    const a = CSS.indexOf("#cc-fs-resume {");
    return a < 0 ? "" : CSS.slice(a, CSS.indexOf("#auth-screen {", a));
  })();
  check("it is not pinned across the top of the screen any more",
        !/top:\s*10px/.test(block) && !/left:\s*50%/.test(block));
  check("…it sits in a corner instead",
        /left:\s*14px/.test(block) && /bottom:\s*calc\(14px/.test(block));
  check("…dressed like the game's own panels, not a loud orange pill",
        !/#f6c178/.test(block) && /rgba\(95,179,214/.test(block));
  check("…and it is quiet until you look at it",
        /\.ccfs-word \{[\s\S]{0,120}?max-width:\s*0/.test(block)
        && /:hover \.ccfs-word,[\s\S]{0,120}?max-width:\s*190px/.test(block));
  check("nothing shows it until the player is signed in",
        /body\.cc-signed-in #cc-fs-resume\.show \{ display: flex; \}/.test(CSS)
        && !/^\s*#cc-fs-resume\.show \{/m.test(CSS));
  check("…the class goes on when the lobby is revealed",
        /function revealLobby[\s\S]{0,400}?document\.body\.classList\.add\("cc-signed-in"\)/.test(APP));
  check("…and comes off the moment any sign-in step is shown again",
        /function showStep[\s\S]{0,600}?document\.body\.classList\.remove\("cc-signed-in"\)/.test(APP));
  check("the old 210px banner floor is gone with the banner",
        !/#cc-fs-resume \{\s*min-width: 210px/.test(CSS));
  check("it no longer sits on top of every layer in the game",
        /z-index:\s*8950/.test(block) && !/z-index:\s*100000/.test(block),
        "above Player Home (8900), below every modal (9100+)");
  check("…and it stays out of a game, where the action bar already has the button",
        /const inGame = \(\) => !!\(gameEl && gameEl\.style\.display !== "none"\);/.test(APP)
        && /wantsFullscreen && !isFs\(\) && !inGame\(\)/.test(APP),
        "the two bottom corners in a game are seat pills");
  check("…which is re-checked when the game screen opens or closes, not only on Esc",
        /new MutationObserver\(onFsChange\)\.observe\(gameEl/.test(APP),
        "style.display on #pv-game is written in a dozen places and fires no event");
  check("the button carries a glyph and a label it can open out to",
        /id="cc-fs-resume"[\s\S]{0,220}?class="ccfs-glyph"[\s\S]{0,120}?class="ccfs-word"/.test(HTML));
}

// ════════════════════════════════════════════════════════════════════════
//  DRIVE  (a real browser, at five widths)
// ════════════════════════════════════════════════════════════════════════
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => fs.existsSync(p));

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: skipping the drive half.");
} else {
  const PORT = 9660 + (process.pid % 300);
  const SERVER_SRC = `
    const fs=require("fs"),path=require("path"),http=require("http");
    const ROOT=${JSON.stringify(CLIENT)};
    const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",
      ".json":"application/json",".png":"image/png",".jpg":"image/jpeg",
      ".webp":"image/webp",".svg":"image/svg+xml",".ico":"image/x-icon",".m4a":"audio/mp4"};
    http.createServer((req,res)=>{
      const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
      const f=path.join(ROOT,rel);
      if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);res.end();return;}
      res.writeHead(200,{"Content-Type":MIME[path.extname(f)]||"application/octet-stream"});
      fs.createReadStream(f).pipe(res);
    }).listen(${PORT});
  `;

  // Firebase never resolves here and there is no account, so the chooser is
  // opened the way showStep() opens it and the note is put up through the app's
  // own renderer (window.__ccAuthNote → setAuthMsg → setAuthNotice).
  const DRIVER = `
<script>
(function () {
  var tick = 0;
  var iv = setInterval(function () {
    if (++tick > 500) { clearInterval(iv); return; }
    try {
      var spl = document.getElementById("cc-fs-splash"); if (spl) spl.style.display = "none";
      var ls = document.getElementById("auth-loading-screen");
      if (ls) { ls.classList.add("hidden"); ls.style.display = "none"; }
      var as = document.getElementById("auth-screen");
      if (as) { as.classList.remove("hidden"); as.classList.remove("on-nickname"); }
      ["auth-step-guest","auth-step-nickname","auth-step-launch"].forEach(function (id) {
        var e = document.getElementById(id); if (e) e.style.display = "none";
      });
      var c = document.getElementById("auth-step-choose");
      if (c) { c.style.display = ""; c.classList.add("is-armed"); }
      if (tick > 25 && window.__ccAuthNote) {
        clearInterval(iv);
        window.__ccAuthNote("No harm done, that sign-in window closed. Dive in whenever you're ready.", "info");
        window.__ccReady = 1;
      }
    } catch (e) {}
  }, 40);
})();
</script>`;

  const SIZES = [[320, 700], [390, 844], [430, 932], [768, 900], [1440, 900]];
  const WRAPPER = (page) => `<!doctype html><meta charset="utf-8">
<style>body{margin:0;display:flex}iframe{border:0}</style>
${SIZES.map(([w, h], i) => `<iframe id="f${i}" width="${w}" height="${h}" src="/${page}?game_window=1"></iframe>`).join("")}
<div id="out">PENDING</div>
<script>
// Two phases on purpose. The note went up at page time ~1s and puts itself away
// after 7s, so "is it still up" and "did it go away" cannot be the same reading:
// the first pass records that it left, then posts a fresh one to measure.
var sizes = ${JSON.stringify(SIZES)}, res = [], faded = [];
setTimeout(function () {
  sizes.forEach(function (sz, i) {
    try {
      var fr = document.getElementById("f" + i), d = fr.contentDocument, w = fr.contentWindow;
      faded[i] = !d.getElementById("auth-choose-err").classList.contains("is-on");
      w.__ccAuthNote("No harm done, that sign-in window closed. Dive in whenever you're ready.", "info");
    } catch (e) { faded[i] = null; }
  });
}, 10500);
setTimeout(function () {
  sizes.forEach(function (sz, i) {
    try {
      var fr = document.getElementById("f" + i), d = fr.contentDocument, w = fr.contentWindow;
      var note = d.getElementById("auth-choose-err");
      var step = d.getElementById("auth-step-choose");
      var gBtn = d.getElementById("auth-guest-btn");
      var oBtn = d.getElementById("auth-choose-google-btn");
      var cs = w.getComputedStyle(note);
      var nb = note.getBoundingClientRect(), sb = step.getBoundingClientRect();
      var gb = gBtn.getBoundingClientRect(), ob = oBtn.getBoundingClientRect();
      var say = note.querySelector(".auth-note-say");
      var over = function (a, b) {
        return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
      };
      var mid = d.elementFromPoint(Math.round(nb.left + nb.width / 2),
                                   Math.round(nb.top + nb.height / 2));
      // The chip: forced into the state it appears in, and then denied it.
      var chip = d.getElementById("cc-fs-resume");
      chip.classList.add("show");
      d.body.classList.remove("cc-signed-in");
      var chipBeforeSignIn = w.getComputedStyle(chip).display;
      d.body.classList.add("cc-signed-in");
      var chipCs = w.getComputedStyle(chip), cb = chip.getBoundingClientRect();

      res.push({
        w: sz[0], vw: w.innerWidth, vh: w.innerHeight,
        shown: note.classList.contains("is-on") && cs.visibility === "visible" && +cs.opacity > .9,
        tone: note.className,
        hasIcon: !!note.querySelector("svg.auth-note-ico"),
        // The words are the player's, and they must be words, not markup.
        said: say ? say.textContent.slice(0, 24) : "",
        // Not red, and not see-through: a card, on artwork.
        ink: cs.color,
        painted: cs.backgroundImage !== "none" || cs.backgroundColor !== "rgba(0, 0, 0, 0)",
        // It sits under the two painted buttons and touches neither.
        below: nb.top >= ob.bottom - 1,
        clearOfButtons: !over(nb, gb) && !over(nb, ob),
        insideStep: nb.left >= sb.left - 1 && nb.right <= sb.right + 1 && nb.bottom <= sb.bottom + 1,
        // A sentence, not a clipped sliver.
        lines: Math.round(nb.height),
        clipped: note.scrollWidth > note.clientWidth + 1 || note.scrollHeight > note.clientHeight + 1,
        // Transparent to the pointer: the artwork is what is under it.
        eatsClicks: !!(mid && (mid === note || note.contains(mid))),
        sideways: d.documentElement.scrollWidth > w.innerWidth + 1,
        chipBeforeSignIn: chipBeforeSignIn,
        chipAfterSignIn: chipCs.display,
        chipW: Math.round(cb.width), chipH: Math.round(cb.height),
        chipLeft: Math.round(cb.left), chipBottomGap: Math.round(w.innerHeight - cb.bottom),
        chipTop: Math.round(cb.top),
        fadedLater: faded[i]
      });
    } catch (e) { res.push({ w: sz[0], err: String(e && e.message) }); }
  });
  document.getElementById("out").textContent = JSON.stringify(res);
}, 11400);
</script>`;

  const page = "__note_step.html", wrap = "__note_wrap.html";
  fs.writeFileSync(path.join(CLIENT, page), HTML + DRIVER);
  fs.writeFileSync(path.join(CLIENT, wrap), WRAPPER(page));
  const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });

  let rows = null;
  try {
    for (let attempt = 0; attempt < 3 && !rows; attempt++) {
      const dom = execFileSync(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", "--window-size=3400,1000", "--virtual-time-budget=60000",
        "--dump-dom", `http://localhost:${PORT}/${wrap}`],
        { encoding: "utf8", maxBuffer: 64e6, stdio: ["ignore", "pipe", "ignore"] });
      const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
      const raw = m ? m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                          .replace(/&lt;/g, "<").replace(/&gt;/g, ">")
                          .replace(/&#39;/g, "'") : "";
      if (raw && raw !== "PENDING") { try { rows = JSON.parse(raw); } catch (_) {} }
    }
  } finally {
    [page, wrap].forEach((f) => { try { fs.unlinkSync(path.join(CLIENT, f)); } catch (_) {} });
    server.kill();
  }

  console.log("\nmeasured in a real browser, at five widths");
  if (!rows) {
    console.log("  ✗ FAIL: the harness never reported"); fail++;
  } else {
    rows.forEach((r) => {
      const at = r.w + "px:";
      check(at + " the screen rendered", !r.err, r.err);
      if (r.err) return;
      check(at + " the note is up and readable", r.shown, r.tone);
      check(at + " …it is the calm one, not a fault", /is-info/.test(r.tone || ""), r.tone);
      check(at + " …with its icon", r.hasIcon);
      check(at + " …and the sentence it was given", /^No harm done/.test(r.said || ""), r.said);
      check(at + " it is a card, not text laid on the art", r.painted);
      check(at + " …in navy ink, not red", r.ink === "rgb(18, 58, 104)", r.ink);
      check(at + " it sits below the two painted buttons and touches neither",
            r.below && r.clearOfButtons);
      check(at + " …inside the artwork, not off the letterbox", r.insideStep);
      check(at + " …with every word of it visible", !r.clipped, "h=" + r.lines);
      check(at + " a click aimed at the artwork goes to the artwork", !r.eatsClicks);
      check(at + " nothing pushes the page sideways", !r.sideways);
      check(at + " the calm note takes itself away again", r.fadedLater === true);
      check(at + " the full-screen chip is nowhere before sign-in",
            r.chipBeforeSignIn === "none", r.chipBeforeSignIn);
      // A fixed box blockifies inline-flex, so "flex" is what this computes to.
      check(at + " …and is a corner chip once you are in",
            r.chipAfterSignIn === "flex" && r.chipW <= 70 && r.chipH >= 28 && r.chipH <= 46,
            `${r.chipAfterSignIn} ${r.chipW}x${r.chipH}`);
      check(at + " …in the bottom-left corner, out of the way of the top bar",
            r.chipLeft <= 20 && r.chipBottomGap <= 24 && r.chipTop > r.vh / 2,
            `l=${r.chipLeft} gapB=${r.chipBottomGap} top=${r.chipTop} vh=${r.vh}`);
    });
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
