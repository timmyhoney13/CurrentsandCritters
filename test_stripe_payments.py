"""The Stripe money path: what turns a real payment into a real reward.

These are the pieces that decide whether a player who paid actually GETS what
they paid for, and whether anyone who did NOT pay can mint themselves coins.
Every check here is on the pure decision functions in multiplayer_server, so the
suite runs with no Firestore, no network, and no Stripe account.

Covered:
  • signature verification — the ONLY thing standing between a stranger's POST
    and free Critter Coins,
  • price → reward mapping, including the live-mode ways it can silently miss,
  • the wall tier / lifetime-total maths,
  • the custom-field readers that carry the buyer's wall name and username,
  • the session-status endpoint's input validation.

Run:  python3 test_stripe_payments.py
"""

import hashlib
import hmac
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import multiplayer_server as ms


def _sign(payload: bytes, secret: str, timestamp: int = None) -> str:
    """Build a real Stripe-Signature header the way Stripe builds it."""
    ts = int(time.time()) if timestamp is None else timestamp
    signed = f"{ts}".encode() + b"." + payload
    mac = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


class TestWebhookSignature(unittest.TestCase):
    """The gate. If any of these flip, anyone can POST themselves free coins."""

    SECRET = "whsec_test_secret_value"
    BODY = json.dumps({"id": "evt_1", "type": "checkout.session.completed"}).encode()

    def test_accepts_a_genuine_signature(self):
        header = _sign(self.BODY, self.SECRET)
        self.assertTrue(ms._verify_stripe_signature(self.BODY, header, self.SECRET))

    def test_rejects_a_forged_signature(self):
        header = _sign(self.BODY, "whsec_the_wrong_secret")
        self.assertFalse(ms._verify_stripe_signature(self.BODY, header, self.SECRET))

    def test_rejects_a_tampered_body(self):
        """Signed one payload, delivered another — the classic attack."""
        header = _sign(self.BODY, self.SECRET)
        tampered = json.dumps({"id": "evt_1", "amount_total": 99999999}).encode()
        self.assertFalse(ms._verify_stripe_signature(tampered, header, self.SECRET))

    def test_rejects_when_no_secret_is_configured(self):
        """An unset STRIPE_WEBHOOK_SECRET must FAIL CLOSED, never fulfil."""
        header = _sign(self.BODY, self.SECRET)
        self.assertFalse(ms._verify_stripe_signature(self.BODY, header, ""))

    def test_rejects_a_missing_header(self):
        self.assertFalse(ms._verify_stripe_signature(self.BODY, "", self.SECRET))

    def test_rejects_a_replayed_old_event(self):
        """Outside the tolerance window a captured-and-resent event is refused."""
        stale = int(time.time()) - (ms.STRIPE_SIG_TOLERANCE_SEC + 60)
        header = _sign(self.BODY, self.SECRET, timestamp=stale)
        self.assertFalse(ms._verify_stripe_signature(self.BODY, header, self.SECRET))

    def test_accepts_when_one_of_several_signatures_matches(self):
        """During a signing-secret rotation Stripe sends multiple v1 values."""
        ts = int(time.time())
        good = _sign(self.BODY, self.SECRET, timestamp=ts).split("v1=")[1]
        header = f"t={ts},v1=deadbeef,v1={good}"
        self.assertTrue(ms._verify_stripe_signature(self.BODY, header, self.SECRET))


