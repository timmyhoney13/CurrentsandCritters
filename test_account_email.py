"""test_account_email.py, the email address on a username-and-password account.

A username here is not an email: `mermaid_92` signs in to Firebase as
`mermaid_92@players.currentsandcritters.com`, an address at a domain that
receives nothing. That is what makes "this username is taken" a real answer,
and it is also why an account had no way back in when its password was
forgotten. This module is that way back in, and these are the rules that make
it safe to have:

  1. An address is NOT linked until its confirmation link is clicked. A typo,
     or a stranger's address typed by mistake, must never become the way into
     somebody's account.
  2. The token is signed, and covers the uid AND the address, so a link for one
     account cannot confirm another, and a link cannot be edited into one for a
     different address.
  3. A link expires, and a link for an address that has since been REPLACED is
     dead even before it expires.
  4. One confirmed address per account, and one account per confirmed address.
  5. /api/account/forgot-password answers with the same sentence whatever it
     finds. A sign-in screen must not be a way to ask which usernames exist.

    python3 test_account_email.py
"""
from __future__ import annotations

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

os.environ.setdefault("ACCOUNT_EMAIL_SECRET", "test-secret-for-the-suite")

import account_email as ae  # noqa: E402

# The in-memory Firestore lives next door. Importing it rather than pasting a
# third copy is what keeps the suites testing the same Firestore semantics.
from test_level_pass_server import FakeDb  # noqa: E402


class FakeHandler:
    """Just enough of the request handler: a JSON reply, and the HTML page the
    confirmation link lands on."""
    def __init__(self):
        self.payload = None
        self.status = 200
        self.html = b""

    def _send_json(self, payload, status=200):
        self.payload = payload
        self.status = status

    def _emit_html(self, raw):
        self.html = raw


class Parsed:
    def __init__(self, path, query=""):
        self.path = path
        self.query = query


SENT: list = []


def _fake_send(to_email, msg):
    SENT.append({"to": to_email, "subject": msg.get("subject", ""),
                 "html": msg.get("html", ""), "text": msg.get("text", "")})
    return True


class Base(unittest.TestCase):
    def setUp(self):
        SENT.clear()
        self.db = FakeDb()
        ae.init(get_firestore=lambda: self.db,
                verify_token=lambda tok: {"uid": tok[5:]} if tok.startswith("tok::") else None,
                login_domain="players.currentsandcritters.com")
        ae._last_link_at.clear()
        # No real transport in a test, and no real Firebase Admin either.
        self._send = ae._send
        ae._send = _fake_send
        self._transport = ae.nl_email.transport
        ae.nl_email.transport = lambda: "smtp"

    def tearDown(self):
        ae._send = self._send
        ae.nl_email.transport = self._transport

    def account(self, uid, **fields):
        data = {"nickname": uid.title(), "login_username": uid}
        data.update(fields)
        self.db.collection("users").document(uid).set(data)

    def profile(self, uid):
        return self.db.collection("users").document(uid).get().to_dict() or {}


class TestValidation(Base):
    def test_shape(self):
        self.assertTrue(ae.valid_email("reef@example.com"))
        self.assertTrue(ae.valid_email("a.b-c_d@sub.example.co.uk"))
        self.assertFalse(ae.valid_email(""))
        self.assertFalse(ae.valid_email("reef@example"))       # no dot
        self.assertFalse(ae.valid_email("reef @example.com"))  # a space
        self.assertFalse(ae.valid_email("reef@example.com "*30))

    def test_the_synthetic_login_address_is_not_a_way_back_in(self):
        # It is the account's own login name. Accepting it would file an
        # undeliverable address as the one deliverable thing about the account.
        self.assertFalse(ae.valid_email("mermaid_92@players.currentsandcritters.com"))


