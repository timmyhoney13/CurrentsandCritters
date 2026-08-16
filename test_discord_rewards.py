"""Tests for the Discord join reward (discord_server.py).

Three jobs, in order of how much damage the bug would do:

 1. NOBODY IS PAID TWICE. Once per game account, once per Discord account, and
    the guard has to hold when two requests race. This is the whole point of
    the ledger doc-id create(), so it is tested from every direction.

 2. NOBODY IS PAID WITHOUT BEING A MEMBER. Discord saying "no", Discord not
    answering at all, a garbled reply, a missing scope — none of them may be
    mistaken for a yes, and none of them may write a coin.

 3. THE SIGNED STATE. Discord's callback carries no Firebase token, so the
    signed `state` is the only thing that says whose account a redirect is for.
    Forged, tampered, expired and replayed states all have to bounce BEFORE any
    account is touched.

Run:  python3 test_discord_rewards.py
"""
from __future__ import annotations

import copy
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discord_server as ds  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def read(*parts) -> str:
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


# ══════════════════════════════════════════════════════════════════════════
#  A tiny in-memory Firestore, enough for the transaction under test
# ══════════════════════════════════════════════════════════════════════════
def _deep_merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


class AlreadyExists(Exception):
    pass


class FakeSnap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class FakeDoc:
    def __init__(self, coll, doc_id):
        self._coll = coll
        self.id = doc_id

    def get(self, transaction=None):
        return FakeSnap(self.id, self._coll._docs.get(self.id))

    def set(self, data, merge=False):
        cur = self._coll._docs.get(self.id)
        if merge and isinstance(cur, dict):
            _deep_merge(cur, copy.deepcopy(data))
        else:
            self._coll._docs[self.id] = copy.deepcopy(data)

    def create(self, data):
        # The real guarantee: a doc id that already exists cannot be created.
        if self.id in self._coll._docs:
            raise AlreadyExists(self.id)
        self._coll._docs[self.id] = copy.deepcopy(data)


class FakeColl:
    def __init__(self, db, name):
        self._db = db
        self.name = name
        self._docs = {}

    def document(self, doc_id):
        return FakeDoc(self, doc_id)


class FakeTxn:
    """BUFFERS writes and applies them in one shot at commit, which is the part
    of Firestore that actually matters here: if the ledger create() collides,
    the whole transaction is abandoned and the coin write in the SAME
    transaction never lands either. A fake that wrote as it went would show two
    racing claims paying 500 coins and call it a pass — the bug this test
    exists to catch."""
    def __init__(self, db):
        self._db = db
        self._ops = []

    def set(self, ref, data, merge=False):
        self._ops.append(("set", ref, copy.deepcopy(data), merge))

    def create(self, ref, data):
        self._ops.append(("create", ref, copy.deepcopy(data), False))

    def commit(self):
        with self._db.lock:
            # Every create is checked before anything is written, so a losing
            # transaction leaves the database exactly as it found it.
            for kind, ref, _data, _merge in self._ops:
                if kind == "create" and ref.id in ref._coll._docs:
                    raise AlreadyExists(ref.id)
            for kind, ref, data, merge in self._ops:
                if kind == "create":
                    ref.create(data)
                else:
                    ref.set(data, merge=merge)
        self._ops = []


class FakeDb:
    def __init__(self):
        self._colls = {}
        self.lock = threading.RLock()

    def collection(self, name):
        if name not in self._colls:
            self._colls[name] = FakeColl(self, name)
        return self._colls[name]

    def transaction(self):
        return FakeTxn(self)

    # convenience for the assertions
    def coins(self, uid):
        doc = self._colls["users"]._docs.get(uid) or {}
        return int((doc.get("stats") or {}).get("critter_coins") or 0)

    def ledger_ids(self):
        return sorted((self._colls.get("discord_rewards") or FakeColl(self, "x"))._docs)


def _fake_transactional(fn):
    """Stands in for firestore.transactional: run the body, then commit."""
    def _wrapped(txn, *args, **kwargs):
        result = fn(txn, *args, **kwargs)
        txn.commit()
        return result
    return _wrapped


