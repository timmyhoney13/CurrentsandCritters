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


# The exact questions on the shop's "Game purchase info" Add to Cart form.
Q_EMAIL = "Email you play Currents and Critters on"
Q_WALL  = "Do you want to be added to the Supporter Reef Wall?"
Q_NAME  = "Name for the Supporter Reef Wall"
Q_USER  = "Game username (only if your account has no email)"


def _form(email="", wall="", name="", user=""):
    rows = [{"label": Q_EMAIL, "value": email}, {"label": Q_WALL, "value": wall},
            {"label": Q_NAME, "value": name}, {"label": Q_USER, "value": user}]
    return [r for r in rows if r["value"]]


def _with_form(order, **kw):
    for li in order["lineItems"]:
        li["customizations"] = _form(**kw)
    return order


class FormAnswers(unittest.TestCase):
    def test_each_question_lands_in_its_own_slot(self):
        got = sq.form_answers({}, {"customizations": _form(
            email="me@x.com", wall="Yes, show my name", name="Reef Fan", user="finn")})
        self.assertEqual(got, {"play_email": "me@x.com", "username": "finn",
                               "wall": "name", "wall_name": "Reef Fan"})

    def test_the_username_question_is_not_read_as_the_email(self):
        """Its label says 'has no email'; it must still count as the username."""
        got = sq.form_answers({}, {"customizations": _form(user="finn")})
        self.assertEqual((got["username"], got["play_email"]), ("finn", ""))

    def test_wall_choices(self):
        self.assertEqual(sq.wall_choice("Yes, show my name"), "name")
        self.assertEqual(sq.wall_choice("Yes, but show me as Anonymous"), "anonymous")
        self.assertEqual(sq.wall_choice("No, don't add me"), "no")
        self.assertEqual(sq.wall_choice("No, don\u2019t add me".replace("\u2019", "'")), "no")
        self.assertEqual(sq.wall_choice(""), "")
        # "Anonymous" contains "no": it must not read as a no.
        self.assertNotEqual(sq.wall_choice("Anonymous"), "no")
        # privacy wins a tie
        self.assertEqual(sq.wall_choice("Yes, show my name, No, don't add me"), "no")
        self.assertEqual(sq.wall_choice("Yes, show my name, Yes, but show me as Anonymous"),
                         "anonymous")

    def test_line_form_beats_the_order_form(self):
        order = {"formSubmission": [{"label": Q_EMAIL, "value": "order@x.com"}]}
        self.assertEqual(sq.form_answers(order, {"customizations": _form(email="line@x.com")})
                         ["play_email"], "line@x.com")
        self.assertEqual(sq.form_answers(order, {})["play_email"], "order@x.com")

    def test_a_non_email_in_the_email_box_is_ignored(self):
        self.assertEqual(sq.form_answers({}, {"customizations": _form(email="finn")})
                         ["play_email"], "")


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


class Matching(unittest.TestCase):
    """Who is credited, from the form's answers."""

    def test_play_email_beats_the_checkout_email(self):
        accts = {"play@x.com": "player", "buyer@example.com": "parent"}
        evs = sq.events_for_order(_with_form(_order(), email="play@x.com"),
                                  find_uid_by_email=lambda e: accts.get(e.lower()))
        self.assertEqual(evs[0]["data"]["object"]["client_reference_id"], "player")

    def test_unmatched_play_email_falls_back_to_checkout_email(self):
        evs = sq.events_for_order(_with_form(_order(), email="typo@x.com"),
                                  find_uid_by_email=lambda e: "me" if e == "buyer@example.com" else None)
        self.assertEqual(evs[0]["data"]["object"]["client_reference_id"], "me")

    def test_typed_username_beats_the_checkout_email(self):
        evs = sq.events_for_order(
            _with_form(_order(), email="nobody@x.com", user="Finn"),
            find_uid_by_email=lambda e: "parent" if e == "buyer@example.com" else None,
            find_uid_by_username=lambda u: "finn-uid" if u == "Finn" else None)
        self.assertEqual(evs[0]["data"]["object"]["client_reference_id"], "finn-uid")

    def test_two_packs_for_two_players_in_one_cart(self):
        order = _order()
        order["lineItems"] = [
            {"id": "a", "sku": "CC-COINS-1000", "productName": "1,000 Critter Coins", "quantity": 1,
             "unitPricePaid": {"currency": "USD", "value": "1.00"},
             "customizations": _form(email="one@x.com", wall="No, don't add me")},
            {"id": "b", "sku": "CC-COINS-1000", "productName": "1,000 Critter Coins", "quantity": 1,
             "unitPricePaid": {"currency": "USD", "value": "1.00"},
             "customizations": _form(email="two@x.com", wall="Yes, show my name")},
        ]
        evs = sq.events_for_order(order, find_uid_by_email=lambda e: e.split("@")[0])
        self.assertEqual([e["data"]["object"]["client_reference_id"] for e in evs], ["one", "two"])


