#!/usr/bin/env node
/* The trade ledger: both players, and what the trade does to each of them.
 *
 * Run:  node test_trade_ledger.js         (no browser needed)
 *
 * The trade screen used to list the items being swapped and nothing else, so
 * the question a player actually has — "what does that leave me at?" — was
 * answered by a paragraph of small print in the XP picker. It now shows both
 * sides: avatars, levels, and every figure as NOW → AFTER, moving while the
 * amount is being typed.
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
 * The rest of the file checks the wiring and the CSS the arithmetic is drawn
 * with, since a correct number painted into a hidden element helps nobody.
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
   A ledger that only subtracts your own offer is lying whenever the other
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
  // The ledger floors at zero rather than showing a player a negative total.
  check("an over-offer floors at zero instead of going negative",
        r.them.after.xp === 0, r.them.after.xp);
  check("a swap that nets out leaves both where they started",
        model(purse(100, 0, 0), purse(100, 0, 0), offer({ coins: 50 }), offer({ coins: 50 }))
          .me.after.coins === 100);
}

/* ══════════════════════════════════════════════════════════════════════
   3. THE UNKNOWN SIDE
   The peer's profile can fail to load. The trade still works, so the
   ledger has to cope rather than throw or invent numbers.
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
console.log("\nthe ledger survives anything typed into the box");
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
   ══════════════════════════════════════════════════════════════════════ */
console.log("\nboth players are on screen, with an icon over each level");
for (const side of ["me", "them"]) {
  check(`the ${side} card has an avatar`, HTML.includes(`id="cctr-${side}-av"`));
  check(`…a level`, HTML.includes(`id="cctr-${side}-lvl"`));
  check(`…the level it would become`, HTML.includes(`id="cctr-${side}-lvl-after"`));
  check(`…a level bar with a projection over it`,
        HTML.includes(`id="cctr-${side}-fill"`) && HTML.includes(`id="cctr-${side}-ghost"`));
  check(`…and a purse`, HTML.includes(`id="cctr-${side}-purse"`));
}
check("the avatar sits above the level, not beside the name",
      HTML.indexOf('id="cctr-me-av"') < HTML.indexOf('id="cctr-me-lvl"'));

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
check("the swing is repeated inside the picker, over the covered ledger",
      APP.includes("function _trRenderSwing(model)") && HTML.includes('id="cc-trade-swing"'));
check("the ledger repaints on every render of the trade",
      APP.includes("_trRenderLedger();\n"));
check("the peer's profile is fetched when the trade opens",
      APP.includes("_trLoadPeer(_trPeerUid);"));
check("…without blocking the open on it",
      !APP.includes("await _trLoadPeer("));
check("a stale profile never lands on a new trade",
      APP.includes('if (!prof || String(uid) !== String(_trPeerUid)) return;'));
check("the ledger is cleared between trades",
      (APP.match(/_trPeer = null; _trDraft = null;/g) || []).length === 2);

console.log("\nwhat the peer's card may and may not say");
check("their level is shown in full: it is public on the leaderboard",
      APP.includes('_trPaintSide("them"') && APP.includes("function _trLevelOf(totalXp)"));
check("their balances are shown as a gain, not as a balance",
      APP.includes("function _trDeltaChipHtml(icon, delta, suffix)"));
check("…and the reveal flag is what decides it",
      APP.includes('_trPaintSide("me", "You", myAvatar, model.me, true);')
      && APP.includes('(_trPeer && _trPeer.avatar) || "", model.them, false);'));
check("the swing follows the same rule",
      APP.includes('sideHtml((_trPeer && _trPeer.name) || _trPeerName || "Them", model.them, false)'));

console.log("\nthe offers go across the screen, not down a thin column");
const items = CSS.slice(CSS.indexOf("    .cctr-items {"), CSS.indexOf("    .cctr-item {"));
check("the item list is a grid", /display:\s*grid/.test(items), items.trim().slice(0, 80));
check("…that flows across in columns", /repeat\(auto-fill,\s*minmax\(/.test(items));
check("…and is not the old single-file flex column",
      !/flex-direction:\s*column/.test(items), items.trim().slice(0, 80));
check("the box is wide enough for two of them side by side",
      /width:\s*min\(1180px,\s*96vw\)/.test(CSS));
check("the sides stack before they get too narrow to read",
      CSS.includes("@media (max-width: 900px)")
      && /@media \(max-width: 900px\)[\s\S]*?#cc-trade-ledger \{[^}]*grid-template-columns: 1fr/.test(CSS));

console.log("\nthe explainer reads across the sheet, not down a ribbon");
{
  // It is a child of .cctr-pk-body, which is a tile GRID. Without an explicit
  // span it is laid into one ~104px column, one word per line.
  const note = CSS.slice(CSS.indexOf("    .cctr-coin-note {"));
  check("the picker note spans the whole grid",
        /grid-column:\s*1\s*\/\s*-1/.test(note.slice(0, 400)), note.slice(0, 120));
  check("…and the tile grid is what makes that necessary",
        /\.cctr-pk-body \{[\s\S]*?display: grid/.test(CSS));
  check("the empty-state message spans it too",
        /\.cctr-pk-empty \{[^}]*grid-column: 1\/-1/.test(CSS));
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
check("the picker no longer blacks out the ledger behind it",
      /#cc-trade-picker \{[\s\S]*?background: rgba\(20,70,130,\.28\)/.test(CSS));
check("…and is short enough to leave it on screen",
      /#cc-trade-picker-box \{[\s\S]*?max-height: 62vh/.test(CSS));

const need = ["#cc-trade-ledger", ".cctr-side", ".cctr-side-av", ".cctr-lvl-now", ".cctr-lvl-after",
              ".cctr-lvl-bar", ".cctr-lvl-fill", ".cctr-lvl-ghost", ".cctr-lvl-xp", ".cctr-purse",
              ".cctr-purse-chip", ".cctr-purse-was", ".cctr-swap", ".cctr-pk-swing",
              ".cctr-swing-side", ".cctr-swing-who", ".cctr-swing-val", ".cctr-swing-lvl"];
for (const sel of need) check(`preview.css styles ${sel}`, CSS.includes("\n    " + sel + " ") || CSS.includes("\n    " + sel + ","));
check("up and down are coloured apart in both places",
      CSS.includes(".cctr-purse-chip.up") && CSS.includes(".cctr-purse-chip.down")
      && CSS.includes(".cctr-lvl-ghost.up") && CSS.includes(".cctr-lvl-ghost.down"));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
