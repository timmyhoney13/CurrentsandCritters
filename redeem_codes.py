"""Currents and Critters: purchase redemption codes.

WHAT THIS FIXES
A donation made on the marketing site is paid on Stripe, not in the game. The
webhook can only credit an account it can IDENTIFY, and from the website there
is usually nothing to identify it with: the tier buttons in index.html are plain
links, so no `client_reference_id` (Firebase uid) rides along. What was left was
the checkout email, matched against the account's email by /claim.

That match cannot work for most of the player base. A username-and-password
account's Firebase email is SYNTHETIC (`name@players.currentsandcritters.com`,
see account_email.py) and its stored `email` is "". It will never equal the real
address someone typed at Stripe, so the buyer could pay and have no way at all
to reach their coins.

A code fixes it because a code proves nothing about identity and does not have
to. It goes to whoever paid, at the address they paid with, and whoever holds it
can spend it on whatever account they like. That is the correct model for a gift
of currency, and it is the same model as a shop gift card.

THE ONE LOCK
A purchase's reward can be delivered by two doors: this code, and the older
email match in _claim_guest_rewards. Two doors on two locks would pay twice, so
there is exactly ONE lock: `unclaimedRewards/{stripeSessionId}.status`. Both
doors open that same document inside a transaction and both refuse when it
already says "claimed". `redeemCodes/{codeKey}` is a POINTER and nothing else,
it holds no status of its own precisely so it cannot disagree with the reward.

WHAT IS STORED IS NOT THE CODE
`redeemCodes` is keyed by an HMAC of the code, never the code itself, so a read
of the database is not a pile of spendable codes. That needs a secret; with none
configured this module refuses to mint rather than key the collection on
plaintext, and the older email-claim path carries on as before.

    redeem_codes.init(...)                       wire up (multiplayer_server)
    redeem_codes.make_code()                     a fresh plaintext code
    redeem_codes.code_key(code)                  its storage key (HMAC)
    redeem_codes.redeem(uid, code)               spend one, transactionally
    redeem_codes.build_email(...)                the message the buyer gets
    redeem_codes.handle_post(...)                POST /api/redeem/code
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from typing import Any, Callable, Dict, Optional, Tuple

# Injected by multiplayer_server.init_* so this module owns no globals of its
# own and can be unit-tested with fakes.
_get_firestore: Optional[Callable[[], Any]] = None
_verify_token: Optional[Callable[[str], Optional[dict]]] = None
_grant_updates: Optional[Callable[[str, Any, Dict[str, Any]], Tuple[Dict[str, Any], int]]] = None


def init(*, get_firestore, verify_token, grant_updates) -> None:
    """`grant_updates(kind, value, user_doc) -> (updates, credited)` is the
    SAME helper the Stripe webhook and the email claim use. Injected rather
    than reimplemented: a second copy of "what a purchase is worth" is how a
    code ends up paying a different amount than the purchase it came from."""
    global _get_firestore, _verify_token, _grant_updates
    _get_firestore = get_firestore
    _verify_token = verify_token
    _grant_updates = grant_updates


# ═══════════════════════════════════════════════════════════════════════════
#  CODE FORMAT
# ═══════════════════════════════════════════════════════════════════════════
# No 0/O/1/I/L. This one arrives by email and is usually pasted, but people do
# retype it off a phone screen, and those five are where that goes wrong.
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_GROUPS = 4
CODE_GROUP_LEN = 4
CODE_LEN = CODE_GROUPS * CODE_GROUP_LEN         # 16 chars, ~79 bits
CODE_PREFIX = "CC"

# A code is a bearer token for real money's worth of goods, so guessing has to
# be pointless as well as improbable.
REDEEM_MAX_FAILS = 6
REDEEM_FAIL_WINDOW_SEC = 15 * 60

_redeem_fails: Dict[str, Tuple[int, float]] = {}

MESSAGES = {
    "unauthorized":    "Sign in to your account first, then enter the code.",
    "no_code":         "Enter the code from your email.",
    "bad_code":        "That code isn't one of ours. Check the email your receipt came in.",
    "not_found":       "That code isn't one of ours. Check the email your receipt came in.",
    "already_claimed": "That code has already been used.",
    "locked":          "Too many wrong codes. Wait 15 minutes and try again.",
    "unavailable":     "Couldn't reach the server. Try again in a moment.",
    "no_secret":       "Redemption codes are not configured on this server.",
}


def _secret() -> bytes:
    """Shared with the account-email tokens by design: one rotation, one blast
    radius. Rotating it makes every UNSPENT code unreachable, which is why it
    falls back through the other long-lived secrets instead of being generated
    per boot."""
    for name in ("REDEEM_CODE_SECRET", "ACCOUNT_EMAIL_SECRET",
                 "NEWSLETTER_UNSUBSCRIBE_SECRET", "SESSION_SECRET"):
        s = os.environ.get(name, "").strip()
        if s:
            return s.encode("utf-8")
    return b""


def secret_configured() -> bool:
    return bool(_secret())


def format_code(raw: str) -> str:
    """The shape the buyer sees: CC-ABCD-EFGH-JKMN-PQRS."""
    s = str(raw or "")
    body = "-".join(s[i:i + CODE_GROUP_LEN] for i in range(0, len(s), CODE_GROUP_LEN))
    return f"{CODE_PREFIX}-{body}" if body else ""


def normalize_code(value: Any) -> str:
    """What was typed, back to the code that was issued.

    Case, dashes, spaces and the CC prefix are all decoration: people paste
    codes with whatever the mail client wrapped them in. Anything left over
    that is not in the alphabet means this is not one of our codes.
    """
    s = str(value or "").upper()
    s = re.sub(r"[\s\-_.]+", "", s)
    if s.startswith(CODE_PREFIX):
        s = s[len(CODE_PREFIX):]
    if len(s) != CODE_LEN:
        return ""
    if any(ch not in CODE_ALPHABET for ch in s):
        return ""
    return s


def make_code() -> str:
    """A fresh code from the system CSPRNG. secrets.choice, never random."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def derive_code(session_id: Any) -> str:
    """The code for a Stripe session, DERIVED rather than drawn at random.

    Stripe retries a webhook it did not get a 200 for, and delivers some events
    more than once. A random code would mint a second one for a purchase that
    already had one, leaving two live codes and two different emails for the
    same payment. Deriving it means every delivery computes the SAME code, and
    it can be recomputed later for a buyer whose email never arrived, without
    reading (or rewriting) the reward record.

    Safe against guessing for the same reason code_key is: the input is a
    `cs_live_…` session id nobody else can predict, and the output is folded
    through HMAC with the server secret. Empty with no secret, which every
    caller checks.
    """
    sid = str(session_id or "").strip()
    key = _secret()
    if not sid or not key:
        return ""
    digest = hmac.new(key, ("redeem-code-seed:" + sid).encode("utf-8"),
                      hashlib.sha256).digest()
    # Rejection-free mapping: 31 does not divide 256, so a plain modulo would
    # make the first few symbols very slightly likelier. With ~79 bits of input
    # entropy that bias is irrelevant to guessing, but taking 2 bytes per symbol
    # costs nothing and removes the question.
    out = []
    for i in range(CODE_LEN):
        chunk = int.from_bytes(digest[(i * 2) % len(digest):][:2] or b"\0\0", "big")
        out.append(CODE_ALPHABET[chunk % len(CODE_ALPHABET)])
    return "".join(out)


