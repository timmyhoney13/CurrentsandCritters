"""Currents & Critters — Clan System (server-authoritative core).

Wired additively into multiplayer_server (same pattern as tournament_server):
    import clan_server
    clan_server.init(...)                     # in main()
    if clan_server.handle_get(self, parsed):  # in do_GET
    if clan_server.handle_post(self, parsed, body):  # in do_POST
    clan_server.on_trade_completed(db, trade) # from _trade_confirm

Everything lives in Firestore (admin SDK — the browser can never touch these
collections directly, rules stay default-deny):
    clans/{clanId}          one doc per clan: identity, members+roles, season
                            counters, activity log, events, daily goal, XP.
    clan_names/{nameLower}  uniqueness reservation (like usernames/).
    clans/{id}/chat/{mid}   clan chat messages (server-moderated).
    clans/{id}/ledger/{lid} point-award dedup + audit (doc-id create() = the
                            atomic "only once" guarantee per game/trade/user).
    clan_meta/season_{sid}  finalized season results (standings, MVPs, rewards).
    clan_reports/{rid}      player reports (names / chat) for admin review.
    clan_flags/{fid}        suspicious-activity flags for admin review.
    users/{uid}             gains: clan_id, clan_cooldown_until, clan_invites,
                            clan_badges; coins land in stats.critter_coins.

Clan seasons are QUARTERLY — the exact same get_season_id() quarters the
competitive ladder uses (Tim: "each season is three months like how long each
competitive season is"). Seasonal counters are keyed by season id, so a new
quarter starts at zero automatically; the old quarter is finalized lazily
(coins, badges, MVP) the first time any clan endpoint runs inside the new one.

Points can only be claimed for games THIS server recorded (games_history /
competitive_games record files), and trades only via the real /api/trade
completion hook — the client can ask, but never self-report a score.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

# ── Tunables (spec'd values — see the Clan System design doc) ────────────────
CLAN_MAX_MEMBERS          = 20
CLAN_NAME_MIN             = 3
CLAN_NAME_MAX             = 30
CLAN_DESC_MAX             = 240
CLAN_CHAT_MAX             = 500        # chars per message
CLAN_CHAT_FETCH           = 80         # messages returned per poll
CLAN_ACTIVITY_MAX         = 120        # activity-log entries kept on the doc
CLAN_MAX_CUSTOM_ROLES     = 5
CLAN_MAX_EVENTS           = 10
CLAN_INVITE_TTL_SEC       = 7 * 24 * 3600
CLAN_INVITE_MAX           = 10         # pending invites per player

POINTS_COMP_WIN           = 3
POINTS_CASUAL_FIRST       = 2
POINTS_CASUAL_SECOND      = 1          # 0 in a 2-player game
POINTS_CASUAL_THIRD       = 1          # only in games with 4+ players
POINTS_TRADE_DAILY        = 1
WEEKLY_POINT_CAP          = 150
COMP_SAME_OPP_DAILY_CAP   = 3          # comp matches vs same opponent that can score / day
CASUAL_SAME_OPPS_DAILY_CAP = 5         # casual games vs same opponent set / day
JOIN_COOLDOWN_SEC         = 24 * 3600  # after leaving/being removed from a clan
TRADE_BOUNCE_WINDOW_SEC   = 7 * 24 * 3600   # same-items back-and-forth window
TRADE_PAIR_WEEK_FLAG      = 3          # >N completed trades same pair/week → flag

SEASON_REWARD_COINS       = [400, 300, 200]   # 1st / 2nd / 3rd place clans
SEASON_REWARD_MIN_POINTS  = 10         # member contribution needed for the coin reward
SEASON_BORDER_TOP_N       = 10         # top-10 clans get the seasonal border
MVP_MIN_POINTS            = 25
MVP_BONUS_COINS           = 150
MVP_ICON_DAYS             = 14         # MVP chip shown for first 2 weeks of next season
SEASON_KEEP               = 8          # quarters of per-member history kept on a clan doc

DAILY_GOAL_XP             = 25         # clan XP for finishing the shared daily goal
CLAN_XP_PER_LEVEL_STEP    = 100        # level n→n+1 costs 100*n XP

# Season 1 = 2026-Q3 (launch quarter). Names rotate through this ocean roster.
CLAN_SEASON_EPOCH         = (2026, 3)  # (year, quarter)
CLAN_SEASON_NAMES = [
    "Riptide", "Undertow", "High Tide", "Deep Current", "Gulf Stream",
    "Whirlpool", "Tidal Bloom", "Abyssal", "Coral Crest", "Moonlit Tide",
    "Kelp Forest", "Open Water",
]

# Weekly clan challenges: the FRAMEWORK is live (progress, rewards, activity,
# leaderboard columns) but the launch list ships EMPTY — Tim approves the
# exact challenges before they go in (see spec: "created later and presented
# for approval"). Add entries here (or via FISH_CLAN_CHALLENGES env JSON):
#   {"id": "w_games15", "name": "Reef Regulars", "desc": "Complete 15 clan games",
#    "metric": "games", "target": 15, "clan_points": 10, "member_xp": 40,
#    "min_contribution": 1}
# metric ∈ games | points | trades | comp_wins | members_active
CLAN_WEEKLY_CHALLENGES: List[Dict[str, Any]] = []
try:
    _raw = os.environ.get("FISH_CLAN_CHALLENGES", "").strip()
    if _raw:
        _parsed = json.loads(_raw)
        if isinstance(_parsed, list):
            CLAN_WEEKLY_CHALLENGES = [c for c in _parsed if isinstance(c, dict) and c.get("id")]
except Exception as _exc:  # noqa: BLE001
    print(f"[clan] FISH_CLAN_CHALLENGES parse failed: {_exc}")

# The shared daily goal roster; one is picked per clan per UTC day.
DAILY_GOALS = [
    {"id": "games",   "target": 5, "label": "Complete 5 games today"},
    {"id": "points",  "target": 10, "label": "Earn 10 Clan Points today"},
    {"id": "members", "target": 3, "label": "3 different members earn points today"},
    {"id": "trade",   "target": 1, "label": "Complete a clan trade today"},
]

PRIVACY_MODES = ("public", "request", "invite")
CORE_ROLES    = ("owner", "captain", "recruiter", "member")

# Permissions grantable to custom roles. Owner-only powers (delete clan,
# transfer ownership, change the owner's role, promote to owner, membership/
# security settings) are NOT in this list and can never be granted.
CUSTOM_PERMS = (
    "invite", "review_requests", "remove_members", "post_announcements",
    "pin_announcements", "moderate_chat", "create_events",
    "manage_challenges", "change_roles",
)

PRESENCE_FRESH_SEC = 5 * 60

# ── Injected from multiplayer_server.init ────────────────────────────────────
_get_firestore: Callable[[], Any] = lambda: None
_verify_token: Callable[[str], Optional[dict]] = lambda t: None
_find_uid_by_username: Callable[[Any, str], Optional[str]] = lambda db, n: None
_level_progress: Callable[[Any], Tuple[int, int, int]] = lambda xp: (1, 0, 50)
_get_season_id: Callable[..., str] = lambda ts=None: "2026-Q3"
_GAMES_HISTORY_DIR: str = "."
_COMPETITIVE_GAMES_DIR: str = "."
_PROF_STRONG_RE: Optional[Any] = None
_PROF_WORD_RE: Optional[Any] = None
_PROF_LEET: Dict[str, str] = {}
_PROF_STRONG: List[str] = []
_PROF_WORDS: List[str] = []

_FINALIZE_LOCK = threading.Lock()
_FINALIZED_SIDS: set = set()          # in-process cache so we don't re-check every call
# name.lower() → (is_a_real_account, checked_at). Every finished game asks about
# each opponent; without this that's a Firestore query per opponent per game.
_REG_CACHE: Dict[str, Tuple[bool, float]] = {}
_REG_TTL_SEC = 600.0


def init(*, get_firestore, verify_token, find_uid_by_username, level_progress,
         get_season_id, games_history_dir, competitive_games_dir,
         prof_strong_re=None, prof_word_re=None, prof_leet=None,
         prof_strong=None, prof_words=None) -> None:
    global _get_firestore, _verify_token, _find_uid_by_username, _level_progress
    global _get_season_id, _GAMES_HISTORY_DIR, _COMPETITIVE_GAMES_DIR
    global _PROF_STRONG_RE, _PROF_WORD_RE, _PROF_LEET, _PROF_STRONG, _PROF_WORDS
    _get_firestore = get_firestore
    _verify_token = verify_token
    _find_uid_by_username = find_uid_by_username
    _level_progress = level_progress
    _get_season_id = get_season_id
    _GAMES_HISTORY_DIR = games_history_dir
    _COMPETITIVE_GAMES_DIR = competitive_games_dir
    _PROF_STRONG_RE = prof_strong_re
    _PROF_WORD_RE = prof_word_re
    _PROF_LEET = dict(prof_leet or {})
    _PROF_STRONG = list(prof_strong or [])
    _PROF_WORDS = list(prof_words or [])


# ── Small time / season helpers ──────────────────────────────────────────────
def _now() -> int:
    return int(time.time())


def _date_key(ts: Optional[int] = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else _now(), tz=timezone.utc).strftime("%Y-%m-%d")


def _week_key(ts: Optional[int] = None) -> str:
    iso = datetime.fromtimestamp(ts if ts is not None else _now(), tz=timezone.utc).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _sid_parse(sid: str) -> Tuple[int, int]:
    """'2026-Q3' → (2026, 3). Falls back to the epoch on garbage."""
    m = re.match(r"^(\d{4})-Q([1-4])$", str(sid or ""))
    if not m:
        return CLAN_SEASON_EPOCH
    return int(m.group(1)), int(m.group(2))


def _season_number(sid: str) -> int:
    y, q = _sid_parse(sid)
    ey, eq = CLAN_SEASON_EPOCH
    return max(1, (y - ey) * 4 + (q - eq) + 1)


def _season_name(sid: str) -> str:
    return CLAN_SEASON_NAMES[(_season_number(sid) - 1) % len(CLAN_SEASON_NAMES)]


def _season_bounds(sid: str) -> Tuple[int, int]:
    """(start_ts, end_ts) of the quarter, UTC. end_ts is the first second of
    the NEXT quarter (the countdown target and the next season's start)."""
    y, q = _sid_parse(sid)
    start = datetime(y, 3 * (q - 1) + 1, 1, tzinfo=timezone.utc)
    ny, nq = (y + 1, 1) if q == 4 else (y, q + 1)
    end = datetime(ny, 3 * (nq - 1) + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp()), int(end.timestamp())


def _prev_sid(sid: str) -> str:
    y, q = _sid_parse(sid)
    return f"{y - 1}-Q4" if q == 1 else f"{y}-Q{q - 1}"


def _season_public(sid: str) -> Dict[str, Any]:
    start, end = _season_bounds(sid)
    return {
        "id": sid,
        "number": _season_number(sid),
        "name": _season_name(sid),
        "starts_ts": start,
        "ends_ts": end,
        "now": _now(),
    }


# ── Clan name filter ─────────────────────────────────────────────────────────
# Reuses the server profanity roots but hardened for NAMES: leet-map digits and
# symbols back to letters, drop every separator (spaces / punctuation / repeats)
# and collapse repeated letters, then look for the roots in the collapsed form.
# This catches "S h 1 t  Sq_u-a-d", "b!tchz", "fuuuuck" etc. Word-roots (ass,
# hell…) are only matched as whole words of the ORIGINAL text so "Bass Reef" and
# "Shellfish Crew" stay legal (the Scunthorpe rule).
_LEET_TO_LETTER: Dict[str, str] = {}
def _leet_map() -> Dict[str, str]:
    global _LEET_TO_LETTER
    if not _LEET_TO_LETTER:
        m: Dict[str, str] = {}
        for letter, alts in (_PROF_LEET or {}).items():
            for ch in alts:
                m.setdefault(ch, letter)
        _LEET_TO_LETTER = m
    return _LEET_TO_LETTER


def _collapse_for_filter(text: str) -> str:
    low = str(text or "").lower()
    mapped = "".join(_leet_map().get(ch, ch) for ch in low)
    letters = re.sub(r"[^a-z]", "", mapped)          # drop spaces, digits-left, punctuation
    return re.sub(r"(.)\1{2,}", r"\1\1", letters)     # coooool → cool-ish (keep doubles)


def text_is_profane(text: str) -> bool:
    """Profanity core, shared by names / descriptions / role & event names.
    Any length — never gate this behind a length check."""
    raw = str(text or "")
    if not raw.strip():
        return False
    # 1) the shared strong/word regexes over the raw text (leet + separator aware)
    if _PROF_STRONG_RE is not None and _PROF_STRONG_RE.search(raw):
        return True
    if _PROF_WORD_RE is not None and _PROF_WORD_RE.search(raw):
        return True
    # 2) collapsed-form check: strips ALL separators/leet, then substring-scan
    #    the strong roots and repeat-collapsed variants of them.
    collapsed = _collapse_for_filter(raw)
    doubled_gone = re.sub(r"(.)\1+", r"\1", collapsed)
    for root in _PROF_STRONG:
        r = re.sub(r"(.)\1+", r"\1", root)
        if root in collapsed or r in doubled_gone:
            return True
    # 3) word roots as whole tokens of the leet-mapped text
    tokens = re.split(r"[^a-z]+", "".join(_leet_map().get(c, c) for c in raw.lower()))
    bad_words = set(_PROF_WORDS)
    for tok in tokens:
        t = re.sub(r"(.)\1+", r"\1", tok)
        if tok in bad_words or t in bad_words:
            return True
    return False


def censor_text(text: str) -> str:
    """Mask swears in free text (clan chat / announcements), same regexes as
    the room-chat filter in multiplayer_server."""
    out = str(text or "")
    if not out:
        return out
    if _PROF_STRONG_RE is not None:
        out = _PROF_STRONG_RE.sub(lambda m: "*" * len(m.group(0)), out)
    if _PROF_WORD_RE is not None:
        out = _PROF_WORD_RE.sub(lambda m: m.group(1) + "*" * len(m.group(2)), out)
    return out


def clan_name_check(name: str) -> Tuple[bool, str]:
    """(ok, reason). reason ∈ '', 'length', 'charset', 'inappropriate'."""
    raw = str(name or "").strip()
    if len(raw) < CLAN_NAME_MIN or len(raw) > CLAN_NAME_MAX:
        return False, "length"
    if not re.match(r"^[A-Za-z0-9 .,'&!\-]+$", raw):
        return False, "charset"
    if not re.search(r"[A-Za-z]", raw):
        return False, "charset"
    if text_is_profane(raw):
        return False, "inappropriate"
    return True, ""


# ── Firestore shorthands ─────────────────────────────────────────────────────
def _clans(db):
    return db.collection("clans")


def _users(db):
    return db.collection("users")


def _txn_helpers():
    from firebase_admin import firestore
    transactional = getattr(firestore, "transactional", None)
    if transactional is None:
        from google.cloud.firestore_v1 import transactional  # type: ignore
    return transactional


def _new_clan_id() -> str:
    return "c" + secrets.token_hex(5)


def _field_delete() -> Any:
    """Firestore's DELETE_FIELD sentinel, or "" when unavailable.

    set(merge=True) MERGES nested maps — dropping a key from the dict you write
    does NOT remove it from the stored doc. Anything that must actually go away
    has to be written as this sentinel. Readers treat "" as absent."""
    try:
        from firebase_admin import firestore as _fs
        sentinel = getattr(_fs, "DELETE_FIELD", None)
        if sentinel is not None:
            return sentinel
    except Exception:
        pass
    return ""


def _clan_level(xp: int) -> Dict[str, int]:
    """Triangular curve: level n→n+1 costs 100*n XP."""
    xp = max(0, int(xp or 0))
    level, spent = 1, 0
    while xp - spent >= CLAN_XP_PER_LEVEL_STEP * level:
        spent += CLAN_XP_PER_LEVEL_STEP * level
        level += 1
    return {"level": level, "xp": xp, "into": xp - spent,
            "next": CLAN_XP_PER_LEVEL_STEP * level}


def _season_slot(clan: Dict[str, Any], sid: str) -> Dict[str, Any]:
    seasons = clan.setdefault("seasons", {})
    slot = seasons.setdefault(sid, {})
    slot.setdefault("points", 0)
    slot.setdefault("comp_wins", 0)
    slot.setdefault("comp_losses", 0)
    slot.setdefault("casual_wins", 0)
    slot.setdefault("games", 0)
    slot.setdefault("trade_points", 0)
    slot.setdefault("challenge_points", 0)
    slot.setdefault("challenges_completed", 0)
    slot.setdefault("contrib", {})
    slot.setdefault("critter_votes", {})
    slot.setdefault("win_streak", 0)
    slot.setdefault("last_gain_ts", 0)
    return slot


def _contrib_slot(slot: Dict[str, Any], uid: str, name: str) -> Dict[str, Any]:
    c = slot["contrib"].setdefault(uid, {})
    c.setdefault("name", name or c.get("name") or "Player")
    if name:
        c["name"] = name
    c.setdefault("points", 0)
    c.setdefault("game_points", 0)
    c.setdefault("trade_points", 0)
    c.setdefault("challenge_points", 0)
    c.setdefault("comp_wins", 0)
    c.setdefault("casual_wins", 0)
    c.setdefault("challenges_done", 0)
    c.setdefault("weekly", {})
    c.setdefault("days_active", 0)
    c.setdefault("last_active_date", "")
    c.setdefault("last_trade_date", "")
    c.setdefault("opp_date", "")
    c.setdefault("opp_counts", {})
    return c


def _activity_push(clan: Dict[str, Any], type_: str, text: str) -> None:
    log = clan.setdefault("activity", [])
    log.insert(0, {"ts": _now(), "type": type_, "text": str(text)[:200]})
    del log[CLAN_ACTIVITY_MAX:]


def _chat_system(db, clan_id: str, text: str) -> None:
    """Best-effort system line into the clan chat (never raises)."""
    try:
        _clans(db).document(clan_id).collection("chat").document(f"m{_now()}_{secrets.token_hex(3)}").set({
            "ts": _now(), "uid": "", "name": "", "kind": "system",
            "text": str(text)[:CLAN_CHAT_MAX],
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] system chat write failed: {exc}")


def _daily_goal_for(clan_id: str, date_key: str) -> Dict[str, Any]:
    h = int(hashlib.md5(f"{clan_id}:{date_key}".encode()).hexdigest(), 16)
    return dict(DAILY_GOALS[h % len(DAILY_GOALS)])


def _daily_slot(clan: Dict[str, Any], clan_id: str) -> Dict[str, Any]:
    today = _date_key()
    d = clan.get("daily") or {}
    if d.get("date") != today:
        d = {"date": today, "goal": _daily_goal_for(clan_id, today),
             "games": 0, "points": 0, "trades": 0, "contributors": {}, "done": False}
    d.setdefault("goal", _daily_goal_for(clan_id, today))
    clan["daily"] = d
    return d


def _weekly_slot(clan: Dict[str, Any]) -> Dict[str, Any]:
    wk = _week_key()
    w = clan.get("weekly") or {}
    if w.get("week") != wk:
        w = {"week": wk, "games": 0, "points": 0, "trades": 0, "comp_wins": 0,
             "contributors": {}, "challenges_done": []}
    clan["weekly"] = w
    return w


def _daily_goal_progress(daily: Dict[str, Any]) -> int:
    gid = (daily.get("goal") or {}).get("id")
    if gid == "games":
        return int(daily.get("games") or 0)
    if gid == "points":
        return int(daily.get("points") or 0)
    if gid == "members":
        return len(daily.get("contributors") or {})
    if gid == "trade":
        return int(daily.get("trades") or 0)
    return 0


def _challenge_metric(weekly: Dict[str, Any], metric: str) -> int:
    if metric == "members_active":
        return len(weekly.get("contributors") or {})
    return int(weekly.get(metric) or 0)


# ── The one award engine ─────────────────────────────────────────────────────
# Every point ever granted flows through _apply_award inside a Firestore
# transaction: dedup ledger create + clan season/contrib counters + weekly cap
# + daily goal + weekly challenge sweep + activity, all-or-nothing.
def _apply_award(db, clan_id: str, uid: str, name: str, *, kind: str,
                 points: int, dedup_id: str, activity_text: str,
                 counts_game: bool = False, is_comp_win: bool = False,
                 is_comp_loss: bool = False, is_casual_win: bool = False,
                 is_trade: bool = False, meta: Optional[Dict[str, Any]] = None
                 ) -> Dict[str, Any]:
    transactional = _txn_helpers()
    clan_ref = _clans(db).document(clan_id)
    ledger_ref = clan_ref.collection("ledger").document(dedup_id)
    txn = db.transaction()
    sid = _get_season_id()
    wk = _week_key()
    today = _date_key()
    out: Dict[str, Any] = {}

    @transactional
    def _run(t) -> Dict[str, Any]:
        led = ledger_ref.get(transaction=t)
        if led.exists:
            return {"ok": False, "error": "already_claimed"}
        snap = clan_ref.get(transaction=t)
        if not snap.exists:
            return {"ok": False, "error": "no_clan"}
        clan = snap.to_dict() or {}
        if uid not in (clan.get("members") or {}):
            return {"ok": False, "error": "not_member"}

        slot = _season_slot(clan, sid)
        contrib = _contrib_slot(slot, uid, name)
        weekly_used = int(contrib["weekly"].get(wk) or 0)
        granted = max(0, min(int(points), WEEKLY_POINT_CAP - weekly_used))
        capped = granted < int(points)

        # Season + contributor counters (wins/losses/games record even when the
        # points themselves were capped or zero — "Clan wins and losses are
        # still recorded in clan statistics").
        if counts_game:
            slot["games"] = int(slot.get("games") or 0) + 1
        if is_comp_win:
            slot["comp_wins"] = int(slot.get("comp_wins") or 0) + 1
            contrib["comp_wins"] = int(contrib.get("comp_wins") or 0) + 1
        if is_comp_loss:
            slot["comp_losses"] = int(slot.get("comp_losses") or 0) + 1
        if is_casual_win:
            slot["casual_wins"] = int(slot.get("casual_wins") or 0) + 1
            contrib["casual_wins"] = int(contrib.get("casual_wins") or 0) + 1
        if is_comp_win or is_casual_win:
            slot["win_streak"] = int(slot.get("win_streak") or 0) + 1
        elif counts_game:
            slot["win_streak"] = 0

        if granted > 0:
            slot["points"] = int(slot.get("points") or 0) + granted
            slot["last_gain_ts"] = _now()
            contrib["points"] = int(contrib.get("points") or 0) + granted
            contrib["weekly"][wk] = weekly_used + granted
            if is_trade:
                slot["trade_points"] = int(slot.get("trade_points") or 0) + granted
                contrib["trade_points"] = int(contrib.get("trade_points") or 0) + granted
            else:
                contrib["game_points"] = int(contrib.get("game_points") or 0) + granted
            life = clan.setdefault("lifetime", {})
            life["points"] = int(life.get("points") or 0) + granted
        if is_trade:
            contrib["last_trade_date"] = today

        # Days-active streak for the contributor (drives "most active member").
        if contrib.get("last_active_date") != today:
            contrib["last_active_date"] = today
            contrib["days_active"] = int(contrib.get("days_active") or 0) + 1

        # Shared daily goal progress.
        daily = _daily_slot(clan, clan_id)
        if counts_game:
            daily["games"] = int(daily.get("games") or 0) + 1
        if is_trade:
            daily["trades"] = int(daily.get("trades") or 0) + 1
        if granted > 0:
            daily["points"] = int(daily.get("points") or 0) + granted
            daily.setdefault("contributors", {})[uid] = True
        goal_done_now = False
        if not daily.get("done") and _daily_goal_progress(daily) >= int((daily.get("goal") or {}).get("target") or 9999):
            daily["done"] = True
            goal_done_now = True
            clan["xp"] = int(clan.get("xp") or 0) + DAILY_GOAL_XP
            _activity_push(clan, "daily_goal",
                           f"🌞 Daily goal complete: {(daily.get('goal') or {}).get('label')} (+{DAILY_GOAL_XP} Clan XP)")

        # Weekly counters + challenge sweep.
        weekly = _weekly_slot(clan)
        if counts_game:
            weekly["games"] = int(weekly.get("games") or 0) + 1
        if is_trade:
            weekly["trades"] = int(weekly.get("trades") or 0) + 1
        if is_comp_win:
            weekly["comp_wins"] = int(weekly.get("comp_wins") or 0) + 1
        if granted > 0:
            weekly["points"] = int(weekly.get("points") or 0) + granted
            wc = weekly.setdefault("contributors", {})
            wc[uid] = int(wc.get(uid) or 0) + granted
        challenges_done_now: List[Dict[str, Any]] = []
        done_list = weekly.setdefault("challenges_done", [])
        for ch in CLAN_WEEKLY_CHALLENGES:
            cid = str(ch.get("id"))
            if cid in done_list:
                continue
            if _challenge_metric(weekly, str(ch.get("metric") or "")) >= int(ch.get("target") or 9999):
                done_list.append(cid)
                cp = int(ch.get("clan_points") or 0)
                slot["challenge_points"] = int(slot.get("challenge_points") or 0) + cp
                slot["challenges_completed"] = int(slot.get("challenges_completed") or 0) + 1
                if cp:
                    slot["points"] = int(slot.get("points") or 0) + cp
                    slot["last_gain_ts"] = _now()
                _activity_push(clan, "challenge",
                               f"🏁 Weekly challenge complete: {ch.get('name')} (+{cp} Clan Points)")
                challenges_done_now.append(dict(ch))

        if activity_text:
            suffix = " (weekly cap reached)" if (capped and granted == 0) else ""
            _activity_push(clan, kind, activity_text.replace("{pts}", str(granted)) + suffix)

        t.set(ledger_ref, {
            "ts": _now(), "uid": uid, "name": name, "kind": kind,
            "points": granted, "requested": int(points), "week": wk,
            "date": today, "season": sid, "meta": meta or {},
        })
        t.set(clan_ref, clan)
        return {"ok": True, "granted": granted, "capped": capped,
                "goal_done": goal_done_now, "challenges_done": challenges_done_now,
                "clan_name": clan.get("name"), "season_points": slot["points"]}

    try:
        out = _run(txn)
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] award txn failed ({kind} {dedup_id}): {exc}")
        return {"ok": False, "error": "award_failed"}
    if out.get("ok"):
        _lb_invalidate()
        if out.get("goal_done"):
            _chat_system(db, clan_id, "🌞 Today's clan goal is complete! +25 Clan XP")
        for ch in out.get("challenges_done") or []:
            _chat_system(db, clan_id, f"🏁 Weekly challenge complete: {ch.get('name')}!")
            _grant_challenge_xp(db, clan_id, ch)
    return out


def _grant_challenge_xp(db, clan_id: str, ch: Dict[str, Any]) -> None:
    """Member XP for a finished weekly challenge — only members whose weekly
    contribution meets the challenge's min_contribution (individual
    participation required; inactive members get nothing)."""
    xp = int(ch.get("member_xp") or 0)
    need = int(ch.get("min_contribution") or 1)
    if xp <= 0:
        return
    try:
        snap = _clans(db).document(clan_id).get()
        clan = snap.to_dict() or {}
        weekly = clan.get("weekly") or {}
        for uid, pts in (weekly.get("contributors") or {}).items():
            if int(pts or 0) < need:
                continue
            uref = _users(db).document(uid)
            usnap = uref.get()
            stats = ((usnap.to_dict() or {}).get("stats") or {}) if usnap.exists else {}
            new_xp = int(stats.get("total_xp") or 0) + xp
            lvl, xp_cur, xp_goal = _level_progress(new_xp)
            uref.set({"stats": {
                "total_xp": new_xp, "level": lvl, "player_level": lvl,
                "xp_current": xp_cur, "level_xp_current": xp_cur,
                "xp_goal": xp_goal, "level_xp_goal": xp_goal,
            }}, merge=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] challenge XP grant failed: {exc}")


# ── Membership / permission engine ───────────────────────────────────────────
def _member_of(clan: Dict[str, Any], uid: str) -> Optional[Dict[str, Any]]:
    return (clan.get("members") or {}).get(uid)


def _role_rank(clan: Dict[str, Any], mem: Optional[Dict[str, Any]]) -> int:
    if not mem:
        return 0
    return {"owner": 4, "captain": 3, "recruiter": 2, "member": 1}.get(mem.get("role"), 1)


def _custom_perms(clan: Dict[str, Any], mem: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    if not mem or not mem.get("custom_role_id"):
        return {}
    for r in clan.get("custom_roles") or []:
        if r.get("id") == mem.get("custom_role_id"):
            return dict(r.get("perms") or {})
    return {}


def _has_perm(clan: Dict[str, Any], uid: str, perm: str) -> bool:
    """One permission gate for every moderated action. Owner: everything.
    Captain: invite/review/remove/announce/pin/moderate/events/challenges/roles.
    Recruiter: invite + review. Custom roles: exactly their granted perms."""
    mem = _member_of(clan, uid)
    if not mem:
        return False
    role = mem.get("role")
    if role == "owner":
        return True
    if role == "captain":
        if perm in ("invite", "review_requests", "remove_members",
                    "post_announcements", "pin_announcements", "moderate_chat",
                    "create_events", "manage_challenges", "change_roles"):
            return True
        if perm == "edit_custom_roles":
            return bool(clan.get("captains_can_edit_roles"))
        return False
    if role == "recruiter" and perm in ("invite", "review_requests"):
        return True
    return bool(_custom_perms(clan, mem).get(perm))


# ── Public shapers ───────────────────────────────────────────────────────────
def _clan_card(clan_id: str, clan: Dict[str, Any], sid: str) -> Dict[str, Any]:
    slot = (clan.get("seasons") or {}).get(sid) or {}
    return {
        "id": clan_id,
        "name": clan.get("name"),
        "icon": clan.get("icon"),
        "icon_name": clan.get("icon_name"),
        "description": clan.get("description") or "",
        "privacy": clan.get("privacy"),
        "member_count": len(clan.get("members") or {}),
        "max_members": CLAN_MAX_MEMBERS,
        "points": int(slot.get("points") or 0),
        "comp_wins": int(slot.get("comp_wins") or 0),
        "casual_wins": int(slot.get("casual_wins") or 0),
        "challenge_points": int(slot.get("challenge_points") or 0),
        "challenges_completed": int(slot.get("challenges_completed") or 0),
        "trade_points": int(slot.get("trade_points") or 0),
        "games": int(slot.get("games") or 0),
        "comp_losses": int(slot.get("comp_losses") or 0),
        "level": _clan_level(int(clan.get("xp") or 0))["level"],
        "season_border": clan.get("season_border"),
    }


def _lb_sort_key(card: Dict[str, Any]) -> Tuple:
    """Season ranking with the spec'd tiebreakers: points, comp wins,
    challenges completed, casual wins, distinct contributors, first-to-score."""
    return (-card["points"], -card["comp_wins"], -card["challenges_completed"],
            -card["casual_wins"], -card.get("_contributors", 0), card.get("_last_gain_ts", 1 << 60))


_LB_CACHE: Dict[str, Any] = {"sid": "", "at": 0.0, "rows": []}
_LB_TTL_SEC = 20.0


def _lb_invalidate() -> None:
    """Drop the standings cache. Called after every write that can change what
    the leaderboard shows, so a brand-new clan (or a just-earned point) is
    never missing from the board the player looks at one second later."""
    _LB_CACHE["at"] = 0.0


def _leaderboard_rows(db, sid: str, cap: int = 500, fresh: bool = False) -> List[Dict[str, Any]]:
    """Season standings for every clan. This is a whole-collection read, and
    the Clans page, the leaderboard and each profile all want it — so results
    are cached briefly. Anything that must observe its own write passes
    fresh=True (nothing does today; points land well inside the TTL)."""
    now = time.time()
    if (not fresh and _LB_CACHE["sid"] == sid
            and now - float(_LB_CACHE["at"] or 0) < _LB_TTL_SEC):
        return copy.deepcopy(_LB_CACHE["rows"])
    rows: List[Dict[str, Any]] = []
    try:
        for doc in _clans(db).limit(cap).stream():
            clan = doc.to_dict() or {}
            card = _clan_card(doc.id, clan, sid)
            slot = (clan.get("seasons") or {}).get(sid) or {}
            contrib = slot.get("contrib") or {}
            card["_contributors"] = sum(1 for c in contrib.values() if int(c.get("points") or 0) > 0)
            card["_last_gain_ts"] = int(slot.get("last_gain_ts") or 0) or (1 << 60)
            rows.append(card)
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] leaderboard scan failed: {exc}")
    rows.sort(key=_lb_sort_key)
    wl_record = lambda c: f"{c['comp_wins'] + c['casual_wins']}-{max(0, c['games'] - c['comp_wins'] - c['casual_wins'])}"
    for i, c in enumerate(rows):
        c["rank"] = i + 1
        c["record"] = wl_record(c)
        c.pop("_contributors", None)
        c.pop("_last_gain_ts", None)
    _LB_CACHE.update({"sid": sid, "at": now, "rows": copy.deepcopy(rows)})
    return rows


# ── Season finalize (lazy, idempotent) ───────────────────────────────────────
def ensure_season_finalized(db) -> None:
    """First clan call inside a new quarter finalizes the previous one:
    snapshot standings, pay coin rewards to eligible members of the top 3,
    stamp badges/borders, pick each clan's MVP, write clan_meta/season_<sid>.
    The meta doc's create() is the cross-process idempotency lock."""
    sid_now = _get_season_id()
    prev = _prev_sid(sid_now)
    if prev in _FINALIZED_SIDS:
        return
    with _FINALIZE_LOCK:
        if prev in _FINALIZED_SIDS:
            return
        meta_ref = db.collection("clan_meta").document(f"season_{prev}")
        try:
            if meta_ref.get().exists:
                _FINALIZED_SIDS.add(prev)
                return
        except Exception:
            return
        # Anything to finalize at all? (Fresh install: prev quarter has no data.)
        rows = _leaderboard_rows(db, prev, fresh=True)   # paying out — never off a cache
        rows = [r for r in rows if r["points"] > 0 or r["games"] > 0]
        try:
            meta_ref.create({"finalizing": True, "ts": _now()})
        except Exception:
            _FINALIZED_SIDS.add(prev)   # someone else won the race
            return
        results: List[Dict[str, Any]] = []
        try:
            start, end = _season_bounds(prev)
            for r in rows:
                clan_ref = _clans(db).document(r["id"])
                snap = clan_ref.get()
                if not snap.exists:
                    continue
                clan = snap.to_dict() or {}
                slot = (clan.get("seasons") or {}).get(prev) or {}
                contrib: Dict[str, Any] = slot.get("contrib") or {}
                members: Dict[str, Any] = clan.get("members") or {}
                place = r["rank"]
                coins_per = SEASON_REWARD_COINS[place - 1] if place <= 3 else 0
                rewarded: Dict[str, int] = {}
                badges: List[str] = []
                # Coin + badge rewards: current members only, ≥10 season points.
                for uid, c in contrib.items():
                    if uid not in members:
                        continue      # left the clan → no seasonal rewards
                    if int(c.get("points") or 0) < SEASON_REWARD_MIN_POINTS:
                        continue
                    updates: Dict[str, Any] = {}
                    if coins_per:
                        rewarded[uid] = coins_per
                    if place <= 3:
                        badges.append(uid)
                    try:
                        uref = _users(db).document(uid)
                        usnap = uref.get()
                        udoc = usnap.to_dict() or {}
                        stats = udoc.get("stats") or {}
                        if coins_per:
                            updates["stats"] = {"critter_coins": int(stats.get("critter_coins") or 0) + coins_per}
                        if place <= 3:
                            blist = [b for b in (udoc.get("clan_badges") or []) if isinstance(b, dict)]
                            blist.append({"type": "season", "place": place,
                                          "season": _season_number(prev), "sid": prev,
                                          "clan": clan.get("name"), "ts": _now()})
                            updates["clan_badges"] = blist
                        if updates:
                            uref.set(updates, merge=True)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[clan] finalize reward for {uid} failed: {exc}")
                # MVP: highest-points CURRENT member, ≥25 pts, not mainly trades,
                # no confirmed violations. Tiebreak: game points → comp wins →
                # challenges → days active.
                mvp = None
                cands = []
                for uid, c in contrib.items():
                    if uid not in members:
                        continue
                    pts = int(c.get("points") or 0)
                    if pts < MVP_MIN_POINTS:
                        continue
                    if int(c.get("trade_points") or 0) * 2 > pts:
                        continue      # majority-trades can't take MVP
                    if c.get("violations_confirmed"):
                        continue
                    cands.append((uid, c))
                cands.sort(key=lambda uc: (
                    -int(uc[1].get("points") or 0),
                    -int(uc[1].get("game_points") or 0),
                    -int(uc[1].get("comp_wins") or 0),
                    -int(uc[1].get("challenges_done") or 0),
                    -int(uc[1].get("days_active") or 0)))
                if cands:
                    uid, c = cands[0]
                    mvp = {"uid": uid, "name": c.get("name") or "Player",
                           "points": int(c.get("points") or 0)}
                    try:
                        uref = _users(db).document(uid)
                        usnap = uref.get()
                        udoc = usnap.to_dict() or {}
                        stats = udoc.get("stats") or {}
                        blist = [b for b in (udoc.get("clan_badges") or []) if isinstance(b, dict)]
                        blist.append({"type": "mvp", "season": _season_number(prev),
                                      "sid": prev, "clan": clan.get("name"),
                                      "title": f"Season {_season_number(prev)} Clan MVP",
                                      "ts": _now()})
                        uref.set({"clan_badges": blist,
                                  "stats": {"critter_coins": int(stats.get("critter_coins") or 0) + MVP_BONUS_COINS}},
                                 merge=True)
                        rewarded[uid] = int(rewarded.get(uid) or 0) + MVP_BONUS_COINS
                    except Exception as exc:  # noqa: BLE001
                        print(f"[clan] finalize MVP reward failed: {exc}")
                # Most-active + best-comp member for the results screen.
                most_active = max(contrib.items(),
                                  key=lambda uc: int(uc[1].get("days_active") or 0),
                                  default=(None, None))
                top_comp = max(contrib.items(),
                               key=lambda uc: int(uc[1].get("comp_wins") or 0),
                               default=(None, None))
                entry = {
                    "clan_id": r["id"], "name": clan.get("name"), "icon": clan.get("icon"),
                    "rank": place, "points": r["points"], "comp_wins": r["comp_wins"],
                    "casual_wins": r["casual_wins"], "challenge_points": r["challenge_points"],
                    "challenges_completed": r["challenges_completed"],
                    "trade_points": r["trade_points"], "games": r["games"],
                    "record": r["record"], "member_count": r["member_count"],
                    "mvp": mvp, "coins_per_member": coins_per,
                    "rewarded": rewarded,
                    "most_active": ({"uid": most_active[0], "name": (most_active[1] or {}).get("name"),
                                     "days": int((most_active[1] or {}).get("days_active") or 0)}
                                    if most_active[0] else None),
                    "top_comp": ({"uid": top_comp[0], "name": (top_comp[1] or {}).get("name"),
                                  "wins": int((top_comp[1] or {}).get("comp_wins") or 0)}
                                 if top_comp[0] else None),
                }
                results.append(entry)
                # Stamp the clan doc: previous-season snapshot, MVP chip window,
                # top-10 border, activity note.
                stamp: Dict[str, Any] = {}
                prev_results = clan.get("prev_results") or {}
                prev_results[prev] = {k: entry[k] for k in
                                      ("rank", "points", "mvp", "coins_per_member", "record")}
                stamp["prev_results"] = prev_results
                if place <= SEASON_BORDER_TOP_N:
                    stamp["season_border"] = {"sid": prev, "rank": place,
                                              "season": _season_number(prev)}
                if mvp:
                    stamp["mvp_chip"] = {"uid": mvp["uid"], "name": mvp["name"],
                                         "sid": prev, "season": _season_number(prev),
                                         "until": end + MVP_ICON_DAYS * 24 * 3600}
                _activity_push(clan, "season",
                               f"🏆 Season {_season_number(prev)} final: #{place}"
                               + (f" — rewards paid to {len(rewarded)} member(s)" if rewarded else ""))
                stamp["activity"] = clan.get("activity")
                # Keep the doc from growing forever: only the last SEASON_KEEP
                # quarters of per-member breakdowns and placements stay on the
                # clan. Older finals live on in clan_meta/season_<sid>.
                # merge=True MERGES nested maps (it never drops keys), so the
                # retired quarters have to be explicit field deletes.
                delete_sentinel = _field_delete()
                for field, data in (("seasons", clan.get("seasons") or {}),
                                    ("prev_results", prev_results)):
                    if len(data) <= SEASON_KEEP:
                        continue
                    drop = sorted(data.keys(), reverse=True)[SEASON_KEEP:]
                    merged = dict(stamp.get(field) or {})
                    for old_sid in drop:
                        merged[old_sid] = delete_sentinel
                    stamp[field] = merged
                clan_ref.set(stamp, merge=True)
                _chat_system(db, r["id"],
                             f"🏆 Season {_season_number(prev)} ({_season_name(prev)}) finished — "
                             f"{clan.get('name')} placed #{place}!"
                             + (f" MVP: {mvp['name']} 🎖" if mvp else ""))
            meta_ref.set({
                "sid": prev, "season": _season_number(prev), "name": _season_name(prev),
                "finalized": True, "finalizing": False, "ts": _now(),
                "standings": results[:100],
                "next_season": _season_public(sid_now),
            })
            _FINALIZED_SIDS.add(prev)
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] season finalize failed: {exc}")
            try:
                meta_ref.set({"finalizing": False, "error": str(exc)[:200]}, merge=True)
            except Exception:
                pass


