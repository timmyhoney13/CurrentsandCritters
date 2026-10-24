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
  8. The optional "tell me who wins" box takes an email OR a phone number,
     judged by the same rules the newsletter uses for an address, and an
     answer it cannot use is REFUSED OUT LOUD. Somebody who mistyped their
     number and was thanked anyway thinks they are on the list and is not.
  9. Those people are not subscribers. They gave an address for one stated
     purpose, so nothing here writes to newsletterSubscribers, and the admin
     tab that shows them says so.

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

    def test_a_pick_is_drawn_as_a_critter_card_and_can_be_taken_back(self):
        """The slots use the same card as the grid: art tile, name, rank
        badge. And a filled one is a real button, so a pick can be removed
        from the bar instead of hunting for its tile again."""
        bar = self.html[self.html.index('id="plushSlots"'):]
        bar = bar[:bar.index('id="plushClear"')]
        self.assertEqual(bar.count('<button class="plush-slot"'), pv.MAX_PICKS,
                         "every slot is a button, not a div with a click handler")
        self.assertEqual(bar.count("disabled"), pv.MAX_PICKS,
                         "an empty slot is not focusable or clickable")
        for part in ('class="rank"', 'class="art"', 'class="who"'):
            self.assertEqual(bar.count(part), pv.MAX_PICKS, "every slot needs " + part)
        self.assertIn("getElementById('plushSlots').addEventListener('click'", self.html)
        self.assertIn("picks.splice(i, 1)", self.html)
        css = open(os.path.join(ROOT, "styles.css"), encoding="utf-8").read()
        self.assertIn(".plush-slot .art", css)
        self.assertIn(".plush-slot .rank", css)

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
        self.assertIn("plush_vote.init(", self.srv)
        init = self.srv[self.srv.index("plush_vote.init("):]
        self.assertIn("get_firestore=_get_firestore", init[:400])

    def test_a_vote_survives_a_server_that_is_a_deploy_behind(self):
        """The website and the vote server deploy separately, so the page can
        be live hours before the API is. A vote lost to that is a real vote
        lost: it is held on the device and sent on the next visit, and the
        reader is told that plainly instead of being shown an error they can
        do nothing about.

        The HOLDING PEN IS A QUEUE, one entry per ballot. It was a single slot
        once, and that erased votes for real: at a table the second person's
        held ballot overwrote the first person's. test_plush_queue.js drives
        the whole thing in a browser across a reload; these are the anchors
        that keep the shape from quietly going back."""
        js = self.html[self.html.index("// ── Plushy vote ─"):]
        js = js[:js.index("// ── Auto-hide nav")]
        self.assertIn("cc-plush-pending", js, "the held ballots need somewhere to live")
        self.assertIn("function serverNotReady", js)
        for code in ("404", "502", "503"):
            self.assertIn(code, js, "a %s is the server, not the voter" % code)
        self.assertIn("holdVote(ballot)", js)
        self.assertIn("saved on this device", js)
        # ...and they really are sent again, not just stored and forgotten.
        self.assertIn("function heldVotes()", js)
        self.assertIn("function flushHeld()", js)
        self.assertIn("applyVote(data, true)", js)
        # Several, each under its OWN id: re-stamping a held ballot with the
        # device's current id files it as an edit of whoever is holding the
        # phone now, which erases a vote at the other end too.
        self.assertIn("Array.isArray(value) ? value : [value]", js,
                      "a device still holding the old single-ballot shape keeps its vote")
        self.assertIn("heldVotes().filter(v => v.voter !== payload.voter)", js,
                      "re-sending replaces only that voter's own held ballot")
        self.assertIn("dropHeldVote(ballot.voter)", js,
                      "a sent ballot clears ITS entry, not the whole queue")
        self.assertNotIn("held.voter = voter", js,
                         "a held ballot must keep the id it was cast under")
        # A ballot the server REFUSED on its merits is not held forever, but
        # "busy, ask me later" (429) is not a refusal.
        self.assertIn("} else if (!retryLater(res)) {", js)
        self.assertIn("function retryLater", js)
        retry = js[js.index("function retryLater"):]
        self.assertIn("429", retry[:300], "a 429 means later, not never")

    def test_passing_the_device_round_is_offered_to_everybody(self):
        """One phone going round a table is the normal way this poll gets
        voted on, and one device means one ballot: the second person to vote
        UPDATES the first person's unless a new ballot is minted first. The
        hand-off used to be hidden behind a #passitround URL flag, which
        nobody at a table has ever typed, so three people voting on one phone
        left one ballot and two erased votes. It is now offered to everybody
        the moment a vote is in."""
        bar = self.html[self.html.index('id="plushNext"'):]
        bar = bar[:bar.index(">") + 1]
        self.assertIn("hidden", bar, "the hand-off button ships hidden")
        js = self.html[self.html.index("// ── Plushy vote ─"):]
        js = js[:js.index("// ── Auto-hide nav")]
        self.assertIn("nextBtn.hidden = !(sent.length > 0)", js,
                      "shown to anybody who has just voted, no flag to know about")
        self.assertNotIn("cc-plush-passitround", js,
                         "the per-device switch is gone, not left as dead config")
        # The hand-off must mint a NEW ballot, and must not touch anybody's
        # vote that is still waiting to be sent.
        hand = js[js.index("nextBtn.addEventListener"):]
        hand = hand[:hand.index("paintGrid();")]
        self.assertIn("voter = newVoterId()", hand)
        self.assertIn("sent = []", hand)
        self.assertNotIn("dropHeldVote", hand,
                         "handing the phone on must not delete an unsent vote")
        self.assertIn("heldVotes().length", hand,
                      "...and it says so rather than claiming the vote is counted")

    def test_the_limits_suit_a_device_being_passed_around(self):
        """Every ballot at a table arrives from ONE address, so a ceiling set
        for one-voter-per-address refuses real people holding a phone."""
        self.assertGreaterEqual(pv.NEW_PER_HOUR, 20)
        self.assertGreaterEqual(pv.NEW_PER_DAY, 80)
        self.assertGreater(pv.NEW_PER_DAY, pv.NEW_PER_HOUR)
        self.assertGreaterEqual(pv.VOTES_PER_MIN, 12)
        # Still a ceiling, though: this is a poll, not an open socket.
        self.assertLessEqual(pv.NEW_PER_HOUR, 60)
        self.assertLessEqual(pv.VOTES_PER_MIN, 30)

    def test_the_status_code_comes_back_with_the_body(self):
        """"That vote is wrong" and "that server has no vote in it" are
        different answers, and the page cannot tell them apart from the body
        alone."""
        js = self.html[self.html.index("function post(url, payload)"):]
        js = js[:js.index("tabs.addEventListener")]
        self.assertIn("status: r.status", js)
        self.assertIn("status: 0", js, "a network failure needs a status of its own")

    def test_no_em_dashes_in_what_the_reader_sees(self):
        """House rule: em dashes do not go on screen."""
        start = self.html.index('id="plushy"')
        block = self.html[start:self.html.index("</section>", start)]
        self.assertNotIn("\u2014", block)
        self.assertNotIn("&mdash;", block)


