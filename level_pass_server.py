"""Currents and Critters — the Level Pass (server-authoritative).

A free reward track laid over the existing 1–100 level curve. Reaching a level
unlocks that level's tier; the player claims it and the SERVER writes the goods.
Wired additively into multiplayer_server exactly like clan_server /
prestige_server / discord_server:

    import level_pass_server
    level_pass_server.init(get_firestore=..., verify_token=...,
                           level_for_xp=..., background_paths=...)   # in main()
    if level_pass_server.handle_post(self, parsed, body): ...        # in do_POST

WHY THE SERVER OWNS THIS
Every tier pays real currency — Critter Coins, Streak Shields, backgrounds. A
client that could say "I reached 60, pay me" is a free-coins button. So the
level is RE-DERIVED here from `stats.total_xp` on the account's own document,
inside the same transaction that writes the reward. The browser's idea of its
level is never an input to a payout.

EXACTLY ONCE, PER LEVEL
Each tier is claimable once, ever. The guard is a create()d ledger document,
`level_pass_claims/{uid}__L{level}`, written INSIDE the payout transaction — so
the reward cannot exist without the ledger and the ledger cannot exist without
the reward. Two tabs double-tapping "Claim" end with one payout and one loser.
(The same primitive as discord_server._grant and prestige_server._commit.)

WHAT THE TRACK DELIBERATELY DOES NOT GRANT
The level-gated CRITTERS (Blue Tang at 10, Sea Star at 100, …) appear on the
track as milestones but are `showcase` — not claimable here. They are already
granted by the client's own unlock sweep, which honours the trading system's
re-earn rule (a critter you traded away has to be earned again, not handed back
by a second unlock path). Paying them out here would quietly reopen that hole.

THE PREMIUM ROW THAT ISN'T HERE YET
Every tier carries a `row` of "free". A paid season track would add tiers with
row "premium" and one entitlement check at claim time; nothing else in this file
or its client would have to move. That is the whole reason `row` exists now.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

# ── Injected by init() (no circular import with multiplayer_server) ──────────
_get_firestore: Optional[Callable[[], Any]] = None
_verify_token: Optional[Callable[[str], Optional[dict]]] = None
_level_for_xp: Optional[Callable[[Any], Any]] = None
_level_totals: List[int] = []
_background_paths: List[str] = []


def init(*, get_firestore, verify_token, level_for_xp, level_totals,
         background_paths) -> None:
    """`level_for_xp` is multiplayer_server._level_progress_for_total_xp and
    `level_totals` is its LEVEL_XP_TOTALS — the ONE level curve, injected
    rather than copied. A third copy of that table is exactly the kind of drift
    that demotes live players.

    The table is also SERVED to the client (see state_payload), because the
    pass and the avatar gallery both have to answer "how much XP until level
    37?" and the honest way to do that is to read the same numbers the server
    grants levels from."""
    global _get_firestore, _verify_token, _level_for_xp, _level_totals, _background_paths
    _get_firestore = get_firestore
    _verify_token = verify_token
    _level_for_xp = level_for_xp
    _level_totals = [int(n) for n in (level_totals or [])]
    _background_paths = [str(p) for p in (background_paths or [])]


# ═══════════════════════════════════════════════════════════════════════════
#  THE 24-HOUR XP BOOST
# ═══════════════════════════════════════════════════════════════════════════
# +20% on EVERY XP source for 24 hours: games, the daily login bonus, weekly
# challenges, achievements, clan challenges, tournaments. Declared here so the
# server, the client's multiplier and the pass card all read one number.
BOOST_PERCENT = 20
BOOST_HOURS = 24
BOOST_MS = BOOST_HOURS * 60 * 60 * 1000

# Hoard caps. Consumables stack, but not without limit — an unclaimed track
# should not become a 40-shield stockpile the day someone hits level 100.
MAX_SHIELDS = 5          # matches PHST_PERK_STACK_MAX in preview-app.js
MAX_BOOSTS = 5
MAX_REROLLS = 5


# ═══════════════════════════════════════════════════════════════════════════
#  THE TRACK
# ═══════════════════════════════════════════════════════════════════════════
# One reward per level at most, and most levels have none — the track is meant
# to be a steady drip, not a slot machine. Coins cluster on the levels either
# side of a critter unlock so the milestone levels feel like arrivals.
#
# type:
#   coins      → Critter Coins                     (amount)
#   shield     → Streak Shields, covers a missed day of a daily streak (amount)
#   sticker    → one critter chat sticker, server-picked from critters you own
#   boost      → one 24-hour +20% XP boost, held until you activate it
#   reroll     → one Weekly Swap token: unlimited weekly-challenge swaps for
#                the rest of that week
#   background → one exclusive avatar background, server-picked from unowned
#   critter    → SHOWCASE ONLY. The level-gated avatar, granted by the normal
#                unlock path — never claimed here (see the module docstring).
_TRACK_SPEC: Sequence[Dict[str, Any]] = (
    {"level": 2,   "type": "coins",      "amount": 50},
    {"level": 3,   "type": "sticker",    "amount": 1},
    {"level": 5,   "type": "shield",     "amount": 1},
    {"level": 7,   "type": "coins",      "amount": 75},
    {"level": 9,   "type": "coins",      "amount": 100},
    {"level": 10,  "type": "critter",    "critter": "Blue Tang",             "img": "/avatars/blue-tang.png"},
    {"level": 11,  "type": "coins",      "amount": 100},
    {"level": 13,  "type": "boost",      "amount": 1},
    {"level": 15,  "type": "reroll",     "amount": 1},
    {"level": 17,  "type": "sticker",    "amount": 1},
    {"level": 19,  "type": "coins",      "amount": 150},
    {"level": 20,  "type": "critter",    "critter": "Orange Tube Sponge",    "img": "/avatars/sea-sponge.png"},
    {"level": 21,  "type": "coins",      "amount": 150},
    {"level": 23,  "type": "shield",     "amount": 1},
    {"level": 25,  "type": "sticker",    "amount": 1},
    {"level": 27,  "type": "reroll",     "amount": 1},
    {"level": 29,  "type": "coins",      "amount": 200},
    {"level": 30,  "type": "critter",    "critter": "Mahi Mahi",             "img": "/avatars/mahi-mahi.png"},
    {"level": 31,  "type": "coins",      "amount": 200},
    {"level": 33,  "type": "boost",      "amount": 1},
    {"level": 35,  "type": "shield",     "amount": 1},
    {"level": 37,  "type": "sticker",    "amount": 1},
    # Level 40 is the one milestone level with no critter of its own — the
    # curve jumps 30 → 50. The mid-track background fills it so the longest
    # gap on the whole pass still has something waiting at the end of it.
    {"level": 40,  "type": "background", "amount": 1},
    {"level": 43,  "type": "reroll",     "amount": 1},
    {"level": 45,  "type": "coins",      "amount": 250},
    {"level": 47,  "type": "sticker",    "amount": 1},
    {"level": 49,  "type": "coins",      "amount": 250},
    {"level": 50,  "type": "critter",    "critter": "Manta Ray",             "img": "/avatars/manta-ray.png"},
    {"level": 51,  "type": "coins",      "amount": 250},
    {"level": 53,  "type": "shield",     "amount": 1},
    {"level": 55,  "type": "boost",      "amount": 1},
    {"level": 57,  "type": "sticker",    "amount": 1},
    {"level": 59,  "type": "coins",      "amount": 300},
    {"level": 60,  "type": "critter",    "critter": "King Crab",             "img": "/avatars/king-crab.png"},
    {"level": 61,  "type": "coins",      "amount": 300},
    {"level": 63,  "type": "reroll",     "amount": 1},
    {"level": 65,  "type": "shield",     "amount": 1},
    {"level": 67,  "type": "sticker",    "amount": 1},
    {"level": 69,  "type": "coins",      "amount": 350},
    {"level": 70,  "type": "critter",    "critter": "Blue Marlin",           "img": "/avatars/blue-marlin.png"},
    {"level": 71,  "type": "coins",      "amount": 350},
    {"level": 73,  "type": "boost",      "amount": 1},
    {"level": 75,  "type": "background", "amount": 1},
    {"level": 77,  "type": "sticker",    "amount": 1},
    {"level": 79,  "type": "coins",      "amount": 400},
    {"level": 80,  "type": "critter",    "critter": "Great Albatross",       "img": "/avatars/great-albatross.png"},
    {"level": 81,  "type": "coins",      "amount": 400},
    {"level": 83,  "type": "shield",     "amount": 1},
    {"level": 85,  "type": "reroll",     "amount": 1},
    {"level": 87,  "type": "sticker",    "amount": 1},
    {"level": 89,  "type": "coins",      "amount": 450},
    {"level": 90,  "type": "critter",    "critter": "Great White Shark",     "img": "/avatars/great-white-shark.png"},
    {"level": 91,  "type": "coins",      "amount": 450},
    {"level": 93,  "type": "boost",      "amount": 1},
    {"level": 95,  "type": "shield",     "amount": 1},
    {"level": 97,  "type": "sticker",    "amount": 1},
    {"level": 99,  "type": "coins",      "amount": 500},
    {"level": 100, "type": "critter",    "critter": "Sea Star",              "img": "/avatars/sea-star.png"},
    # The end of the track pays like the end of a track.
    {"level": 100, "type": "coins",      "amount": 1000, "key": "100c"},
    {"level": 100, "type": "background", "amount": 1,    "key": "100b"},
)

# How each reward type describes itself. One place, so the pass card, the claim
# toast and the ledger entry can never word the same reward three ways.
_TYPE_META: Dict[str, Dict[str, str]] = {
    "coins":      {"icon": "🪙", "name": "Critter Coins"},
    "shield":     {"icon": "🛡️", "name": "Streak Shield"},
    "sticker":    {"icon": "🎴", "name": "Critter Sticker"},
    "boost":      {"icon": "⚡", "name": f"{BOOST_HOURS}h XP Boost"},
    "reroll":     {"icon": "🔄", "name": "Weekly Swap"},
    "background": {"icon": "🖼️", "name": "Avatar Background"},
    "critter":    {"icon": "⭐", "name": "Critter"},
}

CLAIMABLE_TYPES = frozenset({"coins", "shield", "sticker", "boost", "reroll", "background"})


def _tier_id(spec: Dict[str, Any]) -> str:
    """Stable identifier for a tier. Level 100 carries three rewards, so the
    level alone is not unique — `key` disambiguates, and it is what the ledger
    document is named after. Changing a key re-opens that tier for claiming, so
    they are append-only in practice."""
    extra = str(spec.get("key") or "")
    return f"L{int(spec['level'])}" + (f"_{extra}" if extra else "")


def _describe(spec: Dict[str, Any]) -> str:
    t = str(spec.get("type"))
    n = int(spec.get("amount") or 1)
    if t == "coins":
        return f"{n:,} Critter Coins"
    if t == "shield":
        return f"{n} Streak Shield" + ("s" if n != 1 else "")
    if t == "sticker":
        return "1 Critter Sticker"
    if t == "boost":
        return f"{BOOST_HOURS}-Hour XP Boost (+{BOOST_PERCENT}%)"
    if t == "reroll":
        return "Weekly Swap Token"
    if t == "background":
        return "1 Avatar Background"
    if t == "critter":
        return str(spec.get("critter") or "Critter")
    return t


def _blurb(spec: Dict[str, Any]) -> str:
    """The one-line "what does this actually do" under a tier's name."""
    t = str(spec.get("type"))
    return {
        "coins":      "Spend them in the Store on skins, backgrounds and perks.",
        "shield":     "Covers one missed day so your daily streak survives it.",
        "sticker":    "A critter sticker for game chat, picked from ones you own.",
        "boost":      f"+{BOOST_PERCENT}% XP from everything for {BOOST_HOURS} hours — "
                      "games, challenges, achievements and the daily bonus. Activate it when you want it.",
        "reroll":     "Swap out as many weekly challenges as you like for the rest of that week.",
        "background": "An exclusive scene behind your avatar, everywhere it appears.",
        "critter":    "Unlocked automatically the moment you reach this level.",
    }.get(t, "")


