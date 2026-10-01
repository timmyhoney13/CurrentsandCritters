#!/usr/bin/env node
/* King of the Critters, the crown skin at the top of the ladder.
 *
 *   node test_king_crown.js
 *
 * Competitive has no seasons: the skin is earned by CLIMBING to the King of
 * the Critters rank (1200 OP) and, like every other rank unlock, it is then
 * kept forever. It used to be "finish a season on top", decided by a server
 * record rather than a number on the player's own account, so this also pins
 * down that none of that machinery can come back.
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
  slice("function rankTierValue(rankName){", "\n  }") + "\n  }",
  slice("const _RANK_TIER_VALUE = {", "\n"),
  slice("function animalProgressText(a, stats, level){", "\n  }") + "\n  }",
].join("\n");

const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(
  code + "\nthis.API = { rankTierValue, _RANK_TIER_VALUE, animalProgressText, ANIMAL_AVATARS };",
  sandbox);
const { rankTierValue, _RANK_TIER_VALUE, animalProgressText, ANIMAL_AVATARS } = sandbox.API;

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
check("it is earned by reaching a rank", KOC && KOC.unlock.type === "rank");
check("and that rank is King", KOC && KOC.unlock.tier === "king");
check("it is not buyable (no coin price, not a shop unlock)",
      KOC && KOC.unlock.type !== "shop" && KOC.unlock.coins === undefined);
check("the chosen-few line is on it",
      KOC && /Only the chosen few can have it\./.test(KOC.unlock.label)
         && /Only the chosen few can have it\./.test(KOC.facts));
check("the requirement names the King of the Critters rank",
      KOC && /King of the Critters rank/.test(KOC.unlock.label));
check("and it no longer promises a season to finish",
      KOC && !/season/i.test(KOC.unlock.label), KOC && KOC.unlock.label);
check("no em dash in anything a player reads",
      KOC && !/\u2014/.test(KOC.unlock.label) && !/\u2014/.test(KOC.facts) && !/\u2014/.test(KOC.name));
check("the Store never lists it (Store = unlock.type 'shop')",
      ANIMAL_AVATARS.filter(a => a.unlock && a.unlock.type === "shop")
        .every(a => a.id !== "king-of-the-critters"));

// ── who the crown belongs to ────────────────────────────────────────────────
// The sweep's test for a rank unlock is
// rankTierValue(stats.rank_competitive) >= _RANK_TIER_VALUE[u.tier].
section("Only a King rank earns it");
const earns = (rank) => rankTierValue(rank) >= _RANK_TIER_VALUE[KOC.unlock.tier];

check("King of the Critters earns it", earns("King of the Critters"));
check("Emerald, one rung short, does not", !earns("Emerald Emperor Penguin III"));
check("neither does Diamond", !earns("Diamond Dolphin I"));
check("nor Golden Grouper", !earns("Golden Grouper III"));
check("nor Silver", !earns("Silver Spiny Lobster I"));
check("nor Bronze", !earns("Bronze Barracuda I"));
check("Unranked does not", !earns("Unranked"));
check("and neither does a missing rank", !earns("") && !earns(null) && !earns(undefined));
check("the rank string is read the way the rest of competitive reads it",
      earns("  king of the critters  "));

// ── nothing asks the server about seasons any more ──────────────────────────
section("The season machinery is gone, not just unused");
check("no /api/competitive/seasons request is left in the app",
      !/\/api\/competitive\/seasons/.test(SRC));
check("no season-crown lookup is left", !/seasonCrownFor|loadSeasonCrowns/.test(SRC));
check("no 'season_top_op' unlock type is left", !/season_top_op/.test(SRC));
check("no avatar is unlocked by anything season-shaped",
      ANIMAL_AVATARS.every(a => !a.unlock || !/season/i.test(String(a.unlock.type))));

// ── the line under the locked tile ──────────────────────────────────────────
section("The locked tile shows where the player actually stands");
{
  const txt = animalProgressText(KOC, { rank_competitive: "Golden Grouper II" }, 12);
  check("it reports their own rank", /Golden Grouper II/.test(txt), txt);
  check("a player with no games reads as Unranked, not as an error",
        animalProgressText(KOC, {}, 1) === "Current rank: Unranked",
        animalProgressText(KOC, {}, 1));
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
