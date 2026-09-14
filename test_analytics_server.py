"""Tests for the Developer Analytics API (analytics_server.py).

The dashboard's whole value is that its numbers are RIGHT, a wrong retention
rate or an off-by-one date window is worse than no dashboard, because it gets
believed. So these tests build fixed, hand-countable data and assert the exact
numbers, never "it returned something".

THE FIXTURES ARE ACCOUNTS, BECAUSE THE GAME'S RECORD IS THE ACCOUNTS
Every account here carries what the game client really writes: its lifetime
counters, `stats.recent_games` (one entry per finished game, in saveGameStats'
own shape, Date.now() milliseconds and all) and `stats.streak_days`. This file
used to build game-history FILES instead, and every test passed while the live
dashboard showed nothing: the live server's history directory held one game and
the accounts held 435. TestTheAccountsAreTheSource pins that down, and
TestAccountContract checks the field names against preview-app.js itself.

What is covered, in order of how much damage the bug would do:
 1. THE ADMIN GATE. A non-admin, a forged uid, a missing token and an
    unverifiable token must all bounce with the SAME answer.
 2. "NO DATA" IS NOT ZERO. An empty denominator is None ("No data yet").
 3. THE METRIC MATHS. Date ranges and Lifetime, players on the game vs players
    who played, the 50-game log's coverage, who was at a table, per-board card
    win rates, the two Competitive tables, the filters.
 4. THE PAYLOAD SHAPE the client renders (test_analytics_ui.js drives the real
    renderers with payloads this file's fixtures produce).
 5. CACHING, because every account scan is Firestore reads.

Run:  python3 test_analytics_server.py
"""
from __future__ import annotations

import calendar
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
ADMIN_EMAIL = "currentsandcritters@gmail.com"
HERE = os.path.dirname(os.path.abspath(__file__))


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
    """Collections are store keys. A subcollection is keyed by its full path,
    "supporters/u1/payments", which is what a collection-group query finds."""
    def __init__(self, store):
        self._store = store

    def collection(self, name):
        return FakeCollection(name, self._store)

    def collection_group(self, name):
        docs = []
        for path, items in self._store.items():
            if path.split("/")[-1] == name:
                docs.extend(FakeSnap(k, v) for k, v in items.items())
        return FakeQuery(docs)


# ══════════════════════════════════════════════════════════════════════════
#  Fixtures: games the way the client logs them, accounts the way it saves them
# ══════════════════════════════════════════════════════════════════════════
ADMIN_UID = "uid-admin"
PLAYER_UID = "uid-player"
CARD_SPECIES = {"Blue Tang": "Reef Fish", "Mandarin Goby": "Baitfish", "Great White": "Shark"}


def table(when, seats, *, winner=None, mode="normal", boards=None, strats=None):
    """One finished game: `seats` is ((name, final score), ...) in seat order.

    `boards` maps a name to [(ocean, (animal, ...)), ...]; anyone left out gets
    one Blue Tang on the Pacific. `strats` maps a name to the strategy the game
    detected on that board."""
    seats = [(n, s) for n, s in seats]
    return {"when": int(when), "seats": seats,
            "winner": winner or max(seats, key=lambda x: x[1])[0],
            "mode": mode, "boards": boards or {}, "strats": strats or {}}


def logged(game, name, lag=0):
    """One `stats.recent_games` entry, exactly as saveGameStats builds it."""
    names = [n for n, _s in game["seats"]]
    return {
        "r": 1 if game["winner"] == name else 0,
        "s": dict(game["seats"])[name],
        "t": (game["when"] + lag) * 1000,                  # Date.now(): milliseconds
        "opp": [n for n in names if n.lower() != name.lower()][:8],
        "pc": len(names),
        "mode": game["mode"],
        "all": sorted(({"n": n, "s": s} for n, s in game["seats"]), key=lambda x: -x["s"]),
        "bds": [{"n": n, "b": [{"o": ocean, "a": list(animals)}
                               for ocean, animals in (game["boards"].get(n)
                                                      or [("Pacific Ocean", ("Blue Tang",))])]}
                for n in names],
        "win": game["winner"],
        "strat": game["strats"].get(name, "King Salmon"),
    }


def day_of(unix):
    return time.strftime("%Y-%m-%d", time.gmtime(unix))


def account(name, games=(), *, created, seen, online=False, older=0, stats=None, **top):
    """An account document as the game leaves it after playing `games`.

    The counters are built from the same games the log holds, so the two agree
    unless a test says otherwise: `older` adds finished games that are in the
    counters but no longer in the 50-game log (or never were)."""
    mine = sorted((g for g in games if name in dict(g["seats"])), key=lambda g: -g["when"])
    by_size, comp_by_size = {}, {}
    for g in mine:
        target = comp_by_size if g["mode"] == "competitive" else by_size
        key = str(len(g["seats"]))
        target[key] = target.get(key, 0) + 1
    if older:
        by_size["4"] = by_size.get("4", 0) + older
    st = {
        "completed_games": len(mine) + older,
        "normal_games_by_size": by_size,
        "comp_games_by_size": comp_by_size,
        "normal_wins": sum(1 for g in mine if g["winner"] == name and g["mode"] != "competitive"),
        "total_score": sum(dict(g["seats"])[name] for g in mine),
        "streak_days": sorted({day_of(g["when"]) for g in mine}),
        # Newest first, 50 at most, and each save lands a few seconds apart.
        "recent_games": [logged(g, name, lag=i % 7) for i, g in enumerate(mine)][:50],
    }
    st.update(stats or {})
    doc = {"nickname": name, "created_at": int(created), "last_active": int(seen),
           "online": online, "stats": st}
    doc.update(top)
    return doc


# The base world, used wherever a test only needs "some players":
#   Reef  joined 90d ago, on 2h ago: played Sprat (1d), a bot (3d), the dev (5d)
#         and Kelp (39d, outside a 30-day range).
#   Kelp  joined 40d, last seen 38d.   Tide joined 20d, never back, 1 old game.
#   Sprat joined 2d ago, on 1h ago.     Dev and Guest are filtered out by default.
G_SPRAT = table(NOW - DAY + 3600, (("Reef", 50), ("Sprat", 40)))
G_BOTS = table(NOW - 3 * DAY, (("Reef", 45), ("Starter Reef", 30)))
G_DEV = table(NOW - 5 * DAY, (("Dev", 20), ("Reef", 55)))
G_KELP = table(NOW - 39 * DAY, (("Reef", 35), ("Kelp", 44)))
BASE_GAMES = [G_SPRAT, G_BOTS, G_DEV, G_KELP]


def make_users():
    """Six accounts with hand-chosen dates so every cohort is countable.

    Reef / Kelp / Tide are all old enough for a 7-day retention answer; Sprat
    is NOT, and that is the point, it must be excluded from the cohort rather
    than counted as "didn't return"."""
    return {
        ADMIN_UID: account("Dev", BASE_GAMES, created=NOW - 200 * DAY, seen=NOW - 60, online=True,
                           email=ADMIN_EMAIL, is_admin=True,
                           stats={"critter_coins": 99999, "level": 60}),
        "uid-veteran": account("Reef", BASE_GAMES, created=NOW - 90 * DAY, seen=NOW - 2 * 3600,
                               online=True, unlocked_icons=["a", "b", "c"],
                               stats={"total_xp": 5000, "level": 22, "critter_coins": 1200,
                                      "highest_score": 88, "hours_played": 12.5,
                                      "strategy_play_counts": {"King Salmon": 3, "Bait Ball": 1},
                                      "most_played_strategy": "King Salmon"}),
        "uid-lapsed": account("Kelp", BASE_GAMES, created=NOW - 40 * DAY, seen=NOW - 38 * DAY,
                              unlocked_icons=["a"],
                              stats={"total_xp": 210, "level": 4, "critter_coins": 60,
                                     "hours_played": 1.25}),
        "uid-oneshot": account("Tide", created=NOW - 20 * DAY, seen=NOW - 20 * DAY,
                               stats={"completed_games": 1, "level": 1}),
        "uid-fresh": account("Sprat", BASE_GAMES, created=NOW - 2 * DAY, seen=NOW - 3600, online=True,
                             stats={"level": 5, "critter_coins": 300}),
        "uid-guest": account("Guest", created=NOW - 10 * DAY, seen=NOW - 10 * DAY, guest=True,
                             stats={"completed_games": 1}),
    }


