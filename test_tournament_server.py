#!/usr/bin/env python3
"""Server-orchestration tests for tournament_server.py (headless — no Firestore,
no real game rooms). Drives the Tournament object end to end via its public API.

Run:  python3 -m unittest test_tournament_server -v
"""

import unittest

import tournament_server as ts
from tournament_server import (
    TournamentManager, Tournament, TournamentConfig,
    S_READY, S_NOT_READY, S_WAITING, S_ELIMINATED, S_CHAMPION, S_PLAYING, S_DISCONNECTED,
    _now,
)
from tournament_engine import M_READY, M_ACTIVE, M_COMPLETE, M_BYE


def make(cap, ppm, n, *, third_place=False, guest=True):
    """Create a tournament, fill it with n ready players, return (mgr, t).
    guest=False creates real (non-guest) accounts so placement XP applies."""
    prefix = "guest:" if guest else "acct:"
    mgr = TournamentManager()
    cfg = TournamentConfig(cap, ppm, third_place_match=third_place, name="Test Cup")
    t = mgr.create(cfg, prefix + "host", "Host")
    # host is participant 0; add n-1 more
    for i in range(1, n):
        r = t.join(f"{prefix}p{i:02d}", f"P{i:02d}", is_guest=guest)
        assert r["ok"], r
    # everyone ready
    for p in t.participants:
        t.set_ready(p.pid, True)
    return mgr, t


def make_custom(sizes, n, *, guest=True, fill_bots=False):
    """Create a CUSTOM-bracket tournament (opening layout == sizes) with n ready
    players (n <= sum(sizes)). Returns (mgr, t)."""
    prefix = "guest:" if guest else "acct:"
    mgr = TournamentManager()
    cfg = TournamentConfig(sum(sizes), max(sizes), opening_sizes=list(sizes), name="Custom Cup")
    t = mgr.create(cfg, prefix + "host", "Host", fill_bots=fill_bots)
    for i in range(1, n):
        r = t.join(f"{prefix}p{i:02d}", f"P{i:02d}", is_guest=guest)
        assert r["ok"], r
    for p in t.participants:
        t.set_ready(p.pid, True)
    return mgr, t


def play_out(t, winner_pick="first", seed=0):
    """Report results for every ready match until the tournament completes.
    winner_pick 'first' -> lowest pid wins; 'seed' -> deterministic shuffle."""
    import random
    guard = 0
    while t.phase == Tournament.PHASE_RUNNING:
        guard += 1
        if guard > 5000:
            raise AssertionError("play_out did not converge")
        progressed = False
        for row in t.bracket.rounds:
            for m in row:
                if m.status == M_READY and m.filled_count() == m.capacity and not m.room_id:
                    present = [p for p in m.player_ids if p]
                    ranking = sorted(present)
                    if winner_pick == "seed":
                        rng = random.Random(seed * 1000 + m.match_number)
                        ranking = present[:]
                        rng.shuffle(ranking)
                    scores = {pid: 200 - i * 5 for i, pid in enumerate(ranking)}
                    res = t.report_match_result(m.round_index, m.match_index, ranking, scores)
                    assert res["ok"], res
                    progressed = True
        tp = t.bracket.third_place
        if tp is not None and tp.status == M_READY and tp.filled_count() == tp.capacity:
            present = [p for p in tp.player_ids if p]
            res = t.report_match_result(tp.round_index, tp.match_index, sorted(present),
                                        {pid: 100 - i for i, pid in enumerate(sorted(present))})
            assert res["ok"], res
            progressed = True
        if not progressed and t.phase == Tournament.PHASE_RUNNING:
            raise AssertionError("stuck: no ready match to report")


class TestLobby(unittest.TestCase):
    def test_create_and_join(self):
        mgr, t = make(8, 2, 1)  # just the host
        self.assertEqual(len(t.participants), 1)
        r = t.join("guest:a", "A")
        self.assertTrue(r["ok"])
        self.assertEqual(len(t.participants), 2)

    def test_capacity_and_spectator_overflow(self):
        mgr, t = make(4, 2, 4)  # full (host + 3)
        r = t.join("guest:extra", "Extra")  # spectators allowed by default
        self.assertTrue(r["ok"])
        self.assertTrue(r.get("spectator"))
        self.assertEqual(len(t.participants), 4)

    def test_capacity_full_no_spectators(self):
        mgr = TournamentManager()
        t = mgr.create(TournamentConfig(4, 2), "guest:h", "H", spectators_allowed=False)
        for i in range(3):
            t.join(f"guest:{i}", f"P{i}")
        r = t.join("guest:x", "X")
        self.assertFalse(r["ok"])

    def test_start_gate_requires_ready_and_min(self):
        mgr = TournamentManager()
        t = mgr.create(TournamentConfig(8, 2), "guest:h", "H")
        t.join("guest:a", "A")  # only 2 players, not ready
        ok, why = t.can_start()
        self.assertFalse(ok)
        # ready both, still >= min (4)? no -> need 4
        t.set_ready("guest:h", True)
        t.set_ready("guest:a", True)
        ok, why = t.can_start()
        self.assertFalse(ok, "2 players < min 4 should block")

    def test_start_seeds_and_sets_status(self):
        mgr, t = make(8, 2, 8)
        r = t.start()
        self.assertTrue(r["ok"], r)
        self.assertEqual(t.phase, Tournament.PHASE_RUNNING)
        self.assertIsNotNone(t.bracket)
        # every player seeded exactly once into round 1
        seen = [p for m in t.bracket.rounds[0] for p in m.player_ids if p]
        self.assertEqual(sorted(seen), sorted(p.pid for p in t.participants))


class TestRandomizeAndSwitch(unittest.TestCase):
    def test_randomize_preserves_set(self):
        mgr, t = make(8, 2, 6)
        before = sorted(p.pid for p in t.participants)
        t.randomize(rng_seed=1)
        after = sorted(p.pid for p in t.participants)
        self.assertEqual(before, after)
        # join_order is a permutation 0..n-1
        self.assertEqual(sorted(p.join_order for p in t.participants), list(range(6)))

    def test_switch_accept_swaps(self):
        mgr, t = make(8, 2, 4)
        a = t.participants[1]
        b = t.participants[2]
        ai, bi = a.join_order, b.join_order
        t.request_switch(a.pid, b.pid)
        r = t.respond_switch(b.pid, True)
        self.assertTrue(r["accepted"])
        self.assertEqual(a.join_order, bi)
        self.assertEqual(b.join_order, ai)

    def test_switch_decline_keeps(self):
        mgr, t = make(8, 2, 4)
        a = t.participants[1]
        b = t.participants[2]
        ai, bi = a.join_order, b.join_order
        t.request_switch(a.pid, b.pid)
        r = t.respond_switch(b.pid, False)
        self.assertFalse(r["accepted"])
        self.assertEqual((a.join_order, b.join_order), (ai, bi))

    def test_cannot_switch_after_start(self):
        mgr, t = make(8, 2, 8)
        t.start()
        r = t.request_switch(t.participants[1].pid, t.participants[2].pid)
        self.assertFalse(r["ok"])


class TestHostControls(unittest.TestCase):
    def test_rename_lock_remove(self):
        mgr, t = make(8, 2, 5)
        t.host_set(name="Reef Rumble")
        self.assertEqual(t.cfg.name, "Reef Rumble")
        t.host_set(locked=True)
        r = t.join("guest:late", "Late")
        self.assertFalse(r["ok"])  # locked
        victim = t.participants[2].pid
        t.host_remove(victim)
        self.assertIsNone(t._by_pid(victim))

    def test_cannot_remove_host(self):
        mgr, t = make(8, 2, 5)
        r = t.host_remove("guest:host")
        self.assertFalse(r["ok"])

    def test_cancel(self):
        mgr, t = make(8, 2, 5)
        t.cancel()
        self.assertEqual(t.phase, Tournament.PHASE_CANCELLED)

    def test_host_log_records_actions(self):
        mgr, t = make(8, 2, 5)
        t.host_set(name="X")
        t.randomize(rng_seed=1)
        actions = [e["action"] for e in t.host_log]
        self.assertIn("rename", actions)
        self.assertIn("randomize", actions)