# ══════════════════════════════════════════════════════════════════════════
#  A fake Discord, so no test ever touches the network
# ══════════════════════════════════════════════════════════════════════════
class FakeDiscord:
    """Stands in for discord_server._request. Records every call so a test can
    assert what was (and was not) asked."""

    def __init__(self, *, token="tok", member=True, user_id="900001",
                 username="reefkid", token_status=200, me_status=200,
                 member_status=None, guilds_status=200, guilds=None):
        self.token = token
        self.member = member
        self.user_id = user_id
        self.username = username
        self.token_status = token_status
        self.me_status = me_status
        self.member_status = member_status
        self.guilds_status = guilds_status
        self.guilds = guilds
        self.calls = []

    def __call__(self, method, url, *, data=None, headers=None):
        self.calls.append((method, url.split("?")[0]))
        if "/oauth2/token/revoke" in url:
            return 200, {}
        if url.endswith("/oauth2/token"):
            if self.token_status != 200:
                return self.token_status, {"error": "invalid_grant"}
            return 200, {"access_token": self.token}
        if url.endswith("/users/@me"):
            if self.me_status != 200:
                return self.me_status, {}
            return 200, {"id": self.user_id, "username": self.username}
        if "/users/@me/guilds/" in url and url.endswith("/member"):
            if self.member_status is not None:
                return self.member_status, {}
            return (200, {"user": {"id": self.user_id}}) if self.member else (404, {})
        if "/users/@me/guilds" in url:
            if self.guilds_status != 200:
                return self.guilds_status, {}
            rows = self.guilds if self.guilds is not None else (
                [{"id": ds._env("DISCORD_GUILD_ID")}] if self.member else [{"id": "77"}])
            return 200, {"data": rows}
        raise AssertionError(f"unexpected Discord call: {method} {url}")


ENV = {
    "DISCORD_CLIENT_ID": "clientid",
    "DISCORD_CLIENT_SECRET": "clientsecret",
    "DISCORD_GUILD_ID": "123456789",
    "DISCORD_REDIRECT_URI": "https://play.currentsandcritters.com/api/discord/callback",
}


class Base(unittest.TestCase):
    def setUp(self):
        self._env_backup = {k: os.environ.get(k) for k in
                            list(ENV) + ["DISCORD_REWARD_COINS", "DISCORD_INVITE_URL",
                                         "DISCORD_STATE_SECRET"]}
        for k in self._env_backup:
            os.environ.pop(k, None)
        os.environ.update(ENV)

        self.db = FakeDb()
        ds.init(get_firestore=lambda: self.db,
                verify_token=lambda tok: ({"uid": tok[5:]} if str(tok).startswith("token")
                                          else None))
        ds._txn_helpers = lambda: _fake_transactional  # type: ignore[assignment]
        ds._USED_NONCES.clear()
        # Revoking is fire-and-forget hygiene; make it a no-op so no test spawns
        # a thread that would try to reach discord.com.
        self._real_revoke = ds._revoke_later
        ds._revoke_later = lambda token: None  # type: ignore[assignment]
        self._real_request = ds._request

    def tearDown(self):
        ds._request = self._real_request  # type: ignore[assignment]
        ds._revoke_later = self._real_revoke  # type: ignore[assignment]
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def account(self, uid, coins=0, nickname="Reefkid"):
        self.db.collection("users").document(uid).set(
            {"nickname": nickname, "stats": {"critter_coins": coins, "games_played": 12}})

    def discord(self, **kw):
        fake = FakeDiscord(**kw)
        ds._request = fake  # type: ignore[assignment]
        return fake


