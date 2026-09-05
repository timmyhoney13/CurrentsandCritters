"""test_critter_pass_server.py, the Critter Pass, where it charges and pays.

The free Level Pass gives things away. This one takes 4,000 Critter Coins
first, so there is a whole failure mode the free pass does not have: charging
somebody and giving them nothing, or giving somebody the track without charging
them. The tests that matter are therefore:

  1. The track cannot be claimed without the entitlement.
                                          → _owns_pass, read INSIDE the payout
                                            transaction, off the account doc
  2. The purchase cannot happen twice, and cannot happen on an empty wallet.
                                          → the balance is re-read inside the
                                            transaction; the ledger create()
                                            settles a double tap
  3. A tier cannot pay twice, or at a level the account has not reached.
                                          → the ledger create() guard, and the
                                            PASS level re-derived from the
                                            account's OWN season XP (the
                                            request carries no level at all)
  4. A tier that CANNOT pay writes no ledger entry, so it stays claimable.
  5. The numbers Tim asked for are exactly the numbers on the track:
                                          → 4,000 in, 8,500 coins back, pinned
                                            as EQUALITIES, not ranges
  6. An XP drop that raises the level unlocks the tiers above it in the SAME
     "Claim all", because a player who pressed the button once should not have
     to press it four more times to collect what that press unlocked.

And, since the pass stopped riding the account's lifetime curve, the half that
makes it a SEASON pass (PassCurveTests / SeasonXpTests):

  7. A pass level is its own flat SEASON_XP_PER_LEVEL, and the whole climb is
     SEASON_DAYS of play once the track's own XP drops are counted. Pinned as
     arithmetic, because "doable in 30 days" is printed on the purchase card.
  8. The ACCOUNT level unlocks nothing. A level-100 account that buys the pass
     starts at pass level 1 and claims exactly one tier, which is what closes
     the "4,000 in, 8,500 straight back out" printer the old gate had.
  9. Except once: an owner from before this curve keeps the levels they were
     already sold, written in on first sight and never again.
 10. total_xp going DOWN (Prestige) must not wipe a paid-for climb, and must
     never make it negative.

Plus the drift checks that keep the three halves honest: the finale avatar has
to be a real avatar in preview-app.js, the extra-slot maxima have to match the
client's own clamp, and the tab has to actually be wired into Player Home.

    python3 test_critter_pass_server.py
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from datetime import timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import critter_pass_server as cp  # noqa: E402
import level_pass_server as lp  # noqa: E402

# The in-memory Firestore is the Level Pass's: set(merge=True) merges nested
# maps key-by-key and create() on an existing id RAISES, which is the entire
# "cannot happen twice" guarantee. Sharing it rather than writing a second one
# means both passes are proved against the same model of Firestore.
from test_level_pass_server import (  # noqa: E402
    ArrayUnion, FakeDb, LEVEL_TOTALS, MAX_LEVEL, level_progress, xp_for_level,
)

PREVIEW_JS = os.path.join(ROOT, "multiplayer", "client", "js", "preview-app.js")
PREVIEW_HTML = os.path.join(ROOT, "multiplayer", "client", "preview.html")
CRITTER_JS = os.path.join(ROOT, "multiplayer", "client", "js", "critter-pass.js")
CRITTER_CSS = os.path.join(ROOT, "multiplayer", "client", "css", "critter-pass.css")
SERVER_PY = os.path.join(ROOT, "multiplayer_server.py")
CLIENT_DIR = os.path.join(ROOT, "multiplayer", "client")

BACKGROUNDS = [
    "/backgrounds/bg-kelp.png",
    "/backgrounds/bg-coral-reef.png",
    "/backgrounds/bg-artificial-reef.png",
]


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class PassTestBase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        cp.init(
            get_firestore=lambda: self.db,
            verify_token=lambda tok: {"uid": tok[5:]} if tok.startswith("good:") else None,
            level_for_xp=level_progress,
            level_totals=LEVEL_TOTALS,
            background_paths=list(BACKGROUNDS),
        )
        # The two firestore helpers, swapped for the fake's equivalents.
        cp._transactional = lambda: (lambda fn: fn)          # type: ignore[assignment]
        cp._array_union = lambda: ArrayUnion                 # type: ignore[assignment]

    # ── Fixtures ──────────────────────────────────────────────────────────
    # A collection big enough to feed every emote tier. An emote is picked from
    # critters the account OWNS and has no emote for, so a fixture holding only
    # the starter Mullet would make nine of the ten emote tiers refuse, and the
    # "a maxed pass pays everything" test would be measuring the fixture rather
    # than the track. A real level-100 account has at least this many.
    COLLECTION = [f"/avatars/critter-{i}.png" for i in range(1, 15)]

    def make_user(self, uid="u1", level=1, coins=0, owns=False,
                  pass_level=None, season_record=True, **extra):
        """An account at ACCOUNT level `level`, optionally owning the pass.

        `pass_level` is the separate thing this track actually gates on, and it
        defaults to `level` only so the tests written before the pass had a
        curve of its own still mean what they meant. Pass the two different
        numbers to prove which one a payout really reads.

        `season_record=False` writes the entitlement WITHOUT the season record,
        which is exactly the shape of an account that bought the pass before
        this curve existed: the grandfather path."""
        total_xp = xp_for_level(level)
        doc = {
            "nickname": uid.upper(),
            "stats": {"total_xp": total_xp, "critter_coins": coins},
        }
        if owns:
            doc["critter_pass_seasons"] = [cp.SEASON_ID]
            if season_record:
                want = level if pass_level is None else pass_level
                doc[cp.SEASON_FIELD] = {cp.SEASON_ID: {
                    "xp": cp.season_xp_to_reach(want), "mark": total_xp}}
        doc.update(extra)
        self.db.collection("users")._docs[uid] = doc
        return doc

    def user(self, uid="u1"):
        return self.db.collection("users").document(uid).get().to_dict()

    def coins(self, uid="u1"):
        return int((self.user(uid).get("stats") or {}).get("critter_coins") or 0)

    def total_xp(self, uid="u1"):
        return int((self.user(uid).get("stats") or {}).get("total_xp") or 0)

    def season_xp(self, uid="u1"):
        return cp._season_xp(self.user(uid))

    def pass_level(self, uid="u1"):
        return cp._pass_level_of(self.user(uid))

    def set_season_xp(self, value, uid="u1"):
        """Move the pass climb without touching the account's XP, which is the
        only way to prove the two are really separate curves."""
        doc = self.db.collection("users")._docs[uid]
        doc[cp.SEASON_FIELD] = {cp.SEASON_ID: {
            "xp": int(value),
            "mark": int((doc.get("stats") or {}).get("total_xp") or 0),
        }}

    def ledger_ids(self):
        return sorted(self.db.collection("critter_pass_claims")._docs.keys())

    def purchase_ids(self):
        return sorted(self.db.collection("critter_pass_purchases")._docs.keys())

    def sweep(self, uid="u1", rounds=12):
        """Claim everything, the way the CLIENT does: claim_all pays at most
        CLAIM_ALL_LIMIT tiers per call and reports `more`, so a full sweep is a
        loop. Returns the merged result."""
        claimed, skipped, calls = [], {}, 0
        last = {}
        for _ in range(rounds):
            calls += 1
            last = cp.claim_all(self.db, uid)
            if not last.get("ok"):
                return last
            claimed.extend(last.get("claimed") or [])
            for sk in last.get("skipped") or []:
                skipped[sk["tier"]] = sk["error"]
            # A tier can refuse in one batch and pay in the next, so a payout
            # CANCELS an earlier refusal. claim_all already does this within one
            # call; a caller that loops has to do it across calls.
            for r in last.get("claimed") or []:
                skipped.pop(r["tier"], None)
            if not last.get("more") or not last.get("count"):
                break
        return {"ok": True, "claimed": claimed, "count": len(claimed),
                "skipped": [{"tier": k, "error": v} for k, v in sorted(skipped.items())],
                "calls": calls, "more": bool(last.get("more"))}

    def tier_of(self, rtype, level=None):
        """The lowest-level tier of a type, or the one at `level`.

        Tests ask for "a shield tier", not "the shield tier at level 6". The
        track's LEVELS are an economy dial and get retuned; the rules under test
        are not. Looking tiers up by type is what stops a rebalance showing up
        as a wall of failing tests about claiming and caps."""
        for t in sorted(cp.track(), key=lambda x: x["level"]):
            if t["type"] == rtype and (level is None or t["level"] == level):
                return t
        raise AssertionError(f"the track has no {rtype} tier"
                             + (f" at level {level}" if level else " at all"))


# ══════════════════════════════════════════════════════════════════════════
#  THE TRACK ITSELF
# ══════════════════════════════════════════════════════════════════════════
class TrackShapeTests(PassTestBase):
    def test_the_price_is_exactly_the_asking_price(self):
        # An EQUALITY, not a range: 4,000 is a number Tim asked for and the
        # purchase card prints it. A silent retune is a silent price change.
        self.assertEqual(cp.CRITTER_PASS_PRICE, 4000)

    def test_the_whole_track_pays_exactly_the_coin_budget(self):
        # The promise is "max it out and you get 8,500 Critter Coins back".
        # Equality, for the same reason the Level Pass pins its own 4,000.
        self.assertEqual(cp.coin_total(), 8500)
        self.assertEqual(cp.coin_total(), cp.TRACK_COIN_BUDGET)

    def test_the_track_pays_more_back_than_it_costs(self):
        self.assertGreater(cp.coin_total(), cp.CRITTER_PASS_PRICE)

    def test_the_whole_track_pays_exactly_the_xp_budget(self):
        self.assertEqual(cp.xp_total(), 23750)
        self.assertEqual(cp.xp_total(), cp.TRACK_XP_BUDGET)

    def test_the_xp_drops_were_raised_and_never_lowered(self):
        # The drops went 20/level to 25/level once. This is a floor, not an
        # equality: a retune may make the track pay MORE, but a player who
        # bought the pass on the printed 23,750 must never be paid less than
        # the page they bought from promised.
        self.assertGreaterEqual(cp.TRACK_XP_BUDGET, 23750)

    def test_every_single_level_pays_something(self):
        # THE shape of this track: 100 tiers over 100 levels, nothing skipped.
        # A level with no reward on a pass somebody paid 4,000 coins for is a
        # level that feels like nothing happened.
        levels = [t["level"] for t in cp.track()]
        self.assertEqual(len(levels), 100)
        self.assertEqual(sorted(levels), list(range(1, 101)))
        self.assertEqual(len(set(levels)), 100, "two tiers landed on one level")

    def test_the_xp_drop_is_the_formula_it_says_it_is(self):
        # 25 XP per level, every 5 levels. It is a formula rather than a table
        # so a drop is worth the same FRACTION of a level everywhere on the
        # curve, and so the 23,750 total falls out instead of being tuned. This
        # is the test that catches a raise applied to one tier by hand.
        for t in cp.track():
            if t["type"] == "xp":
                self.assertEqual(t["amount"], t["level"] * 25, t["id"])

    def test_the_served_track_agrees_with_the_budgets(self):
        # coin_total() reads the SPEC; the client reads track(). If those two
        # ever disagreed the page would sell a number it does not pay.
        served_coins = sum(t["amount"] for t in cp.track() if t["type"] == "coins")
        served_xp = sum(t["amount"] for t in cp.track() if t["type"] == "xp")
        self.assertEqual(served_coins, cp.TRACK_COIN_BUDGET)
        self.assertEqual(served_xp, cp.TRACK_XP_BUDGET)

    def test_tier_ids_are_unique(self):
        ids = [t["id"] for t in cp.track()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_tier_sits_on_a_real_level(self):
        for t in cp.track():
            self.assertGreaterEqual(t["level"], 1)
            self.assertLessEqual(t["level"], MAX_LEVEL)

    def test_the_track_asked_for_three_extra_dailies_and_three_weeklies(self):
        daily = [t for t in cp.track() if t["type"] == "daily_slot"]
        weekly = [t for t in cp.track() if t["type"] == "weekly_slot"]
        self.assertEqual(len(daily), 3)
        self.assertEqual(len(weekly), 3)
        # The tiers and the cap have to be the same number, or the last tier of
        # each pays nothing and sits there offering a Claim button forever.
        self.assertEqual(len(daily), cp.MAX_EXTRA_DAILY)
        self.assertEqual(len(weekly), cp.MAX_EXTRA_WEEKLY)
        # …and they must be spread out, not three in a row.
        self.assertEqual(len({t["level"] for t in daily}), 3)
        self.assertEqual(len({t["level"] for t in weekly}), 3)

    def test_there_are_emotes_all_along_the_track(self):
        emotes = sorted(t["level"] for t in cp.track() if t["type"] == "emote")
        self.assertGreaterEqual(len(emotes), 6)
        # One in the first quarter and one in the last: "along the pass", not
        # a cluster at one end of it.
        self.assertLess(min(emotes), 25)
        self.assertGreater(max(emotes), 75)

    def test_the_xp_drops_are_spread_over_the_track(self):
        xps = sorted(t["level"] for t in cp.track() if t["type"] == "xp")
        self.assertGreaterEqual(len(xps), 6)
        self.assertLess(min(xps), 25)
        self.assertGreater(max(xps), 75)
        # Every gap between drops is the same, which is what "every so many
        # levels you get an XP drop" means.
        gaps = {b - a for a, b in zip(xps, xps[1:])}
        self.assertEqual(len(gaps), 1)

    def test_the_coins_are_spread_over_the_track(self):
        coins = sorted(t["level"] for t in cp.track() if t["type"] == "coins")
        self.assertGreaterEqual(len(coins), 10)
        self.assertLess(min(coins), 10)
        self.assertGreaterEqual(max(coins), 99)

    def test_the_coin_ramp_never_goes_backwards(self):
        # Two coin tiers ten levels apart must not pay LESS the higher one is:
        # a track that gets stingier as it gets harder reads as a bug. Compared
        # decade to decade rather than tier to tier, because the three
        # milestone payouts deliberately spike above their neighbours.
        by_decade = {}
        for t in cp.track():
            if t["type"] != "coins":
                continue
            by_decade.setdefault((t["level"] - 1) // 10, []).append(t["amount"])
        floors = [min(v) for _, v in sorted(by_decade.items())]
        self.assertEqual(floors, sorted(floors), floors)

    def test_the_finale_is_the_avatar_at_level_100(self):
        avatars = [t for t in cp.track() if t["type"] == "avatar"]
        self.assertEqual(len(avatars), 1)
        self.assertEqual(avatars[0]["level"], 100)
        self.assertEqual(avatars[0]["img"], cp.FINALE_AVATAR)

    def test_every_tier_is_claimable(self):
        # Unlike the free pass there are no showcase-only tiers here: a paid
        # track with a tier you cannot claim is a tier somebody paid for and
        # cannot collect.
        for t in cp.track():
            self.assertTrue(t["claimable"], t["id"])

    def test_every_tier_has_a_label_and_a_blurb(self):
        for t in cp.track():
            self.assertTrue(t["label"].strip(), t["id"])
            self.assertTrue(t["blurb"].strip(), t["id"])
            self.assertTrue(t["icon"].strip(), t["id"])

    def test_no_consumable_has_more_tiers_than_the_hoard_can_hold(self):
        # A fourth shield tier against a three-shield cap is a tier that can
        # never be claimed in one sweep: the hoard is already full by the time
        # it comes up, so it refuses, and "Claim all" reports a skip on a maxed
        # pass forever. Caught by the maxed-pass test; stated here so the
        # REASON is written down next to the numbers.
        for rtype, cap in (("shield", cp.MAX_SHIELDS),
                           ("boost", cp.MAX_BOOSTS),
                           ("swap", cp.MAX_REROLLS)):
            n = len([t for t in cp.track() if t["type"] == rtype])
            self.assertLessEqual(n, cap, f"{n} {rtype} tiers against a cap of {cap}")

    def test_the_hoard_caps_are_the_free_passs_caps(self):
        # Both passes pay into the SAME streak_shields / xp_boosts /
        # weekly_reroll_tokens fields. A second copy of the caps here would let
        # the two disagree about what "full" means.
        self.assertEqual(cp.MAX_SHIELDS, lp.MAX_SHIELDS)
        self.assertEqual(cp.MAX_BOOSTS, lp.MAX_BOOSTS)
        self.assertEqual(cp.MAX_REROLLS, lp.MAX_REROLLS)
        self.assertEqual(cp.BOOST_PERCENT, lp.BOOST_PERCENT)
        self.assertEqual(cp.BOOST_HOURS, lp.BOOST_HOURS)

    def test_the_two_passes_do_not_share_a_ledger(self):
        self.assertNotEqual(cp._ledger(self.db).name, lp._ledger(self.db).name)


# ══════════════════════════════════════════════════════════════════════════
#  BUYING IT
# ══════════════════════════════════════════════════════════════════════════
class BuyTests(PassTestBase):
    def test_buying_charges_exactly_the_price_and_switches_the_track_on(self):
        self.make_user(coins=5000)
        res = cp.buy(self.db, "u1")
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.coins(), 5000 - cp.CRITTER_PASS_PRICE)
        self.assertIn(cp.SEASON_ID, self.user()["critter_pass_seasons"])
        self.assertEqual(self.purchase_ids(), [f"u1__{cp.SEASON_ID}"])

    def test_buying_at_exactly_the_price_works(self):
        self.make_user(coins=cp.CRITTER_PASS_PRICE)
        self.assertTrue(cp.buy(self.db, "u1").get("ok"))
        self.assertEqual(self.coins(), 0)

    def test_one_coin_short_is_refused_and_charges_nothing(self):
        self.make_user(coins=cp.CRITTER_PASS_PRICE - 1)
        res = cp.buy(self.db, "u1")
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("error"), "not_enough_coins")
        self.assertEqual(self.coins(), cp.CRITTER_PASS_PRICE - 1)
        self.assertEqual(self.purchase_ids(), [])
        self.assertNotIn("critter_pass_seasons", self.user())

    def test_buying_twice_charges_once(self):
        self.make_user(coins=cp.CRITTER_PASS_PRICE * 2)
        self.assertTrue(cp.buy(self.db, "u1").get("ok"))
        second = cp.buy(self.db, "u1")
        self.assertFalse(second.get("ok"))
        self.assertEqual(second.get("error"), "already_owned")
        self.assertEqual(self.coins(), cp.CRITTER_PASS_PRICE)

    def test_an_account_already_flagged_as_owning_is_never_charged_again(self):
        # The ledger is gone but the array says yes: believe the array. A
        # missing ledger row must never re-charge somebody who has the pass.
        self.make_user(coins=cp.CRITTER_PASS_PRICE, owns=True)
        res = cp.buy(self.db, "u1")
        self.assertEqual(res.get("error"), "already_owned")
        self.assertEqual(self.coins(), cp.CRITTER_PASS_PRICE)

    def test_buying_keeps_the_rest_of_stats(self):
        self.make_user(coins=5000, level=30)
        before_xp = self.total_xp()
        self.assertTrue(cp.buy(self.db, "u1").get("ok"))
        self.assertEqual(self.total_xp(), before_xp)

    # ── Season Pass vouchers ─────────────────────────────────────────────
    # The Supporter Tiers hand these out (SUPPORTER_TIER_GRANTS.pass_vouchers).
    # One redeems the pass for ONE season, whichever season the holder spends it
    # on, and it must never cost coins as well.
    def test_redeeming_a_voucher_costs_no_coins_and_switches_the_track_on(self):
        self.make_user(coins=0, critter_pass_vouchers=1)
        res = cp.buy(self.db, "u1", use_voucher=True)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res.get("paid"), 0)
        self.assertEqual(res.get("paidWith"), "voucher")
        self.assertEqual(self.coins(), 0)
        self.assertEqual(self.user()["critter_pass_vouchers"], 0)
        self.assertIn(cp.SEASON_ID, self.user()["critter_pass_seasons"])

    def test_redeeming_spends_exactly_one_voucher(self):
        self.make_user(coins=0, critter_pass_vouchers=5)
        self.assertTrue(cp.buy(self.db, "u1", use_voucher=True).get("ok"))
        self.assertEqual(self.user()["critter_pass_vouchers"], 4)

    def test_redeeming_with_no_voucher_is_refused_and_spends_nothing(self):
        self.make_user(coins=cp.CRITTER_PASS_PRICE)
        res = cp.buy(self.db, "u1", use_voucher=True)
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("error"), "no_vouchers")
        # Crucially it does NOT silently fall back to charging coins.
        self.assertEqual(self.coins(), cp.CRITTER_PASS_PRICE)
        self.assertEqual(self.purchase_ids(), [])
        self.assertNotIn("critter_pass_seasons", self.user())

    def test_a_voucher_and_coins_cannot_both_buy_the_same_season(self):
        self.make_user(coins=cp.CRITTER_PASS_PRICE, critter_pass_vouchers=2)
        self.assertTrue(cp.buy(self.db, "u1", use_voucher=True).get("ok"))
        second = cp.buy(self.db, "u1")
        self.assertEqual(second.get("error"), "already_owned")
        self.assertEqual(self.coins(), cp.CRITTER_PASS_PRICE)
        self.assertEqual(self.user()["critter_pass_vouchers"], 1)

    def test_paying_coins_never_touches_a_held_voucher(self):
        """A voucher is worth a whole season: buying with coins must leave it
        for the season its holder actually wants it for."""
        self.make_user(coins=cp.CRITTER_PASS_PRICE, critter_pass_vouchers=3)
        self.assertTrue(cp.buy(self.db, "u1").get("ok"))
        self.assertEqual(self.user()["critter_pass_vouchers"], 3)

    def test_the_ledger_records_which_way_it_was_paid(self):
        self.make_user(coins=0, critter_pass_vouchers=1)
        cp.buy(self.db, "u1", use_voucher=True)
        rec = self.db.collection("critter_pass_purchases")._docs[f"u1__{cp.SEASON_ID}"]
        self.assertEqual(rec["paid_with"], "voucher")
        self.assertEqual(rec["price"], 0)
        self.assertEqual(rec["vouchers_before"] - rec["vouchers_after"], 1)

    def test_the_voucher_count_reaches_the_page(self):
        self.make_user(coins=0, critter_pass_vouchers=4)
        inv = cp.state_payload("u1")["inventory"]
        self.assertEqual(inv["vouchers"], 4)

    def test_a_signed_out_payload_reports_no_vouchers(self):
        self.assertEqual(cp.state_payload(None)["inventory"]["vouchers"], 0)

    def test_a_junk_voucher_count_reads_as_none(self):
        for junk in (None, "", "three", -2, {}):
            self.make_user(coins=0, critter_pass_vouchers=junk)
            self.assertEqual(cp.state_payload("u1")["inventory"]["vouchers"], 0, junk)
            self.assertEqual(cp.buy(self.db, "u1", use_voucher=True).get("error"),
                             "no_vouchers", junk)

    def test_no_account_cannot_buy(self):
        res = cp.buy(self.db, "ghost")
        self.assertEqual(res.get("error"), "no_account")

    def test_the_purchase_ledger_records_the_price_it_charged(self):
        self.make_user(coins=6000)
        cp.buy(self.db, "u1")
        rec = self.db.collection("critter_pass_purchases")._docs[f"u1__{cp.SEASON_ID}"]
        self.assertEqual(rec["price"], cp.CRITTER_PASS_PRICE)
        self.assertEqual(rec["coins_before"] - rec["coins_after"], cp.CRITTER_PASS_PRICE)
        self.assertEqual(rec["season"], cp.SEASON_ID)


# ══════════════════════════════════════════════════════════════════════════
#  THE ENTITLEMENT
# ══════════════════════════════════════════════════════════════════════════
class EntitlementTests(PassTestBase):
    def test_a_non_owner_at_level_100_can_claim_nothing(self):
        self.make_user(level=100, coins=0, owns=False)
        for t in cp.track():
            res = cp.claim(self.db, "u1", t["id"])
            self.assertFalse(res.get("ok"), t["id"])
            self.assertEqual(res.get("error"), "not_owned", t["id"])
        self.assertEqual(self.coins(), 0)
        self.assertEqual(self.ledger_ids(), [])

    def test_claim_all_refuses_a_non_owner(self):
        self.make_user(level=100, owns=False)
        res = cp.claim_all(self.db, "u1")
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("error"), "not_owned")
        self.assertEqual(self.ledger_ids(), [])

    def test_owning_a_DIFFERENT_season_is_not_owning_this_one(self):
        self.make_user(level=100, owns=False, critter_pass_seasons=["S0", "S99"])
        tier = self.tier_of("coins")
        self.assertEqual(cp.claim(self.db, "u1", tier["id"]).get("error"), "not_owned")

    def test_a_junk_entitlement_field_is_not_ownership(self):
        for junk in ("S1", {"S1": True}, 1, None):
            with self.subTest(junk=junk):
                self.db.collection("users")._docs.pop("u1", None)
                self.make_user(level=100, owns=False, critter_pass_seasons=junk)
                tier = self.tier_of("coins")
                self.assertEqual(cp.claim(self.db, "u1", tier["id"]).get("error"), "not_owned")

    def test_buying_then_claiming_works(self):
        self.make_user(level=100, coins=cp.CRITTER_PASS_PRICE)
        self.assertTrue(cp.buy(self.db, "u1").get("ok"))
        tier = self.tier_of("coins")
        res = cp.claim(self.db, "u1", tier["id"])
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.coins(), tier["amount"])


# ══════════════════════════════════════════════════════════════════════════
#  CLAIMING
# ══════════════════════════════════════════════════════════════════════════
class ClaimTests(PassTestBase):
    def test_the_level_comes_off_the_account_not_the_request(self):
        # The request carries no level at all. Change ONLY the stored XP and
        # replay the same call: that is the whole proof.
        tier = self.tier_of("coins", level=99)
        self.make_user(level=98, owns=True)
        first = cp.claim(self.db, "u1", tier["id"])
        self.assertEqual(first.get("error"), "level_locked")
        self.assertEqual(first.get("need"), 99)

        self.user()  # untouched
        self.db.collection("users")._docs["u1"]["stats"]["total_xp"] = xp_for_level(99)
        second = cp.claim(self.db, "u1", tier["id"])
        self.assertTrue(second.get("ok"), second)

    def test_a_tier_pays_once(self):
        tier = self.tier_of("coins")
        self.make_user(level=100, owns=True)
        self.assertTrue(cp.claim(self.db, "u1", tier["id"]).get("ok"))
        again = cp.claim(self.db, "u1", tier["id"])
        self.assertEqual(again.get("error"), "already_claimed")
        self.assertEqual(self.coins(), tier["amount"])
        self.assertEqual(len(self.ledger_ids()), 1)

    def test_the_ledger_id_carries_the_season(self):
        tier = self.tier_of("coins")
        self.make_user(level=100, owns=True)
        cp.claim(self.db, "u1", tier["id"])
        self.assertEqual(self.ledger_ids(), [f"u1__{cp.SEASON_ID}_{tier['id']}"])

    def test_an_unknown_tier_is_refused(self):
        self.make_user(level=100, owns=True)
        for bad in ("", "L999", "../../etc", "L4; drop"):
            self.assertEqual(cp.claim(self.db, "u1", bad).get("error"), "unknown_tier")

    def test_an_xp_drop_moves_total_xp_and_every_derived_level_field(self):
        tier = self.tier_of("xp")
        self.make_user(level=100, owns=True)
        before = self.total_xp()
        res = cp.claim(self.db, "u1", tier["id"])
        self.assertTrue(res.get("ok"), res)
        stats = self.user()["stats"]
        self.assertEqual(stats["total_xp"], before + tier["amount"])
        lvl, cur, goal = level_progress(stats["total_xp"])
        # Every field the leaderboard and the header read, in lock-step, so the
        # new level shows immediately and not after the player's next game.
        for key, want in (("level", lvl), ("player_level", lvl),
                          ("xp_current", cur), ("level_xp_current", cur),
                          ("xp_goal", goal), ("level_xp_goal", goal)):
            self.assertEqual(stats[key], want, key)

    def test_an_xp_drop_does_not_clobber_the_coin_balance(self):
        tier = self.tier_of("xp")
        self.make_user(level=100, coins=777, owns=True)
        self.assertTrue(cp.claim(self.db, "u1", tier["id"]).get("ok"))
        self.assertEqual(self.coins(), 777)

    def test_a_coin_tier_does_not_clobber_total_xp(self):
        tier = self.tier_of("coins")
        self.make_user(level=100, owns=True)
        before = self.total_xp()
        self.assertTrue(cp.claim(self.db, "u1", tier["id"]).get("ok"))
        self.assertEqual(self.total_xp(), before)

    def test_a_daily_slot_tier_raises_the_counter_the_client_mirrors(self):
        self.make_user(level=100, owns=True)
        tiers = [t for t in cp.track() if t["type"] == "daily_slot"]
        for i, t in enumerate(tiers, start=1):
            self.assertTrue(cp.claim(self.db, "u1", t["id"]).get("ok"), t["id"])
            self.assertEqual(self.user().get("bonus_daily_slots"), i)
        self.assertEqual(self.user()["bonus_daily_slots"], cp.MAX_EXTRA_DAILY)

    def test_a_weekly_slot_tier_raises_its_own_counter(self):
        self.make_user(level=100, owns=True)
        tiers = [t for t in cp.track() if t["type"] == "weekly_slot"]
        for i, t in enumerate(tiers, start=1):
            self.assertTrue(cp.claim(self.db, "u1", t["id"]).get("ok"), t["id"])
            self.assertEqual(self.user().get("bonus_weekly_slots"), i)
        # …and the daily counter is untouched by a weekly tier.
        self.assertIsNone(self.user().get("bonus_daily_slots"))

    def test_a_slot_counter_that_is_already_full_refuses_without_a_ledger_entry(self):
        self.make_user(level=100, owns=True, bonus_daily_slots=cp.MAX_EXTRA_DAILY)
        tier = self.tier_of("daily_slot")
        res = cp.claim(self.db, "u1", tier["id"])
        self.assertEqual(res.get("error"), "daily_slots_full")
        self.assertEqual(self.ledger_ids(), [])

    def test_the_finale_avatar_lands_in_unlocked_icons(self):
        self.make_user(level=100, owns=True)
        tier = self.tier_of("avatar")
        res = cp.claim(self.db, "u1", tier["id"])
        self.assertTrue(res.get("ok"), res)
        self.assertIn(cp.FINALE_AVATAR, self.user()["unlocked_icons"])

    def test_the_finale_avatar_appends_and_never_replaces(self):
        # ArrayUnion, not read-modify-write: an account that already owns a
        # dozen critters must not lose them to a one-item list.
        self.make_user(level=100, owns=True,
                       unlocked_icons=["/avatars/narwhal.png", "/avatars/orca.png"])
        cp.claim(self.db, "u1", self.tier_of("avatar")["id"])
        icons = self.user()["unlocked_icons"]
        self.assertIn("/avatars/narwhal.png", icons)
        self.assertIn("/avatars/orca.png", icons)
        self.assertIn(cp.FINALE_AVATAR, icons)

    def test_the_finale_avatar_is_not_duplicated_for_somebody_who_bought_it(self):
        # It is also a 2,000-coin Store skin, so this is the common case, not
        # an edge one. The claim still succeeds: a tier that can never be
        # marked done is a Claim button that never goes away.
        self.make_user(level=100, owns=True, unlocked_icons=[cp.FINALE_AVATAR])
        res = cp.claim(self.db, "u1", self.tier_of("avatar")["id"])
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.user()["unlocked_icons"].count(cp.FINALE_AVATAR), 1)

    def test_an_emote_is_picked_from_critters_the_account_owns(self):
        self.make_user(level=100, owns=True,
                       unlocked_icons=["/avatars/narwhal.png"], emote_icons=[])
        res = cp.claim(self.db, "u1", self.tier_of("emote")["id"])
        self.assertTrue(res.get("ok"), res)
        self.assertIn(res["granted"]["path"], ("/avatars/mullet.png", "/avatars/narwhal.png"))
        self.assertIn(res["granted"]["path"], self.user()["emote_icons"])

    def test_an_emote_tier_with_nothing_left_to_give_stays_claimable(self):
        self.make_user(level=100, owns=True,
                       unlocked_icons=["/avatars/narwhal.png"],
                       emote_icons=["/avatars/mullet.png", "/avatars/narwhal.png"])
        res = cp.claim(self.db, "u1", self.tier_of("emote")["id"])
        self.assertEqual(res.get("error"), "emotes_full")
        self.assertEqual(self.ledger_ids(), [])

    def test_a_background_tier_with_nothing_left_to_give_stays_claimable(self):
        self.make_user(level=100, owns=True, unlocked_backgrounds=list(BACKGROUNDS))
        res = cp.claim(self.db, "u1", self.tier_of("background")["id"])
        self.assertEqual(res.get("error"), "backgrounds_full")
        self.assertEqual(self.ledger_ids(), [])

    def test_a_full_shield_hoard_refuses_without_a_ledger_entry(self):
        self.make_user(level=100, owns=True, streak_shields=cp.MAX_SHIELDS)
        res = cp.claim(self.db, "u1", self.tier_of("shield")["id"])
        self.assertEqual(res.get("error"), "shields_full")
        self.assertEqual(self.ledger_ids(), [])

    def test_a_full_boost_hoard_refuses_without_a_ledger_entry(self):
        self.make_user(level=100, owns=True, xp_boosts=cp.MAX_BOOSTS)
        res = cp.claim(self.db, "u1", self.tier_of("boost")["id"])
        self.assertEqual(res.get("error"), "boosts_full")
        self.assertEqual(self.ledger_ids(), [])

    def test_a_full_swap_hoard_refuses_without_a_ledger_entry(self):
        self.make_user(level=100, owns=True, weekly_reroll_tokens=cp.MAX_REROLLS)
        res = cp.claim(self.db, "u1", self.tier_of("swap")["id"])
        self.assertEqual(res.get("error"), "rerolls_full")
        self.assertEqual(self.ledger_ids(), [])

    def test_every_refusal_has_a_sentence(self):
        seen = set()
        for t in cp.track():
            seen.add(t["type"])
        # Every error the module can return has to be answerable in English:
        # a player who is refused and told nothing assumes it is broken.
        for code in ("not_owned", "already_owned", "not_enough_coins",
                     "level_locked", "already_claimed", "daily_slots_full",
                     "weekly_slots_full", "emotes_full", "backgrounds_full",
                     "shields_full", "boosts_full", "rerolls_full",
                     "unknown_tier", "no_account", "bad_request", "server_error"):
            self.assertIn(code, cp.ERROR_MESSAGES, code)
            self.assertTrue(cp.ERROR_MESSAGES[code].strip())
        self.assertTrue(seen)


# ══════════════════════════════════════════════════════════════════════════
#  CLAIM ALL
# ══════════════════════════════════════════════════════════════════════════
class ClaimAllTests(PassTestBase):
    def test_a_maxed_pass_pays_exactly_the_advertised_coins_and_xp(self):
        # THE promise on the page: unlock it, hit 100, get 8,500 coins back.
        # Nothing is allowed to be skipped here, which is why the consumable
        # tiers are counted against the hoard caps: a fourth shield tier would
        # make this test fail forever, and correctly so.
        self.make_user(level=100, coins=0, owns=True,
                       unlocked_icons=list(self.COLLECTION))
        start_xp = self.total_xp()
        res = self.sweep()
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res.get("skipped"), [], res.get("skipped"))
        self.assertEqual(self.coins(), cp.TRACK_COIN_BUDGET)
        self.assertEqual(self.total_xp(), start_xp + cp.TRACK_XP_BUDGET)
        # …and every tier, once each.
        self.assertEqual(res["count"], len(cp.track()))
        self.assertEqual(len(self.ledger_ids()), len(cp.track()))

    def test_a_maxed_pass_hands_over_every_perk_too(self):
        self.make_user(level=100, coins=0, owns=True,
                       unlocked_icons=list(self.COLLECTION))
        self.sweep()
        doc = self.user()
        self.assertEqual(doc.get("bonus_daily_slots"), cp.MAX_EXTRA_DAILY)
        self.assertEqual(doc.get("bonus_weekly_slots"), cp.MAX_EXTRA_WEEKLY)
        self.assertIn(cp.FINALE_AVATAR, doc.get("unlocked_icons") or [])

    def test_claim_all_twice_pays_once(self):
        self.make_user(level=100, coins=0, owns=True,
                       unlocked_icons=list(self.COLLECTION))
        self.sweep()
        second = self.sweep()
        self.assertEqual(second["count"], 0)
        self.assertEqual(self.coins(), cp.TRACK_COIN_BUDGET)

    def test_claim_all_never_pays_a_tier_above_the_level(self):
        self.make_user(level=20, coins=0, owns=True)
        self.sweep()
        paid_levels = [rec["level"] for rec in
                       self.db.collection("critter_pass_claims")._docs.values()]
        # An XP drop can RAISE the PASS level mid-sweep, which is the point of
        # the outer loop, so the bound is the level at the END, not the start.
        # Measured on the pass curve, deliberately: the account curve would let
        # this pass while the sweep was paying tiers nobody had reached.
        self.assertTrue(paid_levels)
        self.assertLessEqual(max(paid_levels), self.pass_level())

    def test_an_xp_drop_unlocks_the_next_tiers_in_the_same_sweep(self):
        # Pass level 10 with 100 XP to spare before 11. The level-10 XP drop is
        # 750, more than a pass level costs, so claiming it crosses the
        # boundary: a single-pass sweep would stop at 10 and leave the level-11
        # tier sitting there.
        self.make_user(level=1, coins=0, owns=True)
        self.set_season_xp(cp.season_xp_to_reach(11) - 100)
        self.assertEqual(self.pass_level(), 10)

        res = self.sweep()
        self.assertTrue(res.get("ok"), res)
        end_level = self.pass_level()
        self.assertGreaterEqual(end_level, 11)
        paid = {rec["tier"] for rec in
                self.db.collection("critter_pass_claims")._docs.values()}
        for t in cp.track():
            if t["level"] <= end_level:
                self.assertIn(t["id"], paid, f"{t['id']} was unlocked and left behind")

    def test_a_refusing_tier_is_reported_and_the_rest_still_pay(self):
        self.make_user(level=100, coins=0, owns=True,
                       unlocked_icons=list(self.COLLECTION),
                       unlocked_backgrounds=list(BACKGROUNDS))
        res = self.sweep()
        self.assertTrue(res.get("ok"))
        errs = {s["error"] for s in res["skipped"]}
        self.assertIn("backgrounds_full", errs)
        # The coins still landed in full: a tier that cannot pay stops itself,
        # it does not roll back the ones that could.
        self.assertEqual(self.coins(), cp.TRACK_COIN_BUDGET)

    def test_a_tier_that_refuses_then_succeeds_is_not_reported_as_both(self):
        # The shield hoard is full at the start; the first shield tier refuses.
        # Nothing in claim_all can free a shield, so it stays skipped: what is
        # being pinned is that a tier is never in BOTH lists.
        self.make_user(level=100, coins=0, owns=True, streak_shields=cp.MAX_SHIELDS)
        res = self.sweep()
        paid = {r["tier"] for r in res["claimed"]}
        skipped = {s["tier"] for s in res["skipped"]}
        self.assertFalse(paid & skipped)

    def test_claim_all_terminates_on_a_level_1_account(self):
        self.make_user(level=1, coins=0, owns=True)
        res = self.sweep()
        self.assertTrue(res.get("ok"))
        # Level 1 has exactly one tier on it.
        self.assertGreaterEqual(res["count"], 1)

    # ── The per-request cap ───────────────────────────────────────────────
    # Each tier is its own Firestore transaction, so a hundred of them in one
    # request is a request that can outlive its own timeout: the server pays a
    # bounded batch and says there is more, and the client loops.
    def test_one_request_pays_no_more_than_the_limit(self):
        self.make_user(level=100, coins=0, owns=True,
                       unlocked_icons=list(self.COLLECTION))
        first = cp.claim_all(self.db, "u1")
        self.assertTrue(first.get("ok"))
        self.assertLessEqual(first["count"], cp.CLAIM_ALL_LIMIT)
        self.assertTrue(first.get("more"), "a 100-tier track did not report more")

    def test_looping_the_way_the_client_does_still_pays_the_whole_track(self):
        self.make_user(level=100, coins=0, owns=True,
                       unlocked_icons=list(self.COLLECTION))
        res = self.sweep()
        self.assertEqual(res["count"], len(cp.track()))
        self.assertEqual(self.coins(), cp.TRACK_COIN_BUDGET)
        self.assertFalse(res["more"], "the last call still claimed there was more")

    def test_the_client_loop_is_long_enough_for_the_whole_track(self):
        # The client gives up after CLAIM_ALL_ROUNDS. If the track ever grew
        # past what that many bounded batches can pay, "Claim all" would stop
        # part way and look like it had finished.
        js = _read(CRITTER_JS)
        m = re.search(r"const CLAIM_ALL_ROUNDS = (\d+);", js)
        self.assertIsNotNone(m, "CLAIM_ALL_ROUNDS vanished from critter-pass.js")
        self.assertGreaterEqual(int(m.group(1)) * cp.CLAIM_ALL_LIMIT, len(cp.track()))

    def test_a_bounded_batch_still_never_pays_a_tier_twice(self):
        self.make_user(level=100, coins=0, owns=True,
                       unlocked_icons=list(self.COLLECTION))
        res = self.sweep()
        tiers = [r["tier"] for r in res["claimed"]]
        self.assertEqual(len(tiers), len(set(tiers)))


# ══════════════════════════════════════════════════════════════════════════
#  STATE PAYLOAD
# ══════════════════════════════════════════════════════════════════════════
class PassCurveTests(PassTestBase):
    """The pass level is its OWN curve, and it is tuned to thirty days.

    This is the half that makes the Critter Pass a season pass rather than a
    second view of the account's lifetime level, so the tests are about the two
    curves being genuinely different, not about the numbers being pretty."""

    def test_the_pass_is_a_thirty_day_climb(self):
        # THE promise on the page, as arithmetic. The track pays part of its own
        # climb back through XP drops, so what a player has to go and earn is
        # the difference, and that divided by the season is the daily rate the
        # purchase card quotes. An equality, not a range: if a retune moves any
        # of the three numbers this has to be looked at on purpose.
        self.assertEqual(cp.SEASON_XP_TO_MAX,
                         cp.SEASON_XP_PER_LEVEL * (cp.PASS_MAX_LEVEL - 1))
        self.assertEqual(cp.SEASON_XP_TO_EARN,
                         cp.SEASON_XP_TO_MAX - cp.TRACK_XP_BUDGET)
        self.assertEqual(cp.SEASON_XP_PER_DAY,
                         -(-cp.SEASON_XP_TO_EARN // cp.SEASON_DAYS))
        # The promise has to actually hold: a player who earns exactly the
        # quoted rate every day for the whole season, and claims the track's
        # own XP drops on the way, has to REACH Level 100 inside SEASON_DAYS.
        # Floored division fails this by twenty XP and one day.
        self.assertGreaterEqual(
            cp.SEASON_XP_PER_DAY * cp.SEASON_DAYS + cp.TRACK_XP_BUDGET,
            cp.SEASON_XP_TO_MAX)
        # And the rate is a day's play, not a job. The lifetime curve was tuned
        # at ~1,175 XP a day (250,000 over about seven months); asking much more
        # than that for thirty days would make "doable in 30 days" a lie.
        self.assertLessEqual(cp.SEASON_XP_PER_DAY, 1500)
        self.assertGreaterEqual(cp.SEASON_XP_PER_DAY, 800)

    def test_the_pass_curve_is_far_cheaper_than_the_account_curve(self):
        # The whole point of the change. Reaching Level 100 on the account curve
        # is the long game; reaching it on the pass is a season.
        self.assertLess(cp.SEASON_XP_TO_MAX, LEVEL_TOTALS[MAX_LEVEL - 1] // 3)

    def test_every_pass_level_costs_the_same(self):
        # Flat, on purpose: "600 XP a level" is a promise a player can hold in
        # their head, and it is what makes the header bar mean the same thing at
        # level 9 and at level 90.
        costs = {cp.season_xp_to_reach(n + 1) - cp.season_xp_to_reach(n)
                 for n in range(1, cp.PASS_MAX_LEVEL)}
        self.assertEqual(costs, {cp.SEASON_XP_PER_LEVEL})

    def test_the_track_ends_exactly_where_the_curve_does(self):
        # A tier above PASS_MAX_LEVEL could never be claimed, and a curve that
        # ran past the last tier would leave the bar climbing towards nothing.
        self.assertEqual(cp.max_level(), cp.PASS_MAX_LEVEL)

    def test_progress_boundaries(self):
        self.assertEqual(cp.season_progress(0), (1, 0, cp.SEASON_XP_PER_LEVEL))
        self.assertEqual(cp.season_progress(cp.SEASON_XP_PER_LEVEL - 1)[0], 1)
        self.assertEqual(cp.season_progress(cp.SEASON_XP_PER_LEVEL)[0], 2)
        self.assertEqual(cp.season_progress(cp.SEASON_XP_TO_MAX)[0], cp.PASS_MAX_LEVEL)
        # Past the cap the bar reads FULL, never empty: (goal, goal), not
        # (0, goal), or Level 100 would paint as 0% done.
        lvl, into, goal = cp.season_progress(cp.SEASON_XP_TO_MAX * 10)
        self.assertEqual((lvl, into), (cp.PASS_MAX_LEVEL, goal))
        # Junk in is level 1, never a crash and never a free track.
        self.assertEqual(cp.season_progress(None)[0], 1)
        self.assertEqual(cp.season_progress(-5000)[0], 1)

    def test_reaching_a_level_is_clamped_at_both_ends(self):
        self.assertEqual(cp.season_xp_to_reach(1), 0)
        self.assertEqual(cp.season_xp_to_reach(0), 0)
        self.assertEqual(cp.season_xp_to_reach(10 ** 6), cp.SEASON_XP_TO_MAX)


class SeasonXpTests(PassTestBase):
    """Season XP: the delta, the fold, and the reset that must not wipe it."""

    def test_buying_starts_the_climb_at_zero_even_at_account_level_100(self):
        # THE reason the pass has a curve of its own. Keyed on lifetime level, a
        # maxed account would buy the track for 4,000 and take 8,500 straight
        # back out; that is a coin printer, and it is what stopped the pass ever
        # being re-buyable. Keyed on season XP, everybody starts at 1.
        self.make_user(level=MAX_LEVEL, coins=cp.CRITTER_PASS_PRICE)
        res = cp.buy(self.db, "u1")
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res["passLevel"], 1)
        self.assertEqual(self.pass_level(), 1)
        self.assertEqual(self.season_xp(), 0)
        # And nothing above level 1 pays out.
        sweep = cp.claim_all(self.db, "u1")
        self.assertTrue(sweep.get("ok"), sweep)
        paid = {rec["level"] for rec in
                self.db.collection("critter_pass_claims")._docs.values()}
        self.assertEqual(paid, {1})

    def test_a_voucher_starts_the_climb_at_zero_too(self):
        self.make_user(level=MAX_LEVEL, coins=0, critter_pass_vouchers=2)
        res = cp.buy(self.db, "u1", use_voucher=True)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.pass_level(), 1)
        self.assertEqual(self.season_xp(), 0)

    def test_a_tier_is_gated_on_the_pass_level_not_the_account_level(self):
        # The two are moved in opposite directions on purpose: an account level
        # nowhere near the tier, a pass level past it. Only one of them may
        # decide, and it is not the account.
        self.make_user(level=2, coins=0, owns=True, pass_level=40)
        res = cp.claim(self.db, "u1", "L31")
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res["level"], 31)

    def test_a_high_account_level_does_not_unlock_a_tier(self):
        self.make_user(level=MAX_LEVEL, coins=0, owns=True, pass_level=3)
        res = cp.claim(self.db, "u1", "L31")
        self.assertFalse(res.get("ok"))
        self.assertEqual(res["error"], "level_locked")
        self.assertEqual(res["level"], 3)

    def test_earned_xp_moves_the_pass_because_it_is_a_delta(self):
        # No writes to the season record at all: playing raises total_xp, and
        # season XP is the difference since the mark. This is what let the
        # feature ship without touching a single XP award path.
        self.make_user(level=1, coins=0, owns=True, pass_level=1)
        self.assertEqual(self.pass_level(), 1)
        doc = self.db.collection("users")._docs["u1"]
        doc["stats"]["total_xp"] += cp.SEASON_XP_PER_LEVEL * 4
        self.assertEqual(self.pass_level(), 5)
        self.assertEqual(self.season_xp(), cp.SEASON_XP_PER_LEVEL * 4)

    def test_an_xp_drop_counts_towards_the_pass(self):
        # A drop is real XP on stats.total_xp, so it moves the season as well as
        # the account. The 30-day arithmetic assumes exactly this.
        self.make_user(level=1, coins=0, owns=True, pass_level=5)
        before = self.season_xp()
        res = cp.claim(self.db, "u1", "L5")           # 125 XP
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.season_xp(), before + 125)

    def test_the_claim_ledger_records_the_pass_level_it_paid_at(self):
        self.make_user(level=2, coins=0, owns=True, pass_level=20)
        cp.claim(self.db, "u1", "L11")
        rec = list(self.db.collection("critter_pass_claims")._docs.values())[0]
        self.assertEqual(rec["pass_level"], 20)
        self.assertEqual(rec["season_xp"], cp.season_xp_to_reach(20))

    # ── the grandfather ───────────────────────────────────────────────────
    def test_an_owner_from_before_the_curve_keeps_what_they_were_sold(self):
        # The pass shipped gated on lifetime level, so anybody who bought it
        # then paid 4,000 coins for "every level you have already earned
        # counts". Their record is missing, and they must NOT be reset to 1.
        self.make_user(level=47, coins=0, owns=True, season_record=False)
        self.assertEqual(self.pass_level(), 47)
        self.assertTrue(cp._needs_fold(self.user()))

    def test_the_grandfather_is_written_once_and_then_behaves_normally(self):
        self.make_user(level=47, coins=0, owns=True, season_record=False)
        state = cp.state_payload("u1")
        self.assertEqual(state["passLevel"], 47)
        self.assertFalse(cp._needs_fold(self.user()))
        # From here it is an ordinary delta: account XP no longer moves the pass
        # by the account curve, it moves it by the pass curve.
        doc = self.db.collection("users")._docs["u1"]
        doc["stats"]["total_xp"] += cp.SEASON_XP_PER_LEVEL * 2
        self.assertEqual(self.pass_level(), 49)

    def test_a_non_owner_is_never_grandfathered(self):
        # The locked track is a sales pitch, and it has to show what a new buyer
        # would really start from. A level-100 account that has not bought the
        # pass is looking at pass level 1.
        self.make_user(level=MAX_LEVEL, coins=0)
        self.assertEqual(cp.state_payload("u1")["passLevel"], 1)
        self.assertFalse(cp._needs_fold(self.user()))

    # ── total_xp going DOWN (Prestige, an admin correction) ───────────────
    def test_a_reset_total_xp_never_makes_the_climb_negative(self):
        self.make_user(level=MAX_LEVEL, coins=0, owns=True, pass_level=60)
        self.db.collection("users")._docs["u1"]["stats"]["total_xp"] = 0
        self.assertGreaterEqual(self.season_xp(), 0)
        self.assertGreaterEqual(self.pass_level(), 1)

    def test_prestige_carries_the_climb_across_the_reset(self):
        # season_carry_updates is what multiplayer_server injects into
        # prestige_server, and it has to run INSIDE the reset transaction: after
        # total_xp is zero the delta is gone for good.
        self.make_user(level=MAX_LEVEL, coins=0, owns=True, pass_level=60)
        doc = self.user()
        earned = cp.SEASON_XP_PER_LEVEL * 3
        doc["stats"]["total_xp"] += earned                 # some play since the mark
        want = cp.season_xp_to_reach(60) + earned

        updates = cp.season_carry_updates(doc, 0)
        stored = self.db.collection("users")._docs["u1"]
        stored["stats"]["total_xp"] = 0
        stored[cp.SEASON_FIELD] = updates[cp.SEASON_FIELD]

        self.assertEqual(self.season_xp(), want)
        self.assertEqual(self.pass_level(), 63)
        self.assertFalse(cp._needs_fold(self.user()))

    def test_the_carry_is_idempotent_and_skips_accounts_with_no_pass(self):
        self.make_user(level=10, coins=0, owns=False)
        self.assertEqual(cp.season_carry_updates(self.user(), 0), {})
        self.make_user(level=10, coins=0, owns=True, pass_level=10)
        once = cp.season_carry_updates(self.user(), 0)
        self.db.collection("users")._docs["u1"][cp.SEASON_FIELD] = once[cp.SEASON_FIELD]
        self.db.collection("users")._docs["u1"]["stats"]["total_xp"] = 0
        self.assertEqual(cp.season_carry_updates(self.user(), 0), once)

    def test_an_unfolded_reset_freezes_the_climb_rather_than_losing_it(self):
        # The backstop, for a reset that reached the read path without a carry.
        # Progress is frozen at the last fold, which is the safe direction: a
        # frozen climb is a support ticket, a negative one is a refund.
        self.make_user(level=MAX_LEVEL, coins=0, owns=True, pass_level=60)
        self.db.collection("users")._docs["u1"]["stats"]["total_xp"] = 0
        state = cp.state_payload("u1")
        self.assertEqual(state["passLevel"], 60)
        # …and the repair re-marks it, so play from zero starts counting again.
        self.assertFalse(cp._needs_fold(self.user()))
        self.db.collection("users")._docs["u1"]["stats"]["total_xp"] = cp.SEASON_XP_PER_LEVEL
        self.assertEqual(self.pass_level(), 61)

    def test_a_repair_writes_nothing_for_an_account_that_does_not_need_one(self):
        self.make_user(level=10, coins=0, owns=True, pass_level=10)
        before = dict(self.user()[cp.SEASON_FIELD][cp.SEASON_ID])
        cp.state_payload("u1")
        self.assertEqual(self.user()[cp.SEASON_FIELD][cp.SEASON_ID], before)

    def test_the_state_payload_carries_both_levels_and_the_curve(self):
        self.make_user(level=47, coins=0, owns=True, pass_level=12)
        state = cp.state_payload("u1")
        self.assertEqual(state["level"], 47)               # the account, a chip
        self.assertEqual(state["passLevel"], 12)           # the pass, the gate
        self.assertEqual(state["seasonXp"], cp.season_xp_to_reach(12))
        self.assertEqual(state["passXpIntoLevel"], 0)
        self.assertEqual(state["passXpForLevel"], cp.SEASON_XP_PER_LEVEL)
        self.assertEqual(state["seasonXpPerLevel"], cp.SEASON_XP_PER_LEVEL)
        self.assertEqual(state["passMaxLevel"], cp.PASS_MAX_LEVEL)
        self.assertEqual(state["seasonXpToMax"], cp.SEASON_XP_TO_MAX)
        self.assertEqual(state["seasonXpToEarn"], cp.SEASON_XP_TO_EARN)
        self.assertEqual(state["seasonXpPerDay"], cp.SEASON_XP_PER_DAY)


class SeasonClockTests(PassTestBase):
    """The 30-day season window, and the one thing it must not be able to do."""

    def test_a_season_is_thirty_days_long(self):
        self.assertEqual(cp.SEASON_DAYS, 30)
        self.assertEqual(
            (cp.SEASON_ENDS_AT - cp.SEASON_STARTED_AT).days, 30,
            "the end date has to be the start plus SEASON_DAYS, not a second "
            "date somebody typed",
        )

    def test_the_countdown_rounds_up_so_the_last_day_still_says_one(self):
        # Two hours left is still "1 day left". Rounding down would spend the
        # whole final day telling players the season was already over.
        near = cp.SEASON_ENDS_AT - timedelta(hours=2)
        w = cp.season_window(near)
        self.assertEqual(w["seasonDaysLeft"], 1)
        self.assertFalse(w["seasonOver"])

    def test_a_fresh_season_reports_the_full_thirty(self):
        w = cp.season_window(cp.SEASON_STARTED_AT)
        self.assertEqual(w["seasonDaysLeft"], 30)
        self.assertFalse(w["seasonOver"])

    def test_a_finished_season_reports_zero_and_never_goes_negative(self):
        w = cp.season_window(cp.SEASON_ENDS_AT + timedelta(days=400))
        self.assertEqual(w["seasonDaysLeft"], 0)
        self.assertEqual(w["seasonSecondsLeft"], 0)
        self.assertTrue(w["seasonOver"])

    def test_the_clock_running_out_does_not_rotate_the_season(self):
        # THE point of the whole design. SEASON_ID is what every ledger id and
        # the ownership array are keyed by, so if the calendar could move it,
        # a level-100 account would re-buy the track for 4,000 and take 8,500
        # coins straight back out, every 30 days, for ever.
        before = cp.SEASON_ID
        cp.season_window(cp.SEASON_ENDS_AT + timedelta(days=400))
        self.assertEqual(cp.SEASON_ID, before)

    def test_a_lapsed_season_still_pays_out(self):
        # A finished countdown is a DISPLAY. Nothing about claiming may key off
        # it, or an owner would be locked out of a track they paid for while
        # Season 2 was still being built.
        self.make_user(level=100, owns=True)
        tier = self.tier_of("coins")
        res = cp.claim(self.db, "u1", tier["id"])
        self.assertTrue(res.get("ok"), res)

    def test_the_window_is_served_to_the_page(self):
        # The browser must never subtract against its own clock: a device whose
        # date is a week out would print a week of the wrong answer.
        state = cp.state_payload(None)
        for key in ("seasonDays", "seasonDaysLeft", "seasonEndsAt",
                    "seasonStartsAt", "seasonSecondsLeft", "seasonOver"):
            self.assertIn(key, state, key)
        self.assertEqual(state["seasonDays"], 30)


class StateTests(PassTestBase):
    def test_signed_out_gets_the_whole_track_and_no_account_data(self):
        state = cp.state_payload(None)
        self.assertTrue(state["ok"])
        self.assertEqual(len(state["track"]), len(cp.track()))
        self.assertFalse(state["signedIn"])
        self.assertFalse(state["owned"])
        self.assertEqual(state["claimed"], [])
        self.assertEqual(state["level"], 1)
        # The sales pitch numbers are served, not typed into the client.
        self.assertEqual(state["price"], cp.CRITTER_PASS_PRICE)
        self.assertEqual(state["coinTotal"], cp.TRACK_COIN_BUDGET)
        self.assertEqual(state["xpTotal"], cp.TRACK_XP_BUDGET)

    def test_the_level_curve_is_served_so_the_client_never_copies_it(self):
        state = cp.state_payload(None)
        self.assertEqual(state["levelTotals"], LEVEL_TOTALS)

    def test_a_non_owner_has_no_claims_listed(self):
        self.make_user(level=100, owns=False)
        state = cp.state_payload("u1")
        self.assertFalse(state["owned"])
        self.assertEqual(state["claimed"], [])

    def test_an_owner_sees_what_they_claimed(self):
        self.make_user(level=100, owns=True)
        tier = self.tier_of("coins")
        cp.claim(self.db, "u1", tier["id"])
        state = cp.state_payload("u1")
        self.assertTrue(state["owned"])
        self.assertEqual(state["claimed"], [tier["id"]])

    def test_another_seasons_claim_is_not_this_seasons(self):
        self.make_user(level=100, owns=True)
        self.db.collection("critter_pass_claims")._docs["u1__S0_L4"] = {
            "uid": "u1", "season": "S0", "tier": "L4",
        }
        self.assertEqual(cp.state_payload("u1")["claimed"], [])

    def test_the_extra_slot_counts_are_clamped_on_the_way_out(self):
        # A hand-edited document must not be able to ask the browser for nine
        # daily challenges out of a fifty-challenge pool.
        self.make_user(level=100, owns=True,
                       bonus_daily_slots=99, bonus_weekly_slots=-4)
        inv = cp.state_payload("u1")["inventory"]
        self.assertEqual(inv["extraDaily"], cp.MAX_EXTRA_DAILY)
        self.assertEqual(inv["extraWeekly"], 0)

    def test_the_caps_are_served_so_the_badge_agrees_with_the_payout(self):
        state = cp.state_payload(None)
        self.assertEqual(state["caps"]["shields"], cp.MAX_SHIELDS)
        self.assertEqual(state["caps"]["boosts"], cp.MAX_BOOSTS)
        self.assertEqual(state["caps"]["rerolls"], cp.MAX_REROLLS)

    def test_a_read_that_succeeds_says_so(self):
        self.make_user(level=12)
        self.assertTrue(cp.state_payload("u1")["accountRead"])

    def test_a_refusing_database_does_not_claim_the_player_is_level_1(self):
        # The same 2026-09-04 quota outage the Level Pass carries a test for.
        # Here it is the ACCOUNT chip that would lie; the pass level stays 1
        # either way, because season XP is a delta only the server holds.
        self.make_user(level=39, owns=True, pass_level=30)

        class Boom:
            def collection(self, *_a, **_k):
                raise RuntimeError("429 Quota exceeded")

        cp._get_firestore = lambda: Boom()                # type: ignore[assignment]
        state = cp.state_payload("u1")
        self.assertTrue(state["ok"])
        self.assertTrue(state["signedIn"])
        self.assertFalse(state["accountRead"])

    def test_signed_out_is_not_a_failed_read(self):
        self.assertFalse(cp.state_payload(None)["accountRead"])


# ══════════════════════════════════════════════════════════════════════════
#  THE OLDER XP SHAPE
#  An account that stores `level` + `xp_current` and no total_xp. The client
#  has always read those (preview-app.js getStoredTotalXp); both passes now
#  read them through ONE shared function, so the game and the track cannot
#  disagree about what level somebody is.
# ══════════════════════════════════════════════════════════════════════════
class StoredTotalXpTests(PassTestBase):
    def test_the_account_chip_is_not_level_1(self):
        self.db.collection("users")._docs["u1"] = {
            "stats": {"level": 39, "xp_current": 1550},
        }
        self.assertEqual(cp.state_payload("u1")["level"], 39)

    def test_both_passes_read_it_through_the_same_function(self):
        # Not "they happen to agree": the same object. A second copy of this
        # fallback is a second place for the two tracks to drift.
        import level_pass_server as lp
        self.assertIs(cp._lp.stored_total_xp, lp.stored_total_xp)

    def test_the_season_mark_is_stamped_from_the_same_reading(self):
        # Season XP is total_xp minus a mark. If buy() stamped the mark from a
        # raw 0 while the state read answered 57,400, the new owner's first
        # sight of their pass would be 57,400 season XP: the whole track, free.
        self.db.collection("users")._docs["u1"] = {
            "stats": {"level": 39, "xp_current": 1550, "critter_coins": 9999},
        }
        res = cp.buy(self.db, "u1")
        self.assertTrue(res.get("ok"), res)
        state = cp.state_payload("u1")
        self.assertEqual(state["seasonXp"], 0)
        self.assertEqual(state["passLevel"], 1)


# ══════════════════════════════════════════════════════════════════════════
#  HTTP
# ══════════════════════════════════════════════════════════════════════════
class FakeHandler:
    def __init__(self):
        self.payload = None
        self.status = 200

    def _send_json(self, payload, status=200):
        self.payload = payload
        self.status = status


class Parsed:
    def __init__(self, path):
        self.path = path


class HttpTests(PassTestBase):
    def post(self, path, body=None):
        h = FakeHandler()
        handled = cp.handle_post(h, Parsed(path), body or {})
        return handled, h

    def test_other_paths_are_left_alone(self):
        handled, _ = self.post("/api/pass/state")
        self.assertFalse(handled)
        handled, _ = self.post("/api/rooms/x/chat")
        self.assertFalse(handled)

    def test_state_is_readable_signed_out(self):
        handled, h = self.post("/api/critterpass/state")
        self.assertTrue(handled)
        self.assertTrue(h.payload["ok"])
        self.assertFalse(h.payload["signedIn"])

    def test_every_writing_action_needs_a_token(self):
        for action in ("buy", "claim", "claim-all"):
            handled, h = self.post(f"/api/critterpass/{action}", {"tier": "L4"})
            self.assertTrue(handled)
            self.assertEqual(h.status, 401, action)
            self.assertEqual(h.payload["error"], "unauthorized", action)

    def test_a_bad_token_is_not_a_uid(self):
        handled, h = self.post("/api/critterpass/buy", {"idToken": "bad:u1"})
        self.assertEqual(h.status, 401)

    def test_buy_over_http(self):
        self.make_user(coins=9999)
        handled, h = self.post("/api/critterpass/buy", {"idToken": "good:u1"})
        self.assertTrue(h.payload["ok"], h.payload)
        self.assertEqual(self.coins(), 9999 - cp.CRITTER_PASS_PRICE)
        # The reply carries the fresh inventory, so the page does not have to
        # guess what the balance is now.
        self.assertEqual(h.payload["inventory"]["coins"], self.coins())

    def test_a_refused_reply_carries_a_sentence(self):
        self.make_user(coins=0)
        handled, h = self.post("/api/critterpass/buy", {"idToken": "good:u1"})
        self.assertFalse(h.payload["ok"])
        self.assertEqual(h.payload["message"], cp.ERROR_MESSAGES["not_enough_coins"])

    def test_claim_over_http(self):
        self.make_user(level=100, owns=True)
        tier = self.tier_of("coins")
        handled, h = self.post("/api/critterpass/claim",
                               {"idToken": "good:u1", "tier": tier["id"]})
        self.assertTrue(h.payload["ok"], h.payload)
        self.assertEqual(self.coins(), tier["amount"])

    def test_an_unknown_action_is_a_404(self):
        handled, h = self.post("/api/critterpass/nonsense", {"idToken": "good:u1"})
        self.assertTrue(handled)
        self.assertEqual(h.status, 404)


# ══════════════════════════════════════════════════════════════════════════
#  DRIFT: the other halves of the feature
# ══════════════════════════════════════════════════════════════════════════
class DriftTests(unittest.TestCase):
    """The server is only a third of this. These fail when the tab, the module
    or the avatar is renamed on one side and not the other, which is the
    failure that ships looking green."""

    def setUp(self):
        self.app = _read(PREVIEW_JS)
        self.html = _read(PREVIEW_HTML)
        self.js = _read(CRITTER_JS)
        self.css = _read(CRITTER_CSS)
        self.server = _read(SERVER_PY)
        self.server_mod = _read(os.path.join(ROOT, "critter_pass_server.py"))

    def test_the_finale_avatar_is_a_real_avatar(self):
        slug = cp.FINALE_AVATAR.rsplit("/", 1)[-1][: -len(".png")]
        self.assertIn(f'id:"{slug}"', self.app)
        self.assertIn(cp.FINALE_AVATAR, self.app)
        # …and the file is actually on disk, or the tier draws a broken image.
        self.assertTrue(os.path.exists(os.path.join(CLIENT_DIR, cp.FINALE_AVATAR.lstrip("/"))))

    def test_the_finale_avatar_is_the_gull_with_the_bucket(self):
        # Named, not just "an avatar": there are two seagulls in the game and
        # only one of them is carrying a bucket.
        self.assertEqual(cp.FINALE_AVATAR, "/avatars/summer-skin-gull.png")

    def test_the_gallery_says_the_pass_is_a_way_to_get_the_gull(self):
        # It is also a 2,000-coin Store skin. A pass owner climbing to 100 must
        # not be told in the Avatar Gallery that the Store is the only way.
        slug = cp.FINALE_AVATAR.rsplit("/", 1)[-1][: -len(".png")]
        at = self.app.index(f'id:"{slug}"')
        entry = self.app[at:at + 700]
        self.assertIn("Critter Pass", entry)
        # …but its unlock TYPE stays "shop", because that is what decides
        # whether a guest may wear it and whether Prestige relocks it.
        self.assertIn('type:"shop"', entry)

    def test_the_kelp_forest_background_exists_and_is_the_pages_background(self):
        self.assertIn("/backgrounds/kelp-forest.png", self.css)
        self.assertTrue(os.path.exists(
            os.path.join(CLIENT_DIR, "backgrounds", "kelp-forest.png")))

    def test_the_tab_is_wired_into_player_home(self):
        self.assertIn('id="snav-critterpass"', self.html)
        self.assertIn('data-tab="critterpass"', self.html)
        self.assertIn('id="snav-critterpass-badge"', self.html)
        self.assertIn('id="ph-panel-critterpass"', self.html)
        self.assertIn('id="cc-critter-pass-root"', self.html)
        self.assertIn('critterpass:"ph-panel-critterpass"', self.app)
        self.assertIn('if (name === "critterpass")', self.app)

    def test_it_sits_directly_under_the_level_pass_in_the_sidebar(self):
        lp_at = self.html.index('id="snav-levelpass"')
        cp_at = self.html.index('id="snav-critterpass"')
        store_at = self.html.index('id="snav-store"')
        self.assertLess(lp_at, cp_at)
        self.assertLess(cp_at, store_at)

    def test_the_module_and_stylesheet_are_actually_served(self):
        self.assertIn("/js/critter-pass.js", self.html)
        self.assertIn("/css/critter-pass.css", self.html)

    def test_the_bridge_exists_and_is_primed_and_reset(self):
        self.assertIn("window.__ccCritterPass = {", self.app)
        self.assertIn("__ccCritterPassPrime", self.app)
        # Reset on BOTH identity-change paths, or the next account inherits the
        # last one's extra challenge slots.
        self.assertEqual(self.app.count("window.__ccCritterPassReset && window.__ccCritterPassReset()"), 2)

    def test_the_client_reads_the_endpoints_this_module_serves(self):
        self.assertIn('"/api/critterpass/" + action', self.js)
        for action in ("state", "buy", "claim", "claim-all"):
            self.assertIn(f'"{action}"', self.js)

    def test_the_challenge_strip_reads_the_slot_seam(self):
        self.assertIn("window.__ccPassExtraSlots", self.app)
        self.assertIn("window.__ccPassExtraSlots = function", self.js)
        self.assertIn("const _dailySlotCount", self.app)
        self.assertIn("const _weeklySlotCount", self.app)

    def test_the_clients_extra_slot_clamp_matches_the_servers(self):
        m = re.search(r"const _CS_MAX_EXTRA\s*=\s*(\d+);", self.app)
        self.assertIsNotNone(m, "_CS_MAX_EXTRA vanished from preview-app.js")
        self.assertEqual(int(m.group(1)), cp.MAX_EXTRA_DAILY)
        self.assertEqual(int(m.group(1)), cp.MAX_EXTRA_WEEKLY)

    def test_the_base_slot_count_is_still_three(self):
        m = re.search(r"const _CS_BASE_SLOTS\s*=\s*(\d+);", self.app)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), 3)

    def test_the_tide_sweep_never_gets_harder_for_owning_the_pass(self):
        # The sweep target is the BASE count, not the slot count. If these ever
        # read state.slots.length the reward somebody paid for becomes a
        # doubled workload for the same achievement.
        self.assertIn("sweepDone:    completedCount >= _CS_BASE_SLOTS", self.app)
        self.assertIn("sweepDone:      completedCount >= _CS_BASE_SLOTS", self.app)
        self.assertNotIn("sweepDone:    completedCount >= 3", self.app)

    def test_the_server_imports_and_inits_the_module(self):
        self.assertIn("import critter_pass_server", self.server)
        self.assertIn("critter_pass_server.init(", self.server)
        self.assertIn("critter_pass_server.handle_post(self, parsed, body)", self.server)

    def test_prestige_is_wired_to_carry_the_season_across_a_reset(self):
        # Prestige zeroes stats.total_xp and the pass measures its season as a
        # delta against it, so without this injection a Prestige silently
        # freezes a climb somebody paid 4,000 coins for. It is a wiring check
        # because the bug it prevents cannot be seen from either module alone.
        self.assertIn("pass_carry_updates=critter_pass_server.season_carry_updates",
                      self.server)
        self.assertIn("pass_carry_updates=None", _read(os.path.join(ROOT, "prestige_server.py")))

    def test_the_client_reads_the_pass_curve_off_the_server(self):
        # Every "N XP to go" on the rail is the PASS curve. A number typed into
        # the client is a card that keeps quoting the old curve after a retune,
        # and the account curve would have it quoting thousands for a level
        # that costs SEASON_XP_PER_LEVEL.
        js = _read(CRITTER_JS)
        self.assertIn("_state.seasonXpPerLevel", js)
        self.assertIn("_state.seasonXp", js)
        self.assertIn("_state.passLevel", js)
        self.assertNotIn(str(cp.SEASON_XP_PER_LEVEL) + " * ", js)
        # The rail must not gate a tier on the ACCOUNT level any more. Exactly
        # ONE read of it survives, and it is the chip that stops "Pass Level 1"
        # on a level-47 account reading as a bug.
        # TWO reads survive, and neither gates anything: the chip that stops
        # "Pass Level 1" on a level-47 account reading as a bug, and the guest
        # branch that fills that same chip in from browser-banked XP.
        self.assertEqual(len(re.findall(r"_state\.level\b", js)), 2,
                         "the account level gates something again")
        self.assertIn("const acctLvl = num(_state && _state.level, 1);", js)
        self.assertIn("_state.level = lvl;", js)
        # …and the four places that decide what is claimable all read the pass.
        for fn in ("function tierState", "function nextTier",
                   "function unclaimedReady", "function scrollToCurrent"):
            body = js.split(fn, 1)[1].split("\n  }", 1)[0]
            self.assertIn("passLevel()", body, f"{fn} does not gate on the pass level")

    def test_the_account_chip_falls_back_to_what_the_app_knows(self):
        # The server flags a failed read (accountRead:false) and the page has
        # to act on it, or the chip goes on saying Level 1 to a Level 39
        # account for as long as Firestore is refusing.
        self.assertIn("_state.accountRead !== false", self.js)
        self.assertIn("applyLiveAccountLevel();", self.js)
        # …and it must touch the ACCOUNT fields only. A lifetime XP figure says
        # nothing about what a pass has earned since it was unlocked, so a
        # fallback that wrote passLevel would hand out the track.
        body = self.js.split("function applyAccountLevelFromXp", 1)[1].split("\n  }", 1)[0]
        for field in ("passLevel", "seasonXp", "passXpIntoLevel", "passXpForLevel"):
            self.assertNotIn(field, body,
                             f"the account-level fallback writes {field}")

    def test_both_passes_read_stored_xp_through_one_function(self):
        # Not a copied fallback: the same callable, called with THIS module's
        # own curve rather than whichever one the other module was inited with.
        self.assertIn("_lp.stored_total_xp(_stats_of(doc), _level_totals)", self.server_mod)
        self.assertNotIn('_int(_stats_of(doc).get("total_xp"))', self.server_mod)

    def test_the_module_ships_in_the_docker_image(self):
        # Module-scope import: a missing COPY or a missing allowlist entry is a
        # server that will not boot, not a feature that quietly goes missing.
        self.assertIn("COPY critter_pass_server.py", _read(os.path.join(ROOT, "Dockerfile")))
        self.assertIn("!critter_pass_server.py", _read(os.path.join(ROOT, ".dockerignore")))
        self.assertIn("!critter_pass_server.py", _read(os.path.join(ROOT, ".gitignore")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
