#!/usr/bin/env node
/* King of the Critters, the season-crown unlock.
 *
 *   node test_season_crown.js
 *
 * One account per season gets this skin: whoever holds the most Ocean Points in
 * Competitive when the season ENDS. That is the only unlock in the game decided
 * by a server record rather than by a number on the player's own account, so the
 * things worth pinning down are the ways it could wrongly say yes: a lead in a
 * season still being played, a failed request read as "never won one", a name
 * that only differs by case, and a season nobody actually scored in.
 *
 * Same approach as test_avatar_reearn.js: lift the REAL source out of the
 * 39k-line browser file and run it against stubbed collaborators, so a rename or
 * a rewrite in the app fails here instead of silently skipping.
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

function slice(startMarker, endMarker) {
  const i = SRC.indexOf(startMarker);
  if (i < 0) throw new Error(`marker not found in preview-app.js: ${startMarker}`);
  const j = SRC.indexOf(endMarker, i + startMarker.length);
  if (j < 0) throw new Error(`end marker not found after ${startMarker}: ${endMarker}`);
  return SRC.slice(i, j);
}

// ── the code under test, lifted from the app ────────────────────────────────
const code = [
  slice("const ANIMAL_AVATARS = [", "\n  ];") + "\n];",
  slice("let _seasonCrowns = null;", "// Numeric unlock progress"),
  slice("function animalProgressText(a, stats, level){", "\n  }") + "\n  }",
].join("\n");

// apiFetch is the app's own HTTP helper; the sandbox poses as it. Each test
// sets NEXT to the answer the server would give and RQ records the calls.
let NEXT = null, RQ = [];
const sandbox = {
  console,
  apiFetch: async (p, o) => { RQ.push(p); return NEXT; },
};
vm.createContext(sandbox);
vm.runInContext(
  code + "\nthis.API = { loadSeasonCrowns, seasonCrownFor, animalProgressText, ANIMAL_AVATARS };",
  sandbox);
const { loadSeasonCrowns, seasonCrownFor, animalProgressText, ANIMAL_AVATARS } = sandbox.API;

const ok = (seasons) => ({ ok: true, data: { seasons } });

// ── the catalogue entry ─────────────────────────────────────────────────────
section("The skin is in the catalogue and says what it is");
const KOC = ANIMAL_AVATARS.find(a => a.id === "king-of-the-critters");
check("the skin exists", !!KOC);
check("it is named King of the Critters", KOC && KOC.name === "King of the Critters");
check("it points at the drawing", KOC && KOC.img === "/avatars/king-of-the-critters.png");
check("its unlock type is season_top_op", KOC && KOC.unlock.type === "season_top_op");
check("it is not buyable (no coin price, not a shop unlock)",
      KOC && KOC.unlock.type !== "shop" && KOC.unlock.coins === undefined);
check("the chosen-few line is on it",
      KOC && /Only the chosen few can have it\./.test(KOC.unlock.label)
         && /Only the chosen few can have it\./.test(KOC.facts));
check("the requirement says most Ocean Points, season, everyone",
      KOC && /Ocean Points/.test(KOC.unlock.label) && /season/i.test(KOC.unlock.label)
         && /anyone else/.test(KOC.unlock.label));
check("no em dash in anything a player reads",
      KOC && !/—/.test(KOC.unlock.label) && !/—/.test(KOC.facts) && !/—/.test(KOC.name));
check("the Store never lists it (Store = unlock.type 'shop')",
      ANIMAL_AVATARS.filter(a => a.unlock && a.unlock.type === "shop")
        .every(a => a.id !== "king-of-the-critters"));

// ── who the crown belongs to ────────────────────────────────────────────────
section("A crown is an ENDED season won on Ocean Points");
const ROWS = [
  { id: "2026-Q4", is_current: true,  king_name: "Reefer",  king_cp: 1400 },
  { id: "2026-Q3", is_current: false, king_name: "Kelpkaiya", king_cp: 1240 },
  { id: "2026-Q2", is_current: false, king_name: "Reefer",  king_cp: 980 },
];

check("the player who ended a past season on top gets it",
      !!seasonCrownFor(ROWS, "Kelpkaiya"));
check("leading the season still being played is NOT a crown",
      !seasonCrownFor([ROWS[0]], "Reefer"));
check("but that same player's earlier finished season still counts",
      !!seasonCrownFor(ROWS, "Reefer") && seasonCrownFor(ROWS, "Reefer").id === "2026-Q2");
check("somebody who never topped a season gets nothing",
      !seasonCrownFor(ROWS, "Mullet"));
check("names match the way the rest of competitive matches them",
      !!seasonCrownFor(ROWS, "  kElPkAiYa  "));
check("an empty name never matches a season with no king",
      !seasonCrownFor([{ id: "x", is_current: false, king_name: "", king_cp: 5 }], ""));
check("a season nobody scored in crowns nobody",
      !seasonCrownFor([{ id: "x", is_current: false, king_name: "Mullet", king_cp: 0 }], "Mullet"));
check("a failed request is not read as 'never won one'",
      !seasonCrownFor(null, "Kelpkaiya"));

// ── the fetch ───────────────────────────────────────────────────────────────
section("It costs one request, and a failure is not cached");
(async () => {
  RQ = []; NEXT = ok(ROWS);
  const a = await loadSeasonCrowns();
  const b = await loadSeasonCrowns();
  check("the season list loads", Array.isArray(a) && a.length === 3);
  check("it asks the server's own season record",
        RQ[0] === "/api/competitive/seasons", RQ[0]);
  check("a second caller reuses it instead of asking again", RQ.length === 1, `${RQ.length} requests`);
  check("both callers see the same rows", a === b);

  // Fresh sandbox: a failed first request must not poison the session.
  const s2 = { console, apiFetch: async (p) => { RQ.push(p); return NEXT; } };
  vm.createContext(s2);
  vm.runInContext(code + "\nthis.API = { loadSeasonCrowns, seasonCrownFor };", s2);
  RQ = []; NEXT = { ok: false, data: null };
  const bad = await s2.API.loadSeasonCrowns();
  check("a failed request yields no rows (nothing is granted on it)", !bad);
  NEXT = ok(ROWS);
  const good = await s2.API.loadSeasonCrowns();
  check("the next load retries instead of staying empty",
        Array.isArray(good) && good.length === 3);
  check("that retry really went back to the server", RQ.length === 2, `${RQ.length} requests`);

  // A thrown request must not take the unlock sweep down with it.
  const s3 = { console, apiFetch: async () => { throw new Error("offline"); } };
  vm.createContext(s3);
  vm.runInContext(code + "\nthis.API = { loadSeasonCrowns };", s3);
  let threw = false;
  try { check("a thrown request yields no rows", !(await s3.API.loadSeasonCrowns())); }
  catch (_) { threw = true; }
  check("and it never throws out of the unlock sweep", !threw);

  // ── the line under the locked tile ────────────────────────────────────────
  section("The locked tile shows where the player actually stands");
  const txt = animalProgressText(KOC, { comp_cp: 640 }, 12);
  check("it reports their own Ocean Points", /640/.test(txt), txt);
  check("it reports nobody else's", !/1240|1400/.test(txt), txt);
  check("no player with no games reads as an error",
        animalProgressText(KOC, {}, 1) === "Your Ocean Points this season: 0");

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
