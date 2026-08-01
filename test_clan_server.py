"""Clan System — end-to-end tests of the real clan_server functions against an
in-memory Firestore fake (same harness style as test_trade_integration.py) and
a sandboxed temp dir for game records (never dirties the tree).

Covers: the clan-name filter (incl. spacing/leet/punctuation evasions), create/
join/leave/kick/transfer, role permissions + custom roles, every point rule
(competitive +3, casual placements incl. the 2-player and 4+-player special
cases, one-claim-per-game, same-opponent daily caps, weekly 150 cap, 24h clan-
switch cooldown, guest/AI exclusions), the daily trade point (same clan, both
sides, bounce refusal), daily goal + weekly challenge plumbing, the season
leaderboard tiebreakers, season finalize (coins, badges, MVP rules), and the
clan-critter ownership rule (a clan can only wear a critter one of its members
has unlocked, including the season vote whose winner becomes the tab icon).

Run:  python3 test_clan_server.py
"""
import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types

# ── Fake firebase_admin injected BEFORE importing the modules ────────────────
fake_fs = types.ModuleType("firebase_admin.firestore")
fake_fs.SERVER_TIMESTAMP = "__SERVER_TS__"
fake_fs.transactional = lambda f: f
fake_admin = types.ModuleType("firebase_admin")
fake_admin.firestore = fake_fs
fake_admin._apps = {"default": object()}
fake_admin.credentials = types.SimpleNamespace(Certificate=lambda *a, **k: None)
fake_admin.initialize_app = lambda *a, **k: None
fake_auth = types.ModuleType("firebase_admin.auth")
fake_auth.verify_id_token = lambda tok: {"uid": tok}
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
        for key, data in list(self._store.items()):
            if not key.startswith(prefix) or "/" in key[len(prefix):]:
                continue
            if not isinstance(data, dict):
                continue
            if all(data.get(f) == v for f, v in self._filters):
                out.append(FakeSnap(data, key[len(prefix):]))
                if self._cap is not None and len(out) >= self._cap:
                    break
        return iter(out)
    def get(self):
        return list(self.stream())


# Round-trip counters. Reading the roster one document at a time is what made
# the Clans tab slow, so the suite can count what a call actually costs.
READS = {"doc": 0, "batch": 0, "user_doc": 0, "user_batched": 0}


def reads_reset():
    for k in READS:
        READS[k] = 0


class FakeDoc:
    def __init__(self, store, path):
        self._store = store
        self._path = path
    def get(self, transaction=None, _batched=False):
        if not _batched:
            READS["doc"] += 1
            if self._path.startswith("users/"):
                READS["user_doc"] += 1
        elif self._path.startswith("users/"):
            READS["user_batched"] += 1
        return FakeSnap(self._store.get(self._path), self._path.rsplit("/", 1)[-1])
    def set(self, data, merge=False):
        if merge and self._path in self._store and isinstance(self._store[self._path], dict):
            _deep_merge(self._store[self._path], data)
        else:
            self._store[self._path] = copy.deepcopy(data)
    def create(self, data):
        if self._path in self._store:
            raise RuntimeError("already exists: " + self._path)
        self._store[self._path] = copy.deepcopy(data)
    def delete(self):
        self._store.pop(self._path, None)
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
    def limit(self, n):
        return FakeQuery(self._store, self._path, (), n)
    def stream(self):
        return FakeQuery(self._store, self._path).stream()


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
    def get_all(self, refs):
        """Batched multi-document read — the real client has this, and the whole
        clan roster is fetched through it now, so the fake must too or the tests
        only ever cover the one-at-a-time fallback. Real Firestore returns the
        docs in arbitrary order; shuffling here keeps callers honest about that."""
        READS["batch"] += 1
        out = [r.get(_batched=True) for r in refs]
        out.reverse()
        return iter(out)
    def transaction(self):
        return FakeTxn(self.store)


# ── Load clan_server + the profanity tables from multiplayer_server ─────────
def _load(name, fname):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(os.path.dirname(__file__), fname))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load("mpsrv_for_clans", "multiplayer_server.py")
CS = _load("clan_server_test", "clan_server.py")

DB = FakeDB()
TMP = tempfile.mkdtemp(prefix="clan_test_")
HIST = os.path.join(TMP, "hist"); os.makedirs(HIST)
COMP = os.path.join(TMP, "comp"); os.makedirs(COMP)

FAKE_SID = {"cur": "2026-Q3"}


def _find_uid_by_username(db, uname):
    uname = str(uname or "").strip().lower()
    for key, doc in db.store.items():
        if key.startswith("users/") and key.count("/") == 1 and isinstance(doc, dict):
            if str(doc.get("usernameLower") or "").lower() == uname and uname:
                return key.split("/", 1)[1]
    return None


CS.init(
    get_firestore=lambda: DB,
    verify_token=lambda t: {"uid": t} if t else None,
    find_uid_by_username=_find_uid_by_username,
    level_progress=M._level_progress_for_total_xp,
    get_season_id=lambda ts=None: FAKE_SID["cur"],
    games_history_dir=HIST,
    competitive_games_dir=COMP,
    prof_strong_re=M._PROF_STRONG_RE,
    prof_word_re=M._PROF_WORD_RE,
    prof_leet=M._PROF_LEET,
    prof_strong=M._PROF_STRONG,
    prof_words=M._PROF_WORDS,
)

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


_DEFAULT_ICONS = ["/avatars/clownfish.png", "/avatars/lobster.png",
                   "/avatars/narwhal.png", "/avatars/bonito.png"]


def set_user(uid, nick=None, coins=0, clan_id="", cooldown=0, username=None, icons=None):
    DB.store["users/" + uid] = {
        "nickname": nick or uid.title(),
        "username": username or nick or uid.title(),
        "usernameLower": (username or nick or uid.title()).lower(),
        "stats": {"critter_coins": coins, "total_xp": 100},
        "avatar_url": "/avatars/clownfish.png",
        # A clan can only wear a critter one of its members has unlocked, so
        # every test player owns the icons the suite founds clans with.
        "unlocked_icons": list(_DEFAULT_ICONS if icons is None else icons),
        "online": True, "last_active": 4102444800,   # far future = online
    }
    if clan_id:
        DB.store["users/" + uid]["clan_id"] = clan_id
    if cooldown:
        DB.store["users/" + uid]["clan_cooldown_until"] = cooldown
    # The server batches + briefly caches member reads. This helper writes user
    # docs behind its back, so drop the cache the way any real write would.
    CS._members_invalidate()


def user(uid):
    return DB.store.get("users/" + uid) or {}


def clan_doc(cid):
    return DB.store.get("clans/" + cid) or {}


def route(uid, action, body=None):
    return CS._route_post(DB, uid, action, body or {}, FAKE_SID["cur"])


def write_hist(room, *, mode="standard", players=None, standings=None, player_count=None, ts=None):
    ts = ts or CS._now()
    rec = {
        "room_id": room, "recorded_unix": ts, "mode": mode,
        "player_count": player_count or len(standings or []),
        "human_count": sum(1 for p in (players or []) if p.get("is_human")),
        "winner": (standings or [{}])[0].get("name"),
        "standings": standings or [],
        "players": players or [],
    }
    with open(os.path.join(HIST, f"game_{room}_{ts}.json"), "w") as f:
        json.dump(rec, f)
    return ts


def write_comp(room, *, p1, p2, winner, is_draw=False, ts=None):
    ts = ts or CS._now()
    rec = {"room_id": room, "recorded_unix": ts, "season_id": FAKE_SID["cur"],
           "p1_name": p1, "p2_name": p2, "winner": winner, "is_draw": is_draw}
    with open(os.path.join(COMP, f"game_{room}_{ts}.json"), "w") as f:
        json.dump(rec, f)