def track() -> List[Dict[str, Any]]:
    """The published track. Pure data — safe to serve to anyone, signed in or
    not, and the client renders straight from it so the two can never drift."""
    out: List[Dict[str, Any]] = []
    for spec in _TRACK_SPEC:
        t = str(spec["type"])
        meta = _TYPE_META.get(t, {"icon": "🎁", "name": t})
        entry = {
            "id": _tier_id(spec),
            "level": int(spec["level"]),
            "row": "free",
            "type": t,
            "amount": int(spec.get("amount") or 1),
            "icon": meta["icon"],
            "label": _describe(spec),
            "blurb": _blurb(spec),
            "claimable": t in CLAIMABLE_TYPES,
        }
        if t == "critter":
            entry["critter"] = str(spec.get("critter") or "")
            entry["img"] = str(spec.get("img") or "")
        out.append(entry)
    return out


_TRACK_BY_ID: Dict[str, Dict[str, Any]] = {_tier_id(s): dict(s) for s in _TRACK_SPEC}


def max_level() -> int:
    return max(int(s["level"]) for s in _TRACK_SPEC)


# ═══════════════════════════════════════════════════════════════════════════
#  FIRESTORE
# ═══════════════════════════════════════════════════════════════════════════
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_AVATAR_PATH = re.compile(r"^/avatars/[a-z0-9_-]+\.png$")
_BG_PATH = re.compile(r"^/backgrounds/[a-z0-9_-]+\.png$")


