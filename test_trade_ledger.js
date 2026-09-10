#!/usr/bin/env node
/* The trade projection: what an amount typed into the picker does to BOTH
 * players, and to their levels.
 *
 * Run:  node test_trade_ledger.js         (no browser needed)
 *
 * This screen used to carry two copies of the same idea: a two-card "ledger"
 * pinned above the offer columns, and a smaller repeat of it inside the picker
 * sheet. The ledger is gone. It was the half players actually saw fail —
 * preview.html is served unversioned while its CSS and JS carry a ?v= stamp,
 * that stamp was not bumped when the ledger shipped, and the result on every
 * trade was two unstyled cards reading "You / Level -" and "Them / Level -"
 * for a week. What is left is the one copy that sits where the decision is
 * made: directly above the amount box, moving on every keystroke.
 *
 * What is worth testing here is the arithmetic, because it is the part that can
 * be quietly wrong in a way nobody notices until an account loses a level it
 * should not have. _trLedgerModel is pure — two purses and two offers in, both
 * sides' before/after out — and is lifted straight out of preview-app.js so
 * this tests the shipped code rather than a copy of it.
 *
 * The invariants that matter:
 *   · a trade CONSERVES: what leaves one side arrives at the other
 *   · giving lowers you and receiving raises the other, on the same numbers
 *   · nothing ever goes negative
 *   · the "after" accounts for BOTH offers, not just your own half
 *
 * The rest of the file checks the wiring, the level line on both sides, the CSS
 * the arithmetic is drawn with, and the cache buster — since a correct number
 * painted into a hidden element, or into a stylesheet nobody is served, helps
 * nobody.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = __dirname;
const APP  = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");
const CSS  = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (detail != null ? "  [" + detail + "]" : "")); }
}

function grabFn(name, indent) {
  const pad = " ".repeat(indent);
  const start = APP.indexOf(`\n${pad}function ${name}(`);
  if (start < 0) throw new Error(`function ${name}() not found`);
  let depth = 0;
  for (let j = APP.indexOf("{", start); j < APP.length; j++) {
    const ch = APP[j];
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return APP.slice(start, j + 1); }
  }
  throw new Error("unbalanced braces reading " + name);
}

const model = new Function(grabFn("_trLedgerModel", 4) + "\nreturn _trLedgerModel;")();
const nothing = { coins: 0, passes: 0, xp: 0 };
const purse = (c, p, x) => ({ coins: c, passes: p, xp: x });
const offer = (o) => Object.assign({ coins: 0, passes: 0, xp: 0 }, o || {});

/* ══════════════════════════════════════════════════════════════════════
   1. GIVING
   ══════════════════════════════════════════════════════════════════════ */
console.log("\ngiving coins away");
{
  const r = model(purse(5000, 0, 9000), purse(200, 0, 400), offer({ coins: 1200 }), nothing);
  check("mine goes down", r.me.after.coins === 3800, r.me.after.coins);
  check("theirs goes up by the same amount", r.them.after.coins === 1400, r.them.after.coins);
  check("my before is untouched", r.me.now.coins === 5000, r.me.now.coins);
  check("nothing else of mine moved",
        r.me.after.xp === 9000 && r.me.after.passes === 0, JSON.stringify(r.me.after));
}

console.log("\ngiving XP away, which is the one that costs a level");
{
  // 9,000 XP down to 4,000. The levels are read off the real curve elsewhere;
  // here the only claim is the arithmetic the level is then derived from.
  const r = model(purse(0, 0, 9000), purse(0, 0, 1000), offer({ xp: 5000 }), nothing);
  check("my lifetime XP drops", r.me.after.xp === 4000, r.me.after.xp);
  check("theirs rises by exactly that", r.them.after.xp === 6000, r.them.after.xp);
}

console.log("\ngiving Season Pass vouchers");
{
  const r = model(purse(0, 3, 0), purse(0, 0, 0), offer({ passes: 2 }), nothing);
  check("mine goes down", r.me.after.passes === 1, r.me.after.passes);
  check("theirs goes up", r.them.after.passes === 2, r.them.after.passes);
}

