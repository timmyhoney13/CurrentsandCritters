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



# ═══════════════════════════════════════════════════════════════════════════
#  THE RECOVERY CODE
#  The email above is optional and most players never link one, so this is the
#  way back in that needs nothing but a piece of paper. It is a SECOND
#  CREDENTIAL, so what is pinned here is what you would pin about a password:
#  that the plaintext is never stored, that a hash cannot be moved between
#  accounts, that spending one is single-use and never leaves the player
#  holding nothing, and that a wrong username and a wrong code are answered
#  with the same sentence.
# ═══════════════════════════════════════════════════════════════════════════
class CodeBase(Base):
    def setUp(self):
        super().setUp()
        ae._last_issue_at.clear()
        ae._redeem_fails.clear()
        self.passwords = {}
        fb_auth = type("A", (), {
            "update_user": staticmethod(lambda uid, **kw: self._set_pw(uid, **kw)),
            "generate_password_reset_link": staticmethod(lambda addr: "https://x/"),
            "get_user_by_email": staticmethod(lambda addr: self._by_email(addr)),
        })
        sys.modules["firebase_admin"] = type("M", (), {"auth": fb_auth})

    def _by_email(self, addr):
        """Firebase's own username -> uid mapping, which is the one signing in
        uses: the local part is the login name, always lower-cased."""
        local = str(addr or "").split("@")[0]
        for uid, row in self.db.collection("users")._docs.items():
            if str(row.get("login_username") or "").strip().lower() == local:
                return type("U", (), {"uid": uid})
        raise ValueError("no such user")

    def _set_pw(self, uid, **kw):
        self.passwords[uid] = kw.get("password")
        return True

    def give(self, uid="mermaid", **fields):
        """An account with a code on it, and the plaintext for the test."""
        self.account(uid, **fields)
        made = ae.issue_recovery_code(uid)
        self.assertTrue(made["ok"], made)
        ae._last_issue_at.clear()      # cooldown is not what any of these test
        return made["code"]


class TestCodeShape(CodeBase):
    def test_the_alphabet_has_no_lookalikes(self):
        # Read off paper, often by a child: 0/O and 1/I/L are where a
        # transcription goes wrong, so none of them can be in a code.
        for ch in "01ILO":
            self.assertNotIn(ch, ae.CODE_ALPHABET)

    def test_a_code_is_long_enough_to_be_a_credential(self):
        # 16 symbols from 31 is ~79 bits: not guessable, rate limit or no.
        self.assertEqual(ae.CODE_LEN, 16)
        self.assertGreaterEqual(len(ae.CODE_ALPHABET), 31)

    def test_codes_do_not_repeat(self):
        self.assertEqual(len({ae.make_code() for _ in range(400)}), 400)

    def test_it_is_shown_in_groups_and_read_back_without_them(self):
        raw = ae.make_code()
        shown = ae.format_code(raw)
        self.assertEqual(shown.count("-"), 3)
        self.assertEqual(ae.normalize_code(shown), raw)

    def test_people_type_it_however_they_like(self):
        raw = ae.make_code()
        shown = ae.format_code(raw)
        for typed in (shown, shown.lower(), shown.replace("-", ""),
                      shown.replace("-", " "), "  " + shown + "  ",
                      shown.replace("-", "_")):
            self.assertEqual(ae.normalize_code(typed), raw, typed)

    def test_anything_that_is_not_one_of_ours_is_refused(self):
        raw = ae.make_code()
        for bad in ("", None, "hello", raw[:-1], raw + "X", "O" + raw[1:], 12345):
            self.assertEqual(ae.normalize_code(bad), "", repr(bad))


