/* Currents and Critters: Level Pass (self-contained module).
 *
 * Renders the whole "Level Pass" Player-Home page into #cc-level-pass-root:
 * a horizontal reward track laid over the existing 1–100 level curve, the
 * consumables it pays out, and the "how much XP until the next thing" line
 * the whole page is really built around.
 *
 *   window.__ccLevelPassRender()     the page itself
 *   window.__ccLevelPassSync()       re-read state from the server
 *   window.__ccPassBoost()           { active, until, percent, mult }, the XP
 *                                    multiplier every XP path multiplies by
 *   window.__ccPassRerollActive(ms)  is weekly-challenge swapping unlimited
 *                                    for the week starting at `ms`?
 *   window.__ccPassInventory()       cached shields / boosts / swaps
 *
 * NOTHING here decides anything. The server owns the track, re-derives the
 * player's level from their own stored total_xp inside every payout, and
 * writes the goods. This file renders that state and sends intents. Editing a
 * number in here changes what one player sees for one paint and is then thrown
 * away by the server's own re-check (level_pass_server.py).
 *
 * WHY THE BOOST IS READ FROM HERE
 * XP itself is written by the client (the game-end save and __fishGrantXp both
 * increment stats.total_xp), exactly as the Prestige bonus already is. So the
 * boost's EXISTENCE is server-owned, a claim, then an activation, both
 * transactional, while the multiplier is applied client-side beside the
 * Prestige one. window.__ccPassBoost() is that seam, and it is deliberately
 * synchronous and cached: an XP grant must never wait on a network round-trip.
 */
