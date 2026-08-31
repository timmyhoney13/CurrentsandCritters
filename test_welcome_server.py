"""test_welcome_server.py, the welcome bonus and the dev's friends roster.

Both features hand out something the browser must not be able to hand itself,
so the tests are about who gets it, how often, and who cannot get it at all:

  THE BONUS
   1. An account is paid the bonus exactly once, ever.  → the ledger create()
   2. A second call is a SUCCESS that pays nothing, not an error: every
      sign-in asks, so "you already had this" is the normal answer.
   3. The derived level fields move with total_xp, or the header and the
      leaderboard would show a level the account has already left behind.
   4. The bonus ADDS to whatever XP is already there; it never replaces it.
   5. A guest gets nothing: no token at all in the real game, and an
      anonymous token is refused by name.
   6. An account document that does not exist yet is refused rather than
      created, and stays claimable on the next sign-in.

  THE ROSTER
   7. Every player lands in the dev's friends list, and the dev never
      befriends itself.
   8. A second sync writes nothing (only the missing ones are written).
   9. It is ONE-WAY: no player's own friends list is touched.
  10. Only the developer account may ask; everybody else gets 403.
  11. A made-up uid handed to the register hook is not written.

    python3 test_welcome_server.py
"""
from __future__ import annotations

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import welcome_server as ws  # noqa: E402

# The in-memory Firestore lives next door. Importing it rather than pasting a
# third copy is what keeps the suites testing the same Firestore semantics,
# especially set(merge=True) merging nested maps and the create() that raises.
from test_level_pass_server import (  # noqa: E402
    FakeColl, FakeDb, FakeDoc, FakeHandler, FakeSnap, Parsed, _FakeQuery,
    level_progress,
)

PREVIEW_JS = os.path.join(ROOT, "multiplayer", "client", "js", "preview-app.js")
PREVIEW_HTML = os.path.join(ROOT, "multiplayer", "client", "preview.html")
SERVER_PY = os.path.join(ROOT, "multiplayer_server.py")
WELCOME_PY = os.path.join(ROOT, "welcome_server.py")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ══════════════════════════════════════════════════════════════════════════
#  The two things welcome_server needs that the shared fake does not have:
#  sub-collections (users/{uid}/friends/{uid}) and batched writes.
# ══════════════════════════════════════════════════════════════════════════
class Doc(FakeDoc):
    def collection(self, name):
        subs = self._coll._subs.setdefault(self.id, {})
        if name not in subs:
            subs[name] = Coll(self._coll._db, f"{self._coll.name}/{self.id}/{name}")
        return subs[name]


class Coll(FakeColl):
    def __init__(self, db, name):
        super().__init__(db, name)
        self._subs = {}
        self._refs = {}

    def document(self, doc_id):
        # The SAME ref object every time, so a sub-collection reached through
        # two lookups is one sub-collection.
        if doc_id not in self._refs:
            self._refs[doc_id] = Doc(self, doc_id)
        return self._refs[doc_id]

    def select(self, _fields):
        return _FakeQuery([FakeSnap(k, v) for k, v in self._docs.items()])


class Batch:
    def __init__(self, db):
        self._db = db
        self._ops = []

    def set(self, ref, data, merge=False):
        self._ops.append((ref, data, merge))

    def commit(self):
        self._db.commits += 1
        self._db.writes += len(self._ops)
        for ref, data, merge in self._ops:
            ref.set(data, merge=merge)
        self._ops = []


class Db(FakeDb):
    def __init__(self):
        super().__init__()
        self.commits = 0
        self.writes = 0

    def collection(self, name):
        if name not in self._colls:
            self._colls[name] = Coll(self, name)
        return self._colls[name]

    def batch(self):
        return Batch(self)


ADMIN = ws.ADMIN_EMAIL


