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

The real Player-Home Clans nav buttons are in the page too (shield glyph and
all), so the other half of the clan critter is proved here as well: the season
vote's winner replaces that glyph, at the glyph's own size, and the shield comes
back when the payload says you're not in a clan.

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

    def get_all(self, refs):
        """Batched multi-document read — the real client has this, and the whole
        clan roster is fetched through it now, so the fake must too or the tests
        only ever cover the one-at-a-time fallback. Real Firestore returns the
        docs in arbitrary order; shuffling here keeps callers honest about that."""
        out = [r.get() for r in refs]
        out.reverse()
        return iter(out)

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


def set_user(uid, icons=("/avatars/clownfish.png", "/avatars/narwhal.png")):
    DB.store["users/" + uid] = {
        "nickname": NICK[uid], "username": NICK[uid].lower(),
        "usernameLower": NICK[uid].lower(), "coins": 0,
        "avatar_url": "/avatars/clownfish.png", "stats": {"total_xp": 500},
        # A clan may only wear a critter one of its members has unlocked.
        "unlocked_icons": list(icons),
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
# Season vote: the winner becomes the clan's icon on the Clans tab. Deliberately
# NOT the clan's own icon (clownfish), so the nav swap can't pass by accident.
for u in ["mia", "noah", "pia"]:
    R(u, "vote-critter", {"icon": "/avatars/narwhal.png"})
R("rex", "vote-critter", {"icon": "/avatars/clownfish.png"})
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
    # The Clan Rules screen is drawn entirely from this, and the server
    # GENERATES it from the same constants that enforce every rule.
    "/api/clan/rules": {"ok": True, "rules": CS.clan_rules()},
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
check("/api/clan/home carries my unlocked critters (the founding choices)",
      "/avatars/narwhal.png" in (PAYLOADS["/api/clan/home"].get("my_unlocked") or []))
_prof = PAYLOADS["/api/clan/get"].get("clan") or {}
check("/api/clan/get carries the clan's critter pool",
      "/avatars/clownfish.png" in (_prof.get("icon_pool") or []))
check("/api/clan/get carries the vote winner",
      _prof.get("favorite_critter") == "/avatars/narwhal.png")
check("/api/clan/get carries the weekly challenge board",
      len(_prof.get("challenges") or []) == len(CS.CLAN_WEEKLY_CHALLENGES))
check("/api/clan/get carries the season challenge board",
      len(_prof.get("season_challenges") or []) == len(CS.CLAN_SEASON_CHALLENGES))
check("every challenge row carries a target the bar can divide by",
      all(int(c.get("target") or 0) > 0
          for c in (_prof.get("challenges") or []) + (_prof.get("season_challenges") or [])))
_rules = PAYLOADS["/api/clan/rules"]["rules"]
check("/api/clan/rules lists all 51 challenges",
      len(_rules["weekly_challenges"]) + len(_rules["season_challenges"]) == 51)

if not CHROME:
    print("\nSKIP: no Chrome/Chromium — server payloads checked, render check skipped.")
    print(f"\n{'=' * 46}\nRESULT: {_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)

CSS = open(os.path.join(ROOT, "multiplayer/client/css/preview.css"), encoding="utf8").read()
MOD = open(os.path.join(ROOT, "multiplayer/client/js/clans-ui.js"), encoding="utf8").read()

# The two Clans nav buttons below are copied verbatim from preview.html (shield-
# and-check glyph and all): the clan's critter has to replace that glyph, at the
# glyph's own size, on every tab — not just while the Clans tab is open.
PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>__CSS__</style></head><body>
<div id="auth-stats-lobby" class="visible" data-bg-tab="clans">
  <div class="ph-sidebar"><div class="ph-sidebar-nav-card">
  <nav class="ph-snav">
    <button class="ph-snav-item" id="snav-clans" data-tab="clans">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 1.5l5.5 2v4c0 3.2-2.3 5.8-5.5 7-3.2-1.2-5.5-3.8-5.5-7v-4L8 1.5z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M5.6 8l1.7 1.7L10.6 6.3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
      Clans
    </button>
  </nav>
  </div></div>
  <div class="ph-tabs">
    <button class="ph-tab" data-tab="clans">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 1.5l5.5 2v4c0 3.2-2.3 5.8-5.5 7-3.2-1.2-5.5-3.8-5.5-7v-4L8 1.5z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M5.6 8l1.7 1.7L10.6 6.3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
      Clans
    </button>
  </div>
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
// What the Clans nav buttons are wearing right now: the shield glyph, or the
// clan's critter at exactly the glyph's size.
function navState() {
  return [...document.querySelectorAll('.ph-snav-item[data-tab="clans"], .ph-tab[data-tab="clans"]')]
    .map(b => {
      const img = b.querySelector("img.ccC-navcritter");
      const svg = b.querySelector("svg");
      const r = img ? img.getBoundingClientRect() : null;
      return { where: b.className, src: img ? img.getAttribute("src") : "",
               w: r ? Math.round(r.width) : 0, h: r ? Math.round(r.height) : 0,
               round: img ? getComputedStyle(img).borderRadius : "",
               fit: img ? getComputedStyle(img).objectFit : "",
               svgShown: !!svg && getComputedStyle(svg).display !== "none" };
    });
}
(async () => {
  try {
    await window.__ccClansRender(); await wait(200); snap("home");
    out.nav = navState();
    const my = document.querySelector(".ccC-myclan"); if (my) my.click();
    await wait(300); snap("profile");
    out.vote = (() => {
      const sec = [...document.querySelectorAll(".ccC-sec")]
        .find(s => /Favorite clan critter/i.test(s.innerText || ""));
      return { found: !!sec, text: sec ? sec.innerText : "",
               tiles: sec ? sec.querySelectorAll(".ccC-iconpick .ic").length : 0,
               names: sec ? [...sec.querySelectorAll(".ccC-iconpick .ic")]
                              .map(t => (t.innerText || "").trim()) : [] };
    })();
    let t;
    t = tab(/Challenges/i); if (t) t.click(); await wait(250); snap("challenges");
    t = tab(/Members/i);  if (t) t.click(); await wait(250); snap("members");
    t = tab(/Chat/i);     if (t) t.click(); await wait(400); snap("chat");
    t = tab(/Event/i);    if (t) t.click(); await wait(300); snap("events");
    t = tab(/Activity/i); if (t) t.click(); await wait(250); snap("log");
    const back = btn(/Back/i); if (back) back.click(); await wait(300);
    const lb = btn(/Leaderboard/i); if (lb) lb.click(); await wait(300); snap("leaderboard");
    await window.__ccClansRender(); await wait(250);
    const br = btn(/Find a Clan|Browse/i); if (br) br.click(); await wait(300); snap("browse");
    // Clan Rules is the first tab of the Clans page, from any screen.
    await window.__ccClansRender(); await wait(250);
    const rt = tab(/Clan Rules/i); if (rt) rt.click(); await wait(300); snap("rules");
    RESPONSES["/api/clan/home"] = HOME_CLANLESS;
    await window.__ccClansRender(); await wait(300); snap("noclan");
    out.navNoClan = navState();     // no clan → the shield has to come back
    const mk = btn(/Create a Clan/i); if (mk) mk.click(); await wait(350); snap("create");
    out.create = { tiles: document.querySelectorAll(".ccC-iconpick .ic").length,
                   text: (document.querySelector(".ccC-iconpick") || {}).parentNode
                         ? document.querySelector(".ccC-iconpick").parentNode.innerText : "" };
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
for name in ["home", "profile", "challenges", "members", "chat", "events", "log",
             "leaderboard", "browse", "rules", "noclan", "create", "emptyworld"]:
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

# ── Clan Rules: the first tab, and it must show the whole rulebook ──────────
_rt = S["rules"]["text"]
check("rules: it is the FIRST tab of the Clans page",
      "Clan Rules" in (S["home"]["text"] or ""))
check("rules: the half-point-vs-bots rule is stated", "0.5" in _rt or ".5 of a Clan Point" in _rt)
check("rules: the real-opponent rule is stated", "registered" in _rt and "non-guest" in _rt)
check("rules: 25 members", "25" in _rt)
check("rules: it says Critter Coins, never bare 'coins'",
      "Critter Coins" in _rt and not re.search(r"(?<!Critter )\bcoins\b", _rt))
check("rules: every weekly challenge is listed",
      all(c["name"] in _rt for c in CS.CLAN_WEEKLY_CHALLENGES),
      str([c["name"] for c in CS.CLAN_WEEKLY_CHALLENGES if c["name"] not in _rt]))
check("rules: every season challenge is listed",
      all(c["name"] in _rt for c in CS.CLAN_SEASON_CHALLENGES),
      str([c["name"] for c in CS.CLAN_SEASON_CHALLENGES if c["name"] not in _rt]))
check("rules: every competitive rank tier and its payout is listed",
      all(t in _rt for t in ["Bronze Barracuda", "Silver Spiny Lobster", "Golden Grouper",
                             "Diamond Dolphin", "Emerald Emperor Penguin",
                             "King of the Critters"])
      and "200 Critter Coins" in _rt and "+50 Clan Points" in _rt)

# ── Challenges tab inside a clan: both ladders, with progress ───────────────
_ct = S["challenges"]["text"]
check("challenges: the weekly board is there", "weekly clan challenges" in _ct.lower())
check("challenges: the season board is there", "season clan challenges" in _ct.lower())
check("challenges: every weekly challenge has a row",
      all(c["name"] in _ct for c in CS.CLAN_WEEKLY_CHALLENGES),
      str([c["name"] for c in CS.CLAN_WEEKLY_CHALLENGES if c["name"] not in _ct]))
check("challenges: every season challenge has a row",
      all(c["name"] in _ct for c in CS.CLAN_SEASON_CHALLENGES),
      str([c["name"] for c in CS.CLAN_SEASON_CHALLENGES if c["name"] not in _ct]))
check("challenges: progress is shown as done/target, never as a bare number",
      re.search(r"\d+/\d+", _ct) is not None)
check("challenges: it says challenge progress counts every finished game",
      "Clan POINTS" in _ct or "real opponent" in _ct)
check("noclan: the join/create call to action", "not in a clan" in S["noclan"]["text"])
# (the lobby is really laid out here, so CSS text-transform reaches innerText)
check("create: the form", "clan name" in S["create"]["text"].lower())

# The clan's critter on the Clans nav button, in place of the shield-and-check
# glyph — the thing a player sees from every OTHER tab.
NAV = OUT.get("nav") or []
check("nav: both Clans buttons found", len(NAV) == 2, str(NAV))
check("nav: both wear the critter that won the vote",
      bool(NAV) and all(b["src"] == "/avatars/narwhal.png" for b in NAV), str(NAV))
check("nav: the shield glyph is hidden underneath",
      bool(NAV) and not any(b["svgShown"] for b in NAV), str(NAV))
check("nav: sidebar critter is the sidebar glyph's 20px",
      any(b["w"] == 20 and b["h"] == 20 for b in NAV), str(NAV))
check("nav: tab-strip critter is that glyph's 16px",
      any(b["w"] == 16 and b["h"] == 16 for b in NAV), str(NAV))
check("nav: every animal is drawn to the same circle (no squashing)",
      bool(NAV) and all(b["fit"] == "cover" and "50%" in (b["round"] or "") for b in NAV), str(NAV))
NAV0 = OUT.get("navNoClan") or []
check("nav: leaving the clan puts the shield back",
      bool(NAV0) and all(not b["src"] and b["svgShown"] for b in NAV0), str(NAV0))

V = OUT.get("vote") or {}
check("vote: the season vote section rendered", V.get("found") is True)
check("vote: it says the winner becomes the tab icon",
      "Clans tab" in (V.get("text") or ""), (V.get("text") or "")[:120])
check("vote: no em dashes in the section", "—" not in (V.get("text") or ""))
check("vote: only critters the clan has unlocked are offered",
      V.get("tiles") == 2, str(V.get("names")))
check("vote: the tally is shown on the tiles",
      any("vote" in (n or "").lower() for n in (V.get("names") or [])), str(V.get("names")))

CR = OUT.get("create") or {}
check("create: offers only the critters I have unlocked", CR.get("tiles") == 2, str(CR))

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