# ═══════════════════════════════════════════════════════════════════════════
#  9. THE "TELL ME WHO WINS" BOX
# ═══════════════════════════════════════════════════════════════════════════
class TestContactParsing(unittest.TestCase):
    def test_emails_follow_the_newsletter_rules(self):
        self.assertEqual(pv.clean_contact(" Tim@Example.COM "), ("tim@example.com", "email"))
        self.assertEqual(pv.clean_contact("Tim <tim@example.com>"), ("tim@example.com", "email"))
        for bad in ("a@b", "@example.com", "tim@", "tim@exam ple.com"):
            self.assertEqual(pv.clean_contact(bad), ("", ""), bad)

    def test_a_newline_in_an_address_is_refused(self):
        self.assertEqual(pv.clean_contact("tim@example.com\nbcc: someone@else.com"), ("", ""))

    def test_phone_numbers_are_kept_as_digits(self):
        self.assertEqual(pv.clean_contact("(615) 555-0100"), ("6155550100", "phone"))
        self.assertEqual(pv.clean_contact("615-555-0100"), ("6155550100", "phone"))
        self.assertEqual(pv.clean_contact("+44 7700 900123"), ("+447700900123", "phone"))

    def test_one_person_two_spellings_is_one_contact(self):
        a, _ = pv.clean_contact("(615) 555-0100")
        b, _ = pv.clean_contact("615.555.0100")
        self.assertEqual(a, b)

    def test_nonsense_is_refused_rather_than_stored(self):
        for bad in ("", "   ", "yes please", "123", "0" * 20, None, 42):
            self.assertEqual(pv.clean_contact(bad), ("", ""), repr(bad))


