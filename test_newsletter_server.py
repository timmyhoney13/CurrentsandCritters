"""Tests for the newsletter system (newsletter_server.py + newsletter_email.py).

Ordered by how much damage the bug would do:

 1. CONSENT. Who becomes a subscriber, and, much more important, who does
    NOT. A bug that subscribes someone who did not ask is the one that gets a
    sending domain blocked, so most of section 1 is about the paths that must
    produce NOTHING.

 2. IDEMPOTENCY. Stripe retries. Browsers double-click. Render restarts
    mid-campaign. Every one of those is asserted to be a no-op.

 3. SANITISING + INJECTION. The admin is trusted; the HTML they paste from a
    web page is not. Also CSV formula injection and email header injection.

 4. UNSUBSCRIBE. Tokens must be unguessable, un-modifiable, idempotent, and
    must not tell a prober whether an address is on the list.

 5. AUTHORISATION. One account, verified server-side, per route.

Run:  python3 test_newsletter_server.py
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import socket
import sys
import threading
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Environment MUST be set before the modules read it ─────────────────────
os.environ.setdefault("NEWSLETTER_UNSUBSCRIBE_SECRET", "test-secret-do-not-use-in-production")
os.environ.setdefault("ADMIN_EMAIL", "timothy.honey@beardedsealstudios.com")
os.environ.setdefault("NEWSLETTER_FROM_EMAIL", "timothy.honey@beardedsealstudios.com")
os.environ.setdefault("APP_BASE_URL", "https://play.currentsandcritters.com")
os.environ.setdefault("CURRENTS_AND_CRITTERS_URL", "https://currentsandcritters.com")


# ══════════════════════════════════════════════════════════════════════════
#  A fake firebase_admin, installed before newsletter_server imports it.
#  The real package is not a dependency of the test run; the modules only ever
#  touch firestore.transactional / Increment / SERVER_TIMESTAMP.
# ══════════════════════════════════════════════════════════════════════════
class _Increment:
    def __init__(self, by):
        self.by = by


def _install_fake_firebase():
    fs = types.ModuleType("firebase_admin.firestore")
    fs.transactional = lambda fn: fn          # the fake runs the body once
    fs.SERVER_TIMESTAMP = "__SERVER_TIMESTAMP__"
    fs.Increment = _Increment
    fs.ArrayUnion = lambda vals: {"__arrayUnion": vals}
    fa = types.ModuleType("firebase_admin")
    fa.firestore = fs
    sys.modules["firebase_admin"] = fa
    sys.modules["firebase_admin.firestore"] = fs


_install_fake_firebase()

import newsletter_email as ne      # noqa: E402
import newsletter_server as ns     # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
#  In-memory Firestore (documents, subcollections, ==-queries, batches)
# ══════════════════════════════════════════════════════════════════════════
def _deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, _Increment):
            cur = dst.get(k)
            dst[k] = (cur if isinstance(cur, int) else 0) + v.by
        elif isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def _resolve(data):
    """Turn Increment sentinels into plain numbers on a non-merge write."""
    out = {}
    for k, v in data.items():
        out[k] = v.by if isinstance(v, _Increment) else v
    return out


class FakeSnap:
    def __init__(self, doc_id, data, ref=None):
        self.id = doc_id
        self._data = data
        self.exists = data is not None
        self.reference = ref

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class AlreadyExists(Exception):
    """Stands in for google.api_core.exceptions.AlreadyExists. Matched by NAME
    in newsletter_server, so the name here is the contract."""


class FakeDoc:
    def __init__(self, coll, doc_id):
        self._coll = coll
        self.id = doc_id

    def get(self, transaction=None):
        return FakeSnap(self.id, self._coll._docs.get(self.id), ref=self)

    def set(self, data, merge=False):
        cur = self._coll._docs.get(self.id)
        if merge and isinstance(cur, dict):
            _deep_merge(cur, copy.deepcopy(data))
        else:
            self._coll._docs[self.id] = _resolve(copy.deepcopy(data))

    def create(self, data):
        """Write ONLY if the document does not exist, like the real client.

        The fake did not have this at all, so the single-write signup fast path
        silently fell through to the transaction in every test and would have
        shipped completely unexercised.
        """
        if self.id in self._coll._docs:
            raise AlreadyExists("document already exists: " + self.id)
        self._coll._docs[self.id] = _resolve(copy.deepcopy(data))

    def collection(self, name):
        return self._coll._db.collection(self._coll.name + "/" + self.id + "/" + name)


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def where(self, field, op, value):
        assert op == "=="
        return _Query([r for r in self._rows if (r.to_dict() or {}).get(field) == value])

    def limit(self, n):
        return _Query(self._rows[:n])

    def get(self):
        return list(self._rows)

    def stream(self):
        return list(self._rows)


class FakeColl:
    _auto = [0]

    def __init__(self, db, name):
        self._db = db
        self.name = name
        self._docs = {}

    def document(self, doc_id=None):
        if doc_id is None:
            FakeColl._auto[0] += 1
            doc_id = "auto%05d" % FakeColl._auto[0]
        return FakeDoc(self, doc_id)

    def _all(self):
        return [FakeSnap(k, v, ref=FakeDoc(self, k)) for k, v in self._docs.items()]

    def where(self, field, op, value):
        return _Query(self._all()).where(field, op, value)

    def limit(self, n):
        return _Query(self._all()).limit(n)

    def get(self):
        return self._all()

    def stream(self):
        return self._all()


class FakeTxn:
    def __init__(self, db):
        self._db = db

    def set(self, ref, data, merge=False):
        ref.set(data, merge=merge)


class FakeBatch:
    def __init__(self):
        self._ops = []

    def set(self, ref, data, merge=False):
        self._ops.append((ref, data, merge))

    def commit(self):
        for ref, data, merge in self._ops:
            ref.set(data, merge=merge)
        self._ops = []


class FakeDb:
    def __init__(self):
        self._colls = {}

    def collection(self, name):
        if name not in self._colls:
            self._colls[name] = FakeColl(self, name)
        return self._colls[name]

    def transaction(self):
        return FakeTxn(self)

    def batch(self):
        return FakeBatch()


# ══════════════════════════════════════════════════════════════════════════
#  Harness
# ══════════════════════════════════════════════════════════════════════════
ADMIN = "timothy.honey@beardedsealstudios.com"


class SentBox:
    """Stands in for Gmail. Records every message instead of sending it."""

    def __init__(self):
        self.messages = []
        self.fail_with = None
        self.fail_times = 0

    def send(self, **kw):
        if self.fail_with is not None and self.fail_times != 0:
            if self.fail_times > 0:
                self.fail_times -= 1
            raise self.fail_with
        self.messages.append(kw)
        return {"messageId": "<test%d@x>" % len(self.messages), "gmailId": "g%d" % len(self.messages)}

    def to(self, addr):
        return [m for m in self.messages if m.get("to_email") == addr]


def fake_token(email, *, verified=True, uid="uid-admin"):
    """The verifier stand-in accepts a JSON-ish token string."""
    return "TOK|%s|%s|%s" % (uid, email, "1" if verified else "0")


def _verify(tok):
    if not isinstance(tok, str) or not tok.startswith("TOK|"):
        return None
    _, uid, email, ver = tok.split("|", 3)
    return {"uid": uid, "email": email, "email_verified": ver == "1"}


class Handler:
    """Minimal stand-in for the BaseHTTPRequestHandler the module talks to."""

    def __init__(self, ip="1.2.3.4"):
        self.headers = {"X-Forwarded-For": ip}
        self.client_address = (ip, 1234)
        self.replies = []
        self.html = None

    def _send_json(self, payload, status=200):
        self.replies.append((status, payload))

    def _emit_html(self, raw):
        self.html = raw

    @property
    def last(self):
        return self.replies[-1][1] if self.replies else None

    @property
    def status(self):
        return self.replies[-1][0] if self.replies else None


class Parsed:
    def __init__(self, path, query=""):
        self.path = path
        self.query = query


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        self.box = SentBox()
        # The real send worker is a daemon thread that would wake up mid-test,
        # drain the same queues these tests drain by hand and send against
        # whichever FakeDb happened to be current. Claiming it is already
        # started keeps every send in this file on the test's own thread.
        ns._worker_started = True
        ns.init(get_firestore=lambda: self.db, verify_token=_verify, app_version="test")
        self.clear_queues()
        ns._invalidate_subs_cache()
        ns._RL.clear()
        # Never touch the network.
        self._real_send = ne.send_email
        ne.send_email = lambda **kw: self.box.send(**kw)
        self._real_conn = ne.connection_status
        ne.connection_status = lambda: {
            "configured": True, "connected": True, "canSendAsSender": True,
            "senderVerified": True, "transport": "smtp",
            "transportLabel": "SMTP (smtp.example.com)",
            "senderEmail": ADMIN, "senderName": "Currents & Critters", "replyTo": ADMIN,
            "authorizedAs": ADMIN, "scopes": ["smtp.example.com:587 (starttls)"],
            "sanitizer": ne.sanitizer_name(), "dailyCap": 1200, "error": "", "setupHint": "",
        }

    def tearDown(self):
        ne.send_email = self._real_send
        ne.connection_status = self._real_conn

    # -- helpers ---------------------------------------------------------
    def stripe_event(self, email, *, event_id="evt_1", session_id="cs_test_1",
                     paid=True, label="Enter your email to get updates"):
        session = {
            "id": session_id,
            "payment_status": "paid" if paid else "unpaid",
            "custom_fields": ([] if label is None else [{
                "label": {"type": "custom", "custom": label},
                "type": "text", "text": {"value": email},
            }]),
        }
        return {"id": event_id, "type": "checkout.session.completed",
                "data": {"object": session}}, session

    def signup(self, email, **kw):
        ev, sess = self.stripe_event(email, **kw)
        out = ns.handle_stripe_session(ev, sess)
        self.drain()
        return out

    @contextlib.contextmanager
    def transport_configured(self):
        """Make nl_email.transport() report SMTP.

        Base already stubs send_email and connection_status, but the code now
        also asks "is a transport configured at all" straight from the
        environment, and answers that question FIRST because an unconfigured
        transport is the headline, not a detail. So a test about what happens
        DURING a send has to say that sending is set up.
        """
        keys = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD")
        saved = {k: os.environ.get(k) for k in keys}
        os.environ.update({"SMTP_HOST": "smtp.example.com",
                           "SMTP_USERNAME": "u", "SMTP_PASSWORD": "p"})
        try:
            yield
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def legacy_pending(self, email):
        """Create a record the way signups worked BEFORE the confirmation step
        was removed: pending, welcome parked, waiting on a click.

        Nothing in the product writes one of these any more, but Firestore is
        full of them and their confirmation links are permanent, so every path
        that reads one still has to work. This is the only way to build one.
        """
        out = ns._subscribe(email.lower(), source=ns.SOURCE_WEBSITE,
                            status=ns.STATUS_PENDING,
                            consent_note="Website signup; awaiting email confirmation.")
        ns._invalidate_subs_cache()
        return out["subscriber"]

    def clear_queues(self):
        import queue as _q
        for q in (ns._welcome_q, ns._confirm_q):
            while True:
                try:
                    q.get_nowait()
                except _q.Empty:
                    break

    def drain(self):
        """Run the worker's outbound pass synchronously: the confirmation
        emails a signup queued, then the welcomes."""
        import queue as _q
        ns._drain_confirmations(limit=1000)
        while True:
            try:
                sub_id = ns._welcome_q.get_nowait()
            except _q.Empty:
                break
            ns._send_welcome_now(sub_id)

    def subs(self):
        return self.db.collection(ns.C_SUBS)._docs

    def sub_for(self, email):
        return self.subs().get(ns._subscriber_id(email.lower()))

    def admin_post(self, action, payload=None, *, email=ADMIN, verified=True, ip="1.2.3.4"):
        h = Handler(ip)
        body = dict(payload or {})
        body["idToken"] = fake_token(email, verified=verified)
        ns.handle_post(h, Parsed("/api/newsletter/" + action), body)
        return h


# ══════════════════════════════════════════════════════════════════════════
#  1. CONSENT
# ══════════════════════════════════════════════════════════════════════════
class TestConsent(Base):
    def test_completed_checkout_with_email_subscribes(self):
        self.assertEqual(self.signup("Fan@Example.COM"), "created")
        sub = self.sub_for("fan@example.com")
        self.assertIsNotNone(sub)
        self.assertEqual(sub["status"], ns.STATUS_ACTIVE)
        self.assertEqual(sub["emailLower"], "fan@example.com", "stored lowercased")
        self.assertEqual(sub["source"], ns.SOURCE_STRIPE)
        self.assertEqual(sub["stripeSessionId"], "cs_test_1")
        self.assertEqual(sub["stripeEventId"], "evt_1")
        self.assertTrue(sub["unsubId"])
        self.assertEqual(sub["welcomeEmailStatus"], "sent")

    def test_blank_field_does_not_subscribe(self):
        self.assertEqual(self.signup(""), "no_signup")
        self.assertEqual(len(self.subs()), 0, "a blank optional field is not consent")

    def test_whitespace_only_field_does_not_subscribe(self):
        self.assertEqual(self.signup("   "), "no_signup")
        self.assertEqual(len(self.subs()), 0)

    def test_no_newsletter_field_at_all_does_not_subscribe(self):
        self.assertEqual(self.signup("x@y.com", label=None), "no_signup")
        self.assertEqual(len(self.subs()), 0, "paying is not consent")

    def test_a_different_field_does_not_subscribe(self):
        # The receipt/billing address is NOT a newsletter signup.
        self.assertEqual(self.signup("buyer@x.com", label="Billing email"), "no_signup")
        self.assertEqual(len(self.subs()), 0)

    def test_invalid_email_does_not_subscribe(self):
        for bad in ("notanemail", "a@b", "@x.com", "a b@x.com", "x@y..com"):
            self.assertEqual(self.signup(bad, session_id="cs_" + bad[:4],
                                         event_id="evt_" + bad[:4]), "no_signup", bad)
        self.assertEqual(len(self.subs()), 0)

    def test_unpaid_session_does_not_subscribe(self):
        self.assertEqual(self.signup("x@y.com", paid=False), "unpaid")
        self.assertEqual(len(self.subs()), 0,
                         "a checkout that was started but never paid is not a signup")

    def test_heuristic_label_still_requires_update_wording(self):
        self.assertTrue(ns._label_is_newsletter_field("Your email for game updates"))
        self.assertTrue(ns._label_is_newsletter_field("enter your email to get updates"))
        self.assertTrue(ns._label_is_newsletter_field("Enter your email to get updates:"))
        for no in ("Email", "Email address", "Billing email", "Contact email",
                   "Your name", "Shipping address"):
            self.assertFalse(ns._label_is_newsletter_field(no), no)

    def test_extra_label_from_env(self):
        os.environ["NEWSLETTER_FIELD_LABEL"] = "Join the mail thing|Second one"
        try:
            self.assertTrue(ns._label_is_newsletter_field("Join the mail thing"))
            self.assertTrue(ns._label_is_newsletter_field("Second one"))
        finally:
            del os.environ["NEWSLETTER_FIELD_LABEL"]


# ══════════════════════════════════════════════════════════════════════════
#  2. IDEMPOTENCY
# ══════════════════════════════════════════════════════════════════════════
class TestIdempotency(Base):
    def test_stripe_retry_of_same_event_is_a_noop(self):
        ev, sess = self.stripe_event("a@b.com")
        self.assertEqual(ns.handle_stripe_session(ev, sess), "created")
        self.drain()
        # Stripe redelivers the identical event.
        self.assertEqual(ns.handle_stripe_session(ev, sess), "duplicate_event")
        self.drain()
        self.assertEqual(len(self.subs()), 1)
        self.assertEqual(len(self.box.to("a@b.com")), 1, "exactly one welcome email")
        self.assertEqual(len(self.box.to(ADMIN)), 1, "exactly one owner notification")

    def test_second_purchase_by_active_subscriber_sends_nothing(self):
        self.signup("a@b.com", event_id="evt_1", session_id="cs_1")
        self.signup("a@b.com", event_id="evt_2", session_id="cs_2")
        self.assertEqual(len(self.subs()), 1, "no second subscriber record")
        self.assertEqual(len(self.box.to("a@b.com")), 1, "no second welcome email")
        self.assertEqual(len(self.box.to(ADMIN)), 1, "Tim is not told twice")

    def test_reactivation_after_unsubscribe(self):
        self.signup("a@b.com", event_id="evt_1", session_id="cs_1")
        first = dict(self.sub_for("a@b.com"))
        tok_before = ns.make_unsub_token(first)

        ns.unsubscribe_with_token(tok_before)
        self.assertEqual(self.sub_for("a@b.com")["status"], ns.STATUS_UNSUB)
        self.assertGreater(self.sub_for("a@b.com")["unsubscribedAt"], 0)

        # They deliberately enter their address again on a NEW paid checkout.
        self.assertEqual(self.signup("a@b.com", event_id="evt_2", session_id="cs_2"),
                         "reactivated")
        after = self.sub_for("a@b.com")
        self.assertEqual(after["status"], ns.STATUS_ACTIVE)
        self.assertEqual(after["unsubscribedAt"], 0)
        self.assertEqual(after["subscribedAt"], first["subscribedAt"], "original date preserved")
        self.assertGreaterEqual(after["resubscribedAt"], first["resubscribedAt"])
        self.assertEqual(len(self.subs()), 1, "same record, not a second one")
        self.assertEqual(len(self.box.to("a@b.com")), 2,
                         "welcome sent again, this was a fresh, deliberate opt-in")

        # A new token was minted and the OLD link no longer works.
        self.assertNotEqual(ns.make_unsub_token(after), tok_before)
        self.assertIsNone(ns.resolve_unsub_token(tok_before),
                          "the old unsubscribe link is retired by reactivation")

    def test_unsubscribe_is_idempotent(self):
        self.signup("a@b.com")
        tok = ns.make_unsub_token(self.sub_for("a@b.com"))
        first = ns.unsubscribe_with_token(tok)
        second = ns.unsubscribe_with_token(tok)
        self.assertEqual(first["result"], "unsubscribed")
        self.assertEqual(second["result"], "already_unsubscribed")
        self.assertTrue(second["ok"], "clicking the same link twice is not an error")

    def test_welcome_retried_on_transient_failure_then_sent_once(self):
        self.box.fail_with = ne.SendError("network", category="network", retryable=True)
        self.box.fail_times = 1
        ev, sess = self.stripe_event("a@b.com")
        ns.handle_stripe_session(ev, sess)
        self.drain()
        sub = self.sub_for("a@b.com")
        self.assertEqual(sub["welcomeEmailStatus"], "pending", "put back for another try")
        self.assertEqual(len(self.box.to("a@b.com")), 0)

        self.box.fail_with = None
        ns._send_welcome_now(sub["id"])
        self.assertEqual(self.sub_for("a@b.com")["welcomeEmailStatus"], "sent")
        self.assertEqual(len(self.box.to("a@b.com")), 1)

    def test_welcome_permanent_failure_is_recorded_not_retried_forever(self):
        self.box.fail_with = ne.SendError("bad", category="invalid_message", retryable=False)
        self.box.fail_times = -1
        ev, sess = self.stripe_event("a@b.com")
        ns.handle_stripe_session(ev, sess)
        self.drain()
        sub = self.sub_for("a@b.com")
        self.assertEqual(sub["welcomeEmailStatus"], "failed")
        self.assertTrue(sub["welcomeError"])
        # A failed welcome must not be silently re-attempted by the sweep.
        self.assertNotIn(sub["id"], ns._pending_welcome_ids())

    def test_subscriber_survives_a_firestore_failure_without_double_effects(self):
        # Firestore is down when the event arrives → nothing is written and the
        # hook reports an error rather than half-subscribing anyone.
        broken = FakeDb()

        def boom():
            raise RuntimeError("firestore down")

        ns.init(get_firestore=boom, verify_token=_verify)
        ev, sess = self.stripe_event("a@b.com")
        self.assertEqual(ns.handle_stripe_session(ev, sess), "error")
        # Recovered: the same event now subscribes exactly once.
        ns.init(get_firestore=lambda: self.db, verify_token=_verify)
        self.assertEqual(ns.handle_stripe_session(ev, sess), "created")
        self.drain()
        self.assertEqual(len(self.subs()), 1)


# ══════════════════════════════════════════════════════════════════════════
#  3. SANITISING + INJECTION
# ══════════════════════════════════════════════════════════════════════════
class TestSanitizing(unittest.TestCase):
    def test_scripts_and_handlers_are_removed(self):
        dirty = (
            '<h2>Hi</h2><script>alert(1)</script>'
            '<p onclick="steal()" onmouseover="x">text</p>'
            '<iframe src="https://evil.example"></iframe>'
            '<form action="/x"><input name="pw" type="password"></form>'
            '<object data="x"></object><embed src="y">'
            '<style>body{display:none}</style>'
            '<svg onload="alert(1)"></svg>'
            '<link rel="stylesheet" href="http://evil">'
            '<meta http-equiv="refresh" content="0;url=http://evil">'
        )
        out = ne.sanitize_html(dirty).lower()
        for bad in ("<script", "onclick", "onmouseover", "<iframe", "<form", "<input",
                    "<object", "<embed", "<style", "<svg", "onload", "<link", "<meta",
                    "alert(1)", "display:none"):
            self.assertNotIn(bad, out, bad)
        self.assertIn("<h2>hi</h2>", out, "safe content survives")

    def test_unsafe_url_schemes_are_dropped(self):
        for bad in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<script>",
                    "vbscript:x", "file:///etc/passwd", "//evil.example/x"):
            out = ne.sanitize_html('<a href="%s">click</a>' % bad)
            self.assertNotIn("href", out, bad)
            self.assertIn("click", out, "the words survive even when the link does not")

    def test_http_images_rejected_https_kept(self):
        self.assertNotIn("<img", ne.sanitize_html('<img src="http://x/a.png">'))
        self.assertIn("<img", ne.sanitize_html('<img src="https://x/a.png" alt="a">'))

    def test_only_namespaced_classes_survive(self):
        out = ne.sanitize_html('<div class="cc-btn evil-class other">x</div>')
        self.assertIn('class="cc-btn"', out)
        self.assertNotIn("evil-class", out)

    def test_links_get_noopener(self):
        out = ne.sanitize_html('<a href="https://x.com">y</a>')
        self.assertIn('rel="noopener noreferrer"', out)
        self.assertIn('target="_blank"', out)

    def test_header_injection_in_subject_is_neutralised(self):
        # The attack is "make my text become a NEW header line". The defence is
        # that CR/LF never survive into a header value, so the payload stays
        # part of the Subject rather than becoming a Bcc. Asserting on header
        # LINES (not a substring of the whole message) is what actually tests
        # that, the words are allowed to appear, just not as a header.
        import base64
        raw, _ = ne.build_mime(
            to_email="a@b.com",
            subject="Hello\r\nBcc: victim@example.com\r\nX-Evil: 1",
            html_body="<p>x</p>", text_body="x")
        decoded = base64.urlsafe_b64decode(raw).decode("utf-8", "replace")
        headers = decoded.split("\n\n", 1)[0].splitlines()
        starts = [h.split(":", 1)[0].strip().lower() for h in headers if ":" in h and not h.startswith((" ", "\t"))]
        self.assertNotIn("bcc", starts, "injected Bcc must not become a real header")
        self.assertNotIn("x-evil", starts, "injected header must not become a real header")
        self.assertEqual(len([s for s in starts if s == "to"]), 1, "exactly one To header")

        # Same guard on the recipient field itself.
        raw2, _ = ne.build_mime(to_email="a@b.com\r\nBcc: victim@example.com",
                                subject="s", html_body="<p>x</p>", text_body="x")
        d2 = base64.urlsafe_b64decode(raw2).decode("utf-8", "replace")
        starts2 = [h.split(":", 1)[0].strip().lower()
                   for h in d2.split("\n\n", 1)[0].splitlines()
                   if ":" in h and not h.startswith((" ", "\t"))]
        self.assertNotIn("bcc", starts2)

    def test_email_with_newline_is_rejected_outright(self):
        self.assertEqual(ne.normalize_email("a@b.com\nBcc: c@d.com"), "")
        self.assertEqual(ne.normalize_email("a@b.com,c@d.com"), "")

    def test_plain_text_alternative_is_readable(self):
        html = ('<h2>Update</h2><p>Hello <b>there</b></p>'
                '<ul><li>One</li><li>Two</li></ul>'
                '<p><a href="https://currentsandcritters.com">Play now</a></p>')
        text = ne.html_to_text(ne.sanitize_html(html))
        self.assertIn("Update", text)
        self.assertIn("• One", text)
        self.assertIn("• Two", text)
        self.assertIn("Play now <https://currentsandcritters.com>", text)
        self.assertNotIn("<", text.replace("<https://currentsandcritters.com>", ""))


class TestCsv(unittest.TestCase):
    def test_formula_injection_is_defused(self):
        rows = [
            {"email": "=cmd|'/c calc'!A1", "status": "active", "source": "x",
             "subscribedAtIso": "+1", "resubscribedAtIso": "-2", "unsubscribedAtIso": "@x",
             "welcomeEmailStatus": "\tTAB"},
        ]
        out = ns.build_csv(rows)
        body = out.splitlines()[1]
        for cell in body.split('","'):
            stripped = cell.strip('"')
            if stripped:
                self.assertFalse(stripped[0] in ("=", "+", "-", "@", "\t", "\r"),
                                 "dangerous leading char survived: %r" % stripped)
        self.assertIn("'=cmd", out)

    def test_export_carries_no_secrets(self):
        rows = [{"email": "a@b.com", "status": "active", "source": "Stripe Checkout",
                 "subscribedAtIso": "x", "resubscribedAtIso": "y",
                 "unsubscribedAtIso": "", "welcomeEmailStatus": "sent"}]
        out = ns.build_csv(rows).lower()
        for secret in ("unsubid", "token", "tokenversion", "idtoken", "secret",
                       "refresh", "stripe_sk", "sk_"):
            self.assertNotIn(secret, out, secret)


# ══════════════════════════════════════════════════════════════════════════
#  4. UNSUBSCRIBE
# ══════════════════════════════════════════════════════════════════════════
class TestUnsubscribe(Base):
    def test_valid_token_unsubscribes(self):
        self.signup("a@b.com")
        tok = ns.make_unsub_token(self.sub_for("a@b.com"))
        self.assertTrue(ns.unsubscribe_with_token(tok)["known"])
        self.assertEqual(self.sub_for("a@b.com")["status"], ns.STATUS_UNSUB)

    def test_modified_token_is_rejected(self):
        self.signup("a@b.com")
        sub = self.sub_for("a@b.com")
        tok = ns.make_unsub_token(sub)
        uid, mac = tok.split(".", 1)
        # Flip one character of the MAC.
        flipped = ("A" if mac[0] != "A" else "B") + mac[1:]
        self.assertIsNone(ns.resolve_unsub_token(uid + "." + flipped))
        # ...and swapping in another subscriber's id doesn't help either.
        self.signup("c@d.com", event_id="evt_2", session_id="cs_2")
        other = self.sub_for("c@d.com")
        self.assertIsNone(ns.resolve_unsub_token(other["unsubId"] + "." + mac),
                          "a token cannot be pointed at a different subscriber")
        self.assertEqual(self.sub_for("c@d.com")["status"], ns.STATUS_ACTIVE)

    def test_invalid_tokens_do_not_reveal_membership(self):
        self.signup("a@b.com")
        h = Handler()
        ns.handle_post(h, Parsed("/api/newsletter/unsubscribe"), {"token": "nonsense.token"})
        known = h.last
        h2 = Handler()
        tok = ns.make_unsub_token(self.sub_for("a@b.com"))
        ns.handle_post(h2, Parsed("/api/newsletter/unsubscribe"), {"token": tok})
        self.assertEqual(known, h2.last,
                         "the reply is identical whether or not the address exists")

    def test_token_is_not_derived_from_the_address(self):
        self.signup("a@b.com")
        sub = self.sub_for("a@b.com")
        tok = ns.make_unsub_token(sub)
        self.assertNotIn("a@b.com", tok)
        self.assertNotIn(ns._subscriber_id("a@b.com"), tok,
                         "the document id must not appear in the link")
        self.assertGreaterEqual(len(sub["unsubId"]), 16)

    def test_raw_token_is_never_stored(self):
        self.signup("a@b.com")
        sub = self.sub_for("a@b.com")
        tok = ns.make_unsub_token(sub)
        mac = tok.split(".", 1)[1]
        self.assertNotIn(mac, repr(sub), "the signature is recomputed, never persisted")

    def test_one_click_post_works(self):
        self.signup("a@b.com")
        tok = ns.make_unsub_token(self.sub_for("a@b.com"))
        h = Handler()
        h.headers = {"Content-Length": "0"}
        h.rfile = None
        ns.handle_one_click_post(h, Parsed("/newsletter/unsubscribe/" + tok))
        self.assertEqual(h.status, 200)
        self.assertEqual(self.sub_for("a@b.com")["status"], ns.STATUS_UNSUB)

    def test_get_renders_page_but_does_not_unsubscribe(self):
        self.signup("a@b.com")
        tok = ns.make_unsub_token(self.sub_for("a@b.com"))
        h = Handler()
        self.assertTrue(ns.handle_get(h, Parsed("/newsletter/unsubscribe/" + tok)))
        self.assertIsNotNone(h.html, "the confirmation page is served")
        self.assertEqual(self.sub_for("a@b.com")["status"], ns.STATUS_ACTIVE,
                         "a link-scanner GET must not unsubscribe anybody")

    def test_no_secret_means_no_marketing_mail(self):
        old = os.environ.pop("NEWSLETTER_UNSUBSCRIBE_SECRET")
        try:
            ev, sess = self.stripe_event("a@b.com")
            ns.handle_stripe_session(ev, sess)
            self.drain()
            sub = self.sub_for("a@b.com")
            self.assertEqual(sub["welcomeEmailStatus"], "failed")
            self.assertEqual(len(self.box.to("a@b.com")), 0,
                             "never send marketing mail with no working unsubscribe link")
        finally:
            os.environ["NEWSLETTER_UNSUBSCRIBE_SECRET"] = old


# ══════════════════════════════════════════════════════════════════════════
#  5. AUTHORISATION
# ══════════════════════════════════════════════════════════════════════════
class TestAdminAuth(Base):
    def test_no_token_is_denied(self):
        h = Handler()
        ns.handle_post(h, Parsed("/api/newsletter/dashboard"), {})
        self.assertEqual(h.status, 403)

    def test_wrong_google_account_is_denied(self):
        h = self.admin_post("dashboard", email="someone.else@gmail.com")
        self.assertEqual(h.status, 403)
        self.assertEqual(h.last.get("error"), "unauthorized")

    def test_unverified_email_is_denied(self):
        h = self.admin_post("dashboard", verified=False)
        self.assertEqual(h.status, 403)

    def test_forged_token_is_denied(self):
        h = Handler()
        ns.handle_post(h, Parsed("/api/newsletter/subscribers"),
                       {"idToken": "not-a-real-token", "email": ADMIN})
        self.assertEqual(h.status, 403)

    def test_uid_or_email_in_body_is_ignored(self):
        h = Handler()
        ns.handle_post(h, Parsed("/api/newsletter/dashboard"),
                       {"email": ADMIN, "uid": "uid-admin", "isAdmin": True})
        self.assertEqual(h.status, 403, "claims in the body are never trusted")

    def test_every_route_is_protected_independently(self):
        routes = ["dashboard", "subscribers", "subscriber-add", "subscriber-unsubscribe",
                  "subscriber-reactivate", "export", "campaign-save", "campaign-list",
                  "campaign-get", "campaign-duplicate", "campaign-preview", "test-send",
                  "campaign-start", "campaign-progress", "campaign-retry", "audit",
                  "settings", "whoami"]
        for r in routes:
            h = self.admin_post(r, email="attacker@evil.example")
            self.assertEqual(h.status, 403, "%s is not protected" % r)

    def test_unauthorized_attempt_is_audited_without_leaking_the_token(self):
        self.admin_post("subscribers", email="attacker@evil.example")
        rows = list(self.db.collection(ns.C_AUDIT)._docs.values())
        hits = [r for r in rows if r["action"] == "unauthorized_admin_access"]
        self.assertTrue(hits)
        blob = repr(rows)
        self.assertNotIn("TOK|", blob, "no token fragment in the audit log")
        self.assertNotIn("attacker@evil.example", blob,
                         "an attacker-supplied email is not written into the log")

    def test_several_admins_can_be_allowlisted(self):
        """ADMIN_EMAIL may list more than one account, so a second owner login
        does not mean editing code, but it stays an EXACT-match allowlist."""
        old = os.environ.get("ADMIN_EMAIL")
        os.environ["ADMIN_EMAIL"] = ADMIN + ",currentsandcritters@gmail.com"
        try:
            self.assertEqual(ne.admin_email(), ADMIN, "the first listed is primary")
            self.assertIn("currentsandcritters@gmail.com", ne.admin_emails())
            # Both get in...
            for who in (ADMIN, "currentsandcritters@gmail.com",
                        "CurrentsAndCritters@Gmail.COM"):
                h = self.admin_post("whoami", email=who)
                self.assertEqual(h.status, 200, who)
            # ...and nobody else does, including near-misses.
            for who in ("currentsandcritters@gmail.com.evil.com",
                        "xcurrentsandcritters@gmail.com",
                        "currentsandcritters@gmail.co",
                        "someone@beardedsealstudios.com"):
                h = self.admin_post("whoami", email=who)
                self.assertEqual(h.status, 403, who)
        finally:
            if old is None:
                os.environ.pop("ADMIN_EMAIL", None)
            else:
                os.environ["ADMIN_EMAIL"] = old

    def test_admin_gets_through(self):
        h = self.admin_post("whoami")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.last["email"], ADMIN)

    def test_repeated_failures_are_rate_limited(self):
        codes = set()
        for _ in range(45):
            h = self.admin_post("dashboard", email="attacker@evil.example", ip="9.9.9.9")
            codes.add(h.status)
        self.assertIn(429, codes, "brute force is throttled")


# ══════════════════════════════════════════════════════════════════════════
#  6. SUBSCRIBER MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════
class TestSubscriberManagement(Base):
    def test_manual_add_requires_consent_checkbox(self):
        h = self.admin_post("subscriber-add", {"email": "new@x.com"})
        self.assertFalse(h.last["ok"])
        self.assertEqual(len(self.subs()), 0)

        h = self.admin_post("subscriber-add", {"email": "new@x.com", "consent": True})
        self.assertTrue(h.last["ok"])
        self.assertEqual(self.sub_for("new@x.com")["source"], ns.SOURCE_MANUAL)

    def test_manual_add_validates_email(self):
        h = self.admin_post("subscriber-add", {"email": "not-an-email", "consent": True})
        self.assertFalse(h.last["ok"])
        self.assertEqual(len(self.subs()), 0)

    def test_manual_add_rejects_duplicate(self):
        self.signup("dup@x.com")
        h = self.admin_post("subscriber-add", {"email": "DUP@x.com", "consent": True})
        self.assertFalse(h.last["ok"])
        self.assertTrue(h.last.get("duplicate"))
        self.assertEqual(len(self.subs()), 1)

    def test_admin_unsubscribe_preserves_the_record(self):
        self.signup("a@b.com")
        sid = ns._subscriber_id("a@b.com")
        h = self.admin_post("subscriber-unsubscribe", {"id": sid})
        self.assertTrue(h.last["ok"])
        sub = self.sub_for("a@b.com")
        self.assertIsNotNone(sub, "the record is kept, never deleted")
        self.assertEqual(sub["status"], ns.STATUS_UNSUB)
        self.assertGreater(sub["unsubscribedAt"], 0)

    def test_reactivate_requires_a_reason(self):
        self.signup("a@b.com")
        sid = ns._subscriber_id("a@b.com")
        self.admin_post("subscriber-unsubscribe", {"id": sid})
        h = self.admin_post("subscriber-reactivate", {"id": sid})
        self.assertEqual(h.status, 400)
        h = self.admin_post("subscriber-reactivate", {"id": sid, "reason": "emailed asking"})
        self.assertTrue(h.last["ok"])
        self.assertEqual(self.sub_for("a@b.com")["status"], ns.STATUS_ACTIVE)

    def test_search_filter_sort_and_paginate(self):
        for i in range(12):
            self.signup("user%02d@x.com" % i, event_id="evt%d" % i, session_id="cs%d" % i)
        self.admin_post("subscriber-unsubscribe", {"id": ns._subscriber_id("user00@x.com")})
        ns._invalidate_subs_cache()

        h = self.admin_post("subscribers", {"status": "active", "perPage": 10, "page": 1})
        self.assertEqual(h.last["counts"]["active"], 11)
        self.assertEqual(h.last["counts"]["unsubscribed"], 1)
        self.assertEqual(len(h.last["rows"]), 10)
        self.assertEqual(h.last["pages"], 2)

        h = self.admin_post("subscribers", {"query": "user03"})
        self.assertEqual(len(h.last["rows"]), 1)
        self.assertEqual(h.last["rows"][0]["email"], "user03@x.com")

        h = self.admin_post("subscribers", {"sort": "email", "perPage": 50})
        emails = [r["email"] for r in h.last["rows"]]
        self.assertEqual(emails, sorted(emails))

    def test_subscriber_payload_carries_no_token(self):
        self.signup("a@b.com")
        h = self.admin_post("subscribers")
        blob = repr(h.last)
        self.assertNotIn("unsubId", blob)
        self.assertNotIn("tokenVersion", blob)
        self.assertNotIn(self.sub_for("a@b.com")["unsubId"], blob,
                         "the admin browser is never sent a working unsubscribe token")

    def test_export_is_audited(self):
        self.signup("a@b.com")
        h = self.admin_post("export", {"status": "all"})
        self.assertTrue(h.last["ok"])
        self.assertIn("a@b.com", h.last["csv"])
        actions = [r["action"] for r in self.db.collection(ns.C_AUDIT)._docs.values()]
        self.assertIn("csv_exported", actions)


# ══════════════════════════════════════════════════════════════════════════
#  7. CAMPAIGNS
# ══════════════════════════════════════════════════════════════════════════
class TestCampaigns(Base):
    def _make_list(self, n=5):
        for i in range(n):
            self.signup("p%d@x.com" % i, event_id="e%d" % i, session_id="c%d" % i)
        self.box.messages.clear()
        ns._invalidate_subs_cache()

    def _draft(self, subject="Hello", html="<p>Body text</p>"):
        h = self.admin_post("campaign-save", {"subject": subject, "contentHtml": html})
        self.assertTrue(h.last["ok"], h.last)
        return h.last["id"]

    def run_worker(self, passes=20):
        for _ in range(passes):
            for cid in ns._sending_campaign_ids():
                ns._process_campaign_batch(cid)
            if not ns._sending_campaign_ids():
                break

    def test_draft_content_is_sanitized_on_save(self):
        cid = self._draft(html='<p>ok</p><script>alert(1)</script>')
        camp = ns._get_campaign(cid)
        self.assertNotIn("script", camp["contentHtml"].lower())
        self.assertIn("ok", camp["contentHtml"])

    def test_send_requires_the_confirmation_phrase(self):
        self._make_list(3)
        cid = self._draft()
        # Case-sensitive and exact. Surrounding whitespace IS forgiven, it is
        # a paste artefact, not a sign the person didn't mean it, and the
        # friction that matters is having had to type the word at all.
        for bad in ("", "send", "yes", "Send", "SENDD", "SEN", "SEND NOW"):
            h = self.admin_post("campaign-start", {"id": cid, "confirm": bad})
            self.assertFalse(h.last["ok"], bad)
        self.assertEqual(ns._get_campaign(cid)["status"], ns.CAMP_DRAFT)

    def test_full_send_one_email_each_no_duplicates(self):
        self._make_list(5)
        cid = self._draft()
        h = self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        self.assertTrue(h.last["ok"], h.last)
        self.assertEqual(h.last["recipients"], 5)
        self.run_worker()

        camp = ns._get_campaign(cid)
        self.assertEqual(camp["status"], ns.CAMP_SENT)
        self.assertEqual(camp["sentCount"], 5)
        addrs = [m["to_email"] for m in self.box.messages]
        self.assertEqual(sorted(addrs), sorted("p%d@x.com" % i for i in range(5)))
        self.assertEqual(len(addrs), len(set(addrs)), "nobody gets two copies")

    def test_each_message_has_exactly_one_recipient_and_its_own_link(self):
        self._make_list(3)
        cid = self._draft()
        self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        self.run_worker()
        links = set()
        for m in self.box.messages:
            self.assertNotIn(",", m["to_email"], "one address per message")
            self.assertTrue(m["unsubscribe_url"], "every recipient gets an unsubscribe link")
            links.add(m["unsubscribe_url"])
            # No other subscriber's address may appear anywhere in the message.
            for other in ("p0@x.com", "p1@x.com", "p2@x.com"):
                if other != m["to_email"]:
                    self.assertNotIn(other, m["html_body"])
                    self.assertNotIn(other, m["text_body"])
        self.assertEqual(len(links), 3, "each unsubscribe link is unique to its recipient")

    def test_double_click_send_starts_once(self):
        self._make_list(3)
        cid = self._draft()
        first = self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        second = self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        self.assertTrue(first.last["ok"])
        self.assertFalse(second.last["ok"], "the second click is refused")
        self.run_worker()
        self.assertEqual(len(self.box.messages), 3)

    def test_unsubscribed_mid_campaign_is_skipped(self):
        self._make_list(4)
        cid = self._draft()
        self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        # They unsubscribe after the campaign was built but before their turn.
        sub = self.sub_for("p2@x.com")
        ns.unsubscribe_with_token(ns.make_unsub_token(sub))
        self.run_worker()
        addrs = [m["to_email"] for m in self.box.messages]
        self.assertNotIn("p2@x.com", addrs, "status is re-checked immediately before each send")
        self.assertEqual(len(addrs), 3)
        self.assertEqual(ns._get_campaign(cid)["skippedCount"], 1)

    def test_already_unsubscribed_are_never_in_the_campaign(self):
        self._make_list(4)
        ns.unsubscribe_with_token(ns.make_unsub_token(self.sub_for("p0@x.com")))
        ns._invalidate_subs_cache()
        cid = self._draft()
        h = self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        self.assertEqual(h.last["recipients"], 3)
        self.run_worker()
        self.assertNotIn("p0@x.com", [m["to_email"] for m in self.box.messages])

    def test_restart_mid_send_resumes_without_duplicates(self):
        self._make_list(6)
        cid = self._draft()
        self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        # One batch's worth goes out, then the "process dies".
        ns.SEND_BATCH, keep = 2, ns.SEND_BATCH
        try:
            ns._process_campaign_batch(cid)
            sent_before = list(m["to_email"] for m in self.box.messages)
            self.assertEqual(len(sent_before), 2)
            # Restart: a fresh worker picks up exactly the ones still pending.
            self.run_worker()
        finally:
            ns.SEND_BATCH = keep
        addrs = [m["to_email"] for m in self.box.messages]
        self.assertEqual(len(addrs), 6)
        self.assertEqual(len(set(addrs)), 6, "the resumed run re-sent nobody")

    def test_in_flight_at_crash_is_marked_interrupted_not_resent(self):
        self._make_list(2)
        cid = self._draft()
        self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        cref = self.db.collection(ns.C_CAMPAIGNS).document(cid)
        rcoll = cref.collection(ns.C_RECIPIENTS)
        sid = ns._subscriber_id("p0@x.com")
        # Simulate: claimed for sending, then the process died. Lease expired.
        rcoll.document(sid).set({"status": ns.R_SENDING, "leaseUntil": ns._now() - 10,
                                 "attempts": 1}, merge=True)
        self.run_worker()
        after = rcoll.document(sid).get().to_dict()
        self.assertEqual(after["status"], ns.R_INTERRUPTED,
                         "unknown outcome is never silently re-sent")
        self.assertNotIn("p0@x.com", [m["to_email"] for m in self.box.messages])
        # ...but the admin can requeue it explicitly.
        h = self.admin_post("campaign-retry", {"id": cid})
        self.assertTrue(h.last["ok"])
        self.run_worker()
        self.assertIn("p0@x.com", [m["to_email"] for m in self.box.messages])

    def test_retry_never_resends_someone_already_sent(self):
        self._make_list(3)
        cid = self._draft()
        self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        self.run_worker()
        self.assertEqual(len(self.box.messages), 3)
        self.admin_post("campaign-retry", {"id": cid})
        self.run_worker()
        self.assertEqual(len(self.box.messages), 3, "sent recipients are untouched by a retry")

    def test_permanent_failure_for_one_recipient_does_not_stop_the_rest(self):
        self._make_list(3)
        cid = self._draft()
        self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})

        real = self.box.send
        def selective(**kw):
            if kw.get("to_email") == "p1@x.com":
                raise ne.SendError("bad address", category="invalid_message", retryable=False)
            return real(**kw)
        ne.send_email = selective
        self.run_worker()

        addrs = [m["to_email"] for m in self.box.messages]
        self.assertEqual(sorted(addrs), ["p0@x.com", "p2@x.com"])
        camp = ns._get_campaign(cid)
        self.assertEqual(camp["failedCount"], 1)
        self.assertEqual(camp["sentCount"], 2)
        self.assertEqual(camp["status"], ns.CAMP_SENT, "the campaign still completes")

    def test_transient_failure_is_retried_then_succeeds(self):
        self._make_list(1)
        cid = self._draft()
        self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        real = self.box.send
        calls = {"n": 0}
        def flaky(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ne.SendError("429", category="rate_limit", retryable=True)
            return real(**kw)
        ne.send_email = flaky
        self.run_worker()
        self.assertEqual(len(self.box.messages), 1)
        self.assertEqual(ns._get_campaign(cid)["sentCount"], 1)

    def test_sent_campaign_cannot_be_edited(self):
        self._make_list(2)
        cid = self._draft()
        self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        self.run_worker()
        h = self.admin_post("campaign-save", {"id": cid, "subject": "changed",
                                              "contentHtml": "<p>x</p>"})
        self.assertFalse(h.last["ok"])
        self.assertEqual(ns._get_campaign(cid)["subject"], "Hello")

    def test_duplicate_makes_a_new_editable_draft(self):
        cid = self._draft(subject="Original", html="<p>content</p>")
        h = self.admin_post("campaign-duplicate", {"id": cid})
        self.assertTrue(h.last["ok"])
        new = ns._get_campaign(h.last["id"])
        self.assertNotEqual(new["id"], cid)
        self.assertEqual(new["status"], ns.CAMP_DRAFT)
        self.assertIn("Original", new["subject"])
        self.assertIn("content", new["contentHtml"])

    def test_send_blocked_without_gmail(self):
        self._make_list(2)
        cid = self._draft()
        ne.connection_status = lambda: {"connected": False, "canSendAsSender": False,
                                        "transportLabel": "not configured",
                                        "error": "not connected"}
        h = self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        self.assertFalse(h.last["ok"])
        self.assertEqual(ns._get_campaign(cid)["status"], ns.CAMP_DRAFT)

    def test_send_blocked_with_no_active_subscribers(self):
        cid = self._draft()
        h = self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        self.assertFalse(h.last["ok"])

    def test_campaign_history_is_permanent(self):
        self._make_list(2)
        cid = self._draft(subject="Newsletter One")
        self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        self.run_worker()
        h = self.admin_post("campaign-progress", {"id": cid})
        c = h.last["campaign"]
        self.assertEqual(c["subject"], "Newsletter One")
        self.assertEqual(c["intendedRecipients"], 2)
        self.assertEqual(c["tally"]["sent"], 2)
        self.assertTrue(c["startedAtIso"])
        self.assertTrue(c["sentAtIso"])
        self.assertEqual(c["startedBy"], ADMIN)


# ══════════════════════════════════════════════════════════════════════════
#  8. TEST EMAILS
# ══════════════════════════════════════════════════════════════════════════
class TestTestEmails(Base):
    def test_test_email_only_goes_to_the_admin(self):
        h = self.admin_post("test-send", {"subject": "Hi", "contentHtml": "<p>x</p>",
                                          "to": "attacker@evil.example",
                                          "email": "attacker@evil.example"})
        self.assertTrue(h.last["ok"], h.last)
        self.assertEqual([m["to_email"] for m in self.box.messages], [ADMIN],
                         "the destination is never taken from the request")

    def test_test_email_is_labelled_and_carries_no_real_token(self):
        self.signup("real@x.com")
        self.box.messages.clear()
        self.admin_post("test-send", {"subject": "Hi", "contentHtml": "<p>x</p>"})
        m = self.box.messages[0]
        self.assertTrue(m["subject"].startswith("[TEST]"))
        self.assertIn("TEST EMAIL", m["html_body"])
        self.assertIn("TEST EMAIL", m["text_body"])
        self.assertFalse(m.get("unsubscribe_url"), "no live unsubscribe token in a test")
        self.assertNotIn(self.sub_for("real@x.com")["unsubId"], m["html_body"])

    def test_test_email_changes_no_subscriber_records(self):
        self.signup("a@b.com")
        before = copy.deepcopy(self.subs())
        self.admin_post("test-send", {"subject": "Hi", "contentHtml": "<p>x</p>"})
        self.assertEqual(before, self.subs())

    def test_repeated_test_sends_are_throttled(self):
        oks = 0
        for _ in range(14):
            h = self.admin_post("test-send", {"subject": "Hi", "contentHtml": "<p>x</p>"},
                                ip="5.5.5.5")
            if h.last.get("ok"):
                oks += 1
        self.assertLessEqual(oks, 10, "a stuck finger cannot fire twenty test sends")


# ══════════════════════════════════════════════════════════════════════════
#  9. EMAIL CONTENT
# ══════════════════════════════════════════════════════════════════════════
class TestEmailContent(Base):
    def test_welcome_email_content(self):
        self.signup("a@b.com")
        m = self.box.to("a@b.com")[0]
        self.assertEqual(m["subject"], "Welcome to the Currents & Critters Community!")
        html, text = m["html_body"], m["text_body"]
        for phrase in ("Hi!!!", "Thank you for joining the Currents &amp; Critters email list",
                       "New game features and updates", "Online game nights and special events",
                       "Progress on the physical card game",
                       "Rewards and important announcements",
                       "Opportunities to playtest and help improve the game",
                       "Timothy Honey", "Creator of Currents &amp; Critters"):
            self.assertIn(phrase, html, phrase)
        # Footer, on every marketing email, automatically.
        self.assertIn("Bearded Seal Studios LLC", html)
        self.assertIn("916A South Douglas Avenue", html)
        self.assertIn("Nashville, TN 37204-2021", html)
        self.assertIn("You received this email because you signed up", html)
        self.assertIn("Unsubscribe from these emails", html)
        self.assertIn("Privacy Policy", html)
        self.assertIn("currentsandcritters.com", html)
        self.assertIn("email-logo.png", html, "the website logo is in the header")
        self.assertIn("Visit Currents &amp; Critters", html)
        # Plain text alternative exists and is readable.
        self.assertIn("Hi!!!", text)
        self.assertIn("Bearded Seal Studios LLC", text)
        self.assertIn("Unsubscribe from these emails:", text)
        self.assertNotIn("<p", text)

    def test_no_tracking_pixel(self):
        self.signup("a@b.com")
        html = self.box.to("a@b.com")[0]["html_body"]
        self.assertNotIn("width=\"1\"", html)
        self.assertNotIn("open.gif", html)
        self.assertNotIn("/track", html)
        self.assertNotIn("pixel", html.lower())

    def test_owner_notification(self):
        self.signup("fan@x.com")
        m = self.box.to(ADMIN)[0]
        self.assertEqual(m["subject"], "New Currents & Critters Newsletter Subscriber")
        self.assertIn("fan@x.com", m["html_body"])
        self.assertIn("Stripe Checkout", m["html_body"])
        self.assertIn("New signup", m["html_body"])
        self.assertNotIn(self.sub_for("fan@x.com")["unsubId"], m["html_body"],
                         "the owner notification never carries the unsubscribe token")

    def test_owner_notification_says_reactivation(self):
        self.signup("fan@x.com", event_id="e1", session_id="c1")
        ns.unsubscribe_with_token(ns.make_unsub_token(self.sub_for("fan@x.com")))
        self.box.messages.clear()
        self.signup("fan@x.com", event_id="e2", session_id="c2")
        m = self.box.to(ADMIN)[0]
        self.assertIn("Reactivation", m["html_body"])

    def test_mime_headers(self):
        import base64
        raw, mid = ne.build_mime(
            to_email="a@b.com", subject="Subject here",
            html_body="<p>hi</p>", text_body="hi",
            unsubscribe_url="https://play.currentsandcritters.com/newsletter/unsubscribe/t.k",
            one_click_url="https://play.currentsandcritters.com/newsletter/unsubscribe/t.k")
        msg = base64.urlsafe_b64decode(raw).decode("utf-8", "replace")
        self.assertIn("List-Unsubscribe:", msg)
        self.assertIn("List-Unsubscribe-Post: List-Unsubscribe=One-Click", msg)
        self.assertIn("Reply-To: timothy.honey@beardedsealstudios.com", msg)
        self.assertIn("Precedence: bulk", msg)
        self.assertIn("multipart/alternative", msg)
        self.assertIn("text/plain", msg)
        self.assertIn("text/html", msg)
        self.assertTrue(mid.startswith("<") and mid.endswith(">"))
        self.assertIn("Currents", msg.split("From:")[1][:120])

    def test_message_ids_are_unique(self):
        ids = {ne.build_mime(to_email="a@b.com", subject="s", html_body="<p>h</p>",
                             text_body="t")[1] for _ in range(50)}
        self.assertEqual(len(ids), 50)


# ══════════════════════════════════════════════════════════════════════════
# 10. AUDIT LOG + DASHBOARD
# ══════════════════════════════════════════════════════════════════════════
class TestAudit(Base):
    def test_key_actions_are_recorded(self):
        self.signup("a@b.com")
        cid = self.admin_post("campaign-save", {"subject": "S", "contentHtml": "<p>b</p>"}).last["id"]
        self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        self.admin_post("export", {})
        self.admin_post("test-send", {"subject": "S", "contentHtml": "<p>b</p>"})
        actions = {r["action"] for r in self.db.collection(ns.C_AUDIT)._docs.values()}
        for expected in ("subscriber_added_stripe", "welcome_email_sent", "draft_created",
                         "campaign_approved", "campaign_started", "csv_exported",
                         "test_email_sent"):
            self.assertIn(expected, actions, expected)

    def test_audit_never_stores_secrets_or_content(self):
        self.signup("a@b.com")
        secret_body = "<p>SECRET-NEWSLETTER-BODY-TEXT</p>"
        cid = self.admin_post("campaign-save",
                              {"subject": "S", "contentHtml": secret_body}).last["id"]
        self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        blob = repr(list(self.db.collection(ns.C_AUDIT)._docs.values()))
        self.assertNotIn("SECRET-NEWSLETTER-BODY-TEXT", blob, "email content is not logged")
        self.assertNotIn(self.sub_for("a@b.com")["unsubId"], blob, "no unsubscribe token")
        self.assertNotIn("TOK|", blob, "no auth token")
        for k in ("client_secret", "refresh_token", "sk_live", "sk_test", "whsec_"):
            self.assertNotIn(k, blob, k)

    def test_dashboard_numbers(self):
        for i in range(4):
            self.signup("d%d@x.com" % i, event_id="e%d" % i, session_id="c%d" % i)
        self.admin_post("subscriber-unsubscribe", {"id": ns._subscriber_id("d0@x.com")})
        ns._invalidate_subs_cache()
        h = self.admin_post("dashboard")
        self.assertEqual(h.last["activeCount"], 3)
        self.assertEqual(h.last["unsubscribedCount"], 1)
        self.assertEqual(h.last["totalCount"], 4)
        self.assertTrue(h.last["mostRecentSignupAtIso"])

    def test_settings_reports_honestly_and_leaks_nothing(self):
        h = self.admin_post("settings")
        d = h.last
        self.assertIn("gmail", d)
        self.assertIn("stripe", d)
        self.assertTrue(d["unsubscribeSecretSet"])
        self.assertEqual(d["adminEmail"], ADMIN)
        blob = repr(d)
        for secret in (os.environ.get("NEWSLETTER_UNSUBSCRIBE_SECRET", "zzz"),
                       "client_secret", "refresh_token", "access_token"):
            self.assertNotIn(secret, blob, secret)

    def test_stripe_label_diagnostic_records_labels_not_answers(self):
        self.signup("someone@x.com", label="Some Other Question")
        h = self.admin_post("settings")
        s = h.last["stripe"]
        self.assertIn("Some Other Question", s["lastSeenLabels"])
        self.assertFalse(s["lastSeenMatched"])
        self.assertNotIn("someone@x.com", repr(s), "an ANSWER is never recorded, only the label")


# ══════════════════════════════════════════════════════════════════════════
# 10b. THE PUBLIC WEBSITE SIGNUP (confirmed opt-in)
# ══════════════════════════════════════════════════════════════════════════
class TestPublicSignup(Base):
    def post(self, action, payload, ip="7.7.7.7"):
        h = Handler(ip)
        ns.handle_post(h, Parsed("/api/newsletter/" + action), payload)
        # The request itself only QUEUES the mail (see
        # test_the_reply_does_not_wait_for_the_welcome_email). Running the
        # worker's pass here lets every other test go on asking the question it
        # cares about: "did the right mail go to the right person" without
        # each one knowing that the send is deferred.
        self.drain()
        return h

    def test_signup_joins_the_list_at_once_and_mails_the_welcome(self):
        """The whole point of the single opt-in change: no second step, and
        the mail that arrives is the welcome, not a chore."""
        h = self.post("subscribe", {"email": "New@Person.com"})
        self.assertTrue(h.last["ok"])
        sub = self.sub_for("new@person.com")
        self.assertIsNotNone(sub)
        self.assertEqual(sub["status"], ns.STATUS_ACTIVE,
                         "a website signup is a subscriber immediately")
        self.assertEqual(sub["source"], ns.SOURCE_WEBSITE)
        self.assertEqual(sub["welcomeEmailStatus"], "sent")
        sent = self.box.to("new@person.com")
        self.assertEqual(len(sent), 1, "exactly one email, and it is the welcome")
        self.assertIn("Welcome", sent[0]["subject"])
        self.assertNotIn("confirm", sent[0]["subject"].lower())

    def test_the_welcome_carries_the_way_out(self):
        """What replaces the confirmation click. Anyone whose address was
        typed in by somebody else must be able to leave from the FIRST message
        they receive, or single opt-in really would be a way to mail
        strangers."""
        self.post("subscribe", {"email": "typo@x.com"})
        msg = self.box.to("typo@x.com")[0]
        self.assertTrue(msg["unsubscribe_url"], "a live per-person unsubscribe link")
        self.assertTrue(msg["one_click_url"], "and the RFC 8058 one-click form")
        self.assertIn("Unsubscribe from these emails", msg["html_body"])
        # And it genuinely works, first try, with no login.
        token = msg["unsubscribe_url"].rsplit("/", 1)[-1]
        ns.unsubscribe_with_token(token)
        self.assertEqual(self.sub_for("typo@x.com")["status"], ns.STATUS_UNSUB)

    def test_a_signup_never_sends_a_confirmation_email(self):
        self.post("subscribe", {"email": "nochore@x.com"})
        subjects = [m["subject"].lower() for m in self.box.to("nochore@x.com")]
        self.assertTrue(subjects)
        self.assertFalse([s for s in subjects if "confirm" in s],
                         "the confirm-your-email step is gone")

    def test_a_welcome_is_due_even_if_the_send_is_deferred(self):
        """The queue is in-process; the durable record of "this person is owed
        a welcome" is the subscriber document, and it must say so before any
        mail moves, or a restart between the two loses the welcome."""
        h = Handler("7.7.7.5")
        ns.handle_post(h, Parsed("/api/newsletter/subscribe"), {"email": "owed@x.com"})
        ns._invalidate_subs_cache()
        self.assertEqual(self.sub_for("owed@x.com")["welcomeEmailStatus"],
                         ns.WELCOME_PENDING)
        self.assertIn(ns._subscriber_id("owed@x.com"), ns._pending_welcome_ids())

    def test_the_reply_does_not_wait_for_the_welcome_email(self):
        """The bug this guards: the form sat on "Signing you up…" for seconds
        after somebody typed their address, because the request handed the
        message to Gmail, a connect, a login and a round trip to Google,
        before answering the browser. Nothing in the reply depends on that
        send, so it belongs on the worker."""
        real = ne.send_email
        sent_calls = []

        def _slow(**kw):
            sent_calls.append(kw.get("to_email"))
            time.sleep(0.6)
            return real(**kw)

        ne.send_email = _slow
        try:
            h = Handler("7.7.7.9")
            started = time.time()
            ns.handle_post(h, Parsed("/api/newsletter/subscribe"),
                           {"email": "quick@x.com"})
            elapsed = time.time() - started
            self.assertTrue(h.last["ok"])
            self.assertEqual(sent_calls, [], "no mail is sent inside the request")
            self.assertLess(elapsed, 0.3,
                            "the reply must not wait on the mail provider")
            # Queued, not dropped: the person still gets their welcome.
            self.drain()
            # The subscriber's welcome goes FIRST, then Tim's heads-up. Both
            # ride the worker; neither is allowed near the request.
            self.assertEqual(sent_calls[0], "quick@x.com")
            self.assertIn(ADMIN, sent_calls, "and Tim still hears about the signup")
            self.assertIn("Welcome", self.box.to("quick@x.com")[0]["subject"])
        finally:
            ne.send_email = real

    def test_a_welcome_is_never_stuck_behind_a_campaign(self):
        """A campaign batch is 25 messages nobody is waiting on. A signup that
        lands while one is going out must not queue behind it: that person is
        sitting in front of an inbox right now."""
        self.signup("bulk@x.com")
        cid = self.admin_post("campaign-save",
                              {"subject": "S", "contentHtml": "<p>b</p>"}).last["id"]
        self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        self.box.messages.clear()

        # Queue a signup's welcome the way the request handler does, then
        # run the campaign pass: the welcome goes out during it.
        h = Handler("7.7.7.8")
        ns.handle_post(h, Parsed("/api/newsletter/subscribe"), {"email": "urgent@x.com"})
        self.assertEqual(len(self.box.to("urgent@x.com")), 0)
        for c in ns._sending_campaign_ids():
            ns._process_campaign_batch(c)
        self.assertEqual(len(self.box.to("urgent@x.com")), 1,
                         "the welcome jumped the bulk queue")

    def test_a_pending_address_can_never_receive_a_campaign(self):
        self.legacy_pending("pending@x.com")
        self.signup("real@x.com")            # a confirmed Stripe subscriber
        self.box.messages.clear()
        ns._invalidate_subs_cache()

        cid = self.admin_post("campaign-save",
                              {"subject": "S", "contentHtml": "<p>b</p>"}).last["id"]
        r = self.admin_post("campaign-start", {"id": cid, "confirm": "SEND"})
        self.assertTrue(r.last["ok"], r.last)
        self.assertEqual(r.last["recipients"], 1, "only the confirmed subscriber")
        for _ in range(6):
            for c in ns._sending_campaign_ids():
                ns._process_campaign_batch(c)
        addrs = [m["to_email"] for m in self.box.messages]
        self.assertIn("real@x.com", addrs)
        self.assertNotIn("pending@x.com", addrs,
                         "a record stranded in the old pending state is not a subscriber")

    def test_an_old_confirmation_link_still_works(self):
        """Signups stopped sending these, but the ones already sent are
        permanent. A link that answers "that no longer works" to somebody
        doing exactly what they were asked to do is the worst possible
        ending, so this path is kept alive on purpose."""
        sub = self.legacy_pending("join@x.com")
        token = ns.make_confirm_token(sub)
        self.box.messages.clear()

        res = ns.confirm_with_token(token)
        self.assertEqual(res["result"], "confirmed")
        self.assertEqual(self.sub_for("join@x.com")["status"], ns.STATUS_ACTIVE)
        self.drain()
        self.assertEqual(len(self.box.to("join@x.com")), 1)
        self.assertIn("Welcome", self.box.to("join@x.com")[0]["subject"])

    def test_confirming_twice_is_safe(self):
        self.legacy_pending("twice@x.com")
        token = ns.make_confirm_token(self.sub_for("twice@x.com"))
        ns.confirm_with_token(token)
        self.drain()
        self.box.messages.clear()
        again = ns.confirm_with_token(token)
        self.drain()
        self.assertEqual(again["result"], "already_active")
        self.assertEqual(len(self.box.to("twice@x.com")), 0, "no second welcome")

    def test_a_confirm_token_cannot_unsubscribe_and_vice_versa(self):
        """The two links are over the same subscriber id, so without a purpose
        in the signature they would be the SAME string, and the link that
        signs you up would also be the link that removes you."""
        self.signup("both@x.com")
        sub = self.sub_for("both@x.com")
        unsub_tok = ns.make_unsub_token(sub)
        confirm_tok = ns.make_confirm_token(sub)
        self.assertNotEqual(unsub_tok, confirm_tok)
        # A confirm token must not unsubscribe.
        self.assertIsNone(ns.resolve_unsub_token(confirm_tok))
        self.assertEqual(self.sub_for("both@x.com")["status"], ns.STATUS_ACTIVE)
        # An unsubscribe token must not confirm.
        self.assertFalse(ns.confirm_with_token(unsub_tok).get("known"))

    def test_a_tampered_confirm_token_is_refused(self):
        self.legacy_pending("tamper@x.com")
        tok = ns.make_confirm_token(self.sub_for("tamper@x.com"))
        uid, mac = tok.split(".", 1)
        flipped = ("A" if mac[0] != "A" else "B") + mac[1:]
        self.assertFalse(ns.confirm_with_token(uid + "." + flipped).get("known"))
        self.assertEqual(self.sub_for("tamper@x.com")["status"], ns.STATUS_PENDING)

    def test_an_unsubscribed_person_is_not_resurrected_by_an_old_link(self):
        self.legacy_pending("gone@x.com")
        sub = self.sub_for("gone@x.com")
        tok = ns.make_confirm_token(sub)
        ns._unsubscribe_by_id(sub["id"], actor="self-service", reason="test")
        res = ns.confirm_with_token(tok)
        self.assertEqual(res["result"], "unsubscribed")
        self.assertEqual(self.sub_for("gone@x.com")["status"], ns.STATUS_UNSUB)

    def test_the_form_does_not_reveal_who_is_already_subscribed(self):
        self.signup("known@x.com")
        self.box.messages.clear()
        a = self.post("subscribe", {"email": "known@x.com"}).last
        b = self.post("subscribe", {"email": "stranger@x.com"}).last
        self.assertEqual(a, b, "identical reply either way")
        self.assertEqual(len(self.box.to("known@x.com")), 0,
                         "an active subscriber gets no second welcome")

    def test_invalid_addresses_are_rejected(self):
        for bad in ("", "   ", "notanemail", "a@b", "x@y..com", "a b@x.com"):
            h = self.post("subscribe", {"email": bad})
            self.assertFalse(h.last.get("ok"), bad)
        self.assertEqual(len(self.subs()), 0)

    def test_the_form_is_rate_limited(self):
        oks = 0
        for i in range(14):
            h = self.post("subscribe", {"email": "flood%d@x.com" % i}, ip="8.8.8.8")
            if h.last.get("ok"):
                oks += 1
        self.assertLessEqual(oks, 8, "a script cannot stuff the list from one IP")

    def test_counts_keep_pending_out_of_both_buckets(self):
        self.signup("active@x.com")
        self.legacy_pending("waiting@x.com")
        ns._invalidate_subs_cache()
        c = ns.counts()
        self.assertEqual(c["active"], 1)
        self.assertEqual(c["pending"], 1)
        self.assertEqual(c["unsubscribed"], 0,
                         "pending is not 'unsubscribed', that would be a lie in the UI")
        self.assertEqual(c["total"], 2)

    def test_the_dashboard_does_not_call_a_new_signup_unsubscribed(self):
        """The reported bug, at its source.

        counts() was always careful about this; the DASHBOARD derived
        unsubscribed as "everything that is not active", so a record in the
        pending state showed up in the admin panel as having opted out."""
        self.signup("active@x.com")
        self.legacy_pending("stranded@x.com")
        ns._invalidate_subs_cache()
        d = self.admin_post("dashboard").last
        self.assertEqual(d["activeCount"], 1)
        self.assertEqual(d["pendingCount"], 1)
        self.assertEqual(d["unsubscribedCount"], 0,
                         "nobody has unsubscribed; one record was left stranded")
        self.assertEqual(d["totalCount"], 2)

    def test_a_website_signup_counts_as_active_on_the_dashboard(self):
        self.post("subscribe", {"email": "web@x.com"})
        ns._invalidate_subs_cache()
        d = self.admin_post("dashboard").last
        self.assertEqual(d["activeCount"], 1)
        self.assertEqual(d["pendingCount"], 0, "nothing is waiting on anything")

    def test_the_admin_list_can_show_and_filter_the_waiting(self):
        self.legacy_pending("waiting@x.com")
        ns._invalidate_subs_cache()
        rows = self.admin_post("subscribers", {"status": "pending"}).last["rows"]
        self.assertEqual([r["email"] for r in rows], ["waiting@x.com"])
        self.assertEqual(rows[0]["status"], ns.STATUS_PENDING,
                         "the UI cannot label it correctly if the row does not say so")

    def test_an_unconfirmed_signup_never_starves_the_welcome_queue(self):
        """A forever-pending welcome is not just cosmetic: the queue is read a
        page at a time, so enough of them push every real welcome out of it."""
        for i in range(30):
            self.legacy_pending("wait%d@x.com" % i)
        ev, sess = self.stripe_event("paid@x.com")
        ns.handle_stripe_session(ev, sess)      # no drain: this welcome is still due
        ns._invalidate_subs_cache()
        due = ns._pending_welcome_ids(25)
        self.assertIn(ns._subscriber_id("paid@x.com"), due)
        self.assertTrue(all(self.subs()[d]["status"] == ns.STATUS_ACTIVE for d in due),
                        "nothing that cannot send is holding a slot in the queue")
        self.drain()
        self.assertEqual(len(self.box.to("paid@x.com")), 1)

    def test_a_welcome_left_pending_by_an_older_build_is_healed(self):
        """Records written before welcomes were parked are already in
        Firestore with welcomeEmailStatus="pending" and no way out of it. The
        worker's own pass must repair them, not keep re-reading them."""
        self.legacy_pending("legacy@x.com")
        self.box.messages.clear()
        sid = ns._subscriber_id("legacy@x.com")
        ns._db().collection(ns.C_SUBS).document(sid).set(
            {"welcomeEmailStatus": ns.WELCOME_PENDING}, merge=True)
        ns._invalidate_subs_cache()
        self.assertNotIn(sid, ns._pending_welcome_ids(),
                         "an unconfirmed record is not due a welcome")
        self.assertEqual(self.sub_for("legacy@x.com")["welcomeEmailStatus"],
                         ns.WELCOME_AWAITING, "and it is parked so it is never re-read")
        self.assertEqual(len(self.box.to("legacy@x.com")), 0)

    def test_confirming_arms_the_welcome_that_was_parked(self):
        self.legacy_pending("arm@x.com")
        self.box.messages.clear()
        ns.confirm_with_token(ns.make_confirm_token(self.sub_for("arm@x.com")))
        sub = self.sub_for("arm@x.com")
        self.assertEqual(sub["status"], ns.STATUS_ACTIVE)
        self.assertEqual(sub["welcomeEmailStatus"], ns.WELCOME_PENDING)
        self.drain()
        self.assertEqual(len(self.box.to("arm@x.com")), 1, "the welcome actually sends")


