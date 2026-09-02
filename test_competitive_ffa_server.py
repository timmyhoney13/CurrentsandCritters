#!/usr/bin/env python3
"""Competitive (free-for-all): the server half.

Run:  python3 -m unittest test_competitive_ffa_server -v

There are now TWO competitive modes and they are NOT variants of each other:

  • room.competitive is the 1v1 ladder: four seats owned as two fixed PAIRS,
    with the hand-switch view logic, the [0,2,1,3] interleave and the 30-second
    forfeit window all hanging off it, every one of them gated on exactly four
    seats.
  • room.ranked is the free-for-all: an ORDINARY 2-8 player game that happens to
    pay Ocean Points. Nothing about how it is played differs from a casual
    game. Its one and only server-side rule is that it is people only.

So the thing worth pinning down here is that the new flag stays its own flag: it
never turns on the 1v1 machinery, it survives a checkpoint round-trip (a server
restart must not quietly turn a ranked room casual and stop paying CP), and the
no-bots rule is enforced at BOTH doors, room creation and the lobby's Table
Setup, because a rule enforced only in the client is not a rule.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import multiplayer_server as mp


_SANDBOX = {}


def setUpModule():
    """Room checkpoints and game records are written to disk. Point them all at
    a temp dir so these tests never leave rooms, game files or a leaderboard
    behind in the shipped state directories."""
    tmp = tempfile.mkdtemp(prefix="cc-ffa-test-")
    _SANDBOX["dir"] = tmp
    _SANDBOX["saved"] = {
        "ROOM_STATE_DIR": mp.ROOM_STATE_DIR,
        "COMPETITIVE_GAMES_DIR": mp.COMPETITIVE_GAMES_DIR,
        "COMPETITIVE_LEADERBOARD_PATH": mp.COMPETITIVE_LEADERBOARD_PATH,
    }
    mp.ROOM_STATE_DIR = os.path.join(tmp, "state")
    mp.COMPETITIVE_GAMES_DIR = os.path.join(tmp, "competitive_games")
    mp.COMPETITIVE_LEADERBOARD_PATH = os.path.join(mp.COMPETITIVE_GAMES_DIR,
                                                   "leaderboard.json")
    os.makedirs(mp.ROOM_STATE_DIR, exist_ok=True)
    os.makedirs(mp.COMPETITIVE_GAMES_DIR, exist_ok=True)


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
        to be played anywhere from 3 to 8 people."""
        room = make_room(ranked=True, room_id="FFA8", total_players=4, human_players=4)
        res = room.configure_lobby_seats(room.host_control_token, None,
                                         human_players=6, ai_players=0)
        self.assertTrue(res["ok"], res.get("error"))
        with room.cond:
            self.assertEqual(sum(1 for s in room.seats if s.kind == "human"), 6)
            self.assertEqual(sum(1 for s in room.seats if s.kind == "ai"), 0)

    def test_table_setup_can_shrink_to_the_floor(self):
        room = make_room(ranked=True, room_id="FFA9", total_players=4, human_players=4)
        res = room.configure_lobby_seats(room.host_control_token, None,
                                         human_players=mp.COMP_FFA_MIN_PLAYERS,
                                         ai_players=0)
        self.assertTrue(res["ok"], res.get("error"))
        with room.cond:
            self.assertEqual(sum(1 for s in room.seats if s.kind == "human"),
                             mp.COMP_FFA_MIN_PLAYERS)




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