# ── Game claims ──────────────────────────────────────────────────────────────
def _latest_record(dir_path: str, room_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Newest game_<room>_<ts>.json record for a room → (record_id, record)."""
    rid = str(room_id or "").strip().upper()
    if not rid or not os.path.isdir(dir_path):
        return None
    best: Optional[Tuple[int, str]] = None
    try:
        for fname in os.listdir(dir_path):
            if not (fname.startswith(f"game_{rid}_") and fname.endswith(".json")):
                continue
            try:
                ts = int(fname[len(f"game_{rid}_"):-len(".json")])
            except ValueError:
                continue
            if best is None or ts > best[0]:
                best = (ts, fname)
    except OSError:
        return None
    if not best:
        return None
    try:
        with open(os.path.join(dir_path, best[1]), "r", encoding="utf-8") as f:
            return best[1][:-len(".json")], json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _names_match(a: str, b: str) -> bool:
    return str(a or "").strip().lower() == str(b or "").strip().lower()


def _is_registered(db, name: str) -> bool:
    """Is this in-game name a real (non-guest) account?

    Players are shown by NICKNAME in a game, and plenty of accounts have a
    nickname that differs from their unique username — matching on username
    alone would quietly refuse Clan Points for perfectly normal games. So we
    accept either. A nickname isn't unique, but this is only ever used as
    "a real account played here", never to decide WHO gets credited, so the
    worst case is a guest who copied a real player's nickname."""
    nm = str(name or "").strip()
    if not nm:
        return False
    if _find_uid_by_username(db, nm.lower()) is not None:
        return True
    key = nm.lower()
    if key in _REG_CACHE and time.time() - _REG_CACHE[key][1] < _REG_TTL_SEC:
        return _REG_CACHE[key][0]
    found = False
    try:
        for field in ("nickname", "usernameLower", "username"):
            value = key if field == "usernameLower" else nm
            try:
                from google.cloud.firestore_v1 import FieldFilter
                q = _users(db).where(filter=FieldFilter(field, "==", value)).limit(1)
            except Exception:
                q = _users(db).where(field, "==", value).limit(1)
            if any(True for _ in q.stream()):
                found = True
                break
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] registered-name lookup failed: {exc}")
        return False
    _REG_CACHE[key] = (found, time.time())
    return found


