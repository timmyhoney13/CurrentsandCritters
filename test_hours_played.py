"""The three live numbers on the marketing site: players, hours and games.

All three are the kind of number that fails quietly. A counter that never
increments renders as a dash for ever; one that is rebuilt wrongly walks
BACKWARDS on a public page, which is worse than showing nothing; and one that
is simply measuring the wrong thing looks perfectly healthy while it lies.

The live server was showing all three failures at once, which is what these
tests were rewritten around:

  • hours read 0, because the rebuild only ran at server startup and the live
    games_history directory is empty, so there was never anything to rebuild
    from and never another chance to try;
  • the durations it WOULD have summed are wall clock between the first move
    and the last, so a 47-round game left open overnight is recorded as 114
    hours. Nine saved games claimed 255 hours between them;
  • games counted every file in the history directory, including rooms nobody
    played, while the increment beside it counted only games that ended
    normally. The larger won, so the rule the increment applied was dead;
  • guests were never counted anywhere, though the whole game is open to them.

Run:  python3 test_hours_played.py
"""

import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import multiplayer_server as ms

ROOT = os.path.dirname(os.path.abspath(__file__))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), "r", encoding="utf-8") as f:
        return f.read()


class _StatsDirCase(unittest.TestCase):
    """A temp history directory and stats file, and no route to Firestore."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.games = os.path.join(self.tmp.name, "games_history")
        os.makedirs(self.games)
        self.stats = os.path.join(self.tmp.name, "state", "site_stats.json")

        self._patch(ms, "GAMES_HISTORY_DIR", self.games)
        self._patch(ms, "STATS_PATH", self.stats)
        self._patch(ms, "_get_firestore", lambda: None)
        # The recount caches its answer against a fingerprint of the directory
        # and refuses to run twice in quick succession. Both are per-process,
        # so they are reset around every test or the second one in a class
        # would be served the first one's answer.
        self._patch(ms, "_HISTORY_FINGERPRINT", None)
        self._patch(ms, "_HISTORY_TOTALS", None)
        self._patch(ms, "_HISTORY_RECOUNT_AT", 0.0)

    def _patch(self, mod, name, value):
        old = getattr(mod, name)
        setattr(mod, name, value)
        self.addCleanup(setattr, mod, name, old)

    def _game(self, name, **fields):
        fields.setdefault("mode", "standard")
        with open(os.path.join(self.games, name), "w", encoding="utf-8") as f:
            json.dump(fields, f)

    def _write_stats(self, **fields):
        os.makedirs(os.path.dirname(self.stats), exist_ok=True)
        with open(self.stats, "w", encoding="utf-8") as f:
            json.dump(fields, f)

    def _stored(self):
        try:
            with open(self.stats, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def _sync(self):
        return ms.sync_totals_from_history(force=True)


class TestWhatCountsAsAGame(unittest.TestCase):
    """One rule, asked in one place, by both the live increment and the
    rebuild. They used to disagree, and the disagreement was invisible because
    /api/stats reported whichever answer was larger."""

    def test_a_finished_game_counts(self):
        self.assertTrue(ms.game_counts_as_played({"mode": "standard", "rounds": 44}))
        self.assertTrue(ms.game_counts_as_played({"mode": "competitive", "rounds": 12}))

    def test_an_abandoned_game_counts_if_it_got_going(self):
        """The same bar the leaderboard already applies to a truncated game."""
        self.assertTrue(ms.game_counts_as_played({"mode": "truncated", "rounds": 3}))
        self.assertTrue(ms.game_counts_as_played({"mode": "truncated", "rounds": 40}))

    def test_a_room_nobody_played_is_not_a_game(self):
        self.assertFalse(ms.game_counts_as_played({"mode": "truncated", "rounds": 0}))
        self.assertFalse(ms.game_counts_as_played({"mode": "truncated", "rounds": 2}))
        self.assertFalse(ms.game_counts_as_played({"mode": "truncated"}))

    def test_it_survives_junk(self):
        self.assertFalse(ms.game_counts_as_played(None))
        self.assertFalse(ms.game_counts_as_played("game"))
        self.assertFalse(ms.game_counts_as_played([]))


class TestHowLongAGameIsAllowedToHaveTaken(unittest.TestCase):
    def test_an_ordinary_game_is_counted_as_it_stands(self):
        """The one honestly-timed game in the saved history: 24 rounds, 31
        minutes. Nothing about the clamp may touch a game like this."""
        self.assertEqual(ms.counted_play_seconds(
            {"mode": "standard", "rounds": 24, "duration_sec": 1874}), 1874)

    def test_a_room_left_open_is_capped_not_believed(self):
        """A real record: 47 rounds, an ordinary complete game, stamped 114
        hours because nobody closed the room. Summed raw it was almost half of
        the 255 hours nine games were claiming between them."""
        capped = ms.counted_play_seconds(
            {"mode": "standard", "rounds": 47, "duration_sec": 413175})
        self.assertEqual(capped, ms.MAX_COUNTED_GAME_SECONDS)
        self.assertLess(capped, 413175)

    def test_a_capped_game_still_counts_for_something(self):
        """It was played, all 47 rounds of it. Only the clock is wrong, so the
        game is counted AS the ceiling rather than thrown away."""
        self.assertGreater(ms.counted_play_seconds(
            {"mode": "standard", "rounds": 47, "duration_sec": 413175}), 0)

    def test_a_game_that_does_not_count_is_worth_no_time_either(self):
        self.assertEqual(ms.counted_play_seconds(
            {"mode": "truncated", "rounds": 0, "duration_sec": 90000}), 0)

    def test_missing_and_nonsense_timings_are_zero_not_fatal(self):
        for record in ({"mode": "standard", "rounds": 7},
                       {"mode": "standard", "duration_sec": None},
                       {"mode": "standard", "duration_sec": -50}):
            self.assertEqual(ms.counted_play_seconds(record), 0, record)

    def test_the_ceiling_is_above_any_real_game_and_below_any_left_room(self):
        self.assertGreater(ms.MAX_COUNTED_GAME_SECONDS, 1874 * 2)
        self.assertLess(ms.MAX_COUNTED_GAME_SECONDS, 28499)


class TestTheRebuildFromHistory(_StatsDirCase):
    def test_it_sums_every_game(self):
        self._game("game_a_1.json", duration_sec=1800, rounds=40)
        self._game("game_b_2.json", duration_sec=900, rounds=40)
        self._game("game_c_3.json", duration_sec=300, rounds=40)
        self.assertEqual(self._sync(), (3, 3000, 3))
        self.assertEqual(self._stored()["play_seconds"], 3000)
        self.assertEqual(self._stored()["games_played"], 3)

    def test_it_only_counts_game_records(self):
        """leaderboard.json lives in the same directory and is not a game."""
        self._game("game_a_1.json", duration_sec=600, rounds=40)
        self._game("leaderboard.json", duration_sec=999999, rounds=40)
        self._game("notes.txt", duration_sec=1, rounds=40)
        self.assertEqual(self._sync(), (1, 600, 1))

    def test_an_unreadable_record_is_skipped_not_fatal(self):
        """One corrupt file used to be enough to lose the whole rebuild."""
        self._game("game_a_1.json", duration_sec=600, rounds=40)
        with open(os.path.join(self.games, "game_bad_2.json"), "w") as f:
            f.write("{not json")
        self._game("game_c_3.json", duration_sec=400, rounds=40)
        self.assertEqual(self._sync(), (2, 1000, 2))

    def test_rooms_nobody_played_are_left_out_of_both_totals(self):
        """The games number used to be the FILE COUNT, so an opened-and-left
        room was a game somebody had played as far as the website knew."""
        self._game("game_real_1.json", duration_sec=1800, rounds=44)
        self._game("game_empty_2.json", mode="truncated", rounds=0, duration_sec=50000)
        self._game("game_empty_3.json", mode="truncated", rounds=1, duration_sec=50000)
        self.assertEqual(self._sync(), (1, 1800, 1))

    def test_the_hours_are_clamped_per_game_not_in_total(self):
        self._game("game_a_1.json", duration_sec=413175, rounds=47)
        self._game("game_b_2.json", duration_sec=256222, rounds=42)
        self._game("game_c_3.json", duration_sec=1874, rounds=24)
        games, seconds, _timed = self._sync()
        self.assertEqual(games, 3)
        self.assertEqual(seconds, ms.MAX_COUNTED_GAME_SECONDS * 2 + 1874)


class TestHoursAreAllowedToBeCorrected(_StatsDirCase):
    """The headline change, and the one that needed the most care: hours now
    follow the recount DOWN as well as up."""

    def test_an_inflated_total_is_corrected_downwards(self):
        """The stored figure was summed from an unclamped wall clock. A total
        that can only ever rise can never shed that, which is the whole reason
        the floor had to go."""
        self._write_stats(play_seconds=920583, games_played=9)
        self._game("game_a_1.json", duration_sec=1874, rounds=24)
        self._sync()
        self.assertEqual(self._stored()["play_seconds"], 1874)

    def test_games_are_never_walked_backwards(self):
        """A game that was played stays played. A history directory that has
        lost files must not be able to un-play them, and the live one has lost
        every file it ever had."""
        self._write_stats(play_seconds=5000, games_played=107)
        self._game("game_a_1.json", duration_sec=1800, rounds=40)
        self._sync()
        self.assertEqual(self._stored()["games_played"], 107)

    def test_an_empty_history_says_nothing_rather_than_zero(self):
        """The live server's exact situation: a persistent disk whose
        games_history directory is empty. Read as "nobody has played" it would
        wipe totals that only survive because Firestore is holding them."""
        self._write_stats(play_seconds=50000, games_played=107)
        self.assertIsNone(self._sync())
        stored = self._stored()
        self.assertEqual(stored["play_seconds"], 50000)
        self.assertEqual(stored["games_played"], 107)

    def test_a_missing_history_directory_is_not_an_error(self):
        self._patch(ms, "GAMES_HISTORY_DIR", os.path.join(self.tmp.name, "gone"))
        self.assertIsNone(self._sync())          # must not raise

    def test_a_server_with_no_games_invents_no_stats_file(self):
        self._sync()
        self.assertFalse(os.path.exists(self.stats))

    def test_it_keeps_the_counters_it_did_not_come_for(self):
        self._write_stats(games_played=107, registered_players=43,
                          guest_players=9, seen_uids=["a", "b"])
        self._game("game_a_1.json", duration_sec=7200, rounds=40)
        self._sync()
        stored = self._stored()
        self.assertEqual(stored["play_seconds"], 7200)
        self.assertEqual(stored["games_played"], 107)
        self.assertEqual(stored["registered_players"], 43)
        self.assertEqual(stored["guest_players"], 9)
        self.assertEqual(stored["seen_uids"], ["a", "b"])


class TestTheRecountIsCheapToAskFor(_StatsDirCase):
    """Every booting client asks for this, and then every four minutes after
    that, so the usual answer has to cost a directory listing."""

    def test_a_second_ask_is_throttled(self):
        self._game("game_a_1.json", duration_sec=600, rounds=40)
        self.assertEqual(ms.recount_history_totals(force=True), (1, 600, 1))
        self._game("game_b_2.json", duration_sec=600, rounds=40)
        self.assertEqual(ms.recount_history_totals(), (1, 600, 1),
                         "it re-read the directory inside the throttle window")

    def test_an_unchanged_directory_is_not_re_summed(self):
        self._game("game_a_1.json", duration_sec=600, rounds=40)
        self.assertEqual(ms.recount_history_totals(force=True), (1, 600, 1))
        # Same fingerprint, so the cached answer stands even though the files
        # would now sum to something else if they were opened again.
        with open(os.path.join(self.games, "game_a_1.json"), "r+") as f:
            pass
        self.assertEqual(ms.recount_history_totals(force=True), (1, 600, 1))

    def test_a_new_game_changes_the_fingerprint_and_is_picked_up(self):
        self._game("game_a_1.json", duration_sec=600, rounds=40)
        self.assertEqual(ms.recount_history_totals(force=True), (1, 600, 1))
        self._game("game_b_2.json", duration_sec=400, rounds=40)
        self.assertEqual(ms.recount_history_totals(force=True), (2, 1000, 2))

    def test_the_fingerprint_opens_no_files(self):
        self._game("game_a_1.json", duration_sec=600, rounds=40)
        with open(os.path.join(self.games, "game_bad_2.json"), "w") as f:
            f.write("{not json")
        self.assertIsNotNone(ms._history_fingerprint())   # must not raise


class TestCountingGuests(_StatsDirCase):
    """Guests never sign up, so /api/user/register never hears about them.
    Every one of them was played and none of them were counted."""

    def test_a_guest_is_counted(self):
        self.assertEqual(ms.record_guest_players(["guest_aaaaaaaa"]), 1)
        self.assertEqual(self._stored()["guest_players"], 1)

    def test_the_same_guest_is_never_counted_twice(self):
        ms.record_guest_players(["guest_aaaaaaaa"])
        self.assertEqual(ms.record_guest_players(["guest_aaaaaaaa"]), 0)
        self.assertEqual(self._stored()["guest_players"], 1)

    def test_one_guest_playing_four_games_is_one_player(self):
        for _ in range(4):
            ms.record_guest_players(["guest_aaaaaaaa"])
        self.assertEqual(self._stored()["guest_players"], 1)

    def test_a_table_of_guests_is_counted_once_each(self):
        added = ms.record_guest_players(
            ["guest_aaaaaaaa", "guest_bbbbbbbb", "guest_cccccccc"])
        self.assertEqual(added, 3)
        self.assertEqual(self._stored()["guest_players"], 3)

    def test_a_repeat_inside_one_game_is_still_one_guest(self):
        self.assertEqual(
            ms.record_guest_players(["guest_aaaaaaaa", "guest_aaaaaaaa"]), 1)

    def test_a_table_of_accounts_writes_nothing_at_all(self):
        self.assertEqual(ms.record_guest_players([]), 0)
        self.assertEqual(ms.record_guest_players(["", None]), 0)
        self.assertFalse(os.path.exists(self.stats))

    def test_a_token_that_is_not_ours_is_dropped(self):
        for junk in ("short", "has spaces in it", "x" * 200, "semi;colon", 12345):
            self.assertEqual(ms.record_guest_players([junk]), 0, junk)

    def test_the_remembered_tokens_are_bounded(self):
        """seen_uids grew without limit once already, and a loop posting
        made-up ids is what exposed it."""
        self._patch(ms, "GUEST_TOKEN_MEMORY", 10)
        for i in range(25):
            ms.record_guest_players([f"guest_{i:08d}"])
        stored = self._stored()
        self.assertEqual(stored["guest_players"], 25, "it stopped counting")
        self.assertEqual(len(stored["seen_guest_tokens"]), 10,
                         "the token list is growing without limit")

    def test_it_leaves_the_other_counters_alone(self):
        self._write_stats(games_played=107, registered_players=43,
                          play_seconds=5000, seen_uids=["a"])
        ms.record_guest_players(["guest_aaaaaaaa"])
        stored = self._stored()
        self.assertEqual(stored["guest_players"], 1)
        self.assertEqual(stored["games_played"], 107)
        self.assertEqual(stored["registered_players"], 43)
        self.assertEqual(stored["play_seconds"], 5000)
        self.assertEqual(stored["seen_uids"], ["a"])


class TestThereIsNoOpenGuestEndpoint(unittest.TestCase):
    """/api/user/register can demand a verified Firebase token because there is
    an account behind every caller. A guest has none, so an endpoint that took
    somebody's word for one would be a curl loop away from printing whatever
    number its caller liked: which is exactly what happened to registered
    players before that token check existed."""

    def test_guests_are_counted_only_from_a_finished_game(self):
        src = _read("multiplayer_server.py")
        callers = [line for line in src.splitlines()
                   if "record_guest_players(" in line
                   and not line.lstrip().startswith("def ")]
        self.assertEqual(len(callers), 1, f"expected exactly one caller: {callers}")
        self.assertIn("guest_tokens", callers[0])

    def test_no_route_names_a_guest_counter(self):
        src = _read("multiplayer_server.py")
        for path in re.findall(r'parsed\.path == "(/api/[^"]+)"', src):
            self.assertNotIn("guest", path.lower(),
                             f"{path} looks like an open guest counter")


class TestTheStampLandsOnTheSeat(unittest.TestCase):
    """set_seat_guest_token against a real room, because the source checks
    below only prove it is CALLED in the right place."""

    def _room(self):
        room_id = "GUESTTEST"
        ms.ROOMS.rooms.pop(room_id, None)
        return ms.GameRoom(room_id, "Otter", total_players=4, human_players=4,
                           ai_players=0)

    def test_a_guest_who_sits_down_is_marked(self):
        room = self._room()
        res = room.claim_seat("Pip", 1, None)
        self.assertTrue(res["ok"], res)
        room.set_seat_guest_token(res["seat_index"], "guest_aaaaaaaa")
        seat = next(s for s in room.seats if s.index == res["seat_index"])
        self.assertEqual(seat.guest_token, "guest_aaaaaaaa")

    def test_a_signed_in_player_is_left_unmarked(self):
        room = self._room()
        res = room.claim_seat("Otter 2", 1, None)
        room.set_seat_guest_token(res["seat_index"], "")
        seat = next(s for s in room.seats if s.index == res["seat_index"])
        self.assertEqual(seat.guest_token, "")

    def test_only_the_guests_at_the_table_are_collected(self):
        """What _save_game_history hands to record_guest_players."""
        room = self._room()
        for idx, name in ((1, "Pip"), (2, "Wren"), (3, "Fern")):
            res = room.claim_seat(name, idx, None)
            self.assertTrue(res["ok"], res)
            if name != "Wren":                    # Wren is signed in
                room.set_seat_guest_token(res["seat_index"], f"guest_{name.lower()}xx")
        collected = [s.guest_token for s in room.seats
                     if s.kind == "human" and getattr(s, "guest_token", "")]
        self.assertEqual(sorted(collected), ["guest_fernxx", "guest_pipxx"])

    def test_a_bad_seat_index_is_ignored_not_fatal(self):
        room = self._room()
        room.set_seat_guest_token(99, "guest_aaaaaaaa")     # must not raise
        room.set_seat_guest_token(None, "guest_aaaaaaaa")
        self.assertFalse(any(s.guest_token for s in room.seats))


class TestTheSeatCarriesTheToken(unittest.TestCase):
    def test_the_stamp_happens_once_after_the_claim(self):
        """claim_seat returns from five places and every one of them is a guest
        sitting down, so the stamp is applied to the seat_index it hands back
        rather than inside each branch."""
        src = _read("multiplayer_server.py")
        self.assertIn("def set_seat_guest_token", src)
        self.assertEqual(src.count("set_seat_guest_token("), 2,
                         "the stamp is applied in more than one place")

    def test_a_guest_token_never_reaches_a_game_record(self):
        """It identifies a sitting so it can be counted once. Written into a
        history file it would outlive that sitting, next to the name and score
        of the person who used it."""
        src = _read("multiplayer_server.py")
        record_block = src[src.index('            record = {'):src.index('            fname = f"game_')]
        self.assertNotIn("guest", record_block.lower())

    def test_the_client_sends_it_from_one_place(self):
        src = _read("multiplayer", "client", "js", "preview-app.js")
        self.assertEqual(src.count("guest_token:"), 1,
                         "more than one join path builds the token itself")
        self.assertIn('endsWith("/join")', src)

    def test_the_client_keeps_it_for_the_sitting_only(self):
        """A guest is promised that closing the game leaves nothing of theirs
        on the computer."""
        src = _read("multiplayer", "client", "js", "preview-app.js")
        token_fn = src[src.index("function ccGuestPlayToken()"):]
        token_fn = token_fn[:token_fn.index("\n  }")]
        self.assertIn("sessionStorage", token_fn)
        self.assertNotIn("localStorage", token_fn)


class TestTheTotalsTheAccountsAlreadyHeld(unittest.TestCase):
    """The whole point. The game has been keeping per-player games and hours in
    Firestore all along, and the site was not reading them: it published 107
    games and 0 hours while one account's own Player Home showed 155 and 290."""

    def test_one_account_totals_the_way_the_game_does(self):
        """Copied from the client rather than reinvented, so the site total and
        the sum of the pages it is summing cannot disagree."""
        self.assertEqual(ms._player_total_games({"completed_games": 155}), 155)

    def test_it_falls_back_to_the_by_size_counts(self):
        """preview-app.js: Math.max(completed_games, byNormal + byComp)."""
        stats = {"completed_games": 0,
                 "normal_games_by_size": {"2": 10, "4": 5},
                 "comp_games_by_size": {"4": 3}}
        self.assertEqual(ms._player_total_games(stats), 18)

    def test_the_larger_of_the_two_wins(self):
        stats = {"completed_games": 155, "normal_games_by_size": {"2": 3}}
        self.assertEqual(ms._player_total_games(stats), 155)
        stats = {"completed_games": 2, "normal_games_by_size": {"2": 40}}
        self.assertEqual(ms._player_total_games(stats), 40)

    def test_an_account_with_no_stats_counts_nothing(self):
        for stats in ({}, {"completed_games": 0}, {"normal_games_by_size": {}}):
            self.assertEqual(ms._player_total_games(stats), 0, stats)

    def test_junk_in_a_by_size_map_is_skipped_not_fatal(self):
        stats = {"normal_games_by_size": {"2": 5, "4": None, "8": "x"}}
        self.assertEqual(ms._player_total_games(stats), 5)

    def test_a_by_size_field_that_is_not_a_map_is_ignored(self):
        self.assertEqual(ms._player_total_games(
            {"completed_games": 7, "normal_games_by_size": 99}), 7)

    def test_a_flattened_projection_still_reads(self):
        """Some SDK builds return projected fields as flat paths. Reading only
        the nested shape would find nothing, sum zero, and publish it as a
        confident total: exactly the silent wrong number this is all about."""
        flat = {"stats.completed_games": 155, "stats.hours_played": 290}
        stats = ms._stats_map(flat)
        self.assertEqual(ms._player_total_games(stats), 155)
        self.assertEqual(stats.get("hours_played"), 290)

    def test_the_nested_shape_still_reads(self):
        nested = {"stats": {"completed_games": 155, "hours_played": 290}}
        stats = ms._stats_map(nested)
        self.assertEqual(ms._player_total_games(stats), 155)
        self.assertEqual(stats.get("hours_played"), 290)

    def test_a_document_with_no_stats_at_all_is_skipped(self):
        for doc in ({}, {"email": "x@y.z"}, None, "nonsense"):
            self.assertEqual(ms._stats_map(doc), {}, doc)

    def test_a_flattened_by_size_map_still_totals(self):
        stats = ms._stats_map({"stats.completed_games": 0,
                               "stats.normal_games_by_size": {"2": 6, "4": 3}})
        self.assertEqual(ms._player_total_games(stats), 9)

    def test_it_reads_the_field_the_player_is_shown(self):
        """Player Home prints stats.hours_played, so that is what is summed:
        any other field would make the site disagree with the pages."""
        src = _read("multiplayer_server.py")
        self.assertIn('stats.get("hours_played")', src)
        self.assertIn('"stats.completed_games"', src)

    def test_unreachable_firestore_answers_none_never_zero(self):
        old = ms._get_firestore
        ms._get_firestore = lambda: None
        try:
            self.assertIsNone(ms._fetch_player_stat_totals())
        finally:
            ms._get_firestore = old

    def test_the_account_totals_outrank_anything_derived_here(self):
        src = _read("multiplayer_server.py")
        self.assertIn("if isinstance(acct_games, int) and acct_games > games_played:", src)
        self.assertIn("if isinstance(acct_seconds, int) and acct_seconds > play_seconds_measured:", src)

    def test_the_two_sources_are_not_added_together(self):
        """A game counted on this server was counted on the account too."""
        src = _read("multiplayer_server.py")
        self.assertNotIn("play_seconds_measured + acct_seconds", src)
        self.assertNotIn("games_played + acct_games", src)

    def test_accounts_with_totals_are_not_also_estimated(self):
        src = _read("multiplayer_server.py")
        self.assertIn(
            "untimed_games = max(0, games_played - max(timed_games, acct_games or 0))", src)

    def test_the_split_is_published_for_checking(self):
        src = _read("multiplayer_server.py")
        for field in ('"account_games"', '"account_play_seconds"', '"accounts_counted"'):
            self.assertIn(field, src)


