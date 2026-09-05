"""The table's permission to use the Current Controller (the mod menu).

Run:  python3 test_current_controller.py

The Controller can read every hand, deal cards and drive the bots. Handing that
to a paying player is only defensible if the people it would be used ON agree,
so the rule is: asked in the LOBBY, before a card is dealt, and unanimous.

What is pinned here is everything that decides whether somebody may mod a game,
because every one of these has a failure mode that pays out silently:

  * a competitive or ranked room can never arm it (real Ocean Points),
  * one "no" ends the request for the game (a table cannot be worn down),
  * only the seat that ASKED may mod, never everyone at an armed table,
  * a rematch asks again rather than inheriting the last game's yes,
  * a player who walked out cannot hold the vote, and cannot carry it either,
  * ARMED IS NOT MODDED: permission granted and never used leaves an ordinary
    game, because rewards key off `_admin_active` (a mod op really landed).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("ADMIN_MOD_KEY", None)

import multiplayer_server as ms


def room(humans=3, bots=1, **kw):
    r = ms.GameRoom(room_id="t", host_name="P0", total_players=humans + bots,
                    human_players=humans, ai_players=bots, **kw)
    for seat in r.seats:
        if seat.kind == "human":
            seat.claimed_name = f"P{seat.index}"
            seat.token = f"tok{seat.index}"
    return r


def humans_of(r):
    return [s for s in r.seats if s.kind == "human"]


class TestWhoMayAsk(unittest.TestCase):
    def test_a_casual_room_may(self):
        self.assertTrue(room()._cc_casual_locked())

    def test_competitive_and_ranked_may_not(self):
        """These pay Ocean Points and move a public ladder: not a table's own
        game to agree to modify."""
        for kw in ({"competitive": True}, {"ranked": True}):
            r = room(humans=2, bots=0, **kw)
            self.assertFalse(r._cc_casual_locked(), kw)
            out = r.controller_request({"seat_token": humans_of(r)[0].token})
            self.assertFalse(out["ok"], kw)
            self.assertIn("casual", out["error"])

    def test_a_tournament_match_may_not(self):
        r = room(humans=2, bots=0)
        r.tournament_id = "cup1"
        self.assertFalse(r._cc_casual_locked())

    def test_a_seat_token_is_required(self):
        r = room()
        self.assertFalse(r.controller_request({"seat_token": "nonsense"})["ok"])
        self.assertFalse(r.controller_vote({"seat_token": "", "vote": True})["ok"])


class TestTheVote(unittest.TestCase):
    def test_everyone_else_has_to_say_yes(self):
        r = room()           # 3 humans + 1 bot
        h = humans_of(r)
        out = r.controller_request({"seat_token": h[0].token})
        self.assertTrue(out["ok"])
        self.assertEqual(out["needed"], 2, "the asker is not one of the voters")
        self.assertFalse(out["armed"])

        r.controller_vote({"seat_token": h[1].token, "vote": True})
        self.assertFalse(r.cc_armed, "one yes of two is not the table")

        r.controller_vote({"seat_token": h[2].token, "vote": True})
        self.assertTrue(r.cc_armed)

    def test_bots_do_not_vote(self):
        """A bot cannot object to being driven, so it is not in the denominator."""
        r = room(humans=2, bots=6)
        h = humans_of(r)
        self.assertEqual(r.controller_request({"seat_token": h[0].token})["needed"], 1)

    def test_one_no_ends_it_for_the_game(self):
        r = room()
        h = humans_of(r)
        r.controller_request({"seat_token": h[0].token})
        r.controller_vote({"seat_token": h[1].token, "vote": False})
        self.assertTrue(r.cc_denied)
        self.assertFalse(r.cc_armed)
        # …and a later yes cannot revive it.
        self.assertFalse(r.controller_vote({"seat_token": h[2].token, "vote": True})["ok"])
        self.assertFalse(r.cc_armed)
        # …nor can asking again.
        again = r.controller_request({"seat_token": h[0].token})
        self.assertFalse(again["ok"])
        self.assertIn("already said no", again["error"])

    def test_alone_with_bots_arms_at_once(self):
        """"Everyone agreed" is vacuously true at a table of one."""
        r = room(humans=1, bots=3)
        out = r.controller_request({"seat_token": humans_of(r)[0].token})
        self.assertEqual((out["needed"], out["armed"]), (0, True))

    def test_only_the_asker_may_mod(self):
        r = room()
        h = humans_of(r)
        r.controller_request({"seat_token": h[0].token})
        r.controller_vote({"seat_token": h[1].token, "vote": True})
        r.controller_vote({"seat_token": h[2].token, "vote": True})
        self.assertTrue(r._cc_may_mod_locked(h[0]))
        for other in h[1:]:
            self.assertFalse(r._cc_may_mod_locked(other),
                             "an armed table is not an armed table FOR EVERYONE")

    def test_nobody_may_mod_before_the_vote_passes(self):
        r = room()
        h = humans_of(r)
        self.assertFalse(r._cc_may_mod_locked(h[0]), "not even the asker, before asking")
        r.controller_request({"seat_token": h[0].token})
        self.assertFalse(r._cc_may_mod_locked(h[0]), "…nor while the table is deciding")

    def test_two_people_cannot_both_hold_the_request(self):
        r = room()
        h = humans_of(r)
        r.controller_request({"seat_token": h[0].token})
        out = r.controller_request({"seat_token": h[1].token})
        self.assertFalse(out["ok"])
        self.assertIn("already asked", out["error"])

    def test_asking_is_a_lobby_thing(self):
        r = room()
        r.phase = "running"
        out = r.controller_request({"seat_token": humans_of(r)[0].token})
        self.assertFalse(out["ok"])
        self.assertIn("before the game starts", out["error"])

    def test_a_voter_who_leaves_does_not_carry_the_tally(self):
        """Their ballot stops counting the moment they are not at the table,
        or the last player standing inherits a vote nobody gave them."""
        r = room()
        h = humans_of(r)
        r.controller_request({"seat_token": h[0].token})
        r.controller_vote({"seat_token": h[1].token, "vote": True})
        h[1].left_at = 1.0
        tally = r._cc_tally_locked()
        self.assertEqual(tally["yes"], 0)
        self.assertEqual(tally["needed"], 1, "and they are out of the denominator too")


class TestAChairIsNotAPerson(unittest.TestCase):
    """The permission belongs to whoever asked, not to the seat they sat in.

    Every one of these is the same bug wearing a different hat: a seat empties,
    somebody else claims it, and an index-based check hands the newcomer a yes
    the table gave to a different person.
    """

    def _armed(self):
        r = room(humans=2, bots=2)
        h = humans_of(r)
        r.controller_request({"seat_token": h[0].token})
        r.controller_vote({"seat_token": h[1].token, "vote": True})
        assert r.cc_armed
        return r, h

    def test_a_stranger_in_the_askers_chair_gets_nothing(self):
        r, h = self._armed()
        h[0].token = "somebody-else"        # the seat was re-claimed
        self.assertFalse(r._cc_may_mod_locked(h[0]))

    def test_an_asker_who_walked_out_may_not_mod(self):
        r, h = self._armed()
        h[0].left_at = 1.0
        self.assertFalse(r._cc_may_mod_locked(h[0]))

    def test_a_kicked_asker_may_not_mod(self):
        r, h = self._armed()
        h[0].kicked = True
        self.assertFalse(r._cc_may_mod_locked(h[0]))

    def test_a_request_left_behind_is_cleared(self):
        """An open request whose asker has gone must not sit on the lobby: it
        would block everyone else from asking, for a game nobody is waiting on."""
        r = room(humans=3, bots=0)
        h = humans_of(r)
        r.controller_request({"seat_token": h[0].token})
        h[0].left_at = 1.0
        r._cc_payload_locked(None)                     # a poll notices
        self.assertIsNone(r.cc_seat)
        self.assertTrue(r.controller_request({"seat_token": h[1].token})["ok"],
                        "somebody else can now ask")

    def test_a_departure_can_complete_the_vote(self):
        """Two votes needed, one cast, the other voter leaves: the people
        actually at the table have all said yes, so it arms on the next poll
        instead of the asker waiting for a vote nobody can cast."""
        r = room(humans=3, bots=0)
        h = humans_of(r)
        r.controller_request({"seat_token": h[0].token})
        r.controller_vote({"seat_token": h[1].token, "vote": True})
        self.assertFalse(r.cc_armed)
        h[2].left_at = 1.0
        r._cc_payload_locked(None)
        self.assertTrue(r.cc_armed)


class TestArmedIsNotModded(unittest.TestCase):
    def test_permission_alone_leaves_an_ordinary_game(self):
        r = room(humans=1, bots=3)
        r.controller_request({"seat_token": humans_of(r)[0].token})
        self.assertTrue(r.cc_armed)
        self.assertFalse(r._cc_payload_locked(None)["modded"],
                         "a panel nobody opened must not cost anybody their game")

    def test_using_it_marks_the_game(self):
        r = room(humans=1, bots=3)
        r.controller_request({"seat_token": humans_of(r)[0].token})
        r._admin_active = True                      # what admin_activate() sets
        self.assertTrue(r._cc_payload_locked(None)["modded"])


class TestThePayload(unittest.TestCase):
    def test_a_voter_is_told_it_is_their_call(self):
        r = room()
        h = humans_of(r)
        r.controller_request({"seat_token": h[0].token})
        mine = r._cc_payload_locked(h[0])
        theirs = r._cc_payload_locked(h[1])
        self.assertTrue(mine["is_mine"])
        self.assertFalse(mine["can_vote"], "you do not vote on your own request")
        self.assertTrue(theirs["can_vote"])
        self.assertEqual(theirs["asker"], "P0")

    def test_it_reads_cleanly_with_no_request(self):
        p = room()._cc_payload_locked(None)
        self.assertEqual((p["seat"], p["armed"], p["denied"], p["needed"]),
                         (None, False, False, 0))
        self.assertTrue(p["allowed_here"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