def _users(db):
    return db.collection("users")


def _ledger(db):
    return db.collection("level_pass_claims")


def _transactional():
    from firebase_admin import firestore
    fn = getattr(firestore, "transactional", None)
    if fn is None:
        from google.cloud.firestore_v1 import transactional as fn  # type: ignore
    return fn


def _array_union():
    """Appends to unlocked_backgrounds / emote_icons go through ArrayUnion, not
    through "read the list, add one, write it back". The read here is normalised
    (lowercased, junk dropped) so it can pick what is missing — writing that
    normalised copy back would quietly delete any entry the filter rejected."""
    from firebase_admin import firestore
    fn = getattr(firestore, "ArrayUnion", None)
    if fn is None:
        from google.cloud.firestore_v1 import ArrayUnion as fn  # type: ignore
    return fn


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _now_ms() -> int:
    return int(time.time() * 1000)


def _stats_of(doc: Dict[str, Any]) -> Dict[str, Any]:
    s = doc.get("stats")
    return s if isinstance(s, dict) else {}


def _level_of(doc: Dict[str, Any]) -> int:
    """The account's level, derived from its OWN stored total_xp through the
    one shared curve. Never from anything the request carried."""
    total_xp = _int(_stats_of(doc).get("total_xp"))
    if _level_for_xp is None:
        return 1
    try:
        level, _cur, _goal = _level_for_xp(total_xp)
        return max(1, int(level))
    except Exception:  # noqa: BLE001
        return 1


