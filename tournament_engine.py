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
    exactly as the product spec asks. It is fully deterministic from (N, M, A).
  • ADVANCEMENT IS CONFIGURABLE: ``advance_per_match`` is how many finishers get out
    of every match — 1 is classic single elimination, 2+ is a "top N advance" /
    group-stage format. Two invariants keep any (N, M, A) converging on one champion:
      – a match can never advance everyone: effective advance == min(A, capacity - 1)
        (a bye, capacity 1, still advances its lone player);
      – the round that holds a single match IS the Final, so it advances exactly 1.
    See ``_effective_advance`` / ``plan_round_sizes``.
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
# A match must always knock at least one player out, so the most that can ever
# advance is one fewer than the biggest match.
MAX_ADVANCE_PER_MATCH = MAX_PLAYERS_PER_MATCH - 1

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
    # CUSTOM BRACKET (optional): an explicit list of opening-round match sizes,
    # e.g. [3, 4, 2] = a 3-player match, a 4-player match, then a 2-player match.
    # When set, the opening round uses exactly these sizes (instead of the greedy
    # even split from ``players_per_match``); later rounds are still packed greedily
    # with ``players_per_match`` (which is kept == max(opening_sizes)). The whole
    # bracket then has a FIXED shape sized for ``total_capacity`` == sum(opening_sizes),
    # so a custom tournament must fill every seat (players or bots) before it starts.
    opening_sizes: Optional[List[int]] = None
    # DESIGNED BRACKET (optional): the host's hand-built bracket from the canvas
    # builder — an arbitrary DAG of matches with typed player spots and explicit
    # winner-advances-here connections. Serialized form of a ``CustomBracket`` (see
    # below). When set it fully replaces the generated shape: total_capacity is the
    # number of player-entry spots and players_per_match is the largest match.
    custom_graph: Optional[Dict[str, Any]] = None

    @property
    def is_custom(self) -> bool:
        """True for either host-authored shape (opening-size list or full graph) —
        both have a FIXED bracket that must be completely filled before start."""
        return bool(self.opening_sizes) or bool(self.custom_graph)

    @property
    def is_graph(self) -> bool:
        """True only for a canvas-designed bracket (``custom_graph``)."""
        return bool(self.custom_graph)

    def graph(self) -> Optional["CustomBracket"]:
        """Parse ``custom_graph`` into a CustomBracket (None when not a graph)."""
        if not self.custom_graph:
            return None
        return CustomBracket.from_dict(self.custom_graph)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_capacity": self.total_capacity,
            "players_per_match": self.players_per_match,
            "advance_per_match": self.advance_per_match,
            "bracket_type": self.bracket_type,
            "third_place_match": self.third_place_match,
            "name": self.name,
            "opening_sizes": list(self.opening_sizes) if self.opening_sizes else None,
            "custom_graph": dict(self.custom_graph) if self.custom_graph else None,
            "is_custom": self.is_custom,
            "is_graph": self.is_graph,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "TournamentConfig":
        raw_sizes = d.get("opening_sizes")
        sizes = [int(s) for s in raw_sizes] if raw_sizes else None
        graph = d.get("custom_graph")
        return TournamentConfig(
            total_capacity=int(d.get("total_capacity", 0)),
            players_per_match=int(d.get("players_per_match", 0)),
            advance_per_match=int(d.get("advance_per_match", 1) or 1),
            bracket_type=str(d.get("bracket_type", BRACKET_SINGLE_ELIM)),
            third_place_match=bool(d.get("third_place_match", False)),
            name=str(d.get("name", "Tournament")),
            opening_sizes=sizes,
            custom_graph=dict(graph) if isinstance(graph, dict) else None,
        )