class TestFullRun(unittest.TestCase):
    GRID = [(cap, ppm, n)
            for (cap, n) in [(4, 4), (8, 8), (8, 6), (8, 5), (16, 16), (16, 13),
                             (24, 24), (32, 32), (32, 27)]
            for ppm in range(2, 9) if ppm <= n]

    def test_runs_to_champion(self):
        for cap, ppm, n in self.GRID:
            for pick in ("first", "seed"):
                mgr, t = make(cap, ppm, n)
                self.assertTrue(t.start()["ok"], f"start failed cap={cap} ppm={ppm} n={n}")
                play_out(t, winner_pick=pick)
                self.assertEqual(t.phase, Tournament.PHASE_COMPLETE,
                                 f"not complete cap={cap} ppm={ppm} n={n} {pick}")
                self.assertIsNotNone(t.champion_pid)
                # champion status + place
                champ = t._by_pid(t.champion_pid)
                self.assertEqual(champ.status, S_CHAMPION)
                self.assertEqual(champ.final_place, 1)
                # everyone placed exactly once
                placed = [e["player_id"] for e in t.final_placements]
                self.assertEqual(sorted(placed), sorted(p.pid for p in t.participants))
                # non-champions eliminated
                for p in t.participants:
                    if p.pid != t.champion_pid:
                        self.assertEqual(p.status, S_ELIMINATED)

    def test_eliminated_players_never_win_more_matches(self):
        mgr, t = make(16, 2, 16)
        t.start()
        play_out(t)
        # champion won exactly n_rounds matches (no byes for 16 @ 1v1)
        champ = t._by_pid(t.champion_pid)
        self.assertEqual(champ.matches_won, t.bracket.n_rounds)


class TestDuplicateAndValidation(unittest.TestCase):
    def test_duplicate_report_rejected(self):
        mgr, t = make(4, 2, 4)
        t.start()
        m = next(m for row in t.bracket.rounds for m in row if m.status == M_READY)
        present = [p for p in m.player_ids if p]
        r1 = t.report_match_result(m.round_index, m.match_index, sorted(present))
        self.assertTrue(r1["ok"])
        r2 = t.report_match_result(m.round_index, m.match_index, sorted(present))
        self.assertFalse(r2["ok"], "duplicate result must be rejected")

    def test_wrong_players_rejected(self):
        mgr, t = make(4, 2, 4)
        t.start()
        m = next(m for row in t.bracket.rounds for m in row if m.status == M_READY)
        r = t.report_match_result(m.round_index, m.match_index, ["guest:nope", "guest:nada"])
        self.assertFalse(r["ok"])


class TestForfeit(unittest.TestCase):
    def test_mid_tournament_forfeit_advances_opponent(self):
        mgr, t = make(8, 2, 8)
        t.start()
        # first ready match: one player forfeits
        m = next(m for row in t.bracket.rounds for m in row if m.status == M_READY)
        present = [p for p in m.player_ids if p]
        quitter = present[0]
        r = t.leave(quitter)
        self.assertTrue(r.get("forfeit"))
        # the match should now be complete with the other player advanced
        self.assertIn(m.status, (M_COMPLETE, M_BYE))
        self.assertEqual(m.winners[0], present[1])
        # quitter is eliminated and marked quit (no no-quit bonus later)
        qp = t._by_pid(quitter)
        self.assertEqual(qp.status, S_ELIMINATED)
        self.assertTrue(qp.quit)


class TestThirdPlace(unittest.TestCase):
    def test_third_place_match_runs(self):
        mgr, t = make(8, 2, 8, third_place=True)
        t.start()
        self.assertIsNotNone(t.bracket.third_place)
        play_out(t)
        self.assertEqual(t.phase, Tournament.PHASE_COMPLETE)
        places = {e["place"]: e["player_id"] for e in t.final_placements}
        self.assertIn(3, places)
        self.assertIn(4, places)


class TestXP(unittest.TestCase):
    def test_champion_gets_scaled_champion_xp(self):
        mgr, t = make(32, 2, 32, guest=False)  # real accounts -> placement XP applies
        t.start()
        play_out(t)
        champ = t._by_pid(t.champion_pid)
        bd = t._xp_grants.get(champ.pid) or t.compute_xp_for(champ)
        kinds = {it["kind"]: it["amount"] for it in bd["items"]}
        self.assertEqual(kinds.get("champion"), 3000)  # full 32-player scale
        self.assertEqual(bd["total_xp"], sum(it["amount"] for it in bd["items"]))

    def test_guest_padded_field_earns_no_placement_xp(self):
        # A single main + guests (or an all-guest field) must NOT pay champion XP.
        mgr, t = make(4, 2, 4, guest=True)
        t.start(); play_out(t)
        champ = t.compute_xp_for(t._by_pid(t.champion_pid))
        kinds = {it["kind"] for it in champ["items"]}
        self.assertNotIn("champion", kinds, "guest-padded field must not pay champion XP")
        self.assertNotIn("match_win", kinds)

    def test_small_real_tournament_scaled_far_below_big(self):
        mgr4, t4 = make(4, 2, 4, guest=False)
        t4.start(); play_out(t4)
        c4 = t4.compute_xp_for(t4._by_pid(t4.champion_pid))
        mgr32, t32 = make(32, 2, 32, guest=False)
        t32.start(); play_out(t32)
        c32 = t32.compute_xp_for(t32._by_pid(t32.champion_pid))
        # steep curve: a 4-player champion earns well under a third of a 32-player one
        self.assertLess(c4["total_xp"], 0.33 * c32["total_xp"])

    def test_small_tournament_scaled_down(self):
        mgr4, t4 = make(4, 2, 4)
        t4.start(); play_out(t4)
        c4 = t4.compute_xp_for(t4._by_pid(t4.champion_pid))
        mgr32, t32 = make(32, 2, 32)
        t32.start(); play_out(t32)
        c32 = t32.compute_xp_for(t32._by_pid(t32.champion_pid))
        self.assertLess(c4["total_xp"], c32["total_xp"])

    def test_every_finisher_has_xp_breakdown(self):
        mgr, t = make(8, 2, 8)
        t.start(); play_out(t)
        for p in t.participants:
            bd = t._xp_grants.get(p.pid)
            self.assertIsNotNone(bd, f"{p.name} missing xp breakdown")
            self.assertGreater(bd["total_xp"], 0)


class TestStateView(unittest.TestCase):
    def test_state_view_shape(self):
        mgr, t = make(8, 2, 8)
        sv = t.state_view(viewer_pid="guest:host", host_token=t.host_control_token)
        self.assertIn("participants", sv)
        self.assertIn("bracket", sv)  # None before start
        self.assertTrue(sv["viewer"]["is_host"])
        self.assertIn("host_log", sv)  # host sees the log
        t.start()
        sv2 = t.state_view(viewer_pid=t.participants[1].pid)
        self.assertIsNotNone(sv2["bracket"])
        self.assertFalse(sv2["viewer"]["is_host"])
        self.assertIsNotNone(sv2["viewer"]["my_match"])  # player is in a round-1 match

    def test_spectator_view(self):
        mgr, t = make(8, 2, 8)
        t.start()
        t.join("guest:spec", "Spec")  # after start -> spectator
        sv = t.state_view(viewer_pid="guest:spec")
        self.assertEqual(sv["viewer"]["status"], "spectating")