class TestToken(Base):
    def test_roundtrip(self):
        tok = ae.make_token("uid1", "reef@example.com")
        self.assertEqual(ae.read_token(tok), ("uid1", "reef@example.com"))

    def test_a_tampered_token_is_refused(self):
        tok = ae.make_token("uid1", "reef@example.com")
        uid, email_b64, exp, mac = tok.split(".")
        # …a different account
        self.assertIsNone(ae.read_token("uid2.%s.%s.%s" % (email_b64, exp, mac)))
        # …a different address
        import base64
        other = base64.urlsafe_b64encode(b"thief@example.com").decode().rstrip("=")
        self.assertIsNone(ae.read_token("%s.%s.%s.%s" % (uid, other, exp, mac)))
        # …a longer life
        self.assertIsNone(ae.read_token("%s.%s.%d.%s" % (uid, email_b64, int(exp) + 99999, mac)))
        # …and noise
        self.assertIsNone(ae.read_token("nonsense"))

    def test_a_token_expires(self):
        tok = ae.make_token("uid1", "reef@example.com", now=time.time() - ae.CONFIRM_TTL_SEC - 10)
        self.assertIsNone(ae.read_token(tok))

    def test_no_secret_means_no_tokens_at_all(self):
        # Better to refuse than to hand out links anybody could forge.
        old = os.environ.get("ACCOUNT_EMAIL_SECRET")
        for k in ("ACCOUNT_EMAIL_SECRET", "NEWSLETTER_UNSUBSCRIBE_SECRET", "SESSION_SECRET"):
            os.environ.pop(k, None)
        try:
            self.assertFalse(ae.secret_configured())
            self.assertEqual(ae.make_token("uid1", "reef@example.com"), "")
            self.assertFalse(ae.link_email("uid1", "reef@example.com")["ok"])
        finally:
            if old:
                os.environ["ACCOUNT_EMAIL_SECRET"] = old


class TestLink(Base):
    def test_linking_writes_it_unconfirmed_and_mails_the_link(self):
        self.account("mermaid")
        res = ae.link_email("mermaid", "Reef@Example.com")
        self.assertTrue(res["ok"])
        p = self.profile("mermaid")
        self.assertEqual(p["recovery_email"], "reef@example.com")   # lower-cased
        self.assertIs(p["recovery_email_verified"], False)          # NOT yet
        self.assertEqual(len(SENT), 1)
        self.assertEqual(SENT[0]["to"], "reef@example.com")
        self.assertIn("/api/account/verify-email?t=", SENT[0]["html"])

    def test_a_bad_address_is_refused_before_anything_is_written(self):
        self.account("mermaid")
        self.assertFalse(ae.link_email("mermaid", "not-an-address")["ok"])
        self.assertNotIn("recovery_email", self.profile("mermaid"))
        self.assertEqual(SENT, [])

    def test_an_address_confirmed_elsewhere_is_refused(self):
        self.account("owner", recovery_email="reef@example.com", recovery_email_verified=True)
        self.account("thief")
        res = ae.link_email("thief", "reef@example.com")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "taken")
        self.assertNotIn("recovery_email", self.profile("thief"))

    def test_an_address_merely_TYPED_elsewhere_does_not_block_its_owner(self):
        # Somebody typed a stranger's address and never confirmed it. The real
        # owner must still be able to use their own address.
        self.account("typo", recovery_email="reef@example.com", recovery_email_verified=False)
        self.account("owner")
        self.assertTrue(ae.link_email("owner", "reef@example.com")["ok"])

    def test_it_is_not_a_mail_cannon(self):
        self.account("mermaid")
        self.assertTrue(ae.link_email("mermaid", "reef@example.com")["ok"])
        second = ae.link_email("mermaid", "reef@example.com")
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"], "too_soon")
        self.assertEqual(len(SENT), 1)


class TestVerify(Base):
    def test_clicking_the_link_confirms_it(self):
        self.account("mermaid")
        ae.link_email("mermaid", "reef@example.com")
        tok = SENT[0]["html"].split("verify-email?t=")[1].split('"')[0]
        res = ae.verify_email(tok)
        self.assertTrue(res["ok"])
        self.assertIs(self.profile("mermaid")["recovery_email_verified"], True)

    def test_a_link_for_a_replaced_address_is_dead(self):
        self.account("mermaid")
        ae.link_email("mermaid", "old@example.com")
        stale = SENT[0]["html"].split("verify-email?t=")[1].split('"')[0]
        ae._last_link_at.clear()
        ae.link_email("mermaid", "new@example.com")
        res = ae.verify_email(stale)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "stale")
        p = self.profile("mermaid")
        self.assertEqual(p["recovery_email"], "new@example.com")
        self.assertIs(p["recovery_email_verified"], False)

    def test_a_forged_link_confirms_nothing(self):
        self.account("mermaid", recovery_email="reef@example.com",
                     recovery_email_verified=False)
        self.assertFalse(ae.verify_email("mermaid.xxx.999.yyy")["ok"])
        self.assertIs(self.profile("mermaid")["recovery_email_verified"], False)

    def test_clicking_twice_is_not_an_error(self):
        self.account("mermaid")
        ae.link_email("mermaid", "reef@example.com")
        tok = SENT[0]["html"].split("verify-email?t=")[1].split('"')[0]
        ae.verify_email(tok)
        again = ae.verify_email(tok)
        self.assertTrue(again["ok"])
        self.assertTrue(again.get("already"))


