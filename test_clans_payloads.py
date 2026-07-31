"""Clan System — the seam between the REAL server and the REAL Clans tab.

test_clan_server.py proves clan_server.py's rules. test_clans_render.js proves
clans-ui.js paints, but from payloads typed out BY HAND ("shapes copied from
clan_server.py") — so the two halves could drift apart and both suites would
stay green while the live tab rendered blank.

This one closes that seam: it builds a clan world by calling the real server
actions against an in-memory Firestore, takes the payloads the server actually
returns, and feeds those exact bytes to the real js/clans-ui.js in headless
Chrome. Every screen must paint real content — and the empty world (no clans
exist yet, which is what every player sees on launch day) must NOT be blank.

Run:  python3 test_clans_payloads.py     (needs Google Chrome installed)
"""
import copy
import html
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

CHROME = next((p for p in [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome", "/usr/bin/chromium",
] if os.path.exists(p)), None)

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

    def where(self, f, op, v):
        return FakeQuery(self._store, self._path, self._filters + [(f, v)], self._cap)

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

    def get(self, transaction=None):
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

    def where(self, f, op, v):
        return FakeQuery(self._store, self._path).where(f, op, v)

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

    def transaction(self):
        return FakeTxn(self.store)


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, fname))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load("mpsrv_for_clan_payloads", "multiplayer_server.py")
CS = _load("clan_server_payloads", "clan_server.py")

DB = FakeDB()
TMP = tempfile.mkdtemp(prefix="clan_payloads_")
HIST = os.path.join(TMP, "hist"); os.makedirs(HIST)
COMP = os.path.join(TMP, "comp"); os.makedirs(COMP)
SID = "2026-Q3"

CS.init(get_firestore=lambda: DB,
        verify_token=lambda t: {"uid": t} if t else None,
        find_uid_by_username=lambda db, u: None,
        level_progress=M._level_progress_for_total_xp,
        get_season_id=lambda ts=None: SID,
        games_history_dir=HIST, competitive_games_dir=COMP,
        prof_strong_re=M._PROF_STRONG_RE, prof_word_re=M._PROF_WORD_RE,
        prof_leet=M._PROF_LEET, prof_strong=M._PROF_STRONG, prof_words=M._PROF_WORDS)

NICK = {"mia": "Mia", "noah": "Noah", "pia": "Pia", "rex": "Rex",
        "solo": "Solo", "newbie": "Newbie"}


def set_user(uid):
    DB.store["users/" + uid] = {
        "nickname": NICK[uid], "username": NICK[uid].lower(),
        "usernameLower": NICK[uid].lower(), "coins": 0,
        "avatar_url": "/avatars/clownfish.png", "stats": {"total_xp": 500},
    }


def R(uid, action, body=None):
    return CS._route_action(DB, uid, action, body or {}, SID)


def award(cid, uid, n):
    for i in range(n):
        CS._apply_award(DB, cid, uid, NICK[uid], kind="casual_win", points=1,
                        dedup_id=f"{uid}-{i}", activity_text=f"{NICK[uid]} placed 1st",
                        counts_game=True, is_casual_win=True)


for u in NICK:
    set_user(u)

c1 = R("mia", "create", {"name": "Reef Riders", "description": "Chill reef crew.",
                         "icon": "/avatars/clownfish.png", "icon_name": "Clownfish",
                         "privacy": "public"})
CID = c1["clan_id"]
for u in ["noah", "pia", "rex"]:
    R(u, "join", {"clan_id": CID})
R("mia", "role", {"uid": "noah", "role": "captain"})
R("mia", "announce", {"text": "Practice tonight at 8.", "pin": True})
R("mia", "chat-send", {"text": "Welcome to the reef!"})
R("noah", "chat-send", {"text": "Ready when you are."})
R("mia", "events", {"op": "create", "name": "Game Night", "ts": CS._now() + 86400})
for uid, pts in [("mia", 40), ("noah", 25), ("pia", 12), ("rex", 4)]:
    award(CID, uid, pts)

c2 = R("solo", "create", {"name": "Kelp Krew", "description": "Kelp forest crew.",
                          "icon": "/avatars/narwhal.png", "icon_name": "Narwhal",
                          "privacy": "request"})
award(c2["clan_id"], "solo", 18)
CS._lb_invalidate()

PAYLOADS = {
    "/api/clan/home": R("mia", "home"),
    "/api/clan/get": R("mia", "get", {"clan_id": CID}),
    "/api/clan/browse": R("mia", "browse", {"q": ""}),
    "/api/clan/leaderboard": R("mia", "leaderboard"),
    "/api/clan/events": R("mia", "events", {"op": "list"}),
    "/api/clan/chat-get": R("mia", "chat-get", {"clan_id": CID, "since": 0}),
    "/api/clan/season-results": R("mia", "season-results", {}),
}
HOME_CLANLESS = R("newbie", "home")

