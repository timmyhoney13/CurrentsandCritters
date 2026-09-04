"""Currents and Critters, the Critter Pass (server-authoritative).

The PAID track that sits under the free Level Pass. Both are laid over the same
1-100 level curve; the difference is that this one has to be bought once, for
CRITTER_PASS_PRICE Critter Coins, and pays far more back. Wired additively into
multiplayer_server exactly like level_pass_server / clan_server / prestige_server:

    import critter_pass_server
    critter_pass_server.init(get_firestore=..., verify_token=...,
                             level_for_xp=..., level_totals=...,
                             background_paths=...)               # in main()
    if critter_pass_server.handle_post(self, parsed, body): ...   # in do_POST

This is the "premium row" level_pass_server.py's docstring anticipated: every
Level Pass tier carries row "free" so that a paid track would be one entitlement
check away. That check is _owns_pass() below, and it is the only structural
difference between the two files.

WHY IT IS A ONE-TIME PURCHASE AND NOT A REBUYABLE SEASON
The track is keyed on the account's LIFETIME level, not on XP earned inside a
season, because that is the only level this game stores. A pass that could be
re-bought each season would therefore pay a level-100 player the whole 8,500
coins for 4,000, again, every season: a coin printer, not a battle pass. So the
purchase is once, ever, per SEASON_ID, and SEASON_ID is a constant that only
moves when a genuinely new track ships beside this one. Everything (the price,
the ledger ids, the ownership array) is already keyed by it, so Season 2 is a
new constant and a new _TRACK_SPEC, not a rewrite.

WHY THE SERVER OWNS THIS
Same reason as the Level Pass, doubled: 8,500 Critter Coins, 23,750 XP, extra
challenge slots and a 2,000-coin avatar are on this track. The account's level
is RE-DERIVED here from `stats.total_xp` on its own document, inside the same
transaction that writes the reward, and the purchase re-reads the coin balance
inside its own transaction. Nothing the browser sends can move a payout.

EXACTLY ONCE, EVER
Two ledgers, both written with create() INSIDE the transaction they guard:
  critter_pass_purchases/{uid}__{season}          the 4,000-coin purchase
  critter_pass_claims/{uid}__{season}_{tierId}    one per tier
A create() collision is the guard working; every other failure wrote nothing
and must NOT be reported as "already done", or the player believes they were
paid and never retries.

THE ONE REWARD THAT IS NOT A THING YOU HOLD
`daily_slot` / `weekly_slot` raise `bonus_daily_slots` / `bonus_weekly_slots` on
the account. The challenges themselves live in the browser's localStorage (they
roll on the player's own local midnight, which no server can compute), so the
client reads these two numbers and grows its own slot array. They are the count
of EXTRA slots, never the total: a client that cannot reach the server falls
back to the base three, which is the safe direction.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

# The free pass owns the consumable hoard caps and the XP-boost numbers. Both
# passes pay into the SAME `streak_shields` / `xp_boosts` /
# `weekly_reroll_tokens` fields, so a second copy of those caps here would let
# the two disagree about what "full" means, and the sidebar badge reads the
# caps off the state payload to decide whether a tier is actionable at all.
import level_pass_server as _lp

# ── Injected by init() (no circular import with multiplayer_server) ──────────
_get_firestore: Optional[Callable[[], Any]] = None
_verify_token: Optional[Callable[[str], Optional[dict]]] = None
_level_for_xp: Optional[Callable[[Any], Any]] = None
_level_totals: List[int] = []
_background_paths: List[str] = []


def init(*, get_firestore, verify_token, level_for_xp, level_totals,
         background_paths) -> None:
    """Same injection as the Level Pass, and for the same reason: this module
    owns no level curve of its own. `level_for_xp` is
    multiplayer_server._level_progress_for_total_xp and `level_totals` is its
    LEVEL_XP_TOTALS. A third (now fourth) copy of that table is exactly the
    drift that demotes live players.

    The table is SERVED to the client too, so "how much XP until level 72?" is
    answered from the same numbers the server grants levels from."""
    global _get_firestore, _verify_token, _level_for_xp, _level_totals, _background_paths
    _get_firestore = get_firestore
    _verify_token = verify_token
    _level_for_xp = level_for_xp
    _level_totals = [int(n) for n in (level_totals or [])]
    _background_paths = [str(p) for p in (background_paths or [])]


# ═══════════════════════════════════════════════════════════════════════════
#  THE SEASON, THE PRICE, THE BUDGETS
# ═══════════════════════════════════════════════════════════════════════════
# Every ledger id and the ownership array are keyed by this, so a future track
# is a new constant plus a new _TRACK_SPEC and nothing else. Changing it while
# THIS track is live would re-open every tier and re-charge every owner, so it
# is append-only in practice.
SEASON_ID = "S1"
SEASON_NAME = "Season 1: Kelp Forest"

# HOW LONG A SEASON LASTS, and the one thing the clock deliberately does NOT do.
# A season runs SEASON_DAYS from SEASON_STARTED_AT and the page counts down to
# the end of it. The countdown is a DISPLAY, not a trigger: when it reaches zero
# this module keeps serving Season 1 until somebody ships Season 2 by moving
# SEASON_ID and SEASON_STARTED_AT together. That is on purpose, for the reason
# in the docstring above: rotating SEASON_ID re-opens all 100 tiers, so a
# level-100 account would re-buy the track for 4,000 and take 8,500 straight
# back out. A calendar must never be able to do that on its own.
#
# Both are UTC, and the start is the day the track shipped, so the number the
# page prints is the real remaining time and not an offset from whenever a
# server last restarted.
SEASON_DAYS = 30
SEASON_STARTED_AT = datetime(2026, 9, 4, tzinfo=timezone.utc)
SEASON_ENDS_AT = SEASON_STARTED_AT + timedelta(days=SEASON_DAYS)


def season_window(now: Optional[datetime] = None) -> Dict[str, Any]:
    """The season clock, as the page paints it.

    `daysLeft` is rounded UP, because "1 day left" has to keep saying that for
    the whole of the last day; only a season that is actually over reports 0.
    `over` is the honest flag: the track stays claimable either way, so nothing
    downstream may treat it as a lock.
    """
    now = now or datetime.now(timezone.utc)
    secs = int((SEASON_ENDS_AT - now).total_seconds())
    return {
        "seasonDays": SEASON_DAYS,
        "seasonStartsAt": int(SEASON_STARTED_AT.timestamp() * 1000),
        "seasonEndsAt": int(SEASON_ENDS_AT.timestamp() * 1000),
        "seasonSecondsLeft": max(0, secs),
        "seasonDaysLeft": max(0, -(-secs // 86400)),
        "seasonOver": secs <= 0,
    }

# What the pass costs, once. Tim's number, and the reason
# test_the_pass_costs_exactly_the_asking_price pins it as an equality.
CRITTER_PASS_PRICE = 4000

# What a fully-claimed track pays back. Both are pinned by equality tests: they
# are promises printed on the page, not dials somebody can nudge in a rebalance
# without noticing the page now lies.
TRACK_COIN_BUDGET = 8500     # 4,000 in → 8,500 back
TRACK_XP_BUDGET = 23750

# The extra challenge slots. Three tiers grant a daily each and three grant a
# weekly each, so the cap IS the number of tiers: the clamp exists so a future
# retune that adds a fourth tier cannot quietly hand out a fourth slot the
# client's own clamp would then refuse to draw.
MAX_EXTRA_DAILY = 3
MAX_EXTRA_WEEKLY = 3

# The level-100 prize. It is also a 2,000-coin Store skin (unlock.type "shop" in
# preview-app.js), so this is a second route to it, not a second copy: the grant
# is an ArrayUnion into the same `unlocked_icons` the Store writes, which makes
# claiming it twice a no-op rather than a duplicate.
FINALE_AVATAR = "/avatars/summer-skin-gull.png"
FINALE_AVATAR_NAME = "Summer Skin Gull"

# Read straight off the free pass so the boost a Critter Pass tier hands over is
# the SAME boost, with the same percentage and the same 24 hours, as the one the
# Level Pass hands over. They stack into one `xp_boosts` hoard.
BOOST_PERCENT = _lp.BOOST_PERCENT
BOOST_HOURS = _lp.BOOST_HOURS
MAX_SHIELDS = _lp.MAX_SHIELDS
MAX_BOOSTS = _lp.MAX_BOOSTS
MAX_REROLLS = _lp.MAX_REROLLS


# ═══════════════════════════════════════════════════════════════════════════
#  THE TRACK
# ═══════════════════════════════════════════════════════════════════════════
# ONE REWARD ON EVERY LEVEL: 100 tiers over 100 levels, nothing skipped. Laid
# out on a single repeating decade rule, so a player can predict what is coming
# without reading the whole rail. By the level's last digit:
#
#   1, 2, 4, 6, 8   Critter Coins   53 tiers, 8,500 coins EXACTLY
#   5 and 0         an XP drop      19 tiers, one every 5 levels
#   7               a chat emote    10 tiers, one every 10 levels
#   3 and 9         a PERK          20 slots, see the table below
#   100             the Summer Skin Gull
#
# The COIN RAMP is per decade: 25, 50, 75, 100, 125, 150, 175, 200, 225, 250,
# five tiers of it in each decade. That is 6,875; the remaining 1,625 sits on
# three perk slots turned into milestone payouts (L29 225, L59 400, L99 1,000),
# so the run into the Level 100 critter pays like the end of a track.
#
# The XP DROP IS A FORMULA, not a table: 25 XP per level, every 5 levels. So
# level 5 pays 125 and level 95 pays 2,375, and the whole thing sums to 23,750
# on its own. (It was 20/level for 19,000 and was raised, once: the drops were
# paying a smaller share of the climb than the track looked like it did.) A drop
# is worth roughly a third to two thirds of the level it lands on, at every
# point on the curve, which is what keeps it feeling the same at level 90 as at
# level 10. Raising this rule is the ONLY way to move the XP on this track:
# hand-editing one tier puts the table and TRACK_XP_BUDGET out of step, and
# test_the_xp_drops_follow_the_formula fails on exactly that.
#
# THE PERK SLOTS ARE COUNTED AGAINST THE HOARD CAPS, NOT SPRINKLED.
# There are exactly MAX_BOOSTS boost tiers, MAX_SHIELDS shield tiers and
# MAX_REROLLS swap tiers, because a fourth of any of them could never be
# claimed in one sweep: the hoard would already be full and the tier would
# refuse. Three each is the most a player can hold, so "Claim all" on a maxed
# pass clears the whole track instead of always reporting a skip.
#
# Backgrounds stop at 2 for a related reason: there are 8 in the whole game and
# the Level Pass and the referral reward pay out of the same pool, so a third
# would mostly find nothing left to give.
#
# type:
#   coins        → Critter Coins                    (amount)
#   xp           → an XP drop, paid straight onto stats.total_xp (amount)
#   emote        → one critter chat emote, server-picked from critters you own
#   shield       → Streak Shields                    (amount)
#   boost        → one 24-hour +20% XP boost, held until you activate it
#   swap         → one Weekly Swap token
#   background   → one avatar background, server-picked from unowned
#   daily_slot   → +1 daily challenge slot, for good
#   weekly_slot  → +1 weekly challenge slot, for good
#   avatar       → the finale critter, granted into unlocked_icons
_TRACK_SPEC: Sequence[Dict[str, Any]] = (
    {"level": 1,  "type": "coins",      "amount": 25},
    {"level": 2,  "type": "coins",      "amount": 25},
    {"level": 3,  "type": "boost",      "amount": 1},
    {"level": 4,  "type": "coins",      "amount": 25},
    {"level": 5,  "type": "xp",         "amount": 125},
    {"level": 6,  "type": "coins",      "amount": 25},
    {"level": 7,  "type": "emote",      "amount": 1},
    {"level": 8,  "type": "coins",      "amount": 25},
    {"level": 9,  "type": "shield",     "amount": 1},
    {"level": 10, "type": "xp",         "amount": 250},
    {"level": 11, "type": "coins",      "amount": 50},
    {"level": 12, "type": "coins",      "amount": 50},
    {"level": 13, "type": "daily_slot", "amount": 1},
    {"level": 14, "type": "coins",      "amount": 50},
    {"level": 15, "type": "xp",         "amount": 375},
    {"level": 16, "type": "coins",      "amount": 50},
    {"level": 17, "type": "emote",      "amount": 1},
    {"level": 18, "type": "coins",      "amount": 50},
    {"level": 19, "type": "swap",       "amount": 1},
    {"level": 20, "type": "xp",         "amount": 500},
    {"level": 21, "type": "coins",      "amount": 75},
    {"level": 22, "type": "coins",      "amount": 75},
    {"level": 23, "type": "weekly_slot","amount": 1},
    {"level": 24, "type": "coins",      "amount": 75},
    {"level": 25, "type": "xp",         "amount": 625},
    {"level": 26, "type": "coins",      "amount": 75},
    {"level": 27, "type": "emote",      "amount": 1},
    {"level": 28, "type": "coins",      "amount": 75},
    {"level": 29, "type": "coins",      "amount": 225},
    {"level": 30, "type": "xp",         "amount": 750},
    {"level": 31, "type": "coins",      "amount": 100},
    {"level": 32, "type": "coins",      "amount": 100},
    {"level": 33, "type": "boost",      "amount": 1},
    {"level": 34, "type": "coins",      "amount": 100},
    {"level": 35, "type": "xp",         "amount": 875},
    {"level": 36, "type": "coins",      "amount": 100},
    {"level": 37, "type": "emote",      "amount": 1},
    {"level": 38, "type": "coins",      "amount": 100},
    {"level": 39, "type": "shield",     "amount": 1},
    {"level": 40, "type": "xp",         "amount": 1000},
    {"level": 41, "type": "coins",      "amount": 125},
    {"level": 42, "type": "coins",      "amount": 125},
    {"level": 43, "type": "daily_slot", "amount": 1},
    {"level": 44, "type": "coins",      "amount": 125},
    {"level": 45, "type": "xp",         "amount": 1125},
    {"level": 46, "type": "coins",      "amount": 125},
    {"level": 47, "type": "emote",      "amount": 1},
    {"level": 48, "type": "coins",      "amount": 125},
    {"level": 49, "type": "background", "amount": 1},
    {"level": 50, "type": "xp",         "amount": 1250},
    {"level": 51, "type": "coins",      "amount": 150},
    {"level": 52, "type": "coins",      "amount": 150},
    {"level": 53, "type": "weekly_slot","amount": 1},
    {"level": 54, "type": "coins",      "amount": 150},
    {"level": 55, "type": "xp",         "amount": 1375},
    {"level": 56, "type": "coins",      "amount": 150},
    {"level": 57, "type": "emote",      "amount": 1},
    {"level": 58, "type": "coins",      "amount": 150},
    {"level": 59, "type": "coins",      "amount": 400},
    {"level": 60, "type": "xp",         "amount": 1500},
    {"level": 61, "type": "coins",      "amount": 175},
    {"level": 62, "type": "coins",      "amount": 175},
    {"level": 63, "type": "swap",       "amount": 1},
    {"level": 64, "type": "coins",      "amount": 175},
    {"level": 65, "type": "xp",         "amount": 1625},
    {"level": 66, "type": "coins",      "amount": 175},
    {"level": 67, "type": "emote",      "amount": 1},
    {"level": 68, "type": "coins",      "amount": 175},
    {"level": 69, "type": "shield",     "amount": 1},
    {"level": 70, "type": "xp",         "amount": 1750},
    {"level": 71, "type": "coins",      "amount": 200},
    {"level": 72, "type": "coins",      "amount": 200},
    {"level": 73, "type": "daily_slot", "amount": 1},
    {"level": 74, "type": "coins",      "amount": 200},
    {"level": 75, "type": "xp",         "amount": 1875},
    {"level": 76, "type": "coins",      "amount": 200},
    {"level": 77, "type": "emote",      "amount": 1},
    {"level": 78, "type": "coins",      "amount": 200},
    {"level": 79, "type": "boost",      "amount": 1},
    {"level": 80, "type": "xp",         "amount": 2000},
    {"level": 81, "type": "coins",      "amount": 225},
    {"level": 82, "type": "coins",      "amount": 225},
    {"level": 83, "type": "weekly_slot","amount": 1},
    {"level": 84, "type": "coins",      "amount": 225},
    {"level": 85, "type": "xp",         "amount": 2125},
    {"level": 86, "type": "coins",      "amount": 225},
    {"level": 87, "type": "emote",      "amount": 1},
    {"level": 88, "type": "coins",      "amount": 225},
    {"level": 89, "type": "background", "amount": 1},
    {"level": 90, "type": "xp",         "amount": 2250},
    {"level": 91, "type": "coins",      "amount": 250},
    {"level": 92, "type": "coins",      "amount": 250},
    {"level": 93, "type": "swap",       "amount": 1},
    {"level": 94, "type": "coins",      "amount": 250},
    {"level": 95, "type": "xp",         "amount": 2375},
    {"level": 96, "type": "coins",      "amount": 250},
    {"level": 97, "type": "emote",      "amount": 1},
    {"level": 98, "type": "coins",      "amount": 250},
    {"level": 99, "type": "coins",      "amount": 1000},
    {"level": 100, "type": "avatar",      "amount": 1,
     "critter": FINALE_AVATAR_NAME, "img": FINALE_AVATAR},
)

# How each reward type describes itself. One place, so the tier card, the claim
# toast and the ledger entry can never word the same reward three ways.
_TYPE_META: Dict[str, Dict[str, str]] = {
    # A text fallback only: the client paints coin tiers with the minted
    # critter-coin.png, the same coin the Store and the wallet chip show.
    "coins":       {"icon": "🪙", "name": "Critter Coins"},
    "xp":          {"icon": "✨", "name": "XP Drop"},
    "emote":       {"icon": "😀", "name": "Chat Emote"},
    "shield":      {"icon": "🛡️", "name": "Streak Shield"},
    "boost":       {"icon": "⚡", "name": f"{BOOST_HOURS}h XP Boost"},
    "swap":        {"icon": "🔄", "name": "Weekly Swap"},
    "background":  {"icon": "🖼️", "name": "Avatar Background"},
    "daily_slot":  {"icon": "📅", "name": "Extra Daily Challenge"},
    "weekly_slot": {"icon": "🗝️", "name": "Extra Weekly Challenge"},
    "avatar":      {"icon": "⭐", "name": "Critter"},
}

# Everything on this track is claimed here. Unlike the Level Pass, there are no
# showcase-only tiers: the finale avatar is a Store skin, not a level-gated
# critter, so granting it cannot re-open the trading system's re-earn rule.
CLAIMABLE_TYPES = frozenset({
    "coins", "xp", "emote", "shield", "boost", "swap", "background",
    "daily_slot", "weekly_slot", "avatar",
})

# The types that raise a counter the CLIENT then has to mirror. Kept as a set so
# the state payload and the claim path cannot disagree about which they are.
SLOT_TYPES = frozenset({"daily_slot", "weekly_slot"})


def _tier_id(spec: Dict[str, Any]) -> str:
    """Stable identifier for a tier, and the name of its ledger document (with
    the season). Every level carries exactly one reward now, so the level alone
    IS unique; `key` stays because a future track that doubles a level would
    otherwise silently collide two tiers onto one ledger row. Changing a tier id
    re-opens that tier for claiming, so they are append-only in practice."""
    extra = str(spec.get("key") or "")
    return f"L{int(spec['level'])}" + (f"_{extra}" if extra else "")


def _ledger_id(uid: str, tier_id: str) -> str:
    return f"{uid}__{SEASON_ID}_{tier_id}"


def _describe(spec: Dict[str, Any]) -> str:
    t = str(spec.get("type"))
    n = int(spec.get("amount") or 1)
    if t == "coins":
        return f"{n:,} Critter Coins"
    if t == "xp":
        return f"{n:,} XP"
    if t == "emote":
        return "1 Chat Emote"
    if t == "shield":
        return f"{n} Streak Shield" + ("s" if n != 1 else "")
    if t == "boost":
        return f"{BOOST_HOURS}-Hour XP Boost (+{BOOST_PERCENT}%)"
    if t == "swap":
        return "Weekly Swap Token"
    if t == "background":
        return "1 Avatar Background"
    if t == "daily_slot":
        return "+1 Daily Challenge"
    if t == "weekly_slot":
        return "+1 Weekly Challenge"
    if t == "avatar":
        return str(spec.get("critter") or "Critter")
    return t


def _blurb(spec: Dict[str, Any]) -> str:
    """The one-line "what does this actually do" under a tier's name."""
    t = str(spec.get("type"))
    return {
        "coins":       "Spend them in the Store on skins, backgrounds and perks.",
        "xp":          "Paid straight onto your total, so it counts towards your next level right away.",
        "emote":       "A critter you own, sent as a picture in game chat.",
        "shield":      "Covers one missed day so your daily streak survives it.",
        "boost":       f"+{BOOST_PERCENT}% XP from everything for {BOOST_HOURS} hours: "
                       "games, challenges, achievements and the daily bonus. Activate it when you want it.",
        "swap":        "Swap out as many weekly challenges as you like for the rest of that week.",
        "background":  "An exclusive scene behind your avatar, everywhere it appears.",
        "daily_slot":  "One more daily challenge, every day, for keeps. More jobs, more XP.",
        "weekly_slot": "One more weekly challenge, every week, for keeps. More jobs, more XP.",
        "avatar":      "The Critter Pass critter. Equip it in the Avatar Gallery.",
    }.get(t, "")