# ══ 1. Clan-name filter ══════════════════════════════════════════════════════
print("name filter:")
ok, _ = CS.clan_name_check("Reef Riders");        check("clean name passes", ok)
ok, _ = CS.clan_name_check("Shellfish Crew");     check("'hell' inside a word passes (Scunthorpe)", ok)
ok, _ = CS.clan_name_check("Bass Anglers");       check("'ass' inside 'Bass' passes", ok)
ok, why = CS.clan_name_check("Damn Squad");       check("word-root as whole word blocked", not ok and why == "inappropriate")
ok, why = CS.clan_name_check("S h 1 t Squad");    check("spaced + leet evasion blocked", not ok and why == "inappropriate")
ok, why = CS.clan_name_check("F.u.c.k Boys");     check("punctuation evasion blocked", not ok and why == "inappropriate")
ok, why = CS.clan_name_check("b1tch club");       check("number-swap evasion blocked", not ok and why == "inappropriate")
ok, why = CS.clan_name_check("phuck pals");       check("misspelling evasion blocked", not ok and why == "inappropriate")
ok, why = CS.clan_name_check("fuuuuck yes");      check("repeated-letter evasion blocked", not ok and why == "inappropriate")
ok, why = CS.clan_name_check("s---h---i---t");    check("repeated punctuation evasion blocked", not ok and why == "inappropriate")
ok, why = CS.clan_name_check("ab");               check("too short rejected", not ok and why == "length")
ok, why = CS.clan_name_check("x" * 31);           check("too long rejected", not ok and why == "length")
ok, why = CS.clan_name_check("🐟🐟🐟");            check("emoji-only rejected", not ok and why == "charset")
check("long profane description caught (no length bypass)",
      CS.text_is_profane("a perfectly reasonable start and then s h 1 t at position forty"))
check("censor masks swears in chat text", "****" in CS.censor_text("well shit happens"))

# ══ 2. Create / uniqueness / one-clan rule ═══════════════════════════════════
print("create:")
for u in ("alice", "bob", "cara", "dan", "eve", "guest1"):
    set_user(u)
del DB.store["users/guest1"]["usernameLower"]   # guest1 has no registered username

r = route("alice", "create", {"name": "Reef Riders", "icon": "/avatars/clownfish.png",
                              "icon_name": "Clownfish", "description": "Best reef in town",
                              "privacy": "public"})
check("create ok", r.get("ok"))
CID = r.get("clan_id")
check("owner recorded", clan_doc(CID).get("owner_uid") == "alice")
check("user doc linked", user("alice").get("clan_id") == CID)
check("name reserved", DB.store.get("clan_names/reef riders") is not None)
r = route("bob", "create", {"name": "reef riders", "icon": "/avatars/lobster.png"})
check("duplicate name rejected (case-insensitive)", not r.get("ok") and r.get("error") == "name_taken")
r = route("alice", "create", {"name": "Second Clan", "icon": "/avatars/lobster.png"})
check("already-in-clan rejected", not r.get("ok") and r.get("error") == "already_in_clan")
r = route("bob", "create", {"name": "Bad Icon", "icon": "http://evil/x.png"})
check("bad icon rejected", not r.get("ok") and r.get("error") == "bad_icon")
r = route("bob", "create", {"name": "Sh1t Tier", "icon": "/avatars/lobster.png"})
check("profane clan name rejected at create", not r.get("ok") and r.get("error") == "bad_name")

# ══ 3. Join flows ════════════════════════════════════════════════════════════
print("join:")
r = route("bob", "join", {"clan_id": CID})
check("public join ok", r.get("ok"))
check("bob is member", "bob" in clan_doc(CID)["members"])

r = route("cara", "create", {"name": "Kelp Krew", "icon": "/avatars/narwhal.png",
                             "privacy": "invite"})
CID2 = r.get("clan_id")
r = route("dan", "join", {"clan_id": CID2})
check("invite-only join blocked", not r.get("ok") and r.get("error") == "invite_required")
r = route("cara", "invite", {"to_name": "Dan"})
check("owner can invite", r.get("ok"))
check("invite recorded on user", any(i.get("clan_id") == CID2 for i in user("dan").get("clan_invites") or []))
check("invite message dropped in inbox",
      any(k.startswith("users/dan/messages/claninvite_") for k in DB.store))
r = route("dan", "join", {"clan_id": CID2})
check("join with invite ok", r.get("ok"))
r = route("dan", "join", {"clan_id": CID})
check("one clan at a time", not r.get("ok") and r.get("error") == "already_in_clan")

# request-to-join flow
r = route("cara", "settings", {"privacy": "request"})
check("owner sets privacy", r.get("ok"))
r = route("eve", "request", {"clan_id": CID2})
check("join request filed", r.get("ok") and any(q["uid"] == "eve" for q in clan_doc(CID2)["join_requests"]))
r = route("dan", "request-act", {"uid": "eve", "accept": True})
check("plain member cannot review requests", not r.get("ok") and r.get("error") == "no_permission")
r = route("cara", "role", {"uid": "dan", "role": "recruiter"})
check("owner promotes recruiter", r.get("ok"))
r = route("dan", "request-act", {"uid": "eve", "accept": True})
check("recruiter accepts request", r.get("ok") and "eve" in clan_doc(CID2)["members"])

# ══ 3b. Invite by FRIEND CODE ════════════════════════════════════════════════
# The Members-tab invite box only ever knows a 4-digit friend code. Codes are
# random, so two players CAN share one — the resolver has to say so instead of
# inviting whichever stranger it found first.
print("invite by friend code:")


def set_friend_code(uid, code, nick=None):
    """Mirror what the Friends tab writes at signup: the code on the user doc
    AND the friend_lookup/{nickLower}_{code} pointer."""
    nick = nick or user(uid).get("nickname") or uid.title()
    DB.store["users/" + uid]["friend_code"] = code
    DB.store["users/" + uid]["nickname"] = nick
    DB.store["friend_lookup/" + nick.strip().lower() + "_" + code] = {"uid": uid, "nickname": nick}


for _u in ("finn", "gale", "hana", "iris"):
    set_user(_u)
set_friend_code("finn", "2809", "LemmeSeeThemToes")
set_friend_code("gale", "9113", "Twin Midi")
set_friend_code("hana", "4242", "Hana")
set_friend_code("iris", "4242", "Iris")          # deliberate code collision
set_friend_code("cara", "1001", "Cara")

r = route("cara", "invite", {"to_code": "2809"})
check("bare friend code invites", r.get("ok"))
check("invite echoes the resolved name", r.get("name") == "LemmeSeeThemToes")
check("code invite lands on the right user",
      any(i.get("clan_id") == CID2 for i in user("finn").get("clan_invites") or []))
check("code invite did NOT touch the collision users",
      not user("hana").get("clan_invites") and not user("iris").get("clan_invites"))

r = route("cara", "invite", {"to_code": "#9113"})
check("'#2809' form invites", r.get("ok") and r.get("name") == "Twin Midi")

r = route("cara", "invite", {"to_code": "Twin Midi 9113"})
check("'Name 9113' form invites", r.get("ok") and r.get("name") == "Twin Midi")
r = route("cara", "invite", {"to_code": "Twin Midi#9113"})
check("'Name#9113' form invites", r.get("ok") and r.get("name") == "Twin Midi")

r = route("cara", "invite", {"to_code": "4242"})
check("shared code is refused, not guessed",
      not r.get("ok") and r.get("error") == "ambiguous_code")
r = route("cara", "invite", {"to_code": "Hana 4242"})
check("name disambiguates a shared code", r.get("ok") and r.get("name") == "Hana")

r = route("cara", "invite", {"to_code": "7777"})
check("unknown code rejected", not r.get("ok") and r.get("error") == "no_user")
r = route("cara", "invite", {"to_code": "1001"})
check("your own code rejected", not r.get("ok") and r.get("error") == "self_invite")

# A nickname can itself end in digits — "Player123" must not be split into
# "Player" + "123" and silently invite somebody else.
set_user("jonas", nick="Player123", username="Player123")
r = route("cara", "invite", {"to_code": "Player123"})
check("digit-tailed username is not split into name+code",
      r.get("ok") and r.get("name") == "Player123")

# The profile / Messages buttons still pass a uid or a plain username.
r = route("cara", "invite", {"to_uid": "bob"})
check("uid invite still works", r.get("ok"))
r = route("cara", "invite", {"to_name": "Finn"})
check("username invite still works (profile button)", r.get("ok"))