class TestConcurrency(unittest.TestCase):
    def test_no_double_spawn_on_concurrent_finish(self):
        import threading
        spawns = {}; lk = threading.Lock()
        def fake_create(**kw):
            key = (kw["round_index"], kw["match_index"])
            with lk:
                spawns[key] = spawns.get(key, 0) + 1
                n = spawns[key]
            return f"ROOM_{key[0]}_{key[1]}_{n}"
        old = ts._create_match_room
        ts._create_match_room = fake_create
        try:
            mgr, t = make(8, 2, 8)
            self.assertTrue(t.start()["ok"])
            # report all 4 round-1 matches CONCURRENTLY -> round-2 spawn race
            def rep(m):
                present = [p for p in m.player_ids if p]
                t.report_match_result(m.round_index, m.match_index, sorted(present))
            threads = [threading.Thread(target=rep, args=(m,)) for m in list(t.bracket.rounds[0])]
            for th in threads: th.start()
            for th in threads: th.join()
            # every match must have been spawned AT MOST once (no double room)
            for key, n in spawns.items():
                self.assertLessEqual(n, 1, f"match at {key} spawned {n} times (double-spawn race)")
            # each active round-2 match has exactly one room mapping
            room_ids = [m.room_id for row in t.bracket.rounds for m in row if m.room_id]
            self.assertEqual(len(room_ids), len(set(room_ids)), "duplicate room_id on a match")
        finally:
            ts._create_match_room = old

    def test_forfeit_completing_final_finalizes_tournament(self):
        mgr, t = make(4, 2, 4)
        t.start()
        # play both round-1 matches so the final is filled with 2 players
        for m in list(t.bracket.rounds[0]):
            present = [p for p in m.player_ids if p]
            t.report_match_result(m.round_index, m.match_index, sorted(present))
        finalists = [p for p in t.bracket.rounds[1][0].player_ids if p]
        self.assertEqual(len(finalists), 2)
        # one finalist forfeits -> the other must win the tournament + XP finalize
        r = t.leave(finalists[0])
        self.assertTrue(r.get("forfeit"))
        self.assertEqual(t.phase, Tournament.PHASE_COMPLETE, "forfeit on final must finalize tournament")
        self.assertEqual(t.champion_pid, finalists[1])
        self.assertIn(finalists[1], t._xp_grants, "champion XP must be granted after forfeit-final")


class TestLifecycle(unittest.TestCase):
    def test_disconnect_reaper_marks_stale_participant(self):
        mgr, t = make(8, 2, 4)
        victim = t.participants[1]
        victim.last_seen = _now() - 9999
        # a poll from someone else triggers the reaper
        t.state_view(viewer_pid=t.participants[0].pid, host_token=t.host_control_token)
        self.assertFalse(victim.connected)
        self.assertEqual(victim.status, S_DISCONNECTED)

    def test_disconnected_unready_player_does_not_block_start(self):
        mgr = TournamentManager()
        t = mgr.create(TournamentConfig(8, 2), "guest:h", "H")
        for i in range(3):
            t.join(f"guest:{i}", f"P{i}")
        for p in t.participants:
            t.set_ready(p.pid, True)
        ghost = t.participants[3]
        t.set_ready(ghost.pid, False)                 # not ready -> blocks
        self.assertFalse(t.can_start()[0])
        ghost.last_seen = _now() - 9999               # ...then disconnects
        t.state_view(viewer_pid=t.participants[0].pid)
        self.assertTrue(t.can_start()[0], "disconnected not-ready player must not block start")

    def test_host_reassigned_when_host_leaves_lobby(self):
        mgr, t = make(8, 2, 4)
        old = t.host_pid
        t.leave(old)
        self.assertNotEqual(t.host_pid, old)
        self.assertIsNotNone(t._by_pid(t.host_pid))

    def test_host_reassigned_on_disconnect(self):
        mgr, t = make(8, 2, 4)
        old = t.host_pid
        t._by_pid(old).last_seen = _now() - 9999
        t.state_view(viewer_pid=t.participants[1].pid)
        self.assertNotEqual(t.host_pid, old)
        self.assertTrue(t._by_pid(t.host_pid).connected)

    def test_cancelled_state_view_ok(self):
        mgr, t = make(8, 2, 4)
        t.cancel()
        sv = t.state_view(viewer_pid=t.participants[0].pid)
        self.assertEqual(sv["phase"], "cancelled")

    def test_forfeited_player_not_resurrected_on_spawn(self):
        # A winner waiting in a future slot who forfeits must not be flipped back
        # to 'playing' when that match later spawns.
        mgr, t = make(8, 2, 8)
        t.start()
        # resolve round 1 so winners are seeded into round 2
        for m in list(t.bracket.rounds[0]):
            present = [p for p in m.player_ids if p]
            t.report_match_result(m.round_index, m.match_index, sorted(present))
        r2 = t.bracket.rounds[1][0]
        waiting = next(p for p in r2.player_ids if p)
        t.leave(waiting)                              # forfeit while waiting
        self.assertEqual(t._by_pid(waiting).status, S_ELIMINATED)
        self.assertTrue(t._by_pid(waiting).quit)


class _FakeParsed:
    def __init__(self, path, query=""):
        self.path = path
        self.query = query


class _FakeHandler:
    def __init__(self):
        self.last = None
        self.status = None

    def _send_json(self, payload, status=200):
        self.last = payload
        self.status = int(status)


class TestHttpDispatch(unittest.TestCase):
    """Exercise the HTTP dispatch layer (guards the tournament-name vs
    player-name collision fixed after the live smoke test)."""

    def test_create_separates_tournament_and_player_names(self):
        h = _FakeHandler()
        handled = ts.handle_post(h, _FakeParsed("/api/tournament/create"), {
            "total_capacity": 8, "players_per_match": 2,
            "name": "Reef Rumble", "host_name": "Hosty", "guest_id": "h1"})
        self.assertTrue(handled)
        self.assertTrue(h.last["ok"], h.last)
        t = ts.MANAGER.get(h.last["tournament_id"])
        self.assertEqual(t.cfg.name, "Reef Rumble")
        self.assertEqual(t.participants[0].name, "Hosty")  # not the tournament name

    def test_join_uses_player_name(self):
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/create"), {
            "total_capacity": 8, "players_per_match": 2,
            "name": "Cup", "host_name": "H", "guest_id": "h2"})
        tid = h.last["tournament_id"]
        h2 = _FakeHandler()
        ts.handle_post(h2, _FakeParsed("/api/tournament/join"),
                       {"id": tid, "name": "Wanda", "guest_id": "w"})
        self.assertTrue(h2.last["ok"])
        t = ts.MANAGER.get(tid)
        self.assertIn("Wanda", [p.name for p in t.participants])

    def test_disabled_flag_returns_404(self):
        from http import HTTPStatus
        old = ts.TOURNAMENTS_ENABLED
        ts.TOURNAMENTS_ENABLED = False
        try:
            h = _FakeHandler()
            handled = ts.handle_post(h, _FakeParsed("/api/tournament/create"), {})
            self.assertTrue(handled)
            self.assertEqual(h.status, int(HTTPStatus.NOT_FOUND))
        finally:
            ts.TOURNAMENTS_ENABLED = old

    def test_report_dispatch_advances(self):
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/create"), {
            "total_capacity": 4, "players_per_match": 2, "name": "C", "host_name": "H", "guest_id": "rh"})
        tid = h.last["tournament_id"]
        for i in range(3):
            ts.handle_post(_FakeHandler(), _FakeParsed("/api/tournament/join"),
                           {"id": tid, "name": f"P{i}", "guest_id": f"rp{i}"})
        for gid in ["rh", "rp0", "rp1", "rp2"]:
            ts.handle_post(_FakeHandler(), _FakeParsed("/api/tournament/ready"),
                           {"id": tid, "guest_id": gid, "ready": True})
        htok = ts.MANAGER.get(tid).host_control_token
        ts.handle_post(_FakeHandler(), _FakeParsed("/api/tournament/start"),
                       {"id": tid, "guest_id": "rh", "host_token": htok})
        t = ts.MANAGER.get(tid)
        self.assertEqual(t.phase, Tournament.PHASE_RUNNING)