_PASS = _FAIL = 0


def check(name, cond, extra=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✓ {name}")
    else:
        _FAIL += 1
        print(f"  ✗ FAIL: {name}" + (f"  → {extra}" if extra else ""))


print("server payloads:")
for path, p in PAYLOADS.items():
    check(f"{path} returned ok", isinstance(p, dict) and p.get("ok") is True, json.dumps(p)[:160])
check("/api/clan/home carries a season", bool(PAYLOADS["/api/clan/home"].get("season")))
check("/api/clan/home carries my clan", bool(PAYLOADS["/api/clan/home"].get("my_clan")))
check("clanless home still carries a season", bool(HOME_CLANLESS.get("season")))
check("clanless home has no clan", HOME_CLANLESS.get("my_clan") is None)

if not CHROME:
    print("\nSKIP: no Chrome/Chromium — server payloads checked, render check skipped.")
    print(f"\n{'=' * 46}\nRESULT: {_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)

CSS = open(os.path.join(ROOT, "multiplayer/client/css/preview.css"), encoding="utf8").read()
MOD = open(os.path.join(ROOT, "multiplayer/client/js/clans-ui.js"), encoding="utf8").read()

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>__CSS__</style></head><body>
<div id="auth-stats-lobby" data-bg-tab="clans">
  <div class="ph-panel" id="ph-panel-clans"><div id="cc-clans-root"></div></div>
</div>
<pre id="RESULT"></pre>
<script>
const RESPONSES = __PAYLOADS__;
const HOME_CLANLESS = __CLANLESS__;
window.__ccClans = {
  ENABLED: true, APP_BUILD: "test",
  // The REAL bridge (preview-app's apiPost) hands back an ENVELOPE —
  // { ok, status, data } — where data is the server JSON below. Feeding the
  // payload in bare is what let the blank-tab bug through every suite.
  get:  async (p) => ({ ok: true, status: 200, data: RESPONSES[p] || { ok: true } }),
  post: async (p, b) => ({ ok: true, status: 200, data: RESPONSES[p] || { ok: true } }),
  toast: () => {},
  nickname: () => "Mia",
  authUser: () => ({ uid: "mia", getIdToken: async () => "mia" }),
  idToken: async () => "mia",
  avSrc: (u) => u,
  animalAvatars: () => ([{ id:"clownfish", name:"Clownfish", img:"/avatars/clownfish.png" },
                         { id:"narwhal",   name:"Narwhal",   img:"/avatars/narwhal.png" }]),
  currentRoom: () => "",
};
</script>
<script>__MOD__</script>
<script>
const out = { errors: [], screens: {} };
window.addEventListener("error", e => out.errors.push(String(e.message)));
window.addEventListener("unhandledrejection",
  e => out.errors.push("REJECTION: " + ((e.reason && e.reason.message) || e.reason)));
const R = () => document.getElementById("cc-clans-root");
const q = (s) => document.querySelectorAll(s).length;
const wait = (ms) => new Promise(r => setTimeout(r, ms));
function snap(name) {
  const t = R().innerText || "";
  out.screens[name] = {
    len: t.trim().length,
    bad: ["undefined", "NaN", "[object Object]"].filter(w => t.includes(w)),
    text: t,
    counts: { podium:q(".ccC-pod"), myclan:q(".ccC-myclan"), sec:q(".ccC-sec"),
              stat:q(".ccC-stat"), member:q(".ccC-member"), row:q(".ccC-table tbody tr"),
              msg:q(".ccC-msg"), activity:q(".ccC-activity .row"),
              countdown:(document.querySelector(".ccC-count")||{}).innerText||"" },
  };
}
const tab = (re) => [...document.querySelectorAll(".ph-lb-mode-btn")].find(b => re.test(b.textContent));
const btn = (re) => [...document.querySelectorAll(".ccC-btn")].find(b => re.test(b.textContent));
(async () => {
  try {
    await window.__ccClansRender(); await wait(200); snap("home");
    const my = document.querySelector(".ccC-myclan"); if (my) my.click();
    await wait(300); snap("profile");
    let t;
    t = tab(/Members/i);  if (t) t.click(); await wait(250); snap("members");
    t = tab(/Chat/i);     if (t) t.click(); await wait(400); snap("chat");
    t = tab(/Event/i);    if (t) t.click(); await wait(300); snap("events");
    t = tab(/Activity/i); if (t) t.click(); await wait(250); snap("log");
    const back = btn(/Back/i); if (back) back.click(); await wait(300);
    const lb = btn(/Leaderboard/i); if (lb) lb.click(); await wait(300); snap("leaderboard");
    await window.__ccClansRender(); await wait(250);
    const br = btn(/Find a Clan|Browse/i); if (br) br.click(); await wait(300); snap("browse");
    RESPONSES["/api/clan/home"] = HOME_CLANLESS;
    await window.__ccClansRender(); await wait(300); snap("noclan");
    const mk = btn(/Create a Clan/i); if (mk) mk.click(); await wait(350); snap("create");
    // Launch day: the season is real but nobody has founded a clan yet.
    RESPONSES["/api/clan/home"] = Object.assign({}, HOME_CLANLESS, { top3: [], total_clans: 0 });
    await window.__ccClansRender(); await wait(300); snap("emptyworld");
  } catch (e) { out.errors.push("THREW: " + ((e && e.message) || e)); }
  document.getElementById("RESULT").textContent = "@@" + JSON.stringify(out) + "@@";
})();
</script></body></html>"""

page = (PAGE.replace("__CSS__", CSS)
            .replace("__PAYLOADS__", json.dumps(PAYLOADS, default=str))
            .replace("__CLANLESS__", json.dumps(HOME_CLANLESS, default=str))
            .replace("__MOD__", MOD))
fpath = os.path.join(TMP, "clans_payloads.html")
with open(fpath, "w", encoding="utf8") as fh:
    fh.write(page)

dom = subprocess.run(
    [CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
     "--window-size=1280,900", "--virtual-time-budget=15000", "--dump-dom",
     "file://" + fpath],
    capture_output=True, text=True, timeout=180).stdout
m = re.search(r"@@([\s\S]*?)@@", dom)
if not m:
    print("  ✗ FAIL: the render harness produced no result payload")
    sys.exit(1)
OUT = json.loads(html.unescape(m.group(1)))
S = OUT["screens"]

print("\nreal payloads → real Clans tab:")
check("no script errors", not OUT["errors"], "; ".join(OUT["errors"])[:300])
for name in ["home", "profile", "members", "chat", "events", "log",
             "leaderboard", "browse", "noclan", "create", "emptyworld"]:
    s = S.get(name)
    if not s:
        check(f"{name}: rendered", False, "screen never captured")
        continue
    check(f"{name}: paints real content", s["len"] > 40, f"len={s['len']}")
    check(f"{name}: no placeholder junk", not s["bad"], ",".join(s["bad"]))

check("home: both clans on the podium", S["home"]["counts"]["podium"] == 2,
      str(S["home"]["counts"]["podium"]))
check("home: season countdown filled", any(c.isdigit() for c in S["home"]["counts"]["countdown"]),
      S["home"]["counts"]["countdown"])
check("home: my clan row shown", S["home"]["counts"]["myclan"] >= 1)
check("home: the clan name the server returned", "Reef Riders" in S["home"]["text"])
check("profile: stat tiles rendered", S["profile"]["counts"]["stat"] >= 8,
      str(S["profile"]["counts"]["stat"]))
check("profile: the pinned announcement", "Practice tonight" in S["profile"]["text"])
check("members: a row per member", S["members"]["counts"]["member"] == 4,
      str(S["members"]["counts"]["member"]))
check("members: roles from the server", "Owner" in S["members"]["text"]
      and "Captain" in S["members"]["text"])
check("chat: the messages the server stored", S["chat"]["counts"]["msg"] >= 2,
      str(S["chat"]["counts"]["msg"]))
check("chat: message text carried through", "Welcome to the reef" in S["chat"]["text"])
check("events: the scheduled event", "Game Night" in S["events"]["text"])
check("log: activity rows rendered", S["log"]["counts"]["activity"] >= 3,
      str(S["log"]["counts"]["activity"]))
check("leaderboard: a row per clan", S["leaderboard"]["counts"]["row"] == 2,
      str(S["leaderboard"]["counts"]["row"]))
check("leaderboard: both clans named", "Reef Riders" in S["leaderboard"]["text"]
      and "Kelp Krew" in S["leaderboard"]["text"])
check("browse: clans listed", S["browse"]["counts"]["member"] >= 1
      or "Reef Riders" in S["browse"]["text"])
check("noclan: the join/create call to action", "not in a clan" in S["noclan"]["text"])
check("create: the form", "Clan name" in S["create"]["text"])

# Launch day is the state every real player sees first: a season, no clans.
check("empty world: never blank", S["emptyworld"]["len"] > 40, str(S["emptyworld"]["len"]))
check("empty world: invites you to found the first clan",
      "No clans yet" in S["emptyworld"]["text"])
check("empty world: still offers create",
      "not in a clan" in S["emptyworld"]["text"])
check("empty world: season still counts down",
      any(c.isdigit() for c in S["emptyworld"]["counts"]["countdown"]))

import shutil
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'=' * 46}\nRESULT: {_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
