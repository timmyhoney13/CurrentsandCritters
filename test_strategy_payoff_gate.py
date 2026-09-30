"""No bot commits to a plan it has no way to score.

The complaint this suite exists for: every bot at the table was playing King
Salmon -- packing four cards into every ocean, turn after turn -- and none of
them was holding a King Salmon. "+5 per fully occupied ocean" is the only card
in the deck that pays for a filled ocean, so all of that work scored exactly
zero. The same hole runs through every plan:

    Invertebrates without a Red Beaded Anemone or a Barracuda is a board of
    Sea Stars, Sea Urchins and Sea Cucumbers, every one of which is worth NO
    POINTS AT ALL. They are draw engines. The plan scores nothing.

So a bot may only commit to a plan when it can actually get the card that makes
that plan score -- in hand, already on its board, or face-up in the Pool -- and
when it has enough bodies for that card to multiply. Not "somewhere in the
deck": that is exactly the hope that put a whole table on King Salmon.

Five things are checked:

  1. THE TABLE IS TRUE. Every card named as a plan's multiplier is a real card
     whose PRINTED TEXT really does pay per member of that family or chart a
     count of them, and no family that has such a card is missing it. Read off
     the live card database, so a card text edit cannot quietly break the gate.
  2. THE GATE HOLDS. Hand-built positions: a bot with no King Salmon anywhere
     never picks King Salmon; one holding a King Salmon may; one that can see
     the last Red Beaded Anemone in the Pool may take Invertebrates, and one
     whose Anemones are both on other players' boards may not.
  2b. WHAT HAPPENS WHEN NOTHING PAYS. A bot that can pay for nothing takes the
     plan its hand fits best, is marked unpaid, and moves once a real
     multiplier arrives -- then commits. Plus the two bugs the gate uncovered:
     mid-game adoption raising NameError on an undefined `diff`, and the
     planner writing its own pick over a plan training had asked for by name.
  3. REAL GAMES. Full matches at the table size given (six by default), every
     bot's opening pick measured: the multiplier is in reach every time.
  4. THE PLANNER TOO. The A+ Reef Planner picks by imagining the cards it is
     going to draw, which is how it talked itself onto King Salmon in the first
     place. Its picks are measured the same way.

Run:  python3 test_strategy_payoff_gate.py [--games N] [--players N]
"""
import argparse
import collections
import random
import re
import secrets
import sys

import fish_game_all_in_one as fish
import reef_planner


FAILS = []
PASSES = 0


def section(title):
    print(f"\n{title}")


def check(cond, msg, detail=""):
    global PASSES
    if cond:
        PASSES += 1
        print(f"  PASS  {msg}")
    else:
        FAILS.append(msg)
        print(f"  FAIL  {msg}" + (f"\n        {detail}" if detail else ""))


# ──────────────────────────────────────────────────────────────────────────
# 1. The table is true: every multiplier is a real card that really multiplies
# ──────────────────────────────────────────────────────────────────────────

# What each plan's cards ARE, for the sweep that looks for a multiplier we
# forgot to list. Species is how the deck itself groups them.
FAMILY_SPECIES = {
    "mammals": "mammal",
    "baitfish_barrage": "baitfish",
    "birds_of_a_feather": "bird",
    "crustaceans": "crustacean",
    "coral": "coral",
    "cephalopods": "cephalopod",
    "invertebrates": "invertebrate",
}

# The words a card uses when it pays for a family: "+3 per invertebrate",
# "# of razorbill auks 1 = 5 | 2 = 25", "+6 if you have at least three
# cephalopods".
PER_RE = re.compile(r"\+\s*\d+\s*per\s+([a-z' ]+)", re.I)
CHART_RE = re.compile(r"\d\s*=\s*\d")
LEAST_RE = re.compile(r"at least \w+ ([a-z]+)", re.I)


def card_index(db):
    by_name = {}
    for c in db.values():
        by_name.setdefault(fish.card_name_lc(c), c)
    return by_name


