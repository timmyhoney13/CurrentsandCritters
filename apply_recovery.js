#!/usr/bin/env node
/**
 * apply_recovery.js, Currents & Critters stat recovery tool
 *
 * WHAT IT DOES:
 *   1. Reads per-player stats rebuilt from server game-history files
 *      (fetched from /api/admin/recovery_export on your Render server)
 *   2. Scans your Firestore "users" collection for accounts where the
 *      "stats" sub-document looks zeroed (completed_games == 0 or missing)
 *      but the account has a nickname, a sign the loadAndRenderStats bug hit
 *   3. For every affected account, writes back the stats we can rebuild
 *      from server game history ONLY IF those stats are better than what's
 *      currently in Firestore (never downgrades real data)
 *
 * WHAT IT CANNOT RECOVER:
 *   - XP / level / xp_current / xp_goal (calculated per-game, not logged)
 *   - Casual game recent_games detail beyond what the server saved
 *   - Achievements, unlocked_icons (separate Firestore fields, likely intact)
 *   - Friends lists, avatar, nickname (not in stats, already intact)
 *
 * REQUIREMENTS:
 *   npm install firebase-admin node-fetch
 *
 * USAGE:
 *   # Dry run (shows what would change, writes nothing):
 *   node apply_recovery.js --dry-run
 *
 *   # Apply recovery for all affected accounts:
 *   node apply_recovery.js --apply
 *
 *   # Apply recovery for one specific nickname:
 *   node apply_recovery.js --apply --nick "TheFishManTim"
 *
 * SETUP:
 *   1. Go to Firebase Console → Project Settings → Service Accounts
 *   2. Click "Generate new private key" → save as serviceAccountKey.json
 *      in this directory (or set SERVICE_ACCOUNT_PATH env var)
 *   3. Set environment variables (or edit the CONFIG section below):
 *      RENDER_SERVER_URL=https://play.currentsandcritters.com
 *      ADMIN_RECOVERY_KEY=<your ADMIN_RECOVERY_KEY from Render env vars>
 */

"use strict";

const admin  = require("firebase-admin");
const https  = require("https");
const http   = require("http");
const path   = require("path");
const fs     = require("fs");

// ── CONFIG (edit or set via env vars) ─────────────────────────────────────
const SERVICE_ACCOUNT_PATH = process.env.SERVICE_ACCOUNT_PATH
  || path.join(__dirname, "serviceAccountKey.json");
const RENDER_URL = (process.env.RENDER_SERVER_URL
  || "https://play.currentsandcritters.com").replace(/\/$/, "");
const ADMIN_KEY  = process.env.ADMIN_RECOVERY_KEY || "";
// ──────────────────────────────────────────────────────────────────────────

const args      = process.argv.slice(2);
const DRY_RUN   = args.includes("--dry-run") || !args.includes("--apply");
const NICK_ONLY = (() => { const i = args.indexOf("--nick"); return i >= 0 ? args[i+1] : null; })();

if (DRY_RUN) console.log("🔍  DRY RUN, no writes will happen. Pass --apply to write.\n");
if (NICK_ONLY) console.log(`🎯  Targeting single account: "${NICK_ONLY}"\n`);

// ── Firebase init ──────────────────────────────────────────────────────────
if (!fs.existsSync(SERVICE_ACCOUNT_PATH)) {
  console.error(`❌  Service account key not found at: ${SERVICE_ACCOUNT_PATH}`);
  console.error("   Get one from Firebase Console → Project Settings → Service Accounts");
  process.exit(1);
}
const serviceAccount = require(SERVICE_ACCOUNT_PATH);
admin.initializeApp({ credential: admin.credential.cert(serviceAccount) });
const db = admin.firestore();

// ── Helpers ────────────────────────────────────────────────────────────────
function fetch(url) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith("https") ? https : http;
    mod.get(url, (res) => {
      let body = "";
      res.on("data", d => body += d);
      res.on("end", () => {
        try { resolve(JSON.parse(body)); }
        catch(e) { reject(new Error("JSON parse error: " + body.slice(0, 200))); }
      });
    }).on("error", reject);
  });
}

