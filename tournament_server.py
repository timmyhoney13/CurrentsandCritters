#!/usr/bin/env python3
"""Server-authoritative Tournament Mode for Currents & Critters.

This module owns everything tournament-shaped that is NOT pure bracket math
(that lives in tournament_engine.py): the live Tournament object (its own version
counter + condition variable so viewers get SSE updates exactly like a GameRoom),
the lobby/ready/randomize/slot-switch flow, host controls, spawning each bracket
match as a normal GameRoom, feeding match results back into the bracket to advance
rounds, and the idempotent server-authoritative XP grant (modeled on
snap_score._finalize_in_firestore).

It plugs into multiplayer_server.py the same way snap_score does — via init(...)
and handle_get/handle_post — so nothing about the existing game modes changes.

The server is the sole authority: clients never decide winners, advancement, XP,
or placement. Every mutation validates against the bracket + participant roster
and bumps a version so all connected viewers converge on the same state.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
import traceback
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, Callable, Dict, List, Optional, Tuple

import tournament_engine as te
from tournament_engine import (
    TournamentConfig, Bracket, validate_config, validate_opening_sizes, bracket_summary,
    available_formats, compute_player_xp,
    CustomBracket, validate_custom_bracket, custom_bracket_summary,
    SLOT_OPEN, SLOT_HUMAN, SLOT_AI, SLOT_INVITE,
    M_COMPLETE, M_BYE, M_PENDING, M_ACTIVE, M_READY,
    MIN_TOURNAMENT_PLAYERS, MAX_TOURNAMENT_PLAYERS,
    MIN_PLAYERS_PER_MATCH, MAX_PLAYERS_PER_MATCH,
)


def _parse_opening_sizes(raw: Any) -> Optional[List[int]]:
    """Coerce a custom-bracket opening layout from a request into a list of ints.
    Accepts a JSON list ([3,4,2]) or a comma/space string ("3,4,2"); returns None
    when absent/empty so callers fall back to the uniform format."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [x for x in raw.replace(",", " ").split() if x]
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    try:
        sizes = [int(x) for x in raw]
    except (TypeError, ValueError):
        return None
    return sizes or None


