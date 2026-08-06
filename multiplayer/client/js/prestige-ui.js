/* Currents and Critters — Prestige System UI (self-contained module).
 *
 * Renders the whole "Prestige" Player-Home page into #cc-prestige-root, plus
 * the pieces of Prestige that show up OUTSIDE that page:
 *
 *   window.__ccPrestigeRender()        the page itself
 *   window.__ccPrestigeBadgeHtml()     the badge that sits beside a username
 *   window.__ccPrestigeNameHtml()      a username drawn in its Prestige colour
 *   window.__ccPrestigeDecorate()      the same, applied to a live element
 *   window.__ccPrestigeLookup()        batch public lookup (leaderboards, etc.)
 *   window.__ccPrestigeMine()          my own public appearance (cached)
 *   window.__ccPrestigeXp()            base XP → { base, bonus, total }
 *   window.__ccPrestigeSkinFor()       card name → owned alternate skin id
 *   window.__ccPrestigeNotice()        the "you reached the cap" banner
 *   window.__ccPrestigeAppearance()    the username-appearance menu
 *   window.__ccPrestigeCelebrate()     the post-Prestige celebration
 *
 * EVERY rule lives on the server (/api/prestige/*). This file renders state and
 * sends intents: it never decides what a player owns, what they are owed, or
 * whether they may prestige. A tampered value here changes what the player sees
 * for one paint and is then thrown away by the server's own re-check.
 */
