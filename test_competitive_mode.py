#!/usr/bin/env python3
"""Competitive mode, end to end against a REAL GameRoom.

Run:  python3 -m unittest test_competitive_mode -v

Competitive is the odd one out: four seats, but only TWO physical players, each
owning a fixed PAIR of hands (P1 = {0,1}, P2 = {2,3}) that the engine interleaves
0 → 2 → 1 → 3. Almost every competitive bug has the same shape — code that treats
a seat as a player, so the player's OTHER hand is left behind:

  • the turn reaches your second hand and the client never follows it there;
  • Play Again: two players press it, four seats need to be ready, nobody ever
    starts (stuck on "2/4 ready…" forever);
  • you leave and your second hand stays in the room as a seat nobody is in;
  • Surf's Up parks one hand while the other keeps being asked to play.

These tests start a real match on the real engine thread and drive it through the
real submit_action / state_view / play_again / leave_room / set_away APIs.
"""

import os
import shutil
import tempfile
import time
import unittest

import multiplayer_server as mp


TURN_ORDER = [0, 2, 1, 3]  # P1 hand 1, P2 hand 1, P1 hand 2, P2 hand 2

_SANDBOX = {}


def setUpModule():
    """These tests run REAL matches, and a real match writes: a training record,
    a game-history file and a competitive game record. Point all three at a temp
    dir so throwaway test games never land in the shipped dataset."""
    tmp = tempfile.mkdtemp(prefix="cc-comp-test-")
    _SANDBOX["dir"] = tmp
    _SANDBOX["saved"] = {
        "DATASET_PATH": mp.DATASET_PATH,
        "GAMES_HISTORY_DIR": mp.GAMES_HISTORY_DIR,
        "GAMES_LEADERBOARD_PATH": mp.GAMES_LEADERBOARD_PATH,
        "COMPETITIVE_GAMES_DIR": mp.COMPETITIVE_GAMES_DIR,
    }
    mp.DATASET_PATH = os.path.join(tmp, "human_game_dataset.jsonl")
    mp.GAMES_HISTORY_DIR = os.path.join(tmp, "games_history")
    mp.GAMES_LEADERBOARD_PATH = os.path.join(mp.GAMES_HISTORY_DIR, "leaderboard.json")
    mp.COMPETITIVE_GAMES_DIR = os.path.join(tmp, "competitive_games")


def tearDownModule():
    for name, value in _SANDBOX.get("saved", {}).items():
        setattr(mp, name, value)
    shutil.rmtree(_SANDBOX.get("dir", ""), ignore_errors=True)


def _build_room(room_id):
    """A competitive room with both players seated, exactly as the client sets it
    up: P1 creates and claims 0+1, P2 joins and claims 2+3."""
    mp.ROOMS.rooms.pop(room_id, None)
    room = mp.GameRoom(room_id, "Otter", total_players=4, human_players=4,
                       ai_players=0, competitive=True)
    tokens = {0: room.host_seat().token}
    for idx, name in ((1, "Otter 2"), (2, "Heron"), (3, "Heron 2")):
        res = room.claim_seat(name, idx, None)
        assert res["ok"], res
        tokens[idx] = res["seat_token"]
    return room, tokens


def _start(room, tokens):
    room.ai_speed = "fast"
    res = room.start_game(room.host_control_token, tokens[0], mp.CARD_DB)
    assert res["ok"], res


def _wait_for_active_seat(room, timeout=20.0):
    """Block until some seat is waiting on human input; return (seat, payload)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with room.cond:
            active = room.active_action_seat
            legal = room.legal_actions_by_seat.get(active) if active is not None else None
            if active is not None and legal:
                return active, legal
            if room.phase != "running":
                return None, None
        time.sleep(0.005)
    raise AssertionError("no seat became active within %.0fs" % timeout)


def _pick_action(legal):
    """End the turn if we can, otherwise draw, otherwise take anything legal."""
    actions = legal.get("actions", [])
    for want in ("end_turn", "draw"):
        for i, a in enumerate(actions):
            if a.get("kind") == want:
                return i
    return 0 if actions else None


def _play_one_turn(room, tokens, seat, legal, token_seat=None):
    """Submit actions for `seat` until the turn moves on. `token_seat` lets a
    test send the OTHER hand's token (what a client one poll behind does)."""
    token = tokens[seat if token_seat is None else token_seat]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        idx = _pick_action(legal)
        if idx is None:
            break
        res = room.submit_action({"seat_token": token, "action_index": idx,
                                  "request_id": "t-%f" % time.monotonic()})
        if not res.get("ok"):
            return res
        t0 = time.monotonic()
        while time.monotonic() - t0 < 5:
            with room.cond:
                if room.active_action_seat != seat:
                    return {"ok": True}
                cur = room.legal_actions_by_seat.get(seat)
                if cur is not legal:
                    legal = cur
                    break
            time.sleep(0.005)
        if legal is None:
            break
    return {"ok": True}