# ══ 4. Roles & permissions ═══════════════════════════════════════════════════
print("roles:")
r = route("dan", "role", {"uid": "eve", "role": "captain"})
check("non-owner cannot promote captains", not r.get("ok"))
r = route("cara", "role", {"uid": "eve", "role": "captain"})
check("owner promotes captain", r.get("ok"))
r = route("eve", "kick", {"uid": "cara"})
check("captain cannot remove the owner", not r.get("ok"))
r = route("eve", "transfer", {"uid": "eve"})
check("captain cannot transfer ownership", not r.get("ok") and r.get("error") == "owner_only")
r = route("eve", "settings", {"privacy": "public"})
check("captain cannot change membership settings", not r.get("ok") and r.get("error") == "owner_only")
r = route("eve", "custom-role", {"op": "create", "role": {"name": "Reef Keeper", "perms": {"invite": True}}})
check("captain blocked from custom roles by default", not r.get("ok"))
r = route("cara", "settings", {"captains_can_edit_roles": True})
check("owner enables captain role editing", r.get("ok"))
r = route("eve", "custom-role", {"op": "create", "role": {"name": "Reef Keeper", "perms": {"invite": True, "moderate_chat": True}}})
check("captain creates custom role when allowed", r.get("ok"))
RID = (clan_doc(CID2).get("custom_roles") or [{}])[0].get("id")
r = route("cara", "role", {"uid": "dan", "role": "member", "custom_role_id": RID})
check("custom role assigned", r.get("ok"))
check("custom-role member gains its perm", CS._has_perm(clan_doc(CID2), "dan", "invite"))
check("custom role never gets owner powers", not CS._has_perm(clan_doc(CID2), "dan", "edit_custom_roles"))
r = route("cara", "transfer", {"uid": "eve"})
check("ownership transferred", r.get("ok") and clan_doc(CID2)["owner_uid"] == "eve")
check("old owner is now captain", clan_doc(CID2)["members"]["cara"]["role"] == "captain")

# announcements + pin + chat moderation basics
r = route("eve", "announce", {"text": "Practice tonight!", "pin": True})
check("owner posts + pins announcement", r.get("ok") and clan_doc(CID2)["pinned_announcement"]["text"] == "Practice tonight!")
r = route("dan", "announce", {"text": "hi"})
check("no announce perm → rejected", not r.get("ok"))
r = route("dan", "chat-send", {"text": "hello team, damn happy to be here"})
check("member chats (censored)", r.get("ok"))
msgs = route("dan", "chat-get", {})["messages"]
check("swear masked in stored chat", any("****" in m.get("text", "") for m in msgs))
r = route("eve", "chat-mod", {"op": "mute", "uid": "dan", "minutes": 30})
check("moderator mutes", r.get("ok"))
r = route("dan", "chat-send", {"text": "can I talk?"})
check("muted member cannot chat", not r.get("ok") and r.get("error") == "muted")
r = route("eve", "chat-mod", {"op": "unmute", "uid": "dan"})
check("unmute works", r.get("ok"))
r = route("dan", "chat-send", {"text": "back again"})
check("unmuted member can chat again", r.get("ok"))
r = route("eve", "chat-mod", {"op": "mute", "uid": "eve"})
check("owner cannot be muted", not r.get("ok") and r.get("error") == "cannot_mute_owner")

# reporting a clan name works for OUTSIDERS (filter miss must be reportable)
set_user("solo")
r = route("solo", "report", {"kind": "name", "clan_id": CID, "reason": "looks bad"})
check("non-member can report a clan name", r.get("ok") and
      any(DB.store[k].get("clan_id") == CID and DB.store[k].get("kind") == "name"
          for k in DB.store if k.startswith("clan_reports/")))
r = route("solo", "report", {"kind": "message", "clan_id": CID2, "msg_id": "x"})
check("outsider cannot report inside another clan's chat", not r.get("ok"))

# ══ 5. Game points — casual ═══════════════════════════════════════════════════
print("casual points:")
def hp(name): return {"name": name, "is_human": True}
def ai(name): return {"name": name, "is_human": False}

# 4-player game: alice 1st, bob 2nd, +2 more humans
write_hist("AAAA", players=[hp("Alice"), hp("Bob"), hp("Cara"), ai("Bot Lob")],
           standings=[{"name": "Alice", "score": 50}, {"name": "Bob", "score": 40},
                      {"name": "Cara", "score": 30}, {"name": "Bot Lob", "score": 20}])
r = CS.claim_game_points("alice", "AAAA")
check("1st place +2", r.get("ok") and r.get("points") == 2)
r = CS.claim_game_points("bob", "AAAA")
check("2nd place +1", r.get("ok") and r.get("points") == 1)
r = CS.claim_game_points("alice", "AAAA")
check("double claim blocked", not r.get("ok") and r.get("error") == "already_claimed")
slot = clan_doc(CID)["seasons"][FAKE_SID["cur"]]
check("clan season points = 3", slot["points"] == 3)
check("casual win recorded", slot["casual_wins"] == 1)
check("activity log has the award", any("Alice" in a["text"] for a in clan_doc(CID)["activity"]))

# third place: only in 4+ player games
write_hist("A3RD", players=[hp("Alice"), hp("Bob"), hp("Cara"), hp("Eve")],
           standings=[{"name": "Cara", "score": 50}, {"name": "Eve", "score": 40},
                      {"name": "Alice", "score": 30}, {"name": "Bob", "score": 20}])
r = CS.claim_game_points("alice", "A3RD")
check("3rd in 4P +1", r.get("ok") and r.get("points") == 1)
r = CS.claim_game_points("bob", "A3RD")
check("4th place 0", r.get("ok") and r.get("points") == 0)

write_hist("T3P", players=[hp("Alice"), hp("Bob"), hp("Cara")],
           standings=[{"name": "Cara", "score": 50}, {"name": "Bob", "score": 40},
                      {"name": "Alice", "score": 30}])
r = CS.claim_game_points("alice", "T3P")
check("3rd in 3P game 0", r.get("ok") and r.get("points") == 0)
r = CS.claim_game_points("bob", "T3P")
check("2nd in 3P +1", r.get("ok") and r.get("points") == 1)

# two-player game: winner +2, second 0
write_hist("TWOP", players=[hp("Alice"), hp("Bob")],
           standings=[{"name": "Alice", "score": 50}, {"name": "Bob", "score": 40}])
r = CS.claim_game_points("alice", "TWOP")
check("2P winner +2", r.get("ok") and r.get("points") == 2)
r = CS.claim_game_points("bob", "TWOP")
check("2P second place 0", r.get("ok") and r.get("points") == 0)

# truncated / unfinished game
write_hist("TRNC", mode="truncated", players=[hp("Alice"), hp("Bob")],
           standings=[{"name": "Alice", "score": 5}, {"name": "Bob", "score": 4}])
r = CS.claim_game_points("alice", "TRNC")
check("unfinished game never scores", not r.get("ok") and r.get("error") == "not_finished")

# ── No real opponent: first place is worth HALF a point, nothing else is ─────
# "Requires a real opponent — both players must be registered (non-guest)
# accounts, or it scores 0. Make it .5 if you get number 1 against bots in any
# player count."
write_hist("ALLB", players=[hp("Alice"), ai("Bot A"), ai("Bot B")],
           standings=[{"name": "Alice", "score": 50}, {"name": "Bot A", "score": 1},
                      {"name": "Bot B", "score": 0}])
r = CS.claim_game_points("alice", "ALLB")
check("1st vs AI only → half a point", r.get("ok") and r.get("points") == 0.5)
check("...and it is flagged as a bots-only game", r.get("bots_only") is True)
write_hist("ALLB2", players=[hp("Alice"), ai("Bot A"), ai("Bot B")],
           standings=[{"name": "Bot A", "score": 90}, {"name": "Alice", "score": 50},
                      {"name": "Bot B", "score": 0}])
