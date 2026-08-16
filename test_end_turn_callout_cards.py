#!/usr/bin/env python3
"""WHICH CARDS put the "Still your turn — tap ✓ End Turn" callout on screen.

The callout exists for the one situation players read as a frozen game: a turn
that will not end by itself. Two cards do that, and one that looks like it does
must not fire it:

    Hermit Crab            "Play any number of baitfish this turn for free"  → YES
    Loggerhead Sea Turtle  "Play any number of cards by paying the costs"    → YES
    Roosterfish            "*play a free Baitfish*"                          → NO

The Roosterfish is ONE free card. The turn carries on ending exactly the way it
always does, so a prompt there is noise on an ordinary turn.

Nothing here is asserted against a hand-written list of card names — the card
text is read out of the shipped deck files and run through the real ability
executor, so a card whose wording is edited is judged on its NEW wording. The
line between the two behaviours is has_multi_play_window(), and this proves
that line falls exactly where it is meant to.

Run:  python3 test_end_turn_callout_cards.py
"""
import re
import unittest

import fish_game_all_in_one as fish


CARD_FILES = ["cards_vertical.txt", "cards_lr.txt", "cards_oceans.txt"]


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _deck():
    """Every card in the shipped deck files, by lowercased name."""
    db = {}
    for path in CARD_FILES:
        with open(path, "r", encoding="utf-8") as fh:
            db = fish.load_cards_from_lines(fh.read(), db, source=path, strict=True)
    fish.register_all_card_abilities(db)
    by_name = {}
    for card in db.values():
        by_name.setdefault(card.name.strip().lower(), card)
    return db, by_name


DB, BY_NAME = _deck()


def play(name):
    """Play one card, star ability and all, onto an otherwise empty board.
    Returns the PlayerState so its flags can be read."""
    card = BY_NAME[name]
    player = fish.PlayerState(name="Tester")
    gs = fish.GameState(card_db=DB, players=[player], deck=[])
    main, star = fish.split_main_and_star(card.text)
    fish._execute_main_pattern(gs, player, main, card, {})
    if star.strip():
        fish._execute_star_pattern(gs, player, star, card, {})
    return player


class OpenPlayWindow(unittest.TestCase):
    """has_multi_play_window() is the server's answer to 'does this turn end
    by itself?', and the callout is wired straight to it."""

    def test_hermit_crab_opens_the_window(self):
        p = play("hermit crab")
        self.assertTrue(fish.has_multi_play_window(p),
                        "Hermit Crab lets you keep playing baitfish — the turn "
                        "sits there until you End Turn")

    def test_sea_turtle_opens_the_window(self):
        p = play("loggerhead sea turtle")
        self.assertTrue(fish.has_multi_play_window(p),
                        "Loggerhead Sea Turtle lets you keep paying for plays — "
                        "the turn sits there until you End Turn")

    def test_roosterfish_does_not(self):
        p = play("roosterfish")
        self.assertFalse(fish.has_multi_play_window(p),
                         "Roosterfish is ONE free Baitfish — the turn ends the "
                         "way it always does, so no callout")

    def test_roosterfish_still_grants_its_free_baitfish(self):
        """The point is that the callout is off, NOT that the ability is gone."""
        p = play("roosterfish")
        self.assertTrue(p.flags.get("free_baitfish"))

    def test_the_one_shot_and_the_chain_are_different_flags(self):
        """Roosterfish and Hermit Crab both say 'free baitfish'; only the chain
        flag survives into has_multi_play_window."""
        rooster, crab = play("roosterfish"), play("hermit crab")
        self.assertTrue(rooster.flags.get("free_baitfish"))
        self.assertFalse(rooster.flags.get("free_baitfish_chain"))
        self.assertTrue(crab.flags.get("free_baitfish_chain"))

    def test_no_ordinary_card_fires_it(self):
        """Sweep the WHOLE deck: only 'play any number' wording may open the
        window. Anything else would put the callout on a normal turn."""
        offenders = []
        for name in sorted(BY_NAME):
            if name == "end game":
                continue
            if fish.has_multi_play_window(play(name)):
                text = BY_NAME[name].text.lower()
                if "any number" not in text and "any #" not in text and "two free" not in text:
                    offenders.append(f"{name}: {BY_NAME[name].text}")
        self.assertEqual(offenders, [], "cards opening the window with no "
                                        "'play any number' wording:\n" + "\n".join(offenders))

    def test_a_plain_free_play_never_opens_it(self):
        """Every one-shot '*play a free X*' card, swept out of the deck."""
        for name, card in sorted(BY_NAME.items()):
            _, star = fish.split_main_and_star(card.text)
            s = star.lower()
            if re.search(r"play a free \w", s) and "any number" not in card.text.lower():
                self.assertFalse(fish.has_multi_play_window(play(name)),
                                 f"{name} grants ONE free card ({card.text!r}) "
                                 "and must not fire the callout")


class ServerAndClientAreWired(unittest.TestCase):
    """The flag has to survive the trip: rules → legal-actions payload → client."""

    def test_server_publishes_it_from_has_multi_play_window(self):
        src = read("multiplayer_server.py")
        self.assertIn("is_open_play_window = bool(fish.has_multi_play_window(player))", src)
        self.assertIn('legal_payload["is_open_play_window"] = is_open_play_window', src)

    def test_it_is_read_not_popped(self):
        """The window is live for the WHOLE turn. is_replay_turn is popped
        because it announces one moment; this one must stay true across every
        action the player takes inside the window, or the callout would blink
        out the first time they played a card."""
        src = read("multiplayer_server.py")
        self.assertNotIn('flags.pop("multi_play_paid_turn"', src)
        self.assertNotIn('flags.pop("free_baitfish_chain"', src)

    def test_the_fallback_payload_carries_it_too(self):
        """When _serialize_legal_actions throws, the hand-built payload is what
        the client gets — it must not silently drop the callout."""
        src = read("multiplayer_server.py")
        self.assertIn('"is_open_play_window": is_open_play_window,', src)

    def test_client_ors_the_two_flags_in_one_place(self):
        src = read("multiplayer/client/js/preview-app.js")
        self.assertIn("function turnWaitsOnEndTurn(lw)", src)
        self.assertIn("lw.is_replay_turn || lw.is_open_play_window", src)
        # …and every place that turns the callout ON goes through it.
        for m in re.finditer(r"setPlayAgainCallout\((.*?)\);", src):
            arg = m.group(1)
            self.assertTrue(arg in ("false", "true") or "turnWaitsOnEndTurn(lw)" in arg,
                            f"setPlayAgainCallout({arg}) bypasses turnWaitsOnEndTurn")

    def test_the_turn_flags_are_cleared_at_turn_end(self):
        """A window that outlived its turn would leave the callout up on a
        normal turn — worse than never showing it."""
        import inspect
        src = inspect.getsource(fish.clear_turn_only_flags)
        for flag in ("multi_play_paid_turn", "free_baitfish_chain",
                     "free_cephalopods", "free_yellowfin_tuna"):
            self.assertIn(flag, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