def _cooldown_active(udoc: Dict[str, Any]) -> int:
    until = int(udoc.get("clan_cooldown_until") or 0)
    return until if until > _now() else 0


def claim_game_points(uid: str, room_id: str) -> Dict[str, Any]:
    """Award Clan Points for a finished game THIS server recorded. Called by
    the client at the end screen; every rule (placement points, real-player
    minimums, same-opponent daily caps, weekly cap, join cooldown, one claim
    per game) is enforced here against the server's own record."""
    db = _get_firestore()
    if db is None:
        return {"ok": False, "error": "firestore_unavailable"}
    ensure_season_finalized(db)
    usnap = _users(db).document(uid).get()
    udoc = usnap.to_dict() or {} if usnap.exists else {}
    clan_id = str(udoc.get("clan_id") or "")
    if not clan_id:
        return {"ok": False, "error": "no_clan"}
    cd = _cooldown_active(udoc)
    if cd:
        return {"ok": False, "error": "cooldown", "until": cd}
    my_names = {str(udoc.get("nickname") or "").strip().lower(),
                str(udoc.get("username") or "").strip().lower(),
                str(udoc.get("usernameLower") or "").strip().lower()}
    my_names.discard("")
    if not my_names:
        return {"ok": False, "error": "no_username"}

    hist = _latest_record(_GAMES_HISTORY_DIR, room_id)
    if hist is None:
        return {"ok": False, "error": "no_record"}
    rec_id, rec = hist
    mode = str(rec.get("mode") or "")
    if mode == "truncated":
        return {"ok": False, "error": "not_finished"}   # must finish the game

    display_name = udoc.get("nickname") or udoc.get("username") or "Player"
    dedup = f"g_{rec_id}_{uid}"
    # Fast pre-check so a duplicate claim can't tick the same-opponent daily
    # counters; the transaction's ledger create() is the real guarantee.
    try:
        if _clans(db).document(clan_id).collection("ledger").document(dedup).get().exists:
            return {"ok": False, "error": "already_claimed"}
    except Exception:
        pass

    if mode == "competitive":
        comp = _latest_record(_COMPETITIVE_GAMES_DIR, room_id)
        if comp is None:
            return {"ok": False, "error": "no_record"}
        _, crec = comp
        p1, p2 = str(crec.get("p1_name") or ""), str(crec.get("p2_name") or "")
        if p1.strip().lower() in my_names:
            me, opp = p1, p2
        elif p2.strip().lower() in my_names:
            me, opp = p2, p1
        else:
            return {"ok": False, "error": "not_in_game"}
        # Both players must be real, registered (non-guest) accounts.
        if not _is_registered(db, opp):
            return {"ok": False, "error": "opponent_not_registered"}
        won = bool(crec.get("winner")) and _names_match(crec.get("winner"), me)
        # First-3-vs-same-opponent-per-day cap (only the WIN needs it, losses
        # score 0 anyway but still tick the counter so W-L-W-W can't dodge it).
        allowed, over = _bump_opponent_counter(db, clan_id, uid, display_name,
                                               key="comp:" + opp.strip().lower(),
                                               cap=COMP_SAME_OPP_DAILY_CAP)
        pts = POINTS_COMP_WIN if (won and allowed) else 0
        res = _apply_award(
            db, clan_id, uid, display_name, kind="comp",
            points=pts, dedup_id=dedup,
            activity_text=(f"⚔️ {display_name} won a competitive match (+{{pts}} pts)" if won
                           else f"⚔️ {display_name} finished a competitive match"),
            counts_game=True, is_comp_win=won, is_comp_loss=(not won and not crec.get("is_draw")),
            meta={"room": room_id, "opp": opp, "won": won,
                  "opp_capped": bool(won and not allowed)})
        if res.get("ok"):
            res.update({"points": res.get("granted", 0), "mode": "competitive", "won": won,
                        "opp_capped": bool(won and not allowed)})
        return res

    # Casual (standard / team): placement points from the server standings.
    standings = rec.get("standings") or []
    players = rec.get("players") or []
    me_row = None
    for row in standings:
        if str(row.get("name") or "").strip().lower() in my_names:
            me_row = row
            break
    if me_row is None:
        return {"ok": False, "error": "not_in_game"}
    humans = [p for p in players if p.get("is_human")]
    if len(humans) < 2:
        return {"ok": False, "error": "need_real_players"}   # incl. all-AI games
    other_names = [str(p.get("name") or "") for p in humans
                   if str(p.get("name") or "").strip().lower() not in my_names]
    if not any(_is_registered(db, n) for n in other_names):
        return {"ok": False, "error": "need_real_players"}   # everyone else is a guest
    my_score = int(me_row.get("score") or 0)
    place = 1 + sum(1 for row in standings if int(row.get("score") or 0) > my_score)
    n_players = int(rec.get("player_count") or len(standings))
    pts = 0
    if place == 1:
        pts = POINTS_CASUAL_FIRST
    elif place == 2 and n_players > 2:
        pts = POINTS_CASUAL_SECOND
    elif place == 3 and n_players >= 4:
        pts = POINTS_CASUAL_THIRD
    # Repeat-lobby limiter: same exact opponent set only scores a few times/day.
    opp_key = "cas:" + "|".join(sorted(n.strip().lower() for n in other_names))
    allowed, over = _bump_opponent_counter(db, clan_id, uid, display_name,
                                           key=opp_key, cap=CASUAL_SAME_OPPS_DAILY_CAP)
    if not allowed:
        pts = 0
    ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(place, f"{place}th")
    res = _apply_award(
        db, clan_id, uid, display_name, kind="casual",
        points=pts, dedup_id=dedup,
        activity_text=f"🌊 {display_name} finished {ordinal} in a {n_players}-player game (+{{pts}} pts)",
        counts_game=True, is_casual_win=(place == 1),
        meta={"room": room_id, "place": place, "players": n_players,
              "opp_capped": not allowed})
    if res.get("ok"):
        res.update({"points": res.get("granted", 0), "mode": "casual",
                    "place": place, "opp_capped": not allowed})
    return res