# ══════════════════════════════════════════════════════════════════════════
#  1. THE HAPPY PATH
# ══════════════════════════════════════════════════════════════════════════
class TestClaim(Base):
    def test_member_is_paid_the_advertised_amount(self):
        self.account("alice", coins=40)
        self.discord(member=True, user_id="900001", username="alice_reef")

        out = ds.claim_for_code("alice", "code-1")

        self.assertTrue(out["ok"], out)
        self.assertEqual(out["coins_awarded"], 250)
        self.assertEqual(out["coins_total"], 290)
        self.assertEqual(self.db.coins("alice"), 290)
        # The number the player was promised is the number they were paid.
        self.assertEqual(ds.reward_coins(), out["coins_awarded"])

    def test_reward_amount_is_configurable_and_used_everywhere(self):
        os.environ["DISCORD_REWARD_COINS"] = "500"
        self.account("alice")
        self.discord()
        out = ds.claim_for_code("alice", "code-1")
        self.assertEqual(out["coins_awarded"], 500)
        self.assertEqual(self.db.coins("alice"), 500)

    def test_nothing_else_in_stats_is_disturbed(self):
        self.account("alice", coins=10)
        self.discord()
        ds.claim_for_code("alice", "code-1")
        stats = self.db.collection("users")._docs["alice"]["stats"]
        self.assertEqual(stats["games_played"], 12)
        self.assertEqual(stats["critter_coins"], 260)

    def test_ledger_records_both_sides_of_the_pairing(self):
        self.account("alice")
        self.discord(user_id="900001", username="alice_reef")
        ds.claim_for_code("alice", "code-1")
        self.assertEqual(self.db.ledger_ids(), ["d_900001", "u_alice"])
        rec = self.db.collection("discord_rewards")._docs["u_alice"]
        self.assertEqual(rec["uid"], "alice")
        self.assertEqual(rec["discord_id"], "900001")
        self.assertEqual(rec["discord_username"], "alice_reef")
        self.assertEqual(rec["coins_before"], 0)
        self.assertEqual(rec["coins_after"], 250)

    def test_already_a_member_needs_no_backfill(self):
        """Somebody who joined the Discord months ago runs the same flow and is
        paid on the spot — membership is checked live, never from a list."""
        self.account("oldtimer", coins=1_000)
        self.discord(member=True, user_id="111", username="oldtimer")
        out = ds.claim_for_code("oldtimer", "code-1")
        self.assertTrue(out["ok"])
        self.assertEqual(self.db.coins("oldtimer"), 1_250)

    def test_claim_state_reports_the_payout(self):
        self.account("alice")
        self.assertFalse(ds.claim_state(self.db, "alice")["claimed"])
        self.discord(username="alice_reef")
        ds.claim_for_code("alice", "code-1")
        state = ds.claim_state(self.db, "alice")
        self.assertTrue(state["claimed"])
        self.assertEqual(state["coinsAwarded"], 250)
        self.assertEqual(state["discordUsername"], "alice_reef")


