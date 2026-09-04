"""End-to-end integration test of the SERVER trade lifecycle against an
in-memory Firestore fake. Exercises the real _trade_open / _trade_set_offer /
_trade_confirm / _trade_cancel functions (transactions, mirroring, the atomic
swap), simulating two players acting in turn.

Run:  python3 test_trade_integration.py
"""
import copy
import importlib.util
import os
import sys
import types

# ── Fake `firebase_admin` (+ .firestore) injected BEFORE importing the server ──
fake_fs = types.ModuleType("firebase_admin.firestore")
fake_fs.SERVER_TIMESTAMP = "__SERVER_TS__"
fake_fs.transactional = lambda f: f          # passthrough (single-threaded test)
fake_admin = types.ModuleType("firebase_admin")
fake_admin.firestore = fake_fs
fake_admin._apps = {"default": object()}
fake_admin.credentials = types.SimpleNamespace(Certificate=lambda *a, **k: None)
fake_admin.initialize_app = lambda *a, **k: None
fake_auth = types.ModuleType("firebase_admin.auth")
fake_auth.verify_id_token = lambda tok: {"uid": tok}   # token IS the uid, for tests
fake_admin.auth = fake_auth
sys.modules["firebase_admin"] = fake_admin
sys.modules["firebase_admin.firestore"] = fake_fs
sys.modules["firebase_admin.auth"] = fake_auth


def _deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)
    return dst


class FakeSnap:
    def __init__(self, data, doc_id=""):
        self._data = data
        self.id = doc_id
    @property
    def exists(self):
        return self._data is not None
    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class FakeQuery:
    """Just enough of a Firestore query for the admin lookups: equality
    filters over the DIRECT children of one collection."""
    def __init__(self, store, path, filters=(), cap=None):
        self._store, self._path = store, path
        self._filters, self._cap = list(filters), cap
    def where(self, field, op, value):
        assert op == "==", f"fake only supports == (got {op})"
        return FakeQuery(self._store, self._path, self._filters + [(field, value)], self._cap)
    def limit(self, n):
        return FakeQuery(self._store, self._path, self._filters, n)
    def stream(self):
        prefix, out = self._path + "/", []
        for key, data in self._store.items():
            if not key.startswith(prefix) or "/" in key[len(prefix):]:
                continue
            if not isinstance(data, dict):
                continue
            if all(data.get(f) == v for f, v in self._filters):
                out.append(FakeSnap(data, key[len(prefix):]))
                if self._cap is not None and len(out) >= self._cap:
                    break
        return iter(out)


class FakeDoc:
    def __init__(self, store, path):
        self._store = store
        self._path = path
    def get(self, transaction=None):
        return FakeSnap(self._store.get(self._path), self._path.rsplit("/", 1)[-1])
    def set(self, data, merge=False):
        if merge and self._path in self._store and isinstance(self._store[self._path], dict):
            _deep_merge(self._store[self._path], data)
        else:
            self._store[self._path] = copy.deepcopy(data)
    def collection(self, name):
        return FakeCollection(self._store, self._path + "/" + name)


class FakeCollection:
    def __init__(self, store, path):
        self._store = store
        self._path = path
    def document(self, doc_id):
        return FakeDoc(self._store, self._path + "/" + str(doc_id))
    def where(self, field, op, value):
        return FakeQuery(self._store, self._path).where(field, op, value)


class FakeTxn:
    def __init__(self, store):
        self._store = store
    def set(self, ref, data, merge=False):
        ref.set(data, merge=merge)
    def delete(self, ref):
        self._store.pop(ref._path, None)


class FakeDB:
    def __init__(self):
        self.store = {}
    def collection(self, name):
        return FakeCollection(self.store, name)
    def transaction(self):
        return FakeTxn(self.store)


# ── Load the server module ────────────────────────────────────────────────
_spec = importlib.util.spec_from_file_location(
    "mpsrv2", os.path.join(os.path.dirname(__file__), "multiplayer_server.py"))
M = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = M
_spec.loader.exec_module(M)

DB = FakeDB()
M._get_firestore = lambda: DB     # every _trade_* fn reads through this

_PASS = 0
_FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✓ {name}")
    else:
        _FAIL += 1
        print(f"  ✗ FAIL: {name}")


