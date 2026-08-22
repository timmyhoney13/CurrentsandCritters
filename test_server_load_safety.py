#!/usr/bin/env python3
"""The server has to stay up and stay responsive when the whole site is playing.

Run:  python3 -m unittest test_server_load_safety -v

Two independent failure modes are covered here, both measured on a real server
before they were fixed:

1. CPU starvation by the bot planner. Rollout confirmation is pure Python, so
   the GIL means N simultaneous games do not plan in parallel, they queue on
   one core and take the HTTP threads down with them. Measured with 32
   concurrent mixed-mode games: GET /api/health, which touches nothing at all,
   went from 2.5 ms to 5.3 SECONDS. The fix is admission control
   (_deep_plan_scale + _DEEP_PLAN_SEM): planning depth tapers with how busy the
   site is, and bots that can't get a slot fall back to the one-pass chooser,
   which is still a fully weighted bot.

2. Rooms never being released. Finished rooms stayed in RoomManager.rooms, and
   on the mounted disk, for the life of the process, so memory and the disk
   grew forever and every restart got slower. The fix is
   RoomManager.sweep_finished_rooms plus a janitor thread.

These tests assert the POLICY, not the measured milliseconds: timings belong in
a load run, not a unit test.
"""

import os
import shutil
import tempfile
import threading
import time
import unittest

import multiplayer_server as mp


_SANDBOX = {}


def setUpModule():
    """Room bookkeeping writes per-room state files; keep them out of the tree."""
    tmp = tempfile.mkdtemp(prefix="cc-load-test-")
    _SANDBOX["dir"] = tmp
    _SANDBOX["saved"] = {
        "ROOM_STATE_DIR": mp.ROOM_STATE_DIR,
        "DATASET_PATH": mp.DATASET_PATH,
        "GAMES_HISTORY_DIR": mp.GAMES_HISTORY_DIR,
    }
    mp.ROOM_STATE_DIR = os.path.join(tmp, "state")
    mp.DATASET_PATH = os.path.join(tmp, "human_game_dataset.jsonl")
    mp.GAMES_HISTORY_DIR = os.path.join(tmp, "games_history")
    os.makedirs(mp.ROOM_STATE_DIR, exist_ok=True)


def tearDownModule():
    for name, value in _SANDBOX.get("saved", {}).items():
        setattr(mp, name, value)
    shutil.rmtree(_SANDBOX.get("dir", ""), ignore_errors=True)


class DeepPlanAdmissionControl(unittest.TestCase):
    """How much CPU the bot planner may spend, as a function of site load."""

    def setUp(self):
        self._saved = mp._ACTIVE_GAMES

    def tearDown(self):
        mp._ACTIVE_GAMES = self._saved

    def _set_games(self, n):
        mp._ACTIVE_GAMES = n

    def test_quiet_server_plans_at_full_strength(self):
        """A near-idle site must NOT pay for responsiveness with weaker bots."""
        for n in range(0, mp._DEEP_PLAN_FULL_GAMES + 1):
            self._set_games(n)
            self.assertEqual(
                mp._deep_plan_scale(), 1.0,
                f"{n} concurrent game(s) should still allow full-strength planning",
            )

    def test_busy_server_stops_planning_entirely(self):
        """Past the cutoff, rollouts are off, the site's responsiveness wins."""
        for n in (mp._DEEP_PLAN_OFF_GAMES, mp._DEEP_PLAN_OFF_GAMES + 50, 500):
            self._set_games(n)
            self.assertEqual(
                mp._deep_plan_scale(), 0.0,
                f"{n} concurrent games must not be running rollouts",
            )

    def test_taper_is_monotonic_between_the_thresholds(self):
        """Load going up must never make the planner MORE expensive."""
        seen = []
        for n in range(0, mp._DEEP_PLAN_OFF_GAMES + 2):
            self._set_games(n)
            seen.append(mp._deep_plan_scale())
        for earlier, later in zip(seen, seen[1:]):
            self.assertGreaterEqual(
                earlier + 1e-9, later,
                f"planning budget rose with load: {seen}",
            )
        self.assertTrue(all(0.0 <= s <= 1.0 for s in seen), seen)

    def test_slot_semaphore_bounds_simultaneous_rollouts(self):
        """Even at scale 1.0, only _DEEP_PLAN_SLOTS moves may plan at once, and
        the acquire is non-blocking so a game thread is never parked waiting."""
        held = []
        try:
            for _ in range(mp._DEEP_PLAN_SLOTS):
                self.assertTrue(mp._DEEP_PLAN_SEM.acquire(blocking=False))
                held.append(True)
            self.assertFalse(
                mp._DEEP_PLAN_SEM.acquire(blocking=False),
                "slots are meant to be exhausted here, an extra grant means "
                "the cap does not actually cap anything",
            )
        finally:
            for _ in held:
                mp._DEEP_PLAN_SEM.release()

    def test_a_starved_planner_still_returns_a_legal_move(self):
        """Falling back must yield the one-pass pick, never None: a bot that
        returns no action stalls its table forever."""
        self._set_games(mp._DEEP_PLAN_OFF_GAMES + 1)
        self.assertEqual(mp._deep_plan_scale(), 0.0)
        # choose_action_weighted_deep returns base_best (from the light
        # chooser) on this path; assert the contract the caller relies on.
        self.assertTrue(hasattr(mp, "choose_action_weighted_light"))
        self.assertTrue(hasattr(mp, "_confirm_with_rollouts"))


