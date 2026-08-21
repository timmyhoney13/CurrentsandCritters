"""Currents and Critters — the friend-code referral reward (server-authoritative).

A new Google account types a friend's code when it signs up. BOTH accounts get
Critter Coins, and every fifth friend somebody brings in earns them a free
avatar background. Wired additively into multiplayer_server exactly like
clan_server / prestige_server / discord_server:

    import referral_server
    referral_server.init(get_firestore=..., verify_token=...,
                         background_paths=...)                  # in main()
    if referral_server.handle_post(self, parsed, body): ...      # in do_POST

WHY THE SERVER OWNS THIS
It pays two accounts at once, and one of them is not the account making the
request. A browser that could say "pay me and pay 4985" is a coin printer with
extra steps. So the code is resolved here, both payouts happen in ONE Firestore
transaction, and the client's only job is to type the digits and read the answer.

THE FOUR THINGS THAT CANNOT HAPPEN
  1. One account redeeming twice, ever.
     → referral_redemptions/{uid}, create()d inside the payout transaction.
  2. Redeeming your own code.
     → the referrer uid is compared to the caller's.
  3. Two accounts refunding each other forever.
     → if the person whose code was typed already redeemed THIS account's code,
       the loop is refused. That closes the obvious two-account farm.
  4. A veteran account "signing up" years later to collect.
     → the redeeming account must be younger than REFERRAL_WINDOW_DAYS. This is
       a SIGN-UP reward; the field is on the sign-up screen and on Player Home
       for anyone still inside the window.

Fake Google accounts are the residual abuse and no referral programme escapes
it; the window plus once-ever-per-account plus a real Google sign-in is the
same mitigation everyone else lands on.

WHY THE BACKGROUND IS THE REFERRER'S ALONE
"Every five friends" only counts on the side of the person handing the code
out — the friend joining has referred nobody. So the fifth, tenth, fifteenth …
successful referral grants ONE background to the referrer, chosen from the ones
they do not already own. The friend's reward is the coins, every time.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

# ── Injected by init() (no circular import with multiplayer_server) ──────────
_get_firestore: Optional[Callable[[], Any]] = None
_verify_token: Optional[Callable[[str], Optional[dict]]] = None
_background_paths: List[str] = []


def init(*, get_firestore, verify_token, background_paths) -> None:
    global _get_firestore, _verify_token, _background_paths
    _get_firestore = get_firestore
    _verify_token = verify_token
    _background_paths = [str(p) for p in (background_paths or [])]


# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════════
DEFAULT_REWARD_COINS = 100      # paid to BOTH sides, per successful referral
DEFAULT_BACKGROUND_EVERY = 5    # referrals per free background, for the referrer
DEFAULT_WINDOW_DAYS = 14        # how long after signing up a code may be entered


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        n = int(str(os.environ.get(name, "") or default).strip())
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def reward_coins() -> int:
    """Coins each side gets. One definition, read by the payout, the sign-up
    screen's promise and the tests alike."""
    return _env_int("REFERRAL_REWARD_COINS", DEFAULT_REWARD_COINS, 0, 100_000)


def background_every() -> int:
    return _env_int("REFERRAL_BACKGROUND_EVERY", DEFAULT_BACKGROUND_EVERY, 1, 1000)


def window_days() -> int:
    return _env_int("REFERRAL_WINDOW_DAYS", DEFAULT_WINDOW_DAYS, 1, 3650)


# ═══════════════════════════════════════════════════════════════════════════
#  FRIEND-CODE RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════
# Friend codes are random 4-digit numbers and are NOT unique on their own, so a
# collision asks for the name too rather than paying a stranger. Same shapes the
# Clans invite box accepts (see clan_server._resolve_invitee) — a player who
# learned to type "Twin Midi#9113" there should not have to learn something else
# here.
_FC_ONLY_RE = re.compile(r"^#?(\d{3,6})$")                  # "2809" / "#2809"
_FC_NAMED_RE = re.compile(r"^(.+?)\s*[#\s]\s*(\d{3,6})$")   # "Twin Midi 9113"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_BG_PATH = re.compile(r"^/backgrounds/[a-z0-9_-]+\.png$")


def _users(db):
    return db.collection("users")


def _ledger(db):
    return db.collection("referral_redemptions")


