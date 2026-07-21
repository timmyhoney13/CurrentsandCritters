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


if __name__ == "__main__":
    unittest.main(verbosity=2)
