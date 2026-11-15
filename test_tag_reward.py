"""Tests for the plush-tag QR reward (tag_reward.py).

The rules the QR on the plush hang tag promises, in order of how much damage
breaking one would do:

 1. ONCE PER ACCOUNT, EVER. A second claim, a racing claim, and a claim after
    the skin was traded away all get nothing.
 2. ONLY THE QR GIVES IT. No token, a wrong token, or a near-miss token grant
    nothing and never touch an account.
 3. ANY SIGNED-IN ACCOUNT, NEVER A GUEST. No verified Firebase token, no skin,
    and the uid always comes from the token, never from the request body.
 4. IT STAYS. The skin is a "code" unlock, which Prestige keeps forever, and it
    lands in unlocked_icons, where the ordinary trade system can move it.
 5. An account that already owns it does not spend its claim.

Run:  python3 test_tag_reward.py
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tag_reward as tr  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEST_TOKEN = "TestTagToken_0123456789"
FISH = "/avatars/fish.png"


def read(*parts) -> str:
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


# ══════════════════════════════════════════════════════════════════════════
#  A tiny in-memory Firestore (same shape as test_discord_rewards.py's)
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
        if self.id in self._coll._docs:
            raise AlreadyExists(self.id)
        self._coll._docs[self.id] = copy.deepcopy(data)


class FakeColl:
    def __init__(self, name):
        self.name = name
        self._docs = {}

    def document(self, doc_id):
        return FakeDoc(self, doc_id)


class FakeTxn:
    """Buffers writes and applies them at commit, all or nothing, so a losing
    ledger create() also throws away the avatar write beside it."""
    def __init__(self, db):
        self._db = db
        self._ops = []

    def set(self, ref, data, merge=False):
        self._ops.append(("set", ref, copy.deepcopy(data), merge))

    def create(self, ref, data):
        self._ops.append(("create", ref, copy.deepcopy(data), False))

    def commit(self):
        with self._db.lock:
            for kind, ref, _d, _m in self._ops:
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
            self._colls[name] = FakeColl(name)
        return self._colls[name]

    def transaction(self):
        return FakeTxn(self)

    def icons(self, uid):
        doc = self.collection("users")._docs.get(uid) or {}
        return list(doc.get("unlocked_icons") or [])

    def ledger(self):
        return sorted(self.collection("tag_rewards")._docs)


def _fake_transactional(fn):
    def _wrapped(txn, *args, **kwargs):
        result = fn(txn, *args, **kwargs)
        txn.commit()
        return result
    return _wrapped


class FakeHandler:
    def __init__(self):
        self.sent = []

    def _send_json(self, payload, status=200):
        self.sent.append((status, payload))


class Parsed:
    def __init__(self, path):
        self.path = path
        self.query = ""


# ══════════════════════════════════════════════════════════════════════════
class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        self._real_hashes = tr.ACCEPTED_TOKEN_SHA256
        self._real_txn = tr._txn_helpers
        tr.ACCEPTED_TOKEN_SHA256 = (hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),)
        tr.init(get_firestore=lambda: self.db,
                verify_token=lambda tok: ({"uid": tok[5:]} if str(tok).startswith("token")
                                          else None))
        tr._txn_helpers = lambda: _fake_transactional  # type: ignore[assignment]

    def tearDown(self):
        tr.ACCEPTED_TOKEN_SHA256 = self._real_hashes
        tr._txn_helpers = self._real_txn  # type: ignore[assignment]

    def user(self, uid, icons=(), **extra):
        doc = {"nickname": uid.title(), "unlocked_icons": list(icons),
               "stats": {"critter_coins": 500}}
        doc.update(extra)
        self.db.collection("users")._docs[uid] = doc
        return doc

    def post(self, body, path="/api/tag-reward/claim"):
        h = FakeHandler()
        handled = tr.handle_post(h, Parsed(path), body)
        return handled, (h.sent[-1] if h.sent else None)


class OncePerAccount(Base):
    def test_first_claim_grants_the_fish(self):
        self.user("alice", ["/avatars/mullet.png"])
        res = tr.claim("alice", TEST_TOKEN)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["icon"], FISH)
        self.assertEqual(self.db.icons("alice"), ["/avatars/mullet.png", FISH])
        self.assertEqual(self.db.ledger(), ["u_alice"])

    def test_second_claim_is_refused(self):
        self.user("alice")
        self.assertTrue(tr.claim("alice", TEST_TOKEN)["ok"])
        res = tr.claim("alice", TEST_TOKEN)
        self.assertEqual(res["error"], "already_claimed")
        self.assertEqual(self.db.icons("alice").count(FISH), 1)

    def test_trading_it_away_does_not_earn_another(self):
        self.user("alice")
        self.assertTrue(tr.claim("alice", TEST_TOKEN)["ok"])
        # The trade system takes it off the account…
        self.db.collection("users")._docs["alice"]["unlocked_icons"] = []
        # …and scanning again gives nothing back.
        res = tr.claim("alice", TEST_TOKEN)
        self.assertEqual(res["error"], "already_claimed")
        self.assertEqual(self.db.icons("alice"), [])

    def test_racing_claims_grant_once(self):
        self.user("alice")
        results, barrier = [], threading.Barrier(6)

        def go():
            barrier.wait()
            results.append(tr.claim("alice", TEST_TOKEN))

        threads = [threading.Thread(target=go) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for r in results if r.get("ok")), 1, results)
        self.assertTrue(all(r.get("ok") or r.get("error") == "already_claimed" for r in results))
        self.assertEqual(self.db.icons("alice").count(FISH), 1)
        self.assertEqual(self.db.ledger(), ["u_alice"])

    def test_every_account_gets_its_own_claim(self):
        self.user("alice")
        self.user("bob")
        self.assertTrue(tr.claim("alice", TEST_TOKEN)["ok"])
        self.assertTrue(tr.claim("bob", TEST_TOKEN)["ok"])
        self.assertEqual(self.db.ledger(), ["u_alice", "u_bob"])

    def test_nothing_else_on_the_account_moves(self):
        doc = self.user("alice", ["/avatars/clownfish.png"], avatar_url="/avatars/clownfish.png")
        before = copy.deepcopy(doc)
        tr.claim("alice", TEST_TOKEN)
        after = self.db.collection("users")._docs["alice"]
        self.assertEqual(after["stats"], before["stats"])
        self.assertEqual(after["avatar_url"], before["avatar_url"])
        self.assertEqual(after["nickname"], before["nickname"])


class AlreadyOwned(Base):
    def test_owner_is_told_and_claim_is_not_spent(self):
        self.user("alice", [FISH])
        res = tr.claim("alice", TEST_TOKEN)
        self.assertEqual(res["error"], "already_owned")
        self.assertEqual(self.db.ledger(), [])
        self.assertEqual(self.db.icons("alice"), [FISH])

    def test_owner_who_later_trades_it_away_can_still_claim_once(self):
        self.user("alice", [FISH + "?v=3"])
        self.assertEqual(tr.claim("alice", TEST_TOKEN)["error"], "already_owned")
        self.db.collection("users")._docs["alice"]["unlocked_icons"] = []
        self.assertTrue(tr.claim("alice", TEST_TOKEN)["ok"])
        self.assertEqual(tr.claim("alice", TEST_TOKEN)["error"], "already_claimed")


class OnlyTheQr(Base):
    def test_wrong_tokens_grant_nothing(self):
        self.user("alice")
        for bad in ["", None, 123, "nope", TEST_TOKEN + "x", TEST_TOKEN[:-1],
                    TEST_TOKEN.lower(), " " * 30, "a" * 200, "../../etc/passwd"]:
            self.assertEqual(tr.claim("alice", bad)["error"], "bad_token", bad)
        self.assertEqual(self.db.icons("alice"), [])
        self.assertEqual(self.db.ledger(), [])

    def test_surrounding_whitespace_is_tolerated(self):
        self.assertTrue(tr.token_ok("  " + TEST_TOKEN + "\n"))

    def test_shipped_token_hash_is_a_sha256_and_not_the_token(self):
        for h in self._real_hashes:
            self.assertRegex(h, r"^[0-9a-f]{64}$")
        src = read("tag_reward.py")
        # The plaintext never lives in this public repo, only its hash.
        self.assertNotIn("?tag=", re.sub(r"\?tag=<TOKEN>", "", src))


class Accounts(Base):
    def test_signed_out_is_refused_and_untouched(self):
        self.user("alice")
        self.assertEqual(tr.claim(None, TEST_TOKEN)["error"], "unauthorized")
        handled, (status, payload) = self.post({"token": TEST_TOKEN})
        self.assertTrue(handled)
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")
        handled, (status, payload) = self.post({"token": TEST_TOKEN, "idToken": "forged"})
        self.assertEqual(payload["error"], "unauthorized")
        self.assertEqual(self.db.ledger(), [])

    def test_uid_comes_from_the_token_not_the_body(self):
        self.user("alice")
        self.user("victim")
        handled, (status, payload) = self.post(
            {"idToken": "tokenalice", "uid": "victim", "token": TEST_TOKEN})
        self.assertTrue(payload["ok"], payload)
        self.assertIn(FISH, self.db.icons("alice"))
        self.assertNotIn(FISH, self.db.icons("victim"))

    def test_no_profile_yet_is_retryable_and_spends_nothing(self):
        res = tr.claim("newbie", TEST_TOKEN)
        self.assertEqual(res["error"], "no_account")
        self.assertEqual(self.db.ledger(), [])
        self.user("newbie")
        self.assertTrue(tr.claim("newbie", TEST_TOKEN)["ok"])

    def test_firestore_down(self):
        tr.init(get_firestore=lambda: None, verify_token=lambda t: {"uid": "alice"})
        self.assertEqual(tr.claim("alice", TEST_TOKEN)["error"], "firestore_unavailable")

    def test_bad_token_checked_before_account(self):
        calls = []
        tr.init(get_firestore=lambda: self.db,
                verify_token=lambda tok: calls.append(tok) or {"uid": "alice"})
        self.user("alice")
        handled, (status, payload) = self.post({"idToken": "tokenalice", "token": "wrongwrongwrong"})
        self.assertEqual(payload["error"], "bad_token")
        self.assertEqual(calls, [])

    def test_other_paths_are_not_ours(self):
        handled, _ = self.post({"token": TEST_TOKEN}, path="/api/discord/state")
        self.assertFalse(handled)

    def test_every_reply_has_a_sentence(self):
        self.user("alice")
        for body in ({"idToken": "tokenalice", "token": TEST_TOKEN},
                     {"idToken": "tokenalice", "token": TEST_TOKEN},
                     {"token": "nope-nope-nope"}):
            _, (_s, payload) = self.post(body)
            self.assertTrue(payload.get("message"), payload)


class ItStays(Base):
    def test_fish_is_a_keep_forever_code_unlock(self):
        import prestige_server as ps
        self.assertEqual(ps.AVATAR_UNLOCK_TYPES.get(FISH), "code")
        self.assertIn("code", ps.KEEP_FOREVER_UNLOCK_TYPES)

    def test_fish_is_tradable_once_granted(self):
        # The trade core reads ownership straight from unlocked_icons.
        self.user("alice")
        tr.claim("alice", TEST_TOKEN)
        self.assertIn(FISH, self.db.icons("alice"))


class Wiring(unittest.TestCase):
    def test_server_wires_the_module(self):
        src = read("multiplayer_server.py")
        self.assertIn("import tag_reward", src)
        self.assertIn("tag_reward.init(", src)
        self.assertIn("tag_reward.handle_post(self, parsed, body)", src)

    def test_image_ships_the_module(self):
        self.assertIn("COPY tag_reward.py /app/tag_reward.py", read("Dockerfile"))
        self.assertIn("!tag_reward.py", read(".dockerignore"))

    def test_client_loads_the_module(self):
        html = read("multiplayer", "client", "preview.html")
        self.assertRegex(html, r'src="/js/tag-reward\.js\?v=')
        app = read("multiplayer", "client", "js", "preview-app.js")
        self.assertIn("window.__ccTagReward", app)
        self.assertIn("window.__fishPopNewlyGrantedIcons", app)


if __name__ == "__main__":
    unittest.main(verbosity=2)
