#!/usr/bin/env node
/* Real-browser render check for the TRADE CARD IN THE CHAT.
 *
 * Run:  node test_trade_card_render.js      (needs Google Chrome installed)
 *
 * test_trade_ui.js proves the wiring exists in the source. This one puts the
 * REAL card builder and the REAL preview.css into headless Chrome and looks at
 * what actually paints, in both chat surfaces, which is the only way to catch
 * the class of bug a stubbed document cannot: a card that renders empty, an
 * "undefined" in visible text, a button that is enabled when it must not be,
 * an optimistic state that never appears, or a card that scrolls a phone
 * sideways.
 *
 * The card replaced three centered system lines per trade ("X started a
 * trade.", "X confirmed the trade. It is waiting on Y now.", "Trade completed:
 * ..."), so the states they used to narrate are exactly the states checked
 * here: opened, one side confirmed, both confirmed, canceled.
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("SKIP: no Chrome/Chromium found: cannot run the trade card render check.");
  process.exit(0);
}

const CSS = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const SRC = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");

// ── Lift the real code out of the 39k-line browser file ─────────────────────
function slice(startMarker, endMarker) {
  const i = SRC.indexOf(startMarker);
  if (i < 0) throw new Error("marker not found in preview-app.js: " + startMarker);
  const j = SRC.indexOf(endMarker, i + startMarker.length);
  if (j < 0) throw new Error("end marker not found after " + startMarker + ": " + endMarker);
  return SRC.slice(i, j);
}
const CARD_CODE = slice("// ══ THE TRADE CARD, IN THE CHAT ══",
                        "// ── Live sync + DM Trade-button pulse");
const PURE_CODE = [
  slice("function _msgTimeLabel(ts) {", "\n    // Group my cached messages"),
  slice("function _trErrText(code) {", "\n    // POST to a /api/trade/"),
  slice("function _trOfferEmpty(o) {", "\n    // Render one side's items"),
].join("\n");

const AV  = "/avatars/sardine.png";
const AV2 = "/avatars/lobster.png";
const BG  = "/backgrounds/reef.png";
const ME = "me", THEM = "reef";

// A trade state exactly as _trade_public builds it on the server.
const state = (over) => Object.assign({
  tradeId: "me__reef", conv_id: "me__reef",
  participants: [ME, THEM],
  names: { [ME]: "You", [THEM]: "Reef" },
  offers: {
    [ME]:   { coins: 1200, passes: 0, xp: 0, avatars: [AV], backgrounds: [] },
    [THEM]: { coins: 0, passes: 1, xp: 500, avatars: [AV2], backgrounds: [BG] },
  },
  confirmed: { [ME]: false, [THEM]: false },
  version: 3, status: "open", created_by: ME, result: null, last_error: null,
}, over || {});

// The mirror doc the chat actually holds.
const doc = (st) => ({ id: "trade_me__reef", conv_id: "me__reef", trade: true,
                       trade_id: "me__reef", trade_state: st, trade_status: st.status,
                       sender: THEM, sender_name: "Reef", receiver: ME, receiver_name: "You",
                       text: "Trade request", ts: new Date(), read: false });

const CASES = {
  fresh:      state(),
  empty:      state({ offers: { [ME]: { coins: 0, passes: 0, xp: 0, avatars: [], backgrounds: [] },
                                [THEM]: { coins: 0, passes: 0, xp: 0, avatars: [], backgrounds: [] } } }),
  theirTurn:  state({ confirmed: { [ME]: true, [THEM]: false } }),
  myTurn:     state({ confirmed: { [ME]: false, [THEM]: true } }),
  completed:  state({ status: "completed", confirmed: { [ME]: true, [THEM]: true } }),
  canceled:   state({ status: "canceled" }),
  errored:    state({ last_error: "not_enough_coins" }),
  manyItems:  state({ offers: {
                 [ME]: { coins: 0, passes: 0, xp: 0,
                         avatars: [AV, AV2, "/avatars/a.png", "/avatars/b.png", "/avatars/c.png"],
                         backgrounds: [BG] },
                 [THEM]: { coins: 99999999, passes: 12, xp: 250000, avatars: [], backgrounds: [] } } }),
};

const page = `<!doctype html><html><head><meta charset="utf-8">
<style>${CSS}</style>
<style>
  body { margin: 0; font-family: system-ui, sans-serif; }
  /* The two real containers, at the two real widths. The drawer normally
     lives off-screen (position:fixed, translateX(100%)) until it is opened;
     it is put back in flow here so its cards can actually be measured. */
  #cc-msg-drawer { position: static; transform: none; width: 100%; max-width: 100%; }
  .ccm-messages, #pv-chat-conv-msgs { display: flex; flex-direction: column; gap: 8px; overflow-y: auto; }
  #panelwrap { width: 300px; }
  #pv-chat-conv-msgs { padding: 10px 12px; background: #123; }