def test_1_table_is_true(db):
    section("1. every card named as a multiplier really is one")
    by_name = card_index(db)

    for label, names in sorted(fish.STRATEGY_PAYOFF_CARDS.items()):
        real = [n for n in names if n in by_name]
        check(bool(real),
              f"{label}: at least one spelling of its multiplier is a real card",
              f"listed {names}, none of them in the deck")
        for n in real:
            text = by_name[n].text.lower()
            pays = bool(PER_RE.search(text) or CHART_RE.search(text)
                        or LEAST_RE.search(text) or "if you have" in text)
            check(pays, f"{label}: '{n}' is printed as a multiplier or a count chart",
                  f"text reads: {by_name[n].text!r}")

    # The other direction: a per-family multiplier we failed to list.
    section("1b. no per-family multiplier is missing from the table")
    for label, species in sorted(FAMILY_SPECIES.items()):
        listed = {n for n in fish.strategy_payoff_names(label)}
        missed = []
        for name, c in sorted(by_name.items()):
            m = PER_RE.search(c.text)
            if not m:
                continue
            target = m.group(1).strip().lower().rstrip("s")
            if target != species.rstrip("s"):
                continue
            if name not in listed:
                missed.append((name, c.text))
        check(not missed,
              f"{label}: every '+N per {species}' card in the deck is listed",
              f"missing: {missed}")

    # And the plans that have no multiplier at all must be the ones we meant.
    section("1c. a plan with no multiplier listed is a deliberate choice")
    for fam in fish.strategy_family_profiles():
        label = str(fam.get("label", ""))
        if label in fish.STRATEGY_PAYOFF_CARDS:
            continue
        check(label in fish.HYBRID_COMPONENTS,
              f"{label} has no multiplier of its own, and is a combo that borrows its parents'",
              "a single plan with no payoff card listed is a hole in the gate")

    section("1d. the cards the complaint named")
    ks = by_name.get("king salmon")
    check(ks is not None and "per fully occupied ocean" in ks.text.lower(),
          "King Salmon is the card that pays for a filled ocean",
          f"{ks.text if ks else 'missing'}")
    check(fish.STRATEGY_PAYOFF_CARDS["king_salmon"] == ("king salmon",),
          "...and it is the ONLY way the King Salmon plan scores")
    inv = set(fish.STRATEGY_PAYOFF_CARDS["invertebrates"])
    check(inv == {"red beaded anemone", "barracuda"},
          "Invertebrates pays through the Red Beaded Anemone or the Barracuda, nothing else",
          f"{inv}")
    # The rest of the invertebrates really are worth zero points.
    zero = []
    for name, c in by_name.items():
        if fish.card_species_lc(c) == "invertebrate" and name not in inv:
            if not re.search(r"\+\s*\d", c.text):
                zero.append(name)
    check(len(zero) >= 3,
          "the other invertebrates are draw engines worth no points on their own",
          f"{sorted(zero)}")


# ──────────────────────────────────────────────────────────────────────────
# 2. The gate holds on hand-built positions
# ──────────────────────────────────────────────────────────────────────────

def build_table(db, n=4):
    """A bare game: n players, empty boards, empty Pool, full deck."""
    pair_map, face_map = fish.build_non_ocean_pair_maps(db)
    rng = random.Random(7)
    deck, end_uid = fish.build_deck_with_late_end_game(db, pair_map, face_map, rng)
    players = [fish.PlayerState(f"P{i+1}") for i in range(n)]
    gs = fish.GameState(card_db=db, players=players, deck=deck)
    ms = fish.MatchState(end_game_uid=end_uid, pair_primary_to_faces=pair_map,
                         face_to_primary=face_map)
    return gs, ms


def entries_named(gs, ms, name, count=1):
    """Deck entries whose face is `name`, pulled out of the deck."""
    out = []
    for uid in list(gs.deck):
        if any(fish.card_name_lc(gs.card_db[f]) == name for f in fish.entry_faces(ms, uid)):
            gs.deck.remove(uid)
            out.append(uid)
            if len(out) >= count:
                break
    return out


def give(gs, ms, player, *names):
    for n in names:
        player.hand.extend(entries_named(gs, ms, n, 1))


def face_named(gs, ms, entry_uid, name):
    """The face of a two-sided entry that carries `name`. A card can be the
    BACK of its pair, so entry_faces(...)[0] is often the other animal."""
    for f in fish.entry_faces(ms, entry_uid):
        if fish.card_name_lc(gs.card_db[f]) == name:
            return f
    return fish.entry_faces(ms, entry_uid)[0]


