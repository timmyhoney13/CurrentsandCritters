/* Currents and Critters — the Discord join reward chip (self-contained module).
 *
 * Renders the "+250 Critter Coins" chip that sits beside the Join-the-Discord
 * button on Player Home, and runs the claim:
 *
 *   window.__ccDiscordSync()    re-read the offer + whether this account paid
 *   window.__ccDiscordClaim()   start a verified claim (what the chip clicks)
 *
 * NOTHING here decides anything. The server asks Discord whether the player is
 * really in the server, and the server writes the coins. This file shows the
 * offer, opens Discord's own consent screen, and turns the server's answer into
 * a sentence. Editing any value in here changes what one player sees for one
 * paint and is then thrown away by the server's own re-check.
 *
 * THE CHIP IS HIDDEN UNTIL THE SERVER SAYS THE OFFER IS ON. With the Discord
 * credentials unset the server replies enabled:false and the chip never shows,
 * so the game cannot advertise coins nobody can collect.
 */
(function () {
  "use strict";

  function bridge() { return window.__ccDiscord; }

  const $ = (id) => document.getElementById(id);
  const fmt = (n) => (Number(n) || 0).toLocaleString();

  // The bridge's post() resolves to an ENVELOPE — { ok, status, data } — where
  // `data` is the server's JSON body, and it THROWS when the request never
  // landed. Unwrapping in one place is what keeps an async throw from silently
  // blanking the surface it renders (see the Clans tab that shipped blank once).
  function unwrap(res) {
    if (res && typeof res === "object" && "data" in res && "status" in res) {
      return res.data || { ok: false, error: "server_error" };
    }
    return res || { ok: false, error: "server_error" };
  }

  async function post(path, body) {
    const b = bridge();
    if (!b) return { ok: false, error: "unavailable" };
    try {
      return unwrap(await b.post(path, body || {}));
    } catch (_) {
      return { ok: false, error: "offline" };
    }
  }

  async function idToken() {
    try { return (await bridge().idToken()) || ""; } catch (_) { return ""; }
  }

  function toast(msg, kind) { try { bridge().toast(msg, kind); } catch (_) {} }

  // Server error code → a sentence a player can act on. Mirrors the server's
  // own ERROR_MESSAGES (discord_server.py) so the popup and the toast agree.
  const MESSAGES = {
    not_a_member: "You're not in the Discord server yet — join it, then claim again.",
    already_claimed: "You've already collected the Discord reward on this account.",
    discord_already_used: "That Discord account already claimed the reward on another Currents and Critters account.",
    discord_denied: "You cancelled the Discord sign-in, so nothing was claimed.",
    discord_unreachable: "Discord didn't answer just now. Nothing was claimed — try again in a minute.",
    discord_rejected: "Discord wouldn't confirm that sign-in. Nothing was claimed — please try again.",
    state_expired: "That claim window timed out. Give it another go.",
    state_used: "That claim link was already used. Give it another go.",
    bad_state: "That claim link wasn't valid. Give it another go.",
    not_configured: "The Discord reward isn't switched on yet.",
    no_account: "Sign in with Google first to collect Critter Coins.",
    unauthorized: "Sign in with Google first to collect Critter Coins.",
    firestore_unavailable: "Couldn't reach your account just now. Nothing was claimed — please try again.",
    bad_request: "Something was wrong with that claim. Nothing was claimed — please try again.",
    server_error: "Something went wrong. Nothing was claimed — please try again.",
    offline: "Couldn't reach the server. Nothing was claimed — please try again.",
    unavailable: "The reward didn't finish loading — please refresh the page.",
    popup_blocked: "Your browser blocked the Discord window. Allow pop-ups for this site, then try again.",
  };
  const msgFor = (code) => MESSAGES[String(code || "")] || "Something went wrong — nothing was claimed.";

  // ── State, and the chip it paints ────────────────────────────────────────
  let _state = null;      // last server reply
  let _lastUid = null;    // which account _state describes
  let _busy = false;      // a Discord window is open right now
  let _inFlight = null;   // de-dupes overlapping syncs

  function chip() { return $("ph-discord-reward"); }

  function render() {
    const el = chip();
    if (!el) return;
    const txt = $("ph-discord-reward-txt");
    const s = _state;

    // No answer yet, or the offer is off: show nothing at all rather than a
    // promise the server hasn't made.
    if (!s || !s.enabled) { el.hidden = true; return; }

    el.hidden = false;
    el.classList.toggle("is-claimed", !!s.claimed);
    el.classList.toggle("is-busy", _busy);
    el.disabled = !!s.claimed || _busy;

    const coins = fmt(s.coins);
    if (s.claimed) {
      if (txt) txt.textContent = `${fmt(s.coinsAwarded || s.coins)} claimed for joining`;
      el.title = s.claimedAt
        ? `Discord reward collected on ${String(s.claimedAt).slice(0, 10)}`
        : "Discord reward already collected";
      el.setAttribute("aria-label", "Discord reward already collected");
    } else if (_busy) {
      if (txt) txt.textContent = "Checking with Discord…";
      el.title = "Finish signing in with Discord in the other window";
      el.setAttribute("aria-label", "Checking with Discord");
    } else {
      if (txt) txt.textContent = `+${coins} Critter Coins`;
      el.title = `Join the Discord server and claim ${coins} Critter Coins`;
      el.setAttribute("aria-label", `Claim ${coins} Critter Coins for joining the Discord server`);
    }
  }

  // Re-ask the server. Cheap and skipped when the same account already has an
  // answer, because syncStatsHeader calls this on every profile repaint.
  async function sync(force) {
    const b = bridge();
    if (!b || !chip()) return;
    let uid = "";
    try { const u = b.authUser(); uid = (u && u.uid) || ""; } catch (_) { uid = ""; }
    if (!force && _state && _lastUid === uid) { render(); return; }
    if (_inFlight) return _inFlight;

    _inFlight = (async () => {
      const token = uid ? await idToken() : "";
      const res = await post("/api/discord/state", token ? { idToken: token } : {});
      if (res && res.ok) {
        _state = res;
        _lastUid = uid;
      } else if (!_state) {
        // A failed first load must not advertise anything.
        _state = { enabled: false, coins: 0, claimed: false };
        _lastUid = uid;
      }
      render();
    })();
    try { await _inFlight; } finally { _inFlight = null; }
  }

  // ── The claim ────────────────────────────────────────────────────────────
  // The popup is opened SYNCHRONOUSLY inside the click, before the network
  // call, because a window opened after an await is a pop-up blocker's
  // definition of unsolicited. It is pointed at Discord once the URL arrives.
  function openHolder() {
    let win = null;
    try {
      win = window.open("", "cc-discord-claim",
                        "width=520,height=780,menubar=no,toolbar=no");
    } catch (_) { win = null; }
    if (win) {
      try {
        win.document.write(
          '<!doctype html><meta charset="utf-8"><title>Connecting to Discord…</title>'
          + '<body style="margin:0;display:flex;align-items:center;justify-content:center;'
          + 'height:100vh;font-family:system-ui,sans-serif;background:#0b3a5c;color:#eaf6ff">'
          + "Connecting to Discord…</body>");
        win.document.close();
      } catch (_) {}
    }
    return win;
  }

  // Discord's answer comes back from the callback page, which is on our own
  // origin. Both checks matter: the origin, and that it is our message.
  function waitForResult(win) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (result) => {
        if (settled) return;
        settled = true;
        window.removeEventListener("message", onMessage);
        clearInterval(closeTimer);
        clearTimeout(giveUp);
        resolve(result);
      };
      function onMessage(ev) {
        if (ev.origin !== window.location.origin) return;
        const d = ev.data;
        if (!d || typeof d !== "object" || d.source !== "cc-discord") return;
        finish(d);
      }
      window.addEventListener("message", onMessage);
      // Closing the window without finishing is an answer too — otherwise the
      // chip would sit on "Checking with Discord…" forever.
      const closeTimer = setInterval(() => {
        try { if (win && win.closed) finish(null); } catch (_) {}
      }, 700);
      const giveUp = setTimeout(() => finish(null), 5 * 60 * 1000);
    });
  }

  async function claim() {
    const b = bridge();
    if (!b) { toast(msgFor("unavailable"), "warn"); return; }
    if (_busy) return;
    if (_state && _state.claimed) { toast(msgFor("already_claimed")); return; }

    let signedIn = false;
    try { signedIn = !!b.authUser(); } catch (_) { signedIn = false; }
    if (!signedIn) { toast(msgFor("no_account"), "warn"); return; }

    const win = openHolder();
    _busy = true;
    render();
    try {
      const token = await idToken();
      const res = await post("/api/discord/start", { idToken: token });
      if (!res || !res.ok || !res.url) {
        try { if (win) win.close(); } catch (_) {}
        await showFailure(res && res.error);
        // "already claimed" means our copy of the state is stale.
        if (res && res.error === "already_claimed") await sync(true);
        return;
      }
      if (!win) {
        // Pop-ups blocked. Rather than dead-end, hand them the same flow in
        // this tab; the callback page brings them back to the game.
        toast(msgFor("popup_blocked"), "warn");
        window.location.href = res.url;
        return;
      }
      // Both forms are guarded: if the player closed the holder window while
      // the request was in flight, touching its location throws, and an
      // unguarded throw here would escape as an unhandled rejection.
      try { win.location.replace(res.url); }
      catch (_) { try { win.location = res.url; } catch (__) {} }

      const out = await waitForResult(win);
      if (!out) return;                       // closed without finishing
      if (out.ok) {
        // The server has already told us everything the chip needs, so take it
        // rather than asking again — a re-read that failed (or answered from a
        // stale read) would flip a just-paid chip back to "+250 to claim" and
        // invite a second click that can only be refused.
        _state = Object.assign({}, _state, {
          claimed: true,
          coinsAwarded: out.coins || (_state && _state.coins) || 0,
          claimedAt: new Date().toISOString(),
        });
        toast(`+${fmt(out.coins)} Critter Coins for joining the Discord!`, "ok");
        try { b.onClaimed && b.onClaimed(out); } catch (_) {}
      } else {
        await showFailure(out.error);
        if (out.error === "already_claimed") await sync(true);
      }
    } finally {
      _busy = false;
      render();
    }
  }

  // "You're not in the server yet" is the one failure worth a modal, because
  // the fix is a button we can put in front of them.
  async function showFailure(code) {
    const b = bridge();
    const text = msgFor(code);
    if (code === "not_a_member" && b && b.modal) {
      const invite = (_state && _state.inviteUrl) || "https://discord.gg/T9V2eqxf8";
      const coins = fmt((_state && _state.coins) || 0);
      const choice = await b.modal({
        icon: "💬",
        title: "Join the server first",
        body: `We asked Discord and you're not in the Currents and Critters server yet. `
            + `Join it, then claim again to get your ${coins} Critter Coins.`,
        actions: [
          { key: "join", label: "Join the Discord", primary: true },
          { key: "later", label: "Not now" },
        ],
      });
      if (choice === "join") {
        try { window.open(invite, "_blank", "noopener,noreferrer"); } catch (_) {}
      }
      return;
    }
    toast(text, "warn");
  }

  // ── Wiring ───────────────────────────────────────────────────────────────
  function attach() {
    const el = chip();
    if (!el || el.dataset.ccWired === "1") return;
    el.dataset.ccWired = "1";
    el.addEventListener("click", () => { claim(); });
  }

  // The claim can also come back through THIS tab when pop-ups were blocked:
  // the callback page sends the player home with ?discord=<result>.
  function readReturnFlag() {
    let flag = "";
    try {
      flag = new URLSearchParams(window.location.search).get("discord") || "";
    } catch (_) { return; }
    if (!flag) return;
    try {
      const url = new URL(window.location.href);
      url.searchParams.delete("discord");
      window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    } catch (_) {}
    if (flag === "ok") {
      toast("Critter Coins added for joining the Discord!", "ok");
      try { bridge() && bridge().onClaimed && bridge().onClaimed({ ok: true }); } catch (_) {}
      sync(true);
    } else {
      showFailure(flag);
      sync(true);
    }
  }

  function boot() {
    attach();
    readReturnFlag();
    sync(false);
  }

  window.__ccDiscordSync = function () { attach(); sync(false); };
  window.__ccDiscordClaim = claim;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