def set_user(uid, avatars=(), backgrounds=(), coins=0, avatar_url="", background_url="",
             other_stats=None, passes=0):
    stats = {"critter_coins": coins}
    if other_stats:
        stats.update(other_stats)
    DB.store["users/" + uid] = {
        "nickname": uid.title(),
        "unlocked_icons": list(avatars),
        "unlocked_backgrounds": list(backgrounds),
        "stats": stats,
        "critter_pass_vouchers": passes,
        "avatar_url": avatar_url,
        "background_url": background_url,
    }


def passes_of(uid):
    return int((user(uid) or {}).get("critter_pass_vouchers") or 0)


def user(uid):
    return DB.store.get("users/" + uid) or {}


def mirror(uid, tid):
    return DB.store.get(f"users/{uid}/messages/trade_{tid}")


# ══ Scenario from the spec ════════════════════════════════════════════════
# P1 (alice) offers 1,000 Critter Coins + Sardine avatar.
# P2 (bob)   offers Lobster avatar.
A, B = "alice", "bob"
set_user(A, avatars=["/avatars/sardine.png"], coins=1500,
         avatar_url="/avatars/sardine.png",
         other_stats={"games_played": 42, "lifetime_play_again": 91,
                      "total_xp": M.LEVEL_XP_TOTALS[30]})
set_user(B, avatars=["/avatars/lobster.png"], coins=0)
tid = M._trade_id_for(A, B)

print("open:")
r = M._trade_open(A, "Alice", B, "Bob")
check("open ok", r.get("ok") and r["state"]["status"] == "open")
check("mirror written to both", mirror(A, tid) is not None and mirror(B, tid) is not None)
check("started-trade log posted", any(k.startswith(f"users/{B}/messages/tradelog_") for k in DB.store))

print("offers:")
r = M._trade_set_offer(A, "Alice", B, {"coins": 1000, "avatars": ["/avatars/sardine.png"]})
check("alice offer ok", r.get("ok"))
check("version bumped", r["state"]["version"] == 2)
r = M._trade_set_offer(B, "Bob", A, {"avatars": ["/avatars/lobster.png"]})
check("bob offer ok", r.get("ok"))
ver = r["state"]["version"]
check("both offers present in mirror",
      mirror(A, tid)["trade_state"]["offers"][A]["coins"] == 1000
      and mirror(A, tid)["trade_state"]["offers"][B]["avatars"] == ["/avatars/lobster.png"])

print("reject offering an item you don't own:")
r = M._trade_set_offer(A, "Alice", B, {"avatars": ["/avatars/whale.png"]})
check("rejected (not owned)", not r.get("ok") and r.get("error") == "avatar_not_owned")

print("reject offering more coins than you have:")
r = M._trade_set_offer(B, "Bob", A, {"coins": 5})
check("rejected (broke)", not r.get("ok") and r.get("error") == "not_enough_coins")
# Re-set the valid offers (the rejected ones never applied).
M._trade_set_offer(A, "Alice", B, {"coins": 1000, "avatars": ["/avatars/sardine.png"]})
r = M._trade_set_offer(B, "Bob", A, {"avatars": ["/avatars/lobster.png"]})
ver = r["state"]["version"]

print("two-step confirm + reset-on-change:")
r = M._trade_confirm(A, B, ver, True)
check("alice confirm ok, not completed", r.get("ok") and not r.get("completed"))
check("alice marked confirmed", r["state"]["confirmed"][A] is True and r["state"]["confirmed"][B] is False)
# Bob changes his offer AFTER alice confirmed → both confirmations reset + version bump.
r = M._trade_set_offer(B, "Bob", A, {"avatars": []})
check("confirmations reset on change", r["state"]["confirmed"][A] is False and r["state"]["confirmed"][B] is False)
check("version bumped again", r["state"]["version"] > ver)
# Alice's stale confirm (old version) is rejected.
r = M._trade_confirm(A, B, ver, True)
check("stale confirm rejected", not r.get("ok") and r.get("error") == "changed")

print("complete the trade (bob re-offers lobster, both confirm current version):")
r = M._trade_set_offer(B, "Bob", A, {"avatars": ["/avatars/lobster.png"]})
ver = r["state"]["version"]
r = M._trade_confirm(A, B, ver, True)
check("alice re-confirm ok", r.get("ok") and not r.get("completed"))
r = M._trade_confirm(B, A, ver, True)      # note: bob calls with peer=alice
check("bob confirm completes it", r.get("ok") and r.get("completed"))
check("trade status completed", r["state"]["status"] == "completed")

