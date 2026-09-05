#!/usr/bin/env node
/* The Current Controller: the table's permission, and what it costs.
 *
 * Run:  node test_current_controller.js
 *
 * The Controller is the mod menu. It can read every hand, deal cards and drive
 * the bots, so two things have to be true and neither is visible by reading the
 * feature's own code in isolation:
 *
 *   1. NOBODY GETS IT WITHOUT THE TABLE. Permission is a unanimous lobby vote
 *      the SERVER counts, and the server authorizes mod ops from its own record
 *      of that vote, never from anything the browser claims about itself.
 *   2. A MODDED GAME DOES NOT COUNT. No leaderboard stat, no achievement, no
 *      critter, no match-history entry. Everyone still earns the base XP for
 *      finishing, because they did finish.
 *
 * Rule 2 is the one that rots quietly: it is enforced by an ALLOWLIST of the
 * stat keys a modded game may still write, so a stat added next year is safe by
 * default. This file lifts that allowlist and the real prune helpers out of
 * preview-app.js and runs them against the real update dictionary shape.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = __dirname;
const APP = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const CCJS = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/current-controller.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const SERVER = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");

let failures = 0, checks = 0;
function check(cond, label) {
  checks++;
  if (!cond) { failures++; console.log("  ✗ " + label); }
  else console.log("  ✓ " + label);
}

/* ── lift the real rules out of preview-app.js ─────────────────────────── */
function grabBlock(startsWith, endsWith) {
  const a = APP.indexOf(startsWith);
  if (a < 0) throw new Error("not found: " + startsWith);
  const b = APP.indexOf(endsWith, a);
  if (b < 0) throw new Error("unterminated: " + startsWith);
  return APP.slice(a, b + endsWith.length);
}
function grabFn(name, indent) {
  const pad = " ".repeat(indent);
  const start = APP.indexOf(`\n${pad}function ${name}(`);
  if (start < 0) throw new Error(`function ${name}() not found at indent ${indent}`);
  let d = 0;
  for (let j = APP.indexOf("{", start); j < APP.length; j++) {
    const c = APP[j];
    if (c === "{") d++;
    else if (c === "}") { d--; if (d === 0) return APP.slice(start, j + 1); }
  }
  throw new Error("unbalanced braces reading " + name);
}

const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext([
  grabBlock("const CC_MODDED_GAME_XP =", "// the same as a last-place finish"),
  grabBlock("const CC_MODDED_KEEP = new Set([", "]);"),
  grabFn("ccKeepModdedGuestStats", 2),
  grabFn("ccPruneModdedUpdates", 2),
  // `const` at the top level of a vm script is NOT a property of the context
  // object, so the values have to be handed over explicitly or every read of
  // sandbox.CC_* comes back undefined and the file "passes" by testing nothing.
  "this.CC_MODDED_GAME_XP = CC_MODDED_GAME_XP;",
  "this.CC_MODDED_KEEP = CC_MODDED_KEEP;",
  "this.ccKeepModdedGuestStats = ccKeepModdedGuestStats;",
  "this.ccPruneModdedUpdates = ccPruneModdedUpdates;",
].join("\n"), sandbox);

/* Every stat key the real saveGameStats writes for a finished game. Taken from
   the source itself, so a key added later shows up here without an edit. */
