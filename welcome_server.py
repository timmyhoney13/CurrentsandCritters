"""Currents and Critters: what the server does the moment an account is real.

Two jobs, both server-authoritative, both safe to call on every single sign-in
because both are idempotent:

  1. THE WELCOME BONUS.
     Making an account is worth WELCOME_XP (200 by default). ONCE, ever, per
     account. Playing as a guest is worth nothing: a guest holds no Firebase
     session at all, so a guest cannot even ask, and an anonymous sign-in
     (which does carry a token) is refused by name below.

  2. THE DEV ROSTER.
     The developer account is friends with every player, so its Friends tab is
     the live "who is playing right now" board for the whole game. This is
     ONE-WAY: entries are written into the dev's OWN friends sub-collection and
     nowhere else. No player's account is touched, nobody gets a friend
     request, and nobody sees the dev appear in their list.

WHY THE SERVER OWNS BOTH
  • The bonus hands out XP. A browser that could say "pay me 200 XP" is a
    levelling button. The once-ever guarantee is a create()d ledger document,
    `welcome_bonuses/{uid}`, written INSIDE the payout transaction: the XP
    cannot exist without the ledger entry and the ledger entry cannot exist
    without the XP. Two tabs, two devices and a reinstall all lose the race.
  • The roster writes into one account's sub-collection on behalf of a request
    made by a DIFFERENT account (a stranger signs up; the dev's list grows).
    Firestore rules rightly forbid a browser from doing that. The service
    account is the only thing that can, so it is the thing that does.

Wired additively into multiplayer_server exactly like referral_server /
level_pass_server / discord_server:

    import welcome_server
    welcome_server.init(get_firestore=..., verify_token=...,
                        level_progress=...)                    # in main()
    if welcome_server.handle_post(self, parsed, body): ...      # in do_POST

THE LEVEL FIELDS ARE NOT OPTIONAL
`stats.total_xp` is the truth, but the leaderboard, the header and the Level
Pass all read the DERIVED fields beside it. A grant that moved total_xp and
left `level` behind would show a Level 1 player who is really Level 3 until
their next finished game, so the payout writes the whole derived set from the
one injected curve (never a second copy of the XP table, see
xp-curve-and-demotion-invariant).
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

# ── Injected by init() (no circular import with multiplayer_server) ──────────
_get_firestore: Optional[Callable[[], Any]] = None
_verify_token: Optional[Callable[[str], Optional[dict]]] = None
_level_progress: Optional[Callable[[Any], Any]] = None


def init(*, get_firestore, verify_token, level_progress) -> None:
    global _get_firestore, _verify_token, _level_progress
    _get_firestore = get_firestore
    _verify_token = verify_token
    _level_progress = level_progress


# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════════
DEFAULT_WELCOME_XP = 200        # for making an account. Guests get nothing.

# The developer account, spelled the same way analytics_server and the
# leaderboard filter spell it. This is the account the roster is built FOR.
ADMIN_EMAIL = "currentsandcritters@gmail.com"

# A full roster sync walks every user document, so it is not something a
# reloaded page gets to do four times a minute. New sign-ups do not wait for
# it: they are added the moment they register (add_dev_friend).
SYNC_MIN_INTERVAL_SEC = 60.0
# How long the resolved dev uid is trusted before it is looked up again.
DEV_UID_TTL_SEC = 600.0
# Firestore caps a batch at 500 writes.
_WRITE_BATCH = 400

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def welcome_xp() -> int:
    """The bonus, in XP. One definition, read by the payout AND by the sentence
    the client shows, so the two can never quote different numbers."""
    try:
        n = int(str(os.environ.get("WELCOME_XP", "") or DEFAULT_WELCOME_XP).strip())
    except (TypeError, ValueError):
        n = DEFAULT_WELCOME_XP
    return max(0, min(100_000, n))


ERROR_MESSAGES = {
    "unauthorized": "Sign in to collect your welcome bonus.",
    "guest": "Welcome bonuses are for accounts. Create one and it's yours.",
    "no_account": "Your account is still being created: try again in a moment.",
    "forbidden": "That's a developer-only action.",
    "firestore_unavailable": "Couldn't reach the database. Nothing was awarded.",
    "server_error": "Something went wrong. Nothing was awarded.",
}


def message_for(res: Dict[str, Any]) -> str:
    return ERROR_MESSAGES.get(str(res.get("error") or ""), ERROR_MESSAGES["server_error"])


# ═══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def _users(db):
    return db.collection("users")


def _ledger(db):
    return db.collection("welcome_bonuses")


def _transactional():
    from firebase_admin import firestore
    fn = getattr(firestore, "transactional", None)
    if fn is None:
        from google.cloud.firestore_v1 import transactional as fn  # type: ignore
    return fn


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _stats_of(doc: Dict[str, Any]) -> Dict[str, Any]:
    s = doc.get("stats")
    return s if isinstance(s, dict) else {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_admin_doc(data: Dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return False
    return (data.get("is_admin") is True
            or str(data.get("email") or "").strip().lower() == ADMIN_EMAIL)


# ═══════════════════════════════════════════════════════════════════════════
#  THE WELCOME BONUS
# ═══════════════════════════════════════════════════════════════════════════
def grant_welcome_bonus(db, uid: str) -> Dict[str, Any]:
    """Pay this account the welcome bonus, at most once ever.

    Answering an account that already has it is a SUCCESS with granted=False,
    not an error: every sign-in asks, and "you already had this" is the normal
    answer, not a problem to report."""
    if not _SAFE_ID.match(str(uid or "")):
        return {"ok": False, "error": "bad_request"}

    xp = welcome_xp()
    transactional = _transactional()
    me_ref = _users(db).document(uid)
    mine_ref = _ledger(db).document(uid)
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        prev = mine_ref.get(transaction=t)
        me_snap = me_ref.get(transaction=t)

        if prev.exists:
            rec = prev.to_dict() or {}
            return {"ok": True, "granted": False, "xp": _int(rec.get("xp"), xp),
                    "totalXp": _int(rec.get("total_xp_after")),
                    "level": _int(rec.get("level_after"), 1)}

        # No account document means onboarding has not finished writing it yet.
        # Refusing (rather than creating one) keeps this endpoint incapable of
        # inventing an account, and the next sign-in collects the bonus.
        if not me_snap.exists:
            return {"ok": False, "error": "no_account"}

        me = me_snap.to_dict() or {}
        stats = _stats_of(me)
        before = max(0, _int(stats.get("total_xp")))
        after = before + xp

        update: Dict[str, Any] = {"total_xp": after}
        if _level_progress:
            lvl, xp_cur, xp_goal = _level_progress(after)
            # Kept in lock-step with total_xp so the header, the leaderboard and
            # the Level Pass all see the new level immediately.
            update["level"] = lvl
            update["player_level"] = lvl
            update["xp_current"] = xp_cur
            update["level_xp_current"] = xp_cur
            update["xp_goal"] = xp_goal
            update["level_xp_goal"] = xp_goal
        else:
            lvl = _int(stats.get("level"), 1)

        now = _now()
        # merge=True so the rest of the `stats` map keeps everything it holds.
        t.set(me_ref, {"stats": update}, merge=True)
        # THE guarantee, inside the transaction: no XP without the ledger entry,
        # no ledger entry without the XP.
        t.create(mine_ref, {
            "uid": uid,
            "username": str(me.get("nickname") or ""),
            "xp": xp,
            "total_xp_before": before,
            "total_xp_after": after,
            "level_after": lvl,
            "at": now,
        })
        return {"ok": True, "granted": True, "xp": xp,
                "totalXp": after, "level": lvl}

    try:
        return _run(txn)
    except Exception as exc:  # noqa: BLE001
        # A create() collision means another request won the race: the guard
        # doing its job, and the bonus IS on the account. Say so rather than
        # reporting a failure nobody needs to act on.
        if type(exc).__name__ == "AlreadyExists":
            return {"ok": True, "granted": False, "xp": xp}
        import traceback
        print(f"[welcome] bonus failed for {uid}: {exc}\n{traceback.format_exc(limit=4)}")
        return {"ok": False, "error": "server_error"}


# ═══════════════════════════════════════════════════════════════════════════
#  THE DEV ROSTER
# ═══════════════════════════════════════════════════════════════════════════
_DEV_UID: Dict[str, Any] = {"uid": "", "at": 0.0}
_LAST_SYNC: Dict[str, Any] = {"uid": "", "at": 0.0, "result": {}}


def dev_uid(db) -> str:
    """The developer account's uid, cached. Found by ADMIN_EMAIL first (the
    thing that actually identifies it), then by the is_admin flag for an
    account whose email was never stored on the document."""
    now = time.time()
    if _DEV_UID["uid"] and now - float(_DEV_UID["at"]) < DEV_UID_TTL_SEC:
        return str(_DEV_UID["uid"])
    try:
        found = ""
        for snap in _users(db).where("email", "==", ADMIN_EMAIL).limit(1).stream():
            found = snap.id
            break
        if not found:
            for snap in _users(db).where("is_admin", "==", True).limit(1).stream():
                found = snap.id
                break
    except Exception as exc:  # noqa: BLE001 - a missing roster is never fatal
        print(f"[welcome] could not resolve the dev account: {exc}")
        return str(_DEV_UID["uid"])
    if found:
        _DEV_UID.update(uid=found, at=now)
    return found


def _roster_entry(uid: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """One friends-list row. The Friends tab reads the live user document for
    the name, avatar and online dot, so these fields are only the fallback for
    a profile that cannot be read: `auto` marks the row as one the roster put
    there rather than one a person accepted."""
    return {
        "uid": uid,
        "nickname": str(data.get("nickname") or "Player"),
        "avatar_url": str(data.get("avatar_url") or ""),
        "added_at": _now(),
        "auto": True,
    }


def _all_user_rows(users) -> Iterable[Any]:
    """Every user document, carrying only the two fields a roster row needs."""
    try:
        return users.select(["nickname", "avatar_url"]).stream()
    except Exception:
        return users.stream()


def add_dev_friend(db, uid: str) -> bool:
    """Put ONE account into the dev's friends list. Called when a new account
    registers, so the dev sees them without waiting for the next full sync."""
    # Called straight off the register endpoint, where Firestore may not be
    # configured at all. A missing database is a quiet no-op, never a stack
    # trace printed once per sign-up.
    if db is None or not _SAFE_ID.match(str(uid or "")):
        return False
    owner = dev_uid(db)
    if not owner or owner == uid:
        return False
    try:
        snap = _users(db).document(uid).get()
        if not snap.exists:
            # A uid nobody has ever been. The register endpoint takes a raw uid
            # from the browser, so this is the check that stops made-up ids
            # becoming rows in the dev's friends list.
            return False
        _users(db).document(owner).collection("friends").document(uid).set(
            _roster_entry(uid, snap.to_dict() or {}), merge=True)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[welcome] could not add {uid} to the dev roster: {exc}")
        return False


def sync_dev_roster(db, owner_uid: str) -> Dict[str, Any]:
    """Make the dev's friends list hold every account there is.

    Only the MISSING ones are written, so a second call a minute later costs
    one read per player and no writes at all. Rate-limited, because this walks
    the whole user table and a reloaded page must not be able to walk it
    again and again."""
    if db is None:
        return {"ok": False, "error": "firestore_unavailable"}
    if not _SAFE_ID.match(str(owner_uid or "")):
        return {"ok": False, "error": "bad_request"}

    now = time.time()
    if (_LAST_SYNC["uid"] == owner_uid
            and now - float(_LAST_SYNC["at"]) < SYNC_MIN_INTERVAL_SEC
            and _LAST_SYNC["result"]):
        out = dict(_LAST_SYNC["result"])
        out["cached"] = True
        return out

    try:
        friends = _users(db).document(owner_uid).collection("friends")
        have = {snap.id for snap in friends.stream()}

        total = 0
        added: List[str] = []
        batch = db.batch()
        pending = 0
        for snap in _all_user_rows(_users(db)):
            uid = snap.id
            if uid == owner_uid:
                continue
            total += 1
            if uid in have:
                continue
            batch.set(friends.document(uid), _roster_entry(uid, snap.to_dict() or {}),
                      merge=True)
            added.append(uid)
            pending += 1
            if pending >= _WRITE_BATCH:
                batch.commit()
                batch = db.batch()
                pending = 0
        if pending:
            batch.commit()
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(f"[welcome] roster sync failed for {owner_uid}: "
              f"{exc}\n{traceback.format_exc(limit=4)}")
        return {"ok": False, "error": "server_error"}

    result = {"ok": True, "players": total, "added": len(added),
              "friends": len(have | set(added)), "cached": False}
    _LAST_SYNC.update(uid=owner_uid, at=now, result=dict(result))
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════════════
def _claims(body: Dict[str, Any]) -> Optional[dict]:
    if not _verify_token:
        return None
    tok = body.get("idToken") if isinstance(body.get("idToken"), str) else ""
    return _verify_token(tok)


def _is_anonymous(claims: dict) -> bool:
    """An anonymous Firebase session is a guest wearing a token. The game's
    guests do not have one at all, but this is the door that would let them in
    if that ever changed, so it is shut here rather than assumed shut."""
    fb = claims.get("firebase")
    provider = (fb or {}).get("sign_in_provider") if isinstance(fb, dict) else ""
    return str(provider or "").strip().lower() == "anonymous"


def _is_dev(db, claims: dict) -> bool:
    """The caller IS the developer account. The email on the verified token is
    the primary proof; the account document is the fallback for a token that
    carries no email (a username-and-password login)."""
    if str(claims.get("email") or "").strip().lower() == ADMIN_EMAIL:
        return True
    try:
        snap = _users(db).document(str(claims.get("uid") or "")).get()
        return snap.exists and _is_admin_doc(snap.to_dict() or {})
    except Exception:  # noqa: BLE001
        return False


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    """POST /api/welcome/claim    collect the once-ever welcome bonus
       POST /api/welcome/roster   dev only: every player into the dev's friends
    """
    path = parsed.path
    if not path.startswith("/api/welcome/"):
        return False
    action = path[len("/api/welcome/"):]
    if action not in ("claim", "roster"):
        handler._send_json({"ok": False, "error": "unknown_action"}, status=404)
        return True

    claims = _claims(body) or {}
    uid = str(claims.get("uid") or "")
    if not uid:
        handler._send_json({"ok": False, "error": "unauthorized",
                            "message": ERROR_MESSAGES["unauthorized"]}, status=401)
        return True
    if _is_anonymous(claims):
        handler._send_json({"ok": False, "error": "guest",
                            "message": ERROR_MESSAGES["guest"]}, status=403)
        return True

    db = _get_firestore() if _get_firestore else None
    if db is None:
        handler._send_json({"ok": False, "error": "firestore_unavailable",
                            "message": ERROR_MESSAGES["firestore_unavailable"]})
        return True

    if action == "claim":
        res = grant_welcome_bonus(db, uid)
        if not res.get("ok"):
            res["message"] = message_for(res)
        handler._send_json(res)
        return True

    # action == "roster"
    if not _is_dev(db, claims):
        handler._send_json({"ok": False, "error": "forbidden",
                            "message": ERROR_MESSAGES["forbidden"]}, status=403)
        return True
    res = sync_dev_roster(db, uid)
    if not res.get("ok"):
        res["message"] = message_for(res)
    handler._send_json(res)
    return True