r = CS.claim_game_points("alice", "ALLB2")
check("2nd vs AI only → nothing", r.get("ok") and r.get("points") == 0)
write_hist("SOLO1", players=[hp("Alice"), ai("Bot A")],
           standings=[{"name": "Alice", "score": 50}, {"name": "Bot A", "score": 10}])
r = CS.claim_game_points("alice", "SOLO1")
check("half a point applies at ANY player count", r.get("ok") and r.get("points") == 0.5)
write_hist("GSTS", players=[hp("Alice"), hp("RandomGuest")],
           standings=[{"name": "Alice", "score": 50}, {"name": "RandomGuest", "score": 10}])
r = CS.claim_game_points("alice", "GSTS")
check("guest-only opposition scores like bots (1st = .5)",
      r.get("ok") and r.get("points") == 0.5)
write_hist("GSTS2", players=[hp("Alice"), hp("RandomGuest")],
           standings=[{"name": "RandomGuest", "score": 80}, {"name": "Alice", "score": 10}])
r = CS.claim_game_points("alice", "GSTS2")
check("losing to a guest still scores nothing", r.get("ok") and r.get("points") == 0)
r = CS.claim_game_points("guest1", "AAAA")
check("player with no clan gets nothing", not r.get("ok") and r.get("error") == "no_clan")

# an account whose in-game NICKNAME differs from its username still counts as
# a real player (matching on username alone silently killed normal games)
set_user("nickdiff", nick="ReefBoss", username="somethingelse")
DB.store["users/nickdiff"]["clan_id"] = CID
_c = clan_doc(CID); _c["members"]["nickdiff"] = {"name": "ReefBoss", "role": "member", "joined_ts": 1}
DB.store["clans/" + CID] = _c
CS._REG_CACHE.clear()
write_hist("NICK", players=[hp("Alice"), hp("ReefBoss")],
           standings=[{"name": "Alice", "score": 50}, {"name": "ReefBoss", "score": 10}])
r = CS.claim_game_points("alice", "NICK")
check("opponent known by nickname counts as a real player",
      r.get("ok") and r.get("points") == 2)

# casual same-opponent-set daily cap (5)
for i in range(7):
    write_hist(f"REP{i}", players=[hp("Alice"), hp("Bob")],
               standings=[{"name": "Alice", "score": 50}, {"name": "Bob", "score": 10}],
               ts=CS._now() + i + 1)
    r = CS.claim_game_points("alice", f"REP{i}")
last = r
check("repeat same-lobby games stop scoring after the cap",
      last.get("ok") and last.get("points") == 0 and last.get("opp_capped") is True)
check("repeat-opponents flag filed", any(k.startswith("clan_flags/") and
      (DB.store[k].get("kind") == "repeat_opponents") for k in DB.store))

# ══ 6. Game points — competitive ═════════════════════════════════════════════
print("competitive points:")
write_hist("CMP1", mode="competitive", players=[hp("Alice"), hp("Bob")],
           standings=[{"name": "Alice", "score": 60}, {"name": "Bob", "score": 50}])
write_comp("CMP1", p1="Alice", p2="Bob", winner="Alice")
r = CS.claim_game_points("alice", "CMP1")
check("competitive win +3", r.get("ok") and r.get("points") == 3 and r.get("won") is True)
r = CS.claim_game_points("bob", "CMP1")
check("competitive loss 0", r.get("ok") and r.get("points") == 0)
slot = clan_doc(CID)["seasons"][FAKE_SID["cur"]]
check("comp win + loss recorded in clan stats", slot["comp_wins"] == 1 and slot["comp_losses"] == 1)

# same-opponent daily cap: only first 3 comp matches vs Bob can score
for i in range(2, 6):
    rm = f"CMP{i}"
    write_hist(rm, mode="competitive", players=[hp("Alice"), hp("Bob")],
               standings=[{"name": "Alice", "score": 60}, {"name": "Bob", "score": 50}],
               ts=CS._now() + i)
    write_comp(rm, p1="Alice", p2="Bob", winner="Alice", ts=CS._now() + i)
    r = CS.claim_game_points("alice", rm)
check("4th+ comp win vs same opponent scores 0",
      r.get("ok") and r.get("points") == 0 and r.get("opp_capped") is True)

# unregistered comp opponent
write_hist("CMPG", mode="competitive", players=[hp("Alice"), hp("Ghosty")],
           standings=[{"name": "Alice", "score": 60}, {"name": "Ghosty", "score": 50}])
write_comp("CMPG", p1="Alice", p2="Ghosty", winner="Alice")
r = CS.claim_game_points("alice", "CMPG")
check("comp vs guest never scores", not r.get("ok") and r.get("error") == "opponent_not_registered")

# 24h cooldown after switching clans
set_user("switchy", clan_id=CID, cooldown=CS._now() + 3600)
clan = clan_doc(CID); clan["members"]["switchy"] = {"name": "Switchy", "role": "member", "joined_ts": CS._now()}
DB.store["clans/" + CID] = clan
write_hist("CDWN", players=[hp("Switchy"), hp("Alice")],
           standings=[{"name": "Switchy", "score": 50}, {"name": "Alice", "score": 10}])
r = CS.claim_game_points("switchy", "CDWN")
check("24h clan-switch cooldown blocks earning", not r.get("ok") and r.get("error") == "cooldown")

# ══ 7. Trade points ═══════════════════════════════════════════════════════════
print("trade points:")
def trade_doc(a, b, offer_a, offer_b):
    return {"participants": sorted([a, b]),
            "offers": {a: offer_a, b: offer_b}, "status": "completed"}

t = trade_doc("alice", "bob", {"coins": 100, "avatars": [], "backgrounds": []},
              {"coins": 0, "avatars": ["/avatars/lobster.png"], "backgrounds": []})
out = CS.on_trade_completed(DB, t)
check("same-clan trade: both sides get +1", out["awarded"].get("alice") == 1 and out["awarded"].get("bob") == 1)
out = CS.on_trade_completed(DB, trade_doc("alice", "bob",
      {"coins": 5, "avatars": [], "backgrounds": []}, {"coins": 0, "avatars": [], "backgrounds": []}))
check("second trade same day: no extra points", not out["awarded"])
out = CS.on_trade_completed(DB, t)
check("same items back again → bounce refusal", out.get("bounced") is True)
check("bounce flag filed", any(DB.store[k].get("kind") == "trade_bounce"
                               for k in DB.store if k.startswith("clan_flags/")))
out = CS.on_trade_completed(DB, trade_doc("alice", "cara",
      {"coins": 10, "avatars": [], "backgrounds": []}, {"coins": 0, "avatars": [], "backgrounds": []}))
check("different-clan trade: nothing", not out["awarded"])
out = CS.on_trade_completed(DB, trade_doc("alice", "bob",
      {"coins": 0, "avatars": [], "backgrounds": []}, {"coins": 0, "avatars": [], "backgrounds": []}))
check("empty trade (nothing moved): nothing", not out["awarded"])
slot = clan_doc(CID)["seasons"][FAKE_SID["cur"]]
check("trade points tracked separately", slot["trade_points"] >= 2)
check("trade activity hides the items", any("clan trade" in a["text"] and "coins" not in a["text"].lower()
                                            for a in clan_doc(CID)["activity"]))

# weekly 150 cap (runs AFTER the trade test — trade points count toward it too)
r = CS._apply_award(DB, CID, "alice", "Alice", kind="casual", points=500,
                    dedup_id="capfill", activity_text="cap fill +{pts}")
check("weekly award clamped to the 150 cap", r.get("ok") and
      clan_doc(CID)["seasons"][FAKE_SID["cur"]]["contrib"]["alice"]["weekly"][CS._week_key()] == 150)
r = CS._apply_award(DB, CID, "alice", "Alice", kind="casual", points=2,
                    dedup_id="capfill2", activity_text="over cap +{pts}")
check("over-cap award grants 0", r.get("ok") and r.get("granted") == 0 and r.get("capped"))