# ══════════════════════════════════════════════════════════════════════════
# 10c. FINISHING A SIGNUP FROM THE ADMIN PANEL
# ══════════════════════════════════════════════════════════════════════════
class TestAdminFinishesSignup(Base):
    def post(self, action, payload, ip="7.7.7.7"):
        h = Handler(ip)
        ns.handle_post(h, Parsed("/api/newsletter/" + action), payload)
        # The request only queues the mail; run the worker's pass so each test
        # can ask "did the right mail go to the right person".
        self.drain()
        return h

    def test_resending_reuses_the_same_link(self):
        """A resend must not rotate the token, or the mail already sitting in
        their inbox stops working the moment they ask for another copy."""
        self.legacy_pending("again@x.com")
        first = ns.make_confirm_token(self.sub_for("again@x.com"))
        self.box.messages.clear()

        r = self.admin_post("subscriber-resend-confirmation",
                            {"id": ns._subscriber_id("again@x.com")})
        self.assertTrue(r.last["ok"], r.last)
        self.assertEqual(len(self.box.to("again@x.com")), 1)
        self.assertIn("confirm", self.box.to("again@x.com")[0]["subject"].lower())
        self.assertEqual(ns.make_confirm_token(self.sub_for("again@x.com")), first)
        self.assertTrue(ns.confirm_with_token(first)["ok"])

    def test_only_a_waiting_signup_can_be_re_sent_a_link(self):
        self.signup("done@x.com")
        self.box.messages.clear()
        r = self.admin_post("subscriber-resend-confirmation",
                            {"id": ns._subscriber_id("done@x.com")})
        self.assertFalse(r.last["ok"])
        self.assertEqual(len(self.box.to("done@x.com")), 0)

    def test_confirming_by_hand_activates_and_welcomes(self):
        self.legacy_pending("byhand@x.com")
        self.box.messages.clear()
        r = self.admin_post("subscriber-confirm",
                            {"id": ns._subscriber_id("byhand@x.com"),
                             "reason": "my own address; mail went to spam"})
        self.assertTrue(r.last["ok"], r.last)
        sub = self.sub_for("byhand@x.com")
        self.assertEqual(sub["status"], ns.STATUS_ACTIVE)
        self.assertIn("Confirmed by admin", sub["consentNote"])
        self.drain()
        self.assertEqual(len(self.box.to("byhand@x.com")), 1)

    def test_confirming_by_hand_needs_a_reason_and_is_audited(self):
        self.legacy_pending("why@x.com")
        sid = ns._subscriber_id("why@x.com")
        r = self.admin_post("subscriber-confirm", {"id": sid, "reason": "  "})
        self.assertFalse(r.last["ok"])
        self.assertEqual(self.sub_for("why@x.com")["status"], ns.STATUS_PENDING)

        self.admin_post("subscriber-confirm", {"id": sid, "reason": "spoke to them"})
        actions = [a["action"] for a in self.admin_post("audit", {"limit": 50}).last["rows"]]
        self.assertIn("subscriber_confirmed_by_admin", actions,
                      "skipping the email proof must always leave a trace")

    def test_an_unsubscribed_person_cannot_be_confirmed_back_on(self):
        """Reactivate exists and demands its own reason. Confirm-by-hand must
        not become a quieter way to do the same thing."""
        self.signup("left@x.com")
        ns._unsubscribe_by_id(ns._subscriber_id("left@x.com"),
                              actor="self-service", reason="test")
        r = self.admin_post("subscriber-confirm",
                            {"id": ns._subscriber_id("left@x.com"), "reason": "oops"})
        self.assertFalse(r.last["ok"])
        self.assertEqual(self.sub_for("left@x.com")["status"], ns.STATUS_UNSUB)

    def test_a_stranger_cannot_confirm_anybody(self):
        self.legacy_pending("target@x.com")
        sid = ns._subscriber_id("target@x.com")
        for action in ("subscriber-confirm", "subscriber-resend-confirmation"):
            h = self.admin_post(action, {"id": sid, "reason": "x"},
                                email="nobody@example.com")
            self.assertFalse(h.last.get("ok"), action)
        self.assertEqual(self.sub_for("target@x.com")["status"], ns.STATUS_PENDING)


