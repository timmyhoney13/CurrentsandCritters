"""Squarespace shop orders → game accounts (squarespace_store.py).

Drives the REAL fulfilment (multiplayer_server._process_stripe_checkout) on the
same in-memory Firestore stand-in test_redeem_codes.py uses, so what is proved
here is what a Squarespace buyer actually receives.
"""
import importlib
import os
import unittest
from datetime import datetime, timezone

import squarespace_store as sq
import redeem_codes as rc
from test_redeem_codes import _Db


def _order(oid="64aa01", *, sku="CC-COINS-25000", name="25,000 Critter Coins",
           price="20.00", qty=1, email="buyer@example.com", state="PAID",
           fulfillment="PENDING", testmode=False, form=None, line_id="li1"):
    return {
        "id": oid, "orderNumber": "1001", "customerEmail": email,
        "paymentState": state, "fulfillmentStatus": fulfillment,
        "testmode": testmode, "formSubmission": form,
        "grandTotal": {"currency": "USD", "value": price},
        "refundedTotal": {"currency": "USD", "value": "0.00"},
        "lineItems": [{"id": line_id, "sku": sku, "productName": name,
                       "quantity": qty,
                       "unitPricePaid": {"currency": "USD", "value": price}}],
    }


class Mapping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ms = importlib.import_module("multiplayer_server")
        sq.init(get_firestore=lambda: None, fulfil=lambda e: "x",
                find_uid_by_email=lambda e: None,
                coin_packs_by_cents=ms.COIN_PACKS_BY_CENTS,
                tiers_by_cents=ms.SUPPORTER_TIERS_BY_CENTS,
                username_label=ms.CF_USERNAME_LABEL)

    def test_sku_wins(self):
        self.assertEqual(sq.reward_for_line_item({"sku": "cc-tier-tsunami"}),
                         ("tier", "tsunami", 10000))

    def test_name_fallback_for_a_pay_link(self):
        self.assertEqual(sq.reward_for_line_item({"productName": "1000 Critter Coins"}),
                         ("coins", 1000, 100))
        self.assertEqual(sq.reward_for_line_item({"productName": "Ocean Ally (Supporter Tier)"}),
                         ("tier", "ocean-ally", 3500))

    def test_a_plush_is_not_ours_to_deliver(self):
        self.assertIsNone(sq.reward_for_line_item(
            {"sku": "SQ123", "productName": "Whale Shark Weighted Plush"}))
        self.assertIsNone(sq.reward_for_line_item({"productName": "7 Critter Coins"}))

    def test_unpaid_cancelled_and_test_orders_grant_nothing(self):
        self.assertEqual(sq.events_for_order(_order(state="PENDING")), [])
        self.assertEqual(sq.events_for_order(_order(state="REFUNDED")), [])
        self.assertEqual(sq.events_for_order(_order(fulfillment="CANCELED")), [])
        self.assertEqual(sq.events_for_order(_order(testmode=True)), [])
        self.assertEqual(len(sq.events_for_order(_order(testmode=True), allow_test=True)), 1)

    def test_each_unit_is_its_own_stable_grant(self):
        evs = sq.events_for_order(_order(qty=3))
        self.assertEqual([e["id"] for e in evs],
                         ["sqsp_64aa01_li1_0", "sqsp_64aa01_li1_1", "sqsp_64aa01_li1_2"])
        self.assertEqual([e["id"] for e in sq.events_for_order(_order(qty=3))],
                         [e["id"] for e in evs], "ids must not change between polls")
        s = evs[0]["data"]["object"]
        self.assertEqual((s["amount_total"], s["payment_status"]), (2000, "paid"))
        self.assertEqual(s["metadata"]["cc_coins"], 25000)

    def test_email_match_and_typed_username_ride_along(self):
        evs = sq.events_for_order(
            _order(form=[{"label": "Game username", "value": "Finn"}]),
            find_uid_by_email=lambda e: "uid-1" if e == "buyer@example.com" else None)
        s = evs[0]["data"]["object"]
        self.assertEqual(s["client_reference_id"], "uid-1")
        self.assertEqual(s["custom_fields"][0]["text"]["value"], "Finn")

    def test_a_failing_lookup_raises_so_the_order_is_retried(self):
        def boom(_e):
            raise RuntimeError("auth down")
        with self.assertRaises(RuntimeError):
            sq.events_for_order(_order(), find_uid_by_email=boom)