class CompetitiveTurnOrder(unittest.TestCase):
    """A real match: the turn must reach BOTH of each player's hands."""

    @classmethod
    def setUpClass(cls):
        cls.room, cls.tokens = _build_room("CMPT01")
        _start(cls.room, cls.tokens)
        cls.seen = []
        cls.views = {}
        cls.views_opp_turn = {}
        for _ in range(6):
            seat, legal = _wait_for_active_seat(cls.room)
            if seat is None:
                break
            if not cls.seen or cls.seen[-1] != seat:
                cls.seen.append(seat)
                # Snapshot what all four tokens see at this exact turn boundary.
                if len(cls.seen) == 2:  # seat 2 — P1 has just finished hand 1
                    cls.views_opp_turn = {
                        t: cls.room.state_view(cls.tokens[t], "localhost")
                        for t in (0, 1, 2, 3)
                    }
                if len(cls.seen) == 3:  # seat 1 — P1's SECOND hand
                    cls.views = {
                        t: cls.room.state_view(cls.tokens[t], "localhost")
                        for t in (0, 1, 2, 3)
                    }
            _play_one_turn(cls.room, cls.tokens, seat, legal)

    @classmethod
    def tearDownClass(cls):
        with cls.room.cond:
            cls.room.phase = "ended"
            cls.room.cond.notify_all()
        mp.ROOMS.rooms.pop(cls.room.room_id, None)

    def test_hands_interleave_p1_p2_p1_p2(self):
        self.assertEqual(self.seen[:5], TURN_ORDER + [0],
                         "competitive must interleave the four hands 0→2→1→3")

    def test_engine_to_seat_map_matches_turn_order(self):
        self.assertEqual(self.room._comp_game_to_seat,
                         {gi: si for gi, si in enumerate(TURN_ORDER)})

    def test_second_hand_gets_a_real_turn_of_its_own(self):
        """The turn is on P1's hand 2 (seat 1). EITHER of P1's tokens must return
        that hand's view — its YOUR TURN, its legal actions, its cards.

        The client used to have to notice the turn had moved and re-poll with the
        other hand's token. That is a race, and losing it froze the match on hand
        1 while the banner said hand 2 was up. The switch happens here now, so a
        client that polls with hand 1's token — or has only that token left after
        a refresh — still gets hand 2 the moment it is hand 2's turn."""
        self.assertTrue(self.views, "never reached P1's second hand")
        versions = {v["version"] for v in self.views.values()}
        self.assertEqual(len(versions), 1,
                         "all four tokens must be reading the same state version")

        can_act = {t: self.views[t]["viewer"]["can_act"] for t in (0, 1, 2, 3)}
        self.assertEqual(can_act, {0: True, 1: True, 2: False, 3: False},
                         "both of the active player's tokens act for the active hand; "
                         "neither of the opponent's does")

        for t in (0, 1):
            legal = self.views[t]["legal_actions"]
            self.assertIsNotNone(legal, "hand 2 must be offered its legal actions")
            self.assertGreater(len(legal["actions"]), 0)

        # Whichever hand a payload is a view OF, it carries that hand's cards and
        # nobody else's — an opponent's token can never pull a rival hand. P2 is
        # waiting here, so both of P2's tokens show P2's NEXT hand (seat 3).
        for t, shown in ((0, 1), (1, 1), (2, 3), (3, 3)):
            players = self.views[t]["state"]["players"]
            mine = next(p for p in players if p["index"] == shown)
            self.assertGreater(len(mine["hand"]), 0,
                               f"seat {t}'s poll must carry seat {shown}'s hand")
            for p in players:
                if p["index"] != shown:
                    self.assertEqual(p["hand"], [], "no payload may carry a second hand")

    def test_viewer_seat_index_is_the_hand_being_viewed(self):
        """The hand the turn is on when it is mine (seat 1 here), and otherwise
        the hand of mine that is up NEXT (seat 3 for the waiting player)."""
        for t, shown in ((0, 1), (1, 1), (2, 3), (3, 3)):
            self.assertEqual(self.views[t]["viewer"]["seat_index"], shown)

    def test_the_opponents_view_never_follows_my_turn(self):
        """The switch is strictly within one person's own pair: while P1 plays,
        P2's screen sits on one of P2's OWN hands and can't act with it."""
        for t in (2, 3):
            self.assertFalse(self.views[t]["viewer"]["can_act"])
            self.assertIn(self.views[t]["viewer"]["seat_index"], (2, 3))

    def test_my_view_moves_to_my_other_hand_as_soon_as_my_turn_ends(self):
        """P1 has just finished hand 1 and the opponent is playing. P1's screen
        must ALREADY be on hand 2 — the hand they play next.

        It used to sit on the hand they had just played until the opponent
        finished their turn, so the switch ran on the opponent's clock: you
        stared at a board you were done with, then it changed under you the
        moment the turn came back. The wait is now the time you spend planning
        the hand that is actually up next."""
        self.assertTrue(self.views_opp_turn, "never reached the opponent's turn")
        self.assertEqual(self.views_opp_turn[0]["active_action_seat"], 2,
                         "the snapshot must be taken on the opponent's turn")
        for t in (0, 1):
            view = self.views_opp_turn[t]
            self.assertEqual(view["viewer"]["seat_index"], 1,
                             "either of P1's tokens must be shown P1's next hand")
            self.assertFalse(view["viewer"]["can_act"],
                             "showing the next hand early is not a free turn with it")
            players = view["state"]["players"]
            mine = next(p for p in players if p["index"] == 1)
            self.assertGreater(len(mine["hand"]), 0,
                               "hand 2's own cards must be the ones on screen")
            for p in players:
                if p["index"] != 1:
                    self.assertEqual(p["hand"], [], "no payload may carry a second hand")

    def test_the_hand_on_screen_is_never_the_opponents(self):
        """Every switch stays inside the viewer's own pair, at every moment
        sampled — showing early must never hand anyone a rival's cards."""
        for views in (self.views_opp_turn, self.views):
            for t in (0, 1):
                self.assertIn(views[t]["viewer"]["seat_index"], (0, 1))
            for t in (2, 3):
                self.assertIn(views[t]["viewer"]["seat_index"], (2, 3))