# ══════════════════════════════════════════════════════════════════════════
#  2. NOBODY IS PAID TWICE
# ══════════════════════════════════════════════════════════════════════════
class TestPaidOnce(Base):
    def test_same_account_claiming_again_gets_nothing(self):
        self.account("alice", coins=0)
        self.discord()
        self.assertTrue(ds.claim_for_code("alice", "code-1")["ok"])

        again = ds.claim_for_code("alice", "code-2")
        self.assertFalse(again["ok"])
        self.assertEqual(again["error"], "already_claimed")
        self.assertEqual(self.db.coins("alice"), 250)

    def test_claiming_ten_more_times_still_pays_once(self):
        self.account("alice")
        self.discord()
        for _ in range(11):
            ds.claim_for_code("alice", "code")
        self.assertEqual(self.db.coins("alice"), 250)

    def test_one_discord_account_cannot_pay_an_alt_game_account(self):
        self.account("alice")
        self.account("alice_alt")
        self.discord(user_id="900001")            # the SAME Discord login
        self.assertTrue(ds.claim_for_code("alice", "code-1")["ok"])

        out = ds.claim_for_code("alice_alt", "code-2")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "discord_already_used")
        self.assertEqual(self.db.coins("alice_alt"), 0)

    def test_two_different_people_both_get_paid(self):
        """The alt-account guard must not catch genuine second players."""
        self.account("alice")
        self.account("bob")
        self.discord(user_id="900001")
        ds.claim_for_code("alice", "code-1")
        self.discord(user_id="900002")
        ds.claim_for_code("bob", "code-2")
        self.assertEqual(self.db.coins("alice"), 250)
        self.assertEqual(self.db.coins("bob"), 250)

    def test_a_race_between_two_tabs_pays_once(self):
        """Two tabs, both of which read "not claimed" before either commits —
        the worst possible interleaving. The ledger create() is what decides it,
        so exactly one wins, and the loser's coin write is abandoned with it."""
        self.account("alice")
        self.discord()
        results = []
        barrier = threading.Barrier(2)

        # Hold both transactions at the moment they have finished reading and
        # are about to commit, then let them go at each other.
        original_commit = FakeTxn.commit

        def slow_commit(txn):
            try:
                barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass
            return original_commit(txn)

        FakeTxn.commit = slow_commit
        try:
            threads = [threading.Thread(target=lambda: results.append(
                ds.claim_for_code("alice", "code"))) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
        finally:
            FakeTxn.commit = original_commit

        self.assertEqual(len(results), 2)
        self.assertEqual(sum(1 for r in results if r.get("ok")), 1,
                         f"exactly one claim may succeed, got {results}")
        self.assertEqual(self.db.coins("alice"), 250)
        self.assertEqual(self.db.ledger_ids(), ["d_900001", "u_alice"])

    def test_a_failed_claim_writes_nothing_at_all(self):
        self.account("alice", coins=77)
        self.discord(member=False)
        out = ds.claim_for_code("alice", "code-1")
        self.assertFalse(out["ok"])
        self.assertEqual(self.db.coins("alice"), 77)
        self.assertEqual(self.db.ledger_ids(), [])
        # …and the next, legitimate claim still works.
        self.discord(member=True)
        self.assertTrue(ds.claim_for_code("alice", "code-2")["ok"])
        self.assertEqual(self.db.coins("alice"), 327)


# ══════════════════════════════════════════════════════════════════════════
#  3. NOBODY IS PAID WITHOUT BEING A MEMBER
# ══════════════════════════════════════════════════════════════════════════
class TestMembership(Base):
    def test_not_a_member_is_refused(self):
        self.account("alice")
        self.discord(member=False)
        out = ds.claim_for_code("alice", "code-1")
        self.assertEqual(out["error"], "not_a_member")
        self.assertEqual(self.db.coins("alice"), 0)

    def test_discord_unreachable_is_not_a_yes(self):
        self.account("alice")
        fake = self.discord(member=True, member_status=0, guilds_status=0)
        out = ds.claim_for_code("alice", "code-1")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "discord_unreachable")
        self.assertEqual(self.db.coins("alice"), 0)
        self.assertEqual(self.db.ledger_ids(), [])
        self.assertIn(("GET", "https://discord.com/api/v10/users/@me/guilds"),
                      [(m, u) for m, u in fake.calls])

    def test_missing_scope_falls_back_to_the_guild_list(self):
        """An older consent screen has no guilds.members.read. The fallback must
        still answer correctly — both ways."""
        self.account("alice")
        self.discord(member_status=403, guilds=[{"id": "999"}, {"id": ENV["DISCORD_GUILD_ID"]}])
        self.assertTrue(ds.claim_for_code("alice", "code-1")["ok"])

        self.account("bob")
        self.discord(user_id="900002", member_status=403, guilds=[{"id": "999"}])
        self.assertEqual(ds.claim_for_code("bob", "code-2")["error"], "not_a_member")
        self.assertEqual(self.db.coins("bob"), 0)

    def test_a_garbled_guild_list_is_not_a_yes(self):
        self.account("alice")
        self.discord(member_status=403, guilds=[])
        self.assertEqual(ds.claim_for_code("alice", "code-1")["error"], "not_a_member")
        self.assertEqual(self.db.coins("alice"), 0)

    def test_a_rejected_code_never_reaches_the_account(self):
        self.account("alice")
        fake = self.discord(token_status=400)
        out = ds.claim_for_code("alice", "bad-code")
        self.assertEqual(out["error"], "discord_rejected")
        self.assertEqual(self.db.coins("alice"), 0)
        # We stopped at the token exchange — no identity, no membership call.
        self.assertEqual([u for _m, u in fake.calls],
                         ["https://discord.com/api/v10/oauth2/token"])

    def test_an_unidentifiable_user_is_refused(self):
        self.account("alice")
        self.discord(me_status=401)
        self.assertEqual(ds.claim_for_code("alice", "code-1")["error"], "discord_rejected")
        self.assertEqual(self.db.ledger_ids(), [])

    def test_claiming_for_an_account_that_does_not_exist(self):
        self.discord(member=True)
        out = ds.claim_for_code("ghost", "code-1")
        self.assertEqual(out["error"], "no_account")
        self.assertEqual(self.db.ledger_ids(), [])

    def test_unconfigured_server_refuses_every_claim(self):
        os.environ.pop("DISCORD_GUILD_ID")
        self.account("alice")
        self.discord(member=True)
        out = ds.claim_for_code("alice", "code-1")
        self.assertEqual(out["error"], "not_configured")
        self.assertEqual(self.db.coins("alice"), 0)