/* ══════════════════════════════════════════════════════════════════════
   2. THE AFTER IS THE REAL AFTER
   A panel that only subtracts your own offer is lying whenever the other
   side has put something on the table, which is most trades.
   ══════════════════════════════════════════════════════════════════════ */
console.log("\nboth offers count, not just mine");
{
  const r = model(purse(5000, 0, 9000), purse(1000, 0, 2000),
                  offer({ coins: 1200, xp: 500 }), offer({ coins: 400, xp: 3000 }));
  check("my coins: minus what I give, plus what I get",
        r.me.after.coins === 5000 - 1200 + 400, r.me.after.coins);
  check("my XP the same way", r.me.after.xp === 9000 - 500 + 3000, r.me.after.xp);
  check("their coins mirror it", r.them.after.coins === 1000 - 400 + 1200, r.them.after.coins);
  // They offered 3,000 XP holding 2,000, which the server would reject anyway.
  // The model floors at zero rather than showing a player a negative total.
  check("an over-offer floors at zero instead of going negative",
        r.them.after.xp === 0, r.them.after.xp);
  check("a swap that nets out leaves both where they started",
        model(purse(100, 0, 0), purse(100, 0, 0), offer({ coins: 50 }), offer({ coins: 50 }))
          .me.after.coins === 100);
}

/* ══════════════════════════════════════════════════════════════════════
   3. THE UNKNOWN SIDE
   The peer's profile can fail to load. The trade still works, so the
   projection has to cope rather than throw or invent numbers.
   ══════════════════════════════════════════════════════════════════════ */
console.log("\nwhen the peer's profile has not loaded");
{
  const r = model(purse(500, 0, 100), null, offer({ coins: 100 }), nothing);
  check("my side is still computed", r.me.after.coins === 400, r.me.after.coins);
  check("their side is null, not zeros", r.them === null, JSON.stringify(r.them));
}

/* ══════════════════════════════════════════════════════════════════════
   4. NOTHING GOES NEGATIVE, EVER, ON ANY INPUT
   The amounts are validated server-side, but this is what the PLAYER is
   shown while typing, and "-3,000 XP" on your own card is alarming
   nonsense. Fuzzed with junk the inputs can genuinely produce.
   ══════════════════════════════════════════════════════════════════════ */
