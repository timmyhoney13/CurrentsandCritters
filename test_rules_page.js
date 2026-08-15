/* ================================================================
 * test_rules_page.js — the published How to Play page (/rules) and the
 * two files it now shares with the game.
 *
 * The page renders three things: a Quick Start written on the page, the
 * printed rulebook out of js/rulebook.js, and the strategy list out of
 * js/gamedata.js. The last two used to live inside the game's own
 * preview-app.js/preview.css, so the risks worth testing are:
 *
 *   1. the extraction was LOSSLESS — the tables and the CSS the game
 *      still loads are byte-for-byte what they were before the split
 *      (compared against the previous commit, not a copy of them);
 *   2. the game still loads both new files, in the right ORDER;
 *   3. every asset /rules asks for exists, and resolves on BOTH hosts —
 *      Render serves the game paths natively, Vercel needs a rewrite.
 *
 *   node test_rules_page.js
 * ================================================================ */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer", "client");

let passed = 0;
const failures = [];

function check(name, fn) {
  try {
    fn();
    passed++;
    console.log("  ✓ " + name);
  } catch (err) {
    failures.push(name + "\n      " + String(err.message || err).split("\n")[0]);
    console.log("  ✗ " + name + "\n      " + String(err.message || err).split("\n")[0]);
  }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }
function eq(a, b, msg) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    throw new Error(msg + "\n      got:      " + JSON.stringify(a).slice(0, 200)
      + "\n      expected: " + JSON.stringify(b).slice(0, 200));
  }
}
const read = (p) => fs.readFileSync(p, "utf8");

