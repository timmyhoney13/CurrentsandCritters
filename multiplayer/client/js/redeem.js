/* Currents and Critters: purchase code redemption (self-contained module).
 *
 * "You donated on the website, we emailed you a code, you paste it here."
 *
 *   window.__ccRedeemRender()      paint the card
 *   window.__ccRedeemCode(code)    spend a code, resolving to the server's answer
 *
 * NOTHING here decides anything. The code is resolved server-side, the grant
 * happens inside ONE Firestore transaction, and the single-use guard is the
 * reward document's own status. This file collects a string and renders an
 * answer. Every rule lives in redeem_codes.py.
 *
 * WHY IT IS A SECOND BOX AND NOT THE REFERRAL ONE
 * The obvious move is to hang this off the friend-code input that is already in
 * the Friends tab. That input is not always there: referral.js hides it once
 * you have used a friend code, and again once you are past the 14-day sign-up
 * window. A supporter who donated in month three would find no box at all. So
 * this card renders unconditionally, and to keep ONE place to type a code it
 * also accepts a FRIEND code and hands it to referral.js (see forwardToReferral)
 * whenever that window is still open.
 */
(function () {
  "use strict";

  function bridge() { return window.__ccRedeem; }
  if (bridge() && bridge().ENABLED === false) return;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => { const v = Number(n); return Math.round(Number.isFinite(v) ? v : 0).toLocaleString(); };
  const toast = (m, t) => { try { bridge().toast(m, t); } catch (_) {} };

  // The bridge's post() resolves to an ENVELOPE: { ok, status, data }, and
  // THROWS when the request never landed. Unwrap in exactly one place.
  function unwrap(res) {
    if (res && typeof res === "object" && "data" in res && "status" in res) {
      return res.data || { ok: false, error: "server_error" };
    }
    return res || { ok: false, error: "server_error" };
  }

  // Mirrors redeem_codes.MESSAGES; the server also sends `message`, and that
  // one wins when it is there.
  const MESSAGES = {
    unauthorized:    "Sign in to your account first, then enter the code.",
    no_code:         "Enter the code from your email.",
    bad_code:        "That code isn't one of ours. Check the email your receipt came in.",
    not_found:       "That code isn't one of ours. Check the email your receipt came in.",
    already_claimed: "That code has already been used.",
    locked:          "Too many wrong codes. Wait 15 minutes and try again.",
    unavailable:     "Couldn't reach the server. Try again in a moment.",
    no_secret:       "Redemption codes are not set up on this server yet.",
    offline:         "Couldn't reach the server. Try again in a moment.",
    server_error:    "Something went wrong. Try again in a moment.",
  };
  function msgFor(res) {
    const m = res && typeof res.message === "string" ? res.message.trim() : "";
    if (m) return m;
    return MESSAGES[(res && res.error) || "server_error"] || MESSAGES.server_error;
  }

  let _busy = false;

  // A friend code is 4 digits, optionally "Name#4985". A purchase code is 16
  // symbols from a no-confusables alphabet, usually pasted as CC-XXXX-…. They
  // cannot be mistaken for each other, which is what makes one box safe.
  function looksLikeFriendCode(typed) {
    const s = String(typed || "").trim();
    return /^\d{3,6}$/.test(s) || /^.+#\d{3,6}$/.test(s);
  }

  async function forwardToReferral(typed) {
    if (typeof window.__ccReferralRedeem !== "function") return null;
    try { return await window.__ccReferralRedeem(typed); }
    catch (_) { return null; }
  }

  async function post(code) {
    const b = bridge();
    if (!b) return { ok: false, error: "unavailable" };
    const body = { code: String(code || "") };
    try { body.idToken = (await b.idToken()) || ""; } catch (_) { body.idToken = ""; }
    try { return unwrap(await b.post("/api/redeem/code", body)); }
    catch (_) { return { ok: false, error: "offline" }; }
  }

  window.__ccRedeemCode = async function (code) { return await post(code); };

  function host() {
    let el = $("cc-redeem-root");
    if (el) return el;
    const friends = $("ph-panel-friends");
    if (!friends) return null;
    el = document.createElement("div");
    el.id = "cc-redeem-root";
    // Directly under the referral card when there is one, so the two code
    // boxes sit together rather than at opposite ends of the tab.
    const ref = $("cc-referral-root");
    if (ref && ref.parentNode === friends) friends.insertBefore(el, ref.nextSibling);
    else friends.insertBefore(el, friends.firstChild);
    return el;
  }

  function innerHtml() {
    return `
      <div class="ccRD">
        <div class="ccRD-head">
          <span class="ccRD-ico" aria-hidden="true">🎟️</span>
          <div>
            <div class="ccRD-title">Redeem a Code</div>
            <div class="ccRD-sub">Donated or bought coins on the website? We emailed you a
              code. Paste it here and your rewards land on this account.</div>
          </div>
        </div>
        <div class="ccRD-row">
          <input class="ccRD-input" id="ccRD-code" type="text"
                 maxlength="32" placeholder="CC-XXXX-XXXX-XXXX-XXXX" autocomplete="off"
                 spellcheck="false" aria-label="Purchase code">
          <button class="ccRD-btn" type="button" id="ccRD-go">Redeem</button>
        </div>
        <div class="ccRD-err" id="ccRD-err"></div>
      </div>`;
  }

  function render() {
    const el = host();
    if (!el) return;
    const b = bridge();
    let signedIn = false;
    try { signedIn = !!(b && b.signedIn && b.signedIn()); } catch (_) { signedIn = false; }
    if (!signedIn) {
      el.innerHTML = `<div class="ccRD"><div class="ccRD-empty">Sign in or create an account to redeem a purchase code.</div></div>`;
      return;
    }
    el.innerHTML = innerHtml();
    wire();
  }
  window.__ccRedeemRender = render;

  function setErr(msg, good) {
    const e = $("ccRD-err");
    if (!e) { toast(msg, good ? "good" : "warn"); return; }
    e.textContent = msg || "";
    e.className = "ccRD-err" + (msg ? (good ? " is-good" : " is-bad") : "");
  }

  function reward(res) {
    const coins = Number(res && res.coins) || 0;
    const what = String((res && res.rewardName) || "your purchase").trim();
    return coins > 0
      ? `${what} unlocked: +${fmt(coins)} Critter Coins!`
      : `${what} unlocked!`;
  }

  async function doRedeem() {
    if (_busy) return;
    const input = $("ccRD-code");
    const btn = $("ccRD-go");
    const typed = input ? String(input.value || "").trim() : "";
    if (!typed) { setErr(MESSAGES.no_code, false); return; }

    _busy = true;
    if (btn) { btn.disabled = true; btn.textContent = "Checking…"; }
    setErr("", true);

    // One box, two kinds of code. A friend code goes to the module that owns
    // the friend-code rules rather than being half-implemented here.
    let res;
    if (looksLikeFriendCode(typed)) {
      res = await forwardToReferral(typed);
      if (res && res.ok) {
        toast(`+${fmt(res.coins)} Critter Coins from that friend code!`, "good");
        finish(true);
        try { window.__ccReferralRender && window.__ccReferralRender(); } catch (_) {}
        return;
      }
      if (!res) res = { ok: false, error: "bad_code" };
    } else {
      res = await post(typed);
    }

    if (res && res.ok) {
      toast(reward(res), "good");
      setErr(reward(res), true);
      finish(true);
    } else {
      setErr(msgFor(res), false);
      finish(false);
    }
  }

  function finish(good) {
    _busy = false;
    const btn = $("ccRD-go");
    const input = $("ccRD-code");
    if (btn) { btn.disabled = false; btn.textContent = "Redeem"; }
    if (good) {
      if (input) input.value = "";
      // A grant moves coins, XP, the level, backgrounds, icons and the
      // supporter badge on the account document the whole app renders from, so
      // re-read it rather than letting the header keep painting the old
      // balance. This is the only thing that makes the reward VISIBLE.
      try { bridge().onRedeemed && bridge().onRedeemed(); } catch (_) {}
    }
  }

  function wire() {
    const go = $("ccRD-go");
    if (go) go.addEventListener("click", doRedeem);
    const input = $("ccRD-code");
    if (input) input.addEventListener("keydown", (e) => { if (e.key === "Enter") doRedeem(); });
  }

  // The Friends panel is built after auth resolves, so paint on a short poll
  // the same way the referral card does rather than racing it.
  let _tries = 0;
  const _t = setInterval(() => {
    _tries += 1;
    if ($("ph-panel-friends")) render();
    if (_tries > 40) clearInterval(_t);
  }, 500);
  if (document.readyState !== "loading") render();
  else document.addEventListener("DOMContentLoaded", render);
})();