class TestRewardMapping(unittest.TestCase):
    """Price → what the buyer gets. A miss here means a paid player gets nothing."""

    def _session(self, **kw):
        s = {"currency": "usd", "payment_status": "paid"}
        s.update(kw)
        return s

    def test_every_coin_pack_price_maps(self):
        for cents, coins in ms.COIN_PACKS_BY_CENTS.items():
            kind, value = ms._reward_for_session(self._session(amount_total=cents))
            self.assertEqual((kind, value), ("coins", coins), f"${cents/100:.2f} pack")

    def test_every_supporter_tier_price_maps(self):
        for cents, tier in ms.SUPPORTER_TIERS_BY_CENTS.items():
            kind, value = ms._reward_for_session(self._session(amount_total=cents))
            self.assertEqual((kind, value), ("tier", tier), f"${cents/100:.2f} tier")

    def test_every_tier_price_has_grants_defined(self):
        """A tier that maps but has no grant entry would charge for nothing."""
        for tier in ms.SUPPORTER_TIERS_BY_CENTS.values():
            self.assertIn(tier, ms.SUPPORTER_TIER_GRANTS, f"{tier} has no grants")

    def test_metadata_wins_over_price(self):
        """The escape hatch for two products sharing a price."""
        kind, value = ms._reward_for_session(
            self._session(amount_total=100, metadata={"cc_coins": "7777"}))
        self.assertEqual((kind, value), ("coins", 7777))
        kind, value = ms._reward_for_session(
            self._session(amount_total=100, metadata={"cc_tier": "Tide-Turner"}))
        self.assertEqual((kind, value), ("tier", "tide-turner"))

    def test_non_usd_is_not_guessed_from_the_amount(self):
        """1500 JPY is not a $15 tier — the cents tables are USD prices only."""
        kind, value = ms._reward_for_session(
            self._session(currency="jpy", amount_total=1500))
        self.assertEqual((kind, value), (None, None))

    def test_unknown_amount_grants_nothing(self):
        """A live-mode price change, added tax, or quantity>1 lands here: the
        payment is still RECORDED (wall + lifetime total) but grants no reward.
        Set metadata.cc_coins / cc_tier on the Payment Link to decouple."""
        kind, value = ms._reward_for_session(self._session(amount_total=1234))
        self.assertEqual((kind, value), (None, None))

    def test_falls_back_to_subtotal_when_total_shifted(self):
        """Tax or a discount moves amount_total; the pre-tax subtotal still maps."""
        kind, value = ms._reward_for_session(
            self._session(amount_total=1612, amount_subtotal=1500))
        self.assertEqual((kind, value), ("tier", "wave-warrior"))


class TestWallTiers(unittest.TestCase):
    """Wall placement is by LIFETIME total, not by a single payment."""

    def test_below_the_floor_has_no_tier(self):
        self.assertEqual(ms._supporter_tier_for_total(999), (None, None))

    def test_each_band(self):
        cases = [
            (1000,  "wave_warrior",  "small"),        # $10
            (2499,  "wave_warrior",  "small"),
            (2500,  "ocean_ally",    "medium"),       # $25
            (5000,  "tide_turner",   "large"),        # $50
            (10000, "reef_guardian", "extra_large"),  # $100
            (25000, "ocean_legend",  "biggest"),      # $250
            (999999, "ocean_legend", "biggest"),
        ]
        for cents, tier, size in cases:
            self.assertEqual(ms._supporter_tier_for_total(cents), (tier, size),
                             f"${cents/100:.2f}")

    def test_two_small_gifts_add_up_to_a_tier(self):
        """$15 + $15 must reach the $25 band — the point of a lifetime total."""
        self.assertEqual(ms._supporter_tier_for_total(1500 + 1500)[0], "ocean_ally")

    def test_garbage_totals_do_not_crash(self):
        self.assertEqual(ms._supporter_tier_for_total(None), (None, None))
        self.assertEqual(ms._supporter_tier_for_total("abc"), (None, None))


