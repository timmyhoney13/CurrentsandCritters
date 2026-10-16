#!/usr/bin/env node
/* The Kickstarter launch page: /launch (launch.html, served by Vercel).
 *
 * Run:  node test_launch_page.js        (no browser, no network needed)
 *
 * Three things on this page can go wrong quietly, and all three are the
 * kind of wrong that is only noticed by the people it misleads:
 *
 *   1. THE COUNTDOWN ARITHMETIC. A clock that has run out must read zero,
 *      not count up into minus numbers, and a LAUNCH_AT written without a
 *      UTC offset is read as the READER's local time: every visitor outside
 *      Central then gets a countdown ending at the wrong moment. The offset
 *      is checked here because nothing on screen reveals its absence.
 *
 *   2. THE TIERS. They are NOT for sale on this page, they are backed
 *      through the Kickstarter. Every tier button must stay disabled and no
 *      Stripe link may appear, or the page takes money for something the
 *      homepage says cannot be bought here yet. Their names and prices are
 *      also compared against index.html, because two pages quoting
 *      different prices for Ocean Ally is worse than either being wrong.
 *
 *   3. THE CLEAN URL. The page is reached at /launch only because of a
 *      rewrite in vercel.json. Without it the shared link is a 404 and the
 *      file still sits there looking fine.
 *
 * The real launch.html is parsed and its real inline countdown script is
 * executed in a stubbed DOM, so these assertions are against the shipped
 * file, not a copy of it.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const HTML_PATH   = path.join(__dirname, "launch.html");
const INDEX_PATH  = path.join(__dirname, "index.html");
const VERCEL_PATH = path.join(__dirname, "vercel.json");

const HTML   = fs.readFileSync(HTML_PATH, "utf8");
const INDEX  = fs.readFileSync(INDEX_PATH, "utf8");
const VERCEL = JSON.parse(fs.readFileSync(VERCEL_PATH, "utf8"));

let pass = 0;
const failures = [];
const queue = [];
function check(name, fn) { queue.push([name, fn]); }
function section(title) { queue.push([null, title]); }
function runAll() {
  for (const [name, fn] of queue) {
    if (name === null) { console.log("\n" + fn); continue; }
    try { fn(); console.log("  ✓ " + name); pass++; }
    catch (e) { console.log("  ✗ " + name + ": " + e.message); failures.push(name); }
  }
  console.log(`\n${pass} passed, ${failures.length} failed`);
  if (failures.length) { failures.forEach(f => console.log("  FAILED: " + f)); process.exit(1); }
}
function eq(actual, expected, what) {
  if (actual !== expected) {
    throw new Error(`${what || "value"}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}
function ok(cond, msg) { if (!cond) throw new Error(msg || "expected true"); }

// ── Pull the countdown script out of the real file ─────────────────────────
const scripts = [...HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const COUNTDOWN_SRC = scripts.find(s => s.includes("__CC_LAUNCH"));
if (!COUNTDOWN_SRC) {
  console.error("launch.html: no inline script defining __CC_LAUNCH. Nothing to test.");
  process.exit(1);
}

// Swapping the date literal is how the three states are reached: the shipped
// script is run each time, only the one value it is meant to be configured
// with is changed. Fails loudly rather than silently testing the default.
function withConst(src, name, value) {
  const re = new RegExp("var " + name + " = '[^']*';");
  const out = src.replace(re, "var " + name + " = '" + value + "';");
  if (out === src) throw new Error("could not substitute " + name);
  return out;
}

// ── A DOM just big enough for the script under test ────────────────────────
function makeEl(id) {
  const el = {
    id: id || "", className: "", textContent: "",
    href: "", target: "", rel: "", download: "",
    children: [], attrs: {}, listeners: {},
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { this.children = this.children.filter(x => x !== c); return c; },
    insertBefore(node, ref) {
      const i = ref ? this.children.indexOf(ref) : -1;
      if (i < 0) this.children.push(node); else this.children.splice(i, 0, node);
      return node;
    },
    click() { this.clicked = true; }
  };
  Object.defineProperty(el, "firstChild", { get() { return this.children[0] || null; } });
  return el;
}

const COUNTDOWN_IDS = ["cd", "cd-days", "cd-hours", "cd-mins", "cd-secs",
  "cd-days-l", "cd-hours-l", "cd-mins-l", "cd-secs-l", "cd-when-local", "cd-ctas"];

function runCountdown(opts) {
  const o = opts || {};
  const src = o.src || COUNTDOWN_SRC;
  const nodes = {};
  COUNTDOWN_IDS.forEach(id => { nodes[id] = makeEl(id); });
  // Mirrors the markup: data-state starts at "pending", which is the honest
  // state of a page whose script has not run yet.
  nodes["cd"].setAttribute("data-state", "pending");
  // Three real CTAs already stand in the hero, so the insert position the
  // script asks for is a position that exists.
  ["a", "b", "c"].forEach(() => nodes["cd-ctas"].appendChild(makeEl()));

  const body = makeEl("body");
  const intervals = [];
  const docListeners = {};
  const created = [];

  const sandbox = {
    console,
    Date, Math, Intl, JSON, Number, String, Object, Array, isFinite, encodeURIComponent,
    setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
    clearInterval: () => {},
    setTimeout: (fn) => { return 0; },
    Blob: function Blob(parts, o2) { this.parts = parts; this.options = o2; },
    URL: { createObjectURL: () => "blob:stub", revokeObjectURL: () => {} },
    document: {
      hidden: false,
      body,
      getElementById: id => nodes[id] || null,
      querySelector: () => null,
      querySelectorAll: () => [],
      createElement: tag => { const e = makeEl(); e.tag = tag; created.push(e); return e; },
      addEventListener: (t, fn) => { (docListeners[t] = docListeners[t] || []).push(fn); }
    }
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return { api: sandbox.window.__CC_LAUNCH, nodes, intervals, docListeners, created, sandbox };
}

// ══════════════════════════════════════════════════════════════════════════
section("the page is there, and /launch reaches it");

check("launch.html sits at the repo root, which is Vercel's output directory", () => {
  ok(fs.existsSync(HTML_PATH), "launch.html missing");
  ok(HTML.trim().toLowerCase().startsWith("<!doctype html>"), "not an HTML document");
});

check("vercel.json rewrites /launch to /launch.html", () => {
  const r = VERCEL.rewrites.filter(x => x.source === "/launch");
  eq(r.length, 1, "number of /launch rewrites");
  eq(r[0].destination, "/launch.html", "destination");
});

check("the rewrite did not disturb the ones that were already there", () => {
  const sources = VERCEL.rewrites.map(r => r.source);
  eq(sources.length, new Set(sources).size, "duplicate rewrite sources");
  ["/rules", "/privacy", "/about", "/leaderboard", "/css/:file", "/js/:file"]
    .forEach(s => ok(sources.includes(s), "lost the rewrite for " + s));
});

check("the shared stylesheet is the one doing the work", () => {
  ok(HTML.includes('href="styles.css"'), "styles.css not linked");
});

check("the countdown markup the script reaches for is all present", () => {
  COUNTDOWN_IDS.forEach(id => ok(HTML.includes('id="' + id + '"'), "missing id=" + id));
  ok(/<div class="cd" id="cd" data-state="pending">/.test(HTML),
    'the countdown must start at data-state="pending"');
});

// ══════════════════════════════════════════════════════════════════════════
section("the countdown arithmetic");

const run = runCountdown();
const api = run.api;

check("the script exposes itself for testing", () => {
  ok(api && typeof api.partsUntil === "function", "__CC_LAUNCH.partsUntil missing");
});

check("one day, two hours, three minutes, four seconds splits correctly", () => {
  const p = api.partsUntil(((24 + 2) * 3600 + 3 * 60 + 4) * 1000);
  eq(p.days, 1, "days"); eq(p.hours, 2, "hours");
  eq(p.mins, 3, "mins"); eq(p.secs, 4, "secs");
});

check("just under a minute is zero minutes and fifty-nine seconds", () => {
  const p = api.partsUntil(59_999);
  eq(p.days, 0, "days"); eq(p.hours, 0, "hours");
  eq(p.mins, 0, "mins"); eq(p.secs, 59, "secs");
});

check("a clock that has run out reads zero, it does not count up", () => {
  [-1, -1000, -86_400_000, -31_536_000_000].forEach(ms => {
    const p = api.partsUntil(ms);
    eq(p.days, 0, "days at " + ms); eq(p.hours, 0, "hours at " + ms);
    eq(p.mins, 0, "mins at " + ms); eq(p.secs, 0, "secs at " + ms);
    ok(p.total === 0, "total went negative at " + ms);
  });
});

check("a hundred days out is a hundred days, not three months of hours", () => {
  const p = api.partsUntil(100 * 86_400_000);
  eq(p.days, 100, "days"); eq(p.hours, 0, "hours");
});

check("hours, minutes and seconds are padded to two digits", () => {
  eq(api.pad2(0), "00", "pad2(0)"); eq(api.pad2(9), "09", "pad2(9)");
  eq(api.pad2(10), "10", "pad2(10)"); eq(api.pad2(59), "59", "pad2(59)");
});

check("one day reads Day, everything else reads Days", () => {
  eq(api.plural(1, "Day"), "Day", "1");
  eq(api.plural(0, "Day"), "Days", "0");
  eq(api.plural(2, "Day"), "Days", "2");
  eq(api.plural(1, "Second"), "Second", "1 second");
});

// ══════════════════════════════════════════════════════════════════════════
section("the launch moment is an instant, not a wall clock");

check("LAUNCH_AT carries an explicit UTC offset", () => {
  ok(typeof api.LAUNCH_AT === "string", "LAUNCH_AT is not a string");
  if (api.LAUNCH_AT === "") return;          // no date set yet is allowed
  ok(/(Z|[+-]\d{2}:?\d{2})$/.test(api.LAUNCH_AT),
    "LAUNCH_AT has no zone, so every reader outside Central counts down to " +
    "the wrong moment: " + api.LAUNCH_AT);
});

check("LAUNCH_AT parses, and to the instant it says", () => {
  if (api.LAUNCH_AT === "") return;
  ok(api.target !== null, "LAUNCH_AT did not parse");
  eq(api.target, Date.parse(api.LAUNCH_AT), "parsed instant");
});

check("the launch sentence is rendered in the reader's own zone", () => {
  if (api.target === null) return;
  const shown = run.nodes["cd-when-local"].textContent;
  ok(shown && shown.length > 8, "the local-time sentence was never filled in");
  const inLondon = api.fmtLocal(api.target);
  ok(typeof inLondon === "string" && inLondon.length > 8, "fmtLocal returned nothing");
});

// ══════════════════════════════════════════════════════════════════════════
section("the three states");

check("a date in the future counts down", () => {
  const far = new Date(Date.now() + 5 * 86_400_000).toISOString().replace(/\.\d{3}Z$/, "+00:00");
  const r = runCountdown({ src: withConst(COUNTDOWN_SRC, "LAUNCH_AT", far) });
  eq(r.api.state(), "counting", "state");
  eq(r.nodes["cd-days"].textContent, "4", "days shown");
  eq(r.intervals.length, 1, "number of tickers started");
  eq(r.intervals[0].ms, 1000, "tick interval");
});

check("a date in the past says the Kickstarter is live, and stops ticking", () => {
  const r = runCountdown({ src: withConst(COUNTDOWN_SRC, "LAUNCH_AT", "2020-01-01T09:00:00-06:00") });
  eq(r.api.state(), "live", "state");
  eq(r.intervals.length, 0, "a finished countdown must not keep a timer running");
});

check("no date at all says the date is being set, and invents nothing", () => {
  const r = runCountdown({ src: withConst(COUNTDOWN_SRC, "LAUNCH_AT", "") });
  eq(r.api.state(), "pending", "state");
  eq(r.api.target, null, "target");
  eq(r.intervals.length, 0, "timer started with no date");
  eq(r.api.buildIcs(), "", "a calendar file was offered with no date in it");
});

check("an unparseable date is treated as no date, not as 1970", () => {
  const r = runCountdown({ src: withConst(COUNTDOWN_SRC, "LAUNCH_AT", "launch day soon") });
  eq(r.api.state(), "pending", "state");
  eq(r.api.target, null, "target");
});

check("the page re-reads the clock when a sleeping laptop comes back", () => {
  ok((run.docListeners.visibilitychange || []).length === 1,
    "nothing listens for visibilitychange, so a woken laptop shows a stale clock");
});

// ══════════════════════════════════════════════════════════════════════════
section("the calendar file");

check("it is offered when there is a date, and only then", () => {
  const added = run.nodes["cd-ctas"].children.filter(c => /calendar/i.test(c.textContent));
  eq(added.length, api.target === null ? 0 : 1, "calendar buttons in the hero");
  if (api.target !== null) ok((added[0].listeners.click || []).length === 1, "no click handler");
});

check("DTSTART is the launch instant in UTC", () => {
  if (api.target === null) return;
  const ics = api.buildIcs();
  const m = ics.match(/DTSTART:(\d{8}T\d{6}Z)/);
  ok(m, "no DTSTART in the calendar file");
  const expected = new Date(api.target).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  eq(m[1], expected, "DTSTART");
});

check("it is a calendar file a calendar will actually open", () => {
  if (api.target === null) return;
  const ics = api.buildIcs();
  ["BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT", "UID:", "DTSTAMP:",
   "SUMMARY:", "END:VEVENT", "END:VCALENDAR"].forEach(line =>
    ok(ics.includes(line), "missing " + line));
  ok(ics.includes("\r\n"), "iCalendar lines must be CRLF terminated");
  ok(!/\n(?!\r)/.test(ics.replace(/\r\n/g, "")), "a bare newline got in");
  const end = ics.match(/DTEND:(\d{8}T\d{6}Z)/);
  ok(end, "no DTEND");
  ok(Date.parse(ics.match(/DTSTART:(\d{8})T(\d{6})Z/) && "") !== 0, "DTSTART unreadable");
});

// ══════════════════════════════════════════════════════════════════════════
section("the Kickstarter button only exists once there is a Kickstarter");

check("with no campaign URL, nothing on the page links to Kickstarter", () => {
  eq(api.KS_URL, "", "KS_URL is set: this check is for the empty state");
  const linked = run.nodes["cd-ctas"].children.filter(c => /kickstarter\.com/.test(c.href || ""));
  eq(linked.length, 0, "a Kickstarter link appeared with no campaign URL set");
  ok(!/https?:\/\/[^"']*kickstarter\.com/.test(HTML),
    "launch.html links to kickstarter.com before the campaign exists");
});

check("filling the URL in puts the button in the hero, first", () => {
  const r = runCountdown({
    src: withConst(COUNTDOWN_SRC, "KS_URL", "https://www.kickstarter.com/projects/example/x")
  });
  const first = r.nodes["cd-ctas"].children[0];
  ok(/kickstarter\.com/.test(first.href), "the Kickstarter button is not first in the row");
  eq(first.target, "_blank", "target");
  eq(first.rel, "noopener noreferrer", "rel");
});

// ══════════════════════════════════════════════════════════════════════════
section("the tiers are shown, not sold");

check("all four tier buttons are disabled", () => {
  const buttons = [...HTML.matchAll(/<button class="btn [^"]*tier-locked"[^>]*>([^<]*)<\/button>/g)];
  eq(buttons.length, 4, "locked tier buttons");
  buttons.forEach(b => ok(/disabled/.test(b[0]), "a tier button is not disabled: " + b[0]));
  buttons.forEach(b => eq(b[1].trim(), "On Kickstarter soon", "button label"));
});

check("no checkout link reaches this page", () => {
  ok(!/buy\.stripe\.com/.test(HTML), "a Stripe payment link is live on the launch page");
  ok(!/<form/i.test(HTML), "a form on this page would be a checkout nobody is watching");
});

check("the names and prices match the homepage exactly", () => {
  const read = (src) => {
    const out = {};
    const re = /<div class="name">([^<]+)<\/div>\s*<div class="price">\$(\d+)<small>/g;
    let m;
    while ((m = re.exec(src))) out[m[1].trim()] = Number(m[2]);
    return out;
  };
  const here = read(HTML);
  const home = read(INDEX);
  eq(Object.keys(home).length, 4, "tiers found on the homepage");
  eq(Object.keys(here).length, 4, "tiers found on the launch page");
  Object.keys(home).forEach(name => {
    ok(name in here, "the homepage has a " + name + " tier and the launch page does not");
    eq(here[name], home[name], name + " price");
  });
});

check("the pledge is stated in the same words the homepage and the Store use", () => {
  // ONE basis, stated the same way on all three surfaces. The old sentence
  // carried two problems at once: a basis ("of every purchase") that the
  // other surfaces did not share, and a named charity presented as receiving
  // donations. Both are gone; what this check is really for, that the three
  // surfaces cannot drift apart, is unchanged.
  const SENTENCE = "5% goes to ocean conservation.";
  ok(HTML.includes(SENTENCE), "the pledge sentence has drifted from the homepage's wording");
  ok(INDEX.includes(SENTENCE), "the homepage no longer says it this way: update both or neither");
  ok(!/Surfrider/.test(HTML), "the launch page names no conservation charity");
  ok(HTML.includes("is not sponsored by, endorsed by, or officially partnered with any conservation organisation"),
    "the independence disclaimer must travel with the claim");
});

// ══════════════════════════════════════════════════════════════════════════
section("nothing is duplicated that could drift");

check("Game Night comes from the one shared source, not a second copy of the schedule", () => {
  ok(HTML.includes('id="cc-game-night"'), "the Game Night mount is missing");
  ok(/src="\/js\/game-night\.js\?v=/.test(HTML), "game-night.js is not loaded with a cache stamp");
  ok(!/Wednesday/.test(HTML), "the launch page states the Game Night day itself: it must not");
  ok(!/8:00/.test(HTML), "the launch page states the Game Night time itself: it must not");
});

check("the live numbers come from /api/stats, through the homepage's cache key", () => {
  ok(HTML.includes("/api/stats"), "the stats endpoint is not called");
  ok(HTML.includes("cc_live_stats_v1"), "not using the shared stats cache key");
  ok(INDEX.includes("cc_live_stats_v1"), "the homepage changed its cache key: update both");
  ["players", "games", "hours"].forEach(k =>
    ok(HTML.includes('data-placeholder="' + k + '"'), "no tile for " + k));
});

check("the email list points at the newsletter form, with a mailto fallback in the markup", () => {
  ok(HTML.includes("https://play.currentsandcritters.com/newsletter/join"),
    "the newsletter form URL is missing");
  ok(/data-email-list href="mailto:currentsandcritters@gmail\.com/.test(HTML),
    "the mailto fallback must stay in the markup");
});

// ══════════════════════════════════════════════════════════════════════════
section("the house rules about what reaches a screen");

check("no em dash anywhere on the page", () => {
  const n = (HTML.match(/—/g) || []).length;
  eq(n, 0, "em dashes in launch.html");
});

check("no emoji in the player-facing copy", () => {
  // Pictographs are what this is looking for. The BLACK STAR the whole
  // marketing site sets its eyebrows with (U+2605, as in "\u2605 Supporter
  // Tiers") and the arrows on its buttons are typographic marks, not emoji,
  // and are deliberately not in these ranges. U+2B50 is: that one is the
  // emoji star, which lives on the seat pill in the game and nowhere else.
  // U+FE0F catches a plain glyph wearing emoji presentation.
  const emoji = HTML.match(/[\u{1F000}-\u{1FAFF}\u{2B50}\u{FE0F}]/gu) || [];
  eq(emoji.length, 0, "emoji found: " + emoji.join(" "));
});

check("the page says what it is in its title and its share card", () => {
  ok(/<title>[^<]*Kickstarter[^<]*<\/title>/.test(HTML), "the title does not mention Kickstarter");
  ok(/property="og:title"/.test(HTML), "no og:title, and this page exists to be shared");
  ok(/property="og:image" content="https:\/\//.test(HTML), "og:image must be an absolute URL");
  ok(/rel="canonical" href="https:\/\/currentsandcritters\.com\/launch"/.test(HTML),
    "the canonical URL should be the clean /launch one");
});

runAll();