def disk_game(when, *, completed=True, players=4, humans=2, winner="Reef",
              names=("Reef", "Kelp"), duration=900, team=False, ranked=False, competitive=False):
    """One game record in the server's history directory, as
    multiplayer_server._save_game_history writes it. Only Technical Health and
    the leaving-early alert still read these."""
    return {
        "room_id": "AAAAA",
        "recorded_unix": when,
        "mode": ("competitive" if competitive else "ranked" if ranked else "standard"
                 ) if completed else "truncated",
        "ranked": ranked,
        "competitive": competitive,
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
        "players": [{"name": n, "score": 50 - i * 10, "strategy": "Bait Fish", "is_human": True,
                     "seat_index": i, "board": []} for i, n in enumerate(names)],
    }


def cards(payload):
    return {c["label"]: c["value"] for c in payload.get("cards", [])}


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
    extra_store = None
    disk = None
    live = None
    clan_season = "2026-Q3"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cc-analytics-test-")
        self.games_dir = os.path.join(self.tmp, "games")
        os.makedirs(self.games_dir)
        for i, rec in enumerate(self.disk or []):
            with open(os.path.join(self.games_dir, f"game_R{i:04d}_{rec['recorded_unix']}.json"),
                      "w", encoding="utf-8") as fh:
                json.dump(rec, fh)

        self.store = {"users": self.users if self.users is not None else make_users()}
        self.store.update(json.loads(json.dumps(self.extra_store or {})))
        self.db = FakeDB(self.store)
        self.live_payload = self.live or {
            "ok": True, "status_note": "All checks passing.", "online_players": 3,
            "active_games": 2, "open_lobbies": 1, "matchmaking": 0, "stuck_rooms": 0,
            "load": {"rooms": 5, "threads": 20, "deep_plan_slots": 2,
                     "deep_plan_granted": 100, "deep_plan_skipped": 3},
        }
        self.wire()

    def wire(self, **over):
        args = dict(
            get_firestore=lambda: self.db,
            verify_token=self.verify,
            games_history_dir=self.games_dir,
            live_snapshot=lambda: self.live_payload,
            app_version="1.6.50 (test)",
            card_species=CARD_SPECIES,
            clan_season=(lambda: self.clan_season) if self.clan_season else None,
        )
        args.update(over)
        an.init(**args)
        an.reset_caches()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        an.reset_caches()

    @staticmethod
    def verify(token):
        return {
            "admin-token": {"uid": ADMIN_UID, "email": ADMIN_EMAIL},
            "player-token": {"uid": PLAYER_UID, "email": "someone@example.com"},
            "flagged-admin-token": {"uid": "uid-flagged"},
        }.get(token)

    def call(self, section, **body):
        h = Handler()
        body.setdefault("idToken", "admin-token")
        handled = an.handle_post(h, Parsed("/api/analytics/" + section), body)
        self.assertTrue(handled, "handle_post should claim /api/analytics/*")
        return h

    def get(self, section, **body):
        h = self.call(section, **body)
        self.assertEqual(h.status, 200, h.payload)
        self.assertTrue(h.payload.get("ok"), h.payload)
        return h.payload


# ══════════════════════════════════════════════════════════════════════════
#  THE BUG THIS MODULE WAS REWRITTEN FOR
# ══════════════════════════════════════════════════════════════════════════
class TestTheAccountsAreTheSource(AnalyticsTestCase):
    """On live, the server's history directory held ONE game while the accounts
    held 435, and every games page was built from the directory: Gameplay,
    Cards, Competitive and Player Search were empty. Here the directory holds
    one unrelated game, and every page has to show the accounts' games anyway."""
    disk = [disk_game(NOW - DAY, names=("Somebody", "Else"))]

    def test_gameplay_counts_the_games_on_the_accounts(self):
        # 30 days: Reef's three recent games plus Sprat's copy of their game.
        self.assertEqual(cards(self.get("gameplay", days=30))["Games played"], 4)

    def test_lifetime_is_the_accounts_own_counters(self):
        # Reef 4 + Kelp 1 + Tide 1 + Sprat 1: Tide's one game is in no log at all.
        self.assertEqual(cards(self.get("overview", days=0))["Games played"], 7)

    def test_the_cards_page_has_boards_to_measure(self):
        self.assertEqual(cards(self.get("cards", days=30))["Boards measured"], 4)

    def test_player_search_shows_the_players_own_history(self):
        p = self.get("search", query="Reef")["player"]
        self.assertEqual(len(p["recent"]), 4, "every game in Reef's own log")
        self.assertEqual(p["recent"][0]["result"], "Won", "newest first, as the History tab shows it")

    def test_an_empty_history_directory_changes_nothing(self):
        before = cards(self.get("gameplay", days=30))
        for name in os.listdir(self.games_dir):
            os.remove(os.path.join(self.games_dir, name))
        an.reset_caches()
        self.assertEqual(cards(self.get("gameplay", days=30)), before)


# ══════════════════════════════════════════════════════════════════════════
#  1, THE ADMIN GATE
# ══════════════════════════════════════════════════════════════════════════
class TestAdminGate(AnalyticsTestCase):
    def test_admin_email_is_allowed(self):
        h = self.call("overview")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.payload["ok"])

    def test_is_admin_flag_is_allowed_without_the_known_email(self):
        # The gate is not hard-coded to one address: an account FLAGGED admin in
        # Firestore gets in, which is what makes a second dev account possible.
        self.store["users"]["uid-flagged"] = {"nickname": "Dev2", "is_admin": True,
                                              "email": "other@example.com"}
        self.assertEqual(self.call("overview", idToken="flagged-admin-token").status, 200)

    def test_ordinary_player_is_refused(self):
        self.store["users"][PLAYER_UID] = {"nickname": "Someone", "is_admin": False}
        h = self.call("overview", idToken="player-token")
        self.assertEqual(h.status, 403)
        self.assertFalse(h.payload["ok"])

    def test_missing_and_bogus_tokens_are_refused(self):
        for token in ("", "not-a-token", None):
            self.assertEqual(self.call("overview", idToken=token).status, 403,
                             f"token {token!r} must be refused")

    def test_a_forged_uid_in_the_body_is_ignored(self):
        # The uid is never read from the body, only from the verified token.
        h = self.call("overview", idToken="player-token", uid=ADMIN_UID,
                      is_admin=True, email=ADMIN_EMAIL)
        self.assertEqual(h.status, 403)

    def test_every_refusal_gives_the_same_answer(self):
        # A probe must not be able to tell "wrong account" from "bad token".
        answers = set()
        self.store["users"][PLAYER_UID] = {"nickname": "Someone"}
        for token in ("", "bogus", "player-token"):
            h = self.call("overview", idToken=token)
            answers.add((h.status, json.dumps(h.payload, sort_keys=True)))
        self.assertEqual(len(answers), 1, f"refusals differ and leak information: {answers}")

    def test_the_live_tick_is_behind_the_gate_too(self):
        self.assertEqual(self.call("live", idToken="bogus").status, 403)

    def test_non_analytics_paths_are_not_claimed(self):
        h = Handler()
        self.assertFalse(an.handle_post(h, Parsed("/api/clan/home"), {}))
        self.assertIsNone(h.payload)

    def test_unknown_section_is_404_not_a_crash(self):
        self.assertEqual(self.call("not-a-section").status, 404)

    def test_every_named_section_answers_for_both_ranges(self):
        for days in (30, 0):
            for name in list(an._SECTIONS) + ["search", "export", "live"]:
                h = self.call(name, query="Reef", days=days)
                self.assertEqual(h.status, 200, f"{name} ({days}d) should answer 200")
                self.assertTrue(h.payload["ok"], f"{name} ({days}d) returned ok=False: {h.payload}")