(function () {
  "use strict";

  function bridge() { return window.__ccLevelPass; }
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
    try { return unwrap(await b.post("/api/pass/" + action, body)); }
    catch (_) { return { ok: false, error: "offline" }; }
  }

  // Server error code → a sentence a player can act on. Mirrors
  // level_pass_server.ERROR_MESSAGES so the toast and the server agree; the
  // server also sends `message`, and that one wins when it is there.
  const MESSAGES = {
    unauthorized: "Sign in to use the Level Pass.",
    already_claimed: "You've already claimed that reward.",
    level_locked: "You haven't reached that level yet.",
    shields_full: "Your Streak Shields are full: spend one first.",
    boosts_full: "You're holding as many XP Boosts as you can: use one first.",
    rerolls_full: "You're holding as many Weekly Swaps as you can: use one first.",
    backgrounds_full: "You already own every background. This one is waiting for the next batch.",
    stickers_full: "You already have a sticker for every critter you own.",
    boost_running: "An XP Boost is already running.",
    no_boost: "You don't have an XP Boost to activate.",
    no_token: "You don't have a Weekly Swap token.",
    already_active: "Weekly Swaps are already unlimited for you this week.",
    offline: "Couldn't reach the server. Nothing was claimed: please try again.",
    unavailable: "The Level Pass didn't finish loading: please refresh the page.",
    server_error: "Something went wrong. Nothing was claimed: please try again.",
  };
  const msgFor = (res) => (res && res.message)
    || MESSAGES[String((res && res.error) || "")]
    || MESSAGES.server_error;

  // ── State ────────────────────────────────────────────────────────────────
  // `_state` is the last SERVER answer. Everything paints from it, and nothing
  // else writes to it, a claim updates it by re-reading, never by guessing
  // what the server would have done.
  let _state = null;
  let _loading = false;
  let _claimedSet = new Set();
  let _busyTier = "";       // tier id mid-claim, so its button can't double-fire
  let _tickTimer = null;    // repaints the boost countdown

  function inventory() {
    return (_state && _state.inventory) || {
      shields: 0, boosts: 0, rerolls: 0, boostUntil: 0,
      boostActive: false, boostPercent: 20, rerollWeek: 0, coins: 0,
    };
  }
  window.__ccPassInventory = inventory;

  // THE XP SEAM. Synchronous and cached on purpose: every XP grant calls this,
  // and none of them can afford to await a request. An unloaded pass reports
  // "no boost", which is the safe direction, a missed boost is a smaller
  // wrong than XP nobody earned.
  window.__ccPassBoost = function () {
    const inv = inventory();
    const until = num(inv.boostUntil);
    const active = until > Date.now();
    const pct = num(inv.boostPercent, 20);
    return {
      active,
      until: active ? until : 0,
      percent: active ? pct : 0,
      // The multiplier itself, so callers never re-derive it from a percentage
      // and land on a different number than the card promised.
      mult: active ? 1 + (pct / 100) : 1,
    };
  };

  // Weekly-challenge swapping is unlimited for the week a token was spent on.
  // The week is the client's own local Monday-midnight, because that is what
  // the weekly challenges themselves roll on.
  window.__ccPassRerollActive = function (weekStartMs) {
    const week = num(weekStartMs, -1);
    return week > 0 && num(inventory().rerollWeek) === week;
  };

  async function sync() {
    if (_loading) return _state;
    _loading = true;
    try {
      const res = await post("state", {});
      if (res && res.ok) {
        _state = res;
        _claimedSet = new Set(Array.isArray(res.claimed) ? res.claimed : []);
      }
    } finally { _loading = false; }
    // EVERY path that changes what is claimable ends in a sync: the boot
    // prime, opening the page, a claim, a claim-all. Repainting the badge
    // here and nowhere else is what stops the number drifting from the track.
    paintNavBadge();
    return _state;
  }
  window.__ccLevelPassSync = async function () {
    await sync();
    if (isOpen()) render();
    return _state;
  };

  function isOpen() {
    const root = $("cc-level-pass-root");
    return !!(root && root.offsetParent !== null);
  }

  // ── XP maths ─────────────────────────────────────────────────────────────
  // levelTotals[i] is the cumulative total_xp needed to REACH level i+1. It is
  // served by the pass endpoint rather than copied into this file, so the
  // "N XP to go" on a card is computed from the same table the server grants
  // levels from.
  function levelTotals() {
    const t = _state && _state.levelTotals;
    return Array.isArray(t) && t.length ? t : null;
  }
  function xpToReach(level) {
    const totals = levelTotals();
    if (!totals) return null;
    const idx = Math.max(1, Math.min(totals.length, Math.floor(level))) - 1;
    return num(totals[idx]);
  }
  // XP the player still needs before `level` is reached. null when the curve
  // has not loaded, the caller must then say nothing rather than say zero.
  function xpUntil(level) {
    const need = xpToReach(level);
    if (need == null) return null;
    return Math.max(0, need - num(_state && _state.totalXp));
  }

  // ── The next thing waiting ───────────────────────────────────────────────
  // "How much XP till the next thing", the nearest tier above the player's
  // level, claimable or milestone alike. A milestone counts: the critter at 50
  // IS the next thing when you are level 49.
  function nextTier() {
    const track = (_state && _state.track) || [];
    const lvl = num(_state && _state.level, 1);
    let best = null;
    for (const t of track) {
      if (num(t.level) <= lvl) continue;
      if (!best || num(t.level) < num(best.level)) best = t;
    }
    return best;
  }

  function unclaimedReady() {
    const track = (_state && _state.track) || [];
    const lvl = num(_state && _state.level, 1);
    return track.filter(t => t.claimable && num(t.level) <= lvl && !_claimedSet.has(t.id));
  }

  // ── The sidebar's red badge ──────────────────────────────────────────────
  // The same .ph-snav-badge the Messages tab uses, so an unclaimed reward
  // reads exactly like an unread message.
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
      reroll: num(caps.rerolls, 3) - num(inv.rerolls),
    };
    return unclaimedReady().filter(t => {
      const left = room[String(t.type)];
      if (left === undefined) return true;   // coins, stickers, backgrounds
      if (left <= 0) return false;
      room[String(t.type)] = left - 1;       // two shield tiers, one slot free
      return true;
    });
  }

  // Paint it. Signed out, still loading, or nothing waiting all mean "hide",
  // never a stale number, and never a lingering 0.
  function paintNavBadge() {
    const el = $("snav-levelpass-badge");
    if (!el) return;
    let n = 0;
    try { n = (_state && _state.signedIn) ? claimableNow().length : 0; } catch (_) { n = 0; }
    if (n > 0) {
      el.textContent = n > 99 ? "99+" : String(n);
      el.style.display = "";
      el.setAttribute("aria-label", n + " Level Pass reward" + (n === 1 ? "" : "s") + " ready to claim");
    } else {
      el.textContent = "";
      el.style.display = "none";
      el.removeAttribute("aria-label");
    }
  }
  // Signing out drops the cached state and clears the badge on the spot,
  // rather than leaving the previous account's red number on screen until a
  // round-trip comes back.
  window.__ccLevelPassReset = function () {
    _state = null;
    _claimedSet = new Set();
    paintNavBadge();
  };

  // ── A guest's own level ──────────────────────────────────────────────────
  // The server answers a signed-out /api/pass/state with the full track and
  // level 1, because it has no account to look the level up in. A guest DOES
  // have a level though: their XP is banked in this browser, and the page used
  // to refuse to draw at all rather than use it, so "your level and rewards
  // show here" was a promise the page then broke.
  //
  // levelTotals[i] is the cumulative XP to REACH level i+1, the same table the
  // server grants levels from, so deriving from it here cannot disagree with
  // what an account would be shown.
  // totals[i] is the cumulative XP to REACH level i + 1 (index 0 is level 1,
  // which costs nothing), the same convention xpToReach() reads it with.
  function guestLevelFromXp(totalXp) {
    const totals = levelTotals();
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
  // Fold the guest's local XP into the state the whole page reads, so every
  // level test below (tier states, "N XP to go", the header bar) works off one
  // number and none of them need to know who is looking.
  function applyGuestLevel() {
    if (!isGuestView()) return;
    let xp = 0;
    try {
      const gs = (typeof window.__fishGuestStatsGet === "function") ? (window.__fishGuestStatsGet() || {}) : {};
      xp = num(gs.total_xp);
    } catch (_) { xp = 0; }
    applyLevelFromXp(xp);
  }

  // The one place the level and its bar are written from an XP number, so the
  // guest path and the fallback below cannot drift into disagreeing about
  // which end of levelTotals a level starts at.
  function applyLevelFromXp(xp) {
    const lvl = guestLevelFromXp(xp);
    const totals = levelTotals() || [];
    const prev = num(totals[lvl - 1]);            // XP that reached this level
    const next = num(totals[lvl], prev);          // XP that reaches the next one
    _state.level = lvl;
    _state.totalXp = num(xp);
    _state.xpIntoLevel = Math.max(0, num(xp) - prev);
    _state.xpForLevel = Math.max(1, next - prev);
  }

  // ── WHEN THE SERVER COULD NOT READ THE ACCOUNT ───────────────────────────
  // state_payload() sends level 1 when its Firestore read throws, because
  // there is nothing else to put in the field, and it flags that with
  // accountRead:false. Painting that 1 is how a Level 39 player spent a day
  // being told the Level Pass thought they were Level 1: Firestore's daily
  // quota ran out, every read refused, and an unreadable account looked
  // exactly like a brand-new one.
  //
  // The app itself is not in the dark. It loaded the profile at sign-in and is
  // painting that level in the header right now, so the pass shows the SAME
  // level rather than a number it knows is wrong. Nothing is claimable off
  // this: the claim is a separate request that re-reads the account inside its
  // own transaction, so the worst case is a Claim button whose server refuses
  // it, and the best case is the page telling the truth during an outage.
  function liveAccountXp() {
    try {
      const st = (typeof window.__fishGetMyStats === "function") ? window.__fishGetMyStats() : null;
      if (!st || typeof st !== "object") return null;
      const xp = (typeof window.__fishStoredTotalXp === "function")
        ? Number(window.__fishStoredTotalXp(st)) : Number(st.total_xp);
      return (Number.isFinite(xp) && xp >= 0) ? Math.floor(xp) : null;
    } catch (_) { return null; }
  }
  function applyLiveAccountLevel() {
    // Only when the server SAID it could not read: a successful read is
    // authoritative even when it is lower than the browser's copy (an XP write
    // that has not landed yet is the server's to reconcile, not ours).
    if (!_state || !_state.signedIn || _state.accountRead !== false) return;
    const xp = liveAccountXp();
    if (xp == null) return;
    applyLevelFromXp(xp);
  }

  // ── Rendering ────────────────────────────────────────────────────────────
  function tierState(t) {
    const lvl = num(_state && _state.level, 1);
    if (!t.claimable) return num(t.level) <= lvl ? "earned" : "locked";
    if (_claimedSet.has(t.id)) return "claimed";
    return num(t.level) <= lvl ? "ready" : "locked";
  }

  function tierCardHtml(t) {
    const st = tierState(t);
    const isMilestone = t.type === "critter";
    const cls = ["ccLP-tier", "is-" + st];
    if (isMilestone) cls.push("is-milestone");
    if (_busyTier === t.id) cls.push("is-busy");

    // The face of the card: a critter portrait for milestones, the minted
    // Critter Coin for coin tiers, the reward's own glyph for everything else.
    // Coins get the real coin art rather than the generic emoji, because this
    // is the same currency the Store, the wallet chip and the trade window all
    // show, and it should look like one thing in all four places.
    const face = isMilestone && t.img
      ? `<img class="ccLP-tier-img" src="${esc(avSrc(t.img))}" alt="" loading="lazy">`
      : t.type === "coins"
        ? `<img class="ccLP-tier-coin" src="/critter-coin.png?v=1" alt="" draggable="false" loading="lazy">`
        : `<span class="ccLP-tier-ico" aria-hidden="true">${esc(t.icon || "🎁")}</span>`;

    // "N XP to go" on every locked tier, the thing the whole page is for.
    let foot;
    if (st === "ready" && isGuestView()) {
      // A guest can reach a tier but not hold what is in it: the reward is
      // paid into an account. Say that on the card, rather than a Claim button
      // that can only fail.
      foot = `<span class="ccLP-tier-lock">Sign in to claim</span>`;
    } else if (st === "ready") {
      foot = `<button class="ccLP-claim" type="button" data-tier="${esc(t.id)}">Claim</button>`;
    } else if (st === "claimed") {
      foot = `<span class="ccLP-tier-done">✓ Claimed</span>`;
    } else if (st === "earned") {
      foot = `<span class="ccLP-tier-done">✓ Unlocked</span>`;
    } else {
      const left = xpUntil(num(t.level));
      foot = left == null
        ? `<span class="ccLP-tier-lock">🔒 Level ${esc(t.level)}</span>`
        : `<span class="ccLP-tier-togo"><b>${fmt(left)}</b> XP to go</span>`;
    }

    return `
      <div class="${cls.join(" ")}" data-tier="${esc(t.id)}" data-level="${esc(t.level)}">
        <div class="ccLP-tier-lvl">${esc(t.level)}</div>
        <div class="ccLP-tier-face">${face}</div>
        <div class="ccLP-tier-label" title="${esc(t.label)}">${esc(t.label)}</div>
        <div class="ccLP-tier-blurb">${esc(t.blurb || "")}</div>
        <div class="ccLP-tier-foot">${foot}</div>
      </div>`;
  }

  function boostChipHtml() {
    const inv = inventory();
    const b = window.__ccPassBoost();
    if (b.active) {
      const left = Math.max(0, b.until - Date.now());
      const h = Math.floor(left / 3600000);
      const m = Math.floor((left % 3600000) / 60000);
      const s = Math.floor((left % 60000) / 1000);
      // Seconds only appear in the last minute, so the chip is not a twitching
      // stopwatch for 24 hours.
      const clock = h > 0 ? `${h}h ${m}m` : (m > 0 ? `${m}m` : `${s}s`);
      return `<div class="ccLP-chip is-live" title="Every XP source is boosted while this runs">
          <span class="ccLP-chip-ico">⚡</span>
          <span class="ccLP-chip-txt"><b>+${esc(b.percent)}% XP</b><span>${esc(clock)} left</span></span>
        </div>`;
    }
    const held = num(inv.boosts);
    return `<div class="ccLP-chip${held ? "" : " is-empty"}">
        <span class="ccLP-chip-ico">⚡</span>
        <span class="ccLP-chip-txt"><b>${held} XP Boost${held === 1 ? "" : "s"}</b><span>+${esc(num(inv.boostPercent, 20))}% for ${esc(num(_state && _state.boostHours, 24))}h</span></span>
        ${held ? `<button class="ccLP-chip-btn" type="button" id="ccLP-boost-btn">Activate</button>` : ""}
      </div>`;
  }

  function swapChipHtml() {
    const inv = inventory();
    const held = num(inv.rerolls);
    const week = (typeof window.__ccWeekStartMs === "function") ? window.__ccWeekStartMs() : 0;
    const live = week > 0 && num(inv.rerollWeek) === week;
    if (live) {
      return `<div class="ccLP-chip is-live" title="Swap as many weekly challenges as you like until Monday">
          <span class="ccLP-chip-ico">🔄</span>
          <span class="ccLP-chip-txt"><b>Unlimited Swaps</b><span>until Monday</span></span>
        </div>`;
    }
    return `<div class="ccLP-chip${held ? "" : " is-empty"}">
        <span class="ccLP-chip-ico">🔄</span>
        <span class="ccLP-chip-txt"><b>${held} Weekly Swap${held === 1 ? "" : "s"}</b><span>unlimited swaps for a week</span></span>
        ${held ? `<button class="ccLP-chip-btn" type="button" id="ccLP-swap-btn">Use one</button>` : ""}
      </div>`;
  }

  function headerHtml() {
    const inv = inventory();
    const lvl = num(_state && _state.level, 1);
    const into = num(_state && _state.xpIntoLevel);
    const goal = Math.max(1, num(_state && _state.xpForLevel, 1));
    const pct = Math.max(0, Math.min(100, Math.round((into / goal) * 100)));
    const maxLvl = num(_state && _state.maxLevel, 100);
    const nxt = nextTier();
    const ready = unclaimedReady();

    // The headline sentence. A player at the cap has no "next thing", and
    // saying "0 XP until nothing" is worse than saying they are finished.
    let nextLine;
    if (!nxt) {
      nextLine = `<span class="ccLP-next-done">Every reward on the track is yours. 🐙</span>`;
    } else {
      const left = xpUntil(num(nxt.level));
      const what = nxt.type === "critter"
        ? `the <b>${esc(nxt.critter || nxt.label)}</b>`
        : `<b>${esc(nxt.label)}</b>`;
      nextLine = left == null
        ? `Next up at Level ${esc(nxt.level)}: ${what}`
        : `<b class="ccLP-next-xp">${fmt(left)} XP</b> until ${what} <span class="ccLP-next-lvl">· Level ${esc(nxt.level)}</span>`;
    }

    const shields = num(inv.shields);
    return `
      <div class="ccLP-head">
        <div class="ccLP-head-top">
          <div class="ccLP-lvl-badge">
            <span class="ccLP-lvl-word">Level</span>
            <span class="ccLP-lvl-num">${esc(lvl)}</span>
          </div>
          <div class="ccLP-head-mid">
            <div class="ccLP-title">Level Pass</div>
            <div class="ccLP-next">${nextLine}</div>
            <div class="ccLP-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}">
              <div class="ccLP-bar-fill" style="width:${pct}%"></div>
            </div>
            <div class="ccLP-bar-txt">${fmt(into)} / ${fmt(goal)} XP to Level ${esc(Math.min(maxLvl, lvl + 1))}</div>
          </div>
          <div class="ccLP-head-actions">
            ${isGuestView()
              ? `<span class="ccLP-allclear">Rewards are paid into an account</span>`
              : ready.length
                ? `<button class="ccLP-claimall" type="button" id="ccLP-claimall">Claim ${ready.length} reward${ready.length === 1 ? "" : "s"}</button>`
                : `<span class="ccLP-allclear">Nothing to claim</span>`}
          </div>
        </div>
        <div class="ccLP-chips">
          <div class="ccLP-chip">
            <span class="ccLP-chip-ico"><img class="cc-coin" src="/critter-coin.png?v=1" alt="" draggable="false"></span>
            <span class="ccLP-chip-txt"><b>${fmt(inv.coins)}</b><span>Critter Coins</span></span>
          </div>
          <div class="ccLP-chip${shields ? "" : " is-empty"}">
            <span class="ccLP-chip-ico">🛡️</span>
            <span class="ccLP-chip-txt"><b>${shields} Streak Shield${shields === 1 ? "" : "s"}</b><span>covers a missed day</span></span>
          </div>
          ${boostChipHtml()}
          ${swapChipHtml()}
        </div>
      </div>`;
  }

  function render() {
    const root = $("cc-level-pass-root");
    if (!root) return;

    if (!_state) {
      root.innerHTML = `<div class="ccLP"><div class="ccLP-empty">Loading your Level Pass…</div></div>`;
      return;
    }
    // A guest sees the whole track, at their own level, with claiming off:
    // "your level and rewards show here, claiming them needs an account" is
    // what the tab promises, and this is that promise kept. Only a session
    // with no identity at all (nobody has signed in or started a guest run)
    // gets the sign-in line.
    if (!_state.signedIn && !isGuestView()) {
      root.innerHTML = `<div class="ccLP"><div class="ccLP-empty">Sign in or create an account to start earning Level Pass rewards.</div></div>`;
      return;
    }
    applyGuestLevel();
    applyLiveAccountLevel();

    const track = (_state.track || []).slice().sort((a, b) => num(a.level) - num(b.level));
    root.innerHTML = `
      <div class="ccLP">
        ${headerHtml()}
        <div class="ccLP-rail-wrap">
          <button class="ccLP-nav ccLP-nav-prev" type="button" id="ccLP-prev" aria-label="Scroll back">‹</button>
          <div class="ccLP-rail" id="ccLP-rail">${track.map(tierCardHtml).join("")}</div>
          <button class="ccLP-nav ccLP-nav-next" type="button" id="ccLP-next" aria-label="Scroll forward">›</button>
        </div>
        <div class="ccLP-foot-note">
          ${isGuestView()
            ? "You are playing as a guest: your level is kept in this browser. Rewards are paid into an account, so make one and everything you have earned comes with you."
            : "Rewards are yours the moment you reach the level: claim them whenever you like, they never expire."}
        </div>
      </div>`;

    wire();
    scrollToCurrent();
    startTick();
  }

  // Put the player where they actually are, the way every pass does: their
  // current level just left of centre, so the next few rewards are the ones on
  // screen.
  function scrollToCurrent() {
    const rail = $("ccLP-rail");
    if (!rail) return;
    const lvl = num(_state && _state.level, 1);
    let target = null;
    for (const card of rail.querySelectorAll(".ccLP-tier")) {
      if (num(card.getAttribute("data-level")) <= lvl) target = card;
    }
    // Nobody has passed a tier yet → show the start, not a blank rail.
    const anchor = target || rail.querySelector(".ccLP-tier");
    if (!anchor) return;
    const left = anchor.offsetLeft - rail.clientWidth * 0.35;
    try { rail.scrollTo({ left: Math.max(0, left), behavior: "auto" }); }
    catch (_) { rail.scrollLeft = Math.max(0, left); }
  }

  // The boost chip counts down, so it has to repaint, but only while the page
  // is actually on screen, and only once every 30s until the final stretch.
  function startTick() {
    stopTick();
    const b = window.__ccPassBoost();
    if (!b.active) return;
    const every = (b.until - Date.now()) > 120000 ? 30000 : 1000;
    _tickTimer = setInterval(() => {
      if (!isOpen()) { stopTick(); return; }
      if (!window.__ccPassBoost().active) { stopTick(); render(); return; }
      const chips = document.querySelector(".ccLP-chips");
      if (!chips) { stopTick(); return; }
      // Repaint ONLY the chip: re-rendering the page would throw away the
      // player's scroll position in the rail every single tick.
      const holder = document.createElement("div");
      holder.innerHTML = boostChipHtml();
      const live = chips.querySelector(".ccLP-chip.is-live");
      if (live && holder.firstElementChild) live.replaceWith(holder.firstElementChild);
    }, every);
  }
  function stopTick() {
    if (_tickTimer) { clearInterval(_tickTimer); _tickTimer = null; }
  }

  // ── Actions ──────────────────────────────────────────────────────────────
  async function claimTier(tierId) {
    if (!tierId || _busyTier) return;
    _busyTier = tierId;
    render();
    const res = await post("claim", { tier: tierId });
    _busyTier = "";
    if (res && res.ok) {
      announce(res.granted);
      // Re-read rather than patch: the server just moved coins, inventory and
      // possibly an unlocked_backgrounds array, and it is the only thing that
      // knows what actually landed.
      await sync();
      afterGrant();
    } else {
      toast(msgFor(res), "warn");
      // "Already claimed" means this tab was stale: resync so the button
      // stops offering something that is gone.
      if (res && res.error === "already_claimed") await sync();
    }
    render();
  }

  async function claimAll() {
    const btn = $("ccLP-claimall");
    if (btn) { btn.disabled = true; btn.textContent = "Claiming…"; }
    const res = await post("claim-all", {});
    if (res && res.ok) {
      const n = num(res.count);
      const coins = (res.claimed || []).reduce(
        (sum, r) => sum + num(r.granted && r.granted.coins), 0);
      toast(n
        ? `Claimed ${n} reward${n === 1 ? "" : "s"}${coins ? ` · +${fmt(coins)} Critter Coins` : ""}`
        : "Nothing new to claim just yet.", n ? "good" : "info");
      // A tier that refused (a full hoard, no backgrounds left) is reported
      // honestly instead of being swallowed, the rest still paid out.
      (res.skipped || []).forEach(s => toast(msgFor({ error: s.error }), "warn"));
      await sync();
      afterGrant();
    } else {
      toast(msgFor(res), "warn");
    }
    render();
  }

  function announce(granted) {
    if (!granted) return;
    const t = String(granted.type || "");
    if (t === "coins")           toast(`+${fmt(granted.coins)} Critter Coins`, "good");
    else if (t === "shield")     toast("🛡️ Streak Shield added, it covers one missed day.", "good");
    else if (t === "boost")      toast("⚡ XP Boost added: activate it whenever you want it.", "good");
    else if (t === "reroll")     toast("🔄 Weekly Swap added: spend it for a week of free swaps.", "good");
    else if (t === "background") toast("🖼️ New background unlocked: equip it in the Avatar Gallery.", "good");
    else if (t === "sticker")    toast("🎴 New critter sticker unlocked for game chat.", "good");
  }

  // Coins, backgrounds and stickers all live on the account document the rest
  // of the app renders from, so a payout has to push the app to re-read it,
  // otherwise the header keeps painting the old balance.
  function afterGrant() {
    try { bridge().onGranted && bridge().onGranted(); } catch (_) {}
  }

  async function activateBoost() {
    const btn = $("ccLP-boost-btn");
    if (btn) btn.disabled = true;
    const res = await post("boost", {});
    if (res && res.ok) {
      toast(`⚡ XP Boost running: +${res.percent}% XP from everything for ${res.hours} hours.`, "good");
      await sync();
      afterGrant();
    } else {
      toast(msgFor(res), "warn");
    }
    render();
  }

  async function activateSwap() {
    const btn = $("ccLP-swap-btn");
    if (btn) btn.disabled = true;
    const week = (typeof window.__ccWeekStartMs === "function") ? window.__ccWeekStartMs() : 0;
    if (!week) { toast(MESSAGES.server_error, "warn"); render(); return; }
    const res = await post("reroll", { weekStartMs: week });
    if (res && res.ok) {
      toast("🔄 Weekly Swaps unlocked: swap as many challenges as you like until Monday.", "good");
      await sync();
      // The challenge strip's swap buttons only appear once this is live.
      try { window.renderChallengeStrip && window.renderChallengeStrip(); } catch (_) {}
      afterGrant();
    } else {
      toast(msgFor(res), "warn");
    }
    render();
  }

  function wire() {
    const root = $("cc-level-pass-root");
    if (!root) return;
    const rail = $("ccLP-rail");

    root.querySelectorAll(".ccLP-claim").forEach(btn => {
      btn.addEventListener("click", () => claimTier(btn.getAttribute("data-tier")));
    });
    const all = $("ccLP-claimall");
    if (all) all.addEventListener("click", claimAll);
    const boost = $("ccLP-boost-btn");
    if (boost) boost.addEventListener("click", activateBoost);
    const swap = $("ccLP-swap-btn");
    if (swap) swap.addEventListener("click", activateSwap);

    const step = () => Math.max(240, (rail ? rail.clientWidth : 600) * 0.8);
    const prev = $("ccLP-prev");
    const next = $("ccLP-next");
    if (prev && rail) prev.addEventListener("click", () => rail.scrollBy({ left: -step(), behavior: "smooth" }));
    if (next && rail) next.addEventListener("click", () => rail.scrollBy({ left: step(), behavior: "smooth" }));
  }

  // ── Entry points ─────────────────────────────────────────────────────────
  window.__ccLevelPassRender = async function () {
    render();                 // paint the loading state immediately
    await sync();
    render();
  };

  // Keep the cached boost fresh for the XP paths even when the page is never
  // opened: __ccPassBoost() is read by every XP grant, and a stale "no boost"
  // would quietly cost a player the thing they just activated. One read on
  // sign-in is enough: activating a boost resyncs on its own.
  window.__ccLevelPassPrime = function () {
    sync().catch(() => {});
  };

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopTick();
    else if (isOpen()) startTick();
  });
})();
