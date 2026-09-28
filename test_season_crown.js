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
const zlib = require("zlib");

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

// ── the art that actually ships ─────────────────────────────────────────────
// The drawing lives on a PDF page as a photo of the paper with a CROWN drawn
// over it in vector paths. Pull the embedded photo out on its own and the crown
// is simply not in it -- the file still looks like a finished fish, which is why
// this is worth pinning: the whole point of the critter is that he is the king.
// Read the shipped PNG's alpha directly (no image library in this repo) and
// check the band above the fish's back, where the crown and nothing else sits.
function pngAlpha(file) {
  const buf = fs.readFileSync(path.join(__dirname, file));
  const w = buf.readUInt32BE(16), h = buf.readUInt32BE(20);
  const depth = buf[24], color = buf[25], interlace = buf[28];
  if (depth !== 8 || color !== 6 || interlace !== 0) {
    throw new Error(`expected a non-interlaced 8-bit RGBA png, got depth=${depth} color=${color} interlace=${interlace}`);
  }
  const idat = [];
  for (let pos = 8; pos < buf.length; ) {
    const len = buf.readUInt32BE(pos);
    const type = buf.toString("ascii", pos + 4, pos + 8);
    if (type === "IDAT") idat.push(buf.subarray(pos + 8, pos + 8 + len));
    pos += 12 + len;
  }
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const bpp = 4, stride = w * bpp;
  const out = Buffer.alloc(h * stride);
  for (let y = 0; y < h; y++) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) {
      const a = x >= bpp ? out[y * stride + x - bpp] : 0;        // left
      const b = y > 0 ? out[(y - 1) * stride + x] : 0;           // up
      const c = x >= bpp && y > 0 ? out[(y - 1) * stride + x - bpp] : 0;
      let v = line[x];
      if (filter === 1) v += a;
      else if (filter === 2) v += b;
      else if (filter === 3) v += (a + b) >> 1;
      else if (filter === 4) {
        const pp = a + b - c, pa = Math.abs(pp - a), pb = Math.abs(pp - b), pc = Math.abs(pp - c);
        v += (pa <= pb && pa <= pc) ? a : (pb <= pc ? b : c);
      }
      out[y * stride + x] = v & 0xff;
    }
  }
  return { w, h, at: (x, y) => out[y * stride + x * bpp + 3] };
}

section("The shipped drawing still has his crown on");
const ART = pngAlpha("multiplayer/client/avatars/king-of-the-critters.png");
check("the art is the 512px square every other avatar is", ART.w === 512 && ART.h === 512);

function opaque(x0, y0, x1, y1) {
  let n = 0;
  for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) if (ART.at(x, y) > 128) n++;
  return n;
}
// The crown sits at x 211-311, y 130-200. Across that band the crownless art
// (just the top edge of the fish's back running through it) measured 1369
// opaque pixels; with the crown on, 4722.
const crownBand = opaque(200, 125, 320, 205);
check("there is a solid mark above his back, not just the body outline",
      crownBand > 3000, `${crownBand} opaque px (crownless art measured 1369)`);
// Three points with gaps between them. Scanned over the rows the spikes are
// separate on rather than one hard-coded line, so a re-crop that shifts the art
// a few pixels does not fail this for the wrong reason.
function marksAcross(y) {
  let runs = 0, wasOn = false;
  for (let x = 200; x < 330; x++) {
    const on = ART.at(x, y) > 128;
    if (on && !wasOn) runs++;
    wasOn = on;
  }
  return runs;
}
let bestRow = 0, best = 0;
for (let y = 130; y <= 200; y++) { const r = marksAcross(y); if (r > best) { best = r; bestRow = y; } }
check("and it is a crown with three separate points, not one blob",
      best >= 3, `most separate marks on any row was ${best} (at y=${bestRow})`);
check("nothing is drawn outside the circle the rank chip masks it to", (() => {
  const r = ART.w / 2;
  for (let y = 0; y < ART.h; y++) for (let x = 0; x < ART.w; x++) {
    const dx = x - r + 0.5, dy = y - r + 0.5;
    if (dx * dx + dy * dy > r * r && ART.at(x, y) > 40) return false;
  }
  return true;
})());
check("the paper it was photographed on is gone (corners are clear)",
      ART.at(3, 3) === 0 && ART.at(508, 3) === 0 && ART.at(3, 508) === 0 && ART.at(508, 508) === 0);

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