print("ownership actually moved:")
check("alice lost sardine, gained lobster", user(A)["unlocked_icons"] == ["/avatars/lobster.png"])
check("alice coins 1500-1000=500", user(A)["stats"]["critter_coins"] == 500)
check("alice other stats preserved (deep merge)", user(A)["stats"]["games_played"] == 42)
check("alice equipped avatar reset (traded away sardine)", user(A)["avatar_url"] == "/avatars/mullet.png")
check("bob gained sardine, lost lobster", user(B)["unlocked_icons"] == ["/avatars/sardine.png"])
check("bob coins 0+1000=1000", user(B)["stats"]["critter_coins"] == 1000)

# The unlock requirement has to be met AGAIN before an automatic grant can hand
# a traded-away item back, so the giver's progress is snapshotted on the doc.
_alice_away = {e.get("item"): e for e in (user(A).get("traded_away") or [])}
_bob_away = {e.get("item"): e for e in (user(B).get("traded_away") or [])}
check("alice's given sardine is recorded as traded away",
      "/avatars/sardine.png" in _alice_away)
# Level comes from the curve's own level-31 boundary (see alice's setup), so a
# LEVEL_XP_TOTALS retune can never quietly change what this asserts.
check("snapshot carries her progress at trade time",
      _alice_away.get("/avatars/sardine.png", {}).get("stats", {}).get("lifetime_play_again") == 91
      and _alice_away["/avatars/sardine.png"]["level"] == 31)
check("nothing recorded for the lobster she received",
      "/avatars/lobster.png" not in _alice_away)
check("bob's given lobster is recorded, his received sardine is not",
      "/avatars/lobster.png" in _bob_away and "/avatars/sardine.png" not in _bob_away)
check("completed log posted to both",
      sum(1 for k in DB.store if "/messages/tradelog_" in k and k.startswith(f"users/{A}/")) >= 1)

print("no double-spend: confirming an already-completed trade is a safe no-op:")
before = copy.deepcopy(user(A))
r = M._trade_confirm(A, B, ver, True)
check("already-completed rejected", not r.get("ok") and r.get("error") == "already_completed")
check("balances unchanged after repeat", user(A) == before)

print("cannot receive a duplicate (recipient already owns it):")
set_user("carl", avatars=["/avatars/eel.png"], coins=0)
set_user("dana", avatars=["/avatars/eel.png"], coins=0)   # dana already owns eel
M._trade_open("carl", "Carl", "dana", "Dana")
r = M._trade_set_offer("carl", "Carl", "dana", {"avatars": ["/avatars/eel.png"]})
check("offer of an item the recipient owns is rejected",
      not r.get("ok") and r.get("error") == "duplicate_avatar")

print("commit-time ownership loss aborts the whole swap:")
set_user("emma", avatars=["/avatars/ray.png"], coins=0)
set_user("finn", coins=500)
tid2 = M._trade_id_for("emma", "finn")
M._trade_open("emma", "Emma", "finn", "Finn")
ov = M._trade_set_offer("emma", "Emma", "finn", {"avatars": ["/avatars/ray.png"]})["state"]["version"]
ov = M._trade_set_offer("finn", "Finn", "emma", {"coins": 300})["state"]["version"]
M._trade_confirm("emma", "finn", ov, True)
# Emma loses the ray out-of-band (e.g. traded elsewhere) BEFORE finn confirms.
DB.store["users/emma"]["unlocked_icons"] = []
r = M._trade_confirm("finn", "emma", ov, True)
check("commit aborts (emma no longer owns ray)",
      not r.get("ok") and r.get("error") == "avatar_not_owned")
check("finn keeps his coins (nothing moved)", user("finn")["stats"]["critter_coins"] == 500)
check("trade reset, still open (not completed)",
      DB.store[f"trades/{tid2}"]["status"] == "open"
      and DB.store[f"trades/{tid2}"]["confirmed"]["emma"] is False)