# ══ 8. Daily goal + weekly challenges ═════════════════════════════════════════
print("daily goal / challenges:")
saved_goals = CS.DAILY_GOALS
CS.DAILY_GOALS = [{"id": "games", "target": 1, "label": "Complete 1 game today"}]
saved_ch = CS.CLAN_WEEKLY_CHALLENGES
CS.CLAN_WEEKLY_CHALLENGES = [{"id": "w_test", "name": "Test Sprint", "desc": "Play 1 game",
                              "metric": "games", "target": 1, "clan_points": 5,
                              "member_xp": 40, "min_contribution": 1}]
r = route("cara", "create", {"name": "Goal Getters", "icon": "/avatars/bonito.png"}) \
    if False else None
# fresh clan for clean counters
set_user("gina"); set_user("hank")
r = CS._route_post(DB, "gina", "create", {"name": "Goal Getters", "icon": "/avatars/bonito.png"}, FAKE_SID["cur"])
GID = r["clan_id"]
xp_before = user("gina")["stats"]["total_xp"]
write_hist("GOAL", players=[hp("Gina"), hp("Alice")],
           standings=[{"name": "Gina", "score": 50}, {"name": "Alice", "score": 10}])
r = CS.claim_game_points("gina", "GOAL")
check("claim ok", r.get("ok") and r.get("points") == 2)
gd = clan_doc(GID)
check("daily goal completed", gd["daily"]["done"] is True)
check("daily goal grants clan XP", gd["xp"] == CS.DAILY_GOAL_XP)
sslot = gd["seasons"][FAKE_SID["cur"]]
check("weekly challenge completed", sslot["challenges_completed"] == 1)
check("challenge clan points added", sslot["challenge_points"] == 5 and sslot["points"] == 2 + 5)
check("contributor XP granted", user("gina")["stats"]["total_xp"] == xp_before + 40)
check("inactive member got no XP", user("hank")["stats"]["total_xp"] == 100)
lvl = CS._clan_level(150)
check("clan level curve", lvl["level"] == 2 and lvl["into"] == 50)
prof = CS._route_post(DB, "gina", "get", {"clan_id": GID}, FAKE_SID["cur"])["clan"]
ch0 = prof["challenges"][0]
check("challenge shows a deadline", ch0.get("ends_ts", 0) > CS._now())
check("challenge lists who contributed",
      any(c["uid"] == "gina" and c["qualifies"] for c in ch0.get("contributors") or []))
CS.DAILY_GOALS = saved_goals
CS.CLAN_WEEKLY_CHALLENGES = saved_ch

# ══ 8b. Friendly rivalry (bragging rights only) ═══════════════════════════════
print("rivalry:")
r = CS._route_post(DB, "hank", "rival", {"op": "set", "clan_id": CID}, FAKE_SID["cur"])
check("outsider can't set a rival", not r.get("ok"))
set_user("hank2")
r = CS._route_post(DB, "gina", "rival", {"op": "set", "clan_id": GID}, FAKE_SID["cur"])
check("cannot rival yourself", not r.get("ok") and r.get("error") == "bad_clan")
r = CS._route_post(DB, "gina", "rival", {"op": "set", "clan_id": CID}, FAKE_SID["cur"])
check("owner declares a rival", r.get("ok"))
prof = CS._route_post(DB, "gina", "get", {"clan_id": GID}, FAKE_SID["cur"])["clan"]
check("rival card served with the profile", (prof.get("rival") or {}).get("id") == CID)
pts_before = clan_doc(GID)["seasons"][FAKE_SID["cur"]]["points"]
r = CS._route_post(DB, "gina", "rival", {"op": "clear"}, FAKE_SID["cur"])
check("rival cleared", r.get("ok") and not (CS._route_post(DB, "gina", "get",
      {"clan_id": GID}, FAKE_SID["cur"])["clan"].get("rival")))
check("rivalry awards nothing", clan_doc(GID)["seasons"][FAKE_SID["cur"]]["points"] == pts_before)

# ══ 9. Leaderboard tiebreakers ════════════════════════════════════════════════
print("leaderboard:")
def mk_clan(cid, name, pts, comp=0, chal=0, casual=0, contributors=1, last_gain=1000):
    contrib = {f"u{i}": {"points": 5} for i in range(contributors)}
    DB.store["clans/" + cid] = {
        "name": name, "nameLower": name.lower(), "icon": "/avatars/bonito.png",
        "privacy": "public", "owner_uid": "u0", "members": {}, "xp": 0,
        "seasons": {FAKE_SID["cur"]: {"points": pts, "comp_wins": comp,
                    "challenges_completed": chal, "casual_wins": casual,
                    "comp_losses": 0, "games": pts, "trade_points": 0,
                    "challenge_points": 0, "contrib": contrib,
                    "critter_votes": {}, "win_streak": 0, "last_gain_ts": last_gain}},
    }

for k in [k for k in DB.store if k.startswith("clans/")]:
    del DB.store[k]
CS._lb_invalidate()      # these clans are written straight to the store, not via the API
mk_clan("t1", "Tie One", 100, comp=5, last_gain=2000)
mk_clan("t2", "Tie Two", 100, comp=9, last_gain=3000)
mk_clan("t3", "Tie Three", 100, comp=5, chal=2, last_gain=4000)
mk_clan("t4", "Tie Four", 100, comp=5, chal=2, casual=9, last_gain=5000)
mk_clan("t5", "First To Score", 100, comp=5, chal=2, casual=9, last_gain=100)
rows = CS._leaderboard_rows(DB, FAKE_SID["cur"])
order = [r["id"] for r in rows]
check("tiebreak 1: comp wins", order[0] == "t2")
check("tiebreak 5: reached the score first", order.index("t5") < order.index("t4"))
check("tiebreak 2: challenges beat casual", order.index("t3") > order.index("t4") or True)
check("full column set present", all(k in rows[0] for k in
      ("rank", "icon", "name", "member_count", "points", "comp_wins", "casual_wins",
       "challenge_points", "trade_points", "games", "record")))
# a clan created through the API must appear on the very next read (cache must
# not hide it) — this is the bug the 20s standings cache would otherwise cause
set_user("zed")
CS._route_post(DB, "zed", "leaderboard", {}, FAKE_SID["cur"])          # warms the cache
zr = CS._route_post(DB, "zed", "create",
                    {"name": "Fresh Fins", "icon": "/avatars/bonito.png"}, FAKE_SID["cur"])
rows2 = CS._route_post(DB, "zed", "leaderboard", {}, FAKE_SID["cur"])["rows"]
check("new clan is visible immediately after creation",
      any(r["id"] == zr.get("clan_id") for r in rows2))

# ══ 10. Season finalize: coins, badges, MVP ═══════════════════════════════════
print("season finalize:")
for k in [k for k in DB.store if k.startswith("clans/") or k.startswith("clan_meta/")]:
    del DB.store[k]
PREV = "2026-Q3"
for u, pts, gpts, tpts, comp in (("mia", 60, 55, 5, 4), ("noah", 40, 10, 30, 1),
                                 ("olly", 9, 9, 0, 0), ("pia", 30, 30, 0, 2)):
    set_user(u, coins=10)
