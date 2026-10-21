#!/usr/bin/env python3
"""A lobby that nobody can join is a lobby nobody can leave either.

Run:  python3 -m unittest test_lobby_seat_release -v

A seat is taken when `seat.token` is set, and that token used to be cleared in
exactly four places: an explicit /leave, the rejoin reservation expiring on a
RUNNING game, the host resizing the table, and a kick vote. None of those is
"the person closed the tab", and the client does not fill the gap: its
beforeunload beacon posts /leave only for the Head to Head queue
(`_qmSearch`), never for an ordinary lobby seat. It cannot safely do so
either, because a page REFRESH fires beforeunload exactly like a close, and a
refresh must not cost a player their seat.

So every lobby accumulated seats held by nobody, permanently:

    4-seat lobby, 2 people open it and close the tab, 2 real players join
      → public list:  room is gone (it counts as full)
      → join by code: "no open human seats"
      → every seat:   "seat already taken"
      → and it never recovers: the room janitor only reaps a WHOLE room once
        EVERY seat has been silent for two hours, and the real players keep
        it alive forever.

That is "sometimes it does not let me join games".

The fix is server-side on purpose. beforeunload is unreliable by nature
(mobile tab kill, crash, lost network, refresh) so the server, which can see
that a seat has stopped polling, is the only place that can answer this
reliably.
"""

import os
import shutil
import tempfile
import time
import unittest

import multiplayer_server as mp


_SANDBOX = {}


def setUpModule():
    tmp = tempfile.mkdtemp(prefix="cc-lobby-seat-")
    _SANDBOX["dir"] = tmp
    _SANDBOX["saved"] = {"ROOM_STATE_DIR": mp.ROOM_STATE_DIR}
    mp.ROOM_STATE_DIR = os.path.join(tmp, "state")
    os.makedirs(mp.ROOM_STATE_DIR, exist_ok=True)


def tearDownModule():
    for name, value in _SANDBOX.get("saved", {}).items():
        setattr(mp, name, value)
    shutil.rmtree(_SANDBOX.get("dir", ""), ignore_errors=True)


