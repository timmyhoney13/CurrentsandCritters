"""Playing an Ocean takes nothing off the deck.

It used to: every Ocean that went down flipped the top card of the deck face-up
into the Pool, and if that card was END GAME the final round started on the
spot. The rule was removed from every game mode (2026-09-14). Casual, Head to
Head, Competitive, Team, tournaments and the in-game tutorials all play through
fish.run_match -> apply_action, so the engine is the one place it lived, plus
the old standalone copy in "the fish game simulation.py" and the two published
rules texts (js/rulebook.js and rules.html).

These tests pin all of it:

  * a paid Ocean leaves the deck exactly as it was, and the Pool gains only the
    card that paid for it (both engines);
  * END GAME on top of the deck stays there, unrevealed, when an Ocean goes
    down (both engines);
  * an Ocean's OWN "Draw one" still draws, into the hand and not the Pool;
  * across whole bot games, no card ever reaches the Pool during an action
    unless it was in somebody's hand when that action started;
  * neither rules text describes the flip any more.

Run:  python3 test_ocean_play_no_flip.py
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


def test_a_paid_ocean_leaves_the_deck_alone():
    for label, engine in ENGINES:
        deck = [202, 203, 204]
        gs, ms, me = table(engine, deck, [CORAL_REEF, PIER])
        ok = play_ocean(engine, gs, ms, me, CORAL_REEF)
        check(ok, f"[{label}] the Coral Reef play was rejected")
        check(me.board_oceans == [CORAL_REEF], f"[{label}] the Coral Reef is not on the board: {me.board_oceans}")
        check(gs.deck == deck, f"[{label}] playing an Ocean changed the deck: {deck} -> {gs.deck}")
        check(ms.pool == [PIER], f"[{label}] the Pool should hold only the payment, got {ms.pool}")
        check(me.hand == [], f"[{label}] hand should be empty after paying, got {me.hand}")
    print("A PASS: a paid Ocean leaves the deck alone, and only the payment reaches the Pool")


def test_an_ocean_never_reveals_end_game():
    for label, engine in ENGINES:
        db = dict(engine.load_card_db())
        end_uid = max(db) + 1000
        db[end_uid] = engine.CardDef(uid=end_uid, name="END GAME", species="Ocean", cost=0,
                                     direction="N/A", symbol="N/A", text="")
        deck = [end_uid, 202, 203]
        gs, ms, me = table(engine, deck, [CORAL_REEF, PIER], card_db=db, end_game_uid=end_uid)
        ok = play_ocean(engine, gs, ms, me, CORAL_REEF)
        check(ok, f"[{label}] the Coral Reef play was rejected")
        check(not ms.end_game_triggered, f"[{label}] playing an Ocean revealed END GAME")
        check(gs.deck == deck, f"[{label}] END GAME should still be on top of the deck, got {gs.deck}")
        check(end_uid not in ms.pool and end_uid not in ms.discard_pile,
              f"[{label}] END GAME left the deck: pool={ms.pool} discard={ms.discard_pile}")
    print("B PASS: END GAME on top of the deck stays there when an Ocean goes down")


def test_an_oceans_own_draw_still_draws():
    deck = [202, 203, 204]
    gs, ms, me = table(fish, deck, [DEEP_OCEAN])
    check(gs.card_db[DEEP_OCEAN].name == "Deep Ocean", "uid 209 is no longer a Deep Ocean")
    ok = play_ocean(fish, gs, ms, me, DEEP_OCEAN)
    check(ok, "the Deep Ocean play was rejected")
    check(me.hand == [202], f"Deep Ocean's Draw one should put the top card in hand, hand={me.hand}")
    check(gs.deck == [203, 204], f"Deep Ocean should take exactly its one draw, deck={gs.deck}")
    check(ms.pool == [], f"a free Ocean with a draw put cards in the Pool: {ms.pool}")
    print("C PASS: an Ocean's own Draw one still draws, into the hand")


def test_whole_games_never_pool_a_deck_card():
    """Bot games of several sizes through the real run_match. Bot lookahead
    calls apply_action on cloned states, so the spy is only armed for the moves
    the live table actually makes."""
    real_apply, real_add = fish.apply_action, fish.add_to_pool
    seen = {"hands": None}
    tally = {"ocean_plays": 0, "pooled": 0, "pooled_from_nowhere": []}

    def add_spy(ms, uid):
        if seen["hands"] is not None:
            tally["pooled"] += 1
            if uid not in seen["hands"]:
                tally["pooled_from_nowhere"].append((seen["kind"], uid))
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

    check(tally["ocean_plays"] >= 6, f"the games played too few Oceans to prove anything: {tally['ocean_plays']}")
    check(tally["pooled"] > 0, "no card reached the Pool at all, so the spy saw nothing")
    check(not tally["pooled_from_nowhere"],
          f"{len(tally['pooled_from_nowhere'])} card(s) reached the Pool without coming from a hand: "
          f"{tally['pooled_from_nowhere'][:8]}")
    print(f"D PASS: {tally['ocean_plays']} Oceans and {tally['pooled']} Pool arrivals over 3 games, "
          f"every one of them from a hand")


def test_the_rules_no_longer_describe_the_flip():
    # "Flip 1 card from the deck into the Pool", "flips a card off the deck",
    # the rulebook's "When you play an Ocean:" callout. Not every "flip": a
    # turn that flipped, or a draw animation that flips mid-flight, is fine.
    flip = re.compile(r"\bflip[a-z]*\s+(?:1|one|a|the(?:\s+top)?)\s+card\b[^.<]{0,60}\b(?:deck|pool)\b"
                      r"|when you play an ocean|ocean[ -]flip", re.I)
    for rel in ("multiplayer/client/js/rulebook.js", "multiplayer/client/rules.html",
                "multiplayer/client/js/preview-app.js", "multiplayer/client/js/tutorials.js",
                "index.html"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        # The changelog is the one place allowed to say what USED to happen.
        if rel.endswith("preview-app.js"):
            start = text.index("const APP_CHANGELOG = [")
            text = text[:start] + text[text.index("\n  ];", start):]
        hits = [m.group(0) for m in flip.finditer(text)]
        check(not hits, f"{rel} still describes the Ocean flip: {hits}")
    book = (ROOT / "multiplayer/client/js/rulebook.js").read_text(encoding="utf-8")
    check("enters the Pool" not in book,
          "rulebook.js keeps an END GAME edge case that only the Ocean flip could cause")
    for label, engine in ENGINES:
        src = pathlib.Path(engine.__file__).read_text(encoding="utf-8")
        check("Ocean flip" not in src and "flipped = gs.deck.pop" not in src,
              f"{label} still carries the Ocean flip code")
    print("E PASS: no rules text or engine describes the Ocean flip")


if __name__ == "__main__":
    test_a_paid_ocean_leaves_the_deck_alone()
    test_an_ocean_never_reveals_end_game()
    test_an_oceans_own_draw_still_draws()
    test_whole_games_never_pool_a_deck_card()
    test_the_rules_no_longer_describe_the_flip()
    if FAILURES:
        print(f"\n{len(FAILURES)} of {CHECKS} checks FAILED:")
        for f in FAILURES:
            print("  FAIL: " + f)
        sys.exit(1)
    print(f"\nAll {CHECKS} checks passed.")