def validate_config(cfg: TournamentConfig) -> List[str]:
    """Return a list of human-readable errors (empty == valid)."""
    errs: List[str] = []
    if cfg.bracket_type not in SUPPORTED_BRACKETS:
        errs.append(f"Unsupported bracket type: {cfg.bracket_type!r}")
    # A canvas-designed bracket carries its own complete shape, so it is checked by
    # the graph validator instead of the generated-bracket rules below.
    if cfg.custom_graph is not None:
        if cfg.opening_sizes:
            errs.append("A designed bracket cannot also set opening match sizes.")
        try:
            spec = CustomBracket.from_dict(cfg.custom_graph)
        except Exception as exc:  # noqa: BLE001
            errs.append(f"Designed bracket is unreadable: {exc}")
            return errs
        errs.extend(validate_custom_bracket(spec))
        if not errs:
            if cfg.total_capacity != spec.entry_count():
                errs.append(
                    "Designed bracket: total players must equal the number of player "
                    f"spots ({spec.entry_count()})."
                )
            if cfg.players_per_match != spec.max_capacity():
                errs.append(
                    "Designed bracket: players_per_match must equal the largest match "
                    f"({spec.max_capacity()})."
                )
        return errs
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
    # A match must knock at least one player out, or the field never shrinks and the
    # bracket cannot converge on a single champion.
    if not errs and cfg.advance_per_match >= cfg.players_per_match:
        errs.append(
            f"Top {cfg.advance_per_match} cannot advance out of a "
            f"{cfg.players_per_match}-player match — at least one player must be "
            "knocked out."
        )
    # Custom opening-round layout, if provided, must itself be well-formed and be
    # consistent with total_capacity / players_per_match.
    if cfg.opening_sizes is not None:
        errs.extend(validate_opening_sizes(cfg.opening_sizes))
        if not errs and cfg.advance_per_match >= min(cfg.opening_sizes):
            errs.append(
                f"Top {cfg.advance_per_match} cannot advance out of a "
                f"{min(cfg.opening_sizes)}-player match — make every opening match "
                f"at least {cfg.advance_per_match + 1} players, or advance fewer."
            )
        if not errs:
            if sum(cfg.opening_sizes) != cfg.total_capacity:
                errs.append(
                    "Custom bracket: total players must equal the sum of the match "
                    f"sizes ({sum(cfg.opening_sizes)})."
                )
            if cfg.players_per_match != max(cfg.opening_sizes):
                errs.append(
                    "Custom bracket: players_per_match must equal the largest match "
                    f"size ({max(cfg.opening_sizes)})."
                )
    # Belt-and-braces: actually plan the bracket. The rules above should make every
    # surviving config converge, so a failure here is a bug, not a user mistake —
    # but it must never reach a live tournament.
    if not errs:
        try:
            plan_round_sizes(cfg.total_capacity, cfg.players_per_match,
                             cfg.advance_per_match, opening_sizes=cfg.opening_sizes)
        except ValueError as exc:
            errs.append(f"That bracket never reaches a single champion ({exc}).")
    return errs


def validate_opening_sizes(sizes: List[int]) -> List[str]:
    """Validate a host-authored custom opening-round layout (list of match sizes).
    Each match must hold MIN_PLAYERS_PER_MATCH..MAX_PLAYERS_PER_MATCH players (no
    manual byes), there must be at least one match, and the field must total a legal
    tournament size."""
    errs: List[str] = []
    if not isinstance(sizes, (list, tuple)) or len(sizes) < 1:
        errs.append("Custom bracket needs at least one match.")
        return errs
    try:
        ints = [int(s) for s in sizes]
    except (TypeError, ValueError):
        errs.append("Custom bracket match sizes must be whole numbers.")
        return errs
    for s in ints:
        if not (MIN_PLAYERS_PER_MATCH <= s <= MAX_PLAYERS_PER_MATCH):
            errs.append(
                f"Each match must have {MIN_PLAYERS_PER_MATCH}–{MAX_PLAYERS_PER_MATCH} "
                f"players (got {s})."
            )
            break
    total = sum(ints)
    if not (MIN_TOURNAMENT_PLAYERS <= total <= MAX_TOURNAMENT_PLAYERS):
        errs.append(
            f"Custom bracket total players must be {MIN_TOURNAMENT_PLAYERS}–"
            f"{MAX_TOURNAMENT_PLAYERS} (got {total})."
        )
    return errs


# =============================================================================
# DESIGNED BRACKETS — the canvas builder's data model
# =============================================================================
# A designed bracket is an arbitrary DAG of matches instead of a generated grid.
# The host places match boxes on a canvas, sets how many player spots each holds
# (2–8) and how many players advance, then draws a connection from each advancing
# finisher to the exact spot it fills in a later match. Every player spot is typed:
#
#   open        — a human or an AI may take it
#   human       — a real player only
#   ai          — always filled by a bot
#   invite      — reserved for one named player
#   winner_from — filled by the winner of another match      (rank 1)
#   top_from    — filled by the Nth advancing player of another match (rank N)
#
# The two feed kinds are the same edge with a different rank, so they share one
# representation: (source match, rank). "Winner from" is simply rank 1.
SLOT_OPEN = "open"
SLOT_HUMAN = "human"
SLOT_AI = "ai"
SLOT_INVITE = "invite"
SLOT_WINNER_FROM = "winner_from"
SLOT_TOP_FROM = "top_from"

ENTRY_SLOT_KINDS = (SLOT_OPEN, SLOT_HUMAN, SLOT_AI, SLOT_INVITE)
FEED_SLOT_KINDS = (SLOT_WINNER_FROM, SLOT_TOP_FROM)
ALL_SLOT_KINDS = ENTRY_SLOT_KINDS + FEED_SLOT_KINDS

# Canvas limits. 32 matches is far past any 32-player bracket's needs and keeps
# both the validator and the rendered canvas bounded.
MAX_CUSTOM_MATCHES = 32
MAX_LABEL_LEN = 28


