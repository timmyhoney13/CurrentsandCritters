"""Proof that the deal is not biased FOR or AGAINST any strategy.

`test_deck_shuffle_fairness.py` proves the shuffle itself is uniform. This suite
answers the different, sharper question players actually ask:

    "The game knows what I'm going for and stops giving me those cards."

That is a claim about CONDITIONAL independence, not about the shuffle: does the
card you receive depend on the strategy you are playing? A deck can be perfectly
uniformly shuffled and still be rigged if some later code peeks at a player's
plan and hands them a different card. So this suite drives REAL, complete
`run_match` games (bots thinking with the live brain, exactly as the server runs
them) and checks the deal from three independent angles:

  A. STRUCTURAL. During play the draw deck is only ever read from the TOP. The
     exact sequence of cards that leaves the deck all game is a prefix of the
     deck order as it stood at the end of setup, before anybody acted. Nothing
     is ever selected out of the middle, so no code path can choose *which*
     card a given player gets.

  B. CAUSAL. Strategy is an OUTPUT of the deal, never an input to it. At a fixed
     seed, changing every bot's difficulty (and therefore the strategies the
     table commits to) leaves the opening hands and the whole post-setup deck
     order bit-identical. Marking a seat as human likewise cannot change the
     hand it is dealt.

  C. STATISTICAL. Over thousands of real in-game draws, the rate at which a
     player draws a card belonging to their OWN current strategy matches the
     exact odds implied by the cards left in the deck at that moment. Measured
     per strategy family, so "it withholds MY plan's cards specifically" is
     tested one plan at a time.

Test C is conditioned on the live deck composition at each individual draw, which
is what makes it fair: the deck is 43% Ocean by design, and Yellowfin Tuna sits
on 14 of 161 entries while Mandarin Goby sits on 4. A naive rate would flag that
designed imbalance as bias. Comparing each draw against the cards actually
remaining removes the composition effect entirely and leaves only the question
of whether the engine is picking favourites.

THE END GAME TRAP: the END GAME card is deliberately parked in the bottom 15, so
it is NOT exchangeable with the rest of the deck and is excluded from every
denominator here. Leaving it in manufactures a fake signal that reads exactly
like a rigged deck. Same trap as in test_deck_shuffle_fairness.py.

Run:  python3 test_deal_strategy_independence.py [--games N]
"""
import argparse
import collections
import copy
import math
import multiprocessing as mp
import os
import random
import secrets
import statistics
import sys

import fish_game_all_in_one as fish


# --------------------------------------------------------------------------
# Shared match harness: the same policy the live server builds for its bots.
# --------------------------------------------------------------------------

def _brain_maps():
    brain = fish.load_brain(fish.BRAIN_PATH)
    weights = fish.stabilize_weights({**fish.default_weights(), **brain.get("weights", {})})
    return weights, {
        "synergy_map": brain.get("synergy", {}),
        "species_map": brain.get("species_synergy", {}),
        "same_ocean_map": brain.get("same_ocean_synergy", {}),
        "strategy_value_map": brain.get("strategy_value", {}),
        "strategy_count_map": brain.get("strategy_count", {}),
        "strategy_transition_map": brain.get("strategy_transition", {}),
        "strategy_transition_count_map": brain.get("strategy_transition_count", {}),
    }


def _make_policy(weights, maps):
    def policy(gs, ms, p):
        return fish.choose_action_weighted(gs, ms, p, weights, epsilon=0.0, **maps)
    return policy


def _family_name_sets():
    """label -> set of lowercase card names that belong to that strategy."""
    out = {}
    for fam in fish.strategy_family_profiles():
        label = str(fam.get("label", ""))
        names = {str(n).strip().lower() for n in fam.get("names", [])}
        names |= {str(n).strip().lower() for n in fam.get("heavy_hitters", [])}
        names |= {str(n).strip().lower() for n in fam.get("stack_engines", [])}
        names |= {str(n).strip().lower() for n in fam.get("support_names", [])}
        if label and names:
            out[label] = names
    return out


# --------------------------------------------------------------------------
# A. Structural: the deck is only ever read from the top during play.
# --------------------------------------------------------------------------

