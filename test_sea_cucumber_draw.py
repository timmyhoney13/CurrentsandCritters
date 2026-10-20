"""Johnson's Sea Cucumber draws one card per copy when you play a GAME FISH.

Why this file exists
--------------------
On 2026-09-12, commit 5ca4bc5 rewrote both printed faces of this card from
"Draw one when you play a game fish" to "Draw one when you play a yellowfin
tuna", and moved the engine listener with it: trigger_draw_on_game_fish became
trigger_draw_on_yellowfin, firing on the played card's NAME instead of its
SPECIES. That was a guess at what the card was meant to do, and the guess was
wrong -- the card draws off any Game Fish. Playing a Mahi Mahi, a Blue Marlin or
a Sailfish with a Cucumber on the board drew nothing at all.

Two things are asserted here that the old code got wrong even on its own terms:

  * It scales. There are two physical cards carrying a Sea Cucumber face (the
    California Gull card and the Mullet card), so two can sit on one board, and
    two Cucumbers draw two cards.
  * A Game Fish that draws on its OWN text still fires the Cucumber. Goliath
    Grouper reads "Draw one | *Play again*", and the engine used to skip the
    Cucumber entirely for any game fish whose main text contained a draw. Those
    are two separate abilities and both land. (That branch was dormant while the
    trigger matched on the Yellowfin's name, because the Yellowfin does not draw
    on its own text, so restoring the species match would have woken it up.)

The species boundary is asserted too, because it is easy to assume wrong: a
Giant Squid is a CEPHALOPOD, not a Game Fish, and does not fire the Cucumber.
The Manta Ray is the card that draws on a cephalopod.

Card text is read out of the shipped deck files and run through the real
ability executor and the real apply_action, so a card whose wording is edited is
judged on its NEW wording.

Run:  python3 test_sea_cucumber_draw.py
"""

import importlib.util
import sys
import unittest
from typing import Dict, List, Optional

spec = importlib.util.spec_from_file_location("fish", "fish_game_all_in_one.py")
fish = importlib.util.module_from_spec(spec)
sys.modules["fish"] = fish
spec.loader.exec_module(fish)

CARD_DB: Dict[int, "fish.CardDef"] = fish.load_card_db()

CUCUMBER = "johnson's sea cucumber"


def faces_named(name: str) -> List[int]:
    want = name.strip().lower()
    return sorted(u for u, c in CARD_DB.items() if c.name.strip().lower() == want)


def one_face_named(name: str) -> int:
    got = faces_named(name)
    assert got, f"no card face named {name!r} in the deck"
    return got[0]


def new_match():
    pair_map, face_map = fish.build_non_ocean_pair_maps(CARD_DB)
    ms = fish.MatchState(pair_primary_to_faces=pair_map, face_to_primary=face_map)
    me = fish.PlayerState(name="Tester")
    opp = fish.PlayerState(name="Rival")
    gs = fish.GameState(card_db=CARD_DB, players=[me, opp], deck=[])
    # A deep deck so every reactive draw has a card to take.
    gs.deck = [u for u in sorted(CARD_DB) if fish.canonical_entry_uid(ms, u) == u][:80]
    return gs, ms, me


def give_ocean(gs, ms, player, ocean_name: str = "coral reef") -> int:
    """Put a free-standing ocean on the board. Calling it twice gives two oceans."""
    for uid in sorted(CARD_DB):
        c = CARD_DB[uid]
        if (fish.is_ocean(c) and c.name.strip().lower() == ocean_name
                and uid not in player.board_oceans):
            player.board_oceans.append(uid)
            player.ocean_slots[uid] = fish.OceanSlots()
            return uid
    raise AssertionError(f"no spare ocean named {ocean_name}")


def put_on_board(gs, ms, player, face_uid: int, ocean_uid: int) -> None:
    """Place a face straight onto the board, bypassing the play (and its cost).

    The Cucumber has to be on the board ALREADY for its listener to fire, which
    is what a real board looks like by the time a game fish comes down.
    """
    direction = fish.card_direction_lc(CARD_DB[face_uid])
    player.ocean_slots[ocean_uid].slot(direction).append(face_uid)
    fish.sync_reactive_trigger_flags(gs, player)