class TestTopNAdvanceServer(unittest.TestCase):
    """"Top N advance" end to end through the live Tournament object + HTTP layer."""

    @staticmethod
    def _make(cap, ppm, adv, n):
        mgr = TournamentManager()
        cfg = TournamentConfig(cap, ppm, advance_per_match=adv, name="Group Cup")
        t = mgr.create(cfg, "guest:host", "Host")
        for i in range(1, n):
            assert t.join(f"guest:p{i:02d}", f"P{i:02d}")["ok"]
        for p in t.participants:
            t.set_ready(p.pid, True)
        return mgr, t

    def test_full_top_two_tournament_runs_to_a_champion(self):
        mgr, t = self._make(16, 4, 2, 16)
        self.assertTrue(t.start()["ok"], t.can_start())
        self.assertEqual([len(r) for r in t.bracket.rounds], [4, 2, 1])
        play_out(t, winner_pick="seed", seed=4)
        self.assertEqual(t.phase, Tournament.PHASE_COMPLETE)
        self.assertIsNotNone(t.champion_pid)
        champs = [p for p in t.participants if p.status == S_CHAMPION]
        self.assertEqual(len(champs), 1)
        self.assertEqual(len(t.final_placements), 16)

    def test_both_advancing_players_survive_the_round(self):
        """The bug top-N advancement invites: only the match WINNER being moved to
        'waiting' leaves the 2nd-place qualifier looking eliminated."""
        mgr, t = self._make(8, 4, 2, 8)
        self.assertTrue(t.start()["ok"])
        m = t.bracket.rounds[0][0]
        present = [p for p in m.player_ids if p]
        t.report_match_result(0, 0, present, {})
        by_pid = {p.pid: p for p in t.participants}
        self.assertEqual(by_pid[present[0]].status, S_WAITING, "winner advances")
        self.assertEqual(by_pid[present[1]].status, S_WAITING, "runner-up ALSO advances")
        self.assertEqual(by_pid[present[2]].status, S_ELIMINATED)
        self.assertEqual(by_pid[present[3]].status, S_ELIMINATED)
        # both advancing players are credited with reaching the next round...
        self.assertEqual(by_pid[present[0]].deepest_round, 1)
        self.assertEqual(by_pid[present[1]].deepest_round, 1)
        # ...but only 1st place WON the match
        self.assertEqual(by_pid[present[0]].matches_won, 1)
        self.assertEqual(by_pid[present[1]].matches_won, 0)

    def test_bracket_view_exposes_every_advancing_player(self):
        mgr, t = self._make(8, 4, 2, 8)
        t.start()
        m = t.bracket.rounds[0][0]
        present = [p for p in m.player_ids if p]
        t.report_match_result(0, 0, present, {})
        view = t.state_view()["bracket"]["rounds"][0][0]
        self.assertEqual([a["pid"] for a in view["advancing"]], present[:2])
        self.assertEqual([a["place"] for a in view["advancing"]], [1, 2])
        self.assertEqual(view["winner"]["pid"], present[0])

    def test_create_endpoint_accepts_advance_per_match(self):
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/create"), {
            "total_capacity": 16, "players_per_match": 4, "advance_per_match": 2,
            "name": "Groups", "host_name": "H", "guest_id": "adv1"})
        self.assertTrue(h.last["ok"], h.last)
        t = ts.MANAGER.get(h.last["tournament_id"])
        self.assertEqual(t.cfg.advance_per_match, 2)

    def test_create_rejects_an_impossible_advance(self):
        from http import HTTPStatus
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/create"), {
            "total_capacity": 8, "players_per_match": 2, "advance_per_match": 2,
            "name": "Nope", "host_name": "H", "guest_id": "adv2"})
        self.assertFalse(h.last["ok"])
        self.assertEqual(h.status, int(HTTPStatus.BAD_REQUEST))
        self.assertIn("knocked out", h.last["error"])

    def test_create_defaults_to_single_elimination(self):
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/create"), {
            "total_capacity": 8, "players_per_match": 4,
            "name": "Classic", "host_name": "H", "guest_id": "adv3"})
        self.assertTrue(h.last["ok"], h.last)
        self.assertEqual(ts.MANAGER.get(h.last["tournament_id"]).cfg.advance_per_match, 1)

    def test_opening_sizes_take_an_advance_rule(self):
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/create"), {
            "opening_sizes": [4, 4, 4], "advance_per_match": 2,
            "name": "Pods", "host_name": "H", "guest_id": "adv4"})
        self.assertTrue(h.last["ok"], h.last)
        self.assertEqual(ts.MANAGER.get(h.last["tournament_id"]).cfg.advance_per_match, 2)
        # ...but not one a 2-player opening match can't honour
        h2 = _FakeHandler()
        ts.handle_post(h2, _FakeParsed("/api/tournament/create"), {
            "opening_sizes": [4, 4, 2], "advance_per_match": 2,
            "name": "Pods", "host_name": "H", "guest_id": "adv5"})
        self.assertFalse(h2.last["ok"])

    def test_preview_and_options_endpoints(self):
        h = _FakeHandler()
        ts.handle_get(h, _FakeParsed("/api/tournament/preview",
                                     "capacity=16&players_per_match=4&advance=2"))
        s = h.last["summary"]
        self.assertEqual(s["advancing_per_match"], 2)
        self.assertEqual(s["num_rounds"], 3)

        h2 = _FakeHandler()
        ts.handle_get(h2, _FakeParsed("/api/tournament/advance_options",
                                      "capacity=16&players_per_match=4"))
        self.assertEqual([o["advance_per_match"] for o in h2.last["options"]], [1, 2, 3])

        # a rule the match size can't manage is clamped, never an error
        h3 = _FakeHandler()
        ts.handle_get(h3, _FakeParsed("/api/tournament/preview",
                                      "capacity=8&players_per_match=2&advance=5"))
        self.assertTrue(h3.last["ok"], h3.last)
        self.assertEqual(h3.last["summary"]["advancing_per_match"], 1)

    def test_formats_endpoint_reports_advance_fit(self):
        h = _FakeHandler()
        ts.handle_get(h, _FakeParsed("/api/tournament/formats", "capacity=16&advance=2"))
        by = {f["players_per_match"]: f for f in h.last["formats"]}
        self.assertFalse(by[2]["advance_ok"])
        self.assertTrue(by[4]["advance_ok"])
        self.assertEqual(by[4]["advance_per_match"], 2)

    def test_public_listing_describes_the_rule(self):
        mgr, t = self._make(16, 4, 2, 4)
        row = next(r for r in mgr.list_public() if r["tournament_id"] == t.tid)
        self.assertEqual(row["advance_per_match"], 2)
        self.assertEqual(row["advance_label"], "Top 2 advance")

    def test_forfeit_still_advances_the_right_number(self):
        mgr, t = self._make(8, 4, 2, 8)
        t.start()
        m = t.bracket.rounds[0][0]
        present = [p for p in m.player_ids if p]
        t.leave(present[0])
        self.assertEqual(m.status, M_COMPLETE)
        self.assertEqual(len(m.winners), 2, "top 2 still advance when someone forfeits")
        self.assertNotIn(present[0], m.winners, "the quitter does not advance")


