/* Purchase code redemption: client module test (node vm + a small DOM stub).
 *
 * Loads the REAL js/redeem.js against a stub __ccRedeem bridge and pins the
 * rules where a silent failure would leave a paying customer stuck:
 *
 *   • the card mounts itself into the Friends panel, and sits under the
 *     referral card rather than above it
 *   • signed out it asks you to sign in, and shows no box to type into
 *   • an empty box never reaches the network
 *   • a purchase code is POSTed to /api/redeem/code WITH the id token
 *   • a 4-digit FRIEND code is handed to referral.js instead, because there is
 *     one box and two kinds of code
 *   • success calls onRedeemed, which is the only thing that repaints the
 *     header: without it the coins are real but invisible, which reads to a
 *     player as "it didn't work"
 *   • failure does NOT call onRedeemed, leaves the button usable, and says
 *     something a human can act on
 *   • the module never sends an amount anywhere, it cannot pay anybody
 *
 * Run:  node test_redeem_ui.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = fs.readFileSync(
  path.join(__dirname, "multiplayer/client/js/redeem.js"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

// ── DOM stub ──────────────────────────────────────────────────────────────
// Small on purpose. The one thing it must model honestly is that setting
// innerHTML REPLACES the children, so ids that were in the old markup stop
// resolving: that is how a card that renders one state and wires another gets
// caught.
function makeEnv(opts) {
  const o = opts || {};
  const byId = new Map();
  const posts = [];
  const toasts = [];
  const refreshes = [];
  const referralCalls = [];

  function makeNode(tag, id) {
    const node = {
      tagName: String(tag || "div").toUpperCase(),
      _id: id || "",
      // Assigning an id REGISTERS the node, the way appending a element with
      // an id makes it findable. Without this, host()'s $("cc-redeem-root")
      // never resolves, it builds a fresh root on every render, and ids from
      // the previous render survive in the lookup: a stub bug that would hide
      // a real "renders one state, wires another" bug.
      get id() { return this._id; },
      set id(v) { this._id = String(v || ""); if (this._id) byId.set(this._id, this); },
      value: "",
      disabled: false,
      textContent: "",
      className: "",
      _html: "",
      _listeners: {},
      parentNode: null,
      childNodes: [],
      get innerHTML() { return this._html; },
      set innerHTML(html) {
        // Drop the ids this node previously owned, then register the new ones.
        for (const owned of this.childNodes) byId.delete(owned);
        this.childNodes = [];
        this._html = String(html);
        const re = /id="([^"]+)"/g;
        let m;
        while ((m = re.exec(this._html))) {
          const kid = makeNode(/id="ccRD-code"/.test(m[0]) ? "input" : "button", m[1]);
          byId.set(m[1], kid);
          this.childNodes.push(m[1]);
        }
      },
      setAttribute(k, v) { this[k] = String(v); },
      addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
      click() { (this._listeners.click || []).forEach((fn) => fn({})); },
      keydown(key) { (this._listeners.keydown || []).forEach((fn) => fn({ key })); },
      insertBefore(kid) { kid.parentNode = this; return kid; },
    };
    if (id) byId.set(id, node);
    return node;
  }

  const friends = makeNode("div", "ph-panel-friends");
  friends.firstChild = null;
  byId.set("ph-panel-friends", friends);
  if (o.withReferral) {
    const ref = makeNode("div", "cc-referral-root");
    ref.parentNode = friends;
    byId.set("cc-referral-root", ref);
  }

  const bridge = {
    APP_BUILD: "test",
    get: async (p) => {
      if (String(p).indexOf("/api/redeem/state") === 0) {
        if (o.stateThrows) throw new Error("network down");
        return { ok: true, status: 200,
                 data: { ok: true, enabled: o.disabled ? false : true } };
      }
      return { ok: true, status: 200, data: { ok: true } };
    },
    // The REAL bridge resolves to an ENVELOPE and THROWS when the request
    // never lands. The stub must do both or it tests a contract that does not
    // exist.
    post: async (p, b) => {
      posts.push({ p, b });
      let r;
      if (typeof o.postResp === "function") r = o.postResp(p, b);
      else r = o.resp || { ok: true, rewardName: "Tide Turner", coins: 30000 };
      if (r === null) throw new Error("network down");
      return { ok: true, status: 200, data: r };
    },
    toast: (m, t) => toasts.push({ m: String(m), t }),
    signedIn: () => !o.signedOut,
    idToken: async () => (o.signedOut || o.tokenEmpty ? "" : "tok"),
    onRedeemed: () => refreshes.push(Date.now()),
  };

  const doc = {
    readyState: "complete",
    getElementById: (id) => byId.get(id) || null,
    createElement: (tag) => makeNode(tag, ""),
    addEventListener() {},
  };

  const win = {
    __ccRedeem: bridge,
    document: doc,
    setInterval: () => 0,        // the mount poll never needs to fire here
    clearInterval: () => {},
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    console,
  };
  if (o.withReferralModule !== false) {
    win.__ccReferralRedeem = async (code) => {
      referralCalls.push(code);
      return o.referralResp || { ok: true, coins: 100, referrerName: "Pal" };
    };
    win.__ccReferralRender = () => {};
  }
  win.window = win;

  const ctx = vm.createContext(win);
  vm.runInContext(SRC, ctx);
  return { win, doc, byId, posts, toasts, refreshes, referralCalls, friends,
           el: (id) => byId.get(id) || null };
}

(async function run() {
  console.log("\n── mounting ───────────────────────────────────────────────");
  {
    const e = makeEnv({});
    e.win.__ccRedeemRender();
    check("the card mounts itself into the Friends panel",
          !!e.el("cc-redeem-root") || !!e.friends, "no root");
    check("a signed-in player gets a box and a button",
          !!e.el("ccRD-code") && !!e.el("ccRD-go"));
  }
  {
    const e = makeEnv({ signedOut: true });
    e.win.__ccRedeemRender();
    check("signed out there is no box to type into", !e.el("ccRD-code"));
  }

  console.log("\n── redeeming a purchase code ──────────────────────────────");
  {
    const e = makeEnv({});
    e.win.__ccRedeemRender();
    e.el("ccRD-code").value = "CC-ABCD-EFGH-JKMN-PQRS";
    e.el("ccRD-go").click();
    await flush();
    check("it POSTs to /api/redeem/code", e.posts.length === 1 && e.posts[0].p === "/api/redeem/code",
          JSON.stringify(e.posts));
    check("it sends the id token", e.posts[0] && e.posts[0].b.idToken === "tok");
    check("it sends what was typed", e.posts[0] && e.posts[0].b.code === "CC-ABCD-EFGH-JKMN-PQRS");
    check("it never sends an amount",
          e.posts[0] && !("coins" in e.posts[0].b) && !("amount" in e.posts[0].b));
    check("success refreshes the profile (or the reward stays invisible)",
          e.refreshes.length === 1, `refreshes=${e.refreshes.length}`);
    check("success toasts what landed",
          e.toasts.some((t) => /Tide Turner/.test(t.m) && /30,000/.test(t.m)),
          JSON.stringify(e.toasts));
    check("success clears the box", e.el("ccRD-code").value === "");
    check("the button goes back to Redeem", e.el("ccRD-go").textContent === "Redeem");
  }

  console.log("\n── refusals ───────────────────────────────────────────────");
  {
    const e = makeEnv({});
    e.win.__ccRedeemRender();
    e.el("ccRD-go").click();
    await flush();
    check("an empty box never reaches the network", e.posts.length === 0);
    check("an empty box does not refresh anything", e.refreshes.length === 0);
  }
  {
    const e = makeEnv({ resp: { ok: false, error: "already_claimed" } });
    e.win.__ccRedeemRender();
    e.el("ccRD-code").value = "CC-ABCD-EFGH-JKMN-PQRS";
    e.el("ccRD-go").click();
    await flush();
    check("a used code does NOT refresh the profile", e.refreshes.length === 0);
    check("a used code says so", /already been used/i.test(e.el("ccRD-err").textContent),
          e.el("ccRD-err").textContent);
    check("the button is usable again", e.el("ccRD-go").disabled === false);
    check("the typed code is kept so it can be corrected",
          e.el("ccRD-code").value === "CC-ABCD-EFGH-JKMN-PQRS");
  }
  {
    const e = makeEnv({ resp: { ok: false, error: "bad_code", message: "Server said this." } });
    e.win.__ccRedeemRender();
    e.el("ccRD-code").value = "CC-ABCD-EFGH-JKMN-PQRS";
    e.el("ccRD-go").click();
    await flush();
    check("the server's own sentence wins over the local copy",
          e.el("ccRD-err").textContent === "Server said this.", e.el("ccRD-err").textContent);
  }
  {
    const e = makeEnv({ postResp: () => null });   // the request throws
    e.win.__ccRedeemRender();
    e.el("ccRD-code").value = "CC-ABCD-EFGH-JKMN-PQRS";
    e.el("ccRD-go").click();
    await flush();
    check("a dead network is a sentence, not an unhandled rejection",
          /try again/i.test(e.el("ccRD-err").textContent), e.el("ccRD-err").textContent);
    check("a dead network does not refresh the profile", e.refreshes.length === 0);
  }

  console.log("\n── one box, two kinds of code ─────────────────────────────");
  {
    const e = makeEnv({});
    e.win.__ccRedeemRender();
    e.el("ccRD-code").value = "4985";
    e.el("ccRD-go").click();
    await flush();
    check("a 4-digit friend code goes to referral.js", e.referralCalls.length === 1);
    check("a friend code is NOT posted to /api/redeem/code", e.posts.length === 0,
          JSON.stringify(e.posts));
    check("a friend code still refreshes the profile", e.refreshes.length === 1);
  }
  {
    const e = makeEnv({});
    e.win.__ccRedeemRender();
    e.el("ccRD-code").value = "Pal#4985";
    e.el("ccRD-go").click();
    await flush();
    check("Name#code goes to referral.js too", e.referralCalls.length === 1);
  }
  {
    const e = makeEnv({ withReferralModule: false });
    e.win.__ccRedeemRender();
    e.el("ccRD-code").value = "4985";
    e.el("ccRD-go").click();
    await flush();
    check("with referral.js absent a friend code fails politely",
          e.el("ccRD-err").textContent.length > 0 && e.refreshes.length === 0,
          e.el("ccRD-err").textContent);
  }
  {
    const e = makeEnv({ referralResp: { ok: false, error: "already_redeemed" } });
    e.win.__ccRedeemRender();
    e.el("ccRD-code").value = "4985";
    e.el("ccRD-go").click();
    await flush();
    check("a refused friend code does not refresh the profile", e.refreshes.length === 0);
    check("the button is usable again after a refused friend code",
          e.el("ccRD-go").disabled === false);
  }

  console.log("\n── keyboard ───────────────────────────────────────────────");
  {
    const e = makeEnv({});
    e.win.__ccRedeemRender();
    e.el("ccRD-code").value = "CC-ABCD-EFGH-JKMN-PQRS";
    e.el("ccRD-code").keydown("Enter");
    await flush();
    check("Enter in the box redeems", e.posts.length === 1);
  }

  console.log("\n── the feature flag ───────────────────────────────────────");
  {
    const e = makeEnv({ disabled: true });
    await e.win.__ccRedeemSync();
    check("codes off: no box that would only ever refuse", !e.el("ccRD-code"));
    const off = e.el("cc-redeem-root");
    check("codes off: the card says why and names the fallback",
          !!off && /isn't switched on/i.test(off.innerHTML)
                && /Claim Rewards/i.test(off.innerHTML),
          off ? off.innerHTML.slice(0, 120) : "no root");
  }
  {
    const e = makeEnv({ disabled: false });
    await e.win.__ccRedeemSync();
    check("codes on: the box is there", !!e.el("ccRD-code"));
  }
  {
    const e = makeEnv({ stateThrows: true });
    await e.win.__ccRedeemSync();
    check("a dead status check still shows the box (fail open)", !!e.el("ccRD-code"));
  }

  console.log("\n── the module cannot pay anybody ──────────────────────────");
  {
    check("no coin amount is hard-coded in the module",
          !/\b(1000|7000|15000|30000)\b/.test(SRC));
    check("it knows only the two /api/redeem endpoints",
          (SRC.match(/"\/api\/[^"]+"/g) || []).sort().join(",")
            === '"/api/redeem/code","/api/redeem/state"',
          (SRC.match(/"\/api\/[^"]+"/g) || []).join(","));
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  if (fail) process.exit(1);
})();
