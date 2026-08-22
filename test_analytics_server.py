"""Tests for the Developer Analytics API (analytics_server.py).

The dashboard's whole value is that its numbers are RIGHT, a wrong retention
rate or an off-by-one date window is worse than no dashboard, because it gets
believed. So these tests build fixed, hand-countable data and assert the exact
numbers, never "it returned something".

Four jobs, in order of how much damage the bug would do:

 1. THE ADMIN GATE. Every section is player data in aggregate. A non-admin, a
    forged uid, a missing token and an unverifiable token must all bounce with
    the SAME answer, so a probe learns nothing from which one it got.

 2. "NO DATA" IS NOT ZERO. A rate with an empty denominator, an average with
    nothing to average, and a metric whose source is missing must all come back
    as None so the dashboard prints "No data yet". Reporting 0 there reads as
    "this collapsed overnight" and is the easiest way for a dashboard to lie.

 3. THE METRIC MATHS. Retention cohorts (only players who HAVE had the chance
    to return), the date window, the completed-vs-truncated split, per-board
    card win rates, and the filters.

 4. THE PAYLOAD SHAPE the client renders. test_analytics_ui.js drives the real
    renderers with the payloads this file produces, so the two halves cannot
    drift apart while both look green on their own.

Run:  python3 test_analytics_server.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analytics_server as an  # noqa: E402

DAY = 86400
NOW = int(time.time())


# ══════════════════════════════════════════════════════════════════════════
#  A tiny in-memory Firestore, only the reads this module actually makes
# ══════════════════════════════════════════════════════════════════════════
class FakeSnap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeQuery:
    def __init__(self, docs):
        self._docs = docs

    def select(self, _fields):
        return self

    def limit(self, n):
        return FakeQuery(self._docs[:n])

    def stream(self):
        return iter(self._docs)


class FakeCollection(FakeQuery):
    def __init__(self, name, store):
        self._name = name
        self._store = store
        super().__init__([FakeSnap(k, v) for k, v in store.get(name, {}).items()])

    def document(self, doc_id):
        return FakeDoc(self._name, doc_id, self._store)


class FakeDoc:
    def __init__(self, coll, doc_id, store):
        self._coll, self._id, self._store = coll, doc_id, store

    def get(self):
        return FakeSnap(self._id, self._store.get(self._coll, {}).get(self._id))


class FakeDB:
    def __init__(self, store):
        self._store = store

    def collection(self, name):
        return FakeCollection(name, self._store)


# ══════════════════════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════════════════════
ADMIN_UID = "uid-admin"
PLAYER_UID = "uid-player"


def make_users():
    """Six accounts with hand-chosen dates so every cohort is countable.

    joined-90d / joined-40d / joined-20d are all old enough for a 7-day
    retention answer; joined-2d is NOT, and that is the point, it must be
    excluded from the cohort rather than counted as "didn't return".
    """
    return {
        ADMIN_UID: {
            "nickname": "Dev", "email": "currentsandcritters@gmail.com", "is_admin": True,
            "created_at": NOW - 200 * DAY, "last_active": NOW - 60,
            "online": True, "stats": {"completed_games": 999, "critter_coins": 99999, "level": 60},
        },
        # Joined 90d ago, still active today → returned at 1, 7 and 30 days.
        "u-veteran": {
            "nickname": "Reef", "created_at": NOW - 90 * DAY, "last_active": NOW - 2 * 3600,
            "online": True,
            "stats": {"completed_games": 40, "normal_wins": 12, "total_xp": 5000,
                      "level": 22, "critter_coins": 1200, "highest_score": 88},
            "unlocked_icons": ["a", "b", "c"],
        },
        # Joined 40d ago, last seen 38d ago → came back on day 2 only.
        "u-lapsed": {
            "nickname": "Kelp", "created_at": NOW - 40 * DAY, "last_active": NOW - 38 * DAY,
            "stats": {"completed_games": 3, "normal_wins": 1, "total_xp": 210,
                      "level": 4, "critter_coins": 60},
            "unlocked_icons": ["a"],
        },
        # Joined 20d ago, never came back at all (last_active == created_at).
        "u-oneshot": {
            "nickname": "Tide", "created_at": NOW - 20 * DAY, "last_active": NOW - 20 * DAY,
            "stats": {"completed_games": 1, "level": 1, "critter_coins": 0},
        },
        # Joined 2 days ago: too new to have a 7-day answer either way.
        "u-fresh": {
            "nickname": "Sprat", "created_at": NOW - 2 * DAY, "last_active": NOW - 3600,
            "online": True,
            "stats": {"completed_games": 6, "normal_wins": 2, "level": 5, "critter_coins": 300},
        },
        # A guest: excluded unless "Include guest players" is turned on.
        "u-guest": {
            "nickname": "Guest", "guest": True, "created_at": NOW - 10 * DAY,
            "last_active": NOW - 10 * DAY, "stats": {"completed_games": 1},
        },
    }


def game(when, *, completed=True, players=4, humans=2, winner="Reef",
         names=("Reef", "Kelp"), duration=900, animals=None, team=False, boards=None):
    """One game-history record shaped exactly like multiplayer_server writes.

    `animals` gives every player the same board; `boards` (a list, one entry per
    name) gives them different ones, which is the only way to build a card that
    is genuinely lopsided, since a card on BOTH boards wins exactly half the time
    no matter how strong it is.
    """
    animals = animals or [("Mandarin Goby", "Bait Fish")]
    per_player = boards or [animals] * len(names)
    return {
        "room_id": "AAAAA",
        "recorded_unix": when,
        "mode": "standard" if completed else "truncated",
        "player_count": players,
        "human_count": humans,
        "winner": winner,
        "team_mode": team,
        "team_count": 2 if team else 0,
        "started_unix": when - duration,
        "ended_unix": when,
        "duration_sec": duration,
        "rounds": 9,
        "standings": [{"name": n, "score": 50 - i * 10} for i, n in enumerate(names)],
        "players": [{
            "name": n, "score": 50 - i * 10, "strategy": "Bait Fish", "is_human": True,
            "seat_index": i,
            "board": [{"ocean": "Pacific Ocean",
                       "animals": [{"name": a, "species": s, "uid": 101}
                                   for a, s in per_player[i]]}],
        } for i, n in enumerate(names)],
    }


class Handler:
    """Stands in for the BaseHTTPRequestHandler the module answers through."""
    def __init__(self):
        self.payload = None
        self.status = 200

    def _send_json(self, payload, status=200):
        # Round-trip through JSON: a payload the real server can't serialise is
        # a 500 in production, and this is where that gets caught.
        self.payload = json.loads(json.dumps(payload))
        self.status = status


class Parsed:
    def __init__(self, path):
        self.path = path


class AnalyticsTestCase(unittest.TestCase):
    """Shared wiring: a temp history dir and a fake Firestore, torn down clean."""

    users = None
    games = None
    comp_games = None
    live = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cc-analytics-test-")
        self.games_dir = os.path.join(self.tmp, "games")
        self.comp_dir = os.path.join(self.tmp, "comp")
        os.makedirs(self.games_dir)
        os.makedirs(self.comp_dir)

        for i, rec in enumerate(self.games or []):
            with open(os.path.join(self.games_dir, f"game_R{i:04d}_{rec['recorded_unix']}.json"),
                      "w", encoding="utf-8") as fh:
                json.dump(rec, fh)
        for i, rec in enumerate(self.comp_games or []):
            with open(os.path.join(self.comp_dir, f"game_C{i:04d}_{rec['recorded_unix']}.json"),
                      "w", encoding="utf-8") as fh:
                json.dump(rec, fh)

        self.store = {"users": self.users if self.users is not None else make_users()}
        self.db = FakeDB(self.store)
        self.live_payload = self.live or {
            "ok": True, "status_note": "All checks passing.", "online_players": 3,
            "active_games": 2, "open_lobbies": 1, "matchmaking": 0, "stuck_rooms": 0,
            "load": {"rooms": 5, "threads": 20, "deep_plan_slots": 2,
                     "deep_plan_granted": 100, "deep_plan_skipped": 3},
        }

        an.init(
            get_firestore=lambda: self.db,
            verify_token=self.verify,
            games_history_dir=self.games_dir,
            competitive_games_dir=self.comp_dir,
            live_snapshot=lambda: self.live_payload,
            app_version="1.6.50 (test)",
        )
        self.clear_caches()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.clear_caches()

    @staticmethod
    def clear_caches():
        an._USERS_CACHE["rows"] = None
        an._USERS_CACHE["at"] = 0.0
        an._GAMES_CACHE["sig"] = None
        an._GAMES_CACHE["rows"] = None
        an._COMP_CACHE["sig"] = None
        an._COMP_CACHE["rows"] = None

    @staticmethod
    def verify(token):
        return {
            "admin-token": {"uid": ADMIN_UID, "email": "currentsandcritters@gmail.com"},
            "player-token": {"uid": PLAYER_UID, "email": "someone@example.com"},
            "flagged-admin-token": {"uid": "uid-flagged"},
        }.get(token)

    def call(self, section, **body):
        h = Handler()
        body.setdefault("idToken", "admin-token")
        handled = an.handle_post(h, Parsed("/api/analytics/" + section), body)
        self.assertTrue(handled, "handle_post should claim /api/analytics/*")
        return h


# ══════════════════════════════════════════════════════════════════════════
#  1, THE ADMIN GATE
# ══════════════════════════════════════════════════════════════════════════
class TestAdminGate(AnalyticsTestCase):
    games = [game(NOW - DAY)]

    def test_admin_email_is_allowed(self):
        h = self.call("overview")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.payload["ok"])

    def test_is_admin_flag_is_allowed_without_the_known_email(self):
        # The gate is not hard-coded to one address: an account FLAGGED admin in
        # Firestore gets in, which is what makes a second dev account possible.
        self.store["users"]["uid-flagged"] = {"nickname": "Dev2", "is_admin": True,
                                              "email": "other@example.com"}
        h = self.call("overview", idToken="flagged-admin-token")
        self.assertEqual(h.status, 200)

    def test_ordinary_player_is_refused(self):
        self.store["users"][PLAYER_UID] = {"nickname": "Someone", "is_admin": False}
        h = self.call("overview", idToken="player-token")
        self.assertEqual(h.status, 403)
        self.assertFalse(h.payload["ok"])

    def test_missing_and_bogus_tokens_are_refused(self):
        for token in ("", "not-a-token", None):
            h = self.call("overview", idToken=token)
            self.assertEqual(h.status, 403, f"token {token!r} must be refused")

    def test_a_forged_uid_in_the_body_is_ignored(self):
        # The uid is never read from the body, only from the verified token.
        h = self.call("overview", idToken="player-token", uid=ADMIN_UID,
                      is_admin=True, email="currentsandcritters@gmail.com")
        self.assertEqual(h.status, 403)

    def test_every_refusal_gives_the_same_answer(self):
        # A probe must not be able to tell "wrong account" from "bad token".
        answers = set()
        self.store["users"][PLAYER_UID] = {"nickname": "Someone"}
        for token in ("", "bogus", "player-token"):
            h = self.call("overview", idToken=token)
            answers.add((h.status, json.dumps(h.payload, sort_keys=True)))
        self.assertEqual(len(answers), 1, f"refusals differ and leak information: {answers}")

    def test_non_analytics_paths_are_not_claimed(self):
        h = Handler()
        self.assertFalse(an.handle_post(h, Parsed("/api/clan/home"), {}))
        self.assertIsNone(h.payload)

    def test_unknown_section_is_404_not_a_crash(self):
        h = self.call("not-a-section")
        self.assertEqual(h.status, 404)

    def test_every_named_section_answers(self):
        for name in list(an._SECTIONS) + ["search", "export"]:
            h = self.call(name, query="Reef")
            self.assertEqual(h.status, 200, f"{name} should answer 200")
            self.assertTrue(h.payload["ok"], f"{name} returned ok=False: {h.payload}")


# ══════════════════════════════════════════════════════════════════════════
#  2, "NO DATA" IS NOT ZERO
# ══════════════════════════════════════════════════════════════════════════
class TestEmptyIsNotZero(AnalyticsTestCase):
    users = {}
    games = []
    comp_games = []

    def test_rates_with_an_empty_denominator_are_none(self):
        self.assertIsNone(an._pct(0, 0))
        self.assertIsNone(an._pct(5, 0))
        self.assertEqual(an._pct(1, 4), 25.0)

    def test_average_of_nothing_is_none(self):
        self.assertIsNone(an._mean([]))
        self.assertIsNone(an._median([]))
        self.assertEqual(an._mean([2, 4]), 3)

    def test_overview_on_an_empty_game_reports_no_data_not_zeros(self):
        d = self.call("overview").payload
        by = {c["label"]: c for c in d["cards"]}
        for label in ("Games finished", "Came back after 7 days", "Average game length"):
            self.assertIsNone(by[label]["value"],
                              f"{label} must be None (No data yet), not a number, on empty data")
        # Counts, on the other hand, ARE genuinely zero and must say so.
        self.assertEqual(by["New players"]["value"], 0)

    def test_no_baseline_means_no_delta(self):
        # "+100%" against a zero previous period is noise; the card shows nothing.
        self.assertIsNone(an._delta(5, 0))
        self.assertIsNone(an._delta(5, None))
        self.assertEqual(an._delta(150, 100), 50.0)

    def test_without_firestore_the_game_half_still_works(self):
        # The admin email in a VERIFIED token is proof on its own, so the
        # dashboard still opens when Firebase is down, the account numbers go
        # to "no data" and an alert says why, rather than the tool going dark.
        an.init(get_firestore=lambda: None, verify_token=self.verify,
                games_history_dir=self.games_dir, competitive_games_dir=self.comp_dir,
                live_snapshot=lambda: self.live_payload, app_version="")
        self.clear_caches()
        h = Handler()
        an.handle_post(h, Parsed("/api/analytics/overview"), {"idToken": "admin-token"})
        self.assertEqual(h.status, 200)
        self.assertTrue(h.payload["ok"])
        titles = [a["title"] for a in h.payload["alerts"]]
        self.assertIn("Player database not connected", titles)

    def test_without_firestore_a_flag_only_admin_cannot_be_confirmed(self):
        # An account whose admin-ness lives ONLY in Firestore can't be verified
        # with Firestore down, so it is refused rather than assumed.
        an.init(get_firestore=lambda: None, verify_token=self.verify,
                games_history_dir=self.games_dir, competitive_games_dir=self.comp_dir,
                live_snapshot=lambda: self.live_payload, app_version="")
        self.clear_caches()
        h = Handler()
        an.handle_post(h, Parsed("/api/analytics/overview"), {"idToken": "flagged-admin-token"})
        self.assertEqual(h.status, 403)


class TestEmptyHistoryIsNotAFault(AnalyticsTestCase):
    """A server that has recorded no games yet is not a broken server.

    The Technical tab used to fail the "Game records" check on an empty history
    directory, which dragged the Server card to "Needs attention", so a freshly
    deployed box, or one whose disk had just been reset, opened permanently red
    with nothing actually wrong. A check that is always red is a check nobody
    reads. A directory that is MISSING or unwritable is the real fault, and that
    one still has to be loud, and has to name the path so it can be fixed.
    """
    users = {}
    games = []
    comp_games = []

    def _records_check(self):
        d = self.call("technical").payload
        return {c["label"]: c for c in d["checks"]}["Game records"]

    def _server_card(self):
        d = self.call("technical").payload
        return {c["label"]: c for c in d["cards"]}["Server"]

    def test_an_empty_but_writable_directory_passes(self):
        check = self._records_check()
        self.assertTrue(check["ok"], "an empty history directory is not a fault")
        self.assertIn("No games recorded yet", check["detail"])
        # It still has to say WHERE it looked, or an empty dashboard is a dead
        # end for whoever has to work out why nothing is arriving.
        self.assertIn(self.games_dir, check["detail"])

    def test_an_empty_directory_leaves_the_server_healthy(self):
        self.assertEqual(self._server_card()["value"], "Healthy")

    def test_a_missing_directory_is_a_fault_and_names_the_path(self):
        missing = os.path.join(self.tmp, "gone")
        an.init(get_firestore=lambda: self.db, verify_token=self.verify,
                games_history_dir=missing, competitive_games_dir=self.comp_dir,
                live_snapshot=lambda: self.live_payload, app_version="")
        self.clear_caches()
        check = self._records_check()
        self.assertFalse(check["ok"], "a missing history directory IS a fault")
        self.assertIn(missing, check["detail"])
        self.assertEqual(self._server_card()["value"], "Needs attention")

    def test_an_unconfigured_directory_is_a_fault(self):
        an.init(get_firestore=lambda: self.db, verify_token=self.verify,
                games_history_dir="", competitive_games_dir=self.comp_dir,
                live_snapshot=lambda: self.live_payload, app_version="")
        self.clear_caches()
        check = self._records_check()
        self.assertFalse(check["ok"])
        self.assertIn("No history directory is configured", check["detail"])

    def test_an_unwritable_directory_is_a_fault_even_though_it_reads_fine(self):
        locked = os.path.join(self.tmp, "locked")
        os.makedirs(locked)
        os.chmod(locked, 0o500)
        try:
            an.init(get_firestore=lambda: self.db, verify_token=self.verify,
                    games_history_dir=locked, competitive_games_dir=self.comp_dir,
                    live_snapshot=lambda: self.live_payload, app_version="")
            self.clear_caches()
            check = self._records_check()
            if os.access(locked, os.W_OK):
                self.skipTest("running as a user that ignores the mode bits")
            self.assertFalse(check["ok"])
            self.assertIn("not writable", check["detail"])
        finally:
            os.chmod(locked, 0o700)


class TestHistoryPresentStillCounts(AnalyticsTestCase):
    """The happy path has to keep saying the number, not just "fine"."""

    games = [game(NOW - 2 * DAY, completed=True, duration=600),
             game(NOW - 3 * DAY, completed=True, duration=900)]

    def test_a_directory_with_games_reports_the_count(self):
        d = self.call("technical").payload
        check = {c["label"]: c for c in d["checks"]}["Game records"]
        self.assertTrue(check["ok"])
        self.assertIn("games on disk", check["detail"])


# ══════════════════════════════════════════════════════════════════════════
#  3, THE METRIC MATHS
# ══════════════════════════════════════════════════════════════════════════
class TestRetention(AnalyticsTestCase):
    games = [game(NOW - DAY)]

    def rows(self):
        return an._filter_users(an._load_users(force=True), {})

    def test_cohort_excludes_players_too_new_to_have_returned(self):
        # u-fresh joined 2 days ago. At day 7 it cannot be in the cohort, and
        # counting it as "didn't return" is what drags a growing game's
        # retention down for no real reason.
        r = an._retention(self.rows(), 7, NOW)
        names = {u["nickname"] for u in self.rows() if NOW - u["created_at"] >= 7 * DAY}
        self.assertEqual(r["cohort"], len(names))
        self.assertNotIn("Sprat", names)

    def test_returned_counts_only_activity_after_the_window(self):
        # Reef (90d, active today) returned. Kelp last played on day 2 → not a
        # 7-day return. Tide never came back at all.
        r = an._retention(self.rows(), 7, NOW)
        self.assertEqual(r["returned"], 1, "only Reef should count as a 7-day return")
        self.assertEqual(r["cohort"], 3)
        self.assertEqual(r["rate"], 33.3)

    def test_day_one_counts_the_two_day_player(self):
        r = an._retention(self.rows(), 1, NOW)
        self.assertEqual(r["returned"], 3, "Reef, Kelp and Sprat all came back after a day")

    def test_no_eligible_cohort_is_none_not_zero_percent(self):
        fresh = [{"created_at": NOW - 3600, "last_active": NOW}]
        self.assertIsNone(an._retention(fresh, 7, NOW))


class TestGameMetrics(AnalyticsTestCase):
    games = [
        game(NOW - 2 * DAY, completed=True, duration=600),
        game(NOW - 2 * DAY, completed=True, duration=1200),
        game(NOW - 3 * DAY, completed=False),
        game(NOW - 60 * DAY, completed=True, duration=600),      # outside a 30-day range
    ]

    def test_date_window_excludes_older_games(self):
        d = self.call("gameplay", days=30).payload
        by = {c["label"]: c for c in d["cards"]}
        self.assertEqual(by["Games completed"]["value"], 2)
        self.assertEqual(by["Games players left early"]["value"], 1)

    def test_a_wider_range_pulls_the_old_game_back_in(self):
        d = self.call("gameplay", days=90).payload
        by = {c["label"]: c for c in d["cards"]}
        self.assertEqual(by["Games completed"]["value"], 3)

    def test_completion_rate_is_completed_over_started(self):
        d = self.call("gameplay", days=30).payload
        by = {c["label"]: c for c in d["cards"]}
        self.assertEqual(by["Games finished"]["value"], 66.7)   # 2 of 3

    def test_average_length_uses_only_games_that_recorded_one(self):
        # A record with no timing must be LEFT OUT, not averaged in as zero.
        no_timing = game(NOW - DAY, duration=900)
        for k in ("started_unix", "ended_unix", "duration_sec"):
            no_timing[k] = 0
        path = os.path.join(self.games_dir, f"game_NOTIME_{no_timing['recorded_unix']}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(no_timing, fh)
        self.clear_caches()
        d = self.call("gameplay", days=30).payload
        by = {c["label"]: c for c in d["cards"]}
        self.assertEqual(by["Average game length"]["value"], 15.0)   # (600+1200)/2 = 900s
        self.assertIsNone(an._game_duration(no_timing))

    def test_daily_series_lines_up_with_the_day_labels(self):
        d = self.call("gameplay", days=30).payload
        vol = d["volume"]
        self.assertEqual(len(vol["days"]), len(vol["completed"]))
        self.assertEqual(sum(vol["completed"]), 2)
        self.assertEqual(sum(vol["left_early"]), 1)


class TestFilters(AnalyticsTestCase):
    games = [
        game(NOW - DAY, humans=2),
        game(NOW - DAY, humans=1),                       # mostly bots
        game(NOW - DAY, humans=2, players=2, team=True),
    ]

    def test_bot_games_are_out_by_default_and_in_when_asked(self):
        off = self.call("gameplay", days=30).payload
        on = self.call("gameplay", days=30, include_bots=True).payload
        self.assertEqual({c["label"]: c["value"] for c in off["cards"]}["Games completed"], 2)
        self.assertEqual({c["label"]: c["value"] for c in on["cards"]}["Games completed"], 3)

    def test_mode_filter_narrows_to_team_games(self):
        d = self.call("gameplay", days=30, mode="team").payload
        self.assertEqual({c["label"]: c["value"] for c in d["cards"]}["Games completed"], 1)

    def test_player_count_filter(self):
        d = self.call("gameplay", days=30, player_count=2).payload
        self.assertEqual({c["label"]: c["value"] for c in d["cards"]}["Games completed"], 1)

    def test_the_dev_account_is_excluded_from_player_numbers_by_default(self):
        off = self.call("players", days=365).payload
        on = self.call("players", days=365, include_test=True).payload
        total_off = {c["label"]: c["value"] for c in off["cards"]}["Total accounts"]
        total_on = {c["label"]: c["value"] for c in on["cards"]}["Total accounts"]
        self.assertEqual(total_on, total_off + 1,
                         "the admin account should only appear with test accounts on")

    def test_guests_are_excluded_by_default(self):
        off = self.call("players", days=365).payload
        on = self.call("players", days=365, include_guests=True).payload
        self.assertEqual({c["label"]: c["value"] for c in on["cards"]}["Total accounts"],
                         {c["label"]: c["value"] for c in off["cards"]}["Total accounts"] + 1)

    def test_range_is_clamped_to_something_sane(self):
        self.assertEqual(an._filters({"days": 0})["days"], 1)
        self.assertEqual(an._filters({"days": 99999})["days"], an.MAX_RANGE_DAYS)
        self.assertEqual(an._filters({"days": "nonsense"})["days"], an.DEFAULT_RANGE_DAYS)


# Blue Tang sits on BOTH boards, so it wins exactly half the time, that is the
# baseline. Mandarin Goby only ever sits on the LOSER's board, so its win rate is
# 0% and it should be flagged as weak.
_TANG = [("Blue Tang", "Reef Fish")]
_TANG_AND_GOBY = [("Blue Tang", "Reef Fish"), ("Mandarin Goby", "Bait Fish")]


class TestCardBalance(AnalyticsTestCase):
    games = [game(NOW - DAY, winner="Reef", names=("Reef", "Kelp"),
                  boards=[_TANG, _TANG_AND_GOBY]) for _ in range(12)]

    def test_win_rate_is_per_board_not_per_copy(self):
        # A board holding three copies is still ONE win. Counting copies would
        # let a stackable animal inflate its own rate above 100%.
        rec = game(NOW - DAY, winner="Reef", names=("Reef",),
                   animals=[("Blue Tang", "Reef Fish")] * 3)
        with open(os.path.join(self.games_dir, "game_STACK_1.json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        self.clear_caches()
        d = self.call("cards", days=30, min_sample=1, include_bots=True).payload
        row = next(r for r in d["table"]["rows"] if r["name"] == "Blue Tang")
        self.assertLessEqual(row["win_rate"], 100.0)
        self.assertGreater(row["played"], row["boards"], "played counts copies, boards counts boards")

    def test_small_samples_are_never_flagged_for_balance(self):
        d = self.call("cards", days=30, min_sample=1000).payload
        self.assertEqual(d["review"], [], "nothing should be flagged when nothing meets the sample")
        by = {c["label"]: c for c in d["cards"]}
        self.assertIsNone(by["Typical win rate"]["value"])
        self.assertIsNone(by["Worth a balance look"]["value"])

    def test_a_lopsided_card_is_flagged_once_the_sample_is_met(self):
        d = self.call("cards", days=30, min_sample=4).payload
        flagged = {r["name"] for r in d["review"]}
        self.assertIn("Mandarin Goby", flagged,
                      "an animal that never appears on a winning board should be flagged")
        goby = next(r for r in d["review"] if r["name"] == "Mandarin Goby")
        self.assertEqual(goby["direction"], "weak")
        self.assertEqual(goby["win_rate"], 0.0)

    def test_min_sample_is_reported_back_so_the_ui_can_say_it(self):
        self.assertEqual(self.call("cards", min_sample=7).payload["min_sample"], 7)


class TestAlerts(AnalyticsTestCase):
    # Well over the minimum, and two thirds of them abandoned.
    games = ([game(NOW - DAY, completed=False) for _ in range(20)]
             + [game(NOW - DAY, completed=True) for _ in range(10)])

    def test_a_real_completion_problem_raises_one_alert(self):
        d = self.call("overview", days=30).payload
        titles = [a["title"] for a in d["alerts"]]
        self.assertIn("Players are leaving games early", titles)

    def test_overview_never_shows_more_than_three_alerts(self):
        self.live = {"ok": False, "status_note": "Down", "online_players": 0,
                     "active_games": 0, "stuck_rooms": 9, "load": {}}
        self.setUp()
        d = self.call("overview", days=30).payload
        self.assertLessEqual(len(d["alerts"]), 3)

    def test_technical_health_carries_the_full_list(self):
        self.live = {"ok": False, "status_note": "Down", "online_players": 0,
                     "active_games": 0, "stuck_rooms": 9, "load": {}}
        self.setUp()
        overview = self.call("overview", days=30).payload
        technical = self.call("technical", days=30).payload
        self.assertGreaterEqual(len(technical["alerts"]), len(overview["alerts"]))


class TestQuietData(AnalyticsTestCase):
    """A handful of games must not set anything off, a dashboard that cries
    wolf on a 3-game day trains its owner to ignore it."""
    games = [game(NOW - DAY, completed=False), game(NOW - DAY, completed=True)]

    def test_a_tiny_sample_raises_no_completion_alert(self):
        d = self.call("overview", days=30).payload
        titles = [a["title"] for a in d["alerts"]]
        self.assertNotIn("Players are leaving games early", titles)


# ══════════════════════════════════════════════════════════════════════════
#  4, THE PAYLOAD SHAPE THE CLIENT RENDERS
# ══════════════════════════════════════════════════════════════════════════
class TestPayloadShape(AnalyticsTestCase):
    games = [game(NOW - DAY), game(NOW - 2 * DAY, completed=False)]
    comp_games = [{
        "room_id": "CCCCC", "recorded_unix": NOW - DAY, "p1_name": "Reef", "p2_name": "Kelp",
        "winner": "Reef", "loser": "Kelp", "is_draw": False, "forfeit": False,
        "ranked": True, "turn_count": 14,
    }]

    def test_overview_has_ten_or_fewer_cards_and_exactly_four_blocks(self):
        d = self.call("overview").payload
        self.assertLessEqual(len(d["cards"]), 10,
                             "the Overview is capped at ten cards on purpose")
        for block in ("growth", "games", "retention", "live"):
            self.assertIn(block, d)

    def test_every_card_has_the_fields_the_renderer_reads(self):
        for name in an._SECTIONS:
            for c in self.call(name).payload.get("cards", []):
                for key in ("label", "value", "unit", "delta", "hint", "spark", "tone"):
                    self.assertIn(key, c, f"{name} card {c.get('label')!r} is missing {key}")
                self.assertIsInstance(c["spark"], list)

    def test_series_and_day_labels_are_always_the_same_length(self):
        d = self.call("overview").payload
        days = d["growth"]["days"]
        for key, values in d["growth"]["series"].items():
            self.assertEqual(len(values), len(days), f"growth.{key} is not day-aligned")
        self.assertEqual(len(d["games"]["completed"]), len(d["games"]["days"]))

    def test_tables_declare_their_columns(self):
        for name in ("players", "cards", "competitive", "clans"):
            table = self.call(name).payload.get("table") or {}
            self.assertIn("columns", table, f"{name} table has no columns")
            self.assertTrue(any(c.get("always") for c in table["columns"]),
                            f"{name} table must have at least one always-on column")

    def test_search_finds_a_player_and_returns_their_games(self):
        d = self.call("search", query="Reef").payload
        self.assertTrue(d["matches"])
        self.assertEqual(d["player"]["name"], "Reef")
        self.assertTrue(d["player"]["recent"])

    def test_search_needs_at_least_two_characters(self):
        d = self.call("search", query="R").payload
        self.assertEqual(d["matches"], [])
        self.assertIsNone(d["player"])

    def test_search_reaches_the_dev_account_even_though_charts_exclude_it(self):
        # Search is a lookup tool, not a measurement, it must find everyone.
        d = self.call("search", query="Dev").payload
        self.assertTrue(d["matches"], "search should still find the developer account")

    def test_export_carries_every_section(self):
        d = self.call("export").payload
        self.assertEqual(set(d["sections"]), set(an._SECTIONS))

    def test_no_email_or_uid_ever_leaves_the_module(self):
        # The account scan reads emails to identify test accounts; none of that
        # may reach the browser.
        for name in list(an._SECTIONS) + ["search"]:
            blob = json.dumps(self.call(name, query="Reef").payload)
            self.assertNotIn("@", blob, f"{name} leaked an email address")
            self.assertNotIn(ADMIN_UID, blob, f"{name} leaked a uid")
            self.assertNotIn("uid-veteran", blob)

    def test_a_broken_section_returns_an_error_instead_of_a_500(self):
        broken = dict(an._SECTIONS)
        original = an._SECTIONS.copy()
        try:
            an._SECTIONS["gameplay"] = lambda f: (_ for _ in ()).throw(RuntimeError("boom"))
            h = self.call("gameplay")
            self.assertEqual(h.status, 200)
            self.assertFalse(h.payload["ok"])
            self.assertEqual(h.payload["error"], "section_failed")
        finally:
            an._SECTIONS.clear()
            an._SECTIONS.update(original)
        del broken


# ══════════════════════════════════════════════════════════════════════════
#  5: CACHING (a dashboard left open must not rescan Firestore per tick)
# ══════════════════════════════════════════════════════════════════════════
class TestCaching(AnalyticsTestCase):
    games = [game(NOW - DAY)]

    def test_the_account_scan_is_cached(self):
        calls = {"n": 0}

        class Counting(FakeDB):
            def collection(self, name):
                if name == "users":
                    calls["n"] += 1
                return super().collection(name)

        self.db = Counting(self.store)
        an.init(get_firestore=lambda: self.db, verify_token=self.verify,
                games_history_dir=self.games_dir, competitive_games_dir=self.comp_dir,
                live_snapshot=lambda: self.live_payload, app_version="")
        self.clear_caches()
        an._load_users()
        first = calls["n"]
        an._load_users()
        an._load_users()
        self.assertEqual(calls["n"], first, "the user scan should be served from cache")

    def test_a_new_game_invalidates_the_game_cache_immediately(self):
        self.assertEqual(len(an._load_games()), 1)
        rec = game(NOW - 3600)
        # A distinct mtime, so the (count, newest-mtime) signature really moves.
        path = os.path.join(self.games_dir, "game_NEW_1.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        os.utime(path, (time.time() + 5, time.time() + 5))
        self.assertEqual(len(an._load_games()), 2,
                         "a finished game must show up without waiting for a TTL")

    def test_unreadable_records_are_skipped_not_fatal(self):
        with open(os.path.join(self.games_dir, "game_BAD_1.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.clear_caches()
        self.assertEqual(len(an._load_games()), 1)


# ══════════════════════════════════════════════════════════════════════════
#  6, THE RECORD THE SERVER ACTUALLY WRITES
# ══════════════════════════════════════════════════════════════════════════
class TestGameRecordContract(unittest.TestCase):
    """The timing fields the dashboard reads have to be the ones
    multiplayer_server writes. If _save_game_history drops them, "how long is a
    game" silently becomes unanswerable for every new record."""

    def test_save_game_history_writes_the_timing_fields(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "multiplayer_server.py"), "r", encoding="utf-8") as fh:
            src = fh.read()
        start = src.index("def _save_game_history")
        body = src[start:start + 8000]
        for field in ('"started_unix"', '"ended_unix"', '"duration_sec"', '"rounds"'):
            self.assertIn(field, body,
                          f"_save_game_history must still write {field}: analytics reads it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
