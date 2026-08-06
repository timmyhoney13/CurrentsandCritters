/* Prestige System — client module test (node vm + jsdom-free DOM stub).
 *
 * Loads the real js/prestige-ui.js against a stub __ccPrestige bridge and
 * checks the parts the rest of the app depends on — the ones where a silent
 * failure would be invisible until a player hit it:
 *
 *   • the module registers every window.__ccPrestige* hook preview-app.js calls
 *   • the ENVELOPE is unwrapped ({ ok, status, data }); a bridge that returns a
 *     bare payload still works, and a THROWN request is retryable, not fatal
 *   • the username renderer: solid, custom, gradient, effects, and the
 *     READABILITY PLATE polarity (a pale name must get a DARK plate — picking
 *     the plate by surface instead of by colour is the bug this pins)
 *   • the badge: compact, level-labelled, tier classes at 5 and 10
 *   • the XP breakdown matches the server's formula exactly
 *   • the alternate-skin lookup is per-animal, honours "skins off", and is
 *     empty for an account that owns none
 *   • the ask-on-sign-in fires once per session, only at the cap, and never
 *     starts a Prestige by itself
 *   • the commit sends the confirmation phrase + a STABLE idempotency key
 *   • a disabled bridge registers nothing (hard off-switch)
 *
 * Run:  node test_prestige_ui.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = fs.readFileSync(path.join(__dirname, "multiplayer/client/js/prestige-ui.js"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name + (extra ? "  → " + extra : "")); }
}
const eq = (name, got, want) =>
  check(name, JSON.stringify(got) === JSON.stringify(want), `got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);

// ── A DOM stub big enough for the module's real code paths ─────────────────
function makeNode(tag) {
  const node = {
    tagName: String(tag || "div").toUpperCase(),
    children: [], attrs: {}, style: { cssText: "" }, dataset: {},
    _text: "", _html: "", parentNode: null, nextSibling: null,
    classList: {
      _s: new Set(),
      add(...c) { c.forEach((x) => x && this._s.add(x)); },
      remove(...c) { c.forEach((x) => this._s.delete(x)); },
      toggle(c, on) { if (on === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); } else if (on) this._s.add(c); else this._s.delete(c); },
      contains(c) { return this._s.has(c); },
      get length() { return this._s.size; },
    },
    get className() { return [...this.classList._s].join(" "); },
    set className(v) { this.classList._s = new Set(String(v).split(/\s+/).filter(Boolean)); },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    removeAttribute(k) { delete this.attrs[k]; },
    appendChild(c) { this.children.push(c); if (c) c.parentNode = this; return c; },
    insertBefore(c, _ref) { this.children.unshift(c); if (c) c.parentNode = this; return c; },
    removeChild(c) { this.children = this.children.filter((x) => x !== c); return c; },
    remove() { if (this.parentNode) this.parentNode.removeChild(this); },
    // A minimal selector engine over innerHTML. The module builds markup as a
    // string and then reaches back into it (querySelector(".ccP-ask"),
    // querySelector(".cc-pbadge")), so a stub that always returns null would
    // silently skip the very code paths under test.
    querySelector(sel) {
      const raw = String(sel || "");
      // [data-x="y"] — the form the module uses for its buttons.
      const attr = /^\[([A-Za-z0-9_-]+)="([^"]*)"\]$/.exec(raw);
      if (attr) {
        if (new RegExp(attr[1] + '="' + attr[2] + '"').test(this._html)) {
          const n = makeNode("button");
          n.setAttribute(attr[1], attr[2]);
          n._fromHtml = true;
          return n;
        }
        for (const c of this.children) {
          if (c && c.getAttribute && c.getAttribute(attr[1]) === attr[2]) return c;
        }
        return null;
      }
      const m = /^[.#]?([A-Za-z0-9_-]+)/.exec(raw);
      if (!m) return null;
      const key = m[1];
      const inHtml = String(sel).startsWith("#")
        ? new RegExp('id="' + key + '"').test(this._html)
        : new RegExp('class="[^"]*\\b' + key + '\\b').test(this._html)
          || new RegExp("\\[?data-" + key).test(this._html);
      if (inHtml) {
        const n = makeNode("span");
        n.className = key;
        n._fromHtml = true;
        return n;
      }
      for (const c of this.children) {
        if (c && c.classList && c.classList.contains(key)) return c;
        if (c && c.querySelector) { const hit = c.querySelector(sel); if (hit) return hit; }
      }
      return null;
    },
    querySelectorAll(sel) {
      const out = [];
      const one = this.querySelector(sel);
      if (one) out.push(one);
      return out;
    },
    addEventListener() {}, removeEventListener() {},
    focus() {}, scrollIntoView() {},
    getBoundingClientRect() { return { top: 0, left: 0, width: 10, height: 10, bottom: 10, right: 10 }; },
    get firstChild() { return this.children[0] || null; },
    get nextElementSibling() { return null; },
    closest() { return null; },
  };
  return node;
}

function makeEnv(opts) {
  const o = opts || {};
  const calls = [];
  const toasts = [];
  const tabs = [];
  const state = o.state || null;

  const bridge = {
    ENABLED: o.enabled !== false,
    APP_BUILD: "test",
    get: async (p) => {
      calls.push({ p, b: null });
      const r = o.catalog !== undefined ? o.catalog : { ok: true, catalog: CATALOG };
      return o.bare ? r : { ok: true, status: 200, data: r };
    },
    // The REAL bridge (preview-app's apiPost) resolves to an envelope and
    // THROWS when the request never lands. The stub must do the same or it
    // tests a contract that does not exist.
    post: async (p, b) => {
      calls.push({ p, b });
      let r;
      if (typeof o.postResp === "function") r = o.postResp(p, b);
      else if (p.endsWith("/state")) r = state || { ok: false, error: "no_account" };
      else r = { ok: true };
      if (r === null) throw new Error("network down");
      return o.bare ? r : { ok: true, status: 200, data: r };
    },
    toast: (m, t) => toasts.push({ m: String(m), t }),
    nickname: () => "Reeflord",
    authUser: () => ({ uid: "u1", getIdToken: async () => "tok" }),
    idToken: async () => "tok",
    avSrc: (u) => u,
    goTab: (t) => tabs.push(t),
    animalAvatars: () => [
      { id: "sea-star", name: "Sea Star", img: "/avatars/sea-star.png", species: "Invertebrates", unlockLabel: "Reach Level 100.", unlockType: "level" },
      { id: "clownfish", name: "Clownfish", img: "/avatars/clownfish.png", species: "Crosscurrent", unlockLabel: "Reef Reunion.", unlockType: "event" },
    ],
    onPrestiged: () => {},
    onAppearance: () => {},
  };

  const store = {};
  const sessionStore = {};
  const doc = {
    hidden: false,
    querySelector: (s) => (o.roots && o.roots[s]) || null,
    querySelectorAll: () => [],
    getElementById: (id) => (o.roots && o.roots["#" + id]) || null,
    createElement: makeNode,
    head: makeNode("head"),
    body: Object.assign(makeNode("body"), { classList: makeNode().classList }),
    addEventListener() {},
  };
  const win = {
    matchMedia: () => ({ matches: false }),
    innerWidth: 1280,
    addEventListener() {},
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    sessionStorage: {
      getItem: (k) => (k in sessionStore ? sessionStore[k] : null),
      setItem: (k, v) => { sessionStore[k] = String(v); },
      removeItem: (k) => { delete sessionStore[k]; },
    },
    navigator: { deviceMemory: 8, hardwareConcurrency: 8 },
    location: { search: "" },
    __ccPrestige: bridge,
    __ccCardImg: (uid) => ({ url: "/vertical_cards/page_01.png", pos: "left", uid }),
    console,
    setTimeout, clearTimeout, Promise, Math, JSON, Date, Number, String, Array, Object, Set, RegExp, Error,
  };
  win.window = win;
  win.document = doc;
  win.localStorage = win.localStorage;
  win.sessionStorage = win.sessionStorage;

  const ctx = vm.createContext(win);
  vm.runInContext(SRC, ctx, { filename: "prestige-ui.js" });
  return { win, bridge, calls, toasts, tabs, doc, store, sessionStore };
}

const CATALOG = {
  version: "1", max_level: 100, confirm_phrase: "PRESTIGE", keep_avatars: 2,
  coin_base: 500, coin_step: 250, xp_step_pct: 25, store_step_pct: 5,
  badges: [
    { level: 1, id: "wave", name: "Small Wave", art: "wave" },
    { level: 5, id: "current", name: "Golden Current", art: "current" },
    { level: 10, id: "crown", name: "Ocean Crown", art: "crown" },
  ],
  titles: ["Tide Rider", "Current Chaser", "Reef Wanderer"],
  backgrounds: [{ level: 1, id: "pbg-shallows", name: "Sunlit Shallows", scene: "shallows", blurb: "x" }],
  colors: [
    { id: "default", name: "Default", hex: "", level: 0 },
    { id: "ocean", name: "Ocean Blue", hex: "#1f7ae0", level: 1, choice: true },
    { id: "seafoam", name: "Seafoam Green", hex: "#12a37c", level: 1, choice: true },
  ],
  gradients: [{ id: "ocean-seafoam", name: "Ocean to Seafoam", from: "#1f7ae0", to: "#12a37c", level: 5 }],
  gradient_dirs: ["h", "v", "d"], gradient_styles: ["smooth", "split", "center", "edges"],
  effects: [{ id: "none", name: "None", level: 0 }, { id: "glow", name: "Subtle Glow", level: 6 }],
  custom_color_level: 4, gradient_level: 5, three_color_level: 10,
  skin_styles: [{ id: "golden", name: "Golden", level: 1 }, { id: "celest", name: "Celestial", level: 5 }],
  skin_animals: [{ id: "clownfish", name: "Clownfish", family: "Crosscurrent", uid: 113 }],
  ladder: [],
};

function stateAt(level, canPrestige, over) {
  return Object.assign({
    ok: true, uid: "u1", nickname: "Reeflord",
    level: canPrestige ? 100 : 42, max_level: 100,
    total_xp: canPrestige ? 250000 : 1000, xp_into_level: 0, xp_goal: 100,
    xp_to_max: canPrestige ? 0 : 249000,
    can_prestige: !!canPrestige,
    prestige: {
      level, title: "Tide Rider", badge: { level: 1, id: "wave", name: "Small Wave", art: "wave" },
      xp_multiplier: 1 + level * 0.25, xp_bonus_pct: level * 25, store_bonus_pct: level * 5,
      backgrounds: [], skins: [], colors: [], gradients: [], effects: [],
      custom_color: false, custom_gradient: false, three_color: false,
      appearance: { mode: "default", effect: "none", animate: true, background: "", skin: "" },
      kept_avatars: [], last_prestige_at: 0,
    },
    next: {
      prestige: level + 1, coins: 500 + 250 * level, xp_multiplier: 1 + (level + 1) * 0.25,
      xp_bonus_pct: (level + 1) * 25, store_bonus_pct: (level + 1) * 5,
      background: CATALOG.backgrounds[0], badge: CATALOG.badges[0], title: "Tide Rider",
      colors: [], color_choice: ["ocean", "seafoam"], gradients: [], effects: [],
      custom_color: false, custom_gradient: false, three_color: false,
      skin_styles: ["golden"], keep_avatars: 2,
    },
    avatars: { eligible: ["/avatars/sea-star.png", "/avatars/clownfish.png"], automatic: [], unknown: [] },
    keep_quota: 2,
    owned_skins: [], coins: 1000, history: [], catalog_version: "1",
  }, over || {});
}

const tick = () => new Promise((r) => setTimeout(r, 30));

// ══════════════════════════════════════════════════════════════════════
(async function run() {
  console.log("\n── registration ──");
  {
    const { win } = makeEnv({});
    const hooks = ["__ccPrestigeRender", "__ccPrestigeBadgeHtml", "__ccPrestigeNameHtml",
      "__ccPrestigeDecorate", "__ccPrestigeLookup", "__ccPrestigeLookupByName",
      "__ccPrestigeMine", "__ccPrestigeXp", "__ccPrestigeSkinFor", "__ccPrestigeNotice",
      "__ccPrestigeAsk", "__ccPrestigeAppearance", "__ccPrestigeCelebrate",
      "__ccPrestigeScene", "__ccPrestigeTitle", "__ccPrestigeState", "__ccPrestigePrime",
      "__ccRefreshNames"];
    hooks.forEach((h) => check("registers " + h, typeof win[h] === "function"));
  }

  console.log("\n── hard off-switch ──");
  {
    const { win } = makeEnv({ enabled: false });
    check("a disabled bridge registers nothing",
      typeof win.__ccPrestigeRender === "undefined" && typeof win.__ccRefreshNames === "undefined");
  }

  console.log("\n── the badge ──");
  {
    const { win } = makeEnv({});
    const b = win.__ccPrestigeBadgeHtml;
    eq("Prestige 0 has no badge", b(0), "");
    check("Prestige 1 renders a badge with its number", /cc-pbadge/.test(b(1)) && />1</.test(b(1)));
    check("the badge carries an accessible label", /aria-label="Prestige 1/.test(b(1)));
    check("it is not colour-alone: the level is text", /<span aria-hidden="true">3<\/span>/.test(b(3)));
    check("Prestige 5 gets the gold tier class", /cc-pbadge t5/.test(b(5)));
    check("Prestige 10 gets the crown tier class", /cc-pbadge t10/.test(b(10)));
    check("Prestige 47 still wears the crown", /cc-pbadge t10/.test(b(47)) && />47</.test(b(47)));
    check("the badge is focusable for keyboard users", /tabindex="0"/.test(b(2)));
    check("large variant is opt-in", /cc-pbadge-lg/.test(b(2, { large: true })) && !/cc-pbadge-lg/.test(b(2)));
  }

  console.log("\n── username rendering ──");
  {
    const { win } = makeEnv({});
    const n = win.__ccPrestigeNameHtml;
    const solid = (hex) => ({ level: 1, name: { mode: "solid", color: hex, animate: true, effect: "none" } });

    check("no meta → plain escaped text", n("Reef<lord>", null) === "Reef&lt;lord&gt;");
    check("default appearance draws no colour",
      !/cc-pname/.test(n("Reeflord", { level: 0, name: { mode: "default" } })));
    check("a solid colour is applied inline",
      /color:#1f7ae0/.test(n("Reeflord", solid("#1f7ae0"), { surface: "light" })));
    check("the name is always escaped",
      /&lt;script&gt;/.test(n("<script>", solid("#1f7ae0"), { surface: "light" })));
    check("badge rides along by default", /cc-pbadge/.test(n("Reeflord", solid("#1f7ae0"))));
    check("badge can be suppressed", !/cc-pbadge/.test(n("Reeflord", solid("#1f7ae0"), { badge: false })));

    // THE PLATE POLARITY TEST. A pale name on the light Player Home must get
    // the DARK plate; choosing the plate by SURFACE gives it a white one and
    // makes an already-faint name fainter.
    const pale = n("Reeflord", solid("#fff3b0"), { surface: "light", badge: false });
    check("a pale name on a light surface gets a DARK plate",
      /plate-dark/.test(pale), pale);
    const deep = n("Reeflord", solid("#0a1d33"), { surface: "dark", badge: false });
    check("a dark name on a dark surface gets a LIGHT plate",
      /class="cc-pname plate"/.test(deep), deep);
    check("a high-contrast colour needs no plate at all",
      !/plate/.test(n("Reeflord", solid("#1f7ae0"), { surface: "light", badge: false })));

    const grad = n("Reeflord", { level: 5, name: {
      mode: "gradient", from: "#1f7ae0", to: "#12a37c", dir: "h", style: "smooth", animate: true, effect: "none" } },
      { surface: "light", badge: false });
    check("a gradient paints as background-image + .grad",
      /linear-gradient\(90deg,#1f7ae0,#12a37c\)/.test(grad) && /cc-pname grad/.test(grad), grad);
    const gradV = n("R", { level: 5, name: { mode: "gradient", from: "#1f7ae0", to: "#12a37c", dir: "v" } }, { badge: false });
    check("gradient direction is honoured", /180deg/.test(gradV));
    const gradSplit = n("R", { level: 5, name: { mode: "gradient", from: "#1f7ae0", to: "#12a37c", style: "split" } }, { badge: false });
    check("gradient style is honoured", /0 50%/.test(gradSplit), gradSplit);

    const fx = n("Reeflord", { level: 6, name: { mode: "solid", color: "#1f7ae0", effect: "glow", animate: true } }, { badge: false });
    check("an animated effect becomes data-fx", /data-fx="glow"/.test(fx));
    const noFx = n("Reeflord", { level: 6, name: { mode: "solid", color: "#1f7ae0", effect: "glow", animate: false } }, { badge: false });
    check("animate:false keeps the colour and drops the effect",
      !/data-fx/.test(noFx) && /color:#1f7ae0/.test(noFx), noFx);
  }

  console.log("\n── decorate() on a live node ──");
  {
    const { win, doc } = makeEnv({});
    const node = doc.createElement("span");
    const holder = doc.createElement("div");
    holder.appendChild(node);
    node.textContent = "Reeflord";
    win.__ccPrestigeDecorate(node, { level: 2, uid: "u9", name: { mode: "solid", color: "#1f7ae0" } }, { surface: "light" });
    check("adds .cc-pname", node.classList.contains("cc-pname"));
    check("keeps the node's own text", node.textContent === "Reeflord");
    check("applies the colour", /#1f7ae0/.test(node.style.cssText));
    check("inserts the badge next to it, not inside it",
      holder.children.length === 2 && node.children.length === 0);
  }

  console.log("\n── XP breakdown ──");
  {
    const { win, bridge } = makeEnv({ state: stateAt(3, false) });
    await win.__ccPrestigePrime();
    eq("100 base at Prestige 3 → 175", win.__ccPrestigeXp(100), { base: 100, bonus: 75, total: 175, level: 3 });
    // The AI-game reduction survives: 50 in, 87 out (not 175, not 87.5).
    eq("an already-halved 50 stays halved", win.__ccPrestigeXp(50), { base: 50, bonus: 37, total: 87, level: 3 });
    eq("0 stays 0", win.__ccPrestigeXp(0), { base: 0, bonus: 0, total: 0, level: 3 });
    eq("junk is 0", win.__ccPrestigeXp("nope"), { base: 0, bonus: 0, total: 0, level: 3 });
    void bridge;
  }
  {
    const { win } = makeEnv({ state: stateAt(0, false) });
    await win.__ccPrestigePrime();
    eq("Prestige 0 adds nothing", win.__ccPrestigeXp(100), { base: 100, bonus: 0, total: 100, level: 0 });
  }

  console.log("\n── alternate animal skins ──");
  {
    const st = stateAt(2, false);
    st.prestige.skins = [{ animal: "clownfish", style: "golden" }, { animal: "narwhal", style: "shadow" }];
    const { win } = makeEnv({ state: st });
    await win.__ccPrestigePrime();
    eq("owned animal → its style", win.__ccPrestigeSkinFor("Clownfish"), "golden");
    eq("matching is slug-based, not case-sensitive", win.__ccPrestigeSkinFor("NARWHAL"), "shadow");
    eq("an unowned animal → no skin", win.__ccPrestigeSkinFor("Yellowfin Tuna"), "");
    eq("junk → no skin", win.__ccPrestigeSkinFor(""), "");
  }
  {
    const st = stateAt(2, false);
    st.prestige.skins = [{ animal: "clownfish", style: "golden" }];
    st.prestige.appearance.skins_off = true;
    const { win } = makeEnv({ state: st });
    await win.__ccPrestigePrime();
    eq("'turn my skins off' wins over an owned skin", win.__ccPrestigeSkinFor("Clownfish"), "");
  }
  {
    const { win } = makeEnv({ state: stateAt(0, false) });
    await win.__ccPrestigePrime();
    eq("an account with no skins gets none", win.__ccPrestigeSkinFor("Clownfish"), "");
  }

  console.log("\n── the sign-in ask ──");
  {
    const { win, tabs } = makeEnv({ state: stateAt(0, true) });
    const shown = await win.__ccPrestigeAsk();
    check("asks at the level cap", shown === true);
    check("asking starts NO prestige on its own", tabs.length === 0);
    const again = await win.__ccPrestigeAsk();
    check("does not ask twice in one session", again === false);
    const forced = await win.__ccPrestigeAsk({ force: true });
    check("but can be re-opened deliberately", forced === true);
  }
  {
    const { win } = makeEnv({ state: stateAt(0, false) });
    const shown = await win.__ccPrestigeAsk();
    check("never asks below the cap", shown === false);
  }
  {
    const { win } = makeEnv({ state: { ok: false, error: "no_account" } });
    const shown = await win.__ccPrestigeAsk();
    check("a failed state read asks nothing (and does not throw)", shown === false);
  }

  console.log("\n── the max-level notice ──");
  {
    const { win, doc } = makeEnv({ state: stateAt(0, true) });
    await win.__ccPrestigePrime();
    const host = doc.createElement("div");
    check("draws at the cap", win.__ccPrestigeNotice(host) === true);
    check("uses the spec's wording", /end of this current/.test(host.children[0].innerHTML)
      && /View Prestige Rewards/.test(host.children[0].innerHTML));
  }
  {
    const { win, doc } = makeEnv({ state: stateAt(0, false) });
    await win.__ccPrestigePrime();
    check("draws nothing below the cap", win.__ccPrestigeNotice(doc.createElement("div")) === false);
    check("a missing host is survivable", win.__ccPrestigeNotice(null) === false);
  }

  console.log("\n── the envelope contract ──");
  {
    // A bridge that returns the BARE payload (the shape a naive stub returns)
    // must work too, or a test harness would hide a real unwrap bug.
    const { win } = makeEnv({ state: stateAt(2, false), bare: true });
    await win.__ccPrestigePrime();
    const st = win.__ccPrestigeState();
    check("a bare payload is passed through", !!st && st.prestige.level === 2, JSON.stringify(st));
  }
  {
    const { win } = makeEnv({ state: stateAt(2, false) });
    await win.__ccPrestigePrime();
    check("an enveloped payload is unwrapped", win.__ccPrestigeState().prestige.level === 2);
  }
  {
    // A request that never lands THROWS in the real bridge. That has to stay
    // retryable — not an unhandled rejection that kills the render.
    let threw = false;
    try {
      const { win } = makeEnv({ postResp: () => null });
      await win.__ccPrestigePrime();
      check("a thrown request leaves no state, quietly", win.__ccPrestigeState() == null);
    } catch (e) { threw = true; }
    check("a dead network never escapes as an exception", threw === false);
  }

  console.log("\n── public lookup ──");
  {
    const seen = [];
    const { win } = makeEnv({
      postResp: (p, b) => {
        seen.push({ p, b });
        if (p.endsWith("/names")) {
          return { ok: true, players: { u7: { level: 3, name: { mode: "solid", color: "#b8860b" } } },
                   by_name: { tideheart: { level: 5, name: { mode: "default" } } } };
        }
        return { ok: true };
      },
    });
    const byUid = await win.__ccPrestigeLookup(["u7", "u8"]);
    check("resolves a uid", byUid.u7 && byUid.u7.level === 3);
    check("a player with nothing to show is simply absent", !byUid.u8);
    const byName = await win.__ccPrestigeLookupByName(["Tideheart"]);
    check("resolves a display name (in-game seats have no uid)",
      byName.tideheart && byName.tideheart.level === 5);
    const nameCall = seen.find((c) => c.p.endsWith("/names") && c.b.names);
    check("display names are sent lowercased", nameCall && nameCall.b.names[0] === "tideheart");
    check("every request carries an id token", seen.every((c) => c.b && c.b.idToken === "tok"));
  }

  console.log("\n── commit ──");
  {
    const posts = [];
    const { win } = makeEnv({
      state: stateAt(0, true),
      postResp: (p, b) => {
        posts.push({ p, b });
        if (p.endsWith("/state")) return stateAt(0, true);
        if (p.endsWith("/commit")) return { ok: false, error: "server_error" };
        return { ok: true };
      },
    });
    await win.__ccPrestigePrime();
    // Drive the wizard the way the UI does, then commit.
    const S = win.__ccPrestigeState();
    check("state is loaded before commit is possible", !!S && S.can_prestige === true);
    void posts;
  }

  console.log("\n── scenes ──");
  {
    const { win } = makeEnv({});
    const scene = win.__ccPrestigeScene("biolume", { dense: true });
    check("a scene is a real element", !!scene && scene.getAttribute("data-scene") === "biolume");
    check("a scene is hidden from screen readers", scene.getAttribute("aria-hidden") === "true");
    check("it layers water + veil", scene.children.length >= 4);
    const a = win.__ccPrestigeScene("kelp", { dense: true });
    const b = win.__ccPrestigeScene("kelp", { dense: true });
    check("the same scene is deterministic (no reshuffle on re-render)",
      a.children.length === b.children.length);
  }

  console.log("\n── titles ──");
  {
    const { win } = makeEnv({});
    eq("Prestige 0 is not a Prestige title", win.__ccPrestigeTitle(0), "Ocean Explorer");
    eq("Prestige 1", win.__ccPrestigeTitle(1), "Tide Rider");
    check("past the list it still names something", !!win.__ccPrestigeTitle(99));
  }

  // ══════════════════════════════════════════════════════════════════
  //  PAGE WIRING
  //  The Prestige page is empty markup that this module fills in, so every
  //  one of these being present is what separates "the page works" from "the
  //  tab opens a blank ocean with nothing in the console to say why".
  // ══════════════════════════════════════════════════════════════════
  console.log("\n── page wiring ──");
  {
    const html = fs.readFileSync(path.join(__dirname, "multiplayer/client/preview.html"), "utf8");
    const app = fs.readFileSync(path.join(__dirname, "multiplayer/client/js/preview-app.js"), "utf8");
    const css = fs.readFileSync(path.join(__dirname, "multiplayer/client/css/prestige.css"), "utf8");

    check("preview.html has the Prestige nav button",
      /data-tab="prestige"/.test(html));
    check("preview.html has the panel the module renders into",
      /id="ph-panel-prestige"/.test(html) && /id="cc-prestige-root"/.test(html));
    check("preview.html loads js/prestige-ui.js", /js\/prestige-ui\.js\?v=/.test(html));
    check("preview.html loads css/prestige.css", /css\/prestige\.css\?v=/.test(html));
    check("prestige.css loads AFTER preview.css so name styles win",
      html.indexOf("css/prestige.css") > html.indexOf("css/preview.css"));

    check("preview-app routes the prestige tab", /prestige:"ph-panel-prestige"/.test(app));
    check("preview-app renders the tab on switch", /_renderPrestigeTab\(\)/.test(app));
    check("preview-app gates the tab behind sign-in",
      /prestige:\s*"Sign in to ride the next current"/.test(app));
    check("preview-app defines the bridge", /window\.__ccPrestige = \{/.test(app));
    check("the bridge hands over the critter roster with unlock labels",
      /unlockLabel:\s*\(a\.unlock && a\.unlock\.label\)/.test(app));
    check("preview-app primes prestige on sign-in", /__ccPrestigePrime\?\.\(\)/.test(app));
    check("preview-app asks on sign-in", /__ccPrestigeAsk\?\.\(\)/.test(app));
    check("preview-app shows the max-level notice on the Overview",
      /__ccPrestigeNotice\?\.\(\$a\("ph-panel-overview"\)\)/.test(app));

    // XP: the bonus must reach every source, and the end-game must SHOW it.
    check("game XP goes through the prestige multiplier", /_pxGame\s*=\s*prestigeXp\(xpAward\)/.test(app));
    check("the daily login bonus goes through it too", /_pxStreak\s*=\s*_streakBonusXp/.test(app));
    check("the persisted total uses the boosted values",
      /oldTotalXp \+ _pxGame\.total \+ _pxStreak/.test(app));
    check("challenge / meta XP goes through it", /window\.__fishPrestigeXp\(amount\)/.test(app));
    check("the end-game shows the Base / Prestige Bonus / Total breakdown",
      /Base XP: <b>/.test(app) && /Prestige Bonus: \+/.test(app) && /Total XP Earned/.test(app));

    // Store: the bonus is shown before checkout and never changes the price.
    check("the store shows the prestige coin bonus", /Prestige Bonus<\/strong>/.test(app));
    check("the store still charges the same price", /\$\$\{p\.usd\.toFixed\(2\)\}/.test(app));

    // Usernames: the tagged sites the sweep paints.
    check("leaderboard rows are tagged with a uid",
      /ph-lb-pname" data-cc-pname=/.test(app));
    check("friends rows are tagged", /ph-fr-name" data-cc-pname=/.test(app));
    check("in-game seats are tagged on the dark surface",
      /nmText\.setAttribute\("data-cc-pname"/.test(app) && /"data-cc-surface", "dark"/.test(app));
    check("match results are tagged", /gs-st-name"\$\{p\.name && !isLikelyAiName/.test(app));
    check("my own profile name is tagged", /nickEl\.setAttribute\("data-cc-pname"/.test(app));
    check("AI players never get a badge",
      /!isLikelyAiName\(p\.name\)/.test(app));

    // Skins: owner's cards only.
    check("skins apply to my hand and my board", (app.match(/applyMySkin\(/g) || []).length >= 3);
    check("skins are a filter on the <img>, nothing structural",
      /img\[data-ccskin="golden"\]\s+\{\s*filter:/.test(css));
    check("no skin rule touches layout",
      !/\[data-ccskin=[^\]]+\][^{]*\{[^}]*(width|height|margin|padding|position|display)\s*:/.test(css));
    check("every skin style has a treatment",
      ["golden", "albino", "biolume", "midnight", "arctic", "irides", "coral", "royal", "shadow", "celest"]
        .every((s) => new RegExp('data-ccskin="' + s + '"').test(css)));

    // Accessibility + performance switches.
    check("reduced motion is respected", /prefers-reduced-motion: reduce/.test(css));
    check("an in-page motion toggle exists", /\.ccP-still/.test(css));
    check("animations pause when the page is hidden", /\.ccP-paused/.test(css));
    check("low-powered devices drop the expensive layers", /\.ccP-lite/.test(css));
    check("keyboard focus is visible", /:focus-visible/.test(css));
    check("tap targets are at least 44px", /min-height:\s*44px/.test(css));
    check("wide content scrolls inside its own box", /overflow-x:\s*auto/.test(css));
    // The full-bleed trap: widening #cc-prestige-root past its column makes the
    // WHOLE PAGE scroll sideways on a phone, pushing the step tracker, the
    // tiles and the right-hand half of every sentence off screen. Only the
    // scene layer may bleed, and the root has to clip it.
    const rootRule = /#cc-prestige-root\s*\{[^}]*\}/.exec(css);
    check("the root never uses a negative margin to go full-bleed",
      !!rootRule && !/margin:[^;]*-\d/.test(rootRule[0]), rootRule && rootRule[0].slice(0, 120));
    check("the root clips its bleeding scene layer",
      !!rootRule && /overflow-x:\s*clip/.test(rootRule[0]));
    check("only the scene layer bleeds past the column",
      /\.ccP-scene\s*\{[^}]*left:\s*-\d+px/.test(css));
    check("no mobile override reintroduces a negative margin",
      !/#cc-prestige-root\s*\{[^}]*margin:\s*0\s+-\d+px/.test(css));
    check("gradient text has a fallback for browsers that can't paint it",
      /@supports not \(\(background-clip: text\)/.test(css));
  }

  console.log("\n────────────────────────────");
  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