class EndToEnd(unittest.TestCase):
    """A Squarespace order, through the real fulfilment, onto an account."""

    @classmethod
    def setUpClass(cls):
        cls.ms = importlib.import_module("multiplayer_server")

    def setUp(self):
        self.ms = EndToEnd.ms
        self.db = _Db()
        self.sent = []
        self.verified = {}                    # email → uid

        from firebase_admin import firestore
        self._real_txnl = firestore.transactional
        firestore.transactional = lambda fn: fn
        self._real_fs = self.ms._get_firestore
        self.ms._get_firestore = lambda: self.db
        self._real_send = self.ms.nl_email.send_email
        self.ms.nl_email.send_email = lambda **kw: self.sent.append(kw) or {"messageId": "x"}
        rc.init(get_firestore=lambda: self.db, verify_token=lambda t: None,
                grant_updates=self.ms._reward_grant_updates)

        sq.init(get_firestore=lambda: self.db,
                fulfil=self.ms._process_stripe_checkout,
                find_uid_by_email=lambda e: self.verified.get(e.lower()),
                coin_packs_by_cents=self.ms.COIN_PACKS_BY_CENTS,
                tiers_by_cents=self.ms.SUPPORTER_TIERS_BY_CENTS,
                username_label=self.ms.CF_USERNAME_LABEL)
        os.environ["SQUARESPACE_API_KEY"] = "test-key"

    def tearDown(self):
        from firebase_admin import firestore
        firestore.transactional = self._real_txnl
        self.ms._get_firestore = self._real_fs
        self.ms.nl_email.send_email = self._real_send
        os.environ.pop("SQUARESPACE_API_KEY", None)

    def account(self, uid="me", email="buyer@example.com", coins=0):
        self.db.docs[f"users/{uid}"] = {"nickname": uid, "email": email,
                                        "stats": {"critter_coins": coins}}
        self.verified[email.lower()] = uid

    def coins(self, uid="me"):
        return int(((self.db.docs.get(f"users/{uid}") or {}).get("stats") or {})
                   .get("critter_coins") or 0)

    def poll(self, orders, now=None):
        pages = {"n": 0}

        def http_get(params, key):
            self.assertEqual(key, "test-key")
            pages["n"] += 1
            return {"result": orders, "pagination": {"hasNextPage": False}}
        return sq.poll_once(now=now or datetime(2026, 11, 15, tzinfo=timezone.utc),
                            http_get=http_get)

    def test_a_matched_buyer_is_paid_once_however_often_we_poll(self):
        self.account(coins=100)
        for _ in range(3):
            res = self.poll([_order()])
            self.assertTrue(res["ok"], res)
        self.assertEqual(self.coins(), 100 + 25000)

    def test_a_tier_lands_with_its_perks(self):
        self.account()
        self.poll([_order(sku="CC-TIER-OCEAN-ALLY", name="Ocean Ally (Supporter Tier)",
                          price="35.00")])
        self.assertEqual(self.db.docs["users/me"]["supporter_tier"], "ocean-ally")
        self.assertEqual(self.coins(), self.ms.SUPPORTER_TIER_GRANTS["ocean-ally"]["coins"])

    def test_an_unmatched_buyer_gets_a_code_that_pays_in_full(self):
        self.poll([_order(email="stranger@example.com")])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["to_email"], "stranger@example.com")
        rec = self.db.docs["unclaimedRewards/sqsp_64aa01_li1_0"]
        self.assertEqual(rec["status"], "waiting_for_account")

    def test_an_account_made_between_polls_does_not_double_pay(self):
        """First poll: no account, filed as a guest. The buyer signs up, the
        same order is polled again and now MATCHES. That must be a no-op."""
        self.poll([_order()])
        self.account(coins=0)
        self.poll([_order()])
        # The second sighting is a duplicate; the ONE payout arrives through
        # the guest auto-merge for the account's own email…
        self.assertEqual(self.coins(), 25000)
        self.poll([_order()])
        self.assertEqual(self.coins(), 25000, "a later sighting paid the order again")
        # …and the code mailed after the first poll can no longer pay it twice.
        out = rc.redeem("me", rc.derive_code("sqsp_64aa01_li1_0"))
        self.assertEqual(out.get("error"), "already_claimed", out)
        self.assertEqual(self.coins(), 25000)

    def test_quantity_three_pays_three_packs(self):
        self.account()
        self.poll([_order(sku="CC-COINS-1000", name="1,000 Critter Coins",
                          price="1.00", qty=3)])
        self.assertEqual(self.coins(), 3000)

    def test_the_window_advances_and_the_first_poll_looks_back(self):
        os.environ["SQUARESPACE_LOOKBACK_DAYS"] = "30"
        try:
            res = self.poll([])
        finally:
            os.environ.pop("SQUARESPACE_LOOKBACK_DAYS", None)
        self.assertTrue(res["window"][0].startswith("2026-10-16"))
        self.assertEqual(self.db.docs["meta/squarespaceSync"]["polledUntil"],
                         "2026-11-15T00:00:00.000Z")
        res2 = self.poll([], now=datetime(2026, 11, 15, 0, 5, tzinfo=timezone.utc))
        self.assertEqual(res2["window"][0], "2026-11-14T23:50:00.000Z")

    def test_pagination_sends_the_cursor_alone(self):
        calls = []

        def http_get(params, key):
            calls.append(dict(params))
            if "cursor" in params:
                return {"result": [], "pagination": {"hasNextPage": False}}
            return {"result": [], "pagination": {"hasNextPage": True, "nextPageCursor": "c2"}}
        sq.fetch_orders(datetime(2026, 11, 1, tzinfo=timezone.utc),
                        datetime(2026, 11, 2, tzinfo=timezone.utc), key="k", http_get=http_get)
        self.assertEqual(calls[1], {"cursor": "c2"})
        self.assertEqual(set(calls[0]), {"modifiedAfter", "modifiedBefore"})

    def test_a_failure_does_not_advance_the_window(self):
        self.account()
        real = sq._deps["fulfil"]
        sq._deps["fulfil"] = lambda e: (_ for _ in ()).throw(RuntimeError("firestore down"))
        try:
            res = self.poll([_order()])
        finally:
            sq._deps["fulfil"] = real
        self.assertFalse(res["ok"])
        self.assertNotIn("meta/squarespaceSync", self.db.docs)
        self.assertTrue(self.poll([_order()])["ok"])
        self.assertEqual(self.coins(), 25000)


if __name__ == "__main__":
    unittest.main()
