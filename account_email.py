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


def _find_by_login_username(db, username: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """The account that signs in with this username, if there is one.

    login_username is stored as the player typed it, so the lookup tries the
    typed form and the lower-cased one. The synthetic login address is always
    lower-cased, which is why a reset can be generated from either.
    """
    name = str(username or "").strip()
    if not name:
        return None
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
    """POST /api/account/link-email       { idToken, email }
       POST /api/account/forgot-password  { username }   (public, by design)
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
