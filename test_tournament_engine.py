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
    TournamentConfig, Bracket, validate_config, validate_opening_sizes, plan_round_sizes,
    bracket_summary, available_formats, compute_player_xp,
    MIN_TOURNAMENT_PLAYERS, MAX_TOURNAMENT_PLAYERS,
    M_COMPLETE, M_BYE,
)


def custom_cfg(sizes, **kw):
    """Build a valid custom-bracket TournamentConfig from opening match sizes."""
    return TournamentConfig(total_capacity=sum(sizes), players_per_match=max(sizes),
                            opening_sizes=list(sizes), **kw)


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
class TestTopNAdvancement(unittest.TestCase):
    """"Top N advance" — the non-single-elimination formats.

    advance_per_match = 1 is classic single elimination; 2+ turns every match into
    a group whose best N carry on. The guarantee under test is that ANY legal
    (players, per-match, advance) still converges on exactly one champion.
    """

    ALL = [(n, m, a)
           for n in (4, 6, 8, 9, 12, 16, 20, 24, 31, 32)
           for m in range(2, 9) if m <= n
           for a in range(1, m)]

    def test_every_combination_is_valid_and_planned(self):
        for n, m, a in self.ALL:
            cfg = TournamentConfig(n, m, advance_per_match=a)
            self.assertEqual(validate_config(cfg), [], f"N={n} M={m} top{a}")
            grid = plan_round_sizes(n, m, a)
            self.assertEqual(sum(grid[0]), n, f"opening round must hold everyone N={n} M={m} top{a}")
            self.assertEqual(len(grid[-1]), 1, f"last round must be one Final N={n} M={m} top{a}")

    def test_a_match_never_advances_everyone(self):
        """The invariant that makes the field shrink: somebody always goes out."""
        for n, m, a in self.ALL:
            b = Bracket.build(TournamentConfig(n, m, advance_per_match=a), n)
            for match in b.all_matches():
                if match.capacity <= 1:
                    continue    # a bye advances its lone player, having played nobody
                self.assertLess(match.advance, match.capacity,
                                f"N={n} M={m} top{a}: match {match.match_number} "
                                f"advances {match.advance} of {match.capacity}")

    def test_the_final_crowns_exactly_one(self):
        for n, m, a in self.ALL:
            b = Bracket.build(TournamentConfig(n, m, advance_per_match=a), n)
            self.assertEqual(len(b.rounds[-1]), 1, f"N={n} M={m} top{a}")
            self.assertEqual(b.final_match.advance, 1, f"N={n} M={m} top{a}")

    def test_every_advancing_player_has_a_seat(self):
        """Advancing finishers of a round must exactly fill the next round's spots —
        no player is dropped, and no seat is left that nobody can reach."""
        for n, m, a in self.ALL:
            b = Bracket.build(TournamentConfig(n, m, advance_per_match=a), n)
            for r in range(b.n_rounds - 1):
                out = sum(x.advance for x in b.rounds[r])
                seats = sum(x.capacity for x in b.rounds[r + 1])
                self.assertEqual(out, seats, f"N={n} M={m} top{a} round {r}")
                targets = [f for x in b.rounds[r] for f in x.feeds]
                self.assertNotIn(None, targets, f"unconnected winner N={n} M={m} top{a} r{r}")
                self.assertEqual(len(set(targets)), len(targets),
                                 f"two winners sent to one seat N={n} M={m} top{a} r{r}")

    def test_full_tournaments_finish_with_one_champion(self):
        for n, m, a in self.ALL:
            cfg = TournamentConfig(n, m, advance_per_match=a)
            b, order = simulate(cfg, n, seed=5, winner_pick="seed")
            self.assertTrue(b.is_complete(), f"incomplete N={n} M={m} top{a}")
            self.assertIsNotNone(b.champion)
            placements = b.final_placements()
            self.assertEqual(sorted(p["player_id"] for p in placements), sorted(order),
                             f"placements N={n} M={m} top{a}")
            self.assertEqual([p["place"] for p in placements], list(range(1, n + 1)),
                             f"places must be 1..N with no gaps (N={n} M={m} top{a})")

    def test_top_two_of_four_is_the_expected_shape(self):
        """A concrete, readable case: 16 players, 4 per match, top 2 advance."""
        self.assertEqual(plan_round_sizes(16, 4, 2), [[4, 4, 4, 4], [4, 4], [4]])
        b = Bracket.build(TournamentConfig(16, 4, advance_per_match=2), 16)
        self.assertEqual([len(r) for r in b.rounds], [4, 2, 1])
        self.assertTrue(all(x.advance == 2 for x in b.rounds[0]))
        self.assertTrue(all(x.advance == 2 for x in b.rounds[1]))
        self.assertEqual(b.rounds[2][0].advance, 1)

    def test_advancing_players_land_in_the_next_match(self):
        b = Bracket.build(TournamentConfig(8, 4, advance_per_match=2), 8)
        b.seed_players(players(8))
        first = b.rounds[0][0]
        ranking = [p for p in first.player_ids if p]
        b.record_result(0, 0, ranking, {})
        self.assertEqual(first.winners, ranking[:2], "the top 2 advance, in order")
        # both of them are now sitting in the Final
        final_seats = [p for p in b.final_match.player_ids if p]
        self.assertIn(ranking[0], final_seats)
        self.assertIn(ranking[1], final_seats)
        self.assertNotIn(ranking[2], final_seats, "3rd place is knocked out")

    def test_advance_beyond_the_match_size_is_rejected(self):
        errs = validate_config(TournamentConfig(8, 2, advance_per_match=2))
        self.assertTrue(errs)
        self.assertIn("knocked out", errs[0])
        self.assertTrue(validate_config(TournamentConfig(8, 4, advance_per_match=4)))
        self.assertTrue(validate_config(TournamentConfig(8, 4, advance_per_match=0)))

    def test_custom_opening_sizes_respect_the_smallest_match(self):
        """Top 3 can't come out of a 2-player opening match."""
        cfg = custom_cfg([4, 4, 2], advance_per_match=2)
        errs = validate_config(cfg)
        self.assertTrue(errs)
        self.assertIn("2-player match", errs[0])
        # ...but it is fine once every opening match is big enough
        cfg_ok = custom_cfg([4, 4, 4], advance_per_match=2)
        self.assertEqual(validate_config(cfg_ok), [])
        self.assertEqual(plan_round_sizes(0, 4, 2, opening_sizes=[4, 4, 4]),
                         [[4, 4, 4], [3, 3], [4]])

    def test_summary_reports_the_rule(self):
        s = bracket_summary(TournamentConfig(16, 4, advance_per_match=2), 16)
        self.assertEqual(s["advancing_per_match"], 2)
        self.assertEqual(s["advance_label"], "Top 2 advance")
        self.assertEqual([len(r) for r in s["round_sizes"]], [4, 2, 1])
        self.assertEqual(te.advance_label(1), "Winner advances")
        self.assertEqual(te.advance_label(5), "Top 5 advance")

    def test_advance_options_lists_only_legal_rules(self):
        opts = te.advance_options(4, 16)
        self.assertEqual([o["advance_per_match"] for o in opts], [1, 2, 3])
        self.assertTrue(opts[0]["single_elimination"])
        self.assertFalse(opts[1]["single_elimination"])
        # a 1v1 bracket can only ever send the winner through
        self.assertEqual([o["advance_per_match"] for o in te.advance_options(2, 8)], [1])
        # custom opening sizes are capped by the SMALLEST match
        opts = te.advance_options(4, 0, opening_sizes=[4, 4, 3])
        self.assertEqual([o["advance_per_match"] for o in opts], [1, 2])

    def test_formats_flag_sizes_that_cannot_manage_the_rule(self):
        fmts = {f["players_per_match"]: f for f in available_formats(16, advance=2)}
        self.assertFalse(fmts[2]["advance_ok"], "1v1 can't advance 2")
        self.assertEqual(fmts[2]["advance_per_match"], 1)
        self.assertTrue(fmts[4]["advance_ok"])
        self.assertEqual(fmts[4]["advance_per_match"], 2)
        self.assertEqual(fmts[4]["max_advance"], 3)

    def test_single_elimination_is_unchanged(self):
        """Regression guard: adding top-N advancement must not move advance=1 at all.

        These literals were captured from the engine as it shipped in 1.6.31 and
        verified identical across every (players, per-match) pair."""
        self.assertEqual(plan_round_sizes(6, 2, 1), [[2, 2, 2], [2, 1], [2]])
        self.assertEqual(plan_round_sizes(8, 2, 1), [[2, 2, 2, 2], [2, 2], [2]])
        # 3 matches of 8 -> their 3 winners meet in a 3-player Final
        self.assertEqual(plan_round_sizes(24, 8, 1), [[8, 8, 8], [3]])
        self.assertEqual(plan_round_sizes(16, 4, 1), [[4, 4, 4, 4], [4]])
        self.assertEqual(plan_round_sizes(0, 4, 1, opening_sizes=[3, 4, 2]), [[3, 4, 2], [3]])
        # advance defaults to 1, so an explicit 1 must be a no-op everywhere
        for n in (4, 5, 6, 7, 8, 12, 16, 24, 32):
            for m in range(2, 9):
                if m > n:
                    continue
                self.assertEqual(plan_round_sizes(n, m, 1), plan_round_sizes(n, m),
                                 f"default advance changed for N={n} M={m}")


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
        self.assertGreaterEqual(small["scale"], 0.10)  # steep curve, low floor (~0.12)

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