def _str_list(value: Any, pattern: "re.Pattern[str]") -> List[str]:
    """Normalise a Firestore array of asset paths, dropping anything that is
    not a well-formed path. A junk entry must never make the "first thing you
    don't own" search skip a real reward."""
    if not isinstance(value, (list, tuple)):
        return []
    out: List[str] = []
    for item in value:
        s = str(item or "").strip().split("?")[0].lower()
        if pattern.match(s) and s not in out:
            out.append(s)
    return out


# ── Server-picked rewards ───────────────────────────────────────────────────
# A tier that hands over something already owned is a wasted reward, so the
# background and sticker tiers pick at claim time from what this account is
# actually missing — inside the transaction, off the freshly-read document.

def _pick_background(doc: Dict[str, Any]) -> Optional[str]:
    owned = set(_str_list(doc.get("unlocked_backgrounds"), _BG_PATH))
    for path in _background_paths:
        if path.lower() not in owned:
            return path
    return None


def _pick_sticker(doc: Dict[str, Any]) -> Optional[str]:
    """A sticker for a critter this account OWNS and has no sticker for yet.

    Mullet is every account's starter and is not written into unlocked_icons,
    so it is seeded in explicitly — otherwise a brand-new player's level-3
    sticker would have nothing to draw from and the tier would refuse."""
    owned = _str_list(doc.get("unlocked_icons"), _AVATAR_PATH)
    if "/avatars/mullet.png" not in owned:
        owned.insert(0, "/avatars/mullet.png")
    have = set(_str_list(doc.get("emote_icons"), _AVATAR_PATH))
    for path in owned:
        if path not in have:
            return path
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  STATE
# ═══════════════════════════════════════════════════════════════════════════
def _inventory(doc: Dict[str, Any]) -> Dict[str, Any]:
    now = _now_ms()
    boost_until = _int(doc.get("xp_boost_until"))
    return {
        "shields": max(0, _int(doc.get("streak_shields"))),
        "boosts": max(0, _int(doc.get("xp_boosts"))),
        "rerolls": max(0, _int(doc.get("weekly_reroll_tokens"))),
        "boostUntil": boost_until if boost_until > now else 0,
        "boostActive": boost_until > now,
        "boostPercent": BOOST_PERCENT,
        "rerollWeek": _int(doc.get("weekly_reroll_week")),
        "coins": max(0, _int(_stats_of(doc).get("critter_coins"))),
    }