def _parse_custom_graph(raw: Any) -> Optional[CustomBracket]:
    """Coerce a canvas-designed bracket from a request body into a CustomBracket.
    Accepts the {"matches":[…]} object or a bare list of matches; returns None when
    absent so callers fall back to the generated bracket."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if isinstance(raw, (list, tuple)):
        raw = {"matches": list(raw)}
    if not isinstance(raw, dict) or not raw.get("matches"):
        return None
    try:
        spec = CustomBracket.from_dict(raw)
    except Exception:  # noqa: BLE001
        return None
    return spec if spec.matches else None


def _graph_config(spec: CustomBracket, body: Dict[str, Any]) -> TournamentConfig:
    """A TournamentConfig sized by the host's design: capacity == the number of
    player spots, players_per_match == the biggest match."""
    return TournamentConfig(
        total_capacity=spec.entry_count(),
        players_per_match=spec.max_capacity(),
        advance_per_match=1,
        third_place_match=False,   # a design decides 3rd place itself, by its shape
        name=str(body.get("name") or "Currents Cup")[:60],
        custom_graph=spec.to_dict(),
    )

# ── Feature flag ─────────────────────────────────────────────────────────────
# Server routes stay live so the host can test via a hidden client URL, but the
# CLIENT hides all tournament UI until it's flipped public. Set FISH_TOURNAMENTS=0
# to hard-disable the server side entirely.
TOURNAMENTS_ENABLED = str(os.environ.get("FISH_TOURNAMENTS", "1")).strip().lower() not in ("0", "false", "no", "")
# When set, the /api/tournament/report HTTP endpoint is live so the automated
# test harness can drive bracket advancement. NEVER set in production — real
# results must only arrive via the trusted on_room_ended game-end hook.
TEST_HOOKS_ENABLED = str(os.environ.get("FISH_TOURNAMENT_TEST_HOOKS", "")).strip().lower() in ("1", "true", "yes")
# Match launch is gated by a per-match READY CHECK: once a match's slots are all
# filled, every assigned human must ready up (bots are auto-ready) before its game
# starts. There is no timed auto-start — a match waits on its players. A human who
# DISCONNECTS during the ready check no longer blocks the launch (so a dropped
# player can't stall the bracket); the host can also forfeit a stuck player.
# A participant whose client hasn't polled state within this window is marked
# disconnected (survives on the server; reconnect restores them). Chosen well
# above the 5s SSE heartbeat so a brief blip doesn't flap.
DISCONNECT_GRACE_SEC = float(os.environ.get("FISH_TOURNAMENT_DISCONNECT_GRACE", "25"))

# ── Injected from multiplayer_server.init ────────────────────────────────────
_get_firestore: Callable[[], Any] = lambda: None
_verify_token: Callable[[str], Optional[dict]] = lambda t: None
_level_progress: Callable[[Any], Tuple[int, int, int]] = lambda xp: (1, 0, 50)
_STATE_DIR: str = "."
# create_match_room(host_name, players, room_id_hint) -> room_id (str)
# reports the room the tournament should send its match players into.
_create_match_room: Optional[Callable[..., Any]] = None
_start_match_room: Optional[Callable[[str], bool]] = None
_get_room: Optional[Callable[[str], Any]] = None


def init(*, get_firestore, verify_token, level_progress, state_dir,
         create_match_room=None, start_match_room=None, get_room=None) -> None:
    global _get_firestore, _verify_token, _level_progress, _STATE_DIR
    global _create_match_room, _start_match_room, _get_room
    _get_firestore = get_firestore
    _verify_token = verify_token
    _level_progress = level_progress
    _STATE_DIR = state_dir
    _create_match_room = create_match_room
    _start_match_room = start_match_room
    _get_room = get_room


# ── Participant / status model (§5) ──────────────────────────────────────────
S_NOT_READY = "not_ready"
S_READY = "ready"
S_PLAYING = "playing"        # Currently Playing
S_WAITING = "waiting"        # Waiting for Next Round
S_ELIMINATED = "eliminated"
S_SPECTATING = "spectating"
S_DISCONNECTED = "disconnected"
S_CHAMPION = "champion"

# Ocean-critter roster used to fill empty tournament seats with AI. Each bot gets
# a distinct name + real avatar; past the roster length the seq number is appended
# so names stay unique in a big (up to 32) bracket.
_BOT_CRITTERS = [
    ("Barracuda", "/avatars/barracuda.png"), ("Clownfish", "/avatars/clownfish.png"),
    ("Hermit Crab", "/avatars/hermit-crab.png"), ("Manta Ray", "/avatars/manta-ray.png"),
    ("Narwhal", "/avatars/narwhal.png"), ("Lobster", "/avatars/lobster.png"),
    ("Cuttlefish", "/avatars/cuttlefish.png"), ("Blue Tang", "/avatars/blue-tang.png"),
    ("Mahi Mahi", "/avatars/mahi-mahi.png"), ("Octopus", "/avatars/common-octopus.png"),
    ("King Crab", "/avatars/king-crab.png"), ("Mantis Shrimp", "/avatars/mantis-shrimp.png"),
    ("Dolphin", "/avatars/bottlenose-dolphin.png"), ("Whale Shark", "/avatars/whale-shark.png"),
    ("Sailfish", "/avatars/sailfish.png"), ("Sea Turtle", "/avatars/loggerhead-sea-turtle.png"),
    ("Grouper", "/avatars/goliath-grouper.png"), ("Amberjack", "/avatars/amberjack.png"),
    ("Tarpon", "/avatars/tarpon.png"), ("Roosterfish", "/avatars/roosterfish.png"),
    ("Cleaner Wrasse", "/avatars/cleaner-wrasse.png"), ("Mandarin Goby", "/avatars/mandarin-goby.png"),
    ("Bobtail Squid", "/avatars/bobtail-squid.png"), ("Yellowfin", "/avatars/yellowfin-tuna.png"),
]


@dataclass
class Participant:
    pid: str                      # stable id: account uid, or guest:<token>, or bot:<token>
    name: str
    avatar: str = ""
    is_guest: bool = True
    is_bot: bool = False          # AI seat-filler: auto-ready, never reaped, no XP
    join_order: int = 0
    ready: bool = False           # lobby ready
    status: str = S_NOT_READY
    token: str = ""               # per-participant secret (their identity)
    connected: bool = True
    last_seen: float = 0.0
    # progress tracking (drives XP)
    matches_won: int = 0
    deepest_round: int = 0
    completed_first_match: bool = False
    quit: bool = False
    final_place: Optional[int] = None
    xp_awarded: bool = False

    def public(self, *, is_host: bool = False, me: bool = False) -> Dict[str, Any]:
        return {
            "pid": self.pid, "name": self.name, "avatar": self.avatar,
            "is_guest": self.is_guest, "is_bot": self.is_bot, "join_order": self.join_order,
            "ready": self.ready, "status": self.status,
            "connected": self.connected, "is_host": is_host, "me": me,
            "matches_won": self.matches_won, "final_place": self.final_place,
        }


def _now() -> float:
    return time.time()


def _gen_token(n: int = 24) -> str:
    return secrets.token_urlsafe(n)


def _code(n: int = 6) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no easily-confused chars
    return "".join(secrets.choice(alphabet) for _ in range(n))


# =============================================================================
# Tournament — one live tournament, server-authoritative, SSE-broadcastable
# =============================================================================
class Tournament:
    PHASE_LOBBY = "lobby"
    PHASE_RUNNING = "running"
    PHASE_COMPLETE = "complete"
    PHASE_CANCELLED = "cancelled"
    PHASE_PAUSED = "paused"

    def __init__(self, tid: str, cfg: TournamentConfig, host_pid: str, host_name: str, *,
                 visibility: str = "public", password_hash: Optional[str] = None,
                 spectators_allowed: bool = True, xp_reward_level: str = "standard",
                 fill_bots: bool = False):
        self.tid = tid
        self.cfg = cfg
        self.host_pid = host_pid
        self.visibility = visibility if visibility in ("public", "private") else "public"
        self.password_hash = password_hash
        self.spectators_allowed = bool(spectators_allowed)
        self.xp_reward_level = xp_reward_level
        # Bot fill: top empty seats up with AI so a short-handed lobby can still run
        # a full bracket. Set at create time (auto-fill on start) and/or triggered
        # by the host in the lobby. Bots are auto-ready, never reaped, earn no XP.
        self.fill_bots = bool(fill_bots)
        self._bot_seq = 0

        self.cond = threading.Condition()
        self.version = 0
        self.phase = self.PHASE_LOBBY
        self.locked = False              # host lock: no new joins
        self.created_unix = _now()
        self.started_unix: Optional[float] = None
        self.ended_unix: Optional[float] = None
        self.status_note = "Tournament lobby open."

        self.host_control_token = _gen_token()
        # Globally-unique ledger/XP scope: the human-facing tid (a 6-char code) is
        # recycled after prune, but this nonce-suffixed id never collides with a
        # historical Firestore rewardLedger key, so a recycled code can't cause a
        # returning player to be silently paid nothing.
        self.xp_scope_id = f"{tid}-{secrets.token_hex(6)}"
        self._last_xp_retry = 0.0
        self.participants: List[Participant] = []
        self.spectator_pids: Dict[str, float] = {}   # pid -> last_seen
        self.bracket: Optional[Bracket] = None
        self.pending_switch: Dict[str, Dict[str, Any]] = {}  # target_pid -> {from_pid, at}
        self.host_log: List[Dict[str, Any]] = []
        self.announcement: Optional[str] = None
        self.champion_pid: Optional[str] = None
        self.final_placements: List[Dict[str, Any]] = []
        # room_id -> (round_index, match_index)
        self.match_rooms: Dict[str, Tuple[int, int]] = {}
        self.match_seat_tokens: Dict[int, Dict[str, str]] = {}   # match_number -> {pid: seat_token}
        self.match_entered_pids: Dict[int, set] = {}                  # match_number -> set(pids present)
        # Per-match READY CHECK: every human assigned to a match must ready up before
        # its game launches (bots are always auto-ready). match_number -> set(pids ready).
        self.match_ready_pids: Dict[int, set] = {}
        self.match_timers: Dict[int, Any] = {}                   # match_number -> threading.Timer
        self.host_pid_order: List[str] = []                      # join order for host reassignment
        self._xp_grants: Dict[str, Dict[str, Any]] = {}   # pid -> computed breakdown
        # Designed (canvas-built) brackets: the fixed list of typed player spots, in
        # seat order. Cached because the shape never changes once created.
        self._seat_slots_cache: Optional[List[Dict[str, Any]]] = None

        # add the host as the first participant
        host = Participant(pid=host_pid, name=host_name, is_guest=host_pid.startswith("guest:"),
                           join_order=0, token=_gen_token(), last_seen=_now())
        self.participants.append(host)

    # ---- broadcast primitive (mirrors GameRoom._bump_locked) ----------------
    def _bump_locked(self) -> None:
        self.version += 1
        self.cond.notify_all()

    def bump(self) -> None:
        with self.cond:
            self._bump_locked()

    def wait_for_update(self, last_version: int, timeout_sec: float = 5.0) -> bool:
        with self.cond:
            if self.version != last_version:
                return True
            self.cond.wait(timeout=timeout_sec)
            return self.version != last_version

    def _log(self, action: str, detail: str = "", by: str = "host") -> None:
        self.host_log.append({"at": _now(), "action": action, "detail": detail, "by": by})
        self.host_log = self.host_log[-200:]

    # ---- lookups ------------------------------------------------------------
    def _by_pid(self, pid: str) -> Optional[Participant]:
        return next((p for p in self.participants if p.pid == pid), None)

    def _by_token(self, token: str) -> Optional[Participant]:
        if not token:
            return None
        return next((p for p in self.participants if p.token and p.token == token), None)

    def is_host(self, *, pid: Optional[str] = None, host_token: str = "") -> bool:
        if host_token and host_token == self.host_control_token:
            return True
        return bool(pid) and pid == self.host_pid

    def _touch_and_reap_locked(self, me: Optional[Participant]) -> None:
        """MUST hold self.cond. Refresh the viewer's presence, mark stale
        participants disconnected, and reassign the host if it vanished."""
        now = _now()
        changed = False
        if me is not None:
            me.last_seen = now
            if not me.connected:
                me.connected = True
                if me.status == S_DISCONNECTED:
                    me.status = S_READY if me.ready else S_NOT_READY
                changed = True
        for p in self.participants:
            if p.is_bot:
                continue  # bots have no client heartbeat — never reap them
            if p.connected and p.last_seen and (now - p.last_seen) > DISCONNECT_GRACE_SEC:
                p.connected = False
                if self.phase == self.PHASE_LOBBY and p.status in (S_NOT_READY, S_READY):
                    p.status = S_DISCONNECTED
                changed = True
        host = self._by_pid(self.host_pid)
        if host is None or not host.connected:
            if self._reassign_host_locked():
                changed = True
        if changed:
            self._bump_locked()

    def _reassign_host_locked(self) -> bool:
        """Give host authority to the earliest-joined connected participant when
        the current host has left/disconnected. Returns True if reassigned."""
        candidates = sorted([p for p in self.participants if p.connected],
                            key=lambda x: x.join_order)
        if candidates and candidates[0].pid != self.host_pid:
            self.host_pid = candidates[0].pid
            self._log("host_reassigned", f"-> {candidates[0].name}")
            return True
        return False

    @property
    def active_participants(self) -> List[Participant]:
        return [p for p in self.participants
                if p.status not in (S_ELIMINATED, S_SPECTATING) and not p.quit]

    # ---- designed-bracket seats (typed player spots) ------------------------
    def seat_slots(self) -> List[Dict[str, Any]]:
        """The designed bracket's player spots in seat order (empty for generated
        brackets, which simply seed round 1 in join order)."""
        if not self.cfg.is_graph:
            return []
        if self._seat_slots_cache is None:
            try:
                self._seat_slots_cache = Bracket.build_custom(self.cfg).entry_slots()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                self._seat_slots_cache = []
        return self._seat_slots_cache

    @property
    def human_capacity(self) -> int:
        """How many real players can join. A designed bracket's AI-only spots are
        never joinable, so its human capacity is below its total capacity."""
        if not self.cfg.is_graph:
            return self.cfg.total_capacity
        return sum(1 for s in self.seat_slots() if s.get("kind") != SLOT_AI)

    def assign_seats(self) -> List[Optional[str]]:
        """Map the current roster onto the designed bracket's typed spots, aligned
        with ``seat_slots()``. Deterministic from the lobby order, so the host's
        drag-to-arrange still decides who sits where:

          1. an invite-only spot goes to the player it names (by name or id)
          2. human-only spots, then unnamed invite spots, then open spots take real
             players in lobby order
          3. AI-only spots take bots, then any open spot still empty takes a bot

        A NAMED invite spot stays empty until its player arrives — that is what
        "reserved" means. can_start says who it is waiting on, and the host can
        release it by filling the lobby with bots.
        """
        slots = self.seat_slots()
        if not slots:
            return []
        ordered = sorted(self.participants, key=lambda x: x.join_order)
        humans = [p for p in ordered if not p.is_bot]
        bots = [p for p in ordered if p.is_bot]
        out: List[Optional[str]] = [None] * len(slots)

        claimed: set = set()
        for i, s in enumerate(slots):
            if s.get("kind") != SLOT_INVITE or not s.get("invite"):
                continue
            want = str(s["invite"]).strip().lower()
            who = next((p for p in humans if p.pid not in claimed
                        and (p.name.strip().lower() == want or p.pid == s["invite"])), None)
            if who is not None:
                out[i] = who.pid
                claimed.add(who.pid)
        free_humans = [p for p in humans if p.pid not in claimed]
        free_bots = list(bots)

        def fill(kinds: Tuple[str, ...], pool: List[Participant], named_ok: bool = True) -> None:
            for i, s in enumerate(slots):
                if out[i] or s.get("kind") not in kinds or not pool:
                    continue
                if not named_ok and s.get("invite"):
                    continue    # reserved for someone specific — hold it open
                out[i] = pool.pop(0).pid
        fill((SLOT_HUMAN,), free_humans)
        fill((SLOT_INVITE,), free_humans, named_ok=False)
        fill((SLOT_OPEN,), free_humans)
        fill((SLOT_AI,), free_bots)
        fill((SLOT_OPEN, SLOT_INVITE), free_bots)
        return out

    def seat_view(self) -> List[Dict[str, Any]]:
        """Seat plan for the lobby: who (if anyone) holds each designed spot."""
        slots = self.seat_slots()
        if not slots:
            return []
        assign = self.assign_seats()
        by_pid = {p.pid: p for p in self.participants}
        out = []
        for s, pid in zip(slots, assign):
            p = by_pid.get(pid) if pid else None
            out.append({
                "seat": s["seat"], "kind": s["kind"], "invite": s.get("invite", ""),
                "match_number": s["match_number"], "label": s.get("label", ""),
                "slot": s["slot"],
                "pid": pid, "name": p.name if p else "", "avatar": p.avatar if p else "",
                "is_bot": bool(p and p.is_bot),
            })
        return out

    def _empty_seats(self) -> List[Dict[str, Any]]:
        """Designed spots nobody holds yet, with their slot metadata."""
        slots = self.seat_slots()
        if not slots:
            return []
        return [s for s, pid in zip(slots, self.assign_seats()) if not pid]

    # ---- join / leave -------------------------------------------------------
    def join(self, pid: str, name: str, avatar: str = "", is_guest: bool = True) -> Dict[str, Any]:
        with self.cond:
            existing = self._by_pid(pid)
            if existing is not None:
                existing.connected = True
                existing.last_seen = _now()
                if existing.status == S_DISCONNECTED:
                    existing.status = S_READY if existing.ready else S_NOT_READY
                self._bump_locked()
                return {"ok": True, "token": existing.token, "rejoin": True}
            if self.phase != self.PHASE_LOBBY:
                # tournament already started — join as spectator if allowed
                if self.spectators_allowed:
                    self.spectator_pids[pid] = _now()
                    self._bump_locked()
                    return {"ok": True, "spectator": True}
                return {"ok": False, "error": "tournament already started"}
            if self.locked:
                return {"ok": False, "error": "tournament is locked by the host"}
            # A designed bracket's AI-only spots are not joinable, so real players
            # are capped by its human capacity rather than its total capacity.
            human_cap = self.human_capacity
            joined_humans = sum(1 for p in self.participants if not p.is_bot)
            # A lobby topped up with bots must never lock real players out: if a
            # spot a person could take is being held by a bot, the bot steps aside.
            if joined_humans < human_cap and len(self.participants) >= self.cfg.total_capacity:
                self._evict_bot_for_human_locked()
            if joined_humans >= human_cap or len(self.participants) >= self.cfg.total_capacity:
                if self.spectators_allowed:
                    self.spectator_pids[pid] = _now()
                    self._bump_locked()
                    return {"ok": True, "spectator": True, "full": True}
                return {"ok": False, "error": "tournament is full"}
            p = Participant(pid=pid, name=name, avatar=avatar, is_guest=is_guest,
                            join_order=len(self.participants), token=_gen_token(), last_seen=_now())
            self.participants.append(p)
            self._bump_locked()
            return {"ok": True, "token": p.token}

    def leave(self, pid: str) -> Dict[str, Any]:
        with self.cond:
            p = self._by_pid(pid)
            if p is None:
                self.spectator_pids.pop(pid, None)
                self._bump_locked()
                return {"ok": True}
            if self.phase == self.PHASE_LOBBY:
                # simply remove from the lobby
                was_host = (pid == self.host_pid)
                self.participants = [x for x in self.participants if x.pid != pid]
                for i, x in enumerate(self.participants):
                    x.join_order = i
                if was_host:
                    self._reassign_host_locked()   # keep the lobby operable
                self._bump_locked()
                return {"ok": True, "removed": True}
            # mid-tournament leave == forfeit (no no-quit bonus)
            p.quit = True
            was_active = p.status not in (S_ELIMINATED, S_CHAMPION)
            p.status = S_ELIMINATED
            self._log("player_forfeit", f"{p.name} left the tournament", by=p.pid)
            self._forfeit_active_matches_for(p)
            self._bump_locked()
        # A forfeit can fill the next match or complete the final — progress the
        # bracket + finalize OUTSIDE the lock (both take their own locks).
        self._spawn_ready_matches()
        self._maybe_finalize()
        return {"ok": True, "forfeit": True, "was_active": was_active}

    def set_ready(self, pid: str, ready: bool) -> Dict[str, Any]:
        with self.cond:
            p = self._by_pid(pid)
            if p is None:
                return {"ok": False, "error": "not in tournament"}
            if self.phase != self.PHASE_LOBBY:
                return {"ok": False, "error": "tournament already started"}
            p.ready = bool(ready)
            p.status = S_READY if ready else S_NOT_READY
            self._bump_locked()
            return {"ok": True}

    # ---- bots (fill empty seats with AI) ------------------------------------
    def _make_bot(self) -> Participant:
        name, avatar = _BOT_CRITTERS[self._bot_seq % len(_BOT_CRITTERS)]
        tier = self._bot_seq // len(_BOT_CRITTERS)
        if tier:
            name = f"{name} {tier + 1}"
        self._bot_seq += 1
        return Participant(
            pid=f"bot:{secrets.token_hex(6)}", name=f"{name} 🤖", avatar=avatar,
            is_guest=True, is_bot=True, join_order=len(self.participants),
            token=_gen_token(), last_seen=_now(), connected=True,
            ready=True, status=S_READY,
        )

    def _fill_designed_seats_locked(self) -> int:
        """MUST hold self.cond. Add exactly enough bots to take every designed spot
        a bot is allowed to hold (AI-only spots first, then any open spot no real
        player claimed). Human-only spots are left empty — only a person can take
        one, and can_start says so."""
        added = 0
        for _ in range(len(self.seat_slots()) + 1):
            need = [s for s in self._empty_seats() if s.get("kind") != SLOT_HUMAN]
            if not need:
                break
            self.participants.append(self._make_bot())
            added += 1
        return added

    def fill_with_bots(self, target: Optional[int] = None) -> Dict[str, Any]:
        """Top the lobby up to `target` (default: total_capacity) with AI players.
        Lobby-only; returns how many were added."""
        with self.cond:
            if self.phase != self.PHASE_LOBBY:
                return {"ok": False, "error": "can only add bots before start"}
            if self.cfg.is_graph and target is None:
                added = self._fill_designed_seats_locked()
                if added:
                    self._log("fill_bots", f"added {added} bot(s)")
                    self._bump_locked()
                return {"ok": True, "added": added, "joined": len(self.participants)}
            cap = self.cfg.total_capacity
            want = cap if target is None else max(0, min(cap, int(target)))
            added = 0
            while len(self.participants) < want:
                self.participants.append(self._make_bot())
                added += 1
            if added:
                self._log("fill_bots", f"added {added} bot(s)")
                self._bump_locked()
            return {"ok": True, "added": added, "joined": len(self.participants)}

    def _evict_bot_for_human_locked(self) -> bool:
        """MUST hold self.cond. Drop one bot so an arriving player has a spot.

        Bots are seat-fillers, never seat-holders — a host who pre-filled the lobby
        should not have shut the door on their friends. A designed bracket keeps
        exactly as many bots as its AI-only spots require; those are the host's
        deliberate "put a bot here", not filler."""
        if self.phase != self.PHASE_LOBBY:
            return False
        bots = [p for p in self.participants if p.is_bot]
        if not bots:
            return False
        if self.cfg.is_graph:
            needed = sum(1 for s in self.seat_slots() if s.get("kind") == SLOT_AI)
            if len(bots) <= needed:
                return False
        victim = bots[-1]
        self.participants = [p for p in self.participants if p.pid != victim.pid]
        for i, x in enumerate(self.participants):
            x.join_order = i
        self._log("bot_evicted", f"{victim.name} made way for a player")
        return True

    def _match_is_all_bots(self, m) -> bool:
        pids = [pid for pid in m.player_ids if pid]
        if not pids:
            return False
        return all((self._by_pid(pid) or Participant(pid=pid, name="")).is_bot for pid in pids)

    def _resolve_bot_match(self, m) -> bool:
        """Instantly decide an all-bot match server-side (no GameRoom needed).
        Records the result under the lock WITHOUT re-entering _spawn_ready_matches
        (the caller loops). Returns True if it resolved something."""
        import random
        with self.cond:
            if m.status in (M_READY, M_ACTIVE, M_COMPLETE, M_BYE) or m.room_id:
                return False
            if m.is_bye or m.filled_count() != m.capacity:
                return False
            parts = [self._by_pid(pid) for pid in m.player_ids if pid]
            parts = [p for p in parts if p is not None]
            # Random order, but anyone who quit ranks last.
            random.shuffle(parts)
            parts.sort(key=lambda p: 1 if p.quit else 0)
            ranking = [p.pid for p in parts]
            scores = {pid: (len(ranking) - i) * 100 for i, pid in enumerate(ranking)}
            try:
                self._record_and_advance(m, ranking, scores)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return False
            self._bump_locked()
            return True

    # ---- randomize / switch (§3, §4) ----------------------------------------
    def randomize(self, rng_seed: Optional[int] = None) -> Dict[str, Any]:
        with self.cond:
            if self.phase != self.PHASE_LOBBY:
                return {"ok": False, "error": "can only randomize before start"}
            import random
            rng = random.Random(rng_seed) if rng_seed is not None else random.SystemRandom()
            order = list(self.participants)
            rng.shuffle(order)
            for i, p in enumerate(order):
                p.join_order = i
            self.participants = sorted(self.participants, key=lambda x: x.join_order)
            self.pending_switch.clear()
            self._log("randomize", f"shuffled {len(self.participants)} players")
            self._bump_locked()
            return {"ok": True, "order": [p.pid for p in self.participants]}

    def request_switch(self, from_pid: str, to_pid: str) -> Dict[str, Any]:
        with self.cond:
            if self.phase != self.PHASE_LOBBY:
                return {"ok": False, "error": "cannot switch after start"}
            a, b = self._by_pid(from_pid), self._by_pid(to_pid)
            if a is None or b is None:
                return {"ok": False, "error": "player not found"}
            if from_pid == to_pid:
                return {"ok": False, "error": "cannot switch with yourself"}
            self.pending_switch[to_pid] = {"from_pid": from_pid, "at": _now()}
            self._bump_locked()
            return {"ok": True}

    def respond_switch(self, target_pid: str, accept: bool) -> Dict[str, Any]:
        with self.cond:
            req = self.pending_switch.pop(target_pid, None)
            if req is None:
                return {"ok": False, "error": "no pending switch request"}
            from_pid = req["from_pid"]
            if not accept:
                self._bump_locked()
                return {"ok": True, "accepted": False, "from_pid": from_pid}
            a, b = self._by_pid(from_pid), self._by_pid(target_pid)
            if a is None or b is None:
                return {"ok": False, "error": "player left"}
            a.join_order, b.join_order = b.join_order, a.join_order
            self.participants = sorted(self.participants, key=lambda x: x.join_order)
            self._log("switch", f"{a.name} <-> {b.name}", by=from_pid)
            self._bump_locked()
            return {"ok": True, "accepted": True, "from_pid": from_pid}

    def host_move(self, pid: str, new_index: int) -> Dict[str, Any]:
        """Host manually moves a player to a new opening-order index (§13)."""
        with self.cond:
            if self.phase != self.PHASE_LOBBY:
                return {"ok": False, "error": "cannot move after start"}
            p = self._by_pid(pid)
            if p is None:
                return {"ok": False, "error": "player not found"}
            new_index = max(0, min(len(self.participants) - 1, int(new_index)))
            self.participants = [x for x in self.participants if x.pid != pid]
            self.participants.insert(new_index, p)
            for i, x in enumerate(self.participants):
                x.join_order = i
            self._log("host_move", f"{p.name} -> slot {new_index}")
            self._bump_locked()
            return {"ok": True, "host_controlled": True}

    def host_swap(self, pid_a: str, pid_b: str) -> Dict[str, Any]:
        """Host directly swaps two players' bracket positions — no consent needed
        (unlike request_switch). Drives the host drag-swap in the lobby + preview
        bracket. Swapping join_order swaps which opening slot each player seeds into."""
        with self.cond:
            if self.phase != self.PHASE_LOBBY:
                return {"ok": False, "error": "cannot swap after start"}
            if not pid_a or not pid_b or pid_a == pid_b:
                return {"ok": False, "error": "pick two different players"}
            a, b = self._by_pid(pid_a), self._by_pid(pid_b)
            if a is None or b is None:
                return {"ok": False, "error": "player not found"}
            a.join_order, b.join_order = b.join_order, a.join_order
            self.participants = sorted(self.participants, key=lambda x: x.join_order)
            self.pending_switch.clear()
            self._log("host_swap", f"{a.name} <-> {b.name}")
            self._bump_locked()
            return {"ok": True, "host_controlled": True}

    def host_remove(self, pid: str) -> Dict[str, Any]:
        with self.cond:
            if self.phase != self.PHASE_LOBBY:
                return {"ok": False, "error": "cannot remove after start"}
            p = self._by_pid(pid)
            if p is None or p.pid == self.host_pid:
                return {"ok": False, "error": "cannot remove"}
            self.participants = [x for x in self.participants if x.pid != pid]
            for i, x in enumerate(self.participants):
                x.join_order = i
            self._log("host_remove", f"removed {p.name}")
            self._bump_locked()
            return {"ok": True}

    def host_set(self, *, name=None, spectators_allowed=None, locked=None) -> Dict[str, Any]:
        with self.cond:
            if name is not None:
                self.cfg.name = str(name)[:60]
                self._log("rename", self.cfg.name)
            if spectators_allowed is not None:
                self.spectators_allowed = bool(spectators_allowed)
                self._log("spectators", str(self.spectators_allowed))
            if locked is not None:
                self.locked = bool(locked)
                self._log("lock", str(self.locked))
            self._bump_locked()
            return {"ok": True}

    def announce(self, text: str) -> Dict[str, Any]:
        with self.cond:
            self.announcement = str(text)[:280]
            self._log("announce", self.announcement)
            self._bump_locked()
            return {"ok": True}

    def pause(self, paused: bool) -> Dict[str, Any]:
        with self.cond:
            if paused and self.phase == self.PHASE_RUNNING:
                self.phase = self.PHASE_PAUSED
                self._log("pause")
            elif not paused and self.phase == self.PHASE_PAUSED:
                self.phase = self.PHASE_RUNNING
                self._log("resume")
            self._bump_locked()
            return {"ok": True}

    def cancel(self) -> Dict[str, Any]:
        with self.cond:
            self.phase = self.PHASE_CANCELLED
            self.ended_unix = _now()
            self.status_note = "Tournament cancelled by host."
            self._log("cancel")
            self._bump_locked()
            return {"ok": True}

    # ---- start (§5 gate, §6 spawn) -----------------------------------------
    def can_start(self) -> Tuple[bool, str]:
        if self.phase != self.PHASE_LOBBY:
            return False, "already started"
        n = len(self.participants)
        # A DESIGNED bracket is checked against its typed spots: every spot must have
        # someone in it, and only a real player can take a human-only spot.
        if self.cfg.is_graph:
            # AI-only spots are never a blocker — start() always puts a bot in them,
            # because that spot type IS the host saying "a bot plays here".
            empty = [s for s in self._empty_seats() if s.get("kind") != SLOT_AI]
            if empty:
                humans_only = [s for s in empty if s.get("kind") == SLOT_HUMAN]
                invites = [s for s in empty if s.get("kind") == SLOT_INVITE and s.get("invite")]
                if humans_only:
                    where = ", ".join(f"Match {s['match_number']} spot {s['slot'] + 1}"
                                      for s in humans_only[:3])
                    return False, (f"{len(humans_only)} player-only spot(s) still empty "
                                   f"({where}) — they need real players")
                if invites:
                    who = ", ".join(str(s["invite"]) for s in invites[:3])
                    return False, (f"waiting on invited player(s): {who} "
                                   "(or fill their spot with a bot)")
                return False, (f"{len(empty)} spot(s) still empty — add players "
                               "or fill them with bots")
            not_ready = [p for p in self.participants if not p.ready and p.connected]
            if not_ready:
                return False, f"{len(not_ready)} player(s) not ready"
            errs = validate_config(self.cfg)
            return (False, "; ".join(errs)) if errs else (True, "")
        if n < MIN_TOURNAMENT_PLAYERS:
            return False, f"need at least {MIN_TOURNAMENT_PLAYERS} players (have {n})"
        if n < self.cfg.players_per_match:
            return False, "not enough players for one match"
        # A CUSTOM bracket has a fixed shape sized for total_capacity, so every seat
        # must be filled (invite more players or let bots fill them) before it starts,
        # otherwise a designed match would be short a player and never spawn.
        if self.cfg.is_custom and n != self.cfg.total_capacity:
            need = self.cfg.total_capacity - n
            if need > 0:
                return False, (f"custom bracket needs all {self.cfg.total_capacity} seats "
                               f"filled ({need} empty — add players or fill with bots)")
            return False, f"custom bracket holds {self.cfg.total_capacity} players (have {n})"
        not_ready = [p for p in self.participants if not p.ready and p.connected]
        if not_ready:
            return False, f"{len(not_ready)} player(s) not ready"
        errs = validate_config(self.cfg)
        if errs:
            return False, "; ".join(errs)
        return True, ""

    def start(self) -> Dict[str, Any]:
        # If bot-fill is on, top the bracket up to capacity before the start gate so
        # a short-handed lobby (even a solo host) can launch a full tournament.
        if self.fill_bots and self.phase == self.PHASE_LOBBY \
                and len(self.participants) < self.cfg.total_capacity:
            self.fill_with_bots()
        # A designed bracket's AI-only spots ALWAYS need a bot, whether or not the
        # host asked for bot-fill — that spot type is the host saying "put a bot here".
        if self.cfg.is_graph and self.phase == self.PHASE_LOBBY:
            with self.cond:
                if any(s.get("kind") == SLOT_AI for s in self._empty_seats()):
                    for _ in range(len(self.seat_slots()) + 1):
                        if not any(s.get("kind") == SLOT_AI for s in self._empty_seats()):
                            break
                        self.participants.append(self._make_bot())
                    self._bump_locked()
        with self.cond:
            ok, why = self.can_start()
            if not ok:
                return {"ok": False, "error": why}
            n = len(self.participants)
            if self.cfg.is_graph:
                self.bracket = Bracket.build_custom(self.cfg)
                self.bracket.seed_entries(self.assign_seats())
            else:
                self.bracket = Bracket.build(self.cfg, n)
                order = [p.pid for p in sorted(self.participants, key=lambda x: x.join_order)]
                self.bracket.seed_players(order)
            self.phase = self.PHASE_RUNNING
            self.started_unix = _now()
            self.status_note = "Tournament in progress."
            for p in self.participants:
                p.status = S_WAITING
            self._log("start", f"{n} players, {self.bracket.n_rounds} rounds")
            self._bump_locked()
        # spawn all currently-playable matches (round 1). Byes were auto-resolved.
        self._spawn_ready_matches()
        return {"ok": True}

    # ---- match orchestration ------------------------------------------------
    def _playable_matches(self):
        """Matches whose slots are all filled, not yet running/complete."""
        if not self.bracket:
            return []
        out = []
        for row in self.bracket.rounds:
            for m in row:
                if m.status in (M_COMPLETE, M_BYE, M_ACTIVE, M_READY):
                    continue
                if m.room_id:
                    continue
                if m.is_bye:
                    continue
                if m.filled_count() == m.capacity:
                    out.append(m)
        tp = self.bracket.third_place
        if tp is not None and tp.status not in (M_COMPLETE, M_BYE) and not tp.room_id \
                and tp.filled_count() == tp.capacity and tp.capacity >= 2:
            out.append(tp)
        return out

    def _spawn_ready_matches(self) -> None:
        # Resolve all-bot matches instantly (each can unlock the next → loop), and
        # spawn real GameRooms for any match that includes at least one human.
        for _ in range(1000):  # guard against a pathological loop
            playable = self._playable_matches()
            if not playable:
                break
            bot_only = [m for m in playable if self._match_is_all_bots(m)]
            if bot_only:
                progressed = False
                for m in bot_only:
                    progressed = self._resolve_bot_match(m) or progressed
                if progressed:
                    continue          # re-scan: a resolution may have unlocked more
            for m in [m for m in playable if not self._match_is_all_bots(m)]:
                self._spawn_match(m)
            break
        # An all-bot bracket can complete here with no room ever ending, so finalize
        # opportunistically (idempotent; report_match_result also calls it).
        self._maybe_finalize()

    def _spawn_match(self, m) -> None:
        # Atomic claim under the lock so two threads (two matches finishing at the
        # same time, each triggering _spawn_ready_matches) can never double-spawn
        # the same next-round match into two GameRooms.
        with self.cond:
            if m.status in (M_READY, M_ACTIVE, M_COMPLETE, M_BYE) or m.room_id:
                return
            if m.is_bye or m.filled_count() != m.capacity:
                return
            parts = [self._by_pid(pid) for pid in m.player_ids if pid]
            parts = [p for p in parts if p is not None]
            m.status = M_READY          # claim it before releasing the lock
            for p in parts:
                if not p.quit:          # never resurrect a player who forfeited
                    p.status = S_PLAYING
            self._bump_locked()
        if _create_match_room is None:
            # test / headless mode: no real room, results are fed directly.
            return
        try:
            res = _create_match_room(
                tournament_id=self.tid,
                round_index=m.round_index, match_index=m.match_index,
                match_number=m.match_number,
                players=[{"pid": p.pid, "name": p.name, "token": p.token,
                          "is_guest": p.is_guest, "is_bot": p.is_bot} for p in parts],
                spectators_allowed=self.spectators_allowed,
            )
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            with self.cond:
                self.status_note = f"match spawn error: {exc}"
                self._bump_locked()
            return
        room_id = res.get("room_id") if isinstance(res, dict) else res
        seat_tokens = res.get("seat_tokens", {}) if isinstance(res, dict) else {}
        launch_now = False
        with self.cond:
            m.room_id = room_id
            if room_id:
                self.match_rooms[room_id] = (m.round_index, m.match_index)
            self.match_seat_tokens[m.match_number] = dict(seat_tokens or {})
            entered = self.match_entered_pids.setdefault(m.match_number, set())
            ready = self.match_ready_pids.setdefault(m.match_number, set())
            # Bots have no client to "enter" or "ready up", so pre-mark them present
            # AND ready — the match then launches the instant every human is ready.
            entered.update(p.pid for p in parts if p.is_bot)
            ready.update(p.pid for p in parts if p.is_bot)
            # Stays M_READY (waiting on the human ready-check) until everyone readies.
            self._bump_locked()
            # If every human was already ready (e.g. seats readied during the brief
            # window before the room existed), launch straight away.
            launch_now = self._match_all_ready_locked(m)
        if launch_now:
            self._launch_match(m.round_index, m.match_index)

    def _match_all_ready_locked(self, m) -> bool:
        """MUST hold self.cond. True when the match can launch: it has a room, and
        every CONNECTED human assigned to it has readied up (bots are auto-ready;
        a disconnected human doesn't block so a dropped player can't stall the
        bracket). At least one human must be ready so an all-absent match never
        launches into an empty game."""
        if m.status != M_READY or not m.room_id:
            return False
        assigned = [self._by_pid(pid) for pid in m.player_ids if pid]
        assigned = [p for p in assigned if p is not None]
        if not assigned:
            return False
        ready = self.match_ready_pids.get(m.match_number, set())
        humans = [p for p in assigned if not p.is_bot]
        for p in humans:
            if p.connected and p.pid not in ready:
                return False
        return any(p.pid in ready for p in humans)

    def match_ready(self, pid: str, ready: bool = True) -> Dict[str, Any]:
        """A player marks themselves ready (or not) for their current M_READY match.
        The match's game launches the moment every assigned human is ready."""
        if not self.bracket:
            return {"ok": False, "error": "tournament not started"}
        launch = None
        with self.cond:
            me = self._by_pid(pid)
            if me is None:
                return {"ok": False, "error": "not in tournament"}
            target = None
            for row in self.bracket.rounds:
                for m in row:
                    if m.status == M_READY and pid in [x for x in m.player_ids if x]:
                        target = m
                        break
                if target:
                    break
            tp = self.bracket.third_place
            if target is None and tp is not None and tp.status == M_READY \
                    and pid in [x for x in tp.player_ids if x]:
                target = tp
            if target is None:
                return {"ok": False, "error": "no match is waiting for you to ready up"}
            s = self.match_ready_pids.setdefault(target.match_number, set())
            if ready:
                s.add(pid)
            else:
                s.discard(pid)
            self._bump_locked()
            if self._match_all_ready_locked(target):
                launch = (target.round_index, target.match_index)
        if launch is not None:
            self._launch_match(*launch)
        return {"ok": True, "ready": bool(ready)}

    def _launch_match(self, r: int, mi: int) -> None:
        with self.cond:
            try:
                m = self.bracket.get_match(r, mi)
            except Exception:
                return
            if m.status != M_READY or not m.room_id:
                return
            room_id = m.room_id
            tmr = self.match_timers.pop(m.match_number, None)
        if tmr:
            tmr.cancel()
        ok = _start_match_room(room_id) if _start_match_room else False
        with self.cond:
            if ok and m.status == M_READY:
                m.status = M_ACTIVE
                self._bump_locked()

    def match_entered(self, pid: str) -> Dict[str, Any]:
        """A player opened their assigned match room. Entering counts as readying up
        (so a player who deep-links straight into their room still satisfies the
        ready check), but the game only launches once EVERY assigned human is ready —
        entering never bypasses another player's ready-up."""
        if not self.bracket:
            return {"ok": False, "error": "not started"}
        launch = None
        with self.cond:
            target = None
            for row in self.bracket.rounds:
                for m in row:
                    if m.status == M_READY and pid in [x for x in m.player_ids if x]:
                        target = m
                        break
                if target:
                    break
            tp = self.bracket.third_place
            if target is None and tp is not None and tp.status == M_READY \
                    and pid in [x for x in tp.player_ids if x]:
                target = tp
            if target is None:
                return {"ok": True}
            self.match_entered_pids.setdefault(target.match_number, set()).add(pid)
            self.match_ready_pids.setdefault(target.match_number, set()).add(pid)
            self._bump_locked()
            if self._match_all_ready_locked(target):
                launch = (target.round_index, target.match_index)
        if launch is not None:
            self._launch_match(*launch)
        return {"ok": True}

    def _cancel_match_timer(self, match_number: int) -> None:
        tmr = self.match_timers.pop(match_number, None)
        if tmr:
            try:
                tmr.cancel()
            except Exception:  # noqa: BLE001
                pass

    def _forfeit_active_matches_for(self, p: Participant) -> None:
        """If a player forfeits mid-match, their opponents advance. If the match
        hasn't finished, the highest-remaining player is credited the win."""
        if not self.bracket:
            return
        for row in self.bracket.rounds:
            for m in row:
                if m.status in (M_COMPLETE, M_BYE):
                    continue
                if p.pid in [x for x in m.player_ids if x]:
                    others = [x for x in m.player_ids if x and x != p.pid]
                    if others and m.filled_count() == m.capacity:
                        # decide by forfeit: remaining players ranked, forfeiter last
                        ranking = others + [p.pid]
                        try:
                            self._record_and_advance(m, ranking, {}, forfeit_pid=p.pid)
                        except Exception:  # noqa: BLE001
                            traceback.print_exc()

    def report_match_result(self, round_index: int, match_index: int,
                            ranking: List[str], scores: Optional[Dict[str, int]] = None,
                            *, room_id: Optional[str] = None) -> Dict[str, Any]:
        """Server-authoritative result intake (§15). Validates then advances."""
        with self.cond:
            if not self.bracket:
                return {"ok": False, "error": "tournament not started"}
            try:
                m = self.bracket.get_match(round_index, match_index)
            except Exception:
                return {"ok": False, "error": "no such match"}
            if m.status in (M_COMPLETE, M_BYE):
                return {"ok": False, "error": "match already complete"}  # dup guard
            if room_id and m.room_id and room_id != m.room_id:
                return {"ok": False, "error": "result does not belong to this match"}
            present = sorted([p for p in m.player_ids if p])
            if sorted(ranking) != present:
                return {"ok": False, "error": "ranking players do not match this match"}
            try:
                self._record_and_advance(m, ranking, scores or {})
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            self._bump_locked()
        # spawning + finalize happen outside the lock (they take their own locks)
        self._spawn_ready_matches()
        self._maybe_finalize()
        return {"ok": True}

    def _record_and_advance(self, m, ranking: List[str], scores: Dict[str, int],
                            forfeit_pid: Optional[str] = None) -> None:
        """MUST be called under self.cond. Records a result + updates progress."""
        self.bracket.record_result(m.round_index, m.match_index, ranking, scores)
        winner = m.winners[0] if m.winners else None
        # progress tracking for XP
        for pid in ranking:
            p = self._by_pid(pid)
            if not p:
                continue
            p.completed_first_match = True
            p.deepest_round = max(p.deepest_round, m.round_index)
        wp = self._by_pid(winner) if winner else None
        if wp and not m.is_third_place:
            wp.matches_won += 1
            wp.deepest_round = max(wp.deepest_round, m.round_index + 1)
        # losers of this match are eliminated (unless they advanced)
        advancing = set(m.winners)
        for pid in ranking:
            if pid in advancing:
                continue
            lp = self._by_pid(pid)
            if lp and lp.status not in (S_ELIMINATED, S_CHAMPION):
                lp.status = S_ELIMINATED
                lp.final_place = None
        # winner: waiting for next round (or champion decided at finalize)
        if wp:
            wp.status = S_WAITING
        if m.room_id:
            self.match_rooms.pop(m.room_id, None)
        self._cancel_match_timer(m.match_number)   # no auto-start once resolved

    def _maybe_finalize(self) -> None:
        with self.cond:
            if not self.bracket or self.phase in (self.PHASE_COMPLETE, self.PHASE_CANCELLED):
                return
            if not self.bracket.is_complete():
                return
            self.phase = self.PHASE_COMPLETE
            self.ended_unix = _now()
            self.champion_pid = self.bracket.champion
            placements = self.bracket.final_placements()
            self.final_placements = placements
            for entry in placements:
                p = self._by_pid(entry["player_id"])
                if p:
                    p.final_place = entry["place"]
                    p.status = S_CHAMPION if entry["place"] == 1 else S_ELIMINATED
            self.status_note = "Tournament complete."
            self._log("complete", f"champion={self.champion_pid}")
            self._bump_locked()
        # compute + grant XP for everyone (idempotent)
        self._finalize_all_xp()

    # ---- XP (§12) -----------------------------------------------------------
    def compute_xp_for(self, p: Participant) -> Dict[str, Any]:
        n = len(self.participants)
        rounds_total = self.bracket.n_rounds if self.bracket else 1
        place = p.final_place or (self.bracket.final_placements() and
                                  next((e["place"] for e in self.bracket.final_placements()
                                        if e["player_id"] == p.pid), 0)) or 0
        completed_no_quit = (not p.quit) and self.phase == self.PHASE_COMPLETE
        # distinct real (non-guest) accounts that actually played — anti-farm gate
        real_accounts = len({q.pid for q in self.participants
                             if not q.is_guest and q.completed_first_match})
        return compute_player_xp(
            self.xp_scope_id, p.pid,
            n_players=n, place=int(place or 0),
            matches_won=p.matches_won, rounds_total=rounds_total,
            deepest_round=p.deepest_round,
            completed_first_match=p.completed_first_match,
            completed_without_quitting=completed_no_quit,
            real_accounts=real_accounts,
        )

    def _finalize_all_xp(self) -> None:
        for p in list(self.participants):
            if p.is_bot:
                continue  # AI seat-fillers never earn XP (no account to credit)
            if p.xp_awarded or not p.completed_first_match:
                continue
            breakdown = self.compute_xp_for(p)
            self._xp_grants[p.pid] = breakdown
            ok = _grant_xp_firestore(self.xp_scope_id, p, breakdown)
            with self.cond:
                if ok:
                    p.xp_awarded = True
                self._bump_locked()

    def retry_unfinalized_xp(self) -> None:
        """Re-attempt XP grants that a transient Firestore error dropped. Idempotent
        (the reward ledger skips already-granted players), so safe to call repeatedly."""
        if self.phase != self.PHASE_COMPLETE:
            return
        if any(p.completed_first_match and not p.xp_awarded for p in self.participants):
            self._finalize_all_xp()

    def _maybe_retry_xp_async(self) -> None:
        """Throttled background reconciliation, triggered from state reads."""
        if self.phase != self.PHASE_COMPLETE:
            return
        now = _now()
        if now - self._last_xp_retry < 30:
            return
        if not any(p.completed_first_match and not p.xp_awarded for p in self.participants):
            return
        self._last_xp_retry = now
        threading.Thread(target=self.retry_unfinalized_xp, daemon=True).start()

    # ---- external hook: a GameRoom running our match ended ------------------
    def on_room_ended(self, room_id: str, ranking: List[str], scores: Dict[str, int]) -> Dict[str, Any]:
        loc = self.match_rooms.get(room_id)
        if loc is None:
            return {"ok": False, "error": "room not part of this tournament"}
        r, mi = loc
        return self.report_match_result(r, mi, ranking, scores, room_id=room_id)

    # ---- per-viewer state (SSE payload) ------------------------------------
    def state_view(self, *, viewer_pid: Optional[str] = None, host_token: str = "") -> Dict[str, Any]:
        self._maybe_retry_xp_async()   # opportunistic XP reconciliation (throttled)
        with self.cond:
            me = self._by_pid(viewer_pid) if viewer_pid else None
            self._touch_and_reap_locked(me)
            is_host = self.is_host(pid=viewer_pid, host_token=host_token)
            ordered = sorted(self.participants, key=lambda x: x.join_order)
            parts = [p.public(is_host=(p.pid == self.host_pid), me=(me is not None and p.pid == me.pid))
                     for p in ordered]
            summary = None
            try:
                summary_n = (self.cfg.total_capacity
                             if (self.fill_bots or self.cfg.is_custom)
                             else max(len(self.participants), self.cfg.players_per_match))
                summary = bracket_summary(self.cfg, summary_n)
            except Exception:
                summary = None
            # In the lobby, show a live PREVIEW bracket (real bracket once running).
            if self.phase == self.PHASE_LOBBY and not self.bracket:
                bracket_payload = self._preview_bracket()
            else:
                bracket_payload = self._bracket_view()
            payload: Dict[str, Any] = {
                "version": self.version,
                "tournament_id": self.tid,
                "name": self.cfg.name,
                "phase": self.phase,
                "locked": self.locked,
                "visibility": self.visibility,
                "spectators_allowed": self.spectators_allowed,
                "fill_bots": self.fill_bots,
                "config": self.cfg.to_dict(),
                "summary": summary,
                "capacity": self.cfg.total_capacity,
                "human_capacity": self.human_capacity,
                "seats": self.seat_view() if self.phase == self.PHASE_LOBBY else [],
                "joined": len(self.participants),
                "participants": parts,
                "spectator_count": len(self.spectator_pids),
                "host_pid": self.host_pid,
                "champion_pid": self.champion_pid,
                "final_placements": self.final_placements,
                "announcement": self.announcement,
                "status_note": self.status_note,
                "can_start": self.can_start()[0],
                "can_start_reason": self.can_start()[1],
                "bracket": bracket_payload,
                "viewer": {
                    "pid": viewer_pid,
                    "is_host": is_host,
                    "in_tournament": me is not None,
                    "status": me.status if me else (S_SPECTATING if viewer_pid in self.spectator_pids else None),
                    "ready": me.ready if me else False,
                    "my_match": self._my_active_match(me) if me else None,
                    "pending_switch": self.pending_switch.get(viewer_pid) if viewer_pid else None,
                    "final_place": me.final_place if me else None,
                    "xp": self._xp_grants.get(viewer_pid) if viewer_pid else None,
                },
            }
            if is_host:
                payload["host_log"] = self.host_log[-40:]
                payload["host_control_token"] = self.host_control_token
            return payload

    def _bracket_view(self) -> Optional[Dict[str, Any]]:
        if not self.bracket:
            return None
        return self._bracket_view_from(self.bracket)

    def _bracket_view_from(self, br: Bracket) -> Dict[str, Any]:
        name_by = {p.pid: p.name for p in self.participants}
        avatar_by = {p.pid: p.avatar for p in self.participants}

        def m_view(m) -> Dict[str, Any]:
            d = m.to_dict()
            d["players"] = [
                None if pid is None else {"pid": pid, "name": name_by.get(pid, "?"),
                                          "avatar": avatar_by.get(pid, "")}
                for pid in m.player_ids
            ]
            d["winner"] = ({"pid": m.winners[0], "name": name_by.get(m.winners[0], "?")}
                           if m.winners else None)
            return d

        return {
            "n_rounds": br.n_rounds,
            "rounds": [[m_view(m) for m in row] for row in br.rounds],
            "third_place": m_view(br.third_place) if br.third_place else None,
        }

    def _preview_bracket(self) -> Optional[Dict[str, Any]]:
        """A provisional, NON-authoritative bracket for the LOBBY so the host and
        players can view the shape (and current seeding) before the tournament
        starts. Recomputed from the live roster + config every state view; never
        stored. Empty seats show as blank slots that fill as players join / bots
        are added. Returns None when there aren't enough players to shape it yet."""
        try:
            parts = sorted(self.participants, key=lambda x: x.join_order)
            order = [p.pid for p in parts]
            cfg = self.cfg
            if cfg.is_graph:
                # Designed bracket: the host's exact shape, with whoever is here now
                # sitting in the spot they'd actually start in.
                br = Bracket.build_custom(cfg)
                br.seed_entries(self.assign_seats())
            elif cfg.is_custom:
                # Fixed shape (sized for capacity); partial-seed whoever is here now.
                br = Bracket.build(cfg, len(order))
                br.seed_players(order, allow_partial=True)
            else:
                # Uniform: mirror what start() will build. With bot-fill the field
                # grows to capacity; otherwise it's sized for the current roster.
                n = cfg.total_capacity if self.fill_bots else len(order)
                if n < MIN_TOURNAMENT_PLAYERS:
                    return None
                br = Bracket.build(cfg, n)
                br.seed_players(order, allow_partial=(n != len(order)))
            view = self._bracket_view_from(br)
            view["preview"] = True
            return view
        except Exception:
            return None

    def _my_active_match(self, me: Participant) -> Optional[Dict[str, Any]]:
        if not self.bracket:
            return None
        candidates = list(self.bracket.all_matches(include_third=True))
        for m in candidates:
            if m.status in (M_READY, M_ACTIVE) and me.pid in [x for x in m.player_ids if x]:
                return self._match_ready_payload(m, me)
        return None

    def _match_ready_payload(self, m, me: Participant) -> Dict[str, Any]:
        """The viewer-facing snapshot of a match awaiting/running for ``me`` —
        includes the live ready-check roster so the client can render who still
        needs to ready up before the game launches."""
        seat_token = (self.match_seat_tokens.get(m.match_number) or {}).get(me.pid)
        ready = self.match_ready_pids.get(m.match_number, set())
        roster: List[Dict[str, Any]] = []
        for pid in m.player_ids:
            if not pid:
                continue
            p = self._by_pid(pid)
            is_bot = bool(p and p.is_bot)
            roster.append({
                "pid": pid,
                "name": p.name if p else "?",
                "avatar": p.avatar if p else "",
                "is_bot": is_bot,
                "connected": bool(p and p.connected),
                "ready": is_bot or (pid in ready),
                "me": (pid == me.pid),
            })
        return {
            "round_index": m.round_index, "match_index": m.match_index,
            "match_number": m.match_number, "room_id": m.room_id,
            "status": m.status, "seat_token": seat_token,
            "opponents": [x for x in m.player_ids if x and x != me.pid],
            "i_am_ready": (me.pid in ready),
            "ready_roster": roster,
            "ready_count": sum(1 for r in roster if r["ready"]),
            "total_count": len(roster),
        }


