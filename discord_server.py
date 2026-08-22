"""Currents and Critters, the Discord join reward (server-authoritative).

Critter Coins, once, for ACTUALLY being in the Discord server. Wired additively
into multiplayer_server the same way clan_server / prestige_server are:

    import discord_server
    discord_server.init(get_firestore=..., verify_token=...)   # in main()
    if discord_server.handle_get(self, parsed): ...            # in do_GET
    if discord_server.handle_post(self, parsed, body): ...     # in do_POST

WHY THERE IS AN OAUTH FLOW HERE AT ALL
A "did you join? [yes]" button is not a membership check, it is a free coin
button. So the player authorises us with Discord and the SERVER asks Discord
whether that Discord account is in our guild. The browser never tells us the
answer, and there is no code path that pays out without Discord having said yes.

THE THREE THINGS THAT CANNOT HAPPEN
  1. Paid twice for one game account.   →  discord_rewards/u_{uid}
  2. Paid twice for one Discord account (alt game accounts farming the same
     Discord login).                    →  discord_rewards/d_{discord_id}
  3. Paid without being a member.       →  _guild_membership() must return True.
Both ledger docs are written with create() INSIDE the same transaction as the
coin write, so the coins cannot exist without the ledger and the ledger cannot
exist without the coins. That doc-id create() is the whole guarantee: see the
identical pattern in prestige_server._commit.

WHO PAYS FOR PEOPLE ALREADY IN THE SERVER
Nobody has to. An existing member runs the exact same flow: they authorise,
Discord says "yes, a member", they get paid. There is no separate backfill and
no list of names to keep: being in the server is the only thing checked, and
it is checked live at claim time.

CONFIGURATION (all env vars: see DISCORD_REWARD_SETUP.md for the walkthrough)
    DISCORD_CLIENT_ID       the OAuth2 app's Client ID
    DISCORD_CLIENT_SECRET   its Client Secret         (never leaves this server)
    DISCORD_GUILD_ID        the server ("guild") id players must be in
    DISCORD_REDIRECT_URI    optional: defaults to the play. host's /callback,
                            and MUST be listed as a redirect on the OAuth app
    DISCORD_INVITE_URL      optional, the invite the client advertises
    DISCORD_REWARD_COINS    optional, defaults to 100
    DISCORD_STATE_SECRET    optional: defaults to the client secret
With any of the first three missing the feature reports enabled:false, the
client hides the offer entirely, and every claim endpoint refuses. It never
degrades into paying out unverified.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

# ── Injected by init() (no circular import with multiplayer_server) ──────────
_get_firestore: Optional[Callable[[], Any]] = None
_verify_token: Optional[Callable[[str], Optional[dict]]] = None


def init(*, get_firestore, verify_token) -> None:
    global _get_firestore, _verify_token
    _get_firestore = get_firestore
    _verify_token = verify_token


# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════════
DISCORD_API = "https://discord.com/api/v10"
DEFAULT_INVITE_URL = "https://discord.gg/T9V2eqxf8"
DEFAULT_REDIRECT_URI = "https://play.currentsandcritters.com/api/discord/callback"
DEFAULT_REWARD_COINS = 100

# identify            → the Discord user id, which is what stops one Discord
#                       account paying out on five game accounts.
# guilds.members.read → /users/@me/guilds/{id}/member, the precise "is this
#                       person in THAT server" question.
# guilds              → only the fallback, for the rare app whose consent screen
#                       predates guilds.members.read.
OAUTH_SCOPES = "identify guilds guilds.members.read"

STATE_TTL_SECONDS = 15 * 60      # a login that sits open longer than this is dead
HTTP_TIMEOUT = 10                # per Discord call


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def reward_coins() -> int:
    """Critter Coins paid for a verified join. One definition, read by the
    payout, the /state advert and the tests alike."""
    try:
        n = int(_env("DISCORD_REWARD_COINS") or DEFAULT_REWARD_COINS)
    except ValueError:
        n = DEFAULT_REWARD_COINS
    return max(0, min(1_000_000, n))


def invite_url() -> str:
    return _env("DISCORD_INVITE_URL") or DEFAULT_INVITE_URL


def redirect_uri() -> str:
    return _env("DISCORD_REDIRECT_URI") or DEFAULT_REDIRECT_URI


def _state_secret() -> str:
    """Signs the OAuth `state`. The state is the ONLY thing that says which
    account a Discord redirect belongs to (Discord's callback carries no
    Firebase token), so an unsigned or guessable one would let anybody redirect
    coins onto anybody's account."""
    return _env("DISCORD_STATE_SECRET") or _env("DISCORD_CLIENT_SECRET")