@dataclass
class CustomSlot:
    """One player spot inside a designed match."""
    kind: str = SLOT_OPEN
    source: str = ""      # feed kinds: the match id this spot is fed from
    rank: int = 1         # feed kinds: 1 = winner, 2 = runner-up, …
    invite: str = ""      # invite kind: the reserved player's name / friend code

    @property
    def is_entry(self) -> bool:
        return self.kind in ENTRY_SLOT_KINDS

    @property
    def is_feed(self) -> bool:
        return self.kind in FEED_SLOT_KINDS

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"kind": self.kind}
        if self.is_feed:
            d["source"] = self.source
            d["rank"] = int(self.rank)
        if self.kind == SLOT_INVITE and self.invite:
            d["invite"] = self.invite
        return d

    @staticmethod
    def from_dict(d: Any) -> "CustomSlot":
        if isinstance(d, str):          # tolerate a bare kind string
            d = {"kind": d}
        if not isinstance(d, dict):
            d = {}
        kind = str(d.get("kind") or SLOT_OPEN).strip().lower()
        # "winner"/"top" shorthands from the client map onto the canonical kinds.
        kind = {"winner": SLOT_WINNER_FROM, "top": SLOT_TOP_FROM,
                "bot": SLOT_AI, "player": SLOT_HUMAN}.get(kind, kind)
        try:
            rank = int(d.get("rank") or 1)
        except (TypeError, ValueError):
            rank = 1
        return CustomSlot(
            kind=kind,
            source=str(d.get("source") or "")[:64],
            rank=rank,
            invite=str(d.get("invite") or "")[:40],
        )


@dataclass
class CustomMatch:
    """One match box on the canvas."""
    id: str
    slots: List[CustomSlot] = field(default_factory=list)
    advance: int = 1
    label: str = ""
    x: float = 0.0
    y: float = 0.0

    @property
    def capacity(self) -> int:
        return len(self.slots)

    def display(self, index: int) -> str:
        return self.label.strip() or f"Match {index + 1}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "advance": int(self.advance),
            "x": round(float(self.x), 1), "y": round(float(self.y), 1),
            "slots": [s.to_dict() for s in self.slots],
        }

    @staticmethod
    def from_dict(d: Dict[str, Any], fallback_id: str = "") -> "CustomMatch":
        if not isinstance(d, dict):
            d = {}
        raw_slots = d.get("slots")
        slots = [CustomSlot.from_dict(s) for s in raw_slots] if isinstance(raw_slots, (list, tuple)) else []
        def _num(v: Any) -> float:
            try:
                return float(v or 0)
            except (TypeError, ValueError):
                return 0.0
        try:
            advance = int(d.get("advance") or 1)
        except (TypeError, ValueError):
            advance = 1
        return CustomMatch(
            id=str(d.get("id") or fallback_id)[:64],
            slots=slots,
            advance=advance,
            label=str(d.get("label") or "")[:MAX_LABEL_LEN],
            x=_num(d.get("x")), y=_num(d.get("y")),
        )


@dataclass
class CustomBracket:
    """A whole host-designed bracket: match boxes + the connections between them."""
    matches: List[CustomMatch] = field(default_factory=list)

    # ---- (de)serialization --------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {"matches": [m.to_dict() for m in self.matches]}

    @staticmethod
    def from_dict(d: Any) -> "CustomBracket":
        if isinstance(d, (list, tuple)):
            d = {"matches": list(d)}
        if not isinstance(d, dict):
            d = {}
        raw = d.get("matches")
        raw = list(raw) if isinstance(raw, (list, tuple)) else []
        return CustomBracket(matches=[CustomMatch.from_dict(m, fallback_id=f"m{i + 1}")
                                      for i, m in enumerate(raw)])

    # ---- lookups ------------------------------------------------------------
    def by_id(self, mid: str) -> Optional[CustomMatch]:
        return next((m for m in self.matches if m.id == mid), None)

    def index_of(self, mid: str) -> int:
        return next((i for i, m in enumerate(self.matches) if m.id == mid), -1)

    def display(self, mid: str) -> str:
        i = self.index_of(mid)
        return self.matches[i].display(i) if i >= 0 else str(mid)

    def entry_count(self) -> int:
        return sum(1 for m in self.matches for s in m.slots if s.is_entry)

    def human_capacity(self) -> int:
        """Spots a real player can occupy (everything but AI-only)."""
        return sum(1 for m in self.matches for s in m.slots
                   if s.is_entry and s.kind != SLOT_AI)

    def max_capacity(self) -> int:
        return max((m.capacity for m in self.matches), default=0)

    def consumers(self) -> Dict[Tuple[str, int], List[Tuple[str, int]]]:
        """(source match id, rank) -> [(target match id, slot index), …].

        A well-formed bracket has exactly one target per advancing finisher; the
        validator uses the multi-value shape to report duplicates."""
        out: Dict[Tuple[str, int], List[Tuple[str, int]]] = {}
        for m in self.matches:
            for si, s in enumerate(m.slots):
                if s.is_feed:
                    out.setdefault((s.source, int(s.rank)), []).append((m.id, si))
        return out

    def terminal_matches(self) -> List[CustomMatch]:
        """Matches nothing advances out of — a well-formed bracket has exactly one
        (the Final, whose winner is the champion)."""
        fed_from = {s.source for m in self.matches for s in m.slots if s.is_feed}
        return [m for m in self.matches if m.id not in fed_from]


