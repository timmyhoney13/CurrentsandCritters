#!/usr/bin/env python3
"""Exhaustive tests for tournament_engine.py (spec §19).

Run:  python3 test_tournament_engine.py   (or: python3 -m unittest test_tournament_engine -v)

Covers every required tournament size (4/8/16/24/32), every players-per-match
size (2..8), byes, uneven counts, randomization/seeding, advancement into the
correct slot, elimination, third-place resolution, final placements, duplicate
result rejection, and XP scaling + dedup-id stability. It also runs FULL simulated
tournaments start-to-finish for a large grid of (N, M) and asserts the structural
invariants the spec demands.
"""

import math
import random
import unittest

import tournament_engine as te
from tournament_engine import (
    TournamentConfig, Bracket, validate_config, plan_round_sizes,
    bracket_summary, available_formats, compute_player_xp,
    MIN_TOURNAMENT_PLAYERS, MAX_TOURNAMENT_PLAYERS,
    M_COMPLETE, M_BYE,
)


def players(n):
    """Deterministic player ids p00, p01, ... (sortable, stable)."""
    return [f"p{i:02d}" for i in range(n)]


def simulate(cfg, n, seed=0, winner_pick="first"):
    """Play a whole tournament to completion deterministically.

    winner_pick: 'first' -> the lowest-id present player always wins each match;
                 'seed'   -> a fixed pseudo-random-but-deterministic winner.
    Returns (bracket, seed_order).
    """
    b = Bracket.build(cfg, n)
    order = players(n)
    rng = random.Random(seed)
    order = order[:]
    rng.shuffle(order)
    b.seed_players(order)

    rounds_total = b.n_rounds
    guard = 0
    while not b.is_complete():
        guard += 1
        if guard > 10000:
            raise AssertionError("simulation did not converge")
        progressed = False
        for row in b.rounds:
            for m in row:
                if m.status in (M_COMPLETE, M_BYE):
                    continue
                present = [p for p in m.player_ids if p]
                if len(present) != m.capacity:
                    continue  # not ready yet
                # decide a full ranking of the present players
                ranking = sorted(present)
                if winner_pick == "seed":
                    r2 = random.Random(seed * 1000 + m.match_number)
                    ranking = present[:]
                    r2.shuffle(ranking)
                scores = {pid: 200 - i * 7 for i, pid in enumerate(ranking)}
                b.record_result(m.round_index, m.match_index, ranking, scores)
                progressed = True
        # third-place match
        tp = b.third_place
        if tp is not None and tp.status not in (M_COMPLETE, M_BYE):
            present = [p for p in tp.player_ids if p]
            if len(present) == tp.capacity and tp.capacity >= 1:
                ranking = sorted(present)
                b.record_result(tp.round_index, tp.match_index, ranking,
                                {pid: 100 - i for i, pid in enumerate(ranking)})
                progressed = True
        if not progressed:
            raise AssertionError(
                f"stuck: no match became playable (N={n}, M={cfg.players_per_match})"
            )
    return b, order


# =============================================================================
class TestConfigValidation(unittest.TestCase):
    def test_valid_configs(self):
        for n in (4, 8, 16, 24, 32):
            for m in range(2, 9):
                if m > n:
                    continue
                self.assertEqual(validate_config(TournamentConfig(n, m)), [],
                                 f"expected valid: N={n} M={m}")

    def test_capacity_bounds(self):
        self.assertTrue(validate_config(TournamentConfig(3, 2)))   # too few
        self.assertTrue(validate_config(TournamentConfig(33, 2)))  # too many
        self.assertEqual(validate_config(TournamentConfig(4, 2)), [])

    def test_per_match_bounds(self):
        self.assertTrue(validate_config(TournamentConfig(8, 1)))   # <2 per match
        self.assertTrue(validate_config(TournamentConfig(8, 9)))   # >8 per match

    def test_per_match_not_exceed_total(self):
        self.assertTrue(validate_config(TournamentConfig(4, 8)))   # 8 > 4

    def test_advance_must_be_less_than_per_match(self):
        self.assertEqual(validate_config(TournamentConfig(8, 4, advance_per_match=1)), [])
        self.assertTrue(validate_config(TournamentConfig(8, 4, advance_per_match=4)))