def _bump_opponent_counter(db, clan_id: str, uid: str, name: str, *, key: str,
                           cap: int) -> Tuple[bool, int]:
    """Per-player per-day counter for 'same opponent(s)' limits, kept on the
    player's contrib slot. Returns (this_game_still_scores, count_after)."""
    transactional = _txn_helpers()
    clan_ref = _clans(db).document(clan_id)
    txn = db.transaction()
    sid = _get_season_id()
    today = _date_key()
    khash = "o" + hashlib.md5(key.encode()).hexdigest()[:10]

    @transactional
    def _run(t) -> Tuple[bool, int]:
        snap = clan_ref.get(transaction=t)
        if not snap.exists:
            return True, 0
        clan = snap.to_dict() or {}
        slot = _season_slot(clan, sid)
        c = _contrib_slot(slot, uid, name)
        if c.get("opp_date") != today:
            c["opp_date"] = today
            c["opp_counts"] = {}
        n = int((c.get("opp_counts") or {}).get(khash) or 0) + 1
        c["opp_counts"][khash] = n
        t.set(clan_ref, clan)
        return n <= cap, n

    try:
        ok, n = _run(txn)
        if not ok and n == cap + 1:
            _flag(db, "repeat_opponents", uid=uid, clan_id=clan_id,
                  detail=f"{key} exceeded {cap}/day")
        return ok, n
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] opponent counter failed: {exc}")
        return True, 0


def _flag(db, kind: str, **fields) -> None:
    """Suspicious-activity marker for admin review (never raises)."""
    try:
        db.collection("clan_flags").document(f"f{_now()}_{secrets.token_hex(3)}").set(
            {"kind": kind, "ts": _now(), **fields})
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] flag write failed: {exc}")


# ── Trade hook (called from _trade_confirm on completion) ────────────────────
def _trade_signature(trade: Dict[str, Any]) -> str:
    """Order-independent fingerprint of WHAT changed hands — used to refuse
    points for the same items bouncing back and forth between two players."""
    offers = trade.get("offers") or {}
    sides = []
    for uid in sorted(trade.get("participants") or []):
        o = offers.get(uid) or {}
        sides.append(json.dumps({
            "c": int(o.get("coins") or 0),
            "a": sorted(o.get("avatars") or []),
            "b": sorted(o.get("backgrounds") or []),
        }, sort_keys=True))
    blob = "||".join(sorted(sides))    # sorted → A→B same as B→A
    return hashlib.md5(blob.encode()).hexdigest()[:16]