# ══════════════════════════════════════════════════════════════════════════
#  2, "NO DATA" IS NOT ZERO
# ══════════════════════════════════════════════════════════════════════════
class TestEmptyIsNotZero(AnalyticsTestCase):
    users = {}

    def test_rates_with_an_empty_denominator_are_none(self):
        self.assertIsNone(an._pct(0, 0))
        self.assertIsNone(an._pct(5, 0))
        self.assertEqual(an._pct(1, 4), 25.0)

    def test_average_of_nothing_is_none(self):
        self.assertIsNone(an._mean([]))
        self.assertEqual(an._mean([2, 4]), 3)

    def test_overview_on_an_empty_game_reports_no_data_not_zeros(self):
        for days in (30, 0):
            c = cards(self.get("overview", days=days))
            for label in ("Came back after 7 days", "Most played strategy", "Most played table"):
                self.assertIsNone(c[label], f"{label} must be None (No data yet) on empty data")
            # Counts, on the other hand, ARE genuinely zero and must say so.
            self.assertEqual(c["Games played"], 0)
        self.assertEqual(cards(self.get("overview", days=30))["New players"], 0)

    def test_a_rate_with_no_games_under_it_is_none(self):
        self.assertIsNone(cards(self.get("gameplay", days=30))["Played with other people"])
        self.assertIsNone(cards(self.get("gameplay", days=30))["Average score"])

    def test_no_baseline_means_no_delta(self):
        # "+100%" against a zero previous period is noise; the card shows nothing.
        self.assertIsNone(an._delta(5, 0))
        self.assertIsNone(an._delta(5, None))
        self.assertEqual(an._delta(150, 100), 50.0)

    def test_without_firestore_the_dashboard_still_opens_and_says_why(self):
        # The admin email in a VERIFIED token is proof on its own, so the
        # dashboard still opens when Firebase is down, the account numbers go
        # to "no data" and an alert says why, rather than the tool going dark.
        self.wire(get_firestore=lambda: None)
        d = self.get("overview")
        self.assertIn("Player database not connected", [a["title"] for a in d["alerts"]])
        self.assertFalse(d["data_status"]["ok"])

    def test_without_firestore_a_flag_only_admin_cannot_be_confirmed(self):
        # An account whose admin-ness lives ONLY in Firestore can't be verified
        # with Firestore down, so it is refused rather than assumed.
        self.wire(get_firestore=lambda: None)
        self.assertEqual(self.call("overview", idToken="flagged-admin-token").status, 403)


class TestEmptyHistoryIsNotAFault(AnalyticsTestCase):
    """A server that has recorded no games on its own disk is not a broken server.

    The Technical tab used to fail the "Game records" check on an empty history
    directory, which dragged the Server card to "Needs attention", so a freshly
    deployed box opened permanently red with nothing actually wrong. A directory
    that is MISSING or unwritable is the real fault, and that one still has to
    be loud, and has to name the path so it can be fixed.
    """
    disk = []

    def _records_check(self):
        return {c["label"]: c for c in self.get("technical")["checks"]}["Game records"]

    def _server_card(self):
        return cards(self.get("technical"))["Server"]

    def test_an_empty_but_writable_directory_passes(self):
        check = self._records_check()
        self.assertTrue(check["ok"], "an empty history directory is not a fault")
        self.assertIn("No games recorded yet", check["detail"])
        self.assertIn(self.games_dir, check["detail"])
        # And it says where the player numbers come from instead: every account,
        # the dev's and the guest's included (1 + 4 + 1 + 1 + 1 + 1).
        self.assertIn("accounts, which hold 9 games", check["detail"])

    def test_an_empty_directory_leaves_the_server_healthy(self):
        self.assertEqual(self._server_card(), "Healthy")

    def test_a_missing_directory_is_a_fault_and_names_the_path(self):
        missing = os.path.join(self.tmp, "gone")
        self.wire(games_history_dir=missing)
        check = self._records_check()
        self.assertFalse(check["ok"], "a missing history directory IS a fault")
        self.assertIn(missing, check["detail"])
        self.assertEqual(self._server_card(), "Needs attention")

    def test_an_unconfigured_directory_is_a_fault(self):
        self.wire(games_history_dir="")
        check = self._records_check()
        self.assertFalse(check["ok"])
        self.assertIn("No history directory is configured", check["detail"])

    def test_an_unwritable_directory_is_a_fault_even_though_it_reads_fine(self):
        locked = os.path.join(self.tmp, "locked")
        os.makedirs(locked)
        os.chmod(locked, 0o500)
        try:
            self.wire(games_history_dir=locked)
            check = self._records_check()
            if os.access(locked, os.W_OK):
                self.skipTest("running as a user that ignores the mode bits")
            self.assertFalse(check["ok"])
            self.assertIn("not writable", check["detail"])
        finally:
            os.chmod(locked, 0o700)


class TestHistoryPresentStillCounts(AnalyticsTestCase):
    """The happy path has to keep saying the number, not just "fine"."""
    disk = [disk_game(NOW - 2 * DAY), disk_game(NOW - 3 * DAY, completed=False)]

    def test_a_directory_with_games_reports_the_count(self):
        check = {c["label"]: c for c in self.get("technical")["checks"]}["Game records"]
        self.assertTrue(check["ok"])
        self.assertIn("2 games on disk", check["detail"])

    def test_games_that_ended_badly_come_from_the_server_records(self):
        self.assertEqual(cards(self.get("technical", days=30))["Games that ended badly"], 1)


# ══════════════════════════════════════════════════════════════════════════
#  3, THE METRIC MATHS
# ══════════════════════════════════════════════════════════════════════════
class TestRetention(AnalyticsTestCase):
    def rows(self):
        return an._filter_users(an._load_users(force=True), {})

    def test_cohort_excludes_players_too_new_to_have_returned(self):
        # Sprat joined 2 days ago. At day 7 it cannot be in the cohort, and
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
        self.assertEqual(an._retention(self.rows(), 1, NOW)["returned"], 3,
                         "Reef, Kelp and Sprat all came back after a day")

    def test_no_eligible_cohort_is_none_not_zero_percent(self):
        self.assertIsNone(an._retention([{"created_at": NOW - 3600, "last_active": NOW}], 7, NOW))