print("cancel:")
set_user("gwen", avatars=["/avatars/crab.png"], coins=0)
set_user("hugo", coins=0)
M._trade_open("gwen", "Gwen", "hugo", "Hugo")
M._trade_set_offer("gwen", "Gwen", "hugo", {"avatars": ["/avatars/crab.png"]})
r = M._trade_cancel("hugo", "gwen")
check("cancel ok", r.get("ok") and r["state"]["status"] == "canceled")
check("gwen still owns her crab (nothing moved)", user("gwen")["unlocked_icons"] == ["/avatars/crab.png"])
# A fresh open after cancel starts clean.
r = M._trade_open("gwen", "Gwen", "hugo", "Hugo")
check("re-open after cancel is fresh + empty",
      r["state"]["status"] == "open"
      and M._trade_clean_offer(r["state"]["offers"]["gwen"]) == {"coins": 0, "passes": 0, "avatars": [], "backgrounds": []})

# ══ Season Pass vouchers ═══════════════════════════════════════════════════
# The Supporter Tiers hand these out and they are tradable, so the whole
# lifecycle has to work end to end: offered, seen by both sides, and MOVED by
# the same one transaction that moves everything else.
print("trading a Season Pass voucher:")
set_user("ivy", coins=0, passes=3)
set_user("jonah", avatars=["/avatars/mackerel.png"], coins=0, passes=0)
tid_v = M._trade_id_for("ivy", "jonah")
check("open ok", M._trade_open("ivy", "Ivy", "jonah", "Jonah").get("ok"))
r = M._trade_set_offer("ivy", "Ivy", "jonah", {"passes": 2})
check("a voucher offer is accepted", r.get("ok"))
check("the offer carries the count",
      M._trade_clean_offer(r["state"]["offers"]["ivy"])["passes"] == 2)
r = M._trade_set_offer("jonah", "Jonah", "ivy", {"avatars": ["/avatars/mackerel.png"]})
check("the other side offers an avatar", r.get("ok"))
v = r["state"]["version"]
check("ivy confirms", M._trade_confirm("ivy", "jonah", v, True).get("ok"))
r = M._trade_confirm("jonah", "ivy", v, True)
check("both confirmed → completed", r.get("ok") and r.get("completed"))
check("ivy's vouchers dropped by 2", passes_of("ivy") == 1)
check("jonah's vouchers rose by 2", passes_of("jonah") == 2)
check("the avatar came back the other way",
      user("ivy")["unlocked_icons"] == ["/avatars/mackerel.png"])
check("the DM summary names the vouchers",
      any("Critter Pass voucher" in (d.get("text") or "")
          for k, d in DB.store.items()
          if k.startswith("users/ivy/messages/tradelog_") and isinstance(d, dict)))

print("offering more vouchers than you hold:")
set_user("kit", coins=0, passes=1)
set_user("lena", coins=0, passes=0)
M._trade_open("kit", "Kit", "lena", "Lena")
r = M._trade_set_offer("kit", "Kit", "lena", {"passes": 2})
check("refused at offer time", (not r.get("ok")) and r.get("error") == "not_enough_passes")
check("nothing moved", passes_of("kit") == 1 and passes_of("lena") == 0)

print("a voucher spent between the offer and the confirm:")
set_user("mo", coins=0, passes=1)
set_user("nia", coins=500, passes=0)
M._trade_open("mo", "Mo", "nia", "Nia")
M._trade_set_offer("mo", "Mo", "nia", {"passes": 1})
r = M._trade_set_offer("nia", "Nia", "mo", {"coins": 500})
v = r["state"]["version"]
M._trade_confirm("mo", "nia", v, True)
DB.store["users/mo"]["critter_pass_vouchers"] = 0     # redeemed it in another tab
r = M._trade_confirm("nia", "mo", v, True)
check("the swap is refused at commit", (not r.get("ok")) and r.get("error") == "not_enough_passes")
check("nia keeps her coins", int(user("nia")["stats"]["critter_coins"]) == 500)
check("confirmations were reset", r["state"]["confirmed"]["mo"] is False)

# ══ Opening a trade must not be able to fail silently ══════════════════════
# A transaction that dies used to reach the player as "Something went wrong
# with the trade", after which every later tap answered "Open a trade first".
# Opening moves nothing (both offers start empty), so it now falls back to a
# plain write, and only a SECOND failure is reported, with the reason attached.
print("opening a trade survives a broken transaction:")
set_user("orin", coins=0)
set_user("pia", coins=0)
_real_txn = DB.transaction
DB.transaction = lambda: (_ for _ in ()).throw(RuntimeError("BeginTransaction refused"))
r = M._trade_open("orin", "Orin", "pia", "Pia")
DB.transaction = _real_txn
check("the trade still opens", r.get("ok") and r["state"]["status"] == "open")
check("it is a real, empty, open trade",
      M._trade_clean_offer(r["state"]["offers"]["orin"]) == {"coins": 0, "passes": 0, "avatars": [], "backgrounds": []})