# ══════════════════════════════════════════════════════════════════════════
# 11. TRANSPORTS, the system must work with NOTHING Google-shaped configured
# ══════════════════════════════════════════════════════════════════════════
class _EnvSandbox(unittest.TestCase):
    """Base that restores every transport env var, so one test's SMTP_HOST
    cannot decide another test's transport."""

    KEYS = ("NEWSLETTER_TRANSPORT", "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME",
            "SMTP_PASSWORD", "SMTP_SECURITY", "NEWSLETTER_API_KEY",
            "NEWSLETTER_HTTP_PROVIDER", "RESEND_API_KEY", "POSTMARK_API_KEY",
            "BREVO_API_KEY", "SENDGRID_API_KEY", "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
            "NEWSLETTER_FROM_EMAIL", "NEWSLETTER_DAILY_SEND_CAP")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.KEYS}
        for k in self.KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestTransportSelection(_EnvSandbox):
    def test_nothing_configured(self):
        self.assertEqual(ne.transport(), "")
        self.assertFalse(ne.configured())
        st = ne.connection_status()
        self.assertFalse(st["connected"])
        self.assertFalse(st["configured"])
        # It must TELL you how to fix it, and lead with the easy option.
        self.assertIn("SMTP_HOST", st["setupHint"])
        self.assertIn("NEWSLETTER_API_KEY", st["setupHint"])

    def test_smtp_wins_and_needs_no_google(self):
        os.environ.update(SMTP_HOST="smtp.example.com", SMTP_USERNAME="me@x.com",
                          SMTP_PASSWORD="pw")
        self.assertEqual(ne.transport(), "smtp")
        self.assertTrue(ne.configured())
        # Not one Google variable is set.
        for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
            "NEWSLETTER_FROM_EMAIL", "NEWSLETTER_DAILY_SEND_CAP"):
            self.assertIsNone(os.environ.get(k))

    def test_smtp_takes_priority_over_google(self):
        os.environ.update(SMTP_HOST="smtp.example.com", SMTP_USERNAME="me@x.com",
                          SMTP_PASSWORD="pw", GOOGLE_CLIENT_ID="a",
                          GOOGLE_CLIENT_SECRET="b", GOOGLE_REFRESH_TOKEN="c")
        self.assertEqual(ne.transport(), "smtp",
                         "filling in SMTP must make the Google vars dead weight")

    def test_api_key_alone_selects_http(self):
        os.environ["NEWSLETTER_API_KEY"] = "re_test123"
        self.assertEqual(ne.transport(), "http")
        self.assertEqual(ne.http_provider(), "resend")

    def test_provider_named_key_selects_that_provider(self):
        os.environ["POSTMARK_API_KEY"] = "pm-test"
        self.assertEqual(ne.transport(), "http")
        self.assertEqual(ne.http_provider(), "postmark")
        self.assertIn("Postmark", ne.transport_label())

    def test_explicit_override(self):
        os.environ.update(SMTP_HOST="smtp.example.com", SMTP_USERNAME="u",
                          SMTP_PASSWORD="p", NEWSLETTER_API_KEY="re_x",
                          NEWSLETTER_TRANSPORT="http")
        self.assertEqual(ne.transport(), "http")
        os.environ["NEWSLETTER_TRANSPORT"] = "smtp"
        self.assertEqual(ne.transport(), "smtp")

    def test_daily_cap_follows_the_sending_account(self):
        """Switching the From address to a free @gmail.com must not leave a
        Workspace-sized cap pointed at a 500/day mailbox: going over gets the
        account suspended, not bounced."""
        os.environ["NEWSLETTER_FROM_EMAIL"] = "someone@beardedsealstudios.com"
        self.assertFalse(ne.sender_is_consumer_gmail())
        self.assertEqual(ne.daily_send_cap(), ne.WORKSPACE_CAP)

        os.environ["NEWSLETTER_FROM_EMAIL"] = "currentsandcritters@gmail.com"
        self.assertTrue(ne.sender_is_consumer_gmail())
        self.assertEqual(ne.daily_send_cap(), ne.CONSUMER_GMAIL_CAP)
        self.assertLess(ne.CONSUMER_GMAIL_CAP, 500, "must sit under Gmail's real limit")

        # An explicit cap is still honoured, but it gets flagged.
        os.environ["NEWSLETTER_DAILY_SEND_CAP"] = "1200"
        self.assertEqual(ne.daily_send_cap(), 1200)
        st = ne.connection_status()
        self.assertIn("suspended", st["capWarning"])
        self.assertTrue(st["consumerGmail"])

    def test_app_password_spaces_are_stripped(self):
        """Google displays an app password as four space-separated groups, so
        that is exactly what gets pasted into Render. No provider has a
        password containing a space, so the spaces are noise: strip them
        rather than discover in production whether Gmail tolerates them."""
        os.environ.update(SMTP_HOST="h", SMTP_USERNAME="u",
                          SMTP_PASSWORD="woff lfgo xgfb rhpv")
        self.assertEqual(ne.smtp_settings()["password"], "wofflfgoxgfbrhpv")
        os.environ["SMTP_PASSWORD"] = "  abcd efgh\tijkl\nmnop  "
        self.assertEqual(ne.smtp_settings()["password"], "abcdefghijklmnop")

    def test_port_465_implies_implicit_tls(self):
        os.environ.update(SMTP_HOST="h", SMTP_USERNAME="u", SMTP_PASSWORD="p",
                          SMTP_PORT="465")
        self.assertEqual(ne.smtp_settings()["security"], "ssl")
        os.environ["SMTP_PORT"] = "587"
        self.assertEqual(ne.smtp_settings()["security"], "starttls")

    def test_send_without_any_transport_is_a_clear_config_error(self):
        with self.assertRaises(ne.SendError) as cm:
            ne.send_email(to_email="a@b.com", subject="s",
                          html_body="<p>x</p>", text_body="x")
        self.assertEqual(cm.exception.category, "config")
        self.assertFalse(cm.exception.retryable, "a missing config never retries")
        self.assertIn("SMTP_HOST", str(cm.exception))


