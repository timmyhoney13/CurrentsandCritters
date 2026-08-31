#!/usr/bin/env python3
"""Quick Play: the queue counter, and the bot table that ends the search.

Run:  python3 -m unittest test_quick_play_fallback -v

Quick Play used to be able to fail and nothing else. You pressed it, the server
parked you alone in a four-seat room, the client polled forever, and the only
exit was Cancel. On a small playerbase that is what happens EVERY time, so the
button could only ever waste your time. Two things fix it and both are pinned
here:

  1. The bar can say how many people are actually in the queue with you, so
     "nobody is here" is information instead of a guess. The count has to obey
     the same staleness rule matchmaking itself obeys, or it advertises closed
     tabs as opponents and the number on screen is a number you cannot match
     with.

  2. After QUICK_PLAY_BOT_FALLBACK_SECONDS the client hands the room to bots and
     plays. The dangerous moment is the handoff: a second human claiming the
     last open seat while that request is in flight must NOT be turned into a
     bot and lose the game they just joined. So the conversion refuses whenever
     more than one person is seated, and says so in a way the client can act on
     (matched=True), rather than silently botting a real player.
"""

import os
import shutil
import tempfile
import time
import unittest

import multiplayer_server as mp


_SANDBOX = {}


def setUpModule():
    """Rooms checkpoint themselves to disk; keep that out of the shipped dirs."""
    tmp = tempfile.mkdtemp(prefix="cc-quickplay-test-")
    _SANDBOX["dir"] = tmp
    _SANDBOX["saved"] = {"ROOM_STATE_DIR": mp.ROOM_STATE_DIR}
    mp.ROOM_STATE_DIR = os.path.join(tmp, "state")
    os.makedirs(mp.ROOM_STATE_DIR, exist_ok=True)


def tearDownModule():
    for key, value in _SANDBOX.get("saved", {}).items():
        setattr(mp, key, value)
    shutil.rmtree(_SANDBOX.get("dir", ""), ignore_errors=True)


def fresh_manager():
    """A RoomManager with nothing in it. The module-level ROOMS is shared with
    every other test in the process, so each case gets its own."""
    return mp.RoomManager()


def ticket(name):
    # The server's own format check: 8-96 of [A-Za-z0-9_-].
    return f"qm_test_{name}_00000000"


class QueueCount(unittest.TestCase):
    def test_one_searcher_counts_as_one(self):
        rooms = fresh_manager()
        self.assertEqual(rooms.quick_play_queue_size(), 0)
        out = rooms.quick_play_join("Solo", ticket("a"))
        self.assertTrue(out["ok"])
        self.assertFalse(out["matched"])
        self.assertEqual(rooms.quick_play_queue_size(), 1)

    def test_the_same_ticket_retrying_is_still_one_person(self):
        """A dropped reply makes the client repost the same ticket. That must
        return the original seat, and must not inflate the queue."""
        rooms = fresh_manager()
        first = rooms.quick_play_join("Solo", ticket("a"))
        again = rooms.quick_play_join("Solo", ticket("a"))
        self.assertEqual(first["seat_token"], again["seat_token"])
        self.assertEqual(rooms.quick_play_queue_size(), 1)

    def test_two_searchers_match_and_count_as_two(self):
        rooms = fresh_manager()
        rooms.quick_play_join("One", ticket("a"))
        second = rooms.quick_play_join("Two", ticket("b"))
        self.assertTrue(second["matched"])
        self.assertEqual(second["human_seats_filled"], 2)
        self.assertEqual(rooms.quick_play_queue_size(), 2)

    def test_a_closed_tab_is_not_an_opponent(self):
        """The count uses the same staleness window matchmaking uses. A player
        whose tab has been shut for longer than that is not somebody you can be
        matched with, so showing them as queued would be a lie."""
        rooms = fresh_manager()
        rooms.quick_play_join("Ghost", ticket("a"))
        room = next(iter(rooms.rooms.values()))
        with room.cond:
            for seat in room.seats:
                if seat.token:
                    seat.last_seen = time.time() - mp.QUICK_PLAY_STALE_SECONDS - 5
        self.assertEqual(rooms.quick_play_queue_size(), 0)

    def test_a_started_game_is_not_a_queue(self):
        """Only lobby-phase rooms are queues. Once a table is playing, the
        people at it are not waiting for anyone."""
        rooms = fresh_manager()
        rooms.quick_play_join("Solo", ticket("a"))
        room = next(iter(rooms.rooms.values()))
        with room.cond:
            room.phase = "playing"
        self.assertEqual(rooms.quick_play_queue_size(), 0)