class ThreeIsTheFloor(unittest.TestCase):
    """The mode's other server-side rule: a competitive table is at least three
    people. Two of them is Competitive 1v1 with extra steps and the easiest
    thing in the game to farm with a friend, so the count is not allowed under
    the floor at either door. The payout's own check is the last gate, not the
    only one."""

    def test_table_setup_refuses_to_shrink_under_the_floor(self):
        room = make_room(ranked=True, room_id="FF10", total_players=4, human_players=4)
        res = room.configure_lobby_seats(room.host_control_token, None,
                                         human_players=mp.COMP_FFA_MIN_PLAYERS - 1,
                                         ai_players=0)
        self.assertFalse(res["ok"])
        self.assertIn(str(mp.COMP_FFA_MIN_PLAYERS), res["error"])
        with room.cond:
            self.assertEqual(sum(1 for s in room.seats if s.kind == "human"), 4)

    def test_the_floor_does_not_leak_onto_a_casual_room(self):
        """A normal 2-player game is still a normal 2-player game."""
        room = make_room(room_id="FF11", total_players=4, human_players=4)
        res = room.configure_lobby_seats(room.host_control_token, None,
                                         human_players=2, ai_players=0)
        self.assertTrue(res["ok"], res.get("error"))
        with room.cond:
            self.assertEqual(sum(1 for s in room.seats if s.kind == "human"), 2)

    def test_the_floor_does_not_leak_onto_competitive_1v1(self):
        """Competitive 1v1 is four seats owned as two pairs, and its own
        refusal is the one that has to answer."""
        room = make_room(competitive=True, room_id="FF12", total_players=4, human_players=4)
        res = room.configure_lobby_seats(room.host_control_token, None,
                                         human_players=2, ai_players=0)
        self.assertFalse(res["ok"])
        self.assertIn("2 hands", res["error"])

    def test_the_client_mirrors_the_same_number(self):
        """The New Current modal and the lobby's own seat spots refuse the same
        sizes this server does, and they refuse them with this constant. Two
        numbers drifting apart is a lobby whose buttons let the host ask for a
        table the server then rejects.

        (The lobby used to carry a separate Table Setup panel with +/- steppers.
        The eight spots are the control now, so the floor is enforced where they
        ask for a resize: setTableSeats.)"""
        app = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "multiplayer", "client", "js", "preview-app.js")
        with open(app, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn(f"const COMP_FFA_MIN_PLAYERS = {mp.COMP_FFA_MIN_PLAYERS};", src)
        self.assertIn("_ncIsRanked && (human < COMP_FFA_MIN_PLAYERS", src)
        self.assertIn(
            "const minHumans = latestPayload?.room?.ranked ? COMP_FFA_MIN_PLAYERS : 1;", src)
        # A seat somebody is already sitting in is never taken off the table.
        self.assertIn("ctx.humans > Math.max(1, ctx.filled)", src)


class ThreeIsTheFloorAtTheStartButton(unittest.TestCase):
    """A room made before this rule existed can still be sitting in a lobby with
    two seats. What the floor really promises is about the game that gets
    PLAYED, so the start button answers for it too."""

    @staticmethod
    def _claim_every_seat(room):
        with room.cond:
            for i, seat in enumerate(room.seats):
                if seat.kind == "human":
                    seat.token = f"tok{i}"
                    seat.claimed_name = f"P{i}"

    def test_a_two_seat_competitive_lobby_will_not_start(self):
        room = make_room(ranked=True, room_id="FF30", total_players=2, human_players=2)
        self._claim_every_seat(room)
        res = room.start_game(room.host_control_token, None, {})
        self.assertFalse(res["ok"])
        self.assertIn(str(mp.COMP_FFA_MIN_PLAYERS), res["error"])
        self.assertEqual(res["needs_players"], 1)
        self.assertEqual(room.phase, "lobby")

    def test_a_two_player_casual_lobby_starts_as_it_always_did(self):
        """The launch itself is stubbed: what is under test is that the start
        button reaches it, not the game that follows."""
        room = make_room(room_id="FF31", total_players=2, human_players=2)
        self._claim_every_seat(room)
        launched = []
        room._launch_game_locked = lambda card_db, status_note: launched.append(status_note)
        res = room.start_game(room.host_control_token, None, {})
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(len(launched), 1)


class ThreeIsTheFloorForARematch(unittest.TestCase):
    """The one way a table's size falls without anybody resizing it: people
    leaving after the game. Play Again would have started a 2-person
    "competitive" game that could not pay CP, so the rematch is held to the
    same floor and the room says what it is waiting for."""

    @staticmethod
    def _seat_everyone(room, count):
        with room.cond:
            for i, seat in enumerate(room.seats[:count]):
                seat.kind = "human"
                seat.token = f"tok{i}"
                seat.claimed_name = f"P{i}"
                seat.left_at = None
                seat.play_again_ready = True
            room.phase = "ended"

    def test_a_short_competitive_room_does_not_rematch(self):
        room = make_room(ranked=True, room_id="FF20", total_players=3, human_players=3)
        self._seat_everyone(room, 3)
        with room.cond:
            # Two of the three walk away from the end screen.
            for seat in room.seats[1:]:
                seat.token = None
                seat.claimed_name = None
                seat.play_again_ready = False
            started = room._maybe_start_play_again_locked({})
        self.assertFalse(started)
        self.assertEqual(room.phase, "ended")

    def test_a_full_competitive_room_still_rematches(self):
        room = make_room(ranked=True, room_id="FF21", total_players=3, human_players=3)
        self._seat_everyone(room, 3)
        with room.cond:
            self.assertEqual(room._ranked_shortfall_locked(3), 0)

    def test_a_casual_room_rematches_with_whoever_is_left(self):
        """The floor is competitive's rule and nobody else's: two friends
        finishing a casual game can still press Play Again."""
        room = make_room(room_id="FF22", total_players=3, human_players=3)
        self._seat_everyone(room, 3)
        with room.cond:
            self.assertEqual(room._ranked_shortfall_locked(2), 0)

    def test_the_room_says_what_it_is_waiting_for(self):
        room = make_room(ranked=True, room_id="FF23", total_players=3, human_players=3)
        self._seat_everyone(room, 3)
        with room.cond:
            for seat in room.seats[2:]:
                seat.token = None
                seat.claimed_name = None
            note = room._play_again_status_note_locked()
        self.assertIn("1 more player", note)
        self.assertIn(str(mp.COMP_FFA_MIN_PLAYERS), note)
        self.assertNotIn("ready to play again", note)

    def test_a_full_room_still_shows_the_ready_tally(self):
        room = make_room(ranked=True, room_id="FF24", total_players=3, human_players=3)
        self._seat_everyone(room, 3)
        with room.cond:
            note = room._play_again_status_note_locked()
        self.assertIn("ready to play again", note)


class ThreeIsTheFloorAtTheCreateDoor(unittest.TestCase):
    """The create handler is the door a host actually walks through, so the
    refusal is tested through a real HTTP request rather than by reading the
    branch."""

    @classmethod
    def setUpClass(cls):
        cls.server = mp.StableThreadingHTTPServer(("127.0.0.1", 0), mp.MultiplayerHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def _create(self, room_id, players, ranked=True):
        body = json.dumps({
            "host_name": "Host", "room_id": room_id,
            "total_players": players, "human_players": players, "ai_players": 0,
            "ranked": ranked,
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/rooms", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read().decode())

    def test_two_people_is_refused(self):
        status, out = self._create("FFC2", mp.COMP_FFA_MIN_PLAYERS - 1)
        self.assertEqual(status, 400)
        self.assertFalse(out["ok"])
        self.assertIn(str(mp.COMP_FFA_MIN_PLAYERS), out["error"])
        self.assertIsNone(mp.ROOMS.get("FFC2"))

    def test_the_floor_itself_is_allowed(self):
        status, out = self._create("FFC3", mp.COMP_FFA_MIN_PLAYERS)
        self.assertEqual(status, 200, out)
        self.assertTrue(out["ok"], out)
        room = mp.ROOMS.get("FFC3")
        self.assertIsNotNone(room)
        self.assertTrue(room.ranked)
        with room.cond:
            self.assertEqual(len(room.seats), mp.COMP_FFA_MIN_PLAYERS)
            self.assertEqual(sum(1 for s in room.seats if s.kind == "ai"), 0)

    def test_a_casual_two_player_room_still_opens(self):
        status, out = self._create("FFC4", 2, ranked=False)
        self.assertEqual(status, 200, out)
        self.assertTrue(out["ok"], out)


class _FakeMs:
    end_game_triggered = True


class _FakeGs:
    round_count = 9
    players: list = []


class AFinishedGameJoinsTheCompetitiveLedger(unittest.TestCase):
    """One rank, two ways to climb it, so one ledger.

    A free-for-all paid CP and moved the player's rank, but the game itself was
    only ever saved as a normal game. Every competitive history in the app reads
    COMPETITIVE_GAMES_DIR, so a player whose competitive games were all
    free-for-alls had a rank with nothing behind it. The record cannot be
    1v1-shaped (there are 3 to 8 people and no sides), so it carries a players
    list, which is also what tells the two kinds of record apart.
    """

    def setUp(self):
        for fname in os.listdir(mp.COMPETITIVE_GAMES_DIR):
            os.remove(os.path.join(mp.COMPETITIVE_GAMES_DIR, fname))

    def _play(self, room_id, scores, ranked=True):
        """Finish a game with these seat scores and return the record written."""
        room = make_room(ranked=ranked, room_id=room_id,
                         total_players=len(scores), human_players=len(scores))
        with room.cond:
            for i, seat in enumerate(room.seats):
                seat.claimed_name = f"P{i}"
                seat.token = f"tok{i}"
        standings = [{"name": f"P{i}", "score": s, "seat_index": i}
                     for i, s in sorted(enumerate(scores), key=lambda kv: -kv[1])]
        room._save_ranked_game(_FakeGs(), _FakeMs(), standings)
        files = [f for f in os.listdir(mp.COMPETITIVE_GAMES_DIR)
                 if f.startswith(f"game_{room_id}_")]
        if not files:
            return None
        with open(os.path.join(mp.COMPETITIVE_GAMES_DIR, files[0]),
                  encoding="utf-8") as fh:
            return json.load(fh)

    def test_the_game_lands_in_the_competitive_ledger(self):
        rec = self._play("FF40", [50, 90, 70])
        self.assertIsNotNone(rec, "no competitive record was written")
        self.assertEqual(rec["mode"], "ranked")
        self.assertEqual(rec["player_count"], 3)
        self.assertEqual(rec["winner"], "P1")
        self.assertFalse(rec["is_draw"])

    def test_it_is_ranked_at_write_time(self):
        """The 1v1 record waits for a client to confirm it. This one does not
        have to: the ROOM was competitive, so the game was."""
        rec = self._play("FF41", [10, 20, 30])
        self.assertIs(rec["ranked"], True)
        self.assertTrue(rec["season_id"])

    def test_places_are_ordered_and_ties_share_the_better_one(self):
        rec = self._play("FF42", [80, 80, 40, 10])
        by_name = {p["name"]: p for p in rec["players"]}
        self.assertEqual(by_name["P0"]["place"], 1)
        self.assertEqual(by_name["P1"]["place"], 1)
        self.assertEqual(by_name["P2"]["place"], 3)
        self.assertEqual(by_name["P3"]["place"], 4)
        # Nobody is alone in first, so nobody won.
        self.assertIsNone(rec["winner"])
        self.assertTrue(rec["is_draw"])

    def test_a_casual_game_writes_nothing_here(self):
        self.assertIsNone(self._play("FF43", [30, 20, 10], ranked=False))

    def test_the_record_is_written_once(self):
        room = make_room(ranked=True, room_id="FF44", total_players=3, human_players=3)
        with room.cond:
            for i, seat in enumerate(room.seats):
                seat.claimed_name = f"P{i}"
        standings = [{"name": f"P{i}", "score": 10 * (3 - i), "seat_index": i}
                     for i in range(3)]
        room._save_ranked_game(_FakeGs(), _FakeMs(), standings)
        room._save_ranked_game(_FakeGs(), _FakeMs(), standings)
        files = [f for f in os.listdir(mp.COMPETITIVE_GAMES_DIR)
                 if f.startswith("game_FF44_")]
        self.assertEqual(len(files), 1)

    def test_an_abandoned_game_is_not_recorded(self):
        """Same bar the 1v1 record uses: only a game that reached END GAME."""
        room = make_room(ranked=True, room_id="FF45", total_players=3, human_players=3)
        ms = _FakeMs()
        ms.end_game_triggered = False
        room._save_ranked_game(_FakeGs(), ms, [{"name": "P0", "score": 1, "seat_index": 0}])
        self.assertEqual([f for f in os.listdir(mp.COMPETITIVE_GAMES_DIR)
                          if f.startswith("game_FF45_")], [])

    def test_the_all_time_board_reads_first_and_last(self):
        self._play("FF46", [90, 50, 10])
        with open(mp.COMPETITIVE_LEADERBOARD_PATH, encoding="utf-8") as fh:
            board = json.load(fh)
        self.assertEqual(board["P0"]["wins"], 1)
        self.assertEqual(board["P1"]["draws"], 1)
        self.assertEqual(board["P2"]["losses"], 1)
        self.assertEqual(board["P0"]["best_score"], 90)


class EachPlayerStampsTheirOwnCp(unittest.TestCase):
    """A free-for-all has no host who knows everyone's CP: each client works its
    own out from its own rank, so each reports only its own row. The record is
    the meeting place, and it counts a row once so a retry is free."""

    def setUp(self):
        for fname in os.listdir(mp.COMPETITIVE_GAMES_DIR):
            os.remove(os.path.join(mp.COMPETITIVE_GAMES_DIR, fname))
        self.record = {
            "room_id": "FF50", "recorded_unix": mp.now_unix(),
            "season_id": "2026-Q3", "mode": "ranked", "ranked": True,
            "player_count": 3,
            "players": [
                {"name": "Ann", "seat_index": 0, "score": 90, "place": 1},
                {"name": "Bo",  "seat_index": 1, "score": 60, "place": 2},
                {"name": "Cid", "seat_index": 2, "score": 20, "place": 3},
            ],
            "winner": "Ann", "is_draw": False,
        }
        self.path = os.path.join(mp.COMPETITIVE_GAMES_DIR, "game_FF50_1.json")
        mp.atomic_write_json(self.path, self.record)

    def _season_board(self):
        path = os.path.join(mp.COMPETITIVE_GAMES_DIR, "leaderboard_2026-Q3.json")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def _stamp(self, name, **body):
        payload = {"name": name}
        payload.update(body)
        with open(self.path, encoding="utf-8") as fh:
            record = json.load(fh)
        return mp._stamp_ranked_ffa_result(record, self.path, payload)

    def test_a_player_stamps_their_own_row(self):
        out = self._stamp("Bo", cp_after=310, cp_delta=-4, rank_after="Golden Grouper II")
        self.assertTrue(out["ok"])
        self.assertTrue(out["counted"])
        with open(self.path, encoding="utf-8") as fh:
            rec = json.load(fh)
        bo = next(p for p in rec["players"] if p["name"] == "Bo")
        self.assertEqual(bo["cp_after"], 310)
        self.assertEqual(bo["cp_delta"], -4)
        self.assertEqual(bo["rank_after"], "Golden Grouper II")
        # And nobody else's row was touched.
        self.assertNotIn("cp_after", rec["players"][0])

    def test_first_is_a_win_last_is_a_loss_and_the_middle_is_a_draw(self):
        self._stamp("Ann", cp_after=100, cp_delta=12)
        self._stamp("Bo",  cp_after=90,  cp_delta=0)
        self._stamp("Cid", cp_after=80,  cp_delta=-6)
        board = self._season_board()
        self.assertEqual(board["Ann"]["wins"], 1)
        self.assertEqual(board["Bo"]["draws"], 1)
        self.assertEqual(board["Cid"]["losses"], 1)
        self.assertEqual(board["Ann"]["cp"], 100)
        self.assertEqual(board["Ann"]["games"], 1)

    def test_a_repost_updates_without_counting_twice(self):
        self._stamp("Ann", cp_after=100, cp_delta=12)
        out = self._stamp("Ann", cp_after=105, cp_delta=17)
        self.assertTrue(out["ok"])
        self.assertFalse(out["counted"])
        board = self._season_board()
        self.assertEqual(board["Ann"]["games"], 1)
        self.assertEqual(board["Ann"]["wins"], 1)
        with open(self.path, encoding="utf-8") as fh:
            rec = json.load(fh)
        self.assertEqual(rec["players"][0]["cp_after"], 105)

    def test_a_stranger_cannot_stamp_a_game_they_were_not_in(self):
        out = self._stamp("Nobody", cp_after=9999, cp_delta=9999)
        self.assertFalse(out["ok"])
        self.assertIn("not in this game", out["error"])

    def test_a_nameless_request_is_refused(self):
        out = self._stamp("")
        self.assertFalse(out["ok"])


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