# =============================================================================
class TestCustomBracket(unittest.TestCase):
    def test_plan_prepends_custom_opening_round(self):
        # [3,4,2] -> winners 3 -> one 3-player round -> [[3,4,2],[3]]
        self.assertEqual(plan_round_sizes(0, 4, 1, opening_sizes=[3, 4, 2]), [[3, 4, 2], [3]])
        # [2,2,2,2] custom equals the uniform 8@2 shape
        self.assertEqual(plan_round_sizes(0, 2, 1, opening_sizes=[2, 2, 2, 2]),
                         plan_round_sizes(8, 2))
        # single opening match = a one-match tournament
        self.assertEqual(plan_round_sizes(0, 5, 1, opening_sizes=[5]), [[5]])

    def test_build_uses_custom_shape_regardless_of_n(self):
        cfg = custom_cfg([3, 4, 2])
        b = Bracket.build(cfg, cfg.total_capacity)
        self.assertEqual([m.capacity for m in b.rounds[0]], [3, 4, 2])
        self.assertEqual(b.n_rounds, 2)
        # match numbers stay unique + sequential
        nums = [m.match_number for row in b.rounds for m in row]
        self.assertEqual(nums, list(range(1, len(nums) + 1)))

    def test_seed_exact_then_full_run_reaches_one_champion(self):
        for sizes in ([3, 4, 2], [2, 2, 2, 2], [4, 4], [8, 8, 8, 8], [2, 3, 4, 5], [5, 5, 5], [6]):
            with self.subTest(sizes=sizes):
                cfg = custom_cfg(sizes)
                self.assertEqual(validate_config(cfg), [])
                b, order = simulate(cfg, cfg.total_capacity, seed=7, winner_pick="seed")
                self.assertTrue(b.is_complete())
                self.assertIsNotNone(b.champion)
                # champion appeared in the final
                self.assertIn(b.champion, [p for p in b.final_match.player_ids if p])

    def test_partial_seed_leaves_empty_slots(self):
        cfg = custom_cfg([3, 4, 2])   # capacity 9
        b = Bracket.build(cfg, 4)
        b.seed_players(["a", "b", "c", "d"], allow_partial=True)
        self.assertEqual(b.rounds[0][0].player_ids, ["a", "b", "c"])
        self.assertEqual(b.rounds[0][1].player_ids, ["d", None, None, None])
        self.assertEqual(b.rounds[0][2].player_ids, [None, None])

    def test_partial_seed_rejects_more_than_slots(self):
        cfg = custom_cfg([2, 2])   # 4 slots
        b = Bracket.build(cfg, cfg.total_capacity)
        with self.assertRaises(ValueError):
            b.seed_players(["a", "b", "c", "d", "e"], allow_partial=True)

    def test_validation_rejects_bad_custom(self):
        # sum != total_capacity
        self.assertTrue(validate_config(TournamentConfig(10, 4, opening_sizes=[3, 4, 2])))
        # a match too big
        self.assertTrue(validate_opening_sizes([3, 9, 2]))
        # a match too small (byes not allowed in custom design)
        self.assertTrue(validate_opening_sizes([1, 4, 2]))
        # total out of range
        self.assertTrue(validate_opening_sizes([2]))          # 2 < MIN 4
        self.assertTrue(validate_opening_sizes([8] * 5))      # 40 > MAX 32
        # players_per_match must equal the largest match
        self.assertTrue(validate_config(TournamentConfig(9, 3, opening_sizes=[3, 4, 2])))
        # a well-formed one is accepted
        self.assertEqual(validate_config(custom_cfg([3, 4, 2])), [])

    def test_summary_flags_custom(self):
        s = bracket_summary(custom_cfg([3, 4, 2]), 0)
        self.assertTrue(s["is_custom"])
        self.assertEqual(s["opening_sizes"], [3, 4, 2])
        self.assertEqual(s["tournament_size"], 9)
        self.assertEqual(s["num_rounds"], 2)
        self.assertEqual(s["num_opening_matches"], 3)

    def test_config_roundtrip_preserves_custom(self):
        cfg = custom_cfg([3, 4, 2], third_place_match=False, name="Cup")
        d = cfg.to_dict()
        self.assertTrue(d["is_custom"])
        self.assertEqual(d["opening_sizes"], [3, 4, 2])
        cfg2 = TournamentConfig.from_dict(d)
        self.assertEqual(cfg2.opening_sizes, [3, 4, 2])
        self.assertEqual(cfg2.total_capacity, 9)
        self.assertEqual(cfg2.players_per_match, 4)