class TestCustomFields(unittest.TestCase):
    """The three checkout questions that carry the wall name and the username."""

    def _field(self, label, value, ftype="text"):
        return {"key": "k", "label": {"type": "custom", "custom": label},
                "type": ftype, ftype: {"value": value}}

    def test_reads_each_label(self):
        fields = [
            self._field(ms.CF_WALL_NAME_LABEL, "Reef Rider"),
            self._field(ms.CF_WALL_PUBLIC_LABEL, "Yes", "dropdown"),
            self._field(ms.CF_USERNAME_LABEL, "tim_h"),
        ]
        self.assertEqual(ms._custom_field_value(fields, ms.CF_WALL_NAME_LABEL), "Reef Rider")
        self.assertEqual(ms._custom_field_value(fields, ms.CF_WALL_PUBLIC_LABEL), "Yes")
        self.assertEqual(ms._custom_field_value(fields, ms.CF_USERNAME_LABEL), "tim_h")

    def test_label_match_is_case_and_space_insensitive(self):
        """Stripe echoes the label the site owner typed — don't be brittle."""
        fields = [self._field("  name for supporter reef wall  ", "Jett")]
        self.assertEqual(ms._custom_field_value(fields, ms.CF_WALL_NAME_LABEL), "Jett")

    def test_username_question_accepts_both_spellings_of_the_game_name(self):
        """A custom-field label is a BEHAVIOUR KEY, not display text.

        The game's name appears both ways in the wild — "Currents and Critters"
        everywhere it is displayed now, "Currents & Critters" on anything older.
        A Payment Link whose question uses the spelling the server does NOT
        match reads back as "", so a signed-out buyer's typed username is lost
        and their payment can never be tied to their account. Both spellings
        have to keep working."""
        for label in ("Currents & Critters Online Username",
                      "Currents and Critters Online Username"):
            fields = [self._field(label, "reef_rider")]
            self.assertEqual(
                ms._custom_field_value(fields, ms.CF_USERNAME_LABELS), "reef_rider",
                f"a Payment Link asking {label!r} lost the username")

    def test_the_ampersand_spelling_is_still_listed(self):
        """Guard against a future find-and-replace quietly dropping it."""
        self.assertIn("Currents & Critters Online Username", ms.CF_USERNAME_LABELS)

    def test_a_label_tuple_falls_through_to_the_one_that_matches(self):
        fields = [self._field("Some Other Question", "x")]
        self.assertEqual(ms._custom_field_value(fields, ms.CF_USERNAME_LABELS), "")

    def test_missing_field_is_blank_not_an_error(self):
        self.assertEqual(ms._custom_field_value([], ms.CF_WALL_NAME_LABEL), "")
        self.assertEqual(ms._custom_field_value(None, ms.CF_WALL_NAME_LABEL), "")

    def test_yes_answers(self):
        for yes in ("Yes", "yes", "YES", "y", "true", "1", "Show", "public"):
            self.assertTrue(ms._is_affirmative(yes), yes)

    def test_no_answers_keep_the_donor_anonymous(self):
        """Anything not clearly a yes must default to PRIVATE."""
        for no in ("No", "n", "", None, "nope", "later", "maybe"):
            self.assertFalse(ms._is_affirmative(no), repr(no))


class TestWallNameSafety(unittest.TestCase):
    """A clean public name auto-shows; anything else waits for a human."""

    def test_ordinary_names_go_straight_up(self):
        for name in ("The Jett", "Sam O'Neill", "Coral Queen", "J. Hancock",
                     "Scunthorpe Rovers", "Dickinson Family"):
            self.assertFalse(ms._name_needs_review(name), name)

    def test_slurs_and_profanity_are_held(self):
        for name in ("ShitLord", "n i g g e r", "F-u-c-k", "big dick energy"):
            self.assertTrue(ms._name_needs_review(name), name)

    def test_blank_and_overlong_are_held(self):
        self.assertTrue(ms._name_needs_review(""))
        self.assertTrue(ms._name_needs_review("x" * 41))


class TestSessionStatusInput(unittest.TestCase):
    """/api/stripe/session-status must never turn user text into a query."""

    def test_rejects_a_non_stripe_id(self):
        for bad in ("", "   ", "hello", "../../etc/passwd", "cs_" + "x" * 300,
                    "pi_12345", "cs_live_abc def"):
            out = ms._stripe_session_status(bad)
            self.assertFalse(out.get("ok"), repr(bad))
            self.assertEqual(out.get("error"), "bad session id", repr(bad))

    def test_accepts_the_real_shape(self):
        """A well-formed id gets past validation (then needs Firestore, which is
        absent here — so 'unavailable', NOT 'bad session id')."""
        for good in ("cs_test_a1B2c3D4", "cs_live_b1234567890"):
            out = ms._stripe_session_status(good)
            self.assertNotEqual(out.get("error"), "bad session id", good)