# =============================================================================
# Manager
# =============================================================================
class TournamentManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.tournaments: Dict[str, Tournament] = {}

    def create(self, cfg: TournamentConfig, host_pid: str, host_name: str, **kw) -> Tournament:
        errs = validate_config(cfg)
        if errs:
            raise ValueError("; ".join(errs))
        self.prune()   # opportunistic cleanup of ended/abandoned tournaments
        with self.lock:
            for _ in range(200):
                tid = _code(6)
                if tid not in self.tournaments:
                    t = Tournament(tid, cfg, host_pid, host_name, **kw)
                    self.tournaments[tid] = t
                    return t
        raise RuntimeError("unable to allocate tournament code")

    def get(self, tid: str) -> Optional[Tournament]:
        with self.lock:
            return self.tournaments.get(str(tid or "").strip().upper())

    def by_room(self, room_id: str) -> Optional[Tournament]:
        with self.lock:
            for t in self.tournaments.values():
                if room_id in t.match_rooms:
                    return t
        return None

    def list_public(self) -> List[Dict[str, Any]]:
        with self.lock:
            out = []
            for t in self.tournaments.values():
                if t.visibility != "public" or t.phase in (Tournament.PHASE_CANCELLED,):
                    continue
                out.append({
                    "tournament_id": t.tid, "name": t.cfg.name, "phase": t.phase,
                    "joined": len(t.participants), "capacity": t.cfg.total_capacity,
                    "human_capacity": t.human_capacity,
                    "players_per_match": t.cfg.players_per_match,
                    "is_custom": t.cfg.is_custom,
                    "is_graph": t.cfg.is_graph,
                    "opening_sizes": list(t.cfg.opening_sizes) if t.cfg.opening_sizes else None,
                    "has_password": bool(t.password_hash),
                    "spectators_allowed": t.spectators_allowed,
                })
            return out

    def prune(self, older_than_sec: float = 6 * 3600,
              lobby_idle_sec: float = 2 * 3600) -> None:
        with self.lock:
            now = _now()
            dead = []
            for tid, t in self.tournaments.items():
                # 1) ended long ago
                if t.ended_unix and (now - t.ended_unix) > older_than_sec:
                    dead.append(tid); continue
                # 2) abandoned lobby: never started, created long ago, nobody live
                if (t.phase == Tournament.PHASE_LOBBY
                        and (now - t.created_unix) > lobby_idle_sec
                        and not any(p.connected for p in t.participants)):
                    dead.append(tid)
            for tid in dead:
                self.tournaments.pop(tid, None)