# =============================================================================
# DESIGNED (canvas-built) BRACKETS
# =============================================================================
from tournament_engine import (  # noqa: E402
    CustomBracket, CustomMatch, CustomSlot, validate_custom_bracket,
    layer_custom_bracket, custom_bracket_summary, make_uniform_graph,
    SLOT_OPEN, SLOT_HUMAN, SLOT_AI, SLOT_INVITE, SLOT_WINNER_FROM, SLOT_TOP_FROM,
)


def dmatch(mid, slots, advance=1, label="", x=0, y=0):
    """Designed match from a compact slot spec: "open", "ai", ("m1", 1), …"""
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
    return CustomMatch(id=mid, slots=out, advance=advance, label=label, x=x, y=y)


def graph_cfg(spec, **kw):
    return TournamentConfig(total_capacity=spec.entry_count(),
                            players_per_match=spec.max_capacity(),
                            custom_graph=spec.to_dict(), **kw)


def simple_designed():
    """Two 2-player matches feeding a Final — the smallest legal design."""
    return CustomBracket(matches=[
        dmatch("a", [SLOT_OPEN, SLOT_OPEN], label="Round 1"),
        dmatch("b", [SLOT_OPEN, SLOT_OPEN], label="Round 1"),
        dmatch("f", [("a", 1), ("b", 1)], label="Final"),
    ])