class BotFallback(unittest.TestCase):
    def _solo_room(self):
        rooms = fresh_manager()
        out = rooms.quick_play_join("Solo", ticket("a"))
        room = rooms.get(out["room_id"])
        return rooms, room, out

    def test_the_search_ends_in_a_real_game(self):
        rooms, room, out = self._solo_room()
        res = room.quick_play_fill_with_bots(out["host_token"], out["seat_token"], mp.CARD_DB)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res["human_players"], 1)
        self.assertEqual(res["ai_players"], 3)
        with room.cond:
            self.assertNotEqual(room.phase, "lobby")
            kinds = [s.kind for s in room.seats]
            self.assertEqual(kinds.count("human"), 1)
            self.assertEqual(kinds.count("ai"), 3)
            # The one human seat is still the player's, with their token.
            human = [s for s in room.seats if s.kind == "human"][0]
            self.assertEqual(human.token, out["seat_token"])
            self.assertEqual(
                sorted(s.claimed_name for s in room.seats if s.kind == "ai"),
                ["Bot 1", "Bot 2", "Bot 3"],
            )

    def test_a_player_who_just_joined_is_never_botted(self):
        """THE race this endpoint exists to lose safely. Somebody claims the
        last seat while the give-up request is in flight; converting now would
        delete a real person from the game they are sitting in."""
        rooms, room, out = self._solo_room()
        rooms.quick_play_join("Latecomer", ticket("b"))
        res = room.quick_play_fill_with_bots(out["host_token"], out["seat_token"], mp.CARD_DB)
        self.assertFalse(res.get("ok"))
        # The client reads this to abandon the bot plan and open the lobby.
        self.assertTrue(res.get("matched"))
        self.assertEqual(res.get("human_seats_filled"), 2)
        with room.cond:
            self.assertEqual(room.phase, "lobby")
            self.assertEqual(sum(1 for s in room.seats if s.kind == "ai"), 0)

    def test_a_stranger_cannot_end_someone_elses_search(self):
        rooms, room, out = self._solo_room()
        res = room.quick_play_fill_with_bots("not-the-host-token", None, mp.CARD_DB)
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("error"), "host authorization required")
        with room.cond:
            self.assertEqual(room.phase, "lobby")

    def test_it_refuses_an_already_running_game(self):
        rooms, room, out = self._solo_room()
        first = room.quick_play_fill_with_bots(out["host_token"], out["seat_token"], mp.CARD_DB)
        self.assertTrue(first.get("ok"), first)
        second = room.quick_play_fill_with_bots(out["host_token"], out["seat_token"], mp.CARD_DB)
        self.assertFalse(second.get("ok"))

    def test_it_refuses_an_ordinary_room(self):
        """This bypasses the normal all-seats-claimed rule, so it must only ever
        apply to a Quick Play room the player is alone in."""
        room = mp.GameRoom("QPX1", "Host", 4, 4, 0)
        host = room.host_seat()
        res = room.quick_play_fill_with_bots(room.host_control_token, host.token, mp.CARD_DB)
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("error"), "this is not a Quick Play room")

    def test_the_room_leaves_the_queue_once_it_is_playing(self):
        """A later searcher must never be dropped into the bot game that just
        started without them."""
        rooms, room, out = self._solo_room()
        room.quick_play_fill_with_bots(out["host_token"], out["seat_token"], mp.CARD_DB)
        self.assertEqual(rooms.quick_play_queue_size(), 0)
        later = rooms.quick_play_join("Later", ticket("b"))
        self.assertTrue(later["ok"])
        self.assertNotEqual(later["room_id"], out["room_id"])
        self.assertFalse(later["matched"])


class TheFallbackWindow(unittest.TestCase):
    def test_the_wait_is_bounded_and_sane(self):
        """The client is told this number rather than hardcoding its own, so it
        has to exist and be a wait a person will actually sit through."""
        self.assertIsInstance(mp.QUICK_PLAY_BOT_FALLBACK_SECONDS, int)
        self.assertGreaterEqual(mp.QUICK_PLAY_BOT_FALLBACK_SECONDS, 10)
        self.assertLessEqual(mp.QUICK_PLAY_BOT_FALLBACK_SECONDS, 120)

    def test_giving_up_happens_before_the_queue_goes_stale(self):
        """If the fallback outlived the staleness window, a searcher would be
        dropped from other people's queue counts while still waiting."""
        self.assertLess(mp.QUICK_PLAY_BOT_FALLBACK_SECONDS, mp.QUICK_PLAY_STALE_SECONDS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
