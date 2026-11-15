"""test_printed_codes.py: the hang tag's FEBRUARY2ND, limited to the first run.

Asked for as: "put only limited amount of codes because after all the first
run of plushies sell out you will not be able to get the critter again".

What this pins:
  1. The code is recognised however it is spelt, exactly like the page does.
  2. One claim per account, forever (trading the Fish away does not reset it).
  3. An account that already owns the Fish is told so and spends NO claim.
  4. The cap: claim number 250 works, 251 is "sold out" for everybody, and the
     counter never goes past the cap.
  5. The cap can be changed without a deploy (Firestore doc > env > 250), and
     0 closes it at once.
  6. Counter, ledger and grant land together or not at all, and every read in
     the transaction comes before every write (Firestore refuses otherwise).
  7. The uid comes off the verified token, never the request body.
  8. The public status never prints the code.

    python3 test_printed_codes.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import printed_codes as pc  # noqa: E402


# ── a small in-memory Firestore with subcollections and real-ish transactions ──
class Snap:
    def __init__(self, data):
        self._d = None if data is None else json.loads(json.dumps(data))
        self.exists = data is not None

    def to_dict(self):
        return None if self._d is None else dict(self._d)


class Ref:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def collection(self, name):
        return Coll(self.db, self.path + "/" + name)

    def get(self, transaction=None):
        if transaction is not None:
            transaction.on_read()
        return Snap(self.db.docs.get(self.path))


class Coll:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def document(self, doc_id):
        return Ref(self.db, self.path + "/" + doc_id)


class Txn:
    def __init__(self, db):
        self.db, self.writes, self.wrote = db, [], False

    def on_read(self):
        if self.wrote:
            raise AssertionError("Firestore: reads must come before writes in a transaction")

    def set(self, ref, data, merge=False):
        self.wrote = True
        self.writes.append(("set", ref.path, data, merge))

    def update(self, ref, data):
        self.wrote = True
        if ref.path not in self.db.docs:
            raise AssertionError("update on a missing doc " + ref.path)
        self.writes.append(("set", ref.path, data, True))

    def commit(self):
        if self.db.fail_commit:
            raise RuntimeError("commit failed")
        for _, path, data, merge in self.writes:
            if merge and path in self.db.docs:
                self.db.docs[path].update(data)
            else:
                self.db.docs[path] = dict(data)


class Db:
    def __init__(self):
        self.docs = {}
        self.fail_commit = False

    def collection(self, name):
        return Coll(self, name)

    def transaction(self):
        return Txn(self)


def fake_transactional(fn):
    def run(t):
        out = fn(t)
        t.commit()
        return out
    return run


class Handler:
    def __init__(self):
        self.sent = []

    def _send_json(self, payload, status=200):
        self.sent.append((status, payload))


class P:
    def __init__(self, path):
        self.path = path


FISH = "/avatars/fish.png"
COUNTER = "printed_code_claims/FEBRUARY2ND"


class Base(unittest.TestCase):
    def setUp(self):
        os.environ.pop("PRINTED_CODE_FEBRUARY2ND_CAP", None)
        self.db = Db()
        pc.init(get_firestore=lambda: self.db,
                verify_token=lambda tok: {"uid": tok[5:]} if str(tok).startswith("good:") else None)
        pc._transactional = lambda: fake_transactional   # type: ignore[assignment]
        pc._server_timestamp = lambda: "TS"              # type: ignore[assignment]

    def user(self, uid, icons=None):
        self.db.docs["users/" + uid] = {"nickname": uid, "unlocked_icons": list(icons or [])}

    def icons(self, uid):
        return self.db.docs["users/" + uid].get("unlocked_icons")

    def claimed(self):
        return (self.db.docs.get(COUNTER) or {}).get("claimed", 0)


class Spelling(Base):
    def test_every_spelling_the_page_accepts_is_the_tag_code(self):
        for t in ["FEBRUARY2ND", "February 2nd", "february-2nd", " FEBRUARY 2ND ",
                  "FEBUARARY2ND", "FEBUARY2ND", "FEBURARY2ND", "FEBRAURY2ND", "FBRUARY2ND",
                  "FERBUARY2ND", "FEBRUARY2N", "FEBRUARY 2", "Feb 2nd", "FEB2",
                  "February second", "2nd February", "2nd of February"]:
            self.assertIsNotNone(pc.match(t), t)
            self.assertEqual(pc.match(t)["id"], "FEBRUARY2ND", t)

    def test_other_words_and_donation_codes_are_not(self):
        for t in ["MARCH3RD", "FISH", "FEBRUARY", "2ND", "SECOND", "FEBRUARY2026",
                  "DECEMBER2ND", "481902", "222222", "", None]:
            self.assertIsNone(pc.match(t), t)

    def test_the_server_and_the_page_use_the_same_rules(self):
        app = open(os.path.join(ROOT, "multiplayer/client/js/preview-app.js"), encoding="utf-8").read()
        self.assertIn('id: "FEBRUARY2ND"', app)
        self.assertIn(r"/^FEB[A-Z]{0,9}(?:2|2ND|SECOND)$/", app)
        self.assertIn(r"/^(?:2|2ND|SECOND)(?:OF)?FEB[A-Z]{0,9}$/", app)
        self.assertIn('"/avatars/fish.png"', app)
        self.assertIn("codeTypoDistance(c, p.id) <= 2", app)
        self.assertIn('"/api/printed-code/claim"', app)


class Claims(Base):
    def test_a_claim_grants_the_fish_and_counts_one(self):
        self.user("a")
        r = pc.claim("a", "February 2nd")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["number"], 1)
        self.assertEqual(r["remaining"], pc.FIRST_RUN_DEFAULT - 1)
        self.assertIn(FISH, self.icons("a"))
        self.assertEqual(self.claimed(), 1)
        self.assertEqual(self.db.docs[COUNTER + "/accounts/a"]["number"], 1)

    def test_the_default_cap_is_the_first_run_of_250(self):
        self.assertEqual(pc.FIRST_RUN_DEFAULT, 250)
        self.assertEqual(pc.cap_for(pc.PRINTED[0], {}), 250)

    def test_once_per_account_even_after_trading_it_away(self):
        self.user("a")
        self.assertTrue(pc.claim("a", "FEBRUARY2ND")["ok"])
        self.db.docs["users/a"]["unlocked_icons"] = []         # traded away
        r = pc.claim("a", "FEBRUARY2ND")
        self.assertEqual(r["error"], "already_claimed", r)
        self.assertNotIn(FISH, self.icons("a"))
        self.assertEqual(self.claimed(), 1)

    def test_already_owning_the_fish_spends_no_claim(self):
        self.user("a", [FISH])
        r = pc.claim("a", "FEBRUARY2ND")
        self.assertEqual(r["error"], "already_owned")
        self.assertEqual(r["message"], "You already own Fish.")
        self.assertEqual(self.claimed(), 0)
        self.assertNotIn(COUNTER + "/accounts/a", self.db.docs)

    def test_owning_it_under_a_differently_written_path_still_counts(self):
        self.user("a", ["avatars/Fish.png"])
        self.assertEqual(pc.claim("a", "FEBRUARY2ND")["error"], "already_owned")

    def test_other_icons_are_kept(self):
        self.user("a", ["/avatars/mullet.png", "/avatars/amberjack.png"])
        pc.claim("a", "FEBRUARY2ND")
        self.assertEqual(self.icons("a"), ["/avatars/mullet.png", "/avatars/amberjack.png", FISH])

    def test_no_profile_spends_nothing(self):
        r = pc.claim("ghost", "FEBRUARY2ND")
        self.assertEqual(r["error"], "no_profile")
        self.assertEqual(self.claimed(), 0)

    def test_a_wrong_code_never_touches_the_database(self):
        self.db = None  # any read would crash
        pc.init(get_firestore=lambda: (_ for _ in ()).throw(AssertionError("read")),
                verify_token=lambda t: None)
        self.assertEqual(pc.claim("a", "MARCH3RD")["error"], "bad_code")

    def test_signed_out_is_refused(self):
        self.assertEqual(pc.claim("", "FEBRUARY2ND")["error"], "unauthorized")

    def test_a_failed_commit_writes_nothing(self):
        self.user("a")
        self.db.fail_commit = True
        r = pc.claim("a", "FEBRUARY2ND")
        self.assertEqual(r["error"], "unavailable")
        self.assertEqual(self.icons("a"), [])
        self.assertEqual(self.claimed(), 0)
        self.assertNotIn(COUNTER + "/accounts/a", self.db.docs)


class Cap(Base):
    def test_claim_250_works_and_251_is_sold_out(self):
        for i in range(250):
            self.user(f"u{i}")
            r = pc.claim(f"u{i}", "FEBRUARY2ND")
            self.assertTrue(r["ok"], (i, r))
        self.assertEqual(r["number"], 250)
        self.assertEqual(r["remaining"], 0)
        self.user("late")
        r = pc.claim("late", "FEBRUARY2ND")
        self.assertEqual(r["error"], "sold_out", r)
        self.assertIn("first run", r["message"])
        self.assertEqual(self.icons("late"), [])
        self.assertEqual(self.claimed(), 250)
        # ...and it stays shut
        for _ in range(3):
            self.assertEqual(pc.claim("late", "Feb 2nd")["error"], "sold_out")
        self.assertEqual(self.claimed(), 250)

    def test_sold_out_but_already_owning_says_already_owned(self):
        self.db.docs[COUNTER] = {"claimed": 250}
        self.user("a", [FISH])
        self.assertEqual(pc.claim("a", "FEBRUARY2ND")["error"], "already_owned")

    def test_the_firestore_doc_sets_the_cap_without_a_deploy(self):
        self.db.docs[COUNTER] = {"claimed": 3, "cap": 4}
        self.user("a"); self.user("b")
        self.assertTrue(pc.claim("a", "FEBRUARY2ND")["ok"])
        self.assertEqual(pc.claim("b", "FEBRUARY2ND")["error"], "sold_out")

    def test_cap_zero_closes_it_now(self):
        self.db.docs[COUNTER] = {"cap": 0}
        self.user("a")
        self.assertEqual(pc.claim("a", "FEBRUARY2ND")["error"], "sold_out")

    def test_env_var_sets_the_cap_and_the_doc_beats_it(self):
        os.environ["PRINTED_CODE_FEBRUARY2ND_CAP"] = "300"
        try:
            self.assertEqual(pc.cap_for(pc.PRINTED[0], {}), 300)
            self.assertEqual(pc.cap_for(pc.PRINTED[0], {"cap": 120}), 120)
            os.environ["PRINTED_CODE_FEBRUARY2ND_CAP"] = "nonsense"
            self.assertEqual(pc.cap_for(pc.PRINTED[0], {}), 250)
        finally:
            os.environ.pop("PRINTED_CODE_FEBRUARY2ND_CAP", None)

    def test_a_bad_cap_in_the_doc_falls_back_to_250(self):
        for bad in ["abc", -5, None, True]:
            self.assertEqual(pc.cap_for(pc.PRINTED[0], {"cap": bad}), 250, bad)


class Http(Base):
    def test_claim_uses_the_token_uid_not_the_body(self):
        self.user("real"); self.user("victim")
        h = Handler()
        self.assertTrue(pc.handle_post(h, P("/api/printed-code/claim"),
                                       {"idToken": "good:real", "code": "FEBRUARY2ND", "uid": "victim"}))
        self.assertTrue(h.sent[-1][1]["ok"])
        self.assertIn(FISH, self.icons("real"))
        self.assertEqual(self.icons("victim"), [])

    def test_no_token_is_401(self):
        h = Handler()
        pc.handle_post(h, P("/api/printed-code/claim"), {"code": "FEBRUARY2ND"})
        self.assertEqual(h.sent[-1][0], 401)

    def test_other_paths_are_not_ours(self):
        self.assertFalse(pc.handle_post(Handler(), P("/api/redeem/code"), {}))
        self.assertFalse(pc.handle_get(Handler(), P("/api/redeem/state")))

    def test_status_counts_down_and_never_prints_the_code(self):
        self.db.docs[COUNTER] = {"claimed": 40}
        h = Handler()
        self.assertTrue(pc.handle_get(h, P("/api/printed-code/status")))
        body = h.sent[-1][1]
        self.assertEqual(body["fish"], {"label": "Fish", "cap": 250, "claimed": 40,
                                        "remaining": 210, "soldOut": False})
        self.assertNotIn("FEB", json.dumps(body).upper())

    def test_status_with_no_database_still_answers(self):
        pc.init(get_firestore=lambda: None, verify_token=lambda t: None)
        s = pc.status()
        self.assertTrue(s["ok"])
        self.assertEqual(s["fish"]["cap"], 250)
        self.assertIsNone(s["fish"]["remaining"])


class Wiring(unittest.TestCase):
    def test_server_and_image_carry_the_module(self):
        srv = open(os.path.join(ROOT, "multiplayer_server.py"), encoding="utf-8").read()
        self.assertIn("import printed_codes", srv)
        self.assertIn("printed_codes.init(", srv)
        self.assertIn("printed_codes.handle_get(self, parsed)", srv)
        self.assertIn("printed_codes.handle_post(self, parsed, body)", srv)
        self.assertIn("COPY printed_codes.py /app/printed_codes.py",
                      open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read())
        self.assertIn("!printed_codes.py",
                      open(os.path.join(ROOT, ".dockerignore"), encoding="utf-8").read().splitlines())


if __name__ == "__main__":
    unittest.main(verbosity=1)