class WelcomeBase(unittest.TestCase):
    def setUp(self):
        self.db = Db()
        ws.init(
            get_firestore=lambda: self.db,
            verify_token=self.fake_verify,
            level_progress=level_progress,
        )
        ws._transactional = lambda: (lambda fn: fn)   # type: ignore[assignment]
        # Module-level caches survive between tests otherwise, and a cached dev
        # uid or a rate-limited sync from an earlier test is a false pass.
        ws._DEV_UID.update(uid="", at=0.0)
        ws._LAST_SYNC.update(uid="", at=0.0, result={})
        os.environ.pop("WELCOME_XP", None)

    @staticmethod
    def fake_verify(tok):
        """`good:<uid>` is a signed-in account, `dev:<uid>` carries the admin
        email, `anon:<uid>` is an anonymous Firebase session."""
        if tok.startswith("good:"):
            return {"uid": tok[5:], "email": tok[5:] + "@example.com"}
        if tok.startswith("dev:"):
            return {"uid": tok[4:], "email": ADMIN}
        if tok.startswith("anon:"):
            return {"uid": tok[5:], "firebase": {"sign_in_provider": "anonymous"}}
        return None

    # ── fixtures ─────────────────────────────────────────────────────────
    def make_user(self, uid, *, nickname=None, xp=0, **extra):
        nickname = nickname or uid.upper()
        doc = {"nickname": nickname, "avatar_url": f"/avatars/{uid}.png",
               "stats": {"total_xp": xp}}
        doc.update(extra)
        self.db.collection("users")._docs[uid] = doc
        return doc

    def make_dev(self, uid="dev"):
        return self.make_user(uid, nickname="Dev", email=ADMIN, is_admin=True)

    def user(self, uid):
        return self.db.collection("users").document(uid).get().to_dict()

    def stats(self, uid):
        return self.user(uid).get("stats") or {}

    def ledger(self):
        return self.db.collection("welcome_bonuses")._docs

    def friends_of(self, uid):
        return dict(self.db.collection("users").document(uid)
                    .collection("friends")._docs)

    def post(self, action, token):
        h = FakeHandler()
        handled = ws.handle_post(h, Parsed(f"/api/welcome/{action}"), {"idToken": token})
        self.assertTrue(handled, "welcome_server did not claim its own route")
        return h


# ══════════════════════════════════════════════════════════════════════════
#  THE WELCOME BONUS
# ══════════════════════════════════════════════════════════════════════════
class Bonus(WelcomeBase):
    def test_a_new_account_is_paid_the_bonus(self):
        self.make_user("u1")
        res = ws.grant_welcome_bonus(self.db, "u1")
        self.assertTrue(res["ok"])
        self.assertTrue(res["granted"])
        self.assertEqual(res["xp"], ws.welcome_xp())
        self.assertEqual(self.stats("u1")["total_xp"], ws.welcome_xp())

    def test_the_default_bonus_is_two_hundred_xp(self):
        """The number Tim asked for. An economy dial, but not one that should
        move by accident."""
        self.assertEqual(ws.DEFAULT_WELCOME_XP, 200)
        self.assertEqual(ws.welcome_xp(), 200)

    def test_the_bonus_is_paid_once_ever(self):
        self.make_user("u1")
        first = ws.grant_welcome_bonus(self.db, "u1")
        second = ws.grant_welcome_bonus(self.db, "u1")
        third = ws.grant_welcome_bonus(self.db, "u1")
        self.assertTrue(first["granted"])
        self.assertFalse(second["granted"])
        self.assertFalse(third["granted"])
        self.assertEqual(self.stats("u1")["total_xp"], ws.welcome_xp(),
                         "the bonus was paid more than once")
        self.assertEqual(len(self.ledger()), 1)

    def test_asking_again_is_a_success_not_an_error(self):
        """Every sign-in asks. If "already paid" came back as an error the
        client would show a failure to a player who has nothing wrong."""
        self.make_user("u1")
        ws.grant_welcome_bonus(self.db, "u1")
        again = ws.grant_welcome_bonus(self.db, "u1")
        self.assertTrue(again["ok"])
        self.assertNotIn("error", again)

    def test_the_bonus_adds_to_xp_already_earned(self):
        """The retroactive half: an account that has been playing for months
        keeps every point it earned and gains the bonus on top."""
        self.make_user("veteran", xp=12_345)
        ws.grant_welcome_bonus(self.db, "veteran")
        self.assertEqual(self.stats("veteran")["total_xp"], 12_345 + ws.welcome_xp())

    def test_the_derived_level_fields_move_with_total_xp(self):
        """total_xp is the truth, but the header, the leaderboard and the Level
        Pass all read the fields beside it. Leaving them behind shows a level
        the account has already left."""
        self.make_user("u1")
        ws.grant_welcome_bonus(self.db, "u1")
        s = self.stats("u1")
        lvl, cur, goal = level_progress(ws.welcome_xp())
        for key, want in (("level", lvl), ("player_level", lvl),
                          ("xp_current", cur), ("level_xp_current", cur),
                          ("xp_goal", goal), ("level_xp_goal", goal)):
            self.assertEqual(s[key], want, f"stats.{key} did not follow total_xp")

    def test_the_bonus_never_touches_the_rest_of_the_stats_map(self):
        self.make_user("u1", xp=100)
        self.db.collection("users")._docs["u1"]["stats"].update(
            {"critter_coins": 900, "completed_games": 7, "daily_streak": 3})
        ws.grant_welcome_bonus(self.db, "u1")
        s = self.stats("u1")
        self.assertEqual(s["critter_coins"], 900)
        self.assertEqual(s["completed_games"], 7)
        self.assertEqual(s["daily_streak"], 3)

    def test_an_account_that_does_not_exist_yet_is_refused_and_stays_claimable(self):
        """Mid-onboarding the user document may not be written yet. Refusing
        (rather than creating one) keeps this endpoint incapable of inventing
        an account, and burns nothing: the next sign-in collects."""
        res = ws.grant_welcome_bonus(self.db, "ghost")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "no_account")
        self.assertEqual(self.ledger(), {}, "a refused claim wrote a ledger entry")
        self.make_user("ghost")
        self.assertTrue(ws.grant_welcome_bonus(self.db, "ghost")["granted"])

    def test_the_ledger_entry_records_what_was_paid(self):
        self.make_user("u1", xp=50)
        ws.grant_welcome_bonus(self.db, "u1")
        rec = self.ledger()["u1"]
        self.assertEqual(rec["xp"], ws.welcome_xp())
        self.assertEqual(rec["total_xp_before"], 50)
        self.assertEqual(rec["total_xp_after"], 50 + ws.welcome_xp())

    def test_the_amount_is_configurable_but_clamped(self):
        os.environ["WELCOME_XP"] = "500"
        self.assertEqual(ws.welcome_xp(), 500)
        os.environ["WELCOME_XP"] = "-5"
        self.assertEqual(ws.welcome_xp(), 0)
        os.environ["WELCOME_XP"] = "nonsense"
        self.assertEqual(ws.welcome_xp(), ws.DEFAULT_WELCOME_XP)


