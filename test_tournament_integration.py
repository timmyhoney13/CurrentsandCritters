#!/usr/bin/env python3
"""Integration tests for the tournament <-> GameRoom bridge in multiplayer_server.

Verifies the audit fixes that live in the adapter/notify hook (not the pure
tournament_server logic): private pre-claimed match rooms, no ghost seat, and
result mapping by UNIQUE seat name so duplicate display names can't collapse.

Run:  python3 -m unittest test_tournament_integration -v
"""

import unittest

import multiplayer_server as ms
import tournament_server as ts


def _cleanup(room_id):
    try:
        ms.ROOMS.rooms.pop(room_id, None)
    except Exception:
        pass


class TestMatchRoomAdapter(unittest.TestCase):
    def test_private_room_all_seats_preclaimed_no_ghost(self):
        players = [
            {"pid": "acct:a", "name": "Alice", "token": "t1", "is_guest": False},
            {"pid": "acct:b", "name": "Bob", "token": "t2", "is_guest": False},
            {"pid": "acct:c", "name": "Cara", "token": "t3", "is_guest": False},
            {"pid": "acct:d", "name": "Dan", "token": "t4", "is_guest": False},
        ]
        res = ms._tournament_create_match_room(
            tournament_id="TID1", round_index=0, match_index=0, match_number=1, players=players)
        room = ms.ROOMS.get(res["room_id"])
        try:
            human = [s for s in room.seats if s.kind == "human"]
            self.assertEqual(len(human), 4)
            self.assertEqual(room.visibility, "private")
            # every seat pre-claimed (no open seat for an outsider, no ghost host)
            self.assertTrue(all(s.claimed_name and s.token for s in human),
                            "all match seats must be pre-claimed")
            # seat tokens returned for exactly the assigned pids
            self.assertEqual(set(res["seat_tokens"].keys()), {"acct:a", "acct:b", "acct:c", "acct:d"})
            # no two seats share a token
            toks = [s.token for s in human]
            self.assertEqual(len(toks), len(set(toks)))
        finally:
            _cleanup(res["room_id"])

    def test_duplicate_names_disambiguated_and_mapped_by_pid(self):
        # Two players with the SAME display name must still map to distinct pids.
        players = [
            {"pid": "acct:alice", "name": "Otter", "token": "t1", "is_guest": False},
            {"pid": "acct:bob", "name": "Otter", "token": "t2", "is_guest": False},
        ]
        res = ms._tournament_create_match_room(
            tournament_id="TID2", round_index=0, match_index=0, match_number=1, players=players)
        room = ms.ROOMS.get(res["room_id"])
        try:
            human = [s for s in room.seats if s.kind == "human"]
            names = sorted(s.claimed_name for s in human)
            self.assertEqual(names, ["Otter", "Otter (2)"], "duplicate names must be disambiguated")

            # simulate the game ending with bob (the second 'Otter') winning
            room.final_scores = [{"name": "Otter (2)", "score": 150},
                                 {"name": "Otter", "score": 100}]
            captured = {}
            orig = ts.on_room_ended
            ts.on_room_ended = lambda rid, ranking, scores: (captured.update(ranking=ranking, scores=scores) or {"ok": True})
            try:
                ms._notify_tournament_if_match(room)
            finally:
                ts.on_room_ended = orig
            # winner mapped to the correct pid despite identical display names
            self.assertEqual(captured["ranking"][0], "acct:bob", captured)
            self.assertEqual(captured["ranking"][1], "acct:alice", captured)
        finally:
            _cleanup(res["room_id"])

    def test_missing_player_ranked_last(self):
        players = [
            {"pid": "acct:x", "name": "Xor", "token": "t1", "is_guest": False},
            {"pid": "acct:y", "name": "Yara", "token": "t2", "is_guest": False},
        ]
        res = ms._tournament_create_match_room(
            tournament_id="TID3", round_index=0, match_index=0, match_number=1, players=players)
        room = ms.ROOMS.get(res["room_id"])
        try:
            # only one player shows in standings (the other never played)
            room.final_scores = [{"name": "Yara", "score": 120}]
            captured = {}
            orig = ts.on_room_ended
            ts.on_room_ended = lambda rid, ranking, scores: (captured.update(ranking=ranking) or {"ok": True})
            try:
                ms._notify_tournament_if_match(room)
            finally:
                ts.on_room_ended = orig
            self.assertEqual(captured["ranking"][0], "acct:y")
            self.assertIn("acct:x", captured["ranking"])          # still resolved (ranked last)
            self.assertEqual(captured["ranking"][-1], "acct:x")
        finally:
            _cleanup(res["room_id"])


def _report(room):
    """Run the game-end hook and return the ranking it fed the tournament."""
    captured = {}
    orig = ts.on_room_ended
    ts.on_room_ended = lambda rid, ranking, scores: (
        captured.update(ranking=ranking, scores=scores) or {"ok": True})
    try:
        ms._notify_tournament_if_match(room)
    finally:
        ts.on_room_ended = orig
    return captured