class TopOnlyDeck(list):
    """A draw deck that records every mutation performed on it.

    Armed once setup is finished, so it audits only the part of the game where a
    player's strategy actually exists and could, in principle, be peeked at.
    """

    def __init__(self, *a):
        super().__init__(*a)
        self.ops = []
        self.armed = False
        self.armed_order = None

    def arm(self):
        self.armed = True
        self.armed_order = list(self)
        self.ops = []

    def _rec(self, name, detail=None):
        if self.armed:
            self.ops.append((name, detail))

    def pop(self, index=-1):
        self._rec("pop", index)
        return super().pop(index)

    def remove(self, value):
        self._rec("remove", value)
        return super().remove(value)

    def insert(self, index, value):
        self._rec("insert", index)
        return super().insert(index, value)

    def append(self, value):
        self._rec("append", value)
        return super().append(value)

    def extend(self, other):
        self._rec("extend", None)
        return super().extend(other)

    def __setitem__(self, key, value):
        self._rec("setitem", key)
        return super().__setitem__(key, value)

    def __delitem__(self, key):
        self._rec("delitem", key)
        return super().__delitem__(key)

    def sort(self, *a, **k):
        self._rec("sort", None)
        return super().sort(*a, **k)

    def reverse(self):
        self._rec("reverse", None)
        return super().reverse()

    def clear(self):
        self._rec("clear", None)
        return super().clear()


def test_A_deck_is_only_read_from_the_top(games=3, players=4):
    """Play real games and audit every single mutation of the live draw deck."""
    card_db = fish.load_card_db()
    weights, maps = _brain_maps()
    policy = _make_policy(weights, maps)

    real_build = fish.build_deck_with_late_end_game
    real_assign = fish.assign_strategy_families_from_opening_hands
    captured = {}

    def build_hook(*a, **k):
        deck, end_uid = real_build(*a, **k)
        captured["deck"] = TopOnlyDeck(deck)
        return captured["deck"], end_uid

    def assign_hook(gs, *a, **k):
        # Runs immediately after setup and before any player acts: this is the
        # moment the deck order is final and strategies come into existence.
        gs.deck.arm()
        captured["gs"] = gs
        return real_assign(gs, *a, **k)

    checked = 0
    try:
        fish.build_deck_with_late_end_game = build_hook
        fish.assign_strategy_families_from_opening_hands = assign_hook
        for _ in range(games):
            captured.clear()
            gs, ms = fish.run_match(
                card_db=card_db,
                player_names=[f"P{i + 1}" for i in range(players)],
                action_policies=[policy] * players,
                seed=secrets.randbits(64),
                max_turns=260,
                ai_difficulties=["medium"] * players,
            )
            deck = gs.deck
            assert isinstance(deck, TopOnlyDeck), (
                "the live draw deck was replaced by a different list during play, "
                "so it could no longer be audited"
            )
            assert deck.armed, "audit never armed: setup hook did not fire"

            # 1. Every mutation during play is a draw from the top.
            bad = [op for op in deck.ops if op != ("pop", 0)]
            assert not bad, (
                f"the deck was modified from somewhere other than the top during play: "
                f"{collections.Counter(op[0] for op in bad).most_common()}"
            )

            # 2. The cards that left the deck are exactly the top of the deck as
            #    it stood before anyone acted, in that exact order. This is the
            #    part that makes per-player selection impossible.
            drawn = len(deck.ops)
            order = deck.armed_order
            assert list(deck) == order[drawn:], (
                "the cards remaining in the deck are not the untouched tail of the "
                "post-setup deck order: the deck was reordered mid-game"
            )
            assert drawn + len(deck) == len(order), "cards appeared or vanished from the deck"
            checked += 1
    finally:
        fish.build_deck_with_late_end_game = real_build
        fish.assign_strategy_families_from_opening_hands = real_assign

    print(
        f"A PASS: {checked} real games, every card that left the deck came off the top "
        f"in the order fixed at setup: no code path can pick WHICH card a player gets"
    )


