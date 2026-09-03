"""The additive admin tools: handing an account coins/XP, and putting an
off-Stripe donation on the Supporter Reef Wall.

Two things are being pinned here, and they are the two that quietly go wrong:

1. A COIN GRANT MUST NOT CLOBBER A LIVE BALANCE. `_admin_grant` is run against
   production, on a player who may be mid-game, and a game that finishes one
   millisecond later pays coins too. Reading critter_coins and writing back
   read+N would silently erase that payout, so coins go through a Firestore
   Increment, which the SERVER applies to whatever the value is when the write
   lands. XP deliberately does NOT: its six derived level fields have to be
   computed from the new total here, exactly as _admin_set_xp does it.

2. A WALL NAME'S SIZE IS ITS LIFETIME TOTAL. `_admin_record_donation` writes
   the same document the Stripe webhook writes, adds to the same total, and is
   deduped on a payments/{reference} sub-document so a re-run cannot credit one
   gift twice.

Run:  python3 test_admin_grant.py
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import multiplayer_server as ms


# ── A Firestore fake: nested collections, merge writes, Increment ────────────
def _is_increment(v):
    return type(v).__name__ == "Increment" and hasattr(v, "value")


def _deep_merge(dst, src):
    for k, v in src.items():
        if _is_increment(v):
            try:
                base = int(dst.get(k) or 0)
            except (TypeError, ValueError):
                base = 0
            dst[k] = base + int(v.value)
        elif isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)
    return dst


def _resolve(data):
    """A whole-doc set() still has to resolve any Increment against nothing."""
    out = {}
    for k, v in data.items():
        out[k] = int(v.value) if _is_increment(v) else copy.deepcopy(v)
    return out


class _Snap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class _DocRef:
    def __init__(self, db, path, doc_id):
        self._db, self._path, self._id = db, path, doc_id

    def get(self):
        return _Snap(self._id, self._db.docs.setdefault(self._path, {}).get(self._id))

    def set(self, data, merge=False):
        self._db.writes.append((self._path, self._id, data, merge))
        store = self._db.docs.setdefault(self._path, {})
        if merge and self._id in store:
            _deep_merge(store[self._id], data)
        else:
            store[self._id] = _resolve(data)

    def collection(self, name):
        return _Collection(self._db, f"{self._path}/{self._id}/{name}")


class _Collection:
    def __init__(self, db, path, pairs=()):
        self._db, self._path, self._pairs = db, path, list(pairs)

    def document(self, doc_id):
        return _DocRef(self._db, self._path, doc_id)

    def where(self, field, op, value):
        assert op == "==", f"fake only supports == (got {op})"
        return _Collection(self._db, self._path, self._pairs + [(field, value)])

    def limit(self, _n):
        return self

    def stream(self):
        out = []
        for doc_id, data in self._db.docs.get(self._path, {}).items():
            if all((data or {}).get(f) == v for f, v in self._pairs):
                out.append(_Snap(doc_id, data))
        return out

    get = stream          # _build_supporter_wall calls .get(), not .stream()


class _DB:
    def __init__(self, docs=None):
        self.docs = docs or {}
        self.writes = []

    def collection(self, name):
        return _Collection(self, name)


def _account(**over):
    doc = {
        "nickname": "Pufferfish Pratt", "nickname_lower": "pufferfish pratt",
        "username": "PufferfishPratt", "usernameLower": "pufferfishpratt",
        "email": "pratt@example.com",
        "stats": {
            "critter_coins": 9_140,
            "total_xp": 74_820, "level": 38, "player_level": 38,
            "xp_current": 1_420, "level_xp_current": 1_420,
            "xp_goal": 2_600, "level_xp_goal": 2_600,
            "completed_games": 311, "wins": 120, "total_score": 48_002,
        },
        "unlocked_icons": ["/avatars/mullet.png"],
    }
    doc.update(over)
    return doc


class AdminGrantTest(unittest.TestCase):
    def setUp(self):
        self._real = ms._get_firestore
        self.db = _DB({"users": {"uid_pratt": _account(),
                                 "uid_other": {"nickname": "Reeflord",
                                               "nickname_lower": "reeflord",
                                               "stats": {"total_xp": 5_000}}}})
        ms._get_firestore = lambda: self.db

    def tearDown(self):
        ms._get_firestore = self._real

    def stats(self, uid="uid_pratt"):
        return self.db.docs["users"][uid]["stats"]

    # ── coins ──────────────────────────────────────────────────────────────
    def test_coins_are_added_to_the_balance(self):
        res = ms._admin_grant("Pufferfish Pratt", coins=11_500)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res["uid"], "uid_pratt")
        self.assertEqual(self.stats()["critter_coins"], 9_140 + 11_500)

    def test_coins_are_written_as_an_increment_not_a_read_then_write(self):
        """The whole point: the value handed to Firestore is a transform, so a
        game finishing between our read and our write still keeps its payout."""
        ms._admin_grant("Pufferfish Pratt", coins=11_500)
        path, doc_id, data, merge = self.db.writes[-1]
        self.assertEqual((path, doc_id, merge), ("users", "uid_pratt", True))
        self.assertTrue(_is_increment(data["stats"]["critter_coins"]),
                        f"coins were written as {data['stats']['critter_coins']!r}, "
                        "a plain number overwrites a balance that moved")
        self.assertEqual(data["stats"]["critter_coins"].value, 11_500)

    def test_a_balance_that_moves_under_us_is_not_erased(self):
        """The player finishes a game (+250 coins) AFTER we have read their
        balance and BEFORE our write lands. A read-then-write would put
        9,140+11,500 on the document and eat those 250; an increment, which the
        fake resolves against the CURRENT value exactly as Firestore does,
        cannot. This is the failure the whole increment exists to prevent."""
        real_resolve = ms._admin_resolve_account

        def resolve_then_race(who):
            snap, err = real_resolve(who)
            self.db.docs["users"]["uid_pratt"]["stats"]["critter_coins"] += 250
            return snap, err

        ms._admin_resolve_account = resolve_then_race
        try:
            ms._admin_grant("Pufferfish Pratt", coins=11_500)
        finally:
            ms._admin_resolve_account = real_resolve
        self.assertEqual(self.stats()["critter_coins"], 9_140 + 250 + 11_500)

    # ── XP ─────────────────────────────────────────────────────────────────
    def test_xp_is_added_and_every_derived_level_field_moves_with_it(self):
        res = ms._admin_grant("Pufferfish Pratt", xp=5_000)
        self.assertTrue(res.get("ok"), res)
        want_xp = 74_820 + 5_000
        lvl, cur, goal = ms._level_progress_for_total_xp(want_xp)
        s = self.stats()
        self.assertEqual(s["total_xp"], want_xp)
        self.assertEqual(s["level"], lvl)
        self.assertEqual(s["player_level"], lvl)
        self.assertEqual(s["xp_current"], cur)
        self.assertEqual(s["level_xp_current"], cur)
        self.assertEqual(s["xp_goal"], goal)
        self.assertEqual(s["level_xp_goal"], goal)

    def test_the_stored_level_is_what_the_curve_says_for_the_new_total(self):
        for add in (0, 1, 50, 5_000, 250_000):
            self.db.docs["users"]["uid_pratt"] = _account()
            if not add:
                continue
            ms._admin_grant("Pufferfish Pratt", xp=add)
            want = 74_820 + add
            lvl, cur, goal = ms._level_progress_for_total_xp(want)
            s = self.stats()
            self.assertEqual((s["total_xp"], s["level"], s["xp_current"], s["xp_goal"]),
                             (want, lvl, cur, goal), add)

    def test_coins_and_xp_in_one_call(self):
        res = ms._admin_grant("Pufferfish Pratt", coins=11_500, xp=5_000)
        self.assertTrue(res.get("ok"), res)
        s = self.stats()
        self.assertEqual(s["critter_coins"], 9_140 + 11_500)
        self.assertEqual(s["total_xp"], 74_820 + 5_000)

    def test_nothing_else_on_the_account_is_touched(self):
        ms._admin_grant("Pufferfish Pratt", coins=11_500, xp=5_000)
        s = self.stats()
        self.assertEqual(s["completed_games"], 311)
        self.assertEqual(s["wins"], 120)
        self.assertEqual(s["total_score"], 48_002)
        doc = self.db.docs["users"]["uid_pratt"]
        self.assertEqual(doc["unlocked_icons"], ["/avatars/mullet.png"])
        self.assertEqual(doc["nickname"], "Pufferfish Pratt")

    # ── refusals ───────────────────────────────────────────────────────────
    def test_a_grant_can_never_subtract(self):
        for bad in ({"coins": -1}, {"xp": -1}, {"coins": -500, "xp": 5}):
            res = ms._admin_grant("Pufferfish Pratt", **bad)
            self.assertFalse(res.get("ok"), bad)
            self.assertEqual(self.stats()["critter_coins"], 9_140)
            self.assertEqual(self.stats()["total_xp"], 74_820)

    def test_an_empty_grant_is_refused_rather_than_writing_nothing(self):
        res = ms._admin_grant("Pufferfish Pratt")
        self.assertFalse(res.get("ok"))
        self.assertEqual(res["error"], "nothing to grant")

    def test_junk_amounts_are_refused(self):
        for bad in ("lots", 1.5, [5], {"a": 1}):
            res = ms._admin_grant("Pufferfish Pratt", coins=bad)
            self.assertFalse(res.get("ok"), bad)

    def test_a_dry_run_reports_but_changes_nothing(self):
        res = ms._admin_grant("Pufferfish Pratt", coins=11_500, xp=5_000, dry_run=True)
        self.assertTrue(res.get("ok"), res)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["after"]["critter_coins"], 9_140 + 11_500)
        self.assertEqual(res["after"]["total_xp"], 74_820 + 5_000)
        self.assertEqual(self.stats()["critter_coins"], 9_140)
        self.assertEqual(self.stats()["total_xp"], 74_820)
        self.assertEqual(self.db.writes, [])

    def test_it_reports_the_before_and_after(self):
        res = ms._admin_grant("Pufferfish Pratt", coins=11_500, xp=5_000)
        self.assertEqual(res["before"]["critter_coins"], 9_140)
        self.assertEqual(res["before"]["total_xp"], 74_820)
        self.assertEqual(res["before"]["level"], 38)
        self.assertEqual(res["granted"], {"coins": 11_500, "xp": 5_000})
        self.assertEqual(res["nickname"], "Pufferfish Pratt")

    # ── finding the account ────────────────────────────────────────────────
    def test_finds_the_account_by_uid_and_by_name_case_insensitively(self):
        for who in ("uid_pratt", "Pufferfish Pratt", "pufferfish pratt",
                    "PUFFERFISH PRATT", "PufferfishPratt", "pufferfishpratt"):
            self.db.docs["users"]["uid_pratt"] = _account()
            res = ms._admin_grant(who, coins=10)
            self.assertTrue(res.get("ok"), (who, res))
            self.assertEqual(res["uid"], "uid_pratt", who)

    def test_an_unknown_account_is_an_error_not_a_new_document(self):
        res = ms._admin_grant("Nobody At All", coins=10)
        self.assertFalse(res.get("ok"))
        self.assertNotIn("Nobody At All", self.db.docs["users"])


class AdminRecordDonationTest(unittest.TestCase):
    def setUp(self):
        self._real = ms._get_firestore
        self.db = _DB({"users": {"uid_pratt": _account()}})
        ms._get_firestore = lambda: self.db
        ms._WALL_CACHE["data"] = None

    def tearDown(self):
        ms._get_firestore = self._real
        ms._WALL_CACHE["data"] = None

    def supporter(self, uid="uid_pratt"):
        return self.db.docs.get("supporters", {}).get(uid)

    def test_it_creates_the_wall_record_the_stripe_webhook_would_have(self):
        res = ms._admin_record_donation("Pufferfish Pratt", 1000, reference="cash-2026-09-02")
        self.assertTrue(res.get("ok"), res)
        d = self.supporter()
        self.assertEqual(d["displayName"], "Pufferfish Pratt")
        self.assertEqual(d["totalSpentCents"], 1000)
        self.assertEqual(d["status"], "approved")
        self.assertTrue(d["visible"])
        self.assertFalse(d["anonymous"])
        self.assertTrue(d["hasGameAccount"])
        self.assertEqual(d["firebaseUid"], "uid_pratt")
        self.assertEqual(d["paymentCount"], 1)

    def test_the_tier_and_wall_size_come_from_the_shipped_table(self):
        ms._admin_record_donation("Pufferfish Pratt", 1000, reference="a")
        want_tier, want_size = ms._supporter_tier_for_total(1000)
        d = self.supporter()
        self.assertEqual((d["tier"], d["wallSize"]), (want_tier, want_size))

    def test_a_second_gift_accumulates_onto_the_same_lifetime_total(self):
        ms._admin_record_donation("Pufferfish Pratt", 1000, reference="a")
        res = ms._admin_record_donation("Pufferfish Pratt", 4000, reference="b")
        self.assertTrue(res.get("ok"), res)
        d = self.supporter()
        self.assertEqual(d["totalSpentCents"], 5000)
        self.assertEqual(d["paymentCount"], 2)
        # …and the bigger total re-derives a bigger bucket, it is not left stale.
        self.assertEqual((d["tier"], d["wallSize"]), ms._supporter_tier_for_total(5000))

    def test_the_same_reference_twice_is_refused_and_credits_nothing(self):
        ms._admin_record_donation("Pufferfish Pratt", 1000, reference="cash-2026-09-02")
        res = ms._admin_record_donation("Pufferfish Pratt", 1000, reference="cash-2026-09-02")
        self.assertFalse(res.get("ok"), res)
        self.assertIn("already recorded", res["error"])
        self.assertEqual(self.supporter()["totalSpentCents"], 1000)
        self.assertEqual(self.supporter()["paymentCount"], 1)

    def test_the_payment_subdocument_is_the_audit_trail(self):
        ms._admin_record_donation("Pufferfish Pratt", 1000, reference="cash-2026-09-02")
        pay = self.db.docs["supporters/uid_pratt/payments"]["cash-2026-09-02"]
        self.assertEqual(pay["amountCents"], 1000)
        self.assertEqual(pay["source"], "admin_manual")

    def test_a_reference_with_slashes_cannot_break_the_document_path(self):
        res = ms._admin_record_donation("Pufferfish Pratt", 1000, reference="cash/2026/09/02")
        self.assertTrue(res.get("ok"), res)
        self.assertNotIn("/", res["reference"])
        self.assertIn(res["reference"], self.db.docs["supporters/uid_pratt/payments"])

    def test_the_display_name_defaults_to_the_account_but_can_be_overridden(self):
        ms._admin_record_donation("Pufferfish Pratt", 1000, reference="a")
        self.assertEqual(self.supporter()["displayName"], "Pufferfish Pratt")
        ms._admin_record_donation("Pufferfish Pratt", 1000,
                                  display_name="Pratt the Puffer", reference="b")
        self.assertEqual(self.supporter()["displayName"], "Pratt the Puffer")

    def test_a_dry_run_writes_neither_the_total_nor_the_dedup_key(self):
        res = ms._admin_record_donation("Pufferfish Pratt", 1000,
                                        reference="a", dry_run=True)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res["after"]["totalSpentCents"], 1000)
        self.assertIsNone(self.supporter())
        self.assertEqual(self.db.writes, [])

    def test_amounts_must_be_a_positive_whole_number_of_cents(self):
        for bad in (0, -100, "ten", None, 12.5):
            res = ms._admin_record_donation("Pufferfish Pratt", bad, reference="a")
            self.assertFalse(res.get("ok"), bad)
            self.assertIsNone(self.supporter())

    def test_an_unknown_account_records_nothing(self):
        res = ms._admin_record_donation("Nobody At All", 1000, reference="a")
        self.assertFalse(res.get("ok"))
        self.assertEqual(self.db.docs.get("supporters", {}), {})

    # ── the actual ask: it shows up on the wall, bigger than a $1 name ──────
    def test_the_name_reaches_the_public_wall_above_a_smaller_donor(self):
        self.db.docs["supporters"] = {
            "uid_jett": {"displayName": "The Jett", "status": "approved",
                         "visible": True, "totalSpentCents": 100,
                         "tier": None, "wallSize": None},
        }
        ms._admin_record_donation("Pufferfish Pratt", 1000, reference="cash-2026-09-02")
        wall = ms._build_supporter_wall()
        names = [r["displayName"] for r in wall]
        self.assertIn("Pufferfish Pratt", names)
        # The homepage sizes each name continuously from amountCents and the
        # wall arrives already sorted biggest-first, so "bigger than Jett" IS
        # a bigger amountCents and the first row.
        self.assertEqual(names[0], "Pufferfish Pratt", names)
        by_name = {r["displayName"]: r["amountCents"] for r in wall}
        self.assertGreater(by_name["Pufferfish Pratt"], by_name["The Jett"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