class CompetitiveSeatOwnership(unittest.TestCase):
    """One human, two seats: their tokens are interchangeable for THEIR hands
    and useless for the opponent's."""

    def setUp(self):
        self.room, self.tokens = _build_room("CMPT02")
        _start(self.room, self.tokens)
        self.seat, self.legal = _wait_for_active_seat(self.room)

    def tearDown(self):
        with self.room.cond:
            self.room.phase = "ended"
            self.room.cond.notify_all()
        mp.ROOMS.rooms.pop(self.room.room_id, None)

    def test_first_turn_belongs_to_p1_hand_1(self):
        self.assertEqual(self.seat, 0)

    def test_my_other_hands_token_can_act_for_the_active_hand(self):
        """A client one poll behind sends the wrong hand's token. It is still the
        same person, so the server acts as the ACTIVE seat rather than refusing."""
        res = self.room.submit_action({
            "seat_token": self.tokens[1],  # hand 2's token, hand 1's turn
            "action_index": _pick_action(self.legal),
            "request_id": "lagging-client",
        })
        self.assertTrue(res.get("ok"), res)
        with self.room.cond:
            self.assertIn(0, self.room.pending_actions,
                          "the action must be queued for the ACTIVE seat, not the token's seat")

    def test_the_opponents_token_is_refused(self):
        for opp in (2, 3):
            res = self.room.submit_action({
                "seat_token": self.tokens[opp],
                "action_index": _pick_action(self.legal),
                "request_id": "opponent-%d" % opp,
            })
            self.assertFalse(res.get("ok"))
            self.assertEqual(res.get("error"), "not your turn")

    def test_owned_seats_are_the_players_pair(self):
        seats = {s.index for s in self.room._owned_seats_locked(self.room.seats[0])}
        self.assertEqual(seats, {0, 1})
        seats = {s.index for s in self.room._owned_seats_locked(self.room.seats[3])}
        self.assertEqual(seats, {2, 3})

    def test_surf_up_takes_both_of_a_players_hands_away(self):
        out = self.room.set_away({"seat_token": self.tokens[0], "away": True})
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(self.room.seats[0].is_away)
        self.assertTrue(self.room.seats[1].is_away,
                        "a player who stepped away cannot still be playing hand 2")
        self.assertFalse(self.room.seats[2].is_away, "the opponent is unaffected")
        self.assertFalse(self.room.seats[3].is_away)
        # …and an Away player cannot act with either hand.
        res = self.room.submit_action({"seat_token": self.tokens[0], "action_index": 0,
                                       "request_id": "away-1"})
        self.assertFalse(res.get("ok"))
        self.assertIn("Surf's Up", res.get("error", ""))
        # Coming back restores both hands.
        self.room.set_away({"seat_token": self.tokens[1], "away": False})
        self.assertFalse(self.room.seats[0].is_away)
        self.assertFalse(self.room.seats[1].is_away)

    def test_surf_up_announces_the_player_not_the_hand(self):
        self.room.set_away({"seat_token": self.tokens[1], "away": True})
        self.assertIn("Otter is on Surf's Up", self.room.status_note)
        self.assertNotIn("Otter 2", self.room.status_note)

    def test_one_token_is_enough_to_see_the_active_hand(self):
        """What a client has after a refresh: one seat token and no idea the
        room is competitive. It must still be shown the hand whose turn it is —
        here hand 2's token polling on hand 1's turn."""
        view = self.room.state_view(self.tokens[1], "localhost")
        self.assertEqual(view["viewer"]["seat_index"], 0)
        self.assertTrue(view["viewer"]["can_act"])
        self.assertIsNotNone(view["legal_actions"])
        players = view["state"]["players"]
        self.assertGreater(len(next(p for p in players if p["index"] == 0)["hand"]), 0)

    def test_polling_either_hand_keeps_the_whole_player_present(self):
        """One poll means the PERSON is here — a competitive forfeit must never
        fire because only one of their two hands did the polling."""
        for seat in self.room.seats:
            seat.last_seen = 0.0
        self.room.state_view(self.tokens[0], "localhost")
        self.assertGreater(self.room.seats[1].last_seen, 0.0,
                           "hand 2 must count as present when hand 1 polls")
        self.assertEqual(self.room.seats[2].last_seen, 0.0,
                         "the opponent's presence is their own to prove")

    def test_afk_votes_are_counted_per_player_not_per_hand(self):
        """Four seats, two people. Counting seats gave the opponent two of the
        three ballots against you — a majority they hold alone, every turn — and
        listed your own other hand as a voter against you."""
        voters = self.room._afk_eligible_voter_indices_locked(0)
        self.assertEqual(voters, [2],
                         "one ballot per person: the opponent, once, and never my own hand")

    def test_your_own_other_hand_cannot_report_you_afk(self):
        self.room.submit_chat({"seat_token": self.tokens[1], "message": "Otter is afk"})
        self.assertIsNone(self.room.afk_challenge_seat,
                          "a player must not be able to AFK-vote themselves through hand 2")

    def test_either_of_the_opponents_hands_casts_the_one_vote(self):
        """P2 voting with hand 2 (seat 3) still counts — the eligible list holds
        only seat 2, so the vote has to be matched by OWNER, not seat index."""
        self.room.submit_chat({"seat_token": self.tokens[3], "message": "Otter is afk"})
        self.assertEqual(self.room.afk_challenge_seat, 0,
                         "the opponent's second hand casts their side's ballot")
        # …and their first hand cannot then vote a second time.
        self.room.afk_challenge_seat = None
        self.room.afk_challenge_deadline = None
        self.room.submit_chat({"seat_token": self.tokens[2], "message": "Otter is afk"})
        self.assertIsNone(self.room.afk_challenge_seat,
                          "one ballot per person, not one per hand")

    def test_cannot_draw_two_cards_for_your_own_other_hand(self):
        self.room.seats[0].inactive_eligible = True
        res = self.room.draw_for_inactive({"seat_token": self.tokens[1],
                                           "target_seat_index": 0})
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("error"), "cannot draw for yourself")