class TestDateRanges(AnalyticsTestCase):
    def test_the_range_decides_which_games_count(self):
        # Reef 1d/3d/5d + Sprat's copy at 1d; Kelp's game with Reef was 39d ago.
        self.assertEqual(cards(self.get("gameplay", days=2))["Games played"], 2)
        self.assertEqual(cards(self.get("gameplay", days=7))["Games played"], 4)
        self.assertEqual(cards(self.get("gameplay", days=30))["Games played"], 4)
        self.assertEqual(cards(self.get("gameplay", days=90))["Games played"], 6)

    def test_the_comparison_is_the_period_just_before(self):
        # The 30 days before: the game with Kelp, logged by Reef and by Kelp.
        c = {x["label"]: x for x in self.get("overview", days=30)["cards"]}
        self.assertEqual(c["Games played"]["delta"], 100.0, "4 against 2")
        self.assertEqual(c["Played a game"]["delta"], 0.0, "Reef + Sprat against Reef + Kelp")

    def test_players_on_the_game_is_not_players_who_played(self):
        c = cards(self.get("overview", days=30))
        # On the game: Reef, Sprat, and Tide, who opened it 20 days ago without
        # finishing anything. Kelp was last on 38 days ago.
        self.assertEqual(c["Players on the game"], 3)
        self.assertEqual(c["Played a game"], 2, "only Reef and Sprat finished a game")

    def test_a_date_counts_in_exactly_one_of_two_back_to_back_ranges(self):
        users = [{"uid": "a", "play_days": [an._date_noon(day_of(NOW - 30 * DAY))], "log": [],
                  "games": 1}]
        r = an._range({"days": 30})
        f = {"mode": "all"}
        now_hit = an._played_uids(users, f, r["start"], r["end"])
        prev_hit = an._played_uids(users, f, *r["prev"])
        self.assertEqual(len(now_hit) + len(prev_hit), 1, "a day on the boundary is counted once")

    def test_every_series_lines_up_with_its_day_labels(self):
        d = self.get("gameplay", days=30)
        vol = d["volume"]
        for key in ("games", "multiplayer", "players"):
            self.assertEqual(len(vol[key]), len(vol["days"]), key)
        self.assertEqual(sum(vol["games"]), 4)
        self.assertEqual(vol["gran"], "day")

    def test_a_day_is_the_viewers_day(self):
        """Cut at UTC midnight, "today" began at 7pm for a developer in Texas,
        and every chart ended in a cliff that was only the evening."""
        base = calendar.timegm(time.strptime(day_of(NOW - 3 * DAY), "%Y-%m-%d"))
        stamp = base + 2 * 3600                   # 02:00 UTC: 9pm the evening before, in Texas
        utc = an._axis(stamp - 5 * DAY, stamp)
        texas = an._axis(stamp - 5 * DAY, stamp, 5 * 3600)
        self.assertEqual(utc["days"][-1], day_of(base))
        self.assertEqual(texas["days"][-1], day_of(base - DAY))
        self.assertEqual(an._series(texas, [stamp])[-1], 1)
        self.assertEqual(texas["from"], calendar.timegm(time.strptime(texas["days"][0], "%Y-%m-%d")) + 5 * 3600)

    def test_the_browsers_offset_reaches_every_chart(self):
        self.assertEqual(an._filters({"tz": 300})["tz"], 300 * 60)
        self.assertEqual(an._filters({"tz": 99999})["tz"], 14 * 3600, "no timezone is further than 14 hours")
        self.assertEqual(an._filters({})["tz"], 0)
        d = self.get("gameplay", days=30, tz=300)
        self.assertEqual(len(d["volume"]["games"]), len(d["volume"]["days"]))
        self.assertEqual(sum(d["volume"]["games"]), 4, "a shifted day moves games between days, never loses one")

    def test_an_older_log_entry_still_reads(self):
        """The History tab draws an entry with new Date(t) and an `opp` that may
        be one string, so entries that old are still on accounts."""
        entries = an._log_entries([{
            "r": 1, "s": "88", "t": "2026-05-04T18:30:00Z", "opp": "Kelp, Sun", "pc": 3,
            "all": [{"n": "Kelp", "s": 70}, {"n": "Reef", "s": 88}, {"n": "Sun", "s": 40}],
            "bds": [{"n": "Reef", "b": [{"o": "Pier", "a": ["Bonito"]}]}], "win": "Reef",
        }], "Reef")
        self.assertEqual(len(entries), 1, "a date-string timestamp is still a game")
        e = entries[0]
        self.assertEqual(e["t"], calendar.timegm((2026, 5, 4, 18, 30, 0)))
        self.assertEqual(e["me"], "Reef", "a one-string opponent list is still names, not letters")
        self.assertEqual(e["board"], [{"o": "Pier", "a": ["Bonito"]}])
        self.assertEqual((e["score"], e["won"], e["mode"]), (88, True, "casual"))

    def test_the_log_timestamps_are_milliseconds(self):
        # Date.now() is milliseconds. Read as seconds, every game is in the year
        # 58000 and no range would ever contain it.
        row = next(u for u in an._load_users() if u["nickname"] == "Reef")
        self.assertTrue(all(NOW - 60 * DAY < e["t"] <= NOW + DAY for e in row["log"]))


class TestLifetime(AnalyticsTestCase):
    def test_days_zero_is_lifetime(self):
        self.assertEqual(an._filters({"days": 0})["days"], an.LIFETIME)
        self.assertEqual(an._filters({"days": "lifetime"})["days"], an.LIFETIME)
        d = self.get("overview", days=0)
        self.assertTrue(d["lifetime"])
        self.assertEqual(d["range_days"], 0)

    def test_lifetime_has_nothing_to_compare_against(self):
        d = self.get("overview", days=0, compare=True)
        self.assertTrue(all(c["delta"] is None for c in d["cards"]))
        self.assertIsNone(d["growth"]["compare"])

    def test_totals_come_from_the_counters_and_match_the_homepage(self):
        c = cards(self.get("overview", days=0))
        self.assertEqual(c["Players"], 4)
        self.assertEqual(c["Played a game"], 4, "Tide's game predates every log, still a game")
        self.assertEqual(c["Games played"], 7)
        # 12.5 + 1.25, floored exactly as the homepage floors it.
        self.assertEqual(c["Hours played"], 13)

    def test_lifetime_strategies_are_the_ones_players_confirmed(self):
        d = self.get("gameplay", days=0)
        self.assertEqual(d["strategy_source"], "confirmed")
        self.assertEqual(d["strategies"][:2], [{"label": "King Salmon", "value": 3},
                                               {"label": "Bait Ball", "value": 1}])
        # A date range can only use what the game detected on each board.
        ranged = self.get("gameplay", days=30)
        self.assertEqual(ranged["strategy_source"], "detected")
        self.assertEqual(ranged["strategies"], [{"label": "King Salmon", "value": 4}])

    def test_lifetime_table_sizes_are_the_counters(self):
        self.assertEqual(self.get("gameplay", days=0)["sizes"], [{"label": "2 players", "value": 6}])

    def test_lifetime_players_cards_cover_the_whole_life_of_the_game(self):
        c = cards(self.get("players", days=0))
        self.assertNotIn("Played in the last 30 days", c, "Lifetime must not show a 30-day number")
        self.assertEqual(c["Games played"], 7, "the same all-time total as the Overview")
        self.assertFalse(any("30 days" in label or "last" in label.lower() for label in c))

    def test_a_long_lifetime_is_charted_by_week_then_by_month(self):
        weeks = an._axis(NOW - 400 * DAY, NOW)
        self.assertEqual(weeks["gran"], "week")
        self.assertTrue(all(time.strptime(k, "%Y-%m-%d").tm_wday == 0 for k in weeks["days"]),
                        "a week is keyed by its Monday")
        months = an._axis(NOW - 900 * DAY, NOW)
        self.assertEqual(months["gran"], "month")
        self.assertTrue(all(k.endswith("-01") for k in months["days"]))
        self.assertLess(len(months["days"]), 40)

    def test_a_weekly_bucket_counts_a_player_once(self):
        axis = an._axis(NOW - 400 * DAY, NOW)
        monday = an._bucket_start(NOW, "week")
        pairs = [(monday + DAY, "a"), (monday + 2 * DAY, "a"), (monday + DAY, "b")]
        self.assertEqual(an._distinct_series(axis, pairs)[-1], 2)


class TestAnAccountBusierThanItsLog(AnalyticsTestCase):
    """The log keeps 50 games. A player who played 50 in the last three days
    and more before that is under-counted in any range reaching further back,
    and the payload has to say so instead of passing the short count off."""
    BUSY = [table(NOW - DAY - i * 3600, (("Whale", 60), ("Bot A", 10))) for i in range(50)]
    users = {"uid-whale": account(
        "Whale", BUSY, created=NOW - 100 * DAY, seen=NOW - 60, older=150,
        stats={"streak_days": sorted({day_of(NOW - d * DAY) for d in range(1, 21)})})}

    def test_a_range_the_log_cannot_reach_is_flagged(self):
        d = self.get("gameplay", days=30)
        self.assertEqual(d["coverage"]["short_accounts"], 1)
        self.assertIn("aren't counted", d["coverage"]["note"])
        self.assertEqual(cards(d)["Games played"], 50, "what the log holds, and no more")

    def test_an_incomplete_count_gets_no_percentage_change(self):
        c = {x["label"]: x for x in self.get("overview", days=30)["cards"]}
        self.assertIsNone(c["Games played"]["delta"])

    def test_a_range_the_log_does_reach_is_not_flagged(self):
        self.assertIsNone(self.get("gameplay", days=2)["coverage"])

    def test_lifetime_totals_still_count_every_game(self):
        d = self.get("gameplay", days=0)
        self.assertEqual(cards(d)["Games played"], 200)
        self.assertIn("Totals still count every game", d["coverage"]["note"])