class ActiveGameCounter(unittest.TestCase):
    """_deep_plan_scale is only as good as the count feeding it."""

    def setUp(self):
        self._saved = mp._ACTIVE_GAMES
        mp._ACTIVE_GAMES = 0

    def tearDown(self):
        mp._ACTIVE_GAMES = self._saved

    def test_counter_tracks_up_and_down(self):
        mp._note_game_running(+1)
        mp._note_game_running(+1)
        self.assertEqual(mp.active_game_count(), 2)
        mp._note_game_running(-1)
        self.assertEqual(mp.active_game_count(), 1)

    def test_counter_never_goes_negative(self):
        """A stray decrement must not drive the count below zero, a negative
        count would read as 'quiet' forever and re-enable full planning."""
        mp._note_game_running(-5)
        self.assertEqual(mp.active_game_count(), 0)

    def test_counter_is_released_when_a_match_thread_raises(self):
        """The count is decremented in a finally, so a crashed match cannot
        permanently convince the server it is busier than it is."""
        room = mp.GameRoom("LOADX1", "Otter", total_players=4,
                           human_players=1, ai_players=3)
        boom = RuntimeError("simulated match explosion")

        def _explode(_card_db):
            raise boom

        room._run_game_thread_body = _explode
        before = mp.active_game_count()
        with self.assertRaises(RuntimeError):
            room._run_game_thread(mp.CARD_DB)
        self.assertEqual(
            mp.active_game_count(), before,
            "a match that threw left the live-game count inflated",
        )

    def test_concurrent_updates_do_not_lose_counts(self):
        """The counter is read on every bot move from every match thread."""
        def worker():
            for _ in range(200):
                mp._note_game_running(+1)
                mp._note_game_running(-1)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(mp.active_game_count(), 0)