class TestContactOnABallot(unittest.TestCase):
    def setUp(self):
        self.db = reset()

    def ballot(self):
        return self.db.store.get("plush_votes/" + VOTER_A) or {}

    def test_a_contact_is_saved_and_echoed_back(self):
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"],
                       "contact": "Tim@Example.com"}, "198.51.100.1")
        self.assertTrue(out["ok"])
        self.assertEqual(out["contact"], "tim@example.com")
        self.assertEqual(out["contactKind"], "email")
        self.assertEqual(self.ballot()["contact"], "tim@example.com")
        self.assertNotIn("contactError", out)

    def test_a_vote_with_no_contact_still_counts(self):
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"]}, "198.51.100.1")
        self.assertTrue(out["ok"])
        self.assertEqual(out["contact"], "")
        self.assertEqual(self.ballot()["contact"], "")

    def test_an_unusable_contact_is_refused_out_loud(self):
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"],
                       "contact": "call me maybe"}, "198.51.100.1")
        self.assertTrue(out["ok"], "the VOTE still counts")
        self.assertTrue(out["contactError"], "and the voter is told it did not")
        self.assertEqual(out["contact"], "")
        self.assertEqual(self.ballot()["picks"], ["narwhal"])

    def test_an_unusable_edit_never_wipes_a_good_contact(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com"},
                "198.51.100.1")
        out = pv.cast({"voter": VOTER_A, "picks": ["lobster"], "contact": "oops"},
                      "198.51.100.1")
        self.assertTrue(out["contactError"])
        self.assertEqual(out["contact"], "tim@example.com")
        self.assertEqual(self.ballot()["contact"], "tim@example.com")

    def test_clearing_the_box_takes_you_off_the_list(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com"},
                "198.51.100.1")
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": ""}, "198.51.100.1")
        self.assertEqual(out["contact"], "")
        self.assertEqual(self.ballot()["contact"], "")

    def test_a_body_with_no_contact_key_leaves_it_alone(self):
        """An older cached page, or a retry, must not silently unsubscribe
        somebody who asked to be told."""
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com"},
                "198.51.100.1")
        out = pv.cast({"voter": VOTER_A, "picks": ["lobster"]}, "198.51.100.1")
        self.assertEqual(out["contact"], "tim@example.com")
        self.assertEqual(self.ballot()["contact"], "tim@example.com")

    def test_the_public_tally_never_carries_a_contact(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"],
                 "contact": "tim@example.com"}, "198.51.100.1")
        pv.cast({"voter": VOTER_B, "picks": ["lobster"],
                 "contact": "6155550100"}, "198.51.100.2")
        blob = json.dumps(pv.tally())
        self.assertNotIn("tim@example.com", blob)
        self.assertNotIn("6155550100", blob)
        h = FakeHandler()
        pv.handle_get(h, Parsed("/api/plush/tally"))
        self.assertNotIn("contact", json.dumps(h.payload))


class TestAdminList(unittest.TestCase):
    def setUp(self):
        self.db = reset()

    def test_only_ballots_with_a_contact_are_listed(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com"},
                "198.51.100.1")
        pv.cast({"voter": VOTER_B, "picks": ["lobster"]}, "198.51.100.2")
        rows = pv.contact_rows()
        self.assertEqual([r["contact"] for r in rows], ["tim@example.com"])
        self.assertEqual(rows[0]["picks"], ["Narwhal"])
        self.assertEqual(rows[0]["kind"], "email")
        self.assertTrue(rows[0]["atIso"])

    def test_one_person_on_two_devices_is_told_once(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com"},
                "198.51.100.1")
        pv.cast({"voter": VOTER_B, "picks": ["lobster"], "contact": "TIM@example.com"},
                "198.51.100.2")
        rows = pv.contact_rows()
        self.assertEqual(len(rows), 1, "two ballots, one person to tell")

    def test_the_admin_view_ranks_the_critters(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal", "lobster"]}, "198.51.100.1")
        pv.cast({"voter": VOTER_B, "picks": ["narwhal"]}, "198.51.100.2")
        view = pv.admin_view()
        self.assertEqual(view["ballots"], 2)
        self.assertEqual(view["standings"][0]["name"], "Narwhal")
        self.assertEqual(view["standings"][0]["votes"], 2)
        self.assertEqual(view["standings"][0]["firsts"], 2)
        self.assertEqual(view["emails"] + view["phones"], 0)

    def test_the_csv_carries_no_voter_id_and_no_ip(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com",
                 "note": "the horn", "name": "Tim"}, "198.51.100.77")
        text = pv.contacts_csv(pv.contact_rows())
        self.assertIn("tim@example.com", text)
        self.assertIn("Narwhal", text)
        self.assertNotIn(VOTER_A, text)
        self.assertNotIn("198.51.100.77", text)

    def test_a_leading_plus_cannot_become_a_spreadsheet_formula(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "+447700900123"},
                "198.51.100.1")
        text = pv.contacts_csv(pv.contact_rows())
        self.assertIn("\"'+447700900123\"", text)

    def test_the_list_is_empty_without_a_store(self):
        pv.init(get_firestore=lambda: None)
        self.assertEqual(pv.contact_rows(), [])
        self.assertFalse(pv.admin_view()["enabled"])