class TestTheAccountScanCannotBurnTheReadBudget(unittest.TestCase):
    """Firestore's free daily allowance ran out once already (2026-09-04) and
    took XP, game history and both passes with it. This query is one read per
    account per refresh, so it is the most expensive thing on the endpoint."""

    def test_it_is_cached_for_a_long_time(self):
        self.assertGreaterEqual(ms.PLAYER_TOTALS_TTL_SEC, 900.0)

    def test_nothing_keeps_it_warm_on_a_timer(self):
        """A sweeper refreshing this would rescan the whole collection for as
        long as one homepage is open anywhere."""
        self.assertEqual(ms._PLAYER_TOTALS_WARM.keep_warm_window, 0.0)

    def test_the_scan_is_bounded(self):
        self.assertTrue(hasattr(ms, "PLAYER_TOTALS_MAX_ACCOUNTS"))
        self.assertGreater(ms.PLAYER_TOTALS_MAX_ACCOUNTS, 0)
        src = _read("multiplayer_server.py")
        self.assertIn("if accounts >= PLAYER_TOTALS_MAX_ACCOUNTS:", src)

    def test_it_asks_for_only_the_fields_it_needs(self):
        src = _read("multiplayer_server.py")
        self.assertIn("users.select(fields).stream()", src)
        self.assertIn("stream = users.stream()", src,
                      "no fallback if the projection is rejected")