(function () {
  "use strict";

  function bridge() { return window.__ccPrestige; }
  // A MISSING bridge means preview-app.js never reached the line that defines
  // one. Registering anyway (and re-checking at click time) is what stops the
  // tab from being permanently, silently blank — the exact failure the Clans
  // tab shipped with once already.
  if (bridge() && bridge().ENABLED === false) return;

  const $ = (sel, root) => (root || document).querySelector(sel);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const el = (tag, cls, html) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  };
  const num = (v, d) => { const n = Number(v); return Number.isFinite(n) ? n : (d || 0); };
  const fmt = (n) => num(n).toLocaleString();
  const toast = (m, t) => { try { bridge().toast(m, t); } catch (_) {} };
  const avSrc = (u) => { try { return bridge().avSrc(u); } catch (_) { return u; } };

  // The bridge's post() resolves to an ENVELOPE — { ok, status, data } — where
  // `data` is the server's JSON body. Everything below reads the SERVER payload
  // (res.ok, res.prestige, res.avatars…), so unwrap in exactly one place.
  // Getting this wrong is what once rendered a whole tab blank with nothing in
  // the console to say why.
  function unwrap(res) {
    if (res && typeof res === "object" && "data" in res && "status" in res) {
      return res.data || { ok: false, error: "server_error" };
    }
    return res;
  }

  async function post(action, extra) {
    const b = bridge();
    if (!b) return { ok: false, error: "unavailable" };
    const body = Object.assign({}, extra || {});
    body.idToken = await b.idToken();
    if (!body.idToken) return { ok: false, error: "unauthorized" };
    // apiPost THROWS when the request never lands; null means "retryable",
    // which is a different thing from a request the server refused.
    try { return unwrap(await b.post("/api/prestige/" + action, body)); }
    catch (_) { return null; }
  }

  const ERR = {
    unavailable: "Prestige didn't finish loading — please refresh the page.",
    unauthorized: "Sign in to use Prestige.",
    firestore_unavailable: "Prestige is temporarily unavailable — try again shortly.",
    no_account: "We couldn't find your account — try signing in again.",
    not_max_level: "You haven't reached the maximum level yet.",
    already_prestiged: "That Prestige has already been completed.",
    prestige_cap: "You've reached the highest Prestige there is. Incredible.",
    confirm_required: "Type PRESTIGE in the confirmation box first.",
    idempotency_required: "Something went wrong starting the Prestige — please try again.",
    avatars_required: "Choose the two critters you want to keep.",
    avatars_count: "Choose exactly two critters to keep.",
    avatar_not_owned: "One of those critters isn't unlocked on your account.",
    avatar_already_kept: "That critter stays automatically — choose one that would relock.",
    skin_required: "Choose an animal and a skin style.",
    skin_unknown_animal: "That animal isn't in Currents and Critters.",
    skin_unknown_style: "That skin style doesn't exist.",
    skin_style_locked: "That skin style unlocks at a higher Prestige.",
    skin_already_owned: "You already own that skin — pick a different one.",
    color_choice_required: "Choose your name colour to continue.",
    color_locked: "You haven't unlocked that name colour.",
    custom_color_locked: "Custom colours unlock at Prestige 4.",
    gradient_locked: "Gradients unlock at Prestige 5.",
    three_color_locked: "Three-colour gradients unlock at Prestige 10.",
    effect_locked: "You haven't unlocked that name effect.",
    background_locked: "You haven't unlocked that background.",
    skin_locked: "You don't own that animal skin.",
    color_unreadable: "That colour is too hard to read against the game's backgrounds — try a stronger one.",
    color_reserved: "That colour is reserved for game staff and system messages.",
    bad_color: "That isn't a valid colour.",
    server_error: "Something went wrong — try again.",
  };
  const errMsg = (e) => ERR[e] || ("Something went wrong (" + esc(e || "unknown") + ").");
  // The one sentence shown when a commit fails. It is the truth: a failed
  // Prestige transaction writes NO part of the reset, so nothing changed.
  const FAIL_MSG = "The current was interrupted, and your Prestige was not completed. "
    + "Nothing on your account was changed. Please try again.";

  // ── Module state ───────────────────────────────────────────────────────────
  const S = {
    cat: null,          // /api/prestige/catalog
    state: null,        // /api/prestige/state
    step: 0,            // wizard step index
    keep: [],           // chosen avatar paths
    skin: null,         // { animal, style }
    colorPick: "",      // Prestige-1 colour choice
    skinQuery: "",
    skinFamily: "",
    skinStyle: "",
    skinDevice: "desktop",
    confirmText: "",
    busy: false,
    error: "",
    seq: 0,             // bumped on every navigation; late fetches check it
    mine: null,         // my public appearance (badge + name colour)
    lite: false,        // low-power device → fewer layers
    still: false,       // "reduce motion" toggled in-page
  };

  const STEPS = [
    { id: "rewards", label: "Rewards" },
    { id: "avatars", label: "Avatars" },
    { id: "skin", label: "Animal Skin" },
    { id: "color", label: "Name Colour" },
    { id: "review", label: "Reset Review" },
    { id: "confirm", label: "Confirmation" },
  ];

  // ══════════════════════════════════════════════════════════════════════
  //  DEVICE / MOTION BUDGET
  // ══════════════════════════════════════════════════════════════════════
  const prefersReduced = () => {
    try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; }
    catch (_) { return false; }
  };
  function detectLite() {
    try {
      if (navigator.deviceMemory && navigator.deviceMemory <= 4) return true;
      if (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 4) return true;
      if (document.body && document.body.classList.contains("cc-device-mobile")) return true;
      return window.innerWidth < 560;
    } catch (_) { return false; }
  }
  function readStill() {
    try { return localStorage.getItem("cc_prestige_still") === "1" || prefersReduced(); }
    catch (_) { return prefersReduced(); }
  }
  function setStill(on) {
    S.still = !!on;
    try { localStorage.setItem("cc_prestige_still", on ? "1" : "0"); } catch (_) {}
    const r = root(); if (r) r.classList.toggle("ccP-still", S.still);
    document.body.classList.toggle("ccP-still-names", S.still);
  }

  // Anything animating stops when the page is not visible — the difference
  // between a background tab costing nothing and costing a phone's battery.
  document.addEventListener("visibilitychange", () => {
    const hidden = document.hidden;
    const r = root(); if (r) r.classList.toggle("ccP-paused", hidden);
    const c = $("#cc-prestige-celebration"); if (c) c.classList.toggle("ccP-paused", hidden);
  });

  // ══════════════════════════════════════════════════════════════════════
  //  THE LIVING OCEAN SCENE
  //  Also the renderer for every unlocked Prestige background — the scenes
  //  ARE the backgrounds, which is why they can move.
  // ══════════════════════════════════════════════════════════════════════
  // Real critters from the game, used as the drifting silhouettes. No stock
  // art, no placeholders — these are the same PNGs the Avatar Gallery uses.
  const SCENE_CRITTERS = {
    shallows:  ["mullet", "sardine", "bottlenose-dolphin", "flying-fish"],
    kelp:      ["blue-tang", "sea-star", "california-gull", "spiny-lobster"],
    bloom:     ["clownfish", "mandarin-goby", "elkhorn-coral", "reef-triggerfish"],
    midnight:  ["manta-ray", "whale-shark", "big-eye-tuna", "barracuda"],
    golden:    ["yellowfin-tuna", "mahi-mahi", "sailfish", "king-salmon"],
    biolume:   ["bobtail-squid", "cuttlefish", "common-octopus", "sea-anemone"],
    arctic:    ["emperor-penguin", "narwhal", "king-crab", "horned-puffin"],
    trench:    ["giant-squid", "deep-sea-coral", "sea-cucumber", "great-white-shark"],
    surge:     ["osprey", "great-albatross", "magnificent-frigatebird", "blue-marlin"],
    celestial: ["loggerhead-sea-turtle", "manta-ray", "whale-shark", "sea-star"],
  };

  // Which way each critter's ARTWORK points before any mirroring. The deck has
  // no single convention — most animals are drawn facing left, but the sardine,
  // manta ray, whale shark, marlin and others were drawn facing right — so a
  // blanket flip makes half the ocean swim backwards. Every entry below was
  // checked against the actual PNG. "none" = no front to speak of (corals,
  // anemones, a sea star, a head-on bird): those are never mirrored, because
  // mirroring symmetrical art is a no-op at best and looks like a jitter at worst.
  // Anything the scenes use MUST be listed here — test_prestige_ui.js fails the
  // build otherwise, so a new scene critter can't silently swim tail-first.
  const FACING = {
    // ── faces right ──
    sardine: "right", "california-gull": "right", "manta-ray": "right",
    "whale-shark": "right", sailfish: "right", "king-salmon": "right",
    "bobtail-squid": "right", cuttlefish: "right", "common-octopus": "right",
    "great-albatross": "right", "blue-marlin": "right", bunker: "right",
    fish: "right", "peruvian-pelican": "right", tarpon: "right",
    "summer-skin-goby": "right", "summer-skin-gull": "right",
    // ── no meaningful facing ──
    "sea-star": "none", "elkhorn-coral": "none", "sea-anemone": "none",
    "deep-sea-coral": "none", "king-crab": "none", osprey: "none",
    "magnificent-frigatebird": "none", "giant-squid": "none",
    "grooved-brain-coral": "none", "staghorn-coral": "none", "sea-sponge": "none",
    "sea-urchin": "none", lobster: "none", "fourth-of-july": "none",
    // ── everything else faces left (mullet, bottlenose-dolphin, clownfish,
    //    barracuda, tunas, penguin, narwhal, puffin, turtle, shark, …) ──
  };
  /** The class that tells the CSS how to mirror this critter's art. */
  function facingClass(slug) {
    const f = FACING[String(slug || "")];
    return f === "right" ? " faces-right" : (f === "none" ? " faces-none" : "");
  }

  // Deterministic pseudo-random so a given scene always looks the same (a
  // background that reshuffles on every render reads as a glitch).
  function rng(seed) {
    let s = seed >>> 0 || 1;
    return () => { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return ((s >>> 0) % 10000) / 10000; };
  }
  function seedOf(str) {
    let h = 2166136261;
    for (let i = 0; i < String(str).length; i++) { h ^= String(str).charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }

  /** Build one animated ocean scene. `opts.dense` = the full page backdrop;
   *  otherwise a lighter version for a preview tile. */
  function buildScene(sceneId, opts) {
    const o = opts || {};
    const scene = String(sceneId || "shallows");
    const wrap = el("div", "ccP-scene");
    wrap.setAttribute("data-scene", scene);
    wrap.setAttribute("aria-hidden", "true");
    const r = rng(seedOf(scene));

    wrap.appendChild(el("div", "ccP-water"));
    wrap.appendChild(el("div", "ccP-current"));
    if (!S.lite) wrap.appendChild(el("div", "ccP-current b"));
    wrap.appendChild(el("div", "ccP-rays"));
    if (!S.lite) wrap.appendChild(el("div", "ccP-motes"));
    if (scene === "biolume" || scene === "trench" || scene === "celestial") {
      wrap.appendChild(el("div", "ccP-biolume"));
    }

    // Bubbles
    const bubs = el("div", "ccP-bubbles");
    const nBub = S.lite ? 7 : (o.dense ? 18 : 9);
    for (let i = 0; i < nBub; i++) {
      const b = el("span", "ccP-bub");
      const size = 4 + Math.round(r() * 11);
      b.style.cssText = "width:" + size + "px;height:" + size + "px;left:" + Math.round(r() * 98) + "%;"
        + "animation-duration:" + (9 + r() * 13).toFixed(1) + "s;"
        + "animation-delay:-" + (r() * 16).toFixed(1) + "s;";
      bubs.appendChild(b);
    }
    wrap.appendChild(bubs);

    // Kelp + coral silhouettes on the floor
    const flora = el("div", "ccP-flora");
    flora.innerHTML = kelpSvg(scene, r);
    wrap.appendChild(flora);

    // Drifting critters
    if (!S.lite && o.dense !== false) {
      const swim = el("div", "ccP-swimmers");
      const list = SCENE_CRITTERS[scene] || SCENE_CRITTERS.shallows;
      const n = o.dense ? Math.min(4, list.length) : 2;
      for (let i = 0; i < n; i++) {
        const img = el("img", "ccP-swim" + (r() > 0.5 ? " rtl" : "") + facingClass(list[i]));
        img.src = avSrc("/avatars/" + list[i] + ".png");
        img.alt = "";
        img.loading = "lazy";
        img.decoding = "async";
        const w = (o.dense ? 46 : 30) + Math.round(r() * (o.dense ? 54 : 22));
        img.style.cssText = "width:" + w + "px;top:" + (8 + Math.round(r() * 66)) + "%;"
          + "animation-duration:" + (34 + r() * 46).toFixed(1) + "s;"
          + "animation-delay:-" + (r() * 40).toFixed(1) + "s;"
          + "opacity:" + (0.5 + r() * 0.35).toFixed(2) + ";";
        swim.appendChild(img);
      }
      wrap.appendChild(swim);
    }

    wrap.appendChild(el("div", "ccP-veil"));
    return wrap;
  }

  function kelpSvg(scene, r) {
    const cold = scene === "arctic" || scene === "trench" || scene === "midnight";
    // Softer than a silhouette: on the bright reef these read as real plants,
    // not as holes cut in the artwork.
    const kelpC = cold ? "#3f8fae" : "#2f9e78";
    const coralA = scene === "bloom" ? "#f0788a" : (scene === "celestial" ? "#9d7ce8" : "#e07a56");
    const coralB = scene === "biolume" ? "#3ce8c0" : "#f0b060";
    let stalks = "";
    for (let i = 0; i < 7; i++) {
      const x = 5 + i * 15 + Math.round(r() * 7);
      const h = 40 + Math.round(r() * 52);
      const bend = (r() - 0.5) * 22;
      stalks += '<path d="M' + x + ' 100 C' + (x + bend) + ' ' + (100 - h * 0.45)
        + ',' + (x - bend) + ' ' + (100 - h * 0.75) + ',' + (x + bend * 0.6) + ' ' + (100 - h) + '"'
        + ' stroke="' + kelpC + '" stroke-width="' + (1.6 + r() * 2).toFixed(1) + '"'
        + ' stroke-linecap="round" fill="none" opacity="' + (0.5 + r() * 0.4).toFixed(2) + '"'
        + ' style="animation-duration:' + (9 + r() * 8).toFixed(1) + 's;animation-delay:-' + (r() * 6).toFixed(1) + 's"/>';
    }
    return '<svg class="ccP-kelp" viewBox="0 0 100 100" preserveAspectRatio="none"'
      + ' style="left:0;width:100%;height:100%" fill="none">' + stalks + '</svg>'
      + '<svg class="ccP-coral" viewBox="0 0 200 60" preserveAspectRatio="none"'
      + ' style="left:0;width:100%;height:34%" fill="none">'
      + '<path d="M0 60 Q22 40 40 52 Q56 34 74 50 Q94 28 116 48 Q138 32 158 52 Q178 40 200 58 L200 60 Z"'
      + ' fill="rgba(58,120,175,.30)"/>'
      + '<g class="ccP-coralglow">'
      + '<path d="M28 60 q-3-14 4-20 q6 8 3 20" fill="' + coralA + '" opacity=".55"/>'
      + '<path d="M96 60 q-4-17 5-24 q7 10 3 24" fill="' + coralB + '" opacity=".5"/>'
      + '<path d="M164 60 q-3-12 4-18 q6 7 2 18" fill="' + coralA + '" opacity=".45"/>'
      + '<circle cx="60" cy="52" r="4" fill="' + coralB + '" opacity=".5"/>'
      + '<circle cx="132" cy="50" r="3.4" fill="' + coralA + '" opacity=".5"/>'
      + '</g></svg>';
  }

  // ══════════════════════════════════════════════════════════════════════
  //  BADGE ART — SVG, so it costs nothing and scales beside any username
  // ══════════════════════════════════════════════════════════════════════
  const BADGE_ART = {
    wave: '<path d="M1 9c2.5-3 4.5-3 7 0s4.5 3 7 0" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/>',
    wave2: '<path d="M1 6.4c2.5-2.8 4.5-2.8 7 0s4.5 2.8 7 0M1 11c2.5-2.8 4.5-2.8 7 0s4.5 2.8 7 0" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" fill="none"/>',
    coral: '<path d="M8 15V6M8 9 5 5.5M8 9l3-3.5M5 5.5 3.4 2.6M11 5.5 12.6 2.6M8 6 8 2.2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" fill="none"/><circle cx="8" cy="15" r="1.1" fill="currentColor"/>',
    shell: '<path d="M8 14.2C4.2 14.2 1.4 11 1.4 7.4A6.6 6.6 0 0 1 14.6 7.4c0 3.6-2.8 6.8-6.6 6.8Z" stroke="currentColor" stroke-width="1.5" fill="none"/><path d="M8 14V2.2M4.2 12.6 6.6 2.6M11.8 12.6 9.4 2.6" stroke="currentColor" stroke-width="1.1" opacity=".75"/>',
    current: '<path d="M1.5 5.5c3-3 5-3 8 0s4 2.4 5 1M1.5 10.5c3-3 5-3 8 0s4 2.4 5 1" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" fill="none"/><circle cx="13.6" cy="8" r="1.3" fill="currentColor"/>',
    pearl: '<circle cx="8" cy="8" r="4.4" stroke="currentColor" stroke-width="1.6" fill="none"/><circle cx="6.4" cy="6.4" r="1.2" fill="currentColor" opacity=".8"/><path d="M8 1.2v1.6M8 13.2v1.6M1.2 8h1.6M13.2 8h1.6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
    trident: '<path d="M8 15V4M3 6V2.4M13 6V2.4M3 6c0 2.8 2.2 4 5 4s5-1.2 5-4M8 4V1.4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/>',
    nautilus: '<path d="M8 14a6 6 0 1 0-6-6c0 2.2 1.8 4 4 4s3-1.4 3-2.8-1.2-2.2-2.2-2.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/>',
    aurora: '<path d="M1.6 12c1.6-5 4-8.4 6.4-8.4S12.8 7 14.4 12" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" fill="none"/><path d="M4.4 12.6c1.2-3.4 2.4-5.4 3.6-5.4s2.4 2 3.6 5.4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" fill="none" opacity=".7"/><circle cx="8" cy="2.2" r="1.1" fill="currentColor"/>',
    crown: '<path d="M1.6 12.2 2.8 4.4l3.4 3L8 2.2l1.8 5.2 3.4-3 1.2 7.8Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" fill="none"/><path d="M2.4 14.4h11.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
  };

  function badgeFor(level) {
    const n = num(level);
    if (n < 1) return null;
    const list = (S.cat && S.cat.badges) || [
      { level: 1, id: "wave", name: "Small Wave", art: "wave" },
      { level: 2, id: "wave2", name: "Double Wave", art: "wave2" },
      { level: 3, id: "coral", name: "Coral Crest", art: "coral" },
      { level: 4, id: "shell", name: "Glowing Shell", art: "shell" },
      { level: 5, id: "current", name: "Golden Current", art: "current" },
      { level: 6, id: "pearl", name: "Deep Pearl", art: "pearl" },
      { level: 7, id: "trident", name: "Coral Trident", art: "trident" },
      { level: 8, id: "nautilus", name: "Spiral Nautilus", art: "nautilus" },
      { level: 9, id: "aurora", name: "Abyssal Aurora", art: "aurora" },
      { level: 10, id: "crown", name: "Ocean Crown", art: "crown" },
    ];
    let best = null;
    list.forEach((b) => { if (n >= b.level) best = b; });
    return best;
  }

  /** The Prestige badge as an HTML string. Compact by design: it must never
   *  cover the username, the avatar, or anything else on the row.
   *
   *  `opts.decorative` drops the focus stop and the label. Pass it whenever the
   *  badge sits inside an aria-hidden wrapper: a focusable element hidden from
   *  assistive tech is a real violation (the browser refuses to apply the
   *  aria-hidden and logs it), and in those spots the surrounding card already
   *  names the badge, so a second announcement would only repeat it. */
  function badgeHtml(level, opts) {
    const n = num(level);
    if (n < 1) return "";
    const o = opts || {};
    const b = badgeFor(n);
    if (!b) return "";
    const tier = n >= 10 ? " t10" : (n >= 5 ? " t5" : "");
    const art = BADGE_ART[b.art] || BADGE_ART.wave;
    const label = "Prestige " + n + (b.name ? ", " + b.name : "");
    return '<span class="cc-pbadge' + tier + (o.large ? " cc-pbadge-lg" : "") + '"'
      + (o.decorative ? "" : ' data-cc-prestige="' + n + '"')
      + (o.uid && !o.decorative ? ' data-cc-uid="' + esc(o.uid) + '"' : "")
      + (o.decorative
        ? ' aria-hidden="true"'
        : ' role="img" tabindex="0" aria-label="' + esc(label) + '" title="' + esc(label) + '"')
      + ">"
      + '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">' + art + '</svg>'
      + '<span aria-hidden="true">' + n + '</span></span>';
  }

  // ── Badge tooltip (hover / click / keyboard) ──────────────────────────────
  let _tip = null;
  function hideTip() { if (_tip) { _tip.remove(); _tip = null; } }
  function showTip(target) {
    hideTip();
    const lvl = num(target.getAttribute("data-cc-prestige"));
    if (lvl < 1) return;
    const uid = target.getAttribute("data-cc-uid") || "";
    const meta = (uid && _nameCache[uid]) || (uid && S.mine && S.mine.uid === uid ? S.mine : null);
    const b = badgeFor(lvl);
    const bonus = Math.round(lvl * 25);
    const title = (meta && meta.title) || titleFor(lvl);
    const when = meta && meta.last_prestige_at
      ? new Date(meta.last_prestige_at * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
      : "";
    _tip = el("div", "cc-pbadge-tip");
    // Public information only — level, title, XP bonus, date. Never coins,
    // never history, never anything else off the account.
    _tip.innerHTML = "<b>Prestige " + lvl + "</b>"
      + '<div class="r">' + esc(title) + "</div>"
      + '<div class="r">' + esc(b ? b.name : "") + " badge</div>"
      + '<div class="r">+' + bonus + "% XP from every source</div>"
      + (when ? '<div class="r">Last prestiged ' + esc(when) + "</div>" : "");
    document.body.appendChild(_tip);
    const r = target.getBoundingClientRect();
    const t = _tip.getBoundingClientRect();
    let left = Math.min(Math.max(8, r.left + r.width / 2 - t.width / 2), window.innerWidth - t.width - 8);
    let top = r.top - t.height - 9;
    if (top < 8) top = r.bottom + 9;
    _tip.style.left = left + "px";
    _tip.style.top = top + "px";
  }
  document.addEventListener("mouseover", (e) => {
    const t = e.target && e.target.closest && e.target.closest(".cc-pbadge");
    if (t) showTip(t);
  });
  document.addEventListener("mouseout", (e) => {
    if (e.target && e.target.closest && e.target.closest(".cc-pbadge")) hideTip();
  });
  document.addEventListener("focusin", (e) => {
    const t = e.target && e.target.closest && e.target.closest(".cc-pbadge");
    if (t) showTip(t);
  });
  document.addEventListener("focusout", hideTip);
  document.addEventListener("click", (e) => {
    const t = e.target && e.target.closest && e.target.closest(".cc-pbadge");
    if (t) { e.stopPropagation(); showTip(t); } else hideTip();
  });
  window.addEventListener("scroll", hideTip, true);

  function titleFor(level) {
    const list = (S.cat && S.cat.titles) || ["Tide Rider", "Current Chaser", "Reef Wanderer",
      "Deep Diver", "Abyss Walker", "Storm Caller", "Leviathan", "Tide Sovereign",
      "Ocean Sage", "Eternal Current"];
    const n = num(level);
    if (n < 1) return "Ocean Explorer";
    return n <= list.length ? list[n - 1] : ("Eternal Current " + (n - list.length + 1));
  }

  // ══════════════════════════════════════════════════════════════════════
  //  USERNAME APPEARANCE — one renderer, used everywhere a name is drawn
  // ══════════════════════════════════════════════════════════════════════
  function hexToRgb(h) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(h || "").trim());
    if (!m) return null;
    const v = m[1];
    return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
  }
  function relLum(rgb) {
    const f = (c) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
  }
  function contrast(a, b) {
    const la = relLum(a), lb = relLum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  }
  const SURFACE_LIGHT = [0xf4, 0xfb, 0xff];
  const SURFACE_DARK = [0x0c, 0x2a, 0x44];
  const PLATE_LIGHT = [0xff, 0xff, 0xff];
  const PLATE_DARK = [0x04, 0x16, 0x28];

  /** Which readability plate a colour needs.
   *
   *  ⚠️ Polarity comes from the COLOUR, never from the surface. Choosing it by
   *  surface is the obvious-looking version and it is backwards: a pale yellow
   *  on the light Player Home would be given the WHITE plate, which makes an
   *  already-faint name fainter. A light colour needs a dark plate and a dark
   *  colour needs a light one — that is what makes every accepted colour land
   *  at ≥ 4.25:1 (see best_plated_contrast in prestige_server.py). */
  function plateFor(rgb) {
    return contrast(rgb, PLATE_DARK) >= contrast(rgb, PLATE_LIGHT)
      ? "plate plate-dark"
      : "plate";
  }

  /** CSS for a name appearance, plus whether it needs a readability plate on
   *  the surface it is being drawn on. `surface` is "light" | "dark" | "auto". */
  function nameStyle(meta, surface) {
    const name = (meta && meta.name) || {};
    const mode = String(name.mode || "default");
    const out = { css: "", cls: "", fx: "", plate: "" };
    if (mode === "default") return out;

    const wantDark = surface === "dark";
    const surf = wantDark ? SURFACE_DARK : SURFACE_LIGHT;
    const animate = name.animate !== false && !S.still;
    const fx = animate ? String(name.effect || "none") : "none";
    if (fx && fx !== "none") out.fx = fx;

    if (mode === "solid" || mode === "custom") {
      const hex = String(name.color || "");
      const rgb = hexToRgb(hex);
      if (!rgb) return out;
      out.css = "color:" + hex + ";";
      // Not communicating by colour alone AND not making it unreadable: when
      // the chosen colour is low-contrast on THIS surface, seat it on a plate
      // rather than leaving a name nobody can read.
      if (surface !== "auto" && contrast(rgb, surf) < 3.0) out.plate = plateFor(rgb);
      return out;
    }
    if (mode === "gradient") {
      const from = String(name.from || "#1f7ae0");
      const to = String(name.to || "#12a37c");
      const mid = String(name.mid || "");
      const dir = String(name.dir || "h");
      const style = String(name.style || "smooth");
      const angle = dir === "v" ? "180deg" : (dir === "d" ? "135deg" : "90deg");
      let stops;
      if (style === "split") stops = mid ? from + " 0 33%," + mid + " 33% 66%," + to + " 66% 100%" : from + " 0 50%," + to + " 50% 100%";
      else if (style === "center") stops = mid ? from + "," + mid + " 50%," + to : from + "," + to + " 50%," + from;
      else if (style === "edges") stops = mid ? to + "," + mid + " 20%," + from + " 50%," + mid + " 80%," + to : to + "," + from + " 50%," + to;
      else stops = mid ? from + "," + mid + "," + to : from + "," + to;
      out.css = "background-image:linear-gradient(" + angle + "," + stops + ");";
      out.cls = "grad";
      const a = hexToRgb(from), b = hexToRgb(to);
      if (surface !== "auto" && a && b
          && contrast(a, surf) < 3.0 && contrast(b, surf) < 3.0) {
        // Both stops are struggling, so polarity is decided by their average.
        out.plate = plateFor([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2]);
      }
      return out;
    }
    return out;
  }

  /** A username as HTML: the Prestige colour, plus the badge when they have
   *  one. `opts.surface` "light" | "dark"; `opts.badge` false to omit it. */
  function nameHtml(nickname, meta, opts) {
    const o = opts || {};
    const safe = esc(nickname == null ? "" : nickname);
    if (!meta) return safe;
    const st = nameStyle(meta, o.surface || "light");
    const cls = ["cc-pname", st.cls, st.plate].filter(Boolean).join(" ");
    const span = st.css || st.cls || st.plate || st.fx
      ? '<span class="' + cls + '"' + (st.css ? ' style="' + st.css + '"' : "")
        + (st.fx ? ' data-fx="' + esc(st.fx) + '"' : "") + ">" + safe + "</span>"
      : safe;
    const badge = (o.badge === false) ? "" : badgeHtml(meta.level, { uid: meta.uid || o.uid });
    return badge ? span + " " + badge : span;
  }

  /** Same, applied to a live element. The element keeps its own text; only the
   *  colour/effect classes and an appended badge are added, so this is safe to
   *  call on any existing name node in the app. */
  function decorate(node, meta, opts) {
    if (!node || !meta) return;
    const o = opts || {};
    const st = nameStyle(meta, o.surface || "light");
    node.classList.add("cc-pname");
    node.classList.toggle("grad", st.cls === "grad");
    node.classList.remove("plate", "plate-dark");
    if (st.plate) st.plate.split(" ").forEach((c) => node.classList.add(c));
    node.style.cssText = (node.getAttribute("data-cc-basestyle") || "") + st.css;
    if (st.fx) node.setAttribute("data-fx", st.fx); else node.removeAttribute("data-fx");
    if (o.badge !== false && num(meta.level) > 0 && !node.querySelector(".cc-pbadge")
        && !(node.nextElementSibling && node.nextElementSibling.classList
             && node.nextElementSibling.classList.contains("cc-pbadge"))) {
      const holder = el("span");
      holder.innerHTML = " " + badgeHtml(meta.level, { uid: meta.uid || o.uid });
      const badge = holder.querySelector(".cc-pbadge");
      if (badge && node.parentNode) node.parentNode.insertBefore(badge, node.nextSibling);
    }
  }

  // ── Public lookup (badges + colours on OTHER players) ─────────────────────
  const _nameCache = Object.create(null);   // uid → meta
  const _byNameCache = Object.create(null); // nickname.toLowerCase() → meta
  const _nameMiss = Object.create(null);
  const _byNameMiss = Object.create(null);
  let _lookupQueue = [];
  let _byNameQueue = [];
  let _lookupTimer = null;

  /** Same as lookup(), but keyed on the DISPLAY NAME — which is all an in-game
   *  seat, the end-game summary or a tournament bracket ever has. */
  function lookupByName(names) {
    const want = (Array.isArray(names) ? names : [names])
      .map((n) => String(n || "").trim().toLowerCase()).filter(Boolean);
    const need = want.filter((n) => !(n in _byNameCache) && !_byNameMiss[n]);
    const done = () => {
      const out = {};
      want.forEach((n) => { if (_byNameCache[n]) out[n] = _byNameCache[n]; });
      return out;
    };
    if (!need.length) return Promise.resolve(done());
    need.forEach((n) => { if (_byNameQueue.indexOf(n) < 0) _byNameQueue.push(n); });
    return new Promise((resolve) => {
      setTimeout(async () => {
        const batch = _byNameQueue.splice(0, 60);
        if (!batch.length) return resolve(done());
        const res = await post("names", { names: batch });
        if (res && res.ok) {
          const map = res.by_name || {};
          batch.forEach((n) => {
            if (map[n]) _byNameCache[n] = map[n];
            else _byNameMiss[n] = 1;
          });
        } else {
          // Retryable — never poison the cache on a failed request.
          batch.forEach((n) => { delete _byNameMiss[n]; });
        }
        resolve(done());
      }, 80);
    });
  }

  /** Batch-resolve uids → public prestige appearance. Resolves to a map; uids
   *  with nothing to show are simply absent (a Prestige-0 player has no badge
   *  and no colour, so there is nothing to draw). */
  function lookup(uids) {
    const want = (Array.isArray(uids) ? uids : [uids]).map(String).filter(Boolean);
    const need = want.filter((u) => !(u in _nameCache) && !_nameMiss[u]);
    const done = () => {
      const out = {};
      want.forEach((u) => { if (_nameCache[u]) out[u] = _nameCache[u]; });
      return out;
    };
    if (!need.length) return Promise.resolve(done());
    need.forEach((u) => { if (_lookupQueue.indexOf(u) < 0) _lookupQueue.push(u); });
    return new Promise((resolve) => {
      clearTimeout(_lookupTimer);
      _lookupTimer = setTimeout(async () => {
        const batch = _lookupQueue.splice(0, 120);
        if (!batch.length) return resolve(done());
        const res = await post("names", { uids: batch });
        if (res && res.ok && res.players) {
          batch.forEach((u) => {
            if (res.players[u]) { _nameCache[u] = Object.assign({ uid: u }, res.players[u]); }
            else { _nameMiss[u] = 1; }
          });
        } else {
          // A failed lookup must be retryable — do NOT poison the cache.
          batch.forEach((u) => { delete _nameMiss[u]; });
        }
        resolve(done());
      }, 60);
    });
  }

  /** My own public appearance, cached. Everything that draws MY name (the
   *  profile header, my seat in game, my leaderboard row) reads this. */
  async function mine(force) {
    if (S.mine && !force) return S.mine;
    const b = bridge();
    const uid = (b && b.authUser && b.authUser() && b.authUser().uid) || "";
    if (!uid) return null;
    const res = await post("state", {});
    if (!res || !res.ok) return S.mine;
    S.state = res;
    S.mine = {
      uid,
      nickname: res.nickname || "",
      level: num(res.prestige && res.prestige.level),
      title: (res.prestige && res.prestige.title) || "",
      last_prestige_at: num(res.prestige && res.prestige.last_prestige_at),
      xp_bonus_pct: num(res.prestige && res.prestige.xp_bonus_pct),
      name: appearanceToName(res.prestige && res.prestige.appearance),
    };
    _nameCache[uid] = S.mine;
    return S.mine;
  }
  function appearanceToName(app) {
    const a = app || {};
    return {
      mode: a.mode || "default", color: a.color || "", colorId: a.colorId || "",
      gradientId: a.gradientId || "", from: a.from || "", to: a.to || "", mid: a.mid || "",
      dir: a.dir || "h", style: a.style || "smooth",
      effect: a.effect || "none", animate: a.animate !== false,
    };
  }

  /** base XP → { base, bonus, total } at MY prestige level. The end-game and
   *  challenge screens show this breakdown so the bonus is never invisible. */
  function xpBreakdown(baseXp) {
    const base = Math.max(0, Math.floor(num(baseXp)));
    const lvl = num(S.mine && S.mine.level);
    const total = Math.floor(base * (1 + lvl * 0.25));
    return { base, bonus: total - base, total, level: lvl };
  }

  /** Card name → the alternate skin style this account owns for it (or ""),
   *  so the game can tint the player's own card art. Cosmetic only. */
  function skinFor(cardName) {
    const app = S.state && S.state.prestige && S.state.prestige.appearance;
    if (app && app.skins_off) return "";
    const skins = (S.state && S.state.prestige && S.state.prestige.skins) || [];
    if (!skins.length) return "";
    const want = slug(cardName);
    for (let i = skins.length - 1; i >= 0; i--) {
      if (skins[i] && slug(skins[i].animal) === want) return String(skins[i].style || "");
    }
    return "";
  }
  function slug(s) {
    return String(s || "").toLowerCase().replace(/['’]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  }

  // ══════════════════════════════════════════════════════════════════════
  //  DATA
  // ══════════════════════════════════════════════════════════════════════
  async function loadCatalog() {
    if (S.cat) return S.cat;
    const b = bridge();
    if (!b) return null;
    try {
      const res = unwrap(await b.get("/api/prestige/catalog"));
      if (res && res.ok && res.catalog) S.cat = res.catalog;
    } catch (_) {}
    return S.cat;
  }

  async function loadState() {
    const res = await post("state", {});
    if (res && res.ok) {
      S.state = res;
      const b = bridge();
      const uid = (b && b.authUser && b.authUser() && b.authUser().uid) || "";
      if (uid) {
        S.mine = {
          uid, nickname: res.nickname || "",
          level: num(res.prestige.level), title: res.prestige.title || "",
          last_prestige_at: num(res.prestige.last_prestige_at),
          xp_bonus_pct: num(res.prestige.xp_bonus_pct),
          name: appearanceToName(res.prestige.appearance),
        };
        _nameCache[uid] = S.mine;
      }
    }
    return res;
  }

  // The client's avatar catalogue, for names / art / "how you earned it".
  function animalMap() {
    try {
      const list = bridge().animalAvatars() || [];
      const map = Object.create(null);
      list.forEach((a) => { map[String(a.img || "").toLowerCase()] = a; });
      return map;
    } catch (_) { return Object.create(null); }
  }

  // ══════════════════════════════════════════════════════════════════════
  //  RENDER — root + shell
  // ══════════════════════════════════════════════════════════════════════
  function root() { return $("#cc-prestige-root"); }

  function mountScene(scene) {
    const r = root();
    if (!r) return;
    const old = r.querySelector(":scope > .ccP-scene");
    if (old && old.getAttribute("data-scene") === scene) return;
    if (old) old.remove();
    r.insertBefore(buildScene(scene, { dense: true }), r.firstChild);
  }

  /** The scene the page wears: the player's equipped Prestige background if
   *  they have one, otherwise the one they are about to unlock. */
  function activeScene() {
    const p = (S.state && S.state.prestige) || {};
    const cat = S.cat || {};
    const byId = {};
    (cat.backgrounds || []).forEach((b) => { byId[b.id] = b; });
    const equipped = (p.appearance && p.appearance.background) || "";
    if (equipped) {
      const base = equipped.replace(/-\d+$/, "");
      if (byId[base]) return byId[base].scene;
    }
    const nxt = S.state && S.state.next && S.state.next.background;
    if (nxt && nxt.scene) return nxt.scene;
    return "shallows";
  }

  async function render() {
    const r = root();
    if (!r) return;
    const b = bridge();
    if (!b) {
      r.innerHTML = '<div class="ccP-locked"><div class="big">Prestige couldn\'t be drawn</div>'
        + '<div class="sm">The Prestige module didn\'t finish loading. Please refresh the page.</div></div>';
      return;
    }
    S.lite = detectLite();
    S.still = readStill();
    r.classList.toggle("ccP-lite", S.lite);
    r.classList.toggle("ccP-still", S.still);
    document.body.classList.toggle("ccP-still-names", S.still);

    const seq = ++S.seq;
    if (!r.querySelector(".ccP-page")) {
      r.appendChild(el("div", "ccP-page", '<div class="ccP-hero"><div class="ccP-hero-main">'
        + '<h2 class="ccP-title">Prestige</h2>'
        + '<div class="ccP-sub">Reading the current…</div></div></div>'));
    }
    mountScene("shallows");

    await loadCatalog();
    const res = await loadState();
    if (seq !== S.seq) return;   // navigated away while we were fetching

    if (!res) { paintError("Prestige couldn't be reached. Check your connection and try again."); return; }
    if (!res.ok) { paintError(errMsg(res.error)); return; }

    // Selections that are no longer valid (the account changed in another tab)
    // are dropped rather than silently submitted.
    const eligible = new Set((res.avatars && res.avatars.eligible) || []);
    S.keep = S.keep.filter((p) => eligible.has(p));

    mountScene(activeScene());
    paint();
  }

  function paintError(msg) {
    const r = root();
    if (!r) return;
    const page = r.querySelector(".ccP-page") || r.appendChild(el("div", "ccP-page"));
    page.innerHTML = '<div class="ccP-body"><div class="ccP-panel">'
      + '<div class="ccP-panel-h">Prestige</div>'
      + '<div class="ccP-err">' + esc(msg) + "</div>"
      + '<button class="ccP-btn pri" data-act="retry">Try again</button></div></div>';
    const btn = page.querySelector('[data-act="retry"]');
    if (btn) btn.addEventListener("click", render);
  }

  function paint() {
    const r = root();
    if (!r) return;
    let page = r.querySelector(".ccP-page");
    if (!page) { page = el("div", "ccP-page"); r.appendChild(page); }
    const st = S.state;
    const can = !!st.can_prestige;

    page.innerHTML = heroHtml() + progressHtml()
      + (can ? stepsHtml() : "")
      + '<div class="ccP-body" id="ccP-body"></div>'
      + (can ? "" : lockedHtml())
      + historyHtml()
      + footerHtml();

    const body = $("#ccP-body", page);
    if (can && body) body.innerHTML = stepBodyHtml();
    else if (body) body.innerHTML = previewRewardsHtml();

    wire(page);
  }

  // ── Hero ─────────────────────────────────────────────────────────────────
  function heroHtml() {
    const st = S.state;
    const p = st.prestige;
    const lvl = num(p.level);
    return '<div class="ccP-hero">'
      + (lvl > 0 ? '<div class="ccP-hero-badge">' + badgeHtml(lvl, { large: true }) + "</div>" : "")
      + '<div class="ccP-hero-main">'
      + '<h2 class="ccP-title">' + (lvl > 0 ? "Prestige " + lvl : "Prestige") + "</h2>"
      + '<div class="ccP-sub">'
      + (lvl > 0
        ? esc(p.title) + " · every current you ride makes the next one stronger."
        : "Reach Level " + fmt(st.max_level) + " and ride the next current: back to Level 1, "
          + "with permanent rewards that never reset.")
      + "</div>"
      + '<div class="ccP-hero-stats">'
      + stat(fmt(st.level), "Account level")
      + stat("+" + num(p.xp_bonus_pct) + "%", "XP bonus")
      + stat("+" + num(p.store_bonus_pct) + "%", "Store bonus")
      + stat(fmt(st.coins), "Critter Coins")
      + "</div></div></div>";
  }
  function stat(v, label) {
    return '<div class="ccP-stat"><b>' + esc(v) + "</b><span>" + esc(label) + "</span></div>";
  }

  function progressHtml() {
    const st = S.state;
    const lvl = num(st.level), max = num(st.max_level);
    const pct = Math.max(0, Math.min(100, Math.round((lvl / Math.max(1, max)) * 100)));
    const left = num(st.xp_to_max);
    return '<div class="ccP-progress">'
      + '<div class="ccP-bar" role="progressbar" aria-valuemin="1" aria-valuemax="' + max + '"'
      + ' aria-valuenow="' + lvl + '" aria-label="Progress to the level cap">'
      + '<i style="width:' + pct + '%"></i></div>'
      + '<div class="ccP-bar-text"><span>Level <b>' + fmt(lvl) + "</b></span>"
      + "<span>" + (left > 0 ? "<b>" + fmt(left) + "</b> XP to Level " + fmt(max) : "Level cap reached") + "</span>"
      + "</div></div>";
  }

  // ── Locked (not at the cap yet) ──────────────────────────────────────────
  function lockedHtml() {
    const st = S.state;
    return '<div class="ccP-body"><div class="ccP-locked">'
      + '<div class="big">🔒 Prestige unlocks at Level ' + fmt(st.max_level) + "</div>"
      + '<div class="sm">You\'re Level ' + fmt(st.level) + " with <b>" + fmt(st.xp_to_max)
      + " XP</b> to go. Everything below is waiting for you — nothing here can be bought or skipped.</div>"
      + '<button class="ccP-ride" disabled aria-disabled="true" style="margin-top:16px">Ride the Next Current</button>'
      + "</div></div>";
  }

  function previewRewardsHtml() {
    const nxt = S.state.next;
    if (!nxt) return "";
    return '<div class="ccP-panel"><div class="ccP-panel-h">🎁 What Prestige ' + num(nxt.prestige) + " gives you</div>"
      + '<div class="ccP-panel-sub">Every one of these is permanent. None of it ever resets, '
      + "including on the Prestige after this one.</div>"
      + rewardCardsHtml(nxt) + "</div>";
  }

  // ── Steps ────────────────────────────────────────────────────────────────
  function stepsHtml() {
    const reach = maxReachableStep();
    return '<div class="ccP-steps" role="tablist" aria-label="Prestige steps">'
      + STEPS.map((s, i) => {
        const cur = i === S.step;
        const done = i < S.step && stepComplete(i);
        return '<button class="ccP-step' + (done ? " done" : "") + '" role="tab" data-step="' + i + '"'
          + (cur ? ' aria-current="step" aria-selected="true"' : ' aria-selected="false"')
          + (i > reach ? " disabled" : "")
          + '><span class="n" aria-hidden="true">' + (done ? "✓" : (i + 1)) + "</span>"
          + esc(s.label) + "</button>";
      }).join("") + "</div>";
  }
  // How many critters this Prestige needs the player to keep. Normally two —
  // but the SERVER lowers it when the account has fewer relockable critters
  // than that, and the wizard has to agree or it would demand a selection that
  // cannot be made (see keep_quota in prestige_server.py).
  function keepQuota() {
    const st = S.state;
    if (st && Number.isFinite(Number(st.keep_quota))) return Math.max(0, Number(st.keep_quota));
    const elig = ((st && st.avatars) || {}).eligible || [];
    return Math.min(num((st && st.next && st.next.keep_avatars) || 2), elig.length);
  }

  function stepComplete(i) {
    if (i === 1) return S.keep.length === keepQuota();
    if (i === 2) return !!S.skin;
    if (i === 3) return !colorChoiceNeeded() || !!S.colorPick;
    return true;
  }
  function maxReachableStep() {
    // You may always walk BACK, and forward only as far as your choices allow.
    for (let i = 0; i < STEPS.length - 1; i++) if (!stepComplete(i)) return i;
    return STEPS.length - 1;
  }
  function colorChoiceNeeded() {
    const nxt = S.state && S.state.next;
    return !!(nxt && Array.isArray(nxt.color_choice) && nxt.color_choice.length);
  }

  function stepBodyHtml() {
    switch (STEPS[S.step].id) {
      case "rewards": return stepRewards();
      case "avatars": return stepAvatars();
      case "skin": return stepSkin();
      case "color": return stepColor();
      case "review": return stepReview();
      case "confirm": return stepConfirm();
      default: return "";
    }
  }

  // ── Step 1: rewards ──────────────────────────────────────────────────────
  function rewardCardsHtml(nxt) {
    const p = S.state.prestige;
    const bg = nxt.background || {};
    const badge = nxt.badge || {};
    const colors = colorNames(nxt.color_choice && nxt.color_choice.length ? nxt.color_choice : nxt.colors);
    const extras = [];
    if (nxt.custom_color) extras.push("a custom solid-colour creator");
    if (nxt.custom_gradient) extras.push("username gradients");
    if (nxt.three_color) extras.push("three-colour gradients");
    (nxt.effects || []).forEach((id) => {
      const e = ((S.cat && S.cat.effects) || []).find((x) => x.id === id);
      if (e) extras.push(e.name.toLowerCase());
    });

    const card = (ico, lbl, val, cls, desc, from) =>
      '<div class="ccP-rw"><div class="ccP-rw-ico" aria-hidden="true">' + ico + "</div>"
      + '<div class="ccP-rw-lbl">' + esc(lbl) + "</div>"
      + '<div class="ccP-rw-val ' + (cls || "") + '">' + val + "</div>"
      + '<div class="ccP-rw-desc">' + desc + "</div>"
      + (from ? '<div class="ccP-rw-from">' + from + "</div>" : "") + "</div>";

    const coin = '<img src="/critter-coin.png?v=1" alt="">';
    return '<div class="ccP-rewards">'
      + card(coin, "Critter Coins", fmt(nxt.coins), "gold",
        "Paid into your wallet the moment the Prestige completes, with a transaction record.",
        "Balance now <b>" + fmt(S.state.coins) + "</b> → <b>" + fmt(num(S.state.coins) + num(nxt.coins)) + "</b>")
      + card("⭐", "Permanent XP bonus", "+" + num(nxt.xp_bonus_pct) + "%", "cyan",
        "Applies to casual, competitive, AI, daily, weekly, monthly, events, tournaments, clan challenges and login rewards.",
        "Now <b>+" + num(p.xp_bonus_pct) + "%</b> → <b>+" + num(nxt.xp_bonus_pct) + "%</b>")
      + card("🛒", "Critter Coin store bonus", "+" + num(nxt.store_bonus_pct) + "%", "",
        "Extra coins on every Critter Coin package you buy. The price never changes.",
        "Now <b>+" + num(p.store_bonus_pct) + "%</b> → <b>+" + num(nxt.store_bonus_pct) + "%</b>")
      + card("🌊", "Prestige background", esc(bg.name || "—"), "",
        esc(bg.blurb || "") + " A living scene — currents, light, bubbles and critters — yours forever.",
        "Prestige " + num(nxt.prestige) + " background")
      + card("🎨", "Alternate animal skin", "1 animal", "",
        "Pick any animal in the game and unlock an exclusive skin for it. Appearance only — it changes nothing about how the card plays.",
        (nxt.skin_styles || []).length + " styles available to you")
      + card("🏷️", "Name colour", colors || "New options", "",
        colors ? "Wear it anywhere your name appears." : "New username customisation unlocks.",
        extras.length ? "Also unlocks " + esc(extras.join(", ")) : "")
      + card(badgeHtml(num(nxt.prestige), { large: true, decorative: true }) || "🏅", "Prestige badge", esc(badge.name || "—"), "",
        "Shown beside your username across the whole game.", "")
      + "</div>";
  }
  function colorNames(ids) {
    const list = (S.cat && S.cat.colors) || [];
    return (ids || []).map((id) => {
      const c = list.find((x) => x.id === id);
      return c ? c.name : id;
    }).join(" or ");
  }

  function stepRewards() {
    const nxt = S.state.next;
    if (!nxt) return '<div class="ccP-panel"><div class="ccP-ok">You have reached the highest Prestige there is.</div></div>';
    return '<div class="ccP-panel">'
      + '<div class="ccP-panel-h">🎁 Prestige ' + num(nxt.prestige) + " rewards</div>"
      + '<div class="ccP-panel-sub">Exact values, not estimates. Every one is permanent and stacks with what you already have.</div>'
      + rewardCardsHtml(nxt) + "</div>" + resetKeepHtml();
  }

  // ── Step 2: keep two avatars ─────────────────────────────────────────────
  function stepAvatars() {
    const av = S.state.avatars || { eligible: [], automatic: [] };
    const map = animalMap();
    const need = keepQuota();
    const info = (path) => map[String(path).toLowerCase()] || { name: path, img: path, unlockLabel: "" };

    const tile = (path, kind) => {
      const a = info(path);
      const chosen = S.keep.indexOf(path) >= 0;
      const auto = kind === "auto";
      const full = S.keep.length >= need && !chosen;
      return '<button class="ccP-tile" type="button" data-keep="' + esc(path) + '"'
        + ' aria-pressed="' + (chosen ? "true" : "false") + '"'
        + (auto ? " disabled" : (full ? " disabled" : ""))
        + ' aria-label="' + esc(a.name) + (auto ? ", stays automatically" : (chosen ? ", selected to keep" : ", select to keep")) + '">'
        + (chosen ? '<span class="pick" aria-hidden="true">✓</span>' : "")
        + '<span class="tag ' + (auto ? "keep" : "") + '">' + (auto ? "Stays" : "Relocks") + "</span>"
        + '<img class="av" src="' + esc(avSrc(a.img || path)) + '" alt="" loading="lazy">'
        + '<div class="nm">' + esc(a.name) + "</div>"
        + '<div class="req">' + esc(a.unlockLabel || a.species || "") + "</div>"
        + "</button>";
    };

    const counterCls = S.keep.length === need ? "" : " warn";
    return '<div class="ccP-panel">'
      + '<div class="ccP-panel-h">🐟 ' + (need === 1 ? "Keep one critter" : "Keep two critters") + "</div>"
      + '<div class="ccP-panel-sub">'
      + (need === 0
        ? "Nothing you own would relock, so there's nothing to choose here — everything you have stays."
        : (need === 1
          ? "You only have one critter that would relock, so keeping it is the whole choice."
          : "These two stay unlocked through the reset. Everything else you earned by playing "
            + "relocks and has to be earned again — the same way you got it the first time."))
      + "</div>"
      + '<div class="ccP-toolbar"><div class="ccP-panel-sub" style="margin:0">'
      + fmt(av.eligible.length) + " critters would relock.</div>"
      + '<div class="ccP-counter' + counterCls + '" role="status" aria-live="polite">Avatars Selected: '
      + S.keep.length + " of " + need + "</div></div>"
      + (av.eligible.length
        ? '<div class="ccP-grid">' + av.eligible.map((p) => tile(p, "elig")).join("") + "</div>"
        : '<div class="ccP-ok">You have no critters that would relock — nothing to choose.</div>')
      + (S.keep.length ? '<div class="ccP-panel-sub" style="margin:12px 0 0">'
        + "On your profile they'll look like this:</div>" + profilePreviewHtml() : "")
      + "</div>"
      + '<div class="ccP-panel keep">'
      + '<div class="ccP-panel-h">🔒 Stays automatically (' + fmt(av.automatic.length) + ")</div>"
      + '<div class="ccP-panel-sub">Bought, donated, competitive-rank and previously-kept critters are never taken away, '
      + "and they don't use up one of your two picks.</div>"
      + (av.automatic.length
        ? '<div class="ccP-grid">' + av.automatic.map((p) => tile(p, "auto")).join("") + "</div>"
        : '<div class="ccP-panel-sub">None yet.</div>')
      + "</div>";
  }

  function profilePreviewHtml() {
    const map = animalMap();
    const nick = (bridge() && bridge().nickname && bridge().nickname()) || "You";
    const meta = previewMeta();
    return '<div class="ccP-preview-strip">' + S.keep.map((p) => {
      const a = map[String(p).toLowerCase()] || { name: p, img: p };
      return '<div class="ccP-pv light"><img src="' + esc(avSrc(a.img)) + '" alt="">'
        + "<div><span class=\"where\">Profile</span>" + nameHtml(nick, meta, { surface: "light" }) + "</div></div>";
    }).join("") + "</div>";
  }

  // ── Step 3: alternate animal skin ────────────────────────────────────────
  function stepSkin() {
    const cat = S.cat || {};
    const nxt = S.state.next || {};
    const allowed = new Set(nxt.skin_styles || []);
    const owned = new Set((S.state.owned_skins || []).map((s) => s.animal + ":" + s.style));
    const animals = (cat.skin_animals || []);
    const families = [...new Set(animals.map((a) => a.family))].sort();
    const q = S.skinQuery.trim().toLowerCase();
    const list = animals.filter((a) =>
      (!q || a.name.toLowerCase().indexOf(q) >= 0)
      && (!S.skinFamily || a.family === S.skinFamily));

    const styleBtns = (cat.skin_styles || []).map((s) => {
      const lock = !allowed.has(s.id);
      const on = S.skinStyle === s.id;
      return '<button class="ccP-btn' + (on ? " pri" : "") + '" type="button" data-style="' + esc(s.id) + '"'
        + (lock ? " disabled" : "") + ' aria-pressed="' + (on ? "true" : "false") + '">'
        + esc(s.name) + (lock ? " · P" + s.level : "") + "</button>";
    }).join("");

    const grid = list.map((a) => {
      const style = S.skinStyle || (nxt.skin_styles || [])[0] || "golden";
      const key = a.id + ":" + style;
      const has = owned.has(key);
      const on = S.skin && S.skin.animal === a.id && S.skin.style === style;
      return '<button class="ccP-tile" type="button" data-animal="' + esc(a.id) + '"'
        + ' aria-pressed="' + (on ? "true" : "false") + '"' + (has ? " disabled" : "")
        + ' aria-label="' + esc(a.name) + (has ? ", skin already owned" : ", choose this animal") + '">'
        + (on ? '<span class="pick" aria-hidden="true">✓</span>' : "")
        + (has ? '<span class="tag owned">Owned</span>' : "")
        + cardArtHtml(a.uid, style)
        + '<div class="nm">' + esc(a.name) + "</div>"
        + '<div class="req">' + esc(a.family) + "</div></button>";
    }).join("");

    const chosen = S.skin ? animals.find((a) => a.id === S.skin.animal) : null;
    return '<div class="ccP-panel">'
      + '<div class="ccP-panel-h">🎨 Choose an alternate animal skin</div>'
      + '<div class="ccP-panel-sub">Appearance only. A Prestige skin never changes an ability, star ability, cost, '
      + "point value, ocean requirement, card interaction, rarity or competitive strength. "
      + "Once confirmed the choice is permanent.</div>"
      + '<div class="ccP-toolbar">'
      + '<input class="ccP-input" id="ccP-skin-q" type="search" placeholder="Search animals…" '
      + 'value="' + esc(S.skinQuery) + '" aria-label="Search animals by name">'
      + '<select class="ccP-select" id="ccP-skin-fam" aria-label="Filter by family">'
      + '<option value="">All families</option>'
      + families.map((f) => '<option value="' + esc(f) + '"' + (S.skinFamily === f ? " selected" : "") + ">" + esc(f) + "</option>").join("")
      + "</select>"
      + '<div class="ccP-counter' + (S.skin ? "" : " warn") + '" role="status" aria-live="polite">'
      + (S.skin ? "Skin selected" : "No skin selected") + "</div></div>"
      + '<div class="ccP-panel-sub" style="margin:0 0 6px">Skin style</div>'
      + '<div class="ccP-toolbar" role="group" aria-label="Skin style">' + styleBtns + "</div>"
      + (chosen ? skinCompareHtml(chosen) : "")
      + '<div class="ccP-grid" style="margin-top:12px">' + (grid || '<div class="ccP-panel-sub">No animals match that search.</div>') + "</div>"
      + "</div>";
  }

  function cardArtHtml(uid, style) {
    let art = { url: "", pos: "center" };
    try { art = window.__ccCardImg(uid) || art; } catch (_) {}
    if (!art.url) return '<div class="ccP-cardprev"></div>';
    return '<div class="ccP-cardprev pos-' + esc(art.pos) + '"' + (style ? ' data-ccskin="' + esc(style) + '"' : "") + ">"
      + '<img src="' + esc(art.url) + '" alt="" loading="lazy"></div>';
  }

  function skinCompareHtml(a) {
    const style = S.skin ? S.skin.style : S.skinStyle;
    const styleName = (((S.cat && S.cat.skin_styles) || []).find((s) => s.id === style) || {}).name || style;
    return '<div class="ccP-panel" style="margin:12px 0 0;background:rgba(255,255,255,.72)">'
      + '<div class="ccP-panel-h" style="font-size:15px">' + esc(a.name) + " · " + esc(styleName) + "</div>"
      + '<div class="ccP-devicebar" role="group" aria-label="Preview layout">'
      + '<button class="ccP-btn' + (S.skinDevice === "desktop" ? " pri" : "") + '" type="button" data-dev="desktop">Computer</button>'
      + '<button class="ccP-btn' + (S.skinDevice === "mobile" ? " pri" : "") + '" type="button" data-dev="mobile">Mobile</button>'
      + "</div>"
      + '<div class="ccP-compare">'
      + '<figure class="ccP-frame ' + esc(S.skinDevice) + '">' + cardArtHtml(a.uid, "") + "<figcaption>Normal</figcaption></figure>"
      + '<figure class="ccP-frame ' + esc(S.skinDevice) + '">' + cardArtHtml(a.uid, style) + "<figcaption>" + esc(styleName) + "</figcaption></figure>"
      + "</div></div>";
  }

  // ── Step 4: name colour ──────────────────────────────────────────────────
  function stepColor() {
    const nxt = S.state.next || {};
    const cat = S.cat || {};
    const choices = nxt.color_choice || [];
    const autoColors = nxt.colors || [];
    const meta = previewMeta();
    const nick = (bridge() && bridge().nickname && bridge().nickname()) || "You";

    let picker = "";
    if (choices.length) {
      picker = '<div class="ccP-panel-sub">Pick one now. <b>The one you don\'t choose is unlocked by your next '
        + "Prestige</b>, so nothing is lost either way.</div>"
        + '<div class="ccP-swatches">' + choices.map((id) => {
          const c = (cat.colors || []).find((x) => x.id === id) || { id, name: id, hex: "#1f7ae0" };
          const on = S.colorPick === id;
          return '<button class="ccP-swatch" type="button" data-color="' + esc(id) + '"'
            + ' aria-pressed="' + (on ? "true" : "false") + '">'
            + '<span class="dot" style="background:' + esc(c.hex) + '"></span>'
            + '<span><span class="lbl">' + esc(c.name) + "</span>"
            + '<span class="sub">' + (on ? "Selected" : "Tap to choose") + "</span></span></button>";
        }).join("") + "</div>";
    } else if (autoColors.length) {
      picker = '<div class="ccP-ok">Prestige ' + num(nxt.prestige) + " unlocks <b>"
        + esc(colorNames(autoColors)) + "</b> automatically — no choice needed.</div>";
    } else {
      const bits = [];
      if (nxt.custom_color) bits.push("the custom solid-colour creator (colour wheel, sliders and a hex field)");
      if (nxt.custom_gradient) bits.push("username gradients, including four presets and your own colours");
      if (nxt.three_color) bits.push("three-colour gradients");
      (nxt.effects || []).forEach((id) => {
        const e = (cat.effects || []).find((x) => x.id === id);
        if (e) bits.push(e.name.toLowerCase());
      });
      picker = bits.length
        ? '<div class="ccP-ok">Prestige ' + num(nxt.prestige) + " unlocks " + esc(bits.join(", "))
          + ". You'll set it up in <b>Name Appearance</b> after the Prestige — no choice needed here.</div>"
        : '<div class="ccP-ok">No new name colour at this Prestige. Everything you have already unlocked stays.</div>';
    }

    return '<div class="ccP-panel">'
      + '<div class="ccP-panel-h">🏷️ Prestige name colour</div>'
      + picker
      + '<div class="ccP-panel-sub" style="margin-top:14px">How it looks, on light and dark surfaces:</div>'
      + previewStrip(nick, meta)
      + '<div class="ccP-panel-sub" style="margin-top:10px">Colours are added on top of your name, never instead of it — '
      + "a name that would be hard to read gets a subtle plate behind it automatically, and staff/system colours can't be used.</div>"
      + "</div>";
  }

  function previewMeta() {
    // What my name will look like AFTER this Prestige, using the pending pick.
    const p = (S.state && S.state.prestige) || {};
    const app = Object.assign({}, p.appearance || {});
    if (S.colorPick) {
      const c = ((S.cat && S.cat.colors) || []).find((x) => x.id === S.colorPick);
      if (c) { app.mode = "solid"; app.colorId = c.id; app.color = c.hex; }
    }
    return { level: num(p.level) + (S.state && S.state.can_prestige ? 1 : 0), name: appearanceToName(app),
             title: titleFor(num(p.level) + 1) };
  }

  function previewStrip(nick, meta) {
    const av = avSrc("/avatars/mullet.png");
    const row = (cls, where, surface) =>
      '<div class="ccP-pv ' + cls + '"><img src="' + esc(av) + '" alt="">'
      + '<div><span class="where">' + esc(where) + "</span>"
      + nameHtml(nick, meta, { surface }) + "</div></div>";
    return '<div class="ccP-preview-strip">'
      + row("light", "Player Home", "light")
      + row("dark", "In game", "dark")
      + row("lobby", "Game lobby", "light")
      + row("board", "Leaderboard", "dark")
      + "</div>";
  }

  // ── Step 5: reset review ─────────────────────────────────────────────────
  function resetKeepHtml() {
    const li = (ico, txt) => '<li><span class="ico" aria-hidden="true">' + ico + "</span><span>" + txt + "</span></li>";
    return '<div class="ccP-two">'
      + '<div class="ccP-panel reset"><div class="ccP-panel-h">↺ Resets</div>'
      + '<ul class="ccP-list">'
      + li("📉", "Your account level goes back to <b>Level 1</b>")
      + li("⭐", "Your XP goes back to <b>0</b>")
      + li("🔒", "Critters earned from levels, challenges, achievements, statistics and normal play <b>relock</b>")
      + li("🎯", "Progress toward those critters' unlocks starts over where it needs to")
      + li("🐟", "Everything except the <b>two critters you keep</b> has to be earned again")
      + "</ul></div>"
      + '<div class="ccP-panel keep"><div class="ccP-panel-h">🛡️ You keep</div>'
      + '<ul class="ccP-list">'
      + li("🏆", "Competitive rank and Competitive Points")
      + li("🛡️", "Clan membership, role, season points and clan stats")
      + li("👥", "Friends, messages and trade history")
      + li("📊", "Lifetime statistics, match history and completed achievements")
      + li("🪙", "Critter Coins — and this Prestige adds more")
      + li("💳", "Everything bought with coins or real money: avatars, backgrounds, cosmetics")
      + li("🎟️", "Limited-time, event and competitive-rank avatars")
      + li("🌊", "Every previous Prestige reward: badges, name colours, skins, backgrounds")
      + li("💛", "Supporter rewards, account settings and moderation records")
      + "</ul></div></div>";
  }

  function stepReview() {
    const nxt = S.state.next || {};
    const p = S.state.prestige;
    const map = animalMap();
    const cat = S.cat || {};
    const skinAnimal = S.skin ? (cat.skin_animals || []).find((a) => a.id === S.skin.animal) : null;
    const skinStyle = S.skin ? (cat.skin_styles || []).find((s) => s.id === S.skin.style) : null;
    const bg = nxt.background || {};
    const badge = nxt.badge || {};
    const kv = (k, v) => "<dt>" + esc(k) + "</dt><dd>" + v + "</dd>";
    return '<div class="ccP-panel">'
      + '<div class="ccP-panel-h">📋 Review everything</div>'
      + '<div class="ccP-panel-sub">This is exactly what happens when you confirm. Nothing else on your account is touched.</div>'
      + '<dl class="ccP-kv">'
      + kv("Current level", fmt(S.state.level) + " → <b>1</b>")
      + kv("Prestige", num(p.level) + " → <b>" + num(nxt.prestige) + "</b> (" + esc(titleFor(num(nxt.prestige))) + ")")
      + kv("Critter Coins", "+" + fmt(nxt.coins) + " (balance " + fmt(S.state.coins) + " → <b>" + fmt(num(S.state.coins) + num(nxt.coins)) + "</b>)")
      + kv("XP bonus", "+" + num(p.xp_bonus_pct) + "% → <b>+" + num(nxt.xp_bonus_pct) + "%</b>")
      + kv("Store bonus", "+" + num(p.store_bonus_pct) + "% → <b>+" + num(nxt.store_bonus_pct) + "%</b>")
      + kv("Critters kept", S.keep.length
        ? S.keep.map((x) => esc((map[String(x).toLowerCase()] || {}).name || x)).join(" · ")
        : (keepQuota() === 0
          ? "nothing of yours would relock"
          : '<span style="color:#ffd7dc">none selected</span>'))
      + kv("Animal skin", skinAnimal && skinStyle
        ? esc(skinStyle.name) + " " + esc(skinAnimal.name)
        : '<span style="color:#ffd7dc">none selected</span>')
      + kv("Name colour", S.colorPick
        ? esc(colorNames([S.colorPick]))
        : (colorChoiceNeeded() ? '<span style="color:#ffd7dc">none selected</span>'
          : (colorNames(nxt.colors) || "no new colour at this Prestige")))
      + kv("New background", esc(bg.name || "—"))
      + kv("New badge", esc(badge.name || "—") + " " + badgeHtml(num(nxt.prestige)))
      + kv("Critters relocking", fmt(Math.max(0, ((S.state.avatars || {}).eligible || []).length - S.keep.length)))
      + "</dl></div>" + resetKeepHtml();
  }

  // ── Step 6: confirm ──────────────────────────────────────────────────────
  function stepConfirm() {
    const phrase = (S.cat && S.cat.confirm_phrase) || "PRESTIGE";
    const ready = confirmReady();
    return '<div class="ccP-panel">'
      + '<div class="ccP-panel-h">🌊 Final confirmation</div>'
      + '<div class="ccP-warn">Prestiging will return your account to Level 1 and relock most critters earned through '
      + "gameplay. Your purchases, special rewards, statistics, competitive progress and permanent Prestige rewards will "
      + "remain.<br><br>This action cannot normally be undone.</div>"
      + '<label for="ccP-confirm" class="ccP-panel-sub" style="display:block;margin-bottom:6px">'
      + "Type <b>" + esc(phrase) + "</b> to unlock the button:</label>"
      + '<input class="ccP-input ccP-confirm-in" id="ccP-confirm" type="text" autocomplete="off" '
      + 'autocapitalize="characters" spellcheck="false" value="' + esc(S.confirmText) + '" '
      + 'aria-describedby="ccP-confirm-help" style="display:block;width:100%">'
      + '<div class="ccP-panel-sub" id="ccP-confirm-help" style="text-align:center;margin-top:8px" role="status" aria-live="polite">'
      + (ready ? "✓ Ready to ride the next current." : missingText()) + "</div>"
      + (S.error ? '<div class="ccP-err" role="alert">' + esc(S.error) + "</div>" : "")
      + "</div>";
  }
  function confirmReady() {
    const phrase = ((S.cat && S.cat.confirm_phrase) || "PRESTIGE");
    return !!S.state && !!S.state.can_prestige
      && S.keep.length === keepQuota() && !!S.skin
      && (!colorChoiceNeeded() || !!S.colorPick)
      && S.confirmText.trim().toUpperCase() === phrase;
  }
  function missingText() {
    const miss = [];
    if (!S.state.can_prestige) miss.push("reach Level " + fmt(S.state.max_level));
    const need = keepQuota();
    if (S.keep.length !== need) {
      miss.push(need === 0 ? "" : (need === 1 ? "choose one critter to keep" : "choose two critters to keep"));
    }
    if (!S.skin) miss.push("choose an animal skin");
    if (colorChoiceNeeded() && !S.colorPick) miss.push("choose a name colour");
    const phrase = (S.cat && S.cat.confirm_phrase) || "PRESTIGE";
    if (S.confirmText.trim().toUpperCase() !== phrase) miss.push("type " + phrase);
    const real = miss.filter(Boolean);
    return real.length ? "Still to do: " + real.join(", ") + "." : "";
  }

  // ── History + footer ─────────────────────────────────────────────────────
  function historyHtml() {
    const h = (S.state && S.state.history) || [];
    if (!h.length) return "";
    const map = animalMap();
    const rows = h.slice().reverse().map((e) => {
      const when = e.at ? new Date(e.at * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "—";
      const kept = (e.avatars_kept || []).map((p) => esc((map[String(p).toLowerCase()] || {}).name || p)).join(" · ");
      const skin = e.skin ? esc(String(e.skin.style)) + " " + esc(String(e.skin.animal).replace(/-/g, " ")) : "—";
      return '<div class="ccP-hist-row"><div class="ccP-hist-n">' + num(e.prestige) + "</div>"
        + '<div class="ccP-hist-b"><b>' + esc(e.title || titleFor(e.prestige)) + "</b> · " + esc(when)
        + "<br>Level " + fmt(e.level_before) + " before the reset · <b>+" + fmt(e.coins) + "</b> Critter Coins"
        + "<br>Kept: " + (kept || "—") + " · Skin: " + skin
        + "<br>Background: " + esc((e.background && e.background.name) || "—")
        + " · Badge: " + esc((e.badge && e.badge.name) || "—")
        + "<br>XP bonus after: <b>+" + Math.round(num(e.xp_multiplier) * 100 - 100) + "%</b>"
        + " · Store bonus after: <b>+" + num(e.store_bonus_pct) + "%</b></div></div>";
    }).join("");
    return '<div class="ccP-body"><div class="ccP-panel">'
      + '<div class="ccP-panel-h">📜 Prestige history</div>'
      + '<div class="ccP-panel-sub">Only you can see this.</div>'
      + '<div class="ccP-hist">' + rows + "</div></div></div>";
  }

  function footerHtml() {
    const st = S.state;
    const can = !!st.can_prestige;
    const last = S.step === STEPS.length - 1;
    let actions = "";
    if (can) {
      actions = '<div class="ccP-actions sticky">'
        + '<button class="ccP-btn ghost" type="button" data-nav="back"' + (S.step === 0 ? " disabled" : "") + ">← Back</button>"
        + '<div class="ccP-spacer"></div>'
        + (last ? "" : '<button class="ccP-btn pri" type="button" data-nav="next"'
          + (stepComplete(S.step) ? "" : " disabled") + ">Continue →</button>")
        + "</div>";
      if (last) {
        actions += '<div class="ccP-ride-wrap">'
          + '<button class="ccP-ride" type="button" data-act="commit"' + (confirmReady() ? "" : " disabled aria-disabled=\"true\"") + ">"
          + (S.busy ? "Riding the current…" : "Ride the Next Current") + "</button>"
          + '<div class="ccP-ride-note">Your account is only changed once the server confirms every check. '
          + "If anything goes wrong, nothing on your account changes at all.</div></div>";
      }
    }
    return actions
      + '<div class="ccP-actions" style="margin-top:14px">'
      + '<button class="ccP-btn ghost" type="button" data-act="appearance">🏷️ Name Appearance</button>'
      + '<div class="ccP-spacer"></div>'
      + '<button class="ccP-btn ghost" type="button" data-act="still" aria-pressed="' + (S.still ? "true" : "false") + '">'
      + (S.still ? "▶ Background motion: off" : "⏸ Reduce background motion") + "</button></div>";
  }

  // ══════════════════════════════════════════════════════════════════════
  //  WIRING
  // ══════════════════════════════════════════════════════════════════════
  function wire(page) {
    page.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => {
      const i = num(b.getAttribute("data-step"));
      if (i <= maxReachableStep()) { S.step = i; S.error = ""; paint(); }
    }));
    page.querySelectorAll('[data-nav="next"]').forEach((b) => b.addEventListener("click", () => {
      if (S.step < STEPS.length - 1 && stepComplete(S.step)) { S.step++; S.error = ""; paint(); scrollTop(); }
    }));
    page.querySelectorAll('[data-nav="back"]').forEach((b) => b.addEventListener("click", () => {
      if (S.step > 0) { S.step--; S.error = ""; paint(); scrollTop(); }
    }));

    page.querySelectorAll("[data-keep]").forEach((b) => b.addEventListener("click", () => {
      const p = b.getAttribute("data-keep");
      const i = S.keep.indexOf(p);
      const need = keepQuota();
      if (i >= 0) S.keep.splice(i, 1);
      else if (S.keep.length < need) S.keep.push(p);
      else { toast("You can keep " + need + " critters — tap one to swap it out.", "info"); return; }
      paint();
    }));

    const q = $("#ccP-skin-q", page);
    if (q) q.addEventListener("input", () => {
      S.skinQuery = q.value;
      const pos = q.selectionStart;
      paint();
      const q2 = $("#ccP-skin-q");
      if (q2) { q2.focus(); try { q2.setSelectionRange(pos, pos); } catch (_) {} }
    });
    const fam = $("#ccP-skin-fam", page);
    if (fam) fam.addEventListener("change", () => { S.skinFamily = fam.value; paint(); });
    page.querySelectorAll("[data-style]").forEach((b) => b.addEventListener("click", () => {
      S.skinStyle = b.getAttribute("data-style");
      if (S.skin) S.skin = { animal: S.skin.animal, style: S.skinStyle };
      paint();
    }));
    page.querySelectorAll("[data-dev]").forEach((b) => b.addEventListener("click", () => {
      S.skinDevice = b.getAttribute("data-dev"); paint();
    }));
    page.querySelectorAll("[data-animal]").forEach((b) => b.addEventListener("click", () => {
      const style = S.skinStyle || ((S.state.next || {}).skin_styles || [])[0] || "golden";
      const animal = b.getAttribute("data-animal");
      S.skin = (S.skin && S.skin.animal === animal && S.skin.style === style) ? null : { animal, style };
      S.skinStyle = style;
      paint();
    }));

    page.querySelectorAll("[data-color]").forEach((b) => b.addEventListener("click", () => {
      const id = b.getAttribute("data-color");
      S.colorPick = S.colorPick === id ? "" : id;
      paint();
    }));

    const conf = $("#ccP-confirm", page);
    if (conf) conf.addEventListener("input", () => {
      S.confirmText = conf.value;
      const pos = conf.selectionStart;
      paint();
      const c2 = $("#ccP-confirm");
      if (c2) { c2.focus(); try { c2.setSelectionRange(pos, pos); } catch (_) {} }
    });

    const commit = page.querySelector('[data-act="commit"]');
    if (commit) commit.addEventListener("click", doCommit);
    const app = page.querySelector('[data-act="appearance"]');
    if (app) app.addEventListener("click", () => openAppearance());
    const still = page.querySelector('[data-act="still"]');
    if (still) still.addEventListener("click", () => { setStill(!S.still); paint(); });
  }

  function scrollTop() {
    try {
      const r = root();
      if (r && r.scrollIntoView) r.scrollIntoView({ behavior: S.still ? "auto" : "smooth", block: "start" });
    } catch (_) {}
  }

  // ── Commit ───────────────────────────────────────────────────────────────
  let _idemKey = "";
  function idemKey() {
    // One key per attempt-set: a double-tap, a refresh mid-request or a second
    // device replays the SAME key, and the server answers with the same result
    // instead of prestiging twice. Cleared only after a success.
    if (!_idemKey) {
      _idemKey = "p" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
      try { sessionStorage.setItem("cc_prestige_idem", _idemKey); } catch (_) {}
    }
    return _idemKey;
  }
  try { _idemKey = sessionStorage.getItem("cc_prestige_idem") || ""; } catch (_) {}

  async function doCommit() {
    if (S.busy) return;
    if (!confirmReady()) { S.error = missingText(); paint(); return; }
    S.busy = true; S.error = ""; paint();
    const res = await post("commit", {
      confirm: (S.cat && S.cat.confirm_phrase) || "PRESTIGE",
      idempotency_key: idemKey(),
      keep_avatars: S.keep.slice(),
      skin: S.skin,
      name_color: S.colorPick ? { color: S.colorPick } : null,
    });
    S.busy = false;
    if (!res) { S.error = FAIL_MSG; paint(); return; }
    if (!res.ok) {
      // A rule the server refused is worth naming exactly; anything else gets
      // the one honest sentence about nothing having changed.
      S.error = ERR[res.error] ? errMsg(res.error) : FAIL_MSG;
      if (res.error === "already_prestiged" || res.error === "not_max_level") { await render(); }
      else paint();
      return;
    }
    _idemKey = "";
    try { sessionStorage.removeItem("cc_prestige_idem"); } catch (_) {}
    S.keep = []; S.skin = null; S.colorPick = ""; S.confirmText = ""; S.step = 0;
    try { bridge().onPrestiged && bridge().onPrestiged(res); } catch (_) {}
    celebrate(res);
    await render();
  }

  // ══════════════════════════════════════════════════════════════════════
  //  CELEBRATION
  // ══════════════════════════════════════════════════════════════════════
  function celebrate(res) {
    let ov = $("#cc-prestige-celebration");
    if (!ov) { ov = el("div"); ov.id = "cc-prestige-celebration"; document.body.appendChild(ov); }
    const cat = S.cat || {};
    const skinAnimal = res.skin ? (cat.skin_animals || []).find((a) => a.id === res.skin.animal) : null;
    const skinStyle = res.skin ? (cat.skin_styles || []).find((s) => s.id === res.skin.style) : null;
    const map = animalMap();
    const nick = (bridge() && bridge().nickname && bridge().nickname()) || "You";
    const meta = { level: num(res.prestige), title: res.title,
                   name: appearanceToName({ mode: "default" }) };
    // Show the new colour if this Prestige granted exactly one — otherwise the
    // player picks in Name Appearance and we don't guess for them.
    const newColor = (res.colors_unlocked || [])[0];
    if (newColor) {
      const c = (cat.colors || []).find((x) => x.id === newColor);
      if (c) meta.name = appearanceToName({ mode: "solid", colorId: c.id, color: c.hex });
    }
    const kept = (res.avatars_kept || []).map((p) => {
      const a = map[String(p).toLowerCase()] || { name: p, img: p };
      return '<div class="ccP-rw"><div class="ccP-rw-lbl">Critter kept</div>'
        + '<div style="display:flex;align-items:center;gap:10px;margin-top:6px">'
        + '<img src="' + esc(avSrc(a.img)) + '" alt="" style="width:44px;height:44px;border-radius:50%;object-fit:cover">'
        + '<div class="ccP-rw-desc" style="font-weight:900;color:#1a2d5a">' + esc(a.name) + "</div></div></div>";
    }).join("");

    const bg = res.background || {};
    ov.innerHTML = '<div class="ccP-cel-swirl" aria-hidden="true"></div>'
      + '<div class="ccP-cel-wave" aria-hidden="true"></div>'
      + '<div class="ccP-cel-inner" role="dialog" aria-modal="true" aria-labelledby="ccP-cel-h">'
      + '<div class="ccP-cel-title">You have ridden the next current!</div>'
      + '<div class="ccP-cel-num">' + num(res.prestige) + "</div>"
      + '<h2 class="ccP-cel-title" id="ccP-cel-h" style="font-size:clamp(18px,4vw,26px)">'
      + badgeHtml(num(res.prestige), { large: true }) + " " + esc(res.title || "") + "</h2>"
      + (skinAnimal ? '<div style="margin:14px auto 0;max-width:150px">'
        + cardArtHtml(skinAnimal.uid, res.skin.style) + "</div>" : "")
      + '<div class="ccP-cel-msg">Your level has returned to Level 1, but your journey has made you stronger. '
      + "Enjoy your new rewards and begin your next adventure through the oceans of Currents and Critters.</div>"
      + '<div class="ccP-cel-grid">'
      + celCard("🪙", "Critter Coins", "+" + fmt(res.coins_awarded), "Balance: " + fmt(res.coins_total))
      + celCard("⭐", "Permanent XP bonus", "+" + num(res.xp_bonus_pct) + "%", "From every XP source")
      + celCard("🛒", "Store bonus", "+" + num(res.store_bonus_pct) + "%", "On bought coin packs")
      + celCard("🌊", "Background", esc(bg.name || "—"), esc(bg.blurb || ""))
      + (skinAnimal && skinStyle ? celCard("🎨", "Animal skin",
        esc(skinStyle.name) + " " + esc(skinAnimal.name), "Appearance only") : "")
      + celCard("🏷️", "Name colour", nameHtml(nick, meta, { surface: "dark", badge: false }),
        newColor ? "Ready to equip" : "New options unlocked")
      + kept
      + "</div>"
      + '<div class="ccP-cel-btns">'
      + '<button class="ccP-btn pri" type="button" data-cel="bg">Equip New Background</button>'
      + (skinAnimal ? '<button class="ccP-btn" type="button" data-cel="skin">Equip New Animal Skin</button>' : "")
      + (newColor ? '<button class="ccP-btn" type="button" data-cel="color">Use New Name Colour</button>' : "")
      + '<button class="ccP-btn" type="button" data-cel="profile">View Profile</button>'
      + '<button class="ccP-btn ghost" type="button" data-cel="close">Continue Playing</button>'
      + "</div></div>";

    // The scene behind it is the background they just unlocked.
    const scene = (cat.backgrounds || []).find((b) => b.id === String(bg.id || "").replace(/-\d+$/, ""));
    ov.insertBefore(buildScene((scene && scene.scene) || "shallows", { dense: true }), ov.firstChild);
    ov.classList.add("open");
    ov.classList.toggle("ccP-paused", document.hidden);

    // NOTHING is auto-equipped — every cosmetic is opt-in from these buttons.
    const close = () => { ov.classList.remove("open"); ov.innerHTML = ""; };
    ov.querySelectorAll("[data-cel]").forEach((b) => b.addEventListener("click", async () => {
      const what = b.getAttribute("data-cel");
      if (what === "close") return close();
      if (what === "profile") { close(); try { bridge().goTab("overview"); } catch (_) {} return; }
      if (what === "bg") { await equip({ background: bg.id }); toast("Prestige background equipped.", "ok"); return; }
      if (what === "skin") { await equip({ skin: res.skin.animal + ":" + res.skin.style }); toast("Animal skin equipped.", "ok"); return; }
      if (what === "color") {
        const c = (cat.colors || []).find((x) => x.id === newColor);
        if (c) { await equip({ mode: "solid", colorId: c.id }); toast("Name colour equipped.", "ok"); }
        return;
      }
    }));
    const first = ov.querySelector("[data-cel]");
    if (first) try { first.focus(); } catch (_) {}
    ov.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  }
  function celCard(ico, lbl, val, desc) {
    return '<div class="ccP-rw"><div class="ccP-rw-ico" aria-hidden="true">' + ico + "</div>"
      + '<div class="ccP-rw-lbl">' + esc(lbl) + '</div><div class="ccP-rw-val">' + val + "</div>"
      + '<div class="ccP-rw-desc">' + desc + "</div></div>";
  }

  /** Send an appearance change. Merges onto what is already equipped so
   *  equipping a background never silently drops a name colour. */
  async function equip(patch) {
    const cur = (S.state && S.state.prestige && S.state.prestige.appearance) || {};
    const next = Object.assign({}, cur, patch || {});
    const res = await post("appearance", { appearance: next });
    if (!res || !res.ok) { toast(res ? errMsg(res.error) : "Couldn't save that — try again.", "err"); return false; }
    if (S.state && S.state.prestige) S.state.prestige.appearance = res.appearance;
    if (S.mine) S.mine.name = appearanceToName(res.appearance);
    try { bridge().onAppearance && bridge().onAppearance(res.appearance); } catch (_) {}
    return true;
  }

  // ══════════════════════════════════════════════════════════════════════
  //  NAME APPEARANCE MENU
  // ══════════════════════════════════════════════════════════════════════
  async function openAppearance() {
    if (!S.state) { const r = await loadState(); if (!r || !r.ok) { toast(errMsg(r && r.error), "err"); return; } }
    await loadCatalog();
    let bg = $("#ccP-app-bg");
    if (bg) bg.remove();
    bg = el("div", "ccC-modal-bg");
    bg.id = "ccP-app-bg";
    bg.style.zIndex = "9880";
    const box = el("div", "ccP-page");
    // Matches the reef page: cream glass, navy ink, the Player Home's corners.
    box.style.cssText = "background:linear-gradient(180deg,#f7fcff,#e8f4fd);"
      + "border:2px solid rgba(255,255,255,.9);box-shadow:0 22px 60px rgba(10,50,90,.4);"
      + "border-radius:24px;max-width:660px;width:100%;max-height:88vh;overflow-y:auto;padding:6px 0 18px;"
      + "font-family:'Nunito',sans-serif;color:#17365a;";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-label", "Username appearance");
    bg.appendChild(box);
    document.body.appendChild(bg);
    bg.addEventListener("click", (e) => { if (e.target === bg) bg.remove(); });
    bg.addEventListener("keydown", (e) => { if (e.key === "Escape") bg.remove(); });

    const draw = () => {
      const p = S.state.prestige;
      const cat = S.cat || {};
      const app = p.appearance || {};
      const nick = (bridge() && bridge().nickname && bridge().nickname()) || "You";
      const meta = { level: num(p.level), name: appearanceToName(app), title: p.title };
      const owned = new Set(p.colors || []);
      const ownedG = new Set(p.gradients || []);
      const ownedFx = new Set(p.effects || []);

      const swatch = (c) => {
        const have = c.id === "default" || owned.has(c.id);
        const on = (app.mode === "default" && c.id === "default")
          || (app.mode === "solid" && app.colorId === c.id);
        const need = c.level > 0 ? "Prestige " + c.level : "Always available";
        return '<button class="ccP-swatch" type="button" data-solid="' + esc(c.id) + '"'
          + (have ? "" : " disabled") + ' aria-pressed="' + (on ? "true" : "false") + '">'
          + '<span class="dot" style="background:' + esc(c.hex || "#8fb8d8") + '"></span>'
          + '<span><span class="lbl">' + esc(c.name) + "</span>"
          + '<span class="sub">' + (have ? (on ? "Equipped" : "Unlocked") : "🔒 " + need) + "</span></span></button>";
      };
      const gradBtn = (g) => {
        const have = ownedG.has(g.id);
        const on = app.mode === "gradient" && app.gradientId === g.id;
        return '<button class="ccP-swatch" type="button" data-grad="' + esc(g.id) + '"'
          + (have ? "" : " disabled") + ' aria-pressed="' + (on ? "true" : "false") + '">'
          + '<span class="dot" style="background:linear-gradient(90deg,' + esc(g.from) + ',' + esc(g.to) + ')"></span>'
          + '<span><span class="lbl">' + esc(g.name) + "</span>"
          + '<span class="sub">' + (have ? (on ? "Equipped" : "Unlocked") : "🔒 Prestige " + g.level) + "</span></span></button>";
      };
      const fxBtn = (f) => {
        const have = f.id === "none" || ownedFx.has(f.id);
        const on = (app.effect || "none") === f.id;
        return '<button class="ccP-btn' + (on ? " pri" : "") + '" type="button" data-fx="' + esc(f.id) + '"'
          + (have ? "" : " disabled") + ' aria-pressed="' + (on ? "true" : "false") + '">'
          + esc(f.name) + (have ? "" : " · P" + f.level) + "</button>";
      };
      const bgs = (p.backgrounds || []).map((id) => {
        const base = (cat.backgrounds || []).find((b) => b.id === String(id).replace(/-\d+$/, ""));
        const on = app.background === id;
        const lvl = base ? base.level : 0;
        return '<button class="ccP-tile" type="button" data-bg="' + esc(id) + '" aria-pressed="' + (on ? "true" : "false") + '">'
          + (on ? '<span class="pick" aria-hidden="true">✓</span>' : "")
          + '<div class="ccP-scene-thumb" data-scene-thumb="' + esc(base ? base.scene : "shallows") + '"'
          + ' style="height:64px;border-radius:11px;overflow:hidden;position:relative"></div>'
          + '<div class="nm">' + esc(base ? base.name : id) + "</div>"
          + '<div class="req">Prestige ' + lvl + "</div></button>";
      }).join("");
      const skins = (p.skins || []).map((s) => {
        const a = (cat.skin_animals || []).find((x) => x.id === s.animal) || { name: s.animal, uid: 0 };
        const st = (cat.skin_styles || []).find((x) => x.id === s.style) || { name: s.style };
        const key = s.animal + ":" + s.style;
        const on = app.skin === key;
        return '<button class="ccP-tile" type="button" data-skin="' + esc(key) + '" aria-pressed="' + (on ? "true" : "false") + '">'
          + (on ? '<span class="pick" aria-hidden="true">✓</span>' : "")
          + cardArtHtml(a.uid, s.style)
          + '<div class="nm">' + esc(a.name) + '</div><div class="req">' + esc(st.name) + "</div></button>";
      }).join("");

      box.innerHTML = '<div class="ccP-hero" style="padding:18px 20px 8px"><div class="ccP-hero-main">'
        + '<h2 class="ccP-title" style="font-size:26px">Name Appearance</h2>'
        + '<div class="ccP-sub">Everything here is permanent and never resets on a Prestige.</div></div></div>'
        + '<div class="ccP-body">'
        + '<div class="ccP-panel"><div class="ccP-panel-h">Live preview</div>'
        + previewStrip(nick, meta) + "</div>"
        + '<div class="ccP-panel"><div class="ccP-panel-h">Solid colours</div>'
        + '<div class="ccP-swatches">' + (cat.colors || []).map(swatch).join("") + "</div></div>"
        + '<div class="ccP-panel"><div class="ccP-panel-h">Custom colour'
        + (p.custom_color ? "" : " 🔒") + "</div>"
        + (p.custom_color
          ? '<div class="ccP-toolbar">'
            + '<input type="color" class="ccP-input" id="ccP-cc-wheel" value="' + esc(app.color || "#1f7ae0") + '" '
            + 'aria-label="Colour wheel" style="width:64px;height:44px;padding:3px">'
            + '<input type="range" id="ccP-cc-hue" min="0" max="359" value="210" aria-label="Colour slider" style="flex:1 1 140px">'
            + '<input class="ccP-input" id="ccP-cc-hex" value="' + esc(app.color || "#1f7ae0") + '" '
            + 'maxlength="7" spellcheck="false" aria-label="Hexadecimal colour" style="width:104px">'
            + '<button class="ccP-btn pri" type="button" data-act="cc-apply">Use this colour</button></div>'
            + '<div class="ccP-panel-sub" id="ccP-cc-msg" role="status" aria-live="polite">Colours that would be unreadable, '
            + "or that look like staff and system messages, can't be saved.</div>"
          : '<div class="ccP-panel-sub">Unlocks at Prestige ' + num(cat.custom_color_level || 4) + ".</div>")
        + "</div>"
        + '<div class="ccP-panel"><div class="ccP-panel-h">Gradients' + (p.custom_gradient ? "" : " 🔒") + "</div>"
        + '<div class="ccP-swatches">' + (cat.gradients || []).map(gradBtn).join("") + "</div>"
        + (p.custom_gradient ? gradientEditorHtml(app, p, cat)
          : '<div class="ccP-panel-sub" style="margin-top:10px">Unlocks at Prestige ' + num(cat.gradient_level || 5) + ".</div>")
        + "</div>"
        + '<div class="ccP-panel"><div class="ccP-panel-h">Animated effects</div>'
        + '<div class="ccP-panel-sub">Kept slow and subtle on purpose. You can switch the animation off and keep the colour.</div>'
        + '<div class="ccP-toolbar">' + (cat.effects || []).map(fxBtn).join("") + "</div>"
        + '<label class="ccP-panel-sub" style="display:flex;gap:9px;align-items:center;margin-top:10px;cursor:pointer">'
        + '<input type="checkbox" id="ccP-animate"' + (app.animate === false ? "" : " checked") + ">"
        + "Animate my name effects</label></div>"
        + (bgs ? '<div class="ccP-panel"><div class="ccP-panel-h">Prestige backgrounds</div>'
          + '<div class="ccP-panel-sub">Shown behind your profile and on your Prestige page.</div>'
          + '<div class="ccP-grid">' + bgs + "</div></div>" : "")
        + (skins ? '<div class="ccP-panel"><div class="ccP-panel-h">Alternate animal skins</div>'
          + '<div class="ccP-panel-sub">Cosmetic only — they change how your card art looks to you and nothing else.</div>'
          + '<div class="ccP-grid">' + skins + "</div>"
          + '<label class="ccP-panel-sub" style="display:flex;gap:9px;align-items:center;margin-top:10px;cursor:pointer">'
          + '<input type="checkbox" id="ccP-skinsoff"' + (app.skins_off ? " checked" : "") + ">"
          + "Turn all my animal skins off in game</label></div>" : "")
        + '<div class="ccP-actions"><button class="ccP-btn ghost" type="button" data-act="reset-app">Back to default</button>'
        + '<div class="ccP-spacer"></div>'
        + '<button class="ccP-btn pri" type="button" data-act="close">Done</button></div>'
        + "</div>";

      // Paint each background thumbnail with its real (small) scene.
      box.querySelectorAll("[data-scene-thumb]").forEach((n) => {
        n.appendChild(buildScene(n.getAttribute("data-scene-thumb"), { dense: false }));
      });

      const save = async (patch) => { if (await equip(patch)) draw(); };
      box.querySelectorAll("[data-solid]").forEach((b) => b.addEventListener("click", () => {
        const id = b.getAttribute("data-solid");
        save(id === "default" ? { mode: "default", colorId: "", color: "" } : { mode: "solid", colorId: id });
      }));
      box.querySelectorAll("[data-grad]").forEach((b) => b.addEventListener("click", () =>
        save({ mode: "gradient", gradientId: b.getAttribute("data-grad") })));
      box.querySelectorAll("[data-fx]").forEach((b) => b.addEventListener("click", () =>
        save({ effect: b.getAttribute("data-fx") })));
      box.querySelectorAll("[data-bg]").forEach((b) => b.addEventListener("click", () =>
        save({ background: app.background === b.getAttribute("data-bg") ? "" : b.getAttribute("data-bg") })));
      box.querySelectorAll("[data-skin]").forEach((b) => b.addEventListener("click", () =>
        save({ skin: app.skin === b.getAttribute("data-skin") ? "" : b.getAttribute("data-skin") })));
      const anim = $("#ccP-animate", box);
      if (anim) anim.addEventListener("change", () => save({ animate: anim.checked }));
      const soff = $("#ccP-skinsoff", box);
      if (soff) soff.addEventListener("change", () => save({ skins_off: soff.checked }));

      const wheel = $("#ccP-cc-wheel", box), hex = $("#ccP-cc-hex", box), hue = $("#ccP-cc-hue", box);
      const syncPreview = (v) => {
        const msg = $("#ccP-cc-msg", box);
        if (hex) hex.value = v;
        if (wheel) wheel.value = v;
        if (msg) msg.textContent = "Preview: " + v;
      };
      if (wheel) wheel.addEventListener("input", () => syncPreview(wheel.value));
      if (hex) hex.addEventListener("input", () => { if (/^#[0-9a-f]{6}$/i.test(hex.value)) syncPreview(hex.value); });
      if (hue) hue.addEventListener("input", () => syncPreview(hslHex(num(hue.value), 72, 46)));
      const applyBtn = box.querySelector('[data-act="cc-apply"]');
      if (applyBtn) applyBtn.addEventListener("click", async () => {
        const v = (hex && hex.value) || (wheel && wheel.value) || "";
        const res = await post("appearance", {
          appearance: Object.assign({}, app, { mode: "custom", color: v }),
        });
        const msg = $("#ccP-cc-msg", box);
        if (!res || !res.ok) { if (msg) msg.textContent = res ? errMsg(res.error) : "Couldn't save that — try again."; return; }
        S.state.prestige.appearance = res.appearance;
        if (S.mine) S.mine.name = appearanceToName(res.appearance);
        try { bridge().onAppearance && bridge().onAppearance(res.appearance); } catch (_) {}
        draw();
      });

      const gradApply = box.querySelector('[data-act="grad-apply"]');
      if (gradApply) gradApply.addEventListener("click", async () => {
        const g = {
          mode: "gradient", gradientId: "custom",
          from: ($("#ccP-g-from", box) || {}).value || "#1f7ae0",
          to: ($("#ccP-g-to", box) || {}).value || "#12a37c",
          dir: ($("#ccP-g-dir", box) || {}).value || "h",
          style: ($("#ccP-g-style", box) || {}).value || "smooth",
        };
        const midEl = $("#ccP-g-mid", box);
        if (midEl && midEl.value && !midEl.disabled) g.mid = midEl.value;
        const res = await post("appearance", { appearance: Object.assign({}, app, g) });
        const msg = $("#ccP-g-msg", box);
        if (!res || !res.ok) { if (msg) msg.textContent = res ? errMsg(res.error) : "Couldn't save that — try again."; return; }
        S.state.prestige.appearance = res.appearance;
        if (S.mine) S.mine.name = appearanceToName(res.appearance);
        try { bridge().onAppearance && bridge().onAppearance(res.appearance); } catch (_) {}
        draw();
      });

      const resetBtn = box.querySelector('[data-act="reset-app"]');
      if (resetBtn) resetBtn.addEventListener("click", () =>
        save({ mode: "default", colorId: "", color: "", gradientId: "", from: "", to: "", mid: "", effect: "none" }));
      const closeBtn = box.querySelector('[data-act="close"]');
      if (closeBtn) closeBtn.addEventListener("click", () => { bg.remove(); if (root()) paint(); });
    };
    draw();
  }

  function gradientEditorHtml(app, p, cat) {
    const three = !!p.three_color;
    return '<div class="ccP-panel-sub" style="margin-top:12px">Or build your own:</div>'
      + '<div class="ccP-toolbar">'
      + '<label class="ccP-panel-sub" style="margin:0">Start<input type="color" class="ccP-input" id="ccP-g-from" '
      + 'value="' + esc(app.from || "#1f7ae0") + '" style="width:56px;height:40px;padding:3px;margin-left:6px"></label>'
      + '<label class="ccP-panel-sub" style="margin:0">Middle<input type="color" class="ccP-input" id="ccP-g-mid" '
      + 'value="' + esc(app.mid || "#7a49d6") + '"' + (three ? "" : " disabled")
      + ' style="width:56px;height:40px;padding:3px;margin-left:6px"></label>'
      + '<label class="ccP-panel-sub" style="margin:0">End<input type="color" class="ccP-input" id="ccP-g-to" '
      + 'value="' + esc(app.to || "#12a37c") + '" style="width:56px;height:40px;padding:3px;margin-left:6px"></label>'
      + '<select class="ccP-select" id="ccP-g-dir" aria-label="Gradient direction">'
      + '<option value="h"' + (app.dir === "h" ? " selected" : "") + ">Across</option>"
      + '<option value="v"' + (app.dir === "v" ? " selected" : "") + ">Down</option>"
      + '<option value="d"' + (app.dir === "d" ? " selected" : "") + ">Diagonal</option></select>"
      + '<select class="ccP-select" id="ccP-g-style" aria-label="Gradient style">'
      + (cat.gradient_styles || ["smooth", "split", "center", "edges"]).map((s) =>
        '<option value="' + esc(s) + '"' + (app.style === s ? " selected" : "") + ">"
        + esc(s.charAt(0).toUpperCase() + s.slice(1)) + "</option>").join("")
      + "</select>"
      + '<button class="ccP-btn pri" type="button" data-act="grad-apply">Use this gradient</button></div>'
      + '<div class="ccP-panel-sub" id="ccP-g-msg" role="status" aria-live="polite">'
      + (three ? "Three-colour gradients are unlocked." : "Middle colour unlocks at Prestige " + num(cat.three_color_level || 10) + ".")
      + " Gradients that would be hard to read are refused.</div>";
  }

  function hslHex(h, s, l) {
    s /= 100; l /= 100;
    const k = (n) => (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
    const to = (x) => Math.round(255 * x).toString(16).padStart(2, "0");
    return "#" + to(f(0)) + to(f(8)) + to(f(4));
  }

  // ══════════════════════════════════════════════════════════════════════
  //  MAX-LEVEL NOTICE
  // ══════════════════════════════════════════════════════════════════════
  const NOTICE_KEY = "cc_prestige_notice_seen";
  /** The banner shown on Player Home the moment the cap is reached. Mounts
   *  into `host`; returns true when it drew something.
   *
   *  Dismissal is per SESSION, not forever: Prestige is always optional, but a
   *  player sitting at the cap should be reminded it is waiting each time they
   *  come back — not silenced permanently by one stray tap on the ✕. */
  function notice(host) {
    if (!host) return false;
    const st = S.state;
    if (!st || !st.can_prestige) { const old = host.querySelector(".ccP-notice"); if (old) old.remove(); return false; }
    const key = NOTICE_KEY + "_" + num(st.prestige && st.prestige.level);
    let dismissed = false;
    try { dismissed = sessionStorage.getItem(key) === "1"; } catch (_) {}
    if (dismissed) return false;
    if (host.querySelector(".ccP-notice")) return true;
    const n = el("div", "ccP-notice");
    n.setAttribute("role", "status");
    n.innerHTML = '<div class="ccP-notice-txt">'
      + '<div class="ccP-notice-h">You have reached the end of this current!</div>'
      + '<div class="ccP-notice-s">Ride the next current to return to Level 1 and unlock permanent Prestige rewards.</div>'
      + "</div>"
      + '<button class="ccP-notice-btn" type="button">View Prestige Rewards</button>'
      + '<button class="ccP-notice-x" type="button" aria-label="Dismiss">✕</button>';
    n.querySelector(".ccP-notice-btn").addEventListener("click", () => {
      try { bridge().goTab("prestige"); } catch (_) {}
    });
    n.querySelector(".ccP-notice-x").addEventListener("click", () => {
      try { sessionStorage.setItem(key, "1"); } catch (_) {}
      n.remove();
    });
    host.insertBefore(n, host.firstChild);
    return true;
  }

  // ══════════════════════════════════════════════════════════════════════
  //  THE SIGN-IN ASK
  //  Prestige is ALWAYS optional — but a player who has reached the end of the
  //  current gets asked, in the game's own voice, every time they sign in.
  //  "Not right now" costs nothing and the ask returns next session; it never
  //  starts anything on its own and it never blocks the game behind it.
  // ══════════════════════════════════════════════════════════════════════
  const ASK_KEY = "cc_prestige_asked";
  let _askOpen = false;

  async function ask(opts) {
    const o = opts || {};
    // Only refuse while the overlay is genuinely ON SCREEN. Keying purely off
    // the flag means anything that wipes the DOM from under it (a route change,
    // a re-render) suppresses the ask for the rest of the session with no way
    // to get it back.
    if (_askOpen && document.getElementById("cc-prestige-ask")) return false;
    _askOpen = false;
    if (!S.state) { await loadCatalog(); const r = await loadState(); if (!r || !r.ok) return false; }
    const st = S.state;
    if (!st || !st.can_prestige) return false;
    // Once per sign-in per Prestige level. Answering "not right now" is a real
    // answer for this session; signing in again asks again.
    const key = ASK_KEY + "_" + num(st.prestige && st.prestige.level);
    if (!o.force) {
      try { if (sessionStorage.getItem(key) === "1") return false; } catch (_) {}
    }
    try { sessionStorage.setItem(key, "1"); } catch (_) {}

    const nxt = st.next || {};
    const bg = nxt.background || {};
    const badge = nxt.badge || {};
    _askOpen = true;

    const wrap = el("div");
    wrap.id = "cc-prestige-ask";
    wrap.className = "ccP-ask-bg";
    wrap.innerHTML = '<div class="ccP-ask" role="dialog" aria-modal="true" aria-labelledby="ccP-ask-h">'
      + '<div class="ccP-ask-inner">'
      + '<div class="ccP-ask-badge">' + (badgeHtml(num(nxt.prestige), { large: true }) || "🌊") + "</div>"
      + '<h2 class="ccP-title" id="ccP-ask-h">You have reached the end of this current!</h2>'
      + '<div class="ccP-sub">Ride the next current to return to Level 1 and unlock permanent Prestige rewards. '
      + "You can do this whenever you like — nothing expires, and we'll ask again next time you sign in.</div>"
      + '<div class="ccP-ask-grid">'
      + askCard('<img src="/critter-coin.png?v=1" alt="">', fmt(nxt.coins), "Critter Coins")
      + askCard("⭐", "+" + num(nxt.xp_bonus_pct) + "%", "Permanent XP")
      + askCard("🛒", "+" + num(nxt.store_bonus_pct) + "%", "Store bonus")
      + askCard("🎨", "1 animal", "Alternate skin")
      + askCard("🌊", esc(bg.name || "—"), "New background")
      + askCard("🏅", esc(badge.name || "—"), "New badge")
      + "</div>"
      + '<div class="ccP-ask-keep">You keep your competitive rank, clan, friends, coins, achievements, '
      + "lifetime stats and everything you have ever bought.</div>"
      + '<div class="ccP-ask-btns">'
      + '<button class="ccP-ride" type="button" data-ask="go">View Prestige Rewards</button>'
      + '<button class="ccP-btn ghost" type="button" data-ask="later">Not right now</button>'
      + "</div></div></div>";
    // The same living ocean as the page — this is the doorway into it.
    // Guarded: an overlay whose scene failed to mount is still perfectly
    // usable, but one that THREW here would leave a half-built modal on screen
    // with no way to close it.
    const card = wrap.querySelector(".ccP-ask");
    if (card) card.insertBefore(buildScene(bg.scene || "shallows", { dense: true }), card.firstChild);
    document.body.appendChild(wrap);
    wrap.classList.toggle("ccP-paused", document.hidden);

    const close = () => { _askOpen = false; wrap.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
    const later = wrap.querySelector('[data-ask="later"]');
    if (later) later.addEventListener("click", close);
    const go = wrap.querySelector('[data-ask="go"]');
    if (go) {
      go.addEventListener("click", () => {
        close();
        try { bridge().goTab("prestige"); } catch (_) {}
      });
      try { go.focus(); } catch (_) {}
    }
    return true;
  }
  function askCard(ico, val, lbl) {
    return '<div class="ccP-ask-card"><div class="ccP-rw-ico" aria-hidden="true">' + ico + "</div>"
      + '<div class="ccP-rw-val">' + val + '</div><div class="ccP-rw-lbl">' + esc(lbl) + "</div></div>";
  }

  // ══════════════════════════════════════════════════════════════════════
  //  EXPORTS
  // ══════════════════════════════════════════════════════════════════════
  window.__ccPrestigeRender = render;
  window.__ccPrestigeBadgeHtml = badgeHtml;
  window.__ccPrestigeNameHtml = nameHtml;
  window.__ccPrestigeDecorate = decorate;
  window.__ccPrestigeLookup = lookup;
  window.__ccPrestigeLookupByName = lookupByName;

  // ══════════════════════════════════════════════════════════════════════
  //  THE ONE SWEEP THAT PAINTS USERNAMES EVERYWHERE
  //
  //  Any username anywhere in the game becomes a Prestige username by getting
  //  two attributes:  data-cc-pname="<uid|nickname>"  data-cc-surface="dark".
  //  This sweep resolves them (uid or name), applies the colour/effect and
  //  drops the badge in beside it. One call after any render is enough, and
  //  re-running it is free — already-decorated nodes are skipped.
  //
  //  Doing it this way instead of editing every render path is what makes the
  //  colours CONSISTENT: leaderboards, friends, clan rosters, in-game seats,
  //  match results, messages, invites, brackets and spectator screens all get
  //  the identical treatment from the identical code.
  // ══════════════════════════════════════════════════════════════════════
  let _sweepTimer = null;
  function refreshNames(scope) {
    clearTimeout(_sweepTimer);
    _sweepTimer = setTimeout(() => sweep(scope), 40);
  }
  async function sweep(scope) {
    const rootEl = scope || document;
    let nodes;
    try { nodes = rootEl.querySelectorAll("[data-cc-pname]"); } catch (_) { return; }
    if (!nodes || !nodes.length) return;
    const uids = [], names = [];
    nodes.forEach((n) => {
      const key = String(n.getAttribute("data-cc-pname") || "").trim();
      if (!key) return;
      // A Firebase uid is a long opaque token; a nickname is short and is what
      // the player typed. Anything 24+ chars with no space is treated as a uid.
      if (key.length >= 20 && !/\s/.test(key)) uids.push(key);
      else names.push(key);
    });
    const [byUid, byName] = await Promise.all([
      uids.length ? lookup(uids) : Promise.resolve({}),
      names.length ? lookupByName(names) : Promise.resolve({}),
    ]);
    nodes.forEach((n) => {
      const key = String(n.getAttribute("data-cc-pname") || "").trim();
      if (!key) return;
      const meta = byUid[key] || byName[key.toLowerCase()]
        || (S.mine && (S.mine.uid === key || String(S.mine.nickname).toLowerCase() === key.toLowerCase()) ? S.mine : null);
      if (!meta) return;
      const stamp = String(meta.level) + ":" + JSON.stringify(meta.name || {}) + ":" + (S.still ? 1 : 0);
      if (n.getAttribute("data-cc-painted") === stamp) return;
      n.setAttribute("data-cc-painted", stamp);
      decorate(n, meta, {
        surface: n.getAttribute("data-cc-surface") || "light",
        badge: n.getAttribute("data-cc-badge") !== "0",
        uid: meta.uid || key,
      });
    });
  }
  window.__ccRefreshNames = refreshNames;
  window.__ccPrestigeMine = mine;
  window.__ccPrestigeXp = xpBreakdown;
  window.__ccPrestigeSkinFor = skinFor;
  window.__ccPrestigeNotice = notice;
  window.__ccPrestigeAsk = ask;
  window.__ccPrestigeAppearance = openAppearance;
  window.__ccPrestigeCelebrate = celebrate;
  window.__ccPrestigeScene = buildScene;
  window.__ccPrestigeTitle = titleFor;
  window.__ccPrestigeState = () => S.state;
  // Warm the cache as soon as the app has an account, so the FIRST paint of a
  // leaderboard or a friends list already has badges instead of popping them in.
  window.__ccPrestigePrime = async function () {
    try { await loadCatalog(); await mine(true); } catch (_) {}
  };
})();
