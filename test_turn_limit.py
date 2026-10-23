#!/usr/bin/env python3
"""The turn clock: a room where one turn may only take so long.

Run:  python3 -m unittest test_turn_limit -v

A host can put a limit on how long ONE TURN takes. The ladder is 5-second
steps with a 2-second rung underneath the bottom of it, so 2 is the fastest a
turn can be set to and the only value in the list that is not a multiple of
five. Both competitive modes ALWAYS run a clock and it is never slower than
ten seconds; inside that ceiling the host still chooses, including the rungs
below five.

Three things have to hold, and only the first is cheap to believe:

  1. the ladder itself, including what a hand-made request asking for 7
     seconds or for four hours is snapped to;
  2. the rule survives the round trip, a room restored from a checkpoint comes
     back on the same clock and a competitive room can never come back off one;
  3. the clock actually MOVES THE GAME ON. That is the whole feature, and it
     is the part no amount of reading the settings can prove, so the last test
     starts a real match on the real engine thread with a two-second limit,
     never touches it, and watches the turns go round by themselves.
"""

import os
import shutil
import tempfile
import time
import unittest

import multiplayer_server as mp


_SANDBOX = {}


def setUpModule():
    """The last test plays a real match, and a real match writes a training
    record and a game-history file. Point both at a temp dir so a throwaway
    test game never lands in the shipped dataset."""
    tmp = tempfile.mkdtemp(prefix="cc-turnlimit-test-")
    _SANDBOX["dir"] = tmp
    _SANDBOX["saved"] = {
        "DATASET_PATH": mp.DATASET_PATH,
        "GAMES_HISTORY_DIR": mp.GAMES_HISTORY_DIR,
        "GAMES_LEADERBOARD_PATH": mp.GAMES_LEADERBOARD_PATH,
        "ROOM_STATE_DIR": mp.ROOM_STATE_DIR,
    }
    mp.DATASET_PATH = os.path.join(tmp, "human_game_dataset.jsonl")
    mp.GAMES_HISTORY_DIR = os.path.join(tmp, "games_history")
    mp.GAMES_LEADERBOARD_PATH = os.path.join(mp.GAMES_HISTORY_DIR, "leaderboard.json")
    mp.ROOM_STATE_DIR = os.path.join(tmp, "state")
    os.makedirs(mp.ROOM_STATE_DIR, exist_ok=True)


def tearDownModule():
    for name, value in _SANDBOX.get("saved", {}).items():
        setattr(mp, name, value)
    shutil.rmtree(_SANDBOX.get("dir", ""), ignore_errors=True)


class TestTheLadder(unittest.TestCase):
    """normalize_turn_limit is the only thing that decides what a clock may be.
    The modal offers the same numbers, but the server is what answers."""

    def test_off_is_off_in_a_casual_room(self):
        for asked in (0, -1, None, "", "nonsense"):
            self.assertEqual(mp.normalize_turn_limit(asked, competitive=False), 0, asked)

    def test_the_rungs_are_two_then_fives(self):
        self.assertEqual(mp.TURN_LIMIT_CHOICES[0], 2)
        self.assertTrue(all(c % 5 == 0 for c in mp.TURN_LIMIT_CHOICES[1:]))
        self.assertEqual(mp.TURN_LIMIT_CHOICES[1], 5)

    def test_every_rung_survives_itself(self):
        for c in mp.TURN_LIMIT_CHOICES:
            self.assertEqual(mp.normalize_turn_limit(c, competitive=False), c)

    def test_anything_between_the_rungs_snaps_to_one(self):
        self.assertEqual(mp.normalize_turn_limit(1, competitive=False), 2)
        self.assertEqual(mp.normalize_turn_limit(3, competitive=False), 2)
        self.assertEqual(mp.normalize_turn_limit(4, competitive=False), 5)
        self.assertEqual(mp.normalize_turn_limit(7, competitive=False), 5)
        self.assertEqual(mp.normalize_turn_limit(8, competitive=False), 10)
        self.assertEqual(mp.normalize_turn_limit(9999, competitive=False),
                         max(mp.TURN_LIMIT_CHOICES))

    def test_a_competitive_room_is_never_off_the_clock(self):
        self.assertEqual(mp.normalize_turn_limit(0, competitive=True),
                         mp.TURN_LIMIT_COMP_DEFAULT)
        self.assertEqual(mp.normalize_turn_limit(None, competitive=True),
                         mp.TURN_LIMIT_COMP_DEFAULT)

    def test_a_competitive_room_can_go_faster_but_never_slower(self):
        self.assertEqual(mp.normalize_turn_limit(2, competitive=True), 2)
        self.assertEqual(mp.normalize_turn_limit(5, competitive=True), 5)
        self.assertEqual(mp.normalize_turn_limit(10, competitive=True), 10)
        for asked in (15, 30, 60, 3600):
            self.assertEqual(mp.normalize_turn_limit(asked, competitive=True),
                             mp.TURN_LIMIT_COMP_MAX, asked)