class ReefWall(EndToEnd):
    """The Reef Wall answer, through the real fulfilment, onto the wall."""

    def wall_doc(self, uid="me"):
        return self.db.docs[f"supporters/{uid}"]

    def account(self, uid="me", email="buyer@example.com", coins=0, username="ReefFinn"):
        super().account(uid=uid, email=email, coins=coins)
        self.db.docs[f"users/{uid}"]["username"] = username

    def test_show_my_name(self):
        self.account()
        self.poll([_with_form(_order(), email="buyer@example.com",
                              wall="Yes, show my name", name="Tim the Turtle")])
        d = self.wall_doc()
        self.assertEqual((d["displayName"], d["anonymous"], d["visible"], d["status"]),
                         ("Tim the Turtle", False, True, "approved"))
        self.assertEqual(self.coins(), 25000)

    def test_show_my_name_left_blank_uses_the_game_username(self):
        self.account()
        self.poll([_with_form(_order(), email="buyer@example.com", wall="Yes, show my name")])
        d = self.wall_doc()
        self.assertEqual((d["displayName"], d["visible"]), ("ReefFinn", True))

    def test_anonymous_is_on_the_wall_as_anonymous(self):
        self.account()
        self.poll([_with_form(_order(), email="buyer@example.com",
                              wall="Yes, but show me as Anonymous", name="Ignored Name")])
        d = self.wall_doc()
        self.assertEqual((d["displayName"], d["visible"], d["status"]),
                         ("Anonymous", True, "approved"))

    def test_no_is_recorded_but_never_shown(self):
        self.account()
        self.poll([_with_form(_order(), email="buyer@example.com", wall="No, don't add me",
                              name="Should Not Show")])
        d = self.wall_doc()
        self.assertEqual((d["visible"], d["anonymous"]), (False, True))
        self.assertEqual(d["totalSpentCents"], 2000)
        self.assertEqual(self.coins(), 25000, "saying no to the wall must not cost the coins")

    def test_the_wall_lists_shown_buyers_only(self):
        self.account(uid="a", email="a@x.com")
        self.account(uid="b", email="b@x.com")
        self.account(uid="c", email="c@x.com")
        self.poll([
            _with_form(_order("o1"), email="a@x.com", wall="Yes, show my name", name="Alpha"),
            _with_form(_order("o2"), email="b@x.com", wall="Yes, but show me as Anonymous"),
            _with_form(_order("o3"), email="c@x.com", wall="No, don't add me"),
        ])
        real = self.ms._get_firestore
        try:
            self.ms._get_firestore = lambda: _WhereDb(self.db)
            names = sorted(r["displayName"] for r in self.ms._build_supporter_wall())
        finally:
            self.ms._get_firestore = real
        self.assertEqual(names, ["Alpha", "Anonymous"])

    def test_a_guest_who_said_yes_is_on_the_wall_too(self):
        self.poll([_with_form(_order(email="new@x.com"), email="new@x.com",
                              wall="Yes, show my name", name="Newcomer")])
        d = self.db.docs["guestSupporters/new@x.com"]
        self.assertEqual((d["displayName"], d["visible"]), ("Newcomer", True))
        self.assertEqual(len(self.sent), 1, "the guest still gets their code")
        self.assertEqual(self.sent[0]["to_email"], "new@x.com")


class TypedUsername(unittest.TestCase):
    """The "Game username" box finds username-and-password accounts too."""

    @classmethod
    def setUpClass(cls):
        cls.ms = importlib.import_module("multiplayer_server")

    def setUp(self):
        from firebase_admin import auth as fb_auth
        self.fb_auth = fb_auth
        self.db = _Db()
        self._real_fs = self.ms._get_firestore
        self.ms._get_firestore = lambda: self.db
        self._real_get = fb_auth.get_user_by_email
        self._real_q = self.ms._find_uid_by_username
        self.ms._find_uid_by_username = lambda db, u: {"reeffinn": "claimed-uid"}.get(u)
        logins = {"mermaid_92@" + self.ms.CC_LOGIN_DOMAIN: "pw-uid"}

        def get_user_by_email(addr):
            if addr not in logins:
                raise fb_auth.UserNotFoundError("nope")
            return type("U", (), {"uid": logins[addr], "email_verified": False})()
        fb_auth.get_user_by_email = get_user_by_email
        self.db.docs["users/pw-uid"] = {"nickname": "Mermaid"}

    def tearDown(self):
        self.ms._get_firestore = self._real_fs
        self.fb_auth.get_user_by_email = self._real_get
        self.ms._find_uid_by_username = self._real_q

    def test_claimed_username(self):
        self.assertEqual(self.ms._find_uid_by_typed_username("ReefFinn"), "claimed-uid")

    def test_login_name_of_a_password_account(self):
        self.assertEqual(self.ms._find_uid_by_typed_username(" Mermaid_92 "), "pw-uid")

    def test_unknown_name_is_nobody(self):
        self.assertIsNone(self.ms._find_uid_by_typed_username("nobody_here"))
        self.assertIsNone(self.ms._find_uid_by_typed_username("has spaces in it"))
        self.assertIsNone(self.ms._find_uid_by_typed_username(""))


class _WhereDb:
    """Just enough of .collection(x).where(f, "==", v).limit(n).get() for the wall."""

    def __init__(self, db):
        self.db = db

    def collection(self, name):
        db = self.db

        class _Q:
            def where(self, f, op, v):
                self.f, self.v = f, v
                return self

            def limit(self, n):
                return self

            def get(self):
                out = []
                for path, d in db.docs.items():
                    parts = path.split("/")
                    if len(parts) == 2 and parts[0] == name and d.get(self.f) == self.v:
                        out.append(type("S", (), {"id": parts[1], "to_dict": lambda s, d=d: dict(d)})())
                return out
        return _Q()


if __name__ == "__main__":
    unittest.main()
