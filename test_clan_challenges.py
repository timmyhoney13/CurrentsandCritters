"""Clan challenges, every single one, line by line, against the real server.

There are 51 clan challenges (25 weekly + 26 season) and every one of them is
checked here twice over:

  • YOU GET IT BY DOING WHAT IT SAYS: drive the exact thing the challenge
    describes, one step short of its target (must NOT be done), then take the
    last step (must be done, and its Clan Points must have landed).
  • YOU NEVER GET ONE BY ACCIDENT, after each driver, the set of completed
    challenges must equal an explicitly listed expected set. Anything that
    fires without being earned fails the run, and so does anything that was
    genuinely earned but silently skipped.

Everything runs through the REAL clan_server against an in-memory Firestore
fake and a sandboxed temp dir for game records (same harness as
test_clan_server.py, never touches the tree). Nothing is stubbed on the way
in: the counters are moved by claim_game_points reading a game record with the
same shape multiplayer_server writes, by the real trade hook, and by the real
event / join / rival routes.

Run:  python3 test_clan_challenges.py
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
        self._data, self.id = data, doc_id
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


class FakeDoc:
    def __init__(self, store, path):
        self._store, self._path = store, path
    def get(self, transaction=None, _batched=False):
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
        self._store, self._path = store, path
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
        return iter([r.get(_batched=True) for r in refs])
    def transaction(self):
        return FakeTxn(self.store)


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(os.path.dirname(__file__), fname))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load("mpsrv_for_clanch", "multiplayer_server.py")
CS = _load("clan_server_ch", "clan_server.py")

DB = FakeDB()
TMP = tempfile.mkdtemp(prefix="clan_chal_")
HIST = os.path.join(TMP, "hist"); os.makedirs(HIST)
COMP = os.path.join(TMP, "comp"); os.makedirs(COMP)
SID = "2026-Q3"

# ── Controllable clock ───────────────────────────────────────────────────────
# Several challenges are about WHEN you played ("on 5 different days", "every
# week of the month"), so the suite owns the clock. Monday 2026-07-06 12:00 UTC
# sits mid-quarter, so a whole ISO week and a whole month are reachable.
CLOCK = {"t": 1783339200}          # 2026-07-06 (Mon) 12:00 UTC
CS._now = lambda: int(CLOCK["t"])
DAY = 86400


def at(ts):
    CLOCK["t"] = int(ts)


def _find_uid_by_username(db, uname):
    uname = str(uname or "").strip().lower()
    for key, doc in db.store.items():
        if key.startswith("users/") and key.count("/") == 1 and isinstance(doc, dict):
            if uname and str(doc.get("usernameLower") or "").lower() == uname:
                return key.split("/", 1)[1]
    return None


CS.init(
    get_firestore=lambda: DB,
    verify_token=lambda t: {"uid": t} if t else None,
    find_uid_by_username=_find_uid_by_username,
    level_progress=M._level_progress_for_total_xp,
    get_season_id=lambda ts=None: SID,
    games_history_dir=HIST,
    competitive_games_dir=COMP,
    prof_strong_re=M._PROF_STRONG_RE,
    prof_word_re=M._PROF_WORD_RE,
    prof_leet=M._PROF_LEET,
    prof_strong=M._PROF_STRONG,
    prof_words=M._PROF_WORDS,
)
# The season is never "over" during these tests, so the lazy finalize can never
# fire mid-scenario and rewrite the very counters under test.
CS._FINALIZED_SIDS.add(CS._prev_sid(SID))

_PASS = _FAIL = 0
_FAILED = []


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        _FAILED.append(name)
        print(f"  ✗ FAIL: {name}" + (f"  → {detail}" if detail else ""))


# ── World helpers ────────────────────────────────────────────────────────────
_SEQ = {"n": 0}


def nid(prefix):
    _SEQ["n"] += 1
    return f"{prefix}{_SEQ['n']}"


def set_user(uid, nick, *, clan_id="", achievements=None, rank=""):
    DB.store["users/" + uid] = {
        "nickname": nick, "username": nick, "usernameLower": nick.lower(),
        "stats": {"critter_coins": 0, "total_xp": 100,
                  **({"rank_competitive": rank} if rank else {})},
        "avatar_url": "/avatars/clownfish.png",
        "unlocked_icons": ["/avatars/clownfish.png"],
        "achievements": achievements or {},
        "online": True, "last_active": 4102444800,
    }
    if clan_id:
        DB.store["users/" + uid]["clan_id"] = clan_id
    CS._members_invalidate()
    CS._REG_CACHE.clear()


def new_clan(n_members=1, *, name=None, rank=""):
    """A brand-new clan with n_members, each a registered account. Every
    scenario gets its own, so no counter can leak between challenges."""
    cid = nid("clan")
    uids, members = [], {}
    for i in range(n_members):
        uid = nid("u")
        nick = "P" + uid[1:]
        set_user(uid, nick, clan_id=cid, rank=rank)
        uids.append(uid)
        members[uid] = {"name": nick, "role": "owner" if i == 0 else "member",
                        "custom_role_id": None, "joined_ts": CS._now(),
                        "avatar": "/avatars/clownfish.png"}
    DB.store["clans/" + cid] = {
        "name": "Clan " + cid, "icon": "/avatars/clownfish.png",
        "privacy": "public", "owner_uid": uids[0], "members": members,
        "created_ts": CS._now(), "seasons": {}, "activity": [], "xp": 0,
    }
    DB.store["clan_names/" + ("clan " + cid)] = {"clan_id": cid}
    CS._members_invalidate()
    CS._REG_CACHE.clear()
    CS._lb_invalidate()
    return cid, uids


def nick(uid):
    return (DB.store.get("users/" + uid) or {}).get("nickname") or ""


def slot_of(cid):
    return ((DB.store.get("clans/" + cid) or {}).get("seasons") or {}).get(SID) or {}


def weekly_of(cid):
    return (DB.store.get("clans/" + cid) or {}).get("weekly") or {}


def done_set(cid):
    """Every challenge id this clan has completed, weekly + season."""
    return set(weekly_of(cid).get("challenges_done") or []) | set(slot_of(cid).get("challenges_done") or [])


def points(cid):
    return CS._num(slot_of(cid).get("points"))


# ── Game drivers ─────────────────────────────────────────────────────────────
STAT_KEYS = ("oceans_played", "animals_played", "moves", "stars", "star_chains",
             "pool_draws", "deck_draws")


def stats(**kw):
    row = {k: 0 for k in STAT_KEYS}
    row.update({"first_ocean": "", "gobies": 0, "max_ceph_turn": 0,
                "said_gg": False, "behind_at_endgame": False})
    row.update(kw)
    return row


def casual(uid, *, place=1, opponents=None, guests=(), bots=(), team=False,
           cs=None, player_count=None, all_scores=None):
    """Write a finished casual game the way multiplayer_server does, then claim
    it. `opponents` are registered accounts (uid or bare name), `guests` are
    real people with no account, `bots` are AI seats."""
    room = nid("R").upper()
    me = nick(uid)
    opp_names = []
    for o in (opponents or []):
        opp_names.append(nick(o) if o in DB.store.get("users/" + str(o), {}) or ("users/" + str(o)) in DB.store else str(o))
    opp_names += [str(g) for g in guests]
    humans = [me] + opp_names
    bot_names = [str(b) for b in bots]
    n = player_count or (len(humans) + len(bot_names))
    # Standings: put me exactly at `place`, everyone else around me.
    others = [x for x in humans + bot_names if x != me]
    order = others[:place - 1] + [me] + others[place - 1:]
    scores = all_scores or [100 - 5 * i for i in range(len(order))]
    standings = [{"name": nm, "score": scores[i], "seat_index": i}
                 for i, nm in enumerate(order)]
    players = ([{"name": me, "is_human": True, "seat_index": 0,
                 "clan_stats": dict(cs or stats(), name=me, seat_index=0)}]
               + [{"name": nm, "is_human": True, "seat_index": i + 1, "clan_stats": {}}
                  for i, nm in enumerate(opp_names)]
               + [{"name": nm, "is_human": False, "seat_index": len(opp_names) + i + 1,
                   "clan_stats": {}} for i, nm in enumerate(bot_names)])
    rec = {"room_id": room, "recorded_unix": CS._now(), "mode": "standard",
           "player_count": n, "human_count": len(humans),
           "winner": standings[0]["name"], "standings": standings,
           "players": players, "team_mode": bool(team),
           "team_count": 2 if team else 0}
    with open(os.path.join(HIST, f"game_{room}_{CS._now()}.json"), "w") as f:
        json.dump(rec, f)
    return CS.claim_game_points(uid, room)


def comp(uid, *, won=True, opp=None, my_best=100, my_second=70, opp_best=80,
         cs=None, is_draw=False):
    """A finished competitive 1v1 (four seats, two per player) + claim."""
    room = nid("R").upper()
    me = nick(uid)
    opp_name = nick(opp) if opp and ("users/" + str(opp)) in DB.store else (opp or nid("Opp"))
    if ("users/" + str(opp or "")) not in DB.store and opp is None:
        # A registered opponent is required for competitive points; make one.
        ouid = nid("o")
        set_user(ouid, opp_name)
    opp_second = max(0, opp_best - 10)
    crec = {"room_id": room, "recorded_unix": CS._now(), "season_id": SID,
            "p1_name": me, "p2_name": opp_name,
            "p1_best_score": my_best, "p2_best_score": opp_best,
            "p1_second_score": my_second, "p2_second_score": opp_second,
            "winner": (me if won else opp_name) if not is_draw else None,
            "is_draw": is_draw}
    with open(os.path.join(COMP, f"game_{room}_{CS._now()}.json"), "w") as f:
        json.dump(crec, f)
    half = dict(cs or stats())
    # A player owns TWO seats; split the telemetry across them so the test also
    # proves the pair is summed rather than read off one hand.
    a = {k: (half[k] // 2 if isinstance(half.get(k), int) else half.get(k)) for k in half}
    b = {k: (half[k] - a[k] if isinstance(half.get(k), int) and k in STAT_KEYS else
             (half.get(k) if k not in STAT_KEYS else 0)) for k in half}
    players = [
        {"name": me + " A", "is_human": True, "seat_index": 0, "clan_stats": dict(a, seat_index=0)},
        {"name": me + " B", "is_human": True, "seat_index": 1, "clan_stats": dict(b, seat_index=1)},
        {"name": opp_name + " A", "is_human": True, "seat_index": 2, "clan_stats": {}},
        {"name": opp_name + " B", "is_human": True, "seat_index": 3, "clan_stats": {}},
    ]
    rec = {"room_id": room, "recorded_unix": CS._now(), "mode": "competitive",
           "player_count": 4, "human_count": 4, "winner": me,
           "standings": [{"name": p["name"], "score": 10, "seat_index": i}
                         for i, p in enumerate(players)],
           "players": players, "team_mode": False, "team_count": 0}
    with open(os.path.join(HIST, f"game_{room}_{CS._now()}.json"), "w") as f:
        json.dump(rec, f)
    return CS.claim_game_points(uid, room)


def trade(uid_a, uid_b, *, tag=None):
    """One completed clan trade through the real hook."""
    t = tag or nid("t")
    return CS.on_trade_completed(DB, {
        "participants": [uid_a, uid_b],
        "offers": {uid_a: {"coins": 10, "avatars": ["/avatars/a" + t + ".png"], "backgrounds": []},
                   uid_b: {"coins": 0, "avatars": [], "backgrounds": ["bg" + t]}},
    })


# ── The scenario runner ──────────────────────────────────────────────────────
# Each challenge gets: drive one step short → must NOT be done; take the last
# step → must be done, its points must have landed, and the FULL set of
# completed challenges must be exactly what we expected.
def scenario(cid, label, *, expect, short_of=None, last_step=None, target_id=None):
    if short_of is not None:
        short_of()
        got = done_set(cid)
        check(f"{label}: not complete one step short", target_id not in got,
              f"done={sorted(got)}")
    before = points(cid)
    if last_step is not None:
        last_step()
    got = done_set(cid)
    ch = _by_id(target_id)
    check(f"{label}: completes when you do what it says", target_id in got,
          f"done={sorted(got)}")
    check(f"{label}: paid its {ch.get('clan_points')} Clan Points",
          points(cid) >= before + CS._num(ch.get("clan_points")),
          f"{before} → {points(cid)}")
    check(f"{label}: nothing else fired by accident", got == set(expect),
          f"unexpected={sorted(got - set(expect))} missing={sorted(set(expect) - got)}")


_ALL = {c["id"]: c for c in (CS.CLAN_WEEKLY_CHALLENGES + CS.CLAN_SEASON_CHALLENGES)}


def _by_id(cid):
    return _ALL.get(cid) or {}


print("═" * 62)
print("CLAN CHALLENGES, every challenge, line by line")
print("═" * 62)

# ══ 0. Table sanity ══════════════════════════════════════════════════════════
print("\ntables:")
ids = [c["id"] for c in CS.CLAN_WEEKLY_CHALLENGES] + [c["id"] for c in CS.CLAN_SEASON_CHALLENGES]
check("every challenge id is unique", len(ids) == len(set(ids)))
check("25 weekly challenges", len(CS.CLAN_WEEKLY_CHALLENGES) == 25, str(len(CS.CLAN_WEEKLY_CHALLENGES)))
check("26 season challenges", len(CS.CLAN_SEASON_CHALLENGES) == 26, str(len(CS.CLAN_SEASON_CHALLENGES)))
check("every challenge has a name, description, target and reward",
      all(c.get("name") and c.get("desc") and c.get("target") and c.get("clan_points")
          for c in CS.CLAN_WEEKLY_CHALLENGES + CS.CLAN_SEASON_CHALLENGES))
_empty_clan = {"members": {"a": {}, "b": {}}}
_w, _s = CS._weekly_slot({}), CS._season_slot({}, SID)
check("no challenge is complete on an empty clan",
      not any(CS._num(CS._challenge_metric(_w, c["metric"])) >= CS._challenge_target(c, _empty_clan)
              for c in CS.CLAN_WEEKLY_CHALLENGES)
      and not any(CS._num(CS._season_metric(_s, c["metric"])) >= CS._challenge_target(c, _empty_clan)
                  for c in CS.CLAN_SEASON_CHALLENGES))
check("an unknown metric can never complete a challenge",
      CS._num(CS._challenge_metric(_w, "not_a_real_metric")) == 0
      and CS._num(CS._season_metric(_s, "not_a_real_metric")) == 0)

# ══ WEEKLY 1-2: Full Crew / All Hands on Deck ════════════════════════════════
print("\nweekly: participation:")
cid, us = new_clan(10)
def _play_n(members):
    def go():
        for u in members:
            casual(u, place=2, bots=["Bot"])      # last place vs a bot: 0 points
    return go
scenario(cid, "Full Crew (5 different members complete a game)",
         target_id="w_full_crew", expect={"w_full_crew"},
         short_of=_play_n(us[:4]), last_step=_play_n(us[4:5]))
scenario(cid, "All Hands on Deck (10 different members)",
         target_id="w_all_hands", expect={"w_full_crew", "w_all_hands"},
         short_of=_play_n(us[5:9]), last_step=_play_n(us[9:10]))
check("Full Crew counts PLAYERS, not games",
      len(weekly_of(cid).get("players") or {}) == 10 and weekly_of(cid).get("games") == 10)

# One member playing ten games is NOT ten members.
cid, us = new_clan(3)
for _ in range(10):
    casual(us[0], place=2, bots=["Bot"])
check("ten games by ONE member never completes Full Crew",
      "w_full_crew" not in done_set(cid), str(sorted(done_set(cid))))

# ══ WEEKLY 3-4: Daily Divers / Six-Seven ═════════════════════════════════════
print("\nweekly: days played:")
cid, us = new_clan(1)
BASE = 1783339200                          # Monday 12:00 UTC
def _days(lo, hi):
    def go():
        for d in range(lo, hi):
            at(BASE + d * DAY)
            casual(us[0], place=2, bots=["Bot"])
    return go
scenario(cid, "Daily Divers (a game on 5 different days)",
         target_id="w_daily_divers", expect={"w_daily_divers"},
         short_of=_days(0, 4), last_step=_days(4, 5))
scenario(cid, "Six/Seven (a game on 6 of the 7 days)",
         target_id="w_six_seven", expect={"w_daily_divers", "w_six_seven"},
         short_of=None, last_step=_days(5, 6))
check("Six/Seven does not need the days to be in a row",
      sorted((weekly_of(cid).get("days") or {}).keys()) ==
      ["2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10", "2026-07-11"],
      str(sorted((weekly_of(cid).get("days") or {}).keys())))
# Two games on ONE day is one day.
cid, us = new_clan(1)
at(BASE)
casual(us[0], place=2, bots=["Bot"]); casual(us[0], place=2, bots=["Bot"])
check("two games on the same day count as one day",
      len(weekly_of(cid).get("days") or {}) == 1)
at(BASE)

# ══ WEEKLY 5: Double Handed ══════════════════════════════════════════════════
print("\nweekly: competitive:")
cid, us = new_clan(1)
def _comp_n(n, **kw):
    return lambda: [comp(us[0], **kw) for _ in range(n)]
scenario(cid, "Double Handed (10 competitive games)",
         target_id="w_double_handed", expect={"w_double_handed"},
         short_of=_comp_n(9, won=False, opp_best=200, my_best=50, my_second=40),
         last_step=_comp_n(1, won=False, opp_best=200, my_best=50, my_second=40))
check("casual games never count toward Double Handed",
      weekly_of(cid).get("comp_games") == 10 and weekly_of(cid).get("games") == 10)

# ══ WEEKLY 12/13: Competitive Current, Winning Waters ════════════════════════
cid, us = new_clan(5)
scenario(cid, "Competitive Current (win 10 competitive matches)",
         target_id="w_competitive_current",
         expect={"w_competitive_current", "w_double_handed", "w_winning_streak"},
         short_of=lambda: [comp(us[0], won=True) for _ in range(9)],
         last_step=lambda: comp(us[0], won=True))
scenario(cid, "Winning Waters (5 different members win a competitive match)",
         target_id="w_winning_waters",
         expect={"w_competitive_current", "w_double_handed", "w_winning_streak",
                 "w_winning_waters", "w_full_crew"},
         short_of=lambda: [comp(u, won=True) for u in us[1:4]],
         last_step=lambda: comp(us[4], won=True))

# One member winning fifteen matches is not five members.
cid, us = new_clan(5)
for _ in range(15):
    comp(us[0], won=True)
check("15 wins by ONE member never completes Winning Waters",
      "w_winning_waters" not in done_set(cid) and "w_competitive_current" in done_set(cid))

# ══ WEEKLY 14: Winning Streak ════════════════════════════════════════════════
cid, us = new_clan(2)
scenario(cid, "Winning Streak (two three-game competitive streaks)",
         target_id="w_winning_streak", expect={"w_winning_streak"},
         short_of=lambda: [comp(us[0], won=True) for _ in range(3)],
         last_step=lambda: [comp(us[0], won=True) for _ in range(3)])
check("a streak of 3 counts once, not once per win",
      weekly_of(cid).get("comp_streak3") == 2, str(weekly_of(cid).get("comp_streak3")))
# A loss breaks the run.
cid, us = new_clan(2)
comp(us[0], won=True); comp(us[0], won=True)
comp(us[0], won=False, opp_best=300, my_best=10, my_second=5)
comp(us[0], won=True)
check("a loss resets the clan streak", weekly_of(cid).get("comp_streak") == 1
      and weekly_of(cid).get("comp_streak3") == 0)

# ══ WEEKLY 16/17: Double Trouble, Dominant Depths ════════════════════════════
print("\nweekly: two-handed competitive:")
cid, us = new_clan(1)
# Both hands beating their best, but NOT doubling it: Dominant only.
scenario(cid, "Dominant Depths (5 wins with both hands over their best)",
         target_id="w_dominant_depths",
         expect={"w_dominant_depths"},
         short_of=lambda: [comp(us[0], won=True, my_best=110, my_second=101, opp_best=100)
                           for _ in range(4)],
         last_step=lambda: comp(us[0], won=True, my_best=110, my_second=101, opp_best=100))
check("beating them by a nose is NOT doubling them",
      "w_double_trouble" not in done_set(cid))

cid, us = new_clan(1)
scenario(cid, "Double Trouble (both hands double their highest hand)",
         target_id="w_double_trouble", expect={"w_double_trouble"},
         short_of=None,
         last_step=lambda: comp(us[0], won=True, my_best=210, my_second=200, opp_best=100))
# Only ONE hand doubling is not enough.
cid, us = new_clan(1)
comp(us[0], won=True, my_best=210, my_second=150, opp_best=100)
check("one hand doubling them is not enough for Double Trouble",
      "w_double_trouble" not in done_set(cid))
check("...but it is still a Dominant win", weekly_of(cid).get("dominant_wins") == 1)
# Losing with two big hands is not a Dominant WIN.
cid, us = new_clan(1)
comp(us[0], won=False, my_best=210, my_second=200, opp_best=100)
check("Dominant Depths needs the WIN, not just the scores",
      weekly_of(cid).get("dominant_wins") == 0)
check("...while Double Trouble only asks for the two hands",
      weekly_of(cid).get("double_hands") == 1)

# ══ WEEKLY 6: Artificial Start ═══════════════════════════════════════════════
print("\nweekly, how you played:")
cid, us = new_clan(1)
AR = stats(first_ocean="Artificial Reef")
scenario(cid, "Artificial Start (Artificial Reef first in 4 games)",
         target_id="w_artificial_start", expect={"w_artificial_start"},
         short_of=lambda: [casual(us[0], place=2, bots=["Bot"], cs=AR) for _ in range(3)],
         last_step=lambda: casual(us[0], place=2, bots=["Bot"], cs=AR))
cid, us = new_clan(1)
for _ in range(6):
    casual(us[0], place=2, bots=["Bot"], cs=stats(first_ocean="Kelp Forest"))
check("a different first Ocean never counts for Artificial Start",
      weekly_of(cid).get("artificial_first") == 0)

# ══ WEEKLY 7: Casual Current ═════════════════════════════════════════════════
cid, us = new_clan(1)
scenario(cid, "Casual Current (finish first in 4 casual games)",
         target_id="w_casual_current", expect={"w_casual_current"},
         short_of=lambda: [casual(us[0], place=1, bots=["Bot"]) for _ in range(3)],
         last_step=lambda: casual(us[0], place=1, bots=["Bot"]))
cid, us = new_clan(1)
for _ in range(6):
    casual(us[0], place=2, bots=["Bot"])
check("second place never counts as a casual win", weekly_of(cid).get("casual_wins") == 0)

# ══ WEEKLY 8: Crowded Waters ═════════════════════════════════════════════════
cid, us = new_clan(1)
def _four_player(n):
    return lambda: [casual(us[0], place=2, bots=["B1", "B2", "B3"]) for _ in range(n)]
scenario(cid, "Crowded Waters (6 casual games with 4+ players)",
         target_id="w_crowded_waters", expect={"w_crowded_waters"},
         short_of=_four_player(5), last_step=_four_player(1))
cid, us = new_clan(1)
for _ in range(8):
    casual(us[0], place=2, bots=["B1", "B2"])     # 3 players
check("a 3-player game is not a Crowded Waters game", weekly_of(cid).get("casual_4p") == 0)

# ══ WEEKLY 9: Eight at Sea ═══════════════════════════════════════════════════
cid, us = new_clan(1)
REALS = []
for i in range(7):
    ru = nid("r"); set_user(ru, "Real" + ru); REALS.append(ru)
def _eight():
    return casual(us[0], place=2, opponents=REALS)
scenario(cid, "Eight at Sea (two 8-player games, all real people)",
         target_id="w_eight_at_sea", expect={"w_eight_at_sea"},
         short_of=_eight, last_step=_eight)
# One bot in the eight breaks "all real people".
cid, us = new_clan(1)
for _ in range(3):
    casual(us[0], place=2, opponents=REALS[:6], bots=["Bot"])
check("a single bot disqualifies Eight at Sea", weekly_of(cid).get("eight_all_human") == 0)
# A guest is a real person but not a real ACCOUNT.
cid, us = new_clan(1)
for _ in range(3):
    casual(us[0], place=2, opponents=REALS[:6], guests=["DriftingGuest"])
check("a guest seat disqualifies Eight at Sea too", weekly_of(cid).get("eight_all_human") == 0)
# Seven real people is not eight.
cid, us = new_clan(1)
for _ in range(3):
    casual(us[0], place=2, opponents=REALS[:6])
check("a 7-player game is not Eight at Sea", weekly_of(cid).get("eight_all_human") == 0)

# ══ WEEKLY 10: Friendly Competition ══════════════════════════════════════════
print("\nweekly: against another clan:")
other_cid, other_us = new_clan(2, name="Rivals")
cid, us = new_clan(3)
def _win_vs_other(members):
    return lambda: [casual(u, place=1, opponents=[other_us[0]]) for u in members]
scenario(cid, "Friendly Competition (3 members win vs another clan)",
         target_id="w_friendly_competition", expect={"w_friendly_competition"},
         short_of=_win_vs_other(us[:2]), last_step=_win_vs_other(us[2:3]))
# Beating a clanless player is not beating another clan.
cid, us = new_clan(3)
loner = nid("l"); set_user(loner, "Loner" + loner)
for u in us:
    casual(u, place=1, opponents=[loner])
check("beating a player with no clan is not 'against another clan'",
      len(weekly_of(cid).get("vs_clan_winners") or {}) == 0)
# Beating your OWN clanmate is not another clan either.
cid, us = new_clan(3)
for u in us:
    casual(u, place=1, opponents=[us[(us.index(u) + 1) % 3]])
check("beating your own clanmate is not 'against another clan'",
      len(weekly_of(cid).get("vs_clan_winners") or {}) == 0)
# Losing to another clan doesn't count.
cid, us = new_clan(3)
for u in us:
    casual(u, place=2, opponents=[other_us[0]])
check("Friendly Competition needs the WIN",
      len(weekly_of(cid).get("vs_clan_winners") or {}) == 0)

# ══ WEEKLY 11: Comeback Current ══════════════════════════════════════════════
print("\nweekly: comebacks:")
cid, us = new_clan(1)
BEHIND = stats(behind_at_endgame=True)
scenario(cid, "Comeback Current (win 2 you were behind in at End Game)",
         target_id="w_comeback_current", expect={"w_comeback_current"},
         short_of=lambda: casual(us[0], place=1, bots=["Bot"], cs=BEHIND),
         last_step=lambda: casual(us[0], place=1, bots=["Bot"], cs=BEHIND))
cid, us = new_clan(1)
for _ in range(4):
    casual(us[0], place=1, bots=["Bot"], cs=stats(behind_at_endgame=False))
check("leading the whole way is not a comeback", weekly_of(cid).get("comebacks") == 0)
cid, us = new_clan(1)
for _ in range(4):
    casual(us[0], place=2, bots=["Bot"], cs=BEHIND)
check("being behind and LOSING is not a comeback", weekly_of(cid).get("comebacks") == 0)

# ══ WEEKLY 15: Two of a Kind (Humuhumunukuapua'a) ════════════════════════════
cid, us = new_clan(3)
HUMU = stats(max_ceph_turn=5)
scenario(cid, "Two of a Kind (2 members play 5 Cephalopods in one turn)",
         target_id="w_humu_duo", expect={"w_humu_duo"},
         short_of=lambda: casual(us[0], place=2, bots=["Bot"], cs=HUMU),
         last_step=lambda: casual(us[1], place=2, bots=["Bot"], cs=HUMU))
cid, us = new_clan(3)
for _ in range(5):
    casual(us[0], place=2, bots=["Bot"], cs=HUMU)
check("one member doing it five times is still one member",
      len(weekly_of(cid).get("humu_members") or {}) == 1)
cid, us = new_clan(3)
for u in us:
    casual(u, place=2, bots=["Bot"], cs=stats(max_ceph_turn=4))
check("four Cephalopods in a turn is not the achievement",
      len(weekly_of(cid).get("humu_members") or {}) == 0)

# ══ WEEKLY 18-24: the play-telemetry challenges ══════════════════════════════
print("\nweekly: play telemetry:")
TELEMETRY = [
    ("w_ocean_architects", "oceans_played", 75, "Ocean Architects (play 75 Ocean cards)"),
    ("w_critter_collection", "animals_played", 125, "Critter Collection (play 125 animal cards)"),
    ("w_moving_tide", "moves", 20, "Moving Tide (move animals between Oceans 20 times)"),
    ("w_star_power", "stars", 75, "Star Power (activate 75 ★ abilities)"),
    ("w_chain_reaction", "star_chains", 25, "Chain Reaction (25 turns with chained ★s)"),
    ("w_pool_party", "pool_draws", 150, "Pool Party (draw 150 from the Pool)"),
    ("w_deep_draw", "deck_draws", 150, "Deep Draw (draw 150 from the Deck)"),
]
for chid, key, target, label in TELEMETRY:
    cid, us = new_clan(2)
    # Half from each of two members, so the test also proves the counter is the
    # whole CLAN's work and not one player's.
    a, b = target // 2, target - target // 2
    extra = set()
    if key == "animals_played":
        extra = set()     # 125 animals alone completes nothing else
    scenario(cid, label, target_id=chid, expect={chid} | extra,
             short_of=lambda a=a, b=b, key=key: (
                 casual(us[0], place=2, bots=["Bot"], cs=stats(**{key: a})),
                 casual(us[1], place=2, bots=["Bot"], cs=stats(**{key: b - 1}))),
             last_step=lambda key=key: casual(us[0], place=2, bots=["Bot"], cs=stats(**{key: 1})))

# Competitive telemetry is summed over BOTH of a player's hands.
cid, us = new_clan(1)
comp(us[0], won=True, cs=stats(oceans_played=40))
check("competitive telemetry sums both of your hands",
      weekly_of(cid).get("oceans_played") == 40, str(weekly_of(cid).get("oceans_played")))
# A record written before the telemetry existed must not block a claim.
cid, us = new_clan(1)
r = casual(us[0], place=1, bots=["Bot"], cs=None)
check("an old record with no telemetry still claims cleanly", r.get("ok") is True)

# ══ WEEKLY 25: Clan Traders ══════════════════════════════════════════════════
print("\nweekly: trading:")
# A player can only take one trade point a day, so 15 trades in a week needs a
# few partners: 6 members trading in pairs over 5 days is 15.
cid, us = new_clan(6)
PAIRS = [(0, 1), (2, 3), (4, 5)]
def _trades(n):
    def go():
        done = 0
        for d in range(5):
            for a, b in PAIRS:
                if done >= n:
                    return
                at(BASE + d * DAY)
                trade(us[a], us[b])
                done += 1
    return go
scenario(cid, "Clan Traders (15 eligible clan trades)",
         target_id="w_clan_traders",
         expect={"w_clan_traders", "s_clan_trades"},
         short_of=_trades(14),
         last_step=lambda: (at(BASE + 4 * DAY), trade(us[4], us[5])))
check("one trade counts ONCE for the clan, even though it pays both sides",
      weekly_of(cid).get("trades") == 15, str(weekly_of(cid).get("trades")))
at(BASE)

# ══ SEASON: participation ════════════════════════════════════════════════════
print("\nseason: participation:")
cid, us = new_clan(2)
def _with_mate(n):
    return lambda: [casual(us[0], place=2, opponents=[us[1]], bots=["Bot"]) for _ in range(n)]
scenario(cid, "Clan Kickoff (20 games with a clan member in them)",
         target_id="s_clan_kickoff", expect={"s_clan_kickoff"},
         short_of=_with_mate(19), last_step=_with_mate(1))
scenario(cid, "Ocean Expedition (50 such games)",
         target_id="s_ocean_expedition",
         expect={"s_clan_kickoff", "s_ocean_expedition",
                 "s_clan_voyage", "s_ocean_marathon"},
         short_of=_with_mate(29), last_step=_with_mate(1))
check("Clan Voyage and Ocean Marathon rode along on the same 50 games",
      CS._num(slot_of(cid).get("games")) == 50)

# Games with no clanmate never count toward Clan Kickoff.
cid, us = new_clan(2)
for _ in range(25):
    casual(us[0], place=2, bots=["Bot"])
check("a solo game is not 'with one or more clan members'",
      CS._num(slot_of(cid).get("games_with_clanmate")) == 0)
check("...but it does count toward Clan Voyage", "s_clan_voyage" in done_set(cid))

# ══ SEASON: All Together ═════════════════════════════════════════════════════
cid, us = new_clan(8)
scenario(cid, "All Together (8 clan members in one game together)",
         target_id="s_all_together",
         expect={"s_all_together"},
         short_of=lambda: casual(us[0], place=2, opponents=us[1:7]),   # 7 of us
         last_step=lambda: casual(us[0], place=2, opponents=us[1:8]))  # 8 of us
check("All Together needs them in ONE game, not eight games",
      CS._num(slot_of(cid).get("max_clanmates_in_game")) == 8)
cid, us = new_clan(8)
for u in us:
    casual(u, place=2, bots=["Bot"])
check("eight members playing separately is not All Together",
      "s_all_together" not in done_set(cid))

# ══ SEASON: Packed Ocean ═════════════════════════════════════════════════════
cid, us = new_clan(1)
def _six_player(n):
    return lambda: [casual(us[0], place=2, bots=["B1", "B2", "B3", "B4", "B5"])
                    for _ in range(n)]
scenario(cid, "Packed Ocean (5 casual games with 6+ players)",
         target_id="s_packed_ocean", expect={"s_packed_ocean"},
         short_of=_six_player(4), last_step=_six_player(1))
cid, us = new_clan(1)
for _ in range(8):
    casual(us[0], place=2, bots=["B1", "B2", "B3", "B4"])   # 5 players
check("a 5-player game is not a Packed Ocean game",
      CS._num(slot_of(cid).get("casual_6p")) == 0)

# ══ SEASON: gameplay points ══════════════════════════════════════════════════
print("\nseason: points earned by playing:")
cid, us = new_clan(4)
def _earn(n_wins, member):
    # Each win is +2, against a DIFFERENT opponent set each time so the
    # repeat-lobby limiter never zeroes one.
    def go():
        for _ in range(n_wins):
            o = nid("e"); set_user(o, "Foe" + o)
            casual(member, place=1, opponents=[o])
    return go
scenario(cid, "Rising Tide (75 Clan Points through gameplay)",
         target_id="s_rising_tide",
         expect={"s_rising_tide", "w_casual_current", "s_clan_voyage"},
         short_of=_earn(37, us[0]), last_step=_earn(1, us[0]))
check("Rising Tide counts POINTS, not games",
      CS._num(slot_of(cid).get("gameplay_points")) == 76)
# Trade points are not gameplay points.
cid, us = new_clan(2)
for i in range(5):
    at(BASE + i * DAY)
    trade(us[0], us[1])
at(BASE)
check("trade points are not 'through gameplay'",
      CS._num(slot_of(cid).get("gameplay_points")) == 0
      and CS._num(slot_of(cid).get("trade_points")) == 10)

# ══ SEASON: Balanced Waters ══════════════════════════════════════════════════
cid, us = new_clan(4)
# 26 points of casual only: half the job.
for _ in range(13):
    o = nid("e"); set_user(o, "Foe" + o)
    casual(us[0], place=1, opponents=[o])
check("Balanced Waters is not done on casual points alone",
      "s_balanced_waters" not in done_set(cid)
      and CS._num(slot_of(cid).get("casual_points")) == 26)
for _ in range(8):
    comp(us[1], won=True)                                # 8 × 3 = 24 comp points
check("...nor at 24 competitive points", "s_balanced_waters" not in done_set(cid))
comp(us[1], won=True)                                     # 27 comp points
check("Balanced Waters completes once BOTH halves are there",
      "s_balanced_waters" in done_set(cid))
check("its progress reads the SMALLER half while it is unfinished",
      CS._season_metric({"comp_points": 40, "casual_points": 12}, "balanced_points") == 12)

# ══ SEASON: Podium Masters ═══════════════════════════════════════════════════
print("\nseason: podiums:")
cid, us = new_clan(2)
def _podiums(n, place):
    return lambda: [casual(us[0], place=place, bots=["B1", "B2", "B3"]) for _ in range(n)]
scenario(cid, "Podium Masters (100 podium finishes in casual games)",
         target_id="s_podium_masters",
         expect={"s_podium_masters", "w_crowded_waters", "s_clan_voyage",
                 "s_ocean_marathon"},
         short_of=_podiums(99, 3), last_step=_podiums(1, 3))
cid, us = new_clan(1)
for _ in range(10):
    casual(us[0], place=4, bots=["B1", "B2", "B3"])
check("fourth place is not a podium", CS._num(slot_of(cid).get("podiums")) == 0)
# A podium has to actually PLACE, last place is never a podium, however few
# players there were.
cid, us = new_clan(1)
for _ in range(5):
    casual(us[0], place=2, bots=["Bot"])           # 2 players: 2nd is last
check("second in a two-player game is not a podium",
      CS._num(slot_of(cid).get("podiums")) == 0)
for _ in range(5):
    casual(us[0], place=3, bots=["B1", "B2"])      # 3 players: 3rd is last
check("third in a three-player game is not a podium",
      CS._num(slot_of(cid).get("podiums")) == 0)
casual(us[0], place=2, bots=["B1", "B2"])          # 3 players: 2nd places
check("second in a three-player game IS a podium",
      CS._num(slot_of(cid).get("podiums")) == 1)
casual(us[0], place=3, bots=["B1", "B2", "B3"])    # 4 players: 3rd places
check("third in a four-player game IS a podium",
      CS._num(slot_of(cid).get("podiums")) == 2)

# ══ SEASON: Shoot the Moon ═══════════════════════════════════════════════════
print("\nseason: Shoot the Moon:")
cid, us = new_clan(1)
MOON = stats(gobies=4)
scenario(cid, "Shoot the Moon (all 4 Mandarin Gobies on one board)",
         target_id="s_shoot_the_moon", expect={"s_shoot_the_moon"},
         short_of=None, last_step=lambda: casual(us[0], place=2, bots=["Bot"], cs=MOON))
scenario(cid, "Shooting the Moon (the same bonus in five games)",
         target_id="s_shooting_the_moon",
         expect={"s_shoot_the_moon", "s_shooting_the_moon"},
         short_of=lambda: [casual(us[0], place=2, bots=["Bot"], cs=MOON) for _ in range(3)],
         last_step=lambda: casual(us[0], place=2, bots=["Bot"], cs=MOON))
cid, us = new_clan(1)
for _ in range(6):
    casual(us[0], place=2, bots=["Bot"], cs=stats(gobies=3))
check("three Gobies is not Shoot the Moon", CS._num(slot_of(cid).get("moon_games")) == 0)

# ══ SEASON: Fresh Recruits ═══════════════════════════════════════════════════
print("\nseason, the clan itself:")
cid, us = new_clan(1)
def _join(n):
    def go():
        for _ in range(n):
            u = nid("j"); set_user(u, "New" + u)
            CS._join_clan(u, {"clan_id": cid})
    return go
scenario(cid, "Fresh Recruits (two new members)",
         target_id="s_new_members", expect={"s_new_members"},
         short_of=_join(1), last_step=_join(1))
check("the joiners really are members now",
      len((DB.store["clans/" + cid].get("members") or {})) == 3)

# ══ SEASON: Event Organizers ═════════════════════════════════════════════════
cid, us = new_clan(1)
def _events(n):
    def go():
        for _ in range(n):
            CS._route_post(DB, us[0], "events",
                           {"op": "create", "name": "Reef Night " + nid("ev")}, SID)
    return go
scenario(cid, "Event Organizers (organize three clan events)",
         target_id="s_events", expect={"s_events"},
         short_of=_events(2), last_step=_events(1))

# ══ SEASON: rival ════════════════════════════════════════════════════════════
rival_cid, rival_us = new_clan(4, name="The Rivals")
cid, us = new_clan(4)
scenario(cid, "Choose Your Rival (create a rival clan)",
         target_id="s_rival", expect={"s_rival"},
         short_of=None,
         last_step=lambda: CS._route_post(DB, us[0], "rival", {"clan_id": rival_cid}, SID))
# Swapping rivals repeatedly must not pay repeatedly.
before = points(cid)
for _ in range(3):
    CS._route_post(DB, us[0], "rival", {"clan_id": rival_cid}, SID)
check("re-declaring a rival never pays twice", points(cid) == before)

# Cross-Current: a team game with 3 of us and 3 of them.
scenario(cid, "Cross-Current (Team Mode, 3 of us and 3 of our rivals)",
         target_id="s_team_rival", expect={"s_rival", "s_team_rival"},
         short_of=lambda: casual(us[0], place=2, team=True,
                                 opponents=us[1:2] + rival_us[:3]),   # only 2 of us
         last_step=lambda: casual(us[0], place=2, team=True,
                                  opponents=us[1:3] + rival_us[:3]))  # 3 of us, 3 of them
check("Cross-Current needs 3 from EACH clan",
      CS._num(slot_of(cid).get("team_rival_games")) == 1,
      str(slot_of(cid).get("team_rival_games")))
# Not a team game → no Cross-Current, whoever is in it.
cid2, us2 = new_clan(4)
CS._route_post(DB, us2[0], "rival", {"clan_id": rival_cid}, SID)
casual(us2[0], place=2, team=False, opponents=us2[1:4] + rival_us[:3])
check("a casual (non-Team) game is never Cross-Current",
      CS._num(slot_of(cid2).get("team_rival_games")) == 0)
# A team game without the rivals in it is not Cross-Current.
casual(us2[0], place=2, team=True, opponents=us2[1:4])
check("a Team game without the rival clan is not Cross-Current",
      CS._num(slot_of(cid2).get("team_rival_games")) == 0
      and CS._num(slot_of(cid2).get("team_games")) == 1,
      str(slot_of(cid2).get("team_games")))

# ══ SEASON: Team Tide ════════════════════════════════════════════════════════
cid, us = new_clan(1)
def _team(n):
    return lambda: [casual(us[0], place=2, team=True, bots=["Bot"]) for _ in range(n)]
scenario(cid, "Team Tide (play 10 games in Team Mode)",
         target_id="s_team_mode", expect={"s_team_mode"},
         short_of=_team(9), last_step=_team(1))
cid, us = new_clan(1)
for _ in range(12):
    casual(us[0], place=2, team=False, bots=["Bot"])
check("a normal casual game is not a Team Mode game",
      CS._num(slot_of(cid).get("team_games")) == 0)

# ══ SEASON: Good Sports ══════════════════════════════════════════════════════
print("\nseason: good sportsmanship:")
cid, us = new_clan(2)
GG = stats(said_gg=True)
def _gg(n, who=0):
    return lambda: [casual(us[who], place=2, bots=["Bot"], cs=GG) for _ in range(n)]
scenario(cid, 'Good Sports (say "good game" in 25 different games)',
         target_id="s_good_game", expect={"s_good_game", "s_clan_voyage"},
         short_of=_gg(24), last_step=_gg(1))
cid, us = new_clan(1)
for _ in range(30):
    casual(us[0], place=2, bots=["Bot"], cs=stats(said_gg=False))
check("staying quiet never counts toward Good Sports",
      CS._num(slot_of(cid).get("gg_games")) == 0)
# The server, not the client, decides what a "good game" is.
check("the server recognises 'good game'", M._is_good_game_message("good game everyone"))
check("...and 'gg'", M._is_good_game_message("gg"))
check("...and 'GG WP'", M._is_good_game_message("GG WP"))
check("...and 'Goodgame!'", M._is_good_game_message("Goodgame!"))
check("but not 'egg'", not M._is_good_game_message("pass the egg"))
check("...and not 'giggle'", not M._is_good_game_message("giggle"))
check("...and not 'biggest'", not M._is_good_game_message("that was my biggest score"))
check("...and not an empty line", not M._is_good_game_message(""))

# ══ SEASON: Ecosystem Engineers ══════════════════════════════════════════════
print("\nseason: Ecosystem Engineers:")
cid, us = new_clan(2)
for _ in range(6):
    casual(us[0], place=2, bots=["Bot"], cs=stats(animals_played=100))
check("600 animals with no Oceans does not complete Ecosystem Engineers",
      "s_ecosystem_engineers" not in done_set(cid))
for _ in range(5):
    casual(us[1], place=2, bots=["Bot"], cs=stats(oceans_played=111))
check("Ecosystem Engineers completes once BOTH 555s are there",
      "s_ecosystem_engineers" in done_set(cid),
      f"animals={slot_of(cid).get('animals_played')} oceans={slot_of(cid).get('oceans_played')}")
check("its progress reads the smaller of the two",
      CS._season_metric({"animals_played": 900, "oceans_played": 200}, "ecosystem") == 200)

# ══ SEASON: Saving the Invertebrates (whole clan) ════════════════════════════
print("\nseason: Saving the Invertebrates:")
INV = {"saving_the_invertebrates": {"completed": True}}
cid, us = new_clan(3)
for u in us[:2]:
    DB.store["users/" + u]["achievements"] = copy.deepcopy(INV)
CS._members_invalidate(); CS._REG_CACHE.clear()
for u in us[:2]:
    casual(u, place=2, bots=["Bot"])
check("two of three members is not the whole clan",
      "s_invertebrates" not in done_set(cid),
      str(len(slot_of(cid).get("invert_members") or {})))
DB.store["users/" + us[2]]["achievements"] = copy.deepcopy(INV)
CS._members_invalidate(); CS._REG_CACHE.clear()
casual(us[2], place=2, bots=["Bot"])
check("the whole clan holding it completes it", "s_invertebrates" in done_set(cid))
check("a member without the achievement is never counted",
      len(slot_of(cid).get("invert_members") or {}) == 3)
# A clan of one can't claim "everybody" by being alone.
cid, us = new_clan(1)
DB.store["users/" + us[0]]["achievements"] = copy.deepcopy(INV)
CS._members_invalidate(); CS._REG_CACHE.clear()
casual(us[0], place=2, bots=["Bot"])
check("a one-person clan cannot claim 'every member' by itself",
      "s_invertebrates" not in done_set(cid))

# ══ SEASON: Rank Climbers ════════════════════════════════════════════════════
print("\nseason: Rank Climbers:")
LADDER = ["Bronze Barracuda I", "Bronze Barracuda II", "Bronze Barracuda III",
          "Silver Spiny Lobster I", "Silver Spiny Lobster II", "Silver Spiny Lobster III",
          "Golden Grouper I", "Golden Grouper II", "Golden Grouper III",
          "Diamond Dolphin I", "Diamond Dolphin II", "Diamond Dolphin III",
          "Emerald Emperor Penguin I", "Emerald Emperor Penguin II",
          "Emerald Emperor Penguin III", "King of the Critters"]
check("the whole live rank ladder is strictly increasing",
      all(CS._rank_tier_index(LADDER[i]) < CS._rank_tier_index(LADDER[i + 1])
          for i in range(len(LADDER) - 1)),
      str([CS._rank_tier_index(x) for x in LADDER]))
check("Unranked sits below every division", CS._rank_tier_index("Unranked") == 0)

cid, us = new_clan(1)
def _climb(steps):
    def go():
        for name in steps:
            DB.store["users/" + us[0]]["stats"]["rank_competitive"] = name
            CS._members_invalidate(); CS._REG_CACHE.clear()
            comp(us[0], won=False, opp_best=300, my_best=10, my_second=5)
    return go
# The FIRST observation only records where they are; climbs count after that.
scenario(cid, "Rank Climbers (15 division climbs)",
         target_id="s_rank_climbers",
         expect={"s_rank_climbers", "w_double_handed"},
         short_of=_climb(LADDER[:15]), last_step=_climb(LADDER[15:16]))
check("exactly 15 climbs were counted for 16 divisions seen",
      CS._num(slot_of(cid).get("rank_ups")) == 15, str(slot_of(cid).get("rank_ups")))
# Falling back down never counts.
cid, us = new_clan(1)
for name in ["Golden Grouper III", "Silver Spiny Lobster I", "Bronze Barracuda I"]:
    DB.store["users/" + us[0]]["stats"]["rank_competitive"] = name
    CS._members_invalidate(); CS._REG_CACHE.clear()
    comp(us[0], won=False, opp_best=300, my_best=10, my_second=5)
check("dropping rank never counts as a climb", CS._num(slot_of(cid).get("rank_ups")) == 0)

# ══ SEASON: Competitive Fleet ════════════════════════════════════════════════
cid, us = new_clan(10)
scenario(cid, "Competitive Fleet (10 different members win a competitive match)",
         target_id="s_competitive_fleet",
         expect={"s_competitive_fleet", "w_competitive_current", "w_winning_waters",
                 "w_double_handed", "w_winning_streak", "w_full_crew", "w_all_hands"},
         short_of=lambda: [comp(u, won=True) for u in us[:9]],
         last_step=lambda: comp(us[9], won=True))

# ══ SEASON: Regular Tides ════════════════════════════════════════════════════
print("\nseason: Regular Tides (weeks, never months):")
cid, us = new_clan(2)
MONDAYS = [1782734400 + 7 * DAY * i for i in range(11)]   # 11 consecutive Mondays
def _weeks(lo, hi, per_week=3):
    def go():
        for w in range(lo, hi):
            for i in range(per_week):
                at(MONDAYS[w] + 3600 + i * 60)
                casual(us[0], place=2, bots=["Bot"])
    return go
scenario(cid, "Regular Tides (3+ games in 10 different weeks)",
         target_id="s_regular_tides",
         expect={"s_regular_tides", "s_clan_voyage", "w_daily_divers", "w_six_seven"}
                - {"w_daily_divers", "w_six_seven"},
         short_of=_weeks(0, 9), last_step=_weeks(9, 10))
check("it counts WEEKS, not games",
      sum(1 for n in (slot_of(cid).get("week_games") or {}).values() if n >= 3) == 10,
      str(sorted((slot_of(cid).get("week_games") or {}).items())))
# A week with only two games is not a regular week.
cid, us = new_clan(2)
for w in range(11):
    for i in range(2):
        at(MONDAYS[w] + 3600 + i * 60)
        casual(us[0], place=2, bots=["Bot"])
check("eleven weeks of two games each never completes Regular Tides",
      "s_regular_tides" not in done_set(cid))
check("nothing in the system is monthly any more",
      not any("month" in (c.get("metric") or "").lower()
              or "month" in (c.get("desc") or "").lower()
              or "month" in (c.get("name") or "").lower()
              for c in CS.CLAN_WEEKLY_CHALLENGES + CS.CLAN_SEASON_CHALLENGES),
      str([c["id"] for c in CS.CLAN_WEEKLY_CHALLENGES + CS.CLAN_SEASON_CHALLENGES
           if "month" in (c.get("desc") or "").lower()]))
at(BASE)

# ══ SEASON: Ranked Predators ═════════════════════════════════════════════════
cid, us = new_clan(2)
scenario(cid, "Ranked Predators (win 30 competitive matches)",
         target_id="s_ranked_predators",
         expect={"s_ranked_predators", "w_competitive_current", "w_double_handed",
                 "w_winning_streak", "s_clan_voyage", "s_rising_tide"},
         short_of=lambda: [comp(us[0], won=True) for _ in range(29)],
         last_step=lambda: comp(us[0], won=True))

# ══ SEASON: Powerful Current ═════════════════════════════════════════════════
print("\nseason, the big point ladders:")
cid, us = new_clan(4)
def _earn_split(total_wins):
    def go():
        for i in range(total_wins):
            o = nid("e"); set_user(o, "Foe" + o)
            casual(us[i % 3], place=1, opponents=[o])
    return go
scenario(cid, "Powerful Current (150 Clan Points through gameplay)",
         target_id="s_powerful_current",
         expect={"s_powerful_current", "s_rising_tide", "w_casual_current",
                 "s_clan_voyage", "s_ocean_marathon"},
         short_of=_earn_split(74), last_step=_earn_split(1))

# ══ SEASON: Reef Merchants ═══════════════════════════════════════════════════
cid, us = new_clan(2)
def _trade_days(n):
    def go():
        for i in range(n):
            at(BASE + i * DAY)
            trade(us[0], us[1])
    return go
scenario(cid, "Reef Merchants (trade 5 times with clan members)",
         target_id="s_clan_trades", expect={"s_clan_trades"},
         short_of=_trade_days(4),
         last_step=lambda: (at(BASE + 4 * DAY), trade(us[0], us[1])))
check("five real trades read as five, not ten",
      CS._num(slot_of(cid).get("clan_trades")) == 5,
      str(slot_of(cid).get("clan_trades")))
at(BASE)

# ══ SEASON: Rival Reckoning + the rank payout (season finalize) ══════════════
print("\nseason finalize: rival result and rank rewards:")
CS._FINALIZED_SIDS.clear()
CS._FINALIZED_SIDS.add(CS._prev_sid(SID))
win_cid, win_us = new_clan(2, rank="Golden Grouper II")
lose_cid, lose_us = new_clan(2, rank="Bronze Barracuda I")
# The winners out-earn their rivals during the season.
for _ in range(6):
    o = nid("e"); set_user(o, "Foe" + o)
    casual(win_us[0], place=1, opponents=[o])
o = nid("e"); set_user(o, "Foe" + o)
casual(lose_us[0], place=1, opponents=[o])
DB.store["clans/" + win_cid]["rivals"] = {SID: lose_cid}
DB.store["clans/" + lose_cid]["rivals"] = {SID: win_cid}
before_win = points(win_cid)
before_lose = points(lose_cid)
CS._finalize_season_bonuses(DB, SID)
check("beating your rival completes Rival Reckoning",
      "s_beat_rival" in done_set(win_cid))
check("...and losing to them does not",
      "s_beat_rival" not in done_set(lose_cid))
check("a Gold roster brings its clan 15 Clan Points a head",
      points(win_cid) - before_win >= 30 + 10,      # 2 × 15 rank + 10 rival
      f"{before_win} → {points(win_cid)}")
_gold_coins = CS.COMP_RANK_SEASON_REWARDS["gold"]["coins"]
check(f"Gold pays each member {_gold_coins} Critter Coins",
      (DB.store["users/" + win_us[0]]["stats"]["critter_coins"]) == _gold_coins,
      str(DB.store["users/" + win_us[0]]["stats"]["critter_coins"]))
check("Bronze pays nothing at all",
      DB.store["users/" + lose_us[0]]["stats"]["critter_coins"] == 0
      and points(lose_cid) == before_lose)

# Every division name lands on the tier Tim listed. The PAYOUTS are read from
# the constants rather than repeated here: which tier a division belongs to is
# a rule, what that tier pays is an economy dial, and a rebalance should not
# read as six broken tests.
print("\nrank reward table:")
_tiers_ascending = ["unranked", "bronze", "silver", "gold", "diamond", "emerald", "king"]
for div, tier in [
        ("Bronze Barracuda III", "bronze"),
        ("Silver Spiny Lobster II", "silver"),
        ("Golden Grouper I", "gold"),
        ("Diamond Dolphin III", "diamond"),
        ("Emerald Emperor Penguin II", "emerald"),
        ("King of the Critters", "king")]:
    got_tier = CS._rank_tier(div)
    rw = CS.COMP_RANK_SEASON_REWARDS.get(got_tier) or {}
    check(f"{div} maps to {tier}, paying {rw.get('coins')} Critter Coins "
          f"+ {rw.get('clan_points')} Clan Points",
          got_tier == tier and rw.get("coins") is not None,
          f"{got_tier} {rw}")

# A higher rank must never pay less than a lower one. That IS a rule, and it is
# the one thing a rebalance can silently get wrong.
_coins = [CS.COMP_RANK_SEASON_REWARDS[t]["coins"] for t in _tiers_ascending]
_pts = [CS.COMP_RANK_SEASON_REWARDS[t]["clan_points"] for t in _tiers_ascending]
check("the rank payouts never go backwards as the rank goes up",
      _coins == sorted(_coins) and _pts == sorted(_pts), f"{_coins} / {_pts}")
check("Unranked pays nothing", CS.COMP_RANK_SEASON_REWARDS["unranked"]["coins"] == 0)

# ══ The core scoring rule ════════════════════════════════════════════════════
print("\ncore scoring rule:")
cid, us = new_clan(1)
r = casual(us[0], place=1, bots=["B1", "B2", "B3"])
check("1st against bots is half a Clan Point", r.get("points") == 0.5)
r = casual(us[0], place=2, bots=["B1", "B2", "B3"])
check("2nd against bots is nothing", r.get("points") == 0)
real = nid("x"); set_user(real, "RealRival" + real)
r = casual(us[0], place=1, opponents=[real])
check("1st against a real account is the full 2 points", r.get("points") == 2)
check("the clan's total carries the half point", CS._num(slot_of(cid).get("points")) == 2.5)
# The half point has to survive every path that reports it back, not just the
# one that stores it: truncating it on the way out is the same bug.
_home = CS._route_post(DB, us[0], "home", {}, SID)
check("the home screen reports the half point, not a truncated 2",
      _home["my_contribution"]["points"] == 2.5, str(_home["my_contribution"]))
check("...and so does the clan card", _num_ok := (_home["my_clan"]["points"] == 2.5),
      str(_home["my_clan"]["points"]))
_prof = CS._route_post(DB, us[0], "get", {}, SID)["clan"]
check("...and the clan profile", _prof["points"] == 2.5, str(_prof["points"]))
check("...and the member row inside it",
      any(m["points"] == 2.5 for m in _prof["members"]),
      str([m["points"] for m in _prof["members"]]))
check("halves are stored as halves, not rounded away",
      CS._num(0.5) == 0.5 and CS._num(2) == 2 and CS._num(2.0) == 2 and CS._num("bad") == 0)

# Casual against bots still counts for CHALLENGES, only the points are cut.
cid, us = new_clan(1)
casual(us[0], place=2, bots=["Bot"], cs=stats(oceans_played=9, animals_played=4))
check("a bots-only casual game still counts as a game",
      CS._num(slot_of(cid).get("games")) == 1 and weekly_of(cid).get("games") == 1)
check("...and its play still counts toward the telemetry challenges",
      weekly_of(cid).get("oceans_played") == 9 and weekly_of(cid).get("animals_played") == 4)
# Competitive is the opposite: no real opponent, no match at all.
cid, us = new_clan(1)
room = nid("R").upper()
me = nick(us[0])
with open(os.path.join(COMP, f"game_{room}_{CS._now()}.json"), "w") as f:
    json.dump({"room_id": room, "recorded_unix": CS._now(), "season_id": SID,
               "p1_name": me, "p2_name": "WanderingGuest", "p1_best_score": 200,
               "p2_best_score": 10, "p1_second_score": 190, "p2_second_score": 5,
               "winner": me, "is_draw": False}, f)
with open(os.path.join(HIST, f"game_{room}_{CS._now()}.json"), "w") as f:
    json.dump({"room_id": room, "recorded_unix": CS._now(), "mode": "competitive",
               "player_count": 4, "human_count": 4, "winner": me,
               "standings": [{"name": me, "score": 200, "seat_index": 0}],
               "players": [{"name": me, "is_human": True, "seat_index": 0,
                            "clan_stats": stats(oceans_played=9)}]}, f)
r = CS.claim_game_points(us[0], room)
check("a competitive match against a guest is refused outright",
      not r.get("ok") and r.get("error") == "opponent_not_registered", str(r))
check("...and moves nothing at all, not the record, not the telemetry",
      CS._num(slot_of(cid).get("games")) == 0
      and CS._num(slot_of(cid).get("comp_wins")) == 0
      and CS._num(weekly_of(cid).get("oceans_played")) == 0)

# ══ The generated rulebook ═══════════════════════════════════════════════════
print("\npublished rules:")
R = CS.clan_rules()
check("the rules list a 25-member clan", R["max_members"] == 25 == CS.CLAN_MAX_MEMBERS)
check("the rules list every weekly challenge",
      len(R["weekly_challenges"]) == len(CS.CLAN_WEEKLY_CHALLENGES))
check("the rules list every season challenge",
      len(R["season_challenges"]) == len(CS.CLAN_SEASON_CHALLENGES))
check("the rules state the half-point rule",
      any("0.5" in str(x) or "0.5" in str(x) for x in R["core_rules"])
      or any(s["points"] == 0.5 for s in R["scoring"]))
check("the rules name Critter Coins, never bare 'coins'",
      all("Critter Coins" in s for s in R["season_rewards"] if "Coins" in s)
      and not any(("coins" in s and "Critter Coins" not in s) for s in R["season_rewards"]))
check("every rank tier is published",
      [t["tier"] for t in R["rank_rewards"]["tiers"]] ==
      ["Bronze Barracuda", "Silver Spiny Lobster", "Golden Grouper",
       "Diamond Dolphin", "Emerald Emperor Penguin", "King of the Critters"])
check("the published rewards match the ones the server pays",
      all(t["coins"] == CS.COMP_RANK_SEASON_REWARDS[k]["coins"]
          and t["clan_points"] == CS.COMP_RANK_SEASON_REWARDS[k]["clan_points"]
          for t, k in zip(R["rank_rewards"]["tiers"],
                          ["bronze", "silver", "gold", "diamond", "emerald", "king"])))

# ══ The telemetry itself: what the GAME server actually derives ══════════════
# Everything above feeds clan_stats into the clan server. This section proves
# the other half of the seam: that multiplayer_server derives those numbers
# correctly from a room's own executed-action history.
print("\ngame-server telemetry:")


class _Card:
    def __init__(self, uid, name, species):
        self.uid, self.name, self.species = uid, name, species


class _Slots:
    def __init__(self, up=(), down=(), left=(), right=()):
        self.up, self.down, self.left, self.right = list(up), list(down), list(left), list(right)


CARDS = {
    239: _Card(239, "Artificial Reef", "Ocean"),
    253: _Card(253, "Kelp Forest", "Ocean"),
    101: _Card(101, "Giant Squid", "Cephalopod"),
    103: _Card(103, "Bobtail Squid", "Cephalopod"),
    150: _Card(150, "Mandarin Goby", "Game Fish"),
    160: _Card(160, "Yellowfin Tuna", "Game Fish"),
}


def _room(history, *, endgame=None, gg=(), players=None):
    gs = types.SimpleNamespace(card_db=CARDS, players=players or [
        types.SimpleNamespace(name="Alice", board_oceans=[], ocean_slots={}),
        types.SimpleNamespace(name="Bob", board_oceans=[], ocean_slots={}),
    ])
    room = types.SimpleNamespace(action_history=history, clan_endgame_scores=endgame,
                                 clan_gg_names=set(gg), _comp_game_to_seat={})
    return M.GameRoom._clan_game_stats(room, gs, types.SimpleNamespace()), gs


def act(seat, kind, turn=1, **kw):
    base = {"seat_index": seat, "kind": kind, "turn_number": turn,
            "card_uid": -1, "face_uid": -1, "draw_from_pool": 0, "use_star": False}
    base.update(kw)
    return base


st, _ = _room([
    act(0, "play_ocean", face_uid=239),                      # Artificial Reef FIRST
    act(0, "play_ocean", face_uid=253),
    act(0, "play_to_ocean", face_uid=160),
    act(0, "play_to_ocean", face_uid=101, use_star=True),
    act(0, "play_to_ocean", face_uid=103, use_star=True),     # 2nd ★ same turn = a chain
    act(0, "play_to_ocean", face_uid=101, use_star=True, turn=2),   # lone ★, no chain
    act(0, "move_between_oceans", turn=2),
    act(0, "draw", draw_from_pool=1, turn=3),
    act(0, "draw", draw_from_pool=0, turn=3),
    act(1, "play_ocean", face_uid=253),                      # Bob's first ocean
])
check("oceans played counted per player", st[0]["oceans_played"] == 2 and st[1]["oceans_played"] == 1)
check("animal cards counted", st[0]["animals_played"] == 4)
check("moves between oceans counted", st[0]["moves"] == 1)
check("★ activations counted", st[0]["stars"] == 3)
check("a chained-★ turn counts once, not once per ★", st[0]["star_chain_turns"] == 1)
check("a Pool draw is one card from the Pool", st[0]["pool_draws"] == 1)
check("a Deck draw is one card from the Deck", st[0]["deck_draws"] == 1)
check("the FIRST ocean is the one recorded", st[0]["first_ocean"] == "Artificial Reef")
check("...and it is per player", st[1]["first_ocean"] == "Kelp Forest")
check("Cephalopods in one turn are tracked for Humuhumunukuapua'a",
      st[0]["max_ceph_turn"] == 2)

# Five Cephalopods in ONE turn, and four spread over two turns.
st, _ = _room([act(0, "play_to_ocean", face_uid=101, turn=1) for _ in range(5)]
              + [act(0, "play_to_ocean", face_uid=103, turn=2) for _ in range(4)])
check("5 Cephalopods in one turn is the achievement", st[0]["max_ceph_turn"] == 5)
st, _ = _room([act(0, "play_to_ocean", face_uid=101, turn=1) for _ in range(3)]
              + [act(0, "play_to_ocean", face_uid=101, turn=2) for _ in range(3)])
check("3 + 3 across two turns is NOT 5 in one turn", st[0]["max_ceph_turn"] == 3)

# A ★ on a draw is not a thing, only a play can carry one.
st, _ = _room([act(0, "draw", draw_from_pool=1, use_star=True)])
check("a draw never counts as a ★ activation", st[0]["stars"] == 0 and st[0]["pool_draws"] == 1)

# Shoot the Moon: all four Mandarin Gobies on one board.
gobies = types.SimpleNamespace(name="Alice", board_oceans=[239],
                               ocean_slots={239: _Slots(up=[150, 150], down=[150, 150])})
three = types.SimpleNamespace(name="Bob", board_oceans=[239],
                              ocean_slots={239: _Slots(up=[150, 150, 150], down=[160])})
st, _ = _room([], players=[gobies, three])
check("four Mandarin Gobies on the board is Shoot the Moon", st[0]["gobies"] == 4)
check("three is not", st[1]["gobies"] == 3)

# Comeback + good game are observed live, not derived.
st, _ = _room([], endgame={"Alice": 40, "Bob": 90}, gg=["alice"])
check("being behind when End Game was revealed is recorded",
      st[0]["behind_at_endgame"] is True and st[1]["behind_at_endgame"] is False)
check("saying good game is recorded, by name", st[0]["said_gg"] is True and st[1]["said_gg"] is False)
st, _ = _room([], endgame=None)
check("no End Game snapshot means nobody can claim a comeback",
      st[0]["behind_at_endgame"] is False)

# A seat index the room doesn't have must be ignored, not crash the save.
st, _ = _room([act(9, "play_ocean", face_uid=239), act(0, "play_ocean", face_uid=239)])
check("an out-of-range seat in the history is ignored", st[0]["oceans_played"] == 1)

# ══ Every challenge is reachable and was exercised ═══════════════════════════
print("\ncoverage:")
DRIVEN = {
    "w_full_crew", "w_all_hands", "w_daily_divers", "w_six_seven", "w_double_handed",
    "w_artificial_start", "w_casual_current", "w_crowded_waters", "w_eight_at_sea",
    "w_friendly_competition", "w_comeback_current", "w_competitive_current",
    "w_winning_waters", "w_winning_streak", "w_humu_duo", "w_double_trouble",
    "w_dominant_depths", "w_ocean_architects", "w_critter_collection",
    "w_moving_tide", "w_star_power", "w_chain_reaction", "w_pool_party",
    "w_deep_draw", "w_clan_traders",
    "s_clan_kickoff", "s_ocean_expedition", "s_ranked_predators", "s_packed_ocean",
    "s_clan_voyage", "s_ocean_marathon", "s_all_together", "s_regular_tides",
    "s_rising_tide", "s_powerful_current", "s_balanced_waters", "s_podium_masters",
    "s_shoot_the_moon", "s_shooting_the_moon", "s_new_members", "s_competitive_fleet",
    "s_rank_climbers", "s_ecosystem_engineers", "s_invertebrates", "s_good_game",
    "s_events", "s_team_mode", "s_rival", "s_beat_rival", "s_clan_trades",
    "s_team_rival",
}
missing = set(_ALL) - DRIVEN
check("every one of the 51 challenges was driven to completion in this run",
      not missing, "never driven: " + str(sorted(missing)))
check("...and nothing was driven that isn't a real challenge",
      not (DRIVEN - set(_ALL)), str(sorted(DRIVEN - set(_ALL))))

shutil.rmtree(TMP, ignore_errors=True)
print("\n" + "=" * 62)
print(f"RESULT: {_PASS} passed, {_FAIL} failed")
if _FAILED:
    print("failed:\n  - " + "\n  - ".join(_FAILED))
sys.exit(1 if _FAIL else 0)
