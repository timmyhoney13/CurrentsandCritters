"""Currents and Critters — Developer Analytics (server-authoritative, admin only).

Wired additively into multiplayer_server (same pattern as clan_server /
prestige_server):
    import analytics_server
    analytics_server.init(...)                      # in main()
    if analytics_server.handle_post(self, parsed, body):   # in do_POST

WHY THE WHOLE THING IS SERVER-SIDE
The browser cannot compute any of this. Firestore security rules block reading
other players' documents (by design — emails and profiles are private), and the
game-history records live on the Render disk, which no client can see. So every
number here is derived here, with the service account, and shipped to the
dashboard already aggregated. No raw player document ever leaves this module.

WHO CAN CALL IT
POST only, and every call carries a Firebase ID token that is verified here and
then checked against the account's own `is_admin` flag (or ADMIN_EMAIL). A uid
in the body is never trusted. There is no GET form on purpose: analytics answers
must never be reachable by pasting a URL.

THE THREE SOURCES, AND WHAT EACH ONE CAN HONESTLY ANSWER
  • Firestore `users`      — accounts: joins (created_at), activity
                             (last_active/online), progress (stats.*), wallet
                             (stats.critter_coins). This answers "who is
                             playing" and "are they coming back".
  • games_history/*.json   — one record per finished human game, written by
                             multiplayer_server._save_game_history. This answers
                             "what happened in the games" — completion, length,
                             sizes, strategies, every animal on every board.
  • competitive_games/*.json — ranked matches, forfeits included.
Anything none of those can support is reported as "no data", NEVER as a zero: a
zero is a measurement and would read as "this dropped to nothing overnight".

CACHING
Opening a tab must not cost a full Firestore scan. The account snapshot is read
at most once per _USERS_TTL_SEC and shared by every section; the game records
are cached against the history directory's own (count, newest-mtime) signature,
so a newly finished game invalidates them immediately and nothing else does.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# ── Injected by init() (no circular import with multiplayer_server) ──────────
_get_firestore: Optional[Callable[[], Any]] = None
_verify_token: Optional[Callable[[str], Optional[dict]]] = None
_games_dir: str = ""
_competitive_dir: str = ""
_live_snapshot: Optional[Callable[[], Dict[str, Any]]] = None
_app_version: str = ""

ADMIN_EMAIL = "currentsandcritters@gmail.com"

# A day of history is plenty of granularity for every chart the dashboard draws,
# and the longest range it offers is 90 days.
DAY = 86400
MAX_RANGE_DAYS = 365
DEFAULT_RANGE_DAYS = 30


def init(*, get_firestore, verify_token, games_history_dir, competitive_games_dir,
         live_snapshot=None, app_version="") -> None:
    global _get_firestore, _verify_token, _games_dir, _competitive_dir
    global _live_snapshot, _app_version
    _get_firestore = get_firestore
    _verify_token = verify_token
    _games_dir = str(games_history_dir or "")
    _competitive_dir = str(competitive_games_dir or "")
    _live_snapshot = live_snapshot
    _app_version = str(app_version or "")


# ═══════════════════════════════════════════════════════════════════════════
#  SMALL HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def _now() -> int:
    return int(time.time())


def _int(v, default=0) -> int:
    try:
        if isinstance(v, bool):
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(v, default=0.0) -> float:
    try:
        f = float(v)
        return f if f == f else default          # NaN guard
    except (TypeError, ValueError):
        return default


def _pct(part: float, whole: float) -> Optional[float]:
    """Percentage, or None when the denominator is zero — a rate with nothing
    under it is unknown, not 0%."""
    if not whole:
        return None
    return round(100.0 * part / whole, 1)


def _ts(value) -> int:
    """Unix seconds from a Firestore timestamp / datetime / number, 0 if absent."""
    if value is None:
        return 0
    if hasattr(value, "timestamp"):
        try:
            return int(value.timestamp())
        except Exception:  # noqa: BLE001
            return 0
    n = _float(value, 0.0)
    # Firestore also stores millisecond epochs in a few older client writes.
    if n > 4e10:
        n /= 1000.0
    return int(n)


def _day_key(unix: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(unix)))


def _day_series(start: int, end: int) -> List[str]:
    """Every YYYY-MM-DD from start to end inclusive (UTC), oldest first."""
    out: List[str] = []
    cur = int(start) - (int(start) % DAY)
    stop = int(end)
    while cur <= stop and len(out) <= MAX_RANGE_DAYS + 2:
        out.append(_day_key(cur))
        cur += DAY
    return out


def _bucket(days: List[str], stamps: Iterable[int]) -> List[int]:
    """Count stamps into a day-keyed series (same order as `days`)."""
    idx = {d: i for i, d in enumerate(days)}
    out = [0] * len(days)
    for s in stamps:
        i = idx.get(_day_key(s))
        if i is not None:
            out[i] += 1
    return out


def _top(counter: Dict[Any, Any], n: int, key=None) -> List[Tuple[Any, Any]]:
    items = list(counter.items())
    items.sort(key=key or (lambda kv: -_float(kv[1])))
    return items[:n]


def _mean(values: List[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _median(values: List[float]) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


# ═══════════════════════════════════════════════════════════════════════════
#  ACCESS CONTROL
# ═══════════════════════════════════════════════════════════════════════════
def _admin_claims(body: Dict[str, Any]) -> Optional[dict]:
    """Verified claims for an ADMIN account, or None.

    Two independent gates, both required: the ID token must verify (so the
    caller really is that account), and the account must be flagged admin in
    Firestore (or be the known admin email). A client-supplied uid or email is
    never enough — those are trivially forged from devtools.
    """
    if _verify_token is None:
        return None
    tok = body.get("idToken") if isinstance(body.get("idToken"), str) else ""
    claims = _verify_token(tok) if tok else None
    if not claims or not claims.get("uid"):
        return None
    email = str(claims.get("email") or "").strip().lower()
    if email and email == ADMIN_EMAIL:
        return claims
    db = _get_firestore() if _get_firestore else None
    if db is None:
        return None
    try:
        snap = db.collection("users").document(str(claims["uid"])).get()
        data = (snap.to_dict() or {}) if snap.exists else {}
    except Exception as exc:  # noqa: BLE001
        print(f"[analytics] admin lookup failed: {exc}")
        return None
    if data.get("is_admin") is True:
        return claims
    if str(data.get("email") or "").strip().lower() == ADMIN_EMAIL:
        return claims
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  SOURCE 1 — ACCOUNTS (Firestore `users`)
# ═══════════════════════════════════════════════════════════════════════════
# One scan feeds every section, so it is read at most this often. Two minutes
# keeps "Players Online Now" honest while a dashboard left open all afternoon
# still costs a handful of scans, not one per refresh tick.
_USERS_TTL_SEC = 120.0
_USERS_CACHE: Dict[str, Any] = {"at": 0.0, "rows": None}
_USERS_LOCK = threading.Lock()

# Only these fields ever leave Firestore. Emails come across solely to identify
# the developer's own test accounts (the "Include test accounts" filter) and are
# never put in a response payload.
_USER_FIELDS = [
    "nickname", "friend_code", "email", "is_admin", "online", "last_active",
    "created_at", "stats", "unlocked_icons", "unlocked_backgrounds", "prestige",
    "clan_id", "supporter_tier", "guest",
]


def _load_users(force: bool = False) -> List[Dict[str, Any]]:
    """Every account, flattened to just what the dashboard measures."""
    now = time.time()
    with _USERS_LOCK:
        if not force and _USERS_CACHE["rows"] is not None and now - _USERS_CACHE["at"] < _USERS_TTL_SEC:
            return _USERS_CACHE["rows"]
    db = _get_firestore() if _get_firestore else None
    if db is None:
        return []
    rows: List[Dict[str, Any]] = []
    try:
        try:
            stream = db.collection("users").select(_USER_FIELDS).stream()
        except Exception:  # noqa: BLE001 — older SDKs without select()
            stream = db.collection("users").stream()
        for doc in stream:
            d = doc.to_dict() or {}
            st = d.get("stats") if isinstance(d.get("stats"), dict) else {}
            pr = d.get("prestige") if isinstance(d.get("prestige"), dict) else {}
            rows.append({
                "uid": doc.id,
                "nickname": str(d.get("nickname") or ""),
                "friend_code": str(d.get("friend_code") or ""),
                "email_lower": str(d.get("email") or "").strip().lower(),
                "is_admin": d.get("is_admin") is True,
                "is_guest": d.get("guest") is True,
                "online": d.get("online") is True,
                "last_active": _ts(d.get("last_active")),
                "created_at": _ts(d.get("created_at")),
                "games": _int(st.get("completed_games")),
                "wins": _int(st.get("normal_wins")) + _int(st.get("competitive_wins")),
                "comp_games": sum(_int(v) for v in (st.get("comp_games_by_size") or {}).values())
                              if isinstance(st.get("comp_games_by_size"), dict) else 0,
                "total_xp": _int(st.get("total_xp")),
                "level": _int(st.get("level") or st.get("player_level"), 1),
                "coins": _int(st.get("critter_coins")),
                "total_score": _int(st.get("total_score")),
                "highest_score": _int(st.get("highest_score")),
                "icons": len(d.get("unlocked_icons") or []) if isinstance(d.get("unlocked_icons"), list) else 0,
                "backgrounds": len(d.get("unlocked_backgrounds") or []) if isinstance(d.get("unlocked_backgrounds"), list) else 0,
                "prestige_level": _int(pr.get("level")),
                "clan_id": str(d.get("clan_id") or ""),
                "supporter_tier": str(d.get("supporter_tier") or ""),
            })
    except Exception as exc:  # noqa: BLE001
        print(f"[analytics] user scan failed: {exc}")
        return []
    with _USERS_LOCK:
        _USERS_CACHE["rows"] = rows
        _USERS_CACHE["at"] = time.time()
    return rows


def _filter_users(rows: List[Dict[str, Any]], f: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Apply the account-level advanced filters."""
    out = rows
    if not f.get("include_test"):
        out = [r for r in out if not r["is_admin"] and r["email_lower"] != ADMIN_EMAIL]
    if not f.get("include_guests"):
        out = [r for r in out if not r["is_guest"]]
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  SOURCE 2 — FINISHED GAMES (the history directory)
# ═══════════════════════════════════════════════════════════════════════════
# Keyed on the directory's own (file count, newest mtime): a game that finishes
# changes both, so the very next call re-reads. Nothing else invalidates it,
# which is what keeps a busy dashboard off the disk.
_GAMES_CACHE: Dict[str, Any] = {"sig": None, "rows": None}
_COMP_CACHE: Dict[str, Any] = {"sig": None, "rows": None}
_GAMES_LOCK = threading.Lock()
# A hard ceiling so a disk with years of history can never blow the process up.
_MAX_GAME_FILES = 20000


