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
        host_token = cls.room.host_seat().token
        res = cls.room.claim_seat("Heron", 1, None)
        assert res["ok"], res
        cls.room.ai_speed = "fast"
        started = cls.room.start_game(cls.room.host_control_token, host_token, mp.CARD_DB)
        assert started["ok"], started
        # Read off the seats, after the start: starting a game can move a
        # player to another chair, so a token claimed at index 1 is not
        # necessarily seat 1's token once the game is running.
        cls.tokens = {s.index: s.token for s in cls.room.seats if s.kind == "human"}

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


class TestTheTwoFreshBudgets(unittest.TestCase):
    """One clock per TURN is the rule, and the game hands out two turns the
    player did not plan for. Both get a clock of their own, and both are
    bounded so neither can hold a table open.

      1. A ★ "play again" says "take another turn" in the banner and in the
         status note. The engine does NOT start a new engine turn for it: it
         adds to the SAME turn's action budget, so the turn-boundary branch
         that arms the clock never fires for one. Left alone, a star earned on
         second nine of a ten-second turn was a star you could not use.
      2. Picking a card UP to play it. Dropping it on a slot is half a play;
         choosing what to pay with is the other half, and it is the half a
         clock must never make for you.

    Both are measured here on a REAL room with a REAL match thread, because
    the clock is only ever armed from inside the live policy loop.
    """

    LIMIT = 30          # long enough that nothing is ever forced mid-test

    @classmethod
    def setUpClass(cls):
        mp.ROOMS.rooms.pop("TLFRESH", None)
        cls.room = mp.GameRoom("TLFRESH", "Otter", total_players=2, human_players=2,
                               ai_players=0, turn_limit_seconds=cls.LIMIT)
        host_token = cls.room.host_seat().token
        res = cls.room.claim_seat("Heron", 1, None)
        assert res["ok"], res
        cls.room.ai_speed = "fast"
        started = cls.room.start_game(cls.room.host_control_token, host_token, mp.CARD_DB)
        assert started["ok"], started
        # AFTER the start, and off the seats themselves. Starting a game can
        # move a player to another chair (the casual order is shuffled every
        # game now), so "the host holds seat 0" is only true about half the
        # time, and a token read before the start belongs to whichever seat
        # that player ended up in, not to the index it was claimed at.
        cls.tokens = {s.index: s.token for s in cls.room.seats if s.kind == "human"}
        assert len(cls.tokens) == 2, cls.tokens

    @classmethod
    def tearDownClass(cls):
        with cls.room.cond:
            cls.room.phase = "ended"
            cls.room.cond.notify_all()
        mp.ROOMS.rooms.pop("TLFRESH", None)

    @staticmethod
    def _wait_for_active_seat(room, timeout=20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with room.cond:
                if (room.active_action_seat is not None
                        and room.legal_actions_by_seat.get(room.active_action_seat)):
                    return room.active_action_seat
                if room.phase != "running":
                    return None
            time.sleep(0.01)
        raise AssertionError("no seat became active within %.0fs" % timeout)

    def _engine_player_for_seat(self, seat_index):
        """The live engine's PlayerState for a seat. The casual turn order is
        shuffled, so game index and seat index are not the same number."""
        with self.room.cond:
            gs = getattr(self.room, "_live_gs", None)
            game_to_seat = dict(getattr(self.room, "_comp_game_to_seat", {}) or {})
        self.assertIsNotNone(gs, "the match thread has published no engine state")
        for game_idx, player in enumerate(gs.players):
            if game_to_seat.get(game_idx, game_idx) == seat_index:
                return player
        raise AssertionError("no engine player maps back to seat %s" % seat_index)

    def test_01_a_star_play_again_is_given_a_turns_worth_of_clock(self):
        seat = self._wait_for_active_seat(self.room)
        self.assertIsNotNone(seat)
        with self.room.cond:
            before = float(self.room._turn_clock_deadline)
            turn_before = self.room.last_turn_number
        # Spend most of the turn, the way a player who thought about it would.
        with self.room.cond:
            self.room._turn_clock_deadline = time.time() + 1.0
            spent = float(self.room._turn_clock_deadline)
        self.assertLess(spent, before, "the test did not actually spend the turn")
        # The engine grants the extra action by setting this flag; the policy
        # pops it on its next pass, which is the moment under test.
        self._engine_player_for_seat(seat).flags["_replay_turn_next"] = True
        # Act, so the policy loop comes round again INSIDE the same turn.
        payload = self.room.legal_actions_by_seat.get(seat) or {}
        acts = payload.get("actions") or []
        idx = next((i for i, a in enumerate(acts) if a.get("kind") == "draw"), None)
        self.assertIsNotNone(idx, "no draw on offer to take: %s" % [a.get("kind") for a in acts])
        out = self.room.submit_action({"seat_token": self.tokens[seat], "action_index": idx,
                                       "kind": "draw", "request_id": "replay-1"})
        self.assertTrue(out.get("ok"), out)
        # Watch until either the clock moves (the extra turn was given its own
        # budget) or the turn number does (it was never given one and the turn
        # simply ended). Whichever happens first is the answer.
        # The two are read together, under the lock, because the question is
        # precisely whether the clock was given a fresh budget WHILE THIS TURN
        # was still running. A deadline that only moves once the turn number
        # does is the next turn's clock, which proves nothing.
        stop = time.monotonic() + 5
        fresh_inside_the_turn = False
        after, turn_now = spent, turn_before
        while time.monotonic() < stop:
            with self.room.cond:
                after = float(self.room._turn_clock_deadline or 0)
                turn_now = self.room.last_turn_number
            if turn_now != turn_before:
                break                       # the turn ended first
            if after > spent + 1.0:
                fresh_inside_the_turn = True
                break
            time.sleep(0.01)
        self.assertTrue(
            fresh_inside_the_turn,
            "a ★ play again was played out on whatever was left of the turn before "
            "it: the clock never re-armed inside the turn "
            "(deadline %.2f -> %.2f, turn %s -> %s)"
            % (spent, after, turn_before, turn_now))
        self.assertLessEqual(after - time.time(), self.LIMIT + 0.5,
                             "the extra turn got MORE than one turn's worth of clock")

    def test_02_picking_a_card_up_buys_one_fresh_budget(self):
        seat = self._wait_for_active_seat(self.room)
        self.assertIsNotNone(seat)
        with self.room.cond:
            self.room._turn_clock_deadline = time.time() + 1.0
            self.room._turn_clock_play_grace_used = False
            spent = float(self.room._turn_clock_deadline)
        out = self.room.reset_turn_clock_for_play({"seat_token": self.tokens[seat]})
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("granted"), "the first pick-up of the turn was refused")
        with self.room.cond:
            after = float(self.room._turn_clock_deadline)
        self.assertGreater(after, spent + 1.0, "the clock was not given a fresh budget")
        self.assertLessEqual(after - time.time(), self.LIMIT + 0.5, "it got more than a turn")
        # …and only once, so picking a card up and putting it down again cannot
        # hold the table open.
        again = self.room.reset_turn_clock_for_play({"seat_token": self.tokens[seat]})
        self.assertTrue(again.get("ok"), again)
        self.assertFalse(again.get("granted"), "a second pick-up bought a second budget")
        with self.room.cond:
            after2 = float(self.room._turn_clock_deadline)
        self.assertAlmostEqual(after, after2, places=3,
                               msg="the refused second pick-up still moved the clock")

    def test_03_it_never_shortens_a_turn(self):
        seat = self._wait_for_active_seat(self.room)
        with self.room.cond:
            far = time.time() + self.LIMIT + 20
            self.room._turn_clock_deadline = far
            self.room._turn_clock_play_grace_used = False
        self.room.reset_turn_clock_for_play({"seat_token": self.tokens[seat]})
        with self.room.cond:
            now = float(self.room._turn_clock_deadline)
        self.assertAlmostEqual(
            now, far, places=3,
            msg="picking a card up took time OFF a turn that had more left")

    def test_04_only_the_seat_on_the_clock_can_ask(self):
        seat = self._wait_for_active_seat(self.room)
        other = 1 - seat
        out = self.room.reset_turn_clock_for_play({"seat_token": self.tokens[other]})
        self.assertFalse(out.get("ok"), out)
        self.assertIn("not your turn", str(out.get("error")))
        self.assertFalse(
            self.room.reset_turn_clock_for_play({"seat_token": "nonsense"}).get("ok"))

    def test_05_a_room_off_the_clock_simply_says_so(self):
        mp.ROOMS.rooms.pop("TLNOCLK", None)
        room = mp.GameRoom("TLNOCLK", "Otter", total_players=2, human_players=2, ai_players=0)
        try:
            room.phase = "running"
            out = room.reset_turn_clock_for_play({"seat_token": room.host_seat().token})
            self.assertTrue(out.get("ok"), out)
            self.assertFalse(out.get("clock"), "a room with no clock claimed to have one")
        finally:
            mp.ROOMS.rooms.pop("TLNOCLK", None)

    def test_06_arming_the_clock_hands_the_pick_up_grace_back(self):
        # A new turn, and a ★ play-again's extra turn, each get their own.
        with self.room.cond:
            self.room._turn_clock_play_grace_used = True
            self.room._arm_turn_clock_locked(0)
            self.assertFalse(self.room._turn_clock_play_grace_used)


if __name__ == "__main__":
    unittest.main(verbosity=2)