class TestCustomBracketServer(unittest.TestCase):
    def test_custom_requires_all_seats_before_start(self):
        mgr, t = make_custom([3, 4, 2], 4)      # capacity 9, only 4 filled
        ok, why = t.can_start()
        self.assertFalse(ok)
        self.assertIn("seats filled", why)
        # top up with bots -> now full -> can start
        t.fill_with_bots()
        self.assertEqual(len(t.participants), 9)
        ok, why = t.can_start()
        self.assertTrue(ok, why)
        self.assertTrue(t.start()["ok"])
        self.assertEqual(t.phase, Tournament.PHASE_RUNNING)
        self.assertEqual([m.capacity for m in t.bracket.rounds[0]], [3, 4, 2])

    def test_custom_full_human_run_reaches_champion(self):
        mgr, t = make_custom([3, 4, 2], 9)      # all 9 humans, ready
        self.assertTrue(t.can_start()[0], t.can_start()[1])
        self.assertTrue(t.start()["ok"])
        play_out(t, winner_pick="seed", seed=3)
        self.assertEqual(t.phase, Tournament.PHASE_COMPLETE)
        self.assertIsNotNone(t.champion_pid)

    def test_custom_fill_bots_on_start_completes(self):
        mgr, t = make_custom([2, 3, 4], 3, fill_bots=True)   # capacity 9
        self.assertTrue(t.start()["ok"])
        # human matches still need results reported; bot-only matches auto-resolved
        if t.phase == Tournament.PHASE_RUNNING:
            play_out(t, winner_pick="seed", seed=5)
        self.assertIn(t.phase, (Tournament.PHASE_COMPLETE,))
        self.assertIsNotNone(t.champion_pid)

    def test_lobby_state_has_preview_bracket(self):
        mgr, t = make_custom([3, 4, 2], 4)
        st = t.state_view(viewer_pid="guest:host", host_token=t.host_control_token)
        br = st["bracket"]
        self.assertIsNotNone(br)
        self.assertTrue(br.get("preview"))
        self.assertEqual([[m["capacity"] for m in row] for row in br["rounds"]], [[3, 4, 2], [3]])
        # summary reflects custom shape
        self.assertTrue(st["summary"]["is_custom"])
        self.assertEqual(st["summary"]["opening_sizes"], [3, 4, 2])

    def test_uniform_lobby_preview_present(self):
        mgr, t = make(8, 2, 5)     # 5 of 8, no bots
        st = t.state_view(viewer_pid=t.participants[0].pid, host_token=t.host_control_token)
        br = st["bracket"]
        self.assertIsNotNone(br)
        self.assertTrue(br.get("preview"))

    def test_host_swap_swaps_positions(self):
        mgr, t = make(8, 2, 4)
        a, b = t.participants[0].pid, t.participants[3].pid
        oa, ob = t.participants[0].join_order, t.participants[3].join_order
        res = t.host_swap(a, b)
        self.assertTrue(res["ok"], res)
        self.assertEqual(t._by_pid(a).join_order, ob)
        self.assertEqual(t._by_pid(b).join_order, oa)

    def test_host_swap_rejected_after_start(self):
        mgr, t = make(8, 2, 8)
        t.start()
        res = t.host_swap(t.participants[0].pid, t.participants[1].pid)
        self.assertFalse(res["ok"])

    def test_host_swap_rejects_same_or_missing(self):
        mgr, t = make(8, 2, 4)
        self.assertFalse(t.host_swap("guest:host", "guest:host")["ok"])
        self.assertFalse(t.host_swap("guest:host", "guest:nope")["ok"])


class TestCustomHttpDispatch(unittest.TestCase):
    def test_create_custom_via_dispatch(self):
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/create"), {
            "opening_sizes": [3, 4, 2], "name": "Bespoke", "host_name": "H", "guest_id": "ch"})
        self.assertTrue(h.last["ok"], h.last)
        t = ts.MANAGER.get(h.last["tournament_id"])
        self.assertTrue(t.cfg.is_custom)
        self.assertEqual(t.cfg.total_capacity, 9)
        self.assertEqual(t.cfg.players_per_match, 4)
        self.assertEqual(t.cfg.opening_sizes, [3, 4, 2])

    def test_create_custom_string_sizes(self):
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/create"), {
            "opening_sizes": "2,2,2,2", "host_name": "H", "guest_id": "cs"})
        self.assertTrue(h.last["ok"], h.last)
        t = ts.MANAGER.get(h.last["tournament_id"])
        self.assertEqual(t.cfg.opening_sizes, [2, 2, 2, 2])

    def test_create_custom_bad_sizes_rejected(self):
        from http import HTTPStatus
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/create"), {
            "opening_sizes": [9, 9], "host_name": "H", "guest_id": "cb"})  # 9 > max match size
        self.assertFalse(h.last["ok"])
        self.assertEqual(h.status, int(HTTPStatus.BAD_REQUEST))

    def test_preview_get_custom(self):
        h = _FakeHandler()
        handled = ts.handle_get(h, _FakeParsed("/api/tournament/preview", "opening_sizes=3,4,2"))
        self.assertTrue(handled)
        self.assertTrue(h.last["ok"], h.last)
        self.assertTrue(h.last["summary"]["is_custom"])
        self.assertEqual(h.last["summary"]["opening_sizes"], [3, 4, 2])
        self.assertEqual(h.last["summary"]["tournament_size"], 9)

    def test_host_swap_via_dispatch(self):
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/create"), {
            "total_capacity": 8, "players_per_match": 2, "host_name": "H", "guest_id": "sh"})
        tid = h.last["tournament_id"]
        htok = ts.MANAGER.get(tid).host_control_token
        ts.handle_post(_FakeHandler(), _FakeParsed("/api/tournament/join"),
                       {"id": tid, "name": "P1", "guest_id": "sp1"})
        t = ts.MANAGER.get(tid)
        a, b = t.participants[0].pid, t.participants[1].pid
        oa, ob = t.participants[0].join_order, t.participants[1].join_order
        h2 = _FakeHandler()
        ts.handle_post(h2, _FakeParsed("/api/tournament/host"),
                       {"id": tid, "guest_id": "sh", "host_token": htok,
                        "cmd": "swap", "pid": a, "pid_b": b})
        self.assertTrue(h2.last["ok"], h2.last)
        self.assertEqual(t._by_pid(a).join_order, ob)
        self.assertEqual(t._by_pid(b).join_order, oa)