def code_key(code: Any) -> str:
    """The `redeemCodes` document id for a code: an HMAC, never the plaintext.

    Empty when the code is malformed OR this server has no secret. Every caller
    checks, because a collection keyed on plaintext would turn one database
    read into a pile of spendable codes.
    """
    norm = normalize_code(code)
    key = _secret()
    if not norm or not key:
        return ""
    return hmac.new(key, ("redeem-code:" + norm).encode("utf-8"),
                    hashlib.sha256).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
#  FIRESTORE SHIMS
# ═══════════════════════════════════════════════════════════════════════════
# Fetched through a function, not imported at module scope, for the same reason
# referral_server does it: the tests run this module against an in-memory
# Firestore on a machine with no firebase_admin installed, and a module-scope
# import would make the file unimportable there.
def _transactional():
    from firebase_admin import firestore
    fn = getattr(firestore, "transactional", None)
    if fn is None:
        from google.cloud.firestore_v1 import transactional as fn  # type: ignore
    return fn


def _server_timestamp():
    from firebase_admin import firestore
    return getattr(firestore, "SERVER_TIMESTAMP", None)


# ═══════════════════════════════════════════════════════════════════════════
#  RATE LIMIT
# ═══════════════════════════════════════════════════════════════════════════
def _locked(key: str, now: float) -> bool:
    fails, first = _redeem_fails.get(key, (0, 0.0))
    if not fails:
        return False
    if now - first > REDEEM_FAIL_WINDOW_SEC:
        _redeem_fails.pop(key, None)
        return False
    return fails >= REDEEM_MAX_FAILS