# --------------------------------------------------------------------------
# B. Causal: strategy is an output of the deal, never an input.
# --------------------------------------------------------------------------

def _capture_setup(seed, difficulties, human_indices, players=4):
    """Run a match at a fixed seed and snapshot the table the instant setup ends
    (opening hands + the final post-setup deck order), before anyone acts."""
    card_db = fish.load_card_db()
    weights, maps = _brain_maps()
    policy = _make_policy(weights, maps)
    real_assign = fish.assign_strategy_families_from_opening_hands
    snap = {}

    def assign_hook(gs, ms, brain, humans, rng):
        snap["hands"] = [list(p.hand) for p in gs.players]
        snap["deck"] = list(gs.deck)
        # Stop the game here: setup is all this test cares about, and running the
        # full match would just burn time.
        raise _SetupCaptured

    try:
        fish.assign_strategy_families_from_opening_hands = assign_hook
        try:
            fish.run_match(
                card_db=card_db,
                player_names=[f"P{i + 1}" for i in range(players)],
                action_policies=[policy] * players,
                seed=seed,
                max_turns=260,
                human_indices=set(human_indices),
                ai_difficulties=list(difficulties),
            )
        except _SetupCaptured:
            pass
    finally:
        fish.assign_strategy_families_from_opening_hands = real_assign
    return snap


class _SetupCaptured(Exception):
    pass


def test_B_deal_ignores_strategy_and_difficulty(trials=6):
    """At one seed, what the table is PLAYING cannot change what it is DEALT."""
    for t in range(trials):
        seed = secrets.randbits(64)

        # Difficulty drives strategy commitment (skill gates which families a bot
        # may pick, and how hard it commits). If the deal were conditioned on
        # strategy in any way, swapping every bot from easy to hard would move it.
        easy = _capture_setup(seed, ["easy"] * 4, [])
        hard = _capture_setup(seed, ["hard"] * 4, [])
        mixed = _capture_setup(seed, ["easy", "hard", "medium", "hard"], [])

        assert easy["hands"] == hard["hands"] == mixed["hands"], (
            "opening hands changed when only the bots' difficulty/strategy changed: "
            "the deal is reading the strategy"
        )
        assert easy["deck"] == hard["deck"] == mixed["deck"], (
            "the post-setup deck order changed when only difficulty/strategy changed: "
            "the deck is being arranged around what the table is playing"
        )

        # Marking a seat human must not change the hand that seat is dealt.
        # (Playstyle assignment consumes the RNG differently for a human seat, so
        # the later reshuffle legitimately differs; the DEAL must not.)
        human0 = _capture_setup(seed, ["medium"] * 4, [0])
        human2 = _capture_setup(seed, ["medium"] * 4, [2])
        assert easy["hands"] == human0["hands"] == human2["hands"], (
            "opening hands changed depending on which seat was a human: "
            "humans are being dealt from a different deck than bots"
        )

    print(
        f"B PASS: {trials} seeds, opening hands and post-setup deck order are identical "
        f"across easy/hard/mixed tables and human seats: strategy is chosen FROM the "
        f"dealt hand, it never feeds back into the deal"
    )


# --------------------------------------------------------------------------
# C. Statistical: on-plan draw rate matches the odds left in the deck.
# --------------------------------------------------------------------------

