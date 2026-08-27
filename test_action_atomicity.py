"""An action either happens completely, or it does not happen at all.

The bug this pins down, reported as "I got to play a mammal, I realised I had no
spots left for it, and it did not give me back the cards I paid, but it let me
play again": apply_action mutated as it went. It consumed the free-play flag,
moved the payment cards from the hand into the pool and pulled the played card
out of the hand BEFORE it resolved the card onto the board. Any rejection or
exception after that point returned False, so the server told the player "that
move did not happen" and re-offered legal actions, while the cards the move cost
were already gone for good.

Every failure exit is now a rollback, so these tests assert the property rather
than any one of the ~40 fail() sites: after a failed action the entire game state
is byte-identical to what it was before the attempt.
"""
import copy
import random
import sys

import fish_game_all_in_one as fish


# ── helpers ──────────────────────────────────────────────────────────────────

def fingerprint(gs, ms, turn_state=None):
    """Everything an action is allowed to touch, in one comparable value."""
    players = [
        (
            p.name,
            list(p.hand),
            list(p.discard),
            list(p.board_oceans),
            {
                uid: (list(s.up), list(s.down), list(s.left), list(s.right))
                for uid, s in sorted(p.ocean_slots.items())
            },
            p.score,
            p.energy,
            {k: repr(v) for k, v in sorted(p.flags.items())},
        )
        for p in gs.players
    ]
    core = (
        players,
        list(gs.deck),
        list(gs.log),
        gs.turn_index,
        gs.round_count,
        gs.end_game_triggered,
        gs.end_game_trigger_turn_player,
        gs.turns_remaining_after_trigger,
        list(ms.pool),
        list(ms.discard_pile),
        ms.end_game_uid,
        ms.end_game_triggered,
        ms.final_turns_remaining,
    )
    if turn_state is None:
        return core
    return core + (
        turn_state.star_activations,
        turn_state.free_followups,
        list(turn_state.played_face_uids),
        sorted(turn_state.discarded_entry_uids),
        turn_state.replay_pickup_used,
        turn_state.force_end_turn,
        turn_state.draws_this_turn,
    )


OCEAN = fish.CardDef(uid=1, name="Kelp Forest", species="Ocean", cost=0,
                     direction="N/A", symbol="N/A", text="Draw one.")
MAMMAL = fish.CardDef(uid=3, name="Sea Otter", species="Mammal", cost=2,
                      direction="up", symbol="Circle", text="Draw one. *Draw two.*")
PAY_A = fish.CardDef(uid=5, name="Anchovy", species="Bait Fish", cost=0,
                     direction="down", symbol="Circle", text="")
PAY_B = fish.CardDef(uid=7, name="Sardine", species="Bait Fish", cost=0,
                     direction="down", symbol="Square", text="")
MINI_DB = {c.uid: c for c in (OCEAN, MAMMAL, PAY_A, PAY_B)}


def mini_game(**player_kwargs):
    p = fish.PlayerState(
        name="Tim", hand=[3, 5, 7], board_oceans=[1],
        ocean_slots={1: fish.OceanSlots()}, **player_kwargs
    )
    opp = fish.PlayerState(name="Opp", hand=[], board_oceans=[], ocean_slots={})
    gs = fish.GameState(card_db=MINI_DB, players=[p, opp], deck=[11, 12, 13])
    return gs, fish.MatchState(), fish.TurnState(), p


class Boom(RuntimeError):
    pass


def with_exploding_ability(fn):
    """Run fn with the ability resolver raising: the `except Exception` arm inside
    the play resolver is real, card text is interpreted at runtime."""
    original = fish.run_main_ability

    def explode(*_a, **_k):
        raise Boom("ability blew up")

    fish.run_main_ability = explode
    try:
        return fn()
    finally:
        fish.run_main_ability = original


CHECKS = [0]


def check(cond, label):
    CHECKS[0] += 1
    if not cond:
        raise AssertionError(label)


# ── tests ────────────────────────────────────────────────────────────────────

