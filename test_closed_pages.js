#!/usr/bin/env node
/* The two pages that are gone, reached for the way a player would reach them.
 *
 * Run:  node test_closed_pages.js        (needs Google Chrome)
 *
 * The Critter Pass and the in-game Store are OFF THE MENU. They used to be
 * shut — on the menu, opening onto a "Coming soon" cover — and that is a
 * weaker thing than this: a shut page is still a door, and a door with a sign
 * on it is still something a player walks up to and pushes. Now the two
 * sidebar items are commented out of preview.html and PH_CLOSED_TABS in
 * js/preview-app.js sends anything that still asks for those tabs by name to
 * the Overview instead.
 *
 * So this suite asks two questions a static read of the files cannot:
 *
 *   1. IS THERE A DOOR? Not "is the button hidden" but "is the button there
 *      at all", asked of the live DOM after the app has booted and built its
 *      sidebar. A hidden button can be un-hidden by any stylesheet; a button
 *      that was never rendered cannot be clicked by anyone.
 *   2. WHAT HAPPENS TO SOMEONE WHO KNOWS THE NAME? _switchPhTab is published
 *      on window, and every deep-link, old shortcut and endgame jump goes
 *      through it. Called with "store" or "critterpass" it must land the
 *      player on a real page and leave the closed panel unshown — not show an
 *      empty panel, and not throw.
 *
 * The panels are still in the HTML and the renderers are still in the app,
 * because none of this is deleted; what is tested is that nothing shows them.
 * Both standby flags (CCCP_PASS_CLOSED, PHST_STORE_CLOSED) are ALSO still on,
 * and still asserted here: the pages are shut as well as unreachable, so that
 * uncommenting a sidebar item by accident cannot put a live checkout back on
 * screen. test_supporter_tiers_ui.js renders each shelf directly and proves
 * the Coming soon cover is still what they would paint.
 *
 * And the whole session is watched at fetch level: nothing may be bought,
 * claimed or redeemed at any point, through any of this.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const read   = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (detail != null ? "  [" + detail + "]" : "")); }
}

const HTML   = read("preview.html");
const APP    = read("js/preview-app.js");
const PASSJS = read("js/critter-pass.js");
const TUT    = read("js/tutorials.js");

// The two pages under test, and the panel each one lives in.
const CLOSED = [
  { tab: "critterpass", panel: "ph-panel-critterpass", label: "Critter Pass" },
  { tab: "store",       panel: "ph-panel-store",       label: "Store" },
];

// Every panel the app can show, so the driver can answer "where did the player
// actually land" by looking at the screen instead of trusting the tab name.
const PANEL_IDS = [
  "overview", "howto", "normal", "competitive", "history", "friends", "messages",
  "achievements", "leaderboard", "clans", "prestige", "levelpass",
  "critterpass", "store",
].map(t => ({ tab: t, id: "ph-panel-" + t }));

// ══════════════════════════════════════════════════════════════════════════
//  BOTH SWITCHES ARE ON
//  Read from the shipped files, so this suite cannot pass by testing a page
//  that somebody quietly reopened.
// ══════════════════════════════════════════════════════════════════════════
console.log("\nthe two pages are off the menu in the shipped files");
check("the Critter Pass is still shut too", /const CCCP_PASS_CLOSED = true;/.test(PASSJS));
check("the Store is still shut too", /const PHST_STORE_CLOSED = true;/.test(APP));
check("PH_CLOSED_TABS names both pages",
      /const PH_CLOSED_TABS = \["store", "critterpass"\];/.test(APP));
check("…and switchTab sends them somewhere that exists",
      APP.includes("name = phTabOrFallback(name);")
      && /PH_CLOSED_FALLBACK = "overview"/.test(APP));
check("both are normalised on the sidebar-aware wrapper as well, not just the inner one",
      (APP.match(/name = phTabOrFallback\(name\);/g) || []).length >= 2);

// Nothing is deleted. The panels and the renderers stay exactly where they
// were, so putting the pages back is uncommenting, not rebuilding. What is
// asserted is that nothing REACHES them.
check("both panels are still in the HTML, untouched",
      HTML.includes('id="ph-panel-critterpass"') && HTML.includes('id="ph-panel-store"'));
check("both renderers are still wired behind the redirect",
      APP.includes('if (name === "critterpass")  _renderCritterPassTab();')
      && APP.includes('if (name === "store")        renderPhStore();'));
check("both are still in the tab map, so restoring is one array away",
      APP.includes('critterpass:"ph-panel-critterpass"') && APP.includes('store:"ph-panel-store"'));

// THE DOOR LIST HAS TO BE COMPLETE. Neither page may have a sidebar item or a
// horizontal tab in the shipped markup. The commented-out block is not a door:
// what matters is that no LIVE button carries the data-tab, which the DOM
// checks below confirm for real.
// Comments stripped the way the browser strips them, so a button inside a
// multi-line <!-- --> block counts as absent rather than as a line that merely
// does not start with "<!--". The live-DOM checks below are the real proof;
// this one catches a restore that puts the markup back without meaning to.
const HTML_LIVE = HTML.replace(/<!--[\s\S]*?-->/g, "");
check("stripping comments left the file mostly intact, so the regex is not eating the page",
      HTML_LIVE.length > HTML.length * 0.8, `${HTML_LIVE.length} of ${HTML.length}`);
for (const page of CLOSED) {
  const live = HTML_LIVE.split("\n").filter(l =>
    new RegExp(`ph-snav-item[^>]*data-tab="${page.tab}"`).test(l));
  check(`the ${page.label} sidebar item is commented out, so there is nothing to click`,
        live.length === 0, live.join(" | ").slice(0, 120));
  check(`…and it has no horizontal tab either`,
        !new RegExp(`class="ph-tab[^"]*" data-tab="${page.tab}"`).test(HTML));
  check(`…and the real markup is kept verbatim for the day it comes back`,
        new RegExp(`id="snav-${page.tab}"`).test(HTML));
}
check("_switchPhTab is still published, so the redirect is what a deep-link meets",
      APP.includes("window._switchPhTab = switchTab;"));

// The menu tour visited both pages. Those steps must be stepped over, or the
// tour tells the player to click a button that is not there and then waits on
// a tab that can never go active.
console.log("\nthe menu tour does not walk into them");
check("the four steps that visit them are skipped",
      (TUT.match(/skipIf: gtOnStandby,/g) || []).length === 4,
      (TUT.match(/skipIf: gtOnStandby,/g) || []).length);
check("gtOnStandby is on, and is a one-line restore",
      /const gtOnStandby = \(\) => true;/.test(TUT));
for (const t of ["snav-critterpass", "snav-store"]) {
  const step = TUT.slice(TUT.indexOf(`target: "#${t}"`), TUT.indexOf(`target: "#${t}"`) + 200);
  check(`the "click ${t}" step is one of them`, step.includes("skipIf: gtOnStandby"));
}

// ══════════════════════════════════════════════════════════════════════════
//  THE DRIVER
//  Boots the real app as a guest and clicks the real tabs, the way
//  test_guest_access.js does.
// ══════════════════════════════════════════════════════════════════════════
const DRIVER = `
<div id="out" style="display:none"></div>
<script>
(function () {
  var out = { phase: "boot", tick: 0, errors: [], pages: {}, order: [] };
  window.addEventListener("error", function (e) {
    out.errors.push("window: " + (e && e.message));
  });
  var _err = console.error;
  console.error = function () {
    try {
      var s = Array.prototype.map.call(arguments, function (a) {
        return (a && a.message) ? a.message : String(a);
      }).join(" ");
      // The same expected noise test_guest_access.js allows, for the same
      // reason: this harness serves multiplayer/client and nothing else, so
      // there is no backend, no signed-in session, and firebase-config.js
      // (which lives at the repo root) does not resolve. None of that is the
      // behaviour under test. Anything else is a real error and fails the run.
      if (!/permission|network|firestore|Failed to fetch|ERR_|auth\\/|Quota|CONFIGURATION|resource_load_failed|api_fetch_failed|api_http_error/i.test(s)) {
        out.errors.push("console.error: " + s.slice(0, 200));
      }
    } catch (_) {}
    return _err.apply(console, arguments);
  };

  var q = function (s) { return document.querySelector(s); };
  var vis = function (el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    var cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none";
  };
  var click = function (el) {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  };
  function mark() { return document.getElementById("out"); }
  function finish() { out.done = true; mark().textContent = JSON.stringify(out); }

  // Everything a person could act on, anywhere in the panel.
  //
  // tabIndex is READ OFF THE ELEMENT rather than matched from a selector list:
  // that is the browser's own answer to "can this be tabbed to", so a div made
  // focusable with tabindex="0", a contenteditable, or any element type nobody
  // thought to put in a selector all get counted.
  function audit(panel) {
    var all = panel ? Array.prototype.slice.call(panel.querySelectorAll("*")) : [];
    var actionable = [];
    all.forEach(function (el) {
      var tag = el.tagName.toLowerCase();
      var why = [];
      if (tag === "button") why.push("button");
      if (tag === "a" && el.hasAttribute("href")) why.push("a[href]");
      if (tag === "input" || tag === "select" || tag === "textarea") why.push(tag);
      if (typeof el.tabIndex === "number" && el.tabIndex >= 0) why.push("tabIndex=" + el.tabIndex);
      if (el.hasAttribute("onclick")) why.push("onclick");
      if (el.isContentEditable) why.push("contenteditable");
      if (el.hasAttribute("role") && /button|link|checkbox|tab|menuitem/.test(el.getAttribute("role"))) {
        why.push("role=" + el.getAttribute("role"));
      }
      if (why.length) {
        actionable.push({
          tag: tag,
          cls: (el.className || "") + "",
          why: why.join(","),
          reachable: !!el.offsetParent || el === document.activeElement,
          text: (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 50),
        });
      }
    });
    // Reachable, not merely present: an element inside a display:none panel
    // has no offsetParent, so it cannot be clicked or tabbed to however many
    // buttons the panel's markup still contains.
    var reachable = actionable.filter(function (a) { return a.reachable; });
    var text = panel ? (panel.innerText || panel.textContent || "").replace(/\\s+/g, " ").trim() : "";
    return {
      visible: vis(panel),
      reachableCount: reachable.length,
      reachable: reachable,
      actionable: actionable,
      count: actionable.length,
      text: text.slice(0, 400),
      chars: text.length,
      comingSoon: /coming soon/i.test(text),
      // Stripe must not be one fetch away on a shut page either.
      stripe: panel ? panel.innerHTML.indexOf("buy.stripe.com") >= 0 : false,
      guestNote: panel ? panel.querySelectorAll(".ph-guest-note").length : -1,
    };
  }

  // Every click the panel could receive, actually dispatched. If some handler
  // is still bound to a container, this is what finds it.
  function clickEverything(panel) {
    var before = { url: location.href, posts: (window.__ccSpyPosts || []).length };
    var els = panel ? Array.prototype.slice.call(panel.querySelectorAll("*")) : [];
    els.push(panel);
    els.forEach(function (el) { if (el) { try { click(el); } catch (_) {} } });
    return {
      navigated: location.href !== before.url,
      newPosts: (window.__ccSpyPosts || []).length - before.posts,
    };
  }

  // Anything the page sends while it is shut, captured at fetch level: a
  // closed page may still READ its state (that is what keeps an owner's extra
  // challenge slots fresh), but it must never buy, claim or redeem.
  window.__ccSpyPosts = [];
  var _fetch = window.fetch;
  window.fetch = function (u, o) {
    try {
      var url = (typeof u === "string") ? u : (u && u.url) || "";
      var method = ((o && o.method) || "GET").toUpperCase();
      if (method === "POST") window.__ccSpyPosts.push(url);
    } catch (_) {}
    return _fetch.apply(window, arguments);
  };

  var PAGES = ${JSON.stringify(CLOSED)};
  // Which panel is on screen right now, so "where did the player land" is
  // answered by looking, not assumed.
  var PANELS = ${JSON.stringify(PANEL_IDS)};
  function shownPanel() {
    for (var i = 0; i < PANELS.length; i++) {
      if (vis(document.getElementById(PANELS[i].id))) return PANELS[i].tab;
    }
    return null;
  }

  var phase = 1, tick = 0, idx = 0, guard = 0, round = 0;
  // ROUND 0 looks for the door in the live DOM: after the app has booted and
  // built its sidebar, is there anything a player could click? ROUND 1 and 2
  // are the deep-link, the only way left to ask for these tabs by name, done
  // twice because a redirect that only holds on the first call is not one.
  var DOORS = ["snav", "api", "api-again"];

  var iv = setInterval(function () {
    tick++;
    out.tick = tick;
    out.phase = "p" + phase + ":r" + round + ":i" + idx + ":g" + guard;
    if (tick > 2000) { out.phase = "timeout:" + out.phase; finish(); clearInterval(iv); return; }
    try {
      // ── Get into the lobby as a guest ────────────────────────────────
      if (phase === 1) {
        if (tick > 25) {
          var ls = q("#auth-loading-screen"); if (ls) ls.classList.add("hidden");
          var as = q("#auth-screen"); if (as) as.classList.remove("hidden");
        }
        var g = q("#auth-guest-btn");
        if (g && vis(g)) { click(g); phase = 2; }
        return;
      }
      if (phase === 2) {
        var go = q("#auth-guest-go-btn"), nk = q("#auth-guest-nick");
        if (go && vis(go)) {
          if (nk) { nk.value = "ShutBot"; nk.dispatchEvent(new Event("input", { bubbles: true })); }
          click(go); phase = 3;
        }
        return;
      }
      if (phase === 3) {
        var lob = q("#auth-stats-lobby");
        if (lob && lob.classList.contains("visible")) {
          phase = 4; guard = 0;
          // Where the sidebar can actually take a player, for the record: the
          // two removed tabs must not be in it.
          out.sidebar = Array.prototype.map.call(
            document.querySelectorAll(".ph-snav-item[data-tab]"),
            function (b) { return b.dataset.tab; });
        }
        return;
      }

      // ── Reach for each removed page, through each door ───────────────
      if (phase === 4) {
        var page = PAGES[idx];
        if (!page) {
          round++;
          idx = 0;
          if (round >= DOORS.length) { phase = 5; guard = 0; return; }
          return;
        }
        var door = DOORS[round];
        var sel = '.ph-snav-item[data-tab="' + page.tab + '"]';

        // ── The door itself ──────────────────────────────────────────
        // Not "is it hidden" but "is it there". Any button carrying this tab,
        // plus the badge span that used to hang off the Critter Pass item.
        if (door === "snav") {
          var btn = document.querySelector(sel);
          out.pages[page.tab + ":" + door] = {
            door: door,
            buttonExists: !!btn,
            anyElementWithTab: document.querySelectorAll('[data-tab="' + page.tab + '"]').length,
            idExists: !!document.getElementById("snav-" + page.tab),
          };
          out.order.push(page.tab + ":" + door);
          idx++; guard = 0; return;
        }

        // ── The deep-link ────────────────────────────────────────────
        if (guard === 0) {
          // Stand somewhere else first, so "it stayed put" cannot pass by
          // accident: the player is on Achievements and asks for the Store.
          try { window._switchPhTab("achievements"); } catch (_) {}
          guard = 1; return;
        }
        if (guard === 1) {
          out.pages[page.tab + ":" + door] = { door: door, from: shownPanel() };
          try { window._switchPhTab(page.tab); }
          catch (e) { out.pages[page.tab + ":" + door].threw = String(e && e.message); }
          guard = 2; return;
        }
        guard++;
        if (guard < 18) return;            // let any async render land

        var panel = document.getElementById(page.panel);
        var res = out.pages[page.tab + ":" + door];
        var a = audit(panel);
        for (var k in a) res[k] = a[k];
        res.landedOn = shownPanel();
        // Only on the last round, once the honest audits are taken: a
        // synthetic click storm changes the page.
        if (door === "api-again") {
          res.clickStorm = clickEverything(panel);
          res.afterStorm = audit(panel).reachableCount;
        }
        out.order.push(page.tab + ":" + door);
        idx++; guard = 0;
        return;
      }

      // ── The sidebar badge must not be advertising the closed page ────
      if (phase === 5) {
        var badge = q("#snav-critterpass-badge");
        out.badge = {
          present: !!badge,
          shown: !!(badge && vis(badge)),
          text: badge ? (badge.textContent || "").trim() : "",
        };
        // Everything POSTed across the whole session.
        out.posts = (window.__ccSpyPosts || []).slice(0, 40);
        out.spend = out.posts.filter(function (u) {
          return /\\/(buy|claim|claim-all|redeem|checkout|purchase)/.test(u);
        });
        out.phase = "done";
        finish();
        clearInterval(iv);
        return;
      }
    } catch (e) {
      out.errors.push("driver: " + (e && e.message));
      out.phase = "threw"; finish(); clearInterval(iv);
    }
  }, 60);
})();
</script>`;

const PORT = 8890 + (process.pid % 300);
const SERVER_SRC = `
  const fs=require("fs"),path=require("path"),http=require("http");
  const ROOT=${JSON.stringify(CLIENT)};
  const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",
    ".json":"application/json",".png":"image/png",".jpg":"image/jpeg",
    ".webp":"image/webp",".svg":"image/svg+xml",".ico":"image/x-icon",
    ".m4a":"audio/mp4",".webmanifest":"application/manifest+json"};
  http.createServer((req,res)=>{
    const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
    const f=path.join(ROOT,rel);
    if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);res.end();return;}
    res.writeHead(200,{"Content-Type":MIME[path.extname(f)]||"application/octet-stream"});
    fs.createReadStream(f).pipe(res);
  }).listen(${PORT});
`;

if (!CHROME) {
  console.log("\nGoogle Chrome not found: this suite needs a real browser.");
  process.exit(1);
}

const tmp = path.join(CLIENT, "_closed_drive.html");
fs.writeFileSync(tmp, HTML + DRIVER);
const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });
try { execFileSync(process.execPath, ["-e", "setTimeout(()=>{},700)"]); } catch (_) {}

let D = null;
try {
  for (let attempt = 0; attempt < 3 && !D; attempt++) {
    const dom = execFileSync(CHROME, ["--headless", "--disable-gpu", "--no-sandbox",
      "--hide-scrollbars", "--window-size=1440,900", "--virtual-time-budget=240000",
      "--dump-dom", `http://localhost:${PORT}/_closed_drive.html?game_window=1`],
      { encoding: "utf8", maxBuffer: 64e6, stdio: ["ignore", "pipe", "ignore"] });
    const m = /<div id="out"[^>]*>([\s\S]*?)<\/div>/.exec(dom);
    if (!m) continue;
    const raw = m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                    .replace(/&lt;/g, "<").replace(/&gt;/g, ">");
    try { D = JSON.parse(raw); } catch (_) {}
  }
} finally {
  try { server.kill(); } catch (_) {}
  try { fs.unlinkSync(tmp); } catch (_) {}
}

console.log("\nreaching for the removed pages in the real app (guest session)");
if (!D) {
  check("the harness reached the lobby", false, "no result");
} else {
  check("the walkthrough ran to the end", D.phase === "done", D.phase);
  check("nothing threw anywhere in the session",
        (D.errors || []).length === 0, (D.errors || []).slice(0, 3).join(" | "));

  console.log("\n  the sidebar a player is actually given");
  const nav = D.sidebar || [];
  check("it has items on it, so this is a real sidebar and not an empty read",
        nav.length >= 8, JSON.stringify(nav));
  check("the Critter Pass is not one of them", nav.indexOf("critterpass") === -1, JSON.stringify(nav));
  check("the Store is not one of them", nav.indexOf("store") === -1, JSON.stringify(nav));

  for (const page of CLOSED) {
    // ── Is there a door at all? ────────────────────────────────────
    const d = D.pages[page.tab + ":snav"] || {};
    console.log(`\n  ${page.label}: is there anything to click?`);
    check("no sidebar button for it exists in the live DOM",
          d.buttonExists === false, JSON.stringify(d));
    check("nothing at all in the app carries its data-tab",
          d.anyElementWithTab === 0, d.anyElementWithTab);
    check("its old sidebar id resolves to nothing",
          d.idExists === false, d.idExists);

    // ── And what happens to someone who knows the name? ────────────
    for (const door of ["api", "api-again"]) {
      const r = D.pages[page.tab + ":" + door] || {};
      const nth = door === "api" ? "asked for by name (_switchPhTab)" : "asked for a second time";
      console.log(`\n  ${page.label}, ${nth}`);

      check("the call does not throw", r.threw == null, r.threw);
      check("the player was standing somewhere else first",
            r.from === "achievements", r.from);
      check("the page does not open", r.visible === false, JSON.stringify(r).slice(0, 140));
      check("they land on a real page instead of a blank screen",
            r.landedOn === "overview", r.landedOn);
      check("nothing in the panel can be clicked or tabbed to",
            r.reachableCount === 0,
            (r.reachable || []).map(a => `${a.tag}.${a.cls}(${a.why})"${a.text}"`).join(" | "));
      check("no guest note with a Sign in button was inserted into it",
            r.guestNote === 0, r.guestNote);
      check("no Payment Link is anywhere in its markup", r.stripe === false);

      if (door === "api-again") {
        const cs = r.clickStorm || {};
        check("clicking every element in it navigates nowhere",
              cs.navigated === false, JSON.stringify(cs));
        check("…and sends nothing", cs.newPosts === 0, cs.newPosts);
        check("…and opens nothing that was not there before",
              r.afterStorm === 0, r.afterStorm);
      }
    }
  }

  console.log("\n  nothing points at the removed pages either");
  const b = D.badge || {};
  check("the Critter Pass sidebar badge is gone with the item it hung off",
        b.present === false, JSON.stringify(b));

  console.log("\n  nothing was bought, claimed or redeemed in the whole session");
  check("no spend request was sent at any point",
        (D.spend || []).length === 0, (D.spend || []).join(", "));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