class TestTheRoomKeepsIt(unittest.TestCase):
    """What a room is built with, what it tells its players, and what it comes
    back as after the process that was running it restarted."""

    @staticmethod
    def _room(room_id, **kw):
        mp.ROOMS.rooms.pop(room_id, None)
        return mp.GameRoom(room_id, "Otter", total_players=2, human_players=2,
                           ai_players=0, **kw)

    def test_a_casual_room_has_no_clock_unless_asked(self):
        self.assertEqual(self._room("TLAA").turn_limit_seconds, 0)
        self.assertEqual(self._room("TLBB", turn_limit_seconds=15).turn_limit_seconds, 15)

    def test_both_competitive_modes_always_have_one(self):
        self.assertEqual(self._room("TLCC", competitive=True).turn_limit_seconds, 10)
        self.assertEqual(self._room("TLDD", ranked=True).turn_limit_seconds, 10)
        self.assertEqual(
            self._room("TLEE", ranked=True, turn_limit_seconds=45).turn_limit_seconds, 10)
        self.assertEqual(
            self._room("TLFF", competitive=True, turn_limit_seconds=2).turn_limit_seconds, 2)

    def test_create_room_passes_it_through(self):
        mp.ROOMS.rooms.pop("TLGG", None)
        room = mp.ROOMS.create_room("Otter", 2, 2, 0, requested_room_id="TLGG",
                                    turn_limit_seconds=25)
        try:
            self.assertEqual(room.turn_limit_seconds, 25)
        finally:
            mp.ROOMS.rooms.pop("TLGG", None)

    def test_a_restart_brings_the_clock_back(self):
        room = self._room("TLHH", turn_limit_seconds=20)
        with room.cond:
            payload = room._serialize_checkpoint_locked()
        self.assertEqual(payload["turn_limit_seconds"], 20)
        back = mp.GameRoom.from_checkpoint(payload)
        self.assertIsNotNone(back)
        self.assertEqual(back.turn_limit_seconds, 20)

    def test_a_checkpoint_written_before_the_clock_existed_still_loads(self):
        room = self._room("TLII")
        with room.cond:
            payload = room._serialize_checkpoint_locked()
        payload.pop("turn_limit_seconds", None)
        back = mp.GameRoom.from_checkpoint(payload)
        self.assertIsNotNone(back)
        self.assertEqual(back.turn_limit_seconds, 0)

    def test_a_restored_competitive_room_cannot_come_back_off_the_clock(self):
        room = self._room("TLJJ", competitive=True)
        with room.cond:
            payload = room._serialize_checkpoint_locked()
        payload["turn_limit_seconds"] = 0          # a hand-edited state file
        back = mp.GameRoom.from_checkpoint(payload)
        self.assertEqual(back.turn_limit_seconds, mp.TURN_LIMIT_COMP_DEFAULT)

    def test_the_lobby_and_the_browser_both_say_so(self):
        mp.ROOMS.rooms.pop("TLKK", None)
        room = mp.ROOMS.create_room("Otter", 2, 2, 0, requested_room_id="TLKK",
                                    turn_limit_seconds=30)
        try:
            listed = [r for r in mp.ROOMS.list_open_rooms() if r["room_id"] == "TLKK"]
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["turn_limit_seconds"], 30)
            view = room.state_view(room.host_seat().token, "127.0.0.1", "")
            self.assertEqual(view["room"]["turn_limit_seconds"], 30)
        finally:
            mp.ROOMS.rooms.pop("TLKK", None)

    def test_a_room_off_the_clock_sends_no_timer(self):
        room = self._room("TLLL")
        view = room.state_view(room.host_seat().token, "127.0.0.1", "")
        self.assertIsNone(view["turn_timer"])