class TestForgot(Base):
    def setUp(self):
        super().setUp()
        self.links = []
        ae_auth = type("A", (), {"generate_password_reset_link":
                                 staticmethod(lambda addr: self._link(addr))})
        mod = type("M", (), {"auth": ae_auth})
        sys.modules["firebase_admin"] = mod

    def _link(self, addr):
        self.links.append(addr)
        return "https://reset.example/?addr=" + addr

    def test_a_confirmed_address_gets_the_reset(self):
        self.account("mermaid", login_username="mermaid",
                     recovery_email="reef@example.com", recovery_email_verified=True)
        res = ae.forgot_password("mermaid")
        self.assertEqual(res["message"], ae.FORGOT_ANSWER)
        self.assertEqual(self.links, ["mermaid@players.currentsandcritters.com"])
        self.assertEqual(len(SENT), 1)
        self.assertEqual(SENT[0]["to"], "reef@example.com")
        self.assertIn("reset.example", SENT[0]["html"])

    def test_an_UNCONFIRMED_address_gets_nothing(self):
        self.account("mermaid", recovery_email="reef@example.com",
                     recovery_email_verified=False)
        res = ae.forgot_password("mermaid")
        self.assertEqual(res["message"], ae.FORGOT_ANSWER)
        self.assertEqual(SENT, [])

    def test_an_account_with_no_email_gets_nothing(self):
        self.account("mermaid")
        self.assertEqual(ae.forgot_password("mermaid")["message"], ae.FORGOT_ANSWER)
        self.assertEqual(SENT, [])

    def test_a_username_nobody_has_gets_the_same_answer(self):
        # The whole point: the reply cannot be read as "that name exists".
        self.account("mermaid", recovery_email="reef@example.com",
                     recovery_email_verified=True)
        real = ae.forgot_password("mermaid")
        SENT.clear()
        made_up = ae.forgot_password("nobody_at_all")
        self.assertEqual(real["message"], made_up["message"])
        self.assertEqual(real["ok"], made_up["ok"])
        self.assertEqual(SENT, [])

    def test_the_name_is_matched_case_insensitively(self):
        self.account("mermaid", login_username="Mermaid",
                     recovery_email="reef@example.com", recovery_email_verified=True)
        ae.forgot_password("Mermaid")
        self.assertEqual(len(SENT), 1)


class TestRouting(Base):
    def test_link_email_needs_a_real_token(self):
        self.account("mermaid")
        h = FakeHandler()
        ae.handle_post(h, Parsed("/api/account/link-email"),
                       {"idToken": "rubbish", "email": "reef@example.com"})
        self.assertEqual(h.status, 401)
        self.assertNotIn("recovery_email", self.profile("mermaid"))

    def test_link_email_acts_as_the_TOKEN_says_not_the_body(self):
        self.account("mermaid")
        self.account("thief")
        h = FakeHandler()
        ae.handle_post(h, Parsed("/api/account/link-email"),
                       {"idToken": "tok::mermaid", "uid": "thief",
                        "email": "reef@example.com"})
        self.assertTrue(h.payload["ok"])
        self.assertIn("recovery_email", self.profile("mermaid"))
        self.assertNotIn("recovery_email", self.profile("thief"))

    def test_forgot_password_is_public(self):
        h = FakeHandler()
        self.assertTrue(ae.handle_post(h, Parsed("/api/account/forgot-password"),
                                       {"username": "mermaid"}))
        self.assertTrue(h.payload["ok"])

    def test_an_unknown_account_route_is_not_swallowed(self):
        h = FakeHandler()
        self.assertFalse(ae.handle_post(h, Parsed("/api/account/nonsense"), {}))

    def test_the_confirmation_page_is_a_page(self):
        self.account("mermaid")
        ae.link_email("mermaid", "reef@example.com")
        tok = SENT[0]["html"].split("verify-email?t=")[1].split('"')[0]
        h = FakeHandler()
        self.assertTrue(ae.handle_get(h, Parsed("/api/account/verify-email", "t=" + tok)))
        self.assertIn(b"Email confirmed", h.html)
        self.assertIs(self.profile("mermaid")["recovery_email_verified"], True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