class TestLevelCurveMatchesClient(unittest.TestCase):
    """Supporter-tier XP writes the derived level fields; they must agree with
    what the client would compute or the leaderboard disagrees with the profile."""

    def test_level_one_at_zero(self):
        self.assertEqual(ms._level_progress_for_total_xp(0)[0], 1)

    def test_boundaries(self):
        totals = ms.LEVEL_XP_TOTALS
        for lvl in (2, 5, 20, 50, len(totals)):
            self.assertEqual(ms._level_progress_for_total_xp(totals[lvl - 1])[0], lvl)

    def test_caps_at_the_top(self):
        lvl, cur, goal = ms._level_progress_for_total_xp(10 ** 9)
        self.assertEqual(lvl, len(ms.LEVEL_XP_TOTALS))
        self.assertEqual(cur, goal)

    def test_a_tier_bonus_moves_a_new_player_up(self):
        bonus = ms.SUPPORTER_TIER_GRANTS["tide-turner"]["bonus_xp"]
        self.assertGreater(ms._level_progress_for_total_xp(bonus)[0], 1)

    def test_garbage_xp_does_not_crash(self):
        self.assertEqual(ms._level_progress_for_total_xp(None)[0], 1)
        self.assertEqual(ms._level_progress_for_total_xp(-500)[0], 1)


class TestSupporterTierCoinGrants(unittest.TestCase):
    """Supporter Tiers credit Critter Coins. The amounts are printed on three
    separate tier cards, so the danger is not the maths — it's drift."""

    ORDER = ("wave-warrior", "ocean-ally", "tide-turner")

    def test_every_tier_grants_coins(self):
        for tier in ms.SUPPORTER_TIER_GRANTS:
            coins = ms.SUPPORTER_TIER_GRANTS[tier].get("coins")
            self.assertIsInstance(coins, int, f"{tier} coins must be an int")
            self.assertGreater(coins, 0, f"{tier} grants no coins")

    def test_coins_climb_with_price(self):
        """A dearer tier must never hand out fewer coins than a cheaper one."""
        by_price = [ms.SUPPORTER_TIERS_BY_CENTS[c] for c in sorted(ms.SUPPORTER_TIERS_BY_CENTS)]
        self.assertEqual(by_price, list(self.ORDER))
        got = [ms.SUPPORTER_TIER_GRANTS[t]["coins"] for t in by_price]
        self.assertEqual(got, sorted(got), f"coins not monotonic: {got}")

    def test_tiers_stay_under_the_coin_pack_rate(self):
        """Tiers must not out-value the coin packs, or nobody buys a pack.
        Best pack rate is $20 → 25,000 = 1,250 coins per dollar."""
        best_rate = max(c / (cents / 100) for cents, c in ms.COIN_PACKS_BY_CENTS.items())
        for cents, tier in ms.SUPPORTER_TIERS_BY_CENTS.items():
            rate = ms.SUPPORTER_TIER_GRANTS[tier]["coins"] / (cents / 100)
            self.assertLess(rate, best_rate,
                            f"{tier} pays {rate:.0f} coins/$ vs the best pack's {best_rate:.0f}")

    def test_wave_warrior_can_buy_the_backgrounds_it_is_not_given(self):
        """The one tier that does NOT unlock all backgrounds still gets enough
        coins to buy some at 1,000 each. The arithmetic is pinned here; the
        SENTENCE that used to print it on the tier cards is deliberately gone
        (see test_tier_cards_do_not_promise_what_the_coins_buy)."""
        g = ms.SUPPORTER_TIER_GRANTS["wave-warrior"]
        self.assertFalse(g["unlock_all_backgrounds"])
        affordable = g["coins"] // 1000
        self.assertGreaterEqual(affordable, 1)

    def test_tier_cards_do_not_promise_what_the_coins_buy(self):
        """Both tier cards used to spell out exactly what a tier's coins would
        buy ("5 of the 8 backgrounds", "~7 seasonal skins", "a full year of
        seasonal skins"). Every one of those sentences goes stale the moment a
        price or the catalogue moves — and a stale promise on a PAID product is
        the expensive kind of wrong — so they were removed from both surfaces
        and must not come back. The coin AMOUNTS still show; only the claim
        about what they buy is gone."""
        retired = (
            "of the 8 backgrounds",
            "Backgrounds are already yours",
            "full year of seasonal skins",
        )
        root = os.path.dirname(os.path.abspath(__file__))
        for path in (("index.html",),
                     ("multiplayer", "client", "js", "preview-app.js")):
            with open(os.path.join(root, *path), "r", encoding="utf-8") as f:
                text = f.read()
            for claim in retired:
                self.assertNotIn(claim, text,
                                 f"{path[-1]} still promises \"{claim}\"")
            # The amounts themselves must survive the removal.
            self.assertIn("Critter Coins", text,
                          f"{path[-1]} no longer mentions Critter Coins at all")


