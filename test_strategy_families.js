#!/usr/bin/env node
/* Every strategy, and every card in it, says what FAMILY it is.
 *
 * Run:  node test_strategy_families.js
 *
 * The Strategies panel has always coloured a plan and its cards by family: a
 * pink glow means Mammal, a dark blue one means Game Fish. That is only an
 * answer to someone who has already learnt the ten colours, and the panel's
 * own legend is at the top of a list you have scrolled away from by the time
 * you are looking at a card. So the family is now printed as a WORD, on the
 * strategy tile and on every card inside the plan.
 *
 * The names are not written here: they come off window.CC_FAMILY_LABEL in
 * js/gamedata.js, the same table /rules and the printed book name families
 * from, so a family cannot be called one thing at the table and another on
 * the published page. This file runs the real chip builders against the real
 * strategy data and checks that every plan, and every card of every plan,
 * actually gets a name out of them.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = __dirname;
const APP = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const DATA = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/gamedata.js"), "utf8");

let failures = 0, checks = 0;
function check(cond, label) {
  checks++;
  if (cond) console.log("  PASS " + label);
  else { failures++; console.log("  FAIL " + label); }
}

function grabFn(name) {
  const re = new RegExp("\\n( +)(?:function|const) " + name + "\\b");
  const m = re.exec(APP);
  if (!m) throw new Error(`${name} not found in preview-app.js`);
  const start = m.index + 1;
  let i = APP.indexOf("{", start), depth = 0;
  for (let j = i; j < APP.length; j++) {
    if (APP[j] === "{") depth++;
    else if (APP[j] === "}") { depth--; if (depth === 0) return APP.slice(start, j + 1); }
  }
  throw new Error(`unbalanced braces reading ${name}`);
}
function grabLine(needle) {
  const i = APP.indexOf(needle);
  if (i < 0) throw new Error(`could not find: ${needle}`);
  const a = APP.lastIndexOf("\n", i) + 1;
  const b = APP.indexOf("\n", i);
  return APP.slice(a, b < 0 ? APP.length : b);
}

// The real game data first: these are the real plans and the real families.
const W = {};
vm.runInNewContext(DATA, { window: W, console });

const sandbox = { console, window: W, FAMILY_COLORS: W.CC_FAMILY_COLORS };
vm.createContext(sandbox);
vm.runInContext([
  grabLine("const _hesc = (s) =>"),
  grabFn("_normFamily"),
  grabFn("familyColor"),
  grabFn("_famChipHtml"),
  grabFn("_stratFamiliesHtml"),
  // The plan's own chips name whatever CC_STRAT_SYMBOLS resolves, so the
  // fallback path needs the primary table too.
  "const _STRAT_PRIMARY = window.CC_STRAT_PRIMARY;",
].join("\n"), sandbox);

const S = W.CC_BUILTIN_STRATEGIES;
const PRIMARY = W.CC_STRAT_PRIMARY;
const LABELS = W.CC_FAMILY_LABEL;
const nameOf = (html) => [...String(html).matchAll(/>([^<]+)<\/span>/g)].map(m => m[1]);
const colourOf = (html) => [...String(html).matchAll(/--lc:([^"]+)"/g)].map(m => m[1]);

console.log("\nOne card, one family name");
{
  const chip = sandbox._famChipHtml("Bird", "hd-card-fam");
  check(/hs-fam-chip/.test(chip) && /hd-card-fam/.test(chip),
    "a card chip wears both the shared class and its own");
  check(nameOf(chip)[0] === "Birds", "a Bird card says Birds, the name on the poster");
  check(colourOf(chip)[0] === W.CC_FAMILY_COLORS.bird, "and it is tinted the family's own colour");
  check(nameOf(sandbox._famChipHtml("Game Fish"))[0] === "Game Fish",
    "a two-word family keeps both of its words");
  check(nameOf(sandbox._famChipHtml("Ocean"))[0] === "Oceans",
    "an ocean card says Oceans");
  check(sandbox._famChipHtml("", "x") === "", "an unnamed family prints no chip at all");
}

console.log("\nEvery family a card can be is a family the chip can name");
{
  const seen = new Set();
  for (const s of S) for (const c of (s.cards || [])) seen.add(c.species);
  const unnamed = [...seen].filter(sp => !nameOf(sandbox._famChipHtml(sp)).length);
  check(!unnamed.length, "every species in the deck has a printable name"
    + (unnamed.length ? ": " + unnamed.join(", ") : " (" + seen.size + " of them)"));
  const offTable = [...seen].filter(sp => {
    const got = nameOf(sandbox._famChipHtml(sp))[0];
    return !Object.values(LABELS).includes(got);
  });
  check(!offTable.length,
    "no chip invents a name gamedata.js does not publish"
    + (offTable.length ? ": " + offTable.join(", ") : ""));
}

console.log("\nEvery strategy says what it is made of");
{
  const empty = [];
  const tooMany = [];
  for (let i = 0; i < S.length; i++) {
    const row = sandbox._stratFamiliesHtml(i, S[i]);
    const names = nameOf(row);
    if (!names.length) empty.push(S[i].label);
    // A tile holds a name, a blurb and a button, and the chips are the
    // caption for the one or two symbols it already wears. More than two
    // means the plan has started listing its card families instead, which is
    // the thing the caption exists not to be.
    if (names.length > 2) tooMany.push(S[i].label + " (" + names.length + ")");
  }
  check(!empty.length, "no plan is left without a family" + (empty.length ? ": " + empty.join(", ") : ""));
  check(!tooMany.length, "no plan wears more chips than a tile can hold"
    + (tooMany.length ? ": " + tooMany.join(", ") : ""));
}

console.log("\nThe families a plan wears are the families it is built on");
{
  const famOf = (label) => {
    const i = S.findIndex(x => x.label === label);
    return nameOf(sandbox._stratFamiliesHtml(i, S[i]));
  };
  check(famOf("Birds")[0] === "Birds", "the Birds plan leads with Birds");
  check(famOf("Kelp Forest")[0] === "Oceans", "an ocean plan leads with Oceans");
  const lob = famOf("Bird Lobster");
  check(lob.join(" + ") === "Birds + Crustaceans",
    "a combo wears exactly the two families it bridges: " + lob.join(" + "));
  // The caption has to agree with the picture beside it, or the tile is
  // telling a player two different things about the same plan.
  const mismatched = [];
  for (let i = 0; i < S.length; i++) {
    const syms = (W.CC_STRAT_SYMBOLS(i, S[i]) || []).map(e => LABELS[e.key] || e.key);
    const chips = nameOf(sandbox._stratFamiliesHtml(i, S[i]));
    if ([...new Set(syms)].join("|") !== chips.join("|")) {
      mismatched.push(S[i].label + " (" + syms.join("+") + " vs " + chips.join("+") + ")");
    }
  }
  check(!mismatched.length, "every chip names the symbol printed next to it"
    + (mismatched.length ? ": " + mismatched.slice(0, 4).join(", ") : ""));
  // Deduped: the Birds plan lists twelve bird cards, not twelve bird chips.
  const birds = famOf("Birds");
  check(new Set(birds).size === birds.length, "a plan never repeats a family chip");
}

// ═══════════════════════════════════════════════════════════════════════════
//  The chip has to FIT the tile it is printed on
// ═══════════════════════════════════════════════════════════════════════════
// A card tile in the detail view is a grid cell with a floor of 86px, and the
// chip under the name is one unbreakable word. "Invertebrates" at the chip's
// font is most of that 86px before its dot and its padding, so this is not a
// theoretical worry: measured against the real css/preview.css at the widths
// the panel really opens at.
console.log("\nThe family chip fits on a card tile");

const os = require("os");
const { execFileSync } = require("child_process");
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("  (skipped: no Chrome/Chromium found)");
} else {
  const CSS = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
  // The longest name of each kind, plus the longest card name that carries one.
  const CARDS = [];
  for (const st of S) for (const c of (st.cards || [])) CARDS.push(c);
  const byFam = new Map();
  for (const c of CARDS) {
    const fam = nameOf(sandbox._famChipHtml(c.species))[0] || "";
    const prev = byFam.get(fam);
    if (!prev || c.name.length > prev.name.length) byFam.set(fam, c);
  }
  const SAMPLE = [...byFam.entries()];
  const tiles = SAMPLE.map(([fam, c]) =>
    `<div class="hd-card-tile" data-fam="${fam}">`
    + `<div class="hd-card-img"></div>`
    + `<div class="hd-card-name">${c.name}</div>`
    + sandbox._famChipHtml(c.species, "hd-card-fam")
    + `<div class="hd-card-text">${String(c.text).slice(0, 40)}</div>`
    + `</div>`).join("");
  const WIDTHS = [300, 420, 620, 860];
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-famchip-"));
  const page = `<!doctype html><html><head><meta charset="utf-8"><style>${CSS}</style>
<style>html,body{margin:0;background:#fff;font-family:sans-serif}
  .panel{background:#fff;padding:0;margin:0 0 20px}</style></head><body>
${WIDTHS.map(w => `<div class="panel" style="width:${w}px"><div class="hd-cards-grid">${tiles}</div></div>`).join("")}
<div id="out">RUNNING</div>
<script>
var bad = [], info = [];
document.querySelectorAll(".panel").forEach(function (panel) {
  var w = panel.style.width;
  panel.querySelectorAll(".hd-card-tile").forEach(function (tile) {
    var chip = tile.querySelector(".hd-card-fam");
    if (!chip) { bad.push(w + " " + tile.dataset.fam + ": no chip at all"); return; }
    var t = tile.getBoundingClientRect(), c = chip.getBoundingClientRect();
    if (c.right > t.right + 0.5 || c.left < t.left - 0.5) {
      bad.push(w + " " + tile.dataset.fam + " overflows by "
        + Math.round(Math.max(c.right - t.right, t.left - c.left)) + "px"
        + " (chip " + Math.round(c.width) + "px in tile " + Math.round(t.width) + "px)");
    }
    // The BOX fitting is not the same as the NAME fitting: the chip is a flex
    // item, so its width is clamped to the tile and a name too long for it is
    // quietly ellipsised instead of spilling. "Invertebra…" is not an answer
    // to "what family is this", so the text is measured too.
    if (chip.scrollWidth > chip.clientWidth + 1) {
      bad.push(w + " " + tile.dataset.fam + " is cut short ("
        + chip.scrollWidth + "px of name in " + chip.clientWidth + "px)");
    }
    if (c.height < 8) bad.push(w + " " + tile.dataset.fam + " collapsed to " + Math.round(c.height) + "px");
  });
  var t0 = panel.querySelector(".hd-card-tile").getBoundingClientRect();
  info.push(w + " tiles " + Math.round(t0.width) + "px");
});
document.getElementById("out").textContent = JSON.stringify({ bad: bad, info: info });
<\/script></body></html>`;
  fs.writeFileSync(path.join(tmp, "page.html"), page);
  let dom = "";
  try {
    dom = execFileSync(CHROME, [
      "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      "--user-data-dir=" + path.join(tmp, "profile"),
      "--window-size=1000,2400", "--virtual-time-budget=10000", "--dump-dom",
      "file://" + path.join(tmp, "page.html"),
    ], { encoding: "utf8", maxBuffer: 1 << 28, stdio: ["ignore", "pipe", "ignore"] });
  } catch (e) {
    check(false, "could not run Chrome: " + (e && e.message));
  }
  const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
  if (!m || m[1].trim() === "RUNNING") {
    check(false, "the page produced no measurements");
  } else {
    const R = JSON.parse(m[1]
      .replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&"));
    check(!R.bad.length, "no family chip runs off its card tile"
      + (R.bad.length ? ": " + R.bad.slice(0, 5).join("; ") : " (" + R.info.join(", ") + ")"));
  }
  try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (_) {}
}

console.log(`\n${checks - failures} passed, ${failures} failed`);
process.exit(failures ? 1 : 0);
