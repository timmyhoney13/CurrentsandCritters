/* Clan System — client module smoke test (node vm, no browser).
 *
 * Loads the real js/clans-ui.js against a stub __ccClans bridge and checks the
 * public hooks the rest of the app depends on:
 *   • module registers window.__ccClansRender / __ccClanClaimGame /
 *     __ccClanTradePoint / __ccClanInvite / __ccClanCanInvite /
 *     __ccClansPrimeTabIcon
 *   • end-game claim posts /api/clan/claim-game ONCE per room (session dedup),
 *     uppercases the room id, and toasts the earned points + daily-goal bonus
 *   • trade-point hook toasts
 *   • disabled bridge → module registers nothing (hard off-switch)
 *   • the clan critter on the Clans nav button: painted from cache the moment
 *     sign-in resolves (so it's there on every tab, before the Clans tab is
 *     ever opened), corrected by /home, and swapped back to the shield when
 *     the player has no clan
 *
 * Run:  node test_clans_ui.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = fs.readFileSync(path.join(__dirname, "multiplayer/client/js/clans-ui.js"), "utf8");

let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name); }
}

function makeEnv({ enabled = true, postResp } = {}) {
  const calls = [];
  const toasts = [];
  const bridge = {
    ENABLED: enabled,
    APP_BUILD: "test",
    get: async () => ({ ok: true }),
    // The REAL bridge (preview-app's apiPost) resolves to an envelope —
    // { ok, status, data } — and THROWS when the request never lands. The
    // stubs must do the same or they test a contract that doesn't exist.
    post: async (p, b) => {
      calls.push({ p, b });
      const r = typeof postResp === "function" ? postResp(p, b) : (postResp || { ok: true });
      if (r === null) throw new Error("network down");
      return { ok: true, status: 200, data: r };
    },
    toast: (m, t) => toasts.push({ m: String(m), t }),
    nickname: () => "Tester",
    authUser: () => ({ uid: "u1", getIdToken: async () => "tok" }),
    idToken: async () => "tok",
    avSrc: (u) => u,
    animalAvatars: () => [{ id: "clownfish", name: "Clownfish", img: "/avatars/clownfish.png" }],
  };
  const documentStub = {
    querySelector: () => null,
    getElementById: () => null,
    createElement: () => ({ style: {}, classList: { add() {}, remove() {}, toggle() {} },
                            appendChild() {}, addEventListener() {}, setAttribute() {} }),
    head: { appendChild() {} },
    body: { appendChild() {}, removeChild() {}, contains: () => false },
  };
  const windowStub = { __ccClans: bridge };
  const sandbox = { window: windowStub, document: documentStub, console,
                    setInterval: () => 0, clearInterval: () => {},
                    setTimeout: (f) => 0, clearTimeout: () => {},
                    location: { search: "" }, confirm: () => true, Date, Math, JSON, Number, String };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: "clans-ui.js" });
  return { sandbox, windowStub, calls, toasts };
}

// A variant with a real-enough #cc-clans-root so we can read back what the
// module actually paints into the tab (makeEnv's document has no elements).
function makeEnvDom({ enabled = true, hasBridge = true, authed = true, homeResp } = {}) {
  const rootEl = { innerHTML: "", children: [], className: "", style: {},
                   appendChild() {}, addEventListener() {},
                   classList: { add() {}, remove() {}, toggle() {} } };
  const documentStub = {
    querySelector: (s) => (s === "#cc-clans-root" ? rootEl : null),
    getElementById: (i) => (i === "cc-clans-root" ? rootEl : null),
    createElement: () => ({ style: {}, classList: { add() {}, remove() {}, toggle() {} },
                            appendChild() {}, addEventListener() {}, setAttribute() {} }),
    createTextNode: () => ({}),
    head: { appendChild() {} },
    body: { appendChild() {}, removeChild() {}, contains: () => false },
  };
  const posts = [];
  const bridge = {
    ENABLED: enabled, APP_BUILD: "test",
    get: async () => ({ ok: true }),
    post: async (p) => { posts.push(p);
      const payload = (homeResp && p === "/api/clan/home")
        ? homeResp
        : { ok: true, season: { number: 1, name: "Riptide", ends_ts: 0 }, top3: [] };
      return { ok: true, status: 200, data: payload };   // the real bridge envelope
    },
    toast: () => {},
    nickname: () => "Tester",
    authUser: () => (authed ? { uid: "u1", getIdToken: async () => "tok" } : null),
    idToken: async () => "tok",
    avSrc: (u) => u,
    animalAvatars: () => [],
  };
  const windowStub = {};
  if (hasBridge) windowStub.__ccClans = bridge;
  const sandbox = { window: windowStub, document: documentStub, console,
                    setInterval: () => 0, clearInterval: () => {},
                    setTimeout: () => 0, clearTimeout: () => {},
                    location: { search: "" }, confirm: () => true,
                    Date, Math, JSON, Number, String };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: "clans-ui.js" });
  return { windowStub, rootEl, posts };
}

// A variant with the two Player-Home Clans nav buttons (and a localStorage), so
// the critter that replaces the shield glyph can be read back without a browser.
// Chrome + the real CSS cover the look; this covers the wiring: cache first,
// then the payload, and the shield back when there's no clan.
function makeEnvNav({ authed = true, homeResp, stored } = {}) {
  const store = {};
  if (stored) store.cc_clan_tabicon = JSON.stringify(stored);
  const mkBtn = (cls) => {
    const svg = { tag: "svg", style: {} };
    const btn = {
      className: cls, kids: [svg], firstChild: svg,
      querySelector(sel) {
        if (sel === "svg") return svg;
        return this.kids.find(k => k.className === "ccC-navcritter") || null;
      },
      insertBefore(node) {
        this.kids.unshift(node);
        this.firstChild = node;
        // Real nodes know their parent, and the module removes the critter
        // through it when the shield has to come back.
        node.parentNode = { removeChild: (n) => { btn.kids = btn.kids.filter(k => k !== n); } };
      },
    };
    return btn;
  };
  const buttons = [mkBtn("ph-snav-item"), mkBtn("ph-tab")];
  const documentStub = {
    querySelector: () => null,
    querySelectorAll: (sel) => (/data-tab="clans"/.test(sel) ? buttons : []),
    getElementById: () => null,
    createElement: () => ({ style: {}, attrs: {}, classList: { add() {}, remove() {} },
                            appendChild() {}, addEventListener() {}, parentNode: null,
                            setAttribute(k, v) { this.attrs[k] = v; },
                            getAttribute(k) { return this.attrs[k]; } }),
    head: { appendChild() {} },
    body: { appendChild() {}, removeChild() {}, contains: () => false },
  };
  const posts = [];
  const bridge = {
    ENABLED: true, APP_BUILD: "test",
    get: async () => ({ ok: true }),
    post: async (p) => { posts.push(p);
      return { ok: true, status: 200,
               data: (p === "/api/clan/home" ? (homeResp || { ok: true }) : { ok: true }) };
    },
    toast: () => {}, nickname: () => "Tester",
    authUser: () => (authed ? { uid: "u1", getIdToken: async () => "tok" } : null),
    idToken: async () => "tok",
    avSrc: (u) => u,
    animalAvatars: () => [],
  };
  const sandbox = { window: { __ccClans: bridge }, document: documentStub, console,
                    localStorage: { getItem: (k) => (k in store ? store[k] : null),
                                    setItem: (k, v) => { store[k] = String(v); } },
                    setInterval: () => 0, clearInterval: () => {},
                    setTimeout: () => 0, clearTimeout: () => {},
                    location: { search: "" }, confirm: () => true,
                    Date, Math, JSON, Number, String };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: "clans-ui.js" });
  const worn = () => buttons.map(b => {
    const img = b.querySelector("img.ccC-navcritter");
    return { src: img ? img.getAttribute("src") : "",
             shield: b.querySelector("svg").style.display !== "none" };
  });
  return { windowStub: sandbox.window, buttons, worn, posts, store };
}

(async () => {
  console.log("module registration:");
  const env = makeEnv({
    postResp: (p) => p.endsWith("claim-game")
      ? { ok: true, points: 2, clan_name: "Reef Riders", goal_done: true }
      : { ok: true },
  });
  const W = env.windowStub;
  check("__ccClansRender registered", typeof W.__ccClansRender === "function");
  check("__ccClanClaimGame registered", typeof W.__ccClanClaimGame === "function");
  check("__ccClanTradePoint registered", typeof W.__ccClanTradePoint === "function");
  check("__ccClanInvite registered", typeof W.__ccClanInvite === "function");
  check("__ccClanCanInvite registered", typeof W.__ccClanCanInvite === "function");
  check("__ccClansPrimeTabIcon registered", typeof W.__ccClansPrimeTabIcon === "function");
  check("a document without querySelectorAll or localStorage doesn't break the module",
        typeof W.__ccClansRender === "function");

  console.log("end-game claim:");
  await W.__ccClanClaimGame("abcd");
  const claims = env.calls.filter(c => c.p === "/api/clan/claim-game");
  check("posts one claim", claims.length === 1);
  check("room id uppercased", claims[0] && claims[0].b.room_id === "ABCD");
  check("idToken attached", claims[0] && claims[0].b.idToken === "tok");
  check("points toast shown", env.toasts.some(t => t.m.includes("+2 Clan Point")));
  check("daily-goal toast shown", env.toasts.some(t => t.m.includes("Daily Goal")));
  await W.__ccClanClaimGame("abcd");
  await W.__ccClanClaimGame("ABCD");
  check("same room never claimed twice", env.calls.filter(c => c.p === "/api/clan/claim-game").length === 1);

  console.log("claim retry on a dead network:");
  let flaky = 0;
  const env2 = makeEnv({
    postResp: (p) => {
      if (!p.endsWith("claim-game")) return { ok: true };
      flaky++;
      return flaky === 1 ? null : { ok: true, points: 3, clan_name: "Reef Riders" };
    },
  });
  await env2.windowStub.__ccClanClaimGame("ROOM");
  check("no toast when the request never landed", !env2.toasts.some(t => t.m.includes("Clan Point")));
  await env2.windowStub.__ccClanClaimGame("ROOM");
  check("a dropped request can be retried", flaky === 2);
  check("retry awards the points", env2.toasts.some(t => t.m.includes("+3 Clan Point")));
  await env2.windowStub.__ccClanClaimGame("ROOM");
  check("but a landed claim is still one-and-done", flaky === 2);

  console.log("trade point hook:");
  W.__ccClanTradePoint(1);
  check("trade toast shown", env.toasts.some(t => t.m.includes("daily clan trade")));

  console.log("invite by friend code:");
  const envI = makeEnv({
    postResp: (p, b) => (p.endsWith("/invite") && b.to_code === "2809"
      ? { ok: true, name: "LemmeSeeThemToes" }
      : { ok: false, error: "no_user" }),
  });
  const okI = await envI.windowStub.__ccClanInvite("", "", "2809");
  const inv = envI.calls.filter(c => c.p === "/api/clan/invite");
  check("the code goes out as to_code", inv.length === 1 && inv[0].b.to_code === "2809");
  check("invite reported as sent", okI === true);
  check("the toast names who the code resolved to",
        envI.toasts.some(t => t.m.includes("LemmeSeeThemToes")));
  // The profile / Messages buttons pass a uid + name and must keep working.
  const envP = makeEnv({ postResp: () => ({ ok: true }) });
  await envP.windowStub.__ccClanInvite("u7", "Reefy");
  const invP = envP.calls.filter(c => c.p === "/api/clan/invite")[0];
  check("profile invite still sends the uid", invP && invP.b.to_uid === "u7");
  check("profile invite falls back to the name it knows",
        envP.toasts.some(t => t.m.includes("Reefy")));
  // An unknown code has to say so — not fail silently.
  const envBad = makeEnv({ postResp: () => ({ ok: false, error: "ambiguous_code" }) });
  const okBad = await envBad.windowStub.__ccClanInvite("", "", "4242");
  check("a refused invite returns false", okBad === false);
  check("and explains a shared code in plain words",
        envBad.toasts.some(t => /add their name/i.test(t.m)));

  console.log("invite permission probe:");
  const before = env.calls.length;
  const can = W.__ccClanCanInvite();
  check("unknown state returns false", can === false);
  await new Promise(r => setImmediate(r));
  check("and primes /api/clan/home in the background",
        env.calls.slice(before).some(c => c.p === "/api/clan/home"));

  console.log("hard off-switch:");
  const off = makeEnv({ enabled: false });
  check("disabled bridge registers nothing", typeof off.windowStub.__ccClansRender !== "function");

  // The clan critter on the Clans nav button. It has to be there from every
  // OTHER tab too, so none of this may wait for the Clans tab to be opened.
  console.log("clan critter on the nav button:");
  const HOME_FAV = { ok: true, season: { number: 1, name: "Riptide", ends_ts: 0 }, top3: [],
                     my_clan_full: { icon: "/avatars/clownfish.png",
                                     favorite_critter: "/avatars/narwhal.png" } };
  const navEnv = makeEnvNav({ homeResp: HOME_FAV });
  await new Promise(r => setImmediate(r));
  await new Promise(r => setImmediate(r));
  check("the clan home is fetched at load, without opening the tab",
        navEnv.posts.some(p => p === "/api/clan/home"));
  check("both nav buttons wear the vote winner",
        navEnv.worn().every(b => b.src === "/avatars/narwhal.png"), JSON.stringify(navEnv.worn()));
  check("the shield glyph is hidden while the critter is up",
        navEnv.worn().every(b => b.shield === false));
  check("the critter is remembered for the next page load",
        JSON.parse(navEnv.store.cc_clan_tabicon || "{}").icon === "/avatars/narwhal.png");

  // Nobody has voted yet → the clan's own icon, not the shield.
  const navIcon = makeEnvNav({ homeResp: { ok: true, season: { number: 1, name: "R", ends_ts: 0 },
                                           top3: [], my_clan_full: { icon: "/avatars/clownfish.png" } } });
  await new Promise(r => setImmediate(r));
  await new Promise(r => setImmediate(r));
  check("with no votes yet it falls back to the clan's own icon",
        navIcon.worn().every(b => b.src === "/avatars/clownfish.png"), JSON.stringify(navIcon.worn()));

  // The cached critter paints before the payload lands, so there is no flash of
  // shield on a page the player has already used.
  const navCached = makeEnvNav({ stored: { uid: "u1", icon: "/avatars/lobster.png" },
                                 homeResp: HOME_FAV });
  check("the remembered critter is up before any request comes back",
        navCached.worn().every(b => b.src === "/avatars/lobster.png"), JSON.stringify(navCached.worn()));
  await new Promise(r => setImmediate(r));
  await new Promise(r => setImmediate(r));
  check("and the payload corrects it",
        navCached.worn().every(b => b.src === "/avatars/narwhal.png"), JSON.stringify(navCached.worn()));

  // Another account's critter must never show up on this one's button.
  const navOther = makeEnvNav({ stored: { uid: "someone-else", icon: "/avatars/lobster.png" },
                                homeResp: { ok: true, season: { number: 1, name: "R", ends_ts: 0 }, top3: [] } });
  check("a cached critter belonging to another account is ignored",
        navOther.worn().every(b => !b.src && b.shield));

  // No clan (and signed out) → the shield, which is what a non-member sees.
  const navNone = makeEnvNav({ homeResp: { ok: true, season: { number: 1, name: "R", ends_ts: 0 }, top3: [] } });
  await new Promise(r => setImmediate(r));
  await new Promise(r => setImmediate(r));
  check("no clan leaves the shield alone",
        navNone.worn().every(b => !b.src && b.shield), JSON.stringify(navNone.worn()));
  const navOut = makeEnvNav({ authed: false, homeResp: HOME_FAV });
  await new Promise(r => setImmediate(r));
  check("signed out asks the server for nothing", !navOut.posts.length);
  check("signed out shows the shield", navOut.worn().every(b => !b.src && b.shield));

  // The Clans panel is empty markup that this module fills in, so every way it
  // can decline to render has to SAY something. A silent bail-out is what the
  // player experiences as "I click Clans and there is nothing there".
  console.log("never a blank Clans tab:");
  const noBridge = makeEnvDom({ hasBridge: false });
  check("a missing bridge still registers the renderer",
        typeof noBridge.windowStub.__ccClansRender === "function");
  await noBridge.windowStub.__ccClansRender();
  check("a missing bridge explains itself instead of painting nothing",
        /refresh/i.test(noBridge.rootEl.innerHTML));

  const signedOut = makeEnvDom({ authed: false });
  await signedOut.windowStub.__ccClansRender();
  check("signed out says to sign in instead of painting nothing",
        /sign in/i.test(signedOut.rootEl.innerHTML));

  // Signed in, the guards must fall through to the real thing. (What that then
  // paints is covered against real markup by test_clans_render.js.)
  const signedIn = makeEnvDom({ authed: true });
  await signedIn.windowStub.__ccClansRender();
  check("signed in goes and fetches the clan home",
        signedIn.posts.some(p => p === "/api/clan/home"));
  check("signed in shows no bail-out message",
        !/refresh the page|Sign in to join/i.test(signedIn.rootEl.innerHTML));

  // The one failure that actually produces a BLANK tab: every render clears the
  // root first and attaches the finished card last, and the render functions are
  // async — so a throw halfway through lands as an unhandled rejection and the
  // panel just stays empty. A home payload with no season makes renderHome throw.
  const crash = makeEnvDom({ authed: true, homeResp: { ok: true } });
  await crash.windowStub.__ccClansRender();
  await new Promise(r => setImmediate(r));
  await new Promise(r => setImmediate(r));
  check("a crash mid-render is reported, not left blank",
        /couldn.{0,3}t be drawn/i.test(crash.rootEl.innerHTML));
  check("the crash message names the screen",
        /home screen/i.test(crash.rootEl.innerHTML));

  console.log(`\n${"=".repeat(40)}\nRESULT: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