def on_trade_completed(db, trade: Dict[str, Any]) -> Dict[str, Any]:
    """Daily clan trading point. Both players get +1 when: same clan, the trade
    actually moved something, neither point was already earned today, no
    same-items bounce inside 7 days. Never raises — trading itself must never
    break because of clan bookkeeping."""
    out: Dict[str, Any] = {"awarded": {}}
    try:
        parts = [str(p) for p in (trade.get("participants") or []) if p]
        if len(parts) != 2:
            return out
        offers = trade.get("offers") or {}
        moved = any(
            int((offers.get(p) or {}).get("coins") or 0) > 0
            or (offers.get(p) or {}).get("avatars")
            or (offers.get(p) or {}).get("backgrounds")
            for p in parts)
        if not moved:
            return out                      # nothing actually transferred
        udocs: Dict[str, Dict[str, Any]] = {}
        for p in parts:
            snap = _users(db).document(p).get()
            udocs[p] = snap.to_dict() or {} if snap.exists else {}
        clan_a = str(udocs[parts[0]].get("clan_id") or "")
        clan_b = str(udocs[parts[1]].get("clan_id") or "")
        if not clan_a or clan_a != clan_b:
            return out                      # must be the SAME clan
        ensure_season_finalized(db)
        today = _date_key()
        pair_key = "_".join(sorted(parts))
        sig = _trade_signature(trade)
        # Bounce check + pair-volume flag, tracked on a tiny pair doc.
        pair_ref = db.collection("clan_meta").document(f"tradepair_{pair_key}")
        psnap = pair_ref.get()
        pdoc = psnap.to_dict() or {} if psnap.exists else {}
        sigs = {k: int(v) for k, v in (pdoc.get("sigs") or {}).items()}
        bounced = int(sigs.get(sig) or 0) >= _now() - TRADE_BOUNCE_WINDOW_SEC and sig in sigs
        wk = _week_key()
        wk_n = (int(pdoc.get("week_n") or 0) + 1) if pdoc.get("week") == wk else 1
        sigs[sig] = _now()
        cutoff = _now() - TRADE_BOUNCE_WINDOW_SEC
        sigs = {k: v for k, v in sorted(sigs.items(), key=lambda kv: -kv[1])[:20] if v >= cutoff or k == sig}
        pair_ref.set({"sigs": sigs, "week": wk, "week_n": wk_n, "ts": _now(),
                      "clan": clan_a}, merge=True)
        if wk_n == TRADE_PAIR_WEEK_FLAG + 1:
            _flag(db, "trade_pair_volume", clan_id=clan_a, pair=pair_key,
                  detail=f"{wk_n} completed trades this week")
        if bounced:
            _flag(db, "trade_bounce", clan_id=clan_a, pair=pair_key, sig=sig,
                  detail="same items traded back within 7 days")
            out["bounced"] = True
            return out
        for p in parts:
            cd = _cooldown_active(udocs[p])
            if cd:
                continue                    # inside the 24h clan-switch cooldown
            nm = udocs[p].get("nickname") or udocs[p].get("username") or "Player"
            res = _apply_award(
                db, clan_a, p, nm, kind="trade",
                points=POINTS_TRADE_DAILY,
                dedup_id=f"t_{today}_{p}",   # ONE trade point per player per day
                activity_text=f"🤝 {nm} completed a clan trade (+{{pts}} pt)",
                is_trade=True,
                meta={"pair": pair_key})
            if res.get("ok") and int(res.get("granted") or 0) > 0:
                out["awarded"][p] = res.get("granted")
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] trade hook failed: {exc}")
    return out


# ── Clan lifecycle ops ───────────────────────────────────────────────────────
def _create_clan(uid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    db = _get_firestore()
    if db is None:
        return {"ok": False, "error": "firestore_unavailable"}
    ensure_season_finalized(db)
    name = str(body.get("name") or "").strip()
    ok, why = clan_name_check(name)
    if not ok:
        return {"ok": False, "error": "bad_name", "reason": why}
    icon = str(body.get("icon") or "").strip()
    if not re.match(r"^/avatars/[a-z0-9\-]+\.(png|webp)$", icon):
        return {"ok": False, "error": "bad_icon"}
    icon_name = str(body.get("icon_name") or "").strip()[:40]
    desc = str(body.get("description") or "").strip()[:CLAN_DESC_MAX]
    if desc and text_is_profane(desc):
        return {"ok": False, "error": "bad_description"}
    privacy = str(body.get("privacy") or "public")
    if privacy not in PRIVACY_MODES:
        return {"ok": False, "error": "bad_privacy"}

    transactional = _txn_helpers()
    name_lower = name.lower()
    name_ref = db.collection("clan_names").document(name_lower)
    user_ref = _users(db).document(uid)
    clan_id = _new_clan_id()
    clan_ref = _clans(db).document(clan_id)
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        nsnap = name_ref.get(transaction=t)
        if nsnap.exists:
            return {"ok": False, "error": "name_taken"}
        usnap = user_ref.get(transaction=t)
        udoc = usnap.to_dict() or {} if usnap.exists else {}
        if udoc.get("clan_id"):
            return {"ok": False, "error": "already_in_clan"}
        nick = udoc.get("nickname") or udoc.get("username") or "Player"
        clan = {
            "name": name, "nameLower": name_lower,
            "icon": icon, "icon_name": icon_name,
            "description": desc, "privacy": privacy,
            "owner_uid": uid, "created_ts": _now(),
            "members": {uid: {"name": nick, "role": "owner",
                              "custom_role_id": None, "joined_ts": _now(),
                              "avatar": str(udoc.get("avatar_url") or "")}},
            "custom_roles": [], "captains_can_edit_roles": False,
            "pinned_announcement": None,
            "join_requests": [], "muted": {}, "events": [],
            "xp": 0, "lifetime": {"points": 0}, "seasons": {}, "activity": [],
        }
        _activity_push(clan, "create", f"🛡️ {nick} founded the clan")
        t.set(clan_ref, clan)
        t.set(name_ref, {"clan_id": clan_id, "ts": _now()})
        t.set(user_ref, {"clan_id": clan_id, "clan_joined_ts": _now()}, merge=True)
        return {"ok": True, "clan_id": clan_id}

    try:
        res = _run(txn)
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] create failed: {exc}")
        return {"ok": False, "error": "create_failed"}
    if res.get("ok"):
        _chat_system(db, clan_id, f"🛡️ Welcome to {name}! This is your private clan chat.")
    return res


def _join_clan(uid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    db = _get_firestore()
    if db is None:
        return {"ok": False, "error": "firestore_unavailable"}
    ensure_season_finalized(db)
    clan_id = str(body.get("clan_id") or "").strip()
    if not clan_id:
        return {"ok": False, "error": "bad_clan"}
    transactional = _txn_helpers()
    clan_ref = _clans(db).document(clan_id)
    user_ref = _users(db).document(uid)
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        csnap = clan_ref.get(transaction=t)
        if not csnap.exists:
            return {"ok": False, "error": "no_clan"}
        clan = csnap.to_dict() or {}
        usnap = user_ref.get(transaction=t)
        udoc = usnap.to_dict() or {} if usnap.exists else {}
        if udoc.get("clan_id"):
            return {"ok": False, "error": "already_in_clan"}
        members = clan.get("members") or {}
        if uid in members:
            return {"ok": False, "error": "already_member"}
        if len(members) >= CLAN_MAX_MEMBERS:
            return {"ok": False, "error": "clan_full"}
        invites = [i for i in (udoc.get("clan_invites") or [])
                   if isinstance(i, dict) and int(i.get("ts") or 0) > _now() - CLAN_INVITE_TTL_SEC]
        has_invite = any(str(i.get("clan_id")) == clan_id for i in invites)
        privacy = clan.get("privacy")
        if privacy != "public" and not has_invite:
            return {"ok": False, "error": "invite_required" if privacy == "invite" else "request_required"}
        nick = udoc.get("nickname") or udoc.get("username") or "Player"
        members[uid] = {"name": nick, "role": "member", "custom_role_id": None,
                        "joined_ts": _now(), "avatar": str(udoc.get("avatar_url") or "")}
        clan["members"] = members
        clan["join_requests"] = [r for r in (clan.get("join_requests") or [])
                                 if str(r.get("uid")) != uid]
        _activity_push(clan, "join", f"🌊 {nick} joined the clan")
        t.set(clan_ref, clan)
        t.set(user_ref, {"clan_id": clan_id, "clan_joined_ts": _now(),
                         "clan_invites": [i for i in invites if str(i.get("clan_id")) != clan_id]},
              merge=True)
        return {"ok": True, "clan_id": clan_id, "name": clan.get("name"), "nick": nick}

    try:
        res = _run(txn)
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] join failed: {exc}")
        return {"ok": False, "error": "join_failed"}
    if res.get("ok"):
        _chat_system(db, clan_id, f"🌊 {res.pop('nick', 'A new member')} joined the clan — say hi!")
    return res


def _leave_clan(uid: str, *, kicked_by: Optional[str] = None,
                clan_id_hint: str = "") -> Dict[str, Any]:
    """Voluntary leave OR kick (kicked_by set). Earned points stay with the
    clan (contrib is never deleted); the leaver gets the 24h cooldown."""
    db = _get_firestore()
    if db is None:
        return {"ok": False, "error": "firestore_unavailable"}
    transactional = _txn_helpers()
    user_ref = _users(db).document(uid)
    usnap = user_ref.get()
    udoc = usnap.to_dict() or {} if usnap.exists else {}
    clan_id = clan_id_hint or str(udoc.get("clan_id") or "")
    if not clan_id:
        return {"ok": False, "error": "no_clan"}
    clan_ref = _clans(db).document(clan_id)
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        csnap = clan_ref.get(transaction=t)
        if not csnap.exists:
            t.set(user_ref, {"clan_id": ""}, merge=True)
            return {"ok": True, "gone": True}
        clan = csnap.to_dict() or {}
        members = clan.get("members") or {}
        mem = members.get(uid)
        if not mem:
            t.set(user_ref, {"clan_id": ""}, merge=True)
            return {"ok": True, "gone": True}
        if mem.get("role") == "owner" and len(members) > 1:
            return {"ok": False, "error": "transfer_first"}
        nick = mem.get("name") or "A member"
        del members[uid]
        clan["members"] = members
        verb = "was removed from" if kicked_by else "left"
        _activity_push(clan, "leave", f"👋 {nick} {verb} the clan")
        if not members:
            # Last member out → the clan dissolves; free the name.
            t.delete(clan_ref)
            t.delete(db.collection("clan_names").document(str(clan.get("nameLower") or "")))
        else:
            t.set(clan_ref, clan)
        t.set(user_ref, {"clan_id": "",
                         "clan_cooldown_until": _now() + JOIN_COOLDOWN_SEC}, merge=True)
        return {"ok": True, "nick": nick, "dissolved": not members}

    try:
        res = _run(txn)
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] leave failed: {exc}")
        return {"ok": False, "error": "leave_failed"}
    if res.get("ok") and not res.get("gone") and not res.get("dissolved"):
        _chat_system(db, clan_id,
                     f"👋 {res.pop('nick', 'A member')} "
                     + ("was removed from the clan." if kicked_by else "left the clan."))
    return res


# ── Route helpers ────────────────────────────────────────────────────────────
def _auth_uid(body: Dict[str, Any]) -> Optional[str]:
    tok = body.get("idToken") if isinstance(body.get("idToken"), str) else ""
    claims = _verify_token(tok) if tok else None
    return claims.get("uid") if claims and claims.get("uid") else None


def _with_clan(uid: str) -> Tuple[Optional[Any], Optional[str], Optional[Dict[str, Any]], Dict[str, Any]]:
    """(db, clan_id, clan, udoc) for the caller — clan fields None if clanless."""
    db = _get_firestore()
    if db is None:
        return None, None, None, {}
    usnap = _users(db).document(uid).get()
    udoc = usnap.to_dict() or {} if usnap.exists else {}
    clan_id = str(udoc.get("clan_id") or "")
    if not clan_id:
        return db, None, None, udoc
    csnap = _clans(db).document(clan_id).get()
    if not csnap.exists:
        return db, None, None, udoc
    return db, clan_id, csnap.to_dict() or {}, udoc


