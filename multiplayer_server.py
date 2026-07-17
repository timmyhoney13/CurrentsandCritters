#!/usr/bin/env python3
"""Fish Game multiplayer room server (HTTP + SSE, server-authoritative)."""

from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import mimetypes
import os
import random
import re
import secrets
import socket
import string
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass, replace as dataclass_replace
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

# Force non-interactive payment/discard behavior for human seats in run_match.
os.environ.setdefault("FISH_WEB_CONTROL", "1")

import fish_game_all_in_one as fish


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_INDEX_PATH = os.path.join(BASE_DIR, "multiplayer", "client", "index.html")
CLIENT_PREVIEW_PATH = os.path.join(BASE_DIR, "multiplayer", "client", "preview.html")
WEBSITE_INDEX_PATH = os.path.join(BASE_DIR, "multiplayer", "client", "website.html")
RULES_INDEX_PATH = os.path.join(BASE_DIR, "multiplayer", "client", "rules.html")
ABOUT_INDEX_PATH = os.path.join(BASE_DIR, "multiplayer", "client", "about.html")
LEADERBOARD_HTML_PATH = os.path.join(BASE_DIR, "multiplayer", "client", "leaderboard.html")
SUPPORTER_WALL_HTML_PATH  = os.path.join(BASE_DIR, "multiplayer", "client", "supporter-wall.html")
SUPPORTER_ADMIN_HTML_PATH = os.path.join(BASE_DIR, "multiplayer", "client", "supporter-admin.html")
CLAIM_REWARDS_HTML_PATH   = os.path.join(BASE_DIR, "multiplayer", "client", "claim-rewards.html")
DATASET_PATH = os.path.join(BASE_DIR, "multiplayer", "human_game_dataset.jsonl")
ROOM_STATE_DIR = str(
    os.environ.get("FISH_ROOM_STATE_DIR", os.path.join(BASE_DIR, "multiplayer", "state"))
).strip() or os.path.join(BASE_DIR, "multiplayer", "state")
COMPETITIVE_GAMES_DIR = str(
    os.environ.get("FISH_COMPETITIVE_GAMES_DIR", os.path.join(BASE_DIR, "multiplayer", "competitive_games"))
).strip() or os.path.join(BASE_DIR, "multiplayer", "competitive_games")
COMPETITIVE_LEADERBOARD_PATH = os.path.join(COMPETITIVE_GAMES_DIR, "leaderboard.json")
COMPETITIVE_SEASONS_PATH     = os.path.join(COMPETITIVE_GAMES_DIR, "seasons.json")
# Pending forfeit losses for players who left a competitive match. The loser is
# offline at forfeit time, so their CP penalty is applied the next time their
# client loads (it queries /api/competitive/forfeit_pending by name).
COMPETITIVE_FORFEITS_PATH    = os.path.join(COMPETITIVE_GAMES_DIR, "forfeits_pending.json")
# Competitive: a player who leaves a running match and does not return within
# this many seconds forfeits — the player still present wins.
COMPETITIVE_FORFEIT_SEC = 30.0
# Team Mode: the fixed team roster. A team's index (0..3) maps to its color
# name and swatch. A team game starts with 2 teams (Red vs Blue) and the host
# can open up to 4 (adding Green, then Yellow).
TEAM_COLORS = ["Red", "Blue", "Green", "Yellow"]
TEAM_COLOR_HEX = {0: "#e0463c", 1: "#3d7be0", 2: "#3fb26b", 3: "#e6b32e"}
# A pending cross-team swap offer auto-expires this many seconds after it is
# sent if the target never accepts/declines it.
SWAP_REQUEST_TTL_SEC = 45.0
GAMES_HISTORY_DIR = str(
    os.environ.get("FISH_GAMES_HISTORY_DIR", os.path.join(BASE_DIR, "multiplayer", "games_history"))
).strip() or os.path.join(BASE_DIR, "multiplayer", "games_history")
GAMES_LEADERBOARD_PATH = os.path.join(GAMES_HISTORY_DIR, "leaderboard.json")
STATS_PATH = str(
    os.environ.get("FISH_STATS_PATH", os.path.join(ROOM_STATE_DIR, "site_stats.json"))
).strip() or os.path.join(ROOM_STATE_DIR, "site_stats.json")
# Hardcoded historical baseline — the current real totals, so the counter never
# shows a stale/low number even if the Render env vars aren't synced. Applied as a
# floor only: real Firestore counts (registered) and the live games counter climb
# above this and are never lowered by it.
_STATS_SEED_GAMES_FLOOR   = 101
_STATS_SEED_PLAYERS_FLOOR = 18
STATS_SEED_GAMES   = max(_STATS_SEED_GAMES_FLOOR,   max(0, int(os.environ.get("FISH_STATS_SEED_GAMES",   "0") or "0")))
STATS_SEED_PLAYERS = max(_STATS_SEED_PLAYERS_FLOOR, max(0, int(os.environ.get("FISH_STATS_SEED_PLAYERS", "0") or "0")))

# ── Chat profanity guard (server-authoritative) ─────────────────────────────
# Keeps room + spectator chat family-friendly. Swear words are masked with
# asterisks (the message still sends, minus the swear) so a swear can never
# reach another player even if a client bypasses the matching browser-side
# filter in preview-app.js. Two match modes mirror the client exactly:
#   • STRONG roots — matched ANYWHERE, tolerant of leetspeak, repeated letters
#     and separator evasion (f u c k, f-u-c-k, f*u*c*k, sh1t, phuck…).
#   • WORD roots — short words that also live inside innocent words (ass in
#     "class", cock in "peacock"), matched ONLY as a whole word (avoids the
#     "Scunthorpe problem").
# Keep these two lists in sync with CC_PROFANITY in preview-app.js.
_PROF_LEET = {
    "a": "a4@", "b": "b8", "c": "c(", "e": "e3", "g": "g9", "i": "i1!|",
    "l": "l1|", "o": "o0", "s": "s5$", "t": "t7+", "u": "uv", "z": "z2",
}
_PROF_STRONG = [
    "fuck", "phuck", "shit", "bitch", "biotch", "beotch", "cunt", "asshole",
    "dickhead", "bastard", "bollock", "wanker", "pussy", "slut", "whore",
    "faggot", "nigger", "nigga", "retard", "cocksucker", "motherfucker",
    "bullshit", "dumbass", "jackass", "dipshit", "jerkoff", "goddamn",
    "douchebag", "blowjob", "handjob", "dildo", "boner", "penis", "vagina",
]
_PROF_WORDS = [
    "ass", "arse", "damn", "hell", "crap", "piss", "cock", "dick", "tit",
    "prick", "twat", "wank", "hoe", "homo", "queer", "coon", "chink", "spic",
    "kike", "gook", "dyke", "fag", "skank", "sex", "porn", "cum", "anal",
    "boob", "bloody", "bugger", "douche", "wtf", "stfu",
]


def _prof_cls(ch: str) -> str:
    alts = _PROF_LEET.get(ch, ch)
    esc = re.sub(r"([-\\\]^])", r"\\\1", alts)
    return "[" + esc + "]"


def _prof_strong_pat(word: str) -> str:
    return r"[\s._*\-]{0,2}".join(_prof_cls(c) + "+" for c in word)


def _prof_word_core(word: str) -> str:
    # leet-tolerant, repeat-tolerant core + optional simple plural suffix
    return "".join(_prof_cls(c) + "+" for c in word) + r"(?:es|[sz])?"


# One leading-boundary group (1) + one body group (2) wrapping the whole
# alternation — so group indices stay fixed no matter which word matches.
_PROF_STRONG_RE = re.compile("(" + "|".join(_prof_strong_pat(w) for w in _PROF_STRONG) + ")", re.IGNORECASE)
_PROF_WORD_RE   = re.compile(
    "(^|[^a-z0-9])(" + "|".join(_prof_word_core(w) for w in _PROF_WORDS) + ")(?=[^a-z0-9]|$)",
    re.IGNORECASE,
)


def _censor_profanity(text: str) -> str:
    """Mask swear words in `text` with asterisks, preserving length + spacing."""
    if not text:
        return text
    out = _PROF_STRONG_RE.sub(lambda m: "*" * len(m.group(0)), text)
    # group(1) = leading boundary char (kept), group(2) = the swear (masked)
    return _PROF_WORD_RE.sub(lambda m: m.group(1) + "*" * len(m.group(2)), out)


# ── Firebase Admin: exact live "Registered Players" / "Players Online" ──────
# Firestore is the persistent source of truth for accounts and presence, but
# the marketing site cannot read it directly (security rules block public
# reads — by design, to keep emails/profiles private). Instead the server reads
# Firestore with a service account and serves the exact counts through
# /api/stats. Configure by setting FIREBASE_SERVICE_ACCOUNT to the service
# account JSON (or GOOGLE_APPLICATION_CREDENTIALS to a file path). If neither
# is set, the server falls back to its stored counters and online stays 0.
FIREBASE_PRESENCE_FRESH_SEC = 5 * 60      # match the client's 90s ping + margin
# Firestore doc that holds the cross-deploy "games played" counter. The Render
# disk has historically failed to accumulate game-history files (a redeploy or
# disk reset leaves /api/history/games empty), which left games_played frozen at
# the seed. Firestore is the same store that already serves the exact registered
# count and is proven to persist, so the games counter lives here too: every
# finished game atomically increments it and /api/stats reads it back, so the
# marketing number survives redeploys and always climbs.
FIRESTORE_STATS_DOC = ("meta", "site_stats")   # collection, document
_FIRESTORE_DB = None
_FIRESTORE_INIT_DONE = False
_FIRESTORE_LOCK = threading.Lock()
# Cache live counts so a burst of /api/stats hits doesn't hammer Firestore.
_LIVE_COUNTS_CACHE = {"at": 0.0, "registered": None, "online": None, "games": None}
_LIVE_COUNTS_TTL_SEC = 30.0


def _get_firestore():
    """Return a cached Firestore client, or None if Firebase isn't configured."""
    global _FIRESTORE_DB, _FIRESTORE_INIT_DONE
    if _FIRESTORE_INIT_DONE:
        return _FIRESTORE_DB
    with _FIRESTORE_LOCK:
        if _FIRESTORE_INIT_DONE:
            return _FIRESTORE_DB
        _FIRESTORE_INIT_DONE = True
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore
            raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
            if raw:
                cred = credentials.Certificate(json.loads(raw))
            elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip():
                cred = credentials.ApplicationDefault()
            else:
                print("[stats] Firebase service account not set; live user counts disabled.")
                return None
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            _FIRESTORE_DB = firestore.client()
            print("[stats] Firebase Admin initialised; live user counts enabled.")
        except Exception as exc:  # noqa: BLE001 - never let stats setup crash the server
            print(f"[stats] Firebase Admin init failed ({exc}); live user counts disabled.")
            _FIRESTORE_DB = None
        return _FIRESTORE_DB


def _fetch_firestore_games_played(db):
    """Persisted games_played from the Firestore stats doc, or None if absent."""
    try:
        coll, doc = FIRESTORE_STATS_DOC
        snap = db.collection(coll).document(doc).get()
        if snap.exists:
            val = (snap.to_dict() or {}).get("games_played")
            if isinstance(val, (int, float)):
                return int(val)
    except Exception as exc:  # noqa: BLE001
        print(f"[stats] firestore games_played read failed: {exc}")
    return None


def bump_firestore_games_played(n=1):
    """Atomically add n to the persisted games_played counter (no-op if Firebase
    isn't configured). Called once per finished game so the marketing counter
    keeps climbing even across Render redeploys / disk resets."""
    db = _get_firestore()
    if db is None:
        return
    try:
        from firebase_admin import firestore
        Increment = getattr(firestore, "Increment", None)
        if Increment is None:  # older SDKs expose it only on the v1 module
            from google.cloud.firestore_v1 import Increment
        coll, doc = FIRESTORE_STATS_DOC
        db.collection(coll).document(doc).set(
            {"games_played": Increment(n)}, merge=True
        )
        # Reflect the bump immediately instead of waiting for the cache TTL.
        if isinstance(_LIVE_COUNTS_CACHE.get("games"), int):
            _LIVE_COUNTS_CACHE["games"] += n
    except Exception as exc:  # noqa: BLE001
        print(f"[stats] firestore games_played increment failed: {exc}")


def seed_firestore_games_played(floor):
    """Ensure the persisted games_played counter is at least `floor` (apply the
    historical baseline once, never lowering a real, higher count)."""
    db = _get_firestore()
    if db is None or floor <= 0:
        return
    try:
        current = _fetch_firestore_games_played(db) or 0
        if current < floor:
            coll, doc = FIRESTORE_STATS_DOC
            db.collection(coll).document(doc).set({"games_played": floor}, merge=True)
            print(f"[stats] firestore games_played seeded to {floor}")
    except Exception as exc:  # noqa: BLE001
        print(f"[stats] firestore games_played seed failed: {exc}")


def _fetch_live_user_counts():
    """(registered, online, games) straight from Firestore, or (None, None, None)
    if unavailable. `games` is the persisted cross-deploy games_played counter."""
    db = _get_firestore()
    if db is None:
        return None, None, None
    games = _fetch_firestore_games_played(db)
    try:
        users = db.collection("users")
        # Exact account total via server-side aggregate count (no doc data read).
        registered = None
        try:
            agg = users.count().get()
            # Result shape varies by SDK version: list[list[AggregationResult]].
            registered = int(agg[0][0].value)
        except Exception:
            registered = sum(1 for _ in users.select([]).stream())
        # Online query: prefer the modern FieldFilter API, fall back to the
        # positional form on older SDKs.
        try:
            from google.cloud.firestore_v1 import FieldFilter
            online_q = users.where(filter=FieldFilter("online", "==", True))
        except Exception:
            online_q = users.where("online", "==", True)
        # Presence flag plus a freshness window so abruptly closed tabs (where
        # `online` was never cleared) don't linger in the count.
        fresh_after = time.time() - FIREBASE_PRESENCE_FRESH_SEC
        online = 0
        for doc in online_q.stream():
            data = doc.to_dict() or {}
            la = data.get("last_active")
            la_sec = la.timestamp() if hasattr(la, "timestamp") else 0
            if la_sec >= fresh_after:
                online += 1
        return registered, online, games
    except Exception as exc:  # noqa: BLE001
        print(f"[stats] live user count query failed: {exc}")
        return None, None, games


def get_live_user_counts():
    """Cached (registered, online, games); refreshes at most every
    _LIVE_COUNTS_TTL_SEC. `games` is the persisted Firestore games_played."""
    now = time.time()
    if now - _LIVE_COUNTS_CACHE["at"] < _LIVE_COUNTS_TTL_SEC:
        return (_LIVE_COUNTS_CACHE["registered"],
                _LIVE_COUNTS_CACHE["online"],
                _LIVE_COUNTS_CACHE["games"])
    registered, online, games = _fetch_live_user_counts()
    # Keep the last good values if a refresh fails transiently.
    if registered is not None:
        _LIVE_COUNTS_CACHE["registered"] = registered
    if online is not None:
        _LIVE_COUNTS_CACHE["online"] = online
    if games is not None:
        _LIVE_COUNTS_CACHE["games"] = games
    _LIVE_COUNTS_CACHE["at"] = now
    return (_LIVE_COUNTS_CACHE["registered"],
            _LIVE_COUNTS_CACHE["online"],
            _LIVE_COUNTS_CACHE["games"])


# ══════════════════════════════════════════════════════════════════════════
#  STRIPE CHECKOUT WEBHOOK — credit Critter Coins / grant Supporter Tiers
# ══════════════════════════════════════════════════════════════════════════
# Players buy Critter Coins packs and Supporter Tiers through Stripe-hosted
# Payment Links (see the in-app Store). Stripe runs the entire card flow — we
# never see card data, and the client NEVER credits coins to itself. After
# Stripe confirms a payment it POSTs a `checkout.session.completed` event to
# /api/stripe/webhook, and ONLY this handler (gated by the webhook signature)
# grants anything. That signature is the thing that stops a player from POSTing
# a fake "I paid" event to mint themselves coins.
#
# ──────────────────────────────────────────────────────────────────────────
#  WHERE TO PUT YOUR STRIPE KEYS  (set as Render env vars — never hard-code!)
# ──────────────────────────────────────────────────────────────────────────
#   STRIPE_WEBHOOK_SECRET  → the "Signing secret" for THIS endpoint. In the
#       Stripe Dashboard: Developers → Webhooks → "Add endpoint",
#         • Endpoint URL:  https://<your-domain>/api/stripe/webhook
#         • Events to send: checkout.session.completed
#       Stripe then shows a secret starting with "whsec_". Paste it into the
#       STRIPE_WEBHOOK_SECRET env var. (Test mode and live mode each have their
#       OWN signing secret — use the matching one.)
#   STRIPE_SECRET_KEY      → your secret API key ("sk_test_…" in test mode,
#       "sk_live_…" in live mode). It is OPTIONAL here: coin/tier fulfilment is
#       resolved from the checkout amount/metadata without any Stripe API call.
#       Kept for future use (e.g. expanding line items). Never expose it.
#   In Render: Dashboard → your service → Environment → add both with
#   sync:false (they're also listed in render.yaml).
#
#   ⚠️ The Payment Links wired into the Store are TEST links right now, so use
#   the TEST webhook signing secret while testing. Before launch, swap the Store
#   links to LIVE Payment Links AND set STRIPE_WEBHOOK_SECRET to the LIVE
#   endpoint's signing secret.
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "").strip()
# Reject events whose signed timestamp is more than this far from now (Stripe's
# recommended replay-attack guard).
STRIPE_SIG_TOLERANCE_SEC = 5 * 60

# The 8 cosmetic ocean backgrounds — KEEP IN SYNC with EXCLUSIVE_BACKGROUNDS in
# preview-app.js. Granting a tier with "unlock all backgrounds" adds these.
ALL_BACKGROUND_PATHS = [
    "/backgrounds/bg-kelp.png",
    "/backgrounds/bg-coral-reef.png",
    "/backgrounds/bg-artificial-reef.png",
    "/backgrounds/bg-tide-pool.png",
    "/backgrounds/bg-arctic.png",
    "/backgrounds/bg-deep.png",
    "/backgrounds/bg-pier.png",
    "/backgrounds/bg-mangrove.png",
]

# Map a completed checkout to what it grants, keyed by the order TOTAL in cents
# (Stripe `amount_total`). This is unambiguous because every product has a
# distinct price: coin packs $1/$5/$10/$20 and tiers $15/$35/$50.
# ⚠️ These cents MUST match the prices on your Stripe Payment Links. If you
# change a price for launch, update the matching key here. (If you ever add two
# products that share a price, set metadata.cc_coins / metadata.cc_tier on the
# Payment Link or Price instead — _reward_for_session reads metadata first.)
COIN_PACKS_BY_CENTS = {
    100:  1000,    # $1.00  → 1,000 coins
    500:  5250,    # $5.00  → 5,250 coins
    1000: 11500,   # $10.00 → 11,500 coins
    2000: 25000,   # $20.00 → 25,000 coins
}
SUPPORTER_TIERS_BY_CENTS = {
    1500: "wave-warrior",   # $15.00
    3500: "ocean-ally",     # $35.00
    5000: "tide-turner",    # $50.00
}

# What each Supporter Tier grants automatically in Firestore. The remaining
# perks (thank-you email, postcard, physical copy, Supporter Reef Wall name) are
# manual fulfilment — they're saved on the purchase record (stripe_events doc)
# with the buyer's email so you can fulfil them by hand.
SUPPORTER_TIER_GRANTS = {
    "wave-warrior": {"bonus_xp": 10000, "unlock_all_backgrounds": False, "icons": []},
    "ocean-ally":   {"bonus_xp": 25000, "unlock_all_backgrounds": True,  "icons": ["/avatars/fish.png"]},
    "tide-turner":  {"bonus_xp": 50000, "unlock_all_backgrounds": True,  "icons": ["/avatars/fish.png", "/avatars/amberjack.png"]},
}

# Player level curve: the CUMULATIVE total_xp required to REACH each level
# (index 0 = level 1). ⚠️ KEEP IN SYNC with LEVEL_XP_TOTALS in preview-app.js —
# it's what lets a server-granted XP bump (a Supporter-Tier purchase) write the
# stored level fields to exactly what the client would compute from total_xp, so
# the leaderboard / header / profile never disagree.
LEVEL_XP_TOTALS = [
    0, 50, 100, 250, 550, 900, 1400, 2000, 2700, 3550,
    4550, 5700, 7000, 8450, 10100, 11850, 13800, 15900, 18200, 20650,
    23300, 26150, 29150, 32400, 35800, 39400, 43200, 47200, 51400, 55850,
    60450, 65300, 70350, 75650, 81150, 86850, 92800, 99000, 105400, 112000,
    118900, 126000, 133300, 140900, 148700, 156800, 165100, 173650, 182450, 191500,
    200850, 210400, 220200, 230300, 240650, 251250, 262100, 273250, 284650, 296300,
    308250, 320450, 332950, 345700, 358750, 372050, 385650, 399500, 413650, 428100,
    442850, 457850, 473150, 488750, 504600, 520800, 537250, 554000, 571050, 588400,
    606050, 624000, 642250, 660850, 679700, 698850, 718350, 738100, 758200, 778600,
    800000, 825000, 852500, 882500, 915000, 950000, 987500, 1027500, 1070000, 1125000,
]


def _level_progress_for_total_xp(total_xp: Any) -> Tuple[int, int, int]:
    """(level, xp_current, xp_goal) for a total_xp — mirrors the client's
    getLevelProgressFromTotalXp so stored level fields match exactly."""
    try:
        xp = max(0, int(total_xp))
    except (TypeError, ValueError):
        xp = 0
    totals = LEVEL_XP_TOTALS
    maxlvl = len(totals)
    level = 1
    for i in range(maxlvl, 0, -1):
        if xp >= totals[i - 1]:
            level = i
            break
    if level >= maxlvl:
        cap = totals[maxlvl - 1]
        return (maxlvl, cap, cap)
    start = totals[level - 1]
    nxt = totals[level]
    return (level, max(0, xp - start), max(1, nxt - start))

# ── Supporter Reef Wall: LIFETIME-spend tiers ───────────────────────────────
# A supporter's wall TIER and name SIZE come from their LIFETIME total (the sum
# of every payment they've made), NOT a single purchase. Evaluated high→low;
# under the smallest floor a supporter is still recorded but has no wall tier.
#   $10–24.99   wave_warrior   small
#   $25–49.99   ocean_ally     medium
#   $50–99.99   tide_turner    large
#   $100–249.99 reef_guardian  extra_large
#   $250+       ocean_legend   biggest
SUPPORTER_WALL_TIERS: List[Tuple[int, str, str]] = [
    (25000, "ocean_legend",  "biggest"),
    (10000, "reef_guardian", "extra_large"),
    (5000,  "tide_turner",   "large"),
    (2500,  "ocean_ally",    "medium"),
    (1000,  "wave_warrior",  "small"),
]

# The EXACT labels of the three custom questions added to every Stripe Payment
# Link. Stripe echoes them back in session.custom_fields[].label.custom — we
# match on these verbatim (trim + case-insensitive) to read each answer.
CF_WALL_NAME_LABEL   = "Name for Supporter Reef Wall"
CF_WALL_PUBLIC_LABEL = "Show my name publicly on the Supporter Wall?"
CF_USERNAME_LABEL    = "Currents and Critters Online Username"


def _supporter_tier_for_total(total_cents: Any) -> Tuple[Optional[str], Optional[str]]:
    """(tier, wall_size) for a LIFETIME total in cents, or (None, None) below $10."""
    try:
        cents = int(total_cents)
    except (TypeError, ValueError):
        cents = 0
    for floor, tier, size in SUPPORTER_WALL_TIERS:
        if cents >= floor:
            return tier, size
    return None, None


# ── Wall name safety filter ──────────────────────────────────────────────────
# A paid, public wall name goes onto the wall IMMEDIATELY (see the gate in
# _process_stripe_checkout). Names that trip these lists are held as
# pending_review for a human instead — they never auto-show, but they aren't
# rejected either: they surface in the /supporter-admin review list. The three
# lists trade off catching abuse vs. falsely holding innocent names:
#
#   _WALL_SUBSTR_BLOCK  matched inside a SINGLE token — high-signal terms that
#     essentially never appear inside a real word/name, so "ShitLord" and
#     "bullshit" are caught while a multi-word name never crosses a boundary.
#   _WALL_WORD_BLOCK    matched only as a WHOLE token — short words that DO live
#     inside ordinary surnames (Hancock, Dickinson, Cummings, Assange), so we
#     require the entire token to equal them to avoid false holds.
#   _WALL_COLLAPSED_BLOCK  matched in the separator-stripped whole name —
#     catches spaced-out evasion, e.g. "n i g g e r" / "f-a-g-g-o-t" / "F u c k".
#     Only terms that never appear inside an innocent word (even across word
#     boundaries) go here; "shit"/"cunt" are NOT, to avoid "fresh item"/
#     "Scunthorpe" false holds — they're still caught per-token above.
_WALL_SUBSTR_BLOCK = frozenset({
    "fuck", "shit", "bitch", "pussy", "whore", "slut", "faggot", "nigger",
    "nigga", "kike", "chink", "wetback", "tranny", "dildo", "jizz", "asshole",
    "dickhead", "bollocks", "wanker", "hitler", "retard", "cumshot", "blowjob",
    "handjob", "molest", "pedophile", "rapist", "porn",
})
_WALL_WORD_BLOCK = frozenset({
    "ass", "cock", "dick", "cum", "coon", "rape", "anus", "nazi", "penis",
    "prick", "twat", "wank", "cunt", "fag", "semen", "vagina", "bastard",
    "spic", "dyke", "boob", "tit", "hoe", "sex",
})
_WALL_COLLAPSED_BLOCK = frozenset({
    "nigger", "nigga", "faggot", "kike", "chink", "wetback", "tranny",
    "fuck", "bitch", "pussy",
})


def _name_needs_review(name: str) -> bool:
    """True when a public wall name should wait for MANUAL approval instead of
    auto-showing: blank, unusually long, or containing blocked/offensive words.
    A clean name returns False and goes straight onto the wall. Held names are
    not rejected — they surface in the /supporter-admin review list."""
    raw = str(name or "").strip()
    if not raw or len(raw) > 40:
        return True
    low = raw.lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", low) if t]
    if set(tokens) & _WALL_WORD_BLOCK:
        return True
    for tok in tokens:
        if any(bad in tok for bad in _WALL_SUBSTR_BLOCK):
            return True
    collapsed = re.sub(r"[^a-z0-9]+", "", low)
    return any(bad in collapsed for bad in _WALL_COLLAPSED_BLOCK)


def _custom_field_value(custom_fields: Any, label: str) -> str:
    """Read one Stripe custom-field answer by its (case-insensitive) label.

    session.custom_fields is a list of objects shaped like:
        {"key": "…", "label": {"type": "custom", "custom": "<label>"},
         "type": "text", "text": {"value": "…"}}
    The answer lives in the sub-object named by the field's "type"
    (text / dropdown / numeric). Returns "" when the field is absent/blank."""
    if not isinstance(custom_fields, list):
        return ""
    target = str(label or "").strip().lower()
    for field in custom_fields:
        if not isinstance(field, dict):
            continue
        lab = field.get("label")
        name = str(lab.get("custom") or "").strip() if isinstance(lab, dict) else ""
        if name.lower() != target:
            continue
        ftype = str(field.get("type") or "").strip()
        sub = field.get(ftype) if ftype else None
        if isinstance(sub, dict) and sub.get("value") is not None:
            return str(sub.get("value")).strip()
        # Fallback: whichever known answer sub-object actually carries a value.
        for key in ("text", "dropdown", "numeric"):
            sub = field.get(key)
            if isinstance(sub, dict) and sub.get("value") is not None:
                return str(sub.get("value")).strip()
        return ""
    return ""


def _is_affirmative(val: str) -> bool:
    """True when a yes/no answer means YES (show the name publicly)."""
    v = str(val or "").strip().lower()
    if not v:
        return False
    if v[0] == "y":  # yes / yep / yeah / y
        return True
    return v in ("true", "1", "show", "public", "publicly", "sure", "ok", "okay")


def _find_uid_by_username(db, username_lower: str) -> Optional[str]:
    """Find a game account by its UNIQUE username (usernames are unique by
    construction; see _claim_username). Returns the uid or None.

    Deliberately does NOT fall back to the in-game `nickname`, which is NOT
    unique — matching a typed name against a shared nickname could credit a
    payment (and its coins) to the wrong account. Unmatched payments are saved
    as guests and reclaimed later by verified email, which is safe."""
    uname = str(username_lower or "").strip().lower()
    if not uname:
        return None
    try:
        snap = db.collection("users").where("usernameLower", "==", uname).limit(1).get()
        for doc in snap:
            return doc.id
    except Exception as exc:  # noqa: BLE001
        print(f"[stripe] username→uid match failed: {exc}")
    return None


def _session_product_name(session: dict, kind: Optional[str], value: Any) -> str:
    """Best-effort human name for what was bought (for the payment record)."""
    meta = session.get("metadata") or {}
    name = str(meta.get("product_name") or meta.get("cc_product") or "").strip()
    if name:
        return name
    if kind == "coins":
        return f"{value} Critter Coins"
    if kind == "tier":
        return f"Supporter Tier: {value}"
    return "Donation"


def _verify_firebase_id_token(id_token: str) -> Optional[dict]:
    """Verify a Firebase ID token server-side and return its decoded claims
    (with 'uid' and, usually, 'email'), or None if missing/invalid. Used to
    prove account ownership on the claim + username endpoints — we never trust a
    raw uid from the client for those money/identity actions."""
    tok = str(id_token or "").strip()
    if not tok:
        return None
    if _get_firestore() is None:      # also initialises the firebase_admin app
        return None
    try:
        from firebase_admin import auth as fb_auth
        return fb_auth.verify_id_token(tok)
    except Exception as exc:  # noqa: BLE001
        print(f"[auth] Firebase ID token verify failed: {exc}")
        return None


def _verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """Verify a Stripe webhook signature WITHOUT the stripe SDK.

    The `Stripe-Signature` header looks like  't=<unix>,v1=<hex>,v1=<hex>,…'.
    We recompute HMAC-SHA256(secret, b"<t>." + raw_body) and constant-time
    compare it against each v1 signature, and reject events outside the
    timestamp tolerance. Returns False on any problem (no secret/header, bad
    timestamp, no matching signature) — i.e. we fulfil ONLY verified events."""
    if not secret or not sig_header:
        return False
    timestamp = None
    sigs: List[str] = []
    for part in sig_header.split(","):
        if "=" not in part:
            continue
        key, _, val = part.partition("=")
        key = key.strip()
        val = val.strip()
        if key == "t":
            timestamp = val
        elif key == "v1":
            sigs.append(val)
    if not timestamp or not sigs:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > STRIPE_SIG_TOLERANCE_SEC:
        return False  # too old / too far in the future → possible replay
    signed = timestamp.encode("utf-8") + b"." + payload
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)


def _reward_for_session(session: dict) -> Tuple[Optional[str], Any]:
    """Decide what a completed checkout grants. Returns:
         ("coins", <int amount>) | ("tier", "<tier-id>") | (None, None).
    Prefers explicit product metadata (set metadata.cc_coins / metadata.cc_tier
    on the Payment Link or Price if you want to decouple from the price), then
    falls back to the order amount in cents (USD only)."""
    meta = session.get("metadata") or {}
    if meta.get("cc_coins"):
        try:
            return ("coins", int(meta["cc_coins"]))
        except (TypeError, ValueError):
            pass
    if meta.get("cc_tier"):
        return ("tier", str(meta["cc_tier"]).strip().lower())
    # Amount fallback only trusts USD, because the cents tables are USD prices.
    if str(session.get("currency") or "").lower() != "usd":
        return (None, None)
    for cents in (session.get("amount_total"), session.get("amount_subtotal")):
        if isinstance(cents, int):
            if cents in COIN_PACKS_BY_CENTS:
                return ("coins", COIN_PACKS_BY_CENTS[cents])
            if cents in SUPPORTER_TIERS_BY_CENTS:
                return ("tier", SUPPORTER_TIERS_BY_CENTS[cents])
    return (None, None)


def _stripe_session_email(session: dict) -> str:
    cd = session.get("customer_details") or {}
    return str(cd.get("email") or session.get("customer_email") or "").strip()


def _process_stripe_checkout(event: dict) -> str:
    """Apply a verified checkout.session.completed event EXACTLY ONCE.

    A single Firestore transaction does everything for one paid checkout:
      • records the payment under the supporter (deduped by Stripe SESSION id —
        so a re-counted session never inflates totals),
      • adds amount_total to the supporter's LIFETIME total and recomputes their
        wall tier/size from that lifetime total,
      • credits Critter Coins / grants Supporter-Tier perks to a MATCHED game
        account,
      • files an unclaimed reward for a guest who has no account yet,
      • writes a stripe_events/{id} audit marker (a secondary idempotency key).
    The buyer is matched by client_reference_id (Firebase uid) → typed username
    → otherwise saved as a GUEST keyed by Stripe customer id (or session id).
    Returns a short status; raises on transient failures so the caller returns
    500 and Stripe retries (every write is idempotent). ONLY paid sessions are
    fulfilled."""
    session = (event.get("data") or {}).get("object") or {}

    # (6 / security) Only fulfil sessions Stripe actually collected payment for.
    payment_status = str(session.get("payment_status") or "")
    if payment_status != "paid":
        print(f"[stripe] session not paid (payment_status={payment_status!r}); ignoring.")
        return "unpaid"

    db = _get_firestore()
    if db is None:
        # No Firestore configured → raise so Stripe retries once it's back.
        raise RuntimeError("Firebase Admin not configured; cannot fulfil purchase.")

    # ── pull every field the spec names off the session ──────────────────────
    stripe_session_id = str(session.get("id") or "").strip()           # (1)
    if not stripe_session_id:
        print("[stripe] session missing id; ignoring.")
        return "no_session_id"
    event_id = str(event.get("id") or stripe_session_id).strip()
    firebase_uid = session.get("client_reference_id")                  # (2)
    firebase_uid = firebase_uid.strip()[:256] if isinstance(firebase_uid, str) and firebase_uid.strip() else ""
    stripe_customer_id = str(session.get("customer") or "").strip()    # (3)
    checkout_email = _stripe_session_email(session)                    # (4)
    amount_cents = session.get("amount_total")                         # (5)
    amount_cents = int(amount_cents) if isinstance(amount_cents, (int, float)) else 0

    # (7) the three custom questions, read by their EXACT Stripe labels.
    custom_fields = session.get("custom_fields")
    supporter_wall_name  = _custom_field_value(custom_fields, CF_WALL_NAME_LABEL)
    public_wall_choice   = _custom_field_value(custom_fields, CF_WALL_PUBLIC_LABEL)
    username_typed       = _custom_field_value(custom_fields, CF_USERNAME_LABEL).strip()
    username_typed_lower = username_typed.lower()

    kind, value = _reward_for_session(session)        # what was purchased
    product_name = _session_product_name(session, kind, value)

    # ── matching: client_reference_id → typed-username → guest ───────────────
    matched_uid = firebase_uid
    if not matched_uid and username_typed_lower:
        matched_uid = _find_uid_by_username(db, username_typed_lower) or ""

    # Public-name choice → displayName + anonymous flag.
    if _is_affirmative(public_wall_choice):
        anonymous = False
        display_name = supporter_wall_name.strip() or username_typed or "Supporter"
    else:
        anonymous = True
        display_name = "Anonymous"

    from firebase_admin import firestore
    # firebase-admin re-exports these from google.cloud.firestore; fall back to
    # the v1 module on SDK builds that don't (mirrors bump_firestore_games_played).
    transactional = getattr(firestore, "transactional", None)
    if transactional is None:
        from google.cloud.firestore_v1 import transactional
    ArrayUnion = getattr(firestore, "ArrayUnion", None)
    if ArrayUnion is None:
        from google.cloud.firestore_v1 import ArrayUnion
    SERVER_TIMESTAMP = getattr(firestore, "SERVER_TIMESTAMP", None)

    # Logged-in supporter vs guest supporter document paths.
    if matched_uid:
        supporter_ref = db.collection("supporters").document(matched_uid)
        guest_id = ""
    else:
        # Key a guest supporter by EMAIL so repeat purchases from the SAME buyer
        # accumulate onto ONE doc — their lifetime total (and their wall name)
        # keeps growing even though Stripe issues a fresh customer id for every
        # Payment-Link checkout. Email is safe as a Firestore doc id (no "/"),
        # and the claim flow already finds guests by emailLower. Falls back to
        # customer/session id only when Stripe collected no email.
        guest_id = (checkout_email.strip().lower() or stripe_customer_id or stripe_session_id)
        supporter_ref = db.collection("guestSupporters").document(guest_id)
    payment_ref  = supporter_ref.collection("payments").document(stripe_session_id)
    ev_ref       = db.collection("stripe_events").document(event_id)
    user_ref     = db.collection("users").document(matched_uid) if matched_uid else None
    founders_ref = db.collection("meta").document("founders")

    transaction = db.transaction()

    @transactional
    def _apply(txn) -> str:
        # ── ALL reads first (Firestore requires reads before any writes) ─────
        pay_snap  = payment_ref.get(transaction=txn)
        sup_snap  = supporter_ref.get(transaction=txn)
        user_snap = user_ref.get(transaction=txn) if user_ref is not None else None
        existing_user = (user_snap.to_dict() if user_snap is not None else None) or {}
        need_founder = (kind == "tier" and user_snap is not None and not existing_user.get("founder_number"))
        f_snap = founders_ref.get(transaction=txn) if need_founder else None

        # (Duplicate-payment prevention) This Stripe session is already counted →
        # no-op. Idempotent under Stripe's retries / duplicate deliveries.
        if pay_snap.exists:
            return "duplicate"

        prev = sup_snap.to_dict() or {}
        new_total = int(prev.get("totalSpentCents") or 0) + amount_cents   # lifetime
        new_count = int(prev.get("paymentCount") or 0) + 1
        tier_name, wall_size = _supporter_tier_for_total(new_total)        # by lifetime

        # (9 / auto-show + safety) A supporter who asked to be public and typed a
        # CLEAN name goes onto the wall IMMEDIATELY — no manual approval needed.
        #   • anonymous (chose "no")      → recorded but never shown on the wall.
        #   • name trips the blocklist    → held as pending_review + hidden until
        #                                    a human approves it in /supporter-admin.
        #   • repeat gift, same approved  → keeps its current wall placement so a
        #     name                          donor isn't pulled off on every gift.
        #   • clean public name           → approved + visible right now.
        if anonymous:
            status, visible = "approved", False
        elif (sup_snap.exists and prev.get("status") == "approved"
                and str(prev.get("displayName") or "") == display_name):
            status, visible = "approved", bool(prev.get("visible"))
        elif _name_needs_review(display_name):
            status, visible = "pending_review", False
        else:
            status, visible = "approved", True

        # ── writes ───────────────────────────────────────────────────────────
        # 1) the payment record — its existence is the dedup key.
        txn.set(payment_ref, {
            "stripeSessionId":  stripe_session_id,
            "stripeCustomerId": stripe_customer_id,
            "amountCents":      amount_cents,
            "paymentStatus":    payment_status,
            "productName":      product_name,
            "customFields": {
                "nameForSupporterReefWall":          supporter_wall_name,
                "showNamePublicly":                  public_wall_choice,
                "currentsAndCrittersOnlineUsername": username_typed,
            },
            "createdAt": SERVER_TIMESTAMP,
        })

        # 2) the supporter / guest doc.
        sup_doc: Dict[str, Any] = {
            "stripeCustomerId": stripe_customer_id,
            "email":            checkout_email,
            "emailLower":       checkout_email.lower(),   # case-insensitive claim match key
            "displayName":      display_name,
            "anonymous":        anonymous,
            "totalSpentCents":  new_total,
            "tier":             tier_name,
            "wallSize":         wall_size,
            "paymentCount":     new_count,
            "status":           status,
            "visible":          visible,
            "updatedAt":        SERVER_TIMESTAMP,
        }
        if not sup_snap.exists:
            sup_doc["createdAt"] = SERVER_TIMESTAMP
        if matched_uid:
            uname = existing_user.get("username") or username_typed
            sup_doc["firebaseUid"]   = matched_uid
            sup_doc["hasGameAccount"] = True
            sup_doc["username"]      = uname
            sup_doc["usernameLower"] = existing_user.get("usernameLower") or (uname or "").strip().lower()
        else:
            sup_doc["hasGameAccount"]    = False
            sup_doc["claimStatus"]       = prev.get("claimStatus") or "unclaimed"
            sup_doc["usernameTyped"]     = username_typed
            sup_doc["usernameTypedLower"] = username_typed_lower
        txn.set(supporter_ref, sup_doc, merge=True)

        # 3) credit the MATCHED game account (coins / tier perks). Cosmetic only.
        if matched_uid and kind is not None:
            stats = existing_user.get("stats") or {}
            updates: Dict[str, Any] = {}
            if kind == "coins":
                updates["stats"] = {"critter_coins": int(stats.get("critter_coins") or 0) + int(value)}
            elif kind == "tier":
                grant = SUPPORTER_TIER_GRANTS.get(value, {})
                stats_update: Dict[str, Any] = {"supporter_tier": value}
                bonus = int(grant.get("bonus_xp") or 0)
                if bonus:
                    new_xp = int(stats.get("total_xp") or 0) + bonus
                    stats_update["total_xp"] = new_xp
                    # Keep the derived level fields in lock-step with total_xp so
                    # the leaderboard and every other reader see the new level
                    # immediately (not only after the buyer's next game).
                    lvl, xp_cur, xp_goal = _level_progress_for_total_xp(new_xp)
                    stats_update["level"]            = lvl
                    stats_update["player_level"]     = lvl
                    stats_update["xp_current"]       = xp_cur
                    stats_update["level_xp_current"] = xp_cur
                    stats_update["xp_goal"]          = xp_goal
                    stats_update["level_xp_goal"]    = xp_goal
                updates["stats"] = stats_update
                updates["supporter_tier"] = value
                if need_founder:
                    fnum = int((f_snap.to_dict() or {}).get("count") or 0) + 1
                    txn.set(founders_ref, {"count": fnum}, merge=True)
                    updates["founder_number"] = fnum
                if grant.get("unlock_all_backgrounds"):
                    updates["unlocked_backgrounds"] = ArrayUnion(ALL_BACKGROUND_PATHS)
                icons = list(grant.get("icons") or [])
                if icons:
                    updates["unlocked_icons"] = ArrayUnion(icons)
            if updates:
                txn.set(user_ref, updates, merge=True)

        # 4) guest with a deliverable reward → file it to be claimed later.
        if not matched_uid and kind is not None:
            txn.set(db.collection("unclaimedRewards").document(stripe_session_id), {
                "stripeSessionId":  stripe_session_id,
                "stripeCustomerId": stripe_customer_id,
                "email":            checkout_email,
                "emailLower":       checkout_email.lower(),   # case-insensitive claim match key
                "usernameTyped":    username_typed,
                "amountCents":      amount_cents,
                "rewardName":       product_name,
                "rewardKind":       kind,
                "rewardValue":      value,
                "status":           "waiting_for_account",
                "createdAt":        SERVER_TIMESTAMP,
            }, merge=True)

        # 5) audit marker (also a secondary idempotency key on the event id).
        txn.set(ev_ref, {
            "event_id":     event_id,
            "session_id":   stripe_session_id,
            "uid":          matched_uid or None,
            "guest_id":     guest_id or None,
            "email":        checkout_email,
            "amount_total": amount_cents,
            "currency":     session.get("currency"),
            "kind":         kind,
            "value":        value,
            "matched":      bool(matched_uid),
            "processed_at": SERVER_TIMESTAMP,
        }, merge=True)

        return "fulfilled" if matched_uid else "recorded_guest"

    result = _apply(transaction)
    print(f"[stripe] checkout {stripe_session_id}: {result} "
          f"(uid={matched_uid or '-'}, guest={guest_id or '-'}, kind={kind}, value={value})")
    return result


# ══════════════════════════════════════════════════════════════════════════
#  SUPPORTER REEF WALL — public read, admin review, guest claim, usernames
# ══════════════════════════════════════════════════════════════════════════
_WALL_CACHE = {"at": 0.0, "data": None}
_WALL_TTL_SEC = 45.0


def _build_supporter_wall() -> List[Dict[str, Any]]:
    """Public wall rows: ONLY approved + visible supporters, and ONLY the three
    public fields (displayName / wallSize / tier). No emails, Stripe ids,
    Firebase uids, or payment history ever leave this function. Sorted by
    lifetime spend (used purely for ordering — the cents are NOT returned)."""
    db = _get_firestore()
    if db is None:
        return []
    out: List[Dict[str, Any]] = []
    for coll in ("supporters", "guestSupporters"):
        try:
            # Single equality filter is auto-indexed; we filter status in Python
            # so no composite index is required.
            snap = db.collection(coll).where("visible", "==", True).limit(2000).get()
        except Exception as exc:  # noqa: BLE001
            print(f"[wall] query {coll} failed: {exc}")
            continue
        for doc in snap:
            d = doc.to_dict() or {}
            if d.get("status") != "approved":
                continue
            cents = int(d.get("totalSpentCents") or 0)
            out.append({
                "displayName": str(d.get("displayName") or "Supporter"),
                "wallSize":    d.get("wallSize"),   # kept for the standalone /wall page
                "tier":        d.get("tier"),
                # LIFETIME total in cents — the homepage wall sizes each name
                # CONTINUOUSLY from this (every dollar = a bit bigger) and shows
                # it on hover. Exposed intentionally at the site owner's request.
                "amountCents": cents,
                "_sort":       cents,
            })
    out.sort(key=lambda r: r["_sort"], reverse=True)
    for r in out:
        r.pop("_sort", None)
    return out


def _supporter_wall_cached() -> List[Dict[str, Any]]:
    now = time.time()
    if _WALL_CACHE["data"] is not None and (now - _WALL_CACHE["at"]) < _WALL_TTL_SEC:
        return _WALL_CACHE["data"]
    data = _build_supporter_wall()
    _WALL_CACHE["data"] = data
    _WALL_CACHE["at"] = now
    return data


def _admin_list_supporters(filter_mode: str = "pending") -> Dict[str, Any]:
    """Admin-only review list. Includes private fields (email/ids) — this is ONLY
    served behind the admin key. filter_mode 'pending' shows records awaiting
    review; anything else returns everything."""
    db = _get_firestore()
    if db is None:
        return {"ok": False, "error": "firestore unavailable"}
    items: List[Dict[str, Any]] = []
    for coll, kind in (("supporters", "supporter"), ("guestSupporters", "guest")):
        try:
            snap = db.collection(coll).limit(3000).get()
        except Exception as exc:  # noqa: BLE001
            print(f"[admin] list {coll} failed: {exc}")
            continue
        for doc in snap:
            d = doc.to_dict() or {}
            if filter_mode == "pending" and d.get("status") != "pending_review":
                continue
            items.append({
                "kind":            kind,
                "id":              doc.id,
                "displayName":     d.get("displayName"),
                "anonymous":       d.get("anonymous"),
                "status":          d.get("status"),
                "visible":         d.get("visible"),
                "tier":            d.get("tier"),
                "wallSize":        d.get("wallSize"),
                "totalSpentCents": d.get("totalSpentCents"),
                "paymentCount":    d.get("paymentCount"),
                "email":           d.get("email"),
                "username":        d.get("username") or d.get("usernameTyped"),
                "hasGameAccount":  d.get("hasGameAccount"),
            })
    items.sort(key=lambda r: int(r.get("totalSpentCents") or 0), reverse=True)
    return {"ok": True, "supporters": items}


def _admin_update_supporter(kind: str, doc_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Admin edit of one wall record: displayName / status / visible."""
    db = _get_firestore()
    if db is None:
        return {"ok": False, "error": "firestore unavailable"}
    coll = "supporters" if kind == "supporter" else "guestSupporters"
    doc_id = str(doc_id or "").strip()
    if not doc_id:
        return {"ok": False, "error": "missing id"}
    from firebase_admin import firestore
    SERVER_TIMESTAMP = getattr(firestore, "SERVER_TIMESTAMP", None)
    update: Dict[str, Any] = {}
    if "displayName" in fields and fields["displayName"] is not None:
        update["displayName"] = str(fields["displayName"])[:120]
    if "status" in fields and fields["status"] is not None:
        st = str(fields["status"]).strip()
        if st not in ("approved", "rejected", "pending_review"):
            return {"ok": False, "error": "bad status"}
        update["status"] = st
    if "visible" in fields and fields["visible"] is not None:
        update["visible"] = bool(fields["visible"])
    if not update:
        return {"ok": False, "error": "nothing to update"}
    update["updatedAt"] = SERVER_TIMESTAMP
    try:
        db.collection(coll).document(doc_id).set(update, merge=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[admin] update {coll}/{doc_id} failed: {exc}")
        return {"ok": False, "error": "write failed"}
    _WALL_CACHE["data"] = None      # force the public wall to rebuild on next read
    return {"ok": True, "updated": list(update.keys())}


def _claim_username(uid: str, username: str) -> Dict[str, Any]:
    """Reserve a unique Currents & Critters username for a verified account.
    Validates (3–20 chars, letters/numbers/underscore), then claims
    usernames/{usernameLower} in a transaction so two people can't take the same
    name at once. Frees the account's previous reservation when it changes."""
    db = _get_firestore()
    if db is None:
        return {"ok": False, "error": "firestore unavailable"}
    uname = str(username or "").strip()
    if len(uname) < 3 or len(uname) > 20 or not re.match(r"^[A-Za-z0-9_]+$", uname):
        return {"ok": False, "error": "invalid",
                "message": "Use 3–20 letters, numbers, or underscores."}
    uname_lower = uname.lower()
    from firebase_admin import firestore
    transactional = getattr(firestore, "transactional", None)
    if transactional is None:
        from google.cloud.firestore_v1 import transactional
    SERVER_TIMESTAMP = getattr(firestore, "SERVER_TIMESTAMP", None)

    name_ref = db.collection("usernames").document(uname_lower)
    user_ref = db.collection("users").document(uid)
    transaction = db.transaction()

    @transactional
    def _apply(txn) -> str:
        name_snap = name_ref.get(transaction=txn)
        user_snap = user_ref.get(transaction=txn)
        if name_snap.exists and (name_snap.to_dict() or {}).get("uid") != uid:
            return "taken"
        old_lower = str((user_snap.to_dict() or {}).get("usernameLower") or "").strip().lower()
        # Free the account's previous reservation if it's changing names.
        if old_lower and old_lower != uname_lower:
            old_ref = db.collection("usernames").document(old_lower)
            old_snap = old_ref.get(transaction=txn)
            if old_snap.exists and (old_snap.to_dict() or {}).get("uid") == uid:
                txn.delete(old_ref)
        txn.set(name_ref, {
            "uid": uid, "username": uname, "usernameLower": uname_lower,
            "createdAt": SERVER_TIMESTAMP,
        }, merge=True)
        txn.set(user_ref, {"username": uname, "usernameLower": uname_lower}, merge=True)
        return "ok"

    res = _apply(transaction)
    if res == "taken":
        return {"ok": False, "error": "taken", "message": "That username is already taken."}
    return {"ok": True, "username": uname}


def _claim_guest_rewards(uid: str, verified_email: str) -> Dict[str, Any]:
    """Merge a guest's past Stripe payments + rewards into supporters/{uid}.

    Only guest records whose CHECKOUT email matches the claimer's verified email
    are pulled in. Each guest payment is copied into supporters/{uid}/payments
    ONLY if that Stripe session isn't already there — so no payment is ever
    double-counted, and re-running the claim is a safe no-op. Each move folds its
    cents into the supporter's lifetime total + tier in the SAME transaction;
    unclaimed coin rewards are credited, and the guest records are marked
    claimed."""
    db = _get_firestore()
    if db is None:
        return {"ok": False, "error": "firestore unavailable"}
    from firebase_admin import firestore
    transactional = getattr(firestore, "transactional", None)
    if transactional is None:
        from google.cloud.firestore_v1 import transactional
    SERVER_TIMESTAMP = getattr(firestore, "SERVER_TIMESTAMP", None)

    user_ref = db.collection("users").document(uid)
    user_snap = user_ref.get()
    if not user_snap.exists:
        return {"ok": False, "error": "no_account"}
    account = user_snap.to_dict() or {}
    email = str(verified_email or account.get("email") or "").strip().lower()
    if not email:
        return {"ok": False, "error": "no_email"}

    supporter_ref = db.collection("supporters").document(uid)

    # Gather guest supporters whose checkout email matches the verified email.
    # Match on the normalised emailLower so case differences ("John@x"/"john@x")
    # don't hide a payment. (verified_email is already lowercased into `email`.)
    guest_docs: Dict[str, Dict[str, Any]] = {}
    try:
        for doc in db.collection("guestSupporters").where("emailLower", "==", email).limit(50).get():
            guest_docs[doc.id] = doc.to_dict() or {}
    except Exception as exc:  # noqa: BLE001
        print(f"[claim] guest query failed: {exc}")

    claimed_payments = 0
    for gid, gdata in guest_docs.items():
        guest_ref = db.collection("guestSupporters").document(gid)
        try:
            pay_docs = list(guest_ref.collection("payments").limit(500).get())
        except Exception as exc:  # noqa: BLE001
            print(f"[claim] guest payments read failed: {exc}")
            pay_docs = []
        for pdoc in pay_docs:
            sid = pdoc.id
            pdata = pdoc.to_dict() or {}
            dest_ref = supporter_ref.collection("payments").document(sid)
            txn = db.transaction()

            # Move ONE guest payment and fold its cents into the supporter's
            # lifetime total + tier in the SAME transaction. The dest-payment
            # existence check is the dedup key, so a payment can never be counted
            # twice and re-running the claim (or a crash mid-claim) is safe.
            @transactional
            def _move(t, dest_ref=dest_ref, pdata=pdata, gid=gid, gdata=gdata) -> int:
                if dest_ref.get(transaction=t).exists:
                    return 0    # already under the supporter → never double-count
                ssnap = supporter_ref.get(transaction=t)
                usnap = user_ref.get(transaction=t)
                prev = ssnap.to_dict() or {}
                acct = usnap.to_dict() or {}
                amt = int(pdata.get("amountCents") or 0)
                new_total = int(prev.get("totalSpentCents") or 0) + amt
                tier_name, wall_size = _supporter_tier_for_total(new_total)
                sup: Dict[str, Any] = {
                    "firebaseUid":     uid,
                    "hasGameAccount":  True,
                    "email":           prev.get("email") or email,
                    "totalSpentCents": new_total,
                    "tier":            tier_name,
                    "wallSize":        wall_size,
                    "paymentCount":    int(prev.get("paymentCount") or 0) + 1,
                    "updatedAt":       SERVER_TIMESTAMP,
                }
                if not ssnap.exists:
                    # First time this account becomes a supporter (they paid as a
                    # guest before linking): seed it pending + hidden for review,
                    # honouring the guest's public-name choice.
                    sup["createdAt"]     = SERVER_TIMESTAMP
                    sup["status"]        = "pending_review"
                    sup["visible"]       = False
                    sup["displayName"]   = gdata.get("displayName") or "Supporter"
                    sup["anonymous"]     = bool(gdata.get("anonymous", True))
                    sup["username"]      = acct.get("username") or ""
                    sup["usernameLower"] = acct.get("usernameLower") or ""
                t.set(dest_ref, {**pdata, "claimedFromGuest": gid}, merge=True)
                t.set(supporter_ref, sup, merge=True)
                return amt

            try:
                amt = _move(txn)
            except Exception as exc:  # noqa: BLE001
                print(f"[claim] move payment {sid} failed: {exc}")
                amt = 0
            if amt:
                claimed_payments += 1
        try:
            guest_ref.set({"claimStatus": "claimed", "claimedByUid": uid,
                           "updatedAt": SERVER_TIMESTAMP}, merge=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[claim] mark guest {gid} failed: {exc}")

    # Credit unclaimed coin/tier rewards filed against this email.
    coins_credited = 0
    try:
        rewards = list(db.collection("unclaimedRewards").where("emailLower", "==", email).limit(100).get())
    except Exception as exc:  # noqa: BLE001
        print(f"[claim] rewards query failed: {exc}")
        rewards = []
    for rdoc in rewards:
        rid = rdoc.id
        rdata = rdoc.to_dict() or {}
        if rdata.get("status") == "claimed":
            continue
        reward_ref = db.collection("unclaimedRewards").document(rid)
        txn = db.transaction()

        @transactional
        def _claim_reward(t, reward_ref=reward_ref, rdata=rdata) -> int:
            rsnap = reward_ref.get(transaction=t)
            if not rsnap.exists or (rsnap.to_dict() or {}).get("status") == "claimed":
                return 0
            usnap = user_ref.get(transaction=t)
            stats = (usnap.to_dict() or {}).get("stats") or {}
            credited = 0
            if rdata.get("rewardKind") == "coins":
                credited = int(rdata.get("rewardValue") or 0)
                t.set(user_ref, {"stats": {"critter_coins": int(stats.get("critter_coins") or 0) + credited}}, merge=True)
            elif rdata.get("rewardKind") == "tier":
                t.set(user_ref, {"supporter_tier": rdata.get("rewardValue")}, merge=True)
            t.set(reward_ref, {"status": "claimed", "claimedByUid": uid,
                               "claimedAt": SERVER_TIMESTAMP}, merge=True)
            return credited

        try:
            coins_credited += _claim_reward(txn)
        except Exception as exc:  # noqa: BLE001
            print(f"[claim] reward {rid} failed: {exc}")

    if not guest_docs and not rewards:
        return {"ok": True, "claimedPayments": 0, "coinsCredited": 0,
                "message": "No guest payments found for that email."}

    _WALL_CACHE["data"] = None
    return {"ok": True, "claimedPayments": claimed_payments,
            "coinsCredited": coins_credited,
            "message": f"Claimed {claimed_payments} payment(s)."}


BRAIN_LOCK = threading.Lock()
DATASET_LOCK = threading.Lock()
HISTORY_LOCK = threading.Lock()
COMPETITIVE_LOCK = threading.Lock()
STATS_LOCK = threading.Lock()
PUBLIC_BASE_URL = ""
LAN_IP_CACHE: Optional[str] = None
ACTIVE_SERVER: Optional[StableThreadingHTTPServer] = None
CREATE_KEY = ""
CORS_ALLOW_ORIGIN = os.environ.get("FISH_CORS_ALLOW_ORIGIN", "*").strip() or "*"
PUBLIC_LINKS_PATH = (
    str(os.environ.get("FISH_PUBLIC_LINKS_PATH", os.path.join(BASE_DIR, "multiplayer", "public_links.json"))).strip()
    or os.path.join(BASE_DIR, "multiplayer", "public_links.json")
)
MAX_JSON_BODY_BYTES = 128 * 1024
ROOM_CHECKPOINT_SCHEMA_VERSION = 1
ROOM_PERSIST_MIN_INTERVAL_SEC = 0.75
MAX_ACTION_HISTORY = 16000
ROOM_ID_LENGTH = 5
# A player who leaves a running game keeps their seat RESERVED for this long;
# only they (they hold the seat token) can rejoin it, into the same seat.
REJOIN_WINDOW_SEC = 8 * 60
# A home-screen Quick Play client polls its queued room every few seconds.
# Ignore abandoned one-player queues after this window so a new player is
# never matched with a tab that has been closed or disconnected.
QUICK_PLAY_STALE_SECONDS = 3 * 60
# Room codes are 4–12 uppercase letters/numbers. Public rooms use a random
# 5-char code; private rooms use the host's chosen code (which is also the
# password). Both create and restore accept the full 4–12 range.
ROOM_ID_RE = re.compile(r"[A-Z0-9]{4,12}")
ROOM_ID_ACCEPT_RE = re.compile(r"[A-Z0-9]{4,12}")

CLIENT_DIR = os.path.join(BASE_DIR, "multiplayer", "client")
MANIFEST_PATH = os.path.join(CLIENT_DIR, "manifest.webmanifest")
VERSION_JSON_PATH = os.path.join(CLIENT_DIR, "version.json")
SERVICE_WORKER_PATH = os.path.join(CLIENT_DIR, "sw.js")
ICON_PATH = os.path.join(CLIENT_DIR, "icon.svg")
PLAYER_HOME_REFERENCE_PATH = os.path.join(CLIENT_DIR, "player-home-reference.jpg")
PLAYER_HOME_AVATAR_PATH = os.path.join(CLIENT_DIR, "player-home-avatar.jpg")
PLAYER_HOME_FRIEND_TWIN_PATH = os.path.join(CLIENT_DIR, "player-home-friend-twin.jpg")
PLAYER_HOME_FRIEND_MOM_PATH = os.path.join(CLIENT_DIR, "player-home-friend-mom.jpg")
AVATAR_DIR = os.path.join(CLIENT_DIR, "avatars")


def _shuffle_deck_keep_end_bottom15(gs, ms) -> None:
    """Shuffle gs.deck for anti-peek (used on undo) WITHOUT scattering the END
    GAME card out of the bottom 15. Cards are drawn from the FRONT (pop(0)), so
    the "bottom" is the END of the list. END GAME is removed, the rest is
    shuffled, then END GAME is re-inserted at a random position within the last
    15 cards — identical to the engine's authoritative placement in run_match.
    A plain random.shuffle(gs.deck) would move END GAME anywhere, which made the
    game end far too early (e.g. END drawn with 53 cards left) after an undo.
    """
    try:
        deck = gs.deck
        eg = getattr(ms, "end_game_uid", None)
        if eg is not None and eg in deck:
            deck.remove(eg)
            random.shuffle(deck)
            span = min(14, len(deck))
            pos = len(deck) - random.randint(0, span)
            deck.insert(pos, eg)
        else:
            random.shuffle(deck)
    except Exception:
        # Never let a shuffle helper crash the undo path.
        try:
            random.shuffle(gs.deck)
        except Exception:
            pass


def room_state_path(room_id: str) -> str:
    safe = re.sub(r"[^A-Z0-9]", "", str(room_id or "").upper())
    if not safe:
        safe = "UNKNOWN"
    return os.path.join(ROOM_STATE_DIR, f"{safe}.json")


def atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"), ensure_ascii=True)
    os.replace(tmp_path, path)


def remove_room_state_file(room_id: str) -> None:
    try:
        os.remove(room_state_path(room_id))
    except FileNotFoundError:
        return
    except Exception:
        return


def list_room_state_files() -> List[str]:
    try:
        names = os.listdir(ROOM_STATE_DIR)
    except FileNotFoundError:
        return []
    except Exception:
        return []
    out: List[str] = []
    for name in names:
        if not str(name).endswith(".json"):
            continue
        out.append(os.path.join(ROOM_STATE_DIR, str(name)))
    out.sort()
    return out


def json_dumps(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def now_unix() -> int:
    return int(time.time())


def get_season_id(ts: Optional[int] = None) -> str:
    """Return quarterly season ID string like '2026-Q2' for the given unix timestamp (or now)."""
    import datetime
    dt = datetime.datetime.utcfromtimestamp(ts if ts is not None else time.time())
    q = (dt.month - 1) // 3 + 1
    return f"{dt.year}-Q{q}"


def clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    if out < lo:
        return lo
    if out > hi:
        return hi
    return out


def clamp_int_or_none(value: Any, lo: int, hi: int) -> Optional[int]:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    if out < lo or out > hi:
        return None
    return out


def int_list(raw: Any, cap: int = 64) -> List[int]:
    out: List[int] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, int):
            out.append(int(item))
            if len(out) >= cap:
                break
    return out


def room_code(length: int = ROOM_ID_LENGTH) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def safe_name(name: Any, fallback: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return fallback
    cleaned = " ".join(raw.split())
    if len(cleaned) > 32:
        cleaned = cleaned[:32].rstrip()
    return cleaned or fallback


def _split_host_port(host_header: str) -> tuple[str, str]:
    raw = (host_header or "").strip()
    if not raw:
        return "127.0.0.1", "8777"
    if raw.startswith("[") and "]" in raw:
        close = raw.find("]")
        host = raw[1:close]
        rest = raw[close + 1 :]
        if rest.startswith(":"):
            return host, rest[1:] or "8777"
        return host, "8777"
    if ":" in raw:
        host, port = raw.rsplit(":", 1)
        if port.isdigit():
            return host, port
    return raw, "8777"


def _host_header_has_explicit_port(host_header: str) -> bool:
    raw = (host_header or "").strip()
    if not raw:
        return False
    if raw.startswith("[") and "]" in raw:
        close = raw.find("]")
        rest = raw[close + 1 :]
        return rest.startswith(":") and rest[1:].isdigit()
    if raw.count(":") == 1:
        _, port = raw.rsplit(":", 1)
        return port.isdigit()
    return False


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return (
        h in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
        or h.startswith("127.")
        or h == ""
    )


def detect_lan_ipv4() -> Optional[str]:
    global LAN_IP_CACHE
    if LAN_IP_CACHE:
        return LAN_IP_CACHE

    # Fast local fallback from hostname resolution.
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET)
        for info in infos:
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                LAN_IP_CACHE = ip
                return ip
    except Exception:
        pass

    # Mac/Linux fallback: parse interface output.
    try:
        proc = subprocess.run(
            ["ifconfig"],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.5,
        )
        text = proc.stdout or ""
        current_iface = ""
        current_active = False
        for line in text.splitlines():
            if not line.startswith("\t") and ":" in line:
                current_iface = line.split(":", 1)[0].strip()
                current_active = False
                continue
            s = line.strip()
            if s == "status: active":
                current_active = True
                continue
            if s.startswith("inet "):
                parts = s.split()
                if len(parts) >= 2:
                    ip = parts[1]
                    if ip.startswith("127."):
                        continue
                    if current_active and current_iface and current_iface != "lo0":
                        LAN_IP_CACHE = ip
                        return ip
    except Exception:
        pass
    return None


def share_base_url(host_header: str, proto_hint: str = "") -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/")

    host, port = _split_host_port(host_header)
    # Current serveousercontent cert is expired; use HTTP so links still open.
    if host.endswith(".serveousercontent.com"):
        return f"http://{host}"
    if host.endswith(".lhr.life"):
        return f"https://{host}"

    hinted = (proto_hint or "").strip().lower()
    if hinted in {"http", "https"}:
        if _host_header_has_explicit_port(host_header):
            return f"{hinted}://{host}:{port}"
        return f"{hinted}://{host}"

    if _is_loopback_host(host):
        lan_ip = detect_lan_ipv4()
        if lan_ip:
            return f"http://{lan_ip}:{port}"
        return f"http://{host}:{port}"

    # Reverse-tunnel hosts often omit explicit ports and are HTTPS terminated.
    if not _host_header_has_explicit_port(host_header):
        return f"https://{host}"
    if port == "443":
        return f"https://{host}"
    if port == "80":
        return f"http://{host}"
    return f"http://{host}:{port}"


def normalize_public_url(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    try:
        parsed = urlparse(s)
    except Exception:
        return ""
    scheme = (parsed.scheme or "").lower().strip()
    if scheme not in {"http", "https"}:
        return ""
    host = (parsed.netloc or "").strip()
    if not host:
        return ""
    return f"{scheme}://{host}"


def load_public_links() -> List[str]:
    out: List[str] = []

    def _add(raw: Any) -> None:
        url = normalize_public_url(raw)
        if not url:
            return
        if url in out:
            return
        out.append(url)

    _add(PUBLIC_BASE_URL)
    try:
        with open(PUBLIC_LINKS_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return out

    if isinstance(payload, dict):
        candidates = payload.get("public_urls")
        if not isinstance(candidates, list):
            candidates = payload.get("urls")
        if not isinstance(candidates, list):
            candidates = payload.get("links")
    elif isinstance(payload, list):
        candidates = payload
    else:
        candidates = []

    for raw in candidates:
        _add(raw)
        if len(out) >= 12:
            break
    return out


def _missing_card_payload(uid: int) -> Dict[str, Any]:
    return {
        "uid": int(uid),
        "name": "[missing card]",
        "species": "unknown",
        "cost": 0,
        "direction": "n/a",
        "symbol": "n/a",
        "text": "Card data missing from current card_db.",
    }


def _card_payload_safe(gs: fish.GameState, uid: int) -> Dict[str, Any]:
    card = gs.card_db.get(uid)
    if card is None:
        return _missing_card_payload(uid)
    return fish.card_to_dict(card)


def entry_to_dict(ms: fish.MatchState, gs: fish.GameState, entry_uid: int) -> Dict[str, Any]:
    try:
        entry_id = int(entry_uid)
    except Exception:
        entry_id = -1
    try:
        faces = fish.entry_faces(ms, entry_uid)
    except Exception:
        faces = [entry_uid] if isinstance(entry_uid, int) else []
    try:
        label = fish.entry_short_label(ms, gs, entry_uid)
    except Exception:
        label = f"{entry_id}:[invalid entry]" if entry_id >= 0 else "[invalid entry]"
    return {
        "entry_uid": entry_id,
        "label": label,
        "faces": [_card_payload_safe(gs, int(uid)) for uid in faces if isinstance(uid, int)],
    }


# ── Current Controller: admin card minting + full catalog ────────────────────
# The admin "give any card" / "flood a hand" tools create brand-new physical
# copies of a card on the fly. Each minted copy gets a globally-unique uid so it
# can never collide with another game's cards in the process-wide ability
# registry. The uid encodes the original art face in its low 3 digits
# (uid = serial * 1000 + face_uid), so the client's imagePathForUid() /
# cardHalfPos() render the correct sprite for a minted card with no extra
# bookkeeping — anywhere a normal card is drawn (hand, board, zoom, picker).
_MINT_SERIAL_LOCK = threading.Lock()
_MINT_SERIAL_NEXT = 1000  # → minted uids are 1_000_000+ (originals are ≤ 269)


def _alloc_mint_serial() -> int:
    global _MINT_SERIAL_NEXT
    with _MINT_SERIAL_LOCK:
        s = _MINT_SERIAL_NEXT
        _MINT_SERIAL_NEXT += 1
        return s


_ADMIN_CATALOG_CACHE: Optional[List[Dict[str, Any]]] = None


def build_admin_card_catalog() -> List[Dict[str, Any]]:
    """Every card in the game — both faces of each two-sided card (the left+right
    and up+down orientations) plus single-face oceans — regardless of where the
    copies currently sit. Powers the Current Controller add/mint pickers so the
    admin can see and grant EVERY card, not just what is left in the live deck."""
    global _ADMIN_CATALOG_CACHE
    if _ADMIN_CATALOG_CACHE is not None:
        return _ADMIN_CATALOG_CACHE
    card_db = CARD_DB
    pair_primary_to_faces, face_to_primary = fish.build_non_ocean_pair_maps(card_db)
    tmp_ms = fish.MatchState(
        pair_primary_to_faces=pair_primary_to_faces, face_to_primary=face_to_primary
    )
    tmp_gs = fish.GameState(card_db=card_db, players=[], deck=[])
    seen: set = set()
    entries: List[Dict[str, Any]] = []
    for uid in sorted(card_db.keys()):
        prim = int(face_to_primary.get(uid, uid))
        if prim in seen:
            continue
        seen.add(prim)
        entries.append(entry_to_dict(tmp_ms, tmp_gs, prim))
    _ADMIN_CATALOG_CACHE = entries
    return entries


def board_to_dict(player: fish.PlayerState, gs: fish.GameState) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for ocean_uid in player.board_oceans:
        try:
            ocean_id = int(ocean_uid)
        except Exception:
            continue
        slots = player.ocean_slots.get(ocean_uid)
        if not isinstance(slots, fish.OceanSlots):
            slots = fish.OceanSlots()
        up = [_card_payload_safe(gs, int(uid)) for uid in list(getattr(slots, "up", [])) if isinstance(uid, int)]
        down = [_card_payload_safe(gs, int(uid)) for uid in list(getattr(slots, "down", [])) if isinstance(uid, int)]
        left = [_card_payload_safe(gs, int(uid)) for uid in list(getattr(slots, "left", [])) if isinstance(uid, int)]
        right = [_card_payload_safe(gs, int(uid)) for uid in list(getattr(slots, "right", [])) if isinstance(uid, int)]
        payload.append(
            {
                "ocean_uid": ocean_id,
                "ocean": _card_payload_safe(gs, ocean_id),
                "up": up,
                "down": down,
                "left": left,
                "right": right,
            }
        )
    return payload


def choose_action_weighted_light(
    gs: fish.GameState,
    ms: fish.MatchState,
    player: fish.PlayerState,
    weights: Dict[str, float],
    synergy_map: Dict[str, float],
    species_map: Dict[str, float],
    same_ocean_map: Dict[str, float],
    strategy_value_map: Dict[str, float],
    strategy_count_map: Dict[str, int],
    strategy_transition_map: Dict[str, float],
    strategy_transition_count_map: Dict[str, int],
    out_scored: Optional[List["tuple[fish.Action, float]"]] = None,
) -> Optional[fish.Action]:
    """
    Lightweight AI chooser for live multiplayer.
    Avoids deep simulated lookahead to keep turns responsive and avoid
    constant simulation-heavy evaluation.

    If out_scored is provided, the full (action, score) list (sorted best-first)
    is copied into it — used by the Current Controller's Bot Brain Viewer.
    """
    acts = fish.candidate_actions_for_ai(gs, ms, player)
    acts = fish.filter_overbuild_ocean_actions(gs, ms, player, acts)
    non_dead = [a for a in acts if not fish.action_is_dead_engine_play(gs, ms, player, a)]
    if non_dead:
        acts = non_dead
    if not acts:
        return None

    # Per-bot difficulty knobs (set when the game launches; defaults if missing).
    strategy_mult = float(player.flags.get("_ai_strategy_weight", 1.0) or 1.0)
    explore_chance = float(player.flags.get("_ai_explore_chance", 0.0) or 0.0)

    scored: List[tuple[fish.Action, float]] = []
    for action in acts:
        feats = fish.action_features(
            gs,
            ms,
            player,
            action,
            synergy_map=synergy_map,
            species_map=species_map,
            same_ocean_map=same_ocean_map,
            include_sim_delta=False,  # key: no per-action simulation
        )
        score = fish.weighted_score(feats, weights)
        strategy_v, novelty_v, branch_v, _ = fish.strategy_signal(
            gs,
            ms,
            player,
            action,
            strategy_value_map=strategy_value_map,
            strategy_count_map=strategy_count_map,
            strategy_transition_map=strategy_transition_map,
            strategy_transition_count_map=strategy_transition_count_map,
        )
        score += weights.get("strategy_bonus", 0.0) * strategy_v
        score += weights.get("novelty_bonus", 0.0) * novelty_v
        score += weights.get("branch_bonus", 0.0) * branch_v
        score += fish.action_engine_timing_bonus(gs, ms, player, action)
        score += fish.human_realism_action_adjustment(gs, ms, player, action, feats)
        # Strategy + opponent-awareness signal (scaled by per-bot difficulty).
        # action_archetype_bonus internally pulls _strategy_family and
        # _opp_snapshot from player.flags, scores how well the action fits
        # the chosen strategy, and adds blocking value for draws.
        score += strategy_mult * fish.action_archetype_bonus(gs, ms, player, action, None)
        # Board-fit: rewards plays that build on the current board's plan
        # (e.g. dropping baitfish into an ocean already stacked with baitfish).
        score += strategy_mult * fish.action_plan_fit_bonus(gs, player, action)
        # Future value: rewards plays that set up scoring on later turns,
        # not just this turn. Makes medium/hard bots think ahead.
        score += strategy_mult * fish.action_future_value_bonus(gs, ms, player, action)
        scored.append((action, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    if out_scored is not None:
        out_scored.clear()
        out_scored.extend(scored)

    # Easy bots occasionally pick a near-best action instead of the best one
    # so they feel beatable. Hard bots always lock onto the top score.
    if explore_chance > 0.0 and len(scored) > 1 and random.random() < explore_chance:
        # Pick from the top 3 with mild softmax-like preference for higher scores.
        topk = scored[: min(3, len(scored))]
        weights_pick = [max(0.05, s) for _, s in topk]
        total = sum(weights_pick) or 1.0
        r = random.random() * total
        acc = 0.0
        chosen_idx = 0
        for i, w in enumerate(weights_pick):
            acc += w
            if r <= acc:
                chosen_idx = i
                break
        best = topk[chosen_idx][0]
    else:
        best = scored[0][0]

    # Keep board-first behavior if a draw only barely wins.
    if best.kind == "draw":
        play_opts = [(a, s) for a, s in scored if a.kind != "draw"]
        if play_opts:
            best_play, best_play_score = max(play_opts, key=lambda x: x[1])
            if best_play_score >= scored[0][1] - 0.6:
                return best_play
    return best


# Kill switch for the deep-planning layer (rollout confirmation). Set
# FISH_DEEP_BOTS=0 to fall back to the light one-pass chooser everywhere.
_DEEP_BOTS_ENABLED = str(os.environ.get("FISH_DEEP_BOTS", "1")).strip().lower() not in {
    "0", "false", "no", "off",
}

# Per-move wall-clock budget (seconds) for rollout confirmation, by difficulty.
# Candidates are confirmed best-first, so running out of budget just means the
# weakest shortlist entries keep their one-pass score.
_DEEP_PLAN_TIME_BUDGET: Dict[str, float] = {
    "medium": 1.2,
    "hard": 2.2,
}


def choose_action_weighted_deep(
    gs: fish.GameState,
    ms: fish.MatchState,
    player: fish.PlayerState,
    weights: Dict[str, float],
    synergy_map: Dict[str, float],
    species_map: Dict[str, float],
    same_ocean_map: Dict[str, float],
    strategy_value_map: Dict[str, float],
    strategy_count_map: Dict[str, int],
    strategy_transition_map: Dict[str, float],
    strategy_transition_count_map: Dict[str, int],
    out_scored: Optional[List["tuple[fish.Action, float]"]] = None,
) -> Optional[fish.Action]:
    """
    Deep AI chooser for live multiplayer: the light one-pass scorer picks a
    shortlist, then each shortlisted move is CONFIRMED by actually simulating
    it — the move, its follow-ups, one likely reply turn from every opponent,
    and two of our own future turns — scored with the real scoring function.

    Rollouts are determinized (opponents' hidden hands and the deck order are
    reshuffled from the unseen-card pool per sample), so the bot plans like a
    very strong human: full rules understanding, no peeking. Multiple sampled
    worlds are averaged so the bot reasons about what's LIKELY, not one lucky
    layout. Easy bots (plan_candidates=0) skip all of this.
    """
    plan_candidates = int(player.flags.get("_ai_plan_candidates", 0) or 0)
    plan_samples = int(player.flags.get("_ai_plan_samples", 0) or 0)
    confirm_weight = float(player.flags.get("_ai_confirm_weight", 0.0) or 0.0)
    deep_enabled = (
        _DEEP_BOTS_ENABLED
        and plan_candidates > 0
        and plan_samples > 0
        and confirm_weight > 0.0
    )

    # Roll the per-difficulty "human slip" here (not in the light pass) so a
    # light-chooser draw-guard pick is never mistaken for a deliberate slip and
    # deep confirmation only skips when the slip genuinely fired.
    explore_chance = float(player.flags.get("_ai_explore_chance", 0.0) or 0.0)
    slip_fired = deep_enabled and explore_chance > 0.0 and random.random() < explore_chance
    saved_explore = player.flags.get("_ai_explore_chance")
    if deep_enabled and not slip_fired:
        player.flags["_ai_explore_chance"] = 0.0

    scored: List["tuple[fish.Action, float]"] = []
    try:
        base_best = choose_action_weighted_light(
            gs,
            ms,
            player,
            weights,
            synergy_map,
            species_map,
            same_ocean_map,
            strategy_value_map,
            strategy_count_map,
            strategy_transition_map,
            strategy_transition_count_map,
            out_scored=scored,
        )
    finally:
        if deep_enabled and not slip_fired:
            player.flags["_ai_explore_chance"] = saved_explore
    if out_scored is not None:
        out_scored.clear()
        out_scored.extend(scored)
    if base_best is None or not scored:
        return base_best
    if not deep_enabled or slip_fired or len(scored) < 2:
        return base_best

    difficulty = str(player.flags.get("_ai_difficulty", "medium")).strip().lower()
    budget = _DEEP_PLAN_TIME_BUDGET.get(difficulty, 1.2)
    deadline = time.monotonic() + budget

    shortlist_n = min(plan_candidates, len(scored))
    shortlist = list(scored[:shortlist_n])
    # Diversity guard: the one-pass scorer systematically over-ranks draws, so
    # a great play can sit below the shortlist cutoff and never be confirmed.
    # Guarantee real plays a seat at the rollout table.
    if len(scored) > shortlist_n:
        in_short = {id(a) for a, _ in shortlist}
        want_plays = max(2, shortlist_n // 3)
        n_plays = sum(1 for a, _ in shortlist if a.kind != "draw")
        if n_plays < want_plays:
            extra = [
                (a, s) for a, s in scored[shortlist_n:]
                if a.kind != "draw" and id(a) not in in_short
            ]
            shortlist.extend(extra[: want_plays - n_plays])
    blended: List["tuple[fish.Action, float]"] = []
    for action, base_score in shortlist:
        if time.monotonic() >= deadline and blended:
            # Out of budget: leave the rest unconfirmed. Their raw one-pass
            # scores aren't on the blended scale, so ranking them together
            # would be apples-to-oranges; they were lower-ranked anyway.
            break
        confirm_total = 0.0
        samples_run = 0
        for _ in range(plan_samples):
            confirm_total += fish.double_check_action_score(
                gs,
                ms,
                player,
                action,
                weights,
                synergy_map=synergy_map,
                species_map=species_map,
                same_ocean_map=same_ocean_map,
                strategy_value_map=strategy_value_map,
                strategy_count_map=strategy_count_map,
                strategy_transition_map=strategy_transition_map,
                strategy_transition_count_map=strategy_transition_count_map,
                archetype_profile=None,
                determinize_rng=random.Random(random.getrandbits(64)),
            )
            samples_run += 1
            if time.monotonic() >= deadline:
                break
        confirm = confirm_total / max(1, samples_run)
        blended.append(
            (action, base_score * (1.0 - confirm_weight) + confirm * confirm_weight)
        )

    blended.sort(key=lambda x: x[1], reverse=True)
    if out_scored is not None:
        # Show the confirmed ranking in the Bot Brain Viewer: blended totals for
        # the shortlist, then the unconfirmed remainder in one-pass order.
        confirmed_ids = {id(a) for a, _ in blended}
        out_scored.clear()
        out_scored.extend(blended)
        out_scored.extend((a, s) for a, s in scored if id(a) not in confirmed_ids)

    best_action, best_total = blended[0]
    # Board-first guard on confirmed totals: a hoarding draw must clearly beat
    # the best real play, mirroring the light chooser's anti-over-draft rule.
    if best_action.kind == "draw":
        play_opts = [(a, t) for a, t in blended if a.kind != "draw"]
        if play_opts:
            best_play, best_play_total = max(play_opts, key=lambda x: x[1])
            if best_play_total >= best_total - 0.25:
                return best_play
    return best_action


@dataclass
class Seat:
    index: int
    kind: str  # human | ai
    label: str
    claimed_name: Optional[str] = None
    token: Optional[str] = None
    is_host: bool = False
    # The player's chosen avatar image path (e.g. "/avatars/clownfish.png").
    # Carried per-seat in game state so every client renders each player's
    # OWN icon — no shared nickname lookup, so two players never share one.
    avatar: Optional[str] = None
    # The player's equipped exclusive background (e.g. "/backgrounds/bg-kelp.png"),
    # rendered behind their avatar on every seat. Empty/None = no background.
    background: Optional[str] = None
    difficulty: str = "medium"  # easy | medium | hard (only meaningful for ai seats)
    # Surf's Up! — player explicitly marked themselves Away. Turn pauses
    # indefinitely on this seat; other seats cannot draw for them.
    is_away: bool = False
    # Set true by the client after the 5-min idle + 30-sec warning expires
    # without any activity; unlocks the "Draw 2 Cards" affordance on the
    # avatar for other seats to use. Cleared on activity / turn change.
    inactive_eligible: bool = False
    # Unix time the player left a RUNNING game. The seat is then RESERVED: its
    # token + name are kept so only that player (who holds the seat token) can
    # rejoin their exact seat, for REJOIN_WINDOW_SEC seconds. Cleared when they
    # reconnect; the seat is freed by cleanup once the window expires.
    left_at: Optional[float] = None
    # Unix time this seat's token last polled state. Used by the competitive
    # forfeit check to detect a player who left/closed/crashed (their client
    # stops polling) — independent of whether they clicked "Leave".
    last_seen: Optional[float] = None
    # Post-game "Play Again" ready-up flag. Set true when this seat clicks Play
    # Again on the end screen; cleared on every fresh game launch. When all
    # active human seats are ready, the game auto-restarts (bots ready implicitly).
    play_again_ready: bool = False
    # Client-generated idempotency key for dedicated Quick Play matchmaking.
    # It prevents a retried request from claiming a second seat.
    quick_play_ticket: Optional[str] = None
    # Team Mode only: which team this seat belongs to (0=Red, 1=Blue, 2=Green,
    # 3=Yellow). None for non-team games. Assigned round-robin at room creation
    # and freely changed in the lobby via the /team and /swap endpoints.
    team: Optional[int] = None

    def status(self) -> str:
        if self.kind == "ai":
            return "ai"
        return "taken" if self.token else "available"


class GameRoom:
    def __init__(
        self,
        room_id: str,
        host_name: str,
        total_players: int,
        human_players: int,
        ai_players: int,
        competitive: bool = False,
        visibility: str = "public",
        password_hash: Optional[str] = None,
        tutorial: bool = False,
        tutorial_variant: Optional[str] = None,
        quick_play: bool = False,
        team_mode: bool = False,
        team_count: int = 2,
    ) -> None:
        self.room_id = room_id
        self.total_players = total_players
        self.human_players = human_players
        self.ai_players = ai_players
        self.competitive = competitive
        self.quick_play = bool(quick_play)
        # Team Mode: a normal game played in teams. team_count teams (2..4) are
        # opened; seats are assigned round-robin so Red/Blue start evenly split
        # (bots included). Players re-team freely in the lobby. swap_requests
        # holds pending cross-team swap offers awaiting the target's consent.
        self.team_mode = bool(team_mode)
        self.team_count = max(2, min(4, int(team_count))) if team_mode else 2
        self.swap_requests: List[Dict[str, Any]] = []
        # Tutorial games rig the human's opening hand so the guided "play an
        # ocean, then two creatures" walkthrough always works. Never set for
        # normal matches. ``tutorial_variant`` selects which rig: None/"" = the
        # default "The Game" hand; "blob" = the B-Lob Strategy tutorial hand.
        self.is_tutorial = bool(tutorial)
        self.tutorial_variant = (str(tutorial_variant).strip().lower() or None) if tutorial_variant else None
        self.visibility = visibility  # "public" | "private"
        self.password_hash = password_hash  # sha256 hex, None if public
        self._competitive_saved = False
        # Set when a competitive match ends because a player left and did not
        # return within COMPETITIVE_FORFEIT_SEC. Surfaced in the state payload so
        # the remaining player's client records the win (and the right loser).
        self._forfeit_result: Optional[Dict[str, Any]] = None
        # Competitive turn-order remapping: game_engine_idx → seat_idx
        # Seats go 0,2,1,3 so turns alternate P1h1,P2h1,P1h2,P2h2
        self._comp_game_to_seat: Dict[int, int] = {}
        self._comp_seat_to_game: Dict[int, int] = {}
        self.seed = secrets.randbits(64)
        self.created_unix = now_unix()
        self.started_unix: Optional[int] = None
        self.ended_unix: Optional[int] = None

        self.cond = threading.Condition()
        self.state_version = 1
        self.phase = "lobby"  # lobby | running | ended | error
        self.status_note = "Lobby open. Claim seats and start when ready."
        self.error_message: Optional[str] = None
        # Names of real players who left while the post-game end screen was up.
        # Surfaced in the live state so every remaining client shows a persistent
        # "<name> left" notice and lowers the play-again ready denominator.
        self.post_game_left: List[str] = []

        self.host_control_token = secrets.token_urlsafe(18)
        self.seats: List[Seat] = []
        ai_num = 1
        for i in range(total_players):
            if i < human_players:
                self.seats.append(
                    Seat(
                        index=i,
                        kind="human",
                        label=f"Player {i + 1}",
                        is_host=(i == 0),  # host is whoever claims the first human seat
                    )
                )
            else:
                self.seats.append(
                    Seat(
                        index=i,
                        kind="ai",
                        label=f"Player {i + 1}",
                        claimed_name=f"Bot {ai_num}",
                        token=None,
                    )
                )
                ai_num += 1

        # Team Mode: assign every seat a starting team round-robin so the opening
        # split is even (seat 0→Red, seat 1→Blue, seat 2→Red …). Bots get a team
        # too so team totals include them. Players re-team freely in the lobby.
        if self.team_mode:
            for seat in self.seats:
                seat.team = seat.index % self.team_count

        host_seat = self.host_seat()
        if host_seat is None:
            raise ValueError("room needs at least one human seat")
        # Auto-claim host seat at room creation so the creator is always Player 1.
        host_seat.claimed_name = safe_name(host_name, "Host")
        host_seat.token = secrets.token_urlsafe(18)
        self.status_note = (
            f"Lobby open. {safe_name(host_name, 'Host')} is Host (Player 1). "
            "Others can claim remaining human seats."
        )

        self.latest_public_state: Optional[Dict[str, Any]] = None
        self.latest_private_hands: Dict[int, List[Dict[str, Any]]] = {}
        self.last_turn_number: int = 0
        # Current Controller: per-room flag — hidden-state capture stays off until
        # the admin actually opens a mod tool in this room (see admin_activate).
        # Initialized here (alongside the bot-brain/override maps) so the
        # admin_mod endpoint is safe even in the lobby, before any game launches.
        self._admin_active: bool = False
        self._bot_brain: Dict[int, Dict[str, Any]] = {}
        self._bot_override: Dict[int, Dict[str, Any]] = {}

        self.legal_actions_by_seat: Dict[int, Dict[str, Any]] = {}
        self.pending_actions: Dict[int, List[Dict[str, Any]]] = {}
        # Idempotency map for action submissions to prevent duplicate action
        # execution when clients retry after transient network issues.
        self.seen_action_requests: Dict[int, Dict[str, int]] = {}
        self.active_action_seat: Optional[int] = None

        self.log_events: List[str] = []
        self.turn_summaries: List[Dict[str, Any]] = []
        self._last_turn_scores: Dict[str, int] = {}
        # Accumulates human-readable action descriptions for the current turn,
        # keyed by player name.  Flushed into turn_summaries at turn_end.
        self._current_turn_descs: Dict[str, List[str]] = {}

        self.training_events: List[str] = []
        self.training_snapshots: List[Dict[str, Any]] = []

        self.final_scores: List[Dict[str, Any]] = []
        self.winner: Optional[str] = None
        self.chat_messages: List[Dict[str, Any]] = []

        # ── Chat-based AFK voting ────────────────────────────────────────
        # Players type "P3 is AFK" / "<username> afk" in chat to vote the
        # CURRENT active player as AFK. At >=50% of the OTHER active players,
        # the active player gets a 10-second "Are you still here?" challenge;
        # if they don't interact, they auto-draw 2 and the turn passes.
        self.afk_votes: Dict[int, set] = {}          # target_seat -> set(voter_seat) this turn
        self.afk_nominated_this_turn: set = set()     # seats already challenged this turn (no re-nom)
        self.afk_challenge_seat: Optional[int] = None # seat currently under the 10s challenge
        self.afk_challenge_id: int = 0                # bumped each challenge; cancel/turn-change invalidates
        self.afk_challenge_deadline: Optional[float] = None  # time.time() when auto-draw fires
        self.afk_immune_until: Dict[int, float] = {}  # seat_index -> time.time() until Surf's Up immunity ends
        self.AFK_CHALLENGE_SECONDS = 20.0
        self.AFK_SURF_IMMUNE_SECONDS = 600.0          # 10 minutes

        self.game_thread: Optional[threading.Thread] = None
        self.action_history: List[Dict[str, Any]] = []
        self.recovery_active = False
        self.recovery_target_count = 0
        self.recovery_cursor = 0
        self.recovery_error: Optional[str] = None
        self._skip_history_record_count = 0

        self._persist_dirty = True
        self._last_persist_monotonic = 0.0

        self.undo_snapshot_gs: Any = None
        self.undo_snapshot_ms: Any = None
        self.undo_eligible_seat: Optional[int] = None
        self.undo_valid: bool = False
        self.undo_requested: bool = False
        # Two-phase undo: at turn_start, save a pending snapshot for the current player.
        # Promote it to the active undo snapshot only when the NEXT player's turn starts,
        # so the eligible seat is always the player who just FINISHED, not the one about to play.
        self._undo_pending_gs: Any = None
        self._undo_pending_ms: Any = None
        self._undo_pending_seat: Optional[int] = None

        # AI speed: "slow" | "normal" | "fast". Host can change mid-game.
        self.ai_speed: str = "normal"

        # ── Spectator mode ──────────────────────────────────────────
        # allow_spectators: True by default for public rooms; private rooms default False.
        self.allow_spectators: bool = (visibility == "public")
        # spectators: token → {"name": str, "joined_unix": int}
        self.spectators: Dict[str, Dict[str, Any]] = {}
        # kick votes: spectator_token → set of voter seat indices
        self._spectator_kick_votes: Dict[str, set] = {}

    # ── Spectator helpers ────────────────────────────────────────────
    def spectator_join(self, name: str) -> Dict[str, Any]:
        """Add a spectator. Returns {ok, spectator_token} or {ok:False, error}."""
        name = str(name or "Spectator").strip()[:32] or "Spectator"
        with self.cond:
            if not self.allow_spectators:
                return {"ok": False, "error": "Spectators are not allowed in this game."}
            if self.phase not in ("lobby", "running"):
                return {"ok": False, "error": "Game is not active."}
            token = secrets.token_urlsafe(18)
            self.spectators[token] = {"name": name, "joined_unix": now_unix()}
            self._add_system_chat(f"{name} joined as a spectator.")
            self._bump_locked()
        return {"ok": True, "spectator_token": token, "name": name}

    def spectator_leave(self, token: str) -> Dict[str, Any]:
        with self.cond:
            spec = self.spectators.pop(token, None)
            self._spectator_kick_votes.pop(token, None)
            if spec:
                self._add_system_chat(f"{spec['name']} left spectator mode.")
                self._bump_locked()
        return {"ok": True}

    def spectator_kick_vote(self, voter_seat_index: int, target_token: str) -> Dict[str, Any]:
        with self.cond:
            if target_token not in self.spectators:
                return {"ok": False, "error": "Spectator not found."}
            votes = self._spectator_kick_votes.setdefault(target_token, set())
            votes.add(voter_seat_index)
            human_count = sum(1 for s in self.seats if s.kind == "human" and s.claimed_name)
            needed = max(1, (human_count + 1) // 2)  # ceil(50%)
            if len(votes) >= needed:
                spec = self.spectators.pop(target_token, None)
                self._spectator_kick_votes.pop(target_token, None)
                if spec:
                    self._add_system_chat(f"{spec['name']} was removed from spectator mode by vote.")
                    self._bump_locked()
                return {"ok": True, "kicked": True, "name": spec["name"] if spec else ""}
            spec_name = self.spectators[target_token]["name"]
            self._bump_locked()
        return {"ok": True, "kicked": False, "votes": len(votes), "needed": needed, "name": spec_name}

    def spectator_list(self) -> List[Dict[str, Any]]:
        with self.cond:
            human_count = sum(1 for s in self.seats if s.kind == "human" and s.claimed_name)
            needed = max(1, (human_count + 1) // 2)
            result = []
            for token, spec in self.spectators.items():
                votes = len(self._spectator_kick_votes.get(token, set()))
                result.append({"name": spec["name"], "joined_unix": spec["joined_unix"],
                               "token_tail": token[-6:], "kick_votes": votes, "kick_needed": needed})
            return result

    def spectator_state_view(self, host_header: str, proto_hint: str = "") -> Dict[str, Any]:
        """State payload for spectators — same as a non-viewer but boards-only (no hand data)."""
        with self.cond:
            state_obj = copy.deepcopy(self.latest_public_state) if isinstance(self.latest_public_state, dict) else None
            if isinstance(state_obj, dict):
                for p in (state_obj.get("players") or []):
                    if isinstance(p, dict):
                        p["hand"] = []  # spectators see boards only, no hands
            human_filled, human_total = self._human_seat_counts_locked()
            return {
                "ok": True,
                "version": self.state_version,
                "spectator": True,
                "room": {
                    "room_id": self.room_id, "phase": self.phase,
                    "total_players": self.total_players, "visibility": str(self.visibility),
                    "share_url": self.room_link(host_header, proto_hint),
                    "team_mode": bool(self.team_mode), "team_count": int(self.team_count),
                },
                "status_note": self.status_note,
                "seats": self.seat_snapshot_locked(),
                "viewer": {"seat_index": None, "can_act": False, "spectator": True},
                "state": state_obj,
                "legal_actions": None,
                "active_action_seat": self.active_action_seat,
                "chat_messages": self.chat_messages[-80:],
                "spectators": self.spectator_list(),
                "final_scores": self.final_scores,
                "winner": self.winner,
                "end_game": (self.latest_public_state or {}).get("end_game", {}),
            }

    def submit_spectator_chat(self, token: str, message: str) -> Dict[str, Any]:
        message = str(message or "").strip()[:500]
        if not message:
            return {"ok": False, "error": "empty message"}
        message = _censor_profanity(message)
        with self.cond:
            spec = self.spectators.get(token)
            if not spec:
                return {"ok": False, "error": "not a spectator"}
            entry = {
                "sender": f"[Spectator] {spec['name']}",
                "message": message,
                "ts": time.time(),
                "spectator": True,
            }
            self.chat_messages.append(entry)
            if len(self.chat_messages) > 200:
                self.chat_messages = self.chat_messages[-200:]
            self._bump_locked()
        return {"ok": True}

    def _add_system_chat(self, text: str) -> None:
        """Append a system notification to chat (must be called under self.cond)."""
        self.chat_messages.append({"sender": "System", "message": text, "ts": time.time(), "system": True})
        if len(self.chat_messages) > 200:
            self.chat_messages = self.chat_messages[-200:]

    def set_allow_spectators(self, host_token: str, seat_token: Optional[str], allow: bool) -> Dict[str, Any]:
        with self.cond:
            if not self._is_host_authorized_locked(host_token, seat_token):
                return {"ok": False, "error": "not authorized"}
            self.allow_spectators = bool(allow)
            self._bump_locked()
        return {"ok": True, "allow_spectators": self.allow_spectators}

    def _bump_locked(self, force_persist: bool = False) -> None:
        self.state_version += 1
        self.cond.notify_all()
        self._persist_dirty = True
        self._persist_if_due_locked(force=force_persist)

    def _int_keyed_dict(self, raw: Any, value_type: type) -> Dict[int, Any]:
        out: Dict[int, Any] = {}
        if not isinstance(raw, dict):
            return out
        for key, value in raw.items():
            try:
                idx = int(key)
            except (TypeError, ValueError):
                continue
            if value_type is dict and not isinstance(value, dict):
                continue
            if value_type is list and not isinstance(value, list):
                continue
            out[idx] = copy.deepcopy(value)
        return out

    def _normalize_action_history_item(self, raw: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        kind = raw.get("kind")
        if not isinstance(kind, str) or not kind:
            return None
        rec: Dict[str, Any] = {"kind": kind}
        for key in ("card_uid", "face_uid", "ocean_uid", "source_ocean_uid", "draw_from_pool", "seat_index", "turn_number"):
            if key in raw and isinstance(raw.get(key), int):
                rec[key] = int(raw.get(key))
        rec["use_star"] = bool(raw.get("use_star"))
        rec["pool_pick_uids"] = int_list(raw.get("pool_pick_uids"), cap=20)
        rec["payment_uids"] = int_list(raw.get("payment_uids"), cap=10)
        if isinstance(raw.get("player_name"), str):
            rec["player_name"] = str(raw.get("player_name"))
        if isinstance(raw.get("source"), str):
            rec["source"] = str(raw.get("source"))
        if isinstance(raw.get("request_id"), str):
            rec["request_id"] = str(raw.get("request_id"))[:96]
        if isinstance(raw.get("recorded_unix"), int):
            rec["recorded_unix"] = int(raw.get("recorded_unix"))
        return rec

    def _serialize_checkpoint_locked(self) -> Dict[str, Any]:
        return {
            "schema_version": ROOM_CHECKPOINT_SCHEMA_VERSION,
            "saved_unix": now_unix(),
            "room_id": self.room_id,
            "total_players": int(self.total_players),
            "human_players": int(self.human_players),
            "ai_players": int(self.ai_players),
            "competitive": bool(self.competitive),
            "quick_play": bool(self.quick_play),
            "team_mode": bool(self.team_mode),
            "team_count": int(self.team_count),
            "swap_requests": [dict(r) for r in self.swap_requests],
            "visibility": str(self.visibility),
            "password_hash": self.password_hash,
            "seed": int(self.seed),
            "created_unix": int(self.created_unix),
            "started_unix": self.started_unix,
            "ended_unix": self.ended_unix,
            "state_version": int(self.state_version),
            "phase": self.phase,
            "status_note": self.status_note,
            "error_message": self.error_message,
            "host_control_token": self.host_control_token,
            "seats": [
                {
                    "index": int(seat.index),
                    "kind": str(seat.kind),
                    "label": str(seat.label),
                    "claimed_name": seat.claimed_name,
                    "token": seat.token,
                    "is_host": bool(seat.is_host),
                    "difficulty": str(seat.difficulty or "medium"),
                    "quick_play_ticket": seat.quick_play_ticket,
                    "team": seat.team,
                }
                for seat in self.seats
            ],
            "ai_speed": str(self.ai_speed or "normal"),
            "latest_public_state": copy.deepcopy(self.latest_public_state),
            "latest_private_hands": {str(k): copy.deepcopy(v) for k, v in self.latest_private_hands.items()},
            "last_turn_number": int(self.last_turn_number),
            "legal_actions_by_seat": {str(k): copy.deepcopy(v) for k, v in self.legal_actions_by_seat.items()},
            "seen_action_requests": {str(k): dict(v) for k, v in self.seen_action_requests.items()},
            "active_action_seat": self.active_action_seat,
            "log_events": list(self.log_events[-500:]),
            "turn_summaries": list(self.turn_summaries[-200:]),
            "final_scores": copy.deepcopy(self.final_scores),
            "winner": self.winner,
            "chat_messages": copy.deepcopy(self.chat_messages[-200:]),
            "action_history": copy.deepcopy(self.action_history),
            "recovery": {
                "active": bool(self.recovery_active),
                "target_count": int(self.recovery_target_count),
                "cursor": int(self.recovery_cursor),
                "error": self.recovery_error,
            },
        }

    def _persist_if_due_locked(self, force: bool = False) -> None:
        if not self._persist_dirty:
            return
        now_mono = time.monotonic()
        if not force and (now_mono - self._last_persist_monotonic) < ROOM_PERSIST_MIN_INTERVAL_SEC:
            return
        payload = self._serialize_checkpoint_locked()
        try:
            atomic_write_json(room_state_path(self.room_id), payload)
            self._persist_dirty = False
            self._last_persist_monotonic = now_mono
        except Exception:
            # Keep state dirty so later updates can retry.
            self._persist_dirty = True

    def persist_now(self) -> None:
        with self.cond:
            self._persist_if_due_locked(force=True)

    def _first_open_human_seat(self) -> Optional[Seat]:
        for seat in self.seats:
            if seat.kind == "human" and not seat.token:
                return seat
        return None

    def _seat_from_token_locked(self, token: Optional[str]) -> Optional[Seat]:
        if not token:
            return None
        for seat in self.seats:
            if seat.token and secrets.compare_digest(seat.token, token):
                return seat
        return None

    def _human_seat_counts_locked(self) -> tuple[int, int]:
        filled = 0
        total = 0
        for seat in self.seats:
            if seat.kind != "human":
                continue
            total += 1
            if seat.token:
                filled += 1
        return filled, total

    def _all_humans_claimed_locked(self) -> bool:
        filled, total = self._human_seat_counts_locked()
        return total > 0 and filled >= total

    # ── Team Mode helpers ──────────────────────────────────────────────
    def _team_populations_locked(self) -> Dict[int, int]:
        """Team index → number of ACTIVE (claimed human or bot) seats on it.
        Open/unclaimed human seats don't count toward a team being 'non-empty'."""
        pops: Dict[int, int] = {}
        for seat in self.seats:
            if seat.team is None:
                continue
            active = (seat.kind == "ai") or bool(seat.token)
            if not active:
                continue
            pops[seat.team] = pops.get(seat.team, 0) + 1
        return pops

    def _team_start_ok_locked(self) -> bool:
        """Team games need at least 2 non-empty teams to be a real team game."""
        if not self.team_mode:
            return True
        return len([t for t, n in self._team_populations_locked().items() if n > 0]) >= 2

    def _clear_swap_requests_for_seat_locked(self, seat_index: int) -> None:
        """Drop any pending swap offer that involves this seat (either side)."""
        self.swap_requests = [
            r for r in self.swap_requests
            if r.get("from_seat") != seat_index and r.get("to_seat") != seat_index
        ]

    def _team_spread_turn_order(self) -> List[int]:
        """Return seat indices ordered so teammates are spread as far apart as
        possible, with per-game randomness (no fixed seat→team pattern).

        Groups seats by team, shuffles within each team and the team order, then
        round-robin interleaves one seat per team. Even teams alternate perfectly
        (teammates ~team_count apart); uneven teams are spread best-effort."""
        by_team: Dict[int, List[int]] = {}
        for seat in self.seats:
            t = seat.team if seat.team is not None else 0
            by_team.setdefault(t, []).append(seat.index)
        buckets = list(by_team.values())
        for b in buckets:
            random.shuffle(b)
        # Place larger teams first each round so their members can't be forced
        # adjacent at the tail; randomize the order of equal-size teams.
        random.shuffle(buckets)
        buckets.sort(key=len, reverse=True)
        order: List[int] = []
        while any(buckets):
            for b in buckets:
                if b:
                    order.append(b.pop(0))
        return order

    def host_seat(self) -> Optional[Seat]:
        for seat in self.seats:
            if seat.is_host:
                return seat
        return None

    def _is_host_authorized_locked(self, host_token: str, seat_token: Optional[str]) -> bool:
        authorized = secrets.compare_digest(self.host_control_token, host_token or "")
        if not authorized and seat_token:
            seat = self._seat_from_token_locked(seat_token)
            if seat is not None and seat.kind == "human" and seat.is_host:
                authorized = True
        return authorized

    def is_host_authorized(self, host_token: str, seat_token: Optional[str]) -> bool:
        with self.cond:
            return self._is_host_authorized_locked(host_token, seat_token)

    def _active_human_seats_locked(self) -> List["Seat"]:
        """Humans currently connected (claimed AND not on a rejoin reservation)."""
        return [s for s in self.seats if s.kind == "human" and s.token is not None and s.left_at is None]

    def _reserved_human_seats_locked(self) -> List["Seat"]:
        """Humans who left a running game and can still rejoin (within window)."""
        now = time.time()
        return [
            s for s in self.seats
            if s.kind == "human" and s.token is not None and s.left_at is not None
            and (now - s.left_at) <= REJOIN_WINDOW_SEC
        ]

    def _expire_left_seats_locked(self) -> None:
        """Free seats whose rejoin reservation has expired, transfer host if the
        host's reservation lapsed, and close the room if nobody can return."""
        now = time.time()
        changed = False
        for s in self.seats:
            if s.kind == "human" and s.left_at is not None and (now - s.left_at) > REJOIN_WINDOW_SEC:
                s.token = None
                s.claimed_name = None
                s.left_at = None
                s.is_host = False
                changed = True
        if not changed:
            return
        active = self._active_human_seats_locked()
        reserved = self._reserved_human_seats_locked()
        if not active and not reserved:
            if self.phase != "ended":
                self.phase = "ended"
                self.status_note = "Room closed — no players returned."
        elif active and not any(s.is_host for s in active):
            active[0].is_host = True
            self.status_note = f"{active[0].claimed_name or active[0].label} is now host."
        self._bump_locked()

    def leave_room(self, body: Dict[str, Any], card_db: Optional[Dict[int, "fish.CardDef"]] = None) -> Dict[str, Any]:
        seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
        with self.cond:
            seat = self._seat_from_token_locked(seat_token)

            # ── Post-game (end screen up): free the seat, record the leaver so
            # remaining clients show "<name> left" and the play-again denominator
            # drops, then re-check readiness (the un-ready leaver going may now
            # complete the ready set and auto-start the next game). ────────────
            if self.phase == "ended":
                if seat is None:
                    return {"ok": True, "action": "left"}
                leaving_name = seat.claimed_name or seat.label
                was_host = seat.is_host
                seat.token = None
                seat.claimed_name = None
                seat.is_host = False
                seat.left_at = None
                seat.play_again_ready = False
                seat.quick_play_ticket = None
                if leaving_name and leaving_name not in self.post_game_left:
                    self.post_game_left.append(leaving_name)

                remaining = self._active_human_seats_locked()
                if not remaining:
                    self.status_note = f"{leaving_name} left. Room closed (no players remaining)."
                    self._bump_locked()
                    return {"ok": True, "action": "discarded"}
                if was_host and not any(s.is_host for s in remaining):
                    remaining[0].is_host = True

                started = False
                if card_db is not None:
                    started = self._maybe_start_play_again_locked(card_db)
                if not started:
                    ready, active, bots = self._play_again_counts_locked()
                    self.status_note = f"{leaving_name} left. {ready}/{active + bots} ready to play again…"
                    self._bump_locked()
                return {"ok": True, "action": "left", "post_game": True}

            if seat is None:
                return {"ok": False, "error": "invalid seat token"}

            was_host = seat.is_host
            leaving_name = seat.claimed_name or seat.label

            # ── Running game: RESERVE the seat so this player can rejoin it ──
            # Keep the token + name (the token is their private key to this exact
            # seat) and stamp left_at. Only they can reconnect (they hold the
            # token); others see the seat as occupied. Cleanup frees it after
            # REJOIN_WINDOW_SEC if they don't return.
            if self.phase == "running":
                seat.left_at = time.time()
                active = self._active_human_seats_locked()
                if was_host:
                    if active:
                        seat.is_host = False
                        active[0].is_host = True
                        self.status_note = (
                            f"{leaving_name} left (can rejoin for {REJOIN_WINDOW_SEC // 60} min). "
                            f"{active[0].claimed_name or active[0].label} is now host."
                        )
                        self._bump_locked()
                        return {"ok": True, "action": "host_transferred",
                                "new_host": active[0].claimed_name or active[0].label,
                                "reserved": True}
                    # No one active to host — keep the (reserved) host slot.
                self.status_note = f"{leaving_name} left — seat held for rejoin ({REJOIN_WINDOW_SEC // 60} min)."
                self._bump_locked()
                return {"ok": True, "action": "left", "reserved": True}

            # ── Lobby (or other): free the seat normally (freely re-claimable) ──
            seat.claimed_name = None
            seat.token = None
            seat.is_host = False
            seat.left_at = None
            seat.quick_play_ticket = None

            remaining = [s for s in self.seats if s.kind == "human" and s.token is not None]
            if not remaining:
                self.phase = "ended"
                self.status_note = f"{leaving_name} left. Room closed (no players remaining)."
                self._bump_locked()
                return {"ok": True, "action": "discarded"}

            if was_host:
                new_host = remaining[0]
                new_host.is_host = True
                self.status_note = f"{leaving_name} left. {new_host.claimed_name or new_host.label} is now host."
                self._bump_locked()
                return {"ok": True, "action": "host_transferred", "new_host": new_host.claimed_name or new_host.label}

            self.status_note = f"{leaving_name} left the room."
            self._bump_locked()
            return {"ok": True, "action": "left"}

    def room_link(self, host_header: str, proto_hint: str = "") -> str:
        return f"{share_base_url(host_header, proto_hint)}/play/{self.room_id}"

    def seat_snapshot_locked(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for seat in self.seats:
            out.append(
                {
                    "index": seat.index,
                    "label": seat.label,
                    "kind": seat.kind,
                    "status": seat.status(),
                    "claimed_name": seat.claimed_name,
                    "is_host": bool(seat.is_host),
                    "difficulty": str(seat.difficulty or "medium"),
                    "is_away": bool(getattr(seat, "is_away", False)),
                    "inactive_eligible": bool(getattr(seat, "inactive_eligible", False)),
                    "team": seat.team,
                }
            )
        return out

    @classmethod
    def from_checkpoint(cls, payload: Dict[str, Any]) -> Optional["GameRoom"]:
        if not isinstance(payload, dict):
            return None
        room_id = str(payload.get("room_id") or "").strip().upper()
        if not ROOM_ID_ACCEPT_RE.fullmatch(room_id):
            return None

        total_players = clamp_int(payload.get("total_players"), 4, 2, 8)
        human_players = clamp_int(payload.get("human_players"), 2, 1, total_players)
        ai_players = clamp_int(payload.get("ai_players"), total_players - human_players, 0, total_players - 1)
        if human_players + ai_players != total_players:
            ai_players = max(0, total_players - human_players)

        host_name = "Host"
        seats_raw = payload.get("seats")
        if isinstance(seats_raw, list):
            for seat_raw in seats_raw:
                if not isinstance(seat_raw, dict):
                    continue
                if seat_raw.get("is_host") and isinstance(seat_raw.get("claimed_name"), str):
                    host_name = safe_name(seat_raw.get("claimed_name"), "Host")
                    break

        competitive = bool(payload.get("competitive", False))
        quick_play = bool(payload.get("quick_play", False))
        team_mode = bool(payload.get("team_mode", False))
        team_count = clamp_int(payload.get("team_count"), 2, 2, 4)
        vis_raw = str(payload.get("visibility") or "public").strip().lower()
        visibility = vis_raw if vis_raw in {"public", "private"} else "public"
        pw_hash = payload.get("password_hash") if isinstance(payload.get("password_hash"), str) else None
        room = cls(
            room_id,
            host_name,
            total_players,
            human_players,
            ai_players,
            competitive=competitive,
            visibility=visibility,
            password_hash=pw_hash,
            quick_play=quick_play,
            team_mode=team_mode,
            team_count=team_count,
        )
        with room.cond:
            room.seed = clamp_int(payload.get("seed"), room.seed, 0, 2**64 - 1)
            room.created_unix = clamp_int(payload.get("created_unix"), room.created_unix, 0, 2**31 - 1)
            room.started_unix = clamp_int_or_none(payload.get("started_unix"), 0, 2**31 - 1)
            room.ended_unix = clamp_int_or_none(payload.get("ended_unix"), 0, 2**31 - 1)
            room.state_version = max(1, clamp_int(payload.get("state_version"), room.state_version, 1, 2**31 - 1))

            phase_raw = str(payload.get("phase") or "").strip().lower()
            room.phase = phase_raw if phase_raw in {"lobby", "running", "ended", "error"} else "lobby"

            if isinstance(payload.get("status_note"), str) and str(payload.get("status_note")).strip():
                room.status_note = str(payload.get("status_note")).strip()
            room.error_message = str(payload.get("error_message")).strip() if isinstance(payload.get("error_message"), str) else None

            host_token_raw = payload.get("host_control_token")
            if isinstance(host_token_raw, str) and host_token_raw.strip():
                room.host_control_token = host_token_raw.strip()

            parsed_seats: List[Seat] = []
            if isinstance(seats_raw, list) and len(seats_raw) == total_players:
                ai_num = 1
                for idx in range(total_players):
                    seat_raw = seats_raw[idx] if isinstance(seats_raw[idx], dict) else {}
                    seat_kind = str(seat_raw.get("kind") or "").strip().lower()
                    if seat_kind not in {"human", "ai"}:
                        seat_kind = "human" if idx < human_players else "ai"
                    default_label = f"Player {idx + 1}"
                    seat_label = safe_name(seat_raw.get("label"), default_label)
                    claimed_name: Optional[str] = None
                    if seat_kind == "ai":
                        raw_ai_name = seat_raw.get("claimed_name")
                        claimed_name = (
                            safe_name(raw_ai_name, f"Bot {ai_num}")
                            if isinstance(raw_ai_name, str) and raw_ai_name.strip()
                            else f"Bot {ai_num}"
                        )
                        ai_num += 1
                    else:
                        raw_human_name = seat_raw.get("claimed_name")
                        if isinstance(raw_human_name, str) and raw_human_name.strip():
                            claimed_name = safe_name(raw_human_name, seat_label)
                    token = seat_raw.get("token") if seat_kind == "human" and isinstance(seat_raw.get("token"), str) else None
                    raw_difficulty = seat_raw.get("difficulty")
                    seat_difficulty = (
                        str(raw_difficulty).strip().lower()
                        if isinstance(raw_difficulty, str) and raw_difficulty.strip().lower() in {"easy", "medium", "hard"}
                        else "medium"
                    )
                    raw_team = seat_raw.get("team")
                    if isinstance(raw_team, int) and 0 <= raw_team < team_count:
                        seat_team: Optional[int] = raw_team
                    elif team_mode:
                        seat_team = idx % team_count
                    else:
                        seat_team = None
                    parsed_seats.append(
                        Seat(
                            index=idx,
                            kind=seat_kind,
                            label=seat_label,
                            claimed_name=claimed_name,
                            token=token,
                            is_host=bool(seat_raw.get("is_host")) if seat_kind == "human" else False,
                            difficulty=seat_difficulty,
                            quick_play_ticket=(
                                str(seat_raw.get("quick_play_ticket")).strip()[:96]
                                if seat_kind == "human"
                                and isinstance(seat_raw.get("quick_play_ticket"), str)
                                and str(seat_raw.get("quick_play_ticket")).strip()
                                else None
                            ),
                            team=seat_team,
                        )
                    )
            if parsed_seats:
                room.seats = parsed_seats

            host_humans = [seat for seat in room.seats if seat.kind == "human" and seat.is_host]
            if not host_humans:
                for seat in room.seats:
                    if seat.kind == "human":
                        seat.is_host = True
                        break
            for seat in room.seats:
                if seat.kind != "human":
                    seat.is_host = False
                    seat.token = None

            raw_swaps = payload.get("swap_requests")
            if isinstance(raw_swaps, list):
                room.swap_requests = [dict(r) for r in raw_swaps if isinstance(r, dict)]

            ai_speed_raw = str(payload.get("ai_speed") or "normal").strip().lower()
            room.ai_speed = ai_speed_raw if ai_speed_raw in {"slow", "normal", "fast"} else "normal"

            latest_public = payload.get("latest_public_state")
            room.latest_public_state = copy.deepcopy(latest_public) if isinstance(latest_public, dict) else None
            room.latest_private_hands = room._int_keyed_dict(payload.get("latest_private_hands"), list)
            room.last_turn_number = clamp_int(payload.get("last_turn_number"), 0, 0, 1000000)
            room.legal_actions_by_seat = room._int_keyed_dict(payload.get("legal_actions_by_seat"), dict)
            room.pending_actions = {}

            seen_raw = payload.get("seen_action_requests")
            parsed_seen: Dict[int, Dict[str, int]] = {}
            if isinstance(seen_raw, dict):
                for seat_key, req_map in seen_raw.items():
                    try:
                        seat_idx = int(seat_key)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(req_map, dict):
                        continue
                    keep: Dict[str, int] = {}
                    for req_id, ts in req_map.items():
                        if not isinstance(req_id, str) or not req_id:
                            continue
                        if len(req_id) > 96:
                            continue
                        if isinstance(ts, int):
                            keep[req_id] = int(ts)
                        if len(keep) >= 512:
                            break
                    if keep:
                        parsed_seen[seat_idx] = keep
            room.seen_action_requests = parsed_seen

            room.active_action_seat = clamp_int_or_none(payload.get("active_action_seat"), 0, max(0, total_players - 1))
            room.log_events = [str(x) for x in list(payload.get("log_events", [])) if isinstance(x, str)][-500:]
            room.turn_summaries = [x for x in list(payload.get("turn_summaries", [])) if isinstance(x, dict)][-200:]
            room.final_scores = [x for x in list(payload.get("final_scores", [])) if isinstance(x, dict)]
            room.winner = str(payload.get("winner")).strip() if isinstance(payload.get("winner"), str) else None
            room.chat_messages = [x for x in list(payload.get("chat_messages", [])) if isinstance(x, dict)][-200:]

            action_history_raw = payload.get("action_history")
            parsed_actions: List[Dict[str, Any]] = []
            if isinstance(action_history_raw, list):
                for item in action_history_raw:
                    normalized = room._normalize_action_history_item(item)
                    if normalized is not None:
                        parsed_actions.append(normalized)
                    if len(parsed_actions) >= MAX_ACTION_HISTORY:
                        break
            room.action_history = parsed_actions
            room.recovery_target_count = len(room.action_history)
            room.recovery_cursor = room.recovery_target_count if room.phase != "running" else 0
            room.recovery_active = bool(room.phase == "running" and room.recovery_target_count > 0)
            room._skip_history_record_count = 0

            recovery_raw = payload.get("recovery")
            if isinstance(recovery_raw, dict) and isinstance(recovery_raw.get("error"), str):
                err_msg = str(recovery_raw.get("error")).strip()
                if err_msg:
                    room.recovery_error = err_msg

            room.training_events = []
            room.training_snapshots = []

            room._persist_dirty = True
            room._last_persist_monotonic = 0.0
            room.game_thread = None

        return room

    def _action_to_history_record(
        self,
        seat_index: int,
        player_name: str,
        action: fish.Action,
        source: str,
        turn_number: Optional[int] = None,
        request_id: str = "",
    ) -> Dict[str, Any]:
        rec: Dict[str, Any] = {
            "recorded_unix": now_unix(),
            "seat_index": int(seat_index),
            "player_name": safe_name(player_name, f"Player {seat_index + 1}"),
            "source": str(source),
            "kind": str(action.kind),
            "card_uid": int(getattr(action, "card_uid", -1)),
            "face_uid": int(action.face_uid if action.face_uid is not None else getattr(action, "card_uid", -1)),
            "draw_from_pool": int(getattr(action, "draw_from_pool", 0)),
            "use_star": bool(getattr(action, "use_star", False)),
            "pool_pick_uids": int_list(getattr(action, "pool_pick_uids", []), cap=20),
            "payment_uids": int_list(getattr(action, "payment_uids", []), cap=10),
        }
        if getattr(action, "ocean_uid", None) is not None:
            rec["ocean_uid"] = int(action.ocean_uid)
        src_ocean = getattr(action, "source_ocean_uid", None)
        if src_ocean is not None:
            rec["source_ocean_uid"] = int(src_ocean)
        req = str(request_id or "").strip()
        if req:
            rec["request_id"] = req[:96]
        if isinstance(turn_number, int):
            rec["turn_number"] = int(turn_number)
        return rec

    def _record_action_history(
        self,
        seat_index: int,
        player_name: str,
        action: fish.Action,
        source: str,
        turn_number: Optional[int] = None,
        request_id: str = "",
    ) -> None:
        rec = self._action_to_history_record(
            seat_index,
            player_name,
            action,
            source,
            turn_number=turn_number,
            request_id=request_id,
        )
        with self.cond:
            if self._skip_history_record_count > 0:
                self._skip_history_record_count -= 1
                return
            self.action_history.append(rec)
            if len(self.action_history) > MAX_ACTION_HISTORY:
                del self.action_history[: len(self.action_history) - MAX_ACTION_HISTORY]
            self.recovery_target_count = len(self.action_history)
            self.recovery_cursor = self.recovery_target_count
            self._persist_dirty = True
            self._persist_if_due_locked(force=True)

    def record_executed_action(
        self,
        seat_index: int,
        player_name: str,
        action: fish.Action,
        turn_number: int,
    ) -> None:
        if seat_index < 0 or seat_index >= self.total_players:
            return
        self._record_action_history(
            seat_index=seat_index,
            player_name=player_name,
            action=action,
            source="executed",
            turn_number=turn_number,
        )

    def _action_cmd_from_history_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        cmd: Dict[str, Any] = {}
        for key in ("kind", "card_uid", "face_uid", "ocean_uid", "source_ocean_uid", "draw_from_pool"):
            value = record.get(key)
            if key == "kind":
                if isinstance(value, str) and value:
                    cmd[key] = value
            elif isinstance(value, int):
                cmd[key] = int(value)
        if isinstance(record.get("use_star"), bool):
            cmd["use_star"] = bool(record.get("use_star"))
        cmd["pool_pick_uids"] = int_list(record.get("pool_pick_uids"), cap=20)
        cmd["payment_uids"] = int_list(record.get("payment_uids"), cap=10)
        return cmd

    def _replay_action_if_available(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        player: fish.PlayerState,
        seat_index: int,
        actions: List[fish.Action],
    ) -> Optional[fish.Action]:
        with self.cond:
            if self.phase != "running" or not self.recovery_active:
                return None
            target = min(self.recovery_target_count, len(self.action_history))
            if self.recovery_cursor >= target:
                self.recovery_active = False
                self.recovery_error = None
                self.status_note = "Recovery complete. Live play resumed."
                self._bump_locked(force_persist=True)
                return None

            record = self.action_history[self.recovery_cursor]
            expected_seat = record.get("seat_index")
            if isinstance(expected_seat, int) and expected_seat != seat_index:
                self.recovery_active = False
                self.recovery_error = (
                    f"replay seat mismatch at action {self.recovery_cursor + 1}: "
                    f"expected seat {expected_seat + 1}, got seat {seat_index + 1}"
                )
                self.status_note = "Recovery desynced. Live play resumed from nearest safe state."
                self._bump_locked(force_persist=True)
                return None
            cmd = self._action_cmd_from_history_record(record)

        chosen = self._resolve_submitted_action(gs, ms, player, actions, cmd)
        with self.cond:
            if not self.recovery_active:
                return None
            if chosen is None:
                self.recovery_active = False
                legal_kinds = [str(cand.kind) for cand in actions[:6]]
                self.recovery_error = (
                    f"replay action not legal at step {self.recovery_cursor + 1} "
                    f"(seat={record.get('seat_index')}, kind={record.get('kind')}, card={record.get('card_uid')}, "
                    f"legal={legal_kinds})"
                )
                self.status_note = "Recovery desynced. Live play resumed from nearest safe state."
                self._bump_locked(force_persist=True)
                return None

            self.recovery_cursor += 1
            self._skip_history_record_count += 1
            target = min(self.recovery_target_count, len(self.action_history))
            if self.recovery_cursor >= target:
                self.recovery_active = False
                self.recovery_error = None
                self.status_note = "Recovery complete. Live play resumed."
                self._bump_locked(force_persist=True)
            elif self.recovery_cursor % 20 == 0:
                self.status_note = f"Resyncing game after server restart — step {self.recovery_cursor} of {target}. Room is staying open, please wait..."
                self._bump_locked(force_persist=False)
        return chosen

    def claim_seat(
        self,
        player_name: str,
        seat_index: Optional[int],
        existing_token: Optional[str],
        allow_takeover: bool = False,
        allow_host_takeover: bool = False,
    ) -> Dict[str, Any]:
        with self.cond:
            existing_seat: Optional[Seat] = None
            if existing_token:
                existing_seat = self._seat_from_token_locked(existing_token)
                # Plain reconnect path only when no explicit seat target was requested.
                if existing_seat is not None and seat_index is None:
                    new_name = safe_name(player_name, existing_seat.label)
                    rejoined = existing_seat.left_at is not None
                    existing_seat.left_at = None  # they're back — lift any reservation
                    if new_name and existing_seat.claimed_name != new_name:
                        existing_seat.claimed_name = new_name
                    if rejoined and not any(s.is_host for s in self.seats if s.kind == "human" and s.token is not None and s.left_at is None):
                        existing_seat.is_host = True
                    self.status_note = (f"{existing_seat.claimed_name or existing_seat.label} rejoined."
                                        if rejoined else
                                        f"{existing_seat.claimed_name or existing_seat.label} reconnected to {existing_seat.label}.")
                    self._bump_locked()
                    return {
                        "ok": True,
                        "seat_index": existing_seat.index,
                        "seat_token": existing_seat.token,
                        "reconnected": True,
                    }

            target: Optional[Seat] = None
            if seat_index is not None:
                if seat_index < 0 or seat_index >= len(self.seats):
                    return {"ok": False, "error": "invalid seat index"}
                target = self.seats[seat_index]
            else:
                if self.phase != "lobby":
                    return {"ok": False, "error": "seat index required after game start"}
                target = self._first_open_human_seat()

            if target is None:
                return {"ok": False, "error": "no open human seats"}
            if target.kind != "human":
                return {"ok": False, "error": "cannot claim AI seat"}
            if existing_seat is not None and target.index == existing_seat.index:
                # Explicit request for the same seat token holder: treat as reconnect/rename.
                new_name = safe_name(player_name, target.label)
                if new_name and target.claimed_name != new_name:
                    target.claimed_name = new_name
                    self.status_note = f"{target.claimed_name} updated name on {target.label}."
                    self._bump_locked()
                return {
                    "ok": True,
                    "seat_index": target.index,
                    "seat_token": target.token,
                    "reconnected": True,
                }
            # A seat reserved for a rejoining player (they left a running game
            # within the window) can only be reclaimed by them — via the
            # token-reconnect path above — never taken over by someone else.
            if (target.left_at is not None
                    and (time.time() - target.left_at) <= REJOIN_WINDOW_SEC
                    and not (existing_seat is not None and existing_seat.index == target.index)):
                return {"ok": False, "error": "seat reserved for a rejoining player"}
            if target.token is not None:
                host_ok = (not target.is_host) or allow_host_takeover
                # Idempotent host reclaim: if the same host identity is already on the host
                # seat, return the current token instead of rotating it on every reclaim call.
                if (
                    allow_takeover
                    and target.is_host
                    and allow_host_takeover
                    and target.claimed_name == safe_name(player_name, target.label)
                ):
                    return {
                        "ok": True,
                        "seat_index": target.index,
                        "seat_token": target.token,
                        "reconnected": True,
                    }
                if allow_takeover and host_ok:
                    previous_name = target.claimed_name or target.label
                    # Seat switch: free the caller's old seat first, but never vacate the
                    # host seat as a side-effect — only explicit host-to-host reclaims may do that.
                    if existing_seat is not None and existing_seat is not target and existing_seat.kind == "human" and not existing_seat.is_host:
                        existing_seat.claimed_name = None
                        existing_seat.token = None
                    target.claimed_name = safe_name(player_name, target.label)
                    target.token = secrets.token_urlsafe(18)
                    if target.is_host:
                        self.status_note = f"{target.claimed_name} reclaimed Host seat ({target.label})."
                    else:
                        self.status_note = f"{target.claimed_name} took over {target.label}."
                    self._bump_locked()
                    return {
                        "ok": True,
                        "seat_index": target.index,
                        "seat_token": target.token,
                        "reconnected": False,
                        "took_over": True,
                        "previous_name": previous_name,
                    }
                return {"ok": False, "error": "seat already taken"}
            if self.phase != "lobby":
                return {"ok": False, "error": "game already started"}

            # Seat switch: free the caller's old seat first, but never vacate the host seat
            # as a side-effect of a regular claim (e.g. if someone received the host's
            # seat_token via a shared URL and tries to claim a different seat).
            if existing_seat is not None and existing_seat is not target and existing_seat.kind == "human" and not existing_seat.is_host:
                existing_seat.claimed_name = None
                existing_seat.token = None
            target.claimed_name = safe_name(player_name, target.label)
            target.token = secrets.token_urlsafe(18)
            self.status_note = f"{target.claimed_name} claimed {target.label}."
            self._bump_locked()
            return {"ok": True, "seat_index": target.index, "seat_token": target.token, "reconnected": False}

    def start_game(
        self,
        host_token: str,
        seat_token: Optional[str],
        card_db: Dict[int, fish.CardDef],
        allow_with_create_key: bool = False,
    ) -> Dict[str, Any]:
        with self.cond:
            if not (allow_with_create_key or self._is_host_authorized_locked(host_token, seat_token)):
                return {"ok": False, "error": "host authorization required"}
            if self.phase != "lobby":
                return {"ok": False, "error": "game already started"}
            if not self._all_humans_claimed_locked():
                filled, total = self._human_seat_counts_locked()
                missing = [seat.label for seat in self.seats if seat.kind == "human" and not seat.token]
                return {
                    "ok": False,
                    "error": "all human seats must be claimed before start",
                    "human_seats_filled": filled,
                    "human_seats_total": total,
                    "missing_seats": missing,
                }
            self._launch_game_locked(card_db, status_note="Game started. Waiting for first turn.")
            return {"ok": True}

    def restart_game(
        self,
        host_token: str,
        seat_token: Optional[str],
        card_db: Dict[int, fish.CardDef],
    ) -> Dict[str, Any]:
        with self.cond:
            if not self._is_host_authorized_locked(host_token, seat_token):
                return {"ok": False, "error": "host authorization required"}
            if self.phase == "running":
                return {"ok": False, "error": "game is still running"}
            if self.game_thread is not None and self.game_thread.is_alive():
                return {"ok": False, "error": "previous game is still finishing"}
            if not self._all_humans_claimed_locked():
                return {"ok": False, "error": "all human seats must be claimed before restart"}

            self._launch_game_locked(card_db, status_note="Game restarted. Waiting for first turn.")
            return {"ok": True}

    def _play_again_counts_locked(self) -> tuple[int, int, int]:
        """(ready_humans, active_humans, bots) for the post-game ready-up.

        active_humans = claimed humans still present (not on a left/rejoin slot).
        ready_humans  = those who have pressed Play Again.
        bots          = AI seats. Bots "auto-ready" implicitly: they never count
                        toward ready_humans, but they ARE in the denominator the
                        client shows (N = active_humans + bots), and the game
                        starts the instant every active human is ready.
        """
        active = self._active_human_seats_locked()
        ready = sum(1 for s in active if s.play_again_ready)
        bots = sum(1 for s in self.seats if s.kind == "ai")
        return ready, len(active), bots

    def _maybe_start_play_again_locked(self, card_db: Dict[int, fish.CardDef]) -> bool:
        """Start a fresh game if every active human has readied up. Returns True
        if a new game was launched."""
        if self.phase != "ended":
            return False
        if self.game_thread is not None and self.game_thread.is_alive():
            return False
        active = self._active_human_seats_locked()
        if not active:
            return False
        if not all(s.play_again_ready for s in active):
            return False
        # Everyone who is still here is ready — bots ready implicitly. Go.
        self._launch_game_locked(card_db, status_note="New game starting — everyone readied up!")
        return True

    def play_again(self, seat_token: Optional[str], card_db: Dict[int, fish.CardDef]) -> Dict[str, Any]:
        with self.cond:
            if self.phase != "ended":
                return {"ok": False, "error": "play again is only available after the game ends"}
            seat = self._seat_from_token_locked(seat_token)
            if seat is None or seat.kind != "human" or seat.left_at is not None:
                return {"ok": False, "error": "invalid seat token"}

            seat.play_again_ready = True
            seat.last_seen = time.time()  # readying counts as activity

            started = self._maybe_start_play_again_locked(card_db)
            ready, active, bots = self._play_again_counts_locked()
            if not started:
                total = active + bots
                self.status_note = f"{ready}/{total} ready to play again…"
                self._bump_locked()
            return {
                "ok": True,
                "started": bool(started),
                "ready_count": ready,
                "total": (active + bots),
            }

    def set_seat_difficulty(
        self,
        host_token: str,
        seat_token: Optional[str],
        seat_index: int,
        difficulty: str,
    ) -> Dict[str, Any]:
        with self.cond:
            if not self._is_host_authorized_locked(host_token, seat_token):
                return {"ok": False, "error": "host authorization required"}
            if self.phase != "lobby":
                return {"ok": False, "error": "difficulty can only change in the lobby"}
            if not isinstance(seat_index, int) or seat_index < 0 or seat_index >= len(self.seats):
                return {"ok": False, "error": "invalid seat index"}
            target = self.seats[seat_index]
            if target.kind != "ai":
                return {"ok": False, "error": "only AI seats have a difficulty"}
            normalized = str(difficulty or "").strip().lower()
            if normalized not in {"easy", "medium", "hard"}:
                return {"ok": False, "error": "difficulty must be easy, medium, or hard"}
            if target.difficulty == normalized:
                return {"ok": True, "difficulty": normalized, "unchanged": True}
            target.difficulty = normalized
            self.status_note = f"{target.claimed_name or target.label} set to {normalized.title()} difficulty."
            self._bump_locked()
            return {"ok": True, "difficulty": normalized}

    # ── Team Mode: lobby team switching + cross-team swaps ──────────────
    def _prune_swap_requests_locked(self) -> bool:
        """Drop swap offers that are expired, or whose seats are no longer a
        valid claimed-human pair on different teams. Returns True if any changed."""
        if not self.swap_requests:
            return False
        now = time.time()
        kept: List[Dict[str, Any]] = []
        for r in self.swap_requests:
            fi, ti = r.get("from_seat"), r.get("to_seat")
            if not isinstance(fi, int) or not isinstance(ti, int):
                continue
            if now - float(r.get("created", now)) > SWAP_REQUEST_TTL_SEC:
                continue
            if not (0 <= fi < len(self.seats) and 0 <= ti < len(self.seats)):
                continue
            a, b = self.seats[fi], self.seats[ti]
            if a.kind != "human" or b.kind != "human" or not a.token or not b.token:
                continue
            if a.team is None or b.team is None or a.team == b.team:
                continue
            kept.append(r)
        changed = len(kept) != len(self.swap_requests)
        self.swap_requests = kept
        return changed

    def set_seat_team(self, seat_token: Optional[str], team: Any) -> Dict[str, Any]:
        with self.cond:
            if not self.team_mode:
                return {"ok": False, "error": "not a team game"}
            if self.phase != "lobby":
                return {"ok": False, "error": "teams can only change in the lobby"}
            seat = self._seat_from_token_locked(seat_token)
            if seat is None or seat.kind != "human":
                return {"ok": False, "error": "seat token invalid"}
            if not isinstance(team, int) or team < 0 or team >= self.team_count:
                return {"ok": False, "error": "invalid team"}
            if seat.team == team:
                return {"ok": True, "team": team, "unchanged": True}
            seat.team = team
            # Any swap offer that involved this seat is now stale.
            self._clear_swap_requests_for_seat_locked(seat.index)
            color = TEAM_COLORS[team] if 0 <= team < len(TEAM_COLORS) else str(team)
            self.status_note = f"{seat.claimed_name or seat.label} joined the {color} team."
            self._bump_locked()
            return {"ok": True, "team": team}

    def request_team_swap(self, seat_token: Optional[str], target_seat: Any) -> Dict[str, Any]:
        with self.cond:
            if not self.team_mode:
                return {"ok": False, "error": "not a team game"}
            if self.phase != "lobby":
                return {"ok": False, "error": "swaps can only happen in the lobby"}
            a = self._seat_from_token_locked(seat_token)
            if a is None or a.kind != "human":
                return {"ok": False, "error": "seat token invalid"}
            if not isinstance(target_seat, int) or target_seat < 0 or target_seat >= len(self.seats):
                return {"ok": False, "error": "invalid target seat"}
            b = self.seats[target_seat]
            if b.index == a.index:
                return {"ok": False, "error": "cannot swap with yourself"}
            if b.kind != "human" or not b.token:
                return {"ok": False, "error": "can only swap with another player"}
            if a.team is None or b.team is None or a.team == b.team:
                return {"ok": False, "error": "that player is already on your team"}
            self._prune_swap_requests_locked()
            # One outstanding offer per requester; replace any prior one.
            self.swap_requests = [r for r in self.swap_requests if r.get("from_seat") != a.index]
            self.swap_requests.append({
                "from_seat": a.index,
                "from_name": a.claimed_name or a.label,
                "from_team": a.team,      # the team the TARGET would move to
                "to_seat": b.index,
                "to_team": b.team,        # the team the REQUESTER would move to
                "created": time.time(),
            })
            self._bump_locked()
            return {"ok": True}

    def respond_team_swap(self, seat_token: Optional[str], action: str, from_seat: Any = None) -> Dict[str, Any]:
        with self.cond:
            if not self.team_mode:
                return {"ok": False, "error": "not a team game"}
            b = self._seat_from_token_locked(seat_token)
            if b is None or b.kind != "human":
                return {"ok": False, "error": "seat token invalid"}
            self._prune_swap_requests_locked()
            # Find the offer addressed to THIS seat (optionally matching sender).
            match = None
            for r in self.swap_requests:
                if r.get("to_seat") == b.index and (from_seat is None or r.get("from_seat") == from_seat):
                    match = r
                    break
            if match is None:
                return {"ok": False, "error": "no pending swap request"}
            a_index = match.get("from_seat")
            if action == "accept":
                if self.phase != "lobby":
                    return {"ok": False, "error": "swaps can only happen in the lobby"}
                a = self.seats[a_index] if isinstance(a_index, int) and 0 <= a_index < len(self.seats) else None
                if a is None or a.kind != "human" or not a.token or a.team is None or b.team is None:
                    self.swap_requests = [r for r in self.swap_requests if r is not match]
                    self._bump_locked()
                    return {"ok": False, "error": "that player is no longer available"}
                a.team, b.team = b.team, a.team
                # Clear every offer touching either seat (both just re-teamed).
                self._clear_swap_requests_for_seat_locked(a.index)
                self._clear_swap_requests_for_seat_locked(b.index)
                self.status_note = f"{a.claimed_name or a.label} and {b.claimed_name or b.label} swapped teams."
                self._bump_locked()
                return {"ok": True, "swapped": True}
            else:  # decline (or anything else) removes just this offer
                self.swap_requests = [r for r in self.swap_requests if r is not match]
                self._bump_locked()
                return {"ok": True, "swapped": False}

    def configure_quick_play_seats(
        self,
        host_token: str,
        seat_token: Optional[str],
        human_players: int,
    ) -> Dict[str, Any]:
        """Change a Quick Play lobby between 2–4 human seats.

        The room always has four total seats. Unselected, unclaimed human seats
        become bots; adding human capacity converts bots back into open seats.
        Claimed human seats are never displaced.
        """
        with self.cond:
            if not self._is_host_authorized_locked(host_token, seat_token):
                return {"ok": False, "error": "host authorization required"}
            if not self.quick_play:
                return {"ok": False, "error": "seat setup is only available in Quick Play"}
            if self.phase != "lobby":
                return {"ok": False, "error": "seat setup can only change in the lobby"}
            if human_players not in {2, 3, 4}:
                return {"ok": False, "error": "human player slots must be 2, 3, or 4"}

            claimed_humans = [
                seat for seat in self.seats
                if seat.kind == "human" and seat.token is not None
            ]
            if len(claimed_humans) > human_players:
                return {
                    "ok": False,
                    "error": (
                        f"{len(claimed_humans)} human players are already in this lobby. "
                        f"Choose {len(claimed_humans)} or more human slots."
                    ),
                    "human_seats_filled": len(claimed_humans),
                }

            desired_human_indices = {seat.index for seat in claimed_humans}
            # Preserve existing open human slots before converting bots back.
            for seat in self.seats:
                if len(desired_human_indices) >= human_players:
                    break
                if seat.kind == "human":
                    desired_human_indices.add(seat.index)
            for seat in self.seats:
                if len(desired_human_indices) >= human_players:
                    break
                desired_human_indices.add(seat.index)

            changed = False
            for seat in self.seats:
                should_be_human = seat.index in desired_human_indices
                if should_be_human and seat.kind != "human":
                    seat.kind = "human"
                    seat.claimed_name = None
                    seat.token = None
                    seat.is_host = False
                    seat.avatar = None
                    seat.background = None
                    seat.left_at = None
                    seat.quick_play_ticket = None
                    changed = True
                elif not should_be_human and seat.kind != "ai":
                    # Claimed humans were included above and can never reach
                    # this branch.
                    seat.kind = "ai"
                    seat.claimed_name = None
                    seat.token = None
                    seat.is_host = False
                    seat.avatar = None
                    seat.background = None
                    seat.left_at = None
                    seat.quick_play_ticket = None
                    changed = True

            bot_number = 1
            for seat in self.seats:
                seat.label = f"Player {seat.index + 1}"
                if seat.kind == "ai":
                    expected_name = f"Bot {bot_number}"
                    if seat.claimed_name != expected_name:
                        seat.claimed_name = expected_name
                        changed = True
                    seat.token = None
                    seat.is_host = False
                    seat.quick_play_ticket = None
                    bot_number += 1

            self.human_players = human_players
            self.ai_players = self.total_players - human_players
            filled, total = self._human_seat_counts_locked()
            if changed:
                setup_text = (
                    f"{human_players} human player{'s' if human_players != 1 else ''} "
                    f"and {self.ai_players} bot{'s' if self.ai_players != 1 else ''}"
                )
                self.status_note = f"Host set the Quick Play lobby to {setup_text}."
                self._add_system_chat(self.status_note)
                self._bump_locked(force_persist=True)
            return {
                "ok": True,
                "human_players": self.human_players,
                "ai_players": self.ai_players,
                "human_seats_filled": filled,
                "human_seats_total": total,
                "can_start": bool(filled >= total),
                "seats": self.seat_snapshot_locked(),
                "unchanged": not changed,
            }

    def terminate_game(self, host_token: str, seat_token: Optional[str]) -> Dict[str, Any]:
        with self.cond:
            if not self._is_host_authorized_locked(host_token, seat_token):
                return {"ok": False, "error": "host authorization required"}
            self.phase = "ended"
            self.status_note = "Game terminated by host."
            self._bump_locked(force_persist=True)
            return {"ok": True}

    def resume_after_restore(self, card_db: Dict[int, fish.CardDef]) -> Dict[str, Any]:
        with self.cond:
            if self.phase != "running":
                return {"ok": False, "error": "room is not running"}
            if self.game_thread is not None and self.game_thread.is_alive():
                return {"ok": True, "already_running": True}

            self.error_message = None
            self.legal_actions_by_seat.clear()
            self.pending_actions.clear()
            self.active_action_seat = None
            # Give every human a fresh forfeit window after a server restart, so a
            # player who polls first can't forfeit one who simply hasn't re-polled
            # yet. Real inactivity is re-detected from here as polls resume.
            _now_seen = time.time()
            for _s in self.seats:
                if _s.kind == "human":
                    _s.last_seen = _now_seen
            self._forfeit_result = None
            if self.recovery_active:
                self.status_note = (
                    f"Resyncing game after server restart — step {self.recovery_cursor} of {self.recovery_target_count}. Room is staying open, please wait..."
                )
            else:
                self.recovery_error = None
                self.status_note = "Resuming live game."
            self._bump_locked(force_persist=True)

            self.game_thread = threading.Thread(target=self._run_game_thread, args=(card_db,), daemon=True)
            self.game_thread.start()
            return {"ok": True}

    def _randomize_seat_positions_locked(self) -> None:
        """Randomly reassign which player occupies which seat POSITION.

        After everyone has joined, the host is whoever created the room and by
        join order always ends up in seat 0 — i.e. always "Player 1". The client
        labels, positions and orders every player purely by seat index (P{index+1},
        turn order, AFK id), so to truly randomize the player order we shuffle the
        seats themselves right as the game launches.

        Every Seat OBJECT (with its token, avatar, background, team, is_host,
        difficulty, claimed_name …) is moved to a new position and its
        index/label renumbered to match. Because a client's viewer.seat_index is
        looked up by its seat token every poll, each player's number updates on
        its own and all per-seat identity stays intact.

        Runs once per launch (see _launch_game_locked), so the arrangement is
        fresh each game, stored server-side on self.seats — every client sees the
        SAME order — and is never re-shuffled mid-game: casual turn order is
        deterministic seat order, so a resume after a server restart rebuilds the
        exact same order.

        Skipped for:
          • competitive — each human owns a fixed PAIR of seats ({0,1}/{2,3})
            and the [0,2,1,3] interleave depends on those fixed positions;
          • tutorials — the guided walkthrough needs the human at seat 0 / first.
        """
        if self.competitive or getattr(self, "is_tutorial", False):
            return
        if len(self.seats) < 2:
            return
        order = list(range(len(self.seats)))
        random.shuffle(order)
        new_seats = [self.seats[old] for old in order]
        for new_index, seat in enumerate(new_seats):
            seat.index = new_index
            seat.label = f"Player {new_index + 1}"
        self.seats = new_seats

    def _launch_game_locked(self, card_db: Dict[int, fish.CardDef], status_note: str) -> None:
        # Randomize the player order once, now that everyone has joined: shuffle
        # which player sits in which seat so the host isn't always Player 1 and
        # turn order (which follows seat order for casual games) is randomized.
        # Must run before anything below reads self.seats. No-op for
        # competitive/tutorial (see _randomize_seat_positions_locked).
        self._randomize_seat_positions_locked()
        # Ensure every game start/restart gets a fresh random shuffle seed.
        self.seed = secrets.randbits(64)
        self.phase = "running"
        self.started_unix = now_unix()
        self.ended_unix = None
        self.error_message = None
        self.status_note = status_note
        # Seed last-seen for every human seat so the competitive forfeit window
        # is measured from game start, not from the first poll.
        _now_seen = time.time()
        for _s in self.seats:
            if _s.kind == "human":
                _s.last_seen = _now_seen
        self._forfeit_result = None
        # Fresh game — clear any post-game ready-up state from the prior round.
        self.post_game_left = []
        for _s in self.seats:
            _s.play_again_ready = False
        self.log_events = []
        self.turn_summaries = []
        self._current_turn_descs = {}
        self.training_events = []
        self.training_snapshots = []
        self.final_scores = []
        self.winner = None
        self.action_history = []
        self.recovery_active = False
        self.recovery_target_count = 0
        self.recovery_cursor = 0
        self.recovery_error = None
        self._skip_history_record_count = 0
        # Hard reset of published state so every game starts from a clean board view.
        self.latest_public_state = None
        self.latest_private_hands = {}
        # ── Current Controller (admin mod tools) live state ──────────────
        # Captured each snapshot for the admin reveal endpoint, and mutated only
        # on the match thread (drained inside _wait_for_action) so we never race
        # the game engine. All reset here so a new game starts clean.
        self._live_gs = None                 # live engine refs (match thread only)
        self._live_ms = None
        self._admin_deck_entries = []        # [entry_to_dict,...] for the deck picker
        self._admin_discards = {}            # seat_idx -> [entry_to_dict,...]
        self._admin_pool_entries = []        # [entry_to_dict,...]
        self._admin_endgame = {}             # end-game card location snapshot
        self._bot_brain = {}                 # seat_idx -> last bot decision {chosen, candidates}
        # Flips True the first time an authorized admin_mod call hits this room.
        # While False, the room does NO extra hidden-state capture, so normal
        # games (where the admin tools are never opened) pay nothing.
        self._admin_active = False
        self._admin_mod_queue = []           # [{id, op, params, event, result}]
        self._bot_override = {}              # seat_idx -> armed override action spec
        self.last_turn_number = 0
        self._last_turn_scores = {}
        self.legal_actions_by_seat.clear()
        self.pending_actions.clear()
        self.seen_action_requests.clear()
        self.active_action_seat = None
        # Clear Surf's Up / inactivity state at game start so a previous
        # game's flags don't carry over.
        for s in self.seats:
            s.is_away = False
            s.inactive_eligible = False
        # Reset chat-based AFK voting state for the fresh game.
        self.afk_votes = {}
        self.afk_nominated_this_turn = set()
        self.afk_challenge_seat = None
        self.afk_challenge_deadline = None
        self.afk_challenge_id += 1
        self.afk_immune_until = {}
        self._bump_locked(force_persist=True)

        self.game_thread = threading.Thread(target=self._run_game_thread, args=(card_db,), daemon=True)
        self.game_thread.start()

    def _competitive_same_owner(self, seat_a: Optional[int], seat_b: Optional[int]) -> bool:
        """Competitive only: each human controls TWO seats (their two hands).
        P1 owns seats {0,1}, P2 owns seats {2,3}. Returns True when both seat
        indices belong to the same human, so a token for one of a player's hands
        is authorized to act for the player's OTHER hand when it's that hand's
        turn. Returns False for any non-competitive or non-4-seat room."""
        if not (self.competitive and len(self.seats) == 4):
            return False
        if seat_a is None or seat_b is None:
            return False
        if not (0 <= seat_a < 4 and 0 <= seat_b < 4):
            return False
        return (seat_a // 2) == (seat_b // 2)

    def submit_action(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        token = payload.get("seat_token")
        with self.cond:
            seat = self._seat_from_token_locked(token if isinstance(token, str) else None)
            if seat is None:
                return {"ok": False, "error": "seat token invalid"}
            if self.phase != "running":
                return {"ok": False, "error": "game is not running"}
            if seat.kind != "human":
                return {"ok": False, "error": "AI seats cannot submit actions"}
            # The seat this action will act AS. Normally the token's own seat. In
            # competitive, a player controls two hands (two seats); accept the
            # token for whichever of the owner's hands is currently active so the
            # second hand never reports "not your turn" if the client's polled
            # token lagged a cycle behind the turn switch.
            act_seat_index = seat.index
            if (self.active_action_seat is not None
                    and self.active_action_seat != seat.index
                    and self._competitive_same_owner(seat.index, self.active_action_seat)):
                act_seat_index = self.active_action_seat
            if self.active_action_seat != act_seat_index:
                return {"ok": False, "error": "not your turn"}

            # Surf's Up!! — while a seat is officially Away it cannot make any
            # move. The player must tap "I'm Back" (toggle Away off) before they
            # can act again. This keeps Surf's Up a true "I've stepped away" state.
            act_seat = self.seats[act_seat_index] if 0 <= act_seat_index < len(self.seats) else seat
            if getattr(act_seat, "is_away", False):
                return {"ok": False, "error": "You're on Surf's Up (Away) — tap “I'm Back” before you can make a move."}

            req_id_raw = payload.get("request_id")
            req_id = req_id_raw.strip() if isinstance(req_id_raw, str) else ""
            seen: Optional[Dict[str, int]] = None
            req_now = 0
            if req_id:
                if len(req_id) > 96:
                    return {"ok": False, "error": "request_id too long"}
                seen = self.seen_action_requests.setdefault(act_seat_index, {})
                req_now = now_unix()
                stale_before = req_now - 900
                if seen:
                    for key, ts in list(seen.items()):
                        if ts < stale_before:
                            del seen[key]
                if req_id in seen:
                    # Duplicate retry of an already-accepted action request.
                    return {"ok": True, "duplicate": True}
                if len(seen) >= 256:
                    for key, _ in sorted(seen.items(), key=lambda kv: kv[1])[:80]:
                        del seen[key]

            idx = payload.get("action_index")
            has_semantic = any(
                key in payload
                for key in ("kind", "card_uid", "face_uid", "ocean_uid", "source_ocean_uid", "draw_from_pool", "use_star")
            )
            if not isinstance(idx, int) and not has_semantic:
                return {"ok": False, "error": "action_index must be int or include action identity fields"}

            picks_raw = payload.get("pool_pick_uids", [])
            payment_raw = payload.get("payment_uids", [])
            picks = [x for x in picks_raw if isinstance(x, int)] if isinstance(picks_raw, list) else []
            payment = [x for x in payment_raw if isinstance(x, int)] if isinstance(payment_raw, list) else []

            cmd: Dict[str, Any] = {
                "action_index": idx if isinstance(idx, int) else None,
                "pool_pick_uids": picks,
                "payment_uids": payment,
                "submitted_unix": now_unix(),
            }
            kind = payload.get("kind")
            if isinstance(kind, str) and kind:
                cmd["kind"] = kind
            for key in ("card_uid", "face_uid", "ocean_uid", "source_ocean_uid", "draw_from_pool"):
                value = payload.get(key)
                if isinstance(value, int):
                    cmd[key] = value
            if isinstance(payload.get("use_star"), bool):
                cmd["use_star"] = bool(payload.get("use_star"))
            if req_id:
                cmd["request_id"] = req_id

            queue = self.pending_actions.setdefault(act_seat_index, [])
            if len(queue) >= 12:
                return {"ok": False, "error": "too many queued actions; wait for state update"}
            if req_id and seen is not None:
                seen[req_id] = req_now or now_unix()
            queue.append(cmd)
            self.status_note = f"Action queued from {seat.claimed_name or seat.label}."
            self._bump_locked()
            return {"ok": True}

    def _wait_for_action(self, seat_index: int, timeout_sec: float = 300.0) -> Optional[Dict[str, Any]]:
        """Block until an action arrives, the game ends, or timeout_sec elapses (returns None on timeout)."""
        deadline = time.monotonic() + timeout_sec
        with self.cond:
            while self.phase == "running":
                # Apply any queued Current Controller mod mutations here — this
                # runs on the match thread, so engine state is never raced.
                self._drain_admin_mods_locked()
                q = self.pending_actions.get(seat_index)
                if q:
                    return q.pop(0)
                # A human elsewhere armed a flag-driven Undo (submit_undo's path 3)
                # while THIS seat was parked here waiting for input. That restore
                # only runs at the TOP of a policy loop — which a blocked human
                # never reaches on its own — so without waking here the undo would
                # hang until this seat acted or the 30-min timeout fired (the
                # "I pressed Undo and nothing happened" bug at a turn handoff).
                # Return a sentinel so the caller re-loops and its top-of-loop
                # _apply_pending_undo_restore honors the undo immediately. The
                # action-queue check above intentionally wins, so an explicit
                # undo_confirm/undo_mid_turn command still takes precedence.
                if (
                    self.undo_requested
                    and self.undo_valid
                    and self.undo_snapshot_gs is not None
                ):
                    return {"kind": "__undo_armed__"}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None  # Timed out — caller will fall back to safe action
                self.cond.wait(timeout=min(0.25, remaining))
            return None

    def _serialize_legal_actions(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        player: fish.PlayerState,
        actions: List[fish.Action],
    ) -> Dict[str, Any]:
        payload_actions: List[Dict[str, Any]] = []
        for idx, action in enumerate(actions):
            face_uid = action.face_uid if action.face_uid is not None else action.card_uid
            card = gs.card_db.get(face_uid)
            direction = fish.normalize_direction(card.direction) if card is not None else "n/a"
            cost_to_pay, requires_symbol, required_symbol = self._action_payment_requirements(gs, ms, player, action)
            payment_candidates = [int(uid) for uid in player.hand if uid != action.card_uid] if cost_to_pay > 0 else []
            # star_symbol: the symbol on a star-ability card when played without use_star.
            # If a payment card matches this symbol, the engine auto-triggers the star ability.
            star_symbol = ""
            if card is not None and not bool(action.use_star) and cost_to_pay > 0:
                try:
                    if hasattr(fish, "has_star_ability") and fish.has_star_ability(card):
                        raw_sym = fish.normalize_symbol(getattr(card, "symbol", ""))
                        if raw_sym not in {"", "n/a"}:
                            star_symbol = raw_sym
                except Exception:
                    pass
            payload_actions.append(
                {
                    "index": idx,
                    "kind": action.kind,
                    "card_uid": action.card_uid,
                    "face_uid": face_uid,
                    "face_name": card.name if card is not None else "",
                    "face_direction": direction,
                    "ocean_uid": action.ocean_uid,
                    "source_ocean_uid": getattr(action, "source_ocean_uid", None),
                    "draw_from_pool": action.draw_from_pool,
                    "use_star": bool(action.use_star),
                    "cost_to_pay": int(cost_to_pay),
                    "requires_symbol_match": bool(requires_symbol),
                    "required_symbol": required_symbol,
                    "has_star_ability": bool(star_symbol or (requires_symbol and required_symbol)),
                    "star_symbol": star_symbol,
                    "payment_candidates": payment_candidates,
                    "description": fish.describe_action(gs, ms, action),
                }
            )
        return {
            "updated_unix": now_unix(),
            "player": player.name,
            "turn_index": int(gs.turn_index),
            "round_count": int(gs.round_count),
            "must_discard_to_ten": bool(actions) and all(a.kind in {"discard_to_pool", "discard_batch_to_pool"} for a in actions),
            "tarpon_discard_active": bool(player.flags.get("_tarpon_discard_active", False)),
            "discard_batch_available": any(a.kind == "discard_batch_to_pool" for a in actions),
            "discard_excess": max(0, len(player.hand) - (int(fish.HAND_LIMIT) if hasattr(fish, "HAND_LIMIT") else 10)),
            "hand_limit": int(fish.HAND_LIMIT) if hasattr(fish, "HAND_LIMIT") else 10,
            "actions": payload_actions,
            "draws_taken": int(player.flags.get("_draws_taken", 0)),
        }

    def _resolve_submitted_action(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        player: fish.PlayerState,
        actions: List[fish.Action],
        cmd: Dict[str, Any],
    ) -> Optional[fish.Action]:
        idx_raw = cmd.get("action_index")
        has_identity = any(
            key in cmd for key in ("kind", "card_uid", "face_uid", "ocean_uid", "source_ocean_uid", "draw_from_pool", "use_star")
        )
        chosen: Optional[fish.Action] = None

        if isinstance(idx_raw, int) and 0 <= idx_raw < len(actions):
            by_index = actions[idx_raw]
            if has_identity:
                if self._action_matches_identity(by_index, cmd):
                    chosen = copy.deepcopy(by_index)
                else:
                    # Stale index: resolve by identity so we do not execute
                    # the wrong card/action and accidentally burn hand cards.
                    chosen = self._resolve_action_by_identity(actions, cmd)
            else:
                chosen = copy.deepcopy(by_index)

        if chosen is None and has_identity:
            chosen = self._resolve_action_by_identity(actions, cmd)

        if chosen is None:
            return None

        if chosen.kind == "discard_batch_to_pool":
            # Player submitted the cards they chose to discard in pool_pick_uids.
            # Normalize face UIDs → canonical entry UIDs so two-sided cards resolve
            # to the hand entry, and de-duplicate (preserving order) so the same
            # card can never be queued for discard twice.
            picks_raw = cmd.get("pool_pick_uids", [])
            picks: List[int] = []
            if isinstance(picks_raw, list):
                for x in picks_raw:
                    if not isinstance(x, int) or isinstance(x, bool):
                        continue
                    uid = fish.canonical_entry_uid(ms, x)
                    if uid not in picks:
                        picks.append(uid)
            if not picks:
                # Client submitted batch discard without selecting any cards —
                # reject it so the human policy loops and waits for a real selection.
                return None
            chosen.pool_pick_uids = picks

        if chosen.kind == "draw" and chosen.draw_from_pool > 0:
            need = chosen.draw_from_pool
            picks_raw = cmd.get("pool_pick_uids", [])
            # Normalize face UIDs → canonical entry UIDs so paired cards resolve correctly
            picks = [fish.canonical_entry_uid(ms, x) for x in picks_raw if isinstance(x, int)] if isinstance(picks_raw, list) else []
            if len(picks) == need and len(set(picks)) == need and all(uid in ms.pool for uid in picks):
                chosen.pool_pick_uids = picks
            else:
                chosen.pool_pick_uids = list(ms.pool[:need])

        pay_raw = cmd.get("payment_uids", [])
        provided_payments = [x for x in pay_raw if isinstance(x, int)] if isinstance(pay_raw, list) else []

        if chosen.kind in {"play_ocean", "play_to_ocean"}:
            play_face_uid = chosen.face_uid if chosen.face_uid is not None else chosen.card_uid
            cost_to_pay, requires_symbol, required_symbol = self._action_payment_requirements(gs, ms, player, chosen)

            def valid_payment_set(payments: List[int]) -> bool:
                if cost_to_pay <= 0:
                    return len(payments) == 0
                if len(payments) != cost_to_pay:
                    return False
                if len(set(payments)) != len(payments):
                    return False
                if any(uid == chosen.card_uid or uid not in player.hand for uid in payments):
                    return False
                if requires_symbol:
                    target_sym = fish.normalize_symbol(required_symbol)
                    if target_sym in {"", "n/a"}:
                        card = gs.card_db.get(play_face_uid)
                        if card is None:
                            return False
                        target_sym = fish.normalize_symbol(card.symbol)
                    if target_sym in {"", "n/a"}:
                        return False
                    if not any(fish.symbol_match_for_entry(ms, gs, uid, target_sym) for uid in payments):
                        return False
                return True

            def auto_payment_set() -> Optional[List[int]]:
                if cost_to_pay <= 0:
                    return []
                candidates = [int(uid) for uid in player.hand if int(uid) != int(chosen.card_uid)]
                if len(candidates) < cost_to_pay:
                    return None
                picks: List[int] = []
                remaining = list(candidates)
                if requires_symbol:
                    target_sym = fish.normalize_symbol(required_symbol)
                    if target_sym in {"", "n/a"}:
                        card = gs.card_db.get(play_face_uid)
                        if card is None:
                            return None
                        target_sym = fish.normalize_symbol(card.symbol)
                    if target_sym in {"", "n/a"}:
                        return None
                    match_uid = next((uid for uid in remaining if fish.symbol_match_for_entry(ms, gs, uid, target_sym)), None)
                    if match_uid is None:
                        return None
                    picks.append(match_uid)
                    remaining = [uid for uid in remaining if uid != match_uid]
                for uid in remaining:
                    if len(picks) >= cost_to_pay:
                        break
                    picks.append(uid)
                if not valid_payment_set(picks):
                    return None
                return picks

            if cost_to_pay <= 0:
                chosen.payment_uids = []
            elif valid_payment_set(provided_payments):
                chosen.payment_uids = list(provided_payments)
            else:
                auto_payments = auto_payment_set()
                if auto_payments is None:
                    return None
                chosen.payment_uids = auto_payments
        elif isinstance(pay_raw, list):
            chosen.payment_uids = provided_payments
        return chosen

    def _resolve_action_by_identity(self, actions: List[fish.Action], cmd: Dict[str, Any]) -> Optional[fish.Action]:
        for action in actions:
            if self._action_matches_identity(action, cmd):
                return copy.deepcopy(action)
        return None

    def _action_matches_identity(self, action: fish.Action, cmd: Dict[str, Any]) -> bool:
        kind = cmd.get("kind")
        if isinstance(kind, str) and kind and action.kind != kind:
            return False

        for key in ("card_uid", "face_uid", "ocean_uid", "source_ocean_uid", "draw_from_pool"):
            raw = cmd.get(key)
            if not isinstance(raw, int):
                continue
            if key == "face_uid":
                action_value = action.face_uid if action.face_uid is not None else action.card_uid
            elif key in {"ocean_uid", "source_ocean_uid"}:
                action_value = action.ocean_uid
                if key == "source_ocean_uid":
                    action_value = getattr(action, "source_ocean_uid", None)
            elif key == "draw_from_pool":
                action_value = int(action.draw_from_pool)
            else:
                action_value = getattr(action, key, None)
            # Backward compatibility for older clients that serialized null ocean_uid as 0.
            if key == "ocean_uid" and action_value is None and int(raw) == 0:
                continue
            if action_value is None:
                return False
            if int(action_value) != int(raw):
                return False

        use_star = cmd.get("use_star")
        if isinstance(use_star, bool) and bool(action.use_star) != bool(use_star):
            return False
        return True

    def _action_payment_requirements(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        player: fish.PlayerState,
        action: fish.Action,
    ) -> tuple[int, bool, str]:
        if hasattr(fish, "action_payment_requirements"):
            try:
                return fish.action_payment_requirements(gs, ms, player, action)
            except Exception:
                pass

        if action.kind not in {"play_ocean", "play_to_ocean"}:
            return 0, False, ""

        face_uid = action.face_uid if action.face_uid is not None else action.card_uid
        card = gs.card_db.get(face_uid)
        if card is None:
            return 0, False, ""

        free_play = False
        if hasattr(fish, "is_free_play_eligible"):
            try:
                free_play = bool(fish.is_free_play_eligible(player, card))
            except Exception:
                free_play = False

        cost_to_pay = 0 if free_play else max(0, int(getattr(card, "cost", 0)))
        if not bool(action.use_star):
            return cost_to_pay, False, ""

        has_star = False
        if hasattr(fish, "has_star_ability"):
            try:
                has_star = bool(fish.has_star_ability(card))
            except Exception:
                has_star = False
        if not has_star or cost_to_pay <= 0:
            return cost_to_pay, False, ""

        sym = fish.normalize_symbol(getattr(card, "symbol", ""))
        if sym in {"", "n/a"}:
            return cost_to_pay, False, ""
        return cost_to_pay, True, sym

    @staticmethod
    def _entry_display_name(gs: Any, ms: Any, entry_uid: int) -> str:
        """Return a display string for a pool/hand entry showing BOTH face names."""
        try:
            faces = fish.entry_faces(ms, int(entry_uid))
            names = []
            for fuid in faces:
                c = gs.card_db.get(int(fuid))
                if c and getattr(c, "name", ""):
                    names.append(c.name)
            if names:
                return " / ".join(names)
        except Exception:
            pass
        return "a card"

    @staticmethod
    def _ocean_name(gs: Any, ocean_uid: int) -> str:
        try:
            c = gs.card_db.get(int(ocean_uid))
            if c and getattr(c, "name", ""):
                return c.name
        except Exception:
            pass
        return "an ocean"

    @classmethod
    def _describe_action(cls, gs: Any, ms: Any, action: Any) -> str:
        """Build one human-readable line describing what an action did."""
        kind = str(getattr(action, "kind", ""))
        card_uid = getattr(action, "card_uid", None)
        ocean_uid = getattr(action, "ocean_uid", None)
        draw_from_pool = int(getattr(action, "draw_from_pool", 0))
        src_ocean = getattr(action, "source_ocean_uid", None)

        if kind == "draw":
            if draw_from_pool and card_uid is not None:
                return f"Drew {cls._entry_display_name(gs, ms, card_uid)} from pool"
            return "Drew 1 card from deck"

        if kind == "play_ocean" and card_uid is not None:
            return f"Played ocean: {cls._entry_display_name(gs, ms, card_uid)}"

        if kind == "play_to_ocean" and card_uid is not None:
            card_desc = cls._entry_display_name(gs, ms, card_uid)
            if ocean_uid is not None:
                return f"Played {card_desc} → {cls._ocean_name(gs, ocean_uid)}"
            return f"Played {card_desc}"

        if kind == "discard_to_pool" and card_uid is not None:
            return f"Discarded {cls._entry_display_name(gs, ms, card_uid)} to pool"

        if kind == "discard_batch_to_pool":
            uids = list(getattr(action, "pool_pick_uids", []) or [])
            if uids:
                names = [cls._entry_display_name(gs, ms, u) for u in uids[:4]]
                return "Discarded to pool: " + ", ".join(names)
            return "Discarded cards to pool"

        if kind == "move_card" and card_uid is not None:
            card_desc = cls._entry_display_name(gs, ms, card_uid)
            if ocean_uid is not None and src_ocean is not None:
                return f"Moved {card_desc}: {cls._ocean_name(gs, src_ocean)} → {cls._ocean_name(gs, ocean_uid)}"
            return f"Moved {card_desc}"

        if kind == "end_turn":
            return ""  # don't clutter the log with end_turn

        return ""

    def _append_turn_desc(self, player_name: str, desc: str) -> None:
        if not desc:
            return
        with self.cond:
            parts = self._current_turn_descs.setdefault(player_name, [])
            parts.append(desc)

    def _record_event(self, msg: str) -> None:
        with self.cond:
            self.log_events.append(msg)
            self.training_events.append(msg)
            if len(self.log_events) > 500:
                del self.log_events[:-500]
            if len(self.training_events) > 4000:
                del self.training_events[:-4000]
            self._bump_locked()

    def _record_snapshot(self, gs: fish.GameState, ms: fish.MatchState, turn_number: int, note: str) -> None:
        players_public: List[Dict[str, Any]] = []
        private_hands: Dict[int, List[Dict[str, Any]]] = {}
        scores_now: Dict[str, int] = {}

        for game_idx, p in enumerate(gs.players):
            # Map game-engine index back to seat index for the client
            seat_idx = self._comp_game_to_seat.get(game_idx, game_idx)
            try:
                full_breakdown = fish.full_score_breakdown(gs, p)
                score = int(full_breakdown.get("total", 0))
            except Exception:
                try:
                    score = int(fish.final_points(gs, p))
                except Exception:
                    score = int(getattr(p, "score", 0))
                full_breakdown = {
                    "total": score,
                    "rows": [],
                    "error": "score_breakdown_failed",
                }
            try:
                mandarin_payload = fish.mandarin_goby_score_breakdown(gs, p, precomputed_full=full_breakdown)
            except Exception:
                mandarin_payload = {
                    "count": 0,
                    "points": 0,
                    "table": {"1": 0, "2": 15, "3": 30, "4+": 80},
                    "total_score": score,
                    "error": "mandarin_breakdown_failed",
                }
            try:
                board_payload = board_to_dict(p, gs)
            except Exception:
                board_payload = []
            scores_now[p.name] = score
            try:
                detected_strategy = fish.detect_player_strategy(gs, p)
            except Exception:
                detected_strategy = "Best Guess"
            players_public.append(
                {
                    "index": seat_idx,
                    "name": p.name,
                    "score": score,
                    "score_breakdown": {
                        "full": full_breakdown,
                        "mandarin_goby": mandarin_payload,
                    },
                    "hand_count": len(p.hand),
                    "board_ocean_count": len(p.board_oceans),
                    "board": board_payload,
                    # The strategy this player actually built (guide-based,
                    # per-player). Used by the client for stats / leaderboards /
                    # recap / achievements — no manual choosing.
                    "strategy": detected_strategy,
                }
            )
            hand_payload: List[Dict[str, Any]] = []
            for uid in list(p.hand):
                if not isinstance(uid, int):
                    continue
                hand_payload.append(entry_to_dict(ms, gs, uid))
            private_hands[seat_idx] = hand_payload

        pool_payload = [entry_to_dict(ms, gs, uid) for uid in list(ms.pool) if isinstance(uid, int)]
        current_player = None
        if gs.players:
            try:
                current_idx = int(gs.turn_index) % len(gs.players)
            except Exception:
                current_idx = 0
            if 0 <= current_idx < len(gs.players):
                current_player = gs.players[current_idx].name

        training_players: List[Dict[str, Any]] = []
        for p in gs.players:
            try:
                score_now = int(fish.final_points(gs, p))
            except Exception:
                score_now = int(getattr(p, "score", 0))
            board_slots: Dict[str, Dict[str, List[int]]] = {}
            for ocean_uid in list(p.board_oceans):
                slots = p.ocean_slots.get(ocean_uid)
                if not isinstance(slots, fish.OceanSlots):
                    continue
                board_slots[str(ocean_uid)] = {
                    "up": [int(uid) for uid in list(getattr(slots, "up", [])) if isinstance(uid, int)],
                    "down": [int(uid) for uid in list(getattr(slots, "down", [])) if isinstance(uid, int)],
                    "left": [int(uid) for uid in list(getattr(slots, "left", [])) if isinstance(uid, int)],
                    "right": [int(uid) for uid in list(getattr(slots, "right", [])) if isinstance(uid, int)],
                }
            training_players.append(
                {
                    "name": p.name,
                    "score": score_now,
                    "hand_entry_uids": [int(uid) for uid in list(p.hand) if isinstance(uid, int)],
                    "board_oceans": [int(uid) for uid in list(p.board_oceans) if isinstance(uid, int)],
                    "board_slots": board_slots,
                }
            )

        # turn_index is the game-engine index (0-3 mod n_players). For
        # competitive, translate to the seat index so the client can match it
        # against players[i].index (which is seat_idx, not game_idx).
        raw_turn_idx = int(gs.turn_index)
        if gs.players:
            raw_turn_idx = raw_turn_idx % len(gs.players)
        seat_turn_idx = self._comp_game_to_seat.get(raw_turn_idx, raw_turn_idx)

        public_state = {
            "seed": self.seed,
            "turn_number": int(turn_number),
            "turn_index": seat_turn_idx,
            "round_count": int(gs.round_count),
            "current_player": current_player,
            "note": note,
            "deck_remaining": len(gs.deck),
            "pool_count": len(ms.pool),
            "discard_pile_count": len(ms.discard_pile),
            "pool": pool_payload,
            "players": players_public,
            "end_game": {
                "triggered": bool(ms.end_game_triggered),
                "final_turns_remaining": int(ms.final_turns_remaining),
                "end_game_uid": ms.end_game_uid,
            },
        }

        training_snapshot = {
            "turn_number": int(turn_number),
            "note": note,
            "deck_remaining": len(gs.deck),
            "pool_entry_uids": [int(uid) for uid in list(ms.pool) if isinstance(uid, int)],
            "discard_count": len(ms.discard_pile),
            "players": training_players,
        }

        summary: Optional[Dict[str, Any]] = None
        if note.startswith("turn_end:"):
            ended_player = note.split(":", 1)[1] if ":" in note else ""
            deltas = {
                name: int(score - self._last_turn_scores.get(name, score))
                for name, score in scores_now.items()
            }
            # Collect and flush the action descriptions accumulated this turn.
            turn_actions: List[str] = list(self._current_turn_descs.get(ended_player, []))
            self._current_turn_descs.pop(ended_player, None)
            summary = {
                "turn_number": int(turn_number),
                "player": ended_player,
                "score_deltas": deltas,
                "pool_count": len(ms.pool),
                "ocean_counts": {p.name: len(p.board_oceans) for p in gs.players},
                "actions": turn_actions,
            }
            self._last_turn_scores = scores_now

        # Two-phase undo snapshot (deepcopy done outside the lock for performance).
        # Phase 1 (turn_start for player X): save a pending snapshot of the state
        #   BEFORE X plays — this is the correct restore point for X's future undo.
        # Phase 2 (turn_start for the NEXT player Y): promote the pending snapshot
        #   as the active undo for X (the player who just FINISHED), not for Y.
        # This ensures the undo button is only active for the person who just played,
        # never for the person whose turn is coming up.
        undo_promote_gs: Any = None
        undo_promote_ms: Any = None
        undo_promote_seat: Optional[int] = None
        undo_new_pending_gs: Any = None
        undo_new_pending_ms: Any = None
        undo_new_pending_seat: Optional[int] = None

        if note.startswith("turn_start:"):
            try:
                n_players = max(len(gs.players), 1)
                cur_game_idx = int(gs.turn_index) % n_players
                cur_seat_idx = self._comp_game_to_seat.get(cur_game_idx, cur_game_idx)
                cur_seat = next((s for s in self.seats if s.index == cur_seat_idx), None)

                # Promote the pending snapshot from the previous player's turn_start
                # (= state before they played) as their active undo window.
                if self._undo_pending_gs is not None and self._undo_pending_seat is not None:
                    undo_promote_gs = self._undo_pending_gs
                    undo_promote_ms = self._undo_pending_ms
                    undo_promote_seat = self._undo_pending_seat

                # Save a new pending snapshot for the current player so it can be
                # promoted when the NEXT player's turn starts.
                if cur_seat is not None and cur_seat.kind == "human":
                    undo_new_pending_gs = copy.deepcopy(gs)
                    undo_new_pending_ms = copy.deepcopy(ms)
                    undo_new_pending_seat = cur_seat_idx
            except Exception:
                pass

        with self.cond:
            self.latest_public_state = public_state
            self.latest_private_hands = private_hands
            self.last_turn_number = int(turn_number)
            # ── Current Controller: capture hidden state for the admin reveal ──
            # and keep live engine refs so mod mutations can be applied on the
            # match thread (via _wait_for_action) without racing the engine.
            # Only do the extra (deck/discard) capture once the admin has opened
            # the mod tools in THIS room, so normal games pay nothing.
            self._live_gs = gs
            self._live_ms = ms
            if self._admin_active:
                try:
                    self._admin_pool_entries = list(pool_payload)
                    self._admin_deck_entries = [entry_to_dict(ms, gs, uid) for uid in list(gs.deck) if isinstance(uid, int)]
                    discards: Dict[int, List[Dict[str, Any]]] = {}
                    for g_idx, pl in enumerate(gs.players):
                        s_idx = self._comp_game_to_seat.get(g_idx, g_idx)
                        discards[s_idx] = [entry_to_dict(ms, gs, uid) for uid in list(pl.discard) if isinstance(uid, int)]
                    self._admin_discards = discards
                    eg_uid = getattr(ms, "end_game_uid", None)
                    self._admin_endgame = {
                        "end_game_uid": eg_uid,
                        "triggered": bool(getattr(ms, "end_game_triggered", False)),
                        "in_deck": isinstance(eg_uid, int) and eg_uid in gs.deck,
                        "deck_position_from_bottom": (len(gs.deck) - gs.deck.index(eg_uid)) if (isinstance(eg_uid, int) and eg_uid in gs.deck) else None,
                    }
                except Exception as _exc:
                    self._record_event(f"admin snapshot capture warning: {_exc}")

            # Always update the pending slot (clears it if current player is AI).
            if note.startswith("turn_start:"):
                self._undo_pending_gs = undo_new_pending_gs
                self._undo_pending_ms = undo_new_pending_ms
                self._undo_pending_seat = undo_new_pending_seat
                # A new turn is starting. Clear the active action seat so it does
                # NOT keep pointing at the previous (human) player while an AI
                # bot takes its turn — otherwise that human's client keeps seeing
                # can_act=true ("YOUR TURN") during everyone else's turn.
                # _human_policy re-sets active_action_seat to its own seat the
                # instant it runs (same engine thread, immediately after this
                # snapshot), so a human's own turn is unaffected; AI turns simply
                # leave it None → can_act is false for every seat.
                self.active_action_seat = None

            # Promote previous player's pending snapshot to active undo.
            if undo_promote_gs is not None:
                self.undo_snapshot_gs = undo_promote_gs
                self.undo_snapshot_ms = undo_promote_ms
                self.undo_eligible_seat = undo_promote_seat
                self.undo_valid = True
                self.undo_requested = False

            # Mid-turn undo: if the current player has drawn their first card
            # (_draws_taken == 1) promote the pending snapshot NOW so they can
            # undo the draw before choosing their second card.
            if note.startswith("post_action:") and not self.undo_valid:
                try:
                    mid_draw_player = next(
                        (p for p in gs.players if int(p.flags.get("_draws_taken", 0)) == 1),
                        None,
                    )
                    if mid_draw_player is not None and self._undo_pending_gs is not None:
                        n_players = max(len(gs.players), 1)
                        cur_game_idx = int(gs.turn_index) % n_players
                        cur_seat_idx = self._comp_game_to_seat.get(cur_game_idx, cur_game_idx)
                        if self._undo_pending_seat == cur_seat_idx:
                            self.undo_snapshot_gs = self._undo_pending_gs
                            self.undo_snapshot_ms = self._undo_pending_ms
                            self.undo_eligible_seat = cur_seat_idx
                            self.undo_valid = True
                            self.undo_requested = False
                except Exception:
                    pass

            self.training_snapshots.append(training_snapshot)
            if len(self.training_snapshots) > 1200:
                del self.training_snapshots[:-1200]

            if summary is not None:
                self.turn_summaries.append(summary)
                if len(self.turn_summaries) > 200:
                    del self.turn_summaries[:-200]

            self._bump_locked()

    def _reset_tracking(self, gs: fish.GameState, ms: fish.MatchState) -> None:
        with self.cond:
            self.log_events = [f"Live game log (seed={self.seed})"]
            self.turn_summaries = []
            self.training_events = []
            self.training_snapshots = []
            if not self.recovery_active:
                self.latest_public_state = None
                self.latest_private_hands = {}
            self.legal_actions_by_seat.clear()
            self.pending_actions.clear()
            self.active_action_seat = None
            self._last_turn_scores = {}
            self.undo_snapshot_gs = None
            self.undo_snapshot_ms = None
            self.undo_eligible_seat = None
            self.undo_valid = False
            self.undo_requested = False
            self._undo_pending_gs = None
            self._undo_pending_ms = None
            self._undo_pending_seat = None
            if self.recovery_active:
                self.status_note = (
                    f"Resyncing game after server restart — step {self.recovery_cursor} of {self.recovery_target_count}. Room is staying open, please wait..."
                )
            else:
                self.status_note = "Game running."
            self._bump_locked()

    def _apply_pending_undo_restore(self, gs: fish.GameState, ms: fish.MatchState) -> Optional["fish.Action"]:
        """Flag-driven undo for when no active human is consuming the request.

        The queue-based path (undo_confirm / undo_mid_turn) only works when a human
        policy is blocked in _wait_for_action ready to pick the command up. When the
        player after you is an AI — or the table is momentarily between turns —
        active_action_seat is None, so there is nobody to route the undo to and the
        request was silently dropped (the bug: "undo does nothing, cards not put
        back"). To fix that, every policy (AI and human) calls this at the top of its
        turn. If an undo is armed (self.undo_requested) and a snapshot exists, restore
        the pre-turn state in place and return Action(kind='undo') so the engine
        rewinds and replays the previous human's turn. Returns None when nothing is
        pending — the common case, a cheap flag read with no copying."""
        gs_restore: Any = None
        ms_restore: Any = None
        with self.cond:
            if self.undo_requested and self.undo_valid and self.undo_snapshot_gs is not None:
                gs_restore = copy.deepcopy(self.undo_snapshot_gs)
                ms_restore = copy.deepcopy(self.undo_snapshot_ms)
        if gs_restore is None:
            return None
        # TRUE REVERT to the turn-start snapshot — same exact deck/END-GAME layout,
        # so re-drawing yields the same cards (no reroll) and end game cannot trigger
        # early. Mirrors the in-policy undo_confirm restore below.
        try:
            _eg_probs = fish.validate_end_game_placement(gs_restore, ms_restore, where="post-undo-restore-flag")
            for _p in _eg_probs:
                self._record_event(f"⚠ END GAME PLACEMENT: {_p}")
        except Exception:
            pass
        gs.__dict__.clear()
        gs.__dict__.update(gs_restore.__dict__)
        ms.__dict__.clear()
        ms.__dict__.update(ms_restore.__dict__)
        with self.cond:
            self.undo_requested = False
            self.undo_valid = False
            self.undo_snapshot_gs = None
            self.undo_snapshot_ms = None
            self._undo_pending_gs = None
            self._undo_pending_ms = None
            self._undo_pending_seat = None
            self.legal_actions_by_seat.clear()
            # A rewind invalidates anything queued for the (now-discarded) future.
            self.pending_actions.clear()
            self.active_action_seat = None
            self.status_note = "Undo granted — replaying previous player's turn."
            self._bump_locked()
        # The engine re-reads p = gs.current_player() after it sees this action, so
        # the stale `player` reference held by the caller is harmless.
        return fish.Action(kind="undo")

    def _human_policy(self, seat_index: int):
        # Per-policy state: when a timeout-fallback fires during the draw phase,
        # auto-end the turn on the very next action request instead of waiting
        # another full timeout window. Prevents cards from piling up over time.
        force_end_turn_next = [False]

        def policy(gs: fish.GameState, ms: fish.MatchState, player: fish.PlayerState) -> Optional[fish.Action]:
            while True:
                # Honor a flag-armed undo (requested while no human was active, e.g.
                # during a bot turn that has now ended) before this human acts.
                undo_action = self._apply_pending_undo_restore(gs, ms)
                if undo_action is not None:
                    return undo_action
                actions = fish.legal_actions(gs, ms, player, include_draw=True)
                replay_action = self._replay_action_if_available(gs, ms, player, seat_index, actions)
                if replay_action is not None:
                    force_end_turn_next[0] = False
                    return replay_action

                # Surf's Up!! Away ALWAYS wins. If the player marked themselves
                # Away — even mid-turn, after a prior timeout already armed a
                # forced end/draw — disarm it and never auto-resolve. The cmd-is-
                # None timeout path below keeps waiting while Away, so the table
                # parks the turn instead of drawing cards for an Away player.
                seat_away_now = self.seats[seat_index] if 0 <= seat_index < len(self.seats) else None
                if seat_away_now is not None and getattr(seat_away_now, "is_away", False):
                    force_end_turn_next[0] = False

                # If the last fallback was a forced draw, immediately end the
                # turn now that end_turn is legal — don't wait another window.
                if force_end_turn_next[0]:
                    force_end_turn_next[0] = False
                    for action in actions:
                        if action.kind == "end_turn":
                            return action
                    # end_turn not legal yet — likely hand > 10 from the forced
                    # draw, so the engine is requiring a discard. For the
                    # draw-for-inactive path we don't want to leave the table
                    # waiting on a player who is gone — fall back to a single
                    # discard so the turn can complete.
                    only_discards_now = bool(actions) and all(
                        a.kind in {"discard_to_pool", "discard_batch_to_pool"} for a in actions
                    )
                    if only_discards_now:
                        return self._safe_fallback_action(gs, ms, player)
                    # Still mid-draw (the 2nd of the 2 turn draws is pending): take
                    # that draw now so an auto-drawn/inactive/AFK player completes a
                    # full 2-card draw and the turn ends — instead of falling
                    # through and parking for another full wait window.
                    second_draw = next(
                        (a for a in actions
                         if a.kind == "draw" and int(getattr(a, "draw_from_pool", 0)) == 0),
                        None,
                    ) or next((a for a in actions if a.kind == "draw"), None)
                    if second_draw is not None:
                        return second_draw
                    # Otherwise fall through and re-offer legal actions normally.

                is_replay_turn = bool(player.flags.pop("_replay_turn_next", False))
                # Build a list of species the player can currently play for free.
                free_play_species: List[str] = []
                if player.flags.get("free_mammal"):
                    free_play_species.append("Mammal")
                if player.flags.get("free_baitfish") or player.flags.get("free_baitfish_chain"):
                    free_play_species.append("Baitfish")
                if player.flags.get("free_game_fish"):
                    free_play_species.append("Game Fish")
                if player.flags.get("free_cephalopods") or player.flags.get("free_cephalopod_once"):
                    free_play_species.append("Cephalopod")
                if player.flags.get("free_crustacean"):
                    free_play_species.append("Crustacean")
                if player.flags.get("free_invertebrate"):
                    free_play_species.append("Invertebrate")
                if player.flags.get("free_coral"):
                    free_play_species.append("Coral")
                try:
                    legal_payload = self._serialize_legal_actions(gs, ms, player, actions)
                    legal_payload["is_replay_turn"] = is_replay_turn
                    legal_payload["free_play_species"] = free_play_species
                except Exception as exc:
                    self._record_event(f"_serialize_legal_actions error for {player.name}: {exc}")
                    legal_payload = {
                        "updated_unix": now_unix(),
                        "player": player.name,
                        "turn_index": int(gs.turn_index),
                        "round_count": int(gs.round_count),
                        "must_discard_to_ten": bool(actions) and all(
                            a.kind in {"discard_to_pool", "discard_batch_to_pool"} for a in actions
                        ),
                        "tarpon_discard_active": bool(player.flags.get("_tarpon_discard_active", False)),
                        "discard_batch_available": any(a.kind == "discard_batch_to_pool" for a in actions),
                        "discard_excess": max(0, len(player.hand) - 10),
                        "hand_limit": 10,
                        "actions": [],
                        "is_replay_turn": is_replay_turn,
                        "free_play_species": free_play_species,
                    }
                with self.cond:
                    if self.phase != "running":
                        return None
                    self.legal_actions_by_seat[seat_index] = legal_payload
                    if self.active_action_seat != seat_index:
                        # Turn boundary — clear any stale inactive_eligible from
                        # the previous player so the Draw-2 button doesn't show
                        # on the wrong avatar.
                        for _s in self.seats:
                            if _s.index != seat_index and _s.inactive_eligible:
                                _s.inactive_eligible = False
                        # Also clear it on the newly-active seat; the client will
                        # re-flag if their idle timer expires on this turn.
                        if 0 <= seat_index < len(self.seats):
                            self.seats[seat_index].inactive_eligible = False
                        # New active player → reset AFK votes/nominations and drop
                        # any stale challenge so each player starts fresh each turn.
                        self._afk_reset_turn_locked()
                    self.active_action_seat = seat_index
                    only_discards = bool(actions) and all(a.kind in {"discard_to_pool", "discard_batch_to_pool"} for a in actions)
                    is_tarpon_phase = bool(player.flags.get("_tarpon_discard_active", False))
                    if is_tarpon_phase:
                        self.status_note = (
                            f"{player.name}: Tarpon — choose cards to discard, then select 'end turn now'."
                        )
                    elif only_discards:
                        excess = max(0, len(player.hand) - (int(fish.HAND_LIMIT) if hasattr(fish, "HAND_LIMIT") else 10))
                        self.status_note = (
                            f"{player.name} has {len(player.hand)} cards — select {excess} or more to discard to the pool."
                        )
                    elif is_replay_turn:
                        self.status_note = f"★ Play again! {player.name} takes another turn."
                    elif free_play_species:
                        species_str = " or ".join(free_play_species)
                        self.status_note = f"★ FREE PLAY: {player.name} — play a free {species_str} (or click End Turn to skip)."
                    elif ms.end_game_triggered:
                        self.status_note = f"Final round! {player.name} — draw and play, or choose 'end turn now' to pass."
                    else:
                        self.status_note = f"Waiting for action from {player.name}."
                    self._bump_locked()

                if not actions:
                    with self.cond:
                        self.legal_actions_by_seat.pop(seat_index, None)
                        if self.active_action_seat == seat_index:
                            self.active_action_seat = None
                        self.status_note = f"{player.name}'s turn ended."
                        self._bump_locked()
                    return None

                # Wait for the human to act. Give a long 30 min window so the game
                # never auto-draws cards or skips turns under any normal play pace —
                # only truly-abandoned games will hit the fallback.
                only_discards = bool(actions) and all(
                    a.kind in {"discard_to_pool", "discard_batch_to_pool"} for a in actions
                )
                wait_sec = 1800.0
                cmd = self._wait_for_action(seat_index, timeout_sec=wait_sec)
                if cmd is not None and cmd.get("kind") == "__undo_armed__":
                    # A flag-driven undo was armed (by the previous player) while we
                    # were blocked waiting for input — e.g. they pressed Undo during
                    # the handoff to us. Re-loop so the top-of-loop
                    # _apply_pending_undo_restore restores the snapshot and returns
                    # Action(undo), replaying the undoing player's turn at once.
                    continue
                if cmd is not None and cmd.get("kind") == "undo_mid_turn":
                    # Player undid during their own turn (e.g. drew first card and changed mind).
                    # Restore the pre-turn snapshot; shuffle deck so the returned card(s)
                    # land at a random position rather than exactly where they were drawn from.
                    gs_restore: Any = None
                    ms_restore: Any = None
                    with self.cond:
                        if self.undo_valid and self.undo_snapshot_gs is not None:
                            gs_restore = copy.deepcopy(self.undo_snapshot_gs)
                            ms_restore = copy.deepcopy(self.undo_snapshot_ms)
                    if gs_restore is not None:
                        # TRUE REVERT: restore the pre-turn deck EXACTLY as it was —
                        # do NOT reshuffle. Reshuffling on undo handed the player a
                        # different ("random") card every time they undid a draw, and
                        # worse, let them reroll their draw by undoing repeatedly. The
                        # snapshot is the turn-start state, so the undone card simply
                        # goes back on top and re-drawing yields the same card. END GAME
                        # also stays exactly where it was at turn start (no scatter), so
                        # the early-end-game bug a plain shuffle caused cannot occur.
                        try:
                            _eg_probs = fish.validate_end_game_placement(gs_restore, ms_restore, where="post-undo-restore")
                            for _p in _eg_probs:
                                self._record_event(f"⚠ END GAME PLACEMENT: {_p}")
                        except Exception:
                            pass
                        gs.__dict__.clear()
                        gs.__dict__.update(gs_restore.__dict__)
                        ms.__dict__.clear()
                        ms.__dict__.update(ms_restore.__dict__)
                        # After restoring gs, `player` still references the
                        # pre-restore PlayerState object — it is no longer in
                        # gs.players. Sync the restored player's data into the
                        # same object and put it back into gs.players so that
                        # any subsequent apply_action(gs, ms, player, …) in the
                        # outer engine loop writes to the right player.
                        try:
                            restored_p = gs.players[gs.turn_index]
                            player.__dict__.clear()
                            player.__dict__.update(restored_p.__dict__)
                            gs.players[gs.turn_index] = player
                        except Exception:
                            pass
                        with self.cond:
                            self.undo_requested = False
                            self.undo_valid = False
                            self.undo_snapshot_gs = None
                            self.undo_snapshot_ms = None
                            self._undo_pending_gs = None
                            self._undo_pending_ms = None
                            self._undo_pending_seat = None
                            self.legal_actions_by_seat.clear()
                            self.pending_actions.clear()
                            self.active_action_seat = None
                            self.status_note = f"{player.name} undid their draw — turn restarted."
                            self._bump_locked()
                        # Rebuild the client-visible public state from the reverted
                        # gs/ms. The queue-based mid-turn undo restores state in place
                        # and loops back inside THIS policy without ever returning to
                        # the engine, so — unlike the full-turn undo, which returns
                        # Action("undo") and gets a fresh turn_start snapshot — no
                        # snapshot runs to refresh latest_public_state. Without this the
                        # client keeps showing the pre-undo hand/deck (legal_actions
                        # update, but hand_count/board do not): the drawn card looks
                        # like it never went back, and a re-draw appears to "do nothing"
                        # because the count never changed. Use a neutral note so the
                        # two-phase undo-arming logic (turn_start:/post_action:) is not
                        # retriggered.
                        try:
                            self._record_snapshot(gs, ms, self.last_turn_number, f"post_undo:{player.name}")
                        except Exception:
                            pass
                    continue  # Re-loop: offer fresh legal actions for the same player

                if cmd is not None and cmd.get("kind") == "undo_confirm":
                    # Previous player requested undo — restore state and signal engine.
                    gs_restore: Any = None
                    ms_restore: Any = None
                    with self.cond:
                        if self.undo_valid and self.undo_snapshot_gs is not None:
                            gs_restore = copy.deepcopy(self.undo_snapshot_gs)
                            ms_restore = copy.deepcopy(self.undo_snapshot_ms)
                    if gs_restore is not None:
                        gs.__dict__.clear()
                        gs.__dict__.update(gs_restore.__dict__)
                        ms.__dict__.clear()
                        ms.__dict__.update(ms_restore.__dict__)
                        with self.cond:
                            self.undo_requested = False
                            self.undo_valid = False
                            self.undo_snapshot_gs = None
                            self.undo_snapshot_ms = None
                            # Clear the pending snapshot too — the turn being replayed
                            # means the "next player's" pending is now stale.
                            self._undo_pending_gs = None
                            self._undo_pending_ms = None
                            self._undo_pending_seat = None
                            self.legal_actions_by_seat.clear()
                            # A rewind invalidates anything queued for the discarded future.
                            self.pending_actions.clear()
                            self.active_action_seat = None
                            self.status_note = "Undo granted — replaying previous player's turn."
                            self._bump_locked()
                        return fish.Action(kind="undo")
                    continue

                if cmd is not None and cmd.get("kind") == "draw_for_inactive":
                    # Another player invoked the draw-2-cards affordance after
                    # the inactive warning expired. Pick a plain deck-draw, arm
                    # the force-end flag so the next policy call ends the turn.
                    by_name = str(cmd.get("by_name") or "Another player")
                    draw_action: Optional[fish.Action] = None
                    for action in actions:
                        if action.kind == "draw" and int(getattr(action, "draw_from_pool", 0)) == 0:
                            draw_action = action
                            break
                    if draw_action is None:
                        for action in actions:
                            if action.kind == "end_turn":
                                draw_action = action
                                break
                    if draw_action is None:
                        with self.cond:
                            self.status_note = f"Could not draw for {player.name} — no draw or end action available."
                            self._bump_locked()
                        continue
                    if draw_action.kind == "draw":
                        force_end_turn_next[0] = True
                        # The player isn't at their screen, so don't force them to
                        # discard back down to the normal hand limit at end of turn.
                        # They keep the cards (up to the extended AFK limit of 20)
                        # so they can return to a fuller hand.
                        player.flags["_afk_no_discard"] = True
                    with self.cond:
                        self.status_note = (
                            f"{by_name} drew 2 cards for {player.name} (inactive)."
                        )
                        self._bump_locked()
                    self._record_event(
                        f"{player.name} (seat {seat_index}) drew 2 cards via inactive-rescue by {by_name}."
                    )
                    return draw_action

                if cmd is None:
                    # Phase ended or player timed out.
                    if self.phase != "running":
                        return None
                    # Protected Surf's Up Away — never auto-resolve; wait again.
                    seat_obj = self.seats[seat_index] if 0 <= seat_index < len(self.seats) else None
                    if seat_obj is not None and getattr(seat_obj, "is_away", False):
                        continue
                    # Timeout = the player is AFK / disconnected / not responding.
                    # CRITICAL: never auto-draw cards for them. _safe_timeout_action
                    # only ends the turn (if it can be passed without drawing) or
                    # discards an over-limit hand — it NEVER draws. If the only way
                    # forward would be a draw, it returns None and we PARK the turn
                    # (keep waiting), exactly like the Surf's Up Away path above.
                    # The only way an away player receives cards is the AFK vote
                    # system (a queued draw_for_inactive command), never a timeout.
                    fallback = self._safe_timeout_action(gs, ms, player)
                    if fallback is None:
                        # Can't pass the turn without drawing → do NOT draw. Park
                        # the turn and wait; other players can vote them AFK
                        # ("<name> is AFK" in chat) to make them draw 2 and pass.
                        with self.cond:
                            self.status_note = (
                                f"Waiting for {player.name} — they appear to be away. "
                                f"Other players can vote them AFK to draw 2 and pass the turn."
                            )
                            self._bump_locked()
                        self._record_event(
                            f"{player.name} (seat {seat_index}) timed out while away — "
                            f"turn parked (no auto-draw; awaiting return or AFK vote)."
                        )
                        continue
                    action_desc = "ending turn" if fallback.kind == "end_turn" else "discarding a card"
                    with self.cond:
                        self.status_note = (
                            f"{player.name} took too long — {action_desc} to keep game moving."
                        )
                        self._bump_locked()
                    self._record_event(
                        f"{player.name} (seat {seat_index}) timed out — {action_desc}."
                    )
                    return fallback

                chosen = self._resolve_submitted_action(gs, ms, player, actions, cmd)
                if chosen is None:
                    with self.cond:
                        self.status_note = "Invalid action submitted. Try again."
                        self._bump_locked()
                    continue
                # Player submitted a real action — clear any pending forced-end flag.
                force_end_turn_next[0] = False
                # Discard actions end the discard phase; clear the stale cache so the
                # client's red banner disappears on the next poll rather than persisting
                # until the player's next turn recalculates legal actions.
                if chosen.kind in {"discard_to_pool", "discard_batch_to_pool"}:
                    with self.cond:
                        self.legal_actions_by_seat.pop(seat_index, None)
                        self._bump_locked()
                # Close the previous player's undo window ONLY when a HUMAN at a
                # different seat acts. Bot turns NEVER lock in your turn — you can
                # still undo after any number of bots have played; only a human
                # acting after you makes it permanent. (This policy runs for human
                # seats, so the actor is human; we resolve the seat kind defensively
                # so e.g. drawing for an away human still counts as a human action.)
                with self.cond:
                    if self.undo_valid and self.undo_eligible_seat != seat_index:
                        _acting_seat = next((s for s in self.seats if s.index == seat_index), None)
                        _acting_is_human = (_acting_seat is None) or (_acting_seat.kind == "human")
                        if _acting_is_human:
                            self.undo_valid = False
                            self._bump_locked()
                return chosen

        return policy

    # Think-delay ranges per speed tier (seconds, low..high inclusive).
    # A random value in the range is sampled each policy call so the bot
    # never feels mechanical — it occasionally plays fast or slow even on
    # Normal to mimic natural human rhythm.
    _AI_THINK_RANGES: Dict[str, Tuple[float, float]] = {
        "slow":   (3.0, 5.5),
        "normal": (1.2, 2.8),
        "fast":   (0.3, 0.9),
    }

    def _build_ai_policy(
        self,
        seat_index: int,
        weights: Dict[str, float],
        synergy_map: Dict[str, float],
        species_map: Dict[str, float],
        same_ocean_map: Dict[str, float],
        strategy_value_map: Dict[str, float],
        strategy_count_map: Dict[str, int],
        strategy_transition_map: Dict[str, float],
        strategy_transition_count_map: Dict[str, int],
    ):
        def policy(gs: fish.GameState, ms: fish.MatchState, player: fish.PlayerState) -> Optional[fish.Action]:
            # Honor a human's pending undo before this bot acts. Without this, undo
            # silently failed for the entire duration of every AI turn (the active
            # seat is None during bot turns, so there was nobody to route it to).
            undo_action = self._apply_pending_undo_restore(gs, ms)
            if undo_action is not None:
                return undo_action

            actions = fish.legal_actions(gs, ms, player, include_draw=True)
            replay_action = self._replay_action_if_available(gs, ms, player, seat_index, actions)
            if replay_action is not None:
                return replay_action

            _brain_scored: List["tuple[fish.Action, float]"] = []
            _think_started = time.monotonic()
            chosen = choose_action_weighted_deep(
                gs,
                ms,
                player,
                weights,
                synergy_map,
                species_map,
                same_ocean_map,
                strategy_value_map,
                strategy_count_map,
                strategy_transition_map,
                strategy_transition_count_map,
                out_scored=_brain_scored,
            )
            # Deep planning costs real wall-clock time; count it as "thinking"
            # so the visible pause below doesn't stack on top of it.
            _compute_elapsed = time.monotonic() - _think_started

            # ── Current Controller: record what this bot is thinking, and let an
            # admin override its move. Both are no-ops unless the admin has opened
            # the mod tools in this room, so normal bot play is unaffected.
            if self._admin_active:
                try:
                    self._record_bot_brain(seat_index, gs, ms, player, chosen, _brain_scored, actions)
                    override = self._consume_bot_override(seat_index, gs, ms, player, actions)
                    if override is not None:
                        return override
                except Exception as _exc:
                    self._record_event(f"bot brain/override warning ({player.name}): {_exc}")

            # Think delay — simulate the bot "considering" its move so the
            # game doesn't feel like AI is moving instantly.  Read ai_speed
            # at call time so host changes take effect immediately.
            speed = str(getattr(self, "ai_speed", "normal") or "normal").lower()
            lo, hi = self._AI_THINK_RANGES.get(speed, self._AI_THINK_RANGES["normal"])
            delay = max(0.0, lo + random.random() * (hi - lo) - _compute_elapsed)
            if delay > 0:
                # Interruptible think pause. A plain time.sleep() here meant a human's
                # Undo armed mid-"thinking" wasn't honored until AFTER this bot's move
                # was applied (the board flashed the bot's play, then reverted seconds
                # later). Bail out of the wait the instant an undo is armed; _bump_locked
                # in submit_undo notifies us. Runs on the match thread, so parking it
                # here is exactly equivalent to the old sleep w.r.t. the engine.
                _deadline = time.monotonic() + delay
                with self.cond:
                    while True:
                        _remaining = _deadline - time.monotonic()
                        if _remaining <= 0:
                            break
                        if (
                            self.undo_requested
                            and self.undo_valid
                            and self.undo_snapshot_gs is not None
                        ):
                            break
                        self.cond.wait(timeout=min(0.1, _remaining))

            # Honor an undo armed during (or just before) the think pause BEFORE the
            # bot's chosen move is applied, so the play is never even shown on its way
            # to being reverted.
            undo_action = self._apply_pending_undo_restore(gs, ms)
            if undo_action is not None:
                return undo_action

            return chosen

        return policy

    # ════════════════════════════════════════════════════════════════
    #  CURRENT CONTROLLER — admin mod tools (server side, hard-gated)
    #  Reads come from the latest snapshot; mutations are queued and applied
    #  on the MATCH THREAD (drained in _wait_for_action) so they can never race
    #  the game engine.
    # ════════════════════════════════════════════════════════════════
    def _describe_action(self, gs, ms, action) -> Dict[str, Any]:
        try:
            kind = getattr(action, "kind", "?")
            if kind == "draw":
                label = "draw from pool" if int(getattr(action, "draw_from_pool", 0) or 0) else "draw from deck"
                return {"kind": kind, "label": label, "draw_from_pool": int(getattr(action, "draw_from_pool", 0) or 0)}
            if kind == "discard_batch_to_pool":
                picks = list(getattr(action, "pool_pick_uids", []) or [])
                label = f"discard {len(picks)} card(s) to pool" if picks else "discard selected cards to pool"
                return {"kind": kind, "label": label, "pool_pick_uids": picks}
            face_uid = action.face_uid if getattr(action, "face_uid", None) is not None else getattr(action, "card_uid", None)
            card = gs.card_db.get(face_uid) if face_uid is not None else None
            name = card.name if card is not None else (f"#{face_uid}" if face_uid is not None else "")
            ocean_uid = getattr(action, "ocean_uid", None)
            ocard = gs.card_db.get(ocean_uid) if ocean_uid is not None else None
            ocean = ocard.name if ocard is not None else None
            parts = [kind]
            if name:
                parts.append(name)
            if ocean:
                parts.append("→ " + ocean)
            if getattr(action, "use_star", False):
                parts.append("★")
            return {
                "kind": kind, "label": " ".join(parts),
                "card_uid": getattr(action, "card_uid", None), "face_uid": face_uid,
                "ocean_uid": ocean_uid, "use_star": bool(getattr(action, "use_star", False)),
                "draw_from_pool": int(getattr(action, "draw_from_pool", 0) or 0),
            }
        except Exception:
            return {"kind": getattr(action, "kind", "?"), "label": str(getattr(action, "kind", "?"))}

    def _record_bot_brain(self, seat_index, gs, ms, player, chosen, scored, actions) -> None:
        try:
            strat = str(player.flags.get("_strategy_family") or fish.detect_player_strategy(gs, player) or "")
        except Exception:
            strat = ""
        cands = []
        for act, score in (scored or [])[:6]:
            d = self._describe_action(gs, ms, act)
            d["score"] = round(float(score), 3)
            cands.append(d)
        reason = ""
        if cands:
            reason = f"Highest score {cands[0]['score']} → {cands[0]['label']}" + (f" (strategy: {strat})" if strat else "")
        with self.cond:
            self._bot_brain[seat_index] = {
                "seat": seat_index, "name": player.name, "strategy": strat,
                "chosen": self._describe_action(gs, ms, chosen) if chosen is not None else None,
                "candidates": cands, "reason": reason, "at": time.time(),
                "legal_actions": [self._describe_action(gs, ms, a) for a in (actions or [])[:40]],
            }

    def _consume_bot_override(self, seat_index, gs, ms, player, actions):
        with self.cond:
            spec = self._bot_override.pop(seat_index, None)
        if not spec:
            return None
        # Prefer matching by action signature (robust if the legal-action list
        # shifted since the override was armed); fall back to the raw index.
        desc = spec.get("desc")
        if isinstance(desc, dict):
            for a in actions:
                d = self._describe_action(gs, ms, a)
                if (d.get("kind") == desc.get("kind")
                        and d.get("card_uid") == desc.get("card_uid")
                        and d.get("ocean_uid") == desc.get("ocean_uid")
                        and int(d.get("draw_from_pool", 0) or 0) == int(desc.get("draw_from_pool", 0) or 0)
                        and bool(d.get("use_star")) == bool(desc.get("use_star"))):
                    self._record_event(f"Admin override: {player.name} forced to {d.get('label')}")
                    return a
        idx = spec.get("action_index")
        if isinstance(idx, int) and 0 <= idx < len(actions):
            chosen = actions[idx]
            self._record_event(f"Admin override: {player.name} forced to {self._describe_action(gs, ms, chosen).get('label')}")
            return chosen
        return None

    def _seat_player(self, gs, seat_index):
        g_idx = getattr(self, "_comp_seat_to_game", {}).get(seat_index, seat_index)
        if 0 <= g_idx < len(gs.players):
            return gs.players[g_idx]
        return None

    def _zone_find_and_remove(self, gs, ms, uid):
        uid = int(uid)
        if uid in gs.deck:
            gs.deck.remove(uid); return "deck"
        if uid in ms.pool:
            ms.pool.remove(uid); return "pool"
        if uid in ms.discard_pile:
            ms.discard_pile.remove(uid); return "pool_discard"
        for p in gs.players:
            if uid in p.hand:
                p.hand.remove(uid); return "hand"
            if uid in p.discard:
                p.discard.remove(uid); return "discard"
        return None

    def _mint_card_clone(self, gs, ms, src_uid):
        """Create a brand-new physical copy of the card identified by src_uid
        (any face), cloned from the canonical card definition. The first time a
        game mints, it gives that game its OWN card_db (a shallow copy — we only
        ever add new keys, never mutate existing CardDefs) so minted cards never
        leak into the process-wide CARD_DB shared by every other game. Registers
        the clone's abilities + match pair-map and returns the new canonical
        (primary) uid — or None if the source card is unknown."""
        try:
            src_uid = int(src_uid)
        except (TypeError, ValueError):
            return None
        canon = fish.canonical_entry_uid(ms, src_uid)
        faces = fish.entry_faces(ms, canon)
        if not faces:
            return None
        if gs.card_db is CARD_DB:
            gs.card_db = dict(gs.card_db)
        serial = _alloc_mint_serial()
        new_faces: List[int] = []
        for fuid in faces:
            cd = gs.card_db.get(int(fuid)) or CARD_DB.get(int(fuid))
            if cd is None:
                return None
            new_uid = serial * 1000 + int(fuid)  # low 3 digits = original art face
            new_cd = dataclass_replace(cd, uid=new_uid)
            gs.card_db[new_uid] = new_cd
            try:
                fish._register_card_ability_impl(new_cd)
            except Exception:
                pass
            new_faces.append(new_uid)
        new_primary = new_faces[0]
        if len(new_faces) == 2:
            ms.pair_primary_to_faces[new_primary] = (new_faces[0], new_faces[1])
            ms.face_to_primary[new_faces[0]] = new_primary
            ms.face_to_primary[new_faces[1]] = new_primary
        else:
            ms.face_to_primary[new_primary] = new_primary
        return new_primary

    def _admin_apply_mod_locked(self, gs, ms, op, params) -> Dict[str, Any]:
        """Apply one mod mutation. Caller holds self.cond and runs on the match thread."""
        op = str(op or "")
        P = params or {}
        if op == "hand_add":
            pl = self._seat_player(gs, int(P.get("seat")))
            if pl is None:
                return {"ok": False, "error": "bad seat"}
            src = self._zone_find_and_remove(gs, ms, int(P.get("uid")))
            if src is None:
                return {"ok": False, "error": "uid not found in any zone"}
            pl.hand.append(int(P.get("uid")))
            return {"ok": True, "moved_from": src}
        if op == "hand_remove":
            pl = self._seat_player(gs, int(P.get("seat")))
            uid = int(P.get("uid"))
            if pl is None or uid not in pl.hand:
                return {"ok": False, "error": "card not in that hand"}
            pl.hand.remove(uid); pl.discard.append(uid)
            return {"ok": True}
        if op == "hand_clear":
            pl = self._seat_player(gs, int(P.get("seat")))
            if pl is None:
                return {"ok": False, "error": "bad seat"}
            n = len(pl.hand); pl.discard.extend(pl.hand); pl.hand.clear()
            return {"ok": True, "cleared": n}
        if op == "hand_copy_to_me":
            target = self._seat_player(gs, int(P.get("seat")))
            me = self._seat_player(gs, int(P.get("my_seat")))
            if target is None or me is None:
                return {"ok": False, "error": "bad seat"}
            copied = missed = 0
            for uid in list(target.hand):
                cd0 = gs.card_db.get(uid)
                match = None
                for duid in list(gs.deck):
                    dcd = gs.card_db.get(duid)
                    if dcd and cd0 and dcd.name == cd0.name:
                        match = duid; break
                if match is not None:
                    gs.deck.remove(match); me.hand.append(match); copied += 1
                else:
                    missed += 1
            return {"ok": True, "copied": copied, "unavailable": missed}
        if op == "pool_clear":
            n = len(ms.pool); ms.discard_pile.extend(ms.pool); ms.pool.clear()
            return {"ok": True, "cleared": n}
        if op == "pool_add":
            src = self._zone_find_and_remove(gs, ms, int(P.get("uid")))
            if src is None:
                return {"ok": False, "error": "uid not found"}
            fish.add_to_pool(ms, int(P.get("uid")))
            return {"ok": True, "moved_from": src}
        if op == "pool_remove":
            uid = int(P.get("uid"))
            if uid not in ms.pool:
                return {"ok": False, "error": "not in pool"}
            ms.pool.remove(uid); ms.discard_pile.append(uid)
            return {"ok": True}
        if op == "pool_refill":
            target = int(P.get("count", 6)); added = 0
            while len(ms.pool) < target and gs.deck:
                fish.add_to_pool(ms, gs.deck.pop(0)); added += 1
            return {"ok": True, "added": added}
        if op == "deck_place":
            uid = int(P.get("uid")); dest = str(P.get("dest"))
            src = self._zone_find_and_remove(gs, ms, uid)
            if src is None:
                return {"ok": False, "error": "uid not found"}
            if dest == "deck_top":
                gs.deck.insert(0, uid)
            elif dest == "deck_bottom":
                gs.deck.append(uid)
            elif dest == "pool":
                fish.add_to_pool(ms, uid)
            elif dest == "discard":
                ms.discard_pile.append(uid)
            elif dest == "hand":
                pl = self._seat_player(gs, int(P.get("seat")))
                if pl is None:
                    gs.deck.insert(0, uid); return {"ok": False, "error": "bad seat — returned to deck"}
                pl.hand.append(uid)
            else:
                gs.deck.insert(0, uid); return {"ok": False, "error": "bad dest — returned to deck"}
            return {"ok": True, "moved_from": src}
        if op == "mint":
            # Create fresh copies of ANY card and place them. Unlike the move-based
            # ops above, mint never consumes a real deck copy — so the admin can
            # grant cards that aren't in the deck and FLOOD a hand with N copies.
            dest = str(P.get("dest", "hand") or "hand")
            try:
                count = int(P.get("count", 1))
            except (TypeError, ValueError):
                count = 1
            count = max(1, min(count, 50))
            seat_pl = None
            if dest == "hand":
                seat_pl = self._seat_player(gs, int(P.get("seat")))
                if seat_pl is None:
                    return {"ok": False, "error": "bad seat"}
            elif dest not in ("pool", "deck_top", "deck_bottom", "discard"):
                return {"ok": False, "error": f"bad dest {dest}"}
            minted = 0
            for _ in range(count):
                new_uid = self._mint_card_clone(gs, ms, P.get("uid"))
                if new_uid is None:
                    break
                if dest == "hand":
                    seat_pl.hand.append(new_uid)
                elif dest == "pool":
                    fish.add_to_pool(ms, new_uid)
                elif dest == "deck_top":
                    gs.deck.insert(0, new_uid)
                elif dest == "deck_bottom":
                    gs.deck.append(new_uid)
                elif dest == "discard":
                    ms.discard_pile.append(new_uid)
                minted += 1
            if minted == 0:
                return {"ok": False, "error": "could not mint that card"}
            return {"ok": True, "minted": minted, "dest": dest}
        return {"ok": False, "error": f"unknown op {op}"}

    def _drain_admin_mods_locked(self) -> None:
        """Drain queued mod mutations. Caller holds self.cond; runs on match thread."""
        if not self._admin_mod_queue:
            return
        gs = self._live_gs; ms = self._live_ms
        applied = False
        for item in list(self._admin_mod_queue):
            try:
                if gs is None or ms is None:
                    item["result"] = {"ok": False, "error": "no live game state"}
                else:
                    res = self._admin_apply_mod_locked(gs, ms, item.get("op"), item.get("params") or {})
                    item["result"] = res
                    if res.get("ok"):
                        applied = True
            except Exception as exc:
                item["result"] = {"ok": False, "error": f"{exc}"}
            ev = item.get("event")
            if ev is not None:
                ev.set()
        self._admin_mod_queue.clear()
        self.cond.notify_all()
        if applied and gs is not None and ms is not None:
            try:
                self._record_snapshot(gs, ms, turn_number=self.last_turn_number, note="admin_mod")
            except Exception as exc:
                self._record_event(f"admin_mod re-snapshot warning: {exc}")
            self._bump_locked()

    def admin_enqueue_mod(self, op, params, timeout=6.0) -> Dict[str, Any]:
        ev = threading.Event()
        item = {"id": secrets.token_hex(6), "op": op, "params": params, "event": ev, "result": None}
        with self.cond:
            if self.phase != "running":
                return {"ok": False, "error": "game is not running"}
            self._admin_mod_queue.append(item)
            self.cond.notify_all()
        if not ev.wait(timeout=timeout):
            with self.cond:
                try:
                    self._admin_mod_queue.remove(item)
                except ValueError:
                    pass
            return {"ok": False, "error": "timed out — mutations apply during a human turn; try again on your own turn"}
        return item.get("result") or {"ok": False, "error": "no result"}

    def admin_arm_bot_override(self, seat_index, action_index, action_desc=None) -> Dict[str, Any]:
        with self.cond:
            if action_index is None and not action_desc:
                self._bot_override.pop(int(seat_index), None)
                return {"ok": True, "cleared": True}
            self._bot_override[int(seat_index)] = {
                "action_index": int(action_index) if action_index is not None else None,
                "desc": action_desc if isinstance(action_desc, dict) else None,
            }
        return {"ok": True, "armed": True}

    def _admin_capture_now_locked(self) -> None:
        """One-shot capture of hidden state (deck / pool / discard / end-game)
        straight from the live engine refs, so the very first reveal right after
        the admin opens the tools isn't empty. Caller holds self.cond; no-op if
        there's no live game yet."""
        gs = getattr(self, "_live_gs", None)
        ms = getattr(self, "_live_ms", None)
        if gs is None or ms is None:
            return
        try:
            self._admin_pool_entries = [entry_to_dict(ms, gs, uid) for uid in list(ms.pool) if isinstance(uid, int)]
            self._admin_deck_entries = [entry_to_dict(ms, gs, uid) for uid in list(gs.deck) if isinstance(uid, int)]
            discards: Dict[int, List[Dict[str, Any]]] = {}
            for g_idx, pl in enumerate(gs.players):
                s_idx = self._comp_game_to_seat.get(g_idx, g_idx)
                discards[s_idx] = [entry_to_dict(ms, gs, uid) for uid in list(pl.discard) if isinstance(uid, int)]
            self._admin_discards = discards
            eg_uid = getattr(ms, "end_game_uid", None)
            self._admin_endgame = {
                "end_game_uid": eg_uid,
                "triggered": bool(getattr(ms, "end_game_triggered", False)),
                "in_deck": isinstance(eg_uid, int) and eg_uid in gs.deck,
                "deck_position_from_bottom": (len(gs.deck) - gs.deck.index(eg_uid)) if (isinstance(eg_uid, int) and eg_uid in gs.deck) else None,
            }
        except Exception as _exc:
            self._record_event(f"admin snapshot capture warning: {_exc}")

    def admin_activate(self) -> None:
        """Turn on this room's hidden-state capture the first time the admin uses
        a mod tool here. On first activation it also captures immediately so the
        opening reveal has data without waiting for the next state push."""
        with self.cond:
            first = not self._admin_active
            self._admin_active = True
            if first:
                self._admin_capture_now_locked()

    def admin_reveal(self) -> Dict[str, Any]:
        with self.cond:
            pub = self.latest_public_state if isinstance(self.latest_public_state, dict) else {}
            # All of these are only populated once a game has launched; default
            # them so a reveal in the lobby returns empties instead of erroring.
            admin_discards = getattr(self, "_admin_discards", {}) or {}
            admin_pool = getattr(self, "_admin_pool_entries", []) or []
            admin_deck = getattr(self, "_admin_deck_entries", []) or []
            admin_endgame = getattr(self, "_admin_endgame", {}) or {}
            bot_brain = getattr(self, "_bot_brain", {}) or {}
            players = []
            for p in (pub.get("players") or []):
                s_idx = p.get("index")
                players.append({
                    "index": s_idx, "name": p.get("name"), "score": p.get("score"),
                    "hand": copy.deepcopy(self.latest_private_hands.get(s_idx, [])),
                    "hand_count": p.get("hand_count"),
                    "discard": copy.deepcopy(admin_discards.get(s_idx, [])),
                    "strategy": p.get("strategy"),
                    "board": p.get("board"),
                })
            return {
                "ok": True,
                "phase": self.phase,
                "current_player": pub.get("current_player"),
                "round_count": pub.get("round_count"),
                "players": players,
                "pool": copy.deepcopy(admin_pool),
                "deck": copy.deepcopy(admin_deck),
                "deck_count": len(admin_deck),
                "end_game": copy.deepcopy(admin_endgame),
                "bot_brain": copy.deepcopy(bot_brain),
                "seat_kinds": {s.index: s.kind for s in self.seats},
            }

    def _safe_fallback_action(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        player: fish.PlayerState,
    ) -> Optional[fish.Action]:
        try:
            actions = fish.legal_actions(gs, ms, player, include_draw=True)
        except Exception:
            return None
        if not actions:
            return None
        # Priority 1: end_turn — cleanest exit, doesn't auto-draw or auto-play.
        # If end_turn is legal, the player is past the draw phase, so we can
        # just end their turn without taking any card-modifying action.
        for action in actions:
            if action.kind == "end_turn":
                return action
        # Priority 2: plain deck draw — required when turn can't end yet (draw
        # phase). Drawing from the deck is robust under partial client desync.
        for action in actions:
            if action.kind == "draw" and int(getattr(action, "draw_from_pool", 0)) == 0:
                return action
        # Priority 3: single discard — during discard-mode (hand > 10). Avoid
        # the batch variant which would wipe a hand-load of cards at once.
        for action in actions:
            if action.kind == "discard_to_pool":
                return action
        # Last resort: first non-batch action to avoid side-effects.
        for action in actions:
            if action.kind != "discard_batch_to_pool":
                return action
        return None

    def _safe_timeout_action(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        player: fish.PlayerState,
    ) -> Optional[fish.Action]:
        """Fallback action for an inactive / AFK / disconnected / non-responding
        player whose turn timer expired. UNLIKE _safe_fallback_action, this
        NEVER draws cards. An away player must never have cards drawn for them
        automatically — cards are only ever given through the AFK vote system
        (a `draw_for_inactive` command queued by _afk_resolve_challenge after a
        valid ≥50% vote, or by another player via the draw_for_inactive action).

        Returns:
          • end_turn  — if the turn can be passed WITHOUT drawing (e.g. final
                        round, or any state where ending is already legal);
          • a single discard — only when the hand is over the limit, which
                        removes cards and never gives them;
          • None      — when the only way forward would be to draw a card, so
                        the caller must PARK the turn and keep waiting instead.
        """
        try:
            actions = fish.legal_actions(gs, ms, player, include_draw=True)
        except Exception:
            return None
        if not actions:
            return None
        # Priority 1: end_turn — passes the turn without touching the deck.
        for action in actions:
            if action.kind == "end_turn":
                return action
        # Priority 2: single discard — only reachable when the hand is already
        # over the limit. Discarding removes cards; it never adds any.
        for action in actions:
            if action.kind == "discard_to_pool":
                return action
        # Otherwise the only legal way forward is to DRAW. We must not draw for
        # an away/inactive player — return None so the turn parks and waits.
        return None

    def _wrap_policy_with_fallback(self, seat_label: str, base_policy, seat_index: Optional[int] = None):
        def wrapped(gs: fish.GameState, ms: fish.MatchState, player: fish.PlayerState) -> Optional[fish.Action]:
            try:
                return base_policy(gs, ms, player)
            except Exception as exc:
                self._record_event(
                    f"Policy error on {seat_label} ({player.name}): {exc}. "
                    "Using safe fallback action."
                )
                # During the discard phase, returning any fallback action would silently
                # discard cards the player didn't choose.  Return None instead so the
                # game loop treats this as "no action available" and handles it without
                # auto-discarding.
                if player.flags.get("_discard_mode"):
                    return None
                # Never auto-draw for an away player, even on an error fallback —
                # a "fallback move" is not a valid reason to add cards to an away
                # player's hand (only a vote is). Use the non-drawing variant so
                # the recovery can end/discard but never draw for them.
                seat_obj = (
                    self.seats[seat_index]
                    if seat_index is not None and 0 <= seat_index < len(self.seats)
                    else None
                )
                if seat_obj is not None and getattr(seat_obj, "is_away", False):
                    self._record_event(
                        f"Blocked auto-draw fallback for away player {player.name} "
                        f"(seat {seat_index}) — away players only draw via vote."
                    )
                    return self._safe_timeout_action(gs, ms, player)
                return self._safe_fallback_action(gs, ms, player)

        return wrapped

    def _build_training_record(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        standings: List[Dict[str, Any]],
        human_indices: set[int],
    ) -> Dict[str, Any]:
        top_score = float(standings[0]["score"]) if standings else 0.0
        low_score = float(standings[-1]["score"]) if standings else 0.0
        spread = top_score - low_score
        winner_name = standings[0]["name"] if standings else "N/A"
        winner_index = next((i for i, p in enumerate(gs.players) if p.name == winner_name), -1)
        winner_is_human = winner_index in human_indices

        score_bits = []
        if top_score >= 120:
            score_bits.append("high_top_score")
        if spread >= 30:
            score_bits.append("large_score_spread")
        if winner_is_human:
            score_bits.append("human_winner")

        valuable = bool(score_bits) and (winner_is_human or top_score >= 140)

        key_turns = [
            s
            for s in self.turn_summaries
            if any(int(v) > 0 for v in s.get("score_deltas", {}).values())
        ]

        winner_combo_pattern: Dict[str, int] = {}
        if 0 <= winner_index < len(gs.players):
            wp = gs.players[winner_index]
            for uid in fish.player_board_face_uids(wp):
                card = gs.card_db.get(uid)
                if card is None:
                    continue
                nm = card.name
                winner_combo_pattern[nm] = winner_combo_pattern.get(nm, 0) + 1

        record = {
            "recorded_unix": now_unix(),
            "room_id": self.room_id,
            "seed": int(self.seed),
            "config": {
                "total_players": self.total_players,
                "human_players": self.human_players,
                "ai_players": self.ai_players,
            },
            "standings": standings,
            "winner": winner_name,
            "top_score": top_score,
            "score_spread": spread,
            "valuable": valuable,
            "valuable_reasons": score_bits,
            "move_log": list(self.training_events),
            "turn_summaries": list(self.turn_summaries),
            "key_turns": key_turns,
            "snapshots": list(self.training_snapshots),
            "winner_combo_pattern": winner_combo_pattern,
            "deck_remaining": len(gs.deck),
            "pool_count": len(ms.pool),
            "discard_count": len(ms.discard_pile),
            "human_indices": sorted(int(i) for i in human_indices),
            "has_human_players": bool(human_indices),
        }
        return record

    def _append_training_record(self, record: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(DATASET_PATH), exist_ok=True)
        row = json.dumps(record, separators=(",", ":"))
        with DATASET_LOCK:
            with open(DATASET_PATH, "a", encoding="utf-8") as f:
                f.write(row + "\n")

    def _detect_strategy(self, gs: Any) -> str:
        """Strategy of the top-scoring player, via the guide-based per-player
        detector (single source of truth). Used for competitive game records."""
        try:
            players = list(getattr(gs, "players", []) or [])
            if players:
                top = max(players, key=lambda p: fish.final_points(gs, p))
                return fish.detect_player_strategy(gs, top)
        except Exception:
            pass
        return self._detect_strategy_legacy(gs)

    def _detect_strategy_legacy(self, gs: Any) -> str:
        """Classify the winning strategy using the official strategy guide names."""
        type_counts: Dict[str, int] = {}
        name_counts: Dict[str, int] = {}
        total_cards = 0
        for player in gs.players:
            for ocean_uid in getattr(player, "board_oceans", []):
                slots = player.ocean_slots.get(int(ocean_uid)) if hasattr(player, "ocean_slots") else None
                card_uids = slots.all_cards() if slots else []
                for uid in card_uids:
                    card = gs.card_db.get(uid)
                    if card is None:
                        continue
                    ctype = str(getattr(card, "species", "") or "").strip().lower()
                    cname = str(getattr(card, "name", "") or "").strip().lower()
                    if ctype:
                        type_counts[ctype] = type_counts.get(ctype, 0) + 1
                    if cname:
                        name_counts[cname] = name_counts.get(cname, 0) + 1
                    total_cards += 1
        if total_cards == 0:
            return "Best Guess"
        def pct(key: str) -> float:
            return type_counts.get(key, 0) / total_cards
        # Goby "Shooting the Moon" — any Goby present is the tell.
        if name_counts.get("mandarin goby", 0) >= 1:
            return "Goby"
        # Coral/Cephalopods — cephalopods + meaningful coral base.
        if pct("cephalopod") >= 0.30 and pct("coral") >= 0.15:
            return "Coral/Cephalopods (CC)"
        # Pure Cephalopods
        if pct("cephalopod") >= 0.40:
            return "Cephalopods"
        # Bird/Lobster — birds + crustaceans.
        if pct("bird") >= 0.20 and (pct("crustacean") >= 0.10 or name_counts.get("lobster", 0) >= 1 or name_counts.get("mantis shrimp", 0) >= 1):
            return "Bird/Lobster (B-Lob)"
        # Bird/Coral — birds + coral base.
        if pct("bird") >= 0.20 and pct("coral") >= 0.15:
            return "Bird/Coral (B-Coral)"
        # Pure bird board.
        if pct("bird") >= 0.35:
            return "Bird/Coral (B-Coral)"
        # Baitfish Barrage
        if pct("baitfish") >= 0.35:
            return "Baitfish Barrage"
        # Mammals
        if pct("mammal") >= 0.45:
            return "Mammals"
        # Yellowfin Tuna Stack — key engine card present.
        if name_counts.get("yellowfin tuna", 0) >= 1:
            return "Yellowfin Tuna"
        # Ocean All Blue — majority ocean cards.
        if pct("ocean") >= 0.40 or pct("coral") >= 0.50:
            return "Ocean All Blue"
        # Game Fish catch-all
        if pct("game fish") >= 0.40:
            return "Game Fish"
        return "Best Guess"

    def _check_competitive_forfeit_locked(self) -> None:
        """Competitive only: if one player left the running match and has not
        returned within COMPETITIVE_FORFEIT_SEC, end the game as a forfeit — the
        player still present wins, the one who left loses. MUST be called with
        self.cond held (it mutates phase/winner). Polled from state_view, so the
        remaining player's own polling naturally triggers it within ~1s of the
        30-second mark."""
        if not (self.competitive and len(self.seats) == 4):
            return
        if self.phase != "running":
            return
        if self._forfeit_result is not None:
            return
        now = time.time()
        # Each physical player owns two seats: P1 = {0,1}, P2 = {2,3}. A player
        # is "present" while either of their seats is still polling (last_seen
        # fresh) and not on an expired leave reservation. A player whose most
        # recent activity across BOTH hands is older than the forfeit window has
        # left/closed/crashed — they forfeit. This is polled from state_view, so
        # the player still here just refreshed their own last_seen and counts as
        # present; we only ever forfeit the OTHER (absent) player.
        def _group_present(group: int) -> bool:
            for s in self.seats:
                if s.kind != "human" or (s.index // 2) != group:
                    continue
                ls = s.last_seen
                if ls is not None and (now - ls) < COMPETITIVE_FORFEIT_SEC:
                    if s.left_at is None or (now - s.left_at) < COMPETITIVE_FORFEIT_SEC:
                        return True
            return False
        p1_present = _group_present(0)
        p2_present = _group_present(1)
        # Only call a forfeit when exactly one side is present (the other left).
        # If neither is present, no one is around to award the win to — let the
        # room be cleaned up normally instead.
        leaver_group: Optional[int] = None
        if p1_present and not p2_present:
            leaver_group = 1
        elif p2_present and not p1_present:
            leaver_group = 0
        if leaver_group is None:
            return
        p1_name = (self.seats[0].claimed_name or "Player 1") if len(self.seats) > 0 else "Player 1"
        p2_name = (self.seats[2].claimed_name or "Player 2") if len(self.seats) > 2 else "Player 2"
        loser_name  = p1_name if leaver_group == 0 else p2_name
        winner_name = p2_name if leaver_group == 0 else p1_name
        self.phase = "ended"
        self.ended_unix = now_unix()
        self.winner = winner_name
        self.active_action_seat = None
        self.legal_actions_by_seat.clear()
        # Publish a final_scores tally from the current board so the winner's
        # client can locate its entry and award XP/streak/history normally (the
        # match was mid-play, so final_scores was still empty).
        try:
            fs: List[Dict[str, Any]] = []
            if isinstance(self.latest_public_state, dict):
                for p in self.latest_public_state.get("players", []) or []:
                    if isinstance(p, dict) and p.get("name"):
                        fs.append({"name": str(p["name"]), "score": int(p.get("score", 0) or 0)})
            if fs:
                fs.sort(key=lambda e: e["score"], reverse=True)
                self.final_scores = fs
        except Exception:
            pass
        self._forfeit_result = {
            "forfeit": True,
            "winner": winner_name,
            "loser": loser_name,
            "reason": (f"{loser_name} left the match and did not return within "
                       f"{int(COMPETITIVE_FORFEIT_SEC)} seconds — {winner_name} wins by forfeit."),
        }
        self.status_note = (
            f"{loser_name} forfeited (left the match for {int(COMPETITIVE_FORFEIT_SEC)}s). "
            f"{winner_name} wins."
        )
        # Block the normal end-of-game competitive save (no end-game trigger here).
        self._competitive_saved = True
        try:
            self._save_competitive_forfeit(winner_name, loser_name)
        except Exception as exc:
            self._record_event(f"Forfeit save warning: {exc}")
        self._record_event(f"Competitive forfeit: {loser_name} left; {winner_name} wins.")
        self._bump_locked(force_persist=True)

    def _save_competitive_forfeit(self, winner_name: str, loser_name: str) -> None:
        """Write a competitive game record for a forfeit AND enqueue a pending
        forfeit-loss for the (offline) loser so their CP penalty is applied the
        next time their client loads. Winner's CP is applied live by their
        client when it sees the forfeit result in the state payload."""
        os.makedirs(COMPETITIVE_GAMES_DIR, exist_ok=True)
        ts = now_unix()
        # Best-effort current scores for the record (forfeits have no final tally).
        score_map: Dict[str, int] = {}
        try:
            if isinstance(self.latest_public_state, dict):
                for p in self.latest_public_state.get("players", []) or []:
                    if isinstance(p, dict) and p.get("name"):
                        score_map[str(p["name"])] = int(p.get("score", 0) or 0)
        except Exception:
            score_map = {}
        p1_name = (self.seats[0].claimed_name or "Player 1") if len(self.seats) > 0 else "Player 1"
        p2_name = (self.seats[2].claimed_name or "Player 2") if len(self.seats) > 2 else "Player 2"
        record = {
            "room_id": self.room_id,
            "recorded_unix": ts,
            "season_id": get_season_id(ts),
            "p1_name": p1_name,
            "p2_name": p2_name,
            "p1_best_score": int(score_map.get(p1_name, 0)),
            "p2_best_score": int(score_map.get(p2_name, 0)),
            "p1_second_score": int(score_map.get(p1_name, 0)),
            "p2_second_score": int(score_map.get(p2_name, 0)),
            "winner": winner_name,
            "loser": loser_name,
            "is_draw": False,
            "forfeit": True,
            "ranked": False,
            "turn_count": 0,
            "strategy": "Forfeit",
            "standings": [
                {"name": winner_name, "score": int(score_map.get(winner_name, 0))},
                {"name": loser_name,  "score": int(score_map.get(loser_name, 0))},
            ],
            "board_snapshot": {},
        }
        game_path = os.path.join(COMPETITIVE_GAMES_DIR, f"game_{self.room_id}_{ts}.json")
        atomic_write_json(game_path, record)
        try:
            self._update_competitive_leaderboard(p1_name, p2_name, winner_name, is_draw=False)
        except Exception as exc:
            self._record_event(f"Forfeit leaderboard warning: {exc}")
        # Enqueue the pending loss for the offline loser (CP applied on next login).
        with COMPETITIVE_LOCK:
            try:
                with open(COMPETITIVE_FORFEITS_PATH, "r", encoding="utf-8") as f:
                    pending = json.load(f)
                if not isinstance(pending, dict):
                    pending = {}
            except (FileNotFoundError, json.JSONDecodeError):
                pending = {}
            entry_id = f"{self.room_id}_{ts}"
            pending[entry_id] = {
                "id": entry_id,
                "loser": loser_name,
                "winner": winner_name,
                "room_id": self.room_id,
                "ts": ts,
                "season_id": get_season_id(ts),
                "processed": False,
            }
            # Keep the file from growing unbounded — drop processed entries older
            # than 30 days.
            cutoff = ts - 30 * 24 * 3600
            for k in list(pending.keys()):
                v = pending.get(k) or {}
                if v.get("processed") and int(v.get("ts", 0)) < cutoff:
                    del pending[k]
            atomic_write_json(COMPETITIVE_FORFEITS_PATH, pending)

    def _save_competitive_game(self, gs: Any, ms: Any, standings: List[Dict[str, Any]]) -> None:
        if self._competitive_saved:
            return
        self._competitive_saved = True
        # Only record games that completed via the end-game trigger.
        if not getattr(ms, "end_game_triggered", False):
            return
        if getattr(gs, "round_count", 0) < 1:
            return
        try:
            seats = self.seats
            # P1 owns seats 0 & 1; P2 owns seats 2 & 3
            p1_name = seats[0].claimed_name or "Player 1" if len(seats) > 0 else "Player 1"
            p2_name = seats[2].claimed_name or "Player 2" if len(seats) > 2 else "Player 2"
            score_map = {s.get("name"): s.get("score", 0) for s in standings}
            p1_scores = [score_map.get(seats[i].claimed_name, 0) for i in (0, 1) if i < len(seats) and seats[i].claimed_name]
            p2_scores = [score_map.get(seats[i].claimed_name, 0) for i in (2, 3) if i < len(seats) and seats[i].claimed_name]
            p1_best   = max(p1_scores) if p1_scores else 0
            p2_best   = max(p2_scores) if p2_scores else 0
            p1_second = min(p1_scores) if len(p1_scores) >= 2 else p1_best
            p2_second = min(p2_scores) if len(p2_scores) >= 2 else p2_best
            # Determine winner with tiebreaker (second-best hand); exact tie = draw
            is_draw = False
            if p1_best > p2_best:
                winner: Optional[str] = p1_name
            elif p2_best > p1_best:
                winner = p2_name
            elif p1_second > p2_second:
                winner = p1_name
            elif p2_second > p1_second:
                winner = p2_name
            else:
                winner = None
                is_draw = True
            strategy = self._detect_strategy(gs)
            turn_count = getattr(gs, "round_count", 0)
            board_snapshot = copy.deepcopy(self.latest_public_state) if isinstance(self.latest_public_state, dict) else {}
            ts = now_unix()
            record = {
                "room_id": self.room_id,
                "recorded_unix": ts,
                "season_id": get_season_id(ts),
                "p1_name": p1_name,
                "p2_name": p2_name,
                "p1_best_score": p1_best,
                "p2_best_score": p2_best,
                "p1_second_score": p1_second,
                "p2_second_score": p2_second,
                "winner": winner,
                "is_draw": is_draw,
                "ranked": False,
                "turn_count": turn_count,
                "strategy": strategy,
                "standings": standings,
                "board_snapshot": board_snapshot,
            }
            game_path = os.path.join(COMPETITIVE_GAMES_DIR, f"game_{self.room_id}_{now_unix()}.json")
            atomic_write_json(game_path, record)
            self._update_competitive_leaderboard(p1_name, p2_name, winner, is_draw)
        except Exception as exc:
            self._record_event(f"Competitive save warning: {exc}")

    def _update_competitive_leaderboard(self, p1_name: str, p2_name: str, winner: Optional[str], is_draw: bool = False) -> None:
        try:
            with COMPETITIVE_LOCK:
                try:
                    with open(COMPETITIVE_LEADERBOARD_PATH, "r", encoding="utf-8") as f:
                        board: Dict[str, Any] = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError):
                    board = {}
                for name in (p1_name, p2_name):
                    if name not in board:
                        board[name] = {"wins": 0, "losses": 0, "draws": 0, "games": 0}
                    board[name]["games"] = board[name].get("games", 0) + 1
                if is_draw:
                    board[p1_name]["draws"] = board[p1_name].get("draws", 0) + 1
                    board[p2_name]["draws"] = board[p2_name].get("draws", 0) + 1
                elif winner:
                    board[winner]["wins"] = board[winner].get("wins", 0) + 1
                    loser = p2_name if winner == p1_name else p1_name
                    board[loser]["losses"] = board[loser].get("losses", 0) + 1
                atomic_write_json(COMPETITIVE_LEADERBOARD_PATH, board)
        except Exception as exc:
            self._record_event(f"Leaderboard update warning: {exc}")

    def _save_game_history(self, gs: Any, ms: Any, standings: List[Dict[str, Any]], human_indices: set) -> None:
        """Save a completed human game to the history directory with full score breakdowns."""
        try:
            rounds_played = getattr(gs, "round_count", 0) or 0
            if rounds_played < 1:
                self._record_event(f"Game history skip: rounds_played={rounds_played} < 1")
                return
            any_board = any(len(getattr(p, "board_oceans", [])) > 0 for p in gs.players)
            if not any_board:
                self._record_event("Game history skip: no player has board cards")
                return
            ended_normally = getattr(ms, "end_game_triggered", False)
            os.makedirs(GAMES_HISTORY_DIR, exist_ok=True)
            player_details = []
            for p in gs.players:
                try:
                    breakdown = fish.full_score_breakdown(gs, p)
                except Exception:
                    breakdown = {}
                board_cards = []
                for ocean_uid in getattr(p, "board_oceans", []):
                    ocean_card = gs.card_db.get(int(ocean_uid)) if gs.card_db else None
                    slots = p.ocean_slots.get(int(ocean_uid)) if hasattr(p, "ocean_slots") else None
                    animals = []
                    if slots is not None:
                        for direction in ("up", "down", "left", "right"):
                            for uid in getattr(slots, direction, []):
                                c = gs.card_db.get(int(uid))
                                if c:
                                    animals.append({"name": c.name, "species": c.species, "uid": int(uid)})
                    board_cards.append({
                        "ocean": ocean_card.name if ocean_card else str(ocean_uid),
                        "animals": animals,
                    })
                strategy = "Unknown"
                type_counts: Dict[str, int] = {}
                total_cards = 0
                for ocean_data in board_cards:
                    for a in ocean_data["animals"]:
                        sp = a.get("species", "")
                        if sp:
                            type_counts[sp] = type_counts.get(sp, 0) + 1
                            total_cards += 1
                if total_cards > 0:
                    dominant = max(type_counts, key=lambda k: type_counts[k])
                    ratio = type_counts[dominant] / total_cards
                    if ratio >= 0.4:
                        strategy = dominant
                    else:
                        strategy = "Mixed"
                score = next((s["score"] for s in standings if s["name"] == p.name), 0)
                player_details.append({
                    "name": p.name,
                    "score": score,
                    "strategy": strategy,
                    "is_human": gs.players.index(p) in human_indices,
                    "board": board_cards,
                    "score_breakdown": breakdown,
                })
            winner_name = standings[0]["name"] if standings else None
            record = {
                "room_id": self.room_id,
                "recorded_unix": now_unix(),
                "mode": ("competitive" if self.competitive else "standard") if ended_normally else "truncated",
                "player_count": len(gs.players),
                "human_count": len(human_indices),
                "winner": winner_name,
                "standings": standings,
                "players": player_details,
            }
            fname = f"game_{self.room_id}_{now_unix()}.json"
            atomic_write_json(os.path.join(GAMES_HISTORY_DIR, fname), record)
            self._record_event(f"Game history saved: {fname} (rounds={rounds_played})")
            # Increment persistent games_played counter for the marketing site stats.
            if ended_normally:
                try:
                    os.makedirs(os.path.dirname(STATS_PATH), exist_ok=True)
                    with STATS_LOCK:
                        try:
                            with open(STATS_PATH, "r", encoding="utf-8") as _sf:
                                _stats = json.load(_sf)
                        except (FileNotFoundError, json.JSONDecodeError):
                            _stats = {"registered_players": 0, "seen_uids": [], "games_played": 0}
                        _stats["games_played"] = int(_stats.get("games_played", 0)) + 1
                        atomic_write_json(STATS_PATH, _stats)
                except Exception as _se:
                    self._record_event(f"Stats games_played update warning: {_se}")
                # Also bump the persisted Firestore counter so the marketing
                # number survives Render redeploys / disk resets and keeps
                # climbing as games finish.
                try:
                    bump_firestore_games_played(1)
                except Exception as _fe:
                    self._record_event(f"Firestore games_played bump warning: {_fe}")
            # Only count truncated games in leaderboard if they went a reasonable distance.
            if ended_normally or rounds_played >= 3:
                self._update_history_leaderboard(player_details, winner_name)
        except Exception as exc:
            import traceback as _tb
            self._record_event(f"Game history save ERROR: {exc} | {_tb.format_exc(limit=5)}")

    def _update_history_leaderboard(self, player_details: List[Dict[str, Any]], winner_name: Optional[str]) -> None:
        try:
            with HISTORY_LOCK:
                try:
                    with open(GAMES_LEADERBOARD_PATH, "r", encoding="utf-8") as f:
                        board: Dict[str, Any] = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError):
                    board = {}
                for p in player_details:
                    name = p["name"]
                    score = int(p.get("score", 0))
                    strategy = p.get("strategy", "Unknown")
                    if name not in board:
                        board[name] = {"games": 0, "wins": 0, "total_score": 0, "best_score": 0, "strategies": {}}
                    entry = board[name]
                    entry["games"] = entry.get("games", 0) + 1
                    entry["total_score"] = entry.get("total_score", 0) + score
                    entry["best_score"] = max(entry.get("best_score", 0), score)
                    if name == winner_name:
                        entry["wins"] = entry.get("wins", 0) + 1
                    strats = entry.setdefault("strategies", {})
                    strats[strategy] = strats.get(strategy, 0) + 1
                atomic_write_json(GAMES_LEADERBOARD_PATH, board)
        except Exception as exc:
            self._record_event(f"History leaderboard update warning: {exc}")

    def _run_game_thread(self, card_db: Dict[int, fish.CardDef]) -> None:
        recorder = RoomLiveRecorder(self)
        gs: Any = None
        ms: Any = None
        standings: List[Dict[str, Any]] = []
        human_indices: set = set()
        human_game = False
        _game_saved = False
        try:
            with BRAIN_LOCK:
                try:
                    brain = fish.load_brain(fish.BRAIN_PATH)
                except Exception as exc:
                    brain = {"weights": fish.default_weights()}
                    self._record_event(f"Brain load warning: {exc}. Continuing with default live weights.")

            # Per-player-count bot: pick the brain trained for THIS table size,
            # seeded from the shared brain on first use. Bots play each count with
            # their own learned weights + strategy values.
            player_count = len(self.seats)
            cbrain = fish.get_count_brain(brain, player_count)
            # Turtle learning gate: live bots refuse to play the turtle until this
            # count's brain has learned it wins games (training unlocks it). Passed
            # into run_match per-player so concurrent games don't clobber it.
            try:
                turtle_gated_flag = not fish.turtle_is_effective(cbrain)
            except Exception:
                turtle_gated_flag = False

            use_history = fish.use_historical_policy_bias()
            ai_weights = dict(fish.default_weights())
            if use_history:
                ai_weights.update(cbrain.get("weights", {}))
            ai_weights = fish.stabilize_weights(ai_weights)

            synergy_map = cbrain.get("synergy", {}) if use_history and isinstance(cbrain.get("synergy"), dict) else {}
            species_map = (
                cbrain.get("species_synergy", {}) if use_history and isinstance(cbrain.get("species_synergy"), dict) else {}
            )
            same_ocean_map = (
                cbrain.get("same_ocean_synergy", {})
                if use_history and isinstance(cbrain.get("same_ocean_synergy"), dict)
                else {}
            )
            strategy_value_map = (
                cbrain.get("strategy_value", {}) if use_history and isinstance(cbrain.get("strategy_value"), dict) else {}
            )
            strategy_count_map = (
                cbrain.get("strategy_count", {}) if use_history and isinstance(cbrain.get("strategy_count"), dict) else {}
            )
            strategy_transition_map = (
                cbrain.get("strategy_transition", {})
                if use_history and isinstance(cbrain.get("strategy_transition"), dict)
                else {}
            )
            strategy_transition_count_map = (
                cbrain.get("strategy_transition_count", {})
                if use_history and isinstance(cbrain.get("strategy_transition_count"), dict)
                else {}
            )

            # Competitive: interleave P1/P2 hands so turns go 0→2→1→3
            # (Player 1, Player 3, Player 2, Player 4)
            if self.competitive and len(self.seats) == 4:
                seat_turn_order = [0, 2, 1, 3]
            elif self.team_mode:
                # Team Mode: randomize the play order and spread teammates as far
                # apart as possible (no fixed seat→team pattern, no adjacency).
                # Reuses the same game_idx↔seat_idx remap plumbing as competitive.
                seat_turn_order = self._team_spread_turn_order()
            else:
                # Casual games: turn order simply follows the seat order — P1
                # (seat 0) first, then P2, P3 … around the table. The seats
                # themselves were randomly assigned to players at launch
                # (_randomize_seat_positions_locked), so who is P1 — and thus who
                # goes first — is already randomized fresh each game; play then
                # proceeds cleanly in player-number order. Tutorials are NOT
                # reseated, so the human stays seat 0 (game_idx 0) and goes first
                # for the guided walkthrough. This branch is DETERMINISTIC (no
                # per-thread shuffle) so a resume after a server restart rebuilds
                # the exact same order and never reshuffles mid-game.
                seat_turn_order = sorted(s.index for s in self.seats)
            self._comp_game_to_seat = {gi: si for gi, si in enumerate(seat_turn_order)}
            self._comp_seat_to_game = {si: gi for gi, si in enumerate(seat_turn_order)}

            player_names: List[str] = []
            policies = []
            ai_difficulties_by_game_idx: List[str] = []
            for game_idx, seat_idx in enumerate(seat_turn_order):
                seat = next(s for s in self.seats if s.index == seat_idx)
                if seat.kind == "human":
                    human_indices.add(game_idx)
                    player_names.append(seat.claimed_name or seat.label)
                    human_policy = self._human_policy(seat.index)
                    policies.append(self._wrap_policy_with_fallback(seat.claimed_name or seat.label, human_policy, seat_index=seat.index))
                    ai_difficulties_by_game_idx.append("")  # placeholder for humans
                else:
                    human_indices.discard(game_idx)
                    player_names.append(seat.claimed_name or seat.label)
                    ai_policy = self._build_ai_policy(
                        seat.index,
                        ai_weights,
                        synergy_map,
                        species_map,
                        same_ocean_map,
                        strategy_value_map,
                        strategy_count_map,
                        strategy_transition_map,
                        strategy_transition_count_map,
                    )
                    policies.append(
                        self._wrap_policy_with_fallback(seat.claimed_name or seat.label, ai_policy, seat_index=seat.index)
                    )
                    ai_difficulties_by_game_idx.append(str(seat.difficulty or "medium").strip().lower())

            human_game = bool(human_indices)

            # Tutorial games (1 human + AI) rig the human's opening hand so the
            # guided walkthrough always has an Ocean + two playable creatures.
            tutorial_human_index: Optional[int] = None
            if getattr(self, "is_tutorial", False) and len(human_indices) == 1:
                tutorial_human_index = next(iter(human_indices))

            gs, ms = fish.run_match(
                card_db=card_db,
                player_names=player_names,
                action_policies=policies,
                seed=self.seed,
                max_turns=260,
                human_index=None,
                human_indices=human_indices,
                verbose=False,
                verbose_state=False,
                online_weights=None,
                online_state=None,
                online_state_path=None,
                live_recorder=recorder,
                ai_difficulties=ai_difficulties_by_game_idx,
                tutorial_human_index=tutorial_human_index,
                tutorial_variant=getattr(self, "tutorial_variant", None),
                turtle_gated=turtle_gated_flag,
            )

            def _safe_score(player: fish.PlayerState) -> int:
                try:
                    return int(fish.final_points(gs, player))
                except Exception:
                    return int(getattr(player, "score", 0))

            standings = [
                {"name": p.name, "score": _safe_score(p)}
                for p in sorted(gs.players, key=lambda x: _safe_score(x), reverse=True)
            ]
            winner_name = standings[0]["name"] if standings else None
            training_record: Dict[str, Any]
            try:
                training_record = self._build_training_record(gs, ms, standings, human_indices)
                self._append_training_record(training_record)
            except Exception as exc:
                training_record = {"valuable": False}
                self._record_event(f"Training record warning: {exc}")

            if human_game:
                self._save_game_history(gs, ms, standings, human_indices)

            if human_game and not self.competitive:
                # Learn from real human gameplay in every live game.
                # For mixed human+AI tables, avoid full-match updates that may amplify AI-only patterns.
                valuable = bool(training_record.get("valuable"))
                human_names = {
                    gs.players[i].name
                    for i in human_indices
                    if 0 <= i < len(gs.players)
                }
                winner_is_human = bool(winner_name and winner_name in human_names)
                human_only_game = len(human_indices) == len(gs.players)
                demo_boost = 2.4 if valuable else 1.2
                if winner_is_human:
                    demo_boost += 0.25
                if human_only_game:
                    demo_boost += 0.2
                # ── Quality gate: only learn from GOOD 4-/5-player games ───
                # Scores aren't comparable across player counts, so we only
                # train on the balanced 4P/5P format with a real developed
                # board (top score >= 100) that ended naturally. Learning there
                # applies to every player count (the AI brain is global).
                _top_score = int(standings[0].get("score", 0)) if standings else 0
                _pcount = len(gs.players)
                _ended_naturally = bool(getattr(ms, "end_game_triggered", False))
                _game_good_to_learn = (
                    _pcount in (4, 5) and _ended_naturally and _top_score >= 100
                )
                if not _game_good_to_learn:
                    self._record_event(
                        f"AI learning skipped — only 4P/5P games with top>=100 train "
                        f"the AI (players={_pcount}, top={_top_score}, "
                        f"ended_naturally={_ended_naturally})."
                    )
                elif _game_good_to_learn:
                    try:
                        with BRAIN_LOCK:
                            brain2 = fish.load_brain(fish.BRAIN_PATH)
                            # Learn into THIS table size's per-count brain.
                            cbrain2 = fish.get_count_brain(brain2, len(gs.players))
                            # Full synergy/weight update for human-only games.
                            # Real human play is the highest-signal data we have,
                            # so weight its synergy learning ~10× over self-play
                            # (the demo_boost nudges it a bit higher for valuable
                            # / human-won games). The quality gate inside
                            # update_brain_from_match discards marginal games.
                            if human_only_game:
                                fish.update_brain_from_match(
                                    gs, cbrain2, human_weight=max(10.0, demo_boost * 4.0)
                                )
                            # Move-sequence learning runs for every human game.
                            fish.update_strategy_memory_from_match(gs, cbrain2, boost=demo_boost)
                            # Per-archetype win-rate stats (used to bias future archetype selection).
                            finals = [fish.final_points(gs, p) for p in gs.players]
                            fish.update_archetype_stats(cbrain2, gs.players, finals)
                            fish.append_game_memory(brain2, gs, finals)
                            # Human-board reinforcement — highest-signal learning pass.
                            fish.reinforce_human_demo_from_board(gs, human_indices, cbrain2, boost=demo_boost)
                            fish.save_brain(brain2, fish.BRAIN_PATH)
                        learn_mode = "full_match+human_demo" if human_only_game else "human_demo_only"
                        self._record_event(
                            f"AI learned from {len(human_indices)} real human player(s) "
                            f"(mode={learn_mode}, boost={demo_boost:.2f}, strategy_memory=yes, archetype_stats=yes)."
                        )
                    except Exception as exc:
                        self._record_event(f"Human-learning warning: {exc}")

            if self.competitive:
                self._save_competitive_game(gs, ms, standings)

            _game_saved = True
            with self.cond:
                # If a forfeit already decided the result (a player left), keep it —
                # the match loop may have wound down to completion afterward, but the
                # forfeit winner/loser is authoritative. Don't overwrite it with a
                # score-based tally from the fallback-played-out turns.
                if self._forfeit_result is None:
                    self.phase = "ended"
                    self.ended_unix = now_unix()
                    self.status_note = "Game ended."
                    self.final_scores = standings
                    self.winner = winner_name
                self.active_action_seat = None
                self.legal_actions_by_seat.clear()
                self._bump_locked(force_persist=True)

        except Exception as exc:
            trace = traceback.format_exc(limit=20)
            # If run_match completed (gs/ms exist) but post-processing threw, still try to save.
            if not _game_saved and gs is not None and ms is not None:
                try:
                    if not standings and hasattr(gs, "players"):
                        standings = [
                            {"name": p.name, "score": int(getattr(p, "score", 0))}
                            for p in sorted(gs.players, key=lambda x: int(getattr(x, "score", 0)), reverse=True)
                        ]
                    try:
                        tr = self._build_training_record(gs, ms, standings, human_indices)
                        self._append_training_record(tr)
                    except Exception:
                        pass
                    if human_game:
                        self._save_game_history(gs, ms, standings, human_indices)
                    if self.competitive:
                        self._save_competitive_game(gs, ms, standings)
                except Exception as save_exc:
                    self._record_event(f"Emergency save warning: {save_exc}")
            with self.cond:
                # A forfeit result is authoritative — never downgrade an
                # already-decided forfeit win to an error state.
                if self._forfeit_result is None:
                    self.phase = "error"
                    self.error_message = f"{exc}"
                    self.status_note = "Game error."
                    self.log_events.append(trace)
                self.active_action_seat = None
                self.legal_actions_by_seat.clear()
                self._bump_locked(force_persist=True)

    def _viewer_payload_locked(self, seat: Optional[Seat]) -> Dict[str, Any]:
        if seat is None:
            return {
                "seat_index": None,
                "game_index": None,
                "seat_kind": None,
                "display_name": None,
                "is_host": False,
                "can_act": False,
            }
        # The 'players' array the client renders uses seat_index as p.index
        # (set in _record_snapshot). So the client must use seat_index to find
        # "me" in the players list — game_index equals seat_index here.
        return {
            "seat_index": seat.index,
            "game_index": seat.index,
            "seat_kind": seat.kind,
            "display_name": seat.claimed_name or seat.label,
            "is_host": bool(seat.is_host),
            "can_act": bool(self.phase == "running" and self.active_action_seat == seat.index),
        }

    def state_view(
        self,
        seat_token: Optional[str],
        host_header: str,
        proto_hint: str = "",
        host_token: str = "",
    ) -> Dict[str, Any]:
        with self.cond:
            # Lazily expire any rejoin reservations whose window has passed.
            self._expire_left_seats_locked()
            # Team Mode: drop swap offers that timed out or became invalid so the
            # target's popup clears itself on the next poll.
            if self.team_mode and self._prune_swap_requests_locked():
                self._bump_locked()
            viewer_seat = self._seat_from_token_locked(seat_token)
            if viewer_seat is None and host_token:
                if secrets.compare_digest(self.host_control_token, host_token):
                    viewer_seat = self.host_seat()
            # Record this seat's last activity so the competitive forfeit check
            # can tell when a player's client has stopped polling (left/closed).
            if viewer_seat is not None:
                viewer_seat.last_seen = time.time()
            # A player polling with their seat token has returned — clear the
            # rejoin reservation so the seat counts as active again.
            if viewer_seat is not None and viewer_seat.left_at is not None:
                viewer_seat.left_at = None
                if not any(s.is_host for s in self.seats if s.kind == "human" and s.token is not None and s.left_at is None):
                    viewer_seat.is_host = True
                self.status_note = f"{viewer_seat.claimed_name or viewer_seat.label} rejoined."
                self._bump_locked()
            # Competitive: end the match as a forfeit if a player left and never
            # came back within the window. Runs AFTER the viewer's own rejoin is
            # cleared above, so a returning player is never forfeited by mistake.
            self._check_competitive_forfeit_locked()
            # viewer_index is the SEAT index (from the seat token).
            # The players array uses seat_idx as p["index"] (see _record_snapshot),
            # so viewer_index is used both to find "me" in the players list AND
            # to look up private_hands (also keyed by seat_idx).
            viewer_index = viewer_seat.index if viewer_seat is not None else None

            state_obj = copy.deepcopy(self.latest_public_state) if isinstance(self.latest_public_state, dict) else None
            if isinstance(state_obj, dict):
                players_list = state_obj.get("players")
                if not isinstance(players_list, list):
                    players_list = []
                    state_obj["players"] = players_list
                # Attach each player's per-seat avatar so every client renders
                # the correct, separate icon for each player (and picks up
                # mid-game avatar changes immediately, no nickname lookup).
                _seat_by_index = {s.index: s for s in self.seats}
                for p in players_list:
                    if not isinstance(p, dict):
                        continue
                    # p["index"] is ALREADY the seat index (set in
                    # _record_snapshot as seat_idx). Do NOT run it through
                    # _comp_game_to_seat again — that double-applies the
                    # turn-order remap and, whenever the casual order is
                    # shuffled (i.e. every game now), hands each player the
                    # WRONG seat's avatar/background. Look the seat up directly.
                    seat_idx = p.get("index")
                    seat_for_p = _seat_by_index.get(seat_idx)
                    if seat_for_p is not None and seat_for_p.avatar:
                        p["avatar"] = seat_for_p.avatar
                    if seat_for_p is not None and seat_for_p.background:
                        p["background"] = seat_for_p.background
                if viewer_index is not None:
                    for p in players_list:
                        if not isinstance(p, dict):
                            continue
                        # p["index"] is the SEAT index (set in _record_snapshot as
                        # seat_idx). viewer_index is also a seat index (from seat token).
                        # private_hands is keyed by seat_idx. All three match.
                        if p.get("index") == viewer_index:
                            p["hand"] = copy.deepcopy(
                                self.latest_private_hands.get(viewer_index, [])
                            )
                        else:
                            p["hand"] = []
                else:
                    for p in players_list:
                        if isinstance(p, dict):
                            p["hand"] = []

            human_filled, human_total = self._human_seat_counts_locked()

            legal_payload: Optional[Dict[str, Any]] = None
            if viewer_index is not None:
                legal_payload = copy.deepcopy(self.legal_actions_by_seat.get(viewer_index))

            payload = {
                "ok": True,
                "version": self.state_version,
                "room": {
                    "room_id": self.room_id,
                    "phase": self.phase,
                    "created_unix": self.created_unix,
                    "started_unix": self.started_unix,
                    "ended_unix": self.ended_unix,
                    "total_players": self.total_players,
                    "human_players": self.human_players,
                    "ai_players": self.ai_players,
                    "competitive": bool(self.competitive),
                    "quick_play": bool(self.quick_play),
                    "team_mode": bool(self.team_mode),
                    "team_count": int(self.team_count),
                    "swap_requests": [dict(r) for r in self.swap_requests],
                    "human_seats_filled": human_filled,
                    "human_seats_total": human_total,
                    "share_url": self.room_link(host_header, proto_hint),
                    "visibility": str(self.visibility),
                    "has_password": self.password_hash is not None,
                    "allow_spectators": bool(self.allow_spectators),
                },
                "spectators": self.spectator_list(),
                "status_note": self.status_note,
                "error": self.error_message,
                "ai_speed": str(self.ai_speed or "normal"),
                "public_links": load_public_links(),
                "seats": self.seat_snapshot_locked(),
                "viewer": self._viewer_payload_locked(viewer_seat),
                "can_start": bool(
                    self.phase == "lobby"
                    and human_total > 0
                    and human_filled >= human_total
                    and self._team_start_ok_locked()
                ),
                "state": state_obj,
                "legal_actions": legal_payload,
                "active_action_seat": self.active_action_seat,
                "afk_challenge": (
                    {
                        "seat": self.afk_challenge_seat,
                        "remaining": max(0.0, round(float(self.afk_challenge_deadline) - time.time(), 2)),
                    }
                    if self.afk_challenge_seat is not None and self.afk_challenge_deadline is not None
                    else None
                ),
                "log_tail": self.log_events[-120:],
                "turn_summaries": self.turn_summaries[-80:],
                "final_scores": self.final_scores,
                "winner": self.winner,
                "chat_messages": self.chat_messages[-80:],
                "recovery": {
                    "active": bool(self.recovery_active),
                    "cursor": int(self.recovery_cursor),
                    "target_count": int(self.recovery_target_count),
                    "error": self.recovery_error,
                },
                "undo": {
                    "eligible_seat": self.undo_eligible_seat,
                    "valid": bool(self.undo_valid),
                    # True once an undo has been requested but not yet replayed — the
                    # client hides the button so it can't be double-clicked while the
                    # engine (often an AI mid-turn) is still picking the request up.
                    "requested": bool(self.undo_requested),
                },
                # Set only when the match ended because a player left (competitive
                # forfeit). The remaining player's client uses winner/loser here to
                # record the result instead of the (incomplete) score tally.
                "forfeit": self._forfeit_result,
                # Post-game "Play Again" ready-up state. ready_count humans have
                # readied; total = active humans + bots (bots ready implicitly, so
                # the game starts the moment ready_count reaches the human count).
                # left_names lists real players who left while the end screen was up.
                "play_again": {
                    "ready_seats": [s.index for s in self._active_human_seats_locked() if s.play_again_ready],
                    "ready_count": self._play_again_counts_locked()[0],
                    "total": (self._play_again_counts_locked()[1] + self._play_again_counts_locked()[2]),
                    "left_names": list(self.post_game_left),
                },
            }
            return payload

    def submit_chat(self, body: Dict[str, Any]) -> Dict[str, Any]:
        seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
        message = str(body.get("message", "")).strip()[:500]
        target = str(body.get("target", "Everyone")).strip()[:64]
        if not message:
            return {"ok": False, "error": "empty message"}
        message = _censor_profanity(message)  # mask swears before storing/broadcast
        with self.cond:
            seat = self._seat_from_token_locked(seat_token)
            if seat is None or seat.kind != "human":
                return {"ok": False, "error": "valid player seat required"}
            sender = seat.claimed_name if seat.claimed_name else seat.label
            entry: Dict[str, Any] = {
                "sender": sender,
                "target": target,
                "message": message,
                "ts": time.time(),
                "avatar": (seat.avatar if seat and seat.avatar else ""),
                "background": (seat.background if seat and seat.background else ""),
            }
            self.chat_messages.append(entry)
            if len(self.chat_messages) > 200:
                self.chat_messages = self.chat_messages[-200:]
            # Recognize "<P3 / username> is afk" votes against the active player.
            try:
                if seat is not None:
                    self._afk_handle_chat_locked(seat, message)
            except Exception as exc:
                self._record_event(f"AFK vote parse error: {exc}")
            self._bump_locked()  # increment version so SSE pushes chat instantly
        return {"ok": True}

    # ── Chat-based AFK voting ────────────────────────────────────────────
    def _afk_label_for_seat(self, seat: "Seat") -> str:
        """Short P-label (P1, P2, …) for a seat, based on its index."""
        try:
            return f"P{int(seat.index) + 1}"
        except Exception:
            return str(getattr(seat, "label", "?"))

    def _afk_reset_turn_locked(self) -> None:
        """Clear all per-turn AFK state and cancel any live challenge.
        Called on every turn boundary. Immunity (time-based) is NOT cleared."""
        self.afk_votes = {}
        self.afk_nominated_this_turn = set()
        if self.afk_challenge_seat is not None:
            self.afk_challenge_seat = None
            self.afk_challenge_deadline = None
            self.afk_challenge_id += 1  # invalidate any pending resolve timer

    def _afk_eligible_voter_indices_locked(self, target_idx: int) -> List[int]:
        """Seat indices of the OTHER active players who count toward the vote:
        seated humans, not the target, not on Surf's Up (Away), still present."""
        out: List[int] = []
        for s in self.seats:
            if s.index == target_idx:
                continue                     # target never counts toward the %
            if s.kind != "human":
                continue                     # bots can't vote
            if not s.claimed_name or s.token is None:
                continue                     # empty / unseated
            if getattr(s, "left_at", None) is not None:
                continue                     # reserved-but-gone seat
            if getattr(s, "is_away", False):
                continue                     # Away players aren't "active"
            out.append(s.index)
        return out

    def _afk_resolve_target_locked(self, raw: str) -> Optional["Seat"]:
        """Resolve a vote target string to a seat. Accepts 'P3'/'p3' (seat
        index N-1) or a (case-insensitive) claimed username, exact-first then
        unique prefix match. Returns None if unresolved/ambiguous."""
        token = (raw or "").strip()
        if not token:
            return None
        m = re.fullmatch(r"[Pp]\s*(\d{1,2})", token)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(self.seats):
                s = self.seats[idx]
                if s.kind == "human":
                    return s
            return None
        low = token.lower()
        humans = [s for s in self.seats if s.kind == "human" and s.claimed_name]
        for s in humans:
            if (s.claimed_name or "").strip().lower() == low:
                return s
        prefixed = [s for s in humans if (s.claimed_name or "").strip().lower().startswith(low)]
        if len(prefixed) == 1:
            return prefixed[0]
        return None

    def _afk_handle_chat_locked(self, voter: "Seat", message: str) -> None:
        """Parse one chat line for an AFK vote and process it (lock held)."""
        if self.phase != "running":
            return
        # Pattern: "<target> [is] afk|away" — tolerant of case and extra spaces.
        # Accepts variations like "P3 is AFK", "P3 afk", "P3 is away", "P3 away".
        m = re.match(r"^\s*(.+?)\s+(?:is\s+)?(?:afk|away)\s*[!.\s]*$", message, re.IGNORECASE)
        if not m:
            return
        target_seat = self._afk_resolve_target_locked(m.group(1))
        if target_seat is None:
            return  # not a recognizable player — treat as ordinary chat
        # Only the CURRENT active player can be reported.
        if self.active_action_seat is None or target_seat.index != self.active_action_seat:
            self._add_system_chat("You can only report the current player as AFK.")
            self.cond.notify_all()
            return
        if voter.kind != "human" or not voter.claimed_name or voter.token is None:
            return
        if voter.index == target_seat.index:
            return  # can't vote yourself
        target_name = target_seat.claimed_name or self._afk_label_for_seat(target_seat)
        # Surf's Up immunity — can't be voted on for 10 minutes after pressing it.
        if time.time() < float(self.afk_immune_until.get(target_seat.index, 0.0)):
            self._add_system_chat(f"{target_name} is protected by Surf's Up and can't be reported right now.")
            self.cond.notify_all()
            return
        # Already challenged this turn — no re-nomination until their next turn.
        if target_seat.index in self.afk_nominated_this_turn:
            return
        voters = self._afk_eligible_voter_indices_locked(target_seat.index)
        if voter.index not in voters:
            return
        denom = len(voters)
        if denom <= 0:
            return
        ballots = self.afk_votes.setdefault(target_seat.index, set())
        if voter.index in ballots:
            return  # one vote per player per target per turn
        ballots.add(voter.index)
        pct = int(round(100.0 * len(ballots) / denom))
        self._add_system_chat(f"{pct}% of players want {target_name} to draw 2 cards.")
        # >=50% of the other active players → start the 10-second challenge.
        needed = -(-denom // 2)  # ceil(denom / 2)
        if len(ballots) >= needed:
            self._afk_start_challenge_locked(target_seat.index)
        self.cond.notify_all()

    def _afk_start_challenge_locked(self, target_idx: int) -> None:
        if self.afk_challenge_seat is not None:
            return  # a challenge is already running
        if target_idx in self.afk_nominated_this_turn:
            return
        self.afk_challenge_id += 1
        cid = self.afk_challenge_id
        self.afk_challenge_seat = target_idx
        self.afk_challenge_deadline = time.time() + self.AFK_CHALLENGE_SECONDS
        self.afk_nominated_this_turn.add(target_idx)
        self._bump_locked()  # wake SSE streamers so the popup shows immediately
        t = threading.Timer(self.AFK_CHALLENGE_SECONDS, self._afk_resolve_challenge, args=[cid, target_idx])
        t.daemon = True
        t.start()

    def _afk_resolve_challenge(self, challenge_id: int, target_idx: int) -> None:
        """Timer callback: if the challenge wasn't cancelled, force the AFK
        player to draw 2 and end their turn (reuses the inactive-rescue path)."""
        with self.cond:
            if self.phase != "running":
                return
            if self.afk_challenge_id != challenge_id or self.afk_challenge_seat != target_idx:
                return  # cancelled, superseded, or turn moved on
            if self.active_action_seat != target_idx:
                self.afk_challenge_seat = None
                self.afk_challenge_deadline = None
                return
            if 0 <= target_idx < len(self.seats):
                seat = self.seats[target_idx]
                # Last-moment Surf's Up wins — never auto-draw an Away player.
                if getattr(seat, "is_away", False):
                    self.afk_challenge_seat = None
                    self.afk_challenge_deadline = None
                    return
                name = seat.claimed_name or self._afk_label_for_seat(seat)
            else:
                name = f"Player {target_idx + 1}"
            queue = self.pending_actions.setdefault(target_idx, [])
            already = any(isinstance(q, dict) and q.get("kind") == "draw_for_inactive" for q in queue)
            if not already:
                queue.insert(0, {
                    "kind": "draw_for_inactive",
                    "by_seat": -1,
                    "by_name": "AFK vote",
                    "submitted_unix": now_unix(),
                })
            self.afk_challenge_seat = None
            self.afk_challenge_deadline = None
            self._add_system_chat(f"{name} was AFK — drawing 2 cards and passing the turn.")
            self._bump_locked()

    def afk_cancel(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """The challenged player clicked / moved inside the game — cancel the
        AFK check. They can't be re-nominated until their next turn."""
        seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
        with self.cond:
            seat = self._seat_from_token_locked(seat_token)
            if seat is None:
                return {"ok": False, "error": "invalid seat token"}
            if self.afk_challenge_seat is None or self.afk_challenge_seat != seat.index:
                return {"ok": True, "no_challenge": True}
            self.afk_challenge_seat = None
            self.afk_challenge_deadline = None
            self.afk_challenge_id += 1  # invalidate the pending resolve timer
            # afk_nominated_this_turn keeps the seat → no re-nomination this turn.
            self.afk_votes.pop(seat.index, None)
            self._bump_locked()
            return {"ok": True, "cancelled": True}

    def set_ai_speed(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Host-only: set how long AI bots pause to 'think' before playing.
        speed must be 'slow', 'normal', or 'fast'.
        """
        host_token  = body.get("host_token")  if isinstance(body.get("host_token"),  str) else ""
        seat_token  = body.get("seat_token")   if isinstance(body.get("seat_token"),  str) else None
        speed_raw   = str(body.get("speed") or "normal").strip().lower()
        if speed_raw not in {"slow", "normal", "fast"}:
            return {"ok": False, "error": "speed must be 'slow', 'normal', or 'fast'"}
        with self.cond:
            if not self._is_host_authorized_locked(host_token, seat_token):
                return {"ok": False, "error": "host authorization required"}
            self.ai_speed = speed_raw
            self._persist_dirty = True
            self._bump_locked()
        return {"ok": True, "ai_speed": self.ai_speed}

    def set_avatar(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Set the calling seat's avatar image, so every client renders this
        player's own icon (and sees mid-game changes immediately)."""
        seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
        raw = body.get("avatar")
        avatar = str(raw).strip()[:128] if isinstance(raw, str) else ""
        # Only accept our own avatar image paths; ignore anything else.
        if avatar and not re.match(r"^/avatars/[A-Za-z0-9_\-]+\.png$", avatar):
            avatar = ""
        with self.cond:
            seat = self._seat_from_token_locked(seat_token)
            if seat is None:
                return {"ok": False, "error": "invalid seat token"}
            if seat.avatar == (avatar or None):
                return {"ok": True, "avatar": seat.avatar or ""}
            seat.avatar = avatar or None
            self._persist_dirty = True
            self._bump_locked()
        return {"ok": True, "avatar": seat.avatar or ""}

    def set_background(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Set the calling seat's equipped background image, so every client
        renders it behind this player's avatar. Empty string clears it."""
        seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
        raw = body.get("background")
        background = str(raw).strip()[:128] if isinstance(raw, str) else ""
        # Only accept our own background image paths; ignore anything else.
        if background and not re.match(r"^/backgrounds/[A-Za-z0-9_\-]+\.png$", background):
            background = ""
        with self.cond:
            seat = self._seat_from_token_locked(seat_token)
            if seat is None:
                return {"ok": False, "error": "invalid seat token"}
            if seat.background == (background or None):
                return {"ok": True, "background": seat.background or ""}
            seat.background = background or None
            self._persist_dirty = True
            self._bump_locked()
        return {"ok": True, "background": seat.background or ""}

    def set_away(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Toggle the calling seat's Surf's Up!! Away flag.

        While Away the turn loop will not auto-draw or end the player's turn,
        and other seats cannot use the Draw-2-Cards affordance. Returns the
        new is_away value.
        """
        seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
        with self.cond:
            seat = self._seat_from_token_locked(seat_token)
            if seat is None:
                return {"ok": False, "error": "invalid seat token"}
            if seat.kind != "human":
                return {"ok": False, "error": "only human seats can use Surf's Up"}
            want = body.get("away")
            new_val = (not seat.is_away) if not isinstance(want, bool) else bool(want)
            seat.is_away = bool(new_val)
            # Pressing Surf's Up grants 10 minutes of AFK-vote immunity — this
            # holds even if they toggle Surf's Up back off before it expires.
            # Also cancel any live AFK challenge currently aimed at this seat.
            self.afk_immune_until[seat.index] = time.time() + self.AFK_SURF_IMMUNE_SECONDS
            if self.afk_challenge_seat == seat.index:
                self.afk_challenge_seat = None
                self.afk_challenge_deadline = None
                self.afk_challenge_id += 1
            self.afk_votes.pop(seat.index, None)
            # Going Away (or coming back) always clears the inactive-eligible flag:
            # Surf's Up overrides the idle/inactive system entirely, so other
            # seats must never see a "Draw 2 for inactive" affordance on an Away
            # player. Also purge any draw-for-inactive command another seat may
            # have queued just before this player went Away.
            if seat.is_away:
                seat.inactive_eligible = False
                queue = self.pending_actions.get(seat.index)
                if queue:
                    self.pending_actions[seat.index] = [
                        q for q in queue
                        if not (isinstance(q, dict) and q.get("kind") == "draw_for_inactive")
                    ]
            else:
                seat.inactive_eligible = False
            display = seat.claimed_name or seat.label
            note = f"{display} is on Surf's Up — Away" if seat.is_away else f"{display} is back."
            self.chat_messages.append({
                "sender": "System",
                "target": "Everyone",
                "message": note,
                "ts": time.time(),
            })
            if len(self.chat_messages) > 200:
                self.chat_messages = self.chat_messages[-200:]
            self.status_note = note
            self._bump_locked()
            return {"ok": True, "is_away": seat.is_away}

    def set_inactive_eligible(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Caller (the active player's own client) reports that the 5-min idle
        + 30-sec warning window expired without activity. Unlocks the
        Draw-2-Cards avatar action for other seats. Calling with eligible=false
        cancels it (player responded).
        """
        seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
        with self.cond:
            seat = self._seat_from_token_locked(seat_token)
            if seat is None:
                return {"ok": False, "error": "invalid seat token"}
            if seat.kind != "human":
                return {"ok": False, "error": "only human seats"}
            want = body.get("eligible")
            new_val = True if not isinstance(want, bool) else bool(want)
            # Never flag eligible while protected Away — Surf's Up always wins.
            if seat.is_away:
                new_val = False
            if seat.inactive_eligible == new_val:
                return {"ok": True, "inactive_eligible": new_val, "unchanged": True}
            seat.inactive_eligible = bool(new_val)
            self._bump_locked()
            return {"ok": True, "inactive_eligible": seat.inactive_eligible}

    def draw_for_inactive(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Any human seat can invoke this once the target seat is flagged
        inactive_eligible AND it is their turn. Injects a special command
        into the target's pending queue; the turn loop draws 2 from the deck
        and ends the turn.
        """
        seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
        try:
            target_idx = int(body.get("target_seat_index"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "target_seat_index must be int"}
        with self.cond:
            caller = self._seat_from_token_locked(seat_token)
            if caller is None:
                return {"ok": False, "error": "invalid seat token"}
            if caller.kind != "human":
                return {"ok": False, "error": "only human seats can act"}
            if self.phase != "running":
                return {"ok": False, "error": "game is not running"}
            if target_idx < 0 or target_idx >= len(self.seats):
                return {"ok": False, "error": "invalid target seat"}
            target = self.seats[target_idx]
            if target.kind != "human":
                return {"ok": False, "error": "target is not a human seat"}
            if target.is_away:
                return {"ok": False, "error": "target is on protected Surf's Up — wait for them to come back"}
            if not target.inactive_eligible:
                return {"ok": False, "error": "target is not flagged inactive"}
            if self.active_action_seat != target.index:
                return {"ok": False, "error": "not target's turn"}
            if caller.index == target.index:
                return {"ok": False, "error": "cannot draw for yourself"}
            queue = self.pending_actions.setdefault(target.index, [])
            # If a draw-for-inactive cmd is already queued, do nothing (idempotent).
            for q in queue:
                if isinstance(q, dict) and q.get("kind") == "draw_for_inactive":
                    return {"ok": True, "duplicate": True}
            queue.insert(0, {
                "kind": "draw_for_inactive",
                "by_seat": caller.index,
                "by_name": caller.claimed_name or caller.label,
                "submitted_unix": now_unix(),
            })
            target.inactive_eligible = False
            note = (
                f"{caller.claimed_name or caller.label} drew 2 cards for "
                f"{target.claimed_name or target.label} because they were inactive."
            )
            self.chat_messages.append({
                "sender": "System",
                "target": "Everyone",
                "message": note,
                "ts": time.time(),
            })
            if len(self.chat_messages) > 200:
                self.chat_messages = self.chat_messages[-200:]
            self.status_note = note
            self._bump_locked()
            return {"ok": True}

    def submit_undo(self, body: Dict[str, Any]) -> Dict[str, Any]:
        seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
        with self.cond:
            if self.phase != "running":
                return {"ok": False, "error": "game not running"}
            seat = self._seat_from_token_locked(seat_token)
            if seat is None:
                return {"ok": False, "error": "invalid seat token"}
            if not self.undo_valid:
                return {"ok": False, "error": "undo not available"}
            if seat.index != self.undo_eligible_seat:
                return {"ok": False, "error": "not your undo to use"}
            if self.undo_snapshot_gs is None:
                # undo_valid should never be True without a snapshot, but never arm
                # an undo we cannot actually restore.
                return {"ok": False, "error": "undo not available — no snapshot"}
            active = self.active_action_seat
            if active is not None and active == seat.index:
                # Mid-turn undo: player is still in their own turn (e.g. drew first card).
                # Inject undo_mid_turn directly into their own pending queue so the
                # engine picks it up on the next policy wait and restores the snapshot.
                self.undo_requested = True
                self.pending_actions.setdefault(seat.index, []).insert(0, {"kind": "undo_mid_turn"})
                self._bump_locked()
                return {"ok": True}
            active_seat_obj = (
                next((s for s in self.seats if s.index == active), None) if active is not None else None
            )
            if active_seat_obj is not None and active_seat_obj.kind == "human":
                # Post-turn undo, next player is a human waiting for input: route the
                # undo through their pending queue so _wait_for_action wakes instantly
                # and replays the previous player's turn.
                self.undo_requested = True
                self.pending_actions.setdefault(active, []).insert(0, {"kind": "undo_confirm"})
                self._bump_locked()
                return {"ok": True}
            # No active HUMAN is waiting — an AI is taking its turn (active_action_seat
            # is None for the whole duration of every bot turn) or the table is between
            # turns. Arm the undo flag; the next policy to run on the engine thread (AI
            # or human) calls _apply_pending_undo_restore, restores the snapshot, and
            # signals the engine to replay the previous human's turn. This is the path
            # that fixes "undo does nothing" in games with bots.
            self.undo_requested = True
            self._bump_locked()
        return {"ok": True}

    def wait_for_update(self, last_version: int, timeout_sec: float) -> bool:
        with self.cond:
            if self.state_version != last_version:
                return True
            self.cond.wait(timeout=timeout_sec)
            return self.state_version != last_version


class RoomLiveRecorder:
    def __init__(self, room: GameRoom) -> None:
        self.room = room

    def event(self, msg: str) -> None:
        try:
            self.room._record_event(msg)
        except Exception:
            return

    def executed_action(
        self,
        gs: fish.GameState,
        ms: fish.MatchState,
        seat_index: int,
        player_name: str,
        action: fish.Action,
        turn_number: int,
    ) -> None:
        self.room.record_executed_action(
            seat_index=int(seat_index),
            player_name=player_name,
            action=action,
            turn_number=int(turn_number),
        )
        try:
            desc = GameRoom._describe_action(gs, ms, action)
            self.room._append_turn_desc(player_name, desc)
        except Exception:
            pass

    def snapshot(self, gs: fish.GameState, ms: fish.MatchState, turn_number: int, note: str) -> None:
        try:
            self.room._record_snapshot(gs, ms, turn_number=turn_number, note=note)
        except Exception as exc:
            self.event(f"Snapshot warning: {exc}")

    def reset(self, gs: fish.GameState, ms: fish.MatchState) -> None:
        try:
            self.room._reset_tracking(gs, ms)
        except Exception:
            return
        self.event(f"Players: {', '.join(p.name for p in gs.players)}")
        try:
            pool_label = fish.short_entry_list(ms, gs, ms.pool)
        except Exception:
            pool_label = "(unavailable)"
        self.event(f"Opening pool: {pool_label}")
        self.snapshot(gs, ms, turn_number=0, note="game_start")


def _ago(seconds: int) -> str:
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} min ago"
    h = seconds // 3600
    return f"{h} hr ago"


class RoomManager:
    def __init__(self) -> None:
        # Quick Play performs an atomic "find-or-create" while reusing the
        # normal create_room path, so this lock must be safely re-entrant.
        self.lock = threading.RLock()
        self.rooms: Dict[str, GameRoom] = {}

    def _active_room_locked(self) -> Optional[GameRoom]:
        for room in self.rooms.values():
            if room.phase in {"lobby", "running"}:
                return room
        return None

    def create_room(
        self,
        host_name: str,
        total_players: int,
        human_players: int,
        ai_players: int,
        requested_room_id: Optional[str] = None,
        replace_active: bool = False,
        competitive: bool = False,
        visibility: str = "public",
        password_hash: Optional[str] = None,
        tutorial: bool = False,
        tutorial_variant: Optional[str] = None,
        quick_play: bool = False,
        team_mode: bool = False,
        team_count: int = 2,
    ) -> GameRoom:
        with self.lock:
            if replace_active:
                # Legacy: replace the single active lobby room if requested explicitly.
                active = self._active_room_locked()
                if active is not None and active.phase == "lobby":
                    self.rooms.pop(active.room_id, None)
                    remove_room_state_file(active.room_id)
            if requested_room_id is not None:
                rid = str(requested_room_id).strip().upper()
                if not ROOM_ID_RE.fullmatch(rid):
                    raise ValueError("room_id must be 4–12 uppercase letters/numbers")
                if rid in self.rooms:
                    existing = self.rooms.get(rid)
                    if existing is not None and existing.phase in {"ended", "error"}:
                        self.rooms.pop(rid, None)
                        remove_room_state_file(rid)
                    else:
                        raise RuntimeError(f"room id already exists ({rid})")
                room = GameRoom(
                    rid,
                    host_name,
                    total_players,
                    human_players,
                    ai_players,
                    competitive=competitive,
                    visibility=visibility,
                    password_hash=password_hash,
                    tutorial=tutorial,
                    tutorial_variant=tutorial_variant,
                    quick_play=quick_play,
                    team_mode=team_mode,
                    team_count=team_count,
                )
                self.rooms[rid] = room
                room.persist_now()
                return room
            for _ in range(100):
                rid = room_code(ROOM_ID_LENGTH)
                if rid not in self.rooms:
                    room = GameRoom(
                        rid,
                        host_name,
                        total_players,
                        human_players,
                        ai_players,
                        competitive=competitive,
                        visibility=visibility,
                        password_hash=password_hash,
                        tutorial=tutorial,
                        tutorial_variant=tutorial_variant,
                        quick_play=quick_play,
                        team_mode=team_mode,
                        team_count=team_count,
                    )
                    self.rooms[rid] = room
                    room.persist_now()
                    return room
        raise RuntimeError("unable to allocate room code")

    def quick_play_join(self, player_name: str, ticket: str) -> Dict[str, Any]:
        """Atomically rejoin, join, or create a dedicated four-seat queue."""
        clean_ticket = str(ticket or "").strip()[:96]
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,96}", clean_ticket):
            return {"ok": False, "error": "invalid Quick Play ticket"}
        clean_name = safe_name(player_name, "Player")

        def response_for(room: GameRoom, seat: Seat) -> Dict[str, Any]:
            with room.cond:
                filled, total = room._human_seat_counts_locked()
                out: Dict[str, Any] = {
                    "ok": True,
                    "room_id": room.room_id,
                    "seat_index": seat.index,
                    "seat_token": seat.token,
                    "is_host": bool(seat.is_host),
                    "matched": filled >= 2,
                    "human_seats_filled": filled,
                    "human_seats_total": total,
                    "total_players": room.total_players,
                }
                if seat.is_host:
                    out["host_token"] = room.host_control_token
                return out

        with self.lock:
            # Network retries with the same ticket must return the original
            # seat instead of consuming another spot.
            for room in self.rooms.values():
                if not room.quick_play or room.phase != "lobby":
                    continue
                with room.cond:
                    for seat in room.seats:
                        if (
                            seat.kind == "human"
                            and seat.token
                            and seat.quick_play_ticket
                            and secrets.compare_digest(seat.quick_play_ticket, clean_ticket)
                        ):
                            seat.last_seen = time.time()
                            return response_for(room, seat)

            stale_room_ids: List[str] = []
            candidates = sorted(
                (
                    room for room in self.rooms.values()
                    if room.quick_play and room.phase == "lobby"
                ),
                key=lambda room: room.created_unix,
            )
            now = time.time()
            for room in candidates:
                with room.cond:
                    claimed = [
                        seat for seat in room.seats
                        if seat.kind == "human" and seat.token is not None
                    ]
                    latest_seen = max(
                        [float(seat.last_seen or room.created_unix) for seat in claimed],
                        default=float(room.created_unix),
                    )
                    if not claimed or now - latest_seen > QUICK_PLAY_STALE_SECONDS:
                        stale_room_ids.append(room.room_id)
                        continue
                    open_seat = next(
                        (
                            seat for seat in room.seats
                            if seat.kind == "human" and seat.token is None
                        ),
                        None,
                    )
                    open_index = open_seat.index if open_seat is not None else None
                if open_index is None:
                    continue
                joined = room.claim_seat(clean_name, open_index, None)
                if not joined.get("ok"):
                    continue
                with room.cond:
                    seat = room.seats[int(joined["seat_index"])]
                    seat.quick_play_ticket = clean_ticket
                    seat.last_seen = now
                    room._add_system_chat(f"{seat.claimed_name or seat.label} joined the Quick Play lobby.")
                    room._bump_locked(force_persist=True)
                    return response_for(room, seat)

            for room_id in stale_room_ids:
                stale = self.rooms.pop(room_id, None)
                if stale is not None:
                    with stale.cond:
                        stale.phase = "ended"
                        stale.status_note = "Quick Play search expired."
                        stale._bump_locked(force_persist=True)
                    remove_room_state_file(room_id)

            room = self.create_room(
                clean_name,
                total_players=4,
                human_players=4,
                ai_players=0,
                competitive=False,
                visibility="public",
                quick_play=True,
            )
            host_seat = room.host_seat()
            if host_seat is None:
                return {"ok": False, "error": "failed to create Quick Play host seat"}
            with room.cond:
                host_seat.quick_play_ticket = clean_ticket
                host_seat.last_seen = now
                room.status_note = "Quick Play is searching for another player."
                room._add_system_chat(f"{host_seat.claimed_name or host_seat.label} opened the Quick Play lobby.")
                room._bump_locked(force_persist=True)
                return response_for(room, host_seat)

    def list_open_rooms(self) -> List[Dict[str, Any]]:
        """Return metadata for all lobby-phase rooms that are not full."""
        with self.lock:
            result = []
            now = now_unix()
            for room in self.rooms.values():
                if room.phase != "lobby" or room.quick_play:
                    continue
                with room.cond:
                    filled, total = room._human_seat_counts_locked()
                if filled >= total:
                    continue  # full
                host_seat = room.host_seat()
                host_name = host_seat.claimed_name if host_seat else "Unknown"
                result.append({
                    "room_id": room.room_id,
                    "host_name": host_name,
                    "mode": "competitive" if room.competitive else "normal",
                    "total_players": room.total_players,
                    "human_players": room.human_players,
                    "filled": filled,
                    "visibility": room.visibility,
                    "has_password": room.password_hash is not None,
                    "created_unix": room.created_unix,
                    "created_ago": _ago(now - room.created_unix),
                })
            result.sort(key=lambda r: r["created_unix"], reverse=True)
            return result

    def get(self, room_id: str) -> Optional[GameRoom]:
        with self.lock:
            return self.rooms.get(room_id)

    def remove(self, room_id: str) -> Optional[GameRoom]:
        with self.lock:
            room = self.rooms.pop(room_id, None)
        if room is not None:
            remove_room_state_file(room_id)
        return room

    def active_room(self) -> Optional[GameRoom]:
        with self.lock:
            return self._active_room_locked()

    def _active_rank(self, room: GameRoom) -> tuple[int, int]:
        started = int(room.started_unix or 0)
        created = int(room.created_unix or 0)
        return started, created

    def load_persisted_rooms(self, card_db: Dict[int, fish.CardDef]) -> Dict[str, int]:
        loaded = 0
        resumed = 0
        skipped = 0
        failed = 0

        candidates: List[GameRoom] = []
        seen_ids: set[str] = set()
        for path in list_room_state_files():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception:
                failed += 1
                continue
            room = GameRoom.from_checkpoint(payload if isinstance(payload, dict) else {})
            if room is None:
                failed += 1
                continue
            if room.room_id in seen_ids:
                skipped += 1
                continue
            seen_ids.add(room.room_id)
            candidates.append(room)

        keep_active_id: Optional[str] = None
        active_candidates = [room for room in candidates if room.phase in {"lobby", "running"}]
        if active_candidates:
            keep_active_id = max(active_candidates, key=self._active_rank).room_id

        resume_rooms: List[GameRoom] = []
        with self.lock:
            for room in candidates:
                if room.phase in {"lobby", "running"} and keep_active_id and room.room_id != keep_active_id:
                    room.phase = "ended"
                    room.ended_unix = room.ended_unix or now_unix()
                    room.status_note = "Archived room from previous server session."
                if room.room_id in self.rooms:
                    skipped += 1
                    continue
                self.rooms[room.room_id] = room
                loaded += 1
                if room.phase == "running":
                    resume_rooms.append(room)

        for room in resume_rooms:
            out = room.resume_after_restore(card_db)
            if out.get("ok"):
                resumed += 1
            else:
                failed += 1

        for room in candidates:
            try:
                room.persist_now()
            except Exception:
                continue

        return {"loaded": loaded, "resumed": resumed, "skipped": skipped, "failed": failed}


ROOMS = RoomManager()


class StableThreadingHTTPServer(ThreadingHTTPServer):
    # Avoid hanging shutdown on slow clients and allow quick restarts on the same port.
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


class MultiplayerHandler(SimpleHTTPRequestHandler):
    server_version = "FishMultiplayer/1.0"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def _apply_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", CORS_ALLOW_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Cache-Control")

    def _apply_html_security_headers(self) -> None:
        # unsafe-none required so Firebase popup auth (google.com popup) can
        # check window.closed / call window.close without being blocked by COOP.
        # same-origin-allow-popups was causing "COOP would block window.closed"
        # console errors and broken auth popup teardown.
        self.send_header("Cross-Origin-Opener-Policy", "unsafe-none")
        self._apply_cors_headers()

    def _send_client_asset(
        self,
        file_path: str,
        content_type: Optional[str] = None,
        cache_control: str = "public, max-age=300",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        if not os.path.exists(file_path):
            self._send_json({"ok": False, "error": "asset not found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            with open(file_path, "rb") as f:
                raw = f.read()
        except OSError:
            self._send_json({"ok": False, "error": "asset not readable"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        guessed_type, _ = mimetypes.guess_type(file_path)
        ctype = content_type or guessed_type or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Length", str(len(raw)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(str(key), str(value))
        try:
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _build_recovery_export(self) -> Dict[str, Any]:
        """
        Read every game-history JSON file and every competitive game file on disk,
        compute per-player stats, and return a recovery payload that apply_recovery.js
        can use to restore Firestore stats for accounts hit by the loadAndRenderStats bug.
        """
        player_stats: Dict[str, Any] = {}

        def ensure(name: str) -> Dict[str, Any]:
            if name not in player_stats:
                player_stats[name] = {
                    "nickname": name,
                    "completed_games": 0,
                    "normal_wins": 0,
                    "competitive_wins": 0,
                    "total_score": 0,
                    "highest_score": 0,
                    "highest_score_normal": 0,
                    "highest_score_competitive": 0,
                    "normal_games_by_size": {},
                    "comp_games_by_size": {},
                    "recent_games": [],
                    "game_timestamps": [],
                    "comp_cp": 0,
                    "comp_wins": 0,
                    "comp_losses": 0,
                    "comp_draws": 0,
                }
            return player_stats[name]

        # ── 1. Standard game history ──────────────────────────────────
        try:
            for fname in sorted(os.listdir(GAMES_HISTORY_DIR)):
                if not fname.endswith(".json") or fname == "leaderboard.json":
                    continue
                try:
                    with open(os.path.join(GAMES_HISTORY_DIR, fname), "r", encoding="utf-8") as f:
                        rec = json.load(f)
                except Exception:
                    continue
                mode = rec.get("mode", "standard")
                is_comp = mode == "competitive"
                pc = int(rec.get("player_count", 0))
                winner = rec.get("winner")
                ts = int(rec.get("recorded_unix", 0))
                for p in rec.get("players", []):
                    pname = p.get("name", "")
                    if not pname or not p.get("is_human"):
                        continue
                    # Strip " 2" suffix used for second hands in competitive
                    base_name = pname.rstrip(" ").rstrip("2").rstrip() if is_comp and pname.endswith(" 2") else pname
                    ps = ensure(base_name)
                    score = int(p.get("score", 0))
                    ps["completed_games"] += 1
                    ps["total_score"] += score
                    ps["highest_score"] = max(ps["highest_score"], score)
                    if not is_comp:
                        ps["normal_wins"] += (1 if pname == winner else 0)
                        ps["highest_score_normal"] = max(ps["highest_score_normal"], score)
                        key = str(pc)
                        ps["normal_games_by_size"][key] = ps["normal_games_by_size"].get(key, 0) + 1
                    else:
                        ps["competitive_wins"] += (1 if base_name == winner else 0)
                        ps["highest_score_competitive"] = max(ps["highest_score_competitive"], score)
                        key = str(pc)
                        ps["comp_games_by_size"][key] = ps["comp_games_by_size"].get(key, 0) + 1
                    if ts:
                        ps["game_timestamps"].append(ts)
                    # Keep up to 50 most recent game entries for recent_games
                    ps["recent_games"].append({
                        "ts": ts, "mode": mode, "score": score,
                        "won": pname == winner, "players": pc,
                    })
        except Exception:
            pass

        # ── 2. Competitive leaderboard (CP) ───────────────────────────
        try:
            with open(COMPETITIVE_LEADERBOARD_PATH, "r", encoding="utf-8") as f:
                lb = json.load(f)
            for name, entry in lb.items():
                ps = ensure(name)
                ps["comp_cp"]     = int(entry.get("cp", 0))
                ps["comp_wins"]   = int(entry.get("wins", 0))
                ps["comp_losses"] = int(entry.get("losses", 0))
                ps["comp_draws"]  = int(entry.get("draws", 0))
        except Exception:
            pass

        # ── 3. Sort recent_games and trim to 50 ───────────────────────
        for ps in player_stats.values():
            ps["recent_games"] = sorted(ps["recent_games"], key=lambda g: g["ts"], reverse=True)[:50]
            del ps["game_timestamps"]

        return {"ok": True, "players": player_stats, "count": len(player_stats)}

    def _run_learn_from_history(
        self,
        card_db: Dict[int, fish.CardDef],
        priority_nick: str = "",
        min_score: int = 0,
    ) -> Dict[str, Any]:
        """Replay every saved game-history JSON through the AI learning pipeline.

        Reconstructs a synthetic GameState (board layout only) from the saved
        JSON record so update_brain_from_match and reinforce_human_demo_from_board
        can learn card synergies from real human games.

        priority_nick — if set, games containing this player (case-insensitive)
            are boosted 5× over the baseline 2.4× human demo boost.
        min_score — only include games where the winner scored at least this many
            points (use 0 to include everything; set e.g. 80 for leaderboard-quality).
        """
        name_to_uid: Dict[str, int] = {}
        for uid, c in card_db.items():
            key = c.name.strip().lower()
            if key not in name_to_uid:
                name_to_uid[key] = uid

        def _build_synthetic_gs(rec: Dict) -> Optional[tuple]:
            """Return (gs, human_indices) or None if the record is unusable."""
            players_data = rec.get("players", [])
            if not players_data:
                return None
            winner_name = rec.get("winner", "")
            ps_list = []
            human_indices: set = set()
            for i, pd in enumerate(players_data):
                p = fish.PlayerState(pd.get("name", f"Player{i}"))
                # Reconstruct board: add ocean + animals to ocean_slots
                for ocean_rec in pd.get("board", []):
                    ocean_name = str(ocean_rec.get("ocean", "")).strip().lower()
                    ocean_uid = name_to_uid.get(ocean_name)
                    if ocean_uid is None:
                        continue
                    p.board_oceans.append(ocean_uid)
                    slots = fish.OceanSlots()
                    dirs = ["up", "down", "left", "right"]
                    animals = ocean_rec.get("animals", [])
                    for j, a in enumerate(animals):
                        aname = str(a.get("name", "")).strip().lower()
                        auid = name_to_uid.get(aname)
                        if auid is None:
                            continue
                        # Place animals in order across directions
                        d = dirs[j % len(dirs)]
                        getattr(slots, d).append(auid)
                    p.ocean_slots[ocean_uid] = slots
                if pd.get("is_human"):
                    human_indices.add(i)
                ps_list.append(p)
            if not ps_list or not any(len(p.board_oceans) > 0 for p in ps_list):
                return None
            gs = fish.GameState(card_db=card_db, players=ps_list, deck=[])
            # Inject final scores so update_brain_from_match can rank winners/losers
            standings = rec.get("standings", [])
            score_map = {s["name"]: int(s.get("score", 0)) for s in standings}
            for p in gs.players:
                p.score = score_map.get(p.name, 0)
            return gs, human_indices, winner_name

        results = {"processed": 0, "skipped": 0, "boosted": 0, "errors": 0}

        try:
            with BRAIN_LOCK:
                brain = fish.load_brain(fish.BRAIN_PATH)

                files = sorted(f for f in os.listdir(GAMES_HISTORY_DIR)
                               if f.endswith(".json") and f != "leaderboard.json")

                for fname in files:
                    try:
                        with open(os.path.join(GAMES_HISTORY_DIR, fname), "r", encoding="utf-8") as f:
                            rec = json.load(f)
                    except Exception:
                        results["errors"] += 1
                        continue

                    # Quality gate: skip truncated / empty games
                    mode = rec.get("mode", "standard")
                    if mode == "truncated":
                        results["skipped"] += 1
                        continue

                    winner_score = rec.get("standings", [{}])[0].get("score", 0) if rec.get("standings") else 0
                    if int(winner_score) < min_score:
                        results["skipped"] += 1
                        continue

                    built = _build_synthetic_gs(rec)
                    if built is None:
                        results["skipped"] += 1
                        continue
                    gs, human_indices, winner_name = built

                    # Decide boost level: priority player's games get an extra kick.
                    has_priority = priority_nick and any(
                        str(pd.get("name", "")).lower().replace(" 2", "") == priority_nick.lower()
                        for pd in rec.get("players", []) if pd.get("is_human")
                    )
                    boost = 5.0 if has_priority else 2.4
                    if has_priority:
                        results["boosted"] += 1

                    # Learn each replayed game into its own table-size brain.
                    # These are all real human games — weight synergy learning
                    # ~10× (priority players higher). update_brain_from_match's
                    # internal quality gate discards undeveloped / near-tie games.
                    cbrain = fish.get_count_brain(brain, len(gs.players))
                    try:
                        fish.update_brain_from_match(
                            gs, cbrain, human_weight=max(10.0, boost * 2.5)
                        )
                        fish.update_strategy_memory_from_match(gs, cbrain, boost=boost)
                        if human_indices:
                            fish.reinforce_human_demo_from_board(gs, human_indices, cbrain, boost=boost)
                    except Exception:
                        results["errors"] += 1
                        continue

                    results["processed"] += 1

                fish.save_brain(brain, fish.BRAIN_PATH)

        except Exception as exc:
            return {"ok": False, "error": str(exc), **results}

        return {"ok": True, **results}

    def _send_json(self, payload: Dict[str, Any], status: int = HTTPStatus.OK) -> None:
        raw = json_dumps(payload)
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._apply_cors_headers()
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _send_index_html(self) -> None:
        try:
            with open(CLIENT_INDEX_PATH, "rb") as f:
                raw = f.read()
        except OSError:
            self._send_json({"ok": False, "error": "client index missing"}, status=HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._apply_html_security_headers()
        # Keep clients in sync so all players render the same UI version.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        try:
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _send_preview_html(self) -> None:
        path = CLIENT_PREVIEW_PATH if os.path.exists(CLIENT_PREVIEW_PATH) else CLIENT_INDEX_PATH
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            self._send_json({"ok": False, "error": "client page missing"}, status=HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._apply_html_security_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        try:
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _send_website_html(self) -> None:
        try:
            with open(WEBSITE_INDEX_PATH, "rb") as f:
                raw = f.read()
        except OSError:
            self._send_json({"ok": False, "error": "website page missing"}, status=HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._apply_html_security_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        try:
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _send_rules_html(self) -> None:
        try:
            with open(RULES_INDEX_PATH, "rb") as f:
                raw = f.read()
        except OSError:
            self._send_json({"ok": False, "error": "rules page missing"}, status=HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._apply_html_security_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        try:
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _send_about_html(self) -> None:
        try:
            with open(ABOUT_INDEX_PATH, "rb") as f:
                raw = f.read()
        except OSError:
            self._send_json({"ok": False, "error": "about page missing"}, status=HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._apply_html_security_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        try:
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _send_leaderboard_html(self) -> None:
        try:
            with open(LEADERBOARD_HTML_PATH, "rb") as f:
                raw = f.read()
        except OSError:
            self._send_json({"ok": False, "error": "leaderboard page missing"}, status=HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._apply_html_security_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        try:
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _send_html_file(self, path: str, missing_label: str) -> None:
        """Serve a standalone HTML page from disk with the site's HTML headers."""
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            self._send_json({"ok": False, "error": f"{missing_label} page missing"}, status=HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._apply_html_security_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        try:
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            return

    def _read_json_body(self) -> tuple[Dict[str, Any], Optional[str]]:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if size < 0:
            return {}, "invalid Content-Length"
        if size > MAX_JSON_BODY_BYTES:
            try:
                # Drain a small prefix to keep the connection state sane.
                self.rfile.read(min(size, 4096))
            except Exception:
                pass
            return {}, f"request body too large (max {MAX_JSON_BODY_BYTES} bytes)"

        raw = self.rfile.read(size) if size > 0 else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            return {}, "invalid JSON body"
        if not isinstance(body, dict):
            return {}, "JSON body must be an object"
        return body, None

    def _handle_stripe_webhook(self) -> None:
        """POST /api/stripe/webhook — Stripe calls this after a checkout completes.

        Flow: read the raw body → (1) verify the Stripe signature → parse the
        event → if it's checkout.session.completed, fulfil it server-side. We ACK
        with 200 once handled (so Stripe stops retrying); a transient processing
        failure returns 500 so Stripe retries later (fulfilment is idempotent)."""
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if size <= 0 or size > MAX_JSON_BODY_BYTES:
            self._send_json({"received": False, "error": "invalid body"}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            raw = self.rfile.read(size)
        except Exception:
            self._send_json({"received": False, "error": "read failed"}, status=HTTPStatus.BAD_REQUEST)
            return

        # (1) Verify the event really came from Stripe. Without a configured
        # signing secret we refuse everything — fulfilling unverified events
        # would let anyone POST a fake purchase and mint coins.
        sig = self.headers.get("Stripe-Signature", "")
        if not _verify_stripe_signature(raw, sig, STRIPE_WEBHOOK_SECRET):
            print("[stripe] rejected webhook: invalid or missing signature.")
            self._send_json({"received": False, "error": "invalid signature"}, status=HTTPStatus.BAD_REQUEST)
            return

        try:
            event = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json({"received": False, "error": "invalid JSON"}, status=HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(event, dict):
            self._send_json({"received": False, "error": "invalid event"}, status=HTTPStatus.BAD_REQUEST)
            return

        if event.get("type") == "checkout.session.completed":
            try:
                _process_stripe_checkout(event)
            except Exception as exc:  # noqa: BLE001
                # Transient failure (e.g. Firestore hiccup): 500 → Stripe retries
                # later. The fulfilment transaction is all-or-nothing + idempotent,
                # so a retry can't double-credit.
                print(f"[stripe] checkout fulfilment error: {exc}")
                traceback.print_exc()
                self._send_json({"received": False, "error": "processing error"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

        # Acknowledge handled + unhandled event types so Stripe stops retrying.
        self._send_json({"received": True})

    def _path_parts(self, path: str) -> List[str]:
        return [p for p in path.strip("/").split("/") if p]

    def _room_from_parts(self, parts: List[str]) -> Optional[GameRoom]:
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "rooms":
            return None
        return ROOMS.get(parts[2])

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._apply_cors_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        parts = self._path_parts(parsed.path)

        if parsed.path == "/firebase-config.js":
            cfg = {
                "apiKey":            os.environ.get("VITE_FIREBASE_API_KEY", ""),
                "authDomain":        os.environ.get("VITE_FIREBASE_AUTH_DOMAIN", ""),
                "projectId":         os.environ.get("VITE_FIREBASE_PROJECT_ID", ""),
                "storageBucket":     os.environ.get("VITE_FIREBASE_STORAGE_BUCKET", ""),
                "messagingSenderId": os.environ.get("VITE_FIREBASE_MESSAGING_SENDER_ID", ""),
                "appId":             os.environ.get("VITE_FIREBASE_APP_ID", ""),
            }
            js = f"window.__FISH_FIREBASE_CONFIG = {json.dumps(cfg)};\n"
            body = js.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/manifest.webmanifest":
            self._send_client_asset(MANIFEST_PATH, content_type="application/manifest+json; charset=utf-8")
            return

        # Build manifest polled by the client to prompt a refresh when a new
        # deploy is live. Must always be served fresh so the prompt is accurate.
        if parsed.path == "/version.json":
            self._send_client_asset(
                VERSION_JSON_PATH,
                content_type="application/json; charset=utf-8",
                cache_control="no-store",
            )
            return

        if parsed.path == "/sw.js":
            self._send_client_asset(
                SERVICE_WORKER_PATH,
                content_type="application/javascript; charset=utf-8",
                cache_control="no-store",
                extra_headers={"Service-Worker-Allowed": "/"},
            )
            return

        # Split preview client assets.
        # Keep this route narrow so only files inside multiplayer/client/css and
        # multiplayer/client/js are exposed.
        if re.fullmatch(r"/(?:css|js)/[A-Za-z0-9_.-]+\.(?:css|js)", parsed.path):
            subdir, filename = parsed.path.strip("/").split("/", 1)
            asset_path = os.path.join(CLIENT_DIR, subdir, filename)
            content_type = (
                "text/css; charset=utf-8"
                if subdir == "css"
                else "application/javascript; charset=utf-8"
            )
            self._send_client_asset(asset_path, content_type=content_type, cache_control="public, max-age=86400")
            return

        if parsed.path == "/icon.svg":
            self._send_client_asset(ICON_PATH, content_type="image/svg+xml")
            return

        if parsed.path == "/player-home-reference.jpg":
            self._send_client_asset(PLAYER_HOME_REFERENCE_PATH, content_type="image/jpeg")
            return

        if parsed.path == "/player-home-avatar.jpg":
            self._send_client_asset(PLAYER_HOME_AVATAR_PATH, content_type="image/jpeg")
            return

        if parsed.path == "/player-home-friend-twin.jpg":
            self._send_client_asset(PLAYER_HOME_FRIEND_TWIN_PATH, content_type="image/jpeg")
            return

        if parsed.path == "/player-home-friend-mom.jpg":
            self._send_client_asset(PLAYER_HOME_FRIEND_MOM_PATH, content_type="image/jpeg")
            return

        if parsed.path.startswith("/avatars/"):
            avatar_name = os.path.basename(parsed.path)
            # Animal avatar icons: lowercase letters/digits/-/_ + .png. basename()
            # plus this character class prevent any directory traversal.
            if not re.fullmatch(r"[a-z0-9_-]+\.png", avatar_name):
                self._send_json({"ok": False, "error": "invalid avatar path"}, status=HTTPStatus.BAD_REQUEST)
                return
            avatar_path = os.path.join(AVATAR_DIR, avatar_name)
            # Short cache so avatar art swaps propagate quickly (was 1 day).
            self._send_client_asset(avatar_path, cache_control="public, max-age=600")
            return

        # Exclusive backgrounds (cosmetic, rendered behind avatars).
        if parsed.path.startswith("/backgrounds/"):
            bg_name = os.path.basename(parsed.path)
            if not re.fullmatch(r"[a-z0-9_-]+\.png", bg_name):
                self._send_json({"ok": False, "error": "invalid background path"}, status=HTTPStatus.BAD_REQUEST)
                return
            bg_path = os.path.join(CLIENT_DIR, "backgrounds", bg_name)
            self._send_client_asset(bg_path, cache_control="public, max-age=600")
            return

        # Serve general client PNG assets (game bg, button art, action cards, etc.)
        if re.fullmatch(r"/(game-bg|nc-coral|nc-sil|nc-btn-full|hermit-crab|choose-device|fullscreen-splash|critter-coin|moving-background|moving-background-left|moving-background-right|lobby-coral-(?:red|orange|yellow)|action-card-(?:create|join|tutorial|competitive|quickmatch))\.png", parsed.path):
            asset_path = os.path.join(CLIENT_DIR, os.path.basename(parsed.path))
            if os.path.exists(asset_path):
                self._send_client_asset(asset_path, content_type="image/png", cache_control="public, max-age=86400")
            else:
                self._send_json({"ok": False, "error": "asset not found"}, status=HTTPStatus.NOT_FOUND)
            return

        # login-bg.png — short cache so artwork updates land quickly
        if parsed.path == "/login-bg.png":
            asset_path = os.path.join(CLIENT_DIR, "login-bg.png")
            if os.path.exists(asset_path):
                self._send_client_asset(asset_path, content_type="image/png", cache_control="no-cache")
            else:
                self._send_json({"ok": False, "error": "asset not found"}, status=HTTPStatus.NOT_FOUND)
            return

        # Serve background art PNGs (tab backgrounds, game background, etc.)
        if re.fullmatch(r"/ph-bg-[\w-]+\.png", parsed.path):
            bg_name = os.path.basename(parsed.path)
            bg_path = os.path.join(CLIENT_DIR, bg_name)
            if os.path.exists(bg_path):
                self._send_client_asset(bg_path, content_type="image/png", cache_control="public, max-age=86400")
            else:
                self._send_json({"ok": False, "error": "bg asset not found"}, status=HTTPStatus.NOT_FOUND)
            return

        # Serve background art PNGs referenced with full client path (used by preview.html on Vercel)
        if re.fullmatch(r"/multiplayer/client/(ph-bg-[\w-]+|lobby-coral-(?:red|orange|yellow))\.png", parsed.path):
            bg_name = os.path.basename(parsed.path)
            bg_path = os.path.join(CLIENT_DIR, bg_name)
            if os.path.exists(bg_path):
                self._send_client_asset(bg_path, content_type="image/png", cache_control="public, max-age=86400")
            else:
                self._send_json({"ok": False, "error": "bg asset not found"}, status=HTTPStatus.NOT_FOUND)
            return

        # Serve full-page tab background images (store / leaderboard)
        _bg_match = re.fullmatch(r"/multiplayer/client/(store-bg|leaderboard-bg)\.(jpg|png)", parsed.path)
        if _bg_match:
            bg_name = os.path.basename(parsed.path)
            bg_path = os.path.join(CLIENT_DIR, bg_name)
            _ct = "image/png" if parsed.path.endswith(".png") else "image/jpeg"
            if os.path.exists(bg_path):
                self._send_client_asset(bg_path, content_type=_ct, cache_control="public, max-age=86400")
            else:
                self._send_json({"ok": False, "error": "bg asset not found"}, status=HTTPStatus.NOT_FOUND)
            return

        # Theme song audio file
        if re.fullmatch(r"/theme-song\.(m4a|mp3|ogg)", parsed.path):
            audio_path = os.path.join(CLIENT_DIR, os.path.basename(parsed.path))
            if os.path.exists(audio_path):
                ext = parsed.path.rsplit(".", 1)[-1].lower()
                ctype = {"m4a": "audio/mp4", "mp3": "audio/mpeg", "ogg": "audio/ogg"}.get(ext, "audio/mpeg")
                self._send_client_asset(audio_path, content_type=ctype, cache_control="public, max-age=86400")
            else:
                self._send_json({"ok": False, "error": "audio not found"}, status=HTTPStatus.NOT_FOUND)
            return

        # Card-art sprite sheets (horizontal / vertical / oceans page PNGs).
        # Previously these fell through to SimpleHTTPRequestHandler.do_GET(),
        # which sends NO Cache-Control — so the browser (and Cloudflare, which
        # marked them "DYNAMIC") re-fetched every ~300–600 KB page on every card,
        # every game load. That is why cards took forever to appear. The client
        # already cache-busts each URL with ?v=CARD_IMAGE_VERSION, so the file at
        # a given URL is immutable: serve it with a 1-year immutable cache.
        _card_art = re.fullmatch(r"/(horizontal_cards|vertical_cards|oceans_cards)/(page_\d+\.png)", parsed.path)
        if _card_art:
            card_dir, card_file = _card_art.group(1), _card_art.group(2)
            card_path = os.path.join(BASE_DIR, card_dir, card_file)
            if os.path.exists(card_path):
                self._send_client_asset(
                    card_path,
                    content_type="image/png",
                    cache_control="public, max-age=31536000, immutable",
                )
            else:
                self._send_json({"ok": False, "error": "card art not found"}, status=HTTPStatus.NOT_FOUND)
            return

        if parsed.path == "/api/health":
            self._send_json(
                {
                    "ok": True,
                    "time": now_unix(),
                    "create_key_required": False,
                    "server_version": "2",
                    "public_urls": load_public_links(),
                }
            )
            return

        if parsed.path == "/api/stripe/webhook":
            # The webhook itself only accepts POST (Stripe POSTs signed events).
            # A browser visit is a GET, which would otherwise hit the static file
            # handler and show a scary "404 File not found". Answer GET with a
            # clear 200 so visiting the URL confirms the endpoint is live — this
            # is NOT the address bar's job; paste this exact URL into Stripe.
            self._send_json(
                {
                    "ok": True,
                    "endpoint": "/api/stripe/webhook",
                    "method": "POST",
                    "message": "Stripe webhook endpoint is live. It accepts POST "
                               "events from Stripe only (checkout.session.completed). "
                               "Seeing this means the route is deployed correctly.",
                }
            )
            return

        if parsed.path == "/api/public-links":
            self._send_json(
                {
                    "ok": True,
                    "updated_unix": now_unix(),
                    "public_urls": load_public_links(),
                }
            )
            return

        if len(parts) == 2 and parts[0] == "play":
            self._send_preview_html()
            return

        if parsed.path in {"/preview.html", "/multiplayer/client/preview.html"}:
            self._send_preview_html()
            return

        if len(parts) == 1 and parts[0] in {"game", "preview"}:
            self._send_preview_html()
            return

        if len(parts) == 2 and parts[0] in {"preview"}:
            self._send_preview_html()
            return

        # Old simulation layout kept for reference
        if len(parts) == 1 and parts[0] in {"classic", "old"}:
            self._send_index_html()
            return

        if len(parts) == 2 and parts[0] == "classic":
            self._send_index_html()
            return

        if len(parts) == 1 and parts[0] in {"website", "site"}:
            if os.path.exists(WEBSITE_INDEX_PATH):
                self._send_website_html()
                return
            self._send_json({"ok": False, "error": "website page missing"}, status=HTTPStatus.NOT_FOUND)
            return

        if len(parts) == 1 and parts[0] == "rules":
            if os.path.exists(RULES_INDEX_PATH):
                self._send_rules_html()
                return
            self._send_json({"ok": False, "error": "rules page missing"}, status=HTTPStatus.NOT_FOUND)
            return

        if len(parts) == 1 and parts[0] == "about":
            if os.path.exists(ABOUT_INDEX_PATH):
                self._send_about_html()
                return
            self._send_json({"ok": False, "error": "about page missing"}, status=HTTPStatus.NOT_FOUND)
            return

        if len(parts) == 1 and parts[0] == "leaderboard":
            if os.path.exists(LEADERBOARD_HTML_PATH):
                self._send_leaderboard_html()
                return
            self._send_json({"ok": False, "error": "leaderboard page missing"}, status=HTTPStatus.NOT_FOUND)
            return

        # Supporter Reef Wall pages (public wall, guest claim, admin review).
        if len(parts) == 1 and parts[0] in {"supporter-wall", "wall", "reef-wall"}:
            self._send_html_file(SUPPORTER_WALL_HTML_PATH, "supporter wall")
            return
        if len(parts) == 1 and parts[0] in {"claim", "claim-rewards"}:
            self._send_html_file(CLAIM_REWARDS_HTML_PATH, "claim")
            return
        if len(parts) == 1 and parts[0] in {"supporter-admin", "admin-supporters"}:
            self._send_html_file(SUPPORTER_ADMIN_HTML_PATH, "supporter admin")
            return

        if parsed.path == "/":
            self._send_preview_html()
            return

        # Public Supporter Reef Wall data — approved + visible records only,
        # exposing just displayName / wallSize / tier (no emails/ids/history).
        if parsed.path == "/api/supporters/wall":
            self._send_json({"ok": True, "supporters": _supporter_wall_cached()})
            return

        # Admin review list (pending by default; ?filter=all for everything).
        # Protected by ADMIN_RECOVERY_KEY, like the other /api/admin/* endpoints.
        if parsed.path == "/api/admin/supporters":
            qs_a = parse_qs(parsed.query)
            supplied_key = qs_a.get("admin_key", [None])[0] or ""
            env_key = os.environ.get("ADMIN_RECOVERY_KEY", "").strip()
            if not env_key or supplied_key != env_key:
                self._send_json({"ok": False, "error": "unauthorized"}, status=HTTPStatus.FORBIDDEN)
                return
            filter_mode = (qs_a.get("filter", ["pending"])[0] or "pending").strip()
            self._send_json(_admin_list_supporters(filter_mode))
            return

        if parsed.path == "/api/competitive/forfeit_pending":
            # Unprocessed forfeit losses for a player (matched by name). The
            # loser was offline when the forfeit happened, so their client calls
            # this on load and applies the CP penalty, then acks each entry.
            qs = parse_qs(parsed.query)
            who = (qs.get("name", [""])[0] or "").strip()
            if not who:
                self._send_json({"ok": False, "error": "name required"}, status=HTTPStatus.BAD_REQUEST)
                return
            out: List[Dict[str, Any]] = []
            with COMPETITIVE_LOCK:
                try:
                    with open(COMPETITIVE_FORFEITS_PATH, "r", encoding="utf-8") as f:
                        pending = json.load(f)
                    if not isinstance(pending, dict):
                        pending = {}
                except (FileNotFoundError, json.JSONDecodeError):
                    pending = {}
            for entry in pending.values():
                if (isinstance(entry, dict) and not entry.get("processed")
                        and str(entry.get("loser", "")).strip() == who):
                    out.append({
                        "id": entry.get("id"),
                        "winner": entry.get("winner"),
                        "loser": entry.get("loser"),
                        "room_id": entry.get("room_id"),
                        "ts": entry.get("ts"),
                        "season_id": entry.get("season_id"),
                    })
            out.sort(key=lambda e: int(e.get("ts", 0)))
            self._send_json({"ok": True, "pending": out})
            return

        if parsed.path == "/api/competitive/leaderboard":
            qs = parse_qs(parsed.query)
            season_filter = qs.get("season", [None])[0]
            if season_filter:
                # Return season-specific leaderboard
                season_lb_path = os.path.join(COMPETITIVE_GAMES_DIR, f"leaderboard_{season_filter}.json")
                try:
                    with open(season_lb_path, "r", encoding="utf-8") as f:
                        board = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError):
                    board = {}
            else:
                try:
                    with open(COMPETITIVE_LEADERBOARD_PATH, "r", encoding="utf-8") as f:
                        board = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError):
                    board = {}
            rows = sorted(
                [{"name": k, **v} for k, v in board.items()],
                key=lambda x: (-(x.get("cp", 0) or x.get("wins", 0) * 25), x.get("losses", 0), x.get("name", "")),
            )
            self._send_json({"ok": True, "leaderboard": rows, "season_id": season_filter or get_season_id()})
            return

        if parsed.path == "/api/competitive/seasons":
            # Return list of seasons that have game records, plus current season
            seasons: Dict[str, Any] = {}
            current = get_season_id()
            seasons[current] = {"id": current, "game_count": 0, "is_current": True}
            try:
                for fname in os.listdir(COMPETITIVE_GAMES_DIR):
                    if fname.startswith("game_") and fname.endswith(".json"):
                        fpath = os.path.join(COMPETITIVE_GAMES_DIR, fname)
                        try:
                            with open(fpath, "r", encoding="utf-8") as f:
                                rec = json.load(f)
                            sid = rec.get("season_id") or get_season_id(rec.get("recorded_unix"))
                            if sid not in seasons:
                                seasons[sid] = {"id": sid, "game_count": 0, "is_current": sid == current}
                            if rec.get("ranked"):
                                seasons[sid]["game_count"] = seasons[sid].get("game_count", 0) + 1
                        except Exception:
                            pass
            except FileNotFoundError:
                pass
            # Enrich each season with leaderboard king
            for sid, sdata in seasons.items():
                season_lb_path = os.path.join(COMPETITIVE_GAMES_DIR, f"leaderboard_{sid}.json")
                try:
                    with open(season_lb_path, "r", encoding="utf-8") as f:
                        slb = json.load(f)
                    if slb:
                        best = max(slb.items(), key=lambda kv: (kv[1].get("cp", 0), kv[1].get("wins", 0)))
                        sdata["king_name"] = best[0]
                        sdata["king_cp"] = best[1].get("cp", 0)
                        sdata["king_rank"] = best[1].get("rank", "")
                except Exception:
                    pass
            result = sorted(seasons.values(), key=lambda s: s["id"], reverse=True)
            self._send_json({"ok": True, "seasons": result})
            return

        if parsed.path == "/api/competitive/history":
            qs = parse_qs(parsed.query)
            season_filter = qs.get("season", [None])[0]
            games = []
            try:
                for fname in sorted(os.listdir(COMPETITIVE_GAMES_DIR)):
                    if fname.startswith("game_") and fname.endswith(".json"):
                        fpath = os.path.join(COMPETITIVE_GAMES_DIR, fname)
                        try:
                            with open(fpath, "r", encoding="utf-8") as f:
                                g = json.load(f)
                            if season_filter:
                                g_season = g.get("season_id") or get_season_id(g.get("recorded_unix"))
                                if g_season != season_filter:
                                    continue
                            games.append(g)
                        except Exception:
                            pass
            except FileNotFoundError:
                pass
            games.sort(key=lambda g: g.get("recorded_unix", 0), reverse=True)
            self._send_json({"ok": True, "games": games, "season_id": season_filter or get_season_id()})
            return

        if parsed.path == "/api/history/leaderboard":
            try:
                with open(GAMES_LEADERBOARD_PATH, "r", encoding="utf-8") as f:
                    board = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                board = {}
            rows = sorted(
                [{"name": k, **v} for k, v in board.items()],
                key=lambda x: (-(x.get("wins", 0)), -(x.get("best_score", 0)), x.get("name", "")),
            )
            self._send_json({"ok": True, "leaderboard": rows})
            return

        if parsed.path == "/api/history/games":
            games = []
            try:
                for fname in sorted(os.listdir(GAMES_HISTORY_DIR)):
                    if fname.startswith("game_") and fname.endswith(".json"):
                        fpath = os.path.join(GAMES_HISTORY_DIR, fname)
                        try:
                            with open(fpath, "r", encoding="utf-8") as f:
                                games.append(json.load(f))
                        except Exception:
                            pass
            except FileNotFoundError:
                pass
            games.sort(key=lambda g: g.get("recorded_unix", 0), reverse=True)
            self._send_json({"ok": True, "games": games[:100]})
            return

        if parsed.path == "/api/admin/recovery_export":
            # Returns per-player stats rebuilt from every saved game history file
            # and competitive leaderboard. Used by apply_recovery.js to restore
            # Firestore stats for accounts affected by the loadAndRenderStats bug.
            # Protected: requires ?admin_key= matching ADMIN_RECOVERY_KEY env var.
            qs_r = parse_qs(parsed.query)
            supplied_key = qs_r.get("admin_key", [None])[0] or ""
            env_key = os.environ.get("ADMIN_RECOVERY_KEY", "").strip()
            if not env_key or supplied_key != env_key:
                self._send_json({"ok": False, "error": "unauthorized"}, status=HTTPStatus.FORBIDDEN)
                return
            self._send_json(self._build_recovery_export())
            return

        if parsed.path == "/api/admin/learn_from_history":
            # Run the full AI learning pipeline over every saved game-history JSON.
            # Games containing priority_nick are boosted 5× (default: TheFishManTim).
            # Games where the winner scored < min_score are skipped (default: 0 = all).
            # Protected: requires ?admin_key= matching ADMIN_RECOVERY_KEY env var.
            qs_r = parse_qs(parsed.query)
            supplied_key = qs_r.get("admin_key", [None])[0] or ""
            env_key = os.environ.get("ADMIN_RECOVERY_KEY", "").strip()
            if not env_key or supplied_key != env_key:
                self._send_json({"ok": False, "error": "unauthorized"}, status=HTTPStatus.FORBIDDEN)
                return
            priority_nick = (qs_r.get("priority_nick", [None])[0] or "TheFishManTim").strip()
            min_score = 0
            try:
                min_score = int(qs_r.get("min_score", [0])[0])
            except Exception:
                pass
            card_db = CARD_DB
            result = self._run_learn_from_history(card_db, priority_nick=priority_nick, min_score=min_score)
            self._send_json(result)
            return

        if parsed.path == "/api/rooms":
            self._send_json({"ok": True, "rooms": ROOMS.list_open_rooms()})
            return

        if parsed.path == "/api/stats":
            # Both counters live in STATS_PATH on the persistent disk.
            games_played = 0
            registered_players = 0
            try:
                with STATS_LOCK:
                    with open(STATS_PATH, "r", encoding="utf-8") as f:
                        _s = json.load(f)
                        games_played = int(_s.get("games_played", 0))
                        registered_players = int(_s.get("registered_players", 0))
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            # Self-heal games_played from the actual game-history files on disk:
            # every finished game writes one game_*.json record, so the file
            # count is the ground truth. The stored counter can lag if the
            # in-game increment ever missed a game, so report whichever is
            # larger. This keeps the marketing-site number exact and always
            # moving as new games complete.
            try:
                history_games = sum(
                    1 for _fn in os.listdir(GAMES_HISTORY_DIR)
                    if _fn.startswith("game_") and _fn.endswith(".json")
                )
                if history_games > games_played:
                    games_played = history_games
            except OSError:
                pass
            # Exact registered + live online counts straight from Firestore (the
            # real account list), when a service account is configured. Every
            # account has a Firestore user doc, so the live count is complete
            # and authoritative — use it directly. Falls back to the stored
            # seen-uid counter / 0 when Firebase isn't configured. The persisted
            # Firestore games counter is folded in too so the games number keeps
            # climbing even if the Render disk lost its game-history files.
            live_registered, live_online, live_games = get_live_user_counts()
            if isinstance(live_registered, int) and live_registered >= 0:
                registered_players = live_registered
            online_players = live_online if isinstance(live_online, int) and live_online >= 0 else 0
            if isinstance(live_games, int) and live_games > games_played:
                games_played = live_games
            # Never report below the historical baseline.
            if games_played < STATS_SEED_GAMES:
                games_played = STATS_SEED_GAMES
            self._send_json({
                "ok": True,
                "games_played": games_played,
                "registered_players": registered_players,
                "online_players": online_players,
            })
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "state":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            qs = parse_qs(parsed.query)
            seat_token = qs.get("seat_token", [None])[0]
            host_token = qs.get("host_token", [None])[0]
            spectator_token = qs.get("spectator_token", [None])[0]
            host_header = self.headers.get("Host", "127.0.0.1:8777")
            proto_hint = self.headers.get("X-Forwarded-Proto", "")
            if spectator_token:
                with room.cond:
                    is_spec = spectator_token in room.spectators
                if is_spec:
                    self._send_json(room.spectator_state_view(host_header, proto_hint))
                else:
                    self._send_json({"ok": False, "error": "invalid spectator token"}, status=HTTPStatus.FORBIDDEN)
                return
            self._send_json(
                room.state_view(
                    seat_token if isinstance(seat_token, str) else None,
                    host_header,
                    proto_hint,
                    host_token if isinstance(host_token, str) else "",
                )
            )
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "stream":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            qs = parse_qs(parsed.query)
            seat_token = qs.get("seat_token", [None])[0]
            host_token = qs.get("host_token", [None])[0]
            self._serve_sse(
                room,
                seat_token if isinstance(seat_token, str) else None,
                host_token if isinstance(host_token, str) else "",
            )
            return

        super().do_GET()

    def _serve_sse(self, room: GameRoom, seat_token: Optional[str], host_token: str = "") -> None:
        host_header = self.headers.get("Host", "127.0.0.1:8777")
        proto_hint = self.headers.get("X-Forwarded-Proto", "")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self._apply_cors_headers()
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        last_version = -1
        try:
            while True:
                payload = room.state_view(seat_token, host_header, proto_hint, host_token)
                version = int(payload.get("version", 0))
                if version != last_version:
                    data = json.dumps(payload, separators=(",", ":"))
                    chunk = f"event: state\ndata: {data}\n\n".encode("utf-8")
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    last_version = version

                changed = room.wait_for_update(last_version, timeout_sec=5.0)
                if not changed:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            return

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        # Stripe webhook is handled FIRST, before _read_json_body() consumes the
        # stream: signature verification needs the EXACT raw request bytes.
        if parsed.path == "/api/stripe/webhook":
            self._handle_stripe_webhook()
            return

        parts = self._path_parts(parsed.path)
        body, body_error = self._read_json_body()
        if body_error:
            self._send_json({"ok": False, "error": body_error}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/user/register":
            # Called by the game client once per new Google account sign-up.
            # Body: { "uid": "<firebase_uid>" }
            # Idempotent: tracks seen UIDs so re-registrations don't inflate the count.
            uid_val = body.get("uid") if isinstance(body.get("uid"), str) else ""
            if not uid_val:
                self._send_json({"ok": False, "error": "uid required"}, status=HTTPStatus.BAD_REQUEST)
                return
            uid_val = uid_val.strip()[:256]
            try:
                os.makedirs(os.path.dirname(STATS_PATH), exist_ok=True)
                with STATS_LOCK:
                    try:
                        with open(STATS_PATH, "r", encoding="utf-8") as f:
                            stats = json.load(f)
                    except (FileNotFoundError, json.JSONDecodeError):
                        stats = {"registered_players": 0, "seen_uids": []}
                    seen = set(stats.get("seen_uids") or [])
                    if uid_val not in seen:
                        seen.add(uid_val)
                        stats["registered_players"] = int(stats.get("registered_players", 0)) + 1
                        stats["seen_uids"] = list(seen)
                        atomic_write_json(STATS_PATH, stats)
                    self._send_json({"ok": True, "registered_players": stats["registered_players"]})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # Reserve a unique Currents & Critters username. Requires a verified
        # Firebase ID token (never trust a raw uid for an identity write).
        # Body: { "idToken": "<firebase id token>", "username": "<name>" }
        if parsed.path == "/api/username/claim":
            claims = _verify_firebase_id_token(body.get("idToken") if isinstance(body.get("idToken"), str) else "")
            if not claims or not claims.get("uid"):
                self._send_json({"ok": False, "error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                return
            username = body.get("username") if isinstance(body.get("username"), str) else ""
            self._send_json(_claim_username(claims["uid"], username))
            return

        # Claim past guest payments/rewards into the signed-in account. Requires
        # a verified Firebase ID token; only guest records whose CHECKOUT email
        # matches the token's verified email are merged in.
        # Body: { "idToken": "<firebase id token>" }
        if parsed.path == "/api/supporters/claim":
            claims = _verify_firebase_id_token(body.get("idToken") if isinstance(body.get("idToken"), str) else "")
            if not claims or not claims.get("uid"):
                self._send_json({"ok": False, "error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                return
            self._send_json(_claim_guest_rewards(claims["uid"], str(claims.get("email") or "")))
            return

        # Admin: edit a wall record (displayName / status / visible).
        # Body: { "admin_key": "...", "kind": "supporter"|"guest", "id": "...",
        #         "displayName"?, "status"?, "visible"? }
        if parsed.path == "/api/admin/supporters/update":
            admin_key = body.get("admin_key") if isinstance(body.get("admin_key"), str) else ""
            env_key = os.environ.get("ADMIN_RECOVERY_KEY", "").strip()
            if not env_key or admin_key != env_key:
                self._send_json({"ok": False, "error": "unauthorized"}, status=HTTPStatus.FORBIDDEN)
                return
            kind = str(body.get("kind") or "supporter").strip()
            if kind not in ("supporter", "guest"):
                self._send_json({"ok": False, "error": "bad kind"}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(_admin_update_supporter(kind, str(body.get("id") or ""), body))
            return

        if parsed.path == "/api/stats/seed":
            # One-time endpoint to backfill historical counts.
            # Body: { "admin_key": "...", "games_played": N, "registered_players": N }
            # Protected by the same CREATE_KEY used elsewhere.
            admin_key = body.get("admin_key") if isinstance(body.get("admin_key"), str) else ""
            if not admin_key or not secrets.compare_digest(admin_key, CREATE_KEY or ""):
                self._send_json({"ok": False, "error": "unauthorized"}, status=HTTPStatus.FORBIDDEN)
                return
            new_games   = body.get("games_played")
            new_players = body.get("registered_players")
            if not isinstance(new_games, int) or not isinstance(new_players, int):
                self._send_json({"ok": False, "error": "games_played and registered_players (integers) required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                os.makedirs(os.path.dirname(STATS_PATH), exist_ok=True)
                with STATS_LOCK:
                    try:
                        with open(STATS_PATH, "r", encoding="utf-8") as f:
                            stats = json.load(f)
                    except (FileNotFoundError, json.JSONDecodeError):
                        stats = {"registered_players": 0, "seen_uids": [], "games_played": 0}
                    # Only raise existing counts — never lower them.
                    stats["games_played"]        = max(int(stats.get("games_played", 0)),        new_games)
                    stats["registered_players"]  = max(int(stats.get("registered_players", 0)), new_players)
                    atomic_write_json(STATS_PATH, stats)
                self._send_json({"ok": True, "games_played": stats["games_played"], "registered_players": stats["registered_players"]})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if parsed.path == "/api/server/stop":
            room_id = body.get("room_id") if isinstance(body.get("room_id"), str) else ""
            if not room_id:
                self._send_json({"ok": False, "error": "room_id required"}, status=HTTPStatus.BAD_REQUEST)
                return
            room = ROOMS.get(room_id)
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            host_token = body.get("host_token") if isinstance(body.get("host_token"), str) else ""
            seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            if not room.is_host_authorized(host_token, seat_token):
                self._send_json({"ok": False, "error": "host authorization required"}, status=HTTPStatus.FORBIDDEN)
                return

            server = ACTIVE_SERVER
            if server is None:
                self._send_json({"ok": False, "error": "server not active"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            self._send_json({"ok": True, "status": "stopping"})

            def _shutdown_later() -> None:
                time.sleep(0.12)
                try:
                    server.shutdown()
                except Exception:
                    pass

            threading.Thread(target=_shutdown_later, daemon=True).start()
            return

        if parsed.path == "/api/quickplay":
            ticket = body.get("ticket") if isinstance(body.get("ticket"), str) else ""
            player_name = safe_name(body.get("player_name"), "Player")
            out = ROOMS.quick_play_join(player_name, ticket)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if parsed.path == "/api/rooms":
            pass  # key check removed — open room creation

            total_players = clamp_int(body.get("total_players"), 4, 2, 8)
            human_players = clamp_int(body.get("human_players"), 2, 1, 8)
            ai_players = clamp_int(body.get("ai_players"), total_players - human_players, 0, 8)
            if human_players + ai_players != total_players:
                self._send_json(
                    {"ok": False, "error": "human_players + ai_players must equal total_players"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            host_name = safe_name(body.get("host_name"), "Host")
            requested_room_id = body.get("room_id") if isinstance(body.get("room_id"), str) else None
            replace_active = bool(body.get("replace_active")) if isinstance(body.get("replace_active"), bool) else False
            competitive = bool(body.get("competitive")) if isinstance(body.get("competitive"), bool) else False
            team_mode = bool(body.get("team")) if isinstance(body.get("team"), bool) else False
            team_count = clamp_int(body.get("team_count"), 2, 2, 4) if team_mode else 2
            # A team game is always a normal (non-competitive) game.
            if team_mode:
                competitive = False
            tutorial = bool(body.get("tutorial")) if isinstance(body.get("tutorial"), bool) else False
            tutorial_variant = body.get("tutorial_variant") if isinstance(body.get("tutorial_variant"), str) else None
            vis_raw = str(body.get("visibility") or "public").strip().lower()
            visibility = vis_raw if vis_raw in {"public", "private"} else "public"
            password_plain = body.get("password") if isinstance(body.get("password"), str) else None
            if visibility == "private" and password_plain:
                password_hash: Optional[str] = hashlib.sha256(password_plain.strip().encode()).hexdigest()
            else:
                password_hash = None
            try:
                room = ROOMS.create_room(
                    host_name,
                    total_players,
                    human_players,
                    ai_players,
                    requested_room_id=requested_room_id,
                    replace_active=replace_active,
                    competitive=competitive,
                    visibility=visibility,
                    password_hash=password_hash,
                    tutorial=tutorial,
                    tutorial_variant=tutorial_variant,
                    team_mode=team_mode,
                    team_count=team_count,
                )
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            except RuntimeError as exc:
                active = ROOMS.active_room()
                out: Dict[str, Any] = {"ok": False, "error": str(exc)}
                if active is not None:
                    host_header = self.headers.get("Host", "127.0.0.1:8777")
                    proto_hint = self.headers.get("X-Forwarded-Proto", "")
                    out["active_room_id"] = active.room_id
                    out["active_phase"] = active.phase
                    out["active_share_url"] = active.room_link(host_header, proto_hint)
                self._send_json(out, status=HTTPStatus.CONFLICT)
                return
            host_seat = room.host_seat()
            if host_seat is None:
                self._send_json({"ok": False, "error": "failed to create host seat"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            host_header = self.headers.get("Host", "127.0.0.1:8777")
            proto_hint = self.headers.get("X-Forwarded-Proto", "")
            self._send_json(
                {
                    "ok": True,
                    "room_id": room.room_id,
                    "play_path": f"/play/{room.room_id}",
                    "share_url": room.room_link(host_header, proto_hint),
                    "public_links": load_public_links(),
                    "host_token": room.host_control_token,
                    "seat_token": host_seat.token,
                    "seat_index": host_seat.index,
                }
            )
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "join":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            existing_tok = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            if room.quick_play:
                with room.cond:
                    already_seated = room._seat_from_token_locked(existing_tok) is not None
                if not already_seated:
                    self._send_json(
                        {"ok": False, "error": "Join this room through Quick Play."},
                        status=HTTPStatus.FORBIDDEN,
                    )
                    return
            # Password check for private rooms (skip if the joiner already holds a token for this room)
            if room.password_hash is not None:
                with room.cond:
                    already_seated = room._seat_from_token_locked(existing_tok) is not None
                if not already_seated:
                    pw_plain = body.get("password") if isinstance(body.get("password"), str) else ""
                    pw_given = hashlib.sha256(pw_plain.strip().encode()).hexdigest() if pw_plain else ""
                    if not secrets.compare_digest(pw_given, room.password_hash):
                        self._send_json({"ok": False, "error": "Incorrect password."}, status=HTTPStatus.FORBIDDEN)
                        return
            seat_index_raw = body.get("seat_index")
            seat_index = int(seat_index_raw) if isinstance(seat_index_raw, int) else None
            player_name = safe_name(body.get("player_name"), "Player")
            existing_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            host_token = body.get("host_token") if isinstance(body.get("host_token"), str) else ""
            allow_takeover = bool(body.get("takeover")) if isinstance(body.get("takeover"), bool) else False
            req_create_key = body.get("create_key") if isinstance(body.get("create_key"), str) else ""
            allow_host_takeover = bool(
                (CREATE_KEY and secrets.compare_digest(req_create_key, CREATE_KEY))
                or room.is_host_authorized(host_token, existing_token)
            )
            out = room.claim_seat(
                player_name,
                seat_index,
                existing_token,
                allow_takeover=allow_takeover,
                allow_host_takeover=allow_host_takeover,
            )

            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "start":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            host_token = body.get("host_token") if isinstance(body.get("host_token"), str) else ""
            seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            req_create_key = body.get("create_key") if isinstance(body.get("create_key"), str) else ""
            allow_with_create_key = bool(CREATE_KEY and secrets.compare_digest(req_create_key, CREATE_KEY))
            out = room.start_game(host_token, seat_token, CARD_DB, allow_with_create_key=allow_with_create_key)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "restart":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            host_token = body.get("host_token") if isinstance(body.get("host_token"), str) else ""
            seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            out = room.restart_game(host_token, seat_token, CARD_DB)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "play_again":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            out = room.play_again(seat_token, CARD_DB)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "terminate":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            host_token = body.get("host_token") if isinstance(body.get("host_token"), str) else ""
            seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            out = room.terminate_game(host_token, seat_token)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.FORBIDDEN
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "action":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            out = room.submit_action(body)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        # ── Current Controller: hard-gated admin mod tools ──────────────────
        # Requires BOTH (1) the admin key (constant-time compared) and (2) a
        # valid seat token for THIS room. The key defaults to "dog" but can be
        # overridden by the ADMIN_MOD_KEY env secret. Without the key, every op
        # is rejected — so console/URL tampering by non-admins fails.
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "admin_mod":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            effective_key = os.environ.get("ADMIN_MOD_KEY", "").strip() or "dog"
            supplied = body.get("admin_key") if isinstance(body.get("admin_key"), str) else ""
            if not supplied or not secrets.compare_digest(supplied, effective_key):
                self._send_json({"ok": False, "error": "not authorized"}, status=HTTPStatus.FORBIDDEN)
                return
            seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            with room.cond:
                seat = room._seat_from_token_locked(seat_token)
            if seat is None:
                self._send_json({"ok": False, "error": "valid seat token required"}, status=HTTPStatus.FORBIDDEN)
                return
            # Authorized: turn on hidden-state capture for this room (and capture
            # immediately so the first reveal already has data).
            room.admin_activate()
            op = body.get("op") if isinstance(body.get("op"), str) else ""
            params = body.get("params") if isinstance(body.get("params"), dict) else {}
            try:
                if op == "reveal":
                    out = room.admin_reveal()
                elif op == "catalog":
                    out = {"ok": True, "catalog": build_admin_card_catalog()}
                elif op == "bot_brain":
                    with room.cond:
                        out = {"ok": True, "brain": copy.deepcopy(getattr(room, "_bot_brain", {}) or {})}
                elif op == "bot_override_arm":
                    out = room.admin_arm_bot_override(params.get("seat"), params.get("action_index"), params.get("action"))
                elif op in (
                    "hand_add", "hand_remove", "hand_clear", "hand_copy_to_me",
                    "pool_clear", "pool_add", "pool_remove", "pool_refill", "deck_place",
                    "mint",
                ):
                    out = room.admin_enqueue_mod(op, params)
                else:
                    out = {"ok": False, "error": f"unknown op {op}"}
            except Exception as exc:
                out = {"ok": False, "error": f"{exc}"}
            self._send_json(out, status=HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "chat":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            out = room.submit_chat(body)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "undo":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            out = room.submit_undo(body)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "ai_speed":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            out = room.set_ai_speed(body)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "away":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            out = room.set_away(body)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "afk_cancel":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            out = room.afk_cancel(body)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        # Team Mode: move my own seat to another team (self-service, lobby only).
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "team":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            team_val = body.get("team") if isinstance(body.get("team"), int) else None
            out = room.set_seat_team(seat_token, team_val)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        # Team Mode: request / accept / decline a cross-team player swap.
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "swap":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            action = str(body.get("action") or "").strip().lower()
            if action == "request":
                target = body.get("target_seat") if isinstance(body.get("target_seat"), int) else None
                out = room.request_team_swap(seat_token, target)
            elif action in {"accept", "decline"}:
                from_seat = body.get("from_seat") if isinstance(body.get("from_seat"), int) else None
                out = room.respond_team_swap(seat_token, action, from_seat)
            else:
                out = {"ok": False, "error": "action must be request, accept, or decline"}
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "avatar":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            out = room.set_avatar(body)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "background":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            out = room.set_background(body)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "inactive":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            out = room.set_inactive_eligible(body)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "draw_for_inactive":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            out = room.draw_for_inactive(body)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "leave":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            out = room.leave_room(body, CARD_DB)
            # If the room was discarded and no one remains, remove it from ROOMS
            if out.get("action") == "discarded":
                ROOMS.remove(parts[2])
            self._send_json(out, status=HTTPStatus.OK)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "seat_difficulty":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            host_token = body.get("host_token") if isinstance(body.get("host_token"), str) else ""
            seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            try:
                seat_index = int(body.get("seat_index"))
            except (TypeError, ValueError):
                self._send_json({"ok": False, "error": "seat_index must be int"}, status=HTTPStatus.BAD_REQUEST)
                return
            difficulty = body.get("difficulty") if isinstance(body.get("difficulty"), str) else ""
            out = room.set_seat_difficulty(host_token, seat_token, seat_index, difficulty)
            status = HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "quickplay_seats":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            host_token = body.get("host_token") if isinstance(body.get("host_token"), str) else ""
            seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            try:
                human_players = int(body.get("human_players"))
            except (TypeError, ValueError):
                self._send_json(
                    {"ok": False, "error": "human_players must be 2, 3, or 4"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            out = room.configure_quick_play_seats(
                host_token,
                seat_token,
                human_players,
            )
            if out.get("ok"):
                status = HTTPStatus.OK
            elif out.get("error") == "host authorization required":
                status = HTTPStatus.FORBIDDEN
            else:
                status = HTTPStatus.BAD_REQUEST
            self._send_json(out, status=status)
            return

        # ── Spectator endpoints ──────────────────────────────────────
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "spectate":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            name = str(body.get("name") or "Spectator").strip()[:32] or "Spectator"
            out = room.spectator_join(name)
            self._send_json(out, status=HTTPStatus.OK if out.get("ok") else HTTPStatus.FORBIDDEN)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "spectate_leave":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": True}, status=HTTPStatus.OK)
                return
            token = str(body.get("spectator_token") or "")
            self._send_json(room.spectator_leave(token), status=HTTPStatus.OK)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "spectate_chat":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            token = str(body.get("spectator_token") or "")
            message = str(body.get("message") or "")
            self._send_json(room.submit_spectator_chat(token, message), status=HTTPStatus.OK)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "kick_spectator":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            seat_token = str(body.get("seat_token") or "")
            target_token = str(body.get("spectator_token") or "")
            with room.cond:
                voter_seat = room._seat_from_token_locked(seat_token)
            if voter_seat is None:
                self._send_json({"ok": False, "error": "invalid seat token"}, status=HTTPStatus.FORBIDDEN)
                return
            self._send_json(room.spectator_kick_vote(voter_seat.index, target_token), status=HTTPStatus.OK)
            return

        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "rooms" and parts[3] == "allow_spectators":
            room = ROOMS.get(parts[2])
            if room is None:
                self._send_json({"ok": False, "error": "room not found"}, status=HTTPStatus.NOT_FOUND)
                return
            host_token = str(body.get("host_token") or "")
            seat_token = body.get("seat_token") if isinstance(body.get("seat_token"), str) else None
            allow = bool(body.get("allow", True))
            self._send_json(room.set_allow_spectators(host_token, seat_token, allow), status=HTTPStatus.OK)
            return

        if parsed.path == "/api/competitive/forfeit_ack":
            # The loser's client has applied the CP penalty for a forfeit loss —
            # mark the pending entry processed so it is never applied twice.
            entry_id = str(body.get("id", "")).strip()
            who = str(body.get("name", "")).strip()
            if not entry_id or not who:
                self._send_json({"ok": False, "error": "id and name required"}, status=HTTPStatus.BAD_REQUEST)
                return
            with COMPETITIVE_LOCK:
                try:
                    with open(COMPETITIVE_FORFEITS_PATH, "r", encoding="utf-8") as f:
                        pending = json.load(f)
                    if not isinstance(pending, dict):
                        pending = {}
                except (FileNotFoundError, json.JSONDecodeError):
                    pending = {}
                entry = pending.get(entry_id)
                if not isinstance(entry, dict):
                    self._send_json({"ok": False, "error": "unknown forfeit id"}, status=HTTPStatus.NOT_FOUND)
                    return
                # Guard: only the named loser may ack their own forfeit loss.
                if str(entry.get("loser", "")).strip() != who:
                    self._send_json({"ok": False, "error": "name mismatch"}, status=HTTPStatus.FORBIDDEN)
                    return
                entry["processed"] = True
                entry["processed_unix"] = now_unix()
                if "cp_delta" in body:
                    try:
                        entry["cp_delta"] = int(body.get("cp_delta", 0))
                    except (TypeError, ValueError):
                        pass
                pending[entry_id] = entry
                atomic_write_json(COMPETITIVE_FORFEITS_PATH, pending)
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/competitive/ranked_result":
            room_id = body.get("room_id", "")
            if not room_id:
                self._send_json({"ok": False, "error": "room_id required"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                os.makedirs(COMPETITIVE_GAMES_DIR, exist_ok=True)
                prefix = f"game_{room_id}_"
                files = sorted(
                    [f for f in os.listdir(COMPETITIVE_GAMES_DIR) if f.startswith(prefix) and f.endswith(".json")],
                    reverse=True,
                )
                if not files:
                    self._send_json({"ok": False, "error": "game record not found"}, status=HTTPStatus.NOT_FOUND)
                    return
                fpath = os.path.join(COMPETITIVE_GAMES_DIR, files[0])
                with COMPETITIVE_LOCK:
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            record = json.load(f)
                    except (FileNotFoundError, json.JSONDecodeError):
                        self._send_json({"ok": False, "error": "could not read game record"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                        return
                    confirmed_ts = now_unix()
                    record["ranked"] = True
                    record["ranked_confirmed_unix"] = confirmed_ts
                    # Enrich with client-supplied data if provided
                    if "season_id" in body:
                        record["season_id"] = str(body["season_id"])
                    elif "season_id" not in record:
                        record["season_id"] = get_season_id(confirmed_ts)
                    if "p1_cp_after" in body:
                        record["p1_cp_after"] = int(body.get("p1_cp_after", 0))
                    if "p2_cp_after" in body:
                        record["p2_cp_after"] = int(body.get("p2_cp_after", 0))
                    if "p1_cp_delta" in body:
                        record["p1_cp_delta"] = int(body.get("p1_cp_delta", 0))
                    if "p2_cp_delta" in body:
                        record["p2_cp_delta"] = int(body.get("p2_cp_delta", 0))
                    if "p1_rank_after" in body:
                        record["p1_rank_after"] = str(body.get("p1_rank_after", ""))
                    if "p2_rank_after" in body:
                        record["p2_rank_after"] = str(body.get("p2_rank_after", ""))
                    atomic_write_json(fpath, record)
                # Update seasonal leaderboard
                season_id = record.get("season_id", get_season_id())
                p1_name = record.get("p1_name", "")
                p2_name = record.get("p2_name", "")
                winner  = record.get("winner")
                is_draw = bool(record.get("is_draw", False))
                season_lb_path = os.path.join(COMPETITIVE_GAMES_DIR, f"leaderboard_{season_id}.json")
                with COMPETITIVE_LOCK:
                    try:
                        with open(season_lb_path, "r", encoding="utf-8") as f:
                            season_board: Dict[str, Any] = json.load(f)
                    except (FileNotFoundError, json.JSONDecodeError):
                        season_board = {}
                    for nm in (p1_name, p2_name):
                        if nm and nm not in season_board:
                            season_board[nm] = {"wins": 0, "losses": 0, "draws": 0, "games": 0,
                                                "best_score": 0, "best_streak": 0, "season_id": season_id}
                    if p1_name:
                        season_board[p1_name]["games"] = season_board[p1_name].get("games", 0) + 1
                        bs = max(int(record.get("p1_best_score", 0)), int(season_board[p1_name].get("best_score", 0)))
                        season_board[p1_name]["best_score"] = bs
                        if "p1_cp_after" in record:
                            season_board[p1_name]["cp"] = int(record["p1_cp_after"])
                        if "p1_rank_after" in record:
                            season_board[p1_name]["rank"] = str(record["p1_rank_after"])
                    if p2_name:
                        season_board[p2_name]["games"] = season_board[p2_name].get("games", 0) + 1
                        bs = max(int(record.get("p2_best_score", 0)), int(season_board[p2_name].get("best_score", 0)))
                        season_board[p2_name]["best_score"] = bs
                        if "p2_cp_after" in record:
                            season_board[p2_name]["cp"] = int(record["p2_cp_after"])
                        if "p2_rank_after" in record:
                            season_board[p2_name]["rank"] = str(record["p2_rank_after"])
                    if is_draw:
                        for nm in (p1_name, p2_name):
                            if nm:
                                season_board[nm]["draws"] = season_board[nm].get("draws", 0) + 1
                    elif winner:
                        loser = p2_name if winner == p1_name else p1_name
                        if winner in season_board:
                            season_board[winner]["wins"] = season_board[winner].get("wins", 0) + 1
                        if loser and loser in season_board:
                            season_board[loser]["losses"] = season_board[loser].get("losses", 0) + 1
                    atomic_write_json(season_lb_path, season_board)
                self._send_json({"ok": True, "season_id": season_id})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if parsed.path == "/api/competitive/reset":
            try:
                deleted = 0
                if os.path.exists(COMPETITIVE_GAMES_DIR):
                    for fname in os.listdir(COMPETITIVE_GAMES_DIR):
                        if fname.startswith("game_") and fname.endswith(".json"):
                            try:
                                os.remove(os.path.join(COMPETITIVE_GAMES_DIR, fname))
                                deleted += 1
                            except Exception:
                                pass
                if os.path.exists(COMPETITIVE_LEADERBOARD_PATH):
                    os.remove(COMPETITIVE_LEADERBOARD_PATH)
                self._send_json({"ok": True, "deleted_games": deleted})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self._send_json({"ok": False, "error": "not found"}, status=HTTPStatus.NOT_FOUND)


def main() -> None:
    global PUBLIC_BASE_URL, ACTIVE_SERVER, CREATE_KEY, CORS_ALLOW_ORIGIN
    parser = argparse.ArgumentParser(description="Fish Game multiplayer server")
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument(
        "--public-base-url",
        type=str,
        default="",
        help="Optional externally reachable base URL (example: http://192.168.1.50:8777)",
    )
    parser.add_argument(
        "--create-key",
        type=str,
        default="",
        help="Host-only lobby create key. If omitted, a random key is generated at startup.",
    )
    parser.add_argument(
        "--cors-allow-origin",
        type=str,
        default=os.environ.get("FISH_CORS_ALLOW_ORIGIN", "*"),
        help="CORS Access-Control-Allow-Origin value for API/SSE responses (default: *).",
    )
    args = parser.parse_args()
    PUBLIC_BASE_URL = str(args.public_base_url or "").strip()
    raw_key = str(args.create_key or os.environ.get("FISH_CREATE_KEY") or "").strip()
    CREATE_KEY = raw_key  # empty string = no key required (open access)
    CORS_ALLOW_ORIGIN = str(args.cors_allow_origin or "*").strip() or "*"

    os.makedirs(os.path.dirname(DATASET_PATH), exist_ok=True)
    os.makedirs(GAMES_HISTORY_DIR, exist_ok=True)

    # Bootstrap the stats file with historical seed values if it doesn't exist yet.
    if STATS_SEED_GAMES > 0 or STATS_SEED_PLAYERS > 0:
        try:
            os.makedirs(os.path.dirname(STATS_PATH), exist_ok=True)
            with STATS_LOCK:
                try:
                    with open(STATS_PATH, "r", encoding="utf-8") as _sf:
                        _existing = json.load(_sf)
                except (FileNotFoundError, json.JSONDecodeError):
                    _existing = {}
                # Apply seed as a floor — never lower existing counts.
                _existing["games_played"]       = max(int(_existing.get("games_played", 0)),       STATS_SEED_GAMES)
                _existing["registered_players"] = max(int(_existing.get("registered_players", 0)), STATS_SEED_PLAYERS)
                if "seen_uids" not in _existing:
                    _existing["seen_uids"] = []
                atomic_write_json(STATS_PATH, _existing)
            print(f"Stats seeded: games_played={_existing['games_played']} registered_players={_existing['registered_players']}")
        except Exception as _se:
            print(f"Stats seed warning: {_se}")
        # Mirror the games baseline into Firestore (the persistent source of
        # truth) so /api/stats never drops below it after a redeploy.
        try:
            seed_firestore_games_played(STATS_SEED_GAMES)
        except Exception as _fe:
            print(f"Firestore stats seed warning: {_fe}")

    restore_stats = ROOMS.load_persisted_rooms(CARD_DB)

    ACTIVE_SERVER = StableThreadingHTTPServer((args.host, args.port), MultiplayerHandler)
    bound_host = str(args.host or "").strip() or "0.0.0.0"
    open_host = bound_host
    if open_host in {"0.0.0.0", "::", "[::]"}:
        open_host = "127.0.0.1"
    local_open_url = f"http://{open_host}:{args.port}"
    print(f"Serving Fish multiplayer (listening on {bound_host}:{args.port})")
    print(f"Open in this browser: {local_open_url}/")
    if bound_host in {"0.0.0.0", "::", "[::]", "127.0.0.1", "localhost"}:
        lan_ip = detect_lan_ipv4()
        if lan_ip:
            print(f"Open from another device: http://{lan_ip}:{args.port}/")
    print(f"Host lobby create key: {CREATE_KEY}")
    print(f"Host setup URL: {local_open_url}/?create_key={CREATE_KEY}")
    print(
        "Recovered rooms: "
        f"{restore_stats.get('loaded', 0)} loaded, "
        f"{restore_stats.get('resumed', 0)} resumed, "
        f"{restore_stats.get('skipped', 0)} skipped, "
        f"{restore_stats.get('failed', 0)} failed."
    )
    try:
        ACTIVE_SERVER.serve_forever()
    finally:
        try:
            ACTIVE_SERVER.server_close()
        except Exception:
            pass
        ACTIVE_SERVER = None


CARD_DB = fish.load_card_db()


if __name__ == "__main__":
    main()
