#!/usr/bin/env node
/* The Head to Head screen: what the Head to Head card opens now.
 *
 * It is a coral reef with ten platforms up it, one per rank, each with its
 * own animal standing on it: F the Bobtail Squid, E the Staghorn Coral, D the
 * Peruvian Pelican, C the Staghorn Coral, B the Narwhal, A the Great White
 * Shark, S the Goby, S+ the Bunker, S++ the Sea Star, and the Giant Squid on
 * the summit. The platform you are standing on has your own animal on it.
 * Press a platform, and the table beside it fills with its opponents.
 * Everything below is a way that could quietly stop being true while the
 * screen still looks right:
 *
 *  1. DIFFERENT OPPONENTS. Three identical bots is one opponent copied three
 *     times, and the whole reason to have ten of them is that a table can
 *     hold several at once. Every platform with three rungs in its reach,
 *     rolled many times, must produce three distinct opponents and stay
 *     inside the reach it advertises.
 *
 *  2. NO NAMES AND NO RATINGS. The screen shows a rank and an animal. A name
 *     or an Elo creeping back onto it is the whole redesign undone.
 *
 *  3. THE CLIMB IS REACHABLE. Every rung on the ladder has to be dealt by
 *     SOME spot, or it is a difficulty nobody can ever play, and the spot
 *     above has to be openable by beating what the spot below deals.
 *
 *  4. THE SQUID'S THREE GATES. The story, the climb, and level 60. Any one of
 *     them missing keeps the summit shut, and its fight is five at one table,
 *     not four.
 *
 *  5. IT FITS. Ten platforms, a lineup and a Dive In button, on a 360px
 *     phone, with no platform standing on another.
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

const FNS = ["bmBeatenIds", "bmStoryUnlocked", "bmPlayerLevel",
             "bmGradeLocked", "bmLockNote", "bmTopUnlockedIndex",
             "bmGradeById", "bmIndexOf", "bmAt", "bmTierLetter", "bmTierClass",
             "bmBadge", "bmAnimalFor", "bmSquidId", "bmSpot", "bmSpotRank",
             "bmSpotLocked", "bmSpotLockNote", "bmTopUnlockedSpot", "bmIsFinal",
             "bmSeatCount", "bmGradeBlurb", "bmLoadGrades", "bmFinalLineup", "bmRoll",
             "bmPickSpot", "bmSpotPos", "bmReefArt", "bmRenderLadder", "bmRenderBots",
             "bmAvgRankTier",
             "bmRender", "openBotMatch", "closeBotMatch", "bmStart"]
            .map(grabFn).join("\n\n");

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
//  3. THE REEF  (the logic, run for real, thousands of times)
// ════════════════════════════════════════════════════════════════════════
console.log("\nevery platform rolls different opponents, inside its reach");
const CLIMBED = ["gilbert_carter", "jeanne_villepreux_power", "edward_forbes",
                 "steve_irwin", "william_beebe", "eugenie_clark",
                 "rachel_carson", "jacques_cousteau", "charles_darwin"];
{
  // A player who has been up the whole ladder: everything but the Squid is
  // theirs to be dealt. The climb itself is tested on its own, further down.
  const sandbox = { console, Math, Number, String, Array, JSON, Set, document: null,
                    window: { __fishGetUnlockedIcons: () => [],
                              __ccBotsBeaten: () => CLIMBED.slice(),
                              __ccPlayerLevel: () => 99 } };
  const run = new Function("sandbox", `
    with (sandbox) {
      ${STATE}
      ${grabFn("bmGradeById")}
      ${grabFn("bmIndexOf")}
      ${grabFn("bmAt")}
      ${grabFn("bmRoll")}
      ${grabFn("bmFinalLineup")}
      ${grabFn("bmTierLetter")}
      ${grabFn("bmTierClass")}
      ${grabFn("bmAnimalFor")}
      ${grabFn("bmSquidId")}
      ${grabFn("bmSpot")}
      ${grabFn("bmSpotRank")}
      ${grabFn("bmSpotLocked")}
      ${grabFn("bmSpotLockNote")}
      ${grabFn("bmTopUnlockedSpot")}
      ${grabFn("bmIsFinal")}
      ${grabFn("bmAvgRankTier")}
      ${grabFn("bmGradeBlurb")}
      ${grabFn("bmBeatenIds")}
      ${grabFn("bmStoryUnlocked")}
      ${grabFn("bmPlayerLevel")}
      ${grabFn("bmGradeLocked")}
      ${grabFn("bmLockNote")}
      ${grabFn("bmTopUnlockedIndex")}
      ${grabFn("bmSpotPos")}
      return {
        roll: (i) => { bmRoll(i); return _bmPick.slice(); },
        spots: BM_TIERS, grades: _bmGrades,
        squidLevel: BM_SQUID_LEVEL, finalSeats: BM_FINAL_SEATS,
        tier: bmTierLetter, tierClass: bmTierClass, animal: bmAnimalFor,
        blurb: bmGradeBlurb, indexOf: bmIndexOf,
        locked: bmGradeLocked, note: bmLockNote, top: bmTopUnlockedIndex,
        spotLocked: bmSpotLocked, spotRank: bmSpotRank, spotNote: bmSpotLockNote,
        topSpot: bmTopUnlockedSpot, finalLineup: bmFinalLineup,
        avg: () => { const a = bmAvgRankTier(); return a; },
        setPick: (ids) => { _bmPick = ids.slice(); },
        setTier: (i) => { _bmTier = i; }, isFinal: bmIsFinal, pos: bmSpotPos,
      };
    }
  `)(sandbox);

  const ids = run.grades.map(g => g.id);
  const SPOTS = run.spots;

  // ── the shape of the reef ──
  // The animal on each rank is the one that was asked for, rank by rank.
  const WANT = [["F", "bobtail-squid"], ["E", "staghorn-coral"], ["D", "peruvian-pelican"],
                ["C", "staghorn-coral"], ["B", "narwhal"], ["A", "great-white-shark"],
                ["S", "mandarin-goby"], ["S+", "bunker"], ["S++", "sea-star"],
                ["GS", "giant-squid"]];
  check(SPOTS.length === 10, "the reef has ten platforms, one per rank", String(SPOTS.length));
  check(SPOTS.map(t => t.tier + ":" + t.animal).join() === WANT.map(w => w.join(":")).join(),
        "F Bobtail Squid, E Staghorn Coral, D Peruvian Pelican, C Staghorn Coral, B Narwhal, "
        + "A Great White Shark, S Goby, S+ Bunker, S++ Sea Star, then the Giant Squid",
        SPOTS.map(t => t.tier + ":" + t.animal).join());
  check(SPOTS.map(t => t.n).join() === "1,2,3,4,5,6,7,8,9,10", "…numbered 1 to 10");
  check(SPOTS.filter(t => t.final).length === 1 && SPOTS[9].final === true,
        "…and exactly one of them is the last fight, at the top");
  check(run.squidLevel === 60, "the Giant Squid asks for level 60", String(run.squidLevel));
  check(run.finalSeats === 5, "…and its fight seats five", String(run.finalSeats));

  // Each platform wears the rank it tops out at, and it is the rank the
  // platform says it is for, or the animals would be standing on the wrong
  // letters.
  const ranks = SPOTS.map((_, i) => run.spotRank(i));
  check(new Set(ranks).size === 10, "every platform wears its own rank", ranks.join(","));
  check(ranks.join() === SPOTS.map(t => t.tier).join(),
        "…and it is the rank its animal was chosen for", ranks.join(","));
  check(ranks[9] === "GS", "…and the top one is the Squid's", ranks[9]);

  // ── where they stand ──
  // A zig-zag up the reef: every platform higher than the one below it, on
  // the other side of the channel from it, and the Squid alone in the middle
  // at the top.
  const P = SPOTS.map((_, i) => run.pos(i));
  check(P.every(p => p.x > 0 && p.x < 100 && p.y > 0 && p.y < 100),
        "every platform stands inside the reef", JSON.stringify(P));
  check(P.every((p, i) => i === 0 || p.y < P[i - 1].y),
        "every platform is higher up the reef than the one below it",
        P.map(p => p.y).join(","));
  check(P.slice(0, 9).every((p, i) => i === 0 || (p.x < 50) !== (P[i - 1].x < 50)),
        "…and on the other side of the channel from it, so no two stand over each other",
        P.map(p => p.x).join(","));
  check(P[9].x === 50 && P[9].y === Math.min(...P.map(p => p.y)),
        "…and the Squid has the summit to itself", JSON.stringify(P[9]));

  // ── every spot deals three different, in-reach, unlocked opponents ──
  const dealt = new Set();
  SPOTS.forEach((t, i) => {
    if (t.final) return;
    let distinct = true, inReach = true, sorted = true, squid = false, lockedOne = false;
    const hi = Math.min(t.hi, ids.length - 1);
    const lo = Math.max(0, hi - 2);
    const wide = hi - lo + 1 >= 3;
    for (let n = 0; n < 400; n++) {
      const pick = run.roll(i);
      if (pick.length !== 3) { distinct = false; break; }
      const idx = pick.map(id => ids.indexOf(id));
      idx.forEach(x => dealt.add(x));
      if (wide && new Set(pick).size !== 3) distinct = false;
      if (idx.some(x => x < lo || x > hi)) inReach = false;
      if (idx[0] > idx[1] || idx[1] > idx[2]) sorted = false;
      if (pick.includes("giant_squid")) squid = true;
      if (pick.some(id => run.locked(id))) lockedOne = true;
    }
    check(distinct, `platform ${t.tier}: ${wide ? "three DIFFERENT ranks every time" : "three opponents every time"}`);
    check(inReach, `platform ${t.tier}: never reaches outside the rungs it advertises`);
    check(sorted, `platform ${t.tier}: listed weakest first, so the table reads in order`);
    check(!squid, `platform ${t.tier}: never rolls the Giant Squid`);
    check(!lockedOne, `platform ${t.tier}: deals nobody this player has not earned`);
    check(run.roll(i).includes(ids[hi]), `platform ${t.tier}: always deals its own rank`);
  });

  // A ladder rung no spot ever deals is a difficulty nobody can play.
  const climbing = ids.length - 1;   // everything but the Squid
  const missed = [];
  for (let i = 0; i < climbing; i++) if (!dealt.has(i)) missed.push(ids[i]);
  check(missed.length === 0,
        "every rung on the ladder is dealt by some platform: none is unreachable",
        missed.join(","));

  // ── the chain: the spot above is opened by what the spot below deals ──
  // Spot k opens when its weakest rung opens, which means beating the rung
  // below THAT. If the spot below never deals that rung, the reef dead-ends.
  for (let k = 1; k < SPOTS.length; k++) {
    const t = SPOTS[k];
    const lo = t.final ? ids.length - 1 : Math.max(0, Math.min(t.lo, ids.length - 1));
    const needIdx = lo - 1;
    if (needIdx < 0) continue;
    let seen = false;
    for (let n = 0; n < 400 && !seen; n++) {
      if (run.roll(k - 1).map(id => ids.indexOf(id)).includes(needIdx)) seen = true;
    }
    check(seen, `platform ${t.tier} is opened by a rank platform ${SPOTS[k - 1].tier} actually deals`,
          "otherwise the climb dead-ends here: " + ids[needIdx]);
  }

  // ── the animals follow the reef ──
  // An opponent in the lineup wears the animal of its own rank's platform,
  // whichever platform dealt it.
  const animals = ids.map(run.animal);
  check(animals.join() === WANT.map(w => w[1]).join(),
        "every rank's opponents wear that rank's animal", animals.join(","));
  check(run.animal("steve_irwin") === "staghorn-coral"
     && run.animal("jeanne_villepreux_power") === "staghorn-coral",
        "…so rank C and rank E are both the Staghorn Coral, as asked");

  // ── the last fight ──
  const fin = run.finalLineup();
  check(fin.length === 4, "the last fight seats four bots (five at the table)", String(fin.length));
  check(fin[fin.length - 1] === "giant_squid", "…with the Giant Squid among them", fin.join(","));
  // The three that come with it are the three platforms directly under the
  // summit, so the Squid never turns up flanked by beginners.
  check(fin.slice(0, 3).map(id => ids.indexOf(id)).join() === "6,7,8",
        "…and the three with it are the top three ranks, S, S+ and S++",
        fin.slice(0, 3).join(","));
  check(fin.map(run.animal).join() === "mandarin-goby,bunker,sea-star,giant-squid",
        "…the Goby, the Bunker, the Sea Star and the Squid", fin.map(run.animal).join());
  check(run.roll(9).join() === fin.join(), "pressing the summit seats exactly that table");

  // ── colours: one per tier, and every rung has one ──
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

  // ── the average is a RANK, not a rating ──
  run.setPick(["gilbert_carter", "edward_forbes", "william_beebe"]);   // 0, 2, 4
  check(run.avg() === "D", "the table average is the middle rank of the table", run.avg());
  run.setPick(["charles_darwin", "charles_darwin", "charles_darwin"]);
  check(run.avg() === "S++", "…and a table of one rank averages to that rank", run.avg());
  check(!/\d/.test(String(run.avg())), "…and it is never a number");

  // ── the locks ──
  // bmStoryUnlocked and bmPlayerLevel read the player's own account through
  // the window bridges, so the test can BE the player.
  const asPlayer = (beaten, story, level) => {
    sandbox.window = {
      __fishGetUnlockedIcons: () => (story ? ["/avatars/sea-anemone.png"] : []),
      __ccBotsBeaten: () => beaten.slice(),
      __ccPlayerLevel: () => level,
    };
  };

  asPlayer(CLIMBED, false, 99);
  check(run.locked("giant_squid") === true, "no Red Beaded Anemone, no Squid");
  check(run.locked("charles_darwin") === false,
        "…and nothing else is locked by it, for a player who has climbed");
  check(/Giant Squid/.test(run.note("giant_squid")),
        "…and it says what to go and do", run.note("giant_squid"));
  asPlayer(CLIMBED, true, 99);
  check(run.locked("giant_squid") === false,
        "beat the story, climb the ladder, and the Squid will sit down");

  // ── the level gate, which is the Squid's alone ──
  asPlayer(CLIMBED, true, 59);
  check(run.locked("giant_squid") === true, "level 59 is not enough for the Squid");
  check(/60/.test(run.note("giant_squid")), "…and it says which level is",
        run.note("giant_squid"));
  check(/59/.test(run.note("giant_squid")), "…and which level you are on");
  check(run.grades.filter(g => g.id !== "giant_squid").every(g => !run.locked(g.id)),
        "…and no other rung cares about your level");
  asPlayer(CLIMBED, true, 60);
  check(run.locked("giant_squid") === false, "level 60 exactly is enough");
  asPlayer(CLIMBED, true, 0);
  check(run.locked("giant_squid") === true,
        "a level that cannot be read locks it, rather than giving it away");
  sandbox.window = { __fishGetUnlockedIcons: () => { throw new Error("offline"); },
                     __ccBotsBeaten: () => CLIMBED.slice(),
                     __ccPlayerLevel: () => { throw new Error("offline"); } };
  check(run.locked("giant_squid") === true,
        "an account that cannot be read locks it too");

  asPlayer(CLIMBED, true, 99);
  check(run.tier("S+") === "S+" && run.tier("S") === "S" && run.tier("S++") === "S++",
        "S+ gets its own colour: it is not an S with an extra sign");
  check(run.tier("Giant Squid") === "GS", "…and the Squid is not filed under G");
  check(run.tier(undefined) === "C", "a missing grade still gets a colour rather than crashing");

  // ── the climb, spot by spot ──
  const ORDER = run.grades.map(g => g.id);

  asPlayer([], false, 99);
  check(run.locked(ORDER[0]) === false,
        "a brand-new player can play the bottom rung: there is a way on");
  check(ORDER.slice(1).every(id => run.locked(id) === true),
        "…and every single rung above it is shut",
        ORDER.slice(1).filter(id => !run.locked(id)).join(","));
  const ABOVE = [1, 2, 3, 4, 5, 6, 7, 8, 9];
  check(run.spotLocked(0) === false, "…so the first platform on the reef is open");
  check(ABOVE.every(i => run.spotLocked(i) === true),
        "…and every platform above it wears a lock",
        ABOVE.filter(i => !run.spotLocked(i)).join(","));
  check(run.topSpot() === 0, "…so the climb starts at the bottom of the reef");
  check(/rank F/.test(run.spotNote(1)) && /rank E/.test(run.spotNote(1)),
        "…and the second platform says which rank to beat to open it", run.spotNote(1));
  check(!/Gilbert|Jeanne|Darwin|Carson/.test(run.spotNote(1)),
        "…without naming anybody: the names are gone", run.spotNote(1));
  // A spot cannot smuggle a locked rung onto the table either.
  SPOTS.forEach((t, i) => {
    if (t.final) return;
    let leaked = null;
    for (let n = 0; n < 200 && !leaked; n++) {
      const bad = run.roll(i).find(id => run.locked(id));
      if (bad) leaked = bad;
    }
    check(!leaked, `platform ${t.tier}: deals nobody this brand-new player has earned`, leaked || "");
  });

  // Beating rank F opens the E platform, and nothing further.
  asPlayer([ORDER[0]], false, 99);
  check(run.spotLocked(1) === false, "beat rank F and the E platform opens");
  check(run.spotLocked(2) === true, "…and only that one: the reef is one platform at a time");
  check(run.topSpot() === 1, "…and the top of the climb moved up exactly one");

  // A climb full of holes: beating a rung out of order opens THEIR successor
  // and nobody else's, and no spot may deal one of the rungs still shut.
  asPlayer([ORDER[5]], false, 99);
  check(run.locked(ORDER[6]) === false, "beating a rung opens the one above it");
  check(run.locked(ORDER[2]) === true, "…and leaves the ones you skipped shut");
  SPOTS.forEach((t, i) => {
    if (t.final) return;
    let leaked = null;
    for (let n = 0; n < 300 && !leaked; n++) {
      const bad = run.roll(i).find(id => run.locked(id));
      if (bad) leaked = bad;
    }
    check(!leaked,
          `platform ${t.tier}: deals nobody who is shut, even with the climb full of holes`,
          leaked || "");
  });

  // The Squid needs all three gates: it is the summit, not a side door.
  asPlayer(ORDER.slice(0, ORDER.length - 1), false, 99);
  check(run.spotLocked(9) === true,
        "climbing the whole reef is not enough for the Squid without the story");
  asPlayer([], true, 99);
  check(run.spotLocked(9) === true,
        "…and finishing the story is not enough without the climb");
  asPlayer(CLIMBED, true, 12);
  check(run.spotLocked(9) === true,
        "…and both together are not enough below level 60");
  asPlayer(CLIMBED, true, 60);
  check(run.spotLocked(9) === false,
        "climb the reef, finish the story, reach level 60, and the Squid sits down");

  // The blurb has to change as the ladder rises, or it is decoration. It is
  // no longer on this screen, but the lobby seat tiles still use it.
  const blurbs = run.grades.map(g => run.blurb(g.id));
  check(new Set(blurbs).size >= 5, "the description really changes up the ladder",
        String(new Set(blurbs).size));
  check(/handicap/i.test(run.blurb("giant_squid")),
        "the Squid's line says what it actually is");
}

// ════════════════════════════════════════════════════════════════════════
//  4. WHAT GETS SENT IS WHAT WAS SHOWN
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe table that is posted is the table on the screen");
{
  const start = grabFn("bmStart");
  check(/const final = bmIsFinal\(\);/.test(start), "it knows whether this is the last fight");
  check(/const seats = bmSeatCount\(\);/.test(start),
        "…and how many chairs that puts at the table");
  check(/return bmIsFinal\(\) \? BM_FINAL_SEATS : 4;/.test(grabFn("bmSeatCount")),
        "five seats for the Squid, four for everyone else");
  check(/const bots = seats - 1;/.test(start), "…and exactly one of them is the player");
  check(/total_players: seats, human_players: 1, ai_players: bots/.test(start),
        "which is what the request says");
  check(/ai_difficulties: _bmPick\.slice\(\)/.test(start),
        "the ranks sent are the ranks on screen");
  check(/start_now: true/.test(start),
        "the game starts with the request: a bot match has nobody to wait for");
  check(/_bmPick\.some\(bmGradeLocked\)/.test(start),
        "a locked rank is stopped before the request leaves the browser",
        "a reward handed out by accident is a reward destroyed");
  check(/visibility: "private"/.test(start),
        "a solo bot game is not advertised in the lobby browser");
  check(/r\.data\.started === false && r\.data\.start_error/.test(start),
        "a table that opened but could not start is reported, not entered");
  check(/enterRoom\(rId\)/.test(start), "and a good one is entered");
  check(/setHostToken|setSeatToken/.test(start), "the player's own tokens are kept");
  check(!/\.grade\b/.test(start) && !/Elo/.test(start),
        "nothing it says out loud carries a name or a rating");
}

// ════════════════════════════════════════════════════════════════════════
//  4b. NO NAMES, NO RATINGS, ANYWHERE THE PLAYER LOOKS
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe screen shows ranks and animals, and nothing else");
{
  const shown = [grabFn("bmRenderLadder"), grabFn("bmRenderBots"),
                 grabFn("bmRender"), grabFn("bmAvgRankTier")].join("\n");
  check(!/\.elo\b/.test(shown), "no rung's Elo is ever drawn on this screen");
  check(!/\bElo\b/.test(shown), "…and the word never appears on it either");
  check(!/\.grade\b/.test(shown), "no rung's name is drawn on it");
  check(/`Rank \$\{opt\.tier\}`/.test(shown), "the list of opponents is a list of ranks");
  check(/bmAnimalFor\(id\)/.test(shown), "and every opponent wears its rank's animal");
  check(/Table average rank/.test(HTML), "the table line asks for a rank");
  check(!/bm-avg-elo|Table average <|bm-elo-big/.test(HTML),
        "…and the old Elo average is gone from the markup");
  check(!/bm-bands|Warm Up|Rising|Abyss/.test(HTML + APP.slice(APP.indexOf("// ── Head to Head ─"))),
        "the Warm Up / Rising / Abyss presets are gone");
  // The lobby seat and the in-game badge are the same bots, so they cannot
  // keep printing what this screen stopped printing.
  const box = grabFn("buildDifficultyBox");
  check(!/Elo/.test(box) && !/opt\.grade/.test(box),
        "a lobby seat's grade list is ranks too, with no names and no Elo");
  check(/`Rank \$\{opt\.tier\}`/.test(box), "…spelled the same way");
  check(!/wr-grade-elo/.test(APP), "…and the lobby's Elo chip is gone entirely");
  check(!/wr-grade-elo/.test(CSS), "…including its styles");
}

// ════════════════════════════════════════════════════════════════════════
//  5. THE MARKUP AND STYLES EXIST
// ════════════════════════════════════════════════════════════════════════
console.log("\nthe screen is really in the page");
{
  // The words on the screen, exactly as asked: no robot in the heading, no
  // "Pick a spot" line, and no exclamation point on the subtitle.
  const head = HTML.slice(HTML.indexOf('<div class="bm-head">'),
                          HTML.indexOf('<div class="bm-ladder"'));
  check(/<h2>Head to Head<\/h2>/.test(head), "the heading is Head to Head", head.trim());
  check(!/🤖/.test(head), "…with no robot on it");
  check(/<div class="bm-sub">Climb the ladder and take on tougher opponents<\/div>/.test(head),
        "…and the line under it is 'Climb the ladder and take on tougher opponents'");
  check(!/!/.test(head.replace(/<!--[\s\S]*?-->/g, "")), "…with no exclamation point");
  check(!/Pick a spot|We seat the bots/.test(HTML), "the 'Pick a spot on the reef' line is gone");
  // The home card that opens it is called Head to Head and says the same.
  check(/id="stats-quickmatch-btn" aria-label="Head to Head, Climb the ladder and take on tougher opponents"/.test(HTML),
        "the home card is Head to Head, and says what it is for");
  check(!/Quick Match|Jump into a game/.test(HTML.slice(HTML.indexOf('<div class="ph-actions">'),
                                                       HTML.indexOf('id="stats-join-row"'))),
        "…and nothing on it says Quick Match or 'Jump into a game' any more");

  check(/id="bot-match-modal"/.test(HTML), "the modal is in the HTML");
  ["bm-reef", "bm-ladder", "bm-lineup", "bm-bots", "bm-count", "bm-lineup-sub",
   "bm-avg-rank", "bm-shuffle", "bm-play", "bm-err", "bm-close"]
    .forEach(id => check(new RegExp(`id="${id}"`).test(HTML), `#${id} exists`));
  check(/#bot-match-modal\.open \{ display: flex; \}/.test(CSS), "it opens");
  ["bm-spot", "bm-spot-animal", "bm-spot-ledge", "bm-spot-lock", "bm-spot-you",
   "bm-spot-you-tag", "bm-reef-art", "bm-reef-trail", "bm-bot",
   "bm-bot-face", "bm-grade-badge", "bm-grade-select", "bm-shuffle"]
    .forEach(c => check(new RegExp(`\\.${c}[ ,{:.]`).test(CSS), `.${c} is styled`));
  check(/#bm-reef \{[^}]*coral-background\.png/.test(CSS.replace(/\n/g, " ")),
        "the reef really is the coral reef background");
  check(/\.bm-spot\.is-locked/.test(CSS), "a locked platform looks locked");
  "FDCBAS".split("").forEach(t =>
    check(new RegExp(`\\.bm-tier-${t}[ ,{]`).test(CSS), `tier ${t} has its own colour`));
  "FDCBAS".split("").forEach(t =>
    check(new RegExp(`\\.wr-tier-${t}[ ,{]`).test(CSS), `…and so does tier ${t} in a lobby seat`));
  // The art each platform stands on has to actually be on disk, or the reef
  // draws ten broken images.
  const animalsInApp = [...STATE.matchAll(/animal: "([a-z-]+)"/g)].map(m => m[1]);
  check(animalsInApp.length === 10, "ten platform animals in the source", animalsInApp.join(","));
  [...new Set(animalsInApp)].forEach(a =>
    check(fs.existsSync(path.join(CLIENT, "avatars", a + ".png")),
          `/avatars/${a}.png is on disk`));
  // And the default animal a player with none stands there as.
  check(fs.existsSync(path.join(CLIENT, "avatars", "mullet.png")),
        "/avatars/mullet.png is on disk");
  check(fs.existsSync(path.join(CLIENT, "coral-background.png")),
        "and so is the coral reef");
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
  const b = HTML.indexOf('<!-- ══ SEAT PICKER MODAL', a);
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
let _storyDone = false, _level = 99;
let _beaten = ${JSON.stringify(CLIMBED)};
window.__fishGetUnlockedIcons = () => _storyDone ? ["/avatars/sea-anemone.png"] : [];
window.__ccBotsBeaten = () => _beaten.slice();
window.__ccPlayerLevel = () => _level;
window.__fishNickname = () => "Diver";
window.__fishMyAvatarUrl = () => "/avatars/hermit-crab.png";
`;
  const drive = `
openBotMatch();
// Press a platform the way a player does: the real click handler on the
// real button, so what is tested is what ships.
window.__press = (rank) => {
  const el = document.querySelector('.bm-spot[data-rank="' + rank + '"]');
  el.click();
  return el;
};
window.__setStory = (v) => { _storyDone = !!v; bmRender(); };
window.__setLevel = (v) => { _level = v; bmRender(); };
window.__setBeaten = (list) => { _beaten = list.slice(); bmRender(); };
// Drive the REAL controls: pick each rank through its own list, the way a
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
/* The art is not served in this harness; the boxes it would fill are. */
.bm-spot-animal,.bm-spot-you-img,.bm-bot-face{background:#9ec4e4;}
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
  const ok = (c, m) =>
    L.push((c ? "PASS " : "FAIL ") + w + "px: " + String(m).replace(/\\s+/g, " ").trim());
  const r = el => el.getBoundingClientRect();
  const vw = win.innerWidth;
  ok(vw === w, "the iframe really is " + w + "px wide (got " + vw + ")");
  ok(d.documentElement.scrollWidth <= vw + 1,
     "nothing scrolls sideways (content " + d.documentElement.scrollWidth + " in " + vw + ")");

  const box = d.getElementById("bot-match-box");
  ok(r(box).left >= -1 && r(box).right <= vw + 1, "the panel fits the window");

  // ── the reef ──
  win.__setStory(false); win.__setLevel(99);
  win.__setBeaten(${JSON.stringify(CLIMBED)});
  win.__press("F");
  const reef = d.getElementById("bm-reef");
  const spots = [...d.querySelectorAll(".bm-spot")];
  ok(spots.length === 10, "ten platforms are drawn (" + spots.length + ")");
  const ranks = spots.map(s => s.dataset.rank);
  ok(ranks.join() === "GS,S++,S+,S,A,B,C,D,E,F", "…top of the reef first (" + ranks.join() + ")");
  const WANT = { F: "bobtail-squid", E: "staghorn-coral", D: "peruvian-pelican",
                 C: "staghorn-coral", B: "narwhal", A: "great-white-shark",
                 S: "mandarin-goby", "S+": "bunker", "S++": "sea-star", GS: "giant-squid" };
  spots.forEach((el, i) => {
    const bb = r(el), rk = ranks[i];
    ok(bb.left >= r(reef).left - 1 && bb.right <= r(reef).right + 1
       && bb.top >= r(reef).top - 1 && bb.bottom <= r(reef).bottom + 1,
       "platform " + rk + " stays inside the reef");
    ok(bb.height >= 44 && bb.width >= 60,
       "platform " + rk + " is tappable (" + Math.round(bb.width) + "x" + Math.round(bb.height) + ")");
    const img = el.querySelector(".bm-spot-animal");
    ok(!!img && img.getAttribute("src") === "/avatars/" + WANT[rk] + ".png",
       "platform " + rk + " has the " + WANT[rk] + " standing on it (" + (img && img.getAttribute("src")) + ")");
    const ledge = el.querySelector(".bm-spot-ledge");
    ok(!!ledge && r(ledge).width >= 60, "platform " + rk + " has a ledge to stand on");
    ok(!!ledge && r(img).bottom >= r(ledge).top - 2 && r(img).bottom <= r(ledge).top + 12,
       "…and the animal is standing ON it (feet " + Math.round(r(img).bottom) + ", ledge " + Math.round(r(ledge).top) + ")");
    const badge = el.querySelector(".bm-spot-ledge .bm-grade-badge");
    ok(!!badge && badge.textContent === rk && r(badge).width >= 30,
       "platform " + rk + " wears its rank on the ledge");
    ok(!/\\d{3,}/.test(el.textContent), "platform " + rk + " shows no rating (" + el.textContent.trim() + ")");
  });
  // No platform stands on another, at any width.
  const ledges = spots.map(s => r(s));
  let clash = "";
  for (let a = 0; a < ledges.length; a++) for (let b = a + 1; b < ledges.length; b++) {
    const A = ledges[a], B = ledges[b];
    if (A.left < B.right - 1 && B.left < A.right - 1 && A.top < B.bottom - 1 && B.top < A.bottom - 1)
      clash = clash || (ranks[a] + "/" + ranks[b]);
  }
  ok(!clash, "no two platforms overlap (" + (clash || "none") + ")");
  // The climb goes up: every platform's ledge higher than the one below it
  // (they are listed summit first, so each one sits lower than the last).
  const tops = spots.map(s => r(s.querySelector(".bm-spot-ledge")).top);
  ok(tops.every((y, i) => i === 0 || y > tops[i - 1]),
     "every platform is higher than the one below it");
  // The coral runs the whole way up: the wall art covers the reef from the
  // summit to the floor, not just the bottom of it.
  const art = d.querySelector("#bm-ladder .bm-reef-art");
  ok(!!art, "the reef walls are drawn");
  ok(!!art && r(art).top <= r(spots[0]).top && r(art).bottom >= r(spots[9]).bottom - 1,
     "…from above the summit to below the bottom platform");
  // Only the top one is the Squid.
  const squidArt = spots.filter(s => /giant-squid/.test(s.querySelector(".bm-spot-animal").getAttribute("src")));
  ok(squidArt.length === 1, "exactly one platform is the Giant Squid (" + squidArt.length + ")");
  ok(squidArt[0] === spots[0], "…and it is the summit");
  // Your own animal stands on the platform you are on, and nowhere else.
  const you = [...d.querySelectorAll(".bm-spot-you")];
  ok(you.length === 1, "your own animal is on the reef exactly once (" + you.length + ")");
  ok(you.length === 1 && you[0].closest(".bm-spot").dataset.rank === "F",
     "…on the platform you are standing on");
  ok(you.length === 1 && /hermit-crab/.test(you[0].querySelector("img").getAttribute("src")),
     "…and it is YOUR animal (" + (you[0] && you[0].querySelector("img").getAttribute("src")) + ")");
  ok(you.length === 1 && you[0].textContent.trim() === "You", "…with a You tag on it");

  // ── the locks ──
  // A player who has beaten nobody: F open, everything above it locked and
  // SHOWN, which is the whole point of drawing them.
  win.__setBeaten([]);
  const fresh = [...d.querySelectorAll(".bm-spot")];
  ok(fresh.length === 10, "a brand-new player still sees all ten platforms");
  const locked = fresh.filter(s => s.classList.contains("is-locked"));
  ok(locked.length === 9, "…nine of them locked (" + locked.length + ")");
  ok(!fresh[9].classList.contains("is-locked"), "…and the bottom one open");
  ok(locked.every(s => s.querySelector(".bm-spot-lock")),
     "…each locked platform wearing a lock");
  ok(locked.every(s => !s.querySelector(".bm-spot-you")), "…and you are not standing on any of them");
  locked.forEach(s => {
    const lk = s.querySelector(".bm-spot-lock"), an = s.querySelector(".bm-spot-animal");
    const badge = s.querySelector(".bm-grade-badge");
    ok(r(lk).left < r(an).right && r(lk).right > r(an).left && r(lk).top < r(an).bottom,
       "platform " + s.dataset.rank + "'s lock is on its animal");
    ok(r(lk).bottom <= r(badge).top + 1 || r(lk).right <= r(badge).left + 1 || r(lk).left >= r(badge).right - 1,
       "…and not over its rank, which stays readable");
  });
  // Pressing a locked platform does nothing but say why.
  const before = [...d.querySelectorAll(".bm-bot .bm-grade-badge")].map(e => e.textContent).join();
  win.__press("B");
  ok([...d.querySelectorAll(".bm-bot .bm-grade-badge")].map(e => e.textContent).join() === before,
     "pressing a locked platform does not change the table");
  ok(d.getElementById("bm-err").textContent.length > 0, "…it says why instead");
  ok(!/\\d{3,}/.test(d.getElementById("bm-err").textContent),
     "…without quoting a rating (" + d.getElementById("bm-err").textContent + ")");
  win.__setBeaten(${JSON.stringify(CLIMBED)});
  d.getElementById("bm-err").textContent = "";

  // ── pressing a platform fills the table ──
  win.__press("F");
  const low = [...d.querySelectorAll(".bm-bot .bm-grade-badge")].map(e => e.textContent);
  ok(low.length === 3, "the F platform seats three opponents (" + low.length + ")");
  ok(low.every(t => t === "F"), "…all of them rank F (" + low.join() + ")");
  win.__press("S++");
  const high = [...d.querySelectorAll(".bm-bot .bm-grade-badge")].map(e => e.textContent);
  ok(high.length === 3, "the S++ platform seats three too");
  ok(high.join() !== low.join(), "…and a higher platform is a different table (" + low.join() + " → " + high.join() + ")");
  ok(new Set(high).size === 3, "…three DIFFERENT ranks (" + high.join() + ")");
  ok(d.querySelectorAll(".bm-spot.is-current").length === 1, "exactly one platform is lit");
  ok(d.querySelector(".bm-spot.is-current").dataset.rank === "S++",
     "…and it is the one that was pressed");
  ok(d.querySelector(".bm-spot-you").closest(".bm-spot").dataset.rank === "S++",
     "…and your animal moved up to it");
  const faces = [...d.querySelectorAll(".bm-bot .bm-bot-face")].map(e => e.getAttribute("src"));
  ok(faces.join() === "/avatars/mandarin-goby.png,/avatars/bunker.png,/avatars/sea-star.png",
     "…and each opponent wears its rank's animal (" + faces.join(" ") + ")");

  // ── the lineup rows ──
  [...d.querySelectorAll(".bm-bot")].forEach((el, i) => {
    const bb = r(el);
    ok(bb.left >= r(box).left - 1 && bb.right <= r(box).right + 1,
       "opponent " + i + " stays inside the panel");
    ok(!/\\d{3,}/.test(el.textContent), "opponent " + i + " shows no rating");
    const face = el.querySelector(".bm-bot-face");
    ok(!!face && /\\/avatars\\//.test(face.getAttribute("src")),
       "opponent " + i + " wears its animal");
    const sel = el.querySelector(".bm-grade-select");
    ok(!!sel && sel.options.length === 10, "opponent " + i + " can be set to any of the ten ranks");
    ok([...sel.options].every(o => /^(🔒 )?Rank [A-S+]+$/.test(o.textContent.trim())),
       "opponent " + i + "'s list is ranks only (" + sel.options[0].textContent + ")");
    ok(r(sel).height >= 26, "opponent " + i + "'s list is tappable (" + Math.round(r(sel).height) + "px)");
    ok(r(sel).right <= bb.right + 1, "opponent " + i + "'s list stays on its card");
  });

  ["bm-play", "bm-shuffle", "bm-close", "bm-people-btn"].forEach(id => {
    const bb = r(d.getElementById(id));
    ok(bb.width >= 30 && bb.height >= 26, id + " is tappable (" + Math.round(bb.width) + "x" + Math.round(bb.height) + ")");
    ok(bb.right <= vw + 1 && bb.left >= -1, id + " is on screen");
  });

  // ── the average is a rank ──
  win.__setPick("gilbert_carter", "edward_forbes", "william_beebe");
  const avg = d.querySelector("#bm-avg-rank .bm-grade-badge");
  ok(!!avg, "the table average is a badge");
  ok(avg.textContent === "D", "…carrying the middle rank of the table (" + avg.textContent + ")");
  ok(!/\\d/.test(d.querySelector(".bm-table-line").textContent),
     "…and the whole line has no number on it (" + d.querySelector(".bm-table-line").textContent.trim() + ")");
  const badges = [...d.querySelectorAll(".bm-bot .bm-grade-badge")].map(e => e.textContent);
  ok(badges.join() === "F,D,B", "the badges follow the picks (" + badges.join() + ")");

  // The widest badge on the ladder, on the narrowest screen.
  win.__setPick("charles_darwin", "charles_darwin", "charles_darwin");
  [...d.querySelectorAll(".bm-bot .bm-grade-badge")].forEach((el, i) => {
    ok(el.textContent === "S++", "badge " + i + " prints S++");
    ok(el.scrollWidth <= el.clientWidth + 1, "badge " + i + " is not clipped");
  });

  // ── the Giant Squid's summit ──
  {
    const squidSpot = () => [...d.querySelectorAll(".bm-spot")][0];
    win.__setStory(false); win.__setLevel(99);
    ok(squidSpot().classList.contains("is-locked"), "no story, and the Squid's summit is shut");
    win.__setStory(true); win.__setLevel(59);
    ok(squidSpot().classList.contains("is-locked"), "level 59, and it is still shut");
    win.__press("GS");
    ok(d.querySelectorAll(".bm-bot").length === 3,
       "…pressing it below level 60 does not seat its table");
    ok(/60/.test(d.getElementById("bm-err").textContent),
       "…it says which level instead (" + d.getElementById("bm-err").textContent + ")");
    win.__setLevel(60);
    ok(!squidSpot().classList.contains("is-locked"), "at level 60 the summit opens");
    ok(!squidSpot().querySelector(".bm-spot-lock"), "…and the lock comes off");
    win.__press("GS");
    const line = [...d.querySelectorAll(".bm-bot")];
    ok(line.length === 4, "the last fight seats FOUR bots: five at the table (" + line.length + ")");
    const arts = line.map(el => el.querySelector(".bm-bot-face").getAttribute("src"));
    ok(arts.join() === "/avatars/mandarin-goby.png,/avatars/bunker.png,/avatars/sea-star.png,/avatars/giant-squid.png",
       "…the Goby, the Bunker, the Sea Star and the Squid itself (" + arts.join(" ") + ")");
    ok(squidSpot().querySelector(".bm-spot-you"), "…and you are standing on the summit");
    ok(d.getElementById("bm-count").textContent.indexOf("4 / 4") === 0,
       "…and the count says four (" + d.getElementById("bm-count").textContent + ")");
    ok(line.every(el => el.querySelector(".bm-grade-select").disabled),
       "…on a table that cannot be edited: it is THE fight, not a lineup");
    ok(d.getElementById("bm-shuffle").disabled, "…and rerolling is off");
    ok(/Giant Squid/.test(d.querySelector(".bm-btn-label").textContent),
       "…and the button says what it is (" + d.querySelector(".bm-btn-label").textContent + ")");
    ok(d.getElementById("bm-play").classList.contains("is-final"),
       "…and does not look like an ordinary game");
    line.forEach((el, i) => ok(r(el).right <= r(box).right + 1,
       "last-fight seat " + i + " fits the panel"));
    // Back down the reef, and it is an ordinary table again.
    win.__press("D");
    ok(d.querySelectorAll(".bm-bot").length === 3, "step back down and it is three again");
    ok(!d.getElementById("bm-shuffle").disabled, "…and rerolling is back");
    ok(!/Giant Squid/.test(d.querySelector(".bm-btn-label").textContent),
       "…and so is Dive In");
    win.__setStory(false); win.__setLevel(99);
  }
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
