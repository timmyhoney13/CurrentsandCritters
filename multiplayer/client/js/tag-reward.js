/* Currents and Critters, the plush-tag QR reward (self-contained module).
 *
 * The QR code on the plush hang tag opens the game at
 *
 *     https://play.currentsandcritters.com/?tag=<TOKEN>
 *
 * This file catches that token, wipes it from the address bar, keeps it on the
 * device until there is a signed-in account to claim with, and then asks the
 * server for the skin (POST /api/tag-reward/claim).
 *
 * NOTHING here decides anything. The server checks the token, checks the
 * account, and writes the skin + its once-per-account ledger in one
 * transaction (tag_reward.py). This file only carries the token there and
 * turns the answer into a sentence.
 *
 *   • Guests / signed-out players keep the token and are asked to sign in; the
 *     claim fires by itself the moment an account is signed in (Google sign-in
 *     redirects included, because the token waits in localStorage).
 *   • A brand-new account whose profile is still being written gets a few
 *     spaced retries instead of a dead end.
 *   • The token is dropped once the server gives a FINAL answer (granted,
 *     already claimed, already owned, not our link), never on a network blip.
 */
(function () {
  "use strict";

  const PARAM = "tag";
  const STORE_KEY = "cc_tag_reward_token";
  const TOKEN_SHAPE = /^[A-Za-z0-9_-]{8,64}$/;
  const RETRY_DELAYS_MS = [3000, 8000, 20000, 45000];
  const NUDGE_AFTER_MS = 7000;

  function bridge() { return window.__ccTagReward; }

  function toast(msg, kind) { try { bridge().toast(msg, kind); } catch (_) {} }

  // The bridge's post() resolves to an ENVELOPE { ok, status, data } and throws
  // when the request never landed (same contract as the other reward modules).
  function unwrap(res) {
    if (res && typeof res === "object" && "data" in res && "status" in res) {
      return res.data || { ok: false, error: "server_error" };
    }
    return res || { ok: false, error: "server_error" };
  }

  // ── The token, from the QR link to the device ───────────────────────────
  function pending() {
    try { return localStorage.getItem(STORE_KEY) || ""; } catch (_) { return ""; }
  }
  function remember(tok) {
    try { localStorage.setItem(STORE_KEY, tok); } catch (_) {}
  }
  function forget() {
    try { localStorage.removeItem(STORE_KEY); } catch (_) {}
  }

  function captureFromUrl() {
    let tok = "";
    try { tok = (new URLSearchParams(window.location.search).get(PARAM) || "").trim(); }
    catch (_) { return false; }
    if (!tok) return false;
    if (TOKEN_SHAPE.test(tok)) remember(tok);
    // Out of the address bar either way, so it is not bookmarked or shared by
    // copying the URL.
    try {
      const url = new URL(window.location.href);
      url.searchParams.delete(PARAM);
      window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    } catch (_) {}
    return TOKEN_SHAPE.test(tok);
  }

  // ── The claim ───────────────────────────────────────────────────────────
  const FINAL = new Set(["already_claimed", "already_owned", "bad_token"]);
  const RETRYABLE = new Set(["no_account", "firestore_unavailable", "server_error", "offline"]);

  const MESSAGES = {
    ok: "You unlocked an exclusive skin! It's yours to keep: find it in your Avatar Gallery.",
    already_claimed: "This account has already claimed the tag's free skin.",
    already_owned: "You already own this exclusive skin.",
    bad_token: "That QR link isn't one of ours.",
    unauthorized: "Sign in or create a free account to claim your exclusive skin!",
  };

  let _busy = false;
  let _retry = 0;
  let _retryTimer = null;
  let _nudged = false;

  function signedIn() {
    const b = bridge();
    if (!b) return false;
    try { const u = b.authUser(); return !!(u && u.uid); } catch (_) { return false; }
  }

  function scheduleRetry() {
    if (_retryTimer || _retry >= RETRY_DELAYS_MS.length) return;
    const wait = RETRY_DELAYS_MS[_retry++];
    _retryTimer = setTimeout(() => { _retryTimer = null; tryClaim(); }, wait);
  }

  function nudge() {
    if (_nudged || !pending() || signedIn()) return;
    _nudged = true;
    toast(MESSAGES.unauthorized, "warn");
  }

  async function tryClaim() {
    const tok = pending();
    const b = bridge();
    if (!tok || !b || _busy) return;
    if (!signedIn()) return;          // the "cc:auth" listener calls back in
    _busy = true;
    try {
      let idToken = "";
      try { idToken = (await b.idToken()) || ""; } catch (_) { idToken = ""; }
      if (!idToken) { scheduleRetry(); return; }

      let res;
      try { res = unwrap(await b.post("/api/tag-reward/claim", { idToken, token: tok })); }
      catch (_) { res = { ok: false, error: "offline" }; }

      if (res && res.ok) {
        forget();
        toast(MESSAGES.ok, "ok");
        try { await b.onGranted(res); } catch (_) {}
        return;
      }
      const err = String((res && res.error) || "server_error");
      if (FINAL.has(err)) {
        forget();
        toast(MESSAGES[err] || res.message || "", err === "already_owned" ? "ok" : "warn");
        return;
      }
      if (err === "unauthorized") { nudge(); return; }
      if (RETRYABLE.has(err)) scheduleRetry();
    } finally {
      _busy = false;
    }
  }

  function boot() {
    captureFromUrl();
    if (!pending()) return;
    tryClaim();
    // Signed-out (or a guest) a few seconds after load: say what to do.
    setTimeout(() => { if (!signedIn()) nudge(); }, NUDGE_AFTER_MS);
  }

  // Auth resolves after the page paints, and a sign-in later in the visit is
  // exactly when a waiting token should be spent.
  window.addEventListener("cc:auth", () => { _retry = 0; tryClaim(); });

  window.__ccTagRewardClaim = tryClaim;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