check("both sides were mirrored", mirror("orin", M._trade_id_for("orin", "pia")) is not None)

print("a trade that cannot be written at all says WHY:")


class _DeadColl:
    def document(self, doc_id):
        raise RuntimeError("PermissionDenied: trades is closed")


_real_coll = DB.collection
DB.collection = (lambda name: _DeadColl() if name == "trades" else _real_coll(name))
try:
    DB.transaction = lambda: (_ for _ in ()).throw(RuntimeError("BeginTransaction refused"))
    r = M._trade_open("orin", "Orin", "pia", "Pia")
finally:
    DB.collection = _real_coll
    DB.transaction = _real_txn
check("it fails honestly", (not r.get("ok")) and r.get("error") == "open_failed")
check("and carries the reason the player can report",
      "PermissionDenied" in str(r.get("detail") or "") or "RuntimeError" in str(r.get("detail") or ""))

print("a peer id Firestore would reject never reaches Firestore:")
set_user("quin", coins=0)
r = M._trade_open("quin", "Quin", "a/b", "Slashy")
check("refused as a bad peer", (not r.get("ok")) and r.get("error") == "bad_peer")
check("no trade document was created",
      not any(k.startswith("trades/") and "a/b" in k for k in DB.store))

print("admin revoke: take a cosmetic back and require the requirement again:")
PUFFIN = "/avatars/horned-puffin.png"
set_user("tim", avatars=[PUFFIN, "/avatars/bunker.png"], coins=10,
         avatar_url=PUFFIN, other_stats={"lifetime_play_again": 118, "total_xp": 60_450})
DB.store["users/tim"]["nickname"] = "TheFishManTim"
DB.store["users/tim"]["nickname_lower"] = "thefishmantim"
DB.store["users/tim"]["achievements"] = {"b_lob_master": {"completed": True, "progress": 13}}

r = M._admin_revoke_item("TheFishManTim", PUFFIN, dry_run=True)
check("dry run reports the change", r.get("ok") and r.get("dry_run") is True)
check("dry run resolves the nickname to the uid", r.get("uid") == "tim")
check("dry run changes NOTHING", PUFFIN in DB.store["users/tim"]["unlocked_icons"])

r = M._admin_revoke_item("TheFishManTim", PUFFIN)
check("revoke ok", r.get("ok"))
check("avatar removed", PUFFIN not in DB.store["users/tim"]["unlocked_icons"])
check("other avatars untouched", DB.store["users/tim"]["unlocked_icons"] == ["/avatars/bunker.png"])
check("equipped avatar fell back to the Mullet",
      DB.store["users/tim"]["avatar_url"] == "/avatars/mullet.png")
_tim_away = {e["item"]: e for e in DB.store["users/tim"]["traded_away"]}
check("re-earn baseline written", PUFFIN in _tim_away)
check("baseline is his count right now (needs another 75 on top of 118)",
      _tim_away[PUFFIN]["stats"]["lifetime_play_again"] == 118)
check("achievement meters are captured too",
      _tim_away[PUFFIN]["achievements"]["b_lob_master"] == 13)
check("stats/XP/achievements themselves are untouched",
      DB.store["users/tim"]["stats"]["lifetime_play_again"] == 118
      and DB.store["users/tim"]["stats"]["total_xp"] == 60_450
      and DB.store["users/tim"]["achievements"]["b_lob_master"]["completed"] is True)

r = M._admin_revoke_item("TheFishManTim", PUFFIN)
check("revoking twice is refused, not silently re-snapshotted",
      not r.get("ok") and "does not own" in r.get("error", ""))
check("unknown player is refused",
      not M._admin_revoke_item("nobody-here", PUFFIN).get("ok"))
check("a non-cosmetic path is refused",
      not M._admin_revoke_item("TheFishManTim", "stats.total_xp").get("ok"))
check("uid works as well as nickname",
      M._admin_revoke_item("tim", "/avatars/bunker.png", dry_run=True).get("ok"))

print(f"\n{_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
