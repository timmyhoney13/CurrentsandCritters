#!/usr/bin/env node
/**
 * apply_history_learning.js — Force the AI to learn from all saved games.
 *
 * WHAT IT DOES:
 *   Calls /api/admin/learn_from_history on the Render server, which:
 *   1. Reads every game-history JSON on the server's persistent disk
 *   2. Reconstructs each game's board layout into a synthetic GameState
 *   3. Runs the full AI learning pipeline:
 *        update_brain_from_match    — card synergies, winner vs loser patterns
 *        update_strategy_memory     — strategy-move sequences
 *        reinforce_human_demo_from_board — board-card synergy reinforcement
 *   4. Games from TheFishManTim (and any priority player) get 5× boost
 *   5. High-scoring leaderboard-quality games (optional min_score filter)
 *   6. Saves the updated brain (fish_ai_brain.json) on the server
 *
 * USAGE:
 *   # Learn from ALL saved games (no score filter):
 *   node apply_history_learning.js
 *
 *   # Only learn from high-scoring games (winner scored 80+):
 *   node apply_history_learning.js --min-score 80
 *
 *   # Boost a different priority player instead of TheFishManTim:
 *   node apply_history_learning.js --priority "YourNickname"
 *
 * SETUP:
 *   Set environment variables (or edit CONFIG below):
 *     RENDER_SERVER_URL=https://play.currentsandcritters.com
 *     ADMIN_RECOVERY_KEY=<your key from Render env vars>
 *
 * NOTE: This call may take 10-30 seconds if there are many game files.
 *       The server processes them synchronously in the request handler.
 */

"use strict";

const https = require("https");
const http  = require("http");

// ── CONFIG ────────────────────────────────────────────────────────────────
const RENDER_URL  = (process.env.RENDER_SERVER_URL
  || "https://play.currentsandcritters.com").replace(/\/$/, "");
const ADMIN_KEY   = process.env.ADMIN_RECOVERY_KEY || "";
// ──────────────────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
let priorityNick = "TheFishManTim";
let minScore = 0;

for (let i = 0; i < args.length; i++) {
  if (args[i] === "--priority" && args[i + 1]) priorityNick = args[++i];
  if (args[i] === "--min-score" && args[i + 1]) minScore = parseInt(args[++i], 10) || 0;
}

function fetch(url) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith("https") ? https : http;
    mod.get(url, (res) => {
      let body = "";
      res.on("data", d => body += d);
      res.on("end", () => {
        try { resolve(JSON.parse(body)); }
        catch (e) { reject(new Error("JSON parse error: " + body.slice(0, 300))); }
      });
    }).on("error", reject);
  });
}

(async () => {
  if (!ADMIN_KEY) {
    console.error("❌  ADMIN_RECOVERY_KEY env var is not set.");
    console.error("   export ADMIN_RECOVERY_KEY=<your key from Render>");
    process.exit(1);
  }

  const qs = new URLSearchParams({
    admin_key:     ADMIN_KEY,
    priority_nick: priorityNick,
    min_score:     String(minScore),
  });
  const url = `${RENDER_URL}/api/admin/learn_from_history?${qs}`;

  console.log(`🧠  Triggering AI history learning on Render server…`);
  console.log(`   Priority player: ${priorityNick} (5× boost)`);
  if (minScore > 0) console.log(`   Min winner score: ${minScore}+`);
  console.log(`   URL: ${url.replace(ADMIN_KEY, "***")}\n`);
  console.log("   (This may take 10–30 seconds for large game histories…)\n");

  let result;
  try {
    result = await fetch(url);
  } catch (e) {
    console.error("❌  Network error:", e.message);
    process.exit(1);
  }

  if (!result.ok) {
    console.error("❌  Server returned error:", result.error);
    process.exit(1);
  }

  console.log("✅  Learning pass complete!\n");
  console.log(`   Games processed:  ${result.processed}`);
  console.log(`   Priority-boosted: ${result.boosted}  (games containing "${priorityNick}")`);
  console.log(`   Skipped:          ${result.skipped}  (truncated / low-score / empty)`);
  console.log(`   Errors:           ${result.errors}`);
  console.log("\n   The AI brain (fish_ai_brain.json) has been updated on the server.");
  console.log("   Bots in new games will reflect the updated synergies.");

  process.exit(0);
})();
