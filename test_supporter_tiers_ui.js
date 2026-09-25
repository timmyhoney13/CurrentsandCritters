#!/usr/bin/env node
/* The Supporter Tier shelf in the in-game Store, rendered for real.
 *
 * Run:  node test_supporter_tiers_ui.js
 *
 * The Store is OPEN (PHST_STORE_CLOSED false), and its four SUPPORTER TIERS are
 * shown but not sold: they are backed through the Kickstarter, and
 * PHST_TIERS_KICKSTARTER_ONLY locks their buttons. So the shelf is rendered
 * THREE ways here, because each one proves something the others cannot:
 *
 *   1. As shipped. The shelf a player actually meets: coin packs buyable, tier
 *      cards complete and every tier button disabled, opening nothing.
 *   2. With the store forced shut. The standby cover is one word away at all
 *      times, and a cover nobody renders is a cover nobody notices going wrong.
 *   3. With the tier lock forced off. The four live Payment Links are still in
 *      the file waiting for the day the Kickstarter ends, and this is what keeps
 *      each one paired with the right price while nothing points at it.
 *
 * A tier is granted by the PRICE of the link its button opens, so a tier whose
 * checkout is not live yet must render a locked button and NOT a live-looking
 * one pointed at some other product, which would charge the wrong amount and
 * grant the wrong tier with no visible symptom. Grepping the source for
 * "soon: true" proves the DATA says so; only rendering proves the BUTTON does.
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

const CLOSED_FLAG = grabBlock("const PHST_STORE_CLOSED = ", ";");
const KS_FLAG     = grabBlock("const PHST_TIERS_KICKSTARTER_ONLY = ", ";");
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
// One fresh context per render: both flags are `const`, so two shelves rendered
// differently cannot share a sandbox.
function renderWith(flagSource, ksSource) {
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
  vm.runInContext([EMAIL, TPL, PACKS, TIERS, PHYS, flagSource,
                   ksSource || KS_FLAG, RENDER, CUSTOM].join("\n"), sandbox);
  vm.runInContext("renderPhStore();", sandbox);
  return { html: shelf.innerHTML, sandbox, shelf };
}

const SHIPPED = renderWith(CLOSED_FLAG);                        // exactly as shipped
const SHUT    = renderWith("const PHST_STORE_CLOSED = true;");  // the standby cover
const LIVE    = renderWith("const PHST_STORE_CLOSED = false;",  // tiers unlocked
                           "const PHST_TIERS_KICKSTARTER_ONLY = false;");
const sandbox = SHIPPED.sandbox;
const shelf = SHIPPED.shelf;
const HTML = SHIPPED.html;

console.log("\nthe store is open");
check(/const PHST_STORE_CLOSED = false;/.test(APP),
      "the flag in the shipped file is OFF");
check(!/phst-closed/.test(HTML),
      "the shelf does not paint a Coming soon panel");
check(/phst-coin-grid/.test(HTML), "the Critter Coin packs are on the shelf");
check(/phst-tier-grid/.test(HTML), "the Supporter Tiers are on the shelf");

console.log("\nthe top of the shelf");
// The pledge and the Kickstarter line are true of every row below them, so they
// sit above the first section title rather than inside any one of them.
check(/Kickstarter coming soon/.test(HTML), "the Kickstarter strip is at the top");
check(/Every Purchase Makes Waves!/.test(HTML), "the pledge headline is there");
check(/5% of every purchase supports ocean conservation/.test(HTML),
      "the pledge says 5%");
check(/Surfrider Foundation/.test(HTML), "and names the Surfrider Foundation");
check(/independent supporter and is not sponsored by or officially partnered with the Surfrider Foundation/.test(HTML),
      "the independence disclaimer is on the shelf, not just in the commit");
// The banner must come BEFORE the first thing for sale, or it is a footnote.
check(HTML.indexOf("Every Purchase Makes Waves!") < HTML.indexOf("phst-coin-grid"),
      "the pledge sits above the first thing for sale");
check(HTML.indexOf("Kickstarter coming soon") < HTML.indexOf("Every Purchase Makes Waves!"),
      "the Kickstarter line sits above the pledge");

console.log("\nthe tiers are shown, and sold nowhere");
check(/const PHST_TIERS_KICKSTARTER_ONLY = true;/.test(APP),
      "the tier lock in the shipped file is ON");
const shippedTierBlock = HTML.slice(HTML.indexOf('class="phst-tier-grid"'),
                                    HTML.indexOf('class="phst-custom-tier"'));
check(!/data-stripe/.test(shippedTierBlock),
      "no tier card opens a Stripe checkout");
check(!/buy\.stripe\.com/.test(shippedTierBlock),
      "no Payment Link reaches a tier card");
check((shippedTierBlock.match(/phst-tier-soon/g) || []).length === 4,
      "all four tier buttons are locked");
check((shippedTierBlock.match(/On Kickstarter soon/g) || []).length === 4,
      "and all four say why");
check((shippedTierBlock.match(/<button/g) || []).length ===
      (shippedTierBlock.match(/disabled/g) || []).length,
      "every button on a tier card is disabled");
check(/The Supporter Tiers will be available through Kickstarter soon!/.test(HTML),
      "a line under the grid says where they will be");
check(HTML.indexOf("The Supporter Tiers will be available through Kickstarter soon!")
        > HTML.indexOf('class="phst-tier-grid"'),
      "...and it is BELOW the tiers, where the reader ends up");
// The coin packs are a different product and must still be buyable.
check(/data-stripe/.test(HTML.slice(HTML.indexOf("phst-coin-grid"),
                                    HTML.indexOf("phst-tier-grid"))),
      "the coin packs still take money");

console.log("\nthe shelf is still one word from shut");
check(/phst-closed/.test(SHUT.html) && /Coming soon/.test(SHUT.html),
      "forced shut, it paints one Coming soon panel");
check(!/<button/.test(SHUT.html),
      "there is no button of any kind on the closed shelf");
// Same three ways of asking as the Critter Pass's own closed check: a page is
// only shut when there is nothing on it to reach, and "no <button>" alone
// would miss a link, an input or anything given a tabindex.
check(!/<a\s/.test(SHUT.html),
      "there is no link either");
check(!/tabindex|<input|<select|<textarea|onclick=|contenteditable/.test(SHUT.html),
      "and nothing on it can even be tabbed to");
check(!/data-stripe/.test(SHUT.html),
      "nothing on it opens a Stripe checkout");
check(!/data-skin=|data-bg=|data-perk=|data-custom-tier/.test(SHUT.html),
      "and nothing on it spends Critter Coins either");
check(!/phst-tier-grid|phst-coin-grid|phst-perk-grid/.test(SHUT.html),
      "no tier grid, no coin packs, no perks: the shelf itself is gone");
check(!/buy\.stripe\.com/.test(SHUT.html),
      "no Payment Link reaches the page while the store is shut");

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

console.log("\nas shipped, no tier card can be bought by accident");
for (const [cents, tier] of Object.entries(byCents)) {
  const usd = Number(cents) / 100;
  const card = cards.find((c) => c.includes(`$${usd.toFixed(2)}`)) || "";
  check((card.match(/data-stripe="([^"]*)"/g) || []).length === 0,
        `${tier}: opens NO Payment Link`);
  check(/phst-tier-soon/.test(card) && /disabled/.test(card),
        `${tier}: locked, and the button is disabled`);
  check(!/data-custom-tier/.test(card),
        `${tier}: no "email us" line hung under it`);
}

// ── the four links, checked while nothing points at them ────────────────
// The day the Kickstarter ends, this lock comes off and these buttons go live
// as they are. A tier is granted by the PRICE of the link it opens, so a card
// paired with the wrong URL would charge the wrong amount and grant the wrong
// tier with no visible symptom. That cannot wait to be noticed on the day.
console.log("\nwith the lock off, every tier pairs with the right link");
const liveCards = LIVE.html
  .slice(LIVE.html.indexOf('class="phst-tier-grid"'),
         LIVE.html.indexOf('class="phst-custom-tier"'))
  .split(/<div class="phst-tier[" ]/).slice(1);
check(liveCards.length === Object.keys(byCents).length,
      `one unlocked card per tier (${liveCards.length} of ${Object.keys(byCents).length})`);
for (const [cents, tier] of Object.entries(byCents)) {
  const usd = Number(cents) / 100;
  const card = liveCards.find((c) => c.includes(`$${usd.toFixed(2)}`)) || "";
  const links = card.match(/data-stripe="([^"]*)"/g) || [];
  check(links.length === 1, `${tier}: exactly one Buy link when unlocked`);
  check(/buy\.stripe\.com/.test(links[0] || ""),
        `${tier}: that link is a real Stripe Payment Link`);
  check(!/phst-tier-soon/.test(card), `${tier}: unlocked, so the button is live`);
}
// …and every link the unlocked shelf emits is a well-formed live one.
for (const m of LIVE.html.matchAll(/data-stripe="([^"]+)"/g)) {
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
for (const m of (HTML + SHUT.html + LIVE.html + dlg.innerHTML)
                  .matchAll(/class="([^"]+)"/g)) {
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