# ══════════════════════════════════════════════════════════════════════════
#  4. THE SIGNED STATE  (the only proof of "whose account is this?")
# ══════════════════════════════════════════════════════════════════════════
class TestState(Base):
    def test_round_trip(self):
        uid, err = ds.read_state(ds.make_state("alice"))
        self.assertIsNone(err)
        self.assertEqual(uid, "alice")

    def test_a_forged_state_is_rejected(self):
        import base64
        payload = base64.urlsafe_b64encode(
            json.dumps({"u": "victim", "t": 9_999_999_999, "n": "x"}).encode()
        ).decode().rstrip("=")
        for forged in (f"v1.{payload}.notasignature", f"v1.{payload}.", payload,
                       "v2.a.b", "", "..", "v1..", "garbage"):
            uid, err = ds.read_state(forged)
            self.assertIsNone(uid, f"{forged!r} must not authenticate anyone")
            self.assertTrue(err)

    def test_a_tampered_uid_breaks_the_signature(self):
        import base64
        good = ds.make_state("alice")
        _v, body, sig = good.split(".")
        raw = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        raw["u"] = "victim"
        swapped = base64.urlsafe_b64encode(
            json.dumps(raw, separators=(",", ":"), sort_keys=True).encode()
        ).decode().rstrip("=")
        uid, err = ds.read_state(f"v1.{swapped}.{sig}")
        self.assertIsNone(uid)
        self.assertEqual(err, "bad_state")

    def test_a_state_signed_with_another_secret_is_rejected(self):
        os.environ["DISCORD_STATE_SECRET"] = "secret-a"
        state = ds.make_state("alice")
        os.environ["DISCORD_STATE_SECRET"] = "secret-b"
        self.assertEqual(ds.read_state(state)[1], "bad_state")

    def test_an_expired_state_is_rejected(self):
        real = ds.time.time
        try:
            state = ds.make_state("alice")
            ds.time.time = lambda: real() + ds.STATE_TTL_SECONDS + 5  # type: ignore
            uid, err = ds.read_state(state)
        finally:
            ds.time.time = real  # type: ignore
        self.assertIsNone(uid)
        self.assertEqual(err, "state_expired")

    def test_a_state_is_single_use(self):
        state = ds.make_state("alice")
        self.assertEqual(ds.read_state(state)[0], "alice")
        self.assertEqual(ds.read_state(state)[1], "state_used")

    def test_the_authorize_url_carries_what_discord_needs(self):
        from urllib.parse import parse_qs, urlparse
        url = urlparse(ds.authorize_url("alice"))
        q = parse_qs(url.query)
        self.assertEqual(url.netloc, "discord.com")
        self.assertEqual(q["client_id"], ["clientid"])
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["redirect_uri"], [ENV["DISCORD_REDIRECT_URI"]])
        self.assertIn("identify", q["scope"][0])
        self.assertIn("guilds.members.read", q["scope"][0])
        self.assertEqual(ds.read_state(q["state"][0])[0], "alice")
        # The secret is never in a URL the browser sees.
        self.assertNotIn("clientsecret", ds.authorize_url("alice"))


# ══════════════════════════════════════════════════════════════════════════
#  5. THE HTTP SURFACE
# ══════════════════════════════════════════════════════════════════════════
class FakeHandler:
    def __init__(self):
        self.json = None
        self.status = 200
        self.html = None

    def _send_json(self, payload, status=200):
        self.json = payload
        self.status = int(status)

    def _emit_html(self, raw):
        self.html = raw.decode("utf-8")


