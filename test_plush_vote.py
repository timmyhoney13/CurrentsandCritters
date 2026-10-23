"""test_plush_vote.py, the website's "which critter should be a plushy?" poll.

This is a PUBLIC endpoint that WRITES TO FIRESTORE with no account behind it,
which is the interesting part, so most of what is pinned here is about what it
refuses to do and what it never gives away:

  1. The ballot paper and the website's own critter table are the SAME list.
     A vote for a slug the deck does not have is refused, so a typo on either
     side would quietly throw real votes away. They are compared item for item.
  2. One ballot per voter id, and a re-vote REPLACES. Reloading the page and
     voting again must not count twice.
  3. New ballots are rate limited per address; an UPDATE is not. Somebody
     changing their mind must never be told to come back tomorrow.
  4. The public tally is counts only. The optional note and name are for the
     dev to read in the document, and they never appear in an answer.
  5. No Firestore means no silent success. A poll that takes clicks and stores
     nothing looks perfectly healthy from the website, so cast() says so and
     the tally reports enabled: false.
  6. Nothing a stranger sends decides anything: the voter id must be hex, the
     picks must be known slugs, the note is capped and stripped.
  7. The website really is wired to it, and the server really routes it. A
     section that posts nowhere is the failure nobody notices.

    python3 test_plush_vote.py
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import plush_vote as pv  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
#  A Firestore stand-in: the four calls this module makes, and no more
# ═══════════════════════════════════════════════════════════════════════════
class FakeSnap:
    def __init__(self, data):
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class FakeDoc:
    def __init__(self, store, path):
        self._store, self._path = store, path

    def get(self):
        return FakeSnap(self._store.get(self._path))

    def set(self, data, merge=False):
        self._store[self._path] = copy.deepcopy(data)


class FakeCollection:
    def __init__(self, store, path):
        self._store, self._path = store, path

    def document(self, doc_id):
        return FakeDoc(self._store, self._path + "/" + str(doc_id))

    def stream(self):
        prefix = self._path + "/"
        return iter([FakeSnap(v) for k, v in sorted(self._store.items())
                     if k.startswith(prefix) and "/" not in k[len(prefix):]])


class FakeDB:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return FakeCollection(self.store, name)


class BrokenDB(FakeDB):
    """Firestore answering the phone and then falling over, which is a
    different failure from not being configured at all."""

    def collection(self, name):
        raise RuntimeError("firestore exploded")


class FakeHandler:
    """The one method plush_vote calls on the real handler, plus the two
    attributes _client_key reads."""

    def __init__(self, ip="203.0.113.7"):
        self.payload = None
        self.status = 200
        self.client_address = (ip, 51000)
        self.headers = {}

    def _send_json(self, payload, status=200):
        self.payload = payload
        self.status = int(status)


class Parsed:
    def __init__(self, path):
        self.path = path
        self.query = ""


def reset(db=None):
    """Every test starts with an empty box and no rate-limit history."""
    pv._RL.clear()
    pv._TALLY_CACHE["value"] = None
    pv._TALLY_CACHE["at"] = 0.0
    fresh = db if db is not None else FakeDB()
    pv.init(get_firestore=lambda: fresh)
    return fresh


VOTER_A = "a1b2c3d4e5f60718293a4b5c"
VOTER_B = "ffffffffffffffffffffffff"


# ═══════════════════════════════════════════════════════════════════════════
#  1. THE BALLOT PAPER IS THE WEBSITE'S OWN LIST
# ═══════════════════════════════════════════════════════════════════════════
def website_critters():
    """The CRITTERS table out of index.html, as (slug, name, family)."""
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    start = html.index("const CRITTERS = [")
    body = html[start:html.index("\n];", start)]
    out = []
    for chunk in re.finditer(
            r"\{\s*f:\s*'([^']+)'\s*,\s*n:\s*'((?:[^'\\]|\\.)*)'\s*,\s*t:\s*'([^']+)'", body):
        slug, name, fam = chunk.group(1), chunk.group(2), chunk.group(3)
        name = name.replace("\\'", "'")
        name = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), name)
        out.append((slug, name, fam))
    return out


class TestBallotPaper(unittest.TestCase):
    def test_same_list_as_the_website(self):
        site = website_critters()
        self.assertEqual(len(site), 53, "the gallery should still hold 53 critters")
        self.assertEqual(pv.CRITTERS, site,
                         "the ballot paper and the website's CRITTERS table have drifted")

    def test_every_slug_has_art(self):
        for slug in pv.SLUGS:
            path = os.path.join(ROOT, "assets", "critters", slug + ".png")
            self.assertTrue(os.path.exists(path), "no art for " + slug)

    def test_slugs_are_unique_and_tidy(self):
        self.assertEqual(len(pv.SLUGS), len(set(pv.SLUGS)))
        for slug in pv.SLUGS:
            self.assertRegex(slug, r"^[a-z0-9-]+$")


# ═══════════════════════════════════════════════════════════════════════════
#  2. VALIDATION
# ═══════════════════════════════════════════════════════════════════════════
class TestCleanPicks(unittest.TestCase):
    def test_keeps_order_and_drops_duplicates(self):
        self.assertEqual(pv.clean_picks(["narwhal", "lobster", "narwhal"]),
                         ["narwhal", "lobster"])

    def test_drops_unknown_without_failing_the_ballot(self):
        self.assertEqual(pv.clean_picks(["narwhal", "unicorn-fish", "lobster"]),
                         ["narwhal", "lobster"])

    def test_caps_at_three(self):
        got = pv.clean_picks(["narwhal", "lobster", "sea-urchin", "clownfish"])
        self.assertEqual(got, ["narwhal", "lobster", "sea-urchin"])

    def test_junk_is_empty_not_an_exception(self):
        for junk in (None, 42, {"a": 1}, [], ["", None], [{"f": "narwhal"}]):
            self.assertEqual(pv.clean_picks(junk), [])

    def test_a_bare_string_counts_as_one_pick(self):
        self.assertEqual(pv.clean_picks("narwhal"), ["narwhal"])

    def test_case_and_space_are_forgiven(self):
        self.assertEqual(pv.clean_picks([" Narwhal "]), ["narwhal"])


class TestRefusals(unittest.TestCase):
    def setUp(self):
        self.db = reset()

    def test_voter_id_must_be_hex(self):
        for bad in ("", "short", "NOT-HEX-AT-ALL-REALLY", "zzzzzzzzzzzzzzzz", "../../etc"):
            out = pv.cast({"voter": bad, "picks": ["narwhal"]}, "198.51.100.1")
            self.assertFalse(out["ok"])
            self.assertEqual(out["error"], "bad_voter")
        self.assertEqual(self.db.store, {}, "a refused ballot must write nothing")

    def test_no_valid_picks_is_refused(self):
        out = pv.cast({"voter": VOTER_A, "picks": ["unicorn-fish"]}, "198.51.100.1")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "no_picks")
        self.assertEqual(self.db.store, {})

    def test_note_and_name_are_capped_and_stripped(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"],
                 "note": "line\r\nbreak\x07bell " + "x" * 900,
                 "name": "y" * 200}, "198.51.100.1")
        doc = self.db.store["plush_votes/" + VOTER_A]
        self.assertEqual(len(doc["note"]), pv.NOTE_MAX)
        self.assertEqual(len(doc["name"]), pv.NAME_MAX)
        self.assertNotIn("\r", doc["note"])
        self.assertNotIn("\x07", doc["note"])


# ═══════════════════════════════════════════════════════════════════════════
#  3. COUNTING
# ═══════════════════════════════════════════════════════════════════════════
class TestCounting(unittest.TestCase):
    def setUp(self):
        self.db = reset()

    def test_a_vote_is_stored_and_counted(self):
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal", "lobster"]}, "198.51.100.1")
        self.assertTrue(out["ok"])
        self.assertEqual(out["yours"], ["narwhal", "lobster"])
        self.assertFalse(out["changed"])
        self.assertEqual(out["ballots"], 1)
        self.assertEqual(out["counts"]["narwhal"], 1)
        self.assertEqual(out["counts"]["lobster"], 1)
        self.assertEqual(out["firsts"], {"narwhal": 1},
                         "only the first pick counts as a first")

    def test_re_voting_replaces_instead_of_stacking(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"]}, "198.51.100.1")
        out = pv.cast({"voter": VOTER_A, "picks": ["lobster"]}, "198.51.100.1")
        self.assertTrue(out["ok"])
        self.assertTrue(out["changed"])
        self.assertEqual(out["ballots"], 1, "one browser, one ballot")
        self.assertNotIn("narwhal", out["counts"])
        self.assertEqual(out["counts"]["lobster"], 1)

    def test_a_re_vote_keeps_the_original_timestamp(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"]}, "198.51.100.1")
        first = self.db.store["plush_votes/" + VOTER_A]["created"]
        pv.cast({"voter": VOTER_A, "picks": ["lobster"]}, "198.51.100.1")
        self.assertEqual(self.db.store["plush_votes/" + VOTER_A]["created"], first)

    def test_two_browsers_are_two_ballots(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"]}, "198.51.100.1")
        out = pv.cast({"voter": VOTER_B, "picks": ["narwhal"]}, "198.51.100.2")
        self.assertEqual(out["ballots"], 2)
        self.assertEqual(out["counts"]["narwhal"], 2)

    def test_the_voter_sees_their_own_vote_immediately(self):
        """The tally is cached, so a vote has to force the recount or the
        voter is told their click did nothing."""
        pv.tally()                      # warm the cache with an empty box
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"]}, "198.51.100.1")
        self.assertEqual(out["counts"].get("narwhal"), 1)

    def test_a_stored_ballot_full_of_junk_is_skipped(self):
        self.db.store["plush_votes/manual"] = {"picks": ["unicorn-fish"]}
        self.db.store["plush_votes/empty"] = {"note": "no picks here"}
        got = pv.tally()
        self.assertEqual(got["ballots"], 0)
        self.assertEqual(got["counts"], {})


# ═══════════════════════════════════════════════════════════════════════════
#  4. WHAT THE PUBLIC ANSWER NEVER CARRIES
# ═══════════════════════════════════════════════════════════════════════════
class TestNoLeak(unittest.TestCase):
    def test_tally_is_counts_only(self):
        reset()
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"],
                 "note": "my phone number is 555-0100",
                 "name": "Dana Whitlock"}, "198.51.100.1")
        blob = json.dumps(pv.tally())
        self.assertNotIn("555-0100", blob)
        self.assertNotIn("Whitlock", blob)
        self.assertNotIn("198.51.100.1", blob)
        self.assertEqual(set(pv.tally().keys()),
                         {"ok", "enabled", "ballots", "counts", "firsts", "updated"})


# ═══════════════════════════════════════════════════════════════════════════
#  5. RATE LIMITS
# ═══════════════════════════════════════════════════════════════════════════
class TestRateLimits(unittest.TestCase):
    def setUp(self):
        self.db = reset()

    def test_new_ballots_from_one_address_are_capped(self):
        refused = 0
        for i in range(pv.NEW_PER_HOUR + 4):
            voter = "%024x" % i
            out = pv.cast({"voter": voter, "picks": ["narwhal"]}, "198.51.100.9")
            if not out["ok"]:
                refused += 1
                self.assertEqual(out["error"], "too_many_requests")
        self.assertGreater(refused, 0, "a script cycling ids should hit a wall")

    def test_changing_your_mind_is_never_rate_limited(self):
        """The per-minute cap still applies, but the NEW-ballot caps must not:
        an update is the same person, not another voter."""
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"]}, "198.51.100.8")
        for i in range(pv.NEW_PER_HOUR + 6):
            pv._RL.pop("vote|198.51.100.8|%d" % (int(__import__("time").time()) // 60), None)
            out = pv.cast({"voter": VOTER_A, "picks": ["lobster" if i % 2 else "narwhal"]},
                          "198.51.100.8")
            self.assertTrue(out["ok"], "update %d was refused: %r" % (i, out))

    def test_a_burst_is_slowed_down(self):
        hit_the_wall = False
        for i in range(pv.VOTES_PER_MIN + 3):
            out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"]}, "198.51.100.7")
            if not out["ok"] and out["error"] == "too_many_requests":
                hit_the_wall = True
        self.assertTrue(hit_the_wall)


# ═══════════════════════════════════════════════════════════════════════════
#  6. NO FIRESTORE MEANS NO SILENT SUCCESS
# ═══════════════════════════════════════════════════════════════════════════
class TestNoStore(unittest.TestCase):
    def test_cast_refuses_and_says_why(self):
        pv._RL.clear()
        pv._TALLY_CACHE["value"] = None
        pv.init(get_firestore=lambda: None)
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"]}, "198.51.100.1")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unavailable")
        self.assertTrue(out["message"])

    def test_tally_reports_itself_disabled(self):
        pv._TALLY_CACHE["value"] = None
        pv.init(get_firestore=lambda: None)
        got = pv.tally()
        self.assertTrue(got["ok"])
        self.assertFalse(got["enabled"])
        self.assertEqual(got["ballots"], 0)
        self.assertFalse(pv.enabled())

    def test_a_broken_firestore_does_not_crash_the_page(self):
        reset(BrokenDB())
        got = pv.tally()
        self.assertFalse(got["ok"])
        self.assertEqual(got["error"], "tally_failed")
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"]}, "198.51.100.1")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unavailable")


# ═══════════════════════════════════════════════════════════════════════════
#  7. HTTP
# ═══════════════════════════════════════════════════════════════════════════
class TestHttp(unittest.TestCase):
    def setUp(self):
        self.db = reset()

    def post(self, path, body, ip="203.0.113.7"):
        h = FakeHandler(ip)
        handled = pv.handle_post(h, Parsed(path), body)
        return handled, h

    def get(self, path):
        h = FakeHandler()
        handled = pv.handle_get(h, Parsed(path))
        return handled, h

    def test_vote_route(self):
        handled, h = self.post("/api/plush/vote", {"voter": VOTER_A, "picks": ["narwhal"]})
        self.assertTrue(handled)
        self.assertEqual(h.status, 200)
        self.assertTrue(h.payload["ok"])

    def test_tally_route(self):
        handled, h = self.get("/api/plush/tally")
        self.assertTrue(handled)
        self.assertEqual(h.status, 200)
        self.assertIn("counts", h.payload)

    def test_bad_body_is_400(self):
        handled, h = self.post("/api/plush/vote", {"voter": "nope", "picks": ["narwhal"]})
        self.assertTrue(handled)
        self.assertEqual(h.status, 400)

    def test_rate_limited_is_429(self):
        for _ in range(pv.VOTES_PER_MIN + 2):
            handled, h = self.post("/api/plush/vote",
                                   {"voter": VOTER_A, "picks": ["narwhal"]}, "198.51.100.44")
        self.assertEqual(h.status, 429)

    def test_no_store_is_503(self):
        pv.init(get_firestore=lambda: None)
        handled, h = self.post("/api/plush/vote", {"voter": VOTER_A, "picks": ["narwhal"]})
        self.assertEqual(h.status, 503)

    def test_other_paths_are_left_alone(self):
        for path in ("/api/partner/contact", "/api/stats", "/", "/api/plushy"):
            self.assertFalse(pv.handle_post(FakeHandler(), Parsed(path), {}))
            self.assertFalse(pv.handle_get(FakeHandler(), Parsed(path)))

    def test_the_forwarded_address_is_the_one_limited(self):
        """Render sits behind a proxy: without X-Forwarded-For every voter in
        the world shares one rate-limit bucket."""
        h = FakeHandler("10.0.0.1")
        h.headers = {"X-Forwarded-For": "198.51.100.5, 10.0.0.1"}
        self.assertEqual(pv._client_key(h), "198.51.100.5")


# ═══════════════════════════════════════════════════════════════════════════
#  8. THE WEBSITE AND THE SERVER ARE REALLY WIRED TO IT
# ═══════════════════════════════════════════════════════════════════════════
class TestWiring(unittest.TestCase):
    def setUp(self):
        self.html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        self.srv = open(os.path.join(ROOT, "multiplayer_server.py"), encoding="utf-8").read()
        self.css = open(os.path.join(ROOT, "styles.css"), encoding="utf-8").read()

    def test_the_section_exists_below_meet_the_critters(self):
        critters = self.html.index('id="critters"')
        plushy = self.html.index('id="plushy"')
        oceans = self.html.index('id="oceans"')
        self.assertLess(critters, plushy, "the vote belongs under Meet the Critters")
        self.assertLess(plushy, oceans)

    def test_the_page_posts_to_the_real_endpoint(self):
        self.assertIn("/api/plush/vote", self.html)
        self.assertIn("/api/plush/tally", self.html)
        self.assertIn("play.currentsandcritters.com", self.html)

    def test_every_piece_the_script_reaches_for_is_in_the_markup(self):
        for ident in ("plushGrid", "plushTabs", "plushSend", "plushClear",
                      "plushSay", "plushResults", "plushBoard", "plushTotal"):
            self.assertIn('id="%s"' % ident, self.html, "missing element " + ident)
        self.assertEqual(self.html.count('class="plush-slot"'), pv.MAX_PICKS)

    def test_the_tabs_match_the_families_on_the_ballot(self):
        block = self.html[self.html.index('id="plushTabs"'):]
        block = block[:block.index("</div>")]
        tabs = set(re.findall(r'data-tab="([a-z]+)"', block))
        self.assertEqual(tabs - {"all"}, set(f for _, _, f in pv.CRITTERS),
                         "a family tab filters to nothing, or a family has no tab")

    def test_the_styles_exist(self):
        for cls in (".section.plushy", ".plush-grid", ".plush-pick", ".plush-bar",
                    ".plush-slot", ".plush-send", ".plush-results", ".plush-row"):
            self.assertIn(cls, self.css, "no styles for " + cls)

    def test_the_server_routes_and_initialises_it(self):
        self.assertIn("import plush_vote", self.srv)
        self.assertIn("plush_vote.handle_get(self, parsed)", self.srv)
        self.assertIn("plush_vote.handle_post(self, parsed, body)", self.srv)
        self.assertIn("plush_vote.init(get_firestore=", self.srv)

    def test_no_em_dashes_in_what_the_reader_sees(self):
        """House rule: em dashes do not go on screen."""
        start = self.html.index('id="plushy"')
        block = self.html[start:self.html.index("</section>", start)]
        self.assertNotIn("\u2014", block)
        self.assertNotIn("&mdash;", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