class IdleLobbySeatsAreReleased(unittest.TestCase):
    def setUp(self):
        self._saved = dict(mp.ROOMS.rooms)
        mp.ROOMS.rooms.clear()

    def tearDown(self):
        mp.ROOMS.rooms.clear()
        mp.ROOMS.rooms.update(self._saved)

    def _lobby(self, rid="LOBBY", humans=4):
        """A lobby whose host already occupies seat 0 (GameRoom.__init__ claims
        it), so only humans-1 more people can sit down."""
        room = mp.GameRoom(rid, "Host", total_players=humans,
                           human_players=humans, ai_players=0)
        mp.ROOMS.rooms[rid] = room
        return room

    def _sit(self, room, name):
        out = room.claim_seat(name, None, None)
        self.assertTrue(out.get("ok"), f"{name} could not sit down: {out}")
        return out

    def _go_quiet(self, room, seat_index, seconds):
        """That seat stopped polling `seconds` ago (tab closed, phone asleep)."""
        with room.cond:
            room.seats[seat_index].last_seen = time.time() - seconds

    def _still_here(self, room, seat_index):
        with room.cond:
            room.seats[seat_index].last_seen = time.time()

    # ── The bug ────────────────────────────────────────────────────────────
    def test_an_abandoned_lobby_seat_becomes_joinable_again(self):
        room = self._lobby()
        self._sit(room, "Ghost1")
        self._sit(room, "Ghost2")
        self._sit(room, "RealPlayer")

        # Host and RealPlayer are here; the two in the middle closed the tab.
        self._still_here(room, 0)
        self._go_quiet(room, 1, mp.LOBBY_SEAT_IDLE_SEC + 30)
        self._go_quiet(room, 2, mp.LOBBY_SEAT_IDLE_SEC + 30)
        self._still_here(room, 3)

        out = room.claim_seat("Timothy", None, None)
        self.assertTrue(out.get("ok"),
                        f"a lobby with two abandoned seats refused a player: {out}")
        self.assertIn(out.get("seat_index"), (1, 2))

    def test_the_room_comes_back_into_the_public_list(self):
        room = self._lobby()
        for nm in ("Ghost1", "Ghost2", "RealPlayer"):
            self._sit(room, nm)
        self._still_here(room, 0)
        self._go_quiet(room, 1, mp.LOBBY_SEAT_IDLE_SEC + 30)
        self._go_quiet(room, 2, mp.LOBBY_SEAT_IDLE_SEC + 30)
        self._still_here(room, 3)

        listed = [r for r in mp.ROOMS.list_open_rooms() if r["room_id"] == "LOBBY"]
        self.assertTrue(listed, "the room was still hidden from the public list")
        self.assertEqual(listed[0]["filled"], 2,
                         "the list still counted the abandoned seats as filled")

    # ── The safety property that matters more than the fix ─────────────────
    def test_a_seat_is_never_taken_from_someone_who_is_still_there(self):
        room = self._lobby()
        got = self._sit(room, "Waiting")
        tok = got["seat_token"]

        # Polling the whole time, just waiting for friends to arrive.
        for _ in range(4):
            self._still_here(room, 1)
            room.release_idle_lobby_seats()
            with room.cond:
                self.assertEqual(room.seats[1].token, tok,
                                 "a seat was taken from a player who was still polling")
                self.assertEqual(room.seats[1].claimed_name, "Waiting")

    def test_a_seat_just_under_the_window_is_kept(self):
        room = self._lobby()
        got = self._sit(room, "Dozing")
        self._go_quiet(room, 1, mp.LOBBY_SEAT_IDLE_SEC - 5)

        room.release_idle_lobby_seats()
        with room.cond:
            self.assertEqual(room.seats[1].token, got["seat_token"])

    def test_a_running_game_is_never_touched(self):
        """Mid-game absence is the rejoin reservation's job, not this one."""
        room = self._lobby(rid="PLAYING")
        got = self._sit(room, "Player2")
        self._sit(room, "Player3")
        self._sit(room, "Player4")
        with room.cond:
            room.phase = "running"
        self._go_quiet(room, 1, mp.LOBBY_SEAT_IDLE_SEC * 10)

        room.release_idle_lobby_seats()
        with room.cond:
            self.assertEqual(room.seats[1].token, got["seat_token"],
                             "an idle seat was freed mid-game, losing that player's hand")

    # ── Housekeeping the release has to get right ──────────────────────────
    def test_the_host_seat_going_quiet_hands_host_to_someone_present(self):
        room = self._lobby()
        self._sit(room, "StillHere")
        self._go_quiet(room, 0, mp.LOBBY_SEAT_IDLE_SEC + 30)
        self._still_here(room, 1)

        room.release_idle_lobby_seats()
        with room.cond:
            self.assertIsNone(room.seats[0].token)
            self.assertFalse(room.seats[0].is_host)
            self.assertTrue(room.seats[1].is_host,
                            "nobody was host, so nobody could start the game")

    def test_a_seat_that_never_polled_at_all_still_expires(self):
        """last_seen must be stamped at claim time, or None reads as 'forever'."""
        room = self._lobby()
        got = self._sit(room, "OpenedAndClosed")
        with room.cond:
            self.assertIsNotNone(room.seats[1].last_seen,
                                 "claiming a seat did not stamp last_seen")
        self._go_quiet(room, 1, mp.LOBBY_SEAT_IDLE_SEC + 30)

        room.release_idle_lobby_seats()
        with room.cond:
            self.assertIsNone(room.seats[1].token)
        self.assertNotEqual(got["seat_token"], None)

    def test_the_window_is_long_enough_not_to_rob_a_backgrounded_tab(self):
        # A hidden tab polls every POLL_MS_HIDDEN = 15s; the window has to be
        # several of those so a phone that dozes briefly keeps its seat.
        self.assertGreaterEqual(mp.LOBBY_SEAT_IDLE_SEC, 90)


if __name__ == "__main__":
    unittest.main(verbosity=2)