def validate_custom_bracket(spec: CustomBracket) -> List[str]:
    """Every rule a designed bracket must satisfy before a tournament can open.

    Returns human-readable errors (empty == the bracket is playable):
      • every player spot is a real, valid kind, and starting spots total 4–32
      • every match that needs a next round is connected onward
      • no connection points at a missing match, itself, or a spot already taken
      • the number of players advancing from a match matches the spots waiting
      • exactly one Final, reachable from every match, producing one champion
    """
    errs: List[str] = []
    ms = spec.matches
    if not ms:
        return ["Add at least one match to the bracket."]
    if len(ms) > MAX_CUSTOM_MATCHES:
        return [f"A bracket can hold at most {MAX_CUSTOM_MATCHES} matches (got {len(ms)})."]

    name = lambda mid: spec.display(mid)  # noqa: E731

    # ── per-match shape ──────────────────────────────────────────────────────
    seen_ids: set = set()
    for i, m in enumerate(ms):
        who = m.display(i)
        if not m.id:
            errs.append(f"{who} is missing an id.")
        elif m.id in seen_ids:
            errs.append(f"Two matches share the id {m.id!r} — each match needs its own.")
        seen_ids.add(m.id)
        if not (MIN_PLAYERS_PER_MATCH <= m.capacity <= MAX_PLAYERS_PER_MATCH):
            errs.append(
                f"{who} has {m.capacity} player spot(s) — each match holds "
                f"{MIN_PLAYERS_PER_MATCH}–{MAX_PLAYERS_PER_MATCH}."
            )
            continue
        if m.advance < 1:
            errs.append(f"{who} must advance at least 1 player.")
        elif m.advance >= m.capacity:
            errs.append(
                f"{who} advances {m.advance} of {m.capacity} players — a match must "
                "knock at least one player out."
            )
        for si, s in enumerate(m.slots):
            if s.kind not in ALL_SLOT_KINDS:
                errs.append(f"{who}, spot {si + 1}: {s.kind!r} is not a valid spot type.")
                continue
            if s.is_feed:
                if not s.source:
                    errs.append(f"{who}, spot {si + 1} waits on a match result but no match is connected.")
                elif s.source == m.id:
                    errs.append(f"{who}, spot {si + 1} is fed by {who} itself.")
                elif spec.by_id(s.source) is None:
                    errs.append(f"{who}, spot {si + 1} is fed by a match that no longer exists.")
                elif s.rank < 1:
                    errs.append(f"{who}, spot {si + 1} has an invalid finishing place.")
    if errs:
        return errs   # later checks assume well-formed matches

    # ── connections point somewhere real ─────────────────────────────────────
    for i, m in enumerate(ms):
        for si, s in enumerate(m.slots):
            if not s.is_feed:
                continue
            src = spec.by_id(s.source)
            if src and s.rank > src.advance:
                errs.append(
                    f"{m.display(i)}, spot {si + 1} takes finisher #{s.rank} of "
                    f"{name(s.source)}, but only {src.advance} advance"
                    f"{'' if src.advance != 1 else 's'} from there."
                )
    # A finisher can only go one place.
    for (sid, rank), targets in sorted(spec.consumers().items()):
        if len(targets) > 1:
            spots = ", ".join(f"{name(t[0])} spot {t[1] + 1}" for t in targets)
            errs.append(
                f"{_place_word(rank)} of {name(sid)} is sent to {len(targets)} spots "
                f"({spots}) — a player can only advance to one."
            )
    if errs:
        return errs

    # ── no loops ─────────────────────────────────────────────────────────────
    cycle = _find_cycle(spec)
    if cycle:
        errs.append("These matches feed each other in a loop: "
                    + " → ".join(name(c) for c in cycle) + ".")
        return errs

    # ── exactly one Final, and everything reaches it ─────────────────────────
    terminals = spec.terminal_matches()
    if not terminals:
        errs.append("No Final: every match advances into another one, so no champion is decided.")
        return errs
    if len(terminals) > 1:
        errs.append(
            "The bracket has "
            + str(len(terminals))
            + " matches that lead nowhere ("
            + ", ".join(name(m.id) for m in terminals)
            + ") — connect them so exactly one Final decides the champion."
        )
        return errs
    final = terminals[0]
    if final.advance != 1:
        errs.append(f"{name(final.id)} is the Final — exactly 1 player can win it "
                    f"(it advances {final.advance}).")

    # ── every advancing player has a seat waiting ────────────────────────────
    consumed = spec.consumers()
    for i, m in enumerate(ms):
        if m.id == final.id:
            continue
        for rank in range(1, m.advance + 1):
            if (m.id, rank) not in consumed:
                errs.append(
                    f"{_place_word(rank)} of {m.display(i)} has nowhere to go — "
                    "connect it to a spot in a later match."
                )

    # ── reachability (belt-and-braces; implied by the checks above) ──────────
    unreachable = [m for m in ms if m.id != final.id and not _reaches(spec, m.id, final.id)]
    if unreachable:
        errs.append("These matches never lead to the Final: "
                    + ", ".join(name(m.id) for m in unreachable) + ".")

    # ── field size ───────────────────────────────────────────────────────────
    entries = spec.entry_count()
    if not (MIN_TOURNAMENT_PLAYERS <= entries <= MAX_TOURNAMENT_PLAYERS):
        errs.append(
            f"A tournament needs {MIN_TOURNAMENT_PLAYERS}–{MAX_TOURNAMENT_PLAYERS} "
            f"starting player spots (this bracket has {entries})."
        )
    if spec.human_capacity() < 1:
        errs.append("Every spot is AI-only — leave at least one spot a person can take.")
    return errs


