"""Playing an Ocean flips the top card of the deck into the Pool.

The rule was removed on 2026-09-14 and brought back on 2026-10-19, so this
file is the inverse of the test_ocean_play_no_flip.py it replaces. Casual, Head
to Head, Competitive, Team, tournaments and the in-game tutorials all play
through fish.run_match -> apply_action, so the engine is the one place the rule
lives, plus the standalone copies in "the fish game simulation.py" and "The
fish game simulaiton.py" and the one rules text (js/rulebook.js, which serves
both the printed book and the in-game Full Rulebook) with rules.html's summary
of it.

These tests pin all of it:

  * a paid Ocean takes the top card off the deck and that card lands in the
    Pool, alongside the card that paid for it (both engines);
  * END GAME flipped out of the deck starts the final round and goes to the
    discard pile, not the Pool (both engines);
  * an Ocean's OWN "Draw one" draws the card UNDER the one it flipped;
  * an empty deck is not a crash: the Ocean simply goes down;
  * across whole bot games, every card that reaches the Pool without having
    been in a hand arrives during a play_ocean and nothing else;
  * the flip ledger the live payload reads names the flipped card for exactly
    as long as it is in the Pool, so a card flipped, drawn and then paid back
    in does not flip out twice;
  * both rules texts describe the flip, and the client is still wired to the
    payload key that drives the 200% flip-out reveal.

Run:  python3 test_ocean_play_flip.py
"""

import importlib.util
import pathlib
import random
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fish = _load("fish", "fish_game_all_in_one.py")
legacy = _load("legacy_fish_simulation", "the fish game simulation.py")

ENGINES = [("fish_game_all_in_one.py", fish), ("the fish game simulation.py", legacy)]

CORAL_REEF = 217   # Ocean, cost 1, no draw
PIER = 201         # Ocean, cost 1: any card pays a base cost
DEEP_OCEAN = 209   # Ocean, cost 0, "+1 | Draw one"

FAILURES = []
CHECKS = 0


def check(cond, label):
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(label)
    return bool(cond)


def table(engine, deck, hand, card_db=None, end_game_uid=None):
    db = card_db if card_db is not None else engine.load_card_db()
    primary, face = engine.build_non_ocean_pair_maps(db)
    me = engine.PlayerState(name="Tim", hand=list(hand))
    opp = engine.PlayerState(name="Opp")
    gs = engine.GameState(card_db=db, players=[me, opp], deck=list(deck))
    ms = engine.MatchState(end_game_uid=end_game_uid,
                           pair_primary_to_faces=primary, face_to_primary=face)
    return gs, ms, me


def play_ocean(engine, gs, ms, me, uid):
    action = engine.Action(kind="play_ocean", card_uid=uid, face_uid=uid)
    return engine.apply_action(gs, ms, me, action, engine.TurnState(), engine.choose_payment_ai)


def test_a_paid_ocean_flips_the_top_card():
    for label, engine in ENGINES:
        deck = [202, 203, 204]
        gs, ms, me = table(engine, deck, [CORAL_REEF, PIER])
        ok = play_ocean(engine, gs, ms, me, CORAL_REEF)
        check(ok, f"[{label}] the Coral Reef play was rejected")
        check(me.board_oceans == [CORAL_REEF], f"[{label}] the Coral Reef is not on the board: {me.board_oceans}")
        check(gs.deck == [203, 204], f"[{label}] the Ocean should have taken 202 off the deck, got {gs.deck}")
        check(ms.pool == [PIER, 202],
              f"[{label}] the Pool should hold the payment then the flipped card, got {ms.pool}")
        check(me.hand == [], f"[{label}] hand should be empty after paying, got {me.hand}")
    print("A PASS: a paid Ocean flips the top card of the deck into the Pool")