def play_onto_board(gs, ms, player, *names):
    """Put an ocean down for `player` and attach each named card to it."""
    ocean = entries_named(gs, ms, "deep ocean", 1)[0]
    player.board_oceans.append(ocean)
    player.ocean_slots[ocean] = fish.OceanSlots()
    placed = []
    for nm in names:
        for e in entries_named(gs, ms, nm, 1):
            f = face_named(gs, ms, e, nm)
            player.ocean_slots[ocean].slot(
                fish.card_direction_lc(gs.card_db[f])).append(f)
            placed.append(f)
    return placed


def test_2_gate_holds(db):
    section("2. the gate, on positions built by hand")

    # 2a. No King Salmon anywhere: the plan is refused.
    gs, ms = build_table(db)
    me = gs.players[0]
    give(gs, ms, me, "arctic ocean", "mangrove", "horned puffin",
         "bottlenose dolphin", "sailfish", "goliath grouper", "clownfish")
    check(fish.strategy_payoff_veto(gs, ms, me, "king_salmon"),
          "a hand full of ocean-fillers and no King Salmon is refused King Salmon",
          f"outlook={fish.strategy_payoff_outlook(gs, ms, me, 'king_salmon')}")

    # 2b. Holding one: allowed.
    give(gs, ms, me, "king salmon")
    check(not fish.strategy_payoff_veto(gs, ms, me, "king_salmon"),
          "the same hand WITH a King Salmon may play King Salmon")

    # 2c. Invertebrates: the user's example, exactly.
    gs, ms = build_table(db)
    me = gs.players[0]
    give(gs, ms, me, "common sea star", "sea urchin", "johnson's sea cucumber",
         "orange tube sponge", "deep ocean")
    check(fish.strategy_payoff_veto(gs, ms, me, "invertebrates"),
          "invertebrates with no Red Beaded Anemone and no Barracuda is refused",
          f"outlook={fish.strategy_payoff_outlook(gs, ms, me, 'invertebrates')}")
    give(gs, ms, me, "red beaded anemone")
    check(not fish.strategy_payoff_veto(gs, ms, me, "invertebrates"),
          "a Red Beaded Anemone turns the same board into a plan")

    gs, ms = build_table(db)
    me = gs.players[0]
    give(gs, ms, me, "common sea star", "sea urchin", "johnson's sea cucumber",
         "orange tube sponge", "barracuda")
    check(not fish.strategy_payoff_veto(gs, ms, me, "invertebrates"),
          "...and so does a Barracuda")

    # 2d. The Pool is public and takeable: a multiplier sitting in it counts.
    gs, ms = build_table(db)
    me = gs.players[0]
    give(gs, ms, me, "common sea star", "sea urchin", "johnson's sea cucumber")
    ms.pool.extend(entries_named(gs, ms, "red beaded anemone", 1))
    check(not fish.strategy_payoff_veto(gs, ms, me, "invertebrates"),
          "a Red Beaded Anemone face-up in the Pool is close enough to commit on")

    # 2e. A multiplier with nothing to multiply is not a plan either -- but
    #     WHEN decides it. On turn one there is a whole game to collect them in;
    #     in the last rounds there is not, and "+3 per nothing" is nothing.
    gs, ms = build_table(db)
    me = gs.players[0]
    give(gs, ms, me, "red beaded anemone", "arctic ocean", "deep ocean", "pier")
    check(fish.strategy_min_body(gs) == fish.STRATEGY_MIN_BODY_EARLY,
          "a full deck asks for the fewest bodies: there is a game ahead to collect them",
          f"asked for {fish.strategy_min_body(gs)}")
    check(not fish.strategy_payoff_veto(gs, ms, me, "invertebrates"),
          "an Anemone on turn one is a plan, with the invertebrates still to come")
    del gs.deck[12:]          # the last couple of rounds
    check(fish.strategy_min_body(gs) == fish.STRATEGY_MIN_BODY,
          "a deck this short asks for the full body count: nothing more is coming",
          f"asked for {fish.strategy_min_body(gs)}")
    check(fish.strategy_payoff_veto(gs, ms, me, "invertebrates"),
          "the same Anemone at the end of the game is refused: +3 per nothing is nothing",
          f"outlook={fish.strategy_payoff_outlook(gs, ms, me, 'invertebrates')}")

    # 2f. Every copy on someone else's board: the plan is dead, not merely thin.
    gs, ms = build_table(db, n=3)
    me, rival_a, rival_b = gs.players
    give(gs, ms, me, "common sea star", "sea urchin", "johnson's sea cucumber",
         "orange tube sponge")
    for rival in (rival_a, rival_b):
        play_onto_board(gs, ms, rival, "red beaded anemone")
    ol = fish.strategy_payoff_outlook(gs, ms, me, "invertebrates")
    # Both Anemones are gone; the three Barracuda are still unseen.
    check(ol["gone"] == 2.0, "both Red Beaded Anemones are counted as gone off other boards",
          f"{ol}")
    check(fish.strategy_payoff_veto(gs, ms, me, "invertebrates"),
          "with the Anemones on other boards and no Barracuda, the plan is refused")

    # 2g. Reading the table: an opponent's BOARD says what they are playing,
    #     even when nothing has flagged them with a plan.
    gs, ms = build_table(db, n=3)
    me, rival, _quiet = gs.players
    play_onto_board(gs, ms, rival, "bobtail squid", "common octopus",
                    "cuttlefish", "giant squid")
    check(not rival.flags.get("_strategy_family"),
          "the opponent carries no strategy flag at all")
    crowd = reef_planner.crowd_by_family(gs, ms, me)
    check(crowd.get("cephalopods", 0) == 1,
          "the planner still reads Cephalopods off their board and counts it as taken",
          f"{crowd}")


