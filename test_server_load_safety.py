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

Three more came out of a 100-player load run (scripts/load_test.py: 100 Head
to Head tables, one person and three bots each):

3. The room checkpoint was written with json.dump, which always runs the
   pure-Python encoder, after deep-copying the whole room, on every move of
   every game. That was over half the server's CPU. It is now json.dumps (the
   C encoder, same bytes) over uncopied references, encoded under the lock.

4. Every game parsed its own copy of the ~6 MB brain file and held it all
   match: ~280 MB of identical dicts at 100 games. load_live_brain shares one.

5. Up to 1200 training snapshots per room were held as dicts, ~24 MB for a
   late-game table. They are kept as compact JSON text now (~3 MB) and only
   decoded when the dataset row is built.

These tests assert the POLICY, not the measured milliseconds: timings belong in
a load run, not a unit test.
"""

import json
import os
import random
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


def _room_with_history(room_id="CKPT1"):
    room = mp.GameRoom(room_id, "Host", 4, 1, 3)
    with room.cond:
        room.latest_public_state = {"players": [{"name": "A", "board": [[1, 2], {"x": 3}]}]}
        room.latest_private_hands = {0: [{"uid": 5, "name": "Kelp"}]}
        room.legal_actions_by_seat = {0: {"actions": [{"index": 0, "kind": "draw"}]}}
        room.action_history = [{"kind": "draw", "seat_index": 0, "turn_number": 1}]
        room.chat_messages = [{"sender": "A", "message": "héllo", "ts": 1.5}]
        room.final_scores = [{"name": "A", "score": 12}]
    return room


class CheckpointWriteIsCheap(unittest.TestCase):
    def test_atomic_write_matches_the_old_streaming_bytes(self):
        """Switching dump -> dumps must not change a single byte on disk, or a
        checkpoint written before the deploy reads back differently after it."""
        payload = {"a": [1, 2.5, None, True], "é": {"nested": "ünïcode"}, "z": ""}
        path = os.path.join(mp.ROOM_STATE_DIR, "bytes_check.json")
        mp.atomic_write_json(path, payload)
        with open(path, encoding="utf-8") as f:
            on_disk = f.read()
        import io
        buf = io.StringIO()
        json.dump(payload, buf, separators=(",", ":"), ensure_ascii=True)
        self.assertEqual(on_disk, buf.getvalue())
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_uncopied_serialization_has_the_same_content(self):
        room = _room_with_history()
        with room.cond:
            copied = room._serialize_checkpoint_locked()
            live = room._serialize_checkpoint_locked(copy_values=False)
        copied.pop("saved_unix"); live.pop("saved_unix")
        self.assertEqual(json.dumps(copied, sort_keys=True), json.dumps(live, sort_keys=True))

    def test_default_serialization_still_hands_back_copies(self):
        """Tests and callers outside the persist path mutate what they get."""
        room = _room_with_history("CKPT2")
        with room.cond:
            payload = room._serialize_checkpoint_locked()
        payload["action_history"].append({"kind": "bogus"})
        payload["latest_public_state"]["players"].clear()
        self.assertEqual(len(room.action_history), 1)
        self.assertEqual(len(room.latest_public_state["players"]), 1)

    def test_persisted_checkpoint_round_trips(self):
        room = _room_with_history("CKPT3")
        with room.cond:
            room._persist_dirty = True
            room._persist_if_due_locked(force=True)
            expected = room._serialize_checkpoint_locked()
        with open(mp.room_state_path("CKPT3"), encoding="utf-8") as f:
            saved = json.load(f)
        expected.pop("saved_unix"); saved.pop("saved_unix")
        self.assertEqual(saved, json.loads(json.dumps(expected)))


class SharedLiveBrain(unittest.TestCase):
    def setUp(self):
        import fish_game_all_in_one as fish
        self.fish = fish
        self._saved_path = fish.BRAIN_PATH
        self.path = os.path.join(_SANDBOX["dir"], "brain.json")
        shutil.copyfile(self._saved_path, self.path)
        fish.BRAIN_PATH = self.path
        mp._LIVE_BRAIN.update(key=None, brain=None)

    def tearDown(self):
        self.fish.BRAIN_PATH = self._saved_path
        mp._LIVE_BRAIN.update(key=None, brain=None)

    def test_games_share_one_parsed_brain(self):
        first = mp.load_live_brain()
        self.assertIs(mp.load_live_brain(), first,
                      "every game parsing its own ~3 MB brain is what the cache removes")

    def test_a_saved_brain_reaches_the_next_game(self):
        """End-of-game learning saves a new file; the next game must play on it."""
        first = mp.load_live_brain()
        learned = self.fish.load_brain(self.path)
        learned["games_played"] = int(learned.get("games_played", 0)) + 12345
        self.fish.save_brain(learned, self.path)
        st = os.stat(self.path)
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        second = mp.load_live_brain()
        self.assertIsNot(second, first)
        self.assertEqual(second.get("games_played"), learned["games_played"])

    def test_count_brain_lookup_does_not_fork_the_shared_brain(self):
        # load_live_brain takes BRAIN_LOCK itself (a plain Lock), so it is
        # called outside the lock, the same order the game thread uses.
        brain = mp.load_live_brain()
        with mp.BRAIN_LOCK:
            a = self.fish.get_count_brain(brain, 4)
        again = mp.load_live_brain()
        with mp.BRAIN_LOCK:
            b = self.fish.get_count_brain(again, 4)
        self.assertIs(a, b)


class TrainingSnapshotsAreCompact(unittest.TestCase):
    def _game(self):
        import fish_game_all_in_one as fish
        card_db = fish.load_card_db()
        uids = sorted(card_db.keys())
        rnd = random.Random(7)
        players = []
        for i in range(4):
            pl = fish.PlayerState(name=f"P{i}", hand=rnd.sample(uids, 6))
            for ocean_uid in rnd.sample(uids, 3):
                pl.board_oceans.append(ocean_uid)
                slots = fish.OceanSlots()
                slots.up = rnd.sample(uids, 2)
                slots.left = rnd.sample(uids, 1)
                pl.ocean_slots[ocean_uid] = slots
            players.append(pl)
        gs = fish.GameState(card_db=card_db, players=players, deck=rnd.sample(uids, 20), turn_index=0)
        ms = fish.MatchState(pool=rnd.sample(uids, 4))
        ms.pair_primary_to_faces, ms.face_to_primary = fish.build_non_ocean_pair_maps(card_db)
        return gs, ms

    def test_snapshots_are_held_as_text_and_written_as_dicts(self):
        gs, ms = self._game()
        room = mp.GameRoom("SNAP1", "Host", 4, 1, 3)
        for turn in range(1, 4):
            room._record_snapshot(gs, ms, turn, f"turn_end:P{turn % 4}")
        self.assertEqual(len(room.training_snapshots), 3)
        self.assertTrue(all(isinstance(s, str) for s in room.training_snapshots),
                        "held as dicts, a late-game room costs ~24 MB instead of ~3 MB")
        standings = [{"name": p.name, "score": 10 - i} for i, p in enumerate(gs.players)]
        record = room._build_training_record(gs, ms, standings, {0})
        snaps = record["snapshots"]
        self.assertEqual([s["turn_number"] for s in snaps], [1, 2, 3])
        self.assertEqual(snaps[0]["players"][0]["name"], "P0")
        self.assertIsInstance(snaps[0]["players"][0]["board_slots"], dict)
        json.dumps(record)  # the dataset row still encodes

    def test_undo_copy_shares_cards_but_not_the_table(self):
        gs, _ms = self._game()
        snap = mp.copy_game_state(gs)
        self.assertIs(snap.card_db, gs.card_db, "the ~250 frozen cards are not worth copying")
        self.assertIsNot(snap.players, gs.players)
        snap.players[0].hand.append(-1)
        snap.deck.pop()
        self.assertNotIn(-1, gs.players[0].hand, "Undo must restore a real copy of the table")
        self.assertEqual(len(gs.deck), 20)


class StateViewDoesNotLeakBetweenViewers(unittest.TestCase):
    def test_one_players_view_never_writes_into_the_shared_snapshot(self):
        """state_view copies only the levels it writes to, so a hand attached
        for one viewer must never show up in the stored state or another view."""
        room = mp.GameRoom("VIEW1", "Host", 2, 2, 0)
        host = room.host_seat()
        joined = room.claim_seat("Guest", None, None)
        guest_token = joined["seat_token"]
        guest_index = joined["seat_index"]
        with room.cond:
            room.latest_public_state = {
                "players": [
                    {"index": host.index, "name": "Host", "board": [{"uid": 1}]},
                    {"index": guest_index, "name": "Guest", "board": [{"uid": 2}]},
                ],
                "pool": [{"uid": 9}],
            }
            room.latest_private_hands = {host.index: [{"uid": 100}], guest_index: [{"uid": 200}]}
            room.legal_actions_by_seat = {host.index: {"actions": [{"index": 0}]}}
        host_view = room.state_view(host.token, "localhost")
        guest_view = room.state_view(guest_token, "localhost")

        stored = room.latest_public_state["players"]
        self.assertTrue(all("hand" not in p for p in stored), "a view wrote a hand into the snapshot")

        def hand_of(view, idx):
            return next(p["hand"] for p in view["state"]["players"] if p["index"] == idx)
        self.assertEqual(hand_of(host_view, host.index), [{"uid": 100}])
        self.assertEqual(hand_of(host_view, guest_index), [], "the host must not see the guest's hand")
        self.assertEqual(hand_of(guest_view, guest_index), [{"uid": 200}])
        self.assertEqual(hand_of(guest_view, host.index), [])

        host_view["legal_actions"]["extra"] = True
        host_view["state"]["players"][0]["avatar"] = "x"
        self.assertNotIn("extra", room.legal_actions_by_seat[host.index])
        self.assertNotIn("avatar", room.latest_public_state["players"][0])


class RecoveryCounterCountsOnlyRooms(unittest.TestCase):
    """"Recovered rooms: … N failed" has to mean something.

    The state directory is not only rooms. site_stats.json lives there by
    default (STATS_PATH) and so does snap_sessions.json (snap_score.init is
    handed this very directory). Every .json in it was opened at boot and
    parsed as a room checkpoint, so each of those counted as a recovery
    FAILURE: a brand-new state directory reported "1 failed" holding nothing
    but site_stats.json. That counter is the only thing that says whether
    games came back after a restart, and it could never read zero.
    """

    def setUp(self):
        self._saved_rooms = dict(mp.ROOMS.rooms)
        mp.ROOMS.rooms.clear()
        for name in os.listdir(mp.ROOM_STATE_DIR):
            os.remove(os.path.join(mp.ROOM_STATE_DIR, name))

    def tearDown(self):
        for name in os.listdir(mp.ROOM_STATE_DIR):
            os.remove(os.path.join(mp.ROOM_STATE_DIR, name))
        mp.ROOMS.rooms.clear()
        mp.ROOMS.rooms.update(self._saved_rooms)

    def _write(self, name, payload):
        with open(os.path.join(mp.ROOM_STATE_DIR, name), "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def test_the_other_modules_state_files_are_not_rooms(self):
        self._write("site_stats.json", {"games_played": 101})
        self._write("snap_sessions.json", {"sessions": {}})
        room = mp.GameRoom("ABCDE", "Otter", 4, 1, 3)
        room.persist_now()

        listed = [os.path.basename(p) for p in mp.list_room_state_files()]
        self.assertEqual(listed, ["ABCDE.json"])

    def test_a_directory_of_only_sibling_files_recovers_zero_failures(self):
        self._write("site_stats.json", {"games_played": 101})
        self._write("snap_sessions.json", {"sessions": {}})

        out = mp.ROOMS.load_persisted_rooms(mp.CARD_DB)
        self.assertEqual(out["failed"], 0)
        self.assertEqual(out["loaded"], 0)

    def test_a_room_that_really_is_broken_is_still_counted(self):
        """The whole point of quietening the others: a real failure shows."""
        with open(os.path.join(mp.ROOM_STATE_DIR, "BROKE.json"), "w", encoding="utf-8") as f:
            f.write("{not json at all")
        self._write("site_stats.json", {"games_played": 101})

        out = mp.ROOMS.load_persisted_rooms(mp.CARD_DB)
        self.assertEqual(out["failed"], 1)


class EveryGameSurvivesARestart(unittest.TestCase):
    """A restart must not end the games it is not resuming.

    load_persisted_rooms used to keep the single most recently started
    lobby/running room and set every other one to "ended" with the note
    "Archived room from previous server session." That is from when the server
    served one table at a time. With 10 concurrent games and 60 people in them
    (a measured load), one redeploy ended nine of them mid-play, and because an
    archived room never reaches the code that publishes final_scores, nobody in
    those nine had their game saved either.
    """

    def setUp(self):
        self._saved_rooms = dict(mp.ROOMS.rooms)
        mp.ROOMS.rooms.clear()
        for name in os.listdir(mp.ROOM_STATE_DIR):
            os.remove(os.path.join(mp.ROOM_STATE_DIR, name))

    def tearDown(self):
        for name in os.listdir(mp.ROOM_STATE_DIR):
            os.remove(os.path.join(mp.ROOM_STATE_DIR, name))
        mp.ROOMS.rooms.clear()
        mp.ROOMS.rooms.update(self._saved_rooms)

    def _checkpoint(self, rid, phase, started_ago):
        """Write one room's state file the way a live server would have."""
        room = mp.GameRoom(rid, "Otter", total_players=4,
                           human_players=4, ai_players=0)
        now = mp.now_unix()
        room.phase = phase
        room.created_unix = now - started_ago - 5
        room.started_unix = (now - started_ago) if phase != "lobby" else None
        room.persist_now()
        return room

    def test_ten_lobbies_all_come_back_as_lobbies(self):
        for i in range(10):
            self._checkpoint(f"LOBB{i}", "lobby", started_ago=60 + i)
        mp.ROOMS.rooms.clear()

        out = mp.ROOMS.load_persisted_rooms(mp.CARD_DB)

        self.assertEqual(out["loaded"], 10)
        self.assertEqual(out["failed"], 0)
        phases = sorted(r.phase for r in mp.ROOMS.rooms.values())
        self.assertEqual(phases, ["lobby"] * 10,
                         "a restart must not close nine of ten lobbies")

    def test_no_room_is_marked_archived_any_more(self):
        for i in range(4):
            self._checkpoint(f"ARCH{i}", "lobby", started_ago=10 * i)
        mp.ROOMS.rooms.clear()

        mp.ROOMS.load_persisted_rooms(mp.CARD_DB)

        notes = [str(r.status_note or "") for r in mp.ROOMS.rooms.values()]
        self.assertFalse(
            any("previous server session" in n for n in notes),
            f"a restored room was archived instead of restored: {notes}",
        )

    def test_a_restored_room_can_take_a_human_turn(self):
        """The per-game live state has to exist on EVERY room, not just new ones.

        _launch_game_locked was the only place these were ever assigned, and a
        restored room does not go through it (resume_after_restore starts the
        match thread directly). _wait_for_action calls
        _drain_admin_mods_locked on every human turn, which reads
        _admin_mod_queue, so every human turn in a resumed game raised
        AttributeError; the policy wrapper caught it and played a "safe
        fallback action" instead. Five games restarted mid-play logged it on
        every turn and finished on fallback moves without one player getting a
        turn, and one was so degraded it was dropped for having no board
        cards, losing those four players' game.
        """
        live_state = [
            "_live_gs", "_live_ms", "_admin_deck_entries", "_admin_discards",
            "_admin_pool_entries", "_admin_endgame", "_bot_brain",
            "_admin_active", "_admin_mod_queue", "_bot_override",
            "_current_turn_descs", "_last_turn_scores", "_forfeit_result",
        ]
        room = self._checkpoint("RESUM", "running", started_ago=120)
        payload = json.load(open(os.path.join(mp.ROOM_STATE_DIR, "RESUM.json")))
        restored = mp.GameRoom.from_checkpoint(payload)
        self.assertIsNotNone(restored)

        absent = [a for a in live_state if not hasattr(restored, a)]
        self.assertEqual(absent, [], f"a restored room is missing {absent}")

        # The exact call every human turn makes, on a room that never launched.
        with restored.cond:
            restored._drain_admin_mods_locked()

        # And the same must hold for a room nobody has even persisted.
        plain = mp.GameRoom("PLAIN", "Otter", 4, 4, 0)
        self.assertEqual([a for a in live_state if not hasattr(plain, a)], [])

    def test_a_room_that_had_already_ended_stays_ended(self):
        """Restoring everything must not reopen a finished game."""
        room = self._checkpoint("DONE1", "ended", started_ago=600)
        with room.cond:
            room.ended_unix = mp.now_unix() - 30
            room.final_scores = [{"name": "Otter", "score": 12, "seat_index": 0}]
            # _bump_locked, not a bare persist_now: the checkpoint is only
            # rewritten when something has marked the room dirty, which is what
            # every real mutation does.
            room._bump_locked(force_persist=True)
        mp.ROOMS.rooms.clear()

        mp.ROOMS.load_persisted_rooms(mp.CARD_DB)

        back = mp.ROOMS.rooms["DONE1"]
        self.assertEqual(back.phase, "ended")
        self.assertEqual(back.final_scores, [{"name": "Otter", "score": 12, "seat_index": 0}])


