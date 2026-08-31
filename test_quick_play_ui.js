#!/usr/bin/env node
/* Quick Play's searching bar: the queue counts, and the countdown to bots.
 *
 * The bar is the only thing a searching player can see, and until now the only
 * thing it could ever say was "waiting", forever. It now carries three facts,
 * and each one is here because getting it wrong puts a WRONG number in front of
 * a player rather than merely looking untidy:
 *
 *  1. THE COUNTS ARE OPTIONAL. `online` comes back as null whenever Firestore
 *     is unreachable. Rendering that as "0 players online" would tell everyone
 *     the game is dead every time an unrelated service blips, so an unknown
 *     count is left OFF the bar. Same for a queue of zero.
 *
 *  2. ONE WRITER FOR THE STATUS LINE. Two timers are live while searching: a 1s
 *     tick that owns the countdown and a 2.5s room poll that owns "waiting".
 *     When they both wrote the line directly, the poll wiped the countdown 2.5
 *     seconds out of every 3. Both now go through _qmWaitingStatus.
 *
 *  3. THE HANDOFF CAN BE REFUSED. If somebody claims the last seat while the
 *     give-up request is in flight, the server answers matched:true and keeps
 *     the humans. The client has to READ that and go back to opening the lobby,
 *     otherwise the player is told a bot game is starting and then dumped into
 *     a human lobby with no explanation.
 *
 * The server half (the queue count, the seat conversion, the race) is covered
 * by test_quick_play_fallback.py. This file covers the client's side of that
 * contract and the bar's markup and styling in a real browser.
 *
 * Run:  node test_quick_play_ui.js      (browser section needs Chrome/Chromium)
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (detail != null ? "  [" + String(detail).slice(0, 220) + "]" : "")); }
}

const APP    = read("js/preview-app.js");
const HTML   = read("preview.html");
const CSS    = read("css/preview.css");
const SERVER = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");

// ════════════════════════════════════════════════════════════════════════
//  THE BAR REPORTS THE QUEUE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe searching bar reports the queue and who is online");
{
  check("the bar has a span for the counts",
        /id="qm-search-counts"/.test(HTML));
  check("…inside the bar's text, next to the seconds counter",
        /id="qm-search-counts"[\s\S]{0,120}id="qm-search-secs"/.test(HTML));
  check("the client asks the server for them",
        /apiFetch\(\s*"\/api\/quickplay\/status"/.test(APP));
  check("the server serves that path",
        /parsed\.path == "\/api\/quickplay\/status"/.test(SERVER));
  check("the server answers with queued + online + the window",
        /"queued": queued/.test(SERVER) && /"online": online/.test(SERVER)
        && /"bot_fallback_seconds": QUICK_PLAY_BOT_FALLBACK_SECONDS/.test(SERVER));

  // An unknown online count must never be printed as a confident zero.
  check("the server sends null, not 0, when it cannot count who is online",
        /"online": online if isinstance\(online, int\) and online >= 0 else None/.test(SERVER));
  const render = APP.slice(APP.indexOf("function _qmRenderCounts"),
                           APP.indexOf("async function _qmFetchCounts"));
  check("…and the bar only prints a count it actually has",
        /Number\.isFinite\(search\.online\)\s*&&\s*search\.online\s*>\s*0/.test(render), render.slice(0, 0));
  check("…same for the queue", /search\.queued\s*>\s*0/.test(render));
  check("a queue of one is described as just you, not as an opponent",
        /just you in the queue/.test(render));
  check("the counts poll is lazier than the 2.5s room poll",
        /_qmFetchCounts\(search\), 5000\)/.test(APP));
  check("…and it is cleared when the search ends",
        /if \(search\.countsId\) clearInterval\(search\.countsId\);/.test(APP));
}

// ════════════════════════════════════════════════════════════════════════
//  THE SEARCH ENDS IN A GAME
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe search always ends in a game");
{
  check("the client hands the room to bots at the server's deadline",
        /\/api\/rooms\/\$\{search\.roomId\}\/quickplay_bots/.test(APP));
  check("the server has that route",
        /parts\[3\] == "quickplay_bots"/.test(SERVER));
  check("the deadline comes from the server, not a second hardcoded number",
        /res\.data\.bot_fallback_seconds/.test(APP)
        && /QUICK_PLAY_BOT_FALLBACK_SECONDS = \d+/.test(SERVER));
  check("the player is warned before it happens",
        /Starting a bot match in \$\{remaining\}s…/.test(APP));
  check("…and told why afterwards",
        /No one else was searching, so bots filled the table\./.test(APP));

  const fallback = APP.slice(APP.indexOf("async function _qmFallbackToBots"),
                             APP.indexOf("async function _qmPollRoom"));
  check("a late joiner cancels the bot plan instead of being botted",
        /res\.data\?\.matched/.test(fallback) && /botsRequested = false/.test(fallback));
  check("…and the player is told the lobby is opening instead",
        /Someone joined! Opening the Quick Play lobby…/.test(fallback));
  check("a refused handoff backs off instead of retrying every second",
        /botsRetryAt = Date\.now\(\) \+ \d+/.test(fallback));
  check("…and the tick honours that backoff",
        /if \(search\.botsRetryAt && Date\.now\(\) < search\.botsRetryAt\) return;/.test(APP));
  check("it never fires twice for one search",
        /if \(search\.botsRequested\) return;/.test(fallback)
        || /search\.botsRequested \|\|/.test(APP));
  check("it only fires for the host of an unmatched room",
        /!search\.roomId \|\| !search\.hostToken/.test(APP));
}

// ════════════════════════════════════════════════════════════════════════
//  ONE WRITER FOR THE STATUS LINE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe countdown is not wiped by the room poll");
{
  // The bug this guards: the 1s tick wrote the countdown, then the 2.5s poll
  // wrote "Waiting for another Quick Play player…" straight over it.
  const writers = (APP.match(/_qmSetStatus\("Waiting for another Quick Play player…"\)/g) || []).length;
  check("only one place writes the waiting line", writers === 1, `${writers} writers`);
  const waiting = APP.slice(APP.indexOf("function _qmWaitingStatus"),
                            APP.indexOf("function _qmShowBar"));
  check("…and it is _qmWaitingStatus",
        /_qmSetStatus\("Waiting for another Quick Play player…"\)/.test(waiting));
  check("the room poll goes through it",
        /_qmWaitingStatus\(search, false\)/.test(APP));
  check("the 1s tick only ever writes the countdown",
        /_qmWaitingStatus\(search, true\)/.test(APP)
        && /if \(countdownOnly\) return;/.test(waiting));
  check("the countdown never goes negative", /Math\.max\(0, _qmBotFallbackSecs/.test(waiting));
}

// ════════════════════════════════════════════════════════════════════════
//  IN A REAL BROWSER
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe bar in a real browser");
if (!CHROME) {
  console.log("  – skipped, no Chrome/Chromium found");
} else {
  const page = `<!doctype html><meta charset="utf-8">
<style>${CSS}</style>
${HTML.slice(HTML.indexOf('<div class="qm-search-bar"'),
             HTML.indexOf("</div>", HTML.indexOf('id="qm-cancel-btn"')) + 6)}
<script>
  const bar = document.getElementById("qm-search-bar");
  bar.style.display = "";
  const counts = document.getElementById("qm-search-counts");
  const out = {};
  out.hasCounts = !!counts;
  out.inBar     = !!(counts && bar.contains(counts));
  // Empty span must not open a gap in the bar.
  counts.textContent = "";
  out.emptyWidth = counts.getBoundingClientRect().width;
  out.emptyMargin = getComputedStyle(counts).marginLeft;
  counts.textContent = "· 3 in the queue · 12 players online ·";
  out.filledWidth = counts.getBoundingClientRect().width;
  out.filledMargin = getComputedStyle(counts).marginLeft;
  // The bar stays one row: the counts must not push Cancel off the edge.
  const cancel = document.getElementById("qm-cancel-btn");
  out.cancelVisible = cancel.getBoundingClientRect().right <= bar.getBoundingClientRect().right + 1;
  out.calm = getComputedStyle(bar).backgroundImage;
  bar.classList.add("qm-bots-soon");
  out.soon = getComputedStyle(bar).backgroundImage;
  out.soonText = getComputedStyle(document.getElementById("qm-search-status")).color;
  document.title = JSON.stringify(out);
</script>`;
  const tmp = path.join(require("os").tmpdir(), `qm-bar-${process.pid}.html`);
  fs.writeFileSync(tmp, page);
  let data = null;
  try {
    const dump = execFileSync(CHROME, [
      "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      "--virtual-time-budget=1500", "--window-size=900,700",
      "--dump-dom", "file://" + tmp,
    ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 60000 });
    const m = dump.match(/<title>([\s\S]*?)<\/title>/);
    if (m) data = JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
  } catch (e) { /* reported as a failure below */ }

  if (!data) { console.log("  ✗ FAIL: the browser never reported"); fail++; }
  else {
    check("the counts span is really in the bar", data.hasCounts && data.inBar);
    check("an empty counts span leaves no gap",
          data.emptyWidth < 1 && data.emptyMargin === "0px",
          `${data.emptyWidth}px / ${data.emptyMargin}`);
    check("…and a filled one is spaced from the status text",
          data.filledWidth > 40 && data.filledMargin !== "0px",
          `${data.filledWidth}px / ${data.filledMargin}`);
    check("Cancel stays inside the bar with the counts shown", data.cancelVisible);
    check("the bar changes colour for the bot countdown",
          data.calm && data.soon && data.calm !== data.soon,
          `${data.calm} → ${data.soon}`);
    check("…and its text stays readable against it",
          /^rgb\(/.test(data.soonText || ""), data.soonText);
  }
  try { fs.unlinkSync(tmp); } catch (_) {}
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