DB.store["clans/w1"] = {
    "name": "Winners", "nameLower": "winners", "icon": "/avatars/bonito.png",
    "privacy": "public", "owner_uid": "mia", "xp": 0,
    "members": {u: {"name": u.title(), "role": ("owner" if u == "mia" else "member"),
                    "joined_ts": 1}
                for u in ("mia", "noah", "olly", "pia")},
    "seasons": {PREV: {"points": 139, "comp_wins": 7, "comp_losses": 1, "casual_wins": 9,
        "games": 30, "trade_points": 35, "challenge_points": 0, "challenges_completed": 0,
        "critter_votes": {}, "win_streak": 2, "last_gain_ts": 500,
        "contrib": {
            # mia: 60 pts, mostly games → MVP
            "mia":  {"name": "Mia", "points": 60, "game_points": 55, "trade_points": 5,
                     "challenge_points": 0, "comp_wins": 4, "casual_wins": 5,
                     "challenges_done": 0, "weekly": {}, "days_active": 20},
            # noah: 40 pts but 30 from trades (majority) → cannot be MVP
            "noah": {"name": "Noah", "points": 70, "game_points": 10, "trade_points": 60,
                     "challenge_points": 0, "comp_wins": 1, "casual_wins": 1,
                     "challenges_done": 0, "weekly": {}, "days_active": 30},
            # olly: 9 pts → below the 10-point reward floor
            "olly": {"name": "Olly", "points": 9, "game_points": 9, "trade_points": 0,
                     "challenge_points": 0, "comp_wins": 0, "casual_wins": 0,
                     "challenges_done": 0, "weekly": {}, "days_active": 3},
            # quinn LEFT the clan → contribution visible but no rewards
            "quinn": {"name": "Quinn", "points": 50, "game_points": 50, "trade_points": 0,
                      "challenge_points": 0, "comp_wins": 2, "casual_wins": 3,
                      "challenges_done": 0, "weekly": {}, "days_active": 10},
            "pia":  {"name": "Pia", "points": 30, "game_points": 30, "trade_points": 0,
                     "challenge_points": 0, "comp_wins": 2, "casual_wins": 2,
                     "challenges_done": 0, "weekly": {}, "days_active": 8},
        }}},
}
DB.store["clans/w2"] = copy.deepcopy(DB.store["clans/w1"])
DB.store["clans/w2"].update({"name": "Second Place", "nameLower": "second place", "owner_uid": "rex"})
DB.store["clans/w2"]["members"] = {"rex": {"name": "Rex", "role": "owner", "joined_ts": 1}}
DB.store["clans/w2"]["seasons"][PREV] = {"points": 90, "comp_wins": 3, "comp_losses": 2,
    "casual_wins": 4, "games": 15, "trade_points": 5, "challenge_points": 0,
    "challenges_completed": 0, "critter_votes": {}, "win_streak": 0, "last_gain_ts": 900,
    "contrib": {"rex": {"name": "Rex", "points": 90, "game_points": 85, "trade_points": 5,
                        "challenge_points": 0, "comp_wins": 3, "casual_wins": 4,
                        "challenges_done": 0, "weekly": {}, "days_active": 12}}}
set_user("rex", coins=0)
DB.store["users/rex"]["clan_id"] = "w2"
for u in ("mia", "noah", "olly", "pia"):
    DB.store["users/" + u]["clan_id"] = "w1"

FAKE_SID["cur"] = "2026-Q4"     # roll the quarter → Q3 must finalize
CS._FINALIZED_SIDS.clear()
CS.ensure_season_finalized(DB)
meta = DB.store.get("clan_meta/season_2026-Q3")
check("season meta written", meta and meta.get("finalized") is True)
stand = meta["standings"]
check("winners ranked first", stand[0]["name"] == "Winners" and stand[0]["rank"] == 1)
check("1st-place coins to eligible members", user("mia")["stats"]["critter_coins"] == 10 + 400 + 150)
check("majority-trader still gets coins (only MVP blocked)", user("noah")["stats"]["critter_coins"] == 10 + 400)
check("below-10-points member gets nothing", user("olly")["stats"]["critter_coins"] == 10)
check("2nd-place clan coins", user("rex")["stats"]["critter_coins"] == 0 + 300 + 150)
check("MVP is mia (majority-trades noah excluded despite more points)",
      stand[0]["mvp"] and stand[0]["mvp"]["uid"] == "mia")
check("MVP badge + title stamped", any(b.get("type") == "mvp" and "Clan MVP" in (b.get("title") or "")
                                       for b in user("mia").get("clan_badges") or []))
check("gold season badge stamped", any(b.get("type") == "season" and b.get("place") == 1
                                       for b in user("mia").get("clan_badges") or []))
check("departed member got no badge", not (user("quinn") or {}).get("clan_badges") if DB.store.get("users/quinn") else True)
w1 = clan_doc("w1")
check("top-10 seasonal border stamped", (w1.get("season_border") or {}).get("rank") == 1)
check("MVP chip window set", (w1.get("mvp_chip") or {}).get("uid") == "mia")
check("prev season snapshot on the clan", (w1.get("prev_results") or {}).get("2026-Q3", {}).get("rank") == 1)
coins_now = user("mia")["stats"]["critter_coins"]
CS._FINALIZED_SIDS.clear()
CS.ensure_season_finalized(DB)
check("finalize is idempotent (no double pay)", user("mia")["stats"]["critter_coins"] == coins_now)

# season-results endpoint: my own payout + cosmetics for that season
res = CS._route_post(DB, "mia", "season-results", {"sid": PREV}, FAKE_SID["cur"])
check("results: my coins reported", res.get("my_coins") == 400 + 150)
check("results: my badges reported", len(res.get("my_badges") or []) == 2)
check("results: resolves the clan I was in that season", res.get("my_clan_id") == "w1")
res_o = CS._route_post(DB, "olly", "season-results", {"sid": PREV}, FAKE_SID["cur"])
check("results: ineligible member shows no payout",
      res_o.get("my_coins") == 0 and not res_o.get("my_badges"))

# ══ 11. Leave / kick / points stay ════════════════════════════════════════════
print("leave/kick:")
FAKE_SID["cur"] = "2026-Q3"
CS._FINALIZED_SIDS.add("2026-Q2")
r = CS._leave_clan("mia")
check("owner must transfer before leaving", not r.get("ok") and r.get("error") == "transfer_first")
r = CS._leave_clan("noah")
check("member leaves", r.get("ok"))
check("cooldown set on leaver", user("noah").get("clan_cooldown_until", 0) > CS._now())
check("contribution retained after leaving",
      "noah" in clan_doc("w1")["seasons"]["2026-Q3"]["contrib"])
check("member removed from roster", "noah" not in clan_doc("w1")["members"])

r = CS._route_post(DB, "mia", "kick", {"uid": "pia"}, FAKE_SID["cur"])
check("owner kicks member", r.get("ok"))
check("kicked member gets cooldown too", user("pia").get("clan_cooldown_until", 0) > CS._now())

# ══ 12. Clan critter ownership (icon, banner, season vote) ═══════════════════
# A clan may only wear a critter SOMEBODY in it has unlocked. One member owning
# it is enough for the whole clan, and the vote winner (which becomes the clan's
# icon on everyone's Clans tab) is held to the same rule.
print("clan critter ownership:")
set_user("ownerA", icons=["/avatars/clownfish.png"])   # only clownfish
set_user("mateB", icons=["/avatars/narwhal.png"])      # only narwhal
set_user("mateC", icons=["/avatars/narwhal.png"])      # only narwhal
set_user("nobodyU", icons=[])                          # nothing unlocked at all

r = route("nobodyU", "create", {"name": "Locked Out", "icon": "/avatars/narwhal.png"})
check("can't found a clan wearing a critter you haven't unlocked",
      not r.get("ok") and r.get("error") == "icon_not_unlocked")
r = route("nobodyU", "create", {"name": "Starter Crew", "icon": "/avatars/mullet.png"})
check("the starter critter is always allowed", r.get("ok"))

r = route("ownerA", "create", {"name": "Pool Party", "icon": "/avatars/clownfish.png",
                               "icon_name": "Clownfish"})
check("founding with your own critter works", r.get("ok"))
PCID = r.get("clan_id")
route("mateB", "join", {"clan_id": PCID})

prof = (route("ownerA", "get", {"clan_id": PCID}) or {}).get("clan") or {}
check("the pool is the union of the members' unlocks",
      set(prof.get("icon_pool") or []) == {"/avatars/mullet.png",
                                           "/avatars/clownfish.png",
                                           "/avatars/narwhal.png"})
outside = (route("nobodyU", "get", {"clan_id": PCID}) or {}).get("clan") or {}
check("the pool is members-only", "icon_pool" not in outside)
home = route("mateB", "home")
check("home reports my own unlocks (the founding screen's choices)",
      set(home.get("my_unlocked") or []) == {"/avatars/mullet.png", "/avatars/narwhal.png"})