class TestReadyCheck(unittest.TestCase):
    """Per-match READY CHECK: a match's game only launches once every assigned
    human has readied up (bots auto-ready; a disconnected human doesn't block)."""

    def setUp(self):
        self._old_create = ts._create_match_room
        self._old_start = ts._start_match_room
        self.started = []
        def fake_create(**kw):
            rid = f"R{kw['match_number']}"
            seat_tokens = {p["pid"]: f"seat-{p['pid']}"
                           for p in kw["players"] if not p.get("is_bot")}
            return {"room_id": rid, "seat_tokens": seat_tokens}
        def fake_start(room_id):
            self.started.append(room_id)
            return True
        ts._create_match_room = fake_create
        ts._start_match_room = fake_start

    def tearDown(self):
        ts._create_match_room = self._old_create
        ts._start_match_room = self._old_start

    def test_no_auto_start_before_ready(self):
        mgr, t = make(4, 2, 4, guest=False)
        self.assertTrue(t.start()["ok"])
        for m in t.bracket.rounds[0]:
            self.assertEqual(m.status, M_READY, "match spawns into the ready check")
            self.assertTrue(m.room_id, "match room is created at spawn")
        self.assertEqual(self.started, [], "no match launches until players ready up")

    def test_match_launches_only_when_all_ready(self):
        mgr, t = make(4, 2, 4, guest=False)
        t.start()
        m0 = t.bracket.rounds[0][0]
        a, b = [p for p in m0.player_ids if p]
        t.match_ready(a, True)
        self.assertEqual(m0.status, M_READY, "one player ready is not enough")
        self.assertNotIn(m0.room_id, self.started)
        t.match_ready(b, True)
        self.assertEqual(m0.status, M_ACTIVE, "both ready -> the game launches")
        self.assertIn(m0.room_id, self.started)

    def test_unready_toggles_back(self):
        mgr, t = make(4, 2, 4, guest=False)
        t.start()
        m0 = t.bracket.rounds[0][0]
        a, b = [p for p in m0.player_ids if p]
        t.match_ready(a, True)
        r = t.match_ready(a, False)
        self.assertTrue(r["ok"])
        t.match_ready(b, True)
        self.assertEqual(m0.status, M_READY, "a player who un-readied still blocks the start")
        self.assertEqual(self.started, [])

    def test_ready_roster_exposed_in_state(self):
        mgr, t = make(4, 2, 4, guest=False)
        t.start()
        m0 = t.bracket.rounds[0][0]
        a = next(p for p in m0.player_ids if p)
        mm = t.state_view(viewer_pid=a)["viewer"]["my_match"]
        self.assertIsNotNone(mm)
        self.assertEqual(mm["status"], M_READY)
        self.assertEqual(mm["total_count"], 2)
        self.assertFalse(mm["i_am_ready"])
        self.assertTrue(mm["seat_token"], "seat token delivered so the client can enter")
        t.match_ready(a, True)
        mm2 = t.state_view(viewer_pid=a)["viewer"]["my_match"]
        self.assertTrue(mm2["i_am_ready"])
        self.assertEqual(mm2["ready_count"], 1)

    def test_offline_opponent_gets_a_grace_period_then_stops_blocking(self):
        """A player who has merely gone quiet keeps their match — the game must not
        be played without them because their client missed a couple of polls. Past
        the absent grace the bracket moves on so a player who really left can't
        stall everyone."""
        mgr, t = make(4, 2, 4, guest=False)
        t.start()
        m0 = t.bracket.rounds[0][0]
        a, b = [p for p in m0.player_ids if p]
        t.match_ready(a, True)
        self.assertEqual(m0.status, M_READY)
        t._by_pid(b).last_seen = _now() - 9999          # b drops offline
        t.state_view(viewer_pid=a)                       # reaper marks b disconnected
        t.match_ready(a, True)                           # re-evaluate the gate
        self.assertEqual(m0.status, M_READY,
                         "a blip must not start someone's match without them")
        # Wind the ready check back past the grace: now it launches.
        t.match_ready_since[m0.match_number] = _now() - (ts.MATCH_ABSENT_GRACE_SEC + 5)
        t.match_ready(a, True)
        self.assertEqual(m0.status, M_ACTIVE, "a player who really left can't stall the bracket")

    def test_going_offline_arms_a_recheck_so_the_match_cant_stall(self):
        """Nothing else re-reads the ready gate once a player stops polling, so the
        reaper must schedule the re-check itself."""
        mgr, t = make(4, 2, 4, guest=False)
        t.start()
        m0 = t.bracket.rounds[0][0]
        a, b = [p for p in m0.player_ids if p]
        t.match_ready(a, True)
        t._cancel_match_timer(m0.match_number)           # start from no pending timer
        t._by_pid(b).last_seen = _now() - 9999
        t.state_view(viewer_pid=a)                        # reaper runs
        self.assertIn(m0.match_number, t.match_timers,
                      "a re-check must be scheduled for when the grace expires")
        t._cancel_match_timer(m0.match_number)

    def test_solo_human_with_bots_launches_on_ready(self):
        mgr = TournamentManager()
        cfg = TournamentConfig(4, 2, name="Solo Cup")
        t = mgr.create(cfg, "acct:host", "Host", fill_bots=True)
        for p in t.participants:
            t.set_ready(p.pid, True)
        self.assertTrue(t.start()["ok"])
        human_match = next(m for m in t.bracket.rounds[0]
                           if "acct:host" in [x for x in m.player_ids if x])
        self.assertEqual(human_match.status, M_READY)
        self.assertTrue(human_match.room_id)
        self.assertNotIn(human_match.room_id, self.started)
        t.match_ready("acct:host", True)
        self.assertEqual(human_match.status, M_ACTIVE, "human ready + bots auto-ready -> launch")


# =============================================================================
# DESIGNED (canvas-built) BRACKETS — typed player spots + host-drawn connections
# =============================================================================
from tournament_engine import (  # noqa: E402
    CustomBracket, CustomMatch, CustomSlot, make_uniform_graph,
    SLOT_OPEN, SLOT_HUMAN, SLOT_AI, SLOT_INVITE, SLOT_WINNER_FROM, SLOT_TOP_FROM,
)


def dmatch(mid, slots, advance=1, label=""):
    out = []
    for s in slots:
        if isinstance(s, tuple):
            src, rank = s
            out.append(CustomSlot(kind=SLOT_WINNER_FROM if rank == 1 else SLOT_TOP_FROM,
                                  source=src, rank=rank))
        elif isinstance(s, str) and s.startswith("invite:"):
            out.append(CustomSlot(kind=SLOT_INVITE, invite=s.split(":", 1)[1]))
        else:
            out.append(CustomSlot(kind=s))
    return CustomMatch(id=mid, slots=out, advance=advance, label=label)


def make_designed(spec, n_humans, *, guest=True, fill_bots=False, names=None):
    """Create a tournament from a designed bracket with n_humans ready players."""
    prefix = "guest:" if guest else "acct:"
    mgr = TournamentManager()
    cfg = TournamentConfig(spec.entry_count(), spec.max_capacity(),
                           custom_graph=spec.to_dict(), name="Designed Cup")
    t = mgr.create(cfg, prefix + "host", (names or {}).get(0, "Host"), fill_bots=fill_bots)
    for i in range(1, n_humans):
        nm = (names or {}).get(i, f"P{i:02d}")
        r = t.join(f"{prefix}p{i:02d}", nm, is_guest=guest)
        assert r["ok"], r
    for p in t.participants:
        t.set_ready(p.pid, True)
    return mgr, t


