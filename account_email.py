"""Currents and Critters, the email address on a username-and-password account.

WHY THIS EXISTS
A username here is not an email. Firebase Authentication only knows about
email + password, so `mermaid_92` signs in as
`mermaid_92@players.currentsandcritters.com`: a synthetic address at a domain
that receives nothing. That is what makes Firebase's own uniqueness check on
email into a uniqueness check on the username (see CC_LOGIN_DOMAIN in
preview-app.js), and it is also why, until now, a forgotten password was the
end of the account. There was nobody to mail.

So a REAL address is kept BESIDE the account rather than on it:

    users/{uid}.recovery_email           the address, lower-cased
    users/{uid}.recovery_email_verified  true only after the link was clicked
    users/{uid}.recovery_email_at        when it was last set

Beside, not on, because the account's own email IS its login name: calling
updateEmail() would change the name the player types to get back in. Nothing
about signing in changes here.

WHAT A RESET ACTUALLY IS
Firebase Admin generates its own password-reset link for the SYNTHETIC address
(that is the account it resets), and this module mails that link to the real
one. Firebase never sends it; it cannot, the address it holds is not deliverable.

THE FOUR RULES
  1. An address is not linked until its confirmation link is clicked. Anything
     else lets a typo become the way into somebody's account, and lets one
     player point a stranger's address at their own account.
  2. One confirmed address per account, and one account per confirmed address.
  3. /api/account/forgot-password answers the SAME WAY whatever it finds. A
     sign-in screen must not be a way to ask which usernames exist.
  4. Everything that writes proves ownership with a verified Firebase ID token;
     the uid always comes from the token and never from the body.

THE OTHER WAY BACK IN
An email is optional and most players never link one, so there is a second
door that needs nothing but a piece of paper: a RECOVERY CODE, issued to every
username-and-password account and stored only as an HMAC. Username + code +
a new password gets a locked-out player back in without any mail at all. See
THE RECOVERY CODE below for why it is single-use and why redeeming one hands
back a fresh one in the same breath.

Wired additively into multiplayer_server exactly like referral_server:

    import account_email
    account_email.init(get_firestore=..., verify_token=..., login_domain=...)
    if account_email.handle_post(self, parsed, body): ...
    if account_email.handle_get(self, parsed): ...
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html as _html
import os
import re
import time
from typing import Any, Callable, Dict, Optional, Tuple

import newsletter_email as nl_email

# ── Injected by init() (no circular import with multiplayer_server) ──────────
_get_firestore: Optional[Callable[[], Any]] = None
_verify_token: Optional[Callable[[str], Optional[dict]]] = None
_login_domain: str = "players.currentsandcritters.com"


def init(*, get_firestore, verify_token, login_domain: str = "") -> None:
    global _get_firestore, _verify_token, _login_domain
    _get_firestore = get_firestore
    _verify_token = verify_token
    if login_domain:
        _login_domain = str(login_domain).strip().lower()


# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════════
CONFIRM_TTL_SEC = 48 * 3600      # how long a confirmation link stays good
LINK_COOLDOWN_SEC = 60           # per account, so this is not a mail cannon

# Said the same way to everybody, whatever was found. See rule 3.
FORGOT_ANSWER = (
    "If that account has a confirmed email, a reset link is on its way to it. "
    "An account with no email cannot be reset: play as a guest, or create a new account."
)

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$")
_last_link_at: Dict[str, float] = {}


def valid_email(value: Any) -> bool:
    """The same shape the browser checks, checked again where it counts."""
    v = str(value or "").strip()
    if not (6 <= len(v) <= 190):
        return False
    if v.lower().endswith("@" + _login_domain):
        return False        # the synthetic login address is not a way back in
    return bool(_EMAIL_RE.match(v))


def normalize(value: Any) -> str:
    return str(value or "").strip().lower()


# ═══════════════════════════════════════════════════════════════════════════
#  THE CONFIRMATION TOKEN
#  uid.b64(email).exp.mac: signed, self-describing, and useless on any other
#  account: the mac covers the uid AND the address, so a token for one cannot
#  confirm the other.
# ═══════════════════════════════════════════════════════════════════════════
def _secret() -> bytes:
    s = os.environ.get("ACCOUNT_EMAIL_SECRET", "").strip()
    if not s:
        s = os.environ.get("NEWSLETTER_UNSUBSCRIBE_SECRET", "").strip()
    if not s:
        s = os.environ.get("SESSION_SECRET", "").strip()
    return s.encode("utf-8") if s else b""


def secret_configured() -> bool:
    return bool(_secret())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(txt: str) -> bytes:
    pad = "=" * (-len(txt) % 4)
    return base64.urlsafe_b64decode(txt + pad)


def _mac(uid: str, email_lower: str, exp: int) -> str:
    key = _secret()
    if not key:
        return ""
    msg = "verify-email:%s:%s:%d" % (uid, email_lower, exp)
    return _b64(hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest())[:32]


def make_token(uid: str, email_lower: str, now: Optional[float] = None) -> str:
    exp = int((now if now is not None else time.time()) + CONFIRM_TTL_SEC)
    mac = _mac(uid, email_lower, exp)
    if not mac:
        return ""
    return "%s.%s.%d.%s" % (uid, _b64(email_lower.encode("utf-8")), exp, mac)


def read_token(token: str, now: Optional[float] = None) -> Optional[Tuple[str, str]]:
    """(uid, email) if the token is intact, unexpired and really ours."""
    parts = str(token or "").split(".")
    if len(parts) != 4:
        return None
    uid, email_b64, exp_s, mac = parts
    try:
        exp = int(exp_s)
        email_lower = _unb64(email_b64).decode("utf-8")
    except Exception:  # noqa: BLE001
        return None
    if exp < int(now if now is not None else time.time()):
        return None
    expected = _mac(uid, email_lower, exp)
    if not expected or not hmac.compare_digest(expected, mac):
        return None
    return uid, email_lower


# ═══════════════════════════════════════════════════════════════════════════
#  FIRESTORE
# ═══════════════════════════════════════════════════════════════════════════
def _db():
    return _get_firestore() if _get_firestore else None


def _auth_uid(body: Dict[str, Any]) -> str:
    tok = body.get("idToken") if isinstance(body.get("idToken"), str) else ""
    claims = _verify_token(tok) if (_verify_token and tok) else None
    return str(claims.get("uid") or "") if claims else ""


def _confirmed_elsewhere(db, email_lower: str, uid: str) -> bool:
    """Is this address already CONFIRMED on somebody else's account?

    Only confirmed ones block: an unconfirmed row is somebody's typo, or a
    stranger's address typed by mistake, and neither should be able to lock the
    real owner out of using their own address.
    """
    try:
        rows = (db.collection("users")
                  .where("recovery_email", "==", email_lower)
                  .limit(10).stream())
    except Exception:  # noqa: BLE001
        return False
    for row in rows:
        if row.id == uid:
            continue
        data = row.to_dict() or {}
        if data.get("recovery_email_verified"):
            return True
    return False


def _uid_by_login_email(username: str) -> str:
    """The account that this username signs in as, asked of the thing that
    actually decides: Firebase Auth.

    SIGNING IN IS CASE-INSENSITIVE. ccLoginEmail lower-cases the name before it
    becomes an address, so `Mermaid` and `mermaid` are one account. But
    `login_username` is stored on the profile exactly as it was typed, so a
    Firestore query for the typed string is case-SENSITIVE and disagrees with
    the login it is supposed to be recovering: somebody who signed up as
    `Mermaid` and typed `mermaid` here was told, truthfully as far as the query
    knew, that there was no such account. Asking Auth for the synthetic address
    is the same question the sign-in asks, so the two can no longer differ.
    """
    name = str(username or "").strip().lower()
    if not name:
        return ""
    try:
        from firebase_admin import auth as fb_auth
        user = fb_auth.get_user_by_email("%s@%s" % (name, _login_domain))
        return str(getattr(user, "uid", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def _find_by_login_username(db, username: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """The account that signs in with this username, if there is one.

    Auth first, for the reason above. The Firestore queries stay as the
    fallback for the case where Admin cannot answer (no credentials, an
    account whose Auth user is gone but whose profile is not), and they still
    try the typed form and the lower-cased one.
    """
    name = str(username or "").strip()
    if not name:
        return None
    uid = _uid_by_login_email(name)
    if uid:
        try:
            snap = db.collection("users").document(uid).get()
            if getattr(snap, "exists", False):
                return uid, (snap.to_dict() or {})
        except Exception:  # noqa: BLE001
            pass
    seen = []
    for candidate in {name, name.lower()}:
        try:
            rows = list(db.collection("users")
                          .where("login_username", "==", candidate)
                          .limit(2).stream())
        except Exception:  # noqa: BLE001
            rows = []
        seen.extend(rows)
    for row in seen:
        return row.id, (row.to_dict() or {})
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  THE MAIL
# ═══════════════════════════════════════════════════════════════════════════
def _base_url() -> str:
    return nl_email.app_base_url()


def confirm_url(token: str) -> str:
    return "%s/api/account/verify-email?t=%s" % (_base_url(), token)


def _wrap(title: str, lead: str, button_label: str, button_url: str, tail: str) -> Dict[str, str]:
    """One shape for both letters, so account mail always looks like account
    mail: no images, no tracking, one button, one sentence saying who it is
    for and what happens if it was not them."""
    html = (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'font-size:16px;line-height:1.6;color:#123a68;max-width:520px;margin:0 auto">'
        '<h1 style="font-size:21px;margin:0 0 14px;color:#0d2c52">%s</h1>'
        '<p style="margin:0 0 18px">%s</p>'
        '<p style="margin:0 0 22px">'
        '<a href="%s" style="display:inline-block;background:#e8b34a;color:#3a2a05;'
        'text-decoration:none;font-weight:700;padding:13px 22px;border-radius:10px">%s</a></p>'
        '<p style="margin:0 0 10px;font-size:13.5px;color:#4a6c8f">%s</p>'
        '<p style="margin:0;font-size:12.5px;color:#7089a3;word-break:break-all">'
        'If the button does nothing, paste this into your browser:<br>%s</p></div>'
    ) % (_html.escape(title), lead, _html.escape(button_url),
         _html.escape(button_label), tail, _html.escape(button_url))
    text = "%s\n\n%s\n\n%s\n\n%s\n" % (
        title,
        re.sub(r"<[^>]+>", "", lead),
        button_url,
        re.sub(r"<[^>]+>", "", tail),
    )
    return {"html": html, "text": text}


def build_confirmation(nickname: str, url: str) -> Dict[str, str]:
    who = _html.escape(str(nickname or "your account"))
    body = _wrap(
        "Confirm this email for Currents and Critters",
        "Somebody added this address to <strong>%s</strong>. Confirm it and it "
        "becomes the one way to reset that password if it is ever forgotten." % who,
        "Confirm this email",
        url,
        "The link is good for 48 hours. If this was not you, ignore this message: "
        "nothing is linked until the button is clicked, and no one can sign in with "
        "an address alone.",
    )
    body["subject"] = "Confirm your email for Currents and Critters"
    return body


def build_reset(nickname: str, url: str) -> Dict[str, str]:
    who = _html.escape(str(nickname or "your account"))
    body = _wrap(
        "Reset your Currents and Critters password",
        "A password reset was asked for on <strong>%s</strong>. Pick a new one here." % who,
        "Choose a new password",
        url,
        "If you did not ask for this, ignore it: your password has not changed, and "
        "this link expires on its own.",
    )
    body["subject"] = "Reset your Currents and Critters password"
    return body


def _send(to_email: str, msg: Dict[str, str]) -> bool:
    try:
        nl_email.send_email(to_email=to_email, subject=msg["subject"],
                            html_body=msg["html"], text_body=msg["text"],
                            is_bulk=False, stream="account")
        return True
    except Exception as exc:  # noqa: BLE001
        print("[account] email send failed: %s" % exc)
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  ACTIONS
# ═══════════════════════════════════════════════════════════════════════════
def link_email(uid: str, email: Any) -> Dict[str, Any]:
    """Point an account at an address and mail it a confirmation link."""
    if not valid_email(email):
        return {"ok": False, "error": "bad_email",
                "message": "That email address doesn't look right."}
    if not secret_configured():
        return {"ok": False, "error": "not_configured",
                "message": "Email linking is not switched on for this server yet."}
    if not nl_email.transport():
        return {"ok": False, "error": "no_transport",
                "message": "This server cannot send email yet, so there is nothing to confirm with."}
    db = _db()
    if db is None:
        return {"ok": False, "error": "firestore_unavailable",
                "message": "Could not reach the account store. Try again in a moment."}

    now = time.time()
    last = _last_link_at.get(uid, 0.0)
    if now - last < LINK_COOLDOWN_SEC:
        return {"ok": False, "error": "too_soon",
                "message": "A confirmation was just sent. Check your inbox, then try again in a minute."}

    email_lower = normalize(email)
    if _confirmed_elsewhere(db, email_lower, uid):
        return {"ok": False, "error": "taken",
                "message": "That email is already confirmed on another account."}

    ref = db.collection("users").document(uid)
    try:
        snap = ref.get()
        profile = (snap.to_dict() or {}) if getattr(snap, "exists", False) else {}
    except Exception:  # noqa: BLE001
        profile = {}
    already = normalize(profile.get("recovery_email"))
    verified = bool(profile.get("recovery_email_verified"))
    if already == email_lower and verified:
        return {"ok": True, "verified": True, "email": email_lower,
                "message": "That email is already confirmed on this account."}

    try:
        ref.set({
            "recovery_email": email_lower,
            "recovery_email_verified": False,
            "recovery_email_at": int(now),
        }, merge=True)
    except Exception as exc:  # noqa: BLE001
        print("[account] link write failed: %s" % exc)
        return {"ok": False, "error": "write_failed",
                "message": "Could not save that email. Try again in a moment."}

    token = make_token(uid, email_lower, now)
    msg = build_confirmation(str(profile.get("nickname") or "your account"), confirm_url(token))
    if not _send(email_lower, msg):
        return {"ok": False, "error": "send_failed",
                "message": "Could not send the confirmation email. Check the address and try again."}
    _last_link_at[uid] = now
    return {"ok": True, "verified": False, "email": email_lower,
            "message": "Check %s for the confirmation link." % email_lower}


def verify_email(token: str) -> Dict[str, Any]:
    """Click the link: the address becomes real on that account, and on no other."""
    read = read_token(token)
    if not read:
        return {"ok": False, "error": "bad_token",
                "message": "That confirmation link is not valid any more. Send yourself a new one from Settings."}
    uid, email_lower = read
    db = _db()
    if db is None:
        return {"ok": False, "error": "firestore_unavailable",
                "message": "Could not reach the account store. Try the link again in a moment."}
    ref = db.collection("users").document(uid)
    try:
        snap = ref.get()
        profile = (snap.to_dict() or {}) if getattr(snap, "exists", False) else {}
    except Exception:  # noqa: BLE001
        profile = {}
    if not profile:
        return {"ok": False, "error": "no_account",
                "message": "That account no longer exists."}
    # The address on the account has to still BE this one: a link from an
    # older attempt must not resurrect an address the player has replaced.
    if normalize(profile.get("recovery_email")) != email_lower:
        return {"ok": False, "error": "stale",
                "message": "That link was for a different address. Send yourself a new one from Settings."}
    if profile.get("recovery_email_verified"):
        return {"ok": True, "already": True, "email": email_lower,
                "message": "This email was already confirmed. Nothing else to do."}
    try:
        ref.set({"recovery_email_verified": True,
                 "recovery_email_verified_at": int(time.time())}, merge=True)
    except Exception as exc:  # noqa: BLE001
        print("[account] verify write failed: %s" % exc)
        return {"ok": False, "error": "write_failed",
                "message": "Could not confirm it just now. Try the link again in a moment."}
    return {"ok": True, "email": email_lower,
            "message": "Confirmed. This address can now reset that password."}


def forgot_password(username: Any) -> Dict[str, Any]:
    """Mail Firebase's own reset link for the synthetic address to the real one.

    The reply is FORGOT_ANSWER whatever happens, including when nothing at all
    was found: see rule 3.
    """
    answer = {"ok": True, "message": FORGOT_ANSWER}
    name = str(username or "").strip()
    if not name or len(name) > 64:
        return answer
    db = _db()
    if db is None:
        return answer
    found = _find_by_login_username(db, name)
    if not found:
        return answer
    uid, profile = found
    email_lower = normalize(profile.get("recovery_email"))
    if not email_lower or not profile.get("recovery_email_verified"):
        return answer
    login_name = str(profile.get("login_username") or name).strip().lower()
    synthetic = "%s@%s" % (login_name, _login_domain)
    try:
        from firebase_admin import auth as fb_auth
        link = fb_auth.generate_password_reset_link(synthetic)
    except Exception as exc:  # noqa: BLE001
        print("[account] reset link failed: %s" % exc)
        return answer
    _send(email_lower, build_reset(str(profile.get("nickname") or name), link))
    return answer


# ═══════════════════════════════════════════════════════════════════════════
#  THE RECOVERY CODE
#  ---------------------------------------------------------------------------
#  The email above is optional, and most players will not link one. Without it
#  a forgotten password was the end of the account: the address Firebase holds
#  is synthetic, so there is nobody to mail. This is the way back in that needs
#  nothing from the player except that they kept a piece of paper.
#
#  It is a SECOND CREDENTIAL, not a hint and not a question: username + code
#  gets you in, exactly like username + password. So it is treated like a
#  password everywhere it is handled.
#
#    users/{uid}.recovery_code_hash      HMAC(secret, "recovery-code:uid:CODE")
#    users/{uid}.recovery_code_at        when it was issued
#    users/{uid}.recovery_code_used_at   when it was last spent
#
#  THE HASH IS THE POINT. The plaintext code exists in exactly two places: on
#  the player's paper, and in the one HTTP response that issued it. It is never
#  stored, never logged, and never readable back out of Firestore, so a leaked
#  database dump is not a pile of working credentials. The uid is inside the
#  MAC, so a hash lifted from one account cannot be pasted onto another.
#
#  SPENDING ONE IS SINGLE-USE, and it does not "log you in" and leave you there
#  with nothing: redeeming sets a NEW PASSWORD in the same call and issues a
#  FRESH CODE. A player who used their only way back in must never walk away
#  from that screen still holding nothing, or the next forgotten password is
#  the end of the account after all.
#
#  ENUMERATION. redeem answers a wrong username and a wrong code with the SAME
#  sentence, for the same reason forgot-password answers everybody identically:
#  a sign-in screen must not become a way to ask which usernames are real.
#
#  THE SECRET is the one the tokens above use. Rotating it invalidates every
#  outstanding code, which is why it falls back the way it does rather than
#  being generated per boot.
# ═══════════════════════════════════════════════════════════════════════════

# No 0/O/1/I/L: this is read off paper, often by a child, and those five are
# where a transcription goes wrong.
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_GROUPS = 4
CODE_GROUP_LEN = 4
CODE_LEN = CODE_GROUPS * CODE_GROUP_LEN        # 16 chars, ~79 bits

ISSUE_COOLDOWN_SEC = 30           # per account: rotating is cheap, not free
REDEEM_MAX_FAILS = 5              # per username, per window
REDEEM_FAIL_WINDOW_SEC = 15 * 60

# Said the same way for a username that does not exist and a code that is
# wrong, so neither answer is a question about the other.
REDEEM_ANSWER_BAD = (
    "That username and recovery code don't match. Check both and try again."
)

_last_issue_at: Dict[str, float] = {}
_redeem_fails: Dict[str, Tuple[int, float]] = {}


def format_code(raw: str) -> str:
    """The shape a player sees and writes down: ABCD-EFGH-JKMN-PQRS."""
    s = str(raw or "")
    return "-".join(s[i:i + CODE_GROUP_LEN]
                    for i in range(0, len(s), CODE_GROUP_LEN))


def normalize_code(value: Any) -> str:
    """What the player typed, as the code they were given.

    Dashes, spaces and case are decoration: people write codes down with
    whatever separators they like and type them back in lower case. Anything
    left that is not in the alphabet means it is not one of our codes.
    """
    s = str(value or "").upper()
    s = re.sub(r"[\s\-_.]+", "", s)
    if len(s) != CODE_LEN:
        return ""
    if any(ch not in CODE_ALPHABET for ch in s):
        return ""
    return s


def make_code() -> str:
    """A fresh code from the system CSPRNG. secrets.choice, never random."""
    import secrets
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def code_hash(uid: str, code: str) -> str:
    """What actually gets stored. Empty if this server has no secret, which is
    checked by every caller: a code hashed with nothing is not a credential."""
    key = _secret()
    if not key or not uid or not code:
        return ""
    msg = "recovery-code:%s:%s" % (uid, code)
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).hexdigest()


def code_matches(uid: str, code: str, stored: Any) -> bool:
    """Constant-time, because this is a password comparison."""
    want = code_hash(uid, code)
    have = str(stored or "")
    if not want or not have or len(want) != len(have):
        return False
    return hmac.compare_digest(want, have)


def password_ok(pw: Any) -> bool:
    """The floor the browser's green bar sits on, checked again where it counts.

    Deliberately looser than the meter: the meter is advice about a password
    somebody is inventing, this is a refusal. It only has to stop the ones that
    are not passwords at all.
    """
    s = str(pw or "")
    if not (8 <= len(s) <= 200):
        return False
    kinds = sum(bool(re.search(p, s)) for p in
                (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]"))
    if kinds < 2:
        return False
    if re.match(r"^(?:p+a+s+s+w+o+r+d+|letmein|welcome|qwerty|abc123|iloveyou|"
                r"admin|dragon|monkey|football|baseball|sunshine|princess|"
                r"currents?(?:and)?critters?|critters?|fish|ocean)\d{0,4}!?$",
                s, re.I):
        return False
    return True


def _is_password_account(profile: Dict[str, Any]) -> bool:
    """A recovery code is a way back into a PASSWORD. A Google account signs in
    with Google and has no password here to be locked out of, so it is not
    offered one and cannot redeem one."""
    if str(profile.get("auth_provider") or "").strip().lower() == "google":
        return False
    return bool(str(profile.get("login_username") or "").strip())


def _write_new_code(db, uid: str, now: float) -> Dict[str, Any]:
    """Mint, store the hash, hand back the plaintext. The ONLY place plaintext
    ever leaves this module, and it leaves as a return value, never a log."""
    code = make_code()
    digest = code_hash(uid, code)
    if not digest:
        return {"ok": False, "error": "not_configured",
                "message": "Recovery codes are not switched on for this server yet."}
    try:
        db.collection("users").document(uid).set({
            "recovery_code_hash": digest,
            "recovery_code_at": int(now),
        }, merge=True)
    except Exception as exc:  # noqa: BLE001
        print("[account] recovery code write failed: %s" % exc)
        return {"ok": False, "error": "write_failed",
                "message": "Could not save a recovery code. Try again in a moment."}
    _last_issue_at[uid] = now
    return {"ok": True, "code": format_code(code), "issued_at": int(now)}


def issue_recovery_code(uid: str) -> Dict[str, Any]:
    """Give this account a code, replacing whatever it had.

    Rotating is destructive on purpose: an account has exactly one code, so
    "I lost it, give me another" cannot leave the lost one working.
    """
    if not secret_configured():
        return {"ok": False, "error": "not_configured",
                "message": "Recovery codes are not switched on for this server yet."}
    db = _db()
    if db is None:
        return {"ok": False, "error": "firestore_unavailable",
                "message": "Could not reach the account store. Try again in a moment."}
    try:
        snap = db.collection("users").document(uid).get()
        profile = (snap.to_dict() or {}) if getattr(snap, "exists", False) else {}
    except Exception:  # noqa: BLE001
        profile = {}
    if not profile:
        return {"ok": False, "error": "no_account",
                "message": "That account does not exist yet."}
    if not _is_password_account(profile):
        return {"ok": False, "error": "not_password_account",
                "message": "This account signs in with Google, so it has no password to recover."}
    now = time.time()
    if now - _last_issue_at.get(uid, 0.0) < ISSUE_COOLDOWN_SEC:
        return {"ok": False, "error": "too_soon",
                "message": "A code was just issued. Write that one down, then try again in a moment."}
    return _write_new_code(db, uid, now)


def recovery_code_state(uid: str) -> Dict[str, Any]:
    """Whether there IS one, and when. Never the code: it is not readable."""
    db = _db()
    if db is None:
        return {"ok": False, "error": "firestore_unavailable"}
    try:
        snap = db.collection("users").document(uid).get()
        profile = (snap.to_dict() or {}) if getattr(snap, "exists", False) else {}
    except Exception:  # noqa: BLE001
        profile = {}
    return {
        "ok": True,
        "eligible": _is_password_account(profile),
        "has_code": bool(profile.get("recovery_code_hash")),
        "issued_at": int(profile.get("recovery_code_at") or 0),
        "used_at": int(profile.get("recovery_code_used_at") or 0),
    }


def _redeem_locked(key: str, now: float) -> bool:
    fails, first = _redeem_fails.get(key, (0, 0.0))
    if not fails:
        return False
    if now - first > REDEEM_FAIL_WINDOW_SEC:
        _redeem_fails.pop(key, None)
        return False
    return fails >= REDEEM_MAX_FAILS


def _redeem_failed(key: str, now: float) -> None:
    fails, first = _redeem_fails.get(key, (0, 0.0))
    if not fails or now - first > REDEEM_FAIL_WINDOW_SEC:
        _redeem_fails[key] = (1, now)
    else:
        _redeem_fails[key] = (fails + 1, first)


def redeem_recovery_code(username: Any, code: Any, new_password: Any) -> Dict[str, Any]:
    """Username + code + a new password, and they are back in.

    The three things that happen here happen in this order for a reason:
    the password is changed FIRST, because that is the thing the player came
    for; only then is the spent code replaced, so a failure to mint the next
    one leaves them locked IN rather than out.
    """
    if not secret_configured():
        return {"ok": False, "error": "not_configured",
                "message": "Recovery codes are not switched on for this server yet."}

    name = str(username or "").strip()
    typed = normalize_code(code)
    now = time.time()
    key = name.lower()[:64]

    # Rate limited on the TYPED username, whether or not it names an account:
    # keying it on real accounts only would make the lockout itself an answer.
    if _redeem_locked(key, now):
        return {"ok": False, "error": "too_many",
                "message": "Too many tries. Wait a quarter of an hour, then try again."}

    # A weak new password is the player's own typing and safe to name exactly.
    # It is checked before anything is looked up so that a good code is never
    # spent on an attempt that was going to be refused anyway.
    if not password_ok(new_password):
        return {"ok": False, "error": "weak_password",
                "message": "Pick a longer password: at least 8 characters, "
                           "with more than one kind of character in it."}

    db = _db()
    if db is None:
        return {"ok": False, "error": "firestore_unavailable",
                "message": "Could not reach the account store. Try again in a moment."}

    found = _find_by_login_username(db, name) if (name and typed) else None
    if not found:
        _redeem_failed(key, now)
        return {"ok": False, "error": "no_match", "message": REDEEM_ANSWER_BAD}
    uid, profile = found
    if not _is_password_account(profile) or not code_matches(uid, typed, profile.get("recovery_code_hash")):
        _redeem_failed(key, now)
        return {"ok": False, "error": "no_match", "message": REDEEM_ANSWER_BAD}

    login_name = str(profile.get("login_username") or name).strip().lower()
    synthetic = "%s@%s" % (login_name, _login_domain)
    try:
        from firebase_admin import auth as fb_auth
        fb_auth.update_user(uid, password=str(new_password))
    except Exception as exc:  # noqa: BLE001
        print("[account] recovery password set failed: %s" % exc)
        return {"ok": False, "error": "set_failed",
                "message": "Could not set that password just now. Try again in a moment."}

    _redeem_fails.pop(key, None)
    try:
        db.collection("users").document(uid).set(
            {"recovery_code_used_at": int(now)}, merge=True)
    except Exception:  # noqa: BLE001
        pass

    # The spent code is replaced in the same breath. If minting the next one
    # fails, they are still in: they signed in with the password they just set.
    _last_issue_at.pop(uid, None)
    fresh = _write_new_code(db, uid, now)
    return {
        "ok": True,
        "username": login_name,
        "email": synthetic,
        "code": fresh.get("code") or "",
        "message": ("Your password is set. Signing you in…" if fresh.get("code")
                    else "Your password is set. Make a new recovery code in Settings."),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════════════════════
def _page(ok: bool, message: str) -> bytes:
    """The landing page for a clicked confirmation link. Deliberately plain and
    self-contained: it is opened in whatever browser the mail app hands it to,
    signed out, and its only job is to say what happened and point back."""
    tint = "#1e9b62" if ok else "#b3452f"
    head = "Email confirmed" if ok else "That link did not work"
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>%s | Currents and Critters</title></head>"
        "<body style=\"margin:0;background:#0a1d3a;color:#eaf4ff;"
        "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif\">"
        "<div style=\"max-width:520px;margin:14vh auto 0;padding:28px 24px;"
        "background:rgba(255,255,255,.05);border-radius:16px;text-align:center\">"
        "<div style=\"font-size:34px\">%s</div>"
        "<h1 style=\"font-size:22px;margin:12px 0 10px;color:%s\">%s</h1>"
        "<p style=\"margin:0 0 22px;line-height:1.6;color:#b9d1e6\">%s</p>"
        "<a href=\"%s\" style=\"display:inline-block;background:#e8b34a;color:#3a2a05;"
        "text-decoration:none;font-weight:700;padding:12px 20px;border-radius:10px\">"
        "Back to the game</a></div></body></html>"
        % (_html.escape(head), "\U0001FAB8" if ok else "\U0001F41A", tint,
           _html.escape(head), _html.escape(message), _html.escape(_base_url()))
    ).encode("utf-8")


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    """POST /api/account/link-email            { idToken, email }
       POST /api/account/forgot-password       { username }   (public, by design)
       POST /api/account/recovery-code/issue   { idToken }
       POST /api/account/recovery-code/state   { idToken }
       POST /api/account/recovery-code/redeem  { username, code, new_password }
                                                              (public, by design)
    """
    path = parsed.path
    if not path.startswith("/api/account/"):
        return False
    action = path[len("/api/account/"):]

    if action == "link-email":
        uid = _auth_uid(body)
        if not uid:
            handler._send_json({"ok": False, "error": "unauthorized",
                                "message": "You are not signed in."}, status=401)
            return True
        handler._send_json(link_email(uid, body.get("email")))
        return True

    # Public: somebody who has forgotten their password cannot prove anything.
    # It is safe because the reply says the same thing every time and the link
    # only ever goes to an address already confirmed on that account.
    if action == "forgot-password":
        handler._send_json(forgot_password(body.get("username")))
        return True

    # ── The recovery code ──────────────────────────────────────────────
    # Issuing and asking about one are things only the account holder may do,
    # so both take a token and read the uid off it. Redeeming cannot: the whole
    # situation is that the player has nothing left to prove ownership with.
    # What guards it instead is the code itself, plus a rate limit and one
    # answer for every kind of miss.
    if action == "recovery-code/issue":
        uid = _auth_uid(body)
        if not uid:
            handler._send_json({"ok": False, "error": "unauthorized",
                                "message": "You are not signed in."}, status=401)
            return True
        handler._send_json(issue_recovery_code(uid))
        return True

    if action == "recovery-code/state":
        uid = _auth_uid(body)
        if not uid:
            handler._send_json({"ok": False, "error": "unauthorized",
                                "message": "You are not signed in."}, status=401)
            return True
        handler._send_json(recovery_code_state(uid))
        return True

    if action == "recovery-code/redeem":
        handler._send_json(redeem_recovery_code(
            body.get("username"), body.get("code"), body.get("new_password")))
        return True

    return False


def handle_get(handler, parsed) -> bool:
    """GET /api/account/verify-email?t=…  the link in the confirmation mail."""
    if parsed.path != "/api/account/verify-email":
        return False
    token = ""
    for part in (parsed.query or "").split("&"):
        if part.startswith("t="):
            from urllib.parse import unquote
            token = unquote(part[2:])
            break
    res = verify_email(token)
    handler._emit_html(_page(bool(res.get("ok")), str(res.get("message") or "")))
    return True