def _uid_by_friend_code(db, code: str) -> Tuple[str, str]:
    """Bare friend code → (uid, error)."""
    # Signup writes the code as a STRING. Query the number too: an == filter for
    # "2809" does not match a doc holding 2809, and one legacy account stored
    # that way would look like "no such player" forever.
    try:
        rows = list(_users(db).where("friend_code", "==", str(code)).limit(5).get())
        if not rows and str(code).isdigit():
            rows = list(_users(db).where("friend_code", "==", int(code)).limit(5).get())
    except Exception as exc:  # noqa: BLE001
        print(f"[referral] friend-code lookup failed: {exc}")
        return "", "no_user"
    if not rows:
        return "", "no_user"
    if len(rows) > 1:
        return "", "ambiguous_code"
    return rows[0].id, ""


def _uid_by_name_and_code(db, name: str, code: str) -> str:
    """friend_lookup/{nicknameLower}_{code} — the exact doc signup writes and
    every nickname change rewrites."""
    key = f"{str(name or '').strip().lower()}_{code}"
    try:
        snap = db.collection("friend_lookup").document(key).get()
        if snap.exists:
            return str((snap.to_dict() or {}).get("uid") or "")
    except Exception as exc:  # noqa: BLE001
        print(f"[referral] friend_lookup failed: {exc}")
    return ""


def resolve_code(db, raw: str) -> Tuple[str, str]:
    """Whatever was typed → (referrer uid, error). Accepts "2809", "#2809",
    "Twin Midi 9113" and "Twin Midi#9113"."""
    txt = str(raw or "").strip()
    if not txt:
        return "", "no_code"
    m = _FC_ONLY_RE.match(txt)
    if m:
        return _uid_by_friend_code(db, m.group(1))
    m = _FC_NAMED_RE.match(txt)
    if m:
        uid = _uid_by_name_and_code(db, m.group(1), m.group(2))
        if uid:
            return uid, ""
        # The NAME may be stale (people rename); the code usually is not.
        return _uid_by_friend_code(db, m.group(2))
    return "", "bad_code"


# ═══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def _transactional():
    from firebase_admin import firestore
    fn = getattr(firestore, "transactional", None)
    if fn is None:
        from google.cloud.firestore_v1 import transactional as fn  # type: ignore
    return fn


def _array_union():
    from firebase_admin import firestore
    fn = getattr(firestore, "ArrayUnion", None)
    if fn is None:
        from google.cloud.firestore_v1 import ArrayUnion as fn  # type: ignore
    return fn


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _stats_of(doc: Dict[str, Any]) -> Dict[str, Any]:
    s = doc.get("stats")
    return s if isinstance(s, dict) else {}


def _created_epoch(doc: Dict[str, Any]) -> Optional[float]:
    """`created_at` is a Firestore serverTimestamp — a DatetimeWithNanoseconds
    in the admin SDK. Anything we cannot read as a time returns None, and None
    means "cannot prove this account is old", which the window check treats as
    IN window. Refusing a legitimate new player because their timestamp did not
    parse is the worse failure: they never get their coins and never find out
    why."""
    raw = doc.get("created_at")
    if raw is None:
        return None
    ts = getattr(raw, "timestamp", None)
    if callable(ts):
        try:
            return float(ts())
        except Exception:  # noqa: BLE001
            return None
    if isinstance(raw, (int, float)):
        # Milliseconds if it is clearly too big to be seconds.
        return float(raw) / 1000.0 if float(raw) > 1e11 else float(raw)
    return None


def _within_window(doc: Dict[str, Any]) -> bool:
    created = _created_epoch(doc)
    if created is None:
        return True
    return (time.time() - created) <= window_days() * 86400