class TestCodeHash(CodeBase):
    def test_the_plaintext_is_never_stored(self):
        code = self.give("mermaid")
        stored = self.profile("mermaid")
        blob = repr(stored)
        self.assertNotIn(ae.normalize_code(code), blob)
        self.assertNotIn(code, blob)
        self.assertTrue(stored.get("recovery_code_hash"))

    def test_a_hash_cannot_be_moved_to_another_account(self):
        # The uid is inside the MAC, so a hash lifted out of one row is not a
        # working credential when pasted into another.
        code = self.give("mermaid")
        norm = ae.normalize_code(code)
        stolen = self.profile("mermaid")["recovery_code_hash"]
        self.assertTrue(ae.code_matches("mermaid", norm, stolen))
        self.assertFalse(ae.code_matches("someone_else", norm, stolen))

    def test_a_wrong_code_does_not_match(self):
        self.give("mermaid")
        stored = self.profile("mermaid")["recovery_code_hash"]
        self.assertFalse(ae.code_matches("mermaid", ae.make_code(), stored))

    def test_with_no_secret_nothing_is_a_credential(self):
        # A code hashed with nothing must not be treated as one.
        keys = ("ACCOUNT_EMAIL_SECRET", "NEWSLETTER_UNSUBSCRIBE_SECRET", "SESSION_SECRET")
        saved = {k: os.environ.get(k) for k in keys}
        try:
            for k in keys:
                os.environ.pop(k, None)
            self.assertEqual(ae.code_hash("mermaid", ae.make_code()), "")
            self.assertFalse(ae.code_matches("mermaid", ae.make_code(), "whatever"))
            self.account("mermaid")
            self.assertFalse(ae.issue_recovery_code("mermaid")["ok"])
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class TestIssue(CodeBase):
    def test_an_account_gets_one(self):
        self.account("mermaid")
        res = ae.issue_recovery_code("mermaid")
        self.assertTrue(res["ok"])
        self.assertEqual(ae.normalize_code(res["code"]) and True, True)
        self.assertTrue(self.profile("mermaid")["recovery_code_hash"])

    def test_a_new_one_kills_the_old_one(self):
        # "I lost it, give me another" must not leave the lost one working.
        first = self.give("mermaid")
        second = ae.issue_recovery_code("mermaid")["code"]
        stored = self.profile("mermaid")["recovery_code_hash"]
        self.assertFalse(ae.code_matches("mermaid", ae.normalize_code(first), stored))
        self.assertTrue(ae.code_matches("mermaid", ae.normalize_code(second), stored))

    def test_a_google_account_is_not_offered_one(self):
        # It signs in with Google and has no password here to be locked out of.
        self.account("googler", auth_provider="google", login_username="")
        res = ae.issue_recovery_code("googler")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "not_password_account")

    def test_an_account_that_does_not_exist_gets_nothing(self):
        self.assertFalse(ae.issue_recovery_code("nobody")["ok"])

    def test_rotating_has_a_cooldown(self):
        self.account("mermaid")
        self.assertTrue(ae.issue_recovery_code("mermaid")["ok"])
        again = ae.issue_recovery_code("mermaid")
        self.assertFalse(again["ok"])
        self.assertEqual(again["error"], "too_soon")


class TestState(CodeBase):
    def test_it_never_hands_the_code_back(self):
        # The server keeps only the hash; there is nothing to hand back, and
        # this is the endpoint that would leak it if there were.
        code = self.give("mermaid")
        st = ae.recovery_code_state("mermaid")
        self.assertTrue(st["has_code"])
        self.assertTrue(st["eligible"])
        self.assertNotIn("code", st)
        self.assertNotIn(ae.normalize_code(code), repr(st))

    def test_an_account_with_none_says_so(self):
        self.account("mermaid")
        st = ae.recovery_code_state("mermaid")
        self.assertTrue(st["eligible"])
        self.assertFalse(st["has_code"])

    def test_a_google_account_is_not_eligible(self):
        self.account("googler", auth_provider="google", login_username="")
        self.assertFalse(ae.recovery_code_state("googler")["eligible"])