MANAGER = TournamentManager()


def on_room_ended(room_id: str, ranking: List[str], scores: Dict[str, int]) -> Dict[str, Any]:
    """Module-level bridge called by the server's game-end hook."""
    t = MANAGER.by_room(room_id)
    if t is None:
        return {"ok": False, "error": "no tournament owns this room"}
    return t.on_room_ended(room_id, ranking, scores)


# =============================================================================
# XP finalize (idempotent Firestore write, mirrors snap_score)
# =============================================================================
def _grant_xp_firestore(tid: str, p: Participant, breakdown: Dict[str, Any]) -> bool:
    """Grant a player's tournament XP EXACTLY ONCE via a deterministic reward
    ledger. Returns True if granted (or already granted). Degrades gracefully
    (returns False) if Firestore is unavailable — the computed breakdown is still
    surfaced to the client so it can display the earned XP."""
    if p.is_guest or not p.pid or p.pid.startswith("guest:"):
        return False
    db = _get_firestore()
    if db is None:
        return False
    try:
        from firebase_admin import firestore as fb_fs
    except Exception:  # noqa: BLE001
        return False
    uid = p.pid
    now = _now()
    total_xp = int(breakdown.get("total_xp") or 0)
    items = breakdown.get("items") or []
    user_ref = db.collection("users").document(uid)
    result_ref = db.collection("tournamentResults").document(f"{tid}__{uid}")

    txn = db.transaction()

    @fb_fs.transactional
    def _run(txn):
        # per-player finalize guard: create once, re-run is a no-op
        rsnap = result_ref.get(transaction=txn)
        usnap = user_ref.get(transaction=txn)
        udoc = usnap.to_dict() if usnap.exists else {}
        stats = dict((udoc or {}).get("stats") or {})

        granted_now = 0
        if not rsnap.exists:
            for it in items:
                ledger_key = f"{tid}__{uid}__{it['kind']}"
                # deterministic id => txn.create fails on a real duplicate submit
                txn.create(db.collection("rewardLedger").document(ledger_key), {
                    "tournamentId": tid, "userId": uid, "rewardType": f"tournament_{it['kind']}",
                    "rewardAmount": int(it["amount"]), "createdAt": now,
                    "status": "granted", "uniqueRewardKey": ledger_key,
                })
                granted_now += int(it["amount"])

            new_total = int(stats.get("total_xp") or 0) + granted_now
            lvl, xp_cur, xp_goal = _level_progress(new_total)
            # tournament history entry (stored separately, §12)
            hist = list(stats.get("tournament_history") or [])
            hist = [{
                "tid": tid, "at": now, "place": p.final_place,
                "matches_won": p.matches_won, "xp": total_xp,
                "items": {it["kind"]: int(it["amount"]) for it in items},
            }] + hist
            hist = hist[:50]
            txn.set(user_ref, {
                "stats": {
                    "total_xp": new_total,
                    "level": lvl, "player_level": lvl,
                    "xp_current": xp_cur, "level_xp_current": xp_cur,
                    "xp_goal": xp_goal, "level_xp_goal": xp_goal,
                    "tournament_xp": int(stats.get("tournament_xp") or 0) + granted_now,
                    "tournament_history": hist,
                    "tournaments_played": int(stats.get("tournaments_played") or 0) + 1,
                    "tournament_wins": int(stats.get("tournament_wins") or 0) + (1 if p.final_place == 1 else 0),
                },
            }, merge=True)
            txn.create(result_ref, {
                "tournamentId": tid, "userId": uid, "place": p.final_place,
                "xpTotal": total_xp, "matchesWon": p.matches_won,
                "completedAt": now, "finalized": True,
            })

    try:
        _run(txn)
        return True
    except Exception as exc:  # noqa: BLE001
        # AlreadyExists (duplicate) is success-shaped: XP already granted once.
        if "already exists" in str(exc).lower() or exc.__class__.__name__ == "AlreadyExists":
            return True
        traceback.print_exc()
        return False


