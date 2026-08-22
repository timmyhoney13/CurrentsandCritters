"""Star ability coverage: every ★ on every card, fired for real through the engine.

Why this file exists
--------------------
A ★ ability is opt-in: the player pays the card's cost and includes at least one
card whose symbol matches the card being played, and the engine fires the star
only for an action submitted with use_star=True (see apply_action, a symbol
match on a plain play deliberately does NOT auto-fire it).

That leaves several ways for a star to silently do nothing:
  * its text is not matched by any branch of _execute_star_pattern,
  * it sets a free-play flag no one ever consumes,
  * it is never offered because can_potentially_use_star rejects it,
  * a two-sided card fires the star of the wrong face.

These tests play each distinct ★ card through apply_action and assert the effect
actually landed, so "all the star abilities work" is checked, not assumed.

Run:  python3 test_star_abilities.py
"""

import importlib.util
import sys
from typing import Dict, List, Optional, Tuple

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


# ── Fixture helpers ────────────────────────────────────────────────────────
def new_match() -> Tuple["fish.GameState", "fish.MatchState", "fish.PlayerState"]:
    pair_map, face_map = fish.build_non_ocean_pair_maps(CARD_DB)
    ms = fish.MatchState(pair_primary_to_faces=pair_map, face_to_primary=face_map)
    me = fish.PlayerState(name="Tester")
    opp = fish.PlayerState(name="Rival")
    gs = fish.GameState(card_db=CARD_DB, players=[me, opp], deck=[])
    # A deep deck so "draw N" stars always have cards to take.
    gs.deck = [uid for uid in sorted(CARD_DB) if fish.canonical_entry_uid(ms, uid) == uid][:60]
    return gs, ms, me


def give_ocean(gs, ms, player, ocean_name: str = "coral reef") -> int:
    """Put a free-standing ocean on the board so animals have somewhere to go."""
    for uid in sorted(CARD_DB):
        c = CARD_DB[uid]
        if fish.is_ocean(c) and c.name.strip().lower() == ocean_name:
            player.board_oceans.append(uid)
            player.ocean_slots[uid] = fish.OceanSlots()
            return uid
    raise AssertionError(f"no ocean named {ocean_name}")


def matching_payment(ms, gs, exclude_entry: int, symbol: str, count: int) -> List[int]:
    """`count` hand entries, the first of which matches `symbol`."""
    sym = fish.normalize_symbol(symbol)
    picks: List[int] = []
    for uid in sorted(CARD_DB):
        if fish.canonical_entry_uid(ms, uid) != uid:
            continue
        if uid == exclude_entry or uid in picks:
            continue
        if fish.entry_is_ocean(ms, gs, uid):
            continue
        if not picks and not fish.symbol_match_for_entry(ms, gs, uid, sym):
            continue
        if picks and fish.symbol_match_for_entry(ms, gs, uid, sym):
            # keep the non-matching filler genuinely non-matching so the test
            # proves the FIRST card is what activates the star
            continue
        picks.append(uid)
        if len(picks) == count:
            break
    assert len(picks) == count, f"could not build a {count}-card payment for {symbol}"
    return picks


def play_with_star(gs, ms, player, face_uid: int, ocean_uid: Optional[int]) -> Tuple[bool, str, "fish.TurnState"]:
    """Play `face_uid` from hand with use_star=True and a symbol-matched payment."""
    card = CARD_DB[face_uid]
    entry_uid = fish.canonical_entry_uid(ms, face_uid)
    if entry_uid not in player.hand:
        player.hand.append(entry_uid)
    cost = max(0, int(card.cost))
    payments = matching_payment(ms, gs, entry_uid, card.symbol, cost) if cost else []
    for uid in payments:
        if uid not in player.hand:
            player.hand.append(uid)
    action = fish.Action(
        kind="play_ocean" if fish.is_ocean(card) else "play_to_ocean",
        card_uid=entry_uid,
        face_uid=face_uid,
        ocean_uid=None if fish.is_ocean(card) else ocean_uid,
        use_star=True,
        payment_uids=list(payments),
    )
    ts = fish.TurnState()
    reasons: List[str] = []
    ok = fish.apply_action(gs, ms, player, action, ts, fish.choose_payment_ai, fail_reason=reasons)
    return ok, "; ".join(reasons), ts