def _clan_profile(db, clan_id: str, clan: Dict[str, Any], sid: str,
                  viewer_uid: str = "") -> Dict[str, Any]:
    card = _clan_card(clan_id, clan, sid)
    slot = (clan.get("seasons") or {}).get(sid) or {}
    contrib = slot.get("contrib") or {}
    members_out = []
    mvp_chip = clan.get("mvp_chip") or {}
    mvp_uid = mvp_chip.get("uid") if int(mvp_chip.get("until") or 0) > _now() else None
    wk = _week_key()
    today = _date_key()
    for m_uid, mem in (clan.get("members") or {}).items():
        c = contrib.get(m_uid) or {}
        online, last_seen = False, 0
        try:
            usnap = _users(db).document(m_uid).get()
            u = usnap.to_dict() or {} if usnap.exists else {}
            la = u.get("last_active")
            la_sec = int(la.timestamp()) if hasattr(la, "timestamp") else int(la or 0)
            online = bool(u.get("online")) and la_sec >= _now() - PRESENCE_FRESH_SEC
            last_seen = la_sec
        except Exception:
            pass
        members_out.append({
            "uid": m_uid, "name": mem.get("name"), "avatar": mem.get("avatar") or "",
            "role": mem.get("role"), "custom_role_id": mem.get("custom_role_id"),
            "joined_ts": int(mem.get("joined_ts") or 0),
            "online": online, "last_seen": last_seen,
            "points": int(c.get("points") or 0),
            "weekly_points": int((c.get("weekly") or {}).get(wk) or 0),
            "comp_wins": int(c.get("comp_wins") or 0),
            "casual_wins": int(c.get("casual_wins") or 0),
            "challenges_done": int(c.get("challenges_done") or 0),
            "trade_point_today": c.get("last_trade_date") == today,
            "is_mvp_chip": m_uid == mvp_uid,
        })
    # Season-history contributors who already left still show in contrib.
    former = [{"uid": u, "name": (c or {}).get("name"), "points": int((c or {}).get("points") or 0)}
              for u, c in contrib.items() if u not in (clan.get("members") or {})]
    daily = _daily_slot(dict(clan), clan_id)   # copy → never mutates the stored doc here
    weekly = _weekly_slot(dict(clan))
    votes = slot.get("critter_votes") or {}
    tally: Dict[str, int] = {}
    for icon in votes.values():
        tally[icon] = tally.get(icon, 0) + 1
    fav = max(tally.items(), key=lambda kv: kv[1])[0] if tally else None
    # Friendly rival: the head-to-head stat card (no rewards ride on this).
    rival = None
    rival_id = (clan.get("rivals") or {}).get(sid)
    if rival_id:
        try:
            rsnap = _clans(db).document(str(rival_id)).get()
            if rsnap.exists:
                rival = _clan_card(str(rival_id), rsnap.to_dict() or {}, sid)
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] rival read failed: {exc}")
    out = {
        **card,
        "owner_uid": clan.get("owner_uid"),
        "created_ts": clan.get("created_ts"),
        "captains_can_edit_roles": bool(clan.get("captains_can_edit_roles")),
        "custom_roles": clan.get("custom_roles") or [],
        "pinned_announcement": clan.get("pinned_announcement"),
        "members": sorted(members_out, key=lambda m: (-{"owner": 3, "captain": 2, "recruiter": 1}.get(m["role"], 0), -m["points"])),
        "former_contributors": former,
        "activity": (clan.get("activity") or [])[:60],
        "events": [e for e in (clan.get("events") or []) if int(e.get("ts") or 0) > _now() - 3600],
        "win_streak": int(slot.get("win_streak") or 0),
        "prev_results": clan.get("prev_results") or {},
        "mvp_chip": clan.get("mvp_chip"),
        "level_info": _clan_level(int(clan.get("xp") or 0)),
        "daily_goal": {"goal": daily.get("goal"), "progress": _daily_goal_progress(daily),
                       "done": bool(daily.get("done")), "date": daily.get("date")},
        "weekly": {"week": weekly.get("week"), "games": weekly.get("games"),
                   "points": weekly.get("points"), "trades": weekly.get("trades"),
                   "comp_wins": weekly.get("comp_wins"),
                   "challenges_done": weekly.get("challenges_done") or []},
        "challenges": _challenges_view(weekly, slot, clan),
        "week_ends_ts": _week_end_ts(),
        "favorite_critter": fav,
        "favorite_votes": tally,
        "my_vote": votes.get(viewer_uid) if viewer_uid else None,
        "rival": rival,
        "season": _season_public(sid),
    }
    if viewer_uid and _member_of(clan, viewer_uid):
        out["my"] = {
            "role": (_member_of(clan, viewer_uid) or {}).get("role"),
            "custom_role_id": (_member_of(clan, viewer_uid) or {}).get("custom_role_id"),
            "perms": {p: _has_perm(clan, viewer_uid, p) for p in
                      CUSTOM_PERMS + ("edit_custom_roles",)},
            "is_owner": clan.get("owner_uid") == viewer_uid,
            "contribution": contrib.get(viewer_uid) or {},
        }
        out["join_requests"] = (clan.get("join_requests") or []) if _has_perm(clan, viewer_uid, "review_requests") else []
    return out


def _week_end_ts() -> int:
    """Unix time when the current ISO week rolls over (next Monday 00:00 UTC) —
    the weekly challenge + weekly-cap reset moment."""
    now = datetime.now(tz=timezone.utc)
    midnight = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return int(midnight.timestamp()) + (7 - now.isoweekday() + 1) * 86400