class CompetitivePostGame(unittest.TestCase):
    """Play Again and leaving both count PLAYERS, not seats."""

    def setUp(self):
        self.room, self.tokens = _build_room("CMPT03")
        self.room.phase = "ended"
        self.room.game_thread = None

    def tearDown(self):
        mp.ROOMS.rooms.pop(self.room.room_id, None)

    def test_one_press_per_player_starts_the_rematch(self):
        """The client presses Play Again once, with compMySeats[0]'s token."""
        first = self.room.play_again(self.tokens[0], mp.CARD_DB)
        self.assertTrue(first["ok"])
        self.assertFalse(first["started"], "one player readying must not start the game")
        self.assertTrue(self.room.seats[1].play_again_ready,
                        "readying up readies BOTH of that player's hands")
        self.assertFalse(self.room.seats[2].play_again_ready)

        second = self.room.play_again(self.tokens[2], mp.CARD_DB)
        self.assertTrue(second["started"],
                        "both players ready → the rematch must start (was stuck at 2/4)")
        self.assertEqual(self.room.phase, "running")
        with self.room.cond:
            self.room.phase = "ended"
            self.room.cond.notify_all()

    def test_leaving_after_the_game_takes_both_hands(self):
        out = self.room.leave_room({"seat_token": self.tokens[2]}, mp.CARD_DB)
        self.assertTrue(out["ok"])
        for idx in (2, 3):
            self.assertIsNone(self.room.seats[idx].token,
                              f"seat {idx} left behind as a ghost hand")
            self.assertIsNone(self.room.seats[idx].claimed_name)
        self.assertEqual({s.index for s in self.room._active_human_seats_locked()}, {0, 1})
        self.assertEqual(self.room.post_game_left, ["Heron"],
                         "the PLAYER left once, not two separate hands")

    def test_the_remaining_player_can_still_start_a_rematch_alone(self):
        self.room.leave_room({"seat_token": self.tokens[2]}, mp.CARD_DB)
        res = self.room.play_again(self.tokens[0], mp.CARD_DB)
        self.assertTrue(res["started"],
                        "with the leaver's ghost hand gone, the last player readies 2/2")
        with self.room.cond:
            self.room.phase = "ended"
            self.room.cond.notify_all()

    def test_leaving_mid_game_reserves_both_hands_for_rejoin(self):
        self.room.phase = "running"
        out = self.room.leave_room({"seat_token": self.tokens[2]}, mp.CARD_DB)
        self.assertTrue(out["ok"])
        self.assertTrue(out.get("reserved"))
        for idx in (2, 3):
            self.assertIsNotNone(self.room.seats[idx].left_at,
                                 f"seat {idx} must be held for the rejoining player")
            self.assertIsNotNone(self.room.seats[idx].token, "the rejoin key is kept")
        for idx in (0, 1):
            self.assertIsNone(self.room.seats[idx].left_at)