def track() -> List[Dict[str, Any]]:
    """The published track. Pure data: safe to serve to anyone, signed in, out
    or not owning the pass, and the client renders straight from it so the two
    can never drift. A locked track that can be READ is the whole sales pitch."""
    out: List[Dict[str, Any]] = []
    for spec in _TRACK_SPEC:
        t = str(spec["type"])
        meta = _TYPE_META.get(t, {"icon": "🎁", "name": t})
        entry = {
            "id": _tier_id(spec),
            "level": int(spec["level"]),
            "row": "premium",
            "type": t,
            "amount": int(spec.get("amount") or 1),
            "icon": meta["icon"],
            "label": _describe(spec),
            "blurb": _blurb(spec),
            "claimable": t in CLAIMABLE_TYPES,
        }
        if t == "avatar":
            entry["critter"] = str(spec.get("critter") or "")
            entry["img"] = str(spec.get("img") or "")
        out.append(entry)
    return out


_TRACK_BY_ID: Dict[str, Dict[str, Any]] = {_tier_id(s): dict(s) for s in _TRACK_SPEC}


def max_level() -> int:
    return max(int(s["level"]) for s in _TRACK_SPEC)


def coin_total() -> int:
    """Every Critter Coin on the track. The page prints this number and the
    purchase card sells against it, so it is derived, never typed twice."""
    return sum(int(s.get("amount") or 0) for s in _TRACK_SPEC if s["type"] == "coins")


