#!/usr/bin/env node
/* The Supporter Tier shelf in the in-game Store, rendered for real.
 *
 * Run:  node test_supporter_tiers_ui.js
 *
 * The dearest tier (Tsunami, $100) has no Stripe Payment Link yet. That is the
 * whole risk this file exists for: a tier is granted by the PRICE of the link
 * its button opens, so a locked tier that renders a live-looking Buy button
 * pointed at some other product would charge the wrong amount and grant the
 * wrong tier with no visible symptom. Grepping the source for "soon: true"
 * proves the DATA says so; only rendering proves the BUTTON does.
 *
 * Above $100 there is no button at all, by design: renderPhStore paints a card
 * that opens a ready-written email instead, and that template has to keep the
 * placeholders that tell a reader what to replace.
 *
 * renderPhStore() is lifted out of preview-app.js and run against a stub DOM,
 * exactly the way test_store_perks.js runs the perk helpers. No browser.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = __dirname;
const APP = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const SERVER = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");

let failures = 0, checks = 0;
function check(cond, label) {
  checks++;
  if (!cond) { failures++; console.log("  ✗ " + label); }
  else console.log("  ✓ " + label);
}

/* ── lift the real code out of the app's IIFE ─────────────────────────── */
function grabBlock(startsWith, endsWith) {
  const a = APP.indexOf(startsWith);
  if (a < 0) throw new Error("not found: " + startsWith);
  const b = APP.indexOf(endsWith, a);
  if (b < 0) throw new Error("unterminated: " + startsWith);
  return APP.slice(a, b + endsWith.length);
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

const PACKS  = grabBlock("const PHST_COIN_PACKS = [", "\n      ];");
const TIERS  = grabBlock("const PHST_SUPPORTER_TIERS = [", "\n      ];");
const PHYS   = grabBlock("const PHST_PHYSICAL = [", "];");
const RENDER = grabFn("renderPhStore", 6);
const CUSTOM = grabBlock("window._phstCustomTier = function () {", "\n      };");
const TPL    = grabBlock("const PHST_CUSTOM_TEMPLATE = [", '].join("\\n");');
const EMAIL  = grabBlock('const PHST_CONTACT_EMAIL = "', '";');

/* ── the smallest DOM renderPhStore can paint into ───────────────────── */
function fakeNode() {
  return {
    innerHTML: "",
    classList: { add() {}, remove() {} },
    querySelectorAll: () => [],
    querySelector: () => null,
    addEventListener() {},
    appendChild() {},
    setAttribute() {}, getAttribute: () => null,
    focus() {}, select() {},
  };
}
const shelf = fakeNode();
const sandbox = {
  console,
  document: {
    getElementById: (id) => (id === "ph-store-content" ? shelf : null),
    createElement: () => fakeNode(),
    body: { appendChild() {} },
    addEventListener() {}, removeEventListener() {},
  },
  window: {},
  navigator: {},
  phstFmtCoins: (n) => Number(n).toLocaleString("en-US"),
  escapeHtml: (s) => String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
  _authUser: { uid: "u1" },
};
sandbox.window.window = sandbox.window;
vm.createContext(sandbox);
vm.runInContext([EMAIL, TPL, PACKS, TIERS, PHYS, RENDER, CUSTOM].join("\n"), sandbox);
vm.runInContext("renderPhStore();", sandbox);
const HTML = shelf.innerHTML;

/* ── what the server says the shelf must sell ────────────────────────── */
function pyTable(name) {
  const block = SERVER.slice(SERVER.indexOf(name + " = {"));
  return block.slice(0, block.indexOf("\n}"));
}
const byCents = {};
for (const m of pyTable("SUPPORTER_TIERS_BY_CENTS").matchAll(/(\d+):\s*"([a-z-]+)"/g)) {
  byCents[Number(m[1])] = m[2];
}
const grants = {};
for (const m of pyTable("SUPPORTER_TIER_GRANTS").matchAll(
       /"([a-z-]+)":\s*\{"coins":\s*(\d+),\s*"bonus_xp":\s*(\d+),\s*"pass_vouchers":\s*(\d+)/g)) {
  grants[m[1]] = { coins: +m[2], bonus_xp: +m[3], vouchers: +m[4] };
}

console.log("\nthe shelf");
// One card per opening <div>, bounded by the tier grid itself. `phst-tier` is
// also the prefix of half the classes INSIDE a card, so the split has to
// require a quote or a space straight after it (`phst-tier"` / `phst-tier `)
// or every card comes apart into confetti at its own badge and name.
const shelfHtml = HTML.slice(HTML.indexOf('class="phst-tier-grid"'),
                             HTML.indexOf('class="phst-custom-tier"'));
const cards = shelfHtml.split(/<div class="phst-tier[" ]/).slice(1);
check(cards.length === Object.keys(byCents).length,
      `one card per tier (${cards.length} of ${Object.keys(byCents).length})`);
check(/Wave Warrior/.test(HTML) && /Tsunami/.test(HTML),
      "every tier name reaches the page");
check(!/Riptide/.test(HTML), "the retired Riptide tier is gone from the shelf");

// Two ribbons, two claims, one each: MOST POPULAR on what people pick, BEST
// VALUE on what the money buys. Both on one card reads as neither.
const popCards  = cards.filter((c) => /MOST POPULAR/.test(c));
const bestCards = cards.filter((c) => /BEST VALUE/.test(c));
check(popCards.length === 1 && /\$35\.00/.test(popCards[0]),
      "MOST POPULAR sits on the $35 tier, and only there");
check(bestCards.length === 1 && /\$100\.00/.test(bestCards[0]),
      "BEST VALUE sits on the $100 tier, and only there");
check(!popCards.some((c) => /BEST VALUE/.test(c)),
      "no card wears both ribbons at once");

console.log("\nprices and grants, as painted");
for (const [cents, tier] of Object.entries(byCents)) {
  const usd = Number(cents) / 100;
  const card = cards.find((c) => c.includes(`$${usd.toFixed(2)}`));
  check(!!card, `${tier}: a card priced $${usd.toFixed(2)}`);
  if (!card) continue;
  const g = grants[tier];
  check(card.includes(g.coins.toLocaleString("en-US")),
        `${tier}: the card shows ${g.coins.toLocaleString("en-US")} coins`);
  check(card.includes(`+${g.bonus_xp.toLocaleString("en-US")} bonus XP`),
        `${tier}: the card shows +${g.bonus_xp.toLocaleString("en-US")} bonus XP`);
  const phrase = `${g.vouchers} Season Pass voucher` + (g.vouchers === 1 ? "" : "s");
  check(card.includes(phrase), `${tier}: the card shows ${phrase}`);
}

console.log("\na tier with no Payment Link cannot be bought by accident");
for (const [cents, tier] of Object.entries(byCents)) {
  const usd = Number(cents) / 100;
  const card = cards.find((c) => c.includes(`$${usd.toFixed(2)}`)) || "";
  const links = card.match(/data-stripe="([^"]*)"/g) || [];
  const locked = /phst-tier-soon/.test(card);
  if (locked) {
    check(links.length === 0,
          `${tier}: locked, so it opens NO Payment Link`);
    check(/disabled/.test(card), `${tier}: locked, so the button is disabled`);
    check(!/data-custom-tier/.test(card),
          `${tier}: locked, with no "email us" line hung under it`);
  } else {
    check(links.length === 1, `${tier}: exactly one Buy link`);
    check(/buy\.stripe\.com/.test(links[0] || ""),
          `${tier}: that link is a real Stripe Payment Link`);
  }
}
// …and every price on the shelf is one the webhook actually knows.
for (const m of HTML.matchAll(/data-stripe="([^"]+)"/g)) {
  check(/^https:\/\/buy\.stripe\.com\/[A-Za-z0-9]+$/.test(m[1]),
        "every Buy button opens a live Stripe link: " + m[1]);
}

