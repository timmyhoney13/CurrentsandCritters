"""test_redeem_codes.py, the code that turns a website donation into coins.

The danger here is not the arithmetic, it is DOUBLE PAYMENT. A purchase can now
be collected two ways, by the emailed code and by the older email match in
_claim_guest_rewards, and both of them credit real currency. So the tests are
mostly about the one lock:

  1. A code pays once. The second attempt is refused, on any account.
  2. A purchase already collected by EMAIL cannot then be collected by CODE,
     and the other way round. One lock, two doors.
  3. A code pays exactly what the webhook would have paid, because it calls the
     same helper (Prestige bonus on packs, the FULL tier grant on tiers).
  4. Stripe's retries compute the SAME code, so one payment never ends up with
     two live codes.
  5. The plaintext code is never stored: `redeemCodes` is keyed by an HMAC.
  6. Guessing is rate-limited, and presenting a REAL code clears the counter.
  7. With no secret configured nothing is minted, rather than a collection
     keyed on plaintext.

    python3 test_redeem_codes.py
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# A secret has to exist before the module reads one, and these tests own the
# process, so set it here rather than in setUp.
os.environ["REDEEM_CODE_SECRET"] = "test-secret-for-redeem-codes"

import redeem_codes as rc  # noqa: E402

# The in-memory Firestore lives next door, same as test_referral_server.py.
from test_level_pass_server import FakeDb, FakeHandler, Parsed  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
#  The real grant helper, injected the way multiplayer_server injects it
# ══════════════════════════════════════════════════════════════════════════
SUPPORTER_TIER_COINS = {"wave-warrior": 7000, "ocean-ally": 15000, "tide-turner": 30000}


def fake_grant_updates(kind, value, user_doc):
    """Stands in for multiplayer_server._reward_grant_updates. Deliberately the
    same SHAPE (updates dict, credited int) so a change to that contract breaks
    here loudly rather than silently paying nothing."""
    stats = (user_doc or {}).get("stats") or {}
    have = int(stats.get("critter_coins") or 0)
    if kind == "coins":
        pack = int(value or 0)
        bonus = int(pack * 0.05 * int((user_doc or {}).get("prestige_level") or 0))
        return {"stats": {"critter_coins": have + pack + bonus}}, pack + bonus
    if kind == "tier":
        coins = SUPPORTER_TIER_COINS.get(value, 0)
        return ({"stats": {"critter_coins": have + coins},
                 "supporter_tier": value,
                 "unlocked_backgrounds": ["/backgrounds/bg-kelp.png"]}, coins)
    return {}, 0


class RedeemBase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        rc.init(
            get_firestore=lambda: self.db,
            verify_token=lambda tok: {"uid": tok[5:]} if tok.startswith("good:") else None,
            grant_updates=fake_grant_updates,
        )
        rc._transactional = lambda: (lambda fn: fn)      # type: ignore[assignment]
        rc._server_timestamp = lambda: "TS"              # type: ignore[assignment]
        rc.reset_rate_limit()

    # ── fixtures ─────────────────────────────────────────────────────────
    def make_user(self, uid="me", coins=0, **extra):
        doc = {"nickname": uid, "stats": {"critter_coins": coins}}
        doc.update(extra)
        self.db.collection("users")._docs[uid] = doc
        return doc

    def make_purchase(self, session_id="cs_test_1", kind="coins", value=1000,
                      *, status="waiting_for_account", name="1,000 Critter Coins",
                      email="buyer@example.com"):
        """Files a reward + its code pointer exactly the way the webhook does."""
        code = rc.derive_code(session_id)
        key = rc.code_key(code)
        self.db.collection("unclaimedRewards")._docs[session_id] = {
            "stripeSessionId": session_id,
            "email": email, "emailLower": email.lower(),
            "rewardName": name, "rewardKind": kind, "rewardValue": value,
            "status": status, "redeemCodeKey": key,
        }
        self.db.collection("redeemCodes")._docs[key] = {"stripeSessionId": session_id}
        return code

    def coins(self, uid="me"):
        doc = self.db.collection("users").document(uid).get().to_dict() or {}
        return int((doc.get("stats") or {}).get("critter_coins") or 0)

    def reward(self, session_id="cs_test_1"):
        return self.db.collection("unclaimedRewards")._docs.get(session_id) or {}


# ══════════════════════════════════════════════════════════════════════════
#  CODE FORMAT
# ══════════════════════════════════════════════════════════════════════════
class CodeFormat(unittest.TestCase):
    def test_a_code_is_the_advertised_length_and_alphabet(self):
        code = rc.make_code()
        self.assertEqual(len(code), rc.CODE_LEN)
        self.assertTrue(all(c in rc.CODE_ALPHABET for c in code))

    def test_the_confusable_characters_are_not_in_the_alphabet(self):
        """0/O and 1/I/L are where a retyped code goes wrong."""
        for ch in "01OIL":
            self.assertNotIn(ch, rc.CODE_ALPHABET)

    def test_normalize_accepts_how_people_actually_paste_a_code(self):
        code = rc.make_code()
        shown = rc.format_code(code)                  # CC-ABCD-EFGH-JKMN-PQRS
        for typed in (shown, shown.lower(), code, code.lower(),
                      "  " + shown + "  ", shown.replace("-", " "),
                      shown.replace("-", "")):
            self.assertEqual(rc.normalize_code(typed), code, typed)

    def test_normalize_refuses_anything_that_is_not_a_code(self):
        for junk in ("", None, "4985", "hello", rc.make_code()[:-1],
                     rc.make_code() + "Z", "O" * rc.CODE_LEN):
            self.assertEqual(rc.normalize_code(junk), "", repr(junk))

    def test_format_puts_the_prefix_on_and_normalize_takes_it_off(self):
        code = rc.make_code()
        self.assertTrue(rc.format_code(code).startswith(rc.CODE_PREFIX + "-"))
        self.assertEqual(rc.normalize_code(rc.format_code(code)), code)


class DerivedCodes(unittest.TestCase):
    """Stripe retries. A random code per delivery would leave one payment with
    two live codes and mail the buyer both."""

    def test_the_same_session_always_derives_the_same_code(self):
        self.assertEqual(rc.derive_code("cs_live_abc"), rc.derive_code("cs_live_abc"))

    def test_different_sessions_derive_different_codes(self):
        seen = {rc.derive_code(f"cs_live_{i}") for i in range(200)}
        self.assertEqual(len(seen), 200)

    def test_a_derived_code_is_a_valid_code(self):
        code = rc.derive_code("cs_live_abc")
        self.assertEqual(rc.normalize_code(code), code)

    def test_the_derivation_depends_on_the_secret(self):
        before = rc.derive_code("cs_live_abc")
        os.environ["REDEEM_CODE_SECRET"] = "a-different-secret"
        try:
            self.assertNotEqual(rc.derive_code("cs_live_abc"), before)
        finally:
            os.environ["REDEEM_CODE_SECRET"] = "test-secret-for-redeem-codes"

    def test_every_symbol_of_the_alphabet_can_appear(self):
        """A mapping bug that only ever emitted the first few symbols would
        still pass every test above."""
        seen = set()
        for i in range(4000):
            seen.update(rc.derive_code(f"cs_live_{i}"))
        self.assertEqual(seen, set(rc.CODE_ALPHABET))


class CodeStorage(unittest.TestCase):
    def test_the_pointer_key_is_not_the_code(self):
        code = rc.make_code()
        key = rc.code_key(code)
        self.assertTrue(key)
        self.assertNotIn(code, key)
        self.assertEqual(len(key), 64)           # sha256 hex

    def test_the_key_is_stable_and_case_insensitive(self):
        code = rc.make_code()
        self.assertEqual(rc.code_key(code), rc.code_key(rc.format_code(code).lower()))

    def test_a_malformed_code_has_no_key(self):
        self.assertEqual(rc.code_key("nope"), "")

    def test_no_secret_means_no_key_and_no_derivation(self):
        """Refusing to mint beats keying the collection on plaintext."""
        saved = os.environ.pop("REDEEM_CODE_SECRET")
        others = {k: os.environ.pop(k) for k in
                  ("ACCOUNT_EMAIL_SECRET", "NEWSLETTER_UNSUBSCRIBE_SECRET", "SESSION_SECRET")
                  if k in os.environ}
        try:
            self.assertFalse(rc.secret_configured())
            self.assertEqual(rc.code_key(rc.make_code()), "")
            self.assertEqual(rc.derive_code("cs_live_abc"), "")
        finally:
            os.environ["REDEEM_CODE_SECRET"] = saved
            os.environ.update(others)


# ══════════════════════════════════════════════════════════════════════════
#  REDEEMING
# ══════════════════════════════════════════════════════════════════════════
class Redeeming(RedeemBase):
    def test_a_good_code_pays_the_purchase_onto_the_account(self):
        self.make_user("me", coins=250)
        code = self.make_purchase(kind="coins", value=1000)
        out = rc.redeem("me", code)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["coins"], 1000)
        self.assertEqual(self.coins("me"), 1250)

    def test_the_reward_record_records_who_spent_it_and_how(self):
        self.make_user("me")
        code = self.make_purchase()
        rc.redeem("me", code)
        rec = self.reward()
        self.assertEqual(rec["status"], "claimed")
        self.assertEqual(rec["claimedByUid"], "me")
        self.assertEqual(rec["claimedVia"], "code")

    def test_a_code_works_however_the_buyer_types_it(self):
        self.make_user("me")
        code = self.make_purchase()
        self.assertTrue(rc.redeem("me", rc.format_code(code).lower()).get("ok"))

    def test_a_tier_code_grants_the_whole_tier_not_just_coins(self):
        self.make_user("me")
        code = self.make_purchase(kind="tier", value="tide-turner",
                                  name="Tide Turner")
        out = rc.redeem("me", code)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["coins"], SUPPORTER_TIER_COINS["tide-turner"])
        doc = self.db.collection("users").document("me").get().to_dict()
        self.assertEqual(doc["supporter_tier"], "tide-turner")
        self.assertIn("unlocked_backgrounds", doc)

    def test_the_prestige_bonus_rides_along_on_a_late_claim(self):
        """Paying first and redeeming later must not cost the buyer the bonus
        the webhook would have applied."""
        self.make_user("me", prestige_level=2)          # +10%
        code = self.make_purchase(kind="coins", value=1000)
        self.assertEqual(rc.redeem("me", code)["coins"], 1100)


class PaidOnlyOnce(RedeemBase):
    """The whole point of the one-lock design."""

    def test_a_code_cannot_be_spent_twice(self):
        self.make_user("me", coins=0)
        code = self.make_purchase(kind="coins", value=1000)
        self.assertTrue(rc.redeem("me", code).get("ok"))
        again = rc.redeem("me", code)
        self.assertFalse(again.get("ok"))
        self.assertEqual(again["error"], "already_claimed")
        self.assertEqual(self.coins("me"), 1000)        # not 2000

    def test_a_code_cannot_be_passed_to_a_second_account(self):
        self.make_user("me")
        self.make_user("you")
        code = self.make_purchase(kind="coins", value=1000)
        self.assertTrue(rc.redeem("me", code).get("ok"))
        self.assertFalse(rc.redeem("you", code).get("ok"))
        self.assertEqual(self.coins("you"), 0)

    def test_a_purchase_already_claimed_by_email_refuses_the_code(self):
        """The email claim in _claim_guest_rewards sets exactly this status.
        If the code ignored it, every website donation could be collected
        twice: once at /claim and once here."""
        self.make_user("me")
        code = self.make_purchase(kind="coins", value=1000, status="claimed")
        out = rc.redeem("me", code)
        self.assertEqual(out["error"], "already_claimed")
        self.assertEqual(self.coins("me"), 0)

    def test_a_purchase_credited_at_checkout_refuses_the_code(self):
        """A buyer matched at checkout is credited on the spot and their record
        is born claimed, so their code is a receipt, not a second payment."""
        self.make_user("me")
        code = self.make_purchase(kind="tier", value="ocean-ally", status="claimed")
        self.assertEqual(rc.redeem("me", code)["error"], "already_claimed")
        self.assertEqual(self.coins("me"), 0)


class Refusals(RedeemBase):
    def test_no_uid_is_unauthorized(self):
        self.assertEqual(rc.redeem("", "whatever")["error"], "unauthorized")

    def test_an_empty_code_asks_for_one(self):
        self.assertEqual(rc.redeem("me", "  ")["error"], "no_code")

    def test_a_malformed_code_is_refused_without_touching_the_database(self):
        self.make_user("me")
        self.assertEqual(rc.redeem("me", "not-a-code")["error"], "bad_code")

    def test_an_unknown_but_well_formed_code_is_not_found(self):
        self.make_user("me")
        self.assertEqual(rc.redeem("me", rc.make_code())["error"], "not_found")

    def test_a_code_pointing_at_a_missing_reward_is_not_found(self):
        self.make_user("me")
        code = self.make_purchase()
        del self.db.collection("unclaimedRewards")._docs["cs_test_1"]
        self.assertEqual(rc.redeem("me", code)["error"], "not_found")

    def test_redeeming_onto_an_account_that_does_not_exist_is_refused(self):
        code = self.make_purchase()
        self.assertEqual(rc.redeem("ghost", code)["error"], "unauthorized")
        self.assertEqual(self.reward()["status"], "waiting_for_account")

    def test_every_refusal_carries_a_sentence_for_the_player(self):
        self.make_user("me")
        for res in (rc.redeem("", "x"), rc.redeem("me", ""),
                    rc.redeem("me", "junk"), rc.redeem("me", rc.make_code())):
            self.assertTrue(str(res.get("message") or "").strip(), res)


class RateLimit(RedeemBase):
    def test_guessing_is_locked_out_after_the_limit(self):
        self.make_user("me")
        for _ in range(rc.REDEEM_MAX_FAILS):
            rc.redeem("me", rc.make_code())
        self.assertEqual(rc.redeem("me", rc.make_code())["error"], "locked")

    def test_the_lock_is_per_account(self):
        self.make_user("me")
        self.make_user("you")
        for _ in range(rc.REDEEM_MAX_FAILS + 1):
            rc.redeem("me", rc.make_code())
        self.assertNotEqual(rc.redeem("you", rc.make_code())["error"], "locked")

    def test_a_real_code_clears_the_counter(self):
        """Somebody who fat-fingered their code four times and then got it
        right must not stay one typo away from a 15-minute lockout."""
        self.make_user("me")
        code = self.make_purchase()
        for _ in range(rc.REDEEM_MAX_FAILS - 1):
            rc.redeem("me", rc.make_code())
        self.assertTrue(rc.redeem("me", code).get("ok"))
        for _ in range(rc.REDEEM_MAX_FAILS - 1):
            rc.redeem("me", rc.make_code())
        self.assertNotEqual(rc.redeem("me", rc.make_code()).get("error"), "locked")

    def test_a_locked_account_is_not_paid_even_with_a_real_code(self):
        self.make_user("me")
        code = self.make_purchase()
        for _ in range(rc.REDEEM_MAX_FAILS):
            rc.redeem("me", rc.make_code())
        self.assertEqual(rc.redeem("me", code)["error"], "locked")
        self.assertEqual(self.coins("me"), 0)


# ══════════════════════════════════════════════════════════════════════════
#  HTTP
# ══════════════════════════════════════════════════════════════════════════
class Endpoint(RedeemBase):
    def post(self, body, path="/api/redeem/code"):
        h = FakeHandler()
        handled = rc.handle_post(h, Parsed(path), body)
        return handled, h

    def test_a_good_token_and_code_pays(self):
        self.make_user("me")
        code = self.make_purchase(kind="coins", value=1000)
        handled, h = self.post({"idToken": "good:me", "code": rc.format_code(code)})
        self.assertTrue(handled)
        self.assertTrue(h.payload.get("ok"), h.payload)
        self.assertEqual(self.coins("me"), 1000)

    def test_a_bad_token_is_401_and_pays_nothing(self):
        self.make_user("me")
        code = self.make_purchase()
        handled, h = self.post({"idToken": "forged", "code": code})
        self.assertTrue(handled)
        self.assertEqual(h.status, 401)
        self.assertEqual(self.coins("me"), 0)

    def test_the_uid_comes_off_the_token_not_the_body(self):
        """A body-supplied uid would let anyone spend a code onto anyone."""
        self.make_user("me")
        self.make_user("victim")
        code = self.make_purchase(kind="coins", value=1000)
        self.post({"idToken": "good:me", "uid": "victim", "code": code})
        self.assertEqual(self.coins("me"), 1000)
        self.assertEqual(self.coins("victim"), 0)

    def test_another_prefix_is_not_ours(self):
        handled, _ = self.post({}, path="/api/referral/redeem")
        self.assertFalse(handled)

    def test_an_unknown_action_under_our_prefix_is_declined(self):
        handled, _ = self.post({"idToken": "good:me"}, path="/api/redeem/nonsense")
        self.assertFalse(handled)


# ══════════════════════════════════════════════════════════════════════════
#  THE EMAIL
# ══════════════════════════════════════════════════════════════════════════
class Email(unittest.TestCase):
    def test_an_unclaimed_purchase_is_told_how_to_redeem(self):
        code = rc.make_code()
        subject, html, text = rc.build_email(
            code=code, reward_name="Tide Turner", already_credited=False)
        for body in (html, text):
            self.assertIn(rc.format_code(code), body)
            self.assertIn("Friends", body)
            self.assertIn("Tide Turner", body)
        self.assertIn("Tide Turner", subject)

    def test_a_purchase_already_credited_does_not_send_them_hunting(self):
        """Telling somebody to go and redeem coins they already have is how a
        support email gets written."""
        subject, html, text = rc.build_email(
            code=rc.make_code(), reward_name="Ocean Ally", already_credited=True)
        for body in (html, text):
            self.assertIn("already been added", body)
            self.assertNotIn("Paste the code", body)

    def test_the_code_survives_html_escaping(self):
        code = rc.make_code()
        _s, html, _t = rc.build_email(code=code, reward_name="x",
                                      already_credited=False)
        self.assertIn(rc.format_code(code), html)

    def test_a_hostile_product_name_cannot_inject_markup(self):
        _s, html, _t = rc.build_email(
            code=rc.make_code(), reward_name="<script>alert(1)</script>",
            already_credited=False)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)




# ══════════════════════════════════════════════════════════════════════════
#  END TO END: the real webhook, then the real redeem
# ══════════════════════════════════════════════════════════════════════════
# Everything above tests one half. This runs the ACTUAL
# _process_stripe_checkout from multiplayer_server against an in-memory
# Firestore, takes the code it derives, and spends it through the ACTUAL
# redeem_codes.redeem. It is the only test that proves the two halves are
# joined: a webhook that files a reward under a key the redeemer never looks
# up would pass every other test in this file.
import copy  # noqa: E402
import importlib  # noqa: E402


def _deep(dst, src):
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep(dst[k], v)
        else:
            dst[k] = v
    return dst


class _Snap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class _Doc:
    def __init__(self, db, path):
        self._db, self.path = db, path
        self.id = path.rsplit("/", 1)[-1]

    def get(self, transaction=None):
        return _Snap(self.id, self._db.docs.get(self.path))

    def set(self, data, merge=False):
        data = copy.deepcopy(data)
        if merge and isinstance(self._db.docs.get(self.path), dict):
            _deep(self._db.docs[self.path], data)
        else:
            self._db.docs[self.path] = data

    def collection(self, name):
        return _Coll(self._db, self.path + "/" + name)


class _Q:
    def __init__(self, rows):
        self._rows = rows

    def limit(self, _n):
        return self

    def get(self):
        return list(self._rows)

    def stream(self):
        return list(self._rows)


class _Coll:
    def __init__(self, db, prefix):
        self._db, self.prefix = db, prefix

    def document(self, doc_id):
        return _Doc(self._db, self.prefix + "/" + doc_id)

    def _rows(self):
        out = []
        for path, data in self._db.docs.items():
            head, _, tail = path.rpartition("/")
            if head == self.prefix and "/" not in tail:
                out.append(_Snap(tail, data))
        return out

    def where(self, field, op, value):
        assert op == "=="
        return _Q([r for r in self._rows() if (r.to_dict() or {}).get(field) == value])

    def stream(self):
        return self._rows()


class _Txn:
    def __init__(self, db):
        self._db = db

    def set(self, ref, data, merge=False):
        ref.set(data, merge=merge)


class _Db:
    def __init__(self):
        self.docs = {}

    def collection(self, name):
        return _Coll(self, name)

    def transaction(self):
        return _Txn(self)


class EndToEnd(unittest.TestCase):
    """The real webhook, then the real redeem."""

    @classmethod
    def setUpClass(cls):
        cls.ms = importlib.import_module("multiplayer_server")

    def setUp(self):
        self.ms = EndToEnd.ms
        self.db = _Db()
        self.sent = []

        from firebase_admin import firestore
        self._real_txnl = firestore.transactional
        firestore.transactional = lambda fn: fn        # the fake txn runs the body once

        self._real_fs = self.ms._get_firestore
        self.ms._get_firestore = lambda: self.db
        self._real_send = self.ms.nl_email.send_email
        self.ms.nl_email.send_email = lambda **kw: self.sent.append(kw) or {"messageId": "x"}

        rc.init(get_firestore=lambda: self.db,
                verify_token=lambda t: {"uid": t[5:]} if t.startswith("good:") else None,
                grant_updates=self.ms._reward_grant_updates)
        rc.reset_rate_limit()

    def tearDown(self):
        from firebase_admin import firestore
        firestore.transactional = self._real_txnl
        self.ms._get_firestore = self._real_fs
        self.ms.nl_email.send_email = self._real_send

    # ── fixtures ─────────────────────────────────────────────────────────
    def event(self, *, session_id="cs_live_e2e", cents=1500, uid=None,
              email="buyer@example.com", tier=None, coins=None):
        meta = {}
        if tier:
            meta["cc_tier"] = tier
        if coins:
            meta["cc_coins"] = coins
        return {"id": "evt_" + session_id, "data": {"object": {
            "id": session_id, "payment_status": "paid", "currency": "usd",
            "amount_total": cents, "customer": "cus_1",
            "customer_details": {"email": email},
            "client_reference_id": uid or None,
            "metadata": meta, "custom_fields": []}}}

    def make_account(self, uid="me", coins=0, email=""):
        self.db.docs[f"users/{uid}"] = {
            "nickname": uid, "email": email, "stats": {"critter_coins": coins}}

    def coins(self, uid="me"):
        return int(((self.db.docs.get(f"users/{uid}") or {}).get("stats") or {})
                   .get("critter_coins") or 0)

    # ── the tests ────────────────────────────────────────────────────────
    def test_a_website_donation_reaches_an_account_through_the_code(self):
        """The whole point. Nobody was signed in, Stripe knows only an email,
        and the buyer still ends up with their tier."""
        self.ms._process_stripe_checkout(self.event(cents=1500))
        rec = self.db.docs.get("unclaimedRewards/cs_live_e2e")
        self.assertIsNotNone(rec, "the webhook filed no reward record")
        self.assertEqual(rec["status"], "waiting_for_account")
        self.assertTrue(rec.get("redeemCodeKey"), "no code was minted")

        # The code that was emailed really does resolve to that record.
        self.assertEqual(len(self.sent), 1, self.sent)
        code = rc.derive_code("cs_live_e2e")
        self.assertIn(rc.format_code(code), self.sent[0]["text_body"])
        self.assertEqual(self.sent[0]["to_email"], "buyer@example.com")
        self.assertFalse(self.sent[0]["is_bulk"])

        # …and spending it pays the FULL tier onto a brand-new account.
        self.make_account("me")
        out = rc.redeem("me", rc.format_code(code))
        self.assertTrue(out.get("ok"), out)
        want = self.ms.SUPPORTER_TIER_GRANTS["wave-warrior"]
        self.assertEqual(self.coins("me"), want["coins"])
        acct = self.db.docs["users/me"]
        self.assertEqual(acct["stats"]["total_xp"], want["bonus_xp"])
        self.assertEqual(acct["supporter_tier"], "wave-warrior")
        self.assertEqual(acct["stats"]["level"],
                         self.ms._level_progress_for_total_xp(want["bonus_xp"])[0])

    def test_a_coin_pack_bought_on_the_website_reaches_an_account(self):
        self.ms._process_stripe_checkout(self.event(session_id="cs_p", cents=2000))
        self.make_account("me", coins=100)
        out = rc.redeem("me", rc.derive_code("cs_p"))
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(self.coins("me"), 100 + self.ms.COIN_PACKS_BY_CENTS[2000])

    def test_a_buyer_signed_in_at_checkout_is_paid_on_the_spot(self):
        """…and their record is born claimed, so the code cannot pay twice."""
        self.make_account("me", coins=0)
        self.ms._process_stripe_checkout(self.event(session_id="cs_m", cents=3500, uid="me"))
        want = self.ms.SUPPORTER_TIER_GRANTS["ocean-ally"]
        self.assertEqual(self.coins("me"), want["coins"])
        rec = self.db.docs["unclaimedRewards/cs_m"]
        self.assertEqual(rec["status"], "claimed")
        self.assertEqual(rec["claimedVia"], "checkout")
        before = self.coins("me")
        self.assertEqual(rc.redeem("me", rc.derive_code("cs_m"))["error"], "already_claimed")
        self.assertEqual(self.coins("me"), before)

    def test_that_buyers_email_says_it_already_landed(self):
        self.make_account("me")
        self.ms._process_stripe_checkout(self.event(session_id="cs_m2", cents=3500, uid="me"))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("already been added", self.sent[0]["text_body"])
        self.assertNotIn("Paste the code", self.sent[0]["text_body"])

    def test_the_email_claim_and_the_code_cannot_both_pay(self):
        """The two doors, one lock. This is the double-payment case."""
        self.ms._process_stripe_checkout(self.event(session_id="cs_two", cents=1500))
        self.make_account("me", email="buyer@example.com")
        # Door 1: the older email match.
        self.ms._claim_guest_rewards("me", "buyer@example.com")
        paid = self.coins("me")
        self.assertGreater(paid, 0, "the email claim paid nothing")
        # Door 2: the code, on the same purchase.
        self.assertEqual(rc.redeem("me", rc.derive_code("cs_two"))["error"],
                         "already_claimed")
        self.assertEqual(self.coins("me"), paid)

    def test_the_code_first_then_the_email_claim_also_pays_once(self):
        """The same lock from the other side."""
        self.ms._process_stripe_checkout(self.event(session_id="cs_rev", cents=1500))
        self.make_account("me", email="buyer@example.com")
        self.assertTrue(rc.redeem("me", rc.derive_code("cs_rev")).get("ok"))
        paid = self.coins("me")
        self.ms._claim_guest_rewards("me", "buyer@example.com")
        self.assertEqual(self.coins("me"), paid)

    def test_a_stripe_retry_does_not_mint_a_second_code_or_a_second_email(self):
        ev = self.event(session_id="cs_retry", cents=1500)
        self.ms._process_stripe_checkout(ev)
        self.ms._process_stripe_checkout(ev)          # Stripe redelivers
        self.ms._process_stripe_checkout(ev)
        keys = [p for p in self.db.docs if p.startswith("redeemCodes/")]
        self.assertEqual(len(keys), 1, keys)
        self.assertEqual(len(self.sent), 1, self.sent)

    def test_a_retry_never_pays_twice(self):
        self.make_account("me")
        ev = self.event(session_id="cs_rt", cents=3500, uid="me")
        self.ms._process_stripe_checkout(ev)
        once = self.coins("me")
        self.ms._process_stripe_checkout(ev)
        self.assertEqual(self.coins("me"), once)

    def test_an_unpaid_session_mints_nothing_and_mails_nobody(self):
        ev = self.event(session_id="cs_unpaid", cents=1500)
        ev["data"]["object"]["payment_status"] = "unpaid"
        self.ms._process_stripe_checkout(ev)
        self.assertEqual([p for p in self.db.docs if p.startswith("redeemCodes/")], [])
        self.assertEqual(self.sent, [])

    def test_a_failing_mail_server_does_not_lose_the_purchase(self):
        """The reward is committed before the email is attempted. If sending
        threw and that took the webhook down, Stripe would retry a settled
        payment and the buyer would be left with nothing."""
        def boom(**_kw):
            raise RuntimeError("smtp down")
        self.ms.nl_email.send_email = boom
        self.ms._process_stripe_checkout(self.event(session_id="cs_mailfail", cents=1500))
        rec = self.db.docs.get("unclaimedRewards/cs_mailfail")
        self.assertIsNotNone(rec)
        # …and the code still works, because it is derived, not stored.
        self.make_account("me")
        self.assertTrue(rc.redeem("me", rc.derive_code("cs_mailfail")).get("ok"))

    def test_the_supporter_wall_still_gets_its_row(self):
        """Codes were added beside the wall pipeline, not on top of it."""
        self.ms._process_stripe_checkout(self.event(session_id="cs_wall", cents=5000))
        guests = [p for p in self.db.docs if p.startswith("guestSupporters/")]
        self.assertTrue(guests, "no supporter row was recorded")

    def test_the_audit_marker_is_still_written(self):
        self.ms._process_stripe_checkout(self.event(session_id="cs_audit", cents=1500))
        marker = self.db.docs.get("stripe_events/evt_cs_audit")
        self.assertIsNotNone(marker)
        self.assertEqual(marker["kind"], "tier")


if __name__ == "__main__":
    unittest.main(verbosity=2)
