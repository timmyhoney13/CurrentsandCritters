#!/usr/bin/env python3
"""Snap & Score — physical-game board scanning + scoring companion backend.

Serves score.currentsandcritters.com (same Render service as the game; the
subdomain is routed by Host header in multiplayer_server.py). Two flows:

  • Score My Board (guest): photo → Claude vision detects the cards → the
    OFFICIAL scoring engine (fish.final_points) computes the score → shown to
    the player. No account, no rewards, nothing stored.

  • Multiplayer Score Session: one player creates a session (unique code),
    everyone joins (account or guest), each board is photographed + confirmed,
    every joined player approves the standings, then a server-side Firestore
    transaction awards XP / achievements / avatars / history EXACTLY ONCE via
    a rewardLedger with deterministic doc ids. The browser never writes
    rewards — every grant happens here with firebase-admin.

The AI only ever IDENTIFIES cards. Scores, placements, XP, achievements and
unlocks are always computed by the engine + the whitelists below, both for the
preview shown before approval and (recomputed from scratch) at finalization.

Wired into multiplayer_server.py via init() + handle_get()/handle_post().
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import threading
import time
import traceback
from dataclasses import replace as dataclass_replace
from http import HTTPStatus
from typing import Any, Callable, Dict, List, Optional, Tuple

import fish_game_all_in_one as fish

# ── injected by multiplayer_server.init() ───────────────────────────────────
_get_firestore: Callable[[], Any] = lambda: None
_verify_token: Callable[[str], Optional[dict]] = lambda tok: None
_level_progress: Callable[[Any], Tuple[int, int, int]] = lambda xp: (1, 0, 50)
_STATE_DIR = "."

# Photos arrive as base64 data URLs inside JSON; the game server's normal JSON
# cap (128KB) is far too small, so snap endpoints read their own body.
MAX_PHOTO_BODY_BYTES = 8 * 1024 * 1024
MAX_JSON_BODY_BYTES = 256 * 1024

SESSION_CODE_LEN = 6
SESSION_TTL_SEC = 12 * 3600          # unfinalized sessions expire after 12h
FINALIZED_KEEP_SEC = 24 * 3600       # keep finalized sessions readable for 24h
MAX_OCEANS_PER_BOARD = 14
MAX_CARDS_PER_SLOT = 12              # lobsters/yellowfin can legally stack

SYMBOLS = ["Triangle", "Square", "Heart", "Circle", "Diamond"]

# ── Anti-cheat configuration ────────────────────────────────────────────────
# Admin-configurable via Firestore doc meta/snap_score_config (cached 5 min).
# Everything here is a DEFAULT, not hardcoded policy: edit the doc to tune.
DEFAULT_CONFIG = {
    # Minimum minutes between two REWARDED physical games for one account.
    "min_minutes_between_rewarded": 25,
    # Expected minimum realistic game length by player count (minutes).
    "expected_min_game_minutes": {"2": 15, "3": 20, "4": 25, "5": 30, "6": 35, "7": 40, "8": 45},
    "max_rewarded_games_per_day": 6,
    # How many days finalized board thumbnails are kept for review.
    "image_retention_days": 90,
    # Hamming distance at or under which two dHashes count as near-duplicates.
    "near_duplicate_hamming": 8,
    # Risk thresholds: score < review → normal; < hold → review_recommended;
    # < block → rewards_held; >= block → submission_blocked.
    "risk_review_at": 25,
    "risk_hold_at": 50,
    "risk_block_at": 80,
}
_config_cache: Dict[str, Any] = {"at": 0.0, "cfg": None}
_CONFIG_TTL_SEC = 300.0


def init(get_firestore, verify_token, level_progress, state_dir: str) -> None:
    """Dependency injection from multiplayer_server (avoids circular import)."""
    global _get_firestore, _verify_token, _level_progress, _STATE_DIR
    _get_firestore = get_firestore
    _verify_token = verify_token
    _level_progress = level_progress
    _STATE_DIR = state_dir
    _load_sessions_from_disk()


def get_config() -> Dict[str, Any]:
    """Admin config: Firestore meta/snap_score_config over DEFAULT_CONFIG."""
    now = time.time()
    if _config_cache["cfg"] is not None and now - _config_cache["at"] < _CONFIG_TTL_SEC:
        return _config_cache["cfg"]
    cfg = dict(DEFAULT_CONFIG)
    try:
        db = _get_firestore()
        if db is not None:
            snap = db.collection("meta").document("snap_score_config").get()
            if snap.exists:
                data = snap.to_dict() or {}
                for k, v in data.items():
                    if k in cfg:
                        cfg[k] = v
    except Exception as exc:  # noqa: BLE001 — config fetch must never break scoring
        print(f"[snap] config fetch failed ({exc}); using defaults")
    _config_cache["cfg"] = cfg
    _config_cache["at"] = now
    return cfg


# ════════════════════════════════════════════════════════════════════════════
# Card roster (from the real card database — single source of truth)
# ════════════════════════════════════════════════════════════════════════════

_CARD_DB: Optional[Dict[int, Any]] = None
_ROSTER: Optional[Dict[str, Any]] = None
_ROSTER_LOCK = threading.Lock()


def _card_db() -> Dict[int, Any]:
    global _CARD_DB
    if _CARD_DB is None:
        with _ROSTER_LOCK:
            if _CARD_DB is None:
                _CARD_DB = fish.load_card_db()
    return _CARD_DB


def _is_end_game(cd) -> bool:
    return "end game" in str(cd.name or "").strip().lower()


def _is_ocean(cd) -> bool:
    return str(cd.species or "").strip().lower() == "ocean"


def _card_dir(cd) -> str:
    return str(cd.direction or "").strip().lower()


def build_roster() -> Dict[str, Any]:
    """Every physical card (minus END GAME) for the picker UI + vision enums."""
    global _ROSTER
    if _ROSTER is not None:
        return _ROSTER
    db = _card_db()
    cards = []
    ocean_names: set = set()
    animal_names: set = set()
    for uid in sorted(db.keys()):
        cd = db[uid]
        if _is_end_game(cd):
            continue
        kind = "ocean" if _is_ocean(cd) else _card_dir(cd)
        cards.append({
            "u": uid, "n": cd.name, "sp": cd.species, "d": kind,
            "sym": cd.symbol, "c": cd.cost, "t": cd.text,
        })
        (ocean_names if kind == "ocean" else animal_names).add(str(cd.name).strip())
    _ROSTER = {
        "cards": cards,
        "oceanNames": sorted(ocean_names),
        "animalNames": sorted(animal_names),
        "symbols": SYMBOLS,
        "version": 1,
    }
    return _ROSTER


def _game_fish_names() -> List[str]:
    db = _card_db()
    return sorted({str(cd.name).strip().lower() for cd in db.values()
                   if str(cd.species or "").strip().lower() == "game fish"})


# ════════════════════════════════════════════════════════════════════════════
# Board normalization: client uid lists → engine PlayerStates
# ════════════════════════════════════════════════════════════════════════════

class BoardError(ValueError):
    pass


def _side_matches(cd, side: str) -> bool:
    if side == "ocean":
        return _is_ocean(cd)
    return _card_dir(cd) == side


class _UidAllocator:
    """Maps requested card uids to UNIQUE uids across one whole game.

    The client sends real card-db uids. A physical table can never hold the
    same physical copy twice, but detection/edit mistakes can — and dict-keyed
    ocean slots require unique ocean uids anyway. Repeats are remapped to an
    unused same-name copy, or (when every real copy is used) to a minted clone
    (uid = serial*1000 + face, the same scheme the admin card minter uses, so
    the client art lookup uid%1000 keeps working).
    """

    def __init__(self):
        self.db = dict(_card_db())
        self.used: set = set()
        self.mint_serial = 9001
        self.warnings: List[str] = []

    def take(self, uid: int, side: str, player_name: str) -> Optional[int]:
        base = self.db.get(int(uid))
        if base is None:
            self.warnings.append(f"{player_name}: unknown card id {uid} was skipped")
            return None
        if _is_end_game(base):
            self.warnings.append(f"{player_name}: END GAME card can't be on a board; skipped")
            return None
        if not _side_matches(base, side):
            self.warnings.append(
                f"{player_name}: {base.name} is a {base.direction} card but was placed on "
                f"the {side} side — kept, but double-check the photo"
            )
        if uid not in self.used:
            self.used.add(uid)
            return int(uid)
        # Requested copy already on the table: another real copy of same name?
        for alt_uid in sorted(self.db.keys()):
            alt = self.db[alt_uid]
            if alt_uid in self.used or alt_uid >= 1_000_000:
                continue
            if str(alt.name).lower() == str(base.name).lower() and _side_matches(alt, side) == _side_matches(base, side):
                self.used.add(alt_uid)
                return alt_uid
        # All real copies used → mint a clone and flag it (possible double-count).
        minted = self.mint_serial * 1000 + (int(uid) % 1000)
        self.mint_serial += 1
        self.db[minted] = dataclass_replace(base, uid=minted)
        self.used.add(minted)
        self.warnings.append(
            f"{player_name}: more copies of {base.name} than exist in one deck — "
            f"extra copy counted, but check for a double-scan"
        )
        return minted


def normalize_boards(raw_boards: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Any, List[str]]:
    """Validate + de-duplicate uids for a whole table of boards.

    raw board: {"name": str, "oceans": [{"u": uid, "up": [uids], "down": [...],
    "left": [...], "right": [...]}]}
    Returns (normalized boards in the same shape, game card_db incl. mints,
    warnings).
    """
    alloc = _UidAllocator()
    out = []
    for b in raw_boards:
        name = str(b.get("name") or "Player").strip()[:24] or "Player"
        oceans_in = b.get("oceans")
        if not isinstance(oceans_in, list):
            raise BoardError(f"{name}: board must contain an oceans list")
        if len(oceans_in) > MAX_OCEANS_PER_BOARD:
            raise BoardError(f"{name}: too many oceans ({len(oceans_in)})")
        oceans_out = []
        for oc in oceans_in:
            if not isinstance(oc, dict):
                continue
            ou = alloc.take(oc.get("u", -1), "ocean", name)
            if ou is None:
                continue
            slots_out = {"u": ou}
            for side in ("up", "down", "left", "right"):
                uids = oc.get(side) or []
                if not isinstance(uids, list):
                    uids = []
                if len(uids) > MAX_CARDS_PER_SLOT:
                    raise BoardError(f"{name}: too many cards on one ocean side")
                kept = []
                for u in uids:
                    got = alloc.take(u, side, name)
                    if got is not None:
                        kept.append(got)
                slots_out[side] = kept
            oceans_out.append(slots_out)
        out.append({"name": name, "oceans": oceans_out})
    return out, alloc.db, alloc.warnings


def _build_gs(boards: List[Dict[str, Any]], game_db: Dict[int, Any]):
    players = []
    for b in boards:
        p = fish.PlayerState(name=b["name"])
        for oc in b["oceans"]:
            p.board_oceans.append(oc["u"])
            slots = fish.OceanSlots()
            slots.up = list(oc["up"])
            slots.down = list(oc["down"])
            slots.left = list(oc["left"])
            slots.right = list(oc["right"])
            p.ocean_slots[oc["u"]] = slots
        players.append(p)
    return fish.GameState(card_db=game_db, players=players, deck=[])


def _boards_without(boards, player_idx, ocean_u, card_u=None):
    """Copy of boards with one card (or one whole ocean) removed."""
    out = json.loads(json.dumps(boards))  # boards are plain JSON data
    oceans = out[player_idx]["oceans"]
    for i, oc in enumerate(oceans):
        if oc["u"] != ocean_u:
            continue
        if card_u is None:
            oceans.pop(i)
        else:
            for side in ("up", "down", "left", "right"):
                if card_u in oc[side]:
                    oc[side].remove(card_u)
                    break
        break
    return out


def score_boards(raw_boards: List[Dict[str, Any]], with_breakdown: bool = True) -> Dict[str, Any]:
    """Score a full table with the official engine. Pure — no side effects.

    Returns per-player: score, detected strategy, and a per-card breakdown
    (marginal points: how much the total drops if that card is removed —
    honest about synergies, which is why marginals may not sum to the total).
    """
    boards, game_db, warnings = normalize_boards(raw_boards)
    gs = _build_gs(boards, game_db)

    def total_for(bs, idx):
        g2 = _build_gs(bs, game_db)
        return int(fish.final_points(g2, g2.players[idx]))

    results = []
    for idx, b in enumerate(boards):
        p = gs.players[idx]
        score = int(fish.final_points(gs, p))
        try:
            strategy = str(fish.detect_player_strategy(gs, p) or "")
        except Exception:
            strategy = ""
        breakdown = []
        if with_breakdown:
            for oc in b["oceans"]:
                ocean_cd = game_db[oc["u"]]
                for side in ("up", "down", "left", "right"):
                    for u in oc[side]:
                        delta = score - total_for(_boards_without(boards, idx, oc["u"], u), idx)
                        breakdown.append({"u": u % 1000 if u >= 1_000_000 else u,
                                          "n": game_db[u].name, "kind": "animal",
                                          "ocean": ocean_cd.name, "pts": delta})
                delta = score - total_for(_boards_without(boards, idx, oc["u"]), idx)
                breakdown.append({"u": oc["u"] % 1000 if oc["u"] >= 1_000_000 else oc["u"],
                                  "n": ocean_cd.name, "kind": "ocean",
                                  "ocean": ocean_cd.name, "pts": delta})
        results.append({
            "name": b["name"], "score": score, "strategy": strategy,
            "breakdown": breakdown,
            "board": _board_view(b, game_db),
        })
    return {"players": results, "warnings": warnings}


def _board_view(board: Dict[str, Any], game_db: Dict[int, Any]) -> Dict[str, Any]:
    """Board echoed back with names + display-face uids for the client."""
    oceans = []
    for oc in board["oceans"]:
        entry = {"u": oc["u"] % 1000 if oc["u"] >= 1_000_000 else oc["u"],
                 "n": game_db[oc["u"]].name}
        for side in ("up", "down", "left", "right"):
            entry[side] = [{"u": u % 1000 if u >= 1_000_000 else u, "n": game_db[u].name,
                            "sym": game_db[u].symbol} for u in oc[side]]
        oceans.append(entry)
    return {"oceans": oceans}


# ── final-board facts used by achievements / avatar unlocks ────────────────

def _board_facts(board: Dict[str, Any], game_db: Dict[int, Any]) -> Dict[str, Any]:
    name_counts: Dict[str, int] = {}
    species: set = set()
    ocean_names: List[str] = []
    full_oceans = 0
    oceans_exactly_one = 0
    corals_on_coral_reef = 0
    animals = 0
    for oc in board["oceans"]:
        ocean_cd = game_db[oc["u"]]
        ocean_names.append(str(ocean_cd.name).strip().lower())
        side_counts = []
        ocean_animals = 0
        for side in ("up", "down", "left", "right"):
            side_counts.append(len(oc[side]))
            for u in oc[side]:
                cd = game_db[u]
                nm = str(cd.name).strip().lower()
                name_counts[nm] = name_counts.get(nm, 0) + 1
                sp = str(cd.species or "").strip().lower()
                if sp:
                    species.add(sp)
                animals += 1
                ocean_animals += 1
                if str(ocean_cd.name).strip().lower() == "coral reef" and sp == "coral":
                    corals_on_coral_reef += 1
        if all(c > 0 for c in side_counts):
            full_oceans += 1
        if ocean_animals == 1:
            oceans_exactly_one += 1
    return {
        "name_counts": name_counts,
        "distinct_species": len(species),
        "ocean_names": ocean_names,
        "distinct_ocean_types": len(set(ocean_names)),
        "ocean_count": len(ocean_names),
        "full_oceans": full_oceans,
        "all_oceans_exactly_one": len(ocean_names) > 0 and oceans_exactly_one == len(ocean_names),
        "corals_on_coral_reef": corals_on_coral_reef,
        "animal_count": animals,
    }


# ════════════════════════════════════════════════════════════════════════════
# Verified achievements + avatar unlocks (final-board-provable ONLY)
# ════════════════════════════════════════════════════════════════════════════
# Mirrors the online client's ACHIEVEMENT_DEFS conditions exactly (ids + XP
# from preview-app.js). Anything that needs turn-by-turn history (draw source,
# star usage, per-turn plays, chat, timing) is deliberately NOT here — those
# stay online-only, as the spec requires.

def _ach_defs() -> List[Dict[str, Any]]:
    return [
        {"id": "making_waves", "name": "Making Waves", "xp": 100,
         "check": lambda f, ctx: ctx["score"] >= 100 and ctx["pc"] >= 4},
        {"id": "big_splash", "name": "Big Splash", "xp": 250,
         "check": lambda f, ctx: ctx["score"] >= 150 and ctx["pc"] >= 4},
        {"id": "tidal_wave", "name": "Tidal Wave", "xp": 500,
         "check": lambda f, ctx: ctx["score"] >= 250 and ctx["pc"] >= 4},
        {"id": "tsunami", "name": "Tsunami", "xp": 1000,
         "check": lambda f, ctx: ctx["score"] >= 300},
        {"id": "crowded_current", "name": "Crowded Current", "xp": 150,
         "check": lambda f, ctx: ctx["pc"] >= 6},
        {"id": "full_boat", "name": "Full Boat", "xp": 300,
         "check": lambda f, ctx: ctx["pc"] >= 8},   # physical games have no bots
        {"id": "shoot_the_moon", "name": "Shoot the Moon", "xp": 1000,
         "check": lambda f, ctx: f["name_counts"].get("mandarin goby", 0) >= 4},
        {"id": "the_all_blue", "name": "The All Blue", "xp": 150,
         "check": lambda f, ctx: f["distinct_ocean_types"] >= 8},
        {"id": "one_ocean_wonder", "name": "One Ocean Wonder", "xp": 250,
         "check": lambda f, ctx: ctx["winner"] and f["ocean_count"] >= 1
                  and len(set(f["ocean_names"])) == 1},
        {"id": "circle_of_life", "name": "Circle of Life", "xp": 1000,
         "check": lambda f, ctx: ctx["winner"] and f["distinct_species"] >= 8},
    ]


def _avatar_defs() -> List[Dict[str, Any]]:
    gf = _game_fish_names()
    return [
        {"id": "mandarin-goby", "name": "Mandarin Goby", "img": "/avatars/mandarin-goby.png",
         "check": lambda f, ctx: f["name_counts"].get("mandarin goby", 0) >= 4 and ctx["pc"] >= 3},
        {"id": "deep-sea-coral", "name": "Deep Sea Coral", "img": "/avatars/deep-sea-coral.png",
         "check": lambda f, ctx: ctx["winner"] and f["all_oceans_exactly_one"]},
        {"id": "yellowfin-tuna", "name": "Yellowfin Tuna", "img": "/avatars/yellowfin-tuna.png",
         "check": lambda f, ctx: f["name_counts"].get("yellowfin tuna", 0) >= 8 and ctx["pc"] >= 3},
        {"id": "king-salmon", "name": "King Salmon", "img": "/avatars/king-salmon.png",
         "check": lambda f, ctx: ctx["winner"] and ctx["pc"] >= 3
                  and f["name_counts"].get("king salmon", 0) >= 2 and f["full_oceans"] >= 5},
        {"id": "sailfish", "name": "Sailfish", "img": "/avatars/sailfish.png",
         "check": lambda f, ctx: ctx["pc"] >= 5 and gf
                  and all(f["name_counts"].get(n, 0) >= 1 for n in gf)},
        {"id": "big-eye-tuna", "name": "Big Eye Tuna", "img": "/avatars/big-eye-tuna.png",
         "check": lambda f, ctx: ctx["winner"] and f["name_counts"].get("big eye tuna", 0) >= 5},
        {"id": "magnificent-frigatebird", "name": "Magnificent Frigatebird",
         "img": "/avatars/magnificent-frigatebird.png",
         "check": lambda f, ctx: f["corals_on_coral_reef"] >= 7},
        {"id": "sea-urchin", "name": "Sea Urchin", "img": "/avatars/sea-urchin.png",
         "check": lambda f, ctx: ctx["winner"] and ctx["win_margin"] == 1},
    ]


def _placement_xp(rank: int) -> int:
    """Same table as the online game (no bots in a physical game → no halving)."""
    if rank <= 1:
        return 100
    if rank == 2:
        return 75
    if rank == 3:
        return 50
    return 25


def _rank_for(all_scores: List[int], my_score: int) -> int:
    uniq = sorted(set(all_scores), reverse=True)
    try:
        return uniq.index(my_score) + 1
    except ValueError:
        return len(uniq) + 1


def compute_rewards(session: Dict[str, Any]) -> Dict[str, Any]:
    """Standings + per-seat verified rewards from the CONFIRMED boards.

    Pure calculation — used both for the pre-approval preview and (recomputed
    from scratch, never trusted from the preview) inside finalization.
    """
    seats = [s for s in session["seats"] if s.get("confirmedBoard")]
    raw_boards = [{"name": s["name"], "oceans": s["confirmedBoard"]["oceans"]} for s in seats]
    scored = score_boards(raw_boards, with_breakdown=False)
    boards_norm, game_db, _ = normalize_boards(raw_boards)

    all_scores = [p["score"] for p in scored["players"]]
    top = max(all_scores) if all_scores else 0
    second = max([s for s in all_scores if s < top], default=None)
    pc = int(session.get("playerCount") or len(seats))

    standings = []
    for i, s in enumerate(seats):
        score = scored["players"][i]["score"]
        rank = _rank_for(all_scores, score)
        winner = score == top and len(all_scores) > 0
        facts = _board_facts(boards_norm[i], game_db)
        ctx = {"score": score, "pc": pc, "winner": winner,
               "win_margin": (top - second) if (winner and second is not None) else 0}
        achievements = [{"id": d["id"], "name": d["name"], "xp": d["xp"]}
                        for d in _ach_defs() if _safe_check(d, facts, ctx)]
        avatars = [{"id": d["id"], "name": d["name"], "img": d["img"]}
                   for d in _avatar_defs() if _safe_check(d, facts, ctx)]
        xp_game = _placement_xp(rank)
        standings.append({
            "seat": s["seat"], "name": s["name"], "kind": s.get("kind", "guest"),
            "uid": s.get("uid") or "",
            "score": score, "placement": rank, "winner": winner,
            "strategy": scored["players"][i]["strategy"],
            "xpGame": xp_game,
            "achievements": achievements, "avatars": avatars,
            "xpTotal": xp_game + sum(a["xp"] for a in achievements),
        })
    standings.sort(key=lambda x: (-x["score"], x["seat"]))
    winner_name = standings[0]["name"] if standings else ""
    return {"standings": standings, "winner": winner_name, "playerCount": pc,
            "warnings": scored["warnings"]}


def _safe_check(d, facts, ctx) -> bool:
    try:
        return bool(d["check"](facts, ctx))
    except Exception:
        return False


# ════════════════════════════════════════════════════════════════════════════
# Claude vision detection (identification ONLY — never scoring)
# ════════════════════════════════════════════════════════════════════════════

def _vision_status() -> Tuple[bool, str]:
    """(enabled, reason). Photo detection needs BOTH the ANTHROPIC_API_KEY env
    var set on the server AND the anthropic SDK importable. The reason string is
    surfaced to the client so a misconfiguration is diagnosable, not silent:
      - "no_api_key": set ANTHROPIC_API_KEY on the Render service (it's a
        `sync: false` secret, so it must be entered in the dashboard, not the repo).
      - "sdk_missing": the anthropic package didn't import (rebuild the image).
      - "ok": detection is live.
    """
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return False, "no_api_key"
    try:
        import anthropic  # noqa: F401
    except Exception:
        return False, "sdk_missing"
    return True, "ok"


def _vision_enabled() -> bool:
    return _vision_status()[0]


def _decode_data_url(data_url: str) -> Tuple[bytes, str]:
    m = re.match(r"^data:(image/(?:jpeg|png|webp));base64,(.+)$", data_url or "", re.DOTALL)
    if not m:
        raise BoardError("image must be a jpeg/png/webp data URL")
    raw = base64.b64decode(m.group(2), validate=False)
    if len(raw) > 6 * 1024 * 1024:
        raise BoardError("image too large after decoding (max 6MB)")
    return raw, m.group(1)


def _image_hashes(raw: bytes) -> Dict[str, Any]:
    """sha256 always; dHash + small review thumbnail when Pillow is present."""
    out: Dict[str, Any] = {"sha256": hashlib.sha256(raw).hexdigest(), "dhash": None, "thumb": None}
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw)).convert("L").resize((9, 8))
        px = list(img.getdata())
        bits = 0
        for row in range(8):
            for col in range(8):
                bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
        out["dhash"] = f"{bits:016x}"
        rgb = Image.open(io.BytesIO(raw)).convert("RGB")
        rgb.thumbnail((320, 320))
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=60)
        out["thumb"] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        pass  # Pillow optional — sha256 alone still blocks exact reuse
    return out


def _hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


# ── deterministic pixel-level photo checks (backstop for the model) ──────────
# Unambiguous, resolution-independent signals the model can miss: an almost-
# black frame, a blown-out frame, or a tiny thumbnail. Deliberately does NOT
# try to judge blur (too many false positives across real photos) — clarity is
# the model's job. Pillow is optional; without it these all return False.

_MIN_PIXELS = 480 * 480          # under ~0.23MP is too small to read card text
_DARK_MEAN = 34                  # mean luminance (0-255) below this is too dark
_BRIGHT_MEAN = 233               # above this is blown out / heavy glare


def _photo_pixel_quality(raw: bytes) -> Dict[str, Any]:
    out = {"tooDark": False, "tooBright": False, "tooSmall": False, "brightness": None}
    try:
        from PIL import Image, ImageStat
        img = Image.open(io.BytesIO(raw))
        w, h = img.size
        out["tooSmall"] = (w * h) < _MIN_PIXELS
        mean = ImageStat.Stat(img.convert("L")).mean[0]
        out["brightness"] = round(mean, 1)
        out["tooDark"] = mean < _DARK_MEAN
        out["tooBright"] = mean > _BRIGHT_MEAN
    except Exception:
        pass  # Pillow missing or undecodable — the model's assessment still gates
    return out


_ISSUE_LABELS = {
    "blurry": "the photo is blurry",
    "out_of_focus": "the photo is out of focus",
    "motion_blur": "there's motion blur — hold the phone steady",
    "too_dark": "the photo is too dark — add more light",
    "too_bright": "the photo is overexposed — reduce glare",
    "glare_or_reflection": "there's glare or reflection on the cards",
    "too_far_away": "the board is too far away — move closer",
    "cards_cut_off": "some cards are cut off — fit the whole board in frame",
    "steep_angle": "shoot from straight above, not at an angle",
    "cards_overlapping": "some cards overlap — spread them apart",
    "partial_board": "only part of the board is visible",
    "not_a_board": "no game board was found in the photo",
}


def _retake_decision(detected: Dict[str, Any], pixq: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Should the player retake this photo? Combines the model's clarity
    assessment with unambiguous pixel checks. Returns (needsRetake, reasons).

    A retake is only RECOMMENDED — the review screen still lets the player
    correct cards by hand and continue, so the final confirmed board is always
    human-verified. This gate exists so an unclear photo doesn't silently
    produce a wrong score.
    """
    q = detected.get("quality") or {}
    reasons: List[str] = []

    for issue in q.get("issues") or []:
        label = _ISSUE_LABELS.get(str(issue))
        if label and label not in reasons:
            reasons.append(label)
    if pixq.get("tooDark") and _ISSUE_LABELS["too_dark"] not in reasons:
        reasons.append(_ISSUE_LABELS["too_dark"])
    if pixq.get("tooBright") and _ISSUE_LABELS["too_bright"] not in reasons:
        reasons.append(_ISSUE_LABELS["too_bright"])
    if pixq.get("tooSmall"):
        reasons.append("the photo is low-resolution — move closer or use a sharper camera")
    if int(detected.get("unreadableCount") or 0) > 0:
        reasons.append("some cards couldn't be read clearly")
    if not detected.get("oceans"):
        reasons.append("no ocean cards were detected — center the whole board in the photo")

    clear = q.get("clear", True)
    conf = q.get("confidence")
    low_conf = isinstance(conf, (int, float)) and conf < 0.70
    needs = bool(reasons) or clear is False or low_conf
    if needs and not reasons:
        reasons.append("the board wasn't fully clear — retake for an accurate score")

    # de-dupe, preserve order, cap
    seen: set = set()
    ordered = [r for r in reasons if not (r in seen or seen.add(r))]
    return needs, ordered[:6]


