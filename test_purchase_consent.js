#!/usr/bin/env node
/* Digital-purchase consent, and the shop page's buttons.
 *
 * Run:  node test_purchase_consent.js     (no browser, no network needed)
 *
 * ── WHAT IS ACTUALLY SOLD HERE ──────────────────────────────────────────
 * An audit on 2026-10-15 sorted every price on the site into categories,
 * because the refund law that applies is not the same for all of them:
 *
 *   • CRITTER COINS, $1/$5/$10/$20. Digital, and delivered the moment the
 *     webhook confirms payment. THIS is the category that needs an EU/UK
 *     consumer to agree to supply starting during the withdrawal period and
 *     to acknowledge what that costs them, BEFORE delivery.
 *   • SUPPORTER TIERS, $15 to $100. Display-locked: they render with prices
 *     and perks and have no checkout on either surface.
 *   • THE PHYSICAL CARD GAME, $24.99 on shop.html. Not sold here at all. It
 *     is going to Kickstarter, and the Refund Policy says so.
 *   • THE CRITTER PASS AND LEVEL PASS. Bought with Critter Coins, which are
 *     not money. Not a consumer purchase and not in scope here.
 *
 * So exactly one real-money product is immediately-delivered digital content,
 * and this file pins the acknowledgment onto the one function that can send a
 * buyer to Stripe.
 *
 * ── WHAT WAS WRONG WITH THE SHOP ────────────────────────────────────────
 * shop.html's BUY NOW button ran
 *     showToast('Pre-order confirmed - thanks for making waves')
 * and nothing else. No payment, no order, no record: a confirmation of a
 * purchase that had not happened, on a page offering a $24.99 product, while
 * the Refund Policy stated that no preorders are taken on this site. ADD TO
 * CART said a thing had been added to a cart that does not exist.
 *
 * ── WHAT IS NOT CLAIMED ─────────────────────────────────────────────────
 * Capturing this acknowledgment does not make the withdrawal right disappear.
 * Whether a waiver holds is a question about each buyer's jurisdiction, and
 * the Refund Policy does not pretend otherwise.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = __dirname;
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), "utf8");

const APP    = read("multiplayer", "client", "js", "preview-app.js");
const SHOP   = read("shop.html");
const REFUND = read("multiplayer", "client", "js", "refund-policy.js");
const SERVER = read("multiplayer_server.py");

let pass = 0;
const failures = [];
function check(name, cond) {
  if (cond) { console.log("  ✓ " + name); pass++; }
  else { console.log("  ✗ FAIL: " + name); failures.push(name); }
}
function section(t) { console.log("\n" + t); }
const words = (s) => s.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
// Visible markup only: shop.html carries a comment ABOUT the fake toast it
// used to show, and that comment must not be what fails these checks.
const nocomment = (s) => s.replace(/<!--[\s\S]*?-->/g, "");
const SHOP_LIVE = nocomment(SHOP);

// ════════════════════════════════════════════════════════════════════════
section("1. Nothing reaches Stripe without the acknowledgment");
// ════════════════════════════════════════════════════════════════════════

const GO = (() => {
  const i = APP.indexOf("function _phstGoToStripe(");
  if (i < 0) return "";
  return APP.slice(i, APP.indexOf("\n        }", i) + 10);
})();
check("_phstGoToStripe was found", GO.length > 100);

// Every coin pack and every tier card goes through this one function, so the
// gate belongs here and not on each button.
check("it asks for the acknowledgment before redirecting",
  /ccPurchaseConsent/.test(GO));
check("…and the redirect is downstream of it, not beside it",
  GO.indexOf("ccPurchaseConsent") < GO.indexOf("window.location.href"));
check("sign-in is still required first",
  /_authUser/.test(GO) && GO.indexOf("_authUser") < GO.indexOf("ccPurchaseConsent"));

const GATE = (() => {
  // From the acknowledgement wording through the end of the function, so the
  // sentence the buyer is shown is inside what is being asserted about.
  const i = APP.indexOf("const CC_PURCHASE_ACK_TEXT");
  if (i < 0) return "";
  const j = APP.indexOf("function ccPurchaseConsent(", i);
  if (j < 0) return "";
  return APP.slice(i, APP.indexOf("\n    }\n", j) + 6);
})();
check("ccPurchaseConsent exists", GATE.length > 400);

// The MARKUP, not the reads of .checked that the handler makes.
check("the checkbox starts unchecked",
  /<input type="checkbox" id="cc-pc-ack" \/>/.test(GATE));
check("the continue button starts disabled",
  /disabled/.test(GATE));
check("the acknowledgment names immediate delivery",
  /as soon as (my|your) payment is confirmed|immediately/i.test(words(GATE)));
check("…and says plainly what is given up",
  /right to cancel|right of withdrawal/i.test(words(GATE)));
check("the Refund Policy is linked from the step",
  /href="\/refunds"/.test(GATE));
check("a buyer can still back out",
  /Cancel|cancel/.test(GATE));

// ACCESSIBILITY: this is a dialog that takes a decision, so it has to say so.
check("the step is announced as a dialog",
  /setAttribute\("role", "dialog"\)/.test(GATE) && /aria-modal/.test(GATE));
check("its checkbox has a bound label",
  /<label[^>]*for="cc-pc-ack"/.test(GATE));
check("the heading is tied to the dialog",
  /aria-labelledby/.test(GATE));

// ════════════════════════════════════════════════════════════════════════
section("2. What is recorded, and where");
// ════════════════════════════════════════════════════════════════════════

check("the acknowledgment is sent to the server before the redirect",
  /\/api\/purchase\/consent/.test(GATE));
check("the exact wording goes with it, not just a boolean",
  /ack_text|consent_text/.test(GATE));
check("so does what was being bought",
  /product/.test(GATE));

check("the server has the endpoint",
  /\/api\/purchase\/consent/.test(SERVER));
check("…it demands a verified token",
  /_verify_firebase_id_token/.test(SERVER.slice(
    SERVER.indexOf('"/api/purchase/consent"'),
    SERVER.indexOf('"/api/purchase/consent"') + 2600)));
check("…and stamps the record with the server's clock",
  /_record_purchase_consent/.test(SERVER));

const REC = (() => {
  const i = SERVER.indexOf("def _record_purchase_consent(");
  if (i < 0) return "";
  return SERVER.slice(i, i + 2600);
})();
check("the record goes to Firestore, which survives a restart",
  /_get_firestore/.test(REC));
check("…with a server timestamp",
  /SERVER_TIMESTAMP/.test(REC));
const VALIDATE = (() => {
  const i = SERVER.indexOf("def _purchase_consent_from_body(");
  return i < 0 ? "" : SERVER.slice(i, i + 2200);
})();
check("every stored string is bounded before it is written",
  /\[:_PURCHASE_ACK_TEXT_MAX\]/.test(VALIDATE)
  && /\[:_PURCHASE_PRODUCT_MAX\]/.test(VALIDATE));
check("the acknowledgment must be a real boolean, not a truthy string",
  /is not True/.test(VALIDATE));
check("a failed record does not take the buyer's money silently",
  /return/.test(REC));

// ════════════════════════════════════════════════════════════════════════
section("2b. The step is actually styled");
// ════════════════════════════════════════════════════════════════════════
// ccPurchaseConsent builds its markup in JS, so a missing stylesheet does not
// break any source-level check: it just renders an unstyled pile of text at
// the end of <body>, with no overlay, no card and no centring. That shipped
// once and was caught by eye, not by a test. It is a test now.
{
  const css = read("multiplayer", "client", "css", "preview.css");
  for (const cls of ["cc-purchase-ack", "cc-pc-card", "cc-pc-check", "cc-pc-go",
                     "cc-pc-cancel", "cc-pc-msg", "cc-pc-fine", "cc-pc-what",
                     "cc-pc-row"]) {
    check(`.${cls} is styled`, css.includes("." + cls));
  }
  // The things that make it a dialog rather than a block of text.
  const block = css.slice(css.indexOf(".cc-purchase-ack {"));
  check("it covers the screen", /position:\s*fixed/.test(block) && /inset:\s*0/.test(block));
  check("it sits above the game", /z-index:\s*\d{5,}/.test(block));
  check("its card is centred", /align-items:\s*center/.test(block));
  check("a disabled continue looks disabled",
    /\.cc-pc-go\[disabled\][\s\S]{0,120}opacity/.test(css));
  check("it has a visible focus ring", /\.cc-purchase-ack [^{]*:focus-visible/.test(css));
  check("it honours reduced motion", /prefers-reduced-motion[\s\S]{0,160}cc-purchase-ack/.test(css)
    || /cc-purchase-ack[\s\S]{0,160}prefers-reduced-motion/.test(css));
}

// ════════════════════════════════════════════════════════════════════════
section("3. The shop page no longer confirms purchases that never happened");
// ════════════════════════════════════════════════════════════════════════

check("BUY NOW does not fake a pre-order confirmation",
  !/Pre-?order confirmed/i.test(SHOP_LIVE));
check("ADD TO CART does not claim a cart that does not exist",
  !/Added \$\{qty\}|to cart`/i.test(SHOP_LIVE));
check("the page says where the physical game is actually sold",
  /Kickstarter/i.test(SHOP_LIVE));
check("…and the Refund Policy agrees with it",
  /Kickstarter/i.test(REFUND));

// If a price is shown, it must not be presented as buyable today.
check("no button on the shop page claims to take an order",
  !/id="buyNow"/.test(SHOP_LIVE) && !/id="addToCart"/.test(SHOP_LIVE));
check("…and it offers the one true next step instead",
  /newsletter\/join/.test(SHOP_LIVE));

// ════════════════════════════════════════════════════════════════════════
section("4. The Refund Policy describes the checkout that exists");
// ════════════════════════════════════════════════════════════════════════

check("it says the acknowledgment is taken at checkout",
  /before you are sent to|at checkout|before we send you to/i.test(words(REFUND)));
check("it does not claim the waiver always holds",
  /may still|depending on where you live|mandatory|cannot be signed away/i
    .test(words(REFUND)));
check("billing errors and non-delivery are still honoured",
  /billing error/i.test(words(REFUND)));
check("it still says no preorders are taken here",
  /no preorders|not taking preorders|do not take preorders/i.test(words(REFUND)));

// ────────────────────────────────────────────────────────────────────────
console.log(`\npurchase-consent checks: ${pass + failures.length}`);
if (failures.length) {
  console.log(`${failures.length} FAILED`);
  failures.forEach((f) => console.log("  FAILED: " + f));
  process.exit(1);
}
console.log("all green");
