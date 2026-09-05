"""Custom friend codes: chosen, RESERVED, and paid for exactly once.

Run:  python3 test_custom_friend_code.py

An ordinary friend code is four random digits and is NOT unique: two accounts
can hold the same one, which is why a bare code can be ambiguous. A custom code
is the opposite, and the difference is the whole product: you pick it, and
nobody else can ever have it.

That makes this the one Store purchase the browser cannot do for itself. A
client may only write its own user document, so it cannot reserve a name against
everybody else's; and "check if it's free, then take it" as two steps is a race
that hands the same code to two people. So it is ONE server transaction, and
these tests pin the things that go wrong when it is not:

  * the shape rules (3-9, letters and numbers, and never four digits, which is
    what a RANDOM code looks like),
  * a taken code stays taken,
  * payment happens once, from a token if there is one and coins otherwise,
  * a player who can afford neither is refused rather than given it free,
  * renaming frees the code you were holding, so nobody sits on a pile of them,
  * the lower-cased copy the Add Friend box searches on is written.
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import multiplayer_server as ms


# ── the smallest Firestore that can hold a transaction ────────────────────
def _deep(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep(dst[k], v)
        else:
            dst[k] = v


class _Snap:
    def __init__(self, doc_id, data):
        self.id, self._data, self.exists = doc_id, data, data is not None

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class _Doc:
    def __init__(self, db, path):
        self._db, self.path = db, path
        self.id = path.rsplit("/", 1)[-1]

    def get(self, transaction=None):
        return _Snap(self.id, self._db.docs.get(self.path))

    def set(self, data, merge=False):
        data = copy.deepcopy(data)
        if merge and isinstance(self._db.docs.get(self.path), dict):
            _deep(self._db.docs[self.path], data)
        else:
            self._db.docs[self.path] = data

    def delete(self):
        self._db.docs.pop(self.path, None)


class _Coll:
    def __init__(self, db, prefix):
        self._db, self.prefix = db, prefix

    def document(self, doc_id):
        return _Doc(self._db, self.prefix + "/" + doc_id)


class _Txn:
    def __init__(self, db):
        self._db = db

    def set(self, ref, data, merge=False):
        ref.set(data, merge=merge)

    def delete(self, ref):
        ref.delete()


class _Db:
    def __init__(self):
        self.docs = {}

    def collection(self, name):
        return _Coll(self, name)

    def transaction(self):
        return _Txn(self)


class CodeShape(unittest.TestCase):
    """Checked before anything is read, because a bad shape is a bad shape
    whatever the database says."""

    def test_the_good_ones_pass(self):
        for c in ("abc", "ReefKing", "Tim2026", "a1b2c3d4e", "007x"):
            self.assertEqual(ms._custom_code_error(c), "", c)

    def test_too_short_or_too_long(self):
        for c in ("ab", "a", "", "reeftoolongx"):
            self.assertIn("characters", ms._custom_code_error(c), repr(c))

    def test_letters_and_numbers_only(self):
        for c in ("reef king", "reef-king", "reef_king", "reef!", "réef"):
            self.assertIn("Letters and numbers", ms._custom_code_error(c), c)

    def test_four_digits_is_refused(self):
        """Four digits is exactly what a RANDOM code looks like. Reserving one
        would mean an existing account's ordinary code suddenly belonged to
        somebody else, and every lookup of it became ambiguous."""
        self.assertIn("random code", ms._custom_code_error("4821"))
        # …but other digit lengths are fine, they are not the random shape.
        self.assertEqual(ms._custom_code_error("482"), "")
        self.assertEqual(ms._custom_code_error("48213"), "")


class ClaimingOne(unittest.TestCase):
    def setUp(self):
        self.db = _Db()
        from firebase_admin import firestore
        self._real_txnl = firestore.transactional
        firestore.transactional = lambda fn: fn
        self._real_fs = ms._get_firestore
        ms._get_firestore = lambda: self.db

    def tearDown(self):
        from firebase_admin import firestore
        firestore.transactional = self._real_txnl
        ms._get_firestore = self._real_fs

    def account(self, uid, *, coins=0, tokens=0, code="4821"):
        self.db.docs[f"users/{uid}"] = {
            "friend_code": code,
            "custom_code_tokens": tokens,
            "stats": {"critter_coins": coins},
        }

    def user(self, uid):
        return self.db.docs[f"users/{uid}"]

    # ── paying for it ────────────────────────────────────────────────
    def test_a_token_is_spent_before_any_coins(self):
        """A token has no other use; coins do. So the token goes first, always."""
        self.account("u1", coins=50_000, tokens=2)
        out = ms._claim_custom_friend_code("u1", "ReefKing")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["paid_with"], "token")
        self.assertEqual(self.user("u1")["custom_code_tokens"], 1)
        self.assertEqual(self.user("u1")["stats"]["critter_coins"], 50_000,
                         "not a single coin was taken")

    def test_coins_pay_when_there_is_no_token(self):
        self.account("u1", coins=1500, tokens=0)
        out = ms._claim_custom_friend_code("u1", "ReefKing")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["paid_with"], "coins")
        self.assertEqual(self.user("u1")["stats"]["critter_coins"],
                         1500 - ms.CUSTOM_CODE_COIN_PRICE)

    def test_too_poor_is_refused_not_given_away(self):
        self.account("u1", coins=ms.CUSTOM_CODE_COIN_PRICE - 1, tokens=0)
        out = ms._claim_custom_friend_code("u1", "ReefKing")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "cannot_afford")
        self.assertEqual(self.user("u1")["friend_code"], "4821", "the code did not move")
        self.assertNotIn("customFriendCodes/reefking", self.db.docs,
                         "and nothing was reserved for a payment that never happened")

    def test_a_garbage_balance_never_pays(self):
        """A balance that is somehow a string must refuse, not throw inside the
        transaction and leave a half-written claim."""
        self.db.docs["users/u1"] = {"friend_code": "4821",
                                    "custom_code_tokens": "x",
                                    "stats": {"critter_coins": None}}
        self.assertEqual(ms._claim_custom_friend_code("u1", "ReefKing")["error"], "cannot_afford")

    # ── reserving it ─────────────────────────────────────────────────
    def test_the_code_is_reserved_and_lands_on_the_account(self):
        self.account("u1", tokens=1)
        out = ms._claim_custom_friend_code("u1", "ReefKing")
        self.assertTrue(out["ok"])
        self.assertEqual(self.db.docs["customFriendCodes/reefking"]["uid"], "u1")
        u = self.user("u1")
        self.assertEqual(u["friend_code"], "ReefKing", "the code you typed, in your case")
        self.assertEqual(u["custom_friend_code"], "ReefKing")
        self.assertEqual(u["friend_code_lower"], "reefking",
                         "the Add Friend box searches on this")

    def test_somebody_elses_code_cannot_be_taken(self):
        self.account("u1", tokens=1)
        self.account("u2", tokens=1)
        self.assertTrue(ms._claim_custom_friend_code("u1", "ReefKing")["ok"])
        out = ms._claim_custom_friend_code("u2", "reefking")
        self.assertFalse(out["ok"], "case is not a way around a reservation")
        self.assertEqual(out["error"], "taken")
        self.assertEqual(self.user("u2")["custom_code_tokens"], 1, "and it cost them nothing")

    def test_renaming_frees_the_one_you_were_holding(self):
        """Otherwise a player with eight codes sits on eight names for ever."""
        self.account("u1", tokens=2)
        ms._claim_custom_friend_code("u1", "ReefKing")
        ms._claim_custom_friend_code("u1", "TideBoss")
        self.assertNotIn("customFriendCodes/reefking", self.db.docs)
        self.assertEqual(self.db.docs["customFriendCodes/tideboss"]["uid"], "u1")
        self.assertEqual(self.user("u1")["friend_code"], "TideBoss")

    def test_reclaiming_your_own_code_is_refused_free_of_charge(self):
        self.account("u1", tokens=2)
        ms._claim_custom_friend_code("u1", "ReefKing")
        out = ms._claim_custom_friend_code("u1", "ReefKing")
        self.assertEqual(out["error"], "already_yours")
        self.assertEqual(self.user("u1")["custom_code_tokens"], 1,
                         "a second token was not burned on a code they already had")

    def test_an_account_that_does_not_exist(self):
        self.assertEqual(ms._claim_custom_friend_code("nobody", "ReefKing")["error"], "no_account")

    def test_every_refusal_carries_something_to_show_the_player(self):
        self.account("u1", coins=0, tokens=0)
        for code in ("ab", "reef king", "4821", "ReefKing"):
            out = ms._claim_custom_friend_code("u1", code)
            self.assertFalse(out["ok"], code)
            self.assertTrue(out.get("message"), f"{code} refused with nothing to say")


class TheTiersHandThemOut(unittest.TestCase):
    def test_every_tier_grants_at_least_one(self):
        for tier, grant in ms.SUPPORTER_TIER_GRANTS.items():
            self.assertGreaterEqual(grant["custom_codes"], 1, tier)

    def test_the_ladder_is_one_two_four_eight(self):
        by_price = [ms.SUPPORTER_TIERS_BY_CENTS[c] for c in sorted(ms.SUPPORTER_TIERS_BY_CENTS)]
        self.assertEqual([ms.SUPPORTER_TIER_GRANTS[t]["custom_codes"] for t in by_price],
                         [1, 2, 4, 8])


if __name__ == "__main__":
    unittest.main(verbosity=2)
