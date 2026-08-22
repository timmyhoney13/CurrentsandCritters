"""Discarding down to the hand limit: exactly the cards you picked, and no refill.

Run:  python3 test_hand_limit_discard.py

Two things went wrong at the end of a turn when a player was over the ten-card
limit, and both are pinned here.

1. THE REFILL LOOP.  apply_action fired a "board symbol star draw" after every
   hand-limit batch discard: any board card whose symbol matched a discarded
   card, and whose ★ text mentioned a draw, drew a card.  So trimming 13 → 10
   handed 3 cards straight back, the turn loop saw the player still over the
   limit and re-opened the discard phase, and round it went, "it takes forever
   to discard a card when you are over ten cards ... and it drew me three cards
   for some reason".  A ★ is opt-in and one-shot (play the card, pay with a
   matching symbol, use_star=True); it is not a standing trigger on any discard.
   The hand-limit trim must therefore draw NOTHING.

2. THE WRONG CARDS.  The batch must remove precisely the uids the player picked,
   never a same-named or same-symbol neighbour, and must leave the rest of the
   hand untouched and in order.
"""

import importlib.util
import sys
from typing import Dict, List, Tuple

spec = importlib.util.spec_from_file_location("fish", "fish_game_all_in_one.py")
fish = importlib.util.module_from_spec(spec)
sys.modules["fish"] = fish
spec.loader.exec_module(fish)

CARD_DB: Dict[int, "fish.CardDef"] = fish.load_card_db()

FAILURES: List[str] = []
CHECKS = 0


def check(cond: bool, label: str) -> bool:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(label)
    return bool(cond)


# ── Fixtures ───────────────────────────────────────────────────────────────
def new_match() -> Tuple["fish.GameState", "fish.MatchState", "fish.PlayerState"]:
    pair_map, face_map = fish.build_non_ocean_pair_maps(CARD_DB)
    ms = fish.MatchState(pair_primary_to_faces=pair_map, face_to_primary=face_map)
    me = fish.PlayerState(name="Tester")
    opp = fish.PlayerState(name="Rival")
    gs = fish.GameState(card_db=CARD_DB, players=[me, opp], deck=[])
    # A deep deck: if anything DOES draw, it succeeds and the test sees the
    # extra cards, instead of silently no-opping on an empty deck.
    gs.deck = [uid for uid in sorted(CARD_DB) if fish.canonical_entry_uid(ms, uid) == uid][:80]
    return gs, ms, me


def entry_uids(ms, want: int, *, oceans: bool = False, gs=None) -> List[int]:
    """`want` distinct hand entries (non-ocean unless asked)."""
    out: List[int] = []
    for uid in sorted(CARD_DB):
        if fish.canonical_entry_uid(ms, uid) != uid:
            continue
        if not oceans and gs is not None and fish.entry_is_ocean(ms, gs, uid):
            continue
        out.append(uid)
        if len(out) == want:
            break
    assert len(out) == want, f"only found {len(out)} entries, wanted {want}"
    return out


def star_draw_board_card(gs, ms, player) -> Tuple[int, str]:
    """Put a card with a 'draw' ★ on the board. Returns (face_uid, symbol)."""
    ocean_uid = None
    for uid in sorted(CARD_DB):
        if fish.is_ocean(CARD_DB[uid]):
            ocean_uid = uid
            break
    assert ocean_uid is not None, "no ocean in the card db"
    player.board_oceans.append(ocean_uid)
    player.ocean_slots[ocean_uid] = fish.OceanSlots()

    for uid in sorted(CARD_DB):
        c = CARD_DB[uid]
        if fish.is_ocean(c):
            continue
        _, star = fish.split_main_and_star(c.text)
        if not star or "draw" not in star.lower():
            continue
        sym = fish.normalize_symbol(c.symbol)
        if sym in {"", "n/a"}:
            continue
        player.ocean_slots[ocean_uid].up.append(uid)
        return uid, sym
    raise AssertionError("no board card with a 'draw' ★ found")


def hand_of(ms, gs, player, size: int, *, symbol: str = "", matching: int = 0) -> List[int]:
    """Fill the hand with `size` entries, `matching` of which carry `symbol`."""
    want_match, filler = [], []
    sym = fish.normalize_symbol(symbol) if symbol else ""
    for uid in sorted(CARD_DB):
        if fish.canonical_entry_uid(ms, uid) != uid or fish.entry_is_ocean(ms, gs, uid):
            continue
        is_match = bool(sym) and any(
            fish.normalize_symbol(CARD_DB[f].symbol) == sym for f in fish.entry_faces(ms, uid)
        )
        if is_match and len(want_match) < matching:
            want_match.append(uid)
        elif not is_match and len(filler) < size - matching:
            filler.append(uid)
        if len(want_match) == matching and len(filler) == size - matching:
            break
    assert len(want_match) == matching, f"needed {matching} {symbol} cards, found {len(want_match)}"
    assert len(filler) == size - matching, "not enough non-matching filler"
    # Matching cards first so the picks are easy to name, then filler.
    player.hand = want_match + filler
    for uid in player.hand:
        if uid in gs.deck:
            gs.deck.remove(uid)
    return list(player.hand)


def batch_discard(gs, ms, player, picks: List[int]) -> Tuple[bool, str]:
    action = fish.Action(kind="discard_batch_to_pool", pool_pick_uids=list(picks))
    reasons: List[str] = []
    ok = fish.apply_action(
        gs, ms, player, action, fish.TurnState(), fish.choose_payment_ai, fail_reason=reasons
    )
    return ok, "; ".join(reasons)


