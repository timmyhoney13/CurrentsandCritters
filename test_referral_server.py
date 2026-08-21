"""test_referral_server.py — the friend-code referral reward.

This is the one feature in the game that pays an account OTHER than the one
making the request, so the tests are about who gets paid, how often, and who
cannot get paid at all:

  1. Both sides paid, once, in one call.
  2. One account can only ever redeem ONE code.       → ledger create() guard
  3. You cannot redeem your own code.
  4. Two accounts cannot refund each other forever.   → mutual-referral guard
  5. A years-old account cannot "sign up" to collect. → the window
  6. Every fifth friend earns the REFERRER a background — not the friend, and
     not five backgrounds.

Plus the friend-code resolution the whole thing hangs off: codes are NOT unique
and are stored as strings on new accounts and integers on a few old ones. Both
have to resolve, and a genuine collision has to ask for the name rather than
paying a stranger.

    python3 test_referral_server.py
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import referral_server as rs  # noqa: E402

# The in-memory Firestore lives next door. Importing it rather than pasting a
# third copy is what keeps the two suites testing the same Firestore semantics —
# especially set(merge=True) and the create() that raises.
from test_level_pass_server import (  # noqa: E402
    ArrayUnion, FakeDb, FakeHandler, Parsed,
)

BACKGROUNDS = [
    "/backgrounds/bg-kelp.png",
    "/backgrounds/bg-coral-reef.png",
    "/backgrounds/bg-artificial-reef.png",
]


class RefBase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        rs.init(
            get_firestore=lambda: self.db,
            verify_token=lambda tok: {"uid": tok[5:]} if tok.startswith("good:") else None,
            background_paths=list(BACKGROUNDS),
        )
        rs._transactional = lambda: (lambda fn: fn)   # type: ignore[assignment]
        rs._array_union = lambda: ArrayUnion          # type: ignore[assignment]
        for key in ("REFERRAL_REWARD_COINS", "REFERRAL_BACKGROUND_EVERY",
                    "REFERRAL_WINDOW_DAYS"):
            os.environ.pop(key, None)

    # ── fixtures ─────────────────────────────────────────────────────────
    def make_user(self, uid, code, *, nickname=None, coins=0,
                  age_days=0, **extra):
        nickname = nickname or uid.upper()
        doc = {
            "nickname": nickname,
            "nickname_lower": nickname.lower(),
            "friend_code": code,
            "created_at": datetime.now(timezone.utc) - timedelta(days=age_days),
            "stats": {"critter_coins": coins},
        }
        doc.update(extra)
        self.db.collection("users")._docs[uid] = doc
        if isinstance(code, str):
            self.db.collection("friend_lookup")._docs[f"{nickname.lower()}_{code}"] = {
                "uid": uid, "nickname": nickname,
            }
        return doc

    def user(self, uid):
        return self.db.collection("users").document(uid).get().to_dict()

    def coins(self, uid):
        return int((self.user(uid).get("stats") or {}).get("critter_coins") or 0)

    def ledger(self):
        return self.db.collection("referral_redemptions")._docs


# ══════════════════════════════════════════════════════════════════════════
#  FRIEND-CODE RESOLUTION
# ══════════════════════════════════════════════════════════════════════════
class CodeResolution(RefBase):
    def test_bare_digits_and_hash_prefix_both_resolve(self):
        self.make_user("them", "4985")
        self.assertEqual(rs.resolve_code(self.db, "4985"), ("them", ""))
        self.assertEqual(rs.resolve_code(self.db, "#4985"), ("them", ""))
        self.assertEqual(rs.resolve_code(self.db, "  4985  "), ("them", ""))

    def test_a_code_stored_as_a_number_still_resolves(self):
        """Signup writes the code as a STRING, but a few old accounts hold an
        int. An == filter for "4985" does not match 4985, and that account
        would look like "no such player" forever."""
        self.make_user("them", 4985)
        self.assertEqual(rs.resolve_code(self.db, "4985"), ("them", ""))

    def test_name_and_code_resolves_through_friend_lookup(self):
        self.make_user("them", "9113", nickname="Twin Midi")
        self.assertEqual(rs.resolve_code(self.db, "Twin Midi#9113"), ("them", ""))
        self.assertEqual(rs.resolve_code(self.db, "Twin Midi 9113"), ("them", ""))

    def test_a_stale_name_falls_back_to_the_code(self):
        """People rename. The name half goes out of date; the digits do not."""
        self.make_user("them", "9113", nickname="Twin Midi")
        del self.db.collection("friend_lookup")._docs["twin midi_9113"]
        self.assertEqual(rs.resolve_code(self.db, "Old Name#9113"), ("them", ""))

    def test_a_shared_code_asks_for_the_name_instead_of_guessing(self):
        # Codes are random 4-digit numbers, so collisions are real. Paying a
        # stranger because two people share 4985 is the failure to avoid.
        self.make_user("them", "4985", nickname="A")
        self.make_user("other", "4985", nickname="B")
        uid, err = rs.resolve_code(self.db, "4985")
        self.assertEqual(uid, "")
        self.assertEqual(err, "ambiguous_code")

    def test_nonsense_is_refused(self):
        self.assertEqual(rs.resolve_code(self.db, "")[1], "no_code")
        self.assertEqual(rs.resolve_code(self.db, "hello")[1], "bad_code")
        self.assertEqual(rs.resolve_code(self.db, "1234")[1], "no_user")


# ══════════════════════════════════════════════════════════════════════════
#  THE PAYOUT
# ══════════════════════════════════════════════════════════════════════════
class Payout(RefBase):
    def test_both_sides_are_paid_in_one_call(self):
        self.make_user("them", "4985", coins=7)
        self.make_user("me", "1111", coins=3)
        res = rs.redeem(self.db, "me", "4985")
        self.assertTrue(res["ok"], res)
        coins = rs.reward_coins()
        self.assertEqual(self.coins("me"), 3 + coins)
        self.assertEqual(self.coins("them"), 7 + coins)
        self.assertEqual(res["referrerName"], "THEM")

    def test_the_referrers_count_goes_up_and_the_friends_does_not(self):
        self.make_user("them", "4985")
        self.make_user("me", "1111")
        rs.redeem(self.db, "me", "4985")
        self.assertEqual(self.user("them")["referral_count"], 1)
        self.assertNotIn("referral_count", self.user("me"))

    def test_one_account_can_only_ever_redeem_one_code(self):
        self.make_user("them", "4985")
        self.make_user("third", "7777")
        self.make_user("me", "1111")
        self.assertTrue(rs.redeem(self.db, "me", "4985")["ok"])
        mine = self.coins("me")

        again = rs.redeem(self.db, "me", "7777")
        self.assertFalse(again["ok"])
        self.assertEqual(again["error"], "already_redeemed")
        self.assertEqual(self.coins("me"), mine, "a second code paid out")
        self.assertEqual(self.coins("third"), 0, "a second referrer was paid")

    def test_redeeming_the_same_code_twice_pays_once(self):
        self.make_user("them", "4985")
        self.make_user("me", "1111")
        rs.redeem(self.db, "me", "4985")
        theirs = self.coins("them")
        self.assertEqual(rs.redeem(self.db, "me", "4985")["error"], "already_redeemed")
        self.assertEqual(self.coins("them"), theirs)

    def test_your_own_code_is_refused(self):
        self.make_user("me", "1111", coins=5)
        res = rs.redeem(self.db, "me", "1111")
        self.assertEqual(res["error"], "own_code")
        self.assertEqual(self.coins("me"), 5)
        self.assertEqual(self.ledger(), {})

    def test_two_accounts_cannot_refund_each_other(self):
        """A referred B, so B's code paying A back is the same two people going
        round in a circle. One direction pays; the other does not."""
        self.make_user("a", "1111")
        self.make_user("b", "2222")
        self.assertTrue(rs.redeem(self.db, "b", "1111")["ok"])   # B joined via A
        a_coins = self.coins("a")
        b_coins = self.coins("b")

        back = rs.redeem(self.db, "a", "2222")                   # A now "joins" via B
        self.assertFalse(back["ok"])
        self.assertEqual(back["error"], "mutual_referral")
        self.assertEqual(self.coins("a"), a_coins)
        self.assertEqual(self.coins("b"), b_coins)

    def test_an_unrelated_third_account_is_unaffected_by_that_guard(self):
        self.make_user("a", "1111")
        self.make_user("b", "2222")
        self.make_user("c", "3333")
        rs.redeem(self.db, "b", "1111")
        # C using B's code is a genuine referral, not the A↔B circle.
        self.assertTrue(rs.redeem(self.db, "c", "2222")["ok"])

    def test_a_missing_referrer_or_account_is_refused(self):
        self.make_user("me", "1111")
        self.assertEqual(rs.redeem(self.db, "me", "9999")["error"], "no_user")
        self.make_user("them", "4985")
        self.assertEqual(rs.redeem(self.db, "ghost", "4985")["error"], "no_account")
        self.assertEqual(self.ledger(), {})

    def test_the_payout_leaves_the_rest_of_stats_alone(self):
        self.make_user("them", "4985")
        self.make_user("me", "1111", coins=2)
        self.db.collection("users")._docs["me"]["stats"]["total_xp"] = 5000
        rs.redeem(self.db, "me", "4985")
        self.assertEqual(self.user("me")["stats"]["total_xp"], 5000,
                         "the coin write clobbered the rest of the stats map")


# ══════════════════════════════════════════════════════════════════════════
#  THE SIGN-UP WINDOW
# ══════════════════════════════════════════════════════════════════════════
class Window(RefBase):
    def test_a_fresh_account_is_inside_the_window(self):
        self.make_user("them", "4985")
        self.make_user("me", "1111", age_days=0)
        self.assertTrue(rs.redeem(self.db, "me", "4985")["ok"])

    def test_an_old_account_cannot_collect_a_signup_bonus(self):
        self.make_user("them", "4985", coins=0)
        self.make_user("me", "1111", age_days=rs.window_days() + 1)
        res = rs.redeem(self.db, "me", "4985")
        self.assertEqual(res["error"], "window_closed")
        self.assertEqual(self.coins("me"), 0)
        self.assertEqual(self.coins("them"), 0)
        self.assertEqual(self.ledger(), {})

    def test_the_last_day_of_the_window_still_counts(self):
        self.make_user("them", "4985")
        self.make_user("me", "1111", age_days=rs.window_days() - 1)
        self.assertTrue(rs.redeem(self.db, "me", "4985")["ok"])

    def test_an_unreadable_created_at_fails_OPEN(self):
        """A timestamp we cannot parse must not cost a legitimate new player
        their coins — they would never find out why. Refusing on unknown is the
        worse failure here, so unknown means "in window"."""
        self.make_user("them", "4985")
        self.make_user("me", "1111")
        self.db.collection("users")._docs["me"]["created_at"] = "not a timestamp"
        self.assertTrue(rs.redeem(self.db, "me", "4985")["ok"])

    def test_a_missing_created_at_fails_open_too(self):
        self.make_user("them", "4985")
        self.make_user("me", "1111")
        del self.db.collection("users")._docs["me"]["created_at"]
        self.assertTrue(rs.redeem(self.db, "me", "4985")["ok"])

    def test_an_epoch_number_is_understood_in_seconds_or_millis(self):
        import time as _t
        self.make_user("them", "4985")
        self.make_user("me", "1111")
        old = _t.time() - (rs.window_days() + 5) * 86400
        self.db.collection("users")._docs["me"]["created_at"] = old
        self.assertEqual(rs.redeem(self.db, "me", "4985")["error"], "window_closed")
        self.db.collection("users")._docs["me"]["created_at"] = old * 1000
        self.assertEqual(rs.redeem(self.db, "me", "4985")["error"], "window_closed")


# ══════════════════════════════════════════════════════════════════════════
#  EVERY FIFTH FRIEND
# ══════════════════════════════════════════════════════════════════════════
class BackgroundMilestone(RefBase):
    def refer(self, referrer, code, friend_uid):
        self.make_user(friend_uid, "9" + friend_uid[-3:].rjust(3, "0"))
        return rs.redeem(self.db, friend_uid, code)

    def test_the_fifth_referral_grants_the_referrer_a_background(self):
        every = rs.background_every()
        self.make_user("them", "4985")
        results = [self.refer("them", "4985", f"f{i:03d}") for i in range(every)]
        for r in results[:-1]:
            self.assertTrue(r["ok"])
            self.assertEqual(r["backgroundGranted"], "", "a background came early")
        last = results[-1]
        self.assertTrue(last["ok"])
        self.assertEqual(last["backgroundGranted"], BACKGROUNDS[0])
        self.assertIn(BACKGROUNDS[0], self.user("them")["unlocked_backgrounds"])
        self.assertEqual(self.user("them")["referral_backgrounds"], 1)

    def test_the_friend_gets_coins_but_never_the_background(self):
        every = rs.background_every()
        self.make_user("them", "4985")
        for i in range(every):
            self.refer("them", "4985", f"f{i:03d}")
        fifth = self.user(f"f{every - 1:03d}")
        self.assertEqual(self.coins(f"f{every - 1:03d}"), rs.reward_coins())
        self.assertNotIn("unlocked_backgrounds", fifth,
                         "the referred friend was given a background too")

    def test_the_tenth_referral_grants_the_next_one(self):
        every = rs.background_every()
        self.make_user("them", "4985")
        for i in range(every * 2):
            self.refer("them", "4985", f"f{i:03d}")
        bgs = self.user("them")["unlocked_backgrounds"]
        self.assertEqual(len(bgs), 2, bgs)
        self.assertEqual(bgs, BACKGROUNDS[:2])
        self.assertEqual(self.user("them")["referral_backgrounds"], 2)

    def test_a_milestone_with_nothing_left_to_give_still_pays_the_coins(self):
        every = rs.background_every()
        self.make_user("them", "4985", unlocked_backgrounds=list(BACKGROUNDS))
        for i in range(every):
            self.refer("them", "4985", f"f{i:03d}")
        # The coins are the guaranteed half and they landed…
        self.assertEqual(self.coins("them"), rs.reward_coins() * every)
        # …and nothing claims a background that was never granted.
        self.assertNotIn("referral_backgrounds", self.user("them"))

    def test_the_background_grant_appends_and_never_rewrites_the_list(self):
        every = rs.background_every()
        self.make_user("them", "4985",
                       unlocked_backgrounds=["/backgrounds/CUSTOM-Legacy.PNG"])
        for i in range(every):
            self.refer("them", "4985", f"f{i:03d}")
        after = self.user("them")["unlocked_backgrounds"]
        self.assertIn("/backgrounds/CUSTOM-Legacy.PNG", after,
                      "the odd entry was deleted by the payout")
        self.assertIn(BACKGROUNDS[0], after)


# ══════════════════════════════════════════════════════════════════════════
#  STATE
# ══════════════════════════════════════════════════════════════════════════
class State(RefBase):
    def test_signed_out_gets_the_offer_and_no_account_data(self):
        out = rs.state_payload(None)
        self.assertTrue(out["ok"])
        self.assertFalse(out["signedIn"])
        self.assertEqual(out["coins"], rs.reward_coins())
        self.assertEqual(out["friendCode"], "")

    def test_zero_referrals_is_a_full_set_away_from_a_background(self):
        every = rs.background_every()
        self.make_user("them", "4985")
        out = rs.state_payload("them")
        self.assertEqual(out["referrals"], 0)
        self.assertEqual(out["toNextBackground"], every,
                         "0 referrals must read as a full set to go, not 0")

    def test_counts_down_to_the_next_background_and_resets_after_it(self):
        every = rs.background_every()
        self.make_user("them", "4985", referral_count=every - 1)
        self.assertEqual(rs.state_payload("them")["toNextBackground"], 1)
        self.db.collection("users")._docs["them"]["referral_count"] = every
        self.assertEqual(rs.state_payload("them")["toNextBackground"], every)

    def test_reports_whether_this_account_can_still_use_a_code(self):
        self.make_user("fresh", "1111", age_days=0)
        self.make_user("old", "2222", age_days=rs.window_days() + 3)
        self.assertTrue(rs.state_payload("fresh")["canRedeem"])
        self.assertFalse(rs.state_payload("old")["canRedeem"])

    def test_a_redeemed_account_says_who_it_joined_with(self):
        self.make_user("them", "4985", nickname="Reef Boss")
        self.make_user("me", "1111")
        rs.redeem(self.db, "me", "4985")
        out = rs.state_payload("me")
        self.assertTrue(out["redeemed"])
        self.assertEqual(out["redeemedFrom"], "Reef Boss")
        self.assertFalse(out["canRedeem"])


# ══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════
class Config(RefBase):
    def test_defaults_match_what_the_ui_promises(self):
        self.assertEqual(rs.reward_coins(), 100)
        self.assertEqual(rs.background_every(), 5)

    def test_env_overrides_are_read_and_clamped(self):
        os.environ["REFERRAL_REWARD_COINS"] = "250"
        self.assertEqual(rs.reward_coins(), 250)
        os.environ["REFERRAL_REWARD_COINS"] = "-5"
        self.assertEqual(rs.reward_coins(), 0)
        os.environ["REFERRAL_REWARD_COINS"] = "not a number"
        self.assertEqual(rs.reward_coins(), 100, "a bad value must fall back, not crash")


# ══════════════════════════════════════════════════════════════════════════
#  HTTP
# ══════════════════════════════════════════════════════════════════════════
class Http(RefBase):
    def post(self, path, body):
        h = FakeHandler()
        return rs.handle_post(h, Parsed(path), body), h

    def test_ignores_paths_that_are_not_ours(self):
        handled, _ = self.post("/api/pass/state", {})
        self.assertFalse(handled)

    def test_state_is_readable_signed_out(self):
        handled, h = self.post("/api/referral/state", {})
        self.assertTrue(handled)
        self.assertTrue(h.payload["ok"])
        self.assertFalse(h.payload["signedIn"])

    def test_redeem_needs_a_token(self):
        handled, h = self.post("/api/referral/redeem", {"code": "4985"})
        self.assertTrue(handled)
        self.assertEqual(h.status, 401)
        self.assertEqual(h.payload["error"], "unauthorized")
        self.assertTrue(h.payload["message"])

    def test_redeem_over_http_pays_both_sides(self):
        self.make_user("them", "4985")
        self.make_user("me", "1111")
        _, h = self.post("/api/referral/redeem",
                         {"idToken": "good:me", "code": "4985"})
        self.assertTrue(h.payload["ok"], h.payload)
        self.assertEqual(self.coins("me"), rs.reward_coins())
        self.assertEqual(self.coins("them"), rs.reward_coins())

    def test_every_refusal_carries_a_sentence_a_player_can_act_on(self):
        self.make_user("me", "1111")
        for code, expected in (("", "no_code"), ("hello", "bad_code"),
                               ("9999", "no_user"), ("1111", "own_code")):
            _, h = self.post("/api/referral/redeem",
                             {"idToken": "good:me", "code": code})
            self.assertEqual(h.payload["error"], expected, code)
            self.assertTrue(h.payload["message"])
            self.assertNotEqual(h.payload["message"],
                                rs.ERROR_MESSAGES["server_error"],
                                f"{expected} fell through to the generic message")

    def test_the_closed_window_message_quotes_the_real_number(self):
        self.make_user("them", "4985")
        self.make_user("me", "1111", age_days=rs.window_days() + 1)
        _, h = self.post("/api/referral/redeem",
                         {"idToken": "good:me", "code": "4985"})
        self.assertIn(str(rs.window_days()), h.payload["message"])

    def test_unknown_action_404s(self):
        _, h = self.post("/api/referral/teleport", {"idToken": "good:me"})
        self.assertEqual(h.status, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
