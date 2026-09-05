/* Clan Season Grand Prize banner: module test (node vm, no browser).
 *
 * Loads the real js/clan-prize.js and checks the things that would quietly
 * misinform players rather than crash:
 *
 *   • THE ANTI-DRIFT CHECK. The banner's fallback amount/wording/terms must
 *     still equal SEASON_GRAND_PRIZE_* in clan_server.py. The banner keeps a
 *     fallback so the marketing site paints before the free-tier server wakes;
 *     this test is the reason that fallback is safe to have. The clan coin
 *     rewards are the cautionary tale: the podium advertised 400/300/200 for
 *     months while the server paid 150/100/50.
 *   • the server's figure WINS over the fallback whenever there is one
 *   • a clan on zero points is never announced as the leader
 *   • the runner-up gap is right, and absent when there is no runner-up
 *   • the countdown reads ends_ts as SECONDS (an ms reading is 1000x too long)
 *   • the claim terms are on the banner in every state (a real-world prize with
 *     no stated way to collect it is the thing people ask about instead of
 *     playing)
 *   • nothing renders "undefined" / "NaN" / "[object Object]"
 *   • clan/player names are escaped, they are player-supplied text
 *   • the whole module is optional: clans-ui.js still renders without it
 *
 * Run:  node test_clan_prize.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = __dirname;
const SRC = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/clan-prize.js"), "utf8");
const PY = fs.readFileSync(path.join(ROOT, "clan_server.py"), "utf8");

let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name); }
}

// ── Load the module in a bare sandbox ───────────────────────────────────────
// document is stubbed down to what the module touches at load: it looks for its
// host element, finds nothing, and registers its seams anyway.
function load() {
  const listeners = [];
  const documentStub = {
    readyState: "complete",
    getElementById: () => null,
    querySelector: () => null,
    addEventListener: (t, f) => listeners.push({ t, f }),
    createElement: () => {
      const n = { className: "", innerHTML: "", style: {},
                  appendChild() {}, setAttribute() {},
                  classList: { add() {}, remove() {}, toggle() {} } };
      return n;
    },
    body: { contains: () => false },
    hidden: false,
  };
  const windowStub = {};
  const sandbox = {
    window: windowStub, document: documentStub, console,
    setInterval: () => 0, clearInterval: () => {},
    setTimeout: () => 0, clearTimeout: () => {},
    fetch: () => Promise.reject(new Error("offline")),
    AbortController: function () { this.signal = {}; this.abort = () => {}; },
    Date, Math, JSON, Number, String, Array, Object, Promise,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: "clan-prize.js" });
  return windowStub;
}

const W = load();
const M = W.__ccClanPrize;

const NOW = Math.floor(Date.now() / 1000);
const SEASON = {
  id: "2026-Q3", number: 1, name: "Riptide",
  starts_ts: NOW - 86400 * 30, ends_ts: NOW + 86400 * 44, now: NOW,
  reward_coins: [400, 300, 200], reward_min_points: 10,
  mvp_coins: 50, mvp_min_points: 25, border_top_n: 10, extra_days: 30,
  grand_prize_usd: 100,
  grand_prize_what: "a board game of their choice, shipped to them",
  grand_prize_claim: "The winning clan's owner is contacted after the season is " +
    "finalized, picks the game with their clan, and gives one shipping address. " +
    "Claim within 30 days. One prize per clan, per season, shipped to one address.",
};
const ROWS = [
  { id: "a", name: "Reef Raiders", icon: "/avatars/clownfish.png", points: 1240, rank: 1 },
  { id: "b", name: "Kelp Kings",   icon: "/avatars/otter.png",     points: 1060, rank: 2 },
  { id: "c", name: "Tide Turners", icon: "/avatars/crab.png",      points: 402,  rank: 3 },
];

console.log("module seams:");
check("the module registers window.__ccClanPrize", !!M);
check("...with html() and node() for other modules to render through",
      M && typeof M.html === "function" && typeof M.node === "function");
check("...and a render hook Player Home can re-run after auth",
      typeof W.__ccClanPrizeRender === "function");

// ── The anti-drift check ────────────────────────────────────────────────────
console.log("\nthe banner cannot advertise a prize the server does not name:");
// Reads a constant out of clan_server.py. Handles the two shapes these three
// use: a bare number, and a parenthesised run of adjacent string literals
// (Python's implicit concatenation), which is how the claim terms are wrapped.
// Rebuilding the real string is what makes this an EXACT comparison rather than
// a "looks about right" one, which is the only kind worth having here.
function pyConst(name) {
  const at = PY.indexOf("\n" + name);
  if (at === -1) return null;
  const eq = PY.indexOf("=", at);
  const rest = PY.slice(eq + 1);
  if (rest.trimStart()[0] === "(") {
    const body = rest.slice(rest.indexOf("(") + 1, rest.indexOf(")"));
    return (body.match(/"([^"]*)"/g) || []).map(q => q.slice(1, -1)).join("");
  }
  const line = rest.slice(0, rest.indexOf("\n"));
  const one = line.match(/"([^"]*)"/);
  return one ? one[1] : line.split("#")[0].trim();
}
check("clan_server.py still defines SEASON_GRAND_PRIZE_USD", pyConst("SEASON_GRAND_PRIZE_USD") !== null);
check("...and the banner's fallback amount equals it",
      String(M.PRIZE_USD_FALLBACK) === String(pyConst("SEASON_GRAND_PRIZE_USD")));
check("...and the fallback wording equals SEASON_GRAND_PRIZE_WHAT",
      M.PRIZE_WHAT_FALLBACK === pyConst("SEASON_GRAND_PRIZE_WHAT"));
check("...and the fallback claim terms are the server's, word for word",
      M.PRIZE_CLAIM_FALLBACK === pyConst("SEASON_GRAND_PRIZE_CLAIM"));
check("the server's season payload carries the prize",
      /"grand_prize_usd":\s*SEASON_GRAND_PRIZE_USD/.test(PY));
check("the published clan rules quote it too",
      /SEASON_GRAND_PRIZE_USD\} towards/.test(PY));

// ── What it actually paints ─────────────────────────────────────────────────
console.log("\nthe banner reads the server, not itself:");
const live = M.html(SEASON, ROWS, { cta: "play" });
check("the amount comes from the payload", live.indexOf("$100") !== -1);
const raised = M.html(Object.assign({}, SEASON, { grand_prize_usd: 250 }), ROWS, {});
check("...so raising it on the server raises it on the banner",
      raised.indexOf("$250") !== -1 && raised.indexOf("$100") === -1);
check("...and a four-figure prize is written with a separator",
      M.html(Object.assign({}, SEASON, { grand_prize_usd: 1250 }), ROWS, {}).indexOf("$1,250") !== -1);
check("an old server that sends no prize still paints the fallback",
      M.html({ number: 1, name: "Riptide", ends_ts: SEASON.ends_ts }, ROWS, {}).indexOf("$100") !== -1);
check("and so does no season at all (the server is still waking up)",
      M.html(null, null, { cta: "play" }).indexOf("$100") !== -1);

console.log("\nwho is winning:");
check("the leader is named", live.indexOf("Reef Raiders") !== -1);
check("...with their points", live.indexOf("1,240 pts") !== -1);
check("...and the gap to second, which is what makes it a race",
      live.indexOf("2nd is 180 behind") !== -1);
check("a tie at the top says so", M.html(SEASON,
      [{ name: "A", points: 10 }, { name: "B", points: 10 }], {}).indexOf("level with them") !== -1);
check("one clan alone has no gap line",
      M.html(SEASON, [ROWS[0]], {}).indexOf("behind") === -1);
// Early in a season the leader is often the only clan that has scored at all.
// "2nd is 386 behind" against a clan on zero is the leader's own score written
// twice, and it makes the prize look unreachable on the day it is easiest to
// win. Seen on the live standings, not invented.
check("a runner-up on zero points is not a race",
      M.html(SEASON, [{ name: "Belmont Board Game Club", points: 386 },
                      { name: "Nobody Yet", points: 0 }], {}).indexOf("behind") === -1);
check("...but the leader is still named",
      M.html(SEASON, [{ name: "Belmont Board Game Club", points: 386 },
                      { name: "Nobody Yet", points: 0 }], {}).indexOf("Belmont") !== -1);
const zero = M.html(SEASON, [{ id: "z", name: "Brand New Clan", points: 0 }], {});
check("a clan on zero points is NOT announced as the leader",
      zero.indexOf("Brand New Clan") === -1);
check("...the banner says nobody has scored instead",
      zero.indexOf("Nobody has scored yet") !== -1);
check("an empty season says the same", M.html(SEASON, [], {}).indexOf("Nobody has scored yet") !== -1);
// "The server told us there are no clans" and "the server has not answered"
// are different facts. On the marketing site the second is the normal state
// for the first 30 seconds while the free-tier server wakes, and announcing
// "nobody has scored yet" then is a claim about a season nothing has read.
check("...but not having heard from the server is NOT 'nobody has scored'",
      M.html(SEASON, null, {}).indexOf("Nobody has scored yet") === -1);
check("...and that state still shows the prize and the countdown",
      M.html(SEASON, null, {}).indexOf("$100") !== -1 &&
      /\d+d \d+h left/.test(M.html(SEASON, null, {})));
// A clan name is up to 24 characters of player text inside a nowrap pill, and
// the phone layout sizes the banner body to its min-content. See the
// .ccPrize-body width rule in clan-prize.css: without it a long name silently
// pushed every line of the banner off the right edge of the screen.
const CSS = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/clan-prize.css"), "utf8");
check("the phone layout pins the banner body's width (long clan names)",
      /@media \(max-width: 720px\)[\s\S]*\.ccPrize-body \{ width: 100%; \}/.test(CSS));
check("...and the clan name is the thing allowed to ellipsize",
      /\.ccPrize-lead \.nm \{[\s\S]*text-overflow: ellipsis;/.test(CSS));

console.log("\nthe countdown:");
// NOW is floored to the second, so "44 days out" renders as 43d 23h by the
// time the string is built. Either is right; 44000d is not.
const days = Number((live.match(/(\d+)d \d+h left/) || [])[1]);
check("44 days out reads in days", days === 44 || days === 43);
check("ends_ts is read as SECONDS, not milliseconds (an ms reading is 1000x)",
      Number.isFinite(days) && days < 100);
const soon = M.html(Object.assign({}, SEASON, { ends_ts: NOW + 86400 * 2 }), ROWS, {});
check("the final week turns urgent", soon.indexOf("Final stretch") !== -1);
check("...and 44 days out does not", live.indexOf("Final stretch") === -1);
const over = M.html(Object.assign({}, SEASON, { ends_ts: NOW - 60 }), ROWS, {});
check("a finished season does not count down past zero",
      over.indexOf("being finalized") !== -1 && !/-\d/.test(over));

console.log("\nthe terms, and the way in:");
check("the claim terms are always on the banner",
      live.indexOf("shipping address") !== -1 && zero.indexOf("shipping address") !== -1);
check("the marketing CTA sends the reader to the game",
      live.indexOf("play.currentsandcritters.com") !== -1 && live.indexOf("Join a clan") !== -1);
const inGame = M.html(SEASON, ROWS, { cta: "clans" });
check("the in-game CTA opens the Clans tab instead",
      inGame.indexOf("data-cc-clan-prize-go") !== -1 && inGame.indexOf("play.currents") === -1);
check("on the Clans tab itself there is no CTA at all",
      M.html(SEASON, ROWS, { cta: "none" }).indexOf("ccPrize-btn") === -1);
// On the Clans tab the podium already names the leader and the season block
// already runs the same clock, a few hundred pixels below. Repeating either
// puts the same clan on the screen twice, which is the duplicate-clan problem
// that screen was already fixed for once.
const inTab = M.html(SEASON, ROWS, { cta: "none", standings: false });
check("the Clans tab copy drops the leader the podium already names",
      inTab.indexOf("Reef Raiders") === -1);
check("...and the countdown the season block already runs",
      inTab.indexOf("left") === -1);
check("...but still states the prize and the terms, which is its whole job",
      inTab.indexOf("$100") !== -1 && inTab.indexOf("shipping address") !== -1);
check("...and the empty status row is hidden rather than left as a gap",
      /\.ccPrize-status:empty \{ display: none; \}/.test(
        fs.readFileSync(path.join(ROOT, "multiplayer/client/css/clan-prize.css"), "utf8")));
check("everywhere else the standings stay ON by default",
      M.html(SEASON, ROWS, {}).indexOf("Reef Raiders") !== -1);
check("clans-ui asks for the bare version",
      /standings: false/.test(fs.readFileSync(
        path.join(ROOT, "multiplayer/client/js/clans-ui.js"), "utf8")));

console.log("\nnothing leaks:");
[["live", live], ["zero-points", zero], ["no season", M.html(null, null, {})],
 ["finished", over], ["no rows", M.html(SEASON, [], {})]].forEach(([label, html]) => {
  check(label + ": no undefined / NaN / [object Object]",
        !/undefined|NaN|\[object Object\]/.test(html));
});
const nasty = M.html(SEASON, [{ name: '<img src=x onerror="alert(1)">', points: 5 }], {});
check("a clan name cannot inject markup",
      nasty.indexOf("<img src=x") === -1 && nasty.indexOf("&lt;img") !== -1);

// ── The banner is an advert, not part of the page ───────────────────────────
console.log("\nthe Clans tab does not depend on it:");
const CLANS = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/clans-ui.js"), "utf8");
check("clans-ui.js renders the banner through the shared module",
      CLANS.indexOf("window.__ccClanPrize") !== -1);
check("...and returns null rather than throwing when it is not loaded",
      /if \(!m \|\| typeof m\.node !== "function"\) return null;/.test(CLANS));
check("the prize banner is above the season countdown on Clans home",
      CLANS.indexOf("appendPrize(c, H.season") < CLANS.indexOf("c.appendChild(seasonBlock(H.season))"));

// ── Both hosts really load it ──────────────────────────────────────────────
console.log("\nboth hosts serve the same one file:");
const INDEX = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const PREVIEW = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");
[["index.html", INDEX], ["preview.html", PREVIEW]].forEach(([name, html]) => {
  check(name + " loads /js/clan-prize.js", /\/js\/clan-prize\.js\?v=/.test(html));
  check(name + " loads /css/clan-prize.css", /\/css\/clan-prize\.css\?v=/.test(html));
});
check("the marketing site declares the host element",
      INDEX.indexOf('id="cc-clan-prize"') !== -1);
check("the game loads it after preview-app.js, so __ccApiUrl is set",
      PREVIEW.indexOf("/js/preview-app.js") < PREVIEW.indexOf("/js/clan-prize.js"));
check("the boot meter knows about the extra script",
      PREVIEW.indexOf('"/js/clan-prize.js":') !== -1);
// Player Home builds its panels after auth resolves, so the Overview panel does
// not exist when the module first runs. Without this the banner only appears on
// the next minute tick. It must come after the Game Night call, which is the
// element the prize band positions itself under.
const APP = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
check("opening Overview re-renders the banner",
      APP.indexOf("__ccClanPrizeRender") !== -1);
check("...after Game Night, which it positions itself under",
      APP.indexOf("__ccGameNightRender") < APP.indexOf("__ccClanPrizeRender"));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