class TestTheGamesNobodyTimed(unittest.TestCase):
    """The site said "101 games played" and "0 hours played" on the same row.
    Both cannot be true, and the second one was the lie: the hours counter was
    added long after the games counter and the earlier games were never timed.

    The rule is that a game the site claims is counted at
    AVERAGE_GAME_SECONDS if nothing ever measured it, and at its real duration
    if something did -- never both."""

    def _split(self, games_played, timed_games, measured):
        untimed = max(0, games_played - timed_games)
        estimated = untimed * ms.AVERAGE_GAME_SECONDS
        return measured + estimated, estimated, untimed

    def test_a_site_claiming_games_never_claims_zero_hours(self):
        total, _est, _u = self._split(games_played=107, timed_games=0, measured=0)
        self.assertGreater(total, 0)
        self.assertGreater(total // 3600, 0, "107 games still rounded down to 0 hours")

    def test_a_measured_game_is_not_also_estimated(self):
        """The double-count this whole split exists to prevent."""
        total, estimated, untimed = self._split(
            games_played=1, timed_games=1, measured=1800)
        self.assertEqual(untimed, 0)
        self.assertEqual(estimated, 0)
        self.assertEqual(total, 1800, "the one measured game was counted twice")

    def test_the_estimate_shrinks_as_real_durations_arrive(self):
        totals = [self._split(games_played=100, timed_games=t, measured=t * 1800)[1]
                  for t in (0, 25, 50, 100)]
        self.assertEqual(totals, sorted(totals, reverse=True))
        self.assertEqual(totals[-1], 0, "fully measured and still estimating")

    def test_a_fully_measured_site_reports_only_what_it_measured(self):
        total, estimated, _u = self._split(
            games_played=40, timed_games=40, measured=54321)
        self.assertEqual(estimated, 0)
        self.assertEqual(total, 54321)

    def test_more_timed_games_than_claimed_never_goes_negative(self):
        """games_played is floored at a baseline, so a server that has measured
        more games than the baseline claims must not produce a negative."""
        total, estimated, untimed = self._split(
            games_played=101, timed_games=150, measured=500000)
        self.assertEqual(untimed, 0)
        self.assertEqual(estimated, 0)
        self.assertEqual(total, 500000)

    def test_the_average_is_a_believable_length_for_one_game(self):
        """The only cleanly-timed game in the saved history ran 24 rounds in
        31 minutes, and a full game is 41-47 rounds."""
        self.assertGreaterEqual(ms.AVERAGE_GAME_SECONDS, 20 * 60)
        self.assertLessEqual(ms.AVERAGE_GAME_SECONDS, 90 * 60)
        self.assertLess(ms.AVERAGE_GAME_SECONDS, ms.MAX_COUNTED_GAME_SECONDS)

    def test_the_endpoint_publishes_the_split(self):
        src = _read("multiplayer_server.py")
        for field in ('"play_seconds_measured"', '"play_seconds_estimated"',
                      '"timed_games"', '"untimed_games"', '"average_game_seconds"'):
            self.assertIn(field, src, f"{field} is not on the wire")

    def test_the_endpoint_computes_it_the_way_this_test_does(self):
        """The endpoint subtracts the account totals as well as the server's
        own timed games, so a game the accounts have already counted is not
        also estimated. With the accounts reachable this is normally zero."""
        src = _read("multiplayer_server.py")
        self.assertIn(
            "untimed_games = max(0, games_played - max(timed_games, acct_games or 0))", src)
        self.assertIn("play_seconds_estimated = untimed_games * AVERAGE_GAME_SECONDS", src)
        self.assertIn("play_seconds = play_seconds_measured + play_seconds_estimated", src)


class TestTimedGamesIsTracked(_StatsDirCase):
    """The counter that keeps a measured game from also being estimated."""

    def test_the_rebuild_counts_the_games_it_timed(self):
        self._game("game_a_1.json", duration_sec=1800, rounds=40)
        self._game("game_b_2.json", duration_sec=900, rounds=40)
        self._game("game_c_3.json", rounds=40)          # no timing at all
        games, seconds, timed = self._sync()
        self.assertEqual((games, seconds), (3, 2700))
        self.assertEqual(timed, 2, "an untimed game was counted as timed")
        self.assertEqual(self._stored()["timed_games"], 2)

    def test_a_game_worth_zero_seconds_is_not_a_timed_game(self):
        self._game("game_a_1.json", duration_sec=0, rounds=40)
        _g, _s, timed = self._sync()
        self.assertEqual(timed, 0)

    def test_a_finished_game_bumps_it_beside_the_seconds(self):
        src = _read("multiplayer_server.py")
        self.assertIn('_stats["timed_games"] = int(_stats.get("timed_games", 0) or 0) + 1', src)
        self.assertIn('payload["timed_games"] = Increment(int(n))', src)


class TestTheHoursTileNeverReadsZeroForRealPlay(unittest.TestCase):
    def test_short_playtimes_are_not_floored_away(self):
        """Whole hours are still right ABOVE ten; the bug was flooring
        everything, so 40 minutes of real play published a 0 that looked
        exactly like a server nobody had played."""
        home = _read("index.html")
        self.assertIn("Math.round(liveSeconds / 360) / 10", home,
                      "there is no sub-ten-hour branch")

    def test_small_values_keep_a_decimal(self):
        home = _read("index.html")
        self.assertIn("const decimals = (target > 0 && target < 10) ? 1 : 0;", home)
        self.assertIn("maximumFractionDigits: places", home)

    def test_whole_hours_above_ten(self):
        home = _read("index.html")
        self.assertIn("liveSeconds >= 36000", home)


class TestTheLiveCountsTuple(unittest.TestCase):
    """These counters ride along with the other live counts, so every caller
    unpacks six values now. A caller left on five raises at runtime, in a
    handler, on a page nobody is watching."""

    def test_every_caller_unpacks_six(self):
        src = _read("multiplayer_server.py")
        for line in re.findall(r"^.*=\s*get_live_user_counts\(\).*$", src, re.M):
            self.assertEqual(line.split("=")[0].count(","), 5,
                             f"unpacks the wrong number of values: {line.strip()}")

    def test_a_shorter_tuple_left_by_an_old_build_is_tolerated(self):
        """The warm cache survives a deploy. A cache written by the previous
        build holds five values, and unpacking it into six names would raise
        on the first refresh and leave every live count empty."""
        src = _read("multiplayer_server.py")
        self.assertIn("(tuple(prev) + (None,) * 6)[:6]", src)
        self.assertIn("(tuple(counts) + (None,) * 6)[:6]", src)


class TestWhatTheEndpointSends(unittest.TestCase):
    def test_it_sends_seconds_and_hours(self):
        src = _read("multiplayer_server.py")
        self.assertIn('"play_seconds": play_seconds', src)
        self.assertIn('"hours_played": play_seconds // 3600', src)

    def test_it_sends_the_total_and_both_halves_of_it(self):
        src = _read("multiplayer_server.py")
        self.assertIn('"registered_players": registered_players', src)
        self.assertIn('"guest_players": guest_players', src)
        self.assertIn('"players_total": registered_players + guest_players', src)

    def test_booting_the_game_rechecks_the_totals(self):
        """The rebuild used to run once, at server startup. On a box that stays
        up for weeks that meant the figure was as old as the deploy."""
        src = _read("multiplayer_server.py")
        health = src[src.index('if parsed.path == "/api/health":'):]
        health = health[:health.index("            return")]
        self.assertIn("sync_totals_from_history", health)
        self.assertIn("threading.Thread", health,
                      "the health check waits on a directory listing")

    def test_a_finished_game_banks_a_believable_duration(self):
        """The seconds come off the record that was just written, through the
        same function the rebuild uses, so the counter and the history files
        cannot disagree."""
        src = _read("multiplayer_server.py")
        self.assertIn("_secs = counted_play_seconds(record)", src)
        self.assertIn('_stats["play_seconds"] = int(_stats.get("play_seconds", 0)) + _secs', src)
        self.assertIn("bump_firestore_games_played(1, _secs)", src)

    def test_the_increment_and_the_rebuild_ask_the_same_question(self):
        src = _read("multiplayer_server.py")
        self.assertIn("_counts = game_counts_as_played(record)", src)
        self.assertNotIn('if ended_normally:\n                try:', src,
                         "the increment is back on its own private rule")


class TestWhatTheWebsiteShows(unittest.TestCase):
    def test_it_reads_the_seconds_and_rounds_them_itself(self):
        home = _read("index.html")
        self.assertIn("data.play_seconds", home)
        self.assertIn("liveSeconds / 3600", home)
        self.assertIn('data-placeholder="hours"', home)

    def test_the_player_number_includes_guests(self):
        home = _read("index.html")
        self.assertIn("data.players_total", home)
        self.assertIn("data.registered_players", home,
                      "no fallback for a server that has not deployed yet")

    def test_the_label_no_longer_says_only_registered(self):
        home = _read("index.html")
        band = home[home.index('class="impact-band big-impact"'):]
        band = band[:band.index("</div>\n\n")]
        self.assertNotIn("Registered Players", band)


if __name__ == "__main__":
    unittest.main(verbosity=2)