// ── The pre-split baseline ───────────────────────────────────────
// The lossless checks compare today's files against the last commit that
// still held the tables and the .rb rules INSIDE the game — found by
// walking history rather than hard-coding a sha, so the test keeps
// working as the branch moves on. Skipped (not failed) when history does
// not reach that far: a shallow clone, or a fresh checkout with no git.
function gitShow(rev, file) {
  return execFileSync("git", ["show", rev + ":" + file], { cwd: ROOT, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
}
function findBaseline(file, marker) {
  let revs;
  try {
    revs = execFileSync("git", ["log", "--format=%H", "-n", "400", "--", file],
      { cwd: ROOT, encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] }).trim().split("\n");
  } catch (_) { return null; }
  for (const rev of revs) {
    if (!rev) continue;
    try { if (gitShow(rev, file).includes(marker)) return rev; } catch (_) { /* file absent there */ }
  }
  return null;
}
const BASE_APP = findBaseline("multiplayer/client/js/preview-app.js", "const FAMILY_COLORS = {");
// Marker must be a rule that MOVED. The banner comment stayed behind as a
// signpost, so matching on it would find the post-split commit instead.
const BASE_CSS = findBaseline("multiplayer/client/css/preview.css", ".rb.rb-light {");

// Run a browser-less script that only assigns onto window.
function loadIntoWindow(src, win) {
  const ctx = vm.createContext(win);
  vm.runInContext(src, ctx, { filename: path.basename(src.slice(0, 0) || "x") });
  return win;
}
function freshWindow() {
  const win = {};
  win.window = win;
  return win;
}

// ── The shared data module ───────────────────────────────────────
console.log("\njs/gamedata.js — the tables the game and /rules share");

const gamedataSrc = read(path.join(CLIENT, "js", "gamedata.js"));
const W = freshWindow();
vm.runInContext(gamedataSrc, vm.createContext(W), { filename: "gamedata.js" });

check("exports every table the two consumers need", () => {
  for (const key of ["CC_FAMILY_COLORS", "CC_FAMILY_INK", "CC_STRAT_PRIMARY",
                     "CC_BUILTIN_STRATEGIES", "CC_SPECIES_GUIDE",
                     "CC_FAMILY_AVATAR", "CC_STRAT_AVATAR_OVERRIDE"]) {
    assert(W[key], "window." + key + " is missing");
  }
});

check("touches nothing else on window", () => {
  const extra = Object.keys(W).filter(k => k !== "window" && !k.startsWith("CC_"));
  eq(extra, [], "gamedata.js leaked non-CC_ globals");
});

check("has all ten families in both colour tables", () => {
  const fams = ["coral", "mammal", "crosscurrent", "invertebrate", "game fish",
                "crustacean", "cephalopod", "bird", "baitfish", "ocean"];
  eq(Object.keys(W.CC_FAMILY_COLORS).sort(), fams.slice().sort(), "FAMILY_COLORS families");
  eq(Object.keys(W.CC_FAMILY_INK).sort(), fams.slice().sort(), "FAMILY_INK families");
});

check("every strategy has a label, a blurb and a card list", () => {
  assert(W.CC_BUILTIN_STRATEGIES.length >= 20,
    "expected 20+ strategies, got " + W.CC_BUILTIN_STRATEGIES.length);
  W.CC_BUILTIN_STRATEGIES.forEach((s, i) => {
    assert(s.label, "strategy " + i + " has no label");
    assert(s.blurb, "strategy " + s.label + " has no blurb");
    assert(Array.isArray(s.cards) && s.cards.length, "strategy " + s.label + " has no cards");
  });
});

check("every strategy has a primary family the colour table knows", () => {
  W.CC_BUILTIN_STRATEGIES.forEach((s, i) => {
    const fam = W.CC_STRAT_PRIMARY[i];
    assert(fam, "strategy " + s.label + " has no primary family");
    assert(W.CC_FAMILY_COLORS[fam], "strategy " + s.label + ": unknown family " + fam);
  });
});

check("the species guide covers the nine animal families", () => {
  eq(W.CC_SPECIES_GUIDE.length, 9, "species guide should hold nine families");
  W.CC_SPECIES_GUIDE.forEach(s => {
    assert(W.CC_FAMILY_COLORS[s.key], s.name + ": " + s.key + " is not a colour-table family");
    assert(s.art && s.n && s.slot && s.txt, s.name + " is missing a field");
  });
});

// ── Lossless extraction: compare against the pre-split commit ─────
console.log("\nThe split moved code, it did not rewrite it");

check("the four tables are IDENTICAL to the ones preview-app.js used to hold", () => {
  if (!BASE_APP) { console.log("      (skipped: history does not reach the pre-split commit)"); return; }
  const before = gitShow(BASE_APP, "multiplayer/client/js/preview-app.js");
  // Pull the literals straight out of the old file and evaluate them.
  const grab = (name, open, close) => {
    const start = before.indexOf("const " + name + " = " + open);
    assert(start !== -1, "could not find " + name + " in the previous commit");
    let depth = 0, i = before.indexOf(open, start);
    for (; i < before.length; i++) {
      if (before[i] === open) depth++;
      else if (before[i] === close) { depth--; if (depth === 0) break; }
    }
    return vm.runInNewContext("(" + before.slice(before.indexOf(open, start), i + 1) + ")");
  };
  eq(W.CC_FAMILY_COLORS, grab("FAMILY_COLORS", "{", "}"), "FAMILY_COLORS drifted");
  eq(W.CC_FAMILY_INK, grab("FAMILY_INK", "{", "}"), "FAMILY_INK drifted");
  eq(W.CC_STRAT_PRIMARY, grab("_STRAT_PRIMARY", "[", "]"), "_STRAT_PRIMARY drifted");
  eq(W.CC_BUILTIN_STRATEGIES, grab("BUILTIN_STRATEGIES", "[", "]"), "BUILTIN_STRATEGIES drifted");
  eq(W.CC_FAMILY_AVATAR, grab("_FAMILY_AVATAR", "{", "}"), "_FAMILY_AVATAR drifted");
  eq(W.CC_STRAT_AVATAR_OVERRIDE, grab("_STRAT_AVATAR_OVERRIDE", "{", "}"), "_STRAT_AVATAR_OVERRIDE drifted");
});

check("preview-app.js no longer defines them, it reads them off window", () => {
  const app = read(path.join(CLIENT, "js", "preview-app.js"));
  for (const [local, global_] of [
    ["FAMILY_COLORS", "CC_FAMILY_COLORS"], ["FAMILY_INK", "CC_FAMILY_INK"],
    ["_STRAT_PRIMARY", "CC_STRAT_PRIMARY"], ["BUILTIN_STRATEGIES", "CC_BUILTIN_STRATEGIES"],
    ["HTP_SPECIES", "CC_SPECIES_GUIDE"], ["_FAMILY_AVATAR", "CC_FAMILY_AVATAR"],
    ["_STRAT_AVATAR_OVERRIDE", "CC_STRAT_AVATAR_OVERRIDE"],
  ]) {
    const re = new RegExp("const\\s+" + local + "\\s*=\\s*window\\." + global_ + ";");
    assert(re.test(app), local + " should be `const " + local + " = window." + global_ + ";`");
  }
});

check("every .rb rule moved to css/rulebook.css, and none was left behind", () => {
  if (!BASE_CSS) { console.log("      (skipped: history does not reach the pre-split commit)"); return; }
  const before = gitShow(BASE_CSS, "multiplayer/client/css/preview.css");
  const after = read(path.join(CLIENT, "css", "preview.css"));
  const rb = read(path.join(CLIENT, "css", "rulebook.css"));
  // A rule is its selector line; compare the multisets of .rb-* selector lines.
  const rbLines = (s) => s.split("\n")
    .map(l => l.trim())
    .filter(l => /^\.rb[.\-\s{,:]/.test(l) || /^\.rb\b/.test(l));
  // What matters is that the split LOST nothing, and that preview.css does not
  // grow new book styling behind rulebook.css's back. What does NOT matter is
  // that the stylesheet never grows again — a new figure or table added to
  // rulebook.css since the split is that file doing its job, so additions are
  // allowed where the original multiset comparison forbade them.
  const beforeLines = rbLines(before);
  const nowSet = new Set(rbLines(after).concat(rbLines(rb)));
  const lost = beforeLines.filter(l => !nowSet.has(l));
  eq(lost, [], "rules that existed before the split have gone missing");
  // One `.rb-sec` padding override predates the split and still lives in the
  // in-game modal's media query, where moving it would change the cascade.
  // Pin the count so it stays the only one.
  const leftBehind = rbLines(after);
  assert(leftBehind.length <= rbLines(before).filter(l => leftBehind.includes(l)).length,
    "preview.css gained NEW .rb rules — they belong in css/rulebook.css: " + leftBehind.join(" | "));
});

// ── The game still loads both new files, in order ─────────────────
console.log("\npreview.html wiring");

const previewHtml = read(path.join(CLIENT, "preview.html"));

check("loads css/rulebook.css AFTER css/preview.css", () => {
  const a = previewHtml.indexOf("/css/preview.css");
  const b = previewHtml.indexOf("/css/rulebook.css");
  assert(a !== -1, "preview.css link missing");
  assert(b !== -1, "rulebook.css link missing");
  assert(b > a, "rulebook.css must come after preview.css or the cascade changes");
});

check("loads js/gamedata.js BEFORE js/preview-app.js", () => {
  const a = previewHtml.indexOf("/js/gamedata.js");
  const b = previewHtml.indexOf("/js/preview-app.js");
  assert(a !== -1, "gamedata.js script tag missing");
  assert(a < b, "gamedata.js must be ordered before preview-app.js");
  // Both deferred, or the order stops being guaranteed.
  for (const f of ["gamedata.js", "preview-app.js", "rulebook.js"]) {
    const tag = previewHtml.match(new RegExp("<script[^>]*/js/" + f.replace(".", "\\.") + "[^>]*>"));
    assert(tag && /\bdefer\b/.test(tag[0]), f + " must be loaded with defer");
  }
});

check("the cache-busting ?v= was bumped on every file that changed", () => {
  const build = JSON.parse(read(path.join(CLIENT, "version.json"))).build;
  for (const f of ["/css/preview.css", "/css/rulebook.css", "/js/gamedata.js", "/js/preview-app.js", "/js/rulebook.js"]) {
    const m = previewHtml.match(new RegExp(f.replace(/[/.]/g, "\\$&") + "\\?v=([0-9.\\-]+)"));
    assert(m, f + " has no ?v= cache-buster");
    assert(m[1] === build, f + " is stamped ?v=" + m[1] + " but this build is " + build);
  }
});

check("APP_BUILD matches version.json", () => {
  const app = read(path.join(CLIENT, "js", "preview-app.js"));
  const v = JSON.parse(read(path.join(CLIENT, "version.json")));
  const build = app.match(/const APP_BUILD\s*=\s*"([^"]+)"/)[1];
  const version = app.match(/const APP_VERSION\s*=\s*"([^"]+)"/)[1];
  eq(build, v.build, "APP_BUILD must equal version.json build");
  eq(version, v.version, "APP_VERSION must equal version.json version");
});