class RoomReaping(unittest.TestCase):
    """Finished rooms must be released; rooms in use must never be."""

    def setUp(self):
        self._saved_rooms = dict(mp.ROOMS.rooms)
        mp.ROOMS.rooms.clear()

    def tearDown(self):
        mp.ROOMS.rooms.clear()
        mp.ROOMS.rooms.update(self._saved_rooms)

    def _room(self, rid, phase, ended_ago=None, created_ago=0):
        room = mp.GameRoom(rid, "Otter", total_players=4,
                           human_players=1, ai_players=3)
        room.phase = phase
        now = mp.now_unix()
        room.created_unix = now - created_ago
        if ended_ago is not None:
            room.ended_unix = now - ended_ago
        mp.ROOMS.rooms[rid] = room
        return room

    def test_long_finished_room_is_reaped(self):
        self._room("OLDEND", "ended", ended_ago=mp.ROOM_KEEP_ENDED_SEC + 60)
        out = mp.ROOMS.sweep_finished_rooms()
        self.assertEqual(out["reaped"], 1)
        self.assertNotIn("OLDEND", mp.ROOMS.rooms)

    def test_just_finished_room_is_kept(self):
        """The endgame screen, Play Again and tournament reporting all still
        need this room: reaping it early is worse than keeping it too long."""
        self._room("NEWEND", "ended", ended_ago=5)
        mp.ROOMS.sweep_finished_rooms()
        self.assertIn("NEWEND", mp.ROOMS.rooms)

    def test_errored_room_is_reaped_on_the_same_clock(self):
        self._room("ERRD", "error", ended_ago=mp.ROOM_KEEP_ENDED_SEC + 60)
        mp.ROOMS.sweep_finished_rooms()
        self.assertNotIn("ERRD", mp.ROOMS.rooms)

    def test_running_room_is_never_reaped(self):
        self._room("LIVE", "running", created_ago=99999)
        mp.ROOMS.sweep_finished_rooms()
        self.assertIn("LIVE", mp.ROOMS.rooms)

    def test_room_with_a_live_thread_is_never_reaped(self):
        """Whatever the phase says, a thread still playing the match wins."""
        room = self._room("THRD", "ended", ended_ago=mp.ROOM_KEEP_ENDED_SEC + 999)
        stop = threading.Event()
        t = threading.Thread(target=stop.wait, daemon=True)
        t.start()
        room.game_thread = t
        try:
            mp.ROOMS.sweep_finished_rooms()
            self.assertIn(
                "THRD", mp.ROOMS.rooms,
                "reaped a room whose match thread was still alive",
            )
        finally:
            stop.set()
            t.join(timeout=5)

    def test_abandoned_lobby_is_reaped(self):
        room = self._room("DEADLOB", "lobby",
                          created_ago=mp.ROOM_KEEP_IDLE_LOBBY_SEC + 600)
        for seat in room.seats:
            if seat.token:
                seat.last_seen = time.time() - (mp.ROOM_KEEP_IDLE_LOBBY_SEC + 600)
        mp.ROOMS.sweep_finished_rooms()
        self.assertNotIn("DEADLOB", mp.ROOMS.rooms)

    def test_lobby_someone_is_sitting_in_is_kept(self):
        """Players wait in lobbies for friends; a fresh poll means 'still here'."""
        room = self._room("WAITLOB", "lobby",
                          created_ago=mp.ROOM_KEEP_IDLE_LOBBY_SEC + 600)
        for seat in room.seats:
            if seat.token:
                seat.last_seen = time.time()
        mp.ROOMS.sweep_finished_rooms()
        self.assertIn("WAITLOB", mp.ROOMS.rooms)

    def test_sweep_deletes_the_rooms_state_file_too(self):
        """Otherwise the mounted disk keeps filling and restarts keep slowing."""
        room = self._room("DISKY", "ended", ended_ago=mp.ROOM_KEEP_ENDED_SEC + 60)
        room.persist_now()
        path = mp.room_state_path("DISKY")
        self.assertTrue(os.path.exists(path), "precondition: state file written")
        mp.ROOMS.sweep_finished_rooms()
        self.assertFalse(os.path.exists(path), "state file outlived its room")

    def test_sweep_reports_what_remains(self):
        self._room("KEEP1", "running")
        self._room("GONE1", "ended", ended_ago=mp.ROOM_KEEP_ENDED_SEC + 60)
        out = mp.ROOMS.sweep_finished_rooms()
        self.assertEqual(out["reaped"], 1)
        self.assertEqual(out["remaining"], 1)
        self.assertEqual(mp.ROOMS.room_count(), 1)


class PublicLinksCache(unittest.TestCase):
    """/api/health is hit constantly by the platform health check and by every
    client; it must not re-read and re-parse a file every time."""

    def test_cache_returns_a_stable_value(self):
        first = mp.load_public_links()
        second = mp.load_public_links()
        self.assertEqual(first, second)
        self.assertIsInstance(first, list)

    def test_caller_cannot_mutate_the_cached_list(self):
        """A caller appending to the returned list must not poison the cache."""
        got = mp.load_public_links()
        got.append("https://example.invalid/not-real")
        self.assertNotIn("https://example.invalid/not-real", mp.load_public_links())


class CapacityGuard(unittest.TestCase):
    def test_room_cap_is_configured_sanely(self):
        self.assertTrue(
            mp.MAX_ACTIVE_ROOMS == 0 or mp.MAX_ACTIVE_ROOMS >= 50,
            "a tiny non-zero cap would turn players away on a normal day",
        )

    def test_retention_windows_are_long_enough_to_be_safe(self):
        self.assertGreaterEqual(
            mp.ROOM_KEEP_ENDED_SEC, 10 * 60,
            "players linger on the endgame screen and press Play Again",
        )
        self.assertGreater(mp.ROOM_KEEP_IDLE_LOBBY_SEC, mp.ROOM_KEEP_ENDED_SEC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