class TestTheClockMovesTheGameOn(unittest.TestCase):
    """The part that matters. A real two-player match on a two-second clock,
    with nobody touching it."""

    LIMIT = 2

    @classmethod
    def setUpClass(cls):
        mp.ROOMS.rooms.pop("TLRUN", None)
        cls.room = mp.GameRoom("TLRUN", "Otter", total_players=2, human_players=2,
                               ai_players=0, turn_limit_seconds=cls.LIMIT)
        cls.tokens = {0: cls.room.host_seat().token}
        res = cls.room.claim_seat("Heron", 1, None)
        assert res["ok"], res
        cls.tokens[1] = res["seat_token"]
        cls.room.ai_speed = "fast"
        started = cls.room.start_game(cls.room.host_control_token, cls.tokens[0], mp.CARD_DB)
        assert started["ok"], started

    @classmethod
    def tearDownClass(cls):
        with cls.room.cond:
            cls.room.phase = "ended"
            cls.room.cond.notify_all()
        mp.ROOMS.rooms.pop("TLRUN", None)

    @staticmethod
    def _wait_for_active_seat(room, timeout=20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with room.cond:
                active = room.active_action_seat
                if active is not None and room.legal_actions_by_seat.get(active):
                    return active
                if room.phase != "running":
                    return None
            time.sleep(0.01)
        raise AssertionError("no seat became active within %.0fs" % timeout)

    def test_01_a_live_turn_publishes_a_deadline_the_browser_can_draw(self):
        seat = self._wait_for_active_seat(self.room)
        self.assertIsNotNone(seat)
        view = self.room.state_view(self.tokens[seat], "127.0.0.1", "")
        timer = view["turn_timer"]
        self.assertIsNotNone(timer, "a clocked turn sends no timer")
        self.assertEqual(timer["seat"], seat)
        self.assertEqual(timer["limit"], self.LIMIT)
        # The deadline is read against server_now_ms, so it has to be ahead of
        # it and by no more than the whole limit.
        ahead_ms = timer["deadline_ms"] - view["server_now_ms"]
        self.assertGreater(ahead_ms, 0)
        self.assertLessEqual(ahead_ms, self.LIMIT * 1000 + 50)

    def test_02_the_turns_go_round_with_nobody_playing(self):
        start = self._wait_for_active_seat(self.room)
        self.assertIsNotNone(start)
        seen = set()
        turns = set()
        deadline = time.monotonic() + (self.LIMIT * 6) + 12
        while time.monotonic() < deadline and len(turns) < 4:
            with self.room.cond:
                if self.room.phase != "running":
                    break
                if self.room.active_action_seat is not None:
                    seen.add(self.room.active_action_seat)
                turns.add(self.room.last_turn_number)
            time.sleep(0.05)
        self.assertGreaterEqual(
            len(turns), 4,
            "a two-second clock did not pass the turn by itself (turns seen: %s)" % sorted(turns))
        self.assertEqual(seen, {0, 1}, "the clock only ever passed to one seat: %s" % seen)

    def test_03_it_says_in_the_log_what_it_did(self):
        with self.room.cond:
            log = list(self.room.log_events)
        hits = [line for line in log if "turn limit" in line]
        self.assertTrue(hits, "running out of time is not written into the game log")
        self.assertIn("%ds turn limit" % self.LIMIT, hits[0])

    def test_04_running_out_of_time_never_costs_the_turns_draw(self):
        # The player keeps the two cards the turn owes them and loses only what
        # they did not get around to playing: the same bargain the AFK vote
        # already makes. An empty hand after several auto-passed turns would
        # mean the clock was ending turns without drawing.
        with self.room.cond:
            hands = {idx: len(cards) for idx, cards in self.room.latest_private_hands.items()}
        self.assertTrue(hands, "no hands were published at all")
        self.assertTrue(all(n > 0 for n in hands.values()),
                        "a clocked-out player was left with no cards: %s" % hands)


if __name__ == "__main__":
    unittest.main(verbosity=2)
