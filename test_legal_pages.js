/* ════════════════════════════════════════════════════════════════════════════
 * The seven legal pages, the ones that are not /privacy.
 *
 * test_privacy_policy.js guards the privacy policy. This guards the rest:
 * Terms of Service, Cookie Notice, Refund/Shipping/Preorder, Community
 * Guidelines, Copyright & DMCA, Business & Legal Info, Accessibility.
 *
 * What it is really protecting, in order of how quietly it can break:
 *
 *   1. THE DOCUMENTS ONLY USE MARKUP css/privacy.css ALREADY STYLES. That is
 *      the whole reason one stylesheet dresses the website light skin AND the
 *      in-game dark reader. A document that invents a class renders unstyled
 *      in the game and nobody notices until a player opens it.
 *   2. THE PROMISES. The same idea as the privacy policy's promise list: a
 *      sentence that allocates a right or disclaims one is legally meaningful
 *      and visually invisible, so losing it in an edit must fail a test.
 *   3. BOTH HOSTS SERVE EVERY ROUTE. A policy that 404s on one host is not
 *      published. The game server and vercel.json must agree, including the
 *      long spellings (/terms-of-service, /dmca, …).
 *   4. THE BUILD STAMP. Same trap as everywhere else in this repo: a changed
 *      file keeping its old ?v= reaches nobody.
 *   5. NOTHING IS BEHIND A LOGIN.
 * ═══════════════════════════════════════════════════════════════════════════*/
"use strict";
const fs = require("fs");
const vm = require("vm");
const path = require("path");

let checks = 0, failures = 0;
function check(cond, label) {
  checks++;
  if (cond) return;
  failures++;
  console.log("  ✗ FAIL: " + label);
}
function read(p) { return fs.readFileSync(path.join(__dirname, p), "utf8"); }

const CLIENT = "multiplayer/client";
const KIT_SRC = read(CLIENT + "/js/legal-kit.js");
const PP_CSS  = read(CLIENT + "/css/privacy.css");
const CHROME  = read(CLIENT + "/css/legal-page.css");
const SERVER  = read("multiplayer_server.py");
const VERCEL  = JSON.parse(read("vercel.json"));
const VERSION = JSON.parse(read(CLIENT + "/version.json"));

// file, slug, module, every route that must reach it
const PAGES = [
  { file: "terms.html",                slug: "terms",                module: "terms-of-service",        routes: ["terms", "terms-of-service"] },
  { file: "cookies.html",              slug: "cookies",              module: "cookie-notice",           routes: ["cookies", "cookie-notice"] },
  { file: "refunds.html",              slug: "refunds",              module: "refund-policy",           routes: ["refunds", "refund-policy"] },
  { file: "community-guidelines.html", slug: "community-guidelines", module: "community-guidelines",    routes: ["community-guidelines", "community"] },
  { file: "copyright.html",            slug: "copyright",            module: "copyright-dmca",          routes: ["copyright", "dmca"] },
  { file: "legal.html",                slug: "legal",                module: "business-legal",          routes: ["legal"] },
  { file: "accessibility.html",        slug: "accessibility",        module: "accessibility-statement", routes: ["accessibility"] },
];

// ═══════════════════════════════════════════════════════════════════════════
console.log("\n1. The seven documents");
// ═══════════════════════════════════════════════════════════════════════════
// Run the real modules the way a browser would: they only touch `window`.
const win = {};
vm.runInNewContext(KIT_SRC, { window: win });
for (const p of PAGES) {
  vm.runInNewContext(read(CLIENT + "/js/" + p.module + ".js"), { window: win });
}

