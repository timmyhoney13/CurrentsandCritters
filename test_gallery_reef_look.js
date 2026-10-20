#!/usr/bin/env node
/* The Avatar Gallery stage is the reef, and the critter stands in a circle.
 *
 *   node test_gallery_reef_look.js
 *
 * THE BUG (found 2026-10-19): on play.currentsandcritters.com the gallery's
 * LEFT half -- the stage, the half that is your own critter -- was a dark navy
 * slab with no circle around the art, while the roster beside it was the cream
 * reef it was redesigned into. Half a screen in each of two designs.
 *
 * Nothing was broken in the browser and nothing 404'd. b8fa5eb ("Put the
 * Avatar Gallery on the reef, and the stage water in a circle") shipped the
 * markup and the roster rules, and the `.gal-*` half of preview.css never got
 * committed with it: it sat in the working tree of a shared checkout for four
 * days while main served the pre-redesign colours. The screen can only be
 * wrong this way when the CSS is a commit behind the rest of the feature, so
 * that is what this pins -- against the SHIPPED stylesheet, not against a
 * rendered page, because a render would pass on any machine whose working
 * tree happens to hold the fix.
 *
 * Two rules Timothy gave explicitly, and a third that follows from them:
 *   1. "make the background match the vibe of the game 200%" -- the reef
 *      shows through a cream veil. Never a dark overlay: on a light surface
 *      dimming turns to mud.
 *   2. "it should have a circle around them with the ocean in 30% like it
 *      looks in the mini screen" -- the stage well is a CLIPPED CIRCLE, and
 *      the water inside it is circular too, not a rounded rectangle.
 *   3. Nothing from the old dark palette survives in the gallery block, or
 *      the half that was missed simply comes back somewhere else.
 *
 * The last check is the other half of shipping it: a stylesheet that is right
 * on disk and cache-busted at a stale build reaches nobody who has opened the
 * game before.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const CLIENT = path.join(__dirname, "multiplayer/client");
const CSS = fs.readFileSync(path.join(CLIENT, "css/preview.css"), "utf8");
const HTML = fs.readFileSync(path.join(CLIENT, "preview.html"), "utf8");
const VERSION = JSON.parse(fs.readFileSync(path.join(CLIENT, "version.json"), "utf8"));

let passed = 0, failed = 0;
function check(name, cond, detail) {
  if (cond) { passed++; console.log("  ✓ " + name); }
  else { failed++; console.error("  ✗ FAIL: " + name + (detail ? ": " + detail : "")); }
}
function section(t) { console.log("\n" + t); }

// The body of the FIRST rule for an exact selector, so `.gal-detail` never
// matches `.gal-detail-img-wrap`. Media-query overrides later in the file are
// deliberately out of scope: this is about the base look.
function rule(selector) {
  const re = new RegExp("(^|[\\s,{}])" + selector.replace(/[.\-]/g, "\\$&") + "\\s*\\{", "m");
  const m = re.exec(CSS);
  if (!m) throw new Error("no rule for " + selector + " in preview.css");
  const start = m.index + m[0].length;
  const end = CSS.indexOf("}", start);
  if (end < 0) throw new Error("unterminated rule for " + selector);
  return CSS.slice(start, end);
}

// Everything from the stage rule to the end of the gallery block, which is
// where the old dark palette lived.
function galleryBlock() {
  const i = CSS.indexOf(".gal-tile {");
  const j = CSS.indexOf(".gal-equip-confirm", i);
  if (i < 0 || j < 0) throw new Error("gallery block not found in preview.css");
  return CSS.slice(i, CSS.indexOf("}", j) + 1);
}

section("The stage is the reef, not a slab");
{
  const detail = rule(".gal-detail");
  check("the stage veil is cream", /rgba\(254,\s*245,\s*230/.test(detail),
        JSON.stringify(detail.slice(0, 160)));
  check("the stage does not paint a dark overlay over the reef",
        !/rgba\(\s*(?:[0-9]|[1-5][0-9]),\s*(?:[0-9]{1,2}),\s*(?:[0-9]{1,3})\s*,/.test(detail),
        "a near-black rgba() is painted on .gal-detail");
  check("the stage does not blur the painting behind it",
        !/backdrop-filter/.test(detail));
}

section("The critter stands in a circle of ocean");
{
  const wrap = rule(".gal-detail-img-wrap");
  check("the stage well is a circle", /border-radius:\s*50%/.test(wrap), wrap.trim());
  check("the well clips what is inside it", /overflow:\s*hidden/.test(wrap));
  check("the well is filled with water, not left empty",
        /background:\s*radial-gradient/.test(wrap));

  const tank = rule(".gal-detail-tank");
  check("the water inside the well is circular too",
        /border-radius:\s*50%/.test(tank), tank.trim());
}

section("None of the old dark palette is left in the gallery");
{
  const block = galleryBlock();
  const ghosts = [
    ["purple requirement boxes", /rgba\(120,\s*80,\s*255/],
    ["neon cyan glow",           /rgba\(34,\s*216,\s*248,\s*\.4\)/],
    ["light-on-dark body text",  /#c5ddf2|#dcd2ff|#eaf4ff/],
    ["dark-surface labels",      /#b39aff|#c9b3ff|#8fb8da|#6ff0b8|#ffe2a0/],
  ];
  ghosts.forEach(([what, re]) =>
    check("no " + what, !re.test(block), what));
}

section("...and it reaches a player who has opened the game before");
{
  const m = /css\/preview\.css\?v=([^"']+)/.exec(HTML);
  check("preview.html cache-busts preview.css", !!m);
  check("...at this build", m && m[1] === VERSION.build,
        `${m && m[1]} vs version.json ${VERSION.build}`);

  const app = fs.readFileSync(path.join(CLIENT, "js/preview-app.js"), "utf8");
  const b = /const APP_BUILD\s*=\s*"([^"]+)"/.exec(app);
  check("APP_BUILD matches version.json", b && b[1] === VERSION.build,
        `${b && b[1]} vs ${VERSION.build}`);
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