console.log("\nthe projection survives anything typed into the box");
{
  let rng = 77;
  const rand = () => (rng = (rng * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  const junk = [undefined, null, NaN, -1, -99999, 1e12, 0.5, "12", "", "abc", Infinity, -Infinity];
  const pick = (a) => a[Math.floor(rand() * a.length) % a.length];

  let negative = 0, nonInt = 0, threw = 0, conserved = 0, runs = 0;
  for (let i = 0; i < 3000; i++) {
    const mine   = purse(pick(junk), pick(junk), pick(junk));
    const theirs = rand() < 0.15 ? null : purse(pick(junk), pick(junk), pick(junk));
    const give   = { coins: pick(junk), passes: pick(junk), xp: pick(junk) };
    const recv   = { coins: pick(junk), passes: pick(junk), xp: pick(junk) };
    let r;
    try { r = model(mine, theirs, give, recv); } catch (_) { threw++; continue; }
    runs++;
    for (const s of [r.me, r.them]) {
      if (!s) continue;
      for (const k of ["coins", "passes", "xp"]) {
        if (s.now[k] < 0 || s.after[k] < 0) negative++;
        if (!Number.isInteger(s.now[k]) || !Number.isInteger(s.after[k])) nonInt++;
      }
    }
    // Conservation, checked only where no clamp fired: what one side loses the
    // other gains. A floor at zero legitimately breaks this, which is why the
    // clamped runs are skipped rather than counted as failures.
    if (r.them) {
      for (const k of ["coins", "passes", "xp"]) {
        const dMe = r.me.after[k] - r.me.now[k], dThem = r.them.after[k] - r.them.now[k];
        // Same clamp the model uses, or this test disagrees with it about
        // which runs legitimately hit the floor.
        const cl = (n) => { const v = Math.floor(Number(n)); return Number.isFinite(v) ? Math.max(0, v) : 0; };
        const clampedMe = r.me.now[k] - cl(give[k]) + cl(recv[k]) < 0;
        const clampedThem = r.them.now[k] - cl(recv[k]) + cl(give[k]) < 0;
        if (!clampedMe && !clampedThem && dMe !== -dThem) conserved++;
      }
    }
  }
  check("it never throws", threw === 0, threw + " threw");
  check("it ran on every case", runs === 3000, runs);
  check("no figure is ever negative", negative === 0, negative + " negatives");
  check("every figure is a whole number", nonInt === 0, nonInt + " non-integers");
  check("what one side loses the other gains", conserved === 0, conserved + " leaks");
}

/* ══════════════════════════════════════════════════════════════════════
   5. THE WIRING
   The numbers are drawn in ONE place: the projection panel inside the item
   picker, directly above the amount box. There used to be a second copy —
   a two-card ledger pinned above the offer columns — and it was removed
   because it was the part players actually saw broken: the markup shipped
   while the cache-busted CSS and JS did not, so it sat there as two bare
   words and "Level -" twice, on every trade.
   ══════════════════════════════════════════════════════════════════════ */
console.log("\nthe projection is drawn where the number is typed");
check("the panel is inside the picker sheet, not above it",
      HTML.indexOf('id="cc-trade-picker-box"') < HTML.indexOf('id="cc-trade-swing"')
      && HTML.indexOf('id="cc-trade-swing"') < HTML.indexOf('id="cc-trade-xp-foot"'));
check("it starts hidden", /id="cc-trade-swing" hidden/.test(HTML));
check("…and the CSS lets it stay hidden, over its own display:grid",
      /\.cctr-pk-swing\[hidden\] \{ display: none; \}/.test(CSS));
check("the old two-card ledger is gone from the markup",
      !HTML.includes('id="cc-trade-ledger"') && !HTML.includes('id="cctr-me-lvl"'));
check("…and its rules are gone from the CSS, not left behind as dead weight",
      !CSS.includes("#cc-trade-ledger") && !CSS.includes(".cctr-lvl-bar")
      && !CSS.includes(".cctr-side-av") && !CSS.includes(".cctr-swap"));
check("…and its painter is gone from the app",
      !APP.includes("_trPaintSide") && !APP.includes("_trRenderLedger"));

console.log("\nit moves while you type");
check("every keystroke sets a draft", APP.includes('el.addEventListener("input", live);'));
check("…for all three amounts, through one wiring path",
      (APP.match(/wireAmount\("cc-trade-/g) || []).length === 3);
check("the draft is folded into my offer before the maths",
      APP.includes("function _trOfferWithDraft(offer)")
      && APP.includes("if (_trDraft && _trDraft.kind) o[_trDraft.kind]"));
check("leaving the picker drops the half-typed number",
      APP.includes("_trClearDraft();") && APP.includes("function _trHidePicker()"));
check("switching tabs drops it too",
      APP.includes("_trDraft = null;                 // the number in the old tab's box is gone"));
check("the panel repaints on every render of the trade",
      APP.includes("_trRenderProjection();\n"));
check("…and on every keystroke, not only on commit",
      /function _trSetDraft\(kind, raw\) \{[\s\S]*?_trRenderProjection\(\);/.test(APP));
check("it is only drawn while the picker is actually open",
      /const pickerOpen = !!\(pk && pk\.style\.display !== "none"\);/.test(APP));
check("the peer's profile is fetched when the trade opens",
      APP.includes("_trLoadPeer(_trPeerUid);"));
check("…without blocking the open on it",
      !APP.includes("await _trLoadPeer("));
check("a stale profile never lands on a new trade",
      APP.includes('if (!prof || String(uid) !== String(_trPeerUid)) return;'));
check("the peer is cleared between trades",
      (APP.match(/_trPeer = null; _trDraft = null;/g) || []).length === 2);

/* ══════════════════════════════════════════════════════════════════════
   6. THE LEVEL, ON BOTH SIDES
   The whole reason the panel exists: type an amount of XP and see which
   level it leaves each player at. A level is public — leaderboard, profile,
   the name beside a seat — so both are shown in full.
   ══════════════════════════════════════════════════════════════════════ */
console.log("\ntyping an XP amount shows both levels, before and after");
const lvlLine = (() => {
  const src = grabFn("_trLevelLine", 4);
  // Two stubs: the real level curve is tested elsewhere, what matters here is
  // that both ends are read and both are printed.
  const levels = { 0: 1, 1000: 5, 4000: 9, 9000: 14 };
  return new Function("_trLevelOf",
    src + "\nreturn _trLevelLine;")((xp) => (xp in levels) ? { level: levels[xp] } : null);
})();
check("a drop reads as the level before and the level after",
      lvlLine(9000, 4000) === '<div class="cctr-swing-lvl down">Level 14 → Level 9</div>',
      lvlLine(9000, 4000));
check("a gain reads the same way, the other direction",
      lvlLine(1000, 9000) === '<div class="cctr-swing-lvl up">Level 5 → Level 14</div>',
      lvlLine(1000, 9000));
check("an amount too small to move a level says the level, not an arrow to itself",
      lvlLine(9000, 9000) === '<div class="cctr-swing-lvl">Level 14</div>', lvlLine(9000, 9000));
check("an unreadable curve says so instead of printing a made-up level",
      /muted/.test(lvlLine(9000, 123)), lvlLine(9000, 123));

const swing = APP.slice(APP.indexOf("function _trRenderSwing(model)"),
                        APP.indexOf("// Called on every keystroke in a picker input."));
check("the level line is drawn for BOTH sides", (swing.match(/_trLevelLine\(/g) || []).length === 2);
check("…and only on the XP tab, where a level is what moves",
      (swing.match(/isXp \? _trLevelLine\(/g) || []).length === 2
      && /const isXp = kind === "xp";/.test(swing));
check("my own balance is shown in full, before → after",
      /cctr-purse-was">' \+ _trFmt\(meNow\)/.test(swing));
check("their balance is shown as what they GAIN, never as what they hold",
      /d === 0 \? "No change" : \(d > 0 \? "\+" : "−"\) \+ _trFmt\(Math\.abs\(d\)\)/.test(swing)
      && !/_trFmt\(tNow\)/.test(swing));
check("a peer whose profile has not landed says so, rather than showing Level -",
      /Level still loading…/.test(swing));
check("…and still says what they are being given",
      /typed \? "\+" \+ _trFmt\(typed\)/.test(swing));
check("an empty box asks for a number instead of showing a blank panel",
      /Type an amount below to see where it leaves you both\./.test(swing));
check("the peer's name is escaped before it goes into innerHTML",
      (swing.match(/escapeHtml\(themWho\)/g) || []).length >= 2);

/* ══════════════════════════════════════════════════════════════════════
   7. IT READS ACROSS THE SHEET, NOT DOWN A RIBBON
   ══════════════════════════════════════════════════════════════════════ */
console.log("\nthe offers go across the screen, not down a thin column");
const items = CSS.slice(CSS.indexOf("    .cctr-items {"), CSS.indexOf("    .cctr-item {"));
check("the item list is a grid", /display:\s*grid/.test(items), items.trim().slice(0, 80));
check("…that flows across in columns", /repeat\(auto-fill,\s*minmax\(/.test(items));
check("…and is not the old single-file flex column",
      !/flex-direction:\s*column/.test(items), items.trim().slice(0, 80));
check("the box is wide enough for two of them side by side",
      /width:\s*min\(1180px,\s*96vw\)/.test(CSS));
check("the two offer columns stack before they get too narrow to read",
      CSS.includes("@media (max-width: 900px)")
      && /@media \(max-width: 900px\)[\s\S]*?#cc-trade-cols \{[^}]*grid-template-columns: 1fr/.test(CSS));
check("the two sides of the projection stack on a phone",
      /@media \(max-width: 560px\)[\s\S]*?\.cctr-pk-swing \{[^}]*grid-template-columns: 1fr/.test(CSS));

console.log("\nthe explainer reads across the sheet, not down a ribbon");
{
  // It is a child of .cctr-pk-body, which is a tile GRID. Laid into one ~104px
  // column it reads one word per line down a thin ribbon with the rest of the
  // sheet empty beside it — which is exactly what shipped, because the rule
  // that fixed it went out behind an unchanged ?v= cache buster.
  const note = CSS.slice(CSS.indexOf("    .cctr-coin-note {"));
  check("the picker note spans the whole grid",
        /grid-column:\s*1\s*\/\s*-1/.test(note.slice(0, 400)), note.slice(0, 120));
  check("…and the tile grid is what makes that necessary",
        /\.cctr-pk-body \{[\s\S]*?display: grid/.test(CSS));
  check("belt and braces: the amount tabs stop the body being a grid at all",
        /\.cctr-pk-body\.cctr-pk-prose \{ display: block; \}/.test(CSS)
        && /body\.classList\.toggle\("cctr-pk-prose", prose\);/.test(APP));
  check("…on all three of them", /const prose = \(_trPickerTab === "coins" \|\| _trPickerTab === "passes" \|\| _trPickerTab === "xp"\);/.test(APP));
  check("the empty-state message spans it too",
        /\.cctr-pk-empty \{[^}]*grid-column: 1\/-1/.test(CSS));
}

/* ══════════════════════════════════════════════════════════════════════
   8. THE CACHE BUSTER
   The bug this file was rewritten for was not a logic bug. preview.html is
   served unversioned, but every script and stylesheet it pulls carries a
   ?v= stamp, so shipping new markup without bumping the stamp serves new
   HTML against last week's CSS and JS. That is what put two unstyled,
   unfilled "Level -" cards on the trade screen.
   ══════════════════════════════════════════════════════════════════════ */
console.log("\nthe assets this markup needs are asked for by their new name");
{
  const stamp = JSON.parse(fs.readFileSync(path.join(ROOT, "multiplayer/client/version.json"), "utf8")).build;
  check("version.json carries a build stamp", !!stamp, stamp);
  check("APP_BUILD matches it, which is what the client checks itself against",
        APP.includes('const APP_BUILD   = "' + stamp + '";'), stamp);
  // Every /js/ and /css/ asset preview.html asks for, by the stamp it asks for.
  const vs = new Set((HTML.match(/\/(?:js|css)\/[A-Za-z0-9._-]+\?v=[^"'\s>]+/g) || [])
                       .map(x => x.slice(x.indexOf("?v=") + 3)));
  check("every script and stylesheet is asked for at the current stamp",
        vs.size === 1 && vs.has(stamp), [...vs].join(", ") + " vs " + stamp);
  check("…and there are some, so this check cannot pass vacuously",
        (HTML.match(/\/(?:js|css)\/[A-Za-z0-9._-]+\?v=/g) || []).length > 20);
}

console.log("\nit is painted like the rest of the game");
check("the box is the light ocean card, not the old navy glass",
      /#cc-trade-box \{[\s\S]*?#f2fbff[\s\S]*?\}/.test(CSS));
check("…with the Player Home heading colour",
      /#cc-trade-title \{[\s\S]*?color: #15407e;/.test(CSS));
check("…and the same Cinzel title as the player picker it opens from",
      /#cc-trade-title \{[\s\S]*?font-family: "Cinzel"/.test(CSS));
check("no dark navy panel is left behind",
      !CSS.includes("linear-gradient(160deg, rgba(22,58,118,.99) 0%, rgba(11,30,68,.99) 100%)"));
check("the picker sheet does not black out the offers behind it",
      /#cc-trade-picker \{[\s\S]*?background: rgba\(20,70,130,\.28\)/.test(CSS));
check("…and is short enough to leave them on screen",
      /#cc-trade-picker-box \{[\s\S]*?max-height: 62vh/.test(CSS));

const need = [".cctr-pk-swing", ".cctr-swing-head", ".cctr-swing-side", ".cctr-swing-who",
              ".cctr-swing-val", ".cctr-swing-lvl", ".cctr-swing-arrow", ".cctr-purse-was"];
for (const sel of need) check(`preview.css styles ${sel}`, CSS.includes("\n    " + sel + " ") || CSS.includes("\n    " + sel + ","));
check("up and down are coloured apart, on the level and on the amount",
      CSS.includes(".cctr-swing-lvl.up") && CSS.includes(".cctr-swing-lvl.down")
      && CSS.includes(".cctr-swing-side.up") && CSS.includes(".cctr-swing-side.down"));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