def test_2b_unpaid_and_forced(db):
    section("2b. a plan that cannot pay is not committed to, and a forced one is")

    # A bot dealt no multiplier for anything takes the best plan going, is
    # marked unpaid, and moves the moment a real one arrives.
    gs, ms = build_table(db)
    me = gs.players[0]
    me.flags["_ai_skill_level"] = "expert"
    me.flags["_ai_difficulty"] = "hard"
    me.flags["_strategy_family"] = "king_salmon"
    me.flags["_strategy_family_unpaid"] = True
    give(gs, ms, me, "red beaded anemone", "common sea star", "sea urchin",
         "johnson's sea cucumber", "orange tube sponge")
    moved = fish.maybe_reassess_strategy_family(gs, ms, me)
    check(moved == "invertebrates",
          "an unpaid King Salmon bot handed an Anemone and a pile of invertebrates moves to them",
          f"moved to {moved!r}")
    check(not me.flags.get("_strategy_family_unpaid"),
          "...and is no longer marked unpaid once it has a plan that pays")
    check(fish.maybe_reassess_strategy_family(gs, ms, me) is None,
          "...and does not keep moving afterwards: it is committed now")

    # Mid-game adoption used to raise NameError on an undefined `diff` and get
    # swallowed by the caller's bare except, so a bot with no plan never got one.
    gs, ms = build_table(db)
    me = gs.players[0]
    me.flags["_ai_skill_level"] = "expert"
    me.flags["_ai_difficulty"] = "hard"
    give(gs, ms, me, "red beaded anemone", "common sea star", "sea urchin",
         "johnson's sea cucumber")
    adopted = fish.maybe_reassess_strategy_family(gs, ms, me)
    check(adopted, "a bot with no plan at all adopts one mid-game", f"{adopted!r}")
    check(not fish.strategy_payoff_veto(gs, ms, me, adopted or ""),
          "...and the one it adopts is one it can score")

    # Training asks for a plan by name. The planner must play THAT plan.
    gs, ms = build_table(db)
    me = gs.players[0]
    me.flags["_force_strategy_family"] = "king_salmon"
    for _ in range(7):
        me.hand.append(gs.deck.pop(0))
    params = dict(reef_planner.PARAMS)
    params["time_budget"] = 0.5
    reef_planner.choose_action(gs, ms, me, params=params,
                               rng=random.Random(11))
    check(me.flags.get("_strategy_family") == "king_salmon",
          "a forced plan survives the planner's own opinion, gate and all",
          f"{me.flags.get('_strategy_family')!r} "
          f"(source {me.flags.get('_strategy_family_source')!r})")


# ──────────────────────────────────────────────────────────────────────────
# 3 + 4. Real games
# ──────────────────────────────────────────────────────────────────────────

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


def _reach_at_pick(player):
    """The reach the gate itself accepted, recorded on the player. Reading it
    back later gives a different number: by then the other seats have taken
    their plans, and a rival on the same plan lowers it."""
    return float(player.flags.get("_strategy_family_reach", 0.0))