def _detection_schema() -> Dict[str, Any]:
    roster = build_roster()
    card_item = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "enum": roster["animalNames"] + ["Unknown"]},
            "symbol": {"type": "string", "enum": SYMBOLS + ["Unknown"]},
            "confidence": {"type": "number",
                           "description": "0-1 confidence this card is identified correctly"},
            "partially_hidden": {"type": "boolean"},
        },
        "required": ["name", "symbol", "confidence", "partially_hidden"],
    }
    cards_arr = {"type": "array", "items": card_item}
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            # Assessed FIRST, before identifying anything. Drives the retake gate:
            # a photo that isn't sharp enough to read every card is sent back so
            # the score is computed from a board the player can actually verify.
            "image_quality": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "clear_enough_to_score": {"type": "boolean",
                        "description": "true ONLY if every ocean and card name AND its "
                                       "symbol is sharp and readable with no guessing"},
                    "overall_confidence": {"type": "number",
                        "description": "0-1 confidence the WHOLE board was read correctly"},
                    "issues": {"type": "array", "items": {"type": "string", "enum": [
                        "blurry", "out_of_focus", "motion_blur", "too_dark", "too_bright",
                        "glare_or_reflection", "too_far_away", "cards_cut_off", "steep_angle",
                        "cards_overlapping", "partial_board", "not_a_board"]},
                        "description": "every quality problem you see; empty if the photo is clean"},
                    "advice": {"type": "string",
                        "description": "one short sentence telling the player how to retake a "
                                       "clearer photo (empty if the photo is already clear)"},
                },
                "required": ["clear_enough_to_score", "overall_confidence", "issues", "advice"],
            },
            "oceans": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "ocean": {"type": "string", "enum": roster["oceanNames"] + ["Unknown"]},
                    "confidence": {"type": "number"},
                    "up": cards_arr, "down": cards_arr, "left": cards_arr, "right": cards_arr,
                },
                "required": ["ocean", "confidence", "up", "down", "left", "right"],
            }},
            "unreadable_cards": {"type": "array", "items": {"type": "string"},
                                 "description": "visible cards that could not be identified"},
            "notes": {"type": "string"},
        },
        "required": ["image_quality", "oceans", "unreadable_cards", "notes"],
    }


