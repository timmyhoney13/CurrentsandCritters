"""Currents and Critters — newsletter subscribers, campaigns and admin API.

Wired additively into multiplayer_server, exactly like clan_server /
prestige_server / analytics_server:

    import newsletter_server
    newsletter_server.init(...)                                  # in main()
    if newsletter_server.handle_get(self, parsed): return        # in do_GET
    if newsletter_server.handle_post(self, parsed, body): return # in do_POST
    newsletter_server.handle_stripe_session(event, session)      # in the webhook

Nothing here changes an existing route, an existing collection, or the payment
path. If this whole module were deleted the game, the store and the webhook
would behave exactly as they did before — the Stripe hook is a try/except'd
side call that can only ever log.

────────────────────────────────────────────────────────────────────────────
CONSENT IS THE WHOLE DESIGN
A subscriber is created in exactly two ways:
  1. someone typed an address into the OPTIONAL newsletter field on a Stripe
     checkout that Stripe then confirmed as PAID, or
  2. Tim added them by hand and ticked the box saying they gave permission.
Paying is not consent. Leaving the field blank is not consent. Being emailed a
receipt is not consent. There is no other code path that can produce an active
subscriber, which is why `_subscribe` is the only writer of status="active".

────────────────────────────────────────────────────────────────────────────
IDEMPOTENCY, IN THREE LAYERS
Stripe retries webhooks. Browsers double-click. Render restarts mid-campaign.
Every one of those is handled by a DATABASE-level uniqueness key, not by a
best-effort flag:
  • newsletterWebhookEvents/{stripe_event_id}     — one Stripe event, once.
  • newsletterSubscribers/{sha256(email)}         — one address, one record.
  • …/campaigns/{cid}/recipients/{subscriber_id}  — one campaign, one delivery
                                                    per subscriber.
Each is a Firestore DOCUMENT ID, so the uniqueness is enforced by the store
itself inside a transaction, not by a read-then-write race in Python.

────────────────────────────────────────────────────────────────────────────
UNSUBSCRIBE TOKENS
The link carries "<unsubId>.<mac>":
  unsubId — 16 random urlsafe bytes stored on the subscriber. Opaque; it is
            NOT derived from the email or a row number, so a link cannot be
            guessed from an address and possessing one tells you nothing
            about any other subscriber.
  mac     — HMAC-SHA256(NEWSLETTER_UNSUBSCRIBE_SECRET, unsubId:tokenVersion),
            truncated and base64url'd, compared in constant time.
The MAC is never stored, so a database leak yields no working links, and
bumping tokenVersion (which reactivation does) invalidates every old link at
once. This is why the token is not a stored random string: a stored string
cannot be regenerated for each of ten thousand sends without keeping the
plaintext, and keeping the plaintext is the thing to avoid.

⚠️ Changing NEWSLETTER_UNSUBSCRIBE_SECRET invalidates every unsubscribe link
already sitting in somebody's inbox. Set it once and leave it alone.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import queue
import re
import secrets
import threading
import time
import traceback
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import newsletter_email as nl_email

# ── Injected by init() (no circular import with multiplayer_server) ─────────
_get_firestore: Optional[Callable[[], Any]] = None
_verify_token: Optional[Callable[[str], Optional[dict]]] = None
_app_version: str = ""

# ── Collections ─────────────────────────────────────────────────────────────
C_SUBS = "newsletterSubscribers"
C_EVENTS = "newsletterWebhookEvents"
C_CAMPAIGNS = "newsletterCampaigns"
C_RECIPIENTS = "recipients"          # subcollection of a campaign
C_AUDIT = "newsletterAudit"
C_META = "newsletterMeta"

STATUS_ACTIVE = "active"
STATUS_UNSUB = "unsubscribed"
# A public web form proves nothing about who owns the address, so a signup
# from one lands here and NOTHING can mail it except the confirmation itself.
# Only the person holding the inbox can turn it into an active subscriber.
STATUS_PENDING = "pending"

# Welcome-email states. WELCOME_AWAITING is the one that matters: an
# unconfirmed signup has NO welcome to send yet, and parking it here is what
# keeps it out of the welcome queue until the person clicks the link.
WELCOME_PENDING = "pending"
WELCOME_AWAITING = "awaiting_confirmation"
WELCOME_SKIPPED = "skipped"

SOURCE_STRIPE = "Stripe Checkout"
SOURCE_MANUAL = "Manual Admin Addition"
SOURCE_WEBSITE = "Website Signup"

# Campaign / recipient states.
CAMP_DRAFT, CAMP_SENDING, CAMP_SENT, CAMP_FAILED = "draft", "sending", "sent", "failed"
R_PENDING, R_SENDING, R_SENT = "pending", "sending", "sent"
R_FAILED, R_SKIPPED, R_INTERRUPTED = "failed", "skipped_unsubscribed", "interrupted"

# How many messages one worker pass sends before it re-checks campaign state.
SEND_BATCH = 25
# A recipient claimed for sending but never resolved is presumed interrupted
# after this long (a process died mid-send).
LEASE_SEC = 180
# A retryable failure (network blip, 429) is retried at most this many times
# before the recipient is left as failed for a human to look at.
MAX_ATTEMPTS = 3

MAX_SUBJECT = 200
MAX_PREVIEW = 200
MAX_CONTENT = 400_000
MAX_SUBSCRIBER_SCAN = 20_000


# ═══════════════════════════════════════════════════════════════════════════
#  THE STRIPE NEWSLETTER FIELD — A LABEL IS A BEHAVIOUR KEY
# ═══════════════════════════════════════════════════════════════════════════
# Stripe echoes a Payment Link's custom questions back on the session as
# custom_fields[].label.custom, verbatim. We find the newsletter answer by
# matching that label — so the strings below are not display text, they are
# lookup keys, and if they stop matching what the Payment Link actually asks,
# the lookup silently returns "" and NOBODY EVER GETS SUBSCRIBED with no error
# anywhere. (multiplayer_server learned this the hard way with the username
# field; see CF_USERNAME_LABELS there.)
#
# Three defences against that silent failure:
#   1. several accepted spellings, not one;
#   2. NEWSLETTER_FIELD_LABEL env var to add another without a code deploy;
#   3. every label seen on a checkout that produced no newsletter match is
#      recorded (label text only, never an answer) and shown in the admin
#      Settings tab, so "why is nobody subscribing" is answerable in ten
#      seconds instead of being invisible.
#
# NOTE: Stripe caps a custom-field label at 50 characters, which is why the
# long sentence quoted in the Privacy Policy cannot be the live label.
NEWSLETTER_FIELD_LABELS: Tuple[str, ...] = (
    "Enter your email to get updates",
    "Enter your email to get updates:",
    "Enter your email for updates",
    "Enter your email to join the newsletter",
    "Newsletter email",
    "Email for updates",
)


def _extra_labels() -> List[str]:
    raw = os.environ.get("NEWSLETTER_FIELD_LABEL", "")
    return [p.strip() for p in raw.split("|") if p.strip()]


def accepted_labels() -> List[str]:
    return list(NEWSLETTER_FIELD_LABELS) + _extra_labels()


def _label_is_newsletter_field(label: str) -> bool:
    """Does this custom-field label ask for an email for updates?

    Exact (case/space-insensitive) match against the accepted list first. The
    heuristic fallback then accepts a label that asks for an EMAIL *and*
    mentions updates/newsletter/mailing — e.g. a future reword like "Your email
    for game updates". It deliberately cannot match a plain "Email" or
    "Billing email": consent has to be legible in the question itself, so a
    label that does not say what the address is for never counts as opting in.
    """
    lab = re.sub(r"\s+", " ", str(label or "")).strip().lower().rstrip(":?.")
    if not lab:
        return False
    for acc in accepted_labels():
        if lab == re.sub(r"\s+", " ", acc).strip().lower().rstrip(":?.") :
            return True
    if "email" not in lab:
        return False
    return any(w in lab for w in ("update", "newsletter", "mailing list", "email list"))


def extract_newsletter_email(custom_fields: Any) -> Tuple[str, List[str]]:
    """(normalised email or "", every label seen on the session).

    The labels come back so the caller can record what Stripe actually asked
    when no field matched — the diagnostic that makes a label drift visible.
    """
    seen: List[str] = []
    found = ""
    if not isinstance(custom_fields, list):
        return "", seen
    for field in custom_fields:
        if not isinstance(field, dict):
            continue
        lab = field.get("label")
        name = str(lab.get("custom") or "").strip() if isinstance(lab, dict) else ""
        if name:
            seen.append(name[:120])
        if found or not _label_is_newsletter_field(name):
            continue
        ftype = str(field.get("type") or "").strip()
        value = None
        sub = field.get(ftype) if ftype else None
        if isinstance(sub, dict):
            value = sub.get("value")
        if value is None:
            for key in ("text", "dropdown", "numeric"):
                sub = field.get(key)
                if isinstance(sub, dict) and sub.get("value") is not None:
                    value = sub.get("value")
                    break
        # A blank answer is the customer declining. Not an error, not a signup.
        found = nl_email.normalize_email(value) if value is not None else ""
    return found, seen


# ═══════════════════════════════════════════════════════════════════════════
#  SMALL HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def _now() -> int:
    return int(time.time())


def _iso(unix: Any) -> str:
    try:
        n = int(unix or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(n))


def _int(v, default: int = 0) -> int:
    try:
        if isinstance(v, bool):
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _db():
    """The Firestore handle, or None.

    Defensive because the injected accessor initialises firebase_admin on first
    call and can throw (missing/!invalid service account, a cold-start race). A
    raise here would surface as a 500 on whatever route happened to ask first;
    None makes every caller take its existing "unavailable" path instead.
    """
    if _get_firestore is None:
        return None
    try:
        return _get_firestore()
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] Firestore unavailable: %s" % exc)
        return None


def _subscriber_id(email_lower: str) -> str:
    """Document id = SHA-256 of the normalised address.

    This IS the "unique constraint on normalized subscriber email" — Firestore
    has no UNIQUE index, but a document id is unique by definition, so deriving
    it from the address makes a duplicate structurally impossible rather than
    merely unlikely. Hashing (instead of using the raw address as the id)
    avoids every doc-id character restriction and means the subscriber list
    cannot be enumerated by guessing addresses.
    """
    return hashlib.sha256(email_lower.encode("utf-8")).hexdigest()[:40]


def _unsub_secret() -> bytes:
    s = os.environ.get("NEWSLETTER_UNSUBSCRIBE_SECRET", "").strip()
    if not s:
        # Fall back to the session secret so links still work on a server that
        # has not set the dedicated one; the admin Settings panel flags it.
        s = os.environ.get("SESSION_SECRET", "").strip()
    return s.encode("utf-8") if s else b""


def unsub_secret_configured() -> bool:
    return bool(_unsub_secret())


def _mac(unsub_id: str, version: int, purpose: str = "") -> str:
    """Keyed signature over (purpose, id, version).

    `purpose` is what stops a confirmation link doubling as an unsubscribe link
    and vice versa — two tokens over the same id would otherwise be identical
    strings, and a subscriber could be unsubscribed by the link that was meant
    to sign them up. The unsubscribe purpose is deliberately the EMPTY string
    so tokens already sitting in inboxes keep verifying.
    """
    key = _unsub_secret()
    if not key:
        return ""
    msg = ("%s:%s:%d" % (purpose, unsub_id, int(version))) if purpose else \
          ("%s:%d" % (unsub_id, int(version)))
    raw = hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")[:32]


def make_unsub_token(sub: Dict[str, Any]) -> str:
    uid = str(sub.get("unsubId") or "")
    if not uid:
        return ""
    m = _mac(uid, _int(sub.get("tokenVersion"), 1))
    return ("%s.%s" % (uid, m)) if m else ""


def make_confirm_token(sub: Dict[str, Any]) -> str:
    uid = str(sub.get("unsubId") or "")
    if not uid:
        return ""
    m = _mac(uid, _int(sub.get("tokenVersion"), 1), purpose="confirm")
    return ("%s.%s" % (uid, m)) if m else ""


def confirm_url(sub: Dict[str, Any]) -> str:
    tok = make_confirm_token(sub)
    if not tok:
        return ""
    return "%s/newsletter/confirm/%s" % (nl_email.app_base_url(), tok)


def unsubscribe_url(sub: Dict[str, Any]) -> str:
    tok = make_unsub_token(sub)
    if not tok:
        return ""
    return "%s/newsletter/unsubscribe/%s" % (nl_email.app_base_url(), tok)


def one_click_url(sub: Dict[str, Any]) -> str:
    """The RFC 8058 POST target Gmail/Outlook hit when the reader uses the mail
    client's own Unsubscribe button. Same token, same route — the route accepts
    POST as well as GET."""
    return unsubscribe_url(sub)


def _new_unsub_id() -> str:
    return secrets.token_urlsafe(16)


def _parse_token(token: str) -> Tuple[str, str]:
    t = str(token or "").strip()
    if not t or len(t) > 200 or "." not in t:
        return "", ""
    uid, _, mac = t.partition(".")
    if not re.match(r"^[A-Za-z0-9_\-]{8,64}$", uid) or not re.match(r"^[A-Za-z0-9_\-]{8,64}$", mac):
        return "", ""
    return uid, mac


# ═══════════════════════════════════════════════════════════════════════════
#  AUDIT LOG
# ═══════════════════════════════════════════════════════════════════════════
# What must NEVER land here: newsletter bodies, OAuth tokens, Stripe secrets,
# unsubscribe tokens. `summary` is a short human sentence and every writer
# passes a literal, never a user-controlled blob.
AUDIT_ACTIONS = (
    "subscriber_added_stripe", "subscriber_added_manual", "subscriber_unsubscribed",
    "subscriber_reactivated", "welcome_email_sent", "welcome_email_failed",
    "test_email_sent", "draft_created", "draft_updated", "campaign_approved",
    "campaign_started", "campaign_completed", "campaign_failed", "csv_exported",
    "subscriber_added_website", "confirmation_sent", "confirmation_resent",
    "subscriber_confirmed_by_admin",
    "unauthorized_admin_access", "connection_check",
)


def audit(action: str, *, admin: str = "", subscriber_id: str = "", campaign_id: str = "",
          summary: str = "", correlation_id: str = "") -> None:
    db = _db()
    if db is None:
        return
    try:
        db.collection(C_AUDIT).document().set({
            "action": str(action)[:60],
            "at": _now(),
            "atIso": _iso(_now()),
            "admin": str(admin or "")[:200],
            "subscriberId": str(subscriber_id or "")[:64],
            "campaignId": str(campaign_id or "")[:64],
            "summary": str(summary or "")[:400],
            "correlationId": str(correlation_id or "")[:32],
        })
    except Exception as exc:  # noqa: BLE001 — an audit write must never break the action
        print("[newsletter] audit write failed (%s): %s" % (action, exc))


# ═══════════════════════════════════════════════════════════════════════════
#  RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════════
# In-process fixed-window counters keyed by (bucket, client). Enough to stop
# credential stuffing on the admin routes and token-guessing on unsubscribe;
# it is not a distributed limiter and does not pretend to be (this service
# runs as a single instance — it has a mounted disk).
_RL_LOCK = threading.Lock()
_RL: Dict[str, Tuple[int, int]] = {}
_RL_RULES: Dict[str, Tuple[int, int]] = {
    "admin_auth":  (30, 300),    # 30 failed admin auths / 5 min / IP
    "admin_api":   (600, 300),   # ordinary admin browsing
    "unsubscribe": (60, 300),    # token guessing
    "test_send":   (10, 300),    # accidental / repeated test sends
    "export":      (10, 600),
    "campaign":    (12, 600),    # campaign start / approve
    "signup":      (120, 300),   # the Stripe hook path
    "public_signup": (8, 600),   # the website form — 8 per IP per 10 min
}


def _rate_ok(bucket: str, client: str) -> bool:
    limit, window = _RL_RULES.get(bucket, (300, 300))
    now = _now()
    slot = now // window
    key = "%s|%s|%d" % (bucket, client, slot)
    with _RL_LOCK:
        if len(_RL) > 8000:                      # cheap unbounded-growth guard
            for k in [k for k in _RL if _RL[k][1] < now - 2 * window]:
                _RL.pop(k, None)
        count, _ = _RL.get(key, (0, now))
        count += 1
        _RL[key] = (count, now)
        return count <= limit


def _client_key(handler) -> str:
    """Best-effort client identity for rate limiting. Render sits behind a
    proxy, so the left-most X-Forwarded-For entry is the real client; it is
    spoofable, which is why it is used ONLY for rate limiting and never for
    authorisation."""
    try:
        xff = handler.headers.get("X-Forwarded-For", "") if handler.headers else ""
        if xff:
            return xff.split(",")[0].strip()[:64]
        return str(handler.client_address[0])[:64]
    except Exception:  # noqa: BLE001
        return "unknown"


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN AUTHORISATION
# ═══════════════════════════════════════════════════════════════════════════
def admin_email() -> str:
    return nl_email.admin_email()


def _admin_claims(body: Dict[str, Any], handler=None) -> Optional[dict]:
    """Verified claims for THE newsletter admin, or None.

    Three gates, all required:
      1. the Firebase ID token verifies server-side (proves the caller holds a
         live Google session for that account — a uid or email in the body is
         never looked at),
      2. the token's email is VERIFIED by the identity provider,
      3. that email equals ADMIN_EMAIL exactly.

    Deliberately narrower than analytics_server's check: that one also honours
    an `is_admin` flag on the Firestore user document, which would mean a
    second account could reach the subscriber list. Here the allowlist is one
    address and there is no other way in.
    """
    if _verify_token is None:
        return None
    tok = body.get("idToken") if isinstance(body.get("idToken"), str) else ""
    if not tok:
        return None
    claims = _verify_token(tok)
    if not claims or not claims.get("uid"):
        return None
    if claims.get("email_verified") is not True:
        return None
    email = str(claims.get("email") or "").strip().lower()
    if not nl_email.is_admin_email(email):
        return None
    return claims


# ═══════════════════════════════════════════════════════════════════════════
#  SUBSCRIBERS
# ═══════════════════════════════════════════════════════════════════════════
def _blank_subscriber(email_lower: str, source: str,
                      status: str = STATUS_ACTIVE) -> Dict[str, Any]:
    now = _now()
    return {
        "id": _subscriber_id(email_lower),
        "email": email_lower,
        "emailLower": email_lower,
        "status": status,
        "source": source,
        "subscribedAt": now,
        "resubscribedAt": now,
        "unsubscribedAt": 0,
        "unsubId": _new_unsub_id(),
        "tokenVersion": 1,
        # A record still awaiting an email confirmation has nothing to send
        # yet and must not sit in the welcome queue pretending otherwise. Left
        # as "pending" it would be re-read by the worker every 20 seconds
        # forever, the dashboard would report a welcome email that is pending
        # and never sends, and — because the queue is read a page at a time —
        # a pile of unconfirmed signups would starve the welcomes that ARE
        # due. Confirming flips this to WELCOME_PENDING.
        "welcomeEmailStatus": (WELCOME_PENDING if status == STATUS_ACTIVE
                               else WELCOME_AWAITING),
        "welcomeEmailAt": 0,
        "welcomeAttempts": 0,
        "welcomeLeaseUntil": 0,
        "welcomeError": "",
        # Recorded explicitly rather than inferred from
        # resubscribedAt > subscribedAt: those two are equal for a brand-new
        # record, and equal AGAIN whenever a reactivation lands in the same
        # second as the original signup. An owner notification that says "new
        # signup" about a returning subscriber is a small lie that a timestamp
        # comparison will tell sooner or later.
        "welcomeKind": "new",
        "stripeSessionId": "",
        "stripeEventId": "",
        "consentNote": "",
        "createdAt": now,
        "updatedAt": now,
    }


def _subscribe(
    email_lower: str,
    *,
    source: str,
    stripe_session_id: str = "",
    stripe_event_id: str = "",
    consent_note: str = "",
    event_doc_id: str = "",
    status: str = STATUS_ACTIVE,
) -> Dict[str, Any]:
    """Create or reactivate ONE subscriber, transactionally.

    Returns {"result": "created"|"reactivated"|"already_active",
             "subscriber": {...}, "sendWelcome": bool}

    `event_doc_id`, when given, is a Stripe event id: the transaction also
    creates newsletterWebhookEvents/{id} and ABORTS if it already exists. That
    is what makes a Stripe retry a no-op — the guard and the write commit or
    fail together, so there is no window where the subscriber is created but
    the event is not yet marked processed.
    """
    db = _db()
    if db is None:
        raise RuntimeError("Firestore unavailable")

    from firebase_admin import firestore
    transactional = getattr(firestore, "transactional", None)
    if transactional is None:
        from google.cloud.firestore_v1 import transactional

    sub_ref = db.collection(C_SUBS).document(_subscriber_id(email_lower))
    ev_ref = db.collection(C_EVENTS).document(event_doc_id) if event_doc_id else None
    txn = db.transaction()

    @transactional
    def _apply(t) -> Dict[str, Any]:
        # Reads first — Firestore requires every read before any write.
        snap = sub_ref.get(transaction=t)
        ev_snap = ev_ref.get(transaction=t) if ev_ref is not None else None
        if ev_snap is not None and ev_snap.exists:
            return {"result": "duplicate_event", "subscriber": snap.to_dict() or {},
                    "sendWelcome": False}

        now = _now()
        existing = (snap.to_dict() or {}) if snap.exists else {}

        if not snap.exists:
            doc = _blank_subscriber(email_lower, source, status)
            doc["stripeSessionId"] = stripe_session_id
            doc["stripeEventId"] = stripe_event_id
            doc["consentNote"] = consent_note
            t.set(sub_ref, doc)
            result = "created" if status == STATUS_ACTIVE else "pending"
            out = doc
            send_welcome = (status == STATUS_ACTIVE)
        elif existing.get("status") == STATUS_PENDING and status == STATUS_PENDING:
            # They asked again before confirming. Same record, same token —
            # re-sending the confirmation is the caller's job.
            result, send_welcome, out = "already_pending", False, existing
        elif existing.get("status") == STATUS_ACTIVE:
            # Already on the list. No second record, no second welcome, no
            # second "you have a new subscriber" email to Tim.
            result, send_welcome, out = "already_active", False, existing
        else:
            # Previously unsubscribed and intentionally opting in again:
            # reactivate the SAME record (history preserved), rotate the token
            # so any old unsubscribe link in an old inbox stops working, and
            # send the welcome again because this is a fresh, deliberate
            # decision — not a duplicate of the original signup.
            upd = {
                "status": STATUS_ACTIVE,
                "resubscribedAt": now,
                "unsubscribedAt": 0,
                "unsubId": _new_unsub_id(),
                "tokenVersion": _int(existing.get("tokenVersion"), 1) + 1,
                "welcomeEmailStatus": WELCOME_PENDING,
                "welcomeEmailAt": 0,
                "welcomeAttempts": 0,
                "welcomeLeaseUntil": 0,
                "welcomeError": "",
                "welcomeKind": "reactivation",
                "source": source,
                "updatedAt": now,
            }
            if stripe_session_id:
                upd["stripeSessionId"] = stripe_session_id
            if stripe_event_id:
                upd["stripeEventId"] = stripe_event_id
            if consent_note:
                upd["consentNote"] = consent_note
            t.set(sub_ref, upd, merge=True)
            out = dict(existing)
            out.update(upd)
            result, send_welcome = "reactivated", True

        if ev_ref is not None:
            t.set(ev_ref, {
                "eventId": event_doc_id,
                "sessionId": stripe_session_id,
                "subscriberId": _subscriber_id(email_lower),
                "result": result,
                "processedAt": now,
            })
        return {"result": result, "subscriber": out, "sendWelcome": send_welcome}

    return _apply(txn)


def _unsubscribe_by_id(sub_id: str, *, actor: str, reason: str) -> Dict[str, Any]:
    """Flip one subscriber to unsubscribed. Idempotent: unsubscribing an
    already-unsubscribed person succeeds and changes nothing, because the
    unsubscribe link in an old email must keep working forever without ever
    showing the reader an error."""
    db = _db()
    if db is None:
        raise RuntimeError("Firestore unavailable")
    from firebase_admin import firestore
    transactional = getattr(firestore, "transactional", None)
    if transactional is None:
        from google.cloud.firestore_v1 import transactional

    ref = db.collection(C_SUBS).document(sub_id)
    txn = db.transaction()

    @transactional
    def _apply(t) -> Dict[str, Any]:
        snap = ref.get(transaction=t)
        if not snap.exists:
            return {"ok": False, "result": "not_found"}
        cur = snap.to_dict() or {}
        if cur.get("status") == STATUS_UNSUB:
            return {"ok": True, "result": "already_unsubscribed", "email": cur.get("emailLower", "")}
        now = _now()
        t.set(ref, {"status": STATUS_UNSUB, "unsubscribedAt": now, "updatedAt": now}, merge=True)
        return {"ok": True, "result": "unsubscribed", "email": cur.get("emailLower", "")}

    out = _apply(txn)
    if out.get("result") == "unsubscribed":
        audit("subscriber_unsubscribed", admin=actor, subscriber_id=sub_id,
              summary="Unsubscribed via %s" % reason)
    return out


# ── cached list read ───────────────────────────────────────────────────────
# The admin list searches, filters, sorts and paginates in Python over one
# cached snapshot. That is a deliberate trade: Firestore would need a composite
# index for every (status, sort-field) pair, which is a manual console step per
# combination and a 500 the first time one is missed. At newsletter scale
# (thousands, not millions) one cached scan is both faster and impossible to
# misconfigure. MAX_SUBSCRIBER_SCAN is the honest ceiling — past it the list is
# truncated and the UI says so rather than quietly showing a subset.
_SUBS_CACHE: Dict[str, Any] = {"at": 0.0, "rows": [], "truncated": False}
_SUBS_TTL = 15.0
_SUBS_LOCK = threading.Lock()


def _invalidate_subs_cache() -> None:
    with _SUBS_LOCK:
        _SUBS_CACHE["at"] = 0.0


def _all_subscribers(force: bool = False) -> Tuple[List[Dict[str, Any]], bool]:
    with _SUBS_LOCK:
        if not force and _SUBS_CACHE["rows"] and (time.time() - _SUBS_CACHE["at"]) < _SUBS_TTL:
            return list(_SUBS_CACHE["rows"]), bool(_SUBS_CACHE["truncated"])
    db = _db()
    if db is None:
        return [], False
    rows: List[Dict[str, Any]] = []
    truncated = False
    try:
        snap = db.collection(C_SUBS).limit(MAX_SUBSCRIBER_SCAN + 1).get()
        for doc in snap:
            d = doc.to_dict() or {}
            d["id"] = doc.id
            rows.append(d)
        if len(rows) > MAX_SUBSCRIBER_SCAN:
            rows = rows[:MAX_SUBSCRIBER_SCAN]
            truncated = True
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] subscriber scan failed: %s" % exc)
        return [], False
    with _SUBS_LOCK:
        _SUBS_CACHE["rows"] = list(rows)
        _SUBS_CACHE["at"] = time.time()
        _SUBS_CACHE["truncated"] = truncated
    return rows, truncated


def _public_subscriber(d: Dict[str, Any]) -> Dict[str, Any]:
    """What the admin UI is allowed to see about a subscriber.

    Note what is absent: unsubId and tokenVersion. The admin page never needs
    a working unsubscribe token, so it is never sent one — that keeps the
    token out of the browser, out of the network tab, and out of any screenshot
    or CSV that leaves the machine.
    """
    return {
        "id": d.get("id", ""),
        "email": d.get("emailLower") or d.get("email") or "",
        "status": d.get("status") or STATUS_UNSUB,
        "source": d.get("source") or "",
        "subscribedAt": _int(d.get("subscribedAt")),
        "subscribedAtIso": _iso(d.get("subscribedAt")),
        "resubscribedAt": _int(d.get("resubscribedAt")),
        "resubscribedAtIso": _iso(d.get("resubscribedAt")),
        "unsubscribedAt": _int(d.get("unsubscribedAt")),
        "unsubscribedAtIso": _iso(d.get("unsubscribedAt")),
        "welcomeEmailStatus": d.get("welcomeEmailStatus") or "",
        "welcomeEmailAtIso": _iso(d.get("welcomeEmailAt")),
        "consentNote": str(d.get("consentNote") or "")[:200],
    }


def counts() -> Dict[str, int]:
    rows, _ = _all_subscribers()
    active = sum(1 for r in rows if r.get("status") == STATUS_ACTIVE)
    pending = sum(1 for r in rows if r.get("status") == STATUS_PENDING)
    # Unsubscribed is what is LEFT, not "everything that is not active" —
    # pending sits in neither bucket and must not inflate either.
    return {"active": active, "pending": pending,
            "unsubscribed": len(rows) - active - pending, "total": len(rows)}


# ═══════════════════════════════════════════════════════════════════════════
#  WELCOME + OWNER NOTIFICATION DELIVERY
# ═══════════════════════════════════════════════════════════════════════════
_welcome_q: "queue.Queue[str]" = queue.Queue()


def _claim_welcome(sub_id: str) -> Optional[Dict[str, Any]]:
    """Move one subscriber's welcome email from pending → sending, once.

    The lease is what stops two workers (or a worker and a retry sweep) sending
    the same welcome twice. Returns the subscriber dict when THIS caller owns
    the send, else None.
    """
    db = _db()
    if db is None:
        return None
    from firebase_admin import firestore
    transactional = getattr(firestore, "transactional", None)
    if transactional is None:
        from google.cloud.firestore_v1 import transactional

    ref = db.collection(C_SUBS).document(sub_id)
    txn = db.transaction()

    @transactional
    def _apply(t):
        snap = ref.get(transaction=t)
        if not snap.exists:
            return None
        d = snap.to_dict() or {}
        if d.get("status") != STATUS_ACTIVE:
            return None
        if d.get("welcomeEmailStatus") != WELCOME_PENDING:
            return None
        now = _now()
        if _int(d.get("welcomeLeaseUntil")) > now:
            return None                      # someone else is sending it
        attempts = _int(d.get("welcomeAttempts")) + 1
        t.set(ref, {"welcomeEmailStatus": "sending", "welcomeAttempts": attempts,
                    "welcomeLeaseUntil": now + LEASE_SEC, "updatedAt": now}, merge=True)
        d["welcomeAttempts"] = attempts
        d["id"] = sub_id
        return d

    try:
        return _apply(txn)
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] welcome claim failed: %s" % exc)
        return None


def _finish_welcome(sub_id: str, *, status: str, error: str = "") -> None:
    db = _db()
    if db is None:
        return
    upd = {"welcomeEmailStatus": status, "welcomeLeaseUntil": 0,
           "welcomeError": str(error or "")[:300], "updatedAt": _now()}
    if status == "sent":
        upd["welcomeEmailAt"] = _now()
    try:
        db.collection(C_SUBS).document(sub_id).set(upd, merge=True)
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] welcome status write failed: %s" % exc)
    _invalidate_subs_cache()


def _send_welcome_now(sub_id: str) -> None:
    """Send the welcome email + the owner notification for one subscriber.

    Never raises: this runs on the worker thread, and a failure here must leave
    the subscriber intact and retryable, not take the thread down.
    """
    sub = _claim_welcome(sub_id)
    if not sub:
        return
    email = str(sub.get("emailLower") or sub.get("email") or "")
    unsub = unsubscribe_url(sub)
    is_reactivation = str(sub.get("welcomeKind") or "new") == "reactivation"

    if not unsub:
        _finish_welcome(sub_id, status="failed",
                        error="NEWSLETTER_UNSUBSCRIBE_SECRET is not set, so no unsubscribe "
                              "link could be built. Refusing to send marketing mail without one.")
        audit("welcome_email_failed", subscriber_id=sub_id,
              summary="No unsubscribe secret configured; welcome not sent.")
        return

    try:
        msg = nl_email.build_welcome(unsub, one_click_url(sub))
        nl_email.send_email(
            to_email=email, subject=msg["subject"], html_body=msg["html"],
            text_body=msg["text"], unsubscribe_url=unsub, one_click_url=one_click_url(sub),
        )
    except nl_email.SendError as exc:
        if exc.retryable and _int(sub.get("welcomeAttempts")) < MAX_ATTEMPTS:
            # Known NOT to have been delivered → safe to put back on the queue.
            _finish_welcome(sub_id, status="pending", error=str(exc)[:300])
        else:
            _finish_welcome(sub_id, status="failed", error=str(exc)[:300])
            audit("welcome_email_failed", subscriber_id=sub_id,
                  summary="Welcome email failed: %s" % exc.category)
        print("[newsletter] welcome send failed for %s: %s" % (sub_id, exc))
        return
    except Exception as exc:  # noqa: BLE001
        _finish_welcome(sub_id, status="failed", error=str(exc)[:300])
        audit("welcome_email_failed", subscriber_id=sub_id, summary="Welcome email error")
        print("[newsletter] welcome send error for %s: %s" % (sub_id, exc))
        return

    _finish_welcome(sub_id, status="sent")
    audit("welcome_email_sent", subscriber_id=sub_id,
          summary="Welcome email sent (%s)" % ("reactivation" if is_reactivation else "new signup"))

    # The owner notification rides along with the welcome so the two can never
    # disagree about whether somebody joined, and so a duplicate signup (which
    # never gets here) can never produce a duplicate notification either.
    try:
        note = nl_email.build_owner_notification(
            subscriber_email=email,
            subscribed_at=_iso(sub.get("resubscribedAt") or sub.get("subscribedAt")),
            source=str(sub.get("source") or ""),
            is_reactivation=is_reactivation,
            active_total=counts().get("active"),
        )
        nl_email.send_email(to_email=admin_email(), subject=note["subject"],
                            html_body=note["html"], text_body=note["text"], is_bulk=False)
    except Exception as exc:  # noqa: BLE001
        # The subscriber is on the list and has their welcome; failing to tell
        # Tim about it is not worth marking anything as failed.
        print("[newsletter] owner notification failed: %s" % exc)


def _send_confirmation(sub: Dict[str, Any]) -> None:
    """Send the "click to confirm" email. Never raises: a failure here leaves a
    pending record the person can create again, which is the safe direction."""
    email = str(sub.get("emailLower") or sub.get("email") or "")
    url = confirm_url(sub)
    if not email or not url:
        print("[newsletter] cannot build a confirmation link; skipping")
        return
    try:
        msg = nl_email.build_confirmation(url)
        nl_email.send_email(to_email=email, subject=msg["subject"],
                            html_body=msg["html"], text_body=msg["text"],
                            is_bulk=False)
        audit("confirmation_sent", subscriber_id=str(sub.get("id") or ""),
              summary="Confirmation email sent for a website signup")
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] confirmation send failed: %s" % exc)


def _queue_welcome(sub_id: str) -> None:
    try:
        _welcome_q.put_nowait(sub_id)
    except Exception:  # noqa: BLE001
        pass


# ═══════════════════════════════════════════════════════════════════════════
#  STRIPE HOOK
# ═══════════════════════════════════════════════════════════════════════════
def _record_seen_labels(labels: List[str], matched: bool) -> None:
    """Remember which custom-field labels Stripe actually sent.

    Label TEXT only — never an answer, never an address. This exists so the
    admin Settings tab can show "the last checkout asked: …", which is the
    difference between diagnosing a label mismatch in ten seconds and never
    noticing that signups silently stopped.
    """
    db = _db()
    if db is None or not labels:
        return
    try:
        db.collection(C_META).document("stripeFieldLabels").set({
            "lastSeen": [str(l)[:120] for l in labels][:12],
            "lastSeenAt": _now(),
            "lastSeenMatched": bool(matched),
            "acceptedLabels": accepted_labels()[:20],
        }, merge=True)
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] label diagnostic write failed: %s" % exc)


def handle_stripe_session(event: Dict[str, Any], session: Dict[str, Any]) -> str:
    """Newsletter side of a VERIFIED, PAID Stripe checkout session.

    Called from multiplayer_server's Stripe webhook AFTER the signature is
    verified and after payment fulfilment. Returns a short status string.

    This function must never raise: the purchase has already been fulfilled and
    committed by the time it runs, so throwing would turn a settled payment
    into a 500 and make Stripe retry a completed order. Newsletter problems are
    logged and left for the admin page to show; they never affect money.
    """
    try:
        if str(session.get("payment_status") or "") != "paid":
            return "unpaid"
        email, labels = extract_newsletter_email(session.get("custom_fields"))
        _record_seen_labels(labels, bool(email))
        if not email:
            # Blank field, or an invalid address, or a checkout with no such
            # field. All three mean "no consent given" — not an error.
            return "no_signup"

        event_id = str(event.get("id") or "").strip()[:200]
        session_id = str(session.get("id") or "").strip()[:200]
        if not event_id:
            event_id = "session:" + session_id
        if not event_id:
            return "no_event_id"

        out = _subscribe(
            email,
            source=SOURCE_STRIPE,
            stripe_session_id=session_id,
            stripe_event_id=event_id,
            event_doc_id=event_id,
        )
        result = out.get("result", "")
        sub = out.get("subscriber") or {}
        sub_id = sub.get("id") or _subscriber_id(email)
        _invalidate_subs_cache()

        if result in ("created", "reactivated"):
            audit("subscriber_added_stripe", subscriber_id=sub_id,
                  summary="%s via Stripe Checkout (session %s)"
                          % ("Reactivated" if result == "reactivated" else "Subscribed",
                             session_id[:32]))
            if out.get("sendWelcome"):
                _queue_welcome(sub_id)
        print("[newsletter] stripe signup %s (%s)" % (result, session_id[:24]))
        return result
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] stripe hook failed: %s" % exc)
        traceback.print_exc()
        return "error"


# ═══════════════════════════════════════════════════════════════════════════
#  UNSUBSCRIBE
# ═══════════════════════════════════════════════════════════════════════════
def resolve_unsub_token(token: str) -> Optional[Dict[str, Any]]:
    """Subscriber for a valid token, else None.

    The MAC is checked with hmac.compare_digest, so a wrong token cannot be
    narrowed down by timing, and a token whose MAC does not match the CURRENT
    tokenVersion is rejected — that is how reactivation retires old links.
    """
    uid, mac = _parse_token(token)
    if not uid or not mac:
        return None
    db = _db()
    if db is None:
        return None
    try:
        snap = db.collection(C_SUBS).where("unsubId", "==", uid).limit(1).get()
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] unsub lookup failed: %s" % exc)
        return None
    for doc in snap:
        d = doc.to_dict() or {}
        d["id"] = doc.id
        expected = _mac(uid, _int(d.get("tokenVersion"), 1))
        if expected and hmac.compare_digest(expected, mac):
            return d
        return None
    return None


def request_subscription(email_lower: str, *, source: str = SOURCE_WEBSITE) -> Dict[str, Any]:
    """Step 1 of a PUBLIC signup: record a pending address and hand back the
    confirmation link. Creates nothing that any campaign can reach."""
    out = _subscribe(email_lower, source=source, status=STATUS_PENDING,
                     consent_note="Website signup; awaiting email confirmation.")
    _invalidate_subs_cache()
    return out


def confirm_with_token(token: str) -> Dict[str, Any]:
    """Step 2: the person clicked the link in their inbox, which is the only
    proof we accept that they own the address.

    Idempotent — clicking twice, or a mail client pre-fetching the link, must
    not error and must not re-send the welcome.
    """
    uid, mac = _parse_token(token)
    if not uid or not mac:
        return {"ok": False, "known": False}
    db = _db()
    if db is None:
        return {"ok": False, "known": False}
    try:
        snap = db.collection(C_SUBS).where("unsubId", "==", uid).limit(1).get()
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] confirm lookup failed: %s" % exc)
        return {"ok": False, "known": False}

    sub = None
    for doc in snap:
        d = doc.to_dict() or {}
        d["id"] = doc.id
        expected = _mac(uid, _int(d.get("tokenVersion"), 1), purpose="confirm")
        if expected and hmac.compare_digest(expected, mac):
            sub = d
        break
    if sub is None:
        return {"ok": False, "known": False}

    if sub.get("status") == STATUS_ACTIVE:
        return {"ok": True, "known": True, "result": "already_active",
                "email": sub.get("emailLower", "")}
    if sub.get("status") == STATUS_UNSUB:
        # They unsubscribed after signing up. Honour that over a stale link.
        return {"ok": True, "known": True, "result": "unsubscribed",
                "email": sub.get("emailLower", "")}

    now = _now()
    try:
        db.collection(C_SUBS).document(sub["id"]).set({
            "status": STATUS_ACTIVE,
            "resubscribedAt": now,
            "confirmedAt": now,
            "updatedAt": now,
            # The welcome was parked at WELCOME_AWAITING while this record was
            # unconfirmed. THIS is the moment it becomes due, so arm it here —
            # the worker only ever picks up WELCOME_PENDING.
            "welcomeEmailStatus": WELCOME_PENDING,
            "welcomeAttempts": 0,
            "welcomeLeaseUntil": 0,
            "welcomeError": "",
            "consentNote": "Website signup, confirmed by email click.",
        }, merge=True)
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] confirm write failed: %s" % exc)
        return {"ok": False, "known": True}

    _invalidate_subs_cache()
    audit("subscriber_added_website", subscriber_id=sub["id"],
          summary="Confirmed a website signup by email click")
    _queue_welcome(sub["id"])
    _wake_worker()
    return {"ok": True, "known": True, "result": "confirmed",
            "email": sub.get("emailLower", "")}


def _load_subscriber(sub_id: str) -> Optional[Dict[str, Any]]:
    db = _db()
    if db is None:
        return None
    try:
        snap = db.collection(C_SUBS).document(sub_id).get()
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] subscriber read failed: %s" % exc)
        return None
    if not snap.exists:
        return None
    d = snap.to_dict() or {}
    d["id"] = sub_id
    return d


def resend_confirmation(sub_id: str, *, actor: str) -> Dict[str, Any]:
    """Send the confirmation email again for one PENDING signup.

    Same record, same token: a resend must not invalidate the link already
    sitting in the person's inbox, or clicking the older mail would fail.
    """
    sub = _load_subscriber(sub_id)
    if sub is None:
        return {"ok": False, "error": "not_found"}
    if sub.get("status") != STATUS_PENDING:
        return {"ok": False, "error": "Only a signup that is still waiting for "
                                      "confirmation can be sent one."}
    if not unsub_secret_configured():
        return {"ok": False, "error": "NEWSLETTER_UNSUBSCRIBE_SECRET is not set, so no "
                                      "confirmation link can be built."}
    _send_confirmation(sub)
    audit("confirmation_resent", admin=actor, subscriber_id=sub_id,
          summary="Confirmation email re-sent")
    return {"ok": True, "email": str(sub.get("emailLower") or "")}


def confirm_by_admin(sub_id: str, *, actor: str, reason: str) -> Dict[str, Any]:
    """Turn a pending signup into a subscriber WITHOUT the email click.

    The email click is the proof that somebody owns an address, and this
    bypasses it — so it is deliberately narrow: pending records only, a typed
    reason is required, and the actor is written into the audit log and into
    the record's own consent note. It exists because a confirmation mail can
    land in spam or bounce off a mail server, and the alternative to a
    recorded, attributed override is Tim editing Firestore by hand.
    """
    reason = str(reason or "").strip()[:200]
    if not reason:
        return {"ok": False, "error": "Say why you are confirming this address by hand."}
    sub = _load_subscriber(sub_id)
    if sub is None:
        return {"ok": False, "error": "not_found"}
    if sub.get("status") == STATUS_ACTIVE:
        return {"ok": True, "result": "already_active",
                "email": str(sub.get("emailLower") or "")}
    if sub.get("status") != STATUS_PENDING:
        return {"ok": False, "error": "That person unsubscribed. Use Reactivate instead."}

    db = _db()
    if db is None:
        return {"ok": False, "error": "Firestore unavailable"}
    now = _now()
    try:
        db.collection(C_SUBS).document(sub_id).set({
            "status": STATUS_ACTIVE,
            "resubscribedAt": now,
            "confirmedAt": now,
            "confirmedByAdmin": actor[:200],
            "updatedAt": now,
            "welcomeEmailStatus": WELCOME_PENDING,
            "welcomeAttempts": 0,
            "welcomeLeaseUntil": 0,
            "welcomeError": "",
            "consentNote": ("Confirmed by admin %s: %s" % (actor, reason))[:200],
        }, merge=True)
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] admin confirm failed: %s" % exc)
        return {"ok": False, "error": "Could not confirm that address."}

    _invalidate_subs_cache()
    audit("subscriber_confirmed_by_admin", admin=actor, subscriber_id=sub_id,
          summary="Confirmed by hand: %s" % reason)
    _queue_welcome(sub_id)
    _wake_worker()
    return {"ok": True, "result": "confirmed", "email": str(sub.get("emailLower") or "")}


def unsubscribe_with_token(token: str) -> Dict[str, Any]:
    """Apply an unsubscribe. Always safe to call twice.

    Returns {"ok": bool, "known": bool, "email": str}. `known` is False for a
    bad/absent token — the CALLER still shows a success page in that case, so
    the page can never be used to test whether an address is on the list.
    """
    sub = resolve_unsub_token(token)
    if not sub:
        return {"ok": False, "known": False, "email": ""}
    out = _unsubscribe_by_id(sub["id"], actor="self-service", reason="unsubscribe link")
    _invalidate_subs_cache()
    return {"ok": True, "known": True, "email": str(sub.get("emailLower") or ""),
            "result": out.get("result", "")}


# ═══════════════════════════════════════════════════════════════════════════
#  CAMPAIGNS
# ═══════════════════════════════════════════════════════════════════════════
def _campaign_public(d: Dict[str, Any], *, with_content: bool = False) -> Dict[str, Any]:
    out = {
        "id": d.get("id", ""),
        "subject": d.get("subject") or "",
        "previewText": d.get("previewText") or "",
        "status": d.get("status") or CAMP_DRAFT,
        "createdAt": _int(d.get("createdAt")),
        "createdAtIso": _iso(d.get("createdAt")),
        "updatedAtIso": _iso(d.get("updatedAt")),
        "startedAtIso": _iso(d.get("startedAt")),
        "sentAtIso": _iso(d.get("sentAt")),
        "intendedRecipients": _int(d.get("intendedRecipients")),
        "sentCount": _int(d.get("sentCount")),
        "failedCount": _int(d.get("failedCount")),
        "skippedCount": _int(d.get("skippedCount")),
        "interruptedCount": _int(d.get("interruptedCount")),
        "createdBy": d.get("createdBy") or "",
        "startedBy": d.get("startedBy") or "",
    }
    done = out["sentCount"] + out["failedCount"] + out["skippedCount"] + out["interruptedCount"]
    out["doneCount"] = done
    out["pendingCount"] = max(0, out["intendedRecipients"] - done)
    out["percent"] = (round(100.0 * done / out["intendedRecipients"], 1)
                      if out["intendedRecipients"] else 0.0)
    if with_content:
        out["contentHtml"] = d.get("contentHtml") or ""
    return out


def _get_campaign(cid: str) -> Optional[Dict[str, Any]]:
    db = _db()
    if db is None or not cid:
        return None
    try:
        snap = db.collection(C_CAMPAIGNS).document(cid).get()
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] campaign read failed: %s" % exc)
        return None
    if not snap.exists:
        return None
    d = snap.to_dict() or {}
    d["id"] = snap.id
    return d


def _save_draft(*, cid: str, subject: str, preview: str, content_html: str,
                admin: str) -> Dict[str, Any]:
    db = _db()
    if db is None:
        return {"ok": False, "error": "unavailable"}

    subject = re.sub(r"[\r\n]+", " ", str(subject or "")).strip()[:MAX_SUBJECT]
    preview = re.sub(r"[\r\n]+", " ", str(preview or "")).strip()[:MAX_PREVIEW]
    # Sanitised on the way IN, so nothing unsafe is ever at rest and no later
    # reader has to remember to clean it.
    content_html = nl_email.sanitize_html(str(content_html or "")[:MAX_CONTENT])
    if not subject:
        return {"ok": False, "error": "A subject is required."}

    now = _now()
    if cid:
        existing = _get_campaign(cid)
        if not existing:
            return {"ok": False, "error": "That draft no longer exists."}
        if existing.get("status") != CAMP_DRAFT:
            return {"ok": False, "error": "This newsletter has already been sent and cannot be edited. "
                                          "Duplicate it to make a new draft."}
        db.collection(C_CAMPAIGNS).document(cid).set({
            "subject": subject, "previewText": preview, "contentHtml": content_html,
            "updatedAt": now, "updatedBy": admin,
        }, merge=True)
        audit("draft_updated", admin=admin, campaign_id=cid, summary="Draft updated: %s" % subject[:120])
        return {"ok": True, "id": cid, "created": False}

    ref = db.collection(C_CAMPAIGNS).document()
    ref.set({
        "subject": subject, "previewText": preview, "contentHtml": content_html,
        "status": CAMP_DRAFT, "createdAt": now, "updatedAt": now,
        "createdBy": admin, "intendedRecipients": 0,
        "sentCount": 0, "failedCount": 0, "skippedCount": 0, "interruptedCount": 0,
    })
    audit("draft_created", admin=admin, campaign_id=ref.id, summary="Draft created: %s" % subject[:120])
    return {"ok": True, "id": ref.id, "created": True}


def _render_campaign(camp: Dict[str, Any], *, unsub: str, is_test: bool) -> Dict[str, str]:
    body = nl_email.sanitize_html(camp.get("contentHtml") or "")
    subject = str(camp.get("subject") or "")
    if is_test:
        subject = "[TEST] " + subject
    return {
        "subject": subject,
        "html": nl_email.render_email_html(
            body_html=body, unsubscribe_url=unsub,
            preview_text=str(camp.get("previewText") or ""), is_test=is_test),
        "text": nl_email.render_email_text(body_html=body, unsubscribe_url=unsub, is_test=is_test),
    }


# ── starting a campaign ────────────────────────────────────────────────────
_START_LOCK = threading.Lock()


def _start_campaign(cid: str, *, admin: str, confirm: str) -> Dict[str, Any]:
    """Approve and enqueue a campaign. The single most dangerous button in the
    system, so it is guarded five ways:
      1. the typed confirmation phrase must be exactly SEND,
      2. a process-wide lock serialises concurrent start requests,
      3. the status flip draft→sending happens in a TRANSACTION, so only one
         request can ever win the race (this is what makes a double-click, a
         page refresh and a retried network request all harmless),
      4. recipients are materialised as documents keyed by subscriber id, so a
         second pass cannot create a second delivery for anyone,
      5. every send re-reads the subscriber's live status first.
    """
    if str(confirm or "").strip() != "SEND":
        return {"ok": False, "error": 'Type SEND in the confirmation box to start the send.'}
    db = _db()
    if db is None:
        return {"ok": False, "error": "unavailable"}

    camp = _get_campaign(cid)
    if not camp:
        return {"ok": False, "error": "That newsletter no longer exists."}
    if camp.get("status") != CAMP_DRAFT:
        return {"ok": False, "error": "This newsletter is already %s." % camp.get("status")}
    if not str(camp.get("contentHtml") or "").strip():
        return {"ok": False, "error": "This newsletter has no content yet."}

    gm = nl_email.connection_status()
    if not gm.get("connected") or not gm.get("canSendAsSender"):
        return {"ok": False, "error": "Email sending is not connected (%s): %s"
                                      % (gm.get("transportLabel") or "no transport",
                                         gm.get("error") or "unknown")}
    if not unsub_secret_configured():
        return {"ok": False, "error": "NEWSLETTER_UNSUBSCRIBE_SECRET is not set, so unsubscribe "
                                      "links cannot be generated. Refusing to send."}

    rows, _trunc = _all_subscribers(force=True)
    active = [r for r in rows if r.get("status") == STATUS_ACTIVE]
    if not active:
        return {"ok": False, "error": "There are no active subscribers to send to."}

    from firebase_admin import firestore
    transactional = getattr(firestore, "transactional", None)
    if transactional is None:
        from google.cloud.firestore_v1 import transactional
    ref = db.collection(C_CAMPAIGNS).document(cid)

    with _START_LOCK:
        txn = db.transaction()

        @transactional
        def _claim(t) -> bool:
            snap = ref.get(transaction=t)
            if not snap.exists:
                return False
            if (snap.to_dict() or {}).get("status") != CAMP_DRAFT:
                return False        # somebody already started it
            now = _now()
            t.set(ref, {
                "status": CAMP_SENDING, "startedAt": now, "updatedAt": now,
                "startedBy": admin, "intendedRecipients": len(active),
                "sentCount": 0, "failedCount": 0, "skippedCount": 0, "interruptedCount": 0,
                "sendToken": secrets.token_urlsafe(12),
            }, merge=True)
            return True

        try:
            won = _claim(txn)
        except Exception as exc:  # noqa: BLE001
            print("[newsletter] campaign claim failed: %s" % exc)
            return {"ok": False, "error": "Could not start the send. Please try again."}
        if not won:
            return {"ok": False, "error": "This newsletter is already sending or sent."}

    audit("campaign_approved", admin=admin, campaign_id=cid,
          summary="Approved for %d active subscribers" % len(active))

    # Materialise one recipient document per subscriber, keyed BY SUBSCRIBER ID
    # so the same person can never appear twice in the same campaign.
    written = 0
    try:
        batch = db.batch()
        n = 0
        for sub in active:
            rref = ref.collection(C_RECIPIENTS).document(sub["id"])
            batch.set(rref, {
                "campaignId": cid,
                "subscriberId": sub["id"],
                "email": sub.get("emailLower") or sub.get("email") or "",
                "status": R_PENDING,
                "attempts": 0,
                "gmailMessageId": "",
                "lastErrorCategory": "",
                "sentAt": 0,
                "leaseUntil": 0,
                "updatedAt": _now(),
            })
            n += 1
            written += 1
            if n >= 400:            # Firestore caps a batch at 500 writes
                batch.commit()
                batch = db.batch()
                n = 0
        if n:
            batch.commit()
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] recipient materialisation failed: %s" % exc)
        # The pass is resumable: recipients already written stay pending and
        # the worker keeps going; re-running start is blocked by the status.

    audit("campaign_started", admin=admin, campaign_id=cid,
          summary="Sending started, %d recipients queued" % written)
    _wake_worker()
    return {"ok": True, "id": cid, "recipients": written}


# ── the send worker ────────────────────────────────────────────────────────
_worker_wake = threading.Event()
_worker_started = False


def _wake_worker() -> None:
    _worker_wake.set()


def _claim_recipient(cref, sub_id: str) -> Optional[Dict[str, Any]]:
    """pending → sending for ONE recipient, and re-check the subscriber's live
    status in the same transaction.

    "Check the subscriber's status again immediately before each email is sent"
    is not a nicety: somebody who unsubscribes while a campaign is grinding
    through ten thousand addresses must not receive it, and the only place that
    can be guaranteed is inside the claim.
    """
    db = _db()
    if db is None:
        return None
    from firebase_admin import firestore
    transactional = getattr(firestore, "transactional", None)
    if transactional is None:
        from google.cloud.firestore_v1 import transactional

    rref = cref.collection(C_RECIPIENTS).document(sub_id)
    sref = db.collection(C_SUBS).document(sub_id)
    txn = db.transaction()

    @transactional
    def _apply(t):
        rsnap = rref.get(transaction=t)
        ssnap = sref.get(transaction=t)
        if not rsnap.exists:
            return None
        r = rsnap.to_dict() or {}
        now = _now()
        if r.get("status") in (R_SENT, R_SKIPPED):
            return None
        if r.get("status") == R_SENDING and _int(r.get("leaseUntil")) > now:
            return None
        if r.get("status") in (R_FAILED, R_INTERRUPTED):
            return None                       # only an explicit retry revives these
        sub = (ssnap.to_dict() or {}) if ssnap.exists else {}
        if not ssnap.exists or sub.get("status") != STATUS_ACTIVE:
            t.set(rref, {"status": R_SKIPPED, "updatedAt": now,
                         "lastErrorCategory": "unsubscribed_before_send"}, merge=True)
            return {"_skipped": True}
        attempts = _int(r.get("attempts")) + 1
        t.set(rref, {"status": R_SENDING, "attempts": attempts,
                     "leaseUntil": now + LEASE_SEC, "updatedAt": now}, merge=True)
        sub["id"] = sub_id
        return {"subscriber": sub, "attempts": attempts,
                "email": r.get("email") or sub.get("emailLower") or ""}

    try:
        return _apply(txn)
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] recipient claim failed: %s" % exc)
        return None


def _bump(cref, field: str, by: int = 1) -> None:
    db = _db()
    if db is None:
        return
    try:
        from firebase_admin import firestore
        Increment = getattr(firestore, "Increment", None)
        if Increment is None:
            from google.cloud.firestore_v1 import Increment
        cref.set({field: Increment(by), "updatedAt": _now()}, merge=True)
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] counter bump failed (%s): %s" % (field, exc))


def _process_campaign_batch(cid: str) -> int:
    """Send up to SEND_BATCH messages for one campaign. Returns how many were
    attempted; 0 means there is nothing left to do right now."""
    db = _db()
    if db is None:
        return 0
    cref = db.collection(C_CAMPAIGNS).document(cid)
    camp = _get_campaign(cid)
    if not camp or camp.get("status") != CAMP_SENDING:
        return 0

    # Reclaim anything a previous process died holding. Presumed INTERRUPTED,
    # never auto-resent: at the moment the process died the message may or may
    # not have reached Gmail, and silently re-sending would be the one failure
    # mode this whole design exists to prevent. The admin page surfaces these
    # with an explicit retry button.
    try:
        stale = cref.collection(C_RECIPIENTS).where("status", "==", R_SENDING).limit(200).get()
        now = _now()
        for doc in stale:
            d = doc.to_dict() or {}
            if _int(d.get("leaseUntil")) > now:
                continue
            doc.reference.set({"status": R_INTERRUPTED, "updatedAt": now,
                               "lastErrorCategory": "interrupted"}, merge=True)
            _bump(cref, "interruptedCount")
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] stale sweep failed: %s" % exc)

    try:
        pending = cref.collection(C_RECIPIENTS).where("status", "==", R_PENDING).limit(SEND_BATCH).get()
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] pending query failed: %s" % exc)
        return 0

    ids = [doc.id for doc in pending]
    if not ids:
        _finish_campaign(cid)
        return 0

    attempted = 0
    for sub_id in ids:
        claim = _claim_recipient(cref, sub_id)
        if claim is None:
            continue
        if claim.get("_skipped"):
            _bump(cref, "skippedCount")
            continue

        sub = claim["subscriber"]
        unsub = unsubscribe_url(sub)
        rref = cref.collection(C_RECIPIENTS).document(sub_id)
        attempted += 1
        try:
            msg = _render_campaign(camp, unsub=unsub, is_test=False)
            res = nl_email.send_email(
                to_email=claim["email"], subject=msg["subject"], html_body=msg["html"],
                text_body=msg["text"], unsubscribe_url=unsub, one_click_url=one_click_url(sub),
            )
            rref.set({"status": R_SENT, "sentAt": _now(), "updatedAt": _now(),
                      "gmailMessageId": str(res.get("gmailId") or "")[:120],
                      "leaseUntil": 0, "lastErrorCategory": ""}, merge=True)
            _bump(cref, "sentCount")
        except nl_email.SendError as exc:
            if exc.category == "daily_cap":
                # Not this recipient's fault: put them straight back to pending
                # and stop the pass. Sending resumes on the next worker tick
                # after the cap rolls over.
                rref.set({"status": R_PENDING, "leaseUntil": 0, "updatedAt": _now(),
                          "lastErrorCategory": "daily_cap"}, merge=True)
                print("[newsletter] daily cap reached; pausing campaign %s" % cid)
                return attempted
            if exc.retryable and claim["attempts"] < MAX_ATTEMPTS:
                rref.set({"status": R_PENDING, "leaseUntil": 0, "updatedAt": _now(),
                          "lastErrorCategory": exc.category}, merge=True)
            else:
                rref.set({"status": R_FAILED, "leaseUntil": 0, "updatedAt": _now(),
                          "lastErrorCategory": exc.category}, merge=True)
                _bump(cref, "failedCount")
            if not exc.retryable and exc.category in ("auth_revoked", "config", "forbidden"):
                # A credential problem is not per-recipient; grinding through
                # the rest of the list would just fail ten thousand times.
                print("[newsletter] fatal send error, pausing campaign %s: %s" % (cid, exc.category))
                return attempted
        except Exception as exc:  # noqa: BLE001
            rref.set({"status": R_FAILED, "leaseUntil": 0, "updatedAt": _now(),
                      "lastErrorCategory": "unknown"}, merge=True)
            _bump(cref, "failedCount")
            print("[newsletter] send error for campaign %s: %s" % (cid, exc))
    return attempted


def _finish_campaign(cid: str) -> None:
    """Close a campaign once nothing is pending. Counts are recomputed from the
    recipient documents rather than trusted from the running totals, so a
    dropped increment shows the truth in the history."""
    db = _db()
    if db is None:
        return
    cref = db.collection(C_CAMPAIGNS).document(cid)
    try:
        if cref.collection(C_RECIPIENTS).where("status", "==", R_PENDING).limit(1).get():
            return
        if cref.collection(C_RECIPIENTS).where("status", "==", R_SENDING).limit(1).get():
            return
        tally = {R_SENT: 0, R_FAILED: 0, R_SKIPPED: 0, R_INTERRUPTED: 0}
        for doc in cref.collection(C_RECIPIENTS).limit(MAX_SUBSCRIBER_SCAN).get():
            st = (doc.to_dict() or {}).get("status")
            if st in tally:
                tally[st] += 1
        now = _now()
        cref.set({
            "status": CAMP_SENT, "sentAt": now, "updatedAt": now,
            "sentCount": tally[R_SENT], "failedCount": tally[R_FAILED],
            "skippedCount": tally[R_SKIPPED], "interruptedCount": tally[R_INTERRUPTED],
        }, merge=True)
        audit("campaign_completed", campaign_id=cid,
              summary="Completed: %d sent, %d failed, %d skipped, %d interrupted"
                      % (tally[R_SENT], tally[R_FAILED], tally[R_SKIPPED], tally[R_INTERRUPTED]))
        print("[newsletter] campaign %s complete: %s" % (cid, tally))
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] campaign finish failed: %s" % exc)


def _sending_campaign_ids() -> List[str]:
    db = _db()
    if db is None:
        return []
    try:
        snap = db.collection(C_CAMPAIGNS).where("status", "==", CAMP_SENDING).limit(10).get()
        return [doc.id for doc in snap]
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] sending-campaign query failed: %s" % exc)
        return []


def _park_welcome(sub_id: str, status: str, why: str) -> None:
    """Take one welcome OUT of the queue without sending it.

    Used for records that can never receive a welcome in their current state:
    an unconfirmed signup (nothing is due until they click the link) and an
    address that unsubscribed before the welcome went out (they opted out).
    Both would otherwise stay welcomeEmailStatus="pending" forever, and a
    forever-pending row is re-read on every worker pass for the life of the
    server.
    """
    db = _db()
    if db is None:
        return
    try:
        db.collection(C_SUBS).document(sub_id).set(
            {"welcomeEmailStatus": status, "welcomeLeaseUntil": 0,
             "welcomeError": why, "updatedAt": _now()}, merge=True)
        _invalidate_subs_cache()
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] welcome park failed for %s: %s" % (sub_id, exc))


def _pending_welcome_ids(limit: int = 50) -> List[str]:
    """Ids of subscribers whose welcome email is genuinely due.

    The Firestore query can only ask one question (welcomeEmailStatus ==
    pending); asking it together with status == active would need a composite
    index, which is a manual console step this system deliberately does not
    depend on. So the status test happens here, on documents already read —
    and anything that can never send is parked on the spot rather than left to
    be re-read on the next pass, which is also what heals records written
    before welcomes were parked at signup.
    """
    db = _db()
    if db is None:
        return []
    try:
        snap = (db.collection(C_SUBS)
                .where("welcomeEmailStatus", "==", WELCOME_PENDING).limit(limit).get())
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] pending-welcome query failed: %s" % exc)
        return []
    due: List[str] = []
    for doc in snap:
        d = doc.to_dict() or {}
        status = d.get("status")
        if status == STATUS_ACTIVE:
            due.append(doc.id)
        elif status == STATUS_PENDING:
            _park_welcome(doc.id, WELCOME_AWAITING,
                          "Waiting for the person to confirm their email address.")
        else:
            _park_welcome(doc.id, WELCOME_SKIPPED,
                          "Unsubscribed before the welcome email was sent.")
    return due


_WORKER_IDLE_SEC = 20.0


def _worker_loop() -> None:
    """One daemon thread drives every outbound email.

    Why a thread and not "send it in the request": a campaign to a few thousand
    people takes minutes, and an HTTP handler that runs for minutes is a
    timeout, a browser retry, and — without the recipient documents — a second
    copy for everyone. The browser's job is to say "go"; this loop does the
    work and the campaign state in Firestore is what survives a restart.
    """
    # Give the process a moment to finish booting Firestore before the first
    # query, so a cold start does not log a spurious failure.
    time.sleep(5)
    print("[newsletter] send worker started")
    while True:
        try:
            did_work = False

            # 1) Welcome emails queued by this process, then any stragglers
            #    left pending by an earlier one.
            drained = 0
            while drained < 25:
                try:
                    sub_id = _welcome_q.get_nowait()
                except queue.Empty:
                    break
                drained += 1
                did_work = True
                _send_welcome_now(sub_id)
            if drained == 0:
                for sub_id in _pending_welcome_ids(25):
                    did_work = True
                    _send_welcome_now(sub_id)

            # 2) Campaigns.
            for cid in _sending_campaign_ids():
                if _process_campaign_batch(cid) > 0:
                    did_work = True

            if did_work:
                _worker_wake.set()          # loop straight round for the next batch
        except Exception as exc:  # noqa: BLE001 — the worker must never die
            print("[newsletter] worker error: %s" % exc)
            traceback.print_exc()
        _worker_wake.wait(timeout=_WORKER_IDLE_SEC)
        _worker_wake.clear()


def start_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    threading.Thread(target=_worker_loop, name="newsletter-worker", daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════════
#  CSV EXPORT
# ═══════════════════════════════════════════════════════════════════════════
# Excel, Numbers and Google Sheets all execute a cell that begins with = + - @
# (or a leading tab / CR, which they trim before deciding). A subscriber whose
# address is "=HYPERLINK(...)@x.com" would otherwise run a formula on the
# machine of whoever opens the export. Prefixing a single quote makes the cell
# literal text in all three.
_CSV_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: Any) -> str:
    s = "" if value is None else str(value)
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    if s and s[0] in _CSV_DANGEROUS:
        return "'" + s
    return s


def build_csv(rows: List[Dict[str, Any]]) -> str:
    """Subscriber export. Carries no unsubscribe tokens, no OAuth tokens, no
    Stripe ids and no credentials of any kind — only what the admin already
    sees on screen."""
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(["Email", "Status", "Source", "Subscribed (UTC)",
                "Last subscribed/reactivated (UTC)", "Unsubscribed (UTC)", "Welcome email"])
    for r in rows:
        w.writerow([
            _csv_safe(r.get("email")),
            _csv_safe(r.get("status")),
            _csv_safe(r.get("source")),
            _csv_safe(r.get("subscribedAtIso")),
            _csv_safe(r.get("resubscribedAtIso")),
            _csv_safe(r.get("unsubscribedAtIso")),
            _csv_safe(r.get("welcomeEmailStatus")),
        ])
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN ACTIONS
# ═══════════════════════════════════════════════════════════════════════════
def _filter_sort_page(rows: List[Dict[str, Any]], body: Dict[str, Any]) -> Dict[str, Any]:
    q = str(body.get("query") or "").strip().lower()[:200]
    status = str(body.get("status") or "all").strip().lower()
    sort = str(body.get("sort") or "recent").strip().lower()
    page = max(1, _int(body.get("page"), 1))
    per = min(200, max(10, _int(body.get("perPage"), 50)))

    out = [_public_subscriber(r) for r in rows]
    if status in (STATUS_ACTIVE, STATUS_UNSUB, STATUS_PENDING):
        out = [r for r in out if r["status"] == status]
    if q:
        out = [r for r in out if q in r["email"]]

    reverse = sort in ("recent", "email_desc")
    if sort in ("recent", "oldest"):
        out.sort(key=lambda r: (r["subscribedAt"], r["email"]), reverse=reverse)
    elif sort in ("recent_activity",):
        out.sort(key=lambda r: (max(r["subscribedAt"], r["resubscribedAt"]), r["email"]), reverse=True)
    else:
        out.sort(key=lambda r: r["email"], reverse=reverse)

    total = len(out)
    start = (page - 1) * per
    return {
        "rows": out[start:start + per],
        "total": total,
        "page": page,
        "perPage": per,
        "pages": max(1, (total + per - 1) // per),
    }


def _admin_add(body: Dict[str, Any], admin: str) -> Dict[str, Any]:
    email = nl_email.normalize_email(body.get("email"))
    if not email:
        return {"ok": False, "error": "That is not a valid email address."}
    if body.get("consent") is not True:
        return {"ok": False, "error": "Confirm that this person gave permission to receive updates."}
    note = str(body.get("consentNote") or "").strip()[:200]
    try:
        out = _subscribe(email, source=SOURCE_MANUAL, consent_note=note or "Added by admin; "
                                                                          "permission confirmed.")
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] manual add failed: %s" % exc)
        return {"ok": False, "error": "Could not save that subscriber. Please try again."}
    _invalidate_subs_cache()
    result = out.get("result")
    sub = out.get("subscriber") or {}
    sub_id = sub.get("id") or _subscriber_id(email)
    if result == "already_active":
        return {"ok": False, "error": "%s is already an active subscriber." % email,
                "duplicate": True}
    audit("subscriber_added_manual", admin=admin, subscriber_id=sub_id,
          summary="%s added manually (%s)" % (email, result))
    if out.get("sendWelcome"):
        _queue_welcome(sub_id)
        _wake_worker()
    return {"ok": True, "result": result, "email": email}


def _admin_test_send(body: Dict[str, Any], admin: str) -> Dict[str, Any]:
    """Send a preview to the admin address, and ONLY the admin address.

    The recipient is not taken from the request at all — a test send that
    accepts a destination is a spam relay wearing an admin login."""
    cid = str(body.get("id") or "").strip()[:64]
    camp = _get_campaign(cid) if cid else None
    if not camp:
        # Allow testing unsaved composer content too, so Tim can iterate
        # without saving a draft for every tweak.
        camp = {
            "subject": str(body.get("subject") or "").strip()[:MAX_SUBJECT],
            "previewText": str(body.get("previewText") or "").strip()[:MAX_PREVIEW],
            "contentHtml": nl_email.sanitize_html(str(body.get("contentHtml") or "")[:MAX_CONTENT]),
        }
    if not str(camp.get("subject") or "").strip():
        return {"ok": False, "error": "Add a subject before sending a test."}

    gm = nl_email.connection_status()
    if not gm.get("connected"):
        return {"ok": False, "error": "Email sending is not connected (%s): %s"
                                      % (gm.get("transportLabel") or "no transport",
                                         gm.get("error") or "unknown")}

    # A test carries NO real unsubscribe token: it is not addressed to a
    # subscriber, and putting a live token in it would let anyone with the test
    # email unsubscribe that person.
    msg = _render_campaign(camp, unsub="", is_test=True)
    try:
        nl_email.send_email(to_email=admin_email(), subject=msg["subject"],
                            html_body=msg["html"], text_body=msg["text"], is_bulk=False)
    except nl_email.SendError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] test send failed: %s" % exc)
        return {"ok": False, "error": "The test email could not be sent."}
    audit("test_email_sent", admin=admin, campaign_id=cid,
          summary="Test email sent to %s" % admin_email())
    return {"ok": True, "sentTo": admin_email()}


def _admin_dashboard() -> Dict[str, Any]:
    rows, truncated = _all_subscribers()
    pub = [_public_subscriber(r) for r in rows]
    active = [r for r in pub if r["status"] == STATUS_ACTIVE]
    # Somebody who signed up on the website and has not clicked the link in
    # their inbox yet is PENDING, and is neither of the other two things.
    # Counting them as unsubscribed — which "everything that is not active"
    # quietly does — tells the admin that a person who has just joined has
    # opted out, which is the exact opposite of what happened.
    waiting = [r for r in pub if r["status"] == STATUS_PENDING]
    newest = max((r["subscribedAt"] for r in pub), default=0)
    newest_row = next((r for r in sorted(pub, key=lambda x: x["subscribedAt"], reverse=True)), None)

    db = _db()
    sent_campaigns = 0
    current: Optional[Dict[str, Any]] = None
    recent: List[Dict[str, Any]] = []
    if db is not None:
        try:
            for doc in db.collection(C_CAMPAIGNS).limit(500).get():
                d = doc.to_dict() or {}
                d["id"] = doc.id
                if d.get("status") == CAMP_SENT:
                    sent_campaigns += 1
                if d.get("status") == CAMP_SENDING and current is None:
                    current = _campaign_public(d)
                recent.append(d)
        except Exception as exc:  # noqa: BLE001
            print("[newsletter] dashboard campaign scan failed: %s" % exc)
    recent.sort(key=lambda d: _int(d.get("createdAt")), reverse=True)

    pending_welcome = sum(1 for r in rows
                          if r.get("welcomeEmailStatus") == WELCOME_PENDING)
    failed_welcome = sum(1 for r in rows if r.get("welcomeEmailStatus") == "failed")

    return {
        "activeCount": len(active),
        "pendingCount": len(waiting),
        "unsubscribedCount": len(pub) - len(active) - len(waiting),
        "totalCount": len(pub),
        "truncated": truncated,
        "mostRecentSignup": newest_row["email"] if newest_row else "",
        "mostRecentSignupAtIso": _iso(newest),
        "newslettersSent": sent_campaigns,
        "currentCampaign": current,
        "pendingWelcome": pending_welcome,
        "failedWelcome": failed_welcome,
        "recentCampaigns": [_campaign_public(d) for d in recent[:5]],
        "sendsUsedToday": nl_email.sends_used_today(),
        "dailyCap": nl_email.daily_send_cap(),
    }


def _stripe_status() -> Dict[str, Any]:
    """What can be HONESTLY said about the Stripe side.

    We can prove that this server holds a signing secret and report the last
    checkout whose custom-field labels we saw. We cannot prove the Stripe
    Dashboard is pointed at us or that the endpoint is enabled, so this never
    claims "connected" — it reports observations and lets the page say what
    they mean. Claiming a configuration that was never verified is exactly the
    kind of thing that hides a broken signup for a month.
    """
    out: Dict[str, Any] = {
        "webhookSecretSet": bool(os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()),
        "acceptedLabels": accepted_labels(),
        "lastSeenLabels": [],
        "lastSeenAtIso": "",
        "lastSeenMatched": None,
        "signupsFromStripe": 0,
    }
    db = _db()
    if db is None:
        return out
    try:
        snap = db.collection(C_META).document("stripeFieldLabels").get()
        if snap.exists:
            d = snap.to_dict() or {}
            out["lastSeenLabels"] = list(d.get("lastSeen") or [])
            out["lastSeenAtIso"] = _iso(d.get("lastSeenAt"))
            out["lastSeenMatched"] = d.get("lastSeenMatched")
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] stripe status read failed: %s" % exc)
    rows, _ = _all_subscribers()
    out["signupsFromStripe"] = sum(1 for r in rows if r.get("source") == SOURCE_STRIPE)
    return out


def _admin_audit(body: Dict[str, Any]) -> Dict[str, Any]:
    db = _db()
    if db is None:
        return {"rows": []}
    limit = min(300, max(10, _int(body.get("limit"), 100)))
    rows: List[Dict[str, Any]] = []
    try:
        snap = db.collection(C_AUDIT).limit(1500).get()
        for doc in snap:
            d = doc.to_dict() or {}
            rows.append({
                "action": d.get("action") or "",
                "atIso": d.get("atIso") or _iso(d.get("at")),
                "at": _int(d.get("at")),
                "admin": d.get("admin") or "",
                "subscriberId": d.get("subscriberId") or "",
                "campaignId": d.get("campaignId") or "",
                "summary": d.get("summary") or "",
                "correlationId": d.get("correlationId") or "",
            })
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] audit read failed: %s" % exc)
    rows.sort(key=lambda r: r["at"], reverse=True)
    return {"rows": rows[:limit]}


def _admin_campaign_progress(cid: str) -> Dict[str, Any]:
    camp = _get_campaign(cid)
    if not camp:
        return {"ok": False, "error": "not_found"}
    db = _db()
    tally = {R_PENDING: 0, R_SENDING: 0, R_SENT: 0, R_FAILED: 0, R_SKIPPED: 0, R_INTERRUPTED: 0}
    failures: List[Dict[str, Any]] = []
    if db is not None:
        try:
            cref = db.collection(C_CAMPAIGNS).document(cid)
            for doc in cref.collection(C_RECIPIENTS).limit(MAX_SUBSCRIBER_SCAN).get():
                d = doc.to_dict() or {}
                st = d.get("status") or R_PENDING
                if st in tally:
                    tally[st] += 1
                if st in (R_FAILED, R_INTERRUPTED) and len(failures) < 50:
                    failures.append({
                        "email": d.get("email") or "",
                        "status": st,
                        "attempts": _int(d.get("attempts")),
                        "error": d.get("lastErrorCategory") or "",
                    })
        except Exception as exc:  # noqa: BLE001
            print("[newsletter] progress scan failed: %s" % exc)
    pub = _campaign_public(camp, with_content=True)
    total = sum(tally.values()) or pub["intendedRecipients"]
    done = tally[R_SENT] + tally[R_FAILED] + tally[R_SKIPPED] + tally[R_INTERRUPTED]
    pub.update({
        "tally": tally,
        "totalRecipients": total,
        "percent": round(100.0 * done / total, 1) if total else 0.0,
        "failures": failures,
        "canRetry": (tally[R_FAILED] + tally[R_INTERRUPTED]) > 0,
    })
    return {"ok": True, "campaign": pub}


def _admin_retry_failed(cid: str, admin: str) -> Dict[str, Any]:
    """Put failed / interrupted recipients back in the queue — explicitly, on a
    human's decision. Recipients already marked SENT are never touched, so a
    retry can never produce a second copy for someone who got the first."""
    db = _db()
    if db is None:
        return {"ok": False, "error": "unavailable"}
    camp = _get_campaign(cid)
    if not camp:
        return {"ok": False, "error": "not_found"}
    cref = db.collection(C_CAMPAIGNS).document(cid)
    moved = 0
    try:
        for st in (R_FAILED, R_INTERRUPTED):
            for doc in cref.collection(C_RECIPIENTS).where("status", "==", st).limit(2000).get():
                doc.reference.set({"status": R_PENDING, "attempts": 0, "leaseUntil": 0,
                                   "updatedAt": _now()}, merge=True)
                moved += 1
        if moved:
            cref.set({"status": CAMP_SENDING, "failedCount": 0, "interruptedCount": 0,
                      "updatedAt": _now()}, merge=True)
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] retry failed: %s" % exc)
        return {"ok": False, "error": "Could not requeue those recipients."}
    audit("campaign_started", admin=admin, campaign_id=cid,
          summary="Retry requeued %d recipient(s)" % moved)
    _wake_worker()
    return {"ok": True, "requeued": moved}


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP — ADMIN API
# ═══════════════════════════════════════════════════════════════════════════
def _deny(handler, status: int = 403) -> bool:
    handler._send_json({"ok": False, "error": "unauthorized"}, status=status)
    return True


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    """POST /api/newsletter/... — every admin action, plus the JSON unsubscribe.

    Returns True when this module handled the request.
    """
    path = parsed.path
    if not path.startswith("/api/newsletter/"):
        return False
    action = path[len("/api/newsletter/"):].strip("/")
    client = _client_key(handler)

    # ── public: website signup (no login, by design) ─────────────────────
    if action == "subscribe":
        if not _rate_ok("public_signup", client):
            handler._send_json({"ok": False,
                                "error": "Too many signups from here. Try again shortly."},
                               status=429)
            return True
        email = nl_email.normalize_email(body.get("email"))
        if not email:
            handler._send_json({"ok": False, "error": "That doesn't look like a valid "
                                                      "email address."}, status=400)
            return True
        if not unsub_secret_configured():
            handler._send_json({"ok": False, "error": "Signups are temporarily "
                                                      "unavailable."}, status=503)
            return True
        try:
            out = request_subscription(email)
        except Exception as exc:  # noqa: BLE001
            print("[newsletter] public signup failed: %s" % exc)
            handler._send_json({"ok": False, "error": "Could not sign you up just now. "
                                                      "Please try again."}, status=500)
            return True

        result = out.get("result", "")
        sub = out.get("subscriber") or {}
        # An address ALREADY on the list gets no second confirmation and no
        # different answer: the reply below is identical either way, so this
        # form cannot be used to test whether somebody is a subscriber.
        if result in ("created", "pending", "already_pending"):
            _send_confirmation(sub)
        handler._send_json({"ok": True, "message":
                            "Almost there! Check your email and click the confirmation "
                            "link to finish joining."})
        return True

    # ── public: confirm a website signup ─────────────────────────────────
    if action == "confirm":
        if not _rate_ok("unsubscribe", client):
            handler._send_json({"ok": False, "error": "too_many_requests"}, status=429)
            return True
        res = confirm_with_token(body.get("token") if isinstance(body.get("token"), str) else "")
        handler._send_json({"ok": True, "confirmed": bool(res.get("known")),
                            "result": res.get("result", "")})
        return True

    # ── public: unsubscribe (no login, by design) ────────────────────────
    if action == "unsubscribe":
        if not _rate_ok("unsubscribe", client):
            handler._send_json({"ok": False, "error": "too_many_requests"}, status=429)
            return True
        token = body.get("token") if isinstance(body.get("token"), str) else ""
        res = unsubscribe_with_token(token)
        # Always the same shape whether or not the token matched: the reply
        # must not reveal whether an address is in the database.
        handler._send_json({"ok": True, "unsubscribed": True})
        if res.get("known"):
            print("[newsletter] unsubscribe applied (%s)" % res.get("result"))
        return True

    # ── everything below is admin-only ───────────────────────────────────
    if not _rate_ok("admin_api", client):
        handler._send_json({"ok": False, "error": "too_many_requests"}, status=429)
        return True

    claims = _admin_claims(body, handler)
    if claims is None:
        if not _rate_ok("admin_auth", client):
            handler._send_json({"ok": False, "error": "too_many_requests"}, status=429)
            return True
        # One audit line per rejected attempt, with no token and no email from
        # the request (both are attacker-controlled and one of them is a
        # credential).
        audit("unauthorized_admin_access", summary="Rejected %s from %s" % (action[:40], client))
        return _deny(handler)

    admin = str(claims.get("email") or "")

    try:
        if action == "dashboard":
            handler._send_json({"ok": True, **_admin_dashboard()})
            return True

        if action == "subscribers":
            rows, truncated = _all_subscribers()
            page = _filter_sort_page(rows, body)
            c = counts()
            handler._send_json({"ok": True, **page, "counts": c, "truncated": truncated})
            return True

        if action == "subscriber-add":
            handler._send_json(_admin_add(body, admin))
            _invalidate_subs_cache()
            return True

        if action == "subscriber-unsubscribe":
            sid = str(body.get("id") or "").strip()[:64]
            if not sid:
                handler._send_json({"ok": False, "error": "Missing subscriber."}, status=400)
                return True
            out = _unsubscribe_by_id(sid, actor=admin, reason="admin action")
            _invalidate_subs_cache()
            handler._send_json({"ok": bool(out.get("ok")), "result": out.get("result", "")})
            return True

        if action == "subscriber-resend-confirmation":
            sid = str(body.get("id") or "").strip()[:64]
            if not sid:
                handler._send_json({"ok": False, "error": "Missing subscriber."}, status=400)
                return True
            handler._send_json(resend_confirmation(sid, actor=admin))
            return True

        if action == "subscriber-confirm":
            sid = str(body.get("id") or "").strip()[:64]
            if not sid:
                handler._send_json({"ok": False, "error": "Missing subscriber."}, status=400)
                return True
            out = confirm_by_admin(sid, actor=admin,
                                   reason=str(body.get("reason") or ""))
            _invalidate_subs_cache()
            handler._send_json(out)
            return True

        if action == "subscriber-reactivate":
            sid = str(body.get("id") or "").strip()[:64]
            reason = str(body.get("reason") or "").strip()[:200]
            if not sid:
                handler._send_json({"ok": False, "error": "Missing subscriber."}, status=400)
                return True
            if not reason:
                handler._send_json({"ok": False, "error": "Give a reason for reactivating this "
                                                          "person (they must have asked to rejoin)."},
                                   status=400)
                return True
            db = _db()
            snap = db.collection(C_SUBS).document(sid).get() if db is not None else None
            if snap is None or not snap.exists:
                handler._send_json({"ok": False, "error": "not_found"}, status=404)
                return True
            email = str((snap.to_dict() or {}).get("emailLower") or "")
            out = _subscribe(email, source=SOURCE_MANUAL,
                             consent_note="Reactivated by admin: " + reason)
            _invalidate_subs_cache()
            if out.get("result") in ("reactivated", "created"):
                audit("subscriber_reactivated", admin=admin, subscriber_id=sid,
                      summary="Reactivated: %s" % reason)
                if out.get("sendWelcome"):
                    _queue_welcome(sid)
                    _wake_worker()
            handler._send_json({"ok": True, "result": out.get("result", "")})
            return True

        if action == "export":
            if not _rate_ok("export", client):
                handler._send_json({"ok": False, "error": "too_many_requests"}, status=429)
                return True
            rows, _ = _all_subscribers(force=True)
            # The export honours the SAME status filter and search the list is
            # showing, so "export" always means "export what I am looking at".
            filtered = [_public_subscriber(r) for r in rows]
            status = str(body.get("status") or "all").strip().lower()
            if status in (STATUS_ACTIVE, STATUS_UNSUB, STATUS_PENDING):
                filtered = [r for r in filtered if r["status"] == status]
            q = str(body.get("query") or "").strip().lower()[:200]
            if q:
                filtered = [r for r in filtered if q in r["email"]]
            filtered.sort(key=lambda r: r["subscribedAt"], reverse=True)
            csv_text = build_csv(filtered)
            audit("csv_exported", admin=admin,
                  summary="Exported %d subscriber row(s), filter=%s" % (len(filtered), status))
            handler._send_json({"ok": True, "csv": csv_text, "rows": len(filtered),
                                "filename": "cc-newsletter-subscribers-%s.csv"
                                            % time.strftime("%Y-%m-%d", time.gmtime())})
            return True

        if action == "campaign-save":
            handler._send_json(_save_draft(
                cid=str(body.get("id") or "").strip()[:64],
                subject=body.get("subject"), preview=body.get("previewText"),
                content_html=body.get("contentHtml"), admin=admin))
            return True

        if action == "campaign-list":
            db = _db()
            out: List[Dict[str, Any]] = []
            if db is not None:
                try:
                    for doc in db.collection(C_CAMPAIGNS).limit(500).get():
                        d = doc.to_dict() or {}
                        d["id"] = doc.id
                        out.append(_campaign_public(d))
                except Exception as exc:  # noqa: BLE001
                    print("[newsletter] campaign list failed: %s" % exc)
            out.sort(key=lambda c: c["createdAt"], reverse=True)
            handler._send_json({"ok": True, "campaigns": out})
            return True

        if action == "campaign-get":
            camp = _get_campaign(str(body.get("id") or "").strip()[:64])
            if not camp:
                handler._send_json({"ok": False, "error": "not_found"}, status=404)
                return True
            handler._send_json({"ok": True, "campaign": _campaign_public(camp, with_content=True)})
            return True

        if action == "campaign-duplicate":
            camp = _get_campaign(str(body.get("id") or "").strip()[:64])
            if not camp:
                handler._send_json({"ok": False, "error": "not_found"}, status=404)
                return True
            res = _save_draft(cid="", subject="Copy of " + str(camp.get("subject") or "")[:180],
                              preview=camp.get("previewText"),
                              content_html=camp.get("contentHtml"), admin=admin)
            handler._send_json(res)
            return True

        if action == "campaign-preview":
            cid = str(body.get("id") or "").strip()[:64]
            camp = _get_campaign(cid) if cid else None
            if not camp:
                camp = {"subject": body.get("subject"), "previewText": body.get("previewText"),
                        "contentHtml": body.get("contentHtml")}
            # A preview shows a REPRESENTATIVE link, never a live token.
            sample = "%s/newsletter/unsubscribe/example-preview-link" % nl_email.app_base_url()
            msg = _render_campaign(camp, unsub=sample, is_test=False)
            handler._send_json({"ok": True, "subject": msg["subject"],
                                "html": msg["html"], "text": msg["text"]})
            return True

        if action == "test-send":
            if not _rate_ok("test_send", client):
                handler._send_json({"ok": False, "error": "You have sent several tests in a "
                                                          "row — give it a minute."}, status=429)
                return True
            handler._send_json(_admin_test_send(body, admin))
            return True

        if action == "campaign-start":
            if not _rate_ok("campaign", client):
                handler._send_json({"ok": False, "error": "too_many_requests"}, status=429)
                return True
            handler._send_json(_start_campaign(
                str(body.get("id") or "").strip()[:64], admin=admin,
                confirm=str(body.get("confirm") or "")))
            return True

        if action == "campaign-progress":
            handler._send_json(_admin_campaign_progress(str(body.get("id") or "").strip()[:64]))
            return True

        if action == "campaign-retry":
            if not _rate_ok("campaign", client):
                handler._send_json({"ok": False, "error": "too_many_requests"}, status=429)
                return True
            handler._send_json(_admin_retry_failed(str(body.get("id") or "").strip()[:64], admin))
            return True

        if action == "audit":
            handler._send_json({"ok": True, **_admin_audit(body)})
            return True

        if action == "settings":
            gm = nl_email.connection_status()
            audit("connection_check", admin=admin,
                  summary="Checked %s: %s" % (gm.get("transportLabel") or "no transport",
                                              "ok" if gm.get("connected") else "not connected"))
            handler._send_json({
                "ok": True,
                "gmail": gm,
                "stripe": _stripe_status(),
                "unsubscribeSecretSet": unsub_secret_configured(),
                "adminEmail": admin_email(),
                "adminEmails": nl_email.admin_emails(),
                "appBaseUrl": nl_email.app_base_url(),
                "siteUrl": nl_email.site_url(),
                "privacyUrl": nl_email.privacy_url(),
                "appVersion": _app_version,
            })
            return True

        if action == "whoami":
            handler._send_json({"ok": True, "email": admin})
            return True

        handler._send_json({"ok": False, "error": "unknown_action"}, status=404)
        return True

    except Exception as exc:  # noqa: BLE001
        # Never leak a stack trace to the browser; the full one goes to the
        # server log where only Tim can read it.
        print("[newsletter] admin action %s failed: %s" % (action, exc))
        traceback.print_exc()
        handler._send_json({"ok": False, "error": "Something went wrong on the server."},
                           status=500)
        return True


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP — PUBLIC PAGES
# ═══════════════════════════════════════════════════════════════════════════
def _unsub_page_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "multiplayer", "client", "unsubscribe.html")


def _client_page(name: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "multiplayer", "client", name)


def handle_get(handler, parsed) -> bool:
    """GET /newsletter/unsubscribe/<token> — the public confirmation page.

    Serves the page for ANY token shape. It does not apply the unsubscribe on
    GET, because mail scanners and link-preview bots fetch every URL in an
    email and would otherwise unsubscribe people who never clicked. The page
    posts back to apply it — and it auto-posts on load, so a human still sees
    one click do the whole job.
    """
    path = parsed.path
    # The public signup page, and the page the confirmation link opens.
    if path.rstrip("/") in ("/newsletter/join", "/join", "/newsletter/signup"):
        try:
            with open(_client_page("join.html"), "rb") as f:
                handler._emit_html(f.read())
        except OSError:
            handler._send_json({"ok": False, "error": "join page missing"}, status=404)
        return True
    if path.startswith("/newsletter/confirm"):
        try:
            with open(_client_page("confirm.html"), "rb") as f:
                handler._emit_html(f.read())
        except OSError:
            handler._send_json({"ok": False, "error": "confirm page missing"}, status=404)
        return True
    if not path.startswith("/newsletter/unsubscribe"):
        return False
    if not _rate_ok("unsubscribe", _client_key(handler)):
        handler._send_json({"ok": False, "error": "too_many_requests"}, status=429)
        return True
    try:
        with open(_unsub_page_path(), "rb") as f:
            raw = f.read()
    except OSError:
        handler._send_json({"ok": False, "error": "unsubscribe page missing"}, status=404)
        return True
    handler._emit_html(raw)
    return True


def handle_one_click_post(handler, parsed) -> bool:
    """POST /newsletter/unsubscribe/<token> — RFC 8058 one-click.

    Gmail and Outlook POST here from their own servers when the reader uses the
    mail client's native Unsubscribe button. The body is
    `List-Unsubscribe=One-Click` as form data, NOT JSON, which is why this is
    dispatched before the JSON body reader in do_POST — reading it as JSON
    would consume the stream and fail.
    """
    path = parsed.path
    if not path.startswith("/newsletter/unsubscribe/"):
        return False
    token = path[len("/newsletter/unsubscribe/"):].strip("/")
    if not _rate_ok("unsubscribe", _client_key(handler)):
        handler._send_json({"ok": False, "error": "too_many_requests"}, status=429)
        return True
    # Drain the small form body so the connection stays sane.
    try:
        size = int(handler.headers.get("Content-Length", "0") or 0)
        if 0 < size <= 4096:
            handler.rfile.read(size)
    except Exception:  # noqa: BLE001
        pass
    try:
        unsubscribe_with_token(token)
    except Exception as exc:  # noqa: BLE001
        print("[newsletter] one-click unsubscribe failed: %s" % exc)
    # RFC 8058 wants a 2xx with no useful body.
    handler._send_json({"ok": True})
    return True


# ═══════════════════════════════════════════════════════════════════════════
#  INIT
# ═══════════════════════════════════════════════════════════════════════════
def init(*, get_firestore, verify_token, app_version: str = "") -> None:
    global _get_firestore, _verify_token, _app_version
    _get_firestore = get_firestore
    _verify_token = verify_token
    _app_version = str(app_version or "")
    start_worker()
    print("[newsletter] ready (admins=%s, transport=%s, sanitizer=%s, unsub secret=%s)"
          % (",".join(nl_email.admin_emails()), nl_email.transport_label(), nl_email.sanitizer_name(),
             "set" if unsub_secret_configured() else "MISSING"))
