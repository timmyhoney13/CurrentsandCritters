"""Currents and Critters: Developer Analytics (server-authoritative, admin only).

Wired additively into multiplayer_server (same pattern as clan_server /
prestige_server):
    import analytics_server
    analytics_server.init(...)                      # in main()
    if analytics_server.handle_post(self, parsed, body):   # in do_POST

WHY THE WHOLE THING IS SERVER-SIDE
The browser cannot compute any of this. Firestore security rules block reading
other players' documents (by design: emails and profiles are private), so every
number here is derived here, with the service account, and shipped to the
dashboard already aggregated. No raw player document ever leaves this module.

WHO CAN CALL IT
POST only, and every call carries a Firebase ID token that is verified here and
then checked against the account's own `is_admin` flag (or ADMIN_EMAIL). A uid
in the body is never trusted. There is no GET form on purpose: analytics answers
must never be reachable by pasting a URL.

WHERE EVERY PLAYER AND GAME NUMBER COMES FROM: THE ACCOUNTS
The game keeps its record of play on each account (Firestore `users`), and that
is the only record that survives a deploy:
  • lifetime counters in `stats`: completed_games, games and wins per table
    size, hours_played, strategy_play_counts, competitive wins/losses/draws;
  • `stats.recent_games`: the account's last 50 finished games, each with when
    it ended, table size, mode, score, result, strategy and the boards. It is
    the same log the player's own History tab draws;
  • `stats.streak_days`: every date the account played a game, back 800 days.
This dashboard used to count games from the server's own history files
(games_history/*.json). On live that directory held ONE game while the accounts
held 435, so Gameplay, Cards, Competitive and Events had nothing to draw, while
the tests, whose fixtures were history files too, stayed green. The history
directory is now a Technical Health fact about the server and nothing more. Its
games are never added to the accounts' (a game counted in both would double).

TWO THINGS TO KNOW BEFORE READING A NUMBER
  • A "game played" is one account finishing one game, the unit the homepage
    counts: three friends finishing one game together is three games played.
  • A date range reads each account's log, which holds its last 50 games. An
    account that played more than that inside the range is under-counted, and
    the payload says how many were (`coverage`) rather than passing a short
    number off as a whole one. Lifetime totals come from the counters, which
    have no cap.

"NO DATA" IS NOT ZERO
Anything the sources cannot support is reported as None ("No data yet"), never
as 0: a zero is a measurement, and would read as "this dropped to nothing".

CACHING, AND WHY IT MATTERS HERE
One scan of the accounts (one read per account) feeds every section for
_USERS_TTL_SEC. The live panel's 20-second tick never triggers a scan, and
Refresh can force one at most every _USERS_FORCE_FLOOR_SEC. Firestore's free
read allowance ran out once (2026-09-04) and took XP, history and both passes
down with it; a dashboard must never be the thing that does that again.
"""
from __future__ import annotations

import calendar
import json
from datetime import datetime
import os
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

# ── Injected by init() (no circular import with multiplayer_server) ──────────
_get_firestore: Optional[Callable[[], Any]] = None
_verify_token: Optional[Callable[[str], Optional[dict]]] = None
_games_dir: str = ""
_live_snapshot: Optional[Callable[[], Dict[str, Any]]] = None
_app_version: str = ""
# Animal name (lower-case) → family ("Game Fish", "Bird"...), from the server's
# card database. The boards an account saves carry names only.
_card_species: Dict[str, str] = {}
# The clan season running right now ("2026-Q3"). clan_server owns that rule (a
# season runs a month past the quarter it is named for), so it is asked, not
# re-derived here.
_clan_season: Optional[Callable[[], str]] = None

ADMIN_EMAIL = "currentsandcritters@gmail.com"

DAY = 86400
MAX_RANGE_DAYS = 365
DEFAULT_RANGE_DAYS = 30
# `days` of 0 is Lifetime: every game ever played, no start date, and no
# previous period to compare against.
LIFETIME = 0
# Nothing in this game happened before 2020. A timestamp older than that is a
# broken write, and letting one through would start every lifetime chart in 1970.
_EPOCH_FLOOR = 1577836800


def init(*, get_firestore, verify_token, games_history_dir,
         live_snapshot=None, app_version="", card_species=None, clan_season=None) -> None:
    global _get_firestore, _verify_token, _games_dir
    global _live_snapshot, _app_version, _card_species, _clan_season
    _get_firestore = get_firestore
    _verify_token = verify_token
    _games_dir = str(games_history_dir or "")
    _live_snapshot = live_snapshot
    _app_version = str(app_version or "")
    _card_species = {str(k).strip().lower(): str(v)
                     for k, v in (card_species or {}).items() if k and v}
    _clan_season = clan_season


def reset_caches() -> None:
    """Forget every cached read. For tests, and nothing in the server calls it."""
    with _USERS_LOCK:
        _USERS_CACHE.update({"at": 0.0, "rows": None, "error": "", "ms": 0})
    with _GAMES_LOCK:
        _GAMES_CACHE.update({"sig": None, "rows": None})
    with _AUX_LOCK:
        _AUX_CACHE.clear()


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
        if isinstance(v, bool):
            return default
        f = float(v)
        return f if f == f else default          # NaN guard
    except (TypeError, ValueError):
        return default


def _pct(part: float, whole: float) -> Optional[float]:
    """Percentage, or None when the denominator is zero, a rate with nothing
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
    if isinstance(value, str) and value.strip() and _float(value, -1.0) < 0:
        # The History tab reads `t` with new Date(t), which takes a date string
        # as happily as a number, so an older entry may hold one.
        try:
            return int(datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp())
        except ValueError:
            return 0
    n = _float(value, 0.0)
    # The game client stamps its logs with Date.now(), which is milliseconds.
    if n > 4e10:
        n /= 1000.0
    return int(n)


def _day_key(unix: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(unix)))


def _day_floor(unix: int) -> int:
    return int(unix) - (int(unix) % DAY)


def _date_noon(value) -> Optional[int]:
    """'2026-09-13' → noon UTC on that date, or None for anything else.

    Noon, not midnight: the dates are the PLAYER's local dates, and noon sits
    within twelve hours of every timezone's version of that day. It also makes a
    date belong to exactly one of two back-to-back date ranges, where a midnight
    that lands on a range boundary would belong to both."""
    s = str(value or "")
    if len(s) < 10 or s[4] != "-" or s[7] != "-":
        return None
    try:
        y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
    except ValueError:
        return None
    if not (2000 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31):
        return None
    return calendar.timegm((y, m, d, 12, 0, 0))


def _top(counter: Dict[Any, Any], n: int) -> List[Tuple[Any, Any]]:
    items = list(counter.items())
    # Biggest first, then alphabetical, so a tie never reorders between refreshes.
    items.sort(key=lambda kv: (-_float(kv[1]), str(kv[0])))
    return items[:n]


def _add(counter: Dict[Any, int], key, n: int = 1) -> None:
    counter[key] = counter.get(key, 0) + n


def _mean(values: List[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _players_label(size: int) -> str:
    return f"{size} players"


def _hours_total(users: List[Dict[str, Any]]) -> int:
    """Whole hours across accounts, floored the way the homepage floors them
    (multiplayer_server: play_seconds // 3600), so the two never disagree by one."""
    return int(sum(u["hours"] for u in users))


# ═══════════════════════════════════════════════════════════════════════════
#  THE CHART AXIS
# ═══════════════════════════════════════════════════════════════════════════
# A 30-day range is drawn a day at a time. Lifetime can reach back years, and 800
# daily points in a 700px chart is noise, so longer spans step up to weeks and
# then months. Every series in one response shares one axis.
#
# A day is the VIEWER's day. Cut at UTC midnight, "today" began at 7pm for a
# developer in Texas, so every chart ended in a cliff that was only the evening.
# `tz` is the browser's getTimezoneOffset() in seconds (west of UTC positive),
# and bucket starts are seconds on that local clock, which is all a key needs.
def _bucket_start(unix: int, gran: str, tz: int = 0) -> int:
    day = _day_floor(int(unix) - int(tz))
    if gran == "week":
        return day - time.gmtime(day).tm_wday * DAY          # back to Monday
    if gran == "month":
        t = time.gmtime(day)
        return calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0))
    return day


def _next_bucket(start: int, gran: str) -> int:
    if gran == "week":
        return start + 7 * DAY
    if gran == "month":
        t = time.gmtime(start)
        y, m = (t.tm_year + 1, 1) if t.tm_mon == 12 else (t.tm_year, t.tm_mon + 1)
        return calendar.timegm((y, m, 1, 0, 0, 0))
    return start + DAY