def _challenges_view(weekly: Dict[str, Any], slot: Dict[str, Any],
                     clan: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    done = set(weekly.get("challenges_done") or [])
    contribs = weekly.get("contributors") or {}
    members = (clan or {}).get("members") or {}
    ends = _week_end_ts()
    out = []
    for ch in CLAN_WEEKLY_CHALLENGES:
        need = int(ch.get("min_contribution") or 1)
        # Who actually pulled their weight toward this week's rewards.
        who = [{"uid": u, "name": (members.get(u) or {}).get("name") or "Player",
                "points": int(p or 0), "qualifies": int(p or 0) >= need}
               for u, p in sorted(contribs.items(), key=lambda kv: -int(kv[1] or 0))]
        out.append({
            "id": ch.get("id"), "name": ch.get("name"), "desc": ch.get("desc"),
            "metric": ch.get("metric"), "target": int(ch.get("target") or 0),
            "progress": _challenge_metric(weekly, str(ch.get("metric") or "")),
            "clan_points": int(ch.get("clan_points") or 0),
            "member_xp": int(ch.get("member_xp") or 0),
            "min_contribution": need,
            "done": ch.get("id") in done,
            "ends_ts": ends,
            "contributors": who[:20],
        })
    return out


# ── HTTP surface ─────────────────────────────────────────────────────────────
def handle_get(handler, parsed) -> bool:
    """GET /api/clan/leaderboard — public read (everything else is POST)."""
    if parsed.path != "/api/clan/leaderboard":
        return False
    db = _get_firestore()
    if db is None:
        handler._send_json({"ok": False, "error": "firestore_unavailable"})
        return True
    ensure_season_finalized(db)
    sid = _get_season_id()
    handler._send_json({"ok": True, "season": _season_public(sid),
                        "rows": _leaderboard_rows(db, sid)})
    return True


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:  # noqa: C901
    path = parsed.path
    if not path.startswith("/api/clan/") and not path.startswith("/api/admin/clan"):
        return False

    # ── Admin review endpoints (name/chat reports) ───────────────────────────
    if path.startswith("/api/admin/clan"):
        admin_key = body.get("admin_key") if isinstance(body.get("admin_key"), str) else ""
        env_key = os.environ.get("ADMIN_RECOVERY_KEY", "").strip()
        if not env_key or not secrets.compare_digest(admin_key, env_key):
            handler._send_json({"ok": False, "error": "unauthorized"}, status=403)
            return True
        db = _get_firestore()
        if db is None:
            handler._send_json({"ok": False, "error": "firestore_unavailable"})
            return True
        if path == "/api/admin/clan-reports":
            rows = []
            try:
                for doc in db.collection("clan_reports").limit(200).stream():
                    rows.append({"id": doc.id, **(doc.to_dict() or {})})
            except Exception as exc:  # noqa: BLE001
                print(f"[clan] report list failed: {exc}")
            rows.sort(key=lambda r: -int(r.get("ts") or 0))
            handler._send_json({"ok": True, "reports": rows})
            return True
        if path == "/api/admin/clan-report-act":
            rid = str(body.get("id") or "")
            action = str(body.get("action") or "dismiss")
            try:
                rref = db.collection("clan_reports").document(rid)
                rep = rref.get().to_dict() or {}
                if action == "force_rename" and rep.get("clan_id"):
                    cref = _clans(db).document(str(rep["clan_id"]))
                    csnap = cref.get()
                    if csnap.exists:
                        clan = csnap.to_dict() or {}
                        old_lower = str(clan.get("nameLower") or "")
                        new_name = f"Clan {str(rep['clan_id'])[-6:].upper()}"
                        cref.set({"name": new_name, "nameLower": new_name.lower(),
                                  "description": ""}, merge=True)
                        db.collection("clan_names").document(new_name.lower()).set(
                            {"clan_id": rep["clan_id"], "ts": _now()})
                        if old_lower:
                            db.collection("clan_names").document(old_lower).delete()
                rref.set({"status": "resolved", "action": action,
                          "resolved_ts": _now()}, merge=True)
                handler._send_json({"ok": True})
            except Exception as exc:  # noqa: BLE001
                handler._send_json({"ok": False, "error": str(exc)[:120]})
            return True
        handler._send_json({"ok": False, "error": "unknown_action"}, status=404)
        return True

    action = path[len("/api/clan/"):]
    uid = _auth_uid(body)
    if not uid:
        handler._send_json({"ok": False, "error": "unauthorized"}, status=401)
        return True
    db = _get_firestore()
    if db is None:
        handler._send_json({"ok": False, "error": "firestore_unavailable"})
        return True
    ensure_season_finalized(db)
    sid = _get_season_id()

    try:
        handler._send_json(_route_post(db, uid, action, body, sid))
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(f"[clan] {action} failed: {exc}\n{traceback.format_exc(limit=4)}")
        handler._send_json({"ok": False, "error": "server_error"})
    return True


# Actions that only read. Everything else can move the standings, so the one
# route funnel below drops the leaderboard cache after it succeeds — a clan
# created this second must be on the board the next second.
_READ_ONLY_ACTIONS = frozenset({
    "home", "leaderboard", "browse", "get", "season-results", "check-name",
    "challenges", "chat-get",
})


def _route_post(db, uid: str, action: str, body: Dict[str, Any], sid: str) -> Dict[str, Any]:
    result = _route_action(db, uid, action, body, sid)
    if action not in _READ_ONLY_ACTIONS and isinstance(result, dict) and result.get("ok"):
        _lb_invalidate()
    return result


def _route_action(db, uid: str, action: str, body: Dict[str, Any], sid: str  # noqa: C901
                  ) -> Dict[str, Any]:
    # ---- reads -------------------------------------------------------------
    if action == "home":
        _, clan_id, clan, udoc = _with_clan(uid)
        rows = _leaderboard_rows(db, sid)
        mine = None
        if clan_id:
            mine = next((r for r in rows if r["id"] == clan_id), None)
        contrib = {}
        if clan is not None and clan_id:
            slot = (clan.get("seasons") or {}).get(sid) or {}
            contrib = (slot.get("contrib") or {}).get(uid) or {}
        prev_meta = {}
        try:
            snap = db.collection("clan_meta").document(f"season_{_prev_sid(sid)}").get()
            prev_meta = snap.to_dict() or {} if snap.exists else {}
        except Exception:
            pass
        invites = [i for i in (udoc.get("clan_invites") or [])
                   if isinstance(i, dict) and int(i.get("ts") or 0) > _now() - CLAN_INVITE_TTL_SEC]
        return {"ok": True, "season": _season_public(sid),
                "top3": rows[:3], "total_clans": len(rows),
                "my_clan": mine,
                "my_clan_full": (_clan_profile(db, clan_id, clan, sid, uid) if clan is not None and clan_id else None),
                "my_contribution": {"points": int(contrib.get("points") or 0),
                                    "game_points": int(contrib.get("game_points") or 0),
                                    "trade_points": int(contrib.get("trade_points") or 0),
                                    "weekly": int((contrib.get("weekly") or {}).get(_week_key()) or 0),
                                    "weekly_cap": WEEKLY_POINT_CAP},
                "cooldown_until": _cooldown_active(udoc),
                "invites": invites,
                "badges": udoc.get("clan_badges") or [],
                "prev_season": {"sid": _prev_sid(sid),
                                "number": _season_number(_prev_sid(sid)),
                                "name": _season_name(_prev_sid(sid)),
                                "standings": (prev_meta.get("standings") or [])[:10]}}

    if action == "leaderboard":
        return {"ok": True, "season": _season_public(sid), "rows": _leaderboard_rows(db, sid)}

    if action == "browse":
        q = str(body.get("query") or "").strip().lower()
        rows = _leaderboard_rows(db, sid)
        if q:
            # Searching by name finds any clan, including invite-only ones (you
            # can look at it and ask around, you just can't press Join).
            rows = [r for r in rows if q in str(r.get("name") or "").lower()]
        else:
            # The plain browse list is the clans you can actually act on.
            rows = [r for r in rows if r.get("privacy") in ("public", "request")]
        open_first = sorted(rows, key=lambda r: (r["member_count"] >= CLAN_MAX_MEMBERS, r["rank"]))
        recommended = [r for r in rows
                       if r.get("privacy") == "public" and r["member_count"] < CLAN_MAX_MEMBERS][:5]
        return {"ok": True, "season": _season_public(sid),
                "rows": open_first[:100], "recommended": recommended}

    if action == "get":
        clan_id = str(body.get("clan_id") or "")
        csnap = _clans(db).document(clan_id).get()
        if not csnap.exists:
            return {"ok": False, "error": "no_clan"}
        clan = csnap.to_dict() or {}
        prof = _clan_profile(db, clan_id, clan, sid, uid)
        rows = _leaderboard_rows(db, sid)
        mine = next((r for r in rows if r["id"] == clan_id), None)
        prof["rank"] = mine["rank"] if mine else None
        prof["record"] = mine["record"] if mine else "0-0"
        return {"ok": True, "clan": prof}

    if action == "season-results":
        want = str(body.get("sid") or _prev_sid(sid))
        snap = db.collection("clan_meta").document(f"season_{want}").get()
        meta = snap.to_dict() or {} if snap.exists else {}
        # the caller's own breakdown for that season, if they were in a clan
        _, clan_id, clan, udoc = _with_clan(uid)
        my = None
        if clan is not None and clan_id:
            slot = (clan.get("seasons") or {}).get(want) or {}
            my = (slot.get("contrib") or {}).get(uid)
        # What I personally walked away with: coins from the standings ledger
        # plus every badge/cosmetic stamped for that season.
        my_coins = 0
        for row in meta.get("standings") or []:
            if row.get("clan_id") == clan_id:
                my_coins = int((row.get("rewarded") or {}).get(uid) or 0)
                break
        my_badges = [b for b in (udoc.get("clan_badges") or [])
                     if isinstance(b, dict) and b.get("sid") == want]
        return {"ok": True, "sid": want, "number": _season_number(want),
                "name": _season_name(want), "meta": meta, "my_contribution": my,
                "my_coins": my_coins, "my_badges": my_badges,
                "my_clan_id": clan_id,
                "next_season": _season_public(sid)}

    if action == "check-name":
        name = str(body.get("name") or "")
        ok, why = clan_name_check(name)
        taken = False
        if ok:
            try:
                taken = db.collection("clan_names").document(name.strip().lower()).get().exists
            except Exception:
                taken = False
        return {"ok": True, "clean": ok, "reason": why, "available": ok and not taken}

    if action == "challenges":
        _, clan_id, clan, _udoc = _with_clan(uid)
        if clan is None or not clan_id:
            return {"ok": False, "error": "no_clan"}
        weekly = _weekly_slot(dict(clan))
        slot = (clan.get("seasons") or {}).get(sid) or {}
        return {"ok": True, "week": weekly.get("week"),
                "week_ends_ts": _week_end_ts(),
                "challenges": _challenges_view(weekly, slot, clan)}

    # ---- lifecycle ----------------------------------------------------------
    if action == "create":
        return _create_clan(uid, body)
    if action == "join":
        return _join_clan(uid, body)
    if action == "leave":
        return _leave_clan(uid)
    if action == "disband":
        _, clan_id, clan, _udoc = _with_clan(uid)
        if clan is None or not clan_id:
            return {"ok": False, "error": "no_clan"}
        if clan.get("owner_uid") != uid:
            return {"ok": False, "error": "owner_only"}
        try:
            for m_uid in list((clan.get("members") or {}).keys()):
                _users(db).document(m_uid).set({"clan_id": ""}, merge=True)
            _clans(db).document(clan_id).delete()
            nl = str(clan.get("nameLower") or "")
            if nl:
                db.collection("clan_names").document(nl).delete()
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] disband failed: {exc}")
            return {"ok": False, "error": "disband_failed"}

    if action == "request":
        clan_id = str(body.get("clan_id") or "")
        cref = _clans(db).document(clan_id)
        csnap = cref.get()
        if not csnap.exists:
            return {"ok": False, "error": "no_clan"}
        clan = csnap.to_dict() or {}
        if clan.get("privacy") != "request":
            return {"ok": False, "error": "not_requestable"}
        usnap = _users(db).document(uid).get()
        udoc = usnap.to_dict() or {} if usnap.exists else {}
        if udoc.get("clan_id"):
            return {"ok": False, "error": "already_in_clan"}
        if len(clan.get("members") or {}) >= CLAN_MAX_MEMBERS:
            return {"ok": False, "error": "clan_full"}
        reqs = [r for r in (clan.get("join_requests") or []) if str(r.get("uid")) != uid][:19]
        reqs.append({"uid": uid, "name": udoc.get("nickname") or udoc.get("username") or "Player",
                     "ts": _now()})
        cref.set({"join_requests": reqs}, merge=True)
        return {"ok": True}

    if action == "request-cancel":    # withdraw my own pending join request
        clan_id = str(body.get("clan_id") or "")
        cref = _clans(db).document(clan_id)
        csnap = cref.get()
        if csnap.exists:
            clan = csnap.to_dict() or {}
            cref.set({"join_requests": [r for r in (clan.get("join_requests") or [])
                                        if str(r.get("uid")) != uid]}, merge=True)
        return {"ok": True}

    if action == "request-act":       # accept / reject a join request
        _, clan_id, clan, _udoc = _with_clan(uid)
        if clan is None or not clan_id:
            return {"ok": False, "error": "no_clan"}
        if not _has_perm(clan, uid, "review_requests"):
            return {"ok": False, "error": "no_permission"}
        target = str(body.get("uid") or "")
        accept = bool(body.get("accept"))
        reqs = clan.get("join_requests") or []
        row = next((r for r in reqs if str(r.get("uid")) == target), None)
        if not row:
            return {"ok": False, "error": "no_request"}
        if accept:
            res = _join_clan_direct(db, target, clan_id)
            if not res.get("ok"):
                # still clear the request if the player became unjoinable
                if res.get("error") in ("already_in_clan", "no_user"):
                    _clans(db).document(clan_id).set(
                        {"join_requests": [r for r in reqs if str(r.get("uid")) != target]}, merge=True)
                return res
            return {"ok": True, "joined": True}
        _clans(db).document(clan_id).set(
            {"join_requests": [r for r in reqs if str(r.get("uid")) != target]}, merge=True)
        return {"ok": True, "joined": False}

    if action == "invite":
        _, clan_id, clan, udoc = _with_clan(uid)
        if clan is None or not clan_id:
            return {"ok": False, "error": "no_clan"}
        if not _has_perm(clan, uid, "invite"):
            return {"ok": False, "error": "no_permission"}
        if len(clan.get("members") or {}) >= CLAN_MAX_MEMBERS:
            return {"ok": False, "error": "clan_full"}
        to_uid = str(body.get("to_uid") or "").strip()
        if not to_uid and body.get("to_name"):
            to_uid = _find_uid_by_username(db, str(body.get("to_name")).strip().lower()) or ""
        if not to_uid or to_uid == uid:
            return {"ok": False, "error": "no_user"}
        tsnap = _users(db).document(to_uid).get()
        if not tsnap.exists:
            return {"ok": False, "error": "no_user"}
        tdoc = tsnap.to_dict() or {}
        if str(tdoc.get("clan_id") or "") == clan_id:
            return {"ok": False, "error": "already_member"}
        inviter = udoc.get("nickname") or udoc.get("username") or "A player"
        invites = [i for i in (tdoc.get("clan_invites") or [])
                   if isinstance(i, dict) and int(i.get("ts") or 0) > _now() - CLAN_INVITE_TTL_SEC
                   and str(i.get("clan_id")) != clan_id][-(CLAN_INVITE_MAX - 1):]
        invites.append({"clan_id": clan_id, "name": clan.get("name"),
                        "icon": clan.get("icon"), "by": inviter, "ts": _now()})
        _users(db).document(to_uid).set({"clan_invites": invites}, merge=True)
        # Drop a DM-style note in their Messages inbox (same subcollection the
        # trade system mirrors into — shows up with their existing rules). The
        # clan critter rides along so the invite shows its icon everywhere.
        try:
            _users(db).document(to_uid).collection("messages").document(
                f"claninvite_{clan_id}_{_now()}").set({
                    "from": uid, "fromName": inviter, "kind": "clan_invite",
                    "text": f"🛡️ {inviter} invited you to join the clan "
                            f"“{clan.get('name')}” — open the Clans tab to accept!",
                    "clan_id": clan_id, "clan_name": clan.get("name"),
                    "clan_icon": clan.get("icon"), "ts": _now(), "read": False,
                })
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] invite message failed: {exc}")
        return {"ok": True}

    if action == "report":
        # Deliberately ABOVE the members-only gate: anyone signed in can report
        # a clan NAME that slipped past the filter. Message reports still
        # require being in the clan whose chat the message lives in.
        kind = str(body.get("kind") or "name")
        _, my_clan_id, _my_clan, _u = _with_clan(uid)
        target_clan = str(body.get("clan_id") or my_clan_id or "")
        if not target_clan:
            return {"ok": False, "error": "bad_clan"}
        if kind == "message" and target_clan != my_clan_id:
            return {"ok": False, "error": "no_permission"}
        rep = {"kind": kind, "clan_id": target_clan, "by": uid, "ts": _now(),
               "status": "pending", "reason": str(body.get("reason") or "")[:200]}
        if kind == "message" and body.get("msg_id"):
            rep["msg_id"] = str(body.get("msg_id"))[:40]
            rep["msg_text"] = str(body.get("msg_text") or "")[:200]
        if kind == "name":
            try:
                csnap = _clans(db).document(target_clan).get()
                rep["clan_name"] = ((csnap.to_dict() or {}).get("name") if csnap.exists else "") or ""
            except Exception:
                pass
        db.collection("clan_reports").document(f"r{_now()}_{secrets.token_hex(3)}").set(rep)
        return {"ok": True}

    if action == "invite-decline":
        clan_id = str(body.get("clan_id") or "")
        usnap = _users(db).document(uid).get()
        udoc = usnap.to_dict() or {} if usnap.exists else {}
        invites = [i for i in (udoc.get("clan_invites") or [])
                   if isinstance(i, dict) and str(i.get("clan_id")) != clan_id]
        _users(db).document(uid).set({"clan_invites": invites}, merge=True)
        return {"ok": True}

    # ---- everything below needs an existing membership ---------------------
    _, clan_id, clan, udoc = _with_clan(uid)
    if clan is None or not clan_id:
        return {"ok": False, "error": "no_clan"}

    if action == "kick":
        target = str(body.get("uid") or "")
        tmem = _member_of(clan, target)
        if not tmem:
            return {"ok": False, "error": "not_member"}
        if not _has_perm(clan, uid, "remove_members"):
            return {"ok": False, "error": "no_permission"}
        my_rank = _role_rank(clan, _member_of(clan, uid))
        if _role_rank(clan, tmem) >= my_rank and clan.get("owner_uid") != uid:
            return {"ok": False, "error": "outranked"}   # captains can't kick captains/owner
        if target == clan.get("owner_uid"):
            return {"ok": False, "error": "cannot_remove_owner"}
        return _leave_clan(target, kicked_by=uid, clan_id_hint=clan_id)

    if action == "role":
        target = str(body.get("uid") or "")
        new_role = str(body.get("role") or "member")
        custom_id = str(body.get("custom_role_id") or "") or None
        tmem = _member_of(clan, target)
        if not tmem:
            return {"ok": False, "error": "not_member"}
        if target == clan.get("owner_uid"):
            return {"ok": False, "error": "cannot_change_owner"}
        if new_role == "owner":
            return {"ok": False, "error": "use_transfer"}
        if new_role not in ("captain", "recruiter", "member"):
            return {"ok": False, "error": "bad_role"}
        is_owner = clan.get("owner_uid") == uid
        if new_role == "captain" and not is_owner:
            return {"ok": False, "error": "owner_only"}   # only the owner promotes captains
        if not is_owner:
            if not _has_perm(clan, uid, "change_roles"):
                return {"ok": False, "error": "no_permission"}
            if _role_rank(clan, tmem) >= _role_rank(clan, _member_of(clan, uid)):
                return {"ok": False, "error": "outranked"}
        if custom_id and not any(r.get("id") == custom_id for r in clan.get("custom_roles") or []):
            return {"ok": False, "error": "no_such_role"}
        members = clan.get("members") or {}
        members[target] = {**tmem, "role": new_role, "custom_role_id": custom_id}
        label = new_role + (f" · {custom_id}" if custom_id else "")
        _activity_push(clan, "role", f"🎖 {tmem.get('name')} is now {label}")
        _clans(db).document(clan_id).set({"members": members, "activity": clan.get("activity")}, merge=True)
        return {"ok": True}

    if action == "transfer":
        if clan.get("owner_uid") != uid:
            return {"ok": False, "error": "owner_only"}
        target = str(body.get("uid") or "")
        tmem = _member_of(clan, target)
        if not tmem or target == uid:
            return {"ok": False, "error": "not_member"}
        members = clan.get("members") or {}
        members[uid] = {**members[uid], "role": "captain"}
        members[target] = {**tmem, "role": "owner", "custom_role_id": None}
        _activity_push(clan, "transfer",
                       f"👑 {tmem.get('name')} is the new clan owner")
        _clans(db).document(clan_id).set({"members": members, "owner_uid": target,
                                          "activity": clan.get("activity")}, merge=True)
        _chat_system(db, clan_id, f"👑 {tmem.get('name')} is the new clan owner!")
        return {"ok": True}

    if action == "settings":
        if clan.get("owner_uid") != uid:
            return {"ok": False, "error": "owner_only"}   # icon/privacy/desc = owner (major settings)
        updates: Dict[str, Any] = {}
        if isinstance(body.get("icon"), str) and body.get("icon"):
            icon = str(body["icon"]).strip()
            if not re.match(r"^/avatars/[a-z0-9\-]+\.(png|webp)$", icon):
                return {"ok": False, "error": "bad_icon"}
            updates["icon"] = icon
            updates["icon_name"] = str(body.get("icon_name") or "").strip()[:40]
        if isinstance(body.get("description"), str):
            desc = str(body["description"]).strip()[:CLAN_DESC_MAX]
            if desc and text_is_profane(desc):
                return {"ok": False, "error": "bad_description"}
            updates["description"] = desc
        if isinstance(body.get("privacy"), str) and body.get("privacy"):
            if body["privacy"] not in PRIVACY_MODES:
                return {"ok": False, "error": "bad_privacy"}
            updates["privacy"] = body["privacy"]
        if body.get("captains_can_edit_roles") is not None:
            updates["captains_can_edit_roles"] = bool(body.get("captains_can_edit_roles"))
        if not updates:
            return {"ok": False, "error": "nothing_to_update"}
        _activity_push(clan, "settings", "⚙️ Clan settings updated")
        updates["activity"] = clan.get("activity")
        _clans(db).document(clan_id).set(updates, merge=True)
        return {"ok": True}

    if action == "custom-role":
        is_owner = clan.get("owner_uid") == uid
        if not (is_owner or _has_perm(clan, uid, "edit_custom_roles")):
            return {"ok": False, "error": "no_permission"}
        op = str(body.get("op") or "create")
        roles = list(clan.get("custom_roles") or [])
        if op == "delete":
            rid = str(body.get("id") or "")
            roles = [r for r in roles if r.get("id") != rid]
            members = clan.get("members") or {}
            for m_uid, mem in members.items():
                if mem.get("custom_role_id") == rid:
                    members[m_uid] = {**mem, "custom_role_id": None}
            _clans(db).document(clan_id).set({"custom_roles": roles, "members": members}, merge=True)
            return {"ok": True}
        raw = body.get("role") if isinstance(body.get("role"), dict) else {}
        rname = str(raw.get("name") or "").strip()[:24]
        if len(rname) < 2 or text_is_profane(rname):
            return {"ok": False, "error": "bad_role_name"}
        # Only the whitelisted, never-owner-level permissions can be granted —
        # and a captain can never hand out more than a captain has.
        perms = {p: bool((raw.get("perms") or {}).get(p)) for p in CUSTOM_PERMS}
        if op == "create":
            if len(roles) >= CLAN_MAX_CUSTOM_ROLES:
                return {"ok": False, "error": "too_many_roles"}
            roles.append({"id": "r" + secrets.token_hex(3), "name": rname, "perms": perms})
        else:  # edit
            rid = str(raw.get("id") or body.get("id") or "")
            hit = next((r for r in roles if r.get("id") == rid), None)
            if not hit:
                return {"ok": False, "error": "no_such_role"}
            hit["name"], hit["perms"] = rname, perms
        _activity_push(clan, "roles", f"🧩 Custom role “{rname}” {'created' if op == 'create' else 'updated'}")
        _clans(db).document(clan_id).set({"custom_roles": roles, "activity": clan.get("activity")}, merge=True)
        return {"ok": True, "roles": roles}

    if action == "announce":
        if not _has_perm(clan, uid, "post_announcements"):
            return {"ok": False, "error": "no_permission"}
        text = censor_text(str(body.get("text") or "").strip()[:CLAN_CHAT_MAX])
        if not text:
            return {"ok": False, "error": "empty"}
        pin = bool(body.get("pin"))
        me = _member_of(clan, uid) or {}
        if pin and not _has_perm(clan, uid, "pin_announcements"):
            return {"ok": False, "error": "no_permission"}
        updates: Dict[str, Any] = {}
        if pin:
            updates["pinned_announcement"] = {"text": text, "by": me.get("name"), "ts": _now()}
        _activity_push(clan, "announce", f"📣 {me.get('name')}: {text[:80]}")
        updates["activity"] = clan.get("activity")
        _clans(db).document(clan_id).set(updates, merge=True)
        try:
            _clans(db).document(clan_id).collection("chat").document(
                f"m{_now()}_{secrets.token_hex(3)}").set({
                    "ts": _now(), "uid": uid, "name": me.get("name"),
                    "kind": "announce", "text": text})
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] announce chat write failed: {exc}")
        return {"ok": True}

    if action == "unpin":
        if not _has_perm(clan, uid, "pin_announcements"):
            return {"ok": False, "error": "no_permission"}
        _clans(db).document(clan_id).set({"pinned_announcement": None}, merge=True)
        return {"ok": True}

    if action == "chat-send":
        me = _member_of(clan, uid) or {}
        muted_until = int((clan.get("muted") or {}).get(uid) or 0)
        if muted_until > _now():
            return {"ok": False, "error": "muted", "until": muted_until}
        kind = str(body.get("kind") or "msg")
        if kind not in ("msg", "game_invite", "tourney_invite"):
            kind = "msg"
        text = censor_text(str(body.get("text") or "").strip()[:CLAN_CHAT_MAX])
        if not text:
            return {"ok": False, "error": "empty"}
        doc = {"ts": _now(), "uid": uid, "name": me.get("name"), "kind": kind,
               "text": text}
        if kind == "game_invite" and body.get("room_id"):
            doc["room_id"] = str(body.get("room_id"))[:12].upper()
        if kind == "tourney_invite" and body.get("tid"):
            doc["tid"] = str(body.get("tid"))[:24]
        mid = f"m{_now()}_{secrets.token_hex(3)}"
        _clans(db).document(clan_id).collection("chat").document(mid).set(doc)
        # Occasional prune so the unordered limit() scan below can never fill
        # up with ancient messages and starve the recent ones.
        try:
            if secrets.randbelow(20) == 0:
                cutoff = _now() - 14 * 24 * 3600
                col = _clans(db).document(clan_id).collection("chat")
                for old in col.limit(400).stream():
                    if int(((old.to_dict() or {}).get("ts")) or 0) < cutoff:
                        col.document(old.id).delete()
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] chat prune failed: {exc}")
        return {"ok": True, "id": mid}

    if action == "chat-get":
        since = int(body.get("since") or 0)
        rows = []
        col = _clans(db).document(clan_id).collection("chat")
        try:
            # Newest-first server-side so a long-lived clan chat never streams
            # the whole history on every 3.5s poll. Falls back to a plain
            # capped scan where order_by isn't available (tests / old SDKs).
            try:
                cursor = col.order_by("ts", direction="DESCENDING").limit(CLAN_CHAT_FETCH)
            except Exception:
                cursor = col.limit(600)
            for doc in cursor.stream():
                d = doc.to_dict() or {}
                if int(d.get("ts") or 0) <= since or d.get("deleted"):
                    continue
                rows.append({"id": doc.id, **d})
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] chat read failed: {exc}")
        rows.sort(key=lambda r: int(r.get("ts") or 0))
        muted_until = int((clan.get("muted") or {}).get(uid) or 0)
        return {"ok": True, "messages": rows[-CLAN_CHAT_FETCH:],
                "muted_until": muted_until if muted_until > _now() else 0,
                "pinned": clan.get("pinned_announcement")}

    if action == "chat-mod":
        if not _has_perm(clan, uid, "moderate_chat"):
            return {"ok": False, "error": "no_permission"}
        op = str(body.get("op") or "")
        if op == "delete" and body.get("id"):
            _clans(db).document(clan_id).collection("chat").document(str(body["id"])).set(
                {"deleted": True}, merge=True)
            return {"ok": True}
        if op == "mute" and body.get("uid"):
            target = str(body["uid"])
            if target == clan.get("owner_uid"):
                return {"ok": False, "error": "cannot_mute_owner"}
            mins = max(5, min(1440, int(body.get("minutes") or 30)))
            muted = clan.get("muted") or {}
            muted[target] = _now() + mins * 60
            _clans(db).document(clan_id).set({"muted": muted}, merge=True)
            return {"ok": True, "until": muted[target]}
        if op == "unmute" and body.get("uid"):
            # merge=True merges nested maps, so popping the key locally would
            # leave the stored mute in place — delete the field explicitly.
            _clans(db).document(clan_id).set(
                {"muted": {str(body["uid"]): _field_delete()}}, merge=True)
            return {"ok": True}
        return {"ok": False, "error": "bad_op"}

    if action == "events":
        op = str(body.get("op") or "list")
        events = [e for e in (clan.get("events") or []) if isinstance(e, dict)]
        me = _member_of(clan, uid) or {}
        if op == "list":
            return {"ok": True, "events": events}
        if op == "create":
            if not _has_perm(clan, uid, "create_events"):
                return {"ok": False, "error": "no_permission"}
            events = [e for e in events if int(e.get("ts") or 0) > _now() - 3600][:CLAN_MAX_EVENTS - 1]
            name = str(body.get("name") or "").strip()[:60]
            if not name or text_is_profane(name):
                return {"ok": False, "error": "bad_name"}
            ev = {"id": "e" + secrets.token_hex(3), "name": name,
                  "ts": max(_now(), int(body.get("ts") or 0)),
                  "desc": str(body.get("desc") or "").strip()[:200],
                  "host_uid": uid, "host_name": me.get("name"),
                  "attending": [uid], "reminders": []}
            events.append(ev)
            _activity_push(clan, "event", f"📅 Event scheduled: {name}")
            _clans(db).document(clan_id).set({"events": events, "activity": clan.get("activity")}, merge=True)
            _chat_system(db, clan_id, f"📅 New clan event: {name} — check the Events tab!")
            return {"ok": True, "events": events}
        eid = str(body.get("id") or "")
        ev = next((e for e in events if e.get("id") == eid), None)
        if not ev:
            return {"ok": False, "error": "no_event"}
        if op == "delete":
            if ev.get("host_uid") != uid and not _has_perm(clan, uid, "create_events"):
                return {"ok": False, "error": "no_permission"}
            events = [e for e in events if e.get("id") != eid]
        elif op == "join":
            if uid not in ev.get("attending", []):
                ev.setdefault("attending", []).append(uid)
        elif op == "leave":
            ev["attending"] = [u for u in ev.get("attending", []) if u != uid]
            ev["reminders"] = [u for u in ev.get("reminders", []) if u != uid]
        elif op == "remind":
            if uid not in ev.get("reminders", []):
                ev.setdefault("reminders", []).append(uid)
        else:
            return {"ok": False, "error": "bad_op"}
        _clans(db).document(clan_id).set({"events": events}, merge=True)
        return {"ok": True, "events": events}

    if action == "rival":
        # Friendly season rivalry: ONE rival clan per season, profile stat
        # comparison only. Deliberately awards nothing — a rivalry that paid
        # out would just be two clans farming each other (see spec).
        if not (clan.get("owner_uid") == uid or _has_perm(clan, uid, "post_announcements")):
            return {"ok": False, "error": "no_permission"}
        op = str(body.get("op") or "set")
        rivals = dict(clan.get("rivals") or {})
        if op == "clear":
            # merge=True never drops a nested key — clear it explicitly.
            _clans(db).document(clan_id).set({"rivals": {sid: _field_delete()}}, merge=True)
            return {"ok": True, "rival": None}
        target = str(body.get("clan_id") or "").strip()
        if not target or target == clan_id:
            return {"ok": False, "error": "bad_clan"}
        tsnap = _clans(db).document(target).get()
        if not tsnap.exists:
            return {"ok": False, "error": "no_clan"}
        rivals[sid] = target
        _activity_push(clan, "rival",
                       f"⚔️ Friendly rivalry declared with {(tsnap.to_dict() or {}).get('name')}")
        _clans(db).document(clan_id).set({"rivals": rivals,
                                          "activity": clan.get("activity")}, merge=True)
        _chat_system(db, clan_id,
                     f"⚔️ {(tsnap.to_dict() or {}).get('name')} is our friendly rival this season!")
        return {"ok": True, "rival": target}

    if action == "vote-critter":
        icon = str(body.get("icon") or "").strip()
        if not re.match(r"^/avatars/[a-z0-9\-]+\.(png|webp)$", icon):
            return {"ok": False, "error": "bad_icon"}
        transactional = _txn_helpers()
        clan_ref = _clans(db).document(clan_id)
        txn = db.transaction()

        @transactional
        def _run(t):
            snap = clan_ref.get(transaction=t)
            if not snap.exists:
                return {"ok": False, "error": "no_clan"}
            c = snap.to_dict() or {}
            slot = _season_slot(c, sid)
            slot["critter_votes"][uid] = icon
            t.set(clan_ref, c)
            return {"ok": True}

        return _run(txn)

    if action == "claim-game":
        return claim_game_points(uid, str(body.get("room_id") or ""))

    return {"ok": False, "error": "unknown_action"}