def claimed_ids(db, uid: str) -> List[str]:
    """Which tiers this account has already taken. One query, not one read per
    tier — the track is ~60 tiers and this runs on every page open."""
    if not _SAFE_ID.match(str(uid or "")):
        return []
    out: List[str] = []
    try:
        for snap in _ledger(db).where("uid", "==", uid).stream():
            rec = snap.to_dict() or {}
            tier = str(rec.get("tier") or "")
            if tier and tier not in out:
                out.append(tier)
    except Exception as exc:  # noqa: BLE001
        # A lookup that fails must read as "nothing claimed yet", never as
        # "everything claimed" — the claim itself re-checks the ledger inside
        # its transaction, so the worst case is a button that looks available
        # and then politely says it was already taken.
        print(f"[pass] claimed lookup failed for {uid}: {exc}")
    return out


def state_payload(uid: Optional[str]) -> Dict[str, Any]:
    """Everything the Level Pass page paints itself from."""
    out: Dict[str, Any] = {
        "ok": True,
        "track": track(),
        "maxLevel": max_level(),
        # The cumulative XP to REACH each level, index 0 = level 1. Served so
        # the pass and the gallery's level track can say "12,400 XP until the
        # Manta Ray" from the same numbers the server levels people up with.
        "levelTotals": list(_level_totals),
        "boostPercent": BOOST_PERCENT,
        "boostHours": BOOST_HOURS,
        "signedIn": bool(uid),
        "level": 1,
        "totalXp": 0,
        "xpIntoLevel": 0,
        "xpForLevel": 0,
        "claimed": [],
        "inventory": {"shields": 0, "boosts": 0, "rerolls": 0, "boostUntil": 0,
                      "boostActive": False, "boostPercent": BOOST_PERCENT,
                      "rerollWeek": 0, "coins": 0},
    }
    if not uid:
        return out
    db = _get_firestore() if _get_firestore else None
    if db is None:
        return out
    try:
        snap = _users(db).document(uid).get()
        doc = (snap.to_dict() or {}) if snap.exists else {}
        total_xp = _int(_stats_of(doc).get("total_xp"))
        level, into, goal = (_level_for_xp(total_xp) if _level_for_xp else (1, 0, 1))
        out["level"] = max(1, int(level))
        out["totalXp"] = total_xp
        out["xpIntoLevel"] = int(into)
        out["xpForLevel"] = int(goal)
        out["claimed"] = claimed_ids(db, uid)
        out["inventory"] = _inventory(doc)
    except Exception as exc:  # noqa: BLE001
        print(f"[pass] state failed for {uid}: {exc}")
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  CLAIM
# ═══════════════════════════════════════════════════════════════════════════
def claim(db, uid: str, tier_id: str) -> Dict[str, Any]:
    """Pay one tier, exactly once, and only if the level is really reached.

    Firestore requires every read before every write, so the document and the
    ledger entry are both read up front. The ledger create() at the end is what
    makes a double-tap impossible."""
    if not _SAFE_ID.match(str(uid or "")):
        return {"ok": False, "error": "bad_request"}
    spec = _TRACK_BY_ID.get(str(tier_id or ""))
    if not spec:
        return {"ok": False, "error": "unknown_tier"}
    rtype = str(spec["type"])
    if rtype not in CLAIMABLE_TYPES:
        return {"ok": False, "error": "not_claimable"}

    need_level = int(spec["level"])
    amount = int(spec.get("amount") or 1)
    transactional = _transactional()
    user_ref = _users(db).document(uid)
    claim_ref = _ledger(db).document(f"{uid}__{tier_id}")
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        prev = claim_ref.get(transaction=t)
        user_snap = user_ref.get(transaction=t)

        if prev.exists:
            return {"ok": False, "error": "already_claimed"}
        if not user_snap.exists:
            return {"ok": False, "error": "no_account"}

        doc = user_snap.to_dict() or {}
        level = _level_of(doc)
        if level < need_level:
            # The one check that makes the whole feature safe: the level came
            # off this account's own total_xp a few lines ago, not off the
            # request. A tampered client gets this, every time.
            return {"ok": False, "error": "level_locked", "level": level, "need": need_level}

        update: Dict[str, Any] = {}
        granted: Dict[str, Any] = {"type": rtype, "amount": amount}

        if rtype == "coins":
            before = max(0, _int(_stats_of(doc).get("critter_coins")))
            after = before + amount
            update["stats"] = {"critter_coins": after}
            granted.update({"coins": amount, "coinsTotal": after})

        elif rtype == "shield":
            held = max(0, _int(doc.get("streak_shields")))
            if held >= MAX_SHIELDS:
                return {"ok": False, "error": "shields_full", "max": MAX_SHIELDS}
            after = min(MAX_SHIELDS, held + amount)
            update["streak_shields"] = after
            granted.update({"held": after})

        elif rtype == "boost":
            held = max(0, _int(doc.get("xp_boosts")))
            if held >= MAX_BOOSTS:
                return {"ok": False, "error": "boosts_full", "max": MAX_BOOSTS}
            after = min(MAX_BOOSTS, held + amount)
            update["xp_boosts"] = after
            granted.update({"held": after})

        elif rtype == "reroll":
            held = max(0, _int(doc.get("weekly_reroll_tokens")))
            if held >= MAX_REROLLS:
                return {"ok": False, "error": "rerolls_full", "max": MAX_REROLLS}
            after = min(MAX_REROLLS, held + amount)
            update["weekly_reroll_tokens"] = after
            granted.update({"held": after})

        elif rtype == "background":
            path = _pick_background(doc)
            if not path:
                # Nothing left to give. Refuse WITHOUT writing a ledger entry,
                # so the tier stays claimable if a future release adds more
                # backgrounds — a reward that silently evaporates is worse than
                # one that waits.
                return {"ok": False, "error": "backgrounds_full"}
            update["unlocked_backgrounds"] = _array_union()([path])
            granted.update({"path": path})

        elif rtype == "sticker":
            path = _pick_sticker(doc)
            if not path:
                return {"ok": False, "error": "stickers_full"}
            update["emote_icons"] = _array_union()([path])
            granted.update({"path": path})

        now = time.time()
        entry = {
            "uid": uid,
            "tier": tier_id,
            "level": need_level,
            "type": rtype,
            "amount": amount,
            "granted": {k: v for k, v in granted.items() if k != "type"},
            "username": str(doc.get("nickname") or doc.get("username") or ""),
            "at": now,
            "at_iso": _iso(now),
        }

        # merge=True so the coin write never clobbers the rest of `stats`.
        t.set(user_ref, update, merge=True)
        # Inside the transaction: the reward cannot exist without this, and
        # this cannot exist without the reward.
        t.create(claim_ref, entry)
        return {"ok": True, "tier": tier_id, "level": need_level, "granted": granted}

    try:
        return _run(txn)
    except Exception as exc:  # noqa: BLE001
        # A create() collision is the guard working — another request won the
        # race. Every other failure wrote nothing, and must NOT be reported as
        # "already claimed", or the player believes they were paid and never
        # tries again.
        if type(exc).__name__ == "AlreadyExists":
            return {"ok": False, "error": "already_claimed"}
        import traceback
        print(f"[pass] claim {tier_id} failed for {uid}: {exc}\n{traceback.format_exc(limit=4)}")
        return {"ok": False, "error": "server_error"}