def _pick_background(doc: Dict[str, Any]) -> Optional[str]:
    """The first catalogue background this account does not already own."""
    raw = doc.get("unlocked_backgrounds")
    owned = set()
    if isinstance(raw, (list, tuple)):
        for item in raw:
            s = str(item or "").strip().split("?")[0].lower()
            if _BG_PATH.match(s):
                owned.add(s)
    for path in _background_paths:
        if path.lower() not in owned:
            return path
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  STATE
# ═══════════════════════════════════════════════════════════════════════════
def state_payload(uid: Optional[str]) -> Dict[str, Any]:
    """What the referral card on Player Home paints itself from."""
    every = background_every()
    out: Dict[str, Any] = {
        "ok": True,
        "coins": reward_coins(),
        "backgroundEvery": every,
        "windowDays": window_days(),
        "signedIn": bool(uid),
        "friendCode": "",
        "referrals": 0,
        "backgroundsEarned": 0,
        "toNextBackground": every,
        "redeemed": False,
        "redeemedFrom": "",
        "canRedeem": False,
    }
    if not uid:
        return out
    db = _get_firestore() if _get_firestore else None
    if db is None:
        return out
    try:
        snap = _users(db).document(uid).get()
        doc = (snap.to_dict() or {}) if snap.exists else {}
        out["friendCode"] = str(doc.get("friend_code") or "")
        count = max(0, _int(doc.get("referral_count")))
        out["referrals"] = count
        out["backgroundsEarned"] = max(0, _int(doc.get("referral_backgrounds")))
        # 0 referrals is `every` away from the first background, not 0 away.
        out["toNextBackground"] = every - (count % every) if every > 0 else 0

        mine = _ledger(db).document(uid).get()
        if mine.exists:
            rec = mine.to_dict() or {}
            out["redeemed"] = True
            out["redeemedFrom"] = str(rec.get("referrer_name") or "")
        else:
            out["canRedeem"] = _within_window(doc)
    except Exception as exc:  # noqa: BLE001
        print(f"[referral] state failed for {uid}: {exc}")
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  REDEEM
# ═══════════════════════════════════════════════════════════════════════════
def redeem(db, uid: str, raw_code: str) -> Dict[str, Any]:
    """Pay both sides, exactly once, for one real friend code.

    Firestore wants every read before every write, so all four documents are
    read up front. The create() on referral_redemptions/{uid} at the end is
    what makes "type it twice" and "two tabs at once" impossible."""
    if not _SAFE_ID.match(str(uid or "")):
        return {"ok": False, "error": "bad_request"}

    referrer_uid, err = resolve_code(db, raw_code)
    if err:
        return {"ok": False, "error": err}
    if referrer_uid == uid:
        return {"ok": False, "error": "own_code"}

    coins = reward_coins()
    every = background_every()
    transactional = _transactional()
    me_ref = _users(db).document(uid)
    them_ref = _users(db).document(referrer_uid)
    mine_ref = _ledger(db).document(uid)
    theirs_ref = _ledger(db).document(referrer_uid)
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        mine_prev = mine_ref.get(transaction=t)
        theirs_prev = theirs_ref.get(transaction=t)
        me_snap = me_ref.get(transaction=t)
        them_snap = them_ref.get(transaction=t)

        # ── guard 1: this account has already used a code, ever ────────────
        if mine_prev.exists:
            rec = mine_prev.to_dict() or {}
            return {"ok": False, "error": "already_redeemed",
                    "referrerName": str(rec.get("referrer_name") or "")}

        if not me_snap.exists:
            return {"ok": False, "error": "no_account"}
        if not them_snap.exists:
            return {"ok": False, "error": "no_user"}

        me = me_snap.to_dict() or {}
        them = them_snap.to_dict() or {}

        # ── guard 2: the sign-up window ────────────────────────────────────
        if not _within_window(me):
            return {"ok": False, "error": "window_closed", "days": window_days()}

        # ── guard 3: no two-account merry-go-round ─────────────────────────
        # If the person whose code was typed already redeemed MY code, this is
        # the pair refunding each other. One direction pays; the other does not.
        if theirs_prev.exists:
            rec = theirs_prev.to_dict() or {}
            if str(rec.get("referrer_uid") or "") == uid:
                return {"ok": False, "error": "mutual_referral"}

        my_coins = max(0, _int(_stats_of(me).get("critter_coins")))
        their_coins = max(0, _int(_stats_of(them).get("critter_coins")))
        count_before = max(0, _int(them.get("referral_count")))
        count_after = count_before + 1

        my_update: Dict[str, Any] = {"stats": {"critter_coins": my_coins + coins}}
        their_update: Dict[str, Any] = {
            "stats": {"critter_coins": their_coins + coins},
            "referral_count": count_after,
        }

        # ── every Nth referral: one free background for the REFERRER ───────
        bg_path = ""
        if every > 0 and count_after % every == 0:
            picked = _pick_background(them)
            if picked:
                bg_path = picked
                their_update["unlocked_backgrounds"] = _array_union()([picked])
                their_update["referral_backgrounds"] = \
                    max(0, _int(them.get("referral_backgrounds"))) + 1
            # If they already own every background the milestone still counts —
            # the coins are the guaranteed part. referral_backgrounds is NOT
            # incremented, so the display never claims a background that was
            # not actually granted.

        now = time.time()
        my_name = str(me.get("nickname") or me.get("username") or "")
        their_name = str(them.get("nickname") or them.get("username") or "")
        entry = {
            "uid": uid,
            "username": my_name,
            "referrer_uid": referrer_uid,
            "referrer_name": their_name,
            "coins_each": coins,
            "referrer_count_after": count_after,
            "background_granted": bg_path,
            "at": now,
            "at_iso": _iso(now),
        }

        # merge=True so neither `stats` map loses anything else it holds.
        t.set(me_ref, my_update, merge=True)
        t.set(them_ref, their_update, merge=True)
        # THE guarantee: inside the transaction, so the coins cannot exist
        # without the ledger and the ledger cannot exist without the coins.
        t.create(mine_ref, entry)

        return {"ok": True,
                "coins": coins,
                "coinsTotal": my_coins + coins,
                "referrerName": their_name,
                "referrerCount": count_after,
                "backgroundGranted": bg_path,
                "backgroundEvery": every,
                "toNextBackground": (every - (count_after % every)) if every > 0 else 0}

    try:
        return _run(txn)
    except Exception as exc:  # noqa: BLE001
        # A create() collision means another request won the race — the guard
        # doing its job. Everything else wrote nothing, and saying "already
        # redeemed" to someone who was not paid is the one answer they would
        # believe and never retry.
        if type(exc).__name__ == "AlreadyExists":
            return {"ok": False, "error": "already_redeemed"}
        import traceback
        print(f"[referral] redeem failed for {uid}: {exc}\n{traceback.format_exc(limit=4)}")
        return {"ok": False, "error": "server_error"}


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════════════════════
ERROR_MESSAGES = {
    "unauthorized": "Sign in with Google to use a friend code.",
    "no_account": "We couldn't find your account — try signing in again.",
    "no_code": "Enter your friend's code first.",
    "bad_code": "That doesn't look like a friend code. Try the 4 digits under their name.",
    "no_user": "No player has that friend code.",
    "ambiguous_code": "More than one player has that code — enter it as Name#Code.",
    "own_code": "That's your own friend code!",
    "already_redeemed": "You've already used a friend code on this account.",
    "mutual_referral": "You two already referred each other — only one direction pays out.",
    "window_closed": "Friend codes are a sign-up bonus, and this account is past the window.",
    "firestore_unavailable": "Couldn't reach your account just now. Nothing was awarded.",
    "bad_request": "Something was wrong with that request. Nothing was awarded.",
    "server_error": "Something went wrong. Nothing was awarded — please try again.",
}