</style></head><body>
<div id="cc-msg-drawer"><div class="ccm-messages" id="drawer"></div></div>
<div id="panelwrap"><div id="pv-chat-conv-msgs"></div></div>
<pre id="RESULT"></pre>
<script>
"use strict";
const out = { errors: [], cards: {}, posts: [], toasts: [], opened: [], overflow: {} };
const CASES = ${JSON.stringify(CASES)};
const DOCS  = ${JSON.stringify(Object.fromEntries(Object.entries(CASES).map(([k, v]) => [k, doc(v)])))};
const ME = ${JSON.stringify(ME)};

// ── Stubs for everything the lifted card code reaches out to ───────────────
const $a = (id) => document.getElementById(id);
let _authUser = { uid: ME };
let _guestSessionActive = false;
let _msgOpenConvId = "me__reef";
const _msgChangeCbs = [];
let _postReply = { ok: true };
function _msgRenderOpenConversation() {}
function _trToast(m, k) { out.toasts.push([m, k || ""]); }
function _trOpen(uid, name) { out.opened.push([uid, name]); }
function _trRefreshMyProfile() {}
function _trOverlayOpen() { return false; }
let _trPeerUid = null, _trState = null;
function _trRender() {}
function _trAvatarName(p) { return "Sardine"; }
function _trBgName(p) { return "Coral Reef"; }
// A 1px transparent gif, so a broken file:// image cannot change the layout.
function _trImgSrc(p) { return "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"; }
async function _trPost(action, payload) {
  out.posts.push({ action, payload });
  await new Promise(r => setTimeout(r, 30));
  return _postReply;
}
${PURE_CODE}
${CARD_CODE}