const WRITTEN_KEYS = (() => {
  const body = APP.slice(APP.indexOf("async function saveGameStats("),
                         APP.indexOf("} else if (isGuestSession) {"));
  const keys = new Set();
  for (const m of body.matchAll(/updates\[`?"?(stats\.[A-Za-z0-9_.$}{`\\ +]+)"?`?\]/g)) keys.add(m[1]);
  for (const m of body.matchAll(/"(stats\.[a-z_0-9.]+)":/g)) keys.add(m[1]);
  return [...keys];
})();

console.log("\nthe allowlist is what decides, and it is small");
check(sandbox.CC_MODDED_GAME_XP === 25, "a modded game pays the last-place XP (25)");
check(sandbox.CC_MODDED_KEEP.size === 12,
      `the allowlist holds only XP, level and streak keys (${sandbox.CC_MODDED_KEEP.size})`);
for (const k of sandbox.CC_MODDED_KEEP) {
  check(/^stats\.(total_xp|level|player_level|level_title|xp_current|level_xp_current|xp_goal|level_xp_goal|last_game_xp|streak_days|daily_streak|streak_longest)$/.test(k),
        `allowlisted: ${k}`);
}

console.log("\nnothing that feeds a leaderboard survives a modded game");
const LEADERBOARD_KEYS = [
  "stats.highest_score", "stats.highest_score_normal", "stats.best_game",
  "stats.normal_wins", "stats.balanced_avg_score", "stats.balanced_score_sum",
  "stats.balanced_games", "stats.completed_games", "stats.total_score",
  "stats.hosted_normal_games", "stats.lifetime_deck_draws", "stats.recent_games",
];
const fullUpdate = {};
for (const k of [...LEADERBOARD_KEYS, ...sandbox.CC_MODDED_KEEP]) fullUpdate[k] = 1;
const pruned = sandbox.ccPruneModdedUpdates(fullUpdate);
for (const k of LEADERBOARD_KEYS) {
  check(!(k in pruned), `dropped: ${k}`);
}
for (const k of sandbox.CC_MODDED_KEEP) check(k in pruned, `kept: ${k}`);

console.log("\n…and that holds for every stat the real save writes");
const leaked = WRITTEN_KEYS.filter(k => (k in sandbox.ccPruneModdedUpdates(
  Object.fromEntries(WRITTEN_KEYS.map(x => [x, 1])))) && !sandbox.CC_MODDED_KEEP.has(k));
check(WRITTEN_KEYS.length > 15, `read ${WRITTEN_KEYS.length} written stat keys out of the real save`);
check(leaked.length === 0, "no stat the save writes escapes the allowlist: " + leaked.join(","));

console.log("\na guest is held to the same rule");
const before = { total_xp: 100, highest_score: 900, normal_wins: 4, completed_games: 10 };
const after  = { total_xp: 125, highest_score: 4000, normal_wins: 5, completed_games: 11,
                 daily_streak: 3, streak_days: ["2026-09-05"], streak_longest: 3 };
const kept = sandbox.ccKeepModdedGuestStats(before, after);
check(kept.total_xp === 125, "the guest keeps the XP they earned");
check(kept.daily_streak === 3 && kept.streak_longest === 3, "…and the streak day");
check(kept.highest_score === 900, "…but their high score is untouched");
check(kept.normal_wins === 4, "…and the win does not count");
check(kept.completed_games === 10, "…and neither does the game");

console.log("\nthe browser never decides who may mod");
check(/def _cc_may_mod_locked/.test(SERVER), "the server owns the may-I-mod answer");
check(/table_armed = room\._cc_may_mod_locked\(seat\)/.test(SERVER),
      "the admin_mod route asks the ROOM, not the request body");
check(!/supporter_tier/.test(SERVER.slice(SERVER.indexOf("def _cc_may_mod_locked"),
                                          SERVER.indexOf("def _cc_may_mod_locked") + 800)),
      "…and it never reads a tier the client could claim");
check(/if not table_armed:/.test(SERVER),
      "the admin key is still required when no table armed it");
check(/"modded": bool\(self\._admin_active\)/.test(SERVER),
      "the saved game record remembers it was modded");

console.log("\npermission is per game, and casual only");
const room = SERVER.slice(SERVER.indexOf("def _cc_casual_locked"), SERVER.indexOf("def _cc_eligible_voter_indices_locked"));
check(/self\.competitive or self\.ranked/.test(room), "competitive and ranked rooms never offer it");
check(/tournament_id/.test(room), "…nor a tournament match");
check(/self\.cc_armed = False/.test(SERVER.slice(SERVER.indexOf("self.kick_votes = {}\n        self._kicked_policies = {}"))),
      "a rematch asks the table again instead of inheriting a yes");

console.log("\nthe lobby row, where the emotes were");
check(/id="wr-cc-row"/.test(HTML), "the row exists in the lobby markup");
check(HTML.indexOf('id="wr-cc-row"') < HTML.indexOf('id="wr-emote-row"'),
      "…and sits where the emotes are");
check(/\.wr-cc-row \{/.test(CSS) && /\.wr-cc-ask/.test(CSS) && /\.wr-cc-chip/.test(CSS),
      "preview.css styles the row, the ask and the chip");
const lobby = grabFn("_ccLobbyRender", 2);
check(/emotes\.hidden = true/.test(lobby), "it hides the emotes while it has something to say");
check(/cc\.armed/.test(lobby) && /cc\.denied/.test(lobby) && /cc\.can_vote/.test(lobby),
      "it paints all three states the server can be in");
check(/will not count/.test(lobby), "…and says out loud that the game will not count");
check(/every hand/.test(lobby), "…and what the voter is agreeing to");

console.log("\nthe panel opens for a voted table without any key");
check(/function tableArmed\(\)/.test(CCJS), "current-controller.js knows what an armed table is");
check(/cc\.armed && cc\.is_mine/.test(CCJS), "…and only for the seat that asked");
check(/if \(!tableArmed\(\) && !ccAdminKey\(\)\)/.test(CCJS),
      "a Supporter is never asked for the developer's admin key");
check(/if \(!key && !armed\)/.test(CCJS), "…and a keyless mod call is allowed only when armed");
check(/const admin = mayControl\(\);/.test(CCJS), "the menu item appears for both ways in");

console.log("\nHow to Play tells a player what they are agreeing to");
const htp = grabFn("_htpControllerHtml", 4);
check(/id="htp-tab-controller"/.test(HTML) && /id="htp-pane-controller"/.test(HTML),
      "the tab and its pane exist");
check(/data-htp="controller"/.test(HTML), "…and the tab is wired to the pane");
check(/window\.CC_CONTROLLER_TOOLS/.test(CCJS),
      "current-controller.js publishes its tool list");
check(/window\.CC_CONTROLLER_TOOLS/.test(htp),
      "…and the tab lists THOSE tools rather than keeping a second copy");
check(/mod menu/.test(htp), "it says plainly what a mod menu is");
check(/Tsunami/.test(htp), "…which tier it comes with");
check(/every other human/.test(htp) || /every other player/.test(htp),
      "…that everyone has to agree");
check(/casual/.test(htp), "…that it is casual games only");
for (const cost of ["leaderboard", "achievements", "critters", "base XP"]) {
  check(htp.includes(cost), `…and what it costs: ${cost}`);
}
check(/costs nothing/.test(htp),
      "…and that being allowed and never using it costs nothing");
// Checked against the HTML the function actually PRODUCES, not its source: a
// <ul> inside a <p> is auto-closed by the browser, which drops every following
// line out of the styled block (it renders washed out), and reading the source
// cannot tell an open paragraph from one that was closed two lines earlier.
const tabBox = { console, window: {},
  escapeHtml: (v) => String(v).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])) };
