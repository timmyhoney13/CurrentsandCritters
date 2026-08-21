/* Discord join reward — client module test (node vm + a small DOM stub).
 *
 * Loads the REAL js/discord-reward.js against a stub __ccDiscord bridge and
 * pins the rules where a silent failure would be invisible until a player hit
 * it — or, worse, would advertise coins that don't exist:
 *
 *   • the chip is HIDDEN unless the server says the offer is on, including when
 *     the /state request fails outright (never advertise on a failure)
 *   • it says "+250 Critter Coins" before, and "claimed" after — and once
 *     claimed it is not a button any more
 *   • the Discord window is opened SYNCHRONOUSLY inside the click, before the
 *     network call, or every pop-up blocker kills the flow
 *   • the result is only accepted from OUR origin and only when it is our
 *     message — a page in another tab cannot hand the game a fake "ok"
 *   • closing the window without finishing puts the chip back, it never sticks
 *     on "Checking with Discord…"
 *   • "you're not in the server" offers the invite instead of just complaining
 *   • pop-ups blocked falls back to this tab, and the ?discord= return flag is
 *     read and then wiped from the URL
 *   • the module never sends an amount anywhere — it cannot pay anybody
 *
 * Run:  node test_discord_reward_ui.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = fs.readFileSync(
  path.join(__dirname, "multiplayer/client/js/discord-reward.js"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}
const eq = (name, got, want) =>
  check(name, JSON.stringify(got) === JSON.stringify(want),
        `got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);

const flush = async (n = 6) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

// ── DOM stub: one button, one label, nothing else the module touches ───────
function makeNode(tag, id) {
  return {
    tagName: String(tag || "div").toUpperCase(),
    id: id || "",
    hidden: false,
    disabled: false,
    title: "",
    textContent: "",
    dataset: {},
    attrs: {},
    _listeners: {},
    classList: {
      _s: new Set(),
      add(...c) { c.forEach((x) => x && this._s.add(x)); },
      remove(...c) { c.forEach((x) => this._s.delete(x)); },
      toggle(c, on) { if (on) this._s.add(c); else this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
    click() { (this._listeners.click || []).forEach((fn) => fn({})); },
  };
}

function makeEnv(opts) {
  const o = opts || {};
  const posts = [];
  const toasts = [];
  const modals = [];
  const claimed = [];
  const opened = [];        // window.open calls, in order
  const navigations = [];   // same-tab location changes
  let messageListener = null;
  const timers = new Map();
  let timerSeq = 1;

  const chip = makeNode("button", "ph-discord-reward");
  const txt = makeNode("span", "ph-discord-reward-txt");

  // A fake pop-up. `blocked:true` makes window.open return null, exactly as a
  // blocker does.
  function makePopup() {
    return {
      closed: false,
      location: {
        _href: "",
        replace(u) { this._href = String(u); },
        set href(u) { this._href = String(u); },
        get href() { return this._href; },
      },
      document: { write() {}, close() {} },
      close() { this.closed = true; },
    };
  }

  const bridge = {
    APP_BUILD: "test",
    get: async () => ({ ok: true, status: 200, data: { ok: true } }),
    // The REAL bridge resolves to an ENVELOPE and THROWS when the request never
    // lands. The stub must do both or it tests a contract that doesn't exist.
    post: async (p, b) => {
      posts.push({ p, b });
      // A request that takes real time on the wire. The interesting Discord
      // bugs are all races between one sync and the next, and they are
      // invisible when every reply lands in the same microtask.
      for (let i = 0; i < (o.postDelay || 0); i++) await new Promise((r) => setImmediate(r));
      let r;
      if (typeof o.postResp === "function") r = o.postResp(p, b);
      else if (p.endsWith("/state")) r = o.state || { ok: true, enabled: true, coins: 250, claimed: false, signedIn: true, inviteUrl: "https://discord.gg/test" };
      else r = { ok: true, url: "https://discord.com/oauth2/authorize?x=1", coins: 250 };
      if (r === null) throw new Error("network down");
      return o.bare ? r : { ok: true, status: 200, data: r };
    },
    toast: (m, t) => toasts.push({ m: String(m), t }),
    // Read LIVE off `o`, so a test can sign in (or break the token) partway
    // through, the way Firebase really does.
    authUser: () => (o.signedOut ? null : { uid: o.uid || "u1", getIdToken: async () => "tok" }),
    idToken: async () => (o.signedOut || o.tokenEmpty ? "" : "tok"),
    modal: async (op) => { modals.push(op); return o.modalChoice || "later"; },
    onClaimed: (r) => claimed.push(r),
  };

  const doc = {
    readyState: "complete",
    getElementById: (id) => (id === "ph-discord-reward" ? chip
                           : id === "ph-discord-reward-txt" ? txt : null),
    addEventListener() {},
  };

  const win = {
    location: {
      origin: "https://play.currentsandcritters.com",
      href: "https://play.currentsandcritters.com/" + (o.search || ""),
      pathname: "/",
      search: o.search || "",
      hash: "",
      set href_(v) {},
    },
    history: { replaceState: (_s, _t, url) => { navigations.push("replaceState:" + url); } },
    history_: null,
    open: (url, name, feats) => {
      opened.push({ url, name, feats });
      if (o.popupBlocked) return null;
      const p = makePopup();
      if (url) p.location._href = url;
      opened[opened.length - 1].win = p;
      return p;
    },
    addEventListener: (type, fn) => { if (type === "message") messageListener = fn; },
    removeEventListener: (type) => { if (type === "message") messageListener = null; },
    setTimeout: (fn, ms) => { const id = timerSeq++; timers.set(id, { fn, ms, kind: "timeout" }); return id; },
    setInterval: (fn, ms) => { const id = timerSeq++; timers.set(id, { fn, ms, kind: "interval" }); return id; },
    clearTimeout: (id) => timers.delete(id),
    clearInterval: (id) => timers.delete(id),
    __ccDiscord: bridge,
  };
  // The module assigns location.href to navigate in the pop-up-blocked path.
  Object.defineProperty(win.location, "href", {
    get() { return this._href || "https://play.currentsandcritters.com/" + (o.search || ""); },
    set(v) { this._href = String(v); navigations.push(String(v)); },
    configurable: true,
  });

  const ctx = {
    window: win, document: doc, console,
    URL, URLSearchParams, Date, JSON, Math, Object, Array, String, Number,
    Promise, Error, setImmediate,
    setTimeout: win.setTimeout, setInterval: win.setInterval,
    clearTimeout: win.clearTimeout, clearInterval: win.clearInterval,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(SRC, ctx);

  return {
    ctx, win, chip, txt, posts, toasts, modals, claimed, opened, navigations,
    fireMessage: (data, origin) => {
      if (!messageListener) return false;
      messageListener({ origin: origin || win.location.origin, data });
      return true;
    },
    hasMessageListener: () => !!messageListener,
    tickIntervals: () => { [...timers.values()].filter((t) => t.kind === "interval").forEach((t) => t.fn()); },
    // Fire every pending setTimeout once, soonest first, the way the clock
    // would. Used to run the chip's back-off retries without really waiting.
    runTimeouts: () => {
      [...timers.entries()]
        .filter(([, t]) => t.kind === "timeout")
        .sort((a, b) => a[1].ms - b[1].ms)
        .forEach(([id, t]) => { timers.delete(id); t.fn(); });
    },
    pendingTimers: () => timers.size,
    lastPopup: () => (opened.length ? opened[opened.length - 1].win : null),
  };
}

(async function run() {
  console.log("\nDiscord reward chip — render rules");
  {
    const e = makeEnv({});
    await flush();
    check("chip is shown when the offer is on", e.chip.hidden === false);
    eq("chip advertises the amount the server sent", e.txt.textContent, "+250 Critter Coins");
    check("chip is clickable", e.chip.disabled === false);
    check("chip has an accessible label",
          /Claim 250 Critter Coins/.test(e.chip.getAttribute("aria-label") || ""));
  }
  {
    const e = makeEnv({ state: { ok: true, enabled: false, coins: 250, claimed: false } });
    await flush();
    check("offer switched off ⇒ chip stays hidden", e.chip.hidden === true);
  }
  {
    // A server that never answers must not leave the "+250" placeholder showing.
    const e = makeEnv({ postResp: () => null });
    await flush();
    check("a failed /state request never advertises coins", e.chip.hidden === true);
  }
  {
    const e = makeEnv({ postResp: (p) => (p.endsWith("/state") ? { ok: false, error: "server_error" } : { ok: true }) });
    await flush();
    check("an error reply never advertises coins", e.chip.hidden === true);
  }
  {
    const e = makeEnv({ state: { ok: true, enabled: true, coins: 250, claimed: true,
                                 coinsAwarded: 250, claimedAt: "2026-08-15T00:00:00Z", signedIn: true } });
    await flush();
    check("claimed ⇒ chip is shown", e.chip.hidden === false);
    check("claimed ⇒ says so", /claimed/.test(e.txt.textContent));
    check("claimed ⇒ wears the claimed style", e.chip.classList.contains("is-claimed"));
    check("claimed ⇒ is no longer a button", e.chip.disabled === true);
  }
  {
    const e = makeEnv({ state: { ok: true, enabled: true, coins: 500, claimed: false, signedIn: true } });
    await flush();
    eq("the amount comes from the SERVER, not the markup", e.txt.textContent, "+500 Critter Coins");
  }
  {
    const e = makeEnv({});
    await flush();
    const before = e.posts.length;
    e.ctx.window.__ccDiscordSync();
    await flush();
    eq("a repeat sync for the same account does not re-ask the server",
       e.posts.length, before);
  }

  console.log("\nThe claim");
  {
    const e = makeEnv({});
    await flush();
    const p = e.ctx.window.__ccDiscordClaim();

    // The pop-up has to exist BEFORE the await, or a blocker eats it.
    eq("the Discord window is opened synchronously, before any request",
       e.opened.length, 1);
    eq("…and it is opened blank, then pointed at Discord",
       e.opened[0].url, "");
    check("chip goes busy while Discord is open", e.chip.classList.contains("is-busy"));

    await flush();
    eq("the start request is the only thing sent", e.posts.map((x) => x.p),
       ["/api/discord/state", "/api/discord/start"]);
    eq("the start request proves who is asking", Object.keys(e.posts[1].b), ["idToken"]);
    check("the window was pointed at the URL the server returned",
          e.lastPopup().location.href === "https://discord.com/oauth2/authorize?x=1");

    e.fireMessage({ source: "cc-discord", ok: true, coins: 250, total: 300 });
    await p;
    check("a success toast names the amount", /250/.test((e.toasts.pop() || {}).m || ""));
    eq("the account is re-read so the coin balance repaints", e.claimed.length, 1);
    check("chip flips to claimed", e.chip.classList.contains("is-claimed"));
    check("chip is no longer busy", !e.chip.classList.contains("is-busy"));
    eq("no timers are left running", e.pendingTimers(), 0);
    check("the message listener is removed", !e.hasMessageListener());
  }
  {
    const e = makeEnv({});
    await flush();
    const p = e.ctx.window.__ccDiscordClaim();
    await flush();
    // Another tab shouting at us must be ignored, twice over.
    e.fireMessage({ source: "cc-discord", ok: true, coins: 99999 }, "https://evil.example");
    e.fireMessage({ source: "not-us", ok: true, coins: 99999 });
    check("a foreign origin cannot hand the game a claim", e.claimed.length === 0);
    check("…and neither can a message that isn't ours", e.toasts.length === 0);
    e.fireMessage({ source: "cc-discord", ok: true, coins: 250 });
    await p;
    eq("our own message on our own origin still works", e.claimed.length, 1);
  }
  {
    const e = makeEnv({});
    await flush();
    const p = e.ctx.window.__ccDiscordClaim();
    await flush();
    e.lastPopup().closed = true;
    e.tickIntervals();
    await p;
    check("closing the window un-sticks the chip", !e.chip.classList.contains("is-busy"));
    check("…and it is claimable again", e.chip.disabled === false);
    eq("…and nothing was claimed", e.claimed.length, 0);
    eq("no timers are left running", e.pendingTimers(), 0);
  }
  {
    const e = makeEnv({});
    await flush();
    const first = e.ctx.window.__ccDiscordClaim();
    e.ctx.window.__ccDiscordClaim();          // double-tap
    await flush();
    eq("a double-tap opens one Discord window, not two", e.opened.length, 1);
    e.fireMessage({ source: "cc-discord", ok: true, coins: 250 });
    await first;
  }
  {
    const e = makeEnv({ signedOut: true, state: { ok: true, enabled: true, coins: 250, claimed: false, signedIn: false } });
    await flush();
    await e.ctx.window.__ccDiscordClaim();
    eq("signed out ⇒ no Discord window", e.opened.length, 0);
    check("signed out ⇒ told to sign in", /Sign in/i.test((e.toasts.pop() || {}).m || ""));
  }
  {
    const e = makeEnv({ state: { ok: true, enabled: true, coins: 250, claimed: true, signedIn: true } });
    await flush();
    await e.ctx.window.__ccDiscordClaim();
    eq("already claimed ⇒ no second Discord window", e.opened.length, 0);
    check("already claimed ⇒ says so", /already/i.test((e.toasts.pop() || {}).m || ""));
  }

  console.log("\nWhen it doesn't work");
  {
    const e = makeEnv({ modalChoice: "join" });
    await flush();
    const p = e.ctx.window.__ccDiscordClaim();
    await flush();
    e.fireMessage({ source: "cc-discord", ok: false, error: "not_a_member" });
    await p;
    eq("not a member ⇒ a modal, not just a toast", e.modals.length, 1);
    check("…that offers the invite", (e.modals[0].actions || []).some((a) => a.key === "join"));
    check("…and opens it when taken",
          e.opened.some((o) => String(o.url).includes("discord.gg")));
    check("chip is claimable again", e.chip.disabled === false);
  }
  {
    const e = makeEnv({});
    await flush();
    const p = e.ctx.window.__ccDiscordClaim();
    await flush();
    e.fireMessage({ source: "cc-discord", ok: false, error: "discord_unreachable" });
    await p;
    check("a Discord outage says nothing was claimed",
          /Nothing was claimed/i.test((e.toasts.pop() || {}).m || ""));
    eq("…and nothing was", e.claimed.length, 0);
  }
  {
    const e = makeEnv({ postResp: (p) => (p.endsWith("/state")
      ? { ok: true, enabled: true, coins: 250, claimed: false, signedIn: true }
      : { ok: false, error: "already_claimed" }) });
    await flush();
    await e.ctx.window.__ccDiscordClaim();
    await flush();
    check("a stale chip corrects itself when the server says already claimed",
          e.posts.filter((x) => x.p.endsWith("/state")).length >= 2);
    check("…and the Discord window is closed again", e.lastPopup().closed === true);
  }
  {
    const e = makeEnv({ popupBlocked: true });
    await flush();
    await e.ctx.window.__ccDiscordClaim();
    await flush();
    check("pop-ups blocked ⇒ the player is told",
          e.toasts.some((t) => /pop-?ups/i.test(t.m)));
    check("…and the flow continues in this tab",
          e.navigations.some((n) => n.includes("discord.com/oauth2/authorize")));
  }

  console.log("\nComing back through this tab (pop-ups blocked)");
  {
    const e = makeEnv({ search: "?discord=ok" });
    await flush();
    check("a successful return is announced", e.toasts.some((t) => /Critter Coins/.test(t.m)));
    eq("…and the account is re-read", e.claimed.length, 1);
    check("…and the flag is wiped from the URL",
          e.navigations.some((n) => n.startsWith("replaceState:") && !n.includes("discord=")));
  }
  {
    const e = makeEnv({ search: "?discord=not_a_member", modalChoice: "later" });
    await flush();
    eq("a failed return explains itself", e.modals.length, 1);
    eq("…and claims nothing", e.claimed.length, 0);
  }
  {
    const e = makeEnv({ search: "" });
    await flush();
    eq("no flag ⇒ no toast, no URL rewrite", e.navigations.length, 0);
  }

  // ════════════════════════════════════════════════════════════════════
  //  The one thing this chip must never do
  // ════════════════════════════════════════════════════════════════════
  // Reported from the live game: "I did it with my account, then it said you
  // could redeem it again." The coins were never at risk — the server refuses
  // a second payout and always did — but a chip that re-offers a reward you
  // already collected reads as a bug in the reward, and the only way to find
  // out is to click it and be told no.
  //
  // Every case here is the same mistake wearing a different hat: a /state reply
  // that is NOT about the signed-in account being filed as if it were.
  console.log("\nA reward already collected is never offered again");
  {
    // Firebase resolves AFTER boot, which is the normal order of events: the
    // boot sync asks signed-out, sign-in fires the real one while the first is
    // still on the wire. The old de-dupe handed back the signed-out request
    // instead of making the real one, so the only answer the chip ever had was
    // "nobody has claimed" — and it offered a paid reward for the whole session.
    const o = {
      signedOut: true,
      postDelay: 4,
      postResp: (p, b) => (p.endsWith("/state")
        ? { ok: true, enabled: true, coins: 250, signedIn: !!(b && b.idToken),
            claimed: !!(b && b.idToken), coinsAwarded: b && b.idToken ? 250 : 0 }
        : { ok: true, url: "https://discord.com/oauth2/authorize?x=1", coins: 250 }),
    };
    const e = makeEnv(o);
    await new Promise((r) => setImmediate(r));   // boot's signed-out sync is in flight
    o.signedOut = false;                          // …and Firebase resolves right now
    e.ctx.window.__ccDiscordSync();               // syncStatsHeader, on sign-in
    await flush(40);

    eq("signing in mid-request asks again, as the account", e.posts.length, 2);
    check("…with a token the second time", !!(e.posts[1] && e.posts[1].b && e.posts[1].b.idToken));
    check("a claimed account is told so, not re-offered",
          /claimed/.test(e.txt.textContent), e.txt.textContent);
    check("…and the chip is not a button", e.chip.disabled === true);
  }
  {
    // Same shape, reversed: a signed-out reply must not be allowed to land on
    // top of the signed-in one just because it was slower.
    const o = {
      signedOut: true,
      postDelay: 8,
      postResp: (p, b) => ({ ok: true, enabled: true, coins: 250,
        signedIn: !!(b && b.idToken), claimed: !!(b && b.idToken),
        coinsAwarded: b && b.idToken ? 250 : 0 }),
    };
    const e = makeEnv(o);
    await new Promise((r) => setImmediate(r));
    o.signedOut = false;
    o.postDelay = 1;                              // the newer request answers FIRST
    e.ctx.window.__ccDiscordSync();
    await flush(60);
    check("a slower stale reply cannot overwrite a newer one",
          /claimed/.test(e.txt.textContent), e.txt.textContent);
  }
  {
    // The token came back empty for a signed-in player, so the request went out
    // untokenised and the server answered about nobody. Filing that against the
    // uid pinned "unclaimed" for the rest of the page load, because every later
    // repaint short-circuits on "this account already has an answer".
    const o = {
      tokenEmpty: true,
      postResp: (p, b) => ({ ok: true, enabled: true, coins: 250,
        signedIn: !!(b && b.idToken), claimed: !!(b && b.idToken),
        coinsAwarded: b && b.idToken ? 250 : 0 }),
    };
    const e = makeEnv(o);
    await flush();
    check("an answer about nobody is not advertised as an offer",
          !/\+250/.test(e.txt.textContent), e.txt.textContent);
    check("…and the chip cannot be clicked on it", e.chip.disabled === true);

    o.tokenEmpty = false;                          // the token starts working
    e.ctx.window.__ccDiscordSync();
    await flush(20);
    check("…and the next repaint asks again rather than trusting it",
          e.posts.length >= 2);
    check("…and the truth wins", /claimed/.test(e.txt.textContent), e.txt.textContent);
  }
  {
    // The other half of the same rule: never trap a player who IS owed the
    // reward behind a chip that can't be clicked. When the account can't be
    // read at all, the chip gives up quietly and opens back up — the server is
    // the guard, and a refusal you can read beats a button that never works.
    const o = {
      tokenEmpty: true,
      postResp: (p, b) => ({ ok: true, enabled: true, coins: 250,
        signedIn: !!(b && b.idToken), claimed: false }),
    };
    const e = makeEnv(o);
    await flush();
    const start = e.posts.length;
    // Run the retries the module scheduled, in order, without waiting on them.
    for (let i = 0; i < 6 && e.pendingTimers(); i++) { e.runTimeouts(); await flush(10); }
    check("an unreadable account is retried, not given up on at once",
          e.posts.length > start, `posts ${start} → ${e.posts.length}`);
    check("…and it stops retrying rather than hammering the server",
          e.posts.length <= start + 4, `posts ${start} → ${e.posts.length}`);
    check("…and the chip ends up clickable, never a dead button",
          e.chip.disabled === false);
    eq("…and nothing is left ticking", e.pendingTimers(), 0);
  }

  console.log("\nThe module cannot pay anybody");
  {
    const e = makeEnv({});
    await flush();
    const p = e.ctx.window.__ccDiscordClaim();
    await flush();
    e.fireMessage({ source: "cc-discord", ok: true, coins: 250 });
    await p;
    const bodies = JSON.stringify(e.posts.map((x) => x.b));
    check("no request ever carries an amount", !/coins|amount|critter_coins/i.test(bodies), bodies);
    eq("only two endpoints are ever called",
       [...new Set(e.posts.map((x) => x.p))].sort(),
       ["/api/discord/start", "/api/discord/state"]);
    // The coin balance lives in Firestore and the browser is allowed to write
    // parts of the user doc, so the one thing this module must never learn to
    // do is write its own reward.
    check("the source has no Firestore write in it",
          !/critter_coins|firebase|db\.collection|runTransaction|\.doc\(/.test(SRC));
  }
  {
    // A bridge that hands back the BARE payload instead of the envelope (the
    // shape a careless stub or an older preview-app would give) must still work.
    const e = makeEnv({ bare: true });
    await flush();
    eq("a bare (un-enveloped) reply still renders", e.txt.textContent, "+250 Critter Coins");
  }
  {
    // No bridge at all: preview-app.js never got as far as defining one.
    const ctx = {
      window: { location: { origin: "https://x", href: "https://x/", pathname: "/", search: "", hash: "" },
                history: { replaceState() {} }, addEventListener() {}, removeEventListener() {},
                open: () => null, setTimeout, setInterval, clearTimeout, clearInterval },
      document: { readyState: "complete", getElementById: () => null, addEventListener() {} },
      console, URL, URLSearchParams, Date, JSON, Math, Object, Array, String, Number, Promise, Error,
      setTimeout, setInterval, clearTimeout, clearInterval,
    };
    ctx.globalThis = ctx;
    let threw = null;
    try { vm.createContext(ctx); vm.runInContext(SRC, ctx); await flush(); }
    catch (err) { threw = err; }
    check("a missing bridge is survivable, not a thrown page", threw === null,
          threw && threw.message);
    check("…and the hooks are still registered",
          typeof ctx.window.__ccDiscordSync === "function"
          && typeof ctx.window.__ccDiscordClaim === "function");
  }

  console.log(`\n${pass} passed, ${fail} failed\n`);
  process.exit(fail ? 1 : 0);
})();