_CARD_REF: Optional[str] = None


def _clean_card_text(txt: Any) -> str:
    """Ability text as printed, minus the internal *star* / | markup."""
    t = str(txt or "").replace("*", "").replace("|", "; ")
    return re.sub(r"\s+", " ", t).strip()


def _card_reference() -> str:
    """A compact, exact reference for the vision model, built straight from the
    real card database — every card's name, species, which side it attaches on,
    the symbols its printed copies actually use, and its ability text. This lets
    the model confirm a card by cross-referencing the picture, the printed
    species and ability text, and validate the symbol it reads against the
    copies that truly exist (symbols change scoring)."""
    global _CARD_REF
    if _CARD_REF is not None:
        return _CARD_REF
    db = _card_db()
    side_word = {"up": "above", "down": "below", "left": "left", "right": "right"}
    oceans: Dict[str, str] = {}
    animals: Dict[str, Dict[str, Any]] = {}
    for uid in sorted(db.keys()):
        cd = db[uid]
        if _is_end_game(cd):
            continue
        if _is_ocean(cd):
            oceans.setdefault(str(cd.name).strip(), _clean_card_text(cd.text))
            continue
        side = _card_dir(cd)
        if side not in side_word:
            continue
        a = animals.setdefault(str(cd.name).strip(),
                               {"species": str(cd.species or "").strip(),
                                "sides": set(), "syms": set(), "text": ""})
        a["sides"].add(side)
        sym = str(cd.symbol or "").strip()
        if sym and sym.lower() != "n/a":
            a["syms"].add(sym)
        if not a["text"]:
            a["text"] = _clean_card_text(cd.text)
    lines = ["OCEAN CARDS (name — ability text):"]
    for n in sorted(oceans):
        lines.append(f"- {n} — {oceans[n]}" if oceans[n] else f"- {n}")
    lines.append("")
    lines.append("ANIMAL CARDS (name — species — attaches — symbol(s) its copies use — ability text):")
    for n in sorted(animals):
        a = animals[n]
        sides = "/".join(side_word[s] for s in ("up", "down", "left", "right") if s in a["sides"])
        syms = "/".join(sorted(a["syms"])) or "no symbol"
        tail = f" — {a['text']}" if a["text"] else ""
        lines.append(f"- {n} — {a['species']} — {sides} — {syms}{tail}")
    _CARD_REF = "\n".join(lines)
    return _CARD_REF