console.log("\nabove the top tier: a message, not a checkout");
check(/phst-custom-tier/.test(HTML), "the over-$100 card is on the shelf");
check(/Giving more than \$100\?/.test(HTML), "and it says what it is for");
check((HTML.match(/data-custom-tier/g) || []).length >= 1,
      "it opens the template rather than a payment link");

const dlg = fakeNode();
sandbox.document.createElement = () => dlg;
sandbox.document.getElementById = (id) => (id === "ph-store-content" ? shelf : null);
vm.runInContext("window._phstCustomTier();", sandbox);
const D = dlg.innerHTML;
check(/\[INSERT YOUR NAME HERE\]/.test(D), "the template says where the name goes");
check(/\[INSERT AMOUNT HERE/.test(D), "the template says where the amount goes");
check(/currentsandcritters@gmail\.com/.test(D), "it names the inbox it is going to");
check(/data-act="mail"/.test(D) && /data-act="copy"/.test(D),
      "both ways out are offered (a mailto: opens nothing for some readers)");

console.log("\nthe styles the render depends on");
// Every class the render actually emits, checked ON ITS OWN. An `||` fallback
// here would have let a deleted rule pass on the strength of its neighbour.
const emitted = new Set();
for (const m of (HTML + dlg.innerHTML).matchAll(/class="([^"]+)"/g)) {
  for (const c of m[1].split(/\s+/)) if (/^(phst-|cctm-)/.test(c)) emitted.add(c);
}
for (const cls of emitted) {
  check(CSS.includes("." + cls), `preview.css styles .${cls}`);
}
check(!CSS.includes(".phst-tier-soonnote"),
      "the retired soonnote rule went with the markup that used it");

console.log(`\nsupporter-tier UI checks: ${checks}`);
if (failures) { console.log(`${failures} FAILED`); process.exit(1); }
console.log("supporter tiers OK");
