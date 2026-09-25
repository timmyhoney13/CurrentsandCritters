#!/usr/bin/env python3
"""The monthly donation report, and the 5% it owes to ocean conservation.

Two mistakes this report must never make, because both of them are money:

  • counting a payment twice, which would over-state what we owe and, worse,
    put a donor on the books for money they only gave once;
  • counting a cent less than the pledge, which would under-donate against a
    promise printed on the Store and on the home page.

Everything below is one of those two, or a boundary of the month window.
"""
from __future__ import annotations

import io
import unittest
from datetime import datetime, timedelta, timezone

import multiplayer_server as ms
import donation_report as dr


# ── a Firestore stand-in that has subcollections ────────────────────────────
class _Pay:
    def __init__(self, pid, data):
        self.id, self._d = pid, data

    def to_dict(self):
        return dict(self._d)


class _Ref:
    def __init__(self, payments):
        self._p = payments

    def collection(self, _name):
        return self

    def limit(self, _n):
        return self

    def get(self):
        return [_Pay(k, v) for k, v in self._p.items()]


class _Doc:
    def __init__(self, doc_id, data, payments):
        self.id, self._d = doc_id, data
        self.reference = _Ref(payments or {})

    def to_dict(self):
        return dict(self._d)


class _Coll:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, _n):
        return self

    def get(self):
        return list(self._docs)


class _DB:
    def __init__(self, tree):
        self._t = tree

    def collection(self, name):
        rows = self._t.get(name, {})
        return _Coll([_Doc(k, v.get("doc", {}), v.get("payments", {}))
                      for k, v in rows.items()])


SEP = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def pay(cents, at=SEP, status="paid", product="Ocean Ally"):
    return {"amountCents": cents, "createdAt": at,
            "paymentStatus": status, "productName": product}


class _Base(unittest.TestCase):
    def setUp(self):
        self._real = ms._get_firestore

    def tearDown(self):
        ms._get_firestore = self._real

    def run_report(self, tree, month="2026-09"):
        ms._get_firestore = lambda: _DB(tree)
        return ms._admin_donation_report(month)


# ══════════════════════════════════════════════════════════════════════════
class TestTheMonthWindow(unittest.TestCase):
    def test_a_named_month_is_the_whole_month_and_nothing_else(self):
        start, end, label = ms._month_window_utc("2026-09")
        self.assertEqual(label, "2026-09")
        self.assertEqual(start, datetime(2026, 9, 1, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 10, 1, tzinfo=timezone.utc))

    def test_december_rolls_into_the_next_year(self):
        """The off-by-one that would make December return month 13."""
        start, end, _ = ms._month_window_utc("2026-12")
        self.assertEqual(start, datetime(2026, 12, 1, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2027, 1, 1, tzinfo=timezone.utc))

    def test_no_month_means_the_month_we_are_in(self):
        now = datetime.now(timezone.utc)
        _, _, label = ms._month_window_utc("")
        self.assertEqual(label, now.strftime("%Y-%m"))

    def test_a_month_that_is_not_a_month_is_refused(self):
        for bad in ("2026", "not-a-month", "2026-13", "2026-00"):
            with self.assertRaises(ValueError, msg=bad):
                ms._month_window_utc(bad)


# ══════════════════════════════════════════════════════════════════════════
class TestTheFivePercent(_Base):
    def test_the_share_is_five_percent_of_what_came_in(self):
        d = self.run_report({"supporters": {"u1": {"doc": {}, "payments": {"a": pay(10000)}}}})
        self.assertEqual(d["grossCents"], 10000)
        self.assertEqual(d["conservationShareCents"], 500)
        self.assertEqual(d["sharePct"], 5)

    def test_the_pledge_on_the_page_is_the_pledge_in_the_code(self):
        """The Store and the home page both print this number."""
        self.assertEqual(ms.CONSERVATION_SHARE_PCT, 5)
        pledge = ("%d%% of every purchase supports ocean conservation"
                  % ms.CONSERVATION_SHARE_PCT)
        for path in ("multiplayer/client/js/preview-app.js", "index.html"):
            with io.open(path, encoding="utf-8") as fh:
                self.assertIn(pledge, fh.read(), path)

    def test_a_fraction_of_a_cent_rounds_UP_never_down(self):
        """$15.01 owes 75.05c. Donating 75c is breaking the promise by a cent."""
        d = self.run_report({"supporters": {"u1": {"doc": {}, "payments": {"a": pay(1501)}}}})
        self.assertEqual(d["conservationShareCents"], 76)

    def test_nothing_in_means_nothing_owed(self):
        d = self.run_report({"supporters": {}})
        self.assertEqual((d["grossCents"], d["conservationShareCents"], d["paymentCount"]),
                         (0, 0, 0))


