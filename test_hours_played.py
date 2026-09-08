"""Hours played online: the third number on the marketing site.

The homepage shows registered players, hours played and games played. Two of
those already existed. This one is new, and it is the kind of number that fails
quietly: a counter that never increments renders as a dash for ever, and a
counter that is rebuilt wrongly walks BACKWARDS on the public site, which is
worse than showing nothing.

So the pieces are checked separately:

  • the seconds are already on every game record (duration_sec), which is what
    makes a rebuild possible at all,
  • the rebuild sums exactly those records, ignores anything it cannot read,
    and is applied as a FLOOR so a half-written history directory can never
    lower a real total,
  • /api/stats sends seconds, not hours: rounding is the caller's decision.

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


class TestTheRebuildFromHistory(unittest.TestCase):
    """heal_play_seconds_from_history() against a real directory of records."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.games = os.path.join(self.tmp.name, "games_history")
        os.makedirs(self.games)
        self.stats = os.path.join(self.tmp.name, "state", "site_stats.json")

        # Point the module at the temp dirs, and away from Firestore: the heal
        # mirrors into it, and a test must never reach for a network.
        self._patch(ms, "GAMES_HISTORY_DIR", self.games)
        self._patch(ms, "STATS_PATH", self.stats)
        self._patch(ms, "_get_firestore", lambda: None)

    def _patch(self, mod, name, value):
        old = getattr(mod, name)
        setattr(mod, name, value)
        self.addCleanup(setattr, mod, name, old)

    def _game(self, name, **fields):
        with open(os.path.join(self.games, name), "w", encoding="utf-8") as f:
            json.dump(fields, f)

    def _stored(self):
        try:
            with open(self.stats, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def test_it_sums_every_record(self):
        self._game("game_a_1.json", duration_sec=1800)
        self._game("game_b_2.json", duration_sec=900)
        self._game("game_c_3.json", duration_sec=300)
        ms.heal_play_seconds_from_history()
        self.assertEqual(self._stored()["play_seconds"], 3000)

    def test_it_only_counts_game_records(self):
        """leaderboard.json lives in the same directory and is not a game."""
        self._game("game_a_1.json", duration_sec=600)
        self._game("leaderboard.json", duration_sec=999999)
        self._game("notes.txt", duration_sec=1)
        ms.heal_play_seconds_from_history()
        self.assertEqual(self._stored()["play_seconds"], 600)

    def test_an_unreadable_record_is_skipped_not_fatal(self):
        """One corrupt file used to be enough to lose the whole rebuild."""
        self._game("game_a_1.json", duration_sec=600)
        with open(os.path.join(self.games, "game_bad_2.json"), "w") as f:
            f.write("{not json")
        self._game("game_c_3.json", duration_sec=400)
        ms.heal_play_seconds_from_history()
        self.assertEqual(self._stored()["play_seconds"], 1000)

    def test_old_records_with_no_timing_count_as_zero(self):
        """Games finished before duration_sec existed have none, and a missing
        field must not be read as a missing GAME."""
        self._game("game_a_1.json", duration_sec=600)
        self._game("game_old_2.json", rounds=7)          # no timing at all
        self._game("game_bad_3.json", duration_sec=None)
        self._game("game_neg_4.json", duration_sec=-50)  # clamped, not subtracted
        ms.heal_play_seconds_from_history()
        self.assertEqual(self._stored()["play_seconds"], 600)

    def test_it_is_a_floor_and_never_lowers_a_real_total(self):
        """The public number must only ever climb. A disk that lost half its
        history must not roll the site back to what survived."""
        os.makedirs(os.path.dirname(self.stats), exist_ok=True)
        with open(self.stats, "w", encoding="utf-8") as f:
            json.dump({"play_seconds": 50000, "games_played": 12}, f)
        self._game("game_a_1.json", duration_sec=600)
        ms.heal_play_seconds_from_history()
        stored = self._stored()
        self.assertEqual(stored["play_seconds"], 50000)
        self.assertEqual(stored["games_played"], 12, "it clobbered the other counters")

    def test_it_keeps_the_counters_it_did_not_come_for(self):
        os.makedirs(os.path.dirname(self.stats), exist_ok=True)
        with open(self.stats, "w", encoding="utf-8") as f:
            json.dump({"games_played": 107, "registered_players": 43,
                       "seen_uids": ["a", "b"]}, f)
        self._game("game_a_1.json", duration_sec=7200)
        ms.heal_play_seconds_from_history()
        stored = self._stored()
        self.assertEqual(stored["play_seconds"], 7200)
        self.assertEqual(stored["games_played"], 107)
        self.assertEqual(stored["registered_players"], 43)
        self.assertEqual(stored["seen_uids"], ["a", "b"])

    def test_an_empty_history_writes_nothing(self):
        ms.heal_play_seconds_from_history()
        self.assertFalse(os.path.exists(self.stats),
                         "a server with no games invented a stats file")

    def test_a_missing_history_directory_is_not_an_error(self):
        self._patch(ms, "GAMES_HISTORY_DIR", os.path.join(self.tmp.name, "gone"))
        ms.heal_play_seconds_from_history()   # must not raise


class TestTheLiveCountsTuple(unittest.TestCase):
    """play_seconds rides along with the other live counts, so every caller
    unpacks four values now. A caller left on three raises at runtime, in a
    handler, on a page nobody is watching."""

    def test_every_caller_unpacks_four(self):
        src = _read("multiplayer_server.py")
        for line in re.findall(r"^.*=\s*get_live_user_counts\(\).*$", src, re.M):
            self.assertEqual(line.split("=")[0].count(","), 3,
                             f"unpacks the wrong number of values: {line.strip()}")

    def test_a_three_tuple_left_by_the_old_build_is_tolerated(self):
        """The warm cache survives a deploy. A cache written by the previous
        build holds three values, and unpacking it into four names would raise
        on the first refresh and leave every live count empty."""
        src = _read("multiplayer_server.py")
        self.assertIn("(tuple(prev) + (None,) * 4)[:4]", src)
        self.assertIn("(tuple(counts) + (None,) * 4)[:4]", src)


class TestWhatTheEndpointSends(unittest.TestCase):
    def test_it_sends_seconds_and_hours(self):
        src = _read("multiplayer_server.py")
        self.assertIn('"play_seconds": play_seconds', src)
        self.assertIn('"hours_played": play_seconds // 3600', src)

    def test_the_website_reads_the_seconds_and_rounds_them_itself(self):
        home = _read("index.html")
        self.assertIn("data.play_seconds", home)
        self.assertIn("liveSeconds / 3600", home)
        self.assertIn('data-placeholder="hours"', home)

    def test_a_finished_game_bumps_both_counters(self):
        """The seconds come off the record that was just written, so the
        counter and the history files can never disagree."""
        src = _read("multiplayer_server.py")
        self.assertIn('_stats["play_seconds"] = int(_stats.get("play_seconds", 0)) + int(record.get("duration_sec", 0) or 0)', src)
        self.assertIn('bump_firestore_games_played(1, int(record.get("duration_sec", 0) or 0))', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