def claim_all(db, uid: str) -> Dict[str, Any]:
    """Claim every unlocked, unclaimed tier.

    Deliberately a LOOP of single claims rather than one giant transaction:
    each tier keeps its own ledger doc and its own all-or-nothing guarantee, so
    a tier that refuses (a full shield hoard, no backgrounds left) stops itself
    and leaves every other payout intact. A partial result is reported honestly
    instead of rolling back rewards that were legitimately earned."""
    if not _SAFE_ID.match(str(uid or "")):
        return {"ok": False, "error": "bad_request"}
    try:
        snap = _users(db).document(uid).get()
        if not snap.exists:
            return {"ok": False, "error": "no_account"}
        level = _level_of(snap.to_dict() or {})
    except Exception as exc:  # noqa: BLE001
        print(f"[pass] claim_all level read failed for {uid}: {exc}")
        return {"ok": False, "error": "server_error"}

    already = set(claimed_ids(db, uid))
    results: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for spec in _TRACK_SPEC:
        tid = _tier_id(spec)
        if str(spec["type"]) not in CLAIMABLE_TYPES:
            continue
        if tid in already or int(spec["level"]) > level:
            continue
        res = claim(db, uid, tid)
        if res.get("ok"):
            results.append(res)
        elif res.get("error") != "already_claimed":
            skipped.append({"tier": tid, "error": str(res.get("error") or "error")})
    return {"ok": True, "claimed": results, "skipped": skipped, "count": len(results)}


