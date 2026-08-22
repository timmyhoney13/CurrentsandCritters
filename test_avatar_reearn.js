#!/usr/bin/env node
/* Avatar re-earn-after-trading tests.
 *
 *   node test_avatar_reearn.js
 *
 * Trading an item away must NOT leave its unlock requirement permanently
 * satisfied: banked progress (75 lifetime Play Agains, a level, a rank…) used
 * to hand the avatar straight back on the next stats load. The server snapshots
 * the giver's progress (see _trade_away_after in multiplayer_server.py) and the
 * client compares live progress against that snapshot.
 *
 * preview-app.js is a 25k-line browser file, so instead of loading it we lift
 * the REAL source of the pieces under test out of it and run them in a sandbox
 * with the same collaborators they have in the browser. That keeps the tests
 * honest (they fail if the shipped source changes) without needing a DOM.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = fs.readFileSync(
  path.join(__dirname, "multiplayer/client/js/preview-app.js"), "utf8");

let passed = 0, failed = 0;
function check(name, cond, detail) {
  if (cond) { passed++; console.log("  ✓ " + name); }
  else { failed++; console.error("  ✗ FAIL: " + name + (detail ? ": " + detail : "")); }
}
function section(t) { console.log("\n" + t); }

// ── lift the code under test out of preview-app.js ──────────────────────────
// Each slice runs from a unique marker to the start of the next known one, so a
// rename in the app surfaces here as a hard failure rather than a silent skip.
function slice(startMarker, endMarker) {
  const i = SRC.indexOf(startMarker);
  if (i < 0) throw new Error(`marker not found in preview-app.js: ${startMarker}`);
  const j = SRC.indexOf(endMarker, i + startMarker.length);
  if (j < 0) throw new Error(`end marker not found after ${startMarker}: ${endMarker}`);
  return SRC.slice(i, j);
}

const code = [
  slice("const LEVEL_XP_TOTALS = [", "];") + "];",
  slice("const ANIMAL_AVATARS = [", "\n  ];") + "\n];",
  slice("function getStoredTotalXp(stats) {", "function getLevelProgressFromTotalXp"),
  slice("function rankTierValue(rankName){", "const _RANK_TIER_VALUE"),
  slice("const _RANK_TIER_VALUE =", "\n"),
  slice("const REEARN_GATED_TYPES", "// ── Animal unlock screen queue"),
].join("\n");

// Achievement meters are read through the app's own accessor, so the sandbox
// provides it exactly as the browser does. Tests set ACHS to pose as a player.
let ACHS = {};
const sandbox = { console, window: { __fishGetUserAchievements: () => ACHS } };
vm.createContext(sandbox);
vm.runInContext(code + "\nthis.API = { REEARN_GATED_TYPES, tradedAwayEntry, reEarnState, LEVEL_XP_TOTALS, ANIMAL_AVATARS };",
                sandbox);
const { REEARN_GATED_TYPES, tradedAwayEntry, reEarnState, LEVEL_XP_TOTALS, ANIMAL_AVATARS } = sandbox.API;

// ── fixtures: the real catalogue entries these rules run against ────────────
const PUFFIN = { id: "horned-puffin", img: "/avatars/horned-puffin.png",
  unlock: { type: "stat", stat: "lifetime_play_again", goal: 75, unit: "Play Again abilities" } };
const BUNKER = { id: "bunker", img: "/avatars/bunker.png",
  unlock: { type: "comp_wins", goal: 20 } };
const MAHI   = { id: "mahi-mahi", img: "/avatars/mahi-mahi.png",
  unlock: { type: "level", goal: 30 } };
const SEASTAR = { id: "sea-star", img: "/avatars/sea-star.png",
  unlock: { type: "level", goal: 100 } };
const BARRACUDA = { id: "barracuda", img: "/avatars/barracuda.png",
  unlock: { type: "rank", tier: "bronze" } };
const NARWHAL = { id: "narwhal", img: "/avatars/narwhal.png",
  unlock: { type: "achievement", achId: "narwhal_mammal_wins", goal: 10 } };

const away = (item, stats, extra) =>
  Object.assign({ item, stats, total_xp: (stats || {}).total_xp || 0, rank: "" }, extra || {});

section("gated types (repeatable requirements must NOT be gated):");
["stat", "comp_wins", "level", "rank", "achievement"].forEach(t =>
  check(`${t} is gated`, REEARN_GATED_TYPES.has(t)));
["event", "secret", "code", "shop", "starter"].forEach(t =>
  check(`${t} passes through`, !REEARN_GATED_TYPES.has(t)));

section("tradedAwayEntry:");
const profile = { traded_away: [away("/avatars/bunker.png", { lifetime_comp_wins: 25 }),
                                away("/avatars/horned-puffin.png", { lifetime_play_again: 91 })] };
check("finds the entry for an item",
      tradedAwayEntry(profile, "/avatars/horned-puffin.png").stats.lifetime_play_again === 91);
check("ignores a query string", !!tradedAwayEntry(profile, "/avatars/bunker.png?v=ws15"));
check("null for an item never traded", tradedAwayEntry(profile, "/avatars/lobster.png") === null);
check("null when the account has no history", tradedAwayEntry({}, "/avatars/bunker.png") === null);
check("survives junk entries",
      tradedAwayEntry({ traded_away: [null, "x", 7, away("/avatars/bunker.png", {})] },
                      "/avatars/bunker.png") !== null);
check("takes the MOST RECENT snapshot for an item",
      tradedAwayEntry({ traded_away: [away("/avatars/bunker.png", { lifetime_comp_wins: 20 }),
                                      away("/avatars/bunker.png", { lifetime_comp_wins: 55 })] },
                      "/avatars/bunker.png").stats.lifetime_comp_wins === 55);

section("lifetime-counter unlocks (the Horned Puffin bug):");
// Traded at 91 Play Agains → needs 91 + 75 = 166, not the 91 it already has.
const base91 = away("/avatars/horned-puffin.png", { lifetime_play_again: 91 });
let r = reEarnState(PUFFIN, base91, { lifetime_play_again: 91 }, 12);
check("banked progress does NOT re-unlock it", r.met === false);
check("shows progress toward the new goal", r.text === "91 / 166 Play Again abilities");
check("progress fraction is partial", r.prog > 0.5 && r.prog < 1);
check("still blocked one short",
      reEarnState(PUFFIN, base91, { lifetime_play_again: 165 }, 12).met === false);
check("unlocks after another full 75",
      reEarnState(PUFFIN, base91, { lifetime_play_again: 166 }, 12).met === true);
check("counts as repeatable", r.repeatable === true);
check("a zero baseline just needs the original goal",
      reEarnState(PUFFIN, away("/avatars/horned-puffin.png", {}), { lifetime_play_again: 75 }, 1).met === true);
check("progress text never overshoots the goal",
      reEarnState(PUFFIN, base91, { lifetime_play_again: 9999 }, 12).text === "166 / 166 Play Again abilities");

section("competitive-win unlocks:");
const baseWins = away("/avatars/bunker.png", { lifetime_comp_wins: 25 });
check("25 wins at trade time → needs 45",
      reEarnState(BUNKER, baseWins, { lifetime_comp_wins: 44 }, 5).met === false
      && reEarnState(BUNKER, baseWins, { lifetime_comp_wins: 45 }, 5).met === true);
check("labelled with the unit",
      reEarnState(BUNKER, baseWins, { lifetime_comp_wins: 30 }, 5).text === "30 / 45 Competitive wins");

section("level unlocks (re-earn costs the XP that milestone cost):");
// Level 30 costs LEVEL_XP_TOTALS[29] XP from scratch; traded at 60,450 XP.
const lvl30Cost = LEVEL_XP_TOTALS[29];
const baseXp = away("/avatars/mahi-mahi.png", { total_xp: 60450 }, { total_xp: 60450 });
check("being past the level does not re-unlock it",
      reEarnState(MAHI, baseXp, { total_xp: 60450 }, 31).met === false);
check("unlocks after earning that milestone's XP again",
      reEarnState(MAHI, baseXp, { total_xp: 60450 + lvl30Cost }, 55).met === true);
check("one XP short is still locked",
      reEarnState(MAHI, baseXp, { total_xp: 60450 + lvl30Cost - 1 }, 55).met === false);
check("a max-level avatar is still re-earnable (no level-cap dead end)",
      reEarnState(SEASTAR, away("/avatars/sea-star.png", { total_xp: LEVEL_XP_TOTALS[99] },
                                { total_xp: LEVEL_XP_TOTALS[99] }),
                  { total_xp: LEVEL_XP_TOTALS[99] * 2 }, 100).met === true);
check("XP progress is shown, not a level number",
      reEarnState(MAHI, baseXp, { total_xp: 60450 }, 31).text.endsWith(" XP"));

section("rank unlocks (fall below the tier, then climb back):");
const baseRank = away("/avatars/barracuda.png", {}, { rank: "Gold Grouper" });
check("still Gold → not re-earned (never left the tier)",
      reEarnState(BARRACUDA, baseRank, { rank_competitive: "Gold Grouper" }, 40).met === false);
check("sitting above it is not enough, however high",
      reEarnState(BARRACUDA, baseRank, { rank_competitive: "Diamond Dolphin" }, 40).met === false);
check("dropping below Bronze alone doesn't grant it either",
      reEarnState(BARRACUDA, { ...baseRank, dipped: true },
                  { rank_competitive: "Unranked" }, 40).met === false);
check("dipped, then back at the tier → re-earned",
      reEarnState(BARRACUDA, { ...baseRank, dipped: true },
                  { rank_competitive: "Bronze Barracuda" }, 40).met === true);
check("a season reset (Unranked) counts as the dip",
      reEarnState(BARRACUDA, { ...baseRank, dipped: true },
                  { rank_competitive: "Gold Grouper" }, 40).met === true);
check("already below the tier when traded → just climb back",
      reEarnState(BARRACUDA, away("/avatars/barracuda.png", {}, { rank: "" }),
                  { rank_competitive: "Bronze Barracuda" }, 9).met === true);
const kingBase = reEarnState(BARRACUDA, away("/avatars/barracuda.png", {}, { rank: "King of the Critters" }),
                             { rank_competitive: "King of the Critters" }, 90);
check("top rank is still re-earnable, never trade-only",
      kingBase.repeatable === true && !/trade/i.test(kingBase.text));

section("achievement unlocks (the meter keeps counting, so do it again):");
ACHS = { narwhal_mammal_wins: { completed: true, progress: 14, goal: 10 } };
const narBase = away("/avatars/narwhal.png", {}, { achievements: { narwhal_mammal_wins: 14 } });
check("being completed does NOT re-grant it",
      reEarnState(NARWHAL, narBase, {}, 50).met === false);
check("needs another 10 mammal wins on top of the 14 it had",
      reEarnState(NARWHAL, narBase, {}, 50).text === "14 / 24 qualifying wins");
ACHS = { narwhal_mammal_wins: { completed: true, progress: 24, goal: 10 } };
check("re-earned once the meter reaches it", reEarnState(NARWHAL, narBase, {}, 50).met === true);
check("it is repeatable, never trade-only",
      reEarnState(NARWHAL, narBase, {}, 50).repeatable === true);

const SQUID_ACH = { id:"sea-anemone", img:"/avatars/sea-anemone.png",
  unlock: { type:"achievement", achId:"it_is_finally_over" } };
ACHS = { it_is_finally_over: { completed: true } };
const oneShotBase = away("/avatars/sea-anemone.png", {}, { achievements: {} });
check("a one-off feat shows no bar, just 'do it once more'",
      reEarnState(SQUID_ACH, oneShotBase, {}, 5).prog === null
      && /once more/i.test(reEarnState(SQUID_ACH, oneShotBase, {}, 5).text));
check("completed-but-not-repeated → still locked",
      reEarnState(SQUID_ACH, oneShotBase, {}, 5).met === false);
ACHS = { it_is_finally_over: { completed: true, progress: 1 } };
check("performing it once more → re-earned",
      reEarnState(SQUID_ACH, oneShotBase, {}, 5).met === true);
ACHS = {};

section("the gate is actually wired into the one grant choke point:");
{
  const grantSrc = slice("window.__fishGrantUnlockedIcon = async (iconPath) => {",
                         "// Equip an avatar the player has unlocked");
  check("__fishGrantUnlockedIcon consults the re-earn gate",
        /if \(!_reEarnAllowsGrant\(path\)\) return false;/.test(grantSrc));
  check("it does so BEFORE writing to Firestore",
        grantSrc.indexOf("_reEarnAllowsGrant") < grantSrc.indexOf("arrayUnion"));
  check("_reEarnAllowsGrant is defined", /function _reEarnAllowsGrant\(path\) \{/.test(SRC));
  check("read-only (viewing someone else's collection) still blocks grants first",
        grantSrc.indexOf("_galReadOnly") < grantSrc.indexOf("_reEarnAllowsGrant"));
}

section("robustness:");
check("a snapshot with no stats blob does not throw or auto-grant",
      reEarnState(PUFFIN, { item: PUFFIN.img }, { lifetime_play_again: 74 }, 3).met === false);
check("a snapshot with no stats blob still unlocks at the goal",
      reEarnState(PUFFIN, { item: PUFFIN.img }, { lifetime_play_again: 75 }, 3).met === true);
check("missing live stats are treated as zero",
      reEarnState(PUFFIN, base91, undefined, 1).met === false);
check("an unknown unlock type never auto-grants",
      reEarnState({ img: "/avatars/x.png", unlock: { type: "mystery" } },
                  away("/avatars/x.png", {}), {}, 1).met === false);
check("no rule anywhere leaves an avatar permanently trade-only",
      [[PUFFIN, base91], [BUNKER, baseWins], [MAHI, baseXp],
       [BARRACUDA, kingBase && baseRank], [NARWHAL, narBase]]
        .every(([av, b]) => reEarnState(av, b, {}, 1).repeatable === true));

section("the whole shipped catalogue, avatar by avatar:");
{
  // The product rule: trading an item away means doing its unlock requirement
  // AGAIN, never "this is gone unless someone trades it to you". Every gated
  // avatar in the real registry must therefore report a repeatable re-earn.
  const gated = ANIMAL_AVATARS.filter(a => a.unlock && REEARN_GATED_TYPES.has(a.unlock.type));
  check(`every gated avatar is re-earnable (${gated.length} checked)`,
        gated.every(a => reEarnState(a, away(a.img, {}, { achievements: {} }), {}, 1).repeatable === true),
        gated.filter(a => !reEarnState(a, away(a.img, {}, { achievements: {} }), {}, 1).repeatable)
             .map(a => a.id).join(", "));
  check("every gated avatar starts LOCKED right after the trade",
        gated.every(a => {
          // Pose as a player who fully satisfies the original requirement.
          ACHS = Object.fromEntries(ANIMAL_AVATARS
            .filter(x => x.unlock?.achId)
            .map(x => [x.unlock.achId, { completed: true, progress: 99 }]));
          const stats = { lifetime_play_again: 9e5, lifetime_deck_draws: 9e5,
                          lifetime_baitfish_played: 9e5, lifetime_comp_wins: 9e5,
                          total_xp: LEVEL_XP_TOTALS[99], rank_competitive: "King of the Critters" };
          const base = { item: a.img, stats: { ...stats }, total_xp: stats.total_xp,
                         rank: stats.rank_competitive,
                         achievements: Object.fromEntries(Object.entries(ACHS).map(([k, v]) => [k, v.progress])) };
          return reEarnState(a, base, stats, 100).met === false;
        }));
  ACHS = {};
  const achAvatars = ANIMAL_AVATARS.filter(a => a.unlock?.type === "achievement");
  check("every achievement avatar names the achievement it is tied to",
        achAvatars.length > 0 && achAvatars.every(a => !!a.unlock.achId));
  check("multi-step achievement avatars declare their goal (so 'again' is the full run)",
        achAvatars.filter(a => a.unlock.achId === "narwhal_mammal_wins")
                  .every(a => a.unlock.goal === 10));
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