def _place_word(rank: int) -> str:
    if rank == 1:
        return "The winner"
    if rank == 2:
        return "The runner-up"
    return f"Finisher #{rank}"


def _find_cycle(spec: CustomBracket) -> Optional[List[str]]:
    """Return one cycle of match ids (feeds-into direction), or None."""
    edges: Dict[str, List[str]] = {m.id: [] for m in spec.matches}
    for m in spec.matches:
        for s in m.slots:
            if s.is_feed and s.source in edges:
                edges[s.source].append(m.id)     # source feeds INTO m
    WHITE, GREY, BLACK = 0, 1, 2
    color = {mid: WHITE for mid in edges}
    stack: List[str] = []

    def visit(node: str) -> Optional[List[str]]:
        color[node] = GREY
        stack.append(node)
        for nxt in edges.get(node, ()):
            if color.get(nxt) == GREY:
                return stack[stack.index(nxt):] + [nxt]
            if color.get(nxt) == WHITE:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for mid in list(edges):
        if color[mid] == WHITE:
            found = visit(mid)
            if found:
                return found
    return None


def _reaches(spec: CustomBracket, start: str, target: str) -> bool:
    edges: Dict[str, List[str]] = {m.id: [] for m in spec.matches}
    for m in spec.matches:
        for s in m.slots:
            if s.is_feed and s.source in edges:
                edges[s.source].append(m.id)
    seen: set = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(edges.get(cur, ()))
    return False


def layer_custom_bracket(spec: CustomBracket) -> List[List[CustomMatch]]:
    """Group the designed matches into rounds by longest path from a starting match.

    A match sits one layer past the deepest match feeding it, so the Final (which
    every path ends at) is always alone in the last layer — exactly the invariant
    the runtime Bracket relies on. Within a layer the host's own ordering is kept.
    """
    depth: Dict[str, int] = {}

    def resolve(mid: str, guard: int = 0) -> int:
        if mid in depth:
            return depth[mid]
        if guard > MAX_CUSTOM_MATCHES + 1:
            raise ValueError("designed bracket has a loop")
        m = spec.by_id(mid)
        if m is None:
            return 0
        sources = [s.source for s in m.slots if s.is_feed and spec.by_id(s.source)]
        d = 0 if not sources else 1 + max(resolve(s, guard + 1) for s in sources)
        depth[mid] = d
        return d

    for m in spec.matches:
        resolve(m.id)
    n_layers = max(depth.values(), default=0) + 1
    layers: List[List[CustomMatch]] = [[] for _ in range(n_layers)]
    for m in spec.matches:
        layers[depth.get(m.id, 0)].append(m)
    return [row for row in layers if row]


def custom_bracket_summary(spec: CustomBracket) -> Dict[str, Any]:
    """The host-facing description of a designed bracket (mirrors bracket_summary)."""
    layers = layer_custom_bracket(spec)
    entries = spec.entry_count()
    kinds: Dict[str, int] = {k: 0 for k in ENTRY_SLOT_KINDS}
    for m in spec.matches:
        for s in m.slots:
            if s.is_entry:
                kinds[s.kind] = kinds.get(s.kind, 0) + 1
    opening = [m.capacity for m in layers[0]] if layers else []
    # A designed bracket sets advancement per match, so report the range the host
    # actually drew rather than pretending it is uniform single elimination.
    advances = sorted({int(m.advance) for m in spec.matches}) or [1]
    return {
        "tournament_size": entries,
        "total_capacity": entries,
        "human_capacity": spec.human_capacity(),
        "players_per_match": spec.max_capacity(),
        "advance_per_match": max(advances),
        "advance_range": [min(advances), max(advances)],
        "mixed_advance": len(advances) > 1,
        "is_custom": True,
        "is_graph": True,
        "opening_sizes": list(opening),
        "num_rounds": len(layers),
        "num_opening_matches": len(opening),
        "num_byes": 0,
        "num_opening_byes": 0,
        "opening_match_sizes": list(opening),
        "total_matches": len(spec.matches),
        "playable_matches": len(spec.matches),
        "advancing_per_match": max(advances),
        "third_place_match": False,
        "slot_kinds": kinds,
        "round_labels": [", ".join(sorted({m.label.strip() for m in row if m.label.strip()}))
                         for row in layers],
    }


