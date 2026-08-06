#!/usr/bin/env node
/* The Privacy Policy, and the sign-in it replaced.
 *
 * Run:  node test_privacy_policy.js
 *
 * Three things ship together here and each one can break silently:
 *
 *   1. THE DOCUMENT. js/privacy-policy.js is the ONE source of the policy —
 *      the published /privacy page and the in-game reader both render that
 *      exact string. If a section is dropped, renamed or duplicated, the
 *      contents list and the headings drift apart and nobody notices, because
 *      a legal page that is quietly missing section 9 still *looks* right.
 *      So: every section is present, uniquely id'd, and the specific
 *      commitments (we don't sell data, we don't store your Google password,
 *      the mailing address) are actually in the text.
 *
 *   2. BOTH HOSTS SERVE IT. /privacy is one file behind two front doors —
 *      Vercel (marketing site, via a rewrite) and Render (game host, via a
 *      route). Adding one and forgetting the other 404s on exactly one domain,
 *      which is the failure nobody sees from their own laptop. The page loads
 *      /css and /js by absolute path, and those prefixes need their own Vercel
 *      rewrites or the marketing copy renders unstyled and blank.
 *
 *   3. THE SIGN-IN AND RENAME RULES. First sign-in is now ONE screen (the
 *      scroll-and-agree Terms gate is gone), and a username change is one free
 *      rename then PHST_RENAME_COIN_PRICE coins with no waiting period. The
 *      promise made on the sign-in screen and the amount actually charged must
 *      be the same number, and the charge must ride inside the rename's own
 *      transaction.
 *
 * No browser and no network: the policy module runs for real in a sandbox,
 * everything else is read out of the shipped sources.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = __dirname;
const read = (p) => fs.readFileSync(path.join(ROOT, p), "utf8");

const POLICY_SRC = read("multiplayer/client/js/privacy-policy.js");
const PAGE       = read("multiplayer/client/privacy.html");
const PP_CSS     = read("multiplayer/client/css/privacy.css");
const APP        = read("multiplayer/client/js/preview-app.js");
const HTML       = read("multiplayer/client/preview.html");
const CSS        = read("multiplayer/client/css/preview.css");
const SERVER     = read("multiplayer_server.py");
const VERCEL     = read("vercel.json");
const SITE       = read("index.html");
const SITE_CSS   = read("styles.css");

let failures = 0;
let checks = 0;
function check(cond, label) {
  checks++;
  if (!cond) { failures++; console.log("  ✗ " + label); }
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("\n1. The document itself");
// ═══════════════════════════════════════════════════════════════════════════
// Run the real module the way a browser would: it only touches `window`.
const win = {};
vm.runInNewContext(POLICY_SRC, { window: win });

const HTML_DOC = win.CC_PRIVACY_HTML;
const SECTIONS = win.CC_PRIVACY_SECTIONS;
// Plain text, for "is this sentence actually in the policy" checks.
const TEXT = String(HTML_DOC || "")
  .replace(/<[^>]+>/g, " ")
  .replace(/&amp;/g, "&")
  .replace(/\s+/g, " ");
{
  check(typeof HTML_DOC === "string" && HTML_DOC.length > 5000,
        `the policy renders a substantial document (got ${String(HTML_DOC).length} chars)`);
  check(Array.isArray(SECTIONS) && SECTIONS.length === 18,
        `all 18 sections are published, got ${SECTIONS && SECTIONS.length}`);
  check(win.CC_PRIVACY_UPDATED === "August 6, 2026",
        `the last-updated date is stated, got ${win.CC_PRIVACY_UPDATED}`);
  check(TEXT.includes("Last updated: August 6, 2026"),
        "the last-updated date is printed in the document, not just exported");

  // The contents list and the headings come from ONE array — prove it by
  // finding every advertised section id exactly once in the rendered doc.
  for (const s of SECTIONS || []) {
    const hits = (HTML_DOC.match(new RegExp(`id="${s.id}"`, "g")) || []).length;
    check(hits === 1, `section ${s.n} ("${s.title}") appears exactly once, got ${hits}`);
    check(HTML_DOC.includes(`</span>${s.title}</h3>`),
          `section ${s.n}'s heading prints its own title`);
  }
  // Numbering is 1..18 with no gaps — a gap means a section was deleted.
  check((SECTIONS || []).every((s, i) => s.n === i + 1),
        "sections are numbered 1 to 18 with no gaps");
  check(!HTML_DOC.includes("undefined"), "nothing renders as 'undefined'");

  // The commitments people open a privacy policy to find. Losing any of these
  // in an edit is the kind of change that is legally meaningful and visually
  // invisible.
  const promises = [
    "We do not sell your personal information",
    "We do not receive or store your Google password",
    "Your email address, Google account identifier, and other private account information are not publicly displayed",
    "You may be able to use certain parts of Currents & Critters as a guest without creating an account",
    "Every marketing email will include a working unsubscribe option",
    "not directed to children under 13",
    "916A South Douglas Avenue",
    "Nashville, Tennessee 37204-2021",
    "timothy.honey@beardedsealstudios.com",
  ];
  for (const p of promises) {
    check(TEXT.includes(p), `the policy still says: "${p.slice(0, 52)}…"`);
  }
  // The contact address must be a real mailto, not just printed text.
  check(HTML_DOC.includes('href="mailto:timothy.honey@beardedsealstudios.com"'),
        "the privacy-request address is a working mailto link");
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("2. One document, two skins");
// ═══════════════════════════════════════════════════════════════════════════
// The published page and the in-game reader render the SAME string; only a
// wrapper class differs. If either skin loses its variable block the document
// renders as unreadable low-contrast text on the other surface.
{
  check(/\.pp-doc\.pp-light,\s*\n\.pp-light \.pp-doc \{/.test(PP_CSS), "the light skin is defined");
  check(/\.pp-doc\.pp-dark,\s*\n\.pp-dark \.pp-doc \{/.test(PP_CSS), "the dark skin is defined");
  // Every colour the markup uses must exist in BOTH skins.
  const varsUsed = [...new Set((PP_CSS.match(/var\((--pp-[a-z0-9-]+)\)/g) || [])
    .map((m) => m.slice(4, -1)))];
  const lightBlock = /\.pp-doc\.pp-light,[\s\S]*?\n\}/.exec(PP_CSS)[0];
  const darkBlock  = /\.pp-doc\.pp-dark,[\s\S]*?\n\}/.exec(PP_CSS)[0];
  for (const v of varsUsed) {
    check(lightBlock.includes(v + ":"), `${v} has a light value`);
    check(darkBlock.includes(v + ":"), `${v} has a dark value`);
  }
  check(varsUsed.length >= 10, `the skins actually drive the document (${varsUsed.length} variables)`);

  check(/class="doc pp-light"/.test(PAGE), "the published page wears the light skin");
  check(/class="privacy-scroll pp-dark"/.test(HTML), "the in-game reader wears the dark skin");
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("3. Both hosts serve /privacy");
// ═══════════════════════════════════════════════════════════════════════════
{
  // Render (the game host).
  check(/PRIVACY_HTML_PATH = os\.path\.join\(BASE_DIR, "multiplayer", "client", "privacy\.html"\)/.test(SERVER),
        "the server knows where privacy.html lives");
  check(/parts\[0\] in \{"privacy", "privacy-policy"\}/.test(SERVER),
        "the server routes /privacy (and /privacy-policy)");
  check(/_send_html_file\(PRIVACY_HTML_PATH, "privacy"\)/.test(SERVER),
        "the route serves the page with the site's HTML headers");

  // Vercel (the marketing site).
  const vercel = JSON.parse(VERCEL);
  const rewrite = (src) => vercel.rewrites.find((r) => r.source === src);
  check(rewrite("/privacy") &&
        rewrite("/privacy").destination === "/multiplayer/client/privacy.html",
        "Vercel rewrites /privacy onto the same one file");
  check(!!rewrite("/privacy-policy"), "Vercel rewrites /privacy-policy too");
  // The page loads its assets by absolute path, so those prefixes need their
  // own rewrites or the marketing copy renders unstyled.
  for (const asset of PAGE.match(/(?:href|src)="\/(css|js)\/[^"]+"/g) || []) {
    const prefix = "/" + asset.split("/")[1];
    check(!!rewrite(prefix + "/:file"),
          `${prefix} is rewritten on Vercel, so ${asset.slice(6, -1)} loads on the marketing site`);
  }
  // A bare single-segment path is otherwise read as a room code.
  check(/"privacy",/.test(APP) &&
        /RESERVED_PATH_NAMES = new Set\(\[[\s\S]*?"privacy",[\s\S]*?\]\)/.test(APP),
        "/privacy can never be mistaken for a room ID");
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("4. The published page");
// ═══════════════════════════════════════════════════════════════════════════
{
  check(/<title>Privacy Policy \| Currents and Critters<\/title>/.test(PAGE), "the page is titled");
  check(/<meta name="description"/.test(PAGE), "the page has a description for search results");
  check(/src="\/js\/privacy-policy\.js\?v=/.test(PAGE),
        "the page loads the ONE policy source (cache-busted)");
  check(/href="\/css\/privacy\.css\?v=/.test(PAGE),
        "the page loads the shared stylesheet (cache-busted)");
  check(!/CC_PRIVACY_HTML\s*=/.test(PAGE), "the page never inlines its own copy of the text");
  check(/id="pp-mount"/.test(PAGE) && /mount\.innerHTML = html;/.test(PAGE),
        "the page mounts the shared document");
  check(/doc-fallback/.test(PAGE) && /did not load/.test(PAGE),
        "a failed script says so instead of showing an empty card");
  // Navigation: a rail on desktop, the same list folded up on mobile.
  check(/id="toc-list"/.test(PAGE) && /id="toc-m-list"/.test(PAGE),
        "there is a contents list for desktop and for mobile");
  check(/var built = tocHtml\(\);/.test(PAGE),
        "both contents lists are built from the same section array");
  check(/@media \(max-width: 900px\)[\s\S]*?\.toc \{ display: none; \}[\s\S]*?\.toc-m \{ display: block; \}/.test(PAGE),
        "the rail gives way to the mobile disclosure");
  check(/scroll-margin-top/.test(PP_CSS),
        "deep links clear the sticky top bar instead of hiding under it");
  check(/id="totop"/.test(PAGE), "there is a way back to the top of a long document");
  check(/@media print/.test(PAGE), "the policy prints without the site chrome");
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("5. The link at the bottom of the website");
// ═══════════════════════════════════════════════════════════════════════════
{
  const foot = SITE.slice(SITE.indexOf('<footer class="foot">'));
  check(foot.includes('href="/privacy"'), "the footer links to the policy");
  check(/<div class="foot-legal">[\s\S]*?href="\/privacy"/.test(foot),
        "the legal strip at the very bottom carries the link");
  check(/\.foot-legal \{/.test(SITE_CSS) && /\.foot-legal a \{/.test(SITE_CSS),
        "the legal strip is styled");
  check(/Bearded Seal Studios LLC/.test(foot),
        "the footer names the company the policy is from");
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("6. The in-game reader (Settings → 📜 Legal)");
// ═══════════════════════════════════════════════════════════════════════════
{
  check(/id="settings-privacy-btn"/.test(HTML), "Settings has a Privacy Policy button");
  check(/>Privacy Policy<\/button>/.test(HTML), "the button says what it opens");
  check(/id="privacy-modal"/.test(HTML) && /id="privacy-scroll"/.test(HTML),
        "the reader modal exists");
  check(/const open = \(\) => ccShowPrivacy\(\);/.test(APP), "the button opens the reader");
  check(/scroll\.innerHTML = html;/.test(APP) && /window\.CC_PRIVACY_HTML/.test(APP),
        "the reader renders the shared document");
  check(/src="\/js\/privacy-policy\.js\?v=/.test(HTML),
        "the game loads the policy source");
  check(/href="\/css\/privacy\.css\?v=/.test(HTML),
        "the game loads the shared policy stylesheet");
  // Rendered once and kept, so re-opening is instant.
  check(/let _ccPrivacyRendered = false;/.test(APP), "the document is rendered once and kept");
  // Re-entrancy: a second open must not stack a second set of listeners.
  check(/if \(typeof _ccPrivacyCleanup === "function"\)/.test(APP),
        "re-opening tears down the previous listeners");
  // Dismissable every way a read-only modal should be.
  check(/function onBackdrop\(e\) \{ if \(e\.target === modal\) onClose\(\); \}/.test(APP),
        "clicking the backdrop closes the reader");
  check(/id="privacy-close-btn"/.test(HTML), "there is a Close button");
  check(/id="privacy-web-link"/.test(HTML) && /href="\/privacy"/.test(HTML),
        "the reader offers the full page on the web");
  // The jump list scrolls the MODAL, not the page behind it.
  check(/scroll\.scrollTo\(\{ top: scroll\.scrollTop \+ delta - 6/.test(APP),
        "the jump list scrolls the modal's own scroller, measured with rects");
  check(/\.privacy-scroll \.pp-sec \{ scroll-margin-top: 0; \}/.test(CSS),
        "the page's sticky-header offset doesn't leave a gap inside the modal");
  check(/#privacy-modal \{ z-index: 9800; \}/.test(CSS),
        "the reader sits above the settings modal it opens from");
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("7. First sign-in is one screen");
// ═══════════════════════════════════════════════════════════════════════════
// The scroll-to-the-bottom Terms gate used to stand between a new player and
// their first game — on account creation AND on every single guest dive. It is
// gone; nothing may re-introduce a gate on the way in.
{
  for (const dead of ["ccShowTerms", "__ccShowTerms", "TERMS_VERSION", "hasAgreedToTerms",
                      "persistTermsAgreement", "markGuestTermsAgreed", "consumeGuestTermsAgreed",
                      "GUEST_TERMS_OK_KEY", "terms_version", "terms_agreed_at"]) {
    check(!APP.includes(dead), `the retired Terms gate leaves no '${dead}' behind`);
  }
  for (const dead of ["terms-modal", "terms-scroll", "terms-agree-btn", "settings-terms-btn"]) {
    check(!HTML.includes(dead), `the retired Terms markup leaves no '${dead}' behind`);
    check(!CSS.includes(dead), `the retired Terms styles leave no '${dead}' behind`);
  }

  // Guest: name → in. Account: name → saved → in.
  check(/void finishNicknameSetup\(nick\);\n    \}\);/.test(APP),
        "saving a new username goes straight to the profile write");
  check(/id="auth-step-nickname"/.test(HTML), "the username screen is still the first-run step");
  check(/Create Your Username/.test(HTML), "the screen asks you to create a username");
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("8. One free rename, then coins");
// ═══════════════════════════════════════════════════════════════════════════
{
  const price = Number((/const PHST_RENAME_COIN_PRICE\s*=\s*(\d+)/.exec(APP) || [])[1]);
  check(price === 100, `a username change costs 100 coins, source says ${price}`);

  // The promise on the sign-in screen and the charge in Settings are the SAME
  // number — the screen prints the constant rather than a hard-coded price.
  check(/id="auth-nick-price"/.test(HTML), "the sign-in screen has a slot for the price");
  check(/priceEl\.textContent = String\(PHST_RENAME_COIN_PRICE\)/.test(APP),
        "the sign-in screen prints the constant that charges it");
  check(/once for free/.test(HTML), "the sign-in screen promises one free change");
  check(/Critter Coins/.test(HTML), "the sign-in screen names the currency");

  // Free-vs-paid is decided from the freshly-read doc INSIDE the transaction.
  check(/function _renameIsFree\(profile\)/.test(APP), "there is one rule for 'is this free'");
  const tx = /const out = await _db\.runTransaction\(async \(tx\) => \{[\s\S]*?\n        \}\);/.exec(APP);
  check(!!tx, "the rename is a transaction");
  if (tx) {
    check(/const free = _renameIsFree\(data\);/.test(tx[0]),
          "free-vs-paid is decided from the doc the transaction just read");
    check(/if \(!free && coins < PHST_RENAME_COIN_PRICE\) throw new Error\("coins"\);/.test(tx[0]),
          "a paid rename aborts before writing if the coins aren't there");
    check(/"stats\.critter_coins": after/.test(tx[0]),
          "the coins are taken in the same write as the new name");
    check(/nickname_changed_at: firebase\.firestore\.FieldValue\.serverTimestamp\(\)/.test(tx[0]),
          "the rename spends the free change by stamping the marker");
  }

  // No waiting period anywhere: the cooldown and its Store workaround are gone.
  check(!/nickname_cooldown|settings-nick-cooldown/.test(APP + HTML + CSS),
        "the 24-hour cooldown UI is gone");
  check(!/change your username again/.test(APP),
        "no copy still tells players to come back tomorrow");
  check(/id="settings-nick-cost"/.test(HTML), "Settings shows what the next change costs");
  check(/Your first username change is free\./.test(APP),
        "a player who never renamed is told it's free");
  check(/You have \$\{_myCritterCoins\(\)\}/.test(APP),
        "a player who must pay is shown their balance");

  // The Change button is never disabled — being short on coins is explained at
  // save time, not by a dead button with no reason attached.
  check(/editBtn\.disabled = false;/.test(APP), "the Change button is always live");
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("9. Shipping");
// ═══════════════════════════════════════════════════════════════════════════
{
  // The client polls version.json and re-prompts forever if these two drift.
  const vjson = JSON.parse(read("multiplayer/client/version.json"));
  const appBuild = (/const APP_BUILD\s*=\s*"([^"]+)"/.exec(APP) || [])[1];
  check(vjson.build === appBuild,
        `APP_BUILD (${appBuild}) matches version.json build (${vjson.build})`);

  // /css and /js are served with a 1-day max-age, so a changed file that keeps
  // its old ?v= reaches nobody.
  const bust = (file) => (new RegExp(`${file}\\?v=([0-9.\\-]+)`).exec(HTML) || [])[1];
  // Compared against version.json rather than a literal, so bumping the build
  // can't leave this test green while the real stamps go stale.
  check(bust("preview\\.css") === vjson.build, "preview.css is cache-busted for this build");
  check(bust("preview-app\\.js") === vjson.build, "preview-app.js is cache-busted for this build");
  check(bust("privacy\\.css") === vjson.build, "privacy.css is cache-busted for this build");
  check(bust("privacy-policy\\.js") === vjson.build, "privacy-policy.js is cache-busted for this build");
  check(bust("tutorials\\.js") === vjson.build, "tutorials.js is cache-busted for this build");

  // privacy.html ships its own copies of those two links; they must agree too.
  const pbust = (file) => (new RegExp(`${file}\\?v=([0-9.\\-]+)`).exec(PAGE) || [])[1];
  check(pbust("privacy\\.css") === vjson.build, "privacy.html cache-busts privacy.css for this build");
  check(pbust("privacy-policy\\.js") === vjson.build, "privacy.html cache-busts privacy-policy.js for this build");

  // New client files must be allowlisted for both git and the Docker image, or
  // they simply never reach the server.
  for (const f of [".gitignore", ".dockerignore"]) {
    check(/!multiplayer\/client\/\*\*/.test(read(f)),
          `${f} allows new files under multiplayer/client/`);
  }
}

console.log(`\nprivacy-policy checks: ${checks}`);
if (failures) { console.log(`${failures} FAILED`); process.exit(1); }
console.log("privacy policy OK");