# ══════════════════════════════════════════════════════════════════════════
class TestMoneyIsCountedExactlyOnce(_Base):
    """The claim flow COPIES a guest's payments onto the supporter doc under the
    same Stripe session id, and leaves the guest row in place marked claimed.
    Summing both collections naively counts that money twice."""

    def test_a_claimed_guest_payment_is_not_counted_twice(self):
        d = self.run_report({
            "supporters": {"u1": {"doc": {"displayName": "Ada"},
                                  "payments": {"cs_1": pay(3500)}}},
            "guestSupporters": {"g1": {"doc": {"displayName": "Ada",
                                               "claimStatus": "claimed"},
                                       "payments": {"cs_1": pay(3500)}}},
        })
        self.assertEqual(d["grossCents"], 3500)
        self.assertEqual(d["paymentCount"], 1)
        self.assertEqual(d["conservationShareCents"], 175)

    def test_dedup_holds_even_if_a_claimed_row_is_not_flagged(self):
        """Belt and braces: rows claimed before claimStatus was written still
        share the session id, and the id is what dedups."""
        d = self.run_report({
            "supporters": {"u1": {"doc": {}, "payments": {"cs_1": pay(3500)}}},
            "guestSupporters": {"g1": {"doc": {}, "payments": {"cs_1": pay(3500)}}},
        })
        self.assertEqual(d["grossCents"], 3500)

    def test_an_unclaimed_guest_still_counts(self):
        d = self.run_report({
            "supporters": {"u1": {"doc": {}, "payments": {"cs_1": pay(1500)}}},
            "guestSupporters": {"g1": {"doc": {}, "payments": {"cs_2": pay(5000)}}},
        })
        self.assertEqual(d["grossCents"], 6500)
        self.assertEqual(d["paymentCount"], 2)

    def test_two_gifts_from_one_donor_both_count(self):
        d = self.run_report({"supporters": {"u1": {"doc": {}, "payments": {
            "cs_1": pay(1500), "cs_2": pay(3500)}}}})
        self.assertEqual((d["grossCents"], d["paymentCount"]), (5000, 2))


# ══════════════════════════════════════════════════════════════════════════
class TestOnlyThisMonthAndOnlyRealMoney(_Base):
    def test_last_month_and_next_month_are_left_out(self):
        d = self.run_report({"supporters": {"u1": {"doc": {}, "payments": {
            "before": pay(1000, datetime(2026, 8, 31, 23, 59, 59, tzinfo=timezone.utc)),
            "first":  pay(2000, datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)),
            "last":   pay(4000, datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc)),
            "after":  pay(8000, datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)),
        }}}})
        self.assertEqual(d["grossCents"], 6000)
        self.assertEqual(sorted(r["id"] for r in d["payments"]), ["first", "last"])

    def test_a_payment_stamped_in_another_zone_is_placed_by_its_utc_instant(self):
        """19:30 on Aug 31 in UTC-6 is Sep 1 01:30 UTC, and belongs to September."""
        late_aug_local = datetime(2026, 8, 31, 19, 30, tzinfo=timezone(timedelta(hours=-6)))
        d = self.run_report({"supporters": {"u1": {"doc": {}, "payments": {
            "a": pay(1000, late_aug_local)}}}})
        self.assertEqual(d["grossCents"], 1000)

    def test_an_unsettled_payment_is_reported_but_never_counted(self):
        """Delayed methods land as unpaid and settle later. Promising 5% of money
        that has not arrived is promising money we may never receive."""
        d = self.run_report({"supporters": {"u1": {"doc": {}, "payments": {
            "ok":     pay(2000),
            "unpaid": pay(9900, status="unpaid"),
        }}}})
        self.assertEqual(d["grossCents"], 2000)
        self.assertEqual(d["skippedUnpaid"], 1)

    def test_a_naive_timestamp_is_read_as_utc_not_as_server_local_time(self):
        """Firestore hands back tz-aware datetimes, but a hand-written or
        restored record may not. astimezone() on a naive datetime reads it as the
        SERVER's local zone, which on a UTC-6 box moves anything before 06:00 on
        the 1st into the previous month. 00:30 on Sep 1, naive, is September."""
        naive = datetime(2026, 9, 1, 0, 30)
        self.assertIsNone(naive.tzinfo)
        d = self.run_report({"supporters": {"u1": {"doc": {}, "payments": {
            "a": pay(1000, naive)}}}})
        self.assertEqual(d["grossCents"], 1000)
        self.assertEqual(d["skippedMissingTimestamp"], 0)

    def test_something_that_is_not_a_date_at_all_is_not_counted(self):
        d = self.run_report({"supporters": {"u1": {"doc": {}, "payments": {
            "a": pay(1000, "2026-09-10T12:00:00Z")}}}})
        self.assertEqual(d["grossCents"], 0)
        self.assertEqual(d["skippedMissingTimestamp"], 1)

    def test_a_payment_with_no_timestamp_is_reported_not_guessed(self):
        d = self.run_report({"supporters": {"u1": {"doc": {}, "payments": {
            "ok":      pay(2000),
            "notime":  {"amountCents": 5000, "paymentStatus": "paid"},
        }}}})
        self.assertEqual(d["grossCents"], 2000)
        self.assertEqual(d["skippedMissingTimestamp"], 1)

    def test_a_free_order_counts_and_owes_nothing(self):
        d = self.run_report({"supporters": {"u1": {"doc": {}, "payments": {
            "a": pay(0, status="no_payment_required")}}}})
        self.assertEqual((d["grossCents"], d["conservationShareCents"]), (0, 0))
        self.assertEqual(d["paymentCount"], 1)