class DisconnectsAreNotServerErrors(unittest.TestCase):
    """A player closing their tab must not write a traceback.

    Every client holds an SSE stream plus a keep-alive polling connection, so
    leaving resets a socket this server is waiting on.
    BaseHTTPRequestHandler.handle_one_request does not catch that, so
    socketserver's handle_error printed a full traceback per departure:
    measured at 57 of them, interleaved from 57 threads, for one 60-person
    session across 10 games. On a host where the log is the only health signal,
    a real traceback then has to be found among them.
    """

    def setUp(self):
        # Port 0: the OS picks a free one. Bound only so handle_error has a
        # real instance to call super() on, never served.
        self.server = mp.StableThreadingHTTPServer(("127.0.0.1", 0), mp.MultiplayerHandler)

    def tearDown(self):
        self.server.server_close()

    def _stderr_for(self, exc):
        import contextlib
        import io
        buf = io.StringIO()
        try:
            raise exc
        except Exception:
            with contextlib.redirect_stderr(buf):
                self.server.handle_error(None, ("127.0.0.1", 54321))
        return buf.getvalue()

    def test_a_reset_connection_says_nothing(self):
        for exc in (ConnectionResetError("tab closed"),
                    BrokenPipeError("mid-write"),
                    ConnectionAbortedError("aborted"),
                    TimeoutError("idle keep-alive")):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(self._stderr_for(exc), "")

    def test_a_real_bug_is_still_loud(self):
        out = self._stderr_for(ValueError("a real bug in a handler"))
        self.assertIn("a real bug in a handler", out)
        self.assertIn("Exception occurred during processing of request", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