class TestDesignedBracketServer(unittest.TestCase):
    def test_create_and_seat_plan(self):
        mgr, t = make_designed(make_uniform_graph([2, 2, 2, 2]), 8)
        self.assertTrue(t.cfg.is_graph)
        self.assertEqual(t.cfg.total_capacity, 8)
        self.assertEqual(t.human_capacity, 8)
        seats = t.seat_view()
        self.assertEqual(len(seats), 8)
        self.assertTrue(all(s["pid"] for s in seats))

    def test_ai_only_spots_are_not_joinable_by_people(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_AI]),
            dmatch("b", [SLOT_OPEN, SLOT_AI]),
            dmatch("f", [("a", 1), ("b", 1)]),
        ])
        mgr, t = make_designed(spec, 2)
        self.assertEqual(t.cfg.total_capacity, 4)
        self.assertEqual(t.human_capacity, 2)      # the two AI spots are off-limits
        r = t.join("guest:extra", "Extra")
        self.assertTrue(r.get("spectator"), "a 3rd person must not take an AI spot")

    def test_ai_only_spots_do_not_block_the_start_button(self):
        # An AI-only spot is the host saying "a bot plays here" — start() fills it,
        # so an empty one must never gate the Start button.
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("b", [SLOT_OPEN, SLOT_AI]),
            dmatch("f", [("a", 1), ("b", 1)]),
        ])
        mgr, t = make_designed(spec, 3, fill_bots=False)
        ok, why = t.can_start()
        self.assertTrue(ok, why)
        self.assertTrue(t.start()["ok"])

    def test_ai_spots_are_filled_at_start_without_bot_fill(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_AI]),
            dmatch("b", [SLOT_OPEN, SLOT_AI]),
            dmatch("f", [("a", 1), ("b", 1)]),
        ])
        mgr, t = make_designed(spec, 2, fill_bots=False)
        self.assertTrue(t.start()["ok"], t.can_start())
        seated = [pid for row in t.bracket.rounds for m in row for pid in m.player_ids if pid]
        self.assertEqual(len(seated), 4)
        self.assertEqual(sum(1 for p in t.participants if p.is_bot), 2)

    def test_human_only_spot_blocks_start_until_a_person_takes_it(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_HUMAN, SLOT_HUMAN]),
            dmatch("b", [SLOT_HUMAN, SLOT_HUMAN]),
            dmatch("f", [("a", 1), ("b", 1)]),
        ])
        mgr, t = make_designed(spec, 3, fill_bots=True)
        t.fill_with_bots()
        ok, why = t.can_start()
        self.assertFalse(ok)
        self.assertIn("player-only", why)
        r = t.join("guest:p03", "P03")
        self.assertTrue(r["ok"])
        for p in t.participants:
            t.set_ready(p.pid, True)
        self.assertTrue(t.can_start()[0], t.can_start()[1])

    def test_bot_fill_takes_open_spots_but_never_human_only(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_HUMAN, SLOT_OPEN]),
            dmatch("b", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("f", [("a", 1), ("b", 1)]),
        ])
        mgr, t = make_designed(spec, 1)          # just the host
        t.fill_with_bots()
        empty = t._empty_seats()
        self.assertEqual(len(empty), 0, "host takes the human-only spot, bots take the rest")
        self.assertEqual(sum(1 for p in t.participants if p.is_bot), 3)

    def test_invite_spot_goes_to_the_named_player(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, "invite:Kelp"]),
            dmatch("b", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("f", [("a", 1), ("b", 1)]),
        ])
        mgr, t = make_designed(spec, 4, names={1: "Coral", 2: "Kelp", 3: "Reef"})
        seats = {s["seat"]: s for s in t.seat_view()}
        invite_seat = next(s for s in seats.values() if s["kind"] == SLOT_INVITE)
        self.assertEqual(invite_seat["name"], "Kelp")

    def test_invited_player_missing_blocks_start(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, "invite:Kelp"]),
            dmatch("b", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("f", [("a", 1), ("b", 1)]),
        ])
        mgr, t = make_designed(spec, 3, names={1: "Coral", 2: "Reef"})
        ok, why = t.can_start()
        self.assertFalse(ok)
        self.assertIn("Kelp", why)
        # the host can hand that spot to a bot instead
        t.fill_with_bots()
        for p in t.participants:
            t.set_ready(p.pid, True)
        self.assertTrue(t.can_start()[0], t.can_start()[1])

    def test_lobby_preview_uses_the_designed_shape(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_OPEN], label="Play-in"),
            dmatch("b", [("a", 1), SLOT_OPEN], label="Semifinal"),
            dmatch("c", [SLOT_OPEN, SLOT_OPEN], label="Semifinal"),
            dmatch("f", [("b", 1), ("c", 1)], label="Grand Final"),
        ])
        mgr, t = make_designed(spec, 3)
        st = t.state_view(viewer_pid="guest:host")
        br = st["bracket"]
        self.assertTrue(br["preview"])
        self.assertEqual(br["n_rounds"], 3)
        self.assertEqual(br["rounds"][-1][0]["label"], "Grand Final")
        self.assertEqual(st["summary"]["num_rounds"], 3)
        self.assertEqual(len(st["seats"]), 5)

    def test_state_view_exposes_spot_types(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_AI]),
            dmatch("b", [SLOT_OPEN, "invite:Kelp"]),
            dmatch("f", [("a", 1), ("b", 1)]),
        ])
        mgr, t = make_designed(spec, 2)
        st = t.state_view(viewer_pid="guest:host")
        kinds = st["bracket"]["rounds"][0][0]["slot_kinds"]
        self.assertEqual(kinds[1]["kind"], SLOT_AI)
        self.assertEqual(st["human_capacity"], 3)

    def test_full_designed_run_reaches_a_champion(self):
        mgr, t = make_designed(make_uniform_graph([2, 2, 2, 2]), 8)
        self.assertTrue(t.start()["ok"], t.can_start())
        play_out(t)
        self.assertEqual(t.phase, Tournament.PHASE_COMPLETE)
        self.assertTrue(t.champion_pid)
        self.assertEqual(len(t.final_placements), 8)

    def test_group_stage_top_two_advance(self):
        spec = CustomBracket(matches=[
            dmatch("g1", [SLOT_OPEN] * 4, advance=2, label="Group A"),
            dmatch("g2", [SLOT_OPEN] * 4, advance=2, label="Group B"),
            dmatch("f", [("g1", 1), ("g1", 2), ("g2", 1), ("g2", 2)], label="Final"),
        ])
        mgr, t = make_designed(spec, 8)
        self.assertTrue(t.start()["ok"], t.can_start())
        self.assertEqual(t.bracket.n_rounds, 2)
        play_out(t)
        self.assertEqual(t.phase, Tournament.PHASE_COMPLETE)
        self.assertTrue(t.champion_pid)
        # exactly four players reached the Final
        self.assertEqual(sum(1 for p in t.bracket.final_match.player_ids if p), 4)

    def test_designed_bracket_with_a_late_ai_seat_runs(self):
        # The Final holds the two winners plus a bot that skipped straight to it.
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("b", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("f", [("a", 1), ("b", 1), SLOT_AI], label="Final"),
        ])
        mgr, t = make_designed(spec, 4)
        self.assertTrue(t.start()["ok"], t.can_start())
        bot_pid = next(p.pid for p in t.participants if p.is_bot)
        self.assertIn(bot_pid, t.bracket.final_match.player_ids)
        play_out(t)
        self.assertEqual(t.phase, Tournament.PHASE_COMPLETE)

    def test_a_bot_filled_lobby_still_lets_real_players_in(self):
        # The host tops the lobby up with AI, then a friend arrives — a bot must
        # step aside rather than turn a real player into a spectator.
        mgr, t = make_designed(make_uniform_graph([2, 2]), 1)
        t.fill_with_bots()
        self.assertEqual(len(t.participants), 4)
        r = t.join("guest:friend", "Friend")
        self.assertTrue(r["ok"])
        self.assertFalse(r.get("spectator"), "a bot should have made way")
        self.assertIn("Friend", [p.name for p in t.participants])
        self.assertEqual(len(t.participants), 4)
        self.assertEqual(sum(1 for p in t.participants if p.is_bot), 2)

    def test_bots_holding_ai_only_spots_are_never_evicted(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_AI]),
            dmatch("b", [SLOT_OPEN, SLOT_AI]),
            dmatch("f", [("a", 1), ("b", 1)]),
        ])
        mgr, t = make_designed(spec, 1)
        t.fill_with_bots()
        self.assertEqual(sum(1 for p in t.participants if p.is_bot), 3)
        r = t.join("guest:friend", "Friend")     # takes the second open spot
        self.assertTrue(r["ok"])
        self.assertFalse(r.get("spectator"))
        r2 = t.join("guest:third", "Third")      # only AI-only spots remain
        self.assertTrue(r2.get("spectator"), "AI-only spots are the host's design, not filler")
        self.assertEqual(sum(1 for p in t.participants if p.is_bot), 2)

    def test_uniform_bot_filled_lobby_also_lets_players_in(self):
        mgr = TournamentManager()
        t = mgr.create(TournamentConfig(4, 2, name="Cup"), "guest:host", "Host", fill_bots=True)
        t.fill_with_bots()
        self.assertEqual(len(t.participants), 4)
        r = t.join("guest:friend", "Friend")
        self.assertTrue(r["ok"])
        self.assertFalse(r.get("spectator"))

    def test_seat_types_are_respected_after_arranging(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_HUMAN, SLOT_AI]),
            dmatch("b", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("f", [("a", 1), ("b", 1)]),
        ])
        mgr, t = make_designed(spec, 3)
        t.fill_with_bots()
        t.randomize(rng_seed=5)
        for s, pid in zip(t.seat_slots(), t.assign_seats()):
            p = t._by_pid(pid)
            self.assertIsNotNone(p)
            if s["kind"] == SLOT_AI:
                self.assertTrue(p.is_bot, "a person must never be seated in an AI-only spot")
            if s["kind"] == SLOT_HUMAN:
                self.assertFalse(p.is_bot, "a bot must never be seated in a player-only spot")

    def test_host_swap_moves_players_between_designed_spots(self):
        mgr, t = make_designed(make_uniform_graph([2, 2]), 4)
        seats = t.seat_view()
        a, b = seats[0]["pid"], seats[3]["pid"]
        self.assertTrue(t.host_swap(a, b)["ok"])
        seats2 = t.seat_view()
        self.assertEqual(seats2[0]["pid"], b)
        self.assertEqual(seats2[3]["pid"], a)