class TestRedeem(CodeBase):
    def test_the_right_code_sets_the_password(self):
        code = self.give("mermaid")
        res = ae.redeem_recovery_code("mermaid", code, "Reef-Tide-99")
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.passwords["mermaid"], "Reef-Tide-99")
        self.assertEqual(res["username"], "mermaid")
        self.assertEqual(res["email"], "mermaid@players.currentsandcritters.com")

    def test_it_works_once(self):
        code = self.give("mermaid")
        self.assertTrue(ae.redeem_recovery_code("mermaid", code, "Reef-Tide-99")["ok"])
        ae._redeem_fails.clear()
        again = ae.redeem_recovery_code("mermaid", code, "Other-Tide-77")
        self.assertFalse(again["ok"])
        self.assertEqual(again["message"], ae.REDEEM_ANSWER_BAD)

    def test_spending_one_hands_back_a_fresh_one(self):
        # Nobody may walk away from this screen holding nothing: the next
        # forgotten password would be the end of the account.
        code = self.give("mermaid")
        res = ae.redeem_recovery_code("mermaid", code, "Reef-Tide-99")
        fresh = res["code"]
        self.assertTrue(fresh)
        self.assertNotEqual(ae.normalize_code(fresh), ae.normalize_code(code))
        stored = self.profile("mermaid")["recovery_code_hash"]
        self.assertTrue(ae.code_matches("mermaid", ae.normalize_code(fresh), stored))

    def test_the_typed_form_does_not_matter(self):
        code = self.give("mermaid")
        res = ae.redeem_recovery_code("mermaid", code.lower().replace("-", " "),
                                      "Reef-Tide-99")
        self.assertTrue(res["ok"], res)

    def test_a_wrong_code_and_a_wrong_name_read_identically(self):
        # Rule 5 again: this screen must not be a way to ask which usernames
        # are real, so both misses say exactly the same thing.
        self.give("mermaid")
        wrong_code = ae.redeem_recovery_code("mermaid", ae.format_code(ae.make_code()),
                                             "Reef-Tide-99")
        ae._redeem_fails.clear()
        wrong_name = ae.redeem_recovery_code("nobody_at_all", ae.format_code(ae.make_code()),
                                             "Reef-Tide-99")
        self.assertEqual(wrong_code["message"], wrong_name["message"])
        self.assertEqual(wrong_code["ok"], wrong_name["ok"])
        self.assertEqual(wrong_code["error"], wrong_name["error"])

    def test_a_wrong_code_does_not_change_the_password(self):
        self.give("mermaid")
        ae.redeem_recovery_code("mermaid", ae.format_code(ae.make_code()), "Reef-Tide-99")
        self.assertEqual(self.passwords, {})

    def test_a_weak_password_is_refused_before_the_code_is_spent(self):
        # A good code must not be burned on an attempt that was going to be
        # refused anyway: they would have paid for nothing.
        code = self.give("mermaid")
        res = ae.redeem_recovery_code("mermaid", code, "abc")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "weak_password")
        stored = self.profile("mermaid")["recovery_code_hash"]
        self.assertTrue(ae.code_matches("mermaid", ae.normalize_code(code), stored))

    def test_a_google_account_cannot_redeem(self):
        self.account("googler", auth_provider="google", login_username="googler")
        ae.issue_recovery_code("googler")     # refused, so there is nothing to spend
        res = ae.redeem_recovery_code("googler", ae.format_code(ae.make_code()), "Reef-Tide-99")
        self.assertFalse(res["ok"])

    def test_the_name_is_matched_case_insensitively(self):
        self.account("mermaid", login_username="Mermaid")
        code = ae.issue_recovery_code("mermaid")["code"]
        res = ae.redeem_recovery_code("MERMAID", code, "Reef-Tide-99")
        self.assertTrue(res["ok"], res)
        # …and the synthetic address is built from the lower-cased login name,
        # or the reset would be for an account nobody owns.
        self.assertEqual(res["email"], "mermaid@players.currentsandcritters.com")

    def test_guessing_is_rate_limited(self):
        self.give("mermaid")
        for _ in range(ae.REDEEM_MAX_FAILS):
            res = ae.redeem_recovery_code("mermaid", ae.format_code(ae.make_code()),
                                          "Reef-Tide-99")
            self.assertEqual(res["error"], "no_match")
        locked = ae.redeem_recovery_code("mermaid", ae.format_code(ae.make_code()),
                                         "Reef-Tide-99")
        self.assertEqual(locked["error"], "too_many")

    def test_the_lockout_is_on_the_typed_name_even_when_it_is_nobody(self):
        # Keying it on real accounts only would make the lockout itself the
        # answer to "does this username exist?".
        for _ in range(ae.REDEEM_MAX_FAILS):
            ae.redeem_recovery_code("nobody_at_all", ae.format_code(ae.make_code()),
                                    "Reef-Tide-99")
        locked = ae.redeem_recovery_code("nobody_at_all", ae.format_code(ae.make_code()),
                                         "Reef-Tide-99")
        self.assertEqual(locked["error"], "too_many")

    def test_getting_it_right_clears_the_failures(self):
        code = self.give("mermaid")
        for _ in range(ae.REDEEM_MAX_FAILS - 1):
            ae.redeem_recovery_code("mermaid", ae.format_code(ae.make_code()), "Reef-Tide-99")
        self.assertTrue(ae.redeem_recovery_code("mermaid", code, "Reef-Tide-99")["ok"])
        self.assertNotIn("mermaid", ae._redeem_fails)


