/* Currents and Critters: Critter Pass (self-contained module).
 *
 * The PAID track that sits under the free Level Pass, rendered into
 * #cc-critter-pass-root: a horizontal reward rail over 100 PASS LEVELS, a
 * purchase card in front of it until the pass is unlocked, and the two perks
 * nothing else in the game hands out (an extra daily challenge and an extra
 * weekly challenge, for keeps).
 *
 * A PASS LEVEL IS NOT THE ACCOUNT LEVEL, and getting that wrong here is the
 * one bug that would make every card on the rail lie. The account climbs a
 * 250,000-XP lifetime curve; the pass climbs a flat state.seasonXpPerLevel of
 * SEASON XP (the XP earned since the pass was unlocked), which is what makes
 * Level 100 about a month rather than most of a year. Everything that decides
 * what is claimable reads passLevel(); state.level is the account, and it is
 * used in exactly one place, for a chip.
 *
 *   window.__ccCritterPassRender()   the page itself
 *   window.__ccCritterPassSync()     re-read state from the server
 *   window.__ccCritterPassPrime()    warm the cache without opening the page
 *   window.__ccCritterPassReset()    drop it on sign-out
 *   window.__ccPassExtraSlots()      { daily, weekly } EXTRA challenge slots,
 *                                    read synchronously by the challenge strip
 *   window.__ccCritterPassOwned()    does this account own the pass?
 *
 * NOTHING here decides anything. The server owns the track, owns the 4,000-coin
 * purchase, owns the pass curve (it is served, never typed here), re-derives
 * the pass level from the account's own stored XP inside every payout, and
 * writes the goods. This file renders that state and sends
 * intents. Editing a number in here changes what one player sees for one paint
 * and is then thrown away by the server's own re-check (critter_pass_server.py).
 *
 * WHY THE EXTRA CHALLENGE SLOTS ARE READ FROM HERE
 * The daily and weekly challenges live in localStorage: they roll on the
 * player's OWN local midnight, which no server can compute. So the slot COUNT
 * is server-owned (a claimed tier raises bonus_daily_slots) while the slots
 * themselves are local. window.__ccPassExtraSlots() is that seam, and like
 * __ccPassBoost it is deliberately synchronous and cached: the challenge strip
 * repaints on every progress report and can never await a request. An unloaded
 * pass reports "no extra slots", which is the safe direction: the player sees
 * the base three for a moment, never nine that then vanish.
 */