class TestTierGrantUpdates(unittest.TestCase):
    """_supporter_tier_grant_updates is the ONE place a tier is turned into
    account changes — the webhook and the late-claim path both call it, so a
    guest who claims after the fact gets exactly what a signed-in buyer got."""

    def test_coins_are_added_to_the_existing_balance(self):
        updates, coins = ms._supporter_tier_grant_updates(
            "ocean-ally", {"critter_coins": 700})
        self.assertEqual(coins, ms.SUPPORTER_TIER_GRANTS["ocean-ally"]["coins"])
        self.assertEqual(updates["stats"]["critter_coins"], 700 + coins)

    def test_a_fresh_account_starts_from_zero(self):
        updates, coins = ms._supporter_tier_grant_updates("wave-warrior", {})
        self.assertEqual(updates["stats"]["critter_coins"], coins)

    def test_garbage_balances_do_not_crash_or_go_negative(self):
        for junk in ({"critter_coins": None}, {"critter_coins": "x"},
                     {"critter_coins": -50}, None):
            updates, coins = ms._supporter_tier_grant_updates("tide-turner", junk)
            self.assertEqual(updates["stats"]["critter_coins"], coins, junk)

    def test_xp_and_level_still_ride_along(self):
        updates, _ = ms._supporter_tier_grant_updates("tide-turner", {"total_xp": 0})
        stats = updates["stats"]
        bonus = ms.SUPPORTER_TIER_GRANTS["tide-turner"]["bonus_xp"]
        self.assertEqual(stats["total_xp"], bonus)
        self.assertEqual(stats["level"], ms._level_progress_for_total_xp(bonus)[0])
        self.assertEqual(stats["level"], stats["player_level"])

    def test_the_badge_and_the_perks_are_all_there(self):
        updates, _ = ms._supporter_tier_grant_updates("tide-turner", {})
        self.assertEqual(updates["supporter_tier"], "tide-turner")
        self.assertEqual(updates["stats"]["supporter_tier"], "tide-turner")
        self.assertIn("unlocked_backgrounds", updates)
        self.assertIn("unlocked_icons", updates)

    def test_wave_warrior_gets_no_backgrounds(self):
        updates, _ = ms._supporter_tier_grant_updates("wave-warrior", {})
        self.assertNotIn("unlocked_backgrounds", updates)

    def test_an_unknown_tier_grants_nothing_but_the_badge(self):
        updates, coins = ms._supporter_tier_grant_updates("not-a-tier", {})
        self.assertEqual(coins, 0)
        self.assertNotIn("critter_coins", updates["stats"])
        self.assertNotIn("unlocked_backgrounds", updates)


class TestTierCoinsPrintedEverywhere(unittest.TestCase):
    """The server credits the coins; three separate pages PRINT the number.
    If any of them drifts, a player is promised an amount they don't receive."""

    ROOT = os.path.dirname(os.path.abspath(__file__))

    def _read(self, *parts):
        with open(os.path.join(self.ROOT, *parts), "r", encoding="utf-8") as f:
            return f.read()

    def test_the_in_game_store_lists_the_server_amounts(self):
        js = self._read("multiplayer", "client", "js", "preview-app.js")
        for tier, usd in (("wave-warrior", 15), ("ocean-ally", 35), ("tide-turner", 50)):
            coins = ms.SUPPORTER_TIER_GRANTS[tier]["coins"]
            self.assertIn(f"usd: {usd}, coins: {coins}", js,
                          f"store card for {tier} does not say {coins} coins")

    def test_the_marketing_site_lists_the_server_amounts(self):
        html = self._read("index.html")
        for tier in ms.SUPPORTER_TIER_GRANTS:
            coins = ms.SUPPORTER_TIER_GRANTS[tier]["coins"]
            self.assertIn(f"{coins:,} Critter Coins", html,
                          f"index.html never promises {tier}'s {coins:,} coins")

    def test_the_thanks_page_reads_the_amount_from_the_server(self):
        """/thanks must NOT keep its own copy of the numbers — it prints
        whatever tierCoins the status endpoint sends."""
        html = self._read("multiplayer", "client", "thanks.html")
        self.assertIn("tierCoins", html)
        for tier in ms.SUPPORTER_TIER_GRANTS:
            self.assertNotIn(f"{ms.SUPPORTER_TIER_GRANTS[tier]['coins']:,}", html)


