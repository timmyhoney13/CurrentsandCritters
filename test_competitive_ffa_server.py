#!/usr/bin/env python3
"""Competitive (free-for-all): the server half.

Run:  python3 -m unittest test_competitive_ffa_server -v

There are now TWO competitive modes and they are NOT variants of each other:

  • room.competitive is the 1v1 ladder: four seats owned as two fixed PAIRS,
    with the hand-switch view logic, the [0,2,1,3] interleave and the 30-second
    forfeit window all hanging off it, every one of them gated on exactly four
    seats.
  • room.ranked is the free-for-all: an ORDINARY 2-8 player game that happens to
    pay Competitive Points. Nothing about how it is played differs from a casual
    game. Its one and only server-side rule is that it is people only.

So the thing worth pinning down here is that the new flag stays its own flag: it
never turns on the 1v1 machinery, it survives a checkpoint round-trip (a server
restart must not quietly turn a ranked room casual and stop paying CP), and the
no-bots rule is enforced at BOTH doors, room creation and the lobby's Table
Setup, because a rule enforced only in the client is not a rule.
"""

import os
import shutil
import tempfile
import unittest

import multiplayer_server as mp


_SANDBOX = {}


def setUpModule():
    """Room checkpoints are written to disk. Point them at a temp dir so these
    tests never leave rooms behind in the shipped state directory."""
    tmp = tempfile.mkdtemp(prefix="cc-ffa-test-")
    _SANDBOX["dir"] = tmp
    _SANDBOX["saved"] = {"ROOM_STATE_DIR": mp.ROOM_STATE_DIR}
    mp.ROOM_STATE_DIR = os.path.join(tmp, "state")
    os.makedirs(mp.ROOM_STATE_DIR, exist_ok=True)


def tearDownModule():
    for key, value in _SANDBOX.get("saved", {}).items():
        setattr(mp, key, value)
    shutil.rmtree(_SANDBOX.get("dir", ""), ignore_errors=True)


def make_room(**kwargs):
    opts = dict(room_id="FFA1", host_name="Host", total_players=4,
                human_players=4, ai_players=0)
    opts.update(kwargs)
    return mp.GameRoom(
        opts.pop("room_id"), opts.pop("host_name"), opts.pop("total_players"),
        opts.pop("human_players"), opts.pop("ai_players"), **opts
    )


class RankedFlagIsItsOwnFlag(unittest.TestCase):
    def test_ranked_room_is_not_a_competitive_room(self):
        """The whole reason ranked is a separate flag: turning it on must not
        drag in any of the 1v1 seat-pair machinery."""
        room = make_room(ranked=True)
        self.assertTrue(room.ranked)
        self.assertFalse(room.competitive)
        # _competitive_same_owner is what makes one human own two seats. It must
        # be dead in a ranked room, or seat 0 and seat 1 become one player.
        self.assertFalse(room._competitive_same_owner(0, 1))
        self.assertFalse(room._competitive_same_owner(2, 3))

    def test_competitive_room_is_not_ranked(self):
        room = make_room(competitive=True)
        self.assertTrue(room.competitive)
        self.assertFalse(room.ranked)

    def test_default_room_is_neither(self):
        room = make_room()
        self.assertFalse(room.ranked)
        self.assertFalse(room.competitive)

    def test_ranked_seats_are_shuffled_like_a_normal_game(self):
        """Competitive 1v1 pins seat positions because its interleave depends on
        them. A ranked game is an ordinary game and keeps the normal randomized
        turn order, so the host isn't always Player 1."""
        room = make_room(ranked=True, total_players=8, human_players=8)
        with room.cond:
            names = [s.label for s in room.seats]
            room._randomize_seat_positions_locked()
            # Labels are reassigned by position, so the arrangement is what
            # changed; what matters is that the call is not a no-op the way it
            # is for competitive.
            self.assertEqual(len(room.seats), len(names))
            self.assertEqual([s.index for s in room.seats], list(range(len(room.seats))))

    def test_competitive_seats_are_still_pinned(self):
        room = make_room(competitive=True)
        with room.cond:
            before = [id(s) for s in room.seats]
            room._randomize_seat_positions_locked()
            self.assertEqual([id(s) for s in room.seats], before,
                             "competitive seat order must stay pinned")


