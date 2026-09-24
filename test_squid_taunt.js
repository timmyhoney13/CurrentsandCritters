/* ══ The Giant Squid's parting shot, and what the invertebrates will say ══
 *
 * Beating the Giant Squid at the summit of Head to Head is the ONLY place
 * the game ever points a player at the hidden code. If that taunt does not
 * fire, four critters are tucked around the UI that nobody has been told to
 * look for, and the Spinner Dolphin is unreachable. So:
 *
 *  1. THE TAUNT SAYS THE THREE THINGS. The dolphin is not saved, the code is
 *     out there, and the invertebrates are the ones holding it. Any one of
 *     them missing and the hunt has no starting line.
 *
 *  2. IT FIRES ON THE RIGHT WIN, AND ONLY THEN. The summit, not a rank S+
 *     game; and never once the Spinner Dolphin is already saved.
 *
 *  3. THE BOX CAN BE SHUT, AND IT HANDS THE BOX BACK. It borrows the Giant
 *     Squid's challenge modal, so neither opener may leave the other wearing
 *     its buttons, its message or its speech bubble.
 *
 *  4. THE CRITTERS STILL SPELL THE CODE. Four of them, three lines each, the
 *     digits unchanged, the third click the one that reveals — and not one of
 *     them claiming an order the Avatar Gallery does not actually have.
 *
 * Run:  node test_squid_taunt.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const APP = fs.readFileSync(path.join(CLIENT, "js/preview-app.js"), "utf8");
const CSS = fs.readFileSync(path.join(CLIENT, "css/preview.css"), "utf8");
const HTML = fs.readFileSync(path.join(CLIENT, "preview.html"), "utf8");

let pass = 0, fail = 0;
function check(cond, name, extra) {
  if (cond) pass++;
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

// Lift a function out of the source rather than restating it: what is tested
// is what ships.
function grabFn(name) {
  const re = new RegExp("^  (?:async )?function " + name + "\\s*\\(", "m");
  const m = re.exec(APP);
  if (!m) throw new Error("missing function: " + name);
  const i = APP.indexOf("{", m.index + m[0].length - 1);
  let d = 0;
  for (let j = i; j < APP.length; j++) {
    if (APP[j] === "{") d++;
    else if (APP[j] === "}" && --d === 0) return APP.slice(m.index, j + 1);
  }
  throw new Error("unbalanced: " + name);
}
function grabConst(prefix) {
  const i = APP.indexOf(prefix);
  if (i < 0) throw new Error("missing: " + prefix);
  const end = APP.indexOf("\n  };", i);
  const endL = APP.indexOf(";\n", i);
  return APP.slice(i, (end > -1 && end < endL) ? end + 5 : endL + 1);
}

// ════════════════════════════════════════════════════════════════════════
//  1. WHAT HE SAYS
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe Giant Squid's parting shot");
{
  const TAUNT = grabConst("  const SQUID_TAUNT =");
  const said = new Function(TAUNT + " return SQUID_TAUNT;")();

  check(/spinner dolphin/i.test(said), "he names the Spinner Dolphin", said);
  check(/never/i.test(said), "…and says you will never save her");
  check(/code/i.test(said), "…that there is a code");
  check(/spread throughout the game/i.test(said), "…spread throughout the game");
  check(/invertebrate/i.test(said), "…and that the invertebrates are the ones who know it");
  check(said.length < 240, "…in one bubble's worth of words", String(said.length));
}

// ════════════════════════════════════════════════════════════════════════
//  2. WHEN IT FIRES
// ════════════════════════════════════════════════════════════════════════
console.log("\nit fires on the summit win, and stops once she is saved");
{
  // The real gate, run against a fake window and a fake clock.
  const sandbox = { console, Array, String, Number };
  const run = new Function("sandbox", `
    with (sandbox) {
      let shown = 0, timers = [];
      function setTimeout(fn, ms) { timers.push([fn, ms]); return timers.length; }
      function _showSquidTaunt() { shown++; }
      function bmSquidId() { return "giant_squid"; }
      ${grabFn("_spinnerDolphinSaved")}
      ${grabFn("_squidTauntAfterWin")}
      return {
        fire: (beaten, icons) => {
          shown = 0; timers = [];
          sandbox.window = { __fishGetUnlockedIcons: () => icons };
          _squidTauntAfterWin(beaten);
          timers.forEach(([fn]) => fn());
          return { shown, delay: timers.length ? timers[0][1] : 0 };
        },
      };
    }
  `)(sandbox);

  const LADDER = ["gilbert_carter", "jacques_cousteau"];
  const NOTHING = [];
  const SAVED = ["/avatars/spinner-dolphin.png"];

  check(run.fire(["giant_squid"], NOTHING).shown === 1,
        "beat the Squid and he surfaces");
  check(run.fire(LADDER.concat("giant_squid"), NOTHING).shown === 1,
        "…however many rungs that win also counted for");
  check(run.fire(LADDER, NOTHING).shown === 0,
        "a win over anybody else is silent: he is the one holding her");
  check(run.fire([], NOTHING).shown === 0, "…and so is a game that beat nobody");
  check(run.fire(["giant_squid"], SAVED).shown === 0,
        "once the Spinner Dolphin is saved he has nothing left to say");
  check(run.fire(["giant_squid"], ["/avatars/bottlenose-dolphin.png"]).shown === 1,
        "…and it is HER, not any dolphin, that quiets him");
  check(run.fire(null, NOTHING).shown === 0, "a missing list is not a win");
  check(run.fire("giant_squid", NOTHING).shown === 0, "…nor is a string that contains the id");

  const d = run.fire(["giant_squid"], NOTHING).delay;
  check(d >= 600 && d <= 4000, "he waits for the end screen before covering it", String(d));

  // An account that cannot be read must not swallow the one hint in the game.
  const throws = new Function("sandbox", `
    with (sandbox) {
      ${grabFn("_spinnerDolphinSaved")}
      sandbox.window = { __fishGetUnlockedIcons: () => { throw new Error("offline"); } };
      return _spinnerDolphinSaved();
    }
  `)({ console, Array, String });
  check(throws === false,
        "an unreadable collection counts as NOT saved, so the taunt still fires");
}

// ════════════════════════════════════════════════════════════════════════
//  3. THE BOX THE TWO OF THEM SHARE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe taunt borrows the challenge modal and hands it back");
{
  const show = grabFn("_showSquidTaunt");
  check(/gs-challenge-msg/.test(show), "it writes the line into the squid's own message slot");
  check(/fight[\s\S]*display = "none"/.test(show), "…hides the Challenge button: there is nothing to fight");
  check(/classList\.add\("taunt"\)/.test(show), "…marks the box as a speech bubble");
  check(/addEventListener\("click", close\)/.test(show), "…and wires its own way out");
  check(/remove\("open", "taunt"\)/.test(show), "…which clears both flags");

  // The challenge modal must scrub what the taunt left, or the Squid asks for
  // a fight through a speech bubble with a "Find them" button under it.
  const ch = APP.slice(APP.indexOf("function _showGiantSquidChallenge()"),
                       APP.indexOf("async function saveAvatarSelection()"));
  check(/classList\.remove\("taunt"\)/.test(ch), "the challenge modal takes the bubble back off");
  check(/laterBtn0[\s\S]*"Not now"/.test(ch), "…and puts its own button label back");
  check(/fightBtn\.style\.display = ""/.test(ch), "…and shows the Challenge button again");

  // The markup and the styling the taunt leans on have to actually be there.
  ["giant-squid-modal", "gs-challenge-msg", "gs-challenge-title",
   "gs-challenge-fight-btn", "gs-challenge-later-btn"].forEach(id => {
    check(HTML.indexOf('id="' + id + '"') > -1, "preview.html carries #" + id);
  });
  check(/#giant-squid-modal\.taunt #gs-challenge-msg\s*\{/.test(CSS),
        "the bubble has a style");
  check(/#giant-squid-modal\.taunt #gs-challenge-msg::after/.test(CSS),
        "…and a tail, so it reads as something he is saying");

  // It is reachable from the end-of-game save, which is the only caller.
  check(/_squidTauntAfterWin\(_botsBeatenNow\)/.test(APP),
        "the end-of-game save is what calls it, with the rungs that win beat");
}

// ════════════════════════════════════════════════════════════════════════
//  4. THE FOUR WHO ARE HOLDING THE CODE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe invertebrates, and what they will and will not say");
{
  const SRC = grabConst("  const _SECRET_CRITTERS = {");
  const CRIT = new Function(SRC + " return _SECRET_CRITTERS;")();
  const keys = Object.keys(CRIT);

  check(keys.length === 4, "four of them", keys.join(","));
  check(keys.map(k => CRIT[k].number).sort().join("") === "1379",
        "…holding the digits 1, 3, 7 and 9 between them",
        keys.map(k => CRIT[k].number).join(","));

  // The accepted code is still spelled by these four and nothing else.
  const CODE = (APP.match(/rawCode === "(\d{4})"/) || [])[1];
  check(!!CODE, "the code is still checked somewhere", String(CODE));
  check(CODE.split("").sort().join("") === "1379",
        "…and it is made of exactly these four digits", String(CODE));

  keys.forEach(k => {
    const c = CRIT[k];
    check(Array.isArray(c.lines) && c.lines.length === 3,
          k + ": three lines, one per click", String(c.lines && c.lines.length));
    c.lines.forEach((l, i) => {
      check(typeof l === "string" && l.trim().length > 0, k + ": line " + (i + 1) + " is real");
      check(l.length <= 90, k + ": line " + (i + 1) + " fits the bubble (" + l.length + ")", l);
    });
    check(/^[^a-z]/.test(c.lines[0].trim()[0]) || /^[A-Z“"]/.test(c.lines[0].trim()),
          k + ": opens on a capital");
    check(c.img.indexOf("/avatars/") === 0, k + ": still wears its own art", c.img);
  });

  const all = keys.map(k => CRIT[k].lines.join(" ")).join(" ");
  const firsts = keys.map(k => CRIT[k].lines[0]).join(" ");

  // The scene: every one of them refuses first, and the refusal is about the
  // code, not about being touched.
  check(/won.t tell you the code|not telling you|Don.t touch me|Go away/i.test(firsts),
        "every first click is a refusal", firsts);
  check(/dolphin/i.test(all), "somebody says who this is all about");
  check(/code/i.test(all), "…and that there is a code at all");
  check(/forever|his now|so long/i.test(all), "…and what he has done with her");
  check(/said too much|don.t say it was me|gotta go/i.test(all),
        "…and at least one of them runs for it");

  // The Avatar Gallery sorts itself per player, so no critter may send anyone
  // to "gallery order": it is not a fixed order and the hunt would stall.
  check(!/gallery/i.test(all), "none of them claims an order the gallery does not have", all);

  // The lines the rewrite replaced are gone, so no half-old scene ships.
  ["Stop touching me", "I gotta stay hidden", "I won’t speak to you",
   "He entrusted this to me", "I can hide in plain sight",
   "I don’t know where my friends are", "Put the digits together and save us",
  ].forEach(old => {
    check(APP.indexOf(old) === -1, "the old line is gone: " + JSON.stringify(old));
  });

  // The third click is still the one that pays out, and the reveal still
  // reads the digit off this table.
  const wire = grabFn("_wireSecretCritter");
  check(/st\.stage > 3/.test(wire), "a fourth click does nothing");
  check(/cfg\.lines\[st\.stage - 1\]/.test(wire), "…each click speaks its own line");
  check(/st\.stage === 3/.test(wire), "…and the third is the one that reveals");
  check(/cfg\.number/.test(wire), "…printing the digit this critter is holding");
}

// ════════════════════════════════════════════════════════════════════════
//  5. THE REAL RENDER: he actually pops up, and he can actually be shut
// ════════════════════════════════════════════════════════════════════════
const os = require("os");
const { execFileSync } = require("child_process");
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

function page() {
  const a = HTML.indexOf('<div id="giant-squid-modal">');
  const b = HTML.indexOf("</div>\n</div>", a) + "</div>\n</div>".length;
  const modal = HTML.slice(a, b);
  // _showGiantSquidChallenge lives one scope deeper than the taunt, so it is
  // sliced by hand rather than by grabFn. Its $a is getElementById.
  const chStart = APP.indexOf("    function _showGiantSquidChallenge() {");
  const chEnd = APP.indexOf("\n    }\n", chStart) + "\n    }\n".length;
  const challenge = APP.slice(chStart, chEnd);
  return `<!doctype html><html><head><meta charset="utf-8">
<style>${CSS}</style></head><body>
${modal}
<div id="out" style="display:none"></div>
<script>
function $a(id) { return document.getElementById(id); }
var _activeProfile = { stats: { rank_competitive: "" } };
function rankTierValue() { return 0; }
window.__fishStartGiantSquidChallenge = function () { return Promise.resolve(); };
${grabConst("  const SQUID_TAUNT =")}
let _squidTauntWired = false;
${grabFn("_showSquidTaunt")}
let _gsChallengeWired = false;
${challenge}
const out = [];
const ok = (c, n, x) => out.push((c ? "PASS " : "FAIL ") + n + (x ? "  \u2192 " + x : ""));
const modalEl = $a("giant-squid-modal");
const msg = $a("gs-challenge-msg");
const fight = $a("gs-challenge-fight-btn");
const later = $a("gs-challenge-later-btn");
const cs = (el, pe) => getComputedStyle(el, pe || null);

// ── he is not on screen until he is called ──
ok(cs(modalEl).display === "none", "the box is shut before anything happens", cs(modalEl).display);

// ── the parting shot ──
_showSquidTaunt();
ok(cs(modalEl).display === "flex", "beat him and the box opens", cs(modalEl).display);
ok(modalEl.classList.contains("taunt"), "…in taunt mode");
ok(msg.textContent === SQUID_TAUNT, "…carrying exactly what he says", msg.textContent);
ok(cs(fight).display === "none", "…with no Challenge button: there is nothing to fight");
ok(later.textContent === "Find them", "…and a way onward", later.textContent);
ok(cs($a("gs-challenge-img")).display !== "none", "…and his art is on it");

// It has to LOOK like speech: a filled bubble with a tail pointing up at him.
const bub = cs(msg);
ok(bub.borderTopWidth !== "0px" && bub.borderRadius !== "0px", "the line is drawn as a bubble",
   bub.borderTopWidth + " / " + bub.borderRadius);
ok(/rgba?\\(/.test(bub.backgroundColor) && bub.backgroundColor !== "rgba(0, 0, 0, 0)",
   "…with a fill of its own", bub.backgroundColor);
const tail = cs(msg, "::after");
ok(tail.content !== "none", "…and a tail", tail.content);
const squidBox = $a("gs-challenge-img").getBoundingClientRect();
const msgBox = msg.getBoundingClientRect();
ok(msgBox.top > squidBox.top, "…that hangs under the Squid, not over him",
   Math.round(squidBox.top) + " / " + Math.round(msgBox.top));

// ── it fits a phone ──
ok(document.documentElement.scrollWidth <= window.innerWidth + 1,
   "no sideways scroll at " + window.innerWidth + "px",
   document.documentElement.scrollWidth + " > " + window.innerWidth);
const box = $a("gs-challenge-box").getBoundingClientRect();
ok(box.left >= -1 && box.right <= window.innerWidth + 1, "the box is on screen",
   Math.round(box.left) + ".." + Math.round(box.right));
ok(msg.scrollHeight <= msg.clientHeight + 2, "…and the whole line fits in the bubble",
   msg.scrollHeight + " > " + msg.clientHeight);
const lb = later.getBoundingClientRect();
ok(lb.width >= 30 && lb.height >= 26, "…and the button is tappable",
   Math.round(lb.width) + "x" + Math.round(lb.height));

// ── it can be shut ──
later.click();
ok(cs(modalEl).display === "none", "Find them shuts it", cs(modalEl).display);
ok(!modalEl.classList.contains("taunt"), "…and takes the bubble off with it");
_showSquidTaunt();
modalEl.dispatchEvent(new MouseEvent("click", { bubbles: true }));
ok(cs(modalEl).display === "none", "…and so does clicking the backdrop");

// ── and the Squid's own challenge still looks like a challenge afterwards ──
_showGiantSquidChallenge();
ok(cs(modalEl).display === "flex", "the challenge box still opens after a taunt");
ok(!modalEl.classList.contains("taunt"), "…with the speech bubble taken back off");
ok(later.textContent === "Not now", "…its own button label back", later.textContent);
ok(cs(fight).display !== "none", "…and the Challenge button back");
ok(/beat me in a fight/.test(msg.textContent), "…and its own line back", msg.textContent);
ok(cs(msg).backgroundColor === "rgba(0, 0, 0, 0)", "…drawn as a caption again, not speech",
   cs(msg).backgroundColor);

$a("out").textContent = out.join("\\n");
</scr` + `ipt></body></html>`;
}

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: skipping the render half.");
} else {
  console.log("\nhe really pops up, phone through desktop");
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-squidtaunt-"));
  const file = path.join(tmp, "taunt.html");
  fs.writeFileSync(file, page());
  [360, 430, 820, 1280].forEach(w => {
    let dom = "";
    try {
      dom = execFileSync(CHROME, [
        "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--window-size=" + w + ",900", "--virtual-time-budget=8000",
        "--dump-dom", "file://" + file,
      ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 120000,
           maxBuffer: 64 * 1024 * 1024 });
    } catch (e) {
      check(false, w + "px: Chrome ran", e.message);
      return;
    }
    const m = dom.match(/<div id="out"[^>]*>([\s\S]*?)<\/div>/);
    if (!m || !m[1].trim()) { check(false, w + "px: measurements came back"); return; }
    m[1].split("\n").filter(Boolean).forEach(line =>
      check(line.startsWith("PASS "), w + "px: " + line.replace(/^(PASS|FAIL) /, "")));
  });
  fs.rmSync(tmp, { recursive: true, force: true });
}

if (fail) {
  console.log(`\nFAILED ${fail} of ${pass + fail} checks.`);
  process.exit(1);
}
console.log(`\nAll ${pass} checks passed.`);