class TestBackgroundsStayInSync(unittest.TestCase):
    """'Unlock all backgrounds' must grant exactly the 8 the client shows."""

    def test_paths_are_well_formed_and_unique(self):
        self.assertEqual(len(ms.ALL_BACKGROUND_PATHS), len(set(ms.ALL_BACKGROUND_PATHS)))
        for p in ms.ALL_BACKGROUND_PATHS:
            self.assertTrue(p.startswith("/backgrounds/"), p)

    def test_client_list_matches_the_server_grant(self):
        """EXCLUSIVE_BACKGROUNDS in preview-app.js is the list players see."""
        js_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "multiplayer", "client", "js", "preview-app.js")
        with open(js_path, "r", encoding="utf-8") as f:
            js = f.read()
        for p in ms.ALL_BACKGROUND_PATHS:
            self.assertIn(p, js, f"{p} is granted by the server but absent from the client")


_ROOT = os.path.dirname(os.path.abspath(__file__))


def _read(*parts: str) -> str:
    with open(os.path.join(_ROOT, *parts), "r", encoding="utf-8") as f:
        return f.read()


class TestLivePaymentLinks(unittest.TestCase):
    """Every Buy button points at the RIGHT live Stripe Payment Link.

    The webhook never sees these URLs — it grants by `amount_total` — so a
    button wired to the wrong link is invisible until a real customer is charged
    $50 and handed $1 of coins. These pin each product to its exact URL.
    """

    # product key → (live Payment Link, price in cents)
    LINKS = {
        "coins_1000":   ("https://buy.stripe.com/fZufZi6En1FqgIV38eds400",  100),
        "coins_5250":   ("https://buy.stripe.com/dRm9AU6En1Fq64h6kqds401",  500),
        "coins_11500":  ("https://buy.stripe.com/5kQ00k2o75VGeANaAGds402", 1000),
        "coins_25000":  ("https://buy.stripe.com/4gMdRaaUDbg078l7ouds403", 2000),
        "wave-warrior": ("https://buy.stripe.com/cNi6oI3sbfwggIV7ouds404", 1500),
        "ocean-ally":   ("https://buy.stripe.com/5kQcN6geX83O2S5gZ4ds405", 3500),
        "tide-turner":  ("https://buy.stripe.com/00wfZi6EnfwgcsFcIOds406", 5000),
    }

    def setUp(self):
        self.js   = _read("multiplayer", "client", "js", "preview-app.js")
        self.home = _read("index.html")

    def test_no_test_mode_links_survive_anywhere(self):
        """A `test_` link takes fake cards only — real buyers get nothing."""
        for name, blob in (("preview-app.js", self.js), ("index.html", self.home)):
            self.assertNotIn("buy.stripe.com/test_", blob,
                             f"{name} still ships a TEST-mode Payment Link")

    def test_each_link_is_used_exactly_once_per_file(self):
        """Two products sharing one URL = one of them charges the wrong price.

        Coin packs are sold in the in-game store only. The three tiers are sold
        in BOTH places, so they appear once per file — but never twice in one.
        """
        for key, (url, _cents) in self.LINKS.items():
            in_js, in_home = self.js.count(url), self.home.count(url)
            self.assertEqual(in_js, 1, f"{key}: {url} appears {in_js}x in preview-app.js")
            expect_home = 0 if key.startswith("coins_") else 1
            self.assertEqual(in_home, expect_home,
                             f"{key}: {url} appears {in_home}x in index.html, "
                             f"expected {expect_home}")

    def test_all_seven_links_are_distinct(self):
        urls = [u for u, _ in self.LINKS.values()]
        self.assertEqual(len(urls), len(set(urls)), "duplicate URL across products")

    def test_coin_packs_pair_each_price_with_its_link(self):
        """The $N on the card and the link it opens must agree."""
        import re
        rows = re.findall(
            r"\{\s*usd:\s*(\d+),\s*coins:\s*(\d+),.*?link:\s*\"([^\"]+)\"", self.js)
        self.assertEqual(len(rows), 4, "expected 4 coin packs in PHST_COIN_PACKS")
        for usd, coins, url in rows:
            cents = int(usd) * 100
            want_url, want_cents = self.LINKS[f"coins_{coins}"]
            self.assertEqual(url, want_url, f"${usd} pack points at the wrong link")
            self.assertEqual(cents, want_cents, f"${usd} pack price drifted")
            # …and the server must grant exactly those coins for that price.
            self.assertEqual(ms.COIN_PACKS_BY_CENTS[cents], int(coins),
                             f"${usd} link buys {coins} coins on the card but "
                             f"{ms.COIN_PACKS_BY_CENTS[cents]} from the server")

    def test_supporter_tiers_pair_each_price_with_its_link(self):
        import re
        rows = re.findall(
            r"name:\s*\"([^\"]+)\",\s*usd:\s*(\d+),\s*coins:\s*(\d+),[^}]*?link:\s*\"([^\"]+)\"",
            self.js)
        self.assertEqual(len(rows), 3, "expected 3 tiers in PHST_SUPPORTER_TIERS")
        for name, usd, _coins, url in rows:
            tier = name.lower().replace(" ", "-")
            want_url, want_cents = self.LINKS[tier]
            self.assertEqual(url, want_url, f"{name} points at the wrong link")
            self.assertEqual(int(usd) * 100, want_cents, f"{name} price drifted")
            # The price is the ONLY thing the webhook matches on.
            self.assertEqual(ms.SUPPORTER_TIERS_BY_CENTS[want_cents], tier,
                             f"{name}'s ${usd} link grants a different tier server-side")

    def test_marketing_site_uses_the_same_tier_links_as_the_store(self):
        """Two front doors to one product — they must not drift apart."""
        import re
        for label, tier in (("Wave Warrior", "wave-warrior"),
                            ("Ocean Ally",   "ocean-ally"),
                            ("Tide Turner",  "tide-turner")):
            m = re.search(r'href="([^"]+)"[^>]*>Become an? ' + label, self.home)
            self.assertIsNotNone(m, f"no Become-a-{label} button on the marketing site")
            self.assertEqual(m.group(1), self.LINKS[tier][0],
                             f"{label} on index.html points somewhere else")

    def test_every_live_link_resolves_to_a_known_product(self):
        """Reverse check: each price maps back through the real webhook code."""
        for key, (_url, cents) in self.LINKS.items():
            kind, value = ms._reward_for_session(
                {"currency": "usd", "amount_total": cents})
            self.assertIsNotNone(kind, f"{key} (${cents/100}) grants NOTHING")
            if key.startswith("coins_"):
                self.assertEqual((kind, value), ("coins", int(key.split("_")[1])))
            else:
                self.assertEqual((kind, value), ("tier", key))