# ══════════════════════════════════════════════════════════════════════════
class TestWhatTheReportSays(_Base):
    def test_the_breakdown_groups_by_product(self):
        d = self.run_report({"supporters": {"u1": {"doc": {}, "payments": {
            "a": pay(1500, product="Wave Warrior"),
            "b": pay(1500, product="Wave Warrior"),
            "c": pay(3500, product="Ocean Ally"),
        }}}})
        self.assertEqual(d["byProduct"]["Wave Warrior"], {"count": 2, "cents": 3000})
        self.assertEqual(d["byProduct"]["Ocean Ally"], {"count": 1, "cents": 3500})

    def test_payments_are_listed_oldest_first(self):
        d = self.run_report({"supporters": {"u1": {"doc": {}, "payments": {
            "late":  pay(100, datetime(2026, 9, 20, tzinfo=timezone.utc)),
            "early": pay(100, datetime(2026, 9, 2, tzinfo=timezone.utc)),
        }}}})
        self.assertEqual([r["id"] for r in d["payments"]], ["early", "late"])

    def test_the_window_is_labelled_utc_so_the_figure_can_be_reconciled(self):
        d = self.run_report({"supporters": {}})
        self.assertEqual(d["timezone"], "UTC")
        self.assertTrue(d["windowStart"].startswith("2026-09-01"))
        self.assertTrue(d["windowEnd"].startswith("2026-10-01"))

    def test_a_firestore_outage_is_an_error_not_a_zero(self):
        """'$0 this month' and 'the database did not answer' must never look
        the same: one means donate nothing, the other means ask again."""
        ms._get_firestore = lambda: None
        d = ms._admin_donation_report("2026-09")
        self.assertFalse(d["ok"])
        self.assertNotIn("grossCents", d)

    def test_the_printed_report_states_the_total_and_the_share(self):
        d = self.run_report({"supporters": {"u1": {"doc": {"displayName": "Ada"},
                                                  "payments": {"a": pay(10000)}}}})
        text = dr.report(d)
        self.assertIn("$100.00", text)
        self.assertIn("$5.00", text)
        self.assertIn("5% to ocean conservation", text)
        self.assertIn("2026-09", text)

    def test_the_printed_report_says_what_it_left_out(self):
        d = self.run_report({"supporters": {"u1": {"doc": {}, "payments": {
            "ok": pay(2000), "unpaid": pay(9900, status="unpaid")}}}})
        self.assertIn("not settled yet", dr.report(d))

    def test_an_empty_month_says_so_rather_than_printing_a_bare_zero(self):
        self.assertIn("No payments recorded", dr.report(self.run_report({"supporters": {}})))


if __name__ == "__main__":
    unittest.main(verbosity=2)