class Parsed:
    def __init__(self, path, query=""):
        self.path = path
        self.query = query


class TestHttp(Base):
    def test_state_endpoint_is_readable_signed_out(self):
        h = FakeHandler()
        self.assertTrue(ds.handle_post(h, Parsed("/api/discord/state"), {}))
        self.assertTrue(h.json["ok"])
        self.assertTrue(h.json["enabled"])
        self.assertEqual(h.json["coins"], 250)
        self.assertFalse(h.json["signedIn"])
        self.assertFalse(h.json["claimed"])

    def test_state_endpoint_reports_a_claim(self):
        self.account("alice")
        self.discord()
        ds.claim_for_code("alice", "code-1")
        h = FakeHandler()
        ds.handle_post(h, Parsed("/api/discord/state"), {"idToken": "tokenalice"})
        self.assertTrue(h.json["claimed"])
        self.assertTrue(h.json["signedIn"])

    def test_state_endpoint_says_off_when_unconfigured(self):
        os.environ.pop("DISCORD_CLIENT_SECRET")
        h = FakeHandler()
        ds.handle_post(h, Parsed("/api/discord/state"), {})
        self.assertFalse(h.json["enabled"])

    def test_start_requires_a_verified_token(self):
        h = FakeHandler()
        ds.handle_post(h, Parsed("/api/discord/start"), {"idToken": "forged"})
        self.assertEqual(h.status, 401)
        self.assertEqual(h.json["error"], "unauthorized")

    def test_start_hands_back_a_discord_url(self):
        self.account("alice")
        h = FakeHandler()
        ds.handle_post(h, Parsed("/api/discord/start"), {"idToken": "tokenalice"})
        self.assertTrue(h.json["ok"])
        self.assertTrue(h.json["url"].startswith("https://discord.com/oauth2/authorize?"))

    def test_start_refuses_an_account_already_paid(self):
        self.account("alice")
        self.discord()
        ds.claim_for_code("alice", "code-1")
        h = FakeHandler()
        ds.handle_post(h, Parsed("/api/discord/start"), {"idToken": "tokenalice"})
        self.assertFalse(h.json["ok"])
        self.assertEqual(h.json["error"], "already_claimed")

    def test_there_is_no_endpoint_that_grants_coins_directly(self):
        """The client can ask to START a claim and can READ its state. It can
        never ask to be paid — only Discord's own callback can do that."""
        for action in ("claim", "grant", "award", "confirm", "verify"):
            h = FakeHandler()
            ds.handle_post(h, Parsed(f"/api/discord/{action}"), {"idToken": "tokenalice"})
            self.assertEqual(h.status, 404, action)
            self.assertEqual(self.db.ledger_ids(), [])

    def _callback_result(self, html):
        """The JSON the callback page hands back to the game window."""
        raw = html.split("var RESULT = ", 1)[1].split(";\n", 1)[0]
        return json.loads(raw)

    def test_callback_pays_and_reports(self):
        self.account("alice", coins=5)
        self.discord()
        state = ds.make_state("alice")
        h = FakeHandler()
        self.assertTrue(ds.handle_get(h, Parsed("/api/discord/callback",
                                                f"code=abc&state={state}")))
        self.assertEqual(self.db.coins("alice"), 255)
        result = self._callback_result(h.html)
        self.assertTrue(result["ok"])
        self.assertEqual(result["coins"], 250)
        self.assertEqual(result["total"], 255)
        self.assertEqual(result["source"], "cc-discord")
        # The page must post its answer back to OUR origin and nowhere else.
        self.assertIn("window.location.origin", h.html)
        self.assertNotIn('postMessage(RESULT, "*")', h.html)

    def test_callback_with_a_forged_state_touches_nothing(self):
        self.account("alice")
        fake = self.discord()
        h = FakeHandler()
        ds.handle_get(h, Parsed("/api/discord/callback", "code=abc&state=v1.aaa.bbb"))
        self.assertEqual(self.db.coins("alice"), 0)
        self.assertEqual(self.db.ledger_ids(), [])
        # The forged state was rejected before Discord was ever contacted.
        self.assertEqual(fake.calls, [])

    def test_callback_when_the_player_cancels(self):
        self.account("alice")
        self.discord()
        h = FakeHandler()
        ds.handle_get(h, Parsed("/api/discord/callback",
                                f"error=access_denied&state={ds.make_state('alice')}"))
        self.assertIn("cancelled", h.html)
        self.assertEqual(self.db.coins("alice"), 0)

    def test_callback_page_never_leaks_the_secret(self):
        self.account("alice")
        self.discord()
        h = FakeHandler()
        ds.handle_get(h, Parsed("/api/discord/callback",
                                f"code=abc&state={ds.make_state('alice')}"))
        self.assertNotIn("clientsecret", h.html)
        self.assertNotIn("access_token", h.html)
        self.assertNotIn("tok", h.html.replace("token", ""))

    def test_every_error_code_has_a_sentence(self):
        """A player must never see a raw error code."""
        codes = {"not_a_member", "already_claimed", "discord_already_used",
                 "discord_denied", "discord_unreachable", "discord_rejected",
                 "state_expired", "state_used", "bad_state", "not_configured",
                 "no_account", "firestore_unavailable", "bad_request", "server_error"}
        for code in codes:
            msg = ds.message_for({"ok": False, "error": code})
            self.assertTrue(msg and msg[0].isupper() and msg.endswith((".", "!")), code)
            self.assertNotIn(code, msg)
        self.assertEqual(codes, set(ds.ERROR_MESSAGES))