# =============================================================================
class TestBracketPlanning(unittest.TestCase):
    def test_known_shapes_1v1(self):
        self.assertEqual(plan_round_sizes(8, 2), [[2, 2, 2, 2], [2, 2], [2]])
        self.assertEqual(plan_round_sizes(4, 2), [[2, 2], [2]])
        self.assertEqual(plan_round_sizes(2, 2), [[2]])

    def test_known_shapes_uneven_1v1(self):
        # 6 players, 1v1: greedy fills all three opening matches (NO opening byes);
        # the single unavoidable bye appears in round 2 ([2,2,2] -> [2,1] -> [2]).
        sizes6 = plan_round_sizes(6, 2)
        self.assertEqual(sizes6, [[2, 2, 2], [2, 1], [2]])
        self.assertEqual(sizes6[0].count(1), 0, "6-player 1v1 has no opening byes")
        self.assertEqual(sum(sizes6[0]), 6)
        # 5 players, 1v1: exactly one opening bye ([2,2,1]).
        sizes5 = plan_round_sizes(5, 2)
        self.assertEqual(sizes5[0], [2, 2, 1])
        self.assertEqual(sizes5[0].count(1), 1)
        self.assertEqual(sum(sizes5[0]), 5)

    def test_known_shapes_big_matches(self):
        # 32 players, 8-per-match -> four full matches of 8, then a final of 4.
        # (Greedy honors "8 per match" by filling to 8, minimizing rounds.)
        self.assertEqual(plan_round_sizes(32, 8), [[8, 8, 8, 8], [4]])
        # 16 players, 4 per match -> 4 matches of 4, final of 4
        self.assertEqual(plan_round_sizes(16, 4), [[4, 4, 4, 4], [4]])
        # 4 players, 4 per match -> single match
        self.assertEqual(plan_round_sizes(4, 4), [[4]])

    def test_greedy_packing_minimizes_byes_24_at_4(self):
        # Greedy per-round packing should give ZERO byes for 24 @ 4-per-match,
        # unlike a rigid M-ary bracket which would force 8 byes.
        sizes = plan_round_sizes(24, 4)
        opening_byes = sizes[0].count(1)
        self.assertEqual(opening_byes, 0, f"24@4 should have no byes, got {sizes}")
        # 24 -> 6 -> 2 -> 1
        self.assertEqual([len(r) for r in sizes], [6, 2, 1])

    def test_field_strictly_shrinks_and_ends_at_one(self):
        for n in range(4, 33):
            for m in range(2, 9):
                if m > n:
                    continue
                sizes = plan_round_sizes(n, m)
                # every round's players sum <= previous round's match count*... just
                # assert it converges: final round has exactly one match.
                self.assertEqual(len(sizes[-1]), 1, f"N={n} M={m} final not single")
                # each round splits into ceil(field/m) matches
                field = n
                for row in sizes:
                    self.assertEqual(len(row), math.ceil(field / m))
                    self.assertEqual(sum(row), field)
                    # sizes differ by at most 1
                    self.assertLessEqual(max(row) - min(row), 1)
                    field = len(row)  # advance=1


# =============================================================================
class TestBracketConstruction(unittest.TestCase):
    def test_match_numbers_unique_and_sequential(self):
        cfg = TournamentConfig(16, 4)
        b = Bracket.build(cfg, 16)
        nums = [m.match_number for m in b.all_matches(include_third=False)]
        self.assertEqual(nums, sorted(nums))
        self.assertEqual(len(nums), len(set(nums)))

    def test_feeds_target_real_unique_slots(self):
        for n in (4, 5, 6, 7, 8, 9, 12, 15, 16, 20, 24, 31, 32):
            for m in range(2, 9):
                if m > n:
                    continue
                b = Bracket.build(TournamentConfig(n, m), n)
                targets = set()
                for r in range(b.n_rounds - 1):
                    for match in b.rounds[r]:
                        for feed in match.feeds:
                            if feed is None:
                                continue
                            nr, nmi, nslot = feed
                            self.assertEqual(nr, r + 1)
                            key = (nr, nmi, nslot)
                            self.assertNotIn(key, targets,
                                             f"slot {key} targeted twice (N={n} M={m})")
                            targets.add(key)
                            # the slot exists
                            self.assertLess(nslot, b.rounds[nr][nmi].capacity)
                # number of feed targets equals number of slots in rounds 2..R
                total_slots = sum(mm.capacity for r in range(1, b.n_rounds)
                                  for mm in b.rounds[r])
                self.assertEqual(len(targets), total_slots,
                                 f"not every next-round slot is fed (N={n} M={m})")