def test_end_game_flipped_out_starts_the_final_round():
    for label, engine in ENGINES:
        db = dict(engine.load_card_db())
        end_uid = max(db) + 1000
        db[end_uid] = engine.CardDef(uid=end_uid, name="END GAME", species="Ocean", cost=0,
                                     direction="N/A", symbol="N/A", text="")
        deck = [end_uid, 202, 203]
        gs, ms, me = table(engine, deck, [CORAL_REEF, PIER], card_db=db, end_game_uid=end_uid)
        ok = play_ocean(engine, gs, ms, me, CORAL_REEF)
        check(ok, f"[{label}] the Coral Reef play was rejected")
        check(ms.end_game_triggered, f"[{label}] flipping END GAME out did not start the final round")
        check(ms.final_turns_remaining == len(gs.players),
              f"[{label}] every player should still get one final turn, got {ms.final_turns_remaining}")
        check(gs.deck == [202, 203], f"[{label}] END GAME should have left the deck, got {gs.deck}")
        check(end_uid in ms.discard_pile and end_uid not in ms.pool,
              f"[{label}] END GAME belongs in the discard pile, not the Pool: "
              f"pool={ms.pool} discard={ms.discard_pile}")
    print("B PASS: END GAME flipped out of the deck starts the final round, and is not pooled")


def test_an_oceans_own_draw_takes_the_card_under_the_flip():
    deck = [202, 203, 204]
    gs, ms, me = table(fish, deck, [DEEP_OCEAN])
    check(gs.card_db[DEEP_OCEAN].name == "Deep Ocean", "uid 209 is no longer a Deep Ocean")
    ok = play_ocean(fish, gs, ms, me, DEEP_OCEAN)
    check(ok, "the Deep Ocean play was rejected")
    check(ms.pool == [202], f"the flip should pool the top card first, pool={ms.pool}")
    check(me.hand == [203], f"Draw one should take the card UNDER the flip, hand={me.hand}")
    check(gs.deck == [204], f"the flip and the draw should take one card each, deck={gs.deck}")
    print("C PASS: an Ocean's own Draw one takes the card under the one it flipped")


def test_an_empty_deck_still_plays_the_ocean():
    for label, engine in ENGINES:
        gs, ms, me = table(engine, [], [CORAL_REEF, PIER])
        ok = play_ocean(engine, gs, ms, me, CORAL_REEF)
        check(ok, f"[{label}] an Ocean played on an empty deck was rejected")
        check(me.board_oceans == [CORAL_REEF], f"[{label}] the Ocean is not on the board: {me.board_oceans}")
        check(ms.pool == [PIER], f"[{label}] nothing but the payment can reach the Pool, got {ms.pool}")
    print("D PASS: an empty deck has nothing to flip, and the Ocean goes down anyway")


def test_only_oceans_pool_a_card_from_the_deck():
    """Bot games of several sizes through the real run_match. Bot lookahead
    calls apply_action on cloned states, so the spy is only armed for the moves
    the live table actually makes."""
    real_apply, real_add = fish.apply_action, fish.add_to_pool
    seen = {"hands": None}
    tally = {"ocean_plays": 0, "pooled": 0, "from_the_deck": []}

    def add_spy(ms, uid):
        if seen["hands"] is not None:
            tally["pooled"] += 1
            if uid not in seen["hands"]:
                tally["from_the_deck"].append((seen["kind"], uid))
        return real_add(ms, uid)

    def apply_spy(gs, ms, player, action, *args, **kwargs):
        seen["hands"] = {u for p in gs.players for u in p.hand}
        seen["kind"] = action.kind
        try:
            ok = real_apply(gs, ms, player, action, *args, **kwargs)
        finally:
            seen["hands"] = None
        if ok and action.kind == "play_ocean":
            tally["ocean_plays"] += 1
        return ok

    weights = fish.default_weights()

    def policy(gs, ms, p):
        fish.apply_action = real_apply
        try:
            return fish.choose_action_weighted(gs, ms, p, weights)
        finally:
            fish.apply_action = apply_spy

    card_db = fish.load_card_db()
    fish.add_to_pool = add_spy
    try:
        for seed, players in ((11, 2), (12, 3), (13, 4)):
            random.seed(seed)
            fish.apply_action = apply_spy
            fish.run_match(card_db=card_db, player_names=[f"B{i}" for i in range(players)],
                           action_policies=[policy] * players, seed=seed, max_turns=400,
                           human_index=None, verbose=False, verbose_state=False)
            fish.apply_action = real_apply
    finally:
        fish.apply_action, fish.add_to_pool = real_apply, real_add

    strays = [kind for kind, _uid in tally["from_the_deck"] if kind != "play_ocean"]
    check(tally["ocean_plays"] >= 6, f"the games played too few Oceans to prove anything: {tally['ocean_plays']}")
    check(len(tally["from_the_deck"]) >= 6,
          f"only {len(tally['from_the_deck'])} Pool arrival(s) came off the deck across "
          f"{tally['ocean_plays']} Ocean plays")
    check(len(tally["from_the_deck"]) <= tally["ocean_plays"],
          f"{len(tally['from_the_deck'])} cards came off the deck for {tally['ocean_plays']} Ocean plays, "
          f"so something flips more than one")
    check(not strays,
          f"{len(strays)} card(s) reached the Pool from the deck during actions that are not an Ocean play: "
          f"{strays[:8]}")
    print(f"E PASS: {len(tally['from_the_deck'])} deck cards pooled over {tally['ocean_plays']} Ocean plays "
          f"and {tally['pooled']} Pool arrivals, every one of them an Ocean's")