# ═══════════════════════════════════════════════════════════════════════════
#  10. THE NEWSLETTER OPT-IN BESIDE THE BALLOT
# ═══════════════════════════════════════════════════════════════════════════
class TestNewsletterOptIn(unittest.TestCase):
    """Two separate questions. Leaving a way to be told who wins must never
    subscribe anybody; the tick, and only the tick, does. And the tick goes
    through the newsletter's own public signup, so the consent rules are the
    newsletter's rules wherever the box happens to be drawn."""

    def setUp(self):
        self.db = reset()
        self.calls = []

        def fake_signup(email, *, source="", note=""):
            self.calls.append({"email": email, "source": source, "note": note})
            return {"ok": True, "status": 200, "message": "You're on the list!"}

        pv.init(get_firestore=lambda: self.db, public_signup=fake_signup,
                newsletter_source="Plushy Vote")

    def test_a_contact_alone_never_subscribes(self):
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"],
                       "contact": "tim@example.com"}, "198.51.100.1")
        self.assertTrue(out["ok"])
        self.assertEqual(self.calls, [], "a vote is not a signup")
        self.assertNotIn("newsletter", out)

    def test_an_explicit_false_never_subscribes(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com",
                 "newsletter": False}, "198.51.100.1")
        self.assertEqual(self.calls, [])

    def test_the_tick_subscribes_through_the_newsletter(self):
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"],
                       "contact": " Tim@Example.COM ", "newsletter": True}, "198.51.100.1")
        self.assertTrue(out["ok"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["email"], "tim@example.com",
                         "the normalised address, not what was typed")
        self.assertEqual(self.calls[0]["source"], "Plushy Vote")
        self.assertIn("plushy", self.calls[0]["note"].lower(),
                      "the consent note has to say where they ticked it")
        self.assertTrue(out["newsletter"])

    def test_a_string_true_counts_but_a_string_false_does_not(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "a@b.com",
                 "newsletter": "false"}, "198.51.100.1")
        self.assertEqual(self.calls, [], '"false" is not consent')
        pv.cast({"voter": VOTER_B, "picks": ["narwhal"], "contact": "c@d.com",
                 "newsletter": "true"}, "198.51.100.2")
        self.assertEqual(len(self.calls), 1)

    def test_a_phone_number_cannot_join_an_email_list(self):
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "6155550100",
                       "newsletter": True}, "198.51.100.1")
        self.assertTrue(out["ok"], "the vote still counts")
        self.assertEqual(self.calls, [])
        self.assertIn("email", out["newsletterError"].lower())

    def test_a_tick_with_no_contact_at_all_is_told_so(self):
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "newsletter": True},
                      "198.51.100.1")
        self.assertTrue(out["ok"])
        self.assertEqual(self.calls, [])
        self.assertTrue(out["newsletterError"])

    def test_a_refused_signup_is_surfaced_not_swallowed(self):
        pv.init(get_firestore=lambda: self.db,
                public_signup=lambda e, **k: {"ok": False, "status": 503,
                                              "error": "Signups are temporarily unavailable."})
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com",
                       "newsletter": True}, "198.51.100.1")
        self.assertTrue(out["ok"], "the vote is not lost because the list is down")
        self.assertIn("unavailable", out["newsletterError"])
        self.assertNotIn("newsletter", {k: v for k, v in out.items() if k != "newsletterError"})

    def test_a_signup_that_throws_does_not_lose_the_vote(self):
        def boom(email, **kw):
            raise RuntimeError("firestore exploded")
        pv.init(get_firestore=lambda: self.db, public_signup=boom)
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com",
                       "newsletter": True}, "198.51.100.1")
        self.assertTrue(out["ok"])
        self.assertTrue(out["newsletterError"])
        self.assertEqual(self.db.store["plush_votes/" + VOTER_A]["picks"], ["narwhal"])

    def test_no_newsletter_wired_up_at_all(self):
        pv.init(get_firestore=lambda: self.db)
        out = pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com",
                       "newsletter": True}, "198.51.100.1")
        self.assertTrue(out["ok"])
        self.assertTrue(out["newsletterError"])

    def test_the_server_hands_the_poll_the_real_signup(self):
        srv = open(os.path.join(ROOT, "multiplayer_server.py"), encoding="utf-8").read()
        self.assertIn("public_signup=newsletter_server.public_signup", srv)
        self.assertIn("newsletter_source=newsletter_server.SOURCE_PLUSHY", srv)

    def test_the_newsletter_owns_the_one_public_signup(self):
        """Both the newsletter form and the vote's box end up in the same
        function, or the consent rules are written twice."""
        import newsletter_server
        self.assertTrue(callable(newsletter_server.public_signup))
        ns = open(os.path.join(ROOT, "newsletter_server.py"), encoding="utf-8").read()
        action = ns[ns.index('if action == "subscribe":'):]
        action = action[:action.index('if action == "confirm":')]
        self.assertIn("public_signup(", action,
                      "the website form must go through the same function")

    def test_the_page_asks_separately_and_never_pre_ticks(self):
        html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        self.assertIn('id="plushNews"', html)
        self.assertIn("newsletter: joinBox.checked", html)
        box = html[html.index('id="plushNews"'):]
        box = box[:box.index(">") + 1]
        self.assertNotIn("checked", box, "the newsletter box must never ship pre-ticked")
        label = html[html.index('class="plush-join"'):html.index('id="plushSay"')]
        self.assertIn("unsubscribe", label.lower(),
                      "say how to get off the list where you ask them to join it")

    def test_the_privacy_policy_describes_the_opt_in(self):
        pol = open(os.path.join(ROOT, "multiplayer", "client", "js",
                                "privacy-policy.js"), encoding="utf-8").read()
        section = pol[pol.index('title: "The Plushy Vote"'):]
        section = section[:section.index('title: "Messages and Communication"')]
        self.assertIn("never ticked for you", section)
        self.assertIn("unsubscribe", section.lower())