class TestResultsMapBySeatNotName(unittest.TestCase):
    """The bug that knocked a winner out of their own tournament.

    Standings used to be tied back to bracket participants by DISPLAY NAME. The
    game renames a seat to whatever name the reconnecting client sends, and a
    client deep-linked straight into its match room can easily send a different
    one (nickname changed mid-tournament, a duplicate suffixed at pre-claim, or a
    fresh page load where the profile hasn't resolved and it falls back to
    "Player"). The renamed player then matched nothing in the standings and got
    re-appended LAST: the match winner was recorded as the loser, knocked out of
    a bracket they had actually won, after which every remaining match was
    bot-only and the tournament crowned a champion in seconds.
    """

    def test_winner_still_wins_after_the_client_renames_its_seat(self):
        players = [
            {"pid": "acct:win", "name": "Timmy", "token": "t1", "is_guest": False, "is_bot": False},
            {"pid": "bot:1", "name": "Barracuda 🤖", "token": "t2", "is_guest": True, "is_bot": True},
        ]
        res = ms._tournament_create_match_room(
            tournament_id="TID4", round_index=0, match_index=0, match_number=1, players=players)
        room = ms.ROOMS.get(res["room_id"])
        try:
            # The real client flow: reconnect with the delivered seat token. Here the
            # nickname hasn't resolved yet, so it claims as the default "Player".
            claim = room.claim_seat("Player", None, res["seat_tokens"]["acct:win"])
            self.assertTrue(claim["ok"])
            seat = next(s for s in room.seats if s.tournament_pid == "acct:win")
            self.assertEqual(seat.claimed_name, "Timmy",
                             "a tournament seat keeps the name the bracket knows it by")

            room.final_scores = [{"name": seat.claimed_name, "score": 90, "seat_index": seat.index},
                                 {"name": "Barracuda 🤖", "score": 10,
                                  "seat_index": next(s.index for s in room.seats
                                                     if s.tournament_pid == "bot:1")}]
            cap = _report(room)
            self.assertEqual(cap["ranking"][0], "acct:win",
                             "the player with the top score must be recorded as the winner")
        finally:
            _cleanup(res["room_id"])

    def test_mapping_survives_the_launch_time_seat_shuffle(self):
        players = [
            {"pid": "acct:a", "name": "Ann", "token": "t1", "is_guest": False, "is_bot": False},
            {"pid": "acct:b", "name": "Bo", "token": "t2", "is_guest": False, "is_bot": False},
            {"pid": "bot:z", "name": "Narwhal 🤖", "token": "t3", "is_guest": True, "is_bot": True},
        ]
        res = ms._tournament_create_match_room(
            tournament_id="TID5", round_index=0, match_index=0, match_number=1, players=players)
        room = ms.ROOMS.get(res["room_id"])
        try:
            with room.cond:
                room._randomize_seat_positions_locked()   # what every launch does
            by_pid = {s.tournament_pid: s for s in room.seats}
            self.assertEqual(len(by_pid), 3, "every seat still knows its participant")
            room.final_scores = [
                {"name": by_pid["acct:b"].claimed_name, "score": 77,
                 "seat_index": by_pid["acct:b"].index},
                {"name": by_pid["bot:z"].claimed_name, "score": 55,
                 "seat_index": by_pid["bot:z"].index},
                {"name": by_pid["acct:a"].claimed_name, "score": 12,
                 "seat_index": by_pid["acct:a"].index},
            ]
            cap = _report(room)
            self.assertEqual(cap["ranking"], ["acct:b", "bot:z", "acct:a"])
        finally:
            _cleanup(res["room_id"])

    def test_name_only_standings_still_map(self):
        """Older rooms (restored from disk mid-game) have no seat_index on their
        standings rows; the name map must still resolve them."""
        players = [
            {"pid": "acct:p", "name": "Pia", "token": "t1", "is_guest": False, "is_bot": False},
            {"pid": "acct:q", "name": "Quin", "token": "t2", "is_guest": False, "is_bot": False},
        ]
        res = ms._tournament_create_match_room(
            tournament_id="TID6", round_index=0, match_index=0, match_number=1, players=players)
        room = ms.ROOMS.get(res["room_id"])
        try:
            room.final_scores = [{"name": "Quin", "score": 30}, {"name": "Pia", "score": 20}]
            cap = _report(room)
            self.assertEqual(cap["ranking"], ["acct:q", "acct:p"])
        finally:
            _cleanup(res["room_id"])


class TestAllBotMatchRoom(unittest.TestCase):
    """An all-AI bracket match is a real game in a real room, so no round is ever
    decided without being played."""

    def test_all_bot_match_gets_a_startable_room(self):
        players = [{"pid": f"bot:{i}", "name": f"Critter{i}", "token": "",
                    "is_guest": True, "is_bot": True} for i in range(4)]
        res = ms._tournament_create_match_room(
            tournament_id="TID7", round_index=0, match_index=0, match_number=1, players=players)
        room = ms.ROOMS.get(res["room_id"])
        try:
            self.assertEqual(len(room.seats), 4)
            self.assertTrue(all(s.kind == "ai" for s in room.seats), "no human seat to wait on")
            self.assertEqual(res["seat_tokens"], {}, "nobody to hand a seat token to")
            self.assertIsNotNone(room.host_seat(), "host seat still resolves")
            with room.cond:
                self.assertTrue(room._all_humans_claimed_locked(),
                                "an all-AI match room must be startable")
            self.assertEqual(sorted(s.tournament_pid for s in room.seats),
                             [f"bot:{i}" for i in range(4)])
            # unwatched all-AI game: no artificial think pause holding the bracket up
            self.assertTrue(room._is_unwatched_all_ai_locked_free())
            room.spectator_join("Nosy")
            self.assertFalse(room._is_unwatched_all_ai_locked_free(),
                             "a spectator restores watchable pacing")
        finally:
            _cleanup(res["room_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