def test_failed_play_returns_the_payment():
    gs, ms, ts, p = mini_game()
    before = fingerprint(gs, ms, ts)
    reasons = []
    action = fish.Action(kind="play_to_ocean", card_uid=3, ocean_uid=1, payment_uids=[5, 7])
    ok = with_exploding_ability(
        lambda: fish.apply_action(gs, ms, p, action, ts, fish.choose_payment_ai,
                                  verbose=False, fail_reason=reasons)
    )
    check(ok is False, "a play whose resolution blows up must report failure")
    check(reasons, "a failure must say why")
    check(p.hand == [3, 5, 7], f"the whole hand comes back, got {p.hand}")
    check(ms.pool == [], f"nothing reaches the pool, got {ms.pool}")
    check(fingerprint(gs, ms, ts) == before, "the failed play left the game changed")
    print("A PASS: a play that fails after payment gives back every card, the "
          "played one included")


def test_failed_play_keeps_the_free_play():
    # ★ on a card the free-play flag has just made free: the cost check rejects it
    # AFTER consume_free_flag_if_applicable has already spent the flag.
    gs, ms, ts, p = mini_game()
    p.flags["free_mammal"] = True
    before = fingerprint(gs, ms, ts)
    action = fish.Action(kind="play_to_ocean", card_uid=3, ocean_uid=1, use_star=True)
    ok = fish.apply_action(gs, ms, p, action, ts, fish.choose_payment_ai, verbose=False)
    check(ok is False, "a ★ with no payable cost is rejected")
    check(p.flags.get("free_mammal") is True, "the free mammal must survive a rejected play")
    check(fingerprint(gs, ms, ts) == before, "the rejected ★ play left the game changed")

    # Same for a payment the client got wrong: the flag is spent before the count
    # is even looked at.
    gs, ms, ts, p = mini_game()
    p.flags["free_mammal"] = True
    before = fingerprint(gs, ms, ts)
    action = fish.Action(kind="play_to_ocean", card_uid=3, ocean_uid=1, payment_uids=[5])
    ok = fish.apply_action(gs, ms, p, action, ts, fish.choose_payment_ai, verbose=False)
    check(ok is False, "a wrong payment count is rejected")
    check(p.flags.get("free_mammal") is True, "the free mammal must survive a bad payment")
    check(fingerprint(gs, ms, ts) == before, "the bad payment left the game changed")

    # And the single-use free cephalopod, and the counted yellowfin charges.
    gs, ms, ts, p = mini_game()
    p.flags["free_yellowfin_tuna"] = 2
    before_charges = p.flags["free_yellowfin_tuna"]
    fish.apply_action(gs, ms, p, fish.Action(kind="play_to_ocean", card_uid=3, ocean_uid=1,
                                             payment_uids=[5]),
                      ts, fish.choose_payment_ai, verbose=False)
    check(p.flags["free_yellowfin_tuna"] == before_charges,
          "counted free charges must not be spent by a rejected play")
    print("B PASS: a rejected play never burns a free play")


def test_successful_play_still_commits():
    gs, ms, ts, p = mini_game()
    ok = fish.apply_action(gs, ms, p, fish.Action(kind="play_to_ocean", card_uid=3,
                                                  ocean_uid=1, payment_uids=[5, 7]),
                           ts, fish.choose_payment_ai, verbose=False)
    check(ok is True, "a legal play must still succeed")
    check(p.hand == [], f"hand should be empty after paying 2 and playing 1, got {p.hand}")
    check(sorted(ms.pool) == [5, 7], f"payment reaches the pool, got {ms.pool}")
    check(p.ocean_slots[1].up == [3], f"the mammal is on the board, got {p.ocean_slots[1].up}")
    check(ts.played_face_uids == [3], "turn_state records the play")
    print("C PASS: the transaction commits, a legal play is untouched by the rollback")


def test_failed_draw_leaves_pool_and_deck_alone():
    gs, ms, ts, p = mini_game()
    gs.deck = []
    ms.pool = [5, 7]
    before = fingerprint(gs, ms, ts)
    ok = fish.apply_action(gs, ms, p, fish.Action(kind="draw", draw_from_pool=0),
                           ts, fish.choose_payment_ai, verbose=False)
    check(ok is False, "drawing from an empty deck is rejected")
    check(fingerprint(gs, ms, ts) == before, "the failed draw disturbed the table")

    # A pool draw naming a card that is not in the pool.
    gs, ms, ts, p = mini_game()
    ms.pool = [5]
    before = fingerprint(gs, ms, ts)
    fish.apply_action(gs, ms, p, fish.Action(kind="draw", draw_from_pool=1, pool_pick_uids=[999]),
                      ts, fish.choose_payment_ai, verbose=False)
    check(fingerprint(gs, ms, ts) == before, "a bad pool pick disturbed the table")
    print("D PASS: a rejected draw leaves the deck and the pool exactly as they were")


