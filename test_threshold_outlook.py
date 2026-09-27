"""A card that scores nothing until there are enough of them.

final_points scores the board exactly as it stands, and `sim_point_delta` is
that score played forward one move. So to the bots, a first Kelp Forest and a
third are both worth nothing, and so is the fourth minus twenty. They cannot
tell a Kelp Forest they are going to finish from one they are not.

Measured over 180 finished boards at six players:

    44%  ended holding one to three Kelp Forests, scoring NOTHING
    13%  held squids with fewer than three cephalopods under them
     6%  held a single Mandarin Goby, which scores 0 (two score 14)

`threshold_outlook` is the feature that asks the other question: what will this
card be worth once the count it builds toward lands, and can that count still
be reached at all? This suite checks it reads the game right.

Its WEIGHT is zero, so none of this changes how a bot plays until the trainer
measures a mutant that moves it and finds the mutant wins. That is checked too
-- it is the whole reason the feature can be added to a live ladder safely.

Run:  python3 test_threshold_outlook.py
"""
import random
import sys

import fish_game_all_in_one as fish

FAILS = []
PASSES = 0


def section(t):
    print(f"\n{t}")


def check(cond, msg, detail=""):
    global PASSES
    if cond:
        PASSES += 1
        print(f"  PASS  {msg}")
    else:
        FAILS.append(msg)
        print(f"  FAIL  {msg}" + (f"\n        {detail}" if detail else ""))


def table(db, n=4, seed=5):
    pair_map, face_map = fish.build_non_ocean_pair_maps(db)
    rng = random.Random(seed)
    deck, end_uid = fish.build_deck_with_late_end_game(db, pair_map, face_map, rng)
    players = [fish.PlayerState(f"P{i+1}") for i in range(n)]
    gs = fish.GameState(card_db=db, players=players, deck=deck)
    ms = fish.MatchState(end_game_uid=end_uid, pair_primary_to_faces=pair_map,
                         face_to_primary=face_map)
    return gs, ms


def pull(gs, ms, name, count=1):
    """Take entries carrying `name` out of the deck."""
    out = []
    for uid in list(gs.deck):
        if any(fish.card_name_lc(gs.card_db[f]) == name for f in fish.entry_faces(ms, uid)):
            gs.deck.remove(uid)
            out.append(uid)
            if len(out) >= count:
                break
    return out


def face_of(gs, ms, entry, name):
    for f in fish.entry_faces(ms, entry):
        if fish.card_name_lc(gs.card_db[f]) == name:
            return f
    return fish.entry_faces(ms, entry)[0]


def put_ocean(gs, player, face):
    player.board_oceans.append(face)
    player.ocean_slots[face] = fish.OceanSlots()


def attach(gs, player, ocean_face, face):
    player.ocean_slots[ocean_face].slot(
        fish.card_direction_lc(gs.card_db[face])).append(face)


def play_ocean_action(gs, ms, entry, name):
    f = face_of(gs, ms, entry, name)
    return fish.Action(kind="play_ocean", card_uid=entry, face_uid=f, ocean_uid=None)