class CompetitiveResultRecording(unittest.TestCase):
    """The saved match result must be scored by SEAT, never by display name.

    A player can rename a hand mid-match. The standings carry the names the
    engine started with, so a name lookup silently scores the renamed hand 0 —
    which can record the winner as the loser. Every row already carries its seat.
    """

    def setUp(self):
        self.room, self.tokens = _build_room("CMPT05")
        self.dir = tempfile.mkdtemp(prefix="cc-rec-")
        self._saved_dir = mp.COMPETITIVE_GAMES_DIR
        mp.COMPETITIVE_GAMES_DIR = self.dir

    def tearDown(self):
        mp.COMPETITIVE_GAMES_DIR = self._saved_dir
        shutil.rmtree(self.dir, ignore_errors=True)
        mp.ROOMS.rooms.pop(self.room.room_id, None)

    def _record(self):
        import json
        files = [f for f in os.listdir(self.dir) if f.endswith(".json")]
        self.assertEqual(len(files), 1, f"expected one game record, got {files}")
        with open(os.path.join(self.dir, files[0])) as fh:
            return json.load(fh)

    def test_finished_game_scores_each_side_after_a_rename(self):
        class _GS:
            players = []
            round_count = 7
        class _MS:
            end_game_triggered = True

        # Standings as the engine tallies them: the names it STARTED with, each
        # row tagged with the seat that earned it. P2's hand 1 (seat 2) wins.
        standings = [
            {"name": "Heron",   "score": 90, "seat_index": 2},
            {"name": "Otter",   "score": 60, "seat_index": 0},
            {"name": "Heron 2", "score": 40, "seat_index": 3},
            {"name": "Otter 2", "score": 20, "seat_index": 1},
        ]
        # …and now both players rename their hands mid-match.
        self.room.seats[0].claimed_name = "Otter the Third"
        self.room.seats[1].claimed_name = "Otter's Left Deck"
        self.room.seats[2].claimed_name = "Heron Reborn"
        self.room.seats[3].claimed_name = "Heron's Spare"

        self.room._save_competitive_game(_GS(), _MS(), standings)
        rec = self._record()
        self.assertEqual(rec["p1_best_score"], 60)
        self.assertEqual(rec["p2_best_score"], 90)
        self.assertEqual(rec["p1_second_score"], 20)
        self.assertEqual(rec["p2_second_score"], 40)
        self.assertEqual(rec["winner"], "Heron Reborn",
                         "the higher-scoring side must win even after renaming")
        self.assertFalse(rec["is_draw"])

    def test_forfeit_scores_the_best_hand_of_each_side_by_seat(self):
        self.room.latest_public_state = {"players": [
            {"index": 0, "name": "Otter",   "score": 31},
            {"index": 1, "name": "Otter 2", "score": 55},   # P1's better hand
            {"index": 2, "name": "Heron",   "score": 12},
            {"index": 3, "name": "Heron 2", "score": 7},
        ]}
        self.room.seats[0].claimed_name = "Otter Renamed"
        self.room._save_competitive_forfeit("Otter Renamed", "Heron")
        rec = self._record()
        self.assertTrue(rec["forfeit"])
        self.assertEqual(rec["p1_best_score"], 55, "a side scores its BEST hand")
        self.assertEqual(rec["p2_best_score"], 12)
        self.assertEqual(rec["winner"], "Otter Renamed")
        self.assertEqual(rec["loser"], "Heron")
        by_name = {r["name"]: r["score"] for r in rec["standings"]}
        self.assertEqual(by_name["Otter Renamed"], 55)
        self.assertEqual(by_name["Heron"], 12)