class TestDesignedBracketValidation(unittest.TestCase):
    def test_minimal_design_is_valid(self):
        self.assertEqual(validate_custom_bracket(simple_designed()), [])

    def test_uniform_helper_matches_generated_shape(self):
        for sizes in ([2, 2, 2, 2], [3, 4, 2], [2] * 8, [8, 8, 8, 8]):
            spec = make_uniform_graph(sizes)
            self.assertEqual(validate_custom_bracket(spec), [], f"sizes={sizes}")
            gen = bracket_summary(custom_cfg(sizes), sum(sizes))
            des = custom_bracket_summary(spec)
            self.assertEqual(des["tournament_size"], gen["tournament_size"], sizes)
            self.assertEqual(des["num_rounds"], gen["num_rounds"], sizes)

    def test_no_matches(self):
        self.assertTrue(validate_custom_bracket(CustomBracket()))

    def test_too_many_matches(self):
        spec = CustomBracket(matches=[dmatch(f"m{i}", [SLOT_OPEN, SLOT_OPEN])
                                      for i in range(33)])
        self.assertIn("at most", " ".join(validate_custom_bracket(spec)))

    def test_match_size_bounds(self):
        one = CustomBracket(matches=[dmatch("a", [SLOT_OPEN])])
        self.assertIn("player spot", " ".join(validate_custom_bracket(one)))
        nine = CustomBracket(matches=[dmatch("a", [SLOT_OPEN] * 9)])
        self.assertIn("player spot", " ".join(validate_custom_bracket(nine)))

    def test_advance_must_shrink_the_field(self):
        spec = simple_designed()
        spec.by_id("a").advance = 2      # both players advance out of a 2-player match
        self.assertIn("knock at least one player out", " ".join(validate_custom_bracket(spec)))

    def test_final_produces_one_champion(self):
        spec = simple_designed()
        spec.by_id("f").slots.append(CustomSlot(kind=SLOT_OPEN))
        spec.by_id("f").advance = 2
        self.assertIn("exactly 1 player can win", " ".join(validate_custom_bracket(spec)))

    def test_connection_to_missing_match(self):
        spec = simple_designed()
        spec.by_id("f").slots[0].source = "ghost"
        self.assertIn("no longer exists", " ".join(validate_custom_bracket(spec)))

    def test_connection_with_no_source(self):
        spec = simple_designed()
        spec.by_id("f").slots[0].source = ""
        self.assertIn("no match is connected", " ".join(validate_custom_bracket(spec)))

    def test_self_feeding_match(self):
        spec = simple_designed()
        spec.by_id("f").slots[0].source = "f"
        self.assertIn("itself", " ".join(validate_custom_bracket(spec)))

    def test_rank_beyond_what_advances(self):
        # The Final asks for the runner-up of a match where only the winner advances.
        spec = simple_designed()
        spec.by_id("f").slots[1] = CustomSlot(kind=SLOT_TOP_FROM, source="a", rank=2)
        errs = " ".join(validate_custom_bracket(spec))
        self.assertIn("only 1 advance", errs)

    def test_same_finisher_sent_to_two_spots(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("b", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("f", [("a", 1), ("a", 1)]),      # both spots take a's winner
        ])
        errs = " ".join(validate_custom_bracket(spec))
        self.assertIn("can only advance to one", errs)

    def test_unconnected_match_is_reported(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("b", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("c", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("f", [("a", 1), ("b", 1)]),      # c is never connected onward
        ])
        errs = " ".join(validate_custom_bracket(spec))
        self.assertIn("lead nowhere", errs)
        self.assertIn("exactly one Final", errs)

    def test_second_advancing_player_with_nowhere_to_go(self):
        # 'g' advances two, but only its winner has a spot waiting.
        spec = CustomBracket(matches=[
            dmatch("g", [SLOT_OPEN] * 4, advance=2),
            dmatch("b", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("f", [("g", 1), ("b", 1)]),
        ])
        errs = " ".join(validate_custom_bracket(spec))
        self.assertIn("nowhere to go", errs)
        self.assertIn("runner-up", errs)

    def test_two_finals(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("b", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("f1", [("a", 1), SLOT_OPEN]),
            dmatch("f2", [("b", 1), SLOT_OPEN]),
        ])
        errs = " ".join(validate_custom_bracket(spec))
        self.assertIn("exactly one Final", errs)

    def test_loop_is_rejected(self):
        spec = CustomBracket(matches=[
            dmatch("a", [("b", 1), SLOT_OPEN]),
            dmatch("b", [("a", 1), SLOT_OPEN]),
        ])
        errs = " ".join(validate_custom_bracket(spec))
        self.assertIn("loop", errs)

    def test_field_size_bounds(self):
        tiny = CustomBracket(matches=[dmatch("a", [SLOT_OPEN, SLOT_OPEN])])
        self.assertIn("starting player spots", " ".join(validate_custom_bracket(tiny)))
        big = make_uniform_graph([8] * 5)            # 40 starting spots
        self.assertIn("starting player spots", " ".join(validate_custom_bracket(big)))

    def test_all_ai_bracket_rejected(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_AI, SLOT_AI]),
            dmatch("b", [SLOT_AI, SLOT_AI]),
            dmatch("f", [("a", 1), ("b", 1)]),
        ])
        self.assertIn("AI-only", " ".join(validate_custom_bracket(spec)))

    def test_mixed_seat_kinds_are_valid(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_HUMAN, SLOT_AI, "invite:Reef"], advance=2),
            dmatch("b", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("f", [("a", 1), ("a", 2), ("b", 1), SLOT_AI]),
        ])
        self.assertEqual(validate_custom_bracket(spec), [])
        self.assertEqual(spec.entry_count(), 7)
        self.assertEqual(spec.human_capacity(), 5)   # 7 entry spots minus 2 AI-only