def test_the_flip_ledger_tracks_only_what_is_in_the_pool():
    """The live payload names the flipped card so the client can flip it out at
    200%. The names must not outlive the card's stay in the Pool."""
    gs, ms, me = table(fish, [202, 203, 204, 205], [CORAL_REEF, PIER])
    play_ocean(fish, gs, ms, me, CORAL_REEF)   # pays with the Pier, flips 202
    check(ms.ocean_flip_uids == [202], f"the flipped card should be named, got {ms.ocean_flip_uids}")
    # Somebody draws it out of the Pool, then a second Ocean flips another card.
    # The Deep Ocean is dealt in here rather than held from the start, because a
    # free Ocean in hand is what the payment AI reaches for to pay for the first.
    ms.pool.remove(202)
    me.hand.extend([202, DEEP_OCEAN])
    ok = play_ocean(fish, gs, ms, me, DEEP_OCEAN)
    check(ok, "the second Ocean play was rejected, so the ledger was never asked anything")
    check(ms.ocean_flip_uids == [203],
          f"a flipped card that left the Pool should be forgotten, got {ms.ocean_flip_uids}")
    # A clone plays on without writing back into the real game's ledger.
    clone = fish.clone_match_state(ms)
    clone.ocean_flip_uids.append(999)
    check(ms.ocean_flip_uids == [203], f"a cloned rollout wrote into the live ledger: {ms.ocean_flip_uids}")
    print("F PASS: the flip ledger names the flipped card for exactly its stay in the Pool")


def test_the_rules_describe_the_flip_again():
    book = (ROOT / "multiplayer/client/js/rulebook.js").read_text(encoding="utf-8")
    check("When you play an Ocean:" in book and "Flip 1 card from the deck into the Pool" in book,
          "the Full Rulebook has lost its 'When you play an Ocean' callout")
    check("If END GAME enters the Pool:" in book,
          "the Full Rulebook has lost the END GAME edge case only the Ocean flip can cause")
    rules = (ROOT / "multiplayer/client/rules.html").read_text(encoding="utf-8")
    check(re.search(r"playing one flips a card off the deck into the Pool", rules) is not None,
          "the Rules page no longer says that playing an Ocean flips a card")
    for label, engine in ENGINES:
        src = pathlib.Path(engine.__file__).read_text(encoding="utf-8")
        check("flipped = gs.deck.pop(0)" in src, f"{label} no longer carries the Ocean flip")
    # The reveal is only as good as its wiring: the server names the flipped
    # Pool entries and the client reads that name.
    server = (ROOT / "multiplayer_server.py").read_text(encoding="utf-8")
    check('"ocean_flip_uids": ocean_flip_uids' in server,
          "the live payload no longer tells the client which Pool card came off the deck")
    app = (ROOT / "multiplayer/client/js/preview-app.js").read_text(encoding="utf-8")
    check("state.ocean_flip_uids" in app and "flipOutFromDeck" in app,
          "the client no longer flips the Ocean's card out of the deck")
    print("G PASS: both rules texts describe the flip, and the reveal is wired end to end")


if __name__ == "__main__":
    test_a_paid_ocean_flips_the_top_card()
    test_end_game_flipped_out_starts_the_final_round()
    test_an_oceans_own_draw_takes_the_card_under_the_flip()
    test_an_empty_deck_still_plays_the_ocean()
    test_only_oceans_pool_a_card_from_the_deck()
    test_the_flip_ledger_tracks_only_what_is_in_the_pool()
    test_the_rules_describe_the_flip_again()
    if FAILURES:
        print(f"\n{len(FAILURES)} of {CHECKS} checks FAILED:")
        for f in FAILURES:
            print("  FAIL: " + f)
        sys.exit(1)
    print(f"\nAll {CHECKS} checks passed.")
