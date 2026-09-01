#!/usr/bin/env node
/* Changing a chat's background, over and over, in a real browser.
 *
 * The reported bug was not "the picker is missing", it was "I choose a
 * background and then I cannot change it". That is a bug about the SECOND
 * pick, so no test that sets one wallpaper and checks it can ever catch it.
 * This drives the real picker in real Chrome and changes the wallpaper eight
 * times in a row, checking after every single tap that the pixel actually
 * moved: the CSS variable the conversation is painted with, the file it points
 * at, and the tile the sheet shows as chosen.
 *
 * It does not hand-copy any of that code. The picker, the writer, the painter
 * and the catalog are sliced straight out of preview-app.js, the drawer markup
 * out of preview.html, and the styling out of preview.css, so a change to any
 * of them shows up here instead of drifting away from a mock-up.
 *
 * The two failure modes it pins down beyond "does it change at all":
 *
 *  1. A PEER THAT REFUSES THE WRITE MUST NOT COST ME MY OWN WALLPAPER. The
 *     wallpaper is mirrored into every member's messages subcollection. The
 *     old code awaited those writes in conv-id order, so if the other player's
 *     doc rejected, it threw before my own copy was ever written and the
 *     picker looked dead. My copy is written first now; the fake Firestore
 *     here rejects every peer write to prove it.
 *
 *  2. IT MUST NOT WAIT FOR THE SERVER TO REPAINT. The listener is what
 *     normally refreshes the message cache, and it is not running in this
 *     harness at all, exactly like a slow or offline connection. The pick
 *     still has to show.
 *
 * Run:  node test_chat_background_change.js      (needs Google Chrome / Chromium)
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");
const HTML = fs.readFileSync(path.join(CLIENT, "preview.html"), "utf8");
const CSS = fs.readFileSync(path.join(CLIENT, "css/preview.css"), "utf8");
const APP = fs.readFileSync(path.join(CLIENT, "js/preview-app.js"), "utf8");

let pass = 0, fail = 0;
function check(cond, name, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}

// ── Slice the real code out of the monolith ─────────────────────────────────
function balanced(from, open, close) {
  let depth = 0;
  for (let i = from; i < APP.length; i++) {
    if (APP[i] === open) depth++;
    else if (APP[i] === close) { depth--; if (depth === 0) return i; }
  }
  throw new Error("unbalanced " + open + close);
}
function grabFn(name) {
  const decl = "function " + name + "(";
  const start = APP.indexOf(decl);
  if (start < 0) throw new Error("could not find function " + name + " in preview-app.js");
  // Keep an `async` prefix if the declaration has one.
  const head = APP.lastIndexOf("async ", start) === start - 6 ? start - 6 : start;
  return APP.slice(head, balanced(APP.indexOf("{", APP.indexOf(")", start)), "{", "}") + 1);
}
function grabArray(name) {
  const start = APP.indexOf("const " + name + " = [");
  if (start < 0) throw new Error("could not find const " + name);
  return APP.slice(start, balanced(APP.indexOf("[", start), "[", "]") + 1) + ";";
}

const CATALOG = grabArray("CHAT_BACKGROUNDS");
const ALIASES = APP.match(/const _CHAT_BG_BY_IMG = \{\};\s*CHAT_BACKGROUNDS\.forEach\(b => \{[\s\S]*?\}\);/)[0];
const FNS = ["_msgIsGroupMeta", "_msgGroupMeta", "_msgTs", "_msgChatBgResolve",
             "_msgChatBgFor", "_msgChatBgCacheLocal", "_msgApplyChatBg",
             "_msgChatBgMembers", "_msgSetChatBg", "_msgOpenBgSheet",
             "_msgCloseBgSheet", "_msgRenderBgSheet"].map(grabFn).join("\n\n");

// The drawer markup, including the conversation view and the picker sheet.
const drawerStart = HTML.indexOf('<div id="cc-msg-drawer">');
const drawerEnd = HTML.indexOf("\n</div>", HTML.indexOf('<div class="ccm-bgsheet"', drawerStart)) + "\n</div>".length;
const DRAWER = HTML.slice(drawerStart, HTML.indexOf("</div>", drawerEnd) + "</div>".length);
if (!DRAWER.includes('id="ccm-bgsheet"') || !DRAWER.includes('id="ccm-conv-view"')) {
  throw new Error("could not slice the messaging drawer out of preview.html");
}

// Every wallpaper in the catalog, read back out of the source we just sliced.
const CATALOG_LIST = new Function(CATALOG + "return CHAT_BACKGROUNDS;")();

// ── The page under test ─────────────────────────────────────────────────────
// A fake Firestore that accepts writes to MY subcollection and rejects every
// write to anybody else's, which is failure mode 1 above. No listener runs, so
// nothing feeds the cache back: failure mode 2.
const stubs = `
const $a = id => document.getElementById(id);
const escapeHtml = (v) => String(v == null ? "" : v)
  .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
  .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
function showToast(msg, kind) { window.__toasts.push(String(kind || "") + ": " + msg); }
window.__toasts = [];
window.__fishAvSrc = (u) => String(u || "");
window.__writes = [];
window.__rejected = 0;
const firebase = { firestore: { FieldValue: { serverTimestamp: () => "SERVER_TS" } } };
const MY_UID = "me";
let _authUser = { uid: MY_UID };
let _playerNickname = "TidePoolTim";
let _guestSessionActive = false;
let _msgAllMessages = [];
let _msgOpenConvId = "me__you";
function _msgShowRulesNeeded() { window.__rulesBanner = (window.__rulesBanner || 0) + 1; }
const _db = {
  collection: () => ({
    doc: (uid) => ({
      collection: () => ({
        doc: (docId) => ({
          set: (doc) => {
            window.__writes.push({ uid, docId, chatbg: doc.chatbg });
            if (uid !== MY_UID) {
              window.__rejected++;
              return Promise.reject({ code: "permission-denied" });
            }
            return Promise.resolve();
          },
        }),
      }),
    }),
  }),
};
`;

// Tap a tile the way a player does: open the sheet, click the tile, read back
// what the conversation is actually painted with.
const drive = `
const L = [];
const ok = (c, m) => L.push((c ? "PASS " : "FAIL ") + m);
const view = $a("ccm-conv-view");
const sheet = $a("ccm-bgsheet");
const grid = $a("ccm-bgsheet-grid");

// The drawer has to be a real, laid-out panel for any of this to mean anything.
$a("cc-msg-drawer").classList.add("open");
$a("ccm-list").style.display = "none";
view.style.display = "flex";
$a("ccm-bg-btn").style.display = "";
$a("ccm-bg-btn").addEventListener("click", _msgOpenBgSheet);
$a("ccm-bgsheet-close").addEventListener("click", _msgCloseBgSheet);

const painted = () => (view.style.getPropertyValue("--ccm-chat-bg") || "").trim();
const tileNames = () => [...grid.querySelectorAll(".ccm-bgtile-nm")].map(e => e.textContent);
const chosen = () => {
  const on = grid.querySelector(".ccm-bgtile.on .ccm-bgtile-nm");
  return on ? on.textContent : "";
};

async function tap(name) {
  $a("ccm-bg-btn").click();                       // open the picker like a player
  const tiles = [...grid.querySelectorAll(".ccm-bgtile")];
  const t = tiles.find(el => el.querySelector(".ccm-bgtile-nm").textContent === name);
  if (!t) { ok(false, "tile '" + name + "' exists in the picker"); return; }
  t.click();
  await new Promise(r => setTimeout(r, 0));       // let the async write settle
  await new Promise(r => setTimeout(r, 0));
}

(async () => {
  // ── The picker offers every wallpaper, none of them locked ──
  $a("ccm-bg-btn").click();
  const names = tileNames();
  ok(names[0] === "No Background", "the first tile clears the wallpaper (got '" + names[0] + "')");
  ok(names.length === ${CATALOG_LIST.length + 1},
     "every wallpaper is offered (" + names.length + " tiles for ${CATALOG_LIST.length} scenes + none)");
  ok(grid.querySelectorAll(".ccm-bgtile.locked").length === 0, "no tile is locked");
  ok(grid.querySelectorAll(".ccm-bgtile-lock").length === 0, "no padlock is drawn");
  ok([...grid.querySelectorAll(".ccm-bgtile")].every(t => !t.disabled), "every tile is clickable");
  _msgCloseBgSheet();

  // ── Change it, and change it, and change it ──
  // Eight taps, alternating so the same scene is also re-picked, never twice
  // in a row the same value except where that is the point.
  const order = ${JSON.stringify([
    "Kelp Forest", "Deep Ocean", "Coral Reef", "Arctic Ocean",
    "Open Water", "Pier", "Kelp Forest", "Tide Pool", "Artificial Reef",
  ])};
  const seen = [];
  for (let i = 0; i < order.length; i++) {
    const before = painted();
    await tap(order[i]);
    const after = painted();
    seen.push(after);
    ok(after !== "", "pick " + (i + 1) + " (" + order[i] + ") painted something");
    ok(/^url\\("\\/backgrounds\\/chat-[a-z-]+\\.png"\\)$/.test(after),
       "pick " + (i + 1) + " points at a wide chat scene: " + after);
    if (i > 0 && order[i] !== order[i - 1]) {
      ok(after !== before, "pick " + (i + 1) + " actually CHANGED the wallpaper (was " + before + ")");
    }
    ok(view.classList.contains("has-bg"), "pick " + (i + 1) + " leaves the view flagged has-bg");
    ok(sheet.style.display === "none", "pick " + (i + 1) + " closes the sheet so the scene is visible");
    // Re-opening must show the scene the conversation is really wearing.
    $a("ccm-bg-btn").click();
    ok(chosen() === order[i], "pick " + (i + 1) + " is the ringed tile on re-open (got '" + chosen() + "')");
    _msgCloseBgSheet();
  }
  ok(new Set(seen).size === new Set(order).size,
     "the " + new Set(order).size + " distinct scenes tapped produced " + new Set(seen).size + " distinct wallpapers");

  // ── Clearing, and picking again after clearing ──
  await tap("No Background");
  ok(painted() === "", "No Background clears the wallpaper");
  ok(!view.classList.contains("has-bg"), "cleared view drops has-bg");
  await tap("Tide Pool");
  ok(painted().includes("chat-tide-pool.png"), "a wallpaper can be chosen again after clearing");

  // ── Re-picking the SAME scene is legal, not a dead tap ──
  const same = painted();
  await tap("Tide Pool");
  ok(painted() === same, "re-picking the scene it already wears keeps it");
  ok(window.__toasts.length === 0, "nothing errored at the player: " + JSON.stringify(window.__toasts));

  // ── The peer rejected every mirror write; my wallpaper survived it ──
  ok(window.__rejected > 0, "the fake peer really did reject its mirror writes (" + window.__rejected + ")");
  const mine = window.__writes.filter(w => w.uid === "me");
  ok(mine.length === order.length + 3, "every tap wrote MY copy (" + mine.length + " writes)");
  ok(mine[0].docId === "cbg_me__you", "written to the one deterministic doc id, not a new doc per pick");
  ok(new Set(window.__writes.map(w => w.docId)).size === 1, "all picks share that single doc id");
  ok(!window.__rulesBanner, "a rejected peer write never shows the Firestore-rules banner");

  // ── An old medallion-path pick still paints, as the wide art ──
  _msgAllMessages = [{ id: "cbg_me__old", conv_id: "me__old", meta: true,
                       chatbg: "/backgrounds/bg-coral-reef.png", ts: null, read: true }];
  _msgOpenConvId = "me__old";
  _msgApplyChatBg();
  ok(painted() === 'url("/backgrounds/chat-coral-reef.png")',
     "a wallpaper stored under the old medallion path paints the wide art (got " + painted() + ")");
  _msgOpenConvId = "me__you";

  document.getElementById("out").textContent = L.join("\\n");
  window.__done = 1;
})();
`;

const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => fs.existsSync(p));

// ── 1. Behaviour: does the wallpaper actually change, every time ────────────
function behaviourPage() {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
${CSS}
html,body{margin:0;background:#0b2a4a;}
#cc-msg-drawer{display:flex !important;}
</style></head><body>${DRAWER}<div id="out"></div>
<script>${stubs}
${CATALOG}
${ALIASES}
${FNS}
${drive}
</scr` + `ipt></body></html>`;
}

// ── 2. Layout: the picker and the painted scene at real screen widths ───────
// Measured inside an iframe because headless Chrome refuses a window under
// 500px, and a phone is exactly where a grid of image tiles overflows.
function layoutPage(mode) {
  // mode "drawer" = the in-game right-hand panel; mode "page" = the Messages
  // sidebar tab, where the same element is re-hosted as .ccm-page. The picker
  // is absolutely positioned against the drawer, so page mode is its own risk.
  const pageClass = mode === "page" ? '.classList.add("ccm-page")' : '.classList.add("in-game")';
  const inner = `<!doctype html><html><head><meta charset="utf-8"><style>
${CSS}
html,body{margin:0;background:#0b2a4a;overflow-x:hidden;}
#cc-msg-drawer{display:flex !important;}
</style></head><body>${DRAWER}
<script>${stubs}
${CATALOG}
${ALIASES}
${FNS}
$a("cc-msg-drawer").classList.add("open");
$a("cc-msg-drawer")${pageClass};
$a("ccm-list").style.display = "none";
$a("ccm-conv-view").style.display = "flex";
_msgAllMessages = [{ id:"cbg_me__you", conv_id:"me__you", meta:true,
                     chatbg:"/backgrounds/chat-coral-reef.png", ts:null, read:true }];
_msgApplyChatBg();
_msgOpenBgSheet();
</scr` + `ipt></body></html>`;

  return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#111;}iframe{border:0;display:block;}</style></head><body>
<iframe id="f" width="1280" height="900"></iframe><div id="out"></div>
<script>
const SRC = ${JSON.stringify(inner).replace(/<\//g, "<\\/")};
const WIDTHS = [360, 390, 430, 600, 820, 1024, 1280, 1680];
const L = [];
const f = document.getElementById("f");
function measure(w) {
  const d = f.contentDocument, win = f.contentWindow;
  const ok = (c, m) => L.push((c ? "PASS " : "FAIL ") + "${mode} " + w + "px: " + m);
  const r = el => el.getBoundingClientRect();
  const vw = win.innerWidth;
  ok(vw === w, "the iframe really is " + w + "px wide (got " + vw + ")");
  ok(d.documentElement.scrollWidth <= vw + 1,
     "nothing scrolls sideways (content " + d.documentElement.scrollWidth + ")");

  const view = d.getElementById("ccm-conv-view");
  ok(view.classList.contains("has-bg"), "the conversation is wearing its wallpaper");
  ok(win.getComputedStyle(view).backgroundImage.includes("chat-coral-reef.png"),
     "the painted image is the wide scene");
  ok(win.getComputedStyle(view).backgroundSize === "cover", "the scene covers the panel");

  const box = d.querySelector(".ccm-bgsheet-box");
  const bb = r(box);
  ok(bb.width > 200 && bb.height > 100, "the picker has real size (" + Math.round(bb.width) + "x" + Math.round(bb.height) + ")");
  ok(bb.left >= -1 && bb.right <= vw + 1, "the picker is inside the window");

  const tiles = [...d.querySelectorAll(".ccm-bgtile")];
  ok(tiles.length === ${CATALOG_LIST.length + 1}, "all " + ${CATALOG_LIST.length + 1} + " tiles rendered (" + tiles.length + ")");
  tiles.forEach((el, i) => {
    const b = r(el);
    ok(b.width >= 84 && b.height >= 60,
       "tile " + i + " is a real tap target (" + Math.round(b.width) + "x" + Math.round(b.height) + ")");
    ok(b.left >= bb.left - 1 && b.right <= bb.right + 1, "tile " + i + " is inside the picker");
  });
  const last = r(tiles[tiles.length - 1]);
  ok(last.bottom <= bb.bottom + 1 || box.scrollHeight > box.clientHeight,
     "the last tile is reachable (visible, or the picker scrolls)");
  document.getElementById("out").textContent = L.join("\\n");
}
let i = 0;
f.onload = () => {
  if (i >= WIDTHS.length) return;
  measure(WIDTHS[i]);
  i++;
  if (i < WIDTHS.length) { f.width = WIDTHS[i]; f.srcdoc = SRC; }
};
f.width = WIDTHS[0];
f.srcdoc = SRC;
</scr` + `ipt></body></html>`;
}

function runInChrome(html, label) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cc-chatbg-"));
  const file = path.join(tmp, "p.html");
  fs.writeFileSync(file, html);
  let dom = "";
  try {
    dom = execFileSync(CHROME, [
      "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
      "--window-size=1800,1100", "--virtual-time-budget=20000",
      "--dump-dom", "file://" + file,
    ], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 120000,
         maxBuffer: 64 * 1024 * 1024 });
  } catch (e) {
    check(false, label + ": Chrome ran", e.message);
    return;
  }
  const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
  if (!m || !m[1].trim()) { check(false, label + ": results came back from the page"); }
  else {
    m[1].split("\n").filter(Boolean).forEach(line => {
      check(line.startsWith("PASS "), line.replace(/^(PASS|FAIL) /, ""));
    });
  }
  fs.rmSync(tmp, { recursive: true, force: true });
}

if (!CHROME) {
  console.log("\nSKIP: no Chrome/Chromium found, this test needs a browser.");
  process.exit(0);
}

console.log("\nchanging the wallpaper, nine taps in a row");
runInChrome(behaviourPage(), "behaviour");

console.log("\nthe picker and the painted scene, phone through desktop");
runInChrome(layoutPage("drawer"), "layout/drawer");
console.log("\nthe same, as the Messages page (.ccm-page)");
runInChrome(layoutPage("page"), "layout/page");

console.log(`\n${fail ? "FAILED " + fail + " of " + (pass + fail) : "All " + pass} checks${fail ? "" : " passed"}.`);
process.exit(fail ? 1 : 0);
