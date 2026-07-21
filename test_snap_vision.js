#!/usr/bin/env node
/* Snap & Score local-recognition tests.
 *
 *   node test_snap_vision.js
 *
 * Covers: cross-language descriptor parity with build_snap_card_library.py
 * (fixtures), exact reference matching, 90°/180° rotation, perspective
 * distortion, similar-looking cards + symbol-copy disambiguation, duplicate
 * cards, missing/unreadable cards, false rectangle rejection, low-confidence
 * gating, board assembly (ocean side assignment), and the retake decision.
 * Synthetic photos are rendered from the real library's reference art bases so
 * no image decoder is needed under Node.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const core = require("./multiplayer/client/js/snap-vision-core.js");
const LIB = JSON.parse(fs.readFileSync(path.join(__dirname, "multiplayer/client/snap-card-library.json"), "utf8"));
const FIX = JSON.parse(fs.readFileSync(path.join(__dirname, "test_snap_vision_fixtures.json"), "utf8"));

let passed = 0, failed = 0;
function check(name, cond, detail) {
  if (cond) { passed++; console.log("  ✓ " + name); }
  else { failed++; console.error("  ✗ " + name + (detail ? " — " + detail : "")); }
}
function section(t) { console.log("\n" + t); }

// ── synthetic photo builder ──────────────────────────────────────────────────
// We re-render cards into a fake tabletop photo from their 64×64 descriptor
// bases (nearest-neighbour upscale). That art is exactly what the descriptors
// summarize, so a correct pipeline must re-identify it.

const baseCache = {};
function cardBase(cardId) {
  if (baseCache[cardId]) return baseCache[cardId];
  // reconstruct an approximate base from the library color layout? No — for
  // fidelity we rebuild the base from the fixture when available, otherwise
  // paint a deterministic pseudo-art from the card's descriptors.
  const fx = FIX.fixtures.find((f) => f.id === cardId);
  if (fx) return (baseCache[cardId] = Float64Array.from(fx.base));
  const card = LIB.cards.find((c) => c.id === cardId);
  if (!card) throw new Error("no card " + cardId);
  // deterministic pseudo-art: color-layout cells + hash-seeded texture. Both
  // the photo AND the matcher see art derived from the same reference data,
  // but pseudo-art only matches the real card loosely — tests that need exact
  // matches use the three fixture cards.
  const N = core.BASE_N;
  const base = new Float64Array(N * N * 3);
  let seed = parseInt(card.p.slice(0, 8), 16) || 1;
  const rnd = () => (seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296;
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      const cell = (Math.floor(y / 16) * 4 + Math.floor(x / 16)) * 3;
      const i = (y * N + x) * 3;
      const t = (rnd() - 0.5) * 30;
      base[i] = clamp255(card.c[cell] + t);
      base[i + 1] = clamp255(card.c[cell + 1] + t);
      base[i + 2] = clamp255(card.c[cell + 2] + t);
    }
  }
  return (baseCache[cardId] = base);
}
function clamp255(v) { return v < 0 ? 0 : v > 255 ? 255 : v; }

function makePhoto(w, h, tableLuma) {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) {
    const v = tableLuma + ((i * 2654435761) % 7) - 3; // slight texture
    data[i * 4] = v; data[i * 4 + 1] = v; data[i * 4 + 2] = v; data[i * 4 + 3] = 255;
  }
  return { data, width: w, height: h };
}

// paint a card onto the photo: dark border + base art fill; supports rotation
// (0 portrait, 90 landscape, 180, 270) and mild perspective skew
function paintCard(photo, cardId, cx, cy, cardH, rot, skew) {
  const base = cardBase(cardId);
  const N = core.BASE_N;
  const ar = 5 / 7;
  const cardW = Math.round(cardH * ar);
  const rad = ((rot || 0) * Math.PI) / 180;
  const cosR = Math.cos(rad), sinR = Math.sin(rad);
  const sk = skew || 0;
  const corners = [];
  const halfW = cardW / 2, halfH = cardH / 2;
  [[-halfW, -halfH], [halfW, -halfH], [halfW, halfH], [-halfW, halfH]].forEach(([px, py], idx) => {
    const wob = idx === 0 ? -sk : idx === 2 ? sk : 0; // opposite-corner pinch
    const rx = (px + wob) * cosR - py * sinR;
    const ry = (px + wob) * sinR + py * cosR;
    corners.push([cx + rx, cy + ry]);
  });
  // rasterize: for each photo pixel inside the quad, inverse-map to card space
  const xs = corners.map((p) => p[0]), ys = corners.map((p) => p[1]);
  const H = core.homography(cardW, cardH, corners); // dst card-rect → photo
  const Hinv = core.homography.length ? invertH(H) : null;
  const minX = Math.max(0, Math.floor(Math.min(...xs))), maxX = Math.min(photo.width - 1, Math.ceil(Math.max(...xs)));
  const minY = Math.max(0, Math.floor(Math.min(...ys))), maxY = Math.min(photo.height - 1, Math.ceil(Math.max(...ys)));
  for (let y = minY; y <= maxY; y++) {
    for (let x = minX; x <= maxX; x++) {
      const w = Hinv[6] * x + Hinv[7] * y + Hinv[8];
      const u = (Hinv[0] * x + Hinv[1] * y + Hinv[2]) / w;
      const v = (Hinv[3] * x + Hinv[4] * y + Hinv[5]) / w;
      if (u < 0 || v < 0 || u >= cardW || v >= cardH) continue;
      const i = (y * photo.width + x) * 4;
      const borderPx = Math.min(u, v, cardW - u, cardH - v);
      if (borderPx < cardW * 0.03) { // dark navy card border
        photo.data[i] = 8; photo.data[i + 1] = 26; photo.data[i + 2] = 62;
      } else {
        const bx = Math.min(N - 1, Math.floor((u / cardW) * N));
        const by = Math.min(N - 1, Math.floor((v / cardH) * N));
        const b = (by * N + bx) * 3;
        photo.data[i] = base[b]; photo.data[i + 1] = base[b + 1]; photo.data[i + 2] = base[b + 2];
      }
      photo.data[i + 3] = 255;
    }
  }
  return corners;
}

function invertH(H) {
  // invert a 3×3 homography (row-major 9)
  const [a, b, c, d, e, f, g, h, i] = H;
  const A = e * i - f * h, B = c * h - b * i, C = b * f - c * e;
  const D = f * g - d * i, E = a * i - c * g, F = c * d - a * f;
  const G = d * h - e * g, Hh = b * g - a * h, I = a * e - b * d;
  const det = a * A + b * D + c * G;
  return [A / det, B / det, C / det, D / det, E / det, F / det, G / det, Hh / det, I / det];
}

function cropOfCard(cardId, jitter) {
  // build a clean warped "crop" straight from the base (what warpCard yields
  // on a perfect photo): upscale base 64×64 → 200×280 nearest
  const base = cardBase(cardId);
  const N = core.BASE_N;
  const out = new Float64Array(core.CARD_W * core.CARD_H * 3);
  let seed = 42;
  const rnd = () => (seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296;
  for (let y = 0; y < core.CARD_H; y++) {
    for (let x = 0; x < core.CARD_W; x++) {
      const bx = Math.min(N - 1, Math.floor((x / core.CARD_W) * N));
      const by = Math.min(N - 1, Math.floor((y / core.CARD_H) * N));
      const b = (by * N + bx) * 3, o = (y * core.CARD_W + x) * 3;
      const t = jitter ? (rnd() - 0.5) * jitter : 0;
      out[o] = clamp255(base[b] + t);
      out[o + 1] = clamp255(base[b + 1] + t);
      out[o + 2] = clamp255(base[b + 2] + t);
    }
  }
  return out;
}

// ── 1. cross-language descriptor parity ─────────────────────────────────────
section("descriptor parity with build_snap_card_library.py");
check("fixture descriptor version matches core", FIX.descriptorVersion === core.DESCRIPTOR_VERSION);
FIX.fixtures.forEach((fx) => {
  const got = core.descriptorsFromBase(Float64Array.from(fx.base));
  check(fx.id + " pHash matches Python", got.p === fx.expect.p, got.p + " vs " + fx.expect.p);
  check(fx.id + " dHash matches Python", got.d === fx.expect.d, got.d + " vs " + fx.expect.d);
  check(fx.id + " color layout matches Python", JSON.stringify(got.c) === JSON.stringify(fx.expect.c));
  check(fx.id + " edge histogram matches Python", JSON.stringify(got.e) === JSON.stringify(fx.expect.e));
  check(fx.id + " half color layouts match Python",
        JSON.stringify(got.hh) === JSON.stringify(fx.expect.hh) &&
        JSON.stringify(got.hv) === JSON.stringify(fx.expect.hv));
  check(fx.id + " half pHashes match Python",
        JSON.stringify(got.ph) === JSON.stringify(fx.expect.ph) &&
        JSON.stringify(got.pv) === JSON.stringify(fx.expect.pv));
  check(fx.id + " name-band grids match Python",
        JSON.stringify(got.nh) === JSON.stringify(fx.expect.nh) &&
        JSON.stringify(got.nv) === JSON.stringify(fx.expect.nv));
});

// ── 2. exact + noisy reference matching ─────────────────────────────────────
section("reference matching");
const matcher = core.buildMatcher(LIB);
FIX.fixtures.forEach((fx) => {
  const m = core.matchCrop(cropOfCard(fx.id), matcher);
  const grp = matcher.groupOf[m.cardId];
  check(fx.id + " exact crop → same art group", grp === matcher.groupOf[fx.id], "got " + m.cardId);
  check(fx.id + " exact crop confidence ≥ 0.9", m.conf >= 0.9, "conf " + m.conf);
});
{
  const m = core.matchCrop(cropOfCard("h01", 26), matcher); // noisy crop
  check("noisy crop still matches its art group", matcher.groupOf[m.cardId] === matcher.groupOf["h01"], "got " + m.cardId);
  check("noisy crop confidence ≥ 0.7", m.conf >= 0.7, "conf " + m.conf);
}
{
  // 180° flipped crop must still match (cards can face either player)
  const flipped = core.rotateCrop180(cropOfCard("v01"));
  const m = core.matchCrop(flipped, matcher);
  check("180° crop matches", matcher.groupOf[m.cardId] === matcher.groupOf["v01"], "got " + m.cardId);
  check("180° crop reports flip", m.flip === true);
}
{
  // gibberish crop must NOT be confident
  const junk = new Float64Array(core.CARD_W * core.CARD_H * 3);
  let seed = 7;
  for (let i = 0; i < junk.length; i++) { seed = (seed * 1664525 + 1013904223) >>> 0; junk[i] = seed % 256; }
  const m = core.matchCrop(junk, matcher);
  check("random-noise crop confidence < 0.7", m.conf < 0.7, "conf " + m.conf);
}

// ── 3. similar-looking cards / symbol copies ────────────────────────────────
section("similar cards & symbol copies");
{
  // o01 and o02 are the same Pier art with different symbol badges — the art
  // group must contain both and the badge grids must actually differ
  const g1 = matcher.groupOf["o01"], g2 = matcher.groupOf["o02"];
  check("Pier copies share one art group", g1 === g2, g1 + " vs " + g2);
  const c1 = LIB.cards.find((c) => c.id === "o01"), c2 = LIB.cards.find((c) => c.id === "o02");
  check("Pier badge grids differ between copies", core.badgeDist(c1.b.ocean, c2.b.ocean) > 0.01,
        "badgeDist " + core.badgeDist(c1.b.ocean, c2.b.ocean).toFixed(4));
  check("distinct art keeps distinct groups", matcher.groupOf["h01"] !== matcher.groupOf["h02"]);
}

// ── 4. geometry: homography + perspective warp round-trip ───────────────────
section("geometry");
{
  const H = core.homography(200, 280, [[10, 20], [210, 30], [220, 330], [5, 320]]);
  check("homography maps corners", (() => {
    const map = (x, y) => {
      const w = H[6] * x + H[7] * y + H[8];
      return [(H[0] * x + H[1] * y + H[2]) / w, (H[3] * x + H[4] * y + H[5]) / w];
    };
    const [x1, y1] = map(0, 0), [x2, y2] = map(200, 280);
    return Math.abs(x1 - 10) < 1e-6 && Math.abs(y1 - 20) < 1e-6 &&
           Math.abs(x2 - 220) < 1e-6 && Math.abs(y2 - 330) < 1e-6;
  })());
  const q = core.orderQuad([[100, 0], [0, 0], [0, 70], [100, 70]]); // landscape quad
  const topLen = Math.hypot(q[1][0] - q[0][0], q[1][1] - q[0][1]);
  const sideLen = Math.hypot(q[3][0] - q[0][0], q[3][1] - q[0][1]);
  check("orderQuad canonicalizes landscape → portrait mapping", sideLen > topLen);
}

// ── 5. full-photo detection + identification + assembly ─────────────────────
section("synthetic board scan");
{
  // ocean (o08 fixture) with an up/down card above (h01) and a left/right
  // card to the right (v01); plus a small non-card blob (false rectangle)
  const photo = makePhoto(1200, 900, 190);
  const H = 260; // card height on the "table"
  paintCard(photo, "o08", 600, 470, H, 0, 0);
  paintCard(photo, "h01", 600, 205, H, 90, 3);   // above the ocean, landscape
  paintCard(photo, "v01", 830, 470, H, 0, 4);    // right of the ocean, portrait
  // false rectangle: a bright non-card blob
  for (let y = 780; y < 860; y++) for (let x = 80; x < 140; x++) {
    const i = (y * photo.width + x) * 4;
    photo.data[i] = 250; photo.data[i + 1] = 250; photo.data[i + 2] = 250;
  }

  const res = core.scanBoard(photo, LIB, null, () => {});
  const matchedIds = res.quads.filter((q) => q.match && q.match.conf >= core.DEFAULTS.acceptConf)
                              .map((q) => matcher.groupOf[q.match.cardId]);
  check("detects ≥ 3 card quads", res.quads.length >= 3, "got " + res.quads.length);
  check("ocean identified", matchedIds.includes(matcher.groupOf["o08"]), matchedIds.join(","));
  check("up/down card identified (90° rotated on table)", matchedIds.includes(matcher.groupOf["h01"]), matchedIds.join(","));
  check("left/right card identified", matchedIds.includes(matcher.groupOf["v01"]), matchedIds.join(","));
  // The false rectangle must never be AUTO-ADDED to the scored board. Per the
  // "never silently drop a card" rule it may still keep a visible box, but that
  // box must be flagged (needs-id or size-outlier) rather than attached.
  const targetGroups = [matcher.groupOf["o08"], matcher.groupOf["h01"], matcher.groupOf["v01"]];
  const phantomOnBoard = res.quads.some((q) => q.assigned && q.match &&
    !targetGroups.includes(matcher.groupOf[q.match.cardId]));
  const phantomOcean = res.board.oceans.length > 1;
  check("false rectangle never auto-added to the board (kept as a flagged box)",
        !phantomOnBoard && !phantomOcean,
        res.quads.map((q) => (q.match ? q.match.cardId + ":" + q.match.conf + (q.needsId ? "!id" : "") + (q.sizeOutlier ? "!sz" : "") : "?")).join(", "));

  const ocean = res.board.oceans.find((o) => {
    const card = LIB.cards.find((c) => c.id === "o08" || matcher.groupOf[c.id] === matcher.groupOf["o08"] && c.halves.ocean.u === o.u);
    return card && card.halves.ocean && card.halves.ocean.u === o.u;
  }) || res.board.oceans[0];
  check("board has exactly one ocean", res.board.oceans.length === 1, "got " + res.board.oceans.length);
  if (ocean) {
    check("h-card assigned to the UP side with its up-half uid",
          ocean.up.length === 1 && ocean.up[0] % 2 === 1 && ocean.up[0] <= 96,
          JSON.stringify(ocean));
    check("v-card assigned to the RIGHT side with its right-half uid",
          ocean.right.length === 1 && ocean.right[0] >= 102 && ocean.right[0] % 2 === 0,
          JSON.stringify(ocean));
  }
}

// ── 6. duplicates ───────────────────────────────────────────────────────────
section("duplicate physical cards");
{
  const photo = makePhoto(1200, 700, 195);
  paintCard(photo, "o08", 350, 350, 260, 0, 0);
  paintCard(photo, "h01", 350, 92, 260, 90, 0);
  paintCard(photo, "h01", 860, 92, 260, 90, 0);  // impossible second copy
  const res = core.scanBoard(photo, LIB, null, () => {});
  check("duplicate physical card produces a warning",
        res.warnings.some((w) => w.includes("detected twice")), res.warnings.join(" | "));
}

// ── 7. missing / unassigned cards ───────────────────────────────────────────
section("unassigned & low-confidence handling");
{
  const photo = makePhoto(1400, 700, 195);
  paintCard(photo, "o08", 300, 350, 260, 0, 0);
  paintCard(photo, "v01", 1250, 350, 260, 0, 0); // far away from the ocean
  const res = core.scanBoard(photo, LIB, null, () => {});
  check("far-away card is not silently attached",
        res.board.oceans.length === 1 &&
        ["up", "down", "left", "right"].every((s) => res.board.oceans[0][s].length === 0),
        JSON.stringify(res.board));
  check("far-away card is reported for manual attachment",
        res.warnings.some((w) => w.includes("attach it by hand")) || res.unassigned.length > 0,
        res.warnings.join(" | "));
}

// ── 8. quality / retake gate ────────────────────────────────────────────────
section("photo quality gate");
{
  const dark = makePhoto(900, 700, 12);
  const res = core.scanBoard(dark, LIB, null, () => {});
  check("very dark photo triggers a retake", res.needsRetake === true);
  check("dark reason is player-readable", res.retakeReasons.some((r) => r.includes("too dark")),
        res.retakeReasons.join(" | "));
  const tiny = makePhoto(300, 200, 190);
  const res2 = core.scanBoard(tiny, LIB, null, () => {});
  check("tiny photo triggers a retake", res2.needsRetake === true);
}

// ── 9. REAL card art photos (generated by make_snap_test_photo.py) ──────────
// Composites of the actual sheet PNGs on textured tabletops with rotation,
// blur, color cast and JPEG artifacts — the closest automated stand-in for a
// phone photo. Regenerate with `python3 make_snap_test_photo.py` (files are
// untracked; this section is skipped when they're absent).
section("real card-art photos");
["test_snap_real_photo_closeup", "test_snap_real_photo_minimal",
 "test_snap_real_photo_light", "test_snap_real_photo_dark",
 "test_snap_real_photo_glare", "test_snap_real_photo_touching",
 "test_snap_real_photo_dupes"].forEach((name) => {
  const binPath = path.join(__dirname, name + ".bin");
  if (!fs.existsSync(binPath)) {
    console.log("  (skipped: " + name + ".bin missing — run make_snap_test_photo.py)");
    return;
  }
  const meta = JSON.parse(fs.readFileSync(path.join(__dirname, name + ".json"), "utf8"));
  const img = { data: new Uint8ClampedArray(fs.readFileSync(binPath)),
                width: meta.width, height: meta.height };
  const res = core.scanBoard(img, LIB, null, () => {});
  meta.cards.forEach((truth) => {
    const q = res.quads.find((q2) => q2.match && q2.match.conf >= core.DEFAULTS.acceptConf &&
      matcher.groupOf[q2.match.cardId] === matcher.groupOf[truth.id]);
    check(name + ": " + truth.id + " recognized (conf ≥ 0.7)", !!q,
          "quads: " + res.quads.map((x) => (x.match ? x.match.cardId + ":" + x.match.conf : "?")).join(", "));
  });
  const oceanTruths = meta.cards.filter((c) => c.side === "ocean").length;
  check(name + ": board has the right number of oceans",
        res.board.oceans.length === oceanTruths, JSON.stringify(res.board));
  // every non-ocean truth card must be attached on its true side
  const sidesOk = meta.cards.filter((c) => c.side !== "ocean").every((truth) => {
    return res.board.oceans.some((oc) => oc[truth.side] && oc[truth.side].some((u) => {
      const card = LIB.cards.find((lc) => Object.keys(lc.halves).some((s) => lc.halves[s].u === u));
      return card && matcher.groupOf[card.id] === matcher.groupOf[truth.id];
    }));
  });
  check(name + ": every card attached on its true side", sidesOk, JSON.stringify(res.board));
  // nothing EXTRA reaches the scored board (flagged boxes are fine, but the
  // board must contain only real cards). A distractor may keep a box, but it
  // must be flagged (needsId / sizeOutlier) rather than assigned.
  const truthGroups = meta.cards.map((c) => matcher.groupOf[c.id]);
  const boardCardGroup = (u) => {
    const card = LIB.cards.find((lc) => Object.keys(lc.halves).some((s) => lc.halves[s].u === u));
    return card ? matcher.groupOf[card.id] : null;
  };
  let phantom = null;
  res.board.oceans.forEach((oc) => {
    if (!truthGroups.includes(boardCardGroup(oc.u))) phantom = "ocean u" + oc.u;
    ["up", "down", "left", "right"].forEach((s) => oc[s].forEach((u) => {
      if (!truthGroups.includes(boardCardGroup(u))) phantom = "u" + u + " on " + s;
    }));
  });
  check(name + ": no phantom card reached the board", phantom === null, phantom || "");
});

// duplicate-artwork resolution: two same-art copies must land on DISTINCT UIDs
{
  const name = "test_snap_real_photo_dupes";
  const binPath = path.join(__dirname, name + ".bin");
  if (fs.existsSync(binPath)) {
    const meta = JSON.parse(fs.readFileSync(path.join(__dirname, name + ".json"), "utf8"));
    const img = { data: new Uint8ClampedArray(fs.readFileSync(binPath)),
                  width: meta.width, height: meta.height };
    const res = core.scanBoard(img, LIB, null, () => {});
    const boardUids = res.board.oceans.flatMap((oc) =>
      ["up", "down", "left", "right"].flatMap((s) => oc[s]));
    check(name + ": both same-art copies placed on distinct UIDs",
          boardUids.length === 2 && boardUids[0] !== boardUids[1], JSON.stringify(boardUids));
  }
}

// ── 10. manual quads → identify (the box editor path) ───────────────────────
section("manual quad identification");
{
  const photo = makePhoto(900, 700, 195);
  const corners = paintCard(photo, "o08", 450, 350, 300, 0, 0);
  const quads = core.identifyQuads(photo, [corners], matcher);
  check("manually drawn box identifies the card",
        quads[0].match && matcher.groupOf[quads[0].match.cardId] === matcher.groupOf["o08"],
        quads[0].match && quads[0].match.cardId);
}

console.log("\n" + passed + " passed, " + failed + " failed");
process.exit(failed ? 1 : 0);