def xp_total() -> int:
    return sum(int(s.get("amount") or 0) for s in _TRACK_SPEC if s["type"] == "xp")


# ═══════════════════════════════════════════════════════════════════════════
#  FIRESTORE
# ═══════════════════════════════════════════════════════════════════════════
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_AVATAR_PATH = re.compile(r"^/avatars/[a-z0-9_-]+\.png$")
_BG_PATH = re.compile(r"^/backgrounds/[a-z0-9_-]+\.png$")


def _users(db):
    return db.collection("users")


def _ledger(db):
    return db.collection("critter_pass_claims")


def _purchases(db):
    return db.collection("critter_pass_purchases")


def _transactional():
    from firebase_admin import firestore
    fn = getattr(firestore, "transactional", None)
    if fn is None:
        from google.cloud.firestore_v1 import transactional as fn  # type: ignore
    return fn


def _array_union():
    """Appends to unlocked_backgrounds / unlocked_icons / emote_icons go through
    ArrayUnion, not through "read the list, add one, write it back". The reads
    below are NORMALISED (lowercased, junk dropped) so they can pick what is
    missing: writing that normalised copy back would quietly delete any entry
    the filter rejected."""
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
    """The account's level, derived from its OWN stored total_xp through the one
    shared curve. Never from anything the request carried."""
    total_xp = _int(_stats_of(doc).get("total_xp"))
    if _level_for_xp is None:
        return 1
    try:
        level, _cur, _goal = _level_for_xp(total_xp)
        return max(1, int(level))
    except Exception:  # noqa: BLE001
        return 1