class TestWhoWasAtTheTable(AnalyticsTestCase):
    """Each player at a table logs the game on their own account, seconds apart.
    "Played with other people" depends on recognising those copies as one game."""

    def humans(self, name):
        row = next(u for u in an._load_users() if u["nickname"] == name)
        return {e["t"] // 60: e["humans"] for e in row["log"]}

    def test_two_accounts_logging_one_game_are_two_people(self):
        self.assertEqual(self.humans("Sprat")[(G_SPRAT["when"]) // 60], 2)

    def test_a_game_against_bots_is_one_person(self):
        got = self.humans("Reef")
        self.assertEqual(got[G_BOTS["when"] // 60], 1)

    def test_the_filtered_out_dev_account_still_sat_at_the_table(self):
        self.assertEqual(self.humans("Reef")[G_DEV["when"] // 60], 2)

    def test_played_with_other_people_is_a_share_of_games(self):
        # Of 4 games in 30 days only Reef's bot game was solo.
        self.assertEqual(cards(self.get("gameplay", days=30))["Played with other people"], 75.0)
        self.assertEqual(cards(self.get("gameplay", days=30, only_multiplayer=True))["Games played"], 3)

    def test_a_rematch_with_the_same_scores_is_a_second_game(self):
        first = table(NOW - 2 * DAY, (("Reef", 50), ("Sprat", 40)))
        again = table(NOW - 2 * DAY + 600, (("Reef", 50), ("Sprat", 40)))
        self.store["users"] = {
            "a": account("Reef", [first, again], created=NOW - 9 * DAY, seen=NOW),
            "b": account("Sprat", [first, again], created=NOW - 9 * DAY, seen=NOW),
        }
        an.reset_caches()
        rows = an._load_users()
        self.assertTrue(all(e["humans"] == 2 for u in rows for e in u["log"]),
                        "two games of two people, not one game of four")


class TestPlayerList(AnalyticsTestCase):
    """"I want to see all the players": the table lists every account, not a
    top 25, and says who was on the game in the range."""
    # Seen 1 to 40 days ago, each an hour short of a whole day, so no account
    # sits exactly on the 30-day line (where the second the test runs in decides).
    users = dict(make_users(), **{
        f"uid-extra-{i}": account(f"Extra{i:02d}", created=NOW - 50 * DAY,
                                  seen=NOW - (i + 1) * DAY + 3600, stats={"completed_games": i})
        for i in range(40)})

    def table(self, **body):
        return self.get("players", **body)["table"]

    def test_every_account_is_listed(self):
        # 40 extras + Reef, Kelp, Tide, Sprat (the dev and the guest are filtered).
        self.assertEqual(len(self.table(days=30)["rows"]), 44)
        self.assertEqual(len(self.table(days=0)["rows"]), 44)

    def test_the_rows_say_who_was_on_the_game_in_the_range(self):
        t = self.table(days=30)
        # The 30 extras seen inside 30 days, plus Reef, Sprat and Tide.
        self.assertEqual(t["in_range_count"], 33)
        self.assertEqual(len([r for r in t["rows"] if r["in_range"]]), 33)
        self.assertTrue(all(r["in_range"] for r in self.table(days=0)["rows"]))

    def test_a_row_says_what_that_player_plays(self):
        reef = next(r for r in self.table(days=30)["rows"] if r["name"] == "Reef")
        self.assertEqual((reef["games"], reef["games_range"], reef["days_range"]), (4, 3, 3))
        self.assertEqual(reef["strategy"], "King Salmon")
        self.assertEqual(reef["table"], "2 players")
        self.assertEqual(reef["hours"], 12.5)

    def test_range_columns_only_exist_for_a_range(self):
        keys = lambda t: [c["key"] for c in t["columns"]]
        self.assertIn("games_range", keys(self.table(days=30)))
        self.assertNotIn("games_range", keys(self.table(days=0)))

    def test_the_most_recently_seen_come_first_in_a_range(self):
        self.assertEqual([r["name"] for r in self.table(days=30)["rows"][:2]], ["Sprat", "Reef"])


class TestWinsAreNotDoubleCounted(AnalyticsTestCase):
    """`normal_wins` and `competitive_wins` are not addable: the first is a
    lifetime total that ALREADY counts free-for-all Competitive wins, and the
    second is wiped at every season rollover."""
    users = {"u-comp": account("Reef", created=NOW - 90 * DAY, seen=NOW - 3600,
                               stats={"completed_games": 20, "normal_wins": 8,
                                      "competitive_wins": 3, "total_xp": 900, "level": 10})}

    def row(self):
        return next(r for r in self.get("players", days=365)["table"]["rows"] if r["name"] == "Reef")

    def test_wins_is_the_lifetime_counter_not_a_sum(self):
        self.assertEqual(self.row()["wins"], 8, "8 + 3 = 11 would count every free-for-all win twice")

    def test_season_competitive_wins_are_reported_separately(self):
        self.assertEqual(self.row()["comp_wins"], 3)

    def test_the_season_counter_is_labelled_as_a_season_counter(self):
        """It drops to zero every season, so the column has to say so."""
        cols = {c["key"]: c["label"] for c in self.get("players", days=365)["table"]["columns"]}
        self.assertIn("season", cols["comp_wins"].lower())


class TestOverviewSurvivesALiveFailure(AnalyticsTestCase):
    """Overview is the page the dashboard opens on, and the live snapshot is the
    only thing it reads that the other pages do not. If that call can throw, the
    landing page fails alone and the whole tool reads as broken."""

    def test_a_throwing_live_snapshot_still_renders_the_overview(self):
        def boom():
            raise RuntimeError("room lock is wedged")
        an._live_snapshot = boom
        d = self.get("overview", days=30)
        self.assertTrue(d.get("cards"), "the overview must still build")
        self.assertEqual(cards(d)["Server"], "Needs attention",
                         "a snapshot that failed is not a healthy server")

    def test_a_live_snapshot_that_is_not_a_dict_is_ignored(self):
        an._live_snapshot = lambda: None
        self.assertTrue(self.get("overview", days=30).get("cards"))


# The two competitive tables, plus a casual game for the filter to exclude.
C_FFA_1 = table(NOW - DAY, (("Reef", 50), ("Kelp", 40), ("Sun", 30)), mode="ranked")
C_FFA_2 = table(NOW - 2 * DAY, (("Reef", 30), ("Kelp", 50), ("Sun", 40)), mode="ranked")
C_PAIR = table(NOW - 3 * DAY, (("Reef", 60), ("Kelp", 55)), mode="competitive")
C_CASUAL = table(NOW - DAY - 7200, (("Reef", 50), ("Kelp", 40)))
C_GAMES = [C_FFA_1, C_FFA_2, C_PAIR, C_CASUAL]


class TestCompetitiveIsTwoModes(AnalyticsTestCase):
    """Competitive Mode is the paired 4-seat game AND the free-for-all, and
    both are "Competitive" to the person who played them."""
    users = {
        "u-reef": account("Reef", C_GAMES, created=NOW - 60 * DAY, seen=NOW - 600,
                          stats={"lifetime_comp_wins": 2, "lifetime_comp_losses": 1, "comp_cp": 120,
                                 "rank_competitive": "Bronze Barracuda II"}),
        "u-kelp": account("Kelp", C_GAMES, created=NOW - 60 * DAY, seen=NOW - 900,
                          stats={"lifetime_comp_wins": 1, "lifetime_comp_losses": 2, "comp_cp": 80}),
        "u-sun": account("Sun", C_GAMES, created=NOW - 60 * DAY, seen=NOW - 1200,
                         stats={"lifetime_comp_losses": 2}),
    }

    def test_both_tables_are_counted_once_per_player(self):
        c = cards(self.get("competitive", days=30))
        self.assertEqual(c["Competitive games"], 8, "two free-for-alls of three, one pair of two")
        self.assertEqual(c["Free-for-all games"], 6)
        self.assertEqual(c["Players who played competitive"], 3)

    def test_the_results_are_the_players_own(self):
        d = self.get("competitive", days=30)
        self.assertEqual(d["outcomes"], [{"label": "Won", "value": 3},
                                         {"label": "Didn't win", "value": 5}])
        reef = next(r for r in d["table"]["rows"] if r["name"] == "Reef")
        self.assertEqual((reef["games"], reef["wins"], reef["win_rate"]), (3, 2, 66.7))

    def test_lifetime_uses_the_win_loss_counters(self):
        d = self.get("competitive", days=0)
        self.assertEqual(cards(d)["Competitive games"], 8)
        self.assertEqual(d["outcomes"], [{"label": "Wins", "value": 3},
                                         {"label": "Losses", "value": 5},
                                         {"label": "Draws", "value": 0}])

    def test_the_season_leader_is_named(self):
        d = self.get("competitive", days=30)
        c = {x["label"]: x for x in d["cards"]}
        self.assertEqual(c["Top Ocean Points"]["value"], 120)
        self.assertIn("Reef", c["Top Ocean Points"]["hint"])
        self.assertEqual(d["top"], [{"label": "Reef", "value": 120}, {"label": "Kelp", "value": 80}])

    def test_the_page_ignores_a_casual_mode_filter_but_not_table_size(self):
        self.assertEqual(cards(self.get("competitive", days=30, mode="casual"))["Competitive games"], 8)
        self.assertEqual(cards(self.get("competitive", days=30, player_count=3))["Competitive games"], 6)

    def test_the_mode_filter_means_the_mode_a_player_played(self):
        """Filtering Gameplay to Competitive has to return BOTH competitive tables."""
        self.assertEqual(cards(self.get("gameplay", days=30, mode="competitive"))["Games played"], 8)
        self.assertEqual(cards(self.get("gameplay", days=30, mode="casual"))["Games played"], 2)

    def test_the_gameplay_mix_names_the_two_competitive_modes_apart(self):
        labels = [m["label"] for m in self.get("gameplay", days=30)["modes"]]
        self.assertIn("Competitive (free-for-all)", labels)
        self.assertIn("Competitive (pairs)", labels)
        self.assertNotIn("Competitive", labels, "a bare 'Competitive' doesn't say which table it was")


F_TEAM = table(NOW - DAY, (("Reef", 50), ("Kelp", 40), ("Sun", 45), ("Tide", 30)), mode="team")
F_TABLE_GAME = table(NOW - 2 * DAY, (("Reef", 70), ("Kelp", 60)), mode="physical")
F_CASUAL = table(NOW - DAY - 600, (("Reef", 50), ("Kelp", 40)))
F_SOLO = table(NOW - DAY - 1200, (("Reef", 50), ("Bot", 20), ("Bot 2", 10)))
F_GAMES = [F_TEAM, F_TABLE_GAME, F_CASUAL, F_SOLO]


class TestFilters(AnalyticsTestCase):
    users = dict(make_users(), **{
        "u-reef": account("Reef", F_GAMES, created=NOW - 30 * DAY, seen=NOW),
        "u-kelp": account("Kelp", F_GAMES, created=NOW - 30 * DAY, seen=NOW),
    })
    # The base Reef, Kelp and Sprat played other games; only the dev and the
    # guest stay, for the two account-filter tests.
    for uid in ("uid-veteran", "uid-lapsed", "uid-fresh"):
        users.pop(uid)

    def games(self, **body):
        return cards(self.get("gameplay", days=30, **body))["Games played"]

    def test_every_game_counts_by_default(self):
        # Team 2 + table game 2 + casual 2 + Reef's solo game against bots.
        self.assertEqual(self.games(), 7)

    def test_mode_filter(self):
        self.assertEqual(self.games(mode="team"), 2)
        self.assertEqual(self.games(mode="physical"), 2)
        self.assertEqual(self.games(mode="casual"), 3)

    def test_player_count_filter(self):
        self.assertEqual(self.games(player_count=2), 4)
        self.assertEqual(self.games(player_count=3), 1)

    def test_only_games_with_other_people(self):
        self.assertEqual(self.games(only_multiplayer=True), 6)

    def test_a_game_filter_makes_lifetime_count_from_the_log(self):
        self.assertEqual(cards(self.get("gameplay", days=0, mode="team"))["Games played"], 2)

    def test_the_dev_account_is_excluded_from_player_numbers_by_default(self):
        off = cards(self.get("players", days=365))["Total accounts"]
        on = cards(self.get("players", days=365, include_test=True))["Total accounts"]
        self.assertEqual(on, off + 1, "the admin account should only appear with test accounts on")

    def test_guests_are_excluded_by_default(self):
        off = cards(self.get("players", days=365))["Total accounts"]
        on = cards(self.get("players", days=365, include_guests=True))["Total accounts"]
        self.assertEqual(on, off + 1)

    def test_filters_are_clamped_to_something_sane(self):
        self.assertEqual(an._filters({"days": 99999})["days"], an.MAX_RANGE_DAYS)
        self.assertEqual(an._filters({"days": "nonsense"})["days"], an.DEFAULT_RANGE_DAYS)
        self.assertEqual(an._filters({"days": -5})["days"], an.DEFAULT_RANGE_DAYS)
        self.assertEqual(an._filters({})["days"], an.DEFAULT_RANGE_DAYS)
        self.assertEqual(an._filters({"mode": "bogus"})["mode"], "all")
        self.assertEqual(an._filters({"player_count": 99})["player_count"], 0)

    def test_server_records_still_know_their_table(self):
        """`mode` collapses to "truncated" when nobody finishes, so the mode has
        to survive in its own field or an abandoned ranked match reads as casual."""
        self.assertEqual(an._game_mode(disk_game(NOW, ranked=True)), "ranked")
        self.assertEqual(an._game_mode(disk_game(NOW, competitive=True)), "competitive")
        self.assertEqual(an._game_mode(disk_game(NOW, team=True)), "team")
        self.assertEqual(an._game_mode(disk_game(NOW)), "casual")
        self.assertEqual(an._game_mode(disk_game(NOW, ranked=True, completed=False)), "ranked")


# Blue Tang sits on BOTH boards, so it wins exactly half the time. Mandarin
# Goby only ever sits on the LOSER's board, so its win rate is 0%.
TANG = [("Pacific Ocean", ("Blue Tang",))]
TANG_AND_GOBY = [("Pacific Ocean", ("Blue Tang", "Mandarin Goby"))]
BALANCE_GAMES = [table(NOW - DAY - i * 3600, (("Reef", 50), ("Kelp", 40)),
                       boards={"Reef": TANG, "Kelp": TANG_AND_GOBY}) for i in range(12)]


class TestCardBalance(AnalyticsTestCase):
    users = {
        "u-reef": account("Reef", BALANCE_GAMES, created=NOW - 30 * DAY, seen=NOW),
        "u-kelp": account("Kelp", BALANCE_GAMES, created=NOW - 30 * DAY, seen=NOW),
    }

    def test_each_player_board_is_measured_once(self):
        # 12 games, two signed-in players: 24 boards, never 48 from both copies.
        self.assertEqual(cards(self.get("cards", days=30, min_sample=1))["Boards measured"], 24)

    def test_win_rate_is_per_board_not_per_copy(self):
        # A board holding three copies is still ONE win. Counting copies would
        # let a stackable animal inflate its own rate above 100%.
        stack = table(NOW - 3 * DAY, (("Reef", 50), ("Kelp", 40)),
                      boards={"Reef": [("Pacific Ocean", ("Blue Tang", "Blue Tang", "Blue Tang"))]})
        self.store["users"]["u-reef"] = account("Reef", BALANCE_GAMES + [stack],
                                                created=NOW - 30 * DAY, seen=NOW)
        an.reset_caches()
        row = next(r for r in self.get("cards", days=30, min_sample=1)["table"]["rows"]
                   if r["name"] == "Blue Tang")
        self.assertLessEqual(row["win_rate"], 100.0)
        self.assertGreater(row["played"], row["boards"], "played counts copies, boards counts boards")

    def test_small_samples_are_never_flagged_for_balance(self):
        d = self.get("cards", days=30, min_sample=1000)
        self.assertEqual(d["review"], [], "nothing should be flagged when nothing meets the sample")
        self.assertIsNone(cards(d)["Typical win rate"])
        self.assertIsNone(cards(d)["Worth a balance look"])

    def test_a_lopsided_card_is_flagged_once_the_sample_is_met(self):
        d = self.get("cards", days=30, min_sample=4)
        goby = next(r for r in d["review"] if r["name"] == "Mandarin Goby")
        self.assertEqual(goby["direction"], "weak")
        self.assertEqual(goby["win_rate"], 0.0)

    def test_min_sample_is_reported_back_so_the_ui_can_say_it(self):
        self.assertEqual(self.get("cards", min_sample=7)["min_sample"], 7)

    def test_families_and_oceans_are_counted(self):
        d = self.get("cards", days=30)
        self.assertEqual(d["species"], [{"label": "Reef Fish", "value": 24},
                                        {"label": "Baitfish", "value": 12}])
        self.assertEqual(d["oceans"], [{"label": "Pacific Ocean", "value": 24}])

    def test_an_animal_the_card_database_does_not_know_gets_no_family(self):
        self.wire(card_species={})
        self.assertEqual(self.get("cards", days=30)["species"], [])


class TestAlerts(AnalyticsTestCase):
    # Server records: well over the minimum, and two thirds of them abandoned.
    disk = ([disk_game(NOW - DAY, completed=False) for _ in range(20)]
            + [disk_game(NOW - DAY, completed=True) for _ in range(10)])

    def test_a_real_completion_problem_raises_one_alert(self):
        titles = [a["title"] for a in self.get("overview", days=30)["alerts"]]
        self.assertIn("Players are leaving games early", titles)

    def test_overview_never_shows_more_than_three_alerts(self):
        self.live = {"ok": False, "status_note": "Down", "online_players": 0,
                     "active_games": 0, "stuck_rooms": 9, "load": {}}
        self.setUp()
        self.assertLessEqual(len(self.get("overview", days=30)["alerts"]), 3)

    def test_technical_health_carries_the_full_list(self):
        self.live = {"ok": False, "status_note": "Down", "online_players": 0,
                     "active_games": 0, "stuck_rooms": 9, "load": {}}
        self.setUp()
        self.assertGreaterEqual(len(self.get("technical", days=30)["alerts"]),
                                len(self.get("overview", days=30)["alerts"]))


class TestFewerGamesAlert(AnalyticsTestCase):
    BEFORE = [table(NOW - (35 + i) * DAY, (("Reef", 50), ("Bot", 10))) for i in range(20)]
    AFTER = [table(NOW - (2 + i) * DAY, (("Reef", 50), ("Bot", 10))) for i in range(5)]
    users = {"u-reef": account("Reef", BEFORE + AFTER, created=NOW - 90 * DAY, seen=NOW)}

    def test_a_real_drop_in_games_raises_an_alert(self):
        titles = [a["title"] for a in self.get("overview", days=30)["alerts"]]
        self.assertIn("Fewer games than last period", titles)


class TestQuietData(AnalyticsTestCase):
    """A handful of games must not set anything off, a dashboard that cries
    wolf on a 3-game day trains its owner to ignore it."""
    disk = [disk_game(NOW - DAY, completed=False), disk_game(NOW - DAY, completed=True)]

    def test_a_tiny_sample_raises_no_alert(self):
        titles = [a["title"] for a in self.get("overview", days=30)["alerts"]]
        self.assertNotIn("Players are leaving games early", titles)
        self.assertNotIn("Fewer games than last period", titles)


class TestOtherCollections(AnalyticsTestCase):
    extra_store = {
        "clans": {
            "c1": {"name": "Reefers", "members": {"uid-veteran": {}, "uid-fresh": {}},
                   "seasons": {"2026-Q3": {"points": 40, "games": 9},
                               "2026-Q2": {"points": 100, "games": 30}},
                   "lifetime": {"points": 140}, "created_ts": NOW - 50 * DAY},
            "c2": {"name": "Kelp Gang", "members": {"uid-lapsed": {}},
                   "seasons": {"2026-Q2": {"points": 70, "games": 12}}, "created_ts": NOW - 80 * DAY},
        },
        "supporters": {"uid-veteran": {"tier": "Tsunami"}},
        # Payments are a SUBCOLLECTION of a supporter (or a guest supporter).
        "supporters/uid-veteran/payments": {"cs_1": {"amountCents": 500, "createdAt": NOW - 3 * DAY}},
        "guestSupporters/guest-1/payments": {"cs_2": {"amountCents": 2500, "createdAt": NOW - 40 * DAY}},
        "trades": {
            "a__b": {"status": "completed", "completed_ts": NOW - 2 * DAY},
            "c__d": {"status": "open"},
            "e__f": {"status": "completed", "completed_ts": NOW - 45 * DAY},
        },
    }

    def test_clan_points_are_read_from_the_season_they_belong_to(self):
        """A clan's points live at seasons[<season>].points. The old read looked
        for a top-level field that doesn't exist and showed nobody scoring."""
        d = self.get("clans", days=30)
        self.assertEqual(cards(d)["Points this season"], 40)
        self.assertEqual(d["top"], [{"label": "Reefers", "value": 40}])

    def test_lifetime_clan_points_are_every_season(self):
        d = self.get("clans", days=0)
        self.assertEqual(cards(d)["Points, every season"], 210)
        self.assertEqual([t["label"] for t in d["top"]], ["Reefers", "Kelp Gang"])

    def test_without_the_clan_season_the_latest_season_is_used(self):
        self.wire(clan_season=None)
        self.assertEqual(self.get("clans", days=30)["season"], "2026-Q3")

    def test_purchases_are_read_from_every_supporter(self):
        self.assertEqual(cards(self.get("economy", days=30))["Purchases"], 1)
        self.assertEqual(cards(self.get("economy", days=0))["Purchases"], 2)
        self.assertEqual(cards(self.get("economy", days=30))["Supporters"], 1)

    def test_only_completed_trades_count(self):
        self.assertEqual(cards(self.get("events", days=30))["Trades completed"], 1)
        self.assertEqual(cards(self.get("events", days=0))["Trades completed"], 2)

    def test_a_collection_that_cannot_be_read_is_no_data_not_zero(self):
        class NoClans(FakeDB):
            def collection(self, name):
                if name == "clans":
                    raise RuntimeError("429 quota exceeded")
                return super().collection(name)
        self.db = NoClans(self.store)
        an.reset_caches()
        self.assertIsNone(cards(self.get("clans", days=30))["Clans"])


# ══════════════════════════════════════════════════════════════════════════
#  4, THE PAYLOAD SHAPE THE CLIENT RENDERS
# ══════════════════════════════════════════════════════════════════════════
class TestPayloadShape(AnalyticsTestCase):
    def test_overview_has_ten_or_fewer_cards_and_the_four_blocks(self):
        for days in (30, 0):
            d = self.get("overview", days=days)
            self.assertLessEqual(len(d["cards"]), 10, "the Overview is capped at ten cards on purpose")
            for block in ("growth", "games", "retention", "live"):
                self.assertIn(block, d)

    def test_every_card_has_the_fields_the_renderer_reads(self):
        for days in (30, 0):
            for name in an._SECTIONS:
                for c in self.get(name, days=days).get("cards", []):
                    for key in ("label", "value", "unit", "delta", "hint", "spark", "tone"):
                        self.assertIn(key, c, f"{name} card {c.get('label')!r} is missing {key}")
                    self.assertIsInstance(c["spark"], list)

    def test_series_and_day_labels_are_always_the_same_length(self):
        for days in (7, 30, 0):
            d = self.get("overview", days=days, compare=True)
            n = len(d["growth"]["days"])
            self.assertGreater(n, 0)
            for key, values in d["growth"]["series"].items():
                self.assertEqual(len(values), n, f"growth.{key} is not day-aligned ({days}d)")
            self.assertEqual(len(d["games"]["played"]), len(d["games"]["days"]))
            self.assertIn(d["games"]["gran"], ("day", "week", "month"))

    def test_tables_declare_their_columns_and_a_sort(self):
        for name in ("players", "cards", "competitive", "clans"):
            table_ = self.get(name).get("table") or {}
            self.assertIn("columns", table_, f"{name} table has no columns")
            self.assertTrue(any(c.get("always") for c in table_["columns"]),
                            f"{name} table must have at least one always-on column")
            self.assertTrue(all(c.get("type") for c in table_["columns"]), f"{name} columns need a type")
            self.assertIn("sort", table_)

    def test_search_finds_a_player_and_what_they_play(self):
        d = self.get("search", query="Reef")
        self.assertTrue(d["matches"])
        p = d["player"]
        self.assertEqual(p["name"], "Reef")
        self.assertEqual(p["favorite"], "King Salmon")
        self.assertEqual(p["sizes"], [{"label": "2 players", "value": 4}])
        self.assertEqual({g["result"] for g in p["recent"]}, {"Won", "Lost"})

    def test_search_needs_at_least_two_characters(self):
        d = self.get("search", query="R")
        self.assertEqual(d["matches"], [])
        self.assertIsNone(d["player"])

    def test_search_reaches_the_dev_account_even_though_charts_exclude_it(self):
        # Search is a lookup tool, not a measurement, it must find everyone.
        self.assertTrue(self.get("search", query="Dev")["matches"])

    def test_export_carries_every_section(self):
        self.assertEqual(set(self.get("export")["sections"]), set(an._SECTIONS))

    def test_the_live_tick_carries_only_the_live_panel(self):
        d = self.get("live")
        self.assertEqual(d["live"]["online_players"], 3)
        self.assertNotIn("cards", d)

    def test_every_answer_says_whether_the_player_numbers_are_real(self):
        for name in list(an._SECTIONS) + ["search", "live"]:
            st = self.get(name, query="Reef")["data_status"]
            self.assertTrue(st["ok"], name)
            self.assertEqual(st["accounts"], 6)

    def test_no_email_or_uid_ever_leaves_the_module(self):
        # The account scan reads emails to identify test accounts; none of that
        # may reach the browser.
        for days in (30, 0):
            for name in list(an._SECTIONS) + ["search", "live"]:
                blob = json.dumps(self.get(name, query="Reef", days=days, include_test=True))
                self.assertNotIn("@", blob, f"{name} leaked an email address")
                for uid in make_users():
                    self.assertNotIn(uid, blob, f"{name} leaked a uid")

    def test_a_broken_section_returns_an_error_instead_of_a_500(self):
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


# ══════════════════════════════════════════════════════════════════════════
#  5: CACHING (a dashboard left open must not rescan Firestore per tick)
# ══════════════════════════════════════════════════════════════════════════
class CountingDB(FakeDB):
    def __init__(self, store):
        super().__init__(store)
        self.calls = {}
        self.fail = False

    def collection(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1
        if self.fail:
            raise RuntimeError("429 Quota exceeded")
        return super().collection(name)


class TestCaching(AnalyticsTestCase):
    disk = [disk_game(NOW - DAY)]

    def setUp(self):
        super().setUp()
        self.db = CountingDB(self.store)
        self.wire()

    def scans(self):
        return self.db.calls.get("users", 0)

    def test_the_account_scan_is_cached(self):
        an._load_users()
        first = self.scans()
        an._load_users()
        self.get("gameplay", days=30)
        self.get("players", days=0)
        self.assertEqual(self.scans(), first, "every section shares one scan")

    def test_refresh_rescans_but_never_twice_in_a_few_seconds(self):
        self.get("overview", days=30)
        first = self.scans()
        self.get("overview", days=30, refresh=True)
        self.assertEqual(self.scans(), first, "a scan this fresh is not redone")
        with an._USERS_LOCK:
            an._USERS_CACHE["at"] -= an._USERS_FORCE_FLOOR_SEC + 1
        self.get("overview", days=30, refresh=True)
        self.assertEqual(self.scans(), first + 1)

    def test_requests_arriving_together_share_one_scan(self):
        # Switching the range fires a request while the last one is still
        # scanning. Each used to start its own full read of every account.
        import threading
        real_collection = self.db.collection

        def slow_collection(name):
            if name == "users":
                time.sleep(0.3)
            return real_collection(name)
        self.db.collection = slow_collection
        threads = [threading.Thread(target=an._load_users) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.scans(), 1, "four requests at once, one scan")

    def test_the_live_tick_never_starts_a_scan(self):
        self.get("overview", days=30)
        first = self.scans()
        with an._USERS_LOCK:
            an._USERS_CACHE["at"] -= an._USERS_TTL_SEC * 10       # long expired
        for _ in range(5):
            self.get("live")
        self.assertEqual(self.scans(), first)

    def test_a_failed_scan_keeps_the_last_good_one_and_says_so(self):
        self.assertEqual(cards(self.get("gameplay", days=30))["Games played"], 4)
        self.db.fail = True
        with an._USERS_LOCK:
            an._USERS_CACHE["at"] -= an._USERS_TTL_SEC + 1
        d = self.get("gameplay", days=30)
        self.assertEqual(cards(d)["Games played"], 4, "the last good numbers, not zeros")
        self.assertFalse(d["data_status"]["ok"])
        self.assertIn("Quota", d["data_status"]["error"])
        titles = [a["title"] for a in self.get("overview", days=30)["alerts"]]
        self.assertIn("Couldn't read the player database", titles)

    def test_other_collections_are_cached_too(self):
        self.get("clans", days=30)
        self.get("clans", days=0)
        self.assertEqual(self.db.calls.get("clans", 0), 1)

    def test_a_new_game_on_disk_invalidates_the_record_cache_immediately(self):
        self.assertEqual(len(an._load_games()), 1)
        path = os.path.join(self.games_dir, "game_NEW_1.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(disk_game(NOW - 3600), fh)
        # A distinct mtime, so the (count, newest-mtime) signature really moves.
        os.utime(path, (time.time() + 5, time.time() + 5))
        self.assertEqual(len(an._load_games()), 2)

    def test_unreadable_records_are_skipped_not_fatal(self):
        with open(os.path.join(self.games_dir, "game_BAD_1.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        an.reset_caches()
        self.assertEqual(len(an._load_games()), 1)


class TestFlatProjection(AnalyticsTestCase):
    """Some Firestore SDK builds return a projected read's dotted paths flat.
    Reading only the nested shape against one of those finds nothing and
    confidently reports zero games for everyone."""
    users = {"u-flat": {"nickname": "Flat", "created_at": NOW - 9 * DAY, "last_active": NOW,
                        "stats.completed_games": 12,
                        "stats.normal_games_by_size": {"4": 12},
                        "stats.recent_games": [logged(table(NOW - DAY, (("Flat", 9), ("Bot", 1))), "Flat")]}}

    def test_flat_field_paths_are_read(self):
        self.assertEqual(cards(self.get("overview", days=0))["Games played"], 12)
        self.assertEqual(cards(self.get("gameplay", days=30))["Games played"], 1)


# ══════════════════════════════════════════════════════════════════════════
#  6, THE RECORDS THE GAME ACTUALLY WRITES
# ══════════════════════════════════════════════════════════════════════════
def _source(path):
    with open(os.path.join(HERE, path), "r", encoding="utf-8") as fh:
        return fh.read()


class TestAccountContract(unittest.TestCase):
    """The fields this module reads have to be the ones the game writes. If
    saveGameStats renames one, every games page quietly goes back to empty,
    which is exactly the failure this module was rewritten for."""

    def test_save_game_stats_logs_every_field_the_dashboard_reads(self):
        src = _source("multiplayer/client/js/preview-app.js")
        start = src.index("const recentEntry = {")
        entry = src[start:start + 700]
        for key in ("r:", "s:", "t: Date.now()", "opp:", "pc:", "mode:", "all:", "bds:", "win:", "strat:"):
            self.assertIn(key, entry, f"recentEntry no longer writes {key}")
        for mode in ('"competitive"', '"ranked"', '"team"', '"normal"'):
            self.assertIn(mode, entry, f"recentEntry no longer names the {mode} mode")
        self.assertIn('"stats.recent_games": nextRecent', src)
        self.assertIn(".map(p => ({ n: String(p.name||\"\"), s: Number(p.score||0) }))", src,
                      "`all` must stay a list of {n, s}")

    def test_the_lifetime_counters_are_still_written(self):
        src = _source("multiplayer/client/js/preview-app.js")
        for field in ('"stats.completed_games"', "stats.normal_games_by_size.",
                      "stats.comp_games_by_size.", '"stats.strategy_play_counts"',
                      '"stats.streak_days"', '"stats.hours_played"', '"stats.lifetime_comp_wins"',
                      '"stats.total_score"'):
            self.assertIn(field, src, f"preview-app.js no longer writes {field}")

    def test_streak_days_are_plain_dates(self):
        src = _source("multiplayer/client/js/preview-app.js")
        start = src.index("function _streakLocalDateStr")
        self.assertIn("return `${y}-${m}-${day}`;", src[start:start + 400])

    def test_a_scored_table_game_is_logged_in_the_same_shape(self):
        src = _source("snap_score.py")
        start = src.index("def _history_entry")
        body = src[start:start + 2400]
        for key in ('"r":', '"s":', '"t":', '"opp":', '"pc":', '"mode": "physical"',
                    '"all":', '"bds":', '"win":', '"strat":'):
            self.assertIn(key, body, f"snap_score history entries no longer write {key}")


class TestGameRecordContract(unittest.TestCase):
    """Technical Health still reads the server's own records, so the fields it
    reads have to be the ones _save_game_history writes."""

    def test_save_game_history_writes_the_fields_technical_health_reads(self):
        src = _source("multiplayer_server.py")
        start = src.index("def _save_game_history")
        body = src[start:start + 8000]
        for field in ('"recorded_unix"', '"mode"', '"player_count"', '"human_count"',
                      '"ranked"', '"competitive"', '"team_mode"'):
            self.assertIn(field, body, f"_save_game_history must still write {field}: analytics reads it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