def _vision_system_prompt() -> str:
    return (
        "You identify cards in photos of finished Currents & Critters boards. Accuracy "
        "matters more than completeness: a wrong card gives the player a wrong score, so "
        "when in doubt say \"Unknown\" rather than guess.\n\n"
        "A board is one or more OCEAN cards laid on the table; each ocean can have "
        "animal cards attached on up to four sides (above=up, below=down, left, right). "
        "Up/Down cards are landscape-oriented; Left/Right cards are portrait-oriented — use "
        "orientation to decide which side a card belongs to. Every card prints its NAME, its "
        "SPECIES, one SYMBOL (Triangle, Square, Heart, Circle, Diamond), and its ability "
        "TEXT. Read the name, then CONFIRM it against the animal in the picture, the printed "
        "species, and the ability text in the reference below. Read the symbol carefully and "
        "make sure it is one the reference lists for that card — the symbol picks which copy "
        "it is and changes scoring.\n\n"
        "Only the names in this reference exist; never report a name that isn't here.\n\n"
        + _card_reference() + "\n\n"
        "STEP 1 — assess image_quality FIRST. Set clear_enough_to_score = true ONLY if you "
        "can sharply read every card's name and symbol with no guessing. If the photo is "
        "blurry, dark, glared, too far away, at a steep angle, or any card is cut off or "
        "unreadable, set clear_enough_to_score = false, list the specific issues, and give "
        "one short sentence of advice (e.g. \"Hold the phone flat above the whole board in "
        "good light.\"). Be honest with overall_confidence.\n"
        "STEP 2 — identify only what is genuinely visible. Use \"Unknown\" for any card or "
        "ocean you cannot read confidently, set partially_hidden on covered cards, and give "
        "honest per-card confidence values. Never invent a card that isn't clearly there. "
        "You only identify cards — never compute scores."
    )