def star_cards_by_text() -> Dict[str, int]:
    """One representative face uid per distinct ★ text."""
    out: Dict[str, int] = {}
    for uid in sorted(CARD_DB):
        c = CARD_DB[uid]
        if not fish.has_star_ability(c):
            continue
        _, star = fish.split_main_and_star(c.text)
        key = star.strip().lower() or "<inferred play again>"
        out.setdefault(key, uid)
    return out


# ── Tests ──────────────────────────────────────────────────────────────────
def test_every_star_text_is_reachable_and_fires():
    """Each distinct ★ text plays successfully and registers a star activation."""
    for text, face_uid in sorted(star_cards_by_text().items()):
        card = CARD_DB[face_uid]
        gs, ms, me = new_match()
        ocean = give_ocean(gs, ms, me)
        # Stock the hand with one of every species so "play a free X" stars have
        # something to grant, and the follow-up window is real.
        for species in ("mammal", "baitfish", "game fish", "cephalopod",
                        "crustacean", "invertebrate", "coral"):
            for uid in sorted(CARD_DB):
                c = CARD_DB[uid]
                if c.species.strip().lower() == species and not fish.is_ocean(c):
                    eu = fish.canonical_entry_uid(ms, uid)
                    if eu not in me.hand:
                        me.hand.append(eu)
                    break
        before_score = me.score
        ok, why, ts = play_with_star(gs, ms, me, face_uid, ocean)
        if not check(ok, f"[{card.name} / ★ {text}] play rejected: {why}"):
            continue
        check(ts.star_activations == 1,
              f"[{card.name} / ★ {text}] star did not activate (activations={ts.star_activations})")
        del before_score


def test_draw_stars_actually_draw():
    """'draw one/two/three' stars put that many cards in hand."""
    expected = {"draw one": 1, "draw two": 2, "draw three": 3}
    for text, want in expected.items():
        face_uid = star_cards_by_text().get(text)
        if not check(face_uid is not None, f"no card carries ★ '{text}'"):
            continue
        card = CARD_DB[face_uid]
        gs, ms, me = new_match()
        ocean = give_ocean(gs, ms, me)
        entry = fish.canonical_entry_uid(ms, face_uid)
        me.hand.append(entry)
        cost = max(0, int(card.cost))
        payments = matching_payment(ms, gs, entry, card.symbol, cost)
        me.hand.extend(p for p in payments if p not in me.hand)
        # hand after the play, before the star draws: minus the card, minus payment
        hand_after_play = len(me.hand) - 1 - cost
        action = fish.Action(
            kind="play_ocean" if fish.is_ocean(card) else "play_to_ocean",
            card_uid=entry, face_uid=face_uid,
            ocean_uid=None if fish.is_ocean(card) else ocean,
            use_star=True, payment_uids=list(payments),
        )
        ts = fish.TurnState()
        reasons: List[str] = []
        ok = fish.apply_action(gs, ms, me, action, ts, fish.choose_payment_ai, fail_reason=reasons)
        if not check(ok, f"[{card.name} ★ {text}] play rejected: {'; '.join(reasons)}"):
            continue
        drawn = len(me.hand) - hand_after_play
        check(drawn >= want,
              f"[{card.name} ★ {text}] expected >= {want} cards drawn, got {drawn}")


def test_free_play_stars_set_a_consumable_flag():
    """Every 'play a free <species>' ★ sets a flag that is_free_play_eligible honours."""
    species_of = {
        "play a free mammal": "mammal",
        "play a free baitfish": "baitfish",
        "play a free game fish": "game fish",
        "play a free crustacean": "crustacean",
        "play a free invertebrate": "invertebrate",
        "play a free coral": "coral",
        "play a free cephalopod": "cephalopod",
        "play any number of cephalopods for free": "cephalopod",
    }
    by_text = star_cards_by_text()
    for text, species in species_of.items():
        face_uid = by_text.get(text)
        if not check(face_uid is not None, f"no card carries ★ '{text}'"):
            continue
        card = CARD_DB[face_uid]
        gs, ms, me = new_match()
        ocean = give_ocean(gs, ms, me)
        # a playable card of the granted species must be in hand
        target = None
        for uid in sorted(CARD_DB):
            c = CARD_DB[uid]
            if fish.is_ocean(c) or c.species.strip().lower() != species:
                continue
            eu = fish.canonical_entry_uid(ms, uid)
            if eu == fish.canonical_entry_uid(ms, face_uid):
                continue
            target = (eu, c)
            break
        if not check(target is not None, f"no {species} card in the deck to play for free"):
            continue
        me.hand.append(target[0])
        ok, why, _ts = play_with_star(gs, ms, me, face_uid, ocean)
        if not check(ok, f"[{card.name} ★ {text}] play rejected: {why}"):
            continue
        eligible = fish.is_free_play_eligible(me, target[1])
        check(eligible,
              f"[{card.name} ★ {text}] granted no usable free play for a {species}")