def main():
    db = fish.load_card_db()

    # ── the safety property that lets this ship at all ──────────────────────
    section("the weight starts at zero, so nothing plays differently today")
    check(fish.default_weights()["threshold_outlook"] == 0.0,
          "threshold_outlook's default weight is exactly zero")
    w = fish.stabilize_weights(dict(fish.default_weights()))
    check(w.get("threshold_outlook", None) == 0.0,
          "...and it survives stabilize_weights still at zero",
          f"{w.get('threshold_outlook')}")

    # ── Kelp Forest: nothing below four ─────────────────────────────────────
    section("Kelp Forest scores nothing until the fourth one")
    # The FOURTH one is already visible to sim_point_delta -- it is +20 on the
    # board the moment it lands -- so this feature deliberately says nothing
    # about it and does not double-count. What it is for is the ones BEFORE it,
    # which the board-as-it-stands prices at exactly nothing.
    gs, ms = table(db)
    me = gs.players[0]
    for e in pull(gs, ms, "kelp forest", 2):
        put_ocean(gs, me, face_of(gs, ms, e, "kelp forest"))
    third = pull(gs, ms, "kelp forest", 1)[0]
    me.hand.append(third)
    fourth_in_hand = pull(gs, ms, "kelp forest", 1)[0]
    me.hand.append(fourth_in_hand)
    act3 = play_ocean_action(gs, ms, third, "kelp forest")
    fourth = fish.threshold_outlook(gs, ms, me, act3)
    check(fish.simulated_point_delta(gs, ms, me, act3) == 0.0,
          "the third Kelp Forest is worth nothing to the board as it stands")
    check(fourth > 0.2,
          "...but reads strongly positive with the fourth waiting in hand",
          f"{fourth:.3f}")

    # The first one, when four can no longer be had, is a dead card.
    gs, ms = table(db)
    me, rival = gs.players[0], gs.players[1]
    first = pull(gs, ms, "kelp forest", 1)[0]
    me.hand.append(first)
    # Every other Kelp Forest is on somebody else's board: four is impossible.
    rest = pull(gs, ms, "kelp forest", 20)
    base = pull(gs, ms, "deep ocean", 1)[0]
    put_ocean(gs, rival, face_of(gs, ms, base, "deep ocean"))
    for e in rest:
        rival.board_oceans.append(face_of(gs, ms, e, "kelp forest"))
        rival.ocean_slots[face_of(gs, ms, e, "kelp forest")] = fish.OceanSlots()
    dead = fish.threshold_outlook(gs, ms, me, play_ocean_action(gs, ms, first, "kelp forest"))
    check(dead < 0.0,
          "a Kelp Forest played when four can no longer be had reads NEGATIVE",
          f"{dead:.3f}")
    check(fourth > dead,
          "and the finishing fourth is worth more than the hopeless first",
          f"{fourth:.3f} vs {dead:.3f}")

    # ── the Coral Reef valley ───────────────────────────────────────────────
    section("the Coral Reef valley: four is 16, five is 0, six is 35")
    prof = fish._score_profile(
        [c for c in db.values() if fish.card_name_lc(c) == "coral reef"][0], None)
    vals = [fish._threshold_value(prof.table_pairs, n) for n in range(7)]
    check(vals[4] == 16 and vals[5] == 0 and vals[6] == 35,
          "the chart really does drop to nothing at five", f"{vals}")

    gs, ms = table(db)
    me = gs.players[0]
    for e in pull(gs, ms, "coral reef", 4):
        put_ocean(gs, me, face_of(gs, ms, e, "coral reef"))
    fifth_e = pull(gs, ms, "coral reef", 1)[0]
    me.hand.append(fifth_e)
    # ...with a sixth in hand, so six is certain.
    sixth_e = pull(gs, ms, "coral reef", 1)[0]
    me.hand.append(sixth_e)
    bridge = fish.threshold_outlook(gs, ms, me, play_ocean_action(gs, ms, fifth_e, "coral reef"))
    delta = fish.simulated_point_delta(gs, ms, me, play_ocean_action(gs, ms, fifth_e, "coral reef"))
    check(delta < 0,
          "playing the fifth reef looks like a loss to the board as it stands",
          f"sim_point_delta {delta:+.1f}")
    check(bridge > 0,
          "...but reads POSITIVE once the sixth waiting in hand is counted",
          f"threshold_outlook {bridge:+.3f}")

    # No sixth anywhere: the fifth really is just a loss.
    gs, ms = table(db)
    me, rival = gs.players[0], gs.players[1]
    for e in pull(gs, ms, "coral reef", 4):
        put_ocean(gs, me, face_of(gs, ms, e, "coral reef"))
    lone_fifth = pull(gs, ms, "coral reef", 1)[0]
    me.hand.append(lone_fifth)
    for e in pull(gs, ms, "coral reef", 20):
        f = face_of(gs, ms, e, "coral reef")
        rival.board_oceans.append(f)
        rival.ocean_slots[f] = fish.OceanSlots()
    nobridge = fish.threshold_outlook(
        gs, ms, me, play_ocean_action(gs, ms, lone_fifth, "coral reef"))
    check(nobridge < 0,
          "with every other reef on another board, the fifth reads negative",
          f"{nobridge:+.3f}")
    check(bridge > nobridge,
          "a fifth reef with a sixth behind it beats one with nothing behind it",
          f"{bridge:+.3f} vs {nobridge:+.3f}")

    # ── baitfish: the chart counts SPECIES, not copies ──────────────────────
    section("the baitfish chart counts different species, not copies")
    gs, ms = table(db)
    me = gs.players[0]
    base = pull(gs, ms, "deep ocean", 1)[0]
    bf = face_of(gs, ms, base, "deep ocean")
    put_ocean(gs, me, bf)
    # Three Bonito down: that is ONE species, worth 1, not three worth 11.
    for e in pull(gs, ms, "bonito", 3):
        attach(gs, me, bf, face_of(gs, ms, e, "bonito"))
    another_bonito = pull(gs, ms, "bonito", 1)[0]
    me.hand.append(another_bonito)
    act_same = fish.Action(kind="play_to_ocean", card_uid=another_bonito,
                           face_uid=face_of(gs, ms, another_bonito, "bonito"), ocean_uid=bf)
    same = fish.threshold_outlook(gs, ms, me, act_same)
    check(same == 0.0,
          "a FOURTH Bonito adds no species, so it reads exactly 0",
          f"{same:+.3f}")

    new_kind = pull(gs, ms, "sardine", 1)[0]
    me.hand.append(new_kind)
    act_new = fish.Action(kind="play_to_ocean", card_uid=new_kind,
                          face_uid=face_of(gs, ms, new_kind, "sardine"), ocean_uid=bf)
    fresh = fish.threshold_outlook(gs, ms, me, act_new)
    check(fresh > 0.0,
          "...while a Sardine is a second species and reads positive",
          f"{fresh:+.3f}")
    check(fresh > same,
          "a new species is worth more than another copy of one already down",
          f"{fresh:+.3f} vs {same:+.3f}")

    # ── the squids: paid by a count of OTHER cards ──────────────────────────
    section("a squid is worth nothing until three cephalopods sit together")
    gs, ms = table(db)
    me = gs.players[0]
    base = pull(gs, ms, "deep ocean", 1)[0]
    bf = face_of(gs, ms, base, "deep ocean")
    put_ocean(gs, me, bf)
    squid = pull(gs, ms, "giant squid", 1)[0]
    me.hand.append(squid)
    # Two more cephalopods in hand, so three is certain.
    for nm in ("bobtail squid", "cuttlefish"):
        me.hand.append(pull(gs, ms, nm, 1)[0])
    act = fish.Action(kind="play_to_ocean", card_uid=squid,
                      face_uid=face_of(gs, ms, squid, "giant squid"), ocean_uid=bf)
    withfriends = fish.threshold_outlook(gs, ms, me, act)
    check(withfriends > 0.0,
          "a Giant Squid with two more cephalopods in hand reads positive",
          f"{withfriends:+.3f}")

    # Now with every other cephalopod on other boards: three is impossible.
    gs, ms = table(db)
    me, rival = gs.players[0], gs.players[1]
    base = pull(gs, ms, "deep ocean", 1)[0]
    bf = face_of(gs, ms, base, "deep ocean")
    put_ocean(gs, me, bf)
    squid = pull(gs, ms, "giant squid", 1)[0]
    me.hand.append(squid)
    rbase = pull(gs, ms, "deep ocean", 1)[0]
    rbf = face_of(gs, ms, rbase, "deep ocean")
    put_ocean(gs, rival, rbf)
    for nm in ("bobtail squid", "cuttlefish", "common octopus", "giant squid"):
        for e in pull(gs, ms, nm, 9):
            attach(gs, rival, rbf, face_of(gs, ms, e, nm))
    act = fish.Action(kind="play_to_ocean", card_uid=squid,
                      face_uid=face_of(gs, ms, squid, "giant squid"), ocean_uid=bf)
    alone = fish.threshold_outlook(gs, ms, me, act)
    check(alone < 0.0,
          "...and reads negative when every other cephalopod is on another board",
          f"{alone:+.3f}")
    check(withfriends > alone,
          "a squid with company beats a squid that will never have any",
          f"{withfriends:+.3f} vs {alone:+.3f}")

    # ── a card with no count at all ─────────────────────────────────────────
    section("a card whose score does not depend on a count is left alone")
    gs, ms = table(db)
    me = gs.players[0]
    base = pull(gs, ms, "deep ocean", 1)[0]
    bf = face_of(gs, ms, base, "deep ocean")
    put_ocean(gs, me, bf)
    lob = pull(gs, ms, "lobster", 1)[0]
    me.hand.append(lob)
    act = fish.Action(kind="play_to_ocean", card_uid=lob,
                      face_uid=face_of(gs, ms, lob, "lobster"), ocean_uid=bf)
    check(fish.threshold_outlook(gs, ms, me, act) == 0.0,
          "a Lobster is +4 whatever else is on the board, so this reads exactly 0")
    draw = fish.Action(kind="draw_deck", card_uid=None, face_uid=None, ocean_uid=None)
    check(fish.threshold_outlook(gs, ms, me, draw) == 0.0,
          "and a draw is not a play, so it reads 0 too")

    # ── the feature is actually wired into the vector ───────────────────────
    section("it reaches the bots through action_features")
    gs, ms = table(db)
    me = gs.players[0]
    for e in pull(gs, ms, "kelp forest", 2):
        put_ocean(gs, me, face_of(gs, ms, e, "kelp forest"))
    nxt = pull(gs, ms, "kelp forest", 1)[0]
    me.hand.append(nxt)
    me.hand.append(pull(gs, ms, "kelp forest", 1)[0])
    feats = fish.action_features(gs, ms, me, play_ocean_action(gs, ms, nxt, "kelp forest"))
    check("threshold_outlook" in feats,
          "action_features carries threshold_outlook")
    check(feats.get("threshold_outlook", 0.0) > 0.2,
          "...with the third Kelp Forest's value in it",
          f"{feats.get('threshold_outlook')}")
    scored_zero = fish.weighted_score(feats, fish.stabilize_weights(dict(fish.default_weights())))
    feats_without = dict(feats)
    feats_without["threshold_outlook"] = 0.0
    check(abs(scored_zero - fish.weighted_score(
              feats_without, fish.stabilize_weights(dict(fish.default_weights())))) < 1e-12,
          "and at its default weight of zero it changes the move's score by nothing at all")

    print("\n" + "=" * 52)
    print(f"RESULT: {PASSES} passed, {len(FAILS)} failed")
    for f in FAILS:
        print(f"  - {f}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
