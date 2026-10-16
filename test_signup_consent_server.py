#!/usr/bin/env python3
"""Terms acceptance, as the SERVER sees it.

Run:  python3 -m unittest test_signup_consent_server -v

WHY THE SERVER HAS TO CARE. The client gates both sign-up doors (see
test_signup_consent.js), but the client is the part of this a player controls.
A profile document in Firestore is written by the browser, so a consent record
that lived only in that write would be a record the recorded party could
choose not to make. /api/user/register already demands a verified Firebase ID
token, which makes it the one place in a sign-up where the server knows who is
asking. So that is where the acceptance is recorded, against the uid in the
VERIFIED token rather than anything in the body.

WHAT IS PINNED HERE:

  1. A registration with no consent in it is refused, and refused with 400 so
     a client bug is loud rather than silently unrecorded.
  2. A registration that ticks the age box but names no terms version is
     refused too. "I am 13" is not acceptance of anything.
  3. The uid that the acceptance is filed under comes from the token. Sending
     somebody else's uid in the body must not file an acceptance for them.
  4. The consent is written where it survives a Render restart. The free-tier
     disk does not persist, so a disk-only record is not a record.
  5. The sign-up counter still works, because a consent check that breaks
     sign-ups is worse than the problem it solves.

WHAT IS NOT CLAIMED. The server cannot prevent a client-side Firestore write,
so it cannot stop a determined caller creating a profile document without
consent. It can refuse to register them and it can decline to record an
acceptance that was never given, which is what it does. Closing that gap
needs Firestore security rules, which do not live in this repository: see the
implementation report.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import multiplayer_server as mp


_SANDBOX = {}
_FAKE_UID = "test-uid-consent-0001"


def setUpModule():
    tmp = tempfile.mkdtemp(prefix="cc-consent-test-")
    _SANDBOX["dir"] = tmp
    _SANDBOX["saved"] = {
        "ROOM_STATE_DIR": mp.ROOM_STATE_DIR,
        "STATS_PATH": mp.STATS_PATH,
        "_verify_firebase_id_token": mp._verify_firebase_id_token,
        "_get_firestore": mp._get_firestore,
    }
    mp.ROOM_STATE_DIR = os.path.join(tmp, "state")
    os.makedirs(mp.ROOM_STATE_DIR, exist_ok=True)
    mp.STATS_PATH = os.path.join(tmp, "site_stats.json")

    # A token that verifies, so these tests are about the CONSENT check and
    # not about the token check (test_security_surface covers that).
    def _fake_verify(token):
        return {"uid": _FAKE_UID} if token == "good-token" else None

    mp._verify_firebase_id_token = _fake_verify

    # Capture what the server tries to write, instead of talking to Firestore.
    _SANDBOX["writes"] = []

    class _Doc:
        def __init__(self, path):
            self._path = path

        def set(self, data, merge=False):
            _SANDBOX["writes"].append((self._path, dict(data), bool(merge)))

        def update(self, data):
            _SANDBOX["writes"].append((self._path, dict(data), True))

    class _Query:
        """Enough of the query surface for the dev-roster thread that runs on
        every registration. It finds nothing, which is the right answer here
        and keeps the test output clean."""

        def where(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def get(self):
            return []

        def stream(self):
            return iter(())

    class _Col(_Query):
        def __init__(self, name):
            self._name = name

        def document(self, doc_id):
            return _Doc(self._name + "/" + doc_id)

        def doc(self, doc_id):
            return _Doc(self._name + "/" + doc_id)

    class _DB:
        def collection(self, name):
            return _Col(name)

    mp._get_firestore = lambda: _DB()


def tearDownModule():
    for key, value in _SANDBOX.get("saved", {}).items():
        setattr(mp, key, value)
    shutil.rmtree(_SANDBOX.get("dir", ""), ignore_errors=True)


GOOD_CONSENT = {
    "terms_version": "2026-10-09",
    "terms_text": "I agree to the Terms of Service and acknowledge the Privacy Policy.",
    "age_13_plus": True,
}


class ConsentIsRequiredToRegister(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = mp.StableThreadingHTTPServer(("127.0.0.1", 0), mp.MultiplayerHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        _SANDBOX["writes"].clear()
        try:
            os.remove(mp.STATS_PATH)
        except OSError:
            pass

    def post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read().decode())

    # ── 1. no consent, no registration ──────────────────────────────────
    def test_a_registration_with_no_consent_is_refused(self):
        status, out = self.post("/api/user/register", {"idToken": "good-token"})
        self.assertEqual(status, 400, out)
        self.assertFalse(out.get("ok"))
        self.assertIn("consent", json.dumps(out).lower())

    def test_an_age_tick_alone_is_not_acceptance(self):
        status, out = self.post("/api/user/register", {
            "idToken": "good-token", "consent": {"age_13_plus": True}})
        self.assertEqual(status, 400, out)
        self.assertFalse(out.get("ok"))

    def test_a_terms_version_alone_is_not_an_age_answer(self):
        status, out = self.post("/api/user/register", {
            "idToken": "good-token", "consent": {"terms_version": "2026-10-09"}})
        self.assertEqual(status, 400, out)
        self.assertFalse(out.get("ok"))

    def test_an_age_answer_of_no_is_refused(self):
        bad = dict(GOOD_CONSENT, age_13_plus=False)
        status, out = self.post("/api/user/register",
                                {"idToken": "good-token", "consent": bad})
        self.assertEqual(status, 400, out)

    def test_the_string_true_is_not_a_yes(self):
        """A yes has to be a real boolean. Accepting "false" because it is a
        non-empty string is how this kind of check fails open."""
        for value in ("true", "false", 1, 0, "yes", None, [], {}):
            bad = dict(GOOD_CONSENT, age_13_plus=value)
            status, _ = self.post("/api/user/register",
                                  {"idToken": "good-token", "consent": bad})
            self.assertEqual(status, 400, f"age_13_plus={value!r} was accepted")

    def test_a_consent_that_is_not_an_object_is_refused(self):
        for value in ("yes", 1, True, [], "null"):
            status, _ = self.post("/api/user/register",
                                  {"idToken": "good-token", "consent": value})
            self.assertEqual(status, 400, f"consent={value!r} was accepted")

    # ── 2. the token still decides who this is ──────────────────────────
    def test_the_body_cannot_name_whose_acceptance_this_is(self):
        status, out = self.post("/api/user/register", {
            "idToken": "good-token", "uid": "somebody-elses-uid",
            "consent": GOOD_CONSENT})
        self.assertEqual(status, 200, out)
        paths = [p for p, _, _ in _SANDBOX["writes"]]
        self.assertTrue(any(p.endswith(_FAKE_UID) for p in paths),
                        f"nothing was filed under the token's uid: {paths}")
        self.assertFalse(any("somebody-elses-uid" in p for p in paths),
                         "an acceptance was filed under a uid from the body")

    def test_an_unverified_token_still_cannot_register(self):
        status, out = self.post("/api/user/register", {
            "idToken": "junk", "consent": GOOD_CONSENT})
        self.assertEqual(status, 401, out)
        self.assertEqual(_SANDBOX["writes"], [],
                         "an unverified caller got an acceptance recorded")

    # ── 3. what gets written, and where ─────────────────────────────────
    def test_a_good_registration_records_the_acceptance(self):
        status, out = self.post("/api/user/register", {
            "idToken": "good-token", "consent": GOOD_CONSENT})
        self.assertEqual(status, 200, out)
        self.assertTrue(out.get("ok"))

        written = [d for p, d, _ in _SANDBOX["writes"] if p.endswith(_FAKE_UID)]
        self.assertTrue(written, "the acceptance was not written to Firestore")
        rec = written[0]
        self.assertEqual(rec.get("terms_version"), "2026-10-09")
        self.assertIs(rec.get("age_attested_13_plus"), True)
        self.assertIn("terms_accepted_text", rec)
        self.assertIn("terms_accepted_at", rec)

    def test_the_date_of_birth_is_never_recorded(self):
        """The client computes an age and discards the date. If a date ever
        reaches the server it must not be stored, whatever the client sends."""
        sneaky = dict(GOOD_CONSENT, dob="2001-05-05", date_of_birth="2001-05-05")
        status, _ = self.post("/api/user/register",
                              {"idToken": "good-token", "consent": sneaky})
        self.assertEqual(status, 200)
        blob = json.dumps(_SANDBOX["writes"], default=str)
        self.assertNotIn("2001-05-05", blob,
                         "a date of birth was written to the account")
        self.assertNotIn("date_of_birth", blob)

    def test_a_long_consent_field_cannot_be_used_as_storage(self):
        """Every string here is bounded. An unbounded field on a write-only
        endpoint is a free database."""
        sneaky = dict(GOOD_CONSENT, terms_version="9" * 5000,
                      terms_text="x" * 50000)
        status, _ = self.post("/api/user/register",
                              {"idToken": "good-token", "consent": sneaky})
        blob = json.dumps(_SANDBOX["writes"], default=str)
        self.assertLess(len(blob), 4000, "a consent field was stored unbounded")

    # ── 4. the counter this endpoint was built for still works ──────────
    def test_the_signup_counter_still_counts(self):
        status, out = self.post("/api/user/register", {
            "idToken": "good-token", "consent": GOOD_CONSENT})
        self.assertEqual(status, 200, out)
        self.assertEqual(out.get("registered_players"), 1)

    def test_registering_twice_counts_once(self):
        self.post("/api/user/register", {"idToken": "good-token", "consent": GOOD_CONSENT})
        _, out = self.post("/api/user/register", {"idToken": "good-token", "consent": GOOD_CONSENT})
        self.assertEqual(out.get("registered_players"), 1)

    def test_a_firestore_outage_does_not_lose_the_signup(self):
        """A recorded acceptance matters, and a lost player matters more. If
        the write fails the registration still succeeds and the failure is
        reported, rather than the sign-up being refused."""
        saved = mp._get_firestore

        def _boom():
            raise RuntimeError("firestore is down")

        mp._get_firestore = _boom
        try:
            status, out = self.post("/api/user/register", {
                "idToken": "good-token", "consent": GOOD_CONSENT})
            self.assertEqual(status, 200, out)
            self.assertTrue(out.get("ok"))
        finally:
            mp._get_firestore = saved


class TheVersionTheServerExpects(unittest.TestCase):
    """The server must not be the place a terms version is invented."""

    def test_the_server_accepts_the_version_the_client_pins(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "multiplayer", "client", "js", "preview-app.js"),
                  encoding="utf-8") as fh:
            app = fh.read()
        import re
        m = re.search(r'CC_TERMS_VERSION\s*=\s*"([0-9-]+)"', app)
        self.assertTrue(m, "the client does not pin a terms version")
        self.assertEqual(m.group(1), mp.CC_TERMS_VERSION,
                         "client and server disagree about the current terms version")


if __name__ == "__main__":
    unittest.main(verbosity=2)