# ── Tests ──────────────────────────────────────────────────────────────────
def test_trim_to_limit_draws_nothing():
    """13 → discard 3 → exactly 10 in hand. No board ★ hands cards back."""
    gs, ms, me = new_match()
    _, sym = star_draw_board_card(gs, ms, me)
    hand_of(ms, gs, me, 13, symbol=sym, matching=3)
    picks = me.hand[:3]  # the three symbol-matching cards: the worst case
    deck_before = len(gs.deck)

    ok, why = batch_discard(gs, ms, me, picks)
    check(ok, f"13→10 batch discard rejected: {why}")
    check(len(me.hand) == 10, f"hand is {len(me.hand)} after trimming 3 from 13, expected 10")
    check(
        len(gs.deck) == deck_before,
        f"deck lost {deck_before - len(gs.deck)} card(s), the trim drew cards back",
    )


def test_trim_to_limit_draws_nothing_for_every_matching_symbol():
    """Same, once per symbol that has a 'draw' ★ on a board card, no exceptions."""
    seen = set()
    for uid in sorted(CARD_DB):
        c = CARD_DB[uid]
        if fish.is_ocean(c):
            continue
        _, star = fish.split_main_and_star(c.text)
        sym = fish.normalize_symbol(c.symbol)
        if not star or "draw" not in star.lower() or sym in {"", "n/a"} or sym in seen:
            continue
        seen.add(sym)

        gs, ms, me = new_match()
        ocean = next(u for u in sorted(CARD_DB) if fish.is_ocean(CARD_DB[u]))
        me.board_oceans.append(ocean)
        me.ocean_slots[ocean] = fish.OceanSlots()
        me.ocean_slots[ocean].up.append(uid)
        hand_of(ms, gs, me, 12, symbol=sym, matching=2)
        deck_before = len(gs.deck)

        ok, why = batch_discard(gs, ms, me, me.hand[:2])
        check(ok, f"[{c.name} {sym}] batch discard rejected: {why}")
        check(len(me.hand) == 10, f"[{c.name} {sym}] hand is {len(me.hand)}, expected 10")
        check(len(gs.deck) == deck_before, f"[{c.name} {sym}] the trim drew {deck_before - len(gs.deck)} card(s)")
    check(len(seen) > 0, "no board card with a 'draw' ★ exists: fixture is not testing anything")


def test_trim_removes_exactly_the_picked_cards():
    """The cards that leave are the ones picked; the rest of the hand is untouched."""
    gs, ms, me = new_match()
    hand_of(ms, gs, me, 13)
    before = list(me.hand)
    picks = [before[0], before[6], before[12]]  # first, middle, last of the fan
    expected_left = [u for u in before if u not in picks]

    ok, why = batch_discard(gs, ms, me, picks)
    check(ok, f"batch discard rejected: {why}")
    check(me.hand == expected_left, f"hand is {me.hand}, expected {expected_left}")
    for uid in picks:
        check(uid not in me.hand, f"picked card {uid} is still in hand")
        check(uid in ms.pool or uid in ms.discard_pile, f"picked card {uid} went nowhere")


def test_repeated_uid_cannot_take_a_second_card():
    """A doubled uid in the payload discards one card, not two, and is rejected
    for being under-sized rather than quietly eating a neighbour."""
    gs, ms, me = new_match()
    hand_of(ms, gs, me, 12)
    before = list(me.hand)
    ok, _ = batch_discard(gs, ms, me, [before[0], before[0]])
    check(not ok, "a doubled uid was accepted as a 2-card trim of a 12-card hand")
    check(me.hand == before, "a rejected batch still changed the hand")


def test_under_sized_batch_is_rejected_and_changes_nothing():
    gs, ms, me = new_match()
    hand_of(ms, gs, me, 13)
    before = list(me.hand)
    ok, _ = batch_discard(gs, ms, me, before[:1])
    check(not ok, "a 1-card batch was accepted while 3 over the limit")
    check(me.hand == before, "a rejected batch still changed the hand")


def test_discard_phase_refuses_to_go_below_the_limit():
    gs, ms, me = new_match()
    hand_of(ms, gs, me, 12)
    me.flags["_discard_mode"] = True
    before = list(me.hand)
    ok, _ = batch_discard(gs, ms, me, before[:4])
    check(not ok, "discarding below the hand limit was allowed during the trim")
    check(me.hand == before, "a rejected batch still changed the hand")
    me.flags.pop("_discard_mode", None)


def test_tarpon_cycle_still_draws_back():
    """The Tarpon 'discard N, draw N' loop is a different mechanism and must survive:
    the batch itself draws nothing, the loop does the drawing."""
    gs, ms, me = new_match()
    hand_of(ms, gs, me, 6)
    me.flags["_tarpon_discard_active"] = True
    before = list(me.hand)
    deck_before = len(gs.deck)

    ok, why = batch_discard(gs, ms, me, before[:4])
    check(ok, f"Tarpon batch rejected: {why}")
    # Tarpon may go below the limit, and apply_action itself never draws.
    check(len(me.hand) == 2, f"Tarpon batch left {len(me.hand)} cards, expected 2")
    check(len(gs.deck) == deck_before, "apply_action drew during a Tarpon batch")
    me.flags.pop("_tarpon_discard_active", None)


def test_no_standing_board_star_trigger_remains():
    """The helper is gone for good, a grep-level guard against re-wiring it."""
    src = open("fish_game_all_in_one.py", encoding="utf-8").read()
    live = [
        ln for ln in src.splitlines()
        if "trigger_board_symbol_star_draws" in ln and not ln.lstrip().startswith("#")
    ]
    check(not live, f"trigger_board_symbol_star_draws is back in live code: {live}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"{CHECKS} checks, {len(FAILURES)} failure(s)")
    for f in FAILURES:
        print("  ✗ " + f)
    sys.exit(1 if FAILURES else 0)