def filler_payment(ms, gs, exclude: int, count: int, symbol: str) -> List[int]:
    """`count` hand entries that deliberately do NOT match `symbol`.

    Non-matching on purpose: a plain play must not be able to auto-fire a star,
    so the draws these tests count are only the reactive ones.
    """
    sym = fish.normalize_symbol(symbol)
    picks: List[int] = []
    for uid in sorted(CARD_DB):
        if fish.canonical_entry_uid(ms, uid) != uid:
            continue
        if uid == exclude or uid in picks:
            continue
        if fish.entry_is_ocean(ms, gs, uid):
            continue
        if fish.symbol_match_for_entry(ms, gs, uid, sym):
            continue
        picks.append(uid)
        if len(picks) == count:
            break
    assert len(picks) == count, f"could not build a {count}-card non-matching payment"
    return picks


def play_plain(gs, ms, player, face_uid: int, ocean_uid: Optional[int]):
    """Play a face with use_star=False. Returns (ok, why, cards_drawn)."""
    card = CARD_DB[face_uid]
    entry = fish.canonical_entry_uid(ms, face_uid)
    if entry not in player.hand:
        player.hand.append(entry)
    cost = max(0, int(card.cost))
    payments = filler_payment(ms, gs, entry, cost, card.symbol) if cost else []
    player.hand.extend(p for p in payments if p not in player.hand)
    # Hand size the play itself leaves behind: minus the card, minus the payment.
    hand_after_play = len(player.hand) - 1 - len(payments)
    action = fish.Action(
        kind="play_to_ocean",
        card_uid=entry,
        face_uid=face_uid,
        ocean_uid=ocean_uid,
        use_star=False,
        payment_uids=list(payments),
    )
    ts = fish.TurnState()
    reasons: List[str] = []
    ok = fish.apply_action(gs, ms, player, action, ts, fish.choose_payment_ai,
                           fail_reason=reasons)
    drawn = len(player.hand) - hand_after_play
    return ok, "; ".join(reasons), drawn


class CardText(unittest.TestCase):
    def test_both_faces_read_game_fish(self):
        """The printed text is the trigger, so the deck files must say game fish."""
        faces = faces_named(CUCUMBER)
        self.assertEqual(len(faces), 2, f"expected 2 Sea Cucumber faces, got {faces}")
        for uid in faces:
            text = CARD_DB[uid].text.strip().lower()
            self.assertIn(
                "draw one when you play a game fish", text,
                f"face {uid} reads {CARD_DB[uid].text!r}",
            )

    def test_two_separate_physical_cards_carry_it(self):
        """Two on one board has to be reachable for per-copy scaling to mean anything."""
        _, face_map = fish.build_non_ocean_pair_maps(CARD_DB)
        entries = {face_map.get(u) for u in faces_named(CUCUMBER)}
        self.assertEqual(len(entries), 2,
                         f"Sea Cucumber faces share a physical card: {entries}")


class DrawsOnGameFish(unittest.TestCase):
    def _setup(self, cucumbers: int):
        gs, ms, me = new_match()
        ocean = give_ocean(gs, ms, me)
        for uid in faces_named(CUCUMBER)[:cucumbers]:
            put_on_board(gs, ms, me, uid, ocean)
        return gs, ms, me, ocean

    def test_no_cucumber_draws_nothing(self):
        gs, ms, me, ocean = self._setup(0)
        ok, why, drawn = play_plain(gs, ms, me, one_face_named("mahi mahi"), ocean)
        self.assertTrue(ok, why)
        self.assertEqual(drawn, 0, "a bare Mahi Mahi draws nothing on its own")

    def test_one_cucumber_draws_one(self):
        gs, ms, me, ocean = self._setup(1)
        ok, why, drawn = play_plain(gs, ms, me, one_face_named("mahi mahi"), ocean)
        self.assertTrue(ok, why)
        self.assertEqual(drawn, 1, "one Sea Cucumber draws 1 on a Mahi Mahi")

    def test_two_cucumbers_draw_two(self):
        gs, ms, me, ocean = self._setup(2)
        ok, why, drawn = play_plain(gs, ms, me, one_face_named("mahi mahi"), ocean)
        self.assertTrue(ok, why)
        self.assertEqual(drawn, 2, "two Sea Cucumbers draw 2 on a Mahi Mahi")

    def test_every_game_fish_name_triggers_it(self):
        """Not just the Mahi Mahi: the trigger is the SPECIES, so all of them fire."""
        names = sorted({
            c.name.strip().lower() for c in CARD_DB.values()
            if c.species.strip().lower() == "game fish"
        })
        self.assertIn("mahi mahi", names)
        for name in names:
            face = one_face_named(name)
            own_draw = self._own_main_draw(face)
            gs, ms, me, ocean = self._setup(1)
            ok, why, drawn = play_plain(gs, ms, me, face, ocean)
            self.assertTrue(ok, f"[{name}] play rejected: {why}")
            self.assertGreaterEqual(
                drawn, 1 + own_draw,
                f"[{name}] is a Game Fish and must fire the Cucumber "
                f"(expected >= {1 + own_draw}, got {drawn})",
            )

    @staticmethod
    def _own_main_draw(face_uid: int) -> int:
        """How many cards the card's own MAIN text draws, so we can add the two up."""
        main, _ = fish.split_main_and_star(CARD_DB[face_uid].text)
        low = main.lower()
        if "draw three" in low:
            return 3
        if "draw two" in low or "draw 2" in low:
            return 2
        if "draw one" in low:
            return 1
        return 0

    def test_goliath_grouper_draws_its_own_and_the_cucumbers(self):
        """Two separate abilities, both land: the Grouper's own draw AND the Cucumber's."""
        face = one_face_named("goliath grouper")
        main, _ = fish.split_main_and_star(CARD_DB[face].text)
        self.assertIn("draw one", main.lower(),
                      "this test only means something while the Grouper draws on its main text")
        gs, ms, me, ocean = self._setup(1)
        ok, why, drawn = play_plain(gs, ms, me, face, ocean)
        self.assertTrue(ok, why)
        self.assertEqual(drawn, 2,
                         "Goliath Grouper draws 1 itself and 1 more from the Sea Cucumber")