# ═══════════════════════════════════════════════════════════════════════════
#  CONSUMABLES
# ═══════════════════════════════════════════════════════════════════════════
def activate_boost(db, uid: str) -> Dict[str, Any]:
    """Spend one held boost and start the 24 hours.

    Held separately from claiming so nobody burns a boost the moment they claim
    it. Refuses while one is already running — stacking two boosts into 48
    hours is not what the tier promises, and silently eating the second one is
    worse than saying no."""
    if not _SAFE_ID.match(str(uid or "")):
        return {"ok": False, "error": "bad_request"}
    transactional = _transactional()
    user_ref = _users(db).document(uid)
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        snap = user_ref.get(transaction=t)
        if not snap.exists:
            return {"ok": False, "error": "no_account"}
        doc = snap.to_dict() or {}
        now = _now_ms()
        current = _int(doc.get("xp_boost_until"))
        if current > now:
            return {"ok": False, "error": "boost_running", "until": current}
        held = max(0, _int(doc.get("xp_boosts")))
        if held < 1:
            return {"ok": False, "error": "no_boost"}
        until = now + BOOST_MS
        t.set(user_ref, {"xp_boosts": held - 1, "xp_boost_until": until}, merge=True)
        return {"ok": True, "until": until, "held": held - 1,
                "percent": BOOST_PERCENT, "hours": BOOST_HOURS}

    try:
        return _run(txn)
    except Exception as exc:  # noqa: BLE001
        print(f"[pass] boost activate failed for {uid}: {exc}")
        return {"ok": False, "error": "server_error"}


# A week start the client sends must look like a real Monday-midnight near now.
# Weekly state is local-time on the client, so the server cannot compute this
# itself — but it can refuse an obviously invented one.
_WEEK_MS = 7 * 24 * 60 * 60 * 1000
_WEEK_SLACK_MS = 8 * 24 * 60 * 60 * 1000