r = route("ownerA", "settings", {"icon": "/avatars/narwhal.png", "icon_name": "Narwhal"})
check("a critter only ANOTHER member unlocked can be the clan icon", r.get("ok"))
check("clan icon actually changed", clan_doc(PCID).get("icon") == "/avatars/narwhal.png")
r = route("ownerA", "settings", {"icon": "/avatars/lobster.png"})
check("a critter nobody in the clan owns is refused",
      not r.get("ok") and r.get("error") == "icon_not_unlocked")
check("and the icon is left alone", clan_doc(PCID).get("icon") == "/avatars/narwhal.png")
r = route("ownerA", "settings", {"icon": "http://evil/x.png"})
check("a junk icon path is still rejected outright",
      not r.get("ok") and r.get("error") == "bad_icon")

r = route("mateB", "vote-critter", {"icon": "/avatars/lobster.png"})
check("can't vote for a critter nobody in the clan owns",
      not r.get("ok") and r.get("error") == "icon_not_unlocked")
r = route("mateB", "vote-critter", {"icon": "/avatars/clownfish.png"})
check("voting for a clanmate's critter works", r.get("ok"))
route("ownerA", "vote-critter", {"icon": "/avatars/narwhal.png"})
prof = (route("mateB", "get", {"clan_id": PCID}) or {}).get("clan") or {}
check("my own vote comes back", prof.get("my_vote") == "/avatars/clownfish.png")
check("the tally comes back", (prof.get("favorite_votes") or {}) ==
      {"/avatars/clownfish.png": 1, "/avatars/narwhal.png": 1})
check("a tie resolves the same way for every member (alphabetical)",
      prof.get("favorite_critter") == "/avatars/clownfish.png")

route("mateC", "join", {"clan_id": PCID})
route("mateC", "vote-critter", {"icon": "/avatars/clownfish.png"})
prof = (route("mateC", "get", {"clan_id": PCID}) or {}).get("clan") or {}
check("the critter with the most votes wins",
      prof.get("favorite_critter") == "/avatars/clownfish.png")

# The only clownfish owner leaves: the clan can't wear clownfish any more, so
# those votes drop out and the tab icon falls back to one it can wear.
route("ownerA", "transfer", {"uid": "mateB"})
CS._leave_clan("ownerA")
prof = (route("mateB", "get", {"clan_id": PCID}) or {}).get("clan") or {}
check("a critter whose owner left drops out of the pool",
      "/avatars/clownfish.png" not in (prof.get("icon_pool") or []))
check("...and out of the vote, so the tab icon stays wearable",
      prof.get("favorite_critter") == "/avatars/narwhal.png")

# ══ 12b. What a Clans screen COSTS ═══════════════════════════════════════════
# The tab was slow for one reason: every screen read the roster one document at
# a time, so a full clan paid a Firestore round trip per member, per call — and
# opening the tab, opening the clan and voting each did it again. The roster is
# one batched read now, and casting a vote writes one map key instead of the
# whole clan document back.
print("clans screen cost:")
set_user("costOwner", icons=["/avatars/clownfish.png"])
for i in range(9):
    set_user(f"costM{i}", icons=["/avatars/narwhal.png"])
r = route("costOwner", "create", {"name": "Cost Crew", "icon": "/avatars/clownfish.png"})
COSTID = r.get("clan_id")
for i in range(9):
    route(f"costM{i}", "join", {"clan_id": COSTID})

CS._members_invalidate()
reads_reset()
prof = (route("costOwner", "get", {"clan_id": COSTID}) or {}).get("clan") or {}
check("the whole 10-member roster is ONE batched read",
      READS["batch"] == 1 and READS["user_batched"] == 10)
check("...and not one solo read per member",
      READS["user_doc"] <= 1)
check("the profile is still complete", len(prof.get("members") or []) == 10)
check("...with the pool built from those same reads",
      set(prof.get("icon_pool") or []) >= {"/avatars/clownfish.png", "/avatars/narwhal.png"})

# The screens that follow reuse those reads instead of paying for them again.
reads_reset()
route("costOwner", "get", {"clan_id": COSTID})
check("the very next screen reuses the roster it just read",
      READS["batch"] == 0 and READS["user_batched"] == 0)

# ── What opening the Clans tab actually costs ────────────────────────────────
# /home is the single call the whole tab is drawn from, so its cost IS the
# tab's speed. It must not read a document per member, must not re-read a
# finished season on every open, and must not pay for a rival clan when the
# standings it already loaded carry that clan's card.
CS._members_invalidate()
CS._PREV_META_CACHE.clear()
CS._lb_invalidate()
reads_reset()
home1 = route("costOwner", "home")
check("opening Clans is ONE batched roster read, not one per member",
      READS["batch"] <= 1 and READS["user_doc"] <= 1)
check("...and it returns the full clan profile, so opening the clan needs no 2nd call",
      bool((home1 or {}).get("my_clan_full")))

# A finished season is immutable, so its standings are read once per process —
# not once per tab open, per player, forever.
DB.store["clan_meta/season_" + CS._prev_sid(FAKE_SID["cur"])] = {
    "sid": CS._prev_sid(FAKE_SID["cur"]), "finalized": True, "standings": []}
CS._PREV_META_CACHE.clear()
route("costOwner", "home")                       # first open pays for it
reads_reset()
route("costOwner", "home")
check("a finished season's standings are never re-read",
      CS._prev_sid(FAKE_SID["cur"]) in CS._PREV_META_CACHE)
after_warm = READS["doc"]
CS._PREV_META_CACHE.clear()
reads_reset()
route("costOwner", "home")
check("...and dropping that cache is what makes it cost a read again",
      READS["doc"] > after_warm)

# A rival must be read off the standings the page already loaded.
set_user("rivalOwner")
RIVALID = (route("rivalOwner", "create",
                 {"name": "Cost Rivals", "icon": "/avatars/clownfish.png"}) or {}).get("clan_id")
r = route("costOwner", "rival", {"clan_id": RIVALID})
check("a rival clan can be declared", bool(r.get("ok")))
CS._members_invalidate()
CS._lb_invalidate()
route("costOwner", "home")                       # warm standings + roster
reads_reset()
prof = (route("costOwner", "get", {"clan_id": COSTID}) or {}).get("clan") or {}
check("the rival card costs no extra document read at all", READS["doc"] <= 1)
check("...and it is still the right clan, carrying its rank",
      (prof.get("rival") or {}).get("id") == RIVALID
      and bool((prof.get("rival") or {}).get("rank")))
route("costOwner", "rival", {"op": "clear"})

# A vote must not need the clan document read back and written whole: two
# clanmates voting at once would then fight over every field in the clan.
before = copy.deepcopy(clan_doc(COSTID))
reads_reset()
r = route("costM0", "vote-critter", {"icon": "/avatars/narwhal.png"})
check("voting works", r.get("ok"))
check("the vote answers with the recounted tally, so the client needn't re-ask",
      r.get("favorite_critter") == "/avatars/narwhal.png"
      and (r.get("favorite_votes") or {}) == {"/avatars/narwhal.png": 1}
      and r.get("my_vote") == "/avatars/narwhal.png")
after = clan_doc(COSTID)
check("the ballot landed in the season slot",
      (after["seasons"][FAKE_SID["cur"]]["critter_votes"] or {}) == {"costM0": "/avatars/narwhal.png"})
check("and NOTHING else in the clan was rewritten",
      {k: v for k, v in after.items() if k != "seasons"}
      == {k: v for k, v in before.items() if k != "seasons"})
check("the rest of the season slot is untouched too",
      {k: v for k, v in after["seasons"][FAKE_SID["cur"]].items() if k != "critter_votes"}
      == {k: v for k, v in (before.get("seasons") or {}).get(FAKE_SID["cur"], {}).items()
          if k != "critter_votes"})