const wait = (ms) => new Promise(r => setTimeout(r, ms));
const vis = (el) => (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();

(async () => {
  try {
    const drawer = document.getElementById("drawer");
    const panel  = document.getElementById("pv-chat-conv-msgs");
    for (const key of Object.keys(CASES)) {
      const d = DOCS[key];
      d.ts = new Date();
      const card = _trCardBuild(d, "drawer");
      if (!card) { out.errors.push(key + ": builder returned null"); continue; }
      drawer.appendChild(card);
      const btns = [...card.querySelectorAll(".cctc-btn")];
      out.cards[key] = {
        text: vis(card),
        cls: card.className,
        title: vis(card.querySelector(".cctc-ttl") || {}),
        note: vis(card.querySelector(".cctc-note") || {}),
        noteCls: (card.querySelector(".cctc-note") || {}).className || "",
        buttons: btns.map(b => ({ label: b.textContent, cls: b.className, off: b.disabled })),
        hasX: !!card.querySelector(".cctc-x"),
        thumbs: card.querySelectorAll(".cctc-thumb").length,
        pills: [...card.querySelectorAll(".cctc-pill")].map(p => p.textContent),
        heads: [...card.querySelectorAll(".cctc-side-h")].map(h => h.textContent),
        popped: card.classList.contains("cctc-pop"),
        width: card.getBoundingClientRect().width,
      };
      // Every card is drawn in the in-game panel too, at its narrow width.
      const pc = _trCardBuild(d, "panel");
      if (pc) panel.appendChild(pc);
    }

    // The pop plays once per status, not on every repaint.
    out.repop = !!(_trCardBuild(DOCS.fresh, "drawer") || {}).classList.contains("cctc-pop");

    // ── Confirm, straight from the card ──────────────────────────────────
    out.posts = [];
    const live = document.createElement("div"); document.body.appendChild(live);
    const draw = () => {
      live.innerHTML = "";
      const c = _trCardBuild(DOCS.fresh, "live");
      live.appendChild(c);
      return c;
    };
    _msgChangeCbs.push(draw);
    let card = draw();
    const confirmBtn = [...card.querySelectorAll(".cctc-btn")].find(b => /Confirm/.test(b.textContent));
    const after = JSON.parse(JSON.stringify(CASES.fresh));
    after.confirmed[ME] = true;
    _postReply = { ok: true, completed: false, state: after };
    confirmBtn.click();
    await wait(10);
    out.whileBusy = [...live.querySelectorAll(".cctc-btn")].map(b => b.disabled);
    await wait(200);
    out.confirmPost = out.posts[0] || null;
    out.afterConfirm = [...live.querySelectorAll(".cctc-btn")].map(b => b.textContent);
    // The server's own copy is still the OLD one: the card must already be
    // showing mine, or confirming reads as nothing happening for a round trip.
    out.optimistic = vis(live.querySelector(".cctc-note"));

    // ── Cancel, from the x ───────────────────────────────────────────────
    out.posts = [];
    const cancelled = JSON.parse(JSON.stringify(CASES.fresh));
    cancelled.status = "canceled";
    _postReply = { ok: true, state: cancelled };
    live.querySelector(".cctc-x").click();
    await wait(200);
    out.cancelPost = out.posts[0] || null;
    out.afterCancel = vis(live.querySelector(".cctc-note"));

    // ── Edit opens the full trade screen ─────────────────────────────────
    // The cancel above left my own copy leading the server, which is the point
    // of it; the rest of this probe wants the plain server state again.
    delete _trCardLocal["me__reef"];
    const c2 = _trCardBuild(DOCS.fresh, "edit");
    [...c2.querySelectorAll(".cctc-btn")].find(b => b.textContent === "Edit").click();

    // ── A guest is shown the trade but cannot act on it ──────────────────
    _guestSessionActive = true;
    const g = _trCardBuild(DOCS.fresh, "guest");
    out.guest = { buttons: g.querySelectorAll(".cctc-btn").length, x: g.querySelectorAll(".cctc-x").length,
                  text: vis(g) };
    _guestSessionActive = false;

    // ── A card that is not mine is not drawn at all ──────────────────────
    const foreign = JSON.parse(JSON.stringify(DOCS.fresh));
    foreign.trade_state.participants = ["zoe", "kai"];
    out.foreign = _trCardBuild(foreign, "drawer");

    out.overflow.drawer = { scroll: document.documentElement.scrollWidth,
                            client: document.documentElement.clientWidth,
                            box: Math.round(drawer.clientWidth),
                            cards: [...drawer.querySelectorAll(".cctc")]
                              .map(c => Math.round(c.getBoundingClientRect().width)) };
    out.overflow.panel  = { scroll: panel.scrollWidth, client: panel.clientWidth };
    out.overflow.panelCards = [...panel.querySelectorAll(".cctc")]
      .map(c => Math.round(c.getBoundingClientRect().width));
  } catch (e) {
    out.errors.push("THREW: " + (e && e.stack ? e.stack : String(e)));
  }
  document.getElementById("RESULT").textContent = "@@" + JSON.stringify(out) + "@@";
})();
</script>
</body></html>`;

const file = path.join(os.tmpdir(), `cc_trade_card_${Date.now()}.html`);
fs.writeFileSync(file, page);

function run(width, height) {
  const dom = execFileSync(CHROME, [
    "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    `--window-size=${width},${height}`, "--virtual-time-budget=30000",
    "--dump-dom", "file://" + file,
  ], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  const m = dom.match(/<pre id="RESULT">@@([\s\S]*?)@@<\/pre>/);
  if (!m) throw new Error("no result payload in the DOM dump (the probe did not finish)");
  return JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                        .replace(/&lt;/g, "<").replace(/&gt;/g, ">"));
}

let pass = 0, fail = 0;
const check = (n, c, extra) => {
  if (c) { pass++; console.log("  ✓ " + n); }
  else { fail++; console.log("  ✗ FAIL: " + n + (extra ? "  → " + extra : "")); }
};

console.log("phone (390×844), the width the drawer opens at:");
const R = run(390, 844);
check("the card builder ran without throwing", R.errors.length === 0, R.errors.join(" | "));

const C = R.cards;
check("every state drew a card", Object.keys(C).length === 8, Object.keys(C).join(", "));

console.log("\nan open trade shows both sides and what to do about it");
const f = C.fresh || {};
check("it names who it is with", f.title === "Trade with Reef", f.title);
check("it labels both halves", (f.heads || []).join("/") === "You give/You get", (f.heads || []).join("/"));
check("the cosmetics are shown as pictures", f.thumbs === 3, String(f.thumbs));
check("the balances are counted", (f.pills || []).length === 3, (f.pills || []).join(" | "));
check("my coins are on it", (f.pills || []).some(p => /1,200/.test(p)), (f.pills || []).join(" | "));
check("their pass is on it", (f.pills || []).some(p => /1 pass$/.test(p)), (f.pills || []).join(" | "));
check("their XP is on it", (f.pills || []).some(p => /500 XP/.test(p)), (f.pills || []).join(" | "));
check("it offers Edit and Confirm", (f.buttons || []).map(b => b.label).join("/") === "Edit/Confirm",
      (f.buttons || []).map(b => b.label).join("/"));
check("and a way out of it", f.hasX === true);
check("Confirm is live", (f.buttons || [])[1] && (f.buttons || [])[1].off === false);
check("it pops in when it arrives", f.popped === true);
check("and does not pop again on the next repaint", R.repop === false);

console.log("\nno state prints a placeholder at a player");
for (const [k, c] of Object.entries(C)) {
  const bad = /undefined|NaN|\[object Object\]|null/.exec(c.text || "");
  check(`${k} reads as English`, !bad, bad ? c.text : "");
  check(`${k} says something`, (c.text || "").length > 25, c.text);
}

console.log("\nthe states the old system lines used to narrate");
check("an empty trade says so and will not let you confirm it",
      /Nothing on the table/.test(C.empty.note)
      && C.empty.buttons[1].off === true, C.empty.note);
check("waiting on them reads as waiting",
      /You confirmed\. Waiting for Reef\./.test(C.theirTurn.note), C.theirTurn.note);
check("and my own button says it is armed",
      C.theirTurn.buttons[1].label === "✓ Confirmed"
      && /\bon\b/.test(C.theirTurn.buttons[1].cls), JSON.stringify(C.theirTurn.buttons[1]));
check("waiting on me reads as my turn, and says what happens next",
      /Reef confirmed\. Confirm and it sends\./.test(C.myTurn.note), C.myTurn.note);
check("and that line is the one that is coloured", /\bgo\b/.test(C.myTurn.noteCls), C.myTurn.noteCls);
check("a completed trade says it completed",
      /Trade completed/.test(C.completed.note) && /cctc-completed/.test(C.completed.cls), C.completed.note);
check("it offers no Confirm to press twice",
      C.completed.buttons.map(b => b.label).join("/") === "Trade again",
      C.completed.buttons.map(b => b.label).join("/"));
check("and no way to cancel a finished trade", C.completed.hasX === false);
check("a canceled trade says nothing moved",
      /canceled\. Nothing moved/.test(C.canceled.note) && /cctc-canceled/.test(C.canceled.cls),
      C.canceled.note);
check("a failed swap explains itself on the card",
      /enough Critter Coins/i.test(C.errored.note) && /both confirmations were reset/i.test(C.errored.note),
      C.errored.note);
check("a long offer is summarised, not spilled",
      C.manyItems.thumbs === 5 && /\+2/.test(C.manyItems.text), C.manyItems.text);

console.log("\nthe card can finish the trade by itself");
check("Confirm posts to the confirm endpoint", R.confirmPost && R.confirmPost.action === "confirm",
      JSON.stringify(R.confirmPost));
check("it names the peer, because the trade screen is usually closed",
      R.confirmPost && R.confirmPost.payload.peerUid === "reef", JSON.stringify(R.confirmPost));
check("and the version it is confirming", R.confirmPost && R.confirmPost.payload.version === 3
      && R.confirmPost.payload.confirm === true, JSON.stringify(R.confirmPost));
check("both buttons go dead while it is in flight",
      (R.whileBusy || []).length === 2 && R.whileBusy.every(Boolean), JSON.stringify(R.whileBusy));
check("my tap shows before Firestore catches up",
      (R.afterConfirm || []).join("/") === "Edit/✓ Confirmed", (R.afterConfirm || []).join("/"));
check("and the card says who it is waiting on now",
      /Waiting for Reef/.test(R.optimistic || ""), R.optimistic);
check("the x cancels the trade", R.cancelPost && R.cancelPost.action === "cancel",
      JSON.stringify(R.cancelPost));
check("and the card turns into a canceled one straight away",
      /canceled\. Nothing moved/.test(R.afterCancel || ""), R.afterCancel);
check("Edit opens the full trade screen with that player",
      (R.opened || []).some(o => o[0] === "reef" && o[1] === "Reef"), JSON.stringify(R.opened));

console.log("\nwho may act on it");
const G = R.guest || {};
check("a guest is shown the trade", (G.text || "").includes("Trade with Reef"), G.text);
check("but has no buttons to press", G.buttons === 0 && G.x === 0, JSON.stringify(G));
check("a trade between two other people is not drawn at all", R.foreign === null);

console.log("\nit fits the phone and the in-game panel");
check("the page never scrolls sideways at 390px",
      R.overflow.drawer.scroll <= R.overflow.drawer.client,
      JSON.stringify(R.overflow.drawer));
check("every card in the drawer is really drawn, and stays inside it",
      (R.overflow.drawer.cards || []).length === 8
      && R.overflow.drawer.cards.every(w => w > 200 && w <= R.overflow.drawer.box),
      JSON.stringify(R.overflow.drawer.cards) + " in " + R.overflow.drawer.box);
check("nor does the 300px in-game panel",
      R.overflow.panel.scroll <= R.overflow.panel.client, JSON.stringify(R.overflow.panel));
check("and every card in it stays inside it",
      (R.overflow.panelCards || []).length === 8
      && R.overflow.panelCards.every(w => w <= R.overflow.panel.client),
      JSON.stringify(R.overflow.panelCards) + " in " + R.overflow.panel.client);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