// ── The page itself ──────────────────────────────────────────────
console.log("\nrules.html — the published How to Play page");

const rulesHtml = read(path.join(CLIENT, "rules.html"));

check("has the three panes the page promises", () => {
  for (const pane of ["quick", "rulebook", "strategies"]) {
    assert(rulesHtml.includes('id="pane-' + pane + '"'), "missing pane " + pane);
    assert(rulesHtml.includes('id="tab-' + pane + '"'), "missing tab " + pane);
  }
});

check("pulls the rulebook and the strategies from the game's own files", () => {
  assert(/<script[^>]+\/js\/rulebook\.js/.test(rulesHtml), "does not load js/rulebook.js");
  assert(/<script[^>]+\/js\/gamedata\.js/.test(rulesHtml), "does not load js/gamedata.js");
  assert(/<link[^>]+\/css\/rulebook\.css/.test(rulesHtml), "does not load css/rulebook.css");
  assert(rulesHtml.includes("window.CC_RULEBOOK_HTML"), "does not render CC_RULEBOOK_HTML");
  assert(rulesHtml.includes("window.CC_BUILTIN_STRATEGIES"), "does not render CC_BUILTIN_STRATEGIES");
  assert(rulesHtml.includes("window.CC_SPECIES_GUIDE"), "does not render CC_SPECIES_GUIDE");
});