def _failed(key: str, now: float) -> None:
    fails, first = _redeem_fails.get(key, (0, 0.0))
    if not fails or now - first > REDEEM_FAIL_WINDOW_SEC:
        _redeem_fails[key] = (1, now)
    else:
        _redeem_fails[key] = (fails + 1, first)


def _clear_fails(key: str) -> None:
    _redeem_fails.pop(key, None)


def reset_rate_limit() -> None:
    """Tests only."""
    _redeem_fails.clear()


# ═══════════════════════════════════════════════════════════════════════════
#  REDEEM
# ═══════════════════════════════════════════════════════════════════════════
def _err(code: str, **extra: Any) -> Dict[str, Any]:
    out = {"ok": False, "error": code, "message": MESSAGES.get(code, MESSAGES["bad_code"])}
    out.update(extra)
    return out


def redeem(uid: str, code: Any) -> Dict[str, Any]:
    """Spend one code onto one account.

    Order matters. The code is turned into its storage key first (so a
    malformed code never touches the database), the pointer resolves it to a
    purchase, and only then does a transaction on the REWARD document decide
    anything. The reward document is the single lock: if it already says
    claimed, this returns already_claimed whether the first claim came through
    a code or through the older email match.
    """
    if not uid:
        return _err("unauthorized")
    if not str(code or "").strip():
        return _err("no_code")
    if not secret_configured():
        return _err("no_secret")

    now = time.time()
    if _locked(uid, now):
        return _err("locked")

    key = code_key(code)
    if not key:
        _failed(uid, now)
        return _err("bad_code")

    db = _get_firestore() if _get_firestore else None
    if db is None:
        return _err("unavailable")

    try:
        ptr = db.collection("redeemCodes").document(key).get()
    except Exception as exc:  # noqa: BLE001
        print(f"[redeem] pointer read failed: {exc}")
        return _err("unavailable")
    if not ptr.exists:
        _failed(uid, now)
        return _err("not_found")

    session_id = str((ptr.to_dict() or {}).get("stripeSessionId") or "").strip()
    if not session_id:
        return _err("not_found")

    # A real code was presented: this is not a guessing attempt, whatever the
    # reward document goes on to say.
    _clear_fails(uid)

    transactional = _transactional()
    SERVER_TIMESTAMP = _server_timestamp()

    reward_ref = db.collection("unclaimedRewards").document(session_id)
    user_ref = db.collection("users").document(uid)

    @transactional
    def _spend(t) -> Dict[str, Any]:
        rsnap = reward_ref.get(transaction=t)
        if not rsnap.exists:
            return _err("not_found")
        rdata = rsnap.to_dict() or {}
        if str(rdata.get("status") or "") == "claimed":
            return _err("already_claimed",
                        rewardName=str(rdata.get("rewardName") or ""))

        usnap = user_ref.get(transaction=t)
        if not usnap.exists:
            return _err("unauthorized")
        udoc = usnap.to_dict() or {}

        updates, credited = _grant_updates(
            str(rdata.get("rewardKind") or ""), rdata.get("rewardValue"), udoc)
        if updates:
            t.set(user_ref, updates, merge=True)
        t.set(reward_ref, {
            "status":        "claimed",
            "claimedByUid":  uid,
            "claimedVia":    "code",
            "claimedAt":     SERVER_TIMESTAMP,
        }, merge=True)
        return {"ok": True,
                "rewardName":  str(rdata.get("rewardName") or "your purchase"),
                "rewardKind":  str(rdata.get("rewardKind") or ""),
                "rewardValue": rdata.get("rewardValue"),
                "coins":       int(credited or 0)}

    try:
        out = _spend(db.transaction())
    except Exception as exc:  # noqa: BLE001
        print(f"[redeem] spend failed for {uid}: {exc}")
        return _err("unavailable")

    if out.get("ok"):
        print(f"[redeem] {uid} spent a code for session {session_id}: "
              f"{out.get('rewardName')} (+{out.get('coins')} coins)")
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  THE EMAIL
# ═══════════════════════════════════════════════════════════════════════════
def build_email(*, code: str, reward_name: str, already_credited: bool,
                account_hint: str = "", game_url: str = "") -> Tuple[str, str, str]:
    """(subject, html_body, text_body) for the receipt that carries the code.

    Two shapes, because the honest message differs. If the purchase already
    landed on an account the code is a receipt and a spare key, and saying
    "redeem this now" would send someone hunting for coins they already have.
    If it did not, the code IS the delivery and the instructions are the point.
    """
    shown = format_code(code)
    url = (game_url or "https://play.currentsandcritters.com").rstrip("/")
    what = reward_name or "your purchase"

    if already_credited:
        subject = f"Your {what} is in your account"
        lead_t = (f"Thank you! {what} has already been added"
                  + (f" to {account_hint}." if account_hint else " to your account.")
                  + "\n\nYou do not need to do anything else. Keep the code below "
                    "in case you ever need to prove this purchase:")
        lead_h = (f"Thank you! <b>{_esc(what)}</b> has already been added"
                  + (f" to <b>{_esc(account_hint)}</b>." if account_hint else " to your account.")
                  + "</p><p>You do not need to do anything else. Keep the code below in "
                    "case you ever need to prove this purchase:")
        tail_t = ""
        tail_h = ""
    else:
        subject = f"Your code for {what}"
        lead_t = (f"Thank you for supporting Currents and Critters!\n\n"
                  f"{what} is waiting for you. Here is your code:")
        lead_h = (f"Thank you for supporting Currents and Critters!</p>"
                  f"<p><b>{_esc(what)}</b> is waiting for you. Here is your code:")
        tail_t = ("\n\nTo redeem it:\n"
                  f"  1. Open the game at {url} and sign in.\n"
                  "  2. Go to the Friends tab on your Player Home.\n"
                  "  3. Paste the code into the code box and press Redeem.\n\n"
                  "The code works on any account, so it is fine to redeem it on "
                  "an account you make later. It can only be used once.")
        tail_h = (f"</p><p><b>To redeem it:</b></p><ol>"
                  f'<li>Open the game at <a href="{_esc(url)}">{_esc(url)}</a> and sign in.</li>'
                  "<li>Go to the <b>Friends</b> tab on your Player Home.</li>"
                  "<li>Paste the code into the code box and press <b>Redeem</b>.</li>"
                  "</ol><p>The code works on any account, so it is fine to redeem it on "
                  "an account you make later. It can only be used once.")

    text_body = f"{lead_t}\n\n    {shown}\n{tail_t}\n\n- Currents and Critters\n"
    html_body = (
        '<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
        'font-size:15px;line-height:1.55;color:#12303d;max-width:520px">'
        f"<p>{lead_h}</p>"
        '<p style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
        'font-size:22px;font-weight:700;letter-spacing:.06em;background:#eef7fb;'
        'border:1px solid #cfe6f0;border-radius:10px;padding:14px 16px;'
        f'text-align:center;color:#0b4a63">{_esc(shown)}</p>'
        f"<p>{tail_h}</p>"
        '<p style="color:#5a7b88;font-size:13px">- Currents and Critters</p></div>'
    )
    return subject, html_body, text_body


