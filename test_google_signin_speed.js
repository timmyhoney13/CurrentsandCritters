#!/usr/bin/env node
/* Continue with Google, and how long the player waits after it.
 *
 * The report: "when I sign in with Google it takes a long second to load".
 *
 * On a computer the bare "/" is the LAUNCHER. Signing in there used to send
 * the tab to "/?game_window=1&auth=google", which loaded the entire app a
 * second time (two megabytes of script parsed again, Firebase restoring the
 * session again, the Firestore connection opened again) before the profile
 * read could even start. The launcher and the game window are the same page,
 * told apart only by IS_GAME_WINDOW, which only the auth flow reads, so the
 * page now turns into the game window where it stands.
 *
 * And Google's own screens: every sign-in asked for "select_account consent",
 * so Google showed its "wants to access your Google Account" page every time,
 * not just the first. The picker alone is enough.
 *
 * Driven in headless Chrome against the real markup, CSS and preview-app.js,
 * with the Firebase stub from test_guest_session_isolation.js.
 *
 * Run:  node test_google_signin_speed.js        (needs Google Chrome / Chromium)
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");
const HTML   = read("preview.html");
const APP    = read("js/preview-app.js");

const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (detail != null ? "  [" + String(detail).slice(0, 300) + "]" : "")); }
}

// ════════════════════════════════════════════════════════════════════════
//  SOURCE
// ════════════════════════════════════════════════════════════════════════
console.log("\nGoogle asks as little as it can");
{
  check("no sign-in forces Google's consent screen", !/prompt:\s*"[^"]*consent/.test(APP));
  check("…every Google provider uses the one account-picker prompt",
        (APP.match(/setCustomParameters\(CC_GOOGLE_PROMPT\)/g) || []).length === 4
        && /const CC_GOOGLE_PROMPT = \{ prompt: "select_account" \};/.test(APP));
}

console.log("\nan account signing in on the launcher does not reload the app");
{
  check("the launcher becomes the game window in place",
        /if \(user\) \{[\s\S]{0,600}?ccBecomeGameWindow\(\);\s*\} else \{/.test(APP));
  check("…and no longer navigates to a Google game-window URL",
        !/ccGameUrl\(\{ auth: "google" \}\)/.test(APP));
  check("…and the URL follows, so a refresh comes back as the game window",
        /function ccBecomeGameWindow\(\) \{[\s\S]{0,300}?searchParams\.set\("game_window", "1"\)[\s\S]{0,120}?history\.replaceState/.test(APP));
  check("IS_GAME_WINDOW honours the switch",
        /const IS_GAME_WINDOW = \(\) => _ccUrlIsGameWindow \|\| _ccBecameGameWindow \|\|/.test(APP));
}

// ════════════════════════════════════════════════════════════════════════
//  REAL BROWSER
// ════════════════════════════════════════════════════════════════════════
const GUEST_TEST = fs.readFileSync(path.join(ROOT, "test_guest_session_isolation.js"), "utf8");
const stubMatch = /\nconst STUB = `([\s\S]*?)`;\n/.exec(GUEST_TEST);

const SEED = `
<script>
window.__STUB_DOCS = {
  "users/G-UID-7": {
    nickname: "TideRunner", nickname_lower: "tiderunner", friend_code: "4242",
    email: "tide@example.com", avatar_url: "/avatars/great-white-shark.png",
    unlocked_icons: ["/avatars/great-white-shark.png"],
    stats: { completed_games: 12, hours_played: 4, total_xp: 3000, critter_coins: 50 }
  }
};
// Counts how many times this tab loaded the app. sessionStorage outlives a
// navigation, so a reload into the game window would read 2.
(function () {
  var n = Number(sessionStorage.getItem("__loads") || 0) + 1;
  sessionStorage.setItem("__loads", String(n));
})();
</script>
<script>${stubMatch ? stubMatch[1] : ""}</script>`;

const DRIVER = `
<script>
(function () {
  var out = { errors: [], phase: "boot", loads: 0 };
  window.addEventListener("error", function (e) { out.errors.push("onerror: " + (e && e.message)); });
  window.addEventListener("unhandledrejection", function (e) {
    var r = e && e.reason; out.errors.push("unhandled: " + ((r && r.message) || String(r)));
  });
  function q(s) { return document.querySelector(s); }
  function vis(el) { return !!(el && el.offsetParent !== null); }
  function click(el) { el && el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window })); }
  function finish() {
    out.loads = Number(sessionStorage.getItem("__loads") || 0);
    var d = document.getElementById("out");
    if (!d) { d = document.createElement("div"); d.id = "out"; document.body.appendChild(d); }
    d.textContent = JSON.stringify(out);
  }
  // A marker that only survives if this document is never replaced.
  window.__samePage = "yes";
  var USER = { uid: "G-UID-7", email: "tide@example.com", displayName: "Tide Runner",
               providerData: [{ providerId: "google.com" }], getIdToken: function () { return Promise.resolve("t"); } };
  var phase = 1, guard = 0, t0 = 0;
  var iv = setInterval(function () {
    out.phase = "p" + phase + ":g" + guard;
    try {
      if (phase === 1) {
        var g = q("#auth-choose-google-btn");
        if (vis(g) && ++guard > 10) {
          out.urlBefore = location.search;
          out.launcher = !/game_window=1/.test(location.search);
          // The Google window "closes" with an account, as signInWithPopup does.
          var auth = window.firebase.auth();
          auth.signInWithPopup = function () {
            window.__stubSignIn(USER);
            return Promise.resolve({ user: USER });
          };
          t0 = Date.now();
          click(g); phase = 2; guard = 0;
        } else if (guard === 0 && ++out.waitChooser > 600) {
          out.errors.push("the chooser never appeared on the launcher"); finish(); clearInterval(iv);
        }
        return;
      }
      if (phase === 2) {
        var lobby = q("#auth-stats-lobby");
        if (lobby && lobby.classList.contains("visible")) {
          out.lobbyAfterMs = Date.now() - t0;
          out.samePage = window.__samePage;
          out.urlAfter = location.search;
          out.nick = (document.getElementById("stats-lobby-nick") || {}).textContent || "";
          out.authScreenHidden = q("#auth-screen").classList.contains("hidden");
          out.phase = "done"; finish(); clearInterval(iv); return;
        }
        if (++guard > 900) { out.errors.push("the account's Player Home never opened"); finish(); clearInterval(iv); }
      }
    } catch (e) { out.errors.push("driver: " + (e && e.message)); finish(); clearInterval(iv); }
  }, 50);
  out.waitChooser = 0;
})();
</script>`;

console.log("\nContinue with Google on the launcher (real browser)");
if (!stubMatch) {
  check("the Firebase stub could be borrowed from test_guest_session_isolation.js", false);
} else if (!CHROME) {
  check("Chrome is available to drive the page", false, "no Chrome found");
} else {
  const PORT = 9360 + (process.pid % 300);
  const SERVER_SRC = `
    const fs=require("fs"),path=require("path"),http=require("http");
    const ROOT=${JSON.stringify(CLIENT)};
    const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",".json":"application/json",
      ".png":"image/png",".jpg":"image/jpeg",".webp":"image/webp",".svg":"image/svg+xml",".ico":"image/x-icon"};
    http.createServer((req,res)=>{
      const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
      const f=path.join(ROOT,rel);
      if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);res.end();return;}
      res.writeHead(200,{"Content-Type":MIME[path.extname(f)]||"application/octet-stream"});
      fs.createReadStream(f).pipe(res);
    }).listen(${PORT});
  `;
  const page = HTML
    .replace(/<script[^>]*src="https:\/\/www\.gstatic\.com\/firebasejs[^"]*"[^>]*><\/script>/g, "")
    .replace(/<script[^>]*src="\/firebase-config\.js"[^>]*><\/script>/g, "")
    .replace("</head>", SEED + "</head>") + DRIVER;
  const pageFile = path.join(CLIENT, "_google_signin_drive.html");
  fs.writeFileSync(pageFile, page);
  const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });
  try { execFileSync(process.execPath, ["-e", "setTimeout(()=>{},700)"]); } catch (_) {}

  let D = null;
  try {
    for (let attempt = 0; attempt < 2 && !D; attempt++) {
      const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--window-size=1440,900", "--virtual-time-budget=120000", "--dump-dom",
        `http://localhost:${PORT}/_google_signin_drive.html`],
        { encoding: "utf8", maxBuffer: 64e6, stdio: ["ignore", "pipe", "ignore"] });
      const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
      if (!m) continue;
      const raw = m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                      .replace(/&lt;/g, "<").replace(/&gt;/g, ">");
      try { D = JSON.parse(raw); } catch (_) {}
    }
  } finally {
    try { server.kill(); } catch (_) {}
    try { fs.unlinkSync(pageFile); } catch (_) {}
  }

  if (!D) {
    check("the walkthrough produced a result", false, "no result");
  } else {
    check("the walkthrough finished", D.phase === "done", D.phase + " " + JSON.stringify(D.errors));
    check("it started on the launcher, not the game window", D.launcher === true, D.urlBefore);
    check("the account's Player Home opened", D.phase === "done" && D.authScreenHidden === true);
    check("…with that account's name on it", /TideRunner/.test(D.nick || ""), D.nick);
    check("the app loaded ONCE: no second page load after Google", D.loads === 1, "loads=" + D.loads);
    check("…the very same document carried on", D.samePage === "yes", D.samePage);
    check("the address now says game window, so a refresh stays in the game",
          /game_window=1/.test(D.urlAfter || ""), D.urlAfter);
    check("nothing threw on the way in", !D.errors.length, JSON.stringify(D.errors));
  }
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