def _join_clan_direct(db, uid: str, clan_id: str) -> Dict[str, Any]:
    """Accept-request path: joins without privacy checks (a reviewer already
    said yes). Same one-clan / capacity / cooldown-preserving rules."""
    transactional = _txn_helpers()
    clan_ref = _clans(db).document(clan_id)
    user_ref = _users(db).document(uid)
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        csnap = clan_ref.get(transaction=t)
        if not csnap.exists:
            return {"ok": False, "error": "no_clan"}
        clan = csnap.to_dict() or {}
        usnap = user_ref.get(transaction=t)
        if not usnap.exists:
            return {"ok": False, "error": "no_user"}
        udoc = usnap.to_dict() or {}
        if udoc.get("clan_id"):
            return {"ok": False, "error": "already_in_clan"}
        members = clan.get("members") or {}
        if len(members) >= CLAN_MAX_MEMBERS:
            return {"ok": False, "error": "clan_full"}
        nick = udoc.get("nickname") or udoc.get("username") or "Player"
        members[uid] = {"name": nick, "role": "member", "custom_role_id": None,
                        "joined_ts": _now(), "avatar": str(udoc.get("avatar_url") or "")}
        clan["members"] = members
        clan["join_requests"] = [r for r in (clan.get("join_requests") or [])
                                 if str(r.get("uid")) != uid]
        _activity_push(clan, "join", f"🌊 {nick} joined the clan")
        t.set(clan_ref, clan)
        t.set(user_ref, {"clan_id": clan_id, "clan_joined_ts": _now()}, merge=True)
        return {"ok": True, "nick": nick}

    try:
        res = _run(txn)
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] request-accept join failed: {exc}")
        return {"ok": False, "error": "join_failed"}
    if res.get("ok"):
        _chat_system(db, clan_id, f"🌊 {res.pop('nick', 'A new member')} joined the clan — say hi!")
    return res