// ── Main ───────────────────────────────────────────────────────────────────
(async () => {
  if (!ADMIN_KEY) {
    console.error("❌  ADMIN_RECOVERY_KEY env var is not set.");
    console.error("   Set it to the same value as ADMIN_RECOVERY_KEY on Render.");
    process.exit(1);
  }

  // 1. Fetch rebuilt stats from server ───────────────────────────────────
  const exportUrl = `${RENDER_URL}/api/admin/recovery_export?admin_key=${encodeURIComponent(ADMIN_KEY)}`;
  console.log("⬇️   Fetching recovery data from server…");
  let exportData;
  try {
    exportData = await fetch(exportUrl);
  } catch(e) {
    console.error("❌  Could not fetch recovery data:", e.message);
    process.exit(1);
  }
  if (!exportData.ok) {
    console.error("❌  Server returned error:", exportData.error);
    process.exit(1);
  }
  const serverStats = exportData.players || {};
  console.log(`✅  Got server data for ${exportData.count} players.\n`);

  // 2. Scan Firestore for affected accounts ──────────────────────────────
  console.log("🔎  Scanning Firestore users collection…");
  const snapshot = await db.collection("users").get();
  const allUsers = snapshot.docs.map(d => ({ id: d.id, ...d.data() }));
  console.log(`   Found ${allUsers.length} total accounts.\n`);

  // An account is "affected" if it has a nickname but zeroed stats
  const affected = allUsers.filter(u => {
    const nick = String(u.nickname || "").trim();
    if (!nick) return false;
    if (NICK_ONLY && nick.toLowerCase() !== NICK_ONLY.toLowerCase()) return false;
    const s = u.stats || {};
    // Zeroed or missing stats = likely hit by the bug
    const gamesPlayed = Number(s.completed_games || 0);
    const hasEmail = !!u.email;
    const hasCreatedAt = !!u.created_at;
    return gamesPlayed === 0 && (hasEmail || hasCreatedAt);
  });

  console.log(`🎯  ${affected.length} accounts look affected (nickname + email/created_at but 0 games).\n`);

  let recovered = 0, skipped = 0, notFound = 0;

  for (const user of affected) {
    const nick = String(user.nickname || "").trim();
    const uid  = user.id;

    // Find matching server stats by nickname (case-insensitive)
    const serverEntry = Object.values(serverStats).find(
      p => String(p.nickname || "").toLowerCase() === nick.toLowerCase()
    );

    if (!serverEntry || serverEntry.completed_games === 0) {
      console.log(`  ⚠️  ${nick}, no game history found on server (may be truly new or name mismatch)`);
      notFound++;
      continue;
    }

    const current = user.stats || {};
    const rebuilt = {
      completed_games:           Math.max(Number(current.completed_games   || 0), serverEntry.completed_games),
      normal_wins:               Math.max(Number(current.normal_wins        || 0), serverEntry.normal_wins),
      competitive_wins:          Math.max(Number(current.competitive_wins   || 0), serverEntry.competitive_wins),
      total_score:               Math.max(Number(current.total_score        || 0), serverEntry.total_score),
      highest_score:             Math.max(Number(current.highest_score      || 0), serverEntry.highest_score),
      highest_score_normal:      Math.max(Number(current.highest_score_normal || 0), serverEntry.highest_score_normal),
      highest_score_competitive: Math.max(Number(current.highest_score_competitive || 0), serverEntry.highest_score_competitive),
      comp_cp:                   Math.max(Number(current.comp_cp            || 0), serverEntry.comp_cp),
      comp_wins:                 Math.max(Number(current.comp_wins          || 0), serverEntry.comp_wins),
      comp_losses:               Math.max(Number(current.comp_losses        || 0), serverEntry.comp_losses),
      comp_draws:                Math.max(Number(current.comp_draws         || 0), serverEntry.comp_draws),
      // Preserve keys that can't be rebuilt
      total_xp:      current.total_xp      || 0,
      level:         current.level         || 1,
      player_level:  current.player_level  || 1,
      level_title:   current.level_title   || "Ocean Explorer",
      xp_current:    current.xp_current    || 0,
      level_xp_current: current.level_xp_current || 0,
      xp_goal:       current.xp_goal       || 50,
      level_xp_goal: current.level_xp_goal || 50,
      last_game_xp:  current.last_game_xp  || 0,
      // Restore normal_games_by_size and comp_games_by_size (merge)
      normal_games_by_size: Object.assign({}, current.normal_games_by_size || {}, serverEntry.normal_games_by_size || {}),
      comp_games_by_size:   Object.assign({}, current.comp_games_by_size   || {}, serverEntry.comp_games_by_size   || {}),
      // recent_games: use server data if current is empty
      recent_games: (current.recent_games && current.recent_games.length > 0)
        ? current.recent_games
        : (serverEntry.recent_games || []),
    };

    const anyChange = JSON.stringify(current) !== JSON.stringify(rebuilt);
    if (!anyChange) {
      skipped++;
      continue;
    }

    console.log(`  🔧  ${nick} (uid: ${uid})`);
    console.log(`       games: ${current.completed_games || 0} → ${rebuilt.completed_games}`);
    console.log(`       wins:  ${current.normal_wins || 0} → ${rebuilt.normal_wins}  |  comp wins: ${current.competitive_wins || 0} → ${rebuilt.competitive_wins}`);
    console.log(`       best:  ${current.highest_score || 0} → ${rebuilt.highest_score}  |  OP: ${current.comp_cp || 0} → ${rebuilt.comp_cp}`);

    if (!DRY_RUN) {
      await db.collection("users").doc(uid).set({ stats: rebuilt }, { merge: true });
    }
    recovered++;
  }

  // 3. Summary ────────────────────────────────────────────────────────────
  console.log("\n═══════════════════════════════════════");
  console.log(`  Affected accounts found:  ${affected.length}`);
  console.log(`  Recovered (stats rebuilt): ${recovered}`);
  console.log(`  Already had correct data:  ${skipped}`);
  console.log(`  No server history found:   ${notFound}`);
  if (DRY_RUN) {
    console.log("\n  ⚠️  DRY RUN, nothing was written. Run with --apply to apply.");
  } else {
    console.log("\n  ✅  Recovery complete.");
  }
  console.log("═══════════════════════════════════════\n");

  console.log("NOTE: XP, level, and casual recent-game detail cannot be");
  console.log("rebuilt from game files. For full restoration use Firebase");
  console.log("Console → Firestore → Point-in-Time Recovery (if enabled).");

  process.exit(0);
})();
