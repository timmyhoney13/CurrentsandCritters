"""Currents and Critters — Prestige System (server-authoritative core).

Wired additively into multiplayer_server (same pattern as clan_server):
    import prestige_server
    prestige_server.init(...)                     # in main()
    if prestige_server.handle_get(self, parsed):  # in do_GET
    if prestige_server.handle_post(self, parsed, body):  # in do_POST
    prestige_server.store_bonus_for(stats, coins)  # from the Stripe webhook

A player who reaches the level cap may "ride the next current": their level and
XP go back to the start, and in exchange they keep a permanent, stacking set of
rewards. NOTHING the player paid for, earned competitively, or was given by a
previous Prestige is ever taken away — see KEEP_FOREVER_UNLOCK_TYPES and the
reset list in _commit().

Everything lives in Firestore (admin SDK — the browser can never write any of
it, so a devtools edit of the Prestige level, the coin reward or the XP
multiplier is not a thing that can happen):
    users/{uid}.prestige         the whole prestige record (level, unlocks,
                                 equipped appearance, history)
    users/{uid}.stats            level/XP reset + the coin reward land here
    users/{uid}.unlocked_icons   relocked down to the permanent set
    prestige_ledger/{uid}_{n}    ONE doc per prestige — its doc-id create() is
                                 the atomic "this prestige happened exactly
                                 once" guarantee, and it doubles as the
                                 admin log
    prestige_ledger/{uid}_{n}_idem_{key}  client idempotency key → result, so a
                                 double-tap / refresh / second device replays
                                 the same answer instead of prestiging twice
    prestige_admin_log/{id}      every administrator correction

⚠️ THE TWO TABLES THAT MUST NOT DRIFT
  • LEVEL_XP_TOTALS lives in multiplayer_server.py and js/preview-app.js. This
    module is handed the level function, it does not keep a third copy.
  • AVATAR_UNLOCK_TYPES below mirrors ANIMAL_AVATARS in js/preview-app.js. It is
    what decides whether an avatar relocks, so a new avatar added to the client
    and not here would either survive a Prestige it should not, or be relocked
    when it was bought with real money. test_prestige_server.py parses
    preview-app.js and fails if the two ever disagree.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

# ── Injected by init() (no circular import with multiplayer_server) ──────────
_get_firestore: Optional[Callable[[], Any]] = None
_verify_token: Optional[Callable[[str], Optional[dict]]] = None
_level_progress: Optional[Callable[[Any], Tuple[int, int, int]]] = None
_find_uid_by_username: Optional[Callable[[Any, str], Optional[str]]] = None
_max_level: int = 100


def init(*, get_firestore, verify_token, level_progress, max_level,
         find_uid_by_username=None) -> None:
    global _get_firestore, _verify_token, _level_progress, _max_level
    global _find_uid_by_username
    _get_firestore = get_firestore
    _verify_token = verify_token
    _level_progress = level_progress
    _find_uid_by_username = find_uid_by_username
    _max_level = int(max_level)


# ═══════════════════════════════════════════════════════════════════════════
#  REWARD MATH — the single definition of every number the player is promised
# ═══════════════════════════════════════════════════════════════════════════
PRESTIGE_COIN_BASE = 500        # Prestige 1
PRESTIGE_COIN_STEP = 250        # each further Prestige
PRESTIGE_XP_STEP = 0.25         # +25% XP per Prestige, stacking
PRESTIGE_STORE_STEP = 0.05      # +5% on bought coin packs per Prestige, stacking
PRESTIGE_KEEP_AVATARS = 2       # avatars the player chooses to carry over
CONFIRM_PHRASE = "PRESTIGE"     # typed by hand before the final button unlocks
MAX_PRESTIGE_LEVEL = 999        # a hard ceiling so nothing can loop forever


def coin_reward_for(new_level: Any) -> int:
    """Critter Coins paid for REACHING `new_level` (Prestige 1 = 500)."""
    n = _int(new_level)
    if n < 1:
        return 0
    return PRESTIGE_COIN_BASE + PRESTIGE_COIN_STEP * (n - 1)


def xp_multiplier_for(level: Any) -> float:
    """Total XP = base XP × this. Prestige 0 = 1.0, Prestige 3 = 1.75."""
    return 1.0 + PRESTIGE_XP_STEP * max(0, _int(level))


def store_bonus_pct_for(level: Any) -> int:
    """Extra % of Critter Coins on a BOUGHT coin pack. Prestige 3 = 15."""
    return int(round(PRESTIGE_STORE_STEP * 100)) * max(0, _int(level))


def apply_xp_bonus(base_xp: Any, level: Any) -> Dict[str, int]:
    """{base, bonus, total} for a base XP amount at a Prestige level.

    The bonus is applied to whatever is handed in, so an existing reduction
    (an AI game already halved to 50) stays reduced and is then multiplied —
    50 base at Prestige 3 is 50 + 37 = 87, not 175/2.
    """
    base = max(0, _int(base_xp))
    total = int(math.floor(base * xp_multiplier_for(level)))
    return {"base": base, "bonus": total - base, "total": total}


def store_bonus_coins(base_coins: Any, level: Any) -> int:
    """Extra coins on a PURCHASED pack, rounded to the nearest whole coin.

    Deliberately NOT applied to refunds, admin grants, free rewards, challenge
    rewards or the Prestige coin reward itself — the webhook only calls this on
    a verified `kind == "coins"` Stripe purchase.
    """
    base = max(0, _int(base_coins))
    lvl = max(0, _int(level))
    if base <= 0 or lvl <= 0:
        return 0
    return int(round(base * lvl * PRESTIGE_STORE_STEP))


def store_bonus_for(stats: Any, base_coins: Any) -> int:
    """store_bonus_coins() for a user doc's `prestige` map — the webhook's entry
    point. Reads the STORED level (server data), never anything from a client."""
    return store_bonus_coins(base_coins, prestige_level_of(stats))


def prestige_level_of(doc: Any) -> int:
    """Prestige level from a users/{uid} doc (or its `prestige` map)."""
    if not isinstance(doc, dict):
        return 0
    node = doc.get("prestige") if isinstance(doc.get("prestige"), dict) else doc
    return max(0, _int(node.get("level")))


# ═══════════════════════════════════════════════════════════════════════════
#  COSMETIC CATALOGUES
# ═══════════════════════════════════════════════════════════════════════════
# ── Badges ──────────────────────────────────────────────────────────────────
# `art` is the renderer key js/prestige-ui.js draws (SVG, not an image file, so
# a badge costs nothing to load and scales cleanly beside a username).
PRESTIGE_BADGES: List[Dict[str, Any]] = [
    {"level": 1,  "id": "wave",      "name": "Small Wave",        "art": "wave"},
    {"level": 2,  "id": "wave2",     "name": "Double Wave",       "art": "wave2"},
    {"level": 3,  "id": "coral",     "name": "Coral Crest",       "art": "coral"},
    {"level": 4,  "id": "shell",     "name": "Glowing Shell",     "art": "shell"},
    {"level": 5,  "id": "current",   "name": "Golden Current",    "art": "current"},
    {"level": 6,  "id": "pearl",     "name": "Deep Pearl",        "art": "pearl"},
    {"level": 7,  "id": "trident",   "name": "Coral Trident",     "art": "trident"},
    {"level": 8,  "id": "nautilus",  "name": "Spiral Nautilus",   "art": "nautilus"},
    {"level": 9,  "id": "aurora",    "name": "Abyssal Aurora",    "art": "aurora"},
    {"level": 10, "id": "crown",     "name": "Ocean Crown",       "art": "crown"},
]

PRESTIGE_TITLES = [
    "Tide Rider", "Current Chaser", "Reef Wanderer", "Deep Diver",
    "Abyss Walker", "Storm Caller", "Leviathan", "Tide Sovereign",
    "Ocean Sage", "Eternal Current",
]

# ── Prestige backgrounds ────────────────────────────────────────────────────
# Living scenes, not image files: js/prestige-ui.js paints each one from the
# game's own palette + real critter art (drifting silhouettes), which is what
# lets them move (currents, light rays, bubbles, kelp, bioluminescence) without
# shipping a single new megabyte. `scene` is the renderer key.
PRESTIGE_BACKGROUNDS: List[Dict[str, Any]] = [
    {"level": 1,  "id": "pbg-shallows",  "name": "Sunlit Shallows",     "scene": "shallows",
     "blurb": "Warm surface light raking down through clear, drifting water."},
    {"level": 2,  "id": "pbg-kelp",      "name": "Kelp Cathedral",      "scene": "kelp",
     "blurb": "Towering kelp swaying in a slow green current."},
    {"level": 3,  "id": "pbg-bloom",     "name": "Coral Bloom",         "scene": "bloom",
     "blurb": "A reef in full colour, pulsing gently with the tide."},
    {"level": 4,  "id": "pbg-midnight",  "name": "Midnight Drift",      "scene": "midnight",
     "blurb": "Deep blue quiet, with silhouettes passing far above."},
    # ⚠️ Not "Golden Current" — that is the Prestige 5 BADGE, and a reward card
    # that reads "Background: Golden Current / Badge: Golden Current" looks like
    # a bug on the one screen that has to be unambiguous.
    {"level": 5,  "id": "pbg-golden",    "name": "Amber Tide",          "scene": "golden",
     "blurb": "A warm river of light running straight through the ocean."},
    {"level": 6,  "id": "pbg-biolume",   "name": "Bioluminescent Bay",  "scene": "biolume",
     "blurb": "Cold living light blooming in the dark where you swim."},
    {"level": 7,  "id": "pbg-arctic",    "name": "Arctic Shelf",        "scene": "arctic",
     "blurb": "Pale ice above, impossible blue below."},
    {"level": 8,  "id": "pbg-trench",    "name": "Abyssal Trench",      "scene": "trench",
     "blurb": "The pressure dark, lit only by what lives in it."},
    {"level": 9,  "id": "pbg-surge",     "name": "Storm Surge",         "scene": "surge",
     "blurb": "The surface torn open, currents running hard beneath."},
    {"level": 10, "id": "pbg-celestial", "name": "Celestial Tide",      "scene": "celestial",
     "blurb": "Where the ocean stops being water and starts being sky."},
]

_ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]


def background_for_level(level: Any) -> Dict[str, Any]:
    """The background unlocked AT `level`. Past 10 the ten scenes come round
    again as numbered variants, so a Prestige 14 still unlocks something with a
    real name and a real look instead of nothing at all."""
    n = max(1, _int(level))
    base = PRESTIGE_BACKGROUNDS[(n - 1) % len(PRESTIGE_BACKGROUNDS)]
    cycle = (n - 1) // len(PRESTIGE_BACKGROUNDS)
    if cycle == 0:
        return {**base, "prestige": n}
    suffix = _ROMAN[cycle + 1] if cycle + 1 < len(_ROMAN) else str(cycle + 1)
    return {**base,
            "id": f"{base['id']}-{cycle + 1}",
            "name": f"{base['name']} {suffix}",
            "prestige": n,
            "variant": cycle}


def badge_for_level(level: Any) -> Optional[Dict[str, Any]]:
    """The badge worn at `level` — the highest one reached (10+ all wear the
    Crown, with the number beside it doing the talking)."""
    n = _int(level)
    if n < 1:
        return None
    best = None
    for b in PRESTIGE_BADGES:
        if n >= b["level"]:
            best = b
    return {**best, "prestige": n} if best else None


def title_for_level(level: Any) -> str:
    n = _int(level)
    if n < 1:
        return "Ocean Explorer"
    if n <= len(PRESTIGE_TITLES):
        return PRESTIGE_TITLES[n - 1]
    return f"Eternal Current {n - len(PRESTIGE_TITLES) + 1}"


# ── Username colours ────────────────────────────────────────────────────────
# Every colour carries the level it becomes available at. Prestige 1 is the one
# level where the player PICKS one of two and banks the other for Prestige 2 —
# every later unlock is automatic.
NAME_COLORS: List[Dict[str, Any]] = [
    {"id": "default",  "name": "Default",        "hex": "",        "level": 0},
    {"id": "ocean",    "name": "Ocean Blue",     "hex": "#1f7ae0", "level": 1, "choice": True},
    {"id": "seafoam",  "name": "Seafoam Green",  "hex": "#12a37c", "level": 1, "choice": True},
    {"id": "purple",   "name": "Deep Purple",    "hex": "#7a49d6", "level": 2},
    {"id": "gold",     "name": "Gold",           "hex": "#b8860b", "level": 3},
]
CUSTOM_COLOR_LEVEL = 4          # the solid-colour creator
GRADIENT_LEVEL = 5              # gradients + the gradient editor
THREE_COLOR_LEVEL = 10          # three-stop gradients

NAME_GRADIENTS: List[Dict[str, Any]] = [
    {"id": "ocean-seafoam", "name": "Ocean to Seafoam",  "from": "#1f7ae0", "to": "#12a37c", "level": 5},
    {"id": "purple-ocean",  "name": "Deep Purple to Ocean", "from": "#7a49d6", "to": "#1f7ae0", "level": 5},
    {"id": "gold-coral",    "name": "Gold to Coral",     "from": "#b8860b", "to": "#e0644a", "level": 5},
    {"id": "arctic-white",  "name": "Arctic to White",   "from": "#2f9fd0", "to": "#5b6b78", "level": 5},
]
GRADIENT_DIRECTIONS = ["h", "v", "d"]                 # across / down / diagonal
GRADIENT_STYLES = ["smooth", "split", "center", "edges"]

# Animated name effects. Each is deliberately slow and low-amplitude; the
# player (or their OS reduced-motion setting) can switch all of them off and
# keep the colour — see `animate` in the appearance record.
NAME_EFFECTS: List[Dict[str, Any]] = [
    {"id": "none",    "name": "None",              "level": 0},
    {"id": "glow",    "name": "Subtle Glow",       "level": 6},
    {"id": "shimmer", "name": "Gentle Shimmer",    "level": 7},
    {"id": "bubbles", "name": "Rising Bubbles",    "level": 8},
    {"id": "flow",    "name": "Flowing Water",     "level": 9},
    {"id": "pulse",   "name": "Bioluminescent Pulse", "level": 10},
    {"id": "wave",    "name": "Passing Wave",      "level": 10},
]

# ── Alternate animal skins ──────────────────────────────────────────────────
# Cosmetic art treatments applied over a card's EXISTING artwork — they change
# nothing about the card: not its ability, star ability, cost, points, ocean
# requirement, interactions, rarity or balance. The client renders them; the
# server's job is only to say which (animal, style) pairs an account owns.
SKIN_STYLES: List[Dict[str, Any]] = [
    {"id": "golden",   "name": "Golden",          "level": 1},
    {"id": "albino",   "name": "Albino",          "level": 1},
    {"id": "biolume",  "name": "Bioluminescent",  "level": 1},
    {"id": "midnight", "name": "Midnight",        "level": 2},
    {"id": "arctic",   "name": "Arctic",          "level": 2},
    {"id": "irides",   "name": "Iridescent",      "level": 3},
    {"id": "coral",    "name": "Coral-Covered",   "level": 3},
    {"id": "royal",    "name": "Royal",           "level": 4},
    {"id": "shadow",   "name": "Shadow",          "level": 4},
    {"id": "celest",   "name": "Celestial",       "level": 5},
]

# name → (family, representative card uid). Generated from the printed card
# lists (cards_lr.txt / cards_vertical.txt / cards_oceans.txt); Oceans and END
# GAME are not animals and are not skinnable. test_prestige_server.py
# regenerates this from those files and fails if it drifts.
SKIN_ANIMALS: List[Dict[str, Any]] = [
    {"id": "tarpon",                 "name": "Tarpon",                  "family": "Game Fish",    "uid": 101},
    {"id": "spinner-dolphin",        "name": "Spinner Dolphin",         "family": "Mammal",       "uid": 102},
    {"id": "blue-marlin",            "name": "Blue Marlin",             "family": "Game Fish",    "uid": 104},
    {"id": "great-white-shark",      "name": "Great White Shark",       "family": "Crosscurrent", "uid": 105},
    {"id": "goliath-grouper",        "name": "Goliath Grouper",         "family": "Game Fish",    "uid": 106},
    {"id": "yellowfin-tuna",         "name": "Yellowfin Tuna",          "family": "Game Fish",    "uid": 107},
    {"id": "bottlenose-dolphin",     "name": "Bottlenose Dolphin",      "family": "Mammal",       "uid": 109},
    {"id": "common-octopus",         "name": "Common Octopus",          "family": "Cephalopod",   "uid": 110},
    {"id": "manta-ray",              "name": "Manta Ray",               "family": "Crosscurrent", "uid": 111},
    {"id": "roosterfish",            "name": "Roosterfish",             "family": "Game Fish",    "uid": 112},
    {"id": "clownfish",              "name": "Clownfish",               "family": "Crosscurrent", "uid": 113},
    {"id": "reef-trigger-fish",      "name": "Reef Trigger Fish",       "family": "Crosscurrent", "uid": 115},
    {"id": "narwhal",                "name": "Narwhal",                 "family": "Mammal",       "uid": 117},
    {"id": "bigeye-tuna",            "name": "Bigeye Tuna",             "family": "Crosscurrent", "uid": 120},
    {"id": "sailfish",               "name": "Sailfish",                "family": "Game Fish",    "uid": 128},
    {"id": "mahi-mahi",              "name": "Mahi Mahi",               "family": "Game Fish",    "uid": 135},
    {"id": "barracuda",              "name": "Barracuda",               "family": "Game Fish",    "uid": 139},
    {"id": "king-salmon",            "name": "King Salmon",             "family": "Game Fish",    "uid": 141},
    {"id": "whale-shark",            "name": "Whale Shark",             "family": "Crosscurrent", "uid": 142},
    {"id": "cuttlefish",             "name": "Cuttlefish",              "family": "Cephalopod",   "uid": 144},
    {"id": "giant-squid",            "name": "Giant Squid",             "family": "Cephalopod",   "uid": 150},
    {"id": "bobtail-squid",          "name": "Bobtail Squid",           "family": "Cephalopod",   "uid": 155},
    {"id": "emperor-penguin",        "name": "Emperor Penguin",         "family": "Bird",         "uid": 1},
    {"id": "staghorn-coral",         "name": "Staghorn Coral",          "family": "Coral",        "uid": 2},
    {"id": "hermit-crab",            "name": "Hermit Crab",             "family": "Crustacean",   "uid": 4},
    {"id": "mantis-shrimp",          "name": "Mantis Shrimp",           "family": "Crustacean",   "uid": 6},
    {"id": "spiny-lobster",          "name": "Spiny Lobster",           "family": "Crustacean",   "uid": 8},
    {"id": "horned-puffin",          "name": "Horned Puffin",           "family": "Bird",         "uid": 9},
    {"id": "common-sea-star",        "name": "Common Sea Star",         "family": "Invertebrate", "uid": 10},
    {"id": "lobster",                "name": "Lobster",                 "family": "Crustacean",   "uid": 12},
    {"id": "blue-tang",              "name": "Blue Tang",               "family": "Crosscurrent", "uid": 14},
    {"id": "california-gull",        "name": "California Gull",         "family": "Bird",         "uid": 17},
    {"id": "johnsons-sea-cucumber",  "name": "Johnson's Sea Cucumber",  "family": "Invertebrate", "uid": 18},
    {"id": "deep-sea-coral",         "name": "Deep Sea Coral",          "family": "Coral",        "uid": 20},
    {"id": "orange-tube-sponge",     "name": "Orange Tube Sponge",      "family": "Invertebrate", "uid": 22},
    {"id": "elk-horn-coral",         "name": "Elk Horn Coral",          "family": "Coral",        "uid": 24},
    {"id": "peruvian-pelican",       "name": "Peruvian Pelican",        "family": "Bird",         "uid": 25},
    {"id": "red-beaded-anemone",     "name": "Red Beaded Anemone",      "family": "Invertebrate", "uid": 26},
    {"id": "mandarin-goby",          "name": "Mandarin Goby",           "family": "Crosscurrent", "uid": 28},
    {"id": "cleaner-wrasse",         "name": "Cleaner Wrasse",          "family": "Crosscurrent", "uid": 30},
    {"id": "king-crab",              "name": "King Crab",               "family": "Crustacean",   "uid": 32},
    {"id": "great-albatross",        "name": "Great Albatross",         "family": "Bird",         "uid": 33},
    {"id": "osprey",                 "name": "Osprey",                  "family": "Bird",         "uid": 41},
    {"id": "magnificent-frigatebird", "name": "Magnificent Frigatebird", "family": "Bird",        "uid": 49},
    {"id": "loggerhead-sea-turtle",  "name": "Loggerhead Sea Turtle",   "family": "Crosscurrent", "uid": 52},
    {"id": "mullet",                 "name": "Mullet",                  "family": "Baitfish",     "uid": 53},
    {"id": "bunker",                 "name": "Bunker",                  "family": "Baitfish",     "uid": 61},
    {"id": "grooved-brain-coral",    "name": "Grooved Brain Coral",     "family": "Coral",        "uid": 64},
    {"id": "sardine",                "name": "Sardine",                 "family": "Baitfish",     "uid": 69},
    {"id": "sea-urchin",             "name": "Sea Urchin",              "family": "Invertebrate", "uid": 70},
    {"id": "flying-fish",            "name": "Flying Fish",             "family": "Baitfish",     "uid": 77},
    {"id": "bonito",                 "name": "Bonito",                  "family": "Baitfish",     "uid": 85},
    {"id": "razorbill-auk",          "name": "Razorbill Auk",           "family": "Bird",         "uid": 93},
]
_SKIN_ANIMAL_BY_ID = {a["id"]: a for a in SKIN_ANIMALS}
_SKIN_STYLE_BY_ID = {s["id"]: s for s in SKIN_STYLES}

# ── Avatar relock table ─────────────────────────────────────────────────────
# Mirrors ANIMAL_AVATARS in js/preview-app.js. An avatar RELOCKS on Prestige
# when it was earned by playing; it is kept forever when it was bought, given
# for a donation, or won on the competitive ladder (which itself never resets).
KEEP_FOREVER_UNLOCK_TYPES = frozenset({"starter", "shop", "code", "rank"})
RELOCKABLE_UNLOCK_TYPES = frozenset({"level", "comp_wins", "stat", "achievement", "event", "secret"})

AVATAR_UNLOCK_TYPES: Dict[str, str] = {
    # ── Bait Fish ──
    "/avatars/mullet.png":                  "starter",
    "/avatars/bunker.png":                  "comp_wins",
    "/avatars/sardine.png":                 "secret",
    "/avatars/flying-fish.png":             "event",
    "/avatars/bonito.png":                  "event",
    # ── Birds ──
    "/avatars/emperor-penguin.png":         "rank",
    "/avatars/horned-puffin.png":           "stat",
    "/avatars/california-gull.png":         "achievement",
    "/avatars/peruvian-pelican.png":        "stat",
    "/avatars/great-albatross.png":         "level",
    "/avatars/osprey.png":                  "event",
    "/avatars/magnificent-frigatebird.png": "event",
    "/avatars/razorbill-auk.png":           "event",
    # ── Game Fish ──
    "/avatars/mahi-mahi.png":               "level",
    "/avatars/blue-marlin.png":             "level",
    "/avatars/yellowfin-tuna.png":          "event",
    "/avatars/roosterfish.png":             "stat",
    "/avatars/king-salmon.png":             "event",
    "/avatars/tarpon.png":                  "event",
    "/avatars/barracuda.png":               "rank",
    "/avatars/sailfish.png":                "event",
    "/avatars/goliath-grouper.png":         "rank",
    # ── Coral ──
    "/avatars/staghorn-coral.png":          "achievement",
    "/avatars/deep-sea-coral.png":          "event",
    "/avatars/grooved-brain-coral.png":     "event",
    "/avatars/elkhorn-coral.png":           "achievement",
    # ── Mammals ──
    "/avatars/spinner-dolphin.png":         "achievement",
    "/avatars/bottlenose-dolphin.png":      "rank",
    "/avatars/narwhal.png":                 "achievement",
    # ── Invertebrates ──
    "/avatars/sea-sponge.png":              "level",
    "/avatars/sea-urchin.png":              "event",
    "/avatars/sea-star.png":                "level",
    "/avatars/sea-cucumber.png":            "event",
    "/avatars/sea-anemone.png":             "achievement",
    # ── Crosscurrent ──
    "/avatars/big-eye-tuna.png":            "event",
    "/avatars/cleaner-wrasse.png":          "achievement",
    "/avatars/mandarin-goby.png":           "event",
    "/avatars/loggerhead-sea-turtle.png":   "event",
    "/avatars/blue-tang.png":               "level",
    "/avatars/clownfish.png":               "event",
    "/avatars/great-white-shark.png":       "level",
    "/avatars/manta-ray.png":               "level",
    "/avatars/reef-triggerfish.png":        "achievement",
    "/avatars/whale-shark.png":             "event",
    # ── Crustaceans ──
    "/avatars/mantis-shrimp.png":           "achievement",
    "/avatars/spiny-lobster.png":           "rank",
    "/avatars/lobster.png":                 "event",
    "/avatars/king-crab.png":               "level",
    "/avatars/hermit-crab.png":             "event",
    # ── Cephalopods ──
    "/avatars/bobtail-squid.png":           "secret",
    "/avatars/common-octopus.png":          "secret",
    "/avatars/cuttlefish.png":              "secret",
    "/avatars/giant-squid.png":             "rank",
    # ── Exclusive Avatars ──
    "/avatars/amberjack.png":               "code",
    "/avatars/fish.png":                    "code",
    # ── Summer Skins ──
    "/avatars/summer-skin-gull.png":        "shop",
    "/avatars/summer-skin-hermit-crab.png": "shop",
    "/avatars/summer-skin-goby.png":        "shop",
    # ── Fourth of July Skins ──
    "/avatars/fourth-of-july.png":          "shop",
}


# ═══════════════════════════════════════════════════════════════════════════
#  READABILITY + IMPERSONATION GUARDS FOR CUSTOM COLOURS
# ═══════════════════════════════════════════════════════════════════════════
# The game draws usernames on a light surface (Player Home, leaderboards) and a
# dark one (in-game seats, chat). Where a colour is low-contrast on the surface
# it landed on, the client seats it on a READABILITY PLATE whose polarity is
# chosen from the colour's own luminance — a light name gets a dark plate, a
# dark name gets a light one (see .cc-pname.plate in css/prestige.css).
#
# ⚠️ What that means for this gate, stated plainly so nobody re-tunes it into
# something it cannot be: once the plate polarity is correct, the WORST
# achievable contrast over any colour is ~4.25:1 — better than WCAG AA — and it
# occurs at mid luminance (L≈0.197, which is where the game's own Ocean Blue
# sits). So a luminance gate set anywhere below 4.25 can never reject anything,
# and anywhere above it starts rejecting the game's own palette. The floor below
# is therefore a DEFENSIVE assertion of that invariant, not a filter that fires
# in normal use: it guarantees "every colour we ever store renders at ≥ AA once
# plated". The guard that does the real day-to-day work is the reserved-colour
# list underneath it.
LIGHT_SURFACE = (0xF4, 0xFB, 0xFF)
DARK_SURFACE = (0x0C, 0x2A, 0x44)
# The two plates, matching css/prestige.css: near-white and near-black.
LIGHT_PLATE = (0xFF, 0xFF, 0xFF)
DARK_PLATE = (0x04, 0x16, 0x28)
MIN_CONTRAST = 4.0        # WCAG AA is 4.5 for body text; names are bold ≥14px

# Colours reserved so a player cannot dress their name up as staff or as a
# system message. Anything within RESERVED_DISTANCE of one of these is refused.
RESERVED_COLORS = [
    ("#e02020", "moderation red"),
    ("#ff3b30", "error red"),
    ("#ff8800", "system alert orange"),
    ("#d81b60", "administrator magenta"),
]
RESERVED_DISTANCE = 42.0   # euclidean RGB distance


def _hex_to_rgb(value: str) -> Optional[Tuple[int, int, int]]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(value or "").strip())
    if not m:
        return None
    h = m.group(1)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rel_luminance(rgb: Tuple[int, int, int]) -> float:
    def ch(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> float:
    la, lb = _rel_luminance(a), _rel_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def best_plated_contrast(rgb: Tuple[int, int, int]) -> float:
    """The contrast the client will actually render at, given it may seat the
    name on whichever plate suits the colour. This — not the bare surface — is
    what the readability floor is checked against."""
    return max(_contrast(rgb, LIGHT_PLATE), _contrast(rgb, DARK_PLATE),
               _contrast(rgb, LIGHT_SURFACE), _contrast(rgb, DARK_SURFACE))


def validate_custom_color(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """(normalised '#rrggbb', None) or (None, error). The one gate every custom
    username colour passes through — the creator, the gradient editor, and any
    later feature that lets a player type a hex."""
    rgb = _hex_to_rgb(value)
    if rgb is None:
        return None, "bad_color"
    if best_plated_contrast(rgb) < MIN_CONTRAST:
        return None, "color_unreadable"
    for reserved, _label in RESERVED_COLORS:
        r2 = _hex_to_rgb(reserved) or (0, 0, 0)
        dist = math.sqrt(sum((rgb[i] - r2[i]) ** 2 for i in range(3)))
        if dist < RESERVED_DISTANCE:
            return None, "color_reserved"
    return "#%02x%02x%02x" % rgb, None


# ═══════════════════════════════════════════════════════════════════════════
#  SMALL HELPERS
# ═══════════════════════════════════════════════════════════════════════════
def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _now() -> int:
    return int(time.time())


def _iso(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _users(db):
    return db.collection("users")


def _ledger(db):
    return db.collection("prestige_ledger")


def _admin_log(db):
    return db.collection("prestige_admin_log")


def _txn_helpers():
    from firebase_admin import firestore
    transactional = getattr(firestore, "transactional", None)
    if transactional is None:
        from google.cloud.firestore_v1 import transactional  # type: ignore
    return transactional


def _canon_icon(path: Any) -> str:
    """'/avatars/Mullet.PNG?v=2' → '/avatars/mullet.png'. Stored paths are
    already clean; this makes a client-submitted one safe to compare."""
    s = str(path or "").split("?")[0].strip().lower()
    if not s:
        return ""
    i = s.find("/avatars/")
    if i >= 0:
        s = s[i:]
    return s if s.startswith("/avatars/") else ""


def _auth_uid(body: Dict[str, Any]) -> Optional[str]:
    tok = body.get("idToken") if isinstance(body.get("idToken"), str) else ""
    claims = _verify_token(tok) if (tok and _verify_token) else None
    return claims.get("uid") if claims and claims.get("uid") else None


def _level_of(total_xp: Any) -> int:
    if _level_progress is None:
        return 1
    return int(_level_progress(total_xp)[0])


def _stored_total_xp(stats: Dict[str, Any]) -> int:
    """total_xp is the source of truth; fall back to the level table only for an
    old account that never stored one (mirrors the client's getStoredTotalXp)."""
    direct = stats.get("total_xp")
    if direct is not None:
        n = _int(direct, -1)
        if n >= 0:
            return n
    return 0


# ═══════════════════════════════════════════════════════════════════════════
#  THE PRESTIGE RECORD
# ═══════════════════════════════════════════════════════════════════════════
def _blank_record() -> Dict[str, Any]:
    return {
        "level": 0,
        "xp_multiplier": 1.0,
        "store_bonus_pct": 0,
        "last_prestige_at": 0,
        "kept_avatars": [],       # avatars carried through a Prestige, forever
        "backgrounds": [],        # unlocked prestige background ids
        "skins": [],              # [{animal, style, at}]
        "colors": [],             # unlocked solid colour ids
        "gradients": [],          # unlocked gradient ids
        "effects": [],            # unlocked animated effect ids
        "custom_color": False,
        "custom_gradient": False,
        "three_color": False,
        "appearance": {"mode": "default", "effect": "none", "animate": True,
                       "background": "", "skin": ""},
        "history": [],
    }


def _record_of(udoc: Dict[str, Any]) -> Dict[str, Any]:
    rec = udoc.get("prestige") if isinstance(udoc.get("prestige"), dict) else {}
    out = _blank_record()
    for k, v in (rec or {}).items():
        out[k] = v
    out["level"] = max(0, _int(out.get("level")))
    for key in ("kept_avatars", "backgrounds", "colors", "gradients", "effects", "skins", "history"):
        if not isinstance(out.get(key), list):
            out[key] = []
    if not isinstance(out.get("appearance"), dict):
        out["appearance"] = _blank_record()["appearance"]
    # These two are DERIVED, never trusted from storage — an old doc written
    # before a formula change, or hand-edited, still reports the right numbers.
    out["xp_multiplier"] = xp_multiplier_for(out["level"])
    out["store_bonus_pct"] = store_bonus_pct_for(out["level"])
    return out


def _unlocks_at(level: int) -> Dict[str, Any]:
    """Everything level `level` hands over, as the payload the UI previews and
    the commit actually writes. `color_choice` is the one thing the player picks
    (Prestige 1 only); every other colour is automatic."""
    colors_auto: List[str] = []
    color_choice: List[str] = []
    for c in NAME_COLORS:
        if c["level"] != level or c["id"] == "default":
            continue
        (color_choice if c.get("choice") else colors_auto).append(c["id"])
    # Prestige 2 also hands back whichever Prestige-1 colour was not taken —
    # resolved at commit time against what the account already owns.
    return {
        "prestige": level,
        "coins": coin_reward_for(level),
        "xp_multiplier": xp_multiplier_for(level),
        "xp_bonus_pct": int(round(PRESTIGE_XP_STEP * 100)) * level,
        "store_bonus_pct": store_bonus_pct_for(level),
        "background": background_for_level(level),
        "badge": badge_for_level(level),
        "title": title_for_level(level),
        "colors": colors_auto,
        "color_choice": color_choice,
        "gradients": [g["id"] for g in NAME_GRADIENTS if g["level"] == level],
        "effects": [e["id"] for e in NAME_EFFECTS if e["level"] == level and e["id"] != "none"],
        "custom_color": level == CUSTOM_COLOR_LEVEL,
        "custom_gradient": level == GRADIENT_LEVEL,
        "three_color": level == THREE_COLOR_LEVEL,
        "skin_styles": [s["id"] for s in SKIN_STYLES if s["level"] <= level],
        "keep_avatars": PRESTIGE_KEEP_AVATARS,
    }


def _public_appearance(rec: Dict[str, Any], nickname: str = "") -> Dict[str, Any]:
    """The ONLY prestige data that leaves the server for other players to see:
    level, badge, title, XP bonus, when they last prestiged, and how the name is
    drawn. No coins, no history, no account internals."""
    lvl = max(0, _int(rec.get("level")))
    app = rec.get("appearance") if isinstance(rec.get("appearance"), dict) else {}
    return {
        "nickname": nickname,
        "level": lvl,
        "badge": badge_for_level(lvl),
        "title": title_for_level(lvl),
        "xp_bonus_pct": int(round(PRESTIGE_XP_STEP * 100)) * lvl,
        "last_prestige_at": _int(rec.get("last_prestige_at")),
        "name": {
            "mode": str(app.get("mode") or "default"),
            "color": str(app.get("color") or ""),
            "colorId": str(app.get("colorId") or ""),
            "gradientId": str(app.get("gradientId") or ""),
            "from": str(app.get("from") or ""),
            "to": str(app.get("to") or ""),
            "mid": str(app.get("mid") or ""),
            "dir": str(app.get("dir") or "h"),
            "style": str(app.get("style") or "smooth"),
            "effect": str(app.get("effect") or "none"),
            "animate": bool(app.get("animate", True)),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
#  AVATAR SPLIT — what relocks, what stays, what can be chosen
# ═══════════════════════════════════════════════════════════════════════════
def split_avatars(unlocked: Any, rec: Dict[str, Any]) -> Dict[str, List[str]]:
    """{'eligible', 'automatic', 'unknown'} for an account's unlocked_icons.

    eligible  — earned by playing, would relock, so it can be one of the two
                the player chooses to carry over.
    automatic — bought / donated / competitive-rank / starter / already carried
                over by an earlier Prestige. Stays without being chosen, and
                MUST NOT be selectable (choosing one would waste a slot).
    unknown   — a path this server has never heard of. Treated as automatic:
                when in doubt, do not take something away from a player.
    """
    eligible: List[str] = []
    automatic: List[str] = []
    unknown: List[str] = []
    kept = {_canon_icon(p) for p in (rec.get("kept_avatars") or [])}
    seen = set()
    for raw in (unlocked if isinstance(unlocked, list) else []):
        path = _canon_icon(raw)
        if not path or path in seen:
            continue
        seen.add(path)
        kind = AVATAR_UNLOCK_TYPES.get(path)
        if kind is None:
            unknown.append(path)
        elif kind in KEEP_FOREVER_UNLOCK_TYPES or path in kept:
            automatic.append(path)
        elif kind in RELOCKABLE_UNLOCK_TYPES:
            eligible.append(path)
        else:
            unknown.append(path)
    return {"eligible": sorted(eligible), "automatic": sorted(automatic),
            "unknown": sorted(unknown)}


# ═══════════════════════════════════════════════════════════════════════════
#  STATE / PREVIEW
# ═══════════════════════════════════════════════════════════════════════════
def _state_payload(uid: str, udoc: Dict[str, Any]) -> Dict[str, Any]:
    stats = udoc.get("stats") if isinstance(udoc.get("stats"), dict) else {}
    rec = _record_of(udoc)
    total_xp = _stored_total_xp(stats)
    level, xp_into, xp_goal = (_level_progress(total_xp) if _level_progress else (1, 0, 1))
    can = level >= _max_level
    next_level = rec["level"] + 1
    split = split_avatars(udoc.get("unlocked_icons"), rec)
    owned_skins = {(str(s.get("animal")), str(s.get("style")))
                   for s in rec["skins"] if isinstance(s, dict)}
    return {
        "ok": True,
        "uid": uid,
        "nickname": str(udoc.get("nickname") or ""),
        "level": level,
        "max_level": _max_level,
        "total_xp": total_xp,
        "xp_into_level": xp_into,
        "xp_goal": xp_goal,
        "xp_to_max": max(0, _xp_needed_for_max() - total_xp),
        "can_prestige": can,
        "prestige": {
            "level": rec["level"],
            "title": title_for_level(rec["level"]),
            "badge": badge_for_level(rec["level"]),
            "xp_multiplier": rec["xp_multiplier"],
            "xp_bonus_pct": int(round(PRESTIGE_XP_STEP * 100)) * rec["level"],
            "store_bonus_pct": rec["store_bonus_pct"],
            "backgrounds": list(rec["backgrounds"]),
            "skins": list(rec["skins"]),
            "colors": list(rec["colors"]),
            "gradients": list(rec["gradients"]),
            "effects": list(rec["effects"]),
            "custom_color": bool(rec["custom_color"]),
            "custom_gradient": bool(rec["custom_gradient"]),
            "three_color": bool(rec["three_color"]),
            "appearance": rec["appearance"],
            "kept_avatars": list(rec["kept_avatars"]),
            "last_prestige_at": _int(rec["last_prestige_at"]),
        },
        "next": _unlocks_at(next_level) if next_level <= MAX_PRESTIGE_LEVEL else None,
        "avatars": split,
        # What the wizard must actually collect — 2, or fewer when the player
        # has fewer relockable critters than that (see keep_quota).
        "keep_quota": keep_quota(split),
        "owned_skins": [{"animal": a, "style": s} for (a, s) in sorted(owned_skins)],
        "coins": max(0, _int(stats.get("critter_coins"))),
        "history": list(rec["history"])[-50:],
        "catalog_version": CATALOG_VERSION,
    }


_XP_FOR_MAX_CACHE: Dict[str, int] = {}


def _xp_needed_for_max() -> int:
    """Total XP that reaches the level cap. Derived from the injected level
    function by binary search so this module never keeps its own copy of the
    curve (the one table that has bitten this codebase before)."""
    if "v" in _XP_FOR_MAX_CACHE:
        return _XP_FOR_MAX_CACHE["v"]
    if _level_progress is None:
        return 0
    lo, hi = 0, 1
    while _level_of(hi) < _max_level and hi < 1 << 40:
        hi *= 2
    while lo < hi:
        mid = (lo + hi) // 2
        if _level_of(mid) >= _max_level:
            hi = mid
        else:
            lo = mid + 1
    _XP_FOR_MAX_CACHE["v"] = lo
    return lo


# ═══════════════════════════════════════════════════════════════════════════
#  SELECTION VALIDATION — every choice is re-checked against SERVER data
# ═══════════════════════════════════════════════════════════════════════════
def keep_quota(split: Dict[str, List[str]]) -> int:
    """How many critters this player must choose to keep.

    Two, normally — but never more than they actually have to choose FROM.
    Prestige straight after a Prestige (or an account that only ever bought its
    critters) can have 0 or 1 relockable ones, and demanding two there would
    make Prestige permanently impossible for them: nothing they could do in the
    UI would ever satisfy a requirement with no valid answer.
    """
    return min(PRESTIGE_KEEP_AVATARS, len(split.get("eligible") or []))


def _validate_keep_avatars(raw: Any, split: Dict[str, List[str]]) -> Tuple[List[str], Optional[str]]:
    need = keep_quota(split)
    if not isinstance(raw, list):
        # An empty selection is the CORRECT answer when there is nothing to pick.
        if need == 0:
            return [], None
        return [], "avatars_required"
    picks: List[str] = []
    for item in raw[:8]:
        path = _canon_icon(item)
        if path and path not in picks:
            picks.append(path)
    if len(picks) != need:
        return [], "avatars_count"
    eligible = set(split["eligible"])
    automatic = set(split["automatic"])
    for p in picks:
        if p in automatic:
            return [], "avatar_already_kept"
        if p not in eligible:
            return [], "avatar_not_owned"
    return picks, None


def _validate_skin(raw: Any, level: int, owned: set) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(raw, dict):
        return None, "skin_required"
    animal = str(raw.get("animal") or "").strip().lower()
    style = str(raw.get("style") or "").strip().lower()
    if animal not in _SKIN_ANIMAL_BY_ID:
        return None, "skin_unknown_animal"
    st = _SKIN_STYLE_BY_ID.get(style)
    if st is None:
        return None, "skin_unknown_style"
    if st["level"] > level:
        return None, "skin_style_locked"
    if (animal, style) in owned:
        return None, "skin_already_owned"
    return {"animal": animal, "style": style, "at": _now()}, None


def _validate_color_choice(raw: Any, level: int, owned: List[str]) -> Tuple[List[str], Optional[str]]:
    """The colours this Prestige adds. Prestige 1 requires a pick between two;
    every other level's colours are automatic, and Prestige 2 also hands back
    whichever Prestige-1 colour was not chosen."""
    unlocks = _unlocks_at(level)
    granted = list(unlocks["colors"])
    choices = unlocks["color_choice"]
    if choices:
        pick = str((raw or {}).get("color") if isinstance(raw, dict) else raw or "").strip().lower()
        if pick not in choices:
            return [], "color_choice_required"
        granted.append(pick)
    # Backfill anything from a STRICTLY EARLIER level the account never
    # received — which is how the Prestige-1 colour the player didn't pick
    # arrives at Prestige 2.
    #
    # ⚠️ `c["level"] < level`, NOT `<= level`. With `<=` this loop handed over
    # the OTHER Prestige-1 colour in the same breath as the one the player
    # chose, so the "pick one, bank the other" reward paid out both at once and
    # Prestige 2's colour reward was already spent.
    have = set(owned) | set(granted)
    for c in NAME_COLORS:
        if c["id"] == "default" or c["level"] >= level or c["id"] in have:
            continue
        granted.append(c["id"])
        have.add(c["id"])
    return granted, None


# ═══════════════════════════════════════════════════════════════════════════
#  COMMIT — the one atomic transaction
# ═══════════════════════════════════════════════════════════════════════════
def _idem_id(uid: str, key: str) -> str:
    digest = hashlib.sha256(f"{uid}:{key}".encode("utf-8")).hexdigest()[:32]
    return f"idem_{uid}_{digest}"


def _commit(db, uid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Run the whole Prestige, or change nothing at all.

    Every value the player could have tampered with (their level, what they own,
    the coin reward, the multiplier) is re-read INSIDE the transaction from the
    account document. The request body only ever supplies CHOICES, and each
    choice is checked against that same server-side data before anything moves.
    """
    if str(body.get("confirm") or "").strip().upper() != CONFIRM_PHRASE:
        return {"ok": False, "error": "confirm_required"}

    idem_key = str(body.get("idempotency_key") or "").strip()[:80]
    if not idem_key:
        return {"ok": False, "error": "idempotency_required"}

    transactional = _txn_helpers()
    user_ref = _users(db).document(uid)
    idem_ref = _ledger(db).document(_idem_id(uid, idem_key))
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        # ── replay guard: the same key always returns the same answer ──────
        prev = idem_ref.get(transaction=t)
        if prev.exists:
            stored = prev.to_dict() or {}
            result = stored.get("result")
            if isinstance(result, dict):
                return {**result, "replayed": True}
            return {"ok": False, "error": "in_progress"}

        snap = user_ref.get(transaction=t)
        if not snap.exists:
            return {"ok": False, "error": "no_account"}
        udoc = snap.to_dict() or {}
        stats = udoc.get("stats") if isinstance(udoc.get("stats"), dict) else {}
        rec = _record_of(udoc)

        # ── gate 1: the account really is at the cap ───────────────────────
        total_xp = _stored_total_xp(stats)
        level = _level_of(total_xp)
        if level < _max_level:
            return {"ok": False, "error": "not_max_level",
                    "level": level, "max_level": _max_level}

        new_level = rec["level"] + 1
        if new_level > MAX_PRESTIGE_LEVEL:
            return {"ok": False, "error": "prestige_cap"}

        # ── gate 2: this exact prestige number has not already happened ────
        run_ref = _ledger(db).document(f"{uid}_{new_level}")
        if run_ref.get(transaction=t).exists:
            return {"ok": False, "error": "already_prestiged", "prestige": new_level}

        # ── gate 3: every selection, re-checked against the account ────────
        split = split_avatars(udoc.get("unlocked_icons"), rec)
        keep, err = _validate_keep_avatars(body.get("keep_avatars"), split)
        if err:
            return {"ok": False, "error": err,
                    "eligible": len(split["eligible"]), "need": keep_quota(split)}

        owned_skins = {(str(s.get("animal")), str(s.get("style")))
                       for s in rec["skins"] if isinstance(s, dict)}
        skin, err = _validate_skin(body.get("skin"), new_level, owned_skins)
        if err:
            return {"ok": False, "error": err}

        new_colors, err = _validate_color_choice(body.get("name_color"), new_level, rec["colors"])
        if err:
            return {"ok": False, "error": err,
                    "choices": _unlocks_at(new_level)["color_choice"]}

        # ── the rewards, computed here and nowhere else ────────────────────
        unlocks = _unlocks_at(new_level)
        coins_award = coin_reward_for(new_level)
        bg = background_for_level(new_level)
        badge = badge_for_level(new_level)

        # ── what is kept ───────────────────────────────────────────────────
        kept_forever = sorted(set(rec["kept_avatars"]) | set(keep))
        surviving = sorted(set(split["automatic"]) | set(split["unknown"]) | set(keep))
        relocked = sorted(set(split["eligible"]) - set(keep))

        # An equipped avatar that just relocked would leave the player wearing
        # something they no longer own, so fall back to the starter icon.
        equipped = _canon_icon(udoc.get("avatar_url"))
        next_avatar = udoc.get("avatar_url")
        if equipped and equipped in set(relocked):
            next_avatar = "/avatars/mullet.png"

        # ── the new record ─────────────────────────────────────────────────
        new_rec = dict(rec)
        new_rec["level"] = new_level
        new_rec["xp_multiplier"] = xp_multiplier_for(new_level)
        new_rec["store_bonus_pct"] = store_bonus_pct_for(new_level)
        new_rec["last_prestige_at"] = _now()
        new_rec["kept_avatars"] = kept_forever
        new_rec["backgrounds"] = sorted(set(rec["backgrounds"]) | {bg["id"]})
        new_rec["skins"] = list(rec["skins"]) + [skin]
        new_rec["colors"] = sorted(set(rec["colors"]) | set(new_colors))
        new_rec["gradients"] = sorted(set(rec["gradients"]) | set(unlocks["gradients"]))
        new_rec["effects"] = sorted(set(rec["effects"]) | set(unlocks["effects"]))
        new_rec["custom_color"] = bool(rec["custom_color"] or new_level >= CUSTOM_COLOR_LEVEL)
        new_rec["custom_gradient"] = bool(rec["custom_gradient"] or new_level >= GRADIENT_LEVEL)
        new_rec["three_color"] = bool(rec["three_color"] or new_level >= THREE_COLOR_LEVEL)

        entry = {
            "prestige": new_level,
            "at": new_rec["last_prestige_at"],
            "level_before": level,
            "xp_before": total_xp,
            "coins": coins_award,
            "avatars_kept": keep,
            "avatars_relocked": len(relocked),
            "skin": skin,
            "background": {"id": bg["id"], "name": bg["name"]},
            "colors": new_colors,
            "badge": {"id": badge["id"], "name": badge["name"]} if badge else None,
            "xp_multiplier": new_rec["xp_multiplier"],
            "store_bonus_pct": new_rec["store_bonus_pct"],
            "title": title_for_level(new_level),
        }
        new_rec["history"] = (list(rec["history"]) + [entry])[-100:]

        # ── the reset + the rewards, as ONE write ──────────────────────────
        # Everything not named here is untouched: competitive rank and CP, clan
        # membership/role/points/stats, friends, messages, lifetime stats, match
        # history, achievements, purchased avatars/backgrounds/cosmetics,
        # supporter rewards, settings, moderation records.
        lvl1, into1, goal1 = (_level_progress(0) if _level_progress else (1, 0, 1))
        stats_update = {
            "total_xp": 0,
            "level": lvl1,
            "player_level": lvl1,
            "xp_current": into1,
            "level_xp_current": into1,
            "xp_goal": goal1,
            "level_xp_goal": goal1,
            "critter_coins": max(0, _int(stats.get("critter_coins"))) + coins_award,
            "prestige_level": new_level,
            "prestige_xp_multiplier": new_rec["xp_multiplier"],
            "prestige_store_bonus_pct": new_rec["store_bonus_pct"],
        }
        updates: Dict[str, Any] = {
            "prestige": new_rec,
            "unlocked_icons": surviving,
            "stats": stats_update,
        }
        if next_avatar != udoc.get("avatar_url"):
            updates["avatar_url"] = next_avatar

        t.set(user_ref, updates, merge=True)

        # The ledger doc IS the "this happened once" guarantee AND the admin log
        # — created inside the same transaction, so it cannot exist without the
        # account change and the account change cannot exist without it.
        t.create(run_ref, {
            "uid": uid,
            "username": str(udoc.get("nickname") or udoc.get("username") or ""),
            "old_prestige": rec["level"],
            "new_prestige": new_level,
            "level_before": level,
            "xp_before": total_xp,
            "at": entry["at"],
            "at_iso": _iso(entry["at"]),
            "coins_awarded": coins_award,
            "avatars_kept": keep,
            "avatars_relocked": relocked,
            "skin": skin,
            "background": bg["id"],
            "colors_unlocked": new_colors,
            "badge": badge["id"] if badge else "",
            "idempotency_key": idem_key,
            "result": "ok",
        })

        result = {
            "ok": True,
            "prestige": new_level,
            "title": title_for_level(new_level),
            "badge": badge,
            "coins_awarded": coins_award,
            "coins_total": stats_update["critter_coins"],
            "xp_multiplier": new_rec["xp_multiplier"],
            "xp_bonus_pct": int(round(PRESTIGE_XP_STEP * 100)) * new_level,
            "store_bonus_pct": new_rec["store_bonus_pct"],
            "background": bg,
            "skin": skin,
            "colors_unlocked": new_colors,
            "gradients_unlocked": unlocks["gradients"],
            "effects_unlocked": unlocks["effects"],
            "custom_color": new_rec["custom_color"],
            "custom_gradient": new_rec["custom_gradient"],
            "avatars_kept": keep,
            "avatars_relocked": relocked,
            "level": lvl1,
            "avatar_url": next_avatar,
        }
        # Replay record: a refresh or a second device replays THIS answer.
        t.create(idem_ref, {"uid": uid, "at": entry["at"], "prestige": new_level,
                            "result": result})
        return result

    return _run(txn)


# ═══════════════════════════════════════════════════════════════════════════
#  APPEARANCE — equipping what a Prestige unlocked (never grants anything)
# ═══════════════════════════════════════════════════════════════════════════
def _validate_appearance(raw: Any, rec: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(raw, dict):
        return None, "bad_request"
    lvl = rec["level"]
    mode = str(raw.get("mode") or "default").strip().lower()
    out: Dict[str, Any] = {"mode": "default", "effect": "none", "animate": True,
                           "background": "", "skin": ""}

    if mode == "solid":
        cid = str(raw.get("colorId") or "").strip().lower()
        if cid not in set(rec["colors"]):
            return None, "color_locked"
        entry = next((c for c in NAME_COLORS if c["id"] == cid), None)
        if entry is None:
            return None, "color_locked"
        out["mode"] = "solid"
        out["colorId"] = cid
        out["color"] = entry["hex"]
    elif mode == "custom":
        if not rec["custom_color"]:
            return None, "custom_color_locked"
        color, err = validate_custom_color(raw.get("color"))
        if err:
            return None, err
        out["mode"] = "custom"
        out["color"] = color
        out["colorId"] = ""
    elif mode == "gradient":
        if not rec["custom_gradient"]:
            return None, "gradient_locked"
        gid = str(raw.get("gradientId") or "").strip().lower()
        preset = next((g for g in NAME_GRADIENTS if g["id"] == gid), None)
        if preset is not None:
            if gid not in set(rec["gradients"]):
                return None, "gradient_locked"
            c_from, c_to = preset["from"], preset["to"]
            c_mid = ""
        else:
            # custom gradient: both stops go through the same readability gate
            c_from, err = validate_custom_color(raw.get("from"))
            if err:
                return None, err
            c_to, err = validate_custom_color(raw.get("to"))
            if err:
                return None, err
            c_mid = ""
            if raw.get("mid"):
                if not rec["three_color"]:
                    return None, "three_color_locked"
                c_mid, err = validate_custom_color(raw.get("mid"))
                if err:
                    return None, err
            gid = "custom"
        direction = str(raw.get("dir") or "h").strip().lower()
        style = str(raw.get("style") or "smooth").strip().lower()
        out["mode"] = "gradient"
        out["gradientId"] = gid
        out["from"] = c_from
        out["to"] = c_to
        out["mid"] = c_mid
        out["dir"] = direction if direction in GRADIENT_DIRECTIONS else "h"
        out["style"] = style if style in GRADIENT_STYLES else "smooth"
    elif mode != "default":
        return None, "bad_request"

    effect = str(raw.get("effect") or "none").strip().lower()
    if effect != "none":
        if effect not in set(rec["effects"]):
            return None, "effect_locked"
        out["effect"] = effect
    out["animate"] = bool(raw.get("animate", True))

    bg = str(raw.get("background") or "").strip().lower()
    if bg:
        if bg not in set(rec["backgrounds"]):
            return None, "background_locked"
        out["background"] = bg

    skin = str(raw.get("skin") or "").strip().lower()
    if skin:
        owned = {f"{s.get('animal')}:{s.get('style')}" for s in rec["skins"] if isinstance(s, dict)}
        if skin not in owned:
            return None, "skin_locked"
        out["skin"] = skin
    # `skins_off` turns every owned skin off in-game without giving one up.
    out["skins_off"] = bool(raw.get("skins_off"))
    _ = lvl
    return out, None


def _set_appearance(db, uid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    ref = _users(db).document(uid)
    snap = ref.get()
    if not snap.exists:
        return {"ok": False, "error": "no_account"}
    rec = _record_of(snap.to_dict() or {})
    appearance, err = _validate_appearance(body.get("appearance"), rec)
    if err:
        return {"ok": False, "error": err}
    ref.set({"prestige": {"appearance": appearance}}, merge=True)
    return {"ok": True, "appearance": appearance}


# ═══════════════════════════════════════════════════════════════════════════
#  PUBLIC NAME LOOKUP (badges + name colours on other players)
# ═══════════════════════════════════════════════════════════════════════════
_NAMES_CACHE: Dict[str, Tuple[Dict[str, Any], float]] = {}
_NAMES_TTL = 120.0
_NAMES_LOCK = threading.Lock()


def _public_for_uid(db, uid: str) -> Dict[str, Any]:
    now = time.time()
    with _NAMES_LOCK:
        hit = _NAMES_CACHE.get(uid)
        if hit and now - hit[1] < _NAMES_TTL:
            return hit[0]
    try:
        snap = _users(db).document(uid).get()
        doc = snap.to_dict() or {} if snap.exists else {}
    except Exception as exc:  # noqa: BLE001
        print(f"[prestige] name lookup {uid} failed: {exc}")
        return {}
    out = _public_appearance(_record_of(doc), str(doc.get("nickname") or ""))
    with _NAMES_LOCK:
        _NAMES_CACHE[uid] = (out, now)
        if len(_NAMES_CACHE) > 4000:
            _NAMES_CACHE.clear()
    return out


def _invalidate_name(uid: str) -> None:
    with _NAMES_LOCK:
        _NAMES_CACHE.pop(uid, None)


def _has_appearance(row: Dict[str, Any]) -> bool:
    """Is there anything to draw? A Prestige-0 player with the default colour
    has no badge and no colour, so returning them would only make every caller
    check for emptiness itself."""
    return bool(row) and bool(row.get("level") or (row.get("name") or {}).get("mode") != "default")


# nickname.lower() → uid, so the in-game seats / end-game summary / tournament
# brackets (which only ever know a display NAME) can still show a badge. Names
# change rarely and this is public data either way.
_NAME_UID_CACHE: Dict[str, Tuple[Optional[str], float]] = {}
_NAME_UID_TTL = 600.0


def _uid_for_name(db, name: str) -> Optional[str]:
    key = str(name or "").strip().lower()
    if not key or _find_uid_by_username is None:
        return None
    now = time.time()
    with _NAMES_LOCK:
        hit = _NAME_UID_CACHE.get(key)
        if hit and now - hit[1] < _NAME_UID_TTL:
            return hit[0]
    try:
        uid = _find_uid_by_username(db, key)
    except Exception as exc:  # noqa: BLE001
        print(f"[prestige] username lookup {key!r} failed: {exc}")
        return None
    with _NAMES_LOCK:
        _NAME_UID_CACHE[key] = (uid, now)
        if len(_NAME_UID_CACHE) > 4000:
            _NAME_UID_CACHE.clear()
    return uid


def _names_payload(db, uids: Any, names: Any = None) -> Dict[str, Any]:
    """Public badge + name-colour for a batch of uids AND/OR display names.

    Public data only — level, badge, title, XP-bonus %, last-prestige date and
    how the name is drawn. Never coins, history, or anything else off the
    account (see _public_appearance).
    """
    out: Dict[str, Any] = {}
    seen = 0
    for raw in (uids if isinstance(uids, list) else []):
        uid = str(raw or "").strip()[:128]
        if not uid or uid in out:
            continue
        seen += 1
        if seen > 120:
            break
        row = _public_for_uid(db, uid)
        if _has_appearance(row):
            out[uid] = row

    by_name: Dict[str, Any] = {}
    seen = 0
    for raw in (names if isinstance(names, list) else []):
        nm = str(raw or "").strip()[:64]
        key = nm.lower()
        if not key or key in by_name:
            continue
        seen += 1
        if seen > 60:
            break
        uid = _uid_for_name(db, key)
        if not uid:
            continue
        row = out.get(uid) or _public_for_uid(db, uid)
        if _has_appearance(row):
            by_name[key] = row
    return {"ok": True, "players": out, "by_name": by_name}


# ═══════════════════════════════════════════════════════════════════════════
#  CATALOGUE (public, cacheable — no account data in it)
# ═══════════════════════════════════════════════════════════════════════════
CATALOG_VERSION = "1"


def catalog() -> Dict[str, Any]:
    return {
        "version": CATALOG_VERSION,
        "max_level": _max_level,
        "confirm_phrase": CONFIRM_PHRASE,
        "keep_avatars": PRESTIGE_KEEP_AVATARS,
        "coin_base": PRESTIGE_COIN_BASE,
        "coin_step": PRESTIGE_COIN_STEP,
        "xp_step_pct": int(round(PRESTIGE_XP_STEP * 100)),
        "store_step_pct": int(round(PRESTIGE_STORE_STEP * 100)),
        "badges": PRESTIGE_BADGES,
        "titles": PRESTIGE_TITLES,
        "backgrounds": PRESTIGE_BACKGROUNDS,
        "colors": NAME_COLORS,
        "gradients": NAME_GRADIENTS,
        "gradient_dirs": GRADIENT_DIRECTIONS,
        "gradient_styles": GRADIENT_STYLES,
        "effects": NAME_EFFECTS,
        "custom_color_level": CUSTOM_COLOR_LEVEL,
        "gradient_level": GRADIENT_LEVEL,
        "three_color_level": THREE_COLOR_LEVEL,
        "skin_styles": SKIN_STYLES,
        "skin_animals": SKIN_ANIMALS,
        "keep_forever_types": sorted(KEEP_FOREVER_UNLOCK_TYPES),
        "relockable_types": sorted(RELOCKABLE_UNLOCK_TYPES),
        "avatar_types": AVATAR_UNLOCK_TYPES,
        # Sample rows so the "what do I get next" preview never has to guess.
        "ladder": [
            {
                "prestige": n,
                "coins": coin_reward_for(n),
                "xp_bonus_pct": int(round(PRESTIGE_XP_STEP * 100)) * n,
                "store_bonus_pct": store_bonus_pct_for(n),
                "background": background_for_level(n),
                "badge": badge_for_level(n),
                "title": title_for_level(n),
            }
            for n in range(1, 11)
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN
# ═══════════════════════════════════════════════════════════════════════════
def _admin_history(db, uid: str) -> Dict[str, Any]:
    snap = _users(db).document(uid).get()
    if not snap.exists:
        return {"ok": False, "error": "no_account"}
    doc = snap.to_dict() or {}
    rec = _record_of(doc)
    runs: List[Dict[str, Any]] = []
    try:
        for d in _ledger(db).where("uid", "==", uid).limit(200).stream():
            row = d.to_dict() or {}
            row["id"] = d.id
            runs.append(row)
    except Exception as exc:  # noqa: BLE001
        print(f"[prestige] admin ledger read failed: {exc}")
    runs.sort(key=lambda r: _int(r.get("at")))
    return {
        "ok": True,
        "uid": uid,
        "username": str(doc.get("nickname") or ""),
        "prestige": rec["level"],
        "record": rec,
        "ledger": [r for r in runs if not str(r.get("id", "")).startswith("idem_")],
        "duplicate_attempts": [r for r in runs if str(r.get("id", "")).startswith("idem_")],
    }


def _admin_correct(db, uid: str, body: Dict[str, Any], actor: str) -> Dict[str, Any]:
    """Fix a cosmetic selection or restore a missing reward. Deliberately CANNOT
    change the Prestige level, the coin reward, or anything that would let an
    admin action double-pay — those go through _commit or not at all."""
    ref = _users(db).document(uid)
    snap = ref.get()
    if not snap.exists:
        return {"ok": False, "error": "no_account"}
    rec = _record_of(snap.to_dict() or {})
    action = str(body.get("action") or "").strip()
    changed: Dict[str, Any] = {}

    if action == "replace_skin":
        old = str(body.get("old") or "").strip().lower()
        animal = str(body.get("animal") or "").strip().lower()
        style = str(body.get("style") or "").strip().lower()
        if animal not in _SKIN_ANIMAL_BY_ID or style not in _SKIN_STYLE_BY_ID:
            return {"ok": False, "error": "skin_unknown"}
        skins = [s for s in rec["skins"] if isinstance(s, dict)]
        idx = next((i for i, s in enumerate(skins)
                    if f"{s.get('animal')}:{s.get('style')}" == old), -1)
        if idx < 0:
            return {"ok": False, "error": "skin_not_found"}
        skins[idx] = {"animal": animal, "style": style, "at": _now(), "corrected": True}
        changed["skins"] = skins
    elif action == "grant_background":
        bid = str(body.get("background") or "").strip().lower()
        known = {background_for_level(n)["id"] for n in range(1, rec["level"] + 1)}
        if bid not in known:
            return {"ok": False, "error": "background_unknown"}
        changed["backgrounds"] = sorted(set(rec["backgrounds"]) | {bid})
    elif action == "restore_rewards":
        # Re-derive every cosmetic the account's CURRENT prestige level earns
        # and union it in. Purely additive, so running it twice changes nothing.
        colors, gradients, effects, backgrounds = set(rec["colors"]), set(rec["gradients"]), set(rec["effects"]), set(rec["backgrounds"])
        for n in range(1, rec["level"] + 1):
            u = _unlocks_at(n)
            colors |= set(u["colors"])
            gradients |= set(u["gradients"])
            effects |= set(u["effects"])
            backgrounds.add(background_for_level(n)["id"])
        changed.update({
            "colors": sorted(colors), "gradients": sorted(gradients),
            "effects": sorted(effects), "backgrounds": sorted(backgrounds),
            "custom_color": rec["level"] >= CUSTOM_COLOR_LEVEL,
            "custom_gradient": rec["level"] >= GRADIENT_LEVEL,
            "three_color": rec["level"] >= THREE_COLOR_LEVEL,
        })
    elif action == "reset_appearance":
        changed["appearance"] = _blank_record()["appearance"]
    else:
        return {"ok": False, "error": "unknown_action"}

    ref.set({"prestige": changed}, merge=True)
    _invalidate_name(uid)
    _admin_log(db).document(f"{uid}_{_now()}_{secrets.token_hex(4)}").set({
        "uid": uid, "actor": actor or "admin", "action": action,
        "body": {k: v for k, v in body.items() if k not in ("admin_key", "idToken")},
        "changed": list(changed.keys()), "at": _now(), "at_iso": _iso(_now()),
    })
    return {"ok": True, "changed": list(changed.keys())}


def _admin_disable(db, body: Dict[str, Any]) -> Dict[str, Any]:
    """Temporarily switch a broken reward off for everyone. Stored in a control
    doc the state/commit routes read, so nothing has to be redeployed."""
    kind = str(body.get("kind") or "").strip()
    ident = str(body.get("id") or "").strip().lower()
    off = bool(body.get("disabled", True))
    if kind not in ("background", "skin_style", "color", "gradient", "effect"):
        return {"ok": False, "error": "unknown_kind"}
    ref = db.collection("prestige_control").document("disabled")
    snap = ref.get()
    cur = (snap.to_dict() or {}) if snap.exists else {}
    lst = set(cur.get(kind) or [])
    lst.add(ident) if off else lst.discard(ident)
    ref.set({kind: sorted(lst), "updated_at": _now()}, merge=True)
    _DISABLED_CACHE["at"] = 0.0
    return {"ok": True, "kind": kind, "disabled": sorted(lst)}


_DISABLED_CACHE: Dict[str, Any] = {"at": 0.0, "data": {}}


def _disabled(db) -> Dict[str, List[str]]:
    now = time.time()
    if now - float(_DISABLED_CACHE.get("at") or 0) < 60.0:
        return _DISABLED_CACHE["data"]
    data: Dict[str, List[str]] = {}
    try:
        snap = db.collection("prestige_control").document("disabled").get()
        if snap.exists:
            raw = snap.to_dict() or {}
            data = {k: list(v) for k, v in raw.items() if isinstance(v, list)}
    except Exception:  # noqa: BLE001
        data = {}
    _DISABLED_CACHE.update({"at": now, "data": data})
    return data


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP ROUTES
# ═══════════════════════════════════════════════════════════════════════════
def handle_get(handler, parsed) -> bool:
    """GET /api/prestige/catalog — public, no account data, safely cacheable."""
    if parsed.path != "/api/prestige/catalog":
        return False
    handler._send_json({"ok": True, "catalog": catalog()})
    return True


def _route(db, uid: str, action: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if action == "state":
        snap = _users(db).document(uid).get()
        if not snap.exists:
            return {"ok": False, "error": "no_account"}
        payload = _state_payload(uid, snap.to_dict() or {})
        payload["disabled"] = _disabled(db)
        return payload
    if action == "commit":
        out = _commit(db, uid, body)
        if out.get("ok"):
            _invalidate_name(uid)
        return out
    if action == "appearance":
        out = _set_appearance(db, uid, body)
        if out.get("ok"):
            _invalidate_name(uid)
        return out
    if action == "history":
        snap = _users(db).document(uid).get()
        if not snap.exists:
            return {"ok": False, "error": "no_account"}
        rec = _record_of(snap.to_dict() or {})
        return {"ok": True, "prestige": rec["level"], "history": rec["history"]}
    if action == "names":
        return _names_payload(db, body.get("uids"), body.get("names"))
    return {"ok": False, "error": "unknown_action"}


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    path = parsed.path
    if not path.startswith("/api/prestige/") and not path.startswith("/api/admin/prestige"):
        return False

    # ── admin ───────────────────────────────────────────────────────────────
    if path.startswith("/api/admin/prestige"):
        admin_key = body.get("admin_key") if isinstance(body.get("admin_key"), str) else ""
        env_key = os.environ.get("ADMIN_RECOVERY_KEY", "").strip()
        if not env_key or not secrets.compare_digest(admin_key, env_key):
            handler._send_json({"ok": False, "error": "unauthorized"}, status=403)
            return True
        db = _get_firestore() if _get_firestore else None
        if db is None:
            handler._send_json({"ok": False, "error": "firestore_unavailable"})
            return True
        uid = str(body.get("uid") or "").strip()
        try:
            if path == "/api/admin/prestige-history":
                handler._send_json(_admin_history(db, uid) if uid
                                   else {"ok": False, "error": "uid_required"})
            elif path == "/api/admin/prestige-correct":
                handler._send_json(_admin_correct(db, uid, body, str(body.get("actor") or ""))
                                   if uid else {"ok": False, "error": "uid_required"})
            elif path == "/api/admin/prestige-disable":
                handler._send_json(_admin_disable(db, body))
            elif path == "/api/admin/prestige-preview":
                handler._send_json({"ok": True, "catalog": catalog()})
            else:
                handler._send_json({"ok": False, "error": "unknown_action"}, status=404)
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"[prestige] admin {path} failed: {exc}\n{traceback.format_exc(limit=4)}")
            handler._send_json({"ok": False, "error": "server_error"})
        return True

    # ── player ──────────────────────────────────────────────────────────────
    action = path[len("/api/prestige/"):]
    uid = _auth_uid(body)
    if not uid:
        handler._send_json({"ok": False, "error": "unauthorized"}, status=401)
        return True
    db = _get_firestore() if _get_firestore else None
    if db is None:
        handler._send_json({"ok": False, "error": "firestore_unavailable"})
        return True
    try:
        handler._send_json(_route(db, uid, action, body))
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(f"[prestige] {action} failed for {uid}: {exc}\n{traceback.format_exc(limit=4)}")
        # Nothing leaks about the internals, and the player is told plainly that
        # their account was not touched — because a failed transaction never
        # writes any part of the reset.
        handler._send_json({"ok": False, "error": "server_error"})
    return True
