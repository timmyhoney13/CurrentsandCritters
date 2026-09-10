#!/usr/bin/env node
/* The Head to Head screen: what the Head to Head card opens now.
 *
 * The button used to put the player in a queue for other people and, on a
 * quiet evening, hand them bots anyway after a spinner. It opens a bot game
 * directly: four seats, one person, three bots, and the three bots are three
 * DIFFERENT grades. Everything below is a way that could quietly stop being
 * true while the screen still looks right:
 *
 *  1. THREE DIFFERENT OPPONENTS. Three identical bots is one opponent copied
 *     three times, and the whole reason to have ten of them is that a table
 *     can hold several at once. Every band, rolled many times, must produce
 *     three distinct opponents and stay inside the band it named.
 *
 *  2. THE NUMBER ON THE SCREEN IS THE NUMBER THAT WAS ASKED FOR. The grade the
 *     player picks has to survive being drawn, read back and posted. A screen
 *     that shows S+ and sends the default is the worst possible bug here,
 *     because the game that follows looks perfectly normal.
 *
 *  3. IT DOES NOT QUEUE. The old search must not still be running behind the
 *     new screen, or the player gets yanked into a lobby mid-choice.
 *
 *  4. IT FITS. Three opponent cards, five bands and a ten-entry list of names
 *     as long as "Jeanne Villepreux-Power", on a 360px phone.
 *
 * Run:  node test_bot_match.js      (Chrome/Chromium needed for the layout half)
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const HTML = fs.readFileSync(path.join(CLIENT, "preview.html"), "utf8");
const CSS = fs.readFileSync(path.join(CLIENT, "css/preview.css"), "utf8");
const APP = fs.readFileSync(path.join(CLIENT, "js/preview-app.js"), "utf8");
const PY = fs.readFileSync(path.join(ROOT, "fish_game_all_in_one.py"), "utf8");

let pass = 0, fail = 0;
function check(cond, name, extra) {
  if (cond) pass++;
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

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

// The module's own ladder, bands and state, sliced whole rather than mocked:
// a test that invents its own bands proves nothing about the real ones.
const STATE = APP.slice(APP.indexOf("  const BM_FALLBACK_GRADES = ["),
                        APP.indexOf("let _bmGradesLoaded = false;")
                        + "let _bmGradesLoaded = false;".length);

const FNS = ["bmBeatenIds", "bmStoryUnlocked", "bmGradeLocked", "bmLockNote",
             "bmTopUnlockedIndex",
             "bmGradeById", "bmIndexOf", "bmTierLetter", "bmTierClass", "bmBadge",
             "bmGradeBlurb",
             "bmLoadGrades", "bmRoll", "bmRenderBands", "bmRenderBots",
             "bmRenderSquid", "bmRender",
             "openBotMatch", "closeBotMatch", "bmStart"].map(grabFn).join("\n\n");

// ════════════════════════════════════════════════════════════════════════
//  1. THE BUTTON OPENS A BOT GAME, NOT A QUEUE
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe Head to Head card opens a Head to Head");
{
  const handler = APP.slice(APP.indexOf('const _qmBtn = $a("stats-quickmatch-btn")'),
                            APP.indexOf('$a("stats-create-btn")'));
  check(/openBotMatch\(\)/.test(handler), "pressing it opens the Head to Head screen");
  check(!/startQuickMatch\(\)/.test(handler), "…and does not start a matchmaking search");
  // The queue itself is not deleted, and it is not orphaned either: a button
  // on the new screen still starts it. Code nothing can reach is code nobody
  // maintains, and this is a working feature, not a leftover.
  check(/function startQuickMatch/.test(APP), "the people queue still exists");
  const people = APP.slice(APP.indexOf('document.getElementById("bm-people-btn")'),
                           APP.indexOf('document.getElementById("bm-people-btn")') + 260);
  check(/startQuickMatch\(\)/.test(people),
        "…and the Head to Head screen still offers it, so it is reachable");
  check(/closeBotMatch\(\)/.test(people),
        "…closing this screen on the way, so the search bar is not behind a modal");
  check(/id="bm-people-btn"/.test(HTML), "the way through to it is on the screen");
  const open = grabFn("openBotMatch");
  check(/cancelQuickMatch\(true\)/.test(open),
        "opening the screen stops any search that was already running",
        "otherwise a poll can yank the player into a lobby mid-choice");
}

// ════════════════════════════════════════════════════════════════════════
//  2. THE LADDER THE SCREEN DRAWS IS THE LADDER THE SERVER HAS
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe fallback ladder matches the server's");
{
  // Tiers contain "+" (S+, S++), so the tier alphabet is [A-Z+], not [A-Z].
  const pyGrades = [...PY.matchAll(/^\s*\("([a-z_]+)",\s*"([^"]+)",\s*"([A-Z+]+)",\s*(\d+),/gm)]
    .map(m => ({ id: m[1], grade: m[2], tier: m[3], elo: Number(m[4]) }));
  check(pyGrades.length === 10, "the server ladder has ten rungs", String(pyGrades.length));
  const jsGrades = [...STATE.matchAll(
      /\{ id: "([a-z_]+)",\s*grade: "([^"]+)",\s*elo: (\d+),\s*tier: "([A-Z+]+)",\s*unlock: "([a-z]*)",\s*requires: "([a-z_]*)" \}/g)]
    .map(m => ({ id: m[1], grade: m[2], elo: Number(m[3]), tier: m[4], unlock: m[5],
                 requires: m[6] }));
  check(jsGrades.length === 10, "so does the client's fallback", String(jsGrades.length));
  check(jsGrades.map(g => g.id).join() === pyGrades.map(g => g.id).join(),
        "the same grades, in the same order");
  const drift = jsGrades.filter((g, i) => g.grade !== pyGrades[i].grade
                                       || g.elo !== pyGrades[i].elo
                                       || g.tier !== pyGrades[i].tier);
  check(drift.length === 0,
        "the same names, Elo and badge colours, so a slow /api/bot_grades never shows a wrong number",
        drift.map(g => g.grade).join(", "));
  // The gate has to match too, or the fallback would offer the Squid to
  // somebody who has not earned it, in the seconds before the ladder lands.
  const squidJs = jsGrades.find(g => g.id === "giant_squid");
  check(squidJs && squidJs.unlock === "story", "the fallback knows the Squid is gated");
  check(jsGrades.filter(g => g.unlock === "story").length === 1,
        "…and that the story gate is the Squid's alone");
  // The climb: every rung but the bottom is opened by beating the one below,
  // and the fallback has to agree with the server about which rung that is or
  // it would unlock the wrong opponent in the seconds before the ladder lands.
  check(jsGrades[0].requires === "" && jsGrades[0].unlock === "",
        "the bottom rung is open to everyone in the fallback too");
  check(jsGrades.slice(1).every((g, i) => g.requires === jsGrades[i].id),
        "every other rung names the rung below it",
        jsGrades.map(g => g.id + "<-" + g.requires).join(" "));
  check(jsGrades.slice(1).every(g => g.unlock),
        "…and every one of them is gated until it is earned");
  check(/"giant_squid": "story"/.test(PY), "the server names the same gate");
  // And it is only ever a fallback: the live ladder comes from the server.
  check(/apiFetch\("\/api\/bot_grades"/.test(grabFn("bmLoadGrades")),
        "the real ladder is fetched from the server");
}

// ════════════════════════════════════════════════════════════════════════
//  2b. WHAT COUNTS AS BEATING ONE  (the real rule, run for real)
// ════════════════════════════════════════════════════════════════════════
// The rung above only opens if you WIN, outright, at a table that rung was
// sitting at. This is the block inside saveGameStats that decides it, lifted
// out of the source and run against tables it never saw, because every other
// part of the climb trusts whatever this returns.
console.log("\na rung is only beaten by winning the game outright");
{
  const start = APP.indexOf("const _botsBeatenNow = (() => {");
  check(start > 0, "the beat-recorder is where the climb says it is");
  // Balance the parens from the arrow function to its own "})();".
  let d = 0, end = -1;
  for (let j = APP.indexOf("{", start); j < APP.length; j++) {
    if (APP[j] === "{") d++;
    else if (APP[j] === "}" && --d === 0) { end = APP.indexOf(";", j) + 1; break; }
  }
  check(end > start, "…and it is one self-contained block");
  const src = APP.slice(start, end);

  const beaten = (opts) => {
    const fn = new Function(
      "_modded", "isWinner", "finalScores", "myScore", "_latestSeatsForSurf",
      src + "\nreturn _botsBeatenNow;");
    return fn(opts.modded || false, opts.isWinner, opts.finalScores,
              opts.myScore, opts.seats || []);
  };
  const AI = (id) => ({ kind: "ai", difficulty: id });
  const HUMAN = { kind: "human", claimed_name: "Diver" };
  const THREE = [HUMAN, AI("edward_forbes"), AI("steve_irwin"), AI("william_beebe")];
  const sc = (...xs) => xs.map((n) => ({ score: n }));

  let got = beaten({ isWinner: true, myScore: 80, finalScores: sc(80, 70, 60, 50),
                     seats: THREE });
  check(got.length === 3
        && got.includes("edward_forbes") && got.includes("steve_irwin")
        && got.includes("william_beebe"),
        "winning a table of three hands over all three", got.join(","));

  got = beaten({ isWinner: false, myScore: 70, finalScores: sc(80, 70, 60, 50),
                 seats: THREE });
  check(got.length === 0, "losing hands over nobody", got.join(","));

  // The rule the whole climb rests on: outright. A shared top is not a win
  // over the bot you shared it with.
  got = beaten({ isWinner: true, myScore: 80, finalScores: sc(80, 80, 60, 50),
                 seats: THREE });
  check(got.length === 0, "tying for the top hands over nobody either", got.join(","));

  got = beaten({ isWinner: true, myScore: 80, finalScores: sc(80, 70, 60, 50),
                 seats: THREE, modded: true });
  check(got.length === 0,
        "a modded game hands over nobody: dealing yourself the deck is not beating anyone",
        got.join(","));

  got = beaten({ isWinner: true, myScore: 90,
                 finalScores: sc(90, 70, 60, 50),
                 seats: [HUMAN, HUMAN, AI("rachel_carson"), AI("rachel_carson")] });
  check(got.length === 1 && got[0] === "rachel_carson",
        "people are not rungs, and the same rung twice is still one rung", got.join(","));

  got = beaten({ isWinner: true, myScore: 40, finalScores: [], seats: THREE });
  check(got.length === 0, "a table with no scores hands over nobody", got.join(","));

  got = beaten({ isWinner: true, myScore: 80, finalScores: sc(80, 70),
                 seats: [HUMAN, { kind: "ai", difficulty: "" }] });
  check(got.length === 0, "a bot seat with no grade on it is not recorded", got.join(","));
}

// ════════════════════════════════════════════════════════════════════════
//  3. ROLLING A TABLE  (the logic, run for real, thousands of times)
// ════════════════════════════════════════════════════════════════════════
console.log("\nevery band rolls three different opponents, inside the band");
{
  // The bands are about RANGE, not about the climb, so this player has been
  // up the whole ladder: everything but the Squid is theirs to be dealt. The
  // climb itself is tested on its own, further down.
  const CLIMBED = ["gilbert_carter", "jeanne_villepreux_power", "edward_forbes",
                   "steve_irwin", "william_beebe", "eugenie_clark",
                   "rachel_carson", "jacques_cousteau", "charles_darwin"];
  const sandbox = { console, Math, Number, String, Array, JSON, Set, document: null,
                    window: { __fishGetUnlockedIcons: () => [],
                              __ccBotsBeaten: () => CLIMBED.slice() } };
  const run = new Function("sandbox", `
    with (sandbox) {
      ${STATE}
      ${grabFn("bmGradeById")}
      ${grabFn("bmIndexOf")}
      ${grabFn("bmRoll")}
      ${grabFn("bmTierLetter")}
      ${grabFn("bmTierClass")}
      ${grabFn("bmGradeBlurb")}
      ${grabFn("bmBeatenIds")}
      ${grabFn("bmStoryUnlocked")}
      ${grabFn("bmGradeLocked")}
      ${grabFn("bmLockNote")}
      ${grabFn("bmTopUnlockedIndex")}
      return { bmRoll: () => _bmPick, roll: (b) => { bmRoll(b); return _bmPick.slice(); },
               bands: BM_BANDS, grades: _bmGrades,
               tier: bmTierLetter, tierClass: bmTierClass,
               blurb: bmGradeBlurb, indexOf: bmIndexOf,
               locked: bmGradeLocked, note: bmLockNote, top: bmTopUnlockedIndex };
    }
  `)(sandbox);

  check(run.bands.length >= 4, "there are several bands to choose from");
  // No preset may ever hand over the Squid. It is the end of the story, not
  // something you get by pressing "Full Ladder".
  check(run.grades.some(g => g.id === "giant_squid"),
        "the Squid IS in the list the screen draws: shown, not hidden");
  run.bands.forEach(band => {
    let leaked = false;
    for (let n = 0; n < 300 && !leaked; n++) {
      if (run.roll(band.id).includes("giant_squid")) leaked = true;
    }
    check(!leaked, `${band.label}: never rolls the Giant Squid`);
  });
  const ids = run.grades.map(g => g.id);
  run.bands.forEach(band => {
    let allDistinct = true, allInBand = true, sorted = true, sawTop = false, sawBottom = false;
    const hi = Math.min(band.hi, ids.length - 1);
    const lo = Math.max(0, Math.min(band.lo, hi));
    const wide = hi - lo + 1 > 3;
    for (let n = 0; n < 400; n++) {
      const pick = run.roll(band.id);
      if (pick.length !== 3) { allDistinct = false; break; }
      const idx = pick.map(id => ids.indexOf(id));
      if (wide && new Set(pick).size !== 3) allDistinct = false;
      if (idx.some(i => i < lo || i > hi)) allInBand = false;
      if (idx[0] > idx[1] || idx[1] > idx[2]) sorted = false;
      if (idx.includes(hi)) sawTop = true;
      if (idx.includes(lo)) sawBottom = true;
    }
    check(allDistinct, `${band.label}: three DIFFERENT grades every time`);
    check(allInBand, `${band.label}: never reaches outside the range it advertises`);
    check(sorted, `${band.label}: listed weakest first, so the table reads in order`);
    if (wide) {
      check(sawTop && sawBottom,
            `${band.label}: both ends of the band do come up`,
            "a band that only ever rolls the middle is three bands pretending to be one");
    }
  });

  // A band exactly three wide IS its answer; it must not quietly widen.
  const fierce = run.bands.find(b => b.hi - b.lo === 2);
  if (fierce) {
    const pick = run.roll(fierce.id);
    check(pick.map(id => ids.indexOf(id)).join() === `${fierce.lo},${fierce.lo + 1},${fierce.hi}`,
          `${fierce.label}: a three-wide band seats exactly its three grades`);
  }

  // Colours: one per tier, and every rung has one.
  const TIERS = ["F","E","D","C","B","A","S","S+","S++","GS"];
  const letters = run.grades.map(g => run.tier(g.id));
  check(letters.every(l => TIERS.includes(l)),
        "every rung maps to a tier colour", letters.join(","));
  check(new Set(letters).size === 10, "all ten tiers are represented",
        [...new Set(letters)].join(","));
  // "+" cannot go in a class name unescaped, so the badge spells it "P". A
  // tier whose class came out empty, or carrying a "+", would paint an
  // unstyled badge and nobody would see an error.
  check(run.tierClass("S+") === "SP" && run.tierClass("S++") === "SPP",
        "the + tiers get a class a stylesheet can actually match",
        run.tierClass("S+") + " / " + run.tierClass("S++"));
  check(run.grades.every(g => /^[A-Za-z0-9_-]+$/.test(run.tierClass(g.tier))),
        "…and every tier class is a legal selector",
        run.grades.map(g => run.tierClass(g.tier)).join(","));

  // ── the lock itself ──
  // bmStoryUnlocked reads the player's own collection through the window
  // bridge, so the test can be the player: with and without the critter that
  // only a finished story gives you.
  sandbox.window = { __fishGetUnlockedIcons: () => [],
                     __ccBotsBeaten: () => CLIMBED.slice() };
  check(run.locked("giant_squid") === true, "no Red Beaded Anemone, no Squid");
  check(run.locked("charles_darwin") === false,
        "…and nothing else is locked by it, for a player who has climbed");
  check(/Giant Squid/.test(run.note("giant_squid")),
        "…and it says what to go and do", run.note("giant_squid"));
  sandbox.window = { __fishGetUnlockedIcons: () => ["/avatars/sea-anemone.png"],
                     __ccBotsBeaten: () => CLIMBED.slice() };
  check(run.locked("giant_squid") === false,
        "beat the Squid and the Squid will sit down at your table");
  sandbox.window = { __fishGetUnlockedIcons: () => { throw new Error("offline"); },
                     __ccBotsBeaten: () => CLIMBED.slice() };
  check(run.locked("giant_squid") === true,
        "a collection that cannot be read locks it, rather than giving it away");
  sandbox.window = { __fishGetUnlockedIcons: () => ["/avatars/sea-anemone.png"],
                     __ccBotsBeaten: () => CLIMBED.slice() };
  check(run.tier("S+") === "S+" && run.tier("S") === "S" && run.tier("S++") === "S++",
        "S+ gets its own colour: it is not an S with an extra sign");
  check(run.tier("Giant Squid") === "GS", "…and the Squid is not filed under G");
  check(run.tier(undefined) === "C", "a missing grade still gets a colour rather than crashing");

  // ── the climb ──────────────────────────────────────────────────────────
  // Every rung is opened by beating the one below it. The two ends of that
  // are what matter: a brand-new player must be able to start, and must not
  // be able to skip.
  const ORDER = run.grades.map(g => g.id);
  const asPlayer = (beaten, story) => {
    sandbox.window = {
      __fishGetUnlockedIcons: () => (story ? ["/avatars/sea-anemone.png"] : []),
      __ccBotsBeaten: () => beaten.slice(),
    };
  };

  asPlayer([], false);
  check(run.locked(ORDER[0]) === false,
        "a brand-new player can play the bottom rung: there is a way on");
  check(ORDER.slice(1).every(id => run.locked(id) === true),
        "…and every single rung above it is shut",
        ORDER.slice(1).filter(id => !run.locked(id)).join(","));
  check(run.top() === 0, "…so the climb starts at the bottom");
  check(/Gilbert Thomas Carter/.test(run.note(ORDER[1])),
        "…and the second rung says who to beat to open it", run.note(ORDER[1]));
  // A preset cannot smuggle a locked rung onto the table either.
  run.bands.forEach(band => {
    let leaked = false;
    for (let n = 0; n < 200 && !leaked; n++) {
      if (run.roll(band.id).some(id => run.locked(id))) leaked = true;
    }
    check(!leaked, `${band.label}: deals nobody this player has not earned`);
  });

  // One win, one rung.
  asPlayer([ORDER[0]], false);
  check(run.locked(ORDER[1]) === false, "beat the bottom rung and the next one opens");
  check(run.locked(ORDER[2]) === true, "…and only that one: the climb is one at a time");
  check(run.top() === 1, "…and the top of the climb moved up exactly one");

  // Beating somebody out of order opens THEIR successor and nobody else's:
  // the chain is per-rung, not a high-water mark.
  asPlayer([ORDER[5]], false);
  check(run.locked(ORDER[6]) === false, "beating a rung opens the one above it");
  // …and THIS is the case a range check alone gets wrong. The climb now
  // reaches rung 6, but rungs 1 to 5 are still shut, so a band that trusted
  // its two ends would happily deal one of them. Every rung in the band has
  // to be asked individually.
  run.bands.forEach(band => {
    let leaked = null;
    for (let n = 0; n < 300 && !leaked; n++) {
      const bad = run.roll(band.id).find(id => run.locked(id));
      if (bad) leaked = bad;
    }
    check(!leaked,
          `${band.label}: deals nobody who is shut, even with the climb full of holes`,
          leaked || "");
  });
  check(run.locked(ORDER[2]) === true, "…and leaves the ones you skipped shut");

  // The Squid needs the whole climb AND the story: it is the last rung, not a
  // side door.
  asPlayer(ORDER.slice(0, ORDER.length - 1), false);
  check(run.locked("giant_squid") === true,
        "climbing the whole ladder is not enough for the Squid without the story");
  asPlayer([], true);
  check(run.locked("giant_squid") === true,
        "…and finishing the story is not enough without the climb");
  asPlayer(CLIMBED, true);
  check(run.locked("giant_squid") === false,
        "beat Charles Darwin AND finish the story, and the Squid sits down");

  // The blurb has to change as the ladder rises, or it is decoration.
  const blurbs = run.grades.map(g => run.blurb(g.id));
  check(new Set(blurbs).size >= 5, "the description really changes up the ladder",
        String(new Set(blurbs).size));
  check(run.blurb("f") !== run.blurb("giant_squid"),
        "the bottom and the top do not say the same thing");
  check(/handicap/i.test(run.blurb("giant_squid")),
        "the Squid's line says what it actually is");
}

// ════════════════════════════════════════════════════════════════════════
//  4. WHAT GETS SENT IS WHAT WAS SHOWN
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe table that is posted is the table on the screen");
{
  const start = grabFn("bmStart");
  check(/total_players: 4/.test(start), "four seats");
  check(/human_players: 1/.test(start), "one of them is the player");
  check(/ai_players: 3/.test(start), "three of them are bots");
  check(/ai_difficulties: _bmPick\.slice\(\)/.test(start),
        "the three grades sent are the three grades on screen");
  check(/start_now: true/.test(start),
        "the game starts with the request: a bot match has nobody to wait for");
  check(/_bmPick\.some\(bmGradeLocked\)/.test(start),
        "a locked grade is stopped before the request leaves the browser",
        "a reward handed out by accident is a reward destroyed");
  check(/visibility: "private"/.test(start),
        "a solo bot game is not advertised in the lobby browser");
  check(/r\.data\.started === false && r\.data\.start_error/.test(start),
        "a table that opened but could not start is reported, not entered");
  check(/enterRoom\(rId\)/.test(start), "and a good one is entered");
  check(/setHostToken|setSeatToken/.test(start), "the player's own tokens are kept");
}

// ════════════════════════════════════════════════════════════════════════
//  5. THE MARKUP AND STYLES EXIST
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe screen is really in the page");
{
  check(/id="bot-match-modal"/.test(HTML), "the modal is in the HTML");
  ["bm-bands", "bm-bots", "bm-squid", "bm-avg-elo", "bm-shuffle", "bm-play",
   "bm-err", "bm-close"]
    .forEach(id => check(new RegExp(`id="${id}"`).test(HTML), `#${id} exists`));
  check(/#bot-match-modal\.open \{ display: flex; \}/.test(CSS), "it opens");
  ["bm-band", "bm-bot", "bm-grade-badge", "bm-grade-select", "bm-shuffle",
   "bm-squid", "bm-squid-face", "bm-squid-btn"]
    .forEach(c => check(new RegExp(`\\.${c}[ ,{:]`).test(CSS), `.${c} is styled`));
  "FDCBAS".split("").forEach(t =>
    check(new RegExp(`\\.bm-tier-${t}[ ,{]`).test(CSS), `tier ${t} has its own colour`));
  "FDCBAS".split("").forEach(t =>
    check(new RegExp(`\\.wr-tier-${t}[ ,{]`).test(CSS), `…and so does tier ${t} in a lobby seat`));
}

// ════════════════════════════════════════════════════════════════════════
//  6. THE REAL RENDER, PHONE THROUGH DESKTOP
// ════════════════════════════════════════════════════════════════════════
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

function page() {
  const a = HTML.indexOf('<div id="bot-match-modal">');
  const b = HTML.indexOf("\n</div>", HTML.indexOf('<button id="bm-play">', a)) + "\n</div>".length;
  const modal = HTML.slice(a, b);
  const stubs = `
function apiFetch() { return Promise.resolve({ ok: false, data: null }); }
function apiPost() { return Promise.resolve({ ok: false, data: null }); }
function cancelQuickMatch() { return Promise.resolve(); }
function startQuickMatch() {}
function showToast() {} function ccReport() {} function enterRoom() {}
function setHostToken() {} function setSeatToken() {}
function normalizeRoomId(x) { return String(x || ""); }
function freshRoomCode() { return "ABCDE"; }
function createKeyKey() { return "k"; }
let roomId = "", _myRoomVisibility = "", _myRoomCode = "", compMode = false;
let _storyDone = false;
let _beaten = ["gilbert_carter", "jeanne_villepreux_power", "edward_forbes",
               "steve_irwin", "william_beebe", "eugenie_clark",
               "rachel_carson", "jacques_cousteau", "charles_darwin"];
window.__fishGetUnlockedIcons = () => _storyDone ? ["/avatars/sea-anemone.png"] : [];
window.__ccBotsBeaten = () => _beaten.slice();
window.__fishNickname = () => "Diver";
`;
  const drive = `
openBotMatch();
window.__pickBand = (id) => { _bmBand = id; bmRoll(id); bmRender(); };
// Finish (or un-finish) the story, and redraw: the gate reads the player's
// own collection, so this is the player earning the Red Beaded Anemone.
window.__setStory = (v) => { _storyDone = !!v; bmRender(); };
// Who this player has beaten, so the climb can be wound back to a brand-new
// account and forward again without reloading the page.
window.__setBeaten = (list) => { _beaten = list.slice(); bmRender(); };
// Drive the REAL controls: pick each grade through its own list, the way a
// player does, so the change handler is what does the work.
window.__setPick = (a, b, c) => {
  [a, b, c].forEach((id, i) => {
    const sel = document.querySelectorAll(".bm-bot .bm-grade-select")[i];
    sel.value = id;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  });
};
`;
  const inner = `<!doctype html><html><head><meta charset="utf-8"><style>
${CSS}
html,body{margin:0;} #bot-match-modal{position:static;min-height:100vh;}
</style></head><body>${modal}<script>${stubs}\n${STATE}\n${FNS}\n${drive}</scr` + `ipt></body></html>`;

  return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#111;}iframe{border:0;display:block;}</style></head><body>
<iframe id="f" width="1280" height="1000"></iframe><div id="out"></div>
<script>
const SRC = ${JSON.stringify(inner).replace(/<\//g, "<\\/")};
const WIDTHS = [360, 390, 430, 600, 820, 1024, 1280];
const L = [];
const f = document.getElementById("f");
function measure(w) {
  const d = f.contentDocument, win = f.contentWindow;
  const ok = (c, m) => L.push((c ? "PASS " : "FAIL ") + w + "px: " + m);
  const r = el => el.getBoundingClientRect();
  const vw = win.innerWidth;
  ok(vw === w, "the iframe really is " + w + "px wide (got " + vw + ")");
  ok(d.documentElement.scrollWidth <= vw + 1,
     "nothing scrolls sideways (content " + d.documentElement.scrollWidth + " in " + vw + ")");

  const box = d.getElementById("bot-match-box");
  ok(r(box).left >= -1 && r(box).right <= vw + 1, "the panel fits the window");

  const bots = [...d.querySelectorAll(".bm-bot")];
  ok(bots.length === 3, "three opponents are drawn (" + bots.length + ")");
  bots.forEach((el, i) => {
    const bb = r(el);
    ok(bb.left >= r(box).left - 1 && bb.right <= r(box).right + 1,
       "opponent " + i + " stays inside the panel");
    const badge = el.querySelector(".bm-grade-badge");
    ok(!!badge && r(badge).width >= 30, "opponent " + i + " wears a readable grade badge");
    const sel = el.querySelector(".bm-grade-select");
    ok(!!sel && sel.options.length === 10, "opponent " + i + " can be set to any of the ten rungs");
    const lockedOpts = [...sel.options].filter(o => o.disabled);
    ok(lockedOpts.length === 1 && /Giant Squid/.test(lockedOpts[0].textContent),
       "opponent " + i + " shows the Giant Squid, locked (" + lockedOpts.length + ")");
    ok(/🔒/.test(lockedOpts[0] ? lockedOpts[0].textContent : ""),
       "opponent " + i + "'s locked grade is marked with a lock");
    ok(r(sel).height >= 26, "opponent " + i + "'s list is tappable (" + Math.round(r(sel).height) + "px)");
    ok(r(sel).right <= bb.right + 1, "opponent " + i + "'s list stays on its card");
    const elo = el.querySelector(".bm-bot-elo");
    ok(!!elo && /\\d/.test(elo.textContent), "opponent " + i + " shows an Elo");
    ok(r(elo).right <= bb.right + 1, "…inside its card");
  });
  // Three DIFFERENT grades, in the rendered DOM, not just in the logic.
  const shown = bots.map(el => el.querySelector(".bm-grade-badge").textContent);
  ok(new Set(shown).size === 3, "the three badges show three different grades (" + shown.join(",") + ")");

  const bands = [...d.querySelectorAll(".bm-band")];
  ok(bands.length >= 5, "the bands are drawn (" + bands.length + ")");
  bands.forEach((el, i) => {
    const bb = r(el);
    ok(bb.height >= 30, "band " + i + " is tappable (" + Math.round(bb.height) + "px tall)");
    ok(bb.left >= r(box).left - 1 && bb.right <= r(box).right + 1, "band " + i + " is inside the panel");
  });
  ok(d.querySelectorAll(".bm-band.active").length <= 1, "at most one band is lit at a time");

  ["bm-play", "bm-shuffle", "bm-close", "bm-people-btn"].forEach(id => {
    const bb = r(d.getElementById(id));
    ok(bb.width >= 30 && bb.height >= 26, id + " is tappable (" + Math.round(bb.width) + "x" + Math.round(bb.height) + ")");
    ok(bb.right <= vw + 1 && bb.left >= -1, id + " is on screen");
  });

  // The average has to be the average, and it has to move when the table does.
  const avg = () => Number(d.getElementById("bm-avg-elo").textContent);
  win.__pickBand("warmup");
  const low = avg();
  win.__pickBand("abyss");
  const high = avg();
  ok(high > low, "a harder band raises the table average (" + low + " → " + high + ")");
  win.__setPick("gilbert_carter", "william_beebe", "charles_darwin");
  ok(avg() === Math.round((500 + 1100 + 1900) / 3),
     "the average really is the mean of the three (" + avg() + ")");
  // The badge carries the TIER: the names are people and do not fit in one.
  const badges = [...d.querySelectorAll(".bm-grade-badge")].map(e => e.textContent);
  ok(badges.join() === "F,B,S++", "and the badges follow the picks (" + badges.join() + ")");
  const names = [...d.querySelectorAll(".bm-bot-name")].map(e => e.textContent);
  ok(names.join() === "Gilbert Thomas Carter,William Beebe,Charles Darwin",
     "…and each opponent is named, not numbered (" + names.join(" / ") + ")");
  ok(d.querySelectorAll(".bm-band.active").length === 0,
     "hand-picking a grade un-lights the bands: it is nobody's roll any more");

  // The widest badge on the ladder, on the narrowest screen. "S++" is the
  // widest a player can pick, and the longest NAME on the ladder has to fit
  // on the same row without pushing the Elo off it.
  win.__setPick("charles_darwin", "charles_darwin", "charles_darwin");
  [...d.querySelectorAll(".bm-grade-badge")].forEach((el, i) => {
    ok(el.textContent === "S++", "badge " + i + " prints S++");
    ok(el.scrollWidth <= el.clientWidth + 1, "badge " + i + " is not clipped");
  });
  win.__setPick("jeanne_villepreux_power", "jeanne_villepreux_power",
                "jeanne_villepreux_power");
  [...d.querySelectorAll(".bm-bot")].forEach((row, i) => {
    const nm = row.querySelector(".bm-bot-name");
    const el = row.querySelector(".bm-bot-elo");
    ok(nm.textContent === "Jeanne Villepreux-Power",
       "row " + i + " prints the longest name on the ladder in full");
    ok(r(nm).right <= r(row).right + 1 && r(el).right <= r(row).right + 1,
       "…and neither it nor the Elo runs off the card at " + w + "px");
  });
  {
    const sel = d.querySelector(".bm-bot .bm-grade-select");
    const squid = [...sel.options].find(o => /Giant Squid/.test(o.textContent));
    ok(!!squid, "the Giant Squid is in the list at " + w + "px");
    ok(squid.disabled, "…and it is not selectable");
    ok(sel.getBoundingClientRect().right <= r(sel.closest(".bm-bot")).right + 1,
       "…and the list holding it still fits its card");
  }
  // ── the Giant Squid panel ──
  // Locked, it is still on the screen: that is the whole point of it.
  {
    const sq = d.getElementById("bm-squid");
    ok(!!sq && sq.offsetHeight > 20, "the Giant Squid panel is on the screen");
    ok(/Giant Squid/.test(sq.textContent), "…and it names the Squid");
    ok(/2150/.test(sq.textContent), "…and shows what it is worth");
    ok(sq.classList.contains("is-locked"), "…and shows as locked");
    ok(/Beat the Giant Squid/.test(sq.textContent),
       "…and says what to go and do about it");
    ok(!sq.querySelector(".bm-squid-btn"),
       "…with no way to seat it while it is locked");
    ok(r(sq).right <= r(box).right + 1 && r(sq).left >= r(box).left - 1,
       "…and it fits the panel");

    // Now finish the story and look again.
    win.__setStory(true);
    ok(!sq.classList.contains("is-locked"), "beaten, it stops being locked");
    const btn = sq.querySelector(".bm-squid-btn");
    ok(!!btn, "…and offers to sit down");
    ok(r(btn).height >= 26 && r(btn).width >= 40,
       "…on a button you can actually hit (" + Math.round(r(btn).width) + "x" + Math.round(r(btn).height) + ")");
    btn.click();
    const seated = [...d.querySelectorAll(".bm-bot-name")].map(e => e.textContent);
    ok(seated[2] === "Giant Squid", "…and it takes the last chair (" + seated.join(",") + ")");
    ok(seated[0] !== "Giant Squid" && seated[1] !== "Giant Squid",
       "…exactly one of it, not a table of three");
    const sel2 = d.querySelectorAll(".bm-bot .bm-grade-select")[0];
    ok(![...sel2.options].some(o => o.disabled),
       "…and nothing is locked in the lists any more");
    win.__setStory(false);
  }

  win.__pickBand("rising");
}
f.onload = () => {
  WIDTHS.forEach(w => {
    f.width = String(w);
    f.contentWindow.document.body.offsetHeight;
    try { measure(w); } catch (e) { L.push("FAIL " + w + "px: threw " + e.message); }
  });
  document.getElementById("out").textContent = L.join("\\n");
};
f.srcdoc = SRC;
</scr` + `ipt></body></html>`;
}

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found: skipping the layout half.");
} else {
  console.log("\nthe real render, phone through desktop");
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-botmatch-"));
  const file = path.join(tmp, "botmatch.html");
  fs.writeFileSync(file, page());
  let dom = "";
  try {
    dom = execFileSync(CHROME, [
      "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      "--window-size=1800,1100", "--virtual-time-budget=20000",
      "--dump-dom", "file://" + file,
    ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 120000,
         maxBuffer: 64 * 1024 * 1024 });
  } catch (e) {
    check(false, "Chrome ran", e.message);
  }
  const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
  if (!m || !m[1].trim()) check(false, "measurements came back from the iframe");
  else m[1].split("\n").filter(Boolean).forEach(line =>
    check(line.startsWith("PASS "), line.replace(/^(PASS|FAIL) /, "")));
  fs.rmSync(tmp, { recursive: true, force: true });
}

console.log(`\n${fail ? "FAILED" : "All"} ${fail ? fail + " of " + (pass + fail) : pass} checks${fail ? "" : " passed"}.`);
process.exit(fail ? 1 : 0);