def _play_one_measured_game(seed):
    """One real game, instrumented to record every card drawn from the deck into
    a hand together with the odds that draw had of being 'on-plan' for whoever
    drew it. Returns compact tuples so this can run in a worker process."""
    card_db = fish.load_card_db()
    weights, maps = _brain_maps()
    policy = _make_policy(weights, maps)
    fam_names = _family_name_sets()
    players = 4

    events = []  # (family_label, seat, on_plan, p_expected)
    # Bots plan by deep-copying the whole game and rolling lines forward, and
    # those simulated games draw cards too. Only the ONE real GameState counts,
    # so it is captured at the end of setup and every draw is identity-checked
    # against it. Without this the measurement is swamped by imaginary draws.
    real_gs = {}

    def entry_names(ms, uid):
        out = set()
        for face in fish.entry_faces(ms, uid):
            c = card_db.get(face)
            if c is not None:
                out.add(c.name.strip().lower())
        return out

    real_draw_from_deck = fish.draw_from_deck
    real_draw = fish.draw

    def record(gs, ms, player, uid):
        if gs is not real_gs.get("gs"):
            return  # a bot's imagined rollout, not a real deal
        label = str(player.flags.get("_strategy_family", "") or "")
        names = fam_names.get(label)
        if not names:
            return
        try:
            seat = next(i for i, p in enumerate(gs.players) if p is player)
        except StopIteration:
            return
        # Odds this draw was on-plan, given the cards actually left in the deck.
        # END GAME is bottom-parked by design and never enters a hand, so it is
        # excluded from the denominator (see the END GAME TRAP note up top).
        end_uid = ms.end_game_uid
        pool = [u for u in gs.deck if u != end_uid]
        if not pool:
            return
        on = sum(1 for u in pool if entry_names(ms, u) & names)
        events.append((label, seat, bool(entry_names(ms, uid) & names), on / len(pool)))

    def hooked_draw_from_deck(gs, ms, player, n):
        got = []
        for _ in range(n):
            before = len(player.hand)
            deck_before = list(gs.deck)
            one = real_draw_from_deck(gs, ms, player, 1)
            if len(player.hand) == before:
                break
            # Snapshot state as it was BEFORE the pop for honest odds.
            gs_snapshot_deck, gs.deck = gs.deck, deck_before
            try:
                record(gs, ms, player, one[0])
            finally:
                gs.deck = gs_snapshot_deck
            got.extend(one)
        return got

    def hooked_draw(gs, player, n=1, ms=None):
        for _ in range(n):
            before = len(player.hand)
            deck_before = list(gs.deck)
            real_draw(gs, player, 1, ms=ms)
            if len(player.hand) == before:
                break
            if ms is not None:
                gs_snapshot_deck, gs.deck = gs.deck, deck_before
                try:
                    record(gs, ms, player, player.hand[-1])
                finally:
                    gs.deck = gs_snapshot_deck

    real_assign = fish.assign_strategy_families_from_opening_hands

    def assign_hook(gs, *a, **k):
        real_gs["gs"] = gs
        return real_assign(gs, *a, **k)

    try:
        fish.draw_from_deck = hooked_draw_from_deck
        fish.draw = hooked_draw
        fish.assign_strategy_families_from_opening_hands = assign_hook
        fish.run_match(
            card_db=card_db,
            player_names=[f"P{i + 1}" for i in range(players)],
            action_policies=[policy] * players,
            seed=seed,
            max_turns=260,
            ai_difficulties=["medium"] * players,
        )
    finally:
        fish.draw_from_deck = real_draw_from_deck
        fish.draw = real_draw
        fish.assign_strategy_families_from_opening_hands = real_assign
    return events


def _z_score(events):
    """Poisson-binomial z: observed on-plan draws vs the sum of each draw's own
    odds. Every draw has a different probability (the deck changes constantly),
    so this is the exact test, not a fixed-p binomial."""
    obs = sum(1 for _, _, hit, _ in events if hit)
    exp = sum(p for _, _, _, p in events)
    var = sum(p * (1.0 - p) for _, _, _, p in events)
    if var <= 0:
        return obs, exp, 0.0
    return obs, exp, (obs - exp) / math.sqrt(var)


# A family needs this many real draws before its z-score means anything.
MIN_FAMILY_DRAWS = 300

# Overall false-alarm budget across ALL families in one run.
FAMILY_ALPHA = 0.001


def _family_z_threshold(num_families):
    """Two-sided z cutoff, Bonferroni-corrected for the number of strategies
    scored in this run, so the whole suite has a ~FAMILY_ALPHA false-alarm rate
    rather than that rate per strategy."""
    k = max(1, int(num_families))
    per_family = FAMILY_ALPHA / k
    return statistics.NormalDist().inv_cdf(1.0 - per_family / 2.0)


