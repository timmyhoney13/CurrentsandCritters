"""Leaderboards refresh when a game finishes, not on a clock.

This is the fix for the Firestore quota outage of 2026-09-02. The boards were
cached with a 60s TTL and kept warm by a sweeper that re-ran any board asked
for in the last five minutes, so one player opening the Leaderboard tab set all
16 boards re-querying every ~45 seconds for the next five minutes. Every board
streams a full document per player, so those refreshes cost 16 x <players>
reads a minute and bought nothing, because nobody's stats had moved. The Spark
free tier allows 50,000 reads a day; once it was gone EVERY Firestore read on
the server failed with `429 Quota exceeded` and the supporter wall, the
leaderboards, the clan standings and the admin tools all went dark at once.

A board's contents only change when somebody's stats change, and stats change
when a game ends. So the event refreshes it and the TTL is only a backstop.

Run:  python3 test_leaderboard_refresh.py
"""

import io
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import multiplayer_server as ms


class _Counter:
    """A fetch that records how many real queries it was asked to run."""

    def __init__(self, value="rows"):
        self.calls = 0
        self.value = value

    def __call__(self):
        self.calls += 1
        return self.value


class LeaderboardRefreshTest(unittest.TestCase):
    def setUp(self):
        ms._LB_WARM.invalidate()
        self._settle = ms._LB_SETTLE_SEC

    def tearDown(self):
        ms._LB_SETTLE_SEC = self._settle
        ms._LB_WARM.invalidate()

    def _age(self, key, seconds):
        """Backdate a cached entry, so a sweep can be tested without waiting."""
        with ms._LB_WARM._lock:
            ms._LB_WARM._entries[key]["at"] = time.time() - seconds

    # ── the burn that caused the outage ────────────────────────────────────
    def test_a_warm_board_is_not_requeried_on_a_clock(self):
        """THE REGRESSION TEST. At 100 seconds old a board used seconds ago was
        re-queried under the old 60s TTL (the sweeper fires at 75% of it, so
        45s). It must not be now: nothing has happened that could change it."""
        fetch = _Counter()
        ms._LB_WARM.get("board|50", fetch)
        self.assertEqual(fetch.calls, 1)
        self._age("board|50", 100)
        ms._LB_WARM.sweep()
        self.assertEqual(fetch.calls, 1,
                         "a board was re-queried on a timer with no game "
                         "finished: this is the Firestore quota burn")

    def test_the_ttl_is_a_backstop_not_the_refresh_mechanism(self):
        self.assertGreaterEqual(
            ms._LB_TTL_SEC, 300.0,
            "a short TTL puts the sweeper back on a ~45s clock, which is what "
            "emptied the daily Firestore quota")

    def test_a_board_nobody_opens_is_never_queried(self):
        watched, ignored = _Counter(), _Counter()
        ms._LB_WARM.get("watched|50", watched)
        ms._LB_WARM.sweep()
        self.assertEqual(ignored.calls, 0)
        self.assertEqual(watched.calls, 1)

    def test_serving_a_cached_board_costs_no_query(self):
        fetch = _Counter()
        for _ in range(20):
            ms._LB_WARM.get("board|50", fetch)
        self.assertEqual(fetch.calls, 1)

    # ── the refresh that replaces it ───────────────────────────────────────
    def test_a_finished_game_drops_every_cached_board(self):
        a, b = _Counter(), _Counter()
        ms._LB_WARM.get("xp|50", a)
        ms._LB_WARM.get("wins|75", b)
        self.assertEqual((a.calls, b.calls), (1, 1))

        ms._LB_SETTLE_SEC = 0.05
        ms.invalidate_leaderboards_after_game()

        # Next reader of each board pays for one live query, and gets new rows.
        ms._LB_WARM.get("xp|50", a)
        ms._LB_WARM.get("wins|75", b)
        self.assertEqual((a.calls, b.calls), (2, 2))

    def test_invalidating_costs_no_query_by_itself(self):
        fetch = _Counter()
        ms._LB_WARM.get("board|50", fetch)
        ms._LB_SETTLE_SEC = 0.05
        ms.invalidate_leaderboards_after_game()
        time.sleep(0.2)
        # Dropping a board is free: nothing is re-queried until someone looks.
        self.assertEqual(fetch.calls, 1)

    def test_the_second_pass_throws_away_stats_cached_before_the_writes_landed(self):
        """The race the settle pass exists for: the finishing player's stats are
        written by their own CLIENT, so a reader arriving right after the game
        can re-cache the PRE-game numbers. The delayed second invalidation is
        what stops those sticking for the whole TTL."""
        fetch = _Counter()
        ms._LB_WARM.get("xp|50", fetch)          # pre-game rows, cached
        ms._LB_SETTLE_SEC = 0.05
        ms.invalidate_leaderboards_after_game()

        # A player opens the tab before the client's write lands: the stale
        # numbers go straight back into the cache.
        ms._LB_WARM.get("xp|50", fetch)
        self.assertEqual(fetch.calls, 2)
        self.assertGreater(ms._LB_WARM.stored_at("xp|50"), 0.0)

        time.sleep(0.25)                          # the writes land; second pass
        self.assertEqual(ms._LB_WARM.stored_at("xp|50"), 0.0,
                         "the settle pass did not drop the pre-game rows, so "
                         "they would be served for the whole TTL")
        ms._LB_WARM.get("xp|50", fetch)
        self.assertEqual(fetch.calls, 3)

    def test_the_settle_timer_never_holds_the_process_open(self):
        ms._LB_SETTLE_SEC = 30.0
        ms.invalidate_leaderboards_after_game()
        import threading
        timers = [t for t in threading.enumerate() if isinstance(t, threading.Timer)]
        self.assertTrue(timers, "no settle timer was started")
        self.assertTrue(all(t.daemon for t in timers),
                        "a non-daemon timer keeps the server alive after shutdown")
        for t in timers:
            t.cancel()

    def test_a_broken_cache_never_breaks_the_game(self):
        real = ms._LB_WARM.invalidate

        def boom(*a, **kw):
            raise RuntimeError("firestore is having a day")

        ms._LB_WARM.invalidate = boom
        try:
            ms.invalidate_leaderboards_after_game()   # must not raise
        finally:
            ms._LB_WARM.invalidate = real

    # ── the hook is actually wired to a finished game ──────────────────────
    def test_the_game_completion_path_calls_it(self):
        """A cache that is never invalidated is a cache that is always wrong,
        and the call site is one line in a long method, easy to lose."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "multiplayer_server.py")
        with io.open(path, encoding="utf-8") as fh:
            src = fh.read()
        i = src.find("def _update_history_leaderboard")
        self.assertGreater(i, 0)
        # The call must sit with the "this game counted" branch, above the
        # method definition it shares a block with.
        head = src[:i]
        self.assertIn("invalidate_leaderboards_after_game()", head,
                      "nothing invalidates the boards when a game finishes")
        self.assertIn("if ended_normally or rounds_played >= 3:", head)


if __name__ == "__main__":
    unittest.main(verbosity=2)