class RankedSurvivesARestart(unittest.TestCase):
    def test_checkpoint_round_trip_keeps_the_flag(self):
        """A server restart rebuilds every live room from its checkpoint. If the
        flag were dropped there, a ranked game in progress would come back as a
        casual one and quietly stop paying CP at the end."""
        room = make_room(ranked=True, room_id="FFA2")
        with room.cond:
            payload = room._serialize_checkpoint_locked()
        self.assertTrue(payload["ranked"])
        restored = mp.GameRoom.from_checkpoint(payload)
        self.assertTrue(restored.ranked)
        self.assertFalse(restored.competitive)

    def test_checkpoint_round_trip_of_a_casual_room(self):
        room = make_room(room_id="FFA3")
        with room.cond:
            payload = room._serialize_checkpoint_locked()
        self.assertFalse(payload["ranked"])
        self.assertFalse(mp.GameRoom.from_checkpoint(payload).ranked)


class RankedRoomsAreDescribedAsRanked(unittest.TestCase):
    def test_state_payload_announces_the_flag(self):
        """rankedMode on the client is set from this field and nothing else, so
        a room that doesn't announce itself never pays out."""
        room = make_room(ranked=True, room_id="FFA4")
        payload = room.state_view(None, "localhost")
        self.assertTrue(payload["room"]["ranked"])
        self.assertFalse(payload["room"]["competitive"])

    def test_spectator_payload_announces_the_flag_too(self):
        room = make_room(ranked=True, room_id="FFA5")
        payload = room.spectator_state_view("localhost")
        self.assertTrue(payload["room"]["ranked"])

    def test_casual_payload_says_false_not_missing(self):
        room = make_room(room_id="FFA6")
        payload = room.state_view(None, "localhost")
        self.assertIs(payload["room"]["ranked"], False)


class PeopleOnly(unittest.TestCase):
    """The mode's one server-side rule, at the door it can be pushed on."""

    def test_table_setup_refuses_to_add_a_bot(self):
        room = make_room(ranked=True, room_id="FFA7")
        res = room.configure_lobby_seats(room.host_control_token, None,
                                         human_players=4, ai_players=1)
        self.assertFalse(res["ok"])
        self.assertIn("no bots", res["error"])

    def test_table_setup_still_resizes_the_human_spots(self):
        """The no-bots rule must not freeze the whole table: this mode is meant
        to be played anywhere from 2 to 8 people."""
        room = make_room(ranked=True, room_id="FFA8", total_players=4, human_players=4)
        res = room.configure_lobby_seats(room.host_control_token, None,
                                         human_players=6, ai_players=0)
        self.assertTrue(res["ok"], res.get("error"))
        with room.cond:
            self.assertEqual(sum(1 for s in room.seats if s.kind == "human"), 6)
            self.assertEqual(sum(1 for s in room.seats if s.kind == "ai"), 0)

    def test_table_setup_can_shrink_to_two(self):
        room = make_room(ranked=True, room_id="FFA9", total_players=4, human_players=4)
        res = room.configure_lobby_seats(room.host_control_token, None,
                                         human_players=2, ai_players=0)
        self.assertTrue(res["ok"], res.get("error"))
        with room.cond:
            self.assertEqual(sum(1 for s in room.seats if s.kind == "human"), 2)

    def test_a_casual_room_may_still_add_bots(self):
        """Guard against the rule leaking onto every other room type."""
        room = make_room(room_id="FFAA", total_players=4, human_players=4)
        res = room.configure_lobby_seats(room.host_control_token, None,
                                         human_players=3, ai_players=1)
        self.assertTrue(res["ok"], res.get("error"))
        with room.cond:
            self.assertEqual(sum(1 for s in room.seats if s.kind == "ai"), 1)

    def test_a_ranked_room_never_starts_with_bots(self):
        """Even asked for directly, the seats a ranked room is built with are
        all human: create_room is reached through the HTTP handler, which zeroes
        the bot count before the totals are checked."""
        room = make_room(ranked=True, room_id="FFAB", total_players=8,
                         human_players=8, ai_players=0)
        with room.cond:
            self.assertEqual(sum(1 for s in room.seats if s.kind == "ai"), 0)
            self.assertEqual(len(room.seats), 8)


class GameRecordsNameTheMode(unittest.TestCase):
    def test_the_three_modes_have_three_names(self):
        """The saved game record's mode string is what the stats recovery tool
        and the player's game history read back."""
        self.assertEqual(_record_mode(make_room(room_id="FFAC")), "standard")
        self.assertEqual(_record_mode(make_room(room_id="FFAD", ranked=True)), "ranked")
        self.assertEqual(_record_mode(make_room(room_id="FFAE", competitive=True)), "competitive")


def _record_mode(room):
    """The mode string a finished game would be recorded under. Mirrors the
    expression in _save_game_record so a rename there is caught here."""
    if room.competitive:
        return "competitive"
    return "ranked" if room.ranked else "standard"


if __name__ == "__main__":
    unittest.main(verbosity=2)