# ══════════════════════════════════════════════════════════════════════════
#  WHO MAY COLLECT IT
# ══════════════════════════════════════════════════════════════════════════
class BonusRoutes(WelcomeBase):
    def test_a_signed_in_account_collects_over_http(self):
        self.make_user("u1")
        h = self.post("claim", "good:u1")
        self.assertTrue(h.payload["ok"])
        self.assertTrue(h.payload["granted"])

    def test_no_token_collects_nothing(self):
        """A guest holds no Firebase session at all, so this is the door a
        guest arrives at: shut, with nothing written."""
        self.make_user("u1")
        h = self.post("claim", "")
        self.assertEqual(h.status, 401)
        self.assertFalse(h.payload["ok"])
        self.assertEqual(self.ledger(), {})

    def test_an_anonymous_session_is_refused_by_name(self):
        """A guest with a token is still a guest. The game's guests do not have
        one, but this is the door that would let them in if that changed."""
        self.make_user("u1")
        h = self.post("claim", "anon:u1")
        self.assertEqual(h.status, 403)
        self.assertEqual(h.payload["error"], "guest")
        self.assertEqual(self.ledger(), {})

    def test_the_uid_comes_from_the_token_never_the_body(self):
        """A browser cannot name the account to pay."""
        self.make_user("u1")
        self.make_user("victim")
        h = FakeHandler()
        ws.handle_post(h, Parsed("/api/welcome/claim"),
                       {"idToken": "good:u1", "uid": "victim"})
        self.assertEqual(self.stats("victim")["total_xp"], 0)
        self.assertEqual(self.stats("u1")["total_xp"], ws.welcome_xp())

    def test_an_unknown_action_is_a_404_not_a_payout(self):
        h = self.post("everything", "good:u1")
        self.assertEqual(h.status, 404)