class TestCaseInsensitiveLookup(CodeBase):
    """Signing in lower-cases the username before it becomes an address, so
    `Mermaid` and `mermaid` are ONE account. Recovery used to disagree: it
    queried Firestore for `login_username` exactly as typed, and that field
    holds whatever case the player used at sign-up. Somebody who signed up as
    `Mermaid` and typed `mermaid` here was told there was no such account,
    which was true of the query and false of their account."""

    def test_a_reset_finds_the_account_whatever_case_is_typed(self):
        self.account("mermaid", login_username="Mermaid",
                     recovery_email="reef@example.com", recovery_email_verified=True)
        for typed in ("Mermaid", "mermaid", "MERMAID", "MerMaid"):
            SENT.clear()
            ae.forgot_password(typed)
            self.assertEqual(len(SENT), 1, typed)

    def test_a_code_is_redeemed_whatever_case_is_typed(self):
        self.account("mermaid", login_username="Mermaid")
        for typed in ("Mermaid", "mermaid", "MERMAID", "MerMaid"):
            # Cleared BEFORE issuing: redeeming mints the replacement, which
            # arms the same cooldown this loop would otherwise trip over.
            ae._last_issue_at.clear()
            ae._redeem_fails.clear()
            code = ae.issue_recovery_code("mermaid")["code"]
            res = ae.redeem_recovery_code(typed, code, "Reef-Tide-99")
            self.assertTrue(res["ok"], (typed, res))

    def test_the_fallback_still_works_with_no_Admin_at_all(self):
        # No credentials, or an Auth user that is gone: the Firestore queries
        # are still there, and still answer for the form that was stored.
        sys.modules.pop("firebase_admin", None)
        self.account("mermaid", login_username="mermaid")
        found = ae._find_by_login_username(self.db, "mermaid")
        self.assertIsNotNone(found)
        self.assertEqual(found[0], "mermaid")


class TestPasswordFloor(CodeBase):
    def test_the_floor_is_a_refusal_not_advice(self):
        for good in ("Reef-Tide-99", "abcd1234", "seaHorse7", "kelp forest 12"):
            self.assertTrue(ae.password_ok(good), good)
        for bad in ("", "short1", "alllowercase", "12345678", "Password1",
                    "password", "critters", None, "x" * 201):
            self.assertFalse(ae.password_ok(bad), repr(bad))


class TestCodeRouting(CodeBase):
    def test_issue_needs_a_real_token(self):
        self.account("mermaid")
        h = FakeHandler()
        ae.handle_post(h, Parsed("/api/account/recovery-code/issue"), {"idToken": "nope"})
        self.assertEqual(h.status, 401)
        self.assertFalse(self.profile("mermaid").get("recovery_code_hash"))

    def test_issue_reads_the_uid_off_the_token_not_the_body(self):
        # The body naming somebody else must not issue a code on their account.
        self.account("mermaid")
        self.account("victim")
        h = FakeHandler()
        ae.handle_post(h, Parsed("/api/account/recovery-code/issue"),
                       {"idToken": "tok::mermaid", "uid": "victim"})
        self.assertTrue(h.payload["ok"])
        self.assertTrue(self.profile("mermaid").get("recovery_code_hash"))
        self.assertFalse(self.profile("victim").get("recovery_code_hash"))

    def test_state_needs_a_real_token(self):
        h = FakeHandler()
        ae.handle_post(h, Parsed("/api/account/recovery-code/state"), {})
        self.assertEqual(h.status, 401)

    def test_redeem_is_public_by_design(self):
        # Somebody locked out has nothing to prove ownership with, so this one
        # takes no token at all. The code is what guards it.
        code = self.give("mermaid")
        h = FakeHandler()
        ae.handle_post(h, Parsed("/api/account/recovery-code/redeem"),
                       {"username": "mermaid", "code": code, "new_password": "Reef-Tide-99"})
        self.assertEqual(h.status, 200)
        self.assertTrue(h.payload["ok"])
        self.assertEqual(self.passwords["mermaid"], "Reef-Tide-99")

    def test_an_unknown_account_action_is_still_not_ours(self):
        h = FakeHandler()
        self.assertFalse(ae.handle_post(h, Parsed("/api/account/recovery-code/steal"), {}))

if __name__ == "__main__":
    unittest.main(verbosity=2)
