#!/usr/bin/env python3
"""Tournament engine for Currents & Critters — PURE LOGIC, no I/O.

This module is the server-authoritative backbone for Tournament Mode. It is
deliberately free of Flask/HTTP/Firestore concerns so it can be unit-tested
exhaustively and reused by ``multiplayer_server.py`` (which owns rooms, SSE,
and Firestore).

What lives here:
  • Bracket PLANNING — given a player count and players-per-match, deterministically
    build the whole single-elimination tree (rounds, matches, capacities, the
    "feeds into" links, byes, and smaller opening matches).
  • Bracket STATE — seed players into round 1, record a match result, advance the
    winner into the exact next-round slot, detect the champion.
  • PLACEMENTS — final 1st/2nd/3rd/… ranking, including an optional third-place
    match and a placement-by-progress fallback.
  • XP — a scaled, size-aware reward breakdown with stable component ids so the
    server can pay each component exactly once (idempotency lives in the caller's
    ledger; this module only *computes* the amounts and ids).

Design choices worth knowing:
  • Bracket shaping uses GREEDY PER-ROUND PACKING: each round splits the field into
    ceil(field / M) matches whose sizes differ by at most one. This minimises byes
    (a "bye" is a size-1 match) and prefers "smaller opening matches" over byes,
    exactly as the product spec asks. It is fully deterministic from (N, M).
  • Only the winner of a match advances by default (advance_per_match == 1). The API
    is written so >1 could be added later, but configs that would not strictly
    shrink the field are rejected by validation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ── Limits (mirror the product spec) ─────────────────────────────────────────
MIN_TOURNAMENT_PLAYERS = 4
MAX_TOURNAMENT_PLAYERS = 32
MIN_PLAYERS_PER_MATCH = 2
MAX_PLAYERS_PER_MATCH = 8

BRACKET_SINGLE_ELIM = "single_elimination"
SUPPORTED_BRACKETS = (BRACKET_SINGLE_ELIM,)

# Match status values used across server + client.
M_PENDING = "pending"    # slots not yet filled / earlier rounds not resolved
M_READY = "ready"        # all players present and marked ready
M_ACTIVE = "active"      # game in progress
M_COMPLETE = "complete"  # finished, winner known
M_BYE = "bye"            # single player, auto-advances (no game played)


# =============================================================================
# Config + validation
# =============================================================================

@dataclass
class TournamentConfig:
    total_capacity: int
    players_per_match: int
    advance_per_match: int = 1
    bracket_type: str = BRACKET_SINGLE_ELIM
    third_place_match: bool = False
    name: str = "Tournament"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_capacity": self.total_capacity,
            "players_per_match": self.players_per_match,
            "advance_per_match": self.advance_per_match,
            "bracket_type": self.bracket_type,
            "third_place_match": self.third_place_match,
            "name": self.name,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "TournamentConfig":
        return TournamentConfig(
            total_capacity=int(d.get("total_capacity", 0)),
            players_per_match=int(d.get("players_per_match", 0)),
            advance_per_match=int(d.get("advance_per_match", 1) or 1),
            bracket_type=str(d.get("bracket_type", BRACKET_SINGLE_ELIM)),
            third_place_match=bool(d.get("third_place_match", False)),
            name=str(d.get("name", "Tournament")),
        )


def validate_config(cfg: TournamentConfig) -> List[str]:
    """Return a list of human-readable errors (empty == valid)."""
    errs: List[str] = []
    if cfg.bracket_type not in SUPPORTED_BRACKETS:
        errs.append(f"Unsupported bracket type: {cfg.bracket_type!r}")
    if not (MIN_TOURNAMENT_PLAYERS <= cfg.total_capacity <= MAX_TOURNAMENT_PLAYERS):
        errs.append(
            f"Total players must be {MIN_TOURNAMENT_PLAYERS}–{MAX_TOURNAMENT_PLAYERS} "
            f"(got {cfg.total_capacity})."
        )
    if not (MIN_PLAYERS_PER_MATCH <= cfg.players_per_match <= MAX_PLAYERS_PER_MATCH):
        errs.append(
            f"Players per match must be {MIN_PLAYERS_PER_MATCH}–{MAX_PLAYERS_PER_MATCH} "
            f"(got {cfg.players_per_match})."
        )
    if cfg.players_per_match > cfg.total_capacity:
        errs.append("Players per match cannot exceed total players.")
    if cfg.advance_per_match < 1:
        errs.append("advance_per_match must be >= 1.")
    # Guard the extensible >1 advance path: the field must strictly shrink each
    # round, otherwise the bracket never converges to a single champion.
    if not errs and cfg.advance_per_match >= cfg.players_per_match:
        errs.append("advance_per_match must be less than players_per_match.")
    return errs


# =============================================================================
# Bracket planning (pure structure, no players yet)
# =============================================================================

def _round_match_sizes(field: int, per_match: int, advance: int) -> List[int]:
    """Split ``field`` players into matches of size <= per_match, as evenly as
    possible so byes (size-1 matches) are minimised. Returns match capacities."""
    if field <= 0:
        return []
    n_matches = math.ceil(field / per_match)
    base = field // n_matches
    extra = field % n_matches
    # The first ``extra`` matches get one more player. Larger matches first keeps
    # any unavoidable size-1 byes at the tail, which reads cleanly in the UI.
    return [base + (1 if i < extra else 0) for i in range(n_matches)]


def plan_round_sizes(n_players: int, per_match: int, advance: int = 1) -> List[List[int]]:
    """Deterministically compute every round's match capacities.

    Returns a list of rounds; each round is a list of match capacities.
    e.g. 6 players, 1v1 -> [[2,2,1,1],[2,2],[2]]  (round 1 has two byes)
    """
    rounds: List[List[int]] = []
    field = n_players
    guard = 0
    while field > 1:
        guard += 1
        if guard > 64:
            raise ValueError("bracket failed to converge (bad advance config?)")
        sizes = _round_match_sizes(field, per_match, advance)
        rounds.append(sizes)
        # winners produced this round = sum over matches of min(advance, size)
        field = sum(min(advance, s) for s in sizes)
    return rounds


@dataclass
class BracketMatch:
    round_index: int          # 0-based round
    match_index: int          # 0-based within the round
    match_number: int         # 1-based global, stable label for players
    capacity: int             # number of player slots
    advance: int              # how many winners advance from here
    player_ids: List[Optional[str]] = field(default_factory=list)  # len == capacity
    status: str = M_PENDING
    winners: List[str] = field(default_factory=list)     # advancing ids (len <= advance)
    ranking: List[str] = field(default_factory=list)     # full in-match placement, best->worst
    scores: Dict[str, int] = field(default_factory=dict)  # id -> match score (for tiebreaks)
    # Where each winner slot goes next: list aligned with winners advancing.
    feeds: List[Optional[Tuple[int, int, int]]] = field(default_factory=list)  # (r, m, slot)
    is_third_place: bool = False
    room_id: Optional[str] = None  # the GameRoom running this match (set by server)

    @property
    def is_bye(self) -> bool:
        return self.capacity <= 1

    def filled_count(self) -> int:
        return sum(1 for p in self.player_ids if p)

    def is_full(self) -> bool:
        return self.filled_count() >= self.capacity

    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_index": self.round_index,
            "match_index": self.match_index,
            "match_number": self.match_number,
            "capacity": self.capacity,
            "advance": self.advance,
            "player_ids": list(self.player_ids),
            "status": self.status,
            "winners": list(self.winners),
            "ranking": list(self.ranking),
            "scores": dict(self.scores),
            "feeds": [list(f) if f else None for f in self.feeds],
            "is_third_place": self.is_third_place,
            "is_bye": self.is_bye,
            "room_id": self.room_id,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "BracketMatch":
        m = BracketMatch(
            round_index=int(d["round_index"]),
            match_index=int(d["match_index"]),
            match_number=int(d["match_number"]),
            capacity=int(d["capacity"]),
            advance=int(d.get("advance", 1)),
            player_ids=list(d.get("player_ids", [])),
            status=str(d.get("status", M_PENDING)),
            winners=list(d.get("winners", [])),
            ranking=list(d.get("ranking", [])),
            scores={str(k): int(v) for k, v in dict(d.get("scores", {})).items()},
            feeds=[tuple(f) if f else None for f in d.get("feeds", [])],
            is_third_place=bool(d.get("is_third_place", False)),
            room_id=d.get("room_id"),
        )
        return m


class Bracket:
    """A full single-elimination bracket. Rounds -> matches -> slots.

    ``rounds`` are ordered opening-round first. The final round always has exactly
    one championship match. An optional extra third-place match is appended as its
    own entry on the semifinal-adjacent round when requested and well-defined.
    """

    def __init__(self, cfg: TournamentConfig, rounds: List[List[BracketMatch]],
                 third_place: Optional[BracketMatch] = None):
        self.cfg = cfg
        self.rounds = rounds
        self.third_place = third_place  # optional separate match

    # ---- construction -------------------------------------------------------
    @classmethod
    def build(cls, cfg: TournamentConfig, n_players: int) -> "Bracket":
        errs = validate_config(cfg)
        if errs:
            raise ValueError("; ".join(errs))
        if not (MIN_TOURNAMENT_PLAYERS <= n_players <= cfg.total_capacity):
            raise ValueError(
                f"n_players ({n_players}) must be between {MIN_TOURNAMENT_PLAYERS} "
                f"and total_capacity ({cfg.total_capacity})."
            )
        size_grid = plan_round_sizes(n_players, cfg.players_per_match, cfg.advance_per_match)
        rounds: List[List[BracketMatch]] = []
        match_no = 1
        for r, sizes in enumerate(size_grid):
            row: List[BracketMatch] = []
            for i, cap in enumerate(sizes):
                row.append(BracketMatch(
                    round_index=r, match_index=i, match_number=match_no,
                    capacity=cap, advance=min(cfg.advance_per_match, cap),
                    player_ids=[None] * cap,
                ))
                match_no += 1
            rounds.append(row)

        # Wire the "feeds into" links: winners of round r fill round r+1's slots
        # in order (match 0 first). This yields clean, non-crossing bracket lines.
        for r in range(len(rounds) - 1):
            nxt = rounds[r + 1]
            # Flat list of (next_match_index, slot) target slots for round r+1.
            targets: List[Tuple[int, int]] = []
            for mi, m in enumerate(nxt):
                for slot in range(m.capacity):
                    targets.append((mi, slot))
            t = 0
            for m in rounds[r]:
                m.feeds = []
                for _w in range(m.advance):
                    if t < len(targets):
                        nmi, nslot = targets[t]
                        m.feeds.append((r + 1, nmi, nslot))
                        t += 1
                    else:
                        m.feeds.append(None)

        third = None
        if cfg.third_place_match and _third_place_is_a_match(rounds):
            final = rounds[-1][0]
            semi_round = len(rounds) - 2
            third = BracketMatch(
                round_index=semi_round, match_index=len(rounds[semi_round]),
                match_number=match_no, capacity=len(rounds[semi_round]),
                advance=1, player_ids=[None] * len(rounds[semi_round]),
                is_third_place=True,
            )
            # third-place match is a leaf (no feeds); its winner = 3rd place.
        return cls(cfg, rounds, third)

    # ---- introspection ------------------------------------------------------
    def all_matches(self, include_third: bool = True) -> List[BracketMatch]:
        out: List[BracketMatch] = [m for row in self.rounds for m in row]
        if include_third and self.third_place is not None:
            out.append(self.third_place)
        return out

    def get_match(self, round_index: int, match_index: int) -> BracketMatch:
        if (self.third_place is not None and round_index == self.third_place.round_index
                and match_index == self.third_place.match_index):
            return self.third_place
        return self.rounds[round_index][match_index]

    @property
    def final_match(self) -> BracketMatch:
        return self.rounds[-1][0]

    @property
    def n_rounds(self) -> int:
        return len(self.rounds)

    def opening_matches(self) -> List[BracketMatch]:
        return list(self.rounds[0])

    def bye_matches(self) -> List[BracketMatch]:
        return [m for m in self.rounds[0] if m.is_bye]

    # ---- seeding ------------------------------------------------------------
    def opening_slots(self) -> List[Tuple[int, int]]:
        """Every (match_index, slot) in round 1, in seat order. Excludes byes'
        empty extra slots because byes only have one slot anyway."""
        slots: List[Tuple[int, int]] = []
        for m in self.rounds[0]:
            for s in range(m.capacity):
                slots.append((m.match_index, s))
        return slots

    def seed_players(self, ordered_player_ids: List[str]) -> None:
        """Place players into round-1 slots in the given order (host order or a
        pre-shuffled order). Auto-advances byes. Raises on count mismatch."""
        slots = self.opening_slots()
        if len(ordered_player_ids) != len(slots):
            raise ValueError(
                f"seed_players expected {len(slots)} players, got {len(ordered_player_ids)}"
            )
        for pid, (mi, s) in zip(ordered_player_ids, slots):
            self.rounds[0][mi].player_ids[s] = pid
        self._resolve_byes()

    def _resolve_byes(self) -> None:
        """Any round-1 bye (single player, capacity 1) auto-advances immediately.
        Byes in later rounds cascade through _push_winners as winners arrive."""
        for m in self.rounds[0]:
            self._maybe_resolve_bye(m)

    def _maybe_resolve_bye(self, m: BracketMatch) -> None:
        """If ``m`` is a bye (capacity 1) with its lone slot filled, auto-advance
        its player and push forward (which may cascade into further byes)."""
        if m.is_bye and m.status == M_PENDING and m.filled_count() >= 1:
            pid = next((p for p in m.player_ids if p), None)
            if pid:
                m.status = M_BYE
                m.winners = [pid]
                m.ranking = [pid]
                self._push_winners(m)

    # ---- result recording ---------------------------------------------------
    def record_result(self, round_index: int, match_index: int,
                      ranking: List[str], scores: Optional[Dict[str, int]] = None) -> None:
        """Record a completed match. ``ranking`` is best-first (full placement of
        every player in the match). Advances winner(s) to the next slot(s).

        Raises ValueError if the result is invalid (unknown player, wrong match,
        already complete, wrong player set)."""
        m = self.get_match(round_index, match_index)
        if m.status in (M_COMPLETE, M_BYE):
            raise ValueError(f"match {m.match_number} already complete")
        present = [p for p in m.player_ids if p]
        if sorted(ranking) != sorted(present):
            raise ValueError(
                f"ranking {ranking} does not match the players in match "
                f"{m.match_number} ({present})"
            )
        m.ranking = list(ranking)
        m.scores = dict(scores or {})
        m.winners = list(ranking[: m.advance])
        m.status = M_COMPLETE
        if not m.is_third_place:
            self._push_winners(m)

    def _push_winners(self, m: BracketMatch) -> None:
        touched: List[BracketMatch] = []
        for widx, feed in enumerate(m.feeds):
            if feed is None or widx >= len(m.winners):
                continue
            nr, nmi, nslot = feed
            tgt = self.rounds[nr][nmi]
            tgt.player_ids[nslot] = m.winners[widx]
            touched.append(tgt)
        # If the third-place match is waiting on this round's losers, try to fill.
        self._try_fill_third_place()
        # Cascade: a winner pushed into a later-round bye auto-advances too.
        for tgt in touched:
            self._maybe_resolve_bye(tgt)

    def _try_fill_third_place(self) -> None:
        tp = self.third_place
        if tp is None:
            return
        semi_round = tp.round_index
        losers: List[str] = []
        for m in self.rounds[semi_round]:
            if m.status == M_COMPLETE and len(m.ranking) >= 2:
                losers.append(m.ranking[1])  # the runner-up of each semifinal
        if len(losers) == tp.capacity and all(p is None for p in tp.player_ids):
            tp.player_ids = list(losers)

    # ---- completion / placements --------------------------------------------
    def is_complete(self) -> bool:
        if self.final_match.status not in (M_COMPLETE, M_BYE):
            return False
        if self.third_place is not None and self.third_place.filled_count() >= 1:
            return self.third_place.status in (M_COMPLETE, M_BYE)
        return True

    @property
    def champion(self) -> Optional[str]:
        f = self.final_match
        return f.winners[0] if f.winners else None

    def final_placements(self) -> List[Dict[str, Any]]:
        """Ordered placement list: [{place, player_id, round_reached, best_score}].

        1st/2nd come from the final. 3rd from the third-place match if present,
        else from a progress-based tiebreak. Remaining players are ranked by the
        round they reached (later = better), then by best match score."""
        if not self.final_match.ranking:
            return []
        placements: List[Dict[str, Any]] = []
        used: set = set()
        final = self.final_match

        # Track, for every player, the deepest round they appeared in and their
        # best score anywhere. This drives the tail ranking + tiebreaks.
        reached: Dict[str, int] = {}
        best_score: Dict[str, int] = {}
        for m in self.all_matches(include_third=False):
            for pid in m.player_ids:
                if pid:
                    reached[pid] = max(reached.get(pid, -1), m.round_index)
            for pid, sc in m.scores.items():
                best_score[pid] = max(best_score.get(pid, 0), int(sc))

        def add(pid: str, place: int) -> None:
            if pid in used:
                return
            used.add(pid)
            placements.append({
                "place": place,
                "player_id": pid,
                "round_reached": reached.get(pid, 0),
                "best_score": best_score.get(pid, 0),
            })

        # 1st + 2nd from the final's own ranking.
        add(final.ranking[0], 1)
        if len(final.ranking) >= 2:
            add(final.ranking[1], 2)

        # 3rd place.
        next_place = len(used) + 1
        if len(final.ranking) >= 3:
            # An N-player final ranks 3rd (and beyond) directly.
            for pid in final.ranking[2:]:
                add(pid, next_place)
                next_place += 1
        elif self.third_place is not None and self.third_place.ranking:
            add(self.third_place.ranking[0], next_place)
            next_place += 1
            if len(self.third_place.ranking) >= 2:
                add(self.third_place.ranking[1], next_place)
                next_place += 1

        # Everyone else: by round reached desc, then best score desc, then id.
        remaining = [pid for pid in reached if pid not in used]
        remaining.sort(key=lambda p: (-reached.get(p, 0), -best_score.get(p, 0), str(p)))
        for pid in remaining:
            add(pid, next_place)
            next_place += 1
        return placements


def _third_place_is_a_match(rounds: List[List[BracketMatch]]) -> bool:
    """A dedicated third-place match only makes sense when the final is 1v1 (2
    players from 2 distinct semifinal matches). For N-player finals the final
    itself already ranks 3rd, so no extra match is needed."""
    if len(rounds) < 2:
        return False
    final = rounds[-1][0]
    semis = rounds[-2]
    return final.capacity == 2 and len(semis) == 2


# =============================================================================
# Host preview summary (§2)
# =============================================================================

def bracket_summary(cfg: TournamentConfig, n_players: int) -> Dict[str, Any]:
    """The explanation shown to the host BEFORE they confirm a bracket."""
    sizes = plan_round_sizes(n_players, cfg.players_per_match, cfg.advance_per_match)
    opening = sizes[0] if sizes else []
    byes = sum(1 for r in sizes for c in r if c <= 1)     # total byes across the bracket
    opening_byes = sum(1 for c in opening if c <= 1)
    real_opening = sum(1 for c in opening if c > 1)
    total_matches = sum(len(r) for r in sizes)
    real_matches = sum(1 for r in sizes for c in r if c > 1)
    return {
        "tournament_size": n_players,
        "total_capacity": cfg.total_capacity,
        "players_per_match": cfg.players_per_match,
        "advance_per_match": cfg.advance_per_match,
        "num_rounds": len(sizes),
        "num_opening_matches": real_opening,
        "num_byes": byes,
        "num_opening_byes": opening_byes,
        "opening_match_sizes": list(opening),
        "total_matches": total_matches,   # counts byes as (trivial) matches
        "playable_matches": real_matches, # matches an actual game is spawned for
        "advancing_per_match": cfg.advance_per_match,
        "third_place_match": bool(cfg.third_place_match and _makes_third_place_match(cfg, n_players)),
    }


def _makes_third_place_match(cfg: TournamentConfig, n_players: int) -> bool:
    sizes = plan_round_sizes(n_players, cfg.players_per_match, cfg.advance_per_match)
    if len(sizes) < 2:
        return False
    return sizes[-1][0] == 2 and len(sizes[-2]) == 2


def available_formats(total_capacity: int) -> List[Dict[str, Any]]:
    """Which players-per-match options are valid for a given capacity, each with a
    preview summary. Used to render the §2 bracket-format picker."""
    out: List[Dict[str, Any]] = []
    for m in range(MIN_PLAYERS_PER_MATCH, MAX_PLAYERS_PER_MATCH + 1):
        if m > total_capacity:
            break
        cfg = TournamentConfig(total_capacity=total_capacity, players_per_match=m)
        if validate_config(cfg):
            continue
        summ = bracket_summary(cfg, total_capacity)
        out.append({
            "players_per_match": m,
            "label": " vs. ".join(["1"] * m) if m <= 3 else f"{m}-player matches",
            "num_rounds": summ["num_rounds"],
            "num_opening_matches": summ["num_opening_matches"],
            "num_byes": summ["num_byes"],
            "playable_matches": summ["playable_matches"],
        })
    return out


# =============================================================================
# XP (§12) — amounts + stable component ids; caller enforces idempotency
# =============================================================================

# Base rewards calibrated for a full 32-player tournament.
XP_BASE = {
    "champion": 3000,
    "second": 2000,
    "third": 1000,
    "fourth": 750,
    "reached_final": 500,
    "reached_semifinal": 250,
    "match_win": 200,       # per match win
    "no_quit": 150,
    "participation": 100,   # completed first match
}


# Below this many distinct real (non-guest) accounts, the big placement/champion/
# progress rewards are withheld — a tournament padded with guests or a single main
# vs. throwaways cannot be farmed for meaningful XP. (Guests never get XP at all.)
MIN_REAL_ACCOUNTS_FOR_PLACEMENT_XP = 3


def _size_scale(n_players: int) -> float:
    """Scale placement/bonus rewards down for smaller tournaments so a tiny
    tournament cannot be farmed for full XP. STEEP (quadratic-ish) with a low
    floor so a 4-player bracket pays only a small fraction of a full 32-player run."""
    n = max(MIN_TOURNAMENT_PLAYERS, min(MAX_TOURNAMENT_PLAYERS, int(n_players)))
    frac = (n - MIN_TOURNAMENT_PLAYERS) / (MAX_TOURNAMENT_PLAYERS - MIN_TOURNAMENT_PLAYERS)
    return round(0.12 + 0.88 * (frac ** 1.35), 4)


def compute_player_xp(
    tournament_id: str,
    player_id: str,
    *,
    n_players: int,
    place: int,
    matches_won: int,
    rounds_total: int,
    deepest_round: int,     # 0-based deepest round the player appeared in
    completed_first_match: bool,
    completed_without_quitting: bool,
    real_accounts: Optional[int] = None,   # distinct non-guest accounts that competed
) -> Dict[str, Any]:
    """Return an itemised XP breakdown with stable component ids.

    Each component id is deterministic: ``xp:{tournament_id}:{player_id}:{kind}``.
    The caller records these ids in a ledger and pays each at most once, so a
    duplicate result submission never double-awards (§12/§15). ``real_accounts``
    gates the big rewards: below MIN_REAL_ACCOUNTS_FOR_PLACEMENT_XP distinct real
    accounts, placement/champion/progress XP is withheld (anti-farm)."""
    scale = _size_scale(n_players)
    # Field-integrity gate: a real field earns placement XP; a padded one doesn't.
    ra = n_players if real_accounts is None else int(real_accounts)
    placement_ok = ra >= MIN_REAL_ACCOUNTS_FOR_PLACEMENT_XP
    items: List[Dict[str, Any]] = []

    def item(kind: str, amount: int) -> None:
        if amount <= 0:
            return
        items.append({
            "id": f"xp:{tournament_id}:{player_id}:{kind}",
            "kind": kind,
            "amount": int(round(amount)),
        })

    # Placement rewards (scaled). Withheld unless the field has enough real accounts.
    if placement_ok:
        if place == 1:
            item("champion", XP_BASE["champion"] * scale)
        elif place == 2:
            item("second", XP_BASE["second"] * scale)
        elif place == 3:
            item("third", XP_BASE["third"] * scale)
        elif place == 4:
            item("fourth", XP_BASE["fourth"] * scale)

        # Progress bonuses. "reached final" = appeared in the last round;
        # "reached semifinal" = appeared in the second-to-last round or deeper.
        final_round = rounds_total - 1
        if rounds_total >= 1 and deepest_round >= final_round:
            item("reached_final", XP_BASE["reached_final"] * scale)
        if rounds_total >= 2 and deepest_round >= final_round - 1:
            item("reached_semifinal", XP_BASE["reached_semifinal"] * scale)

    # Match wins (scaled). Also gated so self-play loops can't accrue win XP.
    if matches_won > 0 and placement_ok:
        item("match_win", XP_BASE["match_win"] * matches_won * scale)

    # Flat-ish participation + no-quit (lightly scaled so small tournaments still
    # grant them, but not at full value). These are always allowed (tiny).
    if completed_first_match:
        item("participation", XP_BASE["participation"] * (0.5 + 0.5 * scale))
    if completed_without_quitting:
        item("no_quit", XP_BASE["no_quit"] * (0.5 + 0.5 * scale))

    total = sum(it["amount"] for it in items)
    return {
        "tournament_id": tournament_id,
        "player_id": player_id,
        "place": place,
        "scale": scale,
        "items": items,
        "total_xp": total,
    }
