#!/usr/bin/env python3
"""Every free play the engine grants is one the client is TOLD about.

A ★ can hand you "play a free Mammal", "play a free Baitfish", a free Game
Fish, Cephalopod, Crustacean, Invertebrate, Coral, or two free Yellowfin Tuna.
The engine records each as a flag on the player and honours it in
is_free_play_eligible(). The client only ever learns about it through
legal_actions.free_play_species, and anything missing from that list is a free
play the player is holding with nothing on screen to say so: no line in the
guide bar, no card lit up in the hand, no flash. The Yellowfin Tuna was exactly
that, for the whole life of the card, because it is granted by card NAME (and
as a COUNT, normally two) and the list was built from species alone.

Nothing here is asserted against a hand-written list of cards. The star text is
read out of the shipped deck files and run through the real ability executor,
so a card whose wording is edited is judged on its NEW wording.

Run:  python3 test_free_play_species.py
"""
import unittest

import fish_game_all_in_one as fish
from multiplayer_server import free_play_species_for, _FREE_PLAY_SPECIES_FLAGS

CARD_FILES = ["cards_vertical.txt", "cards_lr.txt", "cards_oceans.txt"]


def _deck():
    db = {}
    for path in CARD_FILES:
        with open(path, "r", encoding="utf-8") as fh:
            db = fish.load_cards_from_lines(fh.read(), db, source=path, strict=True)
    fish.register_all_card_abilities(db)
    return db


class _Player:
    """Just the one attribute free_play_species_for() reads."""

    def __init__(self, **flags):
        self.flags = dict(flags)


class FreePlaySpecies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = _deck()

    # ── the rule itself ────────────────────────────────────────────────────
    def test_no_flags_is_no_free_play(self):
        self.assertEqual(free_play_species_for(_Player()), [])

    def test_each_flag_names_something(self):
        for label, keys in _FREE_PLAY_SPECIES_FLAGS:
            for key in keys:
                got = free_play_species_for(_Player(**{key: True}))
                self.assertIn(label, got, f"{key} should offer {label!r}, got {got}")

    def test_a_spent_count_is_not_a_free_play(self):
        """free_yellowfin_tuna counts down to 0, and 0 is not a free play."""
        self.assertEqual(free_play_species_for(_Player(free_yellowfin_tuna=2)),
                         ["Yellowfin Tuna"])
        self.assertEqual(free_play_species_for(_Player(free_yellowfin_tuna=0)), [])

    def test_two_at_once_are_both_named(self):
        got = free_play_species_for(_Player(free_mammal=True, free_coral=True))
        self.assertEqual(got, ["Mammal", "Coral"])

    # ── against the engine ─────────────────────────────────────────────────
    def test_every_flag_the_engine_honours_is_offered(self):
        """is_free_play_eligible() is the engine's own answer to 'can I play
        this for nothing'. Every flag it reads has to reach the client."""
        import inspect
        src = inspect.getsource(fish.is_free_play_eligible)
        engine_flags = set()
        for line in src.splitlines():
            if 'flags.get("' in line:
                engine_flags.add(line.split('flags.get("')[1].split('"')[0])
        offered = {k for _, keys in _FREE_PLAY_SPECIES_FLAGS for k in keys}
        missing = engine_flags - offered
        self.assertEqual(missing, set(),
                         f"the engine honours {sorted(missing)}, the client is never told")

    def test_the_cards_that_grant_them_really_do(self):
        """Fire each granting card's star ability for real and check the
        player ends up with a free play the client would be told about."""
        want = {
            "play a free mammal": "Mammal",
            "play a free baitfish": "Baitfish",
            "play a free game fish": "Game Fish",
            "play a free coral": "Coral",
        }
        seen = {}
        for card in self.db.values():
            text = (getattr(card, "text", "") or "").lower()
            for phrase, label in want.items():
                if phrase in text:
                    seen.setdefault(label, card)
        self.assertTrue(seen, "no card in the deck grants a free play by text")
        for label, card in sorted(seen.items()):
            with self.subTest(card=card.name):
                player = self._fire(card)
                got = free_play_species_for(player)
                self.assertIn(label, got,
                              f"{card.name} grants {label}, the client is told {got}")

    def test_the_yellowfin_tuna_card_is_announced(self):
        """The one granted by name. Its star sets a COUNT of 2, and before this
        existed the client was told nothing at all."""
        granter = None
        for card in self.db.values():
            text = (getattr(card, "text", "") or "").lower()
            if "yellowfin tuna" in text and "free" in text:
                granter = card
                break
        if granter is None:
            # True as of the current deck: the engine still honours
            # free_yellowfin_tuna (is_free_play_eligible reads it, and
            # has_multi_play_window counts it as an open window), but the
            # Hermit Crab's text was changed to "play any number of baitfish
            # this turn for free" and nothing sets the flag any more. The rule
            # covers it so the day a card says it again, it is announced
            # instead of silently doing nothing on screen.
            self.skipTest("no shipped card grants free Yellowfin Tuna; the "
                          "engine still honours the flag, so the rule keeps it")
        player = self._fire(granter)
        self.assertGreater(int(player.flags.get("free_yellowfin_tuna", 0)), 0,
                           f"{granter.name} did not set the flag")
        self.assertIn("Yellowfin Tuna", free_play_species_for(player))

    # ── helpers ────────────────────────────────────────────────────────────
    def _fire(self, card):
        """Play one card, star ability and all, onto an otherwise empty board,
        exactly as test_end_turn_callout_cards.py does. Returns the player so
        its flags can be read."""
        player = fish.PlayerState(name="Tester")
        gs = fish.GameState(card_db=self.db, players=[player], deck=[])
        main, star = fish.split_main_and_star(card.text)
        fish._execute_main_pattern(gs, player, main, card, {})
        if star.strip():
            fish._execute_star_pattern(gs, player, star, card, {})
        return player


if __name__ == "__main__":
    unittest.main(verbosity=2)