class TestSmtpTransport(_EnvSandbox):
    def setUp(self):
        super().setUp()
        os.environ.update(SMTP_HOST="smtp.example.com", SMTP_USERNAME=ADMIN,
                          SMTP_PASSWORD="apppassword", SMTP_PORT="587")
        ne._smtp_close()
        self.sent = []

        class FakeSMTP:
            def __init__(_s, *a, **k): pass
            def ehlo(_s): return (250, b"ok")
            def starttls(_s, **k): return (220, b"ok")
            def login(_s, u, p): return (235, b"ok")
            def noop(_s): return (250, b"ok")
            def send_message(_s, msg, from_addr=None, to_addrs=None):
                self.sent.append({"msg": msg, "from": from_addr, "to": to_addrs})
                return {}
            def quit(_s): return None
            def close(_s): return None

        self.FakeSMTP = FakeSMTP
        self._orig_connect = ne._smtp_connect
        ne._smtp_connect = lambda cfg: FakeSMTP()

    def tearDown(self):
        ne._smtp_connect = self._orig_connect
        ne._smtp_close()
        super().tearDown()

    def test_sends_one_recipient_with_full_headers(self):
        res = ne.send_email(
            to_email="Fan@Example.com", subject="Hello",
            html_body="<p>hi</p>", text_body="hi",
            unsubscribe_url="https://play.currentsandcritters.com/newsletter/unsubscribe/t.k",
            one_click_url="https://play.currentsandcritters.com/newsletter/unsubscribe/t.k")
        self.assertTrue(res["messageId"].startswith("<"))
        self.assertEqual(len(self.sent), 1)
        rec = self.sent[0]
        self.assertEqual(rec["to"], ["fan@example.com"], "exactly one envelope recipient")
        self.assertEqual(rec["from"], ADMIN)
        msg = rec["msg"]
        self.assertEqual(msg["To"], "fan@example.com")
        self.assertIn("List-Unsubscribe", msg)
        self.assertEqual(msg["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        self.assertEqual(msg["Reply-To"], ADMIN)
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        parts = [p.get_content_type() for p in msg.get_payload()]
        self.assertEqual(parts, ["text/plain", "text/html"],
                         "plain first: multipart/alternative is last-part-wins")

    def test_connection_is_reused_across_sends(self):
        opened = {"n": 0}
        orig = ne._smtp_connect
        def counting(cfg):
            opened["n"] += 1
            return orig(cfg)
        ne._smtp_connect = counting
        for i in range(5):
            ne.send_email(to_email="p%d@x.com" % i, subject="s",
                          html_body="<p>x</p>", text_body="x")
        self.assertEqual(len(self.sent), 5)
        self.assertEqual(opened["n"], 1, "one TLS handshake, not five")

    def test_bad_password_is_permanent_not_retried(self):
        import smtplib
        ne._smtp_connect = lambda cfg: (_ for _ in ()).throw(
            smtplib.SMTPAuthenticationError(535, b"bad creds"))
        with self.assertRaises(ne.SendError) as cm:
            ne.send_email(to_email="a@b.com", subject="s",
                          html_body="<p>x</p>", text_body="x")
        self.assertEqual(cm.exception.category, "auth_revoked")
        self.assertFalse(cm.exception.retryable,
                         "a wrong password must not be retried per-recipient")
        self.assertIn("App Password", str(cm.exception))

    def test_dropped_connection_reconnects_and_still_sends(self):
        import smtplib
        state = {"first": True}
        outer = self
        class Flaky(self.FakeSMTP):
            def send_message(_s, msg, from_addr=None, to_addrs=None):
                if state["first"]:
                    state["first"] = False
                    raise smtplib.SMTPServerDisconnected("closed")
                outer.sent.append({"msg": msg, "from": from_addr, "to": to_addrs})
                return {}
        ne._smtp_connect = lambda cfg: Flaky()
        ne.send_email(to_email="a@b.com", subject="s", html_body="<p>x</p>", text_body="x")
        self.assertEqual(len(self.sent), 1, "the retry delivered it")

    def test_blocked_port_says_what_to_do_instead(self):
        ne._smtp_connect = lambda cfg: (_ for _ in ()).throw(OSError("connection refused"))
        with self.assertRaises(ne.SendError) as cm:
            ne.send_email(to_email="a@b.com", subject="s",
                          html_body="<p>x</p>", text_body="x")
        self.assertEqual(cm.exception.category, "network")
        self.assertTrue(cm.exception.retryable)
        self.assertIn("NEWSLETTER_API_KEY", str(cm.exception),
                      "a blocked SMTP port must point at the HTTP fallback")

    def test_connection_status_reports_smtp(self):
        st = ne.connection_status()
        self.assertEqual(st["transport"], "smtp")
        self.assertTrue(st["connected"])
        self.assertTrue(st["canSendAsSender"])
        self.assertTrue(st["senderVerified"],
                        "From == SMTP username is as close to proven as SMTP gets")
        self.assertIn("smtp.example.com", st["transportLabel"])
        self.assertNotIn("apppassword", repr(st), "the password never leaves the server")

    def test_mismatched_from_is_flagged_but_not_claimed_verified(self):
        os.environ["SMTP_USERNAME"] = "someoneelse@x.com"
        st = ne.connection_status()
        self.assertTrue(st["connected"])
        self.assertFalse(st["senderVerified"],
                         "we did not prove the From address, so we must not claim we did")
        self.assertIn("verified alias", st["setupHint"])


class TestHttpTransport(_EnvSandbox):
    def setUp(self):
        super().setUp()
        os.environ["NEWSLETTER_API_KEY"] = "re_testkey"
        self.calls = []
        self._orig = ne._http_json

        def fake(url, *, data=None, headers=None, method="GET", timeout=30):
            self.calls.append({"url": url, "body": json.loads(data.decode()) if data else {},
                               "headers": headers or {}})
            return 200, {"id": "msg-123", "MessageID": "pm-1", "messageId": "bv-1"}
        ne._http_json = fake

    def tearDown(self):
        ne._http_json = self._orig
        super().tearDown()

    def test_resend_shape(self):
        res = ne.send_email(to_email="a@b.com", subject="Hi",
                            html_body="<p>x</p>", text_body="x",
                            unsubscribe_url="https://u/1", one_click_url="https://u/1")
        self.assertEqual(res["gmailId"], "msg-123")
        c = self.calls[0]
        self.assertEqual(c["url"], "https://api.resend.com/emails")
        self.assertTrue(c["headers"]["Authorization"].startswith("Bearer "))
        self.assertEqual(c["body"]["to"], ["a@b.com"], "one recipient per request")
        self.assertIn("List-Unsubscribe", c["body"]["headers"])
        self.assertEqual(c["body"]["headers"]["List-Unsubscribe-Post"],
                         "List-Unsubscribe=One-Click")
        self.assertTrue(c["body"]["text"], "a plain-text part is always included")

    def test_postmark_shape(self):
        os.environ["NEWSLETTER_HTTP_PROVIDER"] = "postmark"
        ne.send_email(to_email="a@b.com", subject="Hi", html_body="<p>x</p>",
                      text_body="x", unsubscribe_url="https://u/1")
        c = self.calls[0]
        self.assertEqual(c["url"], "https://api.postmarkapp.com/email")
        self.assertIn("X-Postmark-Server-Token", c["headers"])
        self.assertEqual(c["body"]["To"], "a@b.com")
        names = [h["Name"] for h in c["body"]["Headers"]]
        self.assertIn("List-Unsubscribe", names)

    def test_brevo_and_sendgrid_shapes(self):
        for prov, url, key_hdr in (
            ("brevo", "https://api.brevo.com/v3/smtp/email", "api-key"),
            ("sendgrid", "https://api.sendgrid.com/v3/mail/send", "Authorization"),
        ):
            self.calls.clear()
            os.environ["NEWSLETTER_HTTP_PROVIDER"] = prov
            ne.send_email(to_email="a@b.com", subject="Hi",
                          html_body="<p>x</p>", text_body="x")
            c = self.calls[0]
            self.assertEqual(c["url"], url, prov)
            self.assertIn(key_hdr, c["headers"], prov)

    def test_bad_key_is_permanent(self):
        ne._http_json = lambda *a, **k: (401, {"message": "invalid api key"})
        with self.assertRaises(ne.SendError) as cm:
            ne.send_email(to_email="a@b.com", subject="s",
                          html_body="<p>x</p>", text_body="x")
        self.assertEqual(cm.exception.category, "auth_revoked")
        self.assertFalse(cm.exception.retryable)

    def test_rate_limit_is_retryable(self):
        ne._http_json = lambda *a, **k: (429, {"message": "slow down"})
        with self.assertRaises(ne.SendError) as cm:
            ne.send_email(to_email="a@b.com", subject="s",
                          html_body="<p>x</p>", text_body="x")
        self.assertEqual(cm.exception.category, "rate_limit")
        self.assertTrue(cm.exception.retryable)

    def test_api_key_never_appears_in_status(self):
        st = ne.connection_status()
        self.assertEqual(st["transport"], "http")
        self.assertNotIn("re_testkey", repr(st))
        self.assertFalse(st["senderVerified"],
                         "an API provider cannot prove the From address up front")


class TestNoGoogleAnywhere(_EnvSandbox):
    """The point of the whole change: a fully working newsletter with zero
    Google configuration."""

    def test_full_signup_and_send_over_smtp_with_no_google_vars(self):
        os.environ.update(SMTP_HOST="smtp.example.com", SMTP_USERNAME=ADMIN,
                          SMTP_PASSWORD="pw")
        sent = []
        orig_connect = ne._smtp_connect

        class FakeSMTP:
            def ehlo(_s): return (250, b"ok")
            def starttls(_s, **k): return (220, b"ok")
            def login(_s, u, p): return (235, b"ok")
            def noop(_s): return (250, b"ok")
            def send_message(_s, msg, from_addr=None, to_addrs=None):
                sent.append(to_addrs); return {}
            def quit(_s): return None
            def close(_s): return None

        ne._smtp_connect = lambda cfg: FakeSMTP()
        ne._smtp_close()
        db = FakeDb()
        ns.init(get_firestore=lambda: db, verify_token=_verify)
        ns._invalidate_subs_cache()
        try:
            ev = {"id": "evt_ng", "type": "checkout.session.completed", "data": {"object": {
                "id": "cs_ng", "payment_status": "paid", "custom_fields": [{
                    "label": {"type": "custom", "custom": "Enter your email to get updates"},
                    "type": "text", "text": {"value": "fan@example.com"}}]}}}
            self.assertEqual(
                ns.handle_stripe_session(ev, ev["data"]["object"]), "created")
            import queue as _q
            while True:
                try:
                    ns._send_welcome_now(ns._welcome_q.get_nowait())
                except _q.Empty:
                    break
            sub = db.collection(ns.C_SUBS)._docs[ns._subscriber_id("fan@example.com")]
            self.assertEqual(sub["welcomeEmailStatus"], "sent")
            self.assertIn(["fan@example.com"], sent, "the welcome really went out")
            self.assertIn([ADMIN], sent, "and Tim was notified")
        finally:
            ne._smtp_connect = orig_connect
            ne._smtp_close()


# ══════════════════════════════════════════════════════════════════════════
# 12. REAL SMTP, OVER A REAL SOCKET
# ══════════════════════════════════════════════════════════════════════════
# Everything above stubs the transport. This does not: it stands up an actual
# SMTP server on 127.0.0.1, points the REAL send_email() at it through the REAL
# smtplib, and asserts on the bytes that genuinely crossed the wire.
#
# It exists because a mock proves the code calls what you told it to call, and
# nothing more. Only a socket proves the EHLO/AUTH/MAIL/RCPT/DATA conversation
# actually completes, that the envelope carries exactly one recipient, and that
# the message on the wire is the message we think we built.
class _TinySMTP(threading.Thread):
    """A minimal but real SMTP server. Python 3.12 removed the stdlib `smtpd`
    module, and pulling in aiosmtpd for one test is not worth a dependency, so
    this speaks just enough of RFC 5321 for smtplib to complete a session."""

    daemon = True

    def __init__(self):
        super().__init__()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))       # port 0 = let the OS pick a free one
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.sessions = []                     # one entry per delivered message
        self.connections = 0
        self.authed = []
        self._stop = False

    def run(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        f = conn.makefile("rb")

        def say(line):
            conn.sendall(line.encode("ascii") + b"\r\n")

        say("220 test.local ESMTP")
        mail_from, rcpts = "", []
        try:
            while True:
                raw = f.readline()
                if not raw:
                    return
                line = raw.decode("utf-8", "replace").strip()
                up = line.upper()
                if up.startswith("EHLO") or up.startswith("HELO"):
                    say("250-test.local")
                    say("250-AUTH PLAIN LOGIN")
                    say("250 SMTPUTF8")
                elif up.startswith("AUTH PLAIN"):
                    self.authed.append("PLAIN")
                    say("235 2.7.0 Authentication successful")
                elif up.startswith("AUTH LOGIN"):
                    say("334 VXNlcm5hbWU6")
                    f.readline()
                    say("334 UGFzc3dvcmQ6")
                    f.readline()
                    self.authed.append("LOGIN")
                    say("235 2.7.0 Authentication successful")
                elif up.startswith("MAIL FROM"):
                    mail_from = line.split(":", 1)[1].strip().strip("<>")
                    say("250 2.1.0 OK")
                elif up.startswith("RCPT TO"):
                    rcpts.append(line.split(":", 1)[1].strip().strip("<>"))
                    say("250 2.1.5 OK")
                elif up == "DATA":
                    say("354 End data with <CR><LF>.<CR><LF>")
                    chunks = []
                    while True:
                        d = f.readline()
                        if not d or d in (b".\r\n", b".\n"):
                            break
                        # Undo SMTP dot-stuffing so the captured body is the
                        # real message.
                        chunks.append(d[1:] if d.startswith(b"..") else d)
                    self.sessions.append({
                        "from": mail_from, "rcpts": list(rcpts),
                        "data": b"".join(chunks).decode("utf-8", "replace"),
                    })
                    mail_from, rcpts = "", []
                    say("250 2.0.0 Ok: queued")
                elif up == "NOOP":
                    say("250 2.0.0 OK")
                elif up == "RSET":
                    mail_from, rcpts = "", []
                    say("250 2.0.0 OK")
                elif up == "QUIT":
                    say("221 2.0.0 Bye")
                    return
                else:
                    say("250 2.0.0 OK")
        except (OSError, ValueError):
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def shutdown(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass


class TestRealSmtpOverASocket(_EnvSandbox):
    @classmethod
    def setUpClass(cls):
        cls.server = _TinySMTP()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        super().setUp()
        self.server.sessions.clear()
        self.server.authed.clear()
        self.server.connections = 0
        os.environ.update(
            SMTP_HOST="127.0.0.1", SMTP_PORT=str(self.server.port),
            SMTP_SECURITY="none",              # a plaintext loopback socket
            SMTP_USERNAME=ADMIN, SMTP_PASSWORD="app-password-here",
        )
        ne._smtp_close()

    def tearDown(self):
        ne._smtp_close()
        super().tearDown()

    def test_a_real_welcome_email_crosses_the_wire_intact(self):
        unsub = "https://play.currentsandcritters.com/newsletter/unsubscribe/tok.mac"
        msg = ne.build_welcome(unsub, unsub)
        res = ne.send_email(to_email="Fan@Example.COM", subject=msg["subject"],
                            html_body=msg["html"], text_body=msg["text"],
                            unsubscribe_url=unsub, one_click_url=unsub)

        self.assertEqual(len(self.server.sessions), 1, "exactly one message delivered")
        s = self.server.sessions[0]

        # ── envelope ────────────────────────────────────────────────────
        self.assertEqual(s["rcpts"], ["fan@example.com"],
                         "one envelope recipient, lowercased, nobody can see anybody else")
        self.assertEqual(s["from"], ADMIN)
        self.assertTrue(self.server.authed, "the server really did authenticate us")

        # ── parse what actually arrived, as a mail client would ─────────
        import email as _email
        parsed = _email.message_from_string(s["data"])
        self.assertEqual(parsed["To"], "fan@example.com")
        self.assertEqual(parsed["Subject"],
                         "Welcome to the Currents & Critters Community!")
        self.assertIn("Currents", parsed["From"])
        self.assertIn(ADMIN, parsed["From"])
        self.assertEqual(parsed["Reply-To"], ADMIN)
        self.assertEqual(parsed["Message-ID"], res["messageId"])
        self.assertIn(unsub, parsed["List-Unsubscribe"])
        self.assertEqual(parsed["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        self.assertEqual(parsed["Precedence"], "bulk")
        self.assertTrue(parsed.is_multipart())

        parts = parsed.get_payload()
        self.assertEqual([p.get_content_type() for p in parts],
                         ["text/plain", "text/html"],
                         "plain first: multipart/alternative is last-part-wins")

        text = parts[0].get_payload(decode=True).decode("utf-8")
        html = parts[1].get_payload(decode=True).decode("utf-8")

        # ── the copy Tim wrote, and the footer the law wants ────────────
        for phrase in ("Hi!!!", "Thank you for joining the Currents & Critters email list",
                       "Timothy Honey", "Bearded Seal Studios LLC",
                       "916A South Douglas Avenue", "Nashville, TN 37204-2021",
                       "You received this email because you signed up"):
            self.assertIn(phrase, text, "missing from the plain-text part: " + phrase)
        self.assertIn("Hi!!!", html)
        self.assertIn("916A South Douglas Avenue", html)
        self.assertIn(unsub, html)
        self.assertIn(unsub, text)
        self.assertIn("email-logo.png", html, "the logo is in the header")
        self.assertNotIn("<p", text, "the text part is prose, not stripped tags")

    def test_five_sends_reuse_one_connection_and_stay_separate(self):
        for i in range(5):
            ne.send_email(to_email="p%d@x.com" % i, subject="Newsletter %d" % i,
                          html_body="<p>Body %d</p>" % i, text_body="Body %d" % i,
                          unsubscribe_url="https://u/%d" % i)
        self.assertEqual(len(self.server.sessions), 5)
        self.assertEqual(self.server.connections, 1,
                         "one TCP session for the whole batch, not five")
        for i, s in enumerate(self.server.sessions):
            self.assertEqual(s["rcpts"], ["p%d@x.com" % i])
            # The decisive privacy check, on the real bytes: no other
            # subscriber's address appears anywhere in this message.
            for j in range(5):
                if j != i:
                    self.assertNotIn("p%d@x.com" % j, s["data"],
                                     "recipient %d leaked into %d's copy" % (j, i))
            self.assertIn("https://u/%d" % i, s["data"],
                          "each recipient gets their OWN unsubscribe link")

    def test_connection_status_against_a_real_server(self):
        st = ne.connection_status()
        self.assertEqual(st["transport"], "smtp")
        self.assertTrue(st["connected"], st.get("error"))
        self.assertTrue(st["senderVerified"])
        self.assertEqual(st["error"], "")
        self.assertNotIn("app-password-here", repr(st),
                         "the password never leaves the server")

    def test_a_wrong_password_fails_closed_and_says_why(self):
        # Point at a server that refuses AUTH.
        rejecting = _TinySMTP()
        original_serve = rejecting._serve

        def refuse(conn):
            f = conn.makefile("rb")
            conn.sendall(b"220 test.local ESMTP\r\n")
            while True:
                raw = f.readline()
                if not raw:
                    return
                up = raw.decode("utf-8", "replace").strip().upper()
                if up.startswith("EHLO") or up.startswith("HELO"):
                    conn.sendall(b"250-test.local\r\n250 AUTH PLAIN LOGIN\r\n")
                elif up.startswith("AUTH"):
                    conn.sendall(b"535 5.7.8 Username and Password not accepted\r\n")
                elif up == "QUIT":
                    conn.sendall(b"221 Bye\r\n")
                    return
                else:
                    conn.sendall(b"250 OK\r\n")

        rejecting._serve = refuse
        rejecting.start()
        try:
            os.environ["SMTP_PORT"] = str(rejecting.port)
            ne._smtp_close()
            with self.assertRaises(ne.SendError) as cm:
                ne.send_email(to_email="a@b.com", subject="s",
                              html_body="<p>x</p>", text_body="x")
            self.assertEqual(cm.exception.category, "auth_revoked")
            self.assertFalse(cm.exception.retryable,
                             "a wrong password must not be retried per recipient")
            self.assertIn("App Password", str(cm.exception))
            st = ne.connection_status()
            self.assertFalse(st["connected"])
            self.assertIn("App Password", st["error"])
        finally:
            rejecting.shutdown()

    def test_a_dead_server_is_retryable_and_names_the_fallback(self):
        dead = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        dead.bind(("127.0.0.1", 0))
        port = dead.getsockname()[1]
        dead.close()                            # nothing is listening now
        os.environ["SMTP_PORT"] = str(port)
        ne._smtp_close()
        with self.assertRaises(ne.SendError) as cm:
            ne.send_email(to_email="a@b.com", subject="s",
                          html_body="<p>x</p>", text_body="x")
        self.assertEqual(cm.exception.category, "network")
        self.assertTrue(cm.exception.retryable)
        self.assertIn("NEWSLETTER_API_KEY", str(cm.exception),
                      "a blocked port must point at the HTTP fallback")

    def test_a_whole_campaign_goes_out_over_the_real_socket(self):
        """Signup → welcome → campaign, end to end, with a real server on the
        other end and no Google variable set anywhere."""
        db = FakeDb()
        ns.init(get_firestore=lambda: db, verify_token=_verify)
        ns._invalidate_subs_cache()
        ne._smtp_close()

        for i in range(3):
            ev = {"id": "evt_r%d" % i, "type": "checkout.session.completed",
                  "data": {"object": {
                      "id": "cs_r%d" % i, "payment_status": "paid", "custom_fields": [{
                          "label": {"type": "custom",
                                    "custom": "Enter your email to get updates"},
                          "type": "text", "text": {"value": "sub%d@x.com" % i}}]}}}
            self.assertEqual(ns.handle_stripe_session(ev, ev["data"]["object"]), "created")
        import queue as _q
        while True:
            try:
                ns._send_welcome_now(ns._welcome_q.get_nowait())
            except _q.Empty:
                break
        ns._invalidate_subs_cache()

        # 3 welcomes + 3 owner notifications
        self.assertEqual(len(self.server.sessions), 6)

        h = Handler()
        body = {"idToken": fake_token(ADMIN), "subject": "Reef Report",
                "contentHtml": "<h2>News</h2><p>Hello</p>"}
        ns.handle_post(h, Parsed("/api/newsletter/campaign-save"), body)
        cid = h.last["id"]
        h2 = Handler()
        ns.handle_post(h2, Parsed("/api/newsletter/campaign-start"),
                       {"idToken": fake_token(ADMIN), "id": cid, "confirm": "SEND"})
        self.assertTrue(h2.last["ok"], h2.last)

        for _ in range(10):
            for c in ns._sending_campaign_ids():
                ns._process_campaign_batch(c)
            if not ns._sending_campaign_ids():
                break

        camp = ns._get_campaign(cid)
        self.assertEqual(camp["status"], ns.CAMP_SENT)
        self.assertEqual(camp["sentCount"], 3)

        campaign_msgs = [s for s in self.server.sessions
                         if "Reef Report" in s["data"]]
        self.assertEqual(len(campaign_msgs), 3)
        got = sorted(r for s in campaign_msgs for r in s["rcpts"])
        self.assertEqual(got, ["sub0@x.com", "sub1@x.com", "sub2@x.com"])
        self.assertEqual(len(got), len(set(got)), "nobody got two copies")
        links = set()
        for s in campaign_msgs:
            import re as _re
            m = _re.search(r"newsletter/unsubscribe/([A-Za-z0-9_.\-]+)", s["data"])
            self.assertIsNotNone(m, "every campaign email carries an unsubscribe link")
            links.add(m.group(1))
        self.assertEqual(len(links), 3, "each link is unique to its recipient")


# ══════════════════════════════════════════════════════════════════════════
# 13. DELIVERABILITY: staying out of the spam folder
# ══════════════════════════════════════════════════════════════════════════
# Everything here is about the difference between a message being SENT and a
# message being READ. None of it changes what the newsletter says.
class TestDeliverabilityHeaders(unittest.TestCase):
    def raw(self, **kw):
        import base64
        kw.setdefault("to_email", "a@b.com")
        kw.setdefault("subject", "Subject here")
        kw.setdefault("html_body", "<p>hi</p>")
        kw.setdefault("text_body", "hi")
        raw, _mid = ne.build_mime(**kw)
        return base64.urlsafe_b64decode(raw).decode("utf-8", "replace")

    def test_bulk_mail_names_its_list_and_its_feedback_bucket(self):
        msg = self.raw(unsubscribe_url="https://x/newsletter/unsubscribe/t.k",
                       one_click_url="https://x/newsletter/unsubscribe/t.k",
                       stream="campaign-abc123")
        self.assertIn("List-Id:", msg)
        self.assertIn("Feedback-ID: campaign-abc123:", msg,
                      "Postmaster Tools groups complaint rates by this")
        self.assertIn("X-Entity-Ref-ID:", msg)

    def test_the_feedback_id_is_never_folded_onto_a_second_line(self):
        """Folding is legal and every parser unfolds it, but this header has
        exactly one consumer (Google Postmaster Tools) whose parser is not
        ours to test, and a very long campaign id is what pushes it over."""
        for stream in ("welcome", "campaign-" + "x" * 60):
            msg = self.raw(unsubscribe_url="https://x/u/t.k", stream=stream)
            line = [l for l in msg.splitlines() if l.startswith("Feedback-ID:")][0]
            self.assertLessEqual(len(line), 78, line)
            self.assertTrue(line.split(":", 1)[1].strip(),
                            "the value is on the header line, not folded below it")

    def test_the_feedback_id_keeps_the_sender_id_last(self):
        """Four colon-separated fields, and Google reads the LAST as the
        sender. A colon inside the bucket name would shift it out of place and
        silently split this sender's reputation in two."""
        msg = self.raw(unsubscribe_url="https://x/u/t.k", stream="we:ird:name")
        line = [l for l in msg.splitlines() if l.startswith("Feedback-ID:")][0]
        fields = line.split(":", 1)[1].strip().split(":")
        self.assertEqual(len(fields), 4, line)
        self.assertEqual(fields[-1], ne._sender_id())

    def test_a_personal_email_is_not_dressed_as_bulk(self):
        """The owner notification is one message to one person who did not
        subscribe to anything. Marking it bulk, or giving it a List-Id, is a
        lie about what it is."""
        msg = self.raw(is_bulk=False)
        self.assertNotIn("List-Id:", msg)
        self.assertNotIn("Precedence: bulk", msg)
        self.assertNotIn("Feedback-ID:", msg)

    def test_every_message_is_threaded_separately(self):
        ids = {self.raw().split("X-Entity-Ref-ID:")[1].split("\n")[0].strip()
               for _ in range(20)}
        self.assertEqual(len(ids), 20,
                         "a shared ref id is how a monthly newsletter ends up "
                         "collapsed under 'older messages' and unread")

    def test_the_http_transport_sends_the_same_headers_as_smtp(self):
        """The trap this closes: the two transports used to build these
        separately, so switching to an email API silently dropped half of
        them, and nothing in the product looks any different when it does."""
        http = ne._deliverability_headers(
            unsubscribe_url="https://x/u/t.k", one_click_url="https://x/u/t.k",
            is_bulk=True, stream="welcome")
        mime = self.raw(unsubscribe_url="https://x/u/t.k",
                        one_click_url="https://x/u/t.k", stream="welcome")
        for name in ("List-Unsubscribe", "List-Unsubscribe-Post", "List-Id",
                     "Precedence", "Feedback-ID", "X-Entity-Ref-ID"):
            self.assertIn(name, http, name)
            self.assertIn(name + ":", mime, name)


class TestSpamPreflight(unittest.TestCase):
    def check(self, **kw):
        kw.setdefault("subject", "A perfectly ordinary subject")
        kw.setdefault("content_html",
                      "<p>Here is a normal paragraph of real writing with enough "
                      "words in it that nothing looks thin or suspicious to a "
                      "filter reading the message.</p>")
        kw.setdefault("preview_text", "A normal preview line.")
        return ne.spam_preflight(**kw)

    def titles(self, res):
        return " | ".join(f["title"] for f in res["findings"])

    def test_an_ordinary_newsletter_passes_clean(self):
        """A check that fires on every honest draft is a check nobody reads."""
        res = self.check()
        self.assertTrue(res["ok"], self.titles(res))
        self.assertEqual(res["findings"], [])

    def test_it_catches_the_classic_spam_shapes(self):
        res = self.check(subject="RE: ACT NOW!! FREE MONEY GUARANTEED!!",
                         content_html='<p>hi</p><a href="http://bit.ly/x">go</a>')
        t = self.titles(res).lower()
        self.assertFalse(res["ok"])
        self.assertIn("capital letters", t)
        self.assertIn("exclamation", t)
        self.assertIn("re: or fwd:", t)
        self.assertIn("spam-filter phrases", t)
        self.assertIn("shortened links", t)
        self.assertIn("http://", t)

    def test_an_image_only_email_is_flagged(self):
        """Alt text is not body copy. Counting it would let the one email this
        check exists for, a single picture and nothing else, report a word of
        text it does not have and slip past."""
        res = self.check(content_html='<img src="https://x/a.png" alt="a">')
        self.assertFalse(res["ok"])
        self.assertIn("no text at all", self.titles(res).lower())

    def test_a_missing_subject_is_flagged(self):
        self.assertFalse(self.check(subject="   ")["ok"])

    def test_it_only_ever_advises(self):
        """It must never be able to block a send. The worst finding is a
        warning, because a false positive that stops Tim's own newsletter is a
        worse bug than the spam folder."""
        res = self.check(subject="FREE MONEY!!! ACT NOW!!!")
        self.assertTrue(res["findings"])
        for f in res["findings"]:
            self.assertIn(f["level"], ("warn", "note"))
            self.assertTrue(f["detail"], "every finding says WHY, or it is noise")


class TestDomainAuth(unittest.TestCase):
    """The SPF/DKIM/DMARC check. Every lookup is stubbed: a test that depends
    on live DNS fails on a plane."""

    def setUp(self):
        self._real = ne._txt_records
        ne._DNS_CACHE.update({"at": 0.0, "domain": "", "result": None})

    def tearDown(self):
        ne._txt_records = self._real
        ne._DNS_CACHE.update({"at": 0.0, "domain": "", "result": None})

    def stub(self, table):
        # google.com is the reachability probe; answering it keeps the check
        # from short-circuiting into "could not reach DNS".
        table.setdefault("google.com", ["v=spf1 -all"])
        ne._txt_records = lambda name, timeout=6: table.get(name, [])

    def test_a_fully_configured_domain_reads_ready(self):
        dom = ne.sender_domain()
        self.stub({
            dom: ["v=spf1 include:_spf.google.com ~all"],
            "google._domainkey." + dom: ["v=DKIM1; k=rsa; p=AAAA"],
            "_dmarc." + dom: ["v=DMARC1; p=quarantine; rua=mailto:x@y.com"],
        })
        out = ne.domain_auth_status(force=True)
        self.assertTrue(out["ready"], out["summary"])
        self.assertTrue(out["spf"]["ok"])
        self.assertTrue(out["dkim"]["ok"])
        self.assertEqual(out["dmarc"]["policy"], "quarantine")

    def test_a_missing_dmarc_record_is_reported(self):
        dom = ne.sender_domain()
        self.stub({dom: ["v=spf1 include:_spf.google.com ~all"]})
        out = ne.domain_auth_status(force=True)
        self.assertFalse(out["ready"])
        self.assertFalse(out["dmarc"]["ok"])
        self.assertIn("DMARC", out["summary"])

    def test_two_spf_records_are_caught(self):
        """A domain with two SPF records fails SPF outright, and it looks
        completely harmless sitting in a DNS panel."""
        dom = ne.sender_domain()
        self.stub({dom: ["v=spf1 include:_spf.google.com ~all",
                         "v=spf1 include:mailgun.org ~all"],
                   "_dmarc." + dom: ["v=DMARC1; p=none"]})
        out = ne.domain_auth_status(force=True)
        self.assertFalse(out["spf"]["ok"])
        self.assertIn("more than one", out["spf"]["detail"])

    def test_an_unknown_dkim_selector_is_never_called_a_failure(self):
        """Selectors cannot be enumerated from outside, so "not on any name I
        know" is genuinely not the same statement as "you have no DKIM", and
        reporting it as one sends Tim to fix something already correct."""
        dom = ne.sender_domain()
        self.stub({dom: ["v=spf1 ~all"], "_dmarc." + dom: ["v=DMARC1; p=none"]})
        out = ne.domain_auth_status(force=True)
        self.assertFalse(out["dkim"]["ok"])
        self.assertTrue(out["ready"], "DKIM alone must not fail the domain")
        self.assertIn("does not prove DKIM is missing", out["dkim"]["detail"])

    def test_unreachable_dns_is_not_reported_as_a_broken_domain(self):
        ne._txt_records = lambda name, timeout=6: []
        out = ne.domain_auth_status(force=True)
        self.assertFalse(out["dnsReachable"])
        self.assertIn("check that failed", out["summary"])
        self.assertIsNone(ne._DNS_CACHE["result"],
                          "a failed check must not be cached as an answer")

    def test_the_result_is_cached(self):
        dom = ne.sender_domain()
        calls = []
        table = {"google.com": ["v=spf1 -all"], dom: ["v=spf1 ~all"],
                 "_dmarc." + dom: ["v=DMARC1; p=none"]}

        def counting(name, timeout=6):
            calls.append(name)
            return table.get(name, [])

        ne._txt_records = counting
        ne.domain_auth_status(force=True)
        n = len(calls)
        ne.domain_auth_status()
        self.assertEqual(len(calls), n, "an admin page re-render is not a DNS storm")


class TestBounceSuppression(Base):
    def test_a_permanently_rejected_address_comes_off_the_list(self):
        """A dead address re-mailed on every campaign is what a bought list
        looks like from the outside, and mailbox providers score it that way
        against every OTHER message the domain sends."""
        self.signup("real@x.com")
        self.box.messages.clear()
        h = Handler("7.7.7.6")
        ns.handle_post(h, Parsed("/api/newsletter/subscribe"), {"email": "typo@nowhere.invalid"})
        self.box.fail_with = ne.SendError("no such user", category="invalid_recipient",
                                          retryable=False)
        self.box.fail_times = -1                # every send fails
        self.drain()
        self.box.fail_with = None
        ns._invalidate_subs_cache()

        sub = self.sub_for("typo@nowhere.invalid")
        self.assertEqual(sub["status"], ns.STATUS_UNSUB)
        self.assertTrue(sub["bounced"])
        self.assertIn("subscriber_bounced",
                      [a["action"] for a in self.admin_post("audit", {"limit": 50}).last["rows"]],
                      "and it is recorded as a bounce, not as an opt-out")

    def test_a_temporary_failure_does_not_remove_anybody(self):
        """The distinction that matters: a network blip is the network's
        problem, not the address's, and removing somebody over one would lose
        a real subscriber for good."""
        h = Handler("7.7.7.7")
        ns.handle_post(h, Parsed("/api/newsletter/subscribe"), {"email": "fine@x.com"})
        self.box.fail_with = ne.SendError("timeout", category="network", retryable=True)
        self.box.fail_times = -1
        for _ in range(ns.MAX_ATTEMPTS + 2):
            ns._send_welcome_now(ns._subscriber_id("fine@x.com"))
        self.box.fail_with = None
        ns._invalidate_subs_cache()
        sub = self.sub_for("fine@x.com")
        self.assertEqual(sub["status"], ns.STATUS_ACTIVE, "still a subscriber")
        self.assertFalse(sub.get("bounced"))


# ══════════════════════════════════════════════════════════════════════════
# 14. THE THINGS THAT MADE "EMAILS ARE NOT SENDING" HARD TO SEE
# ══════════════════════════════════════════════════════════════════════════
class TestStuckWelcomes(Base):
    """A welcome claimed for sending and never finished.

    This is the one that silently ate welcome emails. _claim_welcome flips the
    record to "sending" and takes a lease; _finish_welcome moves it on. A
    deploy stops the container between those two EVERY TIME one ships, and
    _pending_welcome_ids only ever asks for "pending", so the record sat in
    "sending" forever: never retried, never failed, never counted, and the
    person simply never heard from us.
    """

    def strand(self, email, *, attempts=1, lease_until=0):
        self.signup(email)
        sid = ns._subscriber_id(email)
        ns._db().collection(ns.C_SUBS).document(sid).set(
            {"welcomeEmailStatus": "sending", "welcomeAttempts": attempts,
             "welcomeLeaseUntil": lease_until}, merge=True)
        ns._invalidate_subs_cache()
        self.box.messages.clear()
        return sid

    def test_a_stranded_welcome_is_invisible_to_the_old_queue(self):
        """Proves the bug exists at all: without the sweep, nothing finds it."""
        sid = self.strand("lost@x.com")
        self.assertNotIn(sid, ns._pending_welcome_ids(),
                         "this is why it was never retried")

    def test_the_sweep_re_queues_it_and_the_welcome_actually_sends(self):
        sid = self.strand("lost@x.com")
        self.assertEqual(ns._reclaim_stuck_welcomes(), 1)
        self.assertIn(sid, ns._pending_welcome_ids())
        for s in ns._pending_welcome_ids():
            ns._send_welcome_now(s)
        self.assertEqual(len(self.box.to("lost@x.com")), 1,
                         "the person finally gets the welcome they were owed")

    def test_a_send_still_in_flight_is_never_disturbed(self):
        """The lease is the whole point: re-queueing a live send would mail the
        same person twice."""
        self.strand("inflight@x.com", lease_until=ns._now() + 120)
        self.assertEqual(ns._reclaim_stuck_welcomes(), 0)
        self.assertEqual(
            self.sub_for("inflight@x.com")["welcomeEmailStatus"], "sending")

    def test_a_welcome_that_keeps_dying_is_failed_not_looped_forever(self):
        """If every attempt dies in the same place, re-queueing it forever is an
        infinite loop that hides the real fault instead of reporting it."""
        self.strand("cursed@x.com", attempts=ns.MAX_ATTEMPTS)
        ns._reclaim_stuck_welcomes()
        sub = self.sub_for("cursed@x.com")
        self.assertEqual(sub["welcomeEmailStatus"], "failed")
        self.assertIn("giving up", sub["welcomeError"])

    def test_the_dashboard_reports_stuck_welcomes(self):
        self.strand("lost@x.com")
        ns._invalidate_subs_cache()
        self.assertEqual(self.admin_post("dashboard").last["stuckWelcome"], 1,
                         "a state nothing counted is a state nobody fixes")


class TestSendHealth(Base):
    def test_a_failing_transport_is_reported_as_unhealthy(self):
        with self.transport_configured():
            ne._note_send_fail(ne.SendError("bad password", category="auth_revoked"),
                               "auth_revoked")
            h = ns.send_health()
            self.assertFalse(h["healthy"])
            self.assertIn("bad password", h["summary"])

    def test_a_healthy_send_clears_it(self):
        with self.transport_configured():
            ne._note_send_fail(Exception("boom"), "network")
            ne._note_send_ok()
            h = ne.send_health()
            self.assertTrue(h["healthy"])
            self.assertEqual(h["consecutiveFailures"], 0)

    def test_nothing_tried_yet_is_never_dressed_up_as_a_pass(self):
        """"We have not tried" is not evidence that sending works, and saying
        so would be exactly the false reassurance this whole section exists to
        remove."""
        with ne._HEALTH_LOCK:
            ne._HEALTH.update({"lastOkAt": 0, "lastFailAt": 0, "consecutiveFailures": 0,
                               "sentSinceBoot": 0, "lastError": ""})
        with self.transport_configured():
            h = ne.send_health()
            self.assertFalse(h["everTried"])
            self.assertIn("no delivery result to report", h["summary"])

    def test_an_unconfigured_transport_says_nothing_is_being_delivered(self):
        h = ne.send_health()
        self.assertFalse(h["healthy"])
        self.assertIn("NOTHING is being delivered", h["summary"])

    def test_a_failure_in_the_same_second_as_a_success_still_reads_as_failed(self):
        """Found by the suite, not by reading the code: these are unix SECONDS,
        so a send that fails right after one succeeds shares a timestamp with
        it, and a tie broken by ">" reports the system as healthy. Healthy is
        the one direction this must never guess in."""
        ne._note_send_ok()
        ne._note_send_fail(ne.SendError("late failure", category="network"), "network")
        ns._HEALTH_MIRROR.update({"ok": 0, "fail": 0})
        ns._mirror_send_health()
        doc = self.db.collection(ns.C_META)._docs["sendHealth"]
        self.assertEqual(doc["lastOkAt"], doc["lastFailAt"], "the tie this is about")
        with ne._HEALTH_LOCK:
            ne._HEALTH.update({"lastOkAt": 0, "lastFailAt": 0, "lastError": "",
                               "lastOutcome": "", "consecutiveFailures": 0})
        with self.transport_configured():
            self.assertFalse(ns.send_health()["healthy"])

    def test_a_failure_survives_the_restart_that_hid_it(self):
        """In-memory health is wiped by every deploy, which is precisely when
        somebody goes looking for what went wrong."""
        ne._note_send_fail(ne.SendError("nope", category="auth_revoked"), "auth_revoked")
        ns._HEALTH_MIRROR.update({"ok": 0, "fail": 0})
        ns._mirror_send_health()
        # Simulate the restart: this process now knows nothing.
        with ne._HEALTH_LOCK:
            ne._HEALTH.update({"lastOkAt": 0, "lastFailAt": 0, "lastError": "",
                               "consecutiveFailures": 0})
        with self.transport_configured():
            h = ns.send_health()
            self.assertFalse(h["healthy"])
            self.assertTrue(h["fromPreviousRun"])
            self.assertIn("nope", h["summary"])


class TestHonestSignupReply(Base):
    def test_it_does_not_promise_mail_it_cannot_send(self):
        """The signup still succeeds, because a broken SMTP setting is not the
        subscriber's problem, but telling them an email is on its way when
        nothing can leave the building is how a dead mail system stays
        invisible."""
        h = Handler("6.6.6.1")
        ns.handle_post(h, Parsed("/api/newsletter/subscribe"), {"email": "a@x.com"})
        self.assertTrue(h.last["ok"])
        self.assertNotIn("on its way", h.last["message"])
        self.assertEqual(self.sub_for("a@x.com")["status"], ns.STATUS_ACTIVE,
                         "they are still genuinely subscribed")

    def test_it_does_promise_mail_when_sending_works(self):
        with self.transport_configured():
            h = Handler("6.6.6.2")
            ns.handle_post(h, Parsed("/api/newsletter/subscribe"), {"email": "b@x.com"})
            self.assertIn("on its way", h.last["message"])

    def test_the_reply_is_still_identical_for_every_address(self):
        """The anti-enumeration property must survive the new wording."""
        with self.transport_configured():
            self.signup("known@x.com")
            a = Handler("6.6.6.3")
            ns.handle_post(a, Parsed("/api/newsletter/subscribe"), {"email": "known@x.com"})
            b = Handler("6.6.6.4")
            ns.handle_post(b, Parsed("/api/newsletter/subscribe"), {"email": "new@x.com"})
            self.assertEqual(a.last, b.last)


class TestSignupFastPath(Base):
    def test_a_new_address_is_created_with_a_single_write(self):
        """Three Firestore round trips became one, with somebody watching a
        spinner for every one of them."""
        out = ns.request_subscription("fast@x.com")
        self.assertEqual(out["result"], "created")
        self.assertTrue(out["sendWelcome"])
        sub = self.sub_for("fast@x.com")
        self.assertEqual(sub["status"], ns.STATUS_ACTIVE)
        self.assertEqual(sub["welcomeEmailStatus"], ns.WELCOME_PENDING)
        self.assertTrue(sub["unsubId"], "a usable unsubscribe token was still built")

    def test_the_fast_path_really_is_the_one_being_taken(self):
        """Guards the trap this nearly shipped with: the fake Firestore had no
        create(), so the fast path fell through to the transaction in every
        test and would have gone out completely unexercised."""
        calls = []
        real = ns._subscribe
        ns._subscribe = lambda *a, **k: calls.append(1) or real(*a, **k)
        try:
            ns.request_subscription("nofallback@x.com")
            self.assertEqual(calls, [], "a brand-new address must not need a transaction")
        finally:
            ns._subscribe = real

    def test_a_collision_falls_through_and_stays_correct(self):
        """A returning address is the case create() cannot handle, and the
        transaction must still do the right thing with it."""
        self.signup("back@x.com")
        ns.unsubscribe_with_token(ns.make_unsub_token(self.sub_for("back@x.com")))
        self.assertEqual(self.sub_for("back@x.com")["status"], ns.STATUS_UNSUB)
        out = ns.request_subscription("back@x.com")
        self.assertEqual(out["result"], "reactivated")
        self.assertTrue(out["sendWelcome"])
        self.assertEqual(self.sub_for("back@x.com")["status"], ns.STATUS_ACTIVE)

    def test_signing_up_twice_still_makes_exactly_one_subscriber(self):
        ns.request_subscription("dupe@x.com")
        out = ns.request_subscription("dupe@x.com")
        self.assertEqual(out["result"], "already_active")
        self.assertFalse(out["sendWelcome"], "and no second welcome")
        self.assertEqual(len(self.subs()), 1)


class TestSelfTest(Base):
    def test_it_sends_a_real_email_and_says_so(self):
        with self.transport_configured():
            r = self.admin_post("self-test").last
        self.assertTrue(r["ok"], r)
        self.assertTrue(self.box.to(ADMIN), "a real message actually went out")
        self.assertTrue(all(s["ok"] for s in r["steps"]), r["steps"])

    def test_an_unconfigured_transport_is_named_as_the_whole_problem(self):
        """With nothing configured it must not waste Tim's time on the other
        steps: this one fact explains every missing email."""
        r = self.admin_post("self-test").last
        self.assertFalse(r["ok"])
        self.assertIn("nothing can be sent at all", r["error"])
        self.assertEqual(len(r["steps"]), 1)

    def test_it_reports_the_providers_own_words_on_failure(self):
        """"Sending failed" is not actionable. "The mail server rejected the
        username/password" is."""
        self.box.fail_with = ne.SendError(
            "The mail server rejected the username/password.",
            category="auth_revoked", retryable=False)
        self.box.fail_times = -1
        with self.transport_configured():
            r = self.admin_post("self-test").last
        self.assertFalse(r["ok"])
        self.assertIn("username/password", r["error"])
        self.assertEqual(r["category"], "auth_revoked")
        failed = [s for s in r["steps"] if not s["ok"]]
        self.assertEqual(len(failed), 1, "it names the exact step that broke")

    def test_it_never_sends_anywhere_but_the_admin(self):
        """A test send that accepts a destination is a spam relay wearing an
        admin login."""
        with self.transport_configured():
            self.admin_post("self-test", {"to": "victim@elsewhere.com",
                                          "email": "victim@elsewhere.com"})
        self.assertEqual(self.box.to("victim@elsewhere.com"), [])

    def test_a_stranger_cannot_run_it(self):
        with self.transport_configured():
            h = self.admin_post("self-test", email="nobody@example.com")
        self.assertFalse(h.last.get("ok"))
        self.assertEqual(self.box.messages, [])


class TestWelcomeDoesNotScanTheWholeList(Base):
    def test_sending_a_welcome_does_not_read_every_subscriber(self):
        """It used to: _finish_welcome invalidates the subscriber cache, and
        the very next line called counts(), which reads the entire collection,
        to decorate ONE line of Tim's notification email. Every welcome
        dragged a full scan behind it."""
        self.signup("seed@x.com")
        ns._invalidate_subs_cache()
        h = Handler("6.6.6.9")
        ns.handle_post(h, Parsed("/api/newsletter/subscribe"), {"email": "scan@x.com"})

        scans = []
        real = ns._all_subscribers
        ns._all_subscribers = lambda force=False: scans.append(1) or real(force)
        try:
            self.drain()
        finally:
            ns._all_subscribers = real
        self.assertEqual(scans, [], "no collection scan on the welcome path")
        self.assertEqual(len(self.box.to("scan@x.com")), 1, "and it still sends")


if __name__ == "__main__":
    unittest.main(verbosity=2)