# ══════════════════════════════════════════════════════════════════════════
#  THE DEV ROSTER
# ══════════════════════════════════════════════════════════════════════════
class Roster(WelcomeBase):
    def test_every_player_lands_in_the_dev_friends_list(self):
        self.make_dev()
        for uid in ("a", "b", "c"):
            self.make_user(uid)
        res = ws.sync_dev_roster(self.db, "dev")
        self.assertTrue(res["ok"])
        self.assertEqual(res["added"], 3)
        self.assertEqual(set(self.friends_of("dev")), {"a", "b", "c"})

    def test_the_dev_is_never_its_own_friend(self):
        self.make_dev()
        self.make_user("a")
        ws.sync_dev_roster(self.db, "dev")
        self.assertNotIn("dev", self.friends_of("dev"))

    def test_a_row_carries_the_name_and_avatar_as_a_fallback(self):
        self.make_dev()
        self.make_user("a", nickname="Reef Runner")
        ws.sync_dev_roster(self.db, "dev")
        row = self.friends_of("dev")["a"]
        self.assertEqual(row["uid"], "a")
        self.assertEqual(row["nickname"], "Reef Runner")
        self.assertEqual(row["avatar_url"], "/avatars/a.png")
        self.assertTrue(row["auto"], "a roster row must be marked as one")

    def test_a_second_sync_writes_nothing(self):
        self.make_dev()
        for uid in ("a", "b"):
            self.make_user(uid)
        ws.sync_dev_roster(self.db, "dev")
        before = self.db.writes
        ws._LAST_SYNC.update(at=0.0)          # step past the rate limit
        res = ws.sync_dev_roster(self.db, "dev")
        self.assertEqual(res["added"], 0)
        self.assertEqual(self.db.writes, before,
                         "a repeat sync rewrote rows that were already there")

    def test_only_the_new_ones_are_written(self):
        self.make_dev()
        self.make_user("a")
        ws.sync_dev_roster(self.db, "dev")
        self.make_user("b")
        ws._LAST_SYNC.update(at=0.0)
        before = self.db.writes
        res = ws.sync_dev_roster(self.db, "dev")
        self.assertEqual(res["added"], 1)
        self.assertEqual(self.db.writes - before, 1)
        self.assertEqual(set(self.friends_of("dev")), {"a", "b"})

    def test_the_roster_is_one_way(self):
        """The dev sees everybody. Nobody's own account is touched: no friends
        entry, no friend request, nothing to notice."""
        self.make_dev()
        self.make_user("a")
        ws.sync_dev_roster(self.db, "dev")
        self.assertEqual(self.friends_of("a"), {},
                         "a player's own friends list was written to")
        self.assertEqual(
            self.db.collection("users").document("a")
                .collection("friend_requests")._docs, {})

    def test_the_rate_limit_short_circuits_a_reloaded_page(self):
        self.make_dev()
        self.make_user("a")
        ws.sync_dev_roster(self.db, "dev")
        self.make_user("b")
        res = ws.sync_dev_roster(self.db, "dev")   # straight away
        self.assertTrue(res["cached"])
        self.assertNotIn("b", self.friends_of("dev"),
                         "the rate limit was not applied")

    def test_a_big_roster_is_written_in_batches(self):
        self.make_dev()
        for i in range(ws._WRITE_BATCH + 25):
            self.make_user(f"p{i:04d}")
        res = ws.sync_dev_roster(self.db, "dev")
        self.assertEqual(res["added"], ws._WRITE_BATCH + 25)
        self.assertGreaterEqual(self.db.commits, 2,
                                "more than 500 writes in one batch is refused "
                                "by Firestore, so it has to be split")


class RosterRoutes(WelcomeBase):
    def test_the_dev_account_may_sync(self):
        self.make_dev()
        self.make_user("a")
        h = self.post("roster", "dev:dev")
        self.assertTrue(h.payload["ok"])
        self.assertEqual(h.payload["added"], 1)

    def test_an_ordinary_player_may_not(self):
        self.make_dev()
        self.make_user("a")
        h = self.post("roster", "good:a")
        self.assertEqual(h.status, 403)
        self.assertEqual(h.payload["error"], "forbidden")
        self.assertEqual(self.friends_of("a"), {},
                         "a stranger's sync wrote a roster anyway")

    def test_a_password_account_is_recognised_by_its_document(self):
        """A username-and-password login carries a synthetic address, so the
        token's email is not the admin one. The account document is."""
        self.make_user("dev", nickname="Dev", email=ADMIN, is_admin=True)
        self.make_user("a")
        h = self.post("roster", "good:dev")
        self.assertTrue(h.payload["ok"])
        self.assertEqual(set(self.friends_of("dev")), {"a"})