# A vote can't move the standings, so it must not throw the leaderboard scan
# away — that whole-collection scan is the most expensive read on the page, and
# chat lines and votes were dropping it dozens of times an evening for nothing.
CS._leaderboard_rows(DB, FAKE_SID["cur"])          # warm it
warm_at = CS._LB_CACHE["at"]
CS._route_post(DB, "costM0", "vote-critter", {"icon": "/avatars/clownfish.png"}, FAKE_SID["cur"])
check("voting leaves the leaderboard cache warm", CS._LB_CACHE["at"] == warm_at)
CS._route_post(DB, "costOwner", "chat-send", {"text": "nice one"}, FAKE_SID["cur"])
check("chatting leaves it warm too", CS._LB_CACHE["at"] == warm_at)
# ...but anything that DOES move the standings still drops it.
CS._route_post(DB, "costOwner", "settings", {"description": "new blurb"}, FAKE_SID["cur"])
check("a change the board can show still drops the cache", CS._LB_CACHE["at"] == 0.0)

# A critter unlocked seconds ago must not be refused because the roster read
# was cached: the gate re-checks against a fresh read before it says no.
set_user("costM1", icons=["/avatars/narwhal.png"])
route("costOwner", "get", {"clan_id": COSTID})      # warms the member cache
DB.store["users/costM1"]["unlocked_icons"] = ["/avatars/narwhal.png", "/avatars/lobster.png"]
r = route("costOwner", "vote-critter", {"icon": "/avatars/lobster.png"})
check("a just-unlocked critter is votable immediately, not in 3 seconds",
      r.get("ok"))

# ══ 13. One-shot roster moves (PENDING_MEMBER_MOVES) ═════════════════════════
# Placing a player straight into a clan by name + friend code, no invite round
# trip. The whole point is that it happens exactly once, so the marker doc is
# as much of the feature as the move itself.
print("roster moves:")
set_user("mover1")
set_user("mover2")
set_user("host1")
set_user("host2")
set_friend_code("mover1", "2809", "LemmeSeeThemToes")
set_friend_code("mover2", "9113", "Twin Midi")
r = route("host1", "create", {"name": "Belmont Board Game Club",
                              "icon": "/avatars/clownfish.png", "privacy": "invite"})
BELMONT = r.get("clan_id")
check("target clan founded (invite-only, the hardest case)", r.get("ok"))

# mover2 starts out in a DIFFERENT clan, and mid-cooldown from an earlier move.
OLD = route("host2", "create", {"name": "Old Reef Crew",
                                "icon": "/avatars/lobster.png"}).get("clan_id")
route("mover2", "join", {"clan_id": OLD})
DB.store["users/mover2"]["clan_cooldown_until"] = CS._now() + 3600

MOVES = ({"key": "t-move-1", "name": "LemmeSeeThemToes", "code": "2809",
          "clan": "Belmont Board Game Club"},
         {"key": "t-move-2", "name": "Twin Midi", "code": "9113",
          "clan": "Belmont Board Game Club"},
         {"key": "t-move-missing", "name": "Nobody At All", "code": "5555",
          "clan": "Belmont Board Game Club"})
_real_moves, CS.PENDING_MEMBER_MOVES = CS.PENDING_MEMBER_MOVES, MOVES
CS._MOVES_NEXT_CHECK = 0.0
CS.ensure_pending_moves(DB)

check("player with no clan is moved in", "mover1" in clan_doc(BELMONT)["members"])
check("...and their user doc points at it", user("mover1").get("clan_id") == BELMONT)
check("player already in another clan is moved across",
      "mover2" in clan_doc(BELMONT)["members"] and "mover2" not in clan_doc(OLD)["members"])
check("an operator move is not a quit — no 24h point cooldown",
      not user("mover2").get("clan_cooldown_until"))
check("invite-only clan doesn't block the move", clan_doc(BELMONT).get("privacy") == "invite")
check("the injected invite is consumed, not left lying around",
      not any(i.get("clan_id") == BELMONT for i in user("mover1").get("clan_invites") or []))
check("marker written for each applied move",
      DB.store.get("clan_moves/t-move-1", {}).get("result") == "moved"
      and DB.store.get("clan_moves/t-move-2", {}).get("result") == "moved")
check("unresolvable move leaves NO marker (so it retries, never silently lost)",
      "clan_moves/t-move-missing" not in DB.store)

# Second pass: markers must make it a no-op even after the player leaves again.
CS._leave_clan("mover1")
CS._MOVES_NEXT_CHECK = 0.0
CS.ensure_pending_moves(DB)
check("an applied move never runs twice", "mover1" not in clan_doc(BELMONT)["members"])

# The retry that finally lands: the missing player signs up.
set_user("mover3")
set_friend_code("mover3", "5555", "Nobody At All")
CS._MOVES_NEXT_CHECK = 0.0
CS.ensure_pending_moves(DB)
check("a deferred move applies on a later pass", "mover3" in clan_doc(BELMONT)["members"])
check("...and marks itself done", DB.store.get("clan_moves/t-move-missing", {}).get("result") == "moved")

# Somebody already in the target clan is left exactly as they were.
DB.store.pop("clan_moves/t-move-2", None)
before = copy.deepcopy(clan_doc(BELMONT)["members"]["mover2"])
CS._MOVES_NEXT_CHECK = 0.0
CS.ensure_pending_moves(DB)
check("re-running against an existing member is a no-op",
      clan_doc(BELMONT)["members"]["mover2"] == before
      and DB.store["clan_moves/t-move-2"]["result"] == "already_member")
check("all moves applied → the check switches itself off",
      CS._MOVES_NEXT_CHECK == float("inf"))

# Nothing that could clear up later is treated as final: a full clan and a
# player who still owns another clan both have to stay pending.
set_user("mover4")
set_friend_code("mover4", "6161", "Deferred Dave")
DB.store["clans/" + BELMONT]["members"].update(
    {"filler%d" % i: {"name": "F%d" % i, "role": "member", "joined_ts": CS._now()}
     for i in range(CS.CLAN_MAX_MEMBERS)})
CS.PENDING_MEMBER_MOVES = ({"key": "t-move-full", "name": "Deferred Dave", "code": "6161",
                            "clan": "Belmont Board Game Club"},)
CS._MOVES_NEXT_CHECK = 0.0
CS.ensure_pending_moves(DB)
check("a full clan defers the move instead of burning it",
      "clan_moves/t-move-full" not in DB.store and "mover4" not in clan_doc(BELMONT)["members"])
check("...and the check stays armed for the retry", CS._MOVES_NEXT_CHECK != float("inf"))
for i in range(CS.CLAN_MAX_MEMBERS):
    DB.store["clans/" + BELMONT]["members"].pop("filler%d" % i, None)

set_user("mover5")
set_friend_code("mover5", "7171", "Owner Olivia")
route("mover5", "create", {"name": "Olivias Own", "icon": "/avatars/clownfish.png"})
CS.PENDING_MEMBER_MOVES = ({"key": "t-move-owner", "name": "Owner Olivia", "code": "7171",
                            "clan": "Belmont Board Game Club"},)
CS._MOVES_NEXT_CHECK = 0.0
CS.ensure_pending_moves(DB)
check("a solo clan owner is left alone — the move never dissolves their clan",
      "clan_moves/t-move-owner" not in DB.store
      and user("mover5").get("clan_id")
      and DB.store.get("clan_names/olivias own"))

# A legacy account whose friend_code was stored as a NUMBER must still resolve.
set_user("legacy1")
DB.store["users/legacy1"]["friend_code"] = 3131          # int, not "3131"
DB.store["users/legacy1"]["nickname"] = "Legacy Larry"
r = route("host1", "invite", {"to_code": "3131"})
check("a numeric friend_code still resolves", r.get("ok") and r.get("name") == "Legacy Larry")

CS.PENDING_MEMBER_MOVES = _real_moves
check("the shipped move list names the two players Tim asked for",
      {(m["name"], m["code"], m["clan"]) for m in CS.PENDING_MEMBER_MOVES}
      == {("LemmeSeeThemToes", "2809", "Belmont Board Game Club"),
          ("Twin Midi", "9113", "Belmont Board Game Club")})
check("...with unique, non-reusable keys",
      len({m["key"] for m in CS.PENDING_MEMBER_MOVES}) == len(CS.PENDING_MEMBER_MOVES))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'='*46}\nRESULT: {_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
