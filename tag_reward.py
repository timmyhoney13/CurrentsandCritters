"""Currents and Critters, the plush-tag QR reward (server-authoritative).

The QR code printed on the plush hang tag opens the game at

    https://play.currentsandcritters.com/?tag=<TOKEN>

and the first account that claims with that token gets the exclusive Fish
avatar skin, ONCE PER ACCOUNT, forever. Wired additively into
multiplayer_server the same way discord_server is:

    import tag_reward
    tag_reward.init(get_firestore=..., verify_token=...)   # in main()
    if tag_reward.handle_post(self, parsed, body): ...     # in do_POST

THE RULES
  1. Only the tag's QR gives it. The claim needs the token that is printed in
     the QR, and nothing else in the game grants the skin through this door.
  2. Any signed-in account can claim it. Whoever is signed in when the claim
     lands gets it (guests are asked to sign in first; the browser holds the
     token until they do).
  3. Once per account, forever. `tag_rewards/u_{uid}` is create()d inside the
     same transaction that writes the avatar, so a double tap, two tabs or two
     devices end with ONE grant. The ledger is never deleted, so an account
     that trades the Fish away cannot scan again for another one.
  4. It stays. The Fish is a "code" unlock (prestige_server
     KEEP_FOREVER_UNLOCK_TYPES), so Prestige never relocks it; it lives in
     users/{uid}.unlocked_icons like every other avatar, so the ordinary trade
     system is the only thing that can move it off the account.

An account that ALREADY owns the Fish (for example from a donation code) is
told so and its one claim is NOT spent: nothing was given, so nothing is used.

WHAT IS STORED IS NOT THE TOKEN
This repository is public, so the token itself never appears in it. Only its
SHA-256 is here; the plaintext exists in the printed QR and nowhere else. A new
print run with a new QR = add its hash to ACCEPTED_TOKEN_SHA256.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

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
REWARD_ICON = "/avatars/fish.png"
CAMPAIGN = "plush-tag-qr"

# SHA-256 of every token a printed tag carries. The first entry is the QR on
# the original Currents and Critters plush hang tag (November 2026).
ACCEPTED_TOKEN_SHA256 = (
    "619843838ae83cfa8d36bd746cac858c603c6c51f7dd76953e4797fd2f411ed1",
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

MESSAGES = {
    "ok":                    "You unlocked an exclusive skin!",
    "already_claimed":       "This account has already claimed the tag's free skin.",
    "already_owned":         "You already own this exclusive skin.",
    "bad_token":             "That QR link isn't one of ours.",
    "unauthorized":          "Sign in or create a free account to claim your exclusive skin.",
    "no_account":            "Finish creating your account, then scan again to claim your skin.",
    "firestore_unavailable": "Couldn't reach your account just now. Nothing was claimed: try again.",
    "server_error":          "Something went wrong. Nothing was claimed: try again.",
}


def token_ok(token: Any) -> bool:
    """Constant-time check of a claimed token against the printed ones."""
    tok = str(token or "").strip()
    if not _TOKEN_SHAPE.match(tok):
        return False
    digest = hashlib.sha256(tok.encode("utf-8")).hexdigest()
    ok = False
    for want in ACCEPTED_TOKEN_SHA256:
        # No early exit: every accepted hash is compared every time.
        ok = hmac.compare_digest(digest, want) or ok
    return ok


# ═══════════════════════════════════════════════════════════════════════════
#  FIRESTORE, the grant and its "exactly once per account" guard
# ═══════════════════════════════════════════════════════════════════════════
def _users(db):
    return db.collection("users")


def _ledger(db):
    return db.collection("tag_rewards")


def _txn_helpers():
    from firebase_admin import firestore
    transactional = getattr(firestore, "transactional", None)
    if transactional is None:
        from google.cloud.firestore_v1 import transactional  # type: ignore
    return transactional


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _owned_icons(udoc: Dict[str, Any]) -> list:
    raw = udoc.get("unlocked_icons")
    return [str(x) for x in raw] if isinstance(raw, list) else []


def _owns_reward(icons: list) -> bool:
    return any(str(x or "").split("?")[0].strip().lower() == REWARD_ICON for x in icons)


def claim_state(db, uid: str) -> Dict[str, Any]:
    """Has this account already used its tag claim? Read-only."""
    if not _SAFE_ID.match(str(uid or "")):
        return {"claimed": False}
    snap = _ledger(db).document(f"u_{uid}").get()
    if not snap.exists:
        return {"claimed": False}
    rec = snap.to_dict() or {}
    return {"claimed": True, "claimedAt": rec.get("at_iso") or ""}


def grant(db, uid: str) -> Dict[str, Any]:
    """Give `uid` the Fish exactly once. The token has already been checked.

    All reads happen before all writes (a Firestore transaction requires it),
    and the ledger doc is create()d in the same transaction as the avatar, so
    the skin cannot exist without the ledger and the ledger cannot exist
    without the skin."""
    if not _SAFE_ID.match(str(uid or "")):
        return {"ok": False, "error": "unauthorized"}

    transactional = _txn_helpers()
    user_ref = _users(db).document(uid)
    led_ref = _ledger(db).document(f"u_{uid}")
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        led_prev = led_ref.get(transaction=t)
        user_snap = user_ref.get(transaction=t)

        # This account already used its one claim, whether or not it still
        # holds the Fish (trading it away does not earn a second one).
        if led_prev.exists:
            rec = led_prev.to_dict() or {}
            return {"ok": False, "error": "already_claimed",
                    "claimed_at": rec.get("at_iso") or ""}

        if not user_snap.exists:
            return {"ok": False, "error": "no_account"}

        udoc = user_snap.to_dict() or {}
        icons = _owned_icons(udoc)
        if _owns_reward(icons):
            # Nothing to give, so the claim is not spent.
            return {"ok": False, "error": "already_owned", "icon": REWARD_ICON}

        now = time.time()
        entry = {
            "uid": uid,
            "username": str(udoc.get("nickname") or udoc.get("username") or ""),
            "icon": REWARD_ICON,
            "campaign": CAMPAIGN,
            "at": now,
            "at_iso": _iso(now),
        }
        # merge=True so nothing else on the account is disturbed. The full list
        # is written (not an ArrayUnion) because it was read in this same
        # transaction, which is what makes it safe against a concurrent write.
        t.set(user_ref, {"unlocked_icons": icons + [REWARD_ICON]}, merge=True)
        # THE guarantee: a second create() of this id fails the whole
        # transaction, avatar write included.
        t.create(led_ref, entry)
        return {"ok": True, "icon": REWARD_ICON, "claimed_at": entry["at_iso"]}

    try:
        return _run(txn)
    except Exception as exc:  # noqa: BLE001
        # A create() collision means another request for this account won the
        # race: the guard working, not an outage.
        if type(exc).__name__ == "AlreadyExists":
            return {"ok": False, "error": "already_claimed"}
        import traceback
        print(f"[tag-reward] grant failed for {uid}: {exc}\n{traceback.format_exc(limit=4)}")
        return {"ok": False, "error": "server_error"}


def claim(uid: Optional[str], token: Any) -> Dict[str, Any]:
    """The whole claim: token → signed-in account → grant."""
    if not token_ok(token):
        return {"ok": False, "error": "bad_token"}
    if not uid:
        return {"ok": False, "error": "unauthorized"}
    db = _get_firestore() if _get_firestore else None
    if db is None:
        return {"ok": False, "error": "firestore_unavailable"}
    return grant(db, uid)


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════════════════════
def _auth_uid(body: Dict[str, Any]) -> Optional[str]:
    tok = body.get("idToken") if isinstance(body.get("idToken"), str) else ""
    claims = _verify_token(tok) if (tok and _verify_token) else None
    return claims.get("uid") if claims and claims.get("uid") else None


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    """POST /api/tag-reward/claim  {idToken, token}"""
    if parsed.path != "/api/tag-reward/claim":
        return False
    body = body if isinstance(body, dict) else {}
    token = body.get("token")
    # The token is checked BEFORE the account, so a bad link never costs a
    # Firebase verification and never reveals anything about an account.
    if not token_ok(token):
        result: Dict[str, Any] = {"ok": False, "error": "bad_token"}
    else:
        result = claim(_auth_uid(body), token)
    result["message"] = MESSAGES.get(str(result.get("error") or "ok"), MESSAGES["server_error"])
    status = 401 if result.get("error") == "unauthorized" else 200
    handler._send_json(result, status=status)
    return True