def _owns_pass(doc: Dict[str, Any]) -> bool:
    """THE entitlement check, and the only structural difference between this
    file and level_pass_server.py. Read off the account document inside the
    same transaction as the payout, so a purchase refunded or a document rolled
    back cannot leave a claimable track behind it."""
    seasons = doc.get("critter_pass_seasons")
    if isinstance(seasons, (list, tuple)):
        return SEASON_ID in [str(s) for s in seasons]
    return False


def _vouchers_of(doc: Dict[str, Any]) -> int:
    """How many Critter Pass vouchers this account is holding.

    A voucher is the paid-tier way onto this track: one of them unlocks the
    pass for ONE season, whichever season its holder decides to spend it on, so
    a voucher kept past Season 1 still opens Season 2. It is a plain count on
    the account document (`critter_pass_vouchers`) rather than a per-season
    entitlement precisely so it can be held, saved, and traded away like any
    other balance."""
    return max(0, _int(doc.get("critter_pass_vouchers")))


def _str_list(value: Any, pattern: "re.Pattern[str]") -> List[str]:
    """Normalise a Firestore array of asset paths, dropping anything that is not
    a well-formed path. A junk entry must never make the "first thing you don't
    own" search skip a real reward."""
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
# background and emote tiers pick at claim time from what this account is
# actually missing: inside the transaction, off the freshly-read document.
# Deliberately NOT imported from level_pass_server: that module picks from ITS
# injected background list, and a shared private helper reading another module's
# global is the kind of coupling that breaks silently when one of them is
# re-inited. The two catalogues happen to be the same list today; they are
# passed in separately so they are allowed to stop being.