check("never inlines its own copy of the rules text", () => {
  // A tell-tale rulebook sentence appearing here would mean the page had
  // grown a second, silently-diverging copy of the printed book.
  assert(!rulesHtml.includes("Create the highest-scoring marine ecosystem"),
    "rules.html contains rulebook prose — it must render js/rulebook.js instead");
});

check("reads its family colours lazily, not at parse time", () => {
  // The inline script runs BEFORE the deferred data files, so a snapshot
  // (`var X = window.CC_FAMILY_COLORS`) would leave every family lilac.
  assert(!/var\s+FAMILY_COLORS\s*=\s*window\.CC_FAMILY_COLORS/.test(rulesHtml),
    "family colours are snapshotted at parse time, before gamedata.js has run");
  assert(/function colours\(\)\s*\{\s*return window\.CC_FAMILY_COLORS/.test(rulesHtml),
    "expected a colours() accessor that reads window on every call");
});

// ── Assets resolve on BOTH hosts ─────────────────────────────────
console.log("\nEvery asset /rules asks for resolves on both hosts");

// Absolute paths the page (and the rulebook it renders) request.
const rulebookSrc = read(path.join(CLIENT, "js", "rulebook.js"));
const assetPaths = new Set();
for (const src of [rulesHtml, rulebookSrc]) {
  for (const m of src.matchAll(/["'](\/(css|js|avatars|species|horizontal_cards|vertical_cards|oceans_cards|icon\.svg)[^"'?]*)/g)) {
    assetPaths.add(m[1]);
  }
}

check("found the asset paths to check", () => {
  assert(assetPaths.size >= 5, "expected several asset paths, found " + assetPaths.size);
});

check("the Render game server maps each one onto a file that exists", () => {
  // Render: /css, /js, /avatars, /species, /icon.svg live under
  // multiplayer/client; the card sheets live at the repo root.
  const missing = [];
  for (const p of assetPaths) {
    if (p.includes("page_")) continue;          // built per uid, checked below
    const onDisk = /^\/(horizontal_cards|vertical_cards|oceans_cards)\//.test(p)
      ? path.join(ROOT, p) : path.join(CLIENT, p);
    if (!fs.existsSync(onDisk)) missing.push(p);
  }
  eq(missing, [], "these paths have no file behind them");
});

check("the avatars and species art the page names all exist", () => {
  const missing = [];
  for (const s of W.CC_SPECIES_GUIDE) {
    const art = path.join(CLIENT, "avatars", s.art + ".png");
    const sym = path.join(CLIENT, "species", s.key.replace(/ /g, "-") + ".png");
    if (!fs.existsSync(art)) missing.push("avatars/" + s.art + ".png");
    if (!fs.existsSync(sym)) missing.push("species/" + s.key.replace(/ /g, "-") + ".png");
  }
  for (const slug of Object.values(W.CC_FAMILY_AVATAR).concat(Object.values(W.CC_STRAT_AVATAR_OVERRIDE), ["mullet"])) {
    if (!fs.existsSync(path.join(CLIENT, "avatars", slug + ".png"))) missing.push("avatars/" + slug + ".png");
  }
  eq(missing, [], "missing art");
});

check("Vercel rewrites cover every game-path prefix the page uses", () => {
  const vercel = JSON.parse(read(path.join(ROOT, "vercel.json")));
  const rewrites = vercel.rewrites || [];
  const prefixes = new Set();
  for (const p of assetPaths) {
    const first = p.split("/")[1];
    // The card sheets already sit at the marketing site's root.
    if (["horizontal_cards", "vertical_cards", "oceans_cards"].includes(first)) continue;
    prefixes.add(first.endsWith(".svg") ? first : first);
  }
  const missing = [];
  for (const pre of prefixes) {
    const hit = rewrites.some(r =>
      r.source === "/" + pre || r.source.startsWith("/" + pre + "/"));
    if (!hit) missing.push(pre);
  }
  eq(missing, [], "no Vercel rewrite for these — they would 404 on currentsandcritters.com");
});

check("/rules is still served from this file on both hosts", () => {
  const vercel = JSON.parse(read(path.join(ROOT, "vercel.json")));
  const rule = (vercel.rewrites || []).find(r => r.source === "/rules");
  assert(rule && rule.destination === "/multiplayer/client/rules.html",
    "vercel.json must rewrite /rules to multiplayer/client/rules.html");
  const server = read(path.join(ROOT, "multiplayer_server.py"));
  assert(/RULES_INDEX_PATH\s*=.*rules\.html/.test(server),
    "the game server must still serve multiplayer/client/rules.html at /rules");
});

// ── The marketing site's How to Play band ────────────────────────
console.log("\nindex.html — the band that replaced the Learn-in-60 image");

const indexHtml = read(path.join(ROOT, "index.html"));

check("the flat rules JPEG is gone from the page and from the repo", () => {
  assert(!indexHtml.includes("learn-in-60-seconds"),
    "index.html still references the old rules image");
  assert(!fs.existsSync(path.join(ROOT, "assets", "learn-in-60-seconds.png")),
    "the unused rules image is still shipping in the deploy");
});

check("#how survives, so every existing link to it still lands", () => {
  assert(/id="how"/.test(indexHtml), "the #how anchor was dropped");
  for (const href of ['href="#how"']) {
    assert(indexHtml.includes(href), "expected the page to keep linking to " + href);
  }
});

check("the band ends at a link to the full rules page", () => {
  assert(/<a class="l60-door" href="\/rules">/.test(indexHtml),
    "the How to Play band has no door to /rules");
  assert(indexHtml.includes("Click Here to View the Rules"),
    "the call to action text is missing");
});

check("the words from the old image survived as real text", () => {
  for (const phrase of [
    "Three simple steps happen on every turn",
    "What is an Ocean?", "What are Star Abilities?",
    "How does scoring work?", "What ends the game?",
    "Plan ahead, stay flexible, and combine your cards to score big",
  ]) {
    assert(indexHtml.includes(phrase), "lost from the old image: " + phrase);
  }
});

check("styles.css styles the band it now needs to style", () => {
  const css = read(path.join(ROOT, "styles.css"));
  for (const cls of ["learn60-inner", "l60-beats", "l60-beat", "l60-flow", "l60-qr",
                     "l60-q", "l60-door", "l60-door-btn", "l60-door-chips", "l60-tip"]) {
    assert(css.includes("." + cls), "styles.css has no rule for ." + cls);
  }
  assert(!css.includes(".learn60-img"), "the dead .learn60-img rule is still there");
});

// ── Result ───────────────────────────────────────────────────────
console.log("\n" + "─".repeat(60));
if (failures.length) {
  console.log(passed + " passed, " + failures.length + " FAILED\n");
  failures.forEach(f => console.log("  ✗ " + f));
  process.exit(1);
}
console.log(passed + " passed, 0 failed");