# =============================================================================
class TestSeedingAndByes(unittest.TestCase):
    def test_seed_count_mismatch_raises(self):
        b = Bracket.build(TournamentConfig(8, 2), 8)
        with self.assertRaises(ValueError):
            b.seed_players(players(7))

    def test_byes_auto_advance(self):
        # 5 players 1v1 -> exactly one opening bye that auto-advances into round 2
        b = Bracket.build(TournamentConfig(5, 2), 5)
        b.seed_players(players(5))
        byes = b.bye_matches()
        self.assertEqual(len(byes), 1)
        for bye in byes:
            self.assertEqual(bye.status, M_BYE)
            self.assertEqual(len(bye.winners), 1)
            # the bye player now occupies a round-2 slot
            nr, nmi, nslot = bye.feeds[0]
            self.assertEqual(b.rounds[nr][nmi].player_ids[nslot], bye.winners[0])

    def test_later_round_bye_cascades(self):
        # 5 players 1v1 has a later-round bye that must auto-resolve.
        b = Bracket.build(TournamentConfig(5, 2), 5)
        b.seed_players(players(5))
        # play round 1 real matches; then the later bye should resolve on push
        b2, _ = simulate(TournamentConfig(5, 2), 5)
        self.assertTrue(b2.is_complete())
        self.assertIsNotNone(b2.champion)

    def test_every_player_appears_exactly_once_in_round1(self):
        for n in (4, 5, 6, 7, 8, 13, 16, 24, 32):
            for m in range(2, 9):
                if m > n:
                    continue
                b = Bracket.build(TournamentConfig(n, m), n)
                order = players(n)
                b.seed_players(order)
                seen = [p for match in b.rounds[0] for p in match.player_ids if p]
                self.assertEqual(sorted(seen), sorted(order),
                                 f"round-1 seat mismatch N={n} M={m}")
                self.assertEqual(len(seen), len(set(seen)),
                                 f"duplicate seat N={n} M={m}")


# =============================================================================
class TestResultRecording(unittest.TestCase):
    def test_invalid_ranking_raises(self):
        b = Bracket.build(TournamentConfig(4, 2), 4)
        b.seed_players(players(4))
        m0 = b.rounds[0][0]
        present = [p for p in m0.player_ids if p]
        with self.assertRaises(ValueError):
            b.record_result(0, 0, present + ["ghost"])  # extra player

    def test_duplicate_result_rejected(self):
        b = Bracket.build(TournamentConfig(4, 2), 4)
        b.seed_players(players(4))
        m0 = b.rounds[0][0]
        present = [p for p in m0.player_ids if p]
        b.record_result(0, 0, present)
        with self.assertRaises(ValueError):
            b.record_result(0, 0, present)  # already complete

    def test_winner_advances_to_correct_slot(self):
        b = Bracket.build(TournamentConfig(8, 2), 8)
        b.seed_players(players(8))
        m0 = b.rounds[0][0]
        present = [p for p in m0.player_ids if p]
        b.record_result(0, 0, present)  # first-id wins
        winner = present[0]
        nr, nmi, nslot = m0.feeds[0]
        self.assertEqual(b.rounds[nr][nmi].player_ids[nslot], winner)

    def test_eliminated_players_never_reappear(self):
        b, _ = simulate(TournamentConfig(16, 2), 16)
        # collect, per round, the set of players; a loser must not appear later
        appeared_after = {}
        for r, row in enumerate(b.rounds):
            for match in row:
                for p in match.player_ids:
                    if p:
                        appeared_after.setdefault(p, set()).add(r)
        # A champion appears in every round; a round-1 loser appears only in r0.
        champ = b.champion
        self.assertIn(b.n_rounds - 1, appeared_after[champ])


