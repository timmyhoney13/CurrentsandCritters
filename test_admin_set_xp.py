"""The admin XP tool: setting one account's lifetime XP to an exact number.

XP is the one stat with a real DOWNWARD path (every other admin tool only ever
adds), and it is stored twice over: `stats.total_xp` is the source of truth, but
`level` / `player_level` / `xp_current` / `level_xp_current` / `xp_goal` /
`level_xp_goal` sit beside it and are what the header, the profile and the XP
leaderboard actually read. Writing total_xp alone leaves an account showing one
number in one place and another everywhere else — so the whole point of this
tool is that all seven move together, derived from the SAME level table the
client uses.

Run:  python3 test_admin_set_xp.py
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import multiplayer_server as ms


# ── A Firestore fake just big enough for users/{uid} reads + merge writes ────
class _Snap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


def _deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)
    return dst


class _DocRef:
    def __init__(self, store, doc_id):
        self._store, self._id = store, doc_id

    def get(self):
        return _Snap(self._id, self._store.get(self._id))

    def set(self, data, merge=False):
        if merge and self._id in self._store:
            _deep_merge(self._store[self._id], data)
        else:
            self._store[self._id] = copy.deepcopy(data)


class _Query:
    def __init__(self, store, pairs=()):
        self._store, self._pairs = store, list(pairs)

    def where(self, field, op, value):
        assert op == "==", f"fake only supports == (got {op})"
        return _Query(self._store, self._pairs + [(field, value)])

    def limit(self, _n):
        return self

    def stream(self):
        out = []
        for doc_id, data in self._store.items():
            if all((data or {}).get(f) == v for f, v in self._pairs):
                out.append(_Snap(doc_id, data))
        return out


class _Users(_Query):
    def document(self, doc_id):
        return _DocRef(self._store, doc_id)


class _DB:
    def __init__(self, users):
        self.users = users

    def collection(self, name):
        assert name == "users", name
        return _Users(self.users)


def make_db(users):
    return _DB(users)


class AdminSetXpTest(unittest.TestCase):
    def setUp(self):
        self._real = ms._get_firestore
        self.users = {
            "uid_hotdog": {
                "nickname": "timmyhotdog", "nickname_lower": "timmyhotdog",
                "username": "timmyhotdog", "usernameLower": "timmyhotdog",
                "stats": {
                    "total_xp": 74_820, "level": 38, "player_level": 38,
                    "xp_current": 1_420, "level_xp_current": 1_420,
                    "xp_goal": 2_600, "level_xp_goal": 2_600,
                    # Everything else on the stats map must survive untouched.
                    "critter_coins": 9_140, "completed_games": 311,
                    "total_score": 48_002, "wins": 120,
                },
                "unlocked_icons": ["/avatars/mullet.png"],
                "supporter_tier": "ocean-ally",
            },
            "uid_other": {
                "nickname": "Reeflord", "nickname_lower": "reeflord",
                "stats": {"total_xp": 5_000, "level": 11},
            },
        }
        self.db = make_db(self.users)
        ms._get_firestore = lambda: self.db

    def tearDown(self):
        ms._get_firestore = self._real

    def stats(self, uid="uid_hotdog"):
        return self.users[uid]["stats"]

    # ── the ask: timmyhotdog has 1000 XP, everywhere ────────────────────────
    def test_sets_lifetime_xp_to_exactly_the_number_given(self):
        res = ms._admin_set_xp("timmyhotdog", 1000)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res["uid"], "uid_hotdog")
        self.assertEqual(self.stats()["total_xp"], 1000)

    def test_every_derived_level_field_moves_with_it(self):
        # 1000 XP sits inside level 6 on the shipped curve (level 6 starts at
        # 900, level 7 at 1400) — so 100 into a 500-XP level.
        ms._admin_set_xp("timmyhotdog", 1000)
        s = self.stats()
        self.assertEqual(s["level"], 6)
        self.assertEqual(s["player_level"], 6)
        self.assertEqual(s["xp_current"], 100)
        self.assertEqual(s["level_xp_current"], 100)
        self.assertEqual(s["xp_goal"], 500)
        self.assertEqual(s["level_xp_goal"], 500)

    def test_the_stored_level_is_what_the_curve_says_not_a_guess(self):
        for xp in (0, 49, 50, 99, 100, 899, 900, 1000, 1399, 1400, 250_000, 999_999):
            ms._admin_set_xp("timmyhotdog", xp)
            want_lvl, want_cur, want_goal = ms._level_progress_for_total_xp(xp)
            s = self.stats()
            self.assertEqual(s["total_xp"], xp, xp)
            self.assertEqual(s["level"], want_lvl, xp)
            self.assertEqual(s["xp_current"], want_cur, xp)
            self.assertEqual(s["xp_goal"], want_goal, xp)

    def test_lowering_xp_is_allowed_this_is_the_tool_that_can(self):
        before = self.stats()["total_xp"]
        ms._admin_set_xp("timmyhotdog", 1000)
        self.assertLess(self.stats()["total_xp"], before)
        self.assertEqual(self.stats()["total_xp"], 1000)

    def test_nothing_else_on_the_account_is_touched(self):
        ms._admin_set_xp("timmyhotdog", 1000)
        s = self.stats()
        self.assertEqual(s["critter_coins"], 9_140)
        self.assertEqual(s["completed_games"], 311)
        self.assertEqual(s["total_score"], 48_002)
        self.assertEqual(s["wins"], 120)
        doc = self.users["uid_hotdog"]
        self.assertEqual(doc["unlocked_icons"], ["/avatars/mullet.png"])
        self.assertEqual(doc["supporter_tier"], "ocean-ally")
        self.assertEqual(doc["nickname"], "timmyhotdog")

    def test_it_reports_the_before_and_after(self):
        res = ms._admin_set_xp("timmyhotdog", 1000)
        self.assertEqual(res["before"]["total_xp"], 74_820)
        self.assertEqual(res["before"]["level"], 38)
        self.assertEqual(res["after"]["total_xp"], 1000)
        self.assertEqual(res["after"]["level"], 6)
        self.assertEqual(res["nickname"], "timmyhotdog")

    def test_a_dry_run_changes_nothing(self):
        res = ms._admin_set_xp("timmyhotdog", 1000, dry_run=True)
        self.assertTrue(res.get("ok"), res)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["after"]["total_xp"], 1000)   # what it WOULD do
        self.assertEqual(self.stats()["total_xp"], 74_820)  # …and did not

    # ── finding the account ────────────────────────────────────────────────
    def test_finds_the_account_by_uid_too(self):
        res = ms._admin_set_xp("uid_hotdog", 1000)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.stats()["total_xp"], 1000)

    def test_the_nickname_lookup_is_case_insensitive(self):
        res = ms._admin_set_xp("TimmyHotdog", 1000)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.stats()["total_xp"], 1000)

    def test_an_unknown_name_changes_nobody(self):
        res = ms._admin_set_xp("nobody-by-that-name", 1000)
        self.assertFalse(res.get("ok"))
        self.assertIn("no account found", res["error"])
        self.assertEqual(self.stats()["total_xp"], 74_820)
        self.assertEqual(self.stats("uid_other")["total_xp"], 5_000)

    def test_an_ambiguous_name_refuses_rather_than_picking_one(self):
        self.users["uid_twin"] = {"nickname": "timmyhotdog",
                                  "nickname_lower": "timmyhotdog",
                                  "stats": {"total_xp": 40}}
        res = ms._admin_set_xp("timmyhotdog", 1000)
        self.assertFalse(res.get("ok"))
        self.assertIn("more than one account", res["error"])
        self.assertEqual(self.stats()["total_xp"], 74_820)
        self.assertEqual(self.users["uid_twin"]["stats"]["total_xp"], 40)

    # ── bad input ──────────────────────────────────────────────────────────
    def test_a_missing_name_is_refused(self):
        self.assertFalse(ms._admin_set_xp("", 1000).get("ok"))
        self.assertFalse(ms._admin_set_xp("   ", 1000).get("ok"))

    def test_a_non_number_is_refused(self):
        for bad in ("lots", None, "", [], {}, 12.7):
            res = ms._admin_set_xp("timmyhotdog", bad)
            if bad == 12.7:      # int(12.7) is a real conversion — 12 XP
                self.assertTrue(res.get("ok"), res)
                continue
            self.assertFalse(res.get("ok"), f"{bad!r} should be refused")
        self.assertNotEqual(self.stats()["total_xp"], 0)

    def test_negative_xp_is_refused(self):
        res = ms._admin_set_xp("timmyhotdog", -5)
        self.assertFalse(res.get("ok"))
        self.assertEqual(self.stats()["total_xp"], 74_820)

    def test_zero_is_allowed_it_is_a_real_number_of_xp(self):
        res = ms._admin_set_xp("timmyhotdog", 0)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.stats()["total_xp"], 0)
        self.assertEqual(self.stats()["level"], 1)

    def test_no_firestore_is_an_error_not_a_crash(self):
        ms._get_firestore = lambda: None
        res = ms._admin_set_xp("timmyhotdog", 1000)
        self.assertFalse(res.get("ok"))
        self.assertEqual(res["error"], "firestore_unavailable")

    def test_an_account_with_no_stats_map_yet_still_works(self):
        self.users["uid_fresh"] = {"nickname": "Fresh", "nickname_lower": "fresh"}
        res = ms._admin_set_xp("Fresh", 1000)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res["before"]["total_xp"], 0)
        self.assertEqual(self.users["uid_fresh"]["stats"]["total_xp"], 1000)
        self.assertEqual(self.users["uid_fresh"]["stats"]["level"], 6)


class LevelCurveAgreementTest(unittest.TestCase):
    """The server's copy of the level curve and the client's must be identical,
    or a server-set XP writes a level the client would disagree with — the
    exact "leaderboard says one thing, profile says another" bug this tool
    exists to avoid."""

    def test_the_two_level_tables_are_the_same_numbers(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "multiplayer/client/js/preview-app.js"),
                  encoding="utf-8") as fh:
            src = fh.read()
        start = src.index("LEVEL_XP_TOTALS")
        body = src[src.index("[", start): src.index("]", start) + 1]
        client = [int(n) for n in __import__("re").findall(r"\d+", body)]
        self.assertEqual(client, ms.LEVEL_XP_TOTALS,
                         "LEVEL_XP_TOTALS drifted between the server and the client")


if __name__ == "__main__":
    unittest.main(verbosity=2)