def make_uniform_graph(sizes: List[int]) -> CustomBracket:
    """Build a designed bracket from a list of opening match sizes — the same shape
    the simple custom builder produces. Handy as the canvas builder's starting
    point and as a test fixture: winners feed forward in order, round by round."""
    spec = CustomBracket()
    per_match = max(sizes) if sizes else 2
    grid = plan_round_sizes(0, per_match, 1, opening_sizes=[int(s) for s in sizes])
    counter = 0
    prev_ids: List[str] = []
    for r, row in enumerate(grid):
        ids: List[str] = []
        # Winners of the previous round fill this round's spots in order.
        feed_queue = list(prev_ids)
        for i, cap in enumerate(row):
            counter += 1
            mid = f"m{counter}"
            slots: List[CustomSlot] = []
            for _ in range(cap):
                if r == 0:
                    slots.append(CustomSlot(kind=SLOT_OPEN))
                else:
                    src = feed_queue.pop(0) if feed_queue else ""
                    slots.append(CustomSlot(kind=SLOT_WINNER_FROM, source=src, rank=1))
            spec.matches.append(CustomMatch(
                id=mid, slots=slots, advance=1,
                label=("Final" if (r == len(grid) - 1) else f"Round {r + 1}"),
                x=r * 260, y=i * 160,
            ))
            ids.append(mid)
        prev_ids = ids
    return spec


# =============================================================================
# Bracket planning (pure structure, no players yet)
# =============================================================================

def _effective_advance(capacity: int, advance: int) -> int:
    """How many players really get out of a match of ``capacity`` seats.

    A match can never advance its whole field — somebody has to be knocked out —
    so this clamps to ``capacity - 1``. A bye (capacity 1) is the one exception:
    its lone player advances, having played nobody. This clamp is what guarantees
    every (players, per-match, advance) combination converges on one champion."""
    if capacity <= 1:
        return 1
    return max(1, min(int(advance or 1), capacity - 1))


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


def plan_round_sizes(n_players: int, per_match: int, advance: int = 1,
                     opening_sizes: Optional[List[int]] = None) -> List[List[int]]:
    """Deterministically compute every round's match capacities.

    Returns a list of rounds; each round is a list of match capacities.
    e.g. 6 players, 1v1 -> [[2,2,2],[2,1],[2]]  (round 2 has a bye)

    With ``advance`` > 1 ("top N advance") each match sends its N best finishers on,
    e.g. 16 players in 4-player matches with the top 2 advancing ->
    [[4,4,4,4],[4,4],[4]].

    If ``opening_sizes`` is given it becomes round 1 verbatim (a CUSTOM bracket,
    e.g. [3,4,2] -> [[3,4,2], ...]); ``n_players`` is then ignored and every later
    round is packed greedily from the number of advancing winners.
    """
    rounds: List[List[int]] = []
    # A round holding exactly one match IS the Final — it crowns the champion
    # rather than advancing anyone, whatever ``advance`` says.
    survivors = lambda sizes: (1 if len(sizes) <= 1 else  # noqa: E731
                               sum(_effective_advance(s, advance) for s in sizes))
    if opening_sizes:
        opening = [int(s) for s in opening_sizes]
        rounds.append(opening)
        field = survivors(opening)
    else:
        field = n_players
    guard = 0
    while field > 1:
        guard += 1
        if guard > 64:
            raise ValueError("bracket failed to converge (bad advance config?)")
        sizes = _round_match_sizes(field, per_match, advance)
        rounds.append(sizes)
        field = survivors(sizes)
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
    # Designed-bracket extras (empty for generated brackets): the host's own label
    # ("Semifinal", "Losers Bracket", …), the canvas match id + position, and the
    # per-spot types so the client can show "AI", "reserved for …" on empty seats.
    label: str = ""
    custom_id: str = ""
    slot_kinds: List[Dict[str, Any]] = field(default_factory=list)
    x: float = 0.0
    y: float = 0.0

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
            "label": self.label,
            "custom_id": self.custom_id,
            "slot_kinds": [dict(s) for s in self.slot_kinds],
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
            label=str(d.get("label") or ""),
            custom_id=str(d.get("custom_id") or ""),
            slot_kinds=[dict(s) for s in (d.get("slot_kinds") or [])],
        )
        return m