vm.createContext(tabBox);
const TOOLS_SRC = CCJS.slice(CCJS.indexOf("const TOOLS = ["),
                             CCJS.indexOf("];", CCJS.indexOf("const TOOLS = [")) + 2);
vm.runInContext([
  TOOLS_SRC,
  "window.CC_CONTROLLER_TOOLS = TOOLS.map(t => ({id:t.id, icon:t.icon, name:t.name, desc:t.desc}));",
  htp,
  "this.html = _htpControllerHtml();",
].join("\n"), tabBox);
const HTMLOUT = tabBox.html;
check(typeof HTMLOUT === "string" && HTMLOUT.length > 800, "the tab renders real HTML");
check(!/<p[^>]*>(?:(?!<\/p>)[\s\S])*?<ul/.test(HTMLOUT),
      "no list is nested inside a paragraph");
const opens = (HTMLOUT.match(/<p[ >]/g) || []).length;
const closes = (HTMLOUT.match(/<\/p>/g) || []).length;
check(opens === closes, `every paragraph is closed (${opens} open, ${closes} closed)`);
const toolCount = (tabBox.window.CC_CONTROLLER_TOOLS || []).length;
check(toolCount >= 8 && (HTMLOUT.match(/htp-key-btn/g) || []).length === toolCount,
      `every one of the panel's ${toolCount} tools is listed`);

console.log("\ncurrent-controller checks: ${checks}"
  .replace("${checks}", String(checks)));
if (failures) { console.log(`${failures} FAILED`); process.exit(1); }
console.log("current controller OK");
