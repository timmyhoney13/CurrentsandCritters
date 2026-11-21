"""test_plush_reserve.py, the plush reservation list (the free pre-order list).

The interesting part of this endpoint is that it is PUBLIC, writes Firestore
with no account behind it, and records MONEY the studio will later invoice.
So what is pinned here:

  1. Every price, the free-shipping threshold and the caps come from
     plush/catalog.json, and the website's copies of them agree with it.
  2. The pricing rules, exactly as the customer is told them:
       $44.99 each + shipping and applicable sales tax ($5 off each on 3+);
       1 or 2 plushies -> shipping calculated at checkout;
       3 or more, any mix -> free standard shipping, U.S. addresses only;
       sales tax always calculated at checkout.
  3. Nothing a browser sends decides a stored total: totals are recomputed.
  4. One reservation per email: a second submit UPDATES, never duplicates.
  5. Validation refuses out loud; the honeypot is answered like a success and
     stores nothing.
  6. No Firestore means no silent success.
  7. The newsletter box is a separate question, never implied.
  8. The server and the admin really route it.

    python3 test_plush_reserve.py
    python3 test_plush_reserve.py --serve 8799   (a stub API for test_preorder_page.js)
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import plush_reserve as pr  # noqa: E402


class FakeSnap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeDoc:
    def __init__(self, coll, doc_id):
        self.coll, self.id = coll, doc_id

    def get(self):
        return FakeSnap(self.id, self.coll.docs.get(self.id))

    def set(self, data, merge=False):
        self.coll.docs[self.id] = json.loads(json.dumps(data))


class FakeCollection:
    def __init__(self):
        self.docs = {}

    def document(self, doc_id):
        return FakeDoc(self, doc_id)

    def stream(self):
        return [FakeSnap(k, v) for k, v in list(self.docs.items())]


class FakeDB:
    def __init__(self):
        self.colls = {}

    def collection(self, name):
        return self.colls.setdefault(name, FakeCollection())


def fresh(db=None, signup=None, notify=None):
    pr._RL.clear()
    pr._COUNT_CACHE.update({"at": 0.0, "value": None})
    pr.init(get_firestore=(lambda: db), public_signup=signup, notify=notify)


GOOD = {"first": "Ada", "last": "Lovelace", "email": "Ada@Example.com", "country": "US",
        "zip": "37203", "items": {"whale-shark": 2, "loggerhead-sea-turtle": 1}}


class Catalog(unittest.TestCase):
    def test_prices_are_44_99_with_5_off_on_3(self):
        for p in pr.PRODUCTS:
            self.assertEqual(p["priceCents"], 4499, p["id"])
        self.assertEqual(pr.money(4499), "$44.99")
        self.assertEqual(pr.BUNDLE_DISCOUNT_CENTS, 500)

    def test_specs(self):
        names = {p["id"]: p["name"] for p in pr.PRODUCTS}
        self.assertEqual(names, {"whale-shark": "Weighted Whale Shark",
                                 "loggerhead-sea-turtle": "Weighted Loggerhead Sea Turtle"})
        for p in pr.PRODUCTS:
            self.assertEqual(p["sizeCm"], 50)
            self.assertEqual(p["weightLb"], 2.5)
            self.assertEqual(len(p["frames"]), 5)
        self.assertEqual(pr.CATALOG["features"], ["Premium ultra-soft plush fabric",
                                                  "Securely enclosed weighted filling",
                                                  "No heating components"])
        self.assertEqual(pr.CATALOG["priceNote"], "+ shipping and applicable sales tax")
        self.assertEqual(pr.FREE_SHIPPING_MIN, 3)
        self.assertEqual(pr.FREE_SHIPPING_COUNTRIES, {"US"})

    def test_pledge_uses_the_one_canonical_sentence(self):
        # test_fundraising_claims.js: one pledge sentence, on one basis.
        self.assertEqual(pr.CATALOG["pledge"], "5% goes to ocean conservation")

    def test_website_copies_agree(self):
        index = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        store = index[index.index('data-panel="store"'):index.index("<!-- /panel-store -->")]
        self.assertEqual(store.count("<b>$44.99</b> + shipping and applicable sales tax"), 2)
        self.assertNotIn("Price: <b>$?</b>", store)
        self.assertNotIn("$29.99", index)
        page = open(os.path.join(ROOT, "preorder.html"), encoding="utf-8").read()
        self.assertIn('data-cc-preorder="full"', page)
        self.assertIn("$44.99</b> each + shipping and applicable sales tax", page)
        self.assertIn("/plush/preorder.js", page)


class Pricing(unittest.TestCase):
    def q(self, ws=0, lt=0, country="US"):
        return pr.quote({"whale-shark": ws, "loggerhead-sea-turtle": lt}, country)

    def test_the_four_examples(self):
        for n, cents, off, free in [(1, 4499, 0, False), (2, 8998, 0, False), (3, 11997, 1500, True), (4, 15996, 2000, True)]:
            q = self.q(ws=n)
            self.assertEqual((q["count"], q["subtotalCents"], q["discountCents"], q["freeShipping"]), (n, cents, off, free), n)
            self.assertEqual(q["listCents"], n * 4499)
            self.assertEqual(q["shipping"], "free" if free else "calculated")
            self.assertEqual(q["tax"], "calculated")

    def test_mix_and_match(self):
        q = self.q(ws=2, lt=1)
        self.assertEqual((q["count"], q["subtotalCents"], q["freeShipping"]), (3, 11997, True))
        self.assertEqual([(l["name"], l["qty"], l["lineCents"]) for l in q["lines"]],
                         [("Weighted Whale Shark", 2, 8998), ("Weighted Loggerhead Sea Turtle", 1, 4499)])
        self.assertEqual(self.q(lt=3)["freeShipping"], True)

    def test_remaining(self):
        self.assertEqual([self.q(ws=n)["remainingForFreeShipping"] for n in range(5)], [3, 2, 1, 0, 0])

    def test_no_free_international_shipping(self):
        for c in ("CA", "GB", "AU", "OTHER"):
            q = self.q(ws=3, country=c)
            self.assertTrue(q["qualifiesForFreeShipping"])
            self.assertFalse(q["freeShipping"], c)
            self.assertEqual(q["shipping"], "calculated")

    def test_quantities_are_whole_and_capped(self):
        self.assertEqual(pr.clean_items({"whale-shark": True})["whale-shark"], 0)
        self.assertEqual(pr.clean_items({"whale-shark": "2"})["whale-shark"], 2)
        self.assertEqual(pr.clean_items({"whale-shark": 2.9})["whale-shark"], 2)
        self.assertEqual(pr.clean_items({"whale-shark": -3})["whale-shark"], 0)
        self.assertEqual(pr.clean_items({"whale-shark": 99})["whale-shark"], pr.MAX_PER_ITEM)
        self.assertEqual(pr.clean_items({"whale-shark": float("nan")})["whale-shark"], 0)
        self.assertEqual(pr.clean_items({"made-up": 5}), {"whale-shark": 0, "loggerhead-sea-turtle": 0})
        self.assertEqual(pr.clean_items("nope"), {"whale-shark": 0, "loggerhead-sea-turtle": 0})


class Reserving(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.signups, self.notes = [], []
        fresh(self.db, signup=lambda e, **k: (self.signups.append((e, k)) or {"ok": True, "message": "ok"}),
              notify=self.notes.append)

    def stored(self):
        return self.db.collection(pr.COLLECTION).docs

    def test_stores_recomputed_totals(self):
        body = dict(GOOD, subtotalCents=1, freeShipping=True, price=1)
        out = pr.reserve(body, "1.1.1.1")
        self.assertTrue(out["ok"], out)
        self.assertRegex(out["reference"], r"^CCR-[0-9A-F]{6}$")
        (doc,) = self.stored().values()
        self.assertEqual(doc["subtotalCents"], 11997)
        self.assertTrue(doc["freeShipping"])
        self.assertEqual(doc["email"], "ada@example.com")
        self.assertEqual(doc["items"], {"whale-shark": 2, "loggerhead-sea-turtle": 1})
        self.assertNotIn("price", doc)
        self.assertFalse(doc["newsletter"])
        self.assertEqual(self.signups, [])
        self.assertEqual(len(self.notes), 1)
        # the document id is a hash, never the address in the clear
        (doc_id,) = self.stored().keys()
        self.assertNotIn("@", doc_id)

    def test_second_submit_updates_not_duplicates(self):
        a = pr.reserve(GOOD, "1.1.1.1")
        b = pr.reserve(dict(GOOD, email="ada@example.com ", items={"whale-shark": 1}), "1.1.1.1")
        self.assertTrue(a["ok"] and b["ok"])
        self.assertFalse(a["updated"])
        self.assertTrue(b["updated"])
        self.assertEqual(a["reference"], b["reference"])
        self.assertEqual(len(self.stored()), 1)
        (doc,) = self.stored().values()
        self.assertEqual(doc["count"], 1)
        self.assertFalse(doc["freeShipping"])
        self.assertEqual(doc["revisions"], 1)

    def test_validation(self):
        cases = [
            (dict(GOOD, first=""), "first name"),
            (dict(GOOD, last="  "), "last name"),
            (dict(GOOD, email="nope"), "email"),
            (dict(GOOD, email="a@b.com\nBcc: x@y.com"), "email"),
            (dict(GOOD, country="ZZ"), "ship"),
            (dict(GOOD, zip="123"), "ZIP"),
            (dict(GOOD, items={}), "at least one"),
            (dict(GOOD, items={"whale-shark": 10, "loggerhead-sea-turtle": 10, }, ), None),
        ]
        for body, needle in cases:
            out = pr.reserve(body, "2.2.2.2")
            if needle is None:
                self.assertTrue(out["ok"], out)
            else:
                self.assertFalse(out["ok"], body)
                self.assertIn(needle.lower(), out["message"].lower())
        self.assertTrue(pr.reserve(dict(GOOD, email="b@example.com", country="GB", zip="SW1A 1AA"), "3.3.3.3")["ok"])
        self.assertTrue(pr.reserve(dict(GOOD, email="c@example.com", zip=""), "3.3.3.3")["ok"])

    def test_honeypot_stores_nothing(self):
        out = pr.reserve(dict(GOOD, company_site="http://spam"), "4.4.4.4")
        self.assertTrue(out["ok"])
        self.assertEqual(self.stored(), {})
        self.assertEqual(self.notes, [])

    def test_newsletter_only_when_ticked(self):
        pr.reserve(dict(GOOD, newsletter="false"), "5.5.5.5")
        self.assertEqual(self.signups, [])
        out = pr.reserve(dict(GOOD, email="n@example.com", newsletter=True), "5.5.5.5")
        self.assertEqual(out.get("newsletter"), "ok")
        self.assertEqual(self.signups[0][0], "n@example.com")
        self.assertEqual(self.signups[0][1]["source"], "Plush Reservation")

    def test_rate_limit_on_new_only(self):
        for i in range(pr.NEW_PER_HOUR):
            self.assertTrue(pr.reserve(dict(GOOD, email="r%d@example.com" % i), "6.6.6.6")["ok"])
            pr._RL.pop(next((k for k in pr._RL if k.startswith("reserve-min|")), ""), None)
        out = pr.reserve(dict(GOOD, email="one-more@example.com"), "6.6.6.6")
        self.assertEqual(out["error"], "too_many_requests")

    def test_no_firestore_is_loud(self):
        fresh(None)
        out = pr.reserve(GOOD, "7.7.7.7")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unavailable")
        self.assertFalse(pr.status()["enabled"])

    def test_admin_view_and_csv(self):
        pr.reserve(GOOD, "8.8.8.8")
        pr.reserve(dict(GOOD, email="=cmd@example.com", first="=HYPERLINK", items={"loggerhead-sea-turtle": 1}), "8.8.8.8")
        v = pr.admin_view()
        self.assertEqual(v["reservations"], 2)
        self.assertEqual(v["plushies"], 4)
        self.assertEqual({p["id"]: p["qty"] for p in v["perProduct"]},
                         {"whale-shark": 2, "loggerhead-sea-turtle": 2})
        self.assertEqual(v["freeShippingOrders"], 1)
        self.assertEqual(v["subtotalCents"], 16496)
        csv_text = pr.reservations_csv(v["rows"])
        self.assertIn("'=HYPERLINK", csv_text)
        self.assertIn('"$119.97"', csv_text)

    def test_admin_email_is_escaped(self):
        subject, html, text = pr.build_admin_email({
            "first": "<b>x</b>", "last": "L\nBcc: z@z.com", "email": "ada@example.com", "country": "US",
            "zip": "37203", "items": {"whale-shark": 3}, "reference": "CCR-ABCDEF", "newsletter": False})
        self.assertNotIn("<b>x</b>", html)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)
        self.assertNotIn("\n", subject)
        self.assertIn("Free standard shipping", text)
        self.assertIn("$119.97", text)
        self.assertIn("-$15.00", text)


class Wiring(unittest.TestCase):
    def test_server_routes_and_inits(self):
        src = open(os.path.join(ROOT, "multiplayer_server.py"), encoding="utf-8").read()
        self.assertIn("import plush_reserve", src)
        self.assertIn("plush_reserve.handle_get(self, parsed)", src)
        self.assertIn("plush_reserve.handle_post(self, parsed, body)", src)
        self.assertIn("plush_reserve.init(", src)
        # reserve is routed BEFORE the poll, which owns the rest of /api/plush/
        self.assertLess(src.index("plush_reserve.handle_post"), src.index("plush_vote.handle_post"))

    def test_admin_routes(self):
        src = open(os.path.join(ROOT, "newsletter_server.py"), encoding="utf-8").read()
        self.assertIn('action == "reservations"', src)
        self.assertIn('action == "reservations-export"', src)
        ui = open(os.path.join(ROOT, "multiplayer", "client", "js", "newsletter-admin.js"), encoding="utf-8").read()
        self.assertIn('api("reservations")', ui)

    def test_shipped_to_both_hosts(self):
        docker = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
        ignore = open(os.path.join(ROOT, ".dockerignore"), encoding="utf-8").read()
        vercel_ignore = open(os.path.join(ROOT, ".vercelignore"), encoding="utf-8").read()
        self.assertIn("COPY plush_reserve.py", docker)
        self.assertIn("COPY plush/catalog.json", docker)
        self.assertIn("!plush_reserve.py", ignore)
        self.assertIn("!plush/catalog.json", ignore)
        self.assertIn("!plush/catalog.json", vercel_ignore)
        vercel = json.load(open(os.path.join(ROOT, "vercel.json"), encoding="utf-8"))
        self.assertIn({"source": "/preorder", "destination": "/preorder.html"}, vercel["rewrites"])
        cors = [h for h in vercel["headers"] if h["source"] == "/plush/(.*)"]
        self.assertTrue(cors and any(x["key"] == "Access-Control-Allow-Origin" for x in cors[0]["headers"]))


def serve(port: int) -> None:
    """A stub of the game server's reservation routes, for the browser test."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse
    db = FakeDB()
    fresh(db, signup=lambda e, **k: {"ok": True, "message": "You're on the newsletter list."})

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send_json(self, data, status=200):
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            p = urlparse(self.path)
            if p.path == "/__docs":
                return self._send_json(db.collection(pr.COLLECTION).docs)
            if not pr.handle_get(self, p):
                self._send_json({"ok": False}, 404)

        def do_POST(self):
            p = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if p.path == "/__slow":
                import time
                time.sleep(2)
            if not pr.handle_post(self, p, body):
                self._send_json({"ok": False}, 404)

    print("stub listening on", port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve(int(sys.argv[sys.argv.index("--serve") + 1]))
    else:
        unittest.main(verbosity=1)