def _pick_background(doc: Dict[str, Any]) -> Optional[str]:
    owned = set(_str_list(doc.get("unlocked_backgrounds"), _BG_PATH))
    for path in _background_paths:
        if path.lower() not in owned:
            return path
    return None


def _pick_emote(doc: Dict[str, Any]) -> Optional[str]:
    """An emote for a critter this account OWNS and has no emote for yet.

    Mullet is every account's starter and is not written into unlocked_icons, so
    it is seeded in explicitly: otherwise a brand-new player's first emote tier
    would have nothing to draw from and the tier would refuse."""
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
        # The two numbers the challenge strip mirrors. Clamped on the way OUT as
        # well as on the way in: a document edited by hand must not be able to
        # ask the browser for nine daily challenges.
        "extraDaily": max(0, min(MAX_EXTRA_DAILY, _int(doc.get("bonus_daily_slots")))),
        "extraWeekly": max(0, min(MAX_EXTRA_WEEKLY, _int(doc.get("bonus_weekly_slots")))),
        # Season Pass vouchers in hand. The purchase card reads this to decide
        # whether it offers "Redeem Season Pass Voucher" or the coin price.
        "vouchers": _vouchers_of(doc),
    }


def claimed_ids(db, uid: str) -> List[str]:
    """Which tiers this account has already taken, this season. One query, not
    one read per tier: the track is 60 tiers and this runs on every page open."""
    if not _SAFE_ID.match(str(uid or "")):
        return []
    out: List[str] = []
    try:
        for snap in _ledger(db).where("uid", "==", uid).stream():
            rec = snap.to_dict() or {}
            if str(rec.get("season") or "") != SEASON_ID:
                continue
            tier = str(rec.get("tier") or "")
            if tier and tier not in out:
                out.append(tier)
    except Exception as exc:  # noqa: BLE001
        # A lookup that fails must read as "nothing claimed yet", never as
        # "everything claimed": the claim itself re-checks the ledger inside its
        # transaction, so the worst case is a button that looks available and
        # then politely says it was already taken.
        print(f"[critterpass] claimed lookup failed for {uid}: {exc}")
    return out