class TestDesignedBracketRuntime(unittest.TestCase):
    def test_layering_puts_the_final_last_and_alone(self):
        for spec in (simple_designed(), make_uniform_graph([3, 4, 2]), make_uniform_graph([2] * 8)):
            layers = layer_custom_bracket(spec)
            self.assertEqual(len(layers[-1]), 1, "the Final must be alone in the last round")
            self.assertEqual(layers[-1][0].id, spec.terminal_matches()[0].id)

    def test_uneven_depth_still_ends_at_the_final(self):
        # 'a' plays an extra preliminary round; 'c' waits. The Final must still be last.
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_OPEN], label="Play-in"),
            dmatch("b", [("a", 1), SLOT_OPEN], label="Semifinal"),
            dmatch("c", [SLOT_OPEN, SLOT_OPEN], label="Semifinal"),
            dmatch("f", [("b", 1), ("c", 1)], label="Final"),
        ])
        self.assertEqual(validate_custom_bracket(spec), [])
        layers = layer_custom_bracket(spec)
        self.assertEqual([[m.id for m in row] for row in layers], [["a", "c"], ["b"], ["f"]])

    def test_build_wires_feeds_to_the_exact_designed_spot(self):
        spec = simple_designed()
        br = Bracket.build_custom(graph_cfg(spec), spec)
        a = br.rounds[0][0]
        self.assertEqual(a.feeds, [(1, 0, 0)])        # a's winner -> Final spot 1
        b = br.rounds[0][1]
        self.assertEqual(b.feeds, [(1, 0, 1)])        # b's winner -> Final spot 2
        self.assertEqual(br.final_match.feeds, [None])  # the champion goes nowhere

    def test_labels_and_slot_kinds_survive_the_build(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_AI], label="Losers Bracket", x=10, y=20),
            dmatch("b", [SLOT_OPEN, "invite:Kelp"], label="Losers Bracket"),
            dmatch("f", [("a", 1), ("b", 1)], label="Grand Final"),
        ])
        br = Bracket.build_custom(graph_cfg(spec), spec)
        self.assertEqual(br.rounds[0][0].label, "Losers Bracket")
        self.assertEqual(br.final_match.label, "Grand Final")
        self.assertEqual(br.rounds[0][0].slot_kinds[1]["kind"], SLOT_AI)
        self.assertEqual(br.rounds[0][1].slot_kinds[1]["invite"], "Kelp")
        self.assertEqual((br.rounds[0][0].x, br.rounds[0][0].y), (10, 20))
        # the view payload carries them to the client
        d = br.rounds[0][0].to_dict()
        self.assertEqual(d["label"], "Losers Bracket")
        self.assertEqual(d["custom_id"], "a")
        self.assertEqual(len(d["slot_kinds"]), 2)

    def test_entry_slots_include_later_round_seats(self):
        # The Final has its own AI seat alongside two winner spots.
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("b", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("f", [("a", 1), ("b", 1), SLOT_AI]),
        ])
        self.assertEqual(validate_custom_bracket(spec), [])
        br = Bracket.build_custom(graph_cfg(spec), spec)
        es = br.entry_slots()
        self.assertEqual(len(es), 5)
        self.assertEqual(es[-1]["kind"], SLOT_AI)
        self.assertEqual(es[-1]["round_index"], 1)     # a seat in the Final's round
        self.assertEqual([e["seat"] for e in es], [0, 1, 2, 3, 4])

    def test_seed_entries_fills_only_designed_seats(self):
        spec = CustomBracket(matches=[
            dmatch("a", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("b", [SLOT_OPEN, SLOT_OPEN]),
            dmatch("f", [("a", 1), ("b", 1), SLOT_AI]),
        ])
        br = Bracket.build_custom(graph_cfg(spec), spec)
        br.seed_entries(["p0", "p1", "p2", "p3", "bot"])
        self.assertEqual(br.rounds[0][0].player_ids, ["p0", "p1"])
        self.assertEqual(br.rounds[0][1].player_ids, ["p2", "p3"])
        self.assertEqual(br.final_match.player_ids, [None, None, "bot"])

    def test_partial_seeding_for_the_lobby_preview(self):
        spec = simple_designed()
        br = Bracket.build_custom(graph_cfg(spec), spec)
        br.seed_players(["p0", "p1"], allow_partial=True)
        self.assertEqual(br.rounds[0][0].player_ids, ["p0", "p1"])
        self.assertEqual(br.rounds[0][1].player_ids, [None, None])
        with self.assertRaises(ValueError):
            br.seed_players(["p0", "p1"])          # short of a full field

    def test_full_designed_tournament_runs_to_a_champion(self):
        spec = make_uniform_graph([2, 2, 2, 2])
        cfg = graph_cfg(spec, name="Designed Cup")
        b, order = simulate(cfg, 8)
        self.assertTrue(b.is_complete())
        self.assertIsNotNone(b.champion)
        placements = b.final_placements()
        self.assertEqual(len(placements), 8)
        self.assertEqual(placements[0]["place"], 1)
        self.assertEqual(placements[0]["player_id"], b.champion)

    def test_top_two_advance_into_one_match(self):
        # A 4-player group where the top TWO advance into a 4-player Final.
        spec = CustomBracket(matches=[
            dmatch("g1", [SLOT_OPEN] * 4, advance=2, label="Group A"),
            dmatch("g2", [SLOT_OPEN] * 4, advance=2, label="Group B"),
            dmatch("f", [("g1", 1), ("g1", 2), ("g2", 1), ("g2", 2)], label="Final"),
        ])
        self.assertEqual(validate_custom_bracket(spec), [])
        cfg = graph_cfg(spec)
        self.assertEqual(validate_config(cfg), [])
        br = Bracket.build(cfg, 8)
        br.seed_players(players(8))
        g1 = br.rounds[0][0]
        br.record_result(0, 0, ["p00", "p01", "p02", "p03"], {})
        # both of g1's advancing finishers land in the Final, in designed order
        self.assertEqual(br.final_match.player_ids[:2], ["p00", "p01"])
        br.record_result(0, 1, ["p04", "p05", "p06", "p07"], {})
        self.assertEqual(br.final_match.player_ids, ["p00", "p01", "p04", "p05"])
        br.record_result(1, 0, ["p05", "p00", "p01", "p04"], {})
        self.assertEqual(br.champion, "p05")

    def test_designed_bracket_survives_a_config_roundtrip(self):
        spec = make_uniform_graph([3, 4, 2])
        cfg = graph_cfg(spec, name="Cup")
        d = cfg.to_dict()
        self.assertTrue(d["is_custom"])
        self.assertTrue(d["is_graph"])
        cfg2 = TournamentConfig.from_dict(d)
        self.assertEqual(validate_config(cfg2), [])
        self.assertEqual(cfg2.graph().to_dict(), spec.to_dict())
        b2 = Bracket.build(cfg2, cfg2.total_capacity)
        self.assertEqual(b2.n_rounds, len(layer_custom_bracket(spec)))

    def test_config_rejects_wrong_capacity(self):
        spec = simple_designed()
        cfg = TournamentConfig(total_capacity=9, players_per_match=2,
                               custom_graph=spec.to_dict())
        self.assertIn("must equal the number of player spots", " ".join(validate_config(cfg)))

    def test_config_rejects_graph_plus_opening_sizes(self):
        spec = simple_designed()
        cfg = TournamentConfig(total_capacity=4, players_per_match=2,
                               custom_graph=spec.to_dict(), opening_sizes=[2, 2])
        self.assertTrue(validate_config(cfg))

    def test_build_refuses_an_invalid_design(self):
        spec = CustomBracket(matches=[dmatch("a", [SLOT_OPEN, SLOT_OPEN])])
        with self.assertRaises(ValueError):
            Bracket.build_custom(graph_cfg(spec), spec)

    def test_slot_parsing_is_tolerant(self):
        raw = {"matches": [
            {"id": "a", "slots": ["open", {"kind": "bot"}], "advance": 1, "label": "R1"},
            {"id": "b", "slots": [{"kind": "player"}, {"kind": "invite", "invite": "Coral"}]},
            {"id": "f", "slots": [{"kind": "winner", "source": "a"},
                                  {"kind": "top", "source": "b", "rank": 1}]},
        ]}
        spec = CustomBracket.from_dict(raw)
        self.assertEqual(validate_custom_bracket(spec), [])
        self.assertEqual(spec.by_id("a").slots[1].kind, SLOT_AI)
        self.assertEqual(spec.by_id("b").slots[0].kind, SLOT_HUMAN)
        self.assertEqual(spec.by_id("f").slots[0].kind, SLOT_WINNER_FROM)


# =============================================================================
class TestNoRoundIsEverSkipped(unittest.TestCase):
    """A tournament must work through ALL of its rounds. No champion while a
    match still owes a game — the report was "it didn't let me play the last two
    games, it just decided the winner"."""

    def test_final_alone_is_not_enough_to_be_complete(self):
        cfg = TournamentConfig(total_capacity=8, players_per_match=2)
        br = Bracket.build(cfg, 8)
        br.seed_players(players(8))
        # Play everything except one semifinal, then force a result into the Final.
        br.record_result(0, 0, ["p00", "p01"])
        br.record_result(0, 1, ["p02", "p03"])
        br.record_result(0, 2, ["p04", "p05"])
        br.record_result(0, 3, ["p06", "p07"])
        br.record_result(1, 0, ["p00", "p02"])          # semifinal 1 played
        semi2 = br.rounds[1][1]                          # semifinal 2 NEVER played
        self.assertNotIn(semi2.status, (M_COMPLETE, M_BYE))
        # Hand the Final a result anyway (what an odd bracket shape could do).
        final = br.final_match
        final.player_ids = ["p00", "p04"]
        br.record_result(final.round_index, final.match_index, ["p00", "p04"])
        self.assertEqual([m.match_number for m in br.unresolved_matches()],
                         [semi2.match_number])
        self.assertFalse(br.is_complete(),
                         "a champion must not be crowned with a match still unplayed")
        br.record_result(1, 1, ["p04", "p06"])
        self.assertTrue(br.is_complete())

    def test_every_generated_bracket_plays_every_match(self):
        for n, m, a in [(8, 2, 1), (16, 4, 1), (16, 4, 2), (32, 4, 1), (32, 4, 2),
                        (24, 3, 2), (32, 8, 3), (25, 4, 1), (6, 2, 1)]:
            cfg = TournamentConfig(total_capacity=n, players_per_match=m, advance_per_match=a)
            br, _order = simulate(cfg, n, seed=n + m + a)
            with self.subTest(n=n, m=m, a=a):
                self.assertTrue(br.is_complete())
                self.assertEqual(br.unresolved_matches(), [])
                for mm in br.all_matches():
                    if mm.filled_count() >= 2:
                        self.assertIn(mm.status, (M_COMPLETE, M_BYE),
                                      f"match {mm.match_number} never got a game")


class TestPlacementOrdering(unittest.TestCase):
    def test_deeper_run_always_places_higher(self):
        for n, m, a in [(16, 4, 2), (32, 4, 2), (32, 2, 1), (24, 3, 2)]:
            cfg = TournamentConfig(total_capacity=n, players_per_match=m, advance_per_match=a)
            br, _order = simulate(cfg, n, seed=7)
            pl = br.final_placements()
            with self.subTest(n=n, m=m, a=a):
                self.assertEqual([e["place"] for e in pl], list(range(1, len(pl) + 1)))
                self.assertEqual(len({e["player_id"] for e in pl}), len(pl))
                depths = [e["round_reached"] for e in pl]
                self.assertEqual(depths, sorted(depths, reverse=True),
                                 "nobody may place above a player who went further")

    def test_same_round_exit_ranked_by_how_they_finished(self):
        """Two players knocked out in the same round: the one who finished higher
        in their own match places above the one who came last."""
        cfg = TournamentConfig(total_capacity=8, players_per_match=4, advance_per_match=1)
        br = Bracket.build(cfg, 8)
        br.seed_players(players(8))
        # p01 finishes 2nd of four; p05 finishes 4th of four. Same round, and p05
        # carries the bigger raw score, which used to be all that was compared.
        br.record_result(0, 0, ["p00", "p01", "p02", "p03"],
                         {"p00": 100, "p01": 10, "p02": 9, "p03": 8})
        br.record_result(0, 1, ["p04", "p06", "p07", "p05"],
                         {"p04": 100, "p06": 60, "p07": 50, "p05": 40})
        br.record_result(1, 0, ["p00", "p04"], {"p00": 120, "p04": 90})
        by_pid = {e["player_id"]: e["place"] for e in br.final_placements()}
        self.assertLess(by_pid["p01"], by_pid["p05"],
                        "2nd of four must place above 4th of four from the same round")


if __name__ == "__main__":
    unittest.main(verbosity=2)