def test_3_real_games(db, games, players):
    section(f"3. {games} real games at {players} players: every opening pick, measured")
    weights, maps = _brain_maps()

    def policy(gs, ms, p):
        return fish.choose_action_weighted(gs, ms, p, weights, epsilon=0.0, **maps)

    real = fish.assign_strategy_families_from_opening_hands
    picks = []

    def hook(gs, ms, brain, humans, rng):
        out = real(gs, ms, brain, humans, rng)
        by_name = {p.name: p for p in gs.players}
        for name, label, _fit in out:
            p = by_name[name]
            picks.append((label, _reach_at_pick(p),
                          bool(p.flags.get("_strategy_family_unpaid"))))
        return out

    fish.assign_strategy_families_from_opening_hands = hook
    try:
        for _ in range(games):
            fish.run_match(card_db=db,
                           player_names=[f"P{i+1}" for i in range(players)],
                           action_policies=[policy] * players,
                           seed=secrets.randbits(64), max_turns=260,
                           ai_difficulties=["hard"] * players)
    finally:
        fish.assign_strategy_families_from_opening_hands = real

    bad = [(l, r) for l, r, unpaid in picks if r < fish.MIN_PAYOFF_REACH and not unpaid]
    check(bool(picks), "the games produced opening picks to measure", f"{len(picks)}")
    check(not bad,
          f"all {len(picks)} opening picks can reach the card that makes them score",
          f"{collections.Counter(l for l, _ in bad).most_common()}")
    forced = [l for l, _r, unpaid in picks if unpaid]
    check(len(forced) <= max(1, len(picks) // 10),
          "and almost none of them had to fall back to an unpayable plan",
          f"{len(forced)} of {len(picks)}: {collections.Counter(forced).most_common()}")
    ks = [(l, r, u) for l, r, u in picks if l == "king_salmon"]
    check(all(r >= fish.MIN_PAYOFF_REACH or u for _l, r, u in ks),
          f"no bot went King Salmon without one ({len(ks)} King Salmon picks)")
    return picks


def test_4_planner(db, games, players):
    section(f"4. the Reef Planner's own picks, over {games} games at {players} players")
    params = reef_planner.params_for_grade("A+") or dict(reef_planner.PARAMS)
    seen = []
    real = reef_planner.choose_family

    def hook(gs, ms, player, prms, rng, worlds=4):
        fam, values = real(gs, ms, player, prms, rng, worlds=worlds)
        seen.append((fam, _reach_at_pick(player),
                     bool(player.flags.get("_planner_family_unpaid"))))
        return fam, values

    reef_planner.choose_family = hook
    try:
        for _ in range(games):
            fish.run_match(
                card_db=db,
                player_names=[f"A{i+1}" for i in range(players)],
                action_policies=[
                    (lambda gs, ms, p: reef_planner.choose_action(gs, ms, p, params=params))
                ] * players,
                seed=secrets.randbits(64), max_turns=260,
                ai_difficulties=["hard"] * players,
            )
    finally:
        reef_planner.choose_family = real

    check(bool(seen), "the planner chose plans to measure", f"{len(seen)}")
    bad = [(f, r) for f, r, unpaid in seen if r < fish.MIN_PAYOFF_REACH and not unpaid]
    check(not bad,
          f"all {len(seen)} planner picks can reach their multiplier",
          f"{collections.Counter(f for f, _ in bad).most_common()}")
    ks = [(f, r, u) for f, r, u in seen if f == "king_salmon"]
    check(all(r >= fish.MIN_PAYOFF_REACH or u for _f, r, u in ks),
          f"the planner never goes King Salmon without one ({len(ks)} King Salmon picks)")
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=6)
    ap.add_argument("--players", type=int, default=6)
    ap.add_argument("--planner-games", type=int, default=2)
    args = ap.parse_args()

    db = fish.load_card_db()
    test_1_table_is_true(db)
    test_2_gate_holds(db)
    test_2b_unpaid_and_forced(db)
    test_3_real_games(db, args.games, args.players)
    test_4_planner(db, args.planner_games, min(4, args.players))

    print("\n" + "=" * 50)
    print(f"RESULT: {PASSES} passed, {len(FAILS)} failed")
    for f in FAILS:
        print(f"  - {f}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