def test_C_on_plan_draw_rate_matches_deck_odds(games=96, workers=None):
    """The core test. If the engine withheld a plan's cards from the player
    running that plan, that plan's on-plan draw rate would sit BELOW the odds the
    remaining deck implies, and the z-score would go sharply negative."""
    workers = workers or min(os.cpu_count() or 4, 12)
    seeds = [secrets.randbits(64) for _ in range(games)]
    if workers > 1:
        with mp.Pool(workers) as pool:
            batches = pool.map(_play_one_measured_game, seeds)
    else:
        batches = [_play_one_measured_game(s) for s in seeds]
    events = [e for b in batches for e in b]
    assert len(events) > 2000, f"only {len(events)} draw events collected, raise --games"

    obs, exp, z = _z_score(events)
    print(
        f"\n    pooled: {len(events)} real in-game draws, on-plan {obs} vs {exp:.1f} "
        f"expected from the deck, z={z:+.2f}"
    )
    assert abs(z) < 4.0, (
        f"players draw their OWN strategy's cards at a rate that does not match the "
        f"deck (z={z:+.2f}): the deal is conditioned on what the player is playing"
    )

    by_family = collections.defaultdict(list)
    for e in events:
        by_family[e[0]].append(e)
    testable = [k for k, v in by_family.items() if len(v) >= MIN_FAMILY_DRAWS]

    # A dozen strategies are scored at once, so a fixed cutoff would cry wolf:
    # with 12 independent draws from N(0,1) something clears 4 sigma roughly once
    # every 1400 runs, which is often enough to make a green suite untrustworthy.
    # The cutoff is corrected for how many families were actually scored.
    z_crit = _family_z_threshold(len(testable))

    print(f"    per strategy (z<0 = withheld, z>0 = favoured, cutoff |z|>{z_crit:.2f}):")
    suspects = []
    for label in sorted(by_family, key=lambda k: -len(by_family[k])):
        evs = by_family[label]
        o, x, zz = _z_score(evs)
        testing = label in testable
        mark = "   <-- suspect" if testing and abs(zz) >= z_crit else ""
        note = "" if testing else "  (too few draws to score)"
        print(
            f"      {label:<22} {len(evs):>6} draws  on-plan {o:>5} vs {x:>7.1f}  z={zz:+.2f}{mark}{note}"
        )
        if testing and abs(zz) >= z_crit:
            suspects.append((label, zz))

    # Anything that trips the cutoff must REPLICATE on a fresh, independent set of
    # games before this fails. A real bias reproduces every time; noise does not.
    # (This is not a way of explaining a failure away: it is the second sample
    # that decides, and a suspect that repeats in the same direction fails hard.)
    if suspects:
        print(
            f"    {len(suspects)} suspect(s): {[l for l, _ in suspects]} -> "
            f"re-measuring on {games} fresh independent games"
        )
        seeds2 = [secrets.randbits(64) for _ in range(games)]
        if workers > 1:
            with mp.Pool(workers) as pool:
                batches2 = pool.map(_play_one_measured_game, seeds2)
        else:
            batches2 = [_play_one_measured_game(s) for s in seeds2]
        rerun = collections.defaultdict(list)
        for e in (x for b in batches2 for x in b):
            rerun[e[0]].append(e)

        confirmed = []
        for label, z1 in suspects:
            evs2 = rerun.get(label, [])
            _, _, z2 = _z_score(evs2)
            same_way = (z1 < 0) == (z2 < 0)
            repeats = len(evs2) >= MIN_FAMILY_DRAWS and abs(z2) >= z_crit and same_way
            print(
                f"      {label:<22} first z={z1:+.2f}  replication z={z2:+.2f} "
                f"({len(evs2)} draws) -> {'CONFIRMED' if repeats else 'did not replicate'}"
            )
            if repeats:
                confirmed.append((label, z1, z2))
        assert not confirmed, (
            f"these strategies draw their own cards below/above deck odds in TWO "
            f"independent samples, which is a real bias, not noise: {confirmed}"
        )

    # Seat fairness on the same measure: no chair draws better on-plan than another.
    by_seat = collections.defaultdict(list)
    for e in events:
        by_seat[e[1]].append(e)
    seat_z = {s: _z_score(v)[2] for s, v in sorted(by_seat.items())}
    # Same multiple-comparison correction as the per-family cutoff above: four
    # seats scored at once would otherwise trip a fixed cutoff on noise alone.
    seat_crit = _family_z_threshold(len(seat_z))
    off = {s: round(v, 2) for s, v in seat_z.items() if abs(v) >= seat_crit}
    assert not off, (
        f"seat bias in on-plan draws (cutoff |z|>{seat_crit:.2f}): {off} out of {seat_z}"
    )

    print(
        f"C PASS: across {games} real games and {len(events)} draws, every strategy draws "
        f"its own cards at exactly the rate the remaining deck implies "
        f"(seat z: {', '.join(f'{s + 1}:{v:+.2f}' for s, v in seat_z.items())})"
    )