# =============================================================================
# HTTP plumbing — plugs into multiplayer_server do_GET / do_POST
# =============================================================================
def _pid_and_name(handler, body: Dict[str, Any]) -> Tuple[str, str, bool, str]:
    """Resolve (pid, name, is_guest, avatar) from an authenticated body.
    Accounts authenticate with idToken; guests carry a stable guest_id."""
    id_token = body.get("idToken") if isinstance(body.get("idToken"), str) else ""
    # player_name/host_name take precedence over `name` because create uses
    # `name` for the TOURNAMENT name — the two must never collide.
    name = str(body.get("player_name") or body.get("host_name") or body.get("name") or "Player")[:32]
    avatar = str(body.get("avatar") or "")[:256]
    if id_token:
        claims = _verify_token(id_token)
        if claims and claims.get("uid"):
            return str(claims["uid"]), name, False, avatar
    guest_id = str(body.get("guest_id") or "").strip()[:64]
    if not guest_id:
        guest_id = _gen_token(12)
    return f"guest:{guest_id}", name, True, avatar


def handle_get(handler, parsed) -> bool:
    path = parsed.path
    if not path.startswith("/api/tournament"):
        return False
    if not TOURNAMENTS_ENABLED:
        handler._send_json({"ok": False, "error": "tournaments disabled"}, status=HTTPStatus.NOT_FOUND)
        return True
    try:
        from urllib.parse import parse_qs
        q = parse_qs(parsed.query or "")

        if path == "/api/tournament/config":
            handler._send_json({"ok": True, "enabled": True,
                                "min_players": MIN_TOURNAMENT_PLAYERS,
                                "max_players": MAX_TOURNAMENT_PLAYERS})
            return True
        if path == "/api/tournament/formats":
            cap = int((q.get("capacity") or ["8"])[0] or 8)
            cap = max(MIN_TOURNAMENT_PLAYERS, min(MAX_TOURNAMENT_PLAYERS, cap))
            handler._send_json({"ok": True, "capacity": cap, "formats": available_formats(cap)})
            return True
        if path == "/api/tournament/preview":
            tp = str((q.get("third_place") or ["0"])[0]) in ("1", "true")
            custom = _parse_opening_sizes((q.get("opening_sizes") or [None])[0])
            if custom:
                pre_errs = validate_opening_sizes(custom)
                if pre_errs:
                    handler._send_json({"ok": False, "error": "; ".join(pre_errs)}, status=HTTPStatus.BAD_REQUEST)
                    return True
                cfg = TournamentConfig(sum(custom), max(custom), third_place_match=tp,
                                       opening_sizes=custom)
                cap = sum(custom)
            else:
                cap = max(MIN_TOURNAMENT_PLAYERS, min(MAX_TOURNAMENT_PLAYERS, int((q.get("capacity") or ["8"])[0] or 8)))
                ppm = max(2, min(8, int((q.get("players_per_match") or ["2"])[0] or 2)))
                cfg = TournamentConfig(cap, ppm, third_place_match=tp)
            errs = validate_config(cfg)
            if errs:
                handler._send_json({"ok": False, "error": "; ".join(errs)}, status=HTTPStatus.BAD_REQUEST)
                return True
            handler._send_json({"ok": True, "summary": bracket_summary(cfg, cap)})
            return True
        if path == "/api/tournament/list":
            handler._send_json({"ok": True, "tournaments": MANAGER.list_public()})
            return True
        if path == "/api/tournament/state":
            tid = str((q.get("id") or [""])[0]).upper()
            t = MANAGER.get(tid)
            if not t:
                handler._send_json({"ok": False, "error": "tournament not found"}, status=HTTPStatus.NOT_FOUND)
                return True
            pid = str((q.get("pid") or [""])[0]) or None
            htok = str((q.get("host_token") or [""])[0])
            handler._send_json({"ok": True, "state": t.state_view(viewer_pid=pid, host_token=htok)})
            return True
        if path == "/api/tournament/stream":
            tid = str((q.get("id") or [""])[0]).upper()
            t = MANAGER.get(tid)
            if not t:
                handler._send_json({"ok": False, "error": "tournament not found"}, status=HTTPStatus.NOT_FOUND)
                return True
            pid = str((q.get("pid") or [""])[0]) or None
            htok = str((q.get("host_token") or [""])[0])
            _serve_tournament_sse(handler, t, pid, htok)
            return True
        handler._send_json({"ok": False, "error": "unknown tournament endpoint"}, status=HTTPStatus.NOT_FOUND)
        return True
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        handler._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        return True