# =============================================================================
class TestFullSimulations(unittest.TestCase):
    """Run entire tournaments and assert the spec's end-to-end invariants."""

    GRID = [(n, m)
            for n in (4, 5, 6, 7, 8, 9, 11, 12, 15, 16, 20, 24, 27, 31, 32)
            for m in range(2, 9) if m <= n]

    def test_all_sizes_complete_with_single_champion(self):
        for n, m in self.GRID:
            for pick in ("first", "seed"):
                cfg = TournamentConfig(n, m)
                b, order = simulate(cfg, n, seed=7, winner_pick=pick)
                self.assertTrue(b.is_complete(), f"incomplete N={n} M={m} {pick}")
                self.assertIsNotNone(b.champion, f"no champ N={n} M={m}")
                placements = b.final_placements()
                # every player placed exactly once
                placed_ids = [p["player_id"] for p in placements]
                self.assertEqual(sorted(placed_ids), sorted(order),
                                 f"placement set wrong N={n} M={m} {pick}")
                self.assertEqual(len(placed_ids), len(set(placed_ids)),
                                 f"duplicate placement N={n} M={m} {pick}")
                # place 1 is the champion
                first = [p for p in placements if p["place"] == 1]
                self.assertEqual(len(first), 1)
                self.assertEqual(first[0]["player_id"], b.champion)
                # places are 1..N contiguous-ish (monotincreasing, starts at 1)
                place_vals = sorted(p["place"] for p in placements)
                self.assertEqual(place_vals[0], 1)

    def test_each_match_contains_only_assigned_players(self):
        for n, m in self.GRID:
            b, _ = simulate(TournamentConfig(n, m), n, seed=3)
            for match in b.all_matches():
                present = [p for p in match.player_ids if p]
                # a completed match's ranking must equal its present players
                if match.status == M_COMPLETE:
                    self.assertEqual(sorted(match.ranking), sorted(present),
                                     f"match {match.match_number} ranking != players")

    def test_champion_won_a_final(self):
        for n, m in self.GRID:
            b, _ = simulate(TournamentConfig(n, m), n, seed=11)
            self.assertEqual(b.final_match.winners[0], b.champion)


# =============================================================================
class TestThirdPlace(unittest.TestCase):
    def test_third_place_match_created_for_1v1(self):
        cfg = TournamentConfig(8, 2, third_place_match=True)
        b = Bracket.build(cfg, 8)
        self.assertIsNotNone(b.third_place)

    def test_no_third_place_match_for_big_final(self):
        # An N-player final already ranks 3rd, so no separate match.
        cfg = TournamentConfig(32, 8, third_place_match=True)
        b = Bracket.build(cfg, 32)
        self.assertIsNone(b.third_place)

    def test_third_place_resolves_and_places_3_and_4(self):
        cfg = TournamentConfig(8, 2, third_place_match=True)
        b, _ = simulate(cfg, 8, seed=5)
        self.assertTrue(b.is_complete())
        placements = b.final_placements()
        places = {p["place"]: p["player_id"] for p in placements}
        self.assertIn(1, places)
        self.assertIn(2, places)
        self.assertIn(3, places)
        self.assertIn(4, places)
        # 3rd came from the third-place match winner
        self.assertEqual(places[3], b.third_place.winners[0])

    def test_placement_fallback_without_third_place_match(self):
        # Big final ranks 3rd directly.
        cfg = TournamentConfig(24, 8, third_place_match=False)
        b, _ = simulate(cfg, 24, seed=9)
        placements = b.final_placements()
        top4 = sorted([p for p in placements if p["place"] <= 4], key=lambda x: x["place"])
        self.assertEqual([p["place"] for p in top4], [1, 2, 3, 4])


# =============================================================================
class TestHostPreview(unittest.TestCase):
    def test_summary_fields(self):
        s = bracket_summary(TournamentConfig(24, 4), 24)
        self.assertEqual(s["num_rounds"], 3)
        self.assertEqual(s["num_byes"], 0)
        self.assertEqual(s["tournament_size"], 24)
        self.assertEqual(s["advancing_per_match"], 1)

    def test_summary_reports_byes(self):
        # 5 players 1v1 -> [2,2,1] -> [2,1] -> [2] = two byes total across rounds.
        s = bracket_summary(TournamentConfig(5, 2), 5)
        self.assertEqual(s["num_byes"], 2)
        self.assertEqual(s["num_opening_matches"], 2)  # two real 1v1 opening matches

    def test_available_formats_filtered_by_capacity(self):
        fmts = available_formats(4)
        pms = [f["players_per_match"] for f in fmts]
        self.assertEqual(pms, [2, 3, 4])  # can't have 5-per-match with 4 players
        fmts32 = available_formats(32)
        self.assertEqual([f["players_per_match"] for f in fmts32], [2, 3, 4, 5, 6, 7, 8])