# ═══════════════════════════════════════════════════════════════════════════
#  11. THE NEWSLETTER TAB
# ═══════════════════════════════════════════════════════════════════════════
class TestNewsletterTab(unittest.TestCase):
    """The tab is served by the newsletter admin API, so it has to sit behind
    the same verified-token door as every other tab, and it must not turn
    anybody into a subscriber."""

    @classmethod
    def setUpClass(cls):
        import newsletter_server
        cls.ns = newsletter_server

    def setUp(self):
        self.db = reset()
        self._claims = self.ns._admin_claims

    def tearDown(self):
        self.ns._admin_claims = self._claims

    def call(self, action, body=None, admin=True):
        self.ns._admin_claims = (lambda b, h=None: {"email": "admin@example.com"}) if admin \
            else (lambda b, h=None: None)
        h = FakeHandler("203.0.113.99")
        handled = self.ns.handle_post(h, Parsed("/api/newsletter/" + action), body or {})
        return handled, h

    def test_the_tab_reads_the_vote(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com"},
                "198.51.100.1")
        handled, h = self.call("plushy")
        self.assertTrue(handled)
        self.assertEqual(h.status, 200)
        self.assertTrue(h.payload["ok"])
        self.assertEqual(h.payload["ballots"], 1)
        self.assertEqual(h.payload["contacts"][0]["contact"], "tim@example.com")

    def test_a_stranger_gets_nothing(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com"},
                "198.51.100.1")
        handled, h = self.call("plushy", admin=False)
        self.assertTrue(handled)
        self.assertNotEqual(h.status, 200)
        self.assertNotIn("tim@example.com", json.dumps(h.payload))

    def test_export_returns_a_csv(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com"},
                "198.51.100.1")
        handled, h = self.call("plushy-export")
        self.assertTrue(handled)
        self.assertTrue(h.payload["ok"])
        self.assertEqual(h.payload["rows"], 1)
        self.assertIn("tim@example.com", h.payload["csv"])
        self.assertTrue(h.payload["filename"].endswith(".csv"))

    def test_export_can_be_narrowed_to_one_kind(self):
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com"},
                "198.51.100.1")
        pv.cast({"voter": VOTER_B, "picks": ["lobster"], "contact": "6155550100"},
                "198.51.100.2")
        _, h = self.call("plushy-export", {"kind": "phone"})
        self.assertEqual(h.payload["rows"], 1)
        self.assertNotIn("tim@example.com", h.payload["csv"])

    def test_voting_never_creates_a_subscriber(self):
        """The one invariant the newsletter module is built around."""
        pv.cast({"voter": VOTER_A, "picks": ["narwhal"], "contact": "tim@example.com"},
                "198.51.100.1")
        for key in self.db.store:
            self.assertTrue(key.startswith("plush_votes/"),
                            "a vote wrote outside the poll: " + key)

    def test_the_admin_page_has_the_tab(self):
        js = open(os.path.join(ROOT, "multiplayer", "client", "js",
                               "newsletter-admin.js"), encoding="utf-8").read()
        css = open(os.path.join(ROOT, "multiplayer", "client", "css",
                                "newsletter.css"), encoding="utf-8").read()
        self.assertIn('id: "plushy"', js)
        self.assertIn("plushy: viewPlushy", js)
        self.assertIn("function viewPlushy", js)
        self.assertIn('api("plushy")', js)
        self.assertIn('api("plushy-export"', js)
        for cls in ("n-row-between", "n-meter", "n-meter-fill"):
            self.assertIn("." + cls, css, "no styles for " + cls)
        # The tab must say what the list is and is not.
        self.assertIn("not newsletter subscribers", js)

    def test_the_tab_reads_only_fields_the_server_sends(self):
        """The drift that shows up as a column of "undefined" in a page only
        Tim ever opens: every r.<field> the view reads has to exist on a real
        row, and every d.<field> on a real payload."""
        js = open(os.path.join(ROOT, "multiplayer", "client", "js",
                               "newsletter-admin.js"), encoding="utf-8").read()
        body = js[js.index("function viewPlushy"):js.index("function doPlushyExport")]

        pv.cast({"voter": VOTER_A, "picks": ["narwhal", "lobster"],
                 "contact": "tim@example.com", "name": "Tim"}, "198.51.100.1")
        payload = pv.admin_view()
        row = pv.contact_rows()[0]
        standing = payload["standings"][0]

        # Both loops name their row `r`, so each block is checked against the
        # shape it actually iterates.
        standings_block = body[body.index("d.standings.forEach"):body.index("d.contacts.forEach")]
        contacts_block = body[body.index("d.contacts.forEach"):]
        for field in sorted(set(re.findall(r"\br\.([a-zA-Z]+)", standings_block))):
            self.assertIn(field, standing,
                          "the standings table reads r.%s, which no standing has" % field)
        for field in sorted(set(re.findall(r"\br\.([a-zA-Z]+)", contacts_block))):
            self.assertIn(field, row,
                          "the contact table reads r.%s, which no row has" % field)
        # ok and error are the envelope every admin action answers with.
        allowed = {**payload, "ok": True, "error": ""}
        for field in sorted(set(re.findall(r"\bd\.([a-zA-Z]+)", body))):
            self.assertIn(field, allowed,
                          "viewPlushy reads d.%s, which the server never sends" % field)

    def test_the_website_asks_for_it_and_sends_it(self):
        html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        self.assertIn('id="plushContact"', html)
        self.assertIn("contact: contact.value.trim()", html)
        self.assertIn("cc-plush-contact", html)
        # The ask has to say what the address is for, right where it is asked.
        start = html.index('id="plushContact"')
        label = html[html.index('class="plush-ask"'):start]
        self.assertIn("Optional", label)
        self.assertIn("result", label)


if __name__ == "__main__":
    unittest.main(verbosity=2)