def _axis(start: int, end: int, tz: int = 0) -> Dict[str, Any]:
    """{"days": bucket keys oldest first, "gran": "day" | "week" | "month",
    "tz", "from": the real time the first bucket starts}. A key is the local date
    its bucket starts on, whatever the granularity. The last bucket is the one
    running now, so it is always still filling up."""
    start = min(int(start), int(end))       # a clock-skewed "first seen" can't be tomorrow
    span = max(1, (int(end) - start) // DAY + 1)
    gran = "day" if span <= 120 else "week" if span <= 800 else "month"
    keys: List[str] = []
    first = cur = _bucket_start(start, gran, tz)
    last = _bucket_start(end, gran, tz)
    while cur <= last and len(keys) < 500:
        keys.append(_day_key(cur))
        cur = _next_bucket(cur, gran)
    return {"days": keys, "gran": gran, "tz": int(tz), "from": first + int(tz)}


def _series(axis: Dict[str, Any], stamps: Iterable[int]) -> List[int]:
    """How many stamps fall in each bucket of the axis."""
    idx = {k: i for i, k in enumerate(axis["days"])}
    out = [0] * len(axis["days"])
    for s in stamps:
        i = idx.get(_day_key(_bucket_start(s, axis["gran"], axis.get("tz", 0))))
        if i is not None:
            out[i] += 1
    return out


def _distinct_series(axis: Dict[str, Any], pairs: Iterable[Tuple[int, str]]) -> List[int]:
    """How many different accounts appear in each bucket. A player who played on
    five days of one week is one player that week, not five."""
    idx = {k: i for i, k in enumerate(axis["days"])}
    seen: List[Set[str]] = [set() for _ in axis["days"]]
    for s, who in pairs:
        i = idx.get(_day_key(_bucket_start(s, axis["gran"], axis.get("tz", 0))))
        if i is not None:
            seen[i].add(who)
    return [len(x) for x in seen]


# ═══════════════════════════════════════════════════════════════════════════
#  ACCESS CONTROL
# ═══════════════════════════════════════════════════════════════════════════
def _admin_claims(body: Dict[str, Any]) -> Optional[dict]:
    """Verified claims for an ADMIN account, or None.

    Two independent gates, both required: the ID token must verify (so the
    caller really is that account), and the account must be flagged admin in
    Firestore (or be the known admin email). A client-supplied uid or email is
    never enough, those are trivially forged from devtools.
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
#  THE ACCOUNTS (Firestore `users`): every player and game number
# ═══════════════════════════════════════════════════════════════════════════
_USERS_TTL_SEC = 300.0
# Refresh forces a rescan, but never more often than this, however fast it is
# clicked: each scan is one read per account.
_USERS_FORCE_FLOOR_SEC = 20.0
_MAX_ACCOUNTS = 5000
_USERS_CACHE: Dict[str, Any] = {"at": 0.0, "rows": None, "error": "", "ms": 0}
_USERS_LOCK = threading.Lock()
_USERS_SCAN_LOCK = threading.Lock()

# Only these fields are read. Emails come across solely to recognise the
# developer's own test accounts (the "Include test accounts" filter) and are
# never put in a response payload.
_USER_TOP_FIELDS = [
    "nickname", "friend_code", "email", "is_admin", "guest", "online",
    "last_active", "created_at", "unlocked_icons", "unlocked_backgrounds",
    "prestige", "clan_id", "supporter_tier",
]
# Named one by one rather than taking all of `stats`, which also carries replay
# snapshots of every personal best (best_game, best_game_by_size), achievements
# and season archives, none of which any chart reads.
_USER_STAT_FIELDS = [
    "completed_games", "normal_games_by_size", "comp_games_by_size",
    "physical_games", "normal_wins", "competitive_wins", "competitive_losses",
    "competitive_draws", "lifetime_comp_wins", "lifetime_comp_losses",
    "lifetime_comp_draws", "comp_cp", "rank_competitive", "total_xp", "level",
    "player_level", "critter_coins", "total_score", "highest_score",
    "hours_played", "playtime_by_mode", "strategy_play_counts",
    "most_played_strategy", "favorite_strategy", "streak_days",
    "streak_longest", "tournaments_played", "tournament_wins", "recent_games",
]
_USER_FIELDS = _USER_TOP_FIELDS + [f"stats.{k}" for k in _USER_STAT_FIELDS]


def _stats_map(data: Dict[str, Any]) -> Dict[str, Any]:
    """One account document's `stats`, however the SDK chose to shape it.

    A projected read is documented to keep the nesting ({"stats": {...}}), but
    some builds hand dotted field paths back flat ({"stats.completed_games": 155}).
    Reading only one shape against a server returning the other finds nothing
    and reports it confidently, which is the silent wrong number this whole
    module exists to avoid. multiplayer_server._stats_map accepts both for the
    same reason."""
    if not isinstance(data, dict):
        return {}
    nested = data.get("stats")
    if isinstance(nested, dict) and nested:
        return nested
    flat: Dict[str, Any] = {}
    for key, val in data.items():
        if isinstance(key, str) and key.startswith("stats."):
            flat[key[len("stats."):]] = val
    return flat


def _size_map(value) -> Dict[int, int]:
    """{"4": 12, "2": 3} → {4: 12, 2: 3}: games (or wins) per table size."""
    out: Dict[int, int] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            size, n = _int(k), _int(v)
            if 2 <= size <= 10 and n > 0:
                _add(out, size, n)
    return out


def _count_map(value) -> Dict[str, int]:
    out: Dict[str, int] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            name, n = str(k or "").strip(), _int(v)
            if name and n > 0:
                _add(out, name, n)
    return out


# What a logged game's `mode` means. The client writes "normal" for a casual
# game; the recovery export once wrote "standard".
_LOG_MODES = {
    "normal": "casual", "standard": "casual", "casual": "casual", "": "casual",
    "team": "team", "ranked": "ranked", "competitive": "competitive",
    "physical": "physical",
}
# What each mode is called on screen. Both Competitive modes say "Competitive"
# first, because that is the word the game itself puts in front of a player.
_MODE_LABEL = {
    "casual": "Casual",
    "team": "Team",
    "ranked": "Competitive (free-for-all)",
    "competitive": "Competitive (pairs)",
    "physical": "Table game (Snap Score)",
}
_FILTER_MODES = ("all", "casual", "competitive", "team", "physical")


def _log_entries(raw, nickname: str) -> List[Dict[str, Any]]:
    """An account's `stats.recent_games`, flattened to what the charts read,
    oldest first.

    The entry shape is the client's (saveGameStats in preview-app.js): r = won,
    s = score, t = Date.now() in ms, pc = table size, all = every seat's name and
    score, opp = everyone else's name, bds = every seat's board, win = winner,
    strat = the strategy detected on this player's board. snap_score writes the
    same shape for a scored table game. Older recovery entries used
    ts/score/won/players instead, and still read."""
    out: List[Dict[str, Any]] = []
    for g in raw if isinstance(raw, list) else []:
        if not isinstance(g, dict):
            continue
        when = _ts(g.get("t") if g.get("t") is not None else g.get("ts"))
        if when < _EPOCH_FLOOR:
            continue
        scores = [(str(p.get("n") or ""), _int(p.get("s")))
                  for p in (g.get("all") or []) if isinstance(p, dict)]
        # A list of names, or (in older entries, which the History tab still
        # draws) one "Kelp, Reef" string. Iterating that string would make every
        # letter an opponent.
        raw_opp = g.get("opp") or []
        if isinstance(raw_opp, str):
            raw_opp = raw_opp.split(",")
        opp = {str(o or "").strip().lower() for o in raw_opp if str(o or "").strip()}
        # This player's own name at that table: the one seat that is nobody's
        # opponent. The nickname can have changed since, so it is the fallback.
        me = next((n for n, _s in scores if n.strip().lower() not in opp), "") or nickname
        board = None
        for seat in (g.get("bds") or []):
            if isinstance(seat, dict) and str(seat.get("n") or "").strip().lower() == me.strip().lower():
                board = [{"o": str(oc.get("o") or ""),
                          "a": [str(a) for a in (oc.get("a") or []) if a]}
                         for oc in (seat.get("b") or []) if isinstance(oc, dict)]
                break
        pc = _int(g.get("pc") if g.get("pc") is not None else g.get("players"))
        if pc <= 0:
            pc = len(scores)
        out.append({
            "t": when,
            "mode": _LOG_MODES.get(str(g.get("mode") or "").strip().lower(), "casual"),
            "pc": pc,
            "score": _int(g.get("s") if g.get("s") is not None else g.get("score")),
            # `r` is 1/0 from the client, `won` a boolean in recovery entries.
            "won": g.get("r") == 1 or g.get("won") is True,
            "winner": str(g.get("win") or ""),
            "strat": str(g.get("strat") or "").strip(),
            "scores": scores,
            "opp": sorted(o for o in opp if o),
            "me": me,
            "board": board,
            "humans": 1,
        })
    out.sort(key=lambda e: e["t"])
    return out


def _user_row(uid: str, d: Dict[str, Any]) -> Dict[str, Any]:
    """One account, flattened to exactly what the dashboard measures."""
    st = _stats_map(d)
    pr = d.get("prestige") if isinstance(d.get("prestige"), dict) else {}
    casual_by_size = _size_map(st.get("normal_games_by_size"))
    comp_by_size = _size_map(st.get("comp_games_by_size"))
    completed = _int(st.get("completed_games"))
    nickname = str(d.get("nickname") or "")
    raw_days = st.get("streak_days") if isinstance(st.get("streak_days"), list) else []
    days = sorted({n for n in (_date_noon(s) for s in raw_days) if n})
    hours_by_mode = st.get("playtime_by_mode") if isinstance(st.get("playtime_by_mode"), dict) else {}
    return {
        "uid": uid,
        "nickname": nickname,
        "friend_code": str(d.get("friend_code") or ""),
        "email_lower": str(d.get("email") or "").strip().lower(),
        "is_admin": d.get("is_admin") is True,
        "is_guest": d.get("guest") is True,
        "online": d.get("online") is True,
        "last_active": _ts(d.get("last_active")),
        "created_at": _ts(d.get("created_at")),
        # The rule the homepage and Player Home both use, copied rather than
        # reinvented (multiplayer_server._player_total_games): a total that
        # disagrees with the pages it sums is worse than no total.
        "games": max(completed, sum(casual_by_size.values()) + sum(comp_by_size.values())),
        "completed": completed,
        "casual_by_size": casual_by_size,
        "comp_by_size": comp_by_size,
        # These two counters are NOT addable. `normal_wins` is a lifetime total
        # that already includes free-for-all Competitive wins, and
        # `competitive_wins` is reset to 0 at every season rollover. Summing them
        # double-counted every free-for-all win and made a player's total drop
        # when a season ended, so they are reported as the two things they are.
        "wins": _int(st.get("normal_wins")),
        "comp_wins": _int(st.get("competitive_wins")),
        "life_comp": (_int(st.get("lifetime_comp_wins")), _int(st.get("lifetime_comp_losses")),
                      _int(st.get("lifetime_comp_draws"))),
        "comp_points": _int(st.get("comp_cp")),
        "comp_rank": str(st.get("rank_competitive") or ""),
        "total_xp": _int(st.get("total_xp")),
        "level": _int(st.get("level") or st.get("player_level"), 1),
        "coins": _int(st.get("critter_coins")),
        "total_score": _int(st.get("total_score")),
        "highest_score": _int(st.get("highest_score")),
        # App-open hours, the number each player is shown on their own page.
        "hours": max(0.0, _float(st.get("hours_played"))),
        "match_hours": sum(max(0.0, _float(v)) for v in hours_by_mode.values()),
        "strategy_counts": _count_map(st.get("strategy_play_counts")),
        "favorite": str(st.get("most_played_strategy") or st.get("favorite_strategy") or "").strip(),
        "play_days": days,
        "streak_longest": _int(st.get("streak_longest")),
        "tournaments": _int(st.get("tournaments_played")),
        "icons": len(d.get("unlocked_icons")) if isinstance(d.get("unlocked_icons"), list) else 0,
        "backgrounds": len(d.get("unlocked_backgrounds")) if isinstance(d.get("unlocked_backgrounds"), list) else 0,
        "prestige_level": _int(pr.get("level")),
        "clan_id": str(d.get("clan_id") or ""),
        "supporter_tier": str(d.get("supporter_tier") or st.get("supporter_tier") or ""),
        "log": _log_entries(st.get("recent_games"), nickname),
    }


# The players at one table each log that game on their own account, stamped by
# their own clock when their own save ran, so the copies land seconds apart.
_SAME_GAME_SEC = 3 * 3600


def _link_tables(rows: List[Dict[str, Any]]) -> None:
    """Mark every logged game with how many ACCOUNTS were at that table.

    Two copies are the same game when they agree on mode, table size, winner
    and every seat's final score, and were saved within _SAME_GAME_SEC of each
    other by different accounts. Guests save nothing, so a table with one
    account and a guest reads as one: the count is signed-in players.

    Run over every account, not the filtered ones: a game against the developer
    was still a game with another person in it."""
    groups: Dict[tuple, List[Tuple[str, Dict[str, Any]]]] = {}
    for u in rows:
        for e in u["log"]:
            if not e["scores"]:
                continue
            key = (e["mode"], e["pc"], e["winner"].strip().lower(),
                   tuple(sorted((n.strip().lower(), s) for n, s in e["scores"])))
            groups.setdefault(key, []).append((u["uid"], e))

    def close(cluster: List[Tuple[str, Dict[str, Any]]]) -> None:
        for _uid, entry in cluster:
            entry["humans"] = len(cluster)

    for items in groups.values():
        items.sort(key=lambda it: it[1]["t"])
        cluster: List[Tuple[str, Dict[str, Any]]] = []
        for uid, e in items:
            if cluster and (e["t"] - cluster[0][1]["t"] > _SAME_GAME_SEC
                            or any(c_uid == uid for c_uid, _ in cluster)):
                close(cluster)
                cluster = []
            cluster.append((uid, e))
        close(cluster)


def _load_users(force: bool = False, allow_stale: bool = False) -> List[Dict[str, Any]]:
    """Every account, flattened. Cached; see CACHING in the module docstring.

    A failed scan keeps serving the last good one (and says so through
    _scan_status) rather than blanking every tab at once."""
    def cached() -> Optional[List[Dict[str, Any]]]:
        with _USERS_LOCK:
            rows, age = _USERS_CACHE["rows"], time.time() - _USERS_CACHE["at"]
            if rows is not None and (allow_stale
                                     or age < (_USERS_FORCE_FLOOR_SEC if force else _USERS_TTL_SEC)):
                return rows
        return None

    rows = cached()
    if rows is not None:
        return rows
    # One scan at a time. Switching the range fires a section request while
    # the previous one may still be scanning; without this each started its
    # own full read of every account, doubling the wait and the Firestore bill.
    with _USERS_SCAN_LOCK:
        rows = cached()
        if rows is not None:
            return rows
        return _scan_users()


def _scan_users() -> List[Dict[str, Any]]:
    db = _get_firestore() if _get_firestore else None
    if db is None:
        with _USERS_LOCK:
            _USERS_CACHE["error"] = "Firebase isn't configured on this server."
            return _USERS_CACHE["rows"] or []
    started = time.time()
    fresh: List[Dict[str, Any]] = []
    try:
        users = db.collection("users")
        # A projection costs the same reads but carries far less. A rejected
        # projection falls back to whole documents rather than to nothing.
        try:
            stream = users.select(_USER_FIELDS).stream()
        except Exception:  # noqa: BLE001
            stream = users.stream()
        for doc in stream:
            fresh.append(_user_row(doc.id, doc.to_dict() or {}))
            if len(fresh) >= _MAX_ACCOUNTS:
                print(f"[analytics] account scan stopped at {_MAX_ACCOUNTS} accounts")
                break
    except Exception as exc:  # noqa: BLE001
        print(f"[analytics] user scan failed: {exc}")
        with _USERS_LOCK:
            _USERS_CACHE["error"] = str(exc)[:240] or exc.__class__.__name__
            return _USERS_CACHE["rows"] or []
    _link_tables(fresh)
    with _USERS_LOCK:
        _USERS_CACHE.update({"rows": fresh, "at": time.time(), "error": "",
                             "ms": int((time.time() - started) * 1000)})
    return fresh


def _scan_status() -> Dict[str, Any]:
    """Whether the player numbers on screen are real, and how old they are."""
    with _USERS_LOCK:
        rows, at = _USERS_CACHE["rows"], _USERS_CACHE["at"]
        error, ms = _USERS_CACHE["error"], _USERS_CACHE["ms"]
    return {
        "ok": not error,
        "error": error,
        "accounts": len(rows) if rows is not None else None,
        "age_sec": int(max(0.0, time.time() - at)) if at else None,
        "read_ms": ms,
    }


def _filter_users(rows: List[Dict[str, Any]], f: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Apply the account-level advanced filters."""
    out = rows
    if not f.get("include_test"):
        out = [r for r in out if not r["is_admin"] and r["email_lower"] != ADMIN_EMAIL]
    if not f.get("include_guests"):
        out = [r for r in out if not r["is_guest"]]
    return out


def _users(f: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _filter_users(_load_users(force=bool(f.get("refresh"))), f)


# ═══════════════════════════════════════════════════════════════════════════
#  GAMES, FROM THE ACCOUNTS' LOGS
# ═══════════════════════════════════════════════════════════════════════════
def _game_filtered(f: Dict[str, Any]) -> bool:
    """True when a filter narrows WHICH games count. Only a log entry knows its
    mode, table size and who else was there, so a filtered question can't be
    answered from the lifetime counters."""
    return f.get("mode", "all") != "all" or bool(f.get("player_count")) or bool(f.get("only_multiplayer"))


def _entry_matches(e: Dict[str, Any], f: Dict[str, Any]) -> bool:
    size = _int(f.get("player_count"))
    if size and e["pc"] != size:
        return False
    want = f.get("mode", "all")
    if want == "competitive":
        # Competitive means the mode as a player knows it: BOTH competitive
        # tables, the paired game and the free-for-all.
        if e["mode"] not in ("competitive", "ranked"):
            return False
    elif want != "all" and e["mode"] != want:
        return False
    if f.get("only_multiplayer") and e["humans"] < 2:
        return False
    return True


def _entries(users: List[Dict[str, Any]], f: Dict[str, Any], lo: int, hi: int
             ) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """(account, logged game) for every game the filters keep, lo <= t <= hi."""
    return [(u, e) for u in users for e in u["log"]
            if lo <= e["t"] <= hi and _entry_matches(e, f)]


def _range(f: Dict[str, Any]) -> Dict[str, Any]:
    """The date range asked for, and the one just before it.

    Both ends are inclusive and the two ranges never share a second: `end` is a
    day past now (nothing real is stamped in the future, and a player's date for
    today is stamped at noon, which can be later than now), and the previous
    range stops one second before this one starts."""
    now, tz = _now(), int(f.get("tz") or 0)
    if f["days"] == LIFETIME:
        return {"now": now, "tz": tz, "lifetime": True, "start": 0, "end": now + DAY, "prev": None}
    start = now - f["days"] * DAY
    return {"now": now, "tz": tz, "lifetime": False, "start": start, "end": now + DAY,
            "prev": (start - f["days"] * DAY, start - 1)}


def _first_seen(users: List[Dict[str, Any]], fallback: int) -> int:
    """Where a lifetime chart starts: the first day anything was recorded."""
    firsts = []
    for u in users:
        for v in (u["created_at"],
                  u["play_days"][0] if u["play_days"] else 0,
                  u["log"][0]["t"] if u["log"] else 0):
            if v >= _EPOCH_FLOOR:
                firsts.append(v)
    return min(firsts) if firsts else fallback


def _chart_axis(users: List[Dict[str, Any]], r: Dict[str, Any]) -> Dict[str, Any]:
    start = _first_seen(users, r["now"] - DEFAULT_RANGE_DAYS * DAY) if r["lifetime"] else r["start"]
    return _axis(start, r["now"], r["tz"])


def _played_pairs(users: List[Dict[str, Any]], f: Dict[str, Any], lo: int, hi: int
                  ) -> List[Tuple[int, str]]:
    """(when, uid) for every time an account played inside the range.

    The date list is the complete record, where the log stops at 50 games, but
    it can't say what a game was: a mode or table-size filter is answered from
    the log alone."""
    pairs = [(e["t"], u["uid"]) for u, e in _entries(users, f, lo, hi)]
    if not _game_filtered(f):
        for u in users:
            pairs.extend((d, u["uid"]) for d in u["play_days"] if lo <= d <= hi)
    return pairs


def _played_uids(users: List[Dict[str, Any]], f: Dict[str, Any], lo: int, hi: int,
                 lifetime: bool = False) -> Set[str]:
    got = {uid for _t, uid in _played_pairs(users, f, lo, hi)}
    if lifetime and not _game_filtered(f):
        # Games from before the log and the date list existed are only in the
        # counters, and they are still games this account played.
        got.update(u["uid"] for u in users if u["games"] > 0)
    return got


# The client's resilient writer keeps 40 minimal entries when an account's
# document gets near Firestore's 1MB cap, so a log this long may be a cut one.
_LOG_FULL = 40


def _coverage(users: List[Dict[str, Any]], lo: int, hi: int, lifetime: bool
              ) -> Optional[Dict[str, Any]]:
    """How many accounts played more games in the range than their log holds.
    None when the log covers every account completely."""
    short = 0
    for u in users:
        log = u["log"]
        if not u["games"] or len(log) >= u["games"]:
            continue                       # the log holds every game ever played
        if lifetime:
            short += 1
            continue
        if log and log[0]["t"] <= lo:
            continue                       # the log reaches back past the start
        if u["play_days"]:
            first_logged = log[0]["t"] if log else hi + 1
            if any(lo <= d < first_logged for d in u["play_days"]):
                short += 1                 # played in range before the log begins
        elif len(log) >= _LOG_FULL:
            short += 1                     # no dates to check, and the log is full
    if not short:
        return None
    who = "1 player" if short == 1 else f"{short} players"
    if lifetime:
        note = (f"{who} {'has' if short == 1 else 'have'} played more games than the 50 "
                "each account keeps in detail, so charts leave out their oldest games. "
                "Totals still count every game.")
    else:
        note = (f"{who} played more games in this range than their saved history "
                "keeps (the last 50), so their oldest games here aren't counted.")
    return {"short_accounts": short, "note": note}


def _strategy_counts(users, entries, f, lifetime) -> Tuple[Dict[str, int], str]:
    """(counts, source). Lifetime: the strategy each player CONFIRMED at the end
    of every game (stats.strategy_play_counts). A date range can only use the
    strategy the game DETECTED on each logged board."""
    if lifetime and not _game_filtered(f):
        counts: Dict[str, int] = {}
        for u in users:
            for name, n in u["strategy_counts"].items():
                _add(counts, name, n)
        if counts:
            return counts, "confirmed"
    counts = {}
    for _u, e in entries:
        if e["strat"]:
            _add(counts, e["strat"])
    return counts, "detected"


def _size_counts(users, entries, f, lifetime) -> Dict[int, int]:
    """Games played at each table size."""
    counts: Dict[int, int] = {}
    if lifetime and not _game_filtered(f):
        for u in users:
            for size, n in list(u["casual_by_size"].items()) + list(u["comp_by_size"].items()):
                _add(counts, size, n)
        if counts:
            return counts
    for _u, e in entries:
        if e["pc"]:
            _add(counts, e["pc"])
    return counts


def _favorite(u: Dict[str, Any]) -> str:
    if u["favorite"]:
        return u["favorite"]
    if u["strategy_counts"]:
        return _top(u["strategy_counts"], 1)[0][0]
    counts: Dict[str, int] = {}
    for e in u["log"]:
        if e["strat"]:
            _add(counts, e["strat"])
    return _top(counts, 1)[0][0] if counts else ""


def _favorite_size(u: Dict[str, Any]) -> int:
    counts: Dict[int, int] = {}
    for size, n in list(u["casual_by_size"].items()) + list(u["comp_by_size"].items()):
        _add(counts, size, n)
    if not counts:
        for e in u["log"]:
            if e["pc"]:
                _add(counts, e["pc"])
    return _top(counts, 1)[0][0] if counts else 0


def _comp_games(u: Dict[str, Any]) -> int:
    """Lifetime competitive games, both tables. The W/L/D counters are the
    record; a log with more competitive games in it than they hold (an account
    from before they existed) is the better floor."""
    logged = sum(1 for e in u["log"] if e["mode"] in ("competitive", "ranked"))
    return max(sum(u["life_comp"]), logged)


# ═══════════════════════════════════════════════════════════════════════════
#  THE SERVER'S OWN GAME RECORDS (Technical Health only)
# ═══════════════════════════════════════════════════════════════════════════
# Keyed on the directory's own (file count, newest mtime): a game that finishes
# changes both, so the very next call re-reads. Nothing else invalidates it.
_GAMES_CACHE: Dict[str, Any] = {"sig": None, "rows": None}
_GAMES_LOCK = threading.Lock()
# A hard ceiling so a disk with years of history can never blow the process up.
_MAX_GAME_FILES = 20000


def _history_dir_state(path: str) -> Dict[str, Any]:
    """Why the history directory has no games in it, which is NOT one question
    but two, and they have opposite answers.

    A directory that is missing, unreadable or was never configured is a real
    server fault: games are finishing and their records are going nowhere. A
    directory that is there, readable and simply empty is a server that has not
    recorded a game yet, a fresh deploy, or a reset disk. Reporting the second
    one as a failing check is how the dashboard ends up permanently red for a
    server with nothing wrong with it, and a check that is always red is a check
    nobody reads.

    `writable` separates the third case: readable and empty, but nothing can be
    written into it either, which is the fault dressed up as the innocent one.
    """
    if not str(path or "").strip():
        return {"path": "", "exists": False, "writable": False, "count": 0,
                "problem": "No history directory is configured on this server."}
    if not os.path.isdir(path):
        return {"path": path, "exists": False, "writable": False, "count": 0,
                "problem": f"The history directory does not exist: {path}"}
    count = _dir_signature(path)[0]
    writable = os.access(path, os.W_OK)
    return {
        "path": path, "exists": True, "writable": writable, "count": count,
        "problem": "" if writable else f"The history directory is not writable: {path}",
    }


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
    # Newest first, so the cap drops the OLDEST history.
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


def _game_when(rec: Dict[str, Any]) -> int:
    return _int(rec.get("recorded_unix"))


def _game_completed(rec: Dict[str, Any]) -> bool:
    """A game that reached its real ending. `mode` is written as "truncated"
    when the END GAME card never resolved (everyone left, the room errored)."""
    return str(rec.get("mode") or "") != "truncated"


def _game_mode(rec: Dict[str, Any]) -> str:
    """One of "competitive", "ranked", "team", "casual" for a server record.

    Team games are saved as "standard" (+team_mode) and an abandoned game of ANY
    kind as "truncated", so `mode` alone does not name the table."""
    mode = str(rec.get("mode") or "")
    # A truncated record kept its real mode in these two booleans.
    if mode == "competitive" or rec.get("competitive") is True:
        return "competitive"
    if mode == "ranked" or rec.get("ranked") is True:
        return "ranked"
    if rec.get("team_mode"):
        return "team"
    return "casual"


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
            mode = _game_mode(rec)
            if want_mode == "competitive":
                if mode not in ("competitive", "ranked"):
                    continue
            elif mode != want_mode:
                continue
        if f.get("only_multiplayer") and _int(rec.get("human_count")) < 2:
            continue
        out.append(rec)
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  OTHER COLLECTIONS (clans, payments, supporters, trades)
# ═══════════════════════════════════════════════════════════════════════════
_AUX_TTL_SEC = 300.0
# A failed read is retried sooner than a good one is refreshed.
_AUX_RETRY_SEC = 30.0
_AUX_CACHE: Dict[str, Dict[str, Any]] = {}
_AUX_LOCK = threading.Lock()


def _cached_read(name: str, reader: Callable[[Any], Any], force: bool = False
                 ) -> Tuple[Any, str]:
    """(value, error). value is None when the collection couldn't be read, which
    every caller turns into "No data yet" rather than a zero."""
    now = time.time()
    with _AUX_LOCK:
        hit = _AUX_CACHE.get(name)
        if hit:
            age = now - hit["at"]
            ttl = _AUX_RETRY_SEC if hit["error"] else _AUX_TTL_SEC
            if age < (_USERS_FORCE_FLOOR_SEC if force else ttl):
                return hit["value"], hit["error"]
    db = _get_firestore() if _get_firestore else None
    if db is None:
        return None, "Firebase isn't configured on this server."
    try:
        value, error = reader(db), ""
    except Exception as exc:  # noqa: BLE001
        print(f"[analytics] {name} read failed: {exc}")
        value, error = None, str(exc)[:240] or exc.__class__.__name__
    with _AUX_LOCK:
        _AUX_CACHE[name] = {"at": time.time(), "value": value, "error": error}
    return value, error


def _read_clans(db) -> List[Dict[str, Any]]:
    out = []
    for doc in db.collection("clans").limit(500).stream():
        d = doc.to_dict() or {}
        # A clan's points live per season: clans/{id}.seasons["2026-Q3"].points
        # (clan_server._season_slot). There is no top-level points field, which
        # is why this page used to say no clan had ever scored.
        seasons = {}
        for sid, slot in (d.get("seasons") or {}).items():
            if isinstance(slot, dict):
                seasons[str(sid)] = {"points": _int(slot.get("points")),
                                     "games": _int(slot.get("games"))}
        members = d.get("members")
        lifetime = d.get("lifetime") if isinstance(d.get("lifetime"), dict) else {}
        out.append({
            "name": str(d.get("name") or "Clan"),
            "members": len(members) if isinstance(members, (list, dict)) else _int(d.get("member_count")),
            "seasons": seasons,
            "lifetime_points": _int(lifetime.get("points")),
            "created": _ts(d.get("created_ts") or d.get("created_at")),
        })
    return out


def _read_payments(db) -> List[Dict[str, Any]]:
    # Payments are a SUBcollection: supporters/{uid}/payments and
    # guestSupporters/{id}/payments. A top-level `payments` collection does not
    # exist, and reading one is why Purchases never showed anything.
    out = []
    for doc in db.collection_group("payments").limit(5000).stream():
        d = doc.to_dict() or {}
        out.append({
            "when": _ts(d.get("createdAt") or d.get("created_at")),
            "cents": _int(d.get("amountCents") or d.get("amount_total")),
            "product": str(d.get("productName") or ""),
        })
    return out


def _read_supporters(db) -> int:
    return sum(1 for _ in db.collection("supporters").select([]).stream())


def _read_trades(db) -> List[int]:
    """When each completed trade finished. A trade's id is the PAIR of players
    (multiplayer_server._trade_id_for), so the collection holds one trade per
    pair and a pair trading again replaces its last one."""
    trades = db.collection("trades")
    try:
        stream = trades.select(["status", "completed_ts"]).limit(5000).stream()
    except Exception:  # noqa: BLE001
        stream = trades.limit(5000).stream()
    return [_ts(d.get("completed_ts")) for d in (doc.to_dict() or {} for doc in stream)
            if str(d.get("status") or "") == "completed"]


# ═══════════════════════════════════════════════════════════════════════════
#  FILTERS, CARDS, SHARED BLOCKS
# ═══════════════════════════════════════════════════════════════════════════
def _filters(body: Dict[str, Any]) -> Dict[str, Any]:
    raw = body.get("days")
    if isinstance(raw, str) and raw.strip().lower() in ("lifetime", "all"):
        days = LIFETIME
    else:
        days = _int(raw, DEFAULT_RANGE_DAYS)
        days = DEFAULT_RANGE_DAYS if days < 0 else (LIFETIME if days == 0
                                                    else min(MAX_RANGE_DAYS, days))
    mode = str(body.get("mode") or "all")
    size = _int(body.get("player_count"), 0)
    return {
        "days": days,
        # A lifetime has nothing before it to compare against.
        "compare": bool(body.get("compare")) and days != LIFETIME,
        "only_multiplayer": bool(body.get("only_multiplayer")),
        "include_test": bool(body.get("include_test")),
        "include_guests": bool(body.get("include_guests")),
        "mode": mode if mode in _FILTER_MODES else "all",
        "player_count": size if 2 <= size <= 10 else 0,
        "refresh": bool(body.get("refresh")),
        # getTimezoneOffset() minutes → seconds, west of UTC positive.
        "tz": max(-14 * 60, min(14 * 60, _int(body.get("tz"), 0))) * 60,
    }


def _delta(now_val: Optional[float], prev_val: Optional[float]) -> Optional[float]:
    """Percent change vs the previous period. None when there is no baseline:
    "+100%" off a zero baseline is noise, not information."""
    if now_val is None or prev_val is None or not prev_val:
        return None
    return round(100.0 * (now_val - prev_val) / prev_val, 1)


def _card(label: str, value, *, unit="", delta=None, hint="", spark=None,
          tone="neutral") -> Dict[str, Any]:
    """One summary card. `value=None` renders as "No data yet", never as 0."""
    return {"label": label, "value": value, "unit": unit, "delta": delta,
            "hint": hint, "spark": spark or [], "tone": tone}


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


def _growth(users: List[Dict[str, Any]], f: Dict[str, Any], r: Dict[str, Any],
            axis: Dict[str, Any]) -> Dict[str, Any]:
    """The player-growth chart: who played, who joined, how many in total."""
    played = _distinct_series(axis, _played_pairs(users, f, r["start"], r["end"]))
    joined = [u["created_at"] for u in users if u["created_at"] >= _EPOCH_FLOOR]
    new = _series(axis, joined)
    # Accounts with no join date existed before anything can be charted.
    total = len([u for u in users if u["created_at"] < axis["from"]])
    cumulative = []
    for v in new:
        total += v
        cumulative.append(total)
    out = {"days": axis["days"], "gran": axis["gran"],
           "series": {"played": played, "new": new, "cumulative": cumulative},
           "compare": None}
    if f.get("compare") and r["prev"]:
        lo, hi = r["prev"]
        prev_axis = _axis(lo, lo + (r["now"] - r["start"]), r["tz"])
        out["compare"] = {
            "played": _distinct_series(prev_axis, _played_pairs(users, f, lo, hi)),
            "new": _series(prev_axis, [t for t in joined if lo <= t <= hi]),
        }
    return out


# The live snapshot is the only thing on this page that reaches into the running
# server, and Overview is the page the dashboard opens on. If that call raises,
# an unguarded Overview fails while every other tab still works, which reads as
# "analytics is broken" rather than "one number is missing". A failure here is
# itself worth reporting, so it degrades into the "needs attention" state the
# panel already knows how to draw.
def _live() -> Dict[str, Any]:
    if not _live_snapshot:
        return {}
    try:
        snap = _live_snapshot()
        return snap if isinstance(snap, dict) else {}
    except Exception as exc:  # noqa: BLE001
        print(f"[analytics] live snapshot failed: {exc}")
        return {"ok": False,
                "status_note": "The live server counters could not be read."}


def _online_now(live: Dict[str, Any], users: List[Dict[str, Any]], now: int) -> int:
    online = _int(live.get("online_players"), -1)
    if online < 0:
        online = len([u for u in users if u["online"] and now - u["last_active"] <= 300])
    return online


def _live_panel(live: Dict[str, Any], users: List[Dict[str, Any]], now: int) -> Dict[str, Any]:
    recent = sorted((u for u in users if u["created_at"] > 0), key=lambda u: -u["created_at"])[:5]
    return {
        "online_players": _online_now(live, users, now),
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
        _add(out, int((v - lo) // width))
    return [{"label": f"{lo + b * width}–{lo + (b + 1) * width - 1}{unit}", "value": out.get(b, 0)}
            for b in range(min(buckets, max(out) + 1))]


# ═══════════════════════════════════════════════════════════════════════════
#  ALERTS, only things a developer would actually act on
# ═══════════════════════════════════════════════════════════════════════════
# Below these counts a swing is sampling noise, not a signal. Alerting on a
# 2-game day is how a dashboard trains its owner to ignore it.
_ALERT_MIN_GAMES = 15
_ALERT_MIN_PLAYERS = 10


def _alerts(f: Dict[str, Any], users: List[Dict[str, Any]], live: Dict[str, Any]
            ) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    r = _range(f)
    status = _scan_status()

    if not live.get("ok", True):
        out.append({"level": "bad", "title": "Server needs attention",
                    "detail": str(live.get("status_note") or "A health check is failing."),
                    "section": "technical"})

    if _get_firestore and _get_firestore() is None:
        out.append({"level": "bad", "title": "Player database not connected",
                    "detail": "Firebase isn't configured on this server, so account "
                              "numbers can't be read.", "section": "technical"})
    elif not status["ok"]:
        stale = (f" Showing the read from {status['age_sec'] // 60} min ago."
                 if status["age_sec"] is not None else " No player numbers could be shown.")
        out.append({"level": "bad", "title": "Couldn't read the player database",
                    "detail": f"{status['error']}.{stale}", "section": "technical"})

    # Abandoned games exist only in the server's own records: a game nobody
    # finished is never saved to anybody's account.
    disk = _filter_games(_load_games(), f, r["start"], r["end"])
    if len(disk) >= _ALERT_MIN_GAMES:
        rate = _pct(len([g for g in disk if _game_completed(g)]), len(disk)) or 0
        if rate < 70:
            out.append({"level": "warn", "title": "Players are leaving games early",
                        "detail": f"Only {rate}% of {len(disk)} games the server recorded "
                                  f"were played to the end in this range.", "section": "technical"})

    if r["prev"]:
        lo, hi = r["prev"]
        now_n = len(_entries(users, f, r["start"], r["end"]))
        prev_n = len(_entries(users, f, lo, hi))
        if (prev_n >= _ALERT_MIN_GAMES and now_n < prev_n * 0.6
                and _coverage(users, lo, hi, False) is None):
            out.append({"level": "warn", "title": "Fewer games than last period",
                        "detail": f"{now_n} games this period vs {prev_n} before it.",
                        "section": "gameplay"})

    ret = _retention(users, 7, r["now"])
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


def _server_card(live: Dict[str, Any]) -> Dict[str, Any]:
    ok = bool(live.get("ok", True))
    return _card("Server", "Healthy" if ok else "Needs attention",
                 tone="good" if ok else "bad",
                 hint=str(live.get("status_note") or "All checks passing."))


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════
def _section_overview(f: Dict[str, Any]) -> Dict[str, Any]:
    r = _range(f)
    users = _users(f)
    now, start, end = r["now"], r["start"], r["end"]
    axis = _chart_axis(users, r)
    entries = _entries(users, f, start, end)
    played = _played_uids(users, f, start, end, lifetime=r["lifetime"])
    growth = _growth(users, f, r, axis)
    games_series = _series(axis, [e["t"] for _u, e in entries])
    live = _live()

    retention = [x for x in (_retention(users, d, now) for d in (1, 7, 30)) if x]
    ret7 = next((x for x in retention if x["day"] == 7), None)
    strategies, source = _strategy_counts(users, entries, f, r["lifetime"])
    top_strategy = _top(strategies, 1)
    sizes = _size_counts(users, entries, f, r["lifetime"])
    top_size = _top(sizes, 1)
    counters = r["lifetime"] and not _game_filtered(f)

    ret_card = _card(
        "Came back after 7 days", ret7["rate"] if ret7 else None, unit="%",
        hint=("Of players who joined at least 7 days ago, the share still playing a "
              "week later." if ret7 else ""),
        tone="good" if ret7 and (ret7["rate"] or 0) >= 30 else "neutral")
    strategy_card = _card(
        "Most played strategy", top_strategy[0][0] if top_strategy else None,
        hint=(f"Played {top_strategy[0][1]} times. " if top_strategy else "")
        + ("Each player names the strategy they built at the end of a game."
           if source == "confirmed" else "The strategy the game spotted on each finished board."))
    size_card = _card(
        "Most played table", _players_label(top_size[0][0]) if top_size else None,
        hint=(f"{top_size[0][1]} games at that size." if top_size else ""))
    online_card = _card("Players online now", _online_now(live, users, now),
                        hint="Signed in and seen in the last few minutes.")
    rooms_card = _card("Games being played", _int(live.get("active_games"), 0),
                       hint="Rooms with a game running right now.")

    if r["lifetime"]:
        cards = [
            _card("Players", len(users),
                  hint="Every account ever made. Test and guest accounts follow the Filters."),
            _card("Played a game", len(played), hint="Accounts that have finished at least one game."),
            _card("Games played",
                  sum(u["games"] for u in users) if counters else len(entries),
                  spark=games_series,
                  hint="Every game every account has finished, counted once per player: "
                       "the homepage adds up the same counters."),
            _card("Hours played", _hours_total(users), unit=" h",
                  hint="Hours with the game open, added up across accounts: the number "
                       "each player sees on their own page."),
            ret_card, strategy_card, size_card, online_card, rooms_card, _server_card(live),
        ]
    else:
        lo, hi = r["prev"]
        prev_entries = _entries(users, f, lo, hi)
        prev_played = _played_uids(users, f, lo, hi)
        new_now = [u for u in users if start <= u["created_at"] <= end]
        new_prev = [u for u in users if lo <= u["created_at"] <= hi]
        fully_logged = (_coverage(users, start, end, False) is None
                        and _coverage(users, lo, hi, False) is None)
        cards = [
            _card("Players on the game", len([u for u in users if u["last_active"] >= start]),
                  hint="Accounts that opened the game in this date range, whether or not "
                       "they finished a game."),
            _card("Played a game", len(played), delta=_delta(len(played), len(prev_played)),
                  spark=growth["series"]["played"],
                  hint="Accounts that finished at least one game in this date range."),
            _card("New players", len(new_now), delta=_delta(len(new_now), len(new_prev)),
                  spark=growth["series"]["new"], hint="Accounts created in this date range."),
            _card("Games played", len(entries),
                  delta=_delta(len(entries), len(prev_entries)) if fully_logged else None,
                  spark=games_series,
                  hint="Games finished in this date range, counted once per player at the "
                       "table, the way the homepage counts."),
            ret_card, strategy_card, size_card, online_card, rooms_card, _server_card(live),
        ]

    return {
        "cards": cards,
        "growth": growth,
        "games": {"days": axis["days"], "gran": axis["gran"], "played": games_series,
                  "multiplayer": _series(axis, [e["t"] for _u, e in entries if e["humans"] >= 2])},
        "retention": retention,
        "live": _live_panel(live, users, now),
        "alerts": _alerts(f, users, live)[:3],
        "coverage": _coverage(users, start, end, r["lifetime"]),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: PLAYERS
# ═══════════════════════════════════════════════════════════════════════════
_MAX_TABLE_ROWS = 2000


def _section_players(f: Dict[str, Any]) -> Dict[str, Any]:
    r = _range(f)
    users = _users(f)
    now, start, end = r["now"], r["start"], r["end"]
    axis = _chart_axis(users, r)
    played = _played_uids(users, f, start, end, lifetime=r["lifetime"])

    games_in: Dict[str, int] = {}
    for u, _e in _entries(users, f, start, end):
        _add(games_in, u["uid"])
    days_in = {u["uid"]: len([d for d in u["play_days"] if start <= d <= end]) for u in users}

    levels: Dict[str, int] = {}
    for u in users:
        band = "1–4" if u["level"] < 5 else "5–9" if u["level"] < 10 else \
               "10–24" if u["level"] < 25 else "25–49" if u["level"] < 50 else "50+"
        _add(levels, band)

    if r["lifetime"]:
        # Lifetime is the whole life of the game: no card here may quietly
        # measure a shorter window (a "last 30 days" card once sat in this row).
        counters = not _game_filtered(f)
        cards = [
            _card("Total accounts", len(users)),
            _card("Played a game", len(played), hint="Accounts that have finished at least one game."),
            _card("Games played",
                  sum(u["games"] for u in users) if counters
                  else len(_entries(users, f, start, end)),
                  hint="Every game every account has finished since the game began, "
                       "counted once per player."),
            _card("Hours played", _hours_total(users), unit=" h",
                  hint="Hours with the game open, added up across accounts."),
        ]
    else:
        lo, hi = r["prev"]
        new_now = len([u for u in users if start <= u["created_at"] <= end])
        new_prev = len([u for u in users if lo <= u["created_at"] <= hi])
        cards = [
            _card("Players on the game", len([u for u in users if u["last_active"] >= start]),
                  hint="Accounts that opened the game in this date range."),
            _card("Played a game", len(played),
                  delta=_delta(len(played), len(_played_uids(users, f, lo, hi))),
                  hint="Accounts that finished at least one game in this date range."),
            _card("New players", new_now, delta=_delta(new_now, new_prev),
                  hint="Accounts created in this date range."),
            _card("Total accounts", len(users)),
        ]

    columns = [
        {"key": "name", "label": "Player", "always": True, "type": "text"},
        {"key": "games", "label": "Games", "always": True, "type": "num"},
    ]
    if not r["lifetime"]:
        columns += [
            {"key": "games_range", "label": "Games in range", "always": True, "type": "num"},
            {"key": "days_range", "label": "Days played", "always": True, "type": "num"},
        ]
    columns += [
        {"key": "wins", "label": "Wins", "always": True, "type": "num"},
        {"key": "level", "label": "Level", "always": True, "type": "num"},
        {"key": "strategy", "label": "Favourite strategy", "always": True, "type": "text"},
        {"key": "last_seen", "label": "Last seen", "always": True, "type": "date"},
        {"key": "hours", "label": "Hours", "type": "num"},
        {"key": "table", "label": "Most played table", "type": "text"},
        {"key": "competitive", "label": "Competitive games", "type": "num"},
        {"key": "comp_wins", "label": "Comp wins (season)", "type": "num"},
        {"key": "comp_points", "label": "Ocean Points (season)", "type": "num"},
        {"key": "joined", "label": "Joined", "type": "date"},
        {"key": "xp", "label": "XP", "type": "num"},
        {"key": "coins", "label": "Coins", "type": "num"},
        {"key": "icons", "label": "Critters", "type": "num"},
        {"key": "prestige", "label": "Prestige", "type": "num"},
        {"key": "streak", "label": "Longest streak", "type": "num"},
        {"key": "best", "label": "Best score", "type": "num"},
    ]
    rows = []
    for u in users:
        size = _favorite_size(u)
        rows.append({
            "name": u["nickname"] or "Player",
            "games": u["games"],
            "games_range": games_in.get(u["uid"], 0),
            "days_range": days_in.get(u["uid"], 0),
            "wins": u["wins"],
            "level": u["level"],
            "strategy": _favorite(u),
            "last_seen": u["last_active"],
            "hours": round(u["hours"], 1),
            "table": _players_label(size) if size else "",
            "competitive": _comp_games(u),
            "comp_wins": u["comp_wins"],
            "comp_points": u["comp_points"],
            "joined": u["created_at"],
            "xp": u["total_xp"],
            "coins": u["coins"],
            "icons": u["icons"],
            "prestige": u["prestige_level"],
            "streak": u["streak_longest"],
            "best": u["highest_score"],
            # "On the game in this range": what the table shows first.
            "in_range": r["lifetime"] or u["last_active"] >= start,
        })
    if r["lifetime"]:
        rows.sort(key=lambda x: (-x["games"], -x["last_seen"]))
        sort = {"key": "games", "dir": "desc"}
    else:
        rows.sort(key=lambda x: (-x["last_seen"], -x["games"]))
        sort = {"key": "last_seen", "dir": "desc"}

    return {
        "cards": cards,
        "growth": _growth(users, f, r, axis),
        "retention": [x for x in (_retention(users, d, now) for d in (1, 3, 7, 14, 30)) if x],
        "funnel": [
            {"label": "Created an account", "value": len(users)},
            {"label": "Played a game", "value": len([u for u in users if u["games"] >= 1])},
            {"label": "Came back another day", "value": len(
                [u for u in users if u["created_at"] > 0 and u["last_active"] - u["created_at"] >= DAY])},
            {"label": "Played 5 games", "value": len([u for u in users if u["games"] >= 5])},
            {"label": "Played 25 games", "value": len([u for u in users if u["games"] >= 25])},
        ],
        "levels": [{"label": k, "value": v} for k, v in
                   sorted(levels.items(), key=lambda kv: ["1–4", "5–9", "10–24", "25–49", "50+"].index(kv[0]))],
        "table": {"columns": columns, "rows": rows[:_MAX_TABLE_ROWS], "sort": sort,
                  "in_range_count": len([x for x in rows if x["in_range"]])},
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: GAMEPLAY
# ═══════════════════════════════════════════════════════════════════════════
def _section_gameplay(f: Dict[str, Any]) -> Dict[str, Any]:
    r = _range(f)
    users = _users(f)
    start, end = r["start"], r["end"]
    axis = _chart_axis(users, r)
    entries = _entries(users, f, start, end)
    counters = r["lifetime"] and not _game_filtered(f)

    total = sum(u["games"] for u in users) if counters else len(entries)
    players = _played_uids(users, f, start, end, lifetime=r["lifetime"])
    multiplayer = [e for _u, e in entries if e["humans"] >= 2]
    if counters:
        # An account with games but no score total is missing data, not a zero.
        scored = [u for u in users if u["completed"] > 0 and u["total_score"] > 0]
        avg = (sum(u["total_score"] for u in scored) / sum(u["completed"] for u in scored)) if scored else None
    else:
        avg = _mean([e["score"] for _u, e in entries])

    games_delta = None
    if r["prev"]:
        lo, hi = r["prev"]
        if _coverage(users, start, end, False) is None and _coverage(users, lo, hi, False) is None:
            games_delta = _delta(len(entries), len(_entries(users, f, lo, hi)))

    modes: Dict[str, int] = {}
    for _u, e in entries:
        _add(modes, _MODE_LABEL[e["mode"]])
    strategies, source = _strategy_counts(users, entries, f, r["lifetime"])
    games_series = _series(axis, [e["t"] for _u, e in entries])

    return {
        "cards": [
            _card("Games played", total, delta=games_delta, spark=games_series,
                  hint="Counted once per player at the table, the way the homepage counts."),
            _card("Players who played", len(players)),
            _card("Played with other people", _pct(len(multiplayer), len(entries)), unit="%",
                  hint="Share of games with at least two signed-in players at the table. "
                       "Guests save no games, so a game with a guest counts as solo."),
            _card("Average score", round(avg, 1) if avg is not None else None,
                  hint="Mean final score across the games counted."),
        ],
        "volume": {
            "days": axis["days"], "gran": axis["gran"],
            "games": games_series,
            "multiplayer": _series(axis, [e["t"] for e in multiplayer]),
            "players": _distinct_series(axis, _played_pairs(users, f, start, end)),
        },
        "sizes": [{"label": _players_label(k), "value": v}
                  for k, v in sorted(_size_counts(users, entries, f, r["lifetime"]).items())],
        "modes": [{"label": k, "value": v} for k, v in _top(modes, 6)],
        "strategies": [{"label": k, "value": v} for k, v in _top(strategies, 20)],
        "strategy_source": source,
        "scores": _histogram([e["score"] for _u, e in entries], 8, ""),
        "coverage": _coverage(users, start, end, r["lifetime"]),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: CARDS
# ═══════════════════════════════════════════════════════════════════════════
# A card needs to have been seen this many times before its win rate says
# anything. Below it, one lucky game swings the number by tens of points.
_CARD_MIN_SAMPLE = 20
# How far from the middle a card's win rate has to sit before it is worth a look.
_CARD_FLAG_MARGIN = 12.0


def _section_cards(f: Dict[str, Any]) -> Dict[str, Any]:
    r = _range(f)
    users = _users(f)
    min_sample = max(1, _int(f.get("min_sample"), _CARD_MIN_SAMPLE))

    played: Dict[str, int] = {}      # every copy played, counted once each
    appeared: Dict[str, int] = {}    # BOARDS it appeared on, the win-rate sample
    won_with: Dict[str, int] = {}
    families: Dict[str, int] = {}
    oceans: Dict[str, int] = {}
    boards = 0

    # Each account's OWN board, from its own log: every signed-in player's board
    # is counted once, and never again from a tablemate's copy of the same game.
    for _u, e in _entries(users, f, r["start"], r["end"]):
        if not e["board"]:
            continue
        boards += 1
        # A win rate has to be per BOARD, not per copy: a board holding three
        # Mandarin Gobies is still one win, and counting it three times would
        # let a stackable animal inflate its own rate.
        seen: Set[str] = set()
        for ocean in e["board"]:
            if ocean["o"]:
                _add(oceans, ocean["o"])
            for name in ocean["a"]:
                _add(played, name)
                family = _card_species.get(name.strip().lower())
                if family:
                    _add(families, family)
                seen.add(name)
        for name in seen:
            _add(appeared, name)
            if e["won"]:
                _add(won_with, name)

    rows = []
    for name, n in appeared.items():
        wins = won_with.get(name, 0)
        rows.append({"name": name, "boards": n, "played": played.get(name, 0),
                     "wins": wins, "win_rate": _pct(wins, n),
                     "family": _card_species.get(name.strip().lower(), ""),
                     "enough": n >= min_sample})
    rows.sort(key=lambda x: (-x["played"], x["name"]))

    rated = [x for x in rows if x["enough"] and x["win_rate"] is not None]
    baseline = _mean([x["win_rate"] for x in rated])
    review = []
    if baseline is not None:
        for x in rated:
            gap = x["win_rate"] - baseline
            if abs(gap) >= _CARD_FLAG_MARGIN:
                review.append({**x, "gap": round(gap, 1),
                               "direction": "strong" if gap > 0 else "weak"})
        review.sort(key=lambda x: -abs(x["gap"]))

    return {
        "cards": [
            _card("Animals seen", len(rows), hint="Different animals played to a board."),
            _card("Boards measured", boards,
                  hint="Every signed-in player's final board, one per game they finished."),
            _card("Typical win rate", round(baseline, 1) if baseline is not None else None, unit="%",
                  hint=f"Average across animals on {min_sample}+ boards."),
            _card("Worth a balance look", len(review) or None,
                  tone="warn" if review else "neutral",
                  hint=f"Win rate more than {int(_CARD_FLAG_MARGIN)} points from typical."),
        ],
        "min_sample": min_sample,
        "most_played": [{"label": x["name"], "value": x["played"]} for x in rows[:12]],
        "species": [{"label": k, "value": v} for k, v in _top(families, 10)],
        "oceans": [{"label": k, "value": v} for k, v in _top(oceans, 10)],
        "review": review[:20],
        "table": {
            "columns": [
                {"key": "name", "label": "Animal", "always": True, "type": "text"},
                {"key": "played", "label": "Times played", "always": True, "type": "num"},
                {"key": "boards", "label": "Boards", "always": True, "type": "num"},
                {"key": "win_rate", "label": "Win rate", "always": True, "type": "pct"},
                {"key": "family", "label": "Family", "type": "text"},
                {"key": "wins", "label": "Wins", "type": "num"},
            ],
            "rows": rows[:200],
            "sort": {"key": "played", "dir": "desc"},
        },
        "coverage": _coverage(users, r["start"], r["end"], r["lifetime"]),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: COMPETITIVE
# ═══════════════════════════════════════════════════════════════════════════
def _section_competitive(f: Dict[str, Any]) -> Dict[str, Any]:
    r = _range(f)
    users = _users(f)
    start, end = r["start"], r["end"]
    axis = _chart_axis(users, r)
    # This page is about Competitive whatever the mode filter says; table size
    # and "only games with other people" still narrow it.
    cf = dict(f, mode="competitive")
    entries = _entries(users, cf, start, end)
    counters = r["lifetime"] and not (f.get("player_count") or f.get("only_multiplayer"))

    per: Dict[str, Dict[str, Any]] = {}
    if counters:
        for u in users:
            games = _comp_games(u)
            if games <= 0:
                continue
            w, l, d = u["life_comp"]
            per[u["uid"]] = {"name": u["nickname"] or "Player", "games": games, "wins": w,
                             "losses": l, "draws": d, "win_rate": _pct(w, w + l + d),
                             "points": u["comp_points"], "rank": u["comp_rank"] or "-"}
        wins = sum(x["wins"] for x in per.values())
        outcomes = [{"label": "Wins", "value": wins},
                    {"label": "Losses", "value": sum(x["losses"] for x in per.values())},
                    {"label": "Draws", "value": sum(x["draws"] for x in per.values())}]
        total = sum(x["games"] for x in per.values())
    else:
        for u, e in entries:
            row = per.setdefault(u["uid"], {"name": u["nickname"] or "Player", "games": 0,
                                            "wins": 0, "points": u["comp_points"],
                                            "rank": u["comp_rank"] or "-"})
            row["games"] += 1
            row["wins"] += 1 if e["won"] else 0
        for row in per.values():
            row["win_rate"] = _pct(row["wins"], row["games"])
        wins = sum(x["wins"] for x in per.values())
        total = len(entries)
        outcomes = [{"label": "Won", "value": wins},
                    {"label": "Didn't win", "value": total - wins}]

    paired = [e for _u, e in entries if e["mode"] == "competitive"]
    ffa = [e for _u, e in entries if e["mode"] == "ranked"]
    delta = None
    if r["prev"]:
        lo, hi = r["prev"]
        if _coverage(users, start, end, False) is None and _coverage(users, lo, hi, False) is None:
            delta = _delta(total, len(_entries(users, cf, lo, hi)))
    leader = max(users, key=lambda u: u["comp_points"], default=None)

    columns = [
        {"key": "name", "label": "Player", "always": True, "type": "text"},
        {"key": "games", "label": "Games", "always": True, "type": "num"},
        {"key": "wins", "label": "Wins", "always": True, "type": "num"},
        {"key": "win_rate", "label": "Win rate", "always": True, "type": "pct"},
        {"key": "points", "label": "Ocean Points (season)", "always": True, "type": "num"},
        {"key": "rank", "label": "Rank (season)", "type": "text"},
    ]
    if counters:
        columns[3:3] = [{"key": "losses", "label": "Losses", "type": "num"},
                        {"key": "draws", "label": "Draws", "type": "num"}]
    table = sorted(per.values(), key=lambda x: (-x["games"], -x["wins"], x["name"]))

    return {
        "cards": [
            _card("Competitive games", total, delta=delta,
                  spark=_series(axis, [e["t"] for _u, e in entries]),
                  hint="Both competitive tables, the paired game and the free-for-all, "
                       "counted once per player."),
            _card("Players who played competitive", len(per)),
            _card("Free-for-all games", len(ffa),
                  hint=(f"The other {len(paired)} were the paired 4-seat game."
                        + (" From each player's saved games." if counters else ""))),
            _card("Top Ocean Points", leader["comp_points"] if leader and leader["comp_points"] > 0 else None,
                  hint=(f"{leader['nickname'] or 'Player'}, this season."
                        if leader and leader["comp_points"] > 0 else "Nobody has scored this season.")),
        ],
        "volume": {"days": axis["days"], "gran": axis["gran"],
                   "paired": _series(axis, [e["t"] for e in paired]),
                   "ffa": _series(axis, [e["t"] for e in ffa])},
        "outcomes": outcomes,
        "top": [{"label": u["nickname"] or "Player", "value": u["comp_points"]}
                for u in sorted(users, key=lambda u: -u["comp_points"]) if u["comp_points"] > 0][:8],
        "table": {"columns": columns, "rows": table[:_MAX_TABLE_ROWS],
                  "sort": {"key": "games", "dir": "desc"}},
        "coverage": _coverage(users, start, end, r["lifetime"]),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: CLANS
# ═══════════════════════════════════════════════════════════════════════════
def _current_clan_season(clans: List[Dict[str, Any]]) -> str:
    if _clan_season:
        try:
            sid = str(_clan_season() or "")
            if sid:
                return sid
        except Exception as exc:  # noqa: BLE001
            print(f"[analytics] clan season lookup failed: {exc}")
    keys = [sid for c in clans for sid in c["seasons"]]
    return max(keys) if keys else ""


def _section_clans(f: Dict[str, Any]) -> Dict[str, Any]:
    users = _users(f)
    clans, _err = _cached_read("clans", _read_clans, bool(f.get("refresh")))
    lifetime = f["days"] == LIFETIME
    sid = _current_clan_season(clans or [])

    rows = []
    for c in clans or []:
        slot = c["seasons"].get(sid) or {}
        if lifetime:
            points = max(c["lifetime_points"], sum(s["points"] for s in c["seasons"].values()))
            games = sum(s["games"] for s in c["seasons"].values())
        else:
            points, games = slot.get("points", 0), slot.get("games", 0)
        rows.append({"name": c["name"], "members": c["members"], "points": points,
                     "games": games, "created": c["created"]})
    rows.sort(key=lambda c: (-c["points"], -c["members"], c["name"]))
    in_clan = len([u for u in users if u["clan_id"]])
    sizes = [c["members"] for c in rows if c["members"]]
    known = clans is not None

    return {
        "cards": [
            _card("Clans", len(rows) if known else None),
            _card("Players in a clan", in_clan,
                  hint=f"{_pct(in_clan, len(users))}% of accounts." if users else ""),
            _card("Average clan size", round(_mean(sizes), 1) if sizes else None),
            _card("Points, every season" if lifetime else "Points this season",
                  sum(c["points"] for c in rows) if known else None,
                  hint="Every season added up." if lifetime else (f"Season {sid}." if sid else "")),
        ],
        "season": sid,
        "top": [{"label": c["name"], "value": c["points"]} for c in rows[:10] if c["points"] > 0],
        "sizes": _histogram(sizes, 6, ""),
        "table": {
            "columns": [
                {"key": "name", "label": "Clan", "always": True, "type": "text"},
                {"key": "members", "label": "Members", "always": True, "type": "num"},
                {"key": "points", "label": "Points", "always": True, "type": "num"},
                {"key": "games", "label": "Games", "type": "num"},
                {"key": "created", "label": "Created", "type": "date"},
            ],
            "rows": rows[:200],
            "sort": {"key": "points", "dir": "desc"},
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: ECONOMY
# ═══════════════════════════════════════════════════════════════════════════
def _section_economy(f: Dict[str, Any]) -> Dict[str, Any]:
    r = _range(f)
    users = _users(f)
    refresh = bool(f.get("refresh"))
    axis = _chart_axis(users, r)

    held = [u["coins"] for u in users]
    total_held = sum(held)
    with_coins = len([c for c in held if c > 0])

    payments, _perr = _cached_read("payments", _read_payments, refresh)
    in_range = [p for p in (payments or []) if r["start"] <= p["when"] <= r["end"]]
    supporters, _serr = _cached_read("supporters", _read_supporters, refresh)
    revenue = sum(p["cents"] for p in in_range) / 100.0

    tiers: Dict[str, int] = {}
    for u in users:
        if u["supporter_tier"]:
            _add(tiers, u["supporter_tier"])

    return {
        "cards": [
            _card("Coins players hold", total_held,
                  hint=f"Across {with_coins} accounts with a balance." if with_coins else ""),
            _card("Average balance", round(total_held / with_coins) if with_coins else None,
                  hint="Among accounts holding any coins."),
            _card("Purchases", len(in_range) if payments is not None else None,
                  hint=(f"${revenue:,.2f} in completed Stripe payments." if in_range
                        else "Completed Stripe payments. The Store is switched off for now.")),
            _card("Supporters", supporters, hint="Accounts on the supporter wall."),
        ],
        "revenue": {"days": axis["days"], "gran": axis["gran"],
                    "series": _series(axis, [p["when"] for p in in_range]),
                    "total": round(revenue, 2)},
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
    r = _range(f)
    users = _users(f)
    axis = _chart_axis(users, r)

    trades, _err = _cached_read("trades", _read_trades, bool(f.get("refresh")))
    done = [t for t in (trades or []) if r["lifetime"] or r["start"] <= t <= r["end"]]

    team = ([] if f.get("mode") not in ("all", "team")
            else _entries(users, dict(f, mode="team"), r["start"], r["end"]))
    team_sizes: Dict[int, int] = {}
    for _u, e in team:
        if e["pc"]:
            _add(team_sizes, e["pc"])
    trade_series = _series(axis, [t for t in done if t])

    return {
        "cards": [
            _card("Trades completed", len(done) if trades is not None else None, spark=trade_series,
                  hint="One trade is kept per pair of players, so two players trading "
                       "again count once."),
            _card("Team games", len(team), hint="Counted once per player at the table."),
            _card("Critters unlocked", sum(u["icons"] for u in users), hint="Across every account."),
            _card("Backgrounds unlocked", sum(u["backgrounds"] for u in users),
                  hint="Across every account."),
        ],
        "trades": {"days": axis["days"], "gran": axis["gran"], "series": trade_series},
        "team_sizes": [{"label": _players_label(k), "value": v} for k, v in sorted(team_sizes.items())],
        "coverage": _coverage(users, r["start"], r["end"], r["lifetime"]) if team else None,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: TECHNICAL HEALTH
# ═══════════════════════════════════════════════════════════════════════════
def _section_technical(f: Dict[str, Any]) -> Dict[str, Any]:
    r = _range(f)
    live = _live()
    users = _users(f)
    status = _scan_status()
    all_games = _load_games()
    games = _filter_games(all_games, f, r["start"], r["end"])
    prev = _filter_games(all_games, f, *r["prev"]) if r["prev"] else []
    first = min((_game_when(g) for g in all_games if _game_when(g) >= _EPOCH_FLOOR), default=r["now"])
    axis = _axis(first if r["lifetime"] else r["start"], r["now"], r["tz"])

    truncated = [g for g in games if not _game_completed(g)]
    load = live.get("load") if isinstance(live.get("load"), dict) else {}
    skipped = _int(load.get("deep_plan_skipped"))
    granted = _int(load.get("deep_plan_granted"))
    connected = _get_firestore is not None and _get_firestore() is not None
    account_games = sum(u["games"] for u in _load_users(allow_stale=True))

    if not connected:
        db_ok, db_detail = False, "Firebase isn't configured on this server."
    elif not status["ok"]:
        db_ok, db_detail = False, f"The last read of the accounts failed: {status['error']}"
    else:
        age = status["age_sec"] or 0
        db_ok = True
        db_detail = (f"Read {status['accounts']} accounts in {status['read_ms']} ms, "
                     f"{'just now' if age < 60 else str(age // 60) + ' min ago'}.")

    # An empty history directory is only a fault when the records had somewhere
    # to go and did not arrive. See _history_dir_state.
    hist = _history_dir_state(_games_dir)
    accounts_note = (f" Player numbers come from the accounts, which hold {account_games:,} games."
                     if account_games else "")
    if hist["problem"]:
        records_ok, records_detail = False, hist["problem"]
    elif all_games:
        records_ok = True
        records_detail = f"{len(all_games)} games on disk.{accounts_note}"
    else:
        records_ok = True
        records_detail = ("No games recorded yet. The history directory is empty "
                          f"and writable: {hist['path']}.{accounts_note}")

    checks = [
        {"label": "Player database", "ok": db_ok, "detail": db_detail},
        {"label": "Game records", "ok": records_ok, "detail": records_detail},
        {"label": "Bot thinking budget",
         "ok": not (granted and skipped > granted * 0.25),
         "detail": f"{skipped} deep plans skipped, {granted} granted."},
        {"label": "Rooms in memory", "ok": _int(load.get("rooms")) < 400,
         "detail": f"{_int(load.get('rooms'))} rooms held."},
    ]
    healthy = all(c["ok"] for c in checks) and bool(live.get("ok", True))

    return {
        "cards": [
            _card("Server", "Healthy" if healthy else "Needs attention",
                  tone="good" if healthy else "bad"),
            _card("Rooms in memory", _int(load.get("rooms"))),
            _card("Games that ended badly", len(truncated),
                  delta=_delta(len(truncated), len([g for g in prev if not _game_completed(g)])),
                  tone="warn" if len(truncated) > max(5, len(games) * 0.3) else "neutral",
                  hint="From the server's own records: a game nobody finished is never "
                       "saved to an account."),
            _card("Version", _app_version or None),
        ],
        "checks": checks,
        "records_on_disk": len(all_games),
        "load": {
            "rooms": _int(load.get("rooms")),
            "threads": _int(load.get("threads")),
            "deep_plan_slots": _int(load.get("deep_plan_slots")),
            "deep_plan_granted": granted,
            "deep_plan_skipped": skipped,
        },
        "truncated": {"days": axis["days"], "gran": axis["gran"],
                      "series": _series(axis, [_game_when(g) for g in truncated])},
        "alerts": _alerts(f, users, live),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION: PLAYER SEARCH
# ═══════════════════════════════════════════════════════════════════════════
def _player_detail(u: Dict[str, Any]) -> Dict[str, Any]:
    modes: Dict[str, int] = {}
    for e in u["log"]:
        _add(modes, _MODE_LABEL[e["mode"]])
    sizes: Dict[int, int] = {}
    for size, n in list(u["casual_by_size"].items()) + list(u["comp_by_size"].items()):
        _add(sizes, size, n)
    strategies = dict(u["strategy_counts"])
    if not strategies:
        for e in u["log"]:
            if e["strat"]:
                _add(strategies, e["strat"])
    w, l, d = u["life_comp"]
    return {
        "name": u["nickname"] or "Player",
        "friend_code": u["friend_code"],
        "joined": u["created_at"],
        "last_seen": u["last_active"],
        "online": u["online"],
        "games": u["games"], "wins": u["wins"], "level": u["level"],
        "hours": round(u["hours"], 1),
        "xp": u["total_xp"], "coins": u["coins"], "icons": u["icons"],
        "backgrounds": u["backgrounds"], "prestige": u["prestige_level"],
        "highest_score": u["highest_score"],
        "comp_wins": u["comp_wins"], "comp_points": u["comp_points"],
        "comp_rank": u["comp_rank"], "comp_record": {"wins": w, "losses": l, "draws": d},
        "clan_id": u["clan_id"],
        "favorite": _favorite(u),
        "strategies": [{"label": k, "value": v} for k, v in _top(strategies, 6)],
        "sizes": [{"label": _players_label(k), "value": v} for k, v in _top(sizes, 7)],
        "modes": [{"label": k, "value": v} for k, v in _top(modes, 6)],
        # The same games, in the same order, as this player's own History tab.
        "recent": [{
            "when": e["t"],
            "mode": _MODE_LABEL[e["mode"]],
            "players": e["pc"],
            "score": e["score"],
            "won": e["won"],
            "result": "Won" if e["won"] else "Lost",
            "strategy": e["strat"],
            "opponents": ", ".join(n for n, _s in e["scores"]
                                   if n.strip().lower() != e["me"].strip().lower())[:120],
        } for e in reversed(u["log"])],
    }


def _section_search(f: Dict[str, Any], query: str) -> Dict[str, Any]:
    q = str(query or "").strip().lower()
    if len(q) < 2:
        return {"query": query, "matches": [], "player": None}
    rows = _load_users(force=bool(f.get("refresh")))   # always EVERY account, filters off
    matches = [u for u in rows
               if q in (u["nickname"] or "").lower()
               or q == (u["friend_code"] or "").lower()
               or q == u["uid"].lower()]
    # The exact name first, then the players who have played the most.
    matches.sort(key=lambda u: ((u["nickname"] or "").lower() != q, -u["games"]))
    matches = matches[:25]
    return {
        "query": query,
        "matches": [{"name": u["nickname"] or "Player", "friend_code": u["friend_code"],
                     "games": u["games"], "level": u["level"], "last_seen": u["last_active"]}
                    for u in matches],
        "player": _player_detail(matches[0]) if matches else None,
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
        elif action == "live":
            # The Overview's 20-second tick. It reuses whatever account scan is
            # already cached, however old, and never starts one of its own.
            payload = {"live": _live_panel(_live(), _filter_users(_load_users(allow_stale=True), f),
                                           _now())}
        elif action == "export":
            payload = {"sections": {name: fn(f) for name, fn in _SECTIONS.items()}}
        elif action in _SECTIONS:
            payload = _SECTIONS[action](f)
        else:
            handler._send_json({"ok": False, "error": "unknown_section"}, status=404)
            return True
    except Exception as exc:  # noqa: BLE001, a broken section must not 500 the tool
        print(f"[analytics] section {action} failed: {exc}")
        handler._send_json({"ok": False, "error": "section_failed",
                            "detail": str(exc)[:200]})
        return True

    handler._send_json({"ok": True, "section": action, "generated": _now(),
                        "range_days": f["days"], "lifetime": f["days"] == LIFETIME,
                        "data_status": _scan_status(), **payload})
    return True