class Bracket:
    """A full single-elimination bracket. Rounds -> matches -> slots.

    ``rounds`` are ordered opening-round first. The final round always has exactly
    one championship match. An optional extra third-place match is appended as its
    own entry on the semifinal-adjacent round when requested and well-defined.
    """

    def __init__(self, cfg: TournamentConfig, rounds: List[List[BracketMatch]],
                 third_place: Optional[BracketMatch] = None,
                 spec: Optional[CustomBracket] = None):
        self.cfg = cfg
        self.rounds = rounds
        self.third_place = third_place  # optional separate match
        # Designed brackets keep their source graph so the server can map player
        # spots back to the host's typed seats (AI-only, invite-only, …).
        self.spec = spec

    # ---- construction -------------------------------------------------------
    @classmethod
    def build(cls, cfg: TournamentConfig, n_players: int) -> "Bracket":
        errs = validate_config(cfg)
        if errs:
            raise ValueError("; ".join(errs))
        if cfg.is_graph:
            # A canvas-designed bracket carries its own complete shape.
            return cls.build_custom(cfg)
        if cfg.opening_sizes:
            # Custom bracket: the shape is FIXED by opening_sizes (sized for
            # total_capacity). n_players is only how many real players will be
            # seeded now — the rest of the slots wait for joiners/bots — so it may
            # be anything from 0 (empty preview) up to capacity.
            if not (0 <= n_players <= cfg.total_capacity):
                raise ValueError(
                    f"n_players ({n_players}) must be between 0 and total_capacity "
                    f"({cfg.total_capacity})."
                )
            size_grid = plan_round_sizes(
                n_players, cfg.players_per_match, cfg.advance_per_match,
                opening_sizes=cfg.opening_sizes)
        else:
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
            # The lone match of the last round is the Final: it crowns one champion
            # instead of advancing the configured top N. Must mirror plan_round_sizes.
            is_final_round = (len(sizes) <= 1)
            for i, cap in enumerate(sizes):
                row.append(BracketMatch(
                    round_index=r, match_index=i, match_number=match_no,
                    capacity=cap,
                    advance=1 if is_final_round else _effective_advance(cap, cfg.advance_per_match),
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

    @classmethod
    def build_custom(cls, cfg: TournamentConfig, spec: Optional[CustomBracket] = None) -> "Bracket":
        """Compile a host-designed bracket (canvas graph) into the runtime bracket.

        The graph is laid out into rounds by longest path, which puts the Final
        alone in the last round, then every "winner advances here" connection is
        translated into the same ``feeds`` links a generated bracket uses. From
        that point on the designed bracket runs through the identical machinery —
        record_result / _push_winners / placements all work unchanged.
        """
        if spec is None:
            spec = cfg.graph()
        if spec is None:
            raise ValueError("build_custom needs a designed bracket")
        errs = validate_custom_bracket(spec)
        if errs:
            raise ValueError("; ".join(errs))
        layers = layer_custom_bracket(spec)
        rounds: List[List[BracketMatch]] = []
        pos: Dict[str, Tuple[int, int]] = {}   # custom id -> (round, index)
        match_no = 1
        for r, layer in enumerate(layers):
            row: List[BracketMatch] = []
            for i, cm in enumerate(layer):
                pos[cm.id] = (r, i)
                row.append(BracketMatch(
                    round_index=r, match_index=i, match_number=match_no,
                    capacity=cm.capacity, advance=min(cm.advance, cm.capacity),
                    player_ids=[None] * cm.capacity,
                    label=cm.display(spec.index_of(cm.id)),
                    custom_id=cm.id,
                    slot_kinds=[s.to_dict() for s in cm.slots],
                    x=cm.x, y=cm.y,
                ))
                match_no += 1
            rounds.append(row)

        # Wire feeds straight from the host's connections: advancing finisher #k of
        # a match goes to the exact spot the host connected it to.
        consumed = spec.consumers()
        for cm in spec.matches:
            r, i = pos[cm.id]
            bm = rounds[r][i]
            bm.feeds = []
            for rank in range(1, bm.advance + 1):
                targets = consumed.get((cm.id, rank)) or []
                if not targets:
                    bm.feeds.append(None)     # the Final's champion goes nowhere
                    continue
                tid, tslot = targets[0]
                tr, ti = pos[tid]
                bm.feeds.append((tr, ti, tslot))
        return cls(cfg, rounds, None, spec=spec)

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

    def entry_slots(self) -> List[Dict[str, Any]]:
        """Every spot a player is SEEDED into, in seat order.

        For a generated bracket that is exactly round 1. A designed bracket may put
        starting spots in any round (e.g. a Final with two winner spots and one
        AI-only spot), so this walks the whole bracket in (round, match, spot) order
        and returns the typed entry spots only — feed spots fill themselves as
        matches finish. Each entry: {round_index, match_index, slot, kind, invite,
        match_number, label, seat}."""
        out: List[Dict[str, Any]] = []
        if self.spec is None:
            for m in self.rounds[0]:
                for s in range(m.capacity):
                    out.append({"round_index": 0, "match_index": m.match_index, "slot": s,
                                "kind": SLOT_OPEN, "invite": "",
                                "match_number": m.match_number, "label": m.label,
                                "seat": len(out)})
            return out
        for row in self.rounds:
            for m in row:
                for s, kind_d in enumerate(m.slot_kinds):
                    if str(kind_d.get("kind")) not in ENTRY_SLOT_KINDS:
                        continue
                    out.append({
                        "round_index": m.round_index, "match_index": m.match_index,
                        "slot": s, "kind": str(kind_d.get("kind")),
                        "invite": str(kind_d.get("invite") or ""),
                        "match_number": m.match_number, "label": m.label,
                        "seat": len(out),
                    })
        return out

    def seed_entries(self, pid_by_seat: List[Optional[str]]) -> None:
        """Seed a designed bracket from a seat->player assignment aligned with
        ``entry_slots()``. Empty (None) seats are simply left open, so the same call
        renders a partially-filled lobby preview."""
        slots = self.entry_slots()
        if len(pid_by_seat) > len(slots):
            raise ValueError(f"seed_entries got {len(pid_by_seat)} seats but the "
                             f"bracket has {len(slots)}")
        for pid, es in zip(pid_by_seat, slots):
            if pid:
                self.rounds[es["round_index"]][es["match_index"]].player_ids[es["slot"]] = pid
        self._resolve_byes()

    def seed_players(self, ordered_player_ids: List[str], allow_partial: bool = False) -> None:
        """Place players into round-1 slots in the given order (host order or a
        pre-shuffled order). Auto-advances byes.

        By default the count must match the number of opening slots exactly (the
        real seeding at tournament start). Pass ``allow_partial=True`` to seed FEWER
        players than slots — used to render a live lobby PREVIEW where remaining
        seats are still empty (they fill as players join or bots are added)."""
        if self.spec is not None:
            # Designed bracket: seats are typed and may sit in any round, so fill
            # them in entry-slot order rather than assuming round 1.
            slots = self.entry_slots()
            if len(ordered_player_ids) > len(slots):
                raise ValueError(f"seed_players got {len(ordered_player_ids)} players "
                                 f"but only {len(slots)} spots")
            if not allow_partial and len(ordered_player_ids) != len(slots):
                raise ValueError(f"seed_players expected {len(slots)} players, "
                                 f"got {len(ordered_player_ids)}")
            self.seed_entries(list(ordered_player_ids))
            return
        slots = self.opening_slots()
        if len(ordered_player_ids) > len(slots):
            raise ValueError(
                f"seed_players got {len(ordered_player_ids)} players but only "
                f"{len(slots)} slots"
            )
        if not allow_partial and len(ordered_player_ids) != len(slots):
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
    if cfg.is_graph:
        spec = cfg.graph()
        if spec is not None:
            return custom_bracket_summary(spec)
    if cfg.opening_sizes:
        # Custom bracket: the shape is fixed by the host's opening layout and always
        # describes the full field (== total_capacity), independent of n_players.
        sizes = plan_round_sizes(0, cfg.players_per_match, cfg.advance_per_match,
                                 opening_sizes=cfg.opening_sizes)
        n_players = sum(cfg.opening_sizes)
    else:
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
        "is_custom": cfg.is_custom,
        "opening_sizes": list(cfg.opening_sizes) if cfg.opening_sizes else None,
        "num_rounds": len(sizes),
        "num_opening_matches": real_opening,
        "num_byes": byes,
        "num_opening_byes": opening_byes,
        "opening_match_sizes": list(opening),
        "total_matches": total_matches,   # counts byes as (trivial) matches
        "playable_matches": real_matches, # matches an actual game is spawned for
        "advancing_per_match": cfg.advance_per_match,
        "advance_label": advance_label(cfg.advance_per_match),
        "round_sizes": [list(r) for r in sizes],
        "third_place_match": bool(cfg.third_place_match and _makes_third_place_match(cfg, n_players)),
    }


def _makes_third_place_match(cfg: TournamentConfig, n_players: int) -> bool:
    sizes = plan_round_sizes(n_players, cfg.players_per_match, cfg.advance_per_match,
                             opening_sizes=cfg.opening_sizes)
    if len(sizes) < 2:
        return False
    return sizes[-1][0] == 2 and len(sizes[-2]) == 2


def advance_label(advance: int) -> str:
    """How a given advancement rule reads to a human."""
    if advance <= 1:
        return "Winner advances"
    if advance == 2:
        return "Top 2 advance"
    return f"Top {advance} advance"


def advance_options(players_per_match: int, total_capacity: int = MAX_TOURNAMENT_PLAYERS,
                    opening_sizes: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """Every legal "top N advance" rule for a match size, each with the bracket it
    produces. Drives the host's advancement picker; ``advance = 1`` is classic
    single elimination and is always first."""
    biggest = max(opening_sizes) if opening_sizes else int(players_per_match)
    smallest = min(opening_sizes) if opening_sizes else int(players_per_match)
    cap = sum(opening_sizes) if opening_sizes else int(total_capacity)
    out: List[Dict[str, Any]] = []
    for a in range(1, min(smallest - 1, MAX_ADVANCE_PER_MATCH) + 1):
        cfg = TournamentConfig(total_capacity=cap, players_per_match=biggest,
                               advance_per_match=a,
                               opening_sizes=list(opening_sizes) if opening_sizes else None)
        if validate_config(cfg):
            continue
        summ = bracket_summary(cfg, cap)
        out.append({
            "advance_per_match": a,
            "label": advance_label(a),
            "num_rounds": summ["num_rounds"],
            "playable_matches": summ["playable_matches"],
            "num_byes": summ["num_byes"],
            "single_elimination": a == 1,
        })
    return out


def available_formats(total_capacity: int, advance: int = 1) -> List[Dict[str, Any]]:
    """Which players-per-match options are valid for a given capacity, each with a
    preview summary. Used to render the §2 bracket-format picker. Formats too small
    for the requested "top N advance" are still listed, flagged ``advance_ok: False``
    with the best rule they can manage, so the picker can explain itself."""
    out: List[Dict[str, Any]] = []
    for m in range(MIN_PLAYERS_PER_MATCH, MAX_PLAYERS_PER_MATCH + 1):
        if m > total_capacity:
            break
        eff = max(1, min(int(advance or 1), m - 1))
        cfg = TournamentConfig(total_capacity=total_capacity, players_per_match=m,
                               advance_per_match=eff)
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
            "advance_per_match": eff,
            "advance_ok": eff == max(1, int(advance or 1)),
            "max_advance": m - 1,
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