class NonCompetitiveUnaffected(unittest.TestCase):
    """The seat-pair rules must apply ONLY to competitive rooms."""

    def setUp(self):
        mp.ROOMS.rooms.pop("CMPT04", None)
        self.room = mp.GameRoom("CMPT04", "Otter", total_players=4,
                                human_players=4, ai_players=0)
        self.tokens = {0: self.room.host_seat().token}
        for idx, name in ((1, "Heron"), (2, "Gull"), (3, "Tern")):
            self.tokens[idx] = self.room.claim_seat(name, idx, None)["seat_token"]

    def tearDown(self):
        mp.ROOMS.rooms.pop("CMPT04", None)

    def test_a_casual_seat_owns_only_itself(self):
        for idx in range(4):
            owned = self.room._owned_seats_locked(self.room.seats[idx])
            self.assertEqual([s.index for s in owned], [idx])

    def test_casual_play_again_readies_only_the_presser(self):
        self.room.phase = "ended"
        self.room.game_thread = None
        self.room.play_again(self.tokens[0], mp.CARD_DB)
        self.assertTrue(self.room.seats[0].play_again_ready)
        self.assertFalse(any(self.room.seats[i].play_again_ready for i in (1, 2, 3)))

    def test_casual_leave_frees_only_that_seat(self):
        self.room.phase = "ended"
        self.room.game_thread = None
        self.room.leave_room({"seat_token": self.tokens[1]}, mp.CARD_DB)
        self.assertIsNone(self.room.seats[1].token)
        for idx in (0, 2, 3):
            self.assertIsNotNone(self.room.seats[idx].token)

    def test_casual_surf_up_affects_only_that_seat(self):
        self.room.set_away({"seat_token": self.tokens[0], "away": True})
        self.assertTrue(self.room.seats[0].is_away)
        self.assertFalse(any(self.room.seats[i].is_away for i in (1, 2, 3)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