def test_play_again_grants_a_replay():
    """'play again' ★ hands back another action this turn."""
    for text in ("play again", "<inferred play again>"):
        face_uid = star_cards_by_text().get(text)
        if face_uid is None:
            continue
        card = CARD_DB[face_uid]
        gs, ms, me = new_match()
        ocean = give_ocean(gs, ms, me)
        # keep a playable follow-up in hand so the replay is worth taking
        for uid in sorted(CARD_DB):
            c = CARD_DB[uid]
            if not fish.is_ocean(c) and c.cost == 0:
                me.hand.append(fish.canonical_entry_uid(ms, uid))
                break
        before = int(me.flags.get("replay_actions", 0) or 0)
        ok, why, _ts = play_with_star(gs, ms, me, face_uid, ocean)
        if not check(ok, f"[{card.name} ★ {text}] play rejected: {why}"):
            continue
        after = int(me.flags.get("replay_actions", 0) or 0)
        check(after > before or any("play again" in ln.lower() for ln in gs.log[-6:]),
              f"[{card.name} ★ {text}] no replay granted (replay_actions {before} → {after})")


def test_star_needs_a_matching_symbol():
    """A payment with no matching symbol must be refused on a use_star play."""
    face_uid = star_cards_by_text()["draw one"]
    card = CARD_DB[face_uid]
    gs, ms, me = new_match()
    ocean = give_ocean(gs, ms, me)
    entry = fish.canonical_entry_uid(ms, face_uid)
    me.hand.append(entry)
    sym = fish.normalize_symbol(card.symbol)
    wrong: List[int] = []
    for uid in sorted(CARD_DB):
        if fish.canonical_entry_uid(ms, uid) != uid or uid == entry:
            continue
        if fish.entry_is_ocean(ms, gs, uid):
            continue
        if fish.symbol_match_for_entry(ms, gs, uid, sym):
            continue
        wrong.append(uid)
        if len(wrong) == max(1, int(card.cost)):
            break
    me.hand.extend(wrong)
    action = fish.Action(kind="play_to_ocean", card_uid=entry, face_uid=face_uid,
                         ocean_uid=ocean, use_star=True, payment_uids=list(wrong))
    ts = fish.TurnState()
    ok = fish.apply_action(gs, ms, me, action, ts, fish.choose_payment_ai)
    check(not ok or ts.star_activations == 0,
          f"[{card.name}] star fired on a payment with no {sym} card")


def test_two_sided_card_fires_the_face_it_played():
    """On a pair where only one face has a ★, the ★ follows the face played."""
    pair_map, face_map = fish.build_non_ocean_pair_maps(CARD_DB)
    tested = 0
    for entry, (a_uid, b_uid) in sorted(pair_map.items()):
        a, b = CARD_DB[a_uid], CARD_DB[b_uid]
        if fish.has_star_ability(a) == fish.has_star_ability(b):
            continue
        starred, plain = (a_uid, b_uid) if fish.has_star_ability(a) else (b_uid, a_uid)
        sc = CARD_DB[starred]
        if int(sc.cost) <= 0:
            continue
        gs, ms, me = new_match()
        ocean = give_ocean(gs, ms, me)
        ok, why, ts = play_with_star(gs, ms, me, starred, ocean)
        if not check(ok, f"[{sc.name} (pair {entry})] starred face rejected: {why}"):
            continue
        check(ts.star_activations == 1,
              f"[{sc.name} (pair {entry})] starred face did not fire its ★")
        # The plain face must not claim a star.
        check(not fish.has_star_ability(CARD_DB[plain]),
              f"[pair {entry}] plain face {CARD_DB[plain].name} unexpectedly reports a ★")
        tested += 1
        if tested >= 12:
            break
    check(tested > 0, "no mixed-star two-sided pairs were exercised")


def test_star_symbols_are_real_symbols():
    """Every ★ card carries one of the five real symbols (never N/A)."""
    valid = {"triangle", "square", "heart", "circle", "diamond"}
    for uid in sorted(CARD_DB):
        c = CARD_DB[uid]
        if not fish.has_star_ability(c):
            continue
        sym = fish.normalize_symbol(c.symbol)
        check(sym in valid,
              f"[{uid} {c.name}] has a ★ but symbol {c.symbol!r} is not one of the five")
        check(int(c.cost) > 0,
              f"[{uid} {c.name}] has a ★ but costs {c.cost}, so it can never be activated")