def activate_reroll(db, uid: str, week_start_ms: Any) -> Dict[str, Any]:
    """Spend one Weekly Swap token: unlimited weekly-challenge swaps for the
    rest of that week.

    The week is identified by the client's own local Monday-midnight, because
    that is what the weekly challenges themselves roll on (_getThisMondayMidnight
    in preview-app.js). A value that is not within a week or so of now is
    refused, so a token cannot be parked on some far-future week."""
    if not _SAFE_ID.match(str(uid or "")):
        return {"ok": False, "error": "bad_request"}
    week = _int(week_start_ms, -1)
    now = _now_ms()
    if week <= 0 or abs(now - week) > _WEEK_SLACK_MS:
        return {"ok": False, "error": "bad_week"}

    transactional = _transactional()
    user_ref = _users(db).document(uid)
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        snap = user_ref.get(transaction=t)
        if not snap.exists:
            return {"ok": False, "error": "no_account"}
        doc = snap.to_dict() or {}
        if _int(doc.get("weekly_reroll_week")) == week:
            # Already unlimited this week — charging a second token for
            # something they already have is the one outcome to avoid.
            return {"ok": False, "error": "already_active", "week": week}
        held = max(0, _int(doc.get("weekly_reroll_tokens")))
        if held < 1:
            return {"ok": False, "error": "no_token"}
        t.set(user_ref, {"weekly_reroll_tokens": held - 1,
                         "weekly_reroll_week": week}, merge=True)
        return {"ok": True, "week": week, "held": held - 1}

    try:
        return _run(txn)
    except Exception as exc:  # noqa: BLE001
        print(f"[pass] reroll activate failed for {uid}: {exc}")
        return {"ok": False, "error": "server_error"}


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════════════════════
ERROR_MESSAGES = {
    "unauthorized": "Sign in to use the Level Pass.",
    "no_account": "We couldn't find your account — try signing in again.",
    "unknown_tier": "That reward isn't on the Level Pass.",
    "not_claimable": "That milestone unlocks on its own — there's nothing to claim.",
    "already_claimed": "You've already claimed that reward.",
    "level_locked": "You haven't reached that level yet.",
    "shields_full": "Your Streak Shields are full — spend one first.",
    "boosts_full": "You're holding as many XP Boosts as you can — use one first.",
    "rerolls_full": "You're holding as many Weekly Swaps as you can — use one first.",
    "backgrounds_full": "You already own every background. This one is waiting for the next batch.",
    "stickers_full": "You already have a sticker for every critter you own.",
    "boost_running": "An XP Boost is already running.",
    "no_boost": "You don't have an XP Boost to activate.",
    "no_token": "You don't have a Weekly Swap token.",
    "already_active": "Weekly Swaps are already unlimited for you this week.",
    "bad_week": "Couldn't work out which week that is — reload and try again.",
    "bad_request": "Something was wrong with that request. Nothing was claimed.",
    "firestore_unavailable": "Couldn't reach your account just now. Nothing was claimed.",
    "server_error": "Something went wrong. Nothing was claimed — please try again.",
}


def message_for(result: Dict[str, Any]) -> str:
    return ERROR_MESSAGES.get(str(result.get("error") or ""), ERROR_MESSAGES["server_error"])


def _auth_uid(body: Dict[str, Any]) -> Optional[str]:
    tok = body.get("idToken") if isinstance(body.get("idToken"), str) else ""
    claims = _verify_token(tok) if (tok and _verify_token) else None
    return claims.get("uid") if claims and claims.get("uid") else None


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    """POST /api/pass/state            the track + this account's progress
       POST /api/pass/claim            claim one tier   { tier }
       POST /api/pass/claim-all        claim everything unlocked
       POST /api/pass/boost            start a held 24h XP boost
       POST /api/pass/reroll           spend a Weekly Swap token { weekStartMs }
    """
    path = parsed.path
    if not path.startswith("/api/pass/"):
        return False
    action = path[len("/api/pass/"):]

    # Readable signed-out: the track is a public reward catalogue, and the
    # reply carries no account data without a token.
    if action == "state":
        handler._send_json(state_payload(_auth_uid(body)))
        return True

    uid = _auth_uid(body)
    if not uid:
        handler._send_json({"ok": False, "error": "unauthorized"}, status=401)
        return True
    db = _get_firestore() if _get_firestore else None
    if db is None:
        handler._send_json({"ok": False, "error": "firestore_unavailable"})
        return True

    if action == "claim":
        tier = body.get("tier") if isinstance(body.get("tier"), str) else ""
        res = claim(db, uid, tier)
        if res.get("ok"):
            res["inventory"] = state_payload(uid).get("inventory")
        else:
            res["message"] = message_for(res)
        handler._send_json(res)
        return True

    if action == "claim-all":
        res = claim_all(db, uid)
        if res.get("ok"):
            res["inventory"] = state_payload(uid).get("inventory")
        else:
            res["message"] = message_for(res)
        handler._send_json(res)
        return True

    if action == "boost":
        res = activate_boost(db, uid)
        if not res.get("ok"):
            res["message"] = message_for(res)
        handler._send_json(res)
        return True

    if action == "reroll":
        res = activate_reroll(db, uid, body.get("weekStartMs"))
        if not res.get("ok"):
            res["message"] = message_for(res)
        handler._send_json(res)
        return True

    handler._send_json({"ok": False, "error": "unknown_action"}, status=404)
    return True