# ══════════════════════════════════════════════════════════════════════════
#  6. THE TWO HALVES THAT MUST NOT DRIFT
# ══════════════════════════════════════════════════════════════════════════
class TestWiring(Base):
    def test_the_server_wires_the_module_in(self):
        src = read("multiplayer_server.py")
        self.assertIn("import discord_server", src)
        self.assertIn("discord_server.init(", src)
        self.assertIn("discord_server.handle_get(self, parsed)", src)
        self.assertIn("discord_server.handle_post(self, parsed, body)", src)

    def test_the_client_module_ships_and_is_loaded(self):
        html = read("multiplayer", "client", "preview.html")
        self.assertIn("/js/discord-reward.js?v=", html)
        self.assertIn('id="ph-discord-reward"', html)
        js = read("multiplayer", "client", "js", "discord-reward.js")
        self.assertIn("/api/discord/start", js)
        self.assertIn("/api/discord/state", js)
        # The client must not think it can pay itself.
        self.assertNotIn("critter_coins", js)

    def test_client_error_messages_cover_the_server_codes(self):
        js = read("multiplayer", "client", "js", "discord-reward.js")
        block = js.split("const MESSAGES = {", 1)[1].split("};", 1)[0]
        for code in ds.ERROR_MESSAGES:
            self.assertIn(f"{code}:", block,
                          f"js/discord-reward.js has no sentence for {code}")

    def test_the_advertised_amount_matches_the_default(self):
        """The chip's fallback text and the server's default must agree, so a
        first paint before the server answers never advertises a wrong number."""
        html = read("multiplayer", "client", "preview.html")
        chunk = html.split('id="ph-discord-reward"', 1)[1].split("</button>", 1)[0]
        self.assertIn(f"+{ds.DEFAULT_REWARD_COINS} Critter Coins", chunk)

    def test_the_client_never_holds_a_secret(self):
        js = read("multiplayer", "client", "js", "discord-reward.js")
        for forbidden in ("client_secret", "DISCORD_CLIENT_SECRET", "oauth2/token"):
            self.assertNotIn(forbidden, js)

    def test_the_module_is_allowlisted_for_deploy(self):
        """.gitignore, .dockerignore and the Dockerfile are three separate
        allowlists. multiplayer_server imports discord_server at module scope,
        so missing from any one of them is a server that will not boot."""
        for name in (".gitignore", ".dockerignore"):
            src = read(name)
            self.assertIn("!discord_server.py", src, name)
        docker = read("Dockerfile")
        self.assertIn("COPY discord_server.py", docker)


if __name__ == "__main__":
    unittest.main(verbosity=2)