def test_free_play_star_species_are_declared():
    """star_followup_species_targets covers every 'free <species>' ★ text."""
    for text, face_uid in sorted(star_cards_by_text().items()):
        if "free" not in text:
            continue
        card = CARD_DB[face_uid]
        targets = fish.star_followup_species_targets(card)
        check(bool(targets),
              f"[{card.name} ★ {text}] declares no follow-up species, so the bots never prep for it")


def test_legal_action_payload_never_promises_an_unfirable_star():
    """The gold-border contract: star_symbol ⇒ a real use_star twin is on offer.

    The client paints the gold "discard this to fire the ★" border from
    required_symbol (on the ★ variant) or star_symbol (on the plain one). Since
    the engine only fires a ★ for a use_star play, star_symbol on an action with
    no use_star twin would light cards gold for a star that can never happen.
    """
    import multiplayer_server as mps

    valid = {"triangle", "square", "heart", "circle", "diamond"}
    scanned = 0
    for seed in range(6):
        gs, ms, me = new_match()
        rng = __import__("random").Random(seed)
        # A varied hand plus a couple of oceans, so lots of plays are legal.
        give_ocean(gs, ms, me)
        give_ocean(gs, ms, me, "mangrove")
        entries = [u for u in sorted(CARD_DB) if fish.canonical_entry_uid(ms, u) == u]
        rng.shuffle(entries)
        me.hand = entries[:10]
        actions = fish.legal_actions(gs, ms, me)
        payload = mps.GameRoom._serialize_legal_actions(
            mps.GameRoom.__new__(mps.GameRoom), gs, ms, me, actions
        )
        star_twins = {
            (a["card_uid"], a["face_uid"], a["kind"], a["ocean_uid"])
            for a in payload["actions"] if a["use_star"]
        }
        for a in payload["actions"]:
            scanned += 1
            key = (a["card_uid"], a["face_uid"], a["kind"], a["ocean_uid"])
            if a["star_symbol"]:
                check(not a["use_star"],
                      f"star_symbol set on a use_star action ({a['face_name']})")
                check(a["star_symbol"] in valid,
                      f"[{a['face_name']}] star_symbol {a['star_symbol']!r} is not a real symbol")
                check(key in star_twins,
                      f"[{a['face_name']}] offers '★ if {a['star_symbol']}' but no use_star "
                      f"variant exists, the gold payment border would be a lie")
                check(int(a["cost_to_pay"]) > 0,
                      f"[{a['face_name']}] star_symbol on a free play, nothing to discard")
            if a["use_star"]:
                check(bool(a["requires_symbol_match"]) and a["required_symbol"] in valid,
                      f"[{a['face_name']}] use_star action without a usable required_symbol")
                # The required symbol must be the symbol of the FACE being played.
                face = CARD_DB.get(a["face_uid"])
                check(face is not None and a["required_symbol"] == fish.normalize_symbol(face.symbol),
                      f"[{a['face_name']}] required_symbol {a['required_symbol']!r} is not the "
                      f"played face's symbol")
            # has_star_ability answers for the PLAY, never for affordability.
            # It is the played face's own ★ for every card but one: a Clownfish
            # "copies the Ocean's ability this card is attached to", ★ included,
            # so it has one on a Mangrove or an Arctic Ocean (both "play again")
            # and none on any other host. That is why the check takes ocean_uid.
            face = CARD_DB.get(a["face_uid"])
            if face is not None and a["kind"] in {"play_ocean", "play_to_ocean"}:
                want = bool(fish.has_star_ability_for_play(gs, face, a["ocean_uid"]))
                check(bool(a["has_star_ability"]) == want,
                      f"[{a['face_name']}] has_star_ability disagrees with the card data")
                # …and when there is one, the client is told what it does, since
                # a borrowed ★ is not in the played card's own text.
                if want:
                    check(bool(str(a.get("star_ability", "")).strip()),
                          f"[{a['face_name']}] has a ★ but the payload does not say what it does")
            check(bool(a["star_available"]) == bool(a["star_symbol"] or (a["use_star"] and a["required_symbol"])),
                  f"[{a['face_name']}] star_available out of step with star_symbol/required_symbol")
    check(scanned > 0, "no legal actions were scanned")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"star ability checks: {CHECKS}")
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("  ✗ " + f)
        return 1
    print("all star abilities OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