class RegisterHook(WelcomeBase):
    def test_a_new_account_joins_the_roster_immediately(self):
        self.make_dev()
        self.make_user("newbie")
        self.assertTrue(ws.add_dev_friend(self.db, "newbie"))
        self.assertIn("newbie", self.friends_of("dev"))

    def test_a_made_up_uid_is_not_written(self):
        """The register endpoint takes a raw uid from the browser, so this is
        the check that stops invented ids becoming rows in the dev's list."""
        self.make_dev()
        self.assertFalse(ws.add_dev_friend(self.db, "not-a-real-account"))
        self.assertEqual(self.friends_of("dev"), {})

    def test_it_is_a_quiet_no_op_when_there_is_no_dev_account(self):
        self.make_user("newbie")
        self.assertFalse(ws.add_dev_friend(self.db, "newbie"))

    def test_the_dev_is_found_by_email_then_by_the_flag(self):
        self.make_user("d1", email=ADMIN)
        self.assertEqual(ws.dev_uid(self.db), "d1")
        ws._DEV_UID.update(uid="", at=0.0)
        del self.db.collection("users")._docs["d1"]["email"]
        self.db.collection("users")._docs["d1"]["is_admin"] = True
        self.assertEqual(ws.dev_uid(self.db), "d1")


# ══════════════════════════════════════════════════════════════════════════
#  WIRING: the halves that have to agree with each other
# ══════════════════════════════════════════════════════════════════════════
class Wiring(unittest.TestCase):
    def setUp(self):
        self.client = _read(PREVIEW_JS)
        self.server = _read(SERVER_PY)

    def test_the_client_claims_the_bonus_and_syncs_the_roster(self):
        self.assertIn('apiPost("/api/welcome/claim"', self.client)
        self.assertIn('apiPost("/api/welcome/roster"', self.client)

    def test_both_calls_carry_an_id_token(self):
        for action in ("claim", "roster"):
            m = re.search(r'apiPost\("/api/welcome/%s", *\{([^}]*)\}' % action,
                          self.client)
            self.assertIsNotNone(m, f"the {action} call moved")
            self.assertIn("idToken", m.group(1),
                          f"the {action} call stopped proving who is asking")

    def test_the_bonus_is_claimed_on_every_way_into_an_account(self):
        """revealRegisteredLobby primes the reward modules; a brand-new account
        finishes at revealLobby instead, so THAT path has to prime too or the
        bonus waits for a second sign-in."""
        self.assertIn("_ccClaimWelcomeBonus", self.client)
        body = self.client[self.client.index("async function finishNicknameSetup"):]
        body = body[:body.index("\n    $a(\"auth-nick-input\")")]
        self.assertIn("__ccPrimeRewardModules", body)

    def test_the_server_routes_the_endpoint_and_hooks_registration(self):
        self.assertIn("import welcome_server", self.server)
        self.assertIn("welcome_server.handle_post(self, parsed, body)", self.server)
        self.assertIn("welcome_server.add_dev_friend", self.server)

    def test_the_server_injects_the_one_level_curve(self):
        """A second copy of the XP table is exactly the drift that demotes live
        players, so the bonus derives its level fields from the injected one."""
        m = re.search(r"welcome_server\.init\((.*?)\)\n", self.server, re.S)
        self.assertIsNotNone(m, "welcome_server.init() is not wired in main()")
        self.assertIn("level_progress=_level_progress_for_total_xp", m.group(1))
        self.assertNotIn("LEVEL_XP_TOTALS", _read(WELCOME_PY))

    def test_the_admin_email_matches_the_rest_of_the_server(self):
        self.assertIn(ws.ADMIN_EMAIL, self.server)
        self.assertIn('ANALYTICS_ADMIN_EMAIL = "%s"' % ws.ADMIN_EMAIL, self.client)

    def test_the_friends_tab_counts_who_is_online(self):
        self.assertIn("paintFriendsOnlineCount", self.client)
        self.assertIn('id="ph-friends-online"', _read(PREVIEW_HTML))


if __name__ == "__main__":
    unittest.main(verbosity=2)