class TestDesignedHttpDispatch(unittest.TestCase):
    def _graph_body(self, spec, **kw):
        body = {"custom_graph": spec.to_dict(), "name": "Designed Cup",
                "host_name": "Hosty", "guest_id": "dh"}
        body.update(kw)
        return body

    def test_validate_endpoint_accepts_a_good_design(self):
        h = _FakeHandler()
        handled = ts.handle_post(h, _FakeParsed("/api/tournament/validate"),
                                 {"custom_graph": make_uniform_graph([2, 2, 2, 2]).to_dict()})
        self.assertTrue(handled)
        self.assertTrue(h.last["valid"], h.last)
        self.assertEqual(h.last["errors"], [])
        self.assertEqual(h.last["summary"]["tournament_size"], 8)

    def test_validate_endpoint_explains_a_broken_design(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("b", [SLOT_OPEN, SLOT_OPEN]),
        ])
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/validate"), {"custom_graph": spec.to_dict()})
        self.assertFalse(h.last["valid"])
        self.assertTrue(h.last["errors"])

    def test_validate_endpoint_handles_an_empty_canvas(self):
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/validate"), {"custom_graph": {"matches": []}})
        self.assertFalse(h.last["valid"])
        self.assertTrue(h.last["errors"])

    def test_create_designed_via_dispatch(self):
        spec = make_uniform_graph([3, 4, 2])
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/create"), self._graph_body(spec))
        self.assertTrue(h.last["ok"], h.last)
        self.assertTrue(h.last["designed"])
        t = ts.MANAGER.get(h.last["tournament_id"])
        self.assertTrue(t.cfg.is_graph)
        self.assertEqual(t.cfg.total_capacity, 9)
        self.assertEqual(t.cfg.players_per_match, 4)
        self.assertEqual(t.cfg.name, "Designed Cup")
        self.assertEqual(t.participants[0].name, "Hosty")

    def test_create_rejects_a_broken_design(self):
        from http import HTTPStatus
        spec = CustomBracket(matches=[dmatch("a", [SLOT_OPEN, SLOT_OPEN])])
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/create"), self._graph_body(spec, guest_id="dh2"))
        self.assertFalse(h.last["ok"])
        self.assertEqual(h.status, int(HTTPStatus.BAD_REQUEST))
        self.assertTrue(h.last["errors"])

    def test_create_accepts_a_bare_match_list(self):
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/create"),
                       self._graph_body(make_uniform_graph([2, 2]), guest_id="dh3",
                                        custom_graph=make_uniform_graph([2, 2]).to_dict()["matches"]))
        self.assertTrue(h.last["ok"], h.last)
        t = ts.MANAGER.get(h.last["tournament_id"])
        self.assertEqual(t.cfg.total_capacity, 4)

    def test_designed_tournament_listed_publicly(self):
        h = _FakeHandler()
        ts.handle_post(h, _FakeParsed("/api/tournament/create"),
                       self._graph_body(make_uniform_graph([2, 2]), guest_id="dh4"))
        tid = h.last["tournament_id"]
        row = next(r for r in ts.MANAGER.list_public() if r["tournament_id"] == tid)
        self.assertTrue(row["is_graph"])
        self.assertEqual(row["capacity"], 4)
        self.assertEqual(row["human_capacity"], 4)


class TestBotMatchesArePlayed(unittest.TestCase):
    """Every bracket match is decided by a real game — including all-AI matches.

    They used to be resolved instantly by a coin flip, so the moment the last human
    was knocked out of a bot-filled bracket the server sprinted through the
    remaining rounds and crowned a champion: "it didn't let me play the last two
    games, it just decided the winner".
    """

    def setUp(self):
        self._old_create = ts._create_match_room
        self._old_start = ts._start_match_room
        self.rooms = []       # (match_number, [pids])
        self.started = []

        def fake_create(**kw):
            self.rooms.append((kw["match_number"], [p["pid"] for p in kw["players"]]))
            return {"room_id": f"R{kw['match_number']}",
                    "seat_tokens": {p["pid"]: f"seat-{p['pid']}"
                                    for p in kw["players"] if not p.get("is_bot")}}

        def fake_start(room_id):
            self.started.append(room_id)
            return True

        ts._create_match_room = fake_create
        ts._start_match_room = fake_start

    def tearDown(self):
        ts._create_match_room = self._old_create
        ts._start_match_room = self._old_start

    def _solo_bracket(self, cap=8, ppm=2):
        mgr = TournamentManager()
        cfg = TournamentConfig(cap, ppm, name="Solo Cup")
        t = mgr.create(cfg, "acct:host", "Host", fill_bots=True)
        for p in t.participants:
            t.set_ready(p.pid, True)
        self.assertTrue(t.start()["ok"])
        return t

    def test_all_bot_matches_get_real_rooms_and_launch_themselves(self):
        t = self._solo_bracket()
        bot_matches = [m for m in t.bracket.rounds[0] if t._match_is_all_bots(m)]
        self.assertTrue(bot_matches, "a solo bot-filled bracket has all-bot matches")
        spawned = [num for num, _ in self.rooms]
        for m in bot_matches[:ts.MAX_CONCURRENT_BOT_MATCHES]:
            self.assertIn(m.match_number, spawned, "an all-bot match must get a real game")
            self.assertEqual(m.status, M_ACTIVE, "nobody to ready up: it launches at once")
        self.assertNotIn(M_COMPLETE, [m.status for m in bot_matches],
                         "no all-bot match is decided without its game being played")

    def test_bot_games_are_capped_and_queue_behind_each_other(self):
        t = self._solo_bracket(cap=32, ppm=2)
        in_flight = [m for m in t.bracket.all_matches()
                     if m.status in (M_READY, M_ACTIVE) and t._match_is_all_bots(m)]
        self.assertLessEqual(len(in_flight), ts.MAX_CONCURRENT_BOT_MATCHES,
                             "a 32-seat AI bracket must not start a dozen games at once")
        self.assertTrue(in_flight, "but it does start some")
        # As one reports, the next in the queue starts.
        m = in_flight[0]
        pids = [p for p in m.player_ids if p]
        before = len(self.rooms)
        t.report_match_result(m.round_index, m.match_index, pids,
                              {p: 10 * (len(pids) - i) for i, p in enumerate(pids)})
        self.assertGreater(len(self.rooms), before, "the next queued bot game starts")

    def test_the_human_never_waits_behind_a_bot_game(self):
        t = self._solo_bracket(cap=32, ppm=2)
        human_match = next(m for m in t.bracket.rounds[0]
                           if "acct:host" in [x for x in m.player_ids if x])
        self.assertIn(human_match.match_number, [n for n, _ in self.rooms],
                      "the human's own match is spawned immediately, cap or no cap")

    def test_champion_is_not_crowned_while_a_match_is_unplayed(self):
        t = self._solo_bracket()
        self.assertNotEqual(t.phase, ts.Tournament.PHASE_COMPLETE)
        # Play the whole bracket out; only then may it complete.
        guard = 0
        while t.phase == ts.Tournament.PHASE_RUNNING and guard < 200:
            guard += 1
            acted = False
            for m in list(t.bracket.all_matches()):
                if m.status == M_READY:
                    for pid in [x for x in m.player_ids if x]:
                        p = t._by_pid(pid)
                        if p and not p.is_bot:
                            t.match_ready(pid, True)
                            acted = True
                if m.status == M_ACTIVE:
                    pids = [x for x in m.player_ids if x]
                    self.assertFalse(t.bracket.is_complete(),
                                     "not complete while a game is still running")
                    t.report_match_result(m.round_index, m.match_index, pids,
                                          {p: 10 * (len(pids) - i) for i, p in enumerate(pids)})
                    acted = True
            if not acted:
                break
        self.assertEqual(t.phase, ts.Tournament.PHASE_COMPLETE, "and it does finish")
        self.assertEqual(t.bracket.unresolved_matches(), [])
        places = sorted(e["place"] for e in t.final_placements)
        self.assertEqual(places, list(range(1, len(t.participants) + 1)),
                         "every participant gets exactly one distinct place")

    def test_fallback_resolves_instantly_when_real_bot_games_are_off(self):
        old = ts.PLAY_BOT_MATCHES
        ts.PLAY_BOT_MATCHES = False
        try:
            t = self._solo_bracket()
            bot_matches = [m for m in t.bracket.rounds[0] if t._match_is_all_bots(m)]
            self.assertTrue(all(m.status == M_COMPLETE for m in bot_matches),
                            "kill switch restores the old instant resolution")
        finally:
            ts.PLAY_BOT_MATCHES = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