def state_payload(uid: Optional[str]) -> Dict[str, Any]:
    """Everything the Critter Pass page paints itself from."""
    out: Dict[str, Any] = {
        "ok": True,
        "track": track(),
        "maxLevel": max_level(),
        "seasonId": SEASON_ID,
        "seasonName": SEASON_NAME,
        "price": CRITTER_PASS_PRICE,
        # The 30-day clock. Served rather than computed in the browser so the
        # countdown cannot drift with a wrong device clock, and merged in whole
        # so a new field on the window reaches the page without a second edit.
        **season_window(),
        # Derived from the track, never typed twice: the purchase card sells
        # "4,000 in, 8,500 back" and both halves have to be the real numbers.
        "coinTotal": coin_total(),
        "xpTotal": xp_total(),
        "extraDailyMax": MAX_EXTRA_DAILY,
        "extraWeeklyMax": MAX_EXTRA_WEEKLY,
        "finaleAvatar": FINALE_AVATAR,
        "finaleAvatarName": FINALE_AVATAR_NAME,
        # The cumulative XP to REACH each level, index 0 = level 1. Served so
        # the pass can say "12,400 XP until the gull" from the same numbers the
        # server levels people up with.
        "levelTotals": list(_level_totals),
        # The hoard caps, served rather than copied into the client, so the
        # badge and the payout agree on what "full" means.
        "caps": {"shields": MAX_SHIELDS, "boosts": MAX_BOOSTS, "rerolls": MAX_REROLLS},
        "boostPercent": BOOST_PERCENT,
        "boostHours": BOOST_HOURS,
        "signedIn": bool(uid),
        "owned": False,
        "level": 1,
        "totalXp": 0,
        "xpIntoLevel": 0,
        "xpForLevel": 0,
        "claimed": [],
        "inventory": {"shields": 0, "boosts": 0, "rerolls": 0, "boostUntil": 0,
                      "boostActive": False, "boostPercent": BOOST_PERCENT,
                      "rerollWeek": 0, "coins": 0,
                      "extraDaily": 0, "extraWeekly": 0, "vouchers": 0},
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
        out["owned"] = _owns_pass(doc)
        out["inventory"] = _inventory(doc)
        # Only an owner has claims worth a query. A non-owner's list is empty by
        # construction (nothing can be claimed without the entitlement), so this
        # also saves a Firestore query on every page open before purchase.
        out["claimed"] = claimed_ids(db, uid) if out["owned"] else []
    except Exception as exc:  # noqa: BLE001
        print(f"[critterpass] state failed for {uid}: {exc}")
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  BUY
# ═══════════════════════════════════════════════════════════════════════════
def buy(db, uid: str, use_voucher: bool = False) -> Dict[str, Any]:
    """Unlock the track: either for CRITTER_PASS_PRICE Critter Coins, or for one
    Season Pass voucher.

    The balance (coins or vouchers) is RE-READ inside the transaction, so a
    client that thinks it can pay and cannot gets refused rather than credited.
    The purchase ledger is create()d in the same transaction, which is what
    makes two tabs double-tapping "Unlock" end with one charge and one loser.

    A voucher is spent the same way coins are, and against the SAME once-per-
    season ledger, so the two ways in can never both fire for one season. Which
    one was used is recorded on the purchase (`paid_with`) because they are
    worth different things to a refund."""
    if not _SAFE_ID.match(str(uid or "")):
        return {"ok": False, "error": "bad_request"}
    transactional = _transactional()
    user_ref = _users(db).document(uid)
    buy_ref = _purchases(db).document(f"{uid}__{SEASON_ID}")
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        prev = buy_ref.get(transaction=t)
        user_snap = user_ref.get(transaction=t)
        if prev.exists:
            return {"ok": False, "error": "already_owned"}
        if not user_snap.exists:
            return {"ok": False, "error": "no_account"}
        doc = user_snap.to_dict() or {}
        if _owns_pass(doc):
            # The array says yes but the ledger says no. Believe the array (the
            # player HAS the pass) and refuse rather than charge a second time.
            return {"ok": False, "error": "already_owned"}
        have = max(0, _int(_stats_of(doc).get("critter_coins")))
        vouchers = _vouchers_of(doc)
        now = time.time()
        if use_voucher:
            if vouchers < 1:
                return {"ok": False, "error": "no_vouchers"}
            after_v = vouchers - 1
            t.set(user_ref, {
                "critter_pass_vouchers": after_v,
                "critter_pass_seasons": _array_union()([SEASON_ID]),
            }, merge=True)
            t.create(buy_ref, {
                "uid": uid,
                "season": SEASON_ID,
                "price": 0,
                "paid_with": "voucher",
                "vouchers_before": vouchers,
                "vouchers_after": after_v,
                "coins_before": have,
                "coins_after": have,
                "username": str(doc.get("nickname") or doc.get("username") or ""),
                "at": now,
                "at_iso": _iso(now),
            })
            return {"ok": True, "season": SEASON_ID, "paid": 0,
                    "paidWith": "voucher", "coins": have, "vouchers": after_v}
        if have < CRITTER_PASS_PRICE:
            return {"ok": False, "error": "not_enough_coins",
                    "have": have, "need": CRITTER_PASS_PRICE,
                    "vouchers": vouchers}
        after = have - CRITTER_PASS_PRICE
        t.set(user_ref, {
            # merge=True, so the coin write never clobbers the rest of `stats`.
            "stats": {"critter_coins": after},
            "critter_pass_seasons": _array_union()([SEASON_ID]),
        }, merge=True)
        t.create(buy_ref, {
            "uid": uid,
            "season": SEASON_ID,
            "price": CRITTER_PASS_PRICE,
            "paid_with": "coins",
            "coins_before": have,
            "coins_after": after,
            "username": str(doc.get("nickname") or doc.get("username") or ""),
            "at": now,
            "at_iso": _iso(now),
        })
        return {"ok": True, "season": SEASON_ID, "paid": CRITTER_PASS_PRICE,
                "paidWith": "coins", "coins": after, "vouchers": vouchers}

    try:
        return _run(txn)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "AlreadyExists":
            return {"ok": False, "error": "already_owned"}
        import traceback
        print(f"[critterpass] buy failed for {uid}: {exc}\n{traceback.format_exc(limit=4)}")
        return {"ok": False, "error": "server_error"}


# ═══════════════════════════════════════════════════════════════════════════
#  CLAIM
# ═══════════════════════════════════════════════════════════════════════════
def claim(db, uid: str, tier_id: str) -> Dict[str, Any]:
    """Pay one tier, exactly once, and only to an owner who has really reached
    the level.

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
    claim_ref = _ledger(db).document(_ledger_id(uid, tier_id))
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
        if not _owns_pass(doc):
            # Read off the account inside the transaction, not off the request.
            return {"ok": False, "error": "not_owned"}
        level = _level_of(doc)
        if level < need_level:
            # The check that makes the whole feature safe: the level came off
            # this account's own total_xp a few lines ago, not off the request.
            return {"ok": False, "error": "level_locked", "level": level, "need": need_level}

        update: Dict[str, Any] = {}
        granted: Dict[str, Any] = {"type": rtype, "amount": amount}

        if rtype == "coins":
            before = max(0, _int(_stats_of(doc).get("critter_coins")))
            after = before + amount
            update["stats"] = {"critter_coins": after}
            granted.update({"coins": amount, "coinsTotal": after})

        elif rtype == "xp":
            # An XP drop is written the way the supporter-tier grant writes one:
            # total_xp AND every derived level field, in lock-step, so the
            # leaderboard and the header see the new level immediately instead
            # of after the player's next game. It can also push them past the
            # next tier's level, which is deliberate and is why claim_all loops.
            before = max(0, _int(_stats_of(doc).get("total_xp")))
            after = before + amount
            lvl, xp_cur, xp_goal = (_level_for_xp(after) if _level_for_xp else (1, 0, 1))
            update["stats"] = {
                "total_xp": after,
                "level": int(lvl), "player_level": int(lvl),
                "xp_current": int(xp_cur), "level_xp_current": int(xp_cur),
                "xp_goal": int(xp_goal), "level_xp_goal": int(xp_goal),
            }
            granted.update({"xp": amount, "xpTotal": after, "level": int(lvl)})

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

        elif rtype == "swap":
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
                # backgrounds: a reward that silently evaporates is worse than
                # one that waits.
                return {"ok": False, "error": "backgrounds_full"}
            update["unlocked_backgrounds"] = _array_union()([path])
            granted.update({"path": path})

        elif rtype == "emote":
            path = _pick_emote(doc)
            if not path:
                return {"ok": False, "error": "emotes_full"}
            update["emote_icons"] = _array_union()([path])
            granted.update({"path": path})

        elif rtype == "daily_slot":
            held = max(0, _int(doc.get("bonus_daily_slots")))
            if held >= MAX_EXTRA_DAILY:
                return {"ok": False, "error": "daily_slots_full", "max": MAX_EXTRA_DAILY}
            after = min(MAX_EXTRA_DAILY, held + amount)
            update["bonus_daily_slots"] = after
            granted.update({"slots": after})

        elif rtype == "weekly_slot":
            held = max(0, _int(doc.get("bonus_weekly_slots")))
            if held >= MAX_EXTRA_WEEKLY:
                return {"ok": False, "error": "weekly_slots_full", "max": MAX_EXTRA_WEEKLY}
            after = min(MAX_EXTRA_WEEKLY, held + amount)
            update["bonus_weekly_slots"] = after
            granted.update({"slots": after})

        elif rtype == "avatar":
            path = str(spec.get("img") or "")
            if not _AVATAR_PATH.match(path):
                return {"ok": False, "error": "server_error"}
            # ArrayUnion, so a player who already bought this skin in the Store
            # is not handed a duplicate entry; the claim still records, because
            # a tier that can never be marked done is a Claim button that never
            # goes away.
            update["unlocked_icons"] = _array_union()([path])
            granted.update({"path": path, "critter": str(spec.get("critter") or "")})

        now = time.time()
        entry = {
            "uid": uid,
            "season": SEASON_ID,
            "tier": tier_id,
            "level": need_level,
            "type": rtype,
            "amount": amount,
            "granted": {k: v for k, v in granted.items() if k != "type"},
            "username": str(doc.get("nickname") or doc.get("username") or ""),
            "at": now,
            "at_iso": _iso(now),
        }

        # merge=True so the coin/XP write never clobbers the rest of `stats`.
        t.set(user_ref, update, merge=True)
        # Inside the transaction: the reward cannot exist without this, and this
        # cannot exist without the reward.
        t.create(claim_ref, entry)
        return {"ok": True, "tier": tier_id, "level": need_level, "granted": granted}

    try:
        return _run(txn)
    except Exception as exc:  # noqa: BLE001
        # A create() collision is the guard working: another request won the
        # race. Every other failure wrote nothing, and must NOT be reported as
        # "already claimed", or the player believes they were paid and never
        # tries again.
        if type(exc).__name__ == "AlreadyExists":
            return {"ok": False, "error": "already_claimed"}
        import traceback
        print(f"[critterpass] claim {tier_id} failed for {uid}: {exc}\n{traceback.format_exc(limit=4)}")
        return {"ok": False, "error": "server_error"}


# How many times claim_all re-reads the level and sweeps again. An XP tier can
# lift the account past levels whose tiers were locked when the sweep started,
# and a player who presses "Claim all" once should not have to press it three
# more times to collect what that press unlocked. Bounded because each pass is
# a Firestore read plus a claim per tier, and because a track that needed more
# than this would be a track whose XP drops out-run its own levels.
_CLAIM_ALL_PASSES = 4

# How many tiers ONE claim-all request will pay before handing back "there is
# more". Each tier is its own transaction, which is exactly what stops a tier
# that refuses from rolling back the ones that worked, but a transaction is a
# Firestore round trip: a level-100 player sweeping a hundred of them in a
# single request is a request that can outlive its own timeout, and a timeout
# here reads as "Claim all did nothing" while half the track was actually paid.
# So the request is bounded and the CLIENT loops (js/critter-pass.js), which
# also lets it show the count climbing instead of hanging on one spinner.
CLAIM_ALL_LIMIT = 25


def claim_all(db, uid: str) -> Dict[str, Any]:
    """Claim every unlocked, unclaimed tier.

    Deliberately a LOOP of single claims rather than one giant transaction: each
    tier keeps its own ledger doc and its own all-or-nothing guarantee, so a tier
    that refuses (a full shield hoard, no backgrounds left) stops itself and
    leaves every other payout intact. A partial result is reported honestly
    instead of rolling back rewards that were legitimately earned.

    The outer loop exists for the XP tiers: claiming 1,900 XP can raise the
    account's level, which unlocks tiers that were locked when the sweep began.

    Pays at most CLAIM_ALL_LIMIT tiers and then reports `more: True`. The caller
    is expected to ask again; everything already paid is paid, and the ledger
    makes asking again safe."""
    if not _SAFE_ID.match(str(uid or "")):
        return {"ok": False, "error": "bad_request"}
    try:
        snap = _users(db).document(uid).get()
        if not snap.exists:
            return {"ok": False, "error": "no_account"}
        doc = snap.to_dict() or {}
        if not _owns_pass(doc):
            return {"ok": False, "error": "not_owned"}
    except Exception as exc:  # noqa: BLE001
        print(f"[critterpass] claim_all read failed for {uid}: {exc}")
        return {"ok": False, "error": "server_error"}

    already = set(claimed_ids(db, uid))
    results: List[Dict[str, Any]] = []
    skipped: Dict[str, str] = {}
    more = False

    for _ in range(_CLAIM_ALL_PASSES):
        try:
            snap = _users(db).document(uid).get()
            level = _level_of(snap.to_dict() or {}) if snap.exists else 0
        except Exception as exc:  # noqa: BLE001
            print(f"[critterpass] claim_all level read failed for {uid}: {exc}")
            break
        paid_this_pass = 0
        for spec in _TRACK_SPEC:
            if len(results) >= CLAIM_ALL_LIMIT:
                more = True
                break
            tid = _tier_id(spec)
            if str(spec["type"]) not in CLAIMABLE_TYPES:
                continue
            if tid in already or int(spec["level"]) > level:
                continue
            res = claim(db, uid, tid)
            if res.get("ok"):
                results.append(res)
                already.add(tid)
                skipped.pop(tid, None)
                paid_this_pass += 1
            elif res.get("error") == "already_claimed":
                already.add(tid)
            else:
                # Keyed by tier, so a tier that refuses on pass 1 and succeeds
                # on pass 2 is not reported as both paid and skipped.
                skipped[tid] = str(res.get("error") or "error")
        if more or not paid_this_pass:
            break

    return {"ok": True, "claimed": results,
            "skipped": [{"tier": k, "error": v} for k, v in sorted(skipped.items())],
            "count": len(results), "more": more}


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════════════════════
ERROR_MESSAGES = {
    "unauthorized": "Sign in to use the Critter Pass.",
    "no_account": "We couldn't find your account: try signing in again.",
    "unknown_tier": "That reward isn't on the Critter Pass.",
    "not_claimable": "There's nothing to claim on that tier.",
    "already_claimed": "You've already claimed that reward.",
    "already_owned": "You already own the Critter Pass.",
    "not_owned": f"Unlock the Critter Pass first ({CRITTER_PASS_PRICE:,} Critter Coins).",
    "not_enough_coins": f"You need {CRITTER_PASS_PRICE:,} Critter Coins to unlock the Critter Pass.",
    "no_vouchers": "You don't have a Season Pass voucher to redeem.",
    "level_locked": "You haven't reached that level yet.",
    "shields_full": "Your Streak Shields are full: spend one first.",
    "boosts_full": "You're holding as many XP Boosts as you can: use one first.",
    "rerolls_full": "You're holding as many Weekly Swaps as you can: use one first.",
    "backgrounds_full": "You already own every background. This one is waiting for the next batch.",
    "emotes_full": "You already have an emote for every critter you own.",
    "daily_slots_full": "You already have every extra daily challenge slot.",
    "weekly_slots_full": "You already have every extra weekly challenge slot.",
    "bad_request": "Something was wrong with that request. Nothing was claimed.",
    "firestore_unavailable": "Couldn't reach your account just now. Nothing was claimed.",
    "server_error": "Something went wrong. Nothing was claimed: please try again.",
}


def message_for(result: Dict[str, Any]) -> str:
    return ERROR_MESSAGES.get(str(result.get("error") or ""), ERROR_MESSAGES["server_error"])


def _auth_uid(body: Dict[str, Any]) -> Optional[str]:
    tok = body.get("idToken") if isinstance(body.get("idToken"), str) else ""
    claims = _verify_token(tok) if (tok and _verify_token) else None
    return claims.get("uid") if claims and claims.get("uid") else None


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    """POST /api/critterpass/state       the track + this account's progress
       POST /api/critterpass/buy         unlock the pass for CRITTER_PASS_PRICE,
                                         or for one voucher with { voucher:true }
       POST /api/critterpass/claim       claim one tier   { tier }
       POST /api/critterpass/claim-all   claim everything unlocked
    """
    path = parsed.path
    if not path.startswith("/api/critterpass/"):
        return False
    action = path[len("/api/critterpass/"):]

    # Readable signed-out: the track is the sales pitch, and the reply carries
    # no account data without a token.
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

    if action == "buy":
        res = buy(db, uid, use_voucher=bool(body.get("voucher")))
        if res.get("ok"):
            state = state_payload(uid)
            res["inventory"] = state.get("inventory")
            res["claimed"] = state.get("claimed")
        else:
            res["message"] = message_for(res)
        handler._send_json(res)
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

    handler._send_json({"ok": False, "error": "unknown_action"}, status=404)
    return True
