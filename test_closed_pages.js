#!/usr/bin/env node
/* The two pages that are shut, opened the way a player opens them.
 *
 * Run:  node test_closed_pages.js        (needs Google Chrome)
 *
 * The Critter Pass (CCCP_PASS_CLOSED in js/critter-pass.js) and the in-game
 * Store (PHST_STORE_CLOSED in js/preview-app.js) are both on standby. Their
 * own suites already render each one's CONTENT and find no button in it.
 * This file tests the thing neither of those can: what a person actually gets
 * when they CLICK the tab in the real app.
 *
 * Why that is a different test, and worth its own file:
 *
 *   1. Those suites call the render function directly. Nobody reaches a page
 *      that way. switchTab() is the real door, and it does more than render:
 *      it repaints the panel, sets the background, and INSERTS A GUEST NOTE
 *      into the panel for guests — a note that carries a Sign In button.
 *      A page can be perfectly empty and still be handed a button by the
 *      thing that opened it. Rendering the module alone cannot see that.
 *   2. They audit the module's own root element. This audits THE WHOLE PANEL,
 *      which is a bigger box: the panel holds the module root, the guest note
 *      and the Store's card header. Anything clickable in any of them is a
 *      way to interact with a page that is supposed to have none.
 *   3. "No <button>" is not the same as "cannot be reached". This walks every
 *      element in the panel and reads its real tabIndex, so a div someone
 *      made focusable, an <a href>, an input or an onclick all fail here.
 *
 * Every door is used. For these two pages that is the sidebar item (neither
 * has a horizontal .ph-tab, which is asserted below so a new one cannot be
 * added without this list being brought up to date) and _switchPhTab, the
 * public jump API any deep-link into a tab would go through. Each page is
 * also opened, left, and opened again, because a cover that only survives the
 * first paint is not a closed page.
 *
 * The pages must still OPEN. "Nothing to interact with" is one failure away
 * from "nothing at all", and a blank panel is not what was asked for: the
 * Coming soon notice has to be on screen and readable.
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

// The two pages under test, and the panel each one lives in.
const CLOSED = [
  { tab: "critterpass", panel: "ph-panel-critterpass", label: "Critter Pass" },
  { tab: "store",       panel: "ph-panel-store",       label: "Store" },
];

// ══════════════════════════════════════════════════════════════════════════
//  BOTH SWITCHES ARE ON
//  Read from the shipped files, so this suite cannot pass by testing a page
//  that somebody quietly reopened.
// ══════════════════════════════════════════════════════════════════════════
console.log("\nboth switches are on in the shipped files");
check("the Critter Pass is closed", /const CCCP_PASS_CLOSED = true;/.test(PASSJS));
check("the Store is closed", /const PHST_STORE_CLOSED = true;/.test(APP));
check("both tabs are still in the tab map, so both still OPEN",
      APP.includes('critterpass:"ph-panel-critterpass"') && APP.includes('store:"ph-panel-store"'));
check("clicking either one still runs its renderer",
      APP.includes('if (name === "critterpass")  _renderCritterPassTab();')
      && APP.includes('if (name === "store")        renderPhStore();'));

// THE DOOR LIST BELOW HAS TO BE COMPLETE, so the two ways in are pinned here.
// Both pages are sidebar-only today. If either ever gains a horizontal tab,
// that is a third door this suite is not opening, and this is the check that
// says so rather than letting the new one go untested.
for (const page of CLOSED) {
  check(`the ${page.label} has a sidebar item to click`,
        new RegExp(`ph-snav-item[^>]*data-tab="${page.tab}"`).test(HTML));
  check(`…and no horizontal tab, so the sidebar is the only way a player opens it`,
        !new RegExp(`class="ph-tab[^"]*" data-tab="${page.tab}"`).test(HTML));
}
check("_switchPhTab is published, so a deep-link has a door too",
      APP.includes("window._switchPhTab = switchTab;"));

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
          text: (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 50),
        });
      }
    });
    var text = panel ? (panel.innerText || panel.textContent || "").replace(/\\s+/g, " ").trim() : "";
    return {
      visible: vis(panel),
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
  var phase = 1, tick = 0, idx = 0, guard = 0, round = 0;
  var DOORS = ["snav", "api"];

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
        if (lob && lob.classList.contains("visible")) { phase = 4; guard = 0; }
        return;
      }

      // ── Open each closed page, through each door, twice ──────────────
      // ROUND 0 clicks the sidebar item, which is the only thing a player can
      // click: neither of these two pages has a horizontal .ph-tab (asserted
      // in the static checks, so a new one cannot be added without this list
      // being brought up to date). ROUND 1 goes through _switchPhTab, the
      // public jump API any deep-link into a tab would use.
      //
      // Between the two rounds every page has been left and come back to,
      // which is where a first-paint-only cover would give itself away.
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
        var btn = document.querySelector(sel);
        if (door === "snav" && !btn) {
          out.pages[page.tab + ":" + door] = { missingDoor: sel };
          idx++; guard = 0; return;
        }
        if (guard === 0) {
          if (door === "snav") click(btn);
          else window._switchPhTab(page.tab);
          guard = 1; return;
        }
        guard++;
        if (guard < 18) return;            // let the async renders land

        var panel = document.getElementById(page.panel);
        var res = audit(panel);
        res.door = door;
        // Only on the second round, once everything has been seen once: a
        // synthetic click storm changes the page, so it must not run before
        // the honest audits are taken.
        if (round === 1) res.clickStorm = clickEverything(panel);
        // Re-audit after the storm to prove it did not open anything.
        if (round === 1) res.afterStorm = audit(panel).count;
        out.pages[page.tab + ":" + door] = res;
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

console.log("\nclicking the real tabs in the real app (guest session)");
if (!D) {
  check("the harness reached the lobby and clicked the tabs", false, "no result");
} else {
  check("the walkthrough ran to the end", D.phase === "done", D.phase);
  check("nothing threw anywhere in the session",
        (D.errors || []).length === 0, (D.errors || []).slice(0, 3).join(" | "));

  for (const page of CLOSED) {
    for (const door of ["snav", "api"]) {
      const key = page.tab + ":" + door;
      const r = D.pages[key] || {};
      const via = door === "snav" ? "from the sidebar" : "by deep-link (_switchPhTab)";
      console.log(`\n  ${page.label}, opened ${via}`);

      // It has to OPEN. A page nobody can interact with is not the same
      // thing as a page that is not there.
      check("the page opens", r.visible === true, JSON.stringify(r).slice(0, 120));
      check("it says Coming soon", r.comingSoon === true, (r.text || "").slice(0, 120));
      check("there is something to read, not a blank panel",
            (r.chars || 0) > 60, r.chars);

      // And nothing on it can be acted on, anywhere in the panel.
      check("nothing in the whole panel can be clicked or tabbed to",
            r.count === 0,
            (r.actionable || []).map(a => `${a.tag}.${a.cls}(${a.why})"${a.text}"`).join(" | "));
      check("no guest note was inserted into it",
            r.guestNote === 0, r.guestNote);
      check("no Payment Link is anywhere in its markup", r.stripe === false);

      if (door === "api") {
        // Second visit: it was opened, left for the other page, and opened
        // again. A cover that only survives the first paint fails here.
        check("it is still shut on the second visit", r.comingSoon === true);
        const cs = r.clickStorm || {};
        check("clicking every element in it navigates nowhere",
              cs.navigated === false, JSON.stringify(cs));
        check("…and sends nothing", cs.newPosts === 0, cs.newPosts);
        check("…and opens nothing that was not there before",
              r.afterStorm === 0, r.afterStorm);
      }
    }
  }

  console.log("\n  nothing points at the closed pages either");
  const b = D.badge || {};
  check("the sidebar's Critter Pass badge is not showing a count",
        b.shown === false && !/[1-9]/.test(b.text || ""), JSON.stringify(b));

  console.log("\n  nothing was bought, claimed or redeemed in the whole session");
  check("no spend request was sent at any point",
        (D.spend || []).length === 0, (D.spend || []).join(", "));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