(function () {
  "use strict";

  function bridge() { return window.__ccCritterPass; }
  // A MISSING bridge means preview-app.js never reached the line that defines
  // one. Registering anyway (and re-checking at click time) is what stops the
  // tab being permanently, silently blank, the exact failure the Clans tab
  // shipped with once already.
  if (bridge() && bridge().ENABLED === false) return;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, d) => { const n = Number(v); return Number.isFinite(n) ? n : (d || 0); };
  const fmt = (n) => Math.round(num(n)).toLocaleString();
  const toast = (m, t) => { try { bridge().toast(m, t); } catch (_) {} };

  /* How long is left in the 30-day season, as the header says it.
   *
   * The count comes from the SERVER (state.seasonDaysLeft), never from a
   * subtraction against the device clock: a phone whose date is a week out
   * would otherwise print a week's worth of the wrong answer. It says NOTHING
   * when the field is missing, so an older cached payload just draws the
   * season line it always drew rather than "NaN days left".
   *
   * A finished season is not a lock. The track stays claimable when the clock
   * runs out (the server keeps serving Season 1 until Season 2 actually
   * ships), so the copy promises a new season, it does not take this one away.
   */
  function seasonLeftHtml() {
    if (!_state || _state.seasonDaysLeft == null) return "";
    if (_state.seasonOver) return `<span class="ccCP-season-left">New season coming soon</span>`;
    const d = Math.max(0, num(_state.seasonDaysLeft));
    return `<span class="ccCP-season-left">${d} day${d === 1 ? "" : "s"} left in the season</span>`;
  }
  const avSrc = (u) => { try { return bridge().avSrc(u); } catch (_) { return u; } };

  // The bridge's post() resolves to an ENVELOPE: { ok, status, data }, where
  // `data` is the server's JSON body, and it THROWS when the request never
  // landed. Unwrapping in exactly one place is what keeps an async throw from
  // silently blanking the surface it renders.
  function unwrap(res) {
    if (res && typeof res === "object" && "data" in res && "status" in res) {
      return res.data || { ok: false, error: "server_error" };
    }
    return res || { ok: false, error: "server_error" };
  }

  async function post(action, extra) {
    const b = bridge();
    if (!b) return { ok: false, error: "unavailable" };
    const body = Object.assign({}, extra || {});
    try { body.idToken = (await b.idToken()) || ""; } catch (_) { body.idToken = ""; }
    try { return unwrap(await b.post("/api/critterpass/" + action, body)); }
    catch (_) { return { ok: false, error: "offline" }; }
  }

  // Server error code → a sentence a player can act on. Mirrors
  // critter_pass_server.ERROR_MESSAGES so the toast and the server agree; the
  // server also sends `message`, and that one wins when it is there.
  const MESSAGES = {
    unauthorized: "Sign in to use the Critter Pass.",
    already_claimed: "You've already claimed that reward.",
    already_owned: "You already own the Critter Pass.",
    not_owned: "Unlock the Critter Pass first.",
    not_enough_coins: "You don't have enough Critter Coins yet.",
    no_vouchers: "You don't have a Season Pass voucher to redeem.",
    level_locked: "You haven't reached that level yet.",
    shields_full: "Your Streak Shields are full: spend one first.",
    boosts_full: "You're holding as many XP Boosts as you can: use one first.",
    rerolls_full: "You're holding as many Weekly Swaps as you can: use one first.",
    backgrounds_full: "You already own every background. This one is waiting for the next batch.",
    emotes_full: "You already have an emote for every critter you own.",
    daily_slots_full: "You already have every extra daily challenge slot.",
    weekly_slots_full: "You already have every extra weekly challenge slot.",
    offline: "Couldn't reach the server. Nothing was claimed: please try again.",
    unavailable: "The Critter Pass didn't finish loading: please refresh the page.",
    server_error: "Something went wrong. Nothing was claimed: please try again.",
  };
  const msgFor = (res) => (res && res.message)
    || MESSAGES[String((res && res.error) || "")]
    || MESSAGES.server_error;

  // ── State ────────────────────────────────────────────────────────────────
  // `_state` is the last SERVER answer. Everything paints from it, and nothing
  // else writes to it: a claim updates it by re-reading, never by guessing what
  // the server would have done.
  let _state = null;
  let _loading = false;
  let _claimedSet = new Set();
  let _busyTier = "";       // tier id mid-claim, so its button can't double-fire
  let _buying = false;

  function inventory() {
    return (_state && _state.inventory) || {
      shields: 0, boosts: 0, rerolls: 0, boostUntil: 0,
      boostActive: false, boostPercent: 20, rerollWeek: 0, coins: 0,
      extraDaily: 0, extraWeekly: 0,
    };
  }

  // THE CHALLENGE-SLOT SEAM. Synchronous and cached on purpose: the challenge
  // strip repaints on every progress report, and none of those can afford to
  // await a request. Reporting zero while the pass is unloaded is the safe
  // direction: three slots that grow to four reads as a reward arriving, four
  // that shrink to three reads as one being taken away.
  //
  // Clamped against the server's own maxima so a tampered cache cannot ask the
  // browser to roll nine daily challenges out of a fifty-challenge pool.
  window.__ccPassExtraSlots = function () {
    const inv = inventory();
    const maxD = num(_state && _state.extraDailyMax, 3);
    const maxW = num(_state && _state.extraWeeklyMax, 3);
    return {
      daily: Math.max(0, Math.min(maxD, num(inv.extraDaily))),
      weekly: Math.max(0, Math.min(maxW, num(inv.extraWeekly))),
    };
  };

  window.__ccCritterPassOwned = function () {
    return !!(_state && _state.owned);
  };

  async function sync() {
    if (_loading) return _state;
    _loading = true;
    const before = window.__ccPassExtraSlots();
    try {
      const res = await post("state", {});
      if (res && res.ok) {
        _state = res;
        _claimedSet = new Set(Array.isArray(res.claimed) ? res.claimed : []);
      }
    } finally { _loading = false; }
    // EVERY path that changes what is claimable ends in a sync: the boot prime,
    // opening the page, a buy, a claim, a claim-all. Repainting the badge here
    // and nowhere else is what stops the number drifting from the track.
    paintNavBadge();
    // The challenge strip mirrors the slot counts, and it has no way to know
    // they moved. Only repaint when they REALLY moved: renderChallengeStrip()
    // also stamps "played today", so calling it on every state read would be a
    // side effect hiding inside a cache refresh.
    const after = window.__ccPassExtraSlots();
    if (after.daily !== before.daily || after.weekly !== before.weekly) {
      try { window.renderChallengeStrip && window.renderChallengeStrip(); } catch (_) {}
      try { window._renderIgChallengePanel && window._renderIgChallengePanel(); } catch (_) {}
    }
    return _state;
  }
  window.__ccCritterPassSync = async function () {
    await sync();
    if (isOpen()) render();
    return _state;
  };

  function isOpen() {
    const root = $("cc-critter-pass-root");
    return !!(root && root.offsetParent !== null);
  }

  // ── XP maths: THE PASS CURVE, WHICH IS NOT THE ACCOUNT CURVE ─────────────
  // A Critter Pass tier is gated on the PASS level, and a pass level costs a
  // flat state.seasonXpPerLevel of SEASON XP (the XP earned since the pass was
  // unlocked). The account's own 1-100 climb is a different, far longer curve
  // and is shown only as a chip: mixing the two is what would make a rail card
  // promise "3,800 XP to go" for a level that really costs 600.
  //
  // Everything comes off the served state rather than a number typed here, so
  // a server-side retune moves the rail with it.
  function passLevel() { return num(_state && _state.passLevel, 1); }
  function passMaxLevel() { return num(_state && _state.passMaxLevel, 100); }
  function xpPerPassLevel() { return num(_state && _state.seasonXpPerLevel, 0); }

  // Season XP that REACHES pass `level`. null when the curve has not loaded
  // (an older cached payload), so the caller says nothing rather than zero.
  function seasonXpToReach(level) {
    const per = xpPerPassLevel();
    if (!per) return null;
    const l = Math.max(1, Math.min(passMaxLevel(), Math.floor(num(level, 1))));
    return (l - 1) * per;
  }
  function xpUntil(level) {
    const need = seasonXpToReach(level);
    if (need == null) return null;
    return Math.max(0, need - num(_state && _state.seasonXp));
  }

  // "How much XP till the next thing": the nearest tier above the player's
  // PASS level. Every Critter Pass tier is claimable, so unlike the free pass
  // there is no milestone-versus-reward distinction to make here.
  function nextTier() {
    const track = (_state && _state.track) || [];
    const lvl = passLevel();
    let best = null;
    for (const t of track) {
      if (num(t.level) <= lvl) continue;
      if (!best || num(t.level) < num(best.level)) best = t;
    }
    return best;
  }

  function unclaimedReady() {
    if (!(_state && _state.owned)) return [];
    const track = (_state && _state.track) || [];
    const lvl = passLevel();
    return track.filter(t => t.claimable && num(t.level) <= lvl && !_claimedSet.has(t.id));
  }

  // ── The sidebar's red badge ──────────────────────────────────────────────
  // The same .ph-snav-badge the Messages and Level Pass tabs use, so an
  // unclaimed reward reads exactly like an unread message.
  //
  // It counts only rewards the player can ACT on. A Streak Shield tier sitting
  // on a full hoard is genuinely unclaimable right now, and counting it would
  // paint a red number that clicking cannot clear: the definition of a badge
  // that looks broken. The caps come from the server (state.caps) rather than
  // being copied here, so the badge and the payout agree on what "full" means.
  function claimableNow() {
    const inv = inventory();
    const caps = (_state && _state.caps) || {};
    const room = {
      shield: num(caps.shields, 3) - num(inv.shields),
      boost:  num(caps.boosts, 3) - num(inv.boosts),
      swap:   num(caps.rerolls, 3) - num(inv.rerolls),
    };
    return unclaimedReady().filter(t => {
      const left = room[String(t.type)];
      if (left === undefined) return true;   // coins, xp, emotes, backgrounds, slots
      if (left <= 0) return false;
      room[String(t.type)] = left - 1;       // two shield tiers, one slot free
      return true;
    });
  }

  // Paint it. Signed out, not owning the pass, still loading, or nothing
  // waiting all mean "hide": never a stale number, and never a lingering 0.
  function paintNavBadge() {
    const el = $("snav-critterpass-badge");
    if (!el) return;
    let n = 0;
    try { n = (_state && _state.signedIn && _state.owned) ? claimableNow().length : 0; }
    catch (_) { n = 0; }
    if (n > 0) {
      el.textContent = n > 99 ? "99+" : String(n);
      el.style.display = "";
      el.setAttribute("aria-label", n + " Critter Pass reward" + (n === 1 ? "" : "s") + " ready to claim");
    } else {
      el.textContent = "";
      el.style.display = "none";
      el.removeAttribute("aria-label");
    }
  }
  // Signing out drops the cached state and clears the badge on the spot, rather
  // than leaving the previous account's red number on screen until a round-trip
  // comes back. It also drops the extra challenge slots back to zero, which is
  // the whole reason the challenge strip is repainted here: the next identity
  // must not inherit this one's four dailies.
  window.__ccCritterPassReset = function () {
    _state = null;
    _claimedSet = new Set();
    paintNavBadge();
    // Deliberately does NOT repaint the challenge strip. __ccPassExtraSlots()
    // now answers zero, so the next read reconciles the slots back down on its
    // own, and both callers of this repaint the strip a few lines later anyway.
    // Doing it here would run renderChallengeStrip (which marks today as played
    // and reports the login daily) against the identity being left behind.
  };

  // ── A guest's own ACCOUNT level ──────────────────────────────────────────
  // The server answers a signed-out /api/critterpass/state with level 1,
  // because it has no account to look the level up in. A guest DOES have an
  // account level though: their XP is banked in this browser, and the chip
  // that shows it should be right.
  //
  // It deliberately does NOT touch the pass level. Nobody has a pass level
  // until they unlock the pass, guests included: the climb starts at unlock,
  // so showing a guest pass level 40 would advertise a head start that does
  // not exist. A guest sees the track from Level 1, which is exactly what they
  // would really be buying.
  function accountLevelTotals() {
    const t = _state && _state.levelTotals;
    return Array.isArray(t) && t.length ? t : null;
  }
  function guestLevelFromXp(totalXp) {
    const totals = accountLevelTotals();
    if (!totals) return 1;
    const xp = num(totalXp);
    let lvl = 1;
    for (let i = 0; i < totals.length; i++) {
      if (xp >= num(totals[i])) lvl = i + 1; else break;
    }
    return Math.min(lvl, num(_state && _state.maxLevel, totals.length));
  }
  function isGuestView() {
    return !!(_state && !_state.signedIn && typeof window.__fishIsGuest === "function" && window.__fishIsGuest());
  }
  function applyGuestLevel() {
    if (!isGuestView()) return;
    let xp = 0;
    try {
      const gs = (typeof window.__fishGuestStatsGet === "function") ? (window.__fishGuestStatsGet() || {}) : {};
      xp = num(gs.total_xp);
    } catch (_) { xp = 0; }
    applyAccountLevelFromXp(xp);
  }

  // The ACCOUNT level and its numbers, from an XP figure. It writes the four
  // account fields and nothing else: passLevel / seasonXp are the season's,
  // and no amount of lifetime XP tells you what a pass has earned since it was
  // unlocked. This is the whole reason the two curves have separate fields.
  function applyAccountLevelFromXp(xp) {
    const lvl = guestLevelFromXp(xp);
    const totals = accountLevelTotals() || [];
    const prev = num(totals[lvl - 1]);            // XP that reached this level
    const next = num(totals[lvl], prev);          // XP that reaches the next one
    _state.level = lvl;
    _state.totalXp = num(xp);
    _state.xpIntoLevel = Math.max(0, num(xp) - prev);
    _state.xpForLevel = Math.max(1, next - prev);
  }

  // When the server could not read the account (accountRead:false, a refusing
  // Firestore rather than a signed-out visitor) the Account Level chip would
  // otherwise say 1 to a Level 39 player. The app already loaded that profile,
  // so the chip says what the header says. See the same fallback in
  // js/level-pass.js, which is the page this chip points people at.
  function applyLiveAccountLevel() {
    if (!_state || !_state.signedIn || _state.accountRead !== false) return;
    let xp = null;
    try {
      const st = (typeof window.__fishGetMyStats === "function") ? window.__fishGetMyStats() : null;
      if (st && typeof st === "object") {
        const v = (typeof window.__fishStoredTotalXp === "function")
          ? Number(window.__fishStoredTotalXp(st)) : Number(st.total_xp);
        if (Number.isFinite(v) && v >= 0) xp = Math.floor(v);
      }
    } catch (_) { xp = null; }
    if (xp == null) return;
    applyAccountLevelFromXp(xp);
  }

  // ── Rendering ────────────────────────────────────────────────────────────
  function owned() { return !!(_state && _state.owned); }

  function tierState(t) {
    const lvl = passLevel();
    if (_claimedSet.has(t.id)) return "claimed";
    if (!owned()) return num(t.level) <= lvl ? "waiting" : "locked";
    return num(t.level) <= lvl ? "ready" : "locked";
  }

  function tierFaceHtml(t) {
    // The face of the card: the finale critter's portrait, the minted Critter
    // Coin on a coin tier, the reward's own glyph for everything else. Coins
    // get the real coin art rather than the generic emoji, because this is the
    // same currency the Store, the wallet chip and the trade window all show.
    if (t.type === "avatar" && t.img) {
      return `<img class="ccCP-tier-img" src="${esc(avSrc(t.img))}" alt="" loading="lazy">`;
    }
    if (t.type === "coins") {
      return `<img class="ccCP-tier-coin" src="/critter-coin.png?v=1" alt="" draggable="false" loading="lazy">`;
    }
    return `<span class="ccCP-tier-ico" aria-hidden="true">${esc(t.icon || "🎁")}</span>`;
  }

  function tierCardHtml(t) {
    const st = tierState(t);
    const cls = ["ccCP-tier", "is-" + st];
    if (t.type === "avatar") cls.push("is-finale");
    if (t.type === "daily_slot" || t.type === "weekly_slot") cls.push("is-perk");
    if (_busyTier === t.id) cls.push("is-busy");

    // A guest never reaches "ready": they are not signedIn, so owned() is false
    // and every reached tier is "waiting". That is deliberate, and it is why
    // there is no "sign in to claim" arm here: the purchase card and the foot
    // note both already say an account is what holds the pass, and a third copy
    // on 58 cards would be noise.
    let foot;
    if (st === "claimed") {
      foot = `<span class="ccCP-tier-done">✓ Claimed</span>`;
    } else if (st === "ready") {
      foot = `<button class="ccCP-claim" type="button" data-tier="${esc(t.id)}">Claim</button>`;
    } else if (st === "waiting") {
      // Reached, but the pass is not unlocked. This is the sales pitch: the
      // player is looking at a reward they have already earned the level for.
      foot = `<span class="ccCP-tier-wait">🔓 Unlock to claim</span>`;
    } else {
      const left = xpUntil(num(t.level));
      foot = left == null
        ? `<span class="ccCP-tier-lock">🔒 Level ${esc(t.level)}</span>`
        : `<span class="ccCP-tier-togo"><b>${fmt(left)}</b> XP to go</span>`;
    }

    return `
      <div class="${cls.join(" ")}" data-tier="${esc(t.id)}" data-level="${esc(t.level)}">
        <div class="ccCP-tier-lvl">${esc(t.level)}</div>
        <div class="ccCP-tier-face">${tierFaceHtml(t)}</div>
        <div class="ccCP-tier-label" title="${esc(t.label)}">${esc(t.label)}</div>
        <div class="ccCP-tier-blurb">${esc(t.blurb || "")}</div>
        <div class="ccCP-tier-foot">${foot}</div>
      </div>`;
  }

  // ── The purchase card ────────────────────────────────────────────────────
  // Shown until the pass is unlocked, and it never hides the track: the rail
  // stays underneath, dimmed, with every reward readable. A locked feature you
  // cannot even look at does not sell itself.
  function countOf(type) {
    return ((_state && _state.track) || []).filter(t => t.type === type).length;
  }

  function buyCardHtml() {
    const price = num(_state && _state.price, 4000);
    const coins = num(inventory().coins);
    const short = Math.max(0, price - coins);
    const vouchers = num(inventory().vouchers);
    const guest = isGuestView();
    const signedIn = !!(_state && _state.signedIn);
    const finale = String((_state && _state.finaleAvatarName) || "the finale critter");
    const finaleImg = (_state && _state.finaleAvatar) || "";
    const maxLvl = num(_state && _state.maxLevel, 100);

    // Every number here is derived from the served track and the served pass
    // curve, so a retune moves the sales pitch with it instead of leaving a
    // promise the track no longer keeps. The 30-day line leads because it is
    // the whole reason this track has a curve of its own: on the account's
    // lifetime curve, 100 levels is most of a year. It is a HIGHLIGHT and not
    // a sentence: the paragraph that used to say the same thing in prose was
    // the longest thing on the card and said it twice.
    const days = num(_state && _state.seasonDays, 0);
    const perDay = num(_state && _state.seasonXpPerDay, 0);
    const highlights = [
      ...(days && perDay ? [{ ico: "⏱️", big: "~" + fmt(days) + " days",
        txt: `of play to Level ${fmt(maxLvl)}, at about ${fmt(perDay)} XP a day` }] : []),
      { ico: `<img class="ccCP-hl-coin" src="/critter-coin.png?v=1" alt="" draggable="false">`,
        big: fmt(num(_state && _state.coinTotal)),
        txt: "Critter Coins across the track" },
      { ico: "✨", big: fmt(num(_state && _state.xpTotal)),
        txt: `XP, dropped over ${countOf("xp")} tiers` },
      { ico: "📅", big: "+" + countOf("daily_slot"),
        txt: "daily challenges, every day, for keeps" },
      { ico: "🗝️", big: "+" + countOf("weekly_slot"),
        txt: "weekly challenges, every week, for keeps" },
      { ico: "😀", big: String(countOf("emote")),
        txt: "critter chat emotes" },
      { ico: "⭐", big: "1", txt: finale + " at Level 100" },
    ].map(h => `
      <div class="ccCP-hl">
        <span class="ccCP-hl-ico">${h.ico}</span>
        <span class="ccCP-hl-txt"><b>${esc(h.big)}</b><span>${esc(h.txt)}</span></span>
      </div>`).join("");

    let action;
    if (guest || !signedIn) {
      action = `<div class="ccCP-buy-note">The Critter Pass is bought and kept on an account. Make one (it's free) and it comes with you.</div>`;
    } else if (vouchers > 0) {
      // A Season Pass voucher is the Supporter Tier way in. It costs no coins,
      // and it is spendable on ANY season, so the button says redeem and the
      // note says what redeeming here actually uses up.
      action = `
        <button class="ccCP-buy" type="button" id="ccCP-buy">
          🎟️ Redeem Season Pass Voucher
        </button>
        <div class="ccCP-buy-note">You have <b>${fmt(vouchers)}</b> Season Pass voucher${vouchers === 1 ? "" : "s"}.
          Redeeming here spends one on <b>${esc((_state && _state.seasonName) || "this season")}</b>${vouchers > 1 ? ", and the rest keep for a future season" : ""}.
          No Critter Coins needed.</div>`;
    } else if (short > 0) {
      action = `
        <button class="ccCP-buy" type="button" id="ccCP-buy" disabled>
          Unlock &middot; ${fmt(price)} <img class="cc-coin" src="/critter-coin.png?v=1" alt="Critter Coins" draggable="false">
        </button>
        <div class="ccCP-buy-note">You're <b>${fmt(short)}</b> Critter Coins short. You have ${fmt(coins)}.</div>`;
    } else {
      action = `
        <button class="ccCP-buy" type="button" id="ccCP-buy">
          Unlock &middot; ${fmt(price)} <img class="cc-coin" src="/critter-coin.png?v=1" alt="Critter Coins" draggable="false">
        </button>
        <div class="ccCP-buy-note">You have ${fmt(coins)} Critter Coins. Your Pass Level starts at 1 and climbs on the XP you earn from here: every reward below is yours to claim on the way up.</div>`;
    }

    return `
      <div class="ccCP-buycard">
        <div class="ccCP-buy-main">
          <div class="ccCP-buy-head">
            <span class="ccCP-buy-kicker">${esc((_state && _state.seasonName) || "Critter Pass")}</span>
            <h2 class="ccCP-buy-title">Unlock the Critter Pass</h2>
            <!-- ONE sentence. What the pass costs and what it hands back, and
                 nothing else: the timing, the tier count and the reward mix are
                 all in the highlight row directly underneath, where each is one
                 line the eye can land on instead of a paragraph to read. -->
            <p class="ccCP-buy-sub">
              ${vouchers > 0
                ? `Your voucher covers it`
                : `Spend ${fmt(price)}`} and it pays back
              <b>${fmt(num(_state && _state.coinTotal))}</b> Critter Coins alone.
            </p>
          </div>
          <div class="ccCP-hls">${highlights}</div>
          <div class="ccCP-buy-act">${action}</div>
        </div>
        ${finaleImg ? `<div class="ccCP-buy-art">
          <img src="${esc(avSrc(finaleImg))}" alt="${esc(finale)}" loading="lazy">
          <span>Level 100</span>
        </div>` : ""}
      </div>`;
  }

  function headerHtml() {
    const inv = inventory();
    // The badge, the bar and the "next up" line are all the PASS level. The
    // account level is a chip further down: it gates nothing on this page, and
    // it is only there so "Pass Level 3" on a level-47 account cannot read as a
    // bug. See the XP maths block above for why they are two different curves.
    const lvl = passLevel();
    const into = num(_state && _state.passXpIntoLevel);
    const goal = Math.max(1, num(_state && _state.passXpForLevel, xpPerPassLevel() || 1));
    const pct = Math.max(0, Math.min(100, Math.round((into / goal) * 100)));
    const maxLvl = passMaxLevel();
    const nxt = nextTier();
    const ready = unclaimedReady();
    const acctLvl = num(_state && _state.level, 1);

    // The headline sentence. A player at the cap has no "next thing", and
    // saying "0 XP until nothing" is worse than saying they are finished.
    let nextLine;
    if (!nxt) {
      nextLine = `<span class="ccCP-next-done">Every reward on the Critter Pass is yours. 🐙</span>`;
    } else {
      const left = xpUntil(num(nxt.level));
      const what = `<b>${esc(nxt.label)}</b>`;
      nextLine = left == null
        ? `Next up at Pass Level ${esc(nxt.level)}: ${what}`
        : `<b class="ccCP-next-xp">${fmt(left)} XP</b> until ${what} <span class="ccCP-next-lvl">&middot; Pass Level ${esc(nxt.level)}</span>`;
    }

    let actions;
    if (!owned()) {
      actions = `<span class="ccCP-allclear">Locked</span>`;
    } else if (isGuestView()) {
      actions = `<span class="ccCP-allclear">Rewards are paid into an account</span>`;
    } else if (ready.length) {
      actions = `<button class="ccCP-claimall" type="button" id="ccCP-claimall">Claim ${ready.length} reward${ready.length === 1 ? "" : "s"}</button>`;
    } else {
      actions = `<span class="ccCP-allclear">Nothing to claim</span>`;
    }

    const eD = num(inv.extraDaily), eW = num(inv.extraWeekly);
    const maxD = num(_state && _state.extraDailyMax, 3);
    const maxW = num(_state && _state.extraWeeklyMax, 3);
    const vouchers = num(inv.vouchers);

    return `
      <div class="ccCP-head">
        <div class="ccCP-head-top">
          <div class="ccCP-lvl-badge">
            <span class="ccCP-lvl-word">Pass Level</span>
            <span class="ccCP-lvl-num">${esc(lvl)}</span>
          </div>
          <div class="ccCP-head-mid">
            <div class="ccCP-title">Critter Pass${owned() ? `<span class="ccCP-owned-pill">Unlocked</span>` : ""}</div>
            <div class="ccCP-season">${esc((_state && _state.seasonName) || "")}${seasonLeftHtml()}</div>
            <div class="ccCP-next">${nextLine}</div>
            <div class="ccCP-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}">
              <div class="ccCP-bar-fill" style="width:${pct}%"></div>
            </div>
            <div class="ccCP-bar-txt">${fmt(into)} / ${fmt(goal)} XP to Pass Level ${esc(Math.min(maxLvl, lvl + 1))}</div>
          </div>
          <div class="ccCP-head-actions">${actions}</div>
        </div>
        <div class="ccCP-chips">
          <div class="ccCP-chip">
            <span class="ccCP-chip-ico">🎣</span>
            <span class="ccCP-chip-txt"><b>Account Level ${esc(acctLvl)}</b><span>your lifetime level, on the Level Pass</span></span>
          </div>
          <div class="ccCP-chip">
            <span class="ccCP-chip-ico"><img class="cc-coin" src="/critter-coin.png?v=1" alt="" draggable="false"></span>
            <span class="ccCP-chip-txt"><b>${fmt(inv.coins)}</b><span>Critter Coins</span></span>
          </div>
          <div class="ccCP-chip${eD ? "" : " is-empty"}">
            <span class="ccCP-chip-ico">📅</span>
            <span class="ccCP-chip-txt"><b>+${eD} Daily Challenge${eD === 1 ? "" : "s"}</b><span>${eD} of ${maxD} unlocked</span></span>
          </div>
          <div class="ccCP-chip${eW ? "" : " is-empty"}">
            <span class="ccCP-chip-ico">🗝️</span>
            <span class="ccCP-chip-txt"><b>+${eW} Weekly Challenge${eW === 1 ? "" : "s"}</b><span>${eW} of ${maxW} unlocked</span></span>
          </div>
          ${vouchers > 0 ? `
          <div class="ccCP-chip">
            <span class="ccCP-chip-ico">🎟️</span>
            <span class="ccCP-chip-txt"><b>${fmt(vouchers)} Season Pass Voucher${vouchers === 1 ? "" : "s"}</b><span>${owned()
              ? "spendable on a future season, or tradable" : "redeem one for this season"}</span></span>
          </div>` : ""}
        </div>
      </div>`;
  }

  function render() {
    const root = $("cc-critter-pass-root");
    if (!root) return;
    const perPassLevelFoot = xpPerPassLevel() ? fmt(xpPerPassLevel()) + " XP" : "a fixed amount";

    if (!_state) {
      root.innerHTML = `<div class="ccCP"><div class="ccCP-empty">Loading the Critter Pass…</div></div>`;
      return;
    }
    // A guest sees the whole track, at their own level, with buying off:
    // "here is what it is, an account is what holds it". Only a session with no
    // identity at all (nobody has signed in or started a guest run) gets the
    // sign-in line.
    if (!_state.signedIn && !isGuestView()) {
      root.innerHTML = `<div class="ccCP"><div class="ccCP-empty">Sign in or create an account to unlock the Critter Pass.</div></div>`;
      return;
    }
    applyGuestLevel();
    applyLiveAccountLevel();

    const track = (_state.track || []).slice().sort((a, b) => num(a.level) - num(b.level));
    root.innerHTML = `
      <div class="ccCP${owned() ? " is-owned" : " is-locked"}">
        ${headerHtml()}
        ${owned() ? "" : buyCardHtml()}
        <div class="ccCP-rail-wrap">
          <button class="ccCP-nav ccCP-nav-prev" type="button" id="ccCP-prev" aria-label="Scroll back">‹</button>
          <div class="ccCP-rail" id="ccCP-rail">${track.map(tierCardHtml).join("")}</div>
          <button class="ccCP-nav ccCP-nav-next" type="button" id="ccCP-next" aria-label="Scroll forward">›</button>
        </div>
        <div class="ccCP-foot-note">
          ${isGuestView()
            ? "You are playing as a guest: your level is kept in this browser. The Critter Pass is bought and kept on an account, so make one first, and the XP you earn from then on is what climbs the track."
            : owned()
              ? `Pass Levels are their own climb: ${perPassLevelFoot} of the XP you earn, per level, whatever your account level is. Rewards are yours the moment you reach one, and they never expire.`
              : "Unlock it once and the climb starts there: every reward below comes from the XP you earn afterwards."}
        </div>
      </div>`;

    wire();
    scrollToCurrent();
  }

  // Put the player where they actually are, the way every pass does: their
  // current level just left of centre, so the next few rewards are the ones on
  // screen.
  function scrollToCurrent() {
    const rail = $("ccCP-rail");
    if (!rail) return;
    const lvl = passLevel();
    let target = null;
    for (const card of rail.querySelectorAll(".ccCP-tier")) {
      if (num(card.getAttribute("data-level")) <= lvl) target = card;
    }
    // Nobody has passed a tier yet → show the start, not a blank rail.
    const anchor = target || rail.querySelector(".ccCP-tier");
    if (!anchor) return;
    const left = anchor.offsetLeft - rail.clientWidth * 0.35;
    try { rail.scrollTo({ left: Math.max(0, left), behavior: "auto" }); }
    catch (_) { rail.scrollLeft = Math.max(0, left); }
  }

  // ── Actions ──────────────────────────────────────────────────────────────
  async function buyPass() {
    if (_buying) return;
    const price = num(_state && _state.price, 4000);
    const vouchers = num(inventory().vouchers);
    // A voucher holder never sees the coin price: redeeming spends a thing they
    // were given, not a balance they saved, so the dialog says so and the POST
    // carries voucher:true (the server refuses if the balance is really zero).
    if (vouchers > 0) return redeemVoucher(vouchers);
    // Ask first. This is the biggest single spend in the game, and a mis-tap
    // that empties a wallet is not something a toast can undo.
    //
    // ccPerkModal resolves to { action, selected }, where action is the key of
    // the button pressed (or "cancel" for Escape / the backdrop). A NULL answer
    // means the modal itself never loaded, which must not read as "confirmed":
    // fall back to the browser's own confirm rather than spending 4,000 coins
    // on a dialog nobody saw.
    let answer = null;
    try { answer = bridge().modal ? await bridge().modal({
      icon: "🎟️",
      title: "Unlock the Critter Pass?",
      body: `This spends ${fmt(price)} Critter Coins, once. Your Pass Level starts at 1 `
          + `and climbs on the XP you earn from here${xpPerPassLevel()
              ? ` (a flat ${fmt(xpPerPassLevel())} XP a level, about `
                + `${fmt(num(_state && _state.seasonDays, 30))} days to Level ${fmt(passMaxLevel())})`
              : ""}`
          + `, and the track pays back ${fmt(num(_state && _state.coinTotal))} Critter Coins on the way up.`,
      actions: [
        { key: "cancel", label: "Not yet" },
        { key: "confirm", label: `Spend ${fmt(price)}`, primary: true },
      ],
    }) : null; } catch (_) { answer = null; }
    const go = (answer && typeof answer === "object")
      ? answer.action === "confirm"
      : window.confirm(`Unlock the Critter Pass for ${fmt(price)} Critter Coins?`);
    if (!go) return;

    _buying = true;
    const btn = $("ccCP-buy");
    if (btn) { btn.disabled = true; btn.textContent = "Unlocking…"; }
    const res = await post("buy", {});
    _buying = false;
    if (res && res.ok) {
      toast("🎉 Critter Pass unlocked. You are at Pass Level 1: every reward on the track is now yours to climb for.", "good");
      await sync();
      afterGrant();
    } else {
      toast(msgFor(res), "warn");
      // "Already owned" means this tab was stale: resync so the card stops
      // offering something the account already has.
      if (res && res.error === "already_owned") await sync();
    }
    render();
  }

  // Redeem one Season Pass voucher for THIS season. Kept separate from the coin
  // purchase so neither dialog can ever quote the other's cost.
  async function redeemVoucher(vouchers) {
    const season = String((_state && _state.seasonName) || "this season");
    let answer = null;
    try { answer = bridge().modal ? await bridge().modal({
      icon: "🎟️",
      title: "Redeem a Season Pass voucher?",
      body: `This spends 1 of your ${fmt(vouchers)} voucher${vouchers === 1 ? "" : "s"} `
          + `on ${season} and unlocks the Critter Pass straight away. `
          + `Your Pass Level starts at 1 and climbs on the XP you earn from here. `
          + `${vouchers > 1 ? `The other ${fmt(vouchers - 1)} keep for a future season. ` : ""}`
          + `No Critter Coins are spent.`,
      actions: [
        { key: "cancel", label: "Not yet" },
        { key: "confirm", label: "Redeem voucher", primary: true },
      ],
    }) : null; } catch (_) { answer = null; }
    const go = (answer && typeof answer === "object")
      ? answer.action === "confirm"
      : window.confirm(`Redeem a Season Pass voucher for ${season}?`);
    if (!go) return;

    _buying = true;
    const btn = $("ccCP-buy");
    if (btn) { btn.disabled = true; btn.textContent = "Redeeming…"; }
    const res = await post("buy", { voucher: true });
    _buying = false;
    if (res && res.ok) {
      toast("🎟️ Voucher redeemed. The Critter Pass is unlocked at Pass Level 1: every reward on the track is now yours to climb for.", "good");
      await sync();
      afterGrant();
    } else {
      toast(msgFor(res), "warn");
      if (res && (res.error === "already_owned" || res.error === "no_vouchers")) await sync();
    }
    render();
  }

  async function claimTier(tierId) {
    if (!tierId || _busyTier) return;
    _busyTier = tierId;
    render();
    const res = await post("claim", { tier: tierId });
    _busyTier = "";
    if (res && res.ok) {
      announce(res.granted);
      // Re-read rather than patch: the server just moved coins, XP, inventory
      // and possibly an unlocked_icons array, and it is the only thing that
      // knows what actually landed.
      await sync();
      afterGrant();
    } else {
      toast(msgFor(res), "warn");
      if (res && res.error === "already_claimed") await sync();
    }
    render();
  }

  // The server pays at most CLAIM_ALL_LIMIT tiers per request, because each
  // tier is its own Firestore transaction and a hundred of them in one request
  // is a request that can outlive its own timeout. So this LOOPS while the
  // server says there is more, accumulating as it goes and reporting ONCE at
  // the end: four rounds covers a full 100-tier track, and the rest is headroom
  // for the XP drops that unlock further tiers mid-sweep.
  const CLAIM_ALL_ROUNDS = 8;

  async function claimAll() {
    const btn = $("ccCP-claimall");
    if (btn) { btn.disabled = true; btn.textContent = "Claiming…"; }

    let total = 0, coins = 0, xp = 0, failed = null;
    // Tier → the code it refused with, NOT a bare set of codes. A tier can
    // refuse in one batch and succeed in the next (claiming the Level 100
    // critter gives the emote tiers something new to draw from, and an XP drop
    // can unlock a tier that was locked), so a refusal has to be cancellable
    // by a later payout. Reporting "you already have every emote" about a tier
    // that then worked is a warning about nothing.
    const refusals = new Map();
    for (let round = 0; round < CLAIM_ALL_ROUNDS; round++) {
      const res = await post("claim-all", {});
      if (!res || !res.ok) { failed = res; break; }
      const got = res.claimed || [];
      total += num(res.count);
      coins += got.reduce((sum, r) => sum + num(r.granted && r.granted.coins), 0);
      xp    += got.reduce((sum, r) => sum + num(r.granted && r.granted.xp), 0);
      (res.skipped || []).forEach(s => refusals.set(String(s.tier || ""), String(s.error || "error")));
      got.forEach(r => refusals.delete(String(r.tier || "")));
      if (!res.more || !num(res.count)) break;
      // Show it climbing rather than hanging on one spinner.
      if (btn) btn.textContent = `Claiming… ${fmt(total)}`;
    }

    if (failed) {
      toast(msgFor(failed), "warn");
    } else {
      const bits = [];
      if (coins) bits.push(`+${fmt(coins)} Critter Coins`);
      if (xp) bits.push(`+${fmt(xp)} XP`);
      toast(total
        ? `Claimed ${fmt(total)} reward${total === 1 ? "" : "s"}${bits.length ? " · " + bits.join(" · ") : ""}`
        : "Nothing new to claim just yet.", total ? "good" : "info");
    }
    // A tier that refused (a full hoard, no backgrounds left) is reported
    // honestly instead of being swallowed: the rest still paid out. Deduped by
    // CODE at the last moment, because ten emote tiers with nothing left to
    // give is one thing to tell the player, not ten identical toasts.
    new Set(refusals.values()).forEach(code => toast(msgFor({ error: code }), "warn"));
    if (total || !failed) { await sync(); afterGrant(); }
    render();
  }

  function announce(granted) {
    if (!granted) return;
    const t = String(granted.type || "");
    if (t === "coins")            toast(`+${fmt(granted.coins)} Critter Coins`, "good");
    else if (t === "xp")          toast(`✨ +${fmt(granted.xp)} XP`, "good");
    else if (t === "shield")      toast("🛡️ Streak Shield added, it covers one missed day.", "good");
    else if (t === "boost")       toast("⚡ XP Boost added: activate it on the Level Pass whenever you want it.", "good");
    else if (t === "swap")        toast("🔄 Weekly Swap added: spend it for a week of free swaps.", "good");
    else if (t === "background")  toast("🖼️ New background unlocked: equip it in the Avatar Gallery.", "good");
    else if (t === "emote")       toast("😀 New critter emote unlocked for game chat.", "good");
    else if (t === "daily_slot")  toast(`📅 An extra daily challenge, from now on. You now get ${num(granted.slots) + 3} a day.`, "good");
    else if (t === "weekly_slot") toast(`🗝️ An extra weekly challenge, from now on. You now get ${num(granted.slots) + 3} a week.`, "good");
    else if (t === "avatar")      toast(`⭐ ${granted.critter || "Your critter"} unlocked: equip it in the Avatar Gallery.`, "good");
  }

  // Coins, XP, backgrounds, emotes and icons all live on the account document
  // the rest of the app renders from, so a payout has to push the app to
  // re-read it, otherwise the header keeps painting the old balance.
  function afterGrant() {
    try { bridge().onGranted && bridge().onGranted(); } catch (_) {}
  }

  function wire() {
    const root = $("cc-critter-pass-root");
    if (!root) return;
    const rail = $("ccCP-rail");

    root.querySelectorAll(".ccCP-claim").forEach(btn => {
      btn.addEventListener("click", () => claimTier(btn.getAttribute("data-tier")));
    });
    const all = $("ccCP-claimall");
    if (all) all.addEventListener("click", claimAll);
    const buy = $("ccCP-buy");
    if (buy && !buy.disabled) buy.addEventListener("click", buyPass);

    const step = () => Math.max(240, (rail ? rail.clientWidth : 600) * 0.8);
    const prev = $("ccCP-prev");
    const next = $("ccCP-next");
    if (prev && rail) prev.addEventListener("click", () => rail.scrollBy({ left: -step(), behavior: "smooth" }));
    if (next && rail) next.addEventListener("click", () => rail.scrollBy({ left: step(), behavior: "smooth" }));
  }

  // ── Entry points ─────────────────────────────────────────────────────────
  window.__ccCritterPassRender = async function () {
    render();                 // paint the loading state immediately
    await sync();
    render();
  };

  // Keep the cached slot counts fresh even when the page is never opened:
  // __ccPassExtraSlots() is read by the challenge strip on every repaint, and a
  // stale "no extra slots" would quietly cost a player the tier they claimed.
  // One read on sign-in is enough; every claim resyncs on its own.
  window.__ccCritterPassPrime = function () {
    sync().catch(() => {});
  };
})();