# --------------------------------------------------------------------------
# D. Negative control: prove test C can actually SEE a rigged deal.
# --------------------------------------------------------------------------

def test_D_detector_catches_a_rigged_deal(games=96, workers=None):
    """A fairness test that cannot fail proves nothing.

    This takes the SAME real draw events test C measures, rigs them the way a
    player suspects the game is rigged (quietly swap a slice of a player's
    on-plan draws for off-plan ones), and confirms the detector fires. It also
    confirms the unrigged events from the same games do NOT fire, so the test is
    measuring the rigging and not just noise.
    """
    workers = workers or min(os.cpu_count() or 4, 12)
    seeds = [secrets.randbits(64) for _ in range(games)]
    if workers > 1:
        with mp.Pool(workers) as pool:
            batches = pool.map(_play_one_measured_game, seeds)
    else:
        batches = [_play_one_measured_game(s) for s in seeds]
    events = [e for b in batches for e in b]

    _, _, z_clean = _z_score(events)
    assert abs(z_clean) < 4.0, f"control sample was already off (z={z_clean:+.2f})"

    rng = random.Random(0xC0FFEE)
    cutoff = 4.0

    def rig_z(fraction):
        rigged = []
        for label, seat, hit, p in events:
            # The suspected cheat: you were owed a card for your plan, and the
            # game quietly hands you something else instead.
            if hit and rng.random() < fraction:
                hit = False
            rigged.append((label, seat, hit, p))
        return _z_score(rigged)[2]

    for rig in (0.10, 0.20):
        z_rig = rig_z(rig)
        assert z_rig < -cutoff, (
            f"a {rig:.0%} withholding of on-plan cards went UNDETECTED (z={z_rig:+.2f}) at "
            f"{games} games / {len(events)} draws, so a clean run at this sample size does "
            f"not prove much: re-run with more --games"
        )
        print(f"    withholding {rig:.0%} of on-plan draws -> z={z_rig:+.2f} (caught)")

    # The number that says how much this suite is actually worth: the smallest
    # rig this sample size can see. Anything at or above it cannot hide.
    smallest = next(
        (r for r in [round(0.01 * i, 2) for i in range(1, 21)] if rig_z(r) < -cutoff),
        None,
    )
    print(
        f"    smallest withholding detectable at {games} games "
        f"({len(events)} draws): {smallest:.0%}" if smallest else
        f"    (no withholding under 20% detectable at this sample size)"
    )

    print(
        f"D PASS: on {len(events)} real draws the detector reads z={z_clean:+.2f} clean and "
        f"goes sharply negative the moment cards are withheld: test C has real teeth"
    )


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=96,
                    help="real games for the statistical test (default 96)")
    ap.add_argument("--structural-games", type=int, default=3)
    ap.add_argument("--seeds", type=int, default=6, help="seeds for the causal test")
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args()

    test_A_deck_is_only_read_from_the_top(games=args.structural_games)
    test_B_deal_ignores_strategy_and_difficulty(trials=args.seeds)
    test_C_on_plan_draw_rate_matches_deck_odds(games=args.games, workers=args.workers)
    test_D_detector_catches_a_rigged_deal(games=args.games, workers=args.workers)
    print("\nALL DEAL / STRATEGY INDEPENDENCE TESTS PASSED ✓")


if __name__ == "__main__":
    main()