def test_rollback_covers_the_other_players():
    """Card abilities reach across the table, so a rollback that only healed the
    player who acted would leave opponents robbed by a move that never happened."""
    gs, ms, ts, p = mini_game()
    opp = gs.players[1]
    opp.hand = [11, 12]
    opp.score = 9
    before = fingerprint(gs, ms, ts)

    def wreck_everyone(*_a, **_k):
        opp.hand.clear()
        opp.score = 0
        raise Boom("blew up after hitting the opponent")

    original = fish.run_main_ability
    fish.run_main_ability = wreck_everyone
    try:
        ok = fish.apply_action(gs, ms, p, fish.Action(kind="play_to_ocean", card_uid=3,
                                                      ocean_uid=1, payment_uids=[5, 7]),
                               ts, fish.choose_payment_ai, verbose=False)
    finally:
        fish.run_main_ability = original
    check(ok is False, "the play failed")
    check(opp.hand == [11, 12], f"the opponent's hand comes back, got {opp.hand}")
    check(opp.score == 9, f"the opponent's score comes back, got {opp.score}")
    check(fingerprint(gs, ms, ts) == before, "the failed play left an opponent changed")
    print("E PASS: the rollback covers every player at the table, not just the actor")


def test_ocean_slots_identity_survives_rollback():
    """Lane lists are handed out by reference (abilities, bot caches, the board
    serializer all hold them), so a rollback has to rewind them in place."""
    gs, ms, ts, p = mini_game()
    lane = p.ocean_slots[1].up
    slots_obj = p.ocean_slots[1]
    with_exploding_ability(
        lambda: fish.apply_action(gs, ms, p, fish.Action(kind="play_to_ocean", card_uid=3,
                                                         ocean_uid=1, payment_uids=[5, 7]),
                                  ts, fish.choose_payment_ai, verbose=False)
    )
    check(p.ocean_slots[1] is slots_obj, "the OceanSlots object was replaced")
    check(p.ocean_slots[1].up is lane, "the lane list was replaced instead of rewound")
    check(lane == [], f"the lane must be empty again, got {lane}")

    # A whole ocean added by a failed play_ocean is removed again.
    gs, ms, ts, p = mini_game()
    p.hand = [1]
    before = fingerprint(gs, ms, ts)
    with_exploding_ability(
        lambda: fish.apply_action(gs, ms, p, fish.Action(kind="play_ocean", card_uid=1),
                                  ts, fish.choose_payment_ai, verbose=False)
    )
    check(fingerprint(gs, ms, ts) == before, "a failed play_ocean left the ocean on the board")
    print("F PASS: board lanes rewind in place, a failed ocean play leaves no ocean")