def _serve_tournament_sse(handler, t: Tournament, pid: Optional[str], host_token: str) -> None:
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler._apply_cors_headers()
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()
    last = -1
    try:
        while True:
            payload = t.state_view(viewer_pid=pid, host_token=host_token)
            v = int(payload.get("version", 0))
            if v != last:
                data = json.dumps(payload, separators=(",", ":"))
                handler.wfile.write(f"event: state\ndata: {data}\n\n".encode("utf-8"))
                handler.wfile.flush()
                last = v
            if not t.wait_for_update(last, timeout_sec=5.0):
                handler.wfile.write(b": ping\n\n")
                handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        return
    except Exception:  # noqa: BLE001
        return


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    path = parsed.path
    if not path.startswith("/api/tournament"):
        return False
    if not TOURNAMENTS_ENABLED:
        handler._send_json({"ok": False, "error": "tournaments disabled"}, status=HTTPStatus.NOT_FOUND)
        return True
    try:
        _dispatch_post(handler, path, body or {})
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        handler._send_json({"ok": False, "error": f"server error: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
    return True


def _require(handler, t: Optional[Tournament]) -> bool:
    if t is None:
        handler._send_json({"ok": False, "error": "tournament not found"}, status=HTTPStatus.NOT_FOUND)
        return False
    return True


def _dispatch_post(handler, path: str, body: Dict[str, Any]) -> None:
    # Live check for the bracket BUILDER — takes a work-in-progress design and
    # returns every problem with it plus a preview summary. No tournament exists
    # yet, so this runs before the id lookup below.
    if path == "/api/tournament/validate":
        spec = _parse_custom_graph(body.get("custom_graph") or body.get("bracket"))
        if spec is None:
            handler._send_json({"ok": False, "valid": False,
                                "errors": ["Add at least one match to the bracket."]})
            return
        errs = validate_custom_bracket(spec)
        payload: Dict[str, Any] = {"ok": True, "valid": not errs, "errors": errs}
        if not errs:
            payload["summary"] = custom_bracket_summary(spec)
        handler._send_json(payload)
        return

    if path == "/api/tournament/create":
        graph = _parse_custom_graph(body.get("custom_graph"))
        if graph is not None:
            # Canvas-designed bracket: the host's own shape, spot types and
            # connections. Re-validated here — the client's check is only a preview.
            gerrs = validate_custom_bracket(graph)
            if gerrs:
                handler._send_json({"ok": False, "error": "; ".join(gerrs), "errors": gerrs},
                                   status=HTTPStatus.BAD_REQUEST)
                return
            cfg = _graph_config(graph, body)
            errs = validate_config(cfg)
            if errs:
                handler._send_json({"ok": False, "error": "; ".join(errs)}, status=HTTPStatus.BAD_REQUEST)
                return
            pid, name, is_guest, avatar = _pid_and_name(handler, body)
            vis = str(body.get("visibility") or "public").lower()
            pw = body.get("password") if isinstance(body.get("password"), str) else None
            pw_hash = hashlib.sha256(pw.strip().encode()).hexdigest() if (vis == "private" and pw) else None
            t = MANAGER.create(cfg, pid, name, visibility=vis, password_hash=pw_hash,
                               spectators_allowed=bool(body.get("spectators_allowed", True)),
                               xp_reward_level=str(body.get("xp_reward_level") or "standard"),
                               fill_bots=bool(body.get("fill_bots")))
            host = t._by_pid(pid)
            if host:
                host.avatar = avatar
            handler._send_json({"ok": True, "tournament_id": t.tid,
                                "host_token": t.host_control_token,
                                "token": host.token if host else "",
                                "pid": pid, "designed": True})
            return
        custom = _parse_opening_sizes(body.get("opening_sizes"))
        if custom:
            # Custom bracket: the host's explicit opening-round layout fixes both the
            # total capacity (sum) and the max match size (players_per_match).
            pre_errs = validate_opening_sizes(custom)
            if pre_errs:
                handler._send_json({"ok": False, "error": "; ".join(pre_errs)}, status=HTTPStatus.BAD_REQUEST)
                return
            cap = sum(custom)
            ppm = max(custom)
            cfg = TournamentConfig(
                total_capacity=cap, players_per_match=ppm,
                advance_per_match=1,
                third_place_match=bool(body.get("third_place_match")),
                name=str(body.get("name") or "Currents Cup")[:60],
                opening_sizes=custom,
            )
        else:
            cap = max(MIN_TOURNAMENT_PLAYERS, min(MAX_TOURNAMENT_PLAYERS, int(body.get("total_capacity") or 8)))
            ppm = max(2, min(8, int(body.get("players_per_match") or 2)))
            cfg = TournamentConfig(
                total_capacity=cap, players_per_match=ppm,
                advance_per_match=1,
                third_place_match=bool(body.get("third_place_match")),
                name=str(body.get("name") or "Currents Cup")[:60],
            )
        errs = validate_config(cfg)
        if errs:
            handler._send_json({"ok": False, "error": "; ".join(errs)}, status=HTTPStatus.BAD_REQUEST)
            return
        pid, name, is_guest, avatar = _pid_and_name(handler, body)
        vis = str(body.get("visibility") or "public").lower()
        pw = body.get("password") if isinstance(body.get("password"), str) else None
        pw_hash = hashlib.sha256(pw.strip().encode()).hexdigest() if (vis == "private" and pw) else None
        t = MANAGER.create(cfg, pid, name, visibility=vis, password_hash=pw_hash,
                           spectators_allowed=bool(body.get("spectators_allowed", True)),
                           xp_reward_level=str(body.get("xp_reward_level") or "standard"),
                           fill_bots=bool(body.get("fill_bots")))
        host = t._by_pid(pid)
        if host:
            host.avatar = avatar
        handler._send_json({"ok": True, "tournament_id": t.tid,
                            "host_token": t.host_control_token,
                            "token": host.token if host else "",
                            "pid": pid})
        return

    if path == "/api/tournament/join":
        t = MANAGER.get(str(body.get("id") or "").upper())
        if not _require(handler, t):
            return
        if t.password_hash:
            pw = str(body.get("password") or "")
            if hashlib.sha256(pw.strip().encode()).hexdigest() != t.password_hash:
                handler._send_json({"ok": False, "error": "wrong password"}, status=HTTPStatus.FORBIDDEN)
                return
        pid, name, is_guest, avatar = _pid_and_name(handler, body)
        res = t.join(pid, name, avatar=avatar, is_guest=is_guest)
        res["pid"] = pid
        handler._send_json(res, status=(HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST))
        return

    # all remaining actions target an existing tournament
    t = MANAGER.get(str(body.get("id") or "").upper())
    if not _require(handler, t):
        return
    pid, name, is_guest, avatar = _pid_and_name(handler, body)
    htok = str(body.get("host_token") or "")
    action = path.rsplit("/", 1)[-1]

    def host_only() -> bool:
        if not t.is_host(pid=pid, host_token=htok):
            handler._send_json({"ok": False, "error": "host only"}, status=HTTPStatus.FORBIDDEN)
            return False
        return True

    if action == "ready":
        handler._send_json(t.set_ready(pid, bool(body.get("ready", True))))
    elif action == "leave":
        handler._send_json(t.leave(pid))
    elif action == "entered":
        handler._send_json(t.match_entered(pid))
    elif action == "match_ready":
        handler._send_json(t.match_ready(pid, bool(body.get("ready", True))))
    elif action == "randomize":
        if host_only():
            handler._send_json(t.randomize(rng_seed=body.get("seed")))
    elif action == "switch":
        target = str(body.get("target_pid") or "")
        handler._send_json(t.request_switch(pid, target))
    elif action == "switch_respond":
        handler._send_json(t.respond_switch(pid, bool(body.get("accept"))))
    elif action == "start":
        if host_only():
            handler._send_json(t.start())
    elif action == "host":
        if host_only():
            handler._send_json(_host_action(t, body))
    elif action == "report":
        # SECURITY: real match results ONLY enter via the internal game-end hook
        # (tournament_server.on_room_ended, called server-side). This HTTP path
        # would let any client forge a winner / farm XP, so it is DISABLED unless
        # the test-hook flag is set (used only by the automated test harness).
        if not TEST_HOOKS_ENABLED:
            handler._send_json({"ok": False, "error": "not found"}, status=HTTPStatus.NOT_FOUND)
        else:
            handler._send_json(_client_report(t, pid, body))
    else:
        handler._send_json({"ok": False, "error": f"unknown action {action}"}, status=HTTPStatus.NOT_FOUND)


def _host_action(t: Tournament, body: Dict[str, Any]) -> Dict[str, Any]:
    cmd = str(body.get("cmd") or "")
    if cmd == "lock":
        return t.host_set(locked=bool(body.get("locked", True)))
    if cmd == "rename":
        return t.host_set(name=str(body.get("name") or ""))
    if cmd == "spectators":
        return t.host_set(spectators_allowed=bool(body.get("spectators_allowed", True)))
    if cmd == "move":
        return t.host_move(str(body.get("pid") or ""), int(body.get("index") or 0))
    if cmd == "swap":
        return t.host_swap(str(body.get("pid") or ""), str(body.get("pid_b") or body.get("target_pid") or ""))
    if cmd == "remove":
        return t.host_remove(str(body.get("pid") or ""))
    if cmd == "fill_bots":
        return t.fill_with_bots(target=body.get("target"))
    if cmd == "announce":
        return t.announce(str(body.get("text") or ""))
    if cmd == "pause":
        return t.pause(bool(body.get("paused", True)))
    if cmd == "cancel":
        return t.cancel()
    if cmd == "forfeit":
        return t.leave(str(body.get("pid") or ""))
    return {"ok": False, "error": f"unknown host cmd {cmd}"}


def _client_report(t: Tournament, pid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """A trusted server hook reports results via on_room_ended; this direct path
    is only for the headless/testing harness and is gated so a client can only
    report a match it actually belongs to (defense-in-depth; the winner still
    comes from the authoritative game result, not the client's say-so)."""
    r = int(body.get("round_index"))
    mi = int(body.get("match_index"))
    ranking = [str(x) for x in (body.get("ranking") or [])]
    scores = {str(k): int(v) for k, v in dict(body.get("scores") or {}).items()}
    return t.report_match_result(r, mi, ranking, scores, room_id=body.get("room_id"))
