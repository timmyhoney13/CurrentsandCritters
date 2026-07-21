#!/usr/bin/env python3
"""Server-orchestration tests for tournament_server.py (headless — no Firestore,
no real game rooms). Drives the Tournament object end to end via its public API.

Run:  python3 -m unittest test_tournament_server -v
"""

import unittest

import tournament_server as ts
from tournament_server import (
    TournamentManager, Tournament, TournamentConfig,
    S_READY, S_NOT_READY, S_WAITING, S_ELIMINATED, S_CHAMPION, S_PLAYING,
)
from tournament_engine import M_READY, M_COMPLETE, M_BYE


def make(cap, ppm, n, *, third_place=False):
    """Create a tournament, fill it with n ready players, return (mgr, t)."""
    mgr = TournamentManager()
    cfg = TournamentConfig(cap, ppm, third_place_match=third_place, name="Test Cup")
    t = mgr.create(cfg, "guest:host", "Host")
    # host is participant 0; add n-1 more
    for i in range(1, n):
        r = t.join(f"guest:p{i:02d}", f"P{i:02d}")
        assert r["ok"], r
    # everyone ready
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
        mgr, t = make(32, 2, 32)
        t.start()
        play_out(t)
        champ = t._by_pid(t.champion_pid)
        bd = t._xp_grants.get(champ.pid) or t.compute_xp_for(champ)
        kinds = {it["kind"]: it["amount"] for it in bd["items"]}
        self.assertEqual(kinds.get("champion"), 3000)  # full 32-player scale
        self.assertEqual(bd["total_xp"], sum(it["amount"] for it in bd["items"]))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
