/* Currents and Critters: friend-code referral reward (self-contained module).
 *
 * "A friend signs up with Google, types your friend code, you BOTH get 100
 * Critter Coins. Every fifth friend you bring in earns you a free background."
 *
 *   window.__ccReferralRender()      the referral card on Player Home
 *   window.__ccReferralSync()        re-read state from the server
 *   window.__ccReferralRedeem(code)  redeem a code, what the SIGN-UP screen
 *                                    calls, resolving to the server's answer
 *   window.__ccReferralCanRedeem()   cached "is the window still open?"
 *
 * NOTHING here decides anything. The friend code is resolved server-side, both
 * payouts happen inside ONE Firestore transaction, and the once-ever guard is a
 * create()d ledger document. This file collects digits and renders an answer.
 * Every rule lives in referral_server.py.
 *
 * WHY THE CARD SELF-INJECTS
 * preview.html is shared with a great deal of other work, so the card finds its
 * own home (the Friends panel) rather than needing markup reserved for it. A
 * page that DOES declare <div id="cc-referral-root"> gets it exactly there.
 */
(function () {
  "use strict";

  function bridge() { return window.__ccReferral; }
  if (bridge() && bridge().ENABLED === false) return;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, d) => { const n = Number(v); return Number.isFinite(n) ? n : (d || 0); };
  const fmt = (n) => Math.round(num(n)).toLocaleString();
  const toast = (m, t) => { try { bridge().toast(m, t); } catch (_) {} };

  // The bridge's post() resolves to an ENVELOPE: { ok, status, data }, and
  // THROWS when the request never landed. Unwrap in exactly one place.
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
    try { return unwrap(await b.post("/api/referral/" + action, body)); }
    catch (_) { return { ok: false, error: "offline" }; }
  }

  // Mirrors referral_server.ERROR_MESSAGES; the server also sends `message`,
  // and that one wins when it is there (it can quote the live window length).
  const MESSAGES = {
    unauthorized: "Sign in with Google to use a friend code.",
    no_code: "Enter your friend's code first.",
    bad_code: "That doesn't look like a friend code. Try the 4 digits under their name.",
    no_user: "No player has that friend code.",
    ambiguous_code: "More than one player has that code: enter it as Name#Code.",
    own_code: "That's your own friend code!",
    already_redeemed: "You've already used a friend code on this account.",
    mutual_referral: "You two already referred each other, only one direction pays out.",
    window_closed: "Friend codes are a sign-up bonus, and this account is past the window.",
    offline: "Couldn't reach the server. Nothing was awarded: please try again.",
    unavailable: "The referral reward didn't finish loading: please refresh the page.",
    server_error: "Something went wrong. Nothing was awarded: please try again.",
  };
  const msgFor = (res) => (res && res.message)
    || MESSAGES[String((res && res.error) || "")]
    || MESSAGES.server_error;

  // ── State ────────────────────────────────────────────────────────────────
  let _state = null;
  let _loading = false;
  let _busy = false;

  async function sync() {
    if (_loading) return _state;
    _loading = true;
    try {
      const res = await post("state", {});
      if (res && res.ok) _state = res;
    } finally { _loading = false; }
    return _state;
  }
  window.__ccReferralSync = async function () { await sync(); render(); return _state; };
  window.__ccReferralCanRedeem = () => !!(_state && _state.canRedeem);
  window.__ccReferralState = () => _state;

  // ── The redeem call the SIGN-UP screen uses ──────────────────────────────
  // Deliberately separate from the card's own input: first sign-in has its own
  // error area and its own flow, and it must never depend on this module
  // having painted anything. Resolves to the raw server answer plus a
  // ready-made sentence.
  window.__ccReferralRedeem = async function (code) {
    const typed = String(code || "").trim();
    if (!typed) return { ok: false, error: "no_code", message: MESSAGES.no_code };
    const res = await post("redeem", { code: typed });
    if (res && res.ok) {
      await sync();
      try { bridge().onRedeemed && bridge().onRedeemed(res); } catch (_) {}
    } else if (res) {
      res.message = msgFor(res);
    }
    return res;
  };

  // ── Render ───────────────────────────────────────────────────────────────
  function progressHtml() {
    const every = Math.max(1, num(_state.backgroundEvery, 5));
    const count = num(_state.referrals);
    const toNext = num(_state.toNextBackground, every);
    const into = every - toNext;             // 0…every-1 friends into this set
    const pct = Math.max(0, Math.min(100, Math.round((into / every) * 100)));
    const earned = num(_state.backgroundsEarned);

    return `
      <div class="ccRF-prog">
        <div class="ccRF-prog-top">
          <span class="ccRF-prog-lbl">${fmt(count)} friend${count === 1 ? "" : "s"} joined</span>
          <span class="ccRF-prog-goal">${toNext} more → free background 🖼️</span>
        </div>
        <div class="ccRF-bar"><div class="ccRF-bar-fill" style="width:${pct}%"></div></div>
        ${earned ? `<div class="ccRF-earned">✓ ${fmt(earned)} background${earned === 1 ? "" : "s"} earned so far</div>` : ""}
      </div>`;
  }

  function redeemHtml() {
    if (_state.redeemed) {
      const who = String(_state.redeemedFrom || "").trim();
      return `<div class="ccRF-redeemed">✓ You joined with ${who ? esc(who) + "'s" : "a friend's"} code: coins already paid.</div>`;
    }
    if (!_state.canRedeem) {
      // Past the window. Say so plainly instead of showing a box that will
      // only ever refuse, a dead input is worse than no input.
      return `<div class="ccRF-closed">Friend codes are a sign-up bonus, entered in the first
        ${esc(num(_state.windowDays, 14))} days after making an account.</div>`;
    }
    return `
      <div class="ccRF-redeem">
        <label class="ccRF-redeem-lbl" for="ccRF-code">Someone invite you? Enter their friend code:</label>
        <div class="ccRF-redeem-row">
          <input class="ccRF-input" id="ccRF-code" type="text" inputmode="numeric"
                 maxlength="24" placeholder="e.g. 4985 or Name#4985" autocomplete="off">
          <button class="ccRF-btn" type="button" id="ccRF-go">Claim ${fmt(_state.coins)}</button>
        </div>
        <div class="ccRF-err" id="ccRF-err"></div>
      </div>`;
  }

  function innerHtml() {
    const coins = fmt(_state.coins);
    const every = num(_state.backgroundEvery, 5);
    const code = String(_state.friendCode || "").trim();

    return `
      <div class="ccRF">
        <div class="ccRF-head">
          <span class="ccRF-ico" aria-hidden="true">🎁</span>
          <div>
            <div class="ccRF-title">Invite a Friend</div>
            <div class="ccRF-sub">They sign up with Google and type your code:
              <b>you both get ${coins}</b>
              <img class="cc-coin" src="/critter-coin.png?v=1" alt="Critter Coins" draggable="false">.
              Every <b>${esc(every)}</b> friends earns you a <b>free background</b>.</div>
          </div>
        </div>

        ${code ? `
          <div class="ccRF-code-row">
            <span class="ccRF-code-lbl">Your code</span>
            <code class="ccRF-code" id="ccRF-mycode">${esc(code)}</code>
            <button class="ccRF-copy" type="button" id="ccRF-copy">Copy</button>
          </div>` : ""}

        ${progressHtml()}
        ${redeemHtml()}
      </div>`;
  }

  function render() {
    const el = host();
    if (!el) return;
    if (!_state) { el.innerHTML = `<div class="ccRF"><div class="ccRF-empty">Loading…</div></div>`; return; }
    if (!_state.signedIn) {
      el.innerHTML = `<div class="ccRF"><div class="ccRF-empty">Sign in with Google to invite friends and earn Critter Coins.</div></div>`;
      return;
    }
    el.innerHTML = innerHtml();
    wire();
  }

  function host() {
    let el = $("cc-referral-root");
    if (el) return el;
    const friends = $("ph-panel-friends");
    if (!friends) return null;
    el = document.createElement("div");
    el.id = "cc-referral-root";
    friends.insertBefore(el, friends.firstChild);
    return el;
  }

  function setErr(msg, good) {
    const e = $("ccRF-err");
    if (!e) { toast(msg, good ? "good" : "warn"); return; }
    e.textContent = msg || "";
    e.className = "ccRF-err" + (msg ? (good ? " is-good" : " is-bad") : "");
  }

  async function doRedeem() {
    if (_busy) return;
    const input = $("ccRF-code");
    const btn = $("ccRF-go");
    const typed = input ? String(input.value || "").trim() : "";
    if (!typed) { setErr(MESSAGES.no_code, false); return; }
    _busy = true;
    if (btn) { btn.disabled = true; btn.textContent = "Checking…"; }
    setErr("", true);

    const res = await window.__ccReferralRedeem(typed);
    _busy = false;

    if (res && res.ok) {
      const who = String(res.referrerName || "").trim();
      toast(`+${fmt(res.coins)} Critter Coins, and ${who || "your friend"} got ${fmt(res.coins)} too!`, "good");
      if (res.backgroundGranted) {
        toast("🖼️ Your friend earned a free background from that referral!", "good");
      }
      render();   // repaints into the "you joined with …" state
    } else {
      setErr(msgFor(res), false);
      if (btn) { btn.disabled = false; btn.textContent = `Claim ${fmt(_state && _state.coins)}`; }
    }
  }

  function wire() {
    const go = $("ccRF-go");
    if (go) go.addEventListener("click", doRedeem);
    const input = $("ccRF-code");
    if (input) input.addEventListener("keydown", (e) => { if (e.key === "Enter") doRedeem(); });

    const copy = $("ccRF-copy");
    if (copy) copy.addEventListener("click", async () => {
      const code = String((_state && _state.friendCode) || "").trim();
      if (!code) return;
      try {
        await navigator.clipboard.writeText(code);
        copy.textContent = "Copied!";
        setTimeout(() => { copy.textContent = "Copy"; }, 1600);
      } catch (_) {
        // Clipboard is permission-gated and blocked outright in some embedded
        // views. Selecting the code still lets them copy it by hand, which is
        // a working fallback rather than a dead button.
        try {
          const r = document.createRange();
          r.selectNodeContents($("ccRF-mycode"));
          const sel = window.getSelection();
          sel.removeAllRanges(); sel.addRange(r);
          copy.textContent = "Press ⌘/Ctrl+C";
          setTimeout(() => { copy.textContent = "Copy"; }, 2400);
        } catch (_e) { toast("Your code is " + code, "info"); }
      }
    });
  }

  window.__ccReferralRender = async function () {
    render();
    await sync();
    render();
  };

  // Primed on sign-in so the sign-up screen and the Friends tab both know
  // whether the window is still open without waiting for a paint.
  // The sign-up screen states the reward before any account exists, so its
  // number is written into preview.html as the current default. Once the
  // server answers, overwrite it, that way an env override (REFERRAL_REWARD_COINS)
  // is reflected instead of the screen promising something else.
  function _paintSignupPromise() {
    try {
      const el = document.getElementById("auth-ref-coins");
      if (el && _state && Number.isFinite(Number(_state.coins))) {
        el.textContent = fmt(_state.coins);
      }
    } catch (_) {}
  }

  window.__ccReferralPrime = function () {
    sync().then(_paintSignupPromise).catch(() => {});
  };
})();
