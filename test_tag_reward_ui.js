/* Plush-tag QR reward: client module test (node vm + a tiny browser stub).
 *
 * Loads the REAL js/tag-reward.js against a stub __ccTagReward bridge and pins:
 *   • ?tag=<token> is saved on the device and wiped from the address bar
 *   • signed out: nothing is sent, the token waits, the player is told to sign in
 *   • signing in later ("cc:auth") claims by itself, exactly once
 *   • a grant drops the token, says so, and re-reads the account
 *   • final answers (already claimed / owned / bad link) drop the token
 *   • retryable answers (no profile yet, offline) keep it and try again
 *   • the module never sends a uid or an avatar, only the token + idToken
 *
 * Run:  node test_tag_reward_ui.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = fs.readFileSync(path.join(__dirname, "multiplayer/client/js/tag-reward.js"), "utf8");
const TOKEN = "TestTagToken_0123456789";

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

function makeEnv(opts) {
  const o = opts || {};
  const store = new Map(Object.entries(o.storage || {}));
  const posts = [], toasts = [], granted = [], replaced = [];
  const listeners = {};
  const timers = [];
  let user = o.user || null;
  const replies = (o.replies || []).slice();

  const location = { href: o.href || "https://play.currentsandcritters.com/", search: "" };
  location.search = new URL(location.href).search;

  const win = {
    location,
    history: {
      state: null,
      replaceState(_s, _t, url) {
        replaced.push(url);
        const u = new URL(url, "https://play.currentsandcritters.com");
        location.href = u.href; location.search = u.search;
      },
    },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    dispatch(type) { (listeners[type] || []).forEach((fn) => fn({})); },
    __ccTagReward: {
      post: async (p, body) => {
        posts.push({ p, body });
        const r = replies.length ? replies.shift() : { ok: true, icon: "/avatars/fish.png" };
        if (r === "throw") throw new Error("offline");
        return { ok: true, status: 200, data: r };
      },
      toast: (m, k) => toasts.push({ m, k }),
      authUser: () => user,
      idToken: async () => (user ? "id-" + user.uid : ""),
      onGranted: async (res) => { granted.push(res); },
    },
  };
  const ctx = {
    window: win,
    document: { readyState: "complete", addEventListener() {} },
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    URL, URLSearchParams,
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout() {},
    console,
  };
  ctx.window.window = ctx.window;
  vm.createContext(ctx);
  // The module reads window.location / window.history; expose the same objects
  // as globals the way a browser does.
  ctx.location = location; ctx.history = win.history;
  ctx.addEventListener = win.addEventListener;
  vm.runInContext(SRC, ctx);
  return {
    posts, toasts, granted, replaced, timers, store, win,
    signIn(uid) { user = { uid }; win.dispatch("cc:auth"); },
    runTimers(filterMs) {
      const due = timers.splice(0).filter((t) => (filterMs ? t.ms === filterMs : true));
      due.forEach((t) => t.fn());
    },
  };
}

(async () => {
  console.log("tag-reward.js");

  // ── signed in, straight from the QR ──────────────────────────────────────
  {
    const e = makeEnv({ href: `https://play.currentsandcritters.com/?tag=${TOKEN}&x=1#h`, user: { uid: "alice" } });
    await flush();
    check("token wiped from the address bar, other params kept",
          e.replaced.length === 1 && e.replaced[0] === "/?x=1#h", JSON.stringify(e.replaced));
    check("claims once with token + idToken only",
          e.posts.length === 1 && e.posts[0].p === "/api/tag-reward/claim"
          && JSON.stringify(Object.keys(e.posts[0].body).sort()) === JSON.stringify(["idToken", "token"])
          && e.posts[0].body.token === TOKEN && e.posts[0].body.idToken === "id-alice",
          JSON.stringify(e.posts));
    check("grant: re-reads the account", e.granted.length === 1);
    check("grant: says so", e.toasts.some((t) => t.k === "ok" && /exclusive skin/i.test(t.m)));
    check("grant: token dropped", !e.store.has("cc_tag_reward_token"));
    e.win.dispatch("cc:auth");
    await flush();
    check("no second claim after the token is spent", e.posts.length === 1);
  }

  // ── signed out: wait, nudge, then claim on sign-in ───────────────────────
  {
    const e = makeEnv({ href: `https://play.currentsandcritters.com/?tag=${TOKEN}` });
    await flush();
    check("signed out: nothing sent", e.posts.length === 0);
    check("signed out: token kept on the device", e.store.get("cc_tag_reward_token") === TOKEN);
    e.runTimers(7000);
    check("signed out: told to sign in", e.toasts.some((t) => /sign in/i.test(t.m)));
    e.signIn("bob");
    await flush();
    check("sign-in claims by itself", e.posts.length === 1 && e.posts[0].body.idToken === "id-bob");
    check("…and the token is spent", !e.store.has("cc_tag_reward_token"));
  }

  // ── a later visit with the token already saved ───────────────────────────
  {
    const e = makeEnv({ storage: { cc_tag_reward_token: TOKEN }, user: { uid: "cara" } });
    await flush();
    check("saved token is claimed on a later visit", e.posts.length === 1);
    check("no URL rewrite when there is no ?tag=", e.replaced.length === 0);
  }

  // ── final answers drop the token ─────────────────────────────────────────
  for (const err of ["already_claimed", "already_owned", "bad_token"]) {
    const e = makeEnv({ storage: { cc_tag_reward_token: TOKEN }, user: { uid: "d" },
                        replies: [{ ok: false, error: err }] });
    await flush();
    check(`${err}: token dropped, nothing granted`,
          !e.store.has("cc_tag_reward_token") && e.granted.length === 0);
    check(`${err}: the player is told`, e.toasts.length === 1 && e.toasts[0].m.length > 0);
  }

  // ── retryable answers keep it and try again ──────────────────────────────
  for (const reply of [{ ok: false, error: "no_account" }, "throw", { ok: false, error: "server_error" }]) {
    const label = typeof reply === "string" ? "offline" : reply.error;
    const e = makeEnv({ storage: { cc_tag_reward_token: TOKEN }, user: { uid: "e" },
                        replies: [reply, { ok: true }] });
    await flush();
    check(`${label}: token kept`, e.store.get("cc_tag_reward_token") === TOKEN);
    check(`${label}: a retry is scheduled`, e.timers.some((t) => t.ms === 3000));
    e.runTimers(3000);
    await flush();
    check(`${label}: the retry claims`, e.posts.length === 2 && !e.store.has("cc_tag_reward_token"));
  }

  // ── a malformed ?tag= is wiped but never stored or sent ──────────────────
  {
    const e = makeEnv({ href: "https://play.currentsandcritters.com/?tag=%3Cscript%3E", user: { uid: "f" } });
    await flush();
    check("malformed tag: wiped from the URL", e.replaced.length === 1);
    check("malformed tag: not stored, not sent", !e.store.has("cc_tag_reward_token") && e.posts.length === 0);
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