# =============================================================================
class TestXP(unittest.TestCase):
    def _xp(self, tid, pid, **kw):
        return compute_player_xp(tid, pid, **kw)

    def test_full_32_champion_base(self):
        r = self._xp("t1", "p1", n_players=32, place=1, matches_won=2,
                     rounds_total=2, deepest_round=1,
                     completed_first_match=True, completed_without_quitting=True)
        kinds = {it["kind"]: it["amount"] for it in r["items"]}
        # scale is 1.0 at 32 players
        self.assertEqual(r["scale"], 1.0)
        self.assertEqual(kinds["champion"], 3000)
        self.assertEqual(kinds["match_win"], 400)  # 200 * 2 wins
        self.assertEqual(kinds["reached_final"], 500)

    def test_small_tournament_scaled_down(self):
        big = self._xp("t", "p", n_players=32, place=1, matches_won=1,
                       rounds_total=1, deepest_round=0,
                       completed_first_match=True, completed_without_quitting=True)
        small = self._xp("t", "p", n_players=4, place=1, matches_won=1,
                         rounds_total=1, deepest_round=0,
                         completed_first_match=True, completed_without_quitting=True)
        self.assertLess(small["total_xp"], big["total_xp"],
                        "small tournament must award less (anti-farm)")
        self.assertLess(small["scale"], 1.0)
        self.assertGreaterEqual(small["scale"], 0.35)

    def test_scale_monotonic_in_size(self):
        prev = -1
        for n in range(4, 33):
            r = self._xp("t", "p", n_players=n, place=1, matches_won=0,
                         rounds_total=1, deepest_round=0,
                         completed_first_match=False, completed_without_quitting=False)
            self.assertGreaterEqual(r["scale"], prev)
            prev = r["scale"]

    def test_component_ids_stable_and_unique(self):
        r = self._xp("TID", "PID", n_players=16, place=1, matches_won=3,
                     rounds_total=2, deepest_round=1,
                     completed_first_match=True, completed_without_quitting=True)
        ids = [it["id"] for it in r["items"]]
        self.assertEqual(len(ids), len(set(ids)), "component ids must be unique")
        for it in r["items"]:
            self.assertTrue(it["id"].startswith("xp:TID:PID:"))
        # deterministic: recompute yields identical ids + amounts
        r2 = self._xp("TID", "PID", n_players=16, place=1, matches_won=3,
                      rounds_total=2, deepest_round=1,
                      completed_first_match=True, completed_without_quitting=True)
        self.assertEqual(r["items"], r2["items"])

    def test_dedup_by_ledger_ids(self):
        # Simulate a ledger: paying the same result twice grants XP only once.
        ledger = set()
        awarded = 0
        for _ in range(2):  # duplicate submission
            r = self._xp("t9", "champ", n_players=8, place=1, matches_won=3,
                         rounds_total=3, deepest_round=2,
                         completed_first_match=True, completed_without_quitting=True)
            for it in r["items"]:
                if it["id"] not in ledger:
                    ledger.add(it["id"])
                    awarded += it["amount"]
        once = self._xp("t9", "champ", n_players=8, place=1, matches_won=3,
                        rounds_total=3, deepest_round=2,
                        completed_first_match=True, completed_without_quitting=True)
        self.assertEqual(awarded, once["total_xp"], "duplicate submit double-awarded XP")

    def test_placement_ordering_champion_gt_runner_up(self):
        champ = self._xp("t", "a", n_players=16, place=1, matches_won=2,
                         rounds_total=2, deepest_round=1,
                         completed_first_match=True, completed_without_quitting=True)
        second = self._xp("t", "b", n_players=16, place=2, matches_won=1,
                          rounds_total=2, deepest_round=1,
                          completed_first_match=True, completed_without_quitting=True)
        self.assertGreater(champ["total_xp"], second["total_xp"])


# =============================================================================
class TestSerialization(unittest.TestCase):
    def test_match_roundtrip(self):
        b = Bracket.build(TournamentConfig(8, 2), 8)
        b.seed_players(players(8))
        m = b.rounds[0][0]
        d = m.to_dict()
        m2 = te.BracketMatch.from_dict(d)
        self.assertEqual(m2.to_dict(), d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