def detect_board(image_bytes: bytes, media_type: str,
                 prior_board: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One Claude vision call → detected board in name-space, then mapped to uids."""
    import anthropic
    client = anthropic.Anthropic()
    model = os.environ.get("SNAP_VISION_MODEL", "claude-opus-4-8").strip() or "claude-opus-4-8"
    instructions = (
        "Identify every ocean on this player's board and the animal cards attached to "
        "each side. List oceans left-to-right as laid on the table."
    )
    if prior_board:
        instructions += (
            "\n\nThis photo may show only PART of the board. Cards already identified from "
            "earlier photos of the SAME board are below — return the COMBINED board (keep "
            "prior cards unless this photo clearly contradicts them, and add new ones):\n"
            + json.dumps(prior_board)[:6000]
        )
    resp = client.messages.create(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=_vision_system_prompt(),
        messages=[{"role": "user", "content": [
            {"type": "image",
             "source": {"type": "base64", "media_type": media_type,
                        "data": base64.b64encode(image_bytes).decode("ascii")}},
            {"type": "text", "text": instructions},
        ]}],
        output_config={"format": {"type": "json_schema", "schema": _detection_schema()}},
    )
    if resp.stop_reason == "refusal":
        raise BoardError("the image could not be analyzed — try a clearer photo of just the board")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    data = json.loads(text)
    return _detected_names_to_board(data)


def _detected_names_to_board(data: Dict[str, Any]) -> Dict[str, Any]:
    """Map the model's name-space detection onto real card uids.

    Prefers the copy whose printed symbol matches what the model saw (symbols
    matter for '+N per matching symbol' scoring), falling back to any copy of
    that name on the right side.
    """
    db = _card_db()
    by_name: Dict[Tuple[str, str], List[int]] = {}
    name_all_syms: Dict[str, set] = {}   # every symbol printed on any copy of a name
    name_disp_syms: Dict[str, set] = {}  # same, original casing for messages
    for uid in sorted(db.keys()):
        cd = db[uid]
        if _is_end_game(cd):
            continue
        kind = "ocean" if _is_ocean(cd) else _card_dir(cd)
        nl = str(cd.name).strip().lower()
        by_name.setdefault((nl, kind), []).append(uid)
        sym_disp = str(cd.symbol).strip()
        if sym_disp and sym_disp.lower() != "n/a":
            name_all_syms.setdefault(nl, set()).add(sym_disp.lower())
            name_disp_syms.setdefault(nl, set()).add(sym_disp)

    used: set = set()
    warnings: List[str] = []
    low_confidence: List[str] = []
    unreadable = 0  # cards/oceans the model saw but could not read ("Unknown")

    def pick(name: str, kind: str, symbol: str) -> Optional[int]:
        pool = by_name.get((str(name).strip().lower(), kind)) or []
        if not pool:
            # side mismatch (model put a card on the wrong side) — try all sides
            for (n, k), uids in by_name.items():
                if n == str(name).strip().lower():
                    pool = uids
                    warnings.append(f"{name}: attaches on '{k}', not '{kind}' — check placement")
                    break
        sym = str(symbol or "").strip().lower()
        # The symbol chooses which physical copy this is, and copies score
        # differently. If the model read a symbol that NO copy of this card
        # (on any side) carries, it likely misread it — surface that so the
        # player checks. (Checked name-wide, not per-side, so a left/right side
        # mix-up doesn't masquerade as a symbol error.)
        nl = str(name).strip().lower()
        valid_syms = name_all_syms.get(nl)
        if sym and valid_syms and sym not in valid_syms:
            printed = "/".join(sorted(name_disp_syms.get(nl, set()))) or "none"
            warnings.append(f"{name}: the symbol read ({symbol}) isn't printed on any copy of "
                            f"this card (its copies use {printed}) — double-check the symbol")
            low_confidence.append(str(name))
        symbol_matches = [u for u in pool if u not in used
                          and str(db[u].symbol).strip().lower() == sym]
        for bucket in (symbol_matches, [u for u in pool if u not in used], pool):
            if bucket:
                used.add(bucket[0])
                return bucket[0]
        return None

    oceans_out = []
    for oc in data.get("oceans") or []:
        oname = oc.get("ocean") or "Unknown"
        if oname == "Unknown":
            unreadable += 1
            warnings.append("one ocean card could not be identified — add it manually")
            continue
        ou = pick(oname, "ocean", "")
        if ou is None:
            warnings.append(f"{oname}: not found in the card list")
            continue
        if float(oc.get("confidence") or 0) < 0.6:
            low_confidence.append(oname)
        entry: Dict[str, Any] = {"u": ou}
        for side in ("up", "down", "left", "right"):
            uids = []
            for c in oc.get(side) or []:
                cname = c.get("name") or "Unknown"
                if cname == "Unknown":
                    unreadable += 1
                    warnings.append(f"a card on {oname} ({side}) could not be read — add it manually")
                    continue
                u = pick(cname, side, c.get("symbol") or "")
                if u is None:
                    warnings.append(f"{cname}: not found in the card list")
                    continue
                if float(c.get("confidence") or 0) < 0.6:
                    low_confidence.append(cname)
                if c.get("partially_hidden"):
                    warnings.append(f"{cname} on {oname} looks partially covered — double-check it")
                uids.append(u)
            entry[side] = uids
        oceans_out.append(entry)

    for s in data.get("unreadable_cards") or []:
        unreadable += 1
        warnings.append(f"unreadable card reported: {str(s)[:80]}")
    notes = str(data.get("notes") or "").strip()

    q = data.get("image_quality") or {}
    conf_raw = q.get("overall_confidence")
    quality = {
        "clear": bool(q.get("clear_enough_to_score", True)),
        "confidence": (float(conf_raw) if isinstance(conf_raw, (int, float)) else None),
        "issues": [str(x) for x in (q.get("issues") or [])][:8],
        "advice": str(q.get("advice") or "").strip()[:200],
    }
    return {"oceans": oceans_out, "warnings": warnings,
            "lowConfidence": sorted(set(low_confidence)), "notes": notes[:500],
            "quality": quality, "unreadableCount": unreadable}


# ════════════════════════════════════════════════════════════════════════════
# Multiplayer score sessions (in-memory, persisted to the room-state disk)
# ════════════════════════════════════════════════════════════════════════════

_SESSIONS: Dict[str, Dict[str, Any]] = {}
_SESSIONS_LOCK = threading.RLock()


def _sessions_path() -> str:
    return os.path.join(_STATE_DIR, "snap_sessions.json")


def _persist_sessions_locked() -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = _sessions_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_SESSIONS, f)
        os.replace(tmp, _sessions_path())
    except Exception as exc:  # noqa: BLE001
        print(f"[snap] session persist failed: {exc}")


def _load_sessions_from_disk() -> None:
    try:
        with open(_sessions_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            with _SESSIONS_LOCK:
                _SESSIONS.update(data)
            print(f"[snap] restored {len(data)} score session(s)")
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"[snap] session restore failed: {exc}")


def _prune_sessions_locked() -> None:
    now = time.time()
    dead = []
    for code, s in _SESSIONS.items():
        age = now - float(s.get("createdAt") or now)
        if s.get("status") == "finalized" and age > FINALIZED_KEEP_SEC:
            dead.append(code)
        elif s.get("status") != "finalized" and age > SESSION_TTL_SEC:
            dead.append(code)
    for code in dead:
        _SESSIONS.pop(code, None)


def _new_code() -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(SESSION_CODE_LEN))
        if code not in _SESSIONS:
            return code


def _token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()


def _seat_for_token(session, token: str):
    th = _token_hash(str(token or ""))
    for s in session["seats"]:
        if s.get("tokenHash") == th:
            return s
    return None


def _clean_name(raw: Any, fallback: str = "Player") -> str:
    name = re.sub(r"[^\w \-'.]", "", str(raw or "")).strip()[:24]
    return name or fallback


def create_session(uid: Optional[str], name: str, player_count: int,
                   time_played: Optional[float]) -> Dict[str, Any]:
    player_count = max(2, min(8, int(player_count or 2)))
    now = time.time()
    if time_played is not None:
        if time_played > now + 600:
            raise BoardError("the game time can't be in the future")
        if time_played < now - 30 * 86400:
            raise BoardError("games older than 30 days can't be submitted for rewards")
    with _SESSIONS_LOCK:
        _prune_sessions_locked()
        code = _new_code()
        token = secrets.token_urlsafe(24)
        game_id = f"pg_{int(now)}_{secrets.token_hex(4)}"
        session = {
            "gameId": game_id, "code": code, "status": "gathering",
            "createdAt": now, "timePlayed": time_played, "submittedAt": None,
            "completedAt": None, "playerCount": player_count, "hostSeat": 0,
            "rejections": [], "approvalsResetAt": now,
            "seats": [{
                "seat": 0, "kind": "account" if uid else "guest",
                "uid": uid, "name": name, "joinedAt": now,
                "tokenHash": _token_hash(token), "status": "joined",
                "photoHashes": [], "photoDhashes": [], "thumb": None,
                "detectedBoard": None, "confirmedBoard": None,
                "warnings": [], "approvedAt": None,
            }],
        }
        _SESSIONS[code] = session
        _persist_sessions_locked()
        return {"code": code, "playerToken": token, "seat": 0,
                "session": session_view(session, 0)}


def join_session(code: str, uid: Optional[str], name: str) -> Dict[str, Any]:
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(str(code or "").upper())
        if not session:
            raise BoardError("no session with that code")
        if session["status"] == "finalized":
            raise BoardError("that game is already finalized")
        if uid:
            for s in session["seats"]:
                if s.get("uid") == uid:
                    raise BoardError("this account already joined the session")
        if len(session["seats"]) >= session["playerCount"]:
            raise BoardError("the session is full")
        token = secrets.token_urlsafe(24)
        seat_no = len(session["seats"])
        session["seats"].append({
            "seat": seat_no, "kind": "account" if uid else "guest",
            "uid": uid, "name": name, "joinedAt": time.time(),
            "tokenHash": _token_hash(token), "status": "joined",
            "photoHashes": [], "photoDhashes": [], "thumb": None,
            "detectedBoard": None, "confirmedBoard": None,
            "warnings": [], "approvedAt": None,
        })
        _reset_approvals_locked(session, reason=f"{name} joined")
        _persist_sessions_locked()
        return {"code": session["code"], "playerToken": token, "seat": seat_no,
                "session": session_view(session, seat_no)}


def add_offline_seat(session, name: str) -> Dict[str, Any]:
    """A physical player with no device: gets scored, cannot (and need not)
    approve — the spec requires approval from every JOINED player only."""
    if len(session["seats"]) >= session["playerCount"]:
        raise BoardError("the session is full")
    seat_no = len(session["seats"])
    session["seats"].append({
        "seat": seat_no, "kind": "offline", "uid": None, "name": name,
        "joinedAt": time.time(), "tokenHash": None, "status": "joined",
        "photoHashes": [], "photoDhashes": [], "thumb": None,
        "detectedBoard": None, "confirmedBoard": None,
        "warnings": [], "approvedAt": None,
    })
    _reset_approvals_locked(session, reason=f"{name} added")
    return {"seat": seat_no}


def _reset_approvals_locked(session, reason: str = "") -> None:
    """Any change to boards/roster voids every approval (spec: corrections
    return the game to the correction screen and re-collect approvals)."""
    changed = False
    for s in session["seats"]:
        if s.get("approvedAt"):
            s["approvedAt"] = None
            changed = True
        if s["status"] == "approved":
            s["status"] = "confirmed" if s.get("confirmedBoard") else "joined"
            changed = True
    if changed or reason:
        session["approvalsResetAt"] = time.time()
    if session["status"] == "review" and not _all_confirmed(session):
        session["status"] = "gathering"


def _all_confirmed(session) -> bool:
    return len(session["seats"]) > 0 and all(s.get("confirmedBoard") for s in session["seats"])


def _connected_seats(session):
    return [s for s in session["seats"] if s.get("kind") != "offline"]


def session_view(session, my_seat: Optional[int]) -> Dict[str, Any]:
    """Redacted view for polling — never leaks tokens or other uids."""
    seats = []
    for s in session["seats"]:
        seats.append({
            "seat": s["seat"], "name": s["name"], "kind": s["kind"],
            "status": s["status"],
            "hasBoard": bool(s.get("confirmedBoard") or s.get("detectedBoard")),
            "confirmed": bool(s.get("confirmedBoard")),
            "approved": bool(s.get("approvedAt")),
            "warnings": s.get("warnings") or [],
            "isMe": my_seat is not None and s["seat"] == my_seat,
            "connectedAccount": bool(s.get("uid")),
            "score": s.get("score"),
        })
    view = {
        "code": session["code"], "status": session["status"],
        "playerCount": session["playerCount"], "hostSeat": session["hostSeat"],
        "seats": seats, "createdAt": session["createdAt"],
        "approvalsResetAt": session.get("approvalsResetAt"),
        "rejections": (session.get("rejections") or [])[-3:],
        "awaitingApproval": [s["name"] for s in _connected_seats(session)
                             if not s.get("approvedAt")] if session["status"] == "review" else [],
    }
    if session["status"] in ("review", "finalized"):
        view["preview"] = session.get("preview")
    if session["status"] == "finalized":
        view["results"] = session.get("results")
    if my_seat is not None:
        me = next((s for s in session["seats"] if s["seat"] == my_seat), None)
        if me:
            view["myBoards"] = {
                str(s["seat"]): {
                    "detected": s.get("detectedView"),
                    "confirmed": bool(s.get("confirmedBoard")),
                } for s in session["seats"]
            }
            view["me"] = {"seat": me["seat"], "name": me["name"], "kind": me["kind"]}
    return view


def _refresh_review_state_locked(session) -> None:
    """When every board is confirmed: score the table + build the approval
    preview (standings, expected XP / achievements / avatars)."""
    if not _all_confirmed(session):
        return
    try:
        rewards = compute_rewards(session)
    except BoardError as exc:
        session["previewError"] = str(exc)
        return
    for s in session["seats"]:
        row = next((r for r in rewards["standings"] if r["seat"] == s["seat"]), None)
        s["score"] = row["score"] if row else None
    session["preview"] = rewards
    session["previewError"] = None
    if session["status"] == "gathering":
        session["status"] = "review"


# ════════════════════════════════════════════════════════════════════════════
# Anti-cheat risk scoring (reads happen before the finalize transaction)
# ════════════════════════════════════════════════════════════════════════════

def _risk_level(score: int, cfg) -> str:
    if score >= int(cfg["risk_block_at"]):
        return "submission_blocked"
    if score >= int(cfg["risk_hold_at"]):
        return "rewards_held"
    if score >= int(cfg["risk_review_at"]):
        return "review_recommended"
    return "normal"


def assess_risk(session, db) -> Dict[str, Any]:
    cfg = get_config()
    score = 0
    reasons: List[str] = []
    now = time.time()

    # 1) the same photo submitted for two different seats in THIS session
    seen: Dict[str, int] = {}
    for s in session["seats"]:
        for h in s.get("photoHashes") or []:
            if h in seen and seen[h] != s["seat"]:
                score += 60
                reasons.append("identical photo used for two different boards")
            seen[h] = s["seat"]

    account_uids = [s["uid"] for s in session["seats"] if s.get("uid")]
    if db is None:
        return {"score": score, "reasons": reasons, "level": _risk_level(score, cfg),
                "playerIndex": {}}

    # 2) exact photo reuse across games (hash doc exists for another gameId)
    all_hashes = sorted({h for s in session["seats"] for h in (s.get("photoHashes") or [])})
    for h in all_hashes[:24]:
        try:
            snap = db.collection("physicalImageHashes").document(h).get()
            if snap.exists and (snap.to_dict() or {}).get("gameId") != session["gameId"]:
                score += 60
                reasons.append("a board photo was already used in a previous game")
        except Exception:
            pass

    # 3..6) per-account history from physicalPlayerIndex/{uid}
    player_index: Dict[str, List[Dict[str, Any]]] = {}
    tp = float(session.get("timePlayed") or session.get("createdAt") or now)
    min_gap = float(cfg["min_minutes_between_rewarded"]) * 60.0
    exp_map = cfg.get("expected_min_game_minutes") or {}
    expected_min = float(exp_map.get(str(session["playerCount"]), 25)) * 60.0
    my_dhashes = sorted({d for s in session["seats"] for d in (s.get("photoDhashes") or []) if d})

    for uid in account_uids:
        games: List[Dict[str, Any]] = []
        try:
            snap = db.collection("physicalPlayerIndex").document(uid).get()
            if snap.exists:
                games = list((snap.to_dict() or {}).get("games") or [])
        except Exception:
            games = []
        player_index[uid] = games
        if not games:
            continue
        last = max(games, key=lambda g: float(g.get("co") or 0))
        last_done = float(last.get("co") or 0)
        if last_done and now - last_done < min_gap:
            score += 30
            reasons.append("previous rewarded game finished only minutes ago")
        if last_done and now - last_done < expected_min:
            score += 20
            reasons.append("games completed faster than a game can realistically be played")
        today = [g for g in games if now - float(g.get("co") or 0) < 86400]
        if len(today) >= int(cfg["max_rewarded_games_per_day"]):
            score += 40
            reasons.append("daily rewarded physical game limit reached")
        # overlapping claimed play windows
        for g in games:
            g_tp = float(g.get("tp") or g.get("ca") or 0)
            g_co = float(g.get("co") or 0)
            if g_tp and g_co and g_tp <= tp <= g_co:
                score += 35
                reasons.append("this player was in another physical game at the claimed time")
                break
        # near-duplicate board photos vs this player's recent games
        for g in games:
            for d in g.get("dh") or []:
                if any(_hamming(d, mine) <= int(cfg["near_duplicate_hamming"]) for mine in my_dhashes):
                    score += 35
                    reasons.append("a board photo looks nearly identical to a previous game's")
                    break
            else:
                continue
            break
        # repeated same-group rapid submissions
        others = set(account_uids) - {uid}
        for g in games:
            shared = others & set(g.get("uids") or [])
            if len(shared) >= 1 and now - float(g.get("co") or 0) < min_gap:
                score += 25
                reasons.append("the same group submitted another game only minutes apart")
                break
        # repeated back-dated claims (older-game scoring used unusually often)
        backdated = [g for g in today if float(g.get("sub") or 0) - float(g.get("tp") or 0) > 6 * 3600]
        if session.get("timePlayed") and now - tp > 6 * 3600 and len(backdated) >= 2:
            score += 15
            reasons.append("repeated old-game claims in a short period")

    reasons = sorted(set(reasons))
    return {"score": score, "reasons": reasons, "level": _risk_level(score, cfg),
            "playerIndex": player_index}


# ════════════════════════════════════════════════════════════════════════════
# Finalization — one Firestore transaction, rewards exactly once
# ════════════════════════════════════════════════════════════════════════════

def finalize_session(session) -> Dict[str, Any]:
    """Runs when the LAST connected player approves. Recomputes everything
    from the confirmed boards, applies anti-cheat, then writes the permanent
    record + rewards atomically. Never trusts the preview."""
    rewards = compute_rewards(session)          # full recompute (spec step 5-8)
    now = time.time()
    session["submittedAt"] = session.get("submittedAt") or now
    session["completedAt"] = now

    db = _get_firestore()
    risk = assess_risk(session, db)
    grant_rewards = risk["level"] in ("normal", "review_recommended")
    write_history = risk["level"] != "submission_blocked"

    results = {
        "standings": rewards["standings"], "winner": rewards["winner"],
        "playerCount": rewards["playerCount"], "gameId": session["gameId"],
        "riskLevel": risk["level"], "riskReasons": risk["reasons"],
        "rewardsGranted": False, "historySaved": False,
        "finalizedAt": now,
    }

    if db is None:
        results["note"] = ("Scores are final, but the account server is not configured — "
                           "no XP or history could be saved.")
        return results

    try:
        _finalize_in_firestore(db, session, rewards, risk, grant_rewards, write_history)
        results["rewardsGranted"] = grant_rewards
        results["historySaved"] = write_history
        if risk["level"] == "rewards_held":
            results["note"] = ("This game was saved, but XP and unlocks are held for a "
                               "quick review because the submission looked unusual.")
        elif risk["level"] == "submission_blocked":
            results["note"] = ("This game could not be verified, so no rewards were "
                               "granted. Scores above are still calculated correctly.")
    except _AlreadyFinalized:
        results["note"] = "This game was already submitted — rewards were not granted twice."
        results["historySaved"] = True
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        results["note"] = f"Saving rewards failed ({exc}) — nothing was partially granted."
    return results


class _AlreadyFinalized(Exception):
    pass


def _history_entry(session, rewards, my_row, boards_by_seat) -> Dict[str, Any]:
    """One stats.recent_games entry in the exact shape the game client writes,
    so physical games render in the profile History tab with real board art."""
    all_scores = [{"n": r["name"], "s": r["score"]} for r in rewards["standings"]]
    bds = []
    for r in rewards["standings"]:
        bv = boards_by_seat.get(r["seat"]) or {"oceans": []}
        bds.append({
            "n": r["name"],
            "b": [{"o": oc["n"], "a": [c["n"] for side in ("up", "down", "left", "right")
                                       for c in oc[side]]} for oc in bv["oceans"]],
            "bd": [{"ou": oc["u"], "on": oc["n"],
                    "u": [{"u": c["u"], "n": c["n"]} for c in oc["up"]],
                    "d": [{"u": c["u"], "n": c["n"]} for c in oc["down"]],
                    "l": [{"u": c["u"], "n": c["n"]} for c in oc["left"]],
                    "r": [{"u": c["u"], "n": c["n"]} for c in oc["right"]]}
                   for oc in bv["oceans"]],
        })
    opp = [r["name"] for r in rewards["standings"] if r["seat"] != my_row["seat"]][:8]
    return {
        "r": 1 if my_row["winner"] else 0,
        "s": my_row["score"],
        "t": int((session.get("timePlayed") or session["createdAt"]) * 1000),
        "opp": opp,
        "pc": rewards["playerCount"],
        "mode": "physical",
        "all": all_scores,
        "bds": bds,
        "win": rewards["winner"],
        "strat": my_row["strategy"],
        "pgId": session["gameId"],
    }


def _finalize_in_firestore(db, session, rewards, risk, grant_rewards: bool,
                           write_history: bool) -> None:
    from firebase_admin import firestore as fb_fs

    cfg = get_config()
    now = time.time()
    game_id = session["gameId"]
    game_ref = db.collection("physicalGames").document(game_id)

    # Board views for history entries (computed outside the txn — pure).
    seats_confirmed = [s for s in session["seats"] if s.get("confirmedBoard")]
    raw_boards = [{"name": s["name"], "oceans": s["confirmedBoard"]["oceans"]} for s in seats_confirmed]
    boards_norm, game_db, _ = normalize_boards(raw_boards)
    boards_by_seat = {s["seat"]: _board_view(boards_norm[i], game_db)
                      for i, s in enumerate(seats_confirmed)}

    account_rows = [r for r in rewards["standings"]
                    if r["uid"] and next((s for s in session["seats"]
                                          if s["seat"] == r["seat"]), {}).get("kind") == "account"]
    uids = [r["uid"] for r in account_rows]
    if len(uids) != len(set(uids)):
        raise BoardError("one account is assigned to two seats")

    txn = db.transaction()

    @fb_fs.transactional
    def _run(txn):
        snap = game_ref.get(transaction=txn)
        if snap.exists:
            raise _AlreadyFinalized()
        user_refs = {uid: db.collection("users").document(uid) for uid in uids}
        user_snaps = {uid: ref.get(transaction=txn) for uid, ref in user_refs.items()}
        index_refs = {uid: db.collection("physicalPlayerIndex").document(uid) for uid in uids}
        index_snaps = {uid: ref.get(transaction=txn) for uid, ref in index_refs.items()}

        # ── permanent game record ──────────────────────────────────────────
        all_hashes = sorted({h for s in session["seats"] for h in (s.get("photoHashes") or [])})
        txn.create(game_ref, {
            "status": "completed", "sessionType": "physical_multiplayer",
            "hostUserId": next((s.get("uid") for s in session["seats"]
                                if s["seat"] == session["hostSeat"]), None),
            "gameCode": session["code"],
            "createdAt": session["createdAt"],
            "timePlayed": session.get("timePlayed"),
            "submittedAt": session.get("submittedAt"),
            "completedAt": now,
            "playerCount": rewards["playerCount"],
            "winnerUserId": next((r["uid"] for r in rewards["standings"]
                                  if r["winner"] and r["uid"]), None),
            "winnerName": rewards["winner"],
            "finalStandings": [{"seat": r["seat"], "name": r["name"], "score": r["score"],
                                "placement": r["placement"], "uid": r["uid"] or None,
                                "strategy": r["strategy"]} for r in rewards["standings"]],
            "imageHashes": all_hashes,
            "antiCheatRiskScore": risk["score"],
            "antiCheatReasons": risk["reasons"],
            "antiCheatStatus": risk["level"],
            "rewardStatus": ("granted" if grant_rewards else
                             ("held" if risk["level"] == "rewards_held" else "blocked")),
            "finalized": True,
            "rulesVersion": "physical-v1",
            "scoringVersion": "engine-final-points-v1",
            "imageRetentionDays": int(cfg["image_retention_days"]),
        })
        for s in session["seats"]:
            row = next((r for r in rewards["standings"] if r["seat"] == s["seat"]), None)
            txn.set(game_ref.collection("players").document(str(s["seat"])), {
                "userId": s.get("uid"), "guest": s.get("kind") != "account",
                "displayName": s["name"], "seatNumber": s["seat"],
                "joinedAt": s.get("joinedAt"),
                "approvalStatus": "approved" if s.get("approvedAt") else
                                  ("offline" if s.get("kind") == "offline" else "n/a"),
                "approvedAt": s.get("approvedAt"),
                "confirmedCards": s.get("confirmedBoard"),
                "score": row["score"] if row else None,
                "placement": row["placement"] if row else None,
                "strategy": row["strategy"] if row else "",
                "xpEarned": (row["xpTotal"] if (row and grant_rewards and s.get("kind") == "account") else 0),
                "achievementsEarned": [a["id"] for a in (row["achievements"] if row else [])],
                "avatarsUnlocked": [a["id"] for a in (row["avatars"] if row else [])],
                "thumb": s.get("thumb"),
                "photoHashes": s.get("photoHashes") or [],
                "rewardStatus": "granted" if grant_rewards else risk["level"],
            })

        # ── image hash registry (exact-reuse detection across games) ───────
        for s in session["seats"]:
            dhs = s.get("photoDhashes") or []
            for i, h in enumerate(s.get("photoHashes") or []):
                txn.set(db.collection("physicalImageHashes").document(h), {
                    "gameId": game_id, "seat": s["seat"], "uid": s.get("uid"),
                    "dhash": dhs[i] if i < len(dhs) else None, "at": now,
                }, merge=True)

        # ── per-account rewards + history + stats (exactly once) ───────────
        icon_counts_updates: Dict[str, Any] = {}
        for row in account_rows:
            uid = row["uid"]
            if not user_snaps[uid].exists:
                # Account authenticated but has no users/ doc (never finished
                # onboarding). Skip its rewards instead of failing everyone's.
                continue
            udoc = user_snaps[uid].to_dict() if user_snaps[uid].exists else {}
            stats = dict((udoc or {}).get("stats") or {})
            achievements_state = dict((udoc or {}).get("achievements") or {})
            owned_icons = list((udoc or {}).get("unlocked_icons") or [])

            new_achievements = [a for a in row["achievements"]
                                if not (achievements_state.get(a["id"]) or {}).get("completed")]
            new_avatars = [a for a in row["avatars"] if a["img"] not in owned_icons]
            xp_gain = row["xpGame"] + sum(a["xp"] for a in new_achievements)

            # reward ledger — deterministic ids make double-grants impossible
            ledger_rows = [("xp_game", f"placement_{row['placement']}", row["xpGame"], None)]
            ledger_rows += [("achievement", a["id"], a["xp"], a["id"]) for a in new_achievements]
            ledger_rows += [("avatar", a["id"], 0, a["id"]) for a in new_avatars]
            for rtype, key, amount, extra in ledger_rows:
                ledger_key = f"{game_id}__{uid}__{rtype}__{key}"
                txn.create(db.collection("rewardLedger").document(ledger_key), {
                    "gameId": game_id, "userId": uid, "rewardType": rtype,
                    "rewardAmount": amount, "achievementId": extra if rtype == "achievement" else None,
                    "avatarId": extra if rtype == "avatar" else None,
                    "createdAt": now,
                    "status": "granted" if grant_rewards else "held",
                    "uniqueRewardKey": ledger_key,
                })

            updates: Dict[str, Any] = {}
            if write_history:
                entry = _history_entry(session, rewards, row, boards_by_seat)
                recent = list(stats.get("recent_games") or [])
                recent = [entry] + recent
                recent = recent[:50]
                # keep heavy board layouts only on the 12 newest (client rule)
                for i in range(12, len(recent)):
                    g = recent[i]
                    if isinstance(g, dict) and isinstance(g.get("bds"), list):
                        g["bds"] = [{"n": pp.get("n"), "b": pp.get("b")}
                                    for pp in g["bds"] if isinstance(pp, dict)]
                updates["stats.recent_games"] = recent
                updates["stats.completed_games"] = int(stats.get("completed_games") or 0) + 1
                updates["stats.physical_games"] = int(stats.get("physical_games") or 0) + 1
                updates["stats.total_score"] = int(stats.get("total_score") or 0) + row["score"]
                if row["winner"]:
                    updates["stats.physical_wins"] = int(stats.get("physical_wins") or 0) + 1
                if row["score"] > int(stats.get("highest_score") or 0):
                    updates["stats.highest_score"] = row["score"]
                if row["score"] > int(stats.get("highest_score_physical") or 0):
                    updates["stats.highest_score_physical"] = row["score"]

            if grant_rewards:
                new_total = int(stats.get("total_xp") or 0) + xp_gain
                lvl, xp_cur, xp_goal = _level_progress(new_total)
                updates.update({
                    "stats.total_xp": new_total,
                    "stats.level": lvl, "stats.player_level": lvl,
                    "stats.xp_current": xp_cur, "stats.level_xp_current": xp_cur,
                    "stats.xp_goal": xp_goal, "stats.level_xp_goal": xp_goal,
                    "stats.last_game_xp": row["xpGame"],
                })
                for a in new_achievements:
                    updates[f"achievements.{a['id']}"] = {"completed": True,
                                                          "unlockedAt": int(now * 1000),
                                                          "physical": True}
                if new_avatars:
                    updates["unlocked_icons"] = fb_fs.ArrayUnion([a["img"] for a in new_avatars])
                    for a in new_avatars:
                        safe = a["img"].replace(".", "_").replace("#", "_").replace("$", "_") \
                                        .replace("/", "_").replace("[", "_").replace("]", "_")
                        icon_counts_updates[safe] = fb_fs.Increment(1)

            if updates:
                txn.update(user_refs[uid], updates)

            # per-player physical-game index (rate limiting / overlap detection)
            idx_doc = index_snaps[uid].to_dict() if index_snaps[uid].exists else {}
            games = list((idx_doc or {}).get("games") or [])
            games.append({
                "g": game_id, "ca": session["createdAt"],
                "tp": session.get("timePlayed") or session["createdAt"],
                "sub": session.get("submittedAt") or now, "co": now,
                "pc": rewards["playerCount"], "uids": uids,
                "ih": all_hashes[:16],
                "dh": sorted({d for s in session["seats"]
                              for d in (s.get("photoDhashes") or []) if d})[:16],
            })
            games = games[-20:]
            txn.set(index_refs[uid], {"games": games, "updatedAt": now}, merge=True)

        if icon_counts_updates:
            txn.set(db.collection("meta").document("icon_unlock_counts"),
                    icon_counts_updates, merge=True)
        # Marketing "games played" counter counts finished physical games too,
        # under a separate field so the online number stays clean.
        txn.set(db.collection("meta").document("site_stats"),
                {"physical_games_played": fb_fs.Increment(1)}, merge=True)

    _run(txn)


# ════════════════════════════════════════════════════════════════════════════
# HTTP plumbing (called from multiplayer_server's do_GET / do_POST)
# ════════════════════════════════════════════════════════════════════════════

def _read_json(handler, max_bytes: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        size = int(handler.headers.get("Content-Length", "0") or 0)
    except ValueError:
        return None, "invalid Content-Length"
    if size <= 0:
        return {}, None
    if size > max_bytes:
        try:
            handler.rfile.read(min(size, 4096))
        except Exception:
            pass
        return None, f"request too large (max {max_bytes // (1024 * 1024)}MB)"
    try:
        raw = handler.rfile.read(size)
        body = json.loads(raw.decode("utf-8"))
        if not isinstance(body, dict):
            return None, "body must be a JSON object"
        return body, None
    except Exception:
        return None, "invalid JSON body"


def _auth_identity(body) -> Tuple[Optional[str], Optional[str]]:
    """(uid, name-from-token) when a valid idToken is supplied, else (None, None)."""
    tok = body.get("idToken") if isinstance(body.get("idToken"), str) else ""
    if not tok:
        return None, None
    claims = _verify_token(tok)
    if not claims or not claims.get("uid"):
        return None, None
    return claims["uid"], claims.get("name") or claims.get("email") or None


def handle_get(handler, parsed) -> bool:
    path = parsed.path
    if not path.startswith("/api/snap/"):
        return False
    try:
        if path == "/api/snap/config":
            enabled, reason = _vision_status()
            handler._send_json({"ok": True, "visionEnabled": enabled,
                                "visionReason": reason,
                                "rosterVersion": build_roster()["version"]})
            return True
        if path == "/api/snap/roster":
            handler._send_json({"ok": True, **build_roster()})
            return True
        if path == "/api/snap/session/state":
            from urllib.parse import parse_qs
            q = parse_qs(parsed.query or "")
            code = str((q.get("code") or [""])[0]).upper()
            token = str((q.get("token") or [""])[0])
            with _SESSIONS_LOCK:
                session = _SESSIONS.get(code)
                if not session:
                    handler._send_json({"ok": False, "error": "no session with that code"},
                                       status=HTTPStatus.NOT_FOUND)
                    return True
                seat = _seat_for_token(session, token)
                handler._send_json({"ok": True,
                                    "session": session_view(session, seat["seat"] if seat else None)})
            return True
        handler._send_json({"ok": False, "error": "unknown snap endpoint"},
                           status=HTTPStatus.NOT_FOUND)
        return True
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        handler._send_json({"ok": False, "error": str(exc)},
                           status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return True


def handle_post(handler, parsed) -> bool:  # noqa: C901 — one dispatcher, kept flat on purpose
    path = parsed.path
    if not path.startswith("/api/snap/"):
        return False
    is_photo = path in ("/api/snap/detect", "/api/snap/score-photo")
    body, err = _read_json(handler, MAX_PHOTO_BODY_BYTES if is_photo else MAX_JSON_BODY_BYTES)
    if err:
        handler._send_json({"ok": False, "error": err}, status=HTTPStatus.BAD_REQUEST)
        return True
    try:
        _dispatch_post(handler, path, body or {})
    except BoardError as exc:
        handler._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        handler._send_json({"ok": False, "error": f"server error: {exc}"},
                           status=HTTPStatus.INTERNAL_SERVER_ERROR)
    return True


def _dispatch_post(handler, path: str, body: Dict[str, Any]) -> None:
    # ── guest scoring: board (already picked/corrected) → official score ────
    if path == "/api/snap/score-board":
        boards = body.get("boards")
        if not isinstance(boards, list) or not boards:
            board = body.get("board")
            if not isinstance(board, dict):
                raise BoardError("board required")
            boards = [{"name": _clean_name(body.get("playerName"), "You"),
                       "oceans": board.get("oceans") or []}]
        if len(boards) > 8:
            raise BoardError("too many boards")
        handler._send_json({"ok": True, **score_boards(boards)})
        return

    # ── photo → detection (guest solo AND session seats use this) ───────────
    if path == "/api/snap/detect":
        if not _vision_enabled():
            raise BoardError("photo detection isn't configured on this server yet — "
                             "you can still build your board manually")
        raw, media_type = _decode_data_url(str(body.get("image") or ""))
        hashes = _image_hashes(raw)
        pixq = _photo_pixel_quality(raw)
        prior = body.get("priorBoard") if isinstance(body.get("priorBoard"), dict) else None
        detected = detect_board(raw, media_type, prior)
        # Clarity gate: an unclear photo should be retaken, not silently scored.
        # A rescan that MERGES onto an already-verified board (priorBoard) never
        # forces a retake — the player is only adding a corner they already have.
        needs_retake, retake_reasons = _retake_decision(detected, pixq)
        if prior:
            needs_retake, retake_reasons = False, []
        # Score preview for the detected board (solo flow shows it instantly).
        preview = None
        try:
            preview = score_boards([{"name": "You", "oceans": detected["oceans"]}],
                                   with_breakdown=False)["players"][0]
        except BoardError:
            pass
        out = {"ok": True, "board": {"oceans": detected["oceans"]},
               "warnings": detected["warnings"], "lowConfidence": detected["lowConfidence"],
               "notes": detected["notes"], "hash": hashes["sha256"],
               "scorePreview": preview,
               "needsRetake": needs_retake, "retakeReasons": retake_reasons,
               "quality": detected.get("quality") or {}}
        # Attached to a session seat → store photo evidence + detection there.
        code = str(body.get("code") or "").upper()
        if code:
            token = str(body.get("playerToken") or "")
            with _SESSIONS_LOCK:
                session = _SESSIONS.get(code)
                if not session:
                    raise BoardError("no session with that code")
                if session["status"] == "finalized":
                    raise BoardError("that game is already finalized")
                seat_req = _seat_for_token(session, token)
                if not seat_req:
                    raise BoardError("you are not part of that session")
                target_no = body.get("seat")
                target = next((s for s in session["seats"]
                               if s["seat"] == int(target_no if target_no is not None else seat_req["seat"])), None)
                if not target:
                    raise BoardError("no such seat")
                target["photoHashes"] = (target.get("photoHashes") or [])[-7:] + [hashes["sha256"]]
                target["photoDhashes"] = (target.get("photoDhashes") or [])[-7:] + \
                    ([hashes["dhash"]] if hashes["dhash"] else [])
                target["thumb"] = hashes["thumb"] or target.get("thumb")
                target["detectedBoard"] = {"oceans": detected["oceans"]}
                target["detectedView"] = _detected_view(detected["oceans"])
                target["warnings"] = detected["warnings"]
                target["status"] = "photographed"
                target["confirmedBoard"] = None
                _reset_approvals_locked(session, reason="board rescanned")
                _persist_sessions_locked()
        handler._send_json(out)
        return

    # ── session lifecycle ──────────────────────────────────────────────────
    if path == "/api/snap/session/create":
        uid, tok_name = _auth_identity(body)
        name = _clean_name(body.get("name") or tok_name, "Host")
        tp = body.get("timePlayed")
        time_played = float(tp) / 1000.0 if isinstance(tp, (int, float)) and tp else None
        out = create_session(uid, name, body.get("playerCount") or 2, time_played)
        handler._send_json({"ok": True, **out})
        return

    if path == "/api/snap/session/join":
        uid, tok_name = _auth_identity(body)
        name = _clean_name(body.get("name") or tok_name, "Player")
        out = join_session(body.get("code") or "", uid, name)
        handler._send_json({"ok": True, **out})
        return

    # everything below requires an existing session + a valid seat token
    code = str(body.get("code") or "").upper()
    token = str(body.get("playerToken") or "")
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(code)
        if not session:
            raise BoardError("no session with that code")
        me = _seat_for_token(session, token)
        if not me:
            raise BoardError("you are not part of that session")
        if session["status"] == "finalized" and path != "/api/snap/session/state":
            handler._send_json({"ok": True, "session": session_view(session, me["seat"])})
            return

        if path == "/api/snap/session/add-offline":
            if me["seat"] != session["hostSeat"]:
                raise BoardError("only the host can add players without a device")
            add_offline_seat(session, _clean_name(body.get("name"), "Player"))
            _persist_sessions_locked()
            handler._send_json({"ok": True, "session": session_view(session, me["seat"])})
            return

        if path == "/api/snap/session/confirm-board":
            seat_no = int(body.get("seat", me["seat"]))
            target = next((s for s in session["seats"] if s["seat"] == seat_no), None)
            if not target:
                raise BoardError("no such seat")
            # Connected players (account OR guest with a device) always confirm
            # their own board; only device-less offline seats can be confirmed
            # by someone else. The host gets no special power here — approval
            # and confirmation both belong to the seat's owner.
            if target["seat"] != me["seat"] and target.get("kind") != "offline":
                raise BoardError("each player confirms their own board")
            board = body.get("board")
            if not isinstance(board, dict) or not isinstance(board.get("oceans"), list):
                raise BoardError("board required")
            # validate by scoring it once (raises BoardError on bad shapes)
            score_boards([{"name": target["name"], "oceans": board["oceans"]}],
                         with_breakdown=False)
            target["confirmedBoard"] = {"oceans": board["oceans"]}
            target["detectedView"] = _detected_view(board["oceans"])
            target["status"] = "confirmed"
            _reset_approvals_locked(session, reason="board corrected")
            _refresh_review_state_locked(session)
            _persist_sessions_locked()
            handler._send_json({"ok": True, "session": session_view(session, me["seat"])})
            return

        if path == "/api/snap/session/link-account":
            uid, tok_name = _auth_identity(body)
            if not uid:
                raise BoardError("sign in first, then connect your account")
            if any(s.get("uid") == uid for s in session["seats"] if s["seat"] != me["seat"]):
                raise BoardError("this account is already on another seat")
            me["uid"] = uid
            me["kind"] = "account"
            if tok_name and me["name"] in ("Player", "Host"):
                me["name"] = _clean_name(tok_name, me["name"])
            _reset_approvals_locked(session, reason="account connected")
            _refresh_review_state_locked(session)
            _persist_sessions_locked()
            handler._send_json({"ok": True, "session": session_view(session, me["seat"])})
            return

        if path == "/api/snap/session/reject":
            session["rejections"] = (session.get("rejections") or [])[-9:] + [{
                "by": me["name"], "at": time.time(),
                "seat": body.get("seat"), "reason": str(body.get("reason") or "")[:140],
            }]
            _reset_approvals_locked(session, reason="results rejected")
            session["status"] = "gathering" if not _all_confirmed(session) else "review"
            _persist_sessions_locked()
            handler._send_json({"ok": True, "session": session_view(session, me["seat"])})
            return

        if path == "/api/snap/session/approve":
            if me.get("kind") == "account":
                uid, _n = _auth_identity(body)
                if not uid or uid != me.get("uid"):
                    raise BoardError("approval must come from the signed-in account on this seat")
            if session["status"] != "review":
                _refresh_review_state_locked(session)
            if session["status"] != "review":
                raise BoardError("every board must be photographed and confirmed first")
            me["approvedAt"] = time.time()
            me["status"] = "approved"
            pending = [s["name"] for s in _connected_seats(session) if not s.get("approvedAt")]
            if not pending:
                results = finalize_session(session)
                session["results"] = results
                session["status"] = "finalized"
            _persist_sessions_locked()
            handler._send_json({"ok": True, "session": session_view(session, me["seat"])})
            return

    raise BoardError("unknown snap endpoint")


def _detected_view(oceans_uids: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compact display copy (names + faces) of a uid-space board."""
    try:
        boards, game_db, _ = normalize_boards([{"name": "x", "oceans": oceans_uids}])
        return _board_view(boards[0], game_db)
    except Exception:
        return {"oceans": []}