# ── a tiny stand-in for Firestore, enough for the wall query ────────────────
class _FakeDoc:
    def __init__(self, doc_id, data):
        self.id, self._d = doc_id, data

    def to_dict(self):
        return dict(self._d)


class _FakeQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, field, _op, value):
        return _FakeQuery([d for d in self._docs if d.to_dict().get(field) == value])

    def limit(self, _n):
        return self

    def get(self):
        return list(self._docs)


class _FakeDB:
    def __init__(self, collections):
        self._c = collections

    def collection(self, name):
        return _FakeQuery([_FakeDoc(k, v) for k, v in self._c.get(name, {}).items()])


class TestWallDeduplication(unittest.TestCase):
    """One human, one name. A donor must never appear on the wall twice.

    Someone can pay as a GUEST (marketing site, no sign-in → guestSupporters)
    and later pay signed in (→ supporters/{uid}). The claim merges the guest's
    money onto the supporter doc; if the guest row stays visible, the SAME donor
    stands on the wall twice and their dollars are counted twice.
    """

    def setUp(self):
        self._real = ms._get_firestore
        ms._WALL_CACHE["data"] = None      # the wall is cached for 45s
        ms._WALL_CACHE["at"] = 0.0

    def tearDown(self):
        ms._get_firestore = self._real
        ms._WALL_CACHE["data"] = None
        ms._WALL_CACHE["at"] = 0.0

    def _wall(self, collections):
        ms._get_firestore = lambda: _FakeDB(collections)
        return ms._build_supporter_wall()

    def test_a_claimed_guest_row_is_not_shown_again(self):
        rows = self._wall({
            "supporters": {"uid1": {
                "displayName": "Reef Friend", "status": "approved", "visible": True,
                "totalSpentCents": 5000, "tier": "tide_turner", "wallSize": "large"}},
            "guestSupporters": {"a@b.com": {
                "displayName": "Reef Friend", "status": "approved", "visible": True,
                "totalSpentCents": 2000, "claimStatus": "claimed"}},
        })
        self.assertEqual([r["displayName"] for r in rows], ["Reef Friend"])
        # …and the surviving row carries the COMBINED lifetime total.
        self.assertEqual(rows[0]["amountCents"], 5000)

    def test_an_unclaimed_guest_still_gets_their_name_up(self):
        """A guest who never made an account is a real supporter, not a dupe."""
        rows = self._wall({
            "supporters": {},
            "guestSupporters": {"solo@b.com": {
                "displayName": "Wave Rider", "status": "approved", "visible": True,
                "totalSpentCents": 2500, "claimStatus": "unclaimed"}},
        })
        self.assertEqual([r["displayName"] for r in rows], ["Wave Rider"])

    def test_anonymous_and_pending_names_never_reach_the_wall(self):
        rows = self._wall({
            "supporters": {
                "anon": {"displayName": "Anonymous", "status": "approved",
                         "visible": False, "totalSpentCents": 9000},
                "held": {"displayName": "bad word", "status": "pending_review",
                         "visible": True, "totalSpentCents": 9000},
            },
            "guestSupporters": {},
        })
        self.assertEqual(rows, [])

    def test_wall_is_ordered_by_lifetime_spend(self):
        rows = self._wall({
            "supporters": {
                "small": {"displayName": "Small", "status": "approved",
                          "visible": True, "totalSpentCents": 1000},
                "big":   {"displayName": "Big", "status": "approved",
                          "visible": True, "totalSpentCents": 30000},
            },
            "guestSupporters": {},
        })
        self.assertEqual([r["displayName"] for r in rows], ["Big", "Small"])

    def test_the_wall_never_leaks_private_fields(self):
        rows = self._wall({
            "supporters": {"uid1": {
                "displayName": "Reef Friend", "status": "approved", "visible": True,
                "totalSpentCents": 5000, "email": "secret@example.com",
                "stripeCustomerId": "cus_123", "firebaseUid": "uid1"}},
            "guestSupporters": {},
        })
        self.assertEqual(set(rows[0]), {"displayName", "wallSize", "tier", "amountCents"})


class TestRepeatGiftsGrowOneName(unittest.TestCase):
    """Buying again later must ADD to the same name, not start a second one."""

    def test_lifetime_total_drives_the_tier_not_the_single_payment(self):
        # $15 tier, then $35 tier, then a $20 coin pack = $70 lifetime.
        total = 1500 + 3500 + 2000
        tier, size = ms._supporter_tier_for_total(total)
        self.assertEqual((tier, size), ("tide_turner", "large"))
        # A single $20 pack on its own would only be the small band.
        self.assertEqual(ms._supporter_tier_for_total(2000)[0], "wave_warrior")

    def test_coin_packs_count_toward_the_wall_too(self):
        """Coins are a purchase, so they grow the name like a donation does."""
        for cents in ms.COIN_PACKS_BY_CENTS:
            kind, _ = ms._reward_for_session({"currency": "usd", "amount_total": cents})
            self.assertEqual(kind, "coins")
        # $20 + $20 + $20 crosses the $50 band no single pack reaches.
        self.assertEqual(ms._supporter_tier_for_total(6000)[0], "tide_turner")


if __name__ == "__main__":
    unittest.main(verbosity=2)