def message_for(result: Dict[str, Any]) -> str:
    code = str(result.get("error") or "")
    if code == "window_closed":
        return (f"Friend codes are a sign-up bonus — they can be entered in the first "
                f"{window_days()} days after making an account.")
    return ERROR_MESSAGES.get(code, ERROR_MESSAGES["server_error"])


def _auth_uid(body: Dict[str, Any]) -> Optional[str]:
    tok = body.get("idToken") if isinstance(body.get("idToken"), str) else ""
    claims = _verify_token(tok) if (tok and _verify_token) else None
    return claims.get("uid") if claims and claims.get("uid") else None


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    """POST /api/referral/state   the offer + this account's referral progress
       POST /api/referral/redeem  use a friend's code   { code }
    """
    path = parsed.path
    if not path.startswith("/api/referral/"):
        return False
    action = path[len("/api/referral/"):]

    # Readable signed-out: the sign-up screen advertises the bonus before the
    # account exists, and the reply carries no account data without a token.
    if action == "state":
        handler._send_json(state_payload(_auth_uid(body)))
        return True

    if action == "redeem":
        uid = _auth_uid(body)
        if not uid:
            handler._send_json({"ok": False, "error": "unauthorized",
                                "message": ERROR_MESSAGES["unauthorized"]}, status=401)
            return True
        db = _get_firestore() if _get_firestore else None
        if db is None:
            handler._send_json({"ok": False, "error": "firestore_unavailable",
                                "message": ERROR_MESSAGES["firestore_unavailable"]})
            return True
        code = body.get("code") if isinstance(body.get("code"), str) else ""
        res = redeem(db, uid, code)
        if not res.get("ok"):
            res["message"] = message_for(res)
        handler._send_json(res)
        return True

    handler._send_json({"ok": False, "error": "unknown_action"}, status=404)
    return True