def is_enabled() -> bool:
    return bool(_env("DISCORD_CLIENT_ID") and _env("DISCORD_CLIENT_SECRET")
                and _env("DISCORD_GUILD_ID") and _state_secret())


def config_status() -> Dict[str, Any]:
    """What is missing, for the startup log: names only, never values."""
    missing = [n for n in ("DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET", "DISCORD_GUILD_ID")
               if not _env(n)]
    return {"enabled": is_enabled(), "missing": missing,
            "coins": reward_coins(), "redirect_uri": redirect_uri()}


# ═══════════════════════════════════════════════════════════════════════════
#  SIGNED OAUTH STATE  (CSRF guard + "which account is this redirect for")
# ═══════════════════════════════════════════════════════════════════════════
def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(body: str) -> str:
    return _b64u(hmac.new(_state_secret().encode("utf-8"),
                          body.encode("ascii"), hashlib.sha256).digest())


def make_state(uid: str) -> str:
    """`v1.<payload>.<sig>`: payload carries the uid, an issue time and a
    single-use nonce."""
    payload = {"u": str(uid), "t": int(time.time()), "n": secrets.token_urlsafe(9)}
    body = _b64u(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"v1.{body}.{_sign(body)}"


# Nonces already spent, so a captured redirect URL cannot be replayed to re-run
# the Discord round-trip. (It could never pay twice, the ledger stops that,
# but there is no reason to let it be replayed at all.) Process-local and
# self-pruning; the ledger remains the durable guarantee across restarts.
_USED_NONCES: Dict[str, float] = {}
_NONCE_LOCK = threading.Lock()


def _spend_nonce(nonce: str) -> bool:
    now = time.time()
    with _NONCE_LOCK:
        for key, seen in list(_USED_NONCES.items()):
            if now - seen > STATE_TTL_SECONDS * 2:
                _USED_NONCES.pop(key, None)
        if nonce in _USED_NONCES:
            return False
        _USED_NONCES[nonce] = now
        return True


def read_state(state: str) -> Tuple[Optional[str], Optional[str]]:
    """(uid, None) for a good state, (None, error) otherwise."""
    raw = str(state or "").strip()
    if not raw or len(raw) > 800:
        return None, "bad_state"
    parts = raw.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return None, "bad_state"
    _, body, sig = parts
    if not _state_secret():
        return None, "not_configured"
    if not hmac.compare_digest(sig, _sign(body)):
        return None, "bad_state"
    try:
        payload = json.loads(_b64u_decode(body).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None, "bad_state"
    uid = str((payload or {}).get("u") or "")
    nonce = str((payload or {}).get("n") or "")
    try:
        issued = int((payload or {}).get("t") or 0)
    except (TypeError, ValueError):
        return None, "bad_state"
    if not uid or not nonce:
        return None, "bad_state"
    if time.time() - issued > STATE_TTL_SECONDS:
        return None, "state_expired"
    if not _spend_nonce(nonce):
        return None, "state_used"
    return uid, None


def authorize_url(uid: str) -> str:
    params = {
        "client_id": _env("DISCORD_CLIENT_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": make_state(uid),
        # Always show the consent screen: a player claiming on a second game
        # account must be able to see, and switch, which Discord account they
        # are about to use.
        "prompt": "consent",
    }
    # quote_via=quote so the scope separator is %20 rather than "+", both are
    # accepted, but %20 is what Discord's own docs show and it cannot be
    # misread as a literal plus.
    return ("https://discord.com/oauth2/authorize?"
            + urllib.parse.urlencode(params, quote_via=urllib.parse.quote))


# ═══════════════════════════════════════════════════════════════════════════
#  DISCORD API
# ═══════════════════════════════════════════════════════════════════════════
def _request(method: str, url: str, *, data: Optional[bytes] = None,
             headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, Any]]:
    """(status, parsed-json). Never raises: a transport failure comes back as
    status 0, which every caller treats as "ask again later", never as a
    membership answer."""
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    req.add_header("User-Agent", "CurrentsAndCritters (+https://currentsandcritters.com, 1.0)")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read(1_000_000)
            status = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        try:
            body = exc.read(1_000_000)
        except Exception:  # noqa: BLE001
            body = b""
    except Exception as exc:  # noqa: BLE001
        print(f"[discord] {method} {url.split('?')[0]} failed: {exc}")
        return 0, {}
    try:
        parsed = json.loads(body.decode("utf-8")) if body else {}
    except Exception:  # noqa: BLE001
        parsed = {}
    return status, (parsed if isinstance(parsed, dict) else {"data": parsed})


def _exchange_code(code: str) -> Tuple[Optional[str], Optional[str]]:
    """Authorisation code → access token. (token, None) or (None, error)."""
    form = urllib.parse.urlencode({
        "client_id": _env("DISCORD_CLIENT_ID"),
        "client_secret": _env("DISCORD_CLIENT_SECRET"),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
    }).encode("ascii")
    status, data = _request(
        "POST", f"{DISCORD_API}/oauth2/token", data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    token = str(data.get("access_token") or "")
    if status == 200 and token:
        return token, None
    if status == 0:
        return None, "discord_unreachable"
    # The overwhelmingly likely cause of a 400 here is a redirect_uri that does
    # not match the one registered on the OAuth app, so say so in the log.
    print(f"[discord] token exchange rejected (status={status}, "
          f"error={data.get('error')!r}); check DISCORD_REDIRECT_URI matches the "
          f"OAuth app's redirect exactly: {redirect_uri()}")
    return None, "discord_rejected"


def _me(token: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    status, data = _request("GET", f"{DISCORD_API}/users/@me",
                            headers={"Authorization": f"Bearer {token}"})
    if status == 200 and data.get("id"):
        return data, None
    return None, ("discord_unreachable" if status == 0 else "discord_rejected")


def _guild_membership(token: str, guild_id: str) -> Tuple[Optional[bool], Optional[str]]:
    """(True/False, None) for a real answer, (None, error) when Discord could
    not be asked. A None is NEVER treated as membership."""
    auth = {"Authorization": f"Bearer {token}"}
    status, _data = _request(
        "GET", f"{DISCORD_API}/users/@me/guilds/{urllib.parse.quote(guild_id)}/member",
        headers=auth)
    if status == 200:
        return True, None
    if status == 404:
        return False, None

    # 401/403 means the token lacks guilds.members.read (an older consent).
    # Fall back to the guild list, which the `guilds` scope covers. A user can
    # be in at most 200 guilds, so one page is the whole list.
    status, data = _request("GET", f"{DISCORD_API}/users/@me/guilds?limit=200",
                            headers=auth)
    if status == 200:
        rows = data.get("data") if isinstance(data.get("data"), list) else []
        return any(str((g or {}).get("id")) == str(guild_id) for g in rows), None
    return None, ("discord_unreachable" if status == 0 else "discord_rejected")


def _revoke_later(token: str) -> None:
    """Hand the access token back to Discord once we are done with it. We never
    store it, so this is hygiene rather than a fix; it runs off-thread so the
    player's page is not waiting on it."""
    def _run() -> None:
        try:
            form = urllib.parse.urlencode({
                "client_id": _env("DISCORD_CLIENT_ID"),
                "client_secret": _env("DISCORD_CLIENT_SECRET"),
                "token": token,
            }).encode("ascii")
            _request("POST", f"{DISCORD_API}/oauth2/token/revoke", data=form,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=_run, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════════
#  FIRESTORE, the payout and its two "exactly once" guards
# ═══════════════════════════════════════════════════════════════════════════
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


def _users(db):
    return db.collection("users")


def _ledger(db):
    return db.collection("discord_rewards")


def _txn_helpers():
    from firebase_admin import firestore
    transactional = getattr(firestore, "transactional", None)
    if transactional is None:
        from google.cloud.firestore_v1 import transactional  # type: ignore
    return transactional


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def claim_state(db, uid: str) -> Dict[str, Any]:
    """Has this account already been paid? Read-only."""
    if not _SAFE_ID.match(str(uid or "")):
        return {"claimed": False}
    snap = _ledger(db).document(f"u_{uid}").get()
    if not snap.exists:
        return {"claimed": False}
    rec = snap.to_dict() or {}
    return {"claimed": True,
            "claimedAt": rec.get("at_iso") or "",
            "coinsAwarded": _int(rec.get("coins_awarded")),
            "discordUsername": str(rec.get("discord_username") or "")}


def _grant(db, uid: str, discord_id: str, discord_username: str) -> Dict[str, Any]:
    """Pay the reward exactly once. Discord has already confirmed membership by
    the time this runs, this function's whole job is the "exactly once" part.

    All reads happen before all writes (a Firestore transaction requires it),
    and both ledger docs are create()d, so a race between two tabs, two devices
    or a double-tap ends with one payout and one loser, never two payouts."""
    if not _SAFE_ID.match(str(uid or "")) or not _SAFE_ID.match(str(discord_id or "")):
        return {"ok": False, "error": "bad_request"}

    coins_award = reward_coins()
    transactional = _txn_helpers()
    user_ref = _users(db).document(uid)
    acct_ref = _ledger(db).document(f"u_{uid}")          # this game account
    disc_ref = _ledger(db).document(f"d_{discord_id}")   # this Discord account
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        acct_prev = acct_ref.get(transaction=t)
        disc_prev = disc_ref.get(transaction=t)
        user_snap = user_ref.get(transaction=t)

        # ── guard 1: this game account has already been paid ───────────────
        if acct_prev.exists:
            rec = acct_prev.to_dict() or {}
            return {"ok": False, "error": "already_claimed",
                    "coins_awarded": _int(rec.get("coins_awarded")),
                    "claimed_at": rec.get("at_iso") or ""}

        # ── guard 2: this DISCORD account has already been paid, on some
        #    other game account. One join, one reward. ───────────────────────
        if disc_prev.exists:
            rec = disc_prev.to_dict() or {}
            if str(rec.get("uid") or "") != uid:
                return {"ok": False, "error": "discord_already_used",
                        "claimed_at": rec.get("at_iso") or ""}
            # Same Discord AND same uid but no account doc: only reachable if a
            # previous run half-wrote, which the transaction makes impossible.
            # Refuse rather than invent a second payout.
            return {"ok": False, "error": "already_claimed",
                    "coins_awarded": _int(rec.get("coins_awarded")),
                    "claimed_at": rec.get("at_iso") or ""}

        if not user_snap.exists:
            return {"ok": False, "error": "no_account"}

        udoc = user_snap.to_dict() or {}
        stats = udoc.get("stats") if isinstance(udoc.get("stats"), dict) else {}
        before = max(0, _int(stats.get("critter_coins")))
        after = before + coins_award

        now = time.time()
        entry = {
            "uid": uid,
            "username": str(udoc.get("nickname") or udoc.get("username") or ""),
            "discord_id": discord_id,
            "discord_username": discord_username,
            "coins_awarded": coins_award,
            "coins_before": before,
            "coins_after": after,
            "at": now,
            "at_iso": _iso(now),
            "guild_id": _env("DISCORD_GUILD_ID"),
        }

        # merge=True so nothing else under stats is disturbed.
        t.set(user_ref, {"stats": {"critter_coins": after}}, merge=True)
        # These two create()s ARE the "cannot happen twice" guarantee, and they
        # double as the admin log. Inside the transaction, so they cannot exist
        # without the coins and the coins cannot exist without them.
        t.create(acct_ref, entry)
        t.create(disc_ref, entry)

        return {"ok": True, "coins_awarded": coins_award, "coins_total": after,
                "discord_username": discord_username, "claimed_at": entry["at_iso"]}

    try:
        return _run(txn)
    except Exception as exc:  # noqa: BLE001
        # A create() collision means another request won the race, the guard
        # doing its job, not an outage. ONLY that one maps to "already
        # claimed": a contention abort wrote nothing, and telling someone they
        # have been paid when they have not is the one wrong answer here, since
        # they would believe it and never try again.
        if type(exc).__name__ == "AlreadyExists":
            return {"ok": False, "error": "already_claimed"}
        import traceback
        print(f"[discord] grant failed for {uid}: {exc}\n{traceback.format_exc(limit=4)}")
        return {"ok": False, "error": "server_error"}


def claim_for_code(uid: str, code: str) -> Dict[str, Any]:
    """The whole verified claim: code → token → identity → membership → payout.

    Every failure mode returns a NAMED error the client turns into a sentence.
    There is no branch that pays out without _guild_membership() returning True.
    """
    if not is_enabled():
        return {"ok": False, "error": "not_configured"}
    db = _get_firestore() if _get_firestore else None
    if db is None:
        return {"ok": False, "error": "firestore_unavailable"}

    token, err = _exchange_code(code)
    if err or not token:
        return {"ok": False, "error": err or "discord_rejected"}

    try:
        me, err = _me(token)
        if err or not me:
            return {"ok": False, "error": err or "discord_rejected"}

        member, err = _guild_membership(token, _env("DISCORD_GUILD_ID"))
        if err:
            return {"ok": False, "error": err}
        if member is not True:
            return {"ok": False, "error": "not_a_member"}

        discord_id = str(me.get("id") or "")
        name = str(me.get("global_name") or me.get("username") or "")
        return _grant(db, uid, discord_id, name[:80])
    finally:
        _revoke_later(token)


# ═══════════════════════════════════════════════════════════════════════════
#  THE CALLBACK PAGE  (what Discord redirects the browser to)
# ═══════════════════════════════════════════════════════════════════════════
ERROR_MESSAGES = {
    "not_a_member": "You're not in the Currents and Critters Discord server yet. "
                    "Join it with the Discord button, then claim again.",
    "already_claimed": "You've already collected the Discord reward on this account.",
    "discord_already_used": "That Discord account has already claimed the reward "
                            "on another Currents and Critters account.",
    "discord_denied": "You cancelled the Discord sign-in, so nothing was claimed.",
    "discord_unreachable": "Discord didn't answer just now. Nothing was claimed: "
                           "please try again in a minute.",
    "discord_rejected": "Discord wouldn't confirm that sign-in. Nothing was claimed "
                        "please try again.",
    "state_expired": "That claim window timed out. Please start it again.",
    "state_used": "That claim link was already used. Please start it again.",
    "bad_state": "That claim link wasn't valid. Please start it again from the game.",
    "not_configured": "The Discord reward isn't switched on yet.",
    "no_account": "Sign in to the game first, then claim.",
    "firestore_unavailable": "The server couldn't reach your account just now. "
                             "Nothing was claimed: please try again.",
    "bad_request": "Something was wrong with that claim. Please try again.",
    "server_error": "Something went wrong. Nothing was claimed: please try again.",
}


def message_for(result: Dict[str, Any]) -> str:
    if result.get("ok"):
        coins = _int(result.get("coins_awarded"))
        return f"{coins:,} Critter Coins are on your account. Thanks for joining!"
    return ERROR_MESSAGES.get(str(result.get("error") or ""), ERROR_MESSAGES["server_error"])


def _callback_html(result: Dict[str, Any]) -> bytes:
    """A tiny self-contained page. It hands the result back to the game window
    that opened it and closes; if it was opened in the same tab instead (popup
    blocked), the link returns to the game."""
    ok = bool(result.get("ok"))
    payload = json.dumps({
        "source": "cc-discord",
        "ok": ok,
        "error": str(result.get("error") or ""),
        "coins": _int(result.get("coins_awarded")),
        "total": _int(result.get("coins_total")),
        "message": message_for(result),
    })
    heading = "You're in! 🎉" if ok else "Not claimed"
    icon = "🪙" if ok else "🐚"
    body = message_for(result).replace("&", "&amp;").replace("<", "&lt;")
    accent = "#7ff0b8" if ok else "#ffc7b5"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Currents and Critters: Discord</title>
<style>
  html,body{{margin:0;height:100%;font-family:Nunito,system-ui,-apple-system,Segoe UI,sans-serif;
    background:linear-gradient(180deg,#0b3a5c,#07263d);color:#eaf6ff;
    display:flex;align-items:center;justify-content:center;padding:24px;box-sizing:border-box}}
  .card{{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.16);border-radius:18px;
    padding:28px 26px;max-width:420px;text-align:center;box-shadow:0 18px 50px rgba(0,0,0,.35)}}
  .ico{{font-size:44px;line-height:1}}
  h1{{font-size:1.3rem;margin:14px 0 8px;color:{accent}}}
  p{{margin:0 0 18px;line-height:1.5;opacity:.92;font-size:.98rem}}
  a{{display:inline-block;background:#2fa8e0;color:#04263c;font-weight:800;text-decoration:none;
    padding:11px 20px;border-radius:999px}}
</style></head><body>
<div class="card">
  <div class="ico">{icon}</div>
  <h1>{heading}</h1>
  <p>{body}</p>
  <a href="/" id="back">Back to the game</a>
</div>
<script>
  var RESULT = {payload};
  try {{
    if (window.opener && !window.opener.closed) {{
      window.opener.postMessage(RESULT, window.location.origin);
      setTimeout(function () {{ try {{ window.close(); }} catch (e) {{}} }}, 1800);
    }} else {{
      // Popup was blocked, so this IS the game's tab. Carry the result home in
      // the URL and let the app show the same message there.
      var q = "?discord=" + (RESULT.ok ? "ok" : (RESULT.error || "error"));
      document.getElementById("back").setAttribute("href", "/" + q);
      setTimeout(function () {{ window.location.replace("/" + q); }}, 2600);
    }}
  }} catch (e) {{}}
</script>
</body></html>""".encode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════════════════════
def _auth_uid(body: Dict[str, Any]) -> Optional[str]:
    tok = body.get("idToken") if isinstance(body.get("idToken"), str) else ""
    claims = _verify_token(tok) if (tok and _verify_token) else None
    return claims.get("uid") if claims and claims.get("uid") else None


def _state_payload(uid: Optional[str]) -> Dict[str, Any]:
    """What the Player Home chip renders itself from."""
    out: Dict[str, Any] = {
        "ok": True,
        "enabled": is_enabled(),
        "coins": reward_coins(),
        "inviteUrl": invite_url(),
        "signedIn": bool(uid),
        "claimed": False,
    }
    if not out["enabled"]:
        # Say WHICH variables are still missing (names only, never values) so
        # "the offer never appeared" can be diagnosed with one request instead
        # of a trip through the Render logs. The names are already public, they
        # are in DISCORD_REWARD_SETUP.md, and "off" is obvious from `enabled`.
        out["missing"] = config_status()["missing"]
    if not uid or not out["enabled"]:
        return out
    db = _get_firestore() if _get_firestore else None
    if db is None:
        return out
    try:
        out.update(claim_state(db, uid))
    except Exception as exc:  # noqa: BLE001
        print(f"[discord] state lookup failed for {uid}: {exc}")
    return out


def handle_get(handler, parsed) -> bool:
    """GET /api/discord/callback, where Discord sends the player back.

    Carries no Firebase token (Discord controls this redirect), so the SIGNED
    `state` is the only thing that says whose account this is.
    """
    if parsed.path != "/api/discord/callback":
        return False

    query = urllib.parse.parse_qs(parsed.query or "")
    state = (query.get("state") or [""])[0]
    code = (query.get("code") or [""])[0]
    denied = (query.get("error") or [""])[0]

    if not is_enabled():
        result: Dict[str, Any] = {"ok": False, "error": "not_configured"}
    else:
        uid, err = read_state(state)
        if err or not uid:
            # A bad state is checked BEFORE the code is spent, so a forged
            # redirect never reaches Discord, let alone an account.
            result = {"ok": False, "error": err or "bad_state"}
        elif denied or not code:
            result = {"ok": False, "error": "discord_denied"}
        else:
            try:
                result = claim_for_code(uid, code)
            except Exception as exc:  # noqa: BLE001
                import traceback
                print(f"[discord] callback failed for {uid}: {exc}\n"
                      f"{traceback.format_exc(limit=4)}")
                result = {"ok": False, "error": "server_error"}

    handler._emit_html(_callback_html(result))
    return True


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    """POST /api/discord/state: is the offer on, and has this account claimed?
       POST /api/discord/start: begin a verified claim (returns the Discord URL)
    """
    path = parsed.path
    if not path.startswith("/api/discord/"):
        return False
    action = path[len("/api/discord/"):]

    # `state` is deliberately readable signed-out: the offer is advertised to
    # guests too, and the reply carries no account data without a token.
    if action == "state":
        uid = _auth_uid(body)
        handler._send_json(_state_payload(uid))
        return True

    if action == "start":
        if not is_enabled():
            handler._send_json({"ok": False, "error": "not_configured"})
            return True
        uid = _auth_uid(body)
        if not uid:
            handler._send_json({"ok": False, "error": "unauthorized"}, status=401)
            return True
        # A courtesy pre-check so an already-paid account is told so without a
        # pointless trip to Discord. It is NOT the guard: _grant is.
        db = _get_firestore() if _get_firestore else None
        if db is not None:
            try:
                if claim_state(db, uid).get("claimed"):
                    handler._send_json({"ok": False, "error": "already_claimed"})
                    return True
            except Exception as exc:  # noqa: BLE001
                print(f"[discord] pre-check failed for {uid}: {exc}")
        handler._send_json({"ok": True, "url": authorize_url(uid),
                            "coins": reward_coins()})
        return True

    handler._send_json({"ok": False, "error": "unknown_action"}, status=404)
    return True