def test_random_actions_over_the_real_deck():
    """Hammer the real card set: whatever the engine rejects, and however it
    rejects it, the table must come out unchanged."""
    card_db = fish.load_card_db()
    uids = sorted(card_db.keys())
    rnd = random.Random(20260827)
    failures = 0
    attempts = 0

    for trial in range(400):
        players = []
        for i in range(rnd.randint(2, 4)):
            pl = fish.PlayerState(name=f"P{i}", hand=rnd.sample(uids, rnd.randint(3, 10)))
            for ocean_uid in rnd.sample(uids, rnd.randint(0, 4)):
                pl.board_oceans.append(ocean_uid)
                slots = fish.OceanSlots()
                slots.up = rnd.sample(uids, rnd.randint(0, 2))
                slots.right = rnd.sample(uids, rnd.randint(0, 2))
                pl.ocean_slots[ocean_uid] = slots
            pl.score = rnd.randint(0, 40)
            pl.flags["free_mammal"] = bool(rnd.getrandbits(1))
            pl.flags["_draws_taken"] = rnd.choice([0, 1])
            players.append(pl)
        gs = fish.GameState(card_db=card_db, players=players,
                            deck=rnd.sample(uids, rnd.randint(0, 30)),
                            turn_index=0)
        ms = fish.MatchState(pool=rnd.sample(uids, rnd.randint(0, 6)))
        ms.pair_primary_to_faces, ms.face_to_primary = fish.build_non_ocean_pair_maps(card_db)
        ts = fish.TurnState()
        p = gs.players[0]

        action = fish.Action(
            kind=rnd.choice(["draw", "play_ocean", "play_to_ocean", "move_between_oceans",
                             "discard_to_pool", "discard_batch_to_pool", "nonsense_kind"]),
            card_uid=rnd.choice(p.hand + uids[:5]),
            ocean_uid=rnd.choice(list(p.ocean_slots) or [None] + uids[:3]),
            draw_from_pool=rnd.choice([0, 1]),
            use_star=bool(rnd.getrandbits(1)),
        )
        if rnd.getrandbits(1) and len(p.hand) > 1:
            action.payment_uids = rnd.sample(p.hand, rnd.randint(1, min(3, len(p.hand))))
        if action.kind == "discard_batch_to_pool":
            action.pool_pick_uids = rnd.sample(p.hand, rnd.randint(0, len(p.hand)))

        before = fingerprint(gs, ms, ts)
        attempts += 1
        ok = fish.apply_action(gs, ms, p, action, ts, fish.choose_payment_ai, verbose=False)
        if not ok:
            failures += 1
            after = fingerprint(gs, ms, ts)
            if after != before:
                for i, (b, a) in enumerate(zip(before, after)):
                    if b != a:
                        raise AssertionError(
                            f"trial {trial}: rejected {action.kind} changed state slot {i}\n"
                            f"  before: {b}\n  after:  {a}"
                        )
                raise AssertionError(f"trial {trial}: rejected {action.kind} changed state")

    check(failures > 50, f"the fuzz needs real rejections to prove anything, got {failures}")
    print(f"G PASS: {failures} rejected actions out of {attempts} random ones, "
          f"every single one left the table untouched")


def test_deep_state_matches_a_manual_deepcopy():
    """Belt and braces: compare the rollback against a full deepcopy of the state,
    so a field the hand-rolled capture forgot would show up here."""
    card_db = fish.load_card_db()
    uids = sorted(card_db.keys())
    rnd = random.Random(4242)
    p = fish.PlayerState(name="Tim", hand=rnd.sample(uids, 8))
    for ocean_uid in rnd.sample(uids, 3):
        p.board_oceans.append(ocean_uid)
        p.ocean_slots[ocean_uid] = fish.OceanSlots(up=rnd.sample(uids, 2))
    gs = fish.GameState(card_db=card_db, players=[p], deck=rnd.sample(uids, 20))
    ms = fish.MatchState(pool=rnd.sample(uids, 4))
    ts = fish.TurnState(free_followups=1, draws_this_turn=1)
    ts.discarded_entry_uids.add(uids[0])

    gs_copy = copy.deepcopy(gs.__dict__)
    del gs_copy["card_db"]
    ms_copy = copy.deepcopy(ms)

    with_exploding_ability(
        lambda: fish.apply_action(gs, ms, p, fish.Action(kind="play_to_ocean",
                                                         card_uid=p.hand[0],
                                                         ocean_uid=p.board_oceans[0]),
                                  ts, fish.choose_payment_ai, verbose=False)
    )
    live = dict(gs.__dict__)
    del live["card_db"]
    check(repr(live) == repr(gs_copy), "GameState differs from a full deepcopy after rollback")
    check(repr(ms.__dict__) == repr(ms_copy.__dict__), "MatchState differs after rollback")
    check(ts.free_followups == 1 and ts.draws_this_turn == 1,
          "turn_state must rewind too")
    print("H PASS: the rollback matches a full deepcopy of the state, field for field")


if __name__ == "__main__":
    test_failed_play_returns_the_payment()
    test_failed_play_keeps_the_free_play()
    test_successful_play_still_commits()
    test_failed_draw_leaves_pool_and_deck_alone()
    test_rollback_covers_the_other_players()
    test_ocean_slots_identity_survives_rollback()
    test_random_actions_over_the_real_deck()
    test_deep_state_matches_a_manual_deepcopy()
    print(f"\n{CHECKS[0]} checks")
    print("ALL ACTION ATOMICITY TESTS PASSED ✓")