def _esc(s: Any) -> str:
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════════════════════
def _auth_uid(body: Dict[str, Any]) -> str:
    """The uid comes off the VERIFIED token, never out of the body: a code that
    could be redeemed onto a uid the caller merely names is a code anyone can
    spend onto anyone."""
    if not _verify_token:
        return ""
    claims = _verify_token(str(body.get("idToken") or ""))
    return str((claims or {}).get("uid") or "")


def state() -> Dict[str, Any]:
    """Whether this server can resolve a code at all.

    Public on purpose, and it carries no secret material: it says only whether
    a feature is configured, the same way the Discord offer does. It exists
    because the alternative is finding out from a paying customer. Without it
    the only place "codes are OFF" is written down is the boot log, so a
    deploy that lost its secret would look completely healthy from outside
    while every buyer's code bounced.
    """
    return {"ok": True, "enabled": secret_configured()}


def handle_get(handler, parsed) -> bool:
    """GET /api/redeem/state  → {ok, enabled}"""
    if parsed.path != "/api/redeem/state":
        return False
    handler._send_json(state())
    return True


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    """POST /api/redeem/code   { idToken, code }
       POST /api/redeem/state  (the same answer as the GET, for the client)"""
    path = parsed.path
    if not path.startswith("/api/redeem/"):
        return False
    action = path[len("/api/redeem/"):]

    if action == "state":
        handler._send_json(state())
        return True

    if action == "code":
        uid = _auth_uid(body)
        if not uid:
            handler._send_json(_err("unauthorized"), status=401)
            return True
        handler._send_json(redeem(uid, body.get("code")))
        return True

    return False