const DOCS = {};
{
  check(Object.keys(win.CC_LEGAL).length === 7,
        `all seven documents register, got ${Object.keys(win.CC_LEGAL).length}`);

  for (const p of PAGES) {
    const doc = win.CC_LEGAL[p.slug];
    check(!!doc, `${p.slug} registered itself`);
    if (!doc) continue;
    DOCS[p.slug] = doc;

    const html = win.ccLegalDocHtml(doc);
    const secs = win.ccLegalSections(doc);
    const text = html.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/\s+/g, " ");

    check(typeof doc.title === "string" && doc.title.length > 3, `${p.slug} has a title`);
    check(doc.updated === "October 9, 2026", `${p.slug} states its last-updated date, got ${doc.updated}`);
    check(text.includes("Last updated: October 9, 2026"), `${p.slug} prints that date in the document`);
    check(typeof doc.lede === "string" && doc.lede.length > 40, `${p.slug} opens with a lede`);
    check(secs.length >= 5, `${p.slug} is a substantial document (${secs.length} sections)`);
    check(html.length > 4000, `${p.slug} renders real content (${html.length} chars)`);
    check(!html.includes("undefined"), `${p.slug} renders nothing as 'undefined'`);

    // Numbering is positional: 1..n, no gaps, each id exactly once.
    check(secs.every((s, i) => s.n === i + 1), `${p.slug} is numbered 1..${secs.length} with no gaps`);
    for (const s of secs) {
      const hits = (html.match(new RegExp(`id="${s.id}"`, "g")) || []).length;
      check(hits === 1, `${p.slug} section ${s.n} ("${s.title}") appears exactly once, got ${hits}`);
      check(html.includes(`</span>${s.title}</h3>`), `${p.slug} section ${s.n} prints its own title`);
    }

    // No em dashes in anything a player reads (house rule), and no escape
    // sequence that leaked through as literal text.
    check(!text.includes("—"), `${p.slug} has no em dash in player-facing text`);
    check(!/\\u[0-9A-Fa-f]{4}/.test(text), `${p.slug} has no unrendered \\uXXXX escape`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("2. One stylesheet dresses both skins");
// ═══════════════════════════════════════════════════════════════════════════
{
  // Every class the documents use must already be styled by privacy.css,
  // which is what makes the in-game dark reader work for free.
  const used = new Set();
  for (const slug of Object.keys(DOCS)) {
    const html = win.ccLegalDocHtml(DOCS[slug]);
    for (const m of html.matchAll(/class="([^"]+)"/g)) {
      m[1].split(/\s+/).forEach((c) => c && used.add(c));
    }
  }
  check(used.size > 0, "the documents carry classes at all");
  for (const c of used) {
    check(PP_CSS.includes("." + c), `.${c} is styled by css/privacy.css (used by a document)`);
  }
  // Both skins must exist, or one of the two surfaces is unstyled.
  check(/\.pp-doc\.pp-light,\s*\n\.pp-light \.pp-doc \{/.test(PP_CSS), "the light skin is defined");
  check(/\.pp-doc\.pp-dark,\s*\n\.pp-dark \.pp-doc \{/.test(PP_CSS), "the dark skin is defined");

  // The shared chrome has to carry the responsive and print rules, since the
  // seven pages have no inline style of their own.
  check(/\.toc \{ display: none; \}/.test(CHROME), "the chrome hides the desktop rail on narrow screens");
  check(/\.toc-m \{ display: block; \}/.test(CHROME), "the chrome shows the mobile contents instead");
  check(/@media print/.test(CHROME), "the chrome strips the site furniture when printed");
  check(/\.doc-fallback \{/.test(CHROME), "the chrome styles the did-not-load message");
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("3. The promises");
// ═══════════════════════════════════════════════════════════════════════════
// Sentences that grant a right, disclaim one, or state a decision the owner
// actually made. Losing any of these in an edit is legally meaningful and
// visually invisible, which is exactly what a test is for.
{
  const text = (slug) =>
    win.ccLegalDocHtml(DOCS[slug]).replace(/<[^>]+>/g, " ")
      .replace(/&amp;/g, "&").replace(/\s+/g, " ");

  const PROMISES = {
    terms: [
      "not directed to children under 13",
      "They are not money, they have no cash value",
      "laws of the State of Tennessee",
      "Davidson County, Tennessee",
      "Chat and public profile areas are not private",
    ],
    cookies: [
      "We do not use advertising cookies",
      "We do not use Google Analytics, Google Tag Manager, Meta Pixel",
      "Our own servers do not set any",
      "we do not record whether you opened an email",
    ],
    refunds: [
      "generally not refundable once delivered",
      "You paid and nothing arrived",
      "You were charged twice",
      "Anything the law requires",
      "We are not taking preorders for the physical card game on this website",
    ],
    "community-guidelines": [
      "Play fair. Be decent",
      "Never put private information in chat",
      "We never ask for your password",
    ],
    copyright: [
      "Designated Copyright Agent",
      "under penalty of perjury",
      "Stream or record Currents and Critters",
    ],
    legal: [
      "916A South Douglas Avenue",
      "Nashville, Tennessee 37204-2021",
      "5% of every purchase to ocean conservation",
      "not sponsored by, endorsed by, or officially partnered with the Surfrider Foundation",
      "a purchase is not a tax-deductible charitable contribution",
    ],
    accessibility: [
      "WCAG 2.1 Level AA",
      "We are not claiming that we currently conform",
      "There is no skip-to-content link",
    ],
  };

  for (const [slug, lines] of Object.entries(PROMISES)) {
    const t = text(slug);
    for (const line of lines) {
      check(t.includes(line), `${slug} still says: "${line.slice(0, 54)}…"`);
    }
  }

  // The contact address is a working mailto everywhere it appears, not just
  // printed text somebody has to retype.
  for (const slug of Object.keys(DOCS)) {
    const html = win.ccLegalDocHtml(DOCS[slug]);
    check(html.includes('href="mailto:timothy.honey@beardedsealstudios.com"'),
          `${slug} gives a working mailto link`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("4. The pages themselves");
// ═══════════════════════════════════════════════════════════════════════════
{
  for (const p of PAGES) {
    const PAGE = read(CLIENT + "/" + p.file);
    const doc = DOCS[p.slug];
    const route = "/" + p.file.replace(".html", "");

    // Findable by a search engine, and distinct from its siblings.
    check(new RegExp(`<title>${doc.title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\| Currents and Critters</title>`).test(PAGE),
          `${p.file} is titled after its own document`);
    check(/<meta name="description" content="[^"]{60,}"/.test(PAGE), `${p.file} has a real description`);
    check(PAGE.includes(`<link rel="canonical" href="https://currentsandcritters.com${route}" />`),
          `${p.file} declares its canonical URL`);
    check(/<meta name="robots" content="index, follow"/.test(PAGE), `${p.file} invites indexing`);
    check(/<meta property="og:title"/.test(PAGE) && /<meta property="og:description"/.test(PAGE),
          `${p.file} has share metadata`);
    check(/<html lang="en">/.test(PAGE), `${p.file} declares its language`);

    // The shell renders the document and nothing else.
    check(PAGE.includes('id="doc-mount"'), `${p.file} has somewhere to mount the document`);
    check(PAGE.includes(`ccLegalRender("${p.slug}")`), `${p.file} renders its own document`);
    check(/if \(window\.ccLegalRender\)/.test(PAGE), `${p.file} survives the kit failing to load`);
    check(/doc-fallback/.test(PAGE) && /did not load/.test(PAGE), `${p.file} says so if the script fails`);
    check(PAGE.includes('id="toc-list"') && PAGE.includes('id="toc-m-list"'),
          `${p.file} has both contents rails`);
    check(PAGE.includes('id="totop"'), `${p.file} has a way back to the top`);
    check(/class="doc pp-light"/.test(PAGE), `${p.file} wears the light skin`);
    // The text lives in ONE place: the page must not inline its own copy.
    check(!/CC_LEGAL\s*\[/.test(PAGE) && !/CC_LEGAL\./.test(PAGE),
          `${p.file} never inlines its own copy of the text`);

    // Assets, all cache-busted to THIS build.
    const bust = (f) => (new RegExp(`${f}\\?v=([0-9.\\-]+)`).exec(PAGE) || [])[1];
    check(bust("legal-page\\.css") === VERSION.build, `${p.file} cache-busts legal-page.css for this build`);
    check(bust("privacy\\.css") === VERSION.build,    `${p.file} cache-busts privacy.css for this build`);
    check(bust("legal-kit\\.js") === VERSION.build,   `${p.file} cache-busts legal-kit.js for this build`);
    check(bust(p.module.replace(/[-]/g, "\\-") + "\\.js") === VERSION.build,
          `${p.file} cache-busts js/${p.module}.js for this build`);

    // Every legal page reaches every other one, so the set is navigable from
    // inside it and not only from the homepage footer.
    for (const other of PAGES) {
      if (other.file === p.file) continue;
      const r = "/" + other.file.replace(".html", "");
      check(PAGE.includes(`href="${r}"`), `${p.file} links to ${r}`);
    }
    check(PAGE.includes('href="/privacy"'), `${p.file} links to /privacy`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("5. Both hosts serve every route");
// ═══════════════════════════════════════════════════════════════════════════
{
  // The game server (Render).
  check(/LEGAL_PAGES = \{/.test(SERVER), "the server has a route table for the legal pages");
  check(/if len\(parts\) == 1 and parts\[0\] in LEGAL_HTML_PATHS:/.test(SERVER),
        "the server serves anything in that table");
  check(/self\._send_html_file\(LEGAL_HTML_PATHS\[parts\[0\]\], parts\[0\]\)/.test(SERVER),
        "and serves it as a file, like the privacy policy");

  // The marketing site (Vercel).
  const rewrite = (src) => (VERCEL.rewrites || []).find((r) => r.source === src);
  for (const p of PAGES) {
    for (const r of p.routes) {
      check(new RegExp(`"${r}":\\s*"${p.file}"`).test(SERVER),
            `the game server routes /${r} to ${p.file}`);
      const rw = rewrite("/" + r);
      check(!!rw, `Vercel rewrites /${r}`);
      check(rw && rw.destination === `/${CLIENT}/${p.file}`,
            `Vercel sends /${r} to ${p.file}`);
    }
  }
  // /css and /js must be reachable on the marketing host or the pages load bare.
  for (const prefix of ["/css", "/js"]) {
    check(!!rewrite(prefix + "/:file"), `Vercel serves ${prefix} to the marketing site`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("6. Reachable from the website, without an account");
// ═══════════════════════════════════════════════════════════════════════════
{
  const INDEX = read("index.html");
  const foot = INDEX.slice(INDEX.indexOf('class="foot-legal"'));
  check(foot.length > 0, "the homepage has a legal section in its footer");
  for (const r of ["/privacy", "/terms", "/cookies", "/refunds",
                   "/community-guidelines", "/copyright", "/legal", "/accessibility"]) {
    check(foot.includes(`href="${r}"`), `the homepage footer links ${r}`);
  }
  check(/<nav class="foot-legal" aria-label="Legal">/.test(INDEX),
        "the footer legal block is a labelled nav landmark");

  // Nothing about these pages may depend on being signed in.
  for (const p of PAGES) {
    const PAGE = read(CLIENT + "/" + p.file);
    check(!/firebase/i.test(PAGE), `${p.file} does not pull in Firebase`);
    // The word "sign-in" appears legitimately (the Cookie Notice explains
    // that sign-in uses browser storage), so look for real gating machinery.
    check(!/requireAuth|authGate|auth-gate|onAuthStateChanged/.test(PAGE),
          `${p.file} has no sign-in gate`);
    check(!/location\.(href|replace)\s*=\s*["'`][^"'`]*sign/i.test(PAGE),
          `${p.file} never redirects a visitor to sign in`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("7. The reef palette still clears WCAG AA");
// ═══════════════════════════════════════════════════════════════════════════
// The Accessibility Statement these pages ship CLAIMS measured contrast
// figures, so the palette is now a promise and not just a look. This guards
// the text colours against the surfaces they actually sit on.
//
// The trap this palette sets, and the reason the numbers are hard-coded here:
// gold reads as warm and legible and is not. On cream #fef5e6, gold #e8b34a
// is 1.77:1 and gold-deep #c89320 is 2.54:1, both unusable for text, while
// gold-INK #8a5c00 is 5.38:1 and fine. So gold is a surface and gold-ink is
// its text, and the number pill is navy on gold rather than white on gold.
{
  const chan = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const hex = (h) => h.replace("#", "").match(/../g).map((x) => parseInt(x, 16));
  const lum = (h) => { const [r, g, b] = hex(h); return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b); };
  const ratio = (a, b) => {
    const L1 = lum(a), L2 = lum(b);
    return (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
  };
  // Pull the real token values out of the stylesheets, so editing a colour
  // without re-measuring fails here instead of on a player's screen.
  const tok = (css, name) => {
    const m = new RegExp("--" + name + ":\\s*(#[0-9a-fA-F]{6})").exec(css);
    return m && m[1];
  };
  const CREAM = tok(CHROME, "paper");
  check(CREAM === "#fef5e6", `the page surface is the game's cream, got ${CREAM}`);

  // Anchor on the real SELECTORS, not the first mention: the file's header
  // comment names both skins, so indexOf(".pp-light") lands in prose and the
  // slice comes back 33 characters long with no tokens in it.
  const light = PP_CSS.slice(PP_CSS.indexOf(".pp-doc.pp-light,"),
                             PP_CSS.indexOf(".pp-doc.pp-dark,"));
  check(light.length > 200, `the light-skin token block was found (${light.length} chars)`);
  const pairs = [
    ["--pp-body (body text)",    tok(light, "pp-body"),   CREAM, 4.5],
    ["--pp-ink (headings)",      tok(light, "pp-ink"),    CREAM, 4.5],
    ["--pp-accent (accent text)",tok(light, "pp-accent"), CREAM, 4.5],
    ["--pp-muted (muted text)",  tok(light, "pp-muted"),  CREAM, 4.5],
    ["--ink (chrome ink)",       tok(CHROME, "ink"),      CREAM, 4.5],
    ["--muted-strong (chrome)",  tok(CHROME, "muted-strong"), CREAM, 4.5],
    ["--brand-cyan-deep (accent)", tok(CHROME, "brand-cyan-deep"), CREAM, 4.5],
  ];
  for (const [label, fg, bg, need] of pairs) {
    check(!!fg, `${label} is defined`);
    if (!fg) continue;
    const r = ratio(fg, bg);
    check(r >= need, `${label} ${fg} on ${bg} is ${r.toFixed(2)}:1, needs ${need}:1`);
  }

  // The number pill: its ink must clear AA against BOTH gradient stops.
  const numInk = tok(light, "pp-num-ink");
  const numBg = /--pp-num-bg:\s*linear-gradient\([^)]*?(#[0-9a-fA-F]{6})[^)]*?(#[0-9a-fA-F]{6})/.exec(light);
  check(!!numInk && !!numBg, "the section number pill defines an ink and a gradient");
  if (numInk && numBg) {
    for (const stop of [numBg[1], numBg[2]]) {
      const r = ratio(numInk, stop);
      check(r >= 4.5, `number pill ink ${numInk} on ${stop} is ${r.toFixed(2)}:1, needs 4.5:1`);
    }
  }

  // The two golds must never be a text colour. This is the actual mistake
  // this section exists to catch.
  for (const banned of ["#e8b34a", "#c89320"]) {
    for (const [name, css] of [["privacy.css light skin", light], ["legal-page.css", CHROME]]) {
      const asText = new RegExp("color:\\s*" + banned, "i").test(css);
      check(!asText, `${banned} is never used directly as a text colour in ${name}`);
    }
  }
}

console.log(`\nlegal-pages checks: ${checks}`);
if (failures) { console.log(`${failures} FAILED`); process.exit(1); }
console.log("legal pages OK");
