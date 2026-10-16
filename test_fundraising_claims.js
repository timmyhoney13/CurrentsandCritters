#!/usr/bin/env node
/* Every fundraising and ocean-conservation claim the public can read.
 *
 * Run:  node test_fundraising_claims.js      (no browser, no network needed)
 *
 * WHY THIS FILE EXISTS. Two separate audits found the same class of problem
 * here, and it is the one class that costs real money rather than a bug
 * report: a NUMBER OR A CLAIM ON THE PAGE THAT IS NOT TRUE.
 *
 *   • The goal was stated as $25,000 in four places and $25k in a fifth, and
 *     the fifth said "$25k raised" when $0 had been raised.
 *   • "Receipts published." was on the home page. No public receipts page
 *     exists, and donation_report.py is an admin CLI behind a recovery key.
 *   • The 5% pledge was stated on three different BASES in four places
 *     ("every purchase", "every game sold", and twice bare), so no reader
 *     could tell what 5% was 5% of.
 *   • The pledge named the Surfrider Foundation as a recipient of donations
 *     that no verified record shows were ever transferred.
 *
 * So the rule this file enforces is narrow and absolute: the site may state
 * the TARGET and the VERIFIED RAISED TOTAL, and it may state the commitment,
 * and it may not state anything else as fact.
 *
 * The target is $15,000 (set by the owner, 2026-10-15). It is a TARGET. The
 * raised figure is whatever the Supporter Reef Wall really sums to, fed in
 * live by renderDonationGoal, and NOTHING here is allowed to hard-code it.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const read = (...p) => fs.readFileSync(path.join(__dirname, ...p), "utf8");

const HOME     = read("index.html");
const SHOP     = read("shop.html");
const LAUNCH   = read("launch.html");
const STANDBY  = read("_standby", "website", "03-donation-goal.html");
const APP      = read("multiplayer", "client", "js", "preview-app.js");
const BIZLEGAL = read("multiplayer", "client", "js", "business-legal.js");

// Every surface a member of the public can read a fundraising claim on.
const PUBLIC_PAGES = [
  ["index.html", HOME],
  ["shop.html", SHOP],
  ["launch.html", LAUNCH],
  ["_standby/website/03-donation-goal.html", STANDBY],
];

let pass = 0;
const failures = [];
function check(name, cond) {
  if (cond) { console.log("  ✓ " + name); pass++; }
  else { console.log("  ✗ FAIL: " + name); failures.push(name); }
}
function section(t) { console.log("\n" + t); }

// ════════════════════════════════════════════════════════════════════════
section("1. The target is $15,000, and it is the only target");
// ════════════════════════════════════════════════════════════════════════

check("index.html drives the bar from a single $15,000 constant",
  /const DONATION_GOAL = 15000;/.test(HOME));

check("…and no second copy of the number is left to drift",
  (HOME.match(/DONATION_GOAL = /g) || []).length === 1);

for (const [name, src] of PUBLIC_PAGES) {
  // The coin pack "25,000 Critter Coins" and the XP table's 250000 are NOT
  // fundraising figures, so this looks for the dollar-sign forms only.
  check(`${name} states no stale $25,000 target`,
    !/\$25,000|\$25k|\$25K/.test(src));
  check(`${name} states no stale $7,000 target`,
    !/\$7,000|\$7k|\$7K/.test(src));
}

check("the home page's goal span reads $15,000",
  /data-donation="goal">\$15,000</.test(HOME));
check("the standby copy of the band carries the same target",
  /\$15,000/.test(STANDBY) && !/\$25,000/.test(STANDBY));

// ════════════════════════════════════════════════════════════════════════
section("2. Raised is never confused with the target");
// ════════════════════════════════════════════════════════════════════════

check("the bar is labelled Amount Raised, against the target",
  /Amount Raised:/.test(HOME));

check("the raised figure starts at $0 and is filled in live, not hard-coded",
  /data-donation="raised">\$0</.test(HOME) && /renderDonationGoal/.test(HOME));

check("the raised total still comes from the wall's own sum",
  /totalRaisedCents/.test(HOME));

check("$15,000 is never described as money already in hand",
  !/\$15,000 raised|raised \$15,000|\$15k raised/i.test(HOME + SHOP + LAUNCH));

check("the shop's impact note no longer claims a raised total",
  !/\$25k raised|goal: \$\d/i.test(SHOP));

check("'left to reach the goal' is still the remaining-amount wording",
  /left to reach the goal/.test(HOME));

// The bar, the remaining figure and the percentage must all be computed from
// DONATION_GOAL. A literal anywhere in that maths is how the three displays
// drifted apart the first time.
check("the remaining figure is computed, not written down",
  /const left\s*=\s*Math\.max\(0,\s*DONATION_GOAL - donated\)/.test(HOME));
check("the fill percentage is computed from the same constant",
  /\(donated \/ DONATION_GOAL\) \* 100/.test(HOME));
check("the goal label itself is painted from the constant",
  /goal\.textContent\s*=\s*'\$' \+ DONATION_GOAL\.toLocaleString\(\)/.test(HOME));

// ════════════════════════════════════════════════════════════════════════
section("3. Claims that were not true are gone");
// ════════════════════════════════════════════════════════════════════════

for (const [name, src] of PUBLIC_PAGES) {
  check(`${name} does not claim receipts are published`,
    !/Receipts published/i.test(src));
}
// Visible text only. index.html carries a long comment ABOUT the "Donated to
// Ocean Conservation" mistake, and a check that reads comments would fail on
// the very note that stops the bug coming back.
const visible = (s) => s.replace(/<!--[\s\S]*?-->/g, "");
check("no page claims a donation has been transferred",
  !/(donated to|we have donated|donations sent|already donated)/i
    .test(visible(HOME) + visible(SHOP) + visible(LAUNCH)));

check("the Money Raised tile is still never called a donation",
  !/Donated to Ocean Conservation/.test(HOME));

// ════════════════════════════════════════════════════════════════════════
section("4. The campaign copy the owner approved");
// ════════════════════════════════════════════════════════════════════════

// Prose in markup wraps, so a sentence can be split across lines by any
// future reflow. These checks read the copy with runs of whitespace collapsed
// so that re-indenting the block can never fail them.
const flat = (s) => s.replace(/\s+/g, " ");
const HOME_FLAT = flat(HOME);

check("the home page asks for the $15,000 in the owner's words",
  /Help Us Raise \$15,000 to Bring Currents and Critters to Life/.test(HOME_FLAT));
check("…and says what the money is for",
  /manufacturing/i.test(HOME_FLAT) && /advertising/i.test(HOME_FLAT)
  && /launch expenses/i.test(HOME_FLAT));
check("…and carries the campaign line",
  /Play the Game\. Protect the Ocean\. Change the Tide\./.test(HOME_FLAT));
check("every contribution line is present",
  /Every contribution brings us one step closer/.test(HOME_FLAT));

// ════════════════════════════════════════════════════════════════════════
section("5. One pledge sentence, on one basis");
// ════════════════════════════════════════════════════════════════════════

const PLEDGE = "5% goes to ocean conservation";
check("the canonical pledge sentence is what the home page says",
  HOME.includes(PLEDGE));

// The two contradictory bases that the audit found. Neither said gross or
// net, and they are not the same transactions, so a reader could not tell
// which one the 5% was taken from.
for (const [name, src] of [...PUBLIC_PAGES, ["preview-app.js", APP]]) {
  check(`${name} does not state a second, different basis`,
    !/5% of every purchase/i.test(src));
  check(`${name} does not state the "every game sold" basis either`,
    !/on every game sold/i.test(src));
}

check("the pledge is still made, in every place it was made before",
  HOME.includes(PLEDGE) && SHOP.includes(PLEDGE)
  && LAUNCH.includes(PLEDGE) && APP.includes(PLEDGE));

// ════════════════════════════════════════════════════════════════════════
section("6. No unverified charity relationship");
// ════════════════════════════════════════════════════════════════════════

// Naming a charity next to a percentage is what turns a marketing line into a
// possible charitable sales promotion, and "including donations to X" asserts
// a transfer. Until there is documented authorisation and a verified record,
// the marketing pledge names no organisation.
for (const [name, src] of [["index.html", HOME], ["launch.html", LAUNCH],
                           ["preview-app.js", APP]]) {
  check(`${name} does not name a conservation charity at all`,
    !/Surfrider/i.test(src));
  check(`${name} still disclaims any partnership`,
    /is not sponsored by, endorsed by, or officially partnered with any conservation organisation/
      .test(src));
}

// The independence disclaimer is the opposite case: it is protective, it is
// accurate, and it stays on the legal page whatever the marketing says.
check("the legal page still disclaims any partnership",
  /not sponsored by, endorsed by, or officially partnered with the Surfrider Foundation/
    .test(BIZLEGAL));

// The commitment itself is the owner's and is not up for removal.
check("the commitment to ocean conservation survives all of the above",
  /ocean conservation/i.test(HOME) && /ocean conservation/i.test(SHOP)
  && /ocean conservation/i.test(APP));

// ════════════════════════════════════════════════════════════════════════
section("7. The basis is a flag, not a published guess");
// ════════════════════════════════════════════════════════════════════════

// The exact calculation (after discounts and refunds, excluding separately
// charged tax and shipping, before processing fees) is drafted and WAITING ON
// THE OWNER. It must not reach the page until he flips one flag.
check("business-legal.js carries the drafted basis behind an approval flag",
  /CC_PLEDGE_BASIS_APPROVED/.test(BIZLEGAL));
check("…and the flag is off until the owner turns it on",
  /CC_PLEDGE_BASIS_APPROVED\s*=\s*false/.test(BIZLEGAL));
check("…and the drafted wording is actually there to be turned on",
  /excluding separately charged sales tax and shipping/i.test(BIZLEGAL));

// ────────────────────────────────────────────────────────────────────────
console.log(`\nfundraising-claims checks: ${pass + failures.length}`);
if (failures.length) {
  console.log(`${failures.length} FAILED`);
  process.exit(1);
}
console.log("all green");