def _dir_signature(path: str) -> Tuple[int, int]:
    try:
        newest = 0
        count = 0
        with os.scandir(path) as it:
            for e in it:
                if not e.name.startswith("game_") or not e.name.endswith(".json"):
                    continue
                count += 1
                try:
                    newest = max(newest, int(e.stat().st_mtime))
                except OSError:
                    continue
        return count, newest
    except OSError:
        return 0, 0


def _read_records(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        names = [n for n in os.listdir(path) if n.startswith("game_") and n.endswith(".json")]
    except OSError:
        return rows
    # Newest first, so the cap drops the OLDEST history rather than the games
    # every chart is actually about.
    names.sort(reverse=True)
    for name in names[:_MAX_GAME_FILES]:
        try:
            with open(os.path.join(path, name), "r", encoding="utf-8") as fh:
                rec = json.load(fh)
            if isinstance(rec, dict):
                rows.append(rec)
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def _load_games() -> List[Dict[str, Any]]:
    sig = _dir_signature(_games_dir)
    with _GAMES_LOCK:
        if _GAMES_CACHE["sig"] == sig and _GAMES_CACHE["rows"] is not None:
            return _GAMES_CACHE["rows"]
    rows = _read_records(_games_dir)
    with _GAMES_LOCK:
        _GAMES_CACHE["sig"] = sig
        _GAMES_CACHE["rows"] = rows
    return rows


def _load_comp_games() -> List[Dict[str, Any]]:
    sig = _dir_signature(_competitive_dir)
    with _GAMES_LOCK:
        if _COMP_CACHE["sig"] == sig and _COMP_CACHE["rows"] is not None:
            return _COMP_CACHE["rows"]
    rows = _read_records(_competitive_dir)
    with _GAMES_LOCK:
        _COMP_CACHE["sig"] = sig
        _COMP_CACHE["rows"] = rows
    return rows


def _game_when(rec: Dict[str, Any]) -> int:
    return _int(rec.get("recorded_unix"))


def _game_completed(rec: Dict[str, Any]) -> bool:
    """A game that reached its real ending. `mode` is written as "truncated"
    when the END GAME card never resolved (everyone left, the room errored)."""
    return str(rec.get("mode") or "") != "truncated"


def _game_duration(rec: Dict[str, Any]) -> Optional[int]:
    """Seconds of play, or None for records written before the timing fields
    existed. None must stay None — averaging a missing duration in as 0 would
    quietly drag "how long is a game" toward zero."""
    d = _int(rec.get("duration_sec"), -1)
    if d > 0:
        return d
    start, end = _int(rec.get("started_unix")), _int(rec.get("ended_unix"))
    if start > 0 and end > start:
        return end - start
    return None


def _filter_games(rows: List[Dict[str, Any]], f: Dict[str, Any],
                  start: int, end: int) -> List[Dict[str, Any]]:
    out = []
    want_mode = str(f.get("mode") or "all")
    want_size = _int(f.get("player_count"), 0)
    for rec in rows:
        when = _game_when(rec)
        if when < start or when > end:
            continue
        if want_size and _int(rec.get("player_count")) != want_size:
            continue
        if want_mode != "all":
            mode = "competitive" if rec.get("mode") == "competitive" else (
                "team" if rec.get("team_mode") else "casual")
            if mode != want_mode:
                continue
        # "Include bot games" off means: only games at least two humans played.
        if not f.get("include_bots") and _int(rec.get("human_count")) < 2:
            continue
        out.append(rec)
    return out


def _human_players(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [p for p in (rec.get("players") or []) if isinstance(p, dict) and p.get("is_human")]


# ═══════════════════════════════════════════════════════════════════════════
#  FILTERS & RANGE
# ═══════════════════════════════════════════════════════════════════════════
def _filters(body: Dict[str, Any]) -> Dict[str, Any]:
    days = _int(body.get("days"), DEFAULT_RANGE_DAYS)
    days = max(1, min(MAX_RANGE_DAYS, days))
    return {
        "days": days,
        "compare": bool(body.get("compare")),
        "include_bots": bool(body.get("include_bots")),
        "include_test": bool(body.get("include_test")),
        "include_guests": bool(body.get("include_guests")),
        "mode": str(body.get("mode") or "all"),
        "player_count": _int(body.get("player_count"), 0),
    }


def _window(f: Dict[str, Any]) -> Tuple[int, int, int, int]:
    """(start, end, prev_start, prev_end) — the range and the one before it."""
    end = _now()
    start = end - f["days"] * DAY
    return start, end, start - f["days"] * DAY, start


def _delta(now_val: Optional[float], prev_val: Optional[float]) -> Optional[float]:
    """Percent change vs the previous period. None when there is no baseline —
    "+100%" off a zero baseline is noise, not information."""
    if now_val is None or prev_val is None or not prev_val:
        return None
    return round(100.0 * (now_val - prev_val) / prev_val, 1)


def _card(label: str, value, *, unit="", delta=None, hint="", spark=None,
          tone="neutral") -> Dict[str, Any]:
    """One summary card. `value=None` renders as "No data yet", never as 0."""
    return {"label": label, "value": value, "unit": unit, "delta": delta,
            "hint": hint, "spark": spark or [], "tone": tone}


# ═══════════════════════════════════════════════════════════════════════════
#  RETENTION
# ═══════════════════════════════════════════════════════════════════════════
def _retention(users: List[Dict[str, Any]], day_n: int, now: int) -> Optional[Dict[str, Any]]:
    """Share of accounts that were still being seen `day_n` days after joining.

    Only accounts that have HAD the chance count: someone who signed up
    yesterday cannot yet have a 7-day return, and including them would push
    every retention number down as the game grows.
    """
    eligible = [u for u in users if u["created_at"] > 0 and now - u["created_at"] >= day_n * DAY]
    if not eligible:
        return None
    returned = [u for u in eligible if u["last_active"] - u["created_at"] >= day_n * DAY]
    return {"day": day_n, "cohort": len(eligible), "returned": len(returned),
            "rate": _pct(len(returned), len(eligible))}


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════
def _section_overview(f: Dict[str, Any]) -> Dict[str, Any]:
    start, end, pstart, pend = _window(f)
    now = end
    users = _filter_users(_load_users(), f)
    games_all = _load_games()
    games = _filter_games(games_all, f, start, end)
    prev_games = _filter_games(games_all, f, pstart, pend)
    days = _day_series(start, end)

    new_now = [u for u in users if start <= u["created_at"] <= end]
    new_prev = [u for u in users if pstart <= u["created_at"] < pend]
    active_now = [u for u in users if start <= u["last_active"] <= end]
    active_prev = [u for u in users if pstart <= u["last_active"] < pend]
    # "Returning" = active in this window, but joined before it started.
    returning_now = [u for u in active_now if u["created_at"] < start]
    returning_prev = [u for u in active_prev if u["created_at"] < pstart]

    completed = [g for g in games if _game_completed(g)]
    prev_completed = [g for g in prev_games if _game_completed(g)]
    durations = [d for d in (_game_duration(g) for g in completed) if d]

    live = _live_snapshot() if _live_snapshot else {}
    online = _int(live.get("online_players"), -1)
    if online < 0:
        online = len([u for u in users if u["online"] and now - u["last_active"] <= 300])

    growth = _bucket(days, [u["created_at"] for u in new_now])
    returning_series = _bucket(days, [u["last_active"] for u in returning_now])
    cumulative: List[int] = []
    base = len([u for u in users if u["created_at"] < start])
    for v in growth:
        base += v
        cumulative.append(base)
    games_series = _bucket(days, [_game_when(g) for g in completed])
    left_early_series = _bucket(days, [_game_when(g) for g in games if not _game_completed(g)])

    retention = [r for r in (_retention(users, d, now) for d in (1, 7, 30)) if r]
    ret7 = next((r for r in retention if r["day"] == 7), None)

    cards = [
        _card("New players", len(new_now), delta=_delta(len(new_now), len(new_prev)),
              spark=growth, hint="Accounts created in this date range."),
        _card("Active players", len(active_now), delta=_delta(len(active_now), len(active_prev)),
              hint="Accounts seen in the game at least once in this date range."),
        _card("Players who returned", len(returning_now),
              delta=_delta(len(returning_now), len(returning_prev)),
              spark=returning_series,
              hint="Active in this range, but joined before it started."),
        _card("Came back after 7 days", ret7["rate"] if ret7 else None, unit="%",
              hint="Of players who joined at least 7 days ago, the share still "
                   "playing a week later." if ret7 else "",
              tone="good" if ret7 and (ret7["rate"] or 0) >= 30 else "neutral"),
        _card("Games completed", len(completed), delta=_delta(len(completed), len(prev_completed)),
              spark=games_series, hint="Games that reached their real ending."),
        _card("Games finished", _pct(len(completed), len(games)), unit="%",
              delta=_delta(_pct(len(completed), len(games)) or 0,
                           _pct(len(prev_completed), len(prev_games)) or 0),
              hint="Share of started games that were played to the end.",
              tone="warn" if (_pct(len(completed), len(games)) or 100) < 70 else "neutral"),
        _card("Players online now", online, hint="Signed in and seen in the last few minutes."),
        _card("Games being played", _int(live.get("active_games"), 0),
              hint="Rooms with a game running right now."),
        _card("Average game length", round((_mean(durations) or 0) / 60.0, 1) if durations else None,
              unit=" min", hint="Median is "
                                f"{round((_median(durations) or 0) / 60.0, 1)} min." if durations else ""),
        _card("Server", "Healthy" if live.get("ok", True) else "Needs attention",
              tone="good" if live.get("ok", True) else "bad",
              hint=str(live.get("status_note") or "All checks passing.")),
    ]

    return {
        "cards": cards,
        "growth": {
            "days": days,
            "series": {"new": growth, "returning": returning_series, "cumulative": cumulative},
        },
        "games": {"days": days, "completed": games_series, "left_early": left_early_series},
        "retention": retention,
        "live": _live_panel(live, users, now),
        "alerts": _alerts(f, users, games, prev_games, live)[:3],
    }


def _live_panel(live: Dict[str, Any], users: List[Dict[str, Any]], now: int) -> Dict[str, Any]:
    recent = sorted((u for u in users if u["created_at"] > 0), key=lambda u: -u["created_at"])[:5]
    online = _int(live.get("online_players"), -1)
    if online < 0:
        online = len([u for u in users if u["online"] and now - u["last_active"] <= 300])
    return {
        "online_players": online,
        "active_games": _int(live.get("active_games"), 0),
        "matchmaking": _int(live.get("matchmaking"), 0),
        "open_lobbies": _int(live.get("open_lobbies"), 0),
        "server_ok": bool(live.get("ok", True)),
        "server_note": str(live.get("status_note") or "All checks passing."),
        "recent_signups": [
            {"name": u["nickname"] or "Player", "ago": max(0, now - u["created_at"])}
            for u in recent
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  ALERTS — only things a developer would actually act on
# ═══════════════════════════════════════════════════════════════════════════
# Below these counts a swing is sampling noise, not a signal. Alerting on a
# 2-game day is how a dashboard trains its owner to ignore it.
_ALERT_MIN_GAMES = 15
_ALERT_MIN_PLAYERS = 10


def _alerts(f, users, games, prev_games, live) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    now = _now()

    if not live.get("ok", True):
        out.append({"level": "bad", "title": "Server needs attention",
                    "detail": str(live.get("status_note") or "A health check is failing."),
                    "section": "technical"})

    if _get_firestore and _get_firestore() is None:
        out.append({"level": "bad", "title": "Player database not connected",
                    "detail": "Firebase isn't configured on this server, so account "
                              "numbers can't be read.", "section": "technical"})

    completed = [g for g in games if _game_completed(g)]
    if len(games) >= _ALERT_MIN_GAMES:
        rate = _pct(len(completed), len(games)) or 0
        if rate < 70:
            out.append({"level": "warn", "title": "Players are leaving games early",
                        "detail": f"Only {rate}% of {len(games)} games were played to the "
                                  f"end in this range.", "section": "gameplay"})

    if len(prev_games) >= _ALERT_MIN_GAMES and len(games) < len(prev_games) * 0.6:
        out.append({"level": "warn", "title": "Fewer games than last period",
                    "detail": f"{len(games)} games this period vs {len(prev_games)} before it.",
                    "section": "gameplay"})

    ret = _retention(users, 7, now)
    if ret and ret["cohort"] >= _ALERT_MIN_PLAYERS and (ret["rate"] or 0) < 20:
        out.append({"level": "warn", "title": "Few players come back after a week",
                    "detail": f"{ret['rate']}% of {ret['cohort']} players returned a week "
                              f"after joining.", "section": "players"})

    stuck = _int(live.get("stuck_rooms"), 0)
    if stuck:
        out.append({"level": "warn", "title": "Games stuck open",
                    "detail": f"{stuck} rooms have been idle far longer than a game takes.",
                    "section": "technical"})
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: PLAYERS
# ═══════════════════════════════════════════════════════════════════════════
def _section_players(f: Dict[str, Any]) -> Dict[str, Any]:
    start, end, pstart, pend = _window(f)
    now = end
    users = _filter_users(_load_users(), f)
    days = _day_series(start, end)

    new_now = [u for u in users if start <= u["created_at"] <= end]
    new_prev = [u for u in users if pstart <= u["created_at"] < pend]
    active_now = [u for u in users if start <= u["last_active"] <= end]
    returning = [u for u in active_now if u["created_at"] < start]

    growth = _bucket(days, [u["created_at"] for u in new_now])
    prev_growth = _bucket(_day_series(pstart, pend), [u["created_at"] for u in new_prev])
    cumulative: List[int] = []
    base = len([u for u in users if u["created_at"] < start])
    for v in growth:
        base += v
        cumulative.append(base)

    # How far players actually get before they stop — the honest version of a
    # funnel: every step is measured from the same account list.
    signed_up = len(users)
    played_one = len([u for u in users if u["games"] >= 1])
    played_five = len([u for u in users if u["games"] >= 5])
    came_back = len([u for u in users if u["last_active"] - u["created_at"] >= DAY])

    levels: Dict[str, int] = {}
    for u in users:
        band = "1–4" if u["level"] < 5 else "5–9" if u["level"] < 10 else \
               "10–24" if u["level"] < 25 else "25–49" if u["level"] < 50 else "50+"
        levels[band] = levels.get(band, 0) + 1

    top = sorted(users, key=lambda u: (-u["games"], -u["total_xp"]))[:25]
    return {
        "cards": [
            _card("New players", len(new_now), delta=_delta(len(new_now), len(new_prev)), spark=growth),
            _card("Active players", len(active_now)),
            _card("Players who returned", len(returning)),
            _card("Total accounts", len(users)),
        ],
        "growth": {"days": days, "series": {"new": growth, "returning":
                   _bucket(days, [u["last_active"] for u in returning]), "cumulative": cumulative},
                   "compare": prev_growth if f["compare"] else None},
        "retention": [r for r in (_retention(users, d, now) for d in (1, 3, 7, 14, 30)) if r],
        "funnel": [
            {"label": "Created an account", "value": signed_up},
            {"label": "Played a game", "value": played_one},
            {"label": "Came back another day", "value": came_back},
            {"label": "Played 5 games", "value": played_five},
        ],
        "levels": [{"label": k, "value": v} for k, v in
                   sorted(levels.items(), key=lambda kv: ["1–4", "5–9", "10–24", "25–49", "50+"].index(kv[0]))],
        "table": {
            "columns": [
                {"key": "name", "label": "Player", "always": True},
                {"key": "games", "label": "Games", "always": True},
                {"key": "wins", "label": "Wins", "always": True},
                {"key": "level", "label": "Level", "always": True},
                {"key": "last_seen", "label": "Last seen", "always": True},
                {"key": "joined", "label": "Joined"},
                {"key": "xp", "label": "XP"},
                {"key": "coins", "label": "Coins"},
                {"key": "icons", "label": "Critters"},
                {"key": "prestige", "label": "Prestige"},
            ],
            "rows": [{
                "name": u["nickname"] or "Player",
                "games": u["games"], "wins": u["wins"], "level": u["level"],
                "last_seen": u["last_active"], "joined": u["created_at"],
                "xp": u["total_xp"], "coins": u["coins"], "icons": u["icons"],
                "prestige": u["prestige_level"],
            } for u in top],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: GAMEPLAY
# ═══════════════════════════════════════════════════════════════════════════
def _section_gameplay(f: Dict[str, Any]) -> Dict[str, Any]:
    start, end, pstart, pend = _window(f)
    all_games = _load_games()
    games = _filter_games(all_games, f, start, end)
    prev = _filter_games(all_games, f, pstart, pend)
    days = _day_series(start, end)

    completed = [g for g in games if _game_completed(g)]
    left_early = [g for g in games if not _game_completed(g)]
    durations = [d for d in (_game_duration(g) for g in completed) if d]

    sizes: Dict[int, int] = {}
    modes: Dict[str, int] = {}
    strategies: Dict[str, int] = {}
    scores: List[int] = []
    for g in completed:
        sizes[_int(g.get("player_count"))] = sizes.get(_int(g.get("player_count")), 0) + 1
        mode = "Competitive" if g.get("mode") == "competitive" else (
            "Team" if g.get("team_mode") else "Casual")
        modes[mode] = modes.get(mode, 0) + 1
        for p in _human_players(g):
            s = str(p.get("strategy") or "Unknown")
            strategies[s] = strategies.get(s, 0) + 1
            scores.append(_int(p.get("score")))

    return {
        "cards": [
            _card("Games completed", len(completed),
                  delta=_delta(len(completed), len([g for g in prev if _game_completed(g)])),
                  spark=_bucket(days, [_game_when(g) for g in completed])),
            _card("Games finished", _pct(len(completed), len(games)), unit="%",
                  hint="Share of started games played to the end.",
                  tone="warn" if (_pct(len(completed), len(games)) or 100) < 70 else "neutral"),
            _card("Games players left early", len(left_early),
                  tone="warn" if len(left_early) > len(completed) * 0.3 else "neutral"),
            _card("Average game length", round((_mean(durations) or 0) / 60.0, 1) if durations else None,
                  unit=" min",
                  hint=f"From {len(durations)} games that recorded a length." if durations else ""),
        ],
        "volume": {"days": days,
                   "completed": _bucket(days, [_game_when(g) for g in completed]),
                   "left_early": _bucket(days, [_game_when(g) for g in left_early])},
        "sizes": [{"label": f"{k} players", "value": v} for k, v in sorted(sizes.items()) if k],
        "modes": [{"label": k, "value": v} for k, v in _top(modes, 6)],
        "strategies": [{"label": k, "value": v} for k, v in _top(strategies, 8)],
        "scores": _histogram(scores, 8, "pts"),
        "lengths": _histogram([round(d / 60.0) for d in durations], 8, " min"),
    }


def _histogram(values: List[int], buckets: int, unit: str) -> List[Dict[str, Any]]:
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return []
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return [{"label": f"{lo}{unit}", "value": len(vals)}]
    width = max(1, int((hi - lo) / buckets) + 1)
    out: Dict[int, int] = {}
    for v in vals:
        b = int((v - lo) // width)
        out[b] = out.get(b, 0) + 1
    return [{"label": f"{lo + b * width}–{lo + (b + 1) * width - 1}{unit}", "value": out.get(b, 0)}
            for b in range(min(buckets, max(out) + 1))]


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: CARDS
# ═══════════════════════════════════════════════════════════════════════════
# A card needs to have been seen this many times before its win rate says
# anything. Below it, one lucky game swings the number by tens of points.
_CARD_MIN_SAMPLE = 20
# How far from the middle a card's win rate has to sit before it is worth a look.
_CARD_FLAG_MARGIN = 12.0


def _section_cards(f: Dict[str, Any]) -> Dict[str, Any]:
    start, end, _ps, _pe = _window(f)
    games = _filter_games(_load_games(), f, start, end)
    min_sample = max(1, _int(f.get("min_sample"), _CARD_MIN_SAMPLE))

    played: Dict[str, int] = {}      # every copy played, counted once each
    appeared: Dict[str, int] = {}    # BOARDS it appeared on — the win-rate sample
    won_with: Dict[str, int] = {}
    species: Dict[str, int] = {}
    oceans: Dict[str, int] = {}
    boards = 0

    for g in games:
        if not _game_completed(g):
            continue
        winner = str(g.get("winner") or "")
        for p in _human_players(g):
            boards += 1
            is_win = str(p.get("name") or "") == winner
            # A win rate has to be per BOARD, not per copy: a board holding
            # three Mandarin Gobies is still one win, and counting it three
            # times would let a stackable animal inflate its own rate.
            seen: set = set()
            for ocean in (p.get("board") or []):
                if not isinstance(ocean, dict):
                    continue
                oname = str(ocean.get("ocean") or "")
                if oname:
                    oceans[oname] = oceans.get(oname, 0) + 1
                for a in (ocean.get("animals") or []):
                    if not isinstance(a, dict):
                        continue
                    name = str(a.get("name") or "")
                    if not name:
                        continue
                    played[name] = played.get(name, 0) + 1
                    sp = str(a.get("species") or "")
                    if sp:
                        species[sp] = species.get(sp, 0) + 1
                    seen.add(name)
            for name in seen:
                appeared[name] = appeared.get(name, 0) + 1
                if is_win:
                    won_with[name] = won_with.get(name, 0) + 1

    rows = []
    for name, n in appeared.items():
        wins = won_with.get(name, 0)
        rows.append({"name": name, "boards": n, "played": played.get(name, 0),
                     "wins": wins, "win_rate": _pct(wins, n),
                     "enough": n >= min_sample})
    rows.sort(key=lambda r: -r["played"])

    rated = [r for r in rows if r["enough"] and r["win_rate"] is not None]
    baseline = _mean([r["win_rate"] for r in rated])
    review = []
    if baseline is not None:
        for r in rated:
            gap = r["win_rate"] - baseline
            if abs(gap) >= _CARD_FLAG_MARGIN:
                review.append({**r, "gap": round(gap, 1),
                               "direction": "strong" if gap > 0 else "weak"})
        review.sort(key=lambda r: -abs(r["gap"]))

    return {
        "cards": [
            _card("Animals seen", len(rows), hint="Different animals played to a board."),
            _card("Boards measured", boards),
            _card("Typical win rate", round(baseline, 1) if baseline is not None else None, unit="%",
                  hint=f"Average across animals with {min_sample}+ boards."),
            _card("Worth a balance look", len(review) or None,
                  tone="warn" if review else "neutral",
                  hint=f"Win rate more than {int(_CARD_FLAG_MARGIN)} points from typical."),
        ],
        "min_sample": min_sample,
        "most_played": [{"label": r["name"], "value": r["played"]} for r in rows[:12]],
        "species": [{"label": k, "value": v} for k, v in _top(species, 10)],
        "oceans": [{"label": k, "value": v} for k, v in _top(oceans, 10)],
        "review": review[:20],
        "table": {
            "columns": [
                {"key": "name", "label": "Animal", "always": True},
                {"key": "played", "label": "Times played", "always": True},
                {"key": "boards", "label": "Boards", "always": True},
                {"key": "win_rate", "label": "Win rate", "always": True},
                {"key": "wins", "label": "Wins"},
            ],
            "rows": rows[:200],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: COMPETITIVE
# ═══════════════════════════════════════════════════════════════════════════
def _section_competitive(f: Dict[str, Any]) -> Dict[str, Any]:
    start, end, pstart, pend = _window(f)
    all_rows = _load_comp_games()
    rows = [r for r in all_rows if start <= _game_when(r) <= end]
    prev = [r for r in all_rows if pstart <= _game_when(r) < pend]
    days = _day_series(start, end)

    ranked = [r for r in rows if r.get("ranked")]
    forfeits = [r for r in rows if r.get("forfeit")]
    draws = [r for r in rows if r.get("is_draw")]
    turns = [_int(r.get("turn_count")) for r in rows if _int(r.get("turn_count")) > 0]

    wins: Dict[str, int] = {}
    plays: Dict[str, int] = {}
    for r in rows:
        for name in (str(r.get("p1_name") or ""), str(r.get("p2_name") or "")):
            if name:
                plays[name] = plays.get(name, 0) + 1
        w = str(r.get("winner") or "")
        if w:
            wins[w] = wins.get(w, 0) + 1

    table = []
    for name, n in plays.items():
        w = wins.get(name, 0)
        table.append({"name": name, "matches": n, "wins": w, "win_rate": _pct(w, n)})
    table.sort(key=lambda r: (-r["matches"], -r["wins"]))

    return {
        "cards": [
            _card("Ranked matches", len(rows), delta=_delta(len(rows), len(prev)),
                  spark=_bucket(days, [_game_when(r) for r in rows])),
            _card("Matches counted for rank", len(ranked)),
            _card("Matches given up", len(forfeits),
                  tone="warn" if len(forfeits) > max(3, len(rows) * 0.2) else "neutral",
                  hint="One player left before the end."),
            _card("Average turns", round(_mean(turns), 1) if turns else None),
        ],
        "volume": {"days": days, "matches": _bucket(days, [_game_when(r) for r in rows]),
                   "forfeits": _bucket(days, [_game_when(r) for r in forfeits])},
        "outcomes": [
            {"label": "Played out", "value": len(rows) - len(forfeits) - len(draws)},
            {"label": "Given up", "value": len(forfeits)},
            {"label": "Draws", "value": len(draws)},
        ],
        "table": {
            "columns": [
                {"key": "name", "label": "Player", "always": True},
                {"key": "matches", "label": "Matches", "always": True},
                {"key": "wins", "label": "Wins", "always": True},
                {"key": "win_rate", "label": "Win rate", "always": True},
            ],
            "rows": table[:50],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: CLANS
# ═══════════════════════════════════════════════════════════════════════════
def _section_clans(f: Dict[str, Any]) -> Dict[str, Any]:
    users = _filter_users(_load_users(), f)
    db = _get_firestore() if _get_firestore else None
    clans: List[Dict[str, Any]] = []
    if db is not None:
        try:
            for doc in db.collection("clans").limit(500).stream():
                d = doc.to_dict() or {}
                members = d.get("members")
                clans.append({
                    "id": doc.id,
                    "name": str(d.get("name") or "Clan"),
                    "members": len(members) if isinstance(members, (list, dict)) else _int(d.get("member_count")),
                    "points": _int(d.get("season_points") or d.get("points")),
                    "created": _ts(d.get("created_ts") or d.get("created_at")),
                })
        except Exception as exc:  # noqa: BLE001
            print(f"[analytics] clan read failed: {exc}")

    in_clan = len([u for u in users if u["clan_id"]])
    clans.sort(key=lambda c: -c["points"])
    sizes = [c["members"] for c in clans if c["members"]]
    return {
        "cards": [
            _card("Clans", len(clans) or None),
            _card("Players in a clan", in_clan or None,
                  hint=f"{_pct(in_clan, len(users))}% of accounts." if users and in_clan else ""),
            _card("Average clan size", round(_mean(sizes), 1) if sizes else None),
            _card("Points scored", sum(c["points"] for c in clans) or None,
                  hint="This season, across every clan."),
        ],
        "top": [{"label": c["name"], "value": c["points"]} for c in clans[:10]],
        "sizes": _histogram(sizes, 6, ""),
        "table": {
            "columns": [
                {"key": "name", "label": "Clan", "always": True},
                {"key": "members", "label": "Members", "always": True},
                {"key": "points", "label": "Points", "always": True},
                {"key": "created", "label": "Created"},
            ],
            "rows": [{"name": c["name"], "members": c["members"], "points": c["points"],
                      "created": c["created"]} for c in clans[:100]],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: ECONOMY
# ═══════════════════════════════════════════════════════════════════════════
def _section_economy(f: Dict[str, Any]) -> Dict[str, Any]:
    start, end, _ps, _pe = _window(f)
    users = _filter_users(_load_users(), f)
    db = _get_firestore() if _get_firestore else None
    days = _day_series(start, end)

    held = [u["coins"] for u in users]
    total_held = sum(held)
    with_coins = len([c for c in held if c > 0])

    payments: List[Dict[str, Any]] = []
    if db is not None:
        try:
            for doc in db.collection("payments").limit(2000).stream():
                d = doc.to_dict() or {}
                when = _ts(d.get("ts") or d.get("created") or d.get("created_at"))
                if not (start <= when <= end):
                    continue
                payments.append({
                    "when": when,
                    "cents": _int(d.get("amount_total") or d.get("amount") or d.get("cents")),
                    "kind": str(d.get("kind") or d.get("rewardKind") or ""),
                })
        except Exception as exc:  # noqa: BLE001
            print(f"[analytics] payments read failed: {exc}")

    supporters = 0
    if db is not None:
        try:
            supporters = sum(1 for _ in db.collection("supporters").select([]).stream())
        except Exception:  # noqa: BLE001
            supporters = 0

    revenue_cents = sum(p["cents"] for p in payments)
    tiers: Dict[str, int] = {}
    for u in users:
        if u["supporter_tier"]:
            tiers[u["supporter_tier"]] = tiers.get(u["supporter_tier"], 0) + 1

    return {
        "cards": [
            _card("Coins players hold", total_held or None,
                  hint=f"Across {with_coins} accounts with a balance." if with_coins else ""),
            _card("Average balance", round(total_held / with_coins) if with_coins else None),
            _card("Purchases", len(payments) or None, hint="Completed Stripe payments in range."),
            _card("Supporters", supporters or None, hint="Accounts on the supporter wall."),
        ],
        "revenue": {"days": days, "series": _bucket(days, [p["when"] for p in payments]),
                    "total": round(revenue_cents / 100.0, 2)},
        "tiers": [{"label": k, "value": v} for k, v in _top(tiers, 6)],
        "balances": _histogram([c for c in held if c > 0], 8, ""),
        "top_holders": [
            {"label": u["nickname"] or "Player", "value": u["coins"]}
            for u in sorted(users, key=lambda u: -u["coins"])[:10] if u["coins"] > 0
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: EVENTS
# ═══════════════════════════════════════════════════════════════════════════
def _section_events(f: Dict[str, Any]) -> Dict[str, Any]:
    start, end, _ps, _pe = _window(f)
    db = _get_firestore() if _get_firestore else None
    days = _day_series(start, end)
    users = _filter_users(_load_users(), f)

    trades: List[int] = []
    if db is not None:
        try:
            for doc in db.collection("trades").limit(3000).stream():
                d = doc.to_dict() or {}
                when = _ts(d.get("completed_ts") or d.get("ts") or d.get("created_ts"))
                if start <= when <= end and str(d.get("status") or "") in ("completed", "done", ""):
                    trades.append(when)
        except Exception as exc:  # noqa: BLE001
            print(f"[analytics] trades read failed: {exc}")

    games = _filter_games(_load_games(), f, start, end)
    team_games = [g for g in games if g.get("team_mode")]

    team_sizes: Dict[int, int] = {}
    for g in team_games:
        n = _int(g.get("team_count"))
        if n:
            team_sizes[n] = team_sizes.get(n, 0) + 1

    unlocks = sum(u["icons"] for u in users)
    return {
        "cards": [
            _card("Trades completed", len(trades) or None, spark=_bucket(days, trades)),
            _card("Team games", len(team_games) or None),
            _card("Critters unlocked", unlocks or None, hint="Across every account."),
            _card("Backgrounds unlocked", sum(u["backgrounds"] for u in users) or None),
        ],
        "trades": {"days": days, "series": _bucket(days, trades)},
        "team_sizes": [{"label": f"{k} teams", "value": v}
                       for k, v in sorted(team_sizes.items())],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: TECHNICAL HEALTH
# ═══════════════════════════════════════════════════════════════════════════
def _section_technical(f: Dict[str, Any]) -> Dict[str, Any]:
    start, end, pstart, pend = _window(f)
    live = _live_snapshot() if _live_snapshot else {}
    all_games = _load_games()
    games = _filter_games(all_games, f, start, end)
    prev = _filter_games(all_games, f, pstart, pend)
    users = _filter_users(_load_users(), f)
    days = _day_series(start, end)

    truncated = [g for g in games if not _game_completed(g)]
    load = live.get("load") if isinstance(live.get("load"), dict) else {}
    skipped = _int(load.get("deep_plan_skipped"))
    granted = _int(load.get("deep_plan_granted"))

    checks = [
        {"label": "Player database", "ok": _get_firestore is not None and _get_firestore() is not None,
         "detail": "Firebase service account connected."},
        {"label": "Game records", "ok": bool(all_games),
         "detail": f"{len(all_games)} games on disk."},
        {"label": "Bot thinking budget",
         "ok": not (granted and skipped > granted * 0.25),
         "detail": f"{skipped} deep plans skipped, {granted} granted."},
        {"label": "Rooms in memory", "ok": _int(load.get("rooms")) < 400,
         "detail": f"{_int(load.get('rooms'))} rooms held."},
    ]

    return {
        "cards": [
            _card("Server", "Healthy" if all(c["ok"] for c in checks) else "Needs attention",
                  tone="good" if all(c["ok"] for c in checks) else "bad"),
            _card("Rooms in memory", _int(load.get("rooms"))),
            _card("Games that ended badly", len(truncated),
                  delta=_delta(len(truncated), len([g for g in prev if not _game_completed(g)])),
                  tone="warn" if len(truncated) > max(5, len(games) * 0.3) else "neutral"),
            _card("Version", _app_version or None),
        ],
        "checks": checks,
        "load": {
            "rooms": _int(load.get("rooms")),
            "threads": _int(load.get("threads")),
            "deep_plan_slots": _int(load.get("deep_plan_slots")),
            "deep_plan_granted": granted,
            "deep_plan_skipped": skipped,
        },
        "truncated": {"days": days, "series": _bucket(days, [_game_when(g) for g in truncated])},
        "alerts": _alerts(f, users, games, prev, live),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: PLAYER SEARCH
# ═══════════════════════════════════════════════════════════════════════════
def _section_search(f: Dict[str, Any], query: str) -> Dict[str, Any]:
    q = str(query or "").strip().lower()
    if len(q) < 2:
        return {"query": query, "matches": [], "player": None}
    rows = _load_users()          # search always spans EVERY account, filters off
    matches = [u for u in rows
               if q in (u["nickname"] or "").lower()
               or q == (u["friend_code"] or "").lower()
               or q == u["uid"].lower()][:25]
    player = None
    if matches:
        u = matches[0]
        games = [g for g in _load_games()
                 if any(str(p.get("name") or "").lower() == (u["nickname"] or "").lower()
                        for p in _human_players(g))]
        games.sort(key=lambda g: -_game_when(g))
        player = {
            "name": u["nickname"] or "Player",
            "friend_code": u["friend_code"],
            "joined": u["created_at"],
            "last_seen": u["last_active"],
            "online": u["online"],
            "games": u["games"], "wins": u["wins"], "level": u["level"],
            "xp": u["total_xp"], "coins": u["coins"], "icons": u["icons"],
            "backgrounds": u["backgrounds"], "prestige": u["prestige_level"],
            "highest_score": u["highest_score"],
            "clan_id": u["clan_id"],
            "recent": [{
                "when": _game_when(g),
                "mode": "Competitive" if g.get("mode") == "competitive" else (
                    "Team" if g.get("team_mode") else "Casual"),
                "players": _int(g.get("player_count")),
                "won": str(g.get("winner") or "").lower() == (u["nickname"] or "").lower(),
                "score": next((_int(p.get("score")) for p in _human_players(g)
                               if str(p.get("name") or "").lower() == (u["nickname"] or "").lower()), 0),
                "finished": _game_completed(g),
            } for g in games[:15]],
        }
    return {
        "query": query,
        "matches": [{"name": u["nickname"] or "Player", "friend_code": u["friend_code"],
                     "games": u["games"], "level": u["level"], "last_seen": u["last_active"]}
                    for u in matches],
        "player": player,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  ROUTING
# ═══════════════════════════════════════════════════════════════════════════
_SECTIONS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "overview": _section_overview,
    "players": _section_players,
    "gameplay": _section_gameplay,
    "cards": _section_cards,
    "competitive": _section_competitive,
    "clans": _section_clans,
    "economy": _section_economy,
    "events": _section_events,
    "technical": _section_technical,
}


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    """POST /api/analytics/<section>. Returns True if it handled the request."""
    path = parsed.path
    if not path.startswith("/api/analytics/"):
        return False
    action = path[len("/api/analytics/"):].strip("/")

    if _admin_claims(body) is None:
        # Deliberately the same answer for "not signed in", "not an admin" and
        # "bad token": a probe must not be able to tell which.
        handler._send_json({"ok": False, "error": "unauthorized"}, status=403)
        return True

    f = _filters(body)
    f["min_sample"] = _int(body.get("min_sample"), _CARD_MIN_SAMPLE)

    try:
        if action == "search":
            payload = _section_search(f, body.get("query"))
        elif action == "export":
            payload = {"sections": {name: fn(f) for name, fn in _SECTIONS.items()}}
        elif action in _SECTIONS:
            payload = _SECTIONS[action](f)
        else:
            handler._send_json({"ok": False, "error": "unknown_section"}, status=404)
            return True
    except Exception as exc:  # noqa: BLE001 — a broken section must not 500 the tool
        print(f"[analytics] section {action} failed: {exc}")
        handler._send_json({"ok": False, "error": "section_failed",
                            "detail": str(exc)[:200]})
        return True

    handler._send_json({"ok": True, "section": action, "generated": _now(),
                        "range_days": f["days"], **payload})
    return True