class DoesNotTriggerOnOtherSpecies(unittest.TestCase):
    def _one_cucumber(self):
        """Cucumber on one ocean, a clean ocean to play onto.

        Two oceans because the Cucumber sits in the floor slot and most floor
        cards would have nowhere to attach otherwise.
        """
        gs, ms, me = new_match()
        home = give_ocean(gs, ms, me)
        spare = give_ocean(gs, ms, me)
        put_on_board(gs, ms, me, faces_named(CUCUMBER)[0], home)
        return gs, ms, me, spare

    def test_cephalopod_does_not_trigger(self):
        """A Giant Squid is a Cephalopod. The Manta Ray draws on those, not the Cucumber."""
        face = one_face_named("giant squid")
        self.assertEqual(CARD_DB[face].species.strip().lower(), "cephalopod")
        gs, ms, me, ocean = self._one_cucumber()
        ok, why, drawn = play_plain(gs, ms, me, face, ocean)
        self.assertTrue(ok, why)
        self.assertEqual(drawn, 0, "a Cephalopod must not fire the Sea Cucumber")

    def test_crustacean_does_not_trigger(self):
        face = one_face_named("lobster")
        gs, ms, me, ocean = self._one_cucumber()
        ok, why, drawn = play_plain(gs, ms, me, face, ocean)
        self.assertTrue(ok, why)
        self.assertEqual(drawn, 0, "a Crustacean must not fire the Sea Cucumber")

    def test_only_the_cucumber_carries_this_listener(self):
        """If another card grows this text, the counts above stop being about one card."""
        carriers = sorted({
            c.name.strip() for c in CARD_DB.values()
            if "draw one when you play a game fish" in c.text.strip().lower()
        })
        self.assertEqual(carriers, ["Johnson's Sea Cucumber"], f"carriers: {carriers}")


class ListenerRebuild(unittest.TestCase):
    def test_flag_counts_copies_on_board(self):
        gs, ms, me = new_match()
        ocean = give_ocean(gs, ms, me)
        self.assertEqual(int(me.flags.get("trigger_draw_on_game_fish", 0)), 0)
        faces = faces_named(CUCUMBER)
        put_on_board(gs, ms, me, faces[0], ocean)
        self.assertEqual(int(me.flags.get("trigger_draw_on_game_fish", 0)), 1)
        put_on_board(gs, ms, me, faces[1], ocean)
        self.assertEqual(int(me.flags.get("trigger_draw_on_game_fish", 0)), 2)

    def test_flag_clears_on_an_empty_board(self):
        gs, ms, me = new_match()
        give_ocean(gs, ms, me)
        me.flags["trigger_draw_on_game_fish"] = 5
        fish.sync_reactive_trigger_flags(gs, me)
        self.assertNotIn("trigger_draw_on_game_fish", me.flags)


if __name__ == "__main__":
    unittest.main(verbosity=2)
